import logging

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("SERVICES_CRUD")


async def fetch_table_schema(
    schema_name: str,
    table_name: str,
    include_sample: bool = False,
) -> dict:
    """
    Fetches table schema metadata (and optional sample row) via Supabase RPC.

    Args:
        schema_name (str): Postgres schema name (e.g. 'public')
        table_name (str): Table name
        include_sample (bool, optional): Whether to fetch a fully-populated sample row. Defaults to False.

    Returns:
        dict: JSON schema description with columns and optional sample_row
    """
    from core import db_rpc  # Avoid circular import
    
    if not schema_name or not table_name:
        logger.error(
            "❌ Cannot fetch table schema: missing schema_name or table_name | schema=%s | table=%s",
            schema_name,
            table_name,
        )
        return {}

    try:
        rpc_params = {
            "p_schema_name": schema_name,
            "p_table_name": table_name,
            "is_sample_requested": include_sample,
        }

        logger.info(
            "🔍 Fetching table schema | schema=%s | table=%s | sample=%s",
            schema_name,
            table_name,
            include_sample,
        )

        result = await db_rpc("get_table_schema", rpc_params)

        if not result:
            logger.warning(
                "⚠️ Empty schema response | schema=%s | table=%s",
                schema_name,
                table_name,
            )
            return {}

        logger.info(
            "✅ Table schema fetched | schema=%s | table=%s | columns=%s",
            schema_name,
            table_name,
            len(result.get("columns", [])),
        )

        return result

    except Exception as e:
        logger.error(
            "💥 Failed to fetch table schema | schema=%s | table=%s | error=%s",
            schema_name,
            table_name,
            e,
            exc_info=True,
        )
        return {}