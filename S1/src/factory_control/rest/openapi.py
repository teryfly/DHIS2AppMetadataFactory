"""Document-level OpenAPI additions the routing layer cannot express (S5IF-G05 / G07 / G11).

Three things have to be written against the whole document rather than one operation:

* ``components.parameters`` — the cross-cutting headers are middleware behaviour, so
  no handler declares them.  Yet a generated client has to send ``X-Operator`` on every
  state-changing call or the request is rejected with 400 (CP-API-002).  Left
  undeclared, S5 would have to transcribe that by hand, which is exactly what
  UI-APP-007 is meant to remove.
* ``components.headers`` — the response headers the same middleware sets.  The names
  are the ones ``rest/middleware.py`` actually writes (S5IF-G05 【裁决】: the
  implementation is the baseline, the CTD's ``Idempotent-Replayed`` was never built).
* ``components.schemas.ErrorEnvelope`` — one typed body for every failure, with the
  per-code ``details`` fields declared (S5IF-G07.3), so ``details`` stops being an
  open object a generator can only type as ``Record<string, unknown>``.

``x-requirements`` on the other hand *is* per-operation and lives in
:mod:`factory_control.rest.route_meta`, next to the handler it describes.  It carries
S1's own ``CP-*`` ids and the gap document's ``S5IF-*`` ids in one generic field
(S5IF-G11 【裁决】: not an S5-specific tag), which the annotate pass below completes.
"""

from __future__ import annotations

from typing import Any

#: Requirement ids for the operations that existed before this document, keyed by
#: ``"METHOD /path/template"``.
#:
#: Declared here rather than on the handlers because these routes satisfy a *contract*
#: requirement (they were already routed and already answer with these fields) rather
#: than one of the new endpoint ids.  Anything not listed falls back to ``S5IF-G01``:
#: the operation was already there, what it lacked was a typed body.
LEGACY_REQUIREMENTS: dict[str, list[str]] = {
    "GET /api/v1/workflows/{workflow_id}/allowed-actions": ["S5IF-G10", "S5IF-FLD12"],
    "GET /api/v1/workflows/{workflow_id}/stages": ["S5IF-FLD01", "S5IF-FLD02"],
    "GET /api/v1/stages/{stage_id}": ["S5IF-FLD01", "S5IF-FLD02"],
    "POST /api/v1/stages/{stage_id}/run": ["S5IF-G06"],
    "POST /api/v1/stages/{stage_id}/rerun": ["S5IF-G06"],
    "POST /api/v1/stages/{stage_id}/patches": ["S5IF-FLD11"],
    "GET /api/v1/gates/{gate_id}": ["S5IF-FLD07"],
    "GET /api/v1/gates": ["S5IF-FLD03"],
    "GET /api/v1/workflows/{workflow_id}/gates": ["S5IF-FLD07"],
    "GET /api/v1/gates/{gate_id}/explanation": ["S5IF-G03", "S5IF-BP13"],
    "GET /api/v1/questions/{question_id}": ["S5IF-FLD08"],
    "GET /api/v1/workflows/{workflow_id}/questions": ["S5IF-FLD08"],
    "GET /api/v1/deployment-runs/{run_id}": ["S5IF-FLD06"],
    "GET /api/v1/deployment-runs/{run_id}/summary": ["S5IF-FLD05"],
    "GET /api/v1/deployment-runs": ["S5IF-FLD04"],
    "GET /api/v1/workflows/{workflow_id}/deployment-runs": ["S5IF-FLD06"],
    "GET /api/v1/projects/{project_id}/inputs": ["S5IF-INP02"],
    "GET /api/v1/settings": ["S5IF-FLD10"],
    "PUT /api/v1/settings": ["S5IF-FLD10"],
    "GET /api/v1/executions/{execution_id}": ["S5IF-G03"],
    "POST /api/v1/executions/{execution_id}/reconcile": ["S5IF-G03"],
    "GET /api/v1/audit/commit-trace/{execution_id}": ["S5IF-G03"],
    "GET /api/v1/audit": ["S5IF-G04"],
    "GET /api/v1/projects/{project_id}/audit": ["S5IF-G04"],
    "GET /api/v1/workflows/{workflow_id}/artifacts": ["S5IF-G04"],
    "GET /api/v1/projects": ["S5IF-G04"],
    "GET /api/v1/notifications": ["S5IF-G04"],
    "GET /api/v1/artifacts/{artifact_id}/content": ["S5IF-G05", "S5IF-FLD09"],
    "GET /api/v1/workflows/{workflow_id}/events": ["S5IF-G05"],
}

