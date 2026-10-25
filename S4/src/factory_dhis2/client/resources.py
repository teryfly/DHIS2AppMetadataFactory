"""逻辑资源名 → 实际请求（DX-CLI-005、FR-API-001/002）。

调用方永远只说逻辑名（``metadata.import``、``tracker.events``）。端点、方法、参数名与
分页方式都由该环境的 Capability Profile 决定，代码里不出现任何版本相关路径——这正是
新增 ``Dhis242Profile`` 不用改 Client 的机制保证（DX-NFR-07）。

路径里的 ``{…}`` 是**代入槽**：``{typePath}`` 从同目录里该类型的端点取，其余（``{uid}``、
``{namespace}``、``{key}``、``{objectName}``、``{taskPath}``）由调用点提供。槽值一律做
URL 段编码，防止调用方给出的 id 拼出越权路径。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import quote

from factory_dhis2.errors import Dhis2NotFoundError
from factory_dhis2.profiles.base import ResourceEntry, VersionProfile

#: 只允许标识符形态的槽值：DHIS2 UID/code/命名空间/键都落在这个集合里
_SAFE_SEGMENT = re.compile(r"^[A-Za-z0-9_.\-~@+/]*$")
_SLOT = re.compile(r"\{([A-Za-z][A-Za-z0-9_]*)\}")
#: 一个查询参数名长什么样：不符合这个形状的 ``parameters`` 取值是默认值声明，不是参数名
_PARAM_NAME = re.compile(r"^[A-Za-z][A-Za-z0-9_.]*$")

#: 分页方式（DX-CLI-005）
PAGING_PAGER = "pager"
PAGING_TOKEN = "token"
PAGING_NONE = "none"


@dataclass(frozen=True)
class ResolvedRequest:
    method: str
    path: str
    #: ``filter`` 可以有多个值：DHIS2 用重复参数表达条件之间的 AND
    params: dict[str, Any]
    paging: str
    tracker_api: bool
    logical_name: str
    credential_scope: str = field(default="discovery")

    def describe(self) -> str:
        return f"{self.method} {self.path}"


def resolve(
    profile: VersionProfile,
    logical_name: str,
    *,
    dhis2_type: str | None = None,
    path_args: dict[str, str] | None = None,
    params: dict[str, str] | None = None,
) -> ResolvedRequest:
    """把逻辑名解析成一个可发送的请求描述。"""
    entry: ResourceEntry = profile.require_resource(logical_name)
    if "{typePath}" in entry.path and dhis2_type:
        # {typePath} 用该类型自己的端点代入：目录里已登记，不在此处硬编码。凡是路径里带这个
        # 槽的资源都适用（metadata.read 的集合、metadata.read.one 的单对象、metadata.sharing
        # 的共享设置），否则新登记的按类型资源会在解析时留下一个花括号字面量。
        entry = ResourceEntry(
            logical_name=entry.logical_name,
            path=entry.path.replace("{typePath}", profile.type_resource(dhis2_type).path),
            method=entry.method,
            parameters=dict(entry.parameters),
            paging=entry.paging,
            tracker_api=entry.tracker_api,
            credential_scope=entry.credential_scope,
        )
    elif entry.logical_name == "schemas" and dhis2_type:
        entry = ResourceEntry(
            logical_name=entry.logical_name,
            path=_fill(entry.path, {"objectName": _singular(dhis2_type)}),
            method=entry.method,
            parameters=dict(entry.parameters),
            paging=entry.paging,
            tracker_api=entry.tracker_api,
            credential_scope=entry.credential_scope,
        )

    path = _fill(entry.path, path_args or {})
    return ResolvedRequest(
        method=entry.method,
        path=path,
        params=_map_params(entry, params or {}),
        paging=entry.paging or PAGING_NONE,
        tracker_api=entry.tracker_api,
        logical_name=entry.logical_name,
        credential_scope=entry.credential_scope,
    )


def query_skip(key: str) -> set[str]:
    """``fields``/``page`` 这类键是**默认值声明**，不是每次都要带上的查询参数。"""
    return {key} if key in {"fields", "page", "pageSize", "filter", "criterion"} else set()


def _map_params(entry: ResourceEntry, supplied: dict[str, Any]) -> dict[str, Any]:
    """调用方用的逻辑参数名 → 该版本的实际参数名。

    ``parameters`` 里混着两种声明：``{"page": "page"}`` 这种**参数名映射**，以及 ``me`` 的
    ``{"fields": "id,name,authorities[:is]"}`` 这种**默认取值声明**（值是给实例的取值，不是
    参数名）。把后者当成参数名会拼出 ``?<一长串字段>=<值>`` 这种请求，所以只有看起来像标识符
    的值才当映射用；否则按声明名传参，并在调用方没给时在请求里带上这个默认取值。
    """
    out: dict[str, str] = {}
    for name, declared in entry.parameters.items():
        if _PARAM_NAME.fullmatch(declared or ""):
            if declared in supplied:
                out[name] = supplied.pop(declared)
        elif name not in supplied:
            out[name] = declared
    for name, value in supplied.items():
        out.setdefault(name, value)
    return out


def _fill(path: str, args: dict[str, str]) -> str:
    def replace(match: re.Match[str]) -> str:
        slot = match.group(1)
        if slot not in args:
            raise Dhis2NotFoundError(
                f"resource path {path!r} needs {slot!r} but the call did not provide it",
                details={"path": path, "slot": slot, "provided": sorted(args)},
            )
        return _encode_segment(str(args[slot]), slot)

    return _SLOT.sub(replace, path)


def _encode_segment(value: str, slot: str) -> str:
    if not value:
        raise Dhis2NotFoundError(
            f"empty path segment for {slot!r}", details={"slot": slot}
        )
    if slot == "taskPath":
        # 任务地址是实例原样返回的，可能多段；只编码段分隔之外的危险字符
        return "/".join(_encode_segment(part, "uid") for part in value.strip("/").split("/"))
    if not _SAFE_SEGMENT.fullmatch(value):
        raise Dhis2NotFoundError(
            f"path segment for {slot!r} has an unsupported form: {value!r}",
            details={"slot": slot},
        )
    return quote(value, safe="")


def _singular(dhis2_type: str) -> str:
    return dhis2_type


__all__ = [
    "PAGING_NONE",
    "PAGING_PAGER",
    "PAGING_TOKEN",
    "ResolvedRequest",
    "query_skip",
    "resolve",
]
