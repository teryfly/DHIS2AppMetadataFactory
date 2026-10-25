"""Process configuration from the environment (FD-COM-008, CP-RT-001).

Only *deployment* knobs live here.  Runtime tunables that an operator may change
without a restart — retention periods, reminder hours, upload limits — are rows
in ``cp.system_settings`` (CP-SET-001) seeded from the defaults below.
"""

from __future__ import annotations

import re
from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

MIGRATIONS_MODES = ("apply", "check", "off")
ENGINE_MODES = ("inprocess", "temporal")
PROVIDER_MODES = ("auto", "fakes", "real")

#: Who the request is, and who may do what (CP-IAM-001/002, FR-SEC-001).
#:
#: ``off`` is the MVP and remains the default, so a deployment that has not adopted M4 behaves
#: exactly as it did: the declared ``X-Operator`` name is the identity and the policy allows
#: everything.  ``local``/``oidc``/``both`` authenticate every request and enforce the role
#: table, and every one of those modes requires HTTPS.
AUTH_MODES = ("off", "local", "oidc", "both")

#: The SSE channel's allowed shape (CP-EVT-005).
#:
#: ``FACTORY_SSE_CHANNEL`` names a PostgreSQL channel: it is written into ``LISTEN`` by
#: :func:`factory_control.events.listener.NotificationListener.run` and into ``pg_notify()`` by
#: :func:`factory_control.events.event_log.emit_notify`.  A name outside this pattern is not a
#: channel the server will ever deliver on — the listener used to fail *inside* an awaited task
#: nobody checked, so the whole push path died without a word.  Validated at start-up instead
#: (:meth:`Settings.validate_runtime`), which both ``factory-api`` and ``factory-worker`` run.
#: The pattern is the same one ``listener._quoted`` enforces, so the two cannot disagree.
SSE_CHANNEL_PATTERN = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")

#: Below this many characters a master key is guessable by anyone holding the ciphertext, and the
#: encrypted column would be theatre rather than protection.  ``install.sh`` generates 44.
MIN_CREDENTIAL_MASTER_KEY_CHARS = 32


