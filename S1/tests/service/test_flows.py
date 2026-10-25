"""Service-layer coverage of the paths the acceptance rows do not exercise:
settings, uploads and artifact projections, audit queries, notification delivery,
change requests, project archiving/deletion, and the scheduled sweeps.
"""

from __future__ import annotations

import pytest

from factory_control.ports.dto import AgentRunResult, CapabilityFreshness, ExecutionOutcome, Issue
from helpers import (
    OPERATOR,
    create_run,
    drive_run,
    finish_design_line,
    make_client,
    run_stage,
)

pytestmark = pytest.mark.service


def test_system_settings_are_readable_writable_and_audited(harness) -> None:
    """CP-SET-001."""
    from factory_control.foundation import AuditQuery, query_audit, unit_of_work

    client = make_client(harness)
    keys = {item["key"] for item in client.get("/api/v1/settings").json()["items"]}
    assert {"event_retention_days", "max_upload_bytes", "allowed_upload_types"} <= keys

    updated = client.put("/api/v1/settings", json={"event_retention_days": 3}, headers=OPERATOR)
    assert updated.status_code == 200 and updated.json()["updated"] == ["event_retention_days"]
    after = client.get("/api/v1/settings").json()["items"]
    assert next(i for i in after if i["key"] == "event_retention_days")["value"] == 3

    with unit_of_work() as session:
        assert query_audit(AuditQuery(event_type="cp.settings.updated"), session=session).total >= 1

    assert client.put("/api/v1/settings", json={"nonsense": 1}, headers=OPERATOR).status_code == 404


def test_upload_type_and_size_limits(harness, seeded) -> None:
    """CP-API-009 with a machine-readable rejection reason."""
    client = make_client(harness)
    assert client.put("/api/v1/settings", json={"max_upload_bytes": 10}, headers=OPERATOR).status_code == 200

    too_big = client.post(
        f"/api/v1/projects/{seeded['id']}/inputs",
        files={"file": ("big.txt", b"x" * 500, "text/plain")},
        data={"dataClassification": "INTERNAL"},
        headers={"X-Operator": "ada"},
    )
    assert too_big.status_code == 422
    assert too_big.json()["errorCode"] == "UPLOAD_REJECTED"
    assert too_big.json()["details"]["reason"] == "SIZE"  # SRS 8.3
    assert too_big.json()["details"]["violation"] == "SIZE_EXCEEDED"

    bad_type = client.post(
        f"/api/v1/projects/{seeded['id']}/inputs",
        files={"file": ("evil.exe", b"MZ\x00", "application/x-msdownload")},
        data={"dataClassification": "INTERNAL"},
        headers={"X-Operator": "ada"},
    )
    assert bad_type.json()["details"]["reason"] == "TYPE"
    assert bad_type.json()["details"]["violation"] == "MEDIA_TYPE_NOT_ALLOWED"


def test_an_upload_that_declares_no_classification_is_refused(harness, seeded) -> None:
    """FR-REQ-001：分级由上传者声明。S1 替用户默认一个级别，就是把没说过的话写进记录。

    更要紧的是记错分级的后果：一个 PHI/PII 表格被登记成 ``CONFIDENTIAL``，后面每一道
    按 ``PHI_PII`` 拦截的策略检查都会放行，而审计里看不出有任何东西被送进过模型。
    """
    client = make_client(harness)
    refused = client.post(
        f"/api/v1/projects/{seeded['id']}/inputs",
        files={"file": ("spec.csv", b"a,b\n1,2\n", "text/csv")},
        headers={"X-Operator": "ada"},
    )
    assert refused.status_code == 422, refused.text
    assert refused.json()["errorCode"] == "VALIDATION_ERROR"
    assert "dataClassification" in str(refused.json()["details"])


def test_a_phi_input_is_recorded_as_phi_only_when_it_is_declared(harness, seeded) -> None:
    """AC-POL-02 的上游：确认勾选不是 UI 的礼貌要求，是分级能否守住 PHI 的唯一机会。"""
    client = make_client(harness)
    unconfirmed = client.post(
        f"/api/v1/projects/{seeded['id']}/inputs",
        files={"file": ("clients.csv", b"name,phone\n", "text/csv")},
        data={"dataClassification": "PHI_PII"},
        headers={"X-Operator": "ada"},
    )
    assert unconfirmed.status_code == 422, unconfirmed.text
    assert "phiDeclared" in str(unconfirmed.json()["details"])

    confirmed = client.post(
        f"/api/v1/projects/{seeded['id']}/inputs",
        files={"file": ("clients.csv", b"name,phone\n", "text/csv")},
        data={"dataClassification": "PHI_PII", "phiDeclared": "true"},
        headers={"X-Operator": "ada"},
    )
    assert confirmed.status_code == 201, confirmed.text
    assert confirmed.json()["dataClassification"] == "PHI_PII"


