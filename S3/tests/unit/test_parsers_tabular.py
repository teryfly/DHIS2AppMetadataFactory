"""The tabular parsers: CSV/TSV and .xlsx, with the AI-INP-006 column-mapping gate.

The point of these tests is not that a CSV can be read — it is that the two halves of
AI-INP-002's "确定性解析器 + 列映射 / 复核列映射" row stay separable:

* the *parse* is deterministic, so the same bytes give the same NRR twice (AI-INP-010);
* the *mapping* is a human's decision when none was supplied, which shows up as
  ``needs_review``, exactly one open question and every entry ``CANDIDATE`` — never as a
  silently confirmed requirement, and never as a model call (AI-INP-003).

``HEADER_CATEGORY_RULES`` is asserted as data: a reviewer who wants a different reading of
a header edits that table, and the parametrised case here says which header means what.
"""

from __future__ import annotations

import io
import json
import sys
from typing import Any

import pytest
from factory_foundation.api import DataClassification
from openpyxl import Workbook

from factory_intelligence import registries
from factory_intelligence.enums import NrrStatus, ParserType
from factory_intelligence.errors import ParserDependencyMissing
from factory_intelligence.parsing.base import Fragment, NrrCategory, ParseError, Parser
from factory_intelligence.parsing.tabular.csvparser import (
    HEADER_CATEGORY_RULES,
    CsvParser,
    category_for_header,
    normalise_header,
)
from factory_intelligence.parsing.tabular.xlsxparser import XlsxParser

ARTIFACT = "art-0000000000000001"

CSV_FIELDS = "字段,类型,说明\npatient_age,int,患儿月龄\nweight_kg,decimal,出生体重\npatient_age,int,月龄重复行\n"
FIELD_MAPPING = {"字段": "field", "类型": "type", "说明": "description"}

CSV_OPTIONS = "代码,名称,排序\nM,Male,1\nF,Female,2\nU,Unknown,3\n"
OPTION_MAPPING = {"代码": "code", "名称": "name", "排序": "order"}

CSV_ORGANISATION = "机构编码,机构名称,层级\n001,县医院,2\n002,乡卫生院,3\n"
ORGANISATION_MAPPING = {"机构编码": "orgCode", "机构名称": "orgName", "层级": "level"}

#: no recognisable header at all: the parser may not pick a category for it
CSV_SHAPELESS = "a,b\nc,d\n"

CSV_FIELDS_GOLDEN: list[tuple[str, str, str, str]] = [
    (
        "dataRequirement",
        "datarequirement_patient_age",
        "row:字段清单#2",
        "Data requirement 'patient_age' — type: int; description: 患儿月龄.",
    ),
    (
        "dataRequirement",
        "datarequirement_weight_kg",
        "row:字段清单#3",
        "Data requirement 'weight_kg' — type: decimal; description: 出生体重.",
    ),
    (
        "dataRequirement",
        "datarequirement_patient_age_6381",
        "row:字段清单#4",
        "Data requirement 'patient_age' — type: int; description: 月龄重复行.",
    ),
]

CSV_OPTIONS_GOLDEN: list[tuple[str, str, str, str]] = [
    (
        "dataRequirement",
        "datarequirement_male",
        "row:csv#2",
        "Data requirement 'Male' — code 'M'; order: 1.",
    ),
    (
        "dataRequirement",
        "datarequirement_female",
        "row:csv#3",
        "Data requirement 'Female' — code 'F'; order: 2.",
    ),
    (
        "dataRequirement",
        "datarequirement_unknown",
        "row:csv#4",
        "Data requirement 'Unknown' — code 'U'; order: 3.",
    ),
]

CSV_ORGANISATION_GOLDEN: list[tuple[str, str, str, str]] = [
    (
        "organisationRequirement",
        "organisationrequirement_001",
        "row:机构清单#2",
        "Organisation unit '001' — orgName: 县医院; level: 2.",
    ),
    (
        "organisationRequirement",
        "organisationrequirement_002",
        "row:机构清单#3",
        "Organisation unit '002' — orgName: 乡卫生院; level: 3.",
    ),
]


def build_workbook() -> bytes:
    """The .xlsx fixture, in memory: no binary input file is added to the repository."""
    buffer = io.BytesIO()
    workbook = Workbook()
    fields = workbook.active
    fields.title = "字段清单"
    fields.append(["字段", "类型", "说明"])
    fields.append(["patient_age", "int", "患儿月龄"])
    fields.append(["weight_kg", "decimal", "出生体重"])
    options = workbook.create_sheet("选项")
    options.append(["代码", "名称", "选项集"])
    options.append(["M", "Male", "sex"])
    options.append(["F", "Female", "sex"])
    workbook.save(buffer)
    return buffer.getvalue()


