#!/usr/bin/env python
"""Export the OpenAPI 3.1 document of the assembled REST surface (CP-API-009).

    python scripts/export_openapi.py --out docs/openapi.json

The document is generated from the same ``create_app`` the process serves, so a
route that exists in code cannot be missing from the published contract.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))


def build_document() -> dict:
    from factory_control.adapters.registry import default_ports
    from factory_control.config import Settings
    from factory_control.events.bus import EventBus
    from factory_control.rest.app import create_app
    from factory_control.runtime import build_runtime

    settings = Settings(provider_mode="fakes", workflow_engine="inprocess", database_url="sqlite://")
    runtime = build_runtime(settings=settings, bus=EventBus(), **default_ports(settings=settings))
    return create_app(runtime).openapi()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", default="docs/openapi.json", help="where to write the document")
    args = parser.parse_args(argv)

    document = build_document()
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(document, indent=2, ensure_ascii=False, sort_keys=True) + "\n", encoding="utf-8")

    paths = document["paths"]
    print(f"{out}: openapi {document['openapi']}, {len(paths)} paths, {document['info']['version']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
