from core import run_sql_query
from ..utils import format_packages

class ReferenceMemory:
    """
    Handles retrieval of past recommendations based on embeddings.
    Returns both structured objects and text blocks.
    """

    @staticmethod
    async def fetch_by_package_memory(session_id, embedding):
        query = """
            SELECT package_obj_snapshot
            FROM recommendation_items
            WHERE session_id = $1
            ORDER BY memory_embedding <=> $2
            LIMIT 10
        """
        return await run_sql_query(query, [session_id, embedding])

    @staticmethod
    async def fetch_by_request_memory(session_id, embedding):
        query = """
            SELECT id
            FROM recommendation_events
            WHERE session_id = $1
            ORDER BY request_embedding <=> $2
            LIMIT 1
        """
        events = await run_sql_query(query, [session_id, embedding])
        if not events:
            return []
        best_event_id = events[0]["id"]

        rec_query = """
            SELECT package_obj_snapshot
            FROM recommendation_items
            WHERE recommendation_event_id = $1
            ORDER BY memory_embedding <=> $2
            LIMIT 10
        """
        return await run_sql_query(rec_query, [best_event_id, embedding])

    @staticmethod
    async def retrieve(session_id, embedding, basis):
        """
        Returns both structured objects and formatted text blocks
        """
        if basis == "request":
            raw_results = await ReferenceMemory.fetch_by_request_memory(session_id, embedding)
        else:
            raw_results = await ReferenceMemory.fetch_by_package_memory(session_id, embedding)

        reference_packages = format_packages(raw_results)

        summary_msg = "No relevant past recommendations found."
        if raw_results:
            summary_msg = f"Based on past interactions, {len(raw_results)} recommendation(s) are relevant."

        return summary_msg, reference_packages

        
