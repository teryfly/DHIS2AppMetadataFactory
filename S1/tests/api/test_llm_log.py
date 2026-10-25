"""The LLM transcript endpoints (S5IF-INP08/INP09).

The transcript lives in S3; these tests drive the S1 side through the fake port, which is
where the two things S1 owns are checked: the parse status that explains the calls, and the
project/artifact scoping that keeps one project's prompts out of another's log.
"""

from __future__ import annotations

from typing import Any

from factory_control.ports import dto
from tests.helpers import OPERATOR, upload_input


def _fake(harness: Any, project_id: str, artifact_id: str) -> None:
    summary = dto.LlmExchangeSummary(
        exchange_id="aexg-1",
        sequence=1,
        status="OK",
        provider="mock",
        provider_class="LOCAL_ONLY",
        model="mock-agent",
        request_id="req-1",
        input_tokens=12,
        output_tokens=34,
        latency_ms=250,
    )
    fake = harness.runtime.intelligence
    fake.llm_exchanges[(project_id, artifact_id)] = [summary]
    fake.llm_exchange_details["aexg-1"] = dto.LlmExchangeDetail(
        **summary.model_dump(),
        input_artifact_id=artifact_id,
        request=dto.LlmExchangeRequest(
            messages=[
                dto.LlmExchangeMessage(role="system", content="system rules"),
                dto.LlmExchangeMessage(role="user", content="<untrusted>a,b</untrusted>"),
            ],
            schema_name="extraction",
            temperature=0.0,
            max_output_tokens=8000,
        ),
        response=dto.LlmExchangeResponse(
            text='{"items": []}',
            payload={"items": []},
            finish_reason="stop",
            model="mock-agent",
            request_id="req-1",
            usage={"inputTokens": 12, "outputTokens": 34},
        ),
    )


def test_the_log_lists_the_calls_and_the_status_that_explains_them(
    client: Any, harness: Any, seeded_env: dict[str, Any]
) -> None:
    project_id = seeded_env["id"]
    artifact_id = upload_input(client, project_id, "spec")
    response = client.post(
        f"/api/v1/projects/{project_id}/inputs/{artifact_id}/parse",
        headers=OPERATOR,
        json={},
    )
    assert response.status_code == 202, response.text
    _fake(harness, project_id, artifact_id)

    body = client.get(
        f"/api/v1/projects/{project_id}/inputs/{artifact_id}/llm-log", headers=OPERATOR
    ).json()

    assert body["parseStatus"] == "PARSED"
    assert body["parseRunId"].startswith("ipr")
    assert body["total"] == 1
    item = body["items"][0]
    assert item["exchangeId"] == "aexg-1"
    assert item["status"] == "OK"
    assert item["inputTokens"] == 12 and item["outputTokens"] == 34

    detail = client.get(
        f"/api/v1/projects/{project_id}/inputs/{artifact_id}/llm-log/aexg-1",
        headers=OPERATOR,
    ).json()
    assert [message["role"] for message in detail["request"]["messages"]] == ["system", "user"]
    assert detail["request"]["messages"][1]["content"] == "<untrusted>a,b</untrusted>"
    assert detail["response"]["text"] == '{"items": []}'
    assert detail["request"]["schemaName"] == "extraction"


def test_an_unknown_artifact_or_exchange_is_not_found(
    client: Any, harness: Any, seeded_env: dict[str, Any]
) -> None:
    project_id = seeded_env["id"]
    artifact_id = upload_input(client, project_id, "spec")

    missing_artifact = client.get(
        f"/api/v1/projects/{project_id}/inputs/art-nope/llm-log", headers=OPERATOR
    )
    assert missing_artifact.status_code == 404

    missing_exchange = client.get(
        f"/api/v1/projects/{project_id}/inputs/{artifact_id}/llm-log/aexg-nope",
        headers=OPERATOR,
    )
    assert missing_exchange.status_code == 404
