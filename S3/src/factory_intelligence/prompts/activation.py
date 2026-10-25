"""Prompt activation and its gate (AI-REG-002, AI-REG-003, AI-AC-07).

A registered prompt version is not a running one.  :class:`PromptActivationRow` names the
single version an agent type executes, and writing it is refused unless *both* gates of
AI-REG-003 have evidence in the database: an evaluation pass for this
(agent, prompt version, model) triple and a registered Golden Project regression pass.
Checking them at activation rather than at run time means a half-prepared switch fails
loudly in front of the person making it, not later inside a Temporal activity.

The regression gate is a *claim* another team records (``record_regression``).  S3 cannot
verify it — the Golden Project is the test group's asset and the run happens outside this
subsystem — so activation refuses an absent claim and stores the ref it accepted, which is
the honest form of "both results must exist" (AI-BR-09).
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import TypeVar

from factory_foundation.api import (
    ValidationError,
    current_session,
    has_active_uow,
    new_id,
    unit_of_work,
)
from sqlalchemy import select
from sqlalchemy.orm import Session

from factory_intelligence import audit_log
from factory_intelligence.config import settings
from factory_intelligence.enums import AgentType
from factory_intelligence.errors import PromptActivationBlocked, PromptNotFound
from factory_intelligence.models.eval import EvalRunRow
from factory_intelligence.models.registry import (
    PromptActivationRow,
    PromptVersionRow,
    RegressionRefRow,
)

T = TypeVar("T")


@dataclass(frozen=True)
class ActivePrompt:
    """What a run must use: one prompt version plus the model preference order."""

    agent_type: str
    prompt_version_id: str
    name: str
    version: int
    body: str
    system_rules: str
    prohibitions: list[dict[str, object]]
    model_list: list[str]
    activated_by: str
    eval_run_id: str
    regression_passed_ref: str

    @property
    def qualified_name(self) -> str:
        return f"{self.name}.v{self.version}"


def current_activation(
    agent_type: AgentType | str, *, session: Session | None = None
) -> ActivePrompt | None:
    """The active version for one agent, or ``None`` when nobody has activated one.

    ``None`` is answered with a refusal that names the agent: an un-activated agent is a
    deployment step nobody finished, not a model fault.  ``start_agent_run`` returns a
    ``FAILED`` result with no run row (so S1 keeps its manual-authoring path), while
    ``resume_agent_run``, ``stage_chat`` and ``explain_issues`` raise
    :class:`~factory_intelligence.errors.PromptNotActivated` — there the side channel has no
    result to carry a denial.  Nothing degrades to an empty placeholder any more.
    """
    wanted = AgentType(agent_type).value

    def _read(sess: Session) -> ActivePrompt | None:
        row = sess.execute(
            select(PromptActivationRow).where(PromptActivationRow.agent_type == wanted)
        ).scalars().first()
        if row is None:
            return None
        prompt = sess.get(PromptVersionRow, row.prompt_version_id)
        if prompt is None:  # pragma: no cover - a dangling FK cannot be created here
            return None
        return ActivePrompt(
            agent_type=wanted,
            prompt_version_id=prompt.id,
            name=prompt.name,
            version=prompt.version,
            body=prompt.body,
            system_rules=prompt.system_rules or "",
            prohibitions=[dict(item) for item in (prompt.prohibitions or [])],
            model_list=[str(item) for item in (row.model_list or [])],
            activated_by=row.activated_by,
            eval_run_id=row.eval_run_id,
            regression_passed_ref=row.regression_passed_ref,
        )

    return _read_only(session, _read)


def activate(
    *,
    agent_type: AgentType | str,
    prompt_name: str,
    prompt_version: int,
    model_list: list[str],
    operator: str,
    session: Session | None = None,
) -> PromptActivationRow:
    """Switch the active version, gated by AI-REG-003.

    ``model_list`` is the agent's ordered preference (AI-POL-007) and is part of what is
    being activated: a prompt that passed evaluation against model A says nothing about
    model B, so switching both at once would let an untested pairing through the gate.
    """
    if not model_list:
        raise ValidationError(
            "activation needs a model list: AI-POL-007 walks it, and an empty list means "
            "every run is refused by policy",
            code="VALIDATION_ERROR",
        )
    wanted = AgentType(agent_type).value

    def _write(sess: Session) -> PromptActivationRow:
        prompt = sess.execute(
            select(PromptVersionRow).where(
                PromptVersionRow.name == prompt_name, PromptVersionRow.version == prompt_version
            )
        ).scalars().first()
        if prompt is None:
            raise PromptNotFound(
                f"prompt {prompt_name}.v{prompt_version} is not registered",
                details={"name": prompt_name, "version": prompt_version},
            )
        if prompt.agent_type != wanted:
            raise ValidationError(
                f"prompt {prompt_name}.v{prompt_version} is declared for "
                f"{prompt.agent_type}, not {wanted}",
                code="VALIDATION_ERROR",
                details={"declaredFor": prompt.agent_type, "agentType": wanted},
            )
        eval_run = _require_eval_pass(sess, wanted, prompt, model_list)
        regression = _require_regression(sess, prompt)
        row = sess.execute(
            select(PromptActivationRow).where(PromptActivationRow.agent_type == wanted)
        ).scalars().first()
        before = None
        if row is not None:
            before = {"promptVersionId": row.prompt_version_id, "modelList": list(row.model_list)}
        else:
            row = PromptActivationRow(agent_type=wanted)
            sess.add(row)
        row.prompt_version_id = prompt.id
        row.model_list = list(model_list)
        row.activated_by = operator
        row.eval_run_id = str(eval_run.id)
        row.regression_passed_ref = str(regression.id)
        audit_log.record(
            "ai.prompt.activated",
            session=sess,
            object_type="promptActivation",
            object_id=wanted,
            before=before,
            after={
                "prompt": f"{prompt.name}.v{prompt.version}",
                "promptVersionId": prompt.id,
                "modelList": list(model_list),
                "evalRunId": eval_run.id,
                "evalScore": eval_run.score,
                "regressionRef": regression.id,
            },
            operator=operator,
            operator_is_user=True,
        )
        sess.flush()
        return row

    return _run(session, _write)


def _require_eval_pass(
    sess: Session, agent_type: str, prompt: PromptVersionRow, model_list: list[str]
) -> EvalRunRow:
    """An evaluation of *this* prompt version against one of the models being activated."""
    rows = sess.execute(
        select(EvalRunRow)
        .where(
            EvalRunRow.agent_type == agent_type,
            EvalRunRow.prompt_name == prompt.name,
            EvalRunRow.prompt_version == prompt.version,
            EvalRunRow.passed.is_(True),
        )
        .order_by(EvalRunRow.created_at.desc())
    ).scalars().all()
    for run in rows:
        if run.model not in model_list:
            continue
        if run.score >= settings().eval_pass_score:
            return run
        # AI-GOV-003: which gate failed, and against what number, in the payload rather
        # than only in the sentence — S5 must show "缺少通过的评测" without parsing prose.
        raise PromptActivationBlocked(
            f"the newest evaluation of {prompt.name}.v{prompt.version} on {run.model} "
            f"scored {run.score}, below the {settings().eval_pass_score} pass mark",
            details={
                "missing": "EVAL_SCORE",
                "agentType": agent_type,
                "prompt": f"{prompt.name}.v{prompt.version}",
                "evalRunId": run.id,
                "model": run.model,
                "actualScore": run.score,
                "requiredScore": settings().eval_pass_score,
            },
        )
    raise PromptActivationBlocked(
        f"no passing evaluation exists for {prompt.name}.v{prompt.version} with any model "
        f"on this list ({', '.join(model_list)}); run run_evaluation() first (AI-REG-003)",
        details={
            "missing": "EVAL_RUN",
            "agentType": agent_type,
            "prompt": f"{prompt.name}.v{prompt.version}",
            "modelList": list(model_list),
            "requiredScore": settings().eval_pass_score,
        },
    )


def _require_regression(sess: Session, prompt: PromptVersionRow) -> RegressionRefRow:
    row = sess.execute(
        select(RegressionRefRow)
        .where(
            RegressionRefRow.prompt_version_id == prompt.id, RegressionRefRow.passed.is_(True)
        )
        .order_by(RegressionRefRow.created_at.desc())
    ).scalars().first()
    if row is None:
        raise PromptActivationBlocked(
            f"the Golden Project regression has not been recorded as passed for "
            f"{prompt.name}.v{prompt.version}; ask the test group to register it "
            "(AI-REG-003, AI-AC-07)",
            details={
                "missing": "REGRESSION_PASS",
                "prompt": f"{prompt.name}.v{prompt.version}",
                "promptVersionId": prompt.id,
            },
        )
    return row


def record_regression(
    *,
    prompt_name: str,
    prompt_version: int,
    recorded_by: str,
    passed: bool = True,
    model: str | None = None,
    source: str = "golden-project",
    note: str | None = None,
    session: Session | None = None,
) -> RegressionRefRow:
    """Register the test group's regression result for one prompt version."""

    def _write(sess: Session) -> RegressionRefRow:
        prompt = sess.execute(
            select(PromptVersionRow).where(
                PromptVersionRow.name == prompt_name, PromptVersionRow.version == prompt_version
            )
        ).scalars().first()
        if prompt is None:
            raise PromptNotFound(
                f"prompt {prompt_name}.v{prompt_version} is not registered",
                details={"name": prompt_name, "version": prompt_version},
            )
        row = RegressionRefRow(
            id=new_id("argr"),
            prompt_version_id=prompt.id,
            model=model,
            passed=passed,
            source=source,
            recorded_by=recorded_by,
            note=note,
        )
        sess.add(row)
        sess.flush()
        return row

    return _run(session, _write)


def list_activations(*, session: Session | None = None) -> list[ActivePrompt]:
    """Every agent that currently has an active prompt — the deployment's readiness view."""

    def _read(sess: Session) -> list[ActivePrompt]:
        out: list[ActivePrompt] = []
        for agent in AgentType:
            active = current_activation(agent, session=sess)
            if active is not None:
                out.append(active)
        return out

    return _read_only(session, _read)


def list_regressions(
    *,
    prompt_name: str | None = None,
    prompt_version: int | None = None,
    session: Session | None = None,
) -> list[RegressionRefRow]:
    """Registered Golden Project results, newest first (AI-REG-003).

    Only the writer existed until now, so the precondition activation enforces -- "this prompt
    version has a regression result" -- was invisible to both consoles: a gate that cannot see its
    own rule can only pretend to apply it.
    """

    def _read(sess: Session) -> list[RegressionRefRow]:
        stmt = select(RegressionRefRow, PromptVersionRow).join(
            PromptVersionRow, PromptVersionRow.id == RegressionRefRow.prompt_version_id
        )
        if prompt_name:
            stmt = stmt.where(PromptVersionRow.name == prompt_name)
        if prompt_version:
            stmt = stmt.where(PromptVersionRow.version == int(prompt_version))
        return [row for row, _version in sess.execute(stmt.order_by(RegressionRefRow.id.desc())).all()]

    return _read_only(session, _read)


def _run(session: Session | None, action: Callable[[Session], T]) -> T:
    if session is not None or has_active_uow():
        return action(session if session is not None else current_session())
    with unit_of_work() as sess:
        return action(sess)


def _read_only(session: Session | None, action: Callable[[Session], T]) -> T:
    if session is not None:
        return action(session)
    if has_active_uow():
        return action(current_session())
    with unit_of_work() as sess:
        return action(sess)


__all__ = [
    "ActivePrompt",
    "current_activation",
    "activate",
    "record_regression",
    "list_activations",
]
