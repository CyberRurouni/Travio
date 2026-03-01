import logging
from typing import List, Dict
from datetime import timedelta

from .response import generate_ai_response

logger = logging.getLogger("ASSISTANT")


def log_chat_history_readable(
    chat_history: List[Dict], prospect_id: str, session_id: str
):
    """
    Logs chat history in a sequential, human-readable format.
    Includes Prospect ID and Session ID for meticulous tracking.
    Suitable for debugging purposes.
    """
    if not chat_history:
        logger.info(
            "📜 Chat History is empty | 👤 Prospect=%s | 🧩 Session=%s",
            prospect_id,
            session_id,
        )
        return

    logger.info(
        "📜 Chat History (Readable Version) | 👤 Prospect=%s | 🧩 Session=%s",
        prospect_id,
        session_id,
    )

    for entry in chat_history:
        sender = entry.get("sender", "unknown").capitalize()
        text = entry.get("text", "").replace("\r\n", "\n").strip()

        # Differentiate system/state updates clearly
        if sender == "System":
            logger.info("----- SYSTEM LOG -----")
            logger.info(text)
            logger.info("---------------------")
        else:
            logger.info(f"{sender}: {text}\n")


async def handle_outbound_message(
    *,
    assistant,
    smtp_service,
    logger,
    prospect_email: str,
    public_message: str | None = None,
    internal_note: str | None = None,
    subject: str,
    log_prefix: str = "",
    sender: str = "Travio",
    state_action_type: str | None = None,
    state_reason: str | None = None,
):
    """
    Generic outbound handler for public responses, final messages,
    and internal notes.
    """

    try:
        # -----------------------------
        # Public message handling
        # -----------------------------
        if public_message:
            logger.info(
                "%s Sending message | Prospect=%s\n📨 Message:\n%s",
                log_prefix,
                prospect_email,
                public_message,
            )

            # Insert into assistant chat container
            try:
                await assistant.session.chat_container(
                    sender=sender,
                    text=public_message,
                )
            except Exception as e:
                logger.warning("⚠️ Failed inserting message into chat: %s", e)

            # Send email
            try:
                DEFAULT_SUBJECTS = {"not provided", "no subject", ""}
                email_subject = subject.strip().lower() if subject else ""
                final_subject = (
                    "Re: Your Inquiry"
                    if email_subject in DEFAULT_SUBJECTS
                    else f"Re: {subject}"
                )

                await smtp_service.send_email(
                    to=prospect_email,
                    subject=final_subject,
                    body=public_message,
                )
                logger.info("✅ Message successfully sent to prospect.")
            except Exception as e:
                logger.warning("⚠️ Failed sending email: %s", e)

        # -----------------------------
        # Internal note handling
        # -----------------------------
        if internal_note:
            try:
                await assistant.session.chat_container(
                    sender="system",
                    text=f"""[AI_MEMORY]
{internal_note}""",
                )
                logger.info(
                    "📝 Internal note saved to AI memory:\n%s",
                    internal_note,
                )
            except Exception as e:
                logger.warning("⚠️ Failed saving internal note: %s", e)

    except Exception as e:
        logger.error("❌ Error in outbound handler: %s", e)


async def regenerate_and_send(
    assistant,
    smtp_service,
    prospect_email,
    sender,
    intent_guard_data,
    extra_context=None,
):
    updated_history, _ = await assistant.session.chat_container(text="", retrieve=True)

    updated_history_clean = [
        {"sender": m.get("sender"), "text": m.get("text")} for m in updated_history
    ]

    final_ai_result = generate_ai_response(
        chat_container=updated_history_clean,
        intent_guard_data=intent_guard_data,
    )

    final_public = final_ai_result.get("public_message", "")
    final_internal = final_ai_result.get("internal_note", "")

    if final_public:
        await smtp_service.send_email(
            to=prospect_email,
            subject="Re: Your inquiry",
            body=final_public,
        )
        await assistant.session.chat_container(text=final_public, sender=sender)

    if final_internal:
        await assistant.session.chat_container(text=final_internal, sender="system")
