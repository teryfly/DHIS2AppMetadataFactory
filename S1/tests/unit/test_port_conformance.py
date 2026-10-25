"""Every port method must exist on the fake *and* be reachable by the adapter.

``testing/fakes.py`` claims this file guards it, and it must: a port method added
without a fake counterpart fails only at call time, deep inside a request, and a
port method added without a ``METHOD_MAP`` entry fails only when a deployment
actually runs against the real provider.  Both are silent until then, which is
exactly how S1 would learn about them late.
"""

from __future__ import annotations

import importlib
import inspect
import pathlib
import re
from typing import Any

import pytest

import factory_control
from factory_control.adapters.registry import (
    BlueprintAdapter,
    ExecutionAdapter,
    IntelligenceAdapter,
)
from factory_control.ports.protocols import BlueprintPort, ExecutionPort, IntelligencePort
from factory_control.testing.fakes import FakeBlueprint, FakeExecution, FakeIntelligence

PAIRS: list[tuple[type, type, type]] = [
    (BlueprintPort, FakeBlueprint, BlueprintAdapter),
    (IntelligencePort, FakeIntelligence, IntelligenceAdapter),
    (ExecutionPort, FakeExecution, ExecutionAdapter),
]
_IDS = ["blueprint", "intelligence", "execution"]

#: Class attributes the Protocols declare that are not operations.
_NON_METHODS = {"EXTERNAL_IO_METHODS"}


def _port_methods(port: type) -> set[str]:
    return {
        name
        for name, member in inspect.getmembers(port, predicate=inspect.isfunction)
        if not name.startswith("_") and name not in _NON_METHODS
    }


@pytest.mark.parametrize(("port", "fake", "adapter"), PAIRS, ids=_IDS)
def test_fakes_implement_every_port_method(port: type, fake: type, adapter: type) -> None:
    del adapter
    instance: Any = fake()
    missing = sorted(name for name in _port_methods(port) if not hasattr(instance, name))
    assert not missing, f"{fake.__name__} is missing port methods: {missing}"


@pytest.mark.parametrize(("port", "fake", "adapter"), PAIRS, ids=_IDS)
def test_adapter_can_reach_every_port_method(port: type, fake: type, adapter: type) -> None:
    """Either a METHOD_MAP entry or an explicit translation — never neither."""
    del fake
    instance: Any = adapter()
    unmapped = [name for name in sorted(_port_methods(port)) if name not in adapter.METHOD_MAP]
    unreachable = [name for name in unmapped if not callable(getattr(instance, name, None))]
    assert not unreachable, f"{adapter.__name__} cannot reach port methods: {unreachable}"


@pytest.mark.parametrize(("port", "fake", "adapter"), PAIRS, ids=_IDS)
def test_transaction_classification_names_are_real_port_methods(
    port: type, fake: type, adapter: type
) -> None:
    """A typo in either set would silently drop the transaction guard."""
    del fake
    names = _port_methods(port)
    declared = set(adapter.EXTERNAL_IO_METHODS) | set(adapter.SESSION_METHODS)
    assert declared <= names, f"{adapter.__name__} classifies unknown methods: {sorted(declared - names)}"
    assert not set(adapter.EXTERNAL_IO_METHODS) & set(adapter.SESSION_METHODS), (
        f"{adapter.__name__} cannot both forbid and require a unit of work"
    )


@pytest.mark.parametrize(("port", "fake", "adapter"), PAIRS, ids=_IDS)
def test_fakes_accept_the_keyword_arguments_the_port_promises(port: type, fake: type, adapter: type) -> None:
    """A fake that drops an optional keyword fails only when a service passes it."""
    del adapter
    instance: Any = fake()
    for name in sorted(_port_methods(port)):
        port_sig = inspect.signature(getattr(port, name))
        fake_sig = inspect.signature(getattr(instance, name))
        if any(p.kind is inspect.Parameter.VAR_KEYWORD for p in fake_sig.parameters.values()):
            continue
        required = [
            parameter.name
            for parameter in port_sig.parameters.values()
            if parameter.default is not inspect.Parameter.empty
            and parameter.kind in (inspect.Parameter.POSITIONAL_OR_KEYWORD, inspect.Parameter.KEYWORD_ONLY)
        ]
        absent = [r for r in required if r not in fake_sig.parameters and r != "session"]
        assert not absent, f"{fake.__name__}.{name} drops port defaults {absent}"


#: The real provider module behind each adapter, keyed the way ``_IDS`` names them.
_REAL_MODULES = {
    "blueprint": "factory_blueprint.api",
    "intelligence": "factory_intelligence.api",
    "execution": "factory_dhis2.api",
}