#: SRS 8.1's ``需求`` column, one entry per endpoint group.
#:
#: Those rows are the SRS naming the requirement each endpoint exists to satisfy, and
#: CP-API-010 ③ asks every operation to list exactly that in ``x-requirements``.  A key
#: ending in ``/*`` matches every path under it; the templates must match a route
#: character for character, which the contract test enforces.
GROUP_REQUIREMENTS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("/api/v1/projects/{project_id}/effective-policy", ("CP-UI-006",)),
    ("/api/v1/projects/{project_id}/target-environments", ("CP-PRJ-002", "CP-PRJ-003", "CP-PRJ-004")),
    ("/api/v1/projects/{project_id}/upgrade-mode", ("CP-PRJ-005",)),
    ("/api/v1/projects/{project_id}/archive", ("CP-PRJ-006",)),
    ("/api/v1/projects/{project_id}/inputs/*", ("CP-API-009", "CP-UI-007")),
    ("/api/v1/projects/{project_id}/workflow", ("CP-UI-002", "CP-UI-019")),
    ("/api/v1/workflows/{workflow_id}", ("CP-UI-002", "CP-UI-019")),
    ("/api/v1/workflows/{workflow_id}/pause", ("CP-WF-012",)),
    ("/api/v1/workflows/{workflow_id}/resume", ("CP-WF-012",)),
    ("/api/v1/workflows/{workflow_id}/cancel", ("CP-WF-011", "CP-WF-016")),
    ("/api/v1/workflows/{workflow_id}/allowed-actions", ("CP-UI-019",)),
    ("/api/v1/workflows/{workflow_id}/stages", ("CP-UI-008",)),
    ("/api/v1/stages/{stage_id}/*", ("CP-UI-009", "CP-UI-018")),
    ("/api/v1/workflows/{workflow_id}/agent-runs/*", ("CP-UI-007", "CP-WF-011")),
    ("/api/v1/deployment-runs", ("CP-UI-011",)),
    ("/api/v1/workflows/{workflow_id}/deployment-runs", ("CP-UI-011", "CP-WF-018")),
    ("/api/v1/deployment-runs/{run_id}", ("CP-UI-011",)),
    ("/api/v1/deployment-runs/{run_id}/summary", ("CP-UI-012",)),
    ("/api/v1/deployment-runs/{run_id}/drift-check", ("CP-UI-013",)),
    ("/api/v1/deployment-runs/{run_id}/commit", ("CP-API-014", "CP-GATE-017", "CP-GATE-018")),
    ("/api/v1/deployment-runs/{run_id}/cancel", ("CP-WF-011",)),
    ("/api/v1/deployment-runs/{run_id}/environment-bindings/*", ("CP-UI-002", "CP-UI-005")),
    ("/api/v1/executions/*", ("CP-WF-008", "CP-GATE-018")),
    ("/api/v1/questions/*", ("CP-UI-015",)),
    ("/api/v1/workflows/{workflow_id}/questions", ("CP-UI-015",)),
    ("/api/v1/stages/{stage_id}/questions", ("CP-UI-015",)),
    ("/api/v1/gates/*", ("CP-UI-010", "CP-UI-014")),
    ("/api/v1/workflows/{workflow_id}/gates", ("CP-UI-010",)),
    ("/api/v1/artifacts/*", ("CP-UI-016", "CP-UI-007")),
    ("/api/v1/workflows/{workflow_id}/artifacts", ("CP-UI-016",)),
    ("/api/v1/workflows/{workflow_id}/validation-issues", ("CP-UI-004",)),
    ("/api/v1/projects/{project_id}/blueprint/*", ("CP-UI-002", "CP-UI-003", "CP-API-016")),
    ("/api/v1/projects/{project_id}/decisions", ("CP-UI-003", "CP-API-016")),
    ("/api/v1/workflows/{workflow_id}/nrr", ("CP-UI-003", "CP-API-016")),
    ("/api/v1/projects/{project_id}/registry/*", ("CP-UI-002", "CP-API-016")),
    ("/api/v1/workflows/{workflow_id}/object-graph", ("CP-UI-020", "CP-API-016")),
    # deprecated transitional alias of the workflow-level graph (CP-UI-020); it shares
    # the same answer and disappears when no caller is left on the project-level path.
    ("/api/v1/projects/{project_id}/object-graph", ("CP-UI-020", "CP-API-016")),
    ("/api/v1/projects/{project_id}/frontend-package/*", ("CP-UI-021", "CP-API-016")),
    ("/api/v1/projects/{project_id}/package-files", ("CP-UI-002", "CP-API-016")),
    ("/api/v1/projects/{project_id}/uid-pool", ("CP-UI-002", "CP-API-016")),
    ("/api/v1/environments/*", ("CP-UI-001", "CP-API-015", "CP-API-016")),
    ("/api/v1/governance/*", ("CP-UI-006", "CP-API-015", "CP-API-016")),
    ("/api/v1/settings", ("CP-UI-017",)),
    ("/api/v1/upload-limits", ("CP-API-009",)),
)


def _group_ids(path: str) -> list[str]:
    out: list[str] = []
    for template, ids in GROUP_REQUIREMENTS:
        if template.endswith("/*"):
            matched = path == template[:-2] or path.startswith(template[:-1])
        else:
            matched = path == template
        if matched:
            for item in ids:
                if item not in out:
                    out.append(item)
    return out


