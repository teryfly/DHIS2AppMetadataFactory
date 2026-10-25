"""Performance smoke tests for the two pure stages (BP-NFR-01, BP-NFR-02).

Compiling and validating a 1,000-object package must stay under 30 seconds each.
Neither number is a benchmark to tune against — it is the guard that stops a
quadratic lookup from entering the compiler or the validator unnoticed.

They are skipped by default because they are slow, not flaky:

    FACTORY_PERF=1 .venv/bin/pytest -q -m perf tests/perf
"""

from __future__ import annotations

import os
import time
from datetime import UTC, datetime
from typing import Any

import pytest

from factory_blueprint.compilers import metadata as metadata_compiler
from factory_blueprint.compilers.context import CompileInputs
from factory_blueprint.content.document import BlueprintDocument
from factory_blueprint.dto.build import BuildContext
from factory_blueprint.enums import IssueLevel
from factory_blueprint.s4 import contract
from factory_blueprint.s4.stub import discovered_object, discovery_document, profile_document
from factory_blueprint.validators import cross_artifact, static
from tests.unit import helpers

pytestmark = pytest.mark.perf

#: both limits come from the SRS table in chapter 24
LIMIT_SECONDS = 30.0
OBJECTS = 1_000

SKIP_REASON = "set FACTORY_PERF=1 to run the performance smoke tests"


@pytest.mark.skipif(os.environ.get("FACTORY_PERF") != "1", reason=SKIP_REASON)
def test_compiling_a_thousand_objects_stays_under_thirty_seconds() -> None:
    started = time.perf_counter()
    output = metadata_compiler.compile_metadata(_inputs())
    elapsed = time.perf_counter() - started
    assert len(output.objects) >= OBJECTS - 20, "the scenario really is 1,000 objects"
    assert not [issue for issue in output.issues if issue.level is IssueLevel.BLOCKING]
    assert elapsed < LIMIT_SECONDS, f"compiled {len(output.objects)} objects in {elapsed:.1f}s"


@pytest.mark.skipif(os.environ.get("FACTORY_PERF") != "1", reason=SKIP_REASON)
def test_validating_a_thousand_objects_stays_under_thirty_seconds() -> None:
    inputs = _inputs()
    output = metadata_compiler.compile_metadata(inputs)
    started = time.perf_counter()
    issues = [
        *static.validate_build(inputs, output),
        *cross_artifact.validate(inputs.document, output, inputs),
    ]
    elapsed = time.perf_counter() - started
    assert elapsed < LIMIT_SECONDS, f"validated {len(output.objects)} objects in {elapsed:.1f}s"
    assert not [issue for issue in issues if issue.level is IssueLevel.BLOCKING]
    assert len(output.objects) >= OBJECTS - 20


def _inputs() -> CompileInputs:
    document = _document()
    return CompileInputs(
        project_id="pperf",
        blueprint_version="0.1.0",
        # the hash the build names is the document's own, so the architecture check has
        # nothing to report and the measurement covers the real work
        blueprint_hash=BlueprintDocument(document).content_hash,
        document=document,
        build_context=BuildContext(
            built_at=datetime(2026, 5, 5, tzinfo=UTC), seed=1, requester="perf"
        ),
        blueprint_artifact_id="art-01PERF0000000000000000000",
        profile=contract.profile_from_json(profile_document()),
        discovery=_discovery(),
        environment_key="PERF",
        bindings=_bindings(),
        observed={_uid(index): {"id": _uid(index), "name": "old"} for index in range(902, 1002)},
    )


def _document() -> dict[str, Any]:
    """A program of the size BP-NFR-01 names: elements, attributes, stages and rules."""
    document = helpers.empty_document()
    document["registry"]["entries"] = _registry_entries()
    document["mapping"]["entries"] = _mapping_entries()
    document["rules"]["validationRules"] = _validation_rules()
    return document


