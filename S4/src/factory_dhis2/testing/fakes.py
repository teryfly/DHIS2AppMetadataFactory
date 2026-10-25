"""S4 公共 API 的内存 Fake（DX-API-001、00 文档 6.1）。

S1 与 S2 在不装 DHIS2、不开 PostgreSQL 的情况下开发测试时就用它。Fake 与真实实现
**同名同签名**，返回同一批 DTO，所以调用方的测试不会因为换成真实实现而改变形状——
除了这里可以造出真实实例不会给的答案：漂移、权限缺失、超时、结果不明。

Fake 不读也不写任何数据库，也不碰网络。一个偷偷去调真实服务的 Fake 会让 S1 的测试依赖
S4 是否健康，正是这个模块要避免的事（同 S2 的 ``testing/fakes.py`` 的立场）。
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

from factory_dhis2.connection.authorities import SCOPE_NOTES
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
    StepInfo,
)
from factory_dhis2.dto.issue import Issue, make_issue
from factory_dhis2.enums import (
    CapabilityFreshness,
    CredentialScope,
    EnvironmentClass,
    EnvironmentStatus,
    ExecutionStatus,
    IssueLevel,
    MaintenanceAction,
    ReconcileOutcome,
    StepName,
    StepStatus,
)
from factory_dhis2.environment import rules
from factory_dhis2.errors import (
    EnvironmentInvalidError,
    EnvironmentLockedError,
    FeatureNotEnabledError,
)

DEFAULT_VERSION = "2.41.10"


@dataclass
class FakeEnvironment:
    spec: EnvironmentSpec
    version: str = DEFAULT_VERSION
    stale: bool = False
    emergency_lock: bool = False
    lock_reason: str | None = None
    within_window: bool = True
    objects: dict[str, dict[str, Any]] = field(default_factory=dict)
    missing_authorities: dict[str, list[str]] = field(default_factory=dict)


@dataclass
class FakeExecution:
    id: str
    environment_key: str
    mode: str
    status: ExecutionStatus
    payload_hash: str | None
    applied: bool | None
    artifacts: dict[str, str] = field(default_factory=dict)
    steps: list[StepInfo] = field(default_factory=list)
    issues: list[Issue] = field(default_factory=list)
    detail: str | None = None


class FakeDhis2Api:
    """``factory_dhis2.api`` 全部函数的内存实现。"""

    def __init__(self, *, clock: datetime | None = None) -> None:
        self.now = clock or datetime(2026, 1, 1, tzinfo=UTC)
        self.environments: dict[str, FakeEnvironment] = {}
        self.executions: dict[str, FakeExecution] = {}
        self.by_key: dict[str, str] = {}
        self.discovery_artifacts: dict[str, str] = {}
        #: :meth:`list_discovery_results` 的历史（S5IF-ENV11）
        self.discovery_history: list[dict[str, Any]] = []
        #: 最近一次连接测试的持久化替身（DX-CON-005）
        self.connection_tests: dict[str, ConnectionCheck] = {}
        self.capability_artifacts: dict[str, str] = {}
        #: 预设：下一次 VALIDATE / COMMIT 的结果
        self.validate_results: list[Dhis2ValidationOutcome] = []
        self.commit_results: list[ExecutionOutcome] = []
        self.drift_results: dict[str, DriftResult] = {}
        self.expiring: list[ExpiringCredential] = []
        self.maintenance_calls: list[tuple[str, list[dict[str, Any]]]] = []
        self.calls: list[tuple[str, tuple[Any, ...]]] = []
        self._counter = 0
        self.uid_prefix = "fake"

    # --- 预设辅助（测试脚本用）--------------------------------------------

    def add_environment(
        self,
        environment_key: str,
        *,
        environment_class: str = EnvironmentClass.TEST.value,
        version: str = DEFAULT_VERSION,
        emergency_lock: bool = False,
        within_window: bool = True,
        missing_authorities: dict[str, list[str]] | None = None,
    ) -> EnvironmentInfo:
        spec = EnvironmentSpec(
            environment_key=environment_key,
            name=environment_key,
            base_url=f"https://{environment_key}.example.org",
            environment_class=EnvironmentClass(environment_class),
            credentials=[
                _credential(scope) for scope in (CredentialScope.DISCOVERY, CredentialScope.IMPORT)
            ],
            protection=None,
        )
        environment = FakeEnvironment(
            spec=spec,
            version=version,
            emergency_lock=emergency_lock,
            within_window=within_window,
            missing_authorities=dict(missing_authorities or {}),
        )
        self.environments[environment_key] = environment
        return self.get_environment(environment_key) or EnvironmentInfo(environment_key=environment_key)

    def add_object(
        self, environment_key: str, dhis2_type: str, uid: str, **content: Any
    ) -> None:
        self._require(environment_key).objects[uid] = {"id": uid, "dhis2Type": dhis2_type, **content}

    def queue_validate(self, outcome: Dhis2ValidationOutcome) -> None:
        self.validate_results.append(outcome)

    def queue_commit(self, outcome: ExecutionOutcome) -> None:
        self.commit_results.append(outcome)

    def set_drift(self, environment_key: str, result: DriftResult) -> None:
        self.drift_results[environment_key] = result

    def mark_stale(self, environment_key: str) -> None:
        self._require(environment_key).stale = True

    # --- 环境 -------------------------------------------------------------

    def create_environment(self, spec: Mapping[str, Any] | EnvironmentSpec) -> EnvironmentInfo:
        self.calls.append(("create_environment", (dict(spec) if isinstance(spec, Mapping) else spec,)))
        resolved = spec if isinstance(spec, EnvironmentSpec) else EnvironmentSpec.model_validate(dict(spec))
        if resolved.environment_key in self.environments:
            # 真实实现拒绝重复登记（DX-ENV-004）：Fake 静默覆盖会把“注册了两次”这个缺陷
            # 变成一次成功的改写，调用方的测试就再也发现不了它。
            raise EnvironmentInvalidError(
                f"environment {resolved.environment_key!r} already exists",
                details={"environmentKey": resolved.environment_key},
            )
        self.environments[resolved.environment_key] = FakeEnvironment(spec=resolved)
        return self.get_environment(resolved.environment_key)  # type: ignore[return-value]

    def update_environment(
        self, environment_key: str, patch: Mapping[str, Any] | EnvironmentPatch
    ) -> EnvironmentInfo:
        self.calls.append(("update_environment", (environment_key, patch)))
        environment = self._require(environment_key)
        resolved = patch if isinstance(patch, EnvironmentPatch) else EnvironmentPatch.model_validate(dict(patch))
        merged = EnvironmentSpec.model_validate({**environment.spec.model_dump(), **resolved.model_dump(exclude_unset=True)})
        environment.spec = merged
        return self.get_environment(environment_key)  # type: ignore[return-value]

    def disable_environment(self, environment_key: str) -> None:
        self.calls.append(("disable_environment", (environment_key,)))
        self._require(environment_key).spec.status = EnvironmentStatus.DISABLED

    def get_environment(self, environment_key: str) -> EnvironmentInfo | None:
        environment = self.environments.get(environment_key)
        if environment is None:
            return None
        spec = environment.spec
        return EnvironmentInfo(
            environment_key=environment_key,
            name=spec.name,
            base_url=spec.base_url,
            version=environment.version,
            enabled=spec.status is EnvironmentStatus.ACTIVE,
            environment_class=spec.environment_class,
            status=spec.status,
            auth_mode=spec.auth_mode,
            tls_required=spec.tls_required,
            llm_data_policy=spec.llm_data_policy,
            timeout_seconds=spec.timeout_seconds,
            version_constraint=spec.version_constraint,
            credentials=list(spec.credentials),
            profile_version="Dhis241Profile",
        )

    def list_environments(self) -> list[EnvironmentInfo]:
        found = [self.get_environment(key) for key in sorted(self.environments)]
        return [item for item in found if item is not None]

    def get_protection(self, environment_key: str) -> EnvironmentProtection:
        environment = self._require(environment_key)
        spec = environment.spec
        windows = []
        if spec.protection is not None:
            from factory_dhis2.environment.windows import windows_as_documents

            windows = windows_as_documents(
                spec.protection.commit_windows, spec.protection.timezone
            )
        return EnvironmentProtection(
            environment_key=environment_key,
            environment_class=spec.environment_class.value,
            commit_windows=windows,
            within_commit_window=environment.within_window,
            emergency_locked=environment.emergency_lock,
            lock_reason=environment.lock_reason,
            data_export_policy={
                "llmDataPolicy": spec.llm_data_policy.value,
                "rank": spec.llm_data_policy.rank,
                "environmentClass": spec.environment_class.value,
            },
            allows_fixture_import=spec.environment_class is not EnvironmentClass.PRODUCTION,
            deletion_policy=(
                # 与真实实现同一折算：没填就按类别取默认（DX-ENV-002）
                rules.resolve_deletion_policy(
                    spec.environment_class,
                    spec.protection.deletion_policy if spec.protection else None,
                ).value
            ),
            timezone=spec.protection.timezone if spec.protection else "UTC",
            status=spec.status.value,
        )

    def set_emergency_lock(
        self, environment_key: str, locked: bool, reason: str | None = None, operator: str | None = None
    ) -> dict[str, Any]:
        self.calls.append(("set_emergency_lock", (environment_key, locked, reason, operator)))
        environment = self._require(environment_key)
        environment.emergency_lock = locked
        environment.lock_reason = reason if locked else None
        return {
            "environmentKey": environment_key,
            "locked": locked,
            "reason": reason,
            "changedAt": self.now.isoformat(),
        }

    def list_expiring_credentials(self, within_days: int = 14) -> list[ExpiringCredential]:
        configured = list(self.expiring)
        for environment in self.environments.values():
            for reference in environment.spec.credentials:
                if reference.expires_at is None:
                    continue
                if reference.expires_at <= self.now + timedelta(days=within_days):
                    configured.append(
                        ExpiringCredential(
                            environment_key=environment.spec.environment_key,
                            secret_ref=reference.secret_ref,
                            expires_at=reference.expires_at,
                            scope=reference.scope.value,
                        )
                    )
        return configured

    def test_connection(
        self,
        environment_key: str,
        required_scopes: Sequence[str] | None = None,
        *,
        idempotency_key: str,
        operator: str | None = None,
    ) -> ConnectionCheck:
        self.calls.append(("test_connection", (environment_key, required_scopes, idempotency_key)))
        environment = self._require(environment_key)
        scopes = list(required_scopes or ["discovery", "import"])
        checks: list[dict[str, Any]] = [
            {"name": "address", "state": "PASSED", "detail": environment.spec.base_url},
            {"name": "tls", "state": "PASSED" if environment.spec.tls_required else "SKIPPED"},
            {"name": "auth", "state": "PASSED", "scope": "discovery"},
            {"name": "version", "state": "PASSED", "detail": environment.version},
        ]
        missing = {
            scope: list(environment.missing_authorities.get(scope, [])) for scope in scopes
        }
        for scope in scopes:
            checks.append(
                {
                    "name": f"scope.{scope}",
                    "state": "FAILED" if missing[scope] else "PASSED",
                    "scope": scope,
                    "detail": SCOPE_NOTES.get(scope, ""),
                    "missingAuthorities": missing[scope],
                }
            )
        ok = not any(item["state"] == "FAILED" for item in checks)
        artifact_id = self.capability_artifacts.setdefault(environment_key, f"art-fake-cap-{environment_key}")
        outcome = ConnectionCheck(
            ok=ok,
            version=environment.version,
            checks=checks,
            capability_profile_artifact_id=artifact_id,
            missing_authorities={key: value for key, value in missing.items() if value},
            message=None if ok else "缺少必要的服务账户权限",
            environment_key=environment_key,
            tested_at=self.now.isoformat(),
            result="PASSED" if ok else "FAILED",
            dhis2_version=environment.version,
            required_scopes=scopes,
            connection_test_id=f"dxcnt-fake-{environment_key}",
            operator=operator,
        )
        # 与真实实现一样：测过就留下可查的那一份（DX-CON-004/005）
        self.connection_tests[environment_key] = outcome
        return outcome

    def get_last_connection_test(self, environment_key: str) -> ConnectionCheck | None:
        """最近一次连接测试的**已存储**结果（DX-CON-005）：不发任何 DHIS2 请求。"""
        self._require(environment_key)
        return self.connection_tests.get(environment_key)

    #: S1 的 adapter 按这个名字解析（见 ``api.last_connection_check``）
    def last_connection_check(self, environment_key: str) -> ConnectionCheck | None:
        return self.get_last_connection_test(environment_key)

    # --- 能力与发现 -------------------------------------------------------

    def get_capability_profile_index(
        self, environment_key: str
    ) -> CapabilityProfileIndexInfo | None:
        """最新 Capability Profile 索引（DX-CAP-009）：只读内存事实，不发 DHIS2 请求。"""
        environment = self._require(environment_key)
        artifact_id = self.capability_artifacts.get(environment_key)
        if artifact_id is None:
            return None
        return CapabilityProfileIndexInfo(
            profile_index_id=f"dxcap-fake-{environment_key}",
            environment_key=environment_key,
            profile_version="Dhis241Profile",
            dhis2_version=environment.version,
            probed_at=self.now,
            stale=environment.stale,
            stale_reason="version-changed" if environment.stale else None,
            artifact_id=artifact_id,
            content_hash="sha256:" + "c" * 64,
        )

    def check_capability_freshness(self, environment_key: str) -> CapabilityFreshnessInfo:
        environment = self._require(environment_key)
        state = (
            CapabilityFreshness.EXPIRED
            if environment.stale
            else CapabilityFreshness.VALID
        )
        return CapabilityFreshnessInfo(
            state=state,
            capability_profile_artifact_id=self.capability_artifacts.get(environment_key),
            version=environment.version,
            profile_version="Dhis241Profile",
        )

    def probe_capabilities(self, environment_key: str, *, idempotency_key: str) -> str | None:
        self.calls.append(("probe_capabilities", (environment_key, idempotency_key)))
        environment = self._require(environment_key)
        environment.stale = False
        artifact_id = f"art-fake-cap-{environment_key}"
        self.capability_artifacts[environment_key] = artifact_id
        return artifact_id

    def discover(
        self,
        environment_key: str,
        project_id: str | None,
        scope: str | Mapping[str, Any] | None = None,
        explicit_refs: Sequence[Mapping[str, Any]] | None = None,
        *,
        idempotency_key: str,
        progress: Any | None = None,
        cancel: Any | None = None,
    ) -> DiscoveryOutcome:
        self.calls.append(("discover", (environment_key, project_id, scope, list(explicit_refs or ()))))
        environment = self._require(environment_key)
        artifact_id = f"art-fake-dsc-{len(self.discovery_artifacts) + 1:04d}"
        self.discovery_artifacts[environment_key] = artifact_id
        self.discovery_history.append(
            {
                "environment_key": environment_key,
                "artifact_id": artifact_id,
                "project_id": project_id,
                "scope": str(scope if isinstance(scope, str) else "RUN_TARGET"),
                "object_count": len(self._require(environment_key).objects),
            }
        )
        if progress is not None:
            progress.report(step="DISCOVERY", completed_fraction=1.0)
        return DiscoveryOutcome(
            discovery_result_artifact_id=artifact_id,
            capability_profile_artifact_id=self.capability_artifacts.get(environment_key),
            version=environment.version,
            default_object_ids={"categoryCombo": "defaultCategoryCombo"},
            object_count=len(environment.objects),
            content_hash="sha256:" + "f" * 64,
        )

    # --- 只读查询 ---------------------------------------------------------

    def latest_discovery_result(
        self, environment_key: str, project_id: str | None = None
    ) -> str | None:
        """该环境（可限定项目）最近一次发现结果产物 ID。

        真实实现读 ``dx.discovery_index``；Fake 读 :meth:`discover` 累积的历史。两边都给
        **一个产物 ID 字符串而不是对象**，因为调用方（S1 的视图、S2 的编译）就直接把它
        当字符串用。
        """
        rows = [
            item
            for item in self.discovery_history
            if item.get("environment_key") == environment_key
            and (project_id is None or item.get("project_id") == project_id)
        ]
        return str(rows[-1]["artifact_id"]) if rows else None

    def content_format_schema(self, artifact_type: str) -> dict[str, Any]:
        """产物内容格式（DX-CAP-008 / DX-DSC-009）。

        格式是纯数据，与实例状态无关，所以 Fake 直接复用真实实现：调用方在 Fake 下拿到的
        Schema 必须与接上真实 S4 后完全一致，否则"照 Fake 写解析"会在切换时静默失效。
        """
        from factory_dhis2.api import content_format_schema

        return content_format_schema(artifact_type)

    def generate_uids(self, environment_key: str, count: int) -> list[str]:
        self.calls.append(("generate_uids", (environment_key, count)))
        if len(self.uid_prefix) >= 11:
            # 前缀占满了 11 位就再也发不出**互不相同**的合法 UID：明确报错，
            # 而不是在循环里空转把测试进程挂住。
            raise ValueError(
                f"uid_prefix {self.uid_prefix!r} leaves no room for an 11-character DHIS2 UID"
            )
        taken = set()
        out: list[str] = []
        while len(out) < count:
            uid = f"{self.uid_prefix}{self._counter:0{11 - len(self.uid_prefix)}d}"
            self._counter += 1
            if len(uid) != 11 or uid in taken:
                continue
            taken.add(uid)
            out.append(uid)
        return out

    def uid_exists(self, environment_key: str, uids: Sequence[str]) -> dict[str, Any]:
        self.calls.append(("uid_exists", (environment_key, tuple(uids))))
        environment = self._require(environment_key)
        return {
            uid: _Occupancy(bool(uid in environment.objects), environment.objects.get(uid, {}))
            for uid in uids
        }

    def code_exists(
        self, environment_key: str, dhis2_type: str, codes: Sequence[str]
    ) -> dict[str, Any]:
        self.calls.append(("code_exists", (environment_key, dhis2_type, tuple(codes))))
        environment = self._require(environment_key)
        by_code = {
            str(item.get("code")): uid for uid, item in environment.objects.items() if item.get("code")
        }
        return {
            code: _Occupancy(
                code in by_code,
                {"uid": by_code.get(code)},
                basis="fake:in-memory-codes",
            )
            for code in codes
        }

    def has_associated_data(
        self, environment_key: str, dhis2_type: str, uid: str, *, program: str | None = None
    ) -> Any:
        self.calls.append(("has_associated_data", (environment_key, dhis2_type, uid)))
        environment = self._require(environment_key)
        item = environment.objects.get(uid, {})
        has_data = bool(item.get("hasAssociatedData", False))
        return _Verdict(has_data, "Fake：按对象预设的 hasAssociatedData 回答")

    # --- 执行 -------------------------------------------------------------

    def prepare_and_validate(
        self,
        environment_key: str,
        project_id: str,
        package_artifact_id: str,
        expected_hash: str | None,
        *,
        import_options: dict[str, Any] | None = None,
        maintenance_actions: list[dict[str, Any]] | None = None,
        operator: str,
        idempotency_key: str,
        progress: Any | None = None,
        cancel: Any | None = None,
    ) -> Dhis2ValidationOutcome:
        self.calls.append(
            ("prepare_and_validate", (environment_key, project_id, package_artifact_id, expected_hash))
        )
        if self.validate_results:
            outcome = self.validate_results.pop(0)
            outcome.execution_id = outcome.execution_id or self._new_execution_id(environment_key, "VALIDATE", expected_hash)
            self.by_key.setdefault(idempotency_key, outcome.execution_id or "unknown")
            self._remember(
                outcome.execution_id,
                mode="VALIDATE",
                environment_key=environment_key,
                payload_hash=outcome.payload_hash,
                steps=outcome.steps,
                artifacts={
                    "plan": outcome.execution_plan_artifact_id,
                    "snapshot": outcome.snapshot_artifact_id,
                    "importReport": outcome.import_report_artifact_id,
                },
            )
            return outcome
        execution_id = self._new_execution_id(
            environment_key, "VALIDATE", expected_hash, idempotency_key=idempotency_key
        )
        steps = [
            StepInfo(step=item.value, status=StepStatus.COMPLETED.value)
            for item in (StepName.PLAN, StepName.DRIFT_CHECK, StepName.SNAPSHOT, StepName.VALIDATE)
        ]
        outcome = Dhis2ValidationOutcome(
            execution_plan_artifact_id=f"art-fake-plan-{execution_id}",
            snapshot_artifact_id=f"art-fake-snap-{execution_id}",
            import_report_artifact_id=f"art-fake-report-{execution_id}",
            payload_artifact_id=package_artifact_id,
            payload_hash=expected_hash or "sha256:" + "0" * 64,
            passed=True,
            import_options=import_options or {"atomicMode": "ALL"},
            maintenance_actions=list(maintenance_actions or []),
            execution_id=execution_id,
            drift=DriftResult(drifted=False),
            environment_state_hash="sha256:" + "1" * 64,
            steps=steps,
        )
        self._remember(
            execution_id,
            mode="VALIDATE",
            environment_key=environment_key,
            payload_hash=outcome.payload_hash,
            steps=outcome.steps,
            artifacts={
                "plan": outcome.execution_plan_artifact_id,
                "snapshot": outcome.snapshot_artifact_id,
                "importReport": outcome.import_report_artifact_id,
            },
        )
        return outcome

    def check_drift(
        self, environment_key: str, package_artifact_id: str, *, project_id: str | None = None
    ) -> DriftResult:
        self.calls.append(("check_drift", (environment_key, package_artifact_id)))
        return self.drift_results.get(environment_key, DriftResult(drifted=False))

    def commit(
        self,
        environment_key: str,
        project_id: str,
        package_artifact_id: str,
        expected_hash: str,
        *,
        operator: str,
        idempotency_key: str,
        progress: Any | None = None,
        cancel: Any | None = None,
    ) -> ExecutionOutcome:
        self.calls.append(("commit", (environment_key, project_id, package_artifact_id, expected_hash)))
        replayed = self.by_key.get(idempotency_key)
        if replayed is not None:
            return self._outcome(replayed)
        environment = self.environments.get(environment_key)
        if environment is not None and environment.emergency_lock:
            raise EnvironmentLockedError(
                f"environment {environment_key!r} is emergency-locked",
                details={"environmentKey": environment_key},
            )
        if self.commit_results:
            outcome = self.commit_results.pop(0)
            outcome.execution_id = outcome.execution_id or self._new_execution_id(
                environment_key, "COMMIT", expected_hash, idempotency_key=idempotency_key
            )
            if not outcome.environment_key:
                # 预置结论没写环境时补上：回放走的是记录里的行，两边必须一字不差。
                outcome.environment_key = environment_key
        else:
            execution_id = self._new_execution_id(
                environment_key, "COMMIT", expected_hash, idempotency_key=idempotency_key
            )
            outcome = ExecutionOutcome(
                execution_id=execution_id,
                status=ExecutionStatus.COMPLETED,
                report_artifact_id=f"art-fake-execution-{execution_id}",
                import_report_artifact_id=f"art-fake-report-{execution_id}",
                applied=True,
                payload_hash=expected_hash,
                environment_key=environment_key,
                steps=[StepInfo(step=StepName.COMMIT.value, status=StepStatus.COMPLETED.value)],
            )
        self._remember(
            outcome.execution_id,
            mode="COMMIT",
            environment_key=outcome.environment_key or environment_key,
            status=outcome.status,
            applied=outcome.applied,
            payload_hash=outcome.payload_hash,
            detail=outcome.detail,
            steps=outcome.steps,
            issues=outcome.issues,
            artifacts={
                "report": outcome.report_artifact_id,
                "importReport": outcome.import_report_artifact_id,
            },
        )
        self.by_key[idempotency_key] = outcome.execution_id or "unknown"
        return outcome

    def get_execution_by_key(self, idempotency_key: str) -> ExecutionOutcome | None:
        execution_id = self.by_key.get(idempotency_key)
        return self._outcome(execution_id) if execution_id else None

    def get_execution(self, execution_id: str) -> ExecutionOutcome | None:
        return self._outcome(execution_id) if execution_id in self.executions else None

    def get_execution_status(self, execution_id: str) -> ExecutionStatusInfo | None:
        outcome = self._outcome(execution_id)
        if outcome is None:
            return None
        row = self.executions[execution_id]
        return ExecutionStatusInfo(
            execution_id=outcome.execution_id or execution_id,
            environment_key=outcome.environment_key or "",
            status=outcome.status,
            # 模式与产物引用取自记录本身：写死 "COMMIT" 会让 VALIDATE 的执行在调用方的
            # 状态视图里显示成提交，而 S1 正是按 mode 决定下一步能不能提交的。
            mode=row.mode,
            applied=outcome.applied,
            idempotency_key=self._key_for(execution_id),
            payload_hash=outcome.payload_hash,
            steps=outcome.steps,
            import_report_artifact_id=outcome.import_report_artifact_id,
            snapshot_artifact_id=row.artifacts.get("snapshot"),
            plan_artifact_id=row.artifacts.get("plan"),
            report_artifact_id=outcome.report_artifact_id,
            detail=outcome.detail,
        )

    def reconcile(self, execution_id: str) -> ReconcileResult:
        self.calls.append(("reconcile", (execution_id,)))
        outcome = self.executions.get(execution_id)
        if outcome is None:
            return ReconcileResult(outcome=ReconcileOutcome.UNKNOWN, evidence={"executionId": execution_id})
        if outcome.applied is True:
            return ReconcileResult(outcome=ReconcileOutcome.APPLIED, evidence={"executionId": execution_id})
        if outcome.applied is False:
            return ReconcileResult(outcome=ReconcileOutcome.NOT_APPLIED, evidence={"executionId": execution_id})
        return ReconcileResult(outcome=ReconcileOutcome.UNKNOWN, evidence={"executionId": execution_id})

    def resolve_manual_check(
        self, execution_id: str, outcome: str, note: str, operator: str
    ) -> dict[str, Any]:
        self.calls.append(("resolve_manual_check", (execution_id, outcome, note, operator)))
        row = self.executions.get(execution_id)
        if row is not None:
            row.status = ExecutionStatus.COMPLETED if outcome == "PASSED" else ExecutionStatus.FAILED
            row.applied = outcome == "PASSED"
        return {"executionId": execution_id, "resolved": True, "releasesLock": True, "note": note}

    def post_verify(
        self,
        environment_key: str,
        project_id: str,
        package_artifact_id: str,
        execution_id: str | None,
        *,
        operator: str,
        idempotency_key: str,
        progress: Any | None = None,
        cancel: Any | None = None,
    ) -> PostVerifyOutcome:
        self.calls.append(("post_verify", (environment_key, project_id, package_artifact_id, execution_id)))
        return PostVerifyOutcome(
            report_artifact_id=f"art-fake-verify-{execution_id or 'none'}",
            consistent=True,
            execution_id=execution_id,
            compared_objects=len(self._require(environment_key).objects),
        )

    def import_fixtures(
        self,
        environment_key: str,
        project_id: str,
        fixture_artifact_id: str,
        *,
        operator: str,
        idempotency_key: str,
        progress: Any | None = None,
        cancel: Any | None = None,
    ) -> ExecutionOutcome:
        # Fake 允许 M3 路径被演练：调用方要能在真实实现缺席时测自己的编排
        environment = self._require(environment_key)
        if environment.spec.environment_class is EnvironmentClass.PRODUCTION:
            raise FeatureNotEnabledError(
                "Fixture 导入禁止用于 PRODUCTION 类环境（DX-TRK-002）",
                details={"environmentKey": environment_key},
            )
        execution_id = self._new_execution_id(environment_key, "FIXTURE", fixture_artifact_id)
        return ExecutionOutcome(
            execution_id=execution_id,
            status=ExecutionStatus.COMPLETED,
            report_artifact_id=f"art-fake-fixture-{execution_id}",
            applied=True,
            environment_key=environment_key,
            demo_user_secret_refs=[{"username": "demo", "secretRef": "memory://dx/demo"}],
        )

    def run_maintenance(
        self,
        environment_key: str,
        execution_id: str,
        actions: list[dict[str, Any]],
        *,
        operator: str,
        idempotency_key: str,
    ) -> ExecutionOutcome:
        self.maintenance_calls.append((environment_key, list(actions)))
        names = [str(item.get("action")) for item in actions]
        unsupported = [name for name in names if name != MaintenanceAction.CLEAR_APP_CACHE.value]
        if unsupported:
            raise FeatureNotEnabledError(
                f"MVP 只支持清理应用缓存，不支持：{unsupported}",
                details={"unsupported": unsupported, "rule": "DX-EXE-013"},
            )
        return ExecutionOutcome(
            execution_id=execution_id,
            status=ExecutionStatus.COMPLETED,
            applied=True,
            environment_key=environment_key,
            report_artifact_id=f"art-fake-maintenance-{execution_id}",
        )

    def delete_objects(
        self,
        environment_key: str,
        project_id: str,
        deletion_artifact_id: str,
        expected_hash: str,
        *,
        operator: str,
        idempotency_key: str,
        progress: Any | None = None,
        cancel: Any | None = None,
    ) -> ExecutionOutcome:
        self.calls.append(("delete_objects", (environment_key, deletion_artifact_id, expected_hash)))
        execution_id = self._new_execution_id(environment_key, "DELETE", expected_hash)
        return ExecutionOutcome(
            execution_id=execution_id,
            status=ExecutionStatus.COMPLETED,
            applied=True,
            payload_hash=expected_hash,
            environment_key=environment_key,
            report_artifact_id=f"art-fake-delete-{execution_id}",
        )

    def diff_snapshot_vs_current(self, execution_id: str) -> DiffReportOutcome:
        return DiffReportOutcome(
            diff_report_artifact_id=f"art-fake-diff-{execution_id}",
            objects=[],
            summary={"created": 0, "updated": 0, "unchanged": 0, "referenceOnly": 0},
        )

    def diff_environments(
        self, project_id: str, key_a: str, key_b: str, *, blueprint_version: str | None = None
    ) -> DiffReportOutcome:
        return DiffReportOutcome(
            diff_report_artifact_id=f"art-fake-diff-{key_a}-{key_b}",
            objects=[],
            summary={
                "environmentA": key_a,
                "environmentB": key_b,
                "blueprintVersion": blueprint_version,
            },
        )

    def delete_project_data(self, project_id: str, *, session: Any | None = None) -> None:
        """DX-DATA-02：只删该项目的发现历史，**执行记录留下**。

        删执行记录会让 Fake 与真实实现给出相反的结论：真实实现连尝试都不做（数据库触发器
        ``dx.retention_guard`` 挡住），因为追溯链要求任一 COMMIT 可回溯到载荷哈希与操作者。
        """
        self.calls.append(("delete_project_data", (project_id,)))
        self.discovery_history = [
            item for item in self.discovery_history if item.get("project_id") != project_id
        ]

    def list_discovery_results(
        self,
        environment_key: str,
        project_id: str | None = None,
        scope: str | None = None,
        *,
        limit: int = 50,
        offset: int = 0,
    ) -> list[DiscoveryResultInfo]:
        """已存储的发现索引（S5IF-ENV11）。Fake 里由 :meth:`discover` 累积。"""
        rows = [
            item
            for item in self.discovery_history
            if item.get("environment_key") == environment_key
            and (project_id is None or item.get("project_id") == project_id)
            and (scope is None or item.get("scope") == scope)
        ]
        return [
            DiscoveryResultInfo(
                artifact_id=str(item["artifact_id"]),
                project_id=item.get("project_id"),
                scope=item.get("scope"),
                object_count=int(item.get("object_count") or 0),
                content_hash=item.get("content_hash"),
                discovered_at=self.now,
                environment_key=environment_key,
            )
            for item in rows[offset : offset + max(1, limit)]
        ]

    def search_discovery_objects(
        self,
        discovery_artifact_id: str,
        dhis2_type: str | None = None,
        query: str | None = None,
        *,
        limit: int = 50,
        offset: int = 0,
    ) -> DiscoveryObjectSearch:
        """在预置的对象集合里检索候选（DX-DSC-011）：与真实实现同样不访问 DHIS2。"""
        entry = next(
            (
                item
                for item in reversed(self.discovery_history)
                if item.get("artifact_id") == discovery_artifact_id
            ),
            None,
        )
        environment_key = str(entry["environment_key"]) if entry else None
        candidates: list[DiscoveryObjectCandidate] = []
        if environment_key is not None:
            environment = self._require(environment_key)
            needle = (query or "").strip().casefold()
            for uid, row in environment.objects.items():
                item_type = str(row.get("dhis2Type") or "")
                if dhis2_type and item_type != dhis2_type:
                    continue
                code = row.get("code")
                name = row.get("name")
                if needle:
                    haystack = " ".join(
                        str(part) for part in (code, name, uid) if part is not None
                    ).casefold()
                    if needle not in haystack:
                        continue
                candidates.append(
                    DiscoveryObjectCandidate(
                        uid=uid,
                        dhis2_type=item_type,
                        code=code if isinstance(code, str) else None,
                        name=name if isinstance(name, str) else None,
                        last_updated=row.get("lastUpdated")
                        if isinstance(row.get("lastUpdated"), str)
                        else None,
                        content_hash=row.get("contentHash"),
                    )
                )
        candidates.sort(key=lambda item: (item.dhis2_type, item.uid))
        size = max(1, limit)
        start = max(0, offset)
        return DiscoveryObjectSearch(
            discovery_artifact_id=discovery_artifact_id,
            dhis2_type=dhis2_type,
            query=query,
            items=candidates[start : start + size],
            total=len(candidates),
            limit=size,
            offset=start,
        )

    def add_discovery_result(
        self, environment_key: str, *, artifact_id: str, scope: str = "RUN_TARGET", object_count: int = 0
    ) -> None:
        """预置一条历史发现，供视图测试使用。"""
        self.discovery_history.append(
            {
                "environment_key": environment_key,
                "artifact_id": artifact_id,
                "scope": scope,
                "object_count": object_count,
            }
        )

    # --- 内部 -------------------------------------------------------------

    def _require(self, environment_key: str) -> FakeEnvironment:
        """未知环境报成**环境**问题：真实实现里 ``loader.require_environment`` 就是这个错误。

        Fake 报别的类型会让调用方的测试“本地绿、接真就红”——它捕获的是 Fake 的错误，
        而不是 S4 的错误（DX-ENV-004：S4 没有默认环境）。
        """
        environment = self.environments.get(environment_key)
        if environment is None:
            raise EnvironmentInvalidError(
                f"unknown environment {environment_key!r}: S4 keeps no default environment "
                "(DX-ENV-004), register it first",
                details={"environmentKey": environment_key},
            )
        return environment

    def _new_execution_id(
        self,
        environment_key: str,
        mode: str,
        payload_hash: str | None,
        *,
        idempotency_key: str | None = None,
    ) -> str:
        execution_id = f"exe-fake-{len(self.executions) + 1:04d}"
        self.executions[execution_id] = FakeExecution(
            id=execution_id,
            environment_key=environment_key,
            mode=mode,
            status=ExecutionStatus.COMPLETED,
            payload_hash=payload_hash,
            applied=True,
        )
        if idempotency_key:
            self.by_key[idempotency_key] = execution_id
        return execution_id

    def _remember(
        self,
        execution_id: str | None,
        *,
        mode: str,
        environment_key: str | None = None,
        status: ExecutionStatus = ExecutionStatus.COMPLETED,
        applied: bool | None = True,
        payload_hash: str | None = None,
        detail: str | None = None,
        steps: list[StepInfo] | None = None,
        issues: list[Issue] | None = None,
        artifacts: Mapping[str, str | None] | None = None,
    ) -> None:
        """把结论写回执行记录（DX-EXE-036）。

        没有这一步，同幂等键的第二次调用会拿到一份**空的**步骤与产物引用：调用方据此写
        的执行状态与第一次不一致，而“回放必须与首次同结论”正是这条契约的全部内容。测试
        预置的执行 ID 也在这里补登记，否则 :meth:`get_execution_by_key` 会查不到行。
        """
        key = execution_id or "unknown"
        row = self.executions.get(key)
        if row is None:
            row = FakeExecution(
                id=key,
                environment_key=environment_key or "",
                mode=mode,
                status=status,
                payload_hash=payload_hash,
                applied=applied,
            )
            self.executions[key] = row
        row.mode = mode
        row.status = status
        row.applied = applied
        row.payload_hash = payload_hash
        row.detail = detail
        row.steps = list(steps or [])
        row.issues = list(issues or [])
        row.artifacts = {name: value for name, value in (artifacts or {}).items() if value}

    def _key_for(self, execution_id: str) -> str | None:
        """执行 ID 反查幂等键（DX-EXE-036 的“按键确认”在这一侧也要看得见）。"""
        return next((key for key, value in self.by_key.items() if value == execution_id), None)

    def _outcome(self, execution_id: str) -> ExecutionOutcome:
        row = self.executions[execution_id]
        return ExecutionOutcome(
            execution_id=row.id,
            status=row.status,
            report_artifact_id=row.artifacts.get("report"),
            import_report_artifact_id=row.artifacts.get("importReport"),
            applied=row.applied,
            payload_hash=row.payload_hash,
            steps=row.steps,
            issues=row.issues,
            environment_key=row.environment_key,
            detail=row.detail,
        )


class _Occupancy(dict[str, Any]):
    """``dict`` 子类：真值性等于“是否存在”，同时带着类型与 code（DX-QRY-002）。

    S2 的调用形态是 ``dict(s4.uid_exists(...))`` 后按真值判断；用普通 dict 装富信息会
    让每次判断都恒为真，从而把“UID 已被占用”这一事实悄悄丢掉。
    """

    def __init__(
        self, exists: bool, detail: Mapping[str, Any] | None = None, *, basis: str = "fake:in-memory-objects"
    ) -> None:
        super().__init__(
            {
                "exists": exists,
                "dhis2Type": (detail or {}).get("dhis2Type"),
                "code": (detail or {}).get("code"),
                "uid": (detail or {}).get("id") or (detail or {}).get("uid"),
                "name": (detail or {}).get("name"),
                # 真实实现总带 ``basis``（DX-QRY-002：结论要看得见依据）；Fake 不给的话，
                # 调用方读 basis 的代码在本地就永远走不到。
                "basis": (detail or {}).get("basis") or basis,
            }
        )

    def __bool__(self) -> bool:
        return bool(self["exists"])

    @property
    def exists(self) -> bool:
        return bool(self["exists"])


class _Verdict(dict[str, Any]):
    """``has_associated_data`` 的返回值：真值性就是“有没有关联数据”（DX-QRY-004）。"""

    def __init__(self, has_data: bool, basis: str, evidence: Mapping[str, Any] | None = None) -> None:
        super().__init__(
            {"hasData": has_data, "basis": basis, "evidence": dict(evidence or {})}
        )

    def __bool__(self) -> bool:
        return bool(self["hasData"])

    @property
    def has_data(self) -> bool:
        """与真实实现的 ``AssociatedDataVerdict.has_data`` 同名同义。"""
        return bool(self["hasData"])


def _credential(scope: CredentialScope) -> Any:
    from factory_dhis2.dto.environment import CredentialRef

    return CredentialRef(scope=scope, secret_ref=f"memory://dx/{scope.value}")


def fake_issue(description: str, *, uid: str | None = None, key: str | None = None) -> Issue:
    """测试脚本用的阻塞问题（Fake 可以造出真实实例未必给的问题）。"""
    return make_issue("DXIS-REFERENCE_ERROR", IssueLevel.BLOCKING, description, object_key=key, object_uid=uid)


__all__ = ["DEFAULT_VERSION", "FakeDhis2Api", "fake_issue"]
