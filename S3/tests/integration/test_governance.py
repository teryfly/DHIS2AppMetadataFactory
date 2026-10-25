"""``api.governance(action, payload)`` — S3's operator surface, as S1 forwards it
(CP-API-007, AI-REG-001/002/003/004/005, AI-POL-002/005, AI-COST-001/002, AI-EVAL-001,
AI-LLM-002/005, 00 doc 6.2).

S1 stores nothing of its own, so the action vocabulary in ``governance/dispatcher.py`` *is*
the REST-visible shape of every governance rule S3 has.  Three things are pinned here that no
other file reaches:

* **the routing** — an unknown action fails loudly with the list of known ones and a malformed
  payload names the key that was missing, because a typo in an operator button must not look
  like a working endpoint;
* **the transactional rule** — ``governance`` is deliberately *not* marked external I/O
  (``S1/src/factory_control/adapters/registry.py:95``), because S1 may call it with its own
  transaction open.  So a state-changing action with no transaction opens and commits one and
  leaves nothing open; with a session supplied it writes into that session; inside a caller's
  transaction it enlists, and so rolls back with it; and a read commits nothing of the caller's
  either.  An action that would need external I/O is refused with a pointer to the public
  function that may do it rather than quietly opening a nested transaction (00 doc 6.2);
* **a gate that is only half a gate** — the budget *switch* (`budget.set`) writes the row the
  runtime check has obeyed since the MVP (SRS 12, AI-COST-002); both halves are asserted here.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest
from factory_foundation.api import (
    AuditQuery,
    FactoryError,
    NotFoundError,
    ValidationError,
    has_active_uow,
    is_external_io,
    new_id,
    query_audit,
    unit_of_work,
)
from sqlalchemy import func, select

from factory_intelligence import api
from factory_intelligence.cost.usage import ROLLUP_DIMENSIONS
from factory_intelligence.enums import CURRENT_MILESTONE, AgentType, ProviderClass
from factory_intelligence.errors import (
    AiError,
    BudgetInvalid,
    GovernanceActionUnknown,
    PromptActivationBlocked,
    PromptNotFound,
)
from factory_intelligence.governance.dispatcher import ACTIONS, dispatch, known_actions
from factory_intelligence.llm.registry import candidates_for_agent
from factory_intelligence.llm.types import HealthStatus
from factory_intelligence.models.eval import EvalRunRow
from factory_intelligence.models.policy import OrgPolicyRow, PolicyRelaxationRow
from factory_intelligence.models.registry import (
    PromptActivationRow,
    PromptVersionRow,
    ProviderRow,
    RegressionRefRow,
)
from factory_intelligence.prompts.registry import discover_sources
from factory_intelligence.testing.harness import IntelligenceHarness
from tests.integration.agent_runtime import (
    add_operation,
    answer,
    clear_prompt_activations,
    install_mock_model,
    open_draft,
    request_for,
    requirement_item,
    script,
    seed_project,
    unique_project_id,
)

#: Every action the operator surface routes (``governance/dispatcher.py`` ``ACTIONS``).
DECLARED_ACTIONS: list[str] = [
    "provider.register",
    "provider.enable",
    "provider.update",
    "provider.list",
    "model.register",
    "model.update",
    "model.enable",
    "model.list",
    "prompt.list",
    "prompt.get",
    "prompt.activate",
    "prompt.source.reload",
    "regression.record",
    "policy.org.get",
    "policy.org.relax",
    # AI-GOV-006: the project's effective policy is a read the workbench needs on its own
    "policy.effective.get",
    "budget.set",
    "budget.get",
    "usage.rollup",
    # AI-INP-013/014/015, forwarded by S1's input and NRR endpoints (CP-UI-007, CP-UI-003)
    "input.parseList",
    "input.columnMapping",
    "nrr.query",
    "eval.list",
    "eval.get",
    "eval.run",
    "agent.list",
    "similar.search",
]

#: S1/src/factory_control/adapters/registry.py:95 — ``IntelligenceAdapter.EXTERNAL_IO_METHODS``.
#: Copied rather than imported: S1 depends on S3 and never the other way round (00 doc 5).
S1_EXTERNAL_IO_METHODS = {"parse_input", "start_agent_run", "resume_agent_run", "explain_issues"}

#: a packaged prompt source, so these tests name a version that start-up really registered
CONSOLIDATE = "requirements.consolidate"

RELAX_INTERNAL = {
    "change": {"level": "INTERNAL", "addClasses": [ProviderClass.APPROVED_EXTERNAL.value]},
    "reason": "the vendor signed the data-processing addendum",
    "operator": "gov-ops",
}


class _Rollback(RuntimeError):
    """Raised inside a caller's transaction to prove an action enlisted in it."""


# --- helpers ---------------------------------------------------------------------


def _count(model: type[Any], **filters: Any) -> int:
    """Rows of one ``ai`` table, narrowed by column.

    Narrowed on purpose: other test sessions write to the same schemas, and an assertion about
    the whole table would be an assertion about whichever run reached it first.
    """
    with unit_of_work() as session:
        statement = select(func.count()).select_from(model)
        if filters:
            statement = statement.filter_by(**filters)
        return int(session.scalar(statement) or 0)


def _provider_names() -> list[str]:
    return [str(item["name"]) for item in api.governance("provider.list", {})["items"]]


def _models_of(provider: str) -> list[dict[str, Any]]:
    return [item for item in api.governance("model.list", {})["items"] if item["provider"] == provider]


def _model_refs() -> list[str]:
    return [str(item["ref"]) for item in api.governance("model.list", {})["items"]]


def _events(event_type: str, operator: str | None = None) -> list[Any]:
    return list(query_audit(AuditQuery(event_type=event_type, operator_name=operator, limit=100)).items)


def _register_provider(
    name: str = "gov", provider_class: ProviderClass | str = ProviderClass.LOCAL_ONLY
) -> Any:
    declared = (
        provider_class.value
        if isinstance(provider_class, ProviderClass)
        else provider_class
    )
    return api.governance("provider.register", {"name": name, "providerClass": declared})


def _register_model(provider: str = "gov", model: str = "gov-1") -> Any:
    _register_provider(provider)
    return api.governance(
        "model.register",
        {
            "provider": provider,
            "model": model,
            "capabilities": {"structuredOutput": True, "maxContextTokens": 32_000},
            "applicableAgents": {AgentType.REQUIREMENTS.value: 1},
            "unitPriceInputPer1k": 0.5,
            "unitPriceOutputPer1k": 2.0,
        },
    )


def _prompt_id(name: str = CONSOLIDATE, version: int = 1) -> str:
    items = [
        item
        for item in api.governance("prompt.list", {})["items"]
        if item["name"] == name and item["version"] == version
    ]
    assert items, f"the packaged prompt {name}.v{version} is not registered by start-up"
    return str(items[0]["id"])


def _seed_eval_run(
    *,
    agent_type: AgentType = AgentType.REQUIREMENTS,
    prompt_name: str = CONSOLIDATE,
    prompt_version: int = 1,
    model: str = "gov/gov-1",
    score: float = 0.97,
    passed: bool = True,
) -> str:
    """Write ``ai.eval_run`` with the ORM.

    The evidence AI-REG-003 gates on cannot arrive through this surface in a test: ``eval.run``
    refuses here because running an evaluation is external I/O, so the activation tests state
    the row directly rather than through a verb that is designed to refuse.
    """
    run_id = new_id("aeval")
    with unit_of_work() as session:
        session.add(
            EvalRunRow(
                id=run_id,
                agent_type=agent_type.value,
                prompt_version_id=_prompt_id(prompt_name, prompt_version),
                prompt_name=prompt_name,
                prompt_version=prompt_version,
                model=model,
                provider=model.partition("/")[0],
                eval_set_version="v1",
                score=score,
                passed=passed,
                case_count=12,
                summary={"cases": 12, "matched": 12},
                run_by="eval-bot",
            )
        )
    return run_id


def _gate_both_halves(operator: str = "gate-ops", model_list: list[str] | None = None) -> dict[str, Any]:
    """Give ``prompt.activate`` its evaluation and its regression, then return the payload."""
    _register_model()
    _seed_eval_run()
    api.governance("regression.record", {"promptName": CONSOLIDATE, "promptVersion": 1})
    return {
        "agentType": AgentType.REQUIREMENTS.value,
        "promptName": CONSOLIDATE,
        "promptVersion": 1,
        "modelList": model_list or ["gov/gov-1"],
        "operator": operator,
    }


# --- the vocabulary (CP-API-007) --------------------------------------------------


