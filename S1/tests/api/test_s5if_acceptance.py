"""The document's section-12 acceptance criteria that need a flow, not a table.

Each test is one row of ``S5IF-AC``.  The ones that only check a shape live in
``test_openapi_contract.py`` and ``test_s5if_endpoints.py``.
"""

from __future__ import annotations

from typing import Any
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from factory_control.credentials.backend import install_master_key_backend

OPERATOR = {"X-Operator": "ada"}

PROD_BODY = {
    "environmentKey": "prod-new",
    "baseUrl": "https://prod.invalid",
    "environmentClass": "PRODUCTION",
    "versionConstraint": "2.41.x",
    "credentials": [
        {"scope": "discovery", "secretRef": "vault://same/key"},
        {"scope": "import", "secretRef": "vault://same/key"},
    ],
}


def test_ac04_production_sharing_one_secret_is_refused_with_the_scopes_named(
    client: TestClient, seeded_env: Any
) -> None:
    """CP-AC-14 / S5IF-AC04 / DX-ENV-005: the conflict is S1's check, so S5 never sends it to S4."""
    del seeded_env
    response = client.post("/api/v1/environments", headers=OPERATOR, json=PROD_BODY)
    assert response.status_code == 422, response.text
    body = response.json()
    assert body["errorCode"] == "VALIDATION_ERROR"
    assert body["details"]["conflictScopes"] == ["discovery", "import"]


def test_ac04_distinct_secrets_are_accepted(client: TestClient, seeded_env: Any) -> None:
    del seeded_env
    body = {
        **PROD_BODY,
        "credentials": [
            {"scope": "discovery", "secretRef": "vault://prod/disc"},
            {"scope": "import", "secretRef": "vault://prod/imp"},
        ],
        # DX-ENV-002: real S4 refuses a PRODUCTION environment without windows, so the body
        # that S5 must send is what this acceptance criterion has to assert.
        "protection": {
            "timezone": "Africa/Addis_Ababa",
            "commitWindows": [{"days": [2, 4], "start": "01:00", "end": "05:00"}],
        },
    }
    response = client.post("/api/v1/environments", headers=OPERATOR, json=body)
    assert response.status_code == 201, response.text
    created = response.json()
    assert created["environmentClass"] == "PRODUCTION"
    # Both scopes came back, and neither came back as anything but a reference.
    assert {c["scope"] for c in created["credentials"]} == {"discovery", "import"}
    assert all(c["secretRef"].startswith("vault://") for c in created["credentials"])
    protection = client.get("/api/v1/environments/prod-new/protection", headers=OPERATOR).json()
    assert protection["timezone"] == "Africa/Addis_Ababa"
    assert protection["commitWindows"] == [{"days": [2, 4], "start": "01:00", "end": "05:00"}]


def test_a_production_environment_without_commit_windows_is_refused(
    client: TestClient, seeded_env: Any
) -> None:
    """DX-ENV-002 at the door: S4 raises it deep down, where S5 would see a nameless 500."""
    del seeded_env
    body = {
        **PROD_BODY,
        "credentials": [
            {"scope": "discovery", "secretRef": "vault://prod/disc"},
            {"scope": "import", "secretRef": "vault://prod/imp"},
        ],
    }
    response = client.post("/api/v1/environments", headers=OPERATOR, json=body)
    assert response.status_code == 422, response.text
    details = response.json()["details"]
    assert details["rule"] == "DX-ENV-002"
    assert details["fields"][0]["path"] == "protection.commitWindows"


def test_one_secret_reference_per_scope(client: TestClient, seeded_env: Any) -> None:
    """A second row for a scope is a save that silently drops one of them.

    S4 keys its credential rows by scope, so two rows arrive either as an IntegrityError — a
    500 naming nothing — or, on an update, as "last one wins" with no error at all.
    """
    del seeded_env
    response = client.post(
        "/api/v1/environments",
        headers=OPERATOR,
        json={
            "environmentKey": "dup-scope",
            "baseUrl": "https://dup.invalid",
            "versionConstraint": "2.41.x",
            "credentials": [
                {"scope": "discovery", "secretRef": "env://A_DISC"},
                {"scope": "discovery", "secretRef": "env://A_DISC_LATER"},
                {"scope": "import", "secretRef": "env://A_IMP"},
            ],
        },
    )
    assert response.status_code == 422, response.text
    assert response.json()["details"]["duplicateScopes"] == ["discovery"]


def test_the_emergency_lock_is_not_a_profile_field(client: TestClient, seeded_env: Any) -> None:
    """DX-ENV-007: writing the lock through the profile leaves no reason, time or operator."""
    del seeded_env
    response = client.post(
        "/api/v1/environments",
        headers=OPERATOR,
        json={
            "environmentKey": "lock-through-profile",
            "baseUrl": "https://lock.invalid",
            "versionConstraint": "2.41.x",
            "credentials": [
                {"scope": "discovery", "secretRef": "env://L_DISC"},
                {"scope": "import", "secretRef": "env://L_IMP"},
            ],
            "protection": {"emergencyLock": True},
        },
    )
    assert response.status_code == 422, response.text
    assert response.json()["details"]["rule"] == "DX-ENV-007"


