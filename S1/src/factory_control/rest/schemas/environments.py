"""Environment and capability contracts (S5IF-ENV, SRS 4 of the gap document).

The *shape* of an Environment belongs to S4 — this module mirrors only what S1 can
forward, and deliberately carries no credential value, mask, length or prefix:
S5IF-G09 / FR-SEC-007 make a reference the only representable form.
"""

from __future__ import annotations

import re
from datetime import datetime
from typing import Any

from pydantic import ConfigDict, Field, model_validator
from pydantic.alias_generators import to_camel

from factory_control.errors import ValidationFailedError
from factory_control.rest.schemas.common import CamelModel, IssueView, ListEnvelope

#: Indirection schemes an environment or provider row may name (S5IF-G09).
#:
#: ``db://`` is the control plane's own encrypted store (CP-SEC-001) and is what the console
#: produces when an operator types a credential in; ``env://`` is the deployment's environment
#: file.  Those two are the schemes with a real backend behind them today.
#: ``vault://``/``secret://``/``kms://`` stay accepted so a deployment that installs such a
#: transport keeps working — but the console is told they are unimplemented here (UI-ADM-002's
#: amendment), because offering an address that cannot resolve is how a "saved" environment ends
#: up failing its first DHIS2 call.
#:
#: What has *not* changed: no row of an Environment Profile or a provider registration may carry a
#: value.  A value arrives only at ``PUT /api/v1/secrets/{name}`` (S5IF-SEC01), which encrypts it
#: and returns a reference.
REFERENCE_SCHEMES = ("db://", "env://", "vault://", "secret://", "kms://")

ENVIRONMENT_CLASSES = ("DEV", "TEST", "UAT", "PRODUCTION")
AUTH_MODES = ("PAT", "BASIC")
LLM_DATA_POLICIES = ("LOCAL_ONLY", "PRIVATE_NETWORK", "APPROVED_EXTERNAL", "UNRESTRICTED")
CREDENTIAL_SCOPES = ("discovery", "import", "tracker", "datastore", "apps", "maintenance")
#: S4's ``REQUIRED_SCOPES``: discovery reads, import writes; neither is optional in any class.
REQUIRED_CREDENTIAL_SCOPES = ("discovery", "import")
#: S4 defines exactly these two; ``FORBID`` is the PRODUCTION default when none is given.
DELETION_POLICIES = ("FORBID", "SAFE_ONLY")

#: S4's own clock spelling, "HH:MM"; a window written any other way is not a window.
_CLOCK = re.compile(r"^([01]?\d|2[0-3]):[0-5]\d$")


def require_reference(value: str, field: str) -> str:
    """Reject anything that is not an indirection (S5IF-G09, UI-ADM-002)."""
    text = (value or "").strip()
    if not any(text.startswith(scheme) for scheme in REFERENCE_SCHEMES):
        raise ValidationFailedError(
            f"{field} must be a secret reference, not a value",
            details={"field": field, "acceptedSchemes": list(REFERENCE_SCHEMES)},
        )
    return text


class CredentialView(CamelModel):
    scope: str
    secret_ref: str
    expires_at: datetime | None = None
    label: str | None = None


class EnvironmentView(CamelModel):
    environment_key: str
    name: str | None = None
    base_url: str | None = None
    environment_class: str = "TEST"
    version_constraint: str | None = None
    dhis2_version: str | None = None
    auth_mode: str = "PAT"
    tls_required: bool = False
    llm_data_policy: str = "PRIVATE_NETWORK"
    timeout_seconds: int = 60
    status: str = "ACTIVE"
    notes: str | None = None
    secret_refs: dict[str, str] = Field(default_factory=dict)
    credentials: list[CredentialView] = Field(default_factory=list)
    created_at: datetime | None = None
    updated_at: datetime | None = None


class EnvironmentPage(ListEnvelope):
    items: list[EnvironmentView] = Field(default_factory=list)


class CommitWindowView(CamelModel):
    """``{"days":[2,4],"start":"20:00","end":"23:00"}``, ISO weekday (1 = Monday)."""

    days: list[int] = Field(default_factory=list)
    start: str | None = None
    end: str | None = None


class EnvironmentProtectionView(CamelModel):
    environment_key: str
    environment_class: str = "TEST"
    timezone: str = "UTC"
    commit_windows: list[CommitWindowView] = Field(default_factory=list)
    within_commit_window: bool = True
    emergency_locked: bool = False
    lock_reason: str | None = None
    lock_changed_at: datetime | None = None
    lock_changed_by: str | None = None
    deletion_policy: str | None = None
    required_approvals: int | None = None
    operator_role: str | None = None
    allows_fixture_import: bool | None = None
    data_export_policy: dict[str, Any] = Field(default_factory=dict)
    llm_data_policy: str | None = None


