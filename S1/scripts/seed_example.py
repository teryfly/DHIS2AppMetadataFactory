#!/usr/bin/env python
"""Seed one complete mode-B project end to end against a real-provider ``factory-api``.

    bash S1/scripts/restart_spike.sh damf_spike                     # scratch backend, :8201
    cd S1 && .venv/bin/python scripts/llm_stub.py --port 8299 --log /tmp/llm-requests.jsonl &
    DHIS2_CRED=admin:district .venv/bin/python scripts/seed_example.py \
        --run-tag DEMO1 --dhis2-credential-env DHIS2_CRED

Everything on the other side of the REST surface is real: S2 builds the Blueprint, S3 runs the
agents against the model registration (which points at the loopback stand-in — a stand-in is a
stand-in, and this script says so in its summary), S4 speaks to the live DHIS2 demo instance.
Nothing here is product code and nothing here is a fake provider: the script is only a caller.

The chain it walks, in order, with one checkpoint per section:

  A  project (mode B) + parsed input + REQUIREMENTS/DOMAIN/DISCOVERY/MAPPING/PAGE_DESIGN
     PASSED with their Gates approved + BASELINE_COMPILE PASSED
  B  a Deployment Run on the TEST environment driven to VERIFIED — this COMMITs to DHIS2
  C  HANDOFF to HANDED_OFF, and a non-empty frontend package
  D  deletion of everything it committed, through S4's deletion door

Every step is idempotent-or-fresh: ``--run-tag`` names the DHIS2 code prefix (``SB_<tag>_``) and
the secret slug, so two runs do not collide, and re-running with the same tag resumes on an
existing project instead of duplicating it.  Failure is loud on purpose: the first non-2xx prints
method, path, status and body and stops, because that error list is worth more than a green run.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
from collections.abc import Callable
from typing import Any

import httpx

#: the official DHIS2 demo instance the user authorised writes to (FR-TST-002 rules apply)
DHIS2_DEMO = "https://play.im.dhis2.org/stable-2-41-10/api"

DEFAULT_MODEL = "stub-model"
DEFAULT_PROVIDER = "stub"

#: CP-GATE / S5IF: every Gate approval carries this operational confirmation (tests/helpers.py)
CONFIRMATIONS = [{"id": "OPERATIONAL_BACKUP_CONFIRMED", "confirmed": True}]

DESIGN_ORDER = ("REQUIREMENTS", "DOMAIN", "DISCOVERY", "MAPPING", "PAGE_DESIGN", "BASELINE_COMPILE")

#: Design-line stages with no agent and no human submission: they just run (附录 A 触发说明).
AUTO_DESIGN_STAGES = ("DISCOVERY", "BASELINE_COMPILE")

#: The one input document this seed writes.  Deliberately small and specific: every requirement
#: here is answerable by a metadata-only M2 package (fact 7), because a tracker/dataValue design
#: would compile into a package the executor refuses.
INPUT_STEM = "seed-requirements"
#: S3's parse verdicts that mean "there is an NRR to design against" (NEEDS_REVIEW is the one a
#: stand-in extraction with sub-threshold confidence gets, and the stage still has its input)
PARSED_STATUSES = ("COMPLETED", "NEEDS_REVIEW")
INPUT_MARKDOWN = f"""# Seed example: malaria case reporting, monthly summary

## Context
A district health team records malaria case counts per facility each month.
{DHIS2_DEMO} is the target instance for the test environment.

## Must have
1. A data element for "Confirmed malaria cases", integer value type, aggregated monthly.
2. A data element for "Suspected malaria cases", integer value type, aggregated monthly.
3. A constant holding the reporting month offset, used by the monthly summary.
4. A legend set that splits case fatality ratio into low / medium / high bands.
5. A monthly summary of confirmed cases per facility, exportable as CSV for district review.