def test_an_environment_cannot_be_left_without_a_name(client: TestClient, seeded_env: Any) -> None:
    """S4 re-validates the merged profile, so an empty name fails there as a nameless 500."""
    response = client.patch("/api/v1/environments/dev", headers=OPERATOR, json={"name": ""})
    assert response.status_code == 422, response.text
    assert response.json()["errorCode"] == "VALIDATION_ERROR"


@pytest.mark.parametrize(
    ("override", "path", "rule"),
    [
        ({"environmentKey": "has space"}, "environmentKey", None),
        ({"baseUrl": "prod.invalid"}, "baseUrl", None),
        ({"baseUrl": "ftp://prod.invalid"}, "baseUrl", None),
        ({"timeoutSeconds": 0}, "timeoutSeconds", None),
        ({"versionConstraint": "  "}, "versionConstraint", "DX-ENV-001"),
        ({"credentials": [{"scope": "discovery", "secretRef": "env://ONLY_DISC"}]}, "credentials", None),
    ],
    ids=["key-space", "url-scheme-missing", "url-scheme-other", "timeout-zero", "no-constraint", "one-scope"],
)
def test_the_rules_s4_applies_are_answered_by_s1_with_the_field_named(
    client: TestClient, seeded_env: Any, override: dict[str, Any], path: str, rule: str | None
) -> None:
    """Every one of these reaches ``validate_spec`` in S4, where the answer is a 500 with no field.

    The body is otherwise valid, so the named path is the only reason it can be refused.
    """
    del seeded_env
    response = client.post(
        "/api/v1/environments",
        headers=OPERATOR,
        json={
            "environmentKey": "door-checks",
            "baseUrl": "https://door.invalid",
            "versionConstraint": "2.41.x",
            "credentials": [
                {"scope": "discovery", "secretRef": "env://D_DISC"},
                {"scope": "import", "secretRef": "env://D_IMP"},
            ],
            **override,
        },
    )
    assert response.status_code == 422, response.text
    details = response.json()["details"]
    if rule is not None:
        assert details["rule"] == rule, details
    assert details["fields"][0]["path"].startswith(path), details


def test_amending_the_commit_windows_reaches_the_protection_block(
    client: TestClient, seeded_env: Any
) -> None:
    """The windows are editable, so an edit that answers 200 must show in the read-back."""
    edited = client.patch(
        "/api/v1/environments/dev",
        headers=OPERATOR,
        json={
            "protection": {
                "timezone": "Africa/Addis_Ababa",
                "commitWindows": [{"days": [6], "start": "22:00", "end": "23:30"}],
            }
        },
    )
    assert edited.status_code == 200, edited.text
    protection = client.get("/api/v1/environments/dev/protection", headers=OPERATOR).json()
    assert protection["commitWindows"] == [{"days": [6], "start": "22:00", "end": "23:30"}]
    assert protection["timezone"] == "Africa/Addis_Ababa"


def test_ac06_locking_an_environment_moves_the_commit_block_onto_the_environment_key(
    harness: Any, seeded: dict[str, Any], client: TestClient
) -> None:
    """CP-AC-16 / S5IF-AC06 / DX-AC-05: ``targetId`` must be the key, so the reason lands on the right control."""
    from tests.helpers import finish_design_line  # type: ignore[import]

    finish_design_line(client, harness, seeded)
    created = client.post(
        f"/api/v1/workflows/{seeded['workflowId']}/deployment-runs",
        headers=OPERATOR,
        json={"environmentKey": "dev"},
    )
    assert created.status_code == 201, created.text

    harness.execution.set_emergency_lock("dev", True, "security incident", "ada")
    actions = client.get(f"/api/v1/workflows/{seeded['workflowId']}/allowed-actions", headers=OPERATOR).json()
    commit = [a for a in actions["unavailable"] if a["action"] == "execution.commit"]
    assert commit, actions["unavailable"]
    assert commit[0]["reasonCode"] == "ENVIRONMENT_LOCKED"
    assert commit[0]["targetId"] == "dev"
    assert commit[0]["details"]["runId"]

    environment_action = [
        a for a in actions["allowed"] + actions["unavailable"] if a["action"] == "environment.emergency_lock"
    ]
    assert {a["targetId"] for a in environment_action} == {"dev", "prod"}


