import logging
from uuid import UUID
from typing import Any, Optional
from datetime import datetime, timezone
from core import db_select, db_rpc, db_update

logger = logging.getLogger("PROSPECT_CRUD")


async def fetch_agency_id_by_email(email: str) -> UUID | None:
    result = await db_select(
        table="agencies", filters={"issued_email": email}, fields="id"
    )
    if not isinstance(result, list) or not result:
        return None
    row = result[0]
    if not isinstance(row, dict):
        return None
    raw_id: Any = row.get("id")
    if isinstance(raw_id, UUID):
        return raw_id
    if isinstance(raw_id, str):
        try:
            return UUID(raw_id)
        except ValueError:
            return None
    return None


async def register_prospect(
    agency_id: UUID,
    name: str | None,
    channel_type: str,
    identifier_hash: str,
) -> UUID | None:
    """
    Registers a prospect and links them to the agency atomically via RPC.
    Returns agency_prospect_id — used for ALL downstream ops (sessions, presence).
    """
    try:
        result = await db_rpc(
            "register_prospect",
            params={
                "p_agency_id": str(agency_id),
                "p_name": name,
                "p_channel_type": channel_type,
                "p_identifier_hash": identifier_hash,
            },
        )

        if isinstance(result, str):
            return UUID(result)
        elif isinstance(result, list) and result:
            return UUID(str(result[0]))
        elif isinstance(result, dict):
            raw = result.get("register_prospect") or result.get("id")
            return UUID(str(raw)) if raw else None

        logger.error("❌ register_prospect returned nothing | Agency=%s", agency_id)
        return None

    except Exception as e:
        logger.error("❌ register_prospect failed | Agency=%s | Error=%s", agency_id, e)
        return None


async def touch_prospect_presence(
    agency_prospect_id: UUID,
    channel_type: str,
    raw_identifier: str,
) -> None:
    """
    Upserts prospect_presence. Sets last_contacted_at = now().
    """
    try:
        await db_rpc(
            "touch_prospect_presence",
            params={
                "p_agency_prospect_id": str(agency_prospect_id),
                "p_channel_type": channel_type.lower(),
                "p_raw_identifier": raw_identifier,
            },
        )

        logger.info(
            "✅ Presence upserted | AgencyProspectID=%s | Channel=%s",
            agency_prospect_id,
            channel_type,
        )

        try:
            await db_update(
                table="agency_prospects",
                filters={"id": str(agency_prospect_id)},
                updates={"last_contacted_at": datetime.now(timezone.utc)},
            )

            logger.info(
                "✅ last_contacted_at updated on agency_prospects | AgencyProspectID=%s",
                agency_prospect_id,
            )

        except Exception as e:
            logger.error(
                "❌ Failed to update last_contacted_at on agency_prospects | AgencyProspectID=%s | Error=%s",
                agency_prospect_id,
                e,
            )

    except Exception as e:
        logger.error(
            "❌ Presence upsert failed | AgencyProspectID=%s | Error=%s",
            agency_prospect_id,
            e,
        )


async def is_registered_prospect_by_email(email: str) -> Optional[UUID]:
    """Returns global prospect_id if email is registered. NOT agency_prospect_id."""
    from core import hash_identifier

    if not email:
        return None
    identifier_hash = hash_identifier(email)
    result = await db_select(
        table="contact_methods",
        filters={"channel_type": "email", "identifier_hash": identifier_hash},
        fields="prospect_id",
        limit=1,
    )
    if not isinstance(result, list) or not result:
        return None
    row = result[0]
    if not isinstance(row, dict):
        return None
    raw_id = row.get("prospect_id")
    if not raw_id:
        return None
    try:
        return UUID(str(raw_id))
    except Exception:
        return None
