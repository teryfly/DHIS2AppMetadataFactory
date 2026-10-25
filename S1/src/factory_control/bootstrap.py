"""Process start-up (CP-RT-004) and S1 registration.

Order is mandated by 00 doc 6.10 and is not negotiable:

1. logging and tracing;
2. migrations — ``factory-api`` only, in the fixed order ``core → dx → bp → ai → cp``,
   in ``apply`` / ``check`` / ``off`` mode;
3. ``initialize_foundation()``;
4. ``initialize()`` of S4, then S2, then S3 (only for installed providers);
5. S1's own registration: artifact types, audit event types, error codes, ID prefixes.

Steps 2–4 matter because S2/S3/S4 are not published yet: an absent provider is
skipped with a recorded note rather than a crash, so ``factory-api`` boots for
development against the interim fakes.
"""

from __future__ import annotations

import importlib
from dataclasses import dataclass, field
from typing import Any

from factory_control.foundation import (
    CORE_MIGRATION_CONFIG,
    MigrationConfig,
    current_revision,
    initialize_foundation,
    register_artifact_types,
    register_audit_event_types,
    register_error_codes,
    register_id_prefixes,
    run_migrations,
    verify_foundation_contract,
)

#: Provider packages in the order 00 doc 6.10 initialises them.
PROVIDER_PACKAGES: tuple[tuple[str, str], ...] = (
    ("factory_dhis2", "S4"),
    ("factory_blueprint", "S2"),
    ("factory_intelligence", "S3"),
)

MIGRATIONS_APPLY = "apply"
MIGRATIONS_CHECK = "check"
MIGRATIONS_OFF = "off"


@dataclass
class StartupReport:
    """What actually happened, so ``/healthz`` and logs can say it out loud."""

    migrations_mode: str
    applied_schemas: list[str] = field(default_factory=list)
    skipped_schemas: list[str] = field(default_factory=list)
    providers_initialized: list[str] = field(default_factory=list)
    providers_missing: list[str] = field(default_factory=list)
    registered: dict[str, int] = field(default_factory=dict)
    #: ``off`` means declared-operator identity and an allow-everything policy (CP-IAM-001)
    auth_mode: str = "off"

    def as_dict(self) -> dict[str, Any]:
        return {
            "migrationsMode": self.migrations_mode,
            "appliedSchemas": list(self.applied_schemas),
            "skippedSchemas": list(self.skipped_schemas),
            "providersInitialized": list(self.providers_initialized),
            "providersMissing": list(self.providers_missing),
            "registered": dict(self.registered),
            "authMode": self.auth_mode,
        }


def s1_migration_config() -> MigrationConfig:
    """Deferred import so the Alembic script dir is resolved from the installed package."""
    from factory_control.db import CP_MIGRATION_CONFIG

    return CP_MIGRATION_CONFIG


def initialize(*, session: Any = None) -> dict[str, int]:
    """Register S1's artifact types, audit events, error codes and ID prefixes.

    Idempotent: the foundation merges catalogs (FD-ASM-01).  Callers that already
    hold a unit of work pass ``session`` so registration joins that transaction.
    """
    from factory_control.registrations import (
        S1_ARTIFACT_TYPES,
        S1_AUDIT_EVENT_TYPES,
        S1_ID_PREFIXES,
        s1_error_codes,
    )

    # Asked for here, after the providers have registered: the mapping filters on the live
    # catalog, so an earlier snapshot would claim codes that belong upstream.
    error_codes = s1_error_codes()
    register_error_codes(error_codes)
    register_id_prefixes(S1_ID_PREFIXES)
    register_artifact_types(S1_ARTIFACT_TYPES, session=session)
    register_audit_event_types(S1_AUDIT_EVENT_TYPES, session=session)
    return {
        "artifactTypes": len(S1_ARTIFACT_TYPES),
        "auditEventTypes": len(S1_AUDIT_EVENT_TYPES),
        "errorCodes": len(error_codes),
        "idPrefixes": len(S1_ID_PREFIXES),
    }


