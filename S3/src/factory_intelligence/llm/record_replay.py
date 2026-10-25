"""Record-and-replay provider (AI-LLM-006, AI-AC-10).

Replay answers from a JSONL fixture keyed by a digest of the request and performs no
network operation at all — no connection is even opened, so "the end-to-end test does not
depend on a vendor" is a property of the code path rather than of a firewall
(``tests/integration/test_record_replay.py`` proves it with a transport that raises).

The key is a hash of everything that determines an answer: the messages, the schema, the
temperature and the output limit.  A prompt edit therefore *misses* rather than silently
replaying a stale answer, which is the failure mode that would make AI-AC-10 worthless: a
replay that kept passing after the prompt changed would look like a passing test suite
while testing nothing.

**A recording contains the prompt.**  Recording writes the messages it sent, so a
recording made against a real project's inputs is itself sensitive material — the same
data the policy engine was consulted about.  Keep recordings in ``tests/data/`` and
author them from synthetic input; never commit a recording taken from production data.
"""

from __future__ import annotations

import json
from dataclasses import asdict
from pathlib import Path
from typing import Any, Literal

from factory_foundation.api import get_logger, structure_hash

from factory_intelligence.llm.errors import LlmError
from factory_intelligence.llm.protocol import LLMProvider
from factory_intelligence.llm.tokens import estimate_text
from factory_intelligence.llm.types import (
    HealthStatus,
    ModelResponse,
    ProviderInfo,
    StructuredRequest,
    Usage,
)

_logger = get_logger(__name__)

Mode = Literal["record", "replay"]


def request_digest(request: StructuredRequest) -> str:
    """The replay key: a hash over everything that determines an answer.

    ``agent_hint.run_id`` is excluded: it differs every time and would make every
    recording single-use.  The agent and stage are kept, because a mock or replay fixture
    is keyed by them (AI-MOCK-001).
    """
    payload: dict[str, Any] = {
        "schemaName": request.schema_name,
        "temperature": request.temperature,
        "maxOutputTokens": request.max_output_tokens,
        "jsonSchema": request.json_schema,
        "messages": [_message_view(message) for message in request.messages],
    }
    if request.agent_hint is not None:
        payload["agentHint"] = {
            "agentType": request.agent_hint.agent_type,
            "stage": request.agent_hint.stage,
        }
    return structure_hash(payload)


def _message_view(message: Any) -> dict[str, Any]:
    return {
        "role": message.role,
        "content": message.content,
        # images are hashed, not inlined: a base64 screenshot would dominate the fixture
        # and the digest only needs to notice that the picture changed.
        "images": [
            {"mediaType": image.media_type, "label": image.label, "hash": structure_hash(image.data_base64)}
            for image in message.images
        ],
    }


class RecordReplayProvider:
    """Serve answers from a JSONL transcript, or build one through a live provider."""

    def __init__(
        self,
        info: ProviderInfo,
        *,
        path: Path,
        mode: Mode = "replay",
        inner: LLMProvider | None = None,
    ) -> None:
        if mode not in ("record", "replay"):
            raise ValueError(f"unknown record-replay mode {mode!r}")
        if mode == "record" and inner is None:
            raise ValueError("record mode needs an inner provider to record from")
        self._info = info
        self._path = Path(path)
        self._mode = mode
        self._inner = inner

    @property
    def info(self) -> ProviderInfo:
        return self._info

    @property
    def mode(self) -> str:
        return self._mode

    def generate_structured(self, request: StructuredRequest) -> ModelResponse:
        digest = request_digest(request)
        if self._mode == "record":
            assert self._inner is not None  # guaranteed by __init__
            response = self._inner.generate_structured(request)
            self._append(digest, request, response)
            return response
        recorded = self._lookup(digest)
        if recorded is None:
            raise LlmError(
                f"no recorded answer for this request in {self._path.name}",
                details={
                    "digest": digest,
                    "schemaName": request.schema_name,
                    "fixture": str(self._path),
                    "hint": "the prompt, the context or the schema changed; re-record",
                },
            )
        return ModelResponse(
            payload=dict(recorded.get("payload") or {}),
            raw_text=str(recorded.get("raw_text") or ""),
            usage=Usage(
                input_tokens=int(recorded.get("usage", {}).get("input_tokens") or 0),
                output_tokens=int(recorded.get("usage", {}).get("output_tokens") or 0),
            ),
            model=recorded.get("model"),
            finish_reason=recorded.get("finish_reason"),
            request_id=recorded.get("request_id"),
            truncated=bool(recorded.get("truncated")),
        )

    def count_tokens(self, text: str) -> int:
        if self._inner is not None:
            return int(self._inner.count_tokens(text))
        return estimate_text(text)

    def health(self) -> HealthStatus:
        """Replay is always healthy: it needs no network, and probing one would defeat it."""
        if self._inner is not None:
            return self._inner.health()
        return HealthStatus(
            healthy=self._path.is_file(),
            detail=None if self._path.is_file() else f"no transcript at {self._path}",
        )

    # --- transcript I/O --------------------------------------------------------

    def _lookup(self, digest: str) -> dict[str, Any] | None:
        for entry in self._entries():
            if entry.get("digest") == digest:
                response = entry.get("response")
                if isinstance(response, dict):
                    return response
        return None

    def _entries(self) -> list[dict[str, Any]]:
        if not self._path.is_file():
            return []
        entries: list[dict[str, Any]] = []
        for line in self._path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            try:
                parsed = json.loads(line)
            except json.JSONDecodeError:
                _logger.warning("ignoring an unparseable line in the replay transcript")
                continue
            if isinstance(parsed, dict):
                entries.append(parsed)
        return entries

    def _append(self, digest: str, request: StructuredRequest, response: ModelResponse) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        record = {
            "digest": digest,
            "request": {
                "schemaName": request.schema_name,
                "temperature": request.temperature,
                "maxOutputTokens": request.max_output_tokens,
                "messages": [_message_view(message) for message in request.messages],
            },
            "response": asdict(response),
        }
        with self._path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n")


__all__ = ["RecordReplayProvider", "request_digest", "Mode"]
