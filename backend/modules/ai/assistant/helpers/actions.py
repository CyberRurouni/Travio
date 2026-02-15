import logging
import json
from .utils import regenerate_and_send

logger = logging.getLogger("ASSISTANT")


async def handle_action(
    assistant,
    action_type,
    action_details,
    ai_result,
    smtp_service,
    prospect_email,
    agent_email,
    sender,
    intent_guard_data,
):
    from core import db_scanning

    public_message = ai_result.get("public_message", "")
    internal_note = ai_result.get("internal_note", "")

    match action_type:

        # -------------------- GENERAL RESPONSE --------------------
        case "general_response":
            if public_message:
                await smtp_service.send_email(
                    to=prospect_email,
                    subject="Re: Your inquiry",
                    body=public_message,
                )

                await assistant.session.chat_container(
                    text=f"""[STATE_UPDATE]
        action_type: general_response
        status: success
        reason: Responded directly to prospect inquiry
        context: null
        results: null""",
                    sender="system",
                )

            if internal_note:
                await assistant.session.chat_container(
                    text=f"""[AI_MEMORY]
        {internal_note}""",
                    sender="system",
                )

        # -------------------- DATABASE SCAN / PACKAGE RECOMMENDATION --------------------
        case "database_scan" | "recommend_package":

            note = action_details.get("note", "")
            reason = action_details.get("reason", "")

            # 1️⃣ Log pending state
            await assistant.session.chat_container(
                text=f"""[STATE_UPDATE]
        action_type: database_scan
        status: pending
        reason: {reason}
        context: {note}
        results: null""",
                sender="system",
            )

            # 2️⃣ Execute scan
            scanning = await db_scanning(note, session_id=assistant.session.session_id)

            # 3️⃣ Log completion state
            await assistant.session.chat_container(
                text=f"""[STATE_UPDATE]
        action_type: database_scan
        status: success
        reason: Scan completed
        context: {note}
        results: {json.dumps(scanning, indent=2)}""",
                sender="system",
            )

            # 4️⃣ Regenerate deterministically
            await regenerate_and_send(
                assistant,
                smtp_service,
                prospect_email,
                sender,
                intent_guard_data,
            )

        # -------------------- SEEK VALIDATION --------------------
        case "seek_validation":
            note = action_details.get("note", "")
            reason = action_details.get("reason", "")

            # 1️⃣ Log pending validation
            await assistant.session.chat_container(
                text=f"""[STATE_UPDATE]
        action_type: seek_validation
        status: pending
        reason: {reason}
        context: {note}
        results: null""",
                sender="system",
            )

            # 2️⃣ Email agent
            validation_email_body = f"""
        Dear Agent,

        Prospect: {prospect_email}
        Reason: {reason}
        Context: {note}

        Please confirm whether to proceed.
        """

            await smtp_service.send_email(
                to=agent_email,
                subject=f"Validation Required: {prospect_email}",
                body=validation_email_body,
            )

            # 3️⃣ Log completion
            await assistant.session.chat_container(
                text=f"""[STATE_UPDATE]
        action_type: seek_validation
        status: success
        reason: Validation request sent to agent
        context: {note}
        results: null""",
                sender="system",
            )

            await regenerate_and_send(
                assistant,
                smtp_service,
                prospect_email,
                sender,
                intent_guard_data,
            )

        # -------------------- REFERENCE RECOMMENDATION --------------------
        case "reference_recommendation":
            from core import generate_embeddings, ReferenceMemory

            reference_context = action_details.get("reference_context", {})
            basis = reference_context.get("basis")
            note = reference_context.get("note", "")
            reason = action_details.get("reason", "")

            # 1️⃣ Log pending retrieval
            await assistant.session.chat_container(
                text=f"""[STATE_UPDATE]
        action_type: reference_recommendation
        status: pending
        reason: {reason}
        context: {note}
        results: null""",
                sender="system",
            )

            embedding = await generate_embeddings(note)

            reference_packages, reference_msg = await ReferenceMemory.retrieve(
                session_id=assistant.session.session_id,
                embedding=embedding,
                basis=basis,
            )

            # 2️⃣ Log completion
            await assistant.session.chat_container(
                text=f"""[STATE_UPDATE]
        action_type: reference_recommendation
        status: success
        reason: Retrieval completed
        context: {note}
        results: {{
            "reference_message": {json.dumps(reference_msg)},
            "reference_packages": {json.dumps(reference_packages)}
        }}""",
                sender="system",
            )

            await regenerate_and_send(
                assistant,
                smtp_service,
                prospect_email,
                sender,
                intent_guard_data,
            )

        # -------------------- SEEK BOOKING --------------------
        case "seek_booking":
            note = action_details.get("note", "")
            reason = action_details.get("reason", "")

            # 1️⃣ Log pending booking
            await assistant.session.chat_container(
                text=f"""[STATE_UPDATE]
        action_type: seek_booking
        status: pending
        reason: {reason}
        context: {note}
        results: null""",
                sender="system",
            )

            booking_email_body = f"""
        Dear Agent,

        Prospect: {prospect_email}
        Reason: {reason}
        Context: {note}

        Please proceed with booking process.
        """

            await smtp_service.send_email(
                to=agent_email,
                subject=f"Booking Request: {prospect_email}",
                body=booking_email_body,
            )

            # 2️⃣ Log success
            await assistant.session.chat_container(
                text=f"""[STATE_UPDATE]
        action_type: seek_booking
        status: success
        reason: Booking request sent to agent
        context: {note}
        results: null""",
                sender="system",
            )

            await regenerate_and_send(
                assistant,
                smtp_service,
                prospect_email,
                sender,
                intent_guard_data,
            )

        # -------------------- UNKNOWN --------------------
        case _:
            logger.warning("⚠️ Unknown action type: %s", action_type)

    logger.info(
        "📨 AI response processed | Session=%s | Prospect=%s | Action=%s",
        assistant.session.session_id,
        prospect_email,
        action_type,
    )
