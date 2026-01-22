import logging
from typing import Optional, Any, Literal
from postgrest.types import CountMethod, ReturnMethod
from core import supabase

logger = logging.getLogger("DB_MAIN_CRUD")

async def db_select(
    table: str,
    filters: dict | None = None,
    or_filters: list[tuple[str, str, Any]] | None = None,
    exclude_filters: dict | None = None,
    or_excluded_filters: list[tuple[str, str, Any]] | None = None,
    search_filters: dict | None = None,  
    date_range: dict | None = None,
    limit: int | None = None,
    offset: int | None = None,
    order_by: str | None = None,
    desc: bool = False,
    fields: str = "*",
    cursor_field: str | None = None,
    cursor_value: Any | None = None,
    cursor_action: Literal["next", "previous"] | None = "next",
    include_next_cursor: bool = False,
):
    """Generic Supabase selector with support for complex OR, NOT, and search conditions."""
    try:
        query = supabase.table(table).select(fields)

        # ------------------------------
        # AND filters
        # ------------------------------
        if filters:
            for field, value in filters.items():
                if isinstance(value, (list, tuple, set)):
                    query = query.in_(field, list(value))
                else:
                    query = query.eq(field, value)

        # ------------------------------
        # OR filters
        # ------------------------------
        if or_filters:
            or_query = []
            for field, op, value in or_filters:
                if op == "eq":
                    or_query.append(f"{field}.eq.{value}")
                elif op == "in" and isinstance(value, (list, tuple, set)):
                    values_str = ",".join(map(str, value))
                    or_query.append(f"{field}.in.({values_str})")
            if or_query:
                query = query.or_(",".join(or_query))

        # ------------------------------
        # EXCLUDE filters
        # ------------------------------
        if exclude_filters:
            for field, value in exclude_filters.items():
                if isinstance(value, (list, tuple, set)):
                    query = query.not_.in_(field, list(value))
                elif value is None:
                    query = query.not_.is_(field, None)
                else:
                    query = query.neq(field, value)

        # ------------------------------
        # OR-EXCLUDE filters
        # ------------------------------
        if or_excluded_filters:
            or_exclude_query = []
            for field, op, value in or_excluded_filters:
                if op == "eq":
                    or_exclude_query.append(f"{field}.eq.{value}")
                elif op == "in" and isinstance(value, (list, tuple, set)):
                    values_str = ",".join(map(str, value))
                    or_exclude_query.append(f"{field}.in.({values_str})")
            if or_exclude_query:
                query = query.not_.or_(",".join(or_exclude_query))

        # ------------------------------
        # SEARCH filters (ILIKE for partial match)
        # ------------------------------
        if search_filters:
            for field, term in search_filters.items():
                if term:  # skip empty search terms
                    query = query.ilike(field, f"%{term}%")

        # ------------------------------
        # DATE RANGE FILTERS
        # ------------------------------
        if date_range:
            for field, (op, value) in date_range.items():
                if op == "gte":
                    query = query.gte(field, value)
                elif op == "lte":
                    query = query.lte(field, value)

        # ------------------------------
        # Cursor-based pagination
        # ------------------------------
        if cursor_field and cursor_value is not None:
            if cursor_action == "previous":
                query = query.lt(cursor_field, cursor_value)
            else:
                query = query.gt(cursor_field, cursor_value)

        # ------------------------------
        # Sorting
        # ------------------------------
        if order_by:
            query = query.order(order_by, desc=desc)

        # ------------------------------
        # Offset + limit
        # ------------------------------
        if offset is not None:
            end = offset + (limit - 1 if limit else 99)
            query = query.range(offset, end)
        elif limit:
            query = query.limit(limit)

        # ------------------------------
        # Execute
        # ------------------------------
        resp = query.execute()
        data = resp.data or []

        # ------------------------------
        # Include next cursor if requested
        # ------------------------------
        if include_next_cursor and data and cursor_field:
            last_item = data[-1]
            last_cursor_value = last_item.get(cursor_field) if isinstance(last_item, dict) else None
            return {"data": data, "next_cursor": last_cursor_value}

        return data

    except Exception as e:
        logger.error(f"Error selecting from '{table}': {e}", exc_info=True)
        return {"data": [], "next_cursor": None} if include_next_cursor else []