def test_the_routable_actions_are_exactly_the_declared_vocabulary(
    harness: IntelligenceHarness,
) -> None:
    """S1 forwards the action string untouched, so this list is what a UI may call: a name
    added, renamed or dropped here is a REST-visible change (CP-API-007)."""
    _ = harness
    assert sorted(DECLARED_ACTIONS) == known_actions()
    assert set(ACTIONS) == set(DECLARED_ACTIONS)
    assert len(DECLARED_ACTIONS) == 27


@pytest.mark.parametrize(
    "action",
    ["provider.registr", "prompt.reload", "Budget.get", "model.list-disabled", "S3.agent.list", ""],
    ids=["typo", "wrong-name-for-reload", "wrong-case", "dashes", "prefixed", "empty"],
)
def test_an_unknown_action_fails_with_the_list_of_known_ones(
    harness: IntelligenceHarness, action: str
) -> None:
    """A typo must not answer with a plausible empty result.  The refusal is a mapped S3 error
    — so S1 renders an error rather than a crash — and it carries the whole vocabulary the
    caller could have meant."""
    _ = harness
    with pytest.raises(GovernanceActionUnknown) as raised:
        api.governance(action, {})

    error = raised.value
    assert error.error_code == "AI_GOVERNANCE_ACTION_UNKNOWN"
    assert error.retryable is False, "an unspelled action will not spell itself right on a retry"
    assert error.to_dict()["errorCode"] == "AI_GOVERNANCE_ACTION_UNKNOWN"
    assert repr(action) in str(error)
    assert error.details["action"] == action
    assert error.details["known"] == known_actions()
    assert _count(ProviderRow, name="gov") == 0, "an unroutable action routed nowhere and wrote nothing"


@pytest.mark.parametrize(
    ("action", "payload", "missing"),
    [
        ("provider.register", {"providerClass": "LOCAL_ONLY"}, "name"),
        ("provider.register", {"name": "gov"}, "providerClass"),
        ("provider.register", {"name": " ", "providerClass": "LOCAL_ONLY"}, "name"),
        ("provider.enable", {"enabled": False}, "name"),
        ("model.register", {"model": "gov-1"}, "provider"),
        ("model.register", {"provider": "gov"}, "model"),
        ("model.enable", {"enabled": False}, "ref"),
        ("prompt.activate", {"agentType": "REQUIREMENTS"}, "promptName"),
        (
            "prompt.activate",
            {"agentType": "REQUIREMENTS", "promptName": "p", "promptVersion": 1},
            "modelList",
        ),
        ("prompt.get", {"version": 1}, "id"),
        ("regression.record", {"promptVersion": 1}, "promptName"),
        ("policy.org.relax", {"reason": "r", "operator": "malformed-ops"}, "change"),
        (
            "policy.org.relax",
            {"change": {"level": "INTERNAL"}, "operator": "malformed-ops"},
            "reason",
        ),
        ("policy.org.relax", {"change": {"level": "INTERNAL"}, "reason": "r"}, "operator"),
        ("budget.get", {}, "projectId"),
        # ``usage.rollup`` with no project is legal (AI-COST-001 spans projects); a NRR
        # query still has to say which project it means
        ("nrr.query", {}, "projectId"),
        ("eval.get", {}, "id"),
    ],
    ids=[
        "provider-without-name",
        "provider-without-class",
        "provider-with-blank-name",
        "provider-enable-without-name",
        "model-without-provider",
        "model-without-name",
        "model-enable-without-ref",
        "activate-without-prompt-name",
        "activate-without-model-list",
        "prompt-get-without-id",
        "regression-without-prompt-name",
        "relax-without-change",
        "relax-without-reason",
        "relax-without-operator",
        "budget-get-without-project",
        "nrr-query-without-project",
        "eval-get-without-id",
    ],
)
def test_a_malformed_payload_names_the_key_that_is_missing(
    harness: IntelligenceHarness, action: str, payload: dict[str, Any], missing: str
) -> None:
    """The payload comes from an HTTP body S1 never looks inside (CP-API-007), so the refusal
    has to name the key it wanted and the keys it was given — and write nothing.  A whitespace
    string counts as absent, or a blank form field would register a provider called ``" "``."""
    _ = harness
    with pytest.raises(ValidationError) as raised:
        api.governance(action, payload)

    assert raised.value.error_code == "VALIDATION_ERROR"
    assert raised.value.details["missing"] == missing
    assert raised.value.details["given"] == sorted(payload)
    assert missing in str(raised.value)
    assert _count(ProviderRow, name="gov") == 0
    assert _count(PolicyRelaxationRow, operator="malformed-ops") == 0


@pytest.mark.parametrize(
    ("action", "payload", "key", "value"),
    [
        (
            "provider.register",
            {"name": "gov", "provider_class": ProviderClass.PRIVATE_NETWORK.value},
            "providerClass",
            ProviderClass.PRIVATE_NETWORK.value,
        ),
        (
            "model.register",
            {"provider": "gov", "model": "gov-1", "unit_price_input_per_1k": 0.25},
            "unitPriceInputPer1k",
            0.25,
        ),
        ("model.enable", {"providerModel": "gov/gov-1", "enabled": False}, "ref", "gov/gov-1"),
        ("budget.get", {"project_id": "prj-gov"}, "state", "UNSET"),
        ("usage.rollup", {"project_id": "prj-gov", "group_by": ["model"]}, "groupBy", ["model"]),
        ("prompt.get", {"promptVersionId": "apvr-set-below"}, "name", CONSOLIDATE),
        (
            "model.register",
            {"provider": "gov", "model": "gov-agents", "applicable_agents": {"MAPPING": 3}},
            "applicableAgents",
            {AgentType.MAPPING.value: 3},
        ),
    ],
    ids=[
        "provider-register",
        "model-register-price",
        "model-enable",
        "budget-get",
        "usage-rollup",
        "prompt-get",
        "model-register-agents",
    ],
)
def test_a_key_is_read_in_either_spelling(
    harness: IntelligenceHarness, action: str, payload: dict[str, Any], key: str, value: Any
) -> None:
    """A UI cannot be told which casing S3 happened to read, so both spellings are accepted and
    neither is silently ignored — the answer carries the value that was sent (dispatcher
    docstring, CP-API-007: S1 hands the dictionary through untouched)."""
    _ = harness
    if action == "model.register":
        _register_provider()
    if action == "model.enable":
        _register_model()
    if action == "prompt.get":
        payload = {"promptVersionId": _prompt_id()}

    answer = api.governance(action, payload)

    assert answer[key] == value


# --- providers and models (AI-REG-004, AI-LLM-002/005) ----------------------------


def test_provider_register_answers_the_row_and_audits_the_registration(
    harness: IntelligenceHarness,
) -> None:
    """AI-REG-004: a provider is registration data with a class, an adapter and a Secret
    *reference*, and the act of registering it is auditable with the human who did it."""
    _ = harness
    answer = api.governance(
        "provider.register",
        {
            "name": "gov",
            "providerClass": ProviderClass.APPROVED_EXTERNAL.value,
            "endpoint": "https://vendor.example/v1",
            "apiKeySecretRef": "env://GOV_KEY",
            "adapter": "openai_compat",
            "operator": "reg-ops",
        },
    )

    assert sorted(answer) == sorted(
        [
            "id",
            "name",
            "providerClass",
            "endpoint",
            "enabled",
            "adapter",
            "apiKeySecretRef",
            "vendor",
            "suggestedModels",
        ]
    )
    assert answer["id"].startswith("apvd")
    assert answer["providerClass"] == "APPROVED_EXTERNAL"
    assert answer["enabled"] is True
    assert answer["apiKeySecretRef"] == "env://GOV_KEY"
    assert (answer["vendor"], answer["suggestedModels"]) == ("custom", [])

    assert "gov" in _provider_names()
    assert _count(ProviderRow, name="gov") == 1
    events = _events("ai.model.registered", "reg-ops")
    assert len(events) == 1
    assert events[0].operator_type == "USER"
    assert events[0].source_subsystem == "S3"
    assert events[0].after is not None
    assert events[0].after["providerClass"] == "APPROVED_EXTERNAL"


