"""S4 枚举（SRS 2 章领域概念、5 章字段取值）。"""

from __future__ import annotations

import enum


@enum.unique
class EnvironmentClass(str, enum.Enum):
    """DX-ENV-001：环境类别。"""

    DEV = "DEV"
    TEST = "TEST"
    UAT = "UAT"
    PRODUCTION = "PRODUCTION"


#: DX-ENV-002/005、DX-TRK-002、DX-BR-10/11：PRODUCTION 类环境的判定集合。
PRODUCTION_CLASSES: frozenset[str] = frozenset({EnvironmentClass.PRODUCTION.value})


@enum.unique
class AuthMode(str, enum.Enum):
    """DX-ENV-001：认证方式，优先个人访问令牌（DX-ENV-006）。"""

    PAT = "PAT"
    BASIC = "BASIC"


@enum.unique
class EnvironmentStatus(str, enum.Enum):
    """DX-ENV-001。"""

    ACTIVE = "ACTIVE"
    DISABLED = "DISABLED"


@enum.unique
class CredentialScope(str, enum.Enum):
    """总体 SRS 21.3：按操作区分的凭证范围。"""

    DISCOVERY = "discovery"
    IMPORT = "import"
    TRACKER = "tracker"
    DATASTORE = "datastore"
    APPS = "apps"
    MAINTENANCE = "maintenance"


#: 读类操作只允许使用 discovery；写类操作各自绑定自己的范围（DX-BR-03/04）。
READ_SCOPES: frozenset[CredentialScope] = frozenset({CredentialScope.DISCOVERY})

#: DX-ENV-002 之外，MVP 就要求配置的凭证范围（连接测试按调用方需要范围检查）。
MVP_SCOPES: tuple[CredentialScope, ...] = (
    CredentialScope.DISCOVERY,
    CredentialScope.IMPORT,
)


@enum.unique
class LlmDataPolicy(str, enum.Enum):
    """DX-ENV-001 的 ``llmDataPolicy``：该环境数据允许发送到的最宽提供方类别。

    取值与总体 SRS 20.2 的提供方类别一致；D-07 由 S1 取组织策略与全部目标环境策略
    中最严格者，S4 只负责如实回报。
    """

    LOCAL_ONLY = "LOCAL_ONLY"
    PRIVATE_NETWORK = "PRIVATE_NETWORK"
    APPROVED_EXTERNAL = "APPROVED_EXTERNAL"
    UNRESTRICTED = "UNRESTRICTED"

    @property
    def rank(self) -> int:
        """越小越严格，供“最严格者”比较使用（D-07）。"""
        return _POLICY_RANK[self]


_POLICY_RANK: dict[LlmDataPolicy, int] = {
    LlmDataPolicy.LOCAL_ONLY: 0,
    LlmDataPolicy.PRIVATE_NETWORK: 1,
    LlmDataPolicy.APPROVED_EXTERNAL: 2,
    LlmDataPolicy.UNRESTRICTED: 3,
}


@enum.unique
class DeletionPolicy(str, enum.Enum):
    """DX-ENV-002/DX-EXE-038 的删除策略。

    SRS 列出字段但未枚举取值，此处按 15.6“定向删除”的限制定义（【新增】，理由见
    README“与 SRS 的偏离”）：删除只针对 Managed Scope 内且通过关联数据检查的对象。
    """

    #: 一律不允许删除（PRODUCTION 默认）
    FORBID = "FORBID"
    #: 仅允许无关联数据的定向删除，需随 DEPLOY_GATE 审批
    SAFE_ONLY = "SAFE_ONLY"


@enum.unique
class ExecutorType(str, enum.Enum):
    """总体 SRS 15.1 的执行器。"""

    METADATA = "METADATA"
    TRACKER_DATA = "TRACKER_DATA"
    DATASTORE = "DATASTORE"
    APP_UPLOAD = "APP_UPLOAD"


#: DX-EXE-037 / DX-BR-12：一次部署含多个执行器时的固定顺序。
EXECUTOR_ORDER: tuple[ExecutorType, ...] = (
    ExecutorType.METADATA,
    ExecutorType.DATASTORE,
    ExecutorType.TRACKER_DATA,
    ExecutorType.APP_UPLOAD,
)


@enum.unique
class ExecutionMode(str, enum.Enum):
    """``dx.execution.mode``（SRS 5 章）。"""

    PLAN = "PLAN"
    VALIDATE = "VALIDATE"
    COMMIT = "COMMIT"
    POST_VERIFY = "POST_VERIFY"
    FIXTURE = "FIXTURE"
    DELETE = "DELETE"
    MAINTENANCE = "MAINTENANCE"


