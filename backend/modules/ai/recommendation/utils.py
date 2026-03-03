import logging
from core import db_insert, db_select

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


async def insert_packages_into_event(
    session_id,
    recommendation_event_id,
    user_note,
    formatted_packages,
    raw_search_results,
):
    """
    Inserts recommendation items (packages) into a given recommendation event.
    Generates embeddings for memory storage.
    """
    from core import generate_embeddings

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


async def get_existing_recommendation_event(session_id, user_note):
    logger.info(f"🔍 Fetching last recommendation event for session_id={session_id}")

    result = await db_select(
        table="recommendation_events",
        fields="id, prospect_request",
        filters={"session_id": session_id, "prospect_request": user_note},
        limit=1,
    )
    if not result:
        logger.warning(f"⚠️ No recommendation events found for session_id={session_id}")
        return None

    last_event_id = result[0]["id"]

    logger.info(
        f"📊 Last recommendation event ID={last_event_id} for session_id={session_id}"
    )
    return last_event_id
