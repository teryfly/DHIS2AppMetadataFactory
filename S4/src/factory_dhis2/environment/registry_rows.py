"""spec → ``dx`` 行的映射（单一出入口，避免创建与更新各写一套列名）。"""

from __future__ import annotations

from factory_dhis2.dto.environment import EnvironmentSpec
from factory_dhis2.models.environment import (
    EnvironmentCredentialRow,
    EnvironmentProtectionRow,
    EnvironmentRow,
)


def spec_to_environment_row(environment_id: str, spec: EnvironmentSpec) -> EnvironmentRow:
    return EnvironmentRow(
        id=environment_id,
        key=spec.environment_key,
        name=spec.name,
        base_url=spec.base_url,
        environment_class=spec.environment_class.value,
        version_constraint=spec.version_constraint,
        auth_mode=spec.auth_mode.value,
        tls_required=spec.tls_required,
        llm_data_policy=spec.llm_data_policy.value,
        timeout_seconds=spec.timeout_seconds,
        status=spec.status.value,
        async_import_threshold=spec.async_import_threshold,
        notes=spec.notes,
    )


def spec_to_credentials_rows(
    spec: EnvironmentSpec,
) -> list[EnvironmentCredentialRow]:
    return [
        EnvironmentCredentialRow(
            environment_key=spec.environment_key,
            scope=reference.scope.value,
            secret_ref=reference.secret_ref,
            expires_at=reference.expires_at,
            label=reference.label,
        )
        for reference in sorted(spec.credentials, key=lambda item: item.scope.value)
    ]


def spec_to_protection_row(
    spec: EnvironmentSpec,
) -> EnvironmentProtectionRow | None:
    from factory_dhis2.environment.rules import resolve_deletion_policy
    from factory_dhis2.environment.windows import windows_as_documents

    if spec.protection is None:
        return None
    return EnvironmentProtectionRow(
        environment_key=spec.environment_key,
        timezone=spec.protection.timezone,
        commit_windows=windows_as_documents(spec.protection.commit_windows, spec.protection.timezone),
        # 没填时按类别折算（DX-ENV-002）：PRODUCTION 落到 FORBID，其它落到 SAFE_ONLY
        deletion_policy=resolve_deletion_policy(
            spec.environment_class, spec.protection.deletion_policy
        ).value,
        emergency_lock=spec.protection.emergency_lock,
        required_approvals=spec.protection.required_approvals,
        operator_role=spec.protection.operator_role,
    )


def spec_to_rows(
    environment_id: str, spec: EnvironmentSpec
) -> tuple[EnvironmentRow, list[EnvironmentCredentialRow], EnvironmentProtectionRow | None]:
    return (
        spec_to_environment_row(environment_id, spec),
        spec_to_credentials_rows(spec),
        spec_to_protection_row(spec),
    )


__all__ = [
    "spec_to_credentials_rows",
    "spec_to_environment_row",
    "spec_to_protection_row",
    "spec_to_rows",
]
