"""Architecture guards: the properties that a passing test run cannot prove.

Each test here states a rule that the rest of the suite *assumes*, so that a future change
cannot quietly break it:

* **no transaction is open across a model call** (00 doc 6.2, D-11).  ``runtime/engine.py``
  passes its tests whether the provider call sits in the gap or inside a unit of work, so the
  ordering is asserted on the syntax tree instead;
* **S3 talks to its neighbours through their public entry points only** (D-13).  An import of
  ``factory_blueprint.draft.service`` would work — and would make S3 break the next time S2
  refactors an internal module it never promised;
* **S3 never writes another subsystem's schema, and never mints a UID** (D-14, AI 1.3);
* every audit event, error code and id prefix the source emits is registered in S0's catalog,
  because an unregistered one fails at run time inside a transaction that is rolling back;
* the external-I/O marking is exactly the list S1's adapter declares (the mirror of that list
  lives in ``test_contract.py``).
"""

from __future__ import annotations

import ast
import re
from collections.abc import Iterable
from pathlib import Path

import pytest
from factory_foundation.api import is_external_io

from factory_intelligence import api
from factory_intelligence.registries import (
    AI_AUDIT_EVENT_TYPES,
    AI_ERROR_CODES,
    AI_ID_PREFIXES,
)

SRC = Path(__file__).resolve().parents[2] / "src" / "factory_intelligence"
ALLOWED_FOREIGN_APIS = {"factory_foundation.api", "factory_blueprint.api"}
#: ``testing/`` is S3's shipped double layer and stands on S0's own harness; ``migrations/``
#: is alembic's context.  Both are exempt from the api-only rule, and nothing else is.
IMPORT_RULE_EXEMPT = {"testing", "migrations"}
EXEMPT_FOREIGN_MODULES = {"factory_foundation.testing"}
#: third-party packages S3 is allowed to depend on (pyproject declares each of them)
ALLOWED_THIRD_PARTY = {
    "alembic",
    "httpx",
    "jsonschema",
    "openpyxl",
    "pypdf",
    "psycopg",
    "pydantic",
    "referencing",
    "sqlalchemy",
    "python_ulid",
    "ulid",
}
#: the modules that *perform* the provider call.  ``llm/registry.py`` is excluded on purpose:
#: it is the Model Registry, a database reader, not a transport.
TRANSPORT_MODULES = ("openai_compat.py", "record_replay.py", "mock_agent.py")
#: the call names that leave the process, and so may not sit inside a transaction
MODEL_CALLS = ("generate_structured", "generate_vision", "generate")


def source_files(subpackage: str = "") -> list[Path]:
    root = SRC / subpackage if subpackage else SRC
    return sorted(path for path in root.rglob("*.py") if "__pycache__" not in str(path))


def trees(paths: Iterable[Path]) -> list[tuple[Path, ast.Module]]:
    out: list[tuple[Path, ast.Module]] = []
    for path in paths:
        out.append((path, ast.parse(path.read_text(encoding="utf-8"), filename=str(path))))
    return out


def parents(tree: ast.Module) -> dict[ast.AST, ast.AST]:
    mapping: dict[ast.AST, ast.AST] = {}
    for node in ast.walk(tree):
        for child in ast.iter_child_nodes(node):
            mapping[child] = node
    return mapping


def call_names(node: ast.AST) -> set[str]:
    return {
        call.func.attr
        for call in ast.walk(node)
        if isinstance(call, ast.Call) and isinstance(call.func, ast.Attribute)
    }


def imported_modules(tree: ast.Module) -> set[str]:
    out: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            out.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            out.add(node.module)
    return out


# --- the transaction boundary ----------------------------------------------------


def test_no_model_call_sits_inside_a_transaction() -> None:
    """The gap is structural, not conventional (00 doc 6.2).

    A ``unit_of_work()`` around a provider call would hold a database connection — and S2's
    draft row lock — open across a network round trip measured in tens of seconds.
    """
    offenders: list[str] = []
    for path, tree in trees(source_files()):
        ancestry = parents(tree)
        for call in ast.walk(tree):
            if not (isinstance(call, ast.Call) and isinstance(call.func, ast.Attribute)):
                continue
            if call.func.attr not in MODEL_CALLS:
                continue
            node: ast.AST | None = call
            while node is not None:
                if isinstance(node, ast.withitem) and _is_unit_of_work(node.context_expr):
                    offenders.append(f"{path.name}:{call.lineno}")
                    break
                node = ancestry.get(node)
    assert not offenders, f"model call inside unit_of_work: {offenders}"


