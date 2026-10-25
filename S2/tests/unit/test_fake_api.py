"""The in-memory Fake API has to cover the real one (BP-API-001).

S1 and S3 are told to develop against ``factory_blueprint.api`` with
``factory_blueprint.testing`` as their double.  That only works if the double answers
every public function, so this module checks the coverage mechanically and then walks
one full design cycle through it.
"""

from __future__ import annotations

import inspect
from typing import Any

import pytest
from factory_foundation.api import ArtifactError, FactoryError

from factory_blueprint import api
from factory_blueprint.dto.blueprint import InitialProjectInfo
from factory_blueprint.dto.build import BuildResult
from factory_blueprint.dto.patch import Patch, PatchAuthor, PatchContext
from factory_blueprint.dto.portability import ImportResult
from factory_blueprint.enums import (
    AuthorKind,
    BuildKind,
    BuildStatus,
    DeliveryMode,
    DesignStage,
    PatchStatus,
    ProjectType,
    Section,
)
from factory_blueprint.errors import UidPoolExhausted
from factory_blueprint.testing import FakeBlueprintApi, fake_issue
from factory_blueprint.testing.fakes import DEFAULT_BUILD_AT

#: a build context for the double's compile calls, with the date the Fake itself answers with
_CONTEXT = api.BuildContext(built_at=DEFAULT_BUILD_AT, seed=1, requester="tester")

#: the three names of chapter 7.5/7.6 that a later milestone delivers, so the Fake
#: answers them the way the real API does: by refusing
# M3 is delivered, so the Fake covers these too (BP-API-001 leaves no exemption)
NOT_ENABLED: set[str] = set()


def public_api_functions() -> set[str]:
    return {
        name
        for name in api.__all__
        if callable(getattr(api, name, None))
        and not inspect.isclass(getattr(api, name))
        and not name.startswith("_")
        and name not in {"initialize", "configure", "settings", "register_migration"}
        # the Fake is a stand-in for the service functions; the SRS chapter 7 table is
        # what it has to cover, and these helpers are chapter 7.1 schema data reads
        and name not in {"set_dhis2", "get_dhis2", "reset_providers"}
    }


def test_the_fake_implements_every_public_function() -> None:
    missing = sorted(
        name
        for name in public_api_functions() - NOT_ENABLED
        if not callable(getattr(FakeBlueprintApi, name, None))
    )
    assert missing == [], f"the Fake is missing {missing}"


def test_a_caller_can_run_a_whole_design_cycle_without_a_database() -> None:
    fake = FakeBlueprintApi()
    info = InitialProjectInfo(
        delivery_mode=DeliveryMode.B,
        project_type=ProjectType.BROWNFIELD,
        target_dhis2_version_constraint=">=2.41",
        default_language="en",
    )
    handle = fake.prepare_initial_blueprint("p1", info)
    first = fake.register_initial_blueprint("p1", handle)
    assert first.version == "0.1.0"

    draft_id = fake.open_draft("p1", DesignStage.MAPPING)
    assert fake.open_draft("p1", DesignStage.MAPPING) == draft_id, "one active draft per project"
    fake.prepare_draft_base("p1", draft_id)
    outcome = fake.submit_patch(
        "p1",
        draft_id,
        Patch(
            patch_id="ptch-1",
            base_version="0.1.0",
            author=PatchAuthor(kind=AuthorKind.AGENT, ref="run-1"),
            decision_ids=["MAP-001"],
            operations=[
                {
                    "op": "add",
                    "path": "/registry/entries/-",
                    "value": {
                        "businessKey": "pregnancy",
                        "dhis2Type": "program",
                        "disposition": "CREATE",
                    },
                }
            ],
            rationale="one program",
        ),
        PatchContext(stage=DesignStage.MAPPING),
    )
    assert outcome.status is PatchStatus.PROPOSED
    assert outcome.assigned_uids == {"pregnancy": "fake0000000"}

    frozen = fake.freeze_draft(draft_id, ["DEV"])
    assert frozen.promotable
    promoted = fake.promote_draft(draft_id, frozen.draft_hash, idempotency_key="k")
    assert promoted.version == "0.2.0"
    assert [stage for stage in promoted.affected_stages] == [DesignStage.MAPPING]

    build = fake.compile_for_environment(
        "p1", promoted.version, "DEV", "art-discovery", "art-profile", None, idempotency_key="k"
    )
    assert build.status is BuildStatus.COMPLETED
    assert "METADATA_PACKAGE" in build.artifact_ids
    assert fake.reproduce(build.artifact_ids["MANIFEST"]).consistent

    exported = fake.export_blueprint("p1")
    preview = fake.prepare_import(exported.artifact_id, {"art-fake": "art-new"})
    imported = fake.commit_import("p2", preview.batch_id, idempotency_key="k")
    assert isinstance(imported, ImportResult) and imported.current_version

    assert fake.delete_project_data("p1").deleted
    assert fake.calls[0][0] == "prepare_initial_blueprint"
    assert [name for name, _args in fake.calls][-1] == "delete_project_data"


