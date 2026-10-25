"""CP-UI-020~023 at the wire: the graph, the dev package, the guided 404, the M4 gate.

The S0 artifact store and the real error envelope throughout; S2's three new reads
are the transitional port methods served by the testing fake (CP-ASM-08) — exactly
the harness the completion criteria name ("真实 S0、S2 Fake").  Every refusal of a
not-yet-produced resource is pinned as the CP-UI-022 404, never a 200 shell.
"""

from __future__ import annotations

from typing import Any

import pytest
from fastapi.testclient import TestClient

from factory_control.ports import dto
from factory_control.testing.fakes import register_fake_artifact
from factory_control.testing.harness import seed_project
from tests.helpers import create_run, drive_run, finish_design_line  # type: ignore[import]

pytestmark = pytest.mark.api

OPERATOR = {"X-Operator": "ada"}


def _preset_graph(harness: Any, build_id: str, project_id: str) -> dto.DependencyGraph:
    """A graph the fake serves whole: two chained objects, one orphan, one dangling reference.

    S2's own answer shape (BP-TD-009 V1.1.3): nodes are Registry entries with the
    complete graph's degrees — S1 must forward it, not rebuild it (CP-AC-33).
    """
    graph = dto.DependencyGraph(
        build_id=build_id,
        project_id=project_id,
        blueprint_version="0.1.0",
        kind="BASELINE",
        nodes=[
            dto.GraphNode(business_key="weight", dhis2_type="dataElement", disposition="NEW", in_degree=0, out_degree=1),
            dto.GraphNode(business_key="ANC Tally", dhis2_type="indicator", uid="i1", in_degree=1, out_degree=0),
            dto.GraphNode(business_key="legacy", dhis2_type="dataElement", uid="l1", in_degree=0, out_degree=0),
        ],
        edges=[
            dto.ObjectRelation(
                from_business_key="weight",
                to_business_key="ANC Tally",
                field="numerator",
                from_dhis2_type="dataElement",
                to_dhis2_type="indicator",
            )
        ],
        orphans=["legacy"],
        unresolved_registry_refs=[
            dto.UnresolvedGraphReference(
                from_business_key="ANC Tally", field="organisationUnitLevel", target="level:missing"
            )
        ],
    )
    harness.blueprint.dependency_graphs[build_id] = graph
    return graph


# --- CP-UI-020: the dependency graph ---------------------------------------


def test_graph_reads_the_build_recorded_by_the_baseline_compile(
    harness: Any, client: TestClient, seeded_env: dict[str, Any]
) -> None:
    """CP-UI-020 without runId: the build is the one S1 recorded on the baseline StageRun.

    This is why the baseline step now persists ``build_ids`` like BIND_COMPILE always did —
    the event payload alone could not answer "which build does this workflow's graph show".
    """
    pid = seeded_env["id"]
    wid = seeded_env["workflowId"]
    finish_design_line(client, harness, seeded_env)
    baseline_build = f"bld-{pid}-baseline"
    _preset_graph(harness, baseline_build, pid)

    response = client.get(f"/api/v1/workflows/{wid}/object-graph", headers=OPERATOR)

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["workflowId"] == wid
    assert body["runId"] is None
    assert body["buildId"] == baseline_build, "the recorded baseline build, not a guessed one"
    assert "get_dependency_graph" in harness.blueprint.calls


def test_graph_with_run_id_reads_that_runs_environment_bound_build(
    harness: Any, client: TestClient, seeded_env: dict[str, Any]
) -> None:
    """CP-UI-020 with runId: the environment-bound compile of *that* Run, recorded on its stage."""
    pid = seeded_env["id"]
    wid = seeded_env["workflowId"]
    run_id = create_run(client, harness, seeded_env)
    drive_run(client, harness, run_id, stop_at_deploy_gate=True)
    env_build = f"bld-{pid}-dev"
    _preset_graph(harness, env_build, pid)
    _preset_graph(harness, f"bld-{pid}-baseline", pid)

    body = client.get(f"/api/v1/workflows/{wid}/object-graph?runId={run_id}", headers=OPERATOR).json()

    assert body["runId"] == run_id
    assert body["buildId"] == env_build, "the Run's build wins over the newer-file baseline default"


