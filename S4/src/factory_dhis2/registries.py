"""S4 登记表：产物类型、审计事件、错误码、ID 前缀、幂等作用域。

00 文档 6.10 要求每个库子系统在 start-up 登记这四类目录，而 S0 拒绝任何未登记项：
未知产物类型不能创建、未知审计事件类型不能写入（``require_registered``）、未知错误码
不可重试、未登记 ID 前缀不能签发。忘记登记的能力会在**调用方的事务里**运行失败，所以
下表由 ``tests/unit/test_architecture.py`` 与代码保持同步。
"""

from __future__ import annotations

from factory_foundation.api import DHIS2_EXECUTION_SCOPE

#: 永久保留的三张表（DX-DATA-02）：清理项目数据时连删除尝试都不发，数据库层另有同名
#: 触发器兜底。**唯一出处**：``cleanup/service.py`` 与迁移都从这里取，避免两处各写一份而漂移。
RETAINED_TABLES: tuple[str, ...] = ("execution", "validation_record", "environment_lock")

#: S4 创建的产物类型（SRS 1.2、总体 SRS 23.1）。S0 的初始目录已把这些类型的 owner
#: 写成 ``["S4"]``，此处重述是为了让 S4 显式拥有它们，而不是依赖 S0 的好意（同 S3）。
DX_ARTIFACT_TYPES: dict[str, list[str]] = {
    "CAPABILITY_PROFILE": ["S4"],
    "DISCOVERY_RESULT": ["S4"],
    "EXECUTION_PLAN": ["S4"],
    "SNAPSHOT": ["S4"],
    "DHIS2_IMPORT_REPORT": ["S4"],
    "POST_VERIFICATION_REPORT": ["S4"],
    "EXECUTION_REPORT": ["S4"],
    # DIFF_REPORT 由 S2 与 S4 共同拥有（总体 SRS 15.5）：S0 注册时合并 owner 列表。
    "DIFF_REPORT": ["S4"],
}

#: 上限宽松但有限（同 S0/S3 的 FD-ART-006 做法）。一个 5,000 对象规模的发现结果或
#: Snapshot 大约在几十 MB 量级（DX-NFR-03），留出十倍余量。
DX_ARTIFACT_MAX_SIZES: dict[str, int] = {
    "CAPABILITY_PROFILE": 64 * 1024 * 1024,
    "DISCOVERY_RESULT": 256 * 1024 * 1024,
    "SNAPSHOT": 256 * 1024 * 1024,
    "EXECUTION_PLAN": 16 * 1024 * 1024,
    "DHIS2_IMPORT_REPORT": 64 * 1024 * 1024,
    "POST_VERIFICATION_REPORT": 64 * 1024 * 1024,
    "EXECUTION_REPORT": 16 * 1024 * 1024,
    "DIFF_REPORT": 64 * 1024 * 1024,
}

#: 附录 A 的 21 个事件，逐字照搬。名称必须满足 S0 的
#: ``^[a-z][a-z0-9_]*\\.[a-z0-9_]+\\.[a-z0-9_]+$``。
DX_AUDIT_EVENT_TYPES: dict[str, str] = {
    "dx.environment.created": "S4",
    "dx.environment.updated": "S4",
    "dx.environment.disabled": "S4",
    "dx.environment.lock_changed": "S4",
    "dx.credential_ref.changed": "S4",
    "dx.connection.tested": "S4",
    "dx.capability.probed": "S4",
    "dx.capability.stale": "S4",
    "dx.discovery.completed": "S4",
    "dx.snapshot.created": "S4",
    "dx.validate.started": "S4",
    "dx.validate.completed": "S4",
    "dx.commit.started": "S4",
    "dx.commit.completed": "S4",
    "dx.commit.failed": "S4",
    "dx.commit.needs_manual_check": "S4",
    "dx.manual_check.resolved": "S4",
    "dx.maintenance.executed": "S4",
    "dx.post_verify.completed": "S4",
    "dx.fixture.imported": "S4",
    "dx.delete.executed": "S4",
    "dx.env_lock.acquired": "S4",
    "dx.env_lock.released": "S4",
}

