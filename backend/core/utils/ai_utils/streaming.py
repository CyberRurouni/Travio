"""
Streaming JSON reading — the mechanics behind AIClient.stream().

JsonStreamReader opens a streaming completion and stops reading the
instant a complete, balanced JSON object/array has arrived — instead of
waiting for the model to keep going until max_tokens runs out.

_build_continuation_prompt builds the message that asks a model to
continue a response that got cut off — used when a stream ends without
ever balancing (see AIClient.stream's continuation loop).
"""

import logging
from typing import Optional

logger = logging.getLogger("AI UTILS")


class JsonStreamReader:
    """
    Reads a streaming chat completion one piece at a time and stops the
    moment a complete, balanced JSON object or array has arrived.

    How it decides "done": a JSON object/array is self-describing about
    when it's finished — every '{' or '[' must be matched by a '}' or ']'.
    So this just counts: +1 every time it sees the opening character, -1
    every time it sees the matching closing character (ignoring anything
    inside a quoted string, since braces there are just text, not structure).
    The moment that count ('depth') returns to zero, the structure is complete.

    The opening/closing character is only ever figured out once, from
    whichever chunk first contains it.

    Usage:
        reader = JsonStreamReader()
        json_text = reader.read(client, model=model, messages=messages,
                                 temperature=temperature, max_tokens=max_tokens)
        # json_text is a complete JSON string, or None if the stream ended —
        # naturally, or because max_tokens ran out — before ever balancing.
    """

    def __init__(self):
        self.start_char = None      # '{' or '[' — locked in once, from the first chunk that has one
        self.end_char = None        # the matching '}' or ']'
        self.buffer = ""            # everything received so far (may include leading prose)
        self.depth = 0              # how many start_char are currently unclosed
        self.inside_string = False  # are we inside a "..." value right now?
        self.escape_next = False    # was the previous character a backslash?

    def read(self, client, is_continuation: bool = False, **request_kwargs) -> Optional[str]:
        """
        Open the stream and consume it chunk by chunk until the JSON
        balances or the stream ends. This method is SYNCHRONOUS and must be
        run via asyncio.to_thread — the underlying SDK's stream iterator
        blocks while waiting on the network.

        Args:
            is_continuation: True when this call is picking up after a
                previous one got cut off. When True, the first ~40
                characters of the new response are checked against the
                tail of what we already have — models sometimes slightly
                repeat themselves when asked to continue — and trimmed if
                they overlap. See _strip_overlap.

        Raises whatever the underlying client raises on a connection/API
        error — not swallowed here. AIClient.stream is responsible for
        catching that and handing it to ApiErrorHandler.
        """
        stream = client.chat.completions.create(stream=True, **request_kwargs)

        # Held-back text while we wait to have enough to run the overlap
        # check, on the first chunk(s) of a continuation round only.
        pending_overlap_check = "" if is_continuation else None
        OVERLAP_CHECK_MIN_CHARS = 40

        for chunk in stream:
            if not chunk.choices:
                continue
            delta = getattr(chunk.choices[0].delta, "content", None)
            if not delta:
                continue

            if pending_overlap_check is not None:
                pending_overlap_check += delta
                if len(pending_overlap_check) < OVERLAP_CHECK_MIN_CHARS:
                    continue
                delta = self._strip_overlap(pending_overlap_check)
                pending_overlap_check = None

            finished_json = self._consume(delta)
            if finished_json is not None:
                if hasattr(stream, "close"):
                    stream.close()  # early stop — we already have everything we need
                return finished_json

        # Stream ended before we ever collected enough to run the overlap
        # check — flush whatever we were holding, trimmed, before giving up.
        if pending_overlap_check:
            finished_json = self._consume(self._strip_overlap(pending_overlap_check))
            if finished_json is not None:
                return finished_json

        return None

    def _consume(self, new_text: str) -> Optional[str]:
        """Process one newly-arrived piece of text. Returns the finished JSON the moment it balances."""
        offset = len(self.buffer)
        self.buffer += new_text

        for i, ch in enumerate(new_text):
            if self.start_char is None:
                if ch == "{":
                    self.start_char, self.end_char, self.depth = "{", "}", 1
                elif ch == "[":
                    self.start_char, self.end_char, self.depth = "[", "]", 1
                continue

            if self.escape_next:
                self.escape_next = False
                continue

            if ch == "\\" and self.inside_string:
                self.escape_next = True
                continue

            if ch == '"':
                self.inside_string = not self.inside_string
                continue

            if self.inside_string:
                continue

            if ch == self.start_char:
                self.depth += 1
            elif ch == self.end_char:
                self.depth -= 1
                if self.depth == 0:
                    start_index = self.buffer.index(self.start_char)
                    end_index = offset + i + 1
                    return self.buffer[start_index:end_index]

        return None

    def _strip_overlap(self, new_text: str) -> str:
        """
        If a continuation repeats the tail end of what we already collected,
        trim that repeated part off before it gets counted.

        Checks against the last 200 characters already in the buffer, finds
        the LONGEST matching overlap, and removes it.

        Known limitation: only runs once, on the first chunk(s) of a
        continuation. A repeat that dribbles in gradually across several
        tiny chunks could partially slip through uncaught.
        """
        tail = self.buffer[-200:]
        max_check = min(len(tail), len(new_text))

        for length in range(max_check, 0, -1):
            if tail[-length:] == new_text[:length]:
                logger.info(
                    f"🔁 Trimmed {length}-char overlap from continuation: {new_text[:length]!r}"
                )
                return new_text[length:]

        return new_text


def build_continuation_prompt(reader: JsonStreamReader) -> str:
    """
    Build the user-turn message that asks the model to continue a response
    that got cut off mid-JSON.

    Deliberately ends the conversation on a USER turn (not a trailing
    assistant message) — some models/providers are stricter than others
    about the conversation ending on a user turn (Gemini, notably), so this
    works everywhere rather than relying on prefill support that varies by
    model. The partial output is still included as an assistant turn right
    before this, so the model can see exactly what it already wrote.
    """
    tail_preview = reader.buffer[-60:]
    return (
        "Your previous response was cut off before the JSON was complete. "
        f"It ended with: ...{tail_preview!r}\n\n"
        "Continue EXACTLY from that point. Do not repeat anything you already "
        "wrote, do not add a fresh opening brace, and do not add any commentary "
        "or code fences. Output only the raw continuation text."
    )