#: ``S5IF-*`` id → the S1 requirement ids the operation implements (S5IF-G11 ②).
#:
#: Only ids this code base actually cites are attached, so the field stays useful as a
#: coverage index rather than a list of everything that sounds related.
REQUIREMENT_SOURCES: dict[str, list[str]] = {
    **{f"S5IF-ENV{i:02d}": ["CP-API-007"] for i in range(1, 13)},
    **{f"S5IF-BP{i:02d}": ["CP-API-007"] for i in range(1, 16)},
    **{f"S5IF-GOV{i:02d}": ["CP-API-007"] for i in range(1, 20)},
    **{f"S5IF-INP{i:02d}": ["CP-API-007"] for i in range(1, 8)},
    "S5IF-ENV06": ["CP-API-007", "CP-WF-027"],
    "S5IF-ENV07": ["CP-API-007", "CP-WF-027"],
    "S5IF-INP07": ["CP-API-007", "CP-WF-011"],
    "S5IF-G01": ["CP-API-001"],
    "S5IF-G02": ["CP-API-001"],
    "S5IF-G03": ["CP-API-001"],
    "S5IF-G04": ["CP-API-005"],
    "S5IF-G05": ["CP-API-002", "CP-API-003", "CP-API-006"],
    "S5IF-G06": ["CP-API-012", "CP-RT-003"],
    "S5IF-G07": ["CP-API-008", "CP-NFR-09"],
    "S5IF-G08": ["CP-API-003"],
    "S5IF-G09": ["CP-API-007"],
    "S5IF-G10": ["CP-WF-025", "CP-WF-026"],
    "S5IF-FLD01": ["CP-WF-006"],
    "S5IF-FLD02": ["CP-GATE-010"],
    "S5IF-FLD05": ["CP-NFR-03", "CP-UI-012", "CP-UI-013"],
    "S5IF-FLD06": ["CP-WF-021"],
    "S5IF-FLD07": ["CP-GATE-009", "CP-AC-04"],
    "S5IF-FLD08": ["CP-Q-001"],
    "S5IF-FLD10": ["CP-SET-001"],
    "S5IF-FLD11": ["CP-WF-029"],
    "S5IF-FLD12": ["CP-WF-025", "CP-WF-026"],
    "S5IF-INP02": ["CP-API-009"],
}

#: List operations, by the paging parameters they declare (S5IF-G04 / CP-API-005).
_PAGING_PARAMETERS = ("limit", "offset", "cursor", "nextCursor", "sortBy", "sortDir")

#: Every operation in this API may answer with the SRS 8.3 envelope.
ERROR_ENVELOPE: dict[str, Any] = {
    "type": "object",
    "required": ["errorCode", "message", "retryable", "correlationId", "details"],
    "properties": {
        "errorCode": {
            "type": "string",
            "description": "A code from S1's catalogue; see the message column of the same language.",
            "examples": ["OPERATOR_REQUIRED", "APPROVAL_INVALIDATED", "ENVIRONMENT_LOCKED"],
        },
        "message": {"type": "string", "description": "Localised by Accept-Language."},
        "stage": {"type": ["string", "null"], "description": "The pipeline stage that failed, when known."},
        "artifactId": {"type": ["string", "null"]},
        "retryable": {
            "type": "boolean",
            "description": "Whether the same request may be sent again unchanged.",
        },
        "correlationId": {"type": "string", "description": "Echoed as the X-Correlation-Id response header."},
        "details": {"$ref": "#/components/schemas/ErrorDetails"},
    },
}

