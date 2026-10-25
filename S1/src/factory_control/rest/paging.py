"""Opaque cursors and the shared page clamp (S5IF-G04 【裁决】).

The envelope is one shape for every list — ``{items, limit, total?, offset?,
nextCursor?}`` — but which paging knob goes on the wire depends on the store behind
it.  Ordinary lists keep ``offset``.  Audit and artifact lists answer with a
``nextCursor`` instead, because those two are the million-row lists (NFR-CAP-001) and
an offset there means "count and skip", which degrades linearly and shifts rows when
something is inserted between two pages.

The cursor is deliberately opaque: it encodes where S1 stopped, and a client passing
anything else back is a 422 rather than a guess.  S5 must not parse it — that is the
whole reason it exists.
"""

from __future__ import annotations

import base64
import binascii

from factory_control.errors import ValidationFailedError

DEFAULT_PAGE_SIZE = 50
MAX_PAGE_SIZE = 200

_PREFIX = "s1c."


def clamp_limit(limit: int | None) -> int:
    """Page size, bounded: a list endpoint never streams the whole table."""
    if limit is None:
        return DEFAULT_PAGE_SIZE
    return max(1, min(int(limit), MAX_PAGE_SIZE))


def clamp_offset(offset: int | None) -> int:
    return max(0, int(offset or 0))


def encode_cursor(offset: int) -> str:
    return _PREFIX + base64.urlsafe_b64encode(str(int(offset)).encode("utf-8")).decode("ascii")


def decode_cursor(cursor: str | None) -> int:
    """The row count to skip.  ``None`` (or an empty value) is the first page."""
    if not cursor:
        return 0
    if not cursor.startswith(_PREFIX):
        raise _bad_cursor(cursor)
    try:
        raw = base64.urlsafe_b64decode(cursor[len(_PREFIX):].encode("ascii")).decode("ascii")
    except (binascii.Error, UnicodeDecodeError, ValueError) as error:
        raise _bad_cursor(cursor) from error
    if not raw.lstrip("-").isdigit():
        raise _bad_cursor(cursor)
    return max(0, int(raw))


def _bad_cursor(cursor: str) -> ValidationFailedError:
    return ValidationFailedError(
        "the paging cursor is not one this API issued",
        details={"fields": [{"path": "cursor", "message": "not a cursor this API issued"}], "cursor": cursor[:32]},
    )


def next_cursor(*, offset: int, returned: int, total: int | None) -> str | None:
    """The cursor for the page after this one, or ``null`` on the last page.

    ``total`` is ``None`` when the store could not count cheaply; then a full page is
    treated as "there is probably more", which is the only honest answer left.
    """
    consumed = offset + returned
    if total is not None:
        if consumed >= total:
            return None
    elif returned == 0:
        return None
    return encode_cursor(consumed)


SORT_DIRECTIONS = ("ASC", "DESC")


def sort_direction(value: str | None) -> str:
    """``sortDir`` — UPPER_SNAKE like every other enum value on this wire (S5IF-G03).

    The check is case-insensitive on the way in: a client that sends ``asc`` means one
    thing, and refusing it would only make the convention harder to adopt.
    """
    if value is None or value == "":
        return "DESC"
    upper = str(value).upper()
    if upper not in SORT_DIRECTIONS:
        raise ValidationFailedError(
            "sortDir must be ASC or DESC",
            details={
                "fields": [{"path": "sortDir", "message": "must be ASC or DESC"}],
                "value": value,
                "allowed": list(SORT_DIRECTIONS),
            },
        )
    return upper


def sortable(value: str | None, allowed: dict[str, str]) -> str:
    """``sortBy``, checked against the names this list can actually order by.

    ``allowed`` maps the wire name to the store's own column name, so a client can
    never reach a column S1 did not publish — and never gets a 500 for a typo.
    """
    if value is None or value == "":
        return next(iter(allowed))
    if value not in allowed:
        raise ValidationFailedError(
            f"this list cannot be sorted by {value}",
            details={
                "fields": [{"path": "sortBy", "message": f"this list cannot be sorted by {value}"}],
                "value": value,
                "allowed": sorted(allowed),
            },
        )
    return value


__all__ = [
    "DEFAULT_PAGE_SIZE",
    "MAX_PAGE_SIZE",
    "SORT_DIRECTIONS",
    "clamp_limit",
    "clamp_offset",
    "decode_cursor",
    "encode_cursor",
    "next_cursor",
    "sortable",
    "sort_direction",
]