def test_ac12_parsing_twice_with_the_same_key_makes_no_second_question(
    client: TestClient, seeded_env: dict[str, Any]
) -> None:
    """CP-AC-22 / S5IF-AC12 with CP-Q-004: the replay must be the first result, byte for byte."""
    from tests.helpers import upload_input  # type: ignore[import]

    artifact_id = upload_input(client, seeded_env["id"], "twice parsed")
    key = {"X-Operator": "ada", "Idempotency-Key": "parse-once"}
    first = client.post(
        f"/api/v1/projects/{seeded_env['id']}/inputs/{artifact_id}/parse", headers=key, json={}
    )
    assert first.status_code == 202, first.text  # CP-API-012
    assert first.json()["stepRunId"], first.json()
    before = len(_questions(client, seeded_env["workflowId"]))

    replay = client.post(
        f"/api/v1/projects/{seeded_env['id']}/inputs/{artifact_id}/parse", headers=key, json={}
    )
    assert replay.headers.get("X-Idempotent-Replay") == "true", dict(replay.headers)
    assert replay.json() == first.json()
    after = len(_questions(client, seeded_env["workflowId"]))
    assert after == before


def test_ac30_the_workbench_is_offered_every_new_action(
    client: TestClient, harness: Any, seeded_env: dict
) -> None:
    """CP-UI-019 (附录 B: every action the new endpoints run is named here, with its id.

    UI-FORM-005 greys a control out and shows the refusal's reason code, and it can only
    do that if the action list carries the same names 附录 B registers and the target the
    control acts on.
    """
    from tests.helpers import create_run  # type: ignore[import]

    body = client.get(
        f"/api/v1/workflows/{seeded_env['workflowId']}/allowed-actions", headers=OPERATOR
    ).json()
    offered = {entry["action"] for entry in body["allowed"]} | {entry["action"] for entry in body["unavailable"]}
    expected = {
        "input.parse",
        "input.confirm_column_mapping",
        "artifact.download",
        "artifact.explain",
        "artifact.purge",
        "agent.cancel",
        "registry_binding.set",
        "uid_pool.replenish",
        "environment.probe_capabilities",
        "environment.emergency_lock",
        "governance.provider.register",
        "governance.provider.enable",
        "governance.model.register",
        "governance.model.enable",
        "governance.prompt.activate",
        "governance.regression.record",
        "governance.evaluation.run",
        "governance.policy.relax",
    }
    assert not expected - offered, f"actions the workbench is never told about: {sorted(expected - offered)}"

    # An environment action carries the key, a Run action carries the Run (SRS 8.3 targetId).
    locks = [
        entry for entry in body["allowed"] + body["unavailable"]
        if entry["action"] == "environment.emergency_lock"
    ]
    assert {entry["targetId"] for entry in locks} == {"dev", "prod"}, locks

    run_id = create_run(client, harness, seeded_env)
    actions = client.get(
        f"/api/v1/workflows/{seeded_env['workflowId']}/allowed-actions", headers=OPERATOR
    ).json()
    drift = [entry for entry in actions["allowed"] + actions["unavailable"] if entry["action"] == "deployment_run.drift_check"]
    assert any(entry.get("targetId") == run_id for entry in drift), drift


def test_ac14_a_stale_hash_still_invalidates_the_approval(
    harness: Any, seeded: dict[str, Any], client: TestClient
) -> None:
    """CP-AC-24 / S5IF-AC14: existing capability, checked as a regression because UI-AC-05 depends on it."""
    from tests.helpers import create_run, drive_run  # type: ignore[import]

    run_id = create_run(client, harness, seeded)
    view = drive_run(client, harness, run_id, stop_at_deploy_gate=True)
    waiting = [g for g in view["gates"] if g["status"] == "WAITING"]
    assert waiting, view["gates"]
    gate = waiting[0]

    # The approver's page was loaded before these hashes were current.
    response = client.post(
        f"/api/v1/gates/{gate['id']}/approve",
        headers=OPERATOR,
        json={"gateType": gate["type"], "seenBoundHashes": ["sha256:stale"]},
    )
    assert response.status_code == 409, response.text
    body = response.json()
    assert body["errorCode"] == "APPROVAL_INVALIDATED"
    assert body["details"]["gateId"] == gate["id"]


def test_ac09_and_ac13_stay_available_on_the_existing_surface(
    client: TestClient, seeded_env: dict[str, Any]
) -> None:
    """The stage list and the question list keep the fields S5 renders.

    CP-AC-19 / S5IF-AC09 (the seven stage aggregates) is proved together with CP-AC-23 / S5IF-AC13, so AC09 is
    *not* asserted here; what is asserted is that typing the response did not drop
    anything the workbench already reads.
    """
    from tests.helpers import upload_input  # type: ignore[import]

    stages = client.get(f"/api/v1/workflows/{seeded_env['workflowId']}/stages", headers=OPERATOR).json()["items"]
    assert {"id", "stageType", "status", "attempt", "outputArtifactIds", "feedback"} <= set(stages[0])

    artifact_id = upload_input(client, seeded_env["id"], "mapping sheet")
    client.post(f"/api/v1/projects/{seeded_env['id']}/inputs/{artifact_id}/parse", headers=OPERATOR, json={})
    questions = client.get(
        f"/api/v1/workflows/{seeded_env['workflowId']}/questions", headers=OPERATOR
    ).json()["items"]
    for question in questions:
        for suggestion in question["suggestions"]:
            assert {"id", "text", "rationale", "changesUid", "regenerates"} <= set(suggestion)


