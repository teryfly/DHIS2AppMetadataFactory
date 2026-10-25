"""S1 error types and the REST status mapping (CP-API-008, SRS 8.3).

Every error S1 raises is a :class:`factory_foundation.api.FactoryError`, so the
foundation's ``errorCode`` / ``retryable`` / ``stage`` / ``artifactId`` / ``details``
shape is already right; this module adds the codes S1 itself judges and the
single table that maps any code to an HTTP status.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from factory_control.foundation import FactoryError

#: The two headers a request may be refused for lacking (SRS 8.3 ``OPERATOR_REQUIRED``
#: and S5IF-G08).  Named here because the refusal says the header in ``details`` and the
#: middleware reads it from the same place.
OPERATOR_HEADER = "X-Operator"
IDEMPOTENCY_HEADER = "Idempotency-Key"

# --- S1-owned error codes (SRS 8.3 【新增】 list) ----------------------------

OPERATOR_REQUIRED = "OPERATOR_REQUIRED"
IDEMPOTENCY_KEY_REQUIRED = "IDEMPOTENCY_KEY_REQUIRED"
REQUEST_IN_PROGRESS = "REQUEST_IN_PROGRESS"
#: A parse thread died with the process; the run is over and a new click may start one.
PARSE_INTERRUPTED = "PARSE_INTERRUPTED"
INVALID_STATE = "INVALID_STATE"
PROJECT_ARCHIVED = "PROJECT_ARCHIVED"
BUDGET_EXCEEDED = "BUDGET_EXCEEDED"
ENVIRONMENT_LOCKED = "ENVIRONMENT_LOCKED"
OUTSIDE_COMMIT_WINDOW = "OUTSIDE_COMMIT_WINDOW"
PRIOR_VERIFICATION_MISSING = "PRIOR_VERIFICATION_MISSING"
UPLOAD_REJECTED = "UPLOAD_REJECTED"
#: Codes the foundation already owns that S1 answers with, named here because the
#: per-code ``details`` shape is written against them (SRS 8.3).
VALIDATION_ERROR = "VALIDATION_ERROR"
IDEMPOTENCY_CONFLICT = "IDEMPOTENCY_CONFLICT"
ARTIFACT_PURGED = "ARTIFACT_PURGED"
PROVIDER_UNAVAILABLE = "PROVIDER_UNAVAILABLE"
#: CP-UI-023: a milestone feature whose endpoints exist but whose writes are gated.
FEATURE_NOT_ENABLED = "FEATURE_NOT_ENABLED"
#: The foundation's catch-all, named here because a background parse records it on its run.
INTERNAL_ERROR = "INTERNAL_ERROR"
#: CP-SEC-003: a credential some environment or provider still points at cannot be dropped.
SECRET_IN_USE = "SECRET_IN_USE"
#: CP-SEC-001: the foundation's own code, reused because "no store to read from" and "no value
#: behind this reference" are the same answer to a client — 503, and `details.reason` says which of
#: the two. Not marked retryable: nothing a client retries can change a deployment's configuration,
#: and a `reason` of `keyMismatch` in particular means the answer will not improve by itself.
SECRET_UNAVAILABLE = "SECRET_UNAVAILABLE"

#: code -> (retryable).  Codes not listed here come from the foundation catalog.
S1_ERROR_RETRYABLE: dict[str, bool] = {
    OPERATOR_REQUIRED: False,
    IDEMPOTENCY_KEY_REQUIRED: False,
    REQUEST_IN_PROGRESS: True,
    PARSE_INTERRUPTED: True,
    INVALID_STATE: False,
    PROJECT_ARCHIVED: False,
    BUDGET_EXCEEDED: False,
    # S4 owns this code and says a retry will not help: an emergency lock is a human state that
    # only a person lifts.  Claiming True here would have S1's table disagree with the catalog
    # the Temporal engine actually consults (engine/temporal.py).
    ENVIRONMENT_LOCKED: False,
    OUTSIDE_COMMIT_WINDOW: True,
    PRIOR_VERIFICATION_MISSING: False,
    UPLOAD_REJECTED: False,
    "NOT_FOUND": False,
    "IDEMPOTENCY_CONFLICT": False,
    "GATE_NOT_APPROVED": False,
    "APPROVAL_INVALIDATED": False,
    "DRIFT_DETECTED": False,
    "VALIDATION_ERROR": False,
    "SCHEMA_ERROR": False,
    "ARTIFACT_ERROR": False,
    "REGISTRY_ERROR": False,
    "POLICY_DENIED": False,
    "CANCELLED": False,
    "PERMISSION_DENIED": False,
    "UNAUTHENTICATED": False,
    "CSRF_FAILED": False,
    "TRANSACTION_MISUSE": False,
    "ARTIFACT_PURGED": False,
    "CONCURRENCY_ERROR": True,
    "INTERNAL_ERROR": False,
    "LLM_TIMEOUT": True,
    "LLM_ERROR": False,
    "DHIS2_TIMEOUT": True,
    "DHIS2_AUTH_ERROR": False,
    "DHIS2_PERMISSION_ERROR": False,
    "DHIS2_NOT_FOUND": False,
    "DHIS2_IMPORT_ERROR": False,
    # FD-COM-001a (S0 V1.0.1): a missing secret is not retryable — the reference must be
    # fixed, not re-attempted; the catalog's claim is the one clients see.
    "SECRET_UNAVAILABLE": False,
    "MIGRATION_ERROR": False,
    PROVIDER_UNAVAILABLE: True,
    FEATURE_NOT_ENABLED: False,
    SECRET_IN_USE: False,
}

#: code -> HTTP status.  Unknown codes fall back to 500 (CP-API-008: never leak).
ERROR_HTTP_STATUS: dict[str, int] = {
    OPERATOR_REQUIRED: 400,
    IDEMPOTENCY_KEY_REQUIRED: 400,
    "VALIDATION_ERROR": 422,
    "SCHEMA_ERROR": 422,
    "ARTIFACT_ERROR": 422,
    "REGISTRY_ERROR": 422,
    "UPLOAD_REJECTED": 422,
    "IDEMPOTENCY_CONFLICT": 422,
    BUDGET_EXCEEDED: 422,
    "POLICY_DENIED": 403,
    "NOT_FOUND": 404,
    INVALID_STATE: 409,
    PROJECT_ARCHIVED: 409,
    REQUEST_IN_PROGRESS: 409,
    PARSE_INTERRUPTED: 409,
    "GATE_NOT_APPROVED": 409,
    "APPROVAL_INVALIDATED": 409,
    "DRIFT_DETECTED": 409,
    "CONCURRENCY_ERROR": 409,
    ENVIRONMENT_LOCKED: 409,
    OUTSIDE_COMMIT_WINDOW: 409,
    PRIOR_VERIFICATION_MISSING: 409,
    "CANCELLED": 409,
    FEATURE_NOT_ENABLED: 409,
    SECRET_IN_USE: 409,
    "LLM_TIMEOUT": 504,
    "DHIS2_TIMEOUT": 504,
    "LLM_ERROR": 502,
    "DHIS2_AUTH_ERROR": 502,
    "DHIS2_PERMISSION_ERROR": 502,
    "DHIS2_NOT_FOUND": 502,
    "DHIS2_IMPORT_ERROR": 502,
    "SECRET_UNAVAILABLE": 503,
    "PERMISSION_DENIED": 403,
    "UNAUTHENTICATED": 401,
    "CSRF_FAILED": 403,
    "ARTIFACT_PURGED": 410,
    "TRANSACTION_MISUSE": 500,
    "MIGRATION_ERROR": 500,
    INTERNAL_ERROR: 500,
    PROVIDER_UNAVAILABLE: 503,
}

DEFAULT_HTTP_STATUS = 500

#: Provider codes that reach S1 through a forwarded call and must not become 500.
#:
#: SRS 8.3 gives S1 the envelope, so an unmapped provider code would fall through to
#: ``DEFAULT_HTTP_STATUS`` and read as an S1 crash.  Only the codes the rule below
#: lets through are listed here: a provider code that *duplicates* an S1 one is
#: rewritten by :data:`PROVIDER_CODE_ALIASES` and therefore never reaches this table.
PROVIDER_ERROR_HTTP_STATUS: dict[str, int] = {
    "AI_GOVERNANCE_ACTION_UNKNOWN": 422,
    "AI_PROMPT_ACTIVATION_BLOCKED": 409,
    # AI-REG-002: the agent's prompt was never activated — a deployment step left undone,
    # which is a state refusal (the stage can still be authored by hand), not a crash.
    "AI_PROMPT_NOT_ACTIVATED": 409,
    "AI_POLICY_RELAXATION_DENIED": 403,
    "AI_FEATURE_NOT_ENABLED": 409,
    # AI-COST-002: a budget statement S3 refuses to store half-obeyed
    "AI_BUDGET_INVALID": 422,
    "AI_EVAL_SET_MISSING": 404,
    "AI_PROVIDER_UNAVAILABLE": 502,
    # A model whose answer cannot be used is the upstream's failure to answer well, not a
    # defect in the caller's request — 502 says "the model did, and it was not usable".
    "AI_OUTPUT_INVALID": 502,
    # Both are about the bytes S3 was handed: too much of them, or in a shape its
    # parser cannot read. Nothing about the deployment has to change for a retry to work.
    "AI_CONTEXT_TOO_LARGE": 422,
    "SCANNED_DOCUMENT_UNSUPPORTED": 422,
    "AI_PATCH_REJECTED": 422,
    # A parsed document that cannot be read, or a file no parser claims: the upload is the
    # problem, and S4's own report says which. ``UPLOAD_REJECTED`` would lose that.
    "AI_PARSER_INPUT_INVALID": 422,
    "AI_PARSER_UNMATCHED": 422,
    # The answer conformed to its Schema and broke an agent-level prohibition (AI-RT-012). The
    # runtime treats it as a schema failure, so the status says the same thing S5 already
    # renders for one.
    "AI_CONSTRAINT_VIOLATION": 422,
    # A parser library that is not installed is a capability the server does not have right
    # now (the OTel case in user-guide §3.4 is the same shape), not a rejected request.
    "AI_PARSER_DEPENDENCY_MISSING": 503,
    "BP_UNSUPPORTED_SCHEMA_VERSION": 422,
    "BP_FEATURE_NOT_ENABLED": 409,
    "BP_IMPORT_CONFLICT": 409,
    "BP_DRAFT_NOT_MATERIALIZED": 409,
    "UID_POOL_EXHAUSTED": 409,
    # S2 needs a grammar module to compile an expression at all: like the parser above, the
    # answer is "this server cannot do that yet", never "your request was wrong".
    "BP_EXPRESSION_GRAMMAR_MISSING": 503,
    # --- S4: the instance is the upstream ------------------------------------
    # The DHIS2 node could not be reached, answered something unparseable, or its import task
    # vanished from the tracker. All three are the same fact wearing different clothes: S1 did
    # not get a usable answer from the environment, and pressing the button again is a
    # reasonable thing to try later — which is exactly what 502 means here.
    "DHIS2_UNREACHABLE": 502,
    "DHIS2_RESPONSE_INVALID": 502,
    "DHIS2_TASK_LOST": 502,
    # The instance received the import and rejected it. The payload is the problem, so the
    # status points back at the request rather than at the network.
    "DHIS2_TASK_FAILED": 422,
    # TLS that does not match the environment's own requirement (DX-CLI-006) is a configuration
    # state; a retry reaches the same certificate. It changes when an operator edits the
    # environment, which is what 409 is for.
    "DHIS2_TLS_INVALID": 409,
    # Both are the Profile layer refusing to speak to an instance it cannot classify: the
    # version is outside what S4 supports, or the capability record it would build on is
    # older than the instance. Re-probing or registering the right Profile is the fix.
    "DHIS2_VERSION_UNSUPPORTED": 409,
    "DHIS2_PROFILE_STALE": 409,
    # A protection rule said no. These are not input mistakes and not state drift — they are
    # the environment's own guard (DX-ENV-002's FORBID deletion, production protection), so
    # the answer is a refusal the operator can read as one: 403 with S4's details.
    "DX_PRODUCTION_PROTECTED": 403,
    "DX_DELETION_FORBIDDEN": 403,
    # The three-way payload hash (validate / gate / current package) disagreed at commit time.
    # Adjacent to ``DRIFT_DETECTED``, which S1 owns, but the reason and the remedy are S4's, so
    # the code stays distinct and answers 409 like its neighbour.
    "DX_PAYLOAD_HASH_MISMATCH": 409,
    # A validation record older than the package being committed: the workflow has moved, and
    # the operator has to validate again. Nothing about the request itself was wrong.
    "DX_VALIDATION_STALE": 409,
}

#: A provider code that means the same thing as an S1 code is reported as the S1
#: code (S5IF-G07.2): S5 maps one catalogue, and ``details`` keeps what the provider
#: actually said.
#:
#: The rule has two halves, and this table is the first one.  The second half — a
#: provider code with a meaning of its own that the provider has registered in S0's
#: error catalogue — stays as it is, because S5 has to render it distinctly:
#: ``AI_PROMPT_ACTIVATION_BLOCKED`` is what makes the Prompt page say "a passing
#: evaluation or regression record is missing" (S5IF-GOV09).  A code that is neither
#: aliased here nor registered upstream must not be let through at all.
PROVIDER_CODE_ALIASES: dict[str, str] = {
    "DX_OUTSIDE_COMMIT_WINDOW": OUTSIDE_COMMIT_WINDOW,
    "DX_ENVIRONMENT_IN_USE": INVALID_STATE,
    # An environment that is DISABLED, or whose profile S4 will not accept, is refused for the
    # same reason as an environment in use: the state of the resource says no. This is the one
    # S4 raises from ``build_target`` for every operation against a disabled environment, so
    # leaving it unaliased turned "test a disabled environment" into an S1 crash.
    "DX_ENVIRONMENT_INVALID": INVALID_STATE,
    # S4 orders its own steps; disobeying the order is a state refusal, exactly what
    # ``INVALID_STATE`` already means to S5.
    "DX_EXECUTION_ORDER": INVALID_STATE,
    # A milestone gate owned by S1's vocabulary (CP-UI-023): S4 has the same meaning under a
    # different name, and S5 renders one code.
    "DX_FEATURE_NOT_ENABLED": FEATURE_NOT_ENABLED,
    # A UID S4 will not send is a request-level defect with a field in ``details``, the same
    # shape as ``BP_PATCH_MALFORMED`` below.
    "DX_UID_FORMAT_INVALID": "SCHEMA_ERROR",
    # The stored artifact S1 handed to S4 is not what its own index says it is. S4 cannot know
    # that; ``ARTIFACT_ERROR`` is S1's name for an artifact-level failure.
    "DX_INPUT_ARTIFACT_INVALID": "ARTIFACT_ERROR",
    # A discovery scope outside what S4 collects is a refused request, not a broken resource.
    "DX_DISCOVERY_SCOPE_INVALID": "VALIDATION_ERROR",
    "AI_BUDGET_EXCEEDED": BUDGET_EXCEEDED,
    "AI_IN_PROGRESS": REQUEST_IN_PROGRESS,
    "AI_PROMPT_NOT_FOUND": "NOT_FOUND",
    "AI_RUN_NOT_RESUMABLE": INVALID_STATE,
    "AI_CONTEXT_CONTAINS_PHI": "POLICY_DENIED",
    "BP_IN_PROGRESS": REQUEST_IN_PROGRESS,
    "BP_PATCH_MALFORMED": "SCHEMA_ERROR",
    "BP_NOT_PROMOTABLE": INVALID_STATE,
    # CP-UI-021: an ARTIFACT_TYPE_MISMATCH from S2 on a dev-package read means S1
    # handed S2 an artifact of the wrong type — a defect in S1's own resolution, so it
    # answers as S1's internal failure (with the provider code kept in details), never
    # as a missing resource the user could wait out.
    "ARTIFACT_TYPE_MISMATCH": "INTERNAL_ERROR",
}


def http_status_for(error_code: str) -> int:
    """Status for an error: S1's table, then the provider table, else 500.

    An aliased provider code resolves through its S1 name first, so a caller that
    forgets :func:`normalize_provider_error` still answers 409 rather than turning a
    provider's refusal into an S1 crash.
    """
    resolved = PROVIDER_CODE_ALIASES.get(error_code, error_code)
    if resolved in ERROR_HTTP_STATUS:
        return ERROR_HTTP_STATUS[resolved]
    return PROVIDER_ERROR_HTTP_STATUS.get(resolved, DEFAULT_HTTP_STATUS)


def normalize_provider_error(error: FactoryError) -> FactoryError:
    """Rewrite a provider code that duplicates an S1 one, keeping the original in details.

    Returns the same instance when nothing is aliased, so callers can use the result
    unconditionally.
    """
    target = PROVIDER_CODE_ALIASES.get(error.error_code)
    if target is None or target == error.error_code:
        return error
    details = dict(error.details or {})
    details.setdefault("providerErrorCode", error.error_code)
    return FactoryError(
        error.message,
        code=target,
        stage=error.stage,
        artifact_id=error.artifact_id,
        retryable=error.retryable,
        details=details,
    )


# --- Typed errors ----------------------------------------------------------


def _shaped(details: dict[str, Any], **added: Any) -> dict[str, Any]:
    """``details`` with the SRS 8.3 keys added, never replacing what the site wrote.

    A refusal site already names the object and its state in its own words; the shaped
    keys exist so S5 can render *any* refusal of a code from one contract (S5IF-G07.3),
    while the site-specific keys stay for whoever needs the precise field.
    """
    merged = dict(details)
    for key, value in added.items():
        if value is not None:
            merged.setdefault(key, value)
    return merged


#: Detail keys that already name the state an object is in, or the object itself.
_CURRENT_STATE_KEYS = ("currentState", "status", "stageStatus", "runStatus", "gateStatus")
_TARGET_ID_KEYS = (
    "targetId",
    "stageId",
    "runId",
    "deploymentRunId",
    "gateId",
    "workflowId",
    "projectId",
    "questionId",
    "changeRequestId",
    "artifactId",
    "executionId",
    "environmentKey",
)


def _first_string(details: dict[str, Any], keys: tuple[str, ...]) -> str | None:
    for key in keys:
        value = details.get(key)
        if isinstance(value, str) and value:
            return value
    return None


#: :meth:`UploadPolicy.violation` -> the SRS 8.3 ``UPLOAD_REJECTED`` reason.
_UPLOAD_REASONS = {
    "SIZE_EXCEEDED": "SIZE",
    "MEDIA_TYPE_MISSING": "TYPE",
    "MEDIA_TYPE_NOT_ALLOWED": "TYPE",
}


def field_entries(details: dict[str, Any], fallback: str) -> list[dict[str, str]]:
    """Coerce what a site listed as its complaints into ``{path, message}`` (SRS 8.3).

    Sites arrive with different habits — a list of sentences, a list of dicts, one field
    name — and a generated client should not have to know which.
    """
    listed = details.get("fields") or details.get("problems") or []
    items = listed if isinstance(listed, list) else [listed]
    out: list[dict[str, str]] = []
    for item in items:
        if isinstance(item, dict):
            path = str(item.get("path") or item.get("field") or item.get("loc") or "")
            message = str(item.get("message") or item.get("msg") or item.get("error") or "")
        else:
            path, message = "", str(item)
        if message:
            out.append({"path": path, "message": message})
    if not out:
        out = [{"path": str(details.get("field") or ""), "message": str(details.get("error") or fallback)}]
    return out


class ControlPlaneError(FactoryError):
    """Base of every error raised from S1 business logic."""


class OperatorRequiredError(ControlPlaneError):
    """CP-API-002: a state-changing request arrived without ``X-Operator``."""

    code = OPERATOR_REQUIRED

    def __init__(self, message: str | None = None, **kwargs: Any) -> None:
        super().__init__(message, **kwargs)
        #: SRS 8.3: say which header, because the client has to add it, not guess it.
        self.details = _shaped(self.details, header=OPERATOR_HEADER)


class IdempotencyKeyRequiredError(ControlPlaneError):
    """S5IF-G08: ``Idempotency-Key`` is the only source, and a COMMIT needs one.

    The commit request body no longer carries the key, so a call without the header
    has no idempotency at all — which for a COMMIT is a data-integrity problem, not a
    convenience one (AC-EXE-05).  400 rather than 422: nothing about the body is
    wrong, the request is simply missing its one required header.
    """

    code = IDEMPOTENCY_KEY_REQUIRED

    def __init__(self, message: str | None = None, **kwargs: Any) -> None:
        super().__init__(message, **kwargs)
        self.details = _shaped(self.details, header=IDEMPOTENCY_HEADER)


class InvalidStateError(ControlPlaneError):
    """CP-WF-004 and friends: the requested transition is not in appendix A.

    SRS 8.3 fixes what every one of the sixty-odd refusals has to say — which state the
    object is in, which states would have allowed it, and which object at all — so a
    site names those when it knows them and the shape is filled from its own details
    otherwise.  Without it S5 could only show the message.
    """

    code = INVALID_STATE

    def __init__(
        self,
        message: str | None = None,
        *,
        current_state: str | None = None,
        required_states: Sequence[str] = (),
        target_id: str | None = None,
        **kwargs: Any,
    ) -> None:
        super().__init__(message, **kwargs)
        details = self.details
        self.details = _shaped(
            details,
            currentState=current_state or _first_string(details, _CURRENT_STATE_KEYS),
            requiredStates=list(required_states),
            targetId=target_id or _first_string(details, _TARGET_ID_KEYS),
        )


class ProjectArchivedError(ControlPlaneError):
    """CP-PRJ-006: writes against an archived project are refused."""

    code = PROJECT_ARCHIVED


class UploadRejectedError(ControlPlaneError):
    """CP-API-009: file type or size rejected at the upload entry point.

    ``reason`` is ``SIZE`` or ``TYPE`` (SRS 8.3); the machine-readable cause
    :meth:`UploadPolicy.violation` returned rides along as ``violation``, because "the
    media type is missing" and "that media type is not allowed" are different things to
    show a user even though both are a ``TYPE`` refusal.
    """

    code = UPLOAD_REJECTED

    def __init__(self, message: str | None = None, **kwargs: Any) -> None:
        super().__init__(message, **kwargs)
        violation = str(self.details.get("violation") or "")
        if violation:
            self.details = _shaped(self.details, reason=_UPLOAD_REASONS.get(violation, "TYPE"))


class ValidationFailedError(ControlPlaneError):
    """S1's own field check before a request is forwarded (S5IF-G02).

    Raised instead of letting the provider fail deep inside its own validation, so
    S5 gets one error code it can map (UI-REQ-002).  Its ``details`` are projected into
    the SRS 8.3 ``fields[{path, message}]`` shape where the response is rendered, which
    is also where a provider's own ``VALIDATION_ERROR`` is put into that shape.
    """

    code = VALIDATION_ERROR


class RequestInProgressError(ControlPlaneError):
    """CP-API-003 【新增】: same idempotency key, still running.

    ``details.scope`` names the endpoint the key is held on (SRS 8.3), which the request
    answers when the raise site is the middleware and the site itself answers otherwise.
    """

    code = REQUEST_IN_PROGRESS


class GateNotApprovedError(ControlPlaneError):
    """AC-GATE-02: COMMIT or advance requested while the Gate is not APPROVED."""

    code = "GATE_NOT_APPROVED"


class ApprovalInvalidatedError(ControlPlaneError):
    """CP-GATE-009: the hashes the approver saw no longer match the binding."""

    code = "APPROVAL_INVALIDATED"


class DriftDetectedError(ControlPlaneError):
    """CP-GATE-005 / CP-AC-08: S4 reports the target moved since VALIDATE."""

    code = "DRIFT_DETECTED"


class EnvironmentLockedError(ControlPlaneError):
    """CP-GATE-012: S4 emergency lock is on."""

    code = ENVIRONMENT_LOCKED


class OutsideCommitWindowError(ControlPlaneError):
    """CP-GATE-012: now is outside the environment's allowed commit window."""

    code = OUTSIDE_COMMIT_WINDOW