def test_a_provider_probe_answers_why_it_is_unreachable(
    harness: IntelligenceHarness,
) -> None:
    """S5IF-GOV20: the operator's question is "why could the stage not get a reply".

    Three different failures must stay three different answers — an address that answers with a
    refusal is *reachable*, one that times out is not, and one nobody filled in is a registration
    mistake. None of them may leave this function as an exception, because the caller is a button.
    """
    _ = harness
    api.register_provider(
        {
            "name": "probe",
            "providerClass": ProviderClass.APPROVED_EXTERNAL.value,
            "endpoint": "https://vendor.example/v1",
            "apiKeySecretRef": "env://PROBE_KEY",
        }
    )

    class _Answering:
        def health(self) -> HealthStatus:
            return HealthStatus(healthy=True, detail=None)

    class _Refusing:
        def health(self) -> HealthStatus:
            return HealthStatus(healthy=False, detail="connect timeout after 5.0s")

    # The probe reaches exactly one member of LLMProvider; a double that also pretended to
    # generate would be answering a question nobody asked (arg-type, partial double — same
    # trade as `test_llm_record_replay.py`).
    api.set_llm_provider(_Answering())  # type: ignore[arg-type]
    answer = api.check_provider_health("probe")
    assert answer.healthy is True and answer.detail is None
    # AC-13: the answer names the provider and the transport's sentence, never the key reference
    assert answer.ref == "probe"
    assert "env://" not in str(answer.model_dump())

    api.set_llm_provider(_Refusing())  # type: ignore[arg-type]
    refused = api.check_provider_health("probe")
    assert refused.healthy is False and refused.detail == "connect timeout after 5.0s"

    # A provider registered with no address cannot be probed at all; the adapter's own complaint is
    # reported as an unhealthy answer rather than raised through a REST route.
    api.set_llm_provider(None)
    api.register_provider(
        {"name": "no-address", "providerClass": ProviderClass.LOCAL_ONLY.value}
    )
    missing = api.check_provider_health("no-address")
    assert missing.healthy is False and "no endpoint" in str(missing.detail)

    with pytest.raises(NotFoundError):
        api.check_provider_health("never-registered")


def test_a_literal_api_key_is_refused_and_nothing_is_registered(
    harness: IntelligenceHarness,
) -> None:
    """AI-LLM-005, 00 doc 6.9: the registry holds a reference and never a value, and the
    refusal happens before the row exists — a provider registered with a key in it is a leak
    no later error can un-leak."""
    _ = harness
    with pytest.raises(ValidationError) as raised:
        api.governance(
            "provider.register",
            {
                "name": "gov",
                "providerClass": "LOCAL_ONLY",
                "apiKeySecretRef": "sk-live-9f2c0a",
                "operator": "literal-key-ops",
            },
        )

    assert "secret reference" in str(raised.value).lower()
    assert "gov" not in _provider_names()
    assert _count(ProviderRow, name="gov") == 0
    assert _events("ai.model.registered", "literal-key-ops") == []


@pytest.mark.parametrize(
    "provider_class",
    ["LOCAL", "local_only", "PHI_PII", "PRIVATE"],
    ids=["truncated", "wrong-case", "not-a-class", "invented"],
)
def test_a_provider_without_a_known_class_is_refused(
    harness: IntelligenceHarness, provider_class: str
) -> None:
    """AI-LLM-002: ``provider_class`` is never defaulted, because guessing ``LOCAL_ONLY`` would
    be a silent promise that nothing leaves the network."""
    _ = harness
    with pytest.raises(ValidationError) as raised:
        api.governance("provider.register", {"name": "gov", "providerClass": provider_class})

    assert "provider class" in str(raised.value) or "providerClass" in str(raised.value)
    assert raised.value.details["providerClass"] == provider_class
    assert _count(ProviderRow, name="gov") == 0


def test_registering_one_provider_name_twice_updates_the_row(
    harness: IntelligenceHarness,
) -> None:
    """Re-registration is how an operator edits an endpoint or switches a provider off; two
    rows for one name would make ``model.list`` and the policy engine disagree about which
    provider a model belongs to (AI-REG-004's unique name)."""
    _ = harness
    first = _register_provider()
    second = api.governance(
        "provider.register",
        {
            "name": "gov",
            "providerClass": ProviderClass.PRIVATE_NETWORK.value,
            "enabled": False,
        },
    )

    assert second["id"] == first["id"]
    assert _count(ProviderRow, name="gov") == 1
    mine = [item for item in api.governance("provider.list", {})["items"] if item["name"] == "gov"]
    assert len(mine) == 1
    assert mine[0]["providerClass"] == "PRIVATE_NETWORK"
    assert mine[0]["enabled"] is False


def test_model_register_requires_a_registered_provider(
    harness: IntelligenceHarness,
) -> None:
    """A model with no provider has no provider class, and the class is what every policy
    verdict is computed from (AI-POL-002, AI-REG-004)."""
    _ = harness
    with pytest.raises(NotFoundError) as raised:
        api.governance("model.register", {"provider": "absent", "model": "gov-1"})

    assert "absent" in str(raised.value)
    assert _count(ProviderRow, name="absent") == 0
    assert _models_of("absent") == []


def test_model_register_answers_the_price_and_the_agent_priorities(
    harness: IntelligenceHarness,
) -> None:
    """AI-REG-004's whole row is operator-visible — including the per-1k prices AI-COST-001 bills
    with and the per-agent priorities AI-POL-007 walks — because a cost nobody can read is a
    cost nobody can challenge."""
    _ = harness
    answer = _register_model()

    assert sorted(answer) == sorted(
        [
            "id",
            "provider",
            "model",
            "ref",
            "providerClass",
            "capabilities",
            "enabled",
            "unitPriceInputPer1k",
            "unitPriceOutputPer1k",
            "applicableAgents",
            "lastEvalScore",
            "lastEvalRunId",
            # AI-LLM-003: a model registration may carry its own address and Key reference
            "adapter",
            "endpoint",
            "apiKeySecretRef",
        ]
    )
    assert answer["id"].startswith("amdl")
    assert answer["ref"] == "gov/gov-1"
    assert answer["providerClass"] == ProviderClass.LOCAL_ONLY.value
    assert answer["capabilities"] == {
        "structuredOutput": True,
        "vision": False,
        "maxContextTokens": 32_000,
    }
    assert answer["unitPriceInputPer1k"] == 0.5
    assert answer["applicableAgents"] == {AgentType.REQUIREMENTS.value: 1}
    assert answer["lastEvalScore"] is None, "AI-REG-005's standing is empty until an eval lands"


def test_a_model_registered_without_capabilities_is_still_callable(
    harness: IntelligenceHarness,
) -> None:
    """The defaults have to mean "usable", because the console leaves the box alone.

    ``select`` asks every candidate two questions before it tries one: can it answer in a
    checkable shape, and does the request fit inside its window.  A registration that declares
    neither used to answer "no" to both — ``structured_output`` off and a ``0`` token window —
    so the model was listed, claimed by agents, and then denied on every single call with
    "exceeds its 0 window".  The stand-in implementation repaired the same omission to 8000,
    which is exactly why no suite noticed.
    """
    _ = harness
    _register_provider()
    answer = api.governance(
        "model.register",
        {"provider": "gov", "model": "no-caps", "applicableAgents": {AgentType.MAPPING.value: 1}},
    )

    assert answer["capabilities"] == {
        "structuredOutput": True,
        "vision": False,
        "maxContextTokens": 8000,
    }

    candidates = candidates_for_agent(AgentType.MAPPING, needed_tokens=4000)
    assert [item.model for item in candidates] == ["no-caps"]
    assert candidates[0].fits(4000, needs_vision=False, needs_structured=True) is None


def test_editing_a_model_changes_only_the_fields_the_caller_named(
    harness: IntelligenceHarness,
) -> None:
    """GOV06's edit is not a re-registration, and the two nullable override columns are why.

    ``register_model`` writes every column, so a caller that omits AI-LLM-003's per-model address
    and Key reference writes NULL over them — and S1's read path then back-fills the provider's own
    address, so the console shows a value nobody set and the override can never come off.  Fixing a
    price has to leave the rest of the row alone.
    """
    _ = harness
    _register_provider()
    api.governance(
        "model.register",
        {
            "provider": "gov",
            "model": "gov-1",
            "capabilities": {"structuredOutput": True, "vision": True, "maxContextTokens": 32_000},
            "applicableAgents": {AgentType.MAPPING.value: 1},
            "unitPriceInputPer1k": 0.5,
            "unitPriceOutputPer1k": 2.0,
            "endpoint": "https://gov.example/v1",
            "apiKeySecretRef": "env://GOV_KEY",
        },
    )

    answer = api.governance(
        "model.update",
        {"ref": "gov/gov-1", "unitPriceInputPer1k": 0.25, "operator": "price-ops"},
    )

    assert answer["ref"] == "gov/gov-1"
    assert answer["unitPriceInputPer1k"] == 0.25
    assert answer["unitPriceOutputPer1k"] == 2.0, "an unnamed price is not a zero price"
    assert answer["endpoint"] == "https://gov.example/v1"
    assert answer["apiKeySecretRef"] == "env://GOV_KEY"
    assert answer["capabilities"] == {
        "structuredOutput": True,
        "vision": True,
        "maxContextTokens": 32_000,
    }
    assert answer["applicableAgents"] == {AgentType.MAPPING.value: 1}


