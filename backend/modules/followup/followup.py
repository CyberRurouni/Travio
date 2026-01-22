import asyncio
import logging
from datetime import datetime, timezone

logger = logging.getLogger("FOLLOWUP_HELPER")


async def followup_handler(payload):
    """
    Called when prospect_presence row is updated.
    Determines if follow-up is needed.
    """
    prospect = payload.get("new")
    if not prospect:
        return

    prospect_id = prospect["prospect_id"]
    channel = prospect["channel_type"]
    last_contacted = prospect["last_contacted_at"]

    # Convert to datetime if needed
    if isinstance(last_contacted, str):
        last_contacted = datetime.fromisoformat(last_contacted).replace(tzinfo=timezone.utc)
    
    logger.info(f"⚡ Prospect {prospect_id} created on {channel}")

    # Check if follow-up threshold exceeded (e.g., 3 days)
    delta_days = (datetime.now(timezone.utc) - last_contacted).days
    if delta_days >= 3:
        logger.info(f"⚡ Prospect {prospect_id} requires follow-up on {channel}")
        await schedule_followup(prospect_id, channel)


async def schedule_followup(prospect_id: str, channel: str):
    """
    Placeholder function for follow-up actions.
    """
    logger.info(f"📬 Scheduling follow-up for Prospect {prospect_id} via {channel}")


realtime_followup_initialized = False

async def realtime_followup_handler():
    """
    Sets up realtime subscription and keeps it alive.
    This function runs forever.
    """
    global realtime_followup_initialized
    if realtime_followup_initialized:
        logger.warning("⚠️ Realtime handler already initialized")
        return

    from core import get_async_supabase

    async_supabase = await get_async_supabase()
    if async_supabase is None:
        logger.error("❌ async_supabase not initialized")
        return

    channel = async_supabase.channel("prospect_presence_updates")
    
    # Define the callback properly
    def handle_change(payload):
        """Sync callback that schedules async handler"""
        logger.info(f"🔔 Realtime event received: {payload}")
        asyncio.create_task(followup_handler(payload))
    
    # ✅ Chain both listeners, then subscribe once
    channel.on_postgres_changes(
        event="INSERT", # type: ignore
        schema="public",
        table="prospect_presence",
        callback=handle_change
    ).on_postgres_changes(
        event="UPDATE", # type: ignore
        schema="public",
        table="prospect_presence",
        callback=handle_change
    )
    
    await channel.subscribe()
    realtime_followup_initialized = True
    logger.info("✅ Realtime Follow Up Propelled - keeping subscription alive...")
    
    try:
        await asyncio.Event().wait()  # Efficient way to block forever
    except asyncio.CancelledError:
        logger.info("🛑 Realtime subscription cancelled")
        await channel.unsubscribe()
        raise

