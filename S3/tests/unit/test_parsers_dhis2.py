"""The DHIS2 metadata parser: reuse observations, never uid identity (AI-INP-002, SRS 1.3).

This is the "既有 DHIS2 元数据 — 元数据导出 JSON、发现结果" row of AI-INP-002: a
deterministic parser whose review obligation is 复核复用意图.  The tests therefore pin three
things the SRS makes non-negotiable:

* **AI-INP-010** — a fixed export gives a fixed NRR fragment, twice.
* **SRS 1.3** — S3 neither generates nor consumes a DHIS2 uid as identity.  Every
  ``businessKey`` here comes from a ``code``, a display name, or a kind-plus-pointer, and the
  uid that came out of the export stays in ``payload["observedUid"]`` where AI-AG-011's
  similar-object search reads observations.
* **AI-INP-005 / AI-INP-008** — every record carries the real JSON pointer it was read from,
  and an object kind the parser does not know surfaces as an open question instead of a
  guessed category.
"""

from __future__ import annotations

import json
from typing import Any

import pytest
from factory_foundation.api import DataClassification

from factory_intelligence import registries
from factory_intelligence.enums import NrrStatus, ParserType
from factory_intelligence.parsing.base import Fragment, NrrCategory, ParseError, Parser
from factory_intelligence.parsing.dhis2.metajson import KIND_CATEGORY, Dhis2MetadataParser

ARTIFACT = "art-0000000000000001"

DHIS2_EXPORT: dict[str, Any] = {
    "dataElements": [
        {
            "id": "GeX6aPRg3lj",
            "code": "DE_WEIGHT",
            "displayName": "Weight",
            "valueType": "NUMBER",
            "optionSet": {"id": "txGmkrmGAf9", "code": "sex", "name": "Sex"},
        },
        {"id": "v6uR7X0a0TU", "displayName": "Remark", "valueType": "TEXT"},
    ],
    "optionSets": [
        {
            "id": "txGmkrmGAf9",
            "code": "sex",
            "name": "Sex",
            "options": [
                {"id": "pGGvKt2fjPx", "code": "M", "name": "Male"},
                {"id": "xYy3YX2Z0bR", "code": "F", "name": "Female"},
            ],
        }
    ],
    "userGroups": [
        {"id": "UIdChw0000000", "code": "CHW", "displayName": "Community Health Workers"}
    ],
    "programs": [
        {"id": "uZ0oODaHrnq", "code": "ANC", "name": "Antenatal care", "accessLevel": "open"}
    ],
    "programIndicators": [
        {
            "id": "oZ0mODaHrnq",
            "code": "BMI",
            "displayName": "Body mass index",
            "expression": "#{GeX6aPRg3lj} / #{v6uR7X0a0TU}",
        }
    ],
    "dataElementOperands": [
        {
            "id": "W7cgFxRr2CA",
            "dataElement": {"id": "GeX6aPRg3lj", "code": "DE_WEIGHT"},
            "attributeOptionCombo": "HVVpKRwcELa",
        }
    ],
    "validationRules": [
        {"id": "kZ0FQ6Y0FbT", "code": "VR_WEIGHT", "name": "weight must be over zero"}
    ],
}

#: S4's discovery result: one envelope, ``objects`` each naming their own type.
DISCOVERY_RESULT: dict[str, Any] = {
    "environmentKey": "staging-2.41",
    "discoveredAt": "2026-02-02T10:00:00Z",
    "objects": [
        {
            "dhis2Type": "dataElement",
            "uid": "GeX6aPRg3lj",
            "code": "DE_WEIGHT",
            "name": "Weight",
            "content": {"valueType": "NUMBER"},
        },
        {"dhis2Type": "optionSet", "uid": "txGmkrmGAf9", "code": "sex", "name": "Sex"},
        {"dhis2Type": "validationRule", "uid": "kZ0FQ6Y0FbT", "code": "VR_WEIGHT"},
    ],
    "defaults": {"categoryCombo": "bkcoE1zDFAg"},
}

#: A bare array, the shape a caller may hand over after splitting an export up.
BARE_ARRAY: list[dict[str, Any]] = [
    {"klass": "DATA_ELEMENT", "id": "GeX6aPRg3lj", "code": "DE_WEIGHT", "name": "Weight"},
    {"objectType": "user", "id": "xE7jOejl9FI", "code": "admin", "displayName": "System Admin"},
]