#: DX-EXE-030：需要环境级独占锁的模式。
COMMITTING_MODES: frozenset[ExecutionMode] = frozenset(
    {
        ExecutionMode.COMMIT,
        ExecutionMode.FIXTURE,
        ExecutionMode.DELETE,
        ExecutionMode.MAINTENANCE,
    }
)


@enum.unique
class ExecutionStatus(str, enum.Enum):
    """S1 的 ``ExecutionStatus`` 只有三个成员，字符串必须逐字一致。"""

    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    NEEDS_MANUAL_CHECK = "NEEDS_MANUAL_CHECK"


@enum.unique
class StepStatus(str, enum.Enum):
    """DX-EXE-004：步骤状态，用于按幂等键续跑。"""

    PENDING = "PENDING"
    RUNNING = "RUNNING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    SKIPPED = "SKIPPED"


@enum.unique
class StepName(str, enum.Enum):
    """总体 SRS 15.2 的标准执行流程步骤。"""

    PLAN = "PLAN"
    DRIFT_CHECK = "DRIFT_CHECK"
    SNAPSHOT = "SNAPSHOT"
    VALIDATE = "VALIDATE"
    EXPRESSION_VALIDATE = "EXPRESSION_VALIDATE"
    COMMIT = "COMMIT"
    ASYNC_POLL = "ASYNC_POLL"
    POST_VERIFY = "POST_VERIFY"
    MAINTENANCE = "MAINTENANCE"
    REPORT = "REPORT"


@enum.unique
class CapabilityFreshness(str, enum.Enum):
    """DX-CAP-006：S1 的 ``CapabilityFreshness`` 三值，大小写敏感。"""

    VALID = "VALID"
    EXPIRED = "EXPIRED"
    VERSION_CHANGED = "VERSION_CHANGED"


@enum.unique
class ReconcileOutcome(str, enum.Enum):
    """DX-EXE-035：对账判断。S1 的镜像默认值为 UNKNOWN。"""

    APPLIED = "APPLIED"
    NOT_APPLIED = "NOT_APPLIED"
    PARTIAL_MISMATCH = "PARTIAL_MISMATCH"
    UNKNOWN = "UNKNOWN"


@enum.unique
class ManualCheckOutcome(str, enum.Enum):
    """DX-EXE-031：人工核实结论（S1 校验 ``PASSED`` / ``FAILED``）。"""

    PASSED = "PASSED"
    FAILED = "FAILED"


@enum.unique
class IssueLevel(str, enum.Enum):
    """问题级别（00 文档 6.10）：字段一律叫 ``level``，取值 ``BLOCKING``/``WARNING``/``INFO``。"""

    BLOCKING = "BLOCKING"
    WARNING = "WARNING"
    INFO = "INFO"


@enum.unique
class SourceStage(str, enum.Enum):
    """DX-EXE-027：ImportReport 问题映射回的设计阶段。"""

    MAPPING = "MAPPING"
    PAGE_DESIGN = "PAGE_DESIGN"


@enum.unique
class Disposition(str, enum.Enum):
    """manifest 中的对象处置（S2 的 Registry 词汇，S4 只读取）。"""

    CREATE = "CREATE"
    UPDATE = "UPDATE"
    REUSE = "REUSE"
    REFERENCE = "REFERENCE"
    EXTERNAL = "EXTERNAL"
    DEPRECATED = "DEPRECATED"


#: DX-EXE-020：漂移检查关注的处置——观测基准之后可能被他人修改的对象。
DRIFT_WATCHED_DISPOSITIONS: frozenset[str] = frozenset(
    {
        Disposition.REUSE.value,
        Disposition.REFERENCE.value,
        Disposition.UPDATE.value,
    }
)


@enum.unique
class ObjectChangeKind(str, enum.Enum):
    """DX-DIFF-001：以对象为单位的四种展示。"""

    CREATED = "CREATED"
    UPDATED = "UPDATED"
    UNCHANGED = "UNCHANGED"
    REFERENCE_ONLY = "REFERENCE_ONLY"


@enum.unique
class MaintenanceAction(str, enum.Enum):
    """DX-EXE-013：MVP 仅支持清理应用缓存。"""

    CLEAR_APP_CACHE = "CLEAR_APP_CACHE"


@enum.unique
class CheckState(str, enum.Enum):
    """DX-CON-001/002：连接测试单项结果。"""

    PASSED = "PASSED"
    FAILED = "FAILED"
    SKIPPED = "SKIPPED"


#: DX-DSC-003：Greenfield 发现必须读取的 default 对象。
DEFAULT_OBJECT_TYPES: tuple[str, ...] = (
    "categoryCombo",
    "category",
    "categoryOption",
    "categoryOptionCombo",
)