## Constraints
- Only metadata is in scope for this release: no tracker programme, no data values, no app upload.
- Existing organisation units are reused, never created.
"""


class Bail(Exception):
    """The first step that did not work, with everything needed to diagnose it."""


def _short(value: Any, limit: int = 1200) -> str:
    text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False, default=str)
    return text if len(text) <= limit else text[:limit] + f"... [+{len(text) - limit} chars]"


class Api:
    """One HTTP client, one operator header, and exactly one way to fail."""

    def __init__(self, base_url: str, operator: str, timeout: float = 120.0) -> None:
        self._client = httpx.Client(base_url=base_url, timeout=timeout)
        self._operator = operator
        self._token: str | None = None

    @property
    def token(self) -> str:
        """The session token, empty when the deployment answers without one."""
        return self._token or ""

    def sign_in(self, subject: str, password: str) -> dict[str, Any]:
        """Exchange credentials for a session and keep its token.

        A deployed Factory runs ``FACTORY_AUTH_MODE=local``, and the session token is delivered
        only as an HttpOnly, Secure cookie — which no client will send over plaintext http.  The
        middleware reads the same value from ``Authorization: Bearer`` first, so the seed reads
        it off the login response's own ``Set-Cookie`` rather than asking for the cookie path
        (which would also demand the CSRF header).
        """
        session = self.call(
            "POST",
            "/api/v1/auth/login",
            label=f"sign in as {subject}",
            json_body={"subject": subject, "password": password},
            headers={"Content-Type": "application/json"},
        )
        # The sign-in sets two cookies: the HttpOnly session and a readable CSRF mirror
        # (`factory_csrf`).  Only the session value authenticates a bearer request, and taking
        # whichever cookie came first would send the CSRF mirror and fail with 401.
        for name, value in self._client.cookies.items():
            if value and name != "factory_csrf":
                self._token = value
                break
        if not self._token:
            raise Bail(f"login returned no session token: {_short(session)}")
        self._operator = subject
        return session

    def headers(self, idempotency_key: str | None = None) -> dict[str, str]:
        out = {"X-Operator": self._operator, "Content-Type": "application/json"}
        if self._token:
            out["Authorization"] = f"Bearer {self._token}"
        if idempotency_key:
            out["Idempotency-Key"] = idempotency_key
        return out

    def call(
        self,
        method: str,
        path: str,
        *,
        label: str = "",
        json_body: Any = None,
        headers: dict[str, str] | None = None,
        params: dict[str, Any] | None = None,
        files: Any = None,
        data: Any = None,
        accept: tuple[int, ...] = (200, 201, 202, 204),
        quiet: bool = False,
    ) -> Any:
        """One request, or a ``Bail`` carrying the status and the whole body."""
        if headers is None:
            headers = self.headers()
        if files is not None:
            headers.pop("Content-Type", None)  # multipart needs the transport's own boundary
        try:
            response = self._client.request(
                method, path, json=json_body, headers=headers, params=params, files=files, data=data
            )
        except httpx.HTTPError as error:
            raise Bail(f"{method} {path} -> transport failure: {type(error).__name__}: {error}") from error
        name = label or f"{method} {path}"
        if response.status_code not in accept:
            raise Bail(
                f"{name} [{method} {path}] FAILED\n"
                f"      status: {response.status_code}\n"
                f"      body:   {_short(response.text)}"
            )
        if not quiet:
            print(f"[ok] {name}: {response.status_code} {_short(response.text, 200)}")
        if not response.content:
            return None
        try:
            return response.json()
        except ValueError:
            return response.text

    # --- conveniences ---------------------------------------------------------------
    def get(self, path: str, **kwargs: Any) -> Any:
        return self.call("GET", path, **kwargs)

    def post(self, path: str, body: Any = None, **kwargs: Any) -> Any:
        kwargs.setdefault("json_body", body if body is not None else {})
        return self.call("POST", path, **kwargs)

    def put(self, path: str, body: Any, **kwargs: Any) -> Any:
        return self.call("PUT", path, json_body=body, **kwargs)

    def close(self) -> None:
        self._client.close()

    @property
    def base_url(self) -> str:
        return str(self._client.base_url)


def sign_in_as(base: Api, args: argparse.Namespace, subject: str, password_env: str) -> Api:
    """A separate session for a second duty, or the one client when the deployment needs none.

    An enforced deployment splits the Run across roles deliberately: `deployment.create`, the Run
    stages, PACKAGE_GATE and the COMMIT belong to DHIS2_IMPLEMENTER, while DEPLOY_GATE belongs to
    DEPLOYMENT_APPROVER and AC-GATE-04 refuses to let the person who executed the Run approve it.
    With ``FACTORY_AUTH_MODE=off`` one caller does everything, so an empty subject is not an error.
    """
    if not subject:
        return base
    password = (os.environ.get(password_env) or "").strip()
    if not password:
        raise Bail(f"{subject!r} needs its password in ${password_env}")
    client = Api(base.base_url, subject, timeout=args.timeout)
    session = client.sign_in(subject, password)
    print(f"  signed in as {subject} roles={session.get('roles')}")
    return client


def grant_project_role(api: Api, args: argparse.Namespace, project_id: str, subject: str, role: str) -> None:
    """Give ``subject`` ``role`` *inside* this project, so the grant is the one the policy reads.

    A platform-level grant leaves the caller a non-member (FR-SEC-004) and a project-scoped
    user_roles row used to be written where nothing looked for it; ``--run-subject`` and
    ``--approver-subject`` therefore have to be scoped here, not assumed from the deployment's
    bootstrap roles.
    """
    if not subject:
        return
    users = api.get("/api/v1/users", quiet=True).get("items", [])
    user_id = next((u["id"] for u in users if u.get("subject") == subject), None)
    if user_id is None:
        raise Bail(f"no account {subject!r} to grant {role} to — create it first")
    api.put(
        f"/api/v1/users/{user_id}/roles",
        {"role": role, "projectId": project_id},
        label=f"grant {role} to {subject} in {project_id}",
    )


def wait_for_api(api: Api, timeout: float) -> dict[str, Any]:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            health = api.get("/healthz", quiet=True, accept=(200,))
            if isinstance(health, dict):
                return health
        except Bail:
            time.sleep(0.5)
    raise Bail(f"no healthy factory-api within {timeout}s")


def wait_for_stage(api: Api, stage_id: str, *, timeout: float, what: str = "stage") -> dict[str, Any]:
    """Poll a stage out of RUNNING; returns the stage view, and says so if it never left."""
    deadline = time.monotonic() + timeout
    last: dict[str, Any] = {}
    while time.monotonic() < deadline:
        last = api.get(f"/api/v1/stages/{stage_id}", quiet=True)
        if last.get("status") not in ("RUNNING", None):
            return last
        time.sleep(1.0)
    raise Bail(f"{what} {stage_id} still RUNNING after {timeout}s: {_short(last)}")


def gate_items(api: Api, workflow_id: str) -> list[dict[str, Any]]:
    return api.get(f"/api/v1/workflows/{workflow_id}/gates", quiet=True)["items"]


def approve_gate(api: Api, workflow_id: str, *, expect_type: str | None = None) -> dict[str, Any]:
    """Approve the first WAITING Gate, dispositioning every warning (CP-GATE-008).

    Mirrors ``tests/helpers.py:approve_waiting_gate``: ``seenBoundHashes`` echoes ``boundObjects``
    verbatim, every warning gets a disposition, every confirmation item comes back confirmed.
    """
    waiting = [g for g in gate_items(api, workflow_id) if g["status"] == "WAITING"]
    if expect_type:
        waiting = [g for g in waiting if g["type"] == expect_type]
    if not waiting:
        raise Bail(f"no WAITING gate of type {expect_type} on workflow {workflow_id}: {_short(gate_items(api, workflow_id))}")
    gate = waiting[0]
    dispositions = [
        {"issueId": w.get("issue_id") or w.get("issueId"), "disposition": "ACCEPTED"}
        for w in gate.get("warnings", [])
    ]
    confirmations = [{"id": c["id"], "confirmed": True} for c in gate.get("confirmationItems", [])] + CONFIRMATIONS
    approved = api.post(
        f"/api/v1/gates/{gate['id']}/approve",
        {
            "gateType": gate["type"],
            "seenBoundHashes": gate["boundObjects"],
            "warningDispositions": dispositions,
            "confirmations": confirmations,
        },
        label=f"approve gate {gate['type']} ({gate['id']})",
    )
    return approved


# --- setup: providers, prompts, environments ----------------------------------------


def register_llm(api: Api, args: argparse.Namespace) -> None:
    """Register the model the agents will run on, the way an operator would (AI-GOV-001/002)."""
    slug = f"{args.secret_slug}-llm"
    api.put(f"/api/v1/secrets/{slug}", {"material": "stub-secret"}, quiet=True, accept=(200, 201, 409))
    api.call(
        "POST",
        "/api/v1/governance/providers",
        label="register provider",
        json_body={
            "name": args.provider,
            "providerClass": "LOCAL_ONLY",
            "endpoint": f"{args.llm_url}/v1",
            "apiKeySecretRef": f"db://{slug}",
            "adapter": "openai_compat",
            "enabled": True,
        },
        accept=(200, 201, 409),
        quiet=True,
    )
    api.call(
        "POST",
        "/api/v1/governance/models",
        label="register model",
        json_body={
            "provider": args.provider,
            "model": args.model,
            "endpoint": f"{args.llm_url}/v1",
            "apiKeySecretRef": f"db://{slug}",
            "capabilities": {"structuredOutput": True, "maxContextTokens": 8000, "supportsImages": False},
            "applicableAgents": {
                "REQUIREMENTS": 1, "DOMAIN": 1, "MAPPING": 1, "METADATA_DESIGN": 1,
                "API": 1, "UI": 1, "FIXTURE": 1, "REVIEW": 1,
            },
            "enabled": True,
        },
        accept=(200, 201, 409),
        quiet=True,
    )
    print(f"[ok] provider/model registered: {args.provider}/{args.model} -> {args.llm_url}/v1")


def activate_prompts(api: Api, args: argparse.Namespace) -> None:
    """Run the shipped activation tool: eval, regression record, switch on (AI-REG-002/003)."""
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    import activate_prompts as AP  # noqa: PLC0415 - same directory, script tooling

    model_ref = args.model if "/" in args.model else f"{args.provider}/{args.model}"
    try:
        AP.main([
            "--base-url", args.base_url,
            "--provider", args.provider,
            "--model", model_ref,
            "--operator", args.operator,
            "--token", api.token,
        ])
    except SystemExit as exit_code:
        if exit_code.code not in (0, None):
            raise Bail(f"prompt activation exited with code {exit_code.code}") from exit_code
    except AP.ActivationFailed as failure:
        raise Bail(f"prompt activation failed: {failure}") from failure


def register_environment(api: Api, args: argparse.Namespace, key: str, environment_class: str) -> None:
    """One DHIS2 target: secret, environment, connection test, capability probe (DX-ENV/DX-CAP)."""
    slug = f"{args.secret_slug}-{key}"
    credential = os.environ.get(args.dhis2_credential_env, "")
    if not credential:
        raise Bail(
            f"environment variable {args.dhis2_credential_env} is not set — the DHIS2 credential "
            "is never hardcoded here (S5IF-G09: only an indirection reaches the request body)"
        )
    api.put(f"/api/v1/secrets/{slug}", {"material": credential}, quiet=True, accept=(200, 201, 409))
    api.call(
        "POST",
        "/api/v1/environments",
        label=f"register environment {key}",
        json_body={
            "environmentKey": key,
            "name": f"seed_example {key} ({environment_class})",
            "baseUrl": args.dhis2_base_url,
            "environmentClass": environment_class,
            # a range ("2.40 - 2.41") crashes profile selection; one series does not
            "versionConstraint": "2.41.x",
            "authMode": "BASIC",
            "llmDataPolicy": "LOCAL_ONLY",
            "credentials": [
                {"scope": "discovery", "secretRef": f"db://{slug}", "label": "discovery"},
                {"scope": "import", "secretRef": f"db://{slug}", "label": "import"},
            ],
        },
        # re-running the seed must not die on an environment that already exists
        accept=(200, 201, 409),
    )
    tested = api.post(
        f"/api/v1/environments/{key}/test-connection",
        {"requiredScopes": ["discovery", "import"]},
        label=f"test-connection {key}",
        quiet=True,
    )
    print(f"  test-connection {key}: ok={tested.get('ok')} result={tested.get('result')} version={tested.get('version')}")
    if not tested.get("ok"):
        raise Bail(f"test-connection {key} did not pass: {_short(tested)}")
    probed = api.post(f"/api/v1/environments/{key}/probe-capabilities", None, label=f"probe-capabilities {key}", quiet=True)
    print(f"  probe-capabilities {key}: profile={_short(probed.get('profileName') or probed, 120)}")


# --- checkpoint A -------------------------------------------------------------------


def create_project(api: Api, args: argparse.Namespace) -> dict[str, Any]:
    """A mode-B project targeting the live test environment (CP-PRJ-001).

    `codePrefix` is what makes this safe to run against the shared demo instance: S2 derives
    every managed object's code from the project's generation policy and replaces any code an
    author supplied (BP-REG-009), so the prefix is the only run-scoped identity left on the
    instance for FR-TST-002 to find again and delete.
    """
    body = {
        "name": f"seed-example-{args.run_tag}",
        "ownerName": args.operator,
        "description": "seeded by scripts/seed_example.py (real S2/S3/S4 chain)",
        "deliveryMode": "B",
        "projectType": args.project_type,
        "versionConstraints": "2.41.x",
        "codePrefix": code_prefix(args),
        "targets": [
            {"environmentKey": args.test_env, "promotionOrder": 0, "isDesignReference": True},
        ],
    }
    project = api.call(
        "POST",
        "/api/v1/projects",
        label="create mode-B project",
        json_body=body,
        headers=api.headers(f"seed:{args.run_tag}:project"),
    )
    print(f"  project={project['id']} workflow={project['workflowId']} first stage={project['currentStageId']}")
    return project


def upload_and_parse(api: Api, args: argparse.Namespace, project_id: str) -> str:
    """CP-API-009 two-step upload, then parse — an unparsed input fails the PARSED_INPUT check."""
    # a FAILED parse is not retried by the backend: the step run replays its recorded verdict,
    # so a re-seed that fixed the model answer needs an input of its own
    existing = api.get(f"/api/v1/projects/{project_id}/inputs", quiet=True)
    for row in existing.get("items", []):
        if str(row.get("filename", "")).startswith(INPUT_STEM) and row.get("parseStatus") in PARSED_STATUSES:
            print(f"  input already parsed: {row['artifactId']}")
            return str(row["artifactId"])
    suffix = "" if not existing.get("items") else f"-{len(existing['items'])}"
    filename = f"{INPUT_STEM}{suffix}.md"

    uploaded = api.call(
        "POST",
        f"/api/v1/projects/{project_id}/inputs",
        label="upload input",
        files={"file": (filename, INPUT_MARKDOWN.encode(), "text/markdown")},
        data={"dataClassification": "INTERNAL"},
        # api.headers(), not a hand-built dict: `Api.call` drops the Content-Type a multipart
        # request must not declare, and a bare X-Operator is refused outright once the
        # deployment enforces accounts.
        headers=api.headers(),
        accept=(200, 201),
        quiet=True,
    )
    artifact_id = str(uploaded["artifactId"])
    print(f"  input artifact={artifact_id}")
    api.post(
        f"/api/v1/projects/{project_id}/inputs/{artifact_id}/parse",
        {},
        label="parse input",
        headers=api.headers(f"seed:{args.run_tag}:parse:{artifact_id}"),
    )
    deadline = time.monotonic() + args.timeout
    last: dict[str, Any] = {}
    while time.monotonic() < deadline:
        listed = api.get(f"/api/v1/projects/{project_id}/inputs", quiet=True)
        last = next((r for r in listed.get("items", []) if r.get("artifactId") == artifact_id), {})
        if last.get("parseStatus") in PARSED_STATUSES:
            print(
                f"  input parsed: status={last.get('parseStatus')} "
                f"nrr={last.get('nrrArtifactId')} usedModel={last.get('usedModel')}"
            )
            return artifact_id
        time.sleep(1.0)
    raise Bail(f"input {artifact_id} did not parse within {args.timeout}s; last view: {_short(last)}")


def section_schema_report(api: Api, project_id: str) -> dict[str, Any]:
    return api.get(f"/api/v1/projects/{project_id}/blueprint/section-schemas", quiet=True)


# --- the driver ---------------------------------------------------------------------


def stage_map(api: Api, workflow_id: str) -> dict[str, dict[str, Any]]:
    workflow = api.get(f"/api/v1/workflows/{workflow_id}", quiet=True)
    return {
        s["stageType"]: s
        for s in workflow["stages"]
        if s.get("deploymentRunId") is None
    }


def run_agent_stage(
    api: Api, args: argparse.Namespace, stage: dict[str, Any], *, endpoint: str = "run"
) -> dict[str, Any]:
    """One agent run on a design stage, and the wait for the engine to finish it."""
    api.post(
        f"/api/v1/stages/{stage['id']}/{endpoint}",
        None,
        label=f"agent {endpoint} {stage['stageType']}",
        headers=api.headers(f"seed:{args.run_tag}:{stage['stageType']}:{endpoint}:{stage.get('attempt', 1)}"),
    )
    final = wait_for_stage(api, stage["id"], timeout=args.timeout, what=stage["stageType"])
    print(f"  {stage['stageType']} -> {final.get('status')}")
    return final


def manual_patch(api: Api, args: argparse.Namespace, stage: dict[str, Any], operations: list[dict[str, Any]], rationale: str) -> Any:
    return api.post(
        f"/api/v1/stages/{stage['id']}/patches",
        {"patch": {"operations": operations, "rationale": rationale}},
        label=f"manual patch {stage['stageType']} ({len(operations)} op)",
    )


def _registry_entries(draft: dict[str, Any]) -> list[dict[str, Any]]:
    section = (draft.get("sections") or {}).get("registry") or {}
    entries = section.get("entries") or []
    return [e for e in entries if isinstance(e, dict)]


#: FR-TST-002: the run-scoped identity of everything this seed puts on a shared instance.
def code_prefix(args: argparse.Namespace) -> str:
    return f"SB_{args.run_tag.upper()}"


#: FR-TST-002: on the shared demo instance every object we create carries a run-scoped code
#: prefix, we reference existing objects rather than making new ones, and we delete ours at the end.
def _stamp_codes(value: Any, tag: str) -> Any:
    """Prefix every ``code`` in an authored object with ``SB_<tag>_``.

    Recursed, because a nested object (a legend inside a legend set) is still an object the
    instance will see, and one unprefixed code is one object that cannot be found again to
    be cleaned up.
    """
    if isinstance(value, dict):
        out: dict[str, Any] = {}
        for key, item in value.items():
            if key == "code" and isinstance(item, str) and not item.startswith(f"SB_{tag}_"):
                out[key] = f"SB_{tag}_{item}"
            else:
                out[key] = _stamp_codes(item, tag)
        return out
    if isinstance(value, list):
        return [_stamp_codes(item, tag) for item in value]
    return value


#: ---------------------------------------------------------------------------
#: Hand-authored design content.
#:
#: MAPPING and PAGE_DESIGN are authored here rather than answered by the stand-in model, for one
#: reason each: MAPPING has to carry the run-scoped code prefix FR-TST-002 demands of a shared
#: instance (the tag is a CLI argument, and a model that never sees it cannot invent it), and
#: PAGE_DESIGN has to reference the businessKeys MAPPING just created.  REQUIREMENTS and DOMAIN go
#: through the real agent path instead, with the stand-in's answers authored in scripts/llm_stub.py.
#:
#: Metadata only, by design: the M2 executor carries metadata and refuses tracker, dataValues and
#: app upload, so every object below is one it can actually commit.
#: ---------------------------------------------------------------------------

#: (businessKey, dhis2Type, code, DHIS2 field values) for each object this seed commits
def _metadata_objects(tag: str) -> list[dict[str, Any]]:
    upper = tag.upper()
    return [
        {
            "businessKey": f"dataelement.{upper.lower()}-confirmed-cases",
            "intentKey": "intent-monthly-ConfirmedCases",
            "operationKey": "listMonthlyConfirmedCases",
            "dhis2Type": "dataElement",
            "code": f"SB_{upper}_confirmed_cases",
            "name": f"SB {upper} Confirmed malaria cases",
            "fields": {
                "name": f"SB {upper} Confirmed malaria cases",
                "shortName": f"SB{upper}conf",
                "description": "Monthly confirmed malaria cases reported by a facility.",
                "valueType": "NUMBER",
                "aggregationType": "SUM",
                "domainType": "AGGREGATE",
                "zeroIsSignificant": False,
            },
        },
        {
            "businessKey": f"dataelement.{upper.lower()}-suspected-cases",
            "intentKey": "intent-monthly-SuspectedCases",
            "operationKey": "listMonthlySuspectedCases",
            "dhis2Type": "dataElement",
            "code": f"SB_{upper}_suspected_cases",
            "name": f"SB {upper} Suspected malaria cases",
            "fields": {
                "name": f"SB {upper} Suspected malaria cases",
                "shortName": f"SB{upper}susp",
                "description": "Monthly suspected malaria cases reported by a facility.",
                "valueType": "NUMBER",
                "aggregationType": "SUM",
                "domainType": "AGGREGATE",
                "zeroIsSignificant": False,
            },
        },
        # No `constant` type descriptor ships in S2's tier-1 catalog
        # (descriptors/descriptor_files/1/ holds dataElement, optionSet, organisationUnit, ...),
        # so a CREATE registry entry of that type compiles to nothing and BPV-ST-001 blocks the
        # baseline for it. The reporting-month offset stays a requirement the page applies in its
        # query parameters rather than a metadata object this release can deliver.
    ]


#: The single domain entity this seed models. Shared with :func:`_domain_ops` because BPV-BP-006
#: refuses a Blueprint where a domain element appears in `domain/entities` but has no mapping row and
#: is not marked unmapped — two copies of the key drift, and the failure surfaces as a BLOCKING
#: freeze problem several stages after the one that caused it.
_DOMAIN_ENTITY = "entity.malariaCaseReport"
#: The single actor, shared by DOMAIN (the actor) and MAPPING (the role it maps to).
_ACTOR = "actor.district-health-officer"


def _mapping_ops(tag: str) -> list[dict[str, Any]]:
    """One architecture decision, one mapped entry and one Registry entry per object.

    BPV-PCH-003 is the rule that ties them: a ``mapping/entries`` row naming a ``decisionId`` that
    is not in ``architecture/decisions`` is a BLOCKING refusal, so each object gets its own decision.
    """
    operations: list[dict[str, Any]] = [
        # accessModel is DOMAIN-stage-adjacent but only in MAPPING's draft scope, so the DHIS2
        # role the district health officer acts under is registered here.
        {
            "op": "add",
            "path": "/accessModel/roles/-",
            "value": {
                # FR-PG-001's check (BPV-XA-005) accepts either `actor.roleKeys` hitting a role key
                # or the actor's own key being one. The first is unreachable from here: `actors` is
                # a DOMAIN-scope section and `accessModel` is MAPPING's, so a roleKey written in
                # DOMAIN could not resolve (BPV-PCH-003) and one cannot be added to the actor from
                # MAPPING (BPV-PCH-001). Sharing the key is the route the validator itself offers.
                "businessKey": _ACTOR,
                "name": {"default": "District health officer"},
                "authorities": ["F_DATA_ELEMENT_ADD", "F_INDICATOR_ADD"],
                "source": {"note": f"hand-authored by seed_example.py for run {tag}"},
            },
        },
        # BPV-BP-006: this release is aggregated metadata, so the entity is deliberately not
        # an object of its own — its attributes become the two case-count data elements below.
        # Saying "not mapped" with the reason is the honest answer; omitting the row is a refusal.
        {
            "op": "add",
            "path": "/mapping/entries/-",
            "value": {
                "businessKey": _DOMAIN_ENTITY,
                "notMapped": True,
                "notMappedReason": "Modelled as the aggregate this release reports, not as a metadata "
                "object of its own: its attributes become the two case-count data elements.",
                "confidence": 0.95,
                "source": {"note": f"hand-authored by seed_example.py for run {tag}"},
            },
        }
    ]
    for index, obj in enumerate(_metadata_objects(tag), start=1):
        decision = f"MAP-{index:03d}"
        operations.append(
            {
                "op": "add",
                "path": "/architecture/decisions/-",
                "value": {
                    "decisionId": decision,
                    "title": f"{obj['name']}: created as {obj['dhis2Type']}",
                    "context": "The input states this release carries metadata only — no tracker, no data values.",
                    "decision": (
                        f"Create {obj['dhis2Type']} {obj['code']} rather than reuse or reference: "
                        "the demo instance has no equivalent object with this run's code prefix."
                    ),
                    "status": "ACCEPTED",
                    "alternatives": [
                        {
                            "option": "Reuse an existing object of the same type",
                            "reasonRejected": "No object on the target carries this run's code prefix.",
                        }
                    ],
                },
            }
        )
        operations.append(
            {
                "op": "add",
                "path": "/mapping/entries/-",
                "value": {
                    "businessKey": obj["businessKey"],
                    "dhis2Type": obj["dhis2Type"],
                    "notMapped": False,
                    "decisionId": decision,
                    "confidence": 0.9,
                    "fields": obj["fields"],
                    "source": {"note": f"hand-authored by seed_example.py for run {tag}"},
                },
            }
        )
        operations.append(
            {
                "op": "add",
                "path": "/registry/entries/-",
                "value": {
                    "businessKey": obj["businessKey"],
                    "dhis2Type": obj["dhis2Type"],
                    "code": obj["code"],
                    "disposition": "CREATE",
                    "status": "APPROVED",
                    "managed": True,
                    # BPV-DEC-003: a CREATE registry entry must carry the decision that chose CREATE
                    # as a top-level `decisionId`. Naming it only inside `source` reads as provenance
                    # and satisfies nothing — the completeness rule asks the entry, not its origin.
                    "decisionId": decision,
                    "controlledFields": ["name", "shortName", "valueType", "aggregationType"],
                    "notes": f"created by seed_example.py run {tag}; deleted again at cleanup",
                    "source": {"decisionId": decision, "note": f"hand-authored by seed_example.py for run {tag}"},
                },
            }
        )
    return operations


def _page_ops(tag: str) -> list[dict[str, Any]]:
    """One page over the two data elements (FR-PG-002).

    Its actor and use case are referenced by key but authored in DOMAIN: `actors` and `useCases`
    belong to that stage's draft scope.
    """
    objects = _metadata_objects(tag)
    registry_refs = [obj["businessKey"] for obj in objects if obj["dhis2Type"] == "dataElement"]
    source = {"note": f"hand-authored by seed_example.py for run {tag}"}
    return [
    ] + [
        # BPV-XA-002: a page that declares no API intent has an unspecified data need, so the
        # compile blocks. One intent per case-count data element, each bound to the registry entry
        # it reads (BPV-XA-004 checks that binding), and the page names them back.
        {
            "op": "add",
            "path": "/apiIntents/items/-",
            "value": {
                "intentKey": obj["intentKey"],
                "pageKey": f"malaria-summary-{tag.lower()}",
                "operationKey": obj["operationKey"],
                "purpose": f"Read {obj['name'].lower()} per facility for one reporting month.",
                # `resource` is a dotted logical path (`^[a-z][a-z0-9]*(\.[a-z][a-zA-Z0-9]*)+$`),
                # not a bare DHIS2 type, and `queryParams`/`fields` are objects keyed by name —
                # a list of parameter names is a shape error, not shorthand.
                "resource": f"dhis2.{obj['dhis2Type']}",
                "method": "GET",
                # `queryParams`/`pathParams` are an object per name whose value is a paramSource
                # (`{"from": ...}`), while `fields` is a plain array of field paths.
                "queryParams": {
                    "organisationUnit": {"from": "userInput", "required": True},
                    "startDate": {"from": "userInput", "required": True},
                    "endDate": {"from": "userInput", "required": False},
                },
                "fields": ["value", "period"],
                "registryRefs": [obj["businessKey"]],
                "permissions": ["role.districtHealthOfficer"],
                "source": source,
            },
        }
        for obj in objects
        if obj["dhis2Type"] == "dataElement"
    ] + [
        {
            "op": "add",
            "path": "/pages/items/-",
            "value": {
                "pageKey": f"malaria-summary-{tag.lower()}",
                "name": "Monthly malaria summary",
                "purpose": "Show confirmed and suspected malaria cases per facility for one month.",
                "routes": ["/malaria/summary"],
                "useCases": ["usecase.monthly-malaria-summary"],
                "actors": [_ACTOR],
                "apiIntentRefs": [obj["intentKey"] for obj in objects if obj["dhis2Type"] == "dataElement"],
                "registryRefs": registry_refs,
                "source": source,
            },
        },
    ]


def _input_requirements() -> list[tuple[int, str, str]]:
    """(line number, sentence, businessKey) for every requirement line of the seed input.

    Shared with :func:`_domain_ops`: BPV-XA-008 blocks a compile when a CONFIRMED requirement is
    covered by no use case, and the only way the two stages can agree on which requirements exist
    is one source of truth for their keys. Derived here rather than listed, so editing
    ``INPUT_MARKDOWN`` cannot leave a use case pointing at a key that no longer has an entry.
    """
    found: list[tuple[int, str, str]] = []
    used: set[str] = set()
    for number, raw in enumerate(INPUT_MARKDOWN.splitlines(), start=1):
        sentence = raw.strip()
        if sentence.startswith("#") or len(sentence) < 12 or len(sentence.split()) < 4:
            continue
        words = [w for w in re.split(r"[^A-Za-z0-9]+", sentence.lower()) if w and not w.isdigit()]
        slug = re.sub(r"[^a-z0-9-]", "", "-".join(words[:4]))[:60].strip("-") or f"line{number}"
        # S2 refuses a section whose entries share a businessKey (BPV-PCH-003), and four words is
        # not enough to distinguish two lines of a real requirements note. The line number breaks
        # the tie, and only when it has to.
        if slug in used:
            slug = f"{slug}-line{number}"
        used.add(slug)
        found.append((number, sentence.rstrip(".").rstrip() + ".", f"req.{slug}"))
    return found


def _requirement_ops(tag: str, artifact_id: str) -> list[dict[str, Any]]:
    """One requirement per stated line of the seed input, sourced to the artifact it came from."""
    return [
        {
            "op": "add",
            "path": "/requirements/items/-",
            "value": {
                "businessKey": key,
                "statement": sentence,
                "source": {"artifactId": artifact_id, "location": f"line:{number}"},
                "priority": "MUST",
                "status": "CONFIRMED",
                "confidence": 0.95,
            },
        }
        for number, sentence, key in _input_requirements()
    ]


def _domain_ops(tag: str, artifact_id: str) -> list[dict[str, Any]]:
    """The one entity the seed input describes, with the attributes it says are captured.

    Authored against S2's own domain section schema rather than from the prose: an attribute is
    ``{name, valueType, required, sensitivity}`` with ``name`` matching ``^[a-z][a-zA-Z0-9]*$``
    (it becomes a DHIS2 identifier later, so "Reporting month" is not a name), it carries **no**
    businessKey of its own, and ``description``/``displayName`` are localizedText objects — a bare
    string is a type error, not a shorter way to say the same thing.
    """
    source = {"artifactId": artifact_id, "location": "line:4"}
    return [
        {
            "op": "add",
            "path": "/domain/entities/-",
            "value": {
                "businessKey": _DOMAIN_ENTITY,
                "displayName": {"default": "Malaria case report"},
                "description": {"default": "One facility's monthly count of suspected and confirmed malaria cases."},
                "attributes": [
                    {
                        "name": "reportingMonth",
                        "label": {"default": "Reporting month"},
                        "valueType": "DATE",
                        "required": True,
                        "sensitivity": "INTERNAL",
                    },
                    {
                        "name": "confirmedCases",
                        "label": {"default": "Confirmed malaria cases"},
                        "valueType": "INTEGER_ZERO_OR_POSITIVE",
                        "required": True,
                        "sensitivity": "INTERNAL",
                    },
                    {
                        "name": "suspectedCases",
                        "label": {"default": "Suspected malaria cases"},
                        "valueType": "INTEGER_ZERO_OR_POSITIVE",
                        "required": True,
                        "sensitivity": "INTERNAL",
                    },
                    {
                        "name": "reportedAtFacility",
                        "label": {"default": "Reporting facility"},
                        "valueType": "TEXT",
                        "required": True,
                        "sensitivity": "PUBLIC",
                    },
                ],
                "source": source,
            },
        },
        # `actors` and `useCases` are DOMAIN-scope sections (impact/stage_map.py), so they are
        # authored here. They used to sit in :func:`_page_ops`, and BPV-PCH-001 refused the whole
        # PAGE_DESIGN patch for it: "submit this change in a draft opened for the owning stage".
        {
            "op": "add",
            "path": "/actors/items/-",
            "value": {
                "businessKey": _ACTOR,
                # `name` is localizedText, not a string, and `roleKeys` is checked against
                # `accessModel/roles` (BPV-PCH-003) — which is a MAPPING-scope section, so the
                # role cannot be registered from a DOMAIN draft. It is authored in
                # :func:`_mapping_ops` and left unreferenced here rather than naming a key that
                # does not exist yet at the point this patch is validated.
                "name": {"default": "District health officer"},
                "source": source,
            },
        },
        {
            "op": "add",
            "path": "/useCases/items/-",
            "value": {
                "businessKey": "usecase.monthly-malaria-summary",
                # useCases.name is a plain string; actors.name is localizedText. Same key,
                # two shapes, in two sibling containers of one stage.
                "name": "Review the monthly malaria summary",
                "actors": [_ACTOR],
                "outcome": "A confirmed/suspected table per facility, exportable for the district review.",
                # BPV-XA-008: a CONFIRMED requirement covered by no use case blocks the compile
                # ("no page, API or metadata object will deliver it"). This is the link.
                "requirements": [key for _number, _sentence, key in _input_requirements()],
                "source": source,
            },
        },
    ]


#: stage type -> the operations that author it.  Section containers and key fields are exactly
#: what ``GET /blueprint/section-schemas`` publishes.  The two that need the input artifact take
#: it from ``args``, because a ``source`` in the Blueprint must name a real artifact (FR-BP-002).
HAND_AUTHORED: dict[str, Callable[..., list[dict[str, Any]]]] = {
    "REQUIREMENTS": lambda tag, artifact_id: _requirement_ops(tag, artifact_id),
    "DOMAIN": lambda tag, artifact_id: _domain_ops(tag, artifact_id),
    "MAPPING": lambda tag, artifact_id: _mapping_ops(tag),
    "PAGE_DESIGN": lambda tag, artifact_id: _page_ops(tag),
}


def author_stage(api: Api, args: argparse.Namespace, project_id: str, stage_type: str, stage: dict[str, Any]) -> None:
    """Hand-author one design stage (fact 6): JSON-Patch ops against the section schema, then submit.

    The section paths and item keys are exactly what ``GET /blueprint/section-schemas`` publishes;
    the operations here are the same ``{"op":"add","path":"/<section>/items/-","value":{...}}`` an
    operator would type in the console.
    """
    builder = HAND_AUTHORED.get(stage_type)
    artifact_id = getattr(args, "input_artifact_id", "") or ""
    if builder is None:
        raise Bail(
            f"{stage_type} was asked to be hand-authored (--manual-stages) but this script has no "
            f"authored content for it; available: {sorted(HAND_AUTHORED)}"
        )
    tag = args.run_tag.upper()
    stamped = [
        {**op, "value": _stamp_codes(op.get("value"), tag)} if isinstance(op.get("value"), dict) else op
        for op in builder(tag, artifact_id)
    ]
    result = manual_patch(api, args, stage, stamped, f"hand-authored by seed_example.py for run {tag}")
    issues = result.get("issues") or []
    for row in issues:
        print(f"    {row.get('level')} {row.get('ruleId') or row.get('rule')}: {_short(row.get('description'), 220)}")
    blocking = [row for row in issues if row.get("level") == "BLOCKING"]
    if result.get("patchStatus") != "PROPOSED":
        raise Bail(
            f"{stage_type}: S2 did not accept the patch as a proposal "
            f"(patchStatus={result.get('patchStatus')!r}).\n"
            f"      operations: {_short(stamped, 900)}\n"
            f"      patch response: {_short(result, 2000)}"
        )
    digest = _draft_digest(api, project_id)
    print(f"    draft after {stage_type}: {_short(digest, 400)}")
    if blocking or not any(count for count in digest.values()):
        raise Bail(
            f"{stage_type}: the hand-authored patch was refused, so nothing reached the draft.\n"
            f"      operations: {_short(stamped, 900)}\n"
            f"      patch response: {_short(result, 2000)}\n"
            f"      draft digest: {_short(digest, 400)}"
        )


def _draft_digest(api: Api, project_id: str) -> dict[str, Any]:
    """How many entries the working draft holds per section — did anything land?

    Not the draft document: S5IF-BP05 (`GET /blueprint/draft`) is metadata-only **by design** —
    ``blueprint_reads.draft`` says the document is deliberately absent because entry content goes
    through ``entries?source=DRAFT`` and shipping a whole draft at 5,000 objects would waste the
    bandwidth the review view needs. Counting here therefore has to ask the entries endpoint.
    """
    page = api.get(
        f"/api/v1/projects/{project_id}/blueprint/entries",
        params={"source": "DRAFT"},
        quiet=True,
    )
    # S5IF-BP04's page names its list `entries`, not `items` (EntryPage), so reading the
    # generic paging key here always saw an empty draft.
    items = page.get("entries") or page.get("items") or []
    digest: dict[str, int] = {}
    for item in items:
        if not isinstance(item, dict):
            continue
        section = str(item.get("_section") or item.get("section") or "?")
        digest[section] = digest.get(section, 0) + 1
    return digest


def run_auto_stage(
    api: Api, args: argparse.Namespace, stage: dict[str, Any], *, endpoint: str = "run"
) -> dict[str, Any]:
    """DISCOVERY / BASELINE_COMPILE: no agent, no human submission — just run it (附录 A)."""
    api.post(
        f"/api/v1/stages/{stage['id']}/{endpoint}",
        None,
        label=f"run {stage['stageType']}",
        headers=api.headers(f"seed:{args.run_tag}:{stage['stageType']}:run"),
    )
    final = wait_for_stage(api, stage["id"], timeout=args.timeout, what=stage["stageType"])
    print(f"  {stage['stageType']} -> {final.get('status')}")
    return final


def run_agent_stage_and_close(
    api: Api, args: argparse.Namespace, workflow_id: str, stage: dict[str, Any], *, endpoint: str = "run"
) -> dict[str, Any]:
    """Agent run, then approve the Gate it opens.  Returns the final stage view."""
    run_agent_stage(api, args, stage, endpoint=endpoint)
    # a rerun of a stage whose content is already approved may simply need the human submit
    refreshed = api.get(f"/api/v1/stages/{stage['id']}", quiet=True)
    if refreshed.get("status") == "WAITING_APPROVAL":
        approve_gate(api, workflow_id)
        refreshed = api.get(f"/api/v1/stages/{stage['id']}", quiet=True)
    print(f"  {stage['stageType']} closed at {refreshed.get('status')}")
    return refreshed


def read_entries(api: Api, project_id: str, section: str) -> list[dict[str, Any]]:
    """The approved Blueprint's own entries for one section — did the design actually land?

    The filter parameter is ``sections`` (a list, S5IF-BP04); passing ``section`` is ignored
    and answers the whole Blueprint, which is how a registry check once passed on entries
    that were not in the registry at all.
    """
    view = api.get(
        f"/api/v1/projects/{project_id}/blueprint/entries",
        params={"sections": section},
        quiet=True,
        accept=(200, 404),
    )
    rows = list(view.get("entries", [])) if isinstance(view, dict) else []
    return [row for row in rows if row.get("_section", section) == section]


def read_registry(api: Api, project_id: str) -> list[dict[str, Any]]:
    return read_entries(api, project_id, "registry")


def print_validation_report(api: Api, args: argparse.Namespace, project_id: str, workflow_id: str) -> list[str]:
    """BASELINE_COMPILE's findings, rule ids verbatim — the most valuable output of checkpoint A."""
    issues = api.get(
        f"/api/v1/workflows/{workflow_id}/validation-issues",
        quiet=True,
        params={"projectId": project_id},
    )
    rows = issues.get("items", issues) if isinstance(issues, dict) else issues
    blocking: list[str] = []
    print(f"  validation issues: {len(rows) if isinstance(rows, list) else _short(issues, 300)}")
    if isinstance(rows, list):
        for row in rows:
            level = row.get("level")
            rule = row.get("ruleId") or row.get("rule_id")
            if level == "BLOCKING":
                blocking.append(str(rule))
                print(f"    BLOCKING {rule}: {_short(row.get('description'), 200)}")
    return blocking


