import os
from pathlib import Path
from dotenv import load_dotenv
import psycopg
from psycopg import sql
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
# Load environment
# ----------------------------
load_dotenv()
DB_URL = os.getenv("DB_URL")

# ----------------------------
# Schema file path
# ----------------------------
schema_file_path = Path(__file__).parent / "schema.sql"

def apply_schema():
    if not schema_file_path.exists():
        logger.error(f"{schema_file_path} not found")
        raise FileNotFoundError(f"{schema_file_path} not found")
    
    with open(schema_file_path, "r") as f:
        schema_content = f.read()
    
    # Split SQL into individual statements
    statements = [stmt.strip() for stmt in schema_content.split(";") if stmt.strip()]
    
    # Connect to Supabase Postgres
    if not DB_URL:
        logger.error("DB_URL is not set.")
        return
    with psycopg.connect(DB_URL) as conn:
        with conn.cursor() as cur:
            for i, stmt in enumerate(statements, start=1):
                try:
                    cur.execute(stmt) # type: ignore
                    logger.info(f"✅ Executed statement {i}/{len(statements)}")
                except Exception as e:
                    logger.warning(f"⚠️ Failed to execute statement {i}:\n{stmt}\nError: {e}")
        conn.commit()
    
    logger.info("🎉 Schema applied successfully!")

if __name__ == "__main__":
    apply_schema()
