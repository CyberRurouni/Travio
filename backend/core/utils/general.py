import logging
import asyncio
import hashlib
from datetime import timedelta
from typing import List

from realtime import Optional

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("GENERAL_UTILS")


# =========================================
# Generate embeddings (async-safe)
# =========================================
async def generate_embeddings(text: str) -> List[float]:
    """
    Get embedding vector for given text using OpenAI embeddings asynchronously.
    """
    try:
        from core import client

        # Use asyncio.to_thread in case client is sync
        resp = await asyncio.to_thread(
            client.embeddings.create,
            model="openai/text-embedding-3-small",
            input=text,
        )
        return resp.data[0].embedding
    except Exception as e:
        logger.exception(f"💥 generate_embeddings failed: {e}")
        return []


# =========================================
# Generate and store embedding for a single package
# =========================================
async def generate_package_embedding(package: dict) -> Optional[List[float]]:
    """
    Generate embedding for a single agency package and store in DB.
    Accepts the full package dict to avoid extra DB read.
    """
    try:
        from core import db_update, format_packages

        package_id = package.get("id")
        if not package_id:
            logger.error("❌ Package dict missing 'id'")
            return None

        logger.info(f"🧠 Generating semantic embedding for package={package_id}")

        # Format the package for better semantic context
        formatted = format_packages([package])
        if not formatted:
            logger.error("❌ Formatting failed")
            return None

        embedding_text = formatted[0]["details"]

        # Generate embedding
        embedding = await generate_embeddings(embedding_text)
        if not embedding:
            logger.error("❌ Embedding generation failed")
            return None

        # Store in DB
        await db_update(
            table="agency_packages",
            updates={"embedding": embedding},
            filters={"id": package_id},
        )

        logger.info(f"✅ Embedding stored for package={package_id}")
        return embedding

    except Exception as e:
        logger.exception(f"💥 generate_package_embedding failed: {e}")
        return None


# =========================================
# Hashing utility for contact identifiers
# =========================================
def hash_identifier(raw_identifier: str) -> str:
    """Deterministic SHA256 hash for contact identifier."""
    return hashlib.sha256(raw_identifier.strip().lower().encode()).hexdigest()


# =========================================
# SMTP Service Instance Getter
# =========================================
async def get_smtp_service(issued_email, app_password):
    from core import InstanceRegistry, SMTPService

    smtp_registry = InstanceRegistry(ttl=timedelta(hours=6))
    return await smtp_registry.get_or_create(
        key="smtp_service",
        factory=SMTPService,
        issued_email=issued_email,
        app_password=app_password,
        factory_type="sync",
    )