def test_a_capability_edit_that_names_one_key_leaves_the_other_two_as_they_were(
    harness: IntelligenceHarness,
) -> None:
    """A patch that says one thing about one capability must not rewrite the other two.

    The typed view fills what is missing with its own defaults, so raising the context window
    through it would also have switched vision back off — the kind of change an operator never
    made and would only discover when an image request started failing.
    """
    _ = harness
    _register_model()  # structured output on, vision off, 32 000 tokens

    answer = api.governance(
        "model.update",
        {"ref": "gov/gov-1", "capabilities": {"maxContextTokens": 64_000}},
    )

    assert answer["capabilities"] == {
        "structuredOutput": True,
        "vision": False,
        "maxContextTokens": 64_000,
    }


def test_an_edit_records_the_previous_values_alongside_the_new_ones(
    harness: IntelligenceHarness,
) -> None:
    """A registration and an edit share one audit event type, so the payload is the only thing
    that tells them apart — and a correction nobody can read back is not an audit trail."""
    _ = harness
    _register_model()

    api.governance(
        "model.update",
        {"ref": "gov/gov-1", "enabled": False, "operator": "edit-ops"},
    )

    # a name this file uses nowhere else: the audit query is by operator, and the switch-off test
    # below counts the same event type under its own operator
    events = _events("ai.model.registered", "edit-ops")
    assert len(events) == 1
    assert events[0].before == {"enabled": True}
    assert events[0].after is not None
    assert events[0].after["action"] == "updateModel"
    assert events[0].after["enabled"] is False


def test_an_edit_that_names_nothing_is_refused_rather_than_applied(
    harness: IntelligenceHarness,
) -> None:
    """An empty patch would write nothing and answer 200 with the untouched row, which reads to an
    operator exactly like a change that worked."""
    _ = harness
    _register_model()

    with pytest.raises(ValidationError) as raised:
        api.governance("model.update", {"ref": "gov/gov-1"})

    assert "at least one field" in str(raised.value)
    assert _models_of("gov")[0]["enabled"] is True


def test_disabling_a_model_takes_it_out_of_rotation_without_deleting_it(
    harness: IntelligenceHarness,
) -> None:
    """AI-REG-005: an operator switches a model off; the row, its price and its evaluation
    history have to survive, and the default listing keeps showing what was switched off —
    otherwise ``model.list`` would answer "never registered" to a deliberate decision."""
    _ = harness
    _register_model()

    answer = api.governance(
        "model.enable", {"ref": "gov/gov-1", "enabled": False, "operator": "off-ops"}
    )
    assert answer == {"ref": "gov/gov-1", "enabled": False}

    assert [item["enabled"] for item in _models_of("gov")] == [False]
    visible = api.governance("model.list", {"includeDisabled": False})["items"]
    assert [item["ref"] for item in visible if item["provider"] == "gov"] == []
    assert len(_events("ai.model.registered", "off-ops")) == 1

    assert api.governance("model.enable", {"ref": "gov/gov-1", "enabled": True})["enabled"] is True
    assert [item["enabled"] for item in _models_of("gov")] == [True]


@pytest.mark.parametrize("ref", ["gov/typo", "gov-1"], ids=["unknown-model", "missing-slash"])
def test_model_enable_refuses_a_ref_that_is_not_registered(
    harness: IntelligenceHarness, ref: str
) -> None:
    """A model ref is ``provider/model``; saying so is the difference between an operator
    fixing a typo and an operator filing a bug."""
    _ = harness
    _register_provider()
    with pytest.raises(NotFoundError) as raised:
        api.governance("model.enable", {"ref": ref})

    assert "provider/model" in str(raised.value)
    assert _models_of("gov") == []


def test_model_list_narrows_to_the_agents_that_declared_a_model(
    harness: IntelligenceHarness,
) -> None:
    """AI-POL-007's candidate list is data: a model no agent declared is registered but
    unreachable, and ``model.list?agentType`` is how an operator sees that."""
    _ = harness
    _register_model(model="for-requirements")
    api.governance(
        "model.register",
        {"provider": "gov", "model": "for-mapping", "applicableAgents": {AgentType.MAPPING.value: 1}},
    )

    def models_for(agent: AgentType) -> list[str]:
        items = api.governance("model.list", {"agentType": agent.value})["items"]
        return sorted(str(item["model"]) for item in items if item["provider"] == "gov")

    assert models_for(AgentType.REQUIREMENTS) == ["for-requirements"]
    assert models_for(AgentType.MAPPING) == ["for-mapping"]
    assert len(_models_of("gov")) == 2


def test_provider_enable_switches_the_class_gate_and_leaves_the_row(
    harness: IntelligenceHarness,
) -> None:
    """Disabling a *provider* removes every model under it from the candidate walk at once —
    which is why the answer is a switch and not a deletion (AI-REG-005)."""
    _ = harness
    _register_model()
    api.governance("model.register", {"provider": "gov", "model": "gov-2"})

    answer = api.governance("provider.enable", {"name": "gov", "enabled": False})
    # S5IF-GOV03: the answer names what was switched, so a caller cannot lose the ref
    assert answer == {"ref": "gov", "enabled": False}

    mine = [item for item in api.governance("provider.list", {})["items"] if item["name"] == "gov"]
    assert [item["enabled"] for item in mine] == [False]
    assert [item["ref"] for item in _models_of("gov")] == ["gov/gov-1", "gov/gov-2"]
    hidden = api.governance("model.list", {"includeDisabled": False})["items"]
    assert [item["ref"] for item in hidden if item["provider"] == "gov"] == []


# --- prompts (AI-REG-001/002/003, AI-AC-07) ---------------------------------------


def test_prompt_list_reports_every_registered_version_and_which_is_active(
    harness: IntelligenceHarness,
) -> None:
    """AI-REG-002: "a version exists" and "this version is in use" are different facts, so the
    operator view has to show both halves."""
    _ = harness
    clear_prompt_activations()
    items = api.governance("prompt.list", {})["items"]

    assert items
    consolidated = [item for item in items if item["name"] == CONSOLIDATE]
    assert not any(item["active"] for item in consolidated), "nothing is active at start-up"
    assert f"{CONSOLIDATE}.v1" in {item["qualifiedName"] for item in items}
    assert sorted(items[0]) == sorted(
        [
            "id",
            "name",
            "version",
            "qualifiedName",
            "agentType",
            "blueprintSchemaRange",
            "dhis2VersionRange",
            "prohibitions",
            "sourceHash",
            "active",
        ]
    )

    narrowed = api.governance("prompt.list", {"agentType": AgentType.REQUIREMENTS.value})["items"]
    assert narrowed and all(item["agentType"] == "REQUIREMENTS" for item in narrowed)
    # every agent of SRS 3.3 now ships a source, so the per-agent filter has no empty answer
    # left to demonstrate; what it must keep proving is that it returns the agent named and the
    # version that agent shipped
    for agent, qualified in (
        (AgentType.API, "api.design.v1"),
        (AgentType.UI, "ui.design.v1"),
        (AgentType.FIXTURE, "fixture.specify.v1"),
    ):
        one_agent = api.governance("prompt.list", {"agentType": agent.value})["items"]
        assert [item["qualifiedName"] for item in one_agent] == [qualified], agent.value
        assert all(item["agentType"] == agent.value for item in one_agent), agent.value


def test_prompt_get_answers_by_name_and_version_or_by_id(
    harness: IntelligenceHarness,
) -> None:
    """The body and the declaration are both operator-visible (AI-REG-001), and the two
    spellings of "which version" — a qualified name or a row id — reach the same row."""
    _ = harness
    by_id = api.governance("prompt.get", {"id": _prompt_id()})
    by_name = api.governance("prompt.get", {"name": CONSOLIDATE, "version": 1})

    assert by_id == by_name
    assert by_id["id"].startswith("apvr")
    assert sorted(by_id) == sorted(
        [
            "id",
            "name",
            "version",
            "qualifiedName",
            "active",
            "agentType",
            "systemRules",
            "body",
            "inputSchemaRef",
            "outputSchemaRef",
            "blueprintSchemaRange",
            "dhis2VersionRange",
            "prohibitions",
            "applicableStages",
            "sourceHash",
        ]
    )
    assert by_id["body"].strip(), "a registered prompt carries its prose, not only its header"