class PriorVerificationMissingError(ControlPlaneError):
    """CP-WF-020 / CP-AC-07: no non-production Run is VERIFIED for this version."""

    code = PRIOR_VERIFICATION_MISSING


class BudgetExceededError(ControlPlaneError):
    """CP-WF-030 / CP-NTF-003: S3 reported budget exhaustion."""

    code = BUDGET_EXCEEDED


class PolicyDeniedError(ControlPlaneError):
    """CP-WF-030: S3 refused to run under the data-exfiltration policy."""

    code = "POLICY_DENIED"


class NotFoundControlError(ControlPlaneError):
    """SRS 8.3 / CP-UI-022: the resource does not exist, or is not generated yet.

    ``reason`` tells the two apart for the client: ``NOT_EXIST`` is a dead reference,
    ``NOT_GENERATED`` is guidance to finish ``requiredStage`` first — a derived resource
    (dependency graph, frontend package, ...) the pipeline has not produced.  A "nothing
    yet" answer must never be faked with a 200 and an empty body (CP-UI-022).
    """

    code = "NOT_FOUND"

    def __init__(
        self,
        message: str | None = None,
        *,
        reason: str = "NOT_EXIST",
        required_stage: str | None = None,
        run_id: str | None = None,
        **kwargs: Any,
    ) -> None:
        super().__init__(message, **kwargs)
        self.details = _shaped(
            self.details, reason=reason, requiredStage=required_stage, runId=run_id
        )


