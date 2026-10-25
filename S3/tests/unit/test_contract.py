"""The contract S1 compiles against (00 doc 6.1, AI-API-001).

S1 resolves ``factory_intelligence.api.<name>`` lazily and coerces every result with
``dto.model_validate(value.model_dump())`` (``factory_control/adapters/delegating.py:53``).
Two of its DTO mirrors declare ``extra="ignore"``, which makes a renamed S3 field
*silent*: the value is dropped, the mirror keeps its default, and the stage proceeds on a
wrong assumption.  That is exactly the failure no runtime test would catch, so the
expected names are pinned here — copied from ``factory_control/ports/dto.py`` with their
line numbers, not imported, because S1 depends on S3 and not the other way round
(00 doc 5).
"""

from __future__ import annotations

import inspect
from typing import Any

from pydantic import BaseModel, ConfigDict

from factory_intelligence import api
from factory_intelligence.dto.common import Answer, Issue, Question, RejectedArtifact
from factory_intelligence.dto.request import AgentRunRequest
from factory_intelligence.dto.result import AgentRunResult, ParsedInput
from factory_intelligence.enums import AgentRunStatus, ParseStatus

#: S1/src/factory_control/ports/dto.py:201
S1_AGENT_RUN_RESULT_FIELDS = [
    "run_id",
    "status",
    "summary",
    "confidence",
    "patch_ids",
    "agent_output_artifact_id",
    "questions",
    "rejected_artifacts",
    "budget_status",
    "denial_reasons",
    "issues",
]

#: S1/src/factory_control/ports/dto.py:217
S1_PARSED_INPUT_FIELDS = [
    "status",
    "nrr_artifact_id",
    "questions",
    "rejected_artifacts",
    "parser_version",
]

#: S1/src/factory_control/ports/dto.py:72
S1_QUESTION_FIELDS = [
    "local_id",
    "text",
    "severity",
    "answer_mode",
    "affects",
    "suggestions",
    "blocking",
]

#: S1/src/factory_control/ports/dto.py:182
S1_REQUEST_FIELDS = [
    "project_id",
    "workflow_id",
    "stage_id",
    "stage",
    "agent_type",
    "draft_id",
    "scope",
    "input_artifact_ids",
    "design_reference_environment_key",
    "target_environment_policies",
    "capability_profile_artifact_id",
    "discovery_result_artifact_id",
    "operator",
    "language",
    "idempotency_key",
]

#: S1/src/factory_control/ports/dto.py:225
S1_ANSWER_FIELDS = [
    "question_local_id",
    "suggestion_id",
    "suggestion_ids",
    "suggestion_texts",
    "custom_text",
    "answered_by",
]

#: S1/src/factory_control/ports/dto.py:41 — S1 reads ``level`` (00 doc 6.10, BP-VAL-001) and
#: still accepts the legacy spellings its own REST schemas emit.
S1_ISSUE_FIELDS = [
    "issue_id",
    "rule_id",
    "level",
    "object_key",
    "object_uid",
    "description",
    "source_stage",
    "artifact_id",
    "business_key",
    "suggested_action",
]

#: S1/src/factory_control/adapters/registry.py:198 — every ``factory_intelligence.api``
#: function ``IntelligenceAdapter.METHOD_MAP`` resolves.  Nine are SRS 7's own; the tenth is
#: S1's addition on top of it (S5IF-GOV10 lists activations, and the one port name that
#: differs from the function it maps to is recorded in :data:`S1_PORT_ALIASES`).
S1_METHOD_MAP = {
    "parse_input",
    "start_agent_run",
    "resume_agent_run",
    "cancel_agent_run",
    "get_agent_run",
    "explain_issues",
    "governance",
    "run_evaluation",
    "list_activations",
    "delete_project_data",
}

#: Port name → provider function name, for the entries that are not the same word.
S1_PORT_ALIASES = {"list_prompt_activations": "list_activations"}

#: S1/src/factory_control/adapters/registry.py:212 — must be called with no UoW open.
S1_EXTERNAL_IO_METHODS = {
    "parse_input",
    "start_agent_run",
    "resume_agent_run",
    "explain_issues",
    "run_evaluation",
}

#: Built increment by increment; each increment removed what it implemented.  Empty is the
#: definition of done for S1's contract, and the assertion below keeps it empty: a name that
#: reappears here is a public function S1 would resolve at runtime and miss at import time.
PENDING_S1_METHODS: set[str] = set()


