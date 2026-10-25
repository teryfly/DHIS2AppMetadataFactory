"""载荷哈希的一致性（DX-EXE-005、AC-EXE-02、总体 SRS FR-EXE-004 的执行侧防线）。

S4 不判断 Gate（C-08），但必须校验调用方给出的期望哈希：

1. 期望哈希 == 载荷产物的实际哈希 —— 证明“执行的就是你批准的那份内容”；
2. 该环境存在同一哈希的最近一次成功 VALIDATE —— 证明“批准的内容确实被实例验过”。

两者任一不符就拒绝。用**产物字节哈希**作为哈希值，与 S1 存进 Gate 绑定与 Run 的值同一
个来源，避免两侧各自算一种哈希。
"""

from __future__ import annotations

from datetime import datetime

from factory_foundation.api import normalize_hash, structure_hash
from sqlalchemy import select
from sqlalchemy.orm import Session

from factory_dhis2.errors import PayloadHashMismatchError, ValidationStaleError
from factory_dhis2.models.execution import ValidationRecordRow
from factory_dhis2.payload.package import MetadataPackage


def payload_hash(package: MetadataPackage) -> str:
    """本次执行的载荷哈希（= 产物内容哈希）。"""
    return package.artifact_hash


def structure_hash_of_payload(package: MetadataPackage) -> str:
    """逻辑载荷的规范化哈希，与 S2 的 ``payload_hash`` 同义，供报告与追溯使用。"""
    return structure_hash(package.payload)


def verify_expected_hash(package: MetadataPackage, expected_hash: str | None) -> str:
    if expected_hash is None:
        raise PayloadHashMismatchError(
            "a committing call must state the payload hash it was approved against "
            "(DX-EXE-005)",
            artifact_id=package.artifact_id,
            details={"artifactId": package.artifact_id, "rule": "DX-EXE-005"},
        )
    actual = package.artifact_hash
    if normalize_hash(expected_hash) != normalize_hash(actual):
        raise PayloadHashMismatchError(
            f"the payload hash is {actual}, but the caller expected {expected_hash}: the "
            "package is not the one that was approved",
            artifact_id=package.artifact_id,
            details={
                "artifactId": package.artifact_id,
                "actual": actual,
                "expected": expected_hash,
                "rule": "AC-EXE-02",
            },
        )
    return actual


def latest_successful_validation(
    session: Session,
    environment_key: str,
    package_hash: str,
    env_state_hash: str | None,
) -> ValidationRecordRow | None:
    """该环境最近一次对**该哈希**的成功 VALIDATE。

    ``env_state_hash`` 给出时一并匹配：VALIDATE 只对那一刻的环境状态有效（DX-EXE-029）。
    """
    statement = (
        select(ValidationRecordRow)
        .where(
            ValidationRecordRow.environment_key == environment_key,
            ValidationRecordRow.payload_hash == package_hash,
            ValidationRecordRow.result == "PASSED",
        )
        .order_by(ValidationRecordRow.validated_at.desc())
    )
    if env_state_hash is not None:
        statement = statement.where(ValidationRecordRow.env_state_hash == env_state_hash)
    return session.execute(statement.limit(1)).scalars().first()


def require_prior_validation(
    session: Session,
    environment_key: str,
    package_hash: str,
    *,
    env_state_hash: str | None = None,
) -> ValidationRecordRow:

    environment = session.execute(
        select(ValidationRecordRow)
        .where(
            ValidationRecordRow.environment_key == environment_key,
            ValidationRecordRow.payload_hash == package_hash,
            ValidationRecordRow.result == "PASSED",
        )
        .order_by(ValidationRecordRow.validated_at.desc())
        .limit(1)
    ).scalars().first()
    if environment is None:
        raise PayloadHashMismatchError(
            f"environment {environment_key!r} has no successful VALIDATE for payload hash "
            f"{package_hash}; COMMIT is refused (DX-EXE-005, AC-EXE-02)",
            details={
                "environmentKey": environment_key,
                "payloadHash": package_hash,
                "rule": "DX-EXE-005",
            },
        )
    if env_state_hash is not None and environment.env_state_hash != env_state_hash:
        raise ValidationStaleError(
            "the environment changed since the last successful VALIDATE of this payload: "
            "the validation only covers that moment (DX-EXE-029), re-run VALIDATE",
            details={
                "environmentKey": environment_key,
                "payloadHash": package_hash,
                "validatedState": environment.env_state_hash,
                "currentState": env_state_hash,
                "validatedAt": environment.validated_at.isoformat()
                if isinstance(environment.validated_at, datetime)
                else None,
            },
        )
    return environment


__all__ = [
    "latest_successful_validation",
    "payload_hash",
    "require_prior_validation",
    "structure_hash_of_payload",
    "verify_expected_hash",
]
