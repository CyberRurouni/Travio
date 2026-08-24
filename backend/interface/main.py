import asyncio
import logging

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("AI_EMAIL_ASSISTANT")


async def main():
    from core import EmailService

    logger.info("🔧 Starting AI Email Assistant (multi-agency)")

    email_service = EmailService()

    # Starts one perpetual IMAP worker thread per agency.
    # Each worker fires a short-lived processor only when new emails arrive.
    await email_service.start_all_imap_workers()

    # Keep app alive
    await asyncio.Event().wait()


if __name__ == "__main__":
    asyncio.run(main()) 