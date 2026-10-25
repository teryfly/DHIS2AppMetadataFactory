"""环境档案的合规规则（DX-ENV-001…009）。

规则集中在一个模块里，创建与更新走同一条判定，避免出现“创建时能过、改一次就绕过”的
缝隙。PRODUCTION 的凭证分离是 **保存时** 拒绝（DX-ENV-005、DX-AC-02），不是运行时提醒：
一个共用了账户的生产环境一旦登记进来，后面所有发现与提交都带着同一个高权限身份。
"""

from __future__ import annotations

from collections.abc import Mapping

from factory_dhis2.client.tls import policy_for
from factory_dhis2.dto.environment import EnvironmentPatch, EnvironmentSpec
from factory_dhis2.enums import (
    CredentialScope,
    DeletionPolicy,
    EnvironmentClass,
    EnvironmentStatus,
)
from factory_dhis2.errors import EnvironmentInvalidError

PRODUCTION_CLASS = EnvironmentClass.PRODUCTION

#: 任何环境都必须至少有 discovery 与 import 之外的最小集合？只有这两项是 MVP 必需的。
REQUIRED_SCOPES: tuple[CredentialScope, ...] = (CredentialScope.DISCOVERY, CredentialScope.IMPORT)


def default_deletion_policy(environment_class: EnvironmentClass) -> DeletionPolicy:
    """DX-ENV-002 的类别默认值：非生产 ``SAFE_ONLY``、PRODUCTION ``FORBID``。

    调用方没填删除策略时按这条折算，而不是在 DTO 里写死一个值 —— 写死会让 PRODUCTION 静默
    拿到非生产的默认，那正是"允许删除生产对象"的由来。
    """
    return (
        DeletionPolicy.FORBID
        if environment_class is EnvironmentClass.PRODUCTION
        else DeletionPolicy.SAFE_ONLY
    )


def resolve_deletion_policy(
    environment_class: EnvironmentClass, policy: DeletionPolicy | None
) -> DeletionPolicy:
    return policy if policy is not None else default_deletion_policy(environment_class)


def validate_spec(spec: EnvironmentSpec) -> None:
    """``create_environment`` 的全部字段规则（DX-ENV-001/002/003/005）。"""
    if not spec.environment_key.strip():
        raise EnvironmentInvalidError("environmentKey must not be empty")
    if spec.environment_key.strip() != spec.environment_key or " " in spec.environment_key:
        raise EnvironmentInvalidError(
            "environmentKey must not contain spaces or surrounding whitespace",
            details={"environmentKey": spec.environment_key},
        )
    if not spec.name.strip():
        raise EnvironmentInvalidError("environment name must not be empty")
    # DX-ENV-001：版本约束是必填的。没有它，"这个环境是不是 2.41"只能靠猜，而猜错的代价
    # 是拿一个不兼容的 Profile 去解析端点与导入参数。
    if not (spec.version_constraint or "").strip():
        raise EnvironmentInvalidError(
            f"environment {spec.environment_key!r} needs a versionConstraint (DX-ENV-001)",
            details={"environmentKey": spec.environment_key, "rule": "DX-ENV-001"},
        )
    # DX-ENV-007：紧急锁定必须走 set_emergency_lock，那里才会留下原因、时刻与变更人并写审计
    if spec.protection is not None and spec.protection.emergency_lock:
        raise EnvironmentInvalidError(
            "emergencyLock cannot be set through the environment profile: use "
            "set_emergency_lock(key, locked, reason), which records who, when and why "
            "(DX-ENV-007)",
            details={"environmentKey": spec.environment_key, "rule": "DX-ENV-007"},
        )
    if not spec.base_url.startswith(("http://", "https://")):
        raise EnvironmentInvalidError(
            f"baseUrl must be an http(s) url: {spec.base_url!r}",
            details={"baseUrl": spec.base_url},
        )
    # DX-CLI-006：TLS 要求与地址必须自洽，登记时就拒绝，不留到第一次调用才发现
    policy_for(spec.base_url, tls_required=spec.tls_required)
    if spec.timeout_seconds <= 0:
        raise EnvironmentInvalidError(
            "timeoutSeconds must be positive", details={"timeoutSeconds": spec.timeout_seconds}
        )

    refs = spec.credential_map()
    for scope in REQUIRED_SCOPES:
        if scope not in refs:
            raise EnvironmentInvalidError(
                f"environment {spec.environment_key!r} needs a Secret reference for "
                f"scope {scope.value!r}",
                details={
                    "environmentKey": spec.environment_key,
                    "missingScope": scope.value,
                    "configured": sorted(item.scope.value for item in refs.values()),
                },
            )

    if spec.environment_class is PRODUCTION_CLASS:
        _validate_production(spec, refs)

    _validate_m4_fields(spec.environment_class, spec.protection)