class EmergencyLockView(CamelModel):
    environment_key: str
    emergency_locked: bool
    lock_reason: str | None = None
    lock_changed_at: datetime | None = None
    lock_changed_by: str | None = None


class DisableResultView(CamelModel):
    environment_key: str
    status: str = "DISABLED"


class ConnectionCheckItemView(CamelModel):
    """One of the eight connection checks, as DX-CON-001 numbers them (S5IF-ENV08).

    ``state`` is ``PASSED``/``FAILED``/``SKIPPED`` — S4's spelling — and ``detail`` is the
    sentence the operator reads next to it, which may be a DHIS2 status line.
    """

    name: str | None = None
    state: str | None = None
    detail: str | None = None


class ConnectionCheckView(CamelModel):
    """The eight DX-CON-001 checks, individually displayable (UI-ADM-001)."""

    environment_key: str | None = None
    ok: bool = False
    result: str | None = None
    dhis2_version: str | None = None
    tested_at: datetime | None = None
    required_scopes: list[str] = Field(default_factory=list)
    checks: list[ConnectionCheckItemView] = Field(default_factory=list)
    missing_authorities: dict[str, list[str]] = Field(default_factory=dict)
    capability_profile_artifact_id: str | None = None
    message: str | None = None


class CapabilityProfileView(CamelModel):
    environment_key: str
    profile_version: str | None = None
    dhis2_version: str | None = None
    probed_at: datetime | None = None
    stale: bool = False
    stale_reason: str | None = None
    freshness: str = "VALID"
    artifact_id: str | None = None
    content_hash: str | None = None
    content: dict[str, Any] | None = None


class ProbeResultView(CamelModel):
    capability_profile_artifact_id: str | None = None
    version: str | None = None
    profile_version: str | None = None


class ObjectDeletionView(CamelModel):
    """One deletion execution as S4 reported it (DX-EXE-038, S4 SRS 7.4).

    The body is S4's ``ExecutionOutcome``, not an S1 judgement: the deletion's verdict is "what
    the instance did", and CP-API-007 forbids S1 from keeping a second copy of it.  ``status``
    therefore carries S4's ``ExecutionStatus`` — ``COMPLETED`` / ``FAILED`` /
    ``NEEDS_MANUAL_CHECK`` — and a refusal that never submitted anything is a ``FAILED`` row with
    ``applied=false`` plus the reason in ``detail``, not an HTTP error.  ``executionId`` is what
    makes the answer followable: ``GET /executions/{executionId}`` and the artifact ids in this
    same body carry the evidence afterwards.

    ``environmentKey`` and ``deletionArtifactId`` are echoed from the request because DX-BR-02
    admits no default environment and a cleanup that deleted from the wrong one must be readable
    from its own answer.
    """

    environment_key: str
    deletion_artifact_id: str
    execution_id: str | None = None
    status: str = "COMPLETED"
    applied: bool | None = None
    payload_hash: str | None = None
    report_artifact_id: str | None = None
    import_report_artifact_id: str | None = None
    steps: list[dict[str, Any]] = Field(default_factory=list)
    issues: list[IssueView] = Field(default_factory=list)
    detail: str | None = None


class DiscoveryResultView(CamelModel):
    artifact_id: str
    project_id: str | None = None
    scope: str | None = None
    object_count: int = 0
    blocking_issues: list[dict[str, Any]] = Field(default_factory=list)
    version: str | None = None
    discovered_at: datetime | None = None
    content_hash: str | None = None


class DiscoveryResultPage(ListEnvelope):
    items: list[DiscoveryResultView] = Field(default_factory=list)


class ExpiringCredentialView(CamelModel):
    environment_key: str
    scope: str | None = None
    secret_ref: str
    expires_at: datetime | None = None
    days_remaining: int | None = None


class ExpiringCredentialList(CamelModel):
    items: list[ExpiringCredentialView] = Field(default_factory=list)


# --- Requests (S5IF-G02) ----------------------------------------------------


class CredentialSpec(CamelModel):
    scope: str = Field(json_schema_extra={"enum": list(CREDENTIAL_SCOPES)})
    secret_ref: str
    label: str | None = None

    @model_validator(mode="after")
    def _check_reference(self) -> CredentialSpec:
        require_reference(self.secret_ref, "credentials[].secretRef")
        return self