XLSX_FIXTURE = build_workbook()
XLSX_MAPPING = {**FIELD_MAPPING, **OPTION_MAPPING, "选项集": "optionSet"}

XLSX_GOLDEN: list[tuple[str, str, str, str]] = [
    (
        "dataRequirement",
        "datarequirement_patient_age",
        "sheet:字段清单!A2",
        "Data requirement 'patient_age' — type: int; description: 患儿月龄.",
    ),
    (
        "dataRequirement",
        "datarequirement_weight_kg",
        "sheet:字段清单!A3",
        "Data requirement 'weight_kg' — type: decimal; description: 出生体重.",
    ),
    (
        "dataRequirement",
        "datarequirement_male",
        "sheet:选项!B2",
        "Data requirement 'Male' — code 'M'; of the option list 'sex'.",
    ),
    (
        "dataRequirement",
        "datarequirement_female",
        "sheet:选项!B3",
        "Data requirement 'Female' — code 'F'; of the option list 'sex'.",
    ),
]

#: the cases whose mapping was supplied, so every entry is CONFIRMED (AI-INP-006)
TABULAR_CONFIRMED_CASES: tuple[tuple[str, Parser, bytes, dict[str, Any]], ...] = (
    (
        "csv field list",
        CsvParser(),
        CSV_FIELDS.encode(),
        {"column_mapping": FIELD_MAPPING, "sheet": "字段清单"},
    ),
    ("csv option list", CsvParser(), CSV_OPTIONS.encode(), {"column_mapping": OPTION_MAPPING}),
    (
        "csv organisation list",
        CsvParser(),
        CSV_ORGANISATION.encode(),
        {"column_mapping": ORGANISATION_MAPPING, "sheet": "机构清单"},
    ),
    ("xlsx two sheets", XlsxParser(), XLSX_FIXTURE, {"column_mapping": XLSX_MAPPING}),
)

#: everything the tabular parsers read, mapping supplied or not: the review-gated cases are
#: the ones whose entries come back CANDIDATE
TABULAR_CASES: tuple[tuple[str, Parser, bytes, dict[str, Any]], ...] = TABULAR_CONFIRMED_CASES + (
    ("csv no mapping", CsvParser(), CSV_FIELDS.encode(), {"sheet": "字段清单"}),
    ("xlsx no mapping", XlsxParser(), XLSX_FIXTURE, {}),
)


def parse_csv(source: str = CSV_FIELDS, **options: Any) -> Fragment:
    return CsvParser().parse(
        source.encode("utf-8"),
        source_artifact_id=ARTIFACT,
        classification=DataClassification.INTERNAL,
        options=options,
    )


def parse_xlsx(content: bytes, **options: Any) -> Fragment:
    return XlsxParser().parse(
        content,
        source_artifact_id=ARTIFACT,
        classification=DataClassification.INTERNAL,
        options=options,
    )


def table(fragment: Fragment) -> list[tuple[str, str, str, str]]:
    return [
        (entry.category.value, entry.business_key, str(entry.location), entry.statement)
        for entry in fragment.entries
    ]


def serialise(fragment: Fragment) -> str:
    return json.dumps([entry.to_nrr_record() for entry in fragment.entries], sort_keys=True)


def test_csv_field_list_golden_fragment() -> None:
    """AI-INP-010: one fixed CSV, one fixed NRR fragment."""
    fragment = parse_csv(column_mapping=FIELD_MAPPING, sheet="字段清单")

    assert table(fragment) == CSV_FIELDS_GOLDEN
    assert fragment.parser_type is ParserType.CSV
    assert fragment.parser_version == "1.0.0"
    assert fragment.source_artifact_id == ARTIFACT
    assert fragment.classification is DataClassification.INTERNAL
    assert fragment.used_model is False
    assert fragment.entries[0].payload == {
        "sheet": "字段清单",
        "values": {"type": "int", "description": "患儿月龄"},
    }


def test_csv_option_list_is_a_data_requirement_with_its_options() -> None:
    """AI-INP-002's 选项清单: a code+name sheet enumerates a data requirement."""
    fragment = parse_csv(CSV_OPTIONS, column_mapping=OPTION_MAPPING)

    assert table(fragment) == CSV_OPTIONS_GOLDEN
    assert [entry.category for entry in fragment.entries] == [NrrCategory.DATA_REQUIREMENT] * 3
    assert fragment.entries[0].payload["options"] == [{"code": "M", "name": "Male"}]


def test_csv_organisation_list_is_an_organisation_requirement() -> None:
    fragment = parse_csv(CSV_ORGANISATION, column_mapping=ORGANISATION_MAPPING, sheet="机构清单")

    assert table(fragment) == CSV_ORGANISATION_GOLDEN
    assert all(entry.category is NrrCategory.ORGANISATION for entry in fragment.entries)


