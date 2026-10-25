"""Assembly-time public-API compatibility (FR-DEP-005, CP-RT-005).

S1 is what turns S0-S4 from five libraries into one running system, so the assembled
combination has to be checked when it is built, not when a customer hits it:

* every provider package resolves to the real subsystem rather than to S1's fallback double;
* every name S1's adapters dispatch to actually exists on that package's public module;
* ``initialize()`` and ``MIGRATION_CONFIG`` are exported by each, because the startup order in
  00 doc 6.10 has no other way to find out;
* S1's declaration of which calls perform external I/O agrees with the provider's own
  marking.  This one is worth calling out: the two lists live in different repositories, and
  when they disagree the failure is a ``TransactionMisuseError`` raised in the middle of a
  stage run -- or, the other way round, an artifact read silently performed inside the
  caller's transaction, which is what 00 doc 6.2 exists to prevent.

Run it alone with ``pytest -m assembly``; it needs no database, no network and no DHIS2, so
it is cheap enough to put in front of a build.
"""

from __future__ import annotations

import importlib
from typing import Any

import pytest

pytestmark = pytest.mark.assembly

#: the library subsystems S1 assembles, by the module each one publishes its API through
PROVIDER_MODULES = {
    "S0": "factory_foundation.api",
    "S2": "factory_blueprint.api",
    "S3": "factory_intelligence.api",
    "S4": "factory_dhis2.api",
}


def _adapters() -> dict[str, type]:
    from factory_control.adapters.registry import BlueprintAdapter, ExecutionAdapter

    return {"S2": BlueprintAdapter, "S4": ExecutionAdapter}


def test_every_provider_package_resolves_to_the_real_subsystem() -> None:
    """``FACTORY_PROVIDER_MODE=auto`` silently falls back to a double; assembly must not."""
    from factory_control.config import Settings
    from factory_control.ports.protocols import BlueprintPort, ExecutionPort, IntelligencePort
    from factory_control.testing.fakes import FakeBlueprint, FakeExecution, FakeIntelligence

    missing = [
        label
        for label, module in PROVIDER_MODULES.items()
        if not _importable(module)
    ]
    assert not missing, f"assembling without {missing}: FR-DEP-005 cannot be satisfied"

    from factory_control.adapters.registry import default_ports

    ports = default_ports(settings=Settings(provider_mode="real"))
    for name, protocol in (
        ("blueprint", BlueprintPort),
        ("intelligence", IntelligencePort),
        ("execution", ExecutionPort),
    ):
        resolved = ports[name]
        double = {"blueprint": FakeBlueprint, "intelligence": FakeIntelligence, "execution": FakeExecution}[name]
        assert not isinstance(resolved, double), (
            f"{double.__name__} survived a real-mode resolution of {name!r}"
        )
        # checked member by member rather than with isinstance: the adapters delegate through
        # ``__getattr__`` and the protocols declare a data member (EXTERNAL_IO_METHODS), and
        # 3.12's runtime_checkable isinstance refuses that combination even when the object
        # answers every name. test_port_conformance.py covers the same surface on the fakes.
        unmet = sorted(
            member
            for member in protocol.__protocol_attrs__
            if not hasattr(resolved, member)
        )
        assert not unmet, f"{name} resolved to {type(resolved).__name__}, which lacks {unmet}"


@pytest.mark.parametrize("label", ["S2", "S3", "S4"])
def test_each_provider_exports_the_startup_surface_the_runner_needs(label: str) -> None:
    """00 doc 6.12: ``initialize()`` and ``MIGRATION_CONFIG`` are how S1 boots a subsystem."""
    module = importlib.import_module(PROVIDER_MODULES[label])
    assert callable(getattr(module, "initialize", None)), f"{label} publishes no initialize()"
    migration = getattr(module, "MIGRATION_CONFIG", None)
    assert migration is not None, f"{label} publishes no MIGRATION_CONFIG"
    assert getattr(migration, "schema", None), f"{label}'s MIGRATION_CONFIG names no schema"


def test_foundation_is_initializable_through_its_own_entry_point() -> None:
    from factory_foundation.api import CORE_MIGRATION_CONFIG, initialize_foundation

    assert callable(initialize_foundation)
    assert CORE_MIGRATION_CONFIG.schema == "core"


@pytest.mark.parametrize("adapter_label", ["S2", "S4"])
def test_external_io_declarations_match_what_the_provider_actually_marks(
    adapter_label: str,
) -> None:
    """A method S1 believes is safe to call inside a transaction must be one the provider agrees is.

    The provider is the only party that knows whether it reaches for an artifact or a network
    socket, and S0's ``@external_io`` is where that is recorded; S1's adapter carries a copy so
    its delegating adapter can open or refuse a unit of work around the call.  The copy is the
    risk, and this is the check that keeps the two in step.
    """
    from factory_foundation.api import is_external_io

    adapter = _adapters()[adapter_label]
    module = importlib.import_module(PROVIDER_MODULES[adapter_label])
    declared_external = set(adapter.EXTERNAL_IO_METHODS)

    wrongly_declared: list[str] = []
    undeclared: list[str] = []
    for port_name, (provider_name, _dto) in adapter.METHOD_MAP.items():
        function: Any = getattr(module, provider_name, None)
        if function is None:
            continue  # a missing name is reported by the seam test, not twice here
        marked = is_external_io(function)
        # the provider refuses the call inside a transaction, so S1 must not open one for it
        if marked and port_name not in declared_external and port_name not in adapter.SESSION_METHODS:
            undeclared.append(port_name)
        if not marked and port_name in declared_external:
            wrongly_declared.append(port_name)

    assert not undeclared, (
        f"{adapter.__name__} may open a unit of work over {undeclared}, which {module.__name__} "
        "marks as external I/O -- the call would raise TransactionMisuseError mid-stage"
    )
    assert not wrongly_declared, (
        f"{adapter.__name__} refuses transactions for {wrongly_declared}, which "
        f"{module.__name__} does not mark as external I/O; the over-declaration is harmless "
        "today and hides the reverse mistake tomorrow"
    )


@pytest.mark.parametrize("adapter_label", ["S2", "S4"])
def test_every_dispatched_name_exists_on_the_provider_module(adapter_label: str) -> None:
    from factory_control.adapters.delegating import DelegatingAdapter

    adapter = _adapters()[adapter_label]
    assert issubclass(adapter, DelegatingAdapter)
    module = importlib.import_module(PROVIDER_MODULES[adapter_label])
    missing = sorted(
        provider_name
        for _port_name, (provider_name, _dto) in adapter.METHOD_MAP.items()
        if provider_name and not hasattr(module, provider_name)
    )
    assert not missing, f"{adapter.__name__} dispatches to names {module.__name__} lacks: {missing}"


def _importable(module: str) -> bool:
    try:
        importlib.import_module(module)
    except ImportError:
        return False
    return True
