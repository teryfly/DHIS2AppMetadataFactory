"""The 49 new endpoints, driven from a table rather than 49 look-alike functions.

Each row asserts the same three things — the route answers, the body validates
against the component the route declares, and the answer is camelCase — which is
exactly the part worth repeating.  The rows that need behaviour no table can express
(business rules, error codes, hash re-checks) are separate tests below.
"""

from __future__ import annotations

from typing import Any

import pytest
from fastapi.testclient import TestClient

from factory_control.rest.schemas import blueprint as BS
from factory_control.rest.schemas import environments as ES
from factory_control.rest.schemas import governance as GS
from factory_control.rest.schemas import inputs as IS
from factory_control.rest.schemas.common import AcceptanceView, CamelModel
from tests.helpers import upload_input  # type: ignore[import]

OPERATOR = {"X-Operator": "ada"}

#: (S5IF id, method, path template, response model, mutating)
READS: list[tuple[str, str, type[CamelModel]]] = [
    ("S5IF-ENV01", "/api/v1/environments", ES.EnvironmentPage),
    ("S5IF-ENV02", "/api/v1/environments/dev", ES.EnvironmentView),
    ("S5IF-ENV06", "/api/v1/environments/dev/protection", ES.EnvironmentProtectionView),
    ("S5IF-ENV09", "/api/v1/environments/dev/capability-profile", ES.CapabilityProfileView),
    ("S5IF-ENV11", "/api/v1/environments/dev/discovery-results", ES.DiscoveryResultPage),
    ("S5IF-ENV12", "/api/v1/environments/expiring-credentials", ES.ExpiringCredentialList),
    ("S5IF-GOV01", "/api/v1/governance/providers", GS.ProviderList),
    ("S5IF-GOV04", "/api/v1/governance/models", GS.ModelList),
    ("S5IF-GOV07", "/api/v1/governance/prompts", GS.PromptList),
    ("S5IF-GOV08", "/api/v1/governance/prompts/requirements/versions/1", GS.PromptDetailView),
    ("S5IF-GOV10", "/api/v1/governance/prompts/activations", GS.ActivationList),
    ("S5IF-GOV12", "/api/v1/governance/policy", GS.PolicyView),
    ("S5IF-GOV14", "/api/v1/governance/evaluations", GS.EvaluationList),
    ("S5IF-GOV15", "/api/v1/governance/evaluations/evr-1", GS.EvaluationView),
    ("S5IF-GOV17", "/api/v1/governance/usage", GS.UsageView),
    ("S5IF-GOV19", "/api/v1/governance/agents", GS.AgentListView),
]


def _snake_free(node: Any, where: str = "response") -> list[str]:
    found: list[str] = []
    if isinstance(node, dict):
        for key, value in node.items():
            # ``_section``/``_path`` are S2's own locator keys, kept on purpose (S5IF-BP04).
            # ``_section``/``_path`` are S2's locator keys, kept on purpose; an
            # ALL-CAPS name is an enum member (``PHI_PII``), not a field name.
            if "_" in key and not key.startswith("_") and not key.isupper():
                found.append(f"{where}.{key}")
            found += _snake_free(value, f"{where}.{key}")
    elif isinstance(node, list):
        for index, item in enumerate(node):
            found += _snake_free(item, f"{where}[{index}]")
    return found


@pytest.mark.parametrize(("s5if", "path", "model"), READS, ids=[r[0] for r in READS])
def test_forwarded_read_answers_with_typed_camel_body(
    seeded_env: dict[str, Any], client: TestClient, s5if: str, path: str, model: type[CamelModel]
) -> None:
    del s5if
    response = client.get(path, headers=OPERATOR)
    assert response.status_code == 200, response.text
    body = response.json()
    model.model_validate(body)  # the contract the route declares is the contract it sends
    assert not _snake_free(body), _snake_free(body)


def test_environment_list_carries_every_field_s5_displays(seeded_env: dict[str, Any], client: TestClient) -> None:
    """S5IF-AC03: the whole of section 4.1, and no credential material at all."""
    body = client.get("/api/v1/environments", headers=OPERATOR).json()
    row = body["items"][0]
    for field in (
        "environmentKey",
        "name",
        "baseUrl",
        "environmentClass",
        "authMode",
        "tlsRequired",
        "llmDataPolicy",
        "timeoutSeconds",
        "status",
        "secretRefs",
        "credentials",
    ):
        assert field in row, field
    credential = row["credentials"][0]
    assert set(credential) == {"scope", "secretRef", "expiresAt", "label"}
    assert not any(key.lower() in {"value", "token", "password", "length", "prefix"} for key in credential)


