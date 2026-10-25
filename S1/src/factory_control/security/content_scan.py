"""CP-API-009's content scan for uploaded inputs.

What this is: a bounded, deterministic structural check of the bytes before S1 stages them.
What this is not: an antivirus engine. It recognises no signature database and cannot prove a
document benign; the SRS line it implements ("恶意内容扫描") is delivered here as the checks an
input control plane can actually run and reason about, and the limit is stated in the SRS rather
than implied by a name.

Each check answers one question the downstream stages cannot ask for themselves:

``EXECUTABLE_CONTENT``   an upload that declares itself a spreadsheet but starts with an
                         executable or script magic number is refused outright -- nothing down
                         the line opens one, so accepting it would only store a landmine.
``ARCHIVE_*``            ``.xlsx``/``.docx`` are ZIP containers, and a container is where a
                         path-traversal member, a symlink entry or a decompression bomb hides.
                         The parser S2 uses would happily walk member names.
"""

from __future__ import annotations

import io
import posixpath
import stat
import zipfile

#: Compressed-to-uncompressed ratio beyond which a container is treated as a bomb. A real
#: workbook sits far below this; the number only has to be above what producers emit.
MAX_COMPRESSION_RATIO = 200
#: Member count cap. Spreadsheets carry a few dozen parts.
MAX_ARCHIVE_ENTRIES = 2000
#: Bytes looked at for a magic-number match.
_MAGIC_WINDOW = 8

_EXECUTABLE_MAGIC: tuple[bytes, ...] = (
    b"MZ",              # PE/DOS
    b"\x7fELF",         # ELF
    b"#!",              # script interpreter line
    b"\xca\xfe\xba\xbe",  # Mach-O fat
    b"\xfe\xed\xfa",    # Mach-O 32/64
)

__all__ = ["MAX_ARCHIVE_ENTRIES", "MAX_COMPRESSION_RATIO", "scan_input_content"]


def scan_input_content(filename: str, media_type: str | None, data: bytes) -> str | None:
    """Return a violation code, or ``None`` when the bytes pass every check that applies.

    The code is a stable string so a console can name the rule that fired instead of paraphrasing
    a sentence; the HTTP answer stays 422 ``UPLOAD_REJECTED`` either way.
    """
    del filename  # the declared type is what is checked; the name is already policy-checked elsewhere
    if not data:
        return None

    head = data[:_MAGIC_WINDOW]
    if any(head.startswith(magic) for magic in _EXECUTABLE_MAGIC):
        return "EXECUTABLE_CONTENT"

    if not zipfile.is_zipfile(io.BytesIO(data)):
        return None

    try:
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            infos = archive.infolist()
    except (zipfile.BadZipFile, OSError):
        # Unreadable but zip-magic: the type policy already passed it, so let the parser be the
        # one to say what is wrong rather than inventing a scan failure here.
        return None

    if len(infos) > MAX_ARCHIVE_ENTRIES:
        return "ARCHIVE_ENTRY_COUNT"

    for info in infos:
        name = info.filename
        normalised = posixpath.normpath(name.replace("\\", "/"))
        if normalised.startswith("/") or normalised.startswith("..") or "/../" in f"/{normalised}":
            return "ARCHIVE_PATH_TRAVERSAL"
        if stat.S_ISLNK(info.external_attr >> 16):
            return "ARCHIVE_SYMLINK"

    uncompressed = sum(int(info.file_size or 0) for info in infos)
    if uncompressed > MAX_COMPRESSION_RATIO * max(1, len(data)):
        return "ARCHIVE_RATIO"

    return None
