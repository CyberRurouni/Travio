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
    from core import call_openai

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
    }

    try:
        logger.debug("🧠 Running INITIAL evaluation")
        
        evaluation = call_openai.blocking(
            messages=messages,
            temperature=0.0,
            max_tokens=500,
            increment=100,
            fallback=fallback,
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
        }

    except Exception as exc:
        logger.exception("💥 Initial evaluation crashed")
        return fallback