def test_connection_test_reports_all_eight_checks_and_missing_authorities(
    harness: Any, seeded_env: dict[str, Any], client: TestClient
) -> None:
    """CP-AC-15 / S5IF-AC05 with DX-CON-001/002: each check is displayable, the gap per scope."""
    ok = client.post("/api/v1/environments/dev/test-connection", headers=OPERATOR, json={}).json()
    assert ok["ok"] is True
    assert [c["name"] for c in ok["checks"]] == [
        "addressReachable", "tlsValid", "authentication", "versionIdentified",
        "versionConstraint", "metadataRead", "metadataImport", "serviceAccountRole",
    ]
    assert all(c["state"] in ("PASSED", "FAILED", "SKIPPED") for c in ok["checks"])

    harness.execution.missing_authorities["import"] = ["M:metadata:add"]
    failed = client.post(
        "/api/v1/environments/dev/test-connection", headers=OPERATOR, json={"requiredScopes": ["discovery", "import"]}
    ).json()
    assert failed["ok"] is False and failed["result"] == "FAILED"
    assert failed["missingAuthorities"] == {"import": ["M:metadata:add"]}
    assert {c["name"]: c["state"] for c in failed["checks"]}["metadataImport"] == "FAILED"


def test_environment_protection_includes_the_field_the_mirror_lacked(
    seeded_env: dict[str, Any], client: TestClient
) -> None:
    """S5IF-ENV06: ``ports/dto.py`` used to have no deletionPolicy at all."""
    body = client.get("/api/v1/environments/dev/protection", headers=OPERATOR).json()
    ES.EnvironmentProtectionView.model_validate(body)
    assert body["deletionPolicy"] == "SAFE_ONLY"
    assert body["environmentKey"] == "dev"


def test_capability_profile_delivers_the_index_layer_without_the_unpublished_body(
    seeded_env: dict[str, Any], client: TestClient
) -> None:
    """S5IF-ENV09 with UP02: index fields now, ``content`` only once S4 ships the format."""
    body = client.get("/api/v1/environments/dev/capability-profile?includeContent=true", headers=OPERATOR).json()
    assert body["freshness"] == "VALID"
    assert body["profileVersion"] == "Dhis241Profile"
    assert body["artifactId"]
    assert body["content"] is None


def test_discovery_results_are_paginated_index_rows(seeded_env: dict[str, Any], client: TestClient) -> None:
    body = client.get("/api/v1/environments/dev/discovery-results?limit=1", headers=OPERATOR).json()
    ES.DiscoveryResultPage.model_validate(body)
    assert body["items"][0]["objectCount"] == 12
    assert {"total", "limit", "offset"} <= set(body)


def test_expiring_credentials_report_days_remaining(seeded_env: dict[str, Any], client: TestClient) -> None:
    body = client.get("/api/v1/environments/expiring-credentials?withinDays=200", headers=OPERATOR).json()
    row = body["items"][0]
    assert {"environmentKey", "scope", "secretRef", "expiresAt", "daysRemaining"} <= set(row)


# --- Blueprint reads ---------------------------------------------------------

BP_PREFIXED: list[tuple[str, str, type[CamelModel]]] = [
    ("S5IF-BP01", "/api/v1/projects/{pid}/blueprint/section-schemas", BS.SectionSchemaIndexView),
    ("S5IF-BP03", "/api/v1/projects/{pid}/blueprint/versions", BS.VersionPage),
    ("S5IF-BP05", "/api/v1/projects/{pid}/blueprint/draft", BS.DraftView),
    ("S5IF-BP06", "/api/v1/projects/{pid}/registry?environmentKey=dev", BS.RegistryPage),
    ("S5IF-BP07", "/api/v1/projects/{pid}/registry/managed-scope?environmentKey=dev", BS.ManagedScopePage),
    ("S5IF-BP08", "/api/v1/projects/{pid}/uid-pool", BS.UidPoolView),
    # S5IF-BP16 became the workflow-level dependency graph (CP-UI-020) whose unbuilt
    # answer is a 404, not a 200 body — shape coverage lives in test_cp_ui_020_023.py.
    # S5IF-BP17 keeps its route but "nothing compiled" is a 404 now (CP-UI-022); the
    # compiled case is pinned by test_package_files_keeps_the_role_and_the_hash.
    ("S5IF-BP13", "/api/v1/projects/{pid}/decisions?decisionIds=d-1", BS.DecisionList),
    ("S5IF-BP15", "/api/v1/workflows/{wid}/nrr", BS.NrrPage),
]


