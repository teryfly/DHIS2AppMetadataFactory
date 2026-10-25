"""Configuration loading (FD-COM-008).

Reads from environment variables and an optional TOMX-free ``.env``-style file.
At minimum exposes ``APP_ENV``, ``DATABASE_URL``, ``OBJECT_STORE_URL``,
``SECRET_BACKEND`` and ``OTEL_EXPORTER_OTLP_ENDPOINT``.  Subsystems may declare
their own keys via :func:`register_settings_schema`.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

from factory_foundation.common.errors import FactoryError

# Well-known keys (FD-COM-008 / 00 doc 6.10).
KNOWN_KEYS = (
    "APP_ENV",
    "DATABASE_URL",
    "OBJECT_STORE_URL",
    "SECRET_BACKEND",
    "OTEL_EXPORTER_OTLP_ENDPOINT",
)

_ENV_FILE = Path.cwd() / ".env"


@dataclass(frozen=True)
class Settings:
    app_env: str
    database_url: str
    object_store_url: str
    secret_backend: str
    otel_endpoint: str | None
    raw: dict[str, str] = field(default_factory=dict, hash=False)

    @property
    def is_production(self) -> bool:
        return self.app_env.lower() in {"prod", "production"}

    @property
    def is_development(self) -> bool:
        return self.app_env.lower() in {"dev", "development", "test"}


_declared_keys: dict[str, tuple[str, bool]] = {}  # key -> (owner_subsystem, required)


def register_settings_schema(key: str, *, owner_subsystem: str, required: bool = False) -> None:
    _declared_keys[key] = (owner_subsystem, required)


def _parse_env_file(path: Path) -> dict[str, str]:
    result: dict[str, str] = {}
    if not path.exists():
        return result
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, _, v = line.partition("=")
        result[k.strip()] = v.strip().strip('"').strip("'")
    return result


def load_settings(
    *, env_file: Path | None = None, environ: dict[str, str] | None = None
) -> Settings:
    """Merge (file < environment) and build a :class:`Settings`."""
    env = dict(os.environ if environ is None else environ)
    file_vars = _parse_env_file(env_file or _ENV_FILE)
    merged: dict[str, str] = {**file_vars, **env}

    def get(key: str, default: str | None = None) -> str | None:
        return merged.get(key, default)

    # Validate subsystem-declared required keys.
    for key, (_owner, req) in _declared_keys.items():
        if req and not merged.get(key):
            raise FactoryError(
                f"required setting missing: {key} (owner {_owner})", code="INTERNAL_ERROR"
            )

    return Settings(
        app_env=get("APP_ENV", "development") or "development",
        database_url=get("DATABASE_URL", "") or "",
        object_store_url=get("OBJECT_STORE_URL", "") or "",
        secret_backend=get("SECRET_BACKEND", "env") or "env",
        otel_endpoint=get("OTEL_EXPORTER_OTLP_ENDPOINT"),
        raw={k: v for k, v in merged.items()},
    )
