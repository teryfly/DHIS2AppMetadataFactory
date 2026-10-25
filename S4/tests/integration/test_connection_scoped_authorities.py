"""连接测试按**凭证范围各自**给结论（DX-CON-001/002/003/004、DX-AC-01、DX-AC-13）。

``../requirements/S4_DHIS2集成与执行_SRS.md`` 3.3 的三条硬要求在这一组里逐条钉住：

* 报告的是**该范围那个服务账户**实际具备与缺少的权限。PRODUCTION 的 discovery 与 import
  本来就是两个账户（DX-ENV-005），用只读账户的授权列表去回答"这个导入账户有没有权限"，
  报出去的结论是伪造的；
* 只用只读请求判定（DX-CON-003）。元数据导入在 DHIS2 里没有全局授权名可查，它按对象的
  manage 共享判定 —— 那就读那个共享设置，绝不"试写一条看看"；
* 一次连接测试之后，目标环境必须什么都没变（DX-AC-01 的后半句）。

实例是内存的、数据库是真的：这些结论要落进 ``dx.connection_test`` 才算可追溯。
"""

from __future__ import annotations

import base64
from collections.abc import Iterator
from typing import Any

import pytest
from factory_foundation.api import install_development_backends, store_secret, unit_of_work

from factory_dhis2 import deps
from factory_dhis2.api import create_environment, get_last_connection_test
from factory_dhis2.api import (
    test_connection as run_connection_check,
)
from factory_dhis2.testing.instance import FakeDhis2Instance
from factory_dhis2.testing.transport import FakeDhis2Transport

pytestmark = [pytest.mark.usefixtures("harness"), pytest.mark.integration]

ENVIRONMENT = "scoped"
BASE = "https://scoped.local/dhis2"
READER = "svcacct-reader:only-read"
WRITER = "svcacct-writer:metadata"


def _spec(**changes: Any) -> dict[str, Any]:
    install_development_backends()
    discovery = store_secret("memory://dx/scoped/discovery", READER)
    importing = store_secret("memory://dx/scoped/import", WRITER)
    spec: dict[str, Any] = {
        "environmentKey": ENVIRONMENT,
        "name": "分范围授权测试实例",
        "baseUrl": BASE,
        "environmentClass": "TEST",
        "versionConstraint": "2.41.x",
        "authMode": "BASIC",
        "tlsRequired": True,
        "llmDataPolicy": "PRIVATE_NETWORK",
        "credentials": [
            {"scope": "discovery", "secretRef": discovery},
            {"scope": "import", "secretRef": importing},
        ],
    }
    spec.update(changes)
    return spec


@pytest.fixture
def instance() -> Iterator[FakeDhis2Instance]:
    fake = FakeDhis2Instance()
    # 两个服务账户、两套权限：discovery 只读，import 能读但管不了对象
    fake.accounts = {
        READER: {
            "id": "u1",
            "username": "reader",
            "authorities": ["F_DATAELEMENT_PUBLIC_ADD"],
            "roles": [{"id": "r1", "name": "Dashboard user"}],
        },
        WRITER: {
            "id": "u2",
            "username": "writer",
            "authorities": [],
            "roles": [{"id": "r2", "name": "Only reader role"}],
        },
    }
    fake.authorities = ["SHOULD_NOT_BE_USED"]
    fake.put("dataElement", "de1", code="WEIGHT", name="体重")
    fake.sharing = {"de1": False}
    deps.set_transport(FakeDhis2Transport(fake))
    create_environment(_spec())
    yield fake


def states_of(check: Any) -> dict[str, str]:
    return {item["name"]: item["state"] for item in check.checks}


def test_the_import_scope_is_judged_from_the_import_account(instance: FakeDhis2Instance) -> None:
    """DX-CON-002 / DX-AC-13：import 范围的 granted/missing 来自它**自己**的那个账户。"""
    check = run_connection_check(
        ENVIRONMENT, ["discovery", "import"], idempotency_key="scope:1", operator="ops"
    )
    assert check.granted_authorities["discovery"] == ["F_DATAELEMENT_PUBLIC_ADD"]
    assert check.granted_authorities["import"] == [], (
        "import 账户的授权列表被 discovery 账户冒充了"
    )
    assert check.account_roles["import"] == ["Only reader role"]
    assert "SHOULD_NOT_BE_USED" not in str(check.model_dump())


