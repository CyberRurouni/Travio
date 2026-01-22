import json
from typing import Dict, Any
from core import call_openai_safe

def detect_intent_drift(body: str, previous_intent: str, model="gemini-2.5-flash-lite") -> Dict[str, Any]:
    prompt = f"""
You are a travel agent AI assistant.

Previous intent: "{previous_intent}"

Analyze the latest message to determine:
1. Has the intent changed?
2. If yes, what is the new intent?
3. Are any NEW travel constraints mentioned?

Constraints include:
- Dates
- Destination
- Budget
- Travelers
- Travel style

Return JSON:
{{
  "intent_changed": true/false,
  "new_intent": "string or null",
  "confidence": float 0.0-1.0,
  "constraints_mentioned": true/false,
  "notes": "observations"
}}

Email body:
\"\"\"{body}\"\"\"
"""

    result = call_openai_safe(
        messages=[{"role": "user", "content": prompt}],
        model=model,
        max_tokens=350,
        response_format="json",
        fallback_response={
            "intent_changed": False,
            "new_intent": None,
            "confidence": 0.0,
            "constraints_mentioned": False,
            "notes": "fallback",
        },
    )

    if isinstance(result, str):
        try:
            result = json.loads(result)
        except json.JSONDecodeError:
            result = {
                "intent_changed": False,
                "new_intent": None,
                "confidence": 0.0,
                "constraints_mentioned": False,
                "notes": "fallback",
            }

    return result

