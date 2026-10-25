"""S1's deletion door against the *real* S4 (DX-EXE-038, FR-TST-002③, CP-API-007).

``tests/api/test_environment_deletion.py`` proves the request shape on S1's own double.  This file
proves the thing a double cannot: that S1's new path reaches ``factory_dhis2.api.delete_objects``
over the real adapter, that the deletion actually goes out to the DHIS2 instance S4 talks to, and
— the half that matters most on a shared public demo instance — that **S4's own safeguards stay
in front of the instance when S1 is the caller**:

* a ``deletionPolicy=FORBID`` environment refuses before anything is submitted (DX-EXE-038);
* an object whose associated-data check cannot be completed is treated as having data, and the
  whole batch is refused (DX-QRY-004's conservative direction);
* an emergency lock refuses a write-class execution (DX-ENV-007, DX-BR-11);
* a repeated idempotency key never deletes twice (NFR-REL-003).

S1 deliberately implements none of those.  Duplicating them on this side would give a caller two
guards that can disagree, and the weaker one would win — which for an irreversible operation is
the worst possible way to be consistent.  What is asserted here is therefore mostly about
requests the instance *did not* get.

The instance is S4's own in-memory DHIS2 behind its httpx transport (``FakeDhis2Transport``), so
nothing is contacted and every request the run made is countable.  It models a committed delete as
an ImportReport rather than by forgetting the row, so the proof below is the recorded DELETE and
the instance's ``deletes_accepted`` counter, not the absence of an object.
"""

from __future__ import annotations

import io
import os
import zipfile
from collections.abc import Iterator
from typing import Any

import pytest
from fastapi.testclient import TestClient

from factory_control.foundation import (
    ArtifactCreate,
    ArtifactStatus,
    DataClassification,
    register_artifact,
    stage_content,
    unit_of_work,
)
from factory_control.testing.harness import (
    ControlHarness,
    control_harness,
    has_real_provider,
)

pytestmark = pytest.mark.skipif(
    not (has_real_provider("S2") and has_real_provider("S4")),
    reason="needs factory_blueprint and factory_dhis2 installed (scripts/install_dev.sh)",
)

OPERATOR = {"X-Operator": "ada", "Content-Type": "application/json"}
PROJECT = "prj-s1-delete"
#: the object the deletion payloads name, seeded onto the in-memory instance
UID = "s1delete001"
CODE = "FT_S1RUN_weight"
#: what the in-memory instance answers to an import: a report, in 2.41's shape (DX-EXE-027).
#: Two are queued per deletion — VALIDATE first, then the COMMIT of the same payload (DX-BR-05).
_OK_DELETE_REPORT: dict[str, Any] = {
    "status": "OK",
    "response": {
        "status": "SUCCESS",
        "stats": {"created": 0, "updated": 0, "deleted": 1, "ignored": 0},
        "objects": [],
    },
}


def _environment_body(key: str, environment_class: str) -> dict[str, Any]:
    body: dict[str, Any] = {
        "environmentKey": key,
        "name": f"{key} 实例（S1 删除链路）",
        "baseUrl": f"https://s1delete-{key}.local/dhis2",
        "environmentClass": environment_class,
        # 2.41 is the series S4's deletion resource ``metadata.delete`` is catalogued for
        "versionConstraint": "2.41.x",
        "authMode": "BASIC",
        "tlsRequired": True,
        "timeoutSeconds": 30,
        "credentials": [
            {"scope": "discovery", "secretRef": f"env://S1DELETE_{key.upper()}_DISCOVERY"},
            {"scope": "import", "secretRef": f"env://S1DELETE_{key.upper()}_IMPORT"},
        ],
    }
    if environment_class == "PRODUCTION":
        # DX-ENV-002 requires the windows; DX-ENV-002 also makes FORBID the production default
        # deletion policy, which is the refusal this file needs and must not override.
        body["protection"] = {
            "timezone": "UTC",
            "commitWindows": [{"days": [2, 4], "start": "01:00", "end": "05:00"}],
        }
    return body


