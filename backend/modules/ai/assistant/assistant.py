import logging
from datetime import timedelta
from core import Session
from core import session_broker, safe_redis_operation
from core import TravelIntentGuard, InstanceRegistry
from .response import generate_ai_response

logger = logging.getLogger("ASSISTANT")


class Assistant:

    def __init__(
        self, agency_id: str, prospect_id: str, session_id: str, session: Session
    ) -> None:
        self.agency_id = agency_id
        self.prospect_id = prospect_id
        self.session_id = session_id
        self.session = session

    @staticmethod
    async def get_smtp_service():
        from core import SMTPService

        smtp_registry = InstanceRegistry(ttl=timedelta(hours=6))
        return await smtp_registry.get_or_create(
            key="smtp_service",
            factory=SMTPService,
            factory_type="sync",
        )

    async def response(
        self,
        prospect_email: str,
        agent_email: str,
        msg: str,
        first_impression: dict,
        sender: str = "Assistant",
    ):
        # 🔎 Retrieve history
        result = await self.session.chat_container(
            session_id=self.session_id,
            text="",
            retrieve=True,
        )

        intent_guard_data = {"first_impression": first_impression}

        chat_history, is_prospect_first_msg = result or ([], True)

        logger.info(
            "ℹ️ Retrieved chat history | Session=%s | HasProspectInitiated=%s",
            self.session_id,
            is_prospect_first_msg,
        )

        chat_history_clean = [
            {"sender": m.get("sender"), "text": m.get("text")} for m in chat_history
        ]

        # 🧠 Intent analysis only after first msg
        if not is_prospect_first_msg:
            analysis = TravelIntentGuard.analyze_conversation(
                subject="",
                latest_msg=msg,
                chat_context=chat_history_clean,
            )
            logger.info("ℹ️ AI Analysis: %s", analysis)
            intent_guard_data.update(analysis)

        # 🤖 Generate AI result
        ai_result = generate_ai_response(
            chat_container=chat_history_clean,
            intent_guard_data=intent_guard_data,
        )

        ai_message: str = ai_result.get("message", "")
        ai_actions = ai_result.get("actions", {})
        action_type = ai_actions.get("type")

        if not action_type:
            logger.warning("⚠️ Missing action type, aborting")
            return

        if not ai_message:
            logger.warning("⚠️ Empty AI message for validation, skipping email send")
            return

        # Get SMTP service instance (with caching)
        smtp_service = await Assistant.get_smtp_service()

        # 🎯 Action dispatcher (clean switch structure)
        match action_type:

            case "general_response":
                # Send email to the prospect
                await smtp_service.send_email(
                    to=prospect_email,
                    subject="Re: Your inquiry",
                    body=ai_message,
                )

                # Update chat history in session
                chat_history.append({"sender": "Assistant", "text": ai_message})

            case "database_scan" | "recommend_package":
                from core import db_scanning


                # Extract note for database scanning context along with reason
                note = ai_actions.get("details", {}).get(
                    "note", "No additional context provided"
                )
                reason = ai_actions.get("details", {}).get(
                    "reason", "No reason provided"
                )
                logger.info(
                    "🔍 Database scan needed | Reason: %s | Note: %s", reason, note
                )

                # Perform database scanning with the provided note as context
                scan_results = await db_scanning(note)

                # Note for future AI responses to understand that a database scan has been performed
                note_for_future = f"Database scan performed with context: {note}. Scan results: {scan_results}"
                chat_history.append({"sender": "Assistant", "text": ai_message})
                chat_history.append({"sender": "System", "text": note_for_future})

                # Call the assistant again to generate a new response based on the database scan results
                generate_ai_response(
                    chat_container=chat_history,
                    intent_guard_data=intent_guard_data,
                    db_scan_results=scan_results
                )

            case "seek_validation":
                # Extract note for agent context
                note = ai_actions.get("details", {}).get(
                    "note", "No additional context provided"
                )
                reason = ai_actions.get("details", {}).get(
                    "reason", "No reason provided"
                )
                logger.info(
                    "🔍 Validation needed | Reason: %s | Note: %s", reason, note
                )

                # Send email to agent for validation
                await smtp_service.send_email(
                    to=agent_email,
                    subject="AI Assistant Validation Needed for Prospect: {}".format(
                        prospect_email
                    ),
                    body=f"""Dear Agent
                    The AI Assistant has identified a strong intent from the prospect ({prospect_email}) and is seeking your validation before proceeding.
                    Reason for validation: {reason}
                    Note: {note}
                    Please review the prospect's inquiry and the AI's analysis, then validate whether the assistant can proceed with the recommended action.
                    Thank you.
                    """,
                )

                # success
                note = f"Successfully send validation email to agent | AgentEmail={agent_email}"

                # update chat history with assistant's message and validation note
                chat_history.append({"sender": "Assistant", "text": ai_message})
                chat_history.append(
                    {"sender": "System", "text": f"Validation needed for agent: {note}"}
                )
                chat_history.append({"sender": "System", "text": note})

                # Inform prospect
                Assistant.response(
                    prospect_email=prospect_email,
                    agent_email=agent_email,
                    first_impression=first_impression,
                    sender="Assistant",
                )

            case _:
                logger.warning("⚠️ Unknown action type: %s", action_type)
                return

        logger.info(
            "📨 AI response processed | Session=%s | Prospect=%s | Action=%s",
            self.session_id,
            self.prospect_id,
            action_type,
        )
