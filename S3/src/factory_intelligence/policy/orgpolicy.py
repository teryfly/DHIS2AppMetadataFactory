"""Organisation policy: reading, combining and relaxing (AI-POL-002, AI-POL-004).

Three rules live here, and each is a decision the SRS made rather than an
implementation detail:

* the system's starting point denies external providers (AI-POL-002), so with no row in
  ``ai.org_policy`` the answer is :data:`~factory_intelligence.enums.SYSTEM_DEFAULT_MATRIX`,
  never an empty mapping; SRS 20.1's :data:`~factory_intelligence.enums.POLICY_MATRIX` is the
  *ceiling* a relaxation may widen towards, and the two are deliberately different constants —
  reading them as one would make every legal relaxation a no-op;
* a relaxation is always recorded with its reason and its operator (AI-POL-002, D-16:
  while the system has no authentication the *name* is the identity, and M4 turns that
  into a platform-administrator check — see :attr:`role_checked`);
* ``PHI_PII`` may not be relaxed at all (AI-POL-005, AC-POL-02).  The engine cannot widen
  it either, since an empty set is the identity of intersection, but a request to try is
  refused here, at the point where someone might believe it succeeded.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from factory_foundation.api import (
    DataClassification,
    ValidationError,
    current_session,
    has_active_uow,
    new_id,
    unit_of_work,
)
from sqlalchemy.orm import Session

from factory_intelligence import audit_log
from factory_intelligence.enums import ProviderClass, as_provider_class
from factory_intelligence.errors import PolicyRelaxationDenied
from factory_intelligence.models.policy import OrgPolicyRow, PolicyRelaxationRow
from factory_intelligence.policy.model import _LEVELS, EffectivePolicy
from factory_intelligence.policy.normalize import org_policy_from_payload

_ORG_ROW_ID = "org"


def read_org_policy(*, session: Session | None = None) -> EffectivePolicy:
    """The stored organisation policy, or AI-POL-002's default when nobody has written one."""
    def _read(sess: Session) -> EffectivePolicy:
        row = sess.get(OrgPolicyRow, _ORG_ROW_ID)
        if row is None:
            return EffectivePolicy.organisation_default()
        return org_policy_from_payload({"allowedClassesByLevel": row.allowed_classes_by_level})

    if session is not None or has_active_uow():
        return _read(session if session is not None else current_session())
    with unit_of_work() as sess:
        return _read(sess)


def effective_policy(
    *, session: Session | None = None, environment_policies: Mapping[str, Any] | None = None
) -> EffectivePolicy:
    """Combine the organisation policy with every target environment (AI-POL-004, D-07).

    The project id is not a parameter: S3 holds no project table (D-14), so the caller —
    S1, which does — passes the environments it knows belong to the project, and this
    function's job is exactly and only the combination.
    """
    from factory_intelligence.policy.normalize import normalize_environment_policies

    parts = [read_org_policy(session=session)]
    parts.extend(normalize_environment_policies(environment_policies))
    return EffectivePolicy.combine(parts)


