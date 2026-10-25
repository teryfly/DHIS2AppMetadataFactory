"""Environment and capability forwarding (S5IF-ENV, SRS 7.2 "环境管理，供公共 API 转发").

Two rules shape every function here:

* nothing is persisted.  An environment is S4's object; S1 projects what S4 says and
  stores no copy (CP-API-007, D-14), and protection facts are read live rather than
  cached (CP-WF-027);
* no unit of work is open across a provider call (00 doc 6.2).  So these functions
  call the port directly and only take a transaction where S1 writes an audit record.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from factory_control.errors import (
    InvalidStateError,
    NotFoundControlError,
    ValidationFailedError,
)
from factory_control.ports.dto import (
    CapabilityFreshness,
    ConnectionCheck,
    DiscoveryResultInfo,
    EnvironmentInfo,
    EnvironmentProtection,
    ExecutionOutcome,
)
from factory_control.runtime import Runtime

#: How long the display-only environment class may be reused (S5IF-FLD04 【裁决】).
CLASS_CACHE_SECONDS = 60
_class_cache: dict[str, tuple[float, str]] = {}


def environment_class(runtime: Runtime, environment_key: str | None) -> str | None:
    """S4's protection class, cached in-process for at most 60 seconds.

    The class belongs to S4; copying it into ``cp`` would make S1 the authority over
    another subsystem's data (D-14, CP-API-007), so a Run list asks for it per row.  That
    is a provider call per row on a Dashboard request, hence the short cache the ruling
    allows.

    Anything that *decides* rather than displays — a COMMIT guard, an allowed-actions
    evaluation — reads protection live and must not come through here (CP-WF-027).
    """
    import time

    if not environment_key:
        return None
    now = time.monotonic()
    cached = _class_cache.get(environment_key)
    if cached is not None and now - cached[0] < CLASS_CACHE_SECONDS:
        return cached[1]
    value = runtime.execution.get_protection(environment_key).environment_class
    _class_cache[environment_key] = (now, value)
    return value


def clear_environment_class_cache() -> None:
    """Drop the short cache — a new harness must not inherit the previous test's view."""
    _class_cache.clear()


def binding_candidates(
    runtime: Runtime,
    run_id: str,
    business_key: str,
    *,
    operator: str,
    dhis2_type: str | None = None,
    query: str | None = None,
    limit: int = 50,
    offset: int = 0,
) -> dict[str, Any]:
    """S5IF-BP11 — the objects this binding could point at, from the stored discovery.

    S1 resolves *which* discovery result to search (the newest one for this project in this
    environment) and which DHIS2 type the object is, from S2's registry refs, then asks S4
    to page through the stored artifact.  S4 owns the DISCOVERY_RESULT format, so S1 never
    parses one; and the read touches no instance, which is why a search box may call it at
    all (S5IF-UP10).
    """
    from factory_control import store as S

    with runtime.transaction(operator=operator) as ctx:
        run = S.get_run(ctx.session, run_id)
        workflow = S.get_workflow(ctx.session, run.workflow_id)
        project_id, version, environment_key = workflow.project_id, run.blueprint_version, run.environment_key

    kind = dhis2_type or _registry_object_type(runtime, project_id, version, environment_key, business_key)
    found = runtime.execution.list_discovery_results(environment_key, project_id)
    if not found:
        raise NotFoundControlError(
            f"no discovery result exists for environment {environment_key}",
            details={"environmentKey": environment_key, "businessKey": business_key},
        )
    artifact_id = str(found[0].artifact_id)
    result = runtime.execution.search_discovery_objects(
        artifact_id, dhis2_type=kind, query=query, limit=limit, offset=offset
    )
    return {
        **dict(result),
        "discoveryArtifactId": artifact_id,
        "dhis2Type": kind,
        "environmentKey": environment_key,
        "businessKey": business_key,
    }


def _registry_object_type(
    runtime: Runtime, project_id: str, version: str, environment_key: str, business_key: str
) -> str | None:
    """The DHIS2 type the Registry gives this business key, so S4 can filter on it."""
    for row in runtime.blueprint.registry_object_refs(project_id, version, environment_key):
        if str(row.get("businessKey") or row.get("business_key") or "") != business_key:
            continue
        return str(row.get("dhis2Type") or row.get("dhis2_type") or "") or None
    return None


