"""Prompt Registry: loading and reading the versioned prompt sources (AI-REG-001/002).

Prompts live as files under ``prompts/source/<agent_type>/<name>.v<N>.md`` with a TOML
front matter block, and ``initialize()`` registers them into ``ai.prompt_version``.
Three properties of that layout are the requirement, not a preference:

* the body is prose and diffs as prose, while its *declaration* (schemas, DHIS2 range,
  prohibitions) is structured data a program can enforce — AI-REG-001 demands both halves
  per prompt, and a Python string constant would put the second half back in code;
* TOML front matter needs no new dependency (``tomllib`` is stdlib since 3.11) and is
  not silently reinterpreted the way an ad-hoc header parser would;
* ``prohibitions`` is machine-readable because AI-RT-012 enforces it as a constraint
  check.  A prompt that says "never emit a UID" and a check that rejects one must be the
  same declared fact, or the two drift apart and only the check remembers.
"""

from __future__ import annotations

import tomllib
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from factory_foundation.api import (
    NotFoundError,
    content_hash,
    current_session,
    has_active_uow,
    new_id,
    new_session,
    unit_of_work,
)
from sqlalchemy import select
from sqlalchemy.orm import Session

from factory_intelligence.enums import AgentType
from factory_intelligence.errors import PromptNotFound
from factory_intelligence.models.registry import PromptVersionRow

PROMPT_SOURCE_ROOT = Path(__file__).resolve().parent / "source"
_FRONT_MATTER_DELIMITER = "+++"


@dataclass(frozen=True)
class PromptDeclaration:
    """One parsed source file."""

    name: str
    version: int
    agent_type: str
    system_rules: str
    body: str
    input_schema_ref: str | None = None
    output_schema_ref: str | None = None
    blueprint_schema_range: str = ">=1"
    dhis2_version_range: str = "*"
    applicable_stages: list[str] = field(default_factory=list)
    prohibitions: list[dict[str, Any]] = field(default_factory=list)
    source_path: str = ""
    source_hash: str = ""

    @property
    def qualified_name(self) -> str:
        """``mapping.decide.v3`` — the spelling SRS AI-REG-001 uses."""
        return f"{self.name}.v{self.version}"


def parse_source(text: str, *, path: str = "") -> PromptDeclaration:
    """Split front matter from body and validate the declaration."""
    stripped = text.lstrip("\ufeff")
    if not stripped.startswith(_FRONT_MATTER_DELIMITER):
        raise ValueError(f"prompt source {path or '<string>'} has no TOML front matter")
    rest = stripped[len(_FRONT_MATTER_DELIMITER) :]
    header, sep, body = rest.partition("\n" + _FRONT_MATTER_DELIMITER)
    if not sep:
        raise ValueError(f"prompt source {path or '<string>'} front matter is not closed")
    declared = tomllib.loads(header.lstrip("\n"))

    name = _required_str(declared, "name", path)
    raw_version = declared.get("version")
    # bool is an int subclass, and TOML reads ``version = true`` happily; without this the
    # file would register onto v1's row under the name ``…vTrue`` (AI-REG-001)
    if isinstance(raw_version, bool) or not isinstance(raw_version, int) or raw_version < 1:
        raise ValueError(f"prompt source {path or '<string>'}: version must be an integer >= 1")
    agent_type = _required_str(declared, "agent_type", path)
    # Fail loudly: an unknown agent has no spec, no checks and no model list to run with.
    AgentType(agent_type)

    stages = _list_of(declared, "stages", path)
    prohibitions_raw = declared.get("prohibitions") or []
    if not isinstance(prohibitions_raw, list) or any(
        not isinstance(item, dict) for item in prohibitions_raw
    ):
        raise ValueError(
            f"prompt source {path or '<string>'}: prohibitions must be [[prohibitions]] tables"
        )

    return PromptDeclaration(
        name=name,
        version=raw_version,
        agent_type=agent_type,
        system_rules=str(declared.get("system_rules") or ""),
        body=body.lstrip("\n").rstrip(),
        input_schema_ref=_optional_str(declared, "input_schema_ref", path),
        output_schema_ref=_optional_str(declared, "output_schema_ref", path),
        blueprint_schema_range=str(declared.get("blueprint_schema_range") or ">=1"),
        dhis2_version_range=str(declared.get("dhis2_version_range") or "*"),
        applicable_stages=[str(item) for item in stages],
        prohibitions=[dict(item) for item in prohibitions_raw],
        source_path=path,
        source_hash=content_hash(text.encode("utf-8")),
    )


def _required_str(declared: dict[str, Any], key: str, path: str) -> str:
    value = declared.get(key)
    if not isinstance(value, str) or not value:
        raise ValueError(f"prompt source {path or '<string>'}: {key!r} is required")
    return value


def _optional_str(declared: dict[str, Any], key: str, path: str) -> str | None:
    value = declared.get(key)
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValueError(f"prompt source {path or '<string>'}: {key!r} must be a string")
    return value


def _list_of(declared: dict[str, Any], key: str, path: str) -> list[Any]:
    value = declared.get(key) or []
    if not isinstance(value, list):
        raise ValueError(f"prompt source {path or '<string>'}: {key!r} must be a list")
    return value


def discover_sources(root: Path | None = None) -> list[PromptDeclaration]:
    """Every packaged prompt source, in a stable order (AI-REG-002)."""
    base = root or PROMPT_SOURCE_ROOT
    if not base.is_dir():
        return []
    return [
        parse_source(path.read_text(encoding="utf-8"), path=str(path.relative_to(base)))
        for path in sorted(base.rglob("*.md"))
    ]


