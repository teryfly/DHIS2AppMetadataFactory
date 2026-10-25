"""Working drafts: open, patch, reset, freeze, promote (BP-DRF, BP-VER, 6.1).

Transaction boundaries follow 00 doc 6.2:

* :func:`open_draft`, :func:`submit_patch`, :func:`reset_draft` and
  :func:`promote_draft` run inside the caller's unit of work;
* :func:`prepare_initial_blueprint`, :func:`prepare_draft_base` and
  :func:`freeze_draft` are external I/O — they read or stage artifact content and
  may call S4 — so they manage their own short transactions.

A project has at most one open draft (BP-DRF-001) and only one draft based on a
version can be promoted (BP-DRF-010), both enforced by partial unique indexes.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

from factory_foundation.api import (
    ConcurrencyError,
    DataClassification,
    FactoryError,
    NotFoundError,
    StagingHandle,
    SystemClock,
    current_session,
    external_io,
    has_active_uow,
    idempotency,
    new_id,
    new_session,
    normalize_hash,
    structure_hash,
    unit_of_work,
)
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from factory_blueprint import artifacts
from factory_blueprint.audit_log import record
from factory_blueprint.content.document import BlueprintDocument, entries_of, normalize
from factory_blueprint.content.initial import DEFAULT_CODE_PREFIX, initial_document
from factory_blueprint.content.layout import CONTAINER_BY_PATH
from factory_blueprint.dto.blueprint import InitialProjectInfo, VersionInfo
from factory_blueprint.dto.issue import Issue, make_issue, sort_issues
from factory_blueprint.dto.patch import (
    DraftInfo,
    FreezeResult,
    ImpactSet,
    MergeQuestionData,
    Patch,
    PatchContext,
    PatchOutcome,
    PromoteResult,
)
from factory_blueprint.enums import (
    ACTIVE_DRAFT_STATUSES,
    ChangeLevel,
    DeliveryMode,
    DesignStage,
    DraftStatus,
    IssueLevel,
    PatchStatus,
    RegistryEntryStatus,
    Section,
    UidAllocationStatus,
    VersionStatus,
    is_managed,
)
from factory_blueprint.errors import BlueprintError, DraftNotMaterialized, NotPromotable
from factory_blueprint.graph import queries
from factory_blueprint.impact import changelvl, impact
from factory_blueprint.impact.stage_map import allowed_sections
from factory_blueprint.indexes import rebuild_indexes
from factory_blueprint.models.draft import BlueprintDraftRow, BlueprintPatchRow
from factory_blueprint.models.prepared import PreparedContentRow
from factory_blueprint.models.uid import UidAllocationRow
from factory_blueprint.models.version import BlueprintVersionRow
from factory_blueprint.patch import validate as patch_validate
from factory_blueprint.registry import code as code_rules
from factory_blueprint.registry import conflicts, pool
from factory_blueprint.schema import migrations, store
from factory_blueprint.validators import completeness
from factory_blueprint.validators.blueprint import validate_blueprint
from factory_blueprint.version import numbering
from factory_blueprint.version import service as version_service

BLUEPRINT_TYPE = "BLUEPRINT"
INITIAL_KIND = "blueprint-initial"
#: the staged content of a delivery-mode upgrade, registered by commit_mode_upgrade
MODE_UPGRADE_KIND = "blueprint-mode-upgrade"
DEFAULT_CLASSIFICATION = DataClassification.INTERNAL


# --- helpers ---------------------------------------------------------------


def _session(session: Session | None) -> Session:
    return session if session is not None else current_session()


@contextmanager
def _reader(session: Session | None) -> Iterator[Session]:
    """A session for the read-only API: enlist when one is open, else self-contained.

    ``get_draft`` and ``draft_document`` are read-only functions, so S1 may call
    them either inside its transaction or on its own.
    """
    if session is not None:
        yield session
    elif has_active_uow():
        yield current_session()
    else:
        with unit_of_work() as new_session_value:
            yield new_session_value


def _read_session() -> Session:
    """A plain session for reads performed by an external-I/O function."""
    return new_session()


def _hash(document: dict[str, Any]) -> str:
    return structure_hash(document)


def _blocking(issues: list[Issue]) -> list[Issue]:
    return [issue for issue in issues if issue.level is IssueLevel.BLOCKING]


def _idempotency(scope: str) -> str:
    return f"bp.{scope}"


def _lock_draft(session: Session, draft_id: str, project_id: str) -> BlueprintDraftRow:
    draft = session.execute(
        select(BlueprintDraftRow).where(BlueprintDraftRow.id == draft_id).with_for_update()
    ).scalars().first()
    if draft is None:
        raise NotFoundError(f"unknown draft {draft_id}", code="NOT_FOUND")
    if draft.project_id != project_id:
        raise NotFoundError(
            f"draft {draft_id} does not belong to project {project_id}", code="NOT_FOUND"
        )
    return draft


def _get_draft(session: Session, draft_id: str) -> BlueprintDraftRow:
    draft = session.get(BlueprintDraftRow, draft_id)
    if draft is None:
        raise NotFoundError(f"unknown draft {draft_id}", code="NOT_FOUND")
    return draft


# --- initialization --------------------------------------------------------


@external_io
def prepare_initial_blueprint(project_id: str, project_info: InitialProjectInfo) -> StagingHandle:
    """Build the ``0.1.0`` document, stage it and record it for registration."""
    document = normalize(initial_document(project_info))
    handle = artifacts.stage_json(document, project_id, BLUEPRINT_TYPE)
    with unit_of_work() as session:
        session.add(
            PreparedContentRow(
                handle=handle.handle,
                project_id=project_id,
                kind=INITIAL_KIND,
                content=document,
                content_hash=_hash(document),
                source_artifact_ids=list(project_info.source_artifact_ids),
            )
        )
    return handle


def register_initial_blueprint(
    project_id: str,
    handle: StagingHandle | str,
    *,
    session: Session | None = None,
    created_by: str | None = None,
) -> VersionInfo:
    """Register the staged content as immutable version ``0.1.0`` (BP-VER-004)."""
    handle_id = handle.handle if isinstance(handle, StagingHandle) else handle
    sess = _session(session)
    if version_service.get_current(sess, project_id) is not None:
        raise FactoryError(
            f"project {project_id} already has a Blueprint version",
            code="BP_IMPORT_CONFLICT",
        )
    prepared = sess.get(PreparedContentRow, handle_id)
    if prepared is None or prepared.project_id != project_id:
        raise NotFoundError(
            f"unknown prepared content for handle {handle_id}", code="NOT_FOUND"
        )
    document = normalize(prepared.content)
    issues = sort_issues([*validate_blueprint(document), *completeness.decision_issues(document)])
    if _blocking(issues):
        raise FactoryError(
            "the initial Blueprint is not valid",
            code="SCHEMA_ERROR",
            details={"issues": [issue.message for issue in _blocking(issues)][:10]},
        )

    content_hash = _hash(document)
    sources = sorted({str(item) for item in prepared.source_artifact_ids})
    info = artifacts.register(
        handle_id,
        project_id=project_id,
        artifact_type=BLUEPRINT_TYPE,
        data_classification=_classification(sess, sources),
        source_artifact_ids=list(sources),
        session=sess,
        build_record={"blueprintSchemaVersion": document.get("blueprintSchemaVersion")},
    )
    row = version_service.insert_version(
        sess,
        version_id=new_id("bpv"),
        project_id=project_id,
        version=str(numbering.Version.initial()),
        parent_version=None,
        schema_version=int(document.get("blueprintSchemaVersion") or store.current_schema_version()),
        change_level=ChangeLevel.MINOR,
        content_hash=content_hash,
        artifact_id=info.id,
        source_artifact_ids=list(sources),
        decision_ids=sorted(_decision_ids(document)),
        created_by=created_by or "S2-initialize",
        promoted_from_stage=None,
        promoted_from_draft_id=None,
    )
    apply_version_semantics(row, document)
    rebuild_indexes(sess, project_id=project_id, blueprint_version=row.version, document=document)
    session_execute_delete_prepared(sess, handle_id)
    record(
        "bp.blueprint.initialized",
        object_type="blueprintVersion",
        object_id=row.id,
        project_id=project_id,
        after={"version": row.version, "contentHash": content_hash, "artifactId": info.id},
        session=sess,
    )
    return to_version_info(row)


def session_execute_delete_prepared(sess: Session, handle_id: str) -> None:
    sess.execute(delete(PreparedContentRow).where(PreparedContentRow.handle == handle_id))


# --- opening and materializing a draft -------------------------------------


def open_draft(
    project_id: str,
    stage: DesignStage | str,
    *,
    change_request_id: str | None = None,
    sections: list[Section] | None = None,
    session: Session | None = None,
    created_by: str | None = None,
) -> str:
    """Return the project's active draft, creating one only when none exists."""
    sess = _session(session)
    existing = sess.execute(
        select(BlueprintDraftRow)
        .where(
            BlueprintDraftRow.project_id == project_id,
            BlueprintDraftRow.status.in_(
                sorted(status.value for status in ACTIVE_DRAFT_STATUSES)
            ),
        )
        .with_for_update()
    ).scalars().first()
    if existing is not None:
        return existing.id

    design_stage = DesignStage(stage)
    current = version_service.require_current(sess, project_id)
    scope = sections or allowed_sections(design_stage, current.delivery_mode)
    draft = BlueprintDraftRow(
        id=new_id("dft"),
        project_id=project_id,
        base_version=current.version,
        stage_scope=[section.value for section in scope],
        stage=design_stage.value,
        change_request_id=change_request_id,
        status=DraftStatus.OPEN.value,
        content={},
        base_content={},
        content_hash="",
        created_by=created_by or current.created_by,
    )
    sess.add(draft)
    sess.flush()
    return draft.id


