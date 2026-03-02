import logging
from datetime import timedelta
from typing import Optional
from uuid import UUID
from .helpers.session_helper import SessionHelper

logger = logging.getLogger("SESSION")


class Session:
    COMPACTION_TRIGGER_WORD_LIMIT = 1000

    def __init__(self, agency_id: str, agency_prospect_id: str):
        self.agency_id = agency_id
        self.agency_prospect_id = agency_prospect_id
        self.session_id: UUID | None = None
        self._cached_first_impression: dict | None = None
        self._compaction_disabled: bool = False

    # ---------------------------------------------------------------------
    # Session Initialization
    # ---------------------------------------------------------------------

    @classmethod
    async def initiate_session(
        cls, agency_id: str, agency_prospect_id: str, msg: str, subject: str = "Not Provided"
    ) -> Optional["Session"]:
        """
        Initiate or retrieve a session.
        Resolution order:
        Cache → DB → Create new session
        """

        from core import TravelIntentGuard

        if not agency_id or not agency_prospect_id:
            logger.error("❌ Missing agency_id or agency_prospect_id")
            return None

        ttl = timedelta(days=14)

        # -------------------- Resolve Existing Session --------------------

        session_id, first_impression = await SessionHelper.resolve_session(
            agency_prospect_id, ttl
        )

        if session_id:
            session = cls(agency_id, agency_prospect_id)
            session.session_id = str(session_id)
            session._cached_first_impression = first_impression

            logger.info(
                "♻️ Existing session restored | Session ID=%s | Prospect ID=%s",
                session.session_id,
                agency_prospect_id,
            )

            return session

        # -------------------- Prevent Creation Without Message --------------------

        if not msg:
            logger.error(
                "❌ Cannot create session: missing initial message | Prospect ID=%s",
                agency_prospect_id,
            )
            return None

        # -------------------- AI First Impression --------------------

        try:
            result = TravelIntentGuard.analyze_conversation(
                subject=subject,
                latest_msg=msg,
                chat_context=[{"sender": "prospect", "text": msg}],
                is_first_message=True,
            )

            first_impression = result.get("first_impression", {}) if result else {}
            intent = first_impression.get("intent", "other")
            confidence = float(first_impression.get("confidence", 0.0))

            logger.info(
                "🧠 First impression captured | Intent=%s | Prospect ID=%s",
                intent,
                agency_prospect_id,
            )

        except Exception as e:
            logger.error(
                "❌ AI analysis failed | Prospect ID=%s | Error=%s",
                agency_prospect_id,
                e,
                exc_info=True,
            )
            first_impression = {}
            intent = "other"
            confidence = 0.0

        # -------------------- Create Session --------------------

        payload = {
            "first_impression": first_impression,
            "intent": intent,
            "intent_confidence": confidence,
        }

        session_id = await SessionHelper.create_session_in_db(agency_prospect_id, payload, ttl)

        if not session_id:
            logger.error(
                "❌ Failed to create session in DB | Prospect ID=%s",
                agency_prospect_id,
            )
            return None

        session = cls(agency_id, agency_prospect_id)
        session.session_id = str(session_id)
        session._cached_first_impression = first_impression

        logger.info(
            "✅ New session created | Session ID=%s | Prospect ID=%s",
            session.session_id,
            agency_prospect_id,
        )

        return session

    # ---------------------------------------------------------------------
    # Chat Container
    # ---------------------------------------------------------------------

    async def chat_container(
        self,
        text: str = "",
        sender: str = "Prospect",
        retrieve: bool = False,
    ):
        """
        Handles:
        - Message buffering (Redis)
        - DB persistence
        - Recursive dialogue compaction
        - Retrieval

        Returns:
            - Compressed essence (if compaction triggered)
            - Full chat history (if retrieve=True)
        """

        from core import compact_dialogue_state

        if not self.session_id:
            logger.error("❌ chat_container called without session_id")
            return

        sender_lower = sender.lower()
        text = text or ""

        # -------------------- Retrieve Only --------------------

        if retrieve and not text.strip():
            chat_history, is_prospect_first_msg = (
                await SessionHelper.fetch_chat_history(self.session_id)
            )

            logger.info(
                "ℹ️ Retrieved chat history | Session=%s | HasProspectInitiated=%s",
                self.session_id,
                is_prospect_first_msg,
            )

            return chat_history, is_prospect_first_msg

        # -------------------- Append Message --------------------

        words = len(text.split())

        msg_data = {
            "sender": sender,
            "text": text,
            "words": words,
        }

        total_words = SessionHelper.add_message_to_chat_buffer(
            self.session_id,
            msg_data,
        )

        logger.info(
            "💬 Message added to buffer | Session=%s | Sender=%s | Words=%d | TotalWords=%d",
            self.session_id,
            sender,
            words,
            total_words,
        )

        await SessionHelper.persist_message(
            self.session_id,
            text,
            sender_lower,
        )

        # -------------------- Trigger Dialogue Compaction --------------------

        if (
            total_words >= self.COMPACTION_TRIGGER_WORD_LIMIT
            and not self._compaction_disabled
        ):

            logger.info(
                "⚡ Compaction threshold reached | Session=%s | TotalWords=%d",
                self.session_id,
                total_words,
            )

            chat_history, _ = await SessionHelper.fetch_chat_history(self.session_id)

            compressed_chat_history, disable_compaction = compact_dialogue_state(chat_history)

            if disable_compaction:
                logger.error(
                    "🛑 Compaction disabled for session | Session=%s",
                    self.session_id,
                )
                self._compaction_disabled = True
                return chat_history

            # Persist the compressed chat (DB + Redis)
            await SessionHelper.persist_compacted_chat(self.session_id, compressed_chat_history)

            logger.info(
                "✅ Compaction successful | Session=%s",
                self.session_id,
            )

            return compressed_chat_history

        # -------------------- Retrieve After Append --------------------

        if retrieve:
            chat_history, is_prospect_first_msg = (
                await SessionHelper.fetch_chat_history(self.session_id)
            )
            return chat_history, is_prospect_first_msg
