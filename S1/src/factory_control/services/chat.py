"""In-stage dialogue, forwarded to S3 (AI-CHAT-001/002/003, FR-UI-002, CP-API-007).

S1 owns none of this conversation: the transcript is S3's artifact and the answers are S3's
model calls.  What S1 adds is the three things only it can supply — the stage the person is
standing in, the egress policy of every target environment of the project (D-07), and the
context header FR-UI-002 requires on screen at all times — and the state check that refuses a
turn on a stage that is locked or skipped.

The turn is external I/O (S3 calls a model), so it runs **outside** any S1 transaction: the
facts are read in a short transaction, the call happens in the gap, and nothing is written back
into ``cp``.  The patch a dialogue turn may produce lands in S2's draft through S3's own
submission path, which is why no patch bookkeeping appears here (AI-CHAT-002).
"""

from __future__ import annotations

from typing import Any

from factory_control import store as S
from factory_control.domain.enums import CHATABLE_STAGE_STATUSES, DIALOGUE_STAGE_TYPES
from factory_control.errors import InvalidStateError, NotFoundControlError
from factory_control.runtime import Runtime
from factory_control.services import workflow as WF


def turn(
    runtime: Runtime,
    stage_id: str,
    message: str,
    *,
    operator: str,
    language: str | None = None,
    idempotency_key: str | None = None,
) -> dict[str, Any]:
    """One dialogue turn (AI-CHAT-001), answered by S3 and projected onto the wire."""
    facts = _chatable_stage(runtime, stage_id, operator=operator)
    reply = runtime.intelligence.stage_chat(
        facts["projectId"],
        facts["stageType"],
        facts["activeDraftId"],
        message,
        _environment_policies(runtime, facts["projectId"], operator=operator),
        operator,
        language=language,
        idempotency_key=idempotency_key or f"chat:{stage_id}:{operator}",
    )
    return {
        "stageId": stage_id,
        "answer": reply.answer,
        "patchIds": list(reply.patch_ids),
        "questions": [_question(item) for item in reply.questions],
        "context": _context(facts),
    }


def transcript(runtime: Runtime, stage_id: str, *, operator: str) -> dict[str, Any]:
    """The session as S3 holds it, with S1's context header (FR-UI-002, AI-CHAT-003)."""
    facts = _with_draft(runtime, _stage_facts(runtime, stage_id, operator=operator))
    turns = runtime.intelligence.get_chat(facts["projectId"], facts["stageType"])
    return {
        "stageId": stage_id,
        "turns": [dict(item) for item in turns or []],
        "context": _context(facts),
    }


def clear(runtime: Runtime, stage_id: str, *, operator: str) -> dict[str, Any]:
    """AI-CHAT-003: the session starts again; the previous transcript stays auditable."""
    facts = _with_draft(runtime, _stage_facts(runtime, stage_id, operator=operator))
    runtime.intelligence.clear_chat(facts["projectId"], facts["stageType"])
    return {"stageId": stage_id, "cleared": True, "context": _context(facts)}


# --- the facts only S1 has -------------------------------------------------------


def _stage_facts(runtime: Runtime, stage_id: str, *, operator: str) -> dict[str, Any]:
    with runtime.transaction(operator=operator, project_id=None) as ctx:
        stage = S.get_stage(ctx.session, stage_id)
        if stage is None:
            raise NotFoundControlError(f"stage {stage_id} not found", details={"stageId": stage_id})
        workflow = S.get_workflow(ctx.session, stage.workflow_id)
        project = S.get_project(ctx.session, workflow.project_id)
        from factory_control.services.project import ensure_writable

        ensure_writable(project)
        target_keys = [t.environment_key for t in S.target_environments(ctx.session, project.id)]
        return {
            "projectId": project.id,
            "projectName": project.name,
            "workflowId": workflow.id,
            "stageId": stage.id,
            "stageType": stage.stage_type,
            "stageStatus": stage.status,
            "deliveryMode": project.delivery_mode,
            "targetEnvironmentKeys": target_keys,
            # filled by _with_draft: active_draft is a provider read, and 00 doc 6.2 forbids
            # one inside a unit of work
            "activeDraftId": None,
        }


def _with_draft(runtime: Runtime, facts: dict[str, Any]) -> dict[str, Any]:
    """The live draft the person is working in — where a requested change would go."""
    draft = WF.active_draft(runtime, facts["projectId"])
    facts["activeDraftId"] = str(draft["draftId"]) if draft else None
    return facts


def _chatable_stage(runtime: Runtime, stage_id: str, *, operator: str) -> dict[str, Any]:
    """The facts, plus the two checks that decide whether a turn may happen at all."""
    facts = _with_draft(runtime, _stage_facts(runtime, stage_id, operator=operator))
    if facts["stageType"] not in DIALOGUE_STAGE_TYPES:
        raise NotFoundControlError(
            f"stage {facts['stageType']} has no design dialogue (AI-CHAT-001)",
            details={"stageId": stage_id, "stageType": facts["stageType"], "chatable": sorted(DIALOGUE_STAGE_TYPES)},
        )
    if facts["stageStatus"] not in CHATABLE_STAGE_STATUSES:
        raise InvalidStateError(
            f"a stage that is {facts['stageStatus']} has no dialogue to join",
            details={
                "currentState": facts["stageStatus"],
                "requiredStates": sorted(CHATABLE_STAGE_STATUSES),
                "targetId": stage_id,
            },
        )
    return facts


def _environment_policies(runtime: Runtime, project_id: str, *, operator: str) -> dict[str, Any]:
    """D-07 inputs: each target environment's own LLM data policy (CP-WF-030's basis)."""
    with runtime.transaction(operator=operator, project_id=project_id) as ctx:
        keys = [t.environment_key for t in S.target_environments(ctx.session, project_id)]
    policies: dict[str, Any] = {}
    for key in keys:
        environment = runtime.execution.get_environment(key)
        if environment is not None:
            policies[key] = environment.llm_data_policy
    return policies


def _context(facts: dict[str, Any]) -> dict[str, Any]:
    """FR-UI-002: the header the panel always shows, gathered while the facts were read."""
    return {
        "projectId": facts["projectId"],
        "projectName": facts["projectName"],
        "workflowId": facts["workflowId"],
        "stage": facts["stageType"],
        "stageStatus": facts["stageStatus"],
        "deliveryMode": facts["deliveryMode"],
        "activeDraftId": facts.get("activeDraftId"),
        "targetEnvironments": list(facts["targetEnvironmentKeys"]),
    }


def _question(item: Any) -> dict[str, Any]:
    return {
        "localId": item.local_id,
        "text": item.text,
        "severity": item.severity,
        "affects": list(item.affects),
        "suggestions": [dict(entry) for entry in item.suggestions],
        "blocking": bool(item.blocking),
    }


__all__ = ["clear", "transcript", "turn"]