@external_io
def prepare_draft_base(project_id: str, draft_id: str) -> str:
    """Materialize the draft's base content from its version artifact.

    Reading artifact content is external I/O, so the caller runs this before the
    stage's first patch instead of inside its transaction.  Idempotent, and it
    applies the Blueprint schema migration of BP-SCH-007 as a SYSTEM-authored
    change which then rides on this stage's Gate approval.
    """
    with _read_session() as reader:
        draft = _get_draft(reader, draft_id)
        if draft.project_id != project_id:
            raise NotFoundError(
                f"draft {draft_id} does not belong to project {project_id}", code="NOT_FOUND"
            )
        if draft.content:
            return draft.content_hash
        base_version = draft.base_version

    with _read_session() as reader:
        row = version_service.get_version(reader, project_id, base_version)
        artifact_id, schema_version = row.artifact_id, row.schema_version
    document, _classification = artifacts.read_json(artifact_id)

    target = store.current_schema_version()
    if schema_version < target:
        if not migrations.has_migration_path(schema_version, target):
            from factory_blueprint.errors import SchemaVersionUnsupported

            raise SchemaVersionUnsupported(
                f"no Blueprint schema migration path from {schema_version} to {target}",
                details={"draftId": draft_id, "from": schema_version, "to": target},
            )
        document = migrations.migrate_document(document, schema_version, target).document
    base = normalize(document)

    with unit_of_work() as session:
        draft = _get_draft(session, draft_id)
        if not draft.content:
            draft.base_content = base
            draft.content = dict(base)
            draft.content_hash = _hash(base)
            session.flush()
        return draft.content_hash


def get_draft(draft_id: str, *, session: Session | None = None) -> DraftInfo:
    with _reader(session) as sess:
        return _get_draft_info(sess, draft_id)


def get_active_draft(project_id: str, *, session: Session | None = None) -> DraftInfo | None:
    """The project's open draft, or nothing when it has none (BP-DRF-011, BP-AC-15).

    S1 shows the stage view with the live draft's id, hash and promotability, and needs
    one call to ask; the whole draft content is deliberately not returned here — entry
    content comes from ``get_entries(source=DRAFT)``, because shipping a 5,000-object
    document to render a header is not what any caller asked for.
    """
    with _reader(session) as sess:
        draft_id = active_draft_id(sess, project_id)
        if draft_id is None:
            return None
        return _get_draft_info(sess, draft_id)


