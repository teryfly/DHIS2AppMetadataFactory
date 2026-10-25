"""环境档案的合规规则（DX-ENV-001/002/003/004/005/008、DX-AC-02）。

规则只在 :mod:`factory_dhis2.environment.rules` 里判定一次，创建与更新共用同一入口；
这些测试不带数据库，因为要证明的是“什么样的档案不允许被登记”，而不是它怎么存。
"""

from __future__ import annotations

import pytest

from factory_dhis2.client.tls import TlsPolicy, policy_for
from factory_dhis2.dto.environment import (
    CommitWindow,
    CredentialRef,
    EnvironmentPatch,
    EnvironmentSpec,
    ProtectionSpec,
)
from factory_dhis2.enums import CredentialScope, DeletionPolicy, EnvironmentClass
from factory_dhis2.environment import rules
from factory_dhis2.environment.registry_rows import (
    spec_to_credentials_rows,
    spec_to_environment_row,
    spec_to_protection_row,
)
from factory_dhis2.errors import Dhis2TlsError, EnvironmentInvalidError

DISCOVERY_REF = "memory://dx/discovery"
IMPORT_REF = "memory://dx/import"


def credentials(
    *scopes: CredentialScope, refs: dict[CredentialScope, str] | None = None
) -> list[CredentialRef]:
    resolved = refs or {
        CredentialScope.DISCOVERY: DISCOVERY_REF,
        CredentialScope.IMPORT: IMPORT_REF,
        CredentialScope.TRACKER: "memory://dx/tracker",
        CredentialScope.DATASTORE: "memory://dx/datastore",
        CredentialScope.APPS: "memory://dx/apps",
        CredentialScope.MAINTENANCE: "memory://dx/maintenance",
    }
    return [
        CredentialRef(scope=scope, secret_ref=resolved[scope])
        for scope in scopes
        if scope in resolved
    ]


def spec(
    *,
    environment_class: EnvironmentClass = EnvironmentClass.TEST,
    refs: dict[CredentialScope, str] | None = None,
    protection: ProtectionSpec | None = None,
    **changes: object,
) -> EnvironmentSpec:
    document: dict[str, object] = {
        "environment_key": "staging",
        "name": "Staging 环境",
        "base_url": "https://dhis.example.org/instance",
        "environment_class": environment_class,
        "version_constraint": "2.41.x",
        "credentials": credentials(
            CredentialScope.DISCOVERY,
            CredentialScope.IMPORT,
            CredentialScope.TRACKER,
            CredentialScope.DATASTORE,
            CredentialScope.APPS,
            CredentialScope.MAINTENANCE,
            refs=refs,
        ),
        "protection": protection,
    }
    document.update(changes)
    return EnvironmentSpec.model_validate(document)


def production_protection() -> ProtectionSpec:
    return ProtectionSpec(
        timezone="Asia/Shanghai",
        commit_windows=[CommitWindow(days=[2, 4], start="20:00", end="23:00")],
        deletion_policy=DeletionPolicy.FORBID,
    )


# --- DX-ENV-005 / DX-AC-02：生产环境的凭证分离 --------------------------------


def test_production_environment_sharing_one_secret_is_refused_on_save() -> None:
    """DX-ENV-005、DX-AC-02：discovery 与 import 引用同一个 Secret 时保存即被拒绝。"""
    shared = "memory://dx/service-account"
    with pytest.raises(EnvironmentInvalidError) as excinfo:
        rules.validate_spec(
            spec(
                environment_class=EnvironmentClass.PRODUCTION,
                refs={
                    CredentialScope.DISCOVERY: shared,
                    CredentialScope.IMPORT: shared,
                },
                protection=production_protection(),
            )
        )
    assert excinfo.value.details["rule"] == "DX-ENV-005"
    assert excinfo.value.details["secretRef"] == shared


def test_production_environment_with_two_service_accounts_is_accepted() -> None:
    """DX-ENV-005：不同引用（不同账户）的生产环境是合规的。"""
    rules.validate_spec(
        spec(
            environment_class=EnvironmentClass.PRODUCTION,
            refs={
                CredentialScope.DISCOVERY: DISCOVERY_REF,
                CredentialScope.IMPORT: IMPORT_REF,
            },
            protection=production_protection(),
        )
    )


def test_non_production_environment_may_share_one_secret() -> None:
    """DX-ENV-005 只约束生产环境：DEV 类共用一个账户不违规。"""
    shared = "memory://dx/one-account"
    rules.validate_spec(
        spec(
            environment_class=EnvironmentClass.DEV,
            refs={CredentialScope.DISCOVERY: shared, CredentialScope.IMPORT: shared},
        )
    )


