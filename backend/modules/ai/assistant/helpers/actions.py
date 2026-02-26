import logging
import json
from .utils import regenerate_and_send, handle_outbound_message

logger = logging.getLogger("ASSISTANT")
logging.basicConfig(level=logging.INFO)


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
    prospect_id,
    session_id,
):
    from core import db_scanning

    public_message = ai_result.get("public_message", "")
    internal_note = ai_result.get("internal_note", "")

    try:
        match action_type:

            # -------------------- GENERAL RESPONSE --------------------
            case "general_response":
                try:
                    logger.info(
                        "💬 Processing general response | Prospect=%s",
                        prospect_email
                    )

                    await handle_outbound_message(
                        assistant=assistant,
                        smtp_service=smtp_service,
                        logger=logger,
                        prospect_email=prospect_email,
                        public_message=public_message,
                        internal_note=internal_note,
                        sender = sender,
                        log_prefix="💬 General response |",
                    )

                except Exception as e:
                    logger.error("❌ Error in general_response action: %s", e)

            # -------------------- DATABASE SCAN / PACKAGE RECOMMENDATION --------------------
            case "database_scan" | "recommend_package":
                try:
                    note = action_details.get("note", "")
                    reason = action_details.get("reason", "")
                    extra = action_details.get("extra", {})

                    logger.info(
                        "🔍 Starting database scan | Prospect=%s | Reason: %s",
                        prospect_email,
                        reason,
                    )
                    logger.info("📝 Scan note:\n%s", note)

                    # 1️⃣ Log pending state
                    try:
                        await assistant.session.chat_container(
                            text=f"""[STATE_UPDATE]
action_type: database_scan
status: pending
reason: {reason}
context: {note}
exclude previous ids = {extra.get("exclude_previous_ids", False)}
results: null""",
                            sender="system",
                        )
                        logger.info("⏳ Database scan pending...")
                    except Exception as e:
                        logger.warning("⚠️ Failed logging pending scan state: %s", e)

                    # 2️⃣ Execute scan
                    try:
                        scanning = await db_scanning(
                            note,
                            session_id=assistant.session.session_id,
                            prospect_id=assistant.prospect_id,
                            exclude_previous_ids=extra.get(
                                "exclude_previous_ids", False
                            ),
                        )
                    except Exception as e:
                        scanning = {}
                        logger.error("❌ Database scan failed: %s", e)

                    # 3️⃣ Log completion state
                    try:
                        await assistant.session.chat_container(
                            text=f"""[STATE_UPDATE]
action_type: database_scan
status: success
reason: Scan completed
context: {note}
exclude previous ids = {extra.get("exclude_previous_ids", False)}
results: {json.dumps(scanning, indent=2)}""",
                            sender="system",
                        )
                        logger.info(
                            "✅ Database scan completed.\n📊 Results:\n%s",
                            json.dumps(scanning, indent=2),
                        )
                    except Exception as e:
                        logger.warning("⚠️ Failed logging scan completion: %s", e)

                    # 4️⃣ Regenerate deterministically
                    try:
                        await regenerate_and_send(
                            assistant,
                            smtp_service,
                            prospect_email,
                            sender,
                            intent_guard_data,
                        )
                        logger.info("🔄 Regeneration after scan triggered.")
                    except Exception as e:
                        logger.warning("⚠️ Failed regenerating after scan: %s", e)

                except Exception as e:
                    logger.error(
                        "❌ Error in database_scan/recommend_package action: %s", e
                    )

            # -------------------- SEEK HUMAN CONTACT --------------------
            case "connect_human_agent":
                try:
                    note = action_details.get("note", "")
                    reason = action_details.get("reason", "")

                    logger.info(
                        "🤝 Connecting human agent | Prospect=%s | Reason: %s",
                        prospect_email,
                        reason,
                    )
                    logger.info("📝 Handoff context:\n%s", note)

                    # 1️⃣ Log pending state
                    try:
                        await assistant.session.chat_container(
                            text=f"""[STATE_UPDATE]
            action_type: connect_human_agent
            status: pending
            reason: {reason}
            context: {note}
            results: null""",
                            sender="system",
                        )
                        logger.info("⏳ Human agent handoff pending...")
                    except Exception as e:
                        logger.warning("⚠️ Failed logging pending handoff: %s", e)

                    # 2️⃣ Send email to agent (fire-and-forget)
                    try:
                        agent_email_body = f"""
            Dear Agent,

            A prospect requires direct assistance.

            Prospect: {prospect_email}
            Reason: {reason}
            Context: {note}

            Please reach out to the prospect directly.
            """
                        await smtp_service.send_email(
                            to=agent_email,
                            subject=f"Human Assistance Required: {prospect_email}",
                            body=agent_email_body,
                        )
                        logger.info("✅ Handoff email sent to agent.")
                    except Exception as e:
                        logger.warning("⚠️ Failed sending handoff email: %s", e)

                    # 3️⃣ Log success state
                    try:
                        await assistant.session.chat_container(
                            text=f"""[STATE_UPDATE]
            action_type: connect_human_agent
            status: email_sent
            context: {note}
            reason: {reason}
            results: Handoff email sent to agent
            """,
                            sender="system",
                        )
                    except Exception as e:
                        logger.warning("⚠️ Failed logging handoff completion: %s", e)

                    # 4️⃣ Regenerate immediately (close loop deterministically)
                    await regenerate_and_send(
                        assistant,
                        smtp_service,
                        prospect_email,
                        sender,
                        intent_guard_data,
                    )

                    logger.info(
                        "🔄 Regeneration after human agent handoff triggered (closed-loop)."
                    )

                except Exception as e:
                    logger.error("❌ Error in connect_human_agent action: %s", e)

            # -------------------- REFERENCE RECOMMENDATION --------------------
            case "reference_recommendation":
                try:
                    from core import generate_embeddings, ReferenceMemory

                    reference_context = action_details.get("reference_context", {})
                    basis = reference_context.get("basis")
                    note = reference_context.get("note", "")
                    reason = action_details.get("reason", "")

                    logger.info(
                        "📚 Reference recommendation | Prospect=%s | Reason: %s",
                        prospect_email,
                        reason,
                    )
                    logger.info("📝 Reference note:\n%s", note)
                    logger.info("🔑 Basis: %s", basis)

                    try:
                        await assistant.session.chat_container(
                            text=f"""[STATE_UPDATE]
action_type: reference_recommendation
status: pending
reason: {reason}
context: {note}
results: null""",
                            sender="system",
                        )
                        logger.info("⏳ Reference retrieval pending...")
                    except Exception as e:
                        logger.warning(
                            "⚠️ Failed logging pending reference retrieval: %s", e
                        )

                    # Embedding + retrieval
                    try:
                        embedding = await generate_embeddings(note)
                        reference_packages, reference_msg = (
                            await ReferenceMemory.retrieve(
                                session_id=assistant.session.session_id,
                                embedding=embedding,
                                basis=basis,
                            )
                        )
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
                        logger.info(
                            "✅ Reference retrieval completed.\n📄 Reference Message:\n%s\n📦 Reference Packages:\n%s",
                            reference_msg,
                            json.dumps(reference_packages, indent=2),
                        )
                    except Exception as e:
                        logger.warning("⚠️ Failed retrieving references: %s", e)

                    try:
                        await regenerate_and_send(
                            assistant,
                            smtp_service,
                            prospect_email,
                            sender,
                            intent_guard_data,
                        )
                        logger.info(
                            "🔄 Regeneration after reference recommendation triggered."
                        )
                    except Exception as e:
                        logger.warning(
                            "⚠️ Failed regenerating after reference recommendation: %s",
                            e,
                        )

                except Exception as e:
                    logger.error("❌ Error in reference_recommendation action: %s", e)

            # -------------------- SEEK BOOKING --------------------
            case "seek_booking":
                try:
                    note = action_details.get("note", "")
                    reason = action_details.get("reason", "")

                    logger.info(
                        "📅 Seeking booking | Prospect=%s | Reason: %s",
                        prospect_email,
                        reason,
                    )
                    logger.info("📝 Booking context:\n%s", note)

                    try:
                        await assistant.session.chat_container(
                            text=f"""[STATE_UPDATE]
action_type: seek_booking
status: pending
reason: {reason}
context: {note}
results: null""",
                            sender="system",
                        )
                        logger.info("⏳ Booking request pending...")
                    except Exception as e:
                        logger.warning("⚠️ Failed logging pending booking: %s", e)

                    try:
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
                        logger.info(
                            "✅ Booking email sent to agent: %s\n📨 Email Body:\n%s",
                            agent_email,
                            booking_email_body,
                        )
                    except Exception as e:
                        logger.warning("⚠️ Failed sending booking email: %s", e)

                    try:
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
                        logger.info("🔄 Regeneration after booking triggered.")
                    except Exception as e:
                        logger.warning(
                            "⚠️ Failed logging booking completion or regeneration: %s", e
                        )

                except Exception as e:
                    logger.error("❌ Error in seek_booking action: %s", e)

            # -------------------- Final Message --------------------
            case "final_message":
                from core import SessionHelper
                try:
                    logger.info(
                        "🎯 Final message reached | Prospect=%s",
                        prospect_email,
                    )

                    # Use shared handler for public message
                    await handle_outbound_message(
                        assistant=assistant,
                        smtp_service=smtp_service,
                        logger=logger,
                        prospect_email=prospect_email,
                        public_message=public_message,
                        sender = sender,
                        log_prefix="🎯 Final message |",
                    )

                    # End Session
                    await SessionHelper.end_session(
                        prospect_id=prospect_id,
                        session_id=session_id
                    )

                except Exception as e:
                    logger.error("❌ Error in final_message action: %s", e)

            # -------------------- UNKNOWN --------------------
            case _:
                logger.warning("⚠️ Unknown action type: %s", action_type)

    except Exception as e:
        logger.critical("❌ Unexpected error in handle_action: %s", e)

    finally:
        logger.info(
            "📨 AI response processed | Session=%s | Prospect=%s | Action=%s",
            getattr(assistant.session, "session_id", "unknown"),
            prospect_email,
            action_type,
        )
