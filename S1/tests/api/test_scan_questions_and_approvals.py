"""Three things the SRS now asks for in this delivery, proved at the wire.

* CP-API-009's second half: the bytes are scanned, not just typed and weighed (the scan is
  structural — see `security/content_scan.py` — and these tests are what keeps that claim honest).
* CP-Q-002 across projects: one request for the unanswered questions an approval inbox needs.
  Until now the only way to ask was per workflow, so the mobile 待办 list dropped its fourth
  category rather than fan out.
* CP-GATE-012's number actually counting: the environment's `requiredApprovals` is what the
  DEPLOY_GATE is created with, so a PRODUCTION gate cannot reach COMMIT on one signature.
"""

from __future__ import annotations

import io
import zipfile
from typing import Any

import pytest
from fastapi.testclient import TestClient

from factory_control.services.runs import _required_approvals

pytestmark = pytest.mark.api

OPERATOR = {"X-Operator": "ada"}


def _zip(members: dict[str, bytes]) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, payload in members.items():
            archive.writestr(name, payload)
    return buf.getvalue()


def test_an_executable_hiding_behind_a_spreadsheet_type_is_refused(
    client: TestClient, seeded: dict
) -> None:
    response = client.post(
        f"/api/v1/projects/{seeded['id']}/inputs",
        headers=OPERATOR,
        files={"file": ("ledger.xlsx", b"\x7fELF" + b"\x00" * 64, "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")},
        data={"dataClassification": "INTERNAL"},
    )
    assert response.status_code == 422, response.text
    assert response.json()["errorCode"] == "UPLOAD_REJECTED"
    assert response.json()["details"]["violation"] == "EXECUTABLE_CONTENT"


def test_a_container_member_climbing_out_of_the_staging_directory_is_refused(
    client: TestClient, seeded: dict
) -> None:
    response = client.post(
        f"/api/v1/projects/{seeded['id']}/inputs",
        headers=OPERATOR,
        files={"file": ("book.xlsx", _zip({"../../etc/passwd": b"root:x:0:0"}), "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")},
        data={"dataClassification": "INTERNAL"},
    )
    assert response.status_code == 422, response.text
    assert response.json()["details"]["violation"] == "ARCHIVE_PATH_TRAVERSAL"


def test_a_decompression_bomb_is_refused_where_a_real_workbook_passes(
    client: TestClient, seeded: dict
) -> None:
    bomb = _zip({"xl/media/blob.bin": b"A" * (4 * 1024 * 1024)})
    refused = client.post(
        f"/api/v1/projects/{seeded['id']}/inputs",
        headers=OPERATOR,
        files={"file": ("bomb.xlsx", bomb, "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")},
        data={"dataClassification": "INTERNAL"},
    )
    assert refused.status_code == 422, refused.text
    assert refused.json()["details"]["violation"] == "ARCHIVE_RATIO"

    workbook = client.post(
        f"/api/v1/projects/{seeded['id']}/inputs",
        headers=OPERATOR,
        files={"file": ("real.xlsx", _zip({"xl/workbook.xml": b"<workbook/>"}), "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")},
        data={"dataClassification": "INTERNAL"},
    )
    assert workbook.status_code == 201, workbook.text
    assert workbook.json()["artifactId"]


def test_questions_are_listed_across_projects_with_the_paging_envelope(
    client: TestClient, harness: Any, seeded: dict
) -> None:
    from factory_control.domain.enums import QuestionStatus
    from factory_control.foundation import unit_of_work
    from factory_control.models import Question

    with unit_of_work() as session:
        from factory_control import store as S

        workflow = S.get_workflow_for_project(session, str(seeded["id"]))
        for index in range(3):
            session.add(
                Question(
                    id=f"q-scan-{index}",
                    project_id=str(seeded["id"]),
                    workflow_id=str(workflow.id),
                    stage_id=None,
                    text=f"open question {index}",
                    severity="HIGH" if index else "LOW",
                    status=QuestionStatus.OPEN.value if index < 2 else QuestionStatus.ANSWERED.value,
                    blocking=index < 2,
                )
            )

    listed = client.get("/api/v1/questions", headers=OPERATOR, params={"status": "OPEN"})
    assert listed.status_code == 200, listed.text
    body = listed.json()
    assert {"items", "total", "limit", "offset"} <= set(body)
    assert body["total"] == 2
    assert {item["id"] for item in body["items"]} == {"q-scan-0", "q-scan-1"}

    narrowed = client.get("/api/v1/questions", headers=OPERATOR, params={"status": "OPEN", "projectId": seeded["id"], "limit": 1})
    assert narrowed.status_code == 200, narrowed.text
    page = narrowed.json()
    assert len(page["items"]) == 1 and page["total"] == 2

    other = client.get("/api/v1/questions", headers=OPERATOR, params={"projectId": "PRJ-not-here"})
    assert other.status_code in (404, 200), other.text
    if other.status_code == 200:
        assert other.json()["items"] == []


def test_required_approvals_reads_the_environment_and_falls_back_to_one(harness: Any) -> None:
    """CP-GATE-012: the number on the environment is the number the Gate is created with.

    An environment whose protection cannot be read falls back to the single approver it had
    before rather than losing the run, and the gate row keeps whichever number was used, so the
    answer stays auditable either way.
    """
    from types import SimpleNamespace

    runtime = harness.runtime
    ctx = SimpleNamespace(execution=runtime.execution)
    key = "prod-two"
    runtime.execution.protections[key] = type(
        "P", (), {"required_approvals": 3}
    )()
    assert _required_approvals(ctx, key) == 3
    assert _required_approvals(ctx, "no-such-environment") == 1

    # A protection row that says nothing sensible must not raise: the run would die on it.
    runtime.execution.protections["odd"] = type("P", (), {"required_approvals": None})()
    assert _required_approvals(ctx, "odd") == 1
    runtime.execution.protections["zero"] = type("P", (), {"required_approvals": 0})()
    assert _required_approvals(ctx, "zero") == 1

def test_a_recorded_regression_can_be_read_back_and_filtered(
    client: TestClient, harness: Any
) -> None:
    """AI-REG-003's read side, which is what lets a console explain a disabled activation.

    Before this existed the only way to know whether a prompt version carried a regression result
    was to try to activate it and read the refusal.
    """
    registered = client.post(
        "/api/v1/governance/regression-records",
        headers={**OPERATOR, "Idempotency-Key": "reg-read-1"},
        json={"promptName": "mapping.extract", "promptVersion": 3, "passed": True, "source": "golden-project"},
    )
    assert registered.status_code in (200, 201), registered.text

    listed = client.get(
        "/api/v1/governance/regression-records",
        headers=OPERATOR,
        params={"promptName": "mapping.extract", "promptVersion": 3},
    )
    assert listed.status_code == 200, listed.text
    body = listed.json()
    assert body["total"] >= 1
    assert all(item["promptVersionId"] == "mapping.extract.v3" for item in body["items"])

    empty = client.get(
        "/api/v1/governance/regression-records",
        headers=OPERATOR,
        params={"promptName": "no.such.prompt", "promptVersion": 99},
    )
    assert empty.status_code == 200, empty.text
    assert empty.json()["items"] == []
