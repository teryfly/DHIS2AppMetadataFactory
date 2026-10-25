"""``BlueprintPort`` over ``factory_blueprint.api`` — the real implementation.

Thin on purpose: every method is a call plus the transaction wrapper S2's own
signature demands.  Any logic here would mean S3 knows something about Blueprint
state that S2 has not said (D-14).
"""

from __future__ import annotations

from typing import Any

from factory_blueprint.api import (
    CurrentVersion,
    DecisionInfo,
    DraftInfo,
    EntrySelector,
    Patch,
    PatchContext,
    PatchOutcome,
    ProjectSemantics,
    SelectedEntries,
    VersionInfo,
)
from factory_blueprint.api import (
    get_current_version as bp_get_current_version,
)
from factory_blueprint.api import (
    get_decisions as bp_get_decisions,
)
from factory_blueprint.api import (
    get_draft as bp_get_draft,
)
from factory_blueprint.api import (
    get_entries as bp_get_entries,
)
from factory_blueprint.api import (
    get_project_semantics as bp_get_project_semantics,
)
from factory_blueprint.api import (
    get_section_schema as bp_get_section_schema,
)
from factory_blueprint.api import (
    get_version as bp_get_version,
)
from factory_blueprint.api import (
    prepare_draft_base as bp_prepare_draft_base,
)
from factory_blueprint.api import (
    replenish_uid_pool as bp_replenish_uid_pool,
)
from factory_blueprint.api import (
    submit_patch as bp_submit_patch,
)
from factory_foundation.api import unit_of_work
from sqlalchemy.orm import Session


class S2Adapter:
    """Calls S2's public API.  Satisfies :class:`~factory_intelligence.s2.protocol.BlueprintPort`."""

    def section_schema(self, section: str, schema_version: int | None = None) -> dict[str, Any]:
        return bp_get_section_schema(section, schema_version)

    def entries(
        self, project_id: str, selector: dict[str, Any], version: str | None = None
    ) -> SelectedEntries:
        return bp_get_entries(project_id, EntrySelector.model_validate(selector), version)

    def decisions(
        self, project_id: str, decision_ids: list[str], version: str | None = None
    ) -> list[DecisionInfo]:
        with unit_of_work() as session:
            return bp_get_decisions(project_id, decision_ids, version, session=session)

    def project_semantics(
        self, project_id: str, version: str | None = None
    ) -> ProjectSemantics:
        with unit_of_work() as session:
            return bp_get_project_semantics(project_id, version, session=session)

    def current_version(self, project_id: str) -> CurrentVersion:
        with unit_of_work() as session:
            return bp_get_current_version(project_id, session=session)

    def version(self, project_id: str, version: str) -> VersionInfo:
        with unit_of_work() as session:
            return bp_get_version(project_id, version, session=session)

    def draft(self, draft_id: str) -> DraftInfo:
        return bp_get_draft(draft_id)

    def prepare_draft_base(self, project_id: str, draft_id: str) -> str:
        return bp_prepare_draft_base(project_id, draft_id)

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
        return bp_submit_patch(
            project_id,
            draft_id,
            patch,
            context,
            idempotency_key=idempotency_key,
            session=session,
        )

    def replenish_uid_pool(
        self,
        project_id: str,
        environment_key: str,
        *,
        batch_size: int | None = None,
        low_water_mark: int | None = None,
    ) -> int:
        kwargs: dict[str, Any] = {}
        if batch_size is not None:
            kwargs["batch_size"] = batch_size
        if low_water_mark is not None:
            kwargs["low_water_mark"] = low_water_mark
        return bp_replenish_uid_pool(project_id, environment_key, **kwargs)


__all__ = ["S2Adapter"]