def test_input_upload_listing_content_and_question_flow(harness, seeded) -> None:
    """SRS 6.1 plus CP-Q-002 / CP-Q-007."""
    client = make_client(harness)
    uploaded = client.post(
        f"/api/v1/projects/{seeded['id']}/inputs",
        files={"file": ("spec.csv", b"a,b\n1,2\n", "text/csv")},
        data={"dataClassification": "INTERNAL"},
        headers={"X-Operator": "ada"},
    )
    assert uploaded.status_code == 201, uploaded.text
    artifact_id = uploaded.json()["artifactId"]

    listing = client.get(f"/api/v1/projects/{seeded['id']}/inputs").json()["items"]
    assert [item["artifactId"] for item in listing] == [artifact_id]

    content = client.get(f"/api/v1/artifacts/{artifact_id}/content")
    assert content.status_code == 200 and b"a,b" in content.content
    # FR-REQ-001: the header carries what the uploader declared, not a level S1 picked.
    assert content.headers["x-data-classification"] == "INTERNAL"

    asked = client.post(
        f"/api/v1/stages/{seeded['currentStageId']}/questions",
        json={"text": "reporting period?", "severity": "MEDIUM", "affects": ["register:vc"]},
        headers=OPERATOR,
    )
    assert asked.status_code == 201, asked.text
    question_id = asked.json()["questionId"]
    detail = client.get(f"/api/v1/questions/{question_id}").json()
    assert detail["projectId"] == seeded["id"] and detail["status"] == "OPEN"

    answered = client.post(
        f"/api/v1/questions/{question_id}/answer", json={"customText": "monthly"}, headers=OPERATOR
    )
    assert answered.status_code == 200 and answered.json()["status"] == "ANSWERED"

    twice = client.post(
        f"/api/v1/questions/{question_id}/answer", json={"customText": "weekly"}, headers=OPERATOR
    )
    assert twice.status_code == 409
    assert client.get(f"/api/v1/workflows/{seeded['workflowId']}/questions?status=OPEN").json()["items"] == []


def test_artifact_listing_diff_lineage_and_purge(harness, seeded) -> None:
    """SRS 8.1 artifact group; purge drops content but keeps metadata (FD-ART-010)."""
    client = make_client(harness)
    ids = []
    for name, body in (("v1.csv", b"a,b\n1,2\n"), ("v2.csv", b"a,b\n1,3\n")):
        uploaded = client.post(
            f"/api/v1/projects/{seeded['id']}/inputs",
            files={"file": (name, body, "text/csv")},
            data={"dataClassification": "INTERNAL"},
            headers={"X-Operator": "ada"},
        )
        ids.append(uploaded.json()["artifactId"])
    first, second = ids

    listed = client.get(f"/api/v1/workflows/{seeded['workflowId']}/artifacts").json()["items"]
    assert {a["id"] for a in listed} >= {first, second}
    assert all(a["creatorSubsystem"] == "S1" for a in listed)

    compared = client.get(f"/api/v1/artifacts/{second}/diff?against={first}").json()
    assert compared["changed"] is True

    up = client.get(f"/api/v1/artifacts/{second}/lineage").json()
    assert up["artifactId"] == second
    purged = client.post(f"/api/v1/artifacts/{first}/purge", json={}, headers=OPERATOR)
    assert purged.status_code == 200 and purged.json()["contentPurged"] is True
    after = client.get(f"/api/v1/artifacts/{first}/content")
    assert after.status_code == 410 and after.json()["errorCode"] == "ARTIFACT_PURGED"
    assert client.get("/api/v1/artifacts/art-missing").status_code == 404
    assert client.post(f"/api/v1/artifacts/{second}/reproduce", json={}, headers=OPERATOR).json()["matches"] is True


