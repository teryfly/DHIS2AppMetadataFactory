"""The input-parse service (AI-INP-001 … AI-INP-012, AC-REQ-01, AC-REQ-02, SRS 7.1).

:func:`run_parse` is the whole of S3's input side and the only function S1 needs from it: one
business artifact in, one ``NRR`` artifact plus one ``ai.input_parse`` row plus one audit event
out.  Every ordering decision inside it answers to a requirement rather than to taste:

1. **Idempotency first** (AI-INP-012, 00 doc 6.3/6.4).  ``T1`` spends the key and reads the
   artifact *metadata* in one transaction; a completed key replays the first result without
   running a parser or reaching for a model.  A hard metadata failure rolls that transaction
   back, so a key is never spent by a request that never began.  The digest covers the
   caller's intent only — a policy edit or a new model registration between two attempts of
   one request is not a different request.
2. **Routing from the tables, never from a preference** (AI-INP-003, AI-BR-02).
   :func:`route_parser` decides from :mod:`factory_intelligence.parsing.dispatch` and the
   model-assisted tables below, and the deterministic branch re-checks the type that actually
   ran against :data:`factory_intelligence.enums.DETERMINISTIC_PARSERS`.  A spreadsheet
   reaching a model is therefore a contradiction in the code, not a caller's mistake —
   including a caller who sets ``options.parser="MARKDOWN"`` over a CSV.
3. **Policy before a byte leaves** (AI-INP-007, AI-POL-001/003).  PHI_PII is refused before the
   engine is consulted at all (AC-POL-02: no relaxation changes it), then the *whole*
   prospective document is judged for the registered models in preference order; a refusal
   returns ``POLICY_DENIED`` naming every rejected artifact and every model that said no, and
   nothing is truncated to make a context fit (AI-BR-03).
4. **Merge and gate before anything is written** (AI-INP-008/009).  Fragments are merged by
   :mod:`factory_intelligence.parsing.merge`, which never picks a winner, and the confidence
   gate holds back whatever a model guessed and whatever two sources disagree about.  Only
   then is the artifact staged.
5. **Every parse is recorded** (AI-INP-004), result or not: the row carries the input hash, the
   parser identity and version, whether a model was involved, and the outcome.

Staging and registration sit on opposite sides of the transaction boundary exactly as
:mod:`factory_intelligence.runtime.start` arranges them: ``T1`` (idempotency + metadata) → gap
(bytes, parsers, the model calls, ``stage_json``) → ``T2`` (register, row, audit, policy
decision, ``idempotency.complete``).  :mod:`factory_intelligence.artifacts` raises on any other
arrangement, and D-11 is why: a parse either exists completely or not at all.

A failure the SRS turns into a *status* — an unmatched format, a scanned document, a policy
refusal — comes back as ``FAILED`` or ``POLICY_DENIED`` with a human-readable next step
(AI-INP-003a), because S1 maps a status onto a REST response far more reliably than it maps an
exception it cannot inspect.  Cancellation and a hard S0 failure propagate after releasing the
key: neither is a parse outcome, and masking a bug would hide it from the operator.
"""

from __future__ import annotations

import base64
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from factory_foundation.api import (
    ArtifactInfo,
    CancellationToken,
    CancelledError,
    DataClassification,
    FactoryError,
    IdempotencyStatus,
    NotFoundError,
    ProgressReporter,
    StagingHandle,
    current_session,
    external_io,
    get_artifact,
    has_active_uow,
    idempotency,
    new_id,
    structure_hash,
    unit_of_work,
)

#: the module already spells ``select`` as the policy engine's function, so the
#: SQLAlchemy one is aliased rather than shadowed
from sqlalchemy import select as sql_select
from sqlalchemy.orm import Session

from factory_intelligence import artifacts, audit_log
from factory_intelligence.config import settings
from factory_intelligence.context.thresholds import GENERATION_POLICY_SECTION, from_entries
from factory_intelligence.cost.recording import RecordingProvider
from factory_intelligence.cost.usage import CallContext
from factory_intelligence.deps import get_blueprint, get_llm_provider
from factory_intelligence.dto.common import Issue, Question, RejectedArtifact
from factory_intelligence.dto.request import ParseOptions
from factory_intelligence.dto.result import ParsedInput
from factory_intelligence.enums import (
    DETERMINISTIC_PARSERS,
    AgentType,
    ItemSourceKind,
    Milestone,
    NrrStatus,
    ParserType,
    ParseStatus,
    PolicyResult,
    ProviderClass,
)
from factory_intelligence.errors import (
    AgentRunInProgress,
    AiError,
    ContextContainsPhi,
    ContextTooLarge,
    ProviderUnavailable,
)
from factory_intelligence.feature import require_milestone
from factory_intelligence.llm.protocol import LLMProvider
from factory_intelligence.llm.registry import candidates_for_agent, registered_model
from factory_intelligence.llm.tokens import estimate_text
from factory_intelligence.models.nrr import NrrEntryRow
from factory_intelligence.models.parse import InputParseRow
from factory_intelligence.models.policy import PolicyDecisionRow
from factory_intelligence.parsing import dispatch, nrr
from factory_intelligence.parsing.base import Fragment, NrrEntry, ParseError, Parser
from factory_intelligence.parsing.confidence import apply_gate, check_confidence_parity
from factory_intelligence.parsing.documents import (
    DocumentText,
    docx_extract,
    llm_extract,
    markdown_extract,
    pdf_extract,
)
from factory_intelligence.parsing.images import vision_extract
from factory_intelligence.parsing.merge import (
    Conflict,
    MergeResult,
    cross_source_conflicts,
    merge_fragments,
)
from factory_intelligence.policy.engine import evaluate
from factory_intelligence.policy.model import ContextItem, DeniedItem, EffectivePolicy
from factory_intelligence.policy.orgpolicy import effective_policy
from factory_intelligence.policy.selection import CandidateModel, Selection, select
from factory_intelligence.registries import IdemScope
from factory_intelligence.runtime.progress import (
    STEP_COMPLETED,
    STEP_CONTEXT,
    STEP_MODEL_CALL,
    STEP_POLICY,
    STEP_QUESTIONS,
    STEP_STARTED,
    Progress,
)

#: AI-INP-002's document and image rows have no deterministic reader, so the extraction itself
#: is the parser and this is its version.  The prompt and schema that actually ran are hashed
#: into the parse notes by ``llm_extract``, so a prompt edit shows up in the parse record
#: rather than only in a changed answer (AI-INP-004, AI-RT-001).
MODEL_PARSER_VERSION = "1.0.0"

#: The artifact types S3 may parse.  ``DISCOVERY_RESULT`` is the "发现结果" half of AI-INP-002's
#: DHIS2-metadata row; anything else is refused rather than guessed at, because parsing one of
#: the factory's own products would make S3's answer its own input.
PARSEABLE_INPUT_TYPES = frozenset({"BUSINESS_INPUT", "DISCOVERY_RESULT"})

#: The registered model list that serves extraction (AI-REG-004).  The NRR is the Requirements
#: stage's input (AI-INP-001), so that agent's preference order is the one a parse walks;
#: inventing a ninth pseudo-agent would leave the list nothing could register.
EXTRACTION_AGENT = AgentType.REQUIREMENTS

#: ``ai.input_parse.parser_type`` when routing refused before any parser ran.
UNROUTED = "none"

#: Suffixes of AI-INP-002's model-assisted rows.  ``dispatch`` deliberately claims none of them
#: — its docstring says the model-assisted suffixes "belong to whoever implements them" — and
#: this table is that half.  Nothing here may gain a deterministic entry without amending the
#: SRS, and a name a deterministic parser covers never reaches it (AI-INP-003).
MODEL_PARSERS_BY_SUFFIX: Mapping[str, ParserType] = {
    # 文档 — deterministic text extraction plus LLM extraction
    ".md": ParserType.MARKDOWN,
    ".markdown": ParserType.MARKDOWN,
    ".txt": ParserType.MARKDOWN,
    # a drawing-tool export the user produced (AI-INP-002's 绘图工具 row) is text
    ".svg": ParserType.MARKDOWN,
    ".xml": ParserType.MARKDOWN,
    ".html": ParserType.MARKDOWN,
    ".htm": ParserType.MARKDOWN,
    ".docx": ParserType.DOCX,
    ".doc": ParserType.DOCX,
    ".pdf": ParserType.PDF,
    # 图片 — visual model extraction
    ".png": ParserType.IMAGE,
    ".jpg": ParserType.IMAGE,
    ".jpeg": ParserType.IMAGE,
    ".gif": ParserType.IMAGE,
    ".bmp": ParserType.IMAGE,
    ".webp": ParserType.IMAGE,
}