@pytest.fixture
def chain() -> Iterator[tuple[ControlHarness, TestClient, Any]]:
    """Real S2 + real S4, one in-memory instance, both environments probed and tested.

    Per test, not per module: these assertions read absolute counters (``deletes_accepted == 0``)
    and a shared ``scripted`` queue, so a module-scoped instance makes an earlier test's delete
    answer for a later one — and a queued failure gets consumed by the wrong request.

    Probed and tested because S4 will not execute against an environment it has never described
    (DX-CAP-003) and will not plan an import whose account authority it has not seen
    (DX-EXE-012) — both reads come from rows S4 persisted, so a deletion needs the two calls to
    have happened first, exactly as a real deployment does them at registration.
    """
    from factory_dhis2 import deps
    from factory_dhis2.capability.runtime_profile import clear_cache
    from factory_dhis2.testing.instance import FakeDhis2Instance
    from factory_dhis2.testing.transport import FakeDhis2Transport

    for key in ("dev", "prod"):
        os.environ[f"S1DELETE_{key.upper()}_DISCOVERY"] = "svcacct-reader:only-read"
        os.environ[f"S1DELETE_{key.upper()}_IMPORT"] = "svcacct-writer:metadata"
    try:
        instance = FakeDhis2Instance()
        instance.authorities = ["F_DATAELEMENT_ADD", "ALL"]
        instance.roles = [{"id": "r1", "name": "Superuser"}]
        # the object the deletion payloads name: put it on the instance so "delete this" is a
        # request about something that exists rather than about a fictional uid
        instance.put("dataElement", UID, code=CODE, name="体重", value_type="NUMBER")
        instance.put("categoryCombo", "cc1", code="default", name="default")

        with control_harness(providers="real", real=("S2", "S4")) as harness:
            deps.set_transport(FakeDhis2Transport(instance))
            client = TestClient(_app(harness))
            for key in ("dev", "prod"):
                created = client.post(
                    "/api/v1/environments", headers=OPERATOR, json=_environment_body(key, _class_of(key))
                )
                assert created.status_code == 201, created.text
                tested = client.post(
                    f"/api/v1/environments/{key}/test-connection", headers=OPERATOR, json={}
                )
                assert tested.status_code == 200, tested.text
                assert tested.json()["ok"] is True, tested.json()
                probed = client.post(f"/api/v1/environments/{key}/probe-capabilities", headers=OPERATOR)
                assert probed.status_code == 200, probed.text
            instance.requests.clear()
            instance.imports_accepted = 0
            instance.deletes_accepted = 0
            try:
                yield harness, client, instance
            finally:
                deps.reset_providers()
                clear_cache()
    finally:
        for name in tuple(os.environ):
            if name.startswith("S1DELETE_"):
                del os.environ[name]


def _class_of(key: str) -> str:
    return "PRODUCTION" if key == "prod" else "TEST"


def _app(harness: ControlHarness) -> Any:
    from factory_control.rest.app import create_app

    return create_app(harness.runtime)


# --- the deletion payload ---------------------------------------------------