def test_audit_queries_and_chain_verification(harness, seeded) -> None:
    """SRS 8.1 audit group and AC-SEC-01 from the S1 side."""
    client = make_client(harness)
    # S5IF-G04: the audit list is a cursor list, so it carries no count at all and never
    # an offset a client could arithmetic its way through.
    from factory_control.rest.paging import encode_cursor

    first = client.get(f"/api/v1/projects/{seeded['id']}/audit", params={"limit": 1}).json()
    assert "total" not in first and "offset" not in first, first
    assert len(first["items"]) == 1
    # A cursor is S1's own token: passing one it issued moves the window, and one it did
    # not is a 422 rather than a guess.
    second = client.get(
        f"/api/v1/projects/{seeded['id']}/audit", params={"limit": 1, "cursor": encode_cursor(1)}
    ).json()
    assert {i["id"] for i in second["items"]}.isdisjoint({i["id"] for i in first["items"]})
    bad = client.get(f"/api/v1/projects/{seeded['id']}/audit", params={"cursor": "not-a-cursor"})
    assert bad.status_code == 422, bad.text
    assert bad.json()["details"]["fields"][0]["path"] == "cursor"
    whole = client.get(f"/api/v1/projects/{seeded['id']}/audit").json()
    assert "cp.project.created" in {item["eventType"] for item in whole["items"]}

    filtered = client.get("/api/v1/audit", params={"eventType": "cp.project.created"}).json()
    assert filtered["items"] and all(i["eventType"] == "cp.project.created" for i in filtered["items"])

    # UI-OPS-002: the filters narrow the query, not the page.  A project's audit list filtered to
    # one event type has to answer with only that type even though the newest 50 records would
    # have been mostly something else; filtering a page in the browser cannot say that.
    scoped = client.get(
        f"/api/v1/projects/{seeded['id']}/audit", params={"eventType": "cp.project.created"}
    ).json()
    assert scoped["items"] and all(i["eventType"] == "cp.project.created" for i in scoped["items"])
    by_operator = client.get(
        f"/api/v1/projects/{seeded['id']}/audit", params={"operatorName": "nobody-but-ada"}
    ).json()
    assert by_operator["items"] == []
    future = client.get(
        f"/api/v1/projects/{seeded['id']}/audit",
        params={"occurredAfter": "2099-01-01T00:00:00+00:00"},
    ).json()
    assert future["items"] == [], "a window that has not happened yet cannot have records"
    object_id = scoped["items"][0]["objectId"]
    traced = client.get(
        f"/api/v1/projects/{seeded['id']}/audit", params={"objectId": object_id}
    ).json()
    assert traced["items"] and all(i["objectId"] == object_id for i in traced["items"])

    assert client.post("/api/v1/audit/verify", json={}, headers=OPERATOR).json()["verified"] is True
    ranged = client.post(
        "/api/v1/audit/verify", json={"firstSequence": 1, "lastSequence": 2}, headers=OPERATOR
    ).json()
    assert ranged["verified"] is True
    assert client.get("/api/v1/audit/commit-trace/exe-none").status_code == 404


def test_project_channels_backoff_and_read_marks(harness, seeded) -> None:
    """CP-NTF-002 / CP-NTF-004."""
    client = make_client(harness)
    configured = client.put(
        f"/api/v1/projects/{seeded['id']}/settings",
        json={"notificationEmails": ["team@example.org"], "gateReminderHours": 12},
        headers=OPERATOR,
    )
    assert configured.status_code == 200
    settings = client.get(f"/api/v1/projects/{seeded['id']}/settings").json()
    assert settings["gateReminderHours"] == 12 and settings["notificationEmails"] == ["team@example.org"]

    harness.intelligence.queue_run(
        AgentRunResult(status="FAILED", issues=[Issue(issue_id="i1", severity="BLOCKING", source_stage="REQUIREMENTS")])
    )
    run_stage(client, harness, seeded["currentStageId"])

    listed = client.get(f"/api/v1/notifications?projectId={seeded['id']}").json()
    assert listed["total"] >= 1
    assert any(item["type"] == "STAGE_FAILED" for item in listed["items"])
    target = listed["items"][0]["id"]
    read = client.post(f"/api/v1/notifications/{target}/read", json={}, headers=OPERATOR)
    assert read.status_code == 200 and read.json()["read"] is True

    from factory_control.services.notifications import deliver_pending

    attempts: list[tuple[str, str]] = []

    def transport(channel: str, to: str, payload: dict) -> bool:
        attempts.append((channel, to))
        return False

    outcome = deliver_pending(harness.runtime, transport=transport)
    assert outcome["failed"] + outcome["exhausted"] >= 1
    assert ("EMAIL", "team@example.org") in attempts
    with harness.runtime.transaction(operator="system") as ctx:
        from factory_control.models import NotificationDelivery

        rows = ctx.session.query(NotificationDelivery).all()
        assert rows and all(row.attempts >= 1 for row in rows)


def test_gate_reminders_repeat_up_to_three_times(harness, seeded) -> None:
    """CP-GATE-016."""
    from datetime import timedelta

    from factory_control.foundation import unit_of_work
    from factory_control.models import Gate
    from factory_control.services.notifications import gate_reminder_due

    client = make_client(harness)
    harness.intelligence.queue_run(AgentRunResult(status="COMPLETED"))
    run_stage(client, harness, seeded["currentStageId"])

    with unit_of_work() as session:
        gate = session.query(Gate).first()
        gate.created_at = gate.created_at - timedelta(hours=100)
        gate.reminder_count = 2
        session.flush()

    reminded = gate_reminder_due(harness.runtime)
    assert reminded
    with unit_of_work() as session:
        assert session.get(Gate, reminded[0]).reminder_count == 3
    assert gate_reminder_due(harness.runtime) == []


