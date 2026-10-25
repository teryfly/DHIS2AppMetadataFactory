"""The built-in roles and what each may do (FR-SEC-002, CP-IAM-002).

The six names are 总体 SRS 2.4's table, and the action sets are keyed on 附录 B's names —
``iam.KNOWN_ACTIONS`` plus the ones S5IF added — because the authorization hook, the
``allowed-actions`` payload and this table have to agree exactly or a button and a 403 disagree.

Roles are code, not rows: the SRS says *内置*角色 and defines no way to invent another, so a
`roles` table would be an invitation to a permission nobody specified.  What *is* data is who
holds which role where (:class:`factory_control.models.UserRole`,
:class:`factory_control.models.ProjectMember`).

Two rules shape every set below:

* **view follows participate.**  Anyone who may act on a project may see it; the reverse is not
  true, which is what makes 查看/编辑/审批 separate grants rather than three names for one.
* **production is a second decision.**  ``gate.approve.DEPLOY_GATE.PRODUCTION`` and
  ``execution.commit.PRODUCTION`` are distinct action names for a reason (CP-GATE-012): a
  Deployment Approver approves, an Implementer commits, and neither is the other's proxy.
"""

from __future__ import annotations

from factory_control import iam
from factory_control.domain.enums import GateType

SOLUTION_ARCHITECT = "SOLUTION_ARCHITECT"
DHIS2_IMPLEMENTER = "DHIS2_IMPLEMENTER"
FRONTEND_DEVELOPER = "FRONTEND_DEVELOPER"
BUSINESS_ANALYST = "BUSINESS_ANALYST"
DEPLOYMENT_APPROVER = "DEPLOYMENT_APPROVER"
PLATFORM_ADMINISTRATOR = "PLATFORM_ADMINISTRATOR"

#: The role names S1 accepts, in 2.4's order.  Anything else is refused at the boundary.
BUILT_IN_ROLES: tuple[str, ...] = (
    SOLUTION_ARCHITECT,
    DHIS2_IMPLEMENTER,
    FRONTEND_DEVELOPER,
    BUSINESS_ANALYST,
    DEPLOYMENT_APPROVER,
    PLATFORM_ADMINISTRATOR,
)

#: Every design-stage Gate an architect decides.  Named per Gate rather than by prefix, so
#: adding one is a deliberate edit here — but spelled *from* GateType, because a literal list
#: here had drifted to `gate.approve.METADATA_GATE` and `gate.approve.FIXTURE_GATE`: neither is
#: a GateType, while the `DESIGN_GATE` that closes PAGE_DESIGN was absent.  The consequence was
#: the same failure `project.export` had: a real Gate no identity in the system could approve.
_DESIGN_GATES = tuple(
    f"{iam.GATE_APPROVE_PREFIX}.{gate.value}"
    for gate in GateType
    if gate.is_design_gate or gate is GateType.HANDOFF_GATE
)

#: 总体 SRS 16.1 names the DHIS2 Implementer as PACKAGE_GATE's default approver, and that Gate
#: had no holder at all: a Run could reach STATIC_VALIDATION and then nobody could approve it.
_PACKAGE_GATE = f"{iam.GATE_APPROVE_PREFIX}.{GateType.PACKAGE_GATE.value}"

_PROJECT_VIEW = (
    iam.PROJECT_VIEW,
    iam.ARTIFACT_READ,
    iam.AUDIT_VIEW,
)