def _request_sample() -> dict[str, object]:
    """A request as S1 actually builds one: optional fields present and ``None``
    (``services/steps.py:222-236``), not omitted."""
    return {
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
        "discovery_result_artifact_id": None,
        "operator": "ops",
        "language": "zh-CN",
        "idempotency_key": "idem-1",
    }


class S1RunMirror(BaseModel):
    """S1's own mirror of a run result (``ports/dto.py:201``): its field names, every type
    left open, ``extra="ignore"`` like the real one.  That looseness is what makes a re-typed
    value disappear without an error, so the round trip is compared by value."""

    model_config = ConfigDict(extra="ignore")

    run_id: Any = None
    status: Any = None
    summary: Any = None
    confidence: Any = None
    patch_ids: Any = None
    agent_output_artifact_id: Any = None
    questions: Any = None
    rejected_artifacts: Any = None
    budget_status: Any = None
    denial_reasons: Any = None
    issues: Any = None


class S1ParsedMirror(BaseModel):
    """S1's mirror of a parse result (``ports/dto.py:217``) — which reads five of S3's ten."""

    model_config = ConfigDict(extra="ignore")

    status: Any = None
    nrr_artifact_id: Any = None
    questions: Any = None
    rejected_artifacts: Any = None
    parser_version: Any = None


def test_s1s_coercion_round_trips_every_result_type() -> None:
    """``delegating.py:53`` replaces what S3 returned with ``dto.model_validate(value.model_dump())``.

    Every field whose *name* matches can still be lost there — a value that dumps into a shape
    the mirror revalidates differently is dropped or altered without an error, and a
    ``WAITING_INPUT`` whose questions vanished is indistinguishable from a completed run.
    """
    run = AgentRunResult(
        run_id="arun-1",
        status=AgentRunStatus.WAITING_INPUT,
        summary="two answers are needed before the mapping can be patched",
        confidence=0.4,
        patch_ids=["bpch-1"],
        agent_output_artifact_id="art-output-1",
        questions=[
            Question(
                local_id="Q1",
                text="Which organisation owns this data element?",
                severity="HIGH",
                affects=["requirements.items.age-at-visit"],
                suggestions=[{"id": "s1", "text": "the facility"}],
                blocking=True,
            )
        ],
        rejected_artifacts=[
            RejectedArtifact(ref="art-2", classification="PHI_PII", reason="denied by policy")
        ],
        budget_status={"state": "WARNING", "spent": 1.0, "limit": 2.0},
        denial_reasons=[],
        issues=[Issue(issue_id="aiz-1", rule_id="AIX-002", message="patch refused")],
    )
    assert list(S1RunMirror.model_fields) == S1_AGENT_RUN_RESULT_FIELDS
    assert set(S1ParsedMirror.model_fields) == set(S1_PARSED_INPUT_FIELDS)

    dumped = run.model_dump()
    coerced = S1RunMirror.model_validate(dumped)
    # every field S1 declares must arrive unchanged; the two AI-RT-019 query fields are
    # S3's own and an ``extra="ignore"`` mirror drops them, which is the documented outcome
    assert {
        name: value for name, value in coerced.model_dump().items() if name in dumped
    } == {name: dumped[name] for name in S1_AGENT_RUN_RESULT_FIELDS}, (
        "S1's coercion changed a field it reads"
    )

    parsed = ParsedInput(
        status=ParseStatus.NEEDS_REVIEW,
        nrr_artifact_id="art-nrr-1",
        questions=[
            Question(
                local_id="Q1",
                text="Confirm the column mapping",
                severity="MEDIUM",
                affects=["requirements.items.-"],
                suggestions=[],
                blocking=False,
            )
        ],
        rejected_artifacts=[RejectedArtifact(ref="art-3", reason="unreadable sheet")],
        parser_version="CSV@1.0.0",
        parser_type="CSV",
        used_model=False,
        parse_id="ainp-1",
    )
    parsed_dump = parsed.model_dump()
    narrowed = S1ParsedMirror.model_validate(parsed_dump)
    assert narrowed.model_dump() == {
        name: parsed_dump[name] for name in S1_PARSED_INPUT_FIELDS
    }, "every field S1 reads must arrive unchanged"