def test_pause_resume_and_cancel_semantics(harness, seeded) -> None:
    """CP-WF-010 / CP-WF-011."""
    client = make_client(harness)
    stage_id = seeded["currentStageId"]

    assert client.post(f"/api/v1/workflows/{seeded['workflowId']}/pause", headers=OPERATOR).status_code == 200
    refused = client.post(f"/api/v1/stages/{stage_id}/run", headers=OPERATOR)
    # The claim is synchronous now (S5IF-G06), so a paused workflow refuses the run where
    # it is asked for instead of failing later inside the engine (CP-WF-011).
    assert refused.status_code == 409, refused.text
    assert refused.json()["errorCode"] == "INVALID_STATE"

    assert client.post(f"/api/v1/workflows/{seeded['workflowId']}/resume", headers=OPERATOR).status_code == 200
    actions = client.get(f"/api/v1/workflows/{seeded['workflowId']}/allowed-actions").json()
    assert any(a["action"] == "stage.run" for a in actions["allowed"])

    cancelled = client.post(f"/api/v1/workflows/{seeded['workflowId']}/cancel", headers=OPERATOR)
    assert cancelled.status_code == 200
    workflow = client.get(f"/api/v1/workflows/{seeded['workflowId']}").json()
    assert workflow["status"] == "CANCELLED" and workflow["cancelled"] is True
    # A cancelled run answers nothing about pausing it.  ``workflow_actions`` has always refused
    # (WORKFLOW_CANCELLED); the write used to accept it, which left a finished workflow carrying a
    # flag no control could lift -- the action list saying one thing and the endpoint another.
    late_pause = client.post(f"/api/v1/workflows/{seeded['workflowId']}/pause", headers=OPERATOR)
    assert late_pause.status_code == 409, late_pause.text
    assert late_pause.json()["errorCode"] == "INVALID_STATE"
    assert late_pause.json()["details"]["reasonCode"] == "WORKFLOW_CANCELLED"
    assert client.get(f"/api/v1/workflows/{seeded['workflowId']}/gates").json()["items"] == []


def test_capability_staleness_sends_the_run_back_to_bind_compile(harness, seeded) -> None:
    """CP-WF-022."""
    client = make_client(harness)
    run_id = create_run(client, harness, seeded)
    view = client.get(f"/api/v1/deployment-runs/{run_id}").json()
    bind = next(s for s in view["stages"] if s["stageType"] == "BIND_COMPILE")

    harness.execution.freshness["dev"] = CapabilityFreshness(state="EXPIRED")
    from factory_control.errors import InvalidStateError
    from factory_control.services.steps import execute_stage

    with pytest.raises(InvalidStateError) as caught:
        execute_stage(harness.runtime, bind["id"], operator="ada", idempotency_key="stale-1")
    assert caught.value.details["restartStage"] == "BIND_COMPILE"
    assert client.get(f"/api/v1/deployment-runs/{run_id}").json()["run"]["status"] == "IN_PROGRESS"


def test_full_run_reaches_verified_and_is_traceable(harness, seeded) -> None:
    """CP-WF-021 ordering plus AC-AUD-01 commit trace."""
    client = make_client(harness)
    run_id = create_run(client, harness, seeded)
    final = drive_run(client, harness, run_id)
    assert final["run"]["status"] == "VERIFIED", final
    statuses = {s["stageType"]: s["status"] for s in final["stages"]}
    assert statuses["BIND_COMPILE"] == "PASSED"
    assert statuses["STATIC_VALIDATION"] == "PASSED"
    assert statuses["DHIS2_VALIDATE"] == "PASSED"
    assert statuses["COMMIT"] == "PASSED"
    assert statuses["POST_VERIFY"] == "PASSED"

    execution_id = final["run"]["executionId"]
    assert execution_id
    trace = client.get(f"/api/v1/audit/commit-trace/{execution_id}").json()
    assert trace["environmentKey"] == "dev"
    assert trace["commitOperator"] == "ada"
    assert trace["payloadHash"]
    assert trace["gateStatus"] == "APPROVED"
    assert trace["approvals"]
    assert trace["auditTrail"]
    project = client.get(f"/api/v1/projects/{seeded['id']}").json()
    assert project["milestoneStatus"] == "VERIFIED"