@pytest.mark.parametrize(("s5if", "template", "model"), BP_PREFIXED, ids=[r[0] for r in BP_PREFIXED])
def test_blueprint_read_answers_with_typed_camel_body(
    seeded_env: dict[str, Any], client: TestClient, s5if: str, template: str, model: type[CamelModel]
) -> None:
    del s5if
    path = template.format(pid=seeded_env["id"], wid=seeded_env["workflowId"])
    response = client.get(path, headers=OPERATOR)
    assert response.status_code == 200, response.text
    body = response.json()
    model.model_validate(body)
    assert not _snake_free(body), _snake_free(body)


def test_section_schema_index_lists_the_seventeen_sections(
    seeded_env: dict[str, Any], client: TestClient
) -> None:
    """S5IF-BP01 / section 5.1 — read from S2's enum, not copied into S1."""
    body = client.get(f"/api/v1/projects/{seeded_env['id']}/blueprint/section-schemas", headers=OPERATOR).json()
    names = [s["section"] for s in body["sections"]]
    assert len(names) == 17
    assert {"project", "mapping", "environmentConstraints", "generationPolicy"} <= set(names)
    by_name = {s["section"]: s for s in body["sections"]}
    assert by_name["project"]["entryContainer"] is None
    assert by_name["pages"]["keyField"] == "pageKey"
    assert body["currentSchemaVersion"] == 1


def test_section_schema_document_is_self_contained(seeded_env: dict[str, Any], client: TestClient) -> None:
    """CP-AC-17 / S5IF-AC07: every ``$ref`` points inside the returned document."""
    body = client.get(
        f"/api/v1/projects/{seeded_env['id']}/blueprint/section-schemas/mapping", headers=OPERATOR
    ).json()
    assert body["$schema"] == "https://json-schema.org/draft/2020-12/schema"


def test_unknown_section_is_404_with_the_known_list(seeded_env: dict[str, Any], client: TestClient) -> None:
    response = client.get(
        f"/api/v1/projects/{seeded_env['id']}/blueprint/section-schemas/nonsense", headers=OPERATOR
    )
    assert response.status_code == 404
    assert response.json()["details"]["known"]


def test_entries_keep_s2_locator_keys(seeded_env: dict[str, Any], client: TestClient) -> None:
    """S5IF-BP04: S5's inspector and its jump-to-source both read ``_section``/``_path``."""
    body = client.get(
        f"/api/v1/projects/{seeded_env['id']}/blueprint/entries?sections=mapping&businessKeys=bk-1",
        headers=OPERATOR,
    ).json()
    entry = body["entries"][0]
    assert entry["_section"] == "mapping"
    assert entry["_path"].startswith("/mapping/")


def test_registry_row_flattens_the_observation_baseline(seeded_env: dict[str, Any], client: TestClient) -> None:
    """S5IF-BP06 / UI-WB-005: the baseline is reachable on the row."""
    body = client.get(f"/api/v1/projects/{seeded_env['id']}/registry?environmentKey=dev", headers=OPERATOR).json()
    row = body["items"][0]
    assert row["observedHash"] and row["discoveryResultId"]


def test_binding_without_a_note_is_rejected_by_s1(
    harness: Any, seeded: dict[str, Any], client: TestClient
) -> None:
    """S5IF-BP10: S2 raises REGISTRY_ERROR for an empty note; S5 must see a 422 from S1."""
    from tests.helpers import create_run, drive_run  # type: ignore[import]

    run_id = create_run(client, harness, seeded)
    drive_run(client, harness, run_id, stop_at_deploy_gate=True)

    missing = client.put(
        f"/api/v1/deployment-runs/{run_id}/environment-bindings/bk-1",
        headers=OPERATOR,
        json={"uid": "A1", "note": "   "},
    )
    assert missing.status_code == 422, missing.text
    assert missing.json()["details"]["fields"][0]["path"] == "note"

    accepted = client.put(
        f"/api/v1/deployment-runs/{run_id}/environment-bindings/bk-1",
        headers=OPERATOR,
        json={"uid": "A1", "note": "matched by hand against the discovery result"},
    )
    assert accepted.status_code == 200, accepted.text
    body = accepted.json()
    # S1 resolves the version and the environment from the Run; S5 never guesses them.
    assert body["environmentKey"] == "dev"
    assert body["blueprintVersion"]
    BS.BindingView.model_validate({**body, "businessKey": "bk-1"})


# --- Governance --------------------------------------------------------------


def test_governance_writes_need_an_operator_and_a_secret_reference(client: TestClient, seeded_env: Any) -> None:
    del seeded_env
    missing = client.post(
        "/api/v1/governance/providers",
        json={
            "name": "p",
            "providerClass": "PRIVATE_NETWORK",
            "endpoint": "https://p.invalid",
            "apiKeySecretRef": "vault://p/key",
        },
    )
    assert missing.status_code == 400
    assert missing.json()["errorCode"] == "OPERATOR_REQUIRED"

    literal = client.post(
        "/api/v1/governance/providers",
        headers=OPERATOR,
        json={
            "name": "p",
            "providerClass": "PRIVATE_NETWORK",
            "endpoint": "https://p.invalid",
            "apiKeySecretRef": "sk-realmagic",
        },
    )
    assert literal.status_code == 422
    assert literal.json()["details"]["acceptedSchemes"]


