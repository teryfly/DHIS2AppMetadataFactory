"""The normalised-requirements read (S5IF-BP15).

A parse writes the NRR through S3, which historically registered the artifact without the
workflow.  The review screen reads by workflow, so those documents were invisible even though
their bytes and source link were present.  These tests pin both the repaired registration
path and the read-side fallback for rows that already exist.
"""

from __future__ import annotations

import io
import json
from typing import Any

from factory_control.foundation import (
    ArtifactCreate,
    ArtifactStatus,
    DataClassification,
    register_artifact,
    stage_content,
    unit_of_work,
)
from factory_control.services.blueprint_reads import nrr_items
from factory_control.services.inputs import record_input


def _upload(harness: Any, seeded: dict[str, Any]) -> str:
    uploaded = record_input(
        harness.runtime,
        seeded["id"],
        filename="出院随访.txt",
        media_type="text/plain",
        data="出院患者随访记录管理系统需求规格说明书\n".encode("utf-8"),
        operator="ada",
        data_classification=DataClassification.INTERNAL,
    )
    return str(uploaded["artifactId"])


def _register_nrr(
    project_id: str,
    source_input_id: str,
    *,
    workflow_id: str | None,
    statement: str = "Patient age must be recorded in months.",
) -> str:
    document = {
        "items": [
            {
                "businessKey": "datarequirement_patient-age",
                "category": "dataRequirement",
                "statement": statement,
                "status": "CANDIDATE",
                "confidence": 1.0,
                "source": {"artifactId": source_input_id},
            }
        ]
    }
    handle = stage_content(
        io.BytesIO(json.dumps(document, ensure_ascii=False).encode("utf-8")),
        project_id=project_id,
        artifact_type="NRR",
        media_type="application/json",
    )
    with unit_of_work() as session:
        info = register_artifact(
            handle,
            ArtifactCreate(
                project_id=project_id,
                type="NRR",
                media_type="application/json",
                data_classification=DataClassification.INTERNAL,
                creator_subsystem="S3",
                workflow_id=workflow_id,
                source_artifact_ids=[source_input_id],
                status=ArtifactStatus.APPROVED,
            ),
            session=session,
        )
    return str(info.id)


def test_an_nrr_registered_with_the_workflow_is_listed(harness, seeded) -> None:
    input_id = _upload(harness, seeded)
    nrr_id = _register_nrr(seeded["id"], input_id, workflow_id=seeded["workflowId"])

    page = nrr_items(harness.runtime, seeded["workflowId"], operator="ada")

    row = next(item for item in page["items"] if item["artifactId"] == nrr_id)
    assert row["statement"] == "Patient age must be recorded in months."
    assert page["total"] == 1


def test_an_nrr_registered_without_the_workflow_is_found_through_its_input(harness, seeded) -> None:
    """Rows written before the registration fix must not need a re-parse to appear."""
    input_id = _upload(harness, seeded)
    nrr_id = _register_nrr(seeded["id"], input_id, workflow_id=None)

    page = nrr_items(harness.runtime, seeded["workflowId"], operator="ada")

    row = next(item for item in page["items"] if item["artifactId"] == nrr_id)
    assert row["businessKey"] == "datarequirement_patient-age"
    assert page["total"] == 1
