"""Turning an accepted answer into a submitted patch (AI-RT-013, AI-RT-016).

The runtime, not the model, calls S2: an accepted answer becomes a :class:`Patch` here, so
a model never has a channel through which a change takes effect (AI-RT-032).  Three
responsibilities sit in this module and nowhere else:

* **authorship** — the patch is ``AGENT``-authored with the run id as its reference, which
  is how S2's ``PatchAuthor.ref`` records that a machine proposed it and a human must gate
  it (AI-BR-01);
* **confirmation records** — an answer that resolved a gating question is written into the
  *follow-up* patch as ``/mapping/entries/<bk>/confirmation`` or
  ``/architecture/decisions/<id>/confirmation``, because S2 decides whether a
  low-confidence decision was confirmed by a user (BP-DEC-004);
* **S2's rejections as feedback** — a rejected patch's issues become the corrective
  validation block, since they are document-level facts the model literally cannot see
  before it submits (AI-RT-013).

The UID pool is the one S2 refusal that is *not* feedback: ``UID_POOL_EXHAUSTED`` is
replenished and resubmitted, and does not consume a retry (AI-RT-013).
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

from factory_blueprint.api import (
    AuthorKind,
    DesignStage,
    Patch,
    PatchAuthor,
    PatchContext,
    PatchOutcome,
    UidPoolExhausted,
)
from factory_foundation.api import structure_hash
from sqlalchemy.orm import Session

from factory_intelligence.agents.spec import AgentSpec
from factory_intelligence.config import settings
from factory_intelligence.deps import get_blueprint, get_clock
from factory_intelligence.dto.agent import AgentOutput
from factory_intelligence.dto.common import Answer, Issue, Question
from factory_intelligence.errors import PatchRejected
from factory_intelligence.runtime import state as run_state
from factory_intelligence.runtime.contextstore import StoredContext

#: S2's ``defs.json#/definitions/dateTime`` requires seconds; ``datetime.isoformat()``
#: drops them when they are zero, which would fail S2's schema at freeze time rather than
#: here — the least discoverable place possible.  Formatted explicitly instead.
_TIMESTAMP_FORMAT = "%Y-%m-%dT%H:%M:%SZ"

_CONFIRMATION_SUFFIX = "confirmation"

#: The two containers S2 keeps confirmation records on, with the field that identifies an
#: entry inside each — S2's keyed containers are JSON arrays, so this is the only pairing
#: that lets a confirmation be folded into the entry it belongs to.
_CONFIRMATION_CONTAINERS: dict[str, tuple[str, str]] = {
    "mapping": ("entries", "businessKey"),
    "architecture": ("decisions", "decisionId"),
}


@dataclass(frozen=True)
class Confirmation:
    """A user's answer, recorded against the object it gated (AI-RT-016, BP-DEC-004).

    ``target_path`` is the *logical* address — ``/mapping/entries/<businessKey>/confirmation``
    — which is what S2's read model reports and what a reviewer recognises.  It is what gets
    stored; it is **not** what gets sent, because S2's patch applier addresses array members
    by index (``factory_blueprint/patch/rfc6902.py:_array_index``) and nothing in its public
    API resolves a businessKey to an index.  So the record is folded into the entry this same
    patch creates, and :func:`build_patch` refuses it otherwise rather than letting S2 raise.
    """

    section: str
    container: str
    key_field: str
    entry_key: str
    value: dict[str, Any]
    target_path: str
    question_local_id: str
    answerer: str
    suggestion_id: str | None
    decision_id: str | None
    business_key: str | None

    @property
    def container_path(self) -> str:
        return f"/{self.section}/{self.container}"

    @property
    def field(self) -> str:
        return _CONFIRMATION_SUFFIX


def build_patch(
    answer: AgentOutput,
    *,
    run_id: str,
    base_version: str,
    rationale: str | None = None,
    confirmations: Sequence[Confirmation] = (),
) -> Patch:
    """Assemble the S2 patch from an accepted answer, folding in its confirmations."""
    operations = [
        dict(item)
        for item in (answer.patch.operations if answer.patch is not None else [])
    ]
    for operation in operations:
        if isinstance(operation.get("value"), dict):
            # a copy, so folding a confirmation in never edits the answer the run records
            operation["value"] = dict(operation["value"])
    stranded = [item for item in confirmations if not _fold_confirmation(operations, item)]
    if stranded:
        raise PatchRejected(
            "a confirmation could not be attached: "
            + ", ".join(item.target_path for item in stranded)
            + " is not created by this patch, and S2 addresses entries inside a keyed "
            "container by array index, which S3 cannot resolve for an entry it did not add "
            "(AI-RT-016). Add the entry in this patch, or record the confirmation in the "
            "patch that creates it.",
            details={
                "runId": run_id,
                "targets": [item.target_path for item in stranded],
                "questions": [item.question_local_id for item in stranded],
            },
        )
    return Patch(
        patch_id=_patch_id(run_id=run_id, operations=operations),
        base_version=base_version,
        author=PatchAuthor(kind=AuthorKind.AGENT, ref=run_id),
        decision_ids=answer.decision_ids,
        operations=operations,
        rationale=rationale or answer.summary,
    )


def _fold_confirmation(operations: list[dict[str, Any]], item: Confirmation) -> bool:
    """Whether one confirmation found the entry it belongs to, and was written into it."""
    for operation in operations:
        if str(operation.get("op") or "") != "add":
            continue
        if not str(operation.get("path") or "").startswith(item.container_path):
            continue
        value = operation.get("value")
        if not isinstance(value, dict):
            continue
        if str(value.get(item.key_field) or "") != item.entry_key:
            continue
        value[item.field] = dict(item.value)
        return True
    return False


def submit(
    *,
    request_project_id: str,
    draft_id: str,
    stage: DesignStage,
    patch: Patch,
    idempotency_key: str,
    session: Session,
) -> PatchOutcome:
    """Submit within the run's transaction (AI-RT-013).

    A rejected patch returns its outcome rather than raising: the caller decides whether the
    rejection buys a corrective retry, and that decision belongs to the retry accounting in
    :mod:`factory_intelligence.runtime.engine`, not here.
    """
    return get_blueprint().submit_patch(
        request_project_id,
        draft_id,
        patch,
        PatchContext(stage=stage, change_request_id=None),
        idempotency_key=idempotency_key,
        session=session,
    )


def submit_with_pool(
    *,
    project_id: str,
    draft_id: str,
    stage: DesignStage,
    patch: Patch,
    idempotency_key: str,
    session: Session,
    design_reference_environment_key: str | None,
) -> PatchOutcome:
    """Submit, replenishing the UID pool and retrying when S2 says it is empty.

    The retry reuses the same idempotency key on purpose.  ``UidPoolExhausted`` is raised by
    S2 after its own ``begin`` row was written but before the patch was accepted, so the
    surrounding transaction rolls that record back with everything else and the same key is
    genuinely unused the second time (S2's ``bp.patch.submit`` scope,
    ``factory_blueprint/draft/service.py:406``).
    """
    attempts = settings().uid_replenish_attempts
    for attempt in range(attempts + 1):
        try:
            return submit(
                request_project_id=project_id,
                draft_id=draft_id,
                stage=stage,
                patch=patch,
                idempotency_key=idempotency_key,
                session=session,
            )
        except UidPoolExhausted as exc:
            if attempt >= attempts or not design_reference_environment_key:
                raise
            # The replenishment is itself external I/O on S2's side and cannot run inside
            # this transaction, so it is deferred to the caller's gap and this raises for it
            # to be retried there; ``PoolReplenishNeeded`` carries what the gap must do.
            raise PoolReplenishNeeded(
                f"S2's UID pool for {design_reference_environment_key} is exhausted",
                details={
                    "environmentKey": design_reference_environment_key,
                    "reason": exc.error_code,
                    "attempt": attempt + 1,
                },
            ) from exc
    raise AssertionError("unreachable: the loop returns or raises")  # pragma: no cover


class PoolReplenishNeeded(Exception):
    """Internal signal: the gap must call ``replenish_uid_pool`` and re-enter the commit.

    Deliberately not a :class:`FactoryError`: it never leaves S3, and giving it an error code
    would invite a caller to treat it as a run outcome.
    """

    def __init__(self, message: str, *, details: dict[str, Any] | None = None) -> None:
        super().__init__(message)
        self.details = details or {}


def rejections_as_issues(outcome: PatchOutcome) -> list[Issue]:
    """S2's findings, in the shape the corrective prompt carries."""
    return [Issue.from_blueprint(issue) for issue in outcome.issues]