# --- checkpoint A -------------------------------------------------------------------


def checkpoint_a(api: Api, args: argparse.Namespace) -> dict[str, Any]:
    """Project + parsed input + the design line closed, BASELINE_COMPILE PASSED.

    One wrinkle the API forces: approving a design Gate promotes the Blueprint, and when that
    promotion is a MINOR change S1's invalidation plan (CP-WF-015 rule 1) marks every affected
    stage INVALIDATED except ``workflow.current_stage_id`` — which by then has already advanced
    to the *next* stage.  So the stage whose Gate was just approved is the one that comes back
    INVALIDATED, and the only legal way onward is to re-drive it (INVALIDATED → READY).  The loop
    below therefore re-runs an invalidated stage instead of assuming one approval is enough; the
    second pass promotes a no-op diff, which is COSMETIC and sticks.
    """
    project = create_project(api, args)
    project_id, workflow_id = str(project["id"]), str(project["workflowId"])
    artifact_id = upload_and_parse(api, args, project_id)
    # S2 requires every requirement entry to cite the input it came from
    # (`source.artifactId` must match `^art-[0-9A-Za-z]{10,40}$`), so the id the parse produced
    # has to reach the hand-authored operations — they read it back off `args`.
    args.input_artifact_id = artifact_id

    schemas = section_schema_report(api, project_id)
    print(f"  blueprint sections: {_short([s.get('section') for s in schemas.get('sections', [])], 500)}")

    approvals: dict[str, int] = {}
    for _round in range(40):
        stages = stage_map(api, workflow_id)
        waiting = [g for g in gate_items(api, workflow_id) if g["status"] == "WAITING"]
        if waiting:
            gate = waiting[0]
            approve_gate(api, workflow_id, expect_type=gate["type"])
            owner = str(gate.get("stageId") or gate.get("type"))
            approvals[owner] = approvals.get(owner, 0) + 1
            if approvals[owner] > 3:
                raise Bail(
                    f"gate {gate['type']} has been approved {approvals[owner]} times "
                    f"and its stage keeps coming back INVALIDATED: {_short(stage_map(api, workflow_id))}"
                )
            continue
        invalidated = [
            t for t in DESIGN_ORDER
            if t in stages and stages[t]["status"] == "INVALIDATED"
        ]
        if invalidated:
            stage_type = invalidated[0]
            stage = stages[stage_type]
            print(
                f"  re-driving {stage_type}: it was invalidated by the promotion of its own Gate "
                "(task #26 — S1 excludes `workflow.current_stage_id` from CP-WF-015's rule 1, but "
                "that id has already advanced to the next stage by the time propagation runs, so "
                "the stage that just passed is not protected and invalidates itself)."
            )
            # Getting out of INVALIDATED needs a rerun, and a rerun of the *agent* is the wrong
            # move for a stage this script authors by hand: the stand-in has nothing new to say
            # about a section that is already populated, so it answers with an empty patch, the
            # resulting promotion is a no-op, and the stage has been seen to land on FAILED after
            # a third pass with its draft still OPEN — which then blocks the next stage's draft,
            # because BP-DRF-001 allows only one active draft per project. Re-claim the stage, then
            # re-run the same close path the READY case uses so the content is authored, not guessed.
            run_auto_stage(api, args, stage, endpoint="rerun")
            # Nothing more here. The rerun re-claims the stage and S2's draft still holds the
            # entries authored on the first pass, so the Gate it opens is approved by the `waiting`
            # branch above. Re-submitting the same hand-authored operations would only be refused:
            # the businessKeys are already in the draft, and BPV-PCH-003 rejects a section holding
            # two entries under one key.
            if state := stage_map(api, workflow_id).get(stage_type, {}).get("status"):
                print(f"  {stage_type} after re-claim: {state}")
            continue
        ready = [t for t in DESIGN_ORDER if t in stages and stages[t]["status"] == "READY"]
        if not ready:
            break
        stage_type = ready[0]
        close_agent_or_authored_stage(api, args, project_id, workflow_id, stage_type, stages[stage_type])

    stages = stage_map(api, workflow_id)
    print("\n  design line:")
    for stage_type in DESIGN_ORDER:
        state = stages.get(stage_type, {}).get("status", "ABSENT")
        print(f"    {stage_type:16} {state}")
    compile_state = stages.get("BASELINE_COMPILE", {}).get("status")
    if compile_state == "FAILED":
        detail = api.get(f"/api/v1/stages/{stages['BASELINE_COMPILE']['id']}", quiet=True)
        raise Bail(f"BASELINE_COMPILE FAILED: {_short(detail)}")
    blocking = print_validation_report(api, args, project_id, workflow_id)
    if compile_state != "PASSED":
        raise Bail(
            f"BASELINE_COMPILE is {compile_state}, not PASSED. Stage states: "
            f"{_short({t: s.get('status') for t, s in stages.items()})}"
        )
    version = str(api.get(f"/api/v1/projects/{project_id}", quiet=True).get("currentBlueprintVersion"))
    counts = {
        section: len(read_entries(api, project_id, section))
        for section in ("requirements", "domain", "mapping", "registry", "pages")
    }
    committed = read_registry(api, project_id)
    print(f"  blueprint version {version}; section entry counts: {_short(counts, 300)}")
    if not counts.get("registry"):
        raise Bail(
            "the design line closed but the Registry is empty, so no Deployment Run would carry "
            f"anything to commit. Counts: {_short(counts)}"
        )
    return {
        "projectId": project_id,
        "workflowId": workflow_id,
        "inputArtifactId": artifact_id,
        "stages": {t: s.get("status") for t, s in stages.items()},
        "authoredBy": {
            t: (
                "auto"
                if t in AUTO_DESIGN_STAGES
                else "hand-authored patch"
                if t in args.manual_stages
                else f"agent ({args.model})"
            )
            for t in DESIGN_ORDER
        },
        "blockingRules": blocking,
        "blueprintVersion": version,
        "registry": committed,
        "sectionCounts": counts,
    }