def test_ac03_no_credential_value_leaves_s1_in_any_environment_answer(
    harness: Any, client: TestClient, seeded_env: dict[str, Any]
) -> None:
    """CP-AC-13 / S5IF-AC03 / FR-SEC-007: reference and expiry only — never a value or a mask."""
    del seeded_env
    harness.execution.add_environment(
        "secret-check",
        environment_class="TEST",
        secret_refs={"discovery": "vault://sc/discovery"},
    )
    paths = (
        "/api/v1/environments",
        "/api/v1/environments/secret-check",
        "/api/v1/environments/secret-check/protection",
        "/api/v1/environments/expiring-credentials",
    )
    for path in paths:
        response = client.get(path, headers=OPERATOR)
        assert response.status_code == 200, response.text
        body = response.text
        for forbidden in ("password", "bearer", "secretValue", "masked", "prefix"):
            assert forbidden not in body.lower(), (path, forbidden)

    listed = client.get("/api/v1/environments", headers=OPERATOR).json()["items"]
    row = [r for r in listed if r["environmentKey"] == "secret-check"][0]
    # The reference is meant to be visible; nothing beside it may be.
    assert row["secretRefs"] == {"discovery": "vault://sc/discovery"}
    assert row["credentials"][0] == {
        "scope": "discovery",
        "secretRef": "vault://sc/discovery",
        "expiresAt": row["credentials"][0]["expiresAt"],
        "label": "secret-check discovery",
    }
    assert set(row["credentials"][0]) == {"scope", "secretRef", "expiresAt", "label"}


def test_ac03b_the_credential_endpoints_never_carry_a_value(harness: Any, client: TestClient) -> None:
    """S5IF-AC03's other half, on the one endpoint that *accepts* a value (S5IF-SEC01…03).

    The test above deliberately does not list these paths: it promises the environment surface stays
    clean, and stretching it over a request that legitimately carries a credential would blur two
    different promises into one green row. Here the value goes in once, so the surfaces to check are
    every answer that could have absorbed it on the way through — the write's own response, the
    listing, the checklist, the audit trail, and a validation failure that would otherwise quote the
    input it rejected.

    The replay table gets a behavioural check rather than a query: replaying the same
    ``Idempotency-Key`` must produce a fresh write, because a recorded response would mean the
    reference — and with it the shape of the store — is now cached per key.
    """
    harness.runtime.settings.credential_master_key = "acceptance-master-key-at-least-32-characters"
    assert install_master_key_backend(harness.runtime.settings)
    sentinel = "ac03b-sk-live-Do-Not-Echo-9e1c"
    key = str(uuid4())
    headers = {**OPERATOR, "Idempotency-Key": key}
    try:
        written = client.put("/api/v1/secrets/ac03b-demo", json={"material": sentinel}, headers=headers)
        assert written.status_code == 200, written.text

        refused = client.put(
            "/api/v1/secrets/ac03b-demo",
            json={"material": {"a": sentinel}},
            headers={**OPERATOR, "Idempotency-Key": str(uuid4())},
        )
        surfaces = {
            "PUT response": written.text,
            "GET list": client.get("/api/v1/secrets", headers=OPERATOR).text,
            "GET setup": client.get("/api/v1/setup", headers=OPERATOR).text,
            "GET audit": client.get("/api/v1/audit", headers=OPERATOR).text,
            # a 422 may name the field — that is the point of the message — but never the value
            "422 body": refused.text,
        }
        assert refused.status_code == 422, refused.text
        for where, body in surfaces.items():
            assert sentinel not in body, where

        replayed = client.put("/api/v1/secrets/ac03b-demo", json={"material": sentinel}, headers=headers)
        assert replayed.status_code == 200, replayed.text
        assert replayed.json()["rotatedAt"], "the same key replayed a stored answer instead of writing again"
    finally:
        # the resolver is process-wide; a key left switched on here would silently enable the store
        # for whatever test runs next in this module
        harness.runtime.settings.credential_master_key = ""


#: Port calls that reach a target DHIS2 instance (S5IF-AC11 counts these).
DHIS2_CALLS = ("check_drift", "test_connection", "probe_capabilities", "discover", "prepare_and_validate")


def _dhys2_calls(harness: Any) -> list[str]:
    return [call for call in harness.execution.calls if call.split(":")[0] in DHIS2_CALLS]


