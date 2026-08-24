import json
from typing import Dict, Any
from core import call_openai

def classify_intent(context: list[dict]) -> Dict[str, Any]:
    """
    Determines authoritative intent and whether constraints are present in context.
    """

    prompt = f"""
You are an AI travel analyst embedded in a sales-oriented travel assistant.

Your task:
1. Identify the primary travel intent
2. Assess clarity and maturity of intent
3. Decide conversation layer (GENERAL vs INTENT)

Return JSON:
{{
  "intent": "family_trip / honeymoon / adventure_trip / business_trip / other / unknown",
  "confidence": float (0.0 - 1.0),
  "conversation_layer": "GENERAL / INTENT",
  "notes": "short explanation"
}}

Examples:

Example:
Conversation:
"We want to book a honeymoon trip to Bali this December."

Output:
{{
  "intent": "honeymoon",
  "confidence": 0.9,
  "conversation_layer": "INTENT",
  "notes": "Destination and timeframe are clearly specified"
}}

Now analyze:

\"\"\"{context}\"\"\"
"""

    result = call_openai.blocking(
        messages=[{"role": "user", "content": prompt}],
        max_tokens=450,
        increment=100,
        fallback={
            "intent": "unknown",
            "confidence": 0.0,
            "conversation_layer": "GENERAL",
            "notes": "fallback"
        }
    )

    if isinstance(result, str):
        try:
            result = json.loads(result)
        except json.JSONDecodeError:
            result = {
                "intent": "unknown",
                "confidence": 0.0,
                "conversation_layer": "GENERAL",
                "notes": "fallback"
            }

    return result


