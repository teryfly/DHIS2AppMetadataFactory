"""S1 running against the *real* S2 and the *real* S4 in one harness.

``test_real_s2.py`` proves the Blueprint boundary alone.  This file puts both providers behind
the same chain, which is the only way to see what a fake can always lie about:

* an environment S1 forwarded really lands in S4's ``dx`` and answers back through S4's own
  DTO — S4's ``EnvironmentSpec`` forbids unknown fields, so a stray key in the spec is a
  rejection, not a ignored field;
* the connection test S4 persists is the row S1's console projection then reads — which works
  only while the port method, the adapter's ``METHOD_MAP`` and ``factory_dhis2.api`` all say
  ``get_last_connection_test``.  A wrong name here is a silent ``null``, because the service
  reaches the read through ``getattr``;
* an Issue out of real S2 reaches S5 with one level name, and with its text intact.

S3 stays faked (the design stages need a presettable agent run), and DHIS2 is S4's own
in-memory instance behind its httpx transport, so no target is contacted — that is also what
lets "this read asked the instance nothing" be counted instead of assumed.

DISCOVERY is covered here because it is the stage a fake could never catch: the design line
asks S4 to discover the design reference *before* MAPPING has produced any Registry entry,
so handing S4 the project's Registry refs asks it to inspect an object list that is always
empty at that point, and S4 refuses a scope that selects nothing rather than pulling a whole
instance (FR-DSC-002). The scope therefore comes from S2's type descriptor catalog for a
Brownfield project and from the Greenfield rule in overall SRS 7.1 for a Greenfield one, and
only a real S4 on the other side of that call can say whether the request it receives reads
anything.
"""

from __future__ import annotations

import json
import os
from collections.abc import Iterator
from typing import Any

import pytest
from fastapi.testclient import TestClient

from factory_control.testing.harness import (
    ControlHarness,
    control_harness,
    has_real_provider,
    seed_project,
)

pytestmark = pytest.mark.skipif(
    not (has_real_provider("S2") and has_real_provider("S4")),
    reason="needs factory_blueprint and factory_dhis2 installed (scripts/install_dev.sh)",
)

OPERATOR = {"X-Operator": "ada", "Content-Type": "application/json"}
DISCOVERY_CREDENTIAL = "svcacct-reader:only-read"
IMPORT_CREDENTIAL = "svcacct-writer:metadata"


def _environment_body(key: str, environment_class: str) -> dict[str, Any]:
    body: dict[str, Any] = {
        "environmentKey": key,
        "name": f"{key} 实例（S1 真实链路）",
        "baseUrl": f"https://s1chain-{key}.local/dhis2",
        "environmentClass": environment_class,
        "versionConstraint": "2.41.x",
        "authMode": "BASIC",
        "tlsRequired": True,
        "timeoutSeconds": 30,
        "credentials": [
            {"scope": "discovery", "secretRef": f"env://S1CHAIN_{key.upper()}_DISCOVERY"},
            {"scope": "import", "secretRef": f"env://S1CHAIN_{key.upper()}_IMPORT"},
        ],
    }
    if environment_class == "PRODUCTION":
        # DX-ENV-002: S4 requires an allowed commit window on a PRODUCTION environment.
        body["protection"] = {
            "timezone": "UTC",
            "commitWindows": [{"days": [2, 4], "start": "01:00", "end": "05:00"}],
        }
    return body


@pytest.fixture(scope="module")
def chain() -> Iterator[tuple[ControlHarness, TestClient, Any]]:
    """A real-S2/real-S4 harness with both target environments registered in S4."""
    from factory_dhis2 import deps
    from factory_dhis2.capability.runtime_profile import clear_cache
    from factory_dhis2.testing.instance import FakeDhis2Instance
    from factory_dhis2.testing.transport import FakeDhis2Transport

    # S1's REST only accepts an indirection as a credential (S5IF-G09), so the value lives in
    # the environment the ``env://`` scheme reads.
    os.environ["S1CHAIN_DEV_DISCOVERY"] = DISCOVERY_CREDENTIAL
    os.environ["S1CHAIN_DEV_IMPORT"] = IMPORT_CREDENTIAL
    os.environ["S1CHAIN_PROD_DISCOVERY"] = DISCOVERY_CREDENTIAL
    os.environ["S1CHAIN_PROD_IMPORT"] = IMPORT_CREDENTIAL
    try:
        instance = FakeDhis2Instance()
        instance.authorities = ["F_DATAELEMENT_ADD", "ALL"]
        instance.roles = [{"id": "r1", "name": "Superuser"}]
        instance.put("dataElement", "de1", code="WEIGHT", name="体重", value_type="NUMBER")
        instance.put("categoryCombo", "cc1", code="default", name="default")
        # S4 rejects any UID that is not 11 characters, and its fake hands out a 13-character
        # filler once this list runs dry, so the list has to outlast the whole run.
        instance.generated_uids = [f"uid{index:08d}" for index in range(1000)]

        with control_harness(providers="real", real=("S2", "S4")) as harness:
            # After the harness: the transport is the only thing standing between S4 and a
            # real instance, and start-up resets S4's provider slots.
            deps.set_transport(FakeDhis2Transport(instance))
            client = TestClient(_app(harness))
            try:
                for key, environment_class in (("dev", "TEST"), ("prod", "PRODUCTION")):
                    created = client.post(
                        "/api/v1/environments",
                        headers=OPERATOR,
                        json=_environment_body(key, environment_class),
                    )
                    assert created.status_code == 201, created.text
                yield harness, client, instance
            finally:
                deps.reset_providers()
                clear_cache()
    finally:
        for name in tuple(os.environ):
            if name.startswith("S1CHAIN_"):
                del os.environ[name]


