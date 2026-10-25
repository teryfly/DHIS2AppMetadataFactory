"""S1 running against the *real* S2 (``factory_blueprint``), not the CP-ASM-08 fake.

The rest of the suite deliberately exercises the fakes, which means an S1↔S2
signature drift is invisible until a deployment calls the real module.  That is not
hypothetical: ``prepare_initial_blueprint`` had been passing S2 a plain dict where
S2 wants an ``InitialProjectInfo``, and every session-taking read would have raised
``TypeError`` — both found only by running it.

S3 stays faked here (``real=("S2",)``): this file is about the Blueprint boundary, and the
design stages need a presettable agent run.  S4 is faked too — not because it is unavailable
(it is installed, and ``test_real_s2_s4.py`` runs against it) but because nothing here needs
an environment yet, and a fake that is never asked a question costs nothing.
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

import pytest
from fastapi.testclient import TestClient

from factory_control.testing.harness import ControlHarness, control_harness, has_real_provider, seed_project

pytestmark = pytest.mark.skipif(not has_real_provider("S2"), reason="factory_blueprint is not installed")

OPERATOR = {"X-Operator": "ada"}


@pytest.fixture
def real_s2() -> Iterator[ControlHarness]:
    with control_harness(providers="real", real=("S2",)) as harness:
        yield harness


@pytest.fixture
def real_client(real_s2: ControlHarness) -> Iterator[TestClient]:
    from factory_control.rest.app import create_app

    with TestClient(create_app(real_s2.runtime)) as client:
        yield client


@pytest.fixture
def real_seeded(real_s2: ControlHarness) -> dict[str, Any]:
    return seed_project(real_s2)


def test_project_creation_lands_a_real_version_0_1_0(
    real_s2: ControlHarness, real_client: TestClient, real_seeded: dict[str, Any]
) -> None:
    """CP-PRJ-001 against S2: the staged draft is registered inside the same UoW."""
    assert real_seeded["currentBlueprintVersion"] == "0.1.0"
    view = real_client.get(f"/api/v1/projects/{real_seeded['id']}", headers=OPERATOR).json()
    assert view["deliveryMode"] == "A"

    semantics = real_s2.blueprint.get_project_semantics(real_seeded["id"])
    # A str-enum leaking through the mirror would land as "DeliveryMode.A" here and
    # silently break every status comparison downstream.
    assert semantics.delivery_mode == "A"
    assert semantics.current_version is not None
    assert semantics.current_version.version == "0.1.0"
    assert semantics.current_version.content_hash.startswith("sha256:")


def test_the_active_draft_is_resolved_from_s1s_own_record(
    real_s2: ControlHarness, real_client: TestClient, real_seeded: dict[str, Any]
) -> None:
    """S5IF-BP05 【裁决】: naming the live draft is S1's job, so S5 never guesses it.

    S2 keeps ``active_draft_id`` module-private (S5IF-UP03), so its project semantics say
    nothing.  S1 does not need them: opening the draft happens inside a stage run, and the
    id is recorded on that stage's ``inputBlueprintRef`` — S1's own column.  Before any
    stage has run there is genuinely no draft to name, which is a 404 rather than a guess.
    """
    from tests.helpers import run_stage  # type: ignore[import]

    before = real_client.get(
        f"/api/v1/projects/{real_seeded['id']}/blueprint/draft", headers=OPERATOR
    )
    assert before.status_code == 404, before.text
    # CP-UI-022: no draft is a stage that has not run, and the console has to be able to tell
    # that apart from a dead reference — S5 and S5M render this as 空态 rather than a failure.
    assert before.json()["details"]["reason"] == "NOT_GENERATED", before.text
    assert before.json()["details"]["requiredStage"] == "REQUIREMENTS", before.text

    view = real_client.get(
        f"/api/v1/workflows/{real_seeded['workflowId']}", headers=OPERATOR
    ).json()
    ready = [stage for stage in view["stages"] if stage["status"] == "READY"]
    run_stage(real_client, real_s2, ready[0]["id"])

    response = real_client.get(
        f"/api/v1/projects/{real_seeded['id']}/blueprint/draft", headers=OPERATOR
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["draftId"], body
    # The whole-document read stays out on purpose: entry content comes from BP04.
    assert body["document"] is None, body

    workflow = real_client.get(
        f"/api/v1/workflows/{real_seeded['workflowId']}", headers=OPERATOR
    ).json()
    assert workflow["activeDraft"]["draftId"] == body["draftId"], workflow.get("activeDraft")
    assert workflow["activeDraft"]["contentHash"], workflow["activeDraft"]


def test_section_schema_is_s2_own_self_contained_document(
    real_client: TestClient, real_seeded: dict[str, Any]
) -> None:
    """S5IF-AC07: S2 inlines every ``$ref`` under ``definitions``."""
    index = real_client.get(
        f"/api/v1/projects/{real_seeded['id']}/blueprint/section-schemas", headers=OPERATOR
    ).json()
    assert len(index["sections"]) == 17
    assert index["currentSchemaVersion"] == 1

    body = real_client.get(
        f"/api/v1/projects/{real_seeded['id']}/blueprint/section-schemas/mapping", headers=OPERATOR
    ).json()
    assert body["$schema"] == "https://json-schema.org/draft/2020-12/schema"
    assert body["definitions"], "S2 must inline the refs it rewrites"
    # Nothing may point outside the returned document.
    assert '"$ref":"#/definitions' in _compact(body) or _external_refs(body) == set()


def test_version_list_carries_the_fields_s2_records(
    real_client: TestClient, real_seeded: dict[str, Any]
) -> None:
    """S5IF-BP03: S2's ``VersionInfo``, not a subset S1 happened to notice."""
    page = real_client.get(
        f"/api/v1/projects/{real_seeded['id']}/blueprint/versions", headers=OPERATOR
    ).json()
    assert page["total"] >= 1
    row = page["items"][-1]
    assert row["version"] == "0.1.0"
    assert row["schemaVersion"] == 1
    assert row["contentHash"].startswith("sha256:")
    assert row["artifactId"]
    assert row["changeLevel"]


