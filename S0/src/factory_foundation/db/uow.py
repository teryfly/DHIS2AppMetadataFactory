"""Unit of Work (FD-DB-003).

``unit_of_work`` is the shared database transaction context.  The first entry on
a call chain opens a session and transaction; nested entries join the same one.
Only the outermost scope commits; an unhandled exception at any depth rolls the
whole thing back.  Callbacks registered with :func:`after_commit` run once the
outer commit succeeds (e.g. writing event-stream notifications).

The active session is exposed through :func:`current_session` so that subsystem
services — and S0's own audit / artifact registration — can enlist in the caller's
transaction without being handed it explicitly.
"""

from __future__ import annotations

import contextvars
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field

from sqlalchemy.orm import Session

from factory_foundation.common.errors import TransactionMisuseError
from factory_foundation.common.logging import get_logger
from factory_foundation.db.engine import new_session

_logger = get_logger(__name__)

AfterCommit = Callable[[], None]


@dataclass
class _UoW:
    session: Session
    depth: int = 1
    _after_commit: list[AfterCommit] = field(default_factory=list)

    def add_after_commit(self, callback: AfterCommit) -> None:
        self._after_commit.append(callback)

    def run_after_commit(self) -> None:
        for cb in self._after_commit:
            try:
                cb()
            except Exception:  # noqa: BLE001 - commit already done, never mask it
                _logger.exception("after_commit callback failed")
        self._after_commit.clear()


_active: contextvars.ContextVar[_UoW | None] = contextvars.ContextVar("factory_uow", default=None)


def current_uow() -> _UoW | None:
    return _active.get()


def has_active_uow() -> bool:
    return _active.get() is not None


def current_session() -> Session:
    uow = _active.get()
    if uow is None:
        raise TransactionMisuseError(
            "no active unit of work; wrap the call in unit_of_work()",
        )
    return uow.session


@contextmanager
def unit_of_work(session: Session | None = None) -> Iterator[Session]:
    """Enter a shared transaction.  Nested entries reuse the outer session."""
    existing = _active.get()
    if existing is not None:
        existing.depth += 1
        try:
            yield existing.session
        finally:
            existing.depth -= 1
        return

    owns_session = session is None
    sess = session if session is not None else new_session()
    uow = _UoW(session=sess)
    token = _active.set(uow)
    try:
        yield sess
        sess.commit()
    except BaseException:
        sess.rollback()
        raise
    else:
        uow.run_after_commit()
    finally:
        _active.reset(token)
        if owns_session:
            sess.close()


def after_commit(callback: AfterCommit) -> None:
    """Register ``callback`` to run after the outermost commit succeeds.

    If no UoW is active the callback runs immediately (best effort): there is no
    transaction to defer behind.
    """
    uow = _active.get()
    if uow is None:
        try:
            callback()
        except Exception:  # noqa: BLE001
            _logger.exception("immediate after_commit callback failed (no active UoW)")
        return
    uow.add_after_commit(callback)


@contextmanager
def resolve_session(session: Session | None = None) -> Iterator[Session]:
    """Yield a session for a read-only or self-contained operation.

    Precedence: an explicitly passed ``session``; else the active UoW session;
    else a fresh session that is closed on exit.  Write operations that must
    enlist in the caller's transaction should use :func:`current_session`
    directly instead.
    """
    if session is not None:
        yield session
        return
    uow = _active.get()
    if uow is not None:
        yield uow.session
        return
    sess = new_session()
    try:
        yield sess
    finally:
        sess.close()


@contextmanager
def writable_session(session: Session | None = None) -> Iterator[Session]:
    """Yield a session for a write that must be committed.

    * an explicitly passed session, or an active UoW, is used as-is — the caller
      commits it;
    * otherwise a self-contained :func:`unit_of_work` is opened and committed.
    """
    if session is not None:
        yield session
        return
    uow = _active.get()
    if uow is not None:
        yield uow.session
        return
    with unit_of_work() as sess:
        yield sess
