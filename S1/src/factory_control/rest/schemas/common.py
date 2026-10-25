"""Response and request models for the public REST surface (S5IF-G01 / G02).

One base config decides the wire naming for the whole API: snake_case in Python,
camelCase on the wire, via the same ``alias_generator=to_camel`` the provider
mirrors in :mod:`factory_control.ports.dto` already use.  FastAPI serialises
``response_model`` output with ``by_alias=True`` by default, so declaring a model
on a route both types the document and renames that route's response — which is
what lets S5 generate a client instead of transcribing one.
"""

from __future__ import annotations

from typing import Annotated, Any

from pydantic import BaseModel, BeforeValidator, ConfigDict, Field, model_validator
from pydantic.alias_generators import to_camel

from factory_control.ports.dto import fold_provider_names


def _upper_snake(value: Any) -> Any:
    """Publish a provider enum value as UPPER_SNAKE without touching the provider's own."""
    return value.upper() if isinstance(value, str) else value


#: A value whose canonical form is UPPER_SNAKE on this wire even when the subsystem that
#: produced it spells it another way — S2's ``ChangeLevel`` says ``Major`` (S5IF-G03).
#: S1 keeps the provider's spelling internally, because it compares against it.
UpperSnakeValue = Annotated[str, BeforeValidator(_upper_snake)]


class CamelModel(BaseModel):
    """Base for every REST model in S1."""

    model_config = ConfigDict(
        alias_generator=to_camel, populate_by_name=True, extra="ignore", from_attributes=True
    )


class IssueView(CamelModel):
    """One finding in a validation / import / compile report.

    ``level`` is the single spelling S5 sees: S2 calls it ``level`` and S3 calls it
    ``severity``, and S1 normalises to ``level`` wherever the two meet (S5IF-BP14,
    S5IF-FLD07).  ``severity`` is accepted on the way in — a report or a row written before
    the rename still says it — and never leaves, so the wire carries one name.
    """

    issue_id: str
    rule_id: str | None = None
    level: str | None = "WARNING"
    severity: str | None = Field(default=None, exclude=True)
    object_key: str | None = None
    object_uid: str | None = None
    description: str = ""
    source_stage: str | None = None
    artifact_id: str | None = None
    suggested_action: str | None = None
    business_key: str | None = None
    dhis2_type: str | None = None
    location: str | None = None
    section: str | None = None
    remediation: str | None = None
    details: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="before")
    @classmethod
    def _accept_provider_spellings(cls, data: Any) -> Any:
        """A report or a row written by a provider may still say ``message`` for the text.

        The same fold the port mirror applies (:func:`factory_control.ports.dto
        .fold_provider_names`), so the read that forwards a provider's own document answers
        the finding with its text rather than an empty string.
        """
        return fold_provider_names(data) if isinstance(data, dict) else data

    @model_validator(mode="after")
    def _one_level_spelling(self) -> IssueView:
        if not self.level:
            self.level = self.severity or "WARNING"
        self.level = str(self.level).upper()
        self.severity = None
        return self


class BoundObjectView(CamelModel):
    """A bound object and its hash — what an approval must quote back (UI-GATE-003)."""

    ref_type: str
    ref_id: str
    content_hash: str


class ListEnvelope(CamelModel):
    """The one list shape every S1 list endpoint returns (S5IF-G04 【裁决】).

    Subclasses declare ``items`` with their own element type; keeping the envelope
    non-generic means a generated client sees a named component per list rather than
    one parameterised one.

    Exactly one paging knob carries meaning per endpoint, and the other stays absent:
    ordinary lists page by ``offset`` and answer ``total``; audit, artifact and event
    lists page by the opaque ``nextCursor`` and omit ``total`` rather than filling it
    with a number the store cannot produce cheaply — a missing field must never read
    as "nothing" (S5IF-G04).  Routes that use the cursor set
    ``response_model_exclude_none``, so an unset field leaves the body instead of
    arriving as ``null``.

    ``sortBy`` / ``sortDir`` are the standard sorting parameters (CP-API-005); both
    are judged at the wire and applied by the database, never in the handler.
    """

    total: int | None = None
    limit: int | None = None
    offset: int | None = None
    next_cursor: str | None = None


class AcceptanceView(CamelModel):
    """The one answer every step-starting endpoint gives (CP-API-012, SRS 8.1).

    ``stageId`` is present only when the step belongs to a stage; ``stepRunId`` is the
    record the outcome is written to and the id the client watches from here on — a
    stage run for a stage step, the artefact an export produces, the provider's own run
    id for a forwarded step.  Progress and result arrive over SSE and on the query
    endpoint of that record, never in this body, and no field here says which engine
    picked the work up (S5IF-G06): a client that had to branch on deployment topology
    would be written twice.
    """

    stage_id: str | None = None
    step_run_id: str
    accepted: bool = True


__all__ = [
    "AcceptanceView",
    "BoundObjectView",
    "UpperSnakeValue",
    "CamelModel",
    "IssueView",
    "ListEnvelope",
]