def last_connection_record(runtime: Runtime, environment_key: str) -> dict[str, Any] | None:
    """What the console shows for the connection: the last test S4 recorded (S5IF-FLD05).

    S4 answers this from its own persisted rows (DX-CON-005), so the read costs no DHIS2
    call and is safe inside a GET.  A provider build without the read at all, or one that
    cannot be reached, still answers ``null`` rather than failing the whole panel.
    Re-testing is a deliberate action (``POST .../test-connection``), never a side effect
    of a GET.
    """
    from factory_control.ports.protocols import ProviderUnavailableError

    reader = getattr(runtime.execution, "get_last_connection_test", None)
    if reader is None:
        return None
    try:
        row = reader(environment_key)
    except ProviderUnavailableError:
        return None
    if row is None:
        return None
    return {
        "ok": bool(row.ok),
        "result": row.result,
        "missingAuthorities": dict(row.missing_authorities or {}),
        "lastTestedAt": row.tested_at,
    }


def list_environments(
    runtime: Runtime,
    *,
    environment_class: str | None = None,
    status: str | None = None,
    limit: int = 50,
    offset: int = 0,
) -> dict[str, Any]:
    rows = runtime.execution.list_environments()
    if environment_class:
        rows = [r for r in rows if (r.environment_class or "").upper() == environment_class.upper()]
    if status:
        rows = [r for r in rows if (r.status or "").upper() == status.upper()]
    rows.sort(key=lambda r: r.environment_key)
    return {
        "items": [_environment_view(r) for r in rows[offset : offset + limit]],
        "total": len(rows),
        "limit": limit,
        "offset": offset,
    }


def get_environment(runtime: Runtime, environment_key: str) -> dict[str, Any]:
    row = runtime.execution.get_environment(environment_key)
    if row is None:
        raise NotFoundControlError(
            f"unknown environment {environment_key}", details={"environmentKey": environment_key}
        )
    return _environment_view(row)


def create_environment(runtime: Runtime, spec: dict[str, Any]) -> dict[str, Any]:
    return _environment_view(runtime.execution.create_environment(spec))


def update_environment(runtime: Runtime, environment_key: str, patch: dict[str, Any]) -> dict[str, Any]:
    if runtime.execution.get_environment(environment_key) is None:
        raise NotFoundControlError(
            f"unknown environment {environment_key}", details={"environmentKey": environment_key}
        )
    if not patch:
        raise ValidationFailedError("no fields submitted", details={"fields": []})
    return _environment_view(runtime.execution.update_environment(environment_key, patch))


#: S4's `EnvironmentStatus` has exactly two values, and this is the one that means "out of service".
OUT_OF_SERVICE = "DISABLED"


def _require_in_service(runtime: Runtime, environment_key: str) -> EnvironmentInfo:
    """Read the row, then refuse what S4 would refuse anyway.

    S4 checks the status before anything else (``environment/loader.py`` puts the DISABLED test
    ahead of ``allow_unprobed``), and ``rules.can_disable`` refuses a second disable.  Making the
    same check here means the answer does not depend on which provider is mounted: a ``fakes``
    deployment that accepted an operation against a disabled instance would let the UI pass every
    test and then meet a 409 the first time it runs for real.
    """
    info = runtime.execution.get_environment(environment_key)
    if info is None:
        raise NotFoundControlError(
            f"unknown environment {environment_key}", details={"environmentKey": environment_key}
        )
    if str(info.status).upper() == OUT_OF_SERVICE or not info.enabled:
        raise InvalidStateError(
            f"environment {environment_key} is out of service and answers nothing about the instance",
            details={
                "environmentKey": environment_key,
                "status": OUT_OF_SERVICE,
                "reasonCode": "ENVIRONMENT_DISABLED",
            },
        )
    return info


def disable_environment(runtime: Runtime, environment_key: str, reason: str | None) -> dict[str, Any]:
    _require_in_service(runtime, environment_key)
    runtime.execution.disable_environment(environment_key, reason)
    return {"environmentKey": environment_key, "status": "DISABLED"}


