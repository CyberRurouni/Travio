from core import run_sql_query
from ..utils import format_packages
import logging, json

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("AI_TRAVEL")

class ReferenceMemory:
    """
    Handles retrieval of past recommendations based on embeddings.
    Returns structured package objects and a summary message.
    """

    @staticmethod
    async def fetch_by_package_memory(session_id, embedding):
        logger.info(f"🛠️ Fetching package memory for session_id={session_id} 🧠")
        query = f"""
            SELECT package_obj_snapshot
            FROM recommendation_items
            WHERE session_id = '{session_id}'
            ORDER BY memory_embedding <=> '{embedding}'
            LIMIT 10
        """
        results = await run_sql_query(query)
        logger.info(f"✅ Retrieved {len(results)} package(s) from package memory 📦")
        return results

    @staticmethod
    async def fetch_by_request_memory(session_id, embedding):
        logger.info(f"🔍 Fetching request memory for session_id={session_id} 🧠")
        query = f"""
            SELECT id
            FROM recommendation_events
            WHERE session_id = '{session_id}'
            ORDER BY request_embedding <=> '{embedding}'
            LIMIT 1
        """
        events = await run_sql_query(query)
        logger.info(f"📊 Found {len(events)} matching event(s) for request memory")

        if not events:
            logger.warning("⚠️ No past events found for request memory 😕")
            return []

        best_event_id = events[0]["id"]
        logger.info(f"🏆 Best matching event_id={best_event_id}")

        rec_query = f"""
            SELECT package_obj_snapshot
            FROM recommendation_items
            WHERE recommendation_event_id = '{best_event_id}'
            ORDER BY memory_embedding <=> '{embedding}'
            LIMIT 10
        """
        results = await run_sql_query(rec_query)
        logger.info(f"✅ Retrieved {len(results)} package(s) from request memory 📦")
        return results

    @staticmethod
    async def retrieve(session_id, embedding, basis):
        logger.info(f"🚀 Retrieving recommendations based on '{basis}' memory")
        if basis == "request":
            raw_results = await ReferenceMemory.fetch_by_request_memory(session_id, embedding)
        else:
            raw_results = await ReferenceMemory.fetch_by_package_memory(session_id, embedding)
        
        parsed_packages = [json.loads(row["package_obj_snapshot"]) for row in raw_results]
        reference_packages = format_packages(parsed_packages)
        if raw_results:
            summary_msg = f"Based on past interactions, {len(raw_results)} recommendation(s) are relevant."
            logger.info(f"✨ Summary: {summary_msg}")
        else:
            summary_msg = "No relevant past recommendations found."
            logger.info(f"❌ Summary: {summary_msg}")

        return reference_packages, summary_msg


        