@pytest.mark.parametrize(
    ("payload", "error"),
    [
        ({"id": "apvr-does-not-exist"}, PromptNotFound),
        ({"name": CONSOLIDATE, "version": 99}, PromptNotFound),
    ],
    ids=["unknown-id", "unknown-version"],
)
def test_prompt_get_refuses_a_version_that_is_not_there(
    harness: IntelligenceHarness, payload: dict[str, Any], error: type[Exception]
) -> None:
    """AI-REG-001: an unregistered version is not an empty prompt."""
    _ = harness
    with pytest.raises(error):
        api.governance("prompt.get", payload)


def test_prompt_activate_is_refused_until_both_gates_exist(
    harness: IntelligenceHarness,
) -> None:
    """AI-REG-003 / AI-AC-07: neither half alone opens the gate — a passing evaluation without
    the test group's regression, then the regression without an evaluation, then both."""
    _ = harness
    clear_prompt_activations()
    _register_model()
    payload = {
        "agentType": AgentType.REQUIREMENTS.value,
        "promptName": CONSOLIDATE,
        "promptVersion": 1,
        "modelList": ["gov/gov-1"],
        "operator": "switch-ops",
    }

    with pytest.raises(PromptActivationBlocked) as neither:
        api.governance("prompt.activate", payload)
    assert "evaluation" in str(neither.value).lower()
    assert _count(PromptActivationRow) == 0

    _seed_eval_run()
    with pytest.raises(PromptActivationBlocked) as only_eval:
        api.governance("prompt.activate", payload)
    assert "regression" in str(only_eval.value).lower()
    assert _count(PromptActivationRow) == 0, "a half-gate must not switch anything"

    api.governance("regression.record", {"promptName": CONSOLIDATE, "promptVersion": 1})
    answer = api.governance("prompt.activate", payload)

    assert answer["agentType"] == AgentType.REQUIREMENTS.value
    assert answer["promptVersionId"] == _prompt_id()
    assert answer["modelList"] == ["gov/gov-1"]
    assert answer["activatedBy"] == "switch-ops"
    assert answer["evalRunId"] and answer["regressionPassedRef"]
    assert _count(PromptActivationRow) == 1
    active = [item for item in api.governance("prompt.list", {})["items"] if item["active"]]
    assert [item["qualifiedName"] for item in active] == [f"{CONSOLIDATE}.v1"]
    assert len(_events("ai.prompt.activated", "switch-ops")) == 1


def test_prompt_activate_refuses_a_model_that_was_never_evaluated(
    harness: IntelligenceHarness,
) -> None:
    """The gate is per (prompt version, model): a prompt that passed on one model says nothing
    about the next, so switching both at once would let an untested pairing through."""
    _ = harness
    clear_prompt_activations()
    _register_model()
    api.governance("model.register", {"provider": "gov", "model": "gov-unevaluated"})
    _seed_eval_run(model="gov/gov-1")
    api.governance("regression.record", {"promptName": CONSOLIDATE, "promptVersion": 1})

    with pytest.raises(PromptActivationBlocked) as raised:
        api.governance(
            "prompt.activate",
            {
                "agentType": AgentType.REQUIREMENTS.value,
                "promptName": CONSOLIDATE,
                "promptVersion": 1,
                "modelList": ["gov/gov-unevaluated"],
                "operator": "mismatch-ops",
            },
        )

    assert "gov/gov-unevaluated" in str(raised.value)
    assert _count(PromptActivationRow) == 0


@pytest.mark.parametrize(
    ("changes", "error"),
    [
        ({"modelList": []}, ValidationError),
        ({"agentType": AgentType.MAPPING.value}, ValidationError),
        ({"promptName": "not.a.prompt"}, PromptNotFound),
        ({"promptVersion": 7}, PromptNotFound),
    ],
    ids=["empty-model-list", "wrong-agent", "unknown-prompt", "unknown-version"],
)
def test_prompt_activate_refuses_a_request_it_cannot_honour(
    harness: IntelligenceHarness, changes: dict[str, Any], error: type[Exception]
) -> None:
    """Both gates are satisfied here, so each refusal is about the request itself: an empty
    model list refuses every run (AI-POL-007), and a version belongs to the agent that declared
    it (AI-REG-001).  Stated at the switch, not later inside a Temporal activity."""
    _ = harness
    clear_prompt_activations()
    payload = _gate_both_halves() | changes

    with pytest.raises(error):
        api.governance("prompt.activate", payload)
    assert _count(PromptActivationRow) == 0


def test_regression_record_stores_the_claim_including_a_failure(
    harness: IntelligenceHarness,
) -> None:
    """AI-REG-003's second half is another team's assertion, so S3 records it with its source
    and its verdict — including ``passed=False``, because a failure nobody wrote down is
    indistinguishable from a regression nobody ran."""
    _ = harness
    failed = api.governance(
        "regression.record",
        {
            "promptName": CONSOLIDATE,
            "promptVersion": 1,
            "passed": False,
            "recordedBy": "test-group",
            "source": "golden-project",
            "note": "two decisions moved",
        },
    )

    assert failed["id"].startswith("argr")
    assert failed["passed"] is False
    assert failed["promptVersionId"] == _prompt_id()
    assert _count(RegressionRefRow) == 1

    with pytest.raises(PromptNotFound):
        api.governance("regression.record", {"promptName": CONSOLIDATE, "promptVersion": 42})
    assert _count(RegressionRefRow) == 1, "a refusal about one version writes nothing"

    _register_model()
    _seed_eval_run()
    with pytest.raises(PromptActivationBlocked) as blocked:
        api.governance(
            "prompt.activate",
            {
                "agentType": AgentType.REQUIREMENTS.value,
                "promptName": CONSOLIDATE,
                "promptVersion": 1,
                "modelList": ["gov/gov-1"],
                "operator": "failed-regression-ops",
            },
        )
    assert "regression" in str(blocked.value).lower()


def test_prompt_source_reload_reregisters_the_packaged_sources(
    harness: IntelligenceHarness,
) -> None:
    """AI-REG-002: start-up loads the packaged sources and an operator may ask for that load
    again after a wheel upgrade — idempotently, because a second reload must not duplicate a
    version."""
    _ = harness
    expected = len(discover_sources())
    first = api.governance("prompt.source.reload", {})
    again = api.governance("prompt.source.reload", {})

    assert first["registered"] == expected
    assert again == first
    assert _count(PromptVersionRow) == expected
    assert f"{CONSOLIDATE}.v1" in first["names"]
    assert f"{CONSOLIDATE}.v1" in [item["qualifiedName"] for item in api.governance("prompt.list", {})["items"]]


def test_prompt_source_reload_accepts_a_root_from_the_payload(
    harness: IntelligenceHarness, tmp_path: Path
) -> None:
    """``root`` is the one payload key that lets an operator load sources that are not the
    packaged ones, and S1 forwards an HTTP body untouched — so the value is a string and has to
    be read as a path."""
    _ = harness
    source = tmp_path / "requirements" / "requirements.extra.v1.md"
    source.parent.mkdir(parents=True)
    source.write_text(
        '+++\nname = "requirements.extra"\nversion = 1\nagent_type = "REQUIREMENTS"\n'
        'system_rules = "none"\n+++\nOperator-loaded source body.\n',
        encoding="utf-8",
    )

    answer = api.governance("prompt.source.reload", {"root": str(tmp_path)})

    assert answer["registered"] == 1
    assert answer["names"] == ["requirements.extra.v1"]


# --- organisation policy (AI-POL-002, AI-POL-005) --------------------------------