#: The same rows by media type, for an artifact registered with no filename.
MODEL_PARSERS_BY_MEDIA_TYPE: Mapping[str, ParserType] = {
    "text/markdown": ParserType.MARKDOWN,
    "text/plain": ParserType.MARKDOWN,
    "text/html": ParserType.MARKDOWN,
    "image/svg+xml": ParserType.MARKDOWN,
    "application/xml": ParserType.MARKDOWN,
    "application/msword": ParserType.DOCX,
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document": ParserType.DOCX,
    "application/pdf": ParserType.PDF,
    "image/png": ParserType.IMAGE,
    "image/jpeg": ParserType.IMAGE,
    "image/gif": ParserType.IMAGE,
    "image/webp": ParserType.IMAGE,
}

#: ``options.exported_as``.  AI-INP-002's drawing-tool row says the user exports first and the
#: export decides the handling, so a declared export format outranks the file name.
EXPORTED_AS_PARSERS: Mapping[str, ParserType] = {
    "png": ParserType.IMAGE,
    "jpg": ParserType.IMAGE,
    "jpeg": ParserType.IMAGE,
    "gif": ParserType.IMAGE,
    "svg": ParserType.MARKDOWN,
    "xml": ParserType.MARKDOWN,
    "html": ParserType.MARKDOWN,
}

#: Native drawing-tool files have no reader at all in MVP: the SRS row asks for an export, so
#: the refusal names what to export to (AI-INP-003a's "tell the user the next step").
DRAWING_TOOL_SUFFIXES = frozenset({".drawio", ".vsdx"})

#: AI-INP-002's last row — 既有前端代码 / API 文档 — is scheduled for M3 (SRS 12).  ``.json``
#: belongs here because a JSON file *without* a DHIS2 marker is that row's OpenAPI document,
#: which dispatch correctly declines to claim: the two share an extension and sit on opposite
#: rows, so the deterministic claim is tried first and only an unclaimed JSON lands here.
LATE_MILESTONE_PARSERS_BY_SUFFIX: Mapping[str, ParserType] = {
    ".openapi.json": ParserType.OPENAPI,
    ".json": ParserType.OPENAPI,
    ".yaml": ParserType.OPENAPI,
    ".yml": ParserType.OPENAPI,
    ".py": ParserType.SOURCE_CODE,
    ".java": ParserType.SOURCE_CODE,
    ".kt": ParserType.SOURCE_CODE,
    ".ts": ParserType.SOURCE_CODE,
    ".tsx": ParserType.SOURCE_CODE,
    ".js": ParserType.SOURCE_CODE,
    ".vue": ParserType.SOURCE_CODE,
    ".go": ParserType.SOURCE_CODE,
    ".rb": ParserType.SOURCE_CODE,
    ".cs": ParserType.SOURCE_CODE,
    ".c": ParserType.SOURCE_CODE,
    ".h": ParserType.SOURCE_CODE,
}

#: The parser types of that row, so an ``options.parser`` naming one is refused here too.
LATE_MILESTONE_PARSERS = frozenset({ParserType.SOURCE_CODE, ParserType.OPENAPI})

_IMAGE_MAGIC = (b"\x89PNG\r\n\x1a\n", b"\xff\xd8\xff", b"GIF87a", b"GIF89a", b"RIFF")

#: The parser types a model is involved in: :data:`DETERMINISTIC_PARSERS`' own complement, so
#: a new model-assisted type cannot be left out of it by forgetting to edit a second list.
MODEL_PARSERS: frozenset[ParserType] = frozenset(ParserType) - DETERMINISTIC_PARSERS


# --- what the three phases pass along ------------------------------------------


@dataclass(frozen=True)
class _Inputs:
    """What ``T1``'s metadata read established about this input (AI-INP-001)."""

    artifact_id: str
    project_id: str
    workflow_id: str | None
    artifact_type: str
    filename: str
    media_type: str
    classification: DataClassification
    content_hash: str
    size: int

    @property
    def names(self) -> tuple[str, ...]:
        """The routing keys, filename first: an extension says more than a media type."""
        return tuple(dict.fromkeys(item for item in (self.filename, self.media_type) if item))

    @property
    def primary(self) -> str:
        return self.names[0] if self.names else ""

    @property
    def secondary(self) -> str:
        return self.names[1] if len(self.names) > 1 else ""


@dataclass(frozen=True)
class _Outcome:
    """The verdict ``T2`` records: status, parser identity, and what was refused."""

    status: ParseStatus
    parser_type: ParserType | None = None
    parser_version: str | None = None
    used_model: bool = False
    error_code: str | None = None
    error_message: str | None = None
    rejected: tuple[RejectedArtifact, ...] = ()
    reasons: tuple[str, ...] = ()


@dataclass(frozen=True)
class _Staged:
    """The NRR content, already written to object storage in the gap."""

    handle: StagingHandle
    record: dict[str, Any]
    classification: DataClassification
    lineage: tuple[str, ...]
    questions: tuple[Question, ...]
    #: the entries and the column-mapping proposals the parse settled on, projected into
    #: ``ai`` so AI-INP-014/015 answer from a row rather than by opening this artifact —
    #: which a 只读 function may not do inside the caller's transaction (00 doc 6.2)
    entries: tuple[NrrEntry, ...] = ()
    proposals: tuple[dict[str, Any], ...] = ()


@dataclass(frozen=True)
class _Decision:
    """One ``ai.policy_decision`` row's worth of judgement (AI-POL-008)."""

    effective_policy: dict[str, Any]
    provider_class: str | None
    model: str | None
    items: tuple[dict[str, Any], ...]
    result: str
    denied_items: tuple[dict[str, Any], ...] = ()
    model_denials: tuple[dict[str, Any], ...] = ()
    warnings: tuple[str, ...] = ()


@dataclass(frozen=True)
class _Cleared:
    """A context the policy *and* the capability both accepted, with what serves it."""

    candidate: CandidateModel
    provider: LLMProvider
    decision: _Decision


@dataclass(frozen=True)
class _Pending:
    """Everything ``T2`` writes, decided entirely outside a transaction."""

    inputs: _Inputs
    options: ParseOptions
    outcome: _Outcome
    input_bytes: int
    staged: _Staged | None = None
    decision: _Decision | None = None


class _PolicyRefusal(Exception):
    """Nothing may be sent, so the parse stops here, naming what was refused (AI-INP-007).

    An exception rather than a returned value because the judgement is taken deep inside the
    model branch, and threading a refusal up through every return type would be a rule each
    later caller has to remember.
    """

    def __init__(
        self,
        *,
        rejected: tuple[RejectedArtifact, ...],
        reasons: tuple[str, ...],
        decision: _Decision,
    ) -> None:
        super().__init__("; ".join(reasons) or "the policy refused this input")
        self.rejected = rejected
        self.reasons = reasons
        self.decision = decision


# --- the public entry point -----------------------------------------------------


