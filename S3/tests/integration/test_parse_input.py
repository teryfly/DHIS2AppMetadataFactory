"""``run_parse`` end to end: real S0 artifacts, the real policy tables, the harness's store.

The unit tests pin the routing decision; these pin everything only a database, an object store
and S0's own guards can prove — that a parse writes an artifact *and* a row *and* an audit event
in one transaction, and never a model where a parser suffices:

* **AI-INP-003 / AI-BR-02** are asserted by arming a recording provider and showing it was
  never called for a CSV, a workbook, a Mermaid file or a DHIS2 export — a test that only
  checked the returned ``used_model`` would pass against a provider that was called anyway;
* **AI-INP-012 / 00 doc 6.4** are asserted against ``core.idempotency_record`` itself: a
  repeated key answers the first result and writes no second artifact, a changed request under
  the same key is a conflict, and a cancelled parse leaves the key unspent;
* **AI-INP-007 / AC-POL-02** are asserted with the provider's call log still empty, because
  "the policy refused" means *nothing left the process*;
* **00 doc 6.2 / D-11** are asserted by the provider recording whether a unit of work was open
  at the moment it was asked to answer — the only way to catch a transaction stretched across
  a model call.

Every artifact is seeded the way production seeds it: ``BUSINESS_INPUT`` created by S1, the NRR
created by S3, because S0's type-ownership guard (FD-ART-002) is part of what is being tested
and a fixture that registered everything as S3's own would sidestep it.
"""

from __future__ import annotations

import io
import json
from collections.abc import Mapping
from typing import Any

import jsonschema
import pytest
from factory_blueprint.api import get_section_schema
from factory_foundation.api import (
    ArtifactCreate,
    ArtifactStatus,
    AuditQuery,
    CancelledError,
    DataClassification,
    IdempotencyConflictError,
    IdempotencyStatus,
    ManualCancellationToken,
    NotFoundError,
    ProgressEvent,
    get_artifact,
    has_active_uow,
    idempotency,
    query_audit,
    register_artifact,
    stage_content,
    unit_of_work,
)
from sqlalchemy import select

from factory_intelligence import api, artifacts
from factory_intelligence.enums import (
    AgentType,
    NrrStatus,
    ParserType,
    ParseStatus,
    PolicyResult,
    ProviderClass,
)
from factory_intelligence.llm.mock_agent import MockAgentProvider, mock_provider
from factory_intelligence.llm.types import (
    Capabilities,
    HealthStatus,
    ProviderInfo,
    StructuredRequest,
)
from factory_intelligence.models.call import ModelCallRow
from factory_intelligence.models.parse import InputParseRow
from factory_intelligence.models.policy import PolicyDecisionRow
from factory_intelligence.parsing import base, dispatch, nrr
from factory_intelligence.parsing.documents.llm_extract import SCHEMA_NAME
from factory_intelligence.parsing.service import MODEL_PARSER_VERSION, run_parse
from factory_intelligence.registries import IdemScope
from factory_intelligence.runtime.progress import (
    STEP_COMPLETED,
    STEP_CONTEXT,
    STEP_MODEL_CALL,
    STEP_ORDER,
    STEP_POLICY,
    STEP_STARTED,
)
from factory_intelligence.testing.harness import IntelligenceHarness
from tests.integration.agent_runtime import unique_project_id as next_project_id
from tests.unit.test_parsers_dhis2 import DHIS2_EXPORT
from tests.unit.test_parsers_diagrams import MERMAID_ER
from tests.unit.test_parsers_tabular import CSV_FIELDS, FIELD_MAPPING, XLSX_FIXTURE, XLSX_MAPPING

OPERATOR = "ops"


#: S1's own upload classification (``factory_control/services/inputs.py:79``), so these tests
#: exercise the level a real business input arrives at rather than a permissive one.
DEFAULT_CLASSIFICATION = DataClassification.CONFIDENTIAL

MD_REQUIREMENTS = "# 登记需求\n\n患儿月龄必须以月为单位记录。\n\n助产士在出院前完成随访登记。\n"

MARKDOWN_ANSWER: dict[str, Any] = {
    "items": [
        {
            "category": "dataRequirement",
            "label": "患儿月龄",
            "statement": "Patient age must be recorded in months.",
            "location": "line:3",
            "confidence": 0.92,
        }
    ]
}

IMAGE_ANSWER: dict[str, Any] = {
    "items": [
        {
            "category": "useCase",
            "label": "Register child",
            "statement": "The midwife registers a child at the facility.",
            "location": "line:1",
            "confidence": 0.8,
            "region": {"shape": "box", "name": "Registration panel"},
        }
    ]
}

PNG_BYTES = b"\x89PNG\r\n\x1a\n" + bytes(range(64))

_REQUIREMENTS_SECTION = get_section_schema("requirements")
REQUIREMENTS_ITEM_SCHEMA: dict[str, Any] = {
    **_REQUIREMENTS_SECTION["properties"]["items"]["items"],
    "definitions": _REQUIREMENTS_SECTION["definitions"],
}

#: The one step ``llm_extract`` and ``vision_extract`` report while they work.  It is theirs,
#: not the orchestration's, so the vocabulary assertions below discount it rather than pretend.
EXTRACTOR_STEP = "extracting"


class _Progress:
    """Every report the parse made, in order (AI-RT-022's parse-side equivalent)."""

    def __init__(self) -> None:
        self.events: list[ProgressEvent] = []

    def report(
        self,
        *,
        step: str,
        completed_fraction: float | None = None,
        message: str | None = None,
        data: dict[str, Any] | None = None,
    ) -> None:
        self.events.append(
            ProgressEvent(
                step=step,
                completed_fraction=completed_fraction,
                message=message,
                data=dict(data or {}),
            )
        )

    @property
    def steps(self) -> list[str]:
        return [event.step for event in self.events]

    @property
    def owned_fractions(self) -> list[float]:
        """The fractions this orchestration reported, in order, ignoring the extractors.

        A report that carried no fraction is absent rather than ``None``: the assertions are
        about the sequence the orchestration actually progressed through.
        """
        out: list[float] = []
        for event in self.events:
            if event.step != EXTRACTOR_STEP and event.completed_fraction is not None:
                out.append(event.completed_fraction)
        return out


