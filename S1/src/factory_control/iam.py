"""Authorization hook (CP-IAM-001).

MVP ships no authentication or roles (D-16), yet **every** state-changing path
already calls :func:`authorize` with an action name from 附录 B.  The MVP policy
allows everything; M4 replaces the policy object without touching the handlers.

Action names are centralised here so a typo cannot silently create a second,
unpolled action string.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from factory_control.foundation import FactoryError


class PermissionDeniedError(FactoryError):
    """Raised by a policy that refuses an action.  Never raised in MVP."""

    code = "PERMISSION_DENIED"


# --- 附录 B action names ----------------------------------------------------

PROJECT_CREATE = "project.create"
PROJECT_VIEW = "project.view"
PROJECT_EDIT = "project.edit"
PROJECT_UPGRADE_MODE = "project.upgrade_mode"
PROJECT_ARCHIVE = "project.archive"
PROJECT_DELETE = "project.delete"
PROJECT_EXPORT = "project.export"
PROJECT_IMPORT = "project.import"
INPUT_UPLOAD = "input.upload"
STAGE_RUN = "stage.run"
#: 附录 B has no dialogue action, because in-stage chat is an S3 capability scheduled for M3
#: that S1's 8.1 endpoint table predates.  A name of its own is what lets M4 grant "may talk to
#: the assistant" without granting ``stage.run``, which starts a build.
STAGE_CHAT = "stage.chat"
PATCH_SUBMIT = "patch.submit"
QUESTION_ANSWER = "question.answer"
DEPLOYMENT_CREATE = "deployment.create"
#: 附录 B names the drift re-check its own action, because UI-DEP-002 puts that
#: button next to a Run and M4 must be able to grant it without granting a new Run.
DEPLOYMENT_RUN_DRIFT_CHECK = "deployment_run.drift_check"
CHANGE_REQUEST_CREATE = "change_request.create"
CHANGE_REQUEST_APPROVE = "change_request.approve"
ARTIFACT_READ = "artifact.read"
ARTIFACT_READ_RESTRICTED = "artifact.read.restricted"
ARTIFACT_READ_PHI = "artifact.read.phi"
ARTIFACT_PURGE = "artifact.purge"
AUDIT_VIEW = "audit.view"
AUDIT_VERIFY = "audit.verify"
ENVIRONMENT_MANAGE = "environment.manage"
GOVERNANCE_MANAGE = "governance.manage"
POLICY_RELAX = "governance.policy.relax"
SETTINGS_MANAGE = "settings.manage"

# --- The actions S5's new buttons call (S5IF-FLD12) --------------------------

ENVIRONMENT_CREATE = "environment.create"
ENVIRONMENT_EDIT = "environment.edit"
ENVIRONMENT_DISABLE = "environment.disable"
ENVIRONMENT_TEST = "environment.test_connection"
ENVIRONMENT_LOCK = "environment.emergency_lock"
ENVIRONMENT_PROBE = "environment.probe_capabilities"
GOVERNANCE_MODEL_REGISTER = "governance.model.register"
GOVERNANCE_MODEL_ENABLE = "governance.model.enable"
GOVERNANCE_PROVIDER_REGISTER = "governance.provider.register"
GOVERNANCE_PROVIDER_ENABLE = "governance.provider.enable"
GOVERNANCE_PROMPT_ACTIVATE = "governance.prompt.activate"
GOVERNANCE_REGRESSION_RECORD = "governance.regression.record"
GOVERNANCE_EVALUATION_RUN = "governance.evaluation.run"
#: CP-SEC-001: writing, listing and removing the credentials the console can enter.  Held by
#: exactly the roles that may point something at a credential (an environment's DHIS2 login, a
#: provider's model key) — the right to store a secret follows the right to use one.
SECRET_MANAGE = "secret.manage"
INPUT_PARSE = "input.parse"
INPUT_CONFIRM_MAPPING = "input.confirm_column_mapping"
ARTIFACT_EXPLAIN = "artifact.explain"
AGENT_CANCEL = "agent.cancel"
REGISTRY_BINDING_SET = "registry_binding.set"
UID_POOL_REPLENISH = "uid_pool.replenish"
BUDGET_SET = "governance.budget.set"
#: 附录 B (V1.1.2) now names these: the dev-package download, and the M4 objects CP-IAM-002
#: makes S1's own — members, restricted grants, the production exemption.  They keep names
#: of their own rather than riding on ``settings.manage``, which would let a settings editor
#: change who can see PHI.
USER_MANAGE = "user.manage"
MEMBER_MANAGE = "member.manage"
RESTRICTED_GRANT_MANAGE = "restricted_grant.manage"
PRODUCTION_EXEMPTION_MANAGE = "production_exemption.manage"
FRONTEND_PACKAGE_DOWNLOAD = "frontend_package.download"

GATE_APPROVE_PREFIX = "gate.approve"
EXECUTION_COMMIT_PREFIX = "execution.commit"


def gate_approve_action(gate_type: str, environment_class: str | None = None) -> str:
    """``gate.approve.<GATE_TYPE>``, or ``gate.approve.DEPLOY_GATE.<ENV_CLASS>``."""
    if environment_class:
        return f"{GATE_APPROVE_PREFIX}.{gate_type}.{environment_class}"
    return f"{GATE_APPROVE_PREFIX}.{gate_type}"


def execution_commit_action(environment_class: str) -> str:
    """``execution.commit.<ENV_CLASS>`` — production commits are separable (CP-GATE-012)."""
    return f"{EXECUTION_COMMIT_PREFIX}.{environment_class}"


#: Every action S1 may ask about; M4's role table is keyed on exactly these.
KNOWN_ACTIONS: frozenset[str] = frozenset(
    {
        PROJECT_CREATE,
        PROJECT_VIEW,
        PROJECT_EDIT,
        PROJECT_UPGRADE_MODE,
        PROJECT_ARCHIVE,
        PROJECT_DELETE,
        PROJECT_EXPORT,
        PROJECT_IMPORT,
        INPUT_UPLOAD,
        STAGE_RUN,
        STAGE_CHAT,
        PATCH_SUBMIT,
        QUESTION_ANSWER,
        DEPLOYMENT_CREATE,
        CHANGE_REQUEST_CREATE,
        CHANGE_REQUEST_APPROVE,
        ARTIFACT_READ,
        ARTIFACT_READ_RESTRICTED,
        ARTIFACT_READ_PHI,
        ARTIFACT_PURGE,
        AUDIT_VIEW,
        AUDIT_VERIFY,
        ENVIRONMENT_MANAGE,
        GOVERNANCE_MANAGE,
        POLICY_RELAX,
        SETTINGS_MANAGE,
        USER_MANAGE,
        MEMBER_MANAGE,
        RESTRICTED_GRANT_MANAGE,
        PRODUCTION_EXEMPTION_MANAGE,
        FRONTEND_PACKAGE_DOWNLOAD,
    }
)


class PermissionPolicy(Protocol):
    def is_allowed(self, *, operator: str, project_id: str | None, action: str) -> bool: ...


@dataclass(frozen=True)
class AllowAllPolicy:
    """The MVP policy (CP-IAM-001): identity is a declared name, nothing is enforced."""

    def is_allowed(self, *, operator: str, project_id: str | None, action: str) -> bool:
        _ = (operator, project_id, action)
        return True


_policy: PermissionPolicy = AllowAllPolicy()


def set_permission_policy(policy: PermissionPolicy) -> None:
    """Install a policy.  M4 calls this with the role-aware implementation."""
    global _policy
    _policy = policy


def get_permission_policy() -> PermissionPolicy:
    return _policy


def authorize(operator: str, project_id: str | None, action: str) -> None:
    """Raise :class:`PermissionDeniedError` unless ``operator`` may run ``action``.

    Handlers must call this instead of checking anything themselves, so the M4
    switch is a policy swap rather than a code sweep.
    """
    if not action:
        raise PermissionDeniedError("empty action name", details={"projectId": project_id})
    if not _policy.is_allowed(operator=operator, project_id=project_id, action=action):
        raise PermissionDeniedError(
            f"operator {operator!r} is not allowed to perform {action!r}",
            details={"action": action, "projectId": project_id},
        )


__all__ = [
    "ARTIFACT_PURGE",
    "ARTIFACT_READ",
    "ARTIFACT_READ_PHI",
    "ARTIFACT_READ_RESTRICTED",
    "AUDIT_VERIFY",
    "AUDIT_VIEW",
    "AllowAllPolicy",
    "CHANGE_REQUEST_APPROVE",
    "CHANGE_REQUEST_CREATE",
    "DEPLOYMENT_CREATE",
    "ENVIRONMENT_MANAGE",
    "EXECUTION_COMMIT_PREFIX",
    "GATE_APPROVE_PREFIX",
    "GOVERNANCE_MANAGE",
    "INPUT_UPLOAD",
    "KNOWN_ACTIONS",
    "PATCH_SUBMIT",
    "POLICY_RELAX",
    "PROJECT_ARCHIVE",
    "PROJECT_DELETE",
    "PROJECT_EDIT",
    "PROJECT_EXPORT",
    "PROJECT_IMPORT",
    "PROJECT_UPGRADE_MODE",
    "PROJECT_CREATE",
    "SECRET_MANAGE",
    "PROJECT_VIEW",
    "PermissionDeniedError",
    "PermissionPolicy",
    "QUESTION_ANSWER",
    "MEMBER_MANAGE",
    "RESTRICTED_GRANT_MANAGE",
    "PRODUCTION_EXEMPTION_MANAGE",
    "FRONTEND_PACKAGE_DOWNLOAD",
    "SETTINGS_MANAGE",
    "USER_MANAGE",
    "STAGE_CHAT",
    "STAGE_RUN",
    "authorize",
    "execution_commit_action",
    "gate_approve_action",
    "get_permission_policy",
    "set_permission_policy",
]
