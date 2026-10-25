"""LLM layer error classification (AI-RT-021, overall SRS 19.2.2).

The distinction that matters to the runtime is *retryable transport* versus *final
failure*: a timeout or a 429 is retried with backoff and does not consume the single
corrective retry AI-RT-011 grants; a refusal, a bad request or a truncated-but-
complete answer does.  Encoding that in exception types rather than in status-code
comparisons at the call site is what keeps the retry ledger auditable (AI-AC-01
asserts an exact number of model calls).
"""

from __future__ import annotations

from typing import Any

from factory_intelligence.errors import AiError

#: HTTP statuses SRS AI-RT-021 names as auto-retryable.
RETRYABLE_STATUS = frozenset({408, 409, 425, 429, 500, 502, 503, 504})


class LlmTransportError(AiError):
    """Timeout, connection loss or a retryable HTTP status: back off and retry."""

    code = "LLM_TIMEOUT"

    def __init__(
        self,
        message: str | None = None,
        *,
        status_code: int | None = None,
        retry_after_seconds: float | None = None,
        request_id: str | None = None,
        **kwargs: Any,
    ) -> None:
        super().__init__(message, **kwargs)
        self.status_code = status_code
        self.retry_after_seconds = retry_after_seconds
        # AI-LLM-004 records *every* model call, and a 429 row has to be traceable to the
        # vendor's log like any other.  The request id is the one identifier that is
        # diagnostic without being a credential, so the retryable class carries it too.
        self.request_id = request_id


class LlmError(AiError):
    """A non-retryable provider failure (AI-LLM-004 records it, the run fails)."""

    code = "LLM_ERROR"

    def __init__(
        self,
        message: str | None = None,
        *,
        status_code: int | None = None,
        request_id: str | None = None,
        **kwargs: Any,
    ) -> None:
        super().__init__(message, **kwargs)
        self.status_code = status_code
        # A provider request id is diagnostic, never a credential.  Deliberately the
        # only identifier kept: enough to correlate a vendor-side log, not enough to
        # replay anything (AI-LLM-005).
        self.request_id = request_id


class LlmSecretMissing(AiError):
    """The Secret reference named by a model registration cannot be resolved."""

    code = "SECRET_UNAVAILABLE"


__all__ = ["LlmTransportError", "LlmError", "LlmSecretMissing", "RETRYABLE_STATUS"]
