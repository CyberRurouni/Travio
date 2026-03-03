import hashlib
import logging
from unittest import result
from core import db_select, get_agency_password

logger = logging.getLogger("CASE_AGENT")


async def fetch_agent_agency(agent_email: str):
    """
    Fetches the agency associated with the given agent email.
    """
    filters = {"agent_email": agent_email.strip().lower()}

    result = await db_select(
        table="agencies",
        fields="id,name",
        filters=filters,
        limit=1,
    )

    if result and len(result) > 0:
        agency_id = result[0].get("id")
        agency_name = result[0].get("name")
        app_password = await get_agency_password(agency_id)
        payload = {
            "agency_id": str(agency_id),
            "agency_name": agency_name,
            "app_password": app_password,
        }
        logger.info(
            "✅ Found agency for agent_email=%s | Agency ID=%s | Agency Name=%s",
            agent_email,
            agency_id,
            agency_name,
        )
        return payload

    logger.warning("❌ No agency found for agent_email=%s", agent_email)
    return None


async def fetch_prospect_id(identifier_hash: str, agency_id: str):
    """
    Fetches the prospect ID associated with the given prospect email hash.
    Uses 'prospect_id' instead of 'id' to match current contact_methods schema.
    """
    filters = {"identifier_hash": identifier_hash, "channel_type": "email"}

    global_prospects = await db_select(
        table="contact_methods",
        fields="prospect_id",
        filters=filters,
        limit=1,
    )

    if global_prospects and len(global_prospects) > 0:
        prospect_id = global_prospects[0].get("prospect_id")
        if prospect_id:
            try:
                result = await db_select(
                    table="agency_prospects",
                    filters={
                        "agency_id": str(agency_id),
                        "prospect_id": str(prospect_id),
                    },
                    fields="id",
                    limit=1,
                )
                if result and len(result) > 0:
                    agency_prospect_id = result[0].get("id")
                    if agency_prospect_id:
                        return str(agency_prospect_id)
            except Exception as e:
                logger.error("❌ Error fetching agency_prospect_id: %s", e)
                return None

    logger.warning("❌ No prospect found for identifier_hash=%s", identifier_hash)
    return None