def test_a_preset_problem_reaches_the_caller_unchanged() -> None:
    """The point of the Fake: show an answer the real S2 would take a database to produce."""
    fake = FakeBlueprintApi()
    fake.seed("p1")
    fake.issues = [
        fake_issue("BPV-BIND-001", "no categoryCombo in DEV matches 'default'"),
        fake_issue("BPV-ST-004", "an expression references an unknown key"),
    ]
    draft_id = fake.open_draft("p1", DesignStage.MAPPING)
    assert fake.freeze_draft(draft_id, ["DEV"]).promotable is False
    build: BuildResult = fake.compile_for_environment(
        "p1", "0.1.0", "DEV", "art-d", "art-p", None, idempotency_key="k"
    )
    assert build.status is BuildStatus.FAILED
    assert build.validation.blocking_count == 2
    assert [issue.rule_id for issue in build.binding_issues] == ["BPV-BIND-001"]
    assert fake.get_entries("p1", api.EntrySelector(sections=[Section.REGISTRY])).not_found == []


def test_an_empty_pool_raises_the_retryable_error() -> None:
    fake = FakeBlueprintApi()
    fake.seed("p1")
    fake.pool_size = 0
    fake.fail_compile_with = "the UID pool is empty"
    with pytest.raises(UidPoolExhausted):
        fake.compile_baseline("p1", "0.1.0", None, idempotency_key="k")
    fake.fail_compile_with = None
    assert fake.replenish_uid_pool("p1", "DEV", min_available=3) == 3
    assert fake.get_uid_pool_status("p1").available == 3


def test_unknown_ids_are_not_found_rather_than_invented() -> None:
    from factory_foundation.api import NotFoundError

    fake = FakeBlueprintApi()
    with pytest.raises(NotFoundError):
        fake.get_current_version("nope")
    with pytest.raises(NotFoundError):
        fake.get_draft("dft-nope")
    with pytest.raises(NotFoundError):
        fake.reset_draft("dft-nope")


def test_an_impact_answer_can_be_preset() -> None:
    fake = FakeBlueprintApi()
    fake.seed("p1")
    assert api_impact_keys(fake.analyze_impact("p1", ["a", "b"])) == ["a", "b"]
    fake.impact = api.ImpactSet(sections=[Section.PAGES], business_keys=["preset"])
    assert fake.analyze_impact("p1", ["a"]).business_keys == ["preset"]


def api_impact_keys(result: Any) -> list[str]:
    return list(result.business_keys)


# --- the product reads: a build's graph and a package's files -------------


def _preset_graph() -> api.DependencyGraph:
    return api.DependencyGraph(
        build_id="preset",
        project_id="p1",
        blueprint_version="0.1.0",
        kind="BASELINE",
        nodes=[
            api.GraphNode(
                business_key="program", dhis2_type="program", managed=True, out_degree=1
            ),
            api.GraphNode(
                business_key="sex", dhis2_type="optionSet", managed=True, in_degree=1
            ),
            api.GraphNode(
                business_key="unreferenced", dhis2_type="categoryOption", managed=False
            ),
        ],
        edges=[
            api.ObjectRelation(from_business_key="program", to_business_key="sex", field="optionSet")
        ],
        orphans=["unreferenced"],
        unresolved_registry_refs=[
            api.UnresolvedGraphReference(
                from_business_key="program", field="categoryCombo", target="nowhere"
            )
        ],
    )


def test_a_build_with_no_preset_graph_reports_not_generated() -> None:
    """BP-AC-17 against the double: nothing preset means nothing generated."""
    from factory_foundation.api import NotFoundError

    fake = FakeBlueprintApi()
    fake.seed("p1")
    build = fake.compile_baseline("p1", "0.1.0", None, idempotency_key="k")
    with pytest.raises(NotFoundError) as caught:
        fake.get_dependency_graph(build.build_id)
    assert caught.value.details["reason"] == "NOT_GENERATED"
    assert caught.value.details["buildId"] == build.build_id

    fake.graphs[build.build_id] = _preset_graph().model_copy(update={"build_id": build.build_id})
    assert fake.get_dependency_graph(build.build_id).nodes == _preset_graph().nodes