@pytest.mark.parametrize(
    ("port", "fake", "adapter", "module"),
    [(port, fake, adapter, importlib.import_module(_REAL_MODULES[name]))
     for (port, fake, adapter), name in zip(PAIRS, _IDS, strict=True)],
    ids=_IDS,
)
def test_every_mapped_name_exists_on_the_real_provider_module(
    port: type, fake: type, adapter: type, module: Any
) -> None:
    """S1's METHOD_MAP has to name functions the real subsystem actually publishes.

    Every other check here proves the Fake side. A mapped name the real provider does not
    export is invisible until a deployment runs in ``real`` mode -- and the delegating
    adapter reaches some reads through ``getattr``, so a renamed or removed public function
    answers ``None`` to a console read instead of raising. That is how a cross-subsystem
    rename becomes a silently wrong answer rather than a failure.
    """
    del port, fake
    missing = sorted(
        target
        for _source, (target, _dto) in adapter.METHOD_MAP.items()
        if not hasattr(module, target)
    )
    assert not missing, f"{adapter.__name__} maps to names {module.__name__} does not export: {missing}"


# --- CP-UI-006 item 6 / CP-RT-005: the transitional facade is not S1's contract ---------

#: S1 source that reaches S3's ``governance(action, payload)``.  CP-UI-006 item 6 permits the
#: façade in exactly one place -- inside the S3 adapter -- until the end of M2.  S3 has since
#: exported every capability as a typed function (AI-GOV-001, AI-GOV-009), so this build needs
#: none of it and the list stays empty: a reference reappearing anywhere, adapter included,
#: fails the assembly.  ``src`` is scanned as text because the ban covers comments and
#: docstrings too -- a route forwarding an action name is invisible to ``inspect``.
FAÇADE_LOCATIONS: frozenset[str] = frozenset()

_FAÇADE_CALLS = re.compile(r"\bgovernance\s*\(")
_FAÇADE_KEYS = re.compile(r"""["']governance["']\s*:""")
#: a typed call is allowed to say the word in prose; only a *call* or a mapping is a use
_PROSE = re.compile(r"`{1,2}governance\(action, payload\)`{1,2}|``governance``")


def test_no_s1_source_reaches_the_governance_facade() -> None:
    package = pathlib.Path(factory_control.__file__).parent
    found: list[str] = []
    for path in sorted(package.rglob("*.py")):
        if "__pycache__" in str(path) or path.name == "governance.py":
            # the module *named* governance is S1's own service and routes, not the façade
            continue
        for line in path.read_text().splitlines():
            stripped = _PROSE.sub("", line)
            if _FAÇADE_CALLS.search(stripped) or _FAÇADE_KEYS.search(stripped):
                found.append(f"{path.relative_to(package)}: {line.strip()}")
    allowed = {name for name in FAÇADE_LOCATIONS}
    offenders = [hit for hit in found if hit.split(":")[0] not in allowed]
    assert not offenders, "S1 still calls S3's action-name facade (CP-UI-006 item 6): " + "; ".join(
        offenders
    )


def test_the_intelligence_port_declares_no_action_method() -> None:
    """The port is the contract, and it has no ``governance`` to call."""
    assert not hasattr(IntelligencePort, "governance")
    assert not hasattr(FakeIntelligence(), "governance")
    assert "governance" not in dict(IntelligenceAdapter.METHOD_MAP)
    assert "governance" not in {target for _source, (target, _dto) in IntelligenceAdapter.METHOD_MAP.items()}


def test_every_typed_governance_function_s3_offers_is_reachable_or_explained() -> None:
    """A capability S3 answers for is either a port method or a documented non-forward.

    This is the direction that keeps the two subsystems honest once the transition window
    closes: S3's own table names the typed function behind each transitional action, so a
    capability S1 has stopped forwarding cannot hide as an action string.
    """
    dispatcher = importlib.import_module("factory_intelligence.governance.dispatcher")
    intelligence = importlib.import_module("factory_intelligence.api")

    #: capabilities with no port method, and why S1 does not forward them
    NOT_FORWARDED = {
        "reload_prompt_sources": "re-reads S3's own prompt pack from disk: an operator action "
        "with no S5 surface and no S1 endpoint",
        "list_input_parses": "S1 answers parse status from its own StageRun records, which is "
        "what keeps CP-API-007 and UI-BR-02 intact (services/inputs.py)",
        "get_column_mapping_proposal": "S3's structured TablePlan stays inside the NRR document "
        "(S5IF-UP07); S1 forwards each review Question's own proposal",
        "query_nrr": "S1 reads NRR entries from the artifact through S2 (blueprint_reads), so "
        "the workflow view has one source for entry content",
    }

    targets = {target for _source, (target, _dto) in IntelligenceAdapter.METHOD_MAP.items()}
    missing = sorted(
        function
        for function in dispatcher.ACTION_CAPABILITIES.values()
        if function and function not in targets and function not in NOT_FORWARDED
    )
    assert not missing, (
        f"S3 publishes governance functions S1's port neither reaches nor declines: {missing}"
    )
    for function in NOT_FORWARDED:
        assert hasattr(intelligence, function), (
            f"S3 dropped {function}; the reason in NOT_FORWARDED no longer applies"
        )
