"""The parse-time LLM exchange log (operational transcript).

The transcript is deliberately separate from ``ai.model_call``: the ledger stays
content-free (AI-LLM-004 / AI-DEL-001), while this table quotes the input so an operator can
see why a parse failed.  What is pinned here is that it is written per attempt, only for
parses, with image bytes left in the call, scoped to its input, and purged on expiry.
"""

from __future__ import annotations

import json
from typing import Any

import pytest
from factory_foundation.api import unit_of_work
from sqlalchemy import select

from factory_intelligence import api
from factory_intelligence.enums import ParseStatus, ProviderClass
from factory_intelligence.llm.mock_agent import mock_provider
from factory_intelligence.llm.types import Capabilities, ProviderInfo
from factory_intelligence.models.exchange import LlmExchangeRow
from factory_intelligence.parsing.documents.llm_extract import SCHEMA_NAME
from factory_intelligence.parsing.service import run_parse
from tests.integration.agent_runtime import unique_project_id as next_project_id
from tests.integration.test_parse_input import (
    IMAGE_ANSWER,
    MARKDOWN_ANSWER,
    MD_REQUIREMENTS,
    PNG_BYTES,
    install_mock,
    put_input,
    register_model,
)


@pytest.fixture
def project_id(harness: Any) -> str:
    _ = harness
    return next_project_id("prj-llmlog")


def _parse(artifact_id: str, *, project_id: str) -> Any:
    return run_parse(
        project_id,
        artifact_id,
        options={},
        operator="ops",
        idempotency_key=f"parse:{artifact_id}",
    )


def _exchanges(project_id: str, artifact_id: str) -> list[LlmExchangeRow]:
    with unit_of_work() as session:
        return list(
            session.execute(
                select(LlmExchangeRow)
                .where(
                    LlmExchangeRow.project_id == project_id,
                    LlmExchangeRow.input_artifact_id == artifact_id,
                )
                .order_by(LlmExchangeRow.created_at, LlmExchangeRow.id)
            ).scalars()
        )


def test_a_model_parse_records_the_request_and_the_raw_answer(
    harness: Any, project_id: str
) -> None:
    _ = harness
    install_mock(answer=MARKDOWN_ANSWER)
    artifact = put_input(
        project_id, MD_REQUIREMENTS.encode(), filename="需求.md", media_type="text/markdown"
    )

    result = _parse(artifact, project_id=project_id)

    assert result.status is ParseStatus.NEEDS_REVIEW
    page = api.list_llm_exchanges(project_id, artifact)
    assert page.total == 1
    summary = page.items[0]
    assert summary.status == "OK"
    assert summary.sequence == 1
    assert summary.input_tokens > 0 and summary.output_tokens > 0
    assert summary.latency_ms is not None

    detail = api.get_llm_exchange(project_id, artifact, summary.exchange_id)
    assert detail is not None
    assert [message.role for message in detail.request.messages] == ["system", "user"]
    # the fenced chunk is the input the model actually received
    assert "<untrusted" in detail.request.messages[-1].content
    assert detail.response is not None
    assert detail.response.text and "dataRequirement" in detail.response.text
    assert detail.response.payload is not None
    assert detail.expires_at is not None


def test_every_transport_attempt_is_its_own_exchange(harness: Any, project_id: str) -> None:
    """The retry policy re-issues the call; the log must show both attempts, not just the last."""
    _ = harness
    register_model()
    provider = mock_provider(
        ProviderInfo(
            provider="mock",
            provider_class=ProviderClass.LOCAL_ONLY,
            model="mock-agent",
            capabilities=Capabilities(structured_output=True, max_context_tokens=1_000_000),
        ),
        scripts={
            SCHEMA_NAME: [
                {"$error": {"kind": "timeout", "message": "model request timed out"}},
                dict(MARKDOWN_ANSWER),
            ]
        },
    )
    api.set_llm_provider(provider)
    artifact = put_input(
        project_id, MD_REQUIREMENTS.encode(), filename="需求.md", media_type="text/markdown"
    )

    result = _parse(artifact, project_id=project_id)

    assert result.status is ParseStatus.NEEDS_REVIEW
    rows = _exchanges(project_id, artifact)
    assert [row.status for row in rows] == ["ERROR", "OK"]
    assert [row.sequence for row in rows] == [1, 2]
    assert rows[0].error_code == "LLM_TIMEOUT"
    assert rows[0].request_id
    assert rows[1].response_text


def test_image_bytes_never_reach_the_log(harness: Any, project_id: str) -> None:
    _ = harness
    install_mock(vision=True, answer=IMAGE_ANSWER)
    artifact = put_input(
        project_id, PNG_BYTES, filename="草图.png", media_type="image/png"
    )

    result = _parse(artifact, project_id=project_id)

    assert result.status is ParseStatus.NEEDS_REVIEW
    page = api.list_llm_exchanges(project_id, artifact)
    detail = api.get_llm_exchange(project_id, artifact, page.items[0].exchange_id)
    assert detail is not None
    images = [image for message in detail.request.messages for image in message.images]
    assert len(images) == 1
    assert images[0].media_type == "image/png"
    assert images[0].size_bytes == len(PNG_BYTES)
    assert "iVBOR" not in json.dumps(detail.request.model_dump(mode="json")), (
        "the base64 payload must stay in the call, not in the transcript"
    )


def test_the_log_is_scoped_to_its_input_and_purged_on_expiry(
    harness: Any, project_id: str
) -> None:
    _ = harness
    install_mock(answer=MARKDOWN_ANSWER)
    artifact = put_input(
        project_id, MD_REQUIREMENTS.encode(), filename="需求.md", media_type="text/markdown"
    )
    _parse(artifact, project_id=project_id)
    exchange_id = api.list_llm_exchanges(project_id, artifact).items[0].exchange_id

    assert api.list_llm_exchanges("prj-other", artifact).total == 0
    assert api.get_llm_exchange(project_id, "art-other", exchange_id) is None
    assert api.get_llm_exchange("prj-other", artifact, exchange_id) is None

    with unit_of_work() as session:
        row = session.get(LlmExchangeRow, exchange_id)
        assert row is not None
        row.expires_at = row.created_at  # already due
    assert api.purge_llm_exchanges() == 1
    assert api.list_llm_exchanges(project_id, artifact).total == 0


def test_a_project_delete_removes_its_transcripts(harness: Any, project_id: str) -> None:
    from factory_intelligence.cleanup import delete_project_data

    _ = harness
    install_mock(answer=MARKDOWN_ANSWER)
    artifact = put_input(
        project_id, MD_REQUIREMENTS.encode(), filename="需求.md", media_type="text/markdown"
    )
    _parse(artifact, project_id=project_id)
    assert api.list_llm_exchanges(project_id, artifact).total == 1

    delete_project_data(project_id)

    assert api.list_llm_exchanges(project_id, artifact).total == 0
