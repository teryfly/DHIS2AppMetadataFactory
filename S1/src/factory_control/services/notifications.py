"""Notifications (CP-NTF-001 … CP-NTF-004).

MVP notifications are project-level: every user sees the same list (D-16).  Email
and webhook delivery is optional per project, retried with exponential backoff up to
``FACTORY_NOTIFICATION_MAX_ATTEMPTS`` attempts (five by default), and its outcome is
recorded on the delivery row.

Credential-secret material never enters a notification body — only Secret
references are stored (CP-NTF-002, 00 doc 6.9).
"""

from __future__ import annotations

import hashlib
import hmac
import json
import smtplib
import urllib.error
import urllib.request
from dataclasses import dataclass
from datetime import timedelta
from email.message import EmailMessage
from typing import Any

from factory_control.config import get_settings
from factory_control.domain.enums import NotificationChannel, NotificationType
from factory_control.foundation import new_id, resolve_secret, utcnow
from factory_control.models import Notification, NotificationDelivery
from factory_control.runtime import ControlContext, Runtime
from factory_control.services.recording import write_audit

#: The retry ceiling is a deployment knob (``FACTORY_NOTIFICATION_MAX_ATTEMPTS``, CP-NTF-002), so
#: it is read per pass: a queued delivery must not stay retryable for a month because the value
#: that was current when the module was imported said five.
BACKOFF_BASE_SECONDS = 60


def _max_attempts() -> int:
    """``notification_max_attempts``, floored at one so a zero cannot strand a retry."""
    return max(1, int(get_settings().notification_max_attempts))


@dataclass(frozen=True)
class ChannelConfig:
    """Where a project wants notifications sent; may come from platform settings."""

    emails: tuple[str, ...] = ()
    webhook_url: str | None = None
    webhook_secret_ref: str | None = None


def notification_view(row: Notification) -> dict[str, Any]:
    return {
        "id": row.id,
        "projectId": row.project_id,
        "type": row.type,
        "objectRef": dict(row.object_ref or {}),
        "message": row.message,
        "read": row.read,
        "createdAt": row.created_at,
    }


def notify(
    ctx: ControlContext,
    *,
    notification_type: NotificationType,
    object_ref: dict[str, Any],
    message: str = "",
    project_id: str | None = None,
) -> Notification:
    """Create one project-level notification and queue its outbound channels."""
    row = Notification(
        id=new_id("ntf"),
        project_id=resolve_project_id(ctx, project_id, object_ref.get("workflowId")),
        type=notification_type.value,
        object_ref=dict(object_ref),
        message=message or notification_type.value,
        read=False,
    )
    ctx.session.add(row)
    ctx.session.flush()
    write_audit(
        ctx,
        event_type="cp.notification.created",
        object_type="Notification",
        object_id=row.id,
        project_id=row.project_id,
        after={"type": row.type, "objectRef": row.object_ref},
    )
    # 19.2.3 declares ``notification.created`` and C-02 makes the SSE stream how S5 learns
    # about a Gate waiting on it: the bell and the stage list refresh without a poll.  The
    # stream is per-workflow, so a notification that names no workflow has no stream to
    # join — the REST read still lists it, which is why the event is skipped rather than
    # the notification dropped.
    workflow_id = ctx.workflow_id or object_ref.get("workflowId")
    if workflow_id:
        from factory_control.services.recording import record_change

        record_change(
            ctx,
            event_type="notification.created",
            audit_event_type="cp.notification.created",
            object_type="Notification",
            object_id=row.id,
            workflow_id=str(workflow_id),
            project_id=row.project_id,
            payload={"type": row.type, "objectRef": dict(row.object_ref or {})},
        )
    if row.project_id is not None:
        queue_channels(ctx, row)
    return row


