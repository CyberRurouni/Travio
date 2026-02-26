import re
import hashlib
import logging
from datetime import timedelta

from core import AGENT_EMAIL
from .crud import fetch_agent_agency, fetch_prospect_id


logger = logging.getLogger("CASE_AGENT")


def is_internal_agent_email(email: str) -> bool:
    """
    Checks whether the provided email belongs to our internal agent.
    """
    return email.strip().lower() == AGENT_EMAIL.strip().lower()


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

    def __init__(self, agent_email: str, msg: str, subject: str = "Not Provided"):
        self.agent_email = agent_email
        self.message = msg
        self.subject = subject

        self.agency_id = None
        self.prospect_email = self._extract_prospect_email()
        self.prospect_id = None
        self.session_id = None
        self.session = None

        logger.info("Initialized CaseAgent | Agent=%s", self.agent_email)

    # ---------------------------------------------------------------------
    # Agency Resolution
    # ---------------------------------------------------------------------

    @staticmethod
    async def resolve_agency_id(agent_email: str):
        """
        Fetch agency ID associated with the agent email.
        """
        logger.info("Resolving agency | Agent=%s", agent_email)

        agency_id = await fetch_agent_agency(agent_email)

        if not agency_id:
            logger.warning("❌ No agency found | Agent=%s", agent_email)
        else:
            logger.info("✅ Agency resolved | AgencyID=%s", agency_id)

        return agency_id

    async def update_agency_id(self):
        self.agency_id = await self.resolve_agency_id(self.agent_email)

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
            email for email in found_emails if not is_internal_agent_email(email)
        ]

        if not prospect_emails:
            logger.warning("❌ No prospect email found in message")
            return None

        logger.info("✅ Prospect email extracted | Email=%s", prospect_emails[0])
        return prospect_emails[0]

    @staticmethod
    async def resolve_prospect_id(prospect_email: str):
        """
        Generate hashed identifier from prospect email
        and fetch corresponding prospect ID.
        """
        if not prospect_email:
            logger.warning("❌ Cannot resolve prospect ID — email missing")
            return None

        identifier_hash = hashlib.sha256(prospect_email.encode("utf-8")).hexdigest()

        prospect_id = await fetch_prospect_id(identifier_hash)

        if not prospect_id:
            logger.warning("❌ No prospect found for hashed email")
        else:
            logger.info("✅ Prospect ID resolved | ProspectID=%s", prospect_id)

        return prospect_id

    async def update_prospect_id(self):
        self.prospect_id = await self.resolve_prospect_id(self.prospect_email)

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
        from core import InstanceRegistry, Session, Assistant

        logger.info("Starting final_message flow")

        # -------------------- Resolve Required Data --------------------

        await self.update_agency_id()
        await self.update_prospect_id()

        if not self.agency_id or not self.prospect_id:
            logger.error(
                "❌ Cannot continue | AgencyID=%s | ProspectID=%s",
                self.agency_id,
                self.prospect_id,
            )
            return

        # -------------------- Resolve or Create Session --------------------

        session_registry = InstanceRegistry(ttl=timedelta(hours=1))

        session = await session_registry.get_or_create(
            key=f"{self.agency_id}:{self.prospect_id}",
            factory=Session.initiate_session,
            agency_id=str(self.agency_id),
            prospect_id=str(self.prospect_id),
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
            self.prospect_id,
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

        assistant_registry = InstanceRegistry(ttl=timedelta(hours=1))

        assistant = await assistant_registry.get_or_create(
            key=f"{self.agency_id}:{self.prospect_id}:{self.session_id}",
            factory=Assistant,
            agency_id=str(self.agency_id),
            prospect_id=str(self.prospect_id),
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
            msg=self.message,
            agent_email=self.agent_email,
            agent_message=True,
            final_message=True,
        )

        logger.info("✅ Final message delivered to assistant")