def test_change_request_lifecycle_and_single_in_progress(harness, seeded) -> None:
    """CP-CR-001 … CP-CR-007."""
    client = make_client(harness)
    finish_design_line(client, harness, seeded)

    created = client.post(
        f"/api/v1/projects/{seeded['id']}/change-requests",
        json={"reason": "add vaccination register", "affectedBusinessKeys": ["dataElement:vc"]},
        headers=OPERATOR,
    )
    assert created.status_code == 201, created.text
    cr_id = created.json()["id"]
    assert created.json()["status"] == "DRAFT"

    assert client.post(f"/api/v1/change-requests/{cr_id}/start", headers=OPERATOR).status_code == 409
    analyzed = client.post(f"/api/v1/change-requests/{cr_id}/impact", headers=OPERATOR)
    assert analyzed.status_code == 200, analyzed.text
    assert analyzed.json()["status"] == "ANALYZED"
    assert analyzed.json()["impact"]["affectedStages"]
    assert client.post(f"/api/v1/change-requests/{cr_id}/start", headers=OPERATOR).status_code == 409

    rejected = client.post(
        f"/api/v1/change-requests/{cr_id}/reject", json={"reason": "not now"}, headers=OPERATOR
    )
    assert rejected.status_code == 200 and rejected.json()["status"] == "REJECTED"
    assert client.post(f"/api/v1/change-requests/{cr_id}/approve", headers=OPERATOR).status_code == 409

    def new_cr(reason: str, key: str) -> str:
        body = client.post(
            f"/api/v1/projects/{seeded['id']}/change-requests",
            json={"reason": reason, "affectedBusinessKeys": [key]},
            headers=OPERATOR,
        ).json()
        client.post(f"/api/v1/change-requests/{body['id']}/impact", headers=OPERATOR)
        client.post(f"/api/v1/change-requests/{body['id']}/approve", headers=OPERATOR)
        return body["id"]

    approved_id = new_cr("second", "a:b")
    started = client.post(f"/api/v1/change-requests/{approved_id}/start", headers=OPERATOR)
    assert started.status_code == 200, started.text
    assert started.json()["status"] == "IN_PROGRESS"

    competing = new_cr("third", "c:d")
    blocked = client.post(f"/api/v1/change-requests/{competing}/start", headers=OPERATOR)
    assert blocked.status_code == 409  # CP-CR-006
    assert client.post(f"/api/v1/change-requests/{approved_id}/complete", headers=OPERATOR).status_code == 409
    # CP-CR-002: only DRAFT and ANALYZED may be cancelled, so an approved request
    # cannot be withdrawn this way.
    assert client.post(f"/api/v1/change-requests/{competing}/cancel", headers=OPERATOR).status_code == 409

    draft = client.post(
        f"/api/v1/projects/{seeded['id']}/change-requests",
        json={"reason": "fourth", "affectedBusinessKeys": ["e:f"]},
        headers=OPERATOR,
    ).json()
    assert client.post(f"/api/v1/change-requests/{draft['id']}/cancel", headers=OPERATOR).status_code == 200
    assert len(client.get(f"/api/v1/projects/{seeded['id']}/change-requests").json()["items"]) == 4


def test_an_authorized_request_hands_its_deletion_candidates_to_the_compile(harness, seeded) -> None:
    """CP-CR-005, BP-CMP-026 and 总体 SRS 15.6: the request names its retirements, and R1 delivers them.

    Two breaks sat on this one path.  R1 fetched the project's IN_PROGRESS request and then asked it
    to be APPROVED — which never matched, because CP-CR-002 is what moves APPROVED to IN_PROGRESS —
    so no compile learned which request authorized it.  And nothing could have named a candidate even
    if it had: ``CreateChangeRequestBody`` had no field for it, and ``analyze`` refilled the row from
    S2's impact read, which has no deletion field at all on the real provider.  Either way
    ``deletionCandidates`` stayed empty, BP-CMP-026 admitted nothing, and DX-EXE-038's directed
    deletion had no payload to submit for any project that actually exists.
    """
    client = make_client(harness)
    finish_design_line(client, harness, seeded)

    retiring = "dataElement:oldWeight"
    harness.blueprint.registry_refs[seeded["id"]] = [
        {"businessKey": retiring, "dhis2Type": "dataElement", "disposition": "DEPRECATED", "uid": "oldWeight001"},
    ]

    created = client.post(
        f"/api/v1/projects/{seeded['id']}/change-requests",
        json={
            "reason": "retire the weight element the program no longer records",
            "affectedBusinessKeys": [retiring],
            "deletionCandidates": [retiring],
        },
        headers=OPERATOR,
    )
    assert created.status_code == 201, created.text
    cr_id = created.json()["id"]
    assert created.json()["deletionCandidates"] == [retiring], "the request must state its own retirements"

    analyzed = client.post(f"/api/v1/change-requests/{cr_id}/impact", headers=OPERATOR)
    assert analyzed.status_code == 200, analyzed.text
    assert analyzed.json()["impact"]["deletionCandidates"] == [retiring], (
        "the impact report must carry what was authorized, not what S2's impact read never had"
    )
    assert client.post(f"/api/v1/change-requests/{cr_id}/approve", headers=OPERATOR).status_code == 200
    assert client.post(f"/api/v1/change-requests/{cr_id}/start", headers=OPERATOR).json()["status"] == "IN_PROGRESS"

    # CP-CR-006 reopened the main line at MAPPING, so the line has to close again before a Run starts.
    finish_design_line(client, harness, seeded)
    final = drive_run(client, harness, create_run(client, harness, seeded))
    assert final["run"]["status"] == "VERIFIED", final

    compile_candidates = harness.blueprint.compile_deletion_candidates
    assert list(compile_candidates) == ["dev"], compile_candidates
    assert compile_candidates["dev"] == [retiring]


