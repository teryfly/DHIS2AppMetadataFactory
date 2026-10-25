"""Reading a build's products back through the public API (SRS 7.5, BP-AC-16 … 18).

The dependency graph and the development package are artifacts, and S2 owns their
format: BP-TD-009 and BP-HO-004 both say a caller must not parse them itself.  These
tests therefore check the read against the bytes it claims to describe, in the shape
S1 receives it -- including the two reasons a caller is told there is nothing to show
(``CP-UI-022``: ``NOT_GENERATED`` for a resource no build produced yet, ``NOT_EXIST``
for an identifier that names nothing).
"""

from __future__ import annotations

import json
from typing import Any

import pytest
from factory_foundation.api import (
    FactoryError,
    NotFoundError,
    TransactionMisuseError,
    unit_of_work,
)

from factory_blueprint import api, artifacts
from factory_blueprint.s4.stub import StubDhis2
from factory_blueprint.testing.harness import BlueprintHarness
from tests.integration import test_compile as scenario
from tests.integration import test_m3_products as m3
from tests.integration import test_registry as graph_scenario

GRAPH_FILE = "dependency-graph.json"


def _graph_bytes(build: Any, role: str) -> dict[str, Any]:
    """The ``dependency-graph.json`` inside the package that build filed under ``role``."""
    blob, _classification = artifacts.read_bytes(build.artifact_ids[role])
    packed = artifacts.unpack_archive(blob)
    return json.loads(packed[GRAPH_FILE].decode("utf-8"))


def _node_tuple(node: api.GraphNode) -> tuple[Any, ...]:
    return (
        node.business_key,
        node.dhis2_type,
        node.uid,
        node.disposition,
        node.managed,
        node.in_payload,
        node.in_degree,
        node.out_degree,
    )


def _edge_tuple(edge: api.ObjectRelation) -> tuple[Any, ...]:
    return (
        edge.from_business_key,
        edge.to_business_key,
        edge.field,
        edge.from_dhis2_type,
        edge.to_dhis2_type,
        edge.edge_kind,
    )


def _assert_matches_the_file(graph: api.DependencyGraph, document: dict[str, Any]) -> None:
    """The read is a projection of the file, with nothing added and nothing dropped."""
    assert graph.schema_version == document["schemaVersion"]
    assert sorted(map(_node_tuple, graph.nodes)) == sorted(
        (
            row["businessKey"],
            row["dhis2Type"],
            row["uid"],
            row["disposition"],
            row["managed"],
            row["inPayload"],
            row["inDegree"],
            row["outDegree"],
        )
        for row in document["nodes"]
    ), "the nodes are not the file's"
    assert sorted(map(_edge_tuple, graph.edges)) == sorted(
        (
            row["from_business_key"],
            row["to_business_key"],
            row["field"],
            row["from_dhis2_type"],
            row["to_dhis2_type"],
            row["edge_kind"],
        )
        for row in document["edges"]
    ), "the edges are not the file's"
    assert graph.orphans == document["orphans"]
    assert [
        (item.from_business_key, item.field, item.target) for item in graph.unresolved_registry_refs
    ] == [
        (row["from_business_key"], row["field"], row["target"])
        for row in document["unresolvedRegistryRefs"]
    ]


# --- BP-AC-16: the graph of a build --------------------------------------


def test_the_environment_bound_graph_matches_the_package_and_lists_dangling_refs(
    harness: BlueprintHarness,
) -> None:
    """AC-16: nodes and edges agree with the metadata package, unresolved references apart."""
    version = m3.designed_version()
    m3._handoff_inputs(version, StubDhis2())
    build = api.compile_for_environment(
        scenario.PROJECT,
        version,
        scenario.TEST_ENV,
        scenario._discovery_artifact(scenario.TEST_ENV),
        scenario._profile_artifact(),
        None,
        scenario.context(),
        idempotency_key="k-ac16-env",
    )
    assert build.status is api.BuildStatus.COMPLETED, build.binding_issues

    graph = api.get_dependency_graph(build.build_id)
    _assert_matches_the_file(graph, _graph_bytes(build, "METADATA_PACKAGE"))

    assert graph.kind == "ENVIRONMENT"
    assert graph.build_id == build.build_id
    assert graph.project_id == scenario.PROJECT
    assert graph.blueprint_version == version
    assert graph.environment_key == scenario.TEST_ENV
    assert graph.nodes, "a compiled version has Registry entries"
    # a metadata object graph holds Registry entries and nothing else (BP-TD-006)
    assert all(node.business_key for node in graph.nodes)
    # a dangling reference names something the graph has no node for: that is the whole
    # point of listing it separately rather than leaving the edge out (BP-TD-009)
    known = {node.business_key for node in graph.nodes}
    for item in graph.unresolved_registry_refs:
        assert item.from_business_key in known
        assert item.target not in known
    # an orphan is a node whose two degrees are both zero, on the whole graph
    for key in graph.orphans:
        assert key in known
        assert not any(
            key in (edge.from_business_key, edge.to_business_key) for edge in graph.edges
        )
    for node in graph.nodes:
        assert (node.in_degree == 0 and node.out_degree == 0) == (
            node.business_key in graph.orphans
        ), "the degree and the orphan verdict must agree"