def test_updating_a_production_environment_into_a_shared_secret_is_refused() -> None:
    """DX-ENV-005、DX-ENV-008：补丁叠加后同样要过规则，不存在“创建能过、改一次就绕过”。"""
    current = spec(
        environment_class=EnvironmentClass.PRODUCTION,
        refs={
            CredentialScope.DISCOVERY: DISCOVERY_REF,
            CredentialScope.IMPORT: IMPORT_REF,
        },
        protection=production_protection(),
    )
    merged_refs = [
        CredentialRef(scope=CredentialScope.DISCOVERY, secret_ref=IMPORT_REF),
        CredentialRef(scope=CredentialScope.IMPORT, secret_ref=IMPORT_REF),
    ]
    with pytest.raises(EnvironmentInvalidError):
        rules.apply_patch(current, EnvironmentPatch(credentials=merged_refs))


# --- DX-ENV-002：生产环境的时间窗与删除策略 -----------------------------------


def test_production_environment_without_commit_windows_is_refused() -> None:
    """DX-ENV-002：空时间窗清单不是一种许可。"""
    with pytest.raises(EnvironmentInvalidError) as excinfo:
        rules.validate_spec(
            spec(
                environment_class=EnvironmentClass.PRODUCTION,
                protection=ProtectionSpec(timezone="UTC", commit_windows=[]),
            )
        )
    assert excinfo.value.details["rule"] == "DX-ENV-002"


def test_production_environment_without_a_protection_block_is_refused() -> None:
    """DX-ENV-002：连保护设置都没有的生产环境不能登记。"""
    with pytest.raises(EnvironmentInvalidError):
        rules.validate_spec(spec(environment_class=EnvironmentClass.PRODUCTION))


def test_commit_window_needs_at_least_one_weekday() -> None:
    """DX-ENV-002：时间窗是“星期 + 时间段”，没有星期的时段无法判定。"""
    with pytest.raises(ValueError):
        CommitWindow(days=[], start="20:00", end="23:00")


def test_commit_window_weekdays_are_iso_one_to_seven() -> None:
    with pytest.raises(ValueError):
        CommitWindow(days=[0], start="20:00", end="23:00")


def test_commit_window_times_are_normalised_to_two_digits() -> None:
    window = CommitWindow(days=[2], start="9:5", end="23:00")
    assert (window.start, window.end) == ("09:05", "23:00")


def test_a_window_ending_before_it_starts_is_a_midnight_crossing_not_an_error() -> None:
    """DX-ENV-002：22:00–02:00 是真实需求，用 end <= start 表示跨午夜。"""
    assert CommitWindow(days=[2], start="22:00", end="02:00").wraps_midnight()
    assert not CommitWindow(days=[2], start="20:00", end="23:00").wraps_midnight()


def test_required_approval_fields_are_refused_until_m4() -> None:
    """DX-ENV-002【调整】：审批人数与操作员角色随 M4 引入，本轮登记就要拒绝。"""
    with pytest.raises(EnvironmentInvalidError) as excinfo:
        rules.validate_spec(
            spec(
                protection=ProtectionSpec(
                    timezone="UTC",
                    commit_windows=[CommitWindow(days=[2], start="20:00", end="23:00")],
                    required_approvals=2,
                )
            )
        )
    assert excinfo.value.details["milestone"] == "M4"


def test_operator_role_is_refused_until_m4() -> None:
    with pytest.raises(EnvironmentInvalidError) as excinfo:
        rules.validate_spec(
            spec(
                protection=ProtectionSpec(
                    timezone="UTC",
                    commit_windows=[CommitWindow(days=[2], start="20:00", end="23:00")],
                    operator_role="dx-operator",
                )
            )
        )
    assert excinfo.value.details["rule"] == "DX-ENV-002"


# --- DX-ENV-001/003/004：字段与凭证范围 ---------------------------------------


def test_mvp_needs_a_secret_reference_for_discovery_and_import() -> None:
    """DX-ENV-001/003：两个 MVP 必需范围缺一个就不能登记。"""
    with pytest.raises(EnvironmentInvalidError) as excinfo:
        rules.validate_spec(
            spec(credentials=[CredentialRef(scope=CredentialScope.DISCOVERY, secret_ref=DISCOVERY_REF)])
        )
    assert excinfo.value.details["missingScope"] == CredentialScope.IMPORT.value


def test_a_secret_reference_without_a_scheme_is_not_a_reference() -> None:
    """DX-ENV-003：只能存引用，引用必须带后端前缀。"""
    with pytest.raises(ValueError):
        CredentialRef(scope=CredentialScope.IMPORT, secret_ref="hunter2!")