def test_a_provider_probe_answers_rather_than_failing(client: TestClient, seeded_env: Any) -> None:
    """S5IF-GOV20: the probe's answer is the feature, so a negative result is still a 200.

    An operator asking "is this address wrong?" needs the sentence back, not an error page: the
    difference between an unreachable address, a refused key and a slow model is exactly what the
    registration screen cannot show any other way, and a provider nobody registered is an answer
    about the registration rather than a server fault.
    """
    del seeded_env
    registered = client.post(
        "/api/v1/governance/providers",
        headers=OPERATOR,
        json={
            "name": "quiet",
            "providerClass": "PRIVATE_NETWORK",
            "endpoint": "https://quiet.invalid/v1",
            "apiKeySecretRef": "env://QUIET_KEY",
        },
    )
    assert registered.status_code == 201, registered.text

    probed = client.post("/api/v1/governance/providers/quiet/test-connection", headers=OPERATOR)
    assert probed.status_code == 200, probed.text
    body = probed.json()
    assert body["ref"] == "quiet"
    assert body["healthy"] is True
    # the reference the registration carries is never echoed back (AI-LLM-005)
    assert "apiKeySecretRef" not in body

    unknown = client.post("/api/v1/governance/providers/nope/test-connection", headers=OPERATOR)
    assert unknown.status_code == 200, unknown.text
    assert unknown.json()["healthy"] is False
    assert unknown.json()["detail"], "a refusal without a reason is the failure this route exists not to have"


def test_editing_a_provider_keeps_what_the_caller_did_not_send(
    client: TestClient, seeded_env: Any
) -> None:
    """S5IF-GOV03: correcting an address must not clear the Key reference.

    Registration replaces the row, so an operator who only wanted to fix a typo had to
    re-supply every field or lose the reference — the failure an edit form cannot avoid,
    because it shows one address box and no key box.
    """
    del seeded_env
    client.post(
        "/api/v1/governance/providers",
        headers=OPERATOR,
        json={
            "name": "typo",
            "providerClass": "PRIVATE_NETWORK",
            "endpoint": "https://old.invalid/v1",
            "apiKeySecretRef": "env://TYPO_KEY",
        },
    )

    moved = client.patch(
        "/api/v1/governance/providers/typo",
        headers=OPERATOR,
        json={"endpoint": "https://new.invalid/v1"},
    )
    assert moved.status_code == 200, moved.text
    row = moved.json()
    assert row["endpoint"] == "https://new.invalid/v1"
    assert row["apiKeySecretRef"] == "env://TYPO_KEY", "the reference survived the edit"
    assert row["providerClass"] == "PRIVATE_NETWORK"
    assert row["enabled"] is True

    switched = client.patch(
        "/api/v1/governance/providers/typo", headers=OPERATOR, json={"enabled": False}
    )
    assert switched.json()["endpoint"] == "https://new.invalid/v1"

    empty = client.patch("/api/v1/governance/providers/typo", headers=OPERATOR, json={})
    assert empty.status_code == 422
    assert empty.json()["errorCode"] == "VALIDATION_ERROR"

    literal = client.patch(
        "/api/v1/governance/providers/typo", headers=OPERATOR, json={"apiKeySecretRef": "sk-real"}
    )
    assert literal.status_code == 422
    assert literal.json()["details"]["acceptedSchemes"]

    unknown = client.patch(
        "/api/v1/governance/providers/never-registered",
        headers=OPERATOR,
        json={"endpoint": "https://x.invalid/v1"},
    )
    assert unknown.status_code == 404, unknown.text

    # a misspelt key must not be accepted-and-ignored: that is a save that did nothing
    typo = client.patch(
        "/api/v1/governance/providers/typo",
        headers=OPERATOR,
        json={"apiKeyRef": "vault://typo/key"},
    )
    assert typo.status_code == 422, typo.text
    assert typo.json()["errorCode"] == "VALIDATION_ERROR"

    # naming a field as null is not "change it to nothing"; the contract must not invite the try
    nulled = client.patch(
        "/api/v1/governance/providers/typo", headers=OPERATOR, json={"endpoint": None}
    )
    assert nulled.status_code == 422, nulled.text
    assert nulled.json()["errorCode"] == "VALIDATION_ERROR"
    # the shown text is the catalogue's, so the part a form can key on lives in `details`
    assert "at least one field" in str(nulled.json()["details"])

    blank = client.patch(
        "/api/v1/governance/providers/typo", headers=OPERATOR, json={"endpoint": "   "}
    )
    assert blank.status_code == 422, blank.text

    # column widths are enforced at the door, not by a SQLAlchemy failure at the far end
    too_long = client.patch(
        "/api/v1/governance/providers/typo",
        headers=OPERATOR,
        json={"endpoint": "https://" + "x" * 600},
    )
    assert too_long.status_code == 422, too_long.text


