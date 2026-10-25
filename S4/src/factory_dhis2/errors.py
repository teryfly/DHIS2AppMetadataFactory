"""S4 错误层次（00 文档 6.5、总体 SRS 19.2.1）。

每个公共函数只抛 :class:`FactoryError` 的子类；``code`` 决定 S1 映射成哪个 REST 错误
响应，``retryable`` 决定 Temporal 是否重试该活动。可重试性一律在
:mod:`factory_dhis2.registries` 登记，本文件只负责形状。

凭证永不进入 ``message`` 与 ``details``（DX-CLI-004）：需要引用时放 Secret 引用，
而不是凭证本身。
"""

from __future__ import annotations

from typing import Any

from factory_foundation.api import FactoryError

from factory_dhis2.enums import IssueLevel, SourceStage


class Dhis2Error(FactoryError):
    """S4 错误的基类。"""

    code = "INTERNAL_ERROR"


# --- 环境与保护（DX-ENV）---------------------------------------------------


class EnvironmentInvalidError(Dhis2Error):
    """DX-ENV-001/005：环境档案不合规则。"""

    code = "DX_ENVIRONMENT_INVALID"


class EnvironmentLockedError(Dhis2Error):
    """DX-ENV-007 / DX-AC-05 / DX-BR-11：紧急锁定开启。"""

    code = "ENVIRONMENT_LOCKED"


class EnvironmentInUseError(Dhis2Error):
    """DX-ENV-008/009：仍有进行中的执行，不能禁用或改目标。"""

    code = "DX_ENVIRONMENT_IN_USE"


class OutsideCommitWindowError(Dhis2Error):
    """DX-ENV-002：当前时刻不在允许提交的时间窗内。"""

    code = "DX_OUTSIDE_COMMIT_WINDOW"


class ProductionProtectedError(Dhis2Error):
    """DX-TRK-002 / DX-AC-09：PRODUCTION 类环境禁止的操作。"""

    code = "DX_PRODUCTION_PROTECTED"


class DeletionForbiddenError(Dhis2Error):
    """DX-EXE-038：删除策略或删除安全检查不允许。"""

    code = "DX_DELETION_FORBIDDEN"


# --- DHIS2 传输与能力（DX-CLI / DX-CAP）------------------------------------


class Dhis2UnreachableError(Dhis2Error):
    """DX-CLI-001：连接失败、超时耗尽。"""

    code = "DHIS2_UNREACHABLE"


class Dhis2TlsError(Dhis2Error):
    """DX-CLI-006：要求 TLS 的环境证书无效。"""

    code = "DHIS2_TLS_INVALID"


class Dhis2AuthError(Dhis2Error):
    """总体 SRS 19.2.1：认证失败。"""

    code = "DHIS2_AUTH_ERROR"


class Dhis2PermissionError(Dhis2Error):
    """总体 SRS 19.2.1：权限不足（连接测试据此报告缺失权限）。"""

    code = "DHIS2_PERMISSION_ERROR"


class Dhis2NotFoundError(Dhis2Error):
    """总体 SRS 19.2.1：实例上不存在该对象或端点。"""

    code = "DHIS2_NOT_FOUND"


class Dhis2TimeoutError(Dhis2Error):
    """总体 SRS 19.2.1：单次请求超时（读类可由 Client 重试，写类不得重试）。"""

    code = "DHIS2_TIMEOUT"


class Dhis2ResponseInvalidError(Dhis2Error):
    """DX-CLI-001：响应不是预期的 JSON 形状。"""

    code = "DHIS2_RESPONSE_INVALID"


class Dhis2ImportError(Dhis2Error):
    """总体 SRS 19.2.1：导入被实例拒绝（非 ImportReport 问题清单，那是报告不是异常）。"""

    code = "DHIS2_IMPORT_ERROR"


class Dhis2TaskLostError(Dhis2Error):
    """DX-EXE-035：提交后无响应且无任务 ID 可查——结果不明。"""

    code = "DHIS2_TASK_LOST"


class Dhis2TaskFailedError(Dhis2Error):
    """DX-EXE-033：异步任务本身失败。"""

    code = "DHIS2_TASK_FAILED"


class VersionUnsupportedError(Dhis2Error):
    """DX-CON-001：版本不满足约束或没有对应 Profile。"""

    code = "DHIS2_VERSION_UNSUPPORTED"


class ProfileStaleError(Dhis2Error):
    """DX-CAP-005/006：Capability Profile 已过期，必须先重新探测。"""

    code = "DHIS2_PROFILE_STALE"


# --- 执行（DX-EXE）---------------------------------------------------------


class PayloadHashMismatchError(Dhis2Error):
    """DX-EXE-005 / AC-EXE-02：期望哈希与实际载荷或最近一次成功 VALIDATE 不一致。"""

    code = "DX_PAYLOAD_HASH_MISMATCH"