#: The per-code shape of ``details`` (S5IF-G07.3), declared as one object with every
#: field S1 writes, each named after the code that puts it there.  A code not listed
#: here answers with ``{}``; a provider code that passed through keeps at least
#: ``providerErrorCode``.  ``details`` never carries a credential (S5IF-G09).
ERROR_DETAILS: dict[str, Any] = {
    "type": "object",
    "description": (
        "Machine-readable context, shape defined per errorCode.  All fields are "
        "optional: read only the ones documented for the code you received.  "
        "Never contains a secret value, a mask, a length or a prefix (S5IF-G09)."
    ),
    "properties": {
        "header": {
            "type": "string",
            "description": "OPERATOR_REQUIRED, IDEMPOTENCY_KEY_REQUIRED: the header that was missing.",
        },
        "fields": {
            "type": "array",
            "items": {"$ref": "#/components/schemas/ValidationErrorField"},
            "description": "VALIDATION_ERROR: one entry per field the request got wrong (SRS 8.3).",
        },
        "reason": {
            "type": "string",
            "enum": ["SIZE", "TYPE", "NOT_GENERATED", "NOT_EXIST"],
            "description": (
                "UPLOAD_REJECTED: which of the two limits the submission broke.  "
                "NOT_FOUND (CP-UI-022): NOT_GENERATED means a pipeline stage would "
                "produce the resource later — see ``requiredStage`` for which one; "
                "NOT_EXIST means the object truly is not there."
            ),
        },
        "requiredStage": {
            "type": "string",
            "description": (
                "NOT_FOUND with reason NOT_GENERATED: the pipeline stage whose output "
                "produces the resource, e.g. BASELINE_COMPILE, BIND_COMPILE or HANDOFF."
            ),
        },
        "violation": {
            "type": "string",
            "description": (
                "UPLOAD_REJECTED: the machine-readable cause behind ``reason`` — a size "
                "limit, or a media type that is absent or not allowed."
            ),
        },
        "requires": {
            "type": "string",
            "description": "INVALID_STATE: the precondition, e.g. ARCHIVED or PROJECT_EXPORT.",
        },
        "status": {"type": "string", "description": "INVALID_STATE: the current state of the object."},
        "currentState": {
            "type": "string",
            "description": "INVALID_STATE: the state the object is in now (SRS 8.3).",
        },
        "requiredStates": {
            "type": "array",
            "items": {"type": "string"},
            "description": (
                "INVALID_STATE: the states that would have allowed the action, empty when "
                "the refusal is not about a state machine at all."
            ),
        },
        "targetId": {
            "type": "string",
            "description": "INVALID_STATE: the object that is in the wrong state.",
        },
        "scope": {
            "type": "string",
            "description": (
                "IDEMPOTENCY_CONFLICT, REQUEST_IN_PROGRESS: the endpoint scope, or the "
                "step, the key or the in-flight action is held on."
            ),
        },
        "key": {"type": "string", "description": "IDEMPOTENCY_CONFLICT, REQUEST_IN_PROGRESS: the key."},
        "gateId": {"type": "string", "description": "APPROVAL_INVALIDATED: the Gate to reload."},
        "mismatches": {
            "type": "array",
            "description": "APPROVAL_INVALIDATED: each bound object whose hash moved.",
            "items": {"$ref": "#/components/schemas/ApprovalMismatch"},
        },
        "environmentKey": {
            "type": "string",
            "description": (
                "ENVIRONMENT_LOCKED, OUTSIDE_COMMIT_WINDOW, PRIOR_VERIFICATION_MISSING: "
                "the environment that refused."
            ),
        },
        "lockReason": {"type": "string", "description": "ENVIRONMENT_LOCKED: why the lock is on."},
        "lockChangedAt": {
            "type": "string",
            "description": "ENVIRONMENT_LOCKED: when the lock went on, ISO 8601.",
        },
        "windows": {
            "type": "array",
            "description": "OUTSIDE_COMMIT_WINDOW: the windows the environment accepts.",
            "items": {"$ref": "#/components/schemas/CommitWindow"},
        },
        "environmentTimezone": {
            "type": "string",
            "description": "OUTSIDE_COMMIT_WINDOW: timezone the windows are expressed in.",
        },
        "currentTime": {
            "type": "string",
            "description": "OUTSIDE_COMMIT_WINDOW: the environment's own clock, ISO 8601.",
        },
        "requiredEvidence": {
            "type": "array",
            "items": {"type": "string"},
            "description": "PRIOR_VERIFICATION_MISSING: what must be produced first.",
        },
        "blueprintVersion": {"type": "string"},
        "maxBytes": {"type": "integer", "description": "UPLOAD_REJECTED: the configured limit."},
        "allowedTypes": {
            "type": "array",
            "items": {"type": "string"},
            "description": "UPLOAD_REJECTED: the media types that pass.",
        },
        "mediaType": {"type": "string", "description": "UPLOAD_REJECTED: what was submitted."},
        "sizeBytes": {"type": "integer", "description": "UPLOAD_REJECTED: what was submitted."},
        "filename": {"type": "string", "description": "UPLOAD_REJECTED."},
        "providerErrorCode": {
            "type": "string",
            "description": (
                "A forwarded call whose provider code S1 reported under its own name: "
                "the code the provider actually raised (S5IF-G07.2)."
            ),
        },
        "capability": {"type": "string", "description": "AI_FEATURE_NOT_ENABLED: the refused capability."},
        "expectedType": {
            "type": "string",
            "description": (
                "INTERNAL_ERROR mapped from S2's ARTIFACT_TYPE_MISMATCH (CP-UI-021): the "
                "artifact type the forwarded read required."
            ),
        },
        "actualType": {
            "type": "string",
            "description": "Same pair as ``expectedType``: the type the artifact actually had.",
        },
        "feature": {
            "type": "string",
            "description": (
                "FEATURE_NOT_ENABLED (CP-UI-023): the appendix-B action whose writes are "
                "gated, e.g. member.manage."
            ),
        },
        "milestone": {
            "type": "string",
            "description": (
                "AI_FEATURE_NOT_ENABLED, FEATURE_NOT_ENABLED: the milestone that delivers it."
            ),
        },
        "known": {
            "type": "array",
            "items": {"type": "string"},
            "description": "AI_GOVERNANCE_ACTION_UNKNOWN: the actions that do exist.",
        },
        "questionId": {"type": "string"},
        "suggestionId": {"type": "string"},
        "stageId": {"type": "string"},
        "runId": {
            "type": "string",
            "description": (
                "NOT_FOUND on the dependency graph (CP-UI-022): the runId the request "
                "asked about, echoed back so the client can tell which Run was refused."
            ),
        },
        "workflowId": {"type": "string"},
        "projectId": {"type": "string"},
        "artifactId": {"type": "string"},
        "executionId": {"type": "string"},
        "changeRequestId": {"type": "string"},
        "retryable": {"type": "boolean"},
    },
}

_APPROVAL_MISMATCH: dict[str, Any] = {
    "type": "object",
    "required": ["refType", "refId", "expectedHash", "actualHash"],
    "properties": {
        "refType": {"type": "string", "description": "What kind of bound object moved."},
        "refId": {"type": "string", "description": "Which bound object moved."},
        "expectedHash": {
            "type": "string",
            "description": "The hash the Gate is bound to — the approval was given against this.",
        },
        "actualHash": {
            "type": ["string", "null"],
            "description": (
                "The other side of the comparison: the hash the page presented, or the hash "
                "the object carries now.  Null when the page never presented this object."
            ),
        },
        "problem": {
            "type": "string",
            "enum": ["NOT_PRESENTED", "STALE"],
            "description": "Why the two hashes do not agree.",
        },
    },
}

