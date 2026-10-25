"""The `X-Operator` transport rule (CP-API-002) — a Chinese operator name is the normal case here.

Header values are limited to ISO-8859-1 by the Fetch standard, and Chromium refuses the request
outright rather than mangling the name. Both consoles therefore percent-encode a name that does not
fit; this is the side that reads it back. The bug this file guards is silent on an ASCII test name:
the page reports "cannot reach the server", and the operator's own identity is what broke it.
"""

from __future__ import annotations

from typing import Any

from fastapi.testclient import TestClient

from factory_control.rest.middleware import decode_operator
from factory_control.testing.harness import ControlHarness

ZH = "张三"
ZH_ENCODED = "%E5%BC%A0%E4%B8%89"


def test_names_that_fit_in_a_header_travel_unchanged() -> None:
    assert decode_operator("ada") == "ada"
    assert decode_operator("Ana Ruiz") == "Ana Ruiz"
    assert decode_operator(None) == ""
    assert decode_operator("  ada  ") == "ada"


def test_a_percent_sign_that_is_not_an_escape_is_part_of_the_name() -> None:
    """Decoding what the client never encoded would invent an identity (and drop a real one)."""
    assert decode_operator("100%") == "100%"
    assert decode_operator("50%off") == "50%off"
    assert decode_operator("%zz") == "%zz"
    # undecodable bytes are not a name to guess at; the value arrives as it was sent
    assert decode_operator("%FF%FE") == "%FF%FE"


def test_an_encoded_name_arrives_as_the_name_the_operator_typed() -> None:
    assert decode_operator(ZH_ENCODED) == ZH
    assert decode_operator("ana-%E5%BC%A0%E4%B8%89") == f"ana-{ZH}"


def test_a_curl_that_sends_the_name_as_utf8_bytes_is_read_back_as_that_name() -> None:
    """`curl -H 'X-Operator: 张三'` cannot be percent-encoded by the caller, and the docs tell people to type it.

    Starlette decodes header bytes as ISO-8859-1, so what reaches us is `å¼ ä¸‰`; the audit row would
    otherwise carry a name no operator ever wrote, and the search by operator name finds nothing.
    """
    assert decode_operator(ZH.encode("utf-8").decode("latin-1")) == ZH
    assert decode_operator("Renée".encode().decode("latin-1")) == "Renée"
    # a true Latin-1 byte is not a mangled UTF-8 name, and is not repaired into one
    assert decode_operator("Renée") == "Renée"
    # ASCII is untouched either way
    assert decode_operator("ada") == "ada"


def test_a_state_change_made_with_a_chinese_name_is_audited_under_that_name(
    harness: ControlHarness, client: TestClient
) -> None:
    harness.execution.add_environment("dev", environment_class="TEST")
    created = client.post(
        "/api/v1/projects",
        headers={"X-Operator": ZH_ENCODED},
        json={
            "name": "中文操作人",
            "ownerName": ZH,
            "deliveryMode": "A",
            "targets": [{"environmentKey": "dev", "isDesignReference": True, "promotionOrder": 1}],
        },
    )
    assert created.status_code == 201, created.text

    # the audit page is cursor-paged and omits `total` on purpose (S5IF-G04), so assert on items
    rows = client.get(f"/api/v1/audit?operatorName={ZH}", headers={"X-Operator": "ada"}).json()
    assert rows["items"], rows
    assert {row["operatorName"] for row in rows["items"]} == {ZH}
    # and nothing was stored half-decoded: the identity in the ledger is the name, not the escape
    assert all("%" not in row["operatorName"] for row in rows["items"]), rows["items"]


def test_a_plain_ascii_name_still_answers_without_any_encoding(harness: ControlHarness, client: TestClient) -> None:
    """The rule is additive: clients that never encode keep working byte for byte."""
    harness.execution.add_environment("dev", environment_class="TEST")
    response = client.post(
        "/api/v1/projects",
        headers={"X-Operator": "ada"},
        json={
            "name": "ascii owner",
            "ownerName": "ada",
            "deliveryMode": "A",
            "targets": [{"environmentKey": "dev", "isDesignReference": True, "promotionOrder": 1}],
        },
    )
    assert response.status_code == 201, response.text
    body: dict[str, Any] = client.get("/api/v1/audit?operatorName=ada", headers={"X-Operator": "ada"}).json()
    assert body["items"], body