def test_entries_keep_s2_locator_keys_on_the_real_read(
    real_client: TestClient, real_seeded: dict[str, Any]
) -> None:
    """S5IF-BP04 against real S2 — the fakes could not prove the keys exist."""
    body = real_client.get(
        f"/api/v1/projects/{real_seeded['id']}/blueprint/entries"
        "?sections=project&businessKeys=&source=APPROVED",
        headers=OPERATOR,
    ).json()
    assert body["source"] == "APPROVED"
    # ``project`` is a whole-object section, so S2 appends {section, value}.
    assert isinstance(body["entries"], list)


def test_dependency_graph_is_a_guided_404_before_any_build(
    real_client: TestClient, real_seeded: dict[str, Any]
) -> None:
    """CP-UI-020/022 through real S2 wiring: nothing was compiled, so the graph is not empty — absent.

    The old read answered 200 with an assembled empty picture; the contract now refuses it
    the same way on the real path as on the fake one, which is what pins CP-AC-31 outside the fakes.
    """
    pid = real_seeded["id"]
    wid = real_seeded["workflowId"]

    for path in (f"/api/v1/projects/{pid}/object-graph", f"/api/v1/workflows/{wid}/object-graph"):
        response = real_client.get(path, headers=OPERATOR)
        assert response.status_code == 404, f"{path}: {response.text}"
        body = response.json()
        assert body["errorCode"] == "NOT_FOUND"
        assert body["details"]["reason"] == "NOT_GENERATED"
        assert body["details"]["requiredStage"] == "BASELINE_COMPILE", path


def test_package_files_answers_not_generated_on_the_real_read_path(
    real_client: TestClient, real_seeded: dict[str, Any]
) -> None:
    """S5IF-BP17 on the real read path: nothing was compiled.

    S2's real ``get_package_manifest`` answers its unbuilt sentinel (``kind="NONE"``,
    ``build_id=""``) rather than raising — and CP-UI-022 says what the caller must turn
    that into: a guided 404, not a 200 with a no-content marker.
    """
    pid = real_seeded["id"]
    response = real_client.get(f"/api/v1/projects/{pid}/package-files", headers=OPERATOR)

    assert response.status_code == 404, response.text
    body = response.json()
    assert body["errorCode"] == "NOT_FOUND"
    assert body["details"]["reason"] == "NOT_GENERATED"
    assert body["details"]["requiredStage"] == "BASELINE_COMPILE"


