"""The engine's two small, decision-shaped helpers: a degraded similar-context and the
corrective feedback wording (AI-AG-011, AI-RT-011/021).

Neither needs a database or a model: ``_similar`` reads one artifact through a seam we can
patch, and ``_append_feedback`` is a pure function of a ``Turn``'s context and report.  Both
are the places where the engine *silently used to swallow* a condition it should have shown a
reviewer -- a discovery result it could not read, and an answer that broke off at the token
ceiling -- so they are pinned here without a run around them.
"""

from __future__ import annotations

from typing import Any

import pytest
from factory_foundation.api import DataClassification

from factory_intelligence import artifacts
from factory_intelligence.context.builder import BuiltContext
from factory_intelligence.dto.request import AgentRunRequest
from factory_intelligence.output.errors import LADDER_ENVELOPE, Finding, ValidationReport
from factory_intelligence.runtime import engine
from factory_intelligence.runtime.engine import Turn


def _request(discovery_artifact_id: str) -> AgentRunRequest:
    return AgentRunRequest.model_validate(
        {
            "project_id": "prj-1",
            "workflow_id": "wf-1",
            "stage_id": "stg-1",
            "stage": "MAPPING",
            "agent_type": "MAPPING",
            "draft_id": None,
            "scope": [],
            "input_artifact_ids": [],
            "design_reference_environment_key": None,
            "target_environment_policies": {},
            "capability_profile_artifact_id": None,
            "discovery_result_artifact_id": discovery_artifact_id,
            "operator": "ops",
            "idempotency_key": "sim:1",
        }
    )


# --- _similar: an unreadable discovery result is reported, not swallowed -------------


def test_similar_degrades_to_empty_with_a_warning_when_the_discovery_is_unreadable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The old code hand-rolled ``json.loads`` and returned ``[]`` on failure, agreeing with
    nothing else and hiding the gap.  Now a corrupt artifact still yields no records, but a
    WARNING surfaces so a brownfield reviewer can see the reuse evidence was missing, not empty.
    """
    monkeypatch.setattr(
        artifacts,
        "read_bytes",
        lambda _artifact_id: (b"{ this is not valid json", DataClassification.INTERNAL, "application/json"),
    )

    records, warning = engine._similar(_request("art-discovery"), None)

    assert records == [], "an unreadable discovery compares against nothing"
    assert warning is not None, "the degradation is visible, not silent"
    assert warning.level == "WARNING"
    assert warning.rule_id == "AI-AG-011"
    assert "discovery" in warning.message.lower()


def test_similar_carries_no_warning_for_a_readable_discovery(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A readable result is not a warning, even when there is nothing proposed to match it."""
    monkeypatch.setattr(
        artifacts,
        "read_bytes",
        lambda _artifact_id: (
            b'{"objects": [{"kind": "dataElement", "code": "X", "name": "Weight"}]}',
            DataClassification.INTERNAL,
            "application/json",
        ),
    )

    records, warning = engine._similar(_request("art-discovery"), None)

    assert warning is None, "a readable artifact is not a degraded context"
    assert records == [], "nothing was proposed, so nothing matched"


def test_similar_is_empty_and_unwarned_without_a_discovery_artifact() -> None:
    """No discovery id means no similar-context was ever on the table -- not a failure."""
    records, warning = engine._similar(_request(""), None)
    assert records == [] and warning is None


# --- _append_feedback: a truncated answer is called out as truncated ----------------


def _turn(*, truncated: bool) -> Turn:
    report = ValidationReport()
    report.stopped_at = LADDER_ENVELOPE
    report.add(Finding(rule_id="AIX-999", ladder=LADDER_ENVELOPE, message="the answer was not JSON"))
    turn = Turn(
        context=BuiltContext(),
        thresholds=None,  # type: ignore[arg-type]
        policy=None,  # type: ignore[arg-type]
        selection=None,  # type: ignore[arg-type]
        model=None,
        provider=None,
    )
    turn.report = report
    turn.truncated = truncated
    return turn


def _feedback_text(turn: Turn) -> Any:
    engine._append_feedback(turn)
    return turn.context.messages[-1].content


def test_the_corrective_feedback_says_so_when_the_answer_was_truncated() -> None:
    """AI-RT-021: an answer that broke off at max_output_tokens is a length problem, not a
    refusal the model should 'fix' among its findings -- so the retry is told to compress and
    return a complete object, in those words."""
    text = _feedback_text(_turn(truncated=True))
    assert "truncat" in text.lower()
    assert "max_output_tokens" in text
    assert "complete JSON" in text
    # the findings are still handed back, after the truncation note
    assert "AIX-999" not in text or "the answer was not JSON" in text


def test_the_corrective_feedback_stays_plain_when_the_answer_was_not_truncated() -> None:
    """The truncation wording is conditional: a genuine schema refusal must not be told it ran
    out of tokens, which would send the model off to shorten output that was already complete."""
    text = _feedback_text(_turn(truncated=False))
    assert "truncat" not in text.lower()
    assert "max_output_tokens" not in text
    assert "previous answer was refused" in text.lower()