class CommitWindowSpec(CamelModel):
    """One allowed commit window (DX-ENV-002): ISO weekdays plus a clock range.

    S4 owns the vocabulary; this mirror exists so an operator is told which window is
    wrong instead of meeting a pydantic error from the far end of the call.
    """

    model_config = ConfigDict(
        alias_generator=to_camel, populate_by_name=True, extra="forbid", str_strip_whitespace=True
    )

    days: list[int] = Field(default_factory=list, min_length=1)
    start: str = "00:00"
    end: str = "23:59"
    note: str | None = None

    @model_validator(mode="after")
    def _check_window(self) -> CommitWindowSpec:
        if any(not 1 <= day <= 7 for day in self.days):
            raise ValidationFailedError(
                "commit window weekdays are ISO 1..7 (1 = Monday)",
                details={"days": self.days, "rule": "DX-ENV-002"},
            )
        for field, value in (("start", self.start), ("end", self.end)):
            if not _CLOCK.fullmatch(value):
                raise ValidationFailedError(
                    f"protection.commitWindows[].{field} looks like '20:00'",
                    details={f"protection.commitWindows[].{field}": value, "rule": "DX-ENV-002"},
                )
        return self


class ProtectionSpec(CamelModel):
    """The protection block a PRODUCTION environment is registered with (DX-ENV-002)."""

    model_config = ConfigDict(
        alias_generator=to_camel, populate_by_name=True, extra="forbid", str_strip_whitespace=True
    )

    timezone: str = "UTC"
    commit_windows: list[CommitWindowSpec] = Field(default_factory=list)
    #: S4 reads "not given" as the class default, so an explicit null must not be invented here
    deletion_policy: str | None = Field(
        default=None, json_schema_extra={"enum": list(DELETION_POLICIES)}
    )
    #: DX-ENV-007: declared so the refusal can name the rule rather than the field
    emergency_lock: bool = False
    required_approvals: int | None = None
    operator_role: str | None = None

    @model_validator(mode="after")
    def _check_protection(self) -> ProtectionSpec:
        if self.emergency_lock:
            raise ValidationFailedError(
                "emergencyLock cannot be set on the profile: use the emergency-lock "
                "endpoint, which records who, when and why",
                details={"emergencyLock": True, "rule": "DX-ENV-007"},
            )
        if self.deletion_policy is not None and self.deletion_policy not in DELETION_POLICIES:
            raise ValidationFailedError(
                "deletionPolicy is not one of the known policies",
                details={"deletionPolicy": self.deletion_policy, "allowed": list(DELETION_POLICIES)},
            )
        return self