def test_csv_action_column_without_a_recognisable_header_is_a_use_case() -> None:
    """One row of AI-INP-002's "字段、选项、组织清单" is a list of things a person does."""
    fragment = parse_csv("编号,用户操作\n1,登记体重\n2,审核儿童卡片\n")

    assert [entry.category for entry in fragment.entries] == [NrrCategory.USE_CASE] * 2
    assert fragment.notes["recognisedBy"] == "action column values"
    assert fragment.entries[0].statement.startswith("Use case '登记体重'")


def test_header_category_rules_are_the_whole_mapping() -> None:
    """The table is data: what a reviewer sees is exactly what the parser applies."""
    assert HEADER_CATEGORY_RULES  # a parser with no rules would parse everything as a question
    assert category_for_header("字段") is NrrCategory.DATA_REQUIREMENT
    assert category_for_header(" Attribute Name ") is NrrCategory.DATA_REQUIREMENT
    assert category_for_header("机构名称") is NrrCategory.ORGANISATION
    assert category_for_header("orgCode") is NrrCategory.ORGANISATION
    assert category_for_header("报表名称") is NrrCategory.REPORT
    assert category_for_header("SMS 提醒") is NrrCategory.NOTIFICATION
    assert category_for_header("语言") is NrrCategory.I18N
    assert category_for_header("角色") is NrrCategory.PARTICIPANT
    assert category_for_header("权限") is NrrCategory.ACCESS
    assert category_for_header("流程") is NrrCategory.BUSINESS_PROCESS
    assert category_for_header("校验规则") is NrrCategory.BUSINESS_RULE
    assert category_for_header("用户操作") is None  # not a header rule: an action *value*
    assert normalise_header("  Field-Name  ") == "field_name"


def test_duplicate_labels_get_distinct_business_keys() -> None:
    """Two rows named the same are two facts; S2 still needs two keys (AI-INP-009)."""
    fragment = parse_csv(column_mapping=FIELD_MAPPING, sheet="字段清单")
    first, _middle, repeated = fragment.entries

    assert first.statement != repeated.statement
    assert repeated.business_key != first.business_key
    assert repeated.business_key.startswith(f"{first.business_key}_")
    assert str(repeated.location) == "row:字段清单#4"


def test_csv_without_a_column_mapping_is_proposed_and_asked() -> None:
    """AI-INP-006: no mapping ⇒ a proposal, NEEDS_REVIEW, one question, nothing confirmed."""
    fragment = parse_csv(CSV_FIELDS, sheet="字段清单")

    assert fragment.needs_review is True
    assert fragment.review_reason is not None and "AI-INP-006" in fragment.review_reason
    assert len(fragment.open_questions) == 1
    question = fragment.open_questions[0]
    assert question["severity"] == "HIGH"
    assert "字段" in question["question"] and "column_mapping" in question["question"]
    assert [entry.status for entry in fragment.entries] == [NrrStatus.CANDIDATE] * 3
    assert fragment.notes["proposedColumnMapping"] == {"字段": "字段", "类型": "类型", "说明": "说明"}


def test_the_same_csv_with_a_confirmed_mapping_is_confirmed() -> None:
    """The other half of AI-INP-006: with a mapping there is nothing to ask."""
    fragment = parse_csv(CSV_FIELDS, column_mapping=FIELD_MAPPING, sheet="字段清单")

    assert fragment.needs_review is False
    assert fragment.review_reason is None
    assert fragment.open_questions == []
    assert [entry.status for entry in fragment.entries] == [NrrStatus.CONFIRMED] * 3


def test_a_sheet_with_nothing_to_match_asks_instead_of_choosing_a_category() -> None:
    """An unrecognisable table produces a question, not a guessed category (AI-INP-008)."""
    fragment = parse_csv(CSV_SHAPELESS)

    assert fragment.entries == []
    assert fragment.is_empty is False
    assert fragment.needs_review is True
    assert len(fragment.open_questions) == 1
    assert "deterministic header rule" in fragment.open_questions[0]["question"]


def test_tsv_and_an_explicit_delimiter_are_honoured() -> None:
    tabbed = "字段\t类型\t说明\nweight\tdecimal\t体重\n"

    by_name = parse_csv(tabbed, column_mapping=FIELD_MAPPING, filename="清单.tsv")
    by_option = parse_csv(tabbed, column_mapping=FIELD_MAPPING, delimiter="\t")

    #: same facts, and the sheet part of the location comes from the file it was named as
    assert [entry.statement for entry in by_name.entries] == [
        entry.statement for entry in by_option.entries
    ]
    assert by_name.notes["delimiter"] == "\t" == by_option.notes["delimiter"]
    assert str(by_name.entries[0].location) == "row:清单#2"
    assert str(by_option.entries[0].location) == "row:csv#2"