def get_protection(runtime: Runtime, environment_key: str) -> dict[str, Any]:
    """Live, never cached (CP-WF-027): the caller must see this instant's answer."""
    return _protection_view(runtime.execution.get_protection(environment_key))


def set_emergency_lock(
    runtime: Runtime, environment_key: str, *, locked: bool, reason: str | None, operator: str
) -> dict[str, Any]:
    result = runtime.execution.set_emergency_lock(environment_key, locked, reason, operator)
    view = dict(result or {})
    view.setdefault("environmentKey", environment_key)
    view.setdefault("emergencyLocked", locked)
    view.setdefault("lockReason", reason if locked else None)
    _audit_lock_change(runtime, environment_key=environment_key, locked=locked, reason=reason, operator=operator)
    return view


def test_connection(
    runtime: Runtime, environment_key: str, *, required_scopes: list[str], operator: str
) -> dict[str, Any]:
    _require_in_service(runtime, environment_key)
    check = runtime.execution.test_connection(
        environment_key,
        required_scopes,
        idempotency_key=f"test-connection:{environment_key}:{operator}",
    )
    return _connection_view(check, environment_key=environment_key, required_scopes=required_scopes)


def capability_profile(
    runtime: Runtime, environment_key: str, *, include_content: bool
) -> dict[str, Any]:
    freshness = runtime.execution.check_capability_freshness(environment_key)
    return _capability_view(freshness, environment_key=environment_key, include_content=include_content)


def probe_capabilities(runtime: Runtime, environment_key: str, *, operator: str) -> dict[str, Any]:
    _require_in_service(runtime, environment_key)
    artifact_id = runtime.execution.probe_capabilities(
        environment_key, idempotency_key=f"probe:{environment_key}:{operator}"
    )
    freshness = runtime.execution.check_capability_freshness(environment_key)
    return {
        "capabilityProfileArtifactId": artifact_id or freshness.capability_profile_artifact_id,
        "version": freshness.dhis2_version or freshness.version,
        "profileVersion": freshness.profile_version,
    }


def delete_objects(
    runtime: Runtime,
    environment_key: str,
    *,
    project_id: str,
    deletion_artifact_id: str,
    expected_hash: str,
    operator: str,
    idempotency_key: str,
) -> dict[str, Any]:
    """Submit a deletion payload to S4 and report what the instance did (DX-EXE-038).

    S1 decides **nothing** about whether the deletion is allowed.  S4 validates the same payload
    it is about to submit (DX-BR-05), re-runs the associated-data check after that validation and
    immediately before the request (DX-QRY-004), obeys the environment's ``deletionPolicy`` and
    its emergency lock (DX-ENV-007), and refuses the whole batch when one candidate is refused —
    a delete is the one operation a DHIS2 instance cannot roll back.  A guard copied onto S1's
    side could only be weaker than that, or disagree with it while both ran.

    Two checks do belong here, because S4 answers them badly rather than safely:

    * a payload from another project reaches S4 as a bare ``ValueError`` from ``load_context``
      (CP-API-008 wants a code and a field, not a 500);
    * an environment S4 has switched off is refused by S4's own loader, and the same
      ``_require_in_service`` the connection test and the probe use keeps that answer the same
      whether the deployment runs on the real S4 or on the double.

    No S1 audit row is written: S4 records the deletion itself (its own ``delete.executed``
    event, S4 SRS 附录 A) with the operator, the environment and the candidate count, and
    CP-API-007 forbids a second copy of a fact the provider owns.
    """
    _require_in_service(runtime, environment_key)
    _require_payload_of_project(
        runtime,
        environment_key=environment_key,
        project_id=project_id,
        deletion_artifact_id=deletion_artifact_id,
        operator=operator,
    )
    # external I/O: no unit of work is open here (00 doc 6.2)
    outcome = runtime.execution.delete_objects(
        environment_key,
        project_id,
        deletion_artifact_id,
        expected_hash,
        operator=operator,
        # the same shape COMMIT uses (routes/runs.py): S4 keys its execution row on this, and a
        # replay must return the first verdict instead of deleting a second time (NFR-REL-003).
        idempotency_key=f"delete:{idempotency_key[:40]}",
    )
    return _deletion_view(
        outcome, environment_key=environment_key, deletion_artifact_id=deletion_artifact_id
    )


