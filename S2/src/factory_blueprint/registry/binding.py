"""Environment bindings (BP-REG-010, D-02).

A version is environment-independent; each environment's UIDs and observation
baselines live in ``bp.environment_binding`` and are resolved while compiling for
that environment.  Resolution is automatic (code first, then the design reference
environment's UID; UPDATE by UID only); when it fails the caller gets a BLOCKING
issue and the user may set the binding by hand, which never creates a version
(BP-BR-14).
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from factory_blueprint.dto.issue import Issue, make_issue, sort_issues
from factory_blueprint.dto.registry import BindingInfo
from factory_blueprint.enums import BindingResolution, Disposition, IssueLevel, Section
from factory_blueprint.models.binding import EnvironmentBindingRow
from factory_blueprint.models.registry import RegistryEntryRow
from factory_blueprint.registry.conflicts import UID_BEARING
from factory_blueprint.registry.pool import UID_PATTERN
from factory_blueprint.s4.contract import DiscoveryResult

RULE_UNRESOLVED = "BPV-BIND-001"
RULE_AMBIGUOUS = "BPV-BIND-002"
RULE_UPDATE_UID_MISMATCH = "BPV-BIND-003"
RULE_MANUAL_UID_ABSENT = "BPV-BIND-004"
RULE_MANUAL_TYPE_MISMATCH = "BPV-BIND-005"

MATCH_CODE_THEN_UID = "CODE_THEN_UID"
MATCH_CODE = "CODE"
MATCH_UID = "UID"


@dataclass
class BindingOutcome:
    bindings: list[BindingInfo] = field(default_factory=list)
    issues: list[Issue] = field(default_factory=list)

    @property
    def blocking(self) -> bool:
        return any(issue.level is IssueLevel.BLOCKING for issue in self.issues)

    def uid_map(self) -> dict[str, str]:
        return {binding.business_key: binding.uid for binding in self.bindings}


def match_rule_for(dhis2_type: str, policy: dict[str, Any]) -> str:
    """The project's binding match rule, with a per-type override (BP-SCH-003)."""
    rules = policy.get("environmentBindingRules") or {}
    for override in rules.get("byType") or []:
        if isinstance(override, dict) and override.get("dhis2Type") == dhis2_type:
            return str(override.get("match") or MATCH_CODE_THEN_UID)
    return str(rules.get("defaultMatch") or MATCH_CODE_THEN_UID)


def resolve_bindings(
    entries: list[dict[str, Any]],
    discovery: DiscoveryResult,
    *,
    environment_key: str,
    design_environment: str,
    policy: dict[str, Any],
) -> BindingOutcome:
    """Resolve every entry's UID and observation baseline in one environment."""
    outcome = BindingOutcome()
    for entry in entries:
        business_key = str(entry.get("businessKey") or "")
        dhis2_type = str(entry.get("dhis2Type") or "")
        disposition = str(entry.get("disposition") or "")
        if not business_key or disposition == Disposition.EXTERNAL.value:
            continue

        if disposition == Disposition.CREATE.value:
            # a created object keeps the same UID in every environment (D-02)
            uid = entry.get("uid")
            if not isinstance(uid, str) or not uid:
                continue
            outcome.bindings.append(
                BindingInfo(
                    environment_key=environment_key,
                    business_key=business_key,
                    uid=uid,
                    resolution=BindingResolution.AUTO,
                )
            )
            continue

        rule = (
            MATCH_UID
            if disposition == Disposition.UPDATE.value
            else match_rule_for(dhis2_type, policy)
        )
        match, ambiguous = _match(entry, discovery, dhis2_type, rule)
        if match is None:
            if ambiguous:
                outcome.issues.append(
                    _binding_issue(
                        RULE_AMBIGUOUS,
                        f"{dhis2_type} code {entry.get('code')!r} matches several objects "
                        f"in {environment_key}",
                        entry,
                    )
                )
            elif disposition == Disposition.UPDATE.value:
                outcome.issues.append(
                    _binding_issue(
                        RULE_UPDATE_UID_MISMATCH,
                        f"the {dhis2_type} this UPDATE addresses (UID {entry.get('uid')!r}) "
                        f"is absent from {environment_key}; an UPDATE is matched by UID "
                        "alone (BP-REG-010.2)",
                        entry,
                        remediation="confirm the object still exists here, or set the "
                        "environment binding manually",
                    )
                )
            else:
                outcome.issues.append(
                    _binding_issue(
                        RULE_UNRESOLVED,
                        f"no {dhis2_type} in environment {environment_key} matches "
                        f"{_match_explanation(entry, rule)}",
                        entry,
                        remediation="set the environment binding manually, or fix the code",
                    )
                )
            continue
        outcome.bindings.append(
            BindingInfo(
                environment_key=environment_key,
                business_key=business_key,
                uid=match.uid,
                resolution=BindingResolution.AUTO,
                observed_last_updated=match.last_updated,
                observed_hash=match.content_hash,
            )
        )
    outcome.bindings.sort(key=lambda item: item.business_key)
    outcome.issues = sort_issues(outcome.issues)
    return outcome


