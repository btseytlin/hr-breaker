"""Retry utilities for LLM API calls with exponential backoff."""

import logging

from tenacity import (
    before_sleep_log,
    retry,
    retry_if_exception,
    stop_after_attempt,
    wait_exponential,
)

from pydantic_ai.exceptions import ModelHTTPError

from hr_breaker.config import get_settings

logger = logging.getLogger(__name__)

RETRYABLE_STATUS_CODES = {429, 500, 502, 503, 504}


def _is_non_retryable_litellm_config_error(exc: BaseException) -> bool:
    if not isinstance(exc, ModelHTTPError):
        return False
    message = str(exc)
    return (
        exc.status_code == 500
        and "APIConnectionError" in message
        and "argument of type 'NoneType' is not iterable" in message
    )


def is_retryable(exc: BaseException) -> bool:
    """Check if exception is retryable (rate limit or transient server error)."""
    if _is_non_retryable_litellm_config_error(exc):
        return False
    if isinstance(exc, ModelHTTPError):
        return exc.status_code in RETRYABLE_STATUS_CODES
    status = getattr(exc, "status_code", None)
    if isinstance(status, int):
        return status in RETRYABLE_STATUS_CODES
    return False


async def run_with_retry(
    func,
    *args,
    _max_attempts: int | None = None,
    _max_wait: float | None = None,
    **kwargs,
):
    """Run an async callable with retry on rate limits and transient errors.

    Args:
        func: Async callable to run.
        *args: Positional args passed to func.
        _max_attempts: Override max retry attempts (default: from settings).
        _max_wait: Override max wait seconds (default: from settings).
        **kwargs: Keyword args passed to func.
    """
    settings = get_settings()
    max_attempts = _max_attempts or settings.retry_max_attempts
    max_wait = _max_wait or settings.retry_max_wait

    @retry(
        retry=retry_if_exception(is_retryable),
        stop=stop_after_attempt(max_attempts),
        wait=wait_exponential(multiplier=1, max=max_wait),
        before_sleep=before_sleep_log(logger, logging.WARNING),
        reraise=True,
    )
    async def _inner():
        return await func(*args, **kwargs)

    return await _inner()