DHIS2_GOLDEN: list[tuple[str, str, str, str]] = [
    (
        "businessEntity",
        "businessentity_de_weight",
        "xref:/dataElements/0",
        "Business entity 'DE_WEIGHT' already exists in the target environment as a DHIS2 "
        "dataElement; value type 'NUMBER', option list 'sex'.",
    ),
    (
        "businessEntity",
        "businessentity_remark",
        "xref:/dataElements/1",
        "Business entity 'Remark' already exists in the target environment as a DHIS2 "
        "dataElement; value type 'TEXT'.",
    ),
    (
        "dataRequirement",
        "datarequirement_sex",
        "xref:/optionSets/0",
        "Data requirement 'sex' is an existing DHIS2 optionSet offering 2 options: "
        "Male (M), Female (F).",
    ),
    (
        "participant",
        "participant_chw",
        "xref:/userGroups/0",
        "Participant 'CHW' already exists in the target environment as a DHIS2 userGroup, so "
        "the roles it holds are observed rather than invented.",
    ),
    (
        "accessRequirement",
        "accessrequirement_anc",
        "xref:/programs/0",
        "Access requirement: the existing DHIS2 program 'ANC' carries access level 'open', so "
        "its sharing and data-capture rules must be carried over.",
    ),
    (
        "businessRule",
        "businessrule_bmi",
        "xref:/programIndicators/0",
        "Business rule 'BMI' is an existing DHIS2 programIndicator that computes "
        "'#{DE_WEIGHT} / #{Remark}' (kept verbatim in the payload).",
    ),
    (
        "businessRule",
        "businessrule_dataelementoperand_at_dataelementoperands_0",
        "xref:/dataElementOperands/0",
        "Business rule 'dataElementOperand at /dataElementOperands/0' is an existing DHIS2 "
        "dataElementOperand that computes '#{DE_WEIGHT}.{observedAttributeOptionComboUid}' "
        "(composed from its parts and kept in the payload).",
    ),
    (
        "openQuestion",
        "openquestion_vr_weight",
        "xref:/validationRules/0",
        "The DHIS2 input contains an object of the unrecognised kind 'validationRule' named "
        "'VR_WEIGHT'; this parser will not guess which requirement it carries, so its reuse "
        "intent must be confirmed (AI-INP-002).",
    ),
]

DHIS2_CASES: tuple[tuple[str, Parser, bytes, dict[str, Any]], ...] = (
    ("metadata export", Dhis2MetadataParser(), json.dumps(DHIS2_EXPORT).encode(), {}),
    ("discovery result", Dhis2MetadataParser(), json.dumps(DISCOVERY_RESULT).encode(), {}),
    ("bare array", Dhis2MetadataParser(), json.dumps(BARE_ARRAY).encode(), {}),
)