def _match_explanation(entry: dict[str, Any], rule: str) -> str:
    if rule == MATCH_UID:
        return f"UID {entry.get('uid')!r}"
    if rule == MATCH_CODE:
        return f"code {entry.get('code')!r}"
    return f"code {entry.get('code')!r} or the design environment UID {entry.get('uid')!r}"


def _match(
    entry: dict[str, Any], discovery: DiscoveryResult, dhis2_type: str, rule: str
) -> tuple[Any, bool]:
    """Return (object, ambiguous) for one entry under the environment's rule."""
    code = entry.get("code")
    uid = entry.get("uid")
    if rule in (MATCH_CODE, MATCH_CODE_THEN_UID) and isinstance(code, str) and code:
        candidates = discovery.find_by_code(dhis2_type, code)
        if len(candidates) > 1:
            return None, True
        if len(candidates) == 1:
            return candidates[0], False
        if rule == MATCH_CODE:
            return None, False
    if isinstance(uid, str) and uid:
        found = discovery.find_by_uid(dhis2_type, uid)
        if found is not None:
            return found, False
        if rule == MATCH_UID:
            return None, False
    return None, False


def _binding_issue(
    rule_id: str,
    message: str,
    entry: dict[str, Any],
    remediation: str | None = None,
) -> Issue:
    uid = entry.get("uid")
    return make_issue(
        rule_id,
        IssueLevel.BLOCKING,
        message,
        business_key=str(entry.get("businessKey") or ""),
        uid=uid if isinstance(uid, str) else None,
        dhis2_type=str(entry.get("dhis2Type") or ""),
        section=Section.REGISTRY,
        remediation=remediation,
    )


def save_bindings(
    session: Session,
    project_id: str,
    blueprint_version: str,
    environment_key: str,
    bindings: list[BindingInfo],
    *,
    discovery_result_id: str | None = None,
) -> int:
    """Upsert the resolved bindings for one environment (short or caller txn)."""
    if not bindings:
        return 0
    statement = pg_insert(EnvironmentBindingRow).values(
        [
            {
                "project_id": project_id,
                "blueprint_version": blueprint_version,
                "environment_key": environment_key,
                "business_key": binding.business_key,
                "uid": binding.uid,
                "observed_last_updated": _timestamp(binding.observed_last_updated),
                "observed_hash": binding.observed_hash,
                "discovery_result_id": binding.discovery_result_id or discovery_result_id,
                "resolution": binding.resolution.value,
                "resolved_by": binding.resolved_by,
                "note": binding.note,
                "pending_reparse": False,
            }
            for binding in bindings
        ]
    )
    statement = statement.on_conflict_do_update(
        index_elements=[
            EnvironmentBindingRow.project_id,
            EnvironmentBindingRow.blueprint_version,
            EnvironmentBindingRow.environment_key,
            EnvironmentBindingRow.business_key,
        ],
        set_={
            "uid": statement.excluded.uid,
            "observed_last_updated": statement.excluded.observed_last_updated,
            "observed_hash": statement.excluded.observed_hash,
            "discovery_result_id": statement.excluded.discovery_result_id,
            "resolution": statement.excluded.resolution,
            "resolved_by": statement.excluded.resolved_by,
            "note": statement.excluded.note,
            "pending_reparse": False,
        },
    )
    session.execute(statement)
    session.flush()
    return len(bindings)


def _timestamp(text_value: str | None) -> Any:
    if not text_value:
        return None
    try:
        return datetime.fromisoformat(text_value.replace("Z", "+00:00"))
    except ValueError:
        return None


def load(
    session: Session,
    project_id: str,
    blueprint_version: str,
    environment_keys: Sequence[str] | str | None = None,
) -> list[EnvironmentBindingRow]:
    """Every binding of one version, or only those of the named environments (BP-REG-015).

    A bare ``environment_key`` is accepted too, because a string is a sequence of
    characters and ``"DEV"`` would otherwise quietly ask for the environments ``D``,
    ``E`` and ``V``.
    """
    if isinstance(environment_keys, str):
        environment_keys = [environment_keys]
    statement = select(EnvironmentBindingRow).where(
        EnvironmentBindingRow.project_id == project_id,
        EnvironmentBindingRow.blueprint_version == blueprint_version,
    )
    if environment_keys:
        statement = statement.where(
            EnvironmentBindingRow.environment_key.in_(sorted(set(environment_keys)))
        )
    return list(
        session.execute(
            statement.order_by(
                EnvironmentBindingRow.environment_key, EnvironmentBindingRow.business_key
            )
        ).scalars()
    )


