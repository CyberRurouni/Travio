import logging
from core import db_insert

logger = logging.getLogger("RECOMMENDATION_UTILS")


def format_packages(results: list) -> list[dict]:
    """
    Convert raw package query results into a list of formatted
    dictionaries containing `package_id` and a human-readable
    `details` summary.

    Returns an empty list if input is invalid (other than list).
    """

    if not isinstance(results, list):
        return []

    formatted_results = []

    for item in results:
        if not isinstance(item, dict):
            continue

        package_id = item.get("id", "N/A")
        details = (
            f"{item.get('name', 'Unnamed Package')} is a package to "
            f"{item.get('destination', 'an unknown destination')}, "
            f"lasting {item.get('duration_days', 'N/A')} days, "
            f"priced at {item.get('price_amount', 'N/A')} {item.get('price_currency', '')}. "
            f"Description: {item.get('description', 'No description provided.')}"
        )

        formatted_results.append({"package_id": package_id, "details": details})

    return formatted_results


async def insert_recommendation_event(session_id, request_embedding, user_request):
    result = await db_insert(
        table="recommendation_events",
        data={
            "session_id": session_id,
            "request_embedding": request_embedding,
            "prospect_request": user_request,
        },
        return_mode="one",
    )
    if not result or not isinstance(result, dict):
        logging.error(
            "❌ Failed to insert recommendation event for session_id=%s", session_id
        )
        return None

    event_id = result.get("id")
    if not event_id:
        logging.error(
            "❌ Recommendation event inserted but no ID returned for session_id=%s",
            session_id,
        )
        return None

    logging.info(
        "✅ Recommendation event created with ID=%s for session_id=%s",
        event_id,
        session_id,
    )
    return event_id


async def insert_recommendation_item(
    session_id, recommendation_event_id, package_id, package_obj, memory_embedding
):
    await db_insert(
        table="recommendation_items",
        data={
            "session_id": session_id,
            "recommendation_event_id": recommendation_event_id,
            "package_id": package_id,
            "package_obj_snapshot": package_obj,
            "memory_embedding": memory_embedding,
        },
    )
