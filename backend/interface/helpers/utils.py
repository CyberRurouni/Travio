import re

# ─── Helper to remove quoted reply tails ───────────────────────────
def strip_email_reply_tail(body: str) -> str:
    """
    Removes the standard email reply tail starting with lines like:
    "On Thu, Feb 19, 2026 at 12:24 PM <someone@example.com> wrote:"
    """
    if not body:
        return body

    # Regex pattern to match "On <date> at <time> <email> wrote:" lines
    pattern = re.compile(
        r"(?m)^On .+ wrote:$"
    )

    # Find the first occurrence
    match = pattern.search(body)
    if match:
        # Keep only the text before the match
        body = body[: match.start()].strip()

    return body