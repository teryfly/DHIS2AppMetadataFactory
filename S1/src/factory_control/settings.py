"""System settings (CP-SET-001).

Runtime tunables live in ``cp.system_settings`` so an operator can change them
without a redeploy; the environment supplies only the initial defaults.  Every
write is audited by the service that performs it.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from factory_control.config import Settings, get_settings
from factory_control.foundation import OperationContext, audit, utcnow
from factory_control.models import SystemSetting

IDEMPOTENCY_RETENTION_HOURS = "idempotency_retention_hours"
EVENT_RETENTION_DAYS = "event_retention_days"
GATE_REMINDER_HOURS = "default_gate_reminder_hours"
MAX_UPLOAD_BYTES = "max_upload_bytes"
ALLOWED_UPLOAD_TYPES = "allowed_upload_types"
ARCHIVED_RETENTION_DAYS = "archived_project_retention_days"
PLATFORM_NOTIFICATION_CHANNELS = "platform_notification_channels"
#: Stage types that start on their own once their predecessor passes.  Manual approval Gates
#: are unaffected: this setting decides *whether to run the next stage*, not whether to approve it.
STAGE_AUTO_CONTINUE = "stage_auto_continue"

SETTING_KEYS: tuple[str, ...] = (
    IDEMPOTENCY_RETENTION_HOURS,
    EVENT_RETENTION_DAYS,
    GATE_REMINDER_HOURS,
    MAX_UPLOAD_BYTES,
    ALLOWED_UPLOAD_TYPES,
    ARCHIVED_RETENTION_DAYS,
    PLATFORM_NOTIFICATION_CHANNELS,
    STAGE_AUTO_CONTINUE,
)

#: Setting key -> the ``cp.audit`` event detail name used when it changes.
AUDITED_KEYS: frozenset[str] = frozenset(SETTING_KEYS)


@dataclass(frozen=True)
class UploadPolicy:
    """CP-API-009 — type whitelist plus size cap, enforced at the upload entry."""

    max_bytes: int
    allowed_types: tuple[str, ...]

    def violation(self, media_type: str | None, size_bytes: int | None) -> str | None:
        """Return a machine-readable reason, or ``None`` when the upload is fine."""
        if size_bytes is not None and size_bytes > self.max_bytes:
            return "SIZE_EXCEEDED"
        normalized = (media_type or "").split(";")[0].strip().lower()
        if not normalized:
            return "MEDIA_TYPE_MISSING"
        allowed = {t.lower() for t in self.allowed_types}
        if normalized not in allowed and not any(
            a.endswith("/*") and normalized.startswith(a[: a.index("/*")]) for a in allowed
        ):
            return "MEDIA_TYPE_NOT_ALLOWED"
        return None


#: Schemes that name a credential store rather than a destination.
_SECRET_SCHEMES = ("vault", "env", "secret", "memory", "awssm")


def _ref_field(value: str, kind: str) -> str:
    """Whether a legacy ``ref`` names a credential or a destination.

    The two are different fields in CP-UI-017 and must not be merged: only ``target`` is
    a place a notice goes, and only ``secretRef`` may name a credential.
    """
    scheme = value.split("://", 1)[0].lower() if "://" in value else ""
    if kind.endswith("_SECRET") or scheme in _SECRET_SCHEMES:
        return "secretRef"
    return "target"


@dataclass
class ResolvedSettings:
    """Everything the services read, with defaults already applied."""

    values: dict[str, Any] = field(default_factory=dict)

    def _int(self, key: str, default: int) -> int:
        raw = self.values.get(key, {}).get("value", default)
        try:
            return int(raw)
        except (TypeError, ValueError):
            return default

    def idempotency_retention_hours(self) -> int:
        return self._int(IDEMPOTENCY_RETENTION_HOURS, 24)

    def event_retention_days(self) -> int:
        return self._int(EVENT_RETENTION_DAYS, 7)

    def gate_reminder_hours(self) -> int:
        return self._int(GATE_REMINDER_HOURS, 24)

    def archived_retention_days(self) -> int:
        return self._int(ARCHIVED_RETENTION_DAYS, 0)

    def auto_continue_stages(self) -> tuple[str, ...]:
        """Stage types that start on their own when READY, in the configured order.

        The first design stage is deliberately never auto-started: REQUIREMENTS is seeded READY
        when a project is created, before any material exists, so running it there would be a
        surprise rather than a continuation.
        """
        from factory_control.domain.enums import StageType

        allowed = {
            StageType.DOMAIN.value,
            StageType.DISCOVERY.value,
            StageType.MAPPING.value,
            StageType.PAGE_DESIGN.value,
            StageType.BASELINE_COMPILE.value,
            StageType.HANDOFF.value,
        }
        raw = self.values.get(STAGE_AUTO_CONTINUE, {}).get("value")
        if isinstance(raw, str):
            entries = [part.strip() for part in raw.split(",")]
        elif isinstance(raw, (list, tuple, set)):
            entries = [str(part).strip() for part in raw]
        else:
            entries = []
        seen: list[str] = []
        for entry in entries:
            value = entry.upper()
            if value in allowed and value not in seen:
                seen.append(value)
        return tuple(seen)

    def upload_policy(self) -> UploadPolicy:
        max_bytes = self._int(MAX_UPLOAD_BYTES, 100 * 1024 * 1024)
        raw = self.values.get(ALLOWED_UPLOAD_TYPES, {}).get("value")
        types: tuple[str, ...]
        if isinstance(raw, list):
            types = tuple(str(t) for t in raw)
        else:
            types = tuple(
                t.strip() for t in str(raw or "").split(",") if t.strip()
            )
        return UploadPolicy(max_bytes=max_bytes, allowed_types=types)

    def platform_channels(self) -> dict[str, Any]:
        raw = self.values.get(PLATFORM_NOTIFICATION_CHANNELS, {}).get("value")
        return dict(raw) if isinstance(raw, dict) else {}

    def notification_channels(self) -> list[dict[str, str]]:
        """The platform channels as CP-UI-017 declares them: ``[{type, target, secretRef}]``.

        The stored value has been written both as that list and as the older
        ``{emails, webhookUrl, webhookSecretRef}`` object that ``defaults_from`` still
        seeds, so a settings screen must not fail because a row predates the current
        spelling.  Where an older entry says only ``ref``, a Secret-reference scheme
        (``vault://``, ``env://``, …) goes to ``secretRef`` and anything else to
        ``target`` — the two must not be confused, because only ``target`` is a destination
        and only ``secretRef`` may name a credential (S5IF-G09).
        """
        raw = self.values.get(PLATFORM_NOTIFICATION_CHANNELS, {}).get("value")
        out: list[dict[str, str]] = []
        if isinstance(raw, list):
            for entry in raw:
                if isinstance(entry, str) and entry:
                    out.append({"type": "EMAIL", "target": entry})
                    continue
                if not isinstance(entry, dict):
                    continue
                kind = str(entry.get("type") or "WEBHOOK")
                item = {"type": kind}
                if entry.get("target"):
                    item["target"] = str(entry["target"])
                if entry.get("secretRef"):
                    item["secretRef"] = str(entry["secretRef"])
                if entry.get("ref"):
                    item[_ref_field(str(entry["ref"]), kind)] = str(entry["ref"])
                out.append(item)
            return out
        if isinstance(raw, dict):
            for email in raw.get("emails") or []:
                if isinstance(email, str) and email:
                    out.append({"type": "EMAIL", "target": email})
            webhook = raw.get("webhookUrl")
            if isinstance(webhook, str) and webhook:
                out.append({"type": "WEBHOOK", "target": webhook})
            secret = raw.get("webhookSecretRef")
            if isinstance(secret, str) and secret:
                out.append({"type": "WEBHOOK_SECRET", "secretRef": secret})
        return out


def defaults_from(process_settings: Settings | None = None) -> dict[str, Any]:
    """The seed values written on first start, taken from the environment."""
    s = process_settings or get_settings()
    return {
        IDEMPOTENCY_RETENTION_HOURS: s.default_idempotency_retention_hours,
        EVENT_RETENTION_DAYS: s.default_event_retention_days,
        GATE_REMINDER_HOURS: s.default_gate_reminder_hours,
        MAX_UPLOAD_BYTES: s.default_max_upload_bytes,
        ALLOWED_UPLOAD_TYPES: s.allowed_upload_types_list(),
        ARCHIVED_RETENTION_DAYS: s.default_archived_project_retention_days,
        PLATFORM_NOTIFICATION_CHANNELS: {
            "emails": [],
            "webhookUrl": None,
            "webhookSecretRef": s.webhook_secret_ref or None,
        },
        STAGE_AUTO_CONTINUE: [],
    }


def seed_missing_settings(*, session: Session, updated_by: str = "system") -> list[str]:
    """Insert any setting row that does not exist yet; returns the keys added."""
    existing = set(session.execute(select(SystemSetting.key)).scalars().all())
    added: list[str] = []
    for key, value in defaults_from().items():
        if key in existing:
            continue
        session.add(SystemSetting(key=key, value={"value": value}, updated_by=updated_by))
        added.append(key)
    if added:
        session.flush()
    return added


def load_settings_snapshot(session: Session) -> ResolvedSettings:
    """Read every setting row once; services pass this around inside a request."""
    rows = session.execute(select(SystemSetting)).scalars().all()
    return ResolvedSettings(values={r.key: dict(r.value or {}) for r in rows})


def set_setting(
    session: Session,
    key: str,
    value: Any,
    *,
    context: OperationContext,
) -> SystemSetting:
    """Write one setting and audit the change (CP-SET-001: 每次变更写审计)."""
    if key not in SETTING_KEYS:
        raise ValueError(f"unknown system setting: {key}")
    row = session.get(SystemSetting, key)
    before = dict(row.value) if row is not None else None
    if row is None:
        row = SystemSetting(key=key, value={"value": value}, updated_by=context.operator_name)
        session.add(row)
    else:
        row.value = {"value": value}
        row.updated_by = context.operator_name
        row.updated_at = utcnow()
    session.flush()
    audit(
        _event("cp.settings.updated", key, before, {"value": value}, context, session),
        session=session,
    )
    return row


def _event(
    event_type: str,
    key: str,
    before: dict[str, Any] | None,
    after: dict[str, Any] | None,
    context: OperationContext,
    session: Session,
) -> Any:
    from factory_control.foundation import AuditEventInput, OperatorType

    _ = session
    return AuditEventInput(
        event_type=event_type,
        object_type="SystemSetting",
        object_id=key,
        project_id=context.project_id,
        before=before,
        after=after,
        source_subsystem="S1",
        operator_type=OperatorType.USER,
        operator_name=context.operator_name,
        correlation_id=context.correlation_id,
    )


__all__ = [
    "ALLOWED_UPLOAD_TYPES",
    "ARCHIVED_RETENTION_DAYS",
    "AUDITED_KEYS",
    "EVENT_RETENTION_DAYS",
    "GATE_REMINDER_HOURS",
    "IDEMPOTENCY_RETENTION_HOURS",
    "MAX_UPLOAD_BYTES",
    "PLATFORM_NOTIFICATION_CHANNELS",
    "ResolvedSettings",
    "SETTING_KEYS",
    "UploadPolicy",
    "defaults_from",
    "load_settings_snapshot",
    "seed_missing_settings",
    "set_setting",
]
