"""The S2 capabilities S3 consumes, as a protocol (SRS 8).

``factory_blueprint.api`` is the real implementation, but S3 asks for its
collaborator through :func:`factory_intelligence.deps.get_blueprint` instead of
importing it in each service — exactly how S2 treats S4 (``factory_blueprint/s4/``).
That is what lets a unit test of context construction run without a database, and it
is what makes the dependency list in SRS 8 auditable as code: a method on this
protocol is a dependency, and there is no other way for S3 to reach S2.

Two shapes are deliberate:

* reads that S2 itself declares transactional (``get_decisions``,
  ``get_project_semantics``, ``get_current_version``) are **self-contained here** — the
  adapter opens its own read transaction, so S3 can call them from the gap of an
  external-I/O function without holding a unit of work open across a model call;
* :meth:`submit_patch` takes the caller's ``session``.  AI-RT-013 and 00 doc 6.2
  require the patch, the ``AGENT_OUTPUT`` artifact, the run row and its audit to
  commit together, which is only possible if S3 hands S2 the transaction it is in.
"""

from __future__ import annotations

from typing import Any, Protocol, runtime_checkable

from factory_blueprint.api import (
    CurrentVersion,
    DecisionInfo,
    DraftInfo,
    Patch,
    PatchContext,
    PatchOutcome,
    ProjectSemantics,
    SelectedEntries,
    VersionInfo,
)
from sqlalchemy.orm import Session


@runtime_checkable
class BlueprintPort(Protocol):
    """S2 as S3 needs it (SRS 8, second row of the dependency table)."""

    def section_schema(self, section: str, schema_version: int | None = None) -> dict[str, Any]:
        """The self-contained JSON Schema of one Blueprint section (AI-ASM-03)."""
        ...

    def entries(self, project_id: str, selector: dict[str, Any], version: str | None = None) -> SelectedEntries:
        """Minimal retrieval by page / businessKey / decision / section (AI-RT-004)."""
        ...

    def decisions(
        self, project_id: str, decision_ids: list[str], version: str | None = None
    ) -> list[DecisionInfo]:
        ...

    def project_semantics(
        self, project_id: str, version: str | None = None
    ) -> ProjectSemantics:
        """Mode, type, languages, and whether the mapping is already frozen."""
        ...

    def current_version(self, project_id: str) -> CurrentVersion:
        ...

    def version(self, project_id: str, version: str) -> VersionInfo:
        """One specific version's index row, for ``blueprint:<version>`` references."""
        ...

    def draft(self, draft_id: str) -> DraftInfo:
        ...

    def prepare_draft_base(self, project_id: str, draft_id: str) -> str:
        """Materialize the draft's working copy (external I/O).

        S1 never calls this and has no port method for it, so an agent run that
        reached :meth:`submit_patch` first would fail with
        ``BP_DRAFT_NOT_MATERIALIZED``.
        """
        ...

    def submit_patch(
        self,
        project_id: str,
        draft_id: str,
        patch: Patch,
        context: PatchContext,
        *,
        idempotency_key: str,
        session: Session,
    ) -> PatchOutcome:
        ...

    def replenish_uid_pool(
        self,
        project_id: str,
        environment_key: str,
        *,
        batch_size: int | None = None,
        low_water_mark: int | None = None,
    ) -> int:
        """AI-RT-013: called when S2 answers ``UID_POOL_EXHAUSTED``."""
        ...


__all__ = ["BlueprintPort"]
