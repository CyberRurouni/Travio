import asyncio
import logging
from core import db_rpc, generate_embeddings, call_openai_safe

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


async def generate_sql_from_request(request: dict, schema: dict) -> str:
    """
    Generate SQL using structured JSON input with constraints and vague exclusions.
    """

    prompt = f"""
You are a Postgres SQL generator for a travel recommendation system.

You MUST follow ALL rules strictly. Any violation makes the output INVALID.

────────────────────────────────────
INPUT
────────────────────────────────────
User Request:
{request}

TABLE NAME:
agency_packages

TABLE SCHEMA:
{schema}

────────────────────────────────────
ABSOLUTE OUTPUT RULES (MANDATORY)
────────────────────────────────────

1. Output ONLY valid JSON.
2. Do NOT include explanations, markdown, comments, or extra text.
3. Do NOT include semicolons.
4. The JSON MUST have exactly this schema:

{{
  "sql": "string"
}}

5. The SQL MUST start with SELECT.
6. Limit results to 10 rows.

────────────────────────────────────
ALLOWED SQL SCOPE
────────────────────────────────────

- SELECT only from agency_packages.
- Allowed columns in SELECT:
  id, name, description, destination, price_amount, price_currency, duration_days
- Always include:
  is_active = TRUE

────────────────────────────────────
STRICT VECTOR RULES (CRITICAL)
────────────────────────────────────

These rules are NON-NEGOTIABLE:

- Vector operators (<=>) ALWAYS return a number.
- Vector expressions MUST NEVER appear alone in WHERE or AND clauses.
- Vector expressions MUST ALWAYS be compared to a numeric threshold.

The ONLY valid vector usages are:

- ORDER BY embedding <=> query_embedding
- embedding <=> query_embedding < 0.8
- embedding <=> exclude_embedding < 0.3

query_embedding and exclude_embedding are PLACEHOLDERS.
They will be replaced by the application at runtime.

Any other vector usage is INVALID.

────────────────────────────────────
MAIN QUERY HANDLING
────────────────────────────────────

- "main_query" is a soft search and influences ranking.
- Do NOT combine main_query with constraints using AND.
- Always use embedding <=> query_embedding in ORDER BY.
- Main_query text MUST NOT appear in WHERE.

────────────────────────────────────
CONSTRAINT HANDLING
────────────────────────────────────

Apply constraints ONLY if they exist in the input.

- exclude_ids:
  AND id NOT IN (...)

- category / destination / price_currency / etc:
  - include → column IN (...)
  - exclude → column NOT IN (...)

- price_amount / duration_days / etc:
  Use BETWEEN min AND max

- ideal_for:
  Use array overlap:
  ideal_for && ARRAY[...]

────────────────────────────────────
VAGUE EXCLUSIONS
────────────────────────────────────

If "vague_exclusion" is a non-empty string:

- Add this clause:

  AND id NOT IN (
      SELECT id
      FROM agency_packages
      WHERE embedding <=> exclude_embedding < 0.3
        AND is_active = TRUE
  )

DO NOT insert vague text into SQL.
DO NOT use ILIKE or text matching.

────────────────────────────────────
ORDERING RULES
────────────────────────────────────

- ALWAYS order by vector similarity:
  ORDER BY embedding <=> query_embedding

- Vector ordering MUST NOT be removed.

────────────────────────────────────
EXAMPLES
────────────────────────────────────

Example 1:
INPUT:
{{
  "main_query": "Family-friendly adventure trips in Canada",
  "constraints": {{
    "destination": {{ "include": ["Canada"], "exclude": ["Quebec"] }},
    "category": {{ "include": ["adventure", "travel"] }}
  }},
  "vague_exclusion": "snowy regions"
}}

OUTPUT:
{{
  "sql": "SELECT id, name, description, destination, price_amount, price_currency, duration_days FROM agency_packages WHERE is_active = TRUE AND destination IN ('Canada') AND destination NOT IN ('Quebec') AND category IN ('adventure','travel') AND id NOT IN (SELECT id FROM agency_packages WHERE embedding <=> exclude_embedding < 0.3 AND is_active = TRUE) ORDER BY embedding <=> query_embedding LIMIT 10"
}}

Example 2:
INPUT:
{{
  "main_query": "Fun trips for a small group",
  "constraints": {{
    "exclude_ids": ["uuid-1", "uuid-2"]
  }},
  "vague_exclusion": ""
}}

OUTPUT:
{{
  "sql": "SELECT id, name, description, destination, price_amount, price_currency, duration_days FROM agency_packages WHERE is_active = TRUE AND id NOT IN ('uuid-1','uuid-2') ORDER BY embedding <=> query_embedding LIMIT 10"
}}

────────────────────────────────────
NOW GENERATE SQL
────────────────────────────────────

Generate SQL for the provided INPUT.
Return ONLY the JSON object.
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


async def smart_package_search(user_request: dict):
    schema = await get_packages_schema()

    sql = await generate_sql_from_request(user_request, schema)
    logger.info("🧠 AI generated SQL:\n%s", sql)

    # Vector embedding for main query
    if "<=>" in sql:
        query_embedding = await generate_embeddings(user_request["main_query"])
        sql = sql.replace("query_embedding", f"'{query_embedding}'")

    # Vector embedding for vague exclusions
    if "vague_exclusion" in user_request and "exclude_embedding" in sql:
        exclude_embedding = await generate_embeddings(user_request["vague_exclusion"])
        sql = sql.replace("exclude_embedding", f"'{exclude_embedding}'")

    results = await run_ai_query(sql)
    logger.info("🧠 AI query returned %d rows", len(results))

    return results


async def main():
    request = {
        "main_query": "I'm looking for fun and exciting travel experiences, maybe with adventure, nature, or cultural activities, for a small group or family. Budget is flexible, and duration can be around a week.",
        "constraints": {
            "category": {
                "include": ["adventure", "travel"],
                "exclude": ["corporate", "umrah"],
            },
            "price_amount": {"min": 500, "max": 10000},
            "price_currency": {"include": ["USD", "CAD"]},
            "ideal_for": {"include": ["family", "group"]},
        },
        "vague_exclusion": "avoid snowy mountains, eco-friendly packages",
    }
    results = await smart_package_search(request)

    logger.info("📦 Results:\n%s", results)


if __name__ == "__main__":
    asyncio.run(main())
