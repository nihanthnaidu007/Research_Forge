"""
ResearchForge LLM utility helpers.
Shared retry logic for all OpenAI API calls across agent files.
"""

import logging
import time
from collections.abc import Callable
from typing import TypeVar

logger = logging.getLogger(__name__)

T = TypeVar("T")

# OpenAI status codes that are safe to retry.
# 429: rate limit exceeded — always retry with backoff
# 500: internal server error — transient, retry
# 503: service unavailable — transient, retry
# 502: bad gateway — transient, retry
RETRYABLE_STATUS_CODES = {429, 500, 502, 503}


def call_with_retry(
    fn: Callable[[], T],
    max_attempts: int = 3,
    base_delay: float = 2.0,
    max_delay: float = 30.0,
    label: str = "LLM call",
) -> T:
    """
    Execute fn() with exponential backoff on transient OpenAI errors.

    Retries on:
      - openai.RateLimitError        (HTTP 429)
      - openai.InternalServerError   (HTTP 500)
      - openai.APIStatusError with retryable status code
      - openai.APIConnectionError    (network hiccup)

    Does NOT retry on:
      - openai.AuthenticationError   (bad key — won't recover)
      - openai.BadRequestError       (bad prompt — won't recover)
      - Any other non-retryable exception

    Args:
        fn:           Zero-argument callable that makes the LLM call.
        max_attempts: Total attempts including the first. Default 3.
        base_delay:   Initial wait in seconds before first retry. Default 2.
        max_delay:    Cap on wait time between retries. Default 30.
        label:        Human-readable label for log messages.

    Returns:
        The return value of fn() on success.

    Raises:
        The last exception if all attempts are exhausted.
    """
    import openai

    last_exception = None

    for attempt in range(1, max_attempts + 1):
        try:
            return fn()
        except (
            openai.RateLimitError,
            openai.InternalServerError,
            openai.APIConnectionError,
        ) as e:
            last_exception = e
            if attempt == max_attempts:
                logger.error(f"{label} failed after {max_attempts} attempts: {e}")
                raise

            delay = min(base_delay * (2 ** (attempt - 1)), max_delay)
            logger.warning(
                f"{label} attempt {attempt}/{max_attempts} failed "
                f"({type(e).__name__}). Retrying in {delay:.1f}s..."
            )
            time.sleep(delay)

        except openai.APIStatusError as e:
            last_exception = e
            if e.status_code in RETRYABLE_STATUS_CODES:
                if attempt == max_attempts:
                    logger.error(
                        f"{label} failed after {max_attempts} attempts "
                        f"(HTTP {e.status_code}): {e}"
                    )
                    raise
                delay = min(base_delay * (2 ** (attempt - 1)), max_delay)
                logger.warning(
                    f"{label} attempt {attempt}/{max_attempts} failed "
                    f"(HTTP {e.status_code}). Retrying in {delay:.1f}s..."
                )
                time.sleep(delay)
            else:
                # Non-retryable status code — raise immediately
                raise

    # Should not reach here — raise the last exception as a safety net
    raise last_exception