def test_cp_ac_31_the_graph_before_any_baseline_compile_is_a_guided_404(
    client: TestClient, seeded_env: dict[str, Any]
) -> None:
    """CP-AC-31: 404 NOT_FOUND, details.reason=NOT_GENERATED, requiredStage=BASELINE_COMPILE."""
    response = client.get(
        f"/api/v1/workflows/{seeded_env['workflowId']}/object-graph", headers=OPERATOR
    )

    assert response.status_code == 404, response.text
    body = response.json()
    assert body["errorCode"] == "NOT_FOUND"
    assert body["details"]["reason"] == "NOT_GENERATED"
    assert body["details"]["requiredStage"] == "BASELINE_COMPILE"


def test_an_unknown_run_id_answers_not_exist_not_not_generated(
    harness: Any, client: TestClient, seeded_env: dict[str, Any]
) -> None:
    """CP-UI-022's second branch: the id names nothing that exists (NOT_EXIST), echoed back."""
    finish_design_line(client, harness, seeded_env)

    response = client.get(
        f"/api/v1/workflows/{seeded_env['workflowId']}/object-graph?runId=run-does-not-exist",
        headers=OPERATOR,
    )

    assert response.status_code == 404, response.text
    body = response.json()
    assert body["details"]["reason"] == "NOT_EXIST"


def test_a_run_of_another_workflow_is_not_exist(
    harness: Any, client: TestClient, seeded_env: dict[str, Any]
) -> None:
    """The run exists — just not for this workflow; that is no less "does not exist" here."""
    other = seed_project(harness, name="other dashboard")
    finish_design_line(client, harness, other)
    other_run = create_run(client, harness, other)

    response = client.get(
        f"/api/v1/workflows/{seeded_env['workflowId']}/object-graph?runId={other_run}",
        headers=OPERATOR,
    )

    assert response.status_code == 404, response.text
    assert response.json()["details"]["reason"] == "NOT_EXIST"


def test_the_graph_is_forwarded_whole_and_narrows_by_the_two_filters(
    harness: Any, client: TestClient, seeded_env: dict[str, Any]
) -> None:
    """S1 assembles nothing: the answer is S2's graph field-for-field (CP-AC-33), and the
    filter follows S2's rule — dimensions AND, members any; an edge survives when either
    end does, so its far end may lie outside the view, while degrees and orphans never move.
    """
    pid = seeded_env["id"]
    finish_design_line(client, harness, seeded_env)
    baseline_build = f"bld-{pid}-baseline"
    _preset_graph(harness, baseline_build, pid)

    body = client.get(
        f"/api/v1/workflows/{seeded_env['workflowId']}/object-graph", headers=OPERATOR
    ).json()

    assert set(body) == {
        "workflowId", "runId", "filters",
        "buildId", "projectId", "blueprintVersion", "kind", "environmentKey", "schemaVersion",
        "nodes", "edges", "orphans", "unresolvedRegistryRefs",
    }, "exactly S2's fields plus the request echo — a renamed or invented field would surface here"
    assert {node["businessKey"] for node in body["nodes"]} == {"weight", "ANC Tally", "legacy"}
    assert set(body["nodes"][0]) == {
        "businessKey", "dhis2Type", "uid", "disposition", "managed", "inPayload",
        "inDegree", "outDegree",
    }
    assert set(body["edges"][0]) == {
        "fromBusinessKey", "toBusinessKey", "field", "fromDhis2Type", "toDhis2Type", "edgeKind"
    }
    assert body["orphans"] == ["legacy"]
    assert body["unresolvedRegistryRefs"] == [
        {"fromBusinessKey": "ANC Tally", "field": "organisationUnitLevel", "target": "level:missing"}
    ]
    for banned in ("counts", "truncated", "matched", "view", "orphan", "isolatedNodes", "unresolvedReferences"):
        assert banned not in body
    assert body["filters"] == {"dhis2Types": [], "businessKeys": []}

    filtered = client.get(
        f"/api/v1/workflows/{seeded_env['workflowId']}/object-graph?dhis2Type=indicator",
        headers=OPERATOR,
    ).json()
    kept = {node["businessKey"] for node in filtered["nodes"]}
    assert kept == {"ANC Tally"}
    assert filtered["edges"] != [], "the edge is kept because one end is — its far end may be outside the view"
    assert filtered["orphans"] == ["legacy"], "narrowing trims the answer, it never recomputes the graph (BP-TD-009)"
    assert filtered["filters"]["dhis2Types"] == ["indicator"]

    by_key = client.get(
        f"/api/v1/workflows/{seeded_env['workflowId']}/object-graph?businessKey=weight&businessKey=legacy",
        headers=OPERATOR,
    ).json()
    assert {node["businessKey"] for node in by_key["nodes"]} == {"weight", "legacy"}
    assert by_key["filters"]["businessKeys"] == ["weight", "legacy"]