def _is_unit_of_work(node: ast.AST) -> bool:
    if isinstance(node, ast.Call):
        func = node.func
        if isinstance(func, ast.Attribute):
            return func.attr == "unit_of_work"
        if isinstance(func, ast.Name):
            return func.id == "unit_of_work"
    return False


def test_a_transport_module_holds_no_transaction() -> None:
    """The modules that perform the provider call own no session: a unit of work there would
    be inherited by whatever the runtime happened to leave open (00 doc 6.2)."""
    offenders = [
        path.name
        for path, tree in trees(source_files("llm"))
        if path.name in TRANSPORT_MODULES
        and any(_is_unit_of_work(item.context_expr) for item in _with_items(tree))
    ]
    assert not offenders, f"transport modules opening a unit of work: {offenders}"


def _with_items(tree: ast.Module) -> list[ast.withitem]:
    return [item for node in ast.walk(tree) if isinstance(node, ast.With) for item in node.items]


def test_only_the_injected_sleeper_is_awaited() -> None:
    """AI-RT-021's backoff must be testable; a literal ``time.sleep`` would make the suite
    slow or, worse, make the delays unobservable."""
    offenders: list[str] = []
    for path, tree in trees(source_files()):
        for call in ast.walk(tree):
            if not isinstance(call, ast.Call):
                continue
            if _is_sleep(call):
                offenders.append(f"{path.name}:{call.lineno}")
    assert not offenders, f"direct sleep calls (use deps.get_sleeper()): {offenders}"


def _is_sleep(call: ast.Call) -> bool:
    func = call.func
    if isinstance(func, ast.Attribute) and func.attr == "sleep":
        return isinstance(func.value, ast.Name) and func.value.id in {"time", "asyncio"}
    return isinstance(func, ast.Name) and func.id == "sleep"


# --- the import surface ----------------------------------------------------------


def test_only_the_public_entry_points_of_other_subsystems_are_imported() -> None:
    """D-13: ``factory_*.api`` is the contract; anything deeper is an internal that may move."""
    offenders: set[str] = set()
    for path, tree in trees(source_files()):
        exempt = path.parent.name in IMPORT_RULE_EXEMPT or (
            path.parent.parent.name in IMPORT_RULE_EXEMPT
        )
        for module in imported_modules(tree):
            root = module.split(".")[0]
            if root == "factory_intelligence" or root in ALLOWED_THIRD_PARTY:
                continue
            if root.startswith("factory_"):
                if module in ALLOWED_FOREIGN_APIS or (exempt and module in EXEMPT_FOREIGN_MODULES):
                    continue
                offenders.add(f"{path.name}:{module}")
            elif "." not in module and not _is_stdlib(root):
                offenders.add(f"{path.name}:{module}")
    assert not offenders, f"non-api imports of other subsystems: {sorted(offenders)}"


def _is_stdlib(module: str) -> bool:
    import sys

    return module in sys.stdlib_module_names


def test_no_subsystem_above_s3_is_imported() -> None:
    """S3 has no dependency on S1 or S4: it is called, it does not call back (D-09)."""
    offenders = [
        module
        for _path, tree in trees(source_files())
        for module in imported_modules(tree)
        if module.split(".")[0] in {"factory_control", "factory_execution", "factory_ui"}
    ]
    assert not offenders, f"S3 imports a caller: {sorted(set(offenders))}"


def test_the_api_export_list_is_the_chapter_7_surface() -> None:
    """Every function of SRS chapter 7 is exported, gated ones included, so a caller can
    branch on availability rather than on a missing attribute."""
    required = {
        "parse_input",
        "start_agent_run",
        "resume_agent_run",
        "cancel_agent_run",
        "get_agent_run",
        "explain_issues",
        "governance",
        "delete_project_data",
        "evaluate_policy",
        "run_evaluation",
        "desensitize",
        "initialize",
        "MIGRATION_CONFIG",
    }
    assert required <= set(api.__all__), f"missing from api.__all__: {sorted(required - set(api.__all__))}"


def test_external_io_marking_is_the_documented_set() -> None:
    marked = {
        name
        for name in api.__all__
        if callable(getattr(api, name)) and is_external_io(getattr(api, name))
    }
    assert marked == {
        "parse_input",
        "start_agent_run",
        "resume_agent_run",
        "explain_issues",
        "run_evaluation",
        "stage_chat",
        "desensitize",
        # the provider probe opens a socket to the registered address, so it must not run while a
        # transaction is holding a connection — the same rule as a parse or a dialogue turn
        "check_provider_health",
    }, "the set that forbids an open transaction, and nothing else"


