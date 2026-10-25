"""UID / code / ownership conflict checks against the target environments.

Freeze and environment-bound compilation both run these (BP-DRF-008.2,
BP-REG-008/009/012).  The existence questions go to S4, which is read-only; the
ownership question is answered from ``bp`` alone, because another project's
allocation is data S2 owns.

Results are issues, never artifact content: they depend on the current state of a
remote instance, so putting them into a product would break byte-level
reproduction (BP-CMP-001, AC-DATA-01).
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from factory_blueprint.content.document import BlueprintDocument
from factory_blueprint.content.layout import CONTAINER_BY_PATH
from factory_blueprint.deps import get_dhis2
from factory_blueprint.dto.issue import Issue, make_issue, sort_issues
from factory_blueprint.enums import Disposition, IssueLevel, Section
from factory_blueprint.models.registry import RegistryEntryRow
from factory_blueprint.models.uid import UidAllocationRow
from factory_blueprint.registry import pool as uid_pool

RULE_UID_CONFLICT = "BPV-ENV-001"
RULE_CODE_CONFLICT = "BPV-ENV-002"
RULE_OWNERSHIP_CONFLICT = "BPV-ENV-003"
RULE_UID_ALREADY_ALLOCATED = "BPV-ENV-004"

UID_BEARING = (Disposition.CREATE.value, Disposition.UPDATE.value)


REGISTRY_CONTAINER = CONTAINER_BY_PATH["/registry/entries"]


def _registry_entries(document: dict[str, object]) -> list[dict[str, object]]:
    return BlueprintDocument(document).container_entries(REGISTRY_CONTAINER)


@dataclass(frozen=True)
class UidClaim:
    """What this project says about a UID it carries into an environment."""

    dhis2_type: str
    disposition: str
    business_key: str


@dataclass(frozen=True)
class CodeClaim:
    """What this project says about a code, and the UID it pairs with."""

    dhis2_type: str
    code: str
    uid: str | None
    disposition: str
    business_key: str


def uid_to_be_checked(entries: Sequence[dict[str, object]]) -> dict[str, str]:
    """UID -> dhis2Type for every entry that must exist or be created as itself."""
    return {uid: claim.dhis2_type for uid, claim in uid_claims(entries).items()}


def uid_claims(entries: Sequence[dict[str, object]]) -> dict[str, UidClaim]:
    """UID -> what our project claims about it.

    A CREATE claim expects the number to be free; an UPDATE claim expects it to be held
    by the object this entry describes, which is the same type under the same UID.
    """
    claims: dict[str, UidClaim] = {}
    for entry in entries:
        disposition = entry.get("disposition")
        if disposition not in UID_BEARING:
            continue
        uid = entry.get("uid")
        if isinstance(uid, str) and uid:
            claims[uid] = UidClaim(
                dhis2_type=str(entry.get("dhis2Type") or ""),
                disposition=str(disposition),
                business_key=str(entry.get("businessKey") or ""),
            )
    return claims


def code_claims(entries: Sequence[dict[str, object]]) -> dict[tuple[str, str], CodeClaim]:
    """(dhis2Type, code) -> the claim, for the entries that carry a code."""
    claims: dict[tuple[str, str], CodeClaim] = {}
    for entry in entries:
        disposition = entry.get("disposition")
        if disposition not in UID_BEARING:
            continue
        code = entry.get("code")
        if not isinstance(code, str) or not code:
            continue
        uid = entry.get("uid")
        claims[(str(entry.get("dhis2Type") or ""), code)] = CodeClaim(
            dhis2_type=str(entry.get("dhis2Type") or ""),
            code=code,
            uid=uid if isinstance(uid, str) and uid else None,
            disposition=str(disposition),
            business_key=str(entry.get("businessKey") or ""),
        )
    return claims


def code_to_be_checked(entries: Sequence[dict[str, object]]) -> dict[str, set[str]]:
    """dhis2Type -> codes, for the entries that carry a code."""
    grouped: dict[str, set[str]] = {}
    for (dhis2_type, code) in code_claims(entries):
        grouped.setdefault(dhis2_type, set()).add(code)
    return grouped


def check_uid_conflicts(
    document: dict[str, object], environment_keys: Sequence[str]
) -> list[Issue]:
    """BP-REG-008: no *other* object may hold one of our UIDs.

    An UPDATE entry's UID is held by its own target object by definition, so an
    occupant of the declared type is the expected answer rather than a conflict; a
    CREATE entry expects the number to be free.
    """
    issues: list[Issue] = []
    entries = _registry_entries(document)
    claims = uid_claims(entries)
    for environment_key in environment_keys:
        present = get_dhis2().uid_exists(environment_key, sorted(claims))
        for uid in sorted(present):
            occupancy = present[uid]
            if not occupancy.exists:
                continue
            claim = claims[uid]
            if claim.disposition == Disposition.UPDATE.value and occupancy.dhis2_type == claim.dhis2_type:
                continue
            if claim.disposition == Disposition.UPDATE.value:
                message = (
                    f"UID {uid} is held by a {occupancy.dhis2_type or 'unknown type'} in "
                    f"environment {environment_key}, not by the {claim.dhis2_type} this "
                    "UPDATE entry targets"
                )
                remediation = "confirm the object's identity, or set the environment binding by hand"
            else:
                message = f"UID {uid} already exists in environment {environment_key}"
                remediation = (
                    "check whether another project created this object, or re-plan the "
                    "entry as UPDATE/REFERENCE"
                )
            issues.append(
                make_issue(
                    RULE_UID_CONFLICT,
                    IssueLevel.BLOCKING,
                    message,
                    business_key=claim.business_key,
                    uid=uid,
                    dhis2_type=claim.dhis2_type,
                    section=Section.REGISTRY,
                    details={"holderType": occupancy.dhis2_type, "holderCode": occupancy.code},
                    remediation=remediation,
                )
            )
    return issues


def check_code_conflicts(
    document: dict[str, object], environment_keys: Sequence[str]
) -> list[Issue]:
    """BP-REG-009: the code must be free among objects that are not our own."""
    issues: list[Issue] = []
    claims = code_claims(_registry_entries(document))
    grouped: dict[str, list[str]] = {}
    for dhis2_type, code in claims:
        grouped.setdefault(dhis2_type, []).append(code)
    for environment_key in environment_keys:
        for dhis2_type in sorted(grouped):
            codes = sorted(set(grouped[dhis2_type]))
            existing = get_dhis2().code_exists(environment_key, dhis2_type, codes)
            for code in sorted(existing):
                occupancy = existing[code]
                if not occupancy.exists:
                    continue
                claim = claims[(dhis2_type, code)]
                # the object our UPDATE entry updates legitimately carries our code
                if (
                    claim.disposition == Disposition.UPDATE.value
                    and claim.uid is not None
                    and occupancy.uid == claim.uid
                ):
                    continue
                issues.append(
                    make_issue(
                        RULE_CODE_CONFLICT,
                        IssueLevel.BLOCKING,
                        f"code {code} is already used by a {dhis2_type} in "
                        f"environment {environment_key}",
                        business_key=claim.business_key,
                        uid=claim.uid,
                        dhis2_type=dhis2_type,
                        section=Section.REGISTRY,
                        details={"holderUid": occupancy.uid},
                        remediation="change generationPolicy.codePrefix or the naming convention",
                    )
                )
    return issues


def check_ownership_conflicts(
    session: Session,
    project_id: str,
    document: dict[str, object],
    environment_keys: Sequence[str],
) -> list[Issue]:
    """BP-REG-012 / D-03: one UID may be managed by only one project per environment."""
    issues: list[Issue] = []
    entries = {
        str(entry.get("uid")): entry for entry in _registry_entries(document)
        if isinstance(entry.get("uid"), str) and entry.get("disposition") in UID_BEARING
    }
    if not entries:
        return issues
    rows = session.execute(
        select(RegistryEntryRow).where(
            RegistryEntryRow.uid.in_(sorted(entries)),
            RegistryEntryRow.project_id != project_id,
            RegistryEntryRow.disposition.in_(UID_BEARING),
        )
    ).scalars()
    for row in rows:
        entry = entries.get(row.uid or "", {})
        for environment_key in environment_keys:
            issues.append(
                make_issue(
                    RULE_OWNERSHIP_CONFLICT,
                    IssueLevel.BLOCKING,
                    f"project {row.project_id} also manages UID {row.uid} as "
                    f"{row.disposition} in environment {environment_key}",
                    business_key=str(entry.get("businessKey") or row.business_key),
                    uid=row.uid,
                    dhis2_type=row.dhis2_type,
                    section=Section.REGISTRY,
                    details={"otherProjectId": row.project_id, "otherDisposition": row.disposition},
                    remediation="hand the object over through a change request, or reference it",
                )
            )
    return issues


def check_no_reattributed_uids(session: Session, project_id: str, uids: Sequence[str]) -> list[Issue]:
    """BP-REG-005: the same UID may never serve two business concepts."""
    issues: list[Issue] = []
    if not uids:
        return issues
    rows = session.execute(
        select(UidAllocationRow).where(
            UidAllocationRow.uid.in_(list(uids)),
            UidAllocationRow.project_id != project_id,
        )
    ).scalars()
    for row in rows:
        issues.append(
            make_issue(
                RULE_UID_ALREADY_ALLOCATED,
                IssueLevel.BLOCKING,
                f"UID {row.uid} belongs to {row.project_id} / {row.business_key}",
                uid=row.uid,
                business_key=row.business_key,
                section=Section.REGISTRY,
            )
        )
    return issues


def check_environment_facing(
    document: dict[str, object], environment_keys: Sequence[str]
) -> list[Issue]:
    """The two checks that ask the target instance — so no unit of work may be open.

    ``uid_exists`` and ``code_exists`` are S4 calls marked ``@external_io``, and S0 refuses
    them the moment a session is current (00 doc 6.2).  They need nothing from the database:
    the claims come from the document alone.  Both callers that want ``check_all`` therefore
    have to run this half *before* opening their session, which is why it is separate
    (BP-REG-008, BP-REG-013).
    """
    return sort_issues(
        [*check_uid_conflicts(document, environment_keys), *check_code_conflicts(document, environment_keys)]
    )


def check_database_facing(
    session: Session, project_id: str, document: dict[str, object], environment_keys: Sequence[str]
) -> list[Issue]:
    """The two checks that read this Factory's own claims (BP-AC-08, BP-REG-012)."""
    return sort_issues(
        [
            *check_ownership_conflicts(session, project_id, document, environment_keys),
            *check_no_reattributed_uids(
                session, project_id, sorted(uid_to_be_checked(_registry_entries(document)))
            ),
        ]
    )


def check_all(
    session: Session,
    project_id: str,
    document: dict[str, object],
    environment_keys: Sequence[str],
) -> list[Issue]:
    """Every environment-facing check, ordered deterministically.

    Callers that hold a unit of work must not use this: it asks S4 (see
    :func:`check_environment_facing`), and only ``check_environment_facing`` +
    ``check_database_facing`` are safe to compose around the session boundary.
    """
    return sort_issues(
        [*check_environment_facing(document, environment_keys), *check_database_facing(session, project_id, document, environment_keys)]
    )


def pool_is_healthy(session: Session, project_id: str, low_water_mark: int) -> bool:
    return uid_pool.available_count(session, project_id) > low_water_mark
