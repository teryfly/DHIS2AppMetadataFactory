"""Environment-bound compilation (BP-CMP-020 … 030).

For one environment: resolve bindings, check UID/code/ownership, compile the
single logical import payload, produce the environment diff and the manifest S4
needs.  The S4 probe results are returned to the caller and stored on the build
record — never inside a product, which would make recompilation environment-state
dependent (BP-CMP-001, AC-DATA-01).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from factory_blueprint.capability import view_of
from factory_blueprint.compilers import files, metadata
from factory_blueprint.compilers.context import CompileInputs
from factory_blueprint.compilers.manifest import metadata_manifest, top_level_manifest
from factory_blueprint.dto.diff import DiffReport
from factory_blueprint.dto.issue import Issue
from factory_blueprint.registry import binding as bindings_module

MANIFEST_NAME = files.MANIFEST_FILE
PAYLOAD_NAME = files.METADATA_FILE


@dataclass
class EnvironmentProduct:
    files: dict[str, bytes] = field(default_factory=dict)
    output: metadata.CompileOutput = field(default_factory=metadata.CompileOutput)
    binding_issues: list[Issue] = field(default_factory=list)
    diff: DiffReport | None = None
    payload_hash: str = ""

    @property
    def blocking(self) -> bool:
        return self.output.blocking or any(
            issue.level.value == "BLOCKING" for issue in self.binding_issues
        )


def observed_content(inputs: CompileInputs) -> dict[str, dict[str, Any]]:
    """UID -> the environment's current content, for the UPDATE merge (FR-CMP-007)."""
    if inputs.discovery is None:
        return {}
    return {
        item.uid: dict(item.content)
        for item in inputs.discovery.objects
        if isinstance(item.content, dict) and item.uid
    }


def compile_for_environment(inputs: CompileInputs) -> EnvironmentProduct:
    """Compile everything for one environment, given its resolved bindings."""
    prepared = CompileInputs(
        project_id=inputs.project_id,
        blueprint_version=inputs.blueprint_version,
        blueprint_hash=inputs.blueprint_hash,
        document=inputs.document,
        build_context=inputs.build_context,
        blueprint_artifact_id=inputs.blueprint_artifact_id,
        profile=inputs.profile,
        profile_ref=inputs.profile_ref,
        discovery=inputs.discovery,
        discovery_ref=inputs.discovery_ref,
        environment_key=inputs.environment_key,
        bindings=dict(inputs.bindings),
        observed=observed_content(inputs),
        design_environment=inputs.design_environment,
        change_request_id=inputs.change_request_id,
        deletion_candidates=list(inputs.deletion_candidates),
        managed_scope=list(inputs.managed_scope),
    )
    output = metadata.compile_metadata(prepared)
    payload = prepared_payload(output)
    payload_hashes = files.outputs_hash({PAYLOAD_NAME: files.json_bytes(payload)})
    manifest_document = metadata_manifest(
        prepared, output, {"compatibility": prepared.compatibility()}, payload_hashes
    )
    product_files = {
        PAYLOAD_NAME: files.json_bytes(payload),
        MANIFEST_NAME: files.json_bytes(manifest_document),
        files.DEPENDENCY_GRAPH_FILE: files.json_bytes(output.dependency_graph),
        files.CHANGE_SUMMARY_FILE: files.json_bytes(output.change_summary),
        files.README_FILE: files.readme(
            prepared, readable_counts(output), output.notes, output.profile_differences
        ),
    }
    product_files.update(files.view_files_by_type(payload))
    product_files.update(_datastore_file(prepared))

    product = EnvironmentProduct(
        output=output,
        files=product_files,
        payload_hash=files.payload_hash(payload),
        diff=_environment_diff(prepared, output),
    )
    return product


def prepared_payload(output: metadata.CompileOutput) -> dict[str, Any]:
    """The single logical import payload — no system control fields inside objects.

    Deletions stay out of it on purpose (FR-CMP-006, FR-CMP-009): a delete is never part
    of the compiled payload, only of the manifest's ``deletionCandidates`` set, and S4
    reads it from there and runs it as its own step.  ``businessKey`` in particular means
    nothing to DHIS2, so carrying it here would put a Factory-only identifier into the
    bytes the gate hashes and the instance is sent.
    """
    return dict(output.payload)


def readable_counts(output: metadata.CompileOutput) -> dict[str, dict[str, int]]:
    return {
        dhis2_type: {
            "create": values.get("created", 0),
            "update": values.get("updated", 0),
            "unchanged": values.get("unchanged", 0),
        }
        for dhis2_type, values in output.counts.items()
    }


def _datastore_file(inputs: CompileInputs) -> dict[str, bytes]:
    """DataStore changes are an M5 product; the file is only written when there are any."""
    configurations = inputs.document.get("appConfigurations")
    if not configurations:
        return {}
    changes = [
        {
            "namespace": str(item.get("namespace")),
            "key": str(item.get("key")),
            "value": item.get("value"),
        }
        for item in sorted(configurations, key=lambda entry: str(entry.get("key")))
        if isinstance(item, dict)
    ]
    if not changes:
        return {}
    return {files.DATASTORE_FILE: files.json_bytes({"dataStores": changes})}


def _environment_diff(inputs: CompileInputs, output: metadata.CompileOutput) -> DiffReport | None:
    """Current state of this environment vs this compile (BP-CMP-029)."""
    if inputs.discovery is None:
        return None
    from factory_blueprint.diff import object_diff

    discovered: dict[str, dict[str, Any]] = {}
    for item in inputs.discovery.objects:
        content = dict(item.content) if isinstance(item.content, dict) else {}
        discovered[item.uid] = {
            **content,
            "dhis2Type": item.dhis2_type,
            "uid": item.uid,
            "code": item.code,
            "name": item.name,
        }
    compiled: dict[str, dict[str, Any]] = {}
    for business_key, payload_object in output.objects.items():
        # the two sides of this comparison meet on the object's identity *in this
        # environment*: a payload carries no ``id`` (FR-CMP-006), so the binding the
        # compile resolved is what names it, and keying by businessKey instead would
        # show every environment object as removed and every compiled one as new
        uid = str(payload_object.get("id") or inputs.bindings.get(business_key) or business_key)
        compiled[uid] = payload_object
    reference_only = {
        str(item.get("uid"))
        for item in output.unmanaged_references
        if isinstance(item.get("uid"), str)
    }
    return object_diff.environment_diff(
        inputs.project_id,
        inputs.blueprint_version,
        inputs.environment_key or "",
        discovered,
        compiled,
        reference_only=reference_only,
        profile=view_of(inputs.profile),
        profile_hash=inputs.profile_ref.artifact_hash if inputs.profile_ref else None,
        differences=output.profile_differences,
    )


def environment_manifest(
    inputs: CompileInputs,
    build_record: dict[str, Any],
    product: EnvironmentProduct,
    *,
    binding: bindings_module.BindingOutcome | None = None,
) -> dict[str, Any]:
    from factory_foundation.api import content_hash

    del binding
    return top_level_manifest(
        inputs,
        build_record,
        {name: content_hash(data) for name, data in sorted(product.files.items())},
        artifact_kind="METADATA_PACKAGE",
    )
