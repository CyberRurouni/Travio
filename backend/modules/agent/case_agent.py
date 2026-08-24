import re
import hashlib
import logging
from datetime import timedelta

from .crud import fetch_agent_agency, fetch_prospect_id
from core import session_registry, assistant_registry


logger = logging.getLogger("CASE_AGENT")


def is_internal_agent_email(email: str, agent_email: str) -> bool:
    """
    Checks whether the provided email belongs to our internal agent.
    """
    return email.strip().lower() == agent_email.strip().lower()


class CaseAgent:
    """
    Handles the flow when an agent contacts a prospect.

    Flow:
    1. Resolve agency from agent email.
    2. Extract prospect email from message body.
    3. Resolve prospect ID.
    4. Resolve or create session.
    5. Inform AI assistant that agent has sent final message.
    """

    def __init__(self, issued_email: str, agent_email: str, msg: str, subject: str = "Not Provided"):
        self.issued_email = issued_email
        self.agent_email = agent_email
        self.message = msg
        self.subject = subject

        self.agency_id = None
        self.agency_name = None
        self.app_password = None
        self.prospect_email = self._extract_prospect_email()
        self.agency_prospect_id = None
        self.session_id = None
        self.session = None

        logger.info("Initialized CaseAgent | Agent=%s", self.agent_email)

    # ---------------------------------------------------------------------
    # Agency Resolution
    # ---------------------------------------------------------------------

    @staticmethod
    async def resolve_agency(agent_email: str):
        """
        Fetch agency ID associated with the agent email.
        """
        logger.info("Resolving agency | Agent=%s", agent_email)

        agency = await fetch_agent_agency(agent_email)

        agency_id = agency.get("agency_id") if agency else None
        agency_name = agency.get("agency_name") if agency else None
        app_password = agency.get("app_password") if agency else None

        if agency_id and agency_name and app_password:
            logger.info(
                "✅ Agency resolved | Agent=%s | AgencyID=%s | AgencyName=%s",
                agent_email,
                agency_id,
                agency_name,
            )
            return agency_id, agency_name, app_password

        return None, None, None

    async def update_agency_id(self):
        self.agency_id, self.agency_name, self.app_password = await self.resolve_agency(
            self.agent_email
        )

    # ---------------------------------------------------------------------
    # Prospect Resolution
    # ---------------------------------------------------------------------

    def _extract_prospect_email(self):
        """
        Extract prospect email from message body.
        Filters out internal agent email.
        """
        logger.info("Extracting prospect email from message")

        email_pattern = r"[\w\.-]+@[\w\.-]+"
        found_emails = re.findall(email_pattern, self.message)

        prospect_emails = [
            email
            for email in found_emails
            if not is_internal_agent_email(email, self.agent_email)
        ]

        if not prospect_emails:
            logger.warning("❌ No prospect email found in message")
            return None

        logger.info("✅ Prospect email extracted | Email=%s", prospect_emails[0])
        return prospect_emails[0]

    async def resolve_prospect_id(self, prospect_email: str):
        """
        Generate hashed identifier from prospect email
        and fetch corresponding prospect ID.
        """
        if not prospect_email:
            logger.warning("❌ Cannot resolve prospect ID — email missing")
            return None

        identifier_hash = hashlib.sha256(prospect_email.encode("utf-8")).hexdigest()

        agency_prospect_id = await fetch_prospect_id(identifier_hash, self.agency_id)

        if not agency_prospect_id:
            logger.warning("❌ No prospect found for hashed email")
        else:
            logger.info("✅ Prospect ID resolved | ProspectID=%s", agency_prospect_id)

        return agency_prospect_id

    async def update_prospect_id(self):
        self.agency_prospect_id = await self.resolve_prospect_id(self.prospect_email)

    # ---------------------------------------------------------------------
    # Main Execution Flow
    # ---------------------------------------------------------------------

    async def final_message(self):
        """
        Executes the full flow:

        1. Resolve agency
        2. Resolve prospect
        3. Resolve/create session
        4. Log agent message
        5. Notify assistant (final message)
        """
        from core import Session, Assistant, get_agency_password

        logger.info("Starting final_message flow")

        # -------------------- Resolve Required Data --------------------

        await self.update_agency_id()
        await self.update_prospect_id()

        if not self.agency_id or not self.agency_prospect_id:
            logger.error(
                "❌ Cannot continue | AgencyID=%s | ProspectID=%s",
                self.agency_id,
                self.agency_prospect_id,
            )
            return

        # -------------------- Resolve or Create Session --------------------

        session = await session_registry.get_or_create(
            key=f"{self.agency_id}:{self.agency_prospect_id}",
            factory=Session.initiate_session,
            agency_id=str(self.agency_id),
            agency_prospect_id=str(self.agency_prospect_id),
            msg=self.message,
            subject=self.subject,
            factory_type="async",
        )

        if not session:
            logger.error("❌ Failed to initialize session")
            return

        self.session = session
        self.session_id = session.session_id

        logger.info(
            "✅ Session ready | SessionID=%s | ProspectID=%s",
            self.session_id,
            self.agency_prospect_id,
        )

        # -------------------- Log Agent & Systme Message --------------------

        await session.chat_container(
            sender="agent",
            text=self.message,
        )

        await session.chat_container(
            text=f"""[STATE_UPDATE]
            action_type: connect_human_agent
            status: contact_established
            context: Agent has sent a message to the prospect. Awaiting AI assistant final response.
            """,
            sender="system",
        )

        logger.info("✅ Agent & System message logged in chat container")

        # -------------------- Resolve Assistant --------------------
        assistant = await assistant_registry.get_or_create(
            key=f"{self.agency_id}:{self.agency_prospect_id}:{self.session_id}",
            factory=Assistant,
            agency_id=str(self.agency_id),
            agency_prospect_id=str(self.agency_prospect_id),
            issued_email=self.issued_email,
            app_password=self.app_password,
            session=self.session,
            factory_type="sync",
        )

        if not assistant:
            logger.error("❌ Failed to initialize assistant")
            return

        logger.info("Assistant instance ready")

        # -------------------- Notify Assistant --------------------

        await assistant.response(
            prospect_email=self.prospect_email,
            first_impression=session._cached_first_impression or {},
            msg=self.message,
            agent_email=self.agent_email,
            agency_name=self.agency_name,
            agent_message=True,
            final_message=True,
        )

        logger.info("✅ Final message delivered to assistant")
