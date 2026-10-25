"""Environment 档案的读写（DX-ENV-001…009、SRS 7.1）。

全部函数都是**事务内**的：环境档案是配置数据，改动必须与它的审计记录同事务提交
（D-11、DX-ENV-008）。只有 ``test_connection`` 是外部 I/O，它在 connection 模块里。
"""

from __future__ import annotations

from datetime import UTC, datetime

from factory_foundation.api import new_id
from sqlalchemy import select
from sqlalchemy.orm import Session

from factory_dhis2 import audit
from factory_dhis2.common.hashing_helper import content_hash
from factory_dhis2.dto.environment import (
    CredentialRef,
    EnvironmentInfo,
    EnvironmentPatch,
    EnvironmentSpec,
)
from factory_dhis2.enums import (
    AuthMode,
    EnvironmentClass,
    EnvironmentStatus,
    LlmDataPolicy,
)
from factory_dhis2.environment import rules
from factory_dhis2.environment.protection import ensure_protection_row
from factory_dhis2.environment.registry_rows import spec_to_rows
from factory_dhis2.errors import EnvironmentInUseError, EnvironmentInvalidError
from factory_dhis2.models.environment import (
    EnvironmentCredentialRow,
    EnvironmentRow,
)
from factory_dhis2.models.execution import ExecutionRow
from factory_dhis2.models.profile import CapabilityProfileIndexRow
from factory_dhis2.registries import DX_ID_PREFIXES  # noqa: F401 - 前缀所有者在此声明

#: 进行中或未确认的执行会阻止更换目标（DX-ENV-008）。
_BLOCKING_STATUSES: tuple[str, ...] = ("RUNNING", "NEEDS_MANUAL_CHECK")


def create_environment(spec: EnvironmentSpec, *, session: Session, operator: str | None = None) -> EnvironmentInfo:
    """登记环境（流程需求 6.1 第 1 步）。"""
    rules.validate_spec(spec)
    exists = session.execute(
        select(EnvironmentRow).where(EnvironmentRow.key == spec.environment_key)
    ).scalars().first()
    if exists is not None:
        raise EnvironmentInvalidError(
            f"environment {spec.environment_key!r} already exists",
            details={"environmentKey": spec.environment_key},
        )
    environment_id = new_id("dxenv")
    row, credentials, protection = spec_to_rows(environment_id, spec)
    session.add(row)
    session.add_all(credentials)
    ensure_protection_row(session, row.key, spec.protection)

    audit.record(
        audit.ENVIRONMENT_CREATED,
        object_type="Environment",
        object_id=environment_id,
        after=_summary(session, row),
        operator=operator,
        session=session,
    )
    for reference in credentials:
        audit.record(
            audit.CREDENTIAL_REF_CHANGED,
            object_type="EnvironmentCredential",
            object_id=f"{row.key}/{reference.scope}",
            after={"environmentKey": row.key, "scope": reference.scope, "secretRef": reference.secret_ref},
            operator=operator,
            session=session,
        )
    return describe_environment(session, row)


def update_environment(
    environment_key: str,
    patch: EnvironmentPatch,
    *,
    session: Session,
    operator: str | None = None,
) -> EnvironmentInfo:
    """改环境档案（DX-ENV-008：每次变更写审计；进行中执行时禁改 baseUrl 与 Secret 引用）。"""
    row = _require(session, environment_key)
    current = _spec_of(session, row)
    merged = rules.apply_patch(current, patch)

    if _has_active_execution(session, environment_key):
        if patch.base_url is not None and patch.base_url != row.base_url:
            raise EnvironmentInUseError(
                f"environment {environment_key!r} has an execution in progress; changing "
                "its baseUrl mid-flight is refused (DX-ENV-008)",
                details={"environmentKey": environment_key},
            )
        if patch.credentials is not None:
            raise EnvironmentInUseError(
                f"environment {environment_key!r} has an execution in progress; its Secret "
                "references cannot change (DX-ENV-008)",
                details={"environmentKey": environment_key},
            )

    before = _summary(session, row)
    row.name = merged.name
    row.base_url = merged.base_url
    row.environment_class = merged.environment_class.value
    row.version_constraint = merged.version_constraint
    row.auth_mode = merged.auth_mode.value
    row.tls_required = merged.tls_required
    row.llm_data_policy = merged.llm_data_policy.value
    row.timeout_seconds = merged.timeout_seconds
    row.status = merged.status.value
    row.async_import_threshold = merged.async_import_threshold
    row.notes = merged.notes

    if merged.protection is not None:
        ensure_protection_row(session, row.key, merged.protection, allow_existing=True)

    if patch.credentials is not None:
        _replace_credentials(session, row, merged, operator=operator)

    after = _summary(session, row)
    audit.record(
        audit.ENVIRONMENT_UPDATED,
        object_type="Environment",
        object_id=row.id,
        before=before,
        after=after,
        operator=operator,
        session=session,
    )
    return describe_environment(session, row)


