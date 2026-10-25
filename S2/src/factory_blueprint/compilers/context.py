"""The closed input set of one compilation (BP-CMP-001).

A compiler may only look at what is in here: the approved Blueprint document, the
Capability Profile and Discovery Result it was given, the environment bindings, and
the build context (time and seed come from the caller, never from the clock).  The
type carries no clock, no randomness and no network handle, which is what makes
"the compiler cannot be nondeterministic" a structural property rather than a
convention.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from factory_blueprint.config import BlueprintSettings, settings
from factory_blueprint.dto.build import BuildContext, BuildInput
from factory_blueprint.s4.contract import CapabilityProfile, DiscoveryResult


@dataclass(frozen=True)
class InputRef:
    """An external input referenced by type and hash, never by mutable ID only.

    Artifact IDs change when a project is imported elsewhere, the hashes must not;
    putting a hash into the product keeps recompilation byte-identical
    (BP-EXP-002, AC-DATA-01).
    """

    role: str
    artifact_id: str
    type: str
    artifact_hash: str

    def as_json(self) -> dict[str, Any]:
        """How a product names this input: type and hash, never the mutable ID.

        An import into another deployment rewrites artifact IDs (BP-EXP-002), so an ID
        inside a product would change its bytes and break recompilation (AC-DATA-01).
        The ID stays in the build record, which is database state, not product bytes.
        """
        return {"role": self.role, "type": self.type, "artifactHash": self.artifact_hash}


@dataclass(frozen=True)
class CompileInputs:
    project_id: str
    blueprint_version: str
    blueprint_hash: str
    document: dict[str, Any]
    build_context: BuildContext
    blueprint_artifact_id: str
    profile: CapabilityProfile | None = None
    profile_ref: InputRef | None = None
    discovery: DiscoveryResult | None = None
    discovery_ref: InputRef | None = None
    environment_key: str | None = None
    #: businessKey -> UID in this environment (empty for a baseline compile)
    bindings: dict[str, str] = field(default_factory=dict)
    #: entries whose payload must be merged over the environment's current object
    observed: dict[str, dict[str, Any]] = field(default_factory=dict)
    design_environment: str | None = None
    change_request_id: str | None = None
    deletion_candidates: list[str] = field(default_factory=list)
    #: this environment's Managed Scope rows (BP-CMP-027a); the manifest must state it,
    #: because S4 builds Snapshots and drift checks from the package, never from S2
    managed_scope: list[dict[str, Any]] = field(default_factory=list)

    @property
    def is_environment_bound(self) -> bool:
        return self.environment_key is not None

    @property
    def delivery_mode(self) -> str:
        return str((self.document.get("project") or {}).get("deliveryMode") or "B")

    @property
    def project_type(self) -> str:
        return str((self.document.get("project") or {}).get("projectType") or "BROWNFIELD")

    @property
    def default_language(self) -> str:
        return str((self.document.get("project") or {}).get("defaultLanguage") or "en")

    @property
    def translation_languages(self) -> list[str]:
        values = (self.document.get("project") or {}).get("translationLanguages") or []
        return sorted({str(item) for item in values})

    @property
    def generation_policy(self) -> dict[str, Any]:
        policy = self.document.get("generationPolicy") or {}
        return dict(policy) if isinstance(policy, dict) else {}

    @property
    def dhis2_version(self) -> str:
        return self.profile.version if self.profile else ""

    def settings(self) -> BlueprintSettings:
        return settings()

    def compatibility(self) -> dict[str, Any]:
        """The compatibility dimensions of a build record (BP-NFR-08, 22.8)."""
        config = settings()
        return {
            "blueprintSchemaVersion": self.document.get("blueprintSchemaVersion"),
            "blueprintVersion": self.blueprint_version,
            "blueprintHash": self.blueprint_hash,
            "compilerVersion": config.compiler_version,
            "templateVersion": config.template_version,
            "descriptorVersion": config.descriptor_version,
            "profileVersion": self.profile.profile_version if self.profile else None,
            "dhis2Version": self.dhis2_version,
            "openApiVersion": config.openapi_version,
            "packageTemplateVersion": config.package_template_version,
        }

    def input_refs(self) -> list[BuildInput]:
        refs = [
            BuildInput(
                role="blueprint",
                artifact_id=self.blueprint_artifact_id,
                type="BLUEPRINT",
                artifact_hash=self.blueprint_hash,
            )
        ]
        for ref in (self.profile_ref, self.discovery_ref):
            if ref is not None:
                refs.append(
                    BuildInput(
                        role=ref.role,
                        artifact_id=ref.artifact_id,
                        type=ref.type,
                        artifact_hash=ref.artifact_hash,
                    )
                )
        return refs