@contextmanager
def _reader(session: Session | None = None) -> Iterator[Session]:
    """Read through the caller's transaction, or a throwaway one.

    ``factory_foundation.api`` exports ``current_session`` and ``unit_of_work`` but not
    its internal ``resolve_session``, and D-13 says S3 imports only the public module —
    so the three-case resolution is written here rather than borrowed.
    """
    if session is not None:
        yield session
        return
    if has_active_uow():
        yield current_session()
        return
    owned = new_session()
    try:
        yield owned
    finally:
        owned.close()


def reload_sources(
    *, root: Path | None = None, session: Session | None = None
) -> list[PromptVersionRow]:
    """Register every source file; existing rows are refreshed in place.

    Idempotent by (name, version): re-running start-up updates the body and hash of a
    version that changed on disk, which is what makes a wheel upgrade observable rather
    than silent.  No activation is touched here — a new version never takes effect
    without AI-REG-003's two gates.
    """
    declarations = discover_sources(root)
    if session is not None or has_active_uow():
        # The caller commits; registering must not end its transaction early.
        with _reader(session) as sess:
            return _upsert_all(declarations, sess)
    with unit_of_work() as sess:
        return _upsert_all(declarations, sess)


def _upsert_all(
    declarations: list[PromptDeclaration], session: Session
) -> list[PromptVersionRow]:
    rows: list[PromptVersionRow] = []
    for declaration in declarations:
        row = session.execute(
            select(PromptVersionRow).where(
                PromptVersionRow.name == declaration.name,
                PromptVersionRow.version == declaration.version,
            )
        ).scalars().first()
        if row is None:
            row = PromptVersionRow(
                id=new_id("apvr"), name=declaration.name, version=declaration.version
            )
            session.add(row)
        row.agent_type = declaration.agent_type
        row.body = declaration.body
        row.system_rules = declaration.system_rules or None
        row.input_schema_ref = declaration.input_schema_ref
        row.output_schema_ref = declaration.output_schema_ref
        row.blueprint_schema_range = declaration.blueprint_schema_range
        row.dhis2_version_range = declaration.dhis2_version_range
        row.prohibitions = declaration.prohibitions
        row.applicable_stages = declaration.applicable_stages
        row.source_hash = declaration.source_hash
        rows.append(row)
    session.flush()
    return rows


def get_prompt(name: str, version: int, *, session: Session | None = None) -> PromptVersionRow:
    """Read one registered prompt version."""
    with _reader(session) as sess:
        row = sess.execute(
            select(PromptVersionRow).where(
                PromptVersionRow.name == name, PromptVersionRow.version == version
            )
        ).scalars().first()
        if row is None:
            raise PromptNotFound(
                f"prompt {name}.v{version} is not registered",
                details={"name": name, "version": version},
            )
        return row


def get_prompt_by_id(
    prompt_version_id: str, *, session: Session | None = None
) -> PromptVersionRow:
    """Read one registered prompt version by its row id.

    An activation records only the id, while a caller that wants to *show* the activated
    prompt needs its name and version too (AI-GOV-008, S5IF-GOV10) — so the id is a first
    class way to read a version rather than something each caller resolves by scanning.
    """
    with _reader(session) as sess:
        row = sess.get(PromptVersionRow, prompt_version_id)
        if row is None:
            raise PromptNotFound(
                f"prompt version {prompt_version_id} is not registered",
                details={"promptVersionId": prompt_version_id},
            )
        return row


def split_qualified_prompt(qualified: str) -> tuple[str, int]:
    """``"mapping.decide.v3"`` → ``("mapping.decide", 3)`` (AI-REG-001's own spelling).

    The registry keys a version on two columns while SRS 7 hands ``run_evaluation`` one
    string.  A prompt name may contain dots, so only a trailing ``.v<digits>`` is a version.
    """
    name, _, digits = qualified.rpartition(".v")
    if not name or not digits.isdigit():
        raise PromptNotFound(
            f"{qualified!r} is not a qualified prompt version; the form is <name>.v<int>",
            details={"qualified": qualified},
        )
    return name, int(digits)


def list_prompts(
    agent_type: AgentType | str | None = None, *, session: Session | None = None
) -> list[PromptVersionRow]:
    """Registered versions, newest first — including the ones nobody activated."""
    wanted = AgentType(agent_type).value if agent_type else None

    def _read(sess: Session) -> list[PromptVersionRow]:
        stmt = select(PromptVersionRow).order_by(
            PromptVersionRow.name, PromptVersionRow.version.desc()
        )
        if wanted is not None:
            stmt = stmt.where(PromptVersionRow.agent_type == wanted)
        return list(sess.execute(stmt).scalars().all())

    with _reader(session) as sess:
        return _read(sess)


def prompt_body(row_id: str, *, session: Session | None = None) -> str:
    """The body text of one version, for ``governance("prompt.get")``."""
    with _reader(session) as sess:
        row = sess.get(PromptVersionRow, row_id)
        if row is None:
            raise NotFoundError(f"prompt version {row_id} not found")
        return row.body


__all__ = [
    "PROMPT_SOURCE_ROOT",
    "PromptDeclaration",
    "parse_source",
    "discover_sources",
    "reload_sources",
    "get_prompt",
    "get_prompt_by_id",
    "split_qualified_prompt",
    "list_prompts",
    "prompt_body",
]
