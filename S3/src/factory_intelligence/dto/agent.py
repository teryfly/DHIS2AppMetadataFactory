"""The agent output envelope — AI-RT-010.

This is the *model's* shape, not S1's: top level ``summary``, ``decisions``,
``patch{baseVersion, operations}``, ``questions[{id, text, severity, affects,
suggestions[...]}]``, ``confidence``, in the SRS's own camelCase.  It is a separate model
from :class:`factory_intelligence.dto.result.AgentRunResult` on purpose — the first is
what an untrusted process is asked to produce and must be validated, the second is what
the orchestrator is promised and is built by S3 after that validation.  Letting one
inherit from the other would quietly weaken whichever contract the second was written
against.
"""

from __future__ import annotations

from typing import Any

from pydantic import AliasChoices, ConfigDict, Field, field_validator, model_validator

from factory_intelligence.dto.common import ApiModel, Question, Suggestion


class PatchEnvelope(ApiModel):
    """``patch``: a base version plus RFC 6902 operations (AI-RT-010).

    The operations are kept as raw mappings here.  Interpreting them is S2's job
    (``factory_blueprint/patch/rfc6902.py``); S3 checks their *paths* against the
    agent's allowed sections and hands the rest to ``submit_patch``.  Reparsing them
    into S3 types would create a second, drifting idea of what a patch means.
    """

    model_config = ConfigDict(populate_by_name=True, extra="ignore")

    base_version: str = Field(
        validation_alias=AliasChoices("baseVersion", "base_version"),
        serialization_alias="baseVersion",
    )
    operations: list[dict[str, Any]] = Field(default_factory=list)
    #: advisory intent, never authoritative (mirrors S2's ``declared_impact``)
    declared_impact: dict[str, Any] | None = Field(
        default=None, validation_alias=AliasChoices("declaredImpact", "declared_impact")
    )

    @model_validator(mode="after")
    def _operations_are_addressed(self) -> PatchEnvelope:
        for index, operation in enumerate(self.operations):
            if "op" not in operation or "path" not in operation:
                raise ValueError(
                    f"patch.operations[{index}] needs 'op' and 'path' "
                    f"(got {sorted(operation)})"
                )
        return self


class ModelQuestion(ApiModel):
    """A question as the model writes it, keyed by its own local ``id``."""

    model_config = ConfigDict(populate_by_name=True, extra="ignore")

    id: str = Field(min_length=1)
    text: str = Field(min_length=1)
    severity: str = "MEDIUM"
    #: ``SINGLE`` (radio) or ``MULTIPLE`` (checkbox).  Anything else is treated as SINGLE,
    #: because a UI that cannot tell the mode must still render an answerable question.
    answer_mode: str = Field(
        default="SINGLE", validation_alias=AliasChoices("answer_mode", "answerMode")
    )
    affects: list[str] = Field(default_factory=list)
    suggestions: list[Suggestion] = Field(default_factory=list)
    blocking: bool | None = None
    details: dict[str, Any] = Field(default_factory=dict)

    @field_validator("answer_mode")
    @classmethod
    def _known_answer_mode(cls, value: str) -> str:
        normalised = str(value or "").strip().upper()
        return normalised if normalised in {"SINGLE", "MULTIPLE"} else "SINGLE"

    def to_canonical(self, local_id: str) -> Question:
        """Rewrite into the id S3 assigned (see ``runtime/questions.py``).

        The model's own ``id`` is remembered inside the question so a later answer that
        quotes it can still be traced back.
        """
        return Question(
            local_id=local_id,
            text=self.text,
            severity=self.severity,
            answer_mode=self.answer_mode,
            affects=list(self.affects),
            suggestions=[
                suggestion.model_dump(mode="json", by_alias=False)
                for suggestion in self.suggestions
            ],
            #: HIGH is blocking whatever the model set (AI-RT-015); the recomputation
            #: happens in ``blocking``, so store the claim as given.
            blocking=bool(self.blocking),
            details={**self.details, "modelId": self.id},
        )


class AgentOutput(ApiModel):
    """A validated agent answer (AI-RT-010)."""

    model_config = ConfigDict(populate_by_name=True, extra="ignore")

    summary: str = Field(min_length=1)
    decisions: list[dict[str, Any]] = Field(default_factory=list)
    patch: PatchEnvelope | None = None
    questions: list[ModelQuestion] = Field(default_factory=list)
    confidence: float | None = Field(
        default=None, validation_alias=AliasChoices("confidence", "conf")
    )

    @model_validator(mode="after")
    def _question_ids_unique(self) -> AgentOutput:
        seen: set[str] = set()
        duplicates: list[str] = []
        for question in self.questions:
            if question.id in seen:
                duplicates.append(question.id)
            seen.add(question.id)
        if duplicates:
            # S1 dedupes on (agentRunId, sourceLocalId) and *skips* a repeat, so two
            # identically-keyed questions would silently lose one of them.
            raise ValueError(
                "questions must have distinct ids; duplicated: " + ", ".join(sorted(set(duplicates)))
            )
        return self

    @property
    def has_patch(self) -> bool:
        return self.patch is not None and bool(self.patch.operations)

    @property
    def decision_ids(self) -> list[str]:
        """Ids S2 must be able to resolve (``get_decisions``); empty entries are skipped."""
        out: list[str] = []
        for decision in self.decisions:
            value = decision.get("decisionId") or decision.get("decision_id") or decision.get("id")
            if isinstance(value, str) and value:
                out.append(value)
        return out


__all__ = ["AgentOutput", "PatchEnvelope", "ModelQuestion"]