@external_io
def run_parse(
    project_id: str,
    input_artifact_id: str,
    *,
    options: ParseOptions | Mapping[str, Any] | None = None,
    operator: str,
    idempotency_key: str,
    progress: ProgressReporter | None = None,
    cancel: CancellationToken | None = None,
) -> ParsedInput:
    """Parse one business input into an NRR artifact (SRS 7.1's ``parse_input``)."""
    report = Progress(progress, cancel)
    chosen = _coerce_options(options)
    replayed = _content_replay(project_id, input_artifact_id, chosen)
    if replayed is not None:
        # AI-INP-012's own words: the same input artifact with the same parse options returns
        # the first result.  A caller key is what a retry carries, not what a *different*
        # attempt at the same question carries — S1's fixed ``parse:{artifact}`` key and a UI
        # that posts the confirmed mapping under a fresh key are the same request.
        report.report(STEP_COMPLETED, 1.0, parseId=replayed.parse_id or UNROUTED, replayed=True)
        return replayed
    started = _begin(
        project_id,
        input_artifact_id,
        idempotency_key=idempotency_key,
        digest=_digest(project_id, input_artifact_id, chosen, operator),
    )
    if isinstance(started, ParsedInput):
        report.report(STEP_COMPLETED, 1.0, parseId=started.parse_id or UNROUTED, replayed=True)
        return started
    report.report(STEP_STARTED, 0.0, artifactId=started.artifact_id)
    pending = _gap(started, options=chosen, idempotency_key=idempotency_key, report=report)
    return _commit(idempotency_key, pending, operator=operator, report=report)


def _coerce_options(options: ParseOptions | Mapping[str, Any] | None) -> ParseOptions:
    """Accept S1's dict, S1's mirror model, or S3's own (AI-INP-006, 00 doc 6.1).

    ``factory_control/services/inputs.py:157`` forwards ``options={}`` verbatim, so a mapping
    is the normal input rather than something to be strict about.
    """
    if options is None:
        return ParseOptions()
    if isinstance(options, ParseOptions):
        return options
    if isinstance(options, Mapping):
        return ParseOptions.model_validate(dict(options))
    return ParseOptions.model_validate(options)


def _digest(
    project_id: str, input_artifact_id: str, options: ParseOptions, operator: str
) -> str:
    """The caller's intent and nothing else (AI-INP-012).

    The organisation policy, the model registry and the clock are left out on purpose: they
    are facts about the world, and a digest covering them would answer a retry with
    ``IDEMPOTENCY_CONFLICT`` for a request nobody changed — the same reasoning as
    :meth:`factory_intelligence.dto.request.AgentRunRequest.digest_payload`.

    ``operator`` is left out for the same reason: S1 derives the parse key from the artifact,
    so an operator who re-parses what a colleague already parsed is asking the same question
    and must get the first answer rather than a conflict.  Who parsed is the row's business
    (``ai.input_parse.operator``), not the key's.
    """
    return structure_hash(
        {
            "projectId": project_id,
            "inputArtifactId": input_artifact_id,
            "options": options.as_json(),
        }
    )


def _begin(
    project_id: str, input_artifact_id: str, *, idempotency_key: str, digest: str
) -> ParsedInput | _Inputs:
    """``T1``: spend the key and read the artifact metadata, in one transaction."""
    with unit_of_work() as session:
        begun = idempotency.begin(IdemScope.INPUT_PARSE, idempotency_key, digest, session=session)
        if not begun.created:
            if begun.first_result is not None:
                # AI-INP-012: the first result, with no second parser run and no second artifact
                return ParsedInput.model_validate(begun.first_result)
            if begun.status is IdempotencyStatus.IN_PROGRESS:
                # The first attempt is still inside the parse, so there is no result to hand
                # back and ParseStatus has no seventh value; a retryable error lets the activity
                # back off and re-enter, which is S2's precedent for BP_IN_PROGRESS (AI-AC-05).
                raise AgentRunInProgress(
                    f"the parse under idempotency key {idempotency_key!r} is still executing "
                    "in another attempt; retry this activity rather than starting a second parse",
                    details={"scope": IdemScope.INPUT_PARSE, "status": begun.status.value},
                )
            # a spent key with no result: that attempt failed, so this one runs (00 doc 6.4)
        info = get_artifact(input_artifact_id, session=session)
        _assert_own_input(project_id, info)
        return _inputs_of(info)


def _issues_of(outcome: _Outcome) -> list[Issue]:
    """The parse's own findings, in S2's unified Issue structure (AI-RT-018, AC-16).

    A refusal to parse is a *blocking* finding — nothing was produced — while a parse that
    came back needing review produced an NRR whose entries are candidates: that is a warning
    a reviewer clears, not a failure.
    """
    if not outcome.error_code and outcome.status is not ParseStatus.NEEDS_REVIEW:
        return []
    level = "BLOCKING" if outcome.error_code else "WARNING"
    message = outcome.error_message or (
        "the column mapping was proposed by header matching and is not confirmed; review it "
        "and re-parse with options.columnMapping (AI-INP-006)"
    )
    return [
        Issue(
            issue_id=f"aip-{outcome.status.value}-{outcome.error_code or 'NEEDS_REVIEW'}",
            rule_id=str(outcome.error_code or "AI-INP-006"),
            level=level,
            message=str(message),
        )
    ]


#: A parse that produced no NRR artifact. Its stored error is a record of one attempt, not an
#: answer a later attempt may reuse — see ``_content_replay``.
_DEAD_END_STATUSES = frozenset({ParseStatus.FAILED.value, ParseStatus.POLICY_DENIED.value})


def _content_replay(
    project_id: str, input_artifact_id: str, options: ParseOptions
) -> ParsedInput | None:
    """The first result for this artifact with these options, if one was recorded (AI-INP-012).

    Matched on the options *as parsed*, not on the caller's idempotency key: two requests
    that ask the same question of the same bytes must not produce two NRR artifacts, and a
    re-parse with a genuinely different mapping is a different question and does run.

    An attempt that produced nothing is not "the first result" of anything.  ``FAILED`` and
    ``POLICY_DENIED`` rows carry a result — the error the transport or the policy raised — and
    replaying it would make a provider mis-registration permanent: fixing the address or
    relaxing the policy and pressing 解析 again has to re-run, or the operator is stuck with an
    answer from a world that no longer exists.  This is the same reasoning that keeps
    ``RecordReplayProvider`` from recording an errored call (AI-AC-10): caching a failure turns
    one transport problem into a permanent replayed answer.
    """
    wanted = options.as_json()

    def _read(sess: Session) -> ParsedInput | None:
        rows: list[InputParseRow] = list(
            sess.execute(
                sql_select(InputParseRow)
                .where(
                    InputParseRow.project_id == project_id,
                    InputParseRow.input_artifact_id == input_artifact_id,
                )
                .order_by(InputParseRow.created_at.desc())
                .limit(20)
            )
            .scalars()
            .all()
        )
        for candidate in rows:
            if candidate.status in _DEAD_END_STATUSES:
                continue
            if dict(candidate.options or {}) == wanted and candidate.result:
                return ParsedInput.model_validate(candidate.result)
        return None

    if has_active_uow():
        return _read(current_session())
    with unit_of_work() as sess:
        return _read(sess)


def _assert_own_input(project_id: str, info: ArtifactInfo) -> None:
    """A cross-project read is a permission failure and propagates (00 doc 4.2)."""
    if info.project_id != project_id:
        raise NotFoundError(
            f"artifact {info.id} belongs to project {info.project_id}, not {project_id}",
            details={"artifactId": info.id, "projectId": info.project_id},
        )


def _inputs_of(info: ArtifactInfo) -> _Inputs:
    filename = (info.build_record or {}).get("filename")
    return _Inputs(
        artifact_id=info.id,
        project_id=info.project_id,
        # The NRR inherits the input's workflow: S1's review read addresses NRRs by workflow, and
        # an NRR registered without one is invisible on the requirements screen even though its
        # bytes exist. The input is uploaded by S1 with the workflow already recorded.
        workflow_id=info.workflow_id,
        artifact_type=info.type,
        filename=filename.strip() if isinstance(filename, str) else "",
        media_type=info.media_type or "",
        classification=info.data_classification,
        content_hash=info.content_hash,
        size=info.size,
    )


# --- the gap: read, route, judge, extract, compose ------------------------------


