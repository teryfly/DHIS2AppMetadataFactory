"""能力探测与合成（DX-CAP-003/005/006、流程 6.1 第 2 步）。

版本 Profile 说“2.41 应该是这样”，探测说“这个实例实际是这样”，两者合成本环境的
Capability Profile 并作为**带版本的产物**持久化。之后所有人都只读产物：

* 运行时猜测端点或字段是不允许的（DX-CAP-003），因为猜测的错误要到导入时才暴露；
* 实例升级后旧产物必须被标记为过期（DX-CAP-005），否则一次升级之后所有历史能力都在
  指导新的执行。
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from factory_foundation.api import external_io, idempotency, new_id, structure_hash, unit_of_work
from sqlalchemy import select
from sqlalchemy.orm import Session

from factory_dhis2 import audit
from factory_dhis2.artifacts import register_staged, stage_document
from factory_dhis2.capability.probe import ProbeResult, probe_instance
from factory_dhis2.client.session import Dhis2Client
from factory_dhis2.contract.capability_profile import (
    CapabilityProfileDocument,
    as_artifact_content,
    build_capability_profile_document,
)
from factory_dhis2.dto.connection import CapabilityFreshnessInfo, CapabilityProfileIndexInfo
from factory_dhis2.enums import (
    CapabilityFreshness,
    CredentialScope,
    EnvironmentStatus,
    LlmDataPolicy,
)
from factory_dhis2.environment.loader import build_target, require_environment
from factory_dhis2.errors import (
    ProfileStaleError,
    VersionUnsupportedError,
)
from factory_dhis2.models.environment import EnvironmentRow
from factory_dhis2.models.profile import CapabilityProfileIndexRow
from factory_dhis2.registries import IdemScope

#: 一次探测里「核心」的那几类：留给只想快速刷新基线的调用方（``schema_types=`` 可以传它）。
#: 它**不是**默认同步范围——默认是目录里的全部类型，见 :func:`default_schema_types`。
CORE_SCHEMA_TYPES: tuple[str, ...] = (
    "dataElement",
    "optionSet",
    "categoryCombo",
    "categoryOption",
    "program",
    "programStage",
    "trackedEntityAttribute",
    "trackedEntityType",
    "organisationUnit",
    "validationRule",
)


def default_schema_types(profile: Any) -> tuple[str, ...]:
    """Schema 包的默认同步范围：该版本目录里的**全部**类型（DX-CAP-001）。

    曾经只同步 10 个核心类型，产物就替剩下的类型做主：S2 只读产物、不许再猜，读不到的
    字段清单只能当"没有"。全目录同步的代价是一次探测多发二十几个 ``GET /api/schemas``
    （2.41.10 在线实测每个约 1 秒），而探测本来就是人工触发的动作。
    """
    return tuple(profile.supported_types)


@external_io
def probe(
    environment_key: str,
    *,
    idempotency_key: str | None = None,
    operator: str | None = None,
    schema_types: Any | None = None,
) -> str:
    """探测并合成新的 CAPABILITY_PROFILE 产物，返回产物 ID（SRS 7.2）。"""
    with unit_of_work() as session:
        target = build_target(session, environment_key, allow_unprobed=True)
    # 同步范围先进幂等摘要：默认值换了（只同步核心 10 类 → 全目录），同一个幂等键两次调用
    # 必须落在同一份「要同步什么」上，否则重放拿到的产物与本次请求根本不是一回事。
    resolved_types = tuple(schema_types or default_schema_types(target.profile))
    digest = structure_hash(
        {
            "operation": "probe_capabilities",
            "environmentKey": environment_key,
            "schemaTypes": sorted(resolved_types),
        }
    )
    begun = (
        idempotency.begin(IdemScope.CAPABILITY_PROBE, idempotency_key, digest)
        if idempotency_key
        else None
    )
    if begun is not None and begun.already_completed and begun.first_result:
        recorded = str((begun.first_result or {}).get("artifactId") or "")
        if recorded:
            return recorded

    client = Dhis2Client(target, scope=CredentialScope.DISCOVERY)
    result = probe_instance(
        client,
        schema_types=resolved_types,
    )
    artifact_id = publish(
        environment_key=environment_key,
        target=target,
        result=result,
        operator=operator,
        idempotency_key=idempotency_key,
    )
    if idempotency_key and begun is not None:
        idempotency.complete(
            IdemScope.CAPABILITY_PROBE, idempotency_key, {"artifactId": artifact_id}
        )
    return artifact_id


def publish(
    *,
    environment_key: str,
    target: Any,
    result: ProbeResult,
    operator: str | None,
    idempotency_key: str | None = None,
    project_id: str | None = None,
) -> str:
    """合成 → 登记产物 → 登记索引 → 旧索引标记过期 → 审计。"""
    profile = target.profile
    # 版本 Profile 与实际探测到的版本必须是同一系列，否则这份产物会从根上说谎：端点目录、
    # 导入参数与表达式语法都按「另一个版本」的规则写，而 S2 只读产物、不许再猜（DX-CAP-003）。
    # 环境没填 versionConstraint 时 loader 会取最新的 Profile，正是这条检查要挡住的场景。
    if _series(profile.dhis2_series) != _series(result.version):
        raise VersionUnsupportedError(
            f"instance {environment_key!r} reports DHIS2 {result.version}, which is not the "
            f"{profile.dhis2_series} series the profile {profile.profile_version} describes; "
            "refusing to publish a Capability Profile built from the wrong version profile",
            details={
                "environmentKey": environment_key,
                "probedVersion": result.version,
                "profileVersion": profile.profile_version,
                "profileSeries": profile.dhis2_series,
                "rule": "DX-CAP-003",
            },
        )
    document = build_capability_profile_document(
        profile,
        environment_key=environment_key,
        dhis2_version=result.version,
        probed=result.summary(),
        probed_at=result.probed_at,
        schemas=result.schemas,
    )
    owner = project_id or f"env-{environment_key}"
    handle = stage_document(
        as_artifact_content(document), project_id=owner, artifact_type="CAPABILITY_PROFILE"
    )
    with unit_of_work() as session:
        info = register_staged(
            handle,
            project_id=owner,
            artifact_type="CAPABILITY_PROFILE",
            session=session,
        )
        mark_previous_stale(session, environment_key, reason="superseded", operator=operator)
        row = CapabilityProfileIndexRow(
            id=new_id("dxcap"),
            environment_key=environment_key,
            profile_version=profile.profile_version,
            dhis2_version=result.version,
            probed_at=datetime.now(UTC),
            stale=False,
            artifact_id=info.id,
            content_hash=info.content_hash,
            idempotency_key=idempotency_key,
        )
        session.add(row)
        audit.record(
            audit.CAPABILITY_PROBED,
            object_type="CapabilityProfile",
            object_id=row.id,
            project_id=project_id,
            after={
                "environmentKey": environment_key,
                "artifactId": info.id,
                "version": result.version,
                "profileVersion": profile.profile_version,
                "endpointsMissing": len(result.unavailable_resources),
            },
            operator=operator,
            session=session,
        )
        session.flush()
    return info.id


def mark_previous_stale(
    session: Session,
    environment_key: str,
    *,
    reason: str,
    operator: str | None = None,
) -> int:
    """把该环境未过期的能力索引全部标记过期（DX-CAP-005）。"""
    rows = list(
        session.execute(
            select(CapabilityProfileIndexRow).where(
                CapabilityProfileIndexRow.environment_key == environment_key,
                CapabilityProfileIndexRow.stale.is_(False),
            )
        ).scalars()
    )
    for row in rows:
        row.stale = True
        row.stale_reason = reason
        audit.record(
            audit.CAPABILITY_STALE,
            object_type="CapabilityProfile",
            object_id=row.id,
            before={"stale": False},
            after={"stale": True, "reason": reason},
            operator=operator,
            session=session,
        )
    session.flush()
    return len(rows)


@external_io
def freshness(environment_key: str, *, operator: str | None = None) -> CapabilityFreshnessInfo:
    """有效 / 已过期 / 版本已变化（DX-CAP-006，S1 在每个 Run 步骤前调用）。

    这里必须问一次实例的版本：只看数据库里的 ``stale`` 标志无法发现“有人升级了实例但没人
    告诉过我们”。
    """
    with unit_of_work() as session:
        target = build_target(session, environment_key, allow_unprobed=True)
        current = session.execute(
            select(CapabilityProfileIndexRow)
            .where(
                CapabilityProfileIndexRow.environment_key == environment_key,
                CapabilityProfileIndexRow.stale.is_(False),
            )
            .order_by(CapabilityProfileIndexRow.probed_at.desc())
            .limit(1)
        ).scalars().first()
        stored_version = current.dhis2_version if current else None
        indexed = current

    client = Dhis2Client(target, scope=CredentialScope.DISCOVERY)
    try:
        live_version = _live_version(client)
    except VersionUnsupportedError:
        raise
    except Exception as exc:  # noqa: BLE001 - 无法触达时不能假装能力有效
        return _of(
            indexed,
            state=CapabilityFreshness.EXPIRED,
            version=None,
            reason=f"无法读取实例版本：{type(exc).__name__}",
        )

    if stored_version is None:
        with unit_of_work() as session:
            mark_previous_stale(session, environment_key, reason="never-probed", operator=operator)
        return _of(
            None,
            state=CapabilityFreshness.EXPIRED,
            version=live_version,
            reason="该环境还没有 Capability Profile",
        )

    if _series(live_version) != _series(stored_version):
        with unit_of_work() as session:
            mark_previous_stale(session, environment_key, reason="version-changed", operator=operator)
        return _of(
            None if indexed is None else indexed,
            state=CapabilityFreshness.VERSION_CHANGED,
            version=live_version,
            reason=f"实例版本已变为 {live_version}，能力记录基于 {stored_version}",
        )

    stale = _series(live_version) != _series(stored_version) or (
        live_version != stored_version and not _patch_only(live_version, stored_version)
    )
    return _of(
        indexed,
        state=CapabilityFreshness.EXPIRED if stale else CapabilityFreshness.VALID,
        version=live_version,
        reason=None if not stale else "补丁号差异超出了该 Profile 的覆盖范围",
    )


def latest_index(session: Session, environment_key: str) -> CapabilityProfileIndexInfo | None:
    """该环境最新的能力索引行（DX-CAP-009）：**只读 ``dx``，不访问 DHIS2**。

    与 :func:`freshness` 的分工要说清：``freshness`` 会读实例版本做实时判定，因此是外部
    I/O；聚合视图（S1 CP-UI-012）要在零 DHIS2 调用的前提下显示"上次探测结果"，用的就是这个
    函数。新鲜度在这里是**已持久化的结论**（由 ``stale`` 与 ``stale_reason`` 折算），不是重判。

    环境未登记时报**环境**问题（DX-ENV-004）：返回 ``None`` 会让调用方把拼错的键当成
    "该环境还没探测过"，进而去探测一个不存在的环境。
    """
    require_environment(session, environment_key)
    row = session.execute(
        select(CapabilityProfileIndexRow)
        .where(CapabilityProfileIndexRow.environment_key == environment_key)
        .order_by(CapabilityProfileIndexRow.probed_at.desc())
        .limit(1)
    ).scalars().first()
    if row is None:
        return None
    return CapabilityProfileIndexInfo(
        profile_index_id=row.id,
        environment_key=row.environment_key,
        profile_version=row.profile_version,
        dhis2_version=row.dhis2_version,
        probed_at=row.probed_at,
        stale=bool(row.stale),
        stale_reason=row.stale_reason,
        artifact_id=row.artifact_id,
        content_hash=row.content_hash,
    )


def freshness_state_of(index: CapabilityProfileIndexInfo | None) -> CapabilityFreshness:
    """把已持久化的索引折算成新鲜度枚举（DX-CAP-009 的 ``VALID``/``EXPIRED``/``VERSION_CHANGED``）。"""
    if index is None:
        return CapabilityFreshness.EXPIRED
    if not index.stale:
        return CapabilityFreshness.VALID
    if index.stale_reason == "version-changed":
        return CapabilityFreshness.VERSION_CHANGED
    return CapabilityFreshness.EXPIRED


def current_document(session: Session, environment_key: str) -> dict[str, Any]:
    """该环境当前有效的 Capability Profile 文档（S2 与 S3 只读它）。"""
    row = session.execute(
        select(CapabilityProfileIndexRow)
        .where(
            CapabilityProfileIndexRow.environment_key == environment_key,
            CapabilityProfileIndexRow.stale.is_(False),
        )
        .order_by(CapabilityProfileIndexRow.probed_at.desc())
        .limit(1)
    ).scalars().first()
    if row is None:
        raise ProfileStaleError(
            f"environment {environment_key!r} has no current Capability Profile "
            "(DX-CAP-003): probe it before use",
            details={"environmentKey": environment_key},
        )
    from factory_dhis2.artifacts import read_json

    return read_json(row.artifact_id)


def require_current_profile(session: Session, environment_key: str) -> CapabilityProfileDocument:
    """把当前产物解析成文档；过期或缺失就是明确错误，不猜。"""
    from factory_dhis2.contract.capability_profile import CapabilityProfileDocument

    return CapabilityProfileDocument.model_validate(current_document(session, environment_key))


def environment_status(session: Session, environment_key: str) -> EnvironmentRow:
    return require_environment(session, environment_key)


def _of(
    indexed: CapabilityProfileIndexRow | None,
    *,
    state: CapabilityFreshness,
    version: str | None,
    reason: str | None,
) -> CapabilityFreshnessInfo:
    """把索引行的事实一并给出（调用方不必再读一次产物）。"""
    return CapabilityFreshnessInfo(
        state=state,
        capability_profile_artifact_id=indexed.artifact_id if indexed else None,
        version=version,
        profile_version=indexed.profile_version if indexed else None,
        reason=reason,
        artifact_id=indexed.id if indexed else None,
        content_hash=indexed.content_hash if indexed else None,
        dhis2_version=indexed.dhis2_version if indexed else None,
        probed_at=indexed.probed_at if indexed else None,
        stale=indexed.stale if indexed else None,
        stale_reason=indexed.stale_reason if indexed else None,
        # S1 的镜像同时接受 ``state`` 与 ``freshness`` 两种写法，这里一起给出
        freshness=state.value,
    )


def _live_version(client: Dhis2Client) -> str:
    payload = client.call("system.info").payload
    if not isinstance(payload, dict):
        raise VersionUnsupportedError(
            "the instance's system info endpoint did not report a version",
            details={"environmentKey": client.target.key},
        )
    value = payload.get("version") or payload.get("versionName")
    if not isinstance(value, str) or not value:
        raise VersionUnsupportedError(
            f"instance reported no usable version: {value!r}",
            details={"environmentKey": client.target.key},
        )
    return value


def _series(version: str) -> str:
    parts = version.strip().split(".")
    return ".".join(parts[:2]) if len(parts) >= 2 else version


def _patch_only(live: str, stored: str) -> bool:
    """同一系列内的补丁号差异不改变端点形状，因此不算版本变化。"""
    return _series(live) == _series(stored)


def policy_of(environment: EnvironmentRow) -> LlmDataPolicy:
    return LlmDataPolicy(environment.llm_data_policy)


def is_active(environment: EnvironmentRow) -> bool:
    return environment.status == EnvironmentStatus.ACTIVE.value


__all__ = [
    "CORE_SCHEMA_TYPES",
    "default_schema_types",
    "current_document",
    "environment_status",
    "freshness",
    "freshness_state_of",
    "is_active",
    "latest_index",
    "mark_previous_stale",
    "policy_of",
    "probe",
    "publish",
    "require_current_profile",
]
