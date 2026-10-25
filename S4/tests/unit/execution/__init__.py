"""``tests/unit/execution`` 共用的小型 DHIS2 替身与夹具装配。

这一层的被测对象是**执行侧的判定逻辑**（读回比对、对账分类、Diff 行、维护与删除的准入），
不是 HTTP。因此这里不用 ``testing/transport`` 的内存实例（那是另一位成员的文件），而是用一个
鸭子类型的 ``FakeClient``：它与 :class:`factory_dhis2.client.session.Dhis2Client` 在同几个
接缝上对齐（``call`` / ``with_scope`` / ``list_all`` / ``target``），因此被测代码走的仍然是
``execution/reads.py`` 那一条真实读路径、真实逻辑资源名、真实 2.41 资源目录。

用真 Profile 是有意的：如果测试里把逻辑资源名换成任意字符串，``client/resources.resolve``
的“目录里没有就拒绝”这条保证就测不到了（DX-CLI-005）。
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

from factory_foundation.api import (
    ArtifactCreate,
    DataClassification,
    NotFoundError,
    register_artifact,
    unit_of_work,
)
from sqlalchemy import select

from factory_dhis2.artifacts import register_staged, stage_archive, stage_document
from factory_dhis2.client.auth import CredentialProvider
from factory_dhis2.enums import (
    AuthMode,
    CredentialScope,
    EnvironmentClass,
    EnvironmentStatus,
    LlmDataPolicy,
)
from factory_dhis2.execution.context import RunContext
from factory_dhis2.payload.package import (
    MANIFEST_FILE,
    METADATA_FILE,
    MetadataPackage,
    read_package,
)
from factory_dhis2.profiles import registry
from factory_dhis2.target import EnvironmentTarget

PROJECT = "prj-exec-0001"
ENVIRONMENT = "env-unit"
BASE_URL = "https://dhis2.example/test"

#: 会被真实 Client 视为写操作的逻辑资源（断言“验证不写实例”时用）
WRITE_RESOURCES: frozenset[str] = frozenset(
    {"metadata.import", "metadata.delete", "tracker.import", "dataStore.write", "apps.upload", "users.create"}
)


@dataclass
class Call:
    """一次被记录的调用。"""

    logical_name: str
    scope: CredentialScope
    dhis2_type: str | None = None
    path_args: dict[str, str] = field(default_factory=dict)
    params: dict[str, str] = field(default_factory=dict)
    body: Any = None

    @property
    def is_write(self) -> bool:
        return self.logical_name in WRITE_RESOURCES


class FakeResponse:
    def __init__(self, status: int = 200, payload: Any = None, headers: dict[str, str] | None = None) -> None:
        self.status = status
        self.payload = payload
        self.headers = headers or {}

    @property
    def ok(self) -> bool:
        return 200 <= self.status < 300

    def header(self, name: str) -> str | None:
        for key, value in self.headers.items():
            if key.lower() == name.lower():
                return value
        return None

    def job_ref(self) -> str | None:
        return self.header("location")


class FakeClient:
    """内存 DHIS2：对象表 + 调用记录。"""

    def __init__(
        self,
        target: EnvironmentTarget,
        *,
        scope: CredentialScope = CredentialScope.DISCOVERY,
        objects: dict[tuple[str, str], dict[str, Any]] | None = None,
        reports: dict[str, dict[str, Any]] | None = None,
        expression_verdicts: dict[str, Any] | None = None,
        calls: list[Call] | None = None,
        responses: dict[str, Any] | None = None,
    ) -> None:
        self.target = target
        self.default_scope = scope
        self.objects: dict[tuple[str, str], dict[str, Any]] = objects or {}
        self.reports = reports or {}
        self.expression_verdicts = expression_verdicts or {}
        self.responses = responses or {}
        self.calls: list[Call] = calls if calls is not None else []

    # --- 与 Dhis2Client 同形的接缝 ------------------------------------------

    def with_scope(self, scope: CredentialScope) -> FakeClient:
        # 用 type(self) 而不是写死类名：测试里的子类覆写必须换范围后仍然生效
        return type(self)(
            self.target,
            scope=scope,
            objects=self.objects,
            reports=self.reports,
            expression_verdicts=self.expression_verdicts,
            calls=self.calls,
            responses=self.responses,
        )

    def call(
        self,
        logical_name: str,
        *,
        scope: CredentialScope | None = None,
        dhis2_type: str | None = None,
        path_args: Any | None = None,
        params: Any | None = None,
        body: Any = None,
        extra_headers: Any | None = None,
    ) -> FakeResponse:
        args = dict(path_args or {})
        query = {str(key): str(value) for key, value in (params or {}).items()}
        used_scope = scope or self.default_scope
        self.calls.append(
            Call(logical_name, used_scope, dhis2_type=dhis2_type, path_args=args, params=query, body=body)
        )
        if logical_name == "metadata.read.one":
            row = self.objects.get((str(dhis2_type), str(args.get("uid"))))
            if row is None:
                raise NotFoundError("not found", details={"uid": args.get("uid")})
            return FakeResponse(200, dict(row))
        if logical_name == "metadata.read":
            rows = [
                dict(item)
                for (item_type, _), item in self.objects.items()
                if item_type == dhis2_type
            ]
            return FakeResponse(200, {_collection(dhis2_type or ""): rows, "pager": {"pageCount": 1}})
        if logical_name.endswith(".validate"):
            verdict = self.expression_verdicts.get(logical_name, {"result": True})
            if isinstance(verdict, list):
                used = query.pop("_used", 0)
                verdict = verdict[min(used, len(verdict) - 1)]
            return FakeResponse(200, verdict)
        if logical_name in WRITE_RESOURCES:
            report = self.reports.get(logical_name, _ok_report())
            return FakeResponse(200, report)
        if logical_name == "maintenance.dataCache":
            return FakeResponse(200, self.responses.get(logical_name, {"status": "OK"}))
        if logical_name in self.responses:
            return FakeResponse(200, self.responses[logical_name])
        return FakeResponse(200, {})

    def list_all(
        self,
        dhis2_type: str,
        *,
        fields: str,
        filters: Any = (),
        page_size: int | None = None,
        limit: int | None = None,
        progress: Any | None = None,
        cancel: Any | None = None,
    ) -> Any:
        collected = [",".join(item) for item in filters if item]
        wanted: set[str] | None = None
        for value in collected:
            if "id:in:[" in value:
                inside = value.split("id:in:[", 1)[1].rstrip("]")
                wanted = {item for item in inside.split(",") if item}
        for (item_type, uid), row in sorted(self.objects.items()):
            if item_type != dhis2_type:
                continue
            if wanted is not None and uid not in wanted:
                continue
            yield dict(row)

    # --- 测试侧的查询 -------------------------------------------------------

    @property
    def write_calls(self) -> list[Call]:
        return [item for item in self.calls if item.is_write]

    def scopes_used(self) -> set[str]:
        return {item.scope.value for item in self.calls}

    def resource_names(self) -> list[str]:
        return [item.logical_name for item in self.calls]


def seed_environment(
    _harness: Any,
    *,
    environment_key: str = ENVIRONMENT,
    environment_class: EnvironmentClass = EnvironmentClass.TEST,
    deletion_policy: str = "SAFE_ONLY",
    emergency_lock: bool = False,
    lock_reason: str | None = None,
    commit_windows: list[dict[str, Any]] | None = None,
    timezone: str = "UTC",
    version_constraint: str = "2.41.x",
    connection_tested: bool = True,
) -> str:
    """在 ``dx`` 里放一个环境档案 + 保护设置 + 凭证引用（DB 侧路径都要它）。

    这些行是准入检查的输入（紧急锁定、删除策略、时间窗），不是被测对象本身；用 ORM 直接写
    比调用 ``create_environment`` 少一层，也就不会被另一位成员那边的规则变化带跑。
    """
    from factory_foundation.api import new_id

    from factory_dhis2.models.environment import (
        EnvironmentCredentialRow,
        EnvironmentProtectionRow,
        EnvironmentRow,
    )

    with unit_of_work() as session:
        existing = session.execute(
            select(EnvironmentRow).where(EnvironmentRow.key == environment_key)
        ).scalars().first()
        if existing is None:
            session.add(
                EnvironmentRow(
                    id=new_id("dxenv"),
                    key=environment_key,
                    name=environment_key,
                    base_url=BASE_URL,
                    environment_class=environment_class.value,
                    version_constraint=version_constraint,
                    auth_mode="PAT",
                    tls_required=True,
                    llm_data_policy="PRIVATE_NETWORK",
                    timeout_seconds=60,
                    status="ACTIVE",
                )
            )
            for scope in (
                CredentialScope.DISCOVERY,
                CredentialScope.IMPORT,
                CredentialScope.MAINTENANCE,
            ):
                session.add(
                    EnvironmentCredentialRow(
                        environment_key=environment_key,
                        scope=scope.value,
                        secret_ref=f"vault://s4/{environment_key}/{scope.value}",
                    )
                )
        protection = session.get(EnvironmentProtectionRow, environment_key)
        if protection is None:
            protection = EnvironmentProtectionRow(environment_key=environment_key)
            session.add(protection)
        protection.timezone = timezone
        protection.commit_windows = commit_windows or []
        protection.deletion_policy = deletion_policy
        protection.emergency_lock = emergency_lock
        protection.lock_reason = lock_reason
    if connection_tested:
        # DX-EXE-012 的输入：计划生成时要能从 ``dx.connection_test`` 读到"这个环境的账户
        # 到底有没有所需权限"。没测过就是无法判定，plan 必须阻断，所以这里默认补一行。
        from factory_dhis2.testing import record_connection_test

        record_connection_test(
            environment_key,
            scopes=(
                CredentialScope.DISCOVERY,
                CredentialScope.IMPORT,
                CredentialScope.MAINTENANCE,
            ),
        )
    return environment_key


def make_target(
    *,
    environment_key: str = ENVIRONMENT,
    environment_class: EnvironmentClass = EnvironmentClass.TEST,
    scopes: tuple[CredentialScope, ...] = (
        CredentialScope.DISCOVERY,
        CredentialScope.IMPORT,
        CredentialScope.MAINTENANCE,
    ),
) -> EnvironmentTarget:
    """一个真 Profile + 真凭证提供者构成的环境目标（凭证值来自假解析器，只在内存）。"""
    registry.load_builtin_profiles()
    return EnvironmentTarget(
        key=environment_key,
        name=environment_key,
        base_url=BASE_URL,
        environment_class=environment_class,
        auth_mode=AuthMode.PAT,
        tls_required=True,
        llm_data_policy=LlmDataPolicy.PRIVATE_NETWORK,
        timeout_seconds=60,
        status=EnvironmentStatus.ACTIVE,
        profile=registry.profile_for_version("2.41.10"),
        dhis2_version="2.41.10",
        credentials=CredentialProvider(
            {scope.value: f"vault://s4/{environment_key}/{scope.value}" for scope in scopes},
            auth_mode=AuthMode.PAT,
            resolver=lambda _ref: "irrelevant-token",
        ),
        capability_profile_artifact_id="dxcapfake000000000000000",
    )


def make_run_context(
    package: MetadataPackage,
    *,
    objects: dict[tuple[str, str], dict[str, Any]] | None = None,
    target: EnvironmentTarget | None = None,
    reports: dict[str, dict[str, Any]] | None = None,
    expression_verdicts: dict[str, Any] | None = None,
    responses: dict[str, Any] | None = None,
    scope: CredentialScope = CredentialScope.DISCOVERY,
    client: FakeClient | None = None,
) -> RunContext:
    """装配一个不碰数据库的执行上下文（判定逻辑的输入就是它）。"""
    resolved_target = target or make_target()
    fake = client or FakeClient(
        resolved_target,
        scope=scope,
        objects=objects,
        reports=reports,
        expression_verdicts=expression_verdicts,
        responses=responses,
    )
    if client is not None:
        fake = client.with_scope(scope)
    return RunContext(
        environment_key=resolved_target.key,
        project_id=package.project_id,
        target=resolved_target,
        package=package,
        client=fake,
    )


# --- 载荷与 manifest -------------------------------------------------------

#: BP-CMP-027a 的八个必需段（缺一段 ``require_complete`` 就阻塞）
_ALL_SECTIONS: tuple[str, ...] = (
    "objects",
    "uidToBusinessKey",
    "dispositions",
    "observationBaselines",
    "managedScope",
    "controlledFields",
    "expressionValidation",
    "deletionCandidates",
)


def build_manifest(
    *,
    objects: list[dict[str, Any]],
    uid_map: list[dict[str, Any]],
    dispositions: dict[str, str],
    controlled_fields: dict[str, list[str]],
    expressions: list[dict[str, Any]] | None = None,
    deletion_candidates: list[dict[str, Any]] | None = None,
    managed_scope: list[dict[str, Any]] | None = None,
    baselines: list[dict[str, Any]] | None = None,
    project_id: str = PROJECT,
    blueprint_version: str = "3.1.0",
    environment_key: str = ENVIRONMENT,
) -> dict[str, Any]:
    return {
        "schemaVersion": 1,
        "projectId": project_id,
        "blueprintVersion": blueprint_version,
        "environmentKey": environment_key,
        "objects": objects,
        "uidToBusinessKey": uid_map,
        "dispositions": dispositions,
        "observationBaselines": baselines or [],
        "managedScope": managed_scope or [
            {"uid": row["uid"], "dhis2Type": row["dhis2Type"], "businessKey": row["businessKey"]}
            for row in uid_map
            if row.get("uid")
        ],
        "controlledFields": controlled_fields,
        "expressionValidation": expressions or [],
        "deletionCandidates": deletion_candidates or [],
        # DX-EXE-010：S2 的载荷总带变更摘要；不给它的话计划会多一条"预期变更无从核对"的
        # 警告，那是有意让缺失可见，测试要的是"齐备的载荷"这一侧
        "changeSummary": {
            "created": [key for key, item in dispositions.items() if item == "CREATE"],
            "updated": [key for key, item in dispositions.items() if item == "UPDATE"],
            "reused": [
                key for key, item in dispositions.items() if item in {"REUSE", "REFERENCE"}
            ],
        },
    }


def package_of(manifest: dict[str, Any], payload: dict[str, Any]) -> MetadataPackage:
    """内存里的包（判定逻辑不需要产物存储）。"""
    return MetadataPackage(
        artifact_id="dxpkgfake0000000000000000",
        artifact_hash="sha256:" + "a" * 64,
        payload=payload,
        manifest=manifest,
        missing_sections=[name for name in _ALL_SECTIONS if name not in manifest],
    )


def register_package_artifact(
    payload: dict[str, Any],
    manifest: dict[str, Any],
    *,
    project_id: str = PROJECT,
) -> str:
    """把包登记成真 METADATA_PACKAGE 产物（S0 校验类型归属，创建者按目录必须是 S2）。

    对象存储由 ``harness`` 夹具装好，这里不再替换它——换掉会让同一个测试里的写入与断言落在
    两个不同的存储上。
    """
    files = {
        METADATA_FILE: json.dumps(payload).encode("utf-8"),
        MANIFEST_FILE: json.dumps(manifest).encode("utf-8"),
    }
    handle = stage_archive(files, project_id, "METADATA_PACKAGE")
    with unit_of_work() as session:
        info = register_artifact(
            handle,
            ArtifactCreate(
                project_id=project_id,
                type="METADATA_PACKAGE",
                media_type="application/zip",
                data_classification=DataClassification.CONFIDENTIAL,
                creator_subsystem="S2",
            ),
            session=session,
        )
    return info.id


def register_json_artifact(
    document: dict[str, Any],
    artifact_type: str,
    *,
    project_id: str = PROJECT,
) -> str:
    """登记一个 S4 自己的 JSON 产物（计划、Snapshot 等 DB 侧路径的输入）。"""
    handle = stage_document(document, project_id=project_id, artifact_type=artifact_type)
    with unit_of_work() as session:
        info = register_staged(
            handle,
            project_id=project_id,
            artifact_type=artifact_type,
            session=session,
        )
    return info.id


def snapshot_document(
    objects: list[dict[str, Any]],
    *,
    kind: str = "PRE_COMMIT",
    environment_key: str = ENVIRONMENT,
    payload_objects: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """一份与 :mod:`execution.snapshot` 同形的 Snapshot 文档。"""
    return {
        "schemaVersion": 1,
        "kind": "SNAPSHOT",
        "snapshotKind": kind,
        "environmentKey": environment_key,
        "environmentClass": "TEST",
        "dhis2Version": "2.41.10",
        "takenAt": "2026-01-01T00:00:00+00:00",
        "managedScope": [],
        "payloadObjects": payload_objects or [],
        "objects": objects,
        "objectCount": len(objects),
    }


def package_from_artifact(artifact_id: str) -> MetadataPackage:
    return read_package(artifact_id)


# --- 小工具 ----------------------------------------------------------------

def ok_report() -> dict[str, Any]:
    return _ok_report()


def error_report(message: str = "cannot delete, data values exist") -> dict[str, Any]:
    return {
        "status": "ERROR",
        "response": {
            "status": "ERROR",
            "stats": {"created": 0, "updated": 0, "deleted": 0, "ignored": 1},
            "objects": [
                {
                    "status": "ERROR",
                    "klass": "dataElement",
                    "errCode": "property not found",
                    "message": message,
                    "params": {"id": "de000000001"},
                }
            ],
        },
    }


def _ok_report() -> dict[str, Any]:
    return {
        "status": "OK",
        "response": {
            "status": "SUCCESS",
            "stats": {"created": 1, "updated": 1, "deleted": 0, "ignored": 0},
            "objects": [],
        },
    }


def _collection(dhis2_type: str) -> str:
    profile = registry.profile_for_version("2.41.10")
    return profile.type_resource(dhis2_type).path


__all__ = [
    "BASE_URL",
    "ENVIRONMENT",
    "PROJECT",
    "Call",
    "FakeClient",
    "FakeResponse",
    "MetadataPackage",
    "WRITE_RESOURCES",
    "build_manifest",
    "error_report",
    "make_run_context",
    "seed_environment",
    "make_target",
    "ok_report",
    "register_json_artifact",
    "package_from_artifact",
    "package_of",
    "snapshot_document",
    "register_package_artifact",
]