ROLE_ACTIONS: dict[str, frozenset[str]] = {
    # 需求确认、领域与映射设计、架构与设计审批
    SOLUTION_ARCHITECT: frozenset(
        {
            *_PROJECT_VIEW,
            iam.PROJECT_CREATE,
            iam.PROJECT_EDIT,
            iam.PROJECT_UPGRADE_MODE,
            iam.INPUT_UPLOAD,
            iam.INPUT_PARSE,
            iam.INPUT_CONFIRM_MAPPING,
            iam.STAGE_RUN,
            iam.STAGE_CHAT,
            iam.PATCH_SUBMIT,
            iam.QUESTION_ANSWER,
            iam.ARTIFACT_EXPLAIN,
            iam.AGENT_CANCEL,
            iam.CHANGE_REQUEST_CREATE,
            # CP-CR-002: "批准 CR 需要 SOLUTION_ARCHITECT 角色（M4 起校验）"
            iam.CHANGE_REQUEST_APPROVE,
            iam.POLICY_RELAX,
            *_DESIGN_GATES,
        }
    ),
    # 环境配置、元数据审核、VALIDATE、COMMIT 执行
    DHIS2_IMPLEMENTER: frozenset(
        {
            *_PROJECT_VIEW,
            iam.PROJECT_CREATE,
            iam.PROJECT_EDIT,
            iam.INPUT_UPLOAD,
            iam.INPUT_PARSE,
            iam.INPUT_CONFIRM_MAPPING,
            iam.STAGE_RUN,
            iam.STAGE_CHAT,
            iam.PATCH_SUBMIT,
            iam.QUESTION_ANSWER,
            iam.ARTIFACT_EXPLAIN,
            iam.AGENT_CANCEL,
            iam.DEPLOYMENT_CREATE,
            iam.DEPLOYMENT_RUN_DRIFT_CHECK,
            _PACKAGE_GATE,
            iam.CHANGE_REQUEST_CREATE,
            iam.ENVIRONMENT_CREATE,
            iam.ENVIRONMENT_EDIT,
            iam.ENVIRONMENT_DISABLE,
            iam.ENVIRONMENT_TEST,
            iam.ENVIRONMENT_PROBE,
            # the implementer is the person who types the DHIS2 login, so the credential right
            # has to travel with the environment rights rather than sitting above them
            iam.SECRET_MANAGE,
            iam.REGISTRY_BINDING_SET,
            iam.UID_POOL_REPLENISH,
            iam.AUDIT_VERIFY,
            # 执行者不是审批人: AC-GATE-04 is exactly this pair held apart
            iam.execution_commit_action("TEST"),
            iam.execution_commit_action("STAGING"),
            iam.execution_commit_action("PRODUCTION"),
        }
    ),
    # 消费开发包,开发并联调自定义应用: reads the handoff, changes nothing
    FRONTEND_DEVELOPER: frozenset({_PROJECT_VIEW[0], iam.ARTIFACT_READ}),
    # 提供与审核业务需求、Use Case
    BUSINESS_ANALYST: frozenset(
        {
            *_PROJECT_VIEW,
            iam.INPUT_UPLOAD,
            iam.INPUT_PARSE,
            iam.QUESTION_ANSWER,
            iam.PATCH_SUBMIT,
            iam.STAGE_CHAT,
            iam.ARTIFACT_EXPLAIN,
            "gate.approve.REQUIREMENT_GATE",
        }
    ),
    # 对生产类环境的提交进行审批 (FR-GATE-005)
    DEPLOYMENT_APPROVER: frozenset(
        {
            *_PROJECT_VIEW,
            iam.AUDIT_VERIFY,
            "gate.approve.DEPLOY_GATE.TEST",
            "gate.approve.DEPLOY_GATE.STAGING",
            "gate.approve.DEPLOY_GATE.PRODUCTION",
        }
    ),
    # 管理环境、模型提供方、数据策略、用户与权限
    PLATFORM_ADMINISTRATOR: frozenset(
        {
            *_PROJECT_VIEW,
            iam.PROJECT_CREATE,
            iam.PROJECT_EDIT,
            iam.PROJECT_ARCHIVE,
            # CP-PRJ-007 makes the export a precondition of the delete ("删除前必须生成
            # PROJECT_EXPORT"), so granting the delete without it handed out a step nobody in the
            # system could take: the guard answered 403 while the state machine answered 409, and
            # an archived project could never be removed.
            iam.PROJECT_EXPORT,
            iam.PROJECT_DELETE,
            iam.PROJECT_IMPORT,
            iam.PROJECT_UPGRADE_MODE,
            iam.ENVIRONMENT_MANAGE,
            iam.ENVIRONMENT_CREATE,
            iam.ENVIRONMENT_EDIT,
            iam.ENVIRONMENT_DISABLE,
            iam.ENVIRONMENT_TEST,
            iam.ENVIRONMENT_LOCK,
            iam.ENVIRONMENT_PROBE,
            iam.GOVERNANCE_MANAGE,
            iam.GOVERNANCE_MODEL_REGISTER,
            iam.GOVERNANCE_MODEL_ENABLE,
            iam.GOVERNANCE_PROVIDER_REGISTER,
            iam.GOVERNANCE_PROVIDER_ENABLE,
            iam.GOVERNANCE_PROMPT_ACTIVATE,
            iam.GOVERNANCE_REGRESSION_RECORD,
            iam.GOVERNANCE_EVALUATION_RUN,
            iam.SECRET_MANAGE,
            iam.BUDGET_SET,
            iam.POLICY_RELAX,
            iam.SETTINGS_MANAGE,
            iam.AUDIT_VERIFY,
            iam.ARTIFACT_PURGE,
            iam.ARTIFACT_READ_PHI,
            iam.ARTIFACT_READ_RESTRICTED,
            iam.INPUT_UPLOAD,
            iam.STAGE_RUN,
            iam.STAGE_CHAT,
            iam.QUESTION_ANSWER,
            iam.CHANGE_REQUEST_CREATE,
            # the exemption is an administrator's act, because it waives a safety rule and
            # not a preference (CP-WF-020's "豁免机制随 M4 的权限一并引入")
            iam.PRODUCTION_EXEMPTION_MANAGE,
            # M4 owns the role and membership tables (总体 SRS 21.4: 用户与角色变更)
            iam.USER_MANAGE,
            iam.MEMBER_MANAGE,
            iam.RESTRICTED_GRANT_MANAGE,
        }
    ),
}

