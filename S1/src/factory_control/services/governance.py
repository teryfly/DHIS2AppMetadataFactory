"""Governance, one port method per capability (S5IF-GOV01 … GOV19).

CP-UI-006 item 6 is why this module no longer forwards ``governance(action, payload)``:
S1 declares a named REST request and response component for every one of these
(CP-API-010, 00 doc 6.11), and a payload that has to be decoded from a free dictionary
first cannot become one.  S3 exports each capability as a typed function (AI-GOV-001),
the port mirrors that one-for-one, and what is left to do here is S1's own work:

* **the camelCase projection** — S3 answers with its models, the wire name is S1's to
  choose (00 doc 6.11), so every body leaves this module through :func:`_wire`;
* **the two fields S3's model view does not carry** — a model's provider address and Key
  *reference* come from the provider row (S5IF-GOV04, S5IF-G09: a reference, never a value);
* **the narrowing the provider cannot do** — a project's effective policy is the
  organisation's intersected with every target environment's (D-07).

S1 still stores nothing (CP-API-007): every answer here is S3's, projected.
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from pydantic import BaseModel

from factory_control import store as S
from factory_control.errors import NotFoundControlError, ProviderUnavailableControlError
from factory_control.runtime import Runtime

#: Strictest first.  A project's effective policy is the intersection of the
#: organisation's and every target environment's (D-07), so the tightest class wins.
POLICY_ORDER = ("LOCAL_ONLY", "PRIVATE_NETWORK", "APPROVED_EXTERNAL", "UNRESTRICTED")

#: The usage dimensions S1 answers for, in the order it declares them (AI-GOV-005).  S1
#: validates a requested dimension against this list before the call, so an unknown one is
#: a 422 from this route rather than a 500 out of S3 (CP-UI-006 item 4).
USAGE_DIMENSIONS = ("project", "workflow", "stage", "agent", "model", "provider")


def _row(value: Any) -> dict[str, Any]:
    """A port result, projected into the camelCase JSON the REST contract declares.

    ``mode="json"`` is what turns a datetime into the ISO string S5 sees and a str-enum into
    its value; ``by_alias=True`` is S1's naming, not S3's field names.  CP-UI-006 item 6 puts
    that projection on this side of the boundary: the response component is S1's.
    """
    if isinstance(value, BaseModel):
        return dict(value.model_dump(mode="json", by_alias=True))
    return dict(value or {})


def _rows(values: Iterable[Any]) -> list[dict[str, Any]]:
    return [_row(value) for value in values]


def _envelope(values: Iterable[Any]) -> dict[str, Any]:
    """The one list shape (S5IF-G04): ``items`` plus the ``total`` the envelope promises."""
    rows = _rows(values)
    return {"items": rows, "total": len(rows)}


# --- providers (S5IF-GOV01 … GOV03) ----------------------------------------


def providers(runtime: Runtime) -> dict[str, Any]:
    return _envelope(runtime.intelligence.list_providers())


def register_provider(runtime: Runtime, spec: dict[str, Any], *, operator: str) -> dict[str, Any]:
    """The Key arrives as a Secret reference; a literal value is rejected upstream (AC-13)."""
    body = dict(spec)
    # the operator is the caller, not a field of the provider being registered
    body.pop("operator", None)
    return _row(runtime.intelligence.register_provider(body, operator))


def check_provider(runtime: Runtime, name: str) -> dict[str, Any]:
    """S5IF-GOV20 — ask one provider's address whether it answers.

    S3's probe is a ``GET /models`` on a five-second budget: nothing is generated, so nothing is
    billable and no project content leaves. The point of exposing it is that a wrong endpoint, a
    wrong key and a model that simply takes longer than the request timeout are three different
    problems, and today an operator can only tell them apart by reading S3's error text off a failed
    parse — or by querying the model-call table, which no screen offers.
    """
    return _row(runtime.intelligence.check_provider_health(name))


def update_provider(
    runtime: Runtime, name: str, patch: dict[str, Any], *, operator: str
) -> dict[str, Any]:
    """Correct what the operator sent, leaving the columns they did not name alone.

    ``register_provider`` replaces the row, so routing an edit through it would clear the Key
    reference of anyone who only meant to fix the address (S5IF-GOV03).
    """
    # PatchProviderRequest.to_patch already dropped the nulls; filtering again here would hide a
    # contract bug behind a no-op instead of letting S3 refuse it
    body = dict(patch)
    body.pop("operator", None)
    return _row(runtime.intelligence.update_provider(name, body, operator))


# --- models (S5IF-GOV04 … GOV06) -------------------------------------------


def models(
    runtime: Runtime, *, agent_type: str | None = None, include_disabled: bool = True
) -> dict[str, Any]:
    """Model rows plus the two provider fields S3's view leaves to the reader (S5IF-GOV04)."""
    rows = runtime.intelligence.list_models(agent_type, include_disabled=include_disabled)
    by_name = {str(row.get("name")): row for row in _rows(runtime.intelligence.list_providers())}
    items = []
    for row in rows:
        record = _row(row)
        provider = by_name.get(str(row.provider)) or {}
        # A model registered without its own address reads it from the provider it belongs
        # to; Reference only — never the key itself (S5IF-G09).
        if record.get("endpoint") is None:
            record["endpoint"] = provider.get("endpoint")
        if record.get("apiKeySecretRef") is None:
            record["apiKeySecretRef"] = provider.get("apiKeySecretRef")
        items.append(record)
    return {"items": items, "total": len(items)}


