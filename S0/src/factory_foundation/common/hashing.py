"""Deterministic JSON serialisation and hashing (FD-COM-003).

Two canonical forms are provided and *every* subsystem must use these rather
than hand-rolling serialisation before hashing:

* :func:`canonical_json` — sorted keys, compact separators, UTF-8.  Used to hash
  data structures.
* :func:`file_json` — sorted keys, fixed 2-space indent, UTF-8, LF, trailing
  newline.  Used to write JSON-file artifacts.

Hash output format is ``sha256:<hex>``.  A data-structure hash always uses the
canonical form; an artifact's *content* hash is the hash of its file bytes.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any

_HASH_PREFIX = "sha256:"


def _default(obj: Any) -> Any:
    # Support a narrow set of common non-JSON-native types deterministically.
    if hasattr(obj, "model_dump"):  # pydantic v2 model
        return obj.model_dump(mode="json")
    if hasattr(obj, "isoformat"):  # datetime/date/time
        return obj.isoformat()
    raise TypeError(f"object of type {type(obj).__name__} is not JSON serialisable")


def canonical_json(value: Any) -> bytes:
    """Return the canonical UTF-8 encoding used for structural hashing."""
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        default=_default,
        allow_nan=False,
    ).encode("utf-8")


def file_json(value: Any) -> bytes:
    """Return the file-form JSON bytes: 2-space indent, LF, trailing newline."""
    text = json.dumps(
        value,
        sort_keys=True,
        indent=2,
        ensure_ascii=False,
        default=_default,
        allow_nan=False,
    )
    return (text + "\n").encode("utf-8")


def sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def content_hash(data: bytes) -> str:
    """Hash raw bytes (e.g. artifact file bytes) as ``sha256:<hex>``."""
    return f"{_HASH_PREFIX}{sha256_hex(data)}"


def structure_hash(value: Any) -> str:
    """Hash a JSON-able data structure using the canonical form."""
    return content_hash(canonical_json(value))


def is_hash_formatted(value: str) -> bool:
    import re

    return bool(re.fullmatch(r"sha256:[0-9a-f]{64}", value))


def normalize_hash(value: str) -> str:
    """Accept ``sha256:<hex>`` or a bare 64-hex digest and return the prefixed form."""
    v = value.strip().lower()
    digest = v[len(_HASH_PREFIX) :] if v.startswith(_HASH_PREFIX) else v
    if len(digest) != 64 or any(c not in "0123456789abcdef" for c in digest):
        raise ValueError(f"not a valid sha256 digest: {value!r}")
    return f"{_HASH_PREFIX}{digest}"