def test_the_run_result_field_names_are_exactly_s1s() -> None:
    # S3 answers everything S1 reads, under those names; the two it does not read yet are
    # AI-RT-019's query fields (question ids and the last progress event), which S1's
    # ``extra="ignore"`` mirror drops rather than breaks on
    assert list(S1_AGENT_RUN_RESULT_FIELDS) == [
        name for name in AgentRunResult.model_fields if name in S1_AGENT_RUN_RESULT_FIELDS
    ]
    assert set(S1_AGENT_RUN_RESULT_FIELDS) <= set(AgentRunResult.model_fields)
    assert {"question_local_ids", "last_progress"} <= set(AgentRunResult.model_fields)


def test_the_parse_result_covers_every_field_s1_reads() -> None:
    """S3 reports more than S1 consumes (``parser_type``, ``used_model``), but never less
    and never a renamed one."""
    assert set(S1_PARSED_INPUT_FIELDS) <= set(ParsedInput.model_fields)


def test_question_answer_and_issue_shapes_match() -> None:
    assert set(S1_QUESTION_FIELDS) <= set(Question.model_fields)
    assert list(Answer.model_fields) == S1_ANSWER_FIELDS
    # an Issue's canonical fields are declared; the legacy spellings S1's mirror reads are
    # computed from them, so the two cannot drift apart
    readable = set(Issue.model_fields) | set(Issue.model_computed_fields)
    assert set(S1_ISSUE_FIELDS) <= readable, sorted(set(S1_ISSUE_FIELDS) - readable)
    assert "level" in Issue.model_fields, "00 doc 6.10 names the level field level"


def test_the_request_accepts_what_s1_sends() -> None:
    assert set(S1_REQUEST_FIELDS) <= set(AgentRunRequest.model_fields)
    parsed = AgentRunRequest.model_validate(_request_sample())
    assert parsed.project_id == "prj-1"
    assert parsed.draft_id is None


def test_api_exports_the_functions_s1_resolves() -> None:
    """A missing name surfaces at call time as PROVIDER_UNAVAILABLE inside a Temporal
    activity; this makes it a test failure instead."""
    built = S1_METHOD_MAP - PENDING_S1_METHODS
    not_callable = {name for name in built if not callable(getattr(api, name, None))}
    assert not not_callable, f"declared built but absent: {sorted(not_callable)}"

    exported = set(api.__all__)
    assert {"initialize", "MIGRATION_CONFIG"} <= exported
    assert built <= exported, f"built but not exported: {sorted(built - exported)}"


def test_initialize_accepts_s1s_argumentless_start_up_call() -> None:
    """S1/src/factory_control/bootstrap.py:117 calls ``init()`` with nothing at all."""
    required = [
        name
        for name, parameter in inspect.signature(api.initialize).parameters.items()
        if parameter.default is inspect.Parameter.empty
    ]
    assert required == []


def test_a_result_never_carries_a_status_s1_cannot_parse() -> None:
    """``factory_control/domain/enums.py:362`` is a strict six-member enum and
    ``services/workflow.py:352`` indexes it directly."""
    from factory_intelligence.enums import AgentRunStatus, RunState

    assert {member.value for member in AgentRunStatus} == {
        "COMPLETED",
        "WAITING_INPUT",
        "FAILED",
        "POLICY_DENIED",
        "BUDGET_EXCEEDED",
        "CANCELLED",
    }
    # ``RUNNING`` is the seventh value and the query's alone (AI-RT-019, AI-AC-14): a run
    # still executing is reported as executing, with its last progress.  Starting or
    # resuming still answers with one of the six, which is what S1 branches on with
    # ``AgentRunStatus(result.status)``.
    assert "RUNNING" in {member.value for member in RunState}
    assert "RUNNING" not in {member.value for member in AgentRunStatus}
    running = AgentRunResult.model_validate(
        {"run_id": "arun-1", "status": "RUNNING", "last_progress": {"step": "model_call"}}
    )
    assert running.status is RunState.RUNNING
    assert running.last_progress == {"step": "model_call"}


def test_external_io_functions_are_the_ones_s1_declares() -> None:
    """S1 refuses to call these inside its transaction and S0 refuses to run them there;
    the two lists must agree (00 doc 6.2)."""
    from factory_foundation.api import is_external_io

    built = S1_EXTERNAL_IO_METHODS - PENDING_S1_METHODS
    for name in built:
        assert is_external_io(getattr(api, name)), f"api.{name} must be marked external I/O"