class _RecordingProvider:
    """The mock, plus whether a unit of work was open at the moment it was asked to answer.

    00 doc 6.2 makes an external call inside an open transaction a bug rather than a style
    preference, and the only way to catch it is to look while the call is happening.
    """

    def __init__(self, inner: MockAgentProvider) -> None:
        self.inner = inner
        self.uow_during_call: list[bool] = []

    @property
    def info(self) -> ProviderInfo:
        return self.inner.info

    @property
    def call_count(self) -> int:
        return self.inner.call_count

    def generate_structured(self, request: StructuredRequest) -> Any:
        self.uow_during_call.append(has_active_uow())
        return self.inner.generate_structured(request)

    def count_tokens(self, text: str) -> int:
        return self.inner.count_tokens(text)

    def health(self) -> HealthStatus:
        return self.inner.health()


@pytest.fixture
def unique_project_id(harness: IntelligenceHarness) -> str:
    """One project per test: ``core`` is not truncated, so artifact reads must not overlap."""
    _ = harness
    return next_project_id("prj-parse")


def put_input(
    project_id: str,
    content: bytes,
    *,
    filename: str,
    media_type: str,
    classification: DataClassification = DEFAULT_CLASSIFICATION,
    artifact_type: str = "BUSINESS_INPUT",
    creator: str = "S1",
    workflow_id: str | None = None,
) -> str:
    """One business input, staged and registered exactly as S1 uploads it."""
    handle = stage_content(
        io.BytesIO(content),
        project_id=project_id,
        artifact_type=artifact_type,
        media_type=media_type,
    )
    with unit_of_work() as session:
        info = register_artifact(
            handle,
            ArtifactCreate(
                project_id=project_id,
                type=artifact_type,
                media_type=media_type,
                data_classification=classification,
                creator_subsystem=creator,
                workflow_id=workflow_id,
                build_record={"filename": filename, "sizeBytes": len(content)},
                status=ArtifactStatus.APPROVED,
            ),
            session=session,
        )
    return str(info.id)


def register_model(
    *,
    provider: str = "mock",
    provider_class: ProviderClass = ProviderClass.LOCAL_ONLY,
    model: str = "mock-agent",
    vision: bool = False,
) -> None:
    """A provider and a model the extraction may be served by (AI-REG-004)."""
    api.governance(
        "provider.register",
        {
            "name": provider,
            "providerClass": provider_class.value,
            "adapter": "mock",
            "endpoint": "https://mock.invalid/v1",
            "apiKeySecretRef": "env://MOCK_API_KEY",
        },
    )
    api.governance(
        "model.register",
        {
            "provider": provider,
            "model": model,
            "capabilities": {
                "structuredOutput": True,
                "vision": vision,
                "maxContextTokens": 1_000_000,
            },
            "applicableAgents": {"REQUIREMENTS": 1},
        },
    )


def serving_provider(
    provider: str, model: str, provider_class: ProviderClass, *, vision: bool, answer: Mapping[str, Any]
) -> MockAgentProvider:
    inner = mock_provider(
        ProviderInfo(
            provider=provider,
            provider_class=provider_class,
            model=model,
            capabilities=Capabilities(
                structured_output=True, vision=vision, max_context_tokens=1_000_000
            ),
        ),
        scripts={SCHEMA_NAME: [dict(answer)]},
    )
    api.set_llm_provider(inner)
    return inner


def install_mock(
    *,
    provider: str = "mock",
    model: str = "mock-agent",
    vision: bool = False,
    answer: Mapping[str, Any] | None = None,
) -> _RecordingProvider:
    """Register a serving model and install the recording mock for it (AI-MOCK-001).

    ``LOCAL_ONLY`` is deliberate: AI-POL-002's default denies both external classes, so a parse
    that reaches the mock is also proof the policy engine let it.
    """
    register_model(provider=provider, model=model, vision=vision)
    inner = mock_provider(
        ProviderInfo(
            provider=provider,
            provider_class=ProviderClass.LOCAL_ONLY,
            model=model,
            capabilities=Capabilities(
                structured_output=True, vision=vision, max_context_tokens=1_000_000
            ),
        ),
        scripts={SCHEMA_NAME: [dict(answer)]} if answer is not None else {},
    )
    recorder = _RecordingProvider(inner)
    api.set_llm_provider(recorder)
    return recorder


def parse(
    artifact_id: str,
    *,
    project_id: str,
    options: Mapping[str, Any] | None = None,
    key: str | None = None,
    progress: _Progress | None = None,
    cancel: ManualCancellationToken | None = None,
) -> Any:
    return run_parse(
        project_id,
        artifact_id,
        options=options,
        operator=OPERATOR,
        idempotency_key=key or f"parse:{artifact_id}",
        progress=progress,
        cancel=cancel,
    )


def rows(*, project_id: str | None = None) -> list[InputParseRow]:
    with unit_of_work() as session:
        statement = select(InputParseRow).order_by(InputParseRow.id)
        if project_id is not None:
            statement = statement.where(InputParseRow.project_id == project_id)
        return list(session.scalars(statement).all())


def policy_rows(*, project_id: str | None = None) -> list[PolicyDecisionRow]:
    with unit_of_work() as session:
        statement = select(PolicyDecisionRow).order_by(PolicyDecisionRow.id)
        if project_id is not None:
            statement = statement.where(PolicyDecisionRow.project_id == project_id)
        return list(session.scalars(statement).all())


def call_rows(*, project_id: str | None = None) -> list[ModelCallRow]:
    """``ai.model_call`` — the ledger AI-NFR-03 demands a 100% coverage of."""
    with unit_of_work() as session:
        statement = select(ModelCallRow).order_by(ModelCallRow.sequence, ModelCallRow.id)
        if project_id is not None:
            statement = statement.where(ModelCallRow.project_id == project_id)
        return list(session.scalars(statement).all())


def nrr_document(artifact_id: str) -> dict[str, Any]:
    document, _classification = artifacts.read_json(artifact_id)
    return document


def audit(event_type: str, *, project_id: str) -> list[dict[str, Any]]:
    page = query_audit(AuditQuery(event_type=event_type, project_id=project_id, limit=50))
    return [
        {"event_type": record.event_type, "after": record.after, "operator": record.operator_name}
        for record in page.items
    ]


def nrr_artifacts(*, project_id: str) -> list[str]:
    """Every NRR this project has produced, read from the rows that cite them."""
    return sorted(
        {row.nrr_artifact_id for row in rows(project_id=project_id) if row.nrr_artifact_id}
    )


def idem_record(key: str) -> Any:
    with unit_of_work() as session:
        return idempotency.get(IdemScope.INPUT_PARSE, key, session=session)


# --- the deterministic rows, and the model that must not be called ---------------


