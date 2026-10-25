"""SRS V1.1.0's additions, end to end: typed governance, parse read models, runtime truth.

The SRS was re-issued after this package was built (V1.0.0 → V1.1.0), and the additions are
not refinements of existing behaviour — they are capabilities that did not exist:

* **AI-GOV-001…008** — every governance capability as one typed function.  The "action name
  plus a free dictionary" façade *was* the public surface before, and S1 cannot declare a
  named REST component from it (00 doc 6.11).  These tests pin the typed functions and that
  the legacy façade answers the same thing, because S1 still forwards ``governance`` today.
* **AI-INP-013/014/015** — the parse records, the structured column-mapping proposal and the
  NRR query, which S1's workbench reads per input (CP-UI-007, CP-UI-003) rather than from its
  own step rows.
* **AI-RT-018/019 with AI-AC-14/16** — a run in flight is reported as ``RUNNING`` with its
  last progress, and findings travel in S2's unified Issue structure whose level field is
  ``level`` (00 doc 6.10).
* **AI-INP-009 across inputs, AI-INP-012 by content, AI-INP-002's reuse review, AI-INP-001's
  NRR-only rule for mapping agents, AI-AG-013's page order, AI-BR-08's reasoning ban.**

Nothing here reaches a network: the model is the scripted mock provider (AI-MOCK-001).
"""

from __future__ import annotations

import json
from typing import Any

import pytest
from factory_foundation.api import FactoryError, new_id, unit_of_work
from pydantic import ValidationError as PydanticValidationError
from sqlalchemy import select

from factory_intelligence import api, artifacts
from factory_intelligence.dto.governance import (
    ColumnMappingProposal,
    ModelSpec,
    NrrFilters,
    Page,
    ProviderSpec,
    wire,
)
from factory_intelligence.enums import AgentType, ParseStatus, ProviderClass, RunState
from factory_intelligence.errors import PromptActivationBlocked
from factory_intelligence.models import AgentRunRow
from factory_intelligence.models.nrr import NrrEntryRow
from factory_intelligence.runtime import state as run_state
from factory_intelligence.testing.harness import IntelligenceHarness
from tests.integration import test_parse_input as pin
from tests.integration.agent_runtime import (
    add_operation,
    answer,
    clear_prompt_activations,
    install_mock_model,
    open_draft,
    request_for,
    requirement_item,
    script,
    seed_project,
    stage_input,
    unique_project_id,
)


def _recent_ai_events() -> list[dict[str, Any]]:
    """The ai.* audit rows written so far, newest last, as S0 stored them."""
    from factory_foundation.api import unit_of_work
    from sqlalchemy import text

    with unit_of_work() as sess:
        rows = sess.execute(
            text("select event_type, after_summary from core.audit_event where event_type = 'ai.model.registered' order by occurred_at, id")
        ).all()
    return [{"event_type": r[0], "after": r[1] or {}} for r in rows]


FIELDS_CSV = "字段,说明\n年龄,采集于登记时\n性别,枚举\n".encode()
FLAT_CSV = b"field,description\nage,collected at registration\n"
CONFLICTING_CSV = b"field,description\nage,collected at the clinic door\n"
NRR_COLUMNS = {"columnMapping": {"field": "field", "description": "description"}}


# --- AI-GOV-001/002: the typed governance surface --------------------------------


def test_providers_are_registered_and_read_through_typed_functions(
    harness: IntelligenceHarness,
) -> None:
    """AI-GOV-002 / AC-13: the address and the Key *reference*, and no value anywhere."""
    _ = harness
    row = api.register_provider(
        ProviderSpec(
            name="typed-local",
            provider_class=ProviderClass.LOCAL_ONLY,
            endpoint="https://intranet.invalid/v1",
            api_key_secret_ref="env://TYPED_LOCAL_KEY",
            adapter="openai_compat",
            vendor="deepseek",
            suggested_models=["deepseek-chat", " deepseek-chat ", "deepseek-reasoner"],
        ),
        "ops",
    )
    assert row.endpoint == "https://intranet.invalid/v1"
    assert row.api_key_secret_ref == "env://TYPED_LOCAL_KEY"
    assert row.vendor == "deepseek"
    # trimmed and deduplicated: two identical candidate buttons are one button
    assert row.suggested_models == ["deepseek-chat", "deepseek-reasoner"]
    assert [item.model_dump() for item in api.list_providers() if item.name == "typed-local"] == [
        row.model_dump()
    ]

    switched = api.set_provider_enabled("typed-local", False, "ops")
    assert (switched.ref, switched.enabled) == ("typed-local", False)
    assert [item.enabled for item in api.list_providers() if item.name == "typed-local"] == [False]

    # a literal key is refused before it can reach a column, an audit row or a log line
    with pytest.raises(FactoryError) as refused:
        api.register_provider(
            {"name": "leaky", "providerClass": "LOCAL_ONLY", "apiKeySecretRef": "sk-secret"},
            "ops",
        )
    assert refused.value.code == "VALIDATION_ERROR"
    assert "reference" in str(refused.value)
    assert "sk-secret" not in str(refused.value), "the refusal does not repeat the secret"