def _provider_migration_config(package: str) -> MigrationConfig | None:
    """``MIGRATION_CONFIG`` of an installed provider, or ``None`` if absent."""
    try:
        module = importlib.import_module(f"{package}.api")
    except ImportError:
        return None
    cfg = getattr(module, "MIGRATION_CONFIG", None)
    return cfg if isinstance(cfg, MigrationConfig) else None


def _call_provider_initialize(package: str) -> bool:
    try:
        module = importlib.import_module(f"{package}.api")
    except ImportError:
        return False
    init = getattr(module, "initialize", None)
    if callable(init):
        init()
    return True


def run_migrations_for(
    configs: list[MigrationConfig], *, mode: str, url: str | None = None
) -> tuple[list[str], list[str]]:
    applied: list[str] = []
    skipped: list[str] = []
    if mode == MIGRATIONS_OFF:
        return applied, [c.schema for c in configs]
    if mode == MIGRATIONS_CHECK:
        for cfg in configs:
            head = _expected_head(cfg)
            actual = current_revision(cfg, url=url) if head else None
            if head and actual != head:
                raise RuntimeError(
                    f"schema {cfg.schema} is at revision {actual!r}, expected {head!r}"
                )
            skipped.append(cfg.schema)
        return applied, skipped
    run_migrations(configs, url=url)
    return [c.schema for c in configs], skipped


def _expected_head(cfg: MigrationConfig) -> str | None:
    """Highest revision id in the schema's script directory, without running Alembic.

    The id is the script's ``revision`` attribute, not its file name: Alembic
    stores what the script declares (``0001_cp_initial``), and ``check`` mode
    compares against ``current_revision()``.
    """
    import re
    from pathlib import Path

    versions = Path(cfg.script_location) / "versions"
    if not versions.is_dir():
        return None
    names = sorted(p.name for p in versions.glob("*.py"))
    if not names:
        return None
    text = (versions / names[-1]).read_text(encoding="utf-8")
    declared = re.search(r"^revision(?::\s*str)?\s*=\s*[\"']([^\"']+)[\"']", text, re.MULTILINE)
    return declared.group(1) if declared else names[-1].split("_", 1)[0]


def _register_credential_backend() -> None:
    """Teach the secret resolver about ``db://`` when the deployment has a master key (CP-SEC-001).

    Deliberately *not* folded into ``install_development_backends()`` above: that call is what
    ``APP_ENV=production`` refuses, and an encrypted credential store is precisely the thing a
    production deployment would want.  It also runs after the migrations, because a backend that
    reads a table before the table exists would turn a first boot into a stack trace.

    Absent or short key means no backend at all — not a failing one.  Existing environments keep
    resolving ``env://`` references exactly as before, ``GET /setup`` reports
    ``credentials.masterKeyConfigured: false``, and the two write endpoints answer 503.
    """
    from factory_control.config import get_settings
    from factory_control.credentials.backend import install_master_key_backend

    install_master_key_backend(get_settings())


def install_secret_backends(target: Any = None) -> Any:
    """Register the secret backends this process is allowed to have (FD-SEC-001).

    ``install_development_backends()`` is what a development start-up calls, and it refuses
    outright under ``APP_ENV=production`` — the ``env`` backend is development-only, and a
    production process must not inherit one from an earlier phase.  Until now that refusal was
    the *only* thing bootstrap did about secrets, so setting ``APP_ENV=production`` made
    ``factory-api`` crash on the way up instead of starting with the backends production is
    allowed to have.

    Production still needs the writable ``memory`` backend: it is where ``store_secret()`` without
    a scheme writes FD-SEC-004's one-time values, and the resolver refuses to guess when there is
    no writable backend at all.  So the production branch registers exactly that one, and nothing
    else.  The ``db://`` credential backend is installed on both paths by
    :func:`_register_credential_backend`, because an encrypted credential store is precisely what
    a production deployment wants; ``vault``/``aws``/``gcp`` are not installed here — a deployment
    that needs them registers its own transport, and an unreadable ``env://`` reference is a
    visible 503 rather than a silently honoured default.

    ``target`` exists for tests: passing a resolver keeps the assertion off the process singleton.
    """
    from factory_control.foundation import (
        MemorySecretBackend,
        install_development_backends,
        load_settings,
        register_secret_backend,
    )

    if not load_settings().is_production:
        return install_development_backends(target)
    if target is not None:
        target.register(MemorySecretBackend())
        return target
    return register_secret_backend(MemorySecretBackend())


