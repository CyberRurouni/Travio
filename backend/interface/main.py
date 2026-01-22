import asyncio
import logging

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("AI_EMAIL_ASSISTANT")


async def main():
    from core import realtime_followup_handler, EmailService

    logger.info("🔧 Starting AI Email Assistant")

    email_service = EmailService()

    # Start IMAP worker once (thread)
    await email_service.start_imap_worker()

    await asyncio.gather(
        realtime_followup_handler(),                 # async websocket
        email_service.perpetual_email_processor(),   # async redis consumer
    )


if __name__ == "__main__":
    asyncio.run(main())