def close_agent_or_authored_stage(
    api: Api,
    args: argparse.Namespace,
    project_id: str,
    workflow_id: str,
    stage_type: str,
    stage: dict[str, Any],
    *,
    endpoint: str = "run",
) -> None:
    """One stage, closed: hand-authored patch, or the real agent, then its Gate."""
    if stage_type in AUTO_DESIGN_STAGES:
        run_auto_stage(api, args, stage)
        return
    if stage_type in args.manual_stages:
        author_stage(api, args, project_id, stage_type, stage)
        api.post(
            f"/api/v1/stages/{stage['id']}/submit-for-approval",
            None,
            label=f"submit {stage_type} for approval",
            headers=api.headers(f"seed:{args.run_tag}:{stage_type}:submit:{stage.get('attempt', 1)}"),
        )
        return
    final = run_agent_stage_and_close(api, args, workflow_id, stage, endpoint=endpoint)
    if final.get("status") not in ("PASSED", "SKIPPED", "WAITING_APPROVAL", "INVALIDATED"):
        raise Bail(
            f"stage {stage_type} did not pass after an agent run: {_short(final)}\n"
            "      (a FAILED/BLOCKED stage means the model answer did not satisfy S2's section "
            "schema — see /tmp/llm-requests.jsonl)"
        )



