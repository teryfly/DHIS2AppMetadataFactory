"""Notification transports (CP-NTF-002): the real send paths and their failures.

Webhooks are signed and emails read their credentials from a platform Secret.  Both
secrets must stay out of the payload, the recorded error and the logs, and a failed
send must become a retryable delivery row rather than an exception.
"""

from __future__ import annotations

import hashlib
import hmac
import json
from typing import Any

import pytest

from factory_control.config import Settings
from factory_control.foundation import store_secret
from factory_control.services import notifications as N
from factory_control.services.notifications import (
    NotificationType,
    deliver_pending,
    notify,
)
from helpers import OPERATOR, make_client

pytestmark = pytest.mark.service

WEBHOOK_URL = "https://hooks.invalid.factory/delivery"


class _FakeResponse:
    def __init__(self, status: int = 202) -> None:
        self.status = status

    def __enter__(self) -> _FakeResponse:
        return self

    def __exit__(self, *_exc: object) -> bool:
        return False


class _Recorder:
    def __init__(self, *, error: Exception | None = None) -> None:
        self.requests: list[Any] = []
        self.error = error

    def __call__(self, request: Any, timeout: float | None = None) -> _FakeResponse:
        self.requests.append(request)
        if self.error is not None:
            raise self.error
        return _FakeResponse()


def _queue_a_notification(harness: Any, seeded: dict[str, Any]) -> str:
    with harness.runtime.transaction(operator="system", project_id=seeded["id"]) as ctx:
        row = notify(
            ctx,
            notification_type=NotificationType.STAGE_BLOCKED,
            object_ref={"workflowId": seeded["workflowId"], "stageId": seeded["currentStageId"]},
            message="the agent was refused",
            project_id=seeded["id"],
        )
        return str(row.id)


def _enable_webhook(client: Any, harness: Any, seeded: dict[str, Any], secret_ref: str | None) -> None:
    configured = client.put(
        "/api/v1/settings",
        json={"platform_notification_channels": {"webhookUrl": WEBHOOK_URL, "emails": []}},
        headers=OPERATOR,
    )
    assert configured.status_code == 200, configured.text
    body: dict[str, Any] = {"notificationWebhookRef": secret_ref, "notificationEmails": []}
    saved = client.put(f"/api/v1/projects/{seeded['id']}/settings", json=body, headers=OPERATOR)
    assert saved.status_code == 200, saved.text


