import json
import logging

from .prompts import REEVALUATION_SYSTEM_PROMPT

logger = logging.getLogger("Email_Evaluator")


# ────────────────────────────────────────────────────────────────
# RE-EVALUATION FUNCTION
# ────────────────────────────────────────────────────────────────
def reevaluate_email(
    sender_id: str,
    message: str,
    previous_questions: list[str],
) -> dict:
    """
    Verifies whether a follow-up message answers prior clarifying questions.
    Always returns safe JSON with guaranteed structure.
    """
    from core import call_openai_safe

    messages = [
        {"role": "system", "content": REEVALUATION_SYSTEM_PROMPT},
        {
            "role": "user",
            "content": f"""
Sender ID:
{sender_id}

Clarifying questions previously asked:
{json.dumps(previous_questions, indent=2)}

New message:
{message}
""",
        },
    ]

    # Define safe fallback
    fallback = {
        "resolution": "unresolved",
        "category": "unknown",
        "confidence": 0.0,
        "reason": "LLM re-evaluation failed",
        "answered_questions": [],
        "missing_questions": previous_questions,
    }

    try:
        logger.debug("🔁 Running RE-EVALUATION")

        reevaluation = call_openai_safe(
            messages=messages,
            temperature=0.0,
            max_tokens=500, 
            response_format="json",
            fallback_response=fallback,
        )

        # Validate structure
        if not isinstance(reevaluation, dict):
            logger.error(f"❌ Invalid reevaluation type: {type(reevaluation)}")
            return fallback

        # Ensure required fields
        return {
            "resolution": reevaluation.get("resolution", "unresolved"),
            "category": reevaluation.get("category", "unknown"),
            "confidence": float(reevaluation.get("confidence", 0.0)),
            "reason": reevaluation.get("reason", "No reason provided"),
            "answered_questions": reevaluation.get("answered_questions", []),
            "missing_questions": reevaluation.get(
                "missing_questions", previous_questions
            ),
        }

    except Exception as exc:
        logger.exception("💥 Re-evaluation crashed")
        return fallback


# ────────────────────────────────────────────────────────────────
# Clarification Email Sender
# ────────────────────────────────────────────────────────────────
def send_clarification_email(classification, sender_email):
    from core import SMTPService # Imported here to avoid circular imports
    logger.warning(f"🔁 Re-evaluation required | Sender={sender_email}")



#     if sender_email == "ahk3155262@gmail.com":
#         questions = classification.get("evaluation", {}).get("clarifying_questions", [])

#         if questions:
#             body_text = "\n".join(f"- {q}" for q in questions)
#             send_email(
#                 to=sender_email,
#                 subject="Quick clarification needed",
#                 body=body_text,
#             )
#             logger.info(f"📤 Clarifying email sent | Sender={sender_email}")
#         else:
#             logger.warning(
#                 f"⚠️ Re-eval needed but no questions available | Sender={sender_email}"
#             )
#     else:
#         logger.warning(
#             f"🚫 Clarification email blocked (unauthorized) | Sender={sender_email}"
#         )