class CreateEnvironmentRequest(CamelModel):
    #: stripped so a name of spaces alone falls back to the key in ``to_spec`` instead of
    #: reaching S4 as a blank display name, which it refuses as an invalid spec
    model_config = ConfigDict(
        alias_generator=to_camel,
        populate_by_name=True,
        extra="ignore",
        from_attributes=True,
        str_strip_whitespace=True,
    )

    environment_key: str
    name: str | None = None
    base_url: str
    environment_class: str = Field(default="TEST", json_schema_extra={"enum": list(ENVIRONMENT_CLASSES)})
    version_constraint: str | None = None
    auth_mode: str = Field(default="PAT", json_schema_extra={"enum": list(AUTH_MODES)})
    tls_required: bool = False
    llm_data_policy: str = Field(
        default="PRIVATE_NETWORK", json_schema_extra={"enum": list(LLM_DATA_POLICIES)}
    )
    timeout_seconds: int = 60
    notes: str | None = None
    credentials: list[CredentialSpec] = Field(default_factory=list)
    protection: ProtectionSpec | None = None

    @model_validator(mode="after")
    def _validate(self) -> CreateEnvironmentRequest:
        # Everything below is a rule S4's ``validate_spec`` applies to the merged profile.
        # S1 forwards this body to that call, so a rule checked only there answers as a
        # pydantic or domain error from the far end: a 500 that names no field, on a form
        # the operator can still submit.  Checking them here is what makes the answer 422.
        if not self.environment_key.strip() or any(ch.isspace() for ch in self.environment_key):
            raise ValidationFailedError(
                "environmentKey must not contain spaces",
                details={
                    "environmentKey": self.environment_key,
                    "fields": [{"path": "environmentKey", "message": "no whitespace allowed"}],
                },
            )
        if not self.base_url.startswith(("http://", "https://")):
            raise ValidationFailedError(
                "baseUrl must be an http(s) url",
                details={
                    "baseUrl": self.base_url,
                    "fields": [{"path": "baseUrl", "message": "must start with http:// or https://"}],
                },
            )
        if self.timeout_seconds < 1:
            raise ValidationFailedError(
                "timeoutSeconds must be a positive number of seconds",
                details={
                    "timeoutSeconds": self.timeout_seconds,
                    "fields": [{"path": "timeoutSeconds", "message": "must be 1 or more"}],
                },
            )
        # DX-ENV-001: without a constraint S1 cannot tell whether this environment speaks the
        # Profile it is about to parse, and guessing is what the constraint exists to prevent.
        if not (self.version_constraint or "").strip():
            raise ValidationFailedError(
                "a version constraint is required (2.41.x, 2.40 - 2.41)",
                details={
                    "rule": "DX-ENV-001",
                    "fields": [{"path": "versionConstraint", "message": "required by DX-ENV-001"}],
                },
            )
        if self.environment_class not in ENVIRONMENT_CLASSES:
            raise ValidationFailedError(
                "environmentClass is not one of the known classes",
                details={"environmentClass": self.environment_class, "allowed": list(ENVIRONMENT_CLASSES)},
            )
        if self.auth_mode not in AUTH_MODES:
            raise ValidationFailedError(
                "authMode must be PAT or BASIC", details={"authMode": self.auth_mode}
            )
        if self.llm_data_policy not in LLM_DATA_POLICIES:
            raise ValidationFailedError(
                "llmDataPolicy is not one of the known policies",
                details={"llmDataPolicy": self.llm_data_policy, "allowed": list(LLM_DATA_POLICIES)},
            )
        for credential in self.credentials:
            if credential.scope not in CREDENTIAL_SCOPES:
                raise ValidationFailedError(
                    "credentials[].scope is not a known scope",
                    details={"scope": credential.scope, "allowed": list(CREDENTIAL_SCOPES)},
                )
        _require_one_ref_per_scope(self.credentials)
        _require_required_scopes(self.credentials)
        if self.environment_class == "PRODUCTION":
            _require_distinct_production_refs(self.credentials)
            _require_commit_windows(self.protection)
        return self

    def to_spec(self) -> dict[str, Any]:
        """The payload S4's ``create_environment`` expects.

        No ``operator`` key: S4's ``EnvironmentSpec`` forbids unknown fields, so an extra key
        is a validation error rather than an ignored field, and the operator reaches S4 through
        the request context S1's middleware already fills from ``X-Operator``
        (``rest/middleware.py:100``).  ``name`` is S4's required display label and S1's is
        optional, so the key stands in when the caller did not give one.
        """
        return {
            "environmentKey": self.environment_key,
            "name": self.name or self.environment_key,
            "baseUrl": self.base_url,
            "environmentClass": self.environment_class,
            "versionConstraint": self.version_constraint,
            "authMode": self.auth_mode,
            "tlsRequired": self.tls_required,
            "llmDataPolicy": self.llm_data_policy,
            "timeoutSeconds": self.timeout_seconds,
            "notes": self.notes,
            "credentials": [c.model_dump(by_alias=True) for c in self.credentials],
            # `exclude_none` keeps "no deletion policy given" a real answer: S4 reads it as the
            # class default (DX-ENV-002), and a null here would say something else.
            "protection": self.protection.model_dump(by_alias=True, exclude_none=True) if self.protection else {},
        }


