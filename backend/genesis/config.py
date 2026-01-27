import json
import hashlib
import os
import time
import imaplib  # Built-in IMAP library, fully compatible with Python 3.14
import email  # Built-in library for parsing email messages
from logging import getLogger, basicConfig, Formatter, StreamHandler
from email import utils  # For parsing email addresses
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
EMAIL = os.getenv("EMAIL") or ""
PASSWORD = os.getenv("PASSWORD") or ""

IMAP_SERVER = "imap.gmail.com"  # Gmail IMAP server
IDLE_TIMEOUT = 60 * 25  # 25 minutes; Gmail requires exiting IDLE periodically

OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY") or ""

# ----------------------------
# Initialization of OpenAI client
# ----------------------------
client = OpenAI(
    api_key=OPENROUTER_API_KEY,
    base_url="https://openrouter.ai/api/v1",
)

# ----------------------------
# Supabase client setup
# ----------------------------
SB_URL = os.getenv("SB_URL") or ""
SB_SERVICE_ROLE_KEY = os.getenv("SB_SERVICE_ROLE_KEY") or ""

supabase = create_client(SB_URL, SB_SERVICE_ROLE_KEY)

async_supabase = None


async def get_async_supabase():
    global async_supabase
    if async_supabase is None:
        async_supabase = await create_async_client(
            SB_URL,
            SB_SERVICE_ROLE_KEY,
        )
        return async_supabase


# ----------------------------
# Function to fetch and process unread emails
# ----------------------------
def scanning(mail):
    """
    RESPONSIBILITY:
    - Fetch unread emails from IMAP
    - Normalize into email_event
    - Publish to Redis
    - Mark as read
    - Deduplicate using Message-ID or content hash
    """

    from core import (
        safe_redis_operation,
        emails_broker,
        emails_stream,
    )  # Avoid circular import

    mail.select("INBOX")

    status, data = mail.search(None, "UNSEEN")
    if status != "OK":
        logger.error("⚠️ Failed to search emails.")
        return

    email_ids = data[0].split()
    if not email_ids:
        logger.info("📭 No new emails found.")
        return

    logger.info(f"📬 Found {len(email_ids)} unread email(s).")

    for eid in email_ids:
        try:
            status, msg_data = mail.fetch(eid, "(RFC822)")
            if status != "OK":
                logger.error(f"⚠️ Failed to fetch email UID {eid}")
                continue

            raw_email = msg_data[0][1]
            msg = email.message_from_bytes(raw_email)

            sender_name, sender_email = utils.parseaddr(msg.get("From") or "")
            subject = msg.get("Subject") or "(No Subject)"
            message_id = msg.get("Message-ID")
            body = ""

            # Extract plain-text body only
            if msg.is_multipart():
                for part in msg.walk():
                    if part.get_content_type() == "text/plain" and not part.get(
                        "Content-Disposition"
                    ):
                        payload = part.get_payload(decode=True)
                        if isinstance(payload, bytes):
                            body = payload.decode(
                                part.get_content_charset() or "utf-8",
                                errors="replace",
                            )
                        break
            else:
                payload = msg.get_payload(decode=True)
                if isinstance(payload, bytes):
                    body = payload.decode(
                        msg.get_content_charset() or "utf-8",
                        errors="replace",
                    )
                else:
                    body = payload or ""

            body = body.strip() if isinstance(body, str) else ""

            # Deduplication key: Message-ID or fallback hash
            if message_id:
                seen_msg = f"email:seen:{message_id}"
            else:
                # Fallback hash of sender + subject + body
                hash_input = f"{sender_email}|{subject}|{body}".encode()
                dedup_hash = hashlib.sha256(hash_input).hexdigest()
                seen_msg = f"email:seen:{dedup_hash}"

            # Check if email is already processed
            if safe_redis_operation(emails_broker.exists, seen_msg):
                logger.info(f"🛑 Skipping duplicate email (UID={eid})")
                # Still mark as read to avoid refetch
                mail.store(eid, "+FLAGS", "\\Seen")
                continue

            # Mark seen_msg in Redis with 1 hr expiration
            safe_redis_operation(emails_broker.set, seen_msg, 1, ex=3600)

            # Construct email_event
            email_event = {
                "sender_name": sender_name,
                "sender_email": sender_email,
                "subject": subject,
                "body": body,
                "message_id": message_id,
                "received_at": time.time(),
            }

            # Publish to Redis (fire-and-forget)
            emails_stream.publish(json.dumps(email_event))

            logger.info(f"📨 Published email from {sender_email} to Redis (UID={eid})")

            # Mark as read ONLY after successful publish
            mail.store(eid, "+FLAGS", "\\Seen")

        except Exception as e:
            logger.error(f"🔥 Failed to process email UID {eid}: {e}", exc_info=True)


# ----------------------------
# Function to enter IDLE mode
# ----------------------------
def idle(mail):
    """
    Enters IMAP IDLE mode and waits for mailbox changes
    or timeout, then exits cleanly.
    """
    tag = mail._new_tag()
    mail.send(f"{tag.decode()} IDLE\r\n".encode())

    # Server must acknowledge IDLE
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

    # 🚪 Exit IDLE
    mail.send(b"DONE\r\n")

    # 🧹 CRITICAL: Drain tagged OK response
    while True:
        resp = mail.readline()
        if resp.startswith(tag):
            logger.info("🟢 IDLE exited cleanly")
            break


# ----------------------------
# Persistent IDLE loop
# ----------------------------
def fetch_unread_emails():
    """
    Persistent IMAP worker.
    - Keeps one IMAP connection alive
    - Enters IDLE
    - Scans on mailbox change or timeout
    - Reconnects on failure
    """
    while True:
        mail = None
        try:
            mail = imaplib.IMAP4_SSL(IMAP_SERVER)
            mail.login(EMAIL, PASSWORD)
            mail.select("INBOX")

            logger.info("🤖 AI assistant online. Waiting for emails...")
            while True:
                idle(mail)  # Block until change or timeout
                scanning(mail)  # Fetch + publish

        except Exception as e:
            logger.error(f"🔥 IMAP error: {e}. Reconnecting in 5s...")

        finally:
            try:
                if mail:
                    mail.logout()
            except Exception:
                pass

            time.sleep(5)
