"""部署后验证对“实例省略字段”的处理（DX-EXE-040、AC-EXE-06、FR-EXE-010）。

2.41.10 实测：回读一个 DataElement 时，``zeroStorageType`` 这种**取值等于 Profile 登记的
默认值**的字段根本不出现在 JSON 里（无论 ``fields=`` 点名还是 ``:all``）。把这种省略当成
“环境里没有这个值”，每次真实部署都会被判成不一致——这是在线 COMMIT 跑出来的第一条事实修正。

修正只到这一层为止：一个没有登记默认值的字段被省略，说明这次部署没有把它写进去，而那正是
FR-EXE-010 要求逐字段核对受控字段要抓的情形。放行它等于用代码假设实例行为（P-10 禁止），
并且让一次部分生效的部署带着"全部一致"的结论晋级。真遇到某字段总是被省略，补的是该版本
Profile 的默认值清单。
"""

from __future__ import annotations

from typing import Any

import pytest

from factory_dhis2.enums import IssueLevel
from factory_dhis2.execution.post_verify import (
    KIND_FIELD_MISMATCH,
    KIND_FIELD_NOT_ECHOED,
    ObjectVerification,
    compare_object,
    issue_for,
)
from factory_dhis2.profiles import registry

pytestmark = pytest.mark.usefixtures("harness")

ELEMENT = {
    "id": "de1",
    "code": "WEIGHT",
    "name": "体重",
    "valueType": "NUMBER",
    "aggregationType": "SUM",
    "zeroStorageType": "value_if_zero",
}


@pytest.fixture(scope="module")
def profile() -> Any:
    registry.load_builtin_profiles()
    return registry.profile_for_version("2.41.10")


def _compare(actual: dict[str, Any], profile: Any) -> list[dict[str, Any]]:
    findings, _compared = compare_object(
        ELEMENT, actual, set(ELEMENT) | {"zeroStorageType"}, profile, "dataElement"
    )
    return findings


def test_a_declared_default_omitted_by_the_instance_is_not_a_difference(
    profile: Any,
) -> None:
    """DX-EXE-040：Profile 登记了默认值且期望就是它 → 省略即一致。"""
    assert profile.property_defaults.get("dataElement", {}).get("zeroStorageType")
    findings = _compare(
        {key: value for key, value in ELEMENT.items() if key != "zeroStorageType"}, profile
    )
    assert findings == [], findings


def test_an_omitted_field_with_no_declared_default_is_a_difference(
    profile: Any,
) -> None:
    """没登记默认值的字段被省略：这就是"没生效"，必须进差异清单。"""
    payload = dict(ELEMENT, formName="随访体重")
    actual = {key: value for key, value in payload.items() if key != "formName"}
    findings, _ = compare_object(
        payload, actual, set(payload) | {"formName"}, profile, "dataElement"
    )
    kinds = {item["kind"] for item in findings}
    assert KIND_FIELD_NOT_ECHOED in kinds, findings
    assert KIND_FIELD_MISMATCH not in kinds, findings
    item = ObjectVerification(
        dhis2_type="dataElement",
        uid="de1",
        business_key="de.weight",
        disposition="CREATE",
        present=True,
        findings=findings,
    )
    assert item.matched is False, (
        "一个期望值不是登记默认值的字段读不回来，就不能说这个对象部署成功了"
    )


def test_a_value_the_instance_actually_echoes_back_different_is_blocking(
    profile: Any,
) -> None:
    """真不一致（实例回了另一个值）照旧是 BLOCKING：放宽省略不等于放宽比对。"""
    actual = dict(ELEMENT, aggregationType="NONE")
    findings = _compare(actual, profile)
    mismatch = [item for item in findings if item["kind"] == KIND_FIELD_MISMATCH]
    assert mismatch, findings
    issue = issue_for(
        ObjectVerification(
            dhis2_type="dataElement",
            uid="de1",
            business_key="de.weight",
            disposition="CREATE",
            present=True,
            findings=mismatch,
        ),
        mismatch[0],
        artifact_id=None,
    )
    assert issue.level is IssueLevel.BLOCKING


def test_every_finding_reaches_the_issue_list(profile: Any) -> None:
    """``consistent`` 由 issues 决定，所以任何一条差异都必须成为一个 Issue。

    曾经有一类"说明性差异"被留在 diffList 里而不进 issues，那正是把一个字段没生效的部署
    判成一致的那条路径。
    """
    findings = _compare({"id": "de1", "code": "WEIGHT", "name": "体重"}, profile)
    assert findings, "aggregationType / zeroStorageType / valueType 都没回读，应有差异"
    item = ObjectVerification(
        dhis2_type="dataElement",
        uid="de1",
        business_key="de.weight",
        disposition="CREATE",
        present=True,
        findings=findings,
    )
    issues = [issue_for(item, finding, artifact_id=None) for finding in findings]
    assert [issue.level for issue in issues] == [IssueLevel.BLOCKING] * len(issues)
    assert item.matched is False
