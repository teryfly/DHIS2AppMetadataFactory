"""The cross-report issue read (BP-VAL-005, BP-AC-12).

S5 shows one findings list built from both kinds of report a workflow produced, so this
tests exactly that: an S2 ``VALIDATION_REPORT`` and an S4 ``DHIS2_IMPORT_REPORT`` read
through one call and come back in one structure, filtered, sorted and paged by S2 rather
than by the client (CP-UI-004).

The import report is written the way S4 really writes it — the instance's own document
kept whole as the evidence a human re-checks against (FR-SEC-014) — because reading the
S4 report through a shape S4 never produces would prove nothing.
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

import pytest
from factory_foundation.api import (
    ArtifactCreate,
    DataClassification,
    register_artifact,
    register_artifact_types,
    unit_of_work,
)

from factory_blueprint import api, artifacts
from factory_blueprint.dto.issue import Issue, make_issue
from factory_blueprint.enums import DesignStage, IssueLevel, Section
from factory_blueprint.testing.harness import BlueprintHarness

PROJECT = "p1"

#: the DHIS2 2.41 synchronous metadata import shape, as an instance answers it
RAW_IMPORT_REPORT: dict[str, Any] = {
    "status": "ERROR",
    "stats": {"created": 1, "updated": 0, "ignored": 1, "total": 2},
    "typeReports": [
        {
            "klass": "org.hisp.dhis.dataelement.DataElement",
            "objectReports": [
                {
                    "uid": "f9bnacNsnp1",
                    "klass": "org.hisp.dhis.dataelement.DataElement",
                    "errorReports": [
                        {
                            "message": "Property categoryCombo not found",
                            "mainObject": True,
                            "errorCode": "E4001",
                            "object": "f9bnacNsnp1",
                        }
                    ],
                },
                {
                    "uid": "ouvcA7mQb12",
                    "klass": "org.hisp.dhis.dataelement.DataElement",
                    "status": "IGNORED",
                    "errorReports": [
                        {"message": "valueType is of unsupported type", "errorCode": "E1000"}
                    ],
                },
            ],
        }
    ],
}


@pytest.fixture(autouse=True)
def _import_report_type(harness: BlueprintHarness) -> Iterator[None]:
    """S4 owns DHIS2_IMPORT_REPORT; register its type as S4 would have, and give the
    read one Registry row so an instance UID can be mapped back to a concept."""
    with unit_of_work() as session:
        register_artifact_types({"DHIS2_IMPORT_REPORT": ["S4"]}, session=session)
    _seed_registry_index("f9bnacNsnp1", "weight")
    yield


@pytest.fixture
def validation_report() -> list[Issue]:
    issues = [
        make_issue(
            "BPV-REG-001",
            IssueLevel.BLOCKING,
            "the program references an unregistered option set",
            business_key="pregnancy",
            dhis2_type="program",
            section=Section.REGISTRY,
        ),
        make_issue(
            "BPV-COV-010",
            IssueLevel.WARNING,
            "French name is missing",
            business_key="weight",
            section=Section.REGISTRY,
        ),
        make_issue(
            "BPV-PG-001",
            IssueLevel.WARNING,
            "the page is not linked to any use case",
            location="/pages/items/0",
            section=Section.PAGES,
            source_stage=DesignStage.PAGE_DESIGN,
        ),
    ]
    handle = artifacts.stage_json(
        {"schemaVersion": 1, "kind": "VALIDATION_REPORT", "projectId": PROJECT,
         "issues": [_camel(issue) for issue in issues]},
        PROJECT,
        "VALIDATION_REPORT",
    )
    with unit_of_work() as session:
        info = register_artifact(
            handle,
            ArtifactCreate(
                project_id=PROJECT,
                type="VALIDATION_REPORT",
                media_type="application/json",
                data_classification=DataClassification.INTERNAL,
                creator_subsystem="S2",
            ),
            session=session,
        )
    return [info.id]


@pytest.fixture
def import_report() -> list[str]:
    handle = artifacts.stage_json(
        {
            "schemaVersion": 1,
            "kind": "DHIS2_IMPORT_REPORT",
            "mode": "COMMIT",
            "environmentKey": "TEST",
            "report": RAW_IMPORT_REPORT,
        },
        PROJECT,
        "DHIS2_IMPORT_REPORT",
    )
    with unit_of_work() as session:
        info = register_artifact(
            handle,
            ArtifactCreate(
                project_id=PROJECT,
                type="DHIS2_IMPORT_REPORT",
                media_type="application/json",
                data_classification=DataClassification.INTERNAL,
                creator_subsystem="S4",
            ),
            session=session,
        )
    return [info.id]


def _seed_registry_index(uid: str, business_key: str) -> None:
    """One Registry index row, so a UID in the instance's report maps back to a concept."""
    from factory_blueprint.models.registry import RegistryEntryRow

    with unit_of_work() as session:
        session.add(
            RegistryEntryRow(
                project_id=PROJECT,
                blueprint_version="0.1.0",
                business_key=business_key,
                dhis2_type="dataElement",
                uid=uid,
                disposition="CREATE",
                managed=True,
                status="APPROVED",
            )
        )


