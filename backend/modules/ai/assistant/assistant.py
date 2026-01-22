import logging
from datetime import timedelta
from core import Session
from core import session_broker, safe_redis_operation
from core import TravelIntentGuard, InstanceRegistry
from .response import generate_ai_response

logger = logging.getLogger("ASSISTANT")

class Assistant:

    def __init__(self, agency_id: str, prospect_id: str, session_id: str, session: Session) -> None:
        self.agency_id = agency_id
        self.prospect_id = prospect_id
        self.session_id = session_id
        self.session = session
    
    @staticmethod
    async def get_smtp_service():
        from core import SMTPService
        smtp_registry = InstanceRegistry(ttl=timedelta(hours=6))
        return await smtp_registry.get_or_create(
            key="smtp_service",
            factory=SMTPService,
            factory_type="sync",
        )


    async def response(self, prospect_email: str, msg: str, first_impression: dict, sender: str = "Assistant"):
        result = await self.session.chat_container(
            session_id=self.session_id, text="", retrieve=True
        )

        intent_guard_data = {"first_impression": first_impression}

        if result is None:
            chat_history, is_prospect_first_msg = [], True
        else:
            chat_history, is_prospect_first_msg = result

        logger.info(
            "ℹ️ Retrieved chat history | Session=%s | HasProspectInitiated=%s",
            self.session_id,
            is_prospect_first_msg,
        )

        chat_history_clean = [
            {"sender": m.get("sender"), "text": m.get("text")}
            for m in chat_history
        ]

        if not is_prospect_first_msg:
            analysis = TravelIntentGuard.analyze_conversation(
                subject="",
                latest_msg=msg,
                chat_context=chat_history_clean
            )
            logger.info("ℹ️ AI Analysis: %s", analysis)
            intent_guard_data.update(analysis)

        # 🤖 Generate AI response (DICT)
        ai_result = generate_ai_response(
            chat_container=chat_history_clean,
            intent_guard_data=intent_guard_data
        )

        ai_message: str = ai_result.get("message", "")
        ai_actions = ai_result.get("actions", {})

        if not ai_message:
            logger.warning("⚠️ Empty AI message, skipping email send")
            return

        # 📨 Send email via singleton SMTPService
        smtp_service = await Assistant.get_smtp_service()

        await smtp_service.send_email(
            to=prospect_email, 
            subject="Re: Your inquiry",
            body=ai_message,
        )

        logger.info(
            "📨 AI response sent | Session=%s | Prospect=%s | Action=%s",
            self.session_id,
            self.prospect_id,
            ai_actions.get("type"),
        )

