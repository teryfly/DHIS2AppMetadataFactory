"""执行计划（DX-EXE-010…013、SRS 6.3）。

计划回答四个问题，且都只依据**已持久化的事实**：

* 要执行什么——执行器与顺序（DX-EXE-037 的固定顺序）、载荷产物与哈希、预期变更（来自
  S2 的变更摘要）、维护动作（默认无，DX-EXE-013）、表达式校验安排（来自 manifest 分类）；
* 用什么参数导入——默认值来自 Capability Profile，调用方改动的每一项都标记为
  “需在 DEPLOY_GATE 显式确认”（DX-EXE-011）；
* 目标环境配不配——每个操作都要被 Capability Profile 支持，且对应凭证范围的服务账户
  要具备所需权限，不满足是 BLOCKING（DX-EXE-012）；
* 什么会变化——Snapshot 的规模与漂移检查的对象清单。
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Any

from factory_foundation.api import unit_of_work
from sqlalchemy import select

from factory_dhis2.artifacts import register_staged, stage_document
from factory_dhis2.dto.issue import Issue, make_issue
from factory_dhis2.enums import (
    CredentialScope,
    DeletionPolicy,
    ExecutorType,
    IssueLevel,
    MaintenanceAction,
)
from factory_dhis2.errors import EnvironmentInvalidError
from factory_dhis2.execution.context import RunContext, document_of, load_context
from factory_dhis2.execution.drift import drift_watch
from factory_dhis2.execution.expressions import deferred, server_checkable
from factory_dhis2.models.environment import ConnectionTestRow
from factory_dhis2.payload.hash import verify_expected_hash
from factory_dhis2.profiles.base import VersionProfile

#: MVP 支持的维护动作（DX-EXE-013：分析表生成等延后）
SUPPORTED_MAINTENANCE: frozenset[str] = frozenset({action.value for action in MaintenanceAction})

#: 执行器 → 需要的凭证范围与所需逻辑资源（DX-EXE-012 的判定表）
EXECUTOR_REQUIREMENTS: dict[str, tuple[CredentialScope, tuple[str, ...]]] = {
    ExecutorType.METADATA.value: (
        CredentialScope.IMPORT,
        ("metadata.import", "system.tasks"),
    ),
    ExecutorType.TRACKER_DATA.value: (CredentialScope.TRACKER, ("tracker.import",)),
    ExecutorType.DATASTORE.value: (CredentialScope.DATASTORE, ("dataStore.write",)),
    ExecutorType.APP_UPLOAD.value: (CredentialScope.APPS, ("apps.upload",)),
}


class PlanBuild:
    """一次计划生成的结果。"""

    def __init__(
        self,
        document: dict[str, Any],
        import_options: dict[str, Any],
        requires_confirmation: list[dict[str, Any]],
        issues: list[Issue],
        maintenance_actions: list[dict[str, Any]],
    ) -> None:
        self.document = document
        self.import_options = import_options
        self.requires_confirmation = requires_confirmation
        self.issues = issues
        self.maintenance_actions = maintenance_actions

    @property
    def blockers(self) -> list[Issue]:
        return [issue for issue in self.issues if issue.is_blocking]

    @property
    def supported(self) -> bool:
        return not self.blockers


def build_plan(
    context: RunContext,
    *,
    import_options: dict[str, Any] | None = None,
    maintenance_actions: list[dict[str, Any]] | None = None,
    expected_hash: str | None = None,
    executor_types: list[str] | None = None,
    deletion_payload: bool = False,
) -> PlanBuild:
    """生成计划文档（不落库，落库由 :func:`save_plan` 做）。"""
    profile = context.target.profile
    package = context.package

    # DX-EXE-005：期望哈希先校验，参数解析再往下走
    payload_hash = verify_expected_hash(package, expected_hash)

    resolved_options, needs_confirmation = resolve_import_options(profile, import_options)
    actions = normalize_maintenance(maintenance_actions)

    issues: list[Issue] = list(_missing_manifest_sections(package.missing_sections))
    issues.extend(_capability_checks(context, executor_types or [ExecutorType.METADATA.value]))
    issues.extend(_deletion_policy_checks(context, deletion_payload))
    if not package.change_summary():
        # DX-EXE-010：预期变更来自 S2 的变更摘要。缺它不阻断执行（服务端 VALIDATE 才是
        # 权威判定），但计划里必须写明"这次没有预期变更可核对"，否则 DEPLOY_GATE 的审批人
        # 看到的空 expectedChanges 会被当成"这次不改任何东西"。
        issues.append(
            make_issue(
                "DX-EXE-010",
                IssueLevel.WARNING,
                "载荷的 manifest 没有 changeSummary 段：执行计划的 expectedChanges 无从核对",
                object_key=context.package.artifact_id,
                artifact_id=context.package.artifact_id,
                suggested_action="由 S2 在载荷里给出变更摘要，或在审批时人工核对预期变更",
            )
        )

    planned = (executor_types or [ExecutorType.METADATA.value])
    ordered = order_executors(planned)

    document = {
        "schemaVersion": 1,
        "kind": "EXECUTION_PLAN",
        "environmentKey": context.environment_key,
        "environmentClass": context.target.environment_class.value,
        "projectKey": context.project_id,
        "payload": document_of(package),
        "payloadHash": payload_hash,
        "blueprintVersion": package.blueprint_version,
        "dhis2Version": context.target.dhis2_version,
        "capabilityProfileArtifactId": context.target.capability_profile_artifact_id,
        "profileVersion": profile.profile_version,
        "executors": ordered,
        "importOptions": resolved_options,
        "requiresConfirmation": needs_confirmation,
        "expectedChanges": package.change_summary(),
        "objectCount": package.object_count,
        # DX-EXE-033 的阈值判定只能看**调用方**给不给 async：合并后的参数里永远带着 Profile
        # 的默认值 ``async=false``，拿它当「调用方指定」会让阈值规则彻底失效（5,000 对象的
        # 包也会走同步导入）。
        "asyncImport": _async_decision(
            context, package.object_count, import_options or {}
        ),
        "maintenanceActions": actions,
        "expressionValidation": {
            "atValidate": [
                {"businessKey": item.get("businessKey"), "field": item.get("field")}
                for item in server_checkable(package)
            ],
            "atPostVerify": [
                {"businessKey": item.get("businessKey"), "field": item.get("field")}
                for item in deferred(package)
            ],
        },
        "driftWatch": drift_watch(package),
        "deletion": deletion_payload,
        # the plan's own findings travel in the document, not only in the returned object:
        # a resubmitted execution restores the PLAN step's persisted document instead of
        # rebuilding it (DX-EXE-033), and a plan that is stored without its blockers would
        # then resume as though nothing blocked it and carry on to VALIDATE (DX-EXE-012).
        # They are also what a DEPLOY_GATE approver needs to see *why* a plan is refused.
        "issues": [issue.model_dump(mode="json") for issue in issues],
        "plannedAt": datetime.now(UTC).isoformat(),
        "notes": package.manifest.get("notes", []),
    }
    return PlanBuild(document, resolved_options, needs_confirmation, issues, actions)


def resolve_import_options(
    profile: VersionProfile, requested: dict[str, Any] | None
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """默认值来自 Profile；被改动的项列出来要求显式确认（DX-EXE-011/013）。"""
    defaults = profile.import_defaults("metadata")
    allowed = profile.import_allowed_parameters("metadata")
    needs_confirm_names = set(profile.import_parameters.get("metadata", {}).get("nonDefaultRequiresConfirmation", []))
    resolved = dict(defaults)
    confirmation: list[dict[str, Any]] = []
    for key, value in (requested or {}).items():
        if key == "importMode":
            # importMode 由步骤决定，不接受调用方指定（DX-BR-05 保证同参数）
            continue
        if key not in defaults and key not in allowed:
            raise EnvironmentInvalidError(
                f"import parameter {key!r} is not supported by "
                f"{profile.profile_version}",
                details={
                    "parameter": key,
                    "profileVersion": profile.profile_version,
                    "supported": sorted(set(defaults) | set(allowed)),
                },
            )
        if key in allowed and str(value) not in {str(item) for item in allowed[key]} and not isinstance(value, bool):
            raise EnvironmentInvalidError(
                f"import parameter {key!r} does not accept {value!r} in "
                f"{profile.profile_version}",
                details={"parameter": key, "allowed": allowed[key]},
            )
        if resolved.get(key) != value:
            resolved[key] = value
            if key in needs_confirm_names:
                confirmation.append(
                    {
                        "parameter": key,
                        "defaultValue": defaults.get(key),
                        "requestedValue": value,
                        "reason": "非默认导入参数必须在 DEPLOY_GATE 显式确认（DX-EXE-011）",
                    }
                )
    return resolved, confirmation


def normalize_maintenance(requested: list[dict[str, Any]] | None) -> list[dict[str, Any]]:
    """维护动作默认关闭，只能由调用方列出（DX-EXE-013）。"""
    out: list[dict[str, Any]] = []
    for item in requested or []:
        name = str(item.get("action") or item.get("name") or "") if isinstance(item, dict) else str(item)
        if name not in SUPPORTED_MAINTENANCE:
            raise EnvironmentInvalidError(
                f"maintenance action {name!r} is not supported in MVP",
                details={"action": name, "supported": sorted(SUPPORTED_MAINTENANCE), "rule": "DX-EXE-013"},
            )
        out.append({"action": name, "credentialScope": CredentialScope.MAINTENANCE.value})
    return out


def order_executors(planned: list[str]) -> list[str]:
    """DX-EXE-037 / DX-BR-12：固定顺序 Metadata → DataStore → Tracker → App Upload。

    顺序的**唯一出处**是 ``executors.ordered_executors``（它按 ``EXECUTOR_ORDER`` 排），计划
    与提交前的守卫都读它，避免两处各排各的。
    """
    from factory_dhis2.executors import ordered_executors

    return [item.executor_type.value for item in ordered_executors(planned)]


def save_plan(context: RunContext, plan: PlanBuild) -> str:
    """以 EXECUTION_PLAN 产物保存（DX-EXE-010）。

    暂存在事务外完成，登记自开一段短事务（00 文档 6.2）。
    """
    owner = context.project_id or f"env-{context.environment_key}"
    handle = stage_document(plan.document, project_id=owner, artifact_type="EXECUTION_PLAN")
    with unit_of_work() as session:
        info = register_staged(
            handle,
            project_id=owner,
            artifact_type="EXECUTION_PLAN",
            source_artifact_ids=[context.package.artifact_id],
            session=session,
        )
    return info.id


def build_and_save(
    environment_key: str,
    package_artifact_id: str,
    *,
    project_id: str | None = None,
    import_options: dict[str, Any] | None = None,
    maintenance_actions: list[dict[str, Any]] | None = None,
    expected_hash: str | None = None,
    executor_types: list[str] | None = None,
) -> tuple[RunContext, PlanBuild, str]:
    """“装配 → 生成 → 保存”一体，供管线与测试直接调用。"""
    context = load_context(environment_key, package_artifact_id, project_id=project_id)
    plan = build_plan(
        context,
        import_options=import_options,
        maintenance_actions=maintenance_actions,
        expected_hash=expected_hash,
        executor_types=executor_types,
    )
    artifact_id = save_plan(context, plan)
    return context, plan, artifact_id


def async_import_decision(
    plan_document: Mapping[str, Any] | None,
    context: RunContext,
    object_count: int,
    *,
    caller_options: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """这次执行到底走不走异步导入（DX-EXE-033）。

    计划里那份决定是权威：同一次部署的 VALIDATE 与 COMMIT 必须走同一条路，否则"校验时
    同步通过、提交时异步失败"这种半状态就说不清是谁的规矩。

    但**计划产物读不到时不能退回同步**：阈值规则的存在正是因为大载荷同步导入会顶穿 HTTP
    超时（5,000 对象的包同步提交几乎必然失败）。这里按同一套规则用对象数与环境阈值重算，
    并把 ``decidedBy`` 标成 ``recomputed``，让报告能看出这一条不是从计划里来的。
    """
    recorded = (plan_document or {}).get("asyncImport")
    if isinstance(recorded, Mapping) and isinstance(recorded.get("async"), bool):
        return dict(recorded)
    # 只认调用方**显式**给的那个 async：计划里的 importOptions 是合并过 Profile 默认值的
    # 一份，而默认值恒带 ``async=false``——拿它当「调用方指定」会让阈值规则失效。
    decided = _async_decision(context, object_count, dict(caller_options or {}))
    if not isinstance(recorded, Mapping):
        decided["decidedBy"] = f"recomputed:{decided['decidedBy']}"
    return decided


def _async_decision(
    context: RunContext, object_count: int, options: dict[str, Any]
) -> dict[str, Any]:
    """DX-EXE-033：阈值来自配置，环境可覆盖；调用方显式要求优先。"""
    from factory_dhis2.config import get_settings

    settings = get_settings()
    threshold = context.target.async_import_threshold or settings.async_import_threshold
    forced = options.get("async")
    use_async = bool(forced) if forced is not None else object_count > threshold
    return {
        "async": use_async,
        "thresholdObjects": threshold,
        "objectCount": object_count,
        "decidedBy": "caller" if forced is not None else "threshold",
        "pollResource": "system.tasks",
    }


def _missing_manifest_sections(missing: list[str]) -> list[Issue]:
    return [
        make_issue(
            "DX-ASM-02",
            IssueLevel.BLOCKING,
            f"manifest 段 {name} 缺失，无法进行漂移检查、Snapshot 与问题映射",
            object_key=name,
        )
        for name in missing
    ]


def _capability_checks(
    context: RunContext, executor_types: list[str]
) -> list[Issue]:
    """DX-EXE-012：每个操作都要被 Capability Profile 支持，账户要有所需权限。"""
    issues: list[Issue] = []
    profile = context.target.profile
    for executor in executor_types:
        scope, resources = EXECUTOR_REQUIREMENTS.get(executor, (CredentialScope.DISCOVERY, ()))
        for resource in resources:
            if profile.resource(resource) is None:
                issues.append(
                    make_issue(
                        "DX-EXE-012",
                        IssueLevel.BLOCKING,
                        f"执行器 {executor} 需要逻辑资源 {resource}，"
                        f"但 {profile.profile_version} 的目录里没有它",
                        object_key=executor,
                    )
                )
        if not context.target.scope_available(scope):
            issues.append(
                make_issue(
                    "DX-EXE-012",
                    IssueLevel.BLOCKING,
                    f"环境 {context.environment_key} 未配置 {scope.value} 凭证范围，"
                    f"执行器 {executor} 无法执行",
                    object_key=executor,
                )
            )
    issues.extend(_authority_issues(context, executor_types))
    return issues


def _authority_issues(context: RunContext, executor_types: list[str]) -> list[Issue]:
    """连接测试报告决定"这个范围的账户到底有没有权限"（DX-EXE-012）。

    读的是**已持久化**的连接测试结果：计划生成不再打 DHIS2（它可能是无网络时就该给出的
    结论），权限缺失的权威来源就是连接测试报告的那一行。

    **没有这一行同样要阻断**。DHIS2 的元数据导入权限按对象共享判定，没有任何一处能在
    不访问实例的前提下凭空确认「这个账户能导入」；把「从没测过」当成「没有缺失所以通过」，
    等于让一次从未验证过的环境绑定直接拿到提交资格。
    """
    required = {
        EXECUTOR_REQUIREMENTS.get(executor, (CredentialScope.DISCOVERY, ()))[0]
        for executor in executor_types
    }
    with unit_of_work() as session:
        row = session.execute(
            select(ConnectionTestRow)
            .where(ConnectionTestRow.environment_key == context.environment_key)
            .order_by(ConnectionTestRow.tested_at.desc())
            .limit(1)
        ).scalars().first()
    if row is None:
        return [
            make_issue(
                "DX-EXE-012",
                IssueLevel.BLOCKING,
                f"环境 {context.environment_key} 还没有任何连接测试结果，服务账户是否具备"
                "所需权限无法判定（DX-EXE-012）",
                object_key=context.environment_key,
                suggested_action="先执行 test_connection 并覆盖所需凭证范围，再生成执行计划",
            )
        ]
    tested = {str(item) for item in (row.required_scopes or [])}
    out: list[Issue] = []
    for scope in sorted(required, key=lambda item: item.value):
        if scope.value not in tested:
            out.append(
                make_issue(
                    "DX-EXE-012",
                    IssueLevel.BLOCKING,
                    f"最近一次连接测试没有覆盖 {scope.value} 凭证范围，无法判定该范围的权限",
                    object_key=scope.value,
                    suggested_action=f"test_connection 带上 requiredScopes=['{scope.value}'] 重测",
                )
            )
    missing = dict(row.missing_authorities or {})
    for scope_value, authorities in sorted(missing.items()):
        if not authorities:
            continue
        out.append(
            make_issue(
                "DX-EXE-012",
                IssueLevel.BLOCKING,
                f"凭证范围 {scope_value} 缺少权限：{', '.join(str(item) for item in authorities)}",
                object_key=str(scope_value),
                suggested_action="由实例管理员补齐授权后重新连接测试",
            )
        )
    return out


def _deletion_policy_checks(context: RunContext, deletion_payload: bool) -> list[Issue]:
    if not deletion_payload:
        return []
    from factory_dhis2.models.environment import EnvironmentProtectionRow

    with unit_of_work() as session:
        row = session.get(EnvironmentProtectionRow, context.environment_key)
    policy = row.deletion_policy if row else DeletionPolicy.SAFE_ONLY.value
    if policy == DeletionPolicy.FORBID.value:
        return [
            make_issue(
                "DX-EXE-038",
                IssueLevel.BLOCKING,
                f"环境 {context.environment_key} 的删除策略为 FORBID，删除载荷不予执行",
                object_key=context.environment_key,
            )
        ]
    return []


def import_options_are_valid(profile: VersionProfile, options: dict[str, Any]) -> bool:
    """供执行器二次确认参数集仍然被该版本支持（防止计划与执行之间漂移）。"""
    defaults = profile.import_defaults("metadata")
    allowed = profile.import_allowed_parameters("metadata")
    for key in options:
        if key in {"importMode"}:
            continue
        if key not in defaults and key not in allowed:
            return False
    return True


__all__ = [
    "EXECUTOR_REQUIREMENTS",
    "PlanBuild",
    "SUPPORTED_MAINTENANCE",
    "build_and_save",
    "async_import_decision",
    "build_plan",
    "import_options_are_valid",
    "normalize_maintenance",
    "order_executors",
    "resolve_import_options",
    "save_plan",
]