def bootstrap(
    *,
    database_url: str,
    migrations_mode: str = MIGRATIONS_APPLY,
    object_store_url: str = "memory://",
    install_logging: bool = True,
) -> StartupReport:
    """Full start-up sequence for ``factory-api`` (CP-RT-004)."""
    from factory_control.db import configure_database

    verify_foundation_contract()

    if install_logging:
        from factory_control.foundation import configure_logging, configure_tracing

        configure_logging()
        configure_tracing()

    from factory_control.foundation import configure_object_store_from_url

    configure_object_store_from_url(object_store_url)
    install_secret_backends()

    configure_database(database_url)

    configs: list[MigrationConfig] = [CORE_MIGRATION_CONFIG]
    report = StartupReport(migrations_mode=migrations_mode)
    for package, label in PROVIDER_PACKAGES:
        cfg = _provider_migration_config(package)
        if cfg is not None:
            configs.append(cfg)
        else:
            report.providers_missing.append(label)

    # S1's own schema is last in the canonical order (core → dx → bp → ai → cp).
    configs.append(s1_migration_config())
    applied, skipped = run_migrations_for(configs, mode=migrations_mode, url=database_url)
    report.applied_schemas = applied
    report.skipped_schemas = skipped

    _register_credential_backend()

    initialize_foundation()

    # 00 doc 6.10: S4, then S2, then S3.
    for package, label in PROVIDER_PACKAGES:
        if _call_provider_initialize(package):
            report.providers_initialized.append(label)

    from factory_control.foundation import unit_of_work

    with unit_of_work() as session:
        report.registered = initialize(session=session)
        from factory_control.settings import seed_missing_settings

        seed_missing_settings(session=session)

    report.auth_mode = install_identity()
    return report


def install_identity() -> str:
    """Choose the policy the handlers will call, and open the deployment (CP-IAM-001/002).

    Called after migrations and registration because it reads and writes ``cp`` rows.  Returns
    the mode as a string so ``/healthz`` can say out loud whether permissions are real — a
    deployment that believed it was authenticated while running ``off`` would be the quiet
    failure this function exists to make visible.
    """
    from factory_control.config import get_settings

    config = get_settings()
    from factory_control import iam

    if config.auth_is_enforced():
        from factory_control.identity.policy import RolePermissionPolicy

        iam.set_permission_policy(RolePermissionPolicy())
    else:
        # stated rather than left to the module default, so a test that installed another
        # policy in-process cannot leak into the next start-up
        iam.set_permission_policy(iam.AllowAllPolicy())
    if config.auth_is_enforced():
        _ensure_bootstrap_admin(config)
    return config.auth_mode


def _ensure_bootstrap_admin(config: Any) -> None:
    """The one account a fresh deployment is administered with, if it has no users at all.

    Without it, ``auth_mode=local`` would be a locked door with the key inside: every endpoint
    needs a session, and creating the first user needs a session.  The password comes from a
    Secret reference (FR-SEC-006), the account is flagged ``mustChangePassword`` so the default
    cannot survive a first login, and an existing account of that name is never touched.
    """
    from factory_control.foundation import resolve_secret, unit_of_work
    from factory_control.identity import directory, service
    from factory_control.identity.roles import PLATFORM_ADMINISTRATOR

    subject = config.bootstrap_admin_subject
    if not subject:
        return
    with unit_of_work() as session:
        if directory.get_by_subject_any_status(session, subject) is not None:
            return
        password = resolve_secret(config.bootstrap_admin_secret_ref).reveal()
        created = service.create_user(
            session, "system", subject=subject, password=password, must_change_password=True
        )
        service.assign_role(session, "system", created["id"], PLATFORM_ADMINISTRATOR)


__all__ = [
    "MIGRATIONS_APPLY",
    "install_identity",
    "install_secret_backends",
    "MIGRATIONS_CHECK",
    "MIGRATIONS_OFF",
    "PROVIDER_PACKAGES",
    "StartupReport",
    "bootstrap",
    "initialize",
    "s1_migration_config",
]
