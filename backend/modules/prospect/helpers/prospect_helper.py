import json
import hashlib
import logging
from uuid import UUID
from datetime import timedelta
from core import session_broker, prospects_broker, safe_redis_operation
from ..crud import fetch_agency_id_by_email, register_prospect, touch_prospect_presence

logger = logging.getLogger("PROSPECT_HELPER")


class ProspectHelper:
    """Helper for prospect registration, caching, and presence tracking."""

    @staticmethod
    def _agency_cache_key(email: str) -> str:
        return f"agency_id_by_email:{email.lower()}"

    @staticmethod
    def _prospect_cache_key(agency_id: UUID, identifier: str) -> str:
        identifier_hash = hashlib.sha256(identifier.encode("utf-8")).hexdigest()
        return f"prospect:{agency_id}:{identifier_hash}"

    @staticmethod
    def _presence_cache_key(prospect_id: UUID, channel_type: str) -> str:
        return f"presence:{prospect_id}:{channel_type.lower()}"

    @staticmethod
    async def resolve_agency_id(email: str, ttl: timedelta = timedelta(hours=24)) -> UUID | None:
        """Fetch agency_id by email using Redis cache or DB fallback."""
        if not email:
            logger.error("❌ Missing email for agency lookup")
            return None

        cache_key = ProspectHelper._agency_cache_key(email)
        cached = safe_redis_operation(session_broker.get, cache_key)

        if cached:
            try:
                agency_id = UUID(cached)
                logger.debug("🔁 Agency ID loaded from cache | Email=%s | Agency ID=%s", email, agency_id)
                return agency_id
            except ValueError:
                logger.warning("⚠️ Invalid agency_id in cache | Email=%s", email)

        # DB fallback
        agency_id = await fetch_agency_id_by_email(email=email)
        if agency_id:
            safe_redis_operation(session_broker.set, cache_key, str(agency_id), ex=int(ttl.total_seconds()))
            logger.debug("✅ Agency ID cached | Email=%s | Agency ID=%s", email, agency_id)
        else:
            logger.info("ℹ️ No agency found for email | Email=%s", email)

        return agency_id

    @staticmethod
    async def register_prospect(
        agency_id: UUID,
        name: str,
        identifier: str,
        channel_type: str = "email",
        ttl_seconds: int = 7 * 24 * 60 * 60
    ) -> UUID | None:
        """Register a prospect with caching to avoid repeated DB writes."""
        if not agency_id:
            logger.error("❌ Cannot register prospect: agency_id not provided")
            return None

        cache_key = ProspectHelper._prospect_cache_key(agency_id, identifier)
        cached = safe_redis_operation(prospects_broker.get, cache_key)
        if cached:
            try:
                prospect_id = UUID(cached.decode("utf-8") if isinstance(cached, bytes) else cached)
                safe_redis_operation(prospects_broker.expire, cache_key, ttl_seconds)
                logger.debug("⚡ Prospect cache hit, TTL renewed | Prospect ID=%s", prospect_id)
                return prospect_id
            except Exception as e:
                logger.warning("⚠️ Invalid prospect cache, clearing | err=%s", e)
                safe_redis_operation(prospects_broker.delete, cache_key)

        # DB registration
        try:
            result = await register_prospect(
                agency_id=agency_id,
                name=name,
                channel_type=channel_type.lower(),
                identifier_hash=hashlib.sha256(identifier.encode("utf-8")).hexdigest(),
            )
            prospect_id = result.get("id") if isinstance(result, dict) else result
        except Exception as e:
            logger.error("❌ Prospect registration failed | Agency=%s | Error=%s", agency_id, e)
            return None

        if prospect_id:
            safe_redis_operation(prospects_broker.set, cache_key, str(prospect_id), ex=ttl_seconds)
            logger.info("✅ Prospect registered and cached | Prospect ID=%s", prospect_id)

        return prospect_id

    @staticmethod
    async def update_presence(prospect_id: UUID, identifier: str, channel_type: str = "email"):
        """Update prospect presence, cached to debounce frequent DB writes."""
        if not prospect_id:
            logger.error("❌ Cannot update presence: prospect_id not set")
            return

        cache_key = ProspectHelper._presence_cache_key(prospect_id, channel_type)
        ttl_seconds = 60 * 60  # 1 hour

        cached = safe_redis_operation(prospects_broker.get, cache_key)
        if cached:
            logger.debug("⚡ Presence cache hit, skipping DB | Prospect ID=%s | Channel=%s", prospect_id, channel_type)
            return

        try:
            await touch_prospect_presence(
                prospect_id=prospect_id,
                channel_type=channel_type.lower(),
                raw_identifier=identifier,
            )
            safe_redis_operation(prospects_broker.set, cache_key, "1", ex=ttl_seconds)
            logger.info("✅ Prospect presence updated | Prospect ID=%s | Channel=%s", prospect_id, channel_type)
        except Exception as e:
            logger.error(
                "❌ Failed to update presence | Prospect ID=%s | Channel=%s | Error=%s",
                prospect_id,
                channel_type,
                e,
            )
