#!/usr/bin/env python
"""Activate S3's shipped prompts on a running ``factory-api`` through the public REST surface.

    python scripts/activate_prompts.py --base-url http://127.0.0.1:8201 \
        --provider stub --model stub/stub-model

A freshly deployed real-S3 backend registers its prompt *versions* at start-up (AI-REG-002) but
activates none of them: switching a version into effect is a separately gated step (AI-REG-003,
AI-GOV-003), and until it happens every agent run falls back to an empty placeholder prompt, so
the model receives runtime scaffolding but none of the authored instructions and prompt
governance is inert.  This script closes that gap the only honest way — through the same three
governance endpoints an operator would use — for every agent that ships a prompt:

1. ``POST /governance/evaluations`` runs ``run_evaluation`` for the version against its packaged
   set on the given model (AI-EVAL-001); the model must answer each case as the set expects for
   the run to pass the 0.95 bar (``scripts/llm_stub.py`` does exactly that for the loopback
   stand-in).  A real endpoint would have to actually be good enough.
2. ``POST /governance/regression-records`` records the test group's Golden Project pass
   (AI-REG-003).  This is the claim activation refuses to invent for itself; standing up a
   development/demo backend is a legitimate place to assert it, so this script asserts it on the
   operator's behalf rather than relaxing the gate.
3. ``POST /governance/prompts/activations`` switches the version in, and only opens once both
   pieces of evidence are on file (AI-GOV-003).

This is development/demo tooling, not a product path: it is a script and never part of S1's
request handling.  It is idempotent and re-runnable — a version already active, an evaluation
already passed, or a regression already recorded are each detected and skipped, so a seed that
runs it after every ``register a model`` converges instead of erroring.

Note the asymmetry the gate encodes (prompts/activation.py): the activation ``modelList`` is
compared against ``EvalRunRow.model``, which is the *bare* model name, while the candidate a run
walks comes from the registry's ``applicableAgents``.  So a model registered here must already be
enabled and applicable to each agent it activates, and the modelList we send is the bare name.
"""

from __future__ import annotations

import argparse
import sys
import time
from typing import Any

import httpx


class ActivationFailed(Exception):
    """A step could not be completed; the reason is in the message so a seed can surface it."""


#: set by main from --token; empty means the deployment answers without a session
_BEARER_TOKEN = ""


def _headers(operator: str, idempotency_key: str | None = None) -> dict[str, str]:
    headers = {"X-Operator": operator}
    if _BEARER_TOKEN:
        # An auth-enforced deployment answers 401 to X-Operator alone; the session token the
        # seed already holds is what makes these writes land.
        headers["Authorization"] = f"Bearer {_BEARER_TOKEN}"
    if idempotency_key is not None:
        headers["Idempotency-Key"] = idempotency_key
    return headers


def _raise_for(client: httpx.Client, method: str, path: str, response: httpx.Response) -> Any:
    if response.status_code >= 400:
        raise ActivationFailed(f"{method} {path} -> {response.status_code}: {response.text[:400]}")
    return response.json()


def _wait_for_api(client: httpx.Client, timeout: float) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            if client.get("/healthz").status_code == 200:
                return
        except httpx.HTTPError:
            time.sleep(0.3)
    raise ActivationFailed(f"no healthy factory-api within {timeout}s")


def shipped_versions(client: httpx.Client, operator: str) -> dict[str, tuple[str, int]]:
    """One ``(prompt name, highest version)`` per agent that has a registered prompt file.

    Read from the registry rather than hardcoded: an agent whose prompt never shipped (API / UI /
    Fixture, delivered in M3) has no version to activate, and the honest answer for it is to skip
    it, not to fabricate a prompt to switch on (AI-REG-002).
    """
    body = _raise_for(client, "GET", "/api/v1/governance/prompts", client.get(
        "/api/v1/governance/prompts", headers=_headers(operator)
    ))
    chosen: dict[str, tuple[str, int]] = {}
    for row in body["items"]:
        agent = str(row["agentType"])
        name, version = str(row["name"]), int(row["version"])
        if agent not in chosen or version > chosen[agent][1]:
            chosen[agent] = (name, version)
    return chosen


def active_versions(client: httpx.Client, operator: str) -> dict[str, tuple[str, int]]:
    body = _raise_for(client, "GET", "/api/v1/governance/prompts/activations", client.get(
        "/api/v1/governance/prompts/activations", headers=_headers(operator)
    ))
    return {
        str(row["agentType"]): (str(row["name"]), int(row["version"]))
        for row in body["items"]
        if row.get("name") is not None
    }


def _evaluation_already_passed(
    client: httpx.Client, operator: str, agent: str, qualified: str, model: str
) -> bool:
    """A passing measurement of *this* version on *this* model is already on the record."""
    body = _raise_for(client, "GET", "/api/v1/governance/evaluations", client.get(
        "/api/v1/governance/evaluations", headers=_headers(operator), params={"agentType": agent}
    ))
    return any(
        row.get("prompt") == qualified and row.get("model") == model and row.get("passed") is True
        for row in body["items"]
    )


