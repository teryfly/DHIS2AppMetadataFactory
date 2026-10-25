"""Requests into S3 (AI-RT-002, AI-INP-004)."""

from __future__ import annotations

from typing import Any

from pydantic import AliasChoices, Field

from factory_intelligence.dto.common import ApiModel


class AgentRunRequest(ApiModel):
    """Everything one run needs, supplied by the caller.

    S3 cannot reach into ``cp`` or ``dx`` (00 doc 4.2, and SRS 8: S3 does not call S1 or
    S4), so the workflow facts, the target environments' export policies, the design
    reference environment and the discovery artifacts all arrive as parameters.  The
    field list mirrors ``factory_control/ports/dto.py:182`` exactly — a field S3 invents
    here is silently dropped at S1's ``extra="ignore"`` boundary, which is why adding to
    this model is a cross-subsystem change and not a local one.
    """

    project_id: str
    workflow_id: str
    stage_id: str
    stage: str
    agent_type: str
    draft_id: str | None = None
    scope: list[str] = Field(default_factory=list)
    input_artifact_ids: list[str] = Field(default_factory=list)
    design_reference_environment_key: str | None = None
    #: {environmentKey: dataExportPolicy} — S1 forwards S4's dict verbatim, so this is
    #: deliberately untyped and normalised defensively (AI-POL-004).
    target_environment_policies: dict[str, Any] = Field(default_factory=dict)
    capability_profile_artifact_id: str | None = None
    discovery_result_artifact_id: str | None = None
    operator: str = "system"
    #: the interface language the human is reading (``zh-CN`` or ``en``); every
    #: human-readable value in the answer is written in it.  Deliberately not part of the
    #: digest: changing the UI language is not a different request, and a resumed turn may
    #: legitimately carry a different one.
    language: str | None = None
    idempotency_key: str

    def digest_payload(self) -> dict[str, Any]:
        """The part of the request the idempotency digest covers.

        ``target_environment_policies`` is excluded on purpose.  S1 re-reads it live from
        S4 on every activity attempt, so an operator editing an environment policy
        between a failed attempt and its retry would change the digest and produce
        ``IDEMPOTENCY_CONFLICT`` where AI-AC-05 demands a resumed run.  The caller's
        *intent* is what a repeated key means; the ambient policy is a fact about the
        world, and it is recorded — in ``ai.policy_decision`` — just not hashed (AI-RT-001).
        """
        return {
            "projectId": self.project_id,
            "workflowId": self.workflow_id,
            "stageId": self.stage_id,
            "stage": self.stage,
            "agentType": self.agent_type,
            "draftId": self.draft_id,
            "scope": list(self.scope),
            "inputArtifactIds": list(self.input_artifact_ids),
            "designReferenceEnvironmentKey": self.design_reference_environment_key,
            "capabilityProfileArtifactId": self.capability_profile_artifact_id,
            "discoveryResultArtifactId": self.discovery_result_artifact_id,
            "operator": self.operator,
        }


class ParseOptions(ApiModel):
    """Options for :func:`factory_intelligence.api.parse_input` (AI-INP-006)."""

    #: {"sourceColumn": "TargetField"} — a confirmed column mapping re-parses with this.
    #: S1 forwards the REST body's camelCase spelling untouched
    #: (``factory_control/rest/schemas/inputs.py`` ``to_options()``), and ``ApiModel`` is
    #: ``extra="ignore"``, so without the aliases below a reviewer's confirmed mapping was
    #: *dropped* and AI-INP-006's re-parse answered exactly as the unconfirmed first parse did.
    column_mapping: dict[str, Any] = Field(
        default_factory=dict,
        validation_alias=AliasChoices("column_mapping", "columnMapping"),
    )
    sheet: str | None = None
    delimiter: str | None = None
    has_header: bool = Field(
        default=True, validation_alias=AliasChoices("has_header", "hasHeader")
    )
    #: a caller may force a parser for an oddly-named file
    parser: str | None = None
    #: drawing-tool exports: the format the user exported to (AI-INP-002 last row)
    exported_as: str | None = Field(
        default=None, validation_alias=AliasChoices("exported_as", "exportedAs")
    )

    def as_json(self) -> dict[str, Any]:
        return self.model_dump(mode="json", exclude_defaults=True)


class ContextItemRef(ApiModel):
    """One entry of a prospective model context, for ``evaluate_policy`` (AI-POL-006).

    The reference grammar is shared by the public read-only function and the runtime so
    both speak about the same objects: ``artifact:<id>``, ``blueprint:<version>`` or
    ``blueprint:draft:<id>``.
    """

    ref: str
    #: an explicit classification wins; otherwise it is resolved from the reference
    classification: str | None = None
    label: str | None = None

    @property
    def artifact_id(self) -> str | None:
        if self.ref.startswith("artifact:"):
            return self.ref[len("artifact:") :]
        return None

    @property
    def is_blueprint(self) -> bool:
        return self.ref.startswith("blueprint:")


__all__ = ["AgentRunRequest", "ParseOptions", "ContextItemRef"]