class FeatureNotEnabledError(ControlPlaneError):
    """CP-UI-023 / CP-AC-32: the endpoint's feature belongs to a later milestone.

    MVP keeps the M4 member / restricted-grant / production-exemption endpoints callable
    for reads, but every write is refused with ``details = {feature, milestone}`` so the
    client can show *why* the button is off rather than pretend it worked.
    """

    code = FEATURE_NOT_ENABLED

    def __init__(
        self,
        message: str | None = None,
        *,
        feature: str,
        milestone: str = "M4",
        **kwargs: Any,
    ) -> None:
        super().__init__(message, **kwargs)
        self.details = _shaped(self.details, feature=feature, milestone=milestone)


class ProviderUnavailableControlError(ControlPlaneError):
    """The provider behind a forwarded call is not installed or not reachable.

    Surfaced as 503 rather than 500 so S5 can show "subsystem unavailable" instead
    of an S1 crash (S5IF-G07.2).
    """

    code = PROVIDER_UNAVAILABLE


class SecretInUseError(ControlPlaneError):
    """CP-SEC-003: an environment or provider still points at this credential.

    Deleting it would not be a cleanup, it would be a future DHIS2 call failing with
    ``SECRET_UNAVAILABLE`` for a reason nobody could reconstruct.  ``referencedBy`` names the
    holders so the operator can un-point them first — the same two-step shape CP-PRJ-007 uses for
    deleting a project.
    """

    code = SECRET_IN_USE

    def __init__(self, message: str | None = None, *, referenced_by: Sequence[str], **kwargs: Any) -> None:
        super().__init__(message, **kwargs)
        self.details = _shaped(self.details, referencedBy=sorted(referenced_by))


