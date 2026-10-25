"""载荷哈希的一致性（DX-EXE-005、AC-EXE-02、DX-EXE-029）。

S4 不判断 Gate（C-08），但提交类函数必须校验两件事：调用方给出的期望哈希等于载荷产物的
实际哈希，以及该环境存在对同一哈希的成功 VALIDATE。VALIDATE 还只对“那一刻的环境状态”
有效——状态哈希不等就是陈旧，必须重跑而不是提交。
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from factory_foundation.api import new_id, unit_of_work
from sqlalchemy import select
from sqlalchemy.orm import Session

from factory_dhis2.errors import PayloadHashMismatchError, ValidationStaleError
from factory_dhis2.models.execution import ValidationRecordRow
from factory_dhis2.payload.hash import (
    latest_successful_validation,
    payload_hash,
    require_prior_validation,
    structure_hash_of_payload,
    verify_expected_hash,
)
from factory_dhis2.payload.package import MetadataPackage

pytestmark = pytest.mark.usefixtures("harness")

DIGEST = "a" * 64
ARTIFACT_HASH = f"sha256:{DIGEST}"
OTHER_HASH = "sha256:" + "b" * 64
STATE_ONE = "sha256:" + "c" * 64
STATE_TWO = "sha256:" + "d" * 64


def package(artifact_hash: str = ARTIFACT_HASH) -> MetadataPackage:
    return MetadataPackage(
        artifact_id="art-package-under-test",
        artifact_hash=artifact_hash,
        payload={"dataElements": [{"id": "FTRrcoaog83", "name": "体重"}]},
        manifest={"uidToBusinessKey": [{"uid": "FTRrcoaog83", "businessKey": "de.weight"}]},
    )


def record(
    session: Session,
    *,
    environment_key: str = "staging",
    payload_hash_value: str = ARTIFACT_HASH,
    env_state_hash: str = STATE_ONE,
    result: str = "PASSED",
    validated_at: datetime = datetime(2026, 1, 1, tzinfo=UTC),
) -> str:
    record_id = new_id("dxvld")
    session.add(
        ValidationRecordRow(
            id=record_id,
            environment_key=environment_key,
            payload_hash=payload_hash_value,
            env_state_hash=env_state_hash,
            execution_id="dxexe0000000000000000000001",
            validated_at=validated_at,
            result=result,
            issue_count=0,
        )
    )
    session.flush()
    return record_id


# --- DX-EXE-005 / AC-EXE-02：期望哈希 -----------------------------------------


def test_the_caller_stating_the_artifact_hash_it_was_approved_against_is_accepted() -> None:
    """AC-EXE-02：期望哈希等于产物内容哈希时放行，并回报实际用的那个。"""
    assert verify_expected_hash(package(), ARTIFACT_HASH) == ARTIFACT_HASH


def test_a_hash_without_the_algorithm_prefix_is_the_same_hash() -> None:
    """哈希有两个写法，比较要在规范化之后进行。"""
    assert verify_expected_hash(package(), DIGEST) == ARTIFACT_HASH


def test_a_package_that_is_not_the_approved_content_is_refused() -> None:
    """AC-EXE-02：批准的内容与执行的内容必须是一件东西。"""
    with pytest.raises(PayloadHashMismatchError) as excinfo:
        verify_expected_hash(package(), OTHER_HASH)
    assert excinfo.value.details["actual"] == ARTIFACT_HASH
    assert excinfo.value.details["expected"] == OTHER_HASH
    assert excinfo.value.details["rule"] == "AC-EXE-02"


def test_a_commit_without_any_stated_hash_is_refused() -> None:
    """DX-EXE-005：没有期望哈希就没有“批准的是哪一份”，直接拒绝。"""
    with pytest.raises(PayloadHashMismatchError) as excinfo:
        verify_expected_hash(package(), None)
    assert excinfo.value.details["rule"] == "DX-EXE-005"


def test_the_payload_hash_is_the_artifact_content_hash() -> None:
    """S1 的 Gate 绑定与 Run 里存的就是同一个值（模块说明）。"""
    assert payload_hash(package()) == ARTIFACT_HASH


def test_the_logical_payload_hash_is_stable_across_the_same_content() -> None:
    """报告与追溯用的逻辑哈希：同一内容永远同一个，与产物字节无关。"""
    first = structure_hash_of_payload(payload_package("体重"))
    second = structure_hash_of_payload(payload_package("体重"))
    assert first == second
    assert first != structure_hash_of_payload(payload_package("身高"))


def payload_package(name: str) -> MetadataPackage:
    return MetadataPackage(
        artifact_id="art-x",
        artifact_hash=ARTIFACT_HASH,
        payload={"dataElements": [{"id": "FTRrcoaog83", "name": name}]},
        manifest={},
    )


# --- DX-EXE-005 / DX-EXE-029：先要有成功的 VALIDATE ---------------------------


def test_a_commit_without_a_successful_validation_for_that_hash_is_refused() -> None:
    """DX-EXE-005：该环境从没验过这份载荷，COMMIT 不予执行。"""
    with unit_of_work() as session, pytest.raises(PayloadHashMismatchError) as excinfo:
        require_prior_validation(session, "staging", ARTIFACT_HASH)
    assert excinfo.value.details["payloadHash"] == ARTIFACT_HASH


def test_a_failed_validation_does_not_open_the_door_to_a_commit() -> None:
    with unit_of_work() as session:
        record(session, result="FAILED")
    with unit_of_work() as session, pytest.raises(PayloadHashMismatchError):
        require_prior_validation(session, "staging", ARTIFACT_HASH)


def test_a_validation_of_another_environment_does_not_count() -> None:
    with unit_of_work() as session:
        record(session, environment_key="other")
    with unit_of_work() as session:
        assert latest_successful_validation(session, "staging", ARTIFACT_HASH, None) is None


def test_the_successful_validation_of_the_same_hash_is_returned() -> None:
    with unit_of_work() as session:
        record_id = record(session)
    with unit_of_work() as session:
        found = require_prior_validation(session, "staging", ARTIFACT_HASH)
    assert found.id == record_id


def test_a_state_change_since_the_validation_makes_it_stale() -> None:
    """DX-EXE-029：VALIDATE 只对那一刻的环境状态有效，状态变了就必须重验。"""
    with unit_of_work() as session:
        record(session, env_state_hash=STATE_ONE)
    with unit_of_work() as session, pytest.raises(ValidationStaleError) as excinfo:
        require_prior_validation(session, "staging", ARTIFACT_HASH, env_state_hash=STATE_TWO)
    assert excinfo.value.details["validatedState"] == STATE_ONE
    assert excinfo.value.details["currentState"] == STATE_TWO


def test_a_matching_environment_state_is_not_stale() -> None:
    with unit_of_work() as session:
        record(session, env_state_hash=STATE_ONE)
    with unit_of_work() as session:
        found = require_prior_validation(session, "staging", ARTIFACT_HASH, env_state_hash=STATE_ONE)
    assert found.env_state_hash == STATE_ONE


def test_the_newest_successful_validation_is_the_one_reported() -> None:
    """DX-EXE-005 的“最近一次”：两次成功之间环境可能被改回去，取最新的结论。"""
    with unit_of_work() as session:
        record(session, env_state_hash=STATE_ONE, validated_at=datetime(2026, 1, 1, tzinfo=UTC))
        newest = record(
            session,
            env_state_hash=STATE_TWO,
            validated_at=datetime(2026, 3, 1, tzinfo=UTC),
        )
    with unit_of_work() as session:
        found = latest_successful_validation(session, "staging", ARTIFACT_HASH, None)
    assert found is not None and found.id == newest


def test_latest_successful_validation_can_narrow_to_one_environment_state() -> None:
    with unit_of_work() as session:
        old_state = record(session, env_state_hash=STATE_ONE)
        record(session, env_state_hash=STATE_TWO, validated_at=datetime(2026, 4, 1, tzinfo=UTC))
    with unit_of_work() as session:
        found = latest_successful_validation(session, "staging", ARTIFACT_HASH, STATE_ONE)
    assert found is not None and found.id == old_state


def test_records_are_kept_for_every_validation_of_a_different_state() -> None:
    """追溯链（FR-SEC-014）：不同状态的两次成功是两条记录，不是覆盖。"""
    with unit_of_work() as session:
        record(session, env_state_hash=STATE_ONE)
        record(session, env_state_hash=STATE_TWO)
    with unit_of_work() as session:
        rows = session.execute(
            select(ValidationRecordRow).where(ValidationRecordRow.environment_key == "staging")
        ).scalars().all()
    assert len(rows) == 2
