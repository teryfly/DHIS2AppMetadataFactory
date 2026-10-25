"""S4 单元测试：环境侧 Diff（DX-DIFF）。

三个文件按 diff 包的三个模块分：``report``（行、摘要、产物——两类对比共用的机器）、
``snapshot_vs_current``（一次执行的提交前 ↔ 提交后）、``environments``（同一版本在两个环境）。

这一层的被测对象是**判定与登记**，不是 HTTP，但 Diff 又要读产物、必要时读环境，所以这里的
装配比纯判定测试厚一层：``dx`` 是真的（环境、执行、Snapshot 索引都落库），产物是真的（经
S0 登记后按 ID 读回），实例是内存的 2.41（真实 ``httpx`` transport，因为"LIVE 分支到底有没
有写环境"只能在真链路上证明）。共用装配集中在本文件，是为了让三个模块用同一份 Snapshot
条目形状——Diff 的意义就在于两侧的形状必须能对齐。
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from factory_foundation.api import (
    install_development_backends,
    new_id,
    store_secret,
    unit_of_work,
)

from factory_dhis2.api import create_environment
from factory_dhis2.discovery.canonical import object_hash
from factory_dhis2.dto.environment import CredentialRef, EnvironmentSpec
from factory_dhis2.enums import (
    AuthMode,
    CredentialScope,
    EnvironmentClass,
    EnvironmentStatus,
    ExecutionStatus,
    LlmDataPolicy,
)
from factory_dhis2.execution.snapshot import KIND_PRE_COMMIT
from factory_dhis2.models.execution import ExecutionRow, SnapshotIndexRow
from factory_dhis2.profiles import registry
from factory_dhis2.profiles.base import VersionProfile

registry.load_builtin_profiles()

#: 比较规则来自随包发布的 2.41 Profile（DX-CAP-001），测试不另写一份排除表
PROFILE: VersionProfile = registry.profile_for_version("2.41.10")

PROJECT = "prj-diff-0001"
ENVIRONMENT = "diff-main"
STAMP = "2026-01-01T00:00:00.000"
LATER = datetime(2026, 1, 1, 12, 0, tzinfo=UTC)
BASE = datetime(2026, 1, 1, tzinfo=UTC)
#: 执行行上的载荷哈希：Diff 不参与判定，但产物 ``artifacts`` 段要把它如实带出去
PAYLOAD_HASH = "sha256:" + "c" * 64


def content_of(uid: str, *, name: str = "体重", code: str | None = None, **body: Any) -> dict[str, Any]:
    """2.41 读回投影后真正会活着回来的那几个字段（``FULL_READ_FIELDS``）。"""
    out: dict[str, Any] = {
        "id": uid,
        "code": code or f"code-{uid}",
        "name": name,
        "created": STAMP,
        "lastUpdated": STAMP,
    }
    out.update(body)
    return out


def entry_of(
    uid: str,
    *,
    dhis2_type: str = "dataElement",
    present: bool = True,
    content: dict[str, Any] | None = None,
    business_key: str | None = None,
) -> dict[str, Any]:
    """一份 Snapshot 的对象条目，形状与 :mod:`execution.snapshot` 逐字一致。"""
    body = content if content is not None else (content_of(uid) if present else None)
    entry: dict[str, Any] = {
        "dhis2Type": dhis2_type,
        "uid": uid,
        "present": present,
        "lastUpdated": (body or {}).get("lastUpdated"),
        "contentHash": object_hash(body, PROFILE.volatile_fields) if body else None,
        "content": body,
    }
    if business_key is not None:
        entry["businessKey"] = business_key
    return entry


def payload_object(
    uid: str,
    *,
    dhis2_type: str = "dataElement",
    business_key: str = "bk.weight",
    disposition: str = "CREATE",
) -> dict[str, Any]:
    """Snapshot 的 ``payloadObjects`` 一行：处置只从这儿来（DX-DIFF-001 的"仅引用"判定）。"""
    return {
        "businessKey": business_key,
        "dhis2Type": dhis2_type,
        "uid": uid,
        "disposition": disposition,
    }


def add_execution(
    *,
    environment_key: str,
    project_id: str | None = PROJECT,
    status: str = ExecutionStatus.COMPLETED.value,
    applied: bool | None = True,
    plan_artifact_id: str | None = None,
    payload_artifact_id: str | None = None,
    payload_hash: str | None = PAYLOAD_HASH,
    snapshot_artifact_id: str | None = None,
    completed_at: datetime | None = None,
    execution_id: str | None = None,
) -> str:
    """放一行执行记录（Diff 的候选集输入），返回它的 ID。

    直接写 ORM 而不是走执行管线：本目录测的是**选哪两份 Snapshot 来比**，管线是另一位成员
    的文件。``completed_at`` 缺省按调用顺序往前推，让"最近一次"是可控的而不是时钟巧合。
    """
    made = execution_id or new_id("dxexe")
    with unit_of_work() as session:
        session.add(
            ExecutionRow(
                id=made,
                project_id=project_id,
                environment_key=environment_key,
                mode="COMMIT",
                status=status,
                applied=applied,
                plan_artifact_id=plan_artifact_id,
                payload_artifact_id=payload_artifact_id,
                payload_hash=payload_hash,
                snapshot_artifact_id=snapshot_artifact_id,
                object_count=1,
                completed_at=completed_at or BASE,
                started_at=(completed_at or BASE) - timedelta(minutes=5),
            )
        )
    return made


def add_snapshot_index(
    artifact_id: str,
    *,
    environment_key: str,
    execution_id: str | None,
    kind: str = KIND_PRE_COMMIT,
    at: datetime | None = None,
) -> str:
    """给一份 Snapshot 产物挂上索引行（DX-EXE-022：事后 Diff 从索引里找它）。"""
    made = new_id("dxsnp")
    with unit_of_work() as session:
        session.add(
            SnapshotIndexRow(
                id=made,
                environment_key=environment_key,
                execution_id=execution_id,
                kind=kind,
                scope={"objects": 1},
                artifact_id=artifact_id,
                content_hash="sha256:" + "d" * 64,
                dhis2_version="2.41.10",
                object_count=1,
                created_at_snapshot=at or (BASE + timedelta(minutes=1)),
            )
        )
    return made


def register_environment(
    environment_key: str,
    *,
    environment_class: EnvironmentClass = EnvironmentClass.TEST,
) -> str:
    """登记一个能装配 Client 的环境：凭证是内存 Secret，目标主机是假域名。

    Diff 自己从不建 Client（``diff_environments`` 只读产物与 ``dx`` 行），但「比较规则来自
    哪一份 Profile」要按真实路径回答（DX-CAP-003），所以环境行与凭证引用是真的存在的。
    """
    install_development_backends()
    scopes = (CredentialScope.DISCOVERY, CredentialScope.IMPORT)
    refs = {
        scope: store_secret(
            f"memory://dx/diff/{environment_key}/{scope.value}",
            f"svcacct-reader:{scope.value}",
        )
        for scope in scopes
    }
    create_environment(
        EnvironmentSpec(
            environment_key=environment_key,
            name=f"Diff 用例环境 {environment_key}",
            base_url=f"https://{environment_key}.fake.test",
            environment_class=environment_class,
            version_constraint="2.41.x",
            auth_mode=AuthMode.BASIC,
            tls_required=True,
            llm_data_policy=LlmDataPolicy.PRIVATE_NETWORK,
            timeout_seconds=30,
            status=EnvironmentStatus.ACTIVE,
            credentials=[CredentialRef(scope=scope, secret_ref=refs[scope]) for scope in scopes],
        )
    )
    return environment_key


__all__ = [
    "BASE",
    "ENVIRONMENT",
    "LATER",
    "PAYLOAD_HASH",
    "PROFILE",
    "PROJECT",
    "STAMP",
    "add_execution",
    "add_snapshot_index",
    "content_of",
    "entry_of",
    "payload_object",
    "register_environment",
]