async def db_upsert(
    table: str,
    data: dict | list[dict],
    conflict_fields: list[str],
    return_mode: str = "one",  # "one", "all", or "none"
) -> Any | None:
    """Insert or update record(s) idempotently based on conflict fields."""
    try:
        #  Convert conflict fields list → comma-separated string
        if isinstance(conflict_fields, (list, tuple)):
            conflict_fields_str = ",".join(conflict_fields)
        else:
            conflict_fields_str = str(conflict_fields)

        # Deduplicate batch rows on conflict keys (avoid '21000' error)
        if isinstance(data, list) and len(data) > 1:
            seen = set()
            unique_data = []
            for row in data:
                key_tuple = tuple(row.get(k) for k in conflict_fields)
                if key_tuple not in seen:
                    seen.add(key_tuple)
                    unique_data.append(row)
            data = unique_data

        resp = (
            supabase.table(table)
            .upsert(data, on_conflict=conflict_fields_str)
            .execute()
        )

        if not resp.data:
            return None

        if return_mode == "one":
            return resp.data[0]
        elif return_mode == "all":
            return resp.data
        elif return_mode == "none":
            return None
        else:
            raise ValueError(f"Invalid return_mode: {return_mode}")

    except Exception as e:
        logger.error(f"Error upserting into {table}: {e}", exc_info=True)
        return None


async def db_insert(
    table: str,
    data: dict | list[dict],
    return_mode: Literal["one", "all", "none"] = "all",
) -> Any:
    """
    Insert one or multiple records into a table, safely ignoring conflicts.
    """
    try:
        resp = supabase.table(table).insert(data).execute()

        if not resp.data:
            logger.info(f"No new records inserted (duplicates ignored) in '{table}'")
            return None

        logger.info(f"Inserted {len(resp.data)} record(s) into '{table}'")

        if return_mode == "one":
            return resp.data[0]
        elif return_mode == "all":
            return resp.data
        elif return_mode == "none":
            return None
        else:
            raise ValueError(
                f"Invalid return_mode: {return_mode}. Valid options are: 'one', 'all', 'none'."
            )

    except Exception as e:
        logger.error(f"Error inserting into {table}: {e}", exc_info=True)
        return None


async def db_update(table: str, updates: dict, filters: dict) -> Any:
    """Update a record matching given filters."""
    try:
        query = supabase.table(table).update(updates)

        for field, value in filters.items():
            if isinstance(value, (list, tuple, set)):
                query = query.in_(field, list(value))
            else:
                query = query.eq(field, value)

        resp = query.execute()
        return resp.data if resp.data else None
    except Exception as e:
        logger.error(f"Error updating {table} with {filters}: {e}")
        return None


async def db_count(
    table: str,
    filters: dict | None = None,
    or_filters: list[tuple[str, str, Any]] | None = None,
    exclude_filters: dict | None = None,
    or_excluded_filters: list[tuple[str, str, Any]] | None = None,
    date_range: dict | None = None,
    limit: int | None = None,
    offset: int | None = None,
    order_by: str | None = None,
    desc: bool = False,
    fields: str = "*",
    cursor_field: str | None = None,
    cursor_value: Any | None = None,
    cursor_action: Literal["next", "previous"] | None = "next",
) -> int:
    """
    Count rows in a table with full feature parity (filters, OR, NOT, date ranges,
    cursor pagination, sorting, limit/offset).
    """
    try:
        query = supabase.table(table).select(fields, count=CountMethod.exact)

        # ------------------------------
        # AND filters
        # ------------------------------
        if filters:
            for field, value in filters.items():
                if isinstance(value, (list, tuple, set)):
                    query = query.in_(field, list(value))
                else:
                    query = query.eq(field, value)

        # ------------------------------
        # OR filters
        # ------------------------------
        if or_filters:
            or_query = []
            for field, op, value in or_filters:
                if op == "eq":
                    or_query.append(f"{field}.eq.{value}")
                elif op == "in" and isinstance(value, (list, tuple, set)):
                    values_str = ",".join(map(str, value))
                    or_query.append(f"{field}.in.({values_str})")
            if or_query:
                query = query.or_(",".join(or_query))

        # ------------------------------
        # EXCLUDE filters
        # ------------------------------
        if exclude_filters:
            for field, value in exclude_filters.items():
                if isinstance(value, (list, tuple, set)):
                    query = query.not_.in_(field, list(value))
                elif value is None:
                    query = query.not_.is_(field, None)
                else:
                    query = query.neq(field, value)

        # ------------------------------
        # OR-EXCLUDE filters
        # ------------------------------
        if or_excluded_filters:
            or_exclude_query = []
            for field, op, value in or_excluded_filters:
                if op == "eq":
                    or_exclude_query.append(f"{field}.eq.{value}")
                elif op == "in" and isinstance(value, (list, tuple, set)):
                    values_str = ",".join(map(str, value))
                    or_exclude_query.append(f"{field}.in.({values_str})")
            if or_exclude_query:
                query = query.not_.or_(",".join(or_exclude_query))

        # ------------------------------
        # DATE RANGE FILTERS
        # ------------------------------
        if date_range:
            for field, (op, value) in date_range.items():
                if op == "gte":
                    query = query.gte(field, value)
                elif op == "lte":
                    query = query.lte(field, value)

        # ------------------------------
        # Cursor-based pagination
        # ------------------------------
        if cursor_field and cursor_value is not None:
            if cursor_action == "previous":
                query = query.lt(cursor_field, cursor_value)
            else:
                query = query.gt(cursor_field, cursor_value)

        # ------------------------------
        # Sorting
        # ------------------------------
        if order_by:
            query = query.order(order_by, desc=desc)

        # ------------------------------
        # Offset + limit
        # ------------------------------
        if offset is not None:
            end = offset + (limit - 1 if limit else 99)
            query = query.range(offset, end)
        elif limit:
            query = query.limit(limit)

        # ------------------------------
        # Execute
        # ------------------------------
        resp = query.execute()
        return resp.count or 0

    except Exception as e:
        logger.error(f"Error counting rows in '{table}': {e}", exc_info=True)
        return 0