class ValidationStaleError(Dhis2Error):
    """DX-EXE-029：VALIDATE 只对“哈希 + 环境 + 那一刻状态”有效，状态已变。"""

    code = "DX_VALIDATION_STALE"


class DriftDetectedError(Dhis2Error):
    """DX-EXE-020 / AC-EXE-03：观测基准之后目标环境被他人修改。"""

    code = "DRIFT_DETECTED"


class ExecutionOrderError(Dhis2Error):
    """DX-EXE-037 / DX-BR-12：多执行器部署里前一步没有成功生效，后一步不得执行。"""

    code = "DX_EXECUTION_ORDER"


class ConcurrencyError(Dhis2Error):
    """DX-EXE-030 / AC-EXE-04：同一环境已有进行中的提交类执行。

    S0 的内置目录把 ``CONCURRENCY_ERROR`` 归给 S0；S4 抛出同一代码，S1 才能用同一
    个 REST 映射处理所有来源的竞争。
    """

    code = "CONCURRENCY_ERROR"


class InputArtifactInvalidError(Dhis2Error):
    """DX-EXE-003 / DX-ASM-02：输入产物缺失、类型不符或 manifest 段不完整。"""

    code = "DX_INPUT_ARTIFACT_INVALID"


class UidFormatInvalidError(Dhis2Error):
    """DX-QRY-001：实例返回的 UID 不符合 UID 形态。"""

    code = "DX_UID_FORMAT_INVALID"


class DiscoveryScopeInvalidError(Dhis2Error):
    """DX-DSC-002：发现范围表达不合法。"""

    code = "DX_DISCOVERY_SCOPE_INVALID"


class FeatureNotEnabledError(Dhis2Error):
    """M3/M4/M5 的能力被提前调用（同 S2 的 ``BP_FEATURE_NOT_ENABLED``）。"""

    code = "DX_FEATURE_NOT_ENABLED"


# --- 辅助 ------------------------------------------------------------------


def blocking_issue_codes() -> tuple[str, ...]:
    """供测试断言：会阻断执行流的代码集合。"""
    return (
        EnvironmentInvalidError.code,
        EnvironmentLockedError.code,
        PayloadHashMismatchError.code,
        DriftDetectedError.code,
        ConcurrencyError.code,
        Dhis2AuthError.code,
        Dhis2PermissionError.code,
        VersionUnsupportedError.code,
        ProductionProtectedError.code,
        DeletionForbiddenError.code,
    )


def issue_from_error(
    error: FactoryError,
    *,
    object_key: str | None = None,
    object_uid: str | None = None,
    stage: SourceStage = SourceStage.MAPPING,
    level: IssueLevel = IssueLevel.BLOCKING,
    issue_id: str | None = None,
    artifact_id: str | None = None,
) -> dict[str, Any]:
    """把一次失败转成标准化问题行（DX-EXE-027 的统一问题格式）。

    字段与 :class:`factory_dhis2.dto.issue.Issue` 同一套语义，只是按产物/线上惯例写成
    camelCase；级别键名一律 ``level``（00 文档 6.10："不得使用 ``severity`` 等别名"）。
    名字对不上时的代价是静默的：读的一侧拿到默认级别，一次失败被当成"只是提醒"。

    ``sourceStage`` 默认 MAPPING：无法判定时按总体 SRS FR-VAL-002 的归类精神归到映射。
    """
    return {
        "issueId": issue_id or f"dx-{error.error_code.lower()}",
        "ruleId": error.error_code,
        "level": level.value,
        "objectKey": object_key,
        "objectUid": object_uid,
        "description": error.message,
        "sourceStage": stage.value,
        "artifactId": artifact_id or error.artifact_id,
        "suggestedAction": (
            "重试该活动" if error.retryable else "修正载荷或环境状态后重新校验"
        ),
        "details": dict(error.details),
    }


__all__ = [
    "Dhis2Error",
    "EnvironmentInvalidError",
    "EnvironmentLockedError",
    "EnvironmentInUseError",
    "OutsideCommitWindowError",
    "ProductionProtectedError",
    "DeletionForbiddenError",
    "Dhis2UnreachableError",
    "Dhis2TlsError",
    "Dhis2AuthError",
    "Dhis2PermissionError",
    "Dhis2NotFoundError",
    "Dhis2TimeoutError",
    "Dhis2ResponseInvalidError",
    "Dhis2ImportError",
    "Dhis2TaskLostError",
    "Dhis2TaskFailedError",
    "VersionUnsupportedError",
    "ProfileStaleError",
    "PayloadHashMismatchError",
    "ValidationStaleError",
    "DriftDetectedError",
    "ConcurrencyError",
    "ExecutionOrderError",
    "InputArtifactInvalidError",
    "UidFormatInvalidError",
    "DiscoveryScopeInvalidError",
    "FeatureNotEnabledError",
    "blocking_issue_codes",
    "issue_from_error",
]
