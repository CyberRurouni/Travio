import json
from typing import Dict, Any
from core import call_openai_safe


def first_impression(
    subject: str, body: str, model="gemini-2.5-flash-lite"
) -> Dict[str, Any]:
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
4. Are ANY travel constraints clearly mentioned at a surface level?

Constraints include:
- Dates or timeframes
- Destinations
- Budget
- Number of travelers
- Travel style (luxury, family, etc.)

Rules:
- If constraints are vague or implied, mark false
- Only mark true if they are explicitly mentioned

Return JSON:
{{
  "intent": "family_trip / honeymoon / adventure_trip / business_trip / other / unknown",
  "confidence": float 0.0-1.0,
  "behavior": "exploring / hesitant / decisive / urgent / curious / unknown",
  "constraints_mentioned": true/false,
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
  "constraints_mentioned": true,
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
        model=model,
        max_tokens=450,
        response_format="json",
        fallback_response={
            "intent": "unknown",
            "confidence": 0.0,
            "behavior": "unknown",
            "constraints_mentioned": False,
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
                "constraints_mentioned": False,
                "notes": "fallback",
            }

    return result
