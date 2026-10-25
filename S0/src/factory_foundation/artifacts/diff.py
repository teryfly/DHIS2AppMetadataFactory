"""Generic artifact diff (FD-ART-012).

Text artifacts are compared line by line; JSON artifacts by structural path.
Object-level semantic diffing is intentionally *not* in S0.  Reading two
artifacts' content is external I/O.
"""

from __future__ import annotations

import difflib
import json
from typing import Any

from factory_foundation.artifacts.dto import DiffResult
from factory_foundation.artifacts.service import get_artifact, read_content_bytes
from factory_foundation.common.errors import ArtifactError
from factory_foundation.db.external_io import external_io


@external_io
def diff(a_id: str, b_id: str) -> DiffResult:
    """Diff two artifacts of the same type.

    The type is checked rather than assumed: comparing a METADATA_PACKAGE against a
    VALIDATION_REPORT produces a confident-looking pile of path differences that means
    nothing, and a caller reading "no differences" or "412 differences" out of it has been
    shown an artefact of the comparison rather than of the project (FD-ART-012).
    """
    a_info = get_artifact(a_id)
    b_info = get_artifact(b_id)
    if a_info.type != b_info.type:
        raise ArtifactError(
            f"cannot diff {a_id} ({a_info.type}) against {b_id} ({b_info.type}): "
            "diff compares two versions of one artifact type",
            code="ARTIFACT_ERROR",
            details={"aId": a_id, "bId": b_id, "aType": a_info.type, "bType": b_info.type},
        )
    a_bytes, _, a_media = read_content_bytes(a_id)
    b_bytes, _, b_media = read_content_bytes(b_id)
    if "json" in a_media or "json" in b_media:
        return _json_diff(a_id, b_id, a_bytes, b_bytes)
    return _line_diff(a_id, b_id, a_bytes, b_bytes)


def _line_diff(a_id: str, b_id: str, a_bytes: bytes, b_bytes: bytes) -> DiffResult:
    a_lines = a_bytes.decode("utf-8", "replace").splitlines()
    b_lines = b_bytes.decode("utf-8", "replace").splitlines()
    rows: list[dict[str, Any]] = []
    for line in difflib.unified_diff(a_lines, b_lines, lineterm="", n=2):
        if line.startswith("+++") or line.startswith("---"):
            continue
        if line.startswith("@@"):
            continue
        kind = "add" if line.startswith("+") else "remove" if line.startswith("-") else "context"
        rows.append({"kind": kind, "text": line[1:] if kind != "context" else line})
    return DiffResult(kind="line", a_id=a_id, b_id=b_id, changed=a_bytes != b_bytes, lines=rows)


def _json_diff(a_id: str, b_id: str, a_bytes: bytes, b_bytes: bytes) -> DiffResult:
    a = json.loads(a_bytes.decode("utf-8"))
    b = json.loads(b_bytes.decode("utf-8"))
    rows: list[dict[str, Any]] = []
    _walk_diff(a, b, "", rows)
    return DiffResult(kind="json", a_id=a_id, b_id=b_id, changed=bool(rows), lines=rows)


def _walk_diff(a: Any, b: Any, path: str, out: list[dict[str, Any]]) -> None:
    if type(a) is not type(b) and not (isinstance(a, (int, float)) and isinstance(b, (int, float))):
        out.append({"path": path or "/", "kind": "change", "a": a, "b": b})
        return
    if isinstance(a, dict):
        for key in sorted(set(a) | set(b)):
            child = f"{path}/{key}"
            if key not in a:
                out.append({"path": child, "kind": "add", "b": b[key]})
            elif key not in b:
                out.append({"path": child, "kind": "remove", "a": a[key]})
            else:
                _walk_diff(a[key], b[key], child, out)
    elif isinstance(a, list):
        for i in range(max(len(a), len(b))):
            child = f"{path}[{i}]"
            if i >= len(a):
                out.append({"path": child, "kind": "add", "b": b[i]})
            elif i >= len(b):
                out.append({"path": child, "kind": "remove", "a": a[i]})
            else:
                _walk_diff(a[i], b[i], child, out)
    else:
        if a != b:
            out.append({"path": path or "/", "kind": "change", "a": a, "b": b})