class UpdateEnvironmentRequest(CamelModel):
    #: whitespace is stripped before ``min_length`` is read, so a name of spaces alone is the
    #: same refusal as an empty one — and neither reaches S4 as a 500
    model_config = ConfigDict(
        alias_generator=to_camel,
        populate_by_name=True,
        extra="ignore",
        from_attributes=True,
        str_strip_whitespace=True,
    )

    #: Declared so a client that echoes the whole record back gets an explanation instead of a
    #: silent no-op: S4's ``EnvironmentPatch`` has no such field and forbids unknown ones, so
    #: forwarding it would surface as a 500 with no field named. A class is chosen at creation.
    environment_class: str | None = None
    #: ``min_length=1`` because S4 re-runs ``validate_spec`` over the merged profile and an
    #: empty name raises there — a pydantic error from inside S4 reaches S5 as a bare 500.
    name: str | None = Field(default=None, min_length=1)
    base_url: str | None = None
    version_constraint: str | None = None
    auth_mode: str | None = None
    tls_required: bool | None = None
    llm_data_policy: str | None = None
    timeout_seconds: int | None = None
    notes: str | None = None
    credentials: list[CredentialSpec] | None = None
    #: S4's patch does carry protection (``ensure_protection_row``), so an operator can amend
    #: the commit windows of a production environment without re-registering it.
    protection: ProtectionSpec | None = None

    @model_validator(mode="after")
    def _check_patch(self) -> UpdateEnvironmentRequest:
        if self.environment_class is not None:
            raise ValidationFailedError(
                "environmentClass cannot be changed after the environment is registered",
                details={
                    "environmentClass": self.environment_class,
                    "rule": "DX-ENV-001",
                    "fields": [
                        {
                            "path": "environmentClass",
                            "message": "environmentClass cannot be changed after creation",
                        }
                    ],
                },
            )
        if self.credentials is not None:
            _require_one_ref_per_scope(self.credentials)
            # S4 validates the *merged* profile, and a patch replaces the credential list
            # whole, so a list missing a required scope is refused here rather than there
            _require_required_scopes(self.credentials)
        if self.auth_mode is not None and self.auth_mode not in AUTH_MODES:
            raise ValidationFailedError(
                "authMode must be PAT or BASIC", details={"authMode": self.auth_mode}
            )
        if self.llm_data_policy is not None and self.llm_data_policy not in LLM_DATA_POLICIES:
            raise ValidationFailedError(
                "llmDataPolicy is not one of the known policies",
                details={"llmDataPolicy": self.llm_data_policy, "allowed": list(LLM_DATA_POLICIES)},
            )
        return self

    def to_patch(self) -> dict[str, Any]:
        """Only the fields the caller actually submitted (S5IF-ENV04)."""
        submitted = self.model_dump(by_alias=True, exclude_none=True)
        submitted.pop("environmentClass", None)
        return submitted


class DisableEnvironmentRequest(CamelModel):
    reason: str | None = None


class EmergencyLockRequest(CamelModel):
    locked: bool
    reason: str | None = None

    @model_validator(mode="after")
    def _reason_required_when_locking(self) -> EmergencyLockRequest:
        if self.locked and not (self.reason or "").strip():
            raise ValidationFailedError(
                "locking an environment requires a reason",
                details={"fields": [{"path": "reason", "message": "locking an environment requires a reason"}]},
            )
        return self


class TestConnectionRequest(CamelModel):
    required_scopes: list[str] | None = None

    @model_validator(mode="after")
    def _check_scopes(self) -> TestConnectionRequest:
        if self.required_scopes is None:
            return self
        unknown = [s for s in self.required_scopes if s not in CREDENTIAL_SCOPES]
        if unknown:
            raise ValidationFailedError(
                "requiredScopes contains unknown scopes",
                details={"unknown": unknown, "allowed": list(CREDENTIAL_SCOPES)},
            )
        return self

    def scopes_or_default(self) -> list[str]:
        return list(self.required_scopes or ["discovery", "import"])


class ObjectDeletionRequest(CamelModel):
    """The three things S4's ``delete_objects`` needs, and nothing S4 must not be told (DX-EXE-038).

    ``extra="forbid"`` is deliberate.  A caller that puts ``idempotencyKey`` in the body would
    otherwise have it dropped in silence while believing the delete was protected against a
    replay — S5IF-G08 【裁决】 made the ``Idempotency-Key`` **header** the only source of the key,
    and a deletion is the one call where that mistake cannot be undone afterwards.

    There is also no ``importOptions`` field: DX-EXE-038 submits the candidate set with
    ``importStrategy=DELETE`` and ``atomicMode=ALL``, and those are S4's safety rules rather than
    a per-request preference (a half-deleted batch is harder to explain than none at all).
    """

    model_config = ConfigDict(
        alias_generator=to_camel,
        populate_by_name=True,
        extra="forbid",
        from_attributes=True,
        str_strip_whitespace=True,
    )

    #: S4 cross-checks the payload's own ``projectId`` against this (``load_context``) and raises
    #: a bare ``ValueError`` when they differ, which would reach the caller as a 500 naming no
    #: field; S1 compares the two against its artifact index first and answers 404 / 422.
    project_id: str = Field(min_length=1)
    deletion_artifact_id: str = Field(min_length=1)
    #: The hash the deletion candidate set was approved against — DX-EXE-005's rule, read the
    #: same way for a deletion: what was validated is what is submitted.
    expected_hash: str = Field(min_length=1)

    @model_validator(mode="after")
    def _check_blanks(self) -> ObjectDeletionRequest:
        """``min_length`` is measured before stripping, so a field of spaces reaches S4 as a key.

        An all-whitespace ``deletionArtifactId`` would be looked up as a literal id and answered
        with S4's artifact-not-found error rather than with the field that was left blank.
        """
        blank = [
            name
            for name, value in (
                ("projectId", self.project_id),
                ("deletionArtifactId", self.deletion_artifact_id),
                ("expectedHash", self.expected_hash),
            )
            if not value.strip()
        ]
        if blank:
            raise ValidationFailedError(
                "a deletion names the project, the payload and the hash it was approved against",
                details={
                    "rule": "DX-EXE-038",
                    "fields": [{"path": name, "message": "must not be blank"} for name in blank],
                },
            )
        return self