_VALIDATION_ERROR_FIELD: dict[str, Any] = {
    "type": "object",
    "required": ["path", "message"],
    "properties": {
        "path": {
            "type": "string",
            "description": "The submitted field, in the request body's own camelCase, dot-separated when nested.",
        },
        "message": {"type": "string", "description": "What is wrong with it."},
    },
}

_COMMIT_WINDOW: dict[str, Any] = {
    "type": "object",
    "required": ["days", "start", "end"],
    "properties": {
        "days": {
            "type": "array",
            "items": {"type": "integer", "minimum": 1, "maximum": 7},
            "description": "ISO weekdays, 1 = Monday.",
        },
        "start": {"type": "string", "description": "HH:MM in the environment's timezone."},
        "end": {"type": "string", "description": "HH:MM in the environment's timezone."},
    },
}


def _header(name: str, *, required: bool, description: str, schema: dict[str, Any]) -> dict[str, Any]:
    return {
        "name": name,
        "in": "header",
        "required": required,
        "description": description,
        "schema": schema,
    }


#: The two names a deployment actually uses, read from the settings rather than restated: the
#: document must say what the middleware sends and checks, and a hand-copied default is how a
#: contract ends up describing a cookie that no longer exists.
def _setting_default(name: str) -> str:
    from factory_control.config import Settings

    return str(Settings.model_fields[name].default)


def security_schemes() -> dict[str, Any]:
    """How a request proves who it is (CP-IAM-002, FR-SEC-001/005).

    Two schemes because one API serves both a browser and a machine: the session cookie is
    ``HttpOnly`` and unreadable to script, which is precisely why the CSRF twin is *not* — a page
    has to read that value to name the header, and the double-submit check still binds it to the
    session because only the digest of it is stored.
    """
    return {
        "sessionCookie": {
            "type": "apiKey",
            "in": "cookie",
            "name": _setting_default("cookie_name"),
            "description": (
                "Set by POST /auth/login or the OIDC exchange; HttpOnly, Secure and "
                "SameSite=Strict, so a client sends it by making the request at all rather than "
                "by naming it (FR-SEC-001/005)."
            ),
        },
        "csrfCookie": {
            "type": "apiKey",
            "in": "cookie",
            "name": _setting_default("csrf_cookie_name"),
            "description": (
                "The readable twin of the session: a page echoes it as "
                f"{_setting_default('csrf_header')} on a state-changing call.  Readable on "
                "purpose — without it a reloaded browser has a live session and no value to name, "
                "so every write would be refused (FR-SEC-005)."
            ),
        },
        "bearerToken": {
            "type": "http",
            "scheme": "bearer",
            "bearerFormat": "opaque",
            "description": (
                "``Authorization: Bearer <session token>`` for a client that is not a browser.  "
                "The token is the session value, so no CSRF header is needed: a header is not "
                "sent ambiently, which is the whole difference the cookie variant has to guard."
            ),
        },
    }


def security_requirements() -> list[dict[str, list[str]]]:
    """Either credential, per operation.  Separate objects mean logical OR in OpenAPI."""
    return [{"sessionCookie": []}, {"bearerToken": []}]


def _public_paths() -> frozenset[str]:
    """Paths that cannot require a session, because establishing one is their job.

    Read off the middleware rather than restated: ``SessionMiddleware`` is what decides whether a
    request is refused, and a second list here would let the document promise a sign-in on a route
    the guard already lets through — or demand a session on the login call itself.
    """
    from factory_control.rest.middleware import UNAUTHENTICATED_PATHS, SessionMiddleware

    return frozenset(SessionMiddleware.PUBLIC_PATHS) | frozenset(UNAUTHENTICATED_PATHS)