def test_editing_a_provider_changes_only_the_fields_supplied(
    harness: IntelligenceHarness,
) -> None:
    """AI-REG-004: re-registration overwrites the whole row, so fixing a mistyped address used
    to clear the Key reference with it.  The edit path leaves every column the caller did not
    name exactly as it was, which is what makes an operator correction safe to attempt."""
    _ = harness
    api.register_provider(
        ProviderSpec(
            name="typo-fixed",
            provider_class=ProviderClass.PRIVATE_NETWORK,
            endpoint="https://old.invalid/v1",
            api_key_secret_ref="env://FIXED_KEY",
        ),
        "ops",
    )

    moved = api.update_provider("typo-fixed", {"endpoint": "https://new.invalid/v1"}, "ops")
    assert moved.endpoint == "https://new.invalid/v1"
    assert moved.api_key_secret_ref == "env://FIXED_KEY", "the Key reference survived the edit"
    assert moved.provider_class is ProviderClass.PRIVATE_NETWORK
    assert moved.enabled is True
    # both presentational fields were absent from the patch, so neither moved
    assert (moved.vendor, moved.suggested_models) == ("custom", [])

    labelled = api.update_provider(
        "typo-fixed",
        {"vendor": "openai", "suggestedModels": ["gpt-4o", "gpt-4o-mini"]},
        "ops",
    )
    assert (labelled.vendor, labelled.suggested_models) == ("openai", ["gpt-4o", "gpt-4o-mini"])

    switched = api.update_provider("typo-fixed", {"enabled": False}, "ops")
    assert (switched.enabled, switched.endpoint) == (False, "https://new.invalid/v1")
    assert switched.suggested_models == ["gpt-4o", "gpt-4o-mini"], "an unrelated edit kept the suggestions"

    # an empty list is a change: it clears the candidates, unlike an absent field
    cleared = api.update_provider("typo-fixed", {"suggestedModels": []}, "ops")
    assert cleared.suggested_models == []
    assert cleared.vendor == "openai"

    with pytest.raises(FactoryError) as blank_vendor:
        api.update_provider("typo-fixed", {"vendor": "   "}, "ops")
    assert blank_vendor.value.code == "VALIDATION_ERROR"

    with pytest.raises(FactoryError) as too_long:
        api.update_provider(
            "typo-fixed", {"suggestedModels": ["x" * 129]}, "ops"
        )
    assert too_long.value.code == "VALIDATION_ERROR"

    with pytest.raises(FactoryError) as unknown:
        api.update_provider("never-registered", {"endpoint": "https://x.invalid/v1"}, "ops")
    assert unknown.value.code == "NOT_FOUND"

    with pytest.raises(FactoryError) as literal:
        api.update_provider("typo-fixed", {"apiKeySecretRef": "sk-secret"}, "ops")
    assert literal.value.code == "VALIDATION_ERROR"
    assert "sk-secret" not in str(literal.value), "the refusal does not repeat the secret"

    with pytest.raises(FactoryError) as nothing:
        api.update_provider("typo-fixed", {}, "ops")
    assert nothing.value.code == "VALIDATION_ERROR"


