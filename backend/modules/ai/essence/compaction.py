import json
import logging
from core import call_openai_safe

logger = logging.getLogger("ESSENCE EXTRACTOR")

system_prompt = """
You are a deterministic Recursive Chat Compression Engine.

You will receive a full chat_container as a JSON array of messages.

Each message contains:
{
  "sender": "...",
  "text": "..."
}

Your task is to compress the conversation into a shorter dialogue form while preserving the full conversational arc.

CRITICAL RULES:

1. Output MUST be a valid JSON array.
2. Preserve the same structure:
   {
     "sender": "...",
     "text": "..."
   }

3. Keep it in dialogue format.
   - Do NOT create summaries.
   - Do NOT create sections.
   - Do NOT analyze.
   - Do NOT explain.
   - Just rewrite shorter.

4. Preserve:
   - Initial user intent
   - Major system/database actions
   - Packages presented
   - Follow-up requests
   - Escalations (e.g., connect_human_agent)
   - Final state

5. Preserve the conversational progression.

If the conversation contains:
- A request
- A system action
- A result
- A follow-up
- An escalation

Then your output MUST reflect each stage in order.

You are NOT allowed to collapse multiple stages into one message.

Minimum structure requirements:
- At least 1 prospect message
- At least 1 system action summary (if actions occurred)
- At least 1 assistant/Travio message (if present)

If 4+ distinct conversational events exist,
the output must contain at least 4–6 messages.

6. Remove:
   - Redundant confirmations
   - Word repetition
   - Email signatures
   - Excessive politeness
   - Quoted message threads

7. Shorten aggressively but never remove transitions.

8. This compression will happen recursively over time.
   - Do NOT expand meaning.
   - Do NOT invent context.
   - Do NOT reinterpret intent.
   - Only reduce verbosity.

9. Keep the latest turns slightly clearer than older ones.

OUTPUT:
Return ONLY the rewritten JSON array.
No markdown.
No commentary.
No explanation.
"""

logger = logging.getLogger("DIALOGUE COMPACTOR")


MIN_REQUIRED_MESSAGES = 4
MAX_RETRIES = 5
BASE_MAX_TOKENS = 1200
TOKEN_INCREMENT = 300


def _is_structurally_valid(original: list[dict], compressed: list[dict]) -> bool:
    """
    Ensures compressed dialogue preserves structural integrity.
    """

    if not isinstance(compressed, list):
        return False

    if len(compressed) < MIN_REQUIRED_MESSAGES:
        return False

    compressed_senders = {m.get("sender") for m in compressed if isinstance(m, dict)}

    original_senders = {m.get("sender") for m in original if isinstance(m, dict)}

    # Must preserve at least two roles
    if len(compressed_senders) < 2:
        return False

    # If system actions existed, they must remain
    if "system" in original_senders and "system" not in compressed_senders:
        return False

    return True


def compact_dialogue_state(chat_history: list[dict]) -> tuple[list[dict], bool]:
    """
    Recursively compresses a dialogue while preserving its structural progression.

    Returns:
        (compressed_chat, compression_disabled)
    """

    logger.info(
        "🟢 Starting dialogue compaction | Messages=%d",
        len(chat_history),
    )

    system_prompt = """ 
    (keep your existing deterministic compression prompt here unchanged)
    """

    try:
        for attempt in range(MAX_RETRIES):

            max_tokens = BASE_MAX_TOKENS + (attempt * TOKEN_INCREMENT)

            result = call_openai_safe(
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": json.dumps(chat_history)},
                ],
                temperature=0,
                max_tokens=max_tokens,
                model="openai/gpt-4o-mini",
                response_format="json",
                fallback_response=chat_history,
            )

            if isinstance(result, str):
                result = json.loads(result)

            if _is_structurally_valid(chat_history, result):
                logger.info(
                    "✨ Dialogue compaction successful | %d → %d messages",
                    len(chat_history),
                    len(result),
                )
                return result, False

            logger.warning(
                "⚠️ Invalid compaction attempt %d | Retrying...",
                attempt + 1,
            )

        # If all retries fail
        logger.error(
            "❌ Dialogue compaction failed after retries. Disabling further compaction."
        )
        return chat_history, True

    except Exception as e:
        logger.error("💥 Dialogue compaction exception: %s", e)
        return chat_history, True