def _get_draft_info(sess: Session, draft_id: str) -> DraftInfo:
    draft = _get_draft(sess, draft_id)
    proposed = sess.execute(
        select(BlueprintPatchRow).where(
            BlueprintPatchRow.draft_id == draft_id,
            BlueprintPatchRow.status == PatchStatus.PROPOSED.value,
        )
    ).scalars().all()
    return DraftInfo(
        draft_id=draft.id,
        project_id=draft.project_id,
        base_version=draft.base_version,
        stage=DesignStage(draft.stage),
        stage_scope=[Section(value) for value in draft.stage_scope],
        status=DraftStatus(draft.status),
        content_hash=draft.content_hash,
        change_request_id=draft.change_request_id,
        frozen_hash=draft.frozen_hash,
        promotable=draft.promotable,
        open_patch_count=len(proposed),
    )


def draft_document(draft_id: str, *, session: Session | None = None) -> dict[str, Any]:
    """The materialized draft content."""
    with _reader(session) as sess:
        draft = _get_draft(sess, draft_id)
        return _require_content(draft)


def draft_base_document(draft_id: str, *, session: Session | None = None) -> dict[str, Any]:
    """The base version's content as it was when the draft materialized (BP-DIFF-003).

    Kept on the row for exactly this comparison: change level and the draft Diff must
    read the same snapshot the patches were applied to, and reading the version's
    artifact here would mean an artifact read inside a transaction (00 doc 6.2).
    """
    with _reader(session) as sess:
        draft = _get_draft(sess, draft_id)
        if not draft.base_content:
            raise DraftNotMaterialized(
                f"draft {draft_id} has no recorded base content; "
                "call prepare_draft_base first",
                details={"draftId": draft_id, "baseVersion": draft.base_version},
            )
        return dict(draft.base_content)


def _require_content(draft: BlueprintDraftRow) -> dict[str, Any]:
    if not draft.content:
        raise DraftNotMaterialized(
            f"draft {draft.id} has no materialized content; call prepare_draft_base first",
            details={"draftId": draft.id, "baseVersion": draft.base_version},
        )
    return dict(draft.content)


# --- patch submission ------------------------------------------------------


def submit_patch(
    project_id: str,
    draft_id: str,
    patch: Patch,
    context: PatchContext,
    *,
    idempotency_key: str,
    session: Session | None = None,
) -> PatchOutcome:
    """Validate one patch and stack it on the draft (BP-DRF-003 … 006)."""
    sess = _session(session)
    digest = structure_hash(
        {
            "draftId": draft_id,
            "patchId": patch.patch_id,
            "operations": patch.operations,
            "stage": context.stage.value,
        }
    )
    scope = _idempotency("patch.submit")
    begun = idempotency.begin(scope, idempotency_key, digest, session=sess)
    if begun.already_completed and begun.first_result is not None:
        return PatchOutcome.model_validate(begun.first_result)

    draft = _lock_draft(sess, draft_id, project_id)
    if not draft.content:
        raise DraftNotMaterialized(
            f"draft {draft_id} needs prepare_draft_base before the first patch",
            details={"draftId": draft_id, "baseVersion": draft.base_version},
        )

    if patch.base_version != draft.base_version:
        outcome = _reject(sess, draft, patch, [], base_mismatch=True, clock=SystemClock().now())
        _complete(sess, scope, idempotency_key, outcome)
        return outcome

    validation = patch_validate.validate_patch(
        draft.content,
        patch.operations,
        allowed_sections=[Section(value) for value in draft.stage_scope],
        schema_version=_schema_version(draft.content),
        mapping_frozen=_mapping_is_frozen(sess, draft),
        change_request_id=context.change_request_id or draft.change_request_id,
        stage=context.stage,
    )
    if not validation.accepted or validation.document is None:
        outcome = _reject(
            sess, draft, patch, validation.issues, base_mismatch=False, clock=SystemClock().now()
        )
        _complete(sess, scope, idempotency_key, outcome)
        return outcome

    moment = SystemClock().now()
    # mutate the applied document in place first: the UIDs S2 allocates become part
    # of the draft content, so the hash below must already include them
    assigned = _assign_uids(
        sess, draft, validation.document, validation.new_create_keys, patch_id=patch.patch_id, moment=moment
    )
    content = normalize(validation.document)
    draft.content = content
    draft.content_hash = _hash(content)
    draft.status = DraftStatus.OPEN.value
    draft.frozen_hash = None
    draft.staging_handle = None
    draft.frozen_issues = None
    draft.promotable = None
    sess.add(
        BlueprintPatchRow(
            id=_unique_patch_id(sess, patch.patch_id),
            project_id=draft.project_id,
            draft_id=draft.id,
            base_version=draft.base_version,
            author_kind=patch.author.kind.value,
            author_ref=patch.author.ref,
            decision_ids=list(patch.decision_ids),
            operations=list(patch.operations),
            rationale=patch.rationale,
            declared_impact=patch.declared_impact,
            change_request_id=context.change_request_id or draft.change_request_id,
            status=PatchStatus.PROPOSED.value,
            assigned_uids=assigned,
            applied_hash=draft.content_hash,
        )
    )
    sess.flush()
    record(
        "bp.patch.submitted",
        object_type="patch",
        object_id=patch.patch_id,
        project_id=draft.project_id,
        after={
            "draftId": draft.id,
            "draftHash": draft.content_hash,
            "stage": context.stage.value,
            "assignedUids": assigned,
        },
        session=sess,
    )
    outcome = PatchOutcome(
        patch_id=patch.patch_id,
        status=PatchStatus.PROPOSED,
        draft_id=draft.id,
        draft_hash=draft.content_hash,
        assigned_uids=assigned,
    )
    _complete(sess, scope, idempotency_key, outcome)
    return outcome


def _complete(sess: Session, scope: str, key: str, outcome: PatchOutcome) -> None:
    idempotency.complete(scope, key, outcome.model_dump(mode="json"), session=sess)


def _schema_version(document: dict[str, Any]) -> int:
    value = document.get("blueprintSchemaVersion")
    return int(value) if isinstance(value, int) else store.current_schema_version()


