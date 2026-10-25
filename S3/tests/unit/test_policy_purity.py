"""AI-NFR-01 as a structural rule, not a hope: the engine touches nothing outside itself.

The requirement is a latency number (P95 < 50 ms for a hundred context items), and a
latency test can only *observe* a violation after it ships.  What makes AI-NFR-01 hold
here is that :mod:`factory_intelligence.policy.engine` and
:mod:`factory_intelligence.policy.model` are pure functions over plain data: no
transaction, no artifact read, no S2 call.  That is checkable from the text of the two
files, which is what this module does — a behavioural test catches a violation only on
the path it happens to walk.

:mod:`factory_intelligence.policy.resolution` is the deliberate exception, and the guard is
checked against it too: a scan that fires nowhere is not a guard, it is a typo.
"""

from __future__ import annotations

import ast
import importlib
from pathlib import Path

import pytest
from factory_blueprint import api as blueprint_api
from factory_foundation import api as foundation_api

from factory_intelligence import policy

SOURCE_ROOT = Path(__file__).resolve().parents[2] / "src" / "factory_intelligence"

#: the two modules whose purity is the requirement
PURE_MODULES = ("policy/engine.py", "policy/model.py")

#: every name here is something the pure modules could reach for and must not: S0's
#: transactions and content store, S2's patch entry point
BANNED_SYMBOLS = (
    "unit_of_work",
    "new_session",
    "current_session",
    "get_artifact",
    "read_content_bytes",
    "stage_content",
    "submit_patch",
)

#: the module that is allowed — indeed required — to reach outside, because it is the
#: only input half of the engine (AI-POL-006)
IO_MODULE = "policy/resolution.py"

#: what a pure policy module may import: the standard library, S0's public vocabulary and
#: S3's own enums and data structures
ALLOWED_IMPORTS = frozenset(
    {
        "__future__",
        "collections.abc",
        "dataclasses",
        "typing",
        "factory_foundation.api",
        "factory_intelligence.enums",
        "factory_intelligence.policy.model",
    }
)

#: the public surface of SRS 3.5, spelled out here so that adding to it is a decision
EXPECTED_EXPORTS = [
    "ContextItem",
    "DeniedItem",
    "EffectivePolicy",
    "Verdict",
    "evaluate",
    "highest_classification",
    "normalize_environment_policies",
    "normalize_one",
    "org_policy_from_payload",
    "effective_policy",
    "read_org_policy",
    "relax",
    "resolve",
    "CandidateModel",
    "ModelDenial",
    "Selection",
    "select",
]


def _source(relative: str) -> str:
    path = SOURCE_ROOT / relative
    assert path.is_file(), f"{path} does not exist: the guard would pass on an empty read"
    text = path.read_text(encoding="utf-8")
    assert len(text) > 500, f"{path} is too short to be the module under guard"
    return text


def _imported_from(relative: str, module: str) -> set[str]:
    """The names one file takes from one specific place, read off its syntax tree."""
    names: set[str] = set()
    for node in ast.walk(ast.parse(_source(relative), filename=relative)):
        if isinstance(node, ast.ImportFrom) and node.module == module:
            names.update(alias.name for alias in node.names)
    return names


def _imported_modules(relative: str) -> set[str]:
    modules: set[str] = set()
    for node in ast.walk(ast.parse(_source(relative), filename=relative)):
        if isinstance(node, ast.Import):
            modules.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            modules.add(node.module)
    return modules


def _files_mentioning(symbol: str) -> list[str]:
    hits: list[str] = []
    for path in sorted(SOURCE_ROOT.rglob("*.py")):
        if "__pycache__" in path.parts:
            continue
        if symbol in path.read_text(encoding="utf-8"):
            hits.append(path.relative_to(SOURCE_ROOT).as_posix())
    return hits


# --- the guard -------------------------------------------------------------------


@pytest.mark.parametrize("module", PURE_MODULES)
@pytest.mark.parametrize("symbol", BANNED_SYMBOLS)
def test_the_policy_engine_reaches_for_no_io_symbol(module: str, symbol: str) -> None:
    """AI-NFR-01, AI-POL-003: the judgement is made of the data in front of it.  A
    ``get_artifact`` in the loop would put a hundred S0 reads on the critical path, and a
    ``unit_of_work`` would let a policy decision hold a transaction open."""
    assert symbol not in _source(module), f"{module} mentions {symbol}"


@pytest.mark.parametrize("module", PURE_MODULES)
def test_no_foreign_subsystem_is_named_at_all(module: str) -> None:
    """00 doc 5 and SRS 1.3: a policy decision does not call S2 — S1 hands the Blueprint
    facts over as parameters (AI-RT-002)."""
    assert "factory_blueprint" not in _source(module)


@pytest.mark.parametrize("module", PURE_MODULES)
def test_the_only_import_from_s0_is_the_classification_enum(module: str) -> None:
    """These two modules' entire S0 dependency is the five-member vocabulary of SRS 20.1.
    A sixth name here means a module started importing behaviour."""
    assert _imported_from(module, "factory_foundation.api") == {"DataClassification"}


@pytest.mark.parametrize("module", PURE_MODULES)
def test_every_import_is_one_a_pure_function_may_have(module: str) -> None:
    """A single new import is how a pure module stops being one, so the allow-list *is*
    the statement of intent."""
    offenders = sorted(_imported_modules(module) - ALLOWED_IMPORTS)
    assert offenders == [], f"{module} imports {offenders}"


