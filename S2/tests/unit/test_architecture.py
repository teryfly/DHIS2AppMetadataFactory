"""Structural guards: properties that must hold by construction, not by review.

Each test here proves something the SRS states as a rule (00 doc 6.1 and 6.2,
BP-CMP-001, BP-DRF-005.4, FD-DB-006, BP-BR-09).  An ordinary behavioural test only
catches a violation when it happens to walk the exact path; reading the source makes
the rule hold for every path at once.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest
from factory_foundation.api import BpBase, error_catalog, find_cross_schema_fk
from factory_foundation.db.external_io import is_external_io

from factory_blueprint import api
from factory_blueprint.registries import BP_AUDIT_EVENT_TYPES, BP_ERROR_CODES, IdemScope

SOURCE = Path(__file__).resolve().parents[2] / "src" / "factory_blueprint"

#: exactly the functions SRS chapter 7 marks 外部 I/O
EXTERNAL_IO_FUNCTIONS = {
    "prepare_initial_blueprint",
    "prepare_draft_base",
    "freeze_draft",
    "prepare_mode_upgrade",
    "replenish_uid_pool",
    "compile_baseline",
    "compile_for_environment",
    "validate_build",
    "reproduce",
    "generate_frontend_package",
    "export_blueprint",
    "prepare_import",
    "get_entries",
    "analyze_impact",
    "diff_versions",
    # a report is an artifact's bytes, so this read cannot run inside a unit of work
    "query_issues",
    # likewise these three: a build's graph and a package's files are artifact content
    "get_dependency_graph",
    "list_package_files",
    "read_package_file",
}

#: modules whose whole purpose is to be a pure function of their inputs
PURE_PACKAGES = (
    "capability",
    "compilers",
    "validators",
    "impact",
    "patch",
    "diff",
    "descriptors",
    "schema",
)

#: a name in this set inside a pure module means the product could depend on
#: something other than the approved Blueprint content (BP-CMP-001)
BANNED_CALLS = {
    "now",
    "utcnow",
    "today",
    "random",
    "randint",
    "shuffle",
    "uuid4",
    "urandom",
    "new_id",
    "sleep",
}

#: importing any of these is equally disqualifying
BANNED_IMPORTS = {"random", "socket", "requests", "httpx", "time", "uuid"}


def _modules(package: str) -> list[Path]:
    root = SOURCE / package
    return sorted(path for path in root.rglob("*.py") if "__pycache__" not in path.parts)


def _banned_in(path: Path) -> list[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    found: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            name = node.func.id if isinstance(node.func, ast.Name) else getattr(node.func, "attr", "")
            if name in BANNED_CALLS:
                found.append(name)
        elif isinstance(node, ast.Import):
            found.extend(alias.name.split(".")[0] for alias in node.names if alias.name.split(".")[0] in BANNED_IMPORTS)
        elif isinstance(node, ast.ImportFrom) and node.module:
            root_package = node.module.split(".")[0]
            if root_package in BANNED_IMPORTS:
                found.append(node.module)
    return sorted(set(found))


def test_exactly_the_documented_functions_are_external_io() -> None:
    marked = {name for name in api.__all__ if is_external_io(getattr(api, name, None))}
    assert marked == EXTERNAL_IO_FUNCTIONS, (
        "SRS chapter 7 and the code disagree about what may not run in a transaction: "
        f"only marked {sorted(marked - EXTERNAL_IO_FUNCTIONS)}, "
        f"unmarked {sorted(EXTERNAL_IO_FUNCTIONS - marked)}"
    )


@pytest.mark.parametrize("package", PURE_PACKAGES)
def test_a_pure_package_reaches_for_no_clock_or_network(package: str) -> None:
    offenders = {path.name: _banned_in(path) for path in _modules(package) if _banned_in(path)}
    assert offenders == {}, f"{package} would let a non-input decide the product: {offenders}"


def test_the_api_exposes_every_function_of_chapter_7() -> None:
    required = {
        "initialize",
        "MIGRATION_CONFIG",
        "list_sections",
        "get_section_schema",
        "prepare_initial_blueprint",
        "register_initial_blueprint",
        "get_current_version",
        "list_versions",
        "get_version",
        "get_project_semantics",
        "get_entries",
        "get_decisions",
        "diff_versions",
        "diff_draft",
        "open_draft",
        "submit_patch",
        "get_draft",
        "get_active_draft",
        "reset_draft",
        "freeze_draft",
        "promote_draft",
        "prepare_mode_upgrade",
        "commit_mode_upgrade",
        "analyze_impact",
        "get_registry",
        "get_managed_scope",
        "get_environment_bindings",
        "replenish_uid_pool",
        "get_uid_pool_status",
        "set_environment_binding",
        "compile_baseline",
        "compile_for_environment",
        "validate_build",
        "query_issues",
        "reproduce",
        "generate_frontend_package",
        "get_dependency_graph",
        "list_package_files",
        "read_package_file",
        "export_blueprint",
        "prepare_import",
        "commit_import",
        "delete_project_data",
    }
    missing = sorted(required - set(api.__all__))
    assert missing == [], f"chapter 7 promises them, __all__ hides them: {missing}"


def test_the_api_imports_only_public_entry_points() -> None:
    """D-13: S2's public surface is ``api``, and ``api`` reaches S0 only through its own."""
    tree = ast.parse((SOURCE / "api.py").read_text(encoding="utf-8"), filename="api.py")
    foundation = {
        node.module
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom) and node.module
    }
    outside = sorted(
        name for name in foundation if name.startswith("factory_foundation") and name != "factory_foundation.api"
    )
    assert outside == []


