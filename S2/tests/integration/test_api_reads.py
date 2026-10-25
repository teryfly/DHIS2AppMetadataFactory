"""The public query surface, exercised through ``factory_blueprint.api`` (SRS 7.2, 7.4).

S1 and S3 are supposed to import nothing but ``api``, so these tests do exactly that:
they call the re-exported names and never reach for a service module.  A test that
needed an internal import would be proving the wrong thing.
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

import pytest
from factory_foundation.api import unit_of_work

from factory_blueprint import api, deps
from factory_blueprint.build.service import require_compiler
from factory_blueprint.enums import ChangeLevel, DesignStage, Section
from factory_blueprint.s4.stub import StubDhis2
from factory_blueprint.testing.harness import BlueprintHarness
from tests.integration import test_compile as scenario

PROJECT = scenario.PROJECT


@pytest.fixture(autouse=True)
def _stub(harness: BlueprintHarness) -> Iterator[Any]:
    stub = StubDhis2()
    deps.set_dhis2(stub)
    yield stub
    deps.reset_providers()


def test_version_queries_answer_from_the_index() -> None:
    version = scenario.approved_version()
    with unit_of_work() as session:
        current = api.get_current_version(PROJECT, session=session)
        listed = api.list_versions(PROJECT, page=1, page_size=10, session=session)
        one = api.get_version(PROJECT, version, session=session)
    assert current.version == version
    assert current.content_hash.startswith("sha256:")
    assert current.change_level is ChangeLevel.MINOR
    assert one.version == version
    assert [item.version for item in listed.items] == sorted(
        (row.version for row in listed.items),
        key=lambda text: tuple(int(part) for part in text.split(".")),
        reverse=True,
    )
    assert listed.total == len(listed.items)


def test_project_semantics_never_read_an_artifact() -> None:
    """A Gate screen asks this on every load, so it must be answerable from ``bp``."""
    scenario.approved_version()
    with unit_of_work() as session:
        semantics = api.get_project_semantics(PROJECT, session=session)
    assert semantics.delivery_mode.value == "B"
    assert semantics.default_language == "en"
    assert semantics.translation_languages == ["fr"]
    assert semantics.target_dhis2_version_constraint == ">=2.41 <2.43"
    assert semantics.mapping_frozen, "the scenario was promoted from the Mapping stage"
    assert semantics.promoted_from_stage is DesignStage.MAPPING
    assert not semantics.has_fixture_specifications


def test_get_entries_selects_by_key_decision_and_section() -> None:
    version = scenario.approved_version()
    by_key = api.get_entries(
        PROJECT, api.EntrySelector(business_keys=["pregnancy"], sections=[Section.MAPPING])
    )
    keys = {str(entry.get("businessKey")) for entry in by_key.entries}
    assert "pregnancy" in keys
    assert by_key.version == version
    assert by_key.not_found == []

    missing = api.get_entries(PROJECT, api.EntrySelector(business_keys=["nope"]))
    assert missing.not_found == ["nope"]

    by_decision = api.get_entries(
        PROJECT, api.EntrySelector(decision_ids=["MAP-001"], sections=[Section.REGISTRY])
    )
    assert {str(entry.get("businessKey")) for entry in by_decision.entries} >= {
        "pregnancy",
        "person",
    }
    whole = api.get_entries(PROJECT, api.EntrySelector(sections=[Section.PROJECT]))
    assert any(item["section"] == "project" for item in whole.entries)


def test_get_entries_can_show_the_unapproved_draft() -> None:
    """``source="DRAFT"`` is how a Gate displays what an Agent proposed (FR-AG-001)."""
    scenario.approved_version()
    draft_view = api.EntrySelector(sections=[Section.RULES], source="DRAFT")
    approved_view = api.EntrySelector(sections=[Section.RULES])
    assert api.get_entries(PROJECT, draft_view).entries == []

    _submit_rule_patch(_open_rule_draft())
    assert {str(item.get("businessKey")) for item in api.get_entries(PROJECT, draft_view).entries} == {
        "termrule"
    }
    assert api.get_entries(PROJECT, approved_view).entries == []


def test_registry_scope_bindings_and_pool_read() -> None:
    version = scenario.approved_version()
    with unit_of_work() as session:
        registry = api.get_registry(PROJECT, version, session=session)
        scoped = api.get_managed_scope(PROJECT, version, scenario.TEST_ENV, session=session)
        pool = api.get_uid_pool_status(PROJECT, session=session)
        bindings = api.get_bindings(PROJECT, version, session=session)
    assert {entry.disposition for entry in registry}
    assert all(entry.business_key for entry in registry)
    # only CREATE / UPDATE / DEPRECATED entries are managed (BP-REG-009)
    managed_keys = {entry.business_key for entry in registry if entry.managed}
    assert {entry.business_key for entry in scoped} <= managed_keys
    assert scoped, "the scenario creates objects"
    assert all(entry.uid for entry in scoped), "a managed object has a UID in every environment"
    assert pool.available >= 0
    assert bindings == [], "nothing is bound until an environment compile runs"


def test_set_environment_binding_is_audited_and_creates_no_version() -> None:
    version = scenario.approved_version()
    before = _current()
    with unit_of_work() as session:
        binding = api.set_environment_binding(
            session,
            project_id=PROJECT,
            blueprint_version=version,
            environment_key=scenario.TEST_ENV,
            business_key="facility",
            uid="ouManual001",
            note="code in UAT is legacy, matched by hand",
            resolved_by="architect",
        )
    assert binding.uid == "ouManual001"
    assert binding.pending_reparse is True, "a hand-set binding waits for a compile to confirm it"
    with unit_of_work() as session:
        listed = api.get_bindings(PROJECT, version, scenario.TEST_ENV, session=session)
    assert [item.business_key for item in listed] == ["facility"]
    assert _current() == before, "a human binding must not produce a new version (BP-BR-14)"


def test_analyze_impact_answers_for_the_widest_case() -> None:
    scenario.approved_version()
    impact = api.analyze_impact(PROJECT, ["pregnancy"], description="renaming the program")
    assert Section.REGISTRY in impact.sections
    assert DesignStage.MAPPING in impact.stages
    assert "pregnancy" in impact.business_keys
    assert "METADATA_PACKAGE" in impact.artifact_types
    # a Program is depended upon by nothing in this scenario, so no extra keys appear
    assert impact.page_keys == []


def test_locate_entries_is_the_transaction_safe_half() -> None:
    scenario.approved_version()
    with unit_of_work() as session:
        located = api.locate_entries(
            PROJECT, api.EntrySelector(business_keys=["pregnancy"]), session=session
        )
    assert located
    sections = {section for section, _key, _path in located}
    assert sections <= {section.value for section in Section}


def test_diff_versions_reports_the_object_level_difference() -> None:
    version = scenario.approved_version()
    report = api.diff_versions(PROJECT, "0.1.0", version)
    states = {item.state for item in report.objects}
    assert "ADDED" in states
    keys = {item.business_key for item in report.objects}
    assert "pregnancy" in keys
    assert report.from_version == "0.1.0" and report.to_version == version


def test_not_enabled_milestones_refuse_clearly() -> None:
    """M3 is delivered; only the M5 capabilities stay refused (BP-TD-002)."""
    from factory_blueprint import config

    assert config.COMPILER_PACKAGE in config.settings().enabled_compilers
    assert config.COMPILER_OPENAPI in config.settings().enabled_compilers
    with pytest.raises(api.FeatureNotEnabled):
        require_compiler(config.COMPILER_DATASTORE)

    # enabling a compiler is a settings change, so a deployment can hold any of them back
    config.configure(enabled_compilers=frozenset({config.COMPILER_METADATA}))
    try:
        with pytest.raises(api.FeatureNotEnabled):
            require_compiler(config.COMPILER_OPENAPI)
    finally:
        config.reset()


def _current() -> str:
    with unit_of_work() as session:
        return api.get_current_version(PROJECT, session=session).version


def _open_rule_draft() -> str:
    with unit_of_work() as session:
        draft_id = api.open_draft(PROJECT, DesignStage.MAPPING, session=session)
    api.prepare_draft_base(PROJECT, draft_id)
    return draft_id


def _submit_rule_patch(draft_id: str) -> None:
    operation: dict[str, Any] = {
        "op": "add",
        "path": "/rules/programRules/-",
        "value": {
            "businessKey": "termrule",
            "name": {"default": "Term rule"},
            "programKey": "pregnancy",
            "expression": "#{gestationalAge} > 42",
            "actions": [],
            "source": {"decisionId": "MAP-001"},
        },
    }
    with unit_of_work() as session:
        api.submit_patch(
            PROJECT,
            draft_id,
            api.Patch(
                patch_id="ptch-rule",
                base_version=_current(),
                author=api.PatchAuthor(kind=api.AuthorKind.USER, ref="tester"),
                decision_ids=["MAP-001"],
                operations=[operation],
                rationale="show a proposed rule that no version contains",
            ),
            api.PatchContext(stage=DesignStage.MAPPING),
            idempotency_key="k-rule-draft-read",
            session=session,
        )


def test_the_dependency_graph_reads_out_as_relationships_not_as_nodes() -> None:
    """BP-IMP-004 through ``api``: the stored edges of one version, edges only.

    Nodes are the Registry's job (``get_registry`` above), so a graph read that also copied entry
    fields would give a caller two answers about one object to keep in step.  The version-less call
    has to answer for the current version, because that is what a UI opening "the model" means.
    """
    from tests.integration import test_registry as graph_scenario

    version = graph_scenario._graph_version()
    with unit_of_work() as session:
        edges = api.get_object_relationships(PROJECT, version, session=session)
        registry = api.get_registry(PROJECT, version, session=session)
        default = api.get_object_relationships(PROJECT, session=session)

    assert edges, "the graph scenario wires a domain attribute into the dependency graph"
    known = {entry.business_key for entry in registry}
    assert all(e.from_business_key in known and e.to_business_key in known for e in edges), (
        "an edge may only join objects the Registry of the same version knows"
    )
    assert all(e.field and e.from_dhis2_type and e.to_dhis2_type for e in edges)
    assert {(e.from_business_key, e.to_business_key, e.field) for e in edges} == {
        (e.from_business_key, e.to_business_key, e.field) for e in edges
    }, "the stored key is (from, to, field): the same pair may reference through several fields"
    assert [(e.from_business_key, e.to_business_key, e.field) for e in default] == [
        (e.from_business_key, e.to_business_key, e.field) for e in edges
    ]


def test_the_relationship_read_narrows_to_the_types_it_was_asked_for() -> None:
    """A filtered view keeps an edge when *either* end is in the set (BP-NFR-03).

    Filtering to edges whose two ends are both inside the set would cut a kept object off from the
    objects it reaches, which is the one thing the graph is opened for.
    """
    from tests.integration import test_registry as graph_scenario

    version = graph_scenario._graph_version()
    with unit_of_work() as session:
        everything = api.get_object_relationships(PROJECT, version, session=session)
        kept = sorted({e.from_dhis2_type for e in everything if e.from_dhis2_type})[0]
        narrowed = api.get_object_relationships(PROJECT, version, [kept], session=session)
        unrelated = api.get_object_relationships(PROJECT, version, ["noSuchType"], session=session)
        wrong_kind = api.get_object_relationships(
            PROJECT, version, None, ["implicit"], session=session
        )

    assert narrowed == [e for e in everything if kept in (e.from_dhis2_type, e.to_dhis2_type)]
    assert unrelated == []
    assert wrong_kind == [], "no producer has earned the implicit kind yet, so asking for it answers nothing"