def test_a_request_may_only_retire_a_deprecated_registry_entry(harness, seeded) -> None:
    """CP-CR-005: the authorization is checked where it is given, not dropped at the compile.

    A candidate that is not DEPRECATED would be filtered out silently by BP-CMP-026 at compile time,
    which reads as success to the operator while the object stays on the instance.
    """
    client = make_client(harness)
    finish_design_line(client, harness, seeded)
    harness.blueprint.registry_refs[seeded["id"]] = [
        {"businessKey": "dataElement:kept", "disposition": "CREATE", "uid": "kept000001"},
        {"businessKey": "dataElement:retired", "disposition": "DEPRECATED", "uid": "retired001"},
    ]

    refused = client.post(
        f"/api/v1/projects/{seeded['id']}/change-requests",
        json={"reason": "clean up", "deletionCandidates": ["dataElement:kept", "dataElement:nowhere"]},
        headers=OPERATOR,
    )
    assert refused.status_code == 422, refused.text
    assert refused.json()["errorCode"] == "VALIDATION_ERROR"
    assert sorted(refused.json()["details"]["notDeprecated"]) == ["dataElement:kept", "dataElement:nowhere"]

    allowed = client.post(
        f"/api/v1/projects/{seeded['id']}/change-requests",
        json={"reason": "clean up", "deletionCandidates": ["dataElement:retired"]},
        headers=OPERATOR,
    )
    assert allowed.status_code == 201, allowed.text
    assert allowed.json()["deletionCandidates"] == ["dataElement:retired"]


def test_execution_views_are_forwarded_not_stored(harness, seeded) -> None:
    """CP-API-007."""
    client = make_client(harness)
    harness.execution.executions["exe-1"] = ExecutionOutcome(
        execution_id="exe-1", status="COMPLETED", report_artifact_id="art-r", applied=True
    )
    assert client.get("/api/v1/executions/exe-1").json()["status"] == "COMPLETED"
    assert client.get("/api/v1/executions/exe-1/report").json()["reportArtifactId"] == "art-r"
    reconciled = client.post("/api/v1/executions/exe-1/reconcile", json={}, headers=OPERATOR)
    assert reconciled.status_code == 200 and reconciled.json()["outcome"] == "APPLIED"
    assert client.get("/api/v1/executions/none").status_code == 404


def test_scheduled_sweeps_all_run_clean(harness, seeded) -> None:
    """CP-SET-002."""
    from factory_control import scheduled
    from factory_control.testing.fakes import ExpiringCredentialSeed

    harness.execution.expiring.append(
        ExpiringCredentialSeed(environment_key="dev", secret_ref="vault://dx/dev")
    )
    results = scheduled.run_all_once(harness.runtime)
    assert set(results) == set(scheduled.JOBS)
    failures = {name: value for name, value in results.items() if "error" in value}
    assert not failures, failures
    assert scheduled.run_all_once(harness.runtime, jobs=["link_audit"])["link_audit"]["linked"] >= 0
    notifications = client_notifications(harness)
    assert any(item["type"] == "CREDENTIAL_EXPIRING" for item in notifications)


def client_notifications(harness) -> list[dict]:
    from factory_control.services.notifications import list_notifications

    return list_notifications(harness.runtime, project_id=None)["items"]


def test_target_environment_registration_rules(harness, seeded) -> None:
    """CP-PRJ-002/003/004."""
    client = make_client(harness)
    no_reference = client.put(
        f"/api/v1/projects/{seeded['id']}/target-environments",
        json={"targetEnvironments": [{"environmentKey": "dev"}, {"environmentKey": "prod"}]},
        headers=OPERATOR,
    )
    assert no_reference.status_code == 409

    replaced = client.put(
        f"/api/v1/projects/{seeded['id']}/target-environments",
        json={"targetEnvironments": [
            {"environmentKey": "dev", "isDesignReference": True, "promotionOrder": 10},
            {"environmentKey": "prod", "promotionOrder": 20},
        ]},
        headers=OPERATOR,
    )
    assert replaced.status_code == 200, replaced.text
    assert replaced.json()["designReference"] == "dev"
    assert {t["environmentKey"] for t in client.get(
        f"/api/v1/projects/{seeded['id']}/target-environments"
    ).json()["items"]} == {"dev", "prod"}

    ghost = client.put(
        f"/api/v1/projects/{seeded['id']}/target-environments",
        json={"targetEnvironments": [{"environmentKey": "ghost", "isDesignReference": True}]},
        headers=OPERATOR,
    )
    assert ghost.status_code == 409  # CP-PRJ-002: S4 must know the key


