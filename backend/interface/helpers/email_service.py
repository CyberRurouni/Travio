import json
import asyncio
import logging

from datetime import timedelta

from core import (
    prospect_registry,
    session_registry,
    assistant_registry,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s.%(msecs)03d | %(levelname)s | %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("AI_EMAIL_ASSISTANT")


class EmailService:
    """
    Multi-agency Email Service.

    - One perpetual IMAP worker thread per agency (24/7, IDLE-based)
    - When an IMAP scan finds new emails → triggers a short-lived processor
      for ONLY that agency to drain its Redis stream then exit
    - No perpetual polling loop — processor only runs when there's work to do
    """

    _running_imap_workers: set[str] = set()
    _imap_lock = asyncio.Lock()

    def __init__(self):
        logger.info("📦 EmailService initialized")

    # ─────────────────────────────────────────────
    # Startup: one perpetual IMAP worker per agency
    # ─────────────────────────────────────────────
    async def start_all_imap_workers(self):
        """
        Load all agencies from DB and start one perpetual IMAP thread per agency.
        Safe to call multiple times — skips already-running workers.
        """
        from core import list_agencies, get_agency_password

        agencies = await list_agencies()

        if not agencies:
            logger.warning("⚠️ No agencies found. No IMAP workers started.")
            return

        loop = asyncio.get_event_loop()

        async with EmailService._imap_lock:
            for agency in agencies:
                agency_id = str(agency["id"])

                if agency_id in EmailService._running_imap_workers:
                    logger.info(f"[{agency_id}] 📡 IMAP worker already running")
                    continue

                # Decrypt password from Vault before handing to thread
                password = await get_agency_password(agency)
                if not password:
                    logger.error(
                        f"[{agency_id}] ❌ Could not retrieve password, skipping"
                    )
                    continue

                # Attach decrypted password to agency dict for the thread
                agency_with_pass = {**agency, "app_password": password}

                logger.info(
                    f"[{agency_id}] 📡 Starting IMAP worker for {agency['issued_email']}"
                )

                asyncio.create_task(
                    asyncio.to_thread(
                        self._run_imap_worker,
                        agency_with_pass,
                        loop,
                    ),
                    name=f"imap-worker-{agency_id}",
                )

                EmailService._running_imap_workers.add(agency_id)

        logger.info(
            f"✅ {len(EmailService._running_imap_workers)} IMAP worker(s) running"
        )

    # ─────────────────────────────────────────────
    # Thread target: wraps the perpetual IMAP loop
    # ─────────────────────────────────────────────
    def _run_imap_worker(self, agency: dict, loop: asyncio.AbstractEventLoop):
        """
        Runs in a background thread (via asyncio.to_thread).
        Passes a callback so scanning can trigger the async processor
        without blocking the thread.
        """
        from core import fetch_unread_emails_for_agency

        def on_new_emails(agency_id: str):
            """
            Called from the IMAP thread only when new emails were published.
            Schedules a short-lived processor coroutine on the event loop.
            """
            asyncio.run_coroutine_threadsafe(
                self._trigger_processor(agency_id),
                loop,
            )

        fetch_unread_emails_for_agency(agency, on_new_emails=on_new_emails)

    # ─────────────────────────────────────────────
    # Triggered processor — runs only when needed
    # ─────────────────────────────────────────────
    async def _trigger_processor(self, agency_id: str):
        """
        Short-lived processor for one agency.
        Drains that agency's Redis stream completely, then exits.

        This is NOT a perpetual loop — it starts when emails arrive
        and stops when the stream is empty.
        """
        from core import get_or_create_agency_email_stream, get_agency_password

        stream = get_or_create_agency_email_stream(agency_id)
        app_password = await get_agency_password(agency_id)
        logger.info(f"[{agency_id}] ⚡ Processor triggered")

        processed = 0
        try:
            while True:
                emails = stream.consume(
                    count=5,
                    use_group=True,
                    consumer_name=f"processor_{agency_id}",
                    block_ms=1000,  # short block — exits fast when stream is empty
                )

                if not emails:
                    break  # stream drained, processor exits cleanly

                tasks = [
                    asyncio.create_task(
                        self.process_email(email_event, agency_id, app_password),
                        name=f"process-{agency_id}-{email_event[0]}",
                    )
                    for email_event in emails
                ]

                results = await asyncio.gather(*tasks, return_exceptions=True)
                processed += len(tasks)

                for i, result in enumerate(results):
                    if isinstance(result, Exception):
                        logger.error(f"[{agency_id}] ❌ Task {i} failed: {result}")

        except Exception as e:
            logger.error(f"[{agency_id}] ❌ Processor error: {e}", exc_info=True)

        logger.info(f"[{agency_id}] ✅ Processor done — {processed} email(s) handled")

    # ─────────────────────────────────────────────
    # Core email processing logic
    # ─────────────────────────────────────────────
    async def process_email(
        self, email_event: tuple, agency_id: str, app_password: str
    ):
        """
        Process a single email for a specific agency.
        All agency context comes from the Redis payload — no env vars.
        """
        from core import (
            get_or_create_agency_email_stream,
            classify_sender,
            is_internal_agent_email,
            safe_redis_operation,
            fetch_last_email_subject,
            HandleProspect,
            Session,
            Assistant,
            CaseAgent,
            session_broker,
        )
        from .utils import extract_clean_email_body

        email_id, content = email_event
        logger.info(f"[{agency_id}] 📩 Processing email | ID={email_id}")

        stream = get_or_create_agency_email_stream(agency_id)

        try:
            # ─── Guard: empty payload ─────────────────────────────
            if content is None:
                logger.warning(f"[{agency_id}] ⚠️ Empty payload | ID={email_id}")
                return

            # ─── Guard: parse JSON ────────────────────────────────
            if isinstance(content, str):
                try:
                    content = json.loads(content)
                except json.JSONDecodeError:
                    logger.error(f"[{agency_id}] ❌ Invalid JSON | ID={email_id}")
                    return

            if not isinstance(content, dict):
                logger.error(f"[{agency_id}] ❌ Bad payload type | ID={email_id}")
                return

            # ─── Extract fields (all from payload) ───────
            issued_email = content.get("issued_email") or ""
            agent_email = content.get("agent_email") or ""
            agency_name = content.get("agency_name") or "Unknown Agency"
            sender_email = content.get("sender_email") or "unknown@unknown"
            sender_name = content.get("sender_name") or "Unknown"
            subject = content.get("subject") or "No Subject"
            body = content.get("body") or ""

            message = extract_clean_email_body(body=body)

            # ─── Guard: internal agent email ──────────────────────
            if is_internal_agent_email(sender_email, agent_email):
                logger.info(
                    f"[{agency_id}] 👤 Internal agent email from {sender_email}"
                )
                case_agent = CaseAgent(
                    agent_email=sender_email,
                    msg=message,
                    issued_email=issued_email,
                    subject=subject,
                )
                await case_agent.final_message()
                return

            # ─── Classify sender intent ───────────────────────────
            classification = await classify_sender(
                agency_id=agency_id,
                sender_id=sender_email,
                subject=subject,
                message=message,
                issued_email=issued_email,
                app_password=app_password,
            )
            logger.info(f"[{agency_id}] 🧪 Classification: {classification}")

            if classification.get("is_prospect") is False:
                return

            # ─── Prospect lifecycle ───────────────────────────────
            prospect: HandleProspect = await prospect_registry.get_or_create(
                key=f"{agency_id}:{sender_email}",  # scoped to agency
                factory=HandleProspect.create,
                issued_email=issued_email,
                prospect_name=sender_name,
                identifier=sender_email,
                factory_type="async",
            )

            agency_db_id, agency_prospect_id = await prospect.register(
                channel_type="email"
            )

            if not agency_db_id or not agency_prospect_id:
                logger.error(f"[{agency_id}] ❌ Prospect registration failed")
                return

            await prospect.mark_presence(channel_type="email")

            # ─── Session lifecycle ────────────────────────────────
            session = await session_registry.get_or_create(
                key=f"{agency_db_id}:{agency_prospect_id}",
                factory=Session.initiate_session,
                agency_id=str(agency_db_id),
                agency_prospect_id=str(agency_prospect_id),
                msg=message,
                subject=subject,
                factory_type="async",
            )

            if session is None:
                logger.error(
                    f"[{agency_id}] ❌ Session failed | Prospect={agency_prospect_id}"
                )
                return

            # ─── Persist inbound message ─────────────────────────
            await session.chat_container(sender="prospect", text=message)

            # ─── Fetch & Store Last Subject ─────────────────────────
            last_subject_key = f"{agency_prospect_id}:{session.session_id}:last_subject"

            DEFAULT_SUBJECTS = {"not provided", "no subject", ""}
            email_subject = subject.strip().lower() if subject else ""
            if email_subject in DEFAULT_SUBJECTS:
                subject = fetch_last_email_subject(
                    agency_prospect_id=agency_db_id, session_id=session.session_id
                )
            else:
                safe_redis_operation(
                    session_broker.set,
                    last_subject_key,
                    subject,
                    ex=timedelta(days=7),
                )

            # ─── Assistant lifecycle ──────────────────────────────
            assistant = await assistant_registry.get_or_create(
                key=f"{agency_db_id}:{agency_prospect_id}:{session.session_id}",
                factory=Assistant,
                agency_id=str(agency_db_id),
                agency_prospect_id=str(agency_prospect_id),
                issued_email=issued_email,
                app_password=app_password,
                session=session,
                factory_type="sync",
            )

            await assistant.response(
                prospect_email=sender_email,
                msg=message,
                subject=subject,
                first_impression=session._cached_first_impression,
                agent_email=agent_email,
                agency_name=agency_name,
            )

        except Exception:
            logger.exception(f"[{agency_id}] 🔥 Failed | ID={email_id}")

        finally:
            stream._acknowledge(email_id)
            logger.info(f"[{agency_id}] ✅ Acknowledged | ID={email_id}")
