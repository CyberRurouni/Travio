import logging
import asyncio

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("GENERAL_UTILS")


# =========================================
# 1️⃣ Generate embeddings
# =========================================
async def generate_embedding(text: str) -> list[float]:
    """Get embedding vector for given text using OpenAI embeddings"""
    from core import client

    resp = client.embeddings.create(model="openai/text-embedding-3-small", input=text)
    return resp.data[0].embedding


# =========================================
# 2️⃣ Populate embeddings for all packages
# =========================================
async def populate_new_embeddings(BATCH_SIZE: int = 20):
    """Populate missing embeddings for agency_packages table"""
    from core import db_select, db_update
    rows = await db_select(
        "agency_packages",
        fields="id, name, description, destination",
        filters={"embedding": None}, 
    )

    if not rows:
        logger.info("✅ No new rows need embeddings.")
        return

    logger.info(f"🧠 Generating embeddings for {len(rows)} rows...")

    # ---- build texts
    texts = [
        f"{r['name']} {r['description'] or ''} {r['destination'] or ''}"
        for r in rows
    ]

    # ---- process in batches (faster + safer)
    for i in range(0, len(rows), BATCH_SIZE):
        batch_rows = rows[i:i+BATCH_SIZE]
        batch_texts = texts[i:i+BATCH_SIZE]

        # parallel embedding calls
        embeddings = await asyncio.gather(
            *[generate_embedding(t) for t in batch_texts]
        )

        # update DB
        for row, embedding in zip(batch_rows, embeddings):
            await db_update(
                "agency_packages",
                updates={"embedding": embedding},
                filters={"id": row["id"]},
            )

        logger.info(f"⚡ Updated {min(i+BATCH_SIZE, len(rows))}/{len(rows)}")

    logger.info("🎉 Embedding population complete.")