def test_policy_org_get_answers_the_default_and_says_it_is_the_default(
    harness: IntelligenceHarness,
) -> None:
    """AI-POL-002 over CP-API-007: an unconfigured deployment answers the deny-by-default
    policy, and ``isDefault`` tells the operator that nobody chose it yet."""
    _ = harness
    answer = api.governance("policy.org.get", {})

    assert sorted(answer) == ["ceiling", "effective", "isDefault", "updatedBy"]
    assert answer["isDefault"] is True
    payload = answer["effective"]
    assert sorted(payload) == ["allowed", "sources", "warnings"]
    assert payload["sources"] == ["org"]
    assert payload["allowed"]["PHI_PII"] == []
    assert "APPROVED_EXTERNAL" not in payload["allowed"]["INTERNAL"]

    # The ceiling travels with the answer, not behind a second call: a form that only has the
    # effective matrix cannot tell a legal widening from one S3 will refuse, and the two are
    # deliberately different documents (AI-POL-001 vs AI-POL-002).
    ceiling = answer["ceiling"]
    assert sorted(ceiling) == ["allowed", "sources", "warnings"]
    assert ceiling["sources"] == ["ceiling"]
    assert "APPROVED_EXTERNAL" in ceiling["allowed"]["INTERNAL"]
    assert ceiling["allowed"]["PHI_PII"] == []
    for level, sendable in payload["allowed"].items():
        assert set(sendable) <= set(ceiling["allowed"][level]), level

    with pytest.raises(ValidationError):
        api.governance("policy.org.relax", {"reason": "no change", "operator": "ops"})
    assert api.governance("policy.org.get", {})["isDefault"] is True


def test_policy_org_relax_answers_and_reports_which_regime_checked_the_role(
    harness: IntelligenceHarness,
) -> None:
    """AI-POL-002 with D-16: the relaxation is written with its operator, and until M4 the name
    *is* the identity — the answer has to say which of the two regimes answered, because a
    caller cannot otherwise tell an unchecked name from a verified administrator."""
    _ = harness
    answer = api.governance("policy.org.relax", RELAX_INTERNAL)

    assert answer["id"].startswith("aplax")
    assert answer["scope"] == "organization"
    assert answer["operator"] == "gov-ops"
    assert answer["roleChecked"] is False
    assert answer["roleGate"] == "operator-name (MVP)"
    assert _count(OrgPolicyRow) == 1
    assert _count(PolicyRelaxationRow) == 1

    after = api.governance("policy.org.get", {})
    assert after["isDefault"] is False
    assert "APPROVED_EXTERNAL" in after["effective"]["allowed"]["INTERNAL"]
    assert after["effective"]["allowed"]["CONFIDENTIAL"] == ["LOCAL_ONLY", "PRIVATE_NETWORK"]
    assert len(_events("ai.policy.relaxed", "gov-ops")) == 1


def test_policy_org_relax_refuses_phi_pii_and_writes_nothing(
    harness: IntelligenceHarness,
) -> None:
    """AC-POL-02 / AI-POL-005: PHI_PII is not relaxable, and the refusal arrives as a mapped S3
    error rather than a validation note, with no trace that it half-happened."""
    _ = harness
    with pytest.raises(AiError) as raised:
        api.governance(
            "policy.org.relax",
            {
                "change": {"level": "PHI_PII", "addClasses": [ProviderClass.LOCAL_ONLY.value]},
                "reason": "anonymised enough",
                "operator": "phi-ops",
            },
        )

    assert raised.value.error_code == "AI_POLICY_RELAXATION_DENIED"
    assert _count(OrgPolicyRow) == 0
    assert _count(PolicyRelaxationRow) == 0
    assert api.governance("policy.org.get", {})["isDefault"] is True


def test_policy_org_relax_refuses_a_widening_past_the_matrix(
    harness: IntelligenceHarness,
) -> None:
    """AI-POL-001 is a ceiling: an operator may widen CONFIDENTIAL toward
    ``PRIVATE_NETWORK``, never toward an external class, and the refusal names the ceiling."""
    _ = harness
    with pytest.raises(AiError) as raised:
        api.governance(
            "policy.org.relax",
            {
                "change": {
                    "level": "CONFIDENTIAL",
                    "addClasses": [ProviderClass.APPROVED_EXTERNAL.value],
                },
                "reason": "we would like more",
                "operator": "ceiling-ops",
            },
        )

    assert raised.value.error_code == "AI_POLICY_RELAXATION_DENIED"
    assert "AI-POL-001" in str(raised.value)
    assert _count(OrgPolicyRow) == 0


# --- budget and usage (SRS 12, AI-COST-001/002/003) ------------------------------


def test_setting_a_budget_is_the_switch_the_runtime_rule_already_waited_for(
    harness: IntelligenceHarness,
) -> None:
    """M4 delivers the switch (AI-GOV-007), not the rule: a limit written here is the same row
    the run path has obeyed since the MVP (AI-COST-002, AI-COST-003)."""
    _ = harness
    project = unique_project_id("gov")
    assert api.governance("budget.get", {"projectId": project})["state"] == "UNSET"

    # currency normalised, steps stored sorted: ``classify`` reports the highest crossed one,
    # and a caller should not have to know that
    written = api.set_budget(project, 5.0, [0.8, 0.5], currency="usd", operator="budget-ops")
    assert (written.state, written.currency, written.limit) == ("OK", "USD", 5.0)

    facade = api.governance(
        "budget.set",
        {"projectId": project, "limit": 4.0, "thresholds": [0.5], "operator": "budget-ops"},
    )
    assert facade["limit"] == 4.0, "the deprecated facade answers the same typed view"

    seed_project(project)
    draft_id = open_draft(project, "REQUIREMENTS")
    provider = install_mock_model(AgentType.REQUIREMENTS)
    script(
        provider,
        AgentType.REQUIREMENTS,
        answer(
            operations=[
                add_operation("/requirements/items/-", requirement_item("art-0000000000"))
            ]
        ),
    )
    started = api.start_agent_run(
        request_for(
            project, AgentType.REQUIREMENTS, draft_id=draft_id, idempotency_key="gov:spend"
        )
    )
    assert started.status == "COMPLETED", started.denial_reasons

    spent = api.governance("budget.get", {"projectId": project})["spent"]
    assert spent > 0, "a limit is compared against the call ledger, not against a guess"

    api.set_budget(project, spent / 2, [0.8], operator="gov-ops")
    assert api.governance("budget.get", {"projectId": project})["state"] == "EXCEEDED"
    refused = api.start_agent_run(
        request_for(
            project, AgentType.REQUIREMENTS, draft_id=draft_id, idempotency_key="gov:refused"
        )
    )
    assert refused.status == "BUDGET_EXCEEDED"
    assert refused.budget_status["state"] == "EXCEEDED"

    # ``limit=None`` is a statement that there is no cap, not a deleted row: the currency and
    # the person who decided stay, and ``UNSET`` would hide that anyone chose
    unlimited = api.set_budget(project, None, [], operator="gov-ops")
    assert (unlimited.state, unlimited.limit, unlimited.currency) == ("OK", None, "USD")


def test_a_budget_that_cannot_be_compared_with_cost_is_refused(
    harness: IntelligenceHarness,
) -> None:
    """A half-obeyed limit is worse than none, so the shape is checked before the row exists.

    A step at 1.0 can never warn before ``EXCEEDED`` and a step at 0.0 warns on the first call,
    so both are silently useless; a currency other than the one ``ai.model`` prices are stated
    in would make ``spent > limit`` compare two monies, and prices carry no currency column to
    reconcile against.
    """
    _ = harness
    project = unique_project_id("gov")
    base = {"projectId": project, "limit": 5.0, "thresholds": [0.8], "operator": "ops"}
    for change, why in (
        ({"limit": -1.0}, "a negative limit"),
        ({"limit": float("nan")}, "a limit that is not a number"),
        ({"thresholds": [1.0]}, "a threshold that can never warn"),
        ({"thresholds": [0.0]}, "a threshold that always warns"),
        ({"currency": "Dollars"}, "a currency that is not three letters"),
    ):
        with pytest.raises(BudgetInvalid) as raised:
            api.governance("budget.set", {**base, **change})
        assert raised.value.error_code == "AI_BUDGET_INVALID", why
    assert api.governance("budget.get", {"projectId": project})["state"] == "UNSET"


def test_usage_rollup_answers_the_grouping_it_was_asked_for(
    harness: IntelligenceHarness,
) -> None:
    """AI-COST-001 over CP-API-007: the answer restates the grouping, publishes the accepted
    vocabulary, and reports the ledger's own rows."""
    _ = harness
    project = unique_project_id("gov")

    answer = api.governance("usage.rollup", {"projectId": project})

    assert answer["groupBy"] == ["stage", "agent", "model"], "the default is the SRS's own triple"
    assert answer["dimensions"] == list(ROLLUP_DIMENSIONS)
    assert answer["items"] == [], "a project that never called a model has no groups"

    narrowed = api.governance("usage.rollup", {"projectId": project, "groupBy": ["model"]})
    assert narrowed["groupBy"] == ["model"]

    with pytest.raises(FactoryError) as refused:
        api.governance("usage.rollup", {"projectId": project, "groupBy": ["nonexistent"]})
    assert "nonexistent" in str(refused.value)
    assert "provider" in str(refused.value), "the refusal states the vocabulary it wanted"
    # AI-GOV-005: a classified refusal, never an unclassified exception S1 would render 500
    assert refused.value.code == "VALIDATION_ERROR"
    assert refused.value.details["allowed"] == list(ROLLUP_DIMENSIONS)