def _app(harness: ControlHarness) -> Any:
    from factory_control.rest.app import create_app

    return create_app(harness.runtime)


def test_the_environment_s1_forwarded_is_the_one_s4_answers(chain: Any) -> None:
    """S5IF-ENV03 / ENV02 against S4: references cross the boundary, secret values never do."""
    _harness, client, _instance = chain
    view = client.get("/api/v1/environments/dev", headers=OPERATOR).json()
    assert view["environmentKey"] == "dev"
    assert view["baseUrl"] == "https://s1chain-dev.local/dhis2"
    assert view["environmentClass"] == "TEST"
    refs = {credential["secretRef"] for credential in view["credentials"]}
    assert refs == {"env://S1CHAIN_DEV_DISCOVERY", "env://S1CHAIN_DEV_IMPORT"}
    # S5IF-G09: not the value, not a mask of it, not its length.
    assert DISCOVERY_CREDENTIAL not in json.dumps(view)

    listed = client.get("/api/v1/environments", headers=OPERATOR).json()
    assert {row["environmentKey"] for row in listed["items"]} == {"dev", "prod"}


def test_the_connection_test_s4_recorded_is_the_one_s1_shows(chain: Any) -> None:
    """S5IF-UP10 / FLD05 on the real read path, under the converged name.

    Two states the console must tell apart: *never tested* (``null``) and *tested and failed*.
    ``services/environments.py:129`` reaches the read through ``getattr``, so a name that does
    not exist on the provider degrades to ``null`` instead of failing — which is exactly why
    this assertion belongs against the real S4 and not a double.
    """
    from factory_control.services.environments import last_connection_record

    harness, client, instance = chain
    assert last_connection_record(harness.runtime, "dev") is None, "nothing tested yet"

    checked = client.post(
        "/api/v1/environments/dev/test-connection",
        headers=OPERATOR,
        json={"requiredScopes": ["discovery", "import"]},
    )
    assert checked.status_code == 200, checked.text
    report = checked.json()
    assert report["ok"] is True, report
    assert len(report["checks"]) >= 8, report["checks"]
    assert report["capabilityProfileArtifactId"], "S4's connection test also persists the profile"

    through_the_port = harness.execution.get_last_connection_test("dev")
    assert through_the_port is not None and through_the_port.ok is True
    assert through_the_port.environment_key == "dev"

    before = len(instance.requests)
    shown = last_connection_record(harness.runtime, "dev")
    assert len(instance.requests) == before, "the recorded read asked the target instance"
    assert shown is not None and shown["ok"] is True, shown
    assert shown["lastTestedAt"], shown
    assert set(shown) == {"ok", "result", "missingAuthorities", "lastTestedAt"}, shown


def test_an_issue_out_of_real_s2_arrives_with_one_level_and_its_text(chain: Any) -> None:
    """S5IF-BP14 / FLD07 through S2's real validator, not a hand-written dict."""
    from tests.helpers import run_stage  # type: ignore[import]

    harness, client, _instance = chain
    seeded = seed_project(harness)
    workflow = client.get(f"/api/v1/workflows/{seeded['workflowId']}", headers=OPERATOR).json()
    ready = [stage for stage in workflow["stages"] if stage["status"] == "READY"]
    assert ready, workflow
    run_stage(client, harness, ready[0]["id"])

    response = client.post(
        f"/api/v1/stages/{ready[0]['id']}/patches",
        headers=OPERATOR,
        json={
            "patch": {
                "operations": [
                    {
                        "op": "add",
                        "path": "/requirements/items/-",
                        "value": {
                            "businessKey": "req-chain-1",
                            "statement": "The clinic must record a visit outcome.",
                            "source": {"artifactId": "art-does-not-exist", "location": "spec §9"},
                        },
                    }
                ],
                "rationale": "added from the real S2 + real S4 chain test",
            }
        },
    )
    assert response.status_code == 200, response.text
    issues = response.json()["issues"]
    assert issues, "S2's validator found nothing to say about an unresolvable source reference"
    for row in issues:
        assert row["level"] in ("BLOCKING", "WARNING", "INFO"), row
        assert "severity" not in row, row
        assert row["issueId"], row
        assert row["description"], row