def _mapping_is_frozen(sess: Session, draft: BlueprintDraftRow) -> bool:
    """Derive BP-DRF-005.5 from the version itself, without asking S1 about Gates.

    The mapping is frozen once the base version was promoted at MAPPING or later
    and actually carries a mapping.
    """
    row = version_service.get_version(sess, draft.project_id, draft.base_version)
    if not row.promoted_from_stage:
        return False
    if DesignStage(row.promoted_from_stage) is DesignStage.REQUIREMENTS:
        return False
    mapping = draft.base_content.get(Section.MAPPING.value) or {}
    return bool(mapping.get("entries"))


def _assign_uids(
    sess: Session,
    draft: BlueprintDraftRow,
    document: dict[str, Any],
    new_create_keys: list[str],
    *,
    patch_id: str,
    moment: Any,
) -> dict[str, str]:
    """Take UIDs from the pool, write them in, and record the allocation.

    A REUSE, REFERENCE or UPDATE entry carries ``observedUid`` — an observation of
    an object that already exists — and S2, not the author, turns it into the
    entry's ``uid`` (BP-REG-001).  A CREATE entry gets a fresh UID from the pool.
    """
    container = CONTAINER_BY_PATH["/registry/entries"]
    # live references into the document: the entries written here are the ones the
    # draft hash is computed from
    entries = entries_of(document, container)
    by_key = {str(entry.get("businessKey")): entry for entry in entries}

    assigned: dict[str, str] = {}
    pending: list[str] = []
    for business_key in sorted({*new_create_keys, *by_key}):
        entry = by_key.get(business_key)
        if entry is None or entry.get("uid"):
            continue
        seen = entry.get("observedUid")
        if isinstance(seen, str) and seen:
            entry["uid"] = seen
            entry["managed"] = is_managed(str(entry.get("disposition") or ""))
            # the observation is not an allocation: S2 did not issue this UID, and a
            # rival project's claim on it must surface as a BLOCKING ownership issue at
            # freeze rather than as a key conflict here (BP-AC-08)
            assigned[business_key] = seen
        elif business_key in new_create_keys:
            pending.append(business_key)

    taken = pool.take(sess, draft.project_id, len(pending))
    policy = document.get(Section.GENERATION_POLICY.value) or {}
    prefix = str(policy.get("codePrefix") or DEFAULT_CODE_PREFIX)
    convention = str(policy.get("namingConvention") or code_rules.PREFIX_TYPE_KEY)
    # identity is S2's to assign: an author-supplied code is replaced, never trusted
    for business_key in pending:
        entry = by_key.get(business_key)
        if entry is not None:
            entry.pop("code", None)
    taken_codes: set[tuple[str, str]] = {
        (str(entry.get("dhis2Type")), str(entry.get("code")))
        for entry in entries
        if entry.get("code")
    }
    try:
        for index, business_key in enumerate(sorted(pending)):
            entry = by_key.get(business_key)
            if entry is None:
                raise FactoryError(
                    f"registry entry {business_key!r} disappeared during UID assignment",
                    code="REGISTRY_ERROR",
                )
            uid, source_environment = taken[index]
            dhis2_type = str(entry.get("dhis2Type") or "")
            generated = _unique_code(
                prefix=prefix,
                convention=convention,
                dhis2_type=dhis2_type,
                business_key=business_key,
                position=index,
                taken=taken_codes,
            )
            taken_codes.add((dhis2_type, generated))
            entry["uid"] = uid
            entry["code"] = generated
            entry["managed"] = True
            entry["status"] = RegistryEntryStatus.PROPOSED.value
            assigned[business_key] = uid
            pool.record_allocation(
                sess,
                uid=uid,
                project_id=draft.project_id,
                business_key=business_key,
                dhis2_type=dhis2_type,
                source_environment=source_environment,
                allocated_at=moment,
                patch_id=patch_id,
            )
    except FactoryError:
        pool.retire(sess, sorted(taken[index][0] for index in range(len(pending))), retired_at=moment)
        raise
    return assigned


def _unique_code(
    *,
    prefix: str,
    convention: str,
    dhis2_type: str,
    business_key: str,
    position: int,
    taken: set[tuple[str, str]],
) -> str:
    candidate = code_rules.derive_code(
        prefix=prefix,
        naming_convention=convention,
        dhis2_type=dhis2_type,
        business_key=business_key,
        position=position,
    )
    if (dhis2_type, candidate) not in taken:
        return candidate
    suffix = 1
    while (dhis2_type, f"{candidate}_{suffix}") in taken:
        suffix += 1
    return f"{candidate}_{suffix}"


def _unique_patch_id(sess: Session, requested: str) -> str:
    """The author's patch ID is the row ID, unless it was already used (a retried
    or repeated patchId must not collide with the earlier record)."""
    if sess.get(BlueprintPatchRow, requested) is None:
        return requested
    suffix = 1
    while sess.get(BlueprintPatchRow, f"{requested}#{suffix}") is not None:
        suffix += 1
    return f"{requested}#{suffix}"