# --- placeholder steps --------------------------------------------------------------


RUN_ORDER = ("BIND_COMPILE", "STATIC_VALIDATION", "DHIS2_VALIDATE", "COMMIT", "POST_VERIFY")


def checkpoint_b(
    api: Api,
    args: argparse.Namespace,
    state: dict[str, Any],
    *,
    runner: Api | None = None,
    deployer: Api | None = None,
) -> dict[str, Any]:
    """Deployment Run on the TEST environment, driven to VERIFIED.

    This is the first step that writes to the shared official demo instance. Everything it creates
    carries the `SB_<tag>_` code prefix FR-TST-002 demands, and the prefix is printed before the
    COMMIT so a wrong tag is caught while it can still be stopped.

    `runner` raises the Run, executes its stages, approves PACKAGE_GATE and commits; `deployer`
    approves DEPLOY_GATE.  An enforced deployment requires them to be two different people
    (AC-GATE-04), so they are parameters rather than assumed to be the design-line caller.
    """
    project_id, workflow_id = state["projectId"], state["workflowId"]
    environment = args.test_env
    run_api = runner or api
    appr_api = deployer or api

    created = run_api.post(
        f"/api/v1/workflows/{workflow_id}/deployment-runs",
        {"environmentKey": environment},
        label="create deployment run",
        headers=run_api.headers(f"seed:{args.run_tag}:create-run"),
    )
    run_id = created["run"]["id"]
    print(f"  run {run_id} on {environment}")

    codes = [entry.get("code") for entry in read_registry(api, project_id) if entry.get("code")]
    prefix = code_prefix(args)
    print(f"  about to COMMIT these codes to {args.dhis2_base_url}: {codes}")
    if not codes:
        raise Bail("the approved Blueprint has no managed codes to commit — the Run would write nothing")
    outside = [code for code in codes if not str(code).startswith(f"{prefix}_")]
    if outside:
        raise Bail(f"these codes are outside the run prefix {prefix}_: {outside}")

    for _round in range(30):
        view = run_api.get(f"/api/v1/deployment-runs/{run_id}")
        stages = {s["stageType"]: s for s in view["stages"]}
        gates = view.get("gates", [])
        waiting = [g for g in gates if g["status"] == "WAITING"]
        if waiting:
            gate = waiting[0]
            # DEPLOY_GATE to the approver, PACKAGE_GATE to whoever ran the stage that opened it.
            approve_run_gate(appr_api if gate["type"] == "DEPLOY_GATE" else run_api, args, gate)
            continue
        # COMMIT has no /run endpoint: approving DEPLOY_GATE moves DHIS2_VALIDATE to PASSED and
        # unlocks COMMIT, and the next poll of this loop starts it here.  LOCKED is every later
        # Run stage's initial state, so it must not be read as startable.
        if stages.get("COMMIT", {}).get("status") == "READY":
            deploy = next((g for g in gates if g["type"] == "DEPLOY_GATE"), None)
            if deploy is None:
                raise Bail(f"COMMIT is READY but the run view has no DEPLOY_GATE: {_short(list(stages), 300)}")
            _commit(run_api, args, run_id, stages, deploy["id"])
            continue
        ready = [t for t in RUN_ORDER if t in stages and t != "COMMIT" and stages[t]["status"] == "READY"]
        if not ready:
            break
        run_auto_stage(run_api, args, stages[ready[0]], endpoint="run")

    final = run_api.get(f"/api/v1/deployment-runs/{run_id}")
    status = (final.get("run") or final).get("status")
    stages = {s["stageType"]: s for s in final["stages"]}
    print(f"  run {run_id} -> {status}")
    if status != "VERIFIED":
        raise Bail(
            f"the Run did not reach VERIFIED (status {status}): "
            + _short([(t, stages[t]["status"]) for t in RUN_ORDER if t in stages], 600)
        )
    state["runId"] = run_id
    return state