@pytest.mark.parametrize("module", PURE_MODULES)
def test_neither_module_names_a_session_or_a_database_column(module: str) -> None:
    """The types that would appear if a query were on its way in: ``Session`` and
    SQLAlchemy itself.  ``.mapped_column`` is S3's own ORM layer and has no business here."""
    text = _source(module)
    assert "sqlalchemy" not in text
    assert "Session" not in text
    assert "Mapped[" not in text


# --- the guard proves something --------------------------------------------------


def test_the_io_module_really_does_import_the_artifact_reader() -> None:
    """AI-POL-006: a classification comes from S0, so *some* module must read it.  If this
    assertion failed, every test above would be passing because the scan can no longer see
    the pattern it is looking for."""
    assert "get_artifact" in _source(IO_MODULE)
    assert "get_artifact" in _imported_from(IO_MODULE, "factory_foundation.api")
    assert IO_MODULE not in PURE_MODULES


@pytest.mark.parametrize("symbol", BANNED_SYMBOLS)
def test_every_banned_name_is_a_real_symbol_somebody_could_import(symbol: str) -> None:
    """The other half of the same argument: the patterns are names the public APIs
    actually export, not strings that stopped existing."""
    assert hasattr(foundation_api, symbol) or hasattr(blueprint_api, symbol), (
        f"{symbol} is no longer a public symbol: the guard is stale"
    )


@pytest.mark.parametrize("symbol", BANNED_SYMBOLS)
def test_the_banned_names_do_appear_elsewhere_in_s3(symbol: str) -> None:
    """If no S3 module ever wrote ``stage_content``, its absence from the engine would
    mean nothing.  Each pattern must be live somewhere that is allowed to use it."""
    users = [path for path in _files_mentioning(symbol) if path not in PURE_MODULES]
    assert users, f"nothing in S3 uses {symbol}: the pattern guards a name nobody has"


def test_orgpolicy_is_the_only_policy_module_that_opens_a_transaction() -> None:
    """``policy/__init__.py`` states the split: engine and model are pure, resolution is the
    only reader of other subsystems, orgpolicy is the only writer to ``ai``."""
    for module in (
        "policy/engine.py",
        "policy/model.py",
        "policy/selection.py",
        "policy/normalize.py",
        IO_MODULE,
    ):
        text = _source(module)
        for symbol in ("unit_of_work", "new_session", "current_session"):
            assert symbol not in text, f"{module} mentions {symbol}"
    assert "unit_of_work" in _source("policy/orgpolicy.py")


# --- the exported surface --------------------------------------------------------


def test_the_policy_package_exports_every_name_it_declares() -> None:
    """SRS 3.5's entry points are reached through this package; a name in ``__all__``
    without a binding is an ``AttributeError`` at somebody's import time."""
    missing = [name for name in policy.__all__ if not hasattr(policy, name)]
    assert missing == [], f"declared but not bound: {missing}"


def test_the_policy_package_declares_exactly_the_documented_surface() -> None:
    """The list is the API; adding a name is a decision about what S1 and S2 may build
    on, and dropping one breaks a caller."""
    assert sorted(policy.__all__) == sorted(EXPECTED_EXPORTS)


def test_the_engine_functions_are_reachable_from_both_ends() -> None:
    """``policy.evaluate`` and the engine module's own ``evaluate`` must be the same
    object, or a caller can import two engines with different rules.  ``policy.select`` and
    ``policy.resolve`` are the *functions* too — which is why the modules behind them are
    named ``selection`` and ``resolution``: a submodule sharing a published name would bind
    the package attribute to a module for whoever imported it last, and the two objects would
    disagree only at run time, in whatever order the imports arrived in."""
    engine = importlib.import_module("factory_intelligence.policy.engine")
    model = importlib.import_module("factory_intelligence.policy.model")
    normalize = importlib.import_module("factory_intelligence.policy.normalize")
    orgpolicy = importlib.import_module("factory_intelligence.policy.orgpolicy")
    select_module = importlib.import_module("factory_intelligence.policy.selection")
    resolve_module = importlib.import_module("factory_intelligence.policy.resolution")

    assert policy.evaluate is engine.evaluate
    assert policy.highest_classification is engine.highest_classification
    assert policy.EffectivePolicy is model.EffectivePolicy
    assert policy.ContextItem is model.ContextItem
    assert policy.Verdict is model.Verdict
    assert policy.normalize_one is normalize.normalize_one
    assert policy.org_policy_from_payload is normalize.org_policy_from_payload
    assert policy.relax is orgpolicy.relax
    assert policy.read_org_policy is orgpolicy.read_org_policy
    assert policy.resolve is resolve_module.resolve
    assert policy.select is select_module.select
    assert callable(policy.select)


def test_no_submodule_shadows_a_name_the_package_publishes() -> None:
    """``policy/evaluate.py`` and ``policy.evaluate`` cannot both exist.

    Importing the submodule binds the name on the package to the *module*, so a caller that
    wrote ``from factory_intelligence.policy import evaluate`` would get a module where the
    engine's function is documented — and the two objects would disagree only at run time, in
    whichever order the imports happened to arrive in.
    """
    import pkgutil

    published = set(policy.__all__)
    submodules = {
        info.name for info in pkgutil.iter_modules(policy.__path__)
    }
    assert not (published & submodules), (
        f"published names shadowed by submodules: {sorted(published & submodules)}"
    )