def register_model(runtime: Runtime, spec: dict[str, Any], *, operator: str) -> dict[str, Any]:
    body = dict(spec)
    body.pop("operator", None)
    return _row(runtime.intelligence.register_model(body, operator))


def update_model(
    runtime: Runtime, ref: str, patch: dict[str, Any], *, operator: str
) -> dict[str, Any]:
    body = {key: value for key, value in dict(patch).items() if key != "operator"}
    return _row(runtime.intelligence.update_model(ref, body, operator))


# --- prompts, activations and regressions (S5IF-GOV07 … GOV12) --------------


def prompts(runtime: Runtime, agent_type: str | None) -> dict[str, Any]:
    return {"items": _rows(runtime.intelligence.list_prompts(agent_type))}


def prompt_detail(runtime: Runtime, name: str, version: int) -> dict[str, Any]:
    return _row(runtime.intelligence.get_prompt(name, version))


def activate_prompt(runtime: Runtime, payload: dict[str, Any]) -> dict[str, Any]:
    """AI-GOV-003: S3 refuses an activation that lacks a passing evaluation or regression."""
    return _row(
        runtime.intelligence.activate_prompt(
            str(payload["agentType"]),
            str(payload["promptName"]),
            int(payload["promptVersion"]),
            [str(item) for item in payload.get("modelList") or []],
            str(payload.get("operator") or "system"),
        )
    )


def activations(runtime: Runtime) -> dict[str, Any]:
    return _envelope(runtime.intelligence.list_prompt_activations())


def record_regression(runtime: Runtime, payload: dict[str, Any]) -> dict[str, Any]:
    return _row(
        runtime.intelligence.record_regression(
            str(payload["promptName"]),
            int(payload["promptVersion"]),
            bool(payload.get("passed", True)),
            payload.get("model"),
            str(payload.get("source") or "golden-project"),
            payload.get("note"),
            # S5 names this actor ``recordedBy`` (S5IF-GOV12); it is the operator, not a field
            # of the regression itself, so it leaves the payload and becomes the call's operator.
            str(payload.get("recordedBy") or payload.get("operator") or "system"),
        )
    )


def regression_records(
    runtime: Runtime,
    *,
    prompt_name: str | None = None,
    prompt_version: int | None = None,
) -> dict[str, Any]:
    """AI-REG-003's read side (S5IF-GOV11).

    ``activate_prompt`` refuses a prompt version with no regression result, and both consoles are
    asked to say *why* the switch is disabled. Only the writer existed, so the refusal could not be
    explained before the operator tried it — which is the same class of defect as a grey button
    with no reason.
    """
    return _envelope(
        runtime.intelligence.list_regressions(
            prompt_name=prompt_name or None,
            prompt_version=int(prompt_version) if prompt_version else None,
        )
    )


# --- policy (S5IF-GOV13 … GOV15, D-07) --------------------------------------


def organisation_policy(runtime: Runtime) -> dict[str, Any]:
    return _row(runtime.intelligence.get_org_policy())


def relax_policy(runtime: Runtime, payload: dict[str, Any]) -> dict[str, Any]:
    """A relaxation without a reason is a 422 before this call (CP-UI-006 item 3)."""
    return _row(
        runtime.intelligence.relax_org_policy(
            dict(payload.get("change") or {}),
            str(payload["reason"]),
            str(payload["operator"]),
            str(payload.get("scope") or "organization"),
        )
    )


def project_effective_policy(runtime: Runtime, project_id: str, *, operator: str) -> dict[str, Any]:
    """D-07: organisation policy narrowed by every target environment of the project."""
    organisation = organisation_policy(runtime)
    with runtime.transaction(operator=operator, project_id=project_id) as ctx:
        targets = S.target_environments(ctx.session, project_id)
        keys = [t.environment_key for t in targets]
    environment_policies: dict[str, str] = {}
    for key in keys:
        environment = runtime.execution.get_environment(key)
        if environment is None:
            raise NotFoundControlError(
                f"unknown environment {key} on project {project_id}",
                details={"environmentKey": key, "projectId": project_id},
            )
        environment_policies[key] = environment.llm_data_policy
    allowed = {
        level: list(classes) for level, classes in (organisation.get("effective") or {}).get("allowed", {}).items()
    }
    binding = _strictest_binding(allowed, environment_policies)
    narrowed = {level: _narrow(classes, binding) for level, classes in allowed.items()}
    return {
        "effective": {
            "allowed": narrowed,
            "sources": list((organisation.get("effective") or {}).get("sources") or []),
            "warnings": list((organisation.get("effective") or {}).get("warnings") or [])
            + ([f"narrowed by environment policy: {binding}"] if binding else []),
        },
        "isDefault": bool(organisation.get("isDefault", True)),
        "projectId": project_id,
        "environmentPolicies": environment_policies,
        "bindingEnvironment": binding,
    }