def uid_map(
    session: Session, project_id: str, blueprint_version: str, environment_key: str
) -> dict[str, str]:
    return {
        row.business_key: row.uid
        for row in load(session, project_id, blueprint_version, [environment_key])
    }


def observed(
    session: Session, project_id: str, blueprint_version: str, environment_key: str
) -> dict[str, EnvironmentBindingRow]:
    return {
        row.business_key: row
        for row in load(session, project_id, blueprint_version, [environment_key])
    }


def set_manual(
    session: Session,
    *,
    project_id: str,
    blueprint_version: str,
    environment_key: str,
    business_key: str,
    uid: str,
    note: str,
    resolved_by: str,
    discovery_result_id: str | None = None,
    observed_hash: str | None = None,
    observed_last_updated: Any = None,
) -> EnvironmentBindingRow:
    """A human sets the binding; recorded, audited, and it makes no new version.

    BP-REG-010.3 puts this choice in a person's hands and BP-BR-14 keeps the version
    still, so what S2 owes in return is refusing a choice that cannot mean anything: an
    entry this version does not define, a string that is not a DHIS2 UID, a CREATE object
    (whose UID S2 allocated and which must stay identical in every environment, D-02 and
    AC-ENV-01), or another project's object (D-03).

    Whether the UID is actually *in* that environment is not answerable here: only the
    Discovery Result knows, and reading an artifact from inside this transaction is not
    allowed.  The next environment compile checks it and says so as a BLOCKING issue
    (``confirm_manual``).
    """
    from factory_blueprint.audit_log import record
    from factory_blueprint.errors import RegistryError

    if not note:
        raise RegistryError("a manual binding must carry an explanation")
    entry = _registry_entry(session, project_id, blueprint_version, business_key)
    _require_bindable(session, project_id, uid, entry)
    existing = session.execute(
        select(EnvironmentBindingRow).where(
            EnvironmentBindingRow.project_id == project_id,
            EnvironmentBindingRow.blueprint_version == blueprint_version,
            EnvironmentBindingRow.environment_key == environment_key,
            EnvironmentBindingRow.business_key == business_key,
        )
    ).scalar_one_or_none()
    if existing is None:
        existing = EnvironmentBindingRow(
            project_id=project_id,
            blueprint_version=blueprint_version,
            environment_key=environment_key,
            business_key=business_key,
            uid=uid,
            resolution=BindingResolution.MANUAL.value,
        )
        session.add(existing)
    before = {"uid": existing.uid, "resolution": existing.resolution}
    existing.uid = uid
    existing.resolution = BindingResolution.MANUAL.value
    existing.resolved_by = resolved_by
    existing.note = note
    existing.discovery_result_id = discovery_result_id
    existing.observed_hash = observed_hash
    # the human overrode what the last compile observed, so this binding is a claim until
    # a compile against a Discovery Result confirms it (BP-REG-015 "是否待重新解析")
    existing.pending_reparse = True
    if observed_last_updated is not None:
        # S1 reads a DHIS2 lastUpdated as text; the column is a timestamp
        existing.observed_last_updated = _timestamp(observed_last_updated) or observed_last_updated
    session.flush()
    record(
        "bp.env_binding.set",
        object_type="environmentBinding",
        object_id=f"{project_id}/{blueprint_version}/{environment_key}/{business_key}",
        project_id=project_id,
        before=before,
        after={"uid": uid, "resolution": BindingResolution.MANUAL.value, "note": note},
        session=session,
    )
    return existing


def _registry_entry(
    session: Session, project_id: str, blueprint_version: str, business_key: str
) -> RegistryEntryRow:
    """The Registry entry a binding is being set for, or a refusal (BP-REG-010.3)."""
    from factory_blueprint.errors import RegistryError

    row = session.execute(
        select(RegistryEntryRow).where(
            RegistryEntryRow.project_id == project_id,
            RegistryEntryRow.blueprint_version == blueprint_version,
            RegistryEntryRow.business_key == business_key,
        )
    ).scalar_one_or_none()
    if row is None:
        raise RegistryError(
            f"version {blueprint_version} of project {project_id} has no Registry entry "
            f"{business_key!r}; a binding can only say which object an entry is",
            details={"businessKey": business_key, "blueprintVersion": blueprint_version},
        )
    return row