def _commit(api: Api, args: argparse.Namespace, run_id: str, stages: dict[str, dict[str, Any]], gate_id: str) -> None:
    """Start COMMIT, keyed to the DEPLOY_GATE it follows.

    The key may not be reused across intents: S1 replays a recorded verdict for a key it has
    already answered, so one flat ``seed:<tag>:commit`` turns a first, premature attempt into a
    409 that outlives the approval that made the commit legitimate.
    """
    accepted = api.post(
        f"/api/v1/deployment-runs/{run_id}/commit",
        None,
        label="COMMIT",
        headers=api.headers(f"seed:{args.run_tag}:commit:{gate_id}"),
        accept=(202,),
    )
    print(f"  COMMIT (from {stages.get('COMMIT', {}).get('status')}) -> {accepted.get('status') or accepted}")


def approve_run_gate(api: Api, args: argparse.Namespace, gate: dict[str, Any]) -> dict[str, Any]:
    """Approve a Gate belonging to a Run or to HANDOFF. Idempotency keys are per Gate."""
    return api.post(
        f"/api/v1/gates/{gate['id']}/approve",
        {
            "gateType": gate["type"],
            "seenBoundHashes": gate["boundObjects"],
            "warningDispositions": [
                {"issueId": w.get("issueId") or w.get("issue_id"), "disposition": "ACCEPTED"}
                for w in gate.get("warnings") or []
            ],
            "confirmations": [{"id": c["id"], "confirmed": True} for c in gate.get("confirmationItems") or []]
            + [{"id": "OPERATIONAL_BACKUP_CONFIRMED", "confirmed": True}],
        },
        label=f"approve {gate['type']}",
        headers=api.headers(f"seed:{args.run_tag}:approve:{gate['id']}"),
    )


