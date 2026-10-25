"""P1: JSON Pointer resolution and RFC 6902 application."""

from __future__ import annotations

import pytest

from factory_blueprint.content.pointer import PointerError, locate, parse_pointer, sections_of
from factory_blueprint.enums import Section
from factory_blueprint.patch import rfc6902
from tests.unit import helpers


def test_parse_pointer_escapes() -> None:
    assert parse_pointer("/registry/entries/a~1b") == ("registry", "entries", "a/b")
    assert parse_pointer("/pages/items/patient~0detail") == ("pages", "items", "patient~detail")
    assert parse_pointer("") == ()


def test_locate_finds_section_container_and_entry() -> None:
    location = locate("/registry/entries/pregnancy.delivery/disposition")
    assert location.section is Section.REGISTRY
    assert location.container is not None
    assert location.container.path == "/registry/entries"
    assert location.entry_key == "pregnancy.delivery"
    assert location.remainder == ("disposition",)


def test_locate_whole_object_section() -> None:
    location = locate("/project/deliveryMode")
    assert location.section is Section.PROJECT
    assert location.top_level_section


def test_unknown_section_raises() -> None:
    with pytest.raises(PointerError):
        locate("/nonsense/items/x")


def test_root_pointer_is_rejected_for_operations() -> None:
    with pytest.raises(PointerError):
        locate("")


def test_sections_of_is_ordered_by_declaration() -> None:
    assert sections_of(["/registry/entries/a", "/project/deliveryMode", "/registry/entries/b"]) == [
        Section.REGISTRY,
        Section.PROJECT,
    ]


def _ops(*raw: dict[str, object]) -> list[rfc6902.Operation]:
    return rfc6902.parse_operations(list(raw))


def test_add_then_replace_and_remove() -> None:
    document = helpers.empty_document()
    entry = helpers.registry_entry("sex", "optionSet", "REUSE")
    applied = rfc6902.apply_operations(
        document, _ops({"op": "add", "path": "/registry/entries/-", "value": entry})
    )
    assert applied["registry"]["entries"][0]["businessKey"] == "sex"
    assert document["registry"]["entries"] == []  # input untouched

    replaced = rfc6902.apply_operations(
        applied, _ops({"op": "replace", "path": "/registry/entries/0/disposition", "value": "REFERENCE"})
    )
    assert replaced["registry"]["entries"][0]["disposition"] == "REFERENCE"

    removed = rfc6902.apply_operations(replaced, _ops({"op": "remove", "path": "/registry/entries/0"}))
    assert removed["registry"]["entries"] == []


def test_move_and_copy() -> None:
    document = helpers.seeded_document()
    entry = document["registry"]["entries"][0]
    copied = rfc6902.apply_operations(
        document,
        _ops({"op": "copy", "from": "/registry/entries/0", "path": "/registry/entries/1"}),
    )
    assert len(copied["registry"]["entries"]) == 2
    assert copied["registry"]["entries"][1] == entry

    moved = rfc6902.apply_operations(
        document, _ops({"op": "move", "from": "/registry/entries/0", "path": "/mapping/entries/-"})
    )
    assert moved["registry"]["entries"] == []
    assert moved["mapping"]["entries"][0]["businessKey"] == "pregnancy"


def test_test_operation_guards_a_concurrent_edit() -> None:
    document = helpers.seeded_document()
    ops = _ops(
        {"op": "test", "path": "/project/deliveryMode", "value": "B"},
        {"op": "replace", "path": "/project/deliveryMode", "value": "A"},
    )
    assert rfc6902.apply_operations(document, ops)["project"]["deliveryMode"] == "A"

    with pytest.raises(rfc6902.PatchMalformed):
        rfc6902.apply_operations(
            document, _ops({"op": "test", "path": "/project/deliveryMode", "value": "A"})
        )


@pytest.mark.parametrize(
    "raw",
    [
        [{"op": "frobnicate", "path": "/registry/entries/-", "value": {}}],
        [{"op": "add", "path": "registry/entries", "value": {}}],
        [{"op": "add", "path": "/registry/entries/-"}],
        [{"op": "remove", "path": "/registry/entries/9"}],
        [{"op": "move", "path": "/registry/entries/0"}],
        [{"op": "test", "path": "/registry/entries/0"}],
        [{"op": "replace", "path": "", "value": {}}],
    ],
)
def test_malformed_operations_are_rejected(raw: list[dict[str, object]]) -> None:
    document = helpers.seeded_document()
    with pytest.raises(rfc6902.PatchMalformed):
        rfc6902.apply_operations(document, rfc6902.parse_operations(raw))


def test_replacing_an_existing_element_is_legal_rfc6902() -> None:
    """The patch engine only enforces patch semantics; the schema rejects garbage."""
    document = helpers.seeded_document()
    applied = rfc6902.apply_operations(
        document, _ops({"op": "replace", "path": "/registry/entries/0", "value": {}})
    )
    assert applied["registry"]["entries"] == [{}]


def test_operation_round_trips_to_json() -> None:
    operation = _ops({"op": "add", "path": "/registry/entries/-", "value": {"a": 1}})[0]
    assert operation.to_json() == {"op": "add", "path": "/registry/entries/-", "value": {"a": 1}}