def test_editing_a_provider_can_move_its_class_and_adapter(
    harness: IntelligenceHarness,
) -> None:
    """AI-REG-004: the class decides what data may leave the Factory and the adapter names the
    transport, so both have to be correctable in place — and both were untested."""
    _ = harness
    api.register_provider(
        ProviderSpec(
            name="reclass",
            provider_class=ProviderClass.PRIVATE_NETWORK,
            endpoint="https://old.invalid/v1",
            api_key_secret_ref="env://RECLASS_KEY",
        ),
        "ops",
    )

    moved = api.update_provider("reclass", {"providerClass": "LOCAL_ONLY"}, "ops")
    assert moved.provider_class is ProviderClass.LOCAL_ONLY
    assert (moved.endpoint, moved.api_key_secret_ref) == ("https://old.invalid/v1", "env://RECLASS_KEY")

    adapted = api.update_provider("reclass", {"adapter": "other_compat"}, "ops")
    assert adapted.adapter == "other_compat"
    assert adapted.provider_class is ProviderClass.LOCAL_ONLY, "the earlier edit survived"

    with pytest.raises(FactoryError) as blank:
        api.update_provider("reclass", {"endpoint": "   "}, "ops")
    assert blank.value.code == "VALIDATION_ERROR"
    # the refusal must not have cost the row its address
    assert api.list_providers()[-1].endpoint == "https://old.invalid/v1"

    # a mapping is decoded by the DTO first, so an unknown class is a pydantic refusal there;
    # the registry's own guard (which reports `details.providerClass`) is what a caller passing
    # keyword arguments straight to `model_registry.update_provider` gets. S1 refuses earlier
    # still, at its own boundary, with the allowed set named.
    with pytest.raises(PydanticValidationError) as bad_class:
        api.update_provider("reclass", {"providerClass": "SOMETHING_ELSE"}, "ops")
    assert "SOMETHING_ELSE" in str(bad_class.value)

    # every edit event speaks the same camelCase vocabulary registration uses; a snake_case key
    # here would make one reader necessary for two writes of the same column
    payloads = [e["after"] for e in _recent_ai_events() if e["after"].get("action") == "updateProvider"]
    assert payloads, "the edits wrote no audit event"
    flat = {k for p in payloads for k in p}
    assert "providerClass" in flat, "the class edit is not in the audit trail"
    assert not {"provider_class", "api_key_secret_ref"} & flat


def test_a_model_may_be_served_at_its_own_address(harness: IntelligenceHarness) -> None:
    """AI-LLM-003: per-registration address and Key, with the provider's as the fallback."""
    _ = harness
    api.register_provider(
        ProviderSpec(
            name="two-servers",
            provider_class=ProviderClass.PRIVATE_NETWORK,
            endpoint="https://vllm.invalid/v1",
            api_key_secret_ref="env://VLLM_KEY",
        ),
        "ops",
    )
    shared = api.register_model(
        ModelSpec(provider="two-servers", model="shared-1", applicable_agents={"MAPPING": 1}),
        "ops",
    )
    own = api.register_model(
        ModelSpec(
            provider="two-servers",
            model="ollama-1",
            endpoint="https://ollama.invalid/v1",
            api_key_secret_ref="env://OLLAMA_KEY",
            unit_price_input_per_1k=0.002,
            applicable_agents={"MAPPING": 2},
        ),
        "ops",
    )
    assert shared.endpoint == "https://vllm.invalid/v1", "absent means the provider's own"
    assert own.endpoint == "https://ollama.invalid/v1"
    assert own.api_key_secret_ref == "env://OLLAMA_KEY"
    assert own.unit_price_input_per_1k == 0.002
    assert [item.ref for item in api.list_models("MAPPING")] == [
        "two-servers/ollama-1",
        "two-servers/shared-1",
    ]
    assert api.set_model_enabled("two-servers/ollama-1", False, "ops").enabled is False
    assert [item.ref for item in api.list_models(include_disabled=False)] == [
        "two-servers/shared-1"
    ]


def test_the_facade_and_the_typed_function_answer_one_row(
    harness: IntelligenceHarness,
) -> None:
    """AI-GOV-001: S1 may forward either spelling, and a capability cannot have two answers.

    This is what keeps ``governance`` honest: it is a transport shim over the typed functions,
    so every payload is decoded into the same spec model and every answer is the same model
    dumped in the camelCase spelling S5 already reads (S5IF-GOV01…GOV19).
    """
    _ = harness
    forwarded = api.governance(
        "provider.register",
        {"name": "one-row", "providerClass": "LOCAL_ONLY", "operator": "ops"},
    )
    direct = wire(
        api.register_provider(
            ProviderSpec(name="one-row", provider_class=ProviderClass.LOCAL_ONLY), "ops"
        )
    )
    assert set(forwarded) == set(direct)
    assert {key: forwarded[key] for key in ("name", "providerClass", "enabled", "adapter")} == {
        key: direct[key] for key in ("name", "providerClass", "enabled", "adapter")
    }

    listed = api.governance("provider.list", {})["items"]
    assert [item for item in listed if item["name"] == "one-row"] == [
        wire(row) for row in api.list_providers() if row.name == "one-row"
    ]

    # the agent catalogue and the effective policy are reachable both ways too
    assert api.governance("agent.list", {}) == wire(api.list_agents())
    assert api.governance("policy.effective.get", {}) == wire(api.get_effective_policy({}))