def components() -> dict[str, Any]:
    return {
        "parameters": {
            "XOperator": _header(
                "X-Operator",
                required=False,
                description=(
                    "Who is acting.  Not required on a read, but mandatory on every "
                    "POST/PUT/PATCH/DELETE: the middleware answers 400 OPERATOR_REQUIRED "
                    "without it (CP-API-002).  Required per operation is expressed by the "
                    "operation referencing this parameter, not by the flag here.\n\n"
                    "**Non-Latin-1 names are percent-encoded.**  A header value may only carry "
                    "ISO-8859-1, so a browser cannot send 张三 at all — it rejects the request "
                    "before it leaves the page.  A client that cannot fit the name sends "
                    "`encodeURIComponent(name)` and S1 decodes it back, which keeps a Chinese "
                    "operator name usable and the audit trail readable.  A name that already fits "
                    "travels unchanged, so an ASCII `curl -H 'X-Operator: ada'` is unaffected.  "
                    "A byte-transparent client such as curl may also send the name as UTF-8 "
                    "directly; the transport reads it as ISO-8859-1, and S1 re-reads those bytes "
                    "as UTF-8, so both spellings of 张三 land in the audit as 张三 (CP-API-002)."
                ),
                schema={"type": "string", "minLength": 1, "maxLength": 255},
            ),
            "XCsrfToken": _header(
                _setting_default("csrf_header"),
                required=False,
                description=(
                    "The value in the readable "
                    f"`{_setting_default('csrf_cookie_name')}` cookie, echoed back on every "
                    "state-changing call made with a cookie session.  Absent on a bearer call, "
                    "which is why it is not required: a header is not sent ambiently, and that "
                    "is the difference the cookie has to cover (FR-SEC-005).  A missing or "
                    "wrong value answers 403 CSRF_FAILED."
                ),
                schema={"type": "string", "minLength": 1},
            ),
            "XIdempotencyKey": _header(
                "Idempotency-Key",
                required=False,
                description=(
                    "Scoped to \"{METHOD} {route-template}\", so the same key on a "
                    "different endpoint is a different request.  A replay returns the "
                    "stored response byte-for-byte with X-Idempotent-Replay: true; the "
                    "same key with a different body answers 422 IDEMPOTENCY_CONFLICT, and "
                    "a key whose first request is still running answers 409 "
                    "REQUEST_IN_PROGRESS (CP-API-003).  This header is the only source of "
                    "the key: a request body never carries one (S5IF-G08)."
                ),
                schema={"type": "string", "minLength": 1, "maxLength": 200},
            ),
            "XAcceptLanguage": _header(
                "Accept-Language",
                required=False,
                description="zh-CN or en; anything else falls back to en.  Chooses the error message only.",
                schema={"type": "string", "enum": ["zh-CN", "en", "zh", "*"]},
            ),
            "XCorrelationId": _header(
                "X-Correlation-Id",
                required=False,
                description="Continued when supplied, generated otherwise, and always echoed back on the response.",
                schema={"type": "string"},
            ),
            "XTraceparent": _header(
                "traceparent",
                required=False,
                description="W3C trace context; continued when supplied, otherwise synthesised from the correlation id.",
                schema={"type": "string"},
            ),
        },
        "headers": {
            "XCorrelationId": {
                "description": "The correlation id of this call, on every response (CP-API-006).",
                "schema": {"type": "string"},
            },
            "XTraceparent": {
                "description": "The trace context of this call, on every response.",
                "schema": {"type": "string"},
            },
            "XIdempotentReplay": {
                "description": (
                    "\"true\" when this response is the stored first result of the same "
                    "Idempotency-Key rather than a fresh evaluation (CP-API-003).  The name "
                    "is the one rest/middleware.py writes; S5IF-G05 fixes the "
                    "implementation as the baseline."
                ),
                "schema": {"type": "string", "enum": ["true"]},
            },
            "XIdempotencyStored": {
                "description": "The id of the stored response row, on a request that was executed with a key.",
                "schema": {"type": "string"},
            },
        },
        "responses": {
            "Error": {
                "description": "The unified SRS 8.3 failure envelope.",
                "content": {"application/json": {"schema": ERROR_ENVELOPE}},
                "headers": {
                    "X-Correlation-Id": {"$ref": "#/components/headers/XCorrelationId"},
                    "traceparent": {"$ref": "#/components/headers/XTraceparent"},
                },
            }
        },
        "schemas": {
            "ErrorDetails": ERROR_DETAILS,
            "ApprovalMismatch": _APPROVAL_MISMATCH,
            "ValidationErrorField": _VALIDATION_ERROR_FIELD,
            "CommitWindow": _COMMIT_WINDOW,
        },
        "securitySchemes": security_schemes(),
    }


VERSION = "1.19.0"

_DESCRIPTION = (
    "S1 Factory Control Plane — the only public entry point of the DAMF Factory (S5 "
    "may call nothing else).  See docs/CHANGELOG.md for the change history; a "
    "breaking change raises the major version (S5IF-G11).\n\n"
    "**Naming.** Every field name on this wire is camelCase and every enum value is "
    "UPPER_SNAKE, including responses projected from a subsystem whose own DTOs are "
    "snake_case (S5IF-G03).  The one exception is a system setting *key*: keys are "
    "data, stay snake_case, and are declared as an enum (S5IF-FLD10).\n\n"
    "**Addressing.** `servers` is deliberately empty.  S5 is served from the same "
    "origin as S1 (UI-APP-008), so the deployment injects the base URL; the API "
    "prefix is `/api/v1`.  A contract test must not pin a host.\n\n"
    "**Pagination.** `{items, limit, total?, offset?, nextCursor?}` (S5IF-G04).  "
    "Ordinary lists page by `offset` and sort by `sortBy`+`sortDir`; audit, artifact "
    "and event lists page by an opaque `nextCursor` and omit `total` when counting "
    "would cost more than the page.  `nextCursor` is null on the last page and must "
    "not be parsed by a client.\n\n"
    "**Errors.** One envelope, one catalogue (S5IF-G07): a provider code that means "
    "the same as an S1 code is reported as the S1 code with the original in "
    "`details.providerErrorCode`; a provider code with a meaning of its own that the "
    "provider registered upstream passes through unchanged.  `details` never contains "
    "a credential, a mask, a length or a prefix (S5IF-G09).  A derived resource the "
    "pipeline has not produced yet — a dependency graph, a development package — is "
    "404 `NOT_FOUND` with `details.reason=NOT_GENERATED` and the `requiredStage` that "
    "would produce it; that is guidance to finish a stage, never a 200 with a "
    "no-content marker (CP-UI-022).\n\n"
    "**Traceability.** `x-requirements` on each operation lists the S1 `CP-*` ids and "
    "the `S5IF-*` ids it satisfies (S5IF-G11)."
)

_HEADER_FOR_METHOD = {"post", "put", "patch", "delete"}
_ALL_HEADER_REFS = ("XAcceptLanguage", "XCorrelationId", "XTraceparent")
_ALWAYS = ["CP-API-001", "CP-API-008", "CP-API-010", "CP-API-011"]
_ERROR_HEADERS = ("X-Correlation-Id", "traceparent")
_IDEMPOTENT_HEADERS = ("X-Idempotent-Replay", "X-Idempotency-Stored")


