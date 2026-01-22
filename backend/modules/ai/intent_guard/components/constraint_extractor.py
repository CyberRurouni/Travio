from typing import Dict, Any
from core import call_openai_safe

def extract_constraints(body: str, model="gemini-2.5-flash-lite") -> Dict[str, Any]:
    prompt = f"""
    You are a travel agent AI assistant. Extract all travel-related constraints mentioned in the email.
    Focus on: dates, destination, budget, number of travelers, travel style, special requirements.
    
    Return JSON:
    {{
        "constraints": {{
            "budget": "string or null",
            "dates": "string or null",
            "destination": "string or null",
            "travelers_count": "int or null",
            "travel_style": "luxury / budget / adventure / family / other",
            "special_requirements": ["list any other constraints"]
        }}
    }}

    Examples:

    Example 1:
    Email: "We are planning a trip to Spain in August for 2 adults and 2 kids. Budget is around $5000. We prefer family-friendly hotels."
    Output:
    {{
        "constraints": {{
            "budget": "$5000",
            "dates": "August",
            "destination": "Spain",
            "travelers_count": 4,
            "travel_style": "family",
            "special_requirements": ["family-friendly hotels"]
        }}
    }}

    Example 2:
    Email: "Looking for a luxury honeymoon in Maldives next February. Prefer overwater villas."
    Output:
    {{
        "constraints": {{
            "budget": null,
            "dates": "next February",
            "destination": "Maldives",
            "travelers_count": 2,
            "travel_style": "luxury",
            "special_requirements": ["overwater villas"]
        }}
    }}

    Now analyze this email body:
    \"\"\"{body}\"\"\"
    """
    result = call_openai_safe(
        messages=[{"role": "user", "content": prompt}],
        model=model,
        max_tokens=400,
        response_format="json",
        fallback_response={
            "constraints": {
                "budget": None,
                "dates": None,
                "destination": None,
                "travelers_count": None,
                "travel_style": None,
                "special_requirements": []
            }
        }
    )

    if isinstance(result, str):
        import json
        try:
            result = json.loads(result)
        except json.JSONDecodeError:
            result = {
                "constraints": {
                "budget": None,
                "dates": None,
                "destination": None,
                "travelers_count": None,
                "travel_style": None,
                "special_requirements": []
            }
            }

    return result