def test_environment_key_must_not_be_empty() -> None:
    """DX-ENV-004：环境键是显式参数，不能是空串。"""
    with pytest.raises(EnvironmentInvalidError):
        rules.validate_spec(spec(environment_key="   "))


def test_environment_key_must_not_contain_spaces() -> None:
    with pytest.raises(EnvironmentInvalidError) as excinfo:
        rules.validate_spec(spec(environment_key="prod eu"))
    assert excinfo.value.details["environmentKey"] == "prod eu"


def test_name_must_not_be_empty() -> None:
    with pytest.raises(EnvironmentInvalidError):
        rules.validate_spec(spec(name="  "))


def test_base_url_must_be_an_http_url() -> None:
    """DX-CLI-006：只允许 http(s) 形态的目标地址。"""
    with pytest.raises(EnvironmentInvalidError):
        rules.validate_spec(spec(base_url="ftp://dhis.example.org"))


def test_plain_http_is_refused_while_tls_is_required() -> None:
    """DX-CLI-006：要求 TLS 的环境不能是明文地址，登记时就拒绝。"""
    with pytest.raises(Dhis2TlsError):
        rules.validate_spec(spec(base_url="http://dhis.example.org", tls_required=True))


def test_plain_http_is_allowed_when_tls_is_explicitly_relaxed() -> None:
    rules.validate_spec(spec(base_url="http://localhost:8080", tls_required=False))


def test_timeout_must_be_positive() -> None:
    """DX-ENV-001、DX-NFR-01：请求超时可按环境配置，但必须是正数。"""
    with pytest.raises(EnvironmentInvalidError):
        rules.validate_spec(spec(timeout_seconds=0))


# --- DX-ENV-008：补丁叠加 ------------------------------------------------------


def test_apply_patch_keeps_every_field_it_did_not_mention() -> None:
    """DX-ENV-008：未给出的字段保持不变。"""
    current = spec(name="旧名字", timeout_seconds=90)
    merged = rules.apply_patch(current, EnvironmentPatch(name="新名字"))
    assert merged.name == "新名字"
    assert merged.timeout_seconds == 90
    assert merged.base_url == current.base_url
    assert merged.credentials == current.credentials


def test_apply_patch_revalidates_the_merged_spec() -> None:
    """DX-ENV-008：补丁叠出来的档案仍然要过全部规则——丢掉必需范围就当场暴露。"""
    current = spec()
    with pytest.raises(EnvironmentInvalidError) as excinfo:
        rules.apply_patch(
            current,
            EnvironmentPatch(
                credentials=[
                    CredentialRef(scope=CredentialScope.DISCOVERY, secret_ref=DISCOVERY_REF)
                ]
            ),
        )
    assert excinfo.value.details["missingScope"] == CredentialScope.IMPORT.value


def test_apply_patch_accepts_a_new_credential_reference_list() -> None:
    current = spec()
    replacement = [
        CredentialRef(scope=CredentialScope.DISCOVERY, secret_ref="memory://dx/rotated-discovery"),
        CredentialRef(scope=CredentialScope.IMPORT, secret_ref="memory://dx/rotated-import"),
    ]
    merged = rules.apply_patch(current, EnvironmentPatch(credentials=replacement))
    assert merged.secret_refs()[CredentialScope.DISCOVERY.value] == "memory://dx/rotated-discovery"
    assert merged.secret_refs()[CredentialScope.IMPORT.value] == "memory://dx/rotated-import"


def test_can_disable_rejects_an_environment_that_is_already_disabled() -> None:
    """DX-ENV-009：重复禁用不是幂等的静默成功，而是一个明确错误。"""
    from factory_dhis2.enums import EnvironmentStatus

    with pytest.raises(EnvironmentInvalidError):
        rules.can_disable(EnvironmentStatus.DISABLED)


def test_can_disable_allows_an_active_environment() -> None:
    from factory_dhis2.enums import EnvironmentStatus

    rules.can_disable(EnvironmentStatus.ACTIVE)


# --- registry_rows：spec → 行（单一出入口） -----------------------------------


def test_spec_to_environment_row_writes_the_declared_columns() -> None:
    row = spec_to_environment_row("dxenv0000000000000000000001", spec(environment_key="prod"))
    assert row.id == "dxenv0000000000000000000001"
    assert row.key == "prod"
    assert row.environment_class == EnvironmentClass.TEST.value
    assert row.status == "ACTIVE"


