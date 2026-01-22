import logging
from datetime import timedelta
from typing import Optional
from uuid import UUID
from .helpers.session_helper import SessionHelper

logger = logging.getLogger("SESSION")


class Session:
    TOTAL_WORDS_THRESHOLD = 350

    def __init__(
        self, agency_id: str, prospect_id: str, session_id: Optional[UUID] = None
    ):
        self.agency_id = agency_id
        self.prospect_id = prospect_id
        self.session_id: Optional[UUID] = session_id
        self._cached_first_impression: dict | None = None

    @classmethod
    async def initiate_session(
        cls, agency_id: str, prospect_id: str, msg: str, subject: str = "Not Provided"
    ) -> Optional[Session]:
        """
        Initiate or retrieve a session. Resolves cache → DB → create new session.
        """
        from core import TravelIntentGuard

        if not agency_id or not prospect_id:
            logger.error("❌ Missing agency_id or prospect_id")
            return None

        ttl = timedelta(days=14)

        # -------------------- Resolve existing session --------------------
        session_id, first_impression = await SessionHelper.resolve_session(
            prospect_id, ttl
        )
        if session_id:
            session = cls(agency_id, prospect_id)
            session.session_id = session_id
            session._cached_first_impression = first_impression
            return session

        # -------------------- Cannot create new session without message --------------------
        if not msg:
            logger.error(
                "❌ Cannot create session: missing initial message | Prospect ID=%s",
                prospect_id,
            )
            return None

        # -------------------- AI first impression --------------------
        try:
            result = TravelIntentGuard.analyze_conversation(
                subject=subject, latest_msg=msg, chat_context= [{"sender": "prospect", "text": msg}], is_first_message=True
            )
            first_impression = result.get("first_impression", {}) if result else {}
            intent = first_impression.get("intent", "other")
            confidence = float(first_impression.get("confidence", 0.0))
            logger.info(
                "🧠 First impression captured | Intent=%s | Prospect ID=%s",
                intent,
                prospect_id,
            )
        except Exception as e:
            logger.error(
                "❌ AI analysis failed | Prospect ID=%s | Error=%s",
                prospect_id,
                e,
                exc_info=True,
            )
            first_impression = {}
            intent = "other"
            confidence = 0.0

        # -------------------- Create session in DB & cache --------------------
        payload = {
            "first_impression": first_impression,
            "intent": intent,
            "intent_confidence": confidence,
        }
        session_id = await SessionHelper.create_session_in_db(prospect_id, payload, ttl)
        if not session_id:
            return None

        session = cls(agency_id, prospect_id)
        session._cached_first_impression = first_impression
        return session

    async def chat_container(
        self,
        session_id: str,
        text: str = "",
        sender: str = "Prospect",
        retrieve: bool = False,
    ):
        """
        Handle a chat message: store in Redis, persist in DB, trigger threshold actions.
        Returns chat history and is_prospect_first_msg flag if retrieve=True.
        """
        if not session_id or session_id.lower() == "none":
            logger.error("❌ chat_container called without session_id")
            return

        sender_lower = sender.lower()
        text = text or ""

        # -------------------- Retrieve only --------------------
        if retrieve and not text.strip():
            chat_history, is_prospect_first_msg = (
                await SessionHelper.fetch_chat_history(session_id)
            )
            logger.info(
                "ℹ️ Retrieved chat history | Session=%s | HasProspectInitiated=%s",
                session_id,
                is_prospect_first_msg,
            )
            return chat_history, is_prospect_first_msg

        # -------------------- Append message --------------------
        words = len(text.split())
        msg_data = {"sender": sender, "text": text, "words": words}
        total_words = SessionHelper.add_message_to_chat_buffer(session_id, msg_data)
        await SessionHelper.persist_message(session_id, text, sender_lower)

        # -------------------- Trigger AI essence extraction --------------------
        if total_words >= self.TOTAL_WORDS_THRESHOLD and sender_lower == "ai":
            logger.info(
                "⚡ Word threshold reached | Trigger AI essence extraction | Session=%s | TotalWords=%d",
                session_id,
                total_words,
            )
            # TODO: Call summarization/extraction here

        # -------------------- Return full chat history if requested --------------------
        if retrieve:
            chat_history, is_prospect_first_msg = (
                await SessionHelper.fetch_chat_history(session_id)
            )
            return chat_history, is_prospect_first_msg
