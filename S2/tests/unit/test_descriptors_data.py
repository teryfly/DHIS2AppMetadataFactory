"""Data-level tests for the shipped Tier 1 type descriptors and compile templates.

They assert the *data* is coherent with overall SRS 6.1–6.5, BP-TD-001/002/004/008
and FR-CMP-006: exactly one descriptor and one template per Tier 1 type, the JSON
key shape ``TypeDescriptor.to_json()`` produces, resolvable field-level dependencies
(the dependency graph of BP-TD-006), controlable fields that never smuggle in a
structural change, and templates whose placeholders are whole string values.

Pure unit tests: they read the JSON files directly — no database, no registry
loader, no compiler.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

import pytest

from factory_blueprint.descriptors.model import Dependency, Implicit, TypeDescriptor
from factory_blueprint.enums import Tier

DESCRIPTOR_ROOT = Path(__file__).resolve().parents[2] / "src" / "factory_blueprint" / "descriptors"
DESCRIPTOR_DIR = DESCRIPTOR_ROOT / "descriptor_files" / "1"
TEMPLATE_DIR = DESCRIPTOR_ROOT / "templates" / "1"

#: overall SRS 6.1 Tier 1, as descriptor file names.  Sharing is not an object of its
#: own (FR-COV-006): it is the implicit rule SHARING_FROM_ACCESS_MODEL.
TIER_1_TYPES: tuple[str, ...] = (
    "trackedEntityType",
    "trackedEntityAttribute",
    "program",
    "programStage",
    "programStageDataElement",
    "programTrackedEntityAttribute",
    "programSection",
    "programStageSection",
    "dataElement",
    "optionSet",
    "option",
    "category",
    "categoryOption",
    "categoryCombo",
    "programRule",
    "programRuleVariable",
    "programRuleAction",
    "programIndicator",
    "validationRule",
    "relationshipType",
    "attribute",
    "organisationUnit",
    "userRole",
    "userGroup",
)

#: the JSON shape ``TypeDescriptor.to_json()`` writes and the registry loader reads.
JSON_KEYS: tuple[str, ...] = (
    "dhis2Type",
    "resource",
    "payloadKey",
    "tier",
    "versionRange",
    "requiredFields",
    "dependencies",
    "controlableFields",
    "translatableFields",
    "forbiddenFields",
    "implicit",
    "template",
    "sortKey",
    "rules",
    "embeddedInParent",
    "descriptorVersion",
)

DEPENDENCY_KEYS = frozenset({"field", "targetType", "required", "itemField", "wrapper"})

#: FR-CMP-006 / SRS 12.4: DHIS2 owns these, a payload must never carry them.
SYSTEM_OWNED_FIELDS: tuple[str, ...] = (
    "href",
    "id",
    "created",
    "lastUpdated",
    "access",
    "displayUrl",
    "externalAccess",
)
#: join objects are replaced with their parent, so only these three are ever echoed back
EMBEDDED_FORBIDDEN: tuple[str, ...] = ("href", "id", "lastUpdated")

DISPLAY_FIELDS = frozenset({"name", "shortName", "description", "formName"})

#: renaming the list/label fields below is a Minor change; anything else that is not a
#: display field must not be authored in an UPDATE entry.
SAFE_NON_DISPLAY_CONTROLABLE = frozenset(
    {
        "minAttributesRequiredSearching",
        "displayInListNoProgram",
        "executionDateLabel",
        "incidentDateLabel",
        "displayIncidentDate",
        "displayOptions",
        "importance",
        "sortOrder",
        "compulsory",
        "displayInReports",
        "mandatory",
        "searchable",
        "displayInList",
    }
)

#: changing any of these is a Major structural change, so no UPDATE entry may own one
#: (FR-REG-001/003).  Join objects *reference* ``dataElement`` and ``categoryCombo`` as
#: their identity, but never own them as controlled fields, so the Tier 1 set needs no
#: exception and JOIN_EXCEPTIONS stays deliberately empty.
STRUCTURAL_BLOCKLIST = frozenset(
    {
        "valueType",
        "domainType",
        "aggregationType",
        "programType",
        "optionSet",
        "categoryCombo",
        "trackedEntity",
        "dataElement",
        "formType",
    }
)
JOIN_EXCEPTIONS: frozenset[tuple[str, str]] = frozenset()
#: structural names that only a later Tier uses (kept as a forward guard, not a gap)
FORWARD_ONLY_STRUCTURAL = frozenset({"formType"})

#: objects a Tier 1 type may reference that are resolved from Discovery instead of
#: having their own descriptor (validation rule operands; FR-COV-003's default combo is
#: a categoryCombo, which does have one).
REFERENCE_ONLY_TARGETS = frozenset({"categoryOptionCombo"})

#: DHIS2 stores these join objects in their parent's array property, not in a
#: top-level metadata collection.
EMBEDDED_IN_PARENT: dict[str, tuple[str, str]] = {
    "programStageDataElement": ("programStage", "programStageDataElements"),
    "programTrackedEntityAttribute": ("program", "programTrackedEntityAttributes"),
    "programSection": ("program", "programSections"),
    "programStageSection": ("programStage", "programStageSections"),
    "programRuleAction": ("programRule", "programRuleActions"),
    "option": ("optionSet", "options"),
}

#: join objects that have no display fields of their own
NAMELESS_JOIN_TYPES = frozenset(
    {"programStageDataElement", "programTrackedEntityAttribute", "programRuleAction"}
)

#: types the factory never creates: their template is DHIS2's reference form
REFERENCE_SKELETON_TYPES = frozenset({"organisationUnit"})

#: DHIS2's irregular collection-key plural, so the resource/payloadKey rule stays honest
IRREGULAR_PLURALS = {"category": "categories"}

CAMEL_CASE = re.compile(r"^[a-z][A-Za-z0-9]*$")
UPPER_SNAKE = re.compile(r"^[A-Z][A-Z0-9_]*$")
PLACEHOLDER_LEAF = re.compile(r"^\$\{[A-Za-z][A-Za-z0-9_]*\}$")
PLACEHOLDER_ANY = re.compile(r"\$\{[^}]*\}")


# --------------------------------------------------------------------------- helpers


def descriptor_data(dhis2_type: str) -> dict[str, Any]:
    path = DESCRIPTOR_DIR / f"{dhis2_type}.json"
    document: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
    return document


def template_data(dhis2_type: str) -> dict[str, Any]:
    path = TEMPLATE_DIR / f"{dhis2_type}.template.json"
    document: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
    return document


def all_descriptors() -> dict[str, dict[str, Any]]:
    return {name: descriptor_data(name) for name in TIER_1_TYPES}


def dependencies(data: dict[str, Any]) -> list[dict[str, Any]]:
    items = data["dependencies"]
    assert isinstance(items, list)
    return [item for item in items if isinstance(item, dict)]


def dependency_fields(data: dict[str, Any]) -> set[str]:
    return {str(item["field"]) for item in dependencies(data)}


def target_types(data: dict[str, Any]) -> set[str]:
    return {str(item["targetType"]) for item in dependencies(data)}


def iter_strings(node: Any) -> list[str]:
    """Every string in a JSON tree, keys excluded."""
    found: list[str] = []
    if isinstance(node, dict):
        for value in node.values():
            found.extend(iter_strings(value))
    elif isinstance(node, list):
        for value in node:
            found.extend(iter_strings(value))
    elif isinstance(node, str):
        found.append(node)
    return found


def placeholder_names(node: Any) -> set[str]:
    return {found[2:-1] for found in PLACEHOLDER_ANY.findall(" ".join(iter_strings(node)))}


# ---------------------------------------------------------------------------- (a) shape


def test_exactly_the_tier_1_descriptor_files_exist() -> None:
    assert sorted(p.stem for p in DESCRIPTOR_DIR.glob("*.json")) == sorted(TIER_1_TYPES)


def test_exactly_the_tier_1_template_files_exist() -> None:
    stems = [p.stem.removesuffix(".template") for p in TEMPLATE_DIR.glob("*.json")]
    assert sorted(stems) == sorted(TIER_1_TYPES)


@pytest.mark.parametrize("dhis2_type", TIER_1_TYPES)
def test_descriptor_has_every_expected_key_with_a_valid_type(dhis2_type: str) -> None:
    data = descriptor_data(dhis2_type)

    assert set(data) == set(JSON_KEYS), f"{dhis2_type}: missing or extra JSON keys"
    assert data["dhis2Type"] == dhis2_type
    assert isinstance(data["resource"], str)
    assert data["resource"].startswith("metadata.")
    assert CAMEL_CASE.match(data["resource"].removeprefix("metadata."))
    assert CAMEL_CASE.match(data["payloadKey"])
    for key in (
        "requiredFields",
        "controlableFields",
        "translatableFields",
        "forbiddenFields",
        "implicit",
        "rules",
    ):
        assert isinstance(data[key], list), f"{dhis2_type}.{key} must be a list"
        assert all(isinstance(name, str) and name for name in data[key])
    assert isinstance(data["dependencies"], list)
    assert isinstance(data["versionRange"], list) and all(
        isinstance(part, str) for part in data["versionRange"]
    )
    assert isinstance(data["template"], str)
    assert isinstance(data["sortKey"], str)
    assert isinstance(data["descriptorVersion"], str)
    assert data["embeddedInParent"] is None or isinstance(data["embeddedInParent"], str)
    assert isinstance(data["tier"], (int, str))


@pytest.mark.parametrize("dhis2_type", TIER_1_TYPES)
def test_descriptor_round_trips_through_the_frozen_data_model(dhis2_type: str) -> None:
    """``descriptors.model.TypeDescriptor`` accepts the file as-is and reproduces it."""
    data = descriptor_data(dhis2_type)

    restored = TypeDescriptor(
        dhis2_type=str(data["dhis2Type"]),
        resource=str(data["resource"]),
        payload_key=str(data["payloadKey"]),
        tier=Tier(str(data["tier"])),
        version_range=(str(data["versionRange"][0]), str(data["versionRange"][1])),
        required_fields=tuple(data["requiredFields"]),
        dependencies=tuple(
            Dependency(
                field=str(item["field"]),
                target_type=str(item["targetType"]),
                required=bool(item["required"]),
                item_field=item.get("itemField"),
                wrapper=item.get("wrapper"),
            )
            for item in dependencies(data)
        ),
        controlable_fields=tuple(data["controlableFields"]),
        translatable_fields=tuple(data["translatableFields"]),
        forbidden_fields=tuple(data["forbiddenFields"]),
        implicit=tuple(Implicit(name) for name in data["implicit"]),
        template=str(data["template"]),
        sort_key=str(data["sortKey"]),
        rules=tuple(data["rules"]),
        embedded_in_parent=data["embeddedInParent"],
        descriptor_version=str(data["descriptorVersion"]),
    )

    produced = restored.to_json()
    # Tier is a str-enum, so to_json() writes "1" while the data files write 1.
    produced["tier"] = int(produced["tier"])
    assert produced == data


@pytest.mark.parametrize("dhis2_type", TIER_1_TYPES)
def test_descriptor_declares_tier_one_range_and_ordering(dhis2_type: str) -> None:
    """BP-TD-002: Tier 1 ships in M2, v2.41 is the compatibility baseline."""
    data = descriptor_data(dhis2_type)

    assert int(data["tier"]) == int(str(Tier.TIER_1.value))
    assert data["versionRange"] == ["2.41", "2.43"]
    assert data["sortKey"] == "businessKey"
    assert data["descriptorVersion"] == "1"
    assert data["template"] == f"{dhis2_type}.template.json"


@pytest.mark.parametrize("dhis2_type", TIER_1_TYPES)
def test_field_names_are_camel_case_and_unique(dhis2_type: str) -> None:
    data = descriptor_data(dhis2_type)

    for key in ("requiredFields", "controlableFields", "translatableFields", "forbiddenFields"):
        names = data[key]
        assert len(names) == len(set(names)), f"{dhis2_type}.{key} has duplicates"
        for name in names:
            assert CAMEL_CASE.match(name), f"{dhis2_type}.{key}: {name!r} is not camelCase"
    assert len(data["rules"]) == len(set(data["rules"])), f"{dhis2_type}: duplicate rules"
    assert len(data["implicit"]) == len(set(data["implicit"])), f"{dhis2_type}: duplicate implicit"
    for rule in data["rules"]:
        assert UPPER_SNAKE.match(rule), f"{dhis2_type}: rule {rule!r} is not UPPER_SNAKE"


@pytest.mark.parametrize("dhis2_type", TIER_1_TYPES)
def test_resource_and_payload_key_follow_the_parent_for_join_objects(dhis2_type: str) -> None:
    data = descriptor_data(dhis2_type)
    segment = data["resource"].removeprefix("metadata.")
    expected_key = IRREGULAR_PLURALS.get(segment, f"{segment}s")

    if dhis2_type in EMBEDDED_IN_PARENT:
        parent, array_key = EMBEDDED_IN_PARENT[dhis2_type]
        assert data["embeddedInParent"] == parent
        # join objects have no collection of their own: they ride in the parent payload
        assert data["resource"] == descriptor_data(parent)["resource"]
        assert data["payloadKey"] == array_key
    else:
        assert data["embeddedInParent"] is None
        assert data["payloadKey"] == expected_key


# ---------------------------------------------------------------------------- (b) graph


@pytest.mark.parametrize("dhis2_type", TIER_1_TYPES)
def test_every_dependency_target_is_registered_or_reference_only(dhis2_type: str) -> None:
    """BP-TD-006: every edge points at a type the compiler can resolve."""
    data = descriptor_data(dhis2_type)

    for item in dependencies(data):
        target = str(item["targetType"])
        assert target in TIER_1_TYPES or target in REFERENCE_ONLY_TARGETS, (
            f"{dhis2_type} depends on unregistered type {target!r}"
        )
        assert target != dhis2_type, f"{dhis2_type} depends on itself"
        assert set(item) <= DEPENDENCY_KEYS, f"{dhis2_type}: {item} has unknown keys"
        assert "field" in item and "targetType" in item
        assert isinstance(item.get("required", True), bool)
        assert CAMEL_CASE.match(str(item["field"])), f"{dhis2_type}: field {item['field']!r}"

    fields = [str(item["field"]) for item in dependencies(data)]
    assert len(fields) == len(set(fields)), f"{dhis2_type}: duplicate dependency fields"


@pytest.mark.parametrize("dhis2_type", TIER_1_TYPES)
def test_nested_and_parent_dependencies_are_well_formed(dhis2_type: str) -> None:
    data = descriptor_data(dhis2_type)

    for item in dependencies(data):
        wrapper, item_field = item.get("wrapper"), item.get("itemField")
        assert not (item_field and not wrapper), f"{dhis2_type}: {item} lacks a wrapper"
        if wrapper and not item_field:
            # a join object pointing at its containing parent
            assert data["embeddedInParent"] == str(item["targetType"]), dhis2_type
            assert wrapper == data["payloadKey"], dhis2_type
        elif wrapper and item_field:
            assert str(item["field"]) == wrapper, dhis2_type
            assert CAMEL_CASE.match(str(item_field)), dhis2_type


def test_required_dependencies_are_declared_where_dhis2_requires_them() -> None:
    """The reference-critical fields of FR-COV-003/004 and the join objects."""
    expected = {
        "dataElement": {"categoryCombo"},
        "program": {"categoryCombo"},
        "programStage": {"program"},
        "programStageDataElement": {"dataElement", "programStage"},
        "programTrackedEntityAttribute": {"attribute", "program"},
        "programSection": {"program"},
        "programStageSection": {"programStage"},
        "programRule": {"program"},
        "programRuleVariable": {"program"},
        "programRuleAction": {"programRule"},
        "programIndicator": {"program"},
        "option": {"optionSet"},
    }

    for dhis2_type, required in expected.items():
        mandatory = {
            str(item["field"])
            for item in dependencies(descriptor_data(dhis2_type))
            if item.get("required") is True
        }
        assert mandatory == required, f"{dhis2_type}: required deps are {sorted(mandatory)}"


# ----------------------------------------------------------------- (c) controlable fields


@pytest.mark.parametrize("dhis2_type", TIER_1_TYPES)
def test_controlable_fields_never_expose_structural_changes(dhis2_type: str) -> None:
    """FR-REG-003: an UPDATE entry may only own fields the descriptor allows."""
    allowed = {field for (name, field) in JOIN_EXCEPTIONS if name == dhis2_type}
    blocked = STRUCTURAL_BLOCKLIST - allowed
    owned = set(descriptor_data(dhis2_type)["controlableFields"])

    assert not owned & blocked, (
        f"{dhis2_type} lets an UPDATE own structural field(s) {sorted(owned & blocked)}"
    )


@pytest.mark.parametrize("dhis2_type", TIER_1_TYPES)
def test_controlable_fields_are_display_or_listed_safe_fields(dhis2_type: str) -> None:
    owned = set(descriptor_data(dhis2_type)["controlableFields"])

    assert owned <= DISPLAY_FIELDS | SAFE_NON_DISPLAY_CONTROLABLE, dhis2_type
    if dhis2_type in NAMELESS_JOIN_TYPES:
        assert not owned & DISPLAY_FIELDS, f"{dhis2_type} has no display fields of its own"
    else:
        assert "name" in owned, f"{dhis2_type}: renaming must be controllable"


@pytest.mark.parametrize("dhis2_type", TIER_1_TYPES)
def test_translatable_fields_are_display_fields(dhis2_type: str) -> None:
    """FR-COV-009: only showable fields carry translations."""
    translated = set(descriptor_data(dhis2_type)["translatableFields"])

    assert translated <= DISPLAY_FIELDS, dhis2_type
    if dhis2_type in REFERENCE_SKELETON_TYPES:
        assert translated == set(), dhis2_type


def test_structural_blocklist_matches_the_data_it_protects() -> None:
    """The blocklist is not decoration: every Tier 1 name really occurs in the data."""
    used: set[str] = set()
    for data in all_descriptors().values():
        used |= set(data["requiredFields"])
        used |= dependency_fields(data)
        used |= {str(item["itemField"]) for item in dependencies(data) if "itemField" in item}
        used |= set(template_data(str(data["dhis2Type"])))
        used |= set(data["controlableFields"])

    assert used >= STRUCTURAL_BLOCKLIST - FORWARD_ONLY_STRUCTURAL
    assert not FORWARD_ONLY_STRUCTURAL & used, "formType is a Tier 2 (DataSet) property"


# ---------------------------------------------------------------------------- (d) templates


@pytest.mark.parametrize("dhis2_type", TIER_1_TYPES)
def test_template_exists_and_placeholders_are_whole_strings(dhis2_type: str) -> None:
    """A substituted value is always the literal ``"${...}"`` and nothing around it."""
    template = template_data(dhis2_type)

    names = placeholder_names(template)
    assert names, f"{dhis2_type}: template has no placeholder to fill"
    for value in iter_strings(template):
        if "${" in value:
            assert PLACEHOLDER_LEAF.match(value), (
                f"{dhis2_type}: {value!r} must be a standalone placeholder string"
            )
    for name in names:
        assert re.match(r"^[a-z][A-Za-z0-9_]*$", name), f"{dhis2_type}: {name!r} is odd"


@pytest.mark.parametrize("dhis2_type", TIER_1_TYPES)
def test_template_is_the_placeholder_skeleton_for_the_descriptor(dhis2_type: str) -> None:
    data = descriptor_data(dhis2_type)
    template = template_data(dhis2_type)

    for name in data["requiredFields"]:
        assert name in template, f"{dhis2_type}: required field {name!r} has no template slot"

    for item in dependencies(data):
        if item.get("wrapper") and not item.get("itemField"):
            continue  # the parent's array carries the join object, not its own skeleton
        field = str(item["field"])
        assert field in template, f"{dhis2_type}: dependency {field!r} has no template slot"
        assert isinstance(template[field], (str, dict)), f"{dhis2_type}: {field!r}"

    assert ("translations" in template) == bool(data["translatableFields"]), dhis2_type
    if dhis2_type in REFERENCE_SKELETON_TYPES:
        assert template == {"id": "${uid}"}, dhis2_type
        return
    assert ("sharing" in template) == ("SHARING_FROM_ACCESS_MODEL" in data["implicit"]), dhis2_type


@pytest.mark.parametrize("dhis2_type", TIER_1_TYPES)
def test_template_keys_are_dhis2_properties_not_system_fields(dhis2_type: str) -> None:
    data = descriptor_data(dhis2_type)
    forbidden = set(data["forbiddenFields"])

    for key in template_data(dhis2_type):
        assert CAMEL_CASE.match(key), f"{dhis2_type}: template key {key!r}"
        if dhis2_type in REFERENCE_SKELETON_TYPES:
            # a reference-only type is emitted as DHIS2's reference form {"id": uid}:
            # that id is the discovered UID of an existing object, never an authored
            # system property of a payload the factory creates.
            assert key == "id", f"{dhis2_type}: reference skeleton must stay a reference"
            continue
        assert key not in forbidden, f"{dhis2_type}: template carries forbidden {key!r}"


#: the only deliberate differences between a template slot and its placeholder name:
#: DHIS2's program property is ``trackedEntity`` while the referenced object is a
#: trackedEntityType, and a reference-only skeleton carries a resolved UID.
PLACEHOLDER_ALIASES = frozenset(
    {("program", "trackedEntity", "trackedEntityType"), ("organisationUnit", "id", "uid")}
)


@pytest.mark.parametrize("dhis2_type", TIER_1_TYPES)
def test_every_placeholder_is_named_after_the_property_it_fills(dhis2_type: str) -> None:
    aliases = {
        (key, name) for (type_name, key, name) in PLACEHOLDER_ALIASES if type_name == dhis2_type
    }

    for key, value in template_data(dhis2_type).items():
        filled = iter_strings(value)
        assert filled, f"{dhis2_type}: slot {key!r} has no value"
        for text in filled:
            name = text[2:-1]
            assert name == key or (key, name) in aliases, (
                f"{dhis2_type}: slot {key!r} is filled by {name!r}"
            )


# --------------------------------------------------------------------- (e) forbidden fields


@pytest.mark.parametrize("dhis2_type", TIER_1_TYPES)
def test_forbidden_fields_cover_the_system_owned_properties(dhis2_type: str) -> None:
    data = descriptor_data(dhis2_type)
    forbidden = set(data["forbiddenFields"])

    assert len(data["forbiddenFields"]) == len(forbidden)
    assert all(CAMEL_CASE.match(name) for name in forbidden)

    if data["embeddedInParent"] is None:
        assert set(SYSTEM_OWNED_FIELDS[:6]) <= forbidden, (
            f"{dhis2_type}: a top-level payload must not carry {SYSTEM_OWNED_FIELDS[:6]}"
        )
        assert "externalAccess" in forbidden, dhis2_type
    else:
        assert set(EMBEDDED_FORBIDDEN) <= forbidden, dhis2_type
        assert forbidden <= set(EMBEDDED_FORBIDDEN) | {"created", "access"}, dhis2_type


# --------------------------------------------------------------- 6.2–6.4 and 6.3 specifics


@pytest.mark.parametrize("dhis2_type", TIER_1_TYPES)
def test_implicit_rules_are_the_named_compiler_rules(dhis2_type: str) -> None:
    """BP-TD-004: implicit dependencies are declared by name, never hard-coded."""
    assert set(descriptor_data(dhis2_type)["implicit"]) <= {rule.value for rule in Implicit}, (
        dhis2_type
    )


@pytest.mark.parametrize("dhis2_type", TIER_1_TYPES)
def test_sharing_is_compiled_from_the_access_model_for_managed_objects(dhis2_type: str) -> None:
    """FR-COV-006: every top-level object gets Sharing from accessModel."""
    data = descriptor_data(dhis2_type)
    declared = "SHARING_FROM_ACCESS_MODEL" in data["implicit"]

    # join objects carry no sharing of their own; the parent's access decides
    assert declared == (data["embeddedInParent"] is None), dhis2_type


def test_data_element_defaults_to_the_environment_category_combo() -> None:
    """FR-COV-003: the field is never omitted; the default combo is a REFERENCE."""
    data = descriptor_data("dataElement")

    assert "categoryCombo" in data["requiredFields"]
    assert {"field": "categoryCombo", "targetType": "categoryCombo", "required": True} in (
        dependencies(data)
    )
    assert "DEFAULT_CATEGORY_COMBO" in data["implicit"]
    assert "DE_VALUE_TYPE_MATCHES_DOMAIN" in data["rules"]


def test_program_declares_tracked_entity_or_event_and_its_children() -> None:
    """FR-COV-004."""
    data = descriptor_data("program")

    assert "PROGRAM_TRACKED_ENTITY_OR_EVENT" in data["implicit"]
    assert data["requiredFields"] == ["name", "shortName", "programType", "categoryCombo"]
    assert "PROGRAM_ACCESS_LEVEL_DECLARED" in data["rules"]
    assert {"field": "trackedEntity", "targetType": "trackedEntityType", "required": False} in (
        dependencies(data)
    )
    stages = next(item for item in dependencies(data) if item["targetType"] == "programStage")
    assert stages["wrapper"] == "programStages" and stages["itemField"] == "program"
    assert "programStages" in template_data("program")


def test_tracked_entity_attribute_distinguishes_its_mount() -> None:
    """FR-COV-005."""
    tea = descriptor_data("trackedEntityAttribute")
    mount = descriptor_data("programTrackedEntityAttribute")

    assert "ATTRIBUTE_MOUNT" in tea["implicit"]
    assert "TEA_MOUNTED_ON_TE_TYPE_OR_PROGRAM" in tea["rules"]
    assert "attributes" in dependency_fields(descriptor_data("trackedEntityType"))
    assert "attribute" in dependency_fields(mount)
    assert target_types(mount) == {"trackedEntityAttribute", "program"}


def test_custom_attribute_values_resolve_through_attribute_definitions() -> None:
    """FR-COV-011."""
    data = descriptor_data("attribute")

    assert "ATTRIBUTE_VALUES" in data["implicit"]
    assert "ATTRIBUTE_APPLIES_TO_OBJECT_TYPE" in data["rules"]


def test_organisation_unit_is_reference_only() -> None:
    """FR-COV-007 / BP-REG-013: the hierarchy comes from Discovery, so nothing is authored."""
    data = descriptor_data("organisationUnit")
    forbidden = set(data["forbiddenFields"])

    assert set(SYSTEM_OWNED_FIELDS[:6]) <= forbidden
    assert {"parent", "path", "level"} <= forbidden, "an authored OU hierarchy is blocking"
    assert data["requiredFields"] == []
    assert dependencies(data) == []
    assert {"OU_REFERENCE_ONLY", "ORG_UNIT_REFERENCE_ONLY"} <= set(data["rules"])
    assert "OU_CREATE_NEEDS_APPROVED_DECISION" in data["rules"]
    assert template_data("organisationUnit") == {"id": "${uid}"}


def test_user_accounts_never_travel_in_a_metadata_payload() -> None:
    """FR-COV-008 / BP-REG-014."""
    for dhis2_type in ("userRole", "userGroup"):
        data = descriptor_data(dhis2_type)

        assert "NO_PASSWORD_IN_PAYLOAD" in data["rules"], dhis2_type
        assert {"password", "userCredentials"} <= set(data["forbiddenFields"]), dhis2_type
        assert not target_types(data), f"{dhis2_type} references environment users"


def test_expressions_reference_business_keys_and_add_no_edges() -> None:
    """FR-CMP-010: the compiler only swaps businessKeys for UIDs inside expressions."""
    for dhis2_type, expression in (
        ("programRule", "condition"),
        ("programIndicator", "expression"),
        ("validationRule", "leftExpression"),
    ):
        data = descriptor_data(dhis2_type)
        assert expression in data["requiredFields"], dhis2_type
        assert expression not in dependency_fields(data), dhis2_type
        assert expression in template_data(dhis2_type), dhis2_type

    action = descriptor_data("programRuleAction")
    assert {"dataElement", "programStage", "programRuleVariable"} <= dependency_fields(action)
    assert all(
        item.get("required") is False
        for item in dependencies(action)
        if item["targetType"] != "programRule"
    )
    assert target_types(descriptor_data("programIndicator")) == {"program"}

    validation = descriptor_data("validationRule")
    assert {
        "field": "categoryOptionCombo",
        "targetType": "categoryOptionCombo",
        "required": False,
    } in dependencies(validation)

    relationship = descriptor_data("relationshipType")
    ends = {str(item["field"]): item for item in dependencies(relationship)}
    assert set(ends) == {"trackedEntityA", "trackedEntityB"}
    # both ends optional, so an event-program relationship stays legal
    assert all(item.get("required") is False for item in ends.values())
    assert target_types(relationship) == {"trackedEntityType"}


def test_option_set_owns_its_options() -> None:
    option = descriptor_data("option")

    assert option["embeddedInParent"] == "optionSet"
    assert option["payloadKey"] == "options"
    assert option["translatableFields"] == ["name"]
    assert {
        "field": "optionSet",
        "targetType": "optionSet",
        "required": True,
        "wrapper": "options",
    } in dependencies(option)
    assert "options" in template_data("optionSet")