def relax(
    *,
    change: Mapping[str, Any],
    reason: str,
    operator: str,
    scope: str = "organization",
    session: Session | None = None,
) -> PolicyRelaxationRow:
    """Widen a level's allowed provider classes, recording who and why.

    ``change`` is ``{"level": "CONFIDENTIAL", "addClasses": ["PRIVATE_NETWORK"]}`` or the
    equivalent ``{"level": ..., "allowedClasses": [...]}`` replacement form.
    """
    if not reason or not reason.strip():
        raise ValidationError(
            "a policy relaxation requires a reason", code="VALIDATION_ERROR"
        )
    level = _level_of(change)
    classes = _classes_of(change)
    if level is DataClassification.PHI_PII:
        raise PolicyRelaxationDenied(
            "PHI_PII may never be made sendable to a provider (AI-POL-005)",
            details={"level": level.value},
        )
    if not classes:
        raise ValidationError(
            "a relaxation must name at least one provider class",
            code="VALIDATION_ERROR",
            details={"change": dict(change)},
        )
    # A relaxation may widen but never replace the matrix's own floor: PUBLIC is allowed
    # everywhere already, and asking to *narrow* by relaxation is a contradiction.
    before = read_org_policy(session=session)
    allowed = {step: before.classes_for(step) for step in _LEVELS}
    widened = allowed[level] | classes
    _refuse_widening_beyond_matrix(level, widened)
    allowed[level] = widened

    def _write(sess: Session) -> PolicyRelaxationRow:
        row = sess.get(OrgPolicyRow, _ORG_ROW_ID)
        if row is None:
            row = OrgPolicyRow(
                id=_ORG_ROW_ID,
                allowed_classes_by_level=_payload(allowed),
                updated_by=operator,
            )
            sess.add(row)
        else:
            row.allowed_classes_by_level = _payload(allowed)
            row.updated_by = operator
        record = PolicyRelaxationRow(
            id=new_id("aplax"),
            scope=scope,
            change=dict(change),
            reason=reason.strip(),
            operator=operator,
            # M4 introduces the role check; until then D-16 accepts the name alone, and
            # this field keeps the difference visible rather than implied.
            role_checked=False,
        )
        sess.add(record)
        audit_log.record(
            "ai.policy.relaxed",
            session=sess,
            object_type="OrgPolicy",
            object_id=_ORG_ROW_ID,
            project_id=None,
            after={
                "scope": scope,
                "level": level.value,
                "classes": sorted(member.value for member in widened),
                "reason": reason.strip(),
                "roleChecked": False,
            },
            operator=operator,
            operator_is_user=True,
        )
        sess.flush()
        return record

    if session is not None or has_active_uow():
        return _write(session if session is not None else current_session())
    with unit_of_work() as sess:
        return _write(sess)


def _refuse_widening_beyond_matrix(
    level: DataClassification, widened: frozenset[ProviderClass]
) -> None:
    """AI-POL-001 is a ceiling, not a starting suggestion.

    ``CONFIDENTIAL`` reaching ``APPROVED_EXTERNAL`` is not a relaxation the SRS allows an
    organisation to make; only ``INTERNAL`` and below may ever include an external class,
    and only ``PUBLIC`` may reach ``UNRESTRICTED``.
    """
    from factory_intelligence.enums import POLICY_MATRIX

    ceiling = POLICY_MATRIX[level]
    beyond = widened - ceiling
    if beyond:
        names = ", ".join(sorted(member.value for member in beyond))
        raise PolicyRelaxationDenied(
            f"{level.value} may never be sent to {names} (AI-POL-001)",
            details={"level": level.value, "beyondCeiling": sorted(str(m) for m in beyond)},
        )


def _level_of(change: Mapping[str, Any]) -> DataClassification:
    raw = change.get("level") or change.get("classification")
    if not isinstance(raw, str):
        raise ValidationError(
            "a relaxation needs a 'level'", code="VALIDATION_ERROR", details={"change": dict(change)}
        )
    try:
        return DataClassification(raw)
    except ValueError as exc:
        raise ValidationError(
            f"unknown data classification {raw!r}", code="VALIDATION_ERROR"
        ) from exc


def _classes_of(change: Mapping[str, Any]) -> frozenset[ProviderClass]:
    raw = change.get("addClasses") or change.get("allowedClasses") or change.get("classes") or []
    if isinstance(raw, str):
        raw = [raw]
    if not isinstance(raw, (list, tuple, set)):
        raise ValidationError(
            "'addClasses' must be a list of provider classes", code="VALIDATION_ERROR"
        )
    out: list[ProviderClass] = []
    for name in raw:
        try:
            out.append(as_provider_class(name))
        except ValueError as exc:
            raise ValidationError(
                f"unknown provider class {name!r}", code="VALIDATION_ERROR"
            ) from exc
    return frozenset(out)


def _payload(allowed: Mapping[DataClassification, frozenset[ProviderClass]]) -> dict[str, Any]:
    return {
        level.value: sorted(member.value for member in allowed.get(level, frozenset()))
        for level in _LEVELS
    }


__all__ = ["read_org_policy", "effective_policy", "relax"]
