"""哈希与规范 JSON 的 S4 侧薄封装。

S0 已经提供 ``canonical_json`` / ``structure_hash`` / ``content_hash``；这里只补一个短指纹，
用于派生稳定的问题 ID 与对账证据的引用，避免把 64 位十六进制塞进每个 ID。
"""

from __future__ import annotations

import hashlib

from factory_foundation.api import canonical_json, content_hash, structure_hash

__all__ = ["canonical_json", "content_hash", "short_digest", "structure_hash"]


def short_digest(value: object, length: int = 12) -> str:
    """对任意值给出短而稳定的指纹（同一输入永远同一结果）。"""
    raw = value.encode("utf-8") if isinstance(value, str) else canonical_json(value)
    return hashlib.sha256(raw).hexdigest()[:length]
