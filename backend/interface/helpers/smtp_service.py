import os
import smtplib
import logging
import asyncio
from email.message import EmailMessage

logger = logging.getLogger("SMTP_Mailer")


class SMTPService:
    """
    Optimized, async-friendly SMTP service.
    - Reuses a single SMTP connection.
    - Automatic reconnect on failures.
    - Retries sending messages to avoid losing emails.
    """

    def __init__(self, retries: int = 1):
        self.SMTP_HOST = os.getenv("SMTP_HOST", "smtp.gmail.com")
        self.SMTP_PORT = int(os.getenv("SMTP_PORT", "587"))
        self.SMTP_USERNAME = os.getenv("EMAIL")
        self.SMTP_PASSWORD = os.getenv("PASSWORD")
        self.FROM_EMAIL = os.getenv("FROM_EMAIL") or self.SMTP_USERNAME

        if not all([self.SMTP_HOST, self.SMTP_PORT, self.SMTP_USERNAME, self.SMTP_PASSWORD, self.FROM_EMAIL]):
            raise RuntimeError("SMTP configuration incomplete")

        self._server: smtplib.SMTP | None = None
        self._lock = asyncio.Lock()
        self._retries = retries
        logger.info("📨 SMTPService initialized")

    def _connect(self):
        """Establish SMTP connection and authenticate."""
        logger.debug("🔐 Connecting to SMTP server")
        server = smtplib.SMTP(self.SMTP_HOST, self.SMTP_PORT, timeout=20)
        server.starttls()
        server.login(self.SMTP_USERNAME, self.SMTP_PASSWORD) # type: ignore
        self._server = server
        logger.info("✅ SMTP connection established")

    def _disconnect(self):
        """Close SMTP connection gracefully."""
        if self._server:
            try:
                self._server.quit()
            except Exception:
                logger.warning("⚠️ SMTP quit failed, closing forcefully")
            finally:
                self._server = None
                logger.info("🛑 SMTP connection closed")

    def _send(self, to: str, subject: str, body: str):
        """
        Blocking send with reconnect and retry on connection failure.
        """
        if self._server is None:
            self._connect()

        msg = EmailMessage()
        msg["From"] = self.FROM_EMAIL
        msg["To"] = to
        msg["Subject"] = subject
        msg.set_content(body)

        attempt = 0
        while attempt <= self._retries:
            try:
                self._server.send_message(msg) # type: ignore
                logger.info(f"✅ Email sent successfully | To={to}")
                return
            except (smtplib.SMTPServerDisconnected, smtplib.SMTPException) as e:
                logger.warning(f"⚠️ SMTP send failed, reconnecting... | Attempt {attempt+1} | To={to}")
                self._disconnect()
                self._connect()
                attempt += 1
        # If still failed after retries
        raise RuntimeError(f"❌ Failed to send email to {to} after {self._retries+1} attempts")

    async def send_email(self, to: str, subject: str, body: str):
        """
        Async-friendly wrapper. Runs the blocking send in a thread pool.
        Serializes access to the shared SMTP connection.
        """
        async with self._lock:
            await asyncio.to_thread(self._send, to, subject, body)