# --- DISCOVERY against the real S4 (CP-WF-028, FR-DSC-001/002, SRS 7.1) ---------


def _seed(harness: ControlHarness, *, project_type: str) -> dict[str, Any]:
    """A project of the given type, registered against the two real-S4 environments."""
    from factory_control.services.project import CreateProjectRequest, TargetEnvironment, create_project

    view = create_project(
        harness.runtime,
        CreateProjectRequest(
            name=f"chain {project_type}",
            owner_name="ada",
            description="real S2 + real S4 discovery chain",
            delivery_mode="A",
            project_type=project_type,
            version_constraints="2.41.x",
            targets=[
                TargetEnvironment(environment_key="dev", promotion_order=0, is_design_reference=True),
                TargetEnvironment(environment_key="prod", promotion_order=100, is_design_reference=False),
            ],
        ),
        operator="ada",
    )
    return view


def _open_discovery(harness: ControlHarness, workflow_id: str) -> str:
    """Put the design line in the state a project at DISCOVERY is really in.

    REQUIREMENTS and DOMAIN have passed and DISCOVERY is the ready stage; the two gates in
    between are not what this test is about, and driving them needs a presettable agent run,
    which is what S3's Fake is for and the real S3 is not.
    """
    from factory_foundation.api import unit_of_work

    from factory_control import store as S
    from factory_control.domain.enums import StageStatus, StageType

    wanted = {
        StageType.REQUIREMENTS.value: StageStatus.PASSED.value,
        StageType.DOMAIN.value: StageStatus.PASSED.value,
        StageType.DISCOVERY.value: StageStatus.READY.value,
    }
    with unit_of_work() as session:
        rows = S.stages_of(session, workflow_id)
        for stage in rows:
            if stage.stage_type in wanted:
                stage.status = wanted[stage.stage_type]
        discovery = next(stage for stage in rows if stage.stage_type == StageType.DISCOVERY.value)
        discovery_id = discovery.id
    return discovery_id


def test_a_brownfield_discovery_reads_the_types_the_factory_can_build(
    chain: Any,
) -> None:
    """The scope S1 sends must make S4 read something, over the real S4 boundary.

    Before this, DISCOVERY passed only against S1's own Fake: the Fake took any scope string
    and returned success, while real S4 answered the same call with "the discovery scope
    selects nothing to read" — because the Registry, which S1 named as the scope, is empty
    until MAPPING, the stage that comes after.  So the design line was broken at stage 3 for
    every real deployment, and nothing in either subsystem's own suite could see it.
    """
    from tests.helpers import run_stage  # type: ignore[import]

    harness, client, instance = chain
    seeded = _seed(harness, project_type="brownfield")
    discovery_id = _open_discovery(harness, str(seeded["workflowId"]))

    outcome = run_stage(client, harness, discovery_id)

    stage = client.get(f"/api/v1/stages/{discovery_id}", headers=OPERATOR).json()
    assert stage["status"] == "PASSED", (outcome, stage)
    asked = {str(row["path"]) for row in instance.requests}
    assert "dataElements" in asked, (
        f"a Brownfield discovery must index the metadata the mapping will fall onto; "
        f"the instance was asked for {sorted(asked)}"
    )
    assert "optionSets" in asked, "FR-DSC-003 compares candidate objects against what exists"
    result = client.get(
        f"/api/v1/workflows/{seeded['workflowId']}/artifacts", headers=OPERATOR
    ).json()["items"]
    assert any(row["type"] == "DISCOVERY_RESULT" for row in result), result


def test_a_greenfield_discovery_reads_no_object_lists(
    chain: Any,
) -> None:
    """SRS 7.1 / CP-WF-028: a Greenfield project reads version, capabilities and defaults only.

    The same call that has to widen for Brownfield has to stay narrow here: reading an
    instance's whole dataElement list on a project that will create everything from scratch
    is the full-pull FR-DSC-002 exists to prevent.
    """
    from tests.helpers import run_stage  # type: ignore[import]

    harness, client, instance = chain
    seeded = _seed(harness, project_type="greenfield")
    discovery_id = _open_discovery(harness, str(seeded["workflowId"]))
    before = len(instance.requests)

    outcome = run_stage(client, harness, discovery_id)

    stage = client.get(f"/api/v1/stages/{discovery_id}", headers=OPERATOR).json()
    assert stage["status"] == "PASSED", (outcome, stage)
    asked = [str(row["path"]) for row in instance.requests[before:]]
    assert "dataElements" not in asked and "optionSets" not in asked, asked
    assert "categoryCombos" in asked, (
        "the default CategoryCombo is still read: FR-COV-003 needs its UID even on a "
        "Greenfield instance"
    )