def test_archive_export_and_delete_sequence(harness, seeded) -> None:
    """CP-PRJ-005/006/007 with CP-BR-10/11."""
    client = make_client(harness)
    project_id = seeded["id"]
    assert client.delete(f"/api/v1/projects/{project_id}", headers=OPERATOR).status_code == 409

    upgraded = client.post(f"/api/v1/projects/{project_id}/upgrade-mode", headers=OPERATOR)
    assert upgraded.status_code == 200, upgraded.text
    assert upgraded.json()["deliveryMode"] == "B"
    by_type = {s["stageType"]: s["status"] for s in client.get(f"/api/v1/projects/{project_id}").json()["stages"]}
    assert by_type["PAGE_DESIGN"] == "READY" and by_type["HANDOFF"] == "LOCKED"
    assert client.post(f"/api/v1/projects/{project_id}/upgrade-mode", headers=OPERATOR).status_code == 409

    archived = client.post(f"/api/v1/projects/{project_id}/archive", headers=OPERATOR)
    assert archived.status_code == 200 and archived.json()["archived"] is True
    blocked = client.patch(f"/api/v1/projects/{project_id}", json={"name": "x"}, headers=OPERATOR)
    assert blocked.status_code == 409 and blocked.json()["errorCode"] == "PROJECT_ARCHIVED"

    exported = client.get(f"/api/v1/projects/{project_id}/export", headers=OPERATOR)
    assert exported.status_code == 202, exported.text  # CP-API-012

    deleted = client.delete(f"/api/v1/projects/{project_id}", headers=OPERATOR)
    assert deleted.status_code == 202, deleted.text
    assert deleted.json()["deleted"] is True
    assert client.get(f"/api/v1/projects/{project_id}").status_code == 404
    for calls in (harness.blueprint.calls, harness.intelligence.calls, harness.execution.calls):
        assert f"delete_project_data:{project_id}" in calls


def test_error_envelope_localizes_and_carries_correlation(harness) -> None:
    """CP-API-008 with CP-NFR-09 / CP-API-006."""
    client = make_client(harness)
    zh = client.post(
        "/api/v1/projects", json={"name": "", "ownerName": "a"},
        headers={"X-Operator": "ada", "Accept-Language": "zh-CN"},
    )
    en = client.post(
        "/api/v1/projects", json={"name": "", "ownerName": "a"},
        headers={"X-Operator": "ada", "Accept-Language": "en"},
    )
    assert zh.status_code == en.status_code == 422
    assert zh.json()["errorCode"] == en.json()["errorCode"] == "VALIDATION_ERROR"
    assert zh.json()["message"] != en.json()["message"]
    assert zh.json()["correlationId"] and zh.headers["x-correlation-id"]
    assert {"path": "name", "message": "name is required"} in zh.json()["details"]["fields"]


def test_authorization_hook_is_swappable(harness, seeded) -> None:
    """CP-IAM-001."""
    from factory_control import iam

    class DenyArchives:
        def is_allowed(self, *, operator: str, project_id, action: str) -> bool:
            return action != iam.PROJECT_ARCHIVE

    client = make_client(harness)
    original = iam.get_permission_policy()
    iam.set_permission_policy(DenyArchives())
    try:
        denied = client.post(f"/api/v1/projects/{seeded['id']}/archive", headers=OPERATOR)
        assert denied.status_code == 403 and denied.json()["errorCode"] == "PERMISSION_DENIED"
    finally:
        iam.set_permission_policy(original)
    assert client.post(f"/api/v1/projects/{seeded['id']}/archive", headers=OPERATOR).status_code == 200


def test_an_environment_that_never_answered_its_connection_test_gets_no_run(harness, seeded) -> None:
    """AC-CAP-02 / FR-CAP-001: S1 refuses to deploy to an instance it cannot serve.

    Whether the version a target runs is one the Factory has a Profile for is S4's answer to
    give, not S1's to guess (P-10) — but S1 is the one that opens a Deployment Run, so it is
    also the one that has to *honour* that answer.  Without this the Run would be created and
    every later step would run against an instance the Factory never reached.
    """
    from factory_control.ports.dto import ConnectionCheck

    client = make_client(harness)
    finish_design_line(client, harness, seeded)

    # the connection test seed_project recorded is gone, exactly as for a fresh v2.39 instance
    harness.execution.connections.pop("dev", None)
    refused = client.post(
        f"/api/v1/workflows/{seeded['workflowId']}/deployment-runs",
        json={"environmentKey": "dev"},
        headers=OPERATOR,
    )
    assert refused.status_code == 409, refused.text
    assert refused.json()["errorCode"] == "INVALID_STATE"
    assert refused.json()["details"]["reason"] == "NO_CONNECTION_TEST"

    # and a test that ran and *failed* — the version is not covered by any Profile
    harness.execution.connections["dev"] = ConnectionCheck(
        ok=False,
        result="FAILED",
        environment_key="dev",
        version="2.39.2",
        checks=[{"name": "versionProfile", "state": "FAILED", "detail": "没有覆盖 2.39.2 的版本 Profile"}],
    )
    refused_again = client.post(
        f"/api/v1/workflows/{seeded['workflowId']}/deployment-runs",
        json={"environmentKey": "dev"},
        headers=OPERATOR,
    )
    assert refused_again.status_code == 409, refused_again.text
    assert refused_again.json()["details"]["reason"] == "CONNECTION_TEST_FAILED"
    assert "versionProfile" in refused_again.json()["details"]["failedChecks"]
    runs = client.get(f"/api/v1/workflows/{seeded['workflowId']}/deployment-runs", headers=OPERATOR).json()
    assert runs["items"] == [], "the refusal created nothing behind itself"


