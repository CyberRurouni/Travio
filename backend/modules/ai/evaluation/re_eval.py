import json
import logging
from .prompts import REEVALUATION_SYSTEM_PROMPT

logger = logging.getLogger("Email_Evaluator")


# ────────────────────────────────────────────────────────────────
# RE-EVALUATION FUNCTION
# ────────────────────────────────────────────────────────────────
async def reevaluate_email(
    sender_id: str,
    conversation_context: list[dict],  # Full history: [{role, content}, ...]
    attempt: int,
) -> dict:
    """
    Stateful re-evaluator.
    - Stores the prospect's message into context.
    - Asks friendly clarifying questions via email.
    - Stores its own response into context for next round.
    - Returns resolution verdict + updated context.
    """
    from core import call_openai_safe, get_smtp_service

    messages = [
        {"role": "system", "content": REEVALUATION_SYSTEM_PROMPT},
        *conversation_context,
    ]

    logger.info(f"🔁 Starting re-evaluation | attempt={attempt} | {sender_id}")
    logger.info(
        f"📜 Conversation context for re-evaluation | {json.dumps(conversation_context, indent=2)}"
    )

    fallback = {
        "resolution": "unresolved",
        "category": "unknown",
        "confidence": 0.0,
        "reason": "LLM re-evaluation failed",
        "friendly_reply": None,
        "updated_context": conversation_context,
    }

    try:
        logger.debug(f"🔁 Running RE-EVALUATION | attempt={attempt} | {sender_id}")

        reevaluation = call_openai_safe(
            messages=messages,
            temperature=0.2,
            max_tokens=600,
            response_format="json",
            fallback_response=fallback,
        )

        if not isinstance(reevaluation, dict):
            logger.error(f"❌ Invalid reevaluation type: {type(reevaluation)}")
            return fallback

        resolution = reevaluation.get("resolution", "unresolved")
        category = reevaluation.get("category", "unknown")
        friendly_reply = reevaluation.get(
            "friendly_reply"
        )  # The email body to send back

        # Append assistant's reply to context for next round
        if friendly_reply:
            conversation_context = conversation_context + [
                {"role": "assistant", "content": friendly_reply}
            ]

        # Send clarification email if not resolved
        if resolution != "resolved" and friendly_reply:
            try:
                smtp = await get_smtp_service()
                await smtp.send_email(
                    to=sender_id,
                    subject="Re: Your message",
                    body=friendly_reply,
                )
                logger.info(
                    f"📨 Clarification email sent | {sender_id} | attempt={attempt}"
                )
            except Exception:
                logger.exception(f"⚠️ Failed to send clarification email | {sender_id}")

        return {
            "resolution": resolution,
            "category": category,
            "confidence": float(reevaluation.get("confidence", 0.0)),
            "reason": reevaluation.get("reason", "No reason provided"),
            "friendly_reply": friendly_reply,
            "updated_context": conversation_context,
        }

    except Exception:
        logger.exception("💥 Re-evaluation crashed")
        return fallback
