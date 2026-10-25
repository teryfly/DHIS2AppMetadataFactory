"""Deterministic file emission (FR-CMP-002).

Every file is written through S0's canonical serializers, every collection is
sorted before it is emitted, and the ZIP carries no timestamps of its own — the
product of two runs with the same inputs is byte-identical (BP-CMP-004).
"""

from __future__ import annotations

import json
from typing import Any

from factory_foundation.api import file_json, structure_hash

from factory_blueprint.compilers.context import CompileInputs

JSON_MEDIA_TYPE = "application/json"
MARKDOWN_MEDIA_TYPE = "text/markdown"

#: metadata package layout, overall SRS 12.4 (S2 SRS BP-CMP-021)
METADATA_FILE = "metadata.json"
MANIFEST_FILE = "metadata_manifest.json"
DEPENDENCY_GRAPH_FILE = "dependency-graph.json"
CHANGE_SUMMARY_FILE = "change-summary.json"
DATASTORE_FILE = "datastore-changes.json"
VIEWS_PREFIX = "views"
README_FILE = "README.md"

#: development-package prefixes (overall SRS 17.1).  They live here rather than beside
#: the constants of one compiler because both the emitter that writes these files and
#: the BP-HO-004 listing that classifies them read them, and the two must not drift.
PAGES_PREFIX = "pages/"
I18N_PREFIX = "i18n/"


def json_bytes(document: Any) -> bytes:
    return file_json(document)


def load_json(data: bytes) -> dict[str, Any]:
    """Read back a JSON file this package just wrote, for a check over the bytes."""
    loaded: dict[str, Any] = json.loads(data.decode("utf-8"))
    return loaded


def md_lines(sections: list[tuple[str, list[str]]]) -> bytes:
    """Markdown built only from the data given: no clock, no random, no ordering drift."""
    out: list[str] = []
    for title, lines in sections:
        out.append(f"# {title}" if not out else f"## {title}")
        out.extend(lines)
        out.append("")
    return ("\n".join(out).rstrip() + "\n").encode("utf-8")


def yaml_bytes(document: Any) -> bytes:
    """Deterministic YAML, for the one product the SRS fixes as `.yaml` (17.1).

    ``sort_keys=False`` because the document is already built in sorted order and the
    key order is part of the product; ``width`` is pinned so a long description cannot
    wrap differently between runs or library versions.
    """
    import yaml

    return yaml.safe_dump(
        document,
        allow_unicode=True,
        default_flow_style=False,
        sort_keys=False,
        width=4096,
    ).encode("utf-8")


def view_files_by_type(payload: dict[str, Any]) -> dict[str, bytes]:
    """Read-only per-type views; the payload itself stays a single import (FR-CMP-005)."""
    return {
        f"{VIEWS_PREFIX}/{key}.json": json_bytes({key: payload[key]})
        for key in sorted(payload)
        if isinstance(payload[key], list)
    }


def payload_hash(payload: dict[str, Any]) -> str:
    """Hash of the logical import payload, the value S1 shows at PACKAGE_GATE."""
    return structure_hash(payload)


def outputs_hash(files: dict[str, bytes]) -> dict[str, str]:
    from factory_foundation.api import content_hash

    return {name: content_hash(data) for name, data in sorted(files.items())}


def package_hash(files: dict[str, bytes]) -> str:
    from factory_foundation.api import content_hash

    return content_hash(b"".join(files[name] for name in sorted(files)))


def readme(
    inputs: CompileInputs,
    counts: dict[str, dict[str, int]],
    notes: list[str],
    differences: list[dict[str, Any]] | None = None,
) -> bytes:
    lines = [
        f"DHIS2 metadata package for project `{inputs.project_id}`.",
        "",
        f"- Blueprint version: {inputs.blueprint_version}",
        f"- Blueprint content hash: {inputs.blueprint_hash}",
        f"- Target DHIS2 version: {inputs.dhis2_version or 'unbound (baseline compile)'}",
        *(
            [f"- Capability Profile: {inputs.profile.profile_version}"]
            if inputs.profile is not None
            else []
        ),
        f"- Environment: {inputs.environment_key or 'not bound (baseline compile)'}",
        f"- Build timestamp (from the build context): {inputs.build_context.built_at.isoformat()}",
        "",
        "## Objects by type",
        "",
        "| type | created | updated | unchanged |",
        "| --- | --- | --- | --- |",
    ]
    for dhis2_type in sorted(counts):
        row = counts[dhis2_type]
        lines.append(
            f"| {dhis2_type} | {row.get('create', 0)} | {row.get('update', 0)} "
            f"| {row.get('unchanged', 0)} |"
        )
    if notes:
        lines.extend(["", "## Notes", ""])
        lines.extend(f"- {note}" for note in sorted(notes))
    if differences:
        # AC-CAP-01: the same Blueprint against another version produces another package,
        # and the reason has to be readable next to the bytes rather than inferred
        lines.extend(["", "## Differences this target version caused", ""])
        lines.extend(
            f"- {item['dhis2_type'] or 'the whole product'} `{item['name']}`: "
            f"{item['detail']} ({item['effect']})"
            for item in differences
        )
    lines.extend(
        [
            "",
            "This package is a single logical import: submit `metadata.json` as one",
            "request. `views/` is read-only and `metadata_manifest.json` holds the",
            "build record, the object list, dispositions, the managed scope of this",
            "environment, the observation baselines, the UID↔businessKey map, the",
            "controlled fields, the environment-dependent objects, the expression",
            "validation plan and the deletion candidates.",
        ]
    )
    return ("\n".join(lines) + "\n").encode("utf-8")