def checkpoint_c(api: Api, args: argparse.Namespace, state: dict[str, Any]) -> dict[str, Any]:
    """HANDOFF: generate the delivery package through real S2 and approve HANDOFF_GATE."""
    workflow_id = state["workflowId"]
    stages = stage_map(api, workflow_id)
    handoff = stages.get("HANDOFF")
    if handoff is None:
        raise Bail(f"no HANDOFF stage: {_short(stages)}")
    if handoff["status"] == "LOCKED":
        raise Bail(f"HANDOFF is still LOCKED after a VERIFIED Run: {_short(stages)}")
    if handoff["status"] == "READY":
        run_auto_stage(api, args, handoff, endpoint="run")
        wait_for_stage(api, handoff["id"], timeout=args.timeout, what="HANDOFF")

    for gate in [g for g in gate_items(api, workflow_id) if g["type"] == "HANDOFF_GATE" and g["status"] == "WAITING"]:
        approve_run_gate(api, args, gate)

    project = api.get(f"/api/v1/projects/{state['projectId']}")
    # There is no bare /frontend-package: CP-UI-021 exposes the listing and one file's text,
    # and S2 owns the layout, so the delivery is proven by reading a file out of it.
    files = api.get(f"/api/v1/projects/{state['projectId']}/frontend-package/files")
    listed = list(files.get("files", []))
    state["package"] = {"artifactId": files.get("artifactId"), "fileCount": len(listed)}
    print(f"  milestone: {project.get('milestoneStatus')}")
    print(f"  delivery package {files.get('artifactId')}: {len(listed)} files, "
          f"{sum(int(f.get('sizeBytes') or 0) for f in listed)} bytes")
    sample = next((f for f in listed if str(f.get("path", "")).endswith(".md")), None)
    if sample is not None:
        text = api.call(
            "GET",
            f"/api/v1/projects/{state['projectId']}/frontend-package/files/{sample['path']}",
            label=f"read {sample['path']}",
            quiet=True,
        )
        first_line = str(text).strip().splitlines()[0] if text else ""
        print(f"  {sample['path']}: {len(str(text))} chars — {_short(first_line, 90)}")
        state["package"]["sampleFile"] = {"path": sample["path"], "firstLine": first_line}
    state["milestone"] = project.get("milestoneStatus")
    return state