# --- evaluation (AI-EVAL-001/005, AI-REG-005) ------------------------------------


def test_eval_list_and_eval_get_answer_the_history(harness: IntelligenceHarness) -> None:
    """AI-REG-005: the registry reports a model's current standing while the full history stays
    queryable here — and a run that did not pass is listed too, because the *absence* of a
    passing evaluation is the fact AI-REG-003 gates on."""
    _ = harness
    passed = _seed_eval_run(score=0.97, passed=True)
    failed = _seed_eval_run(score=0.40, passed=False)

    items = api.governance("eval.list", {})["items"]
    assert {item["id"] for item in items} == {passed, failed}
    assert sorted(items[0]) == sorted(
        [
            "id",
            "agentType",
            "prompt",
            "model",
            "evalSetVersion",
            "score",
            "passed",
            "caseCount",
            "createdAt",
            "summary",
        ]
    )
    assert {item["prompt"] for item in items} == {f"{CONSOLIDATE}.v1"}
    assert {item["passed"] for item in items} == {True, False}

    assert api.governance("eval.list", {"agentType": AgentType.MAPPING.value})["items"] == []
    assert len(api.governance("eval.list", {"limit": 1})["items"]) == 1

    one = api.governance("eval.get", {"id": passed})
    assert one["id"] == passed
    assert one["passed"] is True
    assert one["caseCount"] == 12
    assert one["score"] == pytest.approx(0.97)
    assert one["summary"] == {"cases": 12, "matched": 12}
    # AI-GOV-004: the rows behind the score, and how the injection samples fared
    assert {"cases", "injectionCases", "injectionPassed"} <= set(one)

    with pytest.raises(NotFoundError):
        api.governance("eval.get", {"id": "aeval-nope"})


def test_eval_run_is_refused_with_a_pointer_to_the_function_that_may_do_it(
    harness: IntelligenceHarness,
) -> None:
    """Why this refuses: ``governance`` is not marked external I/O because S1 may call it with
    its own transaction open, so an action that would have to call a model cannot be served here
    at all (00 doc 6.2) — and opening a nested transaction instead would be indistinguishable
    from success."""
    _ = harness
    with pytest.raises(ValidationError) as raised:
        api.governance("eval.run", {"agentType": "REQUIREMENTS", "promptVersion": "x.v1"})

    assert raised.value.details == {"use": "run_evaluation"}
    assert "external I/O" in str(raised.value)
    assert "run_evaluation" in str(raised.value)
    assert is_external_io(api.run_evaluation), "the public function is the one that may"
    assert _count(EvalRunRow) == 0, "a refused evaluation evaluated nothing"


def test_similar_search_is_refused_because_it_would_read_an_artifact(
    harness: IntelligenceHarness,
) -> None:
    """AI-AG-011's search reads a DISCOVERY_RESULT artifact — S4's content, so external I/O —
    and runs inside a Mapping Agent run instead, which *is* marked."""
    _ = harness
    with pytest.raises(ValidationError) as raised:
        api.governance("similar.search", {"projectId": "prj-1", "object": {"name": "weight"}})

    assert raised.value.details == {"use": "start_agent_run with agentType=MAPPING"}
    assert "artifact" in str(raised.value)
    assert is_external_io(api.start_agent_run)
    assert not is_external_io(api.governance)


# --- agents (SRS 3.3, SRS 12) ----------------------------------------------------


def test_agent_list_answers_which_agents_may_run_here(
    harness: IntelligenceHarness, monkeypatch: pytest.MonkeyPatch
) -> None:
    """What S5 needs in order to offer manual authoring instead: the eight agents, their
    milestone, and whether this build serves them (SRS 12)."""
    _ = harness
    answer = api.governance("agent.list", {})

    assert answer["milestone"] == CURRENT_MILESTONE.value
    assert {item["agentType"] for item in answer["items"]} == {member.value for member in AgentType}
    assert sorted(answer["items"][0]) == sorted(
        [
            "agentType",
            "stage",
            "milestone",
            "enabled",
            "promptName",
            "activePrompt",
            "perPage",
            "patchMode",
        ]
    )
    served = {item["agentType"]: item for item in answer["items"]}
    assert served[AgentType.REQUIREMENTS.value]["enabled"] is True
    # UI, API and Fixture are scheduled for M3 and served by this build: ``milestone`` is SRS 12's
    # schedule and ``enabled`` is what feature.py delivers — the split REVIEW has always had
    assert served[AgentType.UI.value]["enabled"] is True
    assert served[AgentType.UI.value]["milestone"] == "M3"
    assert served[AgentType.API.value]["enabled"] is True
    assert served[AgentType.FIXTURE.value]["enabled"] is True
    assert served[AgentType.MAPPING.value]["patchMode"] == "PATCH"

    with monkeypatch.context() as patch:
        # an early opt-in only means something for an agent nothing else has enabled, so the
        # named list is withheld for the duration of this half of the test
        patch.setattr("factory_intelligence.feature.DELIVERED_AGENTS", frozenset())
        gated = {
            item["agentType"]: item for item in api.governance("agent.list", {})["items"]
        }
        assert gated[AgentType.UI.value]["enabled"] is False, "an unserved M3 agent reads as off"
        assert gated[AgentType.REQUIREMENTS.value]["enabled"] is True, "M2 is delivered by the rank"

        api.configure(enabled_agents=(AgentType.UI.value,))
        opted_in = {item["agentType"]: item for item in api.governance("agent.list", {})["items"]}
        assert opted_in[AgentType.UI.value]["enabled"] is True, "a deployment may opt in early"


# --- the transactional rule (00 doc 6.2) -----------------------------------------


def test_governance_is_not_marked_external_io(harness: IntelligenceHarness) -> None:
    """The rule that decides the shape of this whole surface: S1 may call ``governance`` with
    its own transaction open, so it must not carry S0's external-I/O guard — while the four
    names S1 does mark are exactly the ones that need a model or an artifact."""
    _ = harness
    assert "governance" not in S1_EXTERNAL_IO_METHODS
    assert not is_external_io(api.governance)
    for name in sorted(S1_EXTERNAL_IO_METHODS):
        assert is_external_io(getattr(api, name)), f"api.{name} must stay marked"

    with unit_of_work():
        assert api.governance("agent.list", {})["items"], "refused for having a transaction open"
    assert not has_active_uow()


@pytest.mark.parametrize(
    ("action", "payload", "witness"),
    [
        (
            "provider.register",
            {"name": "gov", "providerClass": "LOCAL_ONLY"},
            lambda: "gov" in _provider_names(),
        ),
        (
            "regression.record",
            {"promptName": CONSOLIDATE, "promptVersion": 1},
            lambda: _count(RegressionRefRow) == 1,
        ),
        (
            "policy.org.relax",
            RELAX_INTERNAL,
            lambda: _count(PolicyRelaxationRow) == 1,
        ),
    ],
    ids=["provider-register", "regression-record", "policy-relax"],
)
def test_a_state_changing_action_with_no_transaction_opens_and_commits_its_own(
    harness: IntelligenceHarness,
    action: str,
    payload: dict[str, Any],
    witness: Callable[[], bool],
) -> None:
    """S1 calls the adapter with no transaction for us to manage, so the action has to commit
    what it wrote *and* leave nothing open — a leaked transaction would hold the caller's next
    statement behind it (00 doc 6.2)."""
    _ = harness
    assert not has_active_uow()

    api.governance(action, payload)

    assert not has_active_uow()
    assert witness(), "the write did not survive the call that made it"


def test_a_state_changing_action_written_into_a_session_is_visible_to_its_caller(
    harness: IntelligenceHarness,
) -> None:
    """The other half of the same rule: given the caller's session, the action writes there, so
    the caller reads its own write — and it is the caller's commit, not S3's, that decides
    whether the registration exists."""
    _ = harness
    with unit_of_work() as session:
        answer = dispatch(
            "provider.register", {"name": "gov", "providerClass": "LOCAL_ONLY"}, session=session
        )
        assert session.get(ProviderRow, answer["id"]) is not None, "written somewhere else"
        assert "gov" in _provider_names(), "a read in the same transaction missed the write"
        assert has_active_uow(), "the read replaced the caller's transaction with its own"

    assert [item["id"] for item in api.governance("provider.list", {})["items"]] == [answer["id"]]


