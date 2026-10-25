"""S3 start-up: ``initialize()`` and ``MIGRATION_CONFIG`` (00 doc 6.10).

Registers what S0 refuses to accept unregistered: the artifact types S3 owns, the
sixteen audit events of SRS appendix A, S3's error codes and its ID prefixes.  The
prompt sources (AI-REG-002) are loaded by
:func:`factory_intelligence.prompts.registry.reload_sources`, called from here, so a
process cannot serve a model call against a prompt that was never registered.
"""

from __future__ import annotations

import os
from pathlib import Path

from factory_foundation.api import (
    MigrationConfig,
    current_session,
    has_active_uow,
    register_artifact_types,
    register_audit_event_types,
    register_error_codes,
    register_id_prefixes,
    unit_of_work,
)
from sqlalchemy.orm import Session

from factory_intelligence.registries import (
    AI_ARTIFACT_MAX_SIZES,
    AI_ARTIFACT_TYPES,
    AI_AUDIT_EVENT_TYPES,
    AI_ERROR_CODES,
    AI_ID_PREFIXES,
)

AI_MIGRATION_SCRIPT_LOCATION = str(Path(__file__).resolve().parent / "migrations")

#: Passed to S0's runner, which orders schemas ``core → dx → bp → ai → cp``.
MIGRATION_CONFIG = MigrationConfig(
    schema="ai",
    script_location=AI_MIGRATION_SCRIPT_LOCATION,
    version_table="ai_alembic_version",
)


def _register_all(session: Session) -> None:
    register_artifact_types(
        AI_ARTIFACT_TYPES, max_sizes=AI_ARTIFACT_MAX_SIZES, session=session
    )
    register_audit_event_types(AI_AUDIT_EVENT_TYPES, session=session)
    register_error_codes(AI_ERROR_CODES)
    register_id_prefixes(AI_ID_PREFIXES)
    session.flush()


def _install_default_provider_factory() -> None:
    """Point ``deps`` at the OpenAI-compatible adapter unless something already did.

    AI-LLM-003 makes that adapter the default, and a deployment that installed the mock
    or a replay provider first keeps its choice: start-up must not overwrite a decision,
    only supply the missing one.
    """
    from factory_intelligence import deps
    from factory_intelligence.llm.openai_compat import provider_for

    if deps._provider_factory is None and deps._fixed_provider is None:  # noqa: SLF001
        deps.set_llm_provider_factory(provider_for)


#: The one runtime knob S3 reads from the environment, and why it deserves the exception: it is
#: the ceiling that decides whether a slow-but-working vendor is usable at all, and the right
#: value belongs to the deployment's model list, not to a code release.
TIMEOUT_ENV = "FACTORY_LLM_MODEL_TIMEOUT_SECONDS"
TIMEOUT_RANGE_SECONDS = (1.0, 900.0)


def timeout_from_environment() -> float | None:
    """The per-call budget the deployment asked for, or ``None`` when it asked for none.

    Refuses rather than ignores: a value that cannot be parsed or that is outside the sane band
    would otherwise leave the process running with the packaged default while whoever set it
    believes they raised it — the same failure as a typo in any other setting, and cheaper to
    surface at start-up than to discover in a timed-out parse an hour later.
    """
    raw = (os.getenv(TIMEOUT_ENV) or "").strip()
    if not raw:
        return None
    try:
        seconds = float(raw)
    except ValueError as exc:
        raise ValueError(f"{TIMEOUT_ENV}={raw!r} is not a number of seconds") from exc
    low, high = TIMEOUT_RANGE_SECONDS
    if not low <= seconds <= high:
        raise ValueError(f"{TIMEOUT_ENV}={seconds} must be between {low} and {high} seconds")
    return seconds


def initialize(*, session: Session | None = None, load_prompts: bool = True) -> None:
    """Register S3's catalog entries (FD-ASM-01).

    ``session`` lets S1 enlist this in its own transaction; with none, a short unit of
    work is opened and committed.  S1 calls it argument-less at process start.
    """
    from factory_intelligence.config import configure

    configured = timeout_from_environment()
    if configured is not None:
        configure(model_request_timeout_seconds=configured)
    if session is not None or has_active_uow():
        _register_all(session if session is not None else current_session())
    else:
        with unit_of_work() as sess:
            _register_all(sess)
    _install_default_provider_factory()
    if load_prompts:
        # Registration is a write, so it needs its own transaction rather than the one
        # that may still be open above.
        from factory_intelligence.prompts.registry import reload_sources

        reload_sources(session=session)


__all__ = [
    "initialize",
    "timeout_from_environment",
    "TIMEOUT_ENV",
    "MIGRATION_CONFIG",
    "AI_MIGRATION_SCRIPT_LOCATION",
]