def test_a_mapped_csv_is_parsed_confirmed_and_calls_no_model(
    harness: IntelligenceHarness, unique_project_id: str
) -> None:
    """AI-INP-003 / AC-REQ-02 in its strongest available form: the provider's own count is zero."""
    _ = harness
    provider = install_mock()
    artifact = put_input(
        unique_project_id, CSV_FIELDS.encode(), filename="字段.csv", media_type="text/csv"
    )

    result = parse(artifact, project_id=unique_project_id, options={"column_mapping": FIELD_MAPPING})

    assert result.status is ParseStatus.PARSED
    assert result.parser_type == ParserType.CSV.value
    assert result.parser_version == "CSV@1.0.0"
    assert result.used_model is False
    assert result.questions == []
    assert result.error_code is None
    assert provider.call_count == 0
    assert call_rows(project_id=unique_project_id) == [], "no call means no bill in the ledger"
    assert result.nrr_artifact_id is not None
    document = nrr_document(str(result.nrr_artifact_id))
    assert document["usedModel"] is False
    assert document["inputHashes"] == {artifact: get_artifact(artifact).content_hash}
    assert document["thresholdsApplied"] == {"extractionConfidence": 0.7, "source": "configuration"}
    assert [item["status"] for item in document["items"]] == ["CONFIRMED"] * len(document["items"])
    assert all(item["confidence"] == 1.0 for item in document["items"]), document["items"]


def test_a_csv_without_a_mapping_is_needs_review_with_one_answerable_question(
    harness: IntelligenceHarness, unique_project_id: str
) -> None:
    """AI-INP-006: the mapping is proposed, one question asks for it, nothing is applied silently."""
    _ = harness
    provider = install_mock()
    artifact = put_input(
        unique_project_id, CSV_FIELDS.encode(), filename="字段.csv", media_type="text/csv"
    )

    result = parse(artifact, project_id=unique_project_id)

    assert result.status is ParseStatus.NEEDS_REVIEW
    assert provider.call_count == 0
    assert len(result.questions) == 1
    question = result.questions[0]
    assert question.local_id == "Q1"
    assert question.severity == "HIGH" and question.is_blocking
    assert "options.column_mapping" in question.text
    #: AI-INP-006 proposes the normalised header as the target, and a CJK header normalises
    #: to itself (``columnmapping.normalise_header``), so only its category is being confirmed.
    assert question.suggestions[0]["columnMapping"] == {"字段": "字段", "类型": "类型", "说明": "说明"}
    assert question.details["artifactId"] == artifact
    document = nrr_document(str(result.nrr_artifact_id))
    assert [item["status"] for item in document["items"]] == ["CANDIDATE"] * len(document["items"])
    assert document["parseNotes"][artifact]["mappingReview"][0]["sheet"] == "字段"
    assert document["questions"][0]["localId"] == "Q1"


def test_answering_the_mapping_question_confirms_the_same_entries(
    harness: IntelligenceHarness, unique_project_id: str
) -> None:
    """AI-INP-006's second half: re-parsing with the mapping confirms instead of asking again."""
    _ = harness
    install_mock()
    artifact = put_input(
        unique_project_id, CSV_FIELDS.encode(), filename="字段.csv", media_type="text/csv"
    )

    first = parse(artifact, project_id=unique_project_id, key="parse:asked")
    second = parse(
        artifact,
        project_id=unique_project_id,
        options={"column_mapping": FIELD_MAPPING},
        key="parse:mapped",
    )

    assert first.status is ParseStatus.NEEDS_REVIEW and first.questions
    assert second.status is ParseStatus.PARSED and second.questions == []
    statuses = [item["status"] for item in nrr_document(str(second.nrr_artifact_id))["items"]]
    assert statuses and set(statuses) == {NrrStatus.CONFIRMED.value}


def test_a_workbook_is_read_by_the_deterministic_parser_with_its_own_coordinates(
    harness: IntelligenceHarness, unique_project_id: str
) -> None:
    """AI-INP-002's 结构化清单 row through the service, with the sheet coordinate AI-INP-005 wants."""
    _ = harness
    provider = install_mock()
    artifact = put_input(
        unique_project_id,
        XLSX_FIXTURE,
        filename="字段清单.xlsx",
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )

    result = parse(artifact, project_id=unique_project_id, options={"column_mapping": XLSX_MAPPING})

    assert result.status is ParseStatus.PARSED
    assert result.parser_type == ParserType.XLSX.value
    assert provider.call_count == 0
    document = nrr_document(str(result.nrr_artifact_id))
    locations = [item["source"]["location"] for item in document["items"]]
    assert any(location.startswith("sheet:") for location in locations), locations


@pytest.mark.parametrize(
    ("filename", "media_type", "content", "expected"),
    [
        ("er.mmd", "text/plain", MERMAID_ER.encode(), ParserType.MERMAID),
        ("meta.json", "application/json", json.dumps(DHIS2_EXPORT).encode(), ParserType.DHIS2_METADATA),
    ],
)
def test_the_other_deterministic_rows_of_ai_inp_002_reach_the_service_without_a_model(
    harness: IntelligenceHarness,
    unique_project_id: str,
    filename: str,
    media_type: str,
    content: bytes,
    expected: ParserType,
) -> None:
    _ = harness
    provider = install_mock()
    artifact = put_input(unique_project_id, content, filename=filename, media_type=media_type)

    result = parse(artifact, project_id=unique_project_id)

    assert result.parser_type == expected.value
    assert result.used_model is False
    assert provider.call_count == 0
    assert result.status in {ParseStatus.PARSED, ParseStatus.NEEDS_REVIEW}
    assert result.nrr_artifact_id is not None


# --- reproducibility and idempotency -------------------------------------------


def test_two_parses_of_one_input_write_the_same_nrr_bytes(
    harness: IntelligenceHarness, unique_project_id: str
) -> None:
    """AI-INP-010 / AC-REQ-01, checked the cheap way: the two artifacts' own content hashes."""
    _ = harness
    install_mock()
    artifact = put_input(
        unique_project_id, CSV_FIELDS.encode(), filename="字段.csv", media_type="text/csv"
    )

    asked = parse(artifact, project_id=unique_project_id, key="parse-1")
    first = parse(
        artifact,
        project_id=unique_project_id,
        options={"column_mapping": FIELD_MAPPING},
        key="parse-2",
    )
    second = parse(
        artifact,
        project_id=unique_project_id,
        options={"column_mapping": FIELD_MAPPING},
        key="parse-3",
    )

    assert asked.nrr_artifact_id != first.nrr_artifact_id
    assert get_artifact(str(first.nrr_artifact_id)).content_hash == get_artifact(
        str(second.nrr_artifact_id)
    ).content_hash
    document = nrr_document(str(second.nrr_artifact_id))
    serialised = json.dumps(document, ensure_ascii=False)
    # AI-INP-010: no clock and no minted identifier may appear inside the content
    assert "createdAt" not in serialised and "timestamp" not in serialised
    assert [item["businessKey"] for item in document["items"]] == sorted(
        item["businessKey"] for item in document["items"]
    )