def _reject(
    sess: Session,
    draft: BlueprintDraftRow,
    patch: Patch,
    issues: list[Issue],
    *,
    base_mismatch: bool,
    clock: Any,
) -> PatchOutcome:
    ordered = sort_issues(issues)
    merge_question: MergeQuestionData | None = None
    if base_mismatch:
        ordered = [
            make_issue(
                patch_validate.RULE_STAGE_SCOPE,
                IssueLevel.BLOCKING,
                f"patch baseVersion {patch.base_version} does not match the draft's base "
                f"version {draft.base_version}",
                business_key=patch.patch_id,
                source_stage=DesignStage.MAPPING,
                remediation="rebase the patch on the current draft or merge it manually",
            )
        ]
        merge_question = MergeQuestionData(
            project_id=draft.project_id,
            draft_id=draft.id,
            patch_id=patch.patch_id,
            base_version=patch.base_version,
            draft_version=draft.base_version,
            draft_hash=draft.content_hash,
            rationale=patch.rationale,
            question=(
                f"Patch {patch.patch_id} was written against Blueprint "
                f"{patch.base_version}, but the working draft is based on "
                f"{draft.base_version}. Re-apply the patch, or merge it by hand."
            ),
            options=[
                {"key": "REBASE", "label": "Re-apply the patch on the current draft"},
                {"key": "DISCARD", "label": "Discard the patch"},
            ],
        )
    sess.add(
        BlueprintPatchRow(
            id=_unique_patch_id(sess, patch.patch_id),
            project_id=draft.project_id,
            draft_id=draft.id,
            base_version=patch.base_version,
            author_kind=patch.author.kind.value,
            author_ref=patch.author.ref,
            decision_ids=list(patch.decision_ids),
            operations=list(patch.operations),
            rationale=patch.rationale,
            declared_impact=patch.declared_impact,
            status=PatchStatus.REJECTED.value,
            reject_issues=[issue.model_dump(mode="json") for issue in ordered],
        )
    )
    sess.flush()
    record(
        "bp.patch.rejected",
        object_type="patch",
        object_id=patch.patch_id,
        project_id=draft.project_id,
        after={
            "draftId": draft.id,
            "reason": "BASE_VERSION_MISMATCH" if base_mismatch else "VALIDATION",
            "blocking": len(ordered),
            "rules": sorted({issue.rule_id for issue in ordered}),
        },
        session=sess,
    )
    del clock
    return PatchOutcome(
        patch_id=patch.patch_id,
        status=PatchStatus.REJECTED,
        draft_id=draft.id,
        draft_hash=draft.content_hash,
        issues=ordered,
        merge_question=merge_question,
        blocking_count=len(ordered),
    )


# --- reset, freeze, promote ------------------------------------------------


def reset_draft(draft_id: str, *, session: Session | None = None) -> int:
    """Discard every PROPOSED patch and return the draft to its base (BP-DRF-007)."""
    sess = _session(session)
    project_id = _get_draft(sess, draft_id).project_id
    draft = _lock_draft(sess, draft_id, project_id)
    proposed = sess.execute(
        select(BlueprintPatchRow).where(
            BlueprintPatchRow.draft_id == draft_id,
            BlueprintPatchRow.status == PatchStatus.PROPOSED.value,
        )
    ).scalars().all()
    retired_set: set[str] = set()
    for patch in proposed:
        assigned: dict[str, Any] = patch.assigned_uids if isinstance(patch.assigned_uids, dict) else {}
        for value in assigned.values():
            retired_set.add(str(value))
    retired = sorted(retired_set)
    moment = SystemClock().now()
    for patch in proposed:
        patch.status = PatchStatus.DISCARDED.value
    if retired:
        pool.retire(sess, retired, retired_at=moment)
    if not draft.base_content:
        raise DraftNotMaterialized(
            f"draft {draft_id} has no base content to return to", details={"draftId": draft_id}
        )
    draft.content = dict(draft.base_content)
    draft.content_hash = _hash(draft.content)
    draft.status = DraftStatus.RESET.value
    draft.frozen_hash = None
    draft.staging_handle = None
    draft.frozen_issues = None
    draft.promotable = None
    sess.flush()
    record(
        "bp.draft.reset",
        object_type="draft",
        object_id=draft_id,
        project_id=draft.project_id,
        after={"discardedPatches": len(proposed), "retiredUids": retired},
        session=sess,
    )
    return len(proposed)


def _applicable_at_stage(issues: list[Issue], stage: DesignStage) -> list[Issue]:
    """A draft is deliberately incomplete: only rules that already apply may block.

    Freezing the DOMAIN stage must not report that a domain entity has no mapping
    yet — that is the next stage's job.  A rule whose source stage comes later than
    this draft's stage is therefore not reported (BP-VAL-004, appendix A).
    """
    stages = list(DesignStage)
    rank = stages.index(stage)
    applicable = set(stages[: rank + 1])
    return sort_issues(
        [
            issue
            for issue in issues
            if issue.source_stage is None or issue.source_stage in applicable
        ]
    )


@external_io
def freeze_draft(
    draft_id: str, target_environment_keys: list[str]
) -> FreezeResult:
    """Materialize, validate, conflict-check and stage the draft (BP-DRF-008)."""
    with _read_session() as reader:
        draft = _get_draft(reader, draft_id)
        project_id = draft.project_id
        if not draft.content:
            raise DraftNotMaterialized(
                f"draft {draft_id} has nothing to freeze", details={"draftId": draft_id}
            )
        content = normalize(dict(draft.content))

    stage = DesignStage(draft.stage)
    issues = _applicable_at_stage(
        sort_issues([*validate_blueprint(content), *completeness.decision_issues(content)]),
        stage,
    )
    if target_environment_keys:
        # Asked before the read session opens: UID and code existence are S4 calls, and S0
        # refuses an @external_io function while any unit of work is current (00 doc 6.2).
        on_instance = conflicts.check_environment_facing(content, target_environment_keys)
        with _read_session() as reader:
            issues = sort_issues(
                [
                    *issues,
                    *on_instance,
                    *conflicts.check_database_facing(reader, project_id, content, target_environment_keys),
                ]
            )
    promotable = not _blocking(issues)
    # A promoted draft's Registry entries become APPROVED, and this is the only place that
    # approval can happen. `promote_draft` used to call `approve_registry_entries` after
    # `artifacts.register` had already consumed the staged bytes, so the mutation landed on a dict
    # nothing persisted: every reader of the version artifact — the compiler,
    # `GET /blueprint/entries`, S1's review screens — saw PROPOSED forever, and BPV-XA-004 blocked
    # any later page referencing an entry a Gate had already approved, with no way for an operator
    # to author out of it.
    #
    # After validation, so an approval cannot mask a completeness problem, and before hashing, so
    # `content_hash`, `frozen_hash` and the staged artifact describe the same bytes. A rejected Gate
    # creates no version and the draft's own stored content still says PROPOSED.
    approve_registry_entries(content)
    draft_hash = _hash(content)
    handle = artifacts.stage_json(content, project_id, BLUEPRINT_TYPE)

    with unit_of_work() as session:
        draft = _get_draft(session, draft_id)
        draft.content = content
        draft.content_hash = draft_hash
        draft.status = DraftStatus.FROZEN.value
        draft.frozen_hash = draft_hash
        draft.staging_handle = handle.handle
        draft.frozen_issues = [issue.model_dump(mode="json") for issue in issues]
        draft.promotable = promotable
        session.flush()
        record(
            "bp.draft.frozen",
            object_type="draft",
            object_id=draft_id,
            project_id=project_id,
            after={
                "draftHash": draft_hash,
                "stagingHandle": handle.handle,
                "promotable": promotable,
                "blocking": len(_blocking(issues)),
                "targetEnvironments": sorted(target_environment_keys),
            },
            session=session,
        )
    return FreezeResult(
        draft_id=draft_id,
        draft_hash=draft_hash,
        staging_handle=handle.handle,
        promotable=promotable,
        issues=issues,
        blocking_count=len(_blocking(issues)),
        warnings=[issue for issue in issues if issue.level is IssueLevel.WARNING],
    )