def test_ac11_the_console_aggregates_without_touching_the_target_instance(
    harness: Any, seeded: dict[str, Any], client: TestClient
) -> None:
    """CP-AC-21 / S5IF-AC11 with FLD05: opening the panel asks DHIS2 nothing; the re-check is a POST."""
    from tests.helpers import create_run, run_stage  # type: ignore[import]

    run_id = create_run(client, harness, seeded)
    view = client.get(f"/api/v1/deployment-runs/{run_id}", headers=OPERATOR).json()
    bind = next(s for s in view["stages"] if s["stageType"] == "BIND_COMPILE")
    run_stage(client, harness, bind["id"])

    checked = client.post(
        f"/api/v1/deployment-runs/{run_id}/drift-check",
        headers={**OPERATOR, "Idempotency-Key": "drift-ac11"},
    )
    assert checked.status_code == 202, checked.text  # CP-API-012
    assert checked.json()["stepRunId"], checked.json()

    # The COMMIT's two reports, on the stage that produced them.  Without a real artifact of
    # these types the summary's `execution` block is all-None and its declared shape is never
    # exercised — which is how the console page 500'd for every identity as soon as a genuine
    # execution report existed (S5IF-G03, CP-UI-012).
    from factory_control.foundation import unit_of_work
    from factory_control.models import Stage
    from factory_control.testing.fakes import register_fake_artifact

    exec_report = register_fake_artifact(seeded["id"], "EXECUTION_REPORT", "summary-exec-report")
    import_report = register_fake_artifact(seeded["id"], "DHIS2_IMPORT_REPORT", "summary-import-report")
    with unit_of_work() as session:
        commit = session.query(Stage).filter_by(deployment_run_id=run_id, stage_type="COMMIT").one()
        commit.output_artifact_ids = [*(commit.output_artifact_ids or []), exec_report, import_report]
        session.flush()

    before = _dhys2_calls(harness)
    summary = client.get(f"/api/v1/deployment-runs/{run_id}/summary", headers=OPERATOR)
    assert summary.status_code == 200, summary.text
    body = summary.json()
    slots = body["execution"]
    for field, artifact_id in (("reportArtifactId", exec_report), ("importReportArtifactId", import_report)):
        slot = slots[field]
        assert isinstance(slot, dict) and slot["artifactId"] == artifact_id, (field, slot)
        assert isinstance(slot["status"], str) and slot["status"], (field, slot)
        assert slot["contentPurged"] is False, (field, slot)
    # FD-ART-010: the bytes can go while the row and its hash stay. The slot has to say so,
    # because reading such an artifact raises — a download link that fails on click is the exact
    # thing this field exists to prevent, and `status` cannot express it.
    from factory_control.foundation import purge_content

    purge_content(exec_report)
    after = client.get(f"/api/v1/deployment-runs/{run_id}/summary", headers=OPERATOR).json()
    purged_slot = after["execution"]["reportArtifactId"]
    assert purged_slot["artifactId"] == exec_report and purged_slot["contentPurged"] is True, purged_slot
    assert after["execution"]["importReportArtifactId"]["contentPurged"] is False, after["execution"]
    assert set(body) >= {
        "runId", "environmentClass", "status", "validation", "approval", "payload",
        "commitWindow", "verification", "execution", "changeStats", "maintenanceActions",
    }, sorted(body)
    # The recorded verdict, not a fresh probe: the re-check was the POST above.
    assert body["drift"]["drifted"] is False and body["drift"]["checkedAt"], body.get("drift")
    assert body["capabilityFreshness"]["state"] == "VALID", body.get("capabilityFreshness")
    assert body["payload"]["packageArtifactId"], body["payload"]
    assert _dhys2_calls(harness) == before, "the summary asked the target instance"


def test_bp14_the_issue_filter_is_the_providers_not_the_browsers(
    harness: Any, seeded: dict[str, Any], client: TestClient
) -> None:
    """S5IF-BP14: one Issue model, filtered and paged by the read that owns it."""
    from factory_control.foundation import unit_of_work
    from factory_control.models import Stage
    from factory_control.ports.dto import Issue
    from factory_control.testing.fakes import register_fake_artifact

    report = register_fake_artifact(seeded["id"], "VALIDATION_REPORT", "issues-report")
    harness.blueprint.register_report_issues(
        report,
        [
            Issue(
                issue_id="i1", rule_id="R-1", level="BLOCKING",
                source_stage="MAPPING", business_key="form.a", description="bad",
            ),
            Issue(
                issue_id="i2", rule_id="R-2", severity="WARNING",
                source_stage="REQUIREMENTS", business_key="form.b", description="soft",
            ),
        ],
    )
    with unit_of_work() as session:
        stage = session.get(Stage, seeded["currentStageId"])
        stage.output_artifact_ids = [*(stage.output_artifact_ids or []), report]
        session.flush()

    base = f"/api/v1/workflows/{seeded['workflowId']}/validation-issues"
    everything = client.get(base, headers=OPERATOR, params={"limit": 10})
    assert everything.status_code == 200, everything.text
    body = everything.json()
    assert {item["issueId"] for item in body["items"]} == {"i1", "i2"}, body
    # Both provider spellings came out as ``level``.
    assert {item["level"] for item in body["items"]} == {"BLOCKING", "WARNING"}, body

    blocking = client.get(base, headers=OPERATOR, params={"level": "BLOCKING"}).json()
    assert [item["issueId"] for item in blocking["items"]] == ["i1"], blocking
    staged = client.get(base, headers=OPERATOR, params={"sourceStage": "REQUIREMENTS"}).json()
    assert [item["issueId"] for item in staged["items"]] == ["i2"], staged
    keyed = client.get(base, headers=OPERATOR, params={"businessKey": "form.a"}).json()
    assert [item["issueId"] for item in keyed["items"]] == ["i1"], keyed
    rule = client.get(base, headers=OPERATOR, params={"ruleId": "R-none"}).json()
    assert rule["items"] == [] and rule["total"] == 0, rule


