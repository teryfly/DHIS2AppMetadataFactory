"""S1 test harness.

Composes S0's ``FoundationHarness`` (FD-TST-001: real PostgreSQL, memory object
store, fixed clock, memory secrets) with S1's own ``cp`` schema, registrations and
a runtime wired to the provider ports.

``providers="fakes"`` is the CP-ASM-08 substitute set and is what nearly every test
uses.  ``providers="real"`` runs the same start-up a deployment does — each installed
subsystem's own migrations and ``initialize()`` — so an S1↔S2 signature drift fails
a test rather than surfacing in production.  A subsystem whose package is not installed
keeps its fake in both modes, which is why ``scripts/install_dev.sh`` installs all four.
"""

from __future__ import annotations

import importlib.util
import urllib.parse
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from factory_control.bootstrap import initialize as initialize_s1
from factory_control.config import Settings
from factory_control.db import CP_MIGRATION_CONFIG
from factory_control.events.bus import EventBus
from factory_control.foundation import FixedClock, FoundationHarness, run_migrations
from factory_control.runtime import Runtime, build_runtime
from factory_control.testing.fakes import FakeBlueprint, FakeExecution, FakeIntelligence

TEST_URL_ENV = "FACTORY_TEST_DATABASE_URL"

#: Provider labels in the order 00 doc 6.10 initialises them.
PROVIDER_PACKAGES: dict[str, str] = {
    "S4": "factory_dhis2",
    "S2": "factory_blueprint",
    "S3": "factory_intelligence",
}

#: Schemas dropped on setup.  Append-only audit (FD-ART-007) cannot be DELETEd, so a
#: schema drop is the only wipe — and the provider schemas must go too in real mode.
#: ``dx`` matters as soon as S4 is installed: environments, ``dx.connection_test`` rows and
#: capability profiles would otherwise carry over into the next test, and S4's version table
#: lives inside ``dx``, so re-running its migrations is a no-op rather than a reset.
SCHEMAS = ("cp", "dx", "bp", "ai", "core")


def resolve_url(explicit: str | None = None) -> str:
    """Prefer the argument, then the environment (shared with S0's harness)."""
    import os

    if explicit:
        return explicit
    url = os.environ.get(TEST_URL_ENV)
    if url:
        return url
    return "postgresql+psycopg:///factory_s1_test?host=" + urllib.parse.quote("/var/run/postgresql")


def has_real_provider(label: str) -> bool:
    """Whether a subsystem is importable, so a test can skip rather than fake-pass."""
    package = PROVIDER_PACKAGES.get(label)
    return package is not None and importlib.util.find_spec(package) is not None


@dataclass
class ControlHarness:
    """The wiring one test runs against.

    ``blueprint`` / ``intelligence`` / ``execution`` are the ports, which in
    ``fakes`` mode are the presettable doubles (so a test can do
    ``harness.execution.add_environment(...)``) and in ``real`` mode are the
    adapters.
    """

    url: str
    runtime: Runtime
    blueprint: Any
    intelligence: Any
    execution: Any
    clock: FixedClock
    bus: EventBus
    providers: str = "fakes"

    def stage_statuses(self, workflow_id: str) -> dict[str, str]:
        from factory_control import store as S
        from factory_control.foundation import unit_of_work

        with unit_of_work() as session:
            return {s.stage_type: s.status for s in S.stages_of(session, workflow_id)}

    def preset_execution(self) -> FakeExecution:
        """The S4 double, for a real-mode test that left S4 out of ``real``.

        Such a test still has to name the environments a project targets, and
        :func:`factory_control.adapters.registry.default_ports` hands back the fake for any
        subsystem the caller did not ask to be real.  A harness where S4 really is wired
        raises instead: presetting a double nobody reads would make the test pass on
        nothing.
        """
        if isinstance(self.execution, FakeExecution):
            return self.execution
        raise AssertionError("no S4 fake in this harness; the flow needs S4 presetting")


