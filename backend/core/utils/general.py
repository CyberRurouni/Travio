import logging
import asyncio
import hashlib
from datetime import timedelta
from typing import List, Optional

from core import smtp_registry

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("GENERAL_UTILS")


# =========================================
# Generate embeddings (async-safe, retried)
# =========================================
async def generate_embeddings(text: str, attempts: int = 3, delay: float = 1.0) -> List[float]:
    """
    Get embedding vector for given text using OpenAI embeddings asynchronously.

    Tries up to `attempts` times (small backoff between tries) before giving up.
    Returns an empty list only after every attempt has failed — callers treat
    an empty list as a failure and must abort rather than proceed.
    """
    import time as _time

    for attempt in range(attempts):
        try:
            from core import client

            # Use asyncio.to_thread in case client is sync
            resp = await asyncio.to_thread(
                client.embeddings.create,
                model="openai/text-embedding-3-small",
                input=text,
            )
            embedding = resp.data[0].embedding
            if not embedding:
                raise ValueError("Embedding API returned an empty vector")
            return embedding
        except Exception as e:
            if attempt < attempts - 1:
                logger.warning(
                    f"💥 generate_embeddings attempt {attempt + 1}/{attempts} failed: {e}. Retrying..."
                )
                await asyncio.sleep(delay * (attempt + 1))
            else:
                logger.exception(
                    f"💥 generate_embeddings failed after {attempts} attempts: {e}"
                )
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
    from core import SMTPService

    return await smtp_registry.get_or_create(
        key=f"smtp:{issued_email}",
        factory=SMTPService,
        issued_email=issued_email,
        app_password=app_password,
        factory_type="sync",
    )


# =========================================
# Normalization
# =========================================
def normalize_text(value: Optional[str], mode: str = "lower") -> Optional[str]:
    """
    Normalize a text value.

    - Strips surrounding whitespace.
    - Applies casing based on mode:
        lower → lowercase
        upper → uppercase
        title → title case
    - Returns None if input is empty or None.
    """

    if not value:
        return None

    value = value.strip()

    if not value:
        return None

    if mode == "lower":
        return value.lower()

    if mode == "upper":
        return value.upper()

    if mode == "title":
        return value.title()

    return value


def normalize_list(values: Optional[List[str]]) -> Optional[List[str]]:
    """
    Normalize a list of text values.

    - Removes empty / None entries
    - Strips whitespace
    - Converts values to lowercase
    - Deduplicates entries

    Returns a cleaned list or None if no valid items remain.
    """

    if not values:
        return None

    cleaned = {v.strip().lower() for v in values if v and v.strip()}

    return list(cleaned) if cleaned else None


# =========================================
# Last Subject
# =========================================
def fetch_last_email_subject(agency_prospect_id, session_id):
    """
    A general helper function, used to fetch last subject incase subject isn't provided
    When is subject not provided?
    When user replies or forward a message, there last subject is required to maintain the seq.
    """

    from core import session_broker, safe_redis_operation

    last_subject_key = f"{agency_prospect_id}:{session_id}:last_subject"
    last_subject = safe_redis_operation(session_broker.get, last_subject_key)
    if last_subject:
        # Update ttl
        safe_redis_operation(
            session_broker.set,
            last_subject_key,
            last_subject,
            ex=timedelta(days=7),
        )

        return last_subject
    
    return "no subject"