def test_s2s_real_graph_read_answers_not_exist_for_an_unknown_build(real_s2: ControlHarness) -> None:
    """factory_blueprint 0.2.0's own refusal backs CP-UI-020 — not a transitional double.

    When S1's own record names a build S2 no longer has (data drift), the provider's
    ``NOT_EXIST`` comes back through the real function unchanged; this pins S1's wiring
    onto the shipped implementation (the fake-side semantics live in
    ``tests/api/test_cp_ui_020_023.py``).
    """
    from factory_control.foundation import FactoryError

    with pytest.raises(FactoryError) as caught:
        real_s2.blueprint.get_dependency_graph("bld-known-gone", None)

    error = caught.value
    assert error.error_code == "NOT_FOUND"
    assert dict(error.details or {}).get("reason") == "NOT_EXIST"


def test_s2s_real_package_read_refuses_a_commit_package_by_type(real_s2: ControlHarness) -> None:
    """BP-HO-004 item 2 on the real path: a METADATA_PACKAGE answers ``ARTIFACT_TYPE_MISMATCH``.

    The code belongs to S0 (FD-COM-001a) — S2 raises it, S1 maps it to a 500 with a
    warning (CP-UI-021, pinned at the wire in ``tests/api/test_cp_ui_020_023.py``);
    this pins the provider's own answer so the mapping cannot drift silently.
    """
    from factory_control.foundation import FactoryError
    from factory_control.testing.fakes import register_fake_artifact

    commit_package = register_fake_artifact("prj-real-mismatch", "METADATA_PACKAGE", "wrong-door")
    with pytest.raises(FactoryError) as caught:
        real_s2.blueprint.list_package_files(commit_package)

    error = caught.value
    assert error.error_code == "ARTIFACT_TYPE_MISMATCH"
    assert dict(error.details or {}).get("expectedType") == "FRONTEND_PACKAGE"
    assert dict(error.details or {}).get("actualType") == "METADATA_PACKAGE"


def test_registry_and_pool_reads_answer_in_camel_case(
    real_client: TestClient, real_seeded: dict[str, Any]
) -> None:
    """S5IF-BP06/BP07/BP08 on the real read path."""
    pid = real_seeded["id"]
    registry = real_client.get(
        f"/api/v1/projects/{pid}/registry?environmentKey=dev", headers=OPERATOR
    ).json()
    assert registry["items"] == []  # nothing bound yet; the shape is what matters here

    scope = real_client.get(
        f"/api/v1/projects/{pid}/registry/managed-scope?environmentKey=dev", headers=OPERATOR
    ).json()
    assert scope["items"] == []

    pool = real_client.get(f"/api/v1/projects/{pid}/uid-pool", headers=OPERATOR).json()
    assert pool["projectId"] == pid
    assert isinstance(pool["lowWaterMark"], int)
    assert isinstance(pool["needsReplenishment"], bool)


def test_diff_of_a_version_against_itself_is_empty(
    real_client: TestClient, real_seeded: dict[str, Any]
) -> None:
    """S5IF-BP12 through S2's ``diff_versions``."""
    pid = real_seeded["id"]
    body = real_client.get(
        f"/api/v1/projects/{pid}/blueprint/diff?fromVersion=0.1.0&toVersion=0.1.0",
        headers=OPERATOR,
    ).json()
    assert body["fromVersion"] == "0.1.0"
    assert body["kind"] == "BLUEPRINT_VERSIONS"
    assert body["objects"] == []