def test_moving_the_design_reference_invalidates_discovery_and_what_followed(harness, seeded) -> None:
    """CP-PRJ-003 / FR-ENV-007: the mapping was designed against *that* instance's discovery.

    Re-pointing the design reference does not produce a new Blueprint version, so no version
    propagation fires.  What it does change is the existing-metadata index, the observed
    baselines behind every REUSE / REFERENCE / UPDATE entry and the default objects the
    compiler references — the evidence DISCOVERY and MAPPING stood on.
    """
    from factory_control import store as S
    from factory_control.domain.enums import StageStatus, StageType
    from factory_control.foundation import unit_of_work

    client = make_client(harness)
    finish_design_line(client, harness, seeded)

    replaced = client.put(
        f"/api/v1/projects/{seeded['id']}/target-environments",
        json={
            "targetEnvironments": [
                {"environmentKey": "dev", "promotionOrder": 20},
                {"environmentKey": "prod", "isDesignReference": True, "promotionOrder": 10},
            ]
        },
        headers=OPERATOR,
    )
    assert replaced.status_code == 200, replaced.text
    body = replaced.json()
    assert body["designReferenceChanged"] is True
    assert body["invalidatedStages"], "the change was reported but nothing it broke was invalidated"

    with unit_of_work() as session:
        statuses = {
            stage.stage_type: stage.status
            for stage in S.stages_of(session, seeded["workflowId"])
            if stage.deployment_run_id is None
        }
    assert statuses[StageType.DISCOVERY.value] == StageStatus.INVALIDATED.value
    assert statuses[StageType.MAPPING.value] == StageStatus.INVALIDATED.value
    assert statuses[StageType.BASELINE_COMPILE.value] == StageStatus.INVALIDATED.value
    assert statuses[StageType.REQUIREMENTS.value] == StageStatus.PASSED.value, (
        "the requirement and domain work does not depend on which instance was discovered"
    )


def test_cancelling_a_workflow_reaches_the_provider_call_running_now(harness, seeded) -> None:
    """CP-WF-011 / FR-WF-004: a cancelled workflow must stop telling its workers to carry on.

    Every provider call used to go out with ``cancel=None``: the S3 agent run was stopped
    out-of-band, but a discovery, a compile or a server-side validation kept running to its
    natural end -- holding the environment commit lock (which is never released on a timeout)
    and continuing to write artifacts into a workflow the user had already abandoned.
    """
    from factory_control import progress
    from factory_control import store as S
    from factory_control.domain.enums import StageStatus, StageType
    from factory_control.foundation import ManualCancellationToken

    client = make_client(harness)
    finish_design_line(client, harness, seeded)

    captured: dict[str, object] = {}
    real_discover = harness.execution.discover

    def spy(environment_key: str, project_id, scope, explicit_refs, **kwargs):  # type: ignore[no-untyped-def]
        captured["cancel"] = kwargs.get("cancel")
        return real_discover(environment_key, project_id, scope, explicit_refs, **kwargs)

    harness.execution.discover = spy  # type: ignore[method-assign]
    try:
        with harness.runtime.transaction(operator="ada") as ctx:
            discovery = S.stage_by_type(ctx.session, seeded["workflowId"], StageType.DISCOVERY)
            discovery.status = StageStatus.READY.value
            discovery_id = discovery.id
        outcome = run_stage(client, harness, discovery_id)
    finally:
        harness.execution.discover = real_discover  # type: ignore[method-assign]
    assert "error" not in outcome, outcome

    token = captured.get("cancel")
    assert isinstance(token, ManualCancellationToken), (
        f"the discovery call was handed {token!r}; a running step cannot be cancelled through None"
    )
    assert token.cancelled is False, "the step finished normally, so nothing should have fired"
    assert progress.live_token_count(str(seeded["workflowId"])) == 0, (
        "a finished stage must release its token, or the table fills with dead entries"
    )

    # a step still in flight does get the signal
    live = progress.register_cancellation_token(str(seeded["workflowId"]))
    assert progress.cancel_workflow_tokens(str(seeded["workflowId"]), reason="WORKFLOW_CANCELLED") == 1
    assert live.cancelled is True and live.reason == "WORKFLOW_CANCELLED"
