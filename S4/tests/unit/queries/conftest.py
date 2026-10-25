"""只读查询测试的共同装配：``dx`` 里的真实环境行 + 按环境路由的内存 DHIS2 实例。

四个查询函数是外部 I/O：它们自己开一段短事务读环境档案，再到事务外访问实例（00 文档
6.2）。所以这里**必须有真的 ``dx`` 环境行**——否则“按环境解析”与“只用 discovery 凭证
范围”这两件事根本没被证明过，只是在证明测试自己的替身。

每个环境一台独立实例：``uid_exists`` 的结论本来就是环境级事实，共用一台实例的话，
“同一 UID 在两个环境里答案不同”就永远测不出来。
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

import httpx
import pytest
from factory_foundation.api import install_development_backends, store_secret, unit_of_work

from factory_dhis2 import deps
from factory_dhis2.dto.environment import CredentialRef, EnvironmentSpec
from factory_dhis2.enums import (
    AuthMode,
    CredentialScope,
    EnvironmentClass,
    EnvironmentStatus,
    LlmDataPolicy,
)
from factory_dhis2.environment import service as environment_service
from factory_dhis2.testing.instance import FakeDhis2Instance
from factory_dhis2.testing.transport import FakeDhis2Transport

#: 本目录默认登记的环境键；主机名按 ``<key>.fake.test`` 构造，供路由 transport 分发
ENVIRONMENT_KEYS: tuple[str, ...] = ("dev", "uat")

#: 每个范围一个**不同**的凭证值：这样才分辨得出查询到底借了哪一份身份出门（DX-BR-03）
SCOPE_SECRETS: dict[CredentialScope, str] = {
    CredentialScope.DISCOVERY: "svcacct-reader:only-discovery",
    CredentialScope.IMPORT: "svcacct-writer:never-for-queries",
    CredentialScope.MAINTENANCE: "svcacct-maint:never-for-queries",
}


def valid_uid(index: int) -> str:
    """一个形态合法的 DHIS2 UID：11 位、首位字母、其余字母数字。"""
    return f"uid{index:08d}"


def valid_uids(count: int) -> list[str]:
    return [valid_uid(index) for index in range(count)]


class InstanceDirectory:
    """环境键 → 内存实例，以及一个按主机名分发的 transport。"""

    def __init__(self) -> None:
        self.instances: dict[str, FakeDhis2Instance] = {}
        self._transports: dict[str, FakeDhis2Transport] = {}

    def add(self, environment_key: str, instance: FakeDhis2Instance | None = None) -> FakeDhis2Instance:
        made = instance or FakeDhis2Instance()
        self.instances[environment_key] = made
        self._transports[f"{environment_key}.fake.test"] = FakeDhis2Transport(made)
        return made

    def instance(self, environment_key: str) -> FakeDhis2Instance:
        return self.instances[environment_key]

    def requests(
        self, environment_key: str, contains: str | None = None, method: str | None = None
    ) -> list[dict[str, Any]]:
        rows = self.instances[environment_key].requests
        return [
            row
            for row in rows
            if (contains is None or contains in str(row["path"]))
            and (method is None or row["method"] == method)
        ]

    @property
    def total_requests(self) -> int:
        return sum(len(item.requests) for item in self.instances.values())

    def transport(self) -> httpx.BaseTransport:
        return _RoutingTransport(self._transports)


class _RoutingTransport(httpx.BaseTransport):
    """按主机名把请求交给对应环境的实例。

    未知主机**抛错**而不是随便找一台：否则“查询串到了另一个环境”这种错误会表现为一个
    看起来合理的答案，而那正是 DX-BR-02（每次调用绑定唯一环境）要拦住的事。
    """

    def __init__(self, by_host: dict[str, FakeDhis2Transport]) -> None:
        self._by_host = by_host

    def handle_request(self, request: httpx.Request) -> httpx.Response:
        transport = self._by_host.get(str(request.url.host))
        if transport is None:
            raise AssertionError(f"没有为 {request.url.host!r} 准备实例：调用串了环境")
        return transport.handle_request(request)


def environment_spec(
    environment_key: str,
    refs: dict[CredentialScope, str],
    *,
    environment_class: EnvironmentClass = EnvironmentClass.TEST,
    scopes: tuple[CredentialScope, ...] = (CredentialScope.DISCOVERY, CredentialScope.IMPORT),
) -> EnvironmentSpec:
    return EnvironmentSpec(
        environment_key=environment_key,
        name=f"环境 {environment_key}",
        base_url=f"https://{environment_key}.fake.test",
        environment_class=environment_class,
        version_constraint="2.41.x",
        auth_mode=AuthMode.BASIC,
        tls_required=True,
        llm_data_policy=LlmDataPolicy.PRIVATE_NETWORK,
        timeout_seconds=30,
        status=EnvironmentStatus.ACTIVE,
        credentials=[
            CredentialRef(scope=scope, secret_ref=refs[scope]) for scope in scopes
        ],
    )


def register_environment(
    environment_key: str, *, environment_class: EnvironmentClass = EnvironmentClass.TEST
) -> dict[CredentialScope, str]:
    """写入该环境各范围的 Secret 并登记环境本身。

    每个范围一份**不同**的值：只有这样才分辨得出一次 DHIS2 调用借的是哪份身份（DX-BR-03），
    也才不会让“查询偷偷用了 import 范围”这种错误表现得像个正常结果。
    """
    install_development_backends()
    refs = {
        scope: store_secret(f"dx/queries/{environment_key}/{scope.value}", value)
        for scope, value in SCOPE_SECRETS.items()
    }
    spec = environment_spec(
        environment_key, refs, environment_class=environment_class
    )
    with unit_of_work() as session:
        environment_service.create_environment(spec, session=session, operator="tester")
    return refs


@pytest.fixture
def directory(harness: Any) -> Iterator[InstanceDirectory]:
    """干净的 ``dx`` + 两台已登记环境 + 各自的内存实例，transport 已装好。"""
    made = InstanceDirectory()
    for key in ENVIRONMENT_KEYS:
        made.add(key)
        register_environment(key)
    deps.set_transport(made.transport())
    deps.set_sleeper(lambda seconds: None)
    yield made
    deps.reset_providers()


@pytest.fixture
def dev(directory: InstanceDirectory) -> FakeDhis2Instance:
    """最常用的那台实例。"""
    return directory.instance("dev")