def test_bp11_candidates_are_searched_in_the_stored_discovery_result(
    harness: Any, seeded: dict[str, Any], client: TestClient
) -> None:
    """S5IF-BP11: S4 reads its own artifact, and no DHIS2 call happens (S5IF-UP10)."""
    from factory_control.ports.dto import DiscoveryResultInfo
    from factory_control.testing.fakes import register_fake_artifact
    from tests.helpers import create_run  # type: ignore[import]

    run_id = create_run(client, harness, seeded)
    discovery = register_fake_artifact(seeded["id"], "DISCOVERY_RESULT", "ac-bp11")
    harness.execution.add_discovery_result(
        "dev",
        DiscoveryResultInfo(
            artifact_id=discovery, project_id=seeded["id"], scope="metadata", object_count=3
        ),
    )
    harness.execution.register_discovery_objects(
        discovery,
        [
            {"dhis2Type": "dataValueSet", "code": "candidate-a", "name": "A", "uid": "u1"},
            {"dhis2Type": "dataValueSet", "code": "candidate-b", "name": "B", "uid": "u2"},
            {"dhis2Type": "program", "code": "other", "name": "C", "uid": "u3"},
        ],
    )
    base = f"/api/v1/deployment-runs/{run_id}/environment-bindings/form.a/candidates"

    before = len(_dhys2_calls(harness))
    page = client.get(base, headers=OPERATOR, params={"query": "candidate"})
    assert page.status_code == 200, page.text
    body = page.json()
    assert {item["code"] for item in body["items"]} == {"candidate-a", "candidate-b"}, body
    assert body["discoveryArtifactId"] == discovery, body
    assert len(_dhys2_calls(harness)) == before, "the candidate search touched the instance"

    typed = client.get(base, headers=OPERATOR, params={"dhis2Type": "program"}).json()
    assert [item["code"] for item in typed["items"]] == ["other"], typed
    paged = client.get(base, headers=OPERATOR, params={"limit": 1}).json()
    assert len(paged["items"]) == 1 and paged["total"] == 3, paged


def test_ac16_a_download_names_the_file_and_a_purge_is_410_either_way(
    harness: Any, seeded: dict[str, Any], client: TestClient
) -> None:
    """CP-AC-26 / S5IF-AC16 / FLD09: one exit per artifact, the file name asked for on that one."""
    from factory_control.testing.fakes import register_fake_artifact
    from tests.helpers import upload_input  # type: ignore[import]

    input_id = upload_input(client, seeded["id"], "ward list")
    inline = client.get(f"/api/v1/artifacts/{input_id}/content", headers=OPERATOR)
    assert inline.status_code == 200, inline.text
    assert "content-disposition" not in inline.headers, dict(inline.headers)

    download = client.get(
        f"/api/v1/artifacts/{input_id}/content", params={"download": "true"}, headers=OPERATOR
    )
    assert download.status_code == 200, download.text
    disposition = download.headers["content-disposition"]
    assert disposition.startswith("attachment"), disposition
    # Rule 1: an input keeps the name it was uploaded with, underscores in the ASCII form.
    assert 'filename="ward_list.csv"' in disposition, disposition
    assert "filename*=UTF-8''ward%20list.csv" in disposition, disposition

    exported = client.get(f"/api/v1/projects/{seeded['id']}/export", headers=OPERATOR)
    assert exported.status_code == 202, exported.text
    export_id = exported.json()["stepRunId"]
    named = client.get(
        f"/api/v1/artifacts/{export_id}/content", params={"download": "true"}, headers=OPERATOR
    ).headers["content-disposition"]
    # Rule 2: an export is named after the project it came from.
    assert "clinic_dashboard-export-" in named and named.endswith(".zip"), named

    other = register_fake_artifact(seeded["id"], "METADATA_PACKAGE", "package-for-name")
    typed = client.get(
        f"/api/v1/artifacts/{other}/content", params={"download": "true"}, headers=OPERATOR
    ).headers["content-disposition"]
    # Rule 3: everything else says what it is and which artifact it is.
    assert f'METADATA_PACKAGE-{other}' in typed, typed

    purged = client.post(f"/api/v1/artifacts/{other}/purge", headers=OPERATOR)
    assert purged.status_code == 200, purged.text
    for params in ({}, {"download": "true"}):
        refused = client.get(f"/api/v1/artifacts/{other}/content", params=params, headers=OPERATOR)
        assert refused.status_code == 410, (params, refused.text)
        # SRS 8.3: the purged-artifact refusal names the artifact it could not serve.
        assert refused.json()["details"]["artifactId"] == other, (params, refused.json())
        assert refused.json()["errorCode"] == "ARTIFACT_PURGED", refused.json()


