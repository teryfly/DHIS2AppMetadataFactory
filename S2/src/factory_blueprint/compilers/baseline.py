"""Baseline compilation (BP-CMP-010): the environment-independent products.

Produced before any environment is touched, so that everything determinable from
the Blueprint alone — object definitions, the dependency graph, Blueprint and
cross-product validation — is checked without a DHIS2 instance.  Environment
dependent references stay as placeholders; this package is never deployed.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from factory_blueprint.compilers import files, metadata
from factory_blueprint.compilers.context import CompileInputs
from factory_blueprint.compilers.manifest import top_level_manifest
from factory_blueprint.dto.issue import Issue


@dataclass
class BaselineProduct:
    files: dict[str, bytes] = field(default_factory=dict)
    output: metadata.CompileOutput = field(default_factory=metadata.CompileOutput)
    issues: list[Issue] = field(default_factory=list)
    payload_hash: str = ""

    @property
    def blocking(self) -> bool:
        return self.output.blocking or any(
            issue.level.value == "BLOCKING" for issue in self.issues
        )


BASELINE_METADATA_FILE = "metadata-baseline.json"
BASELINE_SUMMARY_FILE = "change-summary.json"
BASELINE_GRAPH_FILE = "dependency-graph.json"
BASELINE_README_FILE = "README.md"


def compile_baseline(inputs: CompileInputs) -> BaselineProduct:
    """Compile the environment-independent metadata baseline of one version."""
    output = metadata.compile_metadata(inputs)
    baseline_document = {
        "schemaVersion": 1,
        "kind": "METADATA_BASELINE",
        "projectId": inputs.project_id,
        "blueprintVersion": inputs.blueprint_version,
        "blueprintHash": inputs.blueprint_hash,
        "environmentIndependent": True,
        "payload": output.payload,
        "dependencyGraph": output.dependency_graph,
        "changeSummary": output.change_summary,
        "expressions": output.expressions,
        "notes": sorted(set(output.notes)),
    }
    counts = {
        dhis2_type: {"create": values.get("created", 0), "update": values.get("updated", 0),
                    "unchanged": values.get("unchanged", 0)}
        for dhis2_type, values in output.counts.items()
    }
    product = BaselineProduct(
        output=output,
        issues=list(output.issues),
        payload_hash=files.payload_hash(output.payload),
    )
    product.files = {
        BASELINE_METADATA_FILE: files.json_bytes(baseline_document),
        BASELINE_SUMMARY_FILE: files.json_bytes({"byType": counts}),
        BASELINE_GRAPH_FILE: files.json_bytes(output.dependency_graph),
        BASELINE_README_FILE: files.readme(inputs, counts, output.notes),
    }
    return product


def baseline_manifest(inputs: CompileInputs, build_record: dict[str, Any], product: BaselineProduct) -> dict[str, Any]:
    """The MANIFEST describing the baseline products and their hashes."""
    from factory_foundation.api import content_hash

    return top_level_manifest(
        inputs,
        build_record,
        {name: content_hash(data) for name, data in sorted(product.files.items())},
        artifact_kind="METADATA_BASELINE",
    )