def register_deletion_package(
    *, environment_key: str, uid: str = UID, dhis2_type: str = "dataElement", project_id: str = PROJECT, seed: str
) -> tuple[str, str]:
    """Build and register the artifact S4's ``delete_objects`` takes, and return (id, hash).

    It is a payload of its own (DX-EXE-038): the candidate object set, plus the manifest's
    ``deletionCandidates`` section S4 reads the candidates from (``execution/deletion.py``
    ``deletion_candidates``).  S1 never parses either — it hands the id and the hash over.

    The candidate rows are written in the shape S2's compiler emits
    (``factory_blueprint/compilers/metadata.py``, ``businessKey`` / ``dhis2Type`` / ``uid`` /
    ``reason``), which is the seam this file exists to close: a DEPRECATED object leaves
    ``manifest.objects``, so S4's businessKey back-fill cannot reach it and the row must carry its
    own address.  ``S2/tests/unit/test_compile_rules.py`` pins the producer side of the same shape.

    Registered as S2's, because the artifact catalogue makes METADATA_PACKAGE S2's to create
    (FD-ART-002) and S4's own contract suite registers it the same way for the same reason:
    a test assembling a payload is the one legitimate place for it.
    """
    from factory_foundation.api import file_json, structure_hash

    payload = {
        "@DELETE": [{"id": uid, "dhis2Type": dhis2_type, "code": CODE}],
        # what DHIS2's ``DELETE /api/metadata`` is sent: the objects to remove, addressed by id
        "dataElements": [{"id": uid, "code": CODE, "name": "体重"}],
    }
    business_key = f"dataElement.{CODE}"
    manifest = {
        "schemaVersion": 1,
        "projectId": project_id,
        "blueprintVersion": "0.1.0",
        "environmentKey": environment_key,
        "buildRecord": {
            "buildId": f"bld-delete-{seed}",
            "kind": "ENVIRONMENT_BOUND",
            "payloadHash": structure_hash(payload),
            "contractTest": True,
        },
        "objects": [
            {
                "businessKey": business_key,
                "dhis2Type": dhis2_type,
                "uid": uid,
                "code": CODE,
                "disposition": "DEPRECATED",
                "state": "DEPRECATED",
            }
        ],
        "payloadFiles": {"metadata.json": structure_hash(payload)},
        "dispositions": {business_key: "DEPRECATED"},
        "provenance": {"blueprint": {"type": "BLUEPRINT", "artifactHash": "sha256:" + "0" * 64}},
        "managedScope": [
            {"businessKey": business_key, "dhis2Type": dhis2_type, "uid": uid, "disposition": "DEPRECATED"}
        ],
        "observationBaselines": [],
        "uidToBusinessKey": [
            {
                "businessKey": business_key,
                "uid": uid,
                "dhis2Type": dhis2_type,
                "code": CODE,
                "disposition": "DEPRECATED",
                "variesByEnvironment": False,
            }
        ],
        "controlledFields": {},
        "environmentDependentObjects": [],
        "expressionValidation": [],
        "deletionCandidates": [
            {
                "businessKey": business_key,
                "dhis2Type": dhis2_type,
                "uid": uid,
                "reason": "S1 删除链路：本次运行创建的合成对象",
            }
        ],
        "changeSummary": {
            "environmentKey": environment_key,
            "blueprintVersion": "0.1.0",
            "byType": {dhis2_type: {"create": 0}},
        },
        "notes": ["S1 删除链路测试包：单个删除候选"],
    }

    archive = io.BytesIO()
    with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED) as bundle:
        bundle.writestr("metadata.json", file_json(payload))
        bundle.writestr("metadata_manifest.json", file_json(manifest))
    # staging is external I/O and the registration opens its own short transaction (FD-ART-005)
    handle = stage_content(
        iter([archive.getvalue()]),
        project_id=project_id,
        artifact_type="METADATA_PACKAGE",
        media_type="application/zip",
    )
    with unit_of_work() as session:
        row = register_artifact(
            handle,
            ArtifactCreate(
                project_id=project_id,
                type="METADATA_PACKAGE",
                media_type="application/zip",
                data_classification=DataClassification.INTERNAL,
                creator_subsystem="S2",
                status=ArtifactStatus.APPROVED,
                build_record=manifest["buildRecord"],
            ),
            session=session,
        )
    return str(row.id), str(row.content_hash)


def _delete(
    client: TestClient,
    environment_key: str,
    artifact: tuple[str, str],
    *,
    key: str,
    project_id: str = PROJECT,
) -> Any:
    return client.post(
        f"/api/v1/environments/{environment_key}/delete-objects",
        headers={**OPERATOR, "Idempotency-Key": key},
        json={
            "projectId": project_id,
            "deletionArtifactId": artifact[0],
            "expectedHash": artifact[1],
        },
    )


def _writes(instance: Any) -> list[dict[str, Any]]:
    """Every request that could change the instance: a method other than GET."""
    return [row for row in instance.requests if str(row["method"]).upper() != "GET"]


# --- S1 reaches S4's executor, and the instance is asked to delete ------------