def test_webhook_is_signed_with_the_project_secret(
    harness: Any, seeded: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    client = make_client(harness)
    secret_ref = store_secret("factory/test/webhook-secret", "otp-signing-key")
    _enable_webhook(client, harness, seeded, secret_ref)
    _queue_a_notification(harness, seeded)

    recorder = _Recorder()
    monkeypatch.setattr(N.urllib.request, "urlopen", recorder)
    stats = deliver_pending(harness.runtime)
    assert stats["delivered"] == 1

    assert len(recorder.requests) == 1
    request = recorder.requests[0]
    assert request.full_url == WEBHOOK_URL
    body = request.data
    signed = hmac.new(b"otp-signing-key", body, hashlib.sha256).hexdigest()
    assert request.get_header("X-factory-signature") == signed
    assert request.get_header("X-factory-event") == NotificationType.STAGE_BLOCKED.value
    payload = json.loads(body)
    assert payload["projectId"] == seeded["id"]
    # the Secret is never carried in the clear, and no content beyond INTERNAL is sent
    assert "otp-signing-key" not in body.decode("utf-8")
    assert "content" not in json.dumps(payload).lower()


def test_a_webhook_without_a_signing_secret_is_never_sent(
    harness: Any, seeded: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Delivery is unsigned, so it is refused rather than sent in the clear."""
    client = make_client(harness)
    _enable_webhook(client, harness, seeded, None)
    _queue_a_notification(harness, seeded)

    recorder = _Recorder()
    monkeypatch.setattr(N.urllib.request, "urlopen", recorder)
    stats = deliver_pending(harness.runtime)
    assert stats["delivered"] == 0 and recorder.requests == []


def test_a_failed_send_is_recorded_and_backed_off(
    harness: Any, seeded: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    import urllib.error

    client = make_client(harness)
    secret_ref = store_secret("factory/test/webhook-secret-retry", "second-key")
    _enable_webhook(client, harness, seeded, secret_ref)
    notification_id = _queue_a_notification(harness, seeded)

    recorder = _Recorder(error=urllib.error.URLError("connection refused"))
    monkeypatch.setattr(N.urllib.request, "urlopen", recorder)
    stats = deliver_pending(harness.runtime)
    assert stats == {"delivered": 0, "failed": 1, "exhausted": 0}

    retried = deliver_pending(harness.runtime)  # not due yet: nothing is attempted again
    assert retried["delivered"] == 0 and len(recorder.requests) == 1

    listed = client.get(f"/api/v1/notifications?projectId={seeded['id']}").json()["items"]
    assert any(item["id"] == notification_id for item in listed)
    with harness.runtime.transaction(operator="system") as ctx:
        from factory_control.models import NotificationDelivery

        row = ctx.session.query(NotificationDelivery).one()
        assert row.status == "FAILED" and row.attempts == 1
        assert "URLError" in str(row.last_error) and "second-key" not in str(row.last_error)


def test_email_delivery_reads_its_credentials_from_the_secret(
    harness: Any, seeded: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    client = make_client(harness)
    ref = store_secret(
        "factory/test/smtp",
        json.dumps(
            {
                "host": "smtp.invalid",
                "port": 2525,
                "username": "factory",
                "password": "smtp-pass",
                "from": "factory@invalid",
            }
        ),
    )
    monkeypatch.setattr(N, "get_settings", lambda: Settings(smtp_secret_ref=ref))
    saved = client.put(
        f"/api/v1/projects/{seeded['id']}/settings",
        json={"notificationEmails": ["ops@invalid"], "notificationWebhookRef": None},
        headers=OPERATOR,
    )
    assert saved.status_code == 200, saved.text
    _queue_a_notification(harness, seeded)

    sent: dict[str, Any] = {}

    class _FakeSMTP:
        def __init__(self, host: str, port: int, timeout: float | None = None) -> None:
            sent["endpoint"] = (host, port)

        def __enter__(self) -> _FakeSMTP:
            return self

        def __exit__(self, *_exc: object) -> bool:
            return False

        def login(self, user: str, password: str) -> None:
            sent["login"] = (user, password)

        def send_message(self, message: Any) -> None:
            sent["message"] = message

    monkeypatch.setattr(N.smtplib, "SMTP", _FakeSMTP)
    assert deliver_pending(harness.runtime)["delivered"] == 1
    assert sent["endpoint"] == ("smtp.invalid", 2525)
    assert sent["login"] == ("factory", "smtp-pass")
    assert sent["message"]["To"] == "ops@invalid"
    assert sent["message"]["From"] == "factory@invalid"
    assert NotificationType.STAGE_BLOCKED.value in str(sent["message"]["Subject"])
    # the message body carries the notification, never the credentials
    assert "smtp-pass" not in str(sent["message"])


def test_email_without_a_configured_secret_fails_the_delivery_not_the_job(
    harness: Any, seeded: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    client = make_client(harness)
    monkeypatch.setattr(N, "get_settings", lambda: Settings(smtp_secret_ref=""))
    saved = client.put(
        f"/api/v1/projects/{seeded['id']}/settings",
        json={"notificationEmails": ["ops@invalid"], "notificationWebhookRef": None},
        headers=OPERATOR,
    )
    assert saved.status_code == 200, saved.text
    _queue_a_notification(harness, seeded)

    stats = deliver_pending(harness.runtime)
    assert stats["delivered"] == 0 and stats["failed"] == 1