def promote_draft(
    draft_id: str,
    expected_hash: str,
    *,
    idempotency_key: str,
    session: Session | None = None,
    created_by: str | None = None,
) -> PromoteResult:
    """Turn the frozen draft into a new immutable version (BP-DRF-009)."""
    sess = _session(session)
    expected = normalize_hash(expected_hash)
    scope = _idempotency("draft.promote")
    digest = structure_hash({"draftId": draft_id, "expectedHash": expected})
    begun = idempotency.begin(scope, idempotency_key, digest, session=sess)
    if begun.already_completed and begun.first_result is not None:
        return PromoteResult.model_validate(begun.first_result)

    draft = _lock_draft(sess, draft_id, _get_draft(sess, draft_id).project_id)
    if draft.content_hash != expected:
        raise ConcurrencyError(
            "the draft is not in the state the caller approved",
            details={
                "draftId": draft_id,
                "expected": expected,
                "current": draft.content_hash,
                "frozen": draft.frozen_hash,
            },
        )
    if draft.frozen_hash is None or draft.staging_handle is None:
        raise NotPromotable(
            f"draft {draft_id} has not been frozen at this hash", details={"draftId": draft_id}
        )
    if expected != draft.frozen_hash:
        raise NotPromotable(
            "the recorded freeze does not match the expected hash; freeze again",
            details={"draftId": draft_id, "expected": expected, "frozen": draft.frozen_hash},
        )
    if draft.promotable is False:
        raise NotPromotable(
            f"draft {draft_id} carries BLOCKING issues", details={"draftId": draft_id}
        )
    if draft.status == DraftStatus.PROMOTED.value:
        raise ConcurrencyError(
            f"draft {draft_id} was already promoted", details={"draftId": draft_id}
        )

    base = version_service.get_version(sess, draft.project_id, draft.base_version)
    document = normalize(dict(draft.content))
    base_document = normalize(dict(draft.base_content))
    change_level, changes = changelvl.determine(base_document, document)
    new_version = numbering.next_version(numbering.Version.parse(base.version), change_level)

    info = artifacts.register(
        draft.staging_handle,
        project_id=draft.project_id,
        artifact_type=BLUEPRINT_TYPE,
        data_classification=_classification(sess, list(base.source_artifact_ids)),
        source_artifact_ids=list(base.source_artifact_ids),
        session=sess,
        build_record={
            "blueprintSchemaVersion": document.get("blueprintSchemaVersion"),
            "changeLevel": change_level.value,
            "promotedFromDraft": draft.id,
        },
    )

    # Already reflected in the staged bytes the version stores (see `freeze_draft`). Kept so the
    # in-memory document feeding the index rebuild and the version semantics below agrees with
    # them even if a caller promotes without re-reading the artifact.
    approve_registry_entries(document)
    row = version_service.insert_version(
        sess,
        version_id=new_id("bpv"),
        project_id=draft.project_id,
        version=str(new_version),
        parent_version=base.version,
        schema_version=int(document.get("blueprintSchemaVersion") or base.schema_version),
        change_level=change_level,
        content_hash=draft.frozen_hash,
        artifact_id=info.id,
        source_artifact_ids=list(base.source_artifact_ids),
        decision_ids=sorted(_decision_ids(document)),
        created_by=created_by or draft.created_by,
        promoted_from_stage=draft.stage,
        promoted_from_draft_id=draft.id,
    )
    apply_version_semantics(row, document)
    _retire_removed_uids(sess, draft.project_id, base_document, document)
    rebuild_indexes(
        sess, project_id=draft.project_id, blueprint_version=row.version, document=document
    )

    patches = sess.execute(
        select(BlueprintPatchRow).where(
            BlueprintPatchRow.draft_id == draft.id,
            BlueprintPatchRow.status == PatchStatus.PROPOSED.value,
        )
    ).scalars().all()
    for patch in patches:
        patch.status = PatchStatus.APPLIED.value
        patch.result_version = row.version
        patch.applied_hash = draft.frozen_hash
    draft.status = DraftStatus.PROMOTED.value
    sess.flush()

    downstream = queries.downstream(
        sess, draft.project_id, base.version, {c.business_key for c in changes if c.business_key}
    )
    computed = impact.compute_impact(
        BlueprintDocument(document),
        changes,
        change_level,
        DeliveryMode(row.delivery_mode),
        downstream,
    )
    affected = [DesignStage(value) for value in computed["stages"]]
    result = PromoteResult(
        version=str(new_version),
        change_level=change_level.value,
        artifact_id=info.id,
        content_hash=draft.frozen_hash,
        affected_stages=affected,
        impact=ImpactSet(
            sections=[Section(value) for value in computed["sections"]],
            stages=affected,
            business_keys=computed["businessKeys"],
            page_keys=computed["pageKeys"],
            operation_keys=computed["operationKeys"],
            artifact_types=computed["artifactTypes"],
        ),
        applied_patch_ids=sorted(patch.id for patch in patches),
    )
    record(
        "bp.version.created",
        object_type="blueprintVersion",
        object_id=row.id,
        project_id=draft.project_id,
        before={"version": base.version, "contentHash": base.content_hash},
        after={
            "version": row.version,
            "contentHash": draft.frozen_hash,
            "changeLevel": change_level.value,
            "artifactId": info.id,
            "affectedStages": [stage.value for stage in affected],
            "appliedPatches": result.applied_patch_ids,
        },
        session=sess,
    )
    idempotency.complete(scope, idempotency_key, result.model_dump(mode="json"), session=sess)
    return result


