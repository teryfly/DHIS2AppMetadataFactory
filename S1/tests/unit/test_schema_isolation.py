"""Schema isolation (D-08, CP-DATA-01, CP-ASM-04) — asserted without a database.

S1 owns schema ``cp`` and reaches every other subsystem through its public API only.
These are the structural rules that make that true, checked against the source tree
so a violation fails in review rather than in a customer's database.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest

from factory_control.foundation import CpBase, find_cross_schema_fk

pytestmark = pytest.mark.unit

SRC = Path(__file__).resolve().parents[2] / "src" / "factory_control"
FOUNDATION_MODULE = "foundation.py"
ADAPTERS_DIR = SRC / "adapters"

#: Other subsystems' schemas: they may not appear in SQL S1 writes (00 doc 3.2).
FOREIGN_SCHEMAS = ("core", "dx", "bp", "ai")
PROVIDER_PACKAGES = ("factory_foundation", "factory_blueprint", "factory_intelligence", "factory_dhis2")


def _modules() -> list[Path]:
    return sorted(SRC.rglob("*.py"))


def _imported_names(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    found: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            found.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            found.add(node.module)
    return found


def test_no_cross_schema_foreign_keys() -> None:
    """``check_no_cross_schema_fk`` equivalent for CI (CP-DATA-01 / D-08)."""
    violations = find_cross_schema_fk([CpBase.metadata])
    assert violations == [], f"cross-schema foreign keys: {violations}"


def test_every_cp_table_lives_in_the_cp_schema() -> None:
    tables = CpBase.metadata.tables
    assert len(tables) >= 16
    for name, table in tables.items():
        assert table.schema == "cp", f"{name} would be created in schema {table.schema!r}"


def test_only_the_foundation_module_imports_the_foundation() -> None:
    """One chokepoint for S0 (CP-ASM-01), so the contract can be checked in one place."""
    for path in _modules():
        if path.name == FOUNDATION_MODULE or "__pycache__" in str(path):
            continue
        imported = {name for name in _imported_names(path) if name.startswith("factory_foundation")}
        assert not imported, f"{path.name} imports {sorted(imported)} directly"


def test_only_adapters_import_the_provider_subsystems() -> None:
    """S2/S3/S4 are reachable through the port adapters alone (CP-ASM-04)."""
    for path in _modules():
        if ADAPTERS_DIR in path.parents or "__pycache__" in str(path):
            continue
        if path.name in {"fakes.py", "registry.py"}:
            continue
        offenders = {
            name
            for name in _imported_names(path)
            if name.split(".")[0] in {"factory_blueprint", "factory_intelligence", "factory_dhis2"}
        }
        assert not offenders, f"{path.name} imports {sorted(offenders)}"


def test_no_sql_touches_another_subsystem_schema() -> None:
    pattern = re.compile(
        r"\b(select|insert|update|delete|with|from|join)\b.*?\b(" + "|".join(FOREIGN_SCHEMAS) + r")\.",
        re.IGNORECASE | re.DOTALL,
    )
    offenders: list[str] = []
    for path in _modules():
        if "__pycache__" in str(path):
            continue
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if isinstance(node, ast.Constant) and isinstance(node.value, str) and pattern.search(node.value):
                offenders.append(f"{path.name}: {node.value[:60]!r}")
    assert offenders == []


def test_provider_packages_are_never_imported_by_the_domain_and_services() -> None:
    for folder in ("domain", "services", "repositories", "engine", "events"):
        for path in (SRC / folder).rglob("*.py"):
            names = {name.split(".")[0] for name in _imported_names(path)}
            assert not names & set(PROVIDER_PACKAGES), f"{path.name} reaches a provider package"


def test_cp_schema_name_is_the_only_one_s1_migrates() -> None:
    from factory_control.db import CP_MIGRATION_CONFIG

    assert CP_MIGRATION_CONFIG.schema == "cp"
    assert CP_MIGRATION_CONFIG.version_table == "cp_alembic_version"


def test_cross_subsystem_references_are_ids_only() -> None:
    """D-14: another subsystem's object is stored as an id, plus a hash when frozen."""
    reference = re.compile(r"(artifact|draft|version|environment|execution|blueprint|input)_?_(id|key|ref)$")
    for name, table in CpBase.metadata.tables.items():
        for column in table.columns:
            if not reference.search(column.name):
                continue
            kind = column.type.__class__.__name__
            # ids and keys are opaque strings; a *_ref may be a structured {kind, id, hash}
            allowed = {"VARCHAR", "String"} if column.name.endswith(("_id", "_key")) else {"VARCHAR", "String", "JSONB"}
            assert kind in allowed, f"{name}.{column.name} stores {column.type}, not a reference"
