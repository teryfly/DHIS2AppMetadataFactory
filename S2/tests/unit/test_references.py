"""Reference substitution in expressions (BP-CMP-028, BP-AC-09).

`substitute_references` is the only place a businessKey becomes a UID inside an
expression, and it is also what decides whether S2 *noticed* a reference at all.  A
form it fails to see produces a package with a businessKey shipped as if it were a UID,
and a Blueprint problem reported as nothing — so the shapes are enumerated here rather
than trusted.
"""

from __future__ import annotations

import pytest

from factory_blueprint.compilers import refs

BINDINGS = {
    "weight": "wUID0000001",
    "age": "aUID0000002",
    "dueDate": "ddUID0000004",
}


def known(key: str) -> str:
    return BINDINGS[key]


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("#{weight} > 14", ["weight"]),
        ("is #{weight} and #{age}", ["weight", "age"]),
        ("IF(#{weight} > 1, #{age}, 0)", ["weight", "age"]),
        ("A#{weight.coc} > 3", ["weight"]),
        ("V#{weight} != null", ["weight"]),
        ("d2:hasValue(#{weight})", ["weight"]),
        ("D2:COUNT(#{weight}) > 2", ["weight"]),
        ("![eventDate] == null", ["eventDate"]),
        ("[recentDate] - #{dueDate}", ["dueDate", "recentDate"]),
        ("#{pregnancy.weight} > 1", ["pregnancy"]),
    ],
)
def test_every_reference_form_is_seen(text: str, expected: list[str]) -> None:
    assert refs.reference_keys(text) == expected


def test_an_unknown_key_is_reported_rather_than_shipped() -> None:
    """BP-AC-09: a reference to nothing must reach the caller as a problem."""
    result = refs.substitute_references("#{bloodSugar} > 14", BINDINGS)
    assert result.unresolved == ["bloodSugar"]
    assert result.text == "#{bloodSugar} > 14"


def test_substitution_keeps_the_syntax_it_did_not_own() -> None:
    result = refs.substitute_references("IF(#{weight} > 1, #{age}, 0)", BINDINGS)
    assert result.text == "IF(#{" + known("weight") + "} > 1, #{" + known("age") + "}, 0)"
    assert result.references == ["age", "weight"]
    assert result.unresolved == []


def test_a_qualified_reference_keeps_its_qualifier() -> None:
    assert (
        refs.substitute_references("A#{weight.coc} > 3", BINDINGS).text
        == "A#{" + known("weight") + ".coc} > 3"
    )


def test_the_key_is_what_sits_before_the_first_dot() -> None:
    """``weight.coc`` is one object plus a qualifier, never a key called ``weight.coc``."""
    result = refs.substitute_references("#{weight.coc} > 1", BINDINGS)
    assert result.text == "#{" + known("weight") + ".coc} > 1"
    assert result.unresolved == []


def test_a_mixed_expression_reports_both_halves() -> None:
    result = refs.substitute_references("#{weight} + #{ghost} * #{age}", BINDINGS)
    assert result.references == ["age", "weight"]
    assert result.unresolved == ["ghost"]
    assert known("weight") in result.text and "#{ghost}" in result.text


def test_placeholders_round_trip() -> None:
    text = refs.placeholder("weight")
    assert refs.is_placeholder(text) and refs.key_of_placeholder(text) == "weight"
    assert refs.reference_keys(text) == ["weight"]
    assert not refs.is_placeholder("#{weight}")


def test_substitute_value_reaches_into_nested_payloads() -> None:
    value = {"filter": {"id": "weight"}, "items": [{"id": "age"}, {"other": "x"}]}
    result, touched = refs.substitute_value(value, BINDINGS, environment_bound=True)
    assert result == {
        "filter": {"id": known("weight")},
        "items": [{"id": known("age")}, {"other": "x"}],
    }
    assert sorted(touched) == ["age", "weight"]


def test_a_baseline_compile_leaves_an_unbound_reference_as_a_placeholder() -> None:
    result, touched = refs.substitute_value(
        {"id": "facility"}, BINDINGS, environment_bound=False
    )
    assert result == {"id": "{{ref:facility}}"}
    assert touched == ["facility"]


def test_an_expression_only_dhis2_cannot_check_waits_for_post_verify() -> None:
    assert refs.classify_expression(["weight", "age"], {"weight", "age"}) == "DHIS2_VALIDATE"
    assert refs.classify_expression(["weight", "ghost"], {"weight"}) == "POST_VERIFY"
