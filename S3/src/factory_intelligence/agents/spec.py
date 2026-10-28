"""The agent table — SRS 3.3 as data (AI-AG-001 … AI-AG-014).

Every row of the SRS's agent table is one :class:`AgentSpec` here, and the runtime reads
its rules from the row rather than from an ``if agent_type == …`` chain.  Three things
follow from putting the table in one place:

* an agent's **sections** bound what a patch may touch before S2 is ever consulted
  (AI-RT-012): a Mapping Agent cannot write a page and a Requirements Agent cannot write a
  mapping — structurally, not by prompt;
* **``freeze_sensitive``** expresses AI-AG-006's "映射冻结后只输出解释与建议，不产生 Patch".
  The set of sections ARCHITECTURE_GATE freezes is declared per *agent* here rather than
  imported from S2, because whose sections an agent writes is S3's fact; S2 remains the
  authority that refuses the write (BP-DRF-005.5), and without this flag an explain-only
  run would instead fail on a rejection the runtime could have foreseen;
* **``milestone``** is what :mod:`factory_intelligence.feature` gates on, so "delivered" is
  a row in a table rather than a comment in a function.
"""

from __future__ import annotations

import enum
from dataclasses import dataclass, field

from factory_blueprint.api import DesignStage, Section

from factory_intelligence.enums import AgentType, Milestone


class PatchMode(str, enum.Enum):
    """What the runtime does with the agent's ``patch`` part."""

    #: submit the patch to the draft — the normal case (AI-RT-013)
    PATCH = "PATCH"
    #: record the output only, whatever the model put in ``patch`` (AI-AG-006 frozen,
    #: AI-AG-008 review assistant)
    EXPLAIN_ONLY = "EXPLAIN_ONLY"