def _require_payload_of_project(
    runtime: Runtime, *, environment_key: str, project_id: str, deletion_artifact_id: str, operator: str
) -> None:
    """The deletion payload has to be this project's own artifact, or the call is refused here.

    Read from S0's artifact index, which is the only place the ownership fact lives — S1 keeps no
    copy of it (CP-API-007).
    """
    from factory_control.foundation import get_artifact

    with runtime.transaction(operator=operator) as ctx:
        row = get_artifact(deletion_artifact_id, session=ctx.session)
    if row is None:
        raise NotFoundControlError(
            f"unknown deletion payload {deletion_artifact_id}",
            details={
                "artifactId": deletion_artifact_id,
                "projectId": project_id,
                "environmentKey": environment_key,
            },
        )
    owner = str(getattr(row, "project_id", "") or "")
    if owner and owner != project_id:
        raise ValidationFailedError(
            f"the deletion payload belongs to project {owner}, not {project_id}",
            details={
                "rule": "DX-EXE-038",
                "artifactId": deletion_artifact_id,
                "projectId": project_id,
                "fields": [
                    {
                        "path": "projectId",
                        "message": f"the payload belongs to {owner}",
                    }
                ],
            },
        )


def discovery_results(
    runtime: Runtime,
    environment_key: str,
    *,
    project_id: str | None,
    scope: str | None,
    limit: int,
    offset: int,
) -> dict[str, Any]:
    rows = runtime.execution.list_discovery_results(
        environment_key, project_id=project_id, scope=scope, limit=100_000, offset=0
    )
    return {
        "items": [_discovery_view(row) for row in rows[offset : offset + limit]],
        "total": len(rows),
        "limit": limit,
        "offset": offset,
    }


def expiring_credentials(runtime: Runtime, within_days: int) -> dict[str, Any]:
    rows = runtime.execution.list_expiring_credentials(within_days)
    now = datetime.now(UTC)
    items = []
    for row in rows:
        remaining = None if row.expires_at is None else max(0, (row.expires_at - now).days)
        items.append(
            {
                "environmentKey": row.environment_key,
                "scope": row.scope,
                "secretRef": row.secret_ref,
                "expiresAt": row.expires_at,
                "daysRemaining": remaining,
            }
        )
    return {"items": items}


# --- Projections -------------------------------------------------------------


def _environment_view(row: EnvironmentInfo) -> dict[str, Any]:
    return {
        "environmentKey": row.environment_key,
        "name": row.name,
        "baseUrl": row.base_url,
        "environmentClass": row.environment_class,
        "versionConstraint": row.version_constraint,
        "dhis2Version": row.version,
        "authMode": row.auth_mode,
        "tlsRequired": row.tls_required,
        "llmDataPolicy": row.llm_data_policy,
        "timeoutSeconds": row.timeout_seconds,
        "status": row.status if row.enabled else "DISABLED",
        "notes": row.notes,
        # References and expiry only — never a value, mask, length or prefix (G09).
        "secretRefs": dict(row.secret_refs),
        "credentials": [
            {
                "scope": c.scope,
                "secretRef": c.secret_ref,
                "expiresAt": c.expires_at,
                "label": c.label,
            }
            for c in row.credentials
        ],
        "createdAt": row.created_at,
        "updatedAt": row.updated_at,
    }


def _protection_view(row: EnvironmentProtection) -> dict[str, Any]:
    return {
        "environmentKey": row.environment_key,
        "environmentClass": row.environment_class,
        "timezone": row.timezone,
        "commitWindows": list(row.commit_windows),
        "withinCommitWindow": row.within_commit_window,
        "emergencyLocked": row.emergency_locked,
        "lockReason": row.lock_reason,
        "lockChangedAt": row.lock_changed_at,
        "lockChangedBy": row.lock_changed_by,
        "deletionPolicy": row.deletion_policy,
        "requiredApprovals": row.required_approvals,
        "operatorRole": row.operator_role,
        "allowsFixtureImport": row.allows_fixture_import,
        "dataExportPolicy": dict(row.data_export_policy),
        "llmDataPolicy": row.llm_data_policy,
    }