def _require_bindable(session: Session, project_id: str, uid: str, entry: RegistryEntryRow) -> None:
    """Refuse the manual bindings that would silently corrupt an environment."""
    from factory_blueprint.errors import RegistryError

    if not UID_PATTERN.match(uid):
        raise RegistryError(
            f"{uid!r} is not a DHIS2 UID: eleven characters, a letter first",
            details={"uid": uid, "businessKey": entry.business_key},
        )
    if entry.disposition == Disposition.CREATE.value:
        raise RegistryError(
            f"{entry.business_key} is created by this project, so its UID is the one S2 "
            "allocated and it is the same object in every environment (D-02, AC-ENV-01); "
            "binding it differently would split one object into two",
            details={"businessKey": entry.business_key, "disposition": entry.disposition},
        )
    rival = session.execute(
        select(RegistryEntryRow).where(
            RegistryEntryRow.uid == uid,
            RegistryEntryRow.project_id != project_id,
            RegistryEntryRow.disposition.in_(UID_BEARING),
        )
    ).scalars().first()
    if rival is not None:
        raise RegistryError(
            f"UID {uid} is managed by project {rival.project_id} as {rival.disposition}; "
            "one object is owned by one project per environment (D-03)",
            details={
                "uid": uid,
                "otherProjectId": rival.project_id,
                "otherBusinessKey": rival.business_key,
            },
        )


@dataclass
class ManualConfirmation:
    """What this environment's Discovery Result says about the human-set bindings."""

    bindings: list[BindingInfo] = field(default_factory=list)
    issues: list[Issue] = field(default_factory=list)

    @property
    def blocking(self) -> bool:
        return any(issue.level is IssueLevel.BLOCKING for issue in self.issues)


def confirm_manual(
    rows: Sequence[EnvironmentBindingRow],
    entries: Sequence[dict[str, Any]],
    discovery: DiscoveryResult,
    *,
    environment_key: str,
    discovery_result_id: str | None = None,
) -> ManualConfirmation:
    """Read a human's choice back against the environment, and give it a baseline.

    A person may well know an object whose code the matching rules miss — that is the
    whole point of BP-REG-010.3 — but S2 must not then write the guess into a payload as
    though it had been observed.  So this confirms, or reports: the object has to be
    there, of the type the entry says, and its content hash and ``lastUpdated`` become the
    observation baseline the manifest hands to S4 (BP-CMP-027a, BP-AC-13).
    """
    by_key = {
        str(entry.get("businessKey")): entry for entry in entries if entry.get("businessKey")
    }
    outcome = ManualConfirmation()
    for row in sorted(rows, key=lambda item: item.business_key):
        entry = by_key.get(row.business_key)
        if entry is None:
            # the version no longer defines this entry: nothing to confirm, and the
            # binding is a fact about history rather than about this compile
            continue
        dhis2_type = str(entry.get("dhis2Type") or "")
        found = discovery.find_by_uid(dhis2_type, row.uid)
        if found is not None:
            outcome.bindings.append(
                BindingInfo(
                    environment_key=environment_key,
                    business_key=row.business_key,
                    uid=row.uid,
                    resolution=BindingResolution.MANUAL,
                    observed_last_updated=found.last_updated,
                    observed_hash=found.content_hash,
                    discovery_result_id=discovery_result_id,
                    resolved_by=row.resolved_by,
                    note=row.note,
                )
            )
            continue
        wrong_type = next((item for item in discovery.objects if item.uid == row.uid), None)
        rule = RULE_MANUAL_TYPE_MISMATCH if wrong_type is not None else RULE_MANUAL_UID_ABSENT
        detail = (
            f"UID {row.uid} there is a {wrong_type.dhis2_type}, not the {dhis2_type} this "
            "entry needs"
            if wrong_type is not None
            else f"environment {environment_key} holds no object with UID {row.uid}"
        )
        outcome.issues.append(
            make_issue(
                rule,
                IssueLevel.BLOCKING,
                f"{dhis2_type} {row.business_key}: the binding a human set cannot be "
                f"confirmed — {detail}",
                business_key=row.business_key,
                uid=row.uid,
                dhis2_type=dhis2_type,
                section=Section.REGISTRY,
                remediation="set the environment binding again against this environment's "
                "discovery, or let the compile resolve it automatically",
            )
        )
    outcome.issues = sort_issues(outcome.issues)
    return outcome


def mark_pending_reparse(
    session: Session, project_id: str, blueprint_version: str | None = None
) -> int:
    """After an import the environments differ, so every binding needs rechecking."""
    statement = select(EnvironmentBindingRow).where(
        EnvironmentBindingRow.project_id == project_id
    )
    if blueprint_version is not None:
        statement = statement.where(EnvironmentBindingRow.blueprint_version == blueprint_version)
    rows = list(session.execute(statement).scalars())
    for row in rows:
        row.pending_reparse = True
    session.flush()
    return len(rows)


def delete_project_bindings(session: Session, project_id: str) -> int:
    rows = list(
        session.execute(
            select(EnvironmentBindingRow).where(EnvironmentBindingRow.project_id == project_id)
        ).scalars()
    )
    for row in rows:
        session.delete(row)
    session.flush()
    return len(rows)