def test_bp05_the_workflow_view_names_the_draft_s5_must_not_guess(
    harness: Any, seeded: dict[str, Any], client: TestClient
) -> None:
    """S5IF-BP05 【裁决】: the only hard requirement is that S1 resolves the active draft."""
    del harness
    view = client.get(f"/api/v1/workflows/{seeded['workflowId']}", headers=OPERATOR).json()
    assert view["activeDraft"]["draftId"], view.get("activeDraft")
    assert view["activeDraft"]["stage"] == "REQUIREMENTS", view["activeDraft"]
    assert "document" not in view["activeDraft"], "S1 does not forward a whole draft"

    draft = client.get(f"/api/v1/projects/{seeded['id']}/blueprint/draft", headers=OPERATOR)
    assert draft.status_code == 200, draft.text
    assert set(draft.json()) >= {
        "draftId", "baseVersion", "stage", "stageScope", "status", "contentHash",
        "promotable", "openPatchCount",
    }, draft.json()
    assert draft.json()["draftId"] == view["activeDraft"]["draftId"]


def test_ac17_a_commit_without_the_key_header_is_refused(
    harness: Any, seeded: dict[str, Any], client: TestClient
) -> None:
    """CP-AC-27 / S5IF-AC17 / G08: the header is the only place a COMMIT's key comes from."""
    from tests.helpers import finish_design_line  # type: ignore[import]

    finish_design_line(client, harness, seeded)
    created = client.post(
        f"/api/v1/workflows/{seeded['workflowId']}/deployment-runs",
        headers=OPERATOR,
        json={"environmentKey": "dev"},
    )
    assert created.status_code == 201, created.text
    run_id = str(created.json()["run"]["id"])

    missing = client.post(f"/api/v1/deployment-runs/{run_id}/commit", headers=OPERATOR, json={})
    assert missing.status_code == 400, missing.text
    assert missing.json()["errorCode"] == "IDEMPOTENCY_KEY_REQUIRED"

    # A key in the body is no longer a source, so it cannot rescue a missing header.
    body_only = client.post(
        f"/api/v1/deployment-runs/{run_id}/commit",
        headers=OPERATOR,
        json={"idempotencyKey": "not-a-source"},
    )
    assert body_only.status_code == 400, body_only.text


def test_ac10_the_dashboard_fills_both_panels_with_one_request_each(
    harness: Any, seeded: dict[str, Any], client: TestClient
) -> None:
    """CP-AC-20 **and** CP-AC-30 / S5IF-AC10 with FLD03 + FLD04: one request per panel.

    The Run panel is ordered by the most recent change, and its environment class comes
    from S4 at request time — ``cp`` has no column to put it in, which is what keeps
    D-14 true while the panel still shows it.
    """
    from factory_control.models import DeploymentRun
    from tests.helpers import create_run  # type: ignore[import]

    run_id = create_run(client, harness, seeded)

    gates = client.get("/api/v1/gates", params={"status": "WAITING"}, headers=OPERATOR)
    assert gates.status_code == 200, gates.text
    body = gates.json()
    assert set(body) >= {"items", "total", "limit", "offset"}

    runs = client.get("/api/v1/deployment-runs", headers=OPERATOR)
    assert runs.status_code == 200, runs.text
    listed = runs.json()["items"]
    assert listed and listed[0]["id"] == run_id, listed
    assert listed[0]["projectName"] == seeded["name"]
    assert listed[0]["environmentClass"] == "TEST", listed[0]

    # CP-AC-30 / S5IF-FLD04 constraint 2: the class is S4's, so S1 has nowhere to store it.
    assert "environment_class" not in {column.name for column in DeploymentRun.__table__.columns}

    unsorted = client.get("/api/v1/deployment-runs", params={"sortBy": "startedAt", "sortDir": "ASC"})
    assert unsorted.status_code == 200, unsorted.text
    bad = client.get("/api/v1/deployment-runs", params={"sortBy": "nonsense"})
    assert bad.status_code == 422, bad.text
    assert bad.json()["details"]["fields"][0]["path"] == "sortBy"


def test_ac19_the_audit_list_pages_by_cursor_and_never_counts(
    client: TestClient, seeded: dict[str, Any]
) -> None:
    """CP-AC-28 / S5IF-AC19 / G04: three pages by ``nextCursor``, and no ``total`` in the body."""
    seen: list[str] = []
    cursor: str | None = None
    for _ in range(3):
        params: dict[str, Any] = {"limit": 3}
        if cursor:
            params["cursor"] = cursor
        page = client.get("/api/v1/audit", headers=OPERATOR, params=params)
        assert page.status_code == 200, page.text
        body = page.json()
        assert "total" not in body, body
        assert "offset" not in body, body
        seen.extend(item["id"] for item in body["items"])
        cursor = body.get("nextCursor")
        if not cursor:
            break
    assert cursor is None, "the audit list never ended"
    assert len(seen) == len(set(seen)), f"a page repeated a record: {seen}"

    bad = client.get("/api/v1/audit", headers=OPERATOR, params={"cursor": "not-a-cursor"})
    assert bad.status_code == 422, bad.text
    assert bad.json()["errorCode"] == "VALIDATION_ERROR"
    assert bad.json()["details"]["fields"][0]["path"] == "cursor"