def _validate_production(
    spec: EnvironmentSpec, refs: Mapping[CredentialScope, object]
) -> None:
    """DX-ENV-002/005：PRODUCTION 的时间窗、删除策略与凭证分离。"""
    discovery = spec.secret_refs().get(CredentialScope.DISCOVERY.value)
    import_ref = spec.secret_refs().get(CredentialScope.IMPORT.value)
    if discovery and import_ref and discovery == import_ref:
        raise EnvironmentInvalidError(
            f"PRODUCTION environment {spec.environment_key!r} must reference different "
            "Secrets for discovery and import (different service accounts)",
            details={
                "environmentKey": spec.environment_key,
                "secretRef": discovery,
                "rule": "DX-ENV-005",
            },
        )
    protection = spec.protection
    if protection is None or not protection.commit_windows:
        raise EnvironmentInvalidError(
            f"PRODUCTION environment {spec.environment_key!r} must configure allowed "
            "commit windows",
            details={"environmentKey": spec.environment_key, "rule": "DX-ENV-002"},
        )
    # 删除策略对 PRODUCTION 是"默认 FORBID"（DX-ENV-002），不填即取该默认，因此这里不
    # 强制显式填写；真正生效的判定在 deletion 与 plan 里按行上的实际取值执行。


def _validate_m4_fields(environment_class: EnvironmentClass, protection: object) -> None:
    """所需审批人数与操作员角色随 M4 引入（DX-ENV-002【调整】）：本轮只登记不生效。"""
    from factory_dhis2.dto.environment import ProtectionSpec

    if not isinstance(protection, ProtectionSpec):
        return
    if protection.required_approvals is not None or protection.operator_role:
        raise EnvironmentInvalidError(
            "requiredApprovals and operatorRole take effect with M4 permissions",
            details={
                "environmentClass": environment_class.value,
                "milestone": "M4",
                "rule": "DX-ENV-002",
            },
        )


def apply_patch(spec: EnvironmentSpec, patch: EnvironmentPatch) -> EnvironmentSpec:
    """把补丁叠加到现有档案上，产出将要保存的新档案（DX-ENV-008）。"""
    changes: dict[str, object] = {}
    for name, value in patch.model_dump(exclude_unset=True).items():
        if value is None and name not in {"credentials", "protection"}:
            continue
        changes[name] = value
    if patch.credentials is not None:
        changes["credentials"] = patch.credentials
    merged = EnvironmentSpec.model_validate({**spec.model_dump(), **changes})
    validate_spec(merged)
    return merged


def can_disable(status: EnvironmentStatus) -> None:
    if status is EnvironmentStatus.DISABLED:
        raise EnvironmentInvalidError(
            "environment is already disabled", details={"status": status.value}
        )


__all__ = [
    "REQUIRED_SCOPES",
    "apply_patch",
    "can_disable",
    "default_deletion_policy",
    "resolve_deletion_policy",
    "validate_spec",
]