def test_provider_vendor_and_model_suggestions_round_trip(
    client: TestClient, seeded_env: Any
) -> None:
    """The console starts from a vendor preset and offers its model names as candidates.

    Neither field decides a call: ``vendor`` labels where the row started, and
    ``suggestedModels`` feeds the model form's shortcut buttons.  They live on the provider
    so a customized address does not lose the names the operator saw when they picked it.
    """
    del seeded_env
    registered = client.post(
        "/api/v1/governance/providers",
        headers=OPERATOR,
        json={
            "name": "preset",
            "providerClass": "APPROVED_EXTERNAL",
            "endpoint": "https://api.deepseek.com/v1",
            "apiKeySecretRef": "env://DEEPSEEK_API_KEY",
            "vendor": "deepseek",
            "suggestedModels": ["deepseek-chat", " deepseek-chat ", "", "deepseek-reasoner"],
        },
    )
    assert registered.status_code == 201, registered.text
    row = registered.json()
    assert row["vendor"] == "deepseek"
    # trimmed, blank dropped, duplicate collapsed — the same normalisation S3 applies
    assert row["suggestedModels"] == ["deepseek-chat", "deepseek-reasoner"]

    empty = client.post(
        "/api/v1/governance/providers",
        headers=OPERATOR,
        json={
            "name": "plain",
            "providerClass": "PRIVATE_NETWORK",
            "endpoint": "https://llm.invalid/v1",
        },
    )
    assert empty.status_code == 201, empty.text
    assert (empty.json()["vendor"], empty.json()["suggestedModels"]) == ("custom", [])

    edited = client.patch(
        "/api/v1/governance/providers/preset",
        headers=OPERATOR,
        json={"vendor": "custom", "suggestedModels": ["my-model"]},
    )
    assert edited.status_code == 200, edited.text
    assert (edited.json()["vendor"], edited.json()["suggestedModels"]) == ("custom", ["my-model"])

    # an empty list is a change: it clears the candidates rather than leaving them alone
    cleared = client.patch(
        "/api/v1/governance/providers/preset",
        headers=OPERATOR,
        json={"suggestedModels": []},
    )
    assert cleared.status_code == 200, cleared.text
    assert cleared.json()["suggestedModels"] == []
    assert cleared.json()["vendor"] == "custom"

    blank_vendor = client.patch(
        "/api/v1/governance/providers/preset",
        headers=OPERATOR,
        json={"vendor": "   "},
    )
    assert blank_vendor.status_code == 422, blank_vendor.text

    long_name = client.patch(
        "/api/v1/governance/providers/preset",
        headers=OPERATOR,
        json={"suggestedModels": ["x" * 129]},
    )
    assert long_name.status_code == 422, long_name.text
    assert long_name.json()["details"]["fields"][0]["path"] == "suggestedModels"


def test_environment_class_is_not_patchable(
    client: TestClient, seeded_env: Any
) -> None:
    """DX-ENV-001: the class decides the rules an environment already satisfies.

    S4's ``EnvironmentPatch`` has no ``environmentClass`` and forbids unknown keys, so
    forwarding one raised a pydantic error that named nothing and answered 500.  S1
    refuses it by name instead: an operator who wants a production environment registers
    a new key.
    """
    del seeded_env
    reclass = client.patch(
        "/api/v1/environments/dev",
        headers=OPERATOR,
        json={"environmentClass": "PRODUCTION"},
    )
    assert reclass.status_code == 422, reclass.text
    assert reclass.json()["details"]["rule"] == "DX-ENV-001"


def test_budget_get_and_usage_rollup_come_back_in_one_naming_style(
    client: TestClient, seeded_env: Any
) -> None:
    """S5IF-UP08: S3 dumps these two in snake_case; a response may not mix styles."""
    budget = client.get(f"/api/v1/governance/budget?projectId={seeded_env['id']}", headers=OPERATOR).json()
    assert not _snake_free(budget), _snake_free(budget)
    assert "thresholdReached" in budget

    usage = client.get("/api/v1/governance/usage", headers=OPERATOR).json()
    assert not _snake_free(usage), _snake_free(usage)
    assert usage["items"][0]["inputTokens"] == 10


