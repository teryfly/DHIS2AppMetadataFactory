"""The stage-list aggregates, computed once per workflow (S5IF-FLD01 【裁决】).

UI-WB-002 shows seven facts per stage.  S5 cannot assemble them itself — that would be
about forty requests per workflow and would make the navigation infer business state,
which UI-BR-02 forbids — and S1 must not do it per stage either: the ruling says the
whole stage list is computed in **one pass**.  So this module takes the stage rows it was
handed and answers with one aggregate per stage, from three grouped queries regardless of
how many stages or runs the workflow has.

Nothing here calls a provider.  ``check_capability_freshness`` and the S4 environment
reads reach DHIS2, and this runs on the request that paints the navigation
(CP-NFR-03 P95 < 500 ms); each item therefore reads S1's own record of what happened,
which is also what makes the answer stable after the event stream has been retained away.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import case, func, select

from factory_control.domain import checklist as C
from factory_control.domain.enums import QuestionStatus, StageStatus, StageType
from factory_control.models import Question, Stage, StageRun


def rollups(
    session: Any,
    workflow_id: str,
    stages: list[Stage],
    *,
    language: str = "en",
    active_draft_id: str | None = None,
    current_stage_id: str | None = None,
) -> dict[str, dict[str, Any]]:
    """``{stage_id: aggregate}`` for every stage of one workflow, in three queries.

    ``active_draft_id`` is the project's live draft, resolved once by the caller: a design
    stage names the draft it produced its artifacts from, and the current stage names the
    live one even before it recorded a run — S5 must not guess an id (CP-WF-031).
    """
    stage_ids = [stage.id for stage in stages]
    if not stage_ids:
        return {}

    questions = _question_counts(session, workflow_id)
    runs = _stage_runs(session, stage_ids)
    parsed = any(_is_parse_row(row) for row in runs.values())

    design_order, run_order = _orders(stages)
    out: dict[str, dict[str, Any]] = {}
    for stage in stages:
        in_run = stage.deployment_run_id is not None
        stage_type = StageType(stage.stage_type)
        order = run_order if in_run else design_order
        position = order.index(stage_type) if stage_type in order else -1
        prior = order[position - 1] if position > 0 else None
        prior_stage = _sibling(stages, prior, in_run=in_run, run_id=stage.deployment_run_id)
        facts = C.StageFacts(
            stage_type=stage_type,
            status=stage.status,
            in_run=in_run,
            output_artifact_ids=list(stage.output_artifact_ids or []),
            prior_stage_done=None if prior_stage is None else prior_stage.status in _DONE_STATUSES,
            prior_stage_produced=None if prior_stage is None else bool(prior_stage.output_artifact_ids),
            project_has_parsed_input=parsed,
            run_recorded_capability_profile=_profile_recorded(runs, stages, stage) if in_run else False,
        )
        row = runs.get(stage.id)
        out[stage.id] = {
            "inputCompleteness": C.completeness(facts, language),
            "artifactCount": len(stage.output_artifact_ids or []),
            "openQuestionCount": questions.get(stage.id, (0, 0))[0],
            "highOpenQuestionCount": questions.get(stage.id, (0, 0))[1],
            "blockingIssueCount": _blocking_count(row, stage),
            "lastRunAt": row.created_at if row is not None else None,
            "currentArtifactVersion": _current_version(stage),
            "activeDraftId": _draft_of(stage) or (
                active_draft_id if (stage.id == current_stage_id and not in_run) else None
            ),
        }
    return out


def _draft_of(stage: Stage) -> str | None:
    """The draft this stage opened, when its own input reference says so."""
    ref = dict(stage.input_blueprint_ref or {})
    return str(ref["id"]) if str(ref.get("kind") or "") == "DRAFT" and ref.get("id") else None


#: A stage that is finished in the sense that the next one may start.
_DONE_STATUSES = (StageStatus.PASSED.value, StageStatus.SKIPPED.value)


def _is_parse_row(row: StageRun) -> bool:
    """A step run that recorded parsing an input document (S5IF-INP01)."""
    return str(dict(row.result or {}).get("kind")) == "inputParse"


def _question_counts(session: Any, workflow_id: str) -> dict[str, tuple[int, int]]:
    """Open and open-HIGH Question counts, grouped by stage in one query (CP-Q-006)."""
    open_case = case(
        (Question.status == QuestionStatus.OPEN.value, 1),
        else_=0,
    )
    high_open_case = case(
        ((Question.status == QuestionStatus.OPEN.value) & (Question.severity == "HIGH"), 1),
        else_=0,
    )
    rows = session.execute(
        select(
            Question.stage_id,
            func.sum(open_case),
            func.sum(high_open_case),
        )
        .where(Question.workflow_id == workflow_id)
        .group_by(Question.stage_id)
    ).all()
    return {str(row[0]): (int(row[1] or 0), int(row[2] or 0)) for row in rows if row[0]}


def _stage_runs(session: Any, stage_ids: list[str]) -> dict[str, StageRun]:
    """The latest StageRun per stage — one query for the whole stage list.

    The row carries what S1 recorded when that attempt ran: its start time, its parse
    outcome and, for a run step, the blocking count and the capability profile it used.
    """
    rows = session.execute(
        select(StageRun).where(StageRun.stage_id.in_(stage_ids)).order_by(StageRun.attempt)
    ).scalars()
    latest: dict[str, StageRun] = {}
    for row in rows:
        latest[row.stage_id] = row  # ordered by attempt, so the last one wins
    return latest


def _blocking_count(row: StageRun | None, stage: Stage) -> int:
    """Counted from S1's own execution record, not from a provider (S5IF-FLD01)."""
    result = dict(row.result or {}) if row is not None else {}
    recorded = result.get("blockingIssueCount")
    if isinstance(recorded, int):
        return recorded
    feedback = dict(stage.feedback or {})
    issues = feedback.get("issues")
    if isinstance(issues, list):
        return sum(
            1
            for issue in issues
            if isinstance(issue, dict) and str(issue.get("level") or issue.get("severity")) == "BLOCKING"
        )
    return 0


