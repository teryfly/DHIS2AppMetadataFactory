"""Route groups: one module per resource family (SRS 8.1).

Every module exposes ``router = APIRouter(prefix="/api/v1")`` so the *full* route
template — prefix plus path, with the same ``{param}`` names — is unchanged from
when these handlers lived in ``app.py``.  That matters: the idempotency scope is
derived from the route template (:func:`factory_control.rest.middleware._scope_of`)
and the ``operationId`` from the handler function name, so a replay recorded before
the split still matches after it.
"""

from __future__ import annotations

from fastapi import APIRouter

from factory_control.rest.routes import (
    blueprint,
    catalog,
    change_requests,
    chat,
    environments,
    events,
    gates,
    governance,
    identity,
    inputs,
    projects,
    runs,
    secrets,
    setup,
    workflow,
)


def routers() -> list[APIRouter]:
    """The groups, in registration order (route matching depends on it).

    ``environments`` precedes nothing that could shadow it, but its literal
    ``/environments/expiring-credentials`` is declared before
    ``/environments/{environment_key}`` inside the module for the same reason.
    """
    return [
        projects.router,
        workflow.router,
        chat.router,
        gates.router,
        runs.router,
        catalog.router,
        setup.router,
        secrets.router,
        change_requests.router,
        environments.router,
        blueprint.router,
        governance.router,
        identity.router,
        inputs.router,
        events.router,
    ]
