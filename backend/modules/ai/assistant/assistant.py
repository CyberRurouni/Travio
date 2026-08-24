import logging
from typing import Optional
from datetime import timedelta

from core import Session, get_smtp_service, TravelIntentGuard
from .helpers.response import generate_ai_response
from .helpers.actions import handle_action
from .helpers.utils import log_chat_history_readable

logger = logging.getLogger("ASSISTANT")


class Assistant:

    def __init__(
        self,
        agency_id: str,
        agency_prospect_id: str,
        issued_email: str,
        app_password: str,
        session: Session,
    ) -> None:
        self.agency_id = agency_id
        self.agency_prospect_id = agency_prospect_id
        self.issued_email = issued_email
        self.app_password = app_password
        self.session = session

    async def response(
        self,
        prospect_email: str,
        agent_email: str,
        agency_name: str,
        msg: str,
        first_impression: dict,
        subject: str = "Not Provided",
        sender: str = "Travio",
        agent_message: bool = False,
        final_message: bool = False,
    ):

        # -------------------- Retrieve chat history --------------------
        result: Optional[tuple] = await self.session.chat_container(
            text="", retrieve=True
        )
        chat_history, is_prospect_first_msg = result or ([], True)

        log_chat_history_readable(
            chat_history,
            agency_prospect_id=self.agency_prospect_id,
            session_id=self.session.session_id,
        )

        # -------------------- Intent analysis --------------------
        intent_guard_data = {}
        if first_impression:
            intent_guard_data = {"first_impression": first_impression}

        if not agent_message:
            analysis = TravelIntentGuard.analyze_conversation(
                subject="",
                latest_msg=msg,
                chat_context=chat_history,
            )
            if analysis:
                intent_guard_data.update(analysis)

        # Ensure conversation_layer is always surfaced at the top level
        # so response.py can read it without nested lookups.
        # Priority: top-level > intent sub-object > first_impression > default GENERAL
        if "conversation_layer" not in intent_guard_data:
            nested = intent_guard_data.get("intent", {}).get("conversation_layer")
            fi_layer = intent_guard_data.get("first_impression", {}).get("conversation_layer")
            intent_guard_data["conversation_layer"] = nested or fi_layer or "GENERAL"
        
        logger.info(f"🎯 User Intent Analysis: {intent_guard_data}")

        # -------------------- AI Response --------------------
        ai_result = generate_ai_response(
            chat_container=chat_history,
            intent_guard_data=intent_guard_data if intent_guard_data else None,
            agency_name=agency_name,
        )

        action_type = ai_result.get("actions", {}).get("type")
        action_details = ai_result.get("actions", {}).get("details", {})

        if not action_type:
            logger.warning("⚠️ Missing action type")
            return

        smtp_service = await get_smtp_service(
            issued_email=self.issued_email, app_password=self.app_password
        )

        # -------------------- Delegate to Action Layer --------------------
        await handle_action(
            assistant=self,
            subject=subject,
            action_type=action_type,
            action_details=action_details,
            ai_result=ai_result,
            smtp_service=smtp_service,
            prospect_email=prospect_email,
            agent_email=agent_email,
            agency_name=agency_name,
            sender=sender,
            intent_guard_data=intent_guard_data, 
            agency_prospect_id=self.agency_prospect_id,
            session_id=self.session.session_id,
        )

        logger.info(
            "📨 AI response processed | Session=%s | Prospect=%s | Action=%s",
            self.session.session_id,
            self.agency_prospect_id,
            action_type,
        )
