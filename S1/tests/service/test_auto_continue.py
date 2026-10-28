"""Auto stages always continue; agent stages are opt-in per stage and never bypass a Gate."""

from __future__ import annotations

from typing import Any

from factory_control import store as S
from factory_control.domain.enums import StageStatus, StageType
from factory_control.foundation import unit_of_work
from factory_control.models import SystemSetting
from factory_control.services.auto_continue import auto_continue_ready_stages
from factory_control.settings import STAGE_AUTO_CONTINUE


class _Engine:
    def __init__(self) -> None:
        self.started: list[str] = []

    def start_stage(self, stage_id: str, *, operator: str, rerun: bool = False) -> dict[str, Any]:
        self.started.append(stage_id)
        return {"stageId": stage_id, "operator": operator, "rerun": rerun}


def _ready_domain(harness, seeded) -> str:
    with unit_of_work() as session:
        requirements = S.stage_by_type(session, seeded["workflowId"], StageType.REQUIREMENTS)
        domain = S.stage_by_type(session, seeded["workflowId"], StageType.DOMAIN)
        assert requirements is not None and domain is not None
        requirements.status = StageStatus.PASSED.value
        domain.status = StageStatus.READY.value
        session.merge(
            SystemSetting(
                key=STAGE_AUTO_CONTINUE,
                value={"value": ["DOMAIN"]},
                updated_by="test",
            )
        )
        return domain.id


def test_a_configured_ready_stage_is_started(harness, seeded) -> None:
    domain_id = _ready_domain(harness, seeded)
    engine = _Engine()
    harness.runtime.engine = engine

    result = auto_continue_ready_stages(harness.runtime)

    assert result["started"] == [domain_id]
    assert engine.started == [domain_id]


def test_an_unconfigured_ready_stage_is_left_for_a_person(harness, seeded) -> None:
    _ready_domain(harness, seeded)
    with unit_of_work() as session:
        row = session.get(SystemSetting, STAGE_AUTO_CONTINUE)
        assert row is not None
        row.value = {"value": []}
    engine = _Engine()
    harness.runtime.engine = engine

    result = auto_continue_ready_stages(harness.runtime)

    assert result["started"] == []
    assert engine.started == []


def test_an_auto_stage_continues_without_configuration(harness, seeded) -> None:
    """DISCOVERY needs nobody: no click, no opt-in setting."""
    with unit_of_work() as session:
        domain = S.stage_by_type(session, seeded["workflowId"], StageType.DOMAIN)
        discovery = S.stage_by_type(session, seeded["workflowId"], StageType.DISCOVERY)
        assert domain is not None and discovery is not None
        domain.status = StageStatus.PASSED.value
        discovery.status = StageStatus.READY.value
    engine = _Engine()
    harness.runtime.engine = engine

    result = auto_continue_ready_stages(harness.runtime)

    assert result["started"] == [discovery.id]
    assert engine.started == [discovery.id]