#: Actions that name a Gate or an environment class only as a prefix.  Kept as data because a
#: policy that guessed at the split would silently widen a grant.
PREFIXED_ACTIONS: tuple[str, ...] = (
    iam.GATE_APPROVE_PREFIX,
    iam.EXECUTION_COMMIT_PREFIX,
)

#: Every action name the role table can grant, so a typo fails at import rather than granting
#: nothing forever.  ``KNOWN_ACTIONS`` is 附录 B's core list; the rest are the S5IF additions.
_GRANTABLE = {name for actions in ROLE_ACTIONS.values() for name in actions}
_CORE_VOCABULARY = {
    *iam.KNOWN_ACTIONS,
    iam.STAGE_CHAT,
    iam.BUDGET_SET,
    iam.INPUT_PARSE,
    iam.INPUT_CONFIRM_MAPPING,
    iam.ARTIFACT_EXPLAIN,
    iam.AGENT_CANCEL,
    iam.REGISTRY_BINDING_SET,
    iam.UID_POOL_REPLENISH,
    iam.ENVIRONMENT_CREATE,
    iam.ENVIRONMENT_EDIT,
    iam.ENVIRONMENT_DISABLE,
    iam.ENVIRONMENT_TEST,
    iam.ENVIRONMENT_LOCK,
    iam.ENVIRONMENT_PROBE,
    iam.GOVERNANCE_MODEL_REGISTER,
    iam.GOVERNANCE_MODEL_ENABLE,
    iam.GOVERNANCE_PROVIDER_REGISTER,
    iam.GOVERNANCE_PROVIDER_ENABLE,
    iam.GOVERNANCE_PROMPT_ACTIVATE,
    iam.GOVERNANCE_REGRESSION_RECORD,
    iam.GOVERNANCE_EVALUATION_RUN,
    iam.SECRET_MANAGE,
    iam.DEPLOYMENT_RUN_DRIFT_CHECK,
    iam.execution_commit_action("TEST"),
    iam.execution_commit_action("STAGING"),
    iam.execution_commit_action("PRODUCTION"),
    *_DESIGN_GATES,
    _PACKAGE_GATE,
    "gate.approve.DEPLOY_GATE.TEST",
    "gate.approve.DEPLOY_GATE.STAGING",
    "gate.approve.DEPLOY_GATE.PRODUCTION",
    iam.PRODUCTION_EXEMPTION_MANAGE,
    iam.USER_MANAGE,
    iam.MEMBER_MANAGE,
    iam.RESTRICTED_GRANT_MANAGE,
}

_unknown = {name for name in _GRANTABLE if name not in _CORE_VOCABULARY}
if _unknown:  # pragma: no cover - a development-time guard, not a runtime path
    raise ValueError(f"ROLE_ACTIONS grants names 附录 B does not define: {sorted(_unknown)}")


def actions_for(role: str) -> frozenset[str]:
    """What one role may do.  An unknown role name grants nothing at all."""
    return ROLE_ACTIONS.get(role, frozenset())


def is_known_role(role: str) -> bool:
    return role in ROLE_ACTIONS


__all__ = [
    "BUILT_IN_ROLES",
    "BUSINESS_ANALYST",
    "DEPLOYMENT_APPROVER",
    "DHIS2_IMPLEMENTER",
    "FRONTEND_DEVELOPER",
    "PLATFORM_ADMINISTRATOR",
    "PREFIXED_ACTIONS",
    "ROLE_ACTIONS",
    "SOLUTION_ARCHITECT",
    "actions_for",
    "is_known_role",
]