def resolve_project_id(ctx: ControlContext, explicit: str | None, object_ref_workflow: str | None = None) -> str | None:
    """Project attribution for a notification.

    An approval or rejection is written from a transaction that only knows the
    workflow, so the project is resolved through it — otherwise the project-scoped
    notification list silently loses the entry (CP-NTF-001).
    """
    if explicit is not None:
        return explicit
    if ctx.project_id is not None:
        return ctx.project_id
    for candidate in (ctx.workflow_id, object_ref_workflow):
        if candidate is None:
            continue
        from factory_control.models import Workflow

        workflow = ctx.session.get(Workflow, candidate)
        if workflow is not None:
            return workflow.project_id
    return None


def channels_for(ctx: ControlContext, project_id: str | None) -> ChannelConfig:
    """Project channels, plus the platform list for project-less notifications."""
    from factory_control import store as S

    if project_id is None:
        platform = ctx.settings.platform_channels()
        return ChannelConfig(
            emails=tuple(str(e) for e in (platform.get("emails") or [])),
            webhook_url=platform.get("webhookUrl"),
            webhook_secret_ref=platform.get("webhookSecretRef"),
        )
    settings_row = S.project_settings(ctx.session, project_id)
    platform = ctx.settings.platform_channels()
    return ChannelConfig(
        emails=tuple(str(e) for e in (settings_row.notification_emails or [])),
        webhook_url=None,
        webhook_secret_ref=settings_row.notification_webhook_ref,
    )


def queue_channels(ctx: ControlContext, notification: Notification) -> list[NotificationDelivery]:
    config = channels_for(ctx, notification.project_id)
    queued: list[NotificationDelivery] = []
    for email in config.emails:
        queued.append(_queue(ctx, notification, NotificationChannel.EMAIL, email))
    if config.webhook_secret_ref:
        # The reference is stored per project; the URL itself is platform-level or
        # embedded in the resolved secret payload.
        url = _webhook_url_for(ctx, notification.project_id)
        if url:
            queued.append(_queue(ctx, notification, NotificationChannel.WEBHOOK, url))
    ctx.session.flush()
    return queued


def _webhook_url_for(ctx: ControlContext, project_id: str | None) -> str | None:
    platform = ctx.settings.platform_channels()
    return platform.get("webhookUrl")


def _queue(
    ctx: ControlContext, notification: Notification, channel: NotificationChannel, target: str
) -> NotificationDelivery:
    row = NotificationDelivery(
        id=new_id("ndl"),
        notification_id=notification.id,
        channel=channel.value,
        target=_redact_target(target),
        attempts=0,
        status="PENDING",
        next_attempt_at=utcnow(),
    )
    ctx.session.add(row)
    return row


def _redact_target(target: str) -> str:
    """Never persist a credential-bearing URL fragment."""
    if "://" in target and "@" in target:
        scheme, rest = target.split("://", 1)
        return f"{scheme}://***@{rest.split('@', 1)[1]}"
    return target


def notify_gate_pending(ctx: ControlContext, gate: Any) -> Notification:
    return notify(
        ctx,
        notification_type=NotificationType.GATE_PENDING,
        object_ref={
            "gateId": gate.id,
            "gateType": gate.type,
            "workflowId": gate.workflow_id,
            "stageId": gate.stage_id,
            "deploymentRunId": gate.deployment_run_id,
            "environmentKey": gate.environment_key,
        },
        message=f"{gate.type} is waiting for approval",
    )


def notify_gate_approved(ctx: ControlContext, gate: Any) -> Notification:
    return notify(
        ctx,
        notification_type=NotificationType.GATE_APPROVED,
        object_ref={"gateId": gate.id, "gateType": gate.type, "workflowId": gate.workflow_id},
        message=f"{gate.type} approved",
    )


def notify_gate_rejected(ctx: ControlContext, gate: Any, *, reason: str) -> Notification:
    return notify(
        ctx,
        notification_type=NotificationType.GATE_REJECTED,
        object_ref={"gateId": gate.id, "gateType": gate.type, "workflowId": gate.workflow_id},
        message=f"{gate.type} rejected: {reason}",
    )


