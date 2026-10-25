"""Database & Unit-of-Work tests (FD-DB-003..006)."""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from sqlalchemy import Column, ForeignKey, MetaData, String, Table

from factory_foundation.common.errors import (
    ConcurrencyError,
    SchemaError,
    TransactionMisuseError,
)
from factory_foundation.db.external_io import external_io
from factory_foundation.db.fk_check import check_no_cross_schema_fk, find_cross_schema_fk
from factory_foundation.db.optimistic import bump_version, check_version
from factory_foundation.db.uow import after_commit, current_session, unit_of_work
from factory_foundation.models.registry import ErrorCodeRow


@pytest.fixture
def db(foundation):
    # foundation fixture already configures the engine + fresh core schema
    return foundation


def test_outermost_commit_persists(db):
    with unit_of_work() as s:
        s.add(ErrorCodeRow(code="T1", owner_subsystem="S0", retryable=False))
    with unit_of_work() as s:
        assert s.get(ErrorCodeRow, "T1") is not None


def test_nested_joins_same_transaction(db):
    with unit_of_work() as outer, unit_of_work() as inner:
        assert inner is outer
        inner.add(ErrorCodeRow(code="N1", owner_subsystem="S0", retryable=True))
    with unit_of_work() as s:
        assert s.get(ErrorCodeRow, "N1") is not None


def test_inner_exception_rolls_back_whole(db):
    with pytest.raises(RuntimeError), unit_of_work() as s:
        s.add(ErrorCodeRow(code="R1", owner_subsystem="S0", retryable=False))

        with unit_of_work():
            raise RuntimeError("inner boom")
    with unit_of_work() as s:
        assert s.get(ErrorCodeRow, "R1") is None


def test_after_commit_runs_after_outer_commit(db):
    seen: list[str] = []
    with unit_of_work():
        after_commit(lambda: seen.append("cb"))
        assert seen == []  # not yet
    assert seen == ["cb"]


def test_after_commit_not_run_on_rollback(db):
    seen: list[str] = []
    with pytest.raises(ValueError), unit_of_work():
        after_commit(lambda: seen.append("cb"))
        raise ValueError("nope")
    assert seen == []


def test_current_session_requires_uow(db):
    with pytest.raises(TransactionMisuseError):
        current_session()


@external_io
def _some_external_io() -> str:
    return "done"


def test_external_io_inside_uow_raises(db):
    # FD-AC-03
    with pytest.raises(TransactionMisuseError), unit_of_work():
        _some_external_io()


def test_external_io_outside_uow_ok(db):
    assert _some_external_io() == "done"


def test_optimistic_version_check_and_bump():
    row = SimpleNamespace(version=3)
    check_version(row, 3)
    assert bump_version(row, 3) == 4
    with pytest.raises(ConcurrencyError):
        check_version(row, 1)  # stale


def test_cross_schema_fk_detected_and_raises():
    # FD-AC-09: build a throwaway metadata with a cp→core foreign key.
    md = MetaData(schema="core")
    Table("parent_x", md, Column("id", String, primary_key=True), schema="core")
    Table(
        "child_x",
        md,
        Column("id", String, primary_key=True),
        Column("pid", String, ForeignKey("core.parent_x.id")),
        schema="cp",
    )
    violations = find_cross_schema_fk([md])
    assert violations
    assert "cp.child_x.pid" in str(violations[0])
    with pytest.raises(SchemaError):
        check_no_cross_schema_fk([md])


def test_same_schema_fk_ok():
    md = MetaData(schema="core")
    Table("a_ok", md, Column("id", String, primary_key=True))
    Table(
        "b_ok",
        md,
        Column("id", String, primary_key=True),
        Column("aid", String, ForeignKey("core.a_ok.id")),
    )
    assert find_cross_schema_fk([md]) == []
    check_no_cross_schema_fk([md])


def test_cross_schema_fk_across_separate_metadata_is_caught():
    """FD-DB-006 / FD-AC-09: each schema keeps its own MetaData (FD-DB-002), so the
    forbidden declaration points at a table this metadata cannot resolve.  The dotted
    target name is the only way to see it -- and exactly what a subsystem would write."""
    core_md = MetaData(schema="core")
    Table("project_fk_target", core_md, Column("id", String, primary_key=True))
    dx_md = MetaData(schema="dx")
    Table(
        "execution_fk_leak",
        dx_md,
        Column("id", String, primary_key=True),
        Column("project_id", String, ForeignKey("core.project_fk_target.id")),
    )
    violations = find_cross_schema_fk([core_md, dx_md])
    assert [str(v) for v in violations] == ["dx.execution_fk_leak.project_id -> core.project_fk_target"]
    with pytest.raises(SchemaError):
        check_no_cross_schema_fk([core_md, dx_md])
