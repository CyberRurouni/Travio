import hashlib
import logging
from uuid import UUID
from datetime import timedelta
from core import session_broker, prospects_broker, safe_redis_operation
from ..crud import fetch_agency_id_by_email, register_prospect, touch_prospect_presence

logger = logging.getLogger("PROSPECT_HELPER")


class ProspectHelper:

    @staticmethod
    def _agency_cache_key(email: str) -> str:
        return f"agency_id_by_email:{email.lower()}"

    @staticmethod
    def _agency_prospect_cache_key(agency_id: UUID, identifier: str) -> str:
        # Cache key now stores agency_prospect_id (not prospect_id)
        identifier_hash = hashlib.sha256(identifier.encode("utf-8")).hexdigest()
        return f"agency_prospect:{agency_id}:{identifier_hash}"

    @staticmethod
    def _presence_cache_key(agency_prospect_id: UUID, channel_type: str) -> str:
        return f"presence:{agency_prospect_id}:{channel_type.lower()}"

    @staticmethod
    async def resolve_agency_id(
        email: str, ttl: timedelta = timedelta(hours=24)
    ) -> UUID | None:
        if not email:
            return None
        cache_key = ProspectHelper._agency_cache_key(email)
        cached = safe_redis_operation(session_broker.get, cache_key)
        if cached:
            try:
                return UUID(cached)
            except ValueError:
                pass
        agency_id = await fetch_agency_id_by_email(email=email)
        if agency_id:
            safe_redis_operation(
                session_broker.set,
                cache_key,
                str(agency_id),
                ex=int(ttl.total_seconds()),
            )
        return agency_id

    @staticmethod
    async def register_prospect(
        agency_id: UUID,
        name: str,
        identifier: str,
        channel_type: str = "email",
        ttl_seconds: int = 7 * 24 * 60 * 60,
    ) -> UUID | None:
        """
        Returns agency_prospect_id.
        Cache key and stored value both scoped to agency + identifier.
        """
        if not agency_id:
            return None

        cache_key = ProspectHelper._agency_prospect_cache_key(agency_id, identifier)
        cached = safe_redis_operation(prospects_broker.get, cache_key)
        if cached:
            try:
                agency_prospect_id = UUID(
                    cached.decode("utf-8") if isinstance(cached, bytes) else cached
                )
                safe_redis_operation(prospects_broker.expire, cache_key, ttl_seconds)
                logger.debug("⚡ AgencyProspect cache hit | ID=%s", agency_prospect_id)
                return agency_prospect_id
            except Exception as e:
                logger.warning("⚠️ Invalid agency_prospect cache, clearing | err=%s", e)
                safe_redis_operation(prospects_broker.delete, cache_key)

        agency_prospect_id = await register_prospect(
            agency_id=agency_id,
            name=name,
            channel_type=channel_type.lower(),
            identifier_hash=hashlib.sha256(identifier.encode("utf-8")).hexdigest(),
        )

        if agency_prospect_id:
            safe_redis_operation(
                prospects_broker.set, cache_key, str(agency_prospect_id), ex=ttl_seconds
            )
            logger.info(
                "✅ AgencyProspect registered and cached | ID=%s", agency_prospect_id
            )

        return agency_prospect_id

    @staticmethod
    async def update_presence(
        agency_prospect_id: UUID,
        identifier: str,
        channel_type: str = "email",
    ):
        """Debounced presence update using agency_prospect_id."""
        if not agency_prospect_id:
            return

        cache_key = ProspectHelper._presence_cache_key(agency_prospect_id, channel_type)
        cached = safe_redis_operation(prospects_broker.get, cache_key)
        if cached:
            logger.debug(
                "⚡ Presence cache hit, skipping DB | AgencyProspectID=%s",
                agency_prospect_id,
            )
            return

        try:
            await touch_prospect_presence(
                agency_prospect_id=agency_prospect_id,
                channel_type=channel_type.lower(),
                raw_identifier=identifier,
            )
            safe_redis_operation(prospects_broker.set, cache_key, "1", ex=3600)
            logger.info(
                "✅ Presence updated | AgencyProspectID=%s | Channel=%s",
                agency_prospect_id,
                channel_type,
            )
        except Exception as e:
            logger.error(
                "❌ Presence update failed | AgencyProspectID=%s | Error=%s",
                agency_prospect_id,
                e,
            )
