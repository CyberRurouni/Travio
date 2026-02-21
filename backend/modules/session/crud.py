import logging
from uuid import UUID
from typing import Any, List, Dict, Optional, Union
from core import db_insert, db_select, db_rpc, db_delete

logger = logging.getLogger("SESSION_CRUD")


from typing import Dict, Any
from uuid import UUID
import logging

logger = logging.getLogger(__name__)


async def initiate_session(prospect_id: str, payload: Dict[str, Any]) -> UUID | None:
    """
    Initiates a session for a prospect, returning the active session ID.
    Uses the atomic Postgres function 'initiate_session' via RPC.

    Args:
        prospect_id (str): UUID of the prospect
        payload (dict): Additional session metadata. Only 'intent', 'intent_confidence',
                        'first_impression', and 'constraints' are used optionally.

    Returns:
        UUID | None: ID of the active session, or None if creation failed
    """

    if not prospect_id or not payload.get("intent"):
        logger.error(
            "❌ Cannot initiate session: missing prospect_id or intent | prospect_id=%s, intent=%s",
            prospect_id,
            payload.get("intent"),
        )
        return None

    try:
        # Prepare parameters for the atomic RPC function
        session_data: Dict[str, Any] = {
            "prospect_id": prospect_id,
            "intent": payload.get("intent"),
            "intent_confidence": payload.get("intent_confidence"),
            "first_impression": payload.get("first_impression"),
            "constraints": None,
        }

        # Call atomic Postgres function via RPC
        result = await db_rpc("initiate_session", session_data)

        # Postgres RPC returns a dict with the session ID
        session_id: str | None = None
        if isinstance(result, dict):
            session_id = result.get("id")
        elif isinstance(result, list) and len(result) > 0:
            # Sometimes Supabase returns a list of rows
            session_id = result[0].get("id")

        if not session_id:
            logger.error(
                "❌ Failed to get session ID from RPC | Prospect ID=%s | payload=%s",
                prospect_id,
                payload,
            )
            return None

        logger.info(
            "✅ Active session returned | Session ID=%s | Prospect ID=%s",
            session_id,
            prospect_id,
        )

        return UUID(session_id)

    except Exception as e:
        logger.exception(
            "❌ Exception while initiating session | Prospect ID=%s | Error=%s",
            prospect_id,
            e,
        )
        return None


async def fetch_ongoing_session(
    prospect_id: str,
    fields: str = "id",  # can be "id" or "id,intent,intent_confidence,first_impression"
) -> Optional[Union[UUID, dict]]:
    """
    Fetch the ongoing (non-ended) session for a given prospect.

    Args:
        prospect_id (str): UUID of the prospect
        fields (str): Comma-separated fields to fetch. Defaults to "id".
                      Can include "intent,intent_confidence,first_impression"

    Returns:
        UUID | dict | None:
            - If fields="id", returns the session UUID or None
            - If fields includes more, returns a dict with requested fields
    """
    if not prospect_id:
        logger.error("❌ Cannot fetch session: missing prospect_id")
        return None

    try:
        result = await db_select(
            table="sessions",
            filters={
                "prospect_id": prospect_id,
                "is_ended": False,
            },
            fields=fields,
            limit=1,
        )

        if not isinstance(result, list) or not result:
            logger.info(
                "ℹ️ No ongoing session found | Prospect ID=%s",
                prospect_id,
            )
            return None

        row = result[0]

        if not isinstance(row, dict):
            logger.error(
                "❌ Invalid session row format | Prospect ID=%s | Row=%s",
                prospect_id,
                row,
            )
            return None

        # If only ID is requested, return UUID directly
        if fields.strip() == "id":
            raw_id = row.get("id")
            if raw_id is None:
                logger.error(
                    "❌ Session row missing ID | Prospect ID=%s",
                    prospect_id,
                )
                return None

            if isinstance(raw_id, UUID):
                return raw_id
            if isinstance(raw_id, str):
                try:
                    return UUID(raw_id)
                except ValueError:
                    logger.error(
                        "❌ Invalid session UUID format | Prospect ID=%s | ID=%s",
                        prospect_id,
                        raw_id,
                    )
                    return None
            logger.error(
                "❌ Unexpected session ID type | Prospect ID=%s | Type=%s",
                prospect_id,
                type(raw_id),
            )
            return None

        # Otherwise, return dict with requested fields
        # Ensure ID is a UUID
        if "id" in row:
            raw_id = row["id"]
            if isinstance(raw_id, str):
                try:
                    row["id"] = str(UUID(raw_id))  # <-- store as string
                except ValueError:
                    logger.error(
                        "❌ Invalid session UUID format | Prospect ID=%s | ID=%s",
                        prospect_id,
                        raw_id,
                    )
                    row["id"] = None
        return row

    except Exception as e:
        logger.error(
            "❌ Failed to fetch ongoing session | Prospect ID=%s | Error=%s",
            prospect_id,
            e,
            exc_info=True,
        )
        return None