# --- delivery mode upgrade (SRS 7.3, BP-VER-005, appendix B) ----------------


@external_io
def prepare_mode_upgrade(project_id: str) -> StagingHandle:
    """Stage the version a delivery-mode upgrade produces, outside any transaction.

    Mode A has no app of its own; upgrading to B does not invent one — it opens the
    PAGE_DESIGN stage's sections so the pages, API intents and UI intents can be designed
    and approved afterwards.  The only content change here is `project.deliveryMode`.
    """
    with unit_of_work() as session:
        current = session.execute(
            select(BlueprintVersionRow).where(
                BlueprintVersionRow.project_id == project_id,
                BlueprintVersionRow.status == VersionStatus.CURRENT.value,
            )
        ).scalars().first()
        if current is None:
            raise BlueprintError(
                f"project {project_id} has no current version to upgrade",
                code="NOT_FOUND",
                details={"projectId": project_id},
            )
        if active_draft_id(session, project_id) is not None:
            raise BlueprintError(
                "a delivery-mode upgrade changes the base version, so it cannot run while a "
                "draft is open; promote or reset the draft first",
                code="CONFLICT",
                details={"projectId": project_id},
            )
        if current.delivery_mode != DeliveryMode.A.value:
            raise BlueprintError(
                f"project {project_id} is already delivery mode {current.delivery_mode}; only "
                "A -> B is an upgrade (appendix B)",
                code="CONFLICT",
                details={"deliveryMode": current.delivery_mode},
            )
        artifact_id = current.artifact_id

    # reading the version's content is external I/O, so it happens outside the block
    base_document, _classification = artifacts.read_json(artifact_id)
    base_document = normalize(dict(base_document))
    upgraded = dict(base_document)
    project = dict(upgraded.get("project") or {})
    project["deliveryMode"] = DeliveryMode.B.value
    upgraded["project"] = project
    document = normalize(upgraded)

    blocking = _blocking(
        sort_issues([*validate_blueprint(document), *completeness.decision_issues(document)])
    )
    if blocking:
        raise BlueprintError(
            "the upgraded Blueprint does not validate, so the mode change is refused",
            code="VALIDATION_FAILED",
            details={"issues": [issue.summary() for issue in blocking]},
        )
    handle = artifacts.stage_json(document, project_id, BLUEPRINT_TYPE)
    with unit_of_work() as session:
        session.add(
            PreparedContentRow(
                handle=handle.handle,
                project_id=project_id,
                kind=MODE_UPGRADE_KIND,
                content=document,
                base_content=base_document,
                content_hash=_hash(document),
                source_artifact_ids=[current.artifact_id],
            )
        )
    return handle


def commit_mode_upgrade(
    project_id: str,
    handle: StagingHandle | str,
    idempotency_key: str,
    *,
    session: Session | None = None,
    created_by: str | None = None,
) -> PromoteResult:
    """Register the staged upgrade as a new immutable version (BP-VER-005)."""
    sess = _session(session)
    handle_id = handle.handle if isinstance(handle, StagingHandle) else str(handle)
    digest = structure_hash({"project": project_id, "handle": handle_id})
    scope = _idempotency("mode.upgrade")
    begun = idempotency.begin(scope, idempotency_key, digest, session=sess)
    if begun.already_completed and begun.first_result is not None:
        return PromoteResult.model_validate(begun.first_result)

    prepared = sess.execute(
        select(PreparedContentRow).where(
            PreparedContentRow.handle == handle_id,
            PreparedContentRow.project_id == project_id,
            PreparedContentRow.kind == MODE_UPGRADE_KIND,
        )
    ).scalars().first()
    if prepared is None:
        raise BlueprintError(
            f"no staged mode upgrade {handle_id!r} for project {project_id}",
            code="NOT_FOUND",
            details={"projectId": project_id, "handle": handle_id},
        )
    base = version_service.require_current(sess, project_id)
    base_document = normalize(dict(prepared.base_content or {}))
    document = normalize(dict(prepared.content))
    change_level, changes = changelvl.determine(base_document, document)
    new_version = numbering.next_version(numbering.Version.parse(base.version), change_level)

    info = artifacts.register(
        prepared.handle,
        project_id=project_id,
        artifact_type=BLUEPRINT_TYPE,
        data_classification=_classification(sess, list(base.source_artifact_ids)),
        source_artifact_ids=list(base.source_artifact_ids),
        session=sess,
        build_record={
            "blueprintSchemaVersion": document.get("blueprintSchemaVersion"),
            "changeLevel": change_level.value,
            "deliveryModeUpgrade": f"{DeliveryMode.A.value}->{DeliveryMode.B.value}",
        },
    )
    row = version_service.insert_version(
        sess,
        version_id=new_id("bpv"),
        project_id=project_id,
        version=str(new_version),
        parent_version=base.version,
        schema_version=int(document.get("blueprintSchemaVersion") or base.schema_version),
        change_level=change_level,
        content_hash=prepared.content_hash,
        artifact_id=info.id,
        source_artifact_ids=list(base.source_artifact_ids),
        decision_ids=sorted(_decision_ids(document)),
        created_by=created_by or "system",
        promoted_from_stage=None,
        promoted_from_draft_id=None,
    )
    apply_version_semantics(row, document)
    rebuild_indexes(sess, project_id=project_id, blueprint_version=row.version, document=document)

    computed = impact.compute_impact(
        BlueprintDocument(document),
        changes,
        change_level,
        DeliveryMode.B,
        [],
    )
    # a mode B project still owes its pages, so PAGE_DESIGN is always reopened
    stages = sorted(
        {str(stage) for stage in computed["stages"]} | {DesignStage.PAGE_DESIGN.value},
        key=lambda value: [stage.value for stage in DesignStage].index(value),
    )
    affected = [DesignStage(value) for value in stages]
    result = PromoteResult(
        version=str(new_version),
        change_level=change_level.value,
        artifact_id=info.id,
        content_hash=prepared.content_hash,
        affected_stages=affected,
        impact=ImpactSet(
            sections=[Section(value) for value in computed["sections"]],
            stages=affected,
            business_keys=computed["businessKeys"],
            page_keys=computed["pageKeys"],
            operation_keys=computed["operationKeys"],
            artifact_types=computed["artifactTypes"],
        ),
        applied_patch_ids=[],
    )
    record(
        "bp.version.created",
        object_type="blueprintVersion",
        object_id=row.id,
        project_id=project_id,
        before={"version": base.version, "contentHash": base.content_hash, "deliveryMode": DeliveryMode.A.value},
        after={
            "version": row.version,
            "contentHash": prepared.content_hash,
            "changeLevel": change_level.value,
            "artifactId": info.id,
            "deliveryMode": DeliveryMode.B.value,
            "affectedStages": [stage.value for stage in affected],
        },
        session=sess,
    )
    sess.delete(prepared)
    sess.flush()
    idempotency.complete(scope, idempotency_key, result.model_dump(mode="json"), session=sess)
    return result