def test_a_repeated_idempotency_key_replays_the_first_result(
    harness: IntelligenceHarness, unique_project_id: str
) -> None:
    """AI-INP-012: the same key and the same request give the same result, and no second work."""
    _ = harness
    provider = install_mock()
    artifact = put_input(
        unique_project_id, CSV_FIELDS.encode(), filename="字段.csv", media_type="text/csv"
    )

    first = parse(artifact, project_id=unique_project_id, key="parse:once")
    again = parse(artifact, project_id=unique_project_id, key="parse:once")

    assert again == first
    assert again.parse_id == first.parse_id
    assert nrr_artifacts(project_id=unique_project_id) == [str(first.nrr_artifact_id)]
    assert len(rows(project_id=unique_project_id)) == 1
    assert idem_record("parse:once").status is IdempotencyStatus.COMPLETED
    assert provider.call_count == 0


def test_the_same_key_with_a_different_request_is_a_conflict(
    harness: IntelligenceHarness, unique_project_id: str
) -> None:
    """00 doc 6.4: re-using one key for two requests is the caller's bug, not a re-run."""
    _ = harness
    install_mock()
    artifact = put_input(
        unique_project_id, CSV_FIELDS.encode(), filename="字段.csv", media_type="text/csv"
    )
    other = put_input(
        unique_project_id, MERMAID_ER.encode(), filename="er.mmd", media_type="text/plain"
    )

    parse(artifact, project_id=unique_project_id, key="shared")
    with pytest.raises(IdempotencyConflictError):
        parse(other, project_id=unique_project_id, key="shared")


def test_options_may_arrive_as_the_plain_dict_s1_sends(
    harness: IntelligenceHarness, unique_project_id: str
) -> None:
    """``factory_control/services/inputs.py:157`` forwards ``options={}`` verbatim."""
    _ = harness
    install_mock()
    artifact = put_input(
        unique_project_id, CSV_FIELDS.encode(), filename="字段.csv", media_type="text/csv"
    )

    result = parse(artifact, project_id=unique_project_id, options={})

    assert result.status is ParseStatus.NEEDS_REVIEW
    assert rows(project_id=unique_project_id)[0].options == {}


# --- the failures the SRS turns into a status -----------------------------------


def test_an_input_no_row_of_ai_inp_002_covers_comes_back_failed_with_a_next_step(
    harness: IntelligenceHarness, unique_project_id: str
) -> None:
    """AI-INP-003a: a refusal is a result with an instruction in it, never an empty NRR."""
    _ = harness
    install_mock()
    artifact = put_input(
        unique_project_id,
        b"\x00\x01not a format anyone named\x02",
        filename="mystery.bin",
        media_type="application/octet-stream",
    )

    result = parse(artifact, project_id=unique_project_id)

    assert result.status is ParseStatus.FAILED
    assert result.error_code == dispatch.UNMATCHED
    assert result.nrr_artifact_id is None
    assert result.parser_type is None
    for named in ("CSV", "Mermaid", "options.parser"):
        assert named in str(result.error_message)
    assert nrr_artifacts(project_id=unique_project_id) == []
    row = rows(project_id=unique_project_id)[0]
    assert row.status == ParseStatus.FAILED.value
    assert row.parser_type == "none"
    assert row.error_code == dispatch.UNMATCHED
    assert row.input_hash == get_artifact(artifact).content_hash
    assert row.input_bytes == len(b"\x00\x01not a format anyone named\x02")
    assert audit("ai.input.parse_failed", project_id=unique_project_id)


def test_a_native_drawing_file_is_refused_with_the_export_it_asks_for(
    harness: IntelligenceHarness, unique_project_id: str
) -> None:
    """AI-INP-002's 绘图工具 row says export first; the failed parse repeats it (AI-INP-003a)."""
    _ = harness
    install_mock()
    artifact = put_input(
        unique_project_id,
        b"<mxGraphModel><root/></mxGraphModel>",
        filename="flow.drawio",
        media_type="application/xml",
    )

    refused = parse(artifact, project_id=unique_project_id, key="parse:drawio")
    exported = parse(
        artifact,
        project_id=unique_project_id,
        options={"exported_as": "svg"},
        key="parse:drawio-exported",
    )

    assert refused.status is ParseStatus.FAILED
    assert "SVG" in str(refused.error_message) and "PNG" in str(refused.error_message)
    assert exported.parser_type == ParserType.MARKDOWN.value


def test_an_artifact_that_is_not_a_business_input_is_reported_not_parsed(
    harness: IntelligenceHarness, unique_project_id: str
) -> None:
    """Parsing S3's own product would make its answer its own input."""
    _ = harness
    install_mock()
    artifact = put_input(
        unique_project_id, CSV_FIELDS.encode(), filename="字段.csv", media_type="text/csv"
    )
    nrr_id = str(parse(artifact, project_id=unique_project_id).nrr_artifact_id)

    result = parse(nrr_id, project_id=unique_project_id, key="parse:the-nrr")

    assert result.status is ParseStatus.FAILED
    assert result.error_code == dispatch.UNMATCHED
    assert "NRR" in str(result.error_message)


def test_a_scanned_pdf_fails_with_the_mvp_boundary_named(
    harness: IntelligenceHarness, unique_project_id: str
) -> None:
    """AI-INP-003a: no text layer is a loud failure that states the alternative."""
    _ = harness
    from pypdf import PdfWriter

    writer = PdfWriter()
    writer.add_blank_page(width=300, height=300)
    buffer = io.BytesIO()
    writer.write(buffer)
    provider = install_mock()
    artifact = put_input(
        unique_project_id, buffer.getvalue(), filename="scan.pdf", media_type="application/pdf"
    )

    result = parse(artifact, project_id=unique_project_id)

    assert result.status is ParseStatus.FAILED
    assert result.error_code == "SCANNED_DOCUMENT_UNSUPPORTED"
    assert result.parser_type == ParserType.PDF.value
    assert "images" in str(result.error_message)
    assert provider.call_count == 0