def confirmations(
    *,
    run_id: str,
    answers: Sequence[Answer],
    stored: StoredContext,
    session: Session,
) -> list[Confirmation]:
    """The AI-RT-016 half: an answered question confirms the decision it gated.

    Only an answer whose question ``affects`` a mapping or architecture entry produces a
    record, and a question already confirmed on an earlier turn is skipped — S1 resends every
    answered question of the run on each resume, so without that check the second turn would
    write the same confirmation twice.
    """
    already = run_state.confirmed_question_ids(run_id, session=session)
    out: list[Confirmation] = []
    for answer in answers:
        if answer.question_local_id in already:
            continue
        question = _find_question(stored, answer.question_local_id)
        if question is None:
            continue
        for section, container, key_field, entry_key in _confirmable_targets(question):
            target = f"/{section}/{container}/{entry_key}/{_CONFIRMATION_SUFFIX}"
            out.append(
                Confirmation(
                    section=section,
                    container=container,
                    key_field=key_field,
                    entry_key=entry_key,
                    value={
                        "questionId": answer.question_local_id,
                        "answerer": answer.answered_by,
                        "answeredAt": get_clock().now().strftime(_TIMESTAMP_FORMAT),
                    },
                    target_path=target,
                    question_local_id=answer.question_local_id,
                    answerer=answer.answered_by,
                    suggestion_id=_confirmation_suggestion_id(answer),
                    decision_id=entry_key if section == "architecture" else None,
                    business_key=entry_key if section == "mapping" else None,
                )
            )
            run_state.record_confirmation(
                run_state.require_run(run_id, session=session),
                session=session,
                question_local_id=answer.question_local_id,
                answerer=answer.answered_by,
                target_path=target,
                decision_id=entry_key if section == "architecture" else None,
                business_key=entry_key if section == "mapping" else None,
                suggestion_id=_confirmation_suggestion_id(answer),
            )
    return out


