"""S2 registries: artifact types, audit event types, error codes, ID prefixes.

00 doc 6.10 requires every library subsystem to register these at start-up; S0
rejects unregistered artifact types, event types, error codes and ID prefixes.
"""

from __future__ import annotations

#: Artifact types S2 creates (FD-ART-002 catalogue) plus the two types S2 adds
#: for its own outputs: the environment-independent metadata baseline
#: (BP-CMP-010) and the ``bp`` data export file (BP-EXP-001).
BP_ARTIFACT_TYPES: dict[str, list[str]] = {
    "BLUEPRINT": ["S2"],
    "METADATA_PACKAGE": ["S2"],
    "METADATA_BASELINE": ["S2"],
    "DATASTORE_CHANGESET": ["S2"],
    "OPENAPI": ["S2"],
    "UI_SPECIFICATION": ["S2"],
    "FIXTURE_PACKAGE": ["S2"],
    "VALIDATION_REPORT": ["S2"],
    "FRONTEND_PACKAGE": ["S2"],
    "HANDOFF_CHECK_REPORT": ["S2"],
    "MANIFEST": ["S2"],
    "DIFF_REPORT": ["S2", "S4"],
    "BLUEPRINT_EXPORT": ["S2"],
}

#: Audit events produced by S2 (S2 SRS appendix C).
BP_AUDIT_EVENT_TYPES: dict[str, str] = {
    "bp.blueprint.initialized": "S2",
    "bp.patch.submitted": "S2",
    "bp.patch.rejected": "S2",
    "bp.draft.reset": "S2",
    "bp.draft.frozen": "S2",
    "bp.version.created": "S2",
    "bp.env_binding.set": "S2",
    "bp.build.completed": "S2",
    "bp.build.failed": "S2",
    "bp.validation.completed": "S2",
    "bp.handoff.checked": "S2",
    "bp.project.imported": "S2",
    "bp.project.data_deleted": "S2",
}

#: ``{code: (owner, retryable)}`` — retryability follows overall SRS 19.2.2.
#: ``SCHEMA_ERROR`` and ``REGISTRY_ERROR`` are already owned by S2 in the
#: foundation catalogue, so they are not repeated here.
BP_ERROR_CODES: dict[str, tuple[str, bool]] = {
    # BP-REG-008a.3: the caller replenishes the pool and retries
    "UID_POOL_EXHAUSTED": ("S2", True),
    # a malformed RFC 6902 operation is a programming/agent error, never retried
    "BP_PATCH_MALFORMED": ("S2", False),
    "BP_UNSUPPORTED_SCHEMA_VERSION": ("S2", False),
    # a compiler this build of S2 does not deliver (BP-TD-002: M5 work remains)
    "BP_FEATURE_NOT_ENABLED": ("S2", False),
    # promote refused because the frozen draft carries BLOCKING issues (BP-DRF-008)
    "BP_NOT_PROMOTABLE": ("S2", False),
    "BP_IN_PROGRESS": ("S2", True),
    "BP_IMPORT_CONFLICT": ("S2", False),
    # the draft base must be staged by an external-I/O call first (00 doc 6.2)
    "BP_DRAFT_NOT_MATERIALIZED": ("S2", True),
    # BP-VAL-021: the local expression parser has no grammar for that DHIS2 version
    "BP_EXPRESSION_GRAMMAR_MISSING": ("S2", False),
}

#: Codes S0 owns but a deployed S0 may not have registered yet, so S2 fills the gap only
#: when the code is genuinely absent (see ``bootstrap._register_all``) and never overwrites
#: an owner S0 declared.  ``ARTIFACT_TYPE_MISMATCH`` is S0's per FD-COM-001a, and BP-HO-004
#: forbids S2 registering it.
#:
#: TODO(S0 >= 0.2.0, the patch that ships FD-COM-001a): delete this entry once every
#: deployment runs an S0 that registers the code itself — S2 must not own it.
S0_PENDING_ERROR_CODES: dict[str, tuple[str, bool]] = {
    # BP-HO-004: the artifact exists but is not a FRONTEND_PACKAGE; the caller's error
    "ARTIFACT_TYPE_MISMATCH": ("S0", False),
}

#: ID prefixes (00 doc 6.7: type-prefixed ULID).
BP_ID_PREFIXES: dict[str, str] = {
    "bpv": "S2",
    "dft": "S2",
    "ptch": "S2",
    "bld": "S2",
    "vrun": "S2",
    "imp": "S2",
}

#: Idempotency scopes (S0 idempotency store), all namespaced by subsystem.
class IdemScope:
    PATCH_SUBMIT = "bp.patch.submit"
    DRAFT_PROMOTE = "bp.draft.promote"
    MODE_UPGRADE = "bp.mode.upgrade"
    COMPILE_BASELINE = "bp.compile.baseline"
    COMPILE_ENVIRONMENT = "bp.compile.environment"
    VALIDATE_BUILD = "bp.validate.build"
    FRONTEND_PACKAGE = "bp.frontend.package"
    IMPORT_COMMIT = "bp.import.commit"
    INITIAL_REGISTER = "bp.blueprint.initial_register"
