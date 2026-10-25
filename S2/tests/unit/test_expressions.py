"""BP-VAL-021: the local expression parser, its grammar data and reference resolution.

A golden corpus of expressions S2 must accept or reject before anything is sent to a
server, plus the reference-resolution and substitution behaviour the compiler relies on
(BP-CMP-028).  Nothing here reaches the network, the clock or a random source.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from factory_foundation.api import FactoryError

from factory_blueprint.errors import ExpressionGrammarMissing
from factory_blueprint.registries import BP_ERROR_CODES
from factory_blueprint.validators.expressions import (
    RESOLVE_ERROR_CODES,
    SYNTAX_ERROR_CODES,
    Grammar,
    RegistryTarget,
    ast_to_json,
    available_grammars,
    collect_references,
    grammar_versions,
    parse,
    parse_expression,
    registry_index,
    resolve,
    substitute,
    tokenize,
)
from factory_blueprint.validators.expressions import grammar as grammar_module
from factory_blueprint.validators.expressions.grammar import (
    LANGUAGE_PROGRAM_INDICATOR,
    LANGUAGE_PROGRAM_RULE,
    LANGUAGE_VALIDATION_RULE,
)

GRAMMAR = "dhis2-2.41"
EXPRESSIONS_PACKAGE = Path(grammar_module.__file__).resolve().parent
RULE = LANGUAGE_PROGRAM_RULE
INDICATOR = LANGUAGE_PROGRAM_INDICATOR
VALIDATION = LANGUAGE_VALIDATION_RULE

UID_WEIGHT = "We1ght00001"
UID_HEIGHT = "He1ght00001"
UID_DUE = "DueDate0001"
UID_DE = "DataEleme0001"
UID_DE1 = "DataElem10001"
UID_COC = "CatOptCom001"
UID_RISK = "RiskScore0001"
UID_VAR = "RuleVar0001"

APPROVED = "APPROVED"
PROPOSED = "PROPOSED"

#: (language, expression) — every one of these must parse without an issue.
VALID_EXPRESSIONS: list[tuple[str, str]] = [
    (RULE, "#{weight} > 25"),
    (RULE, "#{weight} > 25 && !hasValue(#{dueDate})"),
    (RULE, "V#{programStage} == 'ANC'"),
    (RULE, "D2:COUNT(#{dataElement}) > 3"),
    (RULE, "d2:hasValue(#{dueDate}) || #{dueDate} != 0"),
    (RULE, "#{a} + #{b} * #{c} - 1 / 2 == 0"),
    (RULE, "(#{a} + (#{b} * (#{c} - 1))) > 0"),
    (RULE, "((#{a}))"),
    (RULE, "#{status} in ['A', 'B', 'C']"),
    (RULE, "#{score} > 3 ? 'high' : 'low'"),
    (RULE, "#{score} > 3 ? #{score} > 9 ? 'x' : 'y' : 'z'"),
    (RULE, "#{score} > 1 ? 'a' : #{score} > 2 ? 'b' : 'c'"),
    (RULE, "'it\\'s ok' == #{text}"),
    (RULE, '"say \\"hi\\"" == #{text}'),
    (RULE, "not (#{a} == 1)"),
    (RULE, "!(#{a} == 1) && #{b} != 2"),
    (RULE, "true"),
    (RULE, "#{a} >= 1 && #{a} <= 10 || #{b} == 0"),
    (RULE, "length(#{text}) > 3"),
    (RULE, "contains(#{text}, 'x')"),
    (RULE, "d2:concatenate(#{first}, ' ', #{last}, '-', #{middle})"),
    (RULE, "d2:zing(0.5) > 0.4"),
    (RULE, "-#{offset} + 1.5 <= 0.25"),
    (RULE, "#{a} % 2 == 0"),
    (RULE, "A#{age} > 18"),
    (RULE, "!{city} == 'Kisumu'"),
    (RULE, "C#{threshold} * 100 > #{weight}"),
    (RULE, "DV#{status} == 3"),
    (RULE, "RV#{status} == 3"),
    (RULE, "#{a}\n  +   #{b}\t> 1"),
    (RULE, "d2:condition('#x > 1', 1, 0)"),
    (INDICATOR, "#{weight} / #{height} * 100"),
    (INDICATOR, "d2:daysBetween(#{startDate}, #{endDate})"),
    (INDICATOR, "avg(#{score}) > 5"),
    (INDICATOR, "sum(#{v}) / count(#{v})"),
    (INDICATOR, "min(#{a}, #{b}, #{c}) > 0"),
    (INDICATOR, "if(#{a} > 1, 1, 0)"),
    (INDICATOR, "iR#{riskScore} >= 5"),
    (INDICATOR, "#{de1.coc} + #{de2} > 10"),
    (INDICATOR, "A#{attribute} + #{weight}"),
    (INDICATOR, "![defaultCoc] + 1"),
    (INDICATOR, "d2:monthsBetween(#{start}, #{end}) * 12 + #{a}"),
    (INDICATOR, "d2:addDays(#{startDate}, 30) != 0"),
    (INDICATOR, "d2:numValue(#{textValue}) > 5"),
    (INDICATOR, "!hasValue(#{a}) || #{b} > 0"),
    (INDICATOR, "#{a} > 1 ? 1 : 0"),
    (INDICATOR, "#{a} == #{b} && #{c} != #{d}"),
    (RULE, "d2:in(['1', '2'], #{code})"),
    (INDICATOR, "d2:countIf(#{a}, #{b}, 1)"),
    (VALIDATION, "#{de1} + #{de2} > #{de3}"),
    (VALIDATION, "#{de1.coc} + #{de2.coc} <= #{de3}"),
    (VALIDATION, "C#{threshold} * 100 >= #{de1}"),
    (VALIDATION, "![defaultCoc] + #{de1} == 0"),
    (VALIDATION, "-#{de1} + #{de2} / 2 < 100"),
    (VALIDATION, "(#{de1} + #{de2}) * (#{de3} - #{de4}) >= 0"),
    (VALIDATION, "today + 1 > #{de1}"),
    (VALIDATION, "#{de1} % 5 == 0"),
    (VALIDATION, "1.75 * #{de1} + 0.25 * #{de2}"),
    (VALIDATION, "#{de1} != #{de2}"),
]

#: (language, expression, expected error code) — every one must be rejected with that code.
INVALID_EXPRESSIONS: list[tuple[str, str, str]] = [
    (RULE, "", "EXPR_EMPTY"),
    (RULE, "   ", "EXPR_EMPTY"),
    (RULE, "#{weight", "EXPR_UNCLOSED_REFERENCE"),
    (RULE, "#{dueDate} && #{we", "EXPR_UNCLOSED_REFERENCE"),
    (RULE, "#{}", "EXPR_EMPTY_REFERENCE"),
    (RULE, "C#{} + 1", "EXPR_EMPTY_REFERENCE"),
    (RULE, "![", "EXPR_UNCLOSED_REFERENCE"),
    (RULE, "#{a} +", "EXPR_MISSING_OPERAND"),
    (RULE, "#{a} &&", "EXPR_MISSING_OPERAND"),
    (RULE, "#{a} == ", "EXPR_MISSING_OPERAND"),
    (RULE, "#{a} + * #{b}", "EXPR_UNEXPECTED_TOKEN"),
    (RULE, "* 3", "EXPR_UNEXPECTED_TOKEN"),
    (RULE, "(#{a}", "EXPR_UNBALANCED_PAREN"),
    (RULE, "!(#{a} == #{b}", "EXPR_UNBALANCED_PAREN"),
    (RULE, "#{a} + (#{b} * (#{c})", "EXPR_UNBALANCED_PAREN"),
    (RULE, "hasValue(#{a}", "EXPR_UNEXPECTED_TOKEN"),
    (RULE, "#{a})", "EXPR_TRAILING_INPUT"),
    (RULE, "#{a} #{b}", "EXPR_TRAILING_INPUT"),
    (RULE, "#{a} @@ 3", "EXPR_UNKNOWN_CHARACTER"),
    (RULE, "#{a}; #{b}", "EXPR_UNKNOWN_CHARACTER"),
    (VALIDATION, "#{a} = #{b}", "EXPR_UNKNOWN_CHARACTER"),
    (RULE, "unknownFn(#{a})", "EXPR_UNKNOWN_FUNCTION"),
    (RULE, "d2:unknown(#{a})", "EXPR_UNKNOWN_FUNCTION"),
    (RULE, "d2:COUNTS(#{a})", "EXPR_UNKNOWN_FUNCTION"),
    (RULE, "d2:daysBetween(#{a})", "EXPR_ARITY_MISMATCH"),
    (RULE, "D2:COUNT(#{a}, #{b})", "EXPR_ARITY_MISMATCH"),
    (RULE, "hasValue()", "EXPR_ARITY_MISMATCH"),
    (RULE, "contains(#{a})", "EXPR_ARITY_MISMATCH"),
    (RULE, "d2:daysBetween(#{a}, )", "EXPR_MISSING_OPERAND"),
    (RULE, "#{a} ? 3", "EXPR_MALFORMED_TERNARY"),
    (RULE, "#{a} > 1 ? 2", "EXPR_MALFORMED_TERNARY"),
    (RULE, "iR#{x} > 1", "EXPR_REFERENCE_NOT_ALLOWED"),
    (RULE, "![attr] == 'x'", "EXPR_REFERENCE_NOT_ALLOWED"),
    (RULE, "V#{a.b} > 1", "EXPR_SUFFIX_NOT_ALLOWED"),
    (RULE, "A#{a.b} > 1", "EXPR_SUFFIX_NOT_ALLOWED"),
    (INDICATOR, "A#{a.b} + 1", "EXPR_SUFFIX_NOT_ALLOWED"),
    (RULE, "#{a} in ['x'", "EXPR_UNBALANCED_BRACKET"),
    (RULE, "#{a} in [1, ]", "EXPR_MISSING_OPERAND"),
    (RULE, "#{a} in 3", "EXPR_UNEXPECTED_TOKEN"),
    (RULE, "#{a} in 3]", "EXPR_UNEXPECTED_TOKEN"),
    (RULE, "[1, ] == #{a}", "EXPR_MISSING_OPERAND"),
    (RULE, "'oops", "EXPR_UNTERMINATED_STRING"),
    (RULE, '"oops', "EXPR_UNTERMINATED_STRING"),
    (RULE, "d2:", "EXPR_UNEXPECTED_TOKEN"),
    (RULE, "d2:COUNT", "EXPR_UNEXPECTED_TOKEN"),
    (VALIDATION, "#{a} && #{b}", "EXPR_OPERATOR_NOT_ALLOWED"),
    (VALIDATION, "#{a} > 1 ? 2 : 3", "EXPR_CONSTRUCT_NOT_ALLOWED"),
    (VALIDATION, "!#{a} + 1", "EXPR_OPERATOR_NOT_ALLOWED"),
    (VALIDATION, "[1, 2]", "EXPR_CONSTRUCT_NOT_ALLOWED"),
    (VALIDATION, "V#{programStage} == 'ANC'", "EXPR_REFERENCE_NOT_ALLOWED"),
    (VALIDATION, "A#{attr} + 1", "EXPR_REFERENCE_NOT_ALLOWED"),
    (VALIDATION, "d2:daysBetween(#{a}, #{b})", "EXPR_REFERENCE_NOT_ALLOWED"),
    (VALIDATION, "length(#{a}) > 1", "EXPR_UNKNOWN_FUNCTION"),
    (INDICATOR, "#{a} in [1, 2]", "EXPR_OPERATOR_NOT_ALLOWED"),
]

REGISTRY: dict[str, tuple[str, str | None, str]] = {
    "weight": ("dataElement", UID_WEIGHT, APPROVED),
    "height": ("dataElement", UID_HEIGHT, APPROVED),
    "dueDate": ("dataElement", UID_DUE, APPROVED),
    "de": ("dataElement", UID_DE, APPROVED),
    "de1": ("dataElement", UID_DE1, APPROVED),
    "de1.coc": ("dataElementOperand", UID_COC, APPROVED),
    "riskScore": ("programIndicator", UID_RISK, APPROVED),
}


def test_valid_corpus_covers_every_language() -> None:
    assert len(VALID_EXPRESSIONS) >= 30
    assert len({language for language, _ in VALID_EXPRESSIONS}) == 3
    assert len(INVALID_EXPRESSIONS) >= 30
    assert len({code for _, _, code in INVALID_EXPRESSIONS}) >= 15


def test_every_rejected_corpus_case_names_a_declared_error_code() -> None:
    for language, expression, code in INVALID_EXPRESSIONS:
        assert code in SYNTAX_ERROR_CODES, code
        result = parse(expression, language=language, grammar_version=GRAMMAR)
        assert result.codes, expression
        assert all(error.code in SYNTAX_ERROR_CODES for error in result.errors)


def test_every_resolve_issue_names_a_declared_error_code() -> None:
    registry = dict(REGISTRY)
    registry["proposed"] = ("dataElement", "Proposed001", PROPOSED)
    result = parse(
        "#{weight} + #{missing} + #{proposed} > someVariable",
        language=RULE,
        grammar_version=GRAMMAR,
    )
    assert result.ast is not None
    resolved = resolve(result.ast, registry, language=RULE, grammar_version=GRAMMAR)
    assert {issue.code for issue in resolved.issues} == {
        "EXPR_UNRESOLVED_REFERENCE",
        "EXPR_REFERENCE_NOT_APPROVED",
        "EXPR_UNDECLARED_VARIABLE",
    }
    assert {issue.code for issue in resolved.issues} <= RESOLVE_ERROR_CODES


@pytest.mark.parametrize(("language", "expression"), VALID_EXPRESSIONS)
def test_valid_expression_parses(language: str, expression: str) -> None:
    result = parse(expression, language=language, grammar_version=GRAMMAR)
    assert result.ok, (expression, result.messages)
    assert result.ast is not None
    assert result.errors == []
    assert result.codes == []
    assert result.messages == []


@pytest.mark.parametrize(("language", "expression", "code"), INVALID_EXPRESSIONS)
def test_invalid_expression_reports_its_code(language: str, expression: str, code: str) -> None:
    result = parse(expression, language=language, grammar_version=GRAMMAR)
    assert not result.ok
    assert result.ast is None
    assert code in result.codes
    assert all(error.offset <= len(expression) for error in result.errors)


def test_rejected_expression_is_never_repaired() -> None:
    result = parse("#{a} + ", language=RULE, grammar_version=GRAMMAR)
    assert result.ast is None
    assert result.codes == ["EXPR_MISSING_OPERAND"]
    assert result.errors[0].offset == len("#{a} + ")


def test_empty_expression_is_an_issue_not_an_exception() -> None:
    result = parse("", language=INDICATOR, grammar_version=GRAMMAR)
    assert result.errors[0].code == "EXPR_EMPTY"
    assert result.errors[0].offset == 0


def test_error_offset_points_at_the_offending_character() -> None:
    expression = "#{a} @@ 3"
    result = parse(expression, language=RULE, grammar_version=GRAMMAR)
    error = result.errors[0]
    assert expression[error.offset] == "@"
    assert "@" in error.message


def test_unclosed_reference_offsets_at_its_opener() -> None:
    expression = "#{a} + #{b"
    result = parse(expression, language=RULE, grammar_version=GRAMMAR)
    assert result.errors[0].code == "EXPR_UNCLOSED_REFERENCE"
    assert result.errors[0].offset == expression.index("#{b")


def test_depth_limit_reports_instead_of_exhausting_the_stack() -> None:
    inside = "1 + 1"
    too_deep = parse("(" * 200 + inside + ")" * 200, language=RULE, grammar_version=GRAMMAR)
    assert too_deep.codes == ["EXPR_DEPTH_EXCEEDED"]
    assert too_deep.ast is None
    allowed = parse("(" * 40 + inside + ")" * 40, language=RULE, grammar_version=GRAMMAR)
    assert allowed.ok


def test_flat_expression_chains_do_not_deepen_the_parse() -> None:
    result = parse("#{a} + " * 4000 + "1", language=RULE, grammar_version=GRAMMAR)
    assert result.ok


def test_operator_precedence_from_the_grammar() -> None:
    arithmetic = parse("1 + 2 * 3", language=RULE, grammar_version=GRAMMAR)
    assert arithmetic.ast is not None
    assert arithmetic.ast.op == "+"
    assert arithmetic.ast.args[1].op == "*"
    logical = parse("#{a} || #{b} && #{c}", language=RULE, grammar_version=GRAMMAR)
    assert logical.ast is not None
    assert logical.ast.op == "||"
    assert logical.ast.args[1].op == "&&"


def test_ternary_is_right_associative() -> None:
    result = parse("#{a} > 1 ? 2 : #{b} > 3 ? 4 : 5", language=RULE, grammar_version=GRAMMAR)
    assert result.ast is not None
    assert result.ast.kind == "conditional"
    assert result.ast.args[2].kind == "conditional"


def test_string_literals_keep_escapes_decoded() -> None:
    single = parse("'it\\'s ok'", language=RULE, grammar_version=GRAMMAR)
    assert single.ast is not None
    assert single.ast.text == "it's ok"
    double = parse('"say \\"hi\\""', language=RULE, grammar_version=GRAMMAR)
    assert double.ast is not None
    assert double.ast.text == 'say "hi"'


def test_newlines_and_tabs_are_plain_whitespace() -> None:
    assert parse("#{a}\n+\t#{b}", language=RULE, grammar_version=GRAMMAR).ok


def test_tokenize_reports_kind_and_character_offset() -> None:
    grammar = Grammar.load(GRAMMAR)
    language = grammar.language(RULE)
    tokens = tokenize("#{weight} > 25 && 'x'", grammar, language).tokens
    assert [token.kind for token in tokens] == [
        "reference",
        "operator",
        "number",
        "operator",
        "string",
        "eof",
    ]
    assert tokens[0].offset == 0
    assert tokens[1].offset == 10
    assert tokens[2].text == "25"
    assert tokens[4].offset == 18
    assert tokens[4].value == "x"
    assert tokens[-1].kind == "eof"


def test_reference_token_carries_segments_and_spans() -> None:
    grammar = Grammar.load(GRAMMAR)
    expression = "#{de1.coc}"
    tokens = tokenize(expression, grammar, grammar.language(VALIDATION)).tokens
    reference = tokens[0].reference
    assert reference is not None
    assert [segment.text for segment in reference.segments] == ["de1", "coc"]
    assert reference.segments[0].start == 2
    assert reference.segments[1].end == 9
    assert reference.end == len(expression)


def test_collected_references_are_in_source_order() -> None:
    result = parse("#{b} > (#{a} + #{c})", language=RULE, grammar_version=GRAMMAR)
    assert result.ast is not None
    assert [reference.name for reference in collect_references(result.ast)] == ["b", "a", "c"]


def test_ast_to_json_is_deterministic_and_position_carrying() -> None:
    result = parse("#{a} > 1", language=RULE, grammar_version=GRAMMAR)
    assert result.ast is not None
    first = ast_to_json(result.ast)
    again = parse("#{a} > 1", language=RULE, grammar_version=GRAMMAR)
    assert again.ast is not None
    second = ast_to_json(again.ast)
    assert first == second
    assert json.dumps(first, sort_keys=True) == json.dumps(second, sort_keys=True)
    assert first["kind"] == "binary"
    assert first["op"] == ">"
    assert first["args"][0]["refs"][0]["opener"] == "#{"
    assert first["args"][0]["refs"][0]["segments"] == [{"text": "a", "start": 2, "end": 3}]


def test_parsing_is_repeatable_across_grammar_loads() -> None:
    assert Grammar.load(GRAMMAR) is Grammar.load("2.41")
    first = parse("#{a} && !#{b}", language=RULE, grammar_version=GRAMMAR)
    second = parse("#{a} && !#{b}", language=RULE, grammar_version="dhis2-2.41")
    assert first.codes == second.codes
    assert first.ast is not None and second.ast is not None
    assert ast_to_json(first.ast) == ast_to_json(second.ast)


def test_available_grammars_lists_the_shipped_grammar() -> None:
    assert available_grammars() == ("dhis2-2.41",)
    assert {"2.41", "2.42", GRAMMAR} <= set(grammar_versions())


def test_grammar_name_and_limits_are_kept_in_data() -> None:
    grammar = Grammar.load("2.42")
    assert grammar.name == GRAMMAR
    assert grammar.dhis2_versions == ("2.41", "2.42")
    assert grammar.max_depth == 128


def test_unknown_grammar_version_raises_the_subsystem_error() -> None:
    with pytest.raises(ExpressionGrammarMissing) as excinfo:
        parse("#{a} > 1", language=RULE, grammar_version="dhis2-3.0")
    assert excinfo.value.error_code == "BP_EXPRESSION_GRAMMAR_MISSING"
    assert "dhis2-3.0" in str(excinfo.value)
    assert GRAMMAR in str(excinfo.value)


def test_unknown_language_raises_naming_the_grammar() -> None:
    with pytest.raises(ExpressionGrammarMissing) as excinfo:
        parse("#{a} > 1", language="predictableOutcome", grammar_version=GRAMMAR)
    assert excinfo.value.error_code == "BP_EXPRESSION_GRAMMAR_MISSING"
    assert GRAMMAR in str(excinfo.value)


def test_grammar_language_aliases_are_data_too() -> None:
    assert parse("#{a} > 1", language="programRuleCondition", grammar_version=GRAMMAR).ok
    assert parse("#{a} + #{b}", language="validationRuleExpression", grammar_version=GRAMMAR).ok


def test_the_new_error_code_is_registered_for_s2() -> None:
    assert BP_ERROR_CODES["BP_EXPRESSION_GRAMMAR_MISSING"] == ("S2", False)
    assert issubclass(ExpressionGrammarMissing, FactoryError)


def test_grammar_data_supplies_the_whole_vocabulary() -> None:
    grammar = Grammar.load(GRAMMAR)
    rule = grammar.language(RULE)
    assert {symbol for symbol, spec in rule.operators.items() if not spec.word} >= {
        "&&",
        "||",
        "==",
        "!=",
        "<=",
        ">=",
        "+",
        "-",
        "*",
        "/",
        "%",
    }
    assert set(rule.prefix_operators) == {"!", "not", "-", "+"}
    assert rule.operators["in"].right_operand == "list"
    assert {kind.name for kind in rule.reference_kinds.values()} >= {
        "dataElement",
        "attribute",
        "programRuleVariable",
        "constant",
        "optionCode",
        "d2Function",
    }
    assert {spec.name for spec in rule.functions.values()} == {
        "hasValue",
        "zing",
        "length",
        "contains",
    }
    assert grammar.reference_kinds["d2Function"].openers == ("D2:", "d2:")
    assert grammar.reference_kinds["dataElement"].allows_dots is True
    assert grammar.reference_kinds["attribute"].allows_dots is False


@pytest.mark.parametrize(
    "file_name",
    ["parser.py", "lexer.py", "resolve.py"],
)
def test_python_code_holds_no_dhis2_vocabulary(file_name: str) -> None:
    """Everything the parser recognises comes from the grammar file, not from code."""
    source = (EXPRESSIONS_PACKAGE / file_name).read_text(encoding="utf-8")
    for token in ("#{", "!{", "![", "D2:", "d2:", "hasValue", "daysBetween", "programStage"):
        assert f'"{token}' not in source, f"{token} is hard-coded in {file_name}"


def test_input_string_is_never_modified() -> None:
    expression = "#{weight} > 25 && !hasValue(#{dueDate})"
    result = parse(expression, language=RULE, grammar_version=GRAMMAR)
    assert result.ast is not None
    substituted = substitute(
        expression, result.ast, REGISTRY, language=RULE, grammar_version=GRAMMAR
    )
    assert substituted.ok
    assert expression == "#{weight} > 25 && !hasValue(#{dueDate})"
    assert substituted.text != expression


def test_resolve_accepts_registered_references() -> None:
    result = parse("#{weight} / #{height} * 100", language=INDICATOR, grammar_version=GRAMMAR)
    assert result.ast is not None
    resolved = resolve(result.ast, REGISTRY, language=INDICATOR, grammar_version=GRAMMAR)
    assert resolved.ok
    assert resolved.issues == []
    assert [item.key for item in resolved.references] == ["weight", "height"]
    assert resolved.server_verifiable is True
    assert resolved.deferred_to_post_verify is False


def test_resolve_reports_unknown_business_key() -> None:
    result = parse("#{weight} + #{unknown}", language=RULE, grammar_version=GRAMMAR)
    assert result.ast is not None
    resolved = resolve(result.ast, REGISTRY, language=RULE, grammar_version=GRAMMAR)
    assert not resolved.ok
    issue = resolved.issues[0]
    assert issue.code == "EXPR_UNRESOLVED_REFERENCE"
    assert issue.offset == 12
    assert issue.key == "unknown"


def test_resolve_reports_the_wrong_target_type_for_the_reference_kind() -> None:
    registry = dict(REGISTRY)
    registry["weight"] = ("categoryOption", UID_WEIGHT, APPROVED)
    result = parse("#{weight} > 1", language=RULE, grammar_version=GRAMMAR)
    assert result.ast is not None
    resolved = resolve(result.ast, registry, language=RULE, grammar_version=GRAMMAR)
    assert resolved.issues[0].code == "EXPR_REFERENCE_TYPE_MISMATCH"
    assert "categoryOption" in resolved.issues[0].message


def test_resolve_can_narrow_the_expected_type_by_field_context() -> None:
    result = parse("#{weight} > 1", language=RULE, grammar_version=GRAMMAR)
    assert result.ast is not None
    resolved = resolve(
        result.ast,
        REGISTRY,
        language=RULE,
        grammar_version=GRAMMAR,
        field="programRule.condition",
        expected_types={"dataElement": ("programIndicator",)},
    )
    assert resolved.issues[0].code == "EXPR_REFERENCE_TYPE_MISMATCH"
    assert resolved.issues[0].message.startswith("programRule.condition: ")


def test_resolve_reports_a_proposed_entry() -> None:
    registry = dict(REGISTRY)
    registry["weight"] = ("dataElement", UID_WEIGHT, PROPOSED)
    result = parse("#{weight} > 1", language=RULE, grammar_version=GRAMMAR)
    assert result.ast is not None
    resolved = resolve(result.ast, registry, language=RULE, grammar_version=GRAMMAR)
    assert resolved.issues[0].code == "EXPR_REFERENCE_NOT_APPROVED"


def test_resolve_reports_an_unassigned_uid() -> None:
    registry = dict(REGISTRY)
    registry["weight"] = ("dataElement", None, APPROVED)
    result = parse("#{weight} > 1", language=RULE, grammar_version=GRAMMAR)
    assert result.ast is not None
    resolved = resolve(result.ast, registry, language=RULE, grammar_version=GRAMMAR)
    assert resolved.issues[0].code == "EXPR_UID_NOT_ASSIGNED"


def test_resolve_reports_an_unknown_status() -> None:
    registry = dict(REGISTRY)
    registry["weight"] = ("dataElement", UID_WEIGHT, "SOMETHING_ELSE")
    result = parse("#{weight} > 1", language=RULE, grammar_version=GRAMMAR)
    assert result.ast is not None
    resolved = resolve(result.ast, registry, language=RULE, grammar_version=GRAMMAR)
    assert resolved.issues[0].code == "EXPR_REFERENCE_STATUS_UNKNOWN"


def test_resolve_reports_a_variable_the_grammar_does_not_declare() -> None:
    result = parse("someVariable > 3", language=RULE, grammar_version=GRAMMAR)
    assert result.ok
    assert result.ast is not None
    resolved = resolve(result.ast, REGISTRY, language=RULE, grammar_version=GRAMMAR)
    assert resolved.issues[0].code == "EXPR_UNDECLARED_VARIABLE"
    assert resolved.issues[0].offset == 0


def test_resolve_accepts_the_variables_the_grammar_declares() -> None:
    result = parse(
        "eventDate != dueDate && V#{programStage} == 'ANC'", language=RULE, grammar_version=GRAMMAR
    )
    assert result.ok
    assert result.ast is not None
    resolved = resolve(result.ast, REGISTRY, language=RULE, grammar_version=GRAMMAR)
    assert resolved.issues == []
    assert [item.key for item in resolved.references] == ["programStage"]
    assert resolved.references[0].replacement is None


def test_resolve_accepts_a_registered_program_rule_variable() -> None:
    registry = dict(REGISTRY)
    registry["birthWeight"] = ("programRuleVariable", UID_VAR, APPROVED)
    result = parse("V#{birthWeight} > 3", language=RULE, grammar_version=GRAMMAR)
    assert result.ast is not None
    resolved = resolve(result.ast, registry, language=RULE, grammar_version=GRAMMAR)
    assert resolved.ok
    assert resolved.references[0].replacement == UID_VAR


def test_substitution_is_positional_and_keeps_everything_else() -> None:
    expression = "#{weight} > 25 && !hasValue(#{dueDate}) && V#{programStage} == 'ANC'"
    result = parse(expression, language=RULE, grammar_version=GRAMMAR)
    assert result.ast is not None
    substituted = substitute(
        expression, result.ast, REGISTRY, language=RULE, grammar_version=GRAMMAR
    )
    assert substituted.ok
    assert substituted.text == (
        f"#{{{UID_WEIGHT}}} > 25 && !hasValue(#{{{UID_DUE}}}) && V#{{programStage}} == 'ANC'"
    )
    assert substituted.server_verifiable is True


def test_substitution_uses_the_longest_registered_key_first() -> None:
    expression = "#{de1.coc} + #{de1} + #{de} > 0"
    result = parse(expression, language=VALIDATION, grammar_version=GRAMMAR)
    assert result.ast is not None
    substituted = substitute(
        expression, result.ast, REGISTRY, language=VALIDATION, grammar_version=GRAMMAR
    )
    assert substituted.text == f"#{{{UID_COC}}} + #{{{UID_DE1}}} + #{{{UID_DE}}} > 0"


def test_attribute_and_option_code_references_are_substituted() -> None:
    registry = {
        "age": ("trackedEntityAttribute", "AttrAge0001", APPROVED),
        "status": ("dataElement", UID_DE, APPROVED),
    }
    expression = "A#{age} > 18 && DV#{status} == 2"
    result = parse(expression, language=RULE, grammar_version=GRAMMAR)
    assert result.ast is not None
    substituted = substitute(
        expression, result.ast, registry, language=RULE, grammar_version=GRAMMAR
    )
    assert substituted.text == "A#{AttrAge0001} > 18 && DV#{DataEleme0001} == 2"


def test_repeated_business_keys_are_each_substituted() -> None:
    expression = "#{weight} + #{weight} * 2"
    result = parse(expression, language=RULE, grammar_version=GRAMMAR)
    assert result.ast is not None
    substituted = substitute(
        expression, result.ast, REGISTRY, language=RULE, grammar_version=GRAMMAR
    )
    assert substituted.text == f"#{{{UID_WEIGHT}}} + #{{{UID_WEIGHT}}} * 2"


def test_substitution_refuses_when_a_reference_stays_unknown() -> None:
    expression = "#{weight} + #{missing}"
    result = parse(expression, language=RULE, grammar_version=GRAMMAR)
    assert result.ast is not None
    substituted = substitute(
        expression, result.ast, REGISTRY, language=RULE, grammar_version=GRAMMAR
    )
    assert not substituted.ok
    assert substituted.text == expression
    assert substituted.issues[0].code == "EXPR_UNRESOLVED_REFERENCE"
    assert substituted.deferred_to_post_verify is True


def test_registry_index_accepts_targets_and_leaves_the_input_alone() -> None:
    rows = {"weight": ("dataElement", UID_WEIGHT, APPROVED)}
    assert registry_index(rows)["weight"].uid == UID_WEIGHT
    assert rows["weight"] == ("dataElement", UID_WEIGHT, APPROVED)
    direct = registry_index(
        {"weight": RegistryTarget("weight", "dataElement", UID_WEIGHT, APPROVED)}
    )
    assert direct["weight"].dhis2_type == "dataElement"


def test_the_srs_example_expressions_all_parse() -> None:
    for language, expression in (
        (RULE, "#{weight} > 25 && !hasValue(#{dueDate})"),
        (RULE, "V#{programStage} == 'ANC'"),
        (RULE, "D2:COUNT(#{dataElement}) > 3"),
        (INDICATOR, "#{weight} / #{height} * 100"),
        (INDICATOR, "d2:daysBetween(#{startDate}, #{endDate})"),
        (VALIDATION, "#{de1} + #{de2} > #{de3}"),
    ):
        assert parse(expression, language=language, grammar_version=GRAMMAR).ok


def test_parse_expression_is_the_same_function_as_parse() -> None:
    assert parse_expression is parse
    assert parse_expression("1 + 1", language=RULE, grammar_version=GRAMMAR).ok