def _gap(
    inputs: _Inputs, *, options: ParseOptions, idempotency_key: str, report: Progress
) -> _Pending:
    """All the external work, with no transaction open (00 doc 6.2)."""
    parser_type: ParserType | None = None
    content = b""
    judgement: _Decision | None = None
    try:
        report.safe_point()
        _assert_parseable(inputs)
        content = _read_content(inputs)
        report.report(STEP_CONTEXT, 0.15, bytes=len(content), artifactId=inputs.artifact_id)
        parser_type = route_parser(
            inputs.primary, content=content, media_type=inputs.secondary, options=options
        )
        report.report(
            STEP_CONTEXT, 0.25, parserType=parser_type.value, model=parser_type in MODEL_PARSERS
        )
        report.safe_point()
        if parser_type in DETERMINISTIC_PARSERS:
            fragments = _deterministic(inputs, parser_type, content=content, options=options)
        else:
            fragments, judgement = _with_model(
                inputs, parser_type, content=content, options=options, report=report
            )
        return _compose(
            inputs,
            options,
            parser_type=parser_type,
            fragments=fragments,
            content=content,
            decision=judgement,
            report=report,
        )
    except CancelledError:
        _release(idempotency_key, error_code="CANCELLED")
        raise
    except ContextContainsPhi as exc:
        return _pending_phi(inputs, options, parser_type=parser_type, message=str(exc.message))
    except _PolicyRefusal as refusal:
        return _pending_denied(
            inputs,
            options,
            parser_type=parser_type,
            rejected=refusal.rejected,
            reasons=refusal.reasons,
            decision=refusal.decision,
            read=len(content),
        )
    except (ParseError, AiError) as exc:
        code, message = _code_and_message(exc)
        return _pending_failed(
            inputs,
            options,
            parser_type=parser_type,
            code=code,
            message=message,
            read=len(content),
            used_model=report.model_calls > 0,
        )
    except FactoryError as exc:
        _release(idempotency_key, error_code=exc.error_code)
        raise


def _assert_parseable(inputs: _Inputs) -> None:
    """An artifact of another type is reported, not parsed (AI-INP-003a)."""
    if inputs.artifact_type in PARSEABLE_INPUT_TYPES:
        return
    raise dispatch_unmatched(
        f"artifact {inputs.artifact_id} is a {inputs.artifact_type}, not a business input; "
        f"S3 parses {' or '.join(sorted(PARSEABLE_INPUT_TYPES))} only — upload the file as "
        "an input, or point this call at the artifact that holds the business content"
    )


def _read_content(inputs: _Inputs) -> bytes:
    """The bytes, after the metadata decided what they should be (AI-INP-001)."""
    content, _classification, _media_type = artifacts.read_bytes(inputs.artifact_id)
    if not content:
        raise dispatch_unmatched(
            f"artifact {inputs.artifact_id} holds no content at all, so no NRR could be "
            "written; re-upload the file (AI-INP-003a: an empty parse is reported, not "
            "produced)"
        )
    ceiling = settings().max_input_bytes
    if len(content) > ceiling:
        raise ContextTooLarge(
            f"the input is {len(content)} bytes, over the {ceiling} byte limit AI-NFR-06 "
            "sets; split it or reduce it, because S3 will not read part of a document and "
            "call that a parse",
            details={"artifactId": inputs.artifact_id, "bytes": len(content)},
        )
    return content


# --- routing (AI-INP-002, AI-INP-003) ------------------------------------------


def route_parser(
    name: str,
    *,
    content: bytes,
    media_type: str = "",
    options: ParseOptions | None = None,
) -> ParserType:
    """Which parser owns this input — and the structural proof that a model never gets a
    deterministic format (AI-INP-003, AI-BR-02).

    ``name`` is the filename and ``media_type`` the fallback, because an artifact registered
    without a filename still says ``text/csv``.  ``options.parser`` may name a parser for an
    oddly-named file, but only in the direction the tables already own: forcing a
    deterministic parser is allowed, forcing a model onto a format dispatch covers is refused
    here, before a provider could be reached.
    """
    names = tuple(dict.fromkeys(item for item in (name, media_type) if item))
    forced = _forced_parser(options)
    deterministic = _deterministic_instance(names, content)
    if forced is not None and forced in DETERMINISTIC_PARSERS:
        #: forcing a deterministic type on an unrecognised *name* is allowed — a Mermaid file
        #: called ``requirements`` really does exist — and the content sniff above is what
        #: confirms or contradicts the caller, rather than the caller contradicting itself
        if deterministic is not None and deterministic.parser_type is not forced:
            raise dispatch_unmatched(
                f"options.parser={forced.value} contradicts the parser this input's own name "
                f"and content declare ({deterministic.parser_type.value}); one of the two is "
                "wrong, and guessing would file the whole input under the wrong category "
                "(AI-INP-002)"
            )
        return forced
    if deterministic is not None:
        if forced is not None:
            raise dispatch_unmatched(
                f"'{name or media_type}' is covered by a deterministic parser, but "
                f"options.parser={forced.value} would route it to a model (AI-INP-003, "
                "AI-BR-02); leave options.parser unset and the deterministic parser runs"
            )
        return deterministic.parser_type
    if forced is not None:
        #: a model-assisted type a caller named for an oddly-named file: nothing deterministic
        #: owns this input, so the choice is the caller's to make (AI-INP-006)
        return _late_milestone(forced)
    declared = _exported_as(options)
    if declared is not None:
        return _late_milestone(declared)
    #: AI-INP-002's 绘图工具 row is a name, not a media type: S0 registers a ``.drawio`` upload
    #: as ``application/xml``, so the name must be refused before the media-type table routes
    #: the native file to the document extractor (AI-INP-003a).
    if _drawing_tool(names):
        raise dispatch_unmatched(
            f"'{name or media_type}' is a native drawing-tool file, and AI-INP-002 asks for an "
            "export instead: export it as XML, SVG or PNG, re-upload it, and set "
            "options.exported_as so the export decides the handling"
        )
    by_name = _by_name(names)
    if by_name is not None:
        return _late_milestone(by_name)
    by_magic = _by_magic(content)
    if by_magic is not None:
        return by_magic
    raise dispatch_unmatched(
        f"no parser covers '{name or media_type or 'this input'}': it is neither one of "
        "AI-INP-002's deterministic formats nor one of its document, image or export rows — "
        "convert it to CSV, Excel, Mermaid, PlantUML, Markdown, Word or PDF with a text layer, "
        "a DHIS2 metadata export, or a picture, or supply options.parser or options.exported_as"
    )


def _forced_parser(options: ParseOptions | None) -> ParserType | None:
    """``options.parser``, read strictly: an unknown name is a caller bug, not "unset"."""
    raw = str(getattr(options, "parser", "") or "").strip().upper()
    if not raw:
        return None
    try:
        return ParserType(raw)
    except ValueError as exc:
        raise dispatch_unmatched(
            f"options.parser={raw!r} is not a parser S3 knows; the choices are "
            + ", ".join(sorted(member.value for member in ParserType))
        ) from exc


def _exported_as(options: ParseOptions | None) -> ParserType | None:
    raw = str(getattr(options, "exported_as", "") or "").strip().lower().lstrip(".")
    return EXPORTED_AS_PARSERS.get(raw)


def _by_name(names: tuple[str, ...]) -> ParserType | None:
    """The model-assisted tables, tried as media type then as suffix.

    The M3 table is consulted with the same shape as the M2 one, so a source file or an
    OpenAPI document is answered by the milestone gate rather than falling through to the
    generic "no parser covers this" refusal (AI-INP-002, SRS 12).
    """
    for candidate in names:
        by_media = MODEL_PARSERS_BY_MEDIA_TYPE.get(candidate.strip().lower())
        if by_media is not None:
            return by_media
    for candidate in names:
        suffix = _suffix(candidate)
        if suffix is None:
            continue
        if suffix in MODEL_PARSERS_BY_SUFFIX:
            return MODEL_PARSERS_BY_SUFFIX[suffix]
        if suffix in LATE_MILESTONE_PARSERS_BY_SUFFIX:
            return LATE_MILESTONE_PARSERS_BY_SUFFIX[suffix]
    return None


