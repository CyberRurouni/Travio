import json
import asyncio
import logging
from datetime import timedelta
from uuid import UUID
from typing import Optional
from core import session_broker, safe_redis_operation
from ..crud import fetch_ongoing_session, initiate_session as crud_initiate_session

logger = logging.getLogger("SESSION")


class SessionHelper:
    """Unified helper for session caching, DB access, chat buffers, and persistence."""

    # -------------------- Redis keys --------------------
    @staticmethod
    def _session_cache_key(prospect_id: str) -> str:
        return f"{prospect_id}:ongoing_session"

    @staticmethod
    def _first_impression_cache_key(prospect_id: str) -> str:
        return f"{prospect_id}:ongoing_session:first_impression"

    @staticmethod
    def _chat_buffer_key(session_id: str) -> str:
        return f"chat:{session_id}:buffer"

    @staticmethod
    def _chat_word_count_key(session_id: str) -> str:
        return f"chat:{session_id}:word_count"

    @staticmethod
    def _ttl_seconds(ttl: timedelta) -> int:
        return int(ttl.total_seconds())

    @staticmethod
    def _chat_ttl_seconds() -> int:
        return 60 * 60  # 1 hour

    # -------------------- Session Cache --------------------
    @staticmethod
    def get_cached_session_id(prospect_id: str, ttl: timedelta) -> Optional[UUID]:
        cache_key = SessionHelper._session_cache_key(prospect_id)
        cached_session_id = safe_redis_operation(session_broker.get, cache_key)
        if not cached_session_id:
            return None
        try:
            session_id = UUID(cached_session_id)
        except ValueError:
            logger.warning(
                "⚠️ Invalid session ID in Redis | Prospect ID=%s", prospect_id
            )
            return None
        safe_redis_operation(
            session_broker.expire, cache_key, SessionHelper._ttl_seconds(ttl)
        )
        logger.info(
            "🔁 Session ID fetched from Redis | Prospect ID=%s | Session ID=%s",
            prospect_id,
            session_id,
        )
        return session_id

    @staticmethod
    def get_cached_first_impression(prospect_id: str, ttl: timedelta) -> Optional[dict]:
        cache_key = SessionHelper._first_impression_cache_key(prospect_id)
        cached_value = safe_redis_operation(session_broker.get, cache_key)
        if not cached_value:
            return None
        try:
            first_impression = json.loads(cached_value)
        except Exception:
            logger.warning(
                "⚠️ Corrupted first impression in Redis | Prospect ID=%s", prospect_id
            )
            return None
        safe_redis_operation(
            session_broker.expire, cache_key, SessionHelper._ttl_seconds(ttl)
        )
        logger.info(
            "🧠 First impression fetched from Redis | Prospect ID=%s", prospect_id
        )
        return first_impression

    @staticmethod
    async def fetch_and_cache_first_impression(
        prospect_id: str, ttl: timedelta
    ) -> Optional[dict]:
        logger.info(
            "🛢️ Redis miss for first impression, hitting DB | Prospect ID=%s",
            prospect_id,
        )
        session_data = await fetch_ongoing_session(
            prospect_id=prospect_id, fields="first_impression"
        )
        if not session_data or not isinstance(session_data, dict):
            logger.info(
                "ℹ️ No first impression found in DB | Prospect ID=%s", prospect_id
            )
            return None
        first_impression = session_data.get("first_impression")
        if not first_impression:
            logger.info(
                "ℹ️ Session exists but first impression is empty | Prospect ID=%s",
                prospect_id,
            )
            return None
        safe_redis_operation(
            session_broker.set,
            SessionHelper._first_impression_cache_key(prospect_id),
            json.dumps(first_impression),
            ex=SessionHelper._ttl_seconds(ttl),
        )
        logger.info(
            "🚀 Backfilled first impression into Redis | Prospect ID=%s", prospect_id
        )
        return first_impression

    # -------------------- Session Creation --------------------
    @staticmethod
    async def create_session_in_db(
        prospect_id: str, payload: dict, ttl: timedelta
    ) -> Optional[UUID]:
        session_id = await crud_initiate_session(
            prospect_id=prospect_id, payload=payload
        )
        if not session_id:
            logger.error(
                "❌ Failed to create session in DB | Prospect ID=%s", prospect_id
            )
            return None

        # Cache session ID
        safe_redis_operation(
            session_broker.set,
            SessionHelper._session_cache_key(prospect_id),
            str(session_id),
            ex=SessionHelper._ttl_seconds(ttl),
        )

        # Cache first impression
        first_impression = payload.get("first_impression")
        if first_impression:
            safe_redis_operation(
                session_broker.set,
                SessionHelper._first_impression_cache_key(prospect_id),
                json.dumps(first_impression),
                ex=SessionHelper._ttl_seconds(ttl),
            )
            logger.info(
                "🚀 Cached first impression for new session | Prospect ID=%s",
                prospect_id,
            )

        logger.info(
            "✅ New session created | Prospect ID=%s | Session ID=%s",
            prospect_id,
            session_id,
        )
        return session_id

    # -------------------- Resolve Session --------------------
    @staticmethod
    async def resolve_session(
        prospect_id: str, ttl: timedelta
    ) -> tuple[Optional[UUID], Optional[dict]]:
        session_id = SessionHelper.get_cached_session_id(prospect_id, ttl)
        first_impression = None
        if session_id:
            first_impression = SessionHelper.get_cached_first_impression(
                prospect_id, ttl
            )
            if first_impression is None:
                first_impression = await SessionHelper.fetch_and_cache_first_impression(
                    prospect_id, ttl
                )
            logger.info("✅ Session resolved via cache | Prospect ID=%s", prospect_id)
            return session_id, first_impression

        # DB fallback
        session_data = await fetch_ongoing_session(
            prospect_id=prospect_id, fields="id,first_impression"
        )
        if session_data and isinstance(session_data, dict):
            session_id = session_data.get("id")
            first_impression = session_data.get("first_impression")

            # Backfill Redis
            if session_id:
                safe_redis_operation(
                    session_broker.set,
                    SessionHelper._session_cache_key(prospect_id),
                    str(session_id),
                    ex=SessionHelper._ttl_seconds(ttl),
                )
            if first_impression:
                safe_redis_operation(
                    session_broker.set,
                    SessionHelper._first_impression_cache_key(prospect_id),
                    json.dumps(first_impression),
                    ex=SessionHelper._ttl_seconds(ttl),
                )
                logger.info(
                    "🧠 Backfilled first impression into Redis | Prospect ID=%s",
                    prospect_id,
                )

            logger.info(
                "🔁 Session resolved via DB | Prospect ID=%s | Session ID=%s",
                prospect_id,
                session_id,
            )
            return session_id, first_impression

        logger.info("ℹ️ No existing session found | Prospect ID=%s", prospect_id)
        return None, None

    # -------------------- Chat Buffer --------------------
    @staticmethod
    def add_message_to_chat_buffer(session_id: str, msg_data: dict) -> int:
        buffer_key = SessionHelper._chat_buffer_key(session_id)
        word_count_key = SessionHelper._chat_word_count_key(session_id)
        ttl = SessionHelper._chat_ttl_seconds()

        # Push message
        safe_redis_operation(session_broker.rpush, buffer_key, json.dumps(msg_data))

        # Update word count
        current_words = safe_redis_operation(session_broker.get, word_count_key) or 0
        if isinstance(current_words, bytes):
            current_words = int(current_words.decode("utf-8"))
        elif isinstance(current_words, str):
            current_words = int(current_words)
        total_words = current_words + msg_data.get("words", 0)
        safe_redis_operation(session_broker.set, word_count_key, total_words)

        # Renew TTL
        safe_redis_operation(session_broker.expire, buffer_key, ttl)
        safe_redis_operation(session_broker.expire, word_count_key, ttl)

        logger.debug(
            "💬 Message added | Session=%s | Sender=%s | Words=%d | TotalWords=%d",
            session_id,
            msg_data.get("sender"),
            msg_data.get("words", "text"),
            total_words,
        )
        return total_words

    @staticmethod
    async def fetch_chat_history(session_id: str) -> tuple[list[dict], bool]:
        buffer_key = SessionHelper._chat_buffer_key(session_id)
        ttl = SessionHelper._chat_ttl_seconds()
        chat_history = []

        # Try Redis first
        try:
            raw_msgs = (
                safe_redis_operation(session_broker.lrange, buffer_key, 0, -1) or []
            )
            if raw_msgs:
                chat_history = [json.loads(msg) for msg in raw_msgs]
        except Exception as e:
            logger.error(
                "❌ Failed to read chat buffer from Redis | Session=%s | Error=%s",
                session_id,
                e,
            )

        # DB fallback
        if not chat_history:
            from ..crud import fetch_session_chats

            try:
                db_messages = await fetch_session_chats(session_id)
                if db_messages:
                    chat_history = db_messages
                    for msg in db_messages:
                        safe_redis_operation(
                            session_broker.rpush, buffer_key, json.dumps(msg)
                        )
                    total_words = sum(
                        len(str(msg.get("text") or "").split()) for msg in db_messages
                    )
                    safe_redis_operation(
                        session_broker.set,
                        SessionHelper._chat_word_count_key(session_id),
                        total_words,
                    )
                    safe_redis_operation(session_broker.expire, buffer_key, ttl)
                    safe_redis_operation(
                        session_broker.expire,
                        SessionHelper._chat_word_count_key(session_id),
                        ttl,
                    )
                    logger.info(
                        "📥 Redis buffer backfilled from DB | Session=%s | Messages=%d",
                        session_id,
                        len(db_messages),
                    )
            except Exception as e:
                logger.error(
                    "❌ Failed to fetch chat history from DB | Session=%s | Error=%s",
                    session_id,
                    e,
                )

        # Determine if first message (from the prospect)
        is_first_prospect_msg = 0 < len(chat_history) < 2

        # Pop words count
        chat_history = [
            {"sender": m.get("sender", ""), "text": m.get("text", "")}
            for m in chat_history
        ]

        return chat_history, is_first_prospect_msg

    @staticmethod
    async def persist_message(session_id: str, text: str, sender: str):
        from ..crud import create_session_message

        try:
            await create_session_message(session_id=session_id, sender=sender, msg=text)
        except Exception as e:
            logger.error(
                "❌ Failed to persist chat message | Session=%s | Sender=%s | Error=%s",
                session_id,
                sender,
                e,
            )

    # -------------------- Chat Compaction Persistence --------------------
    @staticmethod
    async def persist_compacted_chat(session_id: str, essence: list[dict]):
        """
        Persist the compressed chat history (essence) to both DB and Redis.

        Steps:
        1. Delete all old chat messages in DB.
        2. Store new compacted messages in DB.
        3. Clear Redis chat buffer and refill with new messages.

        This ensures the session continues with a concise chat history.
        """
        from ..crud import delete_all_chats, create_session_message

        # -------------------- Clear old DB --------------------
        await delete_all_chats(session_id=session_id)

        # -------------------- Persist compacted messages to DB --------------------
        db_tasks = [
            create_session_message(
                session_id=session_id, sender=msg.get("sender", ""), msg=msg.get("text", "")
            )
            for msg in essence
        ]
        await asyncio.gather(*db_tasks)

        # -------------------- Refresh Redis buffer --------------------
        buffer_key = SessionHelper._chat_buffer_key(session_id)
        word_count_key = SessionHelper._chat_word_count_key(session_id)

        # Clear existing Redis buffer
        safe_redis_operation(session_broker.delete, buffer_key)
        safe_redis_operation(session_broker.delete, word_count_key)

        # Refill Redis buffer
        for msg in essence:
            SessionHelper.add_message_to_chat_buffer(session_id, msg)

        logger.info(
            "♻️ Compacted chat persisted | Session=%s | Messages=%d",
            session_id,
            len(essence),
        )