def test_another_project_s_artifact_raises_rather_than_leaking_its_content(
    harness: IntelligenceHarness, unique_project_id: str
) -> None:
    """A permission failure is not a parse outcome (00 doc 4.2)."""
    _ = harness
    install_mock()
    artifact = put_input(
        unique_project_id, CSV_FIELDS.encode(), filename="字段.csv", media_type="text/csv"
    )

    with pytest.raises(NotFoundError):
        parse(artifact, project_id="prj-somebody-else")


def test_an_unknown_artifact_raises_and_leaves_the_key_unspent(
    harness: IntelligenceHarness, unique_project_id: str
) -> None:
    """``T1`` rolls back with the metadata read, so a retry is not locked out (AI-INP-012)."""
    _ = harness
    install_mock()

    with pytest.raises(NotFoundError):
        parse("art-does-not-exist", project_id=unique_project_id, key="parse:missing")
    with pytest.raises(NotFoundError):
        parse("art-does-not-exist", project_id=unique_project_id, key="parse:missing")

    assert rows(project_id=unique_project_id) == []
    with pytest.raises(NotFoundError):
        idem_record("parse:missing")


# --- the model-assisted rows, and the policy in front of them -------------------


def test_a_markdown_input_is_extracted_by_the_model_and_stays_unconfirmed(
    harness: IntelligenceHarness, unique_project_id: str
) -> None:
    """AI-INP-002's document row: 确定性文本提取 + LLM 抽取, and 必须复核."""
    _ = harness
    provider = install_mock(answer=MARKDOWN_ANSWER)
    artifact = put_input(
        unique_project_id, MD_REQUIREMENTS.encode(), filename="需求.md", media_type="text/markdown"
    )

    result = parse(artifact, project_id=unique_project_id)

    assert provider.call_count == 1
    assert result.used_model is True
    assert result.parser_type == ParserType.MARKDOWN.value
    assert result.parser_version == f"MARKDOWN@{MODEL_PARSER_VERSION}"
    assert result.status is ParseStatus.NEEDS_REVIEW
    document = nrr_document(str(result.nrr_artifact_id))
    assert document["usedModel"] is True
    assert [item["status"] for item in document["items"]] == ["CANDIDATE"]
    assert document["items"][0]["source"] == {"artifactId": artifact, "location": "line:3"}
    assert document["parseNotes"][artifact]["chunk"] == 0
    assert document["parseNotes"][artifact]["providerClass"] == ProviderClass.LOCAL_ONLY.value


def test_a_timed_out_extraction_is_retried_instead_of_failing_the_parse(
    harness: IntelligenceHarness, unique_project_id: str
) -> None:
    """AI-RT-021 on the parse path: one ReadTimeout must not return the input to the operator.

    The parse path used to call the provider once: a single 300 s timeout failed the whole input,
    with no retry and no usage row for a second attempt. This pins the retry *and* the ledger the
    retried attempt still has to produce.
    """
    _ = harness
    delays: list[float] = []
    api.set_sleeper(delays.append)
    register_model()
    inner = mock_provider(
        ProviderInfo(
            provider="mock",
            provider_class=ProviderClass.LOCAL_ONLY,
            model="mock-agent",
            capabilities=Capabilities(structured_output=True, max_context_tokens=1_000_000),
        ),
        scripts={
            SCHEMA_NAME: [
                {"$error": {"kind": "timeout", "message": "model request timed out after 300.0s"}},
                dict(MARKDOWN_ANSWER),
            ]
        },
    )
    api.set_llm_provider(inner)
    artifact = put_input(
        unique_project_id, MD_REQUIREMENTS.encode(), filename="需求.md", media_type="text/markdown"
    )

    result = parse(artifact, project_id=unique_project_id)

    assert result.status is ParseStatus.NEEDS_REVIEW
    assert inner.call_count == 2, "the timeout was retried, not returned as the parse's answer"
    assert delays == [0.5], "exponential backoff, and no real sleeping in the suite"
    rows = call_rows(project_id=unique_project_id)
    assert [row.outcome for row in rows] == ["ERROR", "OK"], (
        "AI-NFR-03: the failed attempt is booked like the call that replaced it"
    )
    assert "timed out" in (rows[0].error or "")


def test_the_nrr_is_registered_under_the_inputs_workflow(
    harness: IntelligenceHarness, unique_project_id: str
) -> None:
    """The review screen reads NRRs by workflow, so the parse must record that link.

    Before this, the NRR existed in the artifact store with ``workflow_id`` empty and
    ``GET /workflows/{id}/nrr`` answered nothing: the parsed requirement list was invisible
    even though the bytes and the parse row were both present.
    """
    _ = harness
    install_mock(answer=MARKDOWN_ANSWER)
    workflow_id = "wf-parse-workflow"
    artifact = put_input(
        unique_project_id,
        MD_REQUIREMENTS.encode(),
        filename="出院随访.txt",
        media_type="text/plain",
        workflow_id=workflow_id,
    )

    result = parse(artifact, project_id=unique_project_id)

    assert result.nrr_artifact_id
    assert get_artifact(result.nrr_artifact_id).workflow_id == workflow_id


def test_a_model_assisted_parse_books_its_call_in_the_usage_ledger(
    harness: IntelligenceHarness, unique_project_id: str
) -> None:
    """AI-NFR-03 is a percentage over *calls*, not over entry points (AI-LLM-004, AI-COST-001).

    An extraction has no run row and no corrective retry, but it spent project money on a real
    endpoint, so it must be citable in a rollup: ``call_role`` says what kind of call it was,
    ``workflow_id`` says which input it was for, and the audit event names the artifact that
    left the process.
    """
    _ = harness
    install_mock(answer=MARKDOWN_ANSWER)
    artifact = put_input(
        unique_project_id, MD_REQUIREMENTS.encode(), filename="需求.md", media_type="text/markdown"
    )

    result = parse(artifact, project_id=unique_project_id)

    booked = call_rows(project_id=unique_project_id)
    assert len(booked) == 1
    row = booked[0]
    assert result.used_model is True
    assert row.project_id == unique_project_id
    assert row.workflow_id == f"parse:{artifact}"
    assert (row.agent_type, row.call_role, row.outcome) == (
        AgentType.REQUIREMENTS.value,
        "extraction",
        "OK",
    )
    assert (row.provider, row.model, row.provider_class) == (
        "mock",
        "mock-agent",
        ProviderClass.LOCAL_ONLY.value,
    )
    assert row.run_id is None and row.sequence == 1
    assert row.input_tokens + row.output_tokens > 0, "the answer reported its usage"
    assert row.request_id, "AI-LLM-004: every call cites the provider's request id"

    events = audit("ai.model.called", project_id=unique_project_id)
    assert len(events) == 1
    assert events[0]["after"]["contextArtifacts"] == [
        {"ref": f"artifact:{artifact}", "classification": DEFAULT_CLASSIFICATION.value}
    ]


