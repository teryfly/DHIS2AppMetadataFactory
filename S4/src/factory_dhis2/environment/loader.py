"""从 ``dx`` 行装配 :class:`~factory_dhis2.target.EnvironmentTarget`。

这是**唯一**把数据库行变成 Client 输入的地方，因此“操作未绑定环境”与“用一个过期的
Capability Profile 猜测端点”两件事都只能在这里发生——它选择报错而不是猜测
（DX-BR-02、DX-CAP-003“禁止运行时猜测”）。

没有已持久化的 Capability Profile 时，只有版本 Profile 可用：读系统与 UID 生成这类
与实例差异无关的调用可以进行，任何依赖实例探测结果的操作必须先探测。
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from factory_dhis2.client.auth import CredentialProvider
from factory_dhis2.enums import (
    AuthMode,
    CredentialScope,
    EnvironmentClass,
    EnvironmentStatus,
    LlmDataPolicy,
)
from factory_dhis2.errors import (
    EnvironmentInvalidError,
    ProfileStaleError,
    VersionUnsupportedError,
)
from factory_dhis2.models.environment import (
    EnvironmentCredentialRow,
    EnvironmentRow,
)
from factory_dhis2.models.profile import CapabilityProfileIndexRow
from factory_dhis2.profiles import registry
from factory_dhis2.profiles.base import VersionProfile
from factory_dhis2.target import EnvironmentTarget


def require_environment(session: Session, environment_key: str) -> EnvironmentRow:
    row = session.execute(
        select(EnvironmentRow).where(EnvironmentRow.key == environment_key)
    ).scalars().first()
    if row is None:
        raise EnvironmentInvalidError(
            f"unknown environment {environment_key!r}: S4 keeps no default environment "
            "(DX-ENV-004), the caller must register it first",
            details={"environmentKey": environment_key},
        )
    return row


def latest_profile_index(
    session: Session, environment_key: str
) -> CapabilityProfileIndexRow | None:
    return session.execute(
        select(CapabilityProfileIndexRow)
        .where(
            CapabilityProfileIndexRow.environment_key == environment_key,
            CapabilityProfileIndexRow.stale.is_(False),
        )
        .order_by(CapabilityProfileIndexRow.probed_at.desc())
    ).scalars().first()


def credential_provider(session: Session, row: EnvironmentRow) -> CredentialProvider:
    refs = {
        item.scope: item.secret_ref
        for item in session.execute(
            select(EnvironmentCredentialRow).where(
                EnvironmentCredentialRow.environment_key == row.key
            )
        ).scalars()
    }
    return CredentialProvider(refs, auth_mode=AuthMode(row.auth_mode))


def build_target(
    session: Session,
    environment_key: str,
    *,
    require_probe: bool = False,
    scope_hint: CredentialScope | None = None,
    allow_unprobed: bool = False,
) -> EnvironmentTarget:
    """装配一次调用所需的全部上下文。

    ``require_probe`` 为真时，没有已持久化的 Capability Profile 就是错误——VALIDATE、
    COMMIT 与发现必须按实例真实能力执行，而不是按版本 Profile 的假设执行。

    ``allow_unprobed`` 只服务于**建立**能力记录的那两个入口（连接测试与探测）：它们正是
    第一次问实例“你是什么版本”的动作，要求在它们之前就有能力记录会是先有鸡还是先有蛋。
    没有版本约束时按最新的可用 Profile 走，并且这次调用的结论一定会被写进产物，之后
    所有操作都读那份产物而不是这里的临时选择。
    """
    row = require_environment(session, environment_key)
    target_status = EnvironmentStatus(row.status)
    if target_status is EnvironmentStatus.DISABLED:
        raise EnvironmentInvalidError(
            f"environment {environment_key!r} is DISABLED",
            details={"environmentKey": environment_key, "status": row.status},
        )

    index = latest_profile_index(session, environment_key)
    if index is not None:
        version_profile = _profile_for_series(index.dhis2_version)
        dhis2_version = index.dhis2_version
    elif require_probe:
        raise ProfileStaleError(
            f"environment {environment_key!r} has no persisted Capability Profile; "
            "probe_capabilities must run before this operation (DX-CAP-003)",
            details={"environmentKey": environment_key},
        )
    elif not row.version_constraint and not allow_unprobed:
        raise VersionUnsupportedError(
            f"environment {environment_key!r} has no Capability Profile and no "
            "versionConstraint to select a version profile from",
            details={"environmentKey": environment_key},
        )
    else:
        if row.version_constraint:
            version_profile = _profile_for_series(row.version_constraint)
        else:
            version_profile = registry.newest_profile()
        dhis2_version = row.version_constraint or version_profile.dhis2_series

    target = EnvironmentTarget(
        key=row.key,
        name=row.name,
        base_url=row.base_url,
        environment_class=EnvironmentClass(row.environment_class),
        auth_mode=AuthMode(row.auth_mode),
        tls_required=row.tls_required,
        llm_data_policy=LlmDataPolicy(row.llm_data_policy),
        timeout_seconds=row.timeout_seconds,
        status=target_status,
        profile=version_profile,
        dhis2_version=dhis2_version,
        credentials=credential_provider(session, row),
        capability_profile_artifact_id=index.artifact_id if index else None,
        # DX-EXE-033 的"按环境可配置"：没有这一行赋值，plan 里的阈值判定就永远退回全局值
        async_import_threshold=row.async_import_threshold,
    )
    if scope_hint is not None and not target.scope_available(scope_hint):
        raise EnvironmentInvalidError(
            f"environment {environment_key!r} has no credential for scope "
            f"{scope_hint.value!r}",
            details={"environmentKey": environment_key, "scope": scope_hint.value},
        )
    return target


def _profile_for_series(version_or_constraint: str) -> VersionProfile:
    try:
        return registry.profile_for_version(version_or_constraint)
    except VersionUnsupportedError:
        # 约束写成 "2.41.x" 这类形式时，去掉通配尾再试一次
        cleaned = version_or_constraint.replace(".x", "").replace("*", "").strip("-. ")
        return registry.profile_for_version(cleaned)


__all__ = ["build_target", "credential_provider", "latest_profile_index", "require_environment"]