def test_s1_deletes_the_candidate_through_the_real_s4_and_the_instance_is_asked(
    chain: Any,
) -> None:
    """The gap this closes: ``delete_objects`` was in S4's API and not in S1's METHOD_MAP.

    Asserted in the order DX-EXE-038 requires, because the order is the safety: the payload is
    VALIDATED first, the associated-data check is redone after that validation, and only then is
    the same payload submitted for deletion — so "nothing else was written" is checked too.
    """
    _harness, client, instance = chain
    for _ in range(2):
        instance.queue_import_report(dict(_OK_DELETE_REPORT))
    artifact = register_deletion_package(environment_key="dev", seed="happy")
    before = len(instance.requests)

    try:
        response = _delete(client, "dev", artifact, key="s1-delete-happy")
    finally:
        instance.scripted.clear()

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["status"] == "COMPLETED", body
    assert body["applied"] is True, body
    assert body["environmentKey"] == "dev", body
    assert body["deletionArtifactId"] == artifact[0], body
    assert body["payloadHash"] == artifact[1], body
    assert body["executionId"], body

    asked = instance.requests[before:]
    deletion = [row for row in asked if str(row["method"]).upper() == "DELETE"]
    assert len(deletion) == 1, [dict(row, body="<omitted>") for row in asked]
    assert deletion[0]["path"] == "metadata", deletion[0]
    params = {str(key): str(value) for key, value in (deletion[0]["query"] or {}).items()}
    assert params["importStrategy"] == "DELETE", params
    assert params["importMode"] == "COMMIT", params
    assert params["atomicMode"] == "ALL", "删一半比不删更难解释：删除按原子模式提交 (DX-EXE-038)"
    assert instance.deletes_accepted == 1, instance.deletes_accepted

    # the payload is validated before it is deleted (DX-BR-05), and nothing else is written
    writes = _writes(instance)
    assert [str(row["method"]).upper() for row in writes] == ["POST", "DELETE"], writes
    validated = writes[0]
    assert str(validated["method"]).upper() == "POST" and validated["path"] == "metadata"
    assert {
        str(k): str(v) for k, v in (validated["query"] or {}).items()
    }["importMode"] == "VALIDATE", validated["query"]

    # and the check that can stop it ran between the two (DX-QRY-004)
    reads = [str(row["path"]) for row in asked]
    assert any(path == "dataValues" for path in reads), reads


def test_the_same_key_never_deletes_twice(chain: Any) -> None:
    """NFR-REL-003 on the port, not just behind the middleware.

    Called through the port rather than over HTTP on purpose: the HTTP layer would answer a replay
    from ``cp.idempotent_responses`` and never reach S4, which would prove S1's replay table and
    leave S4's own guarantee untested.  A caller that hits the library directly — the worker, a
    retry after a lost response — must get the same "delete once" answer.
    """
    harness, _client, instance = chain
    for _ in range(2):
        instance.queue_import_report(dict(_OK_DELETE_REPORT))
    artifact = register_deletion_package(environment_key="dev", seed="twice")
    key = "s1-delete-twice"
    try:
        first = harness.execution.delete_objects(
            "dev", PROJECT, artifact[0], artifact[1], operator="ada", idempotency_key=f"delete:{key}"
        )
        deletes_after_first = instance.deletes_accepted
        second = harness.execution.delete_objects(
            "dev", PROJECT, artifact[0], artifact[1], operator="ada", idempotency_key=f"delete:{key}"
        )
    finally:
        instance.scripted.clear()
        instance.import_reports.clear()

    assert first.status == "COMPLETED", first.model_dump()
    assert second.execution_id == first.execution_id, (second.model_dump(), first.model_dump())
    assert instance.deletes_accepted == deletes_after_first, (
        "the same key deleted a second time on the instance"
    )


# --- what S4 must still be the one to refuse ---------------------------------


