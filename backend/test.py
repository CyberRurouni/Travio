import json
import logging
from core import call_openai_safe

logger = logging.getLogger("ESSENCE EXTRACTOR")


def extract_essence(chat_container: list[dict]) -> list[dict]:
    """
    Compresses a chat_container into a shorter dialogue while preserving structure.
    Always returns a list[dict].
    """

    logger.info(f"🟢 Starting essence extraction for {len(chat_container)} messages...")

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

    try:
        result = call_openai_safe(
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": json.dumps(chat_container, indent=2)},
            ],
            temperature=0,
            max_tokens=1200,
            response_format="json",
            fallback_response=chat_container,  # ✅ fallback
        )

        # If model returned string JSON, parse it
        if isinstance(result, str):
            result = json.loads(result)

        logger.info(
            f"✨ Essence extraction completed. Reduced messages from {len(chat_container)} to {len(result)}"
        )
        return result

    except Exception as e:
        logger.error(f"💥 Essence extraction failed: {e}")
        return chat_container


if __name__ == "__main__":
    chat_container = [
        {
            "sender": "prospect",
            "text": "Winter getaway recs, solo. Need accommodation & transport. Cozy/scenic or winter activities. Trending welcome.",
        },
        {"sender": "system", "text": "Scanning winter packages."},
        {"sender": "Travio", "text": "Singapore Stopover: 2 days, 899 SGD."},
        {
            "sender": "prospect",
            "text": "Need hotel details. Prefer snow, cozy, fireplaces, northern lights. Singapore isn't wintery.",
        },
        {
            "sender": "Travio",
            "text": "Hotel details unavailable. Connecting to specialist.",
        },
        {
            "sender": "system",
            "text": "Scanning for snow, cozy winter, northern lights.",
        },
        {
            "sender": "Travio",
            "text": "1. Lapland Aurora Quest: 5 days, 2499 EUR (igloo, reindeer, fireplace, aurora).\n2. Alpine Christmas Charm: 4 days, 1899 CHF (chalet, Matterhorn).\n3. Icelandic Winter Wonderland: 6 days, 3299 USD (aurora, Blue Lagoon).",
        },
        {
            "sender": "prospect",
            "text": "Lapland or Iceland for northern lights in Feb? Better odds?",
        },
        {"sender": "system", "text": "Querying aurora probability."},
        {
            "sender": "Travio",
            "text": "Lapland: 70-80% vs Iceland: 60-70%. Lapland has glass igloos.",
        },
        {"sender": "prospect", "text": "Lapland! More on accommodation and meals?"},
        {"sender": "system", "text": "Querying Lapland package details."},
        {
            "sender": "Travio",
            "text": "2 nights log cabin (fireplace/sauna), 1 night glass igloo, 1 night log cabin. Breakfast daily, 3 dinners (incl. reindeer cooking class). Activities: aurora hunt, reindeer sleigh, snowshoeing.",
        },
        {
            "sender": "prospect",
            "text": "Perfect! Total with flights from NYC? Cancellation policy?",
        },
        {
            "sender": "system",
            "text": "Querying flight costs, total estimate, and cancellation.",
        },
        {
            "sender": "Travio",
            "text": "Flights NYC add $1,200-1,500. Total ~$4,100-4,300. Free cancellation up to 30 days prior.",
        },
        {
            "sender": "prospect",
            "text": "Ready to book! Can Sarah help with Feb dates? And a second igloo night?",
        },
        {
            "sender": "Travio",
            "text": "Connecting you to Sarah, European winter specialist.",
        },
        {
            "sender": "Sarah (Travel Specialist)",
            "text": "Hi Ameer! I can check dates and the double igloo. Which weeks in Feb work?",
        },
        {
            "sender": "prospect",
            "text": "Feb 7-14 or Feb 21-28. Double igloo would be amazing!",
        },
        {
            "sender": "Sarah (Travel Specialist)",
            "text": "Feb 21-28 has double igloo nights available! Husky safari add-on too. Total with flights ~$4,350. Shall I hold it?",
        },
        {"sender": "prospect", "text": "YES! Hold it!"},
        {
            "sender": "Sarah (Travel Specialist)",
            "text": "Done! 48-hour hold. I'll email payment link and insurance. 30% deposit secures.",
        },
        {"sender": "prospect", "text": "Payment sent! Thank you!"},
        {
            "sender": "Sarah (Travel Specialist)",
            "text": "Payment received! Confirmation and itinerary within 24 hours.",
        },
        {
            "sender": "prospect",
            "text": "Hi, I'm Maya, a photographer planning a solo winter trip. I want snow, cozy cabins, and northern lights for photos. Budget is flexible, need accommodation and transport.",
        },
        {
            "sender": "system",
            "text": "Scanning for winter packages with snow, northern lights, scenic landscapes, solo traveler friendly.",
        },
        {
            "sender": "Travio",
            "text": "I found Kyoto Winter Lights (no snow guaranteed) and Swiss Alps Snow Escape (snowy, no aurora). Which interests you?",
        },
        {
            "sender": "prospect",
            "text": "I need snow AND northern lights. Do you have packages combining both?",
        },
        {
            "sender": "Travio",
            "text": "Let me search for aurora photography destinations with snow.",
        },
        {
            "sender": "system",
            "text": "Scanning for northern lights packages with snow, photographer-friendly.",
        },
        {
            "sender": "Travio",
            "text": "I found three options:\n1. Tromso Northern Lights Expedition (Norway): 7 days, 4200 USD, guided photo tours, glass lodge.\n2. Yellowstone Winter & Aurora (USA): 5 days, 3500 USD, rustic cabins, wildlife focus.\n3. Swedish Lapland Ice Hotel & Aurora: 4 days, 3900 USD, ice hotel, photography workshop.",
        },
        {
            "sender": "prospect",
            "text": "These are amazing! Tell me more about the photography workshops in each.",
        },
        {
            "sender": "Travio",
            "text": "Tromso offers comprehensive aurora instruction. Yellowstone focuses on night sky and wildlife. Sweden has a dedicated aurora class and ice hotel shooting.",
        },
        {
            "sender": "prospect",
            "text": "I'm torn between Tromso's instruction and Sweden's Ice Hotel. What's the accommodation like in Tromso? And what's the best time in February for photography?",
        },
        {
            "sender": "Travio",
            "text": "Tromso has a glass lodge with panoramic views. For photography, Feb 7-14 is ideal due to the new moon for darkest skies. Would you like me to check availability?",
        },
        {
            "sender": "prospect",
            "text": "YES! Feb 7-14 sounds perfect! Please check availability and flight costs from Chicago.",
        },
        {
            "sender": "Travio",
            "text": "Great news! There are 2 spots left for Feb 7-14. Flights from Chicago are approx. $1,100-1,400. Total estimate is $5,450 USD. A 25% deposit secures your spot. Shall I hold it?",
        },
        {
            "sender": "prospect",
            "text": "HOLD IT PLEASE! I need to discuss payment options and insurance for camera gear.",
        },
        {
            "sender": "system",
            "text": "Connecting to a human agent specializing in photography travel.",
        },
        {
            "sender": "Erik (Photography Travel Specialist)",
            "text": "Hi Maya! I'm Erik. I can help with payment plans and insurance for your gear. AIG Travel Guard or World Nomads are good options. We can do 50% now, 50% Jan 15. I'll also ensure you get our best aurora guide, Lars.",
        },
        {
            "sender": "prospect",
            "text": "Yes, please! Send the payment link with those terms. I'm so excited!",
        },
        {
            "sender": "Erik (Photography Travel Specialist)",
            "text": "Email sent with payment link, insurance info, and packing list. Once deposit is processed, you'll be added to a private group chat with Lars and other photographers.",
        },
        {"sender": "prospect", "text": "Payment sent! Thank you, Erik! I'm thrilled!"},
        {
            "sender": "Erik (Photography Travel Specialist)",
            "text": "Payment received! You're in! Welcome to the Tromso Photography Expedition, Maya!",
        },
    ]
    chat_history_clean = [
        {"sender": m.get("sender"), "text": m.get("text")} for m in chat_container
    ]

    essence = extract_essence(chat_container=chat_history_clean)
    logger.info(f"📝 Compressed chat output:\n{json.dumps(essence, indent=2)}")