def _by_magic(content: bytes) -> ParserType | None:
    """Recognise a picture or a PDF whose registered name said nothing."""
    head = content[:8]
    if head.startswith(_IMAGE_MAGIC):
        return ParserType.IMAGE
    if head.startswith(b"%PDF-"):
        return ParserType.PDF
    return None


def _drawing_tool(names: tuple[str, ...]) -> bool:
    return any(_suffix(name) in DRAWING_TOOL_SUFFIXES for name in names)


def _late_milestone(parser_type: ParserType) -> ParserType:
    """AI-INP-002's 源代码 / OpenAPI row is M3, and the milestone decides (SRS 12)."""
    if parser_type in LATE_MILESTONE_PARSERS:
        require_milestone(Milestone.M3, "source-code and API-document extraction")
    return parser_type


def _suffix(candidate: str) -> str | None:
    name = candidate.replace("\\", "/").rsplit("/", 1)[-1].lower()
    known = sorted(
        set(MODEL_PARSERS_BY_SUFFIX)
        | set(LATE_MILESTONE_PARSERS_BY_SUFFIX)
        | DRAWING_TOOL_SUFFIXES,
        key=len,
        reverse=True,
    )
    for suffix in known:
        if name.endswith(suffix):
            return suffix
    if "." not in name:
        return None
    return "." + name.rsplit(".", 1)[-1]


def _deterministic_instance(names: tuple[str, ...], content: bytes) -> Parser | None:
    """The deterministic parser dispatch owns, plus the assertion AI-INP-003 is about."""
    for name in names:
        parser = dispatch.select_parser(name, content)
        if parser is None:
            continue
        #: taken from the enum table rather than from the dispatcher's good intentions: what
        #: ran must be a member of the set that defines "never calls a model", so a routing
        #: table that ever mapped a deterministic suffix onto a model-assisted type fails
        #: here, at the boundary, on the first parse of it.
        if parser.parser_type not in DETERMINISTIC_PARSERS:
            raise dispatch_unmatched(
                f"dispatch returned {parser.parser_type.value}, which DETERMINISTIC_PARSERS "
                "does not declare model-free; refusing to hand a deterministic format to a "
                "model (AI-INP-003, AI-BR-02)"
            )
        return parser
    return None


def dispatch_unmatched(message: str) -> ParseError:
    """A routing refusal, with the code ``registries`` already declares for it."""
    return ParseError(dispatch.UNMATCHED, message)


# --- the two branches -----------------------------------------------------------


def _deterministic(
    inputs: _Inputs, parser_type: ParserType, *, content: bytes, options: ParseOptions
) -> list[Fragment]:
    """Run the parser the table chose.  No provider object exists anywhere in this branch."""
    parser = _require_deterministic(inputs, content)
    if parser.parser_type is not parser_type:
        raise dispatch_unmatched(
            f"routing chose {parser_type.value} but dispatch owns "
            f"{parser.parser_type.value} for this input; the two tables disagree "
            "(AI-INP-003)"
        )
    return [
        parser.parse(
            content,
            source_artifact_id=inputs.artifact_id,
            classification=inputs.classification,
            options=_parser_options(inputs, options),
        )
    ]


def _require_deterministic(inputs: _Inputs, content: bytes) -> Parser:
    """The guard again: no match is an error, never a silently empty fragment (AI-INP-003a)."""
    try:
        return dispatch.require_deterministic_parser(inputs.primary, content)
    except ParseError:
        if not inputs.secondary:
            raise
        return dispatch.require_deterministic_parser(inputs.secondary, content)


def _parser_options(inputs: _Inputs, options: ParseOptions) -> dict[str, Any]:
    """The parser-facing view of the caller's choices (AI-INP-006).

    ``filename`` travels with them because the tabular parsers name their sheet from it, and a
    location reading ``row:csv#2`` in an NRR citing a workbook called ``字段清单.xlsx`` is a
    location no reviewer can open.
    """
    values: dict[str, Any] = dict(options.as_json())
    if inputs.filename:
        values.setdefault("filename", inputs.filename)
    return values


def _with_model(
    inputs: _Inputs,
    parser_type: ParserType,
    *,
    content: bytes,
    options: ParseOptions,
    report: Progress,
) -> tuple[list[Fragment], _Decision]:
    """Judge the whole prospective context, then extract — in that order (AI-INP-007).

    The document readers run before the verdict and that is not a loophole: they only turn
    bytes S3 already holds into text with coordinates, and AI-INP-007 governs what *leaves*
    the factory.  Reading first is what lets AI-POL-007's context-window check be answered
    with the size of the real context instead of the size of the file.

    The returned judgement is the allowed half of AI-POL-008: the parse owes ``T2`` the row
    saying which model was cleared for which context, not only the rows that refused.
    """
    document = _read_document(inputs, parser_type, content=content)
    planned = _prospective_text(parser_type, document, content)
    cleared = _judge(inputs, parser_type, planned=planned, size=len(content), report=report)
    report.safe_point()
    report.report(
        STEP_MODEL_CALL,
        0.45,
        model=_model_ref(cleared.candidate),
        providerClass=cleared.candidate.provider_class.value,
    )
    values = _parser_options(inputs, options)
    if parser_type is ParserType.IMAGE:
        report.model_calls += 1
        return (
            [
                vision_extract.extract_image(
                    content,
                    source_artifact_id=inputs.artifact_id,
                    classification=inputs.classification,
                    media_type=inputs.media_type or inputs.filename,
                    content_hash=inputs.content_hash,
                    provider=cleared.provider,
                    candidate=cleared.candidate,
                    parser_version=MODEL_PARSER_VERSION,
                    options=values,
                    progress=report.reporter,
                    cancel=report.cancel,
                )
            ],
            cleared.decision,
        )
    if document is None:  # pragma: no cover - every model row except an image sets a document
        raise dispatch_unmatched(f"{parser_type.value} has no text reader in this build")
    fragments = llm_extract.extract_document(
        document,
        parser_type=parser_type,
        parser_version=MODEL_PARSER_VERSION,
        source_artifact_id=inputs.artifact_id,
        classification=inputs.classification,
        provider=cleared.provider,
        content_hash=inputs.content_hash,
        options=values,
        progress=report.reporter,
        cancel=report.cancel,
    )
    #: one call per chunk, counted as they come back so ``used_model`` and the report of a
    #: parse that then failed describe the calls that actually happened (AI-INP-004)
    report.model_calls += max(len(fragments), 1)
    return fragments, cleared.decision


def _read_document(
    inputs: _Inputs, parser_type: ParserType, *, content: bytes
) -> DocumentText | None:
    """The deterministic text half of AI-INP-002's document row: text with its coordinates.

    A scanned PDF or Word file raises ``SCANNED_DOCUMENT_UNSUPPORTED`` in here, because this is
    the reader that can tell the difference and the only place that should (AI-INP-003a).
    """
    if parser_type is ParserType.MARKDOWN:
        return markdown_extract.extract_markdown(content)
    if parser_type is ParserType.DOCX:
        return docx_extract.extract_docx(content, filename=inputs.filename or None)
    if parser_type is ParserType.PDF:
        return pdf_extract.extract_pdf(content, filename=inputs.filename or None)
    return None


def _prospective_text(parser_type: ParserType, document: DocumentText | None, content: bytes) -> str:
    """Everything that would be sent, for the judgement and the window check (AI-POL-007).

    A picture contributes its base64 form rather than its bytes, because the base64 form is
    what the provider receives and therefore what its context window is measured against.
    """
    if parser_type is ParserType.IMAGE:
        return base64.b64encode(content).decode("ascii")
    if document is not None:
        return document.rendered(document.segments)
    return content.decode("utf-8", errors="replace")


