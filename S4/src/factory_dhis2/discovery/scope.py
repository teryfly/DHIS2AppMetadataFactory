"""发现范围（DX-DSC-002、DX-ASM-03）。

三种收窄方式可以叠加：按对象类型、按 code 前缀、按 Program，再加上**明确对象清单**。
最后一种最重要——环境绑定编译时调用方从 Registry 拿到本版本涉及的对象，把它们交给 S4，
就不必对大型实例做全量拉取（一条 ``dataElements`` 就有几十万行的实例是真实存在的）。

范围表达不合法时抛错而不是“退化成全量”：静默扩大范围会在一次调用里拖垮工作进程，
并且让调用方误以为自己已经按范围发现过了。
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

from factory_dhis2.enums import Disposition
from factory_dhis2.errors import DiscoveryScopeInvalidError
from factory_dhis2.profiles.base import VersionProfile

#: SRS 与 S1 实际使用的两种发现用途
RUN_TARGET = "RUN_TARGET"
DESIGN_REFERENCE = "DESIGN_REFERENCE"
KNOWN_KINDS: frozenset[str] = frozenset({RUN_TARGET, DESIGN_REFERENCE, "GREENFIELD"})


@dataclass
class ResolvedScope:
    """解析后的范围：知道要读哪些类型、带什么过滤器、是否只读 default。"""

    kind: str = RUN_TARGET
    dhis2_types: list[str] = field(default_factory=list)
    filters_by_type: dict[str, list[str]] = field(default_factory=dict)
    #: 明确清单里以 UID 给出的部分 → ``id:in:[…]``
    explicit_uids: dict[str, list[str]] = field(default_factory=dict)
    #: 明确清单里以 code 给出的部分 → ``code:in:[…]``。DX-DSC-002【新增】说"一组明确的
    #  UID **或 code**"，两者混在一个桶里按 id 过滤的话，只给 code 的清单会静默读出空结果
    explicit_codes: dict[str, list[str]] = field(default_factory=dict)
    #: DX-DSC-002 的 code 前缀：按 ``code:ilike:`` 请求，再在本地验证"确实是前缀"
    code_prefixes: list[str] = field(default_factory=list)
    #: DX-DSC-001：是否读取范围内对象的完整内容（UPDATE 合并与 Diff 需要）
    with_content: bool = True
    org_unit_depth: int | None = None
    greenfield: bool = False
    #: 需要读取但类型不在目录里的对象（明确清单指向了 Profile 不支持的类型）
    unsupported_types: list[str] = field(default_factory=list)

    @property
    def is_explicit_only(self) -> bool:
        return bool(self.explicit_uids or self.explicit_codes) and not self.dhis2_types

    def types_to_read(self) -> list[str]:
        return sorted(
            set(self.dhis2_types) | set(self.explicit_uids) | set(self.explicit_codes)
        )

    def selection_for(self, dhis2_type: str) -> list[str]:
        """该类型要发出去的**单个**过滤器清单，每一项是一次请求。

        这里返回的是"多次请求"而不是"多个过滤器"，因为 DHIS2 的多个条件是**重复
        ``filter`` 参数**（AND），逗号拼成一个字符串会被当成一个过滤器的值；而 code 前缀
        之间是 OR。所以：明确清单优先（它最窄），否则每个前缀一次请求，结果取并集。
        """
        out: list[str] = []
        uids = self.explicit_uids.get(dhis2_type)
        codes = self.explicit_codes.get(dhis2_type)
        if uids or codes:
            if uids:
                out.append(f"id:in:[{','.join(sorted(set(uids)))}]")
            if codes:
                out.append(f"code:in:[{','.join(sorted(set(codes)))}]")
            return out
        base = [
            *self.filters_by_type.get(dhis2_type, []),
            *self.filters_by_type.get("*", []),
        ]
        if self.code_prefixes:
            return [*[f"code:ilike:{prefix}" for prefix in self.code_prefixes], *base]
        return base or [""]


def resolve(
    scope: str | Mapping[str, Any] | None,
    explicit_refs: Sequence[Mapping[str, Any]] | None,
    profile: VersionProfile,
) -> ResolvedScope:
    """把调用方给的范围与明确清单解析成可执行的读取计划。"""
    document: dict[str, Any] = _as_document(scope)
    kind = str(document.get("kind") or document.get("scope") or RUN_TARGET).upper()
    if kind == DESIGN_REFERENCE.lower().upper():
        kind = DESIGN_REFERENCE
    if kind not in KNOWN_KINDS:
        raise DiscoveryScopeInvalidError(
            f"unknown discovery scope kind {kind!r}",
            details={"kind": kind, "known": sorted(KNOWN_KINDS)},
        )
    greenfield = kind == "GREENFIELD" or bool(document.get("greenfield"))

    types = [str(item) for item in document.get("dhis2Types") or document.get("dhis2_types") or []]
    prefixes = [str(item) for item in document.get("codePrefixes") or document.get("code_prefixes") or []]
    programs = [str(item) for item in document.get("programs") or []]
    depth = document.get("orgUnitDepth", document.get("org_unit_depth"))

    unsupported = [item for item in types if not profile.has_type(item)]
    supported = [item for item in types if profile.has_type(item)]

    filters: dict[str, list[str]] = {}
    if programs and profile.has_type("program"):
        filters.setdefault("program", []).extend(
            [f"id:in:[{','.join(programs)}]"] if len(programs) < 50
            else [f"code:ilike:{item}" for item in programs]
        )

    resolved = ResolvedScope(
        kind=kind,
        dhis2_types=supported,
        filters_by_type=filters,
        code_prefixes=[item for item in prefixes if item],
        org_unit_depth=int(depth) if isinstance(depth, (int, str)) and str(depth).isdigit() else None,
        greenfield=greenfield,
        unsupported_types=unsupported + [
            str(item.get("dhis2Type"))
            for item in (explicit_refs or ())
            if isinstance(item, Mapping)
            and item.get("dhis2Type")
            and not profile.has_type(str(item.get("dhis2Type")))
        ],
    )
    for item in explicit_refs or ():
        if not isinstance(item, Mapping):
            raise DiscoveryScopeInvalidError(
                "an explicit discovery reference must be an object with dhis2Type and uid/code",
                details={"received": type(item).__name__},
            )
        dhis2_type = str(item.get("dhis2Type") or item.get("dhis2_type") or "")
        if not dhis2_type:
            raise DiscoveryScopeInvalidError(
                "an explicit discovery reference needs dhis2Type plus uid or code",
                details={"received": dict(item)},
            )
        # uid 与 code 分别入桶：它们的过滤字段不同（id:in: 与 code:in:）。以前两者都塞进
        # uid 桶，只给 code 的清单会按 id 过滤，永远匹配不到对象。
        uid = item.get("uid")
        code = item.get("code")
        if uid is None and code is None:
            raise DiscoveryScopeInvalidError(
                "an explicit discovery reference needs dhis2Type plus uid or code",
                details={"received": dict(item)},
            )
        if not profile.has_type(dhis2_type):
            continue
        if uid is not None:
            bucket = resolved.explicit_uids.setdefault(dhis2_type, [])
            value = str(uid)
            if value not in bucket:
                bucket.append(value)
        if code is not None:
            bucket = resolved.explicit_codes.setdefault(dhis2_type, [])
            value = str(code)
            if value not in bucket:
                bucket.append(value)

    if greenfield:
        # DX-DSC-003：Greenfield 只读版本、能力与 default 对象
        resolved.dhis2_types = []
        resolved.explicit_uids = {}
        resolved.explicit_codes = {}
        resolved.code_prefixes = []
        resolved.with_content = False
    if not resolved.types_to_read() and not greenfield and resolved.org_unit_depth is None:
        raise DiscoveryScopeInvalidError(
            "the discovery scope selects nothing to read: give dhis2Types, explicit references, "
            "a code prefix, an org unit depth, or mark it greenfield",
            details={"scope": document, "kind": kind},
        )
    return resolved


def dispositions_in(scope: ResolvedScope) -> list[str]:
    """明确清单里出现的处置（供上层决定要不要读内容）。"""
    return [item.value for item in Disposition]


def _as_document(scope: str | Mapping[str, Any] | None) -> dict[str, Any]:
    if scope is None:
        return {}
    if isinstance(scope, str):
        return {"kind": scope.upper()}
    return dict(scope)


__all__ = [
    "DESIGN_REFERENCE",
    "KNOWN_KINDS",
    "RESERVED_UNUSED",
    "RUN_TARGET",
    "ResolvedScope",
    "dispositions_in",
    "resolve",
]

RESERVED_UNUSED = None