def test_an_activation_refusal_names_what_is_missing(
    harness: IntelligenceHarness,
) -> None:
    """AI-GOV-003 / AI-AC-12: which gate refused, and the bar it fell under, in ``details``."""
    _ = harness
    clear_prompt_activations()
    with pytest.raises(PromptActivationBlocked) as refused:
        api.activate_prompt(
            AgentType.MAPPING.value, "mapping.decide", 1, ["mock/mock-agent"], "ops"
        )
    assert refused.value.details["missing"] == "EVAL_RUN"
    assert refused.value.details["requiredScore"] == api.settings().eval_pass_score
    assert refused.value.details["prompt"] == "mapping.decide.v1"

    regression = api.record_regression(
        "mapping.decide", 1, model="mock/mock-agent", operator="test-group"
    )
    assert regression.passed is True
    # the regression alone is still half a gate (AI-REG-003)
    with pytest.raises(PromptActivationBlocked) as second:
        api.activate_prompt(
            AgentType.MAPPING.value, "mapping.decide", 1, ["mock/mock-agent"], "ops"
        )
    assert second.value.details["missing"] == "EVAL_RUN"

    versions = api.list_prompts("MAPPING")
    assert versions and versions[0].active is False
    detail = api.get_prompt("mapping.decide", 1)
    assert detail.body and detail.qualified_name == "mapping.decide.v1"
    assert detail.active is False


def test_effective_policy_is_the_strictest_of_org_and_every_environment(
    harness: IntelligenceHarness,
) -> None:
    """AI-GOV-006 / AI-AC-06 / D-07: the combination a project page shows (S5IF-GOV12)."""
    _ = harness
    org = api.get_org_policy()
    assert org.is_default is True
    assert "APPROVED_EXTERNAL" not in org.effective.allowed["INTERNAL"]

    relaxed = api.relax_org_policy(
        {"level": "INTERNAL", "addClasses": ["APPROVED_EXTERNAL"]},
        reason="the vendor is under a signed DPA",
        operator="ops",
    )
    assert relaxed.reason and relaxed.role_gate == "operator-name (MVP)"
    assert api.get_org_policy().is_default is False

    combined = api.get_effective_policy(
        {"prod": {"allowed": {"INTERNAL": ["LOCAL_ONLY", "PRIVATE_NETWORK"]}}}
    )
    # AI-AC-06: the environment's narrower answer wins, whatever the organisation relaxed to
    assert "APPROVED_EXTERNAL" not in combined.allowed["INTERNAL"]
    assert "LOCAL_ONLY" in combined.allowed["INTERNAL"]
    assert "org" in combined.sources and any(item.startswith("env:") for item in combined.sources)

    with pytest.raises(FactoryError) as phi:
        api.relax_org_policy(
            {"level": "PHI_PII", "addClasses": ["LOCAL_ONLY"]},
            reason="try it anyway",
            operator="ops",
        )
    assert phi.value.code == "AI_POLICY_RELAXATION_DENIED"


def test_budget_answers_both_directions_and_the_catalogue_reports_the_live_prompt(
    harness: IntelligenceHarness,
) -> None:
    """AI-GOV-007 (read from M1, written from M4) and AI-GOV-008 (the live prompt)."""
    _ = harness
    clear_prompt_activations()
    project = unique_project_id("gov")
    assert api.get_budget(project).state.value == "UNSET"
    assert api.set_budget(project, 10.0, [0.8], operator="ops").limit == 10.0
    assert api.get_budget(project).state.value == "OK", "no spend yet, so the limit is not crossed"

    by_agent = {item.agent_type.value: item for item in api.list_agents().items}
    assert set(by_agent) == {member.value for member in AgentType}
    assert by_agent["MAPPING"].enabled is True
    assert by_agent["API"].per_page is True
    assert by_agent["REVIEW"].patch_mode == "EXPLAIN_ONLY"
    # AI-CHAT-001: in-stage dialogue answers through the Review Assistant, so it ships with
    # chat rather than waiting for the rest of M3.  PAGE_DESIGN's three agents ship for the same
    # kind of reason -- the stage is on the current design line and mode-B HANDOFF reads the
    # intents it produces -- and their milestone still says M3, because that is the schedule,
    # while ``enabled`` is what this build actually does.
    for agent in ("REVIEW", "API", "UI", "FIXTURE"):
        assert by_agent[agent].milestone.value == "M3", agent
        assert by_agent[agent].enabled is True, agent
    assert by_agent["MAPPING"].active_prompt is None, "nothing has been activated here"


