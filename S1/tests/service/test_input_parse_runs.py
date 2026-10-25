"""Background parse runs: claim, finish, fail, reconcile (CP-API-012).

The parse no longer runs inside the request, so the assertions here are about the durable
state the request left behind and the thread that later completes it.  Problems this pins:
a second click while the first is running, a terminal status that never reaches ``FAILED``
after a crash, and a stage snapshot that overwrites the newest per-input run.
"""

from __future__ import annotations

from typing import Any

import pytest
from sqlalchemy import select

from factory_control.errors import PARSE_INTERRUPTED, RequestInProgressError
from factory_control.foundation import DataClassification, FactoryError, unit_of_work
from factory_control.models import InputParseRun, StageRun
from factory_control.services.inputs import list_inputs, record_input
from factory_control.services.parse_runs import (
    reconcile_interrupted,
    start_parse,
)


def _upload(harness: Any, seeded: dict[str, Any], name: str = "brief.md") -> str:
    uploaded = record_input(
        harness.runtime,
        seeded["id"],
        filename=name,
        media_type="text/markdown",
        data=b"# requirements\n- monthly summary\n",
        operator="ada",
        data_classification=DataClassification.INTERNAL,
    )
    return str(uploaded["artifactId"])


def _run(run_id: str) -> InputParseRun:
    with unit_of_work() as session:
        row = session.get(InputParseRun, run_id)
        assert row is not None
        return row


def _listed(harness: Any, project_id: str, artifact_id: str) -> dict[str, Any]:
    page = list_inputs(harness.runtime, project_id, operator="ada")
    return next(item for item in page["items"] if item["artifactId"] == artifact_id)


def test_a_claimed_parse_is_parsing_and_survives_a_reload(harness, seeded) -> None:
    _ = harness
    jobs: list[Any] = []
    harness.runtime.parse_submit = jobs.append  # hold the thread: nothing finishes yet
    artifact_id = _upload(harness, seeded)

    outcome = start_parse(harness.runtime, seeded["id"], artifact_id, operator="ada")

    assert outcome["runId"].startswith("ipr")
    run = _run(outcome["runId"])
    assert run.status == "PARSING" and run.started_at is None
    listed = _listed(harness, seeded["id"], artifact_id)
    assert listed["parseStatus"] == "PARSING"
    assert listed["parseRunId"] == outcome["runId"]
    assert len(jobs) == 1

    with pytest.raises(RequestInProgressError):
        start_parse(harness.runtime, seeded["id"], artifact_id, operator="ada")


def test_the_outcome_is_written_on_the_run_and_the_stage(harness, seeded) -> None:
    _ = harness
    artifact_id = _upload(harness, seeded)

    outcome = start_parse(harness.runtime, seeded["id"], artifact_id, operator="ada")

    run = _run(outcome["runId"])
    assert run.status == "PARSED"
    assert run.nrr_artifact_id and run.finished_at is not None
    assert run.step_run_id
    with unit_of_work() as session:
        stage_run = session.get(StageRun, run.step_run_id)
        assert stage_run is not None
        assert (stage_run.result or {})["status"] == "PARSED"
        assert (stage_run.result or {})["parseRunId"] == outcome["runId"]
    listed = _listed(harness, seeded["id"], artifact_id)
    assert listed["parseStatus"] == "PARSED"
    assert listed["parseRunId"] == outcome["runId"]
    assert listed["nrrArtifactId"] == run.nrr_artifact_id


def test_a_provider_failure_ends_as_failed_with_its_code(harness, seeded) -> None:
    _ = harness
    artifact_id = _upload(harness, seeded)

    class _Failing:
        def __init__(self, inner: Any) -> None:
            self._inner = inner

        def __getattr__(self, name: str) -> Any:
            return getattr(self._inner, name)

        def parse_input(self, *args: Any, **kwargs: Any) -> Any:
            raise FactoryError("the provider is down", code="LLM_TIMEOUT")

    harness.runtime.intelligence = _Failing(harness.runtime.intelligence)
    outcome = start_parse(harness.runtime, seeded["id"], artifact_id, operator="ada")

    run = _run(outcome["runId"])
    assert run.status == "FAILED"
    assert run.error_code == "LLM_TIMEOUT"
    assert "provider is down" in (run.error_message or "")
    assert _listed(harness, seeded["id"], artifact_id)["parseStatus"] == "FAILED"


def test_reconciliation_closes_what_a_restart_interrupted(harness, seeded) -> None:
    _ = harness
    jobs: list[Any] = []
    harness.runtime.parse_submit = jobs.append
    artifact_id = _upload(harness, seeded)
    outcome = start_parse(harness.runtime, seeded["id"], artifact_id, operator="ada")

    assert reconcile_interrupted(harness.runtime) == 1

    run = _run(outcome["runId"])
    assert run.status == "FAILED"
    assert run.error_code == PARSE_INTERRUPTED
    assert _listed(harness, seeded["id"], artifact_id)["parseStatus"] == "FAILED"


def test_the_latest_run_is_the_status_the_list_shows(harness, seeded) -> None:
    _ = harness
    artifact_id = _upload(harness, seeded)

    first = start_parse(harness.runtime, seeded["id"], artifact_id, operator="ada")
    second = start_parse(harness.runtime, seeded["id"], artifact_id, operator="ada")

    listed = _listed(harness, seeded["id"], artifact_id)
    assert listed["parseRunId"] == second["runId"]
    assert listed["parseRunId"] != first["runId"]
    with unit_of_work() as session:
        runs = list(
            session.execute(
                select(InputParseRun)
                .where(InputParseRun.input_artifact_id == artifact_id)
                .order_by(InputParseRun.created_at)
            ).scalars()
        )
    assert [run.status for run in runs] == ["PARSED", "PARSED"]


def test_a_held_job_finishes_when_it_runs_later(harness, seeded) -> None:
    """The route has already answered; running the submitted callable still completes it."""
    _ = harness
    jobs: list[Any] = []
    harness.runtime.parse_submit = jobs.append
    artifact_id = _upload(harness, seeded)
    outcome = start_parse(harness.runtime, seeded["id"], artifact_id, operator="ada")

    jobs[0]()

    assert _run(outcome["runId"]).status == "PARSED"
