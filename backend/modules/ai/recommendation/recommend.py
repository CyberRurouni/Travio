import logging
from .components.request import formulate_request
from .utils import format_packages, format_search_request, insert_recommendation_event, insert_recommendation_item
from core import (
    fetch_table_schema,
    run_sql_query,
    generate_embeddings,
    call_openai_safe,
)

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("AI_TRAVEL")


async def get_packages_schema():
    return await fetch_table_schema("public", "agency_packages", include_sample=False)


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


async def smart_package_search(user_request: dict) -> list[dict]:
    schema = await get_packages_schema()
    sql = await generate_sql_from_request(user_request, schema)
    request_embedding = ""
    logger.info("🧠 AI generated SQL:\n%s", sql)

    # Vector embedding for main query
    if "<=>" in sql:
        request_embedding = await generate_embeddings(user_request["main_query"])
        sql = sql.replace("query_embedding", f"'{request_embedding}'")

    # Vector embedding for vague exclusions
    if "vague_exclusion" in user_request and "exclude_embedding" in sql:
        exclude_embedding = await generate_embeddings(user_request["vague_exclusion"])
        sql = sql.replace("exclude_embedding", f"'{exclude_embedding}'")

    results = await run_sql_query(sql)

    return results, request_embedding


async def db_scanning(user_note: str, session_id: str):
    """
    Simulates scanning the database for packages based on the user note.
    Returns both structured objects and formatted text blocks.
    """
    # Formulate structured request from the note
    search_request = formulate_request(user_note)
    logger.info("🔍 Formulated search request:\n%s", search_request)

    # Fetch raw results
    raw_search_results, request_embedding = await smart_package_search(search_request)

    # Format results
    formatted_packages = format_packages(raw_search_results)
    package_text_blocks = [entry["details"] for entry in formatted_packages]

    # Db insert for recommendation event and items
    formatted_request = format_search_request(search_request)
    if not request_embedding:
       request_embedding = await generate_embeddings(formatted_request)
    
    recommendation_event_id = await insert_recommendation_event(session_id, request_embedding, formatted_request)
    if recommendation_event_id and package_text_blocks:
        for package in package_text_blocks:
            memory_embedding = await generate_embeddings(f"User Request: {search_request['main_query']}\nPackage Details: {package}")
            await insert_recommendation_item(session_id, recommendation_event_id, package, memory_embedding)

    # Generate summary message
    num_packages = len(raw_search_results)
    if num_packages == 0:
        summary_message = "⚠️ No packages found matching the criteria."
        logger.info(summary_message)
    elif num_packages == 1:
        summary_message = "✅ 1 package found matching the criteria."
        logger.info(summary_message)
    else:
        summary_message = f"✅ {num_packages} packages found matching the criteria."
        logger.info(summary_message)

    logger.info("📄 Formatted package results:\n%s", "\n\n".join(package_text_blocks))


    return {
        "results": formatted_packages,
        "message": summary_message,
    }
    

