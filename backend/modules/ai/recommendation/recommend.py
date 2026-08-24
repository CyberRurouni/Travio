import logging, json, re
from .components.request import formulate_request
from .utils import (
    format_packages,
    insert_recommendation_event,
    insert_packages_into_event,
    get_existing_recommendation_event,
)
from core import (
    call_openai,
    fetch_table_schema,
    run_sql_query,
    generate_embeddings,
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

    resp = call_openai.blocking(
        messages=[{"role": "user", "content": prompt}],
        max_tokens=800,
        increment=200,
        fallback={"sql": ""},
    )

    sql = resp.get("sql", "").strip()
    if not sql.lower().startswith("select"):
        raise ValueError(f"Invalid SQL returned: {sql}")

    return sql


# =========================================================
# 🔒 SQL VALIDATOR
# =========================================================
# The LLM generates the SQL string. Before it is ever executed via
# query_sql (SECURITY INVOKER) we validate it so only a single, read-only
# SELECT against the single allowed table is ever run. This is the
# logic-level wall; the read-only role/privileges are the privilege wall.
# A validation failure aborts the scan and surfaces an internal-error
# status to the assistant (the model usually generated something hostile
# or malformed — the assistant handles the messaging).
# =========================================================

# Only these tables may appear in the SQL. Documents that legitimately get
# queried by the recommendation flow are whitelisted.
ALLOWED_SQL_TABLES = {"agency_packages"}

# Keywords that are never valid in a read-only package lookup. Match on
# whole words to avoid false positives like "update" inside a string.
_DANGEROUS_KEYWORDS = [
    "insert",
    "update",
    "delete",
    "drop",
    "alter",
    "truncate",
    "create",
    "grant",
    "revoke",
    "comment",
    "copy",
    "vacuum",
    "reindex",
    "cluster",
    "declare",
    "execute",
    "call",
    "prepare",
    "select into",
    "pg_sleep",
    "lo_",
    "oid",
    "pg_",
    "information_schema",
    "pg_catalog",
    "union",
    "except",
    "intersect",
]

_DANGEROUS_KEYWORD_RE = re.compile(
    r"(?i)\b(" + "|".join(re.escape(k) for k in _DANGEROUS_KEYWORDS) + r")\b"
)


def _validate_table_references(sql: str) -> bool:
    """Ensure every FROM/JOIN target is in the allowed table set."""

    # Find FROM/JOIN ... <table> occurrences (rough but effective at the
    # statement level for a read-only guard).
    for m in re.finditer(r"\b(?:from|join)\s+([\w.]+)\b", sql, re.IGNORECASE):
        table = m.group(1).lower().split(".")[-1]
        if table not in ALLOWED_SQL_TABLES:
            return False
    return True


def validate_generated_sql(sql: str) -> tuple[bool, str]:
    """
    Validate a generated SQL string before execution.

    Returns (is_safe, reason). When unsafe, `reason` explains why the SQL
    was rejected so the assistant can be told what happened.
    """
    if not sql or not isinstance(sql, str):
        return False, "empty_sql"

    trimmed = sql.strip()
    lower = trimmed.lower()

    # 1. Must be a single SELECT statement.
    if not lower.startswith("select"):
        return False, "not_a_select"

    # 2. No statement separators — must be exactly one statement.
    if ";" in trimmed:
        return False, "multiple_statements"

    # 3. No comment markers (LLM could try to smuggle a quote or comment).
    if "--" in trimmed or "/*" in trimmed or "*/" in trimmed:
        return False, "contains_comment"

    # 4. No dangerous keywords / functions / system catalogs.
    if _DANGEROUS_KEYWORD_RE.search(trimmed):
        return False, "dangerous_keyword"

    # 5. Only allowed tables referenced.
    if not _validate_table_references(trimmed):
        return False, "disallowed_table"

    return True, ""


import logging, json

logger = logging.getLogger("AI_TRAVEL")
logging.basicConfig(level=logging.INFO)


async def smart_package_search(
    user_request: dict,
    session_id: str,
    agency_prospect_id: str,
) -> dict:
    """
    Search agency packages for a prospect, with safety guards.

    Returns a status dict:
      {"status": "ok"|"no_results"|"error", "results": [...], "reason": "..."}
    - "ok"         → results is a non-empty, safe result set.
    - "no_results" → the query ran safely but matched nothing.
    - "error"      → an internal/reliability or safety failure (bad embedding,
                     rejected SQL, execution failure). "reason" explains it and
                     is meant to reach the assistant so it can tell the
                     prospect to retry later (rather than falsely claiming no
                     packages exist).
    """
    default = {"status": "error", "results": [], "reason": "unexpected_error"}
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
            return {"status": "error", "results": [], "reason": "sql_generation_failed"}

        # 🔒 Validate the generated SQL before any execution.
        is_safe, reason = validate_generated_sql(sql)
        if not is_safe:
            logger.warning(
                f"🚫 Generated SQL rejected by validator | reason={reason} | sql={sql}"
            )
            return {"status": "error", "results": [], "reason": f"sql_rejected:{reason}"}

        # 1️⃣ Generate vector embedding for main query (required)
        request_embedding = None
        if "<=>" in sql:
            logger.info(
                "🔗 Generating embedding for main query:\n%s",
                user_request.get("main_query"),
            )
            request_embedding = await generate_embeddings(
                user_request.get("main_query", "")
            )
            if not request_embedding:
                logger.error("❌ Main query embedding missing after retries — aborting.")
                return {
                    "status": "error",
                    "results": [],
                    "reason": "embedding_generation_failed",
                }
            sql = sql.replace("query_embedding", f"'{request_embedding}'")
            logger.info("✅ Main query embedding applied to SQL.")

        # 2️⃣ Vector embedding for vague exclusions (required if present in SQL)
        if user_request.get("vague_exclusion") and "exclude_embedding" in sql:
            logger.info(
                "🔗 Generating embedding for vague exclusion:\n%s",
                user_request["vague_exclusion"],
            )
            exclude_embedding = await generate_embeddings(
                user_request["vague_exclusion"]
            )
            if not exclude_embedding:
                logger.error(
                    "❌ Vague-exclusion embedding missing after retries — aborting."
                )
                return {
                    "status": "error",
                    "results": [],
                    "reason": "embedding_generation_failed",
                }
            sql = sql.replace("exclude_embedding", f"'{exclude_embedding}'")
            logger.info("✅ Vague exclusion embedding applied to SQL.")

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
            return {"status": "error", "results": [], "reason": "sql_execution_failed"}

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

        logger.info(
            "🧠 Smart package search completed for prospect %s", agency_prospect_id
        )

        if not results:
            return {"status": "no_results", "results": [], "reason": ""}
        return {"status": "ok", "results": results, "reason": ""}

    except Exception as e:
        logger.critical("❌ Unexpected error in smart_package_search: %s", e)
        return default


async def db_scanning(
    user_note: str, session_id: str, agency_prospect_id: str, exclude_previous_ids: bool
):
    """
    Main function to scan database packages based on a user note, format results,
    and optionally insert recommendation events and items into memory.

    Returns a status dict:
      {"status": "ok"|"no_results"|"error", "message": ..., "results": [...],
       "error_reason": "..."}
    The status is threaded to the assistant so an internal/reliability failure
    ("error") is NOT falsely reported as "no packages found" — the assistant is
    told to ask the prospect to retry later instead.

    Features:
        - Avoids inserting duplicate recommendation events if exclude_previous_ids=True.
        - Logs every major step and warning/error.
    """
    response = {
        "status": "error",
        "results": [],
        "message": "⚠️ Scan failed.",
        "error_reason": "unexpected_error",
    }

    try:
        logger.info("🔍 Starting DB scan | Prospect=%s", agency_prospect_id)
        logger.info("📝 User note:\n%s", user_note)

        # ------------------------------
        # Step 1: Formulate structured search request
        # ------------------------------
        try:
            search_request = formulate_request(user_note)
            search_request["exclude_previous_ids"] = exclude_previous_ids
            logger.info("💡 Formulated structured search request:\n%s", search_request)
        except Exception as e:
            logger.warning("⚠️ Failed to formulate request: %s", e)
            search_request = {}

        # ------------------------------
        # Step 2: Fetch raw search results (with safety/reliability guards)
        # ------------------------------
        try:
            search_result = await smart_package_search(
                user_request=search_request,
                session_id=session_id,
                agency_prospect_id=agency_prospect_id,
            )
        except Exception as e:
            logger.error("❌ Smart package search failed: %s", e)
            search_result = {
                "status": "error",
                "results": [],
                "reason": "search_crashed",
            }

        # ── Abort on internal/safety failure, distinct from "no results" ──
        if search_result.get("status") == "error":
            reason = search_result.get("reason", "unknown")
            logger.error("🚫 DB scan aborted | reason=%s", reason)
            return {
                "status": "error",
                "results": [],
                "message": "⚠️ We hit an internal error while searching. Please try again later.",
                "error_reason": reason,
            }

        raw_search_results = search_result.get("results", [])
        no_results = search_result.get("status") == "no_results"

        # ------------------------------
        # Step 3: Format results for display and memory insertion
        # ------------------------------
        try:
            formatted_packages = format_packages(raw_search_results)
            package_text_blocks = [entry["details"] for entry in formatted_packages]
            logger.info(
                "📄 Formatted package results:\n%s", "\n\n".join(package_text_blocks)
            )
        except Exception as e:
            logger.warning("⚠️ Failed formatting packages: %s", e)
            formatted_packages = []

        # ------------------------------
        # Step 4: Insert recommendation events/items
        # ------------------------------
        try:
            # Check for existing recommendation event to avoid duplicates
            last_event_id = None
            if exclude_previous_ids:
                last_event_id = await get_existing_recommendation_event(session_id, user_note)

            # If a matching previous event exists, only insert items
            if last_event_id and formatted_packages:
                logger.info(
                    "💾 Using existing recommendation event ID=%s for new packages", last_event_id
                )
                await insert_packages_into_event(
                    session_id, last_event_id, user_note, formatted_packages, raw_search_results
                )

            # Otherwise, create a new recommendation event
            else:
                request_embedding = await generate_embeddings(user_note)
                new_event_id = await insert_recommendation_event(session_id, request_embedding, user_note)

                if new_event_id and formatted_packages:
                    logger.info(
                        "💾 Creating new recommendation event ID=%s and inserting packages", new_event_id
                    )
                    await insert_packages_into_event(
                        session_id, new_event_id, user_note, formatted_packages, raw_search_results
                    )

        except Exception as e:
            logger.warning("⚠️ Failed saving recommendation event/items: %s", e)

        # ------------------------------
        # Step 5: Generate summary message
        # ------------------------------
        try:
            num_packages = len(raw_search_results)
            if no_results or num_packages == 0:
                response["status"] = "no_results"
                response["message"] = "⚠️ No packages found matching the criteria."
                logger.warning(response["message"])
            elif num_packages == 1:
                response["status"] = "ok"
                response["message"] = "✅ 1 package found matching the criteria."
                logger.info(response["message"])
            else:
                response["status"] = "ok"
                response["message"] = f"✅ {num_packages} packages found matching the criteria."
                logger.info(response["message"])

            response["results"] = formatted_packages
        except Exception as e:
            logger.warning("⚠️ Failed generating summary message: %s", e)

        logger.info("🔍 DB scan completed for prospect %s", agency_prospect_id)

    except Exception as e:
        logger.critical("❌ Unexpected error in db_scanning: %s", e)

    return response