def test_ac20_running_a_stage_is_accepted_with_a_step_run_and_no_engine(
    harness: Any, seeded: dict[str, Any], client: TestClient
) -> None:
    """CP-AC-29 / S5IF-AC20 / G06: one answer shape whatever the deployment configured."""
    view = client.get(f"/api/v1/workflows/{seeded['workflowId']}", headers=OPERATOR).json()
    ready = [s for s in view["stages"] if s["status"] == "READY"][0]
    response = client.post(f"/api/v1/stages/{ready['id']}/run", headers=OPERATOR)
    assert response.status_code == 202, response.text
    body = response.json()
    assert set(body) == {"stageId", "stepRunId", "accepted"}
    assert body["stageId"] == ready["id"]
    assert body["accepted"] is True
    # The step run is a real row, so the caller can watch it on GET /stages/{id}.
    assert body["stepRunId"] in [r["id"] for r in client.get(f"/api/v1/stages/{ready['id']}", headers=OPERATOR).json()["stageRuns"]]
    harness.runtime.engine.wait(ready["id"], timeout=20)


def test_ac06_a_relaxation_is_granted_read_back_and_cannot_pass_the_ceiling(
    client: TestClient, seeded_env: Any
) -> None:
    """UI-ADM-003 / AI-POL-002: the form's two matrices, and the two refusals between them.

    The read carries the effective policy *and* the ceiling because a caller cannot use one
    without the other: what is missing from ``allowed`` but present in ``ceiling`` is exactly
    the choice a relaxation may make.  Before this, S1 answered the ceiling as the effective
    policy, so a form built on the difference could only ever offer a refusal.
    """
    del seeded_env
    before = client.get("/api/v1/governance/policy", headers=OPERATOR).json()
    assert before["isDefault"] is True
    assert before["effective"]["allowed"]["INTERNAL"] == ["LOCAL_ONLY", "PRIVATE_NETWORK"]
    assert "APPROVED_EXTERNAL" in before["ceiling"]["allowed"]["INTERNAL"]

    granted = client.post(
        "/api/v1/governance/policy/relaxations",
        headers=OPERATOR,
        json={
            "change": {"level": "INTERNAL", "addClasses": ["APPROVED_EXTERNAL"]},
            "reason": "外部托管的解析需要，已单独评审",
            "scope": "organization",
        },
    )
    assert granted.status_code == 201, granted.text
    row = granted.json()
    # the answer names what was recorded, so the operator does not have to infer it from a reload
    assert row["reason"] == "外部托管的解析需要，已单独评审"
    assert row["change"] == {"level": "INTERNAL", "addClasses": ["APPROVED_EXTERNAL"]}
    assert row["createdAt"]
    assert row["roleGate"] == "operator-name (MVP)", "D-16: no role check until M4"

    after = client.get("/api/v1/governance/policy", headers=OPERATOR).json()
    assert after["effective"]["allowed"]["INTERNAL"] == [
        "APPROVED_EXTERNAL",
        "LOCAL_ONLY",
        "PRIVATE_NETWORK",
    ], "the widening took effect and is visible on the same read"
    assert after["isDefault"] is False
    assert after["ceiling"]["allowed"] == before["ceiling"]["allowed"], (
        "granting a relaxation never moves the ceiling"
    )

    phi = client.post(
        "/api/v1/governance/policy/relaxations",
        headers=OPERATOR,
        json={"change": {"level": "PHI_PII", "addClasses": ["LOCAL_ONLY"]}, "reason": "想试试"},
    )
    assert phi.status_code == 403, phi.text
    assert phi.json()["errorCode"] == "AI_POLICY_RELAXATION_DENIED"

    past = client.post(
        "/api/v1/governance/policy/relaxations",
        headers=OPERATOR,
        json={"change": {"level": "CONFIDENTIAL", "addClasses": ["UNRESTRICTED"]}, "reason": "超出上限"},
    )
    assert past.status_code == 403, past.text
    assert past.json()["details"]["ceiling"], "the refusal says how far the level may go"

    no_change = client.post(
        "/api/v1/governance/policy/relaxations", headers=OPERATOR, json={"reason": "没有改动内容"}
    )
    assert no_change.status_code == 422, no_change.text
    no_reason = client.post(
        "/api/v1/governance/policy/relaxations",
        headers=OPERATOR,
        json={"change": {"level": "PUBLIC", "addClasses": ["UNRESTRICTED"]}, "reason": "   "},
    )
    assert no_reason.status_code == 422, no_reason.text
    assert no_reason.json()["details"]["fields"][0]["path"] == "reason"


def _questions(client: TestClient, workflow_id: str) -> list[dict[str, Any]]:
    return list(client.get(f"/api/v1/workflows/{workflow_id}/questions", headers=OPERATOR).json()["items"])
