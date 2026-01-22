import json
import logging
from .initial_eval import evaluate_email
from .re_eval import reevaluate_email

from core import prospects_broker, safe_redis_operation

logger = logging.getLogger("Email_Evaluator")


# ────────────────────────────────────────────────────────────────
# Classifier Function
# ────────────────────────────────────────────────────────────────
PROSPECT_TTL_SECONDS = 14 * 24 * 60 * 60
TRIVIA_TTL_SECONDS = 30 * 24 * 60 * 60
NEEDS_REEVAL_TTL_SECONDS = 7 * 24 * 60 * 60
MAX_UNIDENTIFIED_ATTEMPTS = 3


def classify_sender(sender_id: str, subject: str, message: str) -> dict:
    """
    Full stateful sender classification with re-evaluation logic.
    
    CRASH-PROOF: All Redis operations and LLM calls are wrapped in safety handlers.
    Returns guaranteed safe dictionary structure.
    """
    
    # Validate inputs
    if not sender_id or not isinstance(sender_id, str):
        logger.error(f"❌ Invalid sender_id: {sender_id}")
        return {"is_prospect": False, "re_evaluation_needed": False, "error": "invalid_sender_id"}
    
    subject = subject or ""
    message = message or ""

    prospect_key = f"prospect:{sender_id}"
    trivia_key = f"trivia:{sender_id}"
    needs_reeval_key = f"needs_reeval:{sender_id}"
    attempts_key = f"attempts:{sender_id}"
    questions_key = f"questions:{sender_id}"

    try:
        # ─── Known States ─────────────────────────────
        if safe_redis_operation(prospects_broker.exists, prospect_key):
            logger.info(f"🟢 Known PROSPECT | {sender_id}")
            safe_redis_operation(prospects_broker.setex, prospect_key, PROSPECT_TTL_SECONDS, 1)
            return {"is_prospect": True, "re_evaluation_needed": False}

        if safe_redis_operation(prospects_broker.exists, trivia_key):
            logger.info(f"⚪ Known TRIVIA | {sender_id}")
            safe_redis_operation(prospects_broker.setex, trivia_key, TRIVIA_TTL_SECONDS, 1)
            return {"is_prospect": False, "re_evaluation_needed": False}

        # ─── Re-evaluation Path ──────────────────────
        if safe_redis_operation(prospects_broker.exists, needs_reeval_key):
            questions_raw = safe_redis_operation(prospects_broker.get, questions_key) or "[]"
            
            try:
                previous_questions = json.loads(questions_raw)
                if not isinstance(previous_questions, list):
                    previous_questions = []
            except json.JSONDecodeError:
                logger.error(f"❌ Corrupted questions data for {sender_id}")
                previous_questions = []

            result = reevaluate_email(sender_id, message, previous_questions)
            attempts = int(safe_redis_operation(prospects_broker.get, attempts_key) or 0) + 1

            safe_redis_operation(
                prospects_broker.setex, attempts_key, NEEDS_REEVAL_TTL_SECONDS, attempts
            )

            if result["resolution"] == "resolved" and result["category"] == "prospect":
                logger.info(f"🎯 Intent RESOLVED → PROSPECT | {sender_id}")
                safe_redis_operation(prospects_broker.setex, prospect_key, PROSPECT_TTL_SECONDS, 1)
                safe_redis_operation(prospects_broker.delete, needs_reeval_key, attempts_key, questions_key)
                return {"is_prospect": True, "re_evaluation_needed": False}

            if attempts >= MAX_UNIDENTIFIED_ATTEMPTS:
                logger.warning(f"❌ Max attempts reached → TRIVIA | {sender_id}")
                safe_redis_operation(prospects_broker.setex, trivia_key, TRIVIA_TTL_SECONDS, 1)
                safe_redis_operation(prospects_broker.delete, needs_reeval_key, attempts_key, questions_key)
                return {"is_prospect": False, "re_evaluation_needed": False}

            logger.warning(f"❔ Still unclear | {sender_id} | Attempt {attempts}")
            
            # Store missing questions for next iteration
            missing = result.get("missing_questions", previous_questions)
            safe_redis_operation(
                prospects_broker.setex,
                questions_key,
                NEEDS_REEVAL_TTL_SECONDS,
                json.dumps(missing),
            )
            return {"is_prospect": False, "re_evaluation_needed": True, "evaluation": result}

        # ─── Initial Evaluation ──────────────────────
        result = evaluate_email(sender_id, subject, message)

        if result["category"] == "prospect" and result["confidence"] >= 0.7:
            logger.info(f"💾 New PROSPECT stored | {sender_id}")
            safe_redis_operation(prospects_broker.setex, prospect_key, PROSPECT_TTL_SECONDS, 1)
            return {"is_prospect": True, "re_evaluation_needed": False}

        if result["category"] in {"spam", "promotion"}:
            logger.info(f"🚫 Archived as TRIVIA | {sender_id}")
            safe_redis_operation(prospects_broker.setex, trivia_key, TRIVIA_TTL_SECONDS, 1)
            return {"is_prospect": False, "re_evaluation_needed": False}

        # ─── Unknown → start re-evaluation ───────────
        logger.warning(f"❔ UNKNOWN intent → asking clarifying questions | {sender_id}")
        
        questions = result.get("clarifying_questions", [])
        if not questions:
            # Generate default questions if LLM failed to provide them
            questions = [
                "What is the purpose of your email?",
                "Are you interested in our products or services?",
            ]
            logger.warning(f"⚠️ Using default questions for {sender_id}")
        
        safe_redis_operation(prospects_broker.setex, needs_reeval_key, NEEDS_REEVAL_TTL_SECONDS, 1)
        safe_redis_operation(prospects_broker.setex, attempts_key, NEEDS_REEVAL_TTL_SECONDS, 1)
        safe_redis_operation(
            prospects_broker.setex,
            questions_key,
            NEEDS_REEVAL_TTL_SECONDS,
            json.dumps(questions),
        )

        return {"is_prospect": False, "re_evaluation_needed": True, "evaluation": result}
        
    except Exception as exc:
        logger.exception(f"💥 classify_sender crashed for {sender_id}")
        # Ultimate fallback - don't crash the pipeline
        return {
            "is_prospect": False,
            "re_evaluation_needed": False,
            "error": str(exc),
            "status": "system_error"
        }