def test_a_manual_patch_reaches_s2_and_moves_the_draft_hash(
    real_s2: ControlHarness, real_client: TestClient, real_seeded: dict[str, Any]
) -> None:
    """S5IF-FLD11 on the real path: S2 validates the operations and answers an outcome."""
    from tests.helpers import make_client, run_stage  # type: ignore[import]

    del make_client
    view = real_client.get(f"/api/v1/workflows/{real_seeded['workflowId']}", headers=OPERATOR).json()
    ready = [s for s in view["stages"] if s["status"] == "READY"]
    assert ready
    run_stage(real_client, real_s2, ready[0]["id"])

    before = real_client.get(f"/api/v1/projects/{real_seeded['id']}/blueprint/draft", headers=OPERATOR).json()

    response = real_client.post(
        f"/api/v1/stages/{ready[0]['id']}/patches",
        headers=OPERATOR,
        json={
            "patch": {
                "operations": [
                    {
                        "op": "add",
                        "path": "/requirements/items/-",
                        "value": {
                            "businessKey": "req-live-1",
                            "statement": "The clinic must record a visit outcome.",
                            # BPV-ST-001 requires a well-formed artifact id here; "art-probe" is
                            # too short, and S2 answered REJECTED while this test still read 200.
                            "source": {"artifactId": "art-liveprobear0001", "location": "spec §2"},
                        },
                    }
                ],
                "rationale": "added from the live S2 integration test",
            }
        },
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["draftHash"], "S2 returns the new draft hash and S1 must forward it"
    assert isinstance(body["issues"], list)
    assert isinstance(body["assignedUids"], dict)
    # The test's name claims the hash moves, so make it say so: an operation S2 rejected
    # still answers 200 with a hash, and that is the difference between "the patch reached
    # S2" and "the patch reached S2 and changed the draft".
    assert body["patchStatus"] == "PROPOSED", body
    assert body["draftHash"] != before["contentHash"], "the accepted patch left the draft unchanged"


def test_an_operator_can_author_edit_and_delete_requirements_in_one_attempt(
    real_s2: ControlHarness, real_client: TestClient, real_seeded: dict[str, Any]
) -> None:
    """S5IF-FLD11 for the hand-authoring console (UM-MAN-002): an operator editing a list is
    submitting more than one patch, and each of them has to land.

    ``submit_manual_patch`` keyed its S2 call on ``patch:{stage}:{attempt}``, so the *second*
    different body of one stage attempt answered 422 IDEMPOTENCY_CONFLICT — the phone could add
    one requirement and every later edit or deletion was refused.  Replay protection is the other
    half of this test: the same body sent twice must move the draft once.
    """
    from tests.helpers import run_stage  # type: ignore[import]

    pid = real_seeded["id"]
    view = real_client.get(f"/api/v1/workflows/{real_seeded['workflowId']}", headers=OPERATOR).json()
    stage = next(s for s in view["stages"] if s["stageType"] == "REQUIREMENTS" and s["status"] == "READY")
    run_stage(real_client, real_s2, stage["id"])

    def item(key: str, statement: str, status: str = "CANDIDATE") -> dict[str, Any]:
        return {
            "businessKey": key,
            "statement": statement,
            "status": status,
            "priority": "MUST",
            "confidence": 0.9,
            "source": {"note": "hand-authored on the phone"},
        }

    def post(operations: list[dict[str, Any]], *, key: str = "") -> dict[str, Any]:
        headers = {**OPERATOR, **({"Idempotency-Key": key} if key else {})}
        answer = real_client.post(
            f"/api/v1/stages/{stage['id']}/patches",
            headers=headers,
            json={"patch": {"operations": operations, "rationale": "hand-authored"}},
        )
        assert answer.status_code == 200, answer.text
        return answer.json()

    def keys() -> list[str]:
        body = real_client.get(
            f"/api/v1/projects/{pid}/blueprint/entries?sections=requirements&source=DRAFT", headers=OPERATOR
        ).json()
        return [str(e["businessKey"]) for e in body["entries"]]

    def read_items() -> list[dict[str, Any]]:
        body = real_client.get(
            f"/api/v1/projects/{pid}/blueprint/entries?sections=requirements&source=DRAFT", headers=OPERATOR
        ).json()
        # The read model's locators are not entry fields; the console strips them the same way.
        return [{k: v for k, v in e.items() if k not in ("_section", "_path")} for e in body["entries"]]

    appended = post([{"op": "add", "path": "/requirements/items/-", "value": item("req-live-a", "First entry.")}])
    assert appended["patchStatus"] == "PROPOSED", appended

    # The second entry is the one the old key refused.
    second = post([{"op": "add", "path": "/requirements/items/-", "value": item("req-live-b", "Second entry.")}])
    assert second["patchStatus"] == "PROPOSED", second
    assert second["draftHash"] != appended["draftHash"]
    assert {"req-live-a", "req-live-b"} == set(keys()), keys()

    # A byte-identical resend (a double tap, a retried activity) replays the first outcome.
    replayed = post([{"op": "add", "path": "/requirements/items/-", "value": item("req-live-b", "Second entry.")}])
    assert replayed["draftHash"] == second["draftHash"], "the replay applied the patch a second time"
    assert keys().count("req-live-b") == 1, keys()

    # What the console sends for an edit and for a delete: the whole container, rebuilt from a
    # fresh read.  S2's reader sorts entries by businessKey and its patch engine addresses array
    # members by index only, so an item-level pointer is not expressible from this side.
    merged = read_items()
    edited = next(i for i in merged if i["businessKey"] == "req-live-a")
    edited["status"] = "CONFIRMED"
    assert post([{"op": "replace", "path": "/requirements/items", "value": merged}])["patchStatus"] == "PROPOSED"
    after_edit = read_items()
    assert next(i for i in after_edit if i["businessKey"] == "req-live-a")["status"] == "CONFIRMED"

    removed = post(
        [{"op": "replace", "path": "/requirements/items", "value": [i for i in after_edit if i["businessKey"] != "req-live-b"]}]
    )
    assert removed["patchStatus"] == "PROPOSED", removed
    assert keys() == ["req-live-a"], keys()

    # The trap a content-only key falls into, and the reason the draft hash rides along: this body
    # is byte-identical to the `second` patch above, but the draft has moved since, so it is a new
    # action and must apply.  Keyed on the content alone it replayed the first outcome — the console
    # was told PROPOSED and the entry stayed out of the draft.  Reproduced against a live draft.
    readded = post([{"op": "add", "path": "/requirements/items/-", "value": item("req-live-b", "Second entry.")}])
    assert readded["patchStatus"] == "PROPOSED", readded
    assert readded["draftHash"] != removed["draftHash"], "the repeat body replayed the first outcome"
    assert keys() == ["req-live-a", "req-live-b"], keys()

    # A key the caller sent is the caller's own answer to "is this the same request again?", and it
    # beats anything S1 can infer: the consoles mint a fresh Idempotency-Key per action and release
    # it when the action succeeds (CP-API-003). Without it, a delete followed by an add of the
    # identical content returns the draft to a hash it has already had, and the content-plus-draft
    # fallback reads that as a retry — the re-add is swallowed while the screen says PROPOSED.
    same_body = [{"op": "add", "path": "/requirements/items/-", "value": item("req-live-c", "Third entry.")}]
    keyed = post(same_body, key="hand-key-1")
    reposted = post(same_body, key="hand-key-1")
    assert reposted["draftHash"] == keyed["draftHash"], "the same key applied the patch twice"
    assert keys().count("req-live-c") == 1, keys()

    without_c = post(
        [{"op": "replace", "path": "/requirements/items", "value": [i for i in read_items() if i["businessKey"] != "req-live-c"]}],
        key="hand-key-2",
    )
    assert without_c["patchStatus"] == "PROPOSED", without_c
    assert "req-live-c" not in keys(), keys()

    again = post(same_body, key="hand-key-3")
    assert again["patchStatus"] == "PROPOSED", again
    assert "req-live-c" in keys(), "a fresh key was swallowed as a retry of the identical body"


def test_the_whole_design_line_promotes_real_versions(
    real_s2: ControlHarness, real_client: TestClient, real_seeded: dict[str, Any]
) -> None:
    """The point of this file: freeze + Gate + promote_draft against S2, stage by stage.

    ``open_draft`` and ``promote_draft`` both take S2's session-first-style keyword,
    and a promotion is keyed on the hash the freeze returned — none of that is
    expressible against a fake.
    """
    from tests.helpers import finish_design_line  # type: ignore[import]

    view = finish_design_line(real_client, real_s2, real_seeded)
    assert view["milestoneStatus"], "the derived milestone came back"
    project = real_client.get(f"/api/v1/projects/{real_seeded['id']}", headers=OPERATOR).json()
    assert project["currentBlueprintVersion"] not in (None, "", "0.1.0"), (
        "a completed design line must have promoted past version 0.1.0"
    )

    statuses = {s["stageType"]: s["status"] for s in project["stages"]}
    assert statuses["REQUIREMENTS"] == "PASSED"
    versions = real_client.get(
        f"/api/v1/projects/{real_seeded['id']}/blueprint/versions", headers=OPERATOR
    ).json()
    assert versions["total"] >= 2, "S2 recorded every promotion"


def _compact(payload: Any) -> str:
    import json

    return json.dumps(payload, separators=(",", ":"))


def _external_refs(node: Any, found: set[str] | None = None) -> set[str]:
    refs = found if found is not None else set()
    if isinstance(node, dict):
        for key, value in node.items():
            if key == "$ref" and isinstance(value, str) and not value.startswith("#/definitions/"):
                refs.add(value)
            else:
                _external_refs(value, refs)
    elif isinstance(node, list):
        for item in node:
            _external_refs(item, refs)
    return refs


def _advance_to(client: TestClient, harness: ControlHarness, workflow_id: str, stage_type: str) -> str:
    """Run and approve design stages until *stage_type* is the READY one.

    Registry entries are MAPPING-scope, so a test that wants to hand-author one has to get
    the design line that far; there is no shorter way that stays on the public API.
    """
    from tests.helpers import approve_waiting_gate, run_stage  # type: ignore[import]

    for _ in range(12):
        view = client.get(f"/api/v1/workflows/{workflow_id}", headers=OPERATOR).json()
        design = [s for s in view["stages"] if s["deploymentRunId"] is None]
        target = next(s for s in design if s["stageType"] == stage_type)
        if target["status"] == "READY":
            return target["id"]
        waiting = [g for g in client.get(f"/api/v1/workflows/{workflow_id}/gates", headers=OPERATOR).json()["items"] if g["status"] == "WAITING"]
        if waiting:
            approve_waiting_gate(client, harness, workflow_id)
            continue
        ready = [s for s in design if s["status"] == "READY"]
        assert ready, f"nothing is READY and no Gate waits: {[(s['stageType'], s['status']) for s in design]}"
        outcome = run_stage(client, harness, ready[0]["id"])
        assert "error" not in outcome, outcome
    raise AssertionError(f"never reached a READY {stage_type}")


def test_a_hand_authored_registry_create_gets_a_uid(
    real_s2: ControlHarness, real_client: TestClient, real_seeded: dict[str, Any]
) -> None:
    """A manual MAPPING patch may consume a UID, which needs the pool filled first.

    ``submit_manual_patch`` opened the draft and prepared its base but never replenished,
    while only an agent stage run did.  So the first ``CREATE`` registry entry of a hand-
    authored project was refused with ``UID_POOL_EXHAUSTED`` against a pool that had never
    been touched — and the UI's only answer (``UI-MAN-003``: "the interface shows waiting")
    pointed at a retry that would fail the same way again.
    """
    stage_id = _advance_to(real_client, real_s2, real_seeded["workflowId"], "MAPPING")

    response = real_client.post(
        f"/api/v1/stages/{stage_id}/patches",
        headers=OPERATOR,
        json={
            "patch": {
                "operations": [
                    {
                        "op": "add",
                        "path": "/registry/entries/-",
                        "value": {
                            "businessKey": "de-weight",
                            "dhis2Type": "dataElement",
                            "disposition": "CREATE",
                            "status": "PROPOSED",
                            "source": {"note": "hand-authored in the MAPPING stage"},
                        },
                    }
                ],
                "rationale": "the first registry entry of a manual project",
            }
        },
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["assignedUids"], "S2 assigned no UID to a CREATE entry"
    assert body["assignedUids"].get("de-weight"), body["assignedUids"]

    pool = real_client.get(f"/api/v1/projects/{real_seeded['id']}/uid-pool", headers=OPERATOR).json()
    # 600 drawn, one consumed by the entry above — the point is that the pool was filled at
    # all, which is what the patch could not get before.
    assert pool["available"] > 0, pool


def test_the_replenish_action_has_a_write_path(
    real_s2: ControlHarness, real_client: TestClient, real_seeded: dict[str, Any]
) -> None:
    """``uid_pool.replenish`` is offered by allowed-actions, so a call must exist to follow.

    S1 SRS 697 requires the name in the action list; the audit's own closing note was that a
    name with no writable path behind it makes every UI that gates on it permanently grey.
    """
    actions = real_client.get(
        f"/api/v1/workflows/{real_seeded['workflowId']}/allowed-actions", headers=OPERATOR
    ).json()
    assert "uid_pool.replenish" in str(actions), actions

    before = real_client.get(f"/api/v1/projects/{real_seeded['id']}/uid-pool", headers=OPERATOR).json()
    response = real_client.post(
        f"/api/v1/projects/{real_seeded['id']}/uid-pool/replenish",
        headers={**OPERATOR, "Idempotency-Key": "replenish-1"},
        json={},
    )
    assert response.status_code == 200, response.text
    after = response.json()
    assert after["available"] >= before["available"], (before, after)


def test_replenish_refuses_an_environment_the_project_does_not_target(
    real_s2: ControlHarness, real_client: TestClient, real_seeded: dict[str, Any]
) -> None:
    """Drawing UIDs is scoped to the project's own targets; a stray key is a 422, not a guess."""
    response = real_client.post(
        f"/api/v1/projects/{real_seeded['id']}/uid-pool/replenish",
        headers={**OPERATOR, "Idempotency-Key": "replenish-bad"},
        json={"environmentKey": "not-a-target"},
    )
    assert response.status_code == 422, response.text
    assert "not-a-target" in response.text


def test_a_hand_authored_patch_leaves_a_draft_the_console_can_find(
    real_s2: ControlHarness, real_client: TestClient, real_seeded: dict[str, Any]
) -> None:
    """S5IF-BP05 after a manual patch: the draft read must not 404 on a stage that just wrote one.

    ``resolve_active_draft`` asks S2's project semantics and falls back to S1's own
    ``inputBlueprintRef`` — UI-BR-02 says naming the live draft is S1's job so S5 never guesses.
    The agent path records that column; the hand-authored path opened a draft and threw the id
    away, so right after a patch S2 had accepted, ``GET /blueprint/draft`` answered NOT_EXIST.
    A fake that keeps a project-wide draft hid this: only real S2 returns no draft in semantics.
    """
    from tests.helpers import run_stage  # type: ignore[import]

    view = real_client.get(f"/api/v1/workflows/{real_seeded['workflowId']}", headers=OPERATOR).json()
    ready = [s for s in view["stages"] if s["status"] == "READY"]
    assert ready
    stage_id = ready[0]["id"]
    run_stage(real_client, real_s2, stage_id)

    before = real_client.get(f"/api/v1/projects/{real_seeded['id']}/blueprint/draft", headers=OPERATOR)
    assert before.status_code == 200, before.text
    draft_before = before.json()["draftId"]

    submitted = real_client.post(
        f"/api/v1/stages/{stage_id}/patches",
        headers=OPERATOR,
        json={
            "patch": {
                "operations": [
                    {
                        "op": "add",
                        "path": "/requirements/items/-",
                        "value": {
                            "businessKey": "req-draft-visible",
                            "statement": "The draft that a manual patch opened has to be readable back.",
                            "source": {"artifactId": "art-seedprobeanalogue0001", "location": "spec §3"},
                        },
                    }
                ],
                "rationale": "prove the console can still find the draft after authoring by hand",
            }
        },
    )
    assert submitted.status_code == 200, submitted.text
    assert submitted.json()["patchStatus"] == "PROPOSED", submitted.json()

    after = real_client.get(f"/api/v1/projects/{real_seeded['id']}/blueprint/draft", headers=OPERATOR)
    assert after.status_code == 200, after.text
    assert after.json()["draftId"] == draft_before, "the patch opened a second draft instead of the recorded one"


def test_the_project_code_prefix_reaches_the_real_generation_policy(
    real_s2: ControlHarness, real_seeded: dict[str, Any]
) -> None:
    """FR-TST-002 needs the code a Run commits to identify the run that made it.

    S2 assigns every managed object's code from the project's generation policy and replaces
    an author-supplied one (BP-REG-009), so the only way a shared target instance can be
    cleaned up by prefix is if S1 forwards the prefix at creation — and until this test the
    policy was always S2's own default.
    """
    project = seed_project(real_s2, name="prefixed", code_prefix="SB_L9")
    selected = real_s2.blueprint.get_entries(project["id"], {"sections": ["generationPolicy"]})
    # generationPolicy answers as {"section": ..., "value": {...}}, not as keyed entries
    policy = next(entry["value"] for entry in selected.entries if entry.get("section") == "generationPolicy")
    assert policy["codePrefix"] == "SB_L9", policy

    untouched = real_s2.blueprint.get_entries(real_seeded["id"], {"sections": ["generationPolicy"]})
    default = next(entry["value"] for entry in untouched.entries if entry.get("section") == "generationPolicy")
    assert default["codePrefix"] == "GEN", default


def test_a_code_prefix_that_is_not_an_identifier_is_refused() -> None:
    """The prefix is spliced into a code verbatim, so it cannot be allowed to carry a path."""
    from factory_control.errors import ValidationFailedError
    from factory_control.rest.schemas.requests import CreateProjectBody

    with pytest.raises(ValidationFailedError):
        CreateProjectBody(name="p", owner_name="ada", version_constraints="2.41.x", code_prefix="sb/../x")
    assert CreateProjectBody(
        name="p", owner_name="ada", version_constraints="2.41.x", code_prefix="SB_l1"
    ).to_payload()["codePrefix"] == "SB_l1"
