#!/usr/bin/env python
"""Create a demo project against a running ``factory-api`` (development only).

    python scripts/seed_demo.py --base-url http://127.0.0.1:8080

Needs a server started with ``FACTORY_PROVIDER_MODE=fakes`` and
``FACTORY_FAKE_ENVIRONMENTS=dev:TEST,prod:PRODUCTION`` — S1 itself creates no
business content, so the demo data is what a human would type in the UI.
"""

from __future__ import annotations

import argparse
import time

import httpx

DEMO_INPUT = b"""# clinic dashboard requirements

- monthly summary of ART enrolment per facility
- export to CSV for the district review
- the indicator set is the national HIV dashboard
"""


def _wait_for_api(client: httpx.Client, timeout: float) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            if client.get("/healthz").status_code == 200:
                return
        except httpx.HTTPError:
            time.sleep(0.3)
    raise SystemExit(f"no healthy factory-api within {timeout}s")


def _environments(raw: str) -> list[dict[str, object]]:
    targets = []
    for order, item in enumerate(raw.split(",")):
        key, _, _environment_class = item.partition(":")
        targets.append(
            {
                "environmentKey": key.strip(),
                "promotionOrder": order * 100,
                "isDesignReference": order == 0,
            }
        )
    return targets


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default="http://127.0.0.1:8080")
    parser.add_argument("--operator", default="demo")
    parser.add_argument("--environments", default="dev:TEST,prod:PRODUCTION")
    parser.add_argument("--name", default="demo clinic dashboard")
    parser.add_argument("--wait", type=float, default=20.0)
    args = parser.parse_args(argv)

    headers = {"X-Operator": args.operator}
    with httpx.Client(base_url=args.base_url, headers=headers, timeout=15) as client:
        _wait_for_api(client, args.wait)

        project = client.post(
            "/api/v1/projects",
            json={
                "name": args.name,
                "ownerName": args.operator,
                "description": "seeded by scripts/seed_demo.py",
                "deliveryMode": "A",
                "targets": _environments(args.environments),
            },
            headers={**headers, "Idempotency-Key": f"seed-{args.name}"},
        )
        project.raise_for_status()
        view = project.json()
        workflow_id = view["workflowId"]
        stage_id = view["currentStageId"]

        upload = client.post(
            f"/api/v1/projects/{view['id']}/inputs",
            files={"file": ("requirements.md", DEMO_INPUT, "text/markdown")},
            data={"dataClassification": "INTERNAL"},
        )
        upload.raise_for_status()

        started = client.post(f"/api/v1/stages/{stage_id}/run", json={})
        started.raise_for_status()

        print(f"project   {view['id']}")
        print(f"workflow  {workflow_id}")
        print(f"stage     {stage_id} -> {started.json().get('status')}")
        print(f"input     {upload.json().get('artifactId')}")
        print()
        print("watch it move:")
        print(f"  curl -N -H 'X-Operator: {args.operator}' {args.base_url}/api/v1/workflows/{workflow_id}/events")
        print(f"  curl -s -H 'X-Operator: {args.operator}' {args.base_url}/api/v1/workflows/{workflow_id}/allowed-actions")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