def test_unknown_usage_dimension_is_422_not_500(client: TestClient, seeded_env: Any) -> None:
    """S3 raises a bare ValueError here (cost/usage.py:171), which S1 must pre-empt."""
    del seeded_env
    response = client.get("/api/v1/governance/usage?groupBy=galaxy", headers=OPERATOR)
    assert response.status_code == 422
    assert response.json()["errorCode"] == "VALIDATION_ERROR"


def test_model_list_supplies_the_endpoint_reference_s3_omits(client: TestClient, seeded_env: Any) -> None:
    """S5IF-GOV04 supplement: UI-ADM-003 shows the address and the key's reference."""
    del seeded_env
    client.post(
        "/api/v1/governance/providers",
        headers=OPERATOR,
        json={
            "name": "fake-provider",
            "providerClass": "PRIVATE_NETWORK",
            "endpoint": "https://model.invalid/v1",
            "apiKeySecretRef": "vault://fake-provider/key",
        },
    )
    row = client.get("/api/v1/governance/models", headers=OPERATOR).json()["items"][0]
    assert row["endpoint"] == "https://model.invalid/v1"
    assert row["apiKeySecretRef"] == "vault://fake-provider/key"


def test_evaluation_runs_through_the_port_not_the_refusing_action(client: TestClient, seeded_env: Any) -> None:
    """S5IF-GOV16: the facade's ``eval.run`` always raises, so S1 must use run_evaluation."""
    del seeded_env
    response = client.post(
        "/api/v1/governance/evaluations",
        headers=OPERATOR,
        json={"agentType": "REQUIREMENTS", "qualifiedPrompt": "requirements.v1", "model": "p/m"},
    )
    assert response.status_code == 202, response.text  # CP-API-012
    accepted = AcceptanceView.model_validate(response.json())
    assert accepted.accepted is True and accepted.step_run_id, accepted
    scored = client.get(f"/api/v1/governance/evaluations/{accepted.step_run_id}", headers=OPERATOR).json()
    assert scored["caseCount"] == 1, scored


def test_project_effective_policy_is_narrowed_by_the_environment(
    client: TestClient, seeded_env: dict[str, Any]
) -> None:
    """S5IF-GOV12 / D-07: organisation ∩ every target environment, strictest wins."""
    body = client.get(f"/api/v1/projects/{seeded_env['id']}/effective-policy", headers=OPERATOR).json()
    GS.ProjectPolicyView.model_validate(body)
    assert body["bindingEnvironment"] == "PRIVATE_NETWORK"
    assert "UNRESTRICTED" not in body["effective"]["allowed"]["PUBLIC"]
    assert "APPROVED_EXTERNAL" not in body["effective"]["allowed"]["INTERNAL"]


# --- Inputs and agent runs ---------------------------------------------------


def test_parse_input_route_unlocks_the_whole_chain(client: TestClient, seeded_env: dict[str, Any]) -> None:
    """S5IF-INP01: the service existed with no route; this is the cheapest gap in the document."""
    artifact_id = upload_input(client, seeded_env["id"], "clinic spec")
    response = client.post(
        f"/api/v1/projects/{seeded_env['id']}/inputs/{artifact_id}/parse",
        headers=OPERATOR,
        json={},
    )
    assert response.status_code == 202, response.text
    body = response.json()
    AcceptanceView.model_validate(body)  # CP-API-012
    assert body["stageId"] and body["stepRunId"], body

    # The answer is observed where the contract says: the input row carries the parse result.
    row = client.get(f"/api/v1/projects/{seeded_env['id']}/inputs", headers=OPERATOR).json()["items"][0]
    assert row["parseStatus"] == "PARSED" and row["nrrArtifactId"], row


def test_list_inputs_reports_parse_status(client: TestClient, seeded_env: dict[str, Any]) -> None:
    artifact_id = upload_input(client, seeded_env["id"], "spec doc")
    client.post(f"/api/v1/projects/{seeded_env['id']}/inputs/{artifact_id}/parse", headers=OPERATOR, json={})
    row = client.get(f"/api/v1/projects/{seeded_env['id']}/inputs", headers=OPERATOR).json()["items"][0]
    assert row["parseStatus"] == "PARSED"
    assert row["parserVersion"]
    assert "nrrArtifactId" in row


def test_explain_answers_with_an_artifact_id_not_the_text(client: TestClient, seeded_env: dict[str, Any]) -> None:
    """S5IF-INP05: the body is an AGENT_OUTPUT artifact, read through the content endpoint."""
    artifact_id = _validation_report(client, seeded_env)
    response = client.post(
        f"/api/v1/artifacts/{artifact_id}/explain", headers=OPERATOR, json={"projectId": seeded_env["id"]}
    )
    assert response.status_code == 200, response.text
    assert response.json()["explanationArtifactId"]


