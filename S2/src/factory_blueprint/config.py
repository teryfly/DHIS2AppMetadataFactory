"""S2 configuration: which type tiers and compilers are enabled.

BP-TD-002: Tier 1 ships with M2, Tier 2 and 3 with M5, and enabling them is a
configuration decision, not a code change.  Unknown types are never silently
ignored (FR-COV-002).
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace

from factory_blueprint.enums import Tier

#: compiler identifiers; DataStore is the one a later milestone delivers (M5)
COMPILER_METADATA = "metadata"
COMPILER_OPENAPI = "openapi"
COMPILER_UI_SPEC = "uiSpec"
COMPILER_FIXTURE = "fixture"
COMPILER_PACKAGE = "package"
COMPILER_DATASTORE = "dataStore"


@dataclass(frozen=True)
class BlueprintSettings:
    enabled_tiers: frozenset[Tier] = frozenset({Tier.TIER_1})
    # M3 delivers the OpenAPI, UI Spec, Fixture and Package compilers (SRS 12 里程碑分工);
    # enabling one of them is still a settings change, so a deployment can hold a build back
    enabled_compilers: frozenset[str] = field(
        default_factory=lambda: frozenset(
            {COMPILER_METADATA, COMPILER_OPENAPI, COMPILER_UI_SPEC, COMPILER_FIXTURE, COMPILER_PACKAGE}
        )
    )
    #: descriptor and template set version shipped inside this package (BP-TD-008)
    descriptor_version: str = "1"
    template_version: str = "1"
    # 0.2.0: the dependency graph a compile writes gained its section, isolated-node and
    # unresolved-reference fields (BP-TD-009), so a build record must name the compiler
    # that produced the bytes it is being compared against (BP-CMP-004, BP-NFR-08)
    compiler_version: str = "0.2.0"
    openapi_version: str = "3.1.0"
    package_template_version: str = "0.1.0"
    max_operations_per_patch: int = 2000
    #: BP-REG-008a defaults, overridable per project through generationPolicy
    uid_batch_size: int = 200
    uid_low_water_mark: int = 50


_DEFAULT = BlueprintSettings()
_current: BlueprintSettings = _DEFAULT


def settings() -> BlueprintSettings:
    return _current


def configure(**changes: object) -> BlueprintSettings:
    """Replace configuration values; returns the new settings."""
    global _current
    _current = replace(_current, **changes)  # type: ignore[arg-type]
    return _current


def reset() -> BlueprintSettings:
    global _current
    _current = _DEFAULT
    return _current
