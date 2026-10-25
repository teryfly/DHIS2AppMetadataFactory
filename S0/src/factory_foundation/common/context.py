"""Operation context (FD-COM-005).

A context-variable-carried bag holding ``correlationId``, operator, project,
workflow and stage IDs.  Logging, audit and artifact creation read it
automatically so callers do not have to thread it through every signature.
"""

from __future__ import annotations

import contextvars
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, replace
from typing import Any

from factory_foundation.common.ids import new_correlation_id


@dataclass(frozen=True)
class OperationContext:
    correlation_id: str
    operator_type: str = "SYSTEM"  # USER | SYSTEM
    operator_name: str = "system"
    project_id: str | None = None
    workflow_id: str | None = None
    stage_id: str | None = None
    deployment_run_id: str | None = None
    source_subsystem: str = "S0"

    def with_(self, **changes: Any) -> OperationContext:
        return replace(self, **changes)

    def as_log_fields(self) -> dict[str, str]:
        """Fields appended to every structured log record (00 doc 6.8)."""
        out = {"correlationId": self.correlation_id, "operator": self.operator_name}
        for key, value in (
            ("projectId", self.project_id),
            ("workflowId", self.workflow_id),
            ("stageId", self.stage_id),
            ("deploymentRunId", self.deployment_run_id),
        ):
            if value is not None:
                out[key] = value
        return out


_context_var: contextvars.ContextVar[OperationContext | None] = contextvars.ContextVar(
    "factory_operation_context", default=None
)


def get_operation_context() -> OperationContext | None:
    return _context_var.get()


def current_context_or_default() -> OperationContext:
    ctx = _context_var.get()
    if ctx is None:
        return OperationContext(correlation_id=new_correlation_id())
    return ctx


def current_correlation_id() -> str | None:
    ctx = _context_var.get()
    return ctx.correlation_id if ctx else None


def set_operation_context(ctx: OperationContext) -> contextvars.Token[OperationContext | None]:
    return _context_var.set(ctx)


@contextmanager
def bind_operation_context(**changes: object) -> Iterator[OperationContext]:
    """Context manager establishing (or extending) the operation context.

    If no context exists a fresh one is created with a new correlation id; if
    one exists, ``changes`` are applied on top of it.
    """
    existing = _context_var.get()
    if existing is None:
        ctx: OperationContext | None = OperationContext(correlation_id=new_correlation_id())
    else:
        ctx = existing
    filtered = {k: v for k, v in changes.items() if v is not None}
    if ctx is not None and filtered:
        ctx = ctx.with_(**filtered)
    token = _context_var.set(ctx)
    try:
        assert ctx is not None
        yield ctx
    finally:
        _context_var.reset(token)


def reset_operation_context() -> None:
    _context_var.set(None)
