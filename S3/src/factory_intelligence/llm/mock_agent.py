"""Mock agent provider (AI-MOCK-001, M1 exit criterion).

Returns pre-scripted structured answers keyed by ``<AgentType>/<stage>``, with no network
and no cost, so M1's exit standard — "run all stages and Gates with mock agents" — is
testable before any model exists.  The policy engine still runs in front of it
(``runtime/start.py`` judges the context before choosing a provider at all), which is the
point of the SRS's "策略判定照常执行": a mock that bypassed the policy check would let M1
pass while proving nothing about the only security boundary S3 owns.

Three script shapes give the runtime tests their three failure paths:

* a plain object — the answer, i.e. an :class:`~factory_intelligence.dto.agent.AgentOutput`
  document;
* ``{"$raw": "..."}`` — text that is not valid output, which consumes the run's Schema
  failure path (AI-RT-011, AI-AC-01);
* ``{"$error": {"kind": "transport" | "final", "message": "..."}}`` — a provider failure,
  which is how the backoff of AI-RT-021 and the "policy/schema errors are not retried"
  rule are asserted.

Each key holds a *sequence*: entry 0 is served first, then entry 1, and the last entry is
repeated thereafter, so a script can hand back a Question and then the corrected answer on
resume (AI-AC-04) without needing a different mechanism per stage.
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from factory_intelligence.llm.errors import LlmError, LlmTransportError
from factory_intelligence.llm.tokens import estimate_messages, estimate_text
from factory_intelligence.llm.types import (
    HealthStatus,
    ModelResponse,
    ProviderInfo,
    StructuredRequest,
    Usage,
)


@dataclass
class MockScript:
    """Scripts for one agent/stage key, with the position already served."""

    answers: list[dict[str, Any]] = field(default_factory=list)
    served: int = 0

    def next(self) -> dict[str, Any]:
        if not self.answers:
            raise LlmError("the mock script for this key is empty")
        index = min(self.served, len(self.answers) - 1)
        self.served += 1
        return dict(self.answers[index])


@dataclass
class MockCall:
    """One recorded call, so a test can assert an exact model-call count."""

    key: str
    schema_name: str
    payload: dict[str, Any]


class MockAgentProvider:
    """An :class:`~factory_intelligence.llm.protocol.LLMProvider` that reads a script."""

    def __init__(
        self,
        info: ProviderInfo,
        *,
        scripts: Mapping[str, Sequence[dict[str, Any]]] | None = None,
        fallback: Mapping[str, Any] | None = None,
    ) -> None:
        self._info = info
        self._scripts: dict[str, MockScript] = {
            str(key): MockScript(answers=[dict(item) for item in value])
            for key, value in (scripts or {}).items()
        }
        self._fallback = dict(fallback) if fallback is not None else None
        self.calls: list[MockCall] = []

    @property
    def info(self) -> ProviderInfo:
        return self._info

    @property
    def call_count(self) -> int:
        return len(self.calls)

    def calls_for(self, key: str) -> list[MockCall]:
        return [call for call in self.calls if call.key == key]

    def reset(self) -> None:
        """Rewind every script and forget the recorded calls; for use between tests."""
        for script in self._scripts.values():
            script.served = 0
        self.calls.clear()

    def add_script(self, key: str, *answers: dict[str, Any]) -> None:
        entry = self._scripts.setdefault(key, MockScript())
        entry.answers.extend(dict(answer) for answer in answers)

    # --- the protocol ----------------------------------------------------------

    def generate_structured(self, request: StructuredRequest) -> ModelResponse:
        key = _script_key(request)
        answer = self._answer_for(key, request)
        error = answer.get("$error")
        if error is not None:
            self.calls.append(MockCall(key=key, schema_name=request.schema_name, payload={}))
            _raise_scripted(error, request_id=f"mock-{len(self.calls) + 1}")
        raw = answer.get("$raw")
        if raw is not None:
            self.calls.append(MockCall(key=key, schema_name=request.schema_name, payload={}))
            return self._respond(str(raw), "", request)
        self.calls.append(MockCall(key=key, schema_name=request.schema_name, payload=answer))
        return self._respond(json.dumps(answer, ensure_ascii=False, sort_keys=True), answer, request)

    def count_tokens(self, text: str) -> int:
        return estimate_text(text)

    def health(self) -> HealthStatus:
        return HealthStatus(healthy=True, detail="mock")

    # --- internals -------------------------------------------------------------

    def _answer_for(self, key: str, request: StructuredRequest) -> dict[str, Any]:
        script = self._scripts.get(key)
        if script is None and request.agent_hint is not None:
            # an agent-level script covers every stage it runs at
            script = self._scripts.get(request.agent_hint.agent_type)
        if script is not None:
            return script.next()
        if self._fallback is not None:
            return dict(self._fallback)
        raise LlmError(
            f"no mock script for {key!r}",
            details={
                "key": key,
                "known": sorted(self._scripts),
                "hint": "add the key to the script file, or set a fallback",
            },
        )

    def _respond(self, raw: str, payload: Any, request: StructuredRequest) -> ModelResponse:
        return ModelResponse(
            payload=payload if isinstance(payload, dict) else {},
            raw_text=raw,
            usage=Usage(
                input_tokens=estimate_messages(request.messages),
                output_tokens=estimate_text(raw),
            ),
            model=self._info.model,
            finish_reason="stop",
            request_id=f"mock-{len(self.calls) + 1}",
        )


def _script_key(request: StructuredRequest) -> str:
    if request.agent_hint is not None:
        return request.agent_hint.key
    return request.schema_name


def _raise_scripted(error: Any, *, request_id: str | None = None) -> None:
    """The scripted provider failure, shaped the way a real one arrives.

    A live endpoint answers a refusal with a request id too, so the mock gives a failed call
    one: that is what lets a test assert AI-LLM-004's "every call records its request id" on
    the error path rather than only on an answer.
    """
    document = error if isinstance(error, Mapping) else {}
    kind = str(document.get("kind") or error)
    message = document.get("message") or "scripted provider failure"
    status = document.get("status")
    if kind == "transport":
        raise LlmTransportError(str(message), status_code=int(status or 503), request_id=request_id)
    if kind == "timeout":
        raise LlmTransportError(str(message), request_id=request_id)
    raise LlmError(str(message), status_code=int(status) if status else None, request_id=request_id)


def load_scripts(path: Path) -> dict[str, list[dict[str, Any]]]:
    """Read one script file: ``{"REQUIREMENTS/REQUIREMENTS": [ {...}, ... ], ...}``."""
    document = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(document, dict):
        raise ValueError(f"mock script {path} must hold a JSON object of key -> list")
    out: dict[str, list[dict[str, Any]]] = {}
    for key, value in document.items():
        items = [value] if isinstance(value, dict) else value
        if not isinstance(items, list) or any(not isinstance(item, dict) for item in items):
            raise ValueError(f"mock script {path}: {key!r} must map to an object or a list of objects")
        out[str(key)] = [dict(item) for item in items]
    return out


def load_script_directory(root: Path) -> dict[str, list[dict[str, Any]]]:
    """Merge every ``*.json`` under ``root``; later files win per key."""
    merged: dict[str, list[dict[str, Any]]] = {}
    for path in sorted(Path(root).glob("*.json")):
        merged.update(load_scripts(path))
    return merged


def mock_provider(
    info: ProviderInfo | None = None,
    *,
    scripts: Mapping[str, Sequence[dict[str, Any]]] | None = None,
    script_path: Path | None = None,
    fallback: Mapping[str, Any] | None = None,
) -> MockAgentProvider:
    """Build a mock provider from either inline scripts or a script file."""
    from factory_intelligence.enums import ProviderClass, ProviderKind
    from factory_intelligence.llm.types import Capabilities

    resolved = info or ProviderInfo(
        provider="mock",
        provider_class=ProviderClass.LOCAL_ONLY,
        model="mock-agent",
        capabilities=Capabilities(structured_output=True, vision=True, max_context_tokens=1_000_000),
        source=ProviderKind.MOCK.value,
    )
    merged: dict[str, list[dict[str, Any]]] = {
        str(key): [dict(item) for item in value] for key, value in (scripts or {}).items()
    }
    if script_path is not None:
        merged.update(load_scripts(script_path))
    return MockAgentProvider(resolved, scripts=merged, fallback=fallback)


__all__ = [
    "MockAgentProvider",
    "MockCall",
    "MockScript",
    "load_scripts",
    "load_script_directory",
    "mock_provider",
]