def notify_gate_invalidated(ctx: ControlContext, gate: Any) -> Notification:
    return notify(
        ctx,
        notification_type=NotificationType.GATE_INVALIDATED,
        object_ref={"gateId": gate.id, "gateType": gate.type, "workflowId": gate.workflow_id},
        message=f"{gate.type} invalidated ({gate.invalidated_reason or 'bound content changed'})",
    )


def notify_question_created(ctx: ControlContext, *, stage_id: str, questions: list[Any]) -> None:
    if not questions:
        return
    highest = max((q.severity for q in questions), key=_severity_rank, default="LOW")
    notify(
        ctx,
        notification_type=NotificationType.QUESTION_CREATED,
        object_ref={"stageId": stage_id, "questionIds": [q.id for q in questions], "severity": highest},
        message=f"{len(questions)} question(s) need an answer",
    )


def _severity_rank(value: str) -> int:
    return {"HIGH": 3, "MEDIUM": 2, "LOW": 1}.get(str(value).upper(), 0)


def notify_stage_event(ctx: ControlContext, *, stage: Any, kind: str, detail: str = "") -> Notification:
    mapping = {
        "failed": NotificationType.STAGE_FAILED,
        "blocked": NotificationType.STAGE_BLOCKED,
        "manual_check": NotificationType.STAGE_MANUAL_CHECK,
    }
    return notify(
        ctx,
        notification_type=mapping[kind],
        object_ref={"stageId": stage.id, "stageType": stage.stage_type, "workflowId": stage.workflow_id},
        message=detail or f"{stage.stage_type} {kind}",
    )


def notify_execution(ctx: ControlContext, *, ok: bool, execution_id: str | None, environment_key: str, detail: str = "") -> Notification:
    return notify(
        ctx,
        notification_type=NotificationType.EXECUTION_COMPLETED if ok else NotificationType.EXECUTION_FAILED,
        object_ref={"executionId": execution_id, "environmentKey": environment_key},
        message=detail or ("execution completed" if ok else "execution failed"),
    )


def notify_budget(ctx: ControlContext, *, exceeded: bool, detail: str = "") -> Notification:
    return notify(
        ctx,
        notification_type=NotificationType.BUDGET_EXCEEDED if exceeded else NotificationType.BUDGET_THRESHOLD,
        object_ref={"exceeded": exceeded},
        message=detail,
    )


def notify_credentials_expiring(runtime: Runtime, *, items: list[dict[str, Any]]) -> int:
    """CP-NTF-003: raised by the daily schedule, platform-level (no project)."""
    created = 0
    for item in items:
        with runtime.transaction(operator="system") as ctx:
            notify(
                ctx,
                notification_type=NotificationType.CREDENTIAL_EXPIRING,
                object_ref={
                    "environmentKey": item.get("environmentKey"),
                    "secretRef": item.get("secretRef"),
                    "expiresAt": item.get("expiresAt"),
                },
                message=f"credential for {item.get('environmentKey')} is about to expire",
            )
            created += 1
    return created


def notify_audit_integrity_failure(runtime: Runtime, *, detail: dict[str, Any]) -> Notification:
    with runtime.transaction(operator="system") as ctx:
        return notify(
            ctx,
            notification_type=NotificationType.AUDIT_INTEGRITY_FAILED,
            object_ref=dict(detail),
            message="audit hash-chain verification reported a break",
        )


