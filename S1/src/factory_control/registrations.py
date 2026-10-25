"""S1 registrations into the foundation catalogs (FD-ASM-01, CP-RT-004).

Everything S1 must declare at start-up, as data: the artifact types it owns,
the audit event types it writes (附录 C), its error codes (SRS 8.3) and its ID
prefixes (FD-COM-002).
"""

from __future__ import annotations

from factory_control.errors import S1_ERROR_RETRYABLE
from factory_control.foundation import error_catalog

#: Artifact types owned by S1 (FD-ART-002).  Re-registering merges with the
#: foundation's initial catalog, which already lists these three for S1.
S1_ARTIFACT_TYPES: dict[str, list[str]] = {
    "BUSINESS_INPUT": ["S1"],
    "CHANGE_IMPACT_REPORT": ["S1"],
    "PROJECT_EXPORT": ["S1"],
}

#: Audit event types written by S1 (附录 C).  The foundation rejects any
#: unregistered type, so this list is also the audit-coverage checklist.
S1_AUDIT_EVENT_TYPES: dict[str, str] = {
    "cp.project.created": "S1",
    "cp.project.updated": "S1",
    "cp.project.archived": "S1",
    "cp.project.exported": "S1",
    "cp.project.imported": "S1",
    "cp.project.deleted": "S1",
    "cp.project.environment_updated": "S1",
    "cp.input.uploaded": "S1",
    "cp.artifact.purged": "S1",
    "cp.workflow.created": "S1",
    "cp.workflow.paused": "S1",
    "cp.workflow.resumed": "S1",
    "cp.workflow.cancelled": "S1",
    "cp.workflow.completed": "S1",
    "cp.workflow.updated": "S1",
    "cp.stage.started": "S1",
    "cp.stage.completed": "S1",
    "cp.stage.invalidated": "S1",
    "cp.stage.rerun": "S1",
    "cp.stage.patch_submitted": "S1",
    "cp.stage.manual_check_resolved": "S1",
    "cp.question.created": "S1",
    "cp.question.answered": "S1",
    "cp.question.withdrawn": "S1",
    "cp.question.superseded": "S1",
    "cp.gate.created": "S1",
    "cp.gate.approved": "S1",
    "cp.gate.rejected": "S1",
    "cp.gate.invalidated": "S1",
    # --- M4 identity and permissions (总体 SRS 21.4 names 用户与角色变更 explicitly) -----
    "cp.user.created": "S1",
    "cp.user.updated": "S1",
    "cp.user.disabled": "S1",
    "cp.user.password_set": "S1",
    "cp.user.role_changed": "S1",
    "cp.project.member_added": "S1",
    "cp.project.member_removed": "S1",
    "cp.auth.login": "S1",
    "cp.auth.login_failed": "S1",
    "cp.auth.logout": "S1",
    # FR-SEC-003: a restricted read is authorised separately and recorded every time
    "cp.artifact.grant_created": "S1",
    "cp.artifact.grant_revoked": "S1",
    "cp.artifact.read_restricted": "S1",
    # CP-WF-020's exemption arrives with M4's permissions
    "cp.production.exemption_granted": "S1",
    "cp.production.exemption_revoked": "S1",
    # CP-SEC-001: who put a credential here, and who took it away.  The row records the reference
    # and the timestamps; the value is not something an audit event can carry (FR-SEC-008).
    "cp.secret.written": "S1",
    "cp.secret.deleted": "S1",
    "cp.deployment_run.created": "S1",
    "cp.deployment_run.completed": "S1",
    "cp.deployment_run.cancelled": "S1",
    "cp.deployment_run.outdated": "S1",
    "cp.deployment_run.committed": "S1",
    "cp.deployment_run.verified": "S1",
    "cp.deployment_run.drift_checked": "S1",
    "cp.deployment_run.failed": "S1",
    # 21.4 requires the start and the end of every Agent run, of VALIDATE and of COMMIT to
    # be audited; the SSE event of the same name is written with it (C-02, 19.2.3).  S4 keeps
    # its own ``dx.*`` records of the instance side — these are S1's record of the step.
    "cp.agent_run.started": "S1",
    "cp.agent_run.completed": "S1",
    "cp.validation.completed": "S1",
    "cp.execution.started": "S1",
    "cp.execution.completed": "S1",
    "cp.execution.failed": "S1",
    "cp.change_request.created": "S1",
    "cp.change_request.updated": "S1",
    "cp.change_request.analyzed": "S1",
    "cp.change_request.approved": "S1",
    "cp.change_request.rejected": "S1",
    "cp.change_request.started": "S1",
    "cp.change_request.completed": "S1",
    "cp.change_request.cancelled": "S1",
    "cp.notification.created": "S1",
    "cp.notification.read": "S1",
    "cp.settings.updated": "S1",
    "cp.schedule.completed": "S1",
    # Written on behalf of S4: S1 flips the lock through its own public endpoint, so
    # the record has an S1 owner (S5IF-ENV07).  S4 keeps its own ``dx.*`` namespace.
    "cp.environment.lock_changed": "S1",
    "cp.environment.created": "S1",
    "cp.environment.updated": "S1",
    "cp.environment.disabled": "S1",
    "cp.environment.connection_tested": "S1",
    "cp.environment.capabilities_probed": "S1",
    "cp.input.parsed": "S1",
    "cp.input.parse_started": "S1",
    "cp.artifact.explained": "S1",
    "cp.agent_run.cancelled": "S1",
    "cp.registry.binding_set": "S1",
    "cp.uid_pool.replenished": "S1",
    "cp.governance.provider_registered": "S1",
    "cp.governance.model_registered": "S1",
    "cp.governance.prompt_activated": "S1",
    "cp.governance.policy_relaxed": "S1",
    "cp.governance.evaluation_run": "S1",
}