def disable_environment(environment_key: str, *, session: Session, operator: str | None = None) -> None:
    """禁用环境（DX-ENV-009：禁用前必须没有进行中的执行）。"""
    row = _require(session, environment_key)
    rules.can_disable(EnvironmentStatus(row.status))
    if _has_active_execution(session, environment_key):
        raise EnvironmentInUseError(
            f"environment {environment_key!r} still has an execution in progress; "
            "it cannot be disabled (DX-ENV-009)",
            details={"environmentKey": environment_key},
        )
    row.status = EnvironmentStatus.DISABLED.value
    audit.record(
        audit.ENVIRONMENT_DISABLED,
        object_type="Environment",
        object_id=row.id,
        after={"environmentKey": row.key},
        operator=operator,
        session=session,
    )


def get_environment(environment_key: str, *, session: Session) -> EnvironmentInfo | None:
    """S1 的 ``get_environment``：**未知环境返回 None，不抛错**（档案查询语义）。"""
    row = session.execute(
        select(EnvironmentRow).where(EnvironmentRow.key == environment_key)
    ).scalars().first()
    if row is None:
        return None
    return describe_environment(session, row)


def list_environments(*, session: Session) -> list[EnvironmentInfo]:
    rows = session.execute(select(EnvironmentRow).order_by(EnvironmentRow.key)).scalars().all()
    return [describe_environment(session, row) for row in rows]