# --- AI-INP-013/014/015: the parse read models -----------------------------------


def test_parse_records_report_the_latest_parse_of_each_input(
    harness: IntelligenceHarness,
) -> None:
    """AI-INP-013: one status line per input, with the parser that ran and what it produced."""
    _ = harness
    project = unique_project_id("records")
    first = pin.put_input(project, FIELDS_CSV, filename="fields.csv", media_type="text/csv")
    untouched = pin.put_input(
        project,
        b"# Goals\nReduce missed visits\n",
        filename="notes.md",
        media_type="text/markdown",
    )

    pin.parse(first, project_id=project, key="record:first")
    again = pin.parse(
        first, project_id=project, options={"columnMapping": {"字段": "字段"}}, key="record:second"
    )

    records = {row.input_artifact_id: row for row in api.list_input_parses(project)}
    assert set(records) == {first}, "an input that was never parsed is simply absent"
    latest = records[first]
    assert latest.parse_id == again.parse_id, "the most recent parse is the one reported"
    assert latest.status is ParseStatus.PARSED
    assert latest.parser_type == "CSV" and latest.parser_version
    assert latest.used_model is False and latest.nrr_artifact_id
    assert latest.input_hash.startswith("sha256:")
    assert latest.error_code is None
    assert [row.parse_id for row in api.list_input_parses(project, [first])] == [again.parse_id]
    assert api.list_input_parses(project, [untouched]) == []


def test_the_column_mapping_proposal_is_a_table_not_only_a_question(
    harness: IntelligenceHarness,
) -> None:
    """AI-INP-014: headers, category, proposed mapping, basis and affected keys per sheet."""
    _ = harness
    project = unique_project_id("mapping")
    artifact = pin.put_input(project, FIELDS_CSV, filename="fields.csv", media_type="text/csv")
    result = pin.parse(artifact, project_id=project)
    assert result.status is ParseStatus.NEEDS_REVIEW

    proposal: ColumnMappingProposal = api.get_column_mapping_proposal(project, artifact)
    assert proposal.parse_id == result.parse_id
    assert proposal.status is ParseStatus.NEEDS_REVIEW
    assert [sheet.sheet for sheet in proposal.sheets] == ["fields"]
    sheet = proposal.sheets[0]
    assert sheet.headers == ["字段", "说明"]
    assert sheet.category == "dataRequirement", "the list category it was read as"
    assert set(sheet.mapping) == {"字段", "说明"}
    assert sheet.recognised_by and "header" in sheet.recognised_by
    assert sheet.affected_keys and sheet.question

    # the proposal is executable: the same spelling goes back as options.columnMapping
    confirmed = pin.parse(
        artifact,
        project_id=project,
        options={"columnMapping": sheet.mapping},
        key="mapping:confirmed",
    )
    assert confirmed.status is ParseStatus.PARSED
    assert api.get_column_mapping_proposal(project, artifact).sheets == []


def test_the_nrr_can_be_queried_by_category_confidence_and_page(
    harness: IntelligenceHarness,
) -> None:
    """AI-INP-015 / AI-AC-15: entries with their source location, filtered and paged."""
    _ = harness
    project = unique_project_id("nrr")
    artifact = pin.put_input(project, FIELDS_CSV, filename="fields.csv", media_type="text/csv")
    pin.parse(
        artifact,
        project_id=project,
        options={"columnMapping": {"字段": "field"}},
        key="nrr:confirmed",
    )

    everything = api.query_nrr(project)
    assert everything.total == len(everything.items) > 0
    assert {item.category for item in everything.items} == {"dataRequirement"}
    assert all(item.artifact_id == artifact for item in everything.items)
    assert all(item.location and item.confirmed and item.statement for item in everything.items)

    assert api.query_nrr(project, NrrFilters(categories=["participant"])).items == []
    # the enum member name is accepted as well as its value
    assert api.query_nrr(project, NrrFilters(categories=["DATA_REQUIREMENT"])).total == (
        everything.total
    )
    assert api.query_nrr(project, NrrFilters(min_confidence=0.99)).total == everything.total
    assert api.query_nrr(project, NrrFilters(max_confidence=0.5)).items == []
    assert api.query_nrr(project, NrrFilters(input_artifact_ids=[artifact])).total == (
        everything.total
    )

    paged = api.query_nrr(project, page=Page(limit=1, offset=1))
    assert paged.total == everything.total and len(paged.items) == 1
    assert paged.items[0].business_key == everything.items[1].business_key