class SecretStoreDisabledError(ControlPlaneError):
    """CP-SEC-001: the deployment has no usable ``FACTORY_CREDENTIAL_MASTER_KEY``.

    Reuses the foundation's ``SECRET_UNAVAILABLE`` (503, not retryable in the catalog's reading —
    the configuration has to change first) rather than inventing a second code for the same client
    answer: a reference that cannot be read and a store that was never switched on both mean
    "nothing is going to come back from this".  ``details.reason`` is what separates them, and every
    raise site sets one — ``storeDisabled`` here, then ``missing`` / ``keyMismatch`` / ``corrupt`` /
    ``readOnly`` from the resolver — because the console's next sentence differs for each.
    """

    code = SECRET_UNAVAILABLE

    def __init__(self, message: str | None = None, **kwargs: Any) -> None:
        super().__init__(message, **kwargs)
        self.details = _shaped(self.details, reason="storeDisabled", setting="FACTORY_CREDENTIAL_MASTER_KEY")


__all__ = [
    "ApprovalInvalidatedError",
    "BUDGET_EXCEEDED",
    "BudgetExceededError",
    "ControlPlaneError",
    "DEFAULT_HTTP_STATUS",
    "DriftDetectedError",
    "ENVIRONMENT_LOCKED",
    "EnvironmentLockedError",
    "ERROR_HTTP_STATUS",
    "FEATURE_NOT_ENABLED",
    "FeatureNotEnabledError",
    "GateNotApprovedError",
    "IDEMPOTENCY_KEY_REQUIRED",
    "IdempotencyKeyRequiredError",
    "INVALID_STATE",
    "InvalidStateError",
    "OPERATOR_REQUIRED",
    "OperatorRequiredError",
    "OutsideCommitWindowError",
    "OUTSIDE_COMMIT_WINDOW",
    "PRIOR_VERIFICATION_MISSING",
    "PROVIDER_ERROR_HTTP_STATUS",
    "PROVIDER_CODE_ALIASES",
    "PROVIDER_UNAVAILABLE",
    "PolicyDeniedError",
    "PriorVerificationMissingError",
    "ProjectArchivedError",
    "PROJECT_ARCHIVED",
    "ProviderUnavailableControlError",
    "REQUEST_IN_PROGRESS",
    "PARSE_INTERRUPTED",
    "INTERNAL_ERROR",
    "RequestInProgressError",
    "S1_ERROR_RETRYABLE",
    "SECRET_IN_USE",
    "SECRET_UNAVAILABLE",
    "SecretInUseError",
    "SecretStoreDisabledError",
    "ValidationFailedError",
    "NotFoundControlError",
    "UPLOAD_REJECTED",
    "UploadRejectedError",
    "http_status_for",
    "normalize_provider_error",
]