def _camel(issue: Issue) -> dict[str, Any]:
    reduced = issue.model_dump(mode="json")
    return {
        "issueId": reduced["issue_id"],
        "ruleId": reduced["rule_id"],
        "level": reduced["level"],
        "message": reduced["message"],
        "businessKey": reduced["business_key"],
        "uid": reduced["uid"],
        "dhis2Type": reduced["dhis2_type"],
        "artifactId": reduced["artifact_id"],
        "location": reduced["location"],
        "section": reduced["section"],
        "sourceStage": reduced["source_stage"],
        "remediation": reduced["remediation"],
        "details": reduced["details"],
    }


# --- BP-AC-12: one read, two report kinds, one structure -----------------


def test_both_report_kinds_answer_in_the_same_structure(
    harness: BlueprintHarness, validation_report: list[str], import_report: list[str]
) -> None:
    answer = api.query_issues(validation_report + import_report)
    keys = {item["businessKey"] for item in answer["items"]}
    assert {"pregnancy", "weight"} <= keys, "one from each report kind"
    assert all(set(item) == set(_camel(make_issue("r", IssueLevel.INFO, "m"))) for item in answer["items"]), (
        "every row carries exactly the BP-VAL-001 fields"
    )
    assert all("level" in item and "severity" not in item for item in answer["items"]), (
        "the level field is called ``level`` in both kinds (00 doc 6.10)"
    )


def test_an_import_row_gets_a_rule_id_a_source_stage_and_a_business_key(
    harness: BlueprintHarness, import_report: list[str]
) -> None:
    answer = api.query_issues(import_report)
    by_uid = {item["uid"]: item for item in answer["items"]}
    assert set(by_uid) == {"f9bnacNsnp1", "ouvcA7mQb12"}
    reference = by_uid["f9bnacNsnp1"]
    assert reference["ruleId"] == "DXIS-REFERENCE_ERROR"
    assert reference["level"] == "BLOCKING"
    assert reference["sourceStage"] == DesignStage.MAPPING.value
    assert reference["businessKey"] == "weight", "the Registry index says whose UID this is"
    assert reference["dhis2Type"] == "dataElement", "the Java class name is not trusted"


def test_a_uid_the_registry_does_not_own_is_still_reported(
    harness: BlueprintHarness, import_report: list[str]
) -> None:
    """An object outside the Managed Scope is a fact worth showing, not a row to drop."""
    by_uid = {item["uid"]: item for item in api.query_issues(import_report)["items"]}
    stranger = by_uid["ouvcA7mQb12"]
    assert stranger["businessKey"] is None
    assert stranger["level"] == IssueLevel.WARNING.value


def test_an_ignored_object_is_a_warning_not_a_block(
    harness: BlueprintHarness, import_report: list[str]
) -> None:
    answer = api.query_issues(import_report)
    ignored = [item for item in answer["items"] if item["level"] == IssueLevel.WARNING.value]
    assert len(ignored) == 1, "IGNORED means reported but not applied"
    assert ignored[0]["ruleId"] in {"DXIS-TYPE_ERROR", "DXIS-UNKNOWN"}


def test_filtering_by_level_returns_only_that_level(
    harness: BlueprintHarness, validation_report: list[str], import_report: list[str]
) -> None:
    warnings = api.query_issues(validation_report + import_report, level="WARNING")
    assert warnings["total"] == 3
    assert {item["level"] for item in warnings["items"]} == {"WARNING"}

    blocking = api.query_issues(validation_report + import_report, level="BLOCKING")
    assert {item["level"] for item in blocking["items"]} == {"BLOCKING"}


def test_filtering_by_rule_and_business_key(
    harness: BlueprintHarness, validation_report: list[str]
) -> None:
    by_rule = api.query_issues(validation_report, rule_id="BPV-COV-010")
    assert [item["businessKey"] for item in by_rule["items"]] == ["weight"]

    by_key = api.query_issues(validation_report, business_key="pregnancy")
    assert [item["ruleId"] for item in by_key["items"]] == ["BPV-REG-001"]

    by_stage = api.query_issues(validation_report, source_stage="PAGE_DESIGN")
    assert [item["section"] for item in by_stage["items"]] == ["pages"]


def test_sorting_and_paging_happen_here(
    harness: BlueprintHarness, validation_report: list[str], import_report: list[str]
) -> None:
    every = api.query_issues(validation_report + import_report, limit=200)
    assert every["total"] == len(every["items"]) == 5

    pages: list[str] = []
    for page in range(3):
        answer = api.query_issues(
            validation_report + import_report, sort_by="ruleId", limit=2, offset=page * 2
        )
        assert answer["total"] == 5, "the total is the filtered set, not the page"
        assert answer["limit"] == 2 and answer["offset"] == page * 2
        pages.extend(item["ruleId"] for item in answer["items"])
    assert pages == sorted(item["ruleId"] for item in every["items"]), "pages tile the order"
    assert len(pages) == 5

    descending = api.query_issues(
        validation_report + import_report, sort_by="ruleId", sort_dir="DESC"
    )
    assert descending["items"][0]["ruleId"] == max(item["ruleId"] for item in every["items"])


def test_no_report_ids_answers_empty(harness: BlueprintHarness) -> None:
    answer = api.query_issues([])
    assert answer == {"items": [], "total": 0, "limit": 50, "offset": 0}


def test_the_read_refuses_to_run_inside_a_unit_of_work(
    harness: BlueprintHarness, validation_report: list[str]
) -> None:
    from factory_foundation.api import TransactionMisuseError

    with pytest.raises(TransactionMisuseError), unit_of_work():
        api.query_issues(validation_report)
