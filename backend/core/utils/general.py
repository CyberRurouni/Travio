import logging
import asyncio

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("GENERAL_UTILS")


# =========================================
# 1️⃣ Generate embeddings
# =========================================
async def generate_embeddings(text: str) -> list[float]:
    """Get embedding vector for given text using OpenAI embeddings"""
    from core import client

    resp = client.embeddings.create(model="openai/text-embedding-3-small", input=text)
    return resp.data[0].embedding


# =========================================
# 2️⃣ Populate embeddings for all packages
# =========================================
async def populate_embeddings(BATCH_SIZE: int = 20):
    """Populate missing embeddings for agency_packages table"""
    from core import db_select, db_update

    # Fetch rows that don't have embeddings yet
    rows = await db_select(
        "agency_packages",
        fields="id, name, description, destination",
        filters={"embedding": None},
    )

    if not rows:
        logger.info("✅ No rows need embeddings.")
        return

    logger.info(f"🧠 Generating embeddings for {len(rows)} rows...")

    # Process in batches to limit API + DB concurrency
    for i in range(0, len(rows), BATCH_SIZE):
        batch_rows = rows[i : i + BATCH_SIZE]

        # Build embedding text for this batch
        batch_texts = [
            f"{r['name']} {r['description'] or ''} {r['destination'] or ''}"
            for r in batch_rows
        ]

        # Generate embeddings concurrently
        embeddings = await asyncio.gather(
            *(generate_embeddings(t) for t in batch_texts)
        )

        # Update rows concurrently
        await asyncio.gather(
            *[
                db_update(
                    "agency_packages",
                    updates={"embedding": embedding},
                    filters={"id": row["id"]},
                )
                for row, embedding in zip(batch_rows, embeddings)
            ]
        )

        logger.info(f"⚡ Updated {min(i + BATCH_SIZE, len(rows))}/{len(rows)}")

    logger.info("🎉 Embedding population complete.")