def list_notifications(
    runtime: Runtime,
    *,
    operator: str = "system",
    project_id: str | None = None,
    unread_only: bool = False,
    mine: bool = False,
    limit: int | None = None,
    offset: int | None = None,
) -> dict[str, Any]:
    """CP-NTF-001, with M4's personal half (CP-IAM-002's 个人通知).

    ``mine=true`` keeps the project-level rows every member sees **and** the rows addressed to
    this person — a budget or credential reminder that a Gate must not be missed behind the
    project's own list.  In ``off`` mode there is no account to address, so the filter is
    inert and the answer is the project's list, exactly as before.
    """
    from factory_control import store as S

    with runtime.transaction(operator=operator) as ctx:
        rows, total = S.notifications_of(
            ctx.session, project_id=project_id, unread_only=unread_only, limit=limit, offset=offset
        )
        from factory_control.config import get_settings
        from factory_control.identity import directory

        if mine and get_settings().auth_is_enforced():
            account = directory.by_subject(ctx.session, operator)
            own = account.id if account is not None else ""
            rows = [r for r in rows if r.recipient_user_id in (None, own)] if own else [
                r for r in rows if r.recipient_user_id is None
            ]
            total = len(rows)
        elif mine:
            rows = [r for r in rows if r.recipient_user_id is None]
            total = len(rows)
        return {
            "items": [notification_view(r) for r in rows],
            "total": total,
            "limit": limit or 50,
            "offset": offset or 0,
        }


def mark_read(runtime: Runtime, notification_id: str, *, operator: str) -> dict[str, Any]:
    from factory_control import store as S

    with runtime.transaction(operator=operator) as ctx:
        row = S.get_notification(ctx.session, notification_id)
        was = row.read
        row.read = True
        row.updated_at = utcnow()
        ctx.session.flush()
        if not was:
            write_audit(
                ctx,
                event_type="cp.notification.read",
                object_type="Notification",
                object_id=row.id,
                project_id=row.project_id,
                after={"read": True, "by": operator},
            )
        return notification_view(row)


def gate_reminder_due(
    runtime: Runtime, *, now: Any = None
) -> list[str]:
    """CP-GATE-016: remind after the configured interval, at most three times.

    The interval is per project, falling back to the platform default.
    """
    from sqlalchemy import select

    from factory_control import store as S
    from factory_control.models import Gate, Workflow

    moment = now or utcnow()
    due: list[str] = []
    with runtime.transaction(operator="system") as ctx:
        gates = ctx.session.execute(
            select(Gate).where(Gate.status == "WAITING", Gate.reminder_count < 3)
        ).scalars().all()
        for gate in gates:
            workflow = ctx.session.get(Workflow, gate.workflow_id)
            project_id = workflow.project_id if workflow else None
            hours = ctx.settings.gate_reminder_hours()
            if project_id is not None:
                hours = S.project_settings(ctx.session, project_id).gate_reminder_hours or hours
            elapsed = moment - gate.created_at
            if elapsed >= timedelta(hours=hours * (gate.reminder_count + 1)):
                gate.reminder_count += 1
                notify(
                    ctx,
                    notification_type=NotificationType.GATE_REMINDER,
                    object_ref={"gateId": gate.id, "gateType": gate.type, "reminder": gate.reminder_count},
                    message=f"{gate.type} has been waiting for approval (reminder {gate.reminder_count})",
                    project_id=project_id,
                )
                due.append(gate.id)
        if due:
            ctx.session.flush()
    return due


def deliver_pending(runtime: Runtime, *, transport: Any = None) -> dict[str, int]:
    """Attempt queued deliveries with exponential backoff (CP-NTF-002)."""
    from factory_control import store as S

    stats = {"delivered": 0, "failed": 0, "exhausted": 0}
    max_attempts = _max_attempts()
    with runtime.transaction(operator="system") as ctx:
        pending = S.pending_deliveries(ctx.session, now=utcnow())
        targets = [(d.id, d.channel, d.target, d.attempts) for d in pending]
    for delivery_id, channel, target, attempts in targets:
        ok, error = _send(transport, channel, target, delivery_id, runtime)
        with runtime.transaction(operator="system") as ctx:
            row = ctx.session.get(NotificationDelivery, delivery_id)
            if row is None:
                continue
            row.attempts = attempts + 1
            row.updated_at = utcnow()
            row.last_error = error
            if ok:
                row.status = "DELIVERED"
                row.next_attempt_at = None
                stats["delivered"] += 1
            elif row.attempts >= max_attempts:
                row.status = "FAILED"
                row.next_attempt_at = None
                stats["exhausted"] += 1
            else:
                row.status = "FAILED"
                row.next_attempt_at = utcnow() + timedelta(
                    seconds=BACKOFF_BASE_SECONDS * (2 ** (row.attempts - 1))
                )
                stats["failed"] += 1
            ctx.session.flush()
    return stats


