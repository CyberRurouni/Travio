import hashlib
import logging
from core import db_select

logger = logging.getLogger("CASE_AGENT")


async def fetch_agent_agency(agent_email: str):
    """
    Fetches the agency associated with the given agent email.
    """
    filters = {"agent_email": agent_email.strip().lower()}

    result = await db_select(
        table="agencies",
        fields=["id"],
        filters=filters,
        limit=1,
    )

    if result and len(result) > 0:
        agency_id = result[0].get("id")
        if agency_id:
            return str(agency_id)

    logger.warning("❌ No agency found for agent_email=%s", agent_email)
    return None


async def fetch_prospect_id(identifier_hash: str):
    """
    Fetches the prospect ID associated with the given prospect email hash.
    Uses 'prospect_id' instead of 'id' to match current contact_methods schema.
    """
    filters = {"identifier_hash": identifier_hash, "channel_type": "email"}

    result = await db_select(
        table="contact_methods",
        fields=["prospect_id"], 
        filters=filters,
        limit=1,
    )

    if result and len(result) > 0:
        prospect_id = result[0].get("prospect_id")
        if prospect_id:
            return str(prospect_id)

    logger.warning("❌ No prospect found for identifier_hash=%s", identifier_hash)
    return None