def test_cp_ac_33_the_three_ways_of_naming_the_build_answer_distinctly(
    harness: Any, client: TestClient, seeded_env: dict[str, Any]
) -> None:
    """CP-AC-33: omitted → current version's baseline build; blueprintVersion → that version's;
    runId → the Run's environment-bound build; both at once → 422."""
    pid = seeded_env["id"]
    wid = seeded_env["workflowId"]
    run_id = create_run(client, harness, seeded_env)
    drive_run(client, harness, run_id, stop_at_deploy_gate=True)
    _preset_graph(harness, f"bld-{pid}-baseline", pid)
    _preset_graph(harness, f"bld-{pid}-dev", pid)
    current_version = client.get(f"/api/v1/projects/{pid}", headers=OPERATOR).json()["currentBlueprintVersion"]

    omitted = client.get(f"/api/v1/workflows/{wid}/object-graph", headers=OPERATOR).json()
    by_version = client.get(
        f"/api/v1/workflows/{wid}/object-graph?blueprintVersion={current_version}", headers=OPERATOR
    ).json()
    by_run = client.get(f"/api/v1/workflows/{wid}/object-graph?runId={run_id}", headers=OPERATOR).json()

    assert omitted["buildId"] == f"bld-{pid}-baseline"
    assert by_version["buildId"] == f"bld-{pid}-baseline", "the current version's latest baseline compile"
    assert by_run["buildId"] == f"bld-{pid}-dev", "the Run's environment-bound compile"
    assert by_run["runId"] == run_id and by_version["runId"] is None

    both = client.get(
        f"/api/v1/workflows/{wid}/object-graph?runId={run_id}&blueprintVersion={current_version}",
        headers=OPERATOR,
    )
    assert both.status_code == 422, both.text
    assert both.json()["errorCode"] == "VALIDATION_ERROR"

    other_version = client.get(
        f"/api/v1/workflows/{wid}/object-graph?blueprintVersion=9.9.9", headers=OPERATOR
    )
    assert other_version.status_code == 404, other_version.text
    details = other_version.json()["details"]
    assert details["reason"] == "NOT_GENERATED" and details["requiredStage"] == "BASELINE_COMPILE"
    assert details["blueprintVersion"] == "9.9.9"


