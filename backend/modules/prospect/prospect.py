import logging
from uuid import UUID
from core import safe_redis_operation
from .helpers.prospect_helper import ProspectHelper

logger = logging.getLogger("PROSPECT")


class HandleProspect:
    """
    Lightweight orchestrator for a prospect.
    All heavy lifting (cache, DB, presence) is handled by ProspectHelper.
    """

    def __init__(self, email: str, name: str, identifier: str):
        self.email = email
        self.name = name
        self.identifier = identifier
        self.agency_id: UUID | None = None
        self.prospect_id: UUID | None = None

    @classmethod
    async def create(cls, issued_email: str, prospect_name: str, identifier: str):
        """Factory to initialize prospect and resolve agency_id."""
        self = cls(issued_email, prospect_name, identifier)
        self.agency_id = await ProspectHelper.resolve_agency_id(issued_email)
        if not self.agency_id:
            logger.warning("❌ Could not resolve agency for issued_email: %s", issued_email)
        return self

    async def register(self, channel_type: str = "email") -> tuple[UUID | None, UUID | None]:
        """
        Register the prospect (or use cache).
        Returns (agency_id, prospect_id)
        """
        if not self.agency_id:
            logger.error("❌ Cannot register prospect: agency_id missing")
            return None, None

        self.prospect_id = await ProspectHelper.register_prospect(
            agency_id=self.agency_id,
            name=self.name,
            identifier=self.identifier,
            channel_type=channel_type
        )
        return self.agency_id, self.prospect_id

    async def mark_presence(self, channel_type: str = "email"):
        """Update prospect presence in DB and cache."""
        if not self.prospect_id:
            logger.warning("❌ Cannot mark presence: prospect_id missing")
            return
        await ProspectHelper.update_presence(
            prospect_id=self.prospect_id,
            identifier=self.identifier,
            channel_type=channel_type
        )