def step_report_sections(api: Api, args: argparse.Namespace, project_id: str) -> None:
    schemas = section_schema_report(api, project_id)
    print(json.dumps(schemas, ensure_ascii=False, indent=2)[:3000])


def main(argv: list[str] | None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--base-url", default="http://127.0.0.1:8201")
    parser.add_argument("--operator", default="seed-bot", help="X-Operator name; replaced by --login-subject")
    parser.add_argument("--login-subject", default="",
                        help="account to sign in as, on a deployment with FACTORY_AUTH_MODE=local")
    parser.add_argument("--password-env", default="FACTORY_SEED_PASSWORD",
                        help="NAME of the env var holding that account's password — never the value")
    parser.add_argument("--run-subject", default="",
                        help="account that raises the Deployment Run, executes its stages, approves "
                             "PACKAGE_GATE and commits — DHIS2_IMPLEMENTER on an enforced deployment")
    parser.add_argument("--run-password-env", default="FACTORY_SEED_RUN_PASSWORD",
                        help="NAME of the env var holding --run-subject's password")
    parser.add_argument("--approver-subject", default="",
                        help="account that approves DEPLOY_GATE — must not be --run-subject (AC-GATE-04)")
    parser.add_argument("--approver-password-env", default="FACTORY_SEED_APPROVER_PASSWORD",
                        help="NAME of the env var holding --approver-subject's password")
    parser.add_argument("--run-tag", default="D1", help="DHIS2 code prefix and idempotency salt (SB_<tag>_)")
    parser.add_argument("--llm-url", default="http://127.0.0.1:8299")
    parser.add_argument("--provider", default=DEFAULT_PROVIDER)
    parser.add_argument("--model", default=DEFAULT_MODEL)
    parser.add_argument("--dhis2-credential-env", required=False, default="DHIS2_CRED",
                        help="NAME of the env var holding user:password — never the value")
    parser.add_argument("--dhis2-base-url", default=DHIS2_DEMO)
    parser.add_argument("--test-env", default="sbtest", help="environment key for the TEST target")
    parser.add_argument("--project-type", default="greenfield", choices=["greenfield", "brownfield"])
    parser.add_argument("--secret-slug", default="", help="default: sb-<run-tag lower>")
    parser.add_argument("--timeout", type=float, default=180.0)
    parser.add_argument("--skip-activate", action="store_true", help="prompts already active")
    parser.add_argument("--manual-stages", default="REQUIREMENTS,DOMAIN,MAPPING,PAGE_DESIGN",
                        help="comma list of design stages to hand-author instead of running the agent")
    parser.add_argument("--until", default="setup", choices=["setup", "design", "run", "handoff", "cleanup", "all"],
                        help="stop after this checkpoint")
    args = parser.parse_args(argv)
    if not args.secret_slug:
        args.secret_slug = f"sb-{args.run_tag.lower()}"
        if len(args.secret_slug) < 2 or len(args.secret_slug) > 64:
            raise Bail(f"--run-tag {args.run_tag} cannot make a 2-64 character lowercase slug")
    args.manual_stages = {s.strip().upper() for s in args.manual_stages.split(",") if s.strip()}

    api = Api(args.base_url, args.operator)
    try:
        health = wait_for_api(api, 30)
        print(f"backend providers: {health.get('providers')}")
        if args.login_subject:
            password = (os.environ.get(args.password_env) or "").strip()
            if not password:
                raise Bail(f"--login-subject needs the password in ${args.password_env}")
            session = api.sign_in(args.login_subject, password)
            print(f"  signed in as {session.get('subject')} roles={session.get('roles')}")
        runner = sign_in_as(api, args, args.run_subject, args.run_password_env)
        deployer = sign_in_as(api, args, args.approver_subject, args.approver_password_env)
        if args.run_subject and args.run_subject == args.approver_subject:
            raise Bail(
                "--run-subject and --approver-subject have to be different accounts: AC-GATE-04 "
                "refuses to let the person who executed a Run approve its DEPLOY_GATE"
            )
        register_llm(api, args)
        if not args.skip_activate:
            activate_prompts(api, args)
        register_environment(api, args, args.test_env, "TEST")
        if args.until == "setup":
            print("\nSETUP DONE — provider registered, prompts active, DHIS2 test environment passes.")
            return 0
        design = checkpoint_a(api, args)
        print("\nCHECKPOINT A — design line:")
        print(json.dumps(design, ensure_ascii=False, indent=2))
        # scoped to this project, because a platform grant leaves the caller a non-member (FR-SEC-004)
        grant_project_role(api, args, design["projectId"], args.run_subject, "DHIS2_IMPLEMENTER")
        grant_project_role(api, args, design["projectId"], args.approver_subject, "DEPLOYMENT_APPROVER")
        if args.until == "design":
            return 0
        run_state = checkpoint_b(api, args, design, runner=runner, deployer=deployer)
        if args.until == "run":
            print(json.dumps(run_state, ensure_ascii=False, indent=2))
            return 0
        handed = checkpoint_c(api, args, run_state)
        print("\nCHECKPOINT C — delivery:")
        print(json.dumps(handed, ensure_ascii=False, indent=2))
        return 0
    except Bail as bail:
        print(f"\nSTOPPED AT: {bail}", file=sys.stderr)
        return 1
    finally:
        api.close()


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
