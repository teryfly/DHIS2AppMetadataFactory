"""Schema 包的同步范围（DX-CAP-001）：默认是目录里的**全部**类型。

只同步「核心那十类」是曾经的实现，它把决定权偷偷交给了产物：S2 只读产物、不许再猜，读不到
的字段清单就只能当"没有"。这一节锁住默认范围、显式收窄仍然可用，以及实例答不出某类时的
记录方式（那是实例的事实，不能变成我们的猜测）。
"""

from __future__ import annotations

from typing import Any

import httpx
import pytest

from factory_dhis2 import deps
from factory_dhis2.capability.probe import probe_instance
from factory_dhis2.capability.service import (
    CORE_SCHEMA_TYPES,
    default_schema_types,
)
from factory_dhis2.client.auth import CredentialProvider
from factory_dhis2.client.session import Dhis2Client
from factory_dhis2.enums import (
    AuthMode,
    CredentialScope,
    EnvironmentClass,
    EnvironmentStatus,
    LlmDataPolicy,
)
from factory_dhis2.profiles import registry
from factory_dhis2.target import EnvironmentTarget
from factory_dhis2.testing import FakeDhis2Instance, FakeDhis2Transport

SECRET = "dhp_pat_syncscopevalue0001"


@pytest.fixture(scope="module")
def profile() -> Any:
    registry.load_builtin_profiles()
    return registry.profile_for_version("2.41.10")


def make_target(prof: Any) -> EnvironmentTarget:
    refs = {scope.value: f"memory://dx/sync/{scope.value}" for scope in CredentialScope}
    return EnvironmentTarget(
        key="sync",
        name="Sync",
        base_url="https://dhis.example.org/instance",
        environment_class=EnvironmentClass.DEV,
        auth_mode=AuthMode.PAT,
        tls_required=True,
        llm_data_policy=LlmDataPolicy.LOCAL_ONLY,
        timeout_seconds=30,
        status=EnvironmentStatus.ACTIVE,
        profile=prof,
        dhis2_version="2.41.10",
        credentials=CredentialProvider(
            refs, auth_mode=AuthMode.PAT, resolver=lambda _ref: SECRET
        ),
    )


def client_for(prof: Any, transport: httpx.BaseTransport | None = None) -> Dhis2Client:
    deps.set_transport(transport or FakeDhis2Transport(FakeDhis2Instance()))
    return Dhis2Client(make_target(prof), scope=CredentialScope.DISCOVERY)


class _BlindSchemas(httpx.BaseTransport):
    """对指定的几类假装没有 Schema 端点：2.41.10 在线实测确实有端点是 404 的。"""

    def __init__(self, inner: httpx.BaseTransport, blind: tuple[str, ...]) -> None:
        self._inner = inner
        self._blind = blind

    def handle_request(self, request: httpx.Request) -> httpx.Response:
        for name in self._blind:
            if request.url.path.endswith(f"/schemas/{name}.json"):
                return httpx.Response(
                    404,
                    json={"status": "ERROR", "httpStatusCode": "404", "message": "no schema"},
                    request=request,
                )
        return self._inner.handle_request(request)


def test_the_default_scope_is_the_whole_catalogue(profile: Any) -> None:
    """默认同步范围 = 目录里的全部类型，明显大于曾经那个"核心集"。

    目录本身是 M2 的口径：Tier 2 的类型（DataSet、Indicator……）不在里面，那是 M5 的事，
    所以「全目录」不等于「DHIS2 的全部对象类型」。
    """
    resolved = default_schema_types(profile)

    assert resolved == tuple(profile.supported_types)
    assert set(CORE_SCHEMA_TYPES) < set(resolved)
    for expected in ("programIndicator", "validationRule", "optionGroup", "sqlView"):
        assert expected in resolved, expected
    assert "dataSet" not in resolved, "Tier 2 类型不在 M2 目录里（总体 SRS 6.1 的分层）"


def test_a_default_probe_syncs_every_type_it_can(profile: Any) -> None:
    """一次探测把 34 类的 Schema 都取回来（内存实例全都答得出）。"""
    client = client_for(profile)

    result = probe_instance(
        client,
        schema_types=default_schema_types(profile),
        check_resources=False,
    )

    synced = {item.dhis2_type for item in result.schemas}
    assert synced == set(profile.supported_types)
    assert result.summary()["schemasSynced"] == len(synced)
    # 每类都带着实例给的字段清单，而不是空壳
    data_element = next(item for item in result.schemas if item.dhis2_type == "dataElement")
    assert data_element.properties, "同步的意义在于字段清单，不是类型名"


def test_narrowing_is_still_available_for_callers(profile: Any) -> None:
    """``schema_types=`` 让调用方收窄（快速刷新基线），但那是显式选择。"""
    client = client_for(profile)

    result = probe_instance(client, schema_types=CORE_SCHEMA_TYPES, check_resources=False)

    assert {item.dhis2_type for item in result.schemas} == set(CORE_SCHEMA_TYPES)


def test_a_type_the_instance_cannot_answer_is_recorded_not_invented(profile: Any) -> None:
    """实例答不出的那一类进 ``unavailableResources``：这是实例的事实，不是我们的猜测。"""
    blind = _BlindSchemas(FakeDhis2Transport(FakeDhis2Instance()), ("validationRule",))
    client = client_for(profile, blind)

    result = probe_instance(
        client,
        schema_types=default_schema_types(profile),
        check_resources=False,
    )

    assert "schemas:validationRule" in result.unavailable_resources
    assert "validationRule" not in {item.dhis2_type for item in result.schemas}
    assert result.summary()["unavailableResources"], "探测结论里要看得见这一条"


def test_an_override_survives_a_real_probe_and_reaches_the_artifact(profile: Any) -> None:
    """覆盖 → 探测 → 产物：这一段最容易掉，因为同步回来的那一份"看起来更权威"。

    实例的 Schema 里没有 ``controlableFields``/``forbidden`` 这两个概念，所以同步答案里
    它们为空；覆盖必须在此刻顶上去，同时**不**把实例给的字段清单丢掉。
    """
    from dataclasses import replace

    from factory_dhis2.contract.capability_profile import build_capability_profile_document

    with_override = replace(
        profile,
        descriptor_overrides=({
            "dhis2Type": "dataElement",
            "controlableFields": ["name", "code"],
        },),
    )
    client = client_for(with_override)
    result = probe_instance(
        client, schema_types=default_schema_types(with_override), check_resources=False
    )

    document = build_capability_profile_document(
        with_override,
        environment_key="sync",
        dhis2_version="2.41.10",
        probed=result.summary(),
        probed_at=result.probed_at,
        schemas=result.schemas,
    )

    by_type = {item.dhis2_type: item for item in document.schemas}
    data_element = by_type["dataElement"]
    assert data_element.controlable_fields == ["name", "code"], (
        "可受控字段是版本与项目的决定，实例答不出就等于没有才是错的"
    )
    assert data_element.properties, "实例给的字段清单不能被覆盖冲掉"
    assert len(by_type) == len(with_override.supported_types)