def test_explain_rejects_an_artifact_that_is_not_a_report(client: TestClient, seeded_env: dict[str, Any]) -> None:
    """Only VALIDATION_REPORT and DHIS2_IMPORT_REPORT are explainable; anything else is a 404."""
    from factory_control.testing.fakes import register_fake_artifact

    input_id = register_fake_artifact(seeded_env["id"], "BUSINESS_INPUT", "not-a-report")
    response = client.post(
        f"/api/v1/artifacts/{input_id}/explain", headers=OPERATOR, json={"projectId": seeded_env["id"]}
    )
    assert response.status_code == 404
    assert response.json()["details"]["explainable"] == ["DHIS2_IMPORT_REPORT", "VALIDATION_REPORT"]


def _validation_report(client: TestClient, seeded: dict[str, Any]) -> str:
    from factory_control.testing.fakes import register_fake_artifact

    return register_fake_artifact(seeded["id"], "VALIDATION_REPORT", "for-explain")


def test_an_unreachable_provider_answers_503_not_500(
    harness: Any, client: TestClient, seeded_env: dict[str, Any]
) -> None:
    """S5IF-G07.2: a provider S1 cannot reach is a dependency outage, not an S1 crash.

    ``list_prompt_activations`` is the live case — S3 has not put ``list_activations``
    on its public module (S5IF-UP08), so a real adapter raises before it can answer.
    """
    from factory_control.ports.protocols import ProviderUnavailableError

    def refuse() -> list[dict[str, Any]]:
        raise ProviderUnavailableError("factory_intelligence.api exports no function 'list_activations'")

    del seeded_env
    monkey = harness.runtime.intelligence
    monkey.list_prompt_activations = refuse  # type: ignore[method-assign]
    response = client.get("/api/v1/governance/prompts/activations", headers=OPERATOR)
    assert response.status_code == 503, response.text
    assert response.json()["errorCode"] == "PROVIDER_UNAVAILABLE"


def test_column_mapping_endpoint_exists_and_is_typed(client: TestClient, seeded_env: dict[str, Any]) -> None:
    """S5IF-INP03: the structured proposal is S5IF-UP07, so the transitional shape is what answers."""
    artifact_id = upload_input(client, seeded_env["id"], "grid doc")
    response = client.get(
        f"/api/v1/projects/{seeded_env['id']}/inputs/{artifact_id}/column-mapping", headers=OPERATOR
    )
    assert response.status_code == 200, response.text
    body = response.json()
    IS.ColumnMappingView.model_validate(body)
    assert body["tablePlans"] is None


def test_the_administration_pages_get_their_button_rules_from_s1(client, seeded_env, harness) -> None:
    """S5IF-G10 / FR-UI-009 at platform scope: no workflow, still no local deciding.

    Environments, Governance and Settings are opened before any project exists, so the one
    endpoint that names every action's availability had nothing to be asked about and these
    screens had no source at all.  S5 was left gating on whatever the row in front of it said
    -- for an emergency lock and a policy relaxation, that is the browser deciding who may
    touch a production instance.
    """
    harness.execution.add_environment("dev", environment_class="TEST")
    harness.execution.add_environment("prod", environment_class="PRODUCTION", locked=True)
    harness.execution.add_environment("retired", environment_class="DEV", enabled=False)

    body = client.get("/api/v1/allowed-actions", headers=OPERATOR).json()
    assert body["scope"] == "platform"
    assert body["evaluatedAt"]

    by_key = {(entry["action"], entry.get("targetId")) for entry in body["allowed"]}
    assert ("environment.create", None) in by_key
    assert ("settings.manage", None) in by_key
    assert ("governance.policy.relax", None) in by_key
    # every environment S4 knows about is answered for, keyed by its environment key
    for key in ("dev", "prod"):
        for action in ("environment.edit", "environment.test_connection", "environment.emergency_lock"):
            assert (action, key) in by_key, f"{action} missing for {key}"

    lock = next(
        entry
        for entry in body["allowed"]
        if entry["action"] == "environment.emergency_lock" and entry.get("targetId") == "prod"
    )
    assert lock["details"]["locked"] is True, (
        "the answer has to carry the state S4 is enforcing, so the control reads as unlock"
    )

    # A disabled environment is S4's answer to the Administration pages too: the operations
    # about the instance close, the ones about the row stay, or nobody could switch it back on.
    refused = {
        (entry["action"], entry.get("targetId"))
        for entry in body["unavailable"]
        if entry.get("targetId") == "retired"
    }
    assert refused == {
        ("environment.disable", "retired"),
        ("environment.test_connection", "retired"),
        ("environment.probe_capabilities", "retired"),
    }, refused
    reasons = {entry["reasonCode"] for entry in body["unavailable"] if entry.get("targetId") == "retired"}
    assert reasons == {"ENVIRONMENT_DISABLED"}, reasons
    assert ("environment.edit", "retired") in by_key


