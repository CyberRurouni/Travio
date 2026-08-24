import json
import hashlib
import os
import time
import imaplib
import email
from typing import Optional, Callable
from logging import getLogger, basicConfig, Formatter, StreamHandler
from email import utils
from dotenv import load_dotenv
from openai import OpenAI
from supabase import create_client, create_async_client

# ----------------------------
# Logger setup
# ----------------------------
basicConfig(level="INFO")
logger = getLogger("CONFIG")
formatter = Formatter("%(asctime)s | %(levelname)s | %(message)s", "%Y-%m-%d %H:%M:%S")
handler = StreamHandler()
handler.setFormatter(formatter)
logger.addHandler(handler)

# ----------------------------
# Environment variables
# ----------------------------
load_dotenv()

IMAP_SERVER = "imap.gmail.com"
IDLE_TIMEOUT = 60 * 25  # 25 minutes

OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY") or ""

# ----------------------------
# OpenAI / OpenRouter client
# ----------------------------
client = OpenAI(
    api_key=OPENROUTER_API_KEY,
    base_url="https://openrouter.ai/api/v1",
)

# ----------------------------
# AI client (streaming + non-streaming)
# Single shared instance used throughout the application.
# ----------------------------
from core.utils.ai_utils.client import AIClient  # noqa: E402  (module-level deps only)

call_openai = AIClient()

# ----------------------------
# Supabase clients
# ----------------------------
SB_URL = os.getenv("SB_URL") or ""
SB_SERVICE_ROLE_KEY = os.getenv("SB_SERVICE_ROLE_KEY") or ""

supabase = create_client(SB_URL, SB_SERVICE_ROLE_KEY)

async_supabase = None


async def get_async_supabase():
    global async_supabase
    if async_supabase is None:
        async_supabase = await create_async_client(SB_URL, SB_SERVICE_ROLE_KEY)
        return async_supabase


# ----------------------------
# Per-agency IMAP scanning
# ----------------------------
def scanning(mail, agency: dict) -> int:
    """
    Fetch unread emails for a specific agency and publish
    to that agency's Redis stream.

    Returns the count of newly published emails so the caller
    knows whether to trigger the processor.
    """
    from core import (
        safe_redis_operation,
        emails_broker,
        get_or_create_agency_email_stream,
    )

    agency_id = str(agency["id"])
    stream = get_or_create_agency_email_stream(agency_id)

    mail.select("INBOX")
    status, data = mail.search(None, "UNSEEN")
    if status != "OK":
        logger.error(f"[{agency_id}] ⚠️ Failed to search emails.")
        return 0

    email_ids = data[0].split()
    if not email_ids:
        logger.info(f"[{agency_id}] 📭 No new emails.")
        return 0

    logger.info(f"[{agency_id}] 📬 Found {len(email_ids)} unread email(s).")

    published = 0

    for eid in email_ids:
        try:
            status, msg_data = mail.fetch(eid, "(RFC822)")
            if status != "OK":
                logger.error(f"[{agency_id}] ⚠️ Failed to fetch email UID {eid}")
                continue

            raw_email = msg_data[0][1]
            msg = email.message_from_bytes(raw_email)

            sender_name, sender_email = utils.parseaddr(msg.get("From") or "")
            subject = msg.get("Subject") or "(No Subject)"
            message_id = msg.get("Message-ID")
            body = ""

            if msg.is_multipart():
                for part in msg.walk():
                    if part.get_content_type() == "text/plain" and not part.get(
                        "Content-Disposition"
                    ):
                        payload = part.get_payload(decode=True)
                        if isinstance(payload, bytes):
                            body = payload.decode(
                                part.get_content_charset() or "utf-8", errors="replace"
                            )
                        break
            else:
                payload = msg.get_payload(decode=True)
                if isinstance(payload, bytes):
                    body = payload.decode(
                        msg.get_content_charset() or "utf-8", errors="replace"
                    )
                else:
                    body = payload or ""

            body = body.strip() if isinstance(body, str) else ""

            # Dedup key scoped to agency
            if message_id:
                seen_key = f"email:seen:{agency_id}:{message_id}"
            else:
                hash_input = f"{sender_email}|{subject}|{body}".encode()
                dedup_hash = hashlib.sha256(hash_input).hexdigest()
                seen_key = f"email:seen:{agency_id}:{dedup_hash}"

            if safe_redis_operation(emails_broker.exists, seen_key):
                logger.info(f"[{agency_id}] 🛑 Duplicate email skipped (UID={eid})")
                mail.store(eid, "+FLAGS", "\\Seen")
                continue

            safe_redis_operation(emails_broker.set, seen_key, 1, ex=3600)

            email_event = {
                "agency_id": agency_id,
                "issued_email": agency["issued_email"],
                "agent_email": agency["agent_email"],
                "sender_name": sender_name,
                "sender_email": sender_email,
                "subject": subject,
                "body": body,
                "message_id": message_id,
                "received_at": time.time(),
            }

            stream.publish(json.dumps(email_event))
            published += 1

            logger.info(f"[{agency_id}] 📨 Published email from {sender_email}")

            # Mark as read ONLY after successful publish
            mail.store(eid, "+FLAGS", "\\Seen")

        except Exception as e:
            logger.error(
                f"💥 Error processing email UID {eid}: {e} for agency {agency_id}",
                exc_info=True,
            )

    return published


# ----------------------------
# IMAP IDLE
# ----------------------------
def idle(mail):
    tag = mail._new_tag()
    mail.send(f"{tag.decode()} IDLE\r\n".encode())
    resp = mail.readline()
    if not resp.startswith(b"+"):
        raise RuntimeError(f"❌ IDLE not acknowledged: {resp}")

    logger.info("😴 IDLE started")
    start = time.time()
    while time.time() - start < IDLE_TIMEOUT:
        resp = mail.readline()
        if resp.startswith(b"* "):
            logger.info("⚡ Mailbox change detected!")
            break

    mail.send(b"DONE\r\n")
    while True:
        resp = mail.readline()
        if resp.startswith(tag):
            logger.info("🟢 IDLE exited cleanly")
            break


# ----------------------------
# Per-agency persistent IMAP worker
# ----------------------------
def fetch_unread_emails_for_agency(agency: dict, on_new_emails: Callable):
    """
    Perpetual IMAP worker for a single agency. Runs 24/7 in its own thread.

    Flow per cycle:
      1. IDLE — blocks until mailbox event or 25-min timeout (zero CPU)
      2. scanning() — fetches unseen emails, publishes to Redis, returns count
      3. If count > 0 and on_new_emails is set → fires the callback
         so a short-lived processor is triggered on the async event loop.

    The processor is only triggered when there are actual emails to handle.
    Empty scans (e.g. timeout-only cycles with no new mail) are silent.
    """
    agency_id = str(agency["id"])
    email_addr = agency["issued_email"]
    password = agency["app_password"]  # decrypted app password from Vault

    while True:
        mail = None
        try:
            mail = imaplib.IMAP4_SSL(IMAP_SERVER)
            mail.login(email_addr, password)
            mail.select("INBOX")

            logger.info(f"[{agency_id}] 🤖 IMAP online for {email_addr}")

            while True:
                idle(mail)
                published = scanning(mail, agency)

                # Only trigger processor when emails were actually published
                if published > 0 and on_new_emails:
                    on_new_emails(agency_id)

        except Exception as e:
            logger.error(f"[{agency_id}] 🔥 IMAP error: {e}. Reconnecting in 5s...")

        finally:
            try:
                if mail:
                    mail.logout()
            except Exception:
                pass
            time.sleep(5)
