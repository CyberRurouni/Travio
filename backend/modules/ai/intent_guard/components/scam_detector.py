from typing import Dict, Any
from core import call_openai


def detect_scam(body: str) -> Dict[str, Any]:
    prompt = f"""
    You are a travel agent AI assistant. Determine if this email is potentially a scam or if the prospect
    is disguised to push their own agenda. Look for suspicious links, contradictory requests, or unusual behavior.

    Return JSON:
    {{
        "is_scam": true/false,
        "risk_level": "low/medium/high",
        "reasons": "brief explanation"
    }}

    Examples:

    Example 1:
    Email: "Please pay $1000 upfront to unlock my special travel deal. Contact me urgently."
    Output:
    {{
        "is_scam": true,
        "risk_level": "high",
        "reasons": "Requests upfront payment, urgent pressure tactics"
    }}

    Example 2:
    Email: "Looking for a family trip to Italy. Can you suggest options?"
    Output:
    {{
        "is_scam": false,
        "risk_level": "low",
        "reasons": "Normal travel inquiry"
    }}

    Email body:
    \"\"\"{body}\"\"\"
    """
    result = call_openai.blocking(
        messages=[{"role": "user", "content": prompt}],
        max_tokens=300,
        increment=100,
        fallback={
            "is_scam": False,
            "risk_level": "low",
            "reasons": "fallback",
        },
    )

    if isinstance(result, str):
        import json

        try:
            result = json.loads(result)
        except json.JSONDecodeError:
            result = {"is_scam": False, "risk_level": "low", "reasons": "fallback"}

    return result