def test_the_baseline_graph_is_readable_from_the_same_function(harness: BlueprintHarness) -> None:
    """BP-TD-009 covers both compile kinds, so the same call answers for a baseline."""
    version = m3.designed_version()
    build = api.compile_baseline(
        scenario.PROJECT,
        version,
        scenario._profile_artifact(),
        scenario.context(),
        idempotency_key="k-ac16-baseline",
    )
    assert build.status is api.BuildStatus.COMPLETED, build.validation

    graph = api.get_dependency_graph(build.build_id)
    _assert_matches_the_file(graph, _graph_bytes(build, "METADATA_BASELINE"))
    assert graph.kind == "BASELINE"
    assert graph.environment_key is None
    # a baseline resolves nothing against an environment, so no node carries a bound UID
    assert all(node.uid for node in graph.nodes), "every node was pooled at promote time"


def test_a_filtered_read_keeps_the_full_graph_degrees_and_orphans(harness: BlueprintHarness) -> None:
    """BP-AC-16, second half: the filter trims what comes back, never what was computed."""
    # the compile scenario alone wires nothing together, so the version that has an edge
    # is the one ``test_registry`` builds for exactly that reason
    version = graph_scenario._graph_version()
    build = api.compile_baseline(
        scenario.PROJECT, version, scenario._profile_artifact(), scenario.context(),
        idempotency_key="k-ac16-filter",
    )
    whole = api.get_dependency_graph(build.build_id)
    orphans = set(whole.orphans)
    assert whole.edges and orphans, "the scenario needs both a linked and a lone object"
    linked = {node.business_key for node in whole.nodes if node.business_key not in orphans}

    target = next(node for node in whole.nodes if node.business_key in linked)
    filtered = api.get_dependency_graph(
        build.build_id, api.GraphFilters(dhis2_types=(target.dhis2_type,))
    )
    assert filtered.nodes and all(node.dhis2_type == target.dhis2_type for node in filtered.nodes)
    # a node answers the same question in every view of it: degrees and orphan status are the
    # full graph's, so narrowing to one type cannot make a referenced object look alone
    by_key = {node.business_key: node for node in whole.nodes}
    for node in filtered.nodes:
        assert node == by_key[node.business_key], "the view restated a node it should only trim"
    assert set(filtered.orphans) <= orphans
    # a kept node brings its one-hop edges, and the far end may lie outside the view
    kept = {node.business_key for node in filtered.nodes}
    for edge in filtered.edges:
        assert {edge.from_business_key, edge.to_business_key} & kept

    alone = api.get_dependency_graph(
        build.build_id, api.GraphFilters(business_keys=(whole.orphans[0],))
    )
    assert [node.business_key for node in alone.nodes] == [whole.orphans[0]]
    assert alone.edges == [] and alone.orphans == [whole.orphans[0]]
    # an empty filter set narrows nothing, so the two dimensions can only ever tighten
    assert api.get_dependency_graph(build.build_id, api.GraphFilters()) == whole


# --- BP-AC-17: nothing to read yet ---------------------------------------


def test_a_build_that_produced_no_graph_is_not_generated(harness: BlueprintHarness) -> None:
    version = m3.designed_version()
    _, evidence_id = m3._handoff_inputs(version, StubDhis2())
    handoff = api.generate_frontend_package(
        scenario.PROJECT, version, [scenario.TEST_ENV], evidence_id, scenario.context(),
        idempotency_key="k-ac17-handoff",
    )
    # a handoff build produced a development package and no dependency graph at all, and
    # it still has an artifact id: the reason must be "nothing was generated", not "no build"
    assert "FRONTEND_PACKAGE" in handoff.artifact_ids
    with pytest.raises(NotFoundError) as caught:
        api.get_dependency_graph(handoff.build_id)
    assert caught.value.details["reason"] == "NOT_GENERATED"
    assert caught.value.details["buildId"] == handoff.build_id


def test_a_build_id_nobody_handed_out_is_not_exist(harness: BlueprintHarness) -> None:
    with pytest.raises(NotFoundError) as caught:
        api.get_dependency_graph("bld-01NEVEREXISTED0000000000")
    assert caught.value.details["reason"] == "NOT_EXIST"


# --- BP-HO-004: the development package's files --------------------------


@pytest.fixture
def package_id(harness: BlueprintHarness) -> str:
    version = m3.designed_version()
    _, evidence_id = m3._handoff_inputs(version, StubDhis2())
    result = api.generate_frontend_package(
        scenario.PROJECT, version, [scenario.TEST_ENV], evidence_id, scenario.context(),
        idempotency_key="k-ac18-package",
    )
    assert set(result.artifact_ids) >= {"FRONTEND_PACKAGE", "HANDOFF_CHECK_REPORT"}
    return result.artifact_ids["FRONTEND_PACKAGE"]