def run_evaluation(
    client: httpx.Client, operator: str, agent: str, qualified: str, model_ref: str
) -> tuple[float | None, bool]:
    """Trigger one evaluation and read its verdict back from the endpoint the acceptance names."""
    accepted = _raise_for(client, "POST", "/api/v1/governance/evaluations", client.post(
        "/api/v1/governance/evaluations",
        headers=_headers(operator, f"activate:eval:{agent}:{qualified}:{model_ref}"),
        json={"agentType": agent, "qualifiedPrompt": qualified, "model": model_ref},
    ))
    # ``run_evaluation`` is external I/O but served synchronously behind this route (the engine
    # hand-offs on the eval path are in-process), so the eval run id is already resolvable.
    eval_run_id = accepted["stepRunId"]
    deadline = time.monotonic() + 60
    last: dict[str, Any] | None = None
    while time.monotonic() < deadline:
        last = _raise_for(client, "GET", "/api/v1/governance/evaluations/{id}", client.get(
            f"/api/v1/governance/evaluations/{eval_run_id}", headers=_headers(operator)
        ))
        if last.get("passed") is not None:
            return last.get("score"), bool(last.get("passed"))
        time.sleep(0.5)
    raise ActivationFailed(f"evaluation {eval_run_id} for {qualified} never reported a verdict")


def _regression_already_present(
    client: httpx.Client, operator: str, name: str, version: int
) -> bool:
    body = _raise_for(client, "GET", "/api/v1/governance/regression-records", client.get(
        "/api/v1/governance/regression-records",
        headers=_headers(operator),
        params={"promptName": name, "promptVersion": version},
    ))
    return any(row.get("passed") is True for row in body["items"])


def record_regression(
    client: httpx.Client, operator: str, name: str, version: int, model: str
) -> None:
    _raise_for(client, "POST", "/api/v1/governance/regression-records", client.post(
        "/api/v1/governance/regression-records",
        headers=_headers(operator, f"activate:regression:{name}:v{version}"),
        json={
            "promptName": name,
            "promptVersion": version,
            "recordedBy": operator,
            "passed": True,
            "model": model,
            "source": "golden-project",
            "note": "activated by scripts/activate_prompts.py for a demo/dev backend",
        },
    ))


def activate(
    client: httpx.Client, operator: str, agent: str, name: str, version: int, model: str
) -> None:
    _raise_for(client, "POST", "/api/v1/governance/prompts/activations", client.post(
        "/api/v1/governance/prompts/activations",
        headers=_headers(operator),
        json={"agentType": agent, "promptName": name, "promptVersion": version, "modelList": [model]},
    ))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default="http://127.0.0.1:8201")
    parser.add_argument("--provider", required=True, help="registered provider name (for the model ref)")
    parser.add_argument("--model", required=True, help="model reference 'provider/name' or a bare name")
    parser.add_argument("--operator", default="prompt-activation")
    parser.add_argument("--token", default="", help="session token, for a deployment with auth enforced")
    parser.add_argument("--agents", default="", help="comma-separated agent types; default every agent with a shipped prompt")
    parser.add_argument("--wait", type=float, default=20.0)
    args = parser.parse_args(argv)

    global _BEARER_TOKEN  # noqa: PLW0603 - one script run, one session for every call it makes
    _BEARER_TOKEN = args.token.strip()

    provider, _, bare = args.model.rpartition("/")
    model_ref = args.model if bare else f"{args.provider}/{args.model}"
    model_name = model_ref.rpartition("/")[2]

    with httpx.Client(base_url=args.base_url, timeout=120) as client:
        _wait_for_api(client, args.wait)

        wanted = {a.strip().upper() for a in args.agents.split(",") if a.strip()}
        targets = shipped_versions(client, args.operator)
        if wanted:
            missing = wanted - set(targets)
            if missing:
                raise ActivationFailed(
                    f"{sorted(missing)} have no registered prompt to activate; "
                    "the API / UI / Fixture agents are delivered in M3 and ship no prompt"
                )
            targets = {a: v for a, v in targets.items() if a in wanted}
        currently = active_versions(client, args.operator)

        if not targets:
            print("no shipped prompts found; nothing to activate")
            return 0

        print(f"activating {len(targets)} agent prompt(s) on model {model_ref}")
        for agent, (name, version) in sorted(targets.items()):
            qualified = f"{name}.v{version}"
            if currently.get(agent) == (name, version):
                print(f"  {agent:15} {qualified:32} already active — skipped")
                continue

            if _evaluation_already_passed(client, args.operator, agent, qualified, model_name):
                print(f"  {agent:15} {qualified:32} evaluation already passed")
            else:
                score, passed = run_evaluation(client, args.operator, agent, qualified, model_ref)
                if not passed:
                    raise ActivationFailed(
                        f"{agent}: evaluation of {qualified} on {model_ref} scored {score} and did "
                        "not pass the bar — the model behind this registration is not answering "
                        "the eval set as expected (a real model must actually be good enough)"
                    )
                print(f"  {agent:15} {qualified:32} evaluation passed (score={score})")

            if _regression_already_present(client, args.operator, name, version):
                print(f"  {agent:15} {qualified:32} regression already recorded")
            else:
                record_regression(client, args.operator, name, version, model_name)
                print(f"  {agent:15} {qualified:32} regression recorded")

            activate(client, args.operator, agent, name, version, model_name)
            print(f"  {agent:15} {qualified:32} ACTIVATED")

        # Prove it landed: the readiness view is what a run will read next.
        final = active_versions(client, args.operator)
        print()
        print("active prompts now:")
        for agent in sorted(final):
            name, version = final[agent]
            print(f"  {agent:15} {name}.v{version}")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except ActivationFailed as failure:
        print(f"ACTIVATION STOPPED: {failure}", file=sys.stderr)
        raise SystemExit(1) from failure
