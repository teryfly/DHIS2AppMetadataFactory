"""Booking the model calls that no run owns (AI-NFR-03, AI-LLM-004).

:mod:`factory_intelligence.runtime.engine` writes a usage row after every call because it
owns a run, and :mod:`factory_intelligence.runtime.explain` does the same for an explanation.
Two paths reach those very providers with no run row behind them: a parse extracts an input
file from :mod:`factory_intelligence.parsing.service`, and an evaluation scores a prompt from
:mod:`factory_intelligence.eval.runner`.  AI-NFR-03 is a percentage, not a per-entry-point
rule: *100%* of model calls carry a usage and audit record.

So the ledger is attached to the provider rather than to each caller.  Wrapping at the one
place a provider is resolved covers the document extractor, the vision extractor and any
extractor added later, and leaves those modules with no reason to know a database exists.
The row is written in its own short transaction (as :func:`~factory_intelligence.cost.usage.record_call`
does by default), because a parse that dies half-way still paid for the calls it made.  An
evaluation's row names no project — the spend is real, the project is not (see
:attr:`factory_intelligence.models.call.ModelCallRow.project_id`).
"""

from __future__ import annotations

from dataclasses import replace
from typing import Any

from factory_foundation.api import get_logger

from factory_intelligence.cost import exchange as exchange_log
from factory_intelligence.cost.usage import CallContext, record_call
from factory_intelligence.deps import get_clock
from factory_intelligence.llm.errors import LlmError, LlmTransportError
from factory_intelligence.llm.protocol import LLMProvider
from factory_intelligence.llm.registry import RegisteredModel
from factory_intelligence.llm.types import (
    HealthStatus,
    ModelResponse,
    ProviderInfo,
    StructuredRequest,
    Usage,
)

_logger = get_logger(__name__)


class RecordingProvider:
    """A provider that books every call it serves, including the ones that fail.

    Structurally a :class:`~factory_intelligence.llm.protocol.LLMProvider`: it forwards
    ``info`` untouched so the capability check the caller performs still describes the model
    that will actually answer, and not this wrapper.  It also totals what it booked, so a
    caller that has something of its own to record — an evaluation's summary row — reads the
    figures off the one place they were measured instead of keeping a parallel count.
    """

    def __init__(
        self,
        provider: LLMProvider,
        *,
        model: RegisteredModel,
        context: CallContext,
        context_artifacts: tuple[dict[str, Any], ...] = (),
        operator: str | None = None,
    ) -> None:
        self._provider = provider
        self._model = model
        self._context = context
        self._context_artifacts = context_artifacts
        self._operator = operator
        self._calls = 0
        self._usage = Usage()

    @property
    def info(self) -> ProviderInfo:
        return self._provider.info

    @property
    def calls(self) -> int:
        """How many calls this wrapper has booked — the count a summary reports."""
        return self._calls

    @property
    def usage(self) -> Usage:
        """The tokens answered so far, summed over the calls it booked."""
        return self._usage

    def count_tokens(self, text: str) -> int:
        return self._provider.count_tokens(text)

    def health(self) -> HealthStatus:
        return self._provider.health()

    def generate_structured(self, request: StructuredRequest) -> ModelResponse:
        self._calls += 1
        started = get_clock().now()
        sequence = self._calls
        # The transcript is a diagnostic, not part of the answer: a failure to write it must
        # not turn a working parse into a failed one.
        exchange_id: str | None = None
        try:
            exchange_id = exchange_log.open_exchange(
                replace(self._context, sequence=sequence), model=self._model, request=request
            )
        except Exception:  # noqa: BLE001 - logged, never fatal
            _logger.warning("could not open the parse exchange log", exc_info=True)
        try:
            response = self._provider.generate_structured(request)
        except (LlmTransportError, LlmError) as exc:
            self._close_exchange(exchange_id, started, response=None, error=exc)
            self._book(
                None,
                request,
                started,
                outcome="ERROR",
                error=str(exc.message),
                request_id=exc.request_id,
            )
            raise
        except BaseException as exc:
            # A cancellation or a provider bug still leaves a row that stops saying IN_FLIGHT.
            self._close_exchange(exchange_id, started, response=None, error=exc)
            raise
        self._close_exchange(exchange_id, started, response=response, error=None)
        self._book(response, request, started, outcome="OK", error=None, request_id=None)
        return response

    def _close_exchange(
        self,
        exchange_id: str | None,
        started: Any,
        *,
        response: ModelResponse | None,
        error: BaseException | None,
    ) -> None:
        if exchange_id is None:
            return
        latency_ms = int((get_clock().now() - started).total_seconds() * 1000)
        try:
            exchange_log.close_exchange(
                exchange_id, response=response, error=error, latency_ms=latency_ms
            )
        except Exception:  # noqa: BLE001 - logged, never fatal
            _logger.warning("could not finish the parse exchange log", exc_info=True)

    def _book(
        self,
        response: ModelResponse | None,
        request: StructuredRequest,
        started: Any,
        *,
        outcome: str,
        error: str | None,
        request_id: str | None,
    ) -> None:
        if response is not None:
            self._usage = Usage(
                input_tokens=self._usage.input_tokens + response.usage.input_tokens,
                output_tokens=self._usage.output_tokens + response.usage.output_tokens,
            )
        record_call(
            context=replace(self._context, sequence=self._calls),
            model=self._model,
            response=response,
            temperature=request.temperature,
            max_output_tokens=request.max_output_tokens,
            latency_ms=int((get_clock().now() - started).total_seconds() * 1000),
            outcome=outcome,
            error=error,
            request_id=request_id,
            context_artifacts=self._context_artifacts,
            operator=self._operator,
        )


__all__ = ["RecordingProvider"]