async def db_delete(
    table: str,
    filters: dict | None = None,
    or_filters: list[tuple[str, str, Any]] | None = None,
    exclude_filters: dict | None = None,
    or_excluded_filters: list[tuple[str, str, Any]] | None = None,
    limit: int | None = None,
    returning: Literal["none", "minimal", "representation"] = "none",
) -> list | bool | None:
    """
    Flexible DELETE helper similar to db_select, supporting AND, OR, and NOT filters.
    `returning` can be: "none", "minimal", "representation"
    """
    try:
        return_method = ReturnMethod(returning)
        query = supabase.table(table).delete(returning=return_method)

        # AND filters
        if filters:
            for field, value in filters.items():
                if isinstance(value, (list, tuple, set)):
                    query = query.in_(field, list(value))
                else:
                    query = query.eq(field, value)

        # OR filters
        if or_filters:
            or_query = []
            for field, op, value in or_filters:
                if op == "eq":
                    or_query.append(f"{field}.eq.{value}")
                elif op == "in" and isinstance(value, (list, tuple)):
                    values_str = ",".join(map(str, value))
                    or_query.append(f"{field}.in.({values_str})")
            if or_query:
                query = query.or_(",".join(or_query))

        # EXCLUDE filters
        if exclude_filters:
            for field, value in exclude_filters.items():
                if isinstance(value, (list, tuple, set)):
                    query = query.not_.in_(field, list(value))
                elif value is None:
                    query = query.not_.is_(field, None)
                else:
                    query = query.neq(field, value)

        # OR EXCLUDE filters
        if or_excluded_filters:
            or_exclude_query = []
            for field, op, value in or_excluded_filters:
                if op == "eq":
                    or_exclude_query.append(f"{field}.eq.{value}")
                elif op == "in" and isinstance(value, (list, tuple)):
                    or_exclude_query.append(f"{field}.in.({','.join(map(str, value))})")
            if or_exclude_query:
                query = query.not_.or_(",".join(or_exclude_query))

        resp = query.execute()

        return resp.data if returning != "none" else True

    except Exception as e:
        logger.error(f"Error deleting rows from '{table}': {e}", exc_info=True)
        return None


async def db_rpc(
    function_name: str,
    params: dict | None = None,
):
    """
    Generic RPC executor for Supabase.

    Args:
        function_name: Name of the RPC function.
        params: Dictionary of arguments to pass to the Postgres function.

    Returns:
        dict or list: Raw RPC response from Supabase
    """
    try:
        rpc_payload = params or {}

        logger.debug(f"🔧 RPC call → {function_name} | payload={rpc_payload}")

        resp = supabase.rpc(function_name, rpc_payload).execute()

        # Supabase client returns SingleAPIResponse or ListAPIResponse
        data = getattr(resp, "data", {}) or {}

        logger.debug(f"✅ RPC {function_name} success → {data}")
        return data

    except Exception as e:
        logger.exception(f"💥 RPC {function_name} failed: {e}")
        return {}