@dataclass(frozen=True)
class AgentSpec:
    agent_type: AgentType
    stage: DesignStage
    milestone: Milestone
    #: prompt short name; the active *version* comes from ``ai.prompt_activation``
    prompt_name: str
    #: sections a patch operation may address
    sections: tuple[Section, ...] = ()
    #: AI-AG-013: one run per page, with the page key in ``scope``
    per_page: bool = False
    patch_mode: PatchMode = PatchMode.PATCH
    #: names from ``agents.checks.RULES``, run in this order (AI-RT-012)
    checks: tuple[str, ...] = ()
    #: sections read into the context but never written
    read_only_sections: tuple[Section, ...] = field(default_factory=tuple)
    #: a run needs an open draft to have anywhere to put a patch (AI-ASM-02)
    requires_draft: bool = True
    #: whose decisions may carry a user confirmation record (AI-RT-016, BP-DEC-004)
    confirmable: bool = False
    #: AI-INP-001: a mapping-related agent consumes NRR entries and approved Blueprint
    #: items only.  Raw business input is the parser's job; letting a MAPPING run read a
    #: ``.xlsx`` straight into context would re-decide what the extraction already decided,
    #: with no parse record and no confidence to attach to it.
    nrr_only: bool = False
    #: which sections ARCHITECTURE_GATE freezes for this agent (AI-AG-006)
    frozen_sections: tuple[Section, ...] = ()

    @property
    def key(self) -> str:
        return self.agent_type.value

    @property
    def writable_section_values(self) -> tuple[str, ...]:
        return tuple(section.value for section in self.sections)

    def accepts_patch(self, *, mapping_frozen: bool) -> bool:
        """Whether a patch from this agent is submitted at all (AI-AG-006)."""
        if self.patch_mode is PatchMode.EXPLAIN_ONLY:
            return False
        return not mapping_frozen or not self.frozen_sections

    def stage_rules(self, *, mapping_frozen: bool) -> str:
        """The stage rules of AI-RT-003's second context segment, as this table states them.

        S3 owns no stage-rule store: what a stage permits an agent to do *is* this row — the
        sections it may write, the ones it may only read, whether it patches at all, and what
        happens once the mapping is frozen.  Saying it to the model in its own words, in its
        own segment, is what makes the rule visible to the check that enforces it rather than
        only to the prompt's prose (AI-RT-012).
        """
        lines = [
            f"Stage: {self.stage.value}.",
            f"Your output is {self.patch_mode.value.lower()}: "
            + (
                "a patch against the open working draft, which a human Gate still decides on."
                if self.patch_mode is PatchMode.PATCH
                else "explanation and suggestion only; you submit no patch."
            ),
        ]
        if self.patch_mode is PatchMode.PATCH:
            lines.append(
                "Patch paths are RFC 6902 JSON Pointers over the current draft. Keyed "
                "containers are JSON arrays: append a new entry with `/<container>/-`, and "
                "address an existing entry by its zero-based index (`/<container>/<index>` "
                "or `/<container>/<index>/<field>`). A businessKey is never an array index, "
                "and replacing an index the draft does not contain is invalid. A "
                "WAITING_INPUT answer was not applied: re-emit every operation needed from "
                "the draft in context, not a delta against your previous answer."
            )
        if self.sections:
            lines.append("You may address these Blueprint sections: " + ", ".join(
                section.value for section in self.sections) + ".")
        else:
            lines.append("You may address no Blueprint section.")
        if self.read_only_sections:
            lines.append(
                "You may read but never rewrite: "
                + ", ".join(section.value for section in self.read_only_sections)
                + "."
            )
        if Section.REQUIREMENTS in self.read_only_sections:
            lines.append(
                "The requirements entries in your context come from the approved REQUIREMENTS "
                "Gate. Treat every one of them as confirmed even if its per-entry status still "
                "says CANDIDATE: that status was recorded before the Gate decided, and the "
                "Gate approved the section as a whole."
            )
        if Section.USE_CASES in self.sections:
            lines.append(
                "Use case `name` is a plain string, not a localized object; `purpose` and "
                "`outcome` are plain strings as well, while `steps` and `preconditions` are "
                "arrays of plain strings."
            )
        if Section.ACTORS in self.sections:
            lines.append(
                "Actor `name` and `description` are localized objects such as "
                '{"default": "Nurse"}; an entity attribute is different and has no description.'
            )
        if Section.DOMAIN in self.sections:
            lines.append(
                "Relationship `candidateExpressions` are strings of at most 128 characters; "
                "keep each one short. Entity `displayName`/`description` are localized objects, "
                "while an attribute is {name, valueType, required, sensitivity} and carries no "
                "description field."
            )
        if self.per_page:
            lines.append("One run designs one page; the page key is in your scope.")
        if self.confirmable:
            lines.append(
                "A decision a user confirmed is recorded with the question and the answerer."
            )
        if self.frozen_sections:
            lines.append(
                (
                    "Frozen for this run: "
                    + ", ".join(section.value for section in self.frozen_sections)
                    + " — explain and suggest a source-stage fix instead."
                )
                if mapping_frozen
                else (
                    "These sections freeze once the mapping is approved: "
                    + ", ".join(section.value for section in self.frozen_sections)
                    + "."
                )
            )
        return "\n".join(lines)


_MAPPING_FROZEN = (Section.MAPPING, Section.REGISTRY, Section.ARCHITECTURE)

