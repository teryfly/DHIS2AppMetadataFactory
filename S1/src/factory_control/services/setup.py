"""``GET /api/v1/setup`` — the facts a cold start needs (S5IF-SET01).

A fresh install leaves the operator in front of two consoles that both look correctly built and
empty, and nothing in either one can say why.  The server does know: :meth:`Settings.validate_runtime`
already produces a human-readable list of configuration problems and then uses it exactly once, to
fail fast at boot; the real/fake mode of each port is reported only on ``/healthz``, which nginx does
not proxy; and whether anything has been registered yet required the console to ask four other
endpoints and count rows itself.

This module answers with **facts, not verdicts**.  Which control is *available* keeps coming from
``allowed-actions`` alone (CP-UI-019, FR-UI-009, UI-BR-01) — deciding "what should I do next" from
those facts is presentation, and presentation belongs to the console (总体 SRS P-06).  Nothing here
writes, and nothing here calls DHIS2: every environment read is S4's own persisted row, the same
read ``GET /environments/{key}`` already performs inside a GET (S5IF-FLD05).
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import func, select

from factory_control import iam
from factory_control import store as S
from factory_control.models import User
from factory_control.runtime import Runtime
from factory_control.services import environments as ENV
from factory_control.services import governance as GOV
from factory_control.services import secrets as CREDENTIALS


def _may(operator: str, action: str) -> bool:
    """Whether ``operator`` may read the detail behind one section.

    The endpoint is granted on ``project.view`` (the same read right the platform
    ``allowed-actions`` answer asks for), but governance rows belong to ``governance.manage``.
    Rather than refusing the whole page to someone who is mid-setup, the section they may not see
    is left out — and the console is never told a number it could not have read.
    """
    return iam.get_permission_policy().is_allowed(operator=operator, project_id=None, action=action)


def _engine_view(runtime: Runtime) -> dict[str, Any] | None:
    if runtime.engine is None:
        return None
    return dict(vars(runtime.engine.status()))


def _provider_modes(runtime: Runtime) -> dict[str, str]:
    from factory_control.adapters.registry import provider_modes

    return provider_modes(
        {
            "blueprint": runtime.blueprint,
            "intelligence": runtime.intelligence,
            "execution": runtime.execution,
        }
    )


def _startup_view() -> dict[str, Any] | None:
    from factory_control.api_service import get_startup_report

    report = get_startup_report()
    return report.as_dict() if report is not None else None


def _identity_view(runtime: Runtime, *, operator: str, user_id: str) -> dict[str, Any]:
    settings = runtime.settings
    view: dict[str, Any] = {
        "authMode": settings.auth_mode,
        "enforced": settings.auth_is_enforced(),
        "m4Enabled": settings.m4_features_are_live(),
        "bootstrapAdminDeclared": bool(settings.bootstrap_admin_subject),
    }
    with runtime.read_only(operator=operator) as ctx:
        view["users"] = int(ctx.session.execute(select(func.count()).select_from(User)).scalar() or 0)
        account = ctx.session.get(User, user_id) if user_id else None
    view["pendingPasswordChange"] = bool(account is not None and account.must_change_password)
    return view


def _environments_view(runtime: Runtime) -> dict[str, Any]:
    rows = runtime.execution.list_environments()
    items: list[dict[str, Any]] = []
    never_tested: list[str] = []
    for info in sorted(rows, key=lambda r: str(r.environment_key)):
        key = str(info.environment_key)
        in_service = bool(info.enabled) and str(info.status or "").upper() != "DISABLED"
        record = ENV.last_connection_record(runtime, key) if in_service else None
        last_tested_at = (record or {}).get("lastTestedAt")
        if in_service and last_tested_at is None:
            never_tested.append(key)
        items.append(
            {
                "environmentKey": key,
                "environmentClass": (info.environment_class or "").upper() or None,
                "status": "DISABLED" if not in_service else "ACTIVE",
                "lastTestedAt": last_tested_at,
            }
        )
    in_service_count = sum(1 for item in items if item["status"] == "ACTIVE")
    non_production = sum(
        1
        for item in items
        if item["status"] == "ACTIVE" and (item["environmentClass"] or "") != "PRODUCTION"
    )
    return {
        "total": len(items),
        "inService": in_service_count,
        "nonProductionInService": non_production,
        "neverTested": sorted(never_tested),
        "items": items,
    }


def _intelligence_view(runtime: Runtime) -> dict[str, Any]:
    providers = GOV.providers(runtime)["items"]
    models = GOV.models(runtime)["items"]
    return {
        "providers": len(providers),
        "providersEnabled": sum(1 for row in providers if row.get("enabled") is not False),
        "models": len(models),
        "modelsEnabled": sum(1 for row in models if row.get("enabled") is not False),
    }


def _projects_view(runtime: Runtime, *, operator: str) -> dict[str, Any]:
    with runtime.read_only(operator=operator) as ctx:
        _, total = S.list_projects(ctx.session, limit=1, offset=0)
        _, active = S.list_projects(ctx.session, include_archived=False, limit=1, offset=0)
    return {"total": int(total), "active": int(active)}


def readiness(runtime: Runtime, *, operator: str, user_id: str = "") -> dict[str, Any]:
    """Everything a first run has to be told, in one read."""
    settings = runtime.settings
    return {
        "generatedAt": runtime.clock.now().isoformat(),
        "engine": _engine_view(runtime),
        "providers": _provider_modes(runtime),
        "startup": _startup_view(),
        "configuration": {
            "providerMode": settings.provider_mode,
            "workflowEngine": settings.workflow_engine,
            "migrationsMode": settings.migrations_mode,
            "httpsOnly": bool(settings.https_only),
            "authMode": settings.auth_mode,
            # ``validate_runtime``'s own sentences — env var names only, never a value.
            "problems": list(settings.validate_runtime()),
        },
        "identity": _identity_view(runtime, operator=operator, user_id=user_id),
        "environments": _environments_view(runtime),
        # Same rule as ``intelligence``: a caller without the right gets ``null``, not a zero.  A
        # "0 named credentials" line would read as "nothing entered yet", which is a different fact
        # from "you may not see this", and the one thing this page must not invent is a verdict.
        "credentials": (
            CREDENTIALS.summary(runtime, operator=operator)
            if _may(operator, iam.SECRET_MANAGE)
            else None
        ),
        # A caller without governance.manage gets no counts, not a guess of zero: a zero would
        # read as "nothing registered", which is the one thing this page must not invent.
        "intelligence": (
            _intelligence_view(runtime) if _may(operator, iam.GOVERNANCE_MANAGE) else None
        ),
        "projects": _projects_view(runtime, operator=operator),
    }