def parse(source: Any, **options: Any) -> Fragment:
    content = source if isinstance(source, bytes) else json.dumps(source).encode("utf-8")
    return Dhis2MetadataParser().parse(
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


def test_dhis2_metadata_export_golden_fragment() -> None:
    """AI-INP-010: one fixed export, one fixed NRR fragment."""
    fragment = parse(DHIS2_EXPORT)

    assert table(fragment) == DHIS2_GOLDEN
    assert fragment.parser_type is ParserType.DHIS2_METADATA
    assert fragment.parser_version == "1.0.0"
    assert fragment.source_artifact_id == ARTIFACT
    assert fragment.classification is DataClassification.INTERNAL
    assert fragment.used_model is False
    assert fragment.notes["reviewIntent"] == "reuse"


def test_no_business_key_is_a_dhis2_uid() -> None:
    """SRS 1.3: identity comes from code or name; the uid stays an observation.

    Every uid of the fixture is asserted against, not just the shape of one key: a parser
    that started using uids as identity would be indistinguishable otherwise.
    """
    fragment = parse(DHIS2_EXPORT)
    uids = [item["id"] for item in DHIS2_EXPORT["dataElements"]]
    uids += [item["id"] for item in DHIS2_EXPORT["optionSets"]]
    uids += [item["id"] for item in DHIS2_EXPORT["validationRules"]]

    for entry in fragment.entries:
        for uid in uids:
            assert uid not in entry.business_key
            assert uid not in entry.statement
        assert entry.source_payload()["artifactId"] == ARTIFACT
    observed = [entry.payload.get("observedUid") for entry in fragment.entries]
    assert "GeX6aPRg3lj" in observed


def test_the_identity_falls_back_to_display_name_then_pointer() -> None:
    fragment = parse(DHIS2_EXPORT)
    by_key = {entry.business_key: entry for entry in fragment.entries}

    assert "businessentity_remark" in by_key  # no code: the displayName
    operand = by_key["businessrule_dataelementoperand_at_dataelementoperands_0"]
    assert operand.payload["code"] is None  # no code and no name: kind plus pointer


def test_categories_follow_the_object_kind() -> None:
    fragment = parse(DHIS2_EXPORT)
    by_category: dict[str, list[str]] = {}
    for entry in fragment.entries:
        by_category.setdefault(entry.category.value, []).append(str(entry.location))

    assert by_category == {
        "businessEntity": ["xref:/dataElements/0", "xref:/dataElements/1"],
        "dataRequirement": ["xref:/optionSets/0"],
        "participant": ["xref:/userGroups/0"],
        "accessRequirement": ["xref:/programs/0"],
        "businessRule": ["xref:/programIndicators/0", "xref:/dataElementOperands/0"],
        "openQuestion": ["xref:/validationRules/0"],
    }
    assert KIND_CATEGORY["dataelement"] is NrrCategory.BUSINESS_ENTITY


def test_option_set_payload_carries_option_codes_and_names() -> None:
    fragment = parse(DHIS2_EXPORT)
    option_set = next(
        entry for entry in fragment.entries if entry.category is NrrCategory.DATA_REQUIREMENT
    )

    assert option_set.payload["options"] == [
        {"code": "M", "name": "Male", "observedUid": "pGGvKt2fjPx"},
        {"code": "F", "name": "Female", "observedUid": "xYy3YX2Z0bR"},
    ]


def test_an_expression_is_paraphrased_and_kept_verbatim() -> None:
    """The statement reads in codes; the payload keeps what the export actually said."""
    fragment = parse(DHIS2_EXPORT)
    indicator = fragment.entries[5]
    operand = fragment.entries[6]

    assert indicator.payload["expression"] == "#{GeX6aPRg3lj} / #{v6uR7X0a0TU}"
    assert indicator.payload["expressionSource"] == "observed"
    assert indicator.payload["expressionParaphrase"] == "#{DE_WEIGHT} / #{Remark}"
    assert operand.payload["expressionSource"] == "composedFromFields"
    assert operand.payload["observedAttributeOptionComboUid"] == "HVVpKRwcELa"


def test_an_unrecognised_kind_is_asked_and_never_guessed() -> None:
    """AI-INP-002 lists 复核复用意图 for this input, so an unknown kind surfaces (AI-INP-008)."""
    fragment = parse(DHIS2_EXPORT)
    question_entry = fragment.entries[-1]

    assert question_entry.category is NrrCategory.OPEN_QUESTION
    assert fragment.needs_review is True
    assert fragment.review_reason is not None and "validationRule" in fragment.review_reason
    # two obligations, not one: AI-INP-002's row says *reuse intent* is reviewed for every
    # object this parser recognised, and an unrecognised kind is asked about separately
    assert len(fragment.open_questions) == 2
    assert {item["severity"] for item in fragment.open_questions} == {"HIGH", "MEDIUM"}
    reuse = next(item for item in fragment.open_questions if item["severity"] == "MEDIUM")
    assert "reusing" in reuse["question"] and "dataElement" in reuse["question"]
    assert fragment.notes["unrecognisedKinds"] == {"validationRule": ["/validationRules/0"]}
    assert question_entry.status is NrrStatus.CONFIRMED  # the fact that it is unknown is certain


def test_s4_discovery_result_uses_object_type_and_real_pointers() -> None:
    fragment = parse(DISCOVERY_RESULT)

    assert [entry.category for entry in fragment.entries] == [
        NrrCategory.BUSINESS_ENTITY,
        NrrCategory.DATA_REQUIREMENT,
        NrrCategory.OPEN_QUESTION,
    ]
    assert [str(entry.location) for entry in fragment.entries] == [
        "xref:/objects/0",
        "xref:/objects/1",
        "xref:/objects/2",
    ]
    assert fragment.entries[0].payload["observedUid"] == "GeX6aPRg3lj"
    assert fragment.entries[0].payload["name"] == "Weight"
    assert fragment.notes["skippedNonObjectEntries"] == 0


def test_a_bare_array_input_reads_kind_and_klass() -> None:
    fragment = parse(BARE_ARRAY)

    assert [(entry.category, str(entry.location)) for entry in fragment.entries] == [
        (NrrCategory.BUSINESS_ENTITY, "xref:/0"),
        (NrrCategory.PARTICIPANT, "xref:/1"),
    ]
    assert fragment.entries[0].business_key == "businessentity_de_weight"


def test_a_meta_data_envelope_is_walked_not_invented() -> None:
    fragment = parse({"metaData": {"programs": [DHIS2_EXPORT["programs"][0]]}})

    assert [str(entry.location) for entry in fragment.entries] == ["xref:/metaData/programs/0"]


def test_input_that_is_not_metadata_fails_loudly() -> None:
    """AI-INP-003a's reasoning: no silently empty NRR from a deterministic parser."""
    with pytest.raises(ParseError) as not_json:
        parse(b"not json at all")
    assert not_json.value.code == "AI_PARSER_INPUT_INVALID"
    assert not_json.value.code in registries.AI_ERROR_CODES

    with pytest.raises(ParseError) as no_objects:
        parse({"metaData": {}, "pages": 1})
    assert no_objects.value.code == "AI_PARSER_INPUT_INVALID"


@pytest.mark.parametrize("label,parser,content,options", DHIS2_CASES)
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

    assert json.dumps([entry.to_nrr_record() for entry in first.entries], sort_keys=True) == (
        json.dumps([entry.to_nrr_record() for entry in second.entries], sort_keys=True)
    ), label
    assert first.notes == second.notes, label


@pytest.mark.parametrize("label,parser,content,options", DHIS2_CASES)
def test_keys_are_unique_and_payload_stays_json_serialisable(
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
    assert json.dumps([entry.to_nrr_record() for entry in fragment.entries]), label