_SPECS: tuple[AgentSpec, ...] = (
    AgentSpec(
        agent_type=AgentType.REQUIREMENTS,
        stage=DesignStage.REQUIREMENTS,
        milestone=Milestone.M2,
        prompt_name="requirements.consolidate",
        sections=(Section.REQUIREMENTS,),
        checks=(
            "no_final_products",
            "no_dhis2_reference",
            "decision_coverage",
            "nrr_confidence_parity",
        ),
    ),
    AgentSpec(
        agent_type=AgentType.DOMAIN,
        stage=DesignStage.DOMAIN,
        milestone=Milestone.M2,
        prompt_name="domain.model",
        sections=(Section.DOMAIN, Section.USE_CASES, Section.ACTORS),
        # The domain model is derived from the requirements the project just approved.  Without
        # this the run context held only generationPolicy, and the agent rightly refused to
        # invent entities from nothing.
        read_only_sections=(Section.REQUIREMENTS,),
        checks=(
            "no_final_products",
            "no_dhis2_reference",
            "decision_coverage",
            "attribute_sensitivity",
        ),
    ),
    AgentSpec(
        agent_type=AgentType.MAPPING,
        stage=DesignStage.MAPPING,
        milestone=Milestone.M2,
        prompt_name="mapping.decide",
        sections=_MAPPING_FROZEN,
        checks=(
            "no_dhis2_payload",
            "uid_ban",
            "decision_coverage",
            "decision_registered",
            "low_confidence_question",
            "reuse_decision_per_similar_object",
        ),
        read_only_sections=(Section.REQUIREMENTS, Section.DOMAIN),
        confirmable=True,
        nrr_only=True,
        frozen_sections=_MAPPING_FROZEN,
    ),
    AgentSpec(
        agent_type=AgentType.METADATA_DESIGN,
        stage=DesignStage.MAPPING,
        milestone=Milestone.M2,
        prompt_name="metadata_design.expression",
        sections=(Section.RULES,),
        checks=("no_dhis2_payload", "business_key_references_only", "no_final_products"),
        read_only_sections=_MAPPING_FROZEN,
        frozen_sections=_MAPPING_FROZEN,
        nrr_only=True,
    ),
    # --- M3: in the table so it matches SRS 3.3, gated by ``milestone`` --------------
    AgentSpec(
        agent_type=AgentType.API,
        stage=DesignStage.PAGE_DESIGN,
        milestone=Milestone.M3,
        prompt_name="api.design",
        sections=(Section.API_INTENTS,),
        per_page=True,
        checks=("single_page_scope", "no_registry_write", "logical_resource_names"),
        read_only_sections=_MAPPING_FROZEN + (Section.PAGES,),
        nrr_only=True,
        frozen_sections=_MAPPING_FROZEN,
    ),
    AgentSpec(
        agent_type=AgentType.UI,
        stage=DesignStage.PAGE_DESIGN,
        milestone=Milestone.M3,
        prompt_name="ui.design",
        sections=(Section.UI_INTENTS,),
        per_page=True,
        checks=("single_page_scope", "ui_data_source_resolves"),
        read_only_sections=(Section.API_INTENTS, Section.REGISTRY, Section.PAGES),
        nrr_only=True,
        frozen_sections=_MAPPING_FROZEN,
    ),
    AgentSpec(
        agent_type=AgentType.FIXTURE,
        stage=DesignStage.PAGE_DESIGN,
        milestone=Milestone.M3,
        prompt_name="fixture.specify",
        sections=(Section.FIXTURES,),
        checks=("no_phi_fixture", "synthetic_only"),
        read_only_sections=(Section.DOMAIN, Section.REGISTRY),
        nrr_only=True,
    ),
    AgentSpec(
        agent_type=AgentType.REVIEW,
        stage=DesignStage.REQUIREMENTS,
        milestone=Milestone.M3,
        prompt_name="review.explain",
        sections=(),
        patch_mode=PatchMode.EXPLAIN_ONLY,
        checks=("no_final_products", "uid_ban", "business_key_references_only"),
        requires_draft=False,
    ),
)

#: The compiled table, keyed by agent type.
AGENT_SPECS: dict[AgentType, AgentSpec] = {spec.agent_type: spec for spec in _SPECS}


def spec_for(agent_type: AgentType | str) -> AgentSpec:
    """The row for one agent type."""
    return AGENT_SPECS[AgentType(agent_type)]


__all__ = ["AgentSpec", "PatchMode", "AGENT_SPECS", "spec_for"]