def test_has_header_false_uses_positional_mapping() -> None:
    fragment = parse_csv(
        "patient_age,int,患儿月龄\n",
        has_header=False,
        column_mapping={"1": "字段", "2": "类型", "3": "说明"},
    )

    assert [entry.category for entry in fragment.entries] == [NrrCategory.DATA_REQUIREMENT]
    assert fragment.entries[0].business_key == "datarequirement_patient_age"
    assert str(fragment.entries[0].location) == "row:csv#1"


def test_an_empty_csv_raises_rather_than_reporting_an_empty_nrr() -> None:
    """AI-INP-003a's reasoning applies to every deterministic parser."""
    with pytest.raises(ParseError) as raised:
        parse_csv("\n\n")
    assert raised.value.code == "AI_PARSER_INPUT_INVALID"
    assert raised.value.code in registries.AI_ERROR_CODES


def test_xlsx_golden_fragment_with_real_cell_coordinates() -> None:
    """AI-INP-010 again, and AI-INP-005's location: the A1 coordinate of the label cell."""
    fragment = parse_xlsx(XLSX_FIXTURE, column_mapping=XLSX_MAPPING)

    assert table(fragment) == XLSX_GOLDEN
    assert fragment.parser_type is ParserType.XLSX
    assert fragment.parser_version == "1.0.0"
    assert fragment.used_model is False
    assert fragment.needs_review is False
    assert fragment.notes["sheets"] == {"字段清单": 3, "选项": 3}


def test_xlsx_business_keys_are_unique_across_sheets() -> None:
    """S2's uniqueness is per container, and one fragment is one container."""
    fragment = parse_xlsx(XLSX_FIXTURE, column_mapping=XLSX_MAPPING)
    keys = [entry.business_key for entry in fragment.entries]

    assert len(keys) == len(set(keys))


def test_xlsx_without_a_mapping_is_reviewed_once_per_sheet() -> None:
    fragment = parse_xlsx(XLSX_FIXTURE)

    assert fragment.needs_review is True
    assert len(fragment.open_questions) == 2
    assert [entry.status for entry in fragment.entries] == [NrrStatus.CANDIDATE] * 4
    assert set(fragment.notes["proposedColumnMapping"]) == {"字段清单", "选项"}


def test_xlsx_with_no_header_row_and_no_mapping_needs_review() -> None:
    fragment = parse_xlsx(XLSX_FIXTURE, has_header=False)

    assert fragment.needs_review is True
    assert fragment.entries == []
    assert len(fragment.open_questions) == 2


def test_xlsx_can_read_one_named_sheet() -> None:
    fragment = parse_xlsx(XLSX_FIXTURE, sheet="选项", column_mapping=XLSX_MAPPING)

    assert [str(entry.location) for entry in fragment.entries] == ["sheet:选项!B2", "sheet:选项!B3"]


def test_xlsx_reports_a_missing_dependency_instead_of_an_empty_fragment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """AI-INP-002 makes Excel a mandatory input; an absent library is a loud failure."""
    monkeypatch.setitem(sys.modules, "openpyxl", None)

    with pytest.raises(ParserDependencyMissing) as raised:
        parse_xlsx(XLSX_FIXTURE)

    assert raised.value.error_code == "AI_PARSER_DEPENDENCY_MISSING"
    assert raised.value.error_code in registries.AI_ERROR_CODES


def test_xlsx_reports_an_unreadable_workbook(monkeypatch: pytest.MonkeyPatch) -> None:
    """openpyxl is present here, so a broken file is a parse failure, not an empty sheet."""
    with pytest.raises(ParseError) as raised:
        parse_xlsx(b"this is not a workbook")

    assert raised.value.code == "AI_PARSER_INPUT_INVALID"


@pytest.mark.parametrize("label,parser,content,options", TABULAR_CASES)
def test_parsing_the_same_bytes_twice_gives_the_same_fragment(
    label: str, parser: Parser, content: bytes, options: dict[str, Any]
) -> None:
    first = parser.parse(
        content,
        source_artifact_id=ARTIFACT,
        classification=DataClassification.INTERNAL,
        options=options,
    )
    second = parser.parse(
        content,
        source_artifact_id=ARTIFACT,
        classification=DataClassification.INTERNAL,
        options=options,
    )

    assert serialise(first) == serialise(second), label
    assert first.needs_review == second.needs_review, label


@pytest.mark.parametrize("label,parser,content,options", TABULAR_CASES)
def test_tabular_entries_are_unique_and_located(
    label: str, parser: Parser, content: bytes, options: dict[str, Any]
) -> None:
    fragment = parser.parse(
        content,
        source_artifact_id=ARTIFACT,
        classification=DataClassification.INTERNAL,
        options=options,
    )
    keys = [entry.business_key for entry in fragment.entries]

    assert keys, label
    assert len(keys) == len(set(keys)), label
    assert all(entry.location is not None for entry in fragment.entries), label