def test_a_state_changing_action_inside_a_callers_transaction_rolls_back_with_it(
    harness: IntelligenceHarness,
) -> None:
    """S1's transaction is the unit of work: an action called while it is open must enlist in it
    rather than open a nested one and commit early, or a registration would outlive the request
    that asked for it (00 doc 6.2, D-11 for the audit row)."""
    _ = harness
    with pytest.raises(_Rollback), unit_of_work():
        api.governance(
            "provider.register",
            {"name": "gov", "providerClass": "LOCAL_ONLY", "operator": "enlist-ops"},
        )
        assert has_active_uow(), "the action ended the caller's transaction"
        raise _Rollback("the caller's request failed for an unrelated reason")

    assert not has_active_uow()
    assert _provider_names() == []
    assert _count(ProviderRow) == 0
    assert _events("ai.model.registered", "enlist-ops") == [], "the audit row survived its own rollback"


@pytest.mark.parametrize(
    "action",
    [
        "provider.list",
        "model.list",
        "prompt.list",
        "prompt.get",
        "policy.org.get",
        "budget.get",
        "usage.rollup",
        "eval.list",
        "eval.get",
        "agent.list",
    ],
    ids=[
        "provider-list",
        "model-list",
        "prompt-list",
        "prompt-get",
        "policy-get",
        "budget-get",
        "usage-rollup",
        "eval-list",
        "eval-get",
        "agent-list",
    ],
)
def test_a_read_only_action_leaves_no_transaction_open(
    harness: IntelligenceHarness, action: str
) -> None:
    """Reads are half of this surface, and each one answers through a transaction it owns and
    closes (00 doc 6.2)."""
    _ = harness
    answer = api.governance(action, _read_payload(action))

    assert isinstance(answer, dict)
    assert not has_active_uow()


def test_a_read_only_action_does_not_commit_the_callers_transaction(
    harness: IntelligenceHarness,
) -> None:
    """A read that committed would end the caller's transaction early and make its uncommitted
    writes durable — the failure the enlistment rule exists to prevent, and invisible unless
    somebody looks for the row that should not have survived."""
    _ = harness
    with pytest.raises(_Rollback), unit_of_work() as session:
        session.add(
            OrgPolicyRow(id="org", allowed_classes_by_level={}, updated_by="caller-probe")
        )
        # the probe row is the caller's own write, uncommitted on purpose: an action that
        # committed the transaction it was called in would leave it behind after the rollback
        api.governance("policy.org.get", {})
        api.governance("eval.list", {})
        raise _Rollback("the caller decides when work becomes durable")

    assert _count(OrgPolicyRow) == 0


def _read_payload(action: str) -> dict[str, Any]:
    """The smallest payload that reaches each read-only action."""
    if action == "prompt.get":
        return {"name": CONSOLIDATE, "version": 1}
    if action == "eval.get":
        return {"id": _seed_eval_run()}
    if action in {"usage.rollup", "budget.get"}:
        return {"projectId": "prj-governance-read"}
    if action == "model.list":
        return {"includeDisabled": True}
    return {}


# --- the payload itself ----------------------------------------------------------


def test_a_payload_is_copied_before_the_handlers_see_it(
    harness: IntelligenceHarness,
) -> None:
    """S1 hands over a dict it built from an HTTP body and may well re-send on a retry; an
    action that wrote back into it would change the request the caller thinks it is
    repeating (00 doc 6.4)."""
    _ = harness
    payload: dict[str, Any] = {"name": "gov", "providerClass": "LOCAL_ONLY"}
    before = json.dumps(payload, sort_keys=True)

    api.governance("provider.register", payload)

    assert json.dumps(payload, sort_keys=True) == before
    assert payload == {"name": "gov", "providerClass": "LOCAL_ONLY"}


# --- AI-GOV-009: the façade is a deprecated, thin delegate over the typed functions ---------


def test_the_facade_delegates_every_action_to_a_typed_public_function() -> None:
    """No action may live only in the façade: each names the typed function that also serves it.

    This is what turns "remove the façade" into deleting a table instead of rediscovering which
    actions had no typed exit at all -- the defect class AI-GOV-001 was written against.
    """
    import factory_intelligence.api as api
    from factory_intelligence.governance import dispatcher

    unmapped = sorted(
        action for action in dispatcher.ACTIONS if dispatcher.ACTION_CAPABILITIES.get(action) is None
    )
    # an action with no typed exit is allowed only as a refusal, never as silent logic
    for action in unmapped:
        from factory_foundation.api import FactoryError

        try:
            dispatcher.dispatch(action, {})
        except FactoryError:
            continue
        raise AssertionError(f"{action} has no typed function and does not refuse either")

    def _unmapped(action: str) -> bool:
        # The dict's value is `str | None`; a truthy check outside the expression cannot narrow it
        # for `hasattr`, so the one capability this loop asks about is named once.
        capability = dispatcher.ACTION_CAPABILITIES.get(action)
        if not capability:
            return False
        return not hasattr(api, capability)

    missing = sorted(action for action in dispatcher.ACTIONS if _unmapped(action))
    assert not missing, f"facade actions with no typed public function behind them: {missing}"
    assert "similar.search" in unmapped, "the refusal action has to stay declared as such"


def test_the_facade_is_marked_deprecated_with_a_removal_milestone() -> None:
    """PEP 702 marker, not only a docstring sentence: a caller's tooling has to see it."""
    import factory_intelligence.api as api

    assert getattr(api.governance, "__deprecated__", ""), "the façade is not marked deprecated"
    assert api.REMOVE_AT_MILESTONE == "M2"


def test_the_facade_grows_no_action_without_its_tables_being_updated() -> None:
    from factory_intelligence.governance import dispatcher

    assert sorted(dispatcher.ACTIONS) == sorted(dispatcher.ACTION_CAPABILITIES)
    assert sorted(dispatcher.ACTIONS) == sorted(dispatcher.FACADE_RESULT_MODELS)


def test_the_facade_answers_with_the_projection_of_a_declared_model(harness: Any) -> None:
    """The list actions used to hand-build ``{"items": [...]}``; now the envelope is a model.

    Asserting the façade's answer against the typed function's own models is the check that
    there is no second implementation to keep in step (AI-GOV-009), and it is the shape S1's
    typed REST responses are declared against.
    """
    _ = harness
    from factory_intelligence import api
    from factory_intelligence.dto.governance import (
        GovernanceModel,
        ModelList,
        PromptList,
        PromptReloadResult,
        ProviderList,
        wire,
    )

    cases: list[tuple[str, Any, type[GovernanceModel]]] = [
        ("provider.list", api.list_providers(), ProviderList),
        ("model.list", api.list_models(), ModelList),
        ("prompt.list", api.list_prompts(), PromptList),
    ]
    for action, rows, envelope in cases:
        answer = api.governance(action, {})
        assert answer == wire(envelope(items=list(rows))), action
        assert set(answer) == {"items"}, f"{action} changed its declared envelope"

    # ``prompt.source.reload`` answered with a dict assembled on the spot until AI-GOV-009
    reload_answer = api.governance("prompt.source.reload", {})
    assert reload_answer == wire(api.reload_prompt_sources())
    assert set(reload_answer) == set(wire(PromptReloadResult())), "reload changed its shape"


def test_only_a_declared_refusal_may_answer_with_no_model_at_all() -> None:
    """``None`` in the result table means "this action refuses", and nothing else (AI-GOV-009).

    Without this, an action could keep returning a hand-built dict while its table entry sat at
    ``None`` -- the shape ``prompt.source.reload`` had, which is exactly what the rule forbids.
    A refusal is also required to point somewhere, because an action that only says "no" leaves
    the caller guessing which typed function to call instead.
    """
    from factory_foundation.api import FactoryError

    from factory_intelligence.governance import dispatcher

    undeclared = sorted(
        action
        for action, model in dispatcher.FACADE_RESULT_MODELS.items()
        if model is None and action not in dispatcher.FACADE_REFUSALS
    )
    assert not undeclared, f"actions answering with an undeclared result: {undeclared}"
    assert set(dispatcher.FACADE_REFUSALS) <= set(dispatcher.ACTIONS)

    for action, why in dispatcher.FACADE_REFUSALS.items():
        try:
            dispatcher.dispatch(action, {})
        except FactoryError as error:
            assert {"capability", "use"} & set(error.details or {}), (
                f"{action} refuses without naming the alternative: {error.details}"
            )
            continue
        raise AssertionError(f"{action} is declared a refusal ({why}) but answered anyway")
