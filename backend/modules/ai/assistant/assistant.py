# assistant.py

import logging
from typing import Optional
from datetime import timedelta

from core import Session, InstanceRegistry, TravelIntentGuard
from .helpers.response import generate_ai_response
from .helpers.actions import handle_action
from .helpers.utils import get_smtp_service

logger = logging.getLogger("ASSISTANT")


class Assistant:

    def __init__(self, agency_id: str, prospect_id: str, session: Session) -> None:
        self.agency_id = agency_id
        self.prospect_id = prospect_id
        self.session = session

    async def response(
        self,
        prospect_email: str,
        agent_email: str,
        msg: str,
        first_impression: dict,
        sender: str = "Travio",
    ):

        # -------------------- Retrieve chat history --------------------
        result: Optional[tuple] = await self.session.chat_container(
            text="", retrieve=True
        )
        chat_history, is_prospect_first_msg = result or ([], True)

        chat_history_clean = [
            {"sender": m.get("sender"), "text": m.get("text")} for m in chat_history
        ]

        logger.info(
            "📜 Chat History | 👤 Prospect=%s | 🧩 Session=%s | Messages=%s",
            self.prospect_id,
            self.session.session_id,
            chat_history_clean,
        )

        # -------------------- Intent analysis --------------------
        intent_guard_data = {"first_impression": first_impression}

        if not is_prospect_first_msg:
            analysis = TravelIntentGuard.analyze_conversation(
                subject="",
                latest_msg=msg,
                chat_context=chat_history_clean,
            )
            if analysis:
                intent_guard_data.update(analysis)

        # -------------------- Initial AI Response --------------------
        ai_result = generate_ai_response(
            chat_container=chat_history_clean,
            intent_guard_data=intent_guard_data,
        )

        action_type = ai_result.get("actions", {}).get("type")
        action_details = ai_result.get("actions", {}).get("details", {})

        if not action_type:
            logger.warning("⚠️ Missing action type")
            return

        smtp_service = await get_smtp_service()

        # -------------------- Delegate to Action Layer --------------------
        await handle_action(
            assistant=self,
            action_type=action_type,
            action_details=action_details,
            ai_result=ai_result,
            smtp_service=smtp_service,
            prospect_email=prospect_email,
            agent_email=agent_email,
            sender=sender,
            intent_guard_data=intent_guard_data,
        )

        logger.info(
            "📨 AI response processed | Session=%s | Prospect=%s | Action=%s",
            self.session.session_id,
            self.prospect_id,
            action_type,
        )
