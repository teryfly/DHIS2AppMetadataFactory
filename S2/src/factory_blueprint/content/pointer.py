"""JSON Pointer (RFC 6901) helpers bound to the Blueprint layout."""

from __future__ import annotations

from dataclasses import dataclass

from factory_blueprint.content.layout import ENTRY_CONTAINERS, EntryContainer, unescape
from factory_blueprint.enums import Section

ROOT_POINTER = ""


class PointerError(ValueError):
    """A pointer that is not syntactically usable (RFC 6901 violation)."""


def parse_pointer(pointer: str) -> tuple[str, ...]:
    """Split a pointer into unescaped reference tokens."""
    if pointer == ROOT_POINTER:
        return ()
    if not pointer.startswith("/"):
        raise PointerError(f"pointer must be empty or start with '/': {pointer!r}")
    return tuple(unescape(token) for token in pointer[1:].split("/"))


def pointer_tokens_to_path(tokens: tuple[str, ...]) -> str:
    if not tokens:
        return ROOT_POINTER
    return "/" + "/".join(escape_token(token) for token in tokens)


def escape_token(token: str) -> str:
    return token.replace("~", "~0").replace("/", "~1")


@dataclass(frozen=True)
class PointerLocation:
    """Where a pointer lands inside the Blueprint document."""

    section: Section
    #: the keyed container the pointer is inside, if any
    container: EntryContainer | None
    #: key of the entry the pointer is inside, if any
    entry_key: str | None
    #: remaining tokens below the entry (field path)
    remainder: tuple[str, ...]

    @property
    def top_level_section(self) -> bool:
        return self.container is None


def locate(pointer: str) -> PointerLocation:
    """Resolve a pointer to its section and, when applicable, its entry."""
    tokens = parse_pointer(pointer)
    if not tokens:
        raise PointerError("the document root cannot be addressed by a patch operation")
    try:
        section = Section(tokens[0])
    except ValueError as exc:
        raise PointerError(f"unknown Blueprint section: {tokens[0]!r}") from exc

    for container in ENTRY_CONTAINERS:
        if container.section is not section:
            continue
        prefix = tuple(container.path.split("/")[1:])
        if tokens[: len(prefix)] == prefix and len(tokens) > len(prefix):
            return PointerLocation(
                section=section,
                container=container,
                entry_key=tokens[len(prefix)],
                remainder=tokens[len(prefix) + 1 :],
            )
    return PointerLocation(section=section, container=None, entry_key=None, remainder=tokens[1:])


def sections_of(pointers: list[str]) -> list[Section]:
    """Distinct sections touched by these pointers, in declaration order."""
    seen: list[Section] = []
    for pointer in pointers:
        section = locate(pointer).section
        if section not in seen:
            seen.append(section)
    return seen