def test_the_same_input_with_the_same_options_returns_the_first_result(
    harness: IntelligenceHarness,
) -> None:
    """AI-INP-012's literal wording: keyed by content, not only by the caller's key."""
    _ = harness
    project = unique_project_id("replay")
    artifact = pin.put_input(project, FIELDS_CSV, filename="fields.csv", media_type="text/csv")
    first = pin.parse(
        artifact, project_id=project, options={"columnMapping": {"字段": "字段"}}, key="replay:first"
    )
    again = pin.parse(
        artifact,
        project_id=project,
        options={"columnMapping": {"字段": "字段"}},
        key="replay:second",
    )
    different = pin.parse(
        artifact,
        project_id=project,
        options={"columnMapping": {"字段": "field", "说明": "description"}},
        key="replay:third",
    )

    assert again == first, "one question asked twice is one answer, not two NRR artifacts"
    assert different.parse_id != first.parse_id, "a changed mapping is a different question"
    with unit_of_work() as session:
        produced = {
            row.nrr_artifact_id
            for row in session.scalars(
                select(NrrEntryRow).where(NrrEntryRow.project_id == project)
            )
            if row.nrr_artifact_id
        }
    assert len(produced) == 2


def test_two_inputs_that_disagree_produce_one_conflict_question(
    harness: IntelligenceHarness,
) -> None:
    """AI-INP-009: no winner is picked; both places are named and nothing is confirmed."""
    _ = harness
    project = unique_project_id("conflict")
    first = pin.put_input(project, FLAT_CSV, filename="a.csv", media_type="text/csv")
    second = pin.put_input(project, CONFLICTING_CSV, filename="b.csv", media_type="text/csv")
    pin.parse(first, project_id=project, options=NRR_COLUMNS, key="conflict:first")
    result = pin.parse(second, project_id=project, options=NRR_COLUMNS, key="conflict:second")

    conflicts = [item for item in result.questions if item.details.get("sources")]
    assert conflicts, [item.text for item in result.questions]
    question = conflicts[0]
    assert question.severity == "HIGH" and question.blocking is True
    assert {item["artifactId"] for item in question.details["sources"]} == {first, second}
    assert all(item["location"] for item in question.details["sources"]), "both places are named"
    contested = api.query_nrr(project, NrrFilters(input_artifact_ids=[second]))
    assert contested.items and all(item.confirmed is False for item in contested.items), (
        "a contested fact never enters the confirmed part of the NRR"
    )


def test_existing_metadata_is_reported_as_a_reuse_candidate_not_a_fact(
    harness: IntelligenceHarness,
) -> None:
    """AI-INP-002: 既有 DHIS2 元数据 → 复核复用意图, even for a clean export."""
    _ = harness
    project = unique_project_id("reuse")
    artifact = pin.put_input(
        project,
        b'{"dataElements":[{"id":"DE1","code":"WEIGHT","displayName":"Weight","valueType":"NUMBER"}]}',
        filename="meta.json",
        media_type="application/json",
    )
    result = pin.parse(artifact, project_id=project)

    assert result.status is ParseStatus.NEEDS_REVIEW
    reuse = [item for item in result.questions if "reus" in item.text.lower()]
    assert reuse, [item.text for item in result.questions]
    assert "dataElement" in reuse[0].text


# --- AI-RT-018/019/020, AI-INP-001, AI-AG-013, AI-BR-08 ---------------------------


def _draft(project: str, stage: str = "REQUIREMENTS") -> str:
    seed_project(project)
    return open_draft(project, stage)


def _row(run_id: str) -> dict[str, Any]:
    with unit_of_work() as session:
        row = session.get(AgentRunRow, run_id)
        assert row is not None
        return {
            "status": row.status,
            "lastProgress": dict(row.last_progress or {}),
            "inputHashes": dict(row.input_hashes or {}),
            "blueprintRef": dict(row.blueprint_ref or {}),
        }


class CancellingProvider:
    """Answers once, then cancels the run from "another worker" (AI-AC-08's real shape)."""

    def __init__(self, inner: Any) -> None:
        self._inner = inner
        self.call_count = 0

    def __getattr__(self, name: str) -> Any:
        return getattr(self._inner, name)

    @property
    def info(self) -> Any:
        return self._inner.info

    def generate_structured(self, request: Any) -> Any:
        response = self._inner.generate_structured(request)
        self.call_count += 1
        if request.agent_hint is not None and request.agent_hint.run_id:
            api.cancel_agent_run(request.agent_hint.run_id, reason="gate closed")
        return response