def _registry_entries() -> list[dict[str, Any]]:
    entries = [
        helpers.registry_entry("default.categorycombo", "categoryCombo", "CREATE", uid=_uid(0)),
        helpers.registry_entry("person", "trackedEntityType", "CREATE", uid=_uid(1)),
    ]
    index = 2
    for key in _element_keys():
        entries.append(helpers.registry_entry(key, "dataElement", "CREATE", uid=_uid(index)))
        index += 1
    for key in _attribute_keys():
        entries.append(
            helpers.registry_entry(key, "trackedEntityAttribute", "CREATE", uid=_uid(index))
        )
        index += 1
    for key in _stage_keys():
        entries.append(helpers.registry_entry(key, "programStage", "CREATE", uid=_uid(index)))
        index += 1
    for key in _option_keys():
        entries.append(
            helpers.registry_entry(
                key, "optionSet", "UPDATE", uid=_uid(index), controlled_fields=["name"]
            )
        )
        index += 1
    entries.append(helpers.registry_entry("pregnancy", "program", "CREATE", uid=_uid(index)))
    return entries


def _mapping_entries() -> list[dict[str, Any]]:
    entries = [
        helpers.mapping_entry(
            "default.categorycombo",
            "categoryCombo",
            fields={"name": {"default": "Default"}, "shortName": {"default": "Default"}},
        ),
        helpers.mapping_entry(
            "person",
            "trackedEntityType",
            fields={
                "name": {"default": "Person"},
                "shortName": {"default": "Person"},
                "trackEntities": {"value": False},
            },
        ),
    ]
    for key in _element_keys():
        entries.append(
            helpers.mapping_entry(
                key,
                "dataElement",
                fields={
                    "name": {"default": key, "translations": {"fr": key}},
                    "shortName": {"default": key[:20]},
                    "valueType": "TEXT",
                    "domainType": "ATTRIBUTE",
                    "categoryCombo": "default.categorycombo",
                },
            )
        )
    for key in _attribute_keys():
        entries.append(
            helpers.mapping_entry(
                key,
                "trackedEntityAttribute",
                fields={
                    "name": {"default": key},
                    "valueType": "TEXT",
                    "mount": {"kind": "TRACKED_ENTITY_TYPE", "target": "person"},
                },
            )
        )
    for key in _stage_keys():
        entries.append(
            helpers.mapping_entry(
                key, "programStage", fields={"name": {"default": key}, "program": "pregnancy"}
            )
        )
    for key in _option_keys():
        entries.append(helpers.mapping_entry(key, "optionSet", fields={"name": {"default": key}}))
    entries.append(
        helpers.mapping_entry(
            "pregnancy",
            "program",
            fields={
                "name": {"default": "Pregnancy"},
                "shortName": {"default": "Preg"},
                "programType": "WITH_REGISTRATION",
                "trackedEntity": "person",
                "categoryCombo": "default.categorycombo",
            },
        )
    )
    return entries


def _validation_rules() -> list[dict[str, Any]]:
    elements = _element_keys()
    return [
        {
            "businessKey": f"rule-{index}",
            "name": f"Rule {index}",
            "leftExpression": f"#{{{elements[index % len(elements)]}}}",
            "rightExpression": f"#{{{elements[(index + 1) % len(elements)]}}} + 1",
            "operator": "EQUALS",
        }
        for index in range(200)
    ]


def _element_keys() -> list[str]:
    # no dot: inside ``#{}`` the dot is DHIS2's dataElement/categoryOption separator,
    # so a dotted businessKey there would address a combination rather than the element
    return [f"element-{index:04d}" for index in range(600)]


def _attribute_keys() -> list[str]:
    return [f"attribute.{index:04d}" for index in range(200)]


def _stage_keys() -> list[str]:
    return [f"stage.{index:04d}" for index in range(100)]


def _option_keys() -> list[str]:
    return [f"codeset.{index:04d}" for index in range(100)]


def _uid(index: int) -> str:
    return f"perf{index:07d}"


def _bindings() -> dict[str, str]:
    return {
        str(entry["businessKey"]): str(entry["uid"])
        for entry in _registry_entries()
        if isinstance(entry.get("uid"), str)
    }


def _discovery() -> contract.DiscoveryResult:
    return contract.discovery_from_json(
        discovery_document(
            "PERF",
            [
                discovered_object(
                    "optionSet", _uid(index), content={"id": _uid(index), "name": "old"}
                )
                for index in range(902, 1002)
            ],
            defaults={"categoryCombo": _uid(0)},
        )
    )
