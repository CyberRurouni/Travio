import logging, json
from .components.request import formulate_request
from .utils import (
    format_packages,
    insert_recommendation_event,
    insert_recommendation_item,
)
from core import (
    fetch_table_schema,
    run_sql_query,
    generate_embeddings,
    call_openai_safe,
    safe_redis_operation,
    recommendation_broker,
)

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("AI_TRAVEL")


async def get_packages_schema():
    return await fetch_table_schema("public", "agency_packages", include_sample=False)


async def generate_sql_from_request(request: dict, schema: dict) -> str:
    """
    Generate SQL using structured JSON input with constraints and vague exclusions.

    This prompt is fully deterministic and example-driven to enforce strict adherence.
    """

    prompt = f"""
You are a Postgres SQL generator for a travel recommendation system. You MUST follow **all rules below strictly**. Any violation = INVALID.

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
MANDATORY OUTPUT RULES
────────────────────────────────────

1. Output **only valid JSON**: 
   {{ "sql": "string" }}

2. Do NOT include:
   - Explanations
   - Markdown
   - Comments
   - Semicolon at the end
   - Any text outside the JSON object

3. SQL rules:
   - Must start with SELECT
   - Allowed columns: id, name, description, destination, price_amount, price_currency, duration_days
   - Always include: is_active = TRUE
   - Limit results to 5 rows if limit missing, 10 rows max if limit > 10

────────────────────────────────────
VECTOR RULES (NON-NEGOTIABLE)
────────────────────────────────────

- Vector operator <=> **always returns a number**
- Vector expressions:
    - NEVER appear alone in WHERE/AND
    - ALWAYS compared to numeric threshold
- Only valid usages:
    - ORDER BY embedding <=> query_embedding
    - embedding <=> query_embedding < 0.8
    - embedding <=> exclude_embedding < 0.3
- No other vector usage allowed
- main_query text must never appear in WHERE

────────────────────────────────────
CONSTRAINT RULES
────────────────────────────────────

- exclude_previous_ids = true → include:
    AND id NOT IN (EXCLUDE_PREV_IDS_CLAUSE)
- Include/exclude columns:
    - Use column IN (...) or column NOT IN (...)
- Numeric ranges (price_amount/duration_days):
    - Use BETWEEN min AND max
- Array columns (ideal_for, includes):
    - Use array overlap: column && ARRAY[...]

────────────────────────────────────
VAGUE EXCLUSIONS
────────────────────────────────────

- If vague_exclusion is non-empty, add:

  AND id NOT IN (
      SELECT id
      FROM agency_packages
      WHERE embedding <=> exclude_embedding < 0.3
        AND is_active = TRUE
  )

- Never use text matching (ILIKE) or insert vague_exclusion text

────────────────────────────────────
ORDERING
────────────────────────────────────

- ALWAYS:
    ORDER BY embedding <=> query_embedding
- Vector ordering MUST NOT be removed
- main_query only affects ORDER BY, not WHERE

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
  "vague_exclusion": "snowy regions",
  "exclude_previous_ids": true,
  "limit": 3
}}

OUTPUT:
{{
  "sql": "SELECT id, name, description, destination, price_amount, price_currency, duration_days FROM agency_packages WHERE is_active = TRUE AND destination IN ('Canada') AND destination NOT IN ('Quebec') AND category IN ('adventure','travel') AND id NOT IN (EXCLUDE_PREV_IDS_CLAUSE) AND id NOT IN (SELECT id FROM agency_packages WHERE embedding <=> exclude_embedding < 0.3 AND is_active = TRUE) ORDER BY embedding <=> query_embedding LIMIT 3"
}}

Example 2:
INPUT:
{{
  "main_query": "Fun trips for a small group",
  "constraints": {{}},
  "vague_exclusion": "",
  "exclude_previous_ids": false
}}

OUTPUT:
{{
  "sql": "SELECT id, name, description, destination, price_amount, price_currency, duration_days FROM agency_packages WHERE is_active = TRUE ORDER BY embedding <=> query_embedding LIMIT 5"
}}

Example 3 (array constraints):
INPUT:
{{
  "main_query": "Winter getaway for solo traveler",
  "constraints": {{
    "ideal_for": {{ "include": ["solo", "family"] }},
    "includes": {{ "include": ["accommodation", "transport"] }}
  }},
  "vague_exclusion": "snowy mountains",
  "exclude_previous_ids": true,
  "limit": 5
}}

OUTPUT:
{{
  "sql": "SELECT id, name, description, destination, price_amount, price_currency, duration_days FROM agency_packages WHERE is_active = TRUE AND ideal_for && ARRAY['solo','family'] AND includes && ARRAY['accommodation','transport'] AND id NOT IN (EXCLUDE_PREV_IDS_CLAUSE) AND id NOT IN (SELECT id FROM agency_packages WHERE embedding <=> exclude_embedding < 0.3 AND is_active = TRUE) ORDER BY embedding <=> query_embedding LIMIT 5"
}}

────────────────────────────────────
NOW GENERATE SQL
────────────────────────────────────

- Only for the provided input
- Always include EXCLUDE_PREV_IDS_CLAUSE if exclude_previous_ids=true
- Return **only the JSON object**
- Do not break the examples' pattern
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


import logging, json

logger = logging.getLogger("AI_TRAVEL")
logging.basicConfig(level=logging.INFO)


async def smart_package_search(
    user_request: dict,
    session_id: str,
    agency_prospect_id: str,
) -> list[dict]:
    results = []
    exclude_previous_ids = user_request.get("exclude_previous_ids", False)
    try:
        logger.info(
            "🧠 Starting smart package search | Prospect=%s | Exclude previous: %s",
            agency_prospect_id,
            exclude_previous_ids,
        )

        # 0️⃣ Get schema and generate SQL
        try:
            schema = await get_packages_schema()
            sql = await generate_sql_from_request(user_request, schema)
            logger.info("💡 AI generated SQL (with placeholders):\n%s", sql)
        except Exception as e:
            logger.error("❌ Failed to generate SQL: %s", e)
            return results

        # 1️⃣ Generate vector embedding for main query
        try:
            if "<=>" in sql:
                logger.info(
                    "🔗 Generating embedding for main query:\n%s",
                    user_request.get("main_query"),
                )
                request_embedding = await generate_embeddings(
                    user_request.get("main_query", "")
                )
                sql = sql.replace("query_embedding", f"'{request_embedding}'")
                logger.info("✅ Main query embedding applied to SQL.")
        except Exception as e:
            logger.warning("⚠️ Failed to generate main query embedding: %s", e)

        # 2️⃣ Vector embedding for vague exclusions
        try:
            if user_request.get("vague_exclusion") and "exclude_embedding" in sql:
                logger.info(
                    "🔗 Generating embedding for vague exclusion:\n%s",
                    user_request["vague_exclusion"],
                )
                exclude_embedding = await generate_embeddings(
                    user_request["vague_exclusion"]
                )
                sql = sql.replace("exclude_embedding", f"'{exclude_embedding}'")
                logger.info("✅ Vague exclusion embedding applied to SQL.")
        except Exception as e:
            logger.warning("⚠️ Failed to generate exclusion embedding: %s", e)

        # 3️⃣ Handle exclude_previous_ids
        try:
            prev_ids_label = "EXCLUDE_PREV_IDS_CLAUSE"
            cache_key = f"prev_package_ids:{session_id}:{agency_prospect_id}"
            prev_ids = []

            if exclude_previous_ids:
                logger.info("📦 Checking cached previous package IDs...")
                cached = safe_redis_operation(recommendation_broker.get, cache_key)
                if cached:
                    try:
                        prev_ids = json.loads(cached)
                        logger.info("✅ Cached previous IDs found: %s", prev_ids)
                    except json.JSONDecodeError:
                        prev_ids = []
                        logger.warning("⚠️ Failed to decode cached previous IDs.")
                if prev_ids:
                    sql = sql.replace(
                        prev_ids_label, ",".join(f"'{i}'" for i in prev_ids)
                    )
                    logger.info("✅ Previous IDs applied to SQL.")
                else:
                    sql = sql.replace(f"AND id NOT IN ({prev_ids_label})", "")
                    logger.info(
                        "ℹ️ No previous IDs found; clause removed for cleaner SQL."
                    )
            else:
                logger.info("🗑️ Resetting previous IDs cache for new request.")
                safe_redis_operation(recommendation_broker.delete, cache_key)
        except Exception as e:
            logger.warning("⚠️ Failed handling previous IDs: %s", e)

        # 4️⃣ Run the SQL
        try:
            logger.info("🚀 Executing SQL query...")
            results = await run_sql_query(sql)
            logger.info("✅ SQL query executed. Number of results: %d", len(results))
        except Exception as e:
            logger.error("❌ SQL execution failed: %s", e)

        # 5️⃣ Cache new IDs
        try:
            new_ids = [r["id"] for r in results]
            if new_ids:
                safe_redis_operation(
                    recommendation_broker.set,
                    cache_key,
                    json.dumps(new_ids),
                    ex=24 * 3600,
                )
                logger.info("💾 Cached new package IDs for future use: %s", new_ids)
        except Exception as e:
            logger.warning("⚠️ Failed caching new package IDs: %s", e)

        logger.info("🧠 Smart package search completed for prospect %s", agency_prospect_id)

    except Exception as e:
        logger.critical("❌ Unexpected error in smart_package_search: %s", e)

    return results


async def db_scanning(
    user_note: str, session_id: str, agency_prospect_id: str, exclude_previous_ids: bool
):
    response = {"results": [], "message": "⚠️ Scan failed."}
    try:
        logger.info("🔍 Starting DB scan | Prospect=%s", agency_prospect_id)
        logger.info("📝 User note:\n%s", user_note)

        # Formulate structured request
        try:
            search_request = formulate_request(user_note)
            search_request["exclude_previous_ids"] = exclude_previous_ids
            logger.info("💡 Formulated structured search request:\n%s", search_request)
        except Exception as e:
            logger.warning("⚠️ Failed to formulate request: %s", e)
            search_request = {}

        # Fetch raw results
        try:
            raw_search_results = await smart_package_search(
                user_request=search_request,
                session_id=session_id,
                agency_prospect_id=agency_prospect_id,
            )
        except Exception as e:
            logger.error("❌ Smart package search failed: %s", e)
            raw_search_results = []

        # Format results
        try:
            formatted_packages = format_packages(raw_search_results)
            package_text_blocks = [entry["details"] for entry in formatted_packages]
            logger.info(
                "📄 Formatted package results:\n%s", "\n\n".join(package_text_blocks)
            )
        except Exception as e:
            logger.warning("⚠️ Failed formatting packages: %s", e)
            formatted_packages = []

        # Insert recommendation events/items
        try:
            request_embedding = await generate_embeddings(user_note)
            recommendation_event_id = await insert_recommendation_event(
                session_id, request_embedding, user_note
            )

            if recommendation_event_id and formatted_packages:
                logger.info("💾 Inserting recommendation items into memory...")
                for idx, package in enumerate(formatted_packages):
                    try:
                        memory_embedding = await generate_embeddings(
                            f"User Request: {user_note}\nPackage Details: {package}"
                        )
                        await insert_recommendation_item(
                            session_id,
                            recommendation_event_id,
                            package["package_id"],
                            raw_search_results[idx],
                            memory_embedding,
                        )
                    except Exception as e:
                        logger.warning(
                            "⚠️ Failed inserting package %s: %s",
                            package.get("package_id"),
                            e,
                        )

                logger.info("✅ Recommendation items saved successfully.")
        except Exception as e:
            logger.warning("⚠️ Failed saving recommendation event/items: %s", e)

        # Summary message
        try:
            num_packages = len(raw_search_results)
            if num_packages == 0:
                response["message"] = "⚠️ No packages found matching the criteria."
                logger.warning(response["message"])
            elif num_packages == 1:
                response["message"] = "✅ 1 package found matching the criteria."
                logger.info(response["message"])
            else:
                response["message"] = (
                    f"✅ {num_packages} packages found matching the criteria."
                )
                logger.info(response["message"])

            response["results"] = formatted_packages
        except Exception as e:
            logger.warning("⚠️ Failed generating summary message: %s", e)

        logger.info("🔍 DB scan completed for prospect %s", agency_prospect_id)

    except Exception as e:
        logger.critical("❌ Unexpected error in db_scanning: %s", e)

    return response
