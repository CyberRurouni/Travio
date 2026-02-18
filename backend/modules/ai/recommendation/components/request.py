from typing import Dict, Any
from core import call_openai_safe


def formulate_request(body: str) -> Dict[str, Any]:
    prompt = f"""
You are an information extraction engine for a travel recommendation system.

Your task:
Convert the email into structured JSON that EXACTLY matches the expected search request format.

────────────────────────────────────
DATABASE SCHEMA (REFERENCE)
────────────────────────────────────
Table: agency_packages

Relevant columns:
- category (TEXT)                -- e.g. travel, adventure, umrah, corporate
- destination (TEXT)             -- city, country, or region
- price_amount (NUMERIC)
- price_currency (CHAR(3))       -- USD, EUR, CAD
- duration_days (INT)
- ideal_for (TEXT[])             -- family, couple, honeymoon, group, solo
- includes (TEXT[])              -- hotel, flight, visa, meals, transport

────────────────────────────────────
STRICT EXTRACTION RULES
────────────────────────────────────

1. Output ONLY valid JSON.
2. Follow the exact schema shown below.
3. Do NOT guess or infer missing data.
4. Only extract constraints explicitly stated in the email.
5. Normalize values to database-safe tokens.
6. If unsure, leave the field empty or null.
7. Do NOT invent categories, currencies, or durations.
8. Preferences about “vibe”, “feel”, or “avoidance” go into vague_exclusion.
9. Extract result limit if explicitly mentioned (e.g. “provide/show 3 options” → limit: 3).
10. If limit is not provided, default to 5.
11. If limit exceeds 10, normalize it to 10.
12. If limit is invalid, negative, or zero → default to 5.

────────────────────────────────────
EXPECTED OUTPUT SCHEMA (MANDATORY)
────────────────────────────────────

{{
  "main_query": "string",
  "constraints": {{
    "category": {{ "include": [], "exclude": [] }},
    "destination": {{ "include": [], "exclude": [] }},
    "price_amount": {{ "min": null, "max": null }},
    "price_currency": {{ "include": [] }},
    "duration_days": {{ "min": null, "max": null }},
    "ideal_for": {{ "include": [] }},
    "includes": {{ "include": [] }}
  }},
  "vague_exclusion": "",
  "limit": 5
}}

────────────────────────────────────
NORMALIZATION GUIDELINES
────────────────────────────────────

- “family trip” → ideal_for: ["family"]
- “honeymoon” → ideal_for: ["honeymoon"]
- “corporate retreat” → category: ["corporate"]
- “adventure” → category: ["adventure"]
- “around a week” → duration_days: min 6, max 8
- “show/provide 3 options” → limit: 3
- “top 8 packages” → limit: 8
- “a few options” → DO NOT infer → leave missing
- “no / avoid X” → exclude if X is a hard category
- “avoid vibe / climate / theme” → vague_exclusion

────────────────────────────────────
EXAMPLES (FOLLOW THESE EXACTLY)
────────────────────────────────────

Example 1
EMAIL:
"We’re a family of four looking for an adventure trip in Canada.
Budget between $2,000 and $5,000 USD.
Around a week long.
Please avoid corporate-style packages."

OUTPUT:
{{
  "main_query": "Family adventure travel experience in Canada",
  "constraints": {{
    "category": {{
      "include": ["adventure"],
      "exclude": ["corporate"]
    }},
    "destination": {{
      "include": ["Canada"],
      "exclude": []
    }},
    "price_amount": {{
      "min": 2000,
      "max": 5000
    }},
    "price_currency": {{
      "include": ["USD"]
    }},
    "duration_days": {{
      "min": 6,
      "max": 8
    }},
    "ideal_for": {{
      "include": ["family"]
    }},
    "includes": {{
      "include": []
    }}
  }},
  "vague_exclusion": "",
  "limit": 5,
}}

Example 2
EMAIL:
"Looking for something fun and cultural, maybe Europe or Asia.
Not too touristy, no snowy destinations. Suggest me top three options"

OUTPUT:
{{
  "main_query": "Fun cultural travel experiences in Europe or Asia",
  "constraints": {{
    "category": {{
      "include": [],
      "exclude": []
    }},
    "destination": {{
      "include": ["Europe", "Asia"],
      "exclude": []
    }},
    "price_amount": {{
      "min": null,
      "max": null
    }},
    "price_currency": {{
      "include": []
    }},
    "duration_days": {{
      "min": null,
      "max": null
    }},
    "ideal_for": {{
      "include": []
    }},
    "includes": {{
      "include": []
    }}
  }},
  "vague_exclusion": "touristy places, snowy destinations",
  "limit": 3,
}}

────────────────────────────────────
EMAIL TO ANALYZE
────────────────────────────────────
\"\"\"{body}\"\"\"

Return ONLY the JSON object.
"""

    result = call_openai_safe(
        messages=[{"role": "user", "content": prompt}],
        max_tokens=500,
        response_format="json",
        fallback_response={
            "main_query": "",
            "constraints": {
                "category": {"include": [], "exclude": []},
                "destination": {"include": [], "exclude": []},
                "price_amount": {"min": None, "max": None},
                "price_currency": {"include": []},
                "duration_days": {"min": None, "max": None},
                "ideal_for": {"include": []},
                "includes": {"include": []},
            },
            "vague_exclusion": "",
            "limit": 5,
        },
    )

    # If model returned string, try parsing
    if isinstance(result, str):
        import json
        try:
            result = json.loads(result)
        except json.JSONDecodeError:
            result = {
                "main_query": "",
                "constraints": {
                    "category": {"include": [], "exclude": []},
                    "destination": {"include": [], "exclude": []},
                    "price_amount": {"min": None, "max": None},
                    "price_currency": {"include": []},
                    "duration_days": {"min": None, "max": None},
                    "ideal_for": {"include": []},
                    "includes": {"include": []},
                },
                "vague_exclusion": "",
                "limit": 5,
            }

    # ===============================
    # HARD LIMIT ENFORCEMENT LAYER
    # ===============================
    limit = result.get("limit", 5)

    try:
        limit = int(limit)
    except (TypeError, ValueError):
        limit = 5

    if limit <= 0:
        limit = 5
    elif limit > 10:
        limit = 10

    result["limit"] = limit

    return result