def test_credential_rows_are_one_per_scope_and_sorted() -> None:
    """DX-ENV-003：按范围一行一个引用，顺序固定，产物与审计才可比。"""
    rows = spec_to_credentials_rows(spec())
    assert [row.scope for row in rows] == [
        "apps",
        "datastore",
        "discovery",
        "import",
        "maintenance",
        "tracker",
    ]
    assert {row.environment_key for row in rows} == {"staging"}


def test_protection_row_stores_windows_as_documents_with_the_environment_timezone() -> None:
    row = spec_to_protection_row(
        spec(
            environment_key="prod",
            environment_class=EnvironmentClass.PRODUCTION,
            protection=production_protection(),
        )
    )
    assert row is not None
    assert row.timezone == "Asia/Shanghai"
    assert row.commit_windows[0]["days"] == [2, 4]
    assert row.commit_windows[0]["wrapsMidnight"] is False


def test_environment_without_protection_gets_no_protection_row() -> None:
    assert spec_to_protection_row(spec()) is None


# --- DX-ENV-001：版本约束必填 ------------------------------------------------


@pytest.mark.parametrize("constraint", [None, "", "   "])
def test_a_version_constraint_is_required(constraint: str | None) -> None:
    """DX-ENV-001：版本约束是必填项。

    没有它，"这个环境是不是 2.41"只能靠猜；猜错的就是端点目录与导入参数，直到一次真实
    导入才暴露。空串与纯空格同样算没填。
    """
    with pytest.raises(EnvironmentInvalidError) as excinfo:
        rules.validate_spec(spec(version_constraint=constraint))
    assert excinfo.value.details["rule"] == "DX-ENV-001"


def test_an_explicit_version_constraint_is_accepted() -> None:
    rules.validate_spec(spec(version_constraint="2.41.x"))


# --- DX-ENV-002：删除策略按类别的默认值 --------------------------------------


def test_production_defaults_to_forbid_when_no_policy_is_given() -> None:
    """DX-ENV-002：PRODUCTION 没填删除策略时是 ``FORBID``，不是非生产的 ``SAFE_ONLY``。"""
    row = spec_to_protection_row(
        spec(
            environment_key="prod",
            environment_class=EnvironmentClass.PRODUCTION,
            protection=ProtectionSpec(
                commit_windows=[CommitWindow(days=[2], start="20:00", end="23:00")]
            ),
        )
    )
    assert row is not None
    assert row.deletion_policy == DeletionPolicy.FORBID.value


def test_non_production_defaults_to_safe_only_when_no_policy_is_given() -> None:
    """DX-ENV-002：非生产默认 ``SAFE_ONLY``——"删除要走安全检查"，而不是彻底禁止删除。"""
    row = spec_to_protection_row(
        spec(environment_class=EnvironmentClass.DEV, protection=ProtectionSpec())
    )
    assert row is not None
    assert row.deletion_policy == DeletionPolicy.SAFE_ONLY.value


def test_an_explicit_policy_overrides_the_class_default() -> None:
    row = spec_to_protection_row(
        spec(
            environment_key="prod",
            environment_class=EnvironmentClass.PRODUCTION,
            protection=ProtectionSpec(deletion_policy=DeletionPolicy.SAFE_ONLY),
        )
    )
    assert row is not None
    assert row.deletion_policy == DeletionPolicy.SAFE_ONLY.value


# --- DX-ENV-007：紧急锁定只能走 set_emergency_lock ---------------------------


def test_the_profile_cannot_switch_on_the_emergency_lock() -> None:
    """DX-ENV-007：从档案里开紧急锁会绕过原因、变更人、变更时刻与审计。"""
    with pytest.raises(EnvironmentInvalidError) as excinfo:
        rules.validate_spec(
            spec(
                environment_class=EnvironmentClass.PRODUCTION,
                protection=ProtectionSpec(
                    commit_windows=[CommitWindow(days=[2], start="20:00", end="23:00")],
                    emergency_lock=True,
                ),
            )
        )
    assert excinfo.value.details["rule"] == "DX-ENV-007"


def test_a_production_row_created_through_the_profile_starts_unlocked() -> None:
    row = spec_to_protection_row(
        spec(
            environment_key="prod",
            environment_class=EnvironmentClass.PRODUCTION,
            protection=production_protection(),
        )
    )
    assert row is not None
    assert row.emergency_lock is False


def test_tls_policy_for_an_https_target_validates_the_certificate() -> None:
    """DX-CLI-006：要求 TLS 时 verify=True。"""
    policy = policy_for("https://dhis.example.org/instance/", tls_required=True)
    assert isinstance(policy, TlsPolicy)
    assert policy.verify is True
    assert policy.base_url == "https://dhis.example.org/instance"