# --- shared mapping between documents and index rows ------------------------


def apply_version_semantics(row: BlueprintVersionRow, document: dict[str, Any]) -> None:
    """Mirror the project semantics into the index for cheap reads (D-15)."""
    project = document.get(Section.PROJECT.value) or {}
    row.delivery_mode = str(project.get("deliveryMode") or DeliveryMode.B.value)
    row.project_type = str(project.get("projectType") or "BROWNFIELD")
    row.default_language = str(project.get("defaultLanguage") or "en")
    row.translation_languages = list(project.get("translationLanguages") or [])
    target = project.get("targetDhis2") or {}
    row.target_dhis2_version_constraint = str(target.get("versionConstraint") or "")
    fixtures = document.get(Section.FIXTURES.value) or {}
    row.has_fixture_specifications = bool(fixtures.get("specifications"))


def active_draft_id(session: Session, project_id: str) -> str | None:
    """The project's OPEN or FROZEN draft, if it has one (SRS 7.2 DRAFT source)."""
    return session.execute(
        select(BlueprintDraftRow.id)
        .where(
            BlueprintDraftRow.project_id == project_id,
            BlueprintDraftRow.status.in_(
                sorted(status.value for status in ACTIVE_DRAFT_STATUSES)
            ),
        )
        .order_by(BlueprintDraftRow.created_at.desc())
    ).scalars().first()


def active_draft_document(session: Session, project_id: str) -> dict[str, Any] | None:
    """The working copy of the active draft, or None when there is nothing to show.

    ``get_entries(source="DRAFT")`` uses this so a Gate can display proposed entries
    that no version contains yet.
    """
    draft_id = active_draft_id(session, project_id)
    if draft_id is None:
        return None
    content = _get_draft(session, draft_id).content
    return content if isinstance(content, dict) and content else None


def approve_registry_entries(document: dict[str, Any]) -> None:
    """A promoted draft's entries become APPROVED as part of the new version."""
    container = CONTAINER_BY_PATH["/registry/entries"]
    for entry in entries_of(document, container):
        entry["status"] = RegistryEntryStatus.APPROVED.value


def _decision_ids(document: dict[str, Any]) -> set[str]:
    ids: set[str] = set()
    for path, field in (
        ("/architecture/decisions", "decisionId"),
        ("/mapping/entries", "decisionId"),
        ("/registry/entries", "decisionId"),
    ):
        for entry in BlueprintDocument(document).container_entries(CONTAINER_BY_PATH[path]):
            value = entry.get(field)
            if isinstance(value, str):
                ids.add(value)
    return ids


def _retire_removed_uids(
    sess: Session, project_id: str, base_document: dict[str, Any], document: dict[str, Any]
) -> None:
    """BP-REG-006: a removed entry's UID retires; a DEPRECATED one is flagged."""
    before, after = _uid_map(base_document), _uid_map(document)
    moment = SystemClock().now()
    removed = sorted(uid for key, uid in before.items() if key not in after)
    if removed:
        pool.retire(sess, removed, retired_at=moment)
    deprecated = sorted(
        uid
        for key, uid in after.items()
        if before.get(key) == uid and _disposition(document, key) == "DEPRECATED"
    )
    if deprecated:
        for uid in deprecated:
            row = sess.get(UidAllocationRow, uid)
            if row is not None and row.project_id == project_id:
                row.status = UidAllocationStatus.DEPRECATED.value
                row.retired_at = moment
        sess.flush()


def _uid_map(document: dict[str, Any]) -> dict[str, str]:
    container = CONTAINER_BY_PATH["/registry/entries"]
    return {
        str(entry["businessKey"]): str(entry["uid"])
        for entry in BlueprintDocument(document).container_entries(container)
        if isinstance(entry.get("uid"), str) and isinstance(entry.get("businessKey"), str)
    }


def _disposition(document: dict[str, Any], business_key: str) -> str | None:
    container = CONTAINER_BY_PATH["/registry/entries"]
    for entry in BlueprintDocument(document).container_entries(container):
        if entry.get("businessKey") == business_key:
            return str(entry.get("disposition"))
    return None


def _classification(sess: Session, source_ids: list[str]) -> DataClassification:
    """Never below the highest source classification (FD-ART-008, BP-CMP-007)."""
    levels = [DEFAULT_CLASSIFICATION]
    for artifact_id in source_ids:
        try:
            levels.append(artifacts.read_metadata(artifact_id, session=sess).data_classification)
        except NotFoundError:
            continue
    return artifacts.highest_classification(levels)


def to_version_info(row: BlueprintVersionRow) -> VersionInfo:
    return VersionInfo(
        blueprint_id=row.id,
        project_id=row.project_id,
        version=row.version,
        parent_version=row.parent_version,
        schema_version=row.schema_version,
        change_level=ChangeLevel(row.change_level),
        content_hash=row.content_hash,
        artifact_id=row.artifact_id,
        status=row.status,
        source_artifact_ids=list(row.source_artifact_ids),
        decision_ids=list(row.decision_ids),
        created_by=row.created_by,
        created_at=row.created_at.isoformat(),
        promoted_from_stage=(
            DesignStage(row.promoted_from_stage) if row.promoted_from_stage else None
        ),
    )