def test_a_refused_extraction_still_books_the_call(
    harness: IntelligenceHarness, unique_project_id: str
) -> None:
    """A request the provider refused was still a request (AI-LLM-004 records 错误 as a field).

    The parse turns the refusal into its own FAILED answer rather than propagating it, which is
    exactly why the row has to be written by the call site: the exception never reaches a
    recorder that runs after ``run_parse``.
    """
    _ = harness
    register_model()
    api.set_llm_provider(
        mock_provider(
            ProviderInfo(
                provider="mock",
                provider_class=ProviderClass.LOCAL_ONLY,
                model="mock-agent",
                capabilities=Capabilities(
                    structured_output=True, vision=False, max_context_tokens=1_000_000
                ),
            ),
            scripts={SCHEMA_NAME: [{"$error": {"kind": "final", "message": "gateway refused"}}]},
        )
    )
    artifact = put_input(
        unique_project_id, MD_REQUIREMENTS.encode(), filename="需求.md", media_type="text/markdown"
    )

    result = parse(artifact, project_id=unique_project_id)

    assert result.status is ParseStatus.FAILED
    assert result.error_code == "LLM_ERROR"
    booked = call_rows(project_id=unique_project_id)
    assert len(booked) == 1
    assert booked[0].outcome == "ERROR"
    assert "gateway refused" in str(booked[0].error)
    assert booked[0].call_role == "extraction"


def test_a_low_confidence_extraction_becomes_a_blocking_question(
    harness: IntelligenceHarness, unique_project_id: str
) -> None:
    """AI-INP-008 / AC-REQ-02: below the project threshold a guess is a Question, not an item."""
    _ = harness
    answer = json.loads(json.dumps(MARKDOWN_ANSWER))
    answer["items"][0]["confidence"] = 0.2
    install_mock(answer=answer)
    artifact = put_input(
        unique_project_id, MD_REQUIREMENTS.encode(), filename="需求.md", media_type="text/markdown"
    )

    result = parse(artifact, project_id=unique_project_id)

    assert result.status is ParseStatus.NEEDS_REVIEW
    assert len(result.questions) == 1
    question = result.questions[0]
    assert question.is_blocking and question.severity == "HIGH"
    assert "0.2" in question.text and "0.7" in question.text
    document = nrr_document(str(result.nrr_artifact_id))
    assert question.affects == [f"requirements.items.{document['items'][0]['businessKey']}"]
    assert document["counts"]["heldBack"] == 1
    assert [item["status"] for item in document["items"]] == ["CANDIDATE"]


def test_an_image_is_extracted_only_by_a_model_that_can_see(
    harness: IntelligenceHarness, unique_project_id: str
) -> None:
    """AI-POL-007: the chosen model must satisfy the policy *and* the capability."""
    _ = harness
    provider = install_mock(vision=True, answer=IMAGE_ANSWER)
    artifact = put_input(unique_project_id, PNG_BYTES, filename="草图.png", media_type="image/png")

    result = parse(artifact, project_id=unique_project_id)

    assert provider.call_count == 1
    assert result.used_model is True
    assert result.parser_type == ParserType.IMAGE.value
    assert result.status is ParseStatus.NEEDS_REVIEW
    document = nrr_document(str(result.nrr_artifact_id))
    assert document["items"][0]["source"]["location"] == "region:box(Registration panel)"


def test_a_picture_is_refused_when_no_registered_model_can_see(
    harness: IntelligenceHarness, unique_project_id: str
) -> None:
    """No text-only fallback: a model that cannot see would answer from the file name."""
    _ = harness
    provider = install_mock(vision=False, answer=IMAGE_ANSWER)
    artifact = put_input(unique_project_id, PNG_BYTES, filename="草图.png", media_type="image/png")

    result = parse(artifact, project_id=unique_project_id)

    assert provider.call_count == 0
    assert result.status is ParseStatus.POLICY_DENIED
    assert "vision" in str(result.error_message)
    assert result.nrr_artifact_id is None


def test_a_phi_input_never_reaches_a_provider(
    harness: IntelligenceHarness, unique_project_id: str
) -> None:
    """AC-POL-02: the refusal is decided before the engine is even consulted."""
    _ = harness
    provider = install_mock(answer=MARKDOWN_ANSWER)
    artifact = put_input(
        unique_project_id,
        MD_REQUIREMENTS.encode(),
        filename="病历.md",
        media_type="text/markdown",
        classification=DataClassification.PHI_PII,
    )

    result = parse(artifact, project_id=unique_project_id)

    assert provider.call_count == 0
    assert result.status is ParseStatus.POLICY_DENIED
    assert result.error_code == "AI_CONTEXT_CONTAINS_PHI"
    assert [item.ref for item in result.rejected_artifacts] == [f"artifact:{artifact}"]
    assert result.rejected_artifacts[0].classification == DataClassification.PHI_PII.value
    assert result.rejected_artifacts[0].reason
    assert result.nrr_artifact_id is None
    assert audit("ai.policy.denied", project_id=unique_project_id)
    assert rows(project_id=unique_project_id)[0].status == ParseStatus.POLICY_DENIED.value


def test_a_deterministic_parse_of_a_phi_input_keeps_the_level_but_not_the_lineage(
    harness: IntelligenceHarness, unique_project_id: str
) -> None:
    """A PHI file a parser can read needs no model, so it parses — and the NRR says so.

    ``data_classification`` is the maximum over every source (FD-ART-008) while a PHI_PII source
    is not named as a parent, so nothing downstream can follow an edge back to it (AC-POL-02,
    AI-POL-005).  Each entry still cites it, which is AI-INP-005's provenance.
    """
    _ = harness
    provider = install_mock()
    artifact = put_input(
        unique_project_id,
        CSV_FIELDS.encode(),
        filename="字段.csv",
        media_type="text/csv",
        classification=DataClassification.PHI_PII,
    )

    result = parse(
        artifact, project_id=unique_project_id, options={"column_mapping": FIELD_MAPPING}
    )

    assert provider.call_count == 0
    assert result.status is ParseStatus.PARSED
    info = get_artifact(str(result.nrr_artifact_id))
    assert info.data_classification is DataClassification.PHI_PII
    assert info.source_artifact_ids == []
    document = nrr_document(str(result.nrr_artifact_id))
    assert {item["source"]["artifactId"] for item in document["items"]} == {artifact}