def _judge(
    inputs: _Inputs,
    parser_type: ParserType,
    *,
    planned: str,
    size: int,
    report: Progress,
) -> _Cleared:
    """PHI first, then the effective policy, then the capability (AI-INP-007, AI-POL-007).

    The organisation policy is the whole answer, with no target environment folded in: a parse
    is environment-independent — it feeds the design main line — and SRS 7 hands export
    policies to *agent runs* (AI-RT-002, AI-POL-004) but not to ``parse_input``.
    """
    report.safe_point()
    report.report(STEP_POLICY, 0.3, parserType=parser_type.value)
    ref = f"artifact:{inputs.artifact_id}"
    items = [
        ContextItem(
            ref=ref,
            classification=inputs.classification,
            kind=ItemSourceKind.INPUT_ARTIFACT,
            label=inputs.primary or "business input",
            size_bytes=size,
            is_image=parser_type is ParserType.IMAGE,
        )
    ]
    # AC-POL-02 before the engine at all: no relaxation, default or otherwise, may send
    # PHI_PII to a provider, so consulting a policy that could answer yes is the wrong order.
    artifacts.assert_model_safe([(item.ref, item.classification) for item in items])
    policy = effective_policy()
    needed = estimate_text(planned)
    candidates = candidates_for_agent(EXTRACTION_AGENT, needed_tokens=needed)
    if not candidates:
        raise ProviderUnavailable(
            f"no model is registered for the {EXTRACTION_AGENT.value} agent, so this input "
            "cannot be extracted; register a provider and a model (AI-REG-004), or supply the "
            "file in one of AI-INP-002's deterministic formats, which need no model at all",
            details={"artifactId": inputs.artifact_id, "parserType": parser_type.value},
        )
    selection = select(
        items,
        policy,
        candidates,
        needed_tokens=needed,
        needs_vision=parser_type is ParserType.IMAGE,
    )
    if selection.chosen is None:
        denied = _denied_context_items(items, policy, selection)
        raise _PolicyRefusal(
            rejected=_rejected_items(denied),
            reasons=tuple(selection.reasons),
            decision=_decision_of(items, policy, selection, denied),
        )
    decision = _decision_of(items, policy, selection, ())
    model = registered_model(_model_ref(selection.chosen))
    provider = get_llm_provider(model.provider_info())
    refusal = _capability_refusal(provider, selection.chosen, parser_type)
    if refusal is not None:
        raise _PolicyRefusal(rejected=(), reasons=(refusal,), decision=decision)
    # A parse that consults a model has spent project money, and AI-NFR-03's 100% is a
    # percentage over calls, not over entry points: the ledger is attached here, where the
    # identity is known, rather than taught to every extractor.
    return _Cleared(
        candidate=selection.chosen,
        provider=RecordingProvider(
            provider,
            model=model,
            context=CallContext(
                project_id=inputs.project_id,
                workflow_id=f"parse:{inputs.artifact_id}",
                stage=None,
                agent_type=EXTRACTION_AGENT.value,
                run_id=None,
                call_role="extraction",
                input_artifact_id=inputs.artifact_id,
            ),
            context_artifacts=tuple(
                {"ref": item.ref, "classification": item.classification.value} for item in items
            ),
        ),
        decision=decision,
    )


def _capability_refusal(
    provider: LLMProvider, candidate: CandidateModel, parser_type: ParserType
) -> str | None:
    """The installed provider's own declaration, checked against the input (AI-LLM-002).

    The registry says what the *model* can do; :func:`factory_intelligence.deps.get_llm_provider`
    may hand back a fixed mock or replay provider that cannot, and extracting a picture with a
    provider that cannot see would answer from the file name.
    """
    declared = provider.info.capabilities
    wanted = declared.vision if parser_type is ParserType.IMAGE else declared.structured_output
    if wanted:
        return None
    skill = "vision" if parser_type is ParserType.IMAGE else "structured output"
    return (
        f"{_model_ref(candidate)}: the provider serving it declares no {skill} capability, "
        "so this input cannot be extracted by it (AI-LLM-002, AI-POL-007); no "
        "lower-capability fallback is attempted"
    )


def _model_ref(candidate: CandidateModel) -> str:
    """``provider/model``, the spelling :meth:`RegisteredModel.ref` uses for a lookup."""
    return f"{candidate.provider}/{candidate.model}"


def _denied_context_items(
    items: list[ContextItem], policy: EffectivePolicy, selection: Selection
) -> tuple[DeniedItem, ...]:
    """Every item that made the context unsendable, named rather than dropped (AI-POL-003).

    The classes walked are exactly those the selection tried, so the reason a caller reads is
    the reason one of *its* candidate models refused, not a hypothetical about another.
    """
    out: dict[tuple[str, str], DeniedItem] = {}
    for denial in selection.denials:
        try:
            provider_class = ProviderClass(denial.provider_class)
        except ValueError:  # pragma: no cover - denials are built from the enum
            continue
        for denied in evaluate(items, policy, provider_class).denied:
            out.setdefault((denied.ref, denied.reason), denied)
    return tuple(out.values())


def _rejected_items(denied: Sequence[DeniedItem]) -> tuple[RejectedArtifact, ...]:
    """The same refusals in the DTO a caller of ``parse_input`` is handed (AI-INP-007)."""
    return tuple(
        RejectedArtifact(
            ref=item.ref,
            classification=item.classification.value,
            reason=item.reason,
            allowed_classes=[member.value for member in item.allowed_classes],
        )
        for item in denied
    )


def _decision_of(
    items: list[ContextItem],
    policy: EffectivePolicy,
    selection: Selection,
    denied: Sequence[DeniedItem],
) -> _Decision:
    """The judgement as ``ai.policy_decision`` records it, allowed or refused (AI-POL-008)."""
    chosen = selection.chosen
    return _Decision(
        effective_policy=policy.to_payload(),
        provider_class=chosen.provider_class.value if chosen else None,
        model=chosen.model if chosen else None,
        items=tuple({"ref": item.ref, "classification": item.classification.value} for item in items),
        result=PolicyResult.ALLOWED.value if chosen else PolicyResult.DENIED.value,
        denied_items=tuple(item.as_payload() for item in denied),
        model_denials=tuple(selection.payloads),
        warnings=tuple(policy.warnings),
    )


# --- merge, gate, compose ------------------------------------------------------


def _compose(
    inputs: _Inputs,
    options: ParseOptions,
    *,
    parser_type: ParserType,
    fragments: list[Fragment],
    content: bytes,
    decision: _Decision | None,
    report: Progress,
) -> _Pending:
    """Merge deterministically, gate on confidence, and stage the NRR (AI-INP-008/009).

    ``decision`` is the judgement the model branch took before it was asked: AI-POL-008 wants
    the allowed case recorded too, and only the branch that consulted the policy has it.
    """
    report.safe_point()
    report.report(STEP_QUESTIONS, 0.75, fragments=len(fragments))
    merge = merge_fragments(fragments)
    #: AI-INP-009 across inputs: this parse is compared with the entries the project already
    #: recorded from other inputs, so two documents that disagree about one field produce one
    #: question naming both places instead of two NRR artifacts that quietly contradict.
    merge = merge.with_conflicts(
        merge_fragments_cross(merge.entries, inputs.project_id, inputs.artifact_id)
    )
    threshold, threshold_source = extraction_threshold(inputs.project_id)
    gate = apply_gate(merge.entries, fragments, threshold=threshold, conflicted=_contested(merge))
    questions = nrr.assemble_questions(fragments, merge, gate)
    if not gate.entries and not questions:
        raise ParseError(
            "AI_OUTPUT_INVALID",
            f"the {parser_type.value} parse produced no requirement and no question, so an "
            "empty NRR would be written; the input is reported instead (AI-INP-003a)",
        )
    #: the invariant AI-INP-008 states, checked on the way out rather than trusted
    check_confidence_parity(gate.entries, threshold)
    #: AI-INP-002 marks the document and image rows 必须复核, so a parse a model took part in
    #: is never simply "parsed" however confident its answers were; the review is a human's
    #: verdict on the extraction, not a second opinion on each confidence number.
    used_model = nrr.used_model(fragments)
    needs_review = bool(questions) or used_model or any(
        fragment.needs_review for fragment in fragments
    )
    outcome = _Outcome(
        status=ParseStatus.NEEDS_REVIEW if needs_review else ParseStatus.PARSED,
        parser_type=parser_type,
        parser_version=nrr.parser_version_label(fragments),
        used_model=used_model,
    )
    record = nrr.build_record(
        fragments,
        entries=gate.entries,
        merge=merge,
        gate=gate,
        extraction_threshold=threshold,
        threshold_source=threshold_source,
    )
    classification, lineage = _floor_and_lineage(inputs, fragments)
    handle = artifacts.stage_json(
        inputs.project_id,
        nrr.NRR_ARTIFACT_TYPE,
        nrr.build_document(
            project_id=inputs.project_id,
            fragments=fragments,
            entries=gate.entries,
            merge=merge,
            gate=gate,
            questions=questions,
            extraction_threshold=threshold,
            threshold_source=threshold_source,
        ),
    )
    report.questions = len(questions)
    return _Pending(
        inputs=inputs,
        options=options,
        outcome=outcome,
        input_bytes=len(content),
        staged=_Staged(
            handle=handle,
            record=record,
            classification=classification,
            lineage=lineage,
            questions=tuple(questions),
            entries=tuple(gate.entries),
            proposals=nrr.proposals_of(fragments),
        ),
        decision=decision,
    )