def _referenced(operation: dict[str, Any]) -> set[str]:
    return {
        str(p.get("$ref", "")).split("/")[-1]
        for p in (operation.get("parameters") or [])
        if isinstance(p, dict)
    }


def _honours_idempotency(operation: dict[str, Any]) -> bool:
    """Whether this operation takes ``Idempotency-Key`` (CP-API-003).

    Read off the operation instead of a hand-kept list: the route is what decides, and a
    register here would silently leave a new endpoint without the replay headers the
    middleware already sets on its responses (S5IF-G05).
    """
    refs = _referenced(operation)
    if "XIdempotencyKey" in refs:
        return True
    return any(
        isinstance(p, dict)
        and p.get("in") == "header"
        and str(p.get("name", "")).lower() == "idempotency-key"
        for p in (operation.get("parameters") or [])
    )


def _parameters(operation: dict[str, Any]) -> set[str]:
    return {
        str(p.get("name", ""))
        for p in (operation.get("parameters") or [])
        if isinstance(p, dict) and p.get("in") == "query"
    }


def _requirement_ids(key: str, operation: dict[str, Any]) -> list[str]:
    """The ids one operation satisfies: S1's own, then the gap document's (G11 ②)."""
    declared = list(operation.get("x-requirements") or []) or list(LEGACY_REQUIREMENTS.get(key, ["S5IF-G01"]))
    method, _, path = key.partition(" ")
    ids: list[str] = list(_ALWAYS)
    if method.lower() in _HEADER_FOR_METHOD:
        ids.append("CP-API-002")
    if _honours_idempotency(operation):
        ids.append("CP-API-003")
    if _parameters(operation) & set(_PAGING_PARAMETERS):
        ids.append("CP-API-005")
    for group in _group_ids(path):
        if group not in ids:
            ids.append(group)
    for extra in _extras(declared):
        if extra not in ids:
            ids.append(extra)
    for tag in declared:
        if tag not in ids:
            ids.append(tag)
    return ids


def _extras(declared: list[str]) -> list[str]:
    out: list[str] = []
    for tag in declared:
        for cp in REQUIREMENT_SOURCES.get(tag, []):
            if cp not in out:
                out.append(cp)
    return out


def _key_honoured(method: str, path: str) -> bool:
    """Whether the middleware would honour an idempotency key on this operation (CP-API-003).

    The answer is read from the middleware's own sets rather than restated here: a second list
    is how an endpoint ends up honoured on the wire but undeclared in the contract, which is a
    client that never sends the key — and a duplicate create is exactly what the key exists for.
    """
    from factory_control.rest.middleware import (
        IDEMPOTENT_METHODS,
        NON_IDEMPOTENT_PATH_FRAGMENTS,
    )

    if method.upper() not in IDEMPOTENT_METHODS:
        return False
    return not any(fragment in path for fragment in NON_IDEMPOTENT_PATH_FRAGMENTS)


def annotate_operations(document: dict[str, Any]) -> None:
    """Give every operation its ``x-requirements`` ids, headers and header references.

    FastAPI cannot express any of this from a handler: the headers are middleware
    behaviour and the requirement ids are metadata.  Adding them here rather than on
    a hundred decorators keeps one place where the rule is stated.
    """
    for path, item in document.get("paths", {}).items():
        for method, operation in item.items():
            if not isinstance(operation, dict):
                continue
            if not path.startswith("/api/v1"):
                # ``/healthz`` is an ops endpoint, not part of the S5 contract.
                continue
            key = f"{method.upper()} {path}"
            refs = _referenced(operation)
            wanted = [f"#/components/parameters/{name}" for name in _ALL_HEADER_REFS if name not in refs]
            if _key_honoured(method, path) and "XIdempotencyKey" not in refs:
                wanted.insert(0, "#/components/parameters/XIdempotencyKey")
            if method in _HEADER_FOR_METHOD and "XOperator" not in refs:
                wanted.insert(0, "#/components/parameters/XOperator")
            # The CSRF value belongs on exactly the methods the middleware checks it on, and on
            # no read: declaring it wider would promise a header a bearer call never needs.
            if method in _HEADER_FOR_METHOD and "XCsrfToken" not in refs:
                wanted.insert(0, "#/components/parameters/XCsrfToken")
            if wanted:
                operation.setdefault("parameters", []).extend({"$ref": ref} for ref in wanted)
            if path in _public_paths():
                operation["security"] = []
            operation["x-requirements"] = _requirement_ids(key, operation)
            _attach_error_responses(operation)
            _attach_response_headers(operation, idempotent=_honours_idempotency(operation))


#: The failures every operation can answer with, because they come from the layers every
#: request passes rather than from the handler: the middleware's 400 (a state-changing call
#: without ``X-Operator``), the session guard's 401 and the authorization hook's 403 (both run
#: before any service does, so no operation is exempt while identity is configured), S1's own
#: 404 / 409 judgements, and 422 for a body or query parameter that does not validate
#: (CP-API-008, SRS 8.3).
_UNIVERSAL_ERRORS = ("400", "401", "403", "404", "409", "422")
_ERROR_REF = {"$ref": "#/components/responses/Error"}

