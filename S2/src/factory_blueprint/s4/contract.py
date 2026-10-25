"""S2's reading contract for S4's Capability Profile and Discovery Result.

S4 owns and publishes these formats (BP-ASM-03); S2 only declares the subset it
consumes, tolerates unknown extra fields, and reports a BLOCKING problem instead of
guessing when a field it needs is missing (P-10: capability over assumption).
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field

TOLERANT = ConfigDict(extra="allow", frozen=True)


class ResourceEntry(BaseModel):
    model_config = TOLERANT

    logical_name: str = Field(alias="logicalName")
    path: str
    method: str = "GET"
    parameters: dict[str, str] = Field(default_factory=dict)
    paging: str | None = None
    tracker_api: bool = Field(default=False, alias="trackerApi")


class ObjectSchema(BaseModel):
    """The version's metadata schema for one object type."""

    model_config = TOLERANT

    dhis2_type: str = Field(alias="dhis2Type")
    required: list[str] = Field(default_factory=list)
    properties: dict[str, Any] = Field(default_factory=dict)
    forbidden: list[str] = Field(default_factory=list)
    controlable_fields: list[str] = Field(default_factory=list, alias="controlableFields")


class DeprecatedItem(BaseModel):
    model_config = TOLERANT

    kind: str
    name: str
    removed_in: str | None = Field(default=None, alias="removedIn")


class CapabilityProfile(BaseModel):
    """Version Profile plus the target instance's probed reality (overall SRS 14.3)."""

    model_config = TOLERANT

    environment_key: str | None = Field(default=None, alias="environmentKey")
    version: str
    profile_version: str = Field(default="Dhis241Profile", alias="profileVersion")
    expression_grammar_version: str | None = Field(
        default=None, alias="expressionGrammarVersion"
    )
    resources: list[ResourceEntry] = Field(default_factory=list)
    schemas: list[ObjectSchema] = Field(default_factory=list)
    import_parameters: dict[str, Any] = Field(default_factory=dict, alias="importParameters")
    deprecated: list[DeprecatedItem] = Field(default_factory=list)
    removed: list[DeprecatedItem] = Field(default_factory=list)
    descriptor_overrides: list[dict[str, Any]] = Field(
        default_factory=list, alias="descriptorOverrides"
    )
    #: properties DHIS2 rewrites on its own, so a change in one of them is not a change
    #: to anything the project manages (DX-DSC-007).  S4 excludes them from the field
    #: level of its own Diff; reading them here is what lets S2's agree (AC-CAP-01).
    volatile_fields: list[str] = Field(default_factory=list, alias="volatileFields")
    validation_rules: dict[str, Any] = Field(default_factory=dict, alias="validationRules")
    translation_format: str = Field(default="translations", alias="translationFormat")
    sharing_format: str = Field(default="sharing", alias="sharingFormat")
    #: the segment between an instance's root and a resource path.  The OpenAPI
    #: compiler builds ``servers`` from it rather than hard-coding ``/api``, because a
    #: new version that moved it must not require a compiler change (BP-NFR-06).
    api_prefix: str = Field(default="api", alias="apiPrefix")

    def resource(self, logical_name: str) -> ResourceEntry | None:
        for entry in self.resources:
            if entry.logical_name == logical_name:
                return entry
        return None

    def object_schema(self, dhis2_type: str) -> ObjectSchema | None:
        for schema in self.schemas:
            if schema.dhis2_type == dhis2_type:
                return schema
        return None

    def controlable_fields(self, dhis2_type: str) -> list[str]:
        schema = self.object_schema(dhis2_type)
        return list(schema.controlable_fields) if schema else []

    def forbidden_fields(self, dhis2_type: str) -> list[str]:
        """Properties this version will not take in a payload, named as S4 names them."""
        schema = self.object_schema(dhis2_type)
        return list(schema.forbidden) if schema else []

    def is_removed(self, kind: str, name: str) -> bool:
        return any(item.kind == kind and item.name == name for item in self.removed)

    def is_deprecated(self, kind: str, name: str) -> bool:
        return any(item.kind == kind and item.name == name for item in self.deprecated)


class DiscoveredObject(BaseModel):
    """One existing object of the target environment (overall SRS 9.2 FR-DSC-001)."""

    model_config = TOLERANT

    dhis2_type: str = Field(alias="dhis2Type")
    uid: str
    code: str | None = None
    name: str | None = None
    last_updated: str | None = Field(default=None, alias="lastUpdated")
    content_hash: str | None = Field(default=None, alias="contentHash")
    #: required to compile UPDATE objects; absent means the object content was not
    #: discovered, which the compiler reports as BLOCKING rather than inventing
    content: dict[str, Any] | None = None
    references: dict[str, Any] = Field(default_factory=dict)


class DiscoveryResult(BaseModel):
    model_config = TOLERANT

    environment_key: str = Field(alias="environmentKey")
    discovered_at: str | None = Field(default=None, alias="discoveredAt")
    objects: list[DiscoveredObject] = Field(default_factory=list)
    defaults: dict[str, str] = Field(default_factory=dict)

    def find_by_uid(self, dhis2_type: str, uid: str) -> DiscoveredObject | None:
        for item in self.objects:
            if item.uid == uid and (item.dhis2_type == dhis2_type or not dhis2_type):
                return item
        return None

    def find_by_code(self, dhis2_type: str, code: str) -> list[DiscoveredObject]:
        return [
            item
            for item in self.objects
            if item.dhis2_type == dhis2_type and item.code == code
        ]

    def default_category_combo_uid(self) -> str | None:
        return self.defaults.get("categoryCombo")


def profile_from_json(data: dict[str, Any]) -> CapabilityProfile:
    return CapabilityProfile.model_validate(data)


def discovery_from_json(data: dict[str, Any]) -> DiscoveryResult:
    return DiscoveryResult.model_validate(data)
