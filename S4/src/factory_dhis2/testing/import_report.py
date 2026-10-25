"""ImportReport 的形状与判定（DX-EXE-027、DX-EXE-033）。

假实例的价值在于它**按 DHIS2 的真实形状回答**：S4 的映射代码读的就是这里这些键
（``status``、``response.stats``、``response.objects[].klass/errCode/params``）。报告形状
如果一份写在实例里、另一份写在解析代码里，两边就会互相骗过——所以这里是唯一权威，
实例与测试都从这里构造、判定。

``errCode`` 用 2.41 Profile ``importReportErrorClasses`` 的类别名而不是 DHIS2 的 ``E00xx``
编号：DX-EXE-027 的来源阶段映射（引用/Schema → MAPPING，页面相关 → PAGE_DESIGN）必须在
离线测试里就能跑通，而类别名正是那张表的键。
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

#: 报告级状态（DHIS2 只有这两个值；HTTP 状态码仍是 200，见 :mod:`factory_dhis2.errors`
#: 里 ``Dhis2ImportError`` 的说明：问题清单是报告，不是异常）。
REPORT_STATUS_SUCCESS = "SUCCESS"
REPORT_STATUS_ERROR = "ERROR"

#: 对象行状态。
ROW_STATUS_OK = "OK"
ROW_STATUS_ERROR = "ERROR"
ROW_STATUS_IGNORED = "IGNORED"

#: 类别词汇表 = 2.41 Profile ``validationRules.importReportErrorClasses`` 的键。
ERROR_CLASSES: frozenset[str] = frozenset(
    {
        "REFERENCE_ERROR",
        "TYPE_ERROR",
        "SCHEMA_ERROR",
        "FIELD_ERROR",
        "IDENTIFIER_ERROR",
        "PAGE_ERROR",
        "UI_ERROR",
        "UNKNOWN",
    }
)

#: 行上 ``type`` 的取值（DHIS2 用过去式；ignored 表示“什么都没发生”）。
CHANGE_CREATED = "CREATED"
CHANGE_UPDATED = "UPDATED"
CHANGE_DELETED = "DELETED"
CHANGE_IGNORED = "IGNORED"
CHANGES: frozenset[str] = frozenset({CHANGE_CREATED, CHANGE_UPDATED, CHANGE_DELETED, CHANGE_IGNORED})

#: DX-EXE-011：2.41 实例侧的导入参数默认值（原子、UID 标识、创建并更新）。
DEFAULT_IMPORT_MODE = "COMMIT"
DEFAULT_IMPORT_STRATEGY = "CREATE_AND_UPDATE"
DEFAULT_IDENTIFIER = "ASSIGNED"
DEFAULT_ATOMIC_MODE = "ALL"
DEFAULT_PREHEAT_MODE = "FULL"

IMPORT_MODE_VALIDATE = "VALIDATE"
IMPORT_MODE_COMMIT = "COMMIT"
ATOMIC_MODE_ALL = "ALL"
ATOMIC_MODE_NONE = "NONE"

_TRUE_TOKENS: frozenset[str] = frozenset({"true", "1", "yes", "y", "on"})


@dataclass(frozen=True)
class ImportOptions:
    """一次 metadata/tracker 导入的参数（从查询参数解析，DX-EXE-011）。"""

    import_mode: str = DEFAULT_IMPORT_MODE
    import_strategy: str = DEFAULT_IMPORT_STRATEGY
    identifier: str = DEFAULT_IDENTIFIER
    atomic_mode: str = DEFAULT_ATOMIC_MODE
    preheat_mode: str = DEFAULT_PREHEAT_MODE
    async_mode: bool = False

    @property
    def validates(self) -> bool:
        return self.import_mode.upper() == IMPORT_MODE_VALIDATE

    @property
    def commits(self) -> bool:
        return self.import_mode.upper() == IMPORT_MODE_COMMIT

    @property
    def atomic(self) -> bool:
        return self.atomic_mode.upper() != ATOMIC_MODE_NONE

    @classmethod
    def from_params(cls, params: Mapping[str, str]) -> ImportOptions:
        """查询参数 → 选项；缺省与非法值都落到实例默认值。

        落到默认值而不是报错是刻意的：真实 DHIS2 对缺省参数同样按服务端默认执行，
        假实例如果在这里更严格，就会掩盖“调用方忘了传参数”这类真实缺陷。
        """
        return cls(
            import_mode=_token(params, "importMode", DEFAULT_IMPORT_MODE),
            import_strategy=_token(params, "importStrategy", DEFAULT_IMPORT_STRATEGY),
            identifier=_token(params, "identifier", DEFAULT_IDENTIFIER),
            atomic_mode=_token(params, "atomicMode", DEFAULT_ATOMIC_MODE),
            preheat_mode=_token(params, "preheatMode", DEFAULT_PREHEAT_MODE),
            async_mode=_bool(params, "async"),
        )

    def as_document(self) -> dict[str, Any]:
        """报告里回显的 ``importOptions``（DHIS2 确实原样回显，便于事后核对参数）。"""
        return {
            "importMode": self.import_mode,
            "importStrategy": self.import_strategy,
            "identifier": self.identifier,
            "atomicMode": self.atomic_mode,
            "preheatMode": self.preheat_mode,
            "async": self.async_mode,
        }


@dataclass(frozen=True)
class ReportRow:
    """ImportReport 里的一条对象记录（``response.objects`` 一项）。"""

    klass: str
    name: str
    status: str = ROW_STATUS_OK
    err_code: str = "OK"
    message: str = ""
    change: str | None = None
    uid: str | None = None
    code: str | None = None
    params: Mapping[str, Any] = field(default_factory=dict)

    @property
    def failed(self) -> bool:
        return self.status.upper() == ROW_STATUS_ERROR

    @property
    def effective(self) -> bool:
        """这一行到底改没改环境（VALIDATE 的行没改，但状态仍是 OK）。"""
        return not self.failed and self.change in {CHANGE_CREATED, CHANGE_UPDATED, CHANGE_DELETED}

    def as_document(self) -> dict[str, Any]:
        params: dict[str, Any] = dict(self.params)
        if self.uid is not None:
            # DX-EXE-027 要把问题映射回 businessKey，UID 必须出现在 params 里
            params.setdefault("uid", self.uid)
        if self.code is not None:
            params.setdefault("code", self.code)
        return {
            "klass": self.klass,
            "name": self.name,
            "import": True,
            "status": self.status,
            "message": self.message,
            "type": self.change,
            "id": self.uid,
            "errCode": self.err_code,
            "errorClass": self.err_code if self.failed else None,
            "params": params,
        }

    @classmethod
    def from_document(cls, raw: Mapping[str, Any]) -> ReportRow:
        """接受 DHIS2 形状的 dict，好让测试可以直接写报告行。"""
        err_code = str(raw.get("errCode") or raw.get("errorClass") or "OK")
        return cls(
            klass=str(raw.get("klass") or ""),
            name=str(raw.get("name") or ""),
            status=str(raw.get("status") or ROW_STATUS_OK),
            err_code=err_code,
            message=str(raw.get("message") or ""),
            change=_optional_str(raw.get("type")),
            uid=_optional_str(raw.get("id") or raw.get("uid")),
            code=_optional_str(raw.get("code")),
            params=dict(raw.get("params") or {}),
        )


def klass_for_type(dhis2_type: str) -> str:
    """``dataElement`` → ``DataElement``：DHIS2 的 ``klass`` 就是 Java 类名。"""
    if not dhis2_type:
        return dhis2_type
    return f"{dhis2_type[0].upper()}{dhis2_type[1:]}"


def build_report(
    rows: Sequence[ReportRow],
    options: ImportOptions,
    *,
    rolled_back: bool = False,
    message: str = "",
) -> dict[str, Any]:
    """把行集合拼成 DHIS2 的 ImportReport。

    ``rolled_back=True`` 时 created/updated/deleted 归零：原子模式下一个错就全不生效
    （DX-EXE-011），如果报告仍然报“已创建 3 个”，执行报告就把回滚掉的写入说成生效了。
    """
    created = _count(rows, CHANGE_CREATED)
    updated = _count(rows, CHANGE_UPDATED)
    deleted = _count(rows, CHANGE_DELETED)
    invalid = sum(1 for row in rows if row.failed)
    ignored = max(len(rows) - created - updated - deleted - invalid, 0)
    if rolled_back:
        ignored += created + updated + deleted
        created = updated = deleted = 0
    status = REPORT_STATUS_ERROR if invalid or rolled_back else REPORT_STATUS_SUCCESS
    stats = {
        "created": created,
        "updated": updated,
        "deleted": deleted,
        "ignored": ignored,
        "invalid": invalid,
        "read": len(rows),
    }
    counts = {
        "total": len(rows),
        "created": created,
        "updated": updated,
        "deleted": deleted,
        "ignored": ignored,
        "invalid": invalid,
    }
    return {
        "status": status,
        "httpStatus": "OK" if status == REPORT_STATUS_SUCCESS else "Bad Request",
        "message": message,
        "response": {
            "status": status,
            "stats": stats,
            "mode": options.import_mode,
            "strategy": options.import_strategy,
            "dryRun": options.validates,
            "importOptions": options.as_document(),
            "objects": [row.as_document() for row in rows],
            "counts": counts,
        },
    }


def failure_report(rows: Iterable[Mapping[str, Any]], options: ImportOptions) -> dict[str, Any]:
    """把测试写的错误行变成一个失败报告（AC-VAL-02 的用例来源）。"""
    materialized = [ReportRow.from_document(row) for row in rows]
    return build_report(materialized, options)


def report_status(report: Mapping[str, Any]) -> str:
    return str(report.get("status") or "")


def report_is_success(report: Mapping[str, Any]) -> bool:
    return report_status(report) == REPORT_STATUS_SUCCESS


def report_rows(report: Mapping[str, Any]) -> list[ReportRow]:
    """读出 ``response.objects``；供断言与错误映射使用。"""
    response = report.get("response")
    objects = response.get("objects") if isinstance(response, Mapping) else None
    if not isinstance(objects, Sequence) or isinstance(objects, (str, bytes)):
        return []
    return [ReportRow.from_document(row) for row in objects if isinstance(row, Mapping)]


def error_class_of(err_code: str) -> str:
    """errCode → 类别；不在词汇表里的按 UNKNOWN（DX-EXE-027 的“无法判定”）。"""
    token = (err_code or "").strip().upper()
    return token if token in ERROR_CLASSES else "UNKNOWN"


def _count(rows: Sequence[ReportRow], change: str) -> int:
    return sum(1 for row in rows if not row.failed and row.change == change)


def _token(params: Mapping[str, str], name: str, default: str) -> str:
    value = params.get(name)
    if value is None or not str(value).strip():
        return default
    return str(value).strip().upper()


def _bool(params: Mapping[str, str], name: str) -> bool:
    value = params.get(name)
    if value is None:
        return False
    return str(value).strip().lower() in _TRUE_TOKENS


def _optional_str(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value)
    return text or None


__all__ = [
    "ATOMIC_MODE_ALL",
    "ATOMIC_MODE_NONE",
    "CHANGE_CREATED",
    "CHANGE_DELETED",
    "CHANGE_IGNORED",
    "CHANGE_UPDATED",
    "CHANGES",
    "ERROR_CLASSES",
    "IMPORT_MODE_COMMIT",
    "IMPORT_MODE_VALIDATE",
    "REPORT_STATUS_ERROR",
    "REPORT_STATUS_SUCCESS",
    "ROW_STATUS_ERROR",
    "ROW_STATUS_IGNORED",
    "ROW_STATUS_OK",
    "ImportOptions",
    "ReportRow",
    "build_report",
    "error_class_of",
    "failure_report",
    "klass_for_type",
    "report_is_success",
    "report_rows",
    "report_status",
]
