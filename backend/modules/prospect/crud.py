import logging
from uuid import UUID
from typing import Any, List, Dict, Optional, Union
from core import db_insert, db_select, db_rpc

logger = logging.getLogger("SESSION_CRUD")


async def fetch_agency_id_by_email(email: str) -> UUID | None:
    filters = {"issued_email": email}
    result = await db_select(table="agencies", filters=filters, fields="id")

    if not isinstance(result, list) or not result:
        return None

    row = result[0]

    if not isinstance(row, dict):
        return None

    raw_id: Any = row.get("id")
    if raw_id is None:
        return None

    if isinstance(raw_id, UUID):
        return raw_id

    if isinstance(raw_id, str):
        try:
            return UUID(raw_id)
        except ValueError:
            return None

    return None


async def register_prospect(
    agency_id: UUID, name: str | None, channel_type: str, identifier_hash: str
):
    """Registers a new prospect in the database with atomicity."""
    try:
        prospect_data = {
            "p_agency_id": str(agency_id),
            "p_name": name,
            "p_channel_type": channel_type,
            "p_identifier_hash": identifier_hash,
        }
        result = await db_rpc("register_prospect", params=prospect_data)
        logger.info(f"✅ Prospect registered | Agency ID={agency_id} | Name={name}")
        return result
    except Exception as e:
        logger.error(
            f"❌ Failed to register prospect | Agency ID={agency_id} | Error={str(e)}"
        )
        return None


async def upsert_prospect_presence(
    prospect_id: UUID, channel_type: str, raw_identifier: str
):
    """
    Trigger the Postgres upsert_prospect_presence function.
    Handles TTL renewal and deletion of silent prospects.

    Args:
        prospect_id: UUID of the prospect
        channel_type: The channel type, e.g., 'email', 'whatsapp'
        raw_identifier: Raw contact info (phone/email)
    """
    try:
        payload = {
            "p_prospect_id": str(prospect_id),
            "p_channel_type": channel_type.lower(),
            "p_raw_identifier": raw_identifier,
        }

        result = await db_rpc("upsert_prospect_presence", params=payload)

        logger.info(
            "✅ Prospect presence upserted | Prospect ID=%s | Channel=%s",
            prospect_id,
            channel_type,
        )

        return result

    except Exception as e:
        logger.error(
            "❌ Failed to upsert prospect presence | Prospect ID=%s | Channel=%s | Error=%s",
            prospect_id,
            channel_type,
            e,
        )
        return None