# --- the catalog -----------------------------------------------------------------


def test_every_audit_event_emitted_is_registered() -> None:
    emitted: set[str] = set()
    for _path, tree in trees(source_files()):
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and _is_named_call(node, {"record", "audit"}):
                first = node.args[0] if node.args else None
                if isinstance(first, ast.Constant) and isinstance(first.value, str):
                    emitted.add(first.value)
            if isinstance(node, ast.Assign):
                for target in node.targets:
                    if isinstance(target, ast.Name) and target.id.startswith("_AUDIT"):
                        emitted.update(
                            value.value
                            for value in ast.walk(node.value)
                            if isinstance(value, ast.Constant) and isinstance(value.value, str)
                        )
    unregistered = {name for name in emitted if name.startswith("ai.")} - set(AI_AUDIT_EVENT_TYPES)
    assert not unregistered, f"audit events not in the catalog: {sorted(unregistered)}"


def _is_named_call(node: ast.Call, names: set[str]) -> bool:
    func = node.func
    return isinstance(func, ast.Attribute) and func.attr in names


def test_every_error_code_raised_is_registered() -> None:
    """An unregistered code is a second failure on top of the first one: S0 refuses the audit
    write while the transaction is rolling back."""
    declared = set(AI_ERROR_CODES) | {
        "LLM_ERROR",
        "LLM_TIMEOUT",
        "POLICY_DENIED",
        "TRANSACTION_MISUSE",
        "NOT_FOUND",
        "VALIDATION_ERROR",
        "CONFLICT",
        "IDEMPOTENCY_CONFLICT",
        "ARTIFACT_ERROR",
        "CANCELLED",
        "FEATURE_NOT_ENABLED",
    }
    literal_pattern = re.compile(r'^\s*code\s*=\s*"([A-Z_]+)"', re.MULTILINE)
    codes: set[str] = set()
    for path in source_files():
        codes.update(literal_pattern.findall(path.read_text(encoding="utf-8")))
    unregistered = {code for code in codes if code.startswith("AI_")} - set(AI_ERROR_CODES)
    assert not unregistered, f"AI_ codes not registered: {sorted(unregistered)}"
    assert declared, "the catalog is never empty"


def test_every_id_prefix_used_is_registered() -> None:
    used: set[str] = set()
    for _path, tree in trees(source_files()):
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and _is_named_call(node, {"new_id"}):
                first = node.args[0] if node.args else None
                if isinstance(first, ast.Constant) and isinstance(first.value, str):
                    used.add(first.value)
    assert used <= set(AI_ID_PREFIXES), f"unregistered id prefixes: {sorted(used - set(AI_ID_PREFIXES))}"


# --- what S3 must never do -------------------------------------------------------


def test_no_source_file_writes_another_schema() -> None:
    """D-14: S3 owns ``ai`` and nothing else.  A hand-written ``core``/``bp`` statement would
    silently bypass the owner's own validation."""
    pattern = re.compile(r"(insert\s+into|update|delete\s+from)\s+(core|bp)\.", re.IGNORECASE)
    offenders = [
        f"{path.name}"
        for path in source_files()
        if pattern.search(path.read_text(encoding="utf-8"))
    ]
    assert not offenders, f"raw SQL against another schema: {offenders}"


def test_no_table_is_declared_outside_the_ai_schema() -> None:
    from factory_foundation.api import AiBase

    offenders = [table.name for table in AiBase.metadata.tables.values() if table.schema != "ai"]
    assert not offenders, f"tables outside schema ai: {offenders}"


@pytest.mark.parametrize(
    "forbidden",
    ["trackedEntityTypes", "metadata/{", "OPENAPI", "react."],
    ids=["dhis2-metadata", "metadata-json", "openapi", "ui-code"],
)
def test_the_agent_output_builder_has_no_channel_for_final_products(forbidden: str) -> None:
    """SRS 1.3: S3 proposes; the compiler produces.  Guarded in the *source*, so an agent
    cannot be given a channel to emit a deliverable by accident in a later change."""
    runtime_text = "\n".join(path.read_text(encoding="utf-8") for path in source_files("runtime"))
    assert forbidden.lower() not in runtime_text.lower()
