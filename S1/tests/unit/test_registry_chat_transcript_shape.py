"""``IntelligencePort.get_chat`` promises the turn list, on the real S3 path.

S3 answers ``runtime/chat.Transcript`` — a plain ``@dataclass``, which
``adapters/delegating._coerce`` leaves untouched because it only converts pydantic models,
dicts and lists.  ``services/chat.transcript`` then iterated that object, so opening a stage's
dialogue was an HTTP 500 on every real deployment while every fake, which returns the list,
passed.  Same class of drift as ``test_registry_refs_shape.py``.
"""

from __future__ import annotations

from typing import Any

import pytest

from factory_control.adapters.registry import IntelligenceAdapter

needs_s3 = pytest.mark.skipif(not __import__("importlib").util.find_spec("factory_intelligence"), reason="S3 not installed")


def _adapter(value: Any) -> IntelligenceAdapter:
    def get_chat(project_id: str, stage: str):
        del project_id, stage
        return value

    return IntelligenceAdapter(resolver=lambda package, name: get_chat)


@needs_s3
def test_real_transcript_is_forwarded_as_the_turn_list() -> None:
    from factory_intelligence.runtime.chat import Transcript

    transcript = Transcript(
        project_id="prj-1",
        stage="REQUIREMENTS",
        session_id="chs-1",
        cleared=False,
        context_artifact_id="art-1",
        turns=[{"role": "user", "content": "which fields are missing?", "at": "2026-10-21T08:00:00+00:00"}],
    )
    assert not isinstance(transcript, list), "the stand-in stopped proving anything"

    turns = _adapter(transcript).get_chat("prj-1", "REQUIREMENTS")

    assert turns == [{"role": "user", "content": "which fields are missing?", "at": "2026-10-21T08:00:00+00:00"}]
    # what services/chat.transcript does with it, and the line that raised
    assert [dict(item) for item in turns or []] == turns


def test_a_provider_that_already_answers_the_port_shape_passes_through() -> None:
    """The fake's shape, so the translation must not become a second contract to satisfy."""
    assert _adapter([{"role": "assistant", "content": "two"}]).get_chat("prj-1", "DOMAIN") == [
        {"role": "assistant", "content": "two"}
    ]
    assert _adapter(None).get_chat("prj-1", "DOMAIN") == []