def _require_required_scopes(credentials: list[CredentialSpec]) -> None:
    """S4's ``REQUIRED_SCOPES``, every class: discovery reads, import writes.

    A profile without one of them is not a partially filled form — it is an environment that
    cannot do the second half of its job, so S4 refuses it and S5 must too.
    """
    present = {credential.scope for credential in credentials}
    missing = [scope for scope in REQUIRED_CREDENTIAL_SCOPES if scope not in present]
    if missing:
        raise ValidationFailedError(
            "an environment needs a secret reference for each required scope",
            details={
                "missingScopes": missing,
                "required": list(REQUIRED_CREDENTIAL_SCOPES),
                "fields": [{"path": "credentials", "message": f"missing {', '.join(missing)}"}],
            },
        )


def _require_one_ref_per_scope(credentials: list[CredentialSpec]) -> None:
    """One credential per scope: S4 keys its rows by scope and S5's form can add a second row.

    Two rows for the same scope reach S4 as an ``IntegrityError`` — a 500 that names nothing —
    and reach the fake as a silent "last one wins", which reads to the operator as a save that
    dropped the first row.
    """
    seen: set[str] = set()
    duplicates: set[str] = set()
    for credential in credentials:
        if credential.scope in seen:
            duplicates.add(credential.scope)
        seen.add(credential.scope)
    if duplicates:
        raise ValidationFailedError(
            "each credential scope takes one secret reference",
            details={
                "duplicateScopes": sorted(duplicates),
                "fields": [
                    {"path": "credentials[].scope", "message": "one reference per scope"}
                ],
            },
        )


def _require_commit_windows(protection: ProtectionSpec | None) -> None:
    """DX-ENV-002: a production environment is only production once its windows are named."""
    if protection is None or not protection.commit_windows:
        raise ValidationFailedError(
            "a production environment must configure its allowed commit windows",
            details={
                "rule": "DX-ENV-002",
                "fields": [
                    {
                        "path": "protection.commitWindows",
                        "message": "at least one commit window is required for PRODUCTION",
                    }
                ],
            },
        )


def _require_distinct_production_refs(credentials: list[CredentialSpec]) -> None:
    """DX-ENV-005 / DX-AC-02: discovery and import must not share one secret."""
    refs = {c.scope: c.secret_ref for c in credentials}
    discovery, import_ref = refs.get("discovery"), refs.get("import")
    if discovery is None or import_ref is None:
        raise ValidationFailedError(
            "a production environment needs both a discovery and an import credential reference",
            details={"missing": [s for s, v in (("discovery", discovery), ("import", import_ref)) if v is None]},
        )
    if discovery == import_ref:
        raise ValidationFailedError(
            "production discovery and import must use different secret references",
            details={
                "conflictScopes": ["discovery", "import"],
                "environmentClass": "PRODUCTION",
            },
        )


__all__ = [
    "CapabilityProfileView",
    "CommitWindowSpec",
    "ConnectionCheckItemView",
    "ConnectionCheckView",
    "CreateEnvironmentRequest",
    "DisableEnvironmentRequest",
    "DisableResultView",
    "DiscoveryResultPage",
    "DiscoveryResultView",
    "EmergencyLockRequest",
    "EmergencyLockView",
    "EnvironmentPage",
    "EnvironmentProtectionView",
    "EnvironmentView",
    "ExpiringCredentialList",
    "ExpiringCredentialView",
    "ObjectDeletionRequest",
    "ObjectDeletionView",
    "ProbeResultView",
    "ProtectionSpec",
    "TestConnectionRequest",
    "UpdateEnvironmentRequest",
    "require_reference",
]