def _strictest_binding(
    allowed: dict[str, list[str]], environment_policies: dict[str, str]
) -> str | None:
    """The tightest class any environment permits, or ``None`` when nothing narrows."""
    candidates = [p for p in environment_policies.values() if p in POLICY_ORDER]
    if not candidates:
        return None
    tightest = min(POLICY_ORDER.index(p) for p in candidates)
    return POLICY_ORDER[tightest]


def _narrow(classes: list[str], binding: str | None) -> list[str]:
    if binding is None:
        return list(classes)
    floor = POLICY_ORDER.index(binding)
    kept = [c for c in classes if c in POLICY_ORDER and POLICY_ORDER.index(c) <= floor]
    return kept or list(classes)


# --- evaluations (S5IF-GOV16 … GOV17) ---------------------------------------


def evaluations(runtime: Runtime, agent_type: str | None, limit: int) -> dict[str, Any]:
    return {"items": _rows(runtime.intelligence.list_evaluations(agent_type, limit=limit))}


def evaluation_detail(runtime: Runtime, eval_run_id: str) -> dict[str, Any]:
    """AI-GOV-004: the per-case results and the injection outcome are in this body."""
    return _row(runtime.intelligence.get_evaluation(eval_run_id))


def run_evaluation(runtime: Runtime, payload: dict[str, Any], *, operator: str) -> dict[str, Any]:
    """GOV16 starts a run through ``run_evaluation``, which is external I/O (00 doc 6.2).

    CP-API-012 makes the acceptance name the evaluation run, because that id is where the
    result is watched from (``GET /governance/evaluations/{evalRunId}``); an answer without
    one is a run S3 did not start, and saying so beats a 202 that points nowhere.
    """
    started = runtime.intelligence.run_evaluation(
        str(payload["agentType"]),
        str(payload["qualifiedPrompt"]),
        str(payload["model"]),
        payload.get("evalSetVersion"),
        idempotency_key=f"eval:{payload['agentType']}:{payload['qualifiedPrompt']}:{operator}",
        operator=operator,
    )
    if not started.eval_run_id:
        raise ProviderUnavailableControlError(
            "S3 answered the evaluation request without an evaluation run id",
            details={"agentType": payload["agentType"], "qualifiedPrompt": payload["qualifiedPrompt"]},
        )
    return {
        "id": started.eval_run_id,
        "agentType": started.agent_type,
        "prompt": f"{started.prompt_name}.v{started.prompt_version}",
        "model": started.model,
        "evalSetVersion": started.eval_set_version,
        "score": started.score,
        "passed": started.passed,
        "caseCount": len(started.cases),
        "cases": _rows(started.cases),
        "injectionCases": started.injection_cases,
        "injectionPassed": started.injection_passed,
        "summary": None,
    }


# --- cost and the agent catalogue (S5IF-GOV18 … GOV19) ----------------------


def usage(runtime: Runtime, project_id: str | None, group_by: list[str]) -> dict[str, Any]:
    """The grouping is echoed back, so a cached page cannot be re-labelled as another one."""
    return {
        "groupBy": list(group_by),
        "dimensions": list(USAGE_DIMENSIONS),
        "items": _rows(runtime.intelligence.usage_rollup(project_id, group_by=tuple(group_by))),
    }


def budget(runtime: Runtime, project_id: str) -> dict[str, Any]:
    """AI-GOV-007: readable since M1; the write side arrived with M4."""
    row = _row(runtime.intelligence.get_budget(project_id))
    row["projectId"] = project_id
    return row


def set_budget(
    runtime: Runtime, project_id: str, payload: dict[str, Any], *, operator: str
) -> dict[str, Any]:
    """AI-COST-002's switch (S5IF-GOV18, UI-ADM-003): write the limit, echo the state.

    S1 authorises the action and passes it on.  Whether a run may start on the strength of the
    row is S3's judgement at run time (AI-COST-003), and a stage that is already running finishes
    rather than being cut off — so nothing here pretends to enforce the limit it just wrote.
    """
    row = _row(
        runtime.intelligence.set_budget(
            project_id,
            payload.get("limit"),
            [float(item) for item in (payload.get("thresholds") or [])],
            currency=str(payload.get("currency") or "USD"),
            operator=operator,
        )
    )
    row["projectId"] = project_id
    return row


def agents(runtime: Runtime) -> dict[str, Any]:
    """AI-GOV-008: the catalogue that lets the UI align a stage with its agent type."""
    return _row(runtime.intelligence.list_agents())


__all__ = [
    "POLICY_ORDER",
    "USAGE_DIMENSIONS",
    "activate_prompt",
    "activations",
    "agents",
    "budget",
    "check_provider",
    "evaluation_detail",
    "evaluations",
    "models",
    "organisation_policy",
    "project_effective_policy",
    "prompt_detail",
    "prompts",
    "providers",
    "record_regression",
    "set_budget",
    "register_model",
    "register_provider",
    "relax_policy",
    "run_evaluation",
    "usage",
]