def describe_environment(session: Session, row: EnvironmentRow) -> EnvironmentInfo:
    credentials = list(
        session.execute(
            select(EnvironmentCredentialRow)
            .where(EnvironmentCredentialRow.environment_key == row.key)
            .order_by(EnvironmentCredentialRow.scope)
        ).scalars()
    )
    index = session.execute(
        select(CapabilityProfileIndexRow)
        .where(CapabilityProfileIndexRow.environment_key == row.key)
        .order_by(CapabilityProfileIndexRow.probed_at.desc())
        .limit(1)
    ).scalars().first()
    return EnvironmentInfo(
        environment_key=row.key,
        name=row.name,
        base_url=row.base_url,
        version=index.dhis2_version if index else None,
        enabled=row.status == EnvironmentStatus.ACTIVE.value,
        environment_class=EnvironmentClass(row.environment_class),
        status=EnvironmentStatus(row.status),
        auth_mode=AuthMode(row.auth_mode),
        tls_required=row.tls_required,
        llm_data_policy=LlmDataPolicy(row.llm_data_policy),
        timeout_seconds=row.timeout_seconds,
        version_constraint=row.version_constraint,
        credentials=[
            CredentialRef(
                scope=item.scope,
                secret_ref=item.secret_ref,
                expires_at=item.expires_at,
                label=item.label,
            )
            for item in credentials
        ],
        profile_version=index.profile_version if index else None,
        secret_refs={item.scope: item.secret_ref for item in credentials},
        notes=row.notes,
        async_import_threshold=row.async_import_threshold,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


def _require(session: Session, environment_key: str) -> EnvironmentRow:
    """按 environmentKey 取行，取不到就是错误（改一个不存在的环境没有意义）。"""
    row = session.execute(
        select(EnvironmentRow).where(EnvironmentRow.key == environment_key)
    ).scalars().first()
    if row is None:
        raise EnvironmentInvalidError(
            f"unknown environment {environment_key!r}",
            details={"environmentKey": environment_key},
        )
    return row


def _spec_of(session: Session, row: EnvironmentRow) -> EnvironmentSpec:
    """把现有行还原成 spec，供补丁叠加（避免补丁与规则各写一半）。"""
    from factory_dhis2.environment.protection import protection_spec_of

    credentials = list(
        session.execute(
            select(EnvironmentCredentialRow).where(
                EnvironmentCredentialRow.environment_key == row.key
            )
        ).scalars()
    )
    return EnvironmentSpec(
        environment_key=row.key,
        name=row.name,
        base_url=row.base_url,
        environment_class=EnvironmentClass(row.environment_class),
        version_constraint=row.version_constraint,
        auth_mode=AuthMode(row.auth_mode),
        credentials=[
            CredentialRef(
                scope=item.scope,
                secret_ref=item.secret_ref,
                expires_at=item.expires_at,
                label=item.label,
            )
            for item in credentials
        ],
        tls_required=row.tls_required,
        llm_data_policy=LlmDataPolicy(row.llm_data_policy),
        timeout_seconds=row.timeout_seconds,
        status=EnvironmentStatus(row.status),
        protection=protection_spec_of(session, row.key),
        async_import_threshold=row.async_import_threshold,
        notes=row.notes,
    )


def _replace_credentials(
    session: Session, row: EnvironmentRow, merged: EnvironmentSpec, *, operator: str | None
) -> None:
    existing = {
        item.scope: item
        for item in session.execute(
            select(EnvironmentCredentialRow).where(
                EnvironmentCredentialRow.environment_key == row.key
            )
        ).scalars()
    }
    wanted = merged.credential_map()
    for scope, reference in wanted.items():
        current = existing.get(scope.value)
        if current is None:
            session.add(
                EnvironmentCredentialRow(
                    environment_key=row.key,
                    scope=scope.value,
                    secret_ref=reference.secret_ref,
                    expires_at=reference.expires_at,
                    label=reference.label,
                )
            )
            if reference.secret_ref != row.key:
                audit.record(
                    audit.CREDENTIAL_REF_CHANGED,
                    object_type="EnvironmentCredential",
                    object_id=f"{row.key}/{scope.value}",
                    before=None,
                    after={"secretRef": reference.secret_ref, "scope": scope.value},
                    operator=operator,
                    session=session,
                )
            continue
        if current.secret_ref != reference.secret_ref:
            audit.record(
                audit.CREDENTIAL_REF_CHANGED,
                object_type="EnvironmentCredential",
                object_id=f"{row.key}/{scope.value}",
                before={"secretRef": current.secret_ref},
                after={"secretRef": reference.secret_ref},
                operator=operator,
                session=session,
            )
            current.secret_ref = reference.secret_ref
        current.expires_at = reference.expires_at
        current.label = reference.label
    # ``existing`` 的键来自数据库行（str），``wanted`` 的键是枚举：不统一就会把仍然有效
    # 的凭证引用整批删掉
    wanted_scopes = {scope.value for scope in wanted}
    for existing_scope, current in existing.items():
        if existing_scope in wanted_scopes:
            continue
        session.delete(current)
        audit.record(
            audit.CREDENTIAL_REF_CHANGED,
            object_type="EnvironmentCredential",
            object_id=f"{row.key}/{existing_scope}",
            before={"secretRef": current.secret_ref},
            after=None,
            operator=operator,
            session=session,
        )


def _has_active_execution(session: Session, environment_key: str) -> bool:
    found = session.execute(
        select(ExecutionRow.id)
        .where(
            ExecutionRow.environment_key == environment_key,
            ExecutionRow.status.in_(_BLOCKING_STATUSES),
        )
        .limit(1)
    ).scalars().first()
    return found is not None


def _summary(session: Session, row: EnvironmentRow) -> dict[str, object]:
    """审计摘要：档案列 + 凭证范围 + 保护设置，只有引用与非敏感配置（DX-BR-03）。

    保护设置也必须进摘要（DX-ENV-008"环境配置的**每次**变更写审计"）：时间窗与删除策略是
    PRODUCTION 的安全边界，只改这些时如果摘要不变，审计里就是 before==after，等于没记。
    """
    from factory_dhis2.environment.protection import protection_spec_of

    # 会话是 autoflush=False：刚 add 的凭证行不 flush 就查不到，审计的 after 会漏掉它们
    session.flush()
    protection = protection_spec_of(session, row.key)
    credential_scopes = sorted(
        item.scope
        for item in session.execute(
            select(EnvironmentCredentialRow).where(
                EnvironmentCredentialRow.environment_key == row.key
            )
        ).scalars()
    )
    return {
        **_row_summary(row),
        "credentialScopes": credential_scopes,
        "protection": (
            None
            if protection is None
            else {
                "timezone": protection.timezone,
                "commitWindows": [
                    window.model_dump(mode="json") for window in protection.commit_windows
                ],
                "deletionPolicy": protection.deletion_policy.value
                if protection.deletion_policy
                else None,
            }
        ),
    }


def _row_summary(row: EnvironmentRow) -> dict[str, object]:
    """档案列本身：不需要会话，也不含保护设置（Gate 指纹按这个算）。"""
    return {
        "environmentKey": row.key,
        "name": row.name,
        "baseUrl": row.base_url,
        "environmentClass": row.environment_class,
        "authMode": row.auth_mode,
        "tlsRequired": row.tls_required,
        "llmDataPolicy": row.llm_data_policy,
        "timeoutSeconds": row.timeout_seconds,
        "status": row.status,
        "versionConstraint": row.version_constraint,
        "asyncImportThreshold": row.async_import_threshold,
        "notes": row.notes,
    }


def state_hash_of(*parts: object) -> str:
    """把若干状态片段合成一个稳定指纹（VALIDATE 的“那一刻环境状态”，DX-EXE-029）。"""
    from factory_foundation.api import structure_hash

    return structure_hash([_plain(part) for part in parts])


def _plain(value: object) -> object:
    if isinstance(value, datetime):
        return value.astimezone(UTC).isoformat()
    return value


def environment_content_hash(row: EnvironmentRow) -> str:
    """环境档案的字节哈希，用于 Gate 指纹与变更检测。"""
    return content_hash(repr(sorted(_row_summary(row).items())).encode("utf-8"))


__all__ = [
    "create_environment",
    "disable_environment",
    "environment_content_hash",
    "get_environment",
    "list_environments",
    "state_hash_of",
    "update_environment",
]