def test_the_fake_tells_an_unknown_build_from_an_unproduced_one() -> None:
    from factory_foundation.api import NotFoundError

    fake = FakeBlueprintApi()
    fake.seed("p1")
    fake.graphs["bld-never-handed-out"] = _preset_graph()
    with pytest.raises(NotFoundError) as caught:
        fake.get_dependency_graph("bld-never-handed-out")
    assert caught.value.details["reason"] == "NOT_EXIST"


def test_the_fake_narrows_the_graph_exactly_like_production() -> None:
    from factory_blueprint.artifact_reads import narrow_graph

    fake = FakeBlueprintApi()
    whole = _preset_graph().model_copy(update={"build_id": "b1"})
    fake.graphs["b1"] = whole
    fake.builds["b1"] = _a_build()

    filtered = fake.get_dependency_graph("b1", api.GraphFilters(dhis2_types=("optionSet",)))
    assert [node.business_key for node in filtered.nodes] == ["sex"]
    assert [edge.from_business_key for edge in filtered.edges] == ["program"], "either end keeps it"
    assert filtered.unresolved_registry_refs == [], "its source is not in the view"
    assert filtered == narrow_graph(whole, api.GraphFilters(dhis2_types=("optionSet",)))


def test_the_fake_lists_and_reads_preset_package_files() -> None:
    from factory_foundation.api import NotFoundError

    fake = FakeBlueprintApi()
    fake.seed("p1")
    result = fake.generate_frontend_package(
        "p1", "0.1.0", ["DEV"], "art-evidence", _CONTEXT, idempotency_key="k"
    )
    package_id = result.artifact_ids["FRONTEND_PACKAGE"]
    # the package exists but nobody described it, so the honest answer is an empty listing
    assert fake.list_package_files(package_id).files == []
    # an id the Fake never handed out does not exist (BP-HO-004.1); whether a project has a
    # package at all is S1's judgement, so neither the Fake nor S2 answers NOT_GENERATED here
    with pytest.raises(NotFoundError) as caught:
        fake.list_package_files("art-unknown")
    assert caught.value.details["reason"] == "NOT_EXIST"
    # the wrong artifact type is the caller's error, and S1 maps it to a server failure
    fake.non_package_artifacts["art-package-metadata"] = "METADATA_PACKAGE"
    with pytest.raises(FactoryError) as caught:
        fake.list_package_files("art-package-metadata")
    assert caught.value.error_code == "ARTIFACT_TYPE_MISMATCH"
    assert caught.value.details["expectedType"] == "FRONTEND_PACKAGE"
    assert caught.value.details["actualType"] == "METADATA_PACKAGE"

    fake.packages[package_id].files = {
        "README.md": b"# Pregnancy\n",
        "api/openapi.yaml": b"openapi: 3.1.0\n",
        "metadata/registry.json": b'{"entries": []}\n',
    }
    listing = fake.list_package_files(package_id)
    assert [(item.path, item.kind) for item in listing.files] == [
        ("README.md", "DOCUMENT"),
        ("api/openapi.yaml", "CONTRACT"),
        ("metadata/registry.json", "REGISTRY"),
    ]
    assert listing.files[0].size_bytes == len(b"# Pregnancy\n")
    for entry in listing.files:
        read = fake.read_package_file(package_id, entry.path)
        assert (read.size_bytes, read.content_hash, read.kind) == (
            entry.size_bytes,
            entry.content_hash,
            entry.kind,
        ), "BP-AC-18: the content agrees with the listing"

    for path in ("../secret", "", "README.md#x", "nope.md"):
        with pytest.raises(NotFoundError) as caught:
            fake.read_package_file(package_id, path)
        assert caught.value.details["reason"] == "NOT_EXIST"

    # a file that is not text is reported, never silently returned as an empty string
    fake.packages[package_id].files["blob.bin"] = b"\xff\xfe\x00"
    with pytest.raises(ArtifactError):
        fake.read_package_file(package_id, "blob.bin")


def _a_build() -> BuildResult:
    return BuildResult(
        build_id="b1",
        kind=BuildKind.BASELINE,
        project_id="p1",
        version="0.1.0",
        status=BuildStatus.COMPLETED,
    )
