import asyncio
import logging
import re
from core import db_rpc, db_select, populate_new_embeddings, call_openai_safe

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("AI_TRAVEL")


async def get_packages_schema():
    return await db_rpc(
        "get_table_schema",
        {
            "p_schema_name": "public",
            "p_table_name": "agency_packages",
            "is_sample_requested": True,
        },
    )


async def generate_sql_from_note(note: str, schema: dict) -> str:
    prompt = f"""
You are a Postgres SQL generator for the table 'agency_packages'.

- MUST respond with valid JSON ONLY.
- Do NOT use markdown, explanations, or extra text.
- Return ONLY a JSON object with this exact schema:

{{
  "sql": "string"
}}

Rules:

1. SELECT ONLY the 'id' column.
2. Apply filters inferred from the user intent softly:
   - Use OR for text keywords (name or description) to avoid over-filtering.
   - Include optional filters like 'ideal_for', 'category', 'destination' only if clearly relevant.
   - Apply strict filters (e.g., price limits, is_active = true) only when specified or obvious from intent.
3. Use pgvector similarity (<=>) only if the user mentions "similar", "like", or "recommend".
4. Limit results to 10 rows.
5. Optimize the query for efficiency.
6. Do NOT include extra columns.
7. Do NOT end the query with a semicolon.
8. Make the query **relevant to the user intent**, but **do not over-constrain** so results are returned.

Table schema:

{schema}

User intent:

{note}

Return ONLY a JSON object containing the SQL string.
"""

    resp = call_openai_safe(
        messages=[{"role": "user", "content": prompt}],
        response_format="json",
        fallback_response={"sql": ""},
    )

    sql = resp.get("sql", "").strip()

    if not sql.lower().startswith("select"):
        raise ValueError(f"Invalid SQL returned: {sql}")

    return sql


async def run_ai_query(sql: str):
    return await db_rpc("query_sql", {"sql": sql})


async def smart_package_search(note: str):
    schema = await get_packages_schema()

    sql = await generate_sql_from_note(note, schema)

    logger.info("🧠 AI generated SQL:\n%s", sql)

    ids = await run_ai_query(sql)
    logger.info("🧠 AI query returned IDs: %s", ids)

    results = []
    for item in ids:
        package_id = item["id"]  # <--- extract the UUID string
        result = await db_select(
            "agency_packages",
            fields="id, name, description, destination, price_amount, price_currency, duration_days",
            filters={"id": package_id},
        )
        if result:
            results.append(result[0])

    return results


async def main():
    note = "Winter adventure trips in Hokkaido for families lasting around 5-7 days with snow activities like skiing and festivals, budget up to 2500 JPY."

    await populate_new_embeddings()

    results = await smart_package_search(note)

    logger.info("📦 Results:\n%s", results)


if __name__ == "__main__":
    asyncio.run(main())