def test_a_production_environment_with_a_forbid_deletion_policy_is_refused_without_a_request(
    chain: Any,
) -> None:
    """DX-EXE-038 / DX-ENV-002: PRODUCTION defaults to ``deletionPolicy=FORBID``, and S1 cannot talk past it.

    The assertion is the count of instance requests, not the status code: a guard that decided to
    "try the delete and see" would still answer 4xx here, and the instance would already have been
    told to destroy the object.
    """
    _harness, client, instance = chain
    artifact = register_deletion_package(
        environment_key="prod", seed="prod-forbid", uid="s1delete002"
    )
    before = len(instance.requests)

    response = _delete(client, "prod", artifact, key="s1-delete-prod-forbid")

    assert response.status_code == 403, response.text
    payload = response.json()
    assert payload["errorCode"] == "DX_DELETION_FORBIDDEN", payload
    assert payload["details"]["deletionPolicy"] == "FORBID", payload
    assert payload["details"]["environmentClass"] == "PRODUCTION", payload
    assert len(instance.requests) == before, "S1 let a FORBID environment be asked to delete"
    assert instance.deletes_accepted == 0


def test_an_undecidable_associated_data_check_stops_the_deletion(chain: Any) -> None:
    """DX-QRY-004's conservative direction, through S1's door: cannot tell means do not delete.

    The in-memory instance answers ``dataValues`` with a 503, which is how a check that could not
    be completed looks from here.  A deletion that treated "no answer" as "no data" would delete
    an object that still has values hanging off it — and on a shared public demo instance nobody
    would be able to tell that it was us.
    """
    _harness, client, instance = chain
    for _ in range(2):
        instance.queue_import_report(dict(_OK_DELETE_REPORT))
    artifact = register_deletion_package(environment_key="dev", seed="assoc", uid=UID)
    # ``times`` must outlast the client's retry budget: 503 on a GET is a retryable status, so a
    # single queued failure is absorbed by the second attempt and the check answers "no data" —
    # which would make this test pass for exactly the wrong reason.
    instance.fail_next("dataValues", 503, times=4)
    before = len(instance.requests)

    try:
        response = _delete(client, "dev", artifact, key="s1-delete-associated")
    finally:
        instance.scripted.clear()
        instance.import_reports.clear()

    assert response.status_code == 403, response.text
    payload = response.json()
    assert payload["errorCode"] == "DX_DELETION_FORBIDDEN", payload
    assert payload["details"]["rule"] == "DX-EXE-038", payload
    refused = payload["details"]["refused"]
    assert [row["uid"] for row in refused] == [UID], refused

    # DX-BR-05 makes S4 VALIDATE the deletion payload before it submits, and DHIS2's validate is a
    # POST with ``importMode=VALIDATE`` — so "only GETs were sent" would be the wrong guarantee and
    # an impossible one.  What must not happen is a *destructive* call: any import that is not in
    # VALIDATE mode, and any accepted delete.
    written = [
        row
        for row in instance.requests[before:]
        if str(row["method"]).upper() != "GET" and str((row.get("query") or {}).get("importMode")) != "VALIDATE"
    ]
    assert not written, f"the refusal still sent a destructive request: {written}"
    assert instance.deletes_accepted == 0


def test_an_emergency_locked_environment_refuses_the_deletion_and_unlocking_is_restored(
    chain: Any,
) -> None:
    """DX-ENV-007 / DX-BR-11: a lock stops every write-class execution, deletion included.

    The lock is taken and released inside the test because the environments are module-scoped and
    the suite randomises order: a leaked lock would refuse an unrelated later test with a 403 and
    be reported as a defect in the deletion path.
    """
    harness, client, instance = chain
    locked = client.post(
        "/api/v1/environments/dev/emergency-lock",
        headers=OPERATOR,
        json={"locked": True, "reason": "s1 deletion test holds the environment"},
    )
    assert locked.status_code == 200, locked.text
    artifact = register_deletion_package(environment_key="dev", seed="locked", uid=UID)
    before = len(instance.requests)
    try:
        response = _delete(client, "dev", artifact, key="s1-delete-locked")
    finally:
        harness.execution.set_emergency_lock("dev", False, None, "ada")

    assert response.status_code in (403, 409), response.text
    assert response.json()["errorCode"] in ("DX_EMERGENCY_LOCKED", "ENVIRONMENT_LOCKED", "INVALID_STATE"), (
        response.json()
    )
    assert len(instance.requests) == before, "a locked environment was still asked to delete"


