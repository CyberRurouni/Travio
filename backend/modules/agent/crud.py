import hashlib
from datetime import timedelta
from uuid import UUID
from core import db_select


async def fetch_agent_agency(agent_email: str):
    """
    Fetches the agency associated with the given agent email.
    """

    filters = {"agent_email": agent_email.strip().lower()}

    result = db_select(
        table="agencies",
        fields=["id"],
        filters=filters,
        limit=1,
    )
    if result and len(result) > 0:
        agency_id = result[0].get("id")
        if agency_id:

            if isinstance(agency_id, str):
                return agency_id

            else:
                return str(agency_id)
    return None

async def fetch_prospect_id(identifier_hash: str):
    """
    Fetches the prospect ID associated with the given prospect email.
    """

    filters = {"identifier_hash": identifier_hash, "channel_type": "email"}

    result = db_select(
        table="contact_methods",
        fields=["id"],
        filters=filters,
        limit=1,
    )
    if result and len(result) > 0:
        prospect_id = result[0].get("id")
        if prospect_id:

            if isinstance(prospect_id, str):
                return prospect_id

            else:
                return str(prospect_id)
    return None