"""BP-VAL-021 / D-05: the golden expression corpus replayed against the local parser.

``tests/golden/expressions.json`` records, for the DHIS2 2.41 grammar S2 ships, which
expressions the offline parser must accept and which it must reject with which declared
error code.  The corpus is derived from what ``grammars/dhis2.2.41.json`` declares —
operators, reference kinds, function arities, keywords, variables and per-language
features — so a grammar-data change that alters the product is caught here.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from factory_blueprint.validators.expressions import SYNTAX_ERROR_CODES, available_grammars, parse

CORPUS_PATH = Path(__file__).resolve().parent.parent / "golden" / "expressions.json"


def _load_corpus() -> dict[str, Any]:
    document: dict[str, Any] = json.loads(CORPUS_PATH.read_text(encoding="utf-8"))
    return document


CORPUS: dict[str, Any] = _load_corpus()
ENTRIES: list[dict[str, Any]] = list(CORPUS["entries"])
GRAMMAR_VERSION: str = str(CORPUS["grammarVersion"])


def _entry_id(entry: dict[str, Any], index: int) -> str:
    return f"{index:02d}-{entry['language']}-{entry['expect']}"


@pytest.mark.parametrize(
    "entry", ENTRIES, ids=[_entry_id(entry, index) for index, entry in enumerate(ENTRIES)]
)
def test_golden_expression_behaves_as_recorded(entry: dict[str, Any]) -> None:
    expression = str(entry["expression"])
    result = parse(expression, language=str(entry["language"]), grammar_version=GRAMMAR_VERSION)
    if entry["expect"] == "ok":
        assert result.ok, (expression, result.codes, result.messages)
        assert result.ast is not None
        assert result.errors == []
        return
    assert not result.ok, expression
    assert result.ast is None, "a rejected expression is reported, never repaired (C-07)"
    assert set(entry["codes"]) <= set(result.codes), (expression, result.codes)
    assert all(code in SYNTAX_ERROR_CODES for code in result.codes), result.codes


def test_the_corpus_exercises_every_expression_language_and_outcome() -> None:
    assert {str(entry["language"]) for entry in ENTRIES} == {
        "programRule",
        "programIndicator",
        "validationRule",
    }
    assert {str(entry["expect"]) for entry in ENTRIES} == {"ok", "error"}
    assert len(ENTRIES) >= 30
    assert len({str(entry["expression"]) for entry in ENTRIES}) == len(ENTRIES)


def test_every_corpus_entry_is_fully_described() -> None:
    for entry in ENTRIES:
        assert set(entry) == {"language", "expression", "expect", "codes"}
        assert entry["expect"] in {"ok", "error"}
        assert all(code in SYNTAX_ERROR_CODES for code in entry["codes"])
        if entry["expect"] == "error":
            assert entry["codes"], entry


def test_the_recorded_grammar_is_the_one_shipped_with_this_build() -> None:
    assert GRAMMAR_VERSION in available_grammars()