def test_a_run_in_flight_is_reported_as_running_with_its_progress(
    harness: IntelligenceHarness,
) -> None:
    """AI-RT-019 / AI-AC-14: ``RUNNING``, not one of the six, plus what it last said.

    S1 had to correct this itself: S3 reported a still-running run as ``WAITING_INPUT`` and
    derived ``RUNNING`` from its own stage (``S1 services/agent_runs.py:88``), which is the
    kind of substitution that parks a stage nobody is waiting on.
    """
    _ = harness
    project = unique_project_id("running")
    request = request_for(project, AgentType.REQUIREMENTS, idempotency_key="running:query")
    with unit_of_work() as session:
        run_id = run_state.create_run(request, run_id=new_id("arun"), session=session).id

    view = api.get_agent_run(run_id)
    assert view is not None
    assert view.status is RunState.RUNNING, view.model_dump()
    assert view.summary is None and view.patch_ids == []
    assert view.budget_status["state"] == "UNSET"
    assert view.last_progress == {}

    run_state.record_progress(run_id, "model_call", 0.3, {"modelCallIndex": 1})
    again = api.get_agent_run(run_id)
    assert again is not None
    assert again.status is RunState.RUNNING
    assert again.last_progress["step"] == "model_call"
    assert again.last_progress["data"]["modelCallIndex"] == 1
    assert _row(run_id)["status"] == RunState.RUNNING.value


def test_a_flag_raised_while_the_model_answers_stops_before_the_patch(
    harness: IntelligenceHarness,
) -> None:
    """AI-RT-020 / AI-AC-08: the safe point *after* the call, on a run that would have finished.

    The answer here is valid, so before that safe point existed the run submitted its patch,
    registered its output and returned COMPLETED — after someone had already asked it to
    stop.  That is the half-applied draft AI-AC-08 and AI-COST-003 both refuse.
    """
    _ = harness
    project = unique_project_id("cancel-after")
    draft_id = _draft(project)
    inner = install_mock_model(AgentType.REQUIREMENTS)
    script(
        inner,
        AgentType.REQUIREMENTS,
        answer(
            operations=[add_operation("/requirements/items/-", requirement_item("art-0000000000"))]
        ),
    )
    provider = CancellingProvider(inner)
    api.set_llm_provider(provider)

    result = api.start_agent_run(
        request_for(
            project, AgentType.REQUIREMENTS, draft_id=draft_id, idempotency_key="ac08-after"
        )
    )

    assert result.status.value == "CANCELLED", result.model_dump()
    assert result.patch_ids == [], "the answer was valid, and it was still not submitted"
    assert provider.call_count == 1, "the call already paid for, then the safe point stopped it"
    assert _row(str(result.run_id))["status"] == RunState.CANCELLED.value


def test_a_run_records_the_hash_of_every_input_it_read(
    harness: IntelligenceHarness,
) -> None:
    """AI-RT-001: the run names the bytes it was built from, not only their ids."""
    _ = harness
    project = unique_project_id("hashes")
    draft_id = _draft(project)
    inner = install_mock_model(AgentType.REQUIREMENTS)
    script(inner, AgentType.REQUIREMENTS, answer())
    csv = pin.put_input(project, FIELDS_CSV, filename="fields.csv", media_type="text/csv")
    pin.parse(
        csv, project_id=project, options={"columnMapping": {"字段": "字段"}}, key="hash:parse"
    )
    nrr_artifact = str(api.list_input_parses(project, [csv])[0].nrr_artifact_id)

    result = api.start_agent_run(
        request_for(
            project,
            AgentType.REQUIREMENTS,
            draft_id=draft_id,
            input_artifact_ids=[nrr_artifact],
            idempotency_key="hashes:run",
        )
    )
    assert result.status.value == "COMPLETED", result.model_dump()
    row = _row(str(result.run_id))
    assert set(row["inputHashes"]) == {nrr_artifact}
    assert all(value.startswith("sha256:") for value in row["inputHashes"].values())
    assert row["blueprintRef"]["contentHash"], "the draft or version is hashed too"


def test_a_mapping_agent_is_refused_a_raw_business_input(
    harness: IntelligenceHarness,
) -> None:
    """AI-INP-001: mapping-related agents consume NRR and approved entries, not files."""
    _ = harness
    install_mock_model(AgentType.MAPPING)
    project = unique_project_id("raw")
    draft_id = _draft(project, "MAPPING")
    raw = stage_input(project, {"any": "business input"})

    result = api.start_agent_run(
        request_for(
            project,
            AgentType.MAPPING,
            stage="MAPPING",
            draft_id=draft_id,
            input_artifact_ids=[raw],
            idempotency_key="inp001:raw",
        )
    )
    assert result.status.value == "FAILED", result.model_dump()
    assert "AI-INP-001" in str(result.model_dump()), result.denial_reasons
    assert result.patch_ids == []


