"""内容规范化与内容哈希（DX-DSC-007、AC-EXE-07）。

漂移检查、Snapshot 与 Diff 全都建立在“内容哈希”之上，所以哈希的**排除清单**就是要害：
DHIS2 在几乎每次写入时都会刷新 ``lastUpdated``，还会附带 ``href``/``access`` 这类派生字段。
不排除它们，任何人碰一下对象（哪怕什么都没改）都会让我们报漂移，于是漂移检查很快就被
当成噪音忽略掉——那比没有更糟。

排除清单由版本 Profile 给出（``profiles/data/<系列>/volatile_fields.json``），不写在代码里：
不同版本的派生字段集合本来就不同（DX-CAP-001）。
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from factory_foundation.api import structure_hash

#: 递归进入这些键时同样排除易变字段（嵌套对象与集合）
_DESCEND = (Mapping, dict, list, tuple)

#: 只在**顶层**排除的字段。``id`` 不是"易变或派生"字段（DX-DSC-007 的措辞是 lastUpdated、
#  created、href、access 等），它是对象的身份：嵌套层的 ``id`` 是"引用了谁"，抹掉就等于
#  承认"换一个被引用对象不算内容变化"，于是引用被改写时哈希不变、漂移检查漏报。顶层的
#  ``id`` 已经在索引里单独记着，排除它只是避免冗余。
_TOP_LEVEL_ONLY: frozenset[str] = frozenset({"id"})


def normalize_object(
    document: Mapping[str, Any] | Any,
    volatile_fields: Sequence[str],
) -> Any:
    """递归去掉易变/派生字段，并把字典键排序后返回可哈希的结构。

    排序交给 S0 的规范化 JSON 做（``structure_hash`` 内部就是它），这里只负责**删**：
    两处都排序会让嵌套列表的顺序变化被抵消掉——列表顺序在某些对象里是有意义的
    （如 ``categoryOptionCategories`` 的维度顺序），因此列表原样保留。
    """
    excluded = frozenset(volatile_fields)
    return _strip(document, excluded, top_level=True)


def _strip(value: Any, excluded: frozenset[str], *, top_level: bool = False) -> Any:
    if isinstance(value, Mapping):
        return {
            str(key): _strip(item, excluded)
            for key, item in value.items()
            if not _drops(str(key), excluded, top_level=top_level)
        }
    if isinstance(value, (list, tuple)):
        return [_strip(item, excluded) for item in value]
    return value


def _drops(key: str, excluded: frozenset[str], *, top_level: bool) -> bool:
    if key not in excluded:
        return False
    # 只在顶层排除的身份字段，在嵌套层是"引用了谁"，抹掉它就会漏报引用改写
    return not (key in _TOP_LEVEL_ONLY and not top_level)


def object_hash(
    document: Mapping[str, Any] | Any,
    volatile_fields: Sequence[str],
) -> str:
    """排除易变字段后的规范化 JSON 哈希。"""
    return structure_hash(normalize_object(document, volatile_fields))


def changed_fields(
    before: Mapping[str, Any],
    after: Mapping[str, Any],
    volatile_fields: Sequence[str],
    *,
    only: Sequence[str] | None = None,
) -> list[dict[str, Any]]:
    """字段级差异（DX-DIFF-001 的“更新（字段级）”与 DX-EXE-040 的逐字段比对）。

    ``only`` 给出受控字段清单时，只比较这些字段：UPDATE 对象不该拥有的字段本来就不归
    本次部署管（BP-REG-010），把它们算成差异会产生一堆假报警。
    """
    normalized_before = normalize_object(before, volatile_fields)
    normalized_after = normalize_object(after, volatile_fields)
    if not isinstance(normalized_before, dict) or not isinstance(normalized_after, dict):
        return []
    keys = sorted(set(normalized_before) | set(normalized_after))
    if only is not None:
        wanted = set(only)
        keys = [key for key in keys if key in wanted]
    out: list[dict[str, Any]] = []
    for key in keys:
        left = normalized_before.get(key)
        right = normalized_after.get(key)
        if left != right:
            out.append(
                {
                    "field": key,
                    "before": _preview(left),
                    "after": _preview(right),
                }
            )
    return out


def _preview(value: Any) -> Any:
    """差异预览：长内容截断，避免把整个子对象塞进报告。"""
    if isinstance(value, (dict, list)) and len(str(value)) > 300:
        return {"truncated": str(type(value).__name__), "summary": str(value)[:300]}
    return value


__all__ = ["changed_fields", "normalize_object", "object_hash"]
