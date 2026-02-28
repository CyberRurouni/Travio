import re

def extract_clean_email_body(body: str) -> str:
    """
    Extracts the main message body from an email by removing:
    1. Quoted reply tails starting with lines like:
       "On Thu, Feb 19, 2026 at 12:24 PM <someone@example.com> wrote:"
    2. Forwarded message blocks starting with:
       "---------- Forwarded message ---------"
    3. Everything after these markers, leaving only the clean sender message.
    """
    if not body:
        return body

    # Trim leading/trailing whitespace first
    body = body.strip()

    # Patterns for replies and forwards
    patterns = [
        r"On\s.+?wrote:\s*$",                       # Standard reply tail
        r"-{2,} Forwarded message -{2,}",           # Forwarded message
        r"From:\s.+?\n",                            # Forwarded "From:" lines
        r"Subject:\s.+?\n",                         # Forwarded "Subject:" lines
        r"To:\s.+?\n",                              # Forwarded "To:" lines
        r"Date:\s.+?\n",                            # Forwarded "Date:" lines
    ]

    # Combine into one regex
    combined_pattern = re.compile("|".join(patterns), re.IGNORECASE | re.MULTILINE | re.DOTALL)

    # Search for first occurrence of any pattern
    match = combined_pattern.search(body)
    if match:
        # Skip patterns at the very start if they're forward headers
        if match.start() < 2:  # Allow tiny margin for newlines
            # Search for the first non-header text
            # Take everything after the first double newline (\n\n)
            parts = re.split(r"\n\s*\n", body)
            for part in parts:
                part_clean = part.strip()
                if part_clean and not re.search(r"^-{2,} Forwarded message -{2,}", part_clean):
                    return part_clean
            return ""
        body = body[:match.start()]

    return body.strip()