def _send(transport: Any, channel: str, target: str, delivery_id: str, runtime: Runtime) -> tuple[bool, str | None]:
    """Deliver once.  ``transport`` is injected by tests; defaults to real sends."""
    with runtime.transaction(operator="system") as ctx:
        row = ctx.session.get(NotificationDelivery, delivery_id)
        notification = ctx.session.get(Notification, row.notification_id) if row else None
    if notification is None:
        return False, "notification vanished"
    payload = notification_view(notification)
    try:
        if transport is not None:
            return bool(transport(channel, target, payload)), None
        if channel == NotificationChannel.EMAIL.value:
            return _send_email(target, payload), None
        if channel == NotificationChannel.WEBHOOK.value:
            return _send_webhook(runtime, target, payload), None
    except Exception as exc:  # noqa: BLE001 - delivery failures are recorded, not raised
        return False, f"{type(exc).__name__}: {exc}"[:500]
    return False, f"unsupported channel {channel}"


def _send_email(to: str, payload: dict[str, Any]) -> bool:
    """Send over SMTP; the host and credentials come from the platform Secret ref."""
    settings = get_settings()
    if not settings.smtp_secret_ref:
        raise RuntimeError("no SMTP secret configured for this deployment")
    endpoint = json.loads(resolve_secret(settings.smtp_secret_ref).reveal())
    message = EmailMessage()
    message["Subject"] = f"[Factory] {payload['type']}"
    message["From"] = str(endpoint.get("from", "factory@localhost"))
    message["To"] = to
    message.set_content(json.dumps(payload, ensure_ascii=False, default=str))
    with smtplib.SMTP(str(endpoint.get("host", "localhost")), int(endpoint.get("port", 25)), timeout=10) as client:
        if endpoint.get("username"):
            client.login(str(endpoint["username"]), str(endpoint.get("password", "")))
        client.send_message(message)
    return True


def _send_webhook(runtime: Runtime, url: str, payload: dict[str, Any]) -> bool:
    with runtime.transaction(operator="system") as ctx:
        project_id = payload.get("projectId")
        config = channels_for(ctx, project_id)
        body = json.dumps(payload, ensure_ascii=False, default=str).encode("utf-8")
        signature = ""
        if config.webhook_secret_ref:
            secret = resolve_secret(config.webhook_secret_ref)
            signature = hmac.new(secret.reveal().encode("utf-8"), body, hashlib.sha256).hexdigest()
    request = urllib.request.Request(
        url,
        data=body,
        headers={
            "Content-Type": "application/json",
            "X-Factory-Signature": signature,
            "X-Factory-Event": str(payload.get("type")),
        },
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=runtime.settings.webhook_timeout_seconds) as response:
        return 200 <= int(response.status) < 300


__all__ = [
    "ChannelConfig",
    "channels_for",
    "deliver_pending",
    "gate_reminder_due",
    "list_notifications",
    "mark_read",
    "notification_view",
    "notify",
    "notify_audit_integrity_failure",
    "notify_budget",
    "notify_credentials_expiring",
    "notify_execution",
    "notify_gate_approved",
    "notify_gate_invalidated",
    "notify_gate_pending",
    "notify_gate_rejected",
    "notify_question_created",
    "notify_stage_event",
    "queue_channels",
]
