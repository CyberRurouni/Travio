import json
from typing import Dict, Any
from core import call_openai_safe


def first_impression(subject: str, body: str) -> Dict[str, Any]:
    """
    Produces a FIRST-IMPRESSION assessment of travel intent and behavior.
    Shallow scan only. Also flags whether constraints are visibly mentioned.
    """

    prompt = f"""
You are a travel agent quickly skimming an email for the FIRST time.

This is a FIRST IMPRESSION ONLY.
Do NOT overthink.
Do NOT infer long-term commitment.
Do NOT assume booking readiness unless explicitly stated.

Your task:
1. What does this email *seem* to be about?
2. How clear is the intent at first glance?
3. What is the apparent behavior?

Return JSON:
{{
  "intent": "family_trip / honeymoon / adventure_trip / business_trip / other / unknown",
  "confidence": float 0.0-1.0,
  "behavior": "exploring / hesitant / decisive / urgent / curious / unknown",
  "notes": "brief first-impression reasoning"
}}

Examples:

Example:
Subject: "Family trip to Italy"
Body: "We want to travel to Italy in July with our kids."

Output:
{{
  "intent": "family_trip",
  "confidence": 0.9,
  "behavior": "exploring",
  "notes": "Destination and dates are explicitly mentioned"
}}

Now analyze:

Subject:
\"\"\"{subject}\"\"\"

Body:
\"\"\"{body}\"\"\"
"""

    result = call_openai_safe(
        messages=[{"role": "user", "content": prompt}],
        max_tokens=450,
        response_format="json",
        fallback_response={
            "intent": "unknown",
            "confidence": 0.0,
            "behavior": "unknown",
            "notes": "fallback",
        },
    )

    if isinstance(result, str):
        try:
            result = json.loads(result)
        except json.JSONDecodeError:
            result = {
                "intent": "unknown",
                "confidence": 0.0,
                "behavior": "unknown",
                "notes": "fallback",
            }

    return result
