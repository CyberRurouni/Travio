import logging
from typing import Dict, Any, Optional

from .components.first_impression import first_impression
from .components.intent_classifier import classify_intent
from .components.constraint_extractor import extract_constraints
from .components.intent_drift_detector import detect_intent_drift
from .components.scam_detector import detect_scam

# 🛡️ Configure logger
logger = logging.getLogger("INTENT_GUARD")
logger.setLevel(logging.INFO)


class TravelIntentGuard:
    """
    ✈️ TravelIntentGuard
    -------------------
    Central brain for analyzing travel conversations:
    - Scam detection
    - Intent understanding
    - Constraint extraction
    - Intent drift handling
    """

    @staticmethod
    def analyze_conversation(
        subject: str,
        latest_msg: str,
        chat_context: list[Dict],
        previous_intent: Optional[str] = None,
        conversation_layer: str = "GENERAL",
        is_first_message: bool = False
    ) -> Dict[str, Any]:

        logger.info("🚀 Starting conversation analysis")

        result: Dict[str, Any] = {}

        # 🔍 0️⃣ Scam detection (always on, non-blocking)
        logger.info("🕵️ Running scam detection")
        result["scam"] = detect_scam(latest_msg)

        # 🌟 1️⃣ First message → First Impression
        if is_first_message:
            logger.info("🌟 First message detected → running first impression analysis")

            fi = first_impression(subject, latest_msg)
            result["first_impression"] = fi

            if fi.get("constraints_mentioned"):
                logger.info("📌 Constraints mentioned in first message")
                result["constraints"] = extract_constraints(latest_msg)

            if fi["confidence"] >= 0.9:
                logger.info(
                    "🎯 High-confidence intent detected (%s | %.2f)",
                    fi["intent"],
                    fi["confidence"]
                )
                result["intent"] = {
                    "intent": fi["intent"],
                    "confidence": fi["confidence"],
                    "conversation_layer": "INTENT",
                    "source": "first_impression"
                }
            else:
                logger.info("🤔 Low confidence intent → staying in GENERAL layer")
                result["conversation_layer"] = "GENERAL"

            logger.info("✅ First message analysis completed")
            return result

        # 🔄 2️⃣ INTENT layer → Drift detection
        if conversation_layer == "INTENT" and previous_intent:
            logger.info(
                "🔄 INTENT layer active → checking drift from '%s'",
                previous_intent
            )

            drift = detect_intent_drift(latest_msg, previous_intent)
            result["intent_drift"] = drift

            if drift.get("constraints_mentioned"):
                logger.info("📌 New constraints detected during drift check")
                result["constraints"] = extract_constraints(latest_msg)

            if drift["intent_changed"]:
                logger.warning("⚠️ Intent drift detected → reverting to GENERAL layer")
                result["conversation_layer"] = "GENERAL"
            else:
                logger.info("🧭 No intent drift detected")

            return result

        # 🧠 3️⃣ GENERAL layer → Authoritative intent classification
        logger.info("🧠 GENERAL layer → running intent classification")

        intent = classify_intent(chat_context)
        result["intent"] = intent
        result["conversation_layer"] = intent["conversation_layer"]

        if intent.get("constraints_mentioned"):
            logger.info("📌 Constraints detected during intent classification")
            result["constraints"] = extract_constraints(latest_msg)

        logger.info(
            "🏁 Analysis completed → Layer: %s | Intent: %s",
            result.get("conversation_layer"),
            intent.get("intent")
        )

        return result

