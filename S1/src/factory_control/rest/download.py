"""File names for a downloaded artifact (S5IF-FLD09 【裁决】).

S1 defines the rules, because the name is the only thing a user has to recognise the
file by later.  The three cases are the ones the screens actually need: an input the
user uploaded and wants back, an export taken before deleting a project, and everything
else — which has no human-supplied name at all and gets a descriptive one.

``Content-Disposition`` is a header, so the name has to survive two hazards: a project
called `` clinics";x `` and a name containing a CR/LF pair that would inject a second
header.  Both are handled by quoting per RFC 6266 and by keeping an ASCII fallback.
"""

from __future__ import annotations

import re
import urllib.parse
from datetime import UTC, datetime
from typing import Any

#: Media types whose name a user already knows, so the extension can be inferred
#: instead of repeating the artifact's own MIME suffix.
_EXTENSIONS: dict[str, str] = {
    "application/json": "json",
    "application/zip": "zip",
    "application/octet-stream": "bin",
    "text/csv": "csv",
    "text/plain": "txt",
    "text/markdown": "md",
    "text/html": "html",
    "application/pdf": "pdf",
    "application/yaml": "yaml",
    "text/yaml": "yaml",
}

#: Anything outside this set becomes an underscore in the ASCII fallback, spaces included:
#: a quoted ``filename=`` may legally hold a space, but a downloaded file with one in it is
#: what the fallback exists to avoid.
_SAFE = re.compile(r"[^A-Za-z0-9._-]+")


def download_filename(artifact: Any, *, project_name: str | None = None) -> str:
    """The three rules of S5IF-FLD09, in the order the document gives them."""
    artifact_type = str(getattr(artifact, "type", "") or "ARTIFACT")
    artifact_id = str(getattr(artifact, "id", "") or "unknown")

    if artifact_type == "BUSINESS_INPUT":
        original = str((getattr(artifact, "build_record", None) or {}).get("filename") or "").strip()
        if original:
            return _safe_name(original)

    if artifact_type == "PROJECT_EXPORT":
        stem = _safe_name(project_name or "project")
        day = datetime.now(UTC).strftime("%Y%m%d")
        return f"{stem}-export-{day}.zip"

    return f"{artifact_type}-{artifact_id}.{_extension(artifact)}"


def package_file_filename(package_artifact_id: str, file_path: str) -> str:
    """Rule three of S5IF-FLD09, for one file inside a development package (CP-UI-021).

    The package artifact has no user-known name either, so the name is type + artifact id,
    and the extension of the *requested path* is what lets the saving application pick
    the right handler — a README and an openapi.yaml must not both land as ``.bin``.
    """
    leaf = file_path.rsplit("/", 1)[-1]
    suffix = _SAFE.sub("", leaf.rsplit(".", 1)[-1].lower()) if "." in leaf else ""
    return f"FRONTEND_PACKAGE-{package_artifact_id}.{suffix or 'bin'}"


def content_disposition(filename: str) -> str:
    """``attachment`` with both spellings of the name, as RFC 6266 wants."""
    fallback = _SAFE.sub("_", filename).replace('"', "'")[:120] or "download"
    extended = urllib.parse.quote(filename, safe="")
    return f"attachment; filename=\"{fallback}\"; filename*=UTF-8''{extended}"


def _safe_name(value: str) -> str:
    """Strip control characters — a CR or LF in a name would end the header early."""
    cleaned = "".join(char for char in value if char.isprintable() and char not in "\r\n\x00")
    return cleaned.strip() or "download"


def _extension(artifact: Any) -> str:
    media_type = str(getattr(artifact, "media_type", "") or "")
    if media_type in _EXTENSIONS:
        return _EXTENSIONS[media_type]
    suffix = media_type.rsplit("/", 1)[-1]
    cleaned = _SAFE.sub("", suffix).lower()
    return cleaned or "bin"


__all__ = ["content_disposition", "download_filename", "package_file_filename"]