def test_an_environment_out_of_service_refuses_the_instance_operations(client, harness) -> None:
    """The button rules and the write answer the same way, under either provider.

    S4 refuses operations against a DISABLED environment before it considers anything else
    (``environment/loader.py``: the status check precedes ``allow_unprobed``) and refuses a second
    disable (``rules.can_disable``).  The ``fakes`` deployment used to accept all of them, so a
    measurement taken here said 200 where the real instance answers 409 -- and the console's own
    ``allowed-actions`` said the opposite of both.
    """
    harness.execution.add_environment("dev", environment_class="TEST")
    harness.execution.add_environment("retired", environment_class="DEV", enabled=False)

    for path, payload, key in (
        ("/disable", {"reason": "again"}, "disable-again"),
        ("/test-connection", {"requiredScopes": ["discovery"]}, "test-on-disabled"),
        ("/probe-capabilities", None, "probe-on-disabled"),
    ):
        response = client.post(
            f"/api/v1/environments/retired{path}",
            headers={**OPERATOR, "Idempotency-Key": key},
            json=payload or {},
        )
        assert response.status_code == 409, f"{path}: {response.text}"
        body = response.json()
        assert body["errorCode"] == "INVALID_STATE", path
        assert body["details"]["status"] == "DISABLED", path

    assert client.post(
        "/api/v1/environments/dev/disable", headers={**OPERATOR, "Idempotency-Key": "disable-dev"}, json={"reason": "first"}
    ).status_code == 200

    # ...while the two controls that could still bring it back, or explain it, stay open.
    assert client.patch(
        "/api/v1/environments/retired", headers=OPERATOR, json={"name": "kept editable"}
    ).status_code == 200
    assert client.post(
        "/api/v1/environments/retired/emergency-lock",
        headers={**OPERATOR, "Idempotency-Key": "lock-retired"},
        json={"locked": True, "reason": "a lock on a retired row is still liftable"},
    ).status_code == 200


def test_package_files_is_404_not_generated_when_nothing_was_compiled(
    client: TestClient, seeded_env: dict[str, Any]
) -> None:
    """CP-UI-022 retired the ``kind=NONE`` marker: an unbuilt version is a guided 404, not a 200 shell."""
    response = client.get(
        f"/api/v1/projects/{seeded_env['id']}/package-files", headers=OPERATOR
    )

    assert response.status_code == 404, response.text
    body = response.json()
    assert body["errorCode"] == "NOT_FOUND"
    assert body["details"]["reason"] == "NOT_GENERATED"
    assert body["details"]["requiredStage"] == "BASELINE_COMPILE"


def test_package_files_keeps_the_role_and_the_hash_of_every_file(
    harness: Any, client: TestClient, seeded_env: dict[str, Any]
) -> None:
    """The view is a file list, so the two things an operator checks are hash and object count.

    ``objectCount`` is null for a file that carries no object: zero would claim the package itself
    was empty, which is the wrong reassurance to give next to a gate.
    """
    from factory_control.ports import dto

    pid = seeded_env["id"]
    harness.blueprint.package_manifests[pid] = dto.PackageManifest(
        project_id=pid,
        blueprint_version="0.3.0",
        build_id="bld-1",
        kind="ENVIRONMENT",
        environment_key="dev",
        package_hash="sha256:pkg",
        files=[
            dto.PackageFile(path="metadata.json", role="payload", content_hash="sha256:a", object_count=6),
            dto.PackageFile(path="views/dataElements.json", role="view", dhis2_type="dataElement", content_hash="sha256:b", object_count=4),
            dto.PackageFile(path="README.md", role="document", content_hash="sha256:c"),
        ],
    )

    response = client.get(f"/api/v1/projects/{pid}/package-files", headers=OPERATOR)
    assert response.status_code == 200, response.text
    body = response.json()

    assert body["buildId"] == "bld-1" and body["packageHash"] == "sha256:pkg"
    assert body["kind"] == "ENVIRONMENT", "the NONE marker is gone; a real build kind stays"
    assert [item["path"] for item in body["files"]] == [
        "metadata.json",
        "views/dataElements.json",
        "README.md",
    ]
    payload, view, readme = body["files"]
    assert payload["role"] == "payload" and payload["objectCount"] == 6
    assert view["dhis2Type"] == "dataElement" and view["objectCount"] == 4
    assert readme["objectCount"] is None, "no object is not the same answer as zero objects"