#: ID prefixes owned by S1.
#:
#: ``stg`` is already owned by the foundation (artifact staging handles), so
#: stages take ``sta`` even though 总体 SRS 19.2.3 illustrates ``stg-0441``.
S1_ID_PREFIXES: dict[str, str] = {
    "prj": "S1",  # Project
    "tge": "S1",  # ProjectTargetEnvironment
    "pset": "S1",  # ProjectSetting
    "wf": "S1",  # Workflow
    "sta": "S1",  # Stage
    "srn": "S1",  # StageRun
    "ipr": "S1",  # InputParseRun
    "run": "S1",  # DeploymentRun
    "qst": "S1",  # Question
    "gat": "S1",  # Gate
    "apr": "S1",  # Approval
    "crq": "S1",  # ChangeRequest
    "evt": "S1",  # EventStreamRecord
    "ntf": "S1",  # Notification
    "ndl": "S1",  # NotificationDelivery
    "sys": "S1",  # SystemSetting
    "idr": "S1",  # IdempotentResponse
    "usr": "S1",  # User
    "uro": "S1",  # UserRole
    "pmb": "S1",  # ProjectMember
    "ses": "S1",  # Session
    "rgt": "S1",  # RestrictedGrant
    "pex": "S1",  # ProductionExemption
    "sec": "S1",  # SecretRecord (CP-SEC-001)
}

#: Error codes S1 registers into the foundation catalog: ``{code: (owner, retryable)}``.
#: Retryability here decides which Temporal activities are retried (CP-WF-009).
#:
#: FD-COM-001a (S0 V1.0.1) gives every code exactly one owner and the catalog refuses a
#: re-registration under another name — boot would raise ``RegistryConflictError`` if S1
#: claimed what S0 pre-registers (NOT_FOUND, IDEMPOTENCY_CONFLICT, VALIDATION_ERROR,
#: INTERNAL_ERROR, CANCELLED, SECRET_UNAVAILABLE, TRANSACTION_MISUSE, MIGRATION_ERROR,
#: CONCURRENCY_ERROR, the ARTIFACT_* family) or what belongs upstream (SCHEMA_ERROR and
#: REGISTRY_ERROR to S2, LLM_* and POLICY_DENIED to S3, DHIS2_*, DRIFT_DETECTED and
#: ENVIRONMENT_LOCKED to S4).  S1 raises those directly, which is what FD-COM-001a is for; it
#: registers only the codes it owns — its own judgments plus what S0 already attributes to S1
#: (GATE_NOT_APPROVED, APPROVAL_INVALIDATED).
#:
#: Codes named here are S4's even though S1 answers with them.
_UPSTREAM_OWNED: frozenset[str] = frozenset({"ENVIRONMENT_LOCKED"})


def _registrable_as_s1(code: str) -> bool:
    """Whether S1 may register ``code``.

    ``_UPSTREAM_OWNED`` is stated rather than read from the catalog, because the catalog only
    knows what whoever ran first registered: while the S4 fakes are in place nothing has claimed
    ``ENVIRONMENT_LOCKED`` at all, so a catalog-only filter would let S1 take it — and then refuse
    S4 the moment the real adapter shared the process.
    """
    if code in _UPSTREAM_OWNED:
        return False
    meta = error_catalog.get(code)
    return meta is None or meta.owner_subsystem == "S1"


def s1_error_codes() -> dict[str, tuple[str, bool]]:
    """The codes S1 registers, computed when asked rather than at import.

    The catalog check above is a snapshot of who has registered so far, and boot initializes the
    providers before S1 registers itself (``bootstrap.bootstrap``); evaluating this at import time
    froze the answer to whatever the process happened to import first.
    """
    return {
        code: ("S1", retryable)
        for code, retryable in S1_ERROR_RETRYABLE.items()
        if _registrable_as_s1(code)
    }


__all__ = [
    "S1_ARTIFACT_TYPES",
    "S1_AUDIT_EVENT_TYPES",
    "S1_ID_PREFIXES",
    "s1_error_codes",
]
