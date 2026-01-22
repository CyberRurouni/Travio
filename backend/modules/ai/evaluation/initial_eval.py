import logging
from .prompts import SYSTEM_PROMPT

logger = logging.getLogger("Email_Evaluator")

# ────────────────────────────────────────────────────────────────
# EVALUATION FUNCTIONS
# ────────────────────────────────────────────────────────────────
def evaluate_email(sender_id: str, subject: str, message: str) -> dict:
    """
    Initial intent classification.
    Always returns safe JSON with guaranteed structure.
    """
    from core import call_openai_safe

    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {
            "role": "user",
            "content": f"""
Sender ID:
{sender_id}

Subject:
{subject}

Message:
{message}
""",
        },
    ]

    # Define safe fallback
    fallback = {
        "category": "unknown",
        "confidence": 0.0,
        "reason": "LLM evaluation failed - defaulting to unknown",
        "clarifying_questions": [
            "What is the purpose of your message?",
            "Are you interested in our services?",
        ],
    }

    try:
        logger.debug("🧠 Running INITIAL evaluation")
        
        # CRITICAL FIX: Increase max_tokens to prevent truncation
        evaluation = call_openai_safe(
            messages=messages,
            model="gemini-2.5-flash-lite",
            temperature=0.0,
            max_tokens=500,  
            response_format="json",
            fallback_response=fallback,
        )

        # Validate structure
        if not isinstance(evaluation, dict):
            logger.error(f"❌ Invalid evaluation type: {type(evaluation)}")
            return fallback

        # Ensure required fields with safe defaults
        return {
            "category": evaluation.get("category", "unknown"),
            "confidence": float(evaluation.get("confidence", 0.0)),
            "reason": evaluation.get("reason", "No reason provided"),
            "clarifying_questions": evaluation.get("clarifying_questions", []),
        }

    except Exception as exc:
        logger.exception("💥 Initial evaluation crashed")
        return fallback