def test_the_package_listing_gives_path_kind_size_and_hash(package_id: str) -> None:
    listing = api.list_package_files(package_id)
    assert listing.artifact_id == package_id
    assert listing.project_id == scenario.PROJECT
    assert listing.blueprint_version
    by_path = {item.path: item for item in listing.files}

    # 17.1's tree, described by role rather than by a name pattern the caller guesses
    assert by_path["api/openapi.yaml"].kind == "CONTRACT"
    assert by_path["ui/ui-spec.json"].kind == "UI_SPEC"
    assert by_path[f"pages/{m3.PAGE_KEY}.md"].kind == "UI_SPEC"
    assert by_path["metadata/registry.json"].kind == "REGISTRY"
    assert by_path["metadata/manifest.json"].kind == "DOCUMENT"
    assert by_path["fixtures/demo-data.json"].kind == "FIXTURE"
    assert by_path["dhis2/environment.example.json"].kind == "EXAMPLE"
    assert by_path["traceability/source-map.json"].kind == "TRACEABILITY"
    assert by_path["README.md"].kind == "DOCUMENT"
    assert any(item.startswith("i18n/") for item in by_path)
    assert {item.kind for item in listing.files} <= set(api.PACKAGE_FILE_KINDS)

    # size and hash are the bytes themselves, so the listing is a check, not a claim
    packed = m3._files_of(package_id)
    assert set(by_path) == set(packed)
    for path, entry in by_path.items():
        assert entry.size_bytes == len(packed[path])
        assert entry.content_hash.startswith("sha256:")


def test_a_read_file_hashes_as_the_listing_says(package_id: str) -> None:
    listing = {item.path: item for item in api.list_package_files(package_id).files}
    readme = api.read_package_file(package_id, "README.md")
    assert readme.content == packed_readme(package_id)
    assert readme.media_type == "text/markdown"
    assert (readme.size_bytes, readme.content_hash, readme.kind) == (
        listing["README.md"].size_bytes,
        listing["README.md"].content_hash,
        listing["README.md"].kind,
    )
    assert json.loads(api.read_package_file(package_id, "metadata/registry.json").content)
    assert api.read_package_file(package_id, "api/openapi.yaml").media_type == "application/yaml"


def packed_readme(package_id: str) -> str:
    return m3._files_of(package_id)["README.md"].decode("utf-8")


def test_an_artifact_that_does_not_exist_is_not_exist(harness: BlueprintHarness) -> None:
    """BP-HO-004.1: an unknown id is a thing that is not there (``NOT_GENERATED`` is S1's call)."""
    with pytest.raises(NotFoundError) as caught:
        api.list_package_files("art-01NEVEREXISTED0000000000")
    assert caught.value.details["reason"] == "NOT_EXIST"


def test_a_commit_package_handed_to_the_package_read_is_a_type_mismatch(
    harness: BlueprintHarness,
) -> None:
    """BP-AC-19: the wrong type is the caller's error, not a resource awaiting a stage."""
    version = m3.designed_version()
    build = api.compile_for_environment(
        scenario.PROJECT, version, scenario.TEST_ENV,
        scenario._discovery_artifact(scenario.TEST_ENV), scenario._profile_artifact(),
        None, scenario.context(), idempotency_key="k-ac19",
    )
    metadata_package = build.artifact_ids["METADATA_PACKAGE"]
    for call in (
        lambda: api.list_package_files(metadata_package),
        lambda: api.read_package_file(metadata_package, "metadata.json"),
    ):
        with pytest.raises(FactoryError) as caught:
            call()
        assert caught.value.error_code == "ARTIFACT_TYPE_MISMATCH"
        assert caught.value.details["expectedType"] == "FRONTEND_PACKAGE"
        assert caught.value.details["actualType"] == "METADATA_PACKAGE"


def test_a_crafted_path_is_indistinguishable_from_an_absent_one(package_id: str) -> None:
    """BP-AC-20: every refused path answers the way a legal path that is not there does."""
    absent = "metadata/ghost.json"
    with pytest.raises(NotFoundError) as reference:
        api.read_package_file(package_id, absent)

    for path in (
        "../secret.md",
        "metadata/../../etc/passwd",
        "./README.md",
        "",
        "/README.md",
        "README.md#frag",
        "dhis2\\debugging.md",
        "i18n/en%2F.json",
        "README\x00.md",
        "README\n.md",
        "api/openapi.yaml\x7f",
    ):
        with pytest.raises(NotFoundError) as caught:
            api.read_package_file(package_id, path)
        assert caught.value.details["reason"] == "NOT_EXIST"
        assert caught.value.details["path"] == path
        # the same code, the same detail keys: nothing about the layout is leaked
        assert caught.value.details.keys() == reference.value.details.keys()


# --- the transaction boundary is the declaration (00 doc 6.2) ------------


def test_the_three_reads_refuse_to_run_inside_a_unit_of_work(package_id: str) -> None:
    """They read artifact content, so an enclosing transaction is a programming error."""
    with unit_of_work():
        for call in (
            lambda: api.get_dependency_graph("bld-01ANY000000000000000000"),
            lambda: api.list_package_files(package_id),
            lambda: api.read_package_file(package_id, "README.md"),
        ):
            with pytest.raises(TransactionMisuseError):
                call()
