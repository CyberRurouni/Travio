import asyncio
import logging

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("AI_EMAIL_ASSISTANT")


async def main():
    from core import EmailService

    logger.info("🔧 Starting AI Email Assistant")

    email_service = EmailService()

    await email_service.start_imap_worker()

    # run redis consumer as background task
    asyncio.create_task(
        email_service.perpetual_email_processor(),
        name="redis-consumer"
    )

    # keep app alive forever
    await asyncio.Event().wait()


if __name__ == "__main__":
    asyncio.run(main())