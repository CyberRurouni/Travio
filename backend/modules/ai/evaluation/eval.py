import json
import logging
from .initial_eval import evaluate_email
from .re_eval import reevaluate_email

logger = logging.getLogger("Email_Evaluator")


# ────────────────────────────────────────────────────────────────
# Classifier Function
# ────────────────────────────────────────────────────────────────
PROSPECT_TTL_SECONDS = 14 * 24 * 60 * 60
TRIVIA_TTL_SECONDS = 30 * 24 * 60 * 60
NEEDS_REEVAL_TTL_SECONDS = 7 * 24 * 60 * 60
MAX_UNIDENTIFIED_ATTEMPTS = 3


async def classify_sender(
    agency_id: str,
    sender_id: str,
    subject: str,
    message: str,
    issued_email: str,
    app_password: str,
) -> dict:
    """
    Full stateful sender classification with re-evaluation logic.
    Classification caches are scoped per-agency so a sender is only ever
    remembered as prospect/trivia in the context of one agency.
    """
    from core import (
        is_registered_prospect_by_email,
        prospects_broker,
        safe_redis_operation,
    )

    if not sender_id or not isinstance(sender_id, str):
        logger.error(f"❌ Invalid sender_id: {sender_id}")
        return {
            "is_prospect": False,
            "re_evaluation_needed": False,
            "error": "invalid_sender_id",
        }

    subject = subject or ""
    message = message or ""

    prospect_key = f"prospect:{agency_id}:{sender_id}"
    trivia_key = f"trivia:{agency_id}:{sender_id}"
    needs_reeval_key = f"needs_reeval:{agency_id}:{sender_id}"
    attempts_key = f"attempts:{agency_id}:{sender_id}"
    context_key = (
        f"context:{agency_id}:{sender_id}"  # Stores conversation history for reevaluate_email
    )

    try:
        # ─── Known States (Redis First) ──────────────────────────────
        if safe_redis_operation(prospects_broker.exists, prospect_key):
            logger.info(f"🟢 Known PROSPECT (Redis) | {sender_id}")
            safe_redis_operation(
                prospects_broker.setex, prospect_key, PROSPECT_TTL_SECONDS, 1
            )
            return {"is_prospect": True, "re_evaluation_needed": False}

        if safe_redis_operation(prospects_broker.exists, trivia_key):
            logger.info(f"⚪ Known TRIVIA | {sender_id}")
            safe_redis_operation(
                prospects_broker.setex, trivia_key, TRIVIA_TTL_SECONDS, 1
            )
            return {"is_prospect": False, "re_evaluation_needed": False}

        # ─── Hard DB Check (Fallback) ─────────────────────────────────
        try:
            prospect_id = await is_registered_prospect_by_email(sender_id)
            if prospect_id:
                logger.info(f"🟢 DB Registered PROSPECT | {sender_id}")
                # Cache it so DB is not hit again
                safe_redis_operation(
                    prospects_broker.setex, prospect_key, PROSPECT_TTL_SECONDS, 1
                )
                return {"is_prospect": True, "re_evaluation_needed": False}
        except Exception:
            logger.exception("⚠️ DB check failed — continuing classification")

        # ─── Re-evaluation Path (unknown senders mid-conversation) ────
        if safe_redis_operation(prospects_broker.exists, needs_reeval_key):
            attempts = int(
                safe_redis_operation(prospects_broker.get, attempts_key) or 1
            )

            # Load stored conversation context
            context_raw = (
                safe_redis_operation(prospects_broker.get, context_key) or "[]"
            )
            try:
                conversation_context = json.loads(context_raw)
                if not isinstance(conversation_context, list):
                    conversation_context = []
            except json.JSONDecodeError:
                logger.error(f"❌ Corrupted context for {sender_id}")
                conversation_context = []

            conversation_context = conversation_context + [
                {"role": "user", "content": message}
            ]

            result = await reevaluate_email(
                sender_id=sender_id,
                conversation_context=conversation_context,
                attempt=attempts,
                issued_email=issued_email,
                app_password=app_password,
            )

            # Update attempts
            new_attempts = attempts + 1
            safe_redis_operation(
                prospects_broker.setex,
                attempts_key,
                NEEDS_REEVAL_TTL_SECONDS,
                new_attempts,
            )

            # Store updated context (reevaluate_email appends to it)
            updated_context = result.get("updated_context", conversation_context)
            safe_redis_operation(
                prospects_broker.setex,
                context_key,
                NEEDS_REEVAL_TTL_SECONDS,
                json.dumps(updated_context),
            )

            if result["resolution"] == "resolved" and result["category"] == "prospect":
                logger.info(f"🎯 Intent RESOLVED → PROSPECT | {sender_id}")
                safe_redis_operation(
                    prospects_broker.setex, prospect_key, PROSPECT_TTL_SECONDS, 1
                )
                safe_redis_operation(
                    prospects_broker.delete, needs_reeval_key, attempts_key, context_key
                )
                return {"is_prospect": True, "re_evaluation_needed": False}

            if result["category"] in {"spam", "promotion"}:
                logger.warning(
                    f"🚫 Reevaluation detected spam/promo → TRIVIA | {sender_id}"
                )
                safe_redis_operation(
                    prospects_broker.setex, trivia_key, TRIVIA_TTL_SECONDS, 1
                )
                safe_redis_operation(
                    prospects_broker.delete, needs_reeval_key, attempts_key, context_key
                )
                return {"is_prospect": False, "re_evaluation_needed": False}

            if new_attempts > MAX_UNIDENTIFIED_ATTEMPTS:
                logger.warning(f"❌ Max attempts reached → TRIVIA | {sender_id}")
                safe_redis_operation(
                    prospects_broker.setex, trivia_key, TRIVIA_TTL_SECONDS, 1
                )
                safe_redis_operation(
                    prospects_broker.delete, needs_reeval_key, attempts_key, context_key
                )
                return {"is_prospect": False, "re_evaluation_needed": False}

            logger.warning(f"❔ Still unclear | {sender_id} | Attempt {new_attempts}")
            return {
                "is_prospect": False,
                "re_evaluation_needed": True,
                "evaluation": result,
            }

        # ─── Initial Evaluation ───────────────────────────────────────
        result = evaluate_email(sender_id, subject, message)

        if result["category"] == "prospect" and result["confidence"] >= 0.7:
            logger.info(f"💾 New PROSPECT stored | {sender_id}")
            safe_redis_operation(
                prospects_broker.setex, prospect_key, PROSPECT_TTL_SECONDS, 1
            )
            return {"is_prospect": True, "re_evaluation_needed": False}

        if result["category"] in {"spam", "promotion"}:
            logger.info(f"🚫 Archived as TRIVIA | {sender_id}")
            safe_redis_operation(
                prospects_broker.setex, trivia_key, TRIVIA_TTL_SECONDS, 1
            )
            return {"is_prospect": False, "re_evaluation_needed": False}

        # ─── Unknown → Enter re-evaluation loop ──────────────────────
        logger.warning(f"❔ UNKNOWN intent → entering re-evaluation | {sender_id}")

        # Seed conversation with the original message as context
        initial_context = [
            {"role": "user", "content": f"Subject: {subject}\n\n{message}"}
        ]

        result = await reevaluate_email(
            sender_id=sender_id,
            conversation_context=initial_context,
            attempt=1,
            issued_email=issued_email,
            app_password=app_password,
        )

        updated_context = result.get("updated_context", initial_context)

        # Store re-evaluation state
        safe_redis_operation(
            prospects_broker.setex, needs_reeval_key, NEEDS_REEVAL_TTL_SECONDS, 1
        )
        safe_redis_operation(
            prospects_broker.setex, attempts_key, NEEDS_REEVAL_TTL_SECONDS, 1
        )
        safe_redis_operation(
            prospects_broker.setex,
            context_key,
            NEEDS_REEVAL_TTL_SECONDS,
            json.dumps(updated_context),
        )

        # Edge case: reevaluate resolved on first pass
        if result["resolution"] == "resolved" and result["category"] == "prospect":
            logger.info(f"🎯 Immediately RESOLVED → PROSPECT | {sender_id}")
            safe_redis_operation(
                prospects_broker.setex, prospect_key, PROSPECT_TTL_SECONDS, 1
            )
            safe_redis_operation(
                prospects_broker.delete, needs_reeval_key, attempts_key, context_key
            )
            return {"is_prospect": True, "re_evaluation_needed": False}

        return {
            "is_prospect": False,
            "re_evaluation_needed": True,
            "evaluation": result,
        }

    except Exception as exc:
        logger.exception(f"💥 classify_sender crashed for {sender_id}")
        return {
            "is_prospect": False,
            "re_evaluation_needed": False,
            "error": str(exc),
            "status": "system_error",
        }