#: A group that reaches a provider can fail in the provider's three ways: a refusal the
#: provider owns (502), a provider S1 cannot reach at all (503), and a provider that did not
#: answer in time (504) — ``errors.ERROR_HTTP_STATUS`` maps ``LLM_TIMEOUT`` and
#: ``DHIS2_TIMEOUT`` to the last of these.  Which groups forward is stated by the route
#: module's own ``responses=`` list; the set is completed here so a group cannot promise
#: "unreachable" while forgetting "slow" — which is why a retrying client is never built.
_PROVIDER_ERRORS = ("502", "503", "504")

#: The two components FastAPI writes for a request it could not even parse.  S1 answers
#: that case with the SRS 8.3 envelope too (CP-API-008), so neither belongs in the
#: published contract — an operation describing them would promise a body that never comes.
_FASTAPI_FAILURE_SCHEMAS = ("HTTPValidationError", "ValidationError")


def _is_fastapi_failure(entry: Any) -> bool:
    schema = (((entry or {}).get("content") or {}).get("application/json") or {}).get("schema") or {}
    return str(schema.get("$ref", "")).split("/")[-1] in _FASTAPI_FAILURE_SCHEMAS


def _attach_error_responses(operation: dict[str, Any]) -> None:
    """Point every operation at the one shared error component.

    Handlers declare their own subset with ``responses=``; the universal codes are added here
    so a generated client can type the failure of *any* call, rather than learning by trial
    that ``GET /projects/{id}`` can also answer 409.  A code FastAPI filled in with its own
    validation body is rewritten, because that is not what S1 sends.
    """
    responses = operation.setdefault("responses", {})
    for code in _UNIVERSAL_ERRORS:
        if code not in responses or _is_fastapi_failure(responses[code]):
            responses[code] = dict(_ERROR_REF)
        elif "$ref" in responses[code]:
            # FastAPI merges its own ``description`` into a declared reference; a sibling
            # next to ``$ref`` is ignored by 3.0 tooling, so the reference stands alone.
            responses[code] = {"$ref": responses[code]["$ref"]}
    _complete_provider_errors(responses)


def _complete_provider_errors(responses: dict[str, Any]) -> None:
    """Give a forwarding operation all three provider failures, not the one it thought of."""
    if not any(code in responses for code in _PROVIDER_ERRORS):
        return
    for code in _PROVIDER_ERRORS:
        if code not in responses or _is_fastapi_failure(responses[code]):
            responses[code] = dict(_ERROR_REF)


def _attach_response_headers(operation: dict[str, Any], *, idempotent: bool) -> None:
    """Say on the operation what the middleware sets on the response (S5IF-G05)."""
    refs = {
        "X-Correlation-Id": "#/components/headers/XCorrelationId",
        "traceparent": "#/components/headers/XTraceparent",
        "X-Idempotent-Replay": "#/components/headers/XIdempotentReplay",
        "X-Idempotency-Stored": "#/components/headers/XIdempotencyStored",
    }
    for status, response in (operation.get("responses") or {}).items():
        if not isinstance(response, dict) or "content" not in response:
            # A shared-error ``$ref`` carries its own headers; only inline bodies are
            # annotated here.
            continue
        declared = response.setdefault("headers", {})
        for name in _ERROR_HEADERS:
            declared.setdefault(name, {"$ref": refs[name]})
        if idempotent and status.startswith("2"):
            for name in _IDEMPOTENT_HEADERS:
                declared.setdefault(name, {"$ref": refs[name]})


def augment(document: dict[str, Any]) -> dict[str, Any]:
    """Add the shared components, the version and the traceability tags.  Idempotent."""
    info = document.setdefault("info", {})
    info["version"] = VERSION
    info["description"] = _DESCRIPTION
    # G11 ③: empty on purpose.  The deployment injects the origin; S5 and S1 are
    # same-origin, so pinning one here would be a lie in every other environment.
    document["servers"] = []
    # Every operation needs a session unless it says otherwise (FR-SEC-001: "all APIs require
    # authentication").  Stated once at the top and cleared per operation on the public paths,
    # because FastAPI cannot express either from a handler.
    document["security"] = security_requirements()
    shared = components()
    comps = document.setdefault("components", {})
    for key, value in shared.items():
        if key == "schemas":
            continue
        target = comps.setdefault(key, {})
        target.update(value)
    comps.setdefault("schemas", {}).update(shared["schemas"])
    annotate_operations(document)
    _drop_unreferenced_failure_components(document)
    return document


def _drop_unreferenced_failure_components(document: dict[str, Any]) -> None:
    """Leave no component behind that nothing points at any more."""
    schemas = (document.get("components") or {}).get("schemas") or {}
    referenced = {
        str(node["$ref"]).split("/")[-1]
        for node in _refs(document.get("paths", {}))
    }
    for name in _FASTAPI_FAILURE_SCHEMAS:
        if name in schemas and name not in referenced:
            schemas.pop(name)


def _refs(node: Any) -> list[dict[str, Any]]:
    if isinstance(node, dict):
        found = [node] if "$ref" in node else []
        for value in node.values():
            found += _refs(value)
        return found
    if isinstance(node, list):
        out: list[dict[str, Any]] = []
        for value in node:
            out += _refs(value)
        return out
    return []


__all__ = ["VERSION", "augment", "components", "security_requirements", "security_schemes"]