def test_an_import_account_without_manage_fails_and_names_the_missing_right(
    instance: FakeDhis2Instance,
) -> None:
    """DX-AC-01 / DX-AC-13：缺少元数据导入权限时逐项齐备、列出缺什么、总体失败。"""
    check = run_connection_check(ENVIRONMENT, ["discovery", "import"], idempotency_key="scope:2")
    states = states_of(check)
    assert check.ok is False and check.result == "FAILED"
    assert states["scope.import"] == "FAILED"
    assert states["scope.discovery"] == "PASSED"
    missing = check.missing_authorities["import"]
    assert missing and "manage" in str(missing), missing
    for required in ("address", "tls", "auth", "version", "versionProfile", "account"):
        assert required in states, required
        assert states[required] in {"PASSED", "SKIPPED"}, states


def test_the_failed_import_probe_wrote_nothing_to_the_instance(
    instance: FakeDhis2Instance,
) -> None:
    """DX-CON-003 / DX-AC-01：判定导入权限靠的是读对象的 access，不是试写。"""
    run_connection_check(ENVIRONMENT, ["discovery", "import"], idempotency_key="scope:3")
    methods = {str(item["method"]) for item in instance.requests}
    assert methods == {"GET"}, f"连接测试改变了目标环境：{sorted(methods)}"
    access_reads = [
        item
        for item in instance.requests
        if "access" in str(item.get("query", {}).get("fields", ""))
    ]
    assert access_reads, "没有读对象的 access.manage，import 结论就无从判定"


def test_the_import_verdict_reads_access_manage_not_the_sharing_document(
    instance: FakeDhis2Instance,
) -> None:
    """导入权的证据是对象的 ``access.manage``，不是 ``/sharing``。

    2.41 的 ``/{uid}/sharing`` 回的是 ``owner`` / ``public`` / ``users`` / ``userGroups``，
    **没有** ``manage`` 这个键。判定代码一旦去那儿取 ``payload["manage"]``，它在任何真实实例
    上都恒得 False —— 连接测试恒失败，Deployment Run 恒开不起来，而替身因为回的是一个顶层
    manage 布尔，一路绿灯。所以这条测试两头都钉：读的是带 fields=access 的那次，且替身的
    /sharing 与真实例同形。
    """
    run_connection_check(ENVIRONMENT, ["discovery", "import"], idempotency_key="scope:sharing-shape")

    paths = [str(item["path"]) for item in instance.requests]
    assert not [path for path in paths if path.endswith("/sharing")], (
        "import 判定又去读 /sharing 了：那个文档里没有 manage，读它等于永远判不可导入"
    )

    basic = {"Authorization": "Basic " + base64.b64encode(WRITER.encode()).decode()}
    code, _headers, document = instance.handle(
        "GET", "dataElement/de1", {"fields": "id,access"}, {}, basic
    )
    assert code == 200, document
    assert document["access"]["manage"] is False

    _code, _headers, sharing = instance.handle("GET", "dataElement/de1/sharing", {}, {}, basic)
    assert "manage" not in str(sharing), f"/sharing 替身发明了真实例没有的键：{sharing}"
    assert set(sharing["sharing"]) == {"owner", "public", "external", "users", "userGroups"}, sharing


def test_a_scope_whose_credentials_are_rejected_is_reported_as_failed(
    instance: FakeDhis2Instance,
) -> None:
    """凭证不被接受的范围必须是 FAILED，而不是"沿用别的范围的账户事实"。"""
    instance.accounts.pop(WRITER)
    check = run_connection_check(ENVIRONMENT, ["discovery", "import"], idempotency_key="scope:4")
    states = states_of(check)
    assert states["scope.import"] == "FAILED"
    assert states["scope.discovery"] == "PASSED"
    assert check.ok is False
    assert "<unauthenticated service account>" in check.missing_authorities["import"]


def test_the_per_scope_verdict_is_what_gets_persisted(instance: FakeDhis2Instance) -> None:
    """DX-CON-004/005：持久化与回读的是按范围归组的事实，不只是总体结论。"""
    run_connection_check(
        ENVIRONMENT, ["discovery", "import"], idempotency_key="scope:5", operator="carol"
    )
    stored = get_last_connection_test(ENVIRONMENT)
    assert stored is not None
    assert stored.operator == "carol"
    assert sorted(stored.granted_authorities) == ["discovery", "import"]
    assert stored.granted_authorities["discovery"] == ["F_DATAELEMENT_PUBLIC_ADD"]
    assert "manage" in str(stored.missing_authorities["import"])
    with unit_of_work() as session:
        from factory_dhis2.models.environment import ConnectionTestRow

        row = session.query(ConnectionTestRow).one()
    assert row.granted_authorities == stored.granted_authorities
