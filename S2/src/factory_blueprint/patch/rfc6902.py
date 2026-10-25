"""RFC 6902 (JSON Patch) application — a pure function over Blueprint documents.

S2 applies operations in the order the author declared them and never reorders or
repairs them (overall SRS C-07).  A malformed operation raises rather than
returning a rejection list: it means the author did not produce a patch at all.
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

from factory_foundation.api import FactoryError

from factory_blueprint.content.pointer import PointerError, parse_pointer

VALID_OPERATIONS: frozenset[str] = frozenset(
    {"add", "remove", "replace", "move", "copy", "test"}
)
VALUE_REQUIRED: frozenset[str] = frozenset({"add", "replace", "test"})
FROM_REQUIRED: frozenset[str] = frozenset({"move", "copy"})
LAST_ARRAY_ELEMENT = "-"


class PatchMalformed(FactoryError):
    code = "BP_PATCH_MALFORMED"


@dataclass(frozen=True)
class Operation:
    op: str
    path: str
    value: Any = None
    from_: str | None = None
    has_value: bool = False

    def to_json(self) -> dict[str, Any]:
        out: dict[str, Any] = {"op": self.op, "path": self.path}
        if self.has_value:
            out["value"] = self.value
        if self.from_ is not None:
            out["from"] = self.from_
        return out


def parse_operations(raw: Sequence[Any]) -> list[Operation]:
    """Check the shape of every operation; the document is not involved yet."""
    operations: list[Operation] = []
    for index, item in enumerate(raw):
        if not isinstance(item, dict):
            raise PatchMalformed(f"operation #{index} is not an object")
        op = item.get("op")
        path = item.get("path")
        if not isinstance(op, str) or op not in VALID_OPERATIONS:
            raise PatchMalformed(f"operation #{index} has invalid op: {op!r}")
        if not isinstance(path, str):
            raise PatchMalformed(f"operation #{index} is missing a string path")
        _check_pointer(f"operation #{index} path", path)

        has_value = "value" in item
        if op in VALUE_REQUIRED and not has_value:
            raise PatchMalformed(f"operation #{index} ({op}) is missing a value")
        from_: str | None = None
        if op in FROM_REQUIRED:
            source = item.get("from")
            if not isinstance(source, str):
                raise PatchMalformed(f"operation #{index} ({op}) is missing a string 'from'")
            _check_pointer(f"operation #{index} 'from'", source)
            from_ = source
        operations.append(
            Operation(
                op=op,
                path=path,
                value=item.get("value"),
                from_=from_,
                has_value=has_value,
            )
        )
    return operations


def _check_pointer(label: str, pointer: str) -> None:
    try:
        parse_pointer(pointer)
    except PointerError as exc:
        raise PatchMalformed(f"{label} is not a valid JSON Pointer: {exc}") from exc


def _deep_copy(value: Any) -> Any:
    return json.loads(json.dumps(value, ensure_ascii=False))


def _child(container: Any, token: str) -> Any:
    if isinstance(container, dict):
        if token in container:
            return container[token]
        raise PatchMalformed(f"path segment {token!r} does not exist")
    if isinstance(container, list):
        index = _array_index(container, token)
        if index >= len(container):
            raise PatchMalformed(f"array index {token} out of range")
        return container[index]
    raise PatchMalformed(f"cannot descend into a {type(container).__name__}")


def _array_index(sequence: list[Any], token: str) -> int:
    if token == LAST_ARRAY_ELEMENT:
        return len(sequence)
    try:
        index = int(token)
    except ValueError as exc:
        raise PatchMalformed(f"invalid array index {token!r}") from exc
    if index < 0:
        raise PatchMalformed(f"negative array index {token!r}")
    return index


def _get(document: Any, tokens: Sequence[str]) -> Any:
    node = document
    for token in tokens:
        node = _child(node, token)
    return node


def _exists(document: Any, tokens: Sequence[str]) -> bool:
    try:
        _get(document, tokens)
    except PatchMalformed:
        return False
    return True


def _parent_and_token(document: Any, tokens: Sequence[str]) -> tuple[Any, str]:
    if not tokens:
        raise PatchMalformed("the document root cannot be addressed this way")
    parent = document
    for token in tokens[:-1]:
        parent = _child(parent, token)
    return parent, tokens[-1]


def _set(document: Any, tokens: Sequence[str], value: Any, *, replace: bool) -> None:
    parent, token = _parent_and_token(document, tokens)
    if isinstance(parent, dict):
        if replace and token not in parent:
            raise PatchMalformed(f"cannot replace missing member {token!r}")
        parent[token] = value
        return
    if isinstance(parent, list):
        index = _array_index(parent, token)
        if replace:
            if index >= len(parent):
                raise PatchMalformed(f"array index {token} out of range")
            parent[index] = value
            return
        if index > len(parent):
            raise PatchMalformed(f"array index {token} out of range")
        parent.insert(index, value)
        return
    raise PatchMalformed(f"cannot set a member on a {type(parent).__name__}")


def _remove(document: Any, tokens: Sequence[str]) -> Any:
    parent, token = _parent_and_token(document, tokens)
    if isinstance(parent, dict):
        if token not in parent:
            raise PatchMalformed(f"cannot remove missing member {token!r}")
        return parent.pop(token)
    if isinstance(parent, list):
        index = _array_index(parent, token)
        if index >= len(parent):
            raise PatchMalformed(f"array index {token} out of range")
        return parent.pop(index)
    raise PatchMalformed(f"cannot remove from a {type(parent).__name__}")


def apply_operations(document: dict[str, Any], operations: Sequence[Operation]) -> dict[str, Any]:
    """Return a new document with the operations applied; the input is unchanged."""
    result: dict[str, Any] = _deep_copy(document)
    for position, operation in enumerate(operations):
        _apply_one(result, operation, position)
    return result


def _apply_one(document: dict[str, Any], operation: Operation, position: int) -> None:
    where = f"operation #{position} ({operation.op} {operation.path})"
    tokens = parse_pointer(operation.path)
    try:
        if operation.op == "add":
            _set(document, tokens, _deep_copy(operation.value), replace=False)
        elif operation.op == "replace":
            _set(document, tokens, _deep_copy(operation.value), replace=True)
        elif operation.op == "remove":
            _remove(document, tokens)
        elif operation.op == "test":
            if not _exists(document, tokens):
                raise PatchMalformed("test target does not exist")
            if _get(document, tokens) != operation.value:
                raise PatchMalformed("test failed")
        else:  # move / copy
            if operation.from_ is None:  # pragma: no cover - parse_operations rejects it
                raise PatchMalformed("missing 'from'")
            source = parse_pointer(operation.from_)
            if not _exists(document, source):
                raise PatchMalformed(f"source {operation.from_} does not exist")
            moved = (
                _deep_copy(_get(document, source))
                if operation.op == "copy"
                else _remove(document, source)
            )
            _set(document, tokens, moved, replace=False)
    except PatchMalformed as exc:
        raise PatchMalformed(f"{where}: {exc.message}") from exc
