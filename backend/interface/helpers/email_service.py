import os
import json
import asyncio
import logging

from collections import defaultdict
from datetime import timedelta

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s.%(msecs)03d | %(levelname)s | %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("AI_EMAIL_ASSISTANT")


class EmailService:
    """
    Centralized Email Service responsible for:
    - Running IMAP IDLE + scanning in a background thread
    - Consuming emails from Redis
    - Processing email business logic
    """

    # Enforce a SINGLE IMAP worker process-wide
    imap_lock = asyncio.Lock()
    _imap_task_started = False

    def __init__(self):
        logger.info("📦 EmailService initialized")

    # ─────────────────────────────────────────────
    # IMAP Worker (fire-and-forget, thread-backed)
    # ─────────────────────────────────────────────
    async def start_imap_worker(self):
        """
        Starts the IMAP worker ONCE.
        Runs forever in a background thread.
        """
        from core import fetch_unread_emails

        async with EmailService.imap_lock:
            if EmailService._imap_task_started:
                logger.info("📡 IMAP worker already running")
                return

            logger.info("📡 Starting IMAP worker thread")

            asyncio.create_task(
                asyncio.to_thread(fetch_unread_emails),
                name="imap-worker",
            )

            EmailService._imap_task_started = True

    # ─────────────────────────────────────────────
    # Perpetual Email Processor (Redis consumer)
    # ─────────────────────────────────────────────
    async def perpetual_email_processor(self):
        """
        Continuously consumes emails from Redis and processes them.
        """
        from core import emails_stream

        logger.info("📨 Email processor started")

        while True:
            try:
                emails = emails_stream.consume(
                    count=5,
                    use_group=True,
                    consumer_name="email_processor_1",
                    block_ms=5000,
                )

                if not emails:
                    continue

                tasks = []
                for email in emails:
                    tasks.append(
                        asyncio.create_task(
                            self.process_email(email),
                            name=f"process-email-{email[0]}",
                        )
                    )

                results = await asyncio.gather(*tasks, return_exceptions=True)

                # Log any task failures
                for i, result in enumerate(results):
                    if isinstance(result, Exception):
                        logger.error(f"❌ Email processing task {i} failed: {result}")

            except Exception as e:
                logger.error(f"❌ Email consumer loop error: {e}", exc_info=True)
                await asyncio.sleep(1)  # Prevent tight error loop

    # ─────────────────────────────────────────────
    # Email Processing Logic (async-safe)
    # ─────────────────────────────────────────────
    async def process_email(self, email):
        """
        Process a single email event safely.

        Flow:
        - Validate payload
        - Classify sender intent
        - Resolve / register prospect
        - Create or reuse session
        - Generate assistant response
        - Acknowledge Redis event
        """
        from core import (
            emails_stream,
            AGENT_EMAIL,
            classify_sender,
            send_clarification_email,
            is_internal_agent_email,
            HandleProspect,
            InstanceRegistry,
            Session,
            Assistant,
            CaseAgent,
        )
        from ..helpers.utils import strip_email_reply_tail

        email_id, content = email
        logger.info(f"📩 Processing email | ID={email_id}")

        try:
            # ─── Guard: empty payload ──────────────────────────────
            if content is None:
                logger.warning(f"⚠️ Empty email payload | ID={email_id}")
                return

            # ─── Guard: invalid JSON ──────────────────────────────
            if isinstance(content, str):
                try:
                    content = json.loads(content)
                except json.JSONDecodeError:
                    logger.error(f"❌ Invalid JSON payload | ID={email_id}")
                    return

            # ─── Guard: unsupported payload type ──────────────────
            if not isinstance(content, dict):
                logger.error(f"❌ Unsupported payload type | ID={email_id}")
                return

            # ─── Normalize email fields ───────────────────────────
            sender_email = content.get("sender_email") or "unknown@unknown"
            sender_name = content.get("sender_name") or "Unknown"
            subject = content.get("subject") or "No Subject"
            body = content.get("body") or ""

            # ─── Strip email reply tail ──────────────────────────
            body = strip_email_reply_tail(body=body)

            # ─── Guard: internal agent email ───────────────────────
            if is_internal_agent_email(sender_email):
                logger.info(f"👤 Email from internal agent | Email={sender_email}")

                # ─── Agent lifecycle ──────────────────────────────
                case_agent_registry = InstanceRegistry(ttl=timedelta(hours=1))
                case_agent = await case_agent_registry.get_or_create(
                    key=sender_email,
                    factory=CaseAgent,
                    agent_email=sender_email,
                    message=body,
                    subject=subject,
                    factory_type="async",
                )

                # ─── Execute agent final message flow ─────────────────
                await case_agent.final_message()

                # ─── Done processing internal agent email ─────────────
                return

            # ─── Classify sender intent ───────────────────────────
            classification = classify_sender(
                sender_id=sender_email,
                subject=subject,
                message=body,
            )

            logger.info(f"🧪 Classification result | {classification}")

            # ─── Action: clarification required ──────────────────
            if classification.get("re_evaluation_needed"):
                send_clarification_email(
                    classification=classification,
                    sender_email=sender_email,
                )

            # ─── Guard: not a prospect ────────────────────────────
            if classification.get("is_prospect") is False:
                return

            # ─── Prospect lifecycle ──────────────────────────────
            prospect_registry = InstanceRegistry(ttl=timedelta(hours=1))

            prospect: HandleProspect = await prospect_registry.get_or_create(
                key=sender_email,
                factory=HandleProspect.create,
                issued_email=str(os.getenv("EMAIL")),
                prospect_name=sender_name,
                identifier=sender_email,
                factory_type="async",
            )

            # ─── Prospect registration ───────────────────────────
            agency_id, prospect_id = await prospect.register(channel_type="email")

            # ─── Guard: failed prospect registration ─────────────
            if not agency_id or not prospect_id:
                logger.error("❌ Prospect registration failed")
                return

            # ─── Prospect presence update ────────────────────────
            await prospect.mark_presence(channel_type="email")

            # ─── Session lifecycle ───────────────────────────────
            session_registry = InstanceRegistry(ttl=timedelta(hours=1))

            session = await session_registry.get_or_create(
                key=f"{agency_id}:{prospect_id}",
                factory=Session.initiate_session,
                agency_id=str(agency_id),
                prospect_id=str(prospect_id),
                msg=body,
                subject=subject,
                factory_type="async",
            )

            # ─── Persist inbound message ─────────────────────────
            await session.chat_container(
                sender="prospect",
                text=body,
            )

            # ─── Assistant lifecycle ─────────────────────────────
            assistant_registry = InstanceRegistry(ttl=timedelta(hours=1))

            assistant = await assistant_registry.get_or_create(
                key=f"{agency_id}:{prospect_id}:{session.session_id}",
                factory=Assistant,
                agency_id=str(agency_id),
                prospect_id=str(prospect_id),
                session=session,
                factory_type="sync",
            )

            # ─── Assistant response generation ───────────────────
            await assistant.response(
                prospect_email=sender_email,
                msg=body,
                first_impression=session._cached_first_impression,
                agent_email=AGENT_EMAIL,
            )

        except Exception:
            # ─── Guard: unhandled processing error ───────────────
            logger.exception(f"🔥 Failed processing email | ID={email_id}")

        finally:
            # ─── Finalize: acknowledge Redis event ───────────────
            emails_stream._acknowledge(email_id)
            logger.info(f"✅ Email acknowledged | ID={email_id}")