class Settings(BaseSettings):
    """``FACTORY_*`` environment variables, plus the shared S0 variables."""

    model_config = SettingsConfigDict(
        env_prefix="FACTORY_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    app_env: str = "dev"

    # Shared with the foundation (no FACTORY_ prefix there, so accept both).
    database_url: str = Field(default="", validation_alias="DATABASE_URL")
    object_store_url: str = Field(default="memory://", validation_alias="OBJECT_STORE_URL")
    secret_backend: str = Field(default="memory", validation_alias="SECRET_BACKEND")
    otel_endpoint: str = Field(default="", validation_alias="OTEL_EXPORTER_OTLP_ENDPOINT")

    # Assembly (CP-RT-001..004).
    migrations_mode: str = Field(default="apply")
    workflow_engine: str = Field(default="inprocess")
    provider_mode: str = Field(default="auto")
    fake_environments: str = Field(
        default="", description="dev-only: 'dev:TEST,prod:PRODUCTION' registers these on the S4 fake"
    )
    temporal_target: str = Field(default="localhost:7233")
    temporal_namespace: str = Field(default="factory")
    temporal_task_queue: str = Field(default="factory-main")
    api_host: str = Field(default="0.0.0.0")
    api_port: int = Field(default=8080)
    https_only: bool = Field(default=False, description="404-safe flag used by docs and checks")

    # Defaults seeded into cp.system_settings (CP-SET-001).
    default_idempotency_retention_hours: int = Field(default=24)
    default_event_retention_days: int = Field(default=7)
    default_gate_reminder_hours: int = Field(default=24)
    default_max_upload_bytes: int = Field(default=100 * 1024 * 1024)
    default_allowed_upload_types: str = Field(
        default="application/pdf,text/csv,text/plain,application/vnd.openxmlformats-officedocument."
        "wordprocessingml.document,application/vnd.openxmlformats-officedocument.spreadsheetml.sheet,"
        "application/json,text/markdown,application/msword"
    )
    default_archived_project_retention_days: int = Field(default=0)

    # Notification channels (CP-NTF-002).  Values are Secret references.
    smtp_secret_ref: str = Field(default="")
    webhook_secret_ref: str = Field(default="")
    webhook_timeout_seconds: float = Field(default=10.0)
    notification_max_attempts: int = Field(default=5)

    # SSE fan-out (CP-EVT-005).
    sse_channel: str = Field(default="factory_cp_events")
    sse_poll_seconds: float = Field(default=15.0)
    sse_heartbeat_seconds: float = Field(default=20.0)

    # Activity retry policy (CP-WF-009).
    activity_max_attempts: int = Field(default=5)
    activity_initial_interval_seconds: float = Field(default=2.0)
    activity_backoff_coefficient: float = Field(default=2.0)

    # Audit scheduling (CP-SET-002).
    audit_link_interval_seconds: int = Field(default=5)
    credential_expiry_warning_days: int = Field(default=14)

    # Identity and permissions (CP-IAM-002, FR-SEC-001/005).  Values, not secrets: the OIDC
    # client secret is a Secret *reference*, resolved by S0 like every other credential.
    auth_mode: str = Field(default="off", description="off | local | oidc | both")
    session_idle_minutes: int = Field(default=30, description="FR-SEC-005's idle timeout")
    session_absolute_hours: int = Field(default=12, description="the clock an idle slide cannot move")
    cookie_name: str = Field(default="factory_session")
    #: The CSRF value is mirrored into a cookie the page may *read* (not HttpOnly) so a reloaded
    #: browser can still name the header the middleware demands; the session cookie stays
    #: unreadable.  SameSite=Strict and HTTPS keep a cross-site page from reading either.
    csrf_cookie_name: str = Field(default="factory_csrf")
    csrf_header: str = Field(default="X-CSRF-Token")
    oidc_issuer: str = Field(default="", description="https:// only; the discovery doc is read from it")
    oidc_client_id: str = Field(default="")
    oidc_client_secret_ref: str = Field(default="", description="a S0 Secret reference, never a value")
    oidc_auto_provision: bool = Field(
        default=False, description="create an account on first OIDC sign-in; off means an administrator does"
    )
    bootstrap_admin_subject: str = Field(
        default="",
        description="local account created at boot if absent, so a fresh deployment can be administered",
    )
    bootstrap_admin_secret_ref: str = Field(
        default="", description="its password as a Secret reference; never logged, never returned"
    )
    #: The key that opens ``cp.secrets`` (CP-SEC-001).  Empty means the credential store is off:
    #: the endpoints answer 503 and nothing about the rest of the deployment changes.  It is read
    #: once at boot and never written to the database, an audit row or a log line — the same
    #: posture as ``FACTORY_BOOTSTRAP_ADMIN_SECRET_REF``, and for the same reason.
    credential_master_key: str = Field(
        default="",
        validation_alias="FACTORY_CREDENTIAL_MASTER_KEY",
        description="opens the encrypted credential store; empty disables it",
    )
    m4_features_enabled: bool = Field(
        default=False,
        description="CP-UI-023: deliver the M4 writes (members, restricted grants, exemptions) "
        "ahead of the milestone; off means they answer 409 FEATURE_NOT_ENABLED",
    )

    def auth_is_enforced(self) -> bool:
        """Whether requests must be authenticated and permissions are real (anything but ``off``)."""
        return self.auth_mode in ("local", "oidc", "both")

    def m4_features_are_live(self) -> bool:
        """The one test for M4 writes (CP-UI-023).

        Enforced identity means the M4 feature is by definition deployed — the gate
        exists for MVP deployments that predate it, not for ones that have adopted
        authentication and expect ``member.manage`` to work.
        """
        return self.m4_features_enabled or self.auth_is_enforced()

    def allowed_upload_types_list(self) -> list[str]:
        return [t.strip() for t in self.default_allowed_upload_types.split(",") if t.strip()]

    def credential_store_available(self) -> bool:
        """Whether ``db://`` credentials can be written or read at all (CP-SEC-001).

        A short key counts as absent rather than present: booting with a key that cannot resist an
        offline guess would report "configured" while quietly protecting nothing, and the operator
        would find out from a leaked backup rather than from the console.
        """
        return len(self.credential_master_key.strip()) >= MIN_CREDENTIAL_MASTER_KEY_CHARS

    def validate_runtime(self) -> list[str]:
        """Return human-readable configuration problems; empty means OK."""
        problems: list[str] = []
        if self.migrations_mode not in MIGRATIONS_MODES:
            problems.append(f"FACTORY_MIGRATIONS_MODE must be one of {MIGRATIONS_MODES}")
        if self.workflow_engine not in ENGINE_MODES:
            problems.append(f"FACTORY_WORKFLOW_ENGINE must be one of {ENGINE_MODES}")
        if self.provider_mode not in PROVIDER_MODES:
            problems.append(f"FACTORY_PROVIDER_MODE must be one of {PROVIDER_MODES}")
        if not SSE_CHANNEL_PATTERN.match(self.sse_channel):
            # Both NOTIFY and LISTEN read this one value, so a name that is not an identifier is
            # not a channel: the listener used to raise inside a task nobody awaited (CP-EVT-005).
            problems.append(
                "FACTORY_SSE_CHANNEL must be a PostgreSQL identifier: letters, digits and "
                "underscore only, and it must not start with a digit"
            )
        if not self.database_url:
            problems.append("DATABASE_URL is required")
        if self.auth_mode not in AUTH_MODES:
            problems.append(f"FACTORY_AUTH_MODE must be one of {AUTH_MODES}")
        elif self.auth_is_enforced():
            # FR-SEC-005 is unconditional about HTTPS once sessions exist: a cookie sent in clear
            # is a credential handed to whoever can see the wire.
            if not self.https_only:
                problems.append("FACTORY_HTTPS_ONLY must be true when FACTORY_AUTH_MODE is not 'off'")
            if self.session_idle_minutes < 1 or self.session_absolute_hours < 1:
                problems.append("session timeouts must be at least one minute/hour")
        if self.auth_mode in ("oidc", "both"):
            if not self.oidc_issuer.startswith("https://"):
                problems.append("FACTORY_OIDC_ISSUER must be an https:// URL")
            if not self.oidc_client_id:
                problems.append("FACTORY_OIDC_CLIENT_ID is required in OIDC mode")
            if not self.oidc_client_secret_ref:
                problems.append("FACTORY_OIDC_CLIENT_SECRET_REF is required in OIDC mode")
        if bool(self.bootstrap_admin_subject) != bool(self.bootstrap_admin_secret_ref):
            problems.append(
                "FACTORY_BOOTSTRAP_ADMIN_SUBJECT and FACTORY_BOOTSTRAP_ADMIN_SECRET_REF are set together"
            )
        if self.credential_master_key.strip() and not self.credential_store_available():
            # set but too short to mean anything — worse than unset, because it reads as done
            problems.append(
                f"FACTORY_CREDENTIAL_MASTER_KEY is shorter than {MIN_CREDENTIAL_MASTER_KEY_CHARS} "
                "characters; the credential store stays disabled (generate one with "
                "`python3 -c 'import secrets;print(secrets.token_urlsafe(32))'`)"
            )
        return problems


@lru_cache
def get_settings() -> Settings:
    return Settings()


def reset_settings_cache() -> None:
    get_settings.cache_clear()


__all__ = [
    "ENGINE_MODES",
    "MIGRATIONS_MODES",
    "PROVIDER_MODES",
    "Settings",
    "SSE_CHANNEL_PATTERN",
    "get_settings",
    "reset_settings_cache",
]
