"""Golden products: the byte hashes of a fixed Blueprint's compiles.

AC-REG-01 and BP-CMP-004 promise that recompiling the same version with the same
inputs produces the same bytes.  Comparing two runs inside one process catches most
order bugs; only a committed snapshot catches the ones that would otherwise pass —
a set that changed iteration order between Python releases, a ZIP header, a field
that started leaking an artifact ID.
"""

from __future__ import annotations

import json
import os
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from factory_foundation.api import structure_hash

from factory_blueprint import api, artifacts, deps
from factory_blueprint.build import service as builds
from factory_blueprint.enums import BuildStatus
from factory_blueprint.s4.stub import StubDhis2
from factory_blueprint.testing.harness import BlueprintHarness
from tests.integration import test_compile as scenario

SNAPSHOT = Path(__file__).parent / "products.json"
UPDATE = os.environ.get("BP_SNAPSHOT_UPDATE") == "1"


@pytest.fixture(autouse=True)
def _stub(harness: BlueprintHarness) -> Iterator[Any]:
    stub = StubDhis2()
    deps.set_dhis2(stub)
    yield stub
    deps.reset_providers()


def _setup() -> tuple[str, str]:
    """The scenario's approved version and its Capability Profile artifact."""
    return scenario.approved_version(), scenario._profile_artifact()


def _products(version: str, profile: str, suffix: str) -> dict[str, Any]:
    """Baseline and environment-bound products, hashed file by file.

    Every call registers a *fresh* Discovery Result, so two calls differ only in the
    artifact IDs of their inputs — which is the import case AC-DATA-01 cares about.
    """
    baseline = builds.compile_baseline(
        scenario.PROJECT, version, profile, scenario.context(),
        idempotency_key=f"k-snap-baseline-{suffix}",
    )
    environment = builds.compile_for_environment(
        scenario.PROJECT, version, scenario.TEST_ENV,
        scenario._discovery_artifact(scenario.TEST_ENV), profile, None,
        scenario.context(), idempotency_key=f"k-snap-environment-{suffix}",
        design_reference_environment=scenario.DESIGN_ENV,
    )
    assert baseline.status is BuildStatus.COMPLETED, baseline.validation
    assert environment.status is BuildStatus.COMPLETED, environment.binding_issues
    baseline_bytes, _ = artifacts.read_bytes(baseline.artifact_ids["METADATA_BASELINE"])
    package_bytes, _ = artifacts.read_bytes(environment.artifact_ids["METADATA_PACKAGE"])
    return {
        "blueprintVersion": version,
        "baseline": artifacts.archive_hashes(baseline_bytes),
        "environment": artifacts.archive_hashes(package_bytes),
        "baselinePackageHash": baseline.package_hash,
        "environmentPackageHash": environment.package_hash,
        "environmentPayloadHash": environment.payload_hash,
        # BP-TD-009 read back through the public API.  Hashed without the build and
        # artifact identifiers the two calls cannot share, so what is pinned is the graph
        # a caller sees, and it is pinned next to the file it came from.
        "baselineGraph": _graph_digest(baseline.build_id),
        "environmentGraph": _graph_digest(environment.build_id),
    }


def _graph_digest(build_id: str) -> str:
    graph = api.get_dependency_graph(build_id)
    return structure_hash(
        {
            "kind": graph.kind,
            "blueprintVersion": graph.blueprint_version,
            "schemaVersion": graph.schema_version,
            "nodes": [node.model_dump(mode="json") for node in graph.nodes],
            "edges": [edge.model_dump(mode="json") for edge in graph.edges],
            "orphans": graph.orphans,
            "unresolvedRegistryRefs": [
                item.model_dump(mode="json") for item in graph.unresolved_registry_refs
            ],
        }
    )


def _differing(a: dict[str, Any], b: dict[str, Any]) -> list[str]:
    """Which products changed, named — a bare dict diff hides the interesting line."""
    out: list[str] = []
    for key in sorted(set(a) | set(b)):
        first, second = a.get(key), b.get(key)
        if first == second:
            continue
        if isinstance(first, dict) and isinstance(second, dict):
            out.extend(
                f"{key}/{name}"
                for name in sorted(set(first) | set(second))
                if first.get(name) != second.get(name)
            )
        else:
            out.append(str(key))
    return out


def test_products_match_the_committed_snapshot() -> None:
    version, profile = _setup()
    actual = _products(version, profile, "a")
    if UPDATE or not SNAPSHOT.exists():  # pragma: no cover - maintenance path
        SNAPSHOT.write_text(json.dumps(actual, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        pytest.skip("snapshot recorded; run again to compare against it")
    expected = json.loads(SNAPSHOT.read_text(encoding="utf-8"))
    assert _differing(actual, expected) == [], (
        "a product changed shape: decide whether that is intended, then rerecord "
        "with BP_SNAPSHOT_UPDATE=1"
    )


def test_products_survive_new_input_artifact_ids() -> None:
    """BP-CMP-004 with AC-DATA-01: the recipe decides the bytes, not the run or the IDs."""
    version, profile = _setup()
    assert _differing(_products(version, profile, "a"), _products(version, profile, "b")) == []
