import os
import re
from pathlib import Path
from dotenv import load_dotenv
import psycopg
import logging

# ----------------------------
# Configure logging
# ----------------------------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s"
)
logger = logging.getLogger(__name__)

# ----------------------------
# Load environment (anchored to backend/.env)
# ----------------------------
BACKEND_DIR = Path(__file__).resolve().parents[2]
ENV_PATH = BACKEND_DIR / ".env"
load_dotenv(ENV_PATH)
DB_URL = os.getenv("DB_URL")

# ----------------------------
# Schema file path
# ----------------------------
schema_file_path = Path(__file__).parent / "schema.sql"


def split_sql_statements(sql: str) -> list[str]:
    """
    Split a SQL script into top-level statements, honoring the things that
    can legitimately contain semicolons:
      - single-quoted strings      ('...', with '' escapes)
      - double-quoted identifiers  ("...", with "" escapes)
      - dollar-quoted bodies       ($$...$$ and $tag$...$tag$)
      - line comments              (--)
      - block comments             (/* ... */)

    Semicolons inside any of those are preserved; only top-level semicolons
    terminate a statement. The schema relies on this for its PL/pgSQL
    functions (AS $$ ... $$), its DO $$ ... $$ blocks, and its
    cron.schedule('...', '...', $$ ... $$) calls — a naive split(";") would
    chop those into invalid fragments.
    """
    statements: list[str] = []
    current: list[str] = []
    i = 0
    n = len(sql)

    while i < n:
        c = sql[i]
        nxt = sql[i + 1] if i + 1 < n else ""

        # ---- line comment ----
        if c == "-" and nxt == "-":
            while i < n and sql[i] not in "\r\n":
                current.append(sql[i])
                i += 1
            continue

        # ---- block comment ----
        if c == "/" and nxt == "*":
            current.append(sql[i])
            current.append(sql[i + 1])
            i += 2
            while i < n:
                if sql[i] == "*" and i + 1 < n and sql[i + 1] == "/":
                    current.append(sql[i])
                    current.append(sql[i + 1])
                    i += 2
                    break
                current.append(sql[i])
                i += 1
            continue

        # ---- double-quoted identifier ----
        if c == '"':
            current.append(c)
            i += 1
            while i < n:
                current.append(sql[i])
                if sql[i] == '"':
                    if i + 1 < n and sql[i + 1] == '"':  # escaped "" inside
                        current.append(sql[i + 1])
                        i += 2
                        continue
                    i += 1
                    break
                i += 1
            continue

        # ---- single-quoted string ----
        if c == "'":
            current.append(c)
            i += 1
            while i < n:
                current.append(sql[i])
                if sql[i] == "'":
                    if i + 1 < n and sql[i + 1] == "'":  # escaped '' inside
                        current.append(sql[i + 1])
                        i += 2
                        continue
                    i += 1
                    break
                i += 1
            continue

        # ---- dollar-quoted body ($$ or $tag$) ----
        if c == "$":
            m = re.match(r"\$[A-Za-z0-9_]*\$", sql[i:])
            if m:
                tag = m.group(0)
                current.append(tag)
                i += len(tag)
                end = sql.find(tag, i)
                if end == -1:  # unclosed — take the rest as-is
                    current.append(sql[i:])
                    i = n
                else:
                    current.append(sql[i:end])
                    current.append(tag)
                    i = end + len(tag)
                continue

        # ---- statement separator (top level only) ----
        if c == ";":
            stmt = "".join(current).strip()
            if stmt:
                statements.append(stmt)
            current = []
            i += 1
            continue

        current.append(c)
        i += 1

    # trailing statement without a final semicolon
    stmt = "".join(current).strip()
    if stmt:
        statements.append(stmt)

    return statements


def apply_schema(host: "str | None" = None, port: "str | None" = None) -> None:
    """
    Apply backend/core/db/schema.sql to the database referenced by DB_URL
    (or override host/port for a local Postgres).

    Executes the script as a sequence of top-level statements, logging each
    one and continuing on error (so a single failing statement doesn't abort
    the whole run). Returns after committing; raises if the connection or
    the script file is unusable.
    """
    if not schema_file_path.exists():
        logger.error(f"{schema_file_path} not found")
        raise FileNotFoundError(f"{schema_file_path} not found")

    url = DB_URL
    if url and (host or port):
        # Prefer an explicit local override when both are given.
        logger.warning("Both DB_URL and host/port given — using host/port override.")
        url = None

    if not url and not host:
        logger.error(
            "DB_URL is not set, and no host was provided.\n"
            "Add DB_URL=<supabase connection string> to backend/.env, e.g.\n"
            "  postgresql://postgres.<ref>:<password>@aws-0-<region>.pooler.supabase.com:6543/postgres?sslmode=require\n"
            "or pass host=/port= to use a local Postgres."
        )
        raise EnvironmentError("DB_URL is not set")

    with open(schema_file_path, "r") as f:
        schema_content = f.read()

    statements = split_sql_statements(schema_content)
    logger.info("🎯 Parsed %d top-level SQL statement(s) from schema.sql", len(statements))

    connect_args = {}
    if host:
        connect_args["host"] = host
        connect_args["port"] = int(port or 5432)
    elif "supabase" in url and "sslmode" not in url:
        # Supabase direct/pooler connections require SSL.
        sep = "&" if "?" in url else "?"
        url = f"{url}{sep}sslmode=require"

    ok, failed = 0, 0
    # autocommit runs each statement in its own transaction, so a failure in
    # one statement (e.g. a table that already exists) does not abort the
    # whole run — the remaining statements still get a chance to apply.
    with psycopg.connect(url or "", autocommit=True, **connect_args) as conn:
        with conn.cursor() as cur:
            for i, stmt in enumerate(statements, start=1):
                try:
                    cur.execute(stmt)
                    # Discard any pending result rows (e.g. select cron.schedule(...))
                    if cur.description is not None:
                        cur.fetchall()
                    ok += 1
                    logger.info(f"✅ Executed statement {i}/{len(statements)}")
                except Exception as e:
                    failed += 1
                    logger.warning(
                        f"⚠️ Failed to execute statement {i}/{len(statements)}:\n"
                        f"{stmt[:300]}\nError: {e}"
                    )

    logger.info("🎉 Schema apply finished (%d ok, %d failed)", ok, failed)


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Apply backend schema.sql to Postgres")
    parser.add_argument("--host", help="Override DB host (e.g. localhost)")
    parser.add_argument("--port", help="Override DB port (default 5432)")
    args = parser.parse_args()

    apply_schema(host=args.host, port=args.port)