def test_the_ui_agent_of_a_page_waits_for_that_page_s_api_agent(
    harness: IntelligenceHarness,
) -> None:
    """AI-AG-013: S3 enforces the page order from its own run rows."""
    _ = harness

    project = unique_project_id("uiafterapi")
    draft_id = _draft(project, "PAGE_DESIGN")
    with pytest.MonkeyPatch.context() as patch:
        # a deployment may opt in early (feature.py), so the milestone gate is not the answer
        # start.py binds the name itself, so that binding is what has to answer
        patch.setattr("factory_intelligence.runtime.start.agent_enabled", lambda agent: True)
        result = api.start_agent_run(
            request_for(
                project,
                AgentType.UI,
                stage="PAGE_DESIGN",
                draft_id=draft_id,
                scope=["page:antenatal"],
                idempotency_key="ag013",
            )
        )
    assert result.status.value == "FAILED", result.model_dump()
    assert "API agent" in " ".join(result.denial_reasons), result.denial_reasons


def test_a_models_reasoning_is_never_recorded_as_a_decision(
    harness: IntelligenceHarness,
) -> None:
    """AI-BR-08 / AI-RT-014: the run strips a model's working, whatever it is called.

    The decision envelope leaves its extra properties open on purpose — a provider that adds a
    field must not lose the answer — which is exactly why the strip is code and not prose.
    """
    _ = harness
    project = unique_project_id("br08")
    draft_id = _draft(project)
    inner = install_mock_model(AgentType.REQUIREMENTS)
    script(
        inner,
        AgentType.REQUIREMENTS,
        {
            **answer(
                decisions=[
                    {
                        "id": "d-1",
                        "subject": "the age band recorded at registration",
                        "selected": {"choice": "as stated"},
                        "alternatives": [{"choice": "recomputed from a birth date"}],
                        "confidence": 0.9,
                        # a model's working, in whatever field it chooses to put it in
                        "reasoning": "I first thought, then thought again",
                        "chain_of_thought": ["step one", "step two"],
                    }
                ],
                operations=[
                    add_operation(
                        "/requirements/items/-", requirement_item("art-0000000000")
                    )
                ],
            ),
            # the envelope refuses an unknown *top-level* key outright (AI-RT-031), so the
            # reasoning has to arrive inside a decision, which is where it does fit
        },
    )
    result = api.start_agent_run(
        request_for(project, AgentType.REQUIREMENTS, draft_id=draft_id, idempotency_key="br08")
    )
    assert result.status.value == "COMPLETED", result.model_dump()

    document, _classification = artifacts.read_json(str(result.agent_output_artifact_id))
    text = json.dumps(document)
    assert "I first thought" not in text and "step one" not in text
    decision = document["decisions"][0]
    assert decision["id"] == "d-1" and decision["subject"], "the decision itself survives"
    assert decision["selected"] == {"choice": "as stated"}
    assert {"reasoning", "chain_of_thought"}.isdisjoint(decision)


def test_a_refused_run_reports_its_findings_as_issues_with_a_level(
    harness: IntelligenceHarness,
) -> None:
    """AI-RT-018 / AI-AC-16: 00 doc 6.10's field is ``level``, and a refusal is BLOCKING."""
    _ = harness
    project = unique_project_id("ac16")
    draft_id = _draft(project)
    inner = install_mock_model(AgentType.REQUIREMENTS)
    script(inner, AgentType.REQUIREMENTS, {"summary": ""}, {"summary": ""})

    result = api.start_agent_run(
        request_for(project, AgentType.REQUIREMENTS, draft_id=draft_id, idempotency_key="ac16")
    )

    assert result.status.value == "FAILED"
    assert result.issues, "a failure a workbench cannot grade is not a report"
    assert {issue.level for issue in result.issues} == {"BLOCKING"}
    assert all(issue.rule_id for issue in result.issues)
    dumped = result.issues[0].model_dump(mode="json")
    assert "level" in dumped and "message" in dumped
    assert "severity" not in dumped, "the alias is accepted inbound, never emitted"
    # S1's mirror still reads the legacy spellings; they are computed from the same values
    assert dumped["description"] == dumped["message"]