def _current_version(stage: Stage) -> str | None:
    """The Blueprint version or draft hash the stage's artifacts were produced from."""
    ref = dict(stage.input_blueprint_ref or {})
    return str(ref["hash"]) if ref.get("hash") else (str(ref["id"]) if ref.get("id") else None)


def _profile_recorded(runs: dict[str, StageRun], stages: list[Stage], stage: Stage) -> bool:
    """Did R1 of *this* Run record a capability profile?

    S1 keeps the profile id on the BIND_COMPILE step's result.  Asking S4 instead would
    call ``check_capability_freshness``, which reaches DHIS2 — forbidden on this path.
    """
    bind = _sibling(stages, StageType.BIND_COMPILE, in_run=True, run_id=stage.deployment_run_id)
    if bind is None:
        return False
    row = runs.get(bind.id)
    if row is None:
        return False
    return bool(dict(row.result or {}).get("capabilityProfileArtifactId"))


def _orders(stages: list[Stage]) -> tuple[list[StageType], list[StageType]]:
    """The stage order of each line, restricted to the stages this workflow has.

    ``StageType`` order comes from the domain, not from the rows: a Run created after a
    MAJOR change can hold stages inserted in a different order than they run.
    """
    design = [
        stage_type
        for stage_type in C.order_for(StageType.REQUIREMENTS, in_run=False)
        if any(
            s.stage_type == stage_type.value and s.deployment_run_id is None for s in stages
        )
    ]
    run = [
        stage_type
        for stage_type in C.order_for(StageType.BIND_COMPILE, in_run=True)
        if any(s.stage_type == stage_type.value and s.deployment_run_id is not None for s in stages)
    ]
    return design, run


def _sibling(
    stages: list[Stage], wanted: StageType | None, *, in_run: bool, run_id: str | None
) -> Stage | None:
    if wanted is None:
        return None
    for stage in stages:
        if stage.stage_type != wanted.value:
            continue
        if in_run and stage.deployment_run_id == run_id:
            return stage
        if not in_run and stage.deployment_run_id is None:
            return stage
    return None


__all__ = ["rollups"]
