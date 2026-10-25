"""``GET /api/v1/setup`` contracts (S5IF-SET01).

Declared as typed components rather than one open ``dict`` because the console draws a checklist
from these fields and must not guess that ``inService`` is a number (CP-API-010, S5IF-FLD10 do the
same job for the settings form).  ``intelligence`` is nullable on purpose: it is the answer of a
caller who holds ``governance.manage``, and an absent section would look like zero rows.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import Field

from factory_control.rest.schemas.common import CamelModel


class SetupEngineView(CamelModel):
    name: str | None = None
    durable: bool = False
    detail: dict[str, Any] = Field(default_factory=dict)


class SetupStartupView(CamelModel):
    migrations_mode: str | None = None
    applied_schemas: list[str] = Field(default_factory=list)
    skipped_schemas: list[str] = Field(default_factory=list)
    providers_initialized: list[str] = Field(default_factory=list)
    providers_missing: list[str] = Field(default_factory=list)
    registered: dict[str, int] = Field(default_factory=dict)
    auth_mode: str | None = None


class SetupConfigurationView(CamelModel):
    provider_mode: str | None = None
    workflow_engine: str | None = None
    migrations_mode: str | None = None
    https_only: bool = False
    auth_mode: str | None = None
    #: ``Settings.validate_runtime``'s own sentences; they name environment variables, never values.
    problems: list[str] = Field(default_factory=list)


class SetupIdentityView(CamelModel):
    auth_mode: str | None = None
    enforced: bool = False
    m4_enabled: bool = False
    bootstrap_admin_declared: bool = False
    users: int = 0
    pending_password_change: bool = False


class SetupEnvironmentItem(CamelModel):
    environment_key: str
    environment_class: str | None = None
    #: ``ACTIVE`` or ``DISABLED`` — the same two values S4 uses, and ``DISABLED`` here means the
    #: environment is out of service, which is why its writes answer 409 (S5IF-ENV05).
    status: str
    last_tested_at: datetime | str | None = None


class SetupEnvironmentsView(CamelModel):
    total: int = 0
    in_service: int = 0
    non_production_in_service: int = 0
    #: Keys that exist but have never passed a connection test.  A validation stage refuses them,
    #: so this is the difference between "registered" and "usable".
    never_tested: list[str] = Field(default_factory=list)
    items: list[SetupEnvironmentItem] = Field(default_factory=list)


class SetupIntelligenceView(CamelModel):
    providers: int = 0
    providers_enabled: int = 0
    models: int = 0
    models_enabled: int = 0


class SetupProjectsView(CamelModel):
    total: int = 0
    active: int = 0


class SetupRefHolderView(CamelModel):
    """Where an unresolvable reference is named — an environment scope, a provider, a model."""

    ref: str
    holders: list[str] = Field(default_factory=list)
    #: ``missing`` (no such credential), ``keyMismatch`` (written under another master key),
    #: ``corrupt`` (the blob does not open).  A reason, not a value: this whole section is safe to
    #: render, and the operator's next action differs per reason.
    reason: str


class SetupCredentialsView(CamelModel):
    store_available: bool = False
    master_key_configured: bool = False
    scheme: str = "db"
    named: int = 0
    unresolvable: list[SetupRefHolderView] = Field(default_factory=list)


class SetupView(CamelModel):
    generated_at: datetime | str | None = None
    engine: SetupEngineView | None = None
    #: Which implementation backs each port: ``real`` reaches S2/S3/S4, ``fake`` is the in-process
    #: stand-in that no stage result can be delivered from.
    providers: dict[str, str] = Field(default_factory=dict)
    startup: SetupStartupView | None = None
    configuration: SetupConfigurationView
    identity: SetupIdentityView
    environments: SetupEnvironmentsView
    intelligence: SetupIntelligenceView | None = None
    #: The credential store's own state, or ``null`` for a caller without ``secret.manage``.
    #: ``null`` and "empty" are different facts and the console renders them differently (UI-SET-002).
    credentials: SetupCredentialsView | None = None
    projects: SetupProjectsView
