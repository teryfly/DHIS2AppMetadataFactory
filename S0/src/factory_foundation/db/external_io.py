"""External I/O function marker (FD-DB-004).

Functions that perform slow external work (LLM calls, DHIS2 calls, uploading
large files to object storage) must not run while a database transaction is
open, so they declare themselves with :func:`external_io`.  Calling such a
function from inside an active unit of work raises ``TRANSACTION_MISUSE``
immediately (00 doc 6.2, FD-BR-05).
"""

from __future__ import annotations

import functools
from collections.abc import Callable
from typing import Any, TypeVar

from factory_foundation.common.errors import TransactionMisuseError
from factory_foundation.db.uow import has_active_uow

F = TypeVar("F", bound=Callable[..., Any])


def external_io(func: F) -> F:
    """Mark a function as performing external I/O that forbids an open UoW."""

    @functools.wraps(func)
    def wrapper(*args: object, **kwargs: object) -> Any:
        if has_active_uow():
            raise TransactionMisuseError(
                f"external I/O function {func.__qualname__}() called inside an active unit_of_work",
                details={"function": func.__qualname__},
            )
        return func(*args, **kwargs)

    wrapper.__wrapped_is_external_io__ = True  # type: ignore[attr-defined]
    return wrapper  # type: ignore[return-value]


def is_external_io(func: object) -> bool:
    return bool(getattr(func, "__wrapped_is_external_io__", False))
