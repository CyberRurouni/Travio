from datetime import timedelta
from .response import generate_ai_response


async def get_smtp_service():
    from core import InstanceRegistry, SMTPService
    smtp_registry = InstanceRegistry(ttl=timedelta(hours=6))
    return await smtp_registry.get_or_create(
        key="smtp_service",
        factory=SMTPService,
        factory_type="sync",
    )


async def regenerate_and_send(
    assistant,
    smtp_service,
    prospect_email,
    sender,
    intent_guard_data,
    extra_context=None,
):
    updated_history, _ = await assistant.session.chat_container(
        text="", retrieve=True
    )

    updated_history_clean = [
        {"sender": m.get("sender"), "text": m.get("text")}
        for m in updated_history
    ]

    final_ai_result = generate_ai_response(
        chat_container=updated_history_clean,
        intent_guard_data=intent_guard_data,
    )

    final_public = final_ai_result.get("public_message", "")
    final_internal = final_ai_result.get("internal_note", "")

    if final_public:
        await smtp_service.send_email(
            to=prospect_email,
            subject="Re: Your inquiry",
            body=final_public,
        )
        await assistant.session.chat_container(
            text=final_public, sender=sender
        )

    if final_internal:
        await assistant.session.chat_container(
            text=final_internal, sender="system"
        )