def _connection_view(
    check: ConnectionCheck, *, environment_key: str, required_scopes: list[str]
) -> dict[str, Any]:
    return {
        "environmentKey": check.environment_key or environment_key,
        "ok": check.ok,
        "result": check.result or ("PASSED" if check.ok else "FAILED"),
        "dhis2Version": check.dhis2_version or check.version,
        "testedAt": check.tested_at,
        "requiredScopes": check.required_scopes or required_scopes,
        "checks": list(check.checks),
        "missingAuthorities": dict(check.missing_authorities),
        "capabilityProfileArtifactId": check.capability_profile_artifact_id,
        "message": check.message,
    }


def _capability_view(
    freshness: CapabilityFreshness, *, environment_key: str, include_content: bool
) -> dict[str, Any]:
    artifact_id = freshness.artifact_id or freshness.capability_profile_artifact_id
    return {
        "environmentKey": environment_key,
        "profileVersion": freshness.profile_version,
        "dhis2Version": freshness.dhis2_version or freshness.version,
        "probedAt": freshness.probed_at,
        "stale": bool(freshness.stale) if freshness.stale is not None else freshness.state != "VALID",
        "staleReason": freshness.stale_reason,
        "freshness": freshness.freshness or freshness.state,
        "artifactId": artifact_id,
        "contentHash": freshness.content_hash,
        # The CAPABILITY_PROFILE document format is a contract package S4 has not
        # published yet (S5IF-UP02), so the index layer is delivered and the body is
        # explicitly absent rather than invented.
        "content": freshness.content if include_content else None,
    }


def _discovery_view(row: DiscoveryResultInfo) -> dict[str, Any]:
    return {
        "artifactId": row.artifact_id,
        "projectId": row.project_id,
        "scope": row.scope,
        "objectCount": row.object_count,
        "blockingIssues": [issue.model_dump(by_alias=True) for issue in row.blocking_issues],
        "version": row.version,
        "discoveredAt": row.discovered_at,
        "contentHash": row.content_hash,
    }


def _deletion_view(
    row: ExecutionOutcome, *, environment_key: str, deletion_artifact_id: str
) -> dict[str, Any]:
    """S4's ``ExecutionOutcome`` on the wire, with the two ids the request supplied.

    ``status`` is upper-cased here rather than trusted: S1's mirror declares it as a ``str`` so
    that a provider enum cannot reach a comparison as its repr, and S5IF-AC02 makes UPPER_SNAKE
    a property of the wire instead of of whatever the provider happened to emit.
    """
    return {
        "environmentKey": row.environment_key or environment_key,
        "deletionArtifactId": deletion_artifact_id,
        "executionId": row.execution_id,
        "status": str(row.status).upper(),
        "applied": row.applied,
        "payloadHash": row.payload_hash,
        "reportArtifactId": row.report_artifact_id,
        "importReportArtifactId": row.import_report_artifact_id,
        "steps": list(row.steps),
        "issues": [issue.model_dump(by_alias=True) for issue in row.issues],
        # the refusal reason of a FAILED admission check lives only here (DX-EXE-038 returns an
        # outcome rather than raising), so dropping it would leave "why did nothing delete" unasked
        "detail": row.detail,
    }


def _audit_lock_change(
    runtime: Runtime, *, environment_key: str, locked: bool, reason: str | None, operator: str
) -> None:
    """S5IF-ENV07: the lock flip is auditable even though the state is S4's."""
    from factory_control.services.recording import write_audit

    with runtime.transaction(operator=operator) as ctx:
        write_audit(
            ctx,
            event_type="cp.environment.lock_changed",
            object_type="Environment",
            object_id=environment_key,
            after={"environmentKey": environment_key, "emergencyLocked": locked, "reason": reason},
        )


__all__ = [
    "clear_environment_class_cache",
    "environment_class",
    "capability_profile",
    "create_environment",
    "delete_objects",
    "disable_environment",
    "discovery_results",
    "expiring_credentials",
    "get_environment",
    "get_protection",
    "list_environments",
    "probe_capabilities",
    "set_emergency_lock",
    "test_connection",
    "update_environment",
]
