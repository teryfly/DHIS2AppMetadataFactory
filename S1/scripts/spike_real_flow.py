"""Spike: drive one real agent stage through the assembled backend.

Nothing here is product code.  It answers one question — with S2/S3/S4 wired for real and a
live OpenAI-compatible endpoint on the loopback interface, does a design stage actually
produce a Blueprint patch?  It prints the first hard failure it meets, and the HTTP status
that carried it, so the gap list is built from observed errors rather than from reading.
"""

from __future__ import annotations

import argparse
import json
import sys

import httpx

BASE_DEFAULT = "http://127.0.0.1:8299"
DHIS2 = "https://play.im.dhis2.org/stable-2-41-10/api"
OPS = {"X-Operator": "spike", "Content-Type": "application/json"}


class Bail(Exception):
    pass


def show(label: str, response: httpx.Response, *, quiet: bool = False) -> dict:
    if response.status_code >= 400:
        print(f"[{response.status_code}] {label}\n      {response.text[:700]}")
        raise Bail(label)
    if not quiet:
        body = response.text
        print(f"[ok] {label}: {body[:260]}")
    try:
        return response.json()
    except Exception:  # noqa: BLE001
        return {}


def main(argv: list[str] | None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base", default="http://127.0.0.1:8199")
    parser.add_argument("--llm", default=BASE_DEFAULT)
    parser.add_argument("--skip-dhis2", action="store_true")
    parser.add_argument("--stage", default="REQUIREMENTS")
    args = parser.parse_args(argv)

    client = httpx.Client(base_url=args.base, timeout=120)

    health = client.get("/healthz").json()
    print(f"providers as booted: {health['providers']}")

    # --- 1. the model, registered the way an operator would register it -------------
    secret = show("PUT /secrets/stub-key", client.put("/api/v1/secrets/stub-key", headers=OPS, json={"material": "stub-secret"}), quiet=True)
    print(f"  secret -> {json.dumps(secret)[:200]}")

    show(
        "POST /governance/providers",
        client.post(
            "/api/v1/governance/providers",
            headers=OPS,
            json={
                "name": "stub",
                "providerClass": "LOCAL_ONLY",
                "endpoint": f"{args.llm}/v1",
                "apiKeySecretRef": "db://stub-key",
                "adapter": "openai_compat",
                "enabled": True,
            },
        ),
    )
    show(
        "POST /governance/models",
        client.post(
            "/api/v1/governance/models",
            headers=OPS,
            json={
                "provider": "stub",
                "model": "stub-model",
                "endpoint": f"{args.llm}/v1",
                "apiKeySecretRef": "db://stub-key",
                "capabilities": {"structuredOutput": True, "maxContextTokens": 8000, "supportsImages": False},
                "applicableAgents": {"REQUIREMENTS": 1, "DOMAIN": 1, "MAPPING": 1, "METADATA_DESIGN": 1, "API": 1, "UI": 1, "FIXTURE": 1, "REVIEW": 1},
                "enabled": True,
            },
        ),
    )
    providers = show("GET /governance/providers", client.get("/api/v1/governance/providers", headers=OPS), quiet=True)
    models = show("GET /governance/models", client.get("/api/v1/governance/models", headers=OPS), quiet=True)
    print(f"  providers={json.dumps(providers, ensure_ascii=False)[:300]}")
    print(f"  models={json.dumps(models, ensure_ascii=False)[:400]}")

    # --- 2. a DHIS2 target, so S4 has something real to speak to --------------------
    if not args.skip_dhis2:
        show("PUT /secrets/dhis2-demo", client.put("/api/v1/secrets/dhis2-demo", headers=OPS, json={"material": "admin:district"}), quiet=True)
        created = show(
            "POST /environments",
            client.post(
                "/api/v1/environments",
                headers=OPS,
                json={
                    "environmentKey": "dev",
                    "name": "DHIS2 2.41 official demo",
                    "baseUrl": DHIS2,
                    "environmentClass": "TEST",
                    "versionConstraint": "2.41.x",
                    "authMode": "BASIC",
                    "llmDataPolicy": "LOCAL_ONLY",
                    "credentials": [
                        {"scope": "discovery", "secretRef": "db://dhis2-demo", "label": "discovery"},
                        {"scope": "import", "secretRef": "db://dhis2-demo", "label": "import"},
                    ],
                },
            ),
        )
        show("POST /environments/dev/test-connection", client.post("/api/v1/environments/dev/test-connection", headers=OPS, json={}))
        show("POST /environments/dev/probe-capabilities", client.post("/api/v1/environments/dev/probe-capabilities", headers=OPS, json={}))

    # --- 3. a project, which is what makes S2 create a Blueprint --------------------
    project = show(
        "POST /projects",
        client.post(
            "/api/v1/projects",
            headers={**OPS, "Idempotency-Key": "spike-project-1"},
            json={
                "name": "spike clinic dashboard",
                "ownerName": "spike",
                "description": "spike",
                "deliveryMode": "A",
                "targets": [{"environmentKey": "dev", "promotionOrder": 0, "isDesignReference": True}],
            },
        ),
    )
    project_id, workflow_id, stage_id = project["id"], project["workflowId"], project["currentStageId"]
    print(f"  project={project_id} workflow={workflow_id} stage={stage_id}")

    show("POST /inputs", client.post(f"/api/v1/projects/{project_id}/inputs", headers={"X-Operator": "spike"}, files={"file": ("req.md", b"- monthly ART enrolment per facility\n- CSV export\n", "text/markdown")}, data={"dataClassification": "INTERNAL"}))

    # --- 4. the stage run, which is where real S3 either answers or does not --------
    started = show("POST /stages/{id}/run", client.post(f"/api/v1/stages/{stage_id}/run", headers={**OPS, "Idempotency-Key": "spike-run-1"}, json={}))
    for _ in range(40):
        workflow = client.get(f"/api/v1/workflows/{workflow_id}", headers=OPS).json()
        stage = next(s for s in workflow["stages"] if s["id"] == stage_id)
        print(f"  stage {stage['stageType']} -> {stage['status']}")
        if stage["status"] not in ("RUNNING", "READY"):
            break
        import time

        time.sleep(2)
    runs = client.get(f"/api/v1/workflows/{workflow_id}/agent-runs", headers=OPS)
    print(f"  agent-runs [{runs.status_code}]: {runs.text[:900]}")
    detail = client.get(f"/api/v1/stages/{stage_id}", headers=OPS)
    print(f"  stage detail: {detail.text[:900]}")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main(sys.argv[1:]))
    except Bail as bail:
        print(f"STOPPED AT: {bail}")
        raise SystemExit(1)