@contextmanager
def control_harness(
    *,
    url: str | None = None,
    settings: Settings | None = None,
    delivery_mode: str = "A",
    providers: str = "fakes",
    real: tuple[str, ...] = ("S2", "S3", "S4"),
) -> Iterator[ControlHarness]:
    """A freshly provisioned ``core`` + ``cp`` with the ports wired in.

    ``providers="real"`` wires an installed subsystem's real adapter; ``real`` narrows
    which ones, so a test can put S2 behind real code while S3 keeps its fake.  An
    uninstalled subsystem is a fake regardless.
    """
    resolved = resolve_url(url)
    clock = FixedClock(datetime(2026, 1, 1, tzinfo=UTC))

    from sqlalchemy import create_engine, text

    bootstrap_engine = create_engine(resolved)
    with bootstrap_engine.begin() as conn:
        for schema in SCHEMAS:
            conn.execute(text(f"DROP SCHEMA IF EXISTS {schema} CASCADE"))
    bootstrap_engine.dispose()

    base = FoundationHarness(url=resolved)
    base.setup()
    try:
        from factory_control.services.environments import clear_environment_class_cache

        # A display cache that outlives a test would hand the next one the previous
        # environment's class.
        clear_environment_class_cache()
        if providers == "real":
            # The deployed start-up order (00 doc 6.10): every provider's own
            # migrations and initialize(), then S1's registration.
            from factory_control.bootstrap import bootstrap

            bootstrap(database_url=resolved)
        else:
            run_migrations([CP_MIGRATION_CONFIG], url=resolved)
            initialize_s1()

        from factory_control.foundation import unit_of_work
        from factory_control.settings import seed_missing_settings

        with unit_of_work() as session:
            seed_missing_settings(session=session)

        resolved_settings = settings or Settings(
            database_url=resolved,
            provider_mode="auto" if providers == "real" else "fakes",
        )
        blueprint, intelligence, execution = _ports(resolved_settings, providers, real)
        if providers == "real" and not is_real_adapter(blueprint):
            raise AssertionError("providers=\"real\" but S2 resolved to a fake: S2 is not importable")
        runtime = build_runtime(
            settings=resolved_settings,
            blueprint=blueprint,
            intelligence=intelligence,
            execution=execution,
            bus=EventBus(),
            clock=clock,
        )
        from factory_control.engine.inprocess import InProcessEngine

        runtime.engine = InProcessEngine(runtime=runtime)
        yield ControlHarness(
            url=resolved,
            runtime=runtime,
            blueprint=blueprint,
            intelligence=intelligence,
            execution=execution,
            clock=clock,
            bus=runtime.bus,
            providers=providers,
        )
    finally:
        base.teardown()


def _ports(settings: Settings, providers: str, real: tuple[str, ...]) -> tuple[Any, Any, Any]:
    """The three ports for this mode.

    ``real`` resolves through :func:`factory_control.adapters.registry.default_ports`
    in ``auto`` mode: the subsystems that are installed are wired for real and the rest
    fall back to their fakes, which is what a development deployment does.  A real-mode
    test must then assert on the port it actually cares about —
    :func:`is_real_adapter` — so a silently-substituted fake cannot make it pass.
    """
    if providers != "real":
        return FakeBlueprint(), FakeIntelligence(), FakeExecution()
    from factory_control.adapters.registry import default_ports

    resolved = default_ports(settings=settings.model_copy(update={"provider_mode": "auto"}))
    selected: list[Any] = []
    for label, port, fake in (
        ("S2", resolved["blueprint"], FakeBlueprint),
        ("S3", resolved["intelligence"], FakeIntelligence),
        ("S4", resolved["execution"], FakeExecution),
    ):
        keep = label in real and is_real_adapter(port)
        selected.append(port if keep else fake())
    if not is_real_adapter(selected[0]) and "S2" in real:
        raise AssertionError("providers=\"real\" asked for S2 but factory_blueprint is not importable")
    blueprint_out, intelligence_out, execution_out = selected
    return blueprint_out, intelligence_out, execution_out


def is_real_adapter(port: Any) -> bool:
    """Whether this port is a provider adapter rather than a CP-ASM-08 fake."""
    return not type(port).__name__.startswith("Fake")


def seed_project(
    harness: ControlHarness,
    *,
    name: str = "clinic dashboard",
    delivery_mode: str = "A",
    environments: tuple[tuple[str, str], ...] = (("dev", "TEST"), ("prod", "PRODUCTION")),
    design_reference: str = "dev",
    version_constraints: str = "2.41.x",
    code_prefix: str = "",
) -> dict[str, Any]:
    """Create a project plus its S4 environments and S2 Blueprint state.

    The S2 faking (``set_project`` / ``open_draft``) is skipped in real mode: S2
    itself creates the draft as part of ``create_project``.
    """
    preset = harness.execution if isinstance(harness.execution, FakeExecution) else None
    if preset is not None:
        for key, environment_class in environments:
            preset.add_environment(key, environment_class=environment_class)
            # AC-CAP-02: a Run may only be created for an environment that answered its
            # connection test.  Recording it here is what "the operator provisioned this
            # environment and it works" means on the Fake side; a test that wants the
            # refused-before-deploying case deletes this record or never seeds at all.
            preset.test_connection(key, ["discovery", "import"], idempotency_key=f"seed:{key}")
    fake_blueprint = harness.blueprint if isinstance(harness.blueprint, FakeBlueprint) else None
    if fake_blueprint is not None:
        fake_blueprint.set_project("__pending__", delivery_mode=delivery_mode)

    from factory_control.services.project import CreateProjectRequest, TargetEnvironment, create_project

    targets = [
        TargetEnvironment(
            environment_key=key,
            promotion_order=index * 100,
            is_design_reference=(key == design_reference),
        )
        for index, (key, _cls) in enumerate(environments)
    ]
    if fake_blueprint is not None:
        fake_blueprint.projects.pop("__pending__", None)
    view = create_project(
        harness.runtime,
        CreateProjectRequest(
            name=name,
            owner_name="ada",
            description="demo",
            delivery_mode=delivery_mode,
            version_constraints=version_constraints,
            code_prefix=code_prefix,
            targets=targets,
        ),
        operator="ada",
    )
    if fake_blueprint is not None:
        fake_blueprint.set_project(view["id"], delivery_mode=delivery_mode)
        fake_blueprint.open_draft(view["id"], "REQUIREMENTS")
    return view


__all__ = [
    "ControlHarness",
    "control_harness",
    "has_real_provider",
    "resolve_url",
    "seed_project",
]
