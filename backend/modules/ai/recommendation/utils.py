import logging
from core import db_insert
logger = logging.getLogger("RECOMMENDATION_UTILS")

def format_packages(results):
    formatted_results = []

    for item in results:
        package_id = item.get('id', 'N/A')
        # Compose a natural-sounding descriptive string
        details = (
            f"{item.get('name', 'Unnamed Package')} is a package to "
            f"{item.get('destination', 'an unknown destination')}, "
            f"lasting {item.get('duration_days', 'N/A')} days, "
            f"priced at {item.get('price_amount', 'N/A')} {item.get('price_currency', '')}. "
            f"Description: {item.get('description', 'No description provided.')}"
        )

        formatted_results.append({
            "package_id": package_id,
            "details": details
        })

    return formatted_results

def format_search_request(search_request):
    constraints = search_request.get("constraints", {})
    
    def format_range(field):
        min_val = field.get("min")
        max_val = field.get("max")
        if min_val and max_val:
            return f"between {min_val} and {max_val}"
        elif min_val:
            return f"starting from {min_val}"
        elif max_val:
            return f"up to {max_val}"
        return None

    # Build pieces
    main_query = search_request.get("main_query", "Travel experience")

    category_include = constraints.get("category", {}).get("include", [])
    category_exclude = constraints.get("category", {}).get("exclude", [])

    destination_include = constraints.get("destination", {}).get("include", [])
    destination_exclude = constraints.get("destination", {}).get("exclude", [])

    price_range = format_range(constraints.get("price_amount", {}))
    currency = constraints.get("price_currency", {}).get("include", [])
    
    duration_range = format_range(constraints.get("duration_days", {}))
    
    ideal_for = constraints.get("ideal_for", {}).get("include", [])
    includes = constraints.get("includes", {}).get("include", [])
    
    vague_exclusion = search_request.get("vague_exclusion", "")

    # Compose natural description
    description_parts = [f"User requests for {main_query}."]

    if destination_include:
        description_parts.append(
            f"Preferred destinations include {', '.join(destination_include)}."
        )

    if category_include:
        description_parts.append(
            f"The experience should focus on {', '.join(category_include)} travel."
        )

    if ideal_for:
        description_parts.append(
            f"It should be suitable for {', '.join(ideal_for)}."
        )

    if price_range:
        currency_str = f" {currency[0]}" if currency else ""
        description_parts.append(
            f"The budget is {price_range}{currency_str}."
        )

    if duration_range:
        description_parts.append(
            f"The ideal duration is {duration_range} days."
        )

    if includes:
        description_parts.append(
            f"The package should include {', '.join(includes)}."
        )

    if category_exclude or destination_exclude:
        exclusions = category_exclude + destination_exclude
        description_parts.append(
            f"The user wants to avoid {', '.join(exclusions)} options."
        )

    if vague_exclusion:
        description_parts.append(
            f"Additionally, they prefer to avoid {vague_exclusion}."
        )
    
    formatted_description = " ".join(description_parts)

    return formatted_description


async def insert_recommendation_event(session_id, request_embedding, user_request):
    result = await db_insert(
        table="recommendation_events",
        data={
            "session_id": session_id,
            "request_embedding": request_embedding,
            "prospect_request": user_request,
        },
        return_mode="one"
    )
    if not result or not isinstance(result, dict):
        logging.error("❌ Failed to insert recommendation event for session_id=%s", session_id)
        return None
    
    event_id = result.get("id")
    if not event_id:
        logging.error("❌ Recommendation event inserted but no ID returned for session_id=%s", session_id)
        return None
    
    logging.info("✅ Recommendation event created with ID=%s for session_id=%s", event_id, session_id)
    return event_id
    


async def insert_recommendation_item(session_id, recommendation_event_id, package_obj, memory_embedding):
    await db_insert(
        table="recommendation_items",
        data={
            "session_id": session_id,
            "recommendation_event_id": recommendation_event_id,
            "package_obj_snapshot": package_obj,
            "memory_embedding": memory_embedding,
        }
    )
    