async def create_session_message(
    session_id: str, msg: str, sender: str = "prospect"
) -> None:
    """
    Inserts a new chat message for a session.

    Args:
        session_id (str): UUID of the session the message belongs to
        msg (str): The chat message content
        sender (str, optional): Who sent the message ('prospect' | 'assistant'). Defaults to 'prospect'.
    """
    if not session_id and not msg:
        logger.error(
            "❌ Cannot create session message: missing session_id or msg | session_id=%s, msg=%s",
            session_id,
            msg,
        )
        return

    try:
        chat_data = {"session_id": session_id, "sender": sender.lower(), "msg": msg}

        await db_insert(
            table="chats",
            data=chat_data,
            return_mode="none",  # no need to return anything
        )

        logger.info(
            "✅ Chat message inserted | Session ID=%s | Sender=%s | Msg=%s",
            session_id,
            sender,
            msg,
        )

    except Exception as e:
        logger.error(
            "❌ Exception while inserting session message | Session ID=%s | Error=%s",
            session_id,
            e,
            exc_info=True,
        )


async def fetch_session_chats(session_id: str) -> list[dict[str, Any]]:
    """
    Retrieves all chat messages for a given session, ordered by msg_sequence.

    Args:
        session_id (str): UUID of the session

    Returns:
        list[dict]: List of chat rows (empty list if none found or on error)
    """
    if not session_id:
        logger.error("❌ Cannot fetch session chats: missing session_id")
        return []

    try:
        result = await db_select(
            table="chats", filters={"session_id": session_id}, order_by="msg_sequence"
        )

        if not isinstance(result, list):
            logger.warning(
                "⚠️ Unexpected result type when fetching session chats | Session ID=%s | Type=%s",
                session_id,
                type(result).__name__,
            )
            return []

        # Keep only dict rows
        chats = [row for row in result if isinstance(row, dict)]

        logger.info(
            "✅ Fetched %d chat messages | Session ID=%s", len(chats), session_id
        )

        return chats

    except Exception as e:
        logger.error(
            "❌ Exception while fetching session chats | Session ID=%s | Error=%s",
            session_id,
            e,
            exc_info=True,
        )
        return []


async def delete_all_chats(session_id: str) -> bool:
    """
    Permanently deletes all chat messages for a given session from the database.

    Args:
        session_id (str): UUID of the session

    Returns:
        bool: True if deletion succeeded, False otherwise
    """
    if not session_id:
        logger.error("❌ Cannot delete chats: missing session_id")
        return False

    try:
        result = await db_delete(
            table="chats",
            filters={"session_id": session_id},
            returning="none",  # hard delete, do not return any rows
        )

        if result is True:
            logger.info("✅ All chat messages deleted | Session ID=%s", session_id)
            return True

        logger.warning(
            "⚠️ db_delete returned unexpected value | Session ID=%s | Result=%s",
            session_id,
            result,
        )
        return False

    except Exception as e:
        logger.error(
            "❌ Exception while deleting chat messages | Session ID=%s | Error=%s",
            session_id,
            e,
            exc_info=True,
        )
        return False