#: ``{code: (owner, retryable)}``。可重试性遵循总体 SRS 19.2.2：传输类与竞争类失败
#: 可由调用方的工作流重试；哈希不一致、锁定、权限、判定类失败不得重试。
#:
#: S0 的内置目录已经把 ``DHIS2_AUTH_ERROR`` / ``DHIS2_PERMISSION_ERROR`` /
#: ``DHIS2_NOT_FOUND`` / ``DHIS2_IMPORT_ERROR`` / ``DHIS2_TIMEOUT`` / ``DRIFT_DETECTED``
#: 的 owner 写成 S4，``CONCURRENCY_ERROR`` 的 owner 写成 S0（两者均不可重试，
#: ``DHIS2_TIMEOUT`` 可重试），因此不在这里重复登记。
DX_ERROR_CODES: dict[str, tuple[str, bool]] = {
    # DX-ENV-007 / DX-AC-05：紧急锁定开启后拒绝一切写类执行
    "ENVIRONMENT_LOCKED": ("S4", False),
    # DX-ENV-001/005：环境档案不合法（PRODUCTION 双 Secret、baseUrl、TLS 等）
    "DX_ENVIRONMENT_INVALID": ("S4", False),
    # DX-ENV-009/DX-ENV-008：禁用或修改一个仍有进行中执行的环境
    "DX_ENVIRONMENT_IN_USE": ("S4", True),
    # DX-ENV-002 / AC-EXE-05：当前时刻不在允许提交的时间窗内——稍后可重试
    "DX_OUTSIDE_COMMIT_WINDOW": ("S4", True),
    # DX-TRK-002 / DX-AC-09、总体 SRS 3.3 生产保护
    "DX_PRODUCTION_PROTECTED": ("S4", False),
    # DX-EXE-038：删除策略不允许，或删除安全检查未过
    "DX_DELETION_FORBIDDEN": ("S4", False),
    # DX-EXE-005 / AC-EXE-02：期望哈希与产物或最近一次 VALIDATE 不一致
    "DX_PAYLOAD_HASH_MISMATCH": ("S4", False),
    # DX-EXE-029：VALIDATE 结果只对那一刻的环境状态有效，状态已变
    "DX_VALIDATION_STALE": ("S4", True),
    # DX-CLI-006 / DX-CON-001：地址不可达或 TLS 校验失败
    "DHIS2_UNREACHABLE": ("S4", True),
    "DHIS2_TLS_INVALID": ("S4", False),
    # DX-CON-001：版本不满足约束，或没有对应版本 Profile
    "DHIS2_VERSION_UNSUPPORTED": ("S4", False),
    # DX-CAP-005/006：Capability Profile 过期，调用方须先重新探测
    "DHIS2_PROFILE_STALE": ("S4", True),
    # DX-EXE-033/034：异步任务丢失（结果不明）或任务本身失败
    "DHIS2_TASK_LOST": ("S4", False),
    "DHIS2_TASK_FAILED": ("S4", False),
    # DX-CLI-001：响应不是预期的 JSON 形状
    "DHIS2_RESPONSE_INVALID": ("S4", True),
    # DX-EXE-031：锁仍被一次待人工核实的执行持有
    # 输入产物缺失、不是 METADATA_PACKAGE、或 manifest 段不完整（DX-ASM-02）
    "DX_INPUT_ARTIFACT_INVALID": ("S4", False),
    # DX-QRY-001：实例返回的 UID 不符合 UID 形态
    "DX_EXECUTION_ORDER": ("S4", False),
    "DX_UID_FORMAT_INVALID": ("S4", False),
    # DX-DSC-002：发现范围表达不合法（互斥或无法解析）
    "DX_DISCOVERY_SCOPE_INVALID": ("S4", False),
    # 属于 M3/M4/M5 的能力被提前调用（同 S2 的 BP_FEATURE_NOT_ENABLED）
    "DX_FEATURE_NOT_ENABLED": ("S4", False),
}

#: S4 的 ID 前缀（FD-COM-002：带类型前缀的 ULID）。
DX_ID_PREFIXES: dict[str, str] = {
    "dxenv": "S4",  # Environment
    "dxcnt": "S4",  # ConnectionTest
    "dxcap": "S4",  # CapabilityProfileIndex
    "dxdsc": "S4",  # DiscoveryIndex
    "dxexe": "S4",  # Execution
    "dxvld": "S4",  # ValidationRecord
    "dxsnp": "S4",  # SnapshotIndex
    "dxdu": "S4",  # DemoUserCredential（M3）
}


class IdemScope:
    """幂等作用域（00 文档 6.4）。

    提交类与 VALIDATE 使用 S0 的 ``dx.execution``：其中的记录**永不过期清理**
    （FD-IDEM-003、FD-BR-08），因为一次 COMMIT 的幂等结果必须始终可查（DX-EXE-036、
    AC-EXE-05）。探测与发现只是读，作用域可过期。
    """

    EXECUTION = DHIS2_EXECUTION_SCOPE
    CONNECTION_TEST = "dx.connection.test"
    CAPABILITY_PROBE = "dx.capability.probe"
    DISCOVERY = "dx.discovery"
    ENVIRONMENT_REGISTER = "dx.environment.register"


__all__ = [
    "DX_ARTIFACT_MAX_SIZES",
    "DX_ARTIFACT_TYPES",
    "DX_AUDIT_EVENT_TYPES",
    "DX_ERROR_CODES",
    "DX_ID_PREFIXES",
    "IdemScope",
]