def test_a_confidential_input_is_refused_when_only_an_external_model_is_registered(
    harness: IntelligenceHarness, unique_project_id: str
) -> None:
    """AI-POL-002's default denies both external classes; the refusal names every model tried."""
    _ = harness
    register_model(provider="external", model="hosted", provider_class=ProviderClass.APPROVED_EXTERNAL)
    inner = serving_provider(
        "external",
        "hosted",
        ProviderClass.APPROVED_EXTERNAL,
        vision=False,
        answer=MARKDOWN_ANSWER,
    )
    artifact = put_input(
        unique_project_id,
        MD_REQUIREMENTS.encode(),
        filename="需求.md",
        media_type="text/markdown",
        classification=DataClassification.CONFIDENTIAL,
    )

    result = parse(artifact, project_id=unique_project_id)

    assert inner.call_count == 0
    assert result.status is ParseStatus.POLICY_DENIED
    assert [item.ref for item in result.rejected_artifacts] == [f"artifact:{artifact}"]
    assert sorted(result.rejected_artifacts[0].allowed_classes) == sorted(
        [ProviderClass.LOCAL_ONLY.value, ProviderClass.PRIVATE_NETWORK.value]
    )
    assert "APPROVED_EXTERNAL" in str(result.error_message)
    assert nrr_artifacts(project_id=unique_project_id) == []


def test_every_judgement_is_recorded_in_ai_policy_decision(
    harness: IntelligenceHarness, unique_project_id: str
) -> None:
    """AI-POL-008: the allowed case is the easy one to forget, so both answers are asserted."""
    _ = harness
    artifact = put_input(
        unique_project_id,
        MD_REQUIREMENTS.encode(),
        filename="需求.md",
        media_type="text/markdown",
        classification=DataClassification.CONFIDENTIAL,
    )
    register_model(provider="external", model="hosted", provider_class=ProviderClass.APPROVED_EXTERNAL)
    serving_provider(
        "external", "hosted", ProviderClass.APPROVED_EXTERNAL, vision=False, answer=MARKDOWN_ANSWER
    )
    parse(artifact, project_id=unique_project_id, key="parse:denied")

    register_model(provider="edge", model="local", provider_class=ProviderClass.PRIVATE_NETWORK)
    serving_provider(
        "edge", "local", ProviderClass.PRIVATE_NETWORK, vision=False, answer=MARKDOWN_ANSWER
    )
    # a different option set is a different question: AI-INP-012 replays the first answer for
    # the same artifact *and* the same options, and this test wants a second judgement
    parse(
        artifact,
        project_id=unique_project_id,
        options={"parser": "MARKDOWN"},
        key="parse:allowed",
    )

    recorded = policy_rows(project_id=unique_project_id)
    assert [row.result for row in recorded] == [
        PolicyResult.DENIED.value,
        PolicyResult.ALLOWED.value,
    ]
    assert all(row.run_id is None for row in recorded)
    assert recorded[0].model_denials and recorded[0].denied_items
    assert recorded[0].items == [{"ref": f"artifact:{artifact}", "classification": "CONFIDENTIAL"}]
    assert recorded[1].provider_class == ProviderClass.PRIVATE_NETWORK.value
    assert recorded[1].model == "local"
    assert recorded[1].denied_items == []
    assert recorded[1].effective_policy["sources"] == ["org"]


def test_no_registered_model_is_reported_as_a_missing_registration(
    harness: IntelligenceHarness, unique_project_id: str
) -> None:
    """AI-REG-004: the next step is to register one, or to use a format that needs no model."""
    _ = harness
    artifact = put_input(
        unique_project_id, MD_REQUIREMENTS.encode(), filename="需求.md", media_type="text/markdown"
    )

    result = parse(artifact, project_id=unique_project_id)

    assert result.status is ParseStatus.FAILED
    assert result.error_code == "AI_PROVIDER_UNAVAILABLE"
    assert "register a provider" in str(result.error_message)
    assert policy_rows(project_id=unique_project_id) == []


# --- transaction discipline, progress, cancellation ----------------------------


def test_nothing_external_happens_inside_a_transaction(
    harness: IntelligenceHarness, unique_project_id: str
) -> None:
    """00 doc 6.2 / D-11: the model call is made in the gap, never under an open UoW."""
    _ = harness
    provider = install_mock(answer=MARKDOWN_ANSWER)
    artifact = put_input(
        unique_project_id, MD_REQUIREMENTS.encode(), filename="需求.md", media_type="text/markdown"
    )

    result = parse(artifact, project_id=unique_project_id)

    assert provider.call_count == 1
    assert provider.uow_during_call == [False]
    assert has_active_uow() is False
    assert result.nrr_artifact_id is not None
    # the artifact, the row, the decision and the audit survived together or not at all
    assert rows(project_id=unique_project_id)[0].nrr_artifact_id == result.nrr_artifact_id
    assert len(policy_rows(project_id=unique_project_id)) == 1
    assert len(audit("ai.input.parsed", project_id=unique_project_id)) == 1


def test_progress_reports_the_runtime_step_vocabulary(
    harness: IntelligenceHarness, unique_project_id: str
) -> None:
    """AI-RT-022's step names are a fixed vocabulary because a UI labels each one (00 doc 6.3)."""
    _ = harness
    install_mock(answer=MARKDOWN_ANSWER)
    artifact = put_input(
        unique_project_id, MD_REQUIREMENTS.encode(), filename="需求.md", media_type="text/markdown"
    )
    progress = _Progress()

    parse(artifact, project_id=unique_project_id, progress=progress)

    assert set(progress.steps) <= set(STEP_ORDER) | {EXTRACTOR_STEP}, progress.steps
    assert progress.steps[0] == STEP_STARTED
    assert progress.steps[-1] == STEP_COMPLETED
    assert {STEP_CONTEXT, STEP_POLICY, STEP_MODEL_CALL} <= set(progress.steps)
    fractions = [float(value) for value in progress.owned_fractions]
    assert fractions == sorted(fractions)
    assert fractions[0] == 0.0 and fractions[-1] == 1.0


