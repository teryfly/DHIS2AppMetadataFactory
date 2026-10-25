"""``metadata_manifest.json`` and ``MANIFEST`` (BP-CMP-021/023/027/027a).

S4 never calls S2: everything it needs to drift-check, snapshot, map an
ImportReport back and verify the deployment after commit must be in this file.
Nothing here references an artifact ID that could change on import — inputs are
named by type plus hash.
"""

from __future__ import annotations

from typing import Any

from factory_blueprint.compilers.context import CompileInputs
from factory_blueprint.compilers.files import (
    CHANGE_SUMMARY_FILE,
    DATASTORE_FILE,
    DEPENDENCY_GRAPH_FILE,
    MANIFEST_FILE,
    METADATA_FILE,
    README_FILE,
    VIEWS_PREFIX,
)
from factory_blueprint.compilers.metadata import CompileOutput

#: the twelve manifest sections, in the order the SRS lists them (overall SRS 12.4)
MANIFEST_SECTIONS: tuple[str, ...] = (
    "buildRecord",
    "objects",
    "hashes",
    "dispositions",
    "provenance",
    "managedScope",
    "observationBaselines",
    "uidToBusinessKey",
    "controlledFields",
    "environmentDependentObjects",
    "expressionValidation",
    "deletionCandidates",
)


def metadata_manifest(
    inputs: CompileInputs,
    output: CompileOutput,
    build_record: dict[str, Any],
    file_hashes: dict[str, str],
) -> dict[str, Any]:
    """The full manifest S1 shows at PACKAGE_GATE and S4 consumes."""
    return {
        "schemaVersion": 1,
        "projectId": inputs.project_id,
        "blueprintVersion": inputs.blueprint_version,
        "environmentKey": inputs.environment_key,
        "buildRecord": build_record,
        "objects": _objects(inputs, output),
        "payloadFiles": dict(sorted(file_hashes.items())),
        "dispositions": {
            str(entry["businessKey"]): entry.get("disposition")
            for entry in sorted(output.uid_map, key=lambda item: str(item["businessKey"]))
        },
        "provenance": {
            "blueprint": {"type": "BLUEPRINT", "artifactHash": inputs.blueprint_hash},
            "capabilityProfile": (
                inputs.profile_ref.as_json() if inputs.profile_ref else None
            ),
            "discoveryResult": (
                inputs.discovery_ref.as_json() if inputs.discovery_ref else None
            ),
        },
        "managedScope": output.managed_scope,
        "observationBaselines": sorted(
            output.observation_baselines, key=lambda item: str(item["businessKey"])
        ),
        "uidToBusinessKey": sorted(
            output.uid_map, key=lambda item: (str(item["dhis2Type"]), str(item["businessKey"]))
        ),
        "controlledFields": {
            key: sorted(values) for key, values in sorted(output.controlled_fields.items())
        },
        "environmentDependentObjects": sorted(output.environment_dependent),
        "expressionValidation": output.expressions,
        "deletionCandidates": sorted(
            output.deletion_candidates, key=lambda item: str(item["businessKey"])
        ),
        "referencesOutsideManagedScope": output.change_summary.get(
            "referencesOutsideManagedScope", []
        ),
        "changeSummary": output.change_summary,
        "notes": sorted(set(output.notes)),
    }


def _objects(inputs: CompileInputs, output: CompileOutput) -> list[dict[str, Any]]:
    """Every payload object with the UID S4 must address it by.

    The payload itself carries no ``id`` (FR-CMP-006), so this is where an UPDATE finds
    its merge target and a CREATE finds the object its freshly allocated UID names.
    """
    stated = {
        str(item.get("businessKey")): item for item in output.uid_map
    }
    return sorted(
        (
            {
                "businessKey": key,
                "dhis2Type": str(stated.get(key, {}).get("dhis2Type") or ""),
                "uid": stated.get(key, {}).get("uid") or inputs.bindings.get(key),
                "code": stated.get(key, {}).get("code"),
                "disposition": stated.get(key, {}).get("disposition"),
                "state": output.object_states.get(key, "UNKNOWN"),
            }
            for key in output.objects
        ),
        key=lambda item: (item["dhis2Type"], item["businessKey"]),
    )


def top_level_manifest(
    inputs: CompileInputs,
    build_record: dict[str, Any],
    products: dict[str, str],
    *,
    artifact_kind: str,
) -> dict[str, Any]:
    """The ``MANIFEST`` artifact: what was produced, from what, and how to reproduce it."""
    return {
        "schemaVersion": 1,
        "kind": artifact_kind,
        "projectId": inputs.project_id,
        "blueprintVersion": inputs.blueprint_version,
        "environmentKey": inputs.environment_key,
        "buildRecord": build_record,
        "products": dict(sorted(products.items())),
        "inputs": [ref.model_dump() for ref in inputs.input_refs()],
        "packageFiles": [
            f"{VIEWS_PREFIX}/…",
            CHANGE_SUMMARY_FILE,
            DATASTORE_FILE,
            DEPENDENCY_GRAPH_FILE,
            MANIFEST_FILE,
            METADATA_FILE,
            README_FILE,
        ]
        if artifact_kind == "METADATA_PACKAGE"
        else [MANIFEST_FILE],
    }
