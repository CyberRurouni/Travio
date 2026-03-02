import logging
from uuid import UUID
from core import safe_redis_operation
from .helpers.prospect_helper import ProspectHelper

logger = logging.getLogger("PROSPECT")


class HandleProspect:
    def __init__(self, email: str, name: str, identifier: str):
        self.email = email
        self.name = name
        self.identifier = identifier
        self.agency_id: UUID | None = None
        self.agency_prospect_id: UUID | None = None  # replaces prospect_id

    @classmethod
    async def create(cls, issued_email: str, prospect_name: str, identifier: str):
        self = cls(issued_email, prospect_name, identifier)
        self.agency_id = await ProspectHelper.resolve_agency_id(issued_email)
        if not self.agency_id:
            logger.warning("❌ Could not resolve agency for issued_email: %s", issued_email)
        return self

    async def register(self, channel_type: str = "email") -> tuple[UUID | None, UUID | None]:
        """
        Returns (agency_id, agency_prospect_id).
        agency_prospect_id is used as the key for sessions and presence.
        """
        if not self.agency_id:
            logger.error("❌ Cannot register: agency_id missing")
            return None, None

        self.agency_prospect_id = await ProspectHelper.register_prospect(
            agency_id=self.agency_id,
            name=self.name,
            identifier=self.identifier,
            channel_type=channel_type,
        )
        return self.agency_id, self.agency_prospect_id

    async def mark_presence(self, channel_type: str = "email"):
        if not self.agency_prospect_id:
            logger.warning("❌ Cannot mark presence: agency_prospect_id missing")
            return
        await ProspectHelper.update_presence(
            agency_prospect_id=self.agency_prospect_id,
            identifier=self.identifier,
            channel_type=channel_type,
        )