def _confirmation_suggestion_id(answer: Answer) -> str | None:
    """The evidence column holds one id; a MULTIPLE answer's first selection is enough.

    The full selection is kept in S1's answer record.  The confirmation row exists to say
    *that* the human answered, and which first option the runtime took as the choice.
    """
    if answer.suggestion_id:
        return answer.suggestion_id
    return answer.suggestion_ids[0] if answer.suggestion_ids else None


def _find_question(stored: StoredContext, local_id: str) -> Question | None:
    """The raised question, read back from the persisted context.

    It is read rather than re-derived: the model's numbering was already rewritten into
    canonical local ids when the run waited (``runtime/questions.py``), so the stored copy is
    the only place the ``affects`` of *this* question exists.
    """
    for item in stored.raised_questions:
        if item.local_id == local_id:
            return item
    return None


#: The two entry kinds S2 keeps confirmation records on, in both spellings an agent may use
#: in ``affects`` — a JSON pointer, or the dotted ``section.container.key`` form the prompts
#: ask for — mapped to ``(section, container, key field)``.  A bare businessKey is
#: deliberately *not* resolved by searching the Blueprint: the confirmation has to land on an
#: entry the run was actually shown, and guessing which section a key belongs to would write
#: a confirmation onto the wrong object.
_CONFIRMABLE_PREFIXES: tuple[tuple[tuple[str, ...], tuple[str, str, str]], ...] = (
    (("/mapping/entries/", "mapping.entries."), ("mapping", "entries", "businessKey")),
    (
        ("/architecture/decisions/", "architecture.decisions."),
        ("architecture", "decisions", "decisionId"),
    ),
)


def _confirmable_targets(question: Question) -> list[tuple[str, str, str, str]]:
    """``(section, container, key_field, entry_key)`` for each confirmable ``affects``."""
    targets: list[tuple[str, str, str, str]] = []
    for item in question.affects:
        normalised = item.strip()
        if not normalised:
            continue
        for needles, (section, container, key_field) in _CONFIRMABLE_PREFIXES:
            for needle in needles:
                if not normalised.startswith(needle):
                    continue
                # whatever follows the prefix is the entry key in both spellings; a businessKey
                # and a decisionId contain no "." or "/", so the extra splits are safe and
                # handle an affects value that went on to name a field inside the entry.
                key = normalised[len(needle) :].strip("/").split("/")[0].split(".")[0]
                if key:
                    targets.append((section, container, key_field, key))
                break
            else:
                continue
            break
    return targets


def patch_idempotency_key(run_id: str, attempt: int) -> str:
    """Deterministic per (run, attempt), so a replayed commit cannot apply twice.

    If the commit succeeds and the process dies before S1 sees the result, Temporal retries
    the activity, re-enters the commit, and S2's own ``bp.patch.submit`` idempotency answers
    with the first outcome rather than stacking the patch a second time.
    """
    return f"ai:{run_id}:a{attempt}:{structure_hash({'run': run_id, 'attempt': attempt})[7:23]}"


def spec_stage(spec: AgentSpec) -> DesignStage:
    return spec.stage


def _patch_id(*, run_id: str, operations: Sequence[dict[str, Any]]) -> str:
    """A stable patch id from the run and its content; S2 uniquifies collisions itself."""
    digest = structure_hash({"run": run_id, "ops": list(operations)})
    return f"ptch-{digest[7:39]}"


__all__ = [
    "Confirmation",
    "build_patch",
    "submit",
    "submit_with_pool",
    "rejections_as_issues",
    "confirmations",
    "patch_idempotency_key",
    "spec_stage",
    "PoolReplenishNeeded",
]