def merge_fragments_cross(
    entries: Sequence[NrrEntry], project_id: str, artifact_id: str
) -> list[Conflict]:
    """Conflicts between this parse and the project's recorded NRR history (AI-INP-009)."""
    from factory_intelligence.parsing import records

    history = records.history_entries(project_id, exclude_artifact_id=artifact_id)
    if not history:
        return []
    return list(cross_source_conflicts(entries, history))


def _contested(merge: MergeResult) -> set[tuple[str, str]]:
    """The ``(category, businessKey)`` pairs two sources disagree about (AI-INP-009)."""
    return {(conflict.category.value, conflict.business_key) for conflict in merge.conflicts}


def _floor_and_lineage(
    inputs: _Inputs, fragments: list[Fragment]
) -> tuple[DataClassification, tuple[str, ...]]:
    """The artifact's own classification, and what it may name as a parent.

    ``data_classification`` is the maximum over *every* contributing source, because S0
    refuses a derived artifact below that floor (FD-ART-008) and a PHI_PII input read by a
    deterministic parser still holds PHI_PII content.  A PHI_PII source is not named in
    ``source_artifact_ids`` though: a lineage edge is what a later stage follows to re-read the
    original, and AC-POL-02 with AI-POL-005 forbids that content ever reaching a provider.  The
    entries go on citing it in ``source.artifactId`` (AI-INP-005), so provenance survives
    without the edge.
    """
    by_source = {fragment.source_artifact_id: fragment.classification for fragment in fragments}
    by_source.setdefault(inputs.artifact_id, inputs.classification)
    floor = artifacts.source_floor(list(by_source.values()), inputs.classification)
    named = tuple(
        sorted(
            artifact
            for artifact, level in by_source.items()
            if level is not DataClassification.PHI_PII
        )
    )
    return floor, named


def extraction_threshold(project_id: str) -> tuple[float, str]:
    """The project's extraction threshold, from the Blueprint's ``generationPolicy``.

    AI-INP-008 names the Blueprint as the owner of the number, and a project with no approved
    version yet — exactly the state a first parse is in — has none to read, so the configured
    default applies and is recorded as such in ``thresholdsApplied.source`` rather than passed
    off as the project's own decision.
    """
    selector: dict[str, Any] = {
        "sections": [GENERATION_POLICY_SECTION],
        "business_keys": [],
        "page_keys": [],
        "decision_ids": [],
        "source": "APPROVED",
    }
    try:
        selected = get_blueprint().entries(project_id, selector)
    except FactoryError:
        return settings().default_extraction_confidence, "configuration"
    thresholds = from_entries(selected)
    return thresholds.extraction, thresholds.source


# --- outcomes that are not a parsed NRR ----------------------------------------


def _code_and_message(error: ParseError | AiError) -> tuple[str, str]:
    if isinstance(error, ParseError):
        return error.code, error.message
    return error.error_code, str(error.message)


def _pending_failed(
    inputs: _Inputs,
    options: ParseOptions,
    *,
    parser_type: ParserType | None,
    code: str,
    message: str,
    read: int,
    used_model: bool,
) -> _Pending:
    """``FAILED`` with the next step a person can act on (AI-INP-003a)."""
    return _Pending(
        inputs=inputs,
        options=options,
        outcome=_Outcome(
            status=ParseStatus.FAILED,
            parser_type=parser_type,
            parser_version=(
                f"{parser_type.value}@{MODEL_PARSER_VERSION}"
                if parser_type in MODEL_PARSERS
                else None
            ),
            used_model=used_model,
            error_code=code,
            error_message=message,
        ),
        input_bytes=read,
    )


def _pending_denied(
    inputs: _Inputs,
    options: ParseOptions,
    *,
    parser_type: ParserType | None,
    rejected: tuple[RejectedArtifact, ...],
    reasons: tuple[str, ...],
    decision: _Decision,
    read: int,
) -> _Pending:
    """AI-INP-007: refused, every rejected artifact named, no NRR, no truncation."""
    listed = ", ".join(item.ref for item in rejected) or f"artifact:{inputs.artifact_id}"
    return _Pending(
        inputs=inputs,
        options=options,
        outcome=_Outcome(
            status=ParseStatus.POLICY_DENIED,
            parser_type=parser_type,
            error_code="POLICY_DENIED",
            error_message=(
                f"the effective policy allows no registered model that {listed} may be sent "
                "to, so the parse was refused rather than truncated (AI-INP-007, AI-BR-03); "
                "the models that were tried said: "
                + ("; ".join(reasons) or "nothing")
            ),
            rejected=rejected,
            reasons=reasons,
        ),
        input_bytes=read,
        decision=decision,
    )


def _pending_phi(
    inputs: _Inputs, options: ParseOptions, *, parser_type: ParserType | None, message: str
) -> _Pending:
    """AC-POL-02: PHI_PII reaches no provider, under any policy, ever."""
    ref = f"artifact:{inputs.artifact_id}"
    rejected = RejectedArtifact(
        ref=ref,
        classification=DataClassification.PHI_PII.value,
        reason=message,
        allowed_classes=[],
    )
    return _Pending(
        inputs=inputs,
        options=options,
        outcome=_Outcome(
            status=ParseStatus.POLICY_DENIED,
            parser_type=parser_type,
            error_code="AI_CONTEXT_CONTAINS_PHI",
            error_message=(
                f"{message}; the input is left unparsed — supply a de-identified export, or "
                "hand over one of AI-INP-002's deterministic formats, which need no model "
                "(AC-POL-02, AI-INP-007)"
            ),
            rejected=(rejected,),
            reasons=(message,),
        ),
        input_bytes=inputs.size,
        decision=_Decision(
            effective_policy={},
            provider_class=None,
            model=None,
            items=({"ref": ref, "classification": DataClassification.PHI_PII.value},),
            result=PolicyResult.DENIED.value,
            denied_items=(
                {
                    "ref": ref,
                    "classification": DataClassification.PHI_PII.value,
                    "reason": message,
                    "allowedClasses": [],
                },
            ),
        ),
    )


# --- T2 ------------------------------------------------------------------------


def _commit(
    idempotency_key: str, pending: _Pending, *, operator: str, report: Progress
) -> ParsedInput:
    """Row, artifact metadata, audit, policy decision and the spent key, in one transaction.

    That pairing is D-11 rather than tidiness: a caller can never see an ``ai.input_parse`` row
    whose NRR was never registered, nor an NRR with no audit event behind it.
    """
    parse_id = new_id("aipr")
    with unit_of_work() as session:
        nrr_artifact_id = _register(session, pending)
        session.add(_row(parse_id, pending, idempotency_key, nrr_artifact_id, operator))
        for entry_row in _entry_rows(parse_id, pending, nrr_artifact_id):
            session.add(entry_row)
        if pending.decision is not None:
            session.add(_decision_row(pending.decision, pending.inputs.project_id))
        _audit(session, parse_id, pending, nrr_artifact_id=nrr_artifact_id, operator=operator)
        result = _result_of(parse_id, pending, nrr_artifact_id=nrr_artifact_id)
        idempotency.complete(
            IdemScope.INPUT_PARSE,
            idempotency_key,
            result.model_dump(mode="json"),
            session=session,
        )
    report.report(
        STEP_COMPLETED,
        1.0,
        parseId=parse_id,
        status=result.status.value,
        nrrArtifactId=nrr_artifact_id,
    )
    return result


