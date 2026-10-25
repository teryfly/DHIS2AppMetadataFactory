"""factory_dhis2.api — S4 的唯一公共入口（00 文档 6.1、D-13）。

其他子系统只能导入本模块。函数名、位置参数顺序与关键字参数名都是**冻结的**：S1 用
``getattr(factory_dhis2.api, name)`` 按名字解析调用（``factory_control/adapters/
delegating.py``、``adapters/registry.py`` 的 ``METHOD_MAP``），并把返回值按它自己的
camelCase 容忍镜像重新校验；S2 只按 ``factory_blueprint/s4/protocol.py`` 的四个只读函数
调用。改名或调顺序会在调用方运行期失败，因此这里的每一个签名都有出处。

“事务内”指可以并入调用方的工作单元（S0 的 ``unit_of_work`` 会嵌套复用外层会话）；
“外部 I/O”指必须在事务外调用，函数内部自管短事务（00 文档 6.2）。
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import datetime
from typing import Any

from factory_foundation.api import (
    CancellationToken,
    DataClassification,
    ProgressReporter,
    current_context_or_default,
    external_io,
    is_external_io,
    unit_of_work,
)
from sqlalchemy.orm import Session

from factory_dhis2.bootstrap import DX_MIGRATION_SCRIPT_LOCATION, MIGRATION_CONFIG, initialize

# --- 内容格式契约（§1.2-11、DX-CAP-008、DX-DSC-009）-------------------------
from factory_dhis2.contract.capability_profile import (
    CapabilityProfileDocument,
    DiscoveredSchema,
    ResourceEntryDocument,
)
from factory_dhis2.contract.discovery_result import (
    DiscoveredObjectDocument,
    DiscoveryResultDocument,
    OrgUnitDocument,
)

# --- 环境（DX-ENV）---------------------------------------------------------
from factory_dhis2.dto.connection import (
    CapabilityFreshnessInfo,
    CapabilityProfileIndexInfo,
    ConnectionCheck,
)
from factory_dhis2.dto.discovery import (
    DiscoveryObjectCandidate,
    DiscoveryObjectSearch,
    DiscoveryOutcome,
    DiscoveryResultInfo,
    DiscoveryScope,
)
from factory_dhis2.dto.environment import (
    EnvironmentInfo,
    EnvironmentPatch,
    EnvironmentProtection,
    EnvironmentSpec,
    ExpiringCredential,
)
from factory_dhis2.dto.execution import (
    Dhis2ValidationOutcome,
    DiffReportOutcome,
    DriftResult,
    ExecutionOutcome,
    ExecutionStatusInfo,
    PostVerifyOutcome,
    ReconcileResult,
)
from factory_dhis2.dto.issue import Issue
from factory_dhis2.enums import ExecutionStatus
from factory_dhis2.environment import credentials as _credentials
from factory_dhis2.environment import protection as _protection
from factory_dhis2.environment import service as _environment
from factory_dhis2.errors import FeatureNotEnabledError


def _operator(explicit: str | None = None) -> str | None:
    """操作者：调用参数优先，其次 S0 的操作上下文（MVP 的 ``X-Operator``，D-16）。"""
    if explicit:
        return explicit
    context = current_context_or_default()
    return context.operator_name if context else None


def create_environment(
    spec: Mapping[str, Any] | EnvironmentSpec,
    *,
    operator: str | None = None,
    session: Session | None = None,
) -> EnvironmentInfo:
    """登记目标环境（事务内，SRS 7.1）。"""
    resolved = spec if isinstance(spec, EnvironmentSpec) else EnvironmentSpec.model_validate(dict(spec))
    with unit_of_work(session) as sess:
        return _environment.create_environment(
            resolved, session=sess, operator=_operator(operator)
        )


def update_environment(
    environment_key: str,
    patch: Mapping[str, Any] | EnvironmentPatch,
    *,
    operator: str | None = None,
    session: Session | None = None,
) -> EnvironmentInfo:
    resolved = patch if isinstance(patch, EnvironmentPatch) else EnvironmentPatch.model_validate(dict(patch))
    with unit_of_work(session) as sess:
        return _environment.update_environment(
            environment_key, resolved, session=sess, operator=_operator(operator)
        )


def disable_environment(
    environment_key: str, *, operator: str | None = None, session: Session | None = None
) -> None:
    with unit_of_work(session) as sess:
        _environment.disable_environment(
            environment_key, session=sess, operator=_operator(operator)
        )


def get_environment(
    environment_key: str, *, session: Session | None = None
) -> EnvironmentInfo | None:
    """未知环境返回 ``None``：S1 用它做“环境是否存在”的判断（``runs.py:85``）。"""
    with unit_of_work(session) as sess:
        return _environment.get_environment(environment_key, session=sess)


def list_environments(*, session: Session | None = None) -> list[EnvironmentInfo]:
    with unit_of_work(session) as sess:
        return _environment.list_environments(session=sess)


def get_protection(
    environment_key: str,
    *,
    now: datetime | None = None,
    session: Session | None = None,
) -> EnvironmentProtection:
    """实时读取，永不缓存（CP-WF-027）：``withinCommitWindow`` 取决于此刻。"""
    with unit_of_work(session) as sess:
        return _protection.get_protection(environment_key, session=sess, now=now)


def set_emergency_lock(
    environment_key: str,
    locked: bool,
    reason: str | None = None,
    operator: str | None = None,
    *,
    session: Session | None = None,
) -> dict[str, Any]:
    with unit_of_work(session) as sess:
        return _protection.set_emergency_lock(
            environment_key, locked, reason, _operator(operator) or "system", session=sess
        )


def list_expiring_credentials(
    within_days: int | None = None, *, session: Session | None = None
) -> list[ExpiringCredential]:
    """DX-ENV-006：默认 14 天内到期（可由配置改），由 S1 的每日任务调用并通知。"""
    from factory_dhis2.config import get_settings

    resolved = within_days if within_days is not None else get_settings().credential_expiry_warning_days
    with unit_of_work(session) as sess:
        return _credentials.list_expiring_credentials(within_days=resolved, session=sess)


# --- 能力与发现（DX-CAP / DX-DSC）----------------------------------------

from factory_dhis2.capability import service as _capability
from factory_dhis2.connection import tester as _connection
from factory_dhis2.discovery import service as _discovery


def get_last_connection_test(
    environment_key: str, *, session: Session | None = None
) -> ConnectionCheck | None:
    """最近一次连接测试结果（DX-CON-005）：只读 ``dx`` 数据，**不访问 DHIS2**。

    部署控制台要在零 DHIS2 调用的前提下显示连接状态（S1 CP-UI-012、DX-AC-11）。没有记录时
    返回 ``None``，调用方据此区分"从未测过"与"测过但失败"。
    """
    with unit_of_work(session) as sess:
        return _connection.latest_connection_test(sess, environment_key)


def last_connection_check(
    environment_key: str, *, session: Session | None = None
) -> ConnectionCheck | None:
    """``get_last_connection_test`` 的别名：S1 的 ``ExecutionAdapter.METHOD_MAP`` 用的名字。

    SRS 7.1 写的是 ``get_last_connection_test(key)``，而 S1 已经按 ``last_connection_check``
    落地了解析（``adapters/registry.py``、``services/environments.py:127``）。这里两个名字都
    提供，是为了让 S1 现在就拿得到这条只读查询；**命名分歧要由文档或 S1 收敛成一个**，长期
    并存只会让"哪个是正式入口"再度含糊。
    """
    return get_last_connection_test(environment_key, session=session)


def get_capability_profile_index(
    environment_key: str, *, session: Session | None = None
) -> CapabilityProfileIndexInfo | None:
    """最新 Capability Profile 索引（DX-CAP-009）：只读 ``dx`` 数据，**不访问 DHIS2**。

    与 :func:`check_capability_freshness` 的区别是本函数不实时读实例版本，因此可以出现在
    列表页与聚合视图里；需要"现在还有效吗"的答案时用那个。
    """
    with unit_of_work(session) as sess:
        return _capability.latest_index(sess, environment_key)


@external_io
def test_connection(
    environment_key: str,
    required_scopes: Sequence[str] | None = None,
    *,
    idempotency_key: str,
    operator: str | None = None,
) -> ConnectionCheck:
    """只读连接测试（外部 I/O，SRS 7.1）：禁止试写（DX-CON-003）。"""
    return _connection.test_connection(
        environment_key,
        required_scopes,
        idempotency_key=idempotency_key,
        operator=_operator(operator),
    )


@external_io
def check_capability_freshness(environment_key: str) -> CapabilityFreshnessInfo:
    """有效 / 已过期 / 版本已变化（DX-CAP-006，S1 在每个 Run 步骤前调用）。"""
    return _capability.freshness(environment_key)


@external_io
def probe_capabilities(
    environment_key: str,
    *,
    idempotency_key: str,
    operator: str | None = None,
    schema_types: Sequence[str] | None = None,
) -> str | None:
    """合成并持久化新的 CAPABILITY_PROFILE 产物，返回**产物 ID**（S1 直接把它当字符串用）。"""
    return _capability.probe(
        environment_key,
        idempotency_key=idempotency_key,
        operator=_operator(operator),
        schema_types=schema_types,
    )


@external_io
def discover(
    environment_key: str,
    project_id: str | None,
    scope: str | DiscoveryScope | Mapping[str, Any] | None = None,
    explicit_refs: Sequence[Mapping[str, Any]] | None = None,
    *,
    idempotency_key: str,
    progress: ProgressReporter | None = None,
    cancel: CancellationToken | None = None,
) -> DiscoveryOutcome:
    """元数据发现（外部 I/O，SRS 7.2）：只用 discovery 凭证范围，禁止写入（DX-DSC-004）。"""
    return _discovery.discover(
        environment_key,
        project_id,
        scope,
        explicit_refs,
        idempotency_key=idempotency_key,
        progress=progress,
        cancel=cancel,
    )


def latest_discovery_result(
    environment_key: str,
    project_id: str | None = None,
    *,
    session: Session | None = None,
) -> str | None:
    """该环境（可限定项目）最近一次发现结果产物 ID，供 S2/S1 直接读。"""
    with unit_of_work(session) as sess:
        row = _discovery.latest_discovery(sess, environment_key, project_id)
        return row.artifact_id if row else None


def list_discovery_results(
    environment_key: str,
    project_id: str | None = None,
    scope: str | None = None,
    *,
    limit: int = 50,
    offset: int = 0,
    session: Session | None = None,
) -> list[DiscoveryResultInfo]:
    """列出已存储的发现索引（S5IF-ENV11：审阅与部署视图）。"""
    return _discovery.list_discovery_results(
        environment_key,
        project_id,
        scope,
        limit=limit,
        offset=offset,
        session=session,
    )


@external_io
def search_discovery_objects(
    discovery_artifact_id: str,
    dhis2_type: str | None = None,
    query: str | None = None,
    *,
    limit: int = 50,
    offset: int = 0,
) -> DiscoveryObjectSearch:
    """在指定发现结果里检索候选对象（DX-DSC-011）：只读产物内容，**不访问 DHIS2**。

    环境绑定的人工设置要从该环境的候选对象中选择（S1 CP-UI-005）。发现结果格式归 S4 所有，
    由 S4 解析可避免调用方依赖它的内部结构；DX-AC-12 要求在 5,000 对象规模上分页可用。

    它不碰实例，但**读产物字节是外部 I/O**（S0 的对象存储），所以必须在事务外调用——
    与 S1 的 ``ExecutionAdapter.EXTERNAL_IO_METHODS`` 对它的分类一致。
    """
    return _discovery.search_objects(
        discovery_artifact_id,
        dhis2_type,
        query,
        limit=limit,
        offset=offset,
    )


# --- 只读查询（DX-QRY，供 S2）--------------------------------------------

from factory_dhis2.queries import associated_data as _associated
from factory_dhis2.queries import existence as _existence
from factory_dhis2.queries import uids as _uids


@external_io
def generate_uids(environment_key: str, count: int) -> list[str]:
    """官方 UID 生成接口（``system.id``），不修改实例（DX-QRY-001）。"""
    return _uids.generate_uids(environment_key, count)


@external_io
def uid_exists(environment_key: str, uids: Sequence[str]) -> dict[str, Any]:
    """``{uid: 是否被占用}``。

    值同时携带占用对象的类型与 code（DX-QRY-002），但真值性就等于“是否已存在”，
    因此 S2 的 ``dict(...)`` + ``if not present[uid]`` 用法逐字保持有效。
    """
    return _existence.uid_exists(environment_key, uids)


@external_io
def code_exists(
    environment_key: str, dhis2_type: str, codes: Sequence[str]
) -> dict[str, Any]:
    return _existence.code_exists(environment_key, dhis2_type, codes)


@external_io
def has_associated_data(
    environment_key: str, dhis2_type: str, uid: str, *, program: str | None = None
) -> Any:
    """删除安全检查（DX-QRY-004）：判不出来就返回“有关联数据”，即不允许删除。"""
    return _associated.has_associated_data(environment_key, dhis2_type, uid, program=program)


# --- 执行（DX-EXE）-------------------------------------------------------

from factory_dhis2.cleanup import service as _cleanup
from factory_dhis2.diff import environments as _diff_env
from factory_dhis2.diff import snapshot_vs_current as _diff_snap
from factory_dhis2.execution import commit as _commit
from factory_dhis2.execution import deletion as _deletion
from factory_dhis2.execution import maintenance as _maintenance
from factory_dhis2.execution import manual_check as _manual_check
from factory_dhis2.execution import pipeline as _pipeline
from factory_dhis2.execution import post_verify as _post_verify
from factory_dhis2.execution import reconcile as _reconcile
from factory_dhis2.execution import steps as _steps


@external_io
def prepare_and_validate(
    environment_key: str,
    project_id: str,
    package_artifact_id: str,
    expected_hash: str | None,
    *,
    import_options: dict[str, Any] | None = None,
    maintenance_actions: list[dict[str, Any]] | None = None,
    operator: str,
    idempotency_key: str,
    progress: ProgressReporter | None = None,
    cancel: CancellationToken | None = None,
) -> Dhis2ValidationOutcome:
    """R3：计划 → 漂移 → Snapshot → VALIDATE → 服务端表达式校验（SRS 6.3）。"""
    return _pipeline.prepare_and_validate(
        environment_key,
        project_id,
        package_artifact_id,
        expected_hash,
        import_options=import_options,
        maintenance_actions=maintenance_actions,
        operator=_operator(operator) or operator,
        idempotency_key=idempotency_key,
        progress=progress,
        cancel=cancel,
    )


@external_io
def check_drift(
    environment_key: str,
    package_artifact_id: str,
    *,
    project_id: str | None = None,
    cancel: CancellationToken | None = None,
) -> DriftResult:
    """DX-EXE-021：DEPLOY_GATE 审批与 COMMIT 请求时各调一次（CP-GATE-005/017）。"""
    from factory_dhis2.execution.drift import check_drift as _check

    return _check(environment_key, package_artifact_id, project_id=project_id, cancel=cancel)


@external_io
def commit(
    environment_key: str,
    project_id: str,
    package_artifact_id: str,
    expected_hash: str,
    *,
    operator: str,
    idempotency_key: str,
    progress: ProgressReporter | None = None,
    cancel: CancellationToken | None = None,
) -> ExecutionOutcome:
    """COMMIT（SRS 6.4）：哈希一致、锁定与时间窗、环境锁、再漂移检查，然后提交。"""
    return _commit.commit(
        environment_key,
        project_id,
        package_artifact_id,
        expected_hash,
        operator=_operator(operator) or operator,
        idempotency_key=idempotency_key,
        progress=progress,
        cancel=cancel,
    )


def get_execution_by_key(
    idempotency_key: str, *, session: Session | None = None
) -> ExecutionOutcome | None:
    """DX-EXE-036：COMMIT 重试前唯一的确认路径（CP-GATE-018、AC-EXE-05）。"""
    with unit_of_work(session) as sess:
        row = _steps.find_by_idempotency_key(sess, idempotency_key)
        return _outcome_of(sess, row) if row is not None else None


def get_execution(execution_id: str, *, session: Session | None = None) -> ExecutionOutcome | None:
    with unit_of_work(session) as sess:
        row = _steps.get(sess, execution_id)
        return _outcome_of(sess, row) if row is not None else None


def get_execution_status(
    execution_id: str, *, session: Session | None = None
) -> ExecutionStatusInfo | None:
    """含步骤、是否已生效与各产物 ID（DX-EXE-036）。"""
    with unit_of_work(session) as sess:
        row = _steps.get(sess, execution_id)
        if row is None:
            return None
        return ExecutionStatusInfo(
            execution_id=row.id,
            environment_key=row.environment_key,
            status=_status_of(row.status),
            mode=row.mode,
            applied=row.applied,
            idempotency_key=row.idempotency_key,
            payload_hash=row.payload_hash,
            steps=_steps.steps_as_info(sess, row.id),
            import_report_artifact_id=row.import_report_artifact_id,
            snapshot_artifact_id=row.snapshot_artifact_id,
            report_artifact_id=row.report_artifact_id,
            plan_artifact_id=row.plan_artifact_id,
            async_job_ref=row.async_job_ref,
            detail=row.detail,
        )


@external_io
def reconcile(execution_id: str) -> ReconcileResult:
    """结果不明时的对账：给出判断与证据，不写任何东西（DX-EXE-035）。"""
    return _reconcile.reconcile(execution_id)


def resolve_manual_check(
    execution_id: str,
    outcome: str,
    note: str,
    operator: str,
    *,
    session: Session | None = None,
) -> dict[str, Any]:
    """人工核实结论并释放环境锁（事务内，DX-EXE-031）。"""
    return _manual_check.resolve_manual_check(
        execution_id, outcome, note, _operator(operator) or operator, session=session
    )


@external_io
def post_verify(
    environment_key: str,
    project_id: str,
    package_artifact_id: str,
    execution_id: str | None,
    *,
    operator: str,
    idempotency_key: str,
    progress: ProgressReporter | None = None,
    cancel: CancellationToken | None = None,
) -> PostVerifyOutcome:
    """R5：读回逐字段比对 + 延后表达式校验（SRS 6.5、DX-EXE-040…042）。"""
    return _post_verify.post_verify(
        environment_key,
        project_id,
        package_artifact_id,
        execution_id,
        operator=_operator(operator) or operator,
        idempotency_key=idempotency_key,
        progress=progress,
        cancel=cancel,
    )


@external_io
def import_fixtures(
    environment_key: str,
    project_id: str,
    fixture_artifact_id: str,
    *,
    operator: str,
    idempotency_key: str,
    progress: ProgressReporter | None = None,
    cancel: CancellationToken | None = None,
) -> ExecutionOutcome:
    """R6（M3）：Fixture 导入与演示用户。**本轮未启用**（SRS 12 章 M3）。"""
    raise FeatureNotEnabledError(
        "Fixture import and demo users arrive with M3 (SRS 12 章)：Tracker Data Executor 未启用",
        details={"milestone": "M3", "function": "import_fixtures", "rule": "DX-TRK-001"},
    )


@external_io
def run_maintenance(
    environment_key: str,
    execution_id: str,
    actions: list[dict[str, Any]],
    *,
    operator: str,
    idempotency_key: str,
) -> ExecutionOutcome:
    """DX-EXE-045：只执行计划里已列明并经批准的维护动作，使用 maintenance 范围。"""
    return _maintenance.run_maintenance(
        environment_key,
        execution_id,
        actions,
        operator=_operator(operator) or operator,
        idempotency_key=idempotency_key,
    )


@external_io
def delete_objects(
    environment_key: str,
    project_id: str,
    deletion_artifact_id: str,
    expected_hash: str,
    *,
    operator: str,
    idempotency_key: str,
    progress: ProgressReporter | None = None,
    cancel: CancellationToken | None = None,
) -> ExecutionOutcome:
    """DX-EXE-038：删除候选集作为独立载荷，先 VALIDATE 再重做删除安全检查。"""
    return _deletion.delete_objects(
        environment_key,
        project_id,
        deletion_artifact_id,
        expected_hash,
        operator=_operator(operator) or operator,
        idempotency_key=idempotency_key,
        progress=progress,
        cancel=cancel,
    )


@external_io
def diff_snapshot_vs_current(execution_id: str) -> DiffReportOutcome:
    """DX-DIFF-001 之一：提交前 Snapshot 与提交后状态。"""
    return _diff_snap.diff_snapshot_vs_current(execution_id)


@external_io
def diff_environments(
    project_id: str, key_a: str, key_b: str, *, blueprint_version: str | None = None
) -> DiffReportOutcome:
    """DX-DIFF-001 之二：同一版本在两个环境中的部署状态。"""
    return _diff_env.diff_environments(project_id, key_a, key_b, blueprint_version=blueprint_version)


def delete_project_data(project_id: str, *, session: Session | None = None) -> None:
    """DX-DATA-02：只删项目关联的发现索引；执行记录保留以支撑追溯。"""
    _cleanup.delete_project_data(project_id, session=session)


# --- 内容格式（§1.2-11、DX-CAP-008、DX-DSC-009）-----------------------------


def content_format_schema(artifact_type: str) -> dict[str, Any]:
    """发布给 S2 / S3 的产物内容格式（JSON Schema，机器可读）。

    00 文档 6.1 说每个子系统要把**内容格式**作为契约发布，而 S2/S3 只允许导入
    ``factory_dhis2.api`` —— 格式模型如果只活在 ``factory_dhis2.contract`` 里，调用方要么
    违规深导入、要么照着注释手抄字段，两种都会在下一次改动时悄悄断掉（字段名漂移在
    ``extra="allow"`` 下是不报错的）。这里给出可导出的 Schema，让消费方能做真校验。
    """
    from factory_dhis2.contract import capability_profile as capability_contract
    from factory_dhis2.contract import discovery_result as discovery_contract

    models: dict[str, Any] = {
        "CAPABILITY_PROFILE": capability_contract.CapabilityProfileDocument,
        "DISCOVERY_RESULT": discovery_contract.DiscoveryResultDocument,
    }
    model = models.get(str(artifact_type).upper())
    if model is None:
        raise FeatureNotEnabledError(
            f"S4 没有发布名为 {artifact_type!r} 的产物内容格式",
            details={"requested": artifact_type, "available": sorted(models)},
        )
    schema: dict[str, Any] = model.model_json_schema(by_alias=True)
    return schema


def _status_of(value: str | None) -> ExecutionStatus:
    known = {item.value for item in ExecutionStatus}
    if value in known:
        return ExecutionStatus(value)
    # 进行态（PLANNED/RUNNING）对外一律报告为“未完成且待确认”，S1 只认三个终态
    return ExecutionStatus.NEEDS_MANUAL_CHECK if value == _steps.RUNSTATE else ExecutionStatus.FAILED


def _outcome_of(session: Session, row: Any) -> ExecutionOutcome:
    return ExecutionOutcome(
        execution_id=row.id,
        status=_status_of(row.status),
        report_artifact_id=row.report_artifact_id,
        import_report_artifact_id=row.import_report_artifact_id,
        applied=row.applied,
        payload_hash=row.payload_hash,
        steps=_steps.steps_as_info(session, row.id),
        environment_key=row.environment_key,
        detail=row.detail,
    )


__all__ = [
    # 启动
    "DX_MIGRATION_SCRIPT_LOCATION",
    "MIGRATION_CONFIG",
    "initialize",
    # 环境
    "create_environment",
    "update_environment",
    "disable_environment",
    "get_environment",
    "list_environments",
    "get_protection",
    "set_emergency_lock",
    "list_expiring_credentials",
    "test_connection",
    "get_last_connection_test",
    # S1 的 METHOD_MAP 用的名字（见上面的 docstring）
    "last_connection_check",
    # 能力与发现
    "get_capability_profile_index",
    "check_capability_freshness",
    "probe_capabilities",
    "discover",
    "latest_discovery_result",
    "list_discovery_results",
    "search_discovery_objects",
    # 只读查询
    "generate_uids",
    "uid_exists",
    "code_exists",
    "has_associated_data",
    # 执行
    "prepare_and_validate",
    "check_drift",
    "commit",
    "get_execution_by_key",
    "get_execution",
    "get_execution_status",
    "reconcile",
    "resolve_manual_check",
    "post_verify",
    "import_fixtures",
    "run_maintenance",
    "delete_objects",
    "diff_snapshot_vs_current",
    "diff_environments",
    "delete_project_data",
    # 内容格式（DX-CAP-008、DX-DSC-009）
    "content_format_schema",
    "CapabilityProfileDocument",
    "DiscoveredSchema",
    "ResourceEntryDocument",
    "DiscoveryResultDocument",
    "DiscoveredObjectDocument",
    "OrgUnitDocument",
    # 类型
    "CapabilityFreshnessInfo",
    "CapabilityProfileIndexInfo",
    "ConnectionCheck",
    "DataClassification",
    "Dhis2ValidationOutcome",
    "DiscoveryObjectCandidate",
    "DiscoveryObjectSearch",
    "DiscoveryOutcome",
    "DiscoveryScope",
    "DriftResult",
    "EnvironmentInfo",
    "EnvironmentPatch",
    "EnvironmentProtection",
    "EnvironmentSpec",
    "ExecutionOutcome",
    "ExecutionStatus",
    "ExecutionStatusInfo",
    "ExpiringCredential",
    "DiffReportOutcome",
    "Issue",
    "PostVerifyOutcome",
    "ReconcileResult",
    "external_io",
    "is_external_io",
]
