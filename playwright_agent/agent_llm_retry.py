"""LLM error classification for retry and fallback logic."""

from __future__ import annotations

from anthropic import (
    APIConnectionError,
    APIResponseValidationError,
    APITimeoutError,
    AuthenticationError,
    BadRequestError,
    ConflictError,
    InternalServerError,
    NotFoundError,
    PermissionDeniedError,
    RateLimitError,
    UnprocessableEntityError,
)

RETRYABLE_LLM_EXCEPTIONS = (
    APITimeoutError,
    APIConnectionError,
    RateLimitError,
    InternalServerError,
    ConflictError,
)

NON_RETRYABLE_LLM_EXCEPTIONS = (
    BadRequestError,
    AuthenticationError,
    PermissionDeniedError,
    NotFoundError,
    UnprocessableEntityError,
    APIResponseValidationError,
)


def is_retryable_llm_error(exc: Exception) -> bool:
    """Return True only for transient LLM failures worth retrying."""
    if isinstance(exc, RETRYABLE_LLM_EXCEPTIONS):
        return True
    if isinstance(exc, NON_RETRYABLE_LLM_EXCEPTIONS):
        return False

    text = str(exc).lower()
    non_retryable_patterns = (
        "prompt is too long",
        "maximum context length",
        "invalid request",
        "tool schema",
        "authentication",
        "api key",
        "permission",
        "not found",
        "unprocessable",
    )
    if any(pattern in text for pattern in non_retryable_patterns):
        return False

    retryable_patterns = (
        "timed out",
        "timeout",
        "rate limit",
        "429",
        "connection error",
        "connection reset",
        "temporarily unavailable",
        "service unavailable",
        "overloaded",
        "502",
        "503",
        "504",
    )
    return any(pattern in text for pattern in retryable_patterns)
