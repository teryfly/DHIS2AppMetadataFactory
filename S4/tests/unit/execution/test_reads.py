"""读回侧的 404 语义（DX-EXE-020/022、DX-EXE-040）。

``read_one`` 的契约是“对象不存在就是 ``None``”，不是抛错：CREATE 对象在提交前本来就不存在，
被人删掉也是一种漂移。之前它捕的是 S0 的 ``NotFoundError`` 而 Client 抛的是 S4 的
``Dhis2NotFoundError``，于是 Snapshot 遇到一条正常路径就整步炸掉——这组测试就是钉住这个修正。
"""

from __future__ import annotations

import pytest
from factory_foundation.api import install_development_backends

from factory_dhis2 import deps
from factory_dhis2.client.session import Dhis2Client
from factory_dhis2.enums import CredentialScope
from factory_dhis2.execution.reads import FULL_READ_FIELDS, read_many, read_one
from factory_dhis2.testing.instance import FakeDhis2Instance
from factory_dhis2.testing.transport import FakeDhis2Transport
from tests.unit.testing.test_fake_instance import make_target

pytestmark = pytest.mark.usefixtures("harness")


def _client(instance: FakeDhis2Instance) -> Dhis2Client:
    install_development_backends()
    deps.set_transport(FakeDhis2Transport(instance))
    return Dhis2Client(make_target(), scope=CredentialScope.DISCOVERY)


def test_an_absent_object_reads_as_none_instead_of_raising() -> None:
    """DX-EXE-022：Snapshot 里“不存在”是一种结论，不是一个异常。"""
    instance = FakeDhis2Instance()
    instance.put("dataElement", "de1", code="A", name="甲")
    client = _client(instance)
    assert read_one(client, "dataElement", "de1") is not None
    assert read_one(client, "dataElement", "neverExisted1") is None


def test_read_expands_the_all_fields_token_like_the_instance_does() -> None:
    """DX-EXE-040：``:all`` 必须展开，否则回读比对会把有值看成无值。"""
    instance = FakeDhis2Instance()
    instance.put(
        "dataElement", "de1", code="A", name="甲", valueType="NUMBER", domainType="AGGREGATE"
    )
    client = _client(instance)
    narrow = read_one(client, "dataElement", "de1", fields="id,name")
    assert narrow is not None and "valueType" not in narrow, narrow
    full = read_one(client, "dataElement", "de1", fields=FULL_READ_FIELDS)
    assert full is not None
    assert full["valueType"] == "NUMBER" and full["domainType"] == "AGGREGATE", sorted(full)


def test_read_many_batches_the_in_filter_like_the_instance_expects() -> None:
    """DX-NFR-02：批量读按 ``id:in:[…]`` 分批，上千个 UID 也不超 URL 长度限制。"""
    instance = FakeDhis2Instance()
    for index in range(7):
        instance.put("dataElement", f"de{index}", code=f"C{index}", name=f"元素 {index}")
    client = _client(instance)
    uids = [f"de{index}" for index in range(7)] + ["missingUid0"]
    found = read_many(client, "dataElement", uids, batch_size=3)
    assert sorted(found) == sorted(f"de{index}" for index in range(7))
    paths = [item["path"] for item in instance.requests]
    assert paths.count("dataElements") == 3, paths