def test_the_instance_refusing_the_deletion_is_reported_and_leaves_an_execution(
    chain: Any,
) -> None:
    """A delete the instance rejects answers with the execution record, not with a lost request.

    This is the state a cleanup run must be able to see (FR-TST-002③: cleanup failure only warns,
    but it has to be *knowable*): ``applied=false``, the ImportReport artifact S4 stored, and the
    step list saying which step failed.  Nothing here is S1's judgement — it is S4's outcome
    projected (CP-API-007).
    """
    _harness, client, instance = chain
    instance.queue_import_report(dict(_OK_DELETE_REPORT))
    instance.queue_import_report(
        {
            "status": "ERROR",
            "response": {
                "status": "ERROR",
                "stats": {"created": 0, "updated": 0, "deleted": 0, "ignored": 1},
                "objects": [
                    {
                        "status": "ERROR",
                        "klass": "DataElement",
                        "errCode": "E1004",
                        "message": "cannot delete: associated data",
                        "params": {"id": UID},
                    }
                ],
            },
        }
    )
    artifact = register_deletion_package(environment_key="dev", seed="rejected", uid=UID)

    try:
        response = _delete(client, "dev", artifact, key="s1-delete-rejected")
    finally:
        instance.scripted.clear()
        instance.import_reports.clear()

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["status"] == "FAILED", body
    assert body["applied"] is False, body
    assert body["importReportArtifactId"], body
    assert body["issues"], "an instance rejection with no standardised issue tells nobody what failed"
    assert [row["step"] for row in body["steps"]], body["steps"]

    followed = client.get(f"/api/v1/executions/{body['executionId']}", headers=OPERATOR)
    assert followed.status_code == 200, followed.text


# --- the data-export seam: S4 states a ceiling, S1 forwards it, S3 must honour it -----


def test_the_ceiling_s4_states_is_understood_by_s3(chain: Any) -> None:
    """S4's protection document, read by S3's parser, through S1's own forwarding path.

    ``DX-ENV-001`` gives an environment an ``llmDataPolicy`` — *how far* data may go.  S1 hands
    S4's document to S3 verbatim (``services/steps.py``), and S3 parses an untyped dict it cannot be
    told about in advance.  Those two facts met badly: S4 spells the field ``llmDataPolicy``, S3 did
    not recognise that name at all, and so every environment — however permissive — collapsed to
    S3's ``LOCAL_ONLY`` fail-safe.  Neither subsystem could see the other: S3 must not import S4
    (00 文档第 5 章) and S4 parses no policy.  Only S1, which ships all three in one venv, can pin
    the seam, so it is pinned here.
    """
    from factory_intelligence.policy.normalize import normalize_one

    harness, _client, _instance = chain
    with harness.runtime.transaction(operator="system") as ctx:
        document = dict(ctx.execution.get_protection("dev").data_export_policy)
    assert "llmDataPolicy" in document, sorted(document)

    policy = normalize_one(document, scope="env:dev")
    assert not any("unrecognised keys" in warning for warning in policy.warnings), policy.warnings
    assert policy.classes_for(DataClassification.PUBLIC), "S3 read nothing out of what S4 sent"
    assert not policy.classes_for(DataClassification.PHI_PII), "AI-POL-005: no ceiling opens PHI_PII"


def test_a_wider_ceiling_admits_the_stricter_classes(chain: Any) -> None:
    """The ceiling reading itself, on S4's exact document shape (``environment/protection.py``).

    Read as one exact class, a *wider* ceiling denies the *stricter* providers: an environment
    allowing ``APPROVED_EXTERNAL`` would refuse a local model.  S4 ships ``rank`` alongside the
    value precisely so callers compare strictness rather than match a name.
    """
    from factory_intelligence.policy.normalize import normalize_one

    policy = normalize_one(
        {
            "llmDataPolicy": "APPROVED_EXTERNAL",
            "rank": 1,
            "environmentClass": "TEST",
            "refusedWhen": "外部 LLM 提供方类别严于该策略时不得外发（D-07 由调用方合成）",
            "outsideCommitWindowReason": None,
        },
        scope="env:dev",
    )
    assert {member.value for member in policy.classes_for(DataClassification.PUBLIC)} == {
        "APPROVED_EXTERNAL",
        "PRIVATE_NETWORK",
        "LOCAL_ONLY",
    }
