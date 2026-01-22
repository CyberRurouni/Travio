import logging
import json
from typing import Optional, Dict, Any, Literal, Union
import time

logger = logging.getLogger(__name__)


def extract_first_json(text: str) -> Optional[str]:
    """Extract first complete JSON object from text."""
    start = text.find("{")
    if start == -1:
        return None
    depth = 0
    for i in range(start, len(text)):
        ch = text[i]
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return text[start : i + 1]
    return None


def call_openai(
    messages,
    model="gemini-2.5-flash",
    max_tokens=800,
    temperature=0.0,
    response_format: Literal["json", "text"] = "json",
    max_retries: int = 3,
    retry_delay: float = 1.0,
) -> Union[dict, str]:
    """
    Calls OpenAI/Gemini chat completion with robust error handling.

    Args:
        messages: Chat messages list
        model: Model identifier
        max_tokens: Maximum tokens (increased default for JSON responses)
        temperature: Sampling temperature
        response_format: "json" for dict, "text" for string
        max_retries: Number of retry attempts on failure
        retry_delay: Delay between retries in seconds

    Returns:
        Dict if response_format="json", str if "text"

    Raises:
        ValueError: If JSON parsing fails after all retries
        RuntimeError: If API call fails after all retries
    """
    from core import client  # Avoid circular import

    last_error = None
    
    for attempt in range(max_retries):
        try:
            resp = client.chat.completions.create(
                model=model,
                messages=messages,
                temperature=temperature,
                max_tokens=max_tokens,
            )

            text = resp.choices[0].message.content
            
            # Handle empty response
            if text is None or text.strip() == "":
                raise ValueError("Model returned empty content")
            
            text = text.strip()

            # Return raw text if requested
            if response_format == "text":
                return text

            # ━━━ JSON MODE ━━━
            # Try direct parsing first
            try:
                parsed = json.loads(text)
                logger.debug(f"✅ JSON parsed successfully on attempt {attempt + 1}")
                return parsed
            except json.JSONDecodeError as json_err:
                logger.warning(f"⚠️ Direct JSON parse failed: {json_err}")
                
                # Try extracting first JSON object
                blob = extract_first_json(text)
                if blob:
                    try:
                        parsed = json.loads(blob)
                        logger.debug(f"✅ Extracted JSON parsed on attempt {attempt + 1}")
                        return parsed
                    except json.JSONDecodeError:
                        pass
                
                # Check if response was truncated
                if text.endswith('"') or text.endswith(','):
                    logger.warning(f"🔪 Response appears truncated (attempt {attempt + 1})")
                    last_error = ValueError(
                        f"JSON truncated - increase max_tokens (current: {max_tokens})"
                    )
                else:
                    last_error = ValueError(f"Invalid JSON format:\n{text[:200]}...")
                
                # Retry if not last attempt
                if attempt < max_retries - 1:
                    logger.info(f"🔄 Retrying in {retry_delay}s... (attempt {attempt + 2}/{max_retries})")
                    time.sleep(retry_delay)
                    continue
                    
        except Exception as e:
            logger.error(f"💥 API call failed on attempt {attempt + 1}: {e}")
            last_error = RuntimeError(f"API error: {str(e)}")
            
            if attempt < max_retries - 1:
                time.sleep(retry_delay * (attempt + 1))  # Exponential backoff
                continue

    # All retries exhausted
    raise last_error or RuntimeError("All retry attempts failed")


def call_openai_safe(
    messages,
    model="gemini-2.5-flash-lite",
    max_tokens=800,
    temperature=0.0,
    response_format: Literal["json", "text"] = "json",
    fallback_response: Optional[Dict[str, Any]] = None,
) -> Union[dict, str]:
    """
    Safe wrapper around call_openai that never crashes.
    
    Returns fallback_response instead of raising exceptions.
    Use this in production pipelines where you need graceful degradation.
    
    Args:
        fallback_response: Dict to return if all attempts fail (for JSON mode)
    """
    try:
        return call_openai(
            messages=messages,
            model=model,
            max_tokens=max_tokens,
            temperature=temperature,
            response_format=response_format,
        )
    except Exception as e:
        logger.error(f"🚨 call_openai_safe caught exception: {e}")
        
        if response_format == "json":
            return fallback_response or {
                "error": True,
                "error_message": str(e),
                "status": "fallback"
            }
        else:
            return f"[Error: {str(e)}]"