def test_bp_declares_no_foreign_key_outside_its_schema() -> None:
    """FD-DB-006: ``bp`` rows may not point at another schema's rows."""
    assert find_cross_schema_fk([BpBase.metadata]) == []


def test_every_emitted_audit_event_is_registered() -> None:
    emitted: set[str] = set()
    for path in SOURCE.rglob("*.py"):
        if "__pycache__" in path.parts:
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Name)
                and node.func.id == "record"
                and node.args
                and isinstance(node.args[0], ast.Constant)
                and isinstance(node.args[0].value, str)
                and str(node.args[0].value).startswith("bp.")
            ):
                emitted.add(str(node.args[0].value))
    assert emitted, "the scan found no audit call at all, so it stopped working"
    # ``initialize()`` registers exactly this dictionary with S0, so a name outside it
    # would be refused by the audit service at runtime
    assert emitted <= set(BP_AUDIT_EVENT_TYPES), sorted(emitted - set(BP_AUDIT_EVENT_TYPES))
    assert len(BP_AUDIT_EVENT_TYPES) == 13, "appendix C lists thirteen bp.* events"


def test_every_declared_error_code_is_registered() -> None:
    declared: set[str] = set()
    for path in SOURCE.rglob("*.py"):
        if "__pycache__" in path.parts:
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.ClassDef):
                value = _class_code(node)
                if value:
                    declared.add(value)
    assert declared, "no error class declares a code, so the scan stopped working"
    unknown = sorted(
        code
        for code in declared
        if not error_catalog.is_registered(code) and code not in BP_ERROR_CODES
    )
    assert unknown == [], f"S2 raises codes nobody registered: {unknown}"


def test_no_rule_id_is_declared_and_never_emitted() -> None:
    """BP-VAL-020 … 031 name checks S2 must perform.

    A rule constant that no code path emits reads like an implemented check, so the
    dimension it names would silently go unvalidated.  This scan makes "declared" and
    "used" the same claim.
    """
    unused: list[str] = []
    for path in sorted(SOURCE.rglob("*.py")):
        if "__pycache__" in path.parts:
            continue
        text = path.read_text(encoding="utf-8")
        tree = ast.parse(text, filename=str(path))
        declared = [
            (name, node.value.value)
            for node in ast.walk(tree)
            if isinstance(node, ast.Assign)
            and isinstance(node.value, ast.Constant)
            and isinstance(node.value.value, str)
            for name in (
                target.id for target in node.targets if isinstance(target, ast.Name)
            )
            if name.startswith("RULE_")
        ]
        unused.extend(
            f"{path.relative_to(SOURCE)}: {name} ({value})"
            for name, value in declared
            if text.count(name) < 2
        )
    assert unused == [], [f"rule id nobody reports: {line}" for line in unused]


def _class_code(node: ast.ClassDef) -> str | None:
    for statement in node.body:
        target = None
        value = None
        if isinstance(statement, ast.AnnAssign) and isinstance(statement.target, ast.Name):
            target, value = statement.target.id, statement.value
        elif isinstance(statement, ast.Assign) and isinstance(statement.targets[0], ast.Name):
            target, value = statement.targets[0].id, statement.value
        if target == "code" and isinstance(value, ast.Constant) and isinstance(value.value, str):
            return str(value.value)
    return None


def test_idempotency_scopes_are_namespaced_to_this_subsystem() -> None:
    scopes = {
        value
        for key, value in vars(IdemScope).items()
        if not key.startswith("_") and isinstance(value, str)
    }
    assert scopes
    assert all(scope.startswith("bp.") for scope in scopes), scopes


def test_only_the_public_foundation_entry_point_is_imported() -> None:
    """00 doc 6.1: a subsystem imports ``factory_foundation.api``, nothing deeper.

    ``factory_foundation.testing`` is the one exception, and only inside
    ``factory_blueprint.testing`` — S0 ships it as the harness for exactly this use.
    """
    offenders: list[str] = []
    for path in SOURCE.rglob("*.py"):
        if "__pycache__" in path.parts:
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if not isinstance(node, ast.ImportFrom) or not node.module:
                continue
            module = node.module
            if not module.startswith("factory_foundation"):
                continue
            if module == "factory_foundation.api":
                continue
            if module == "factory_foundation.testing" and path.parent.name == "testing":
                continue
            offenders.append(f"{path.relative_to(SOURCE)}: {module}")
    assert offenders == [], [f"import around the public entry point: {line}" for line in offenders]