def _register(session: Session, pending: _Pending) -> str | None:
    staged = pending.staged
    if staged is None:
        return None
    return artifacts.register_staged(
        staged.handle,
        session=session,
        project_id=pending.inputs.project_id,
        workflow_id=pending.inputs.workflow_id,
        artifact_type=nrr.NRR_ARTIFACT_TYPE,
        data_classification=staged.classification,
        source_artifact_ids=list(staged.lineage),
        build_record=staged.record,
    ).id


def _row(
    parse_id: str,
    pending: _Pending,
    idempotency_key: str,
    nrr_artifact_id: str | None,
    operator: str,
) -> InputParseRow:
    """Every parse is recorded, result or not (AI-INP-004)."""
    outcome = pending.outcome
    return InputParseRow(
        id=parse_id,
        project_id=pending.inputs.project_id,
        input_artifact_id=pending.inputs.artifact_id,
        input_hash=pending.inputs.content_hash,
        parser_type=outcome.parser_type.value if outcome.parser_type else UNROUTED,
        parser_version=_plain_version(outcome),
        options=pending.options.as_json(),
        used_model=outcome.used_model,
        status=outcome.status.value,
        nrr_artifact_id=nrr_artifact_id,
        idempotency_key=idempotency_key,
        error_code=outcome.error_code,
        error_message=outcome.error_message,
        result=(
            _result_of(parse_id, pending, nrr_artifact_id=nrr_artifact_id).model_dump(mode="json")
        ),
        input_bytes=pending.input_bytes,
        mapping_proposal=list(pending.staged.proposals) if pending.staged is not None else None,
        operator=operator,
    )


def _entry_rows(
    parse_id: str, pending: _Pending, nrr_artifact_id: str | None
) -> list[NrrEntryRow]:
    """Project the NRR entries into ``ai.nrr_entry`` for AI-INP-015's query.

    The artifact stays the authoritative document; these rows exist so a category or
    confidence filter can be answered without opening it — which a 只读 function must not do
    inside the caller's transaction (00 doc 6.2).
    """
    if pending.staged is None:
        return []
    rows: list[NrrEntryRow] = []
    for entry in pending.staged.entries:
        rows.append(
            NrrEntryRow(
                id=new_id("anre"),
                project_id=pending.inputs.project_id,
                parse_id=parse_id,
                input_artifact_id=entry.source_artifact_id or pending.inputs.artifact_id,
                nrr_artifact_id=nrr_artifact_id,
                business_key=entry.business_key[:256],
                category=entry.category.value,
                statement=entry.statement,
                location=str(entry.location)[:512] if entry.location is not None else None,
                confidence=entry.confidence,
                status=entry.status.value,
                confirmed=entry.status is NrrStatus.CONFIRMED,
            )
        )
    return rows


def _plain_version(outcome: _Outcome) -> str:
    """``ai.input_parse.parser_version`` holds one version, not the ``type@version`` label.

    The column is 32 characters and is the parser's own version; the joined label lives in the
    NRR's build record, which is where AI-INP-004 asks for it and where several parsers can be
    named truthfully.
    """
    return (outcome.parser_version or UNROUTED).rsplit("@", 1)[-1][:32]


def _decision_row(decision: _Decision, project_id: str) -> PolicyDecisionRow:
    """A parse has no run, so its decision is keyed to the project alone (AI-POL-008)."""
    return PolicyDecisionRow(
        id=new_id("apdc"),
        run_id=None,
        project_id=project_id,
        effective_policy=dict(decision.effective_policy),
        provider_class=decision.provider_class,
        model=decision.model,
        items=[dict(item) for item in decision.items],
        result=decision.result,
        denied_items=[dict(item) for item in decision.denied_items],
        model_denials=[dict(item) for item in decision.model_denials],
        warnings=list(decision.warnings),
    )


def _audit(
    session: Session,
    parse_id: str,
    pending: _Pending,
    *,
    nrr_artifact_id: str | None,
    operator: str,
) -> None:
    """Appendix A's own events: ``ai.input.parsed`` for a result, the failed one for none."""
    outcome = pending.outcome
    staged = pending.staged
    counts = (staged.record.get("counts") or {}) if staged is not None else {}
    after: dict[str, Any] = {
        "status": outcome.status.value,
        "inputArtifactId": pending.inputs.artifact_id,
        "inputHash": pending.inputs.content_hash,
        "parserType": outcome.parser_type.value if outcome.parser_type else UNROUTED,
        "parserVersion": outcome.parser_version or UNROUTED,
        "usedModel": outcome.used_model,
        "nrrArtifactId": nrr_artifact_id,
    }
    if nrr_artifact_id is not None and staged is not None:
        after["nrrHash"] = staged.handle.content_hash
        after["items"] = counts.get("items", 0)
        after["confirmed"] = counts.get("confirmed", 0)
        after["conflicts"] = counts.get("conflicts", 0)
        after["questions"] = len(staged.questions)
        after["dataClassification"] = staged.classification.value
    if outcome.error_code is not None:
        after["errorCode"] = outcome.error_code
        after["errorMessage"] = outcome.error_message
    audit_log.record(
        "ai.input.parsed" if nrr_artifact_id else "ai.input.parse_failed",
        session=session,
        object_type="InputParse",
        object_id=parse_id,
        project_id=pending.inputs.project_id,
        after=after,
        operator=operator,
        #: D-16: while the system has no authentication the name is the identity, and a parse
        #: is something a person asked for rather than the runtime's own bookkeeping.
        operator_is_user=True,
    )
    if outcome.status is ParseStatus.POLICY_DENIED:
        audit_log.record(
            "ai.policy.denied",
            session=session,
            object_type="InputParse",
            object_id=parse_id,
            project_id=pending.inputs.project_id,
            after={
                "parseId": parse_id,
                "deniedItems": [item.model_dump(mode="json") for item in outcome.rejected],
                "modelDenials": (
                    [dict(item) for item in pending.decision.model_denials]
                    if pending.decision is not None
                    else []
                ),
            },
            operator=operator,
        )


def _result_of(parse_id: str, pending: _Pending, *, nrr_artifact_id: str | None) -> ParsedInput:
    outcome = pending.outcome
    staged = pending.staged
    return ParsedInput(
        status=outcome.status,
        nrr_artifact_id=nrr_artifact_id,
        questions=list(staged.questions) if staged is not None else [],
        rejected_artifacts=list(outcome.rejected),
        parser_version=outcome.parser_version,
        parser_type=outcome.parser_type.value if outcome.parser_type else None,
        used_model=outcome.used_model,
        error_code=outcome.error_code,
        error_message=outcome.error_message,
        issues=_issues_of(outcome),
        parse_id=parse_id,
    )


def _release(idempotency_key: str, *, error_code: str) -> None:
    """Give the key back: a cancelled or unstarted parse is not a result (AI-INP-012)."""
    with unit_of_work() as session:
        idempotency.fail(
            IdemScope.INPUT_PARSE, idempotency_key, error_code=error_code, session=session
        )


__all__ = [
    "DRAWING_TOOL_SUFFIXES",
    "EXPORTED_AS_PARSERS",
    "LATE_MILESTONE_PARSERS_BY_SUFFIX",
    "MODEL_PARSER_VERSION",
    "MODEL_PARSERS_BY_MEDIA_TYPE",
    "MODEL_PARSERS_BY_SUFFIX",
    "PARSEABLE_INPUT_TYPES",
    "extraction_threshold",
    "route_parser",
    "run_parse",
]