def test_the_deprecated_project_alias_answers_the_new_shape(
    harness: Any, client: TestClient, seeded_env: dict[str, Any]
) -> None:
    """S5IF-BP16 keeps its path as a deprecated thin forwarder — same service, new body.

    Old parameters are tolerated and ignored (the build is the workflow's answer, not the
    caller's guess); the document marks the operation deprecated so a generated client sees it.
    """
    pid = seeded_env["id"]
    finish_design_line(client, harness, seeded_env)
    _preset_graph(harness, f"bld-{pid}-baseline", pid)

    response = client.get(
        f"/api/v1/projects/{pid}/object-graph?view=mapping&environmentKey=dev&q=weight&limit=2",
        headers=OPERATOR,
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["workflowId"] == seeded_env["workflowId"]
    assert "counts" not in body and "truncated" not in body

    document = client.get("/api/v1/openapi.json").json()
    alias = document["paths"]["/api/v1/projects/{project_id}/object-graph"]["get"]
    assert alias["deprecated"] is True
    assert "CP-UI-020" in alias["x-requirements"]


# --- CP-UI-021: the development package -------------------------------------


def _preset_package(harness: Any, project_id: str, *, blueprint_version: str = "0.1.0") -> str:
    """A real S0 FRONTEND_PACKAGE artifact plus the listing and contents S2 would serve.

    ``blueprint_version`` is what the listing answers for; CP-UI-021 only accepts the
    package that matches the project's current version.
    """
    artifact_id = register_fake_artifact(project_id, "FRONTEND_PACKAGE", f"pkg-{blueprint_version}")
    harness.blueprint.package_file_listings[artifact_id] = dto.PackageFileListing(
        artifact_id=artifact_id,
        project_id=project_id,
        blueprint_version=blueprint_version,
        files=[
            dto.PackageFileEntry(path="README.md", kind="DOCUMENT", size_bytes=4, content_hash="sha256:r"),
            dto.PackageFileEntry(path="api/openapi.yaml", kind="CONTRACT", size_bytes=9, content_hash="sha256:o"),
        ],
    )
    harness.blueprint.package_file_contents[(artifact_id, "README.md")] = dto.PackageFileContent(
        path="README.md", content="# hi", media_type="text/markdown", size_bytes=4, content_hash="sha256:r"
    )
    harness.blueprint.package_file_contents[(artifact_id, "api/openapi.yaml")] = dto.PackageFileContent(
        path="api/openapi.yaml", content="openapi: 3.1.0", media_type="application/yaml",
        size_bytes=14, content_hash="sha256:o",
    )
    return artifact_id


def test_the_package_listing_and_a_single_file_forward_to_s2(
    harness: Any, client: TestClient, seeded_env: dict[str, Any]
) -> None:
    pid = seeded_env["id"]
    artifact_id = _preset_package(harness, pid)

    listing = client.get(f"/api/v1/projects/{pid}/frontend-package/files", headers=OPERATOR)
    assert listing.status_code == 200, listing.text
    body = listing.json()
    assert body["artifactId"] == artifact_id
    assert [item["path"] for item in body["files"]] == ["README.md", "api/openapi.yaml"]
    assert body["files"][1]["kind"] == "CONTRACT", "the dev-package kind is S2's word (BP-HO-004)"
    assert "list_package_files" in harness.blueprint.calls

    readme = client.get(f"/api/v1/projects/{pid}/frontend-package/files/README.md", headers=OPERATOR)
    assert readme.status_code == 200, readme.text
    assert readme.text == "# hi"
    assert readme.headers["X-Data-Classification"] == "INTERNAL", "the package's own classification (CP-UI-016 rule)"
    assert "Content-Disposition" not in readme.headers, "no download asked — inline is the default"

    nested = client.get(
        f"/api/v1/projects/{pid}/frontend-package/files/api/openapi.yaml", headers=OPERATOR
    )
    assert nested.status_code == 200, nested.text
    assert nested.text == "openapi: 3.1.0"


def test_the_package_file_downloads_under_the_cp_ui_016_rule(
    harness: Any, client: TestClient, seeded_env: dict[str, Any]
) -> None:
    """``download=true`` names the file type-artifactId.extension, the extension from the path."""
    pid = seeded_env["id"]
    artifact_id = _preset_package(harness, pid)

    response = client.get(
        f"/api/v1/projects/{pid}/frontend-package/files/api/openapi.yaml?download=true",
        headers=OPERATOR,
    )

    assert response.status_code == 200, response.text
    disposition = response.headers["Content-Disposition"]
    assert f"FRONTEND_PACKAGE-{artifact_id}.yaml" in disposition


def test_the_package_reads_are_a_guided_404_until_handoff_delivers(
    client: TestClient, seeded_env: dict[str, Any]
) -> None:
    """MVP has no FRONTEND_PACKAGE (HANDOFF is M3): CP-UI-022's NOT_GENERATED with requiredStage."""
    pid = seeded_env["id"]

    for path in (f"/api/v1/projects/{pid}/frontend-package/files", f"/api/v1/projects/{pid}/frontend-package/files/README.md"):
        response = client.get(path, headers=OPERATOR)
        assert response.status_code == 404, f"{path}: {response.text}"
        body = response.json()
        assert body["errorCode"] == "NOT_FOUND"
        assert body["details"]["reason"] == "NOT_GENERATED"
        assert body["details"]["requiredStage"] == "HANDOFF"


def test_a_file_outside_the_listing_is_not_exist(
    harness: Any, client: TestClient, seeded_env: dict[str, Any]
) -> None:
    """Membership answers from S2's listing: a path it does not carry truly does not exist."""
    pid = seeded_env["id"]
    _preset_package(harness, pid)

    response = client.get(f"/api/v1/projects/{pid}/frontend-package/files/nope.txt", headers=OPERATOR)

    assert response.status_code == 404, response.text
    assert response.json()["details"]["reason"] == "NOT_EXIST"


def test_a_refused_path_is_indistinguishable_from_a_missing_one(
    harness: Any, client: TestClient, seeded_env: dict[str, Any]
) -> None:
    """BP-HO-004 item 4: traversal-shaped paths answer NOT_EXIST like any missing file.

    S1 forwards that verdict unchanged — the point is that a caller cannot map the
    package's interior by probing it.
    """
    pid = seeded_env["id"]
    _preset_package(harness, pid)

    for probe in ("a%5Cb", "README%23.md"):
        response = client.get(f"/api/v1/projects/{pid}/frontend-package/files/{probe}", headers=OPERATOR)
        assert response.status_code == 404, response.text
        assert response.json()["details"]["reason"] == "NOT_EXIST"


def test_a_package_answering_for_another_version_is_not_generated(
    harness: Any, client: TestClient, seeded_env: dict[str, Any]
) -> None:
    """CP-UI-021: the package must answer for the *current* version — yesterday's listing
    wearing today's label would claim a delivery this version never had."""
    pid = seeded_env["id"]
    _preset_package(harness, pid, blueprint_version="9.9.9")

    response = client.get(f"/api/v1/projects/{pid}/frontend-package/files", headers=OPERATOR)

    assert response.status_code == 404, response.text
    body = response.json()
    assert body["details"]["reason"] == "NOT_GENERATED"
    assert body["details"]["requiredStage"] == "HANDOFF"


def test_a_type_mismatch_from_s2_is_an_s1_defect_answered_as_500(
    harness: Any, client: TestClient, seeded_env: dict[str, Any], caplog: pytest.LogCaptureFixture
) -> None:
    """CP-UI-021: S2's ARTIFACT_TYPE_MISMATCH means S1 handed it the wrong artifact id.

    It is the caller's (S1's) error, never a missing resource: the envelope answers 500
    ``INTERNAL_ERROR`` with the provider code kept in ``details``, and the resolution
    failure is logged as a warning so the drift is noticed without a user report.
    """
    from factory_control.foundation import FactoryError

    pid = seeded_env["id"]
    artifact_id = register_fake_artifact(pid, "FRONTEND_PACKAGE", "pkg-mismatch")
    harness.blueprint.package_file_listings[artifact_id] = dto.PackageFileListing(
        artifact_id=artifact_id, project_id=pid, blueprint_version="0.1.0", files=[]
    )

    def _mismatch(_artifact_id: str) -> dto.PackageFileListing:
        # Simulates the ruled case (CP-UI-021 / BP-HO-004 item 2): the id S1 forwarded
        # resolves at S2 to an artifact of another type — S1's own resolution is wrong.
        raise FactoryError(
            "artifact is not a FRONTEND_PACKAGE",
            code="ARTIFACT_TYPE_MISMATCH",
            details={"expectedType": "FRONTEND_PACKAGE", "actualType": "METADATA_PACKAGE"},
        )

    harness.blueprint.list_package_files = _mismatch  # type: ignore[method-assign]
    with caplog.at_level("WARNING", logger="factory_control.blueprint_reads"):
        response = client.get(f"/api/v1/projects/{pid}/frontend-package/files", headers=OPERATOR)

    assert response.status_code == 500, response.text
    body = response.json()
    assert body["errorCode"] == "INTERNAL_ERROR"
    assert body["details"]["providerErrorCode"] == "ARTIFACT_TYPE_MISMATCH"
    assert any("FRONTEND_PACKAGE" in record.getMessage() for record in caplog.records), (
        "the misresolution is warned about, not swallowed"
    )


# --- CP-UI-023: the M4 gate ---------------------------------------------------

_M4_REFUSALS = [
    ("put", "/api/v1/projects/{pid}/members", {"members": []}, "member.manage"),
    ("delete", "/api/v1/projects/{pid}/members/u-1", None, "member.manage"),
    (
        "post",
        "/api/v1/projects/{pid}/artifact-grants",
        {"userId": "u-1", "classification": "RESTRICTED", "reason": "review"},
        "restricted_grant.manage",
    ),
    ("delete", "/api/v1/projects/{pid}/artifact-grants/u-1?classification=RESTRICTED", None, "restricted_grant.manage"),
    (
        "post",
        "/api/v1/projects/{pid}/production-exemptions",
        {"reason": "go-live", "waiver": "NON_PRODUCTION_VERIFIED"},
        "production_exemption.manage",
    ),
    ("delete", "/api/v1/production-exemptions/pex-1", None, "production_exemption.manage"),
]


@pytest.mark.parametrize(
    ("method", "template", "payload", "feature"),
    _M4_REFUSALS,
    ids=[f"{r[0]}-{r[3]}" for r in _M4_REFUSALS],
)
def test_cp_ac_32_every_m4_write_refuses_with_the_feature_reason(
    client: TestClient, seeded_env: dict[str, Any], method: str, template: str, payload: Any, feature: str
) -> None:
    """MVP添加项目成员 → 409 FEATURE_NOT_ENABLED (CP-AC-32), same reason as the greyed button."""
    path = template.format(pid=seeded_env["id"])
    send = getattr(client, method)

    response = send(path, json=payload, headers=OPERATOR) if payload is not None else send(path, headers=OPERATOR)

    assert response.status_code == 409, response.text
    body = response.json()
    assert body["errorCode"] == "FEATURE_NOT_ENABLED"
    assert body["retryable"] is False
    assert body["details"] == {"feature": feature, "milestone": "M4"}


def test_the_m4_reads_stay_open_and_decide_nothing(
    client: TestClient, seeded_env: dict[str, Any]
) -> None:
    """CP-UI-023: reads answer, and none of them changes an availability judgement."""
    pid = seeded_env["id"]
    wid = seeded_env["workflowId"]

    for path in ("members", "artifact-grants", "production-exemptions"):
        response = client.get(f"/api/v1/projects/{pid}/{path}", headers=OPERATOR)
        assert response.status_code == 200, f"{path}: {response.text}"

    actions = client.get(f"/api/v1/workflows/{wid}/allowed-actions", headers=OPERATOR).json()
    refused = {entry["action"]: entry for entry in actions["unavailable"]}
    for feature in ("member.manage", "restricted_grant.manage", "production_exemption.manage"):
        assert refused[feature]["reasonCode"] == "FEATURE_NOT_ENABLED"
        assert refused[feature]["details"] == {"feature": feature, "milestone": "M4"}
    assert any(entry["action"] == "project.edit" for entry in actions["allowed"]), (
        "the reads left every other verdict alone"
    )


def test_the_m4_actions_are_unavailable_while_the_feature_is_off(
    client: TestClient, seeded_env: dict[str, Any]
) -> None:
    """Appendix B: the three M4 actions are always listed, and unavailable with the feature reason."""
    actions = client.get(
        f"/api/v1/workflows/{seeded_env['workflowId']}/allowed-actions", headers=OPERATOR
    ).json()
    allowed = {entry["action"] for entry in actions["allowed"]}
    refused = {entry["action"] for entry in actions["unavailable"]}
    assert allowed.isdisjoint({"member.manage", "restricted_grant.manage", "production_exemption.manage"})
    assert {"member.manage", "restricted_grant.manage", "production_exemption.manage"} <= refused

    platform = client.get("/api/v1/allowed-actions", headers=OPERATOR).json()
    platform_refused = {entry["action"]: entry for entry in platform["unavailable"]}
    assert platform_refused["member.manage"]["reasonCode"] == "FEATURE_NOT_ENABLED"


def test_switching_the_feature_on_restores_the_writes_and_the_actions(
    harness: Any, client: TestClient, seeded_env: dict[str, Any]
) -> None:
    """``m4_enabled`` is the one test both the route and the action list consult (CP-UI-023)."""
    from factory_control.config import Settings

    settings: Settings = harness.runtime.settings
    settings.m4_features_enabled = True
    try:
        pid = seeded_env["id"]
        written = client.put(f"/api/v1/projects/{pid}/members", json={"members": []}, headers=OPERATOR)
        assert written.status_code == 200, written.text

        actions = client.get(
            f"/api/v1/workflows/{seeded_env['workflowId']}/allowed-actions", headers=OPERATOR
        ).json()
        allowed = {entry["action"] for entry in actions["allowed"]}
        assert {"member.manage", "restricted_grant.manage", "production_exemption.manage"} <= allowed
    finally:
        settings.m4_features_enabled = False


# --- appendix B: frontend_package.download follows S1's own index ------------


def test_the_download_action_fuses_on_the_artifact_index_not_the_package_bytes(
    harness: Any, client: TestClient, seeded_env: dict[str, Any]
) -> None:
    """CP-UI-019/021: the button exists exactly when a FRONTEND_PACKAGE is indexed."""
    pid = seeded_env["id"]
    wid = seeded_env["workflowId"]

    actions = client.get(f"/api/v1/workflows/{wid}/allowed-actions", headers=OPERATOR).json()
    refused = {entry["action"]: entry for entry in actions["unavailable"]}
    assert refused["frontend_package.download"]["reasonCode"] == "PACKAGE_NOT_GENERATED"
    before = harness.blueprint.calls.copy()

    _preset_package(harness, pid)
    actions = client.get(f"/api/v1/workflows/{wid}/allowed-actions", headers=OPERATOR).json()
    allowed = {entry["action"] for entry in actions["allowed"]}
    assert "frontend_package.download" in allowed
    assert "list_package_files" not in harness.blueprint.calls[len(before):], (
        "the availability answer reads S1's index, never S2's package"
    )