def test_a_deterministic_parse_reports_no_model_step(
    harness: IntelligenceHarness, unique_project_id: str
) -> None:
    """The step stream itself proves the model branch was never entered (AI-INP-003)."""
    _ = harness
    install_mock()
    artifact = put_input(
        unique_project_id, MERMAID_ER.encode(), filename="er.mmd", media_type="text/plain"
    )
    progress = _Progress()

    parse(artifact, project_id=unique_project_id, progress=progress)

    assert STEP_MODEL_CALL not in progress.steps and STEP_POLICY not in progress.steps
    assert EXTRACTOR_STEP not in progress.steps
    assert STEP_CONTEXT in progress.steps and STEP_COMPLETED in progress.steps


def test_cancellation_stops_at_a_safe_point_and_leaves_the_key_reusable(
    harness: IntelligenceHarness, unique_project_id: str
) -> None:
    """AI-RT-020 / AI-AC-08: cancellation is not a parse outcome and it spends nothing."""
    _ = harness
    install_mock()
    artifact = put_input(
        unique_project_id, CSV_FIELDS.encode(), filename="字段.csv", media_type="text/csv"
    )
    token = ManualCancellationToken()
    token.cancel("stage abandoned")

    with pytest.raises(CancelledError):
        parse(artifact, project_id=unique_project_id, key="parse:cancelled", cancel=token)

    assert rows(project_id=unique_project_id) == []
    assert idem_record("parse:cancelled").status is IdempotencyStatus.FAILED

    retried = parse(artifact, project_id=unique_project_id, key="parse:cancelled")
    assert retried.status is ParseStatus.NEEDS_REVIEW
    assert len(rows(project_id=unique_project_id)) == 1


def test_a_cancelled_token_is_honoured_before_the_model_is_asked(
    harness: IntelligenceHarness, unique_project_id: str
) -> None:
    """A second safe point after routing, so a long read is still cancellable (AI-RT-020)."""
    _ = harness
    provider = install_mock(answer=MARKDOWN_ANSWER)
    artifact = put_input(
        unique_project_id, MD_REQUIREMENTS.encode(), filename="需求.md", media_type="text/markdown"
    )

    class _CancelAfterRouting(ManualCancellationToken):
        """Flips itself once the parse has read the bytes and chosen a parser."""

        def __init__(self) -> None:
            super().__init__()
            self.checks = 0

        def raise_if_cancelled(self) -> None:
            self.checks += 1
            if self.checks > 1:
                self.cancel("too slow")
            super().raise_if_cancelled()

    with pytest.raises(CancelledError):
        parse(artifact, project_id=unique_project_id, cancel=_CancelAfterRouting())

    assert provider.call_count == 0
    assert rows(project_id=unique_project_id) == []


# --- the NRR's own shape --------------------------------------------------------


def test_the_nrr_entries_validate_against_s2_requirements_schema(
    harness: IntelligenceHarness, unique_project_id: str
) -> None:
    """AI-INP-005: the NRR schema *is* S2's ``requirements.items``, so S2 is the validator."""
    _ = harness
    install_mock(answer=MARKDOWN_ANSWER)
    csv_artifact = put_input(
        unique_project_id, CSV_FIELDS.encode(), filename="字段.csv", media_type="text/csv"
    )
    deterministic = parse(
        csv_artifact,
        project_id=unique_project_id,
        options={"column_mapping": FIELD_MAPPING},
        key="p:csv",
    )
    model_artifact = put_input(
        unique_project_id, MD_REQUIREMENTS.encode(), filename="需求.md", media_type="text/markdown"
    )
    extracted = parse(model_artifact, project_id=unique_project_id, key="p:md")

    categories = {member.value for member in base.NrrCategory}
    for result in (deterministic, extracted):
        document = nrr_document(str(result.nrr_artifact_id))
        assert document["nrrVersion"] == nrr.NRR_VERSION
        assert document["inputArtifactIds"] == [result_input(document)]
        assert document["parserVersions"]
        for item in document["items"]:
            jsonschema.validate(instance=_section_item(item), schema=REQUIREMENTS_ITEM_SCHEMA)
            assert item["category"] in categories
            assert item["source"]["artifactId"] and item["source"]["location"]
            assert 0.0 <= float(item["confidence"]) <= 1.0


def test_the_parse_row_the_build_record_and_the_audit_agree_with_the_result(
    harness: IntelligenceHarness, unique_project_id: str
) -> None:
    """AI-INP-004: one parser identity in the row, the artifact and the audit event."""
    _ = harness
    install_mock()
    artifact = put_input(
        unique_project_id, CSV_FIELDS.encode(), filename="字段.csv", media_type="text/csv"
    )
    input_info = get_artifact(artifact)

    result = parse(
        artifact,
        project_id=unique_project_id,
        options={"column_mapping": FIELD_MAPPING, "sheet": "字段清单"},
    )

    row = rows(project_id=unique_project_id)[0]
    assert row.id == result.parse_id
    assert row.project_id == unique_project_id
    assert row.input_artifact_id == artifact
    assert row.input_hash == input_info.content_hash
    assert row.input_bytes == input_info.size
    assert row.parser_type == ParserType.CSV.value
    assert row.parser_version == "1.0.0"
    assert row.used_model is False
    assert row.status == ParseStatus.PARSED.value
    assert row.nrr_artifact_id == result.nrr_artifact_id
    assert row.idempotency_key == f"parse:{artifact}"
    assert row.operator == OPERATOR
    assert row.options == {"column_mapping": FIELD_MAPPING, "sheet": "字段清单"}

    events = audit("ai.input.parsed", project_id=unique_project_id)
    assert len(events) == 1
    after = events[0]["after"]
    assert after["status"] == ParseStatus.PARSED.value
    assert after["parserType"] == ParserType.CSV.value
    assert after["parserVersion"] == "CSV@1.0.0"
    assert after["usedModel"] is False
    assert after["nrrArtifactId"] == result.nrr_artifact_id
    assert after["dataClassification"] == DEFAULT_CLASSIFICATION.value
    assert after["items"] == len(nrr_document(str(result.nrr_artifact_id))["items"])
    assert events[0]["operator"] == OPERATOR
    assert get_artifact(str(result.nrr_artifact_id)).type == nrr.NRR_ARTIFACT_TYPE
    assert get_artifact(str(result.nrr_artifact_id)).source_artifact_ids == [artifact]


def result_input(document: Mapping[str, Any]) -> str:
    """The one input artifact a parse of this build cites (SRS 7 takes a single artifact)."""
    ids = document["inputArtifactIds"]
    assert len(ids) == 1, ids
    return str(ids[0])


def _section_item(record: Mapping[str, Any]) -> dict[str, Any]:
    """Drop the two keys the NRR carries and S2's section forbids (``additionalProperties``)."""
    return {key: value for key, value in record.items() if key not in {"category", "payload"}}
