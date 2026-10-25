"""Delegating adapters for the not-yet-published providers (D-13, D-14).

Each adapter resolves ``<provider>.api.<function>`` lazily by name, forwards the
call with the same keyword arguments the port declares, and coerces the returned
mapping onto S1's DTO mirror.  Nothing is cached in ``cp``: S1 returns exactly
what the provider says (CP-API-007).

Lazy import matters: ``factory-api`` must start with S2/S3/S4 absent (CP-RT-004),
and a missing provider must surface as one clear ``PROVIDER_UNAVAILABLE`` error at
the call site rather than an ``ImportError`` at boot.
"""

from __future__ import annotations

import importlib
from typing import Any

from pydantic import BaseModel

from factory_control.foundation import FactoryError
from factory_control.ports.protocols import ProviderUnavailableError


class ProviderMethodUnavailableError(ProviderUnavailableError):
    """The provider is installed but does not export the function S1 calls."""


def _plain(model: BaseModel) -> dict[str, Any]:
    """A provider model as JSON-native data.

    ``model_dump()`` keeps ``str``-enum members as instances and datetimes as objects.
    S1\u2019s mirrors declare those fields as ``str``, and a ``str`` subclass would be
    accepted while carrying its repr (``"DeliveryMode.A"``), which then reaches S1\u2019s
    state-machine comparisons.  ``mode="json"`` is what makes the mirror hold "A".
    """
    return dict(model.model_dump(mode="json"))


def _resolve(package: str, function: str) -> Any:
    try:
        module = importlib.import_module(f"{package}.api")
    except ImportError as exc:
        raise ProviderUnavailableError(
            f"provider package {package!r} is not installed; "
            "configure FACTORY_PROVIDER_MODE=fakes or install it"
        ) from exc
    target = getattr(module, function, None)
    if target is None:
        raise ProviderMethodUnavailableError(
            f"{package}.api exports no function {function!r}"
        )
    if not callable(target):
        raise ProviderMethodUnavailableError(f"{package}.api.{function} is not callable")
    return target


def _coerce(value: Any, model: type[BaseModel] | None) -> Any:
    if model is None or value is None:
        return value
    if isinstance(value, model):
        return value
    if isinstance(value, BaseModel):
        return model.model_validate(_plain(value))
    if isinstance(value, dict):
        return model.model_validate(value)
    if isinstance(value, list):
        return [model.model_validate(_plain(item) if isinstance(item, BaseModel) else item) for item in value]
    return value


class DelegatingAdapter:
    """Base for the three provider adapters."""

    PROVIDER_PACKAGE: str = ""
    PROVIDER_LABEL: str = ""
    #: port method name -> (provider function name, result DTO)
    METHOD_MAP: dict[str, tuple[str, type[BaseModel] | None]] = {}
    #: port methods that must not run inside a unit of work (00 doc 6.2)
    EXTERNAL_IO_METHODS: frozenset[str] = frozenset()
    #: port methods whose provider signature requires a ``session`` it cannot open
    #: itself.  The read is pure (S2 SRS 7.2 keeps ``bp`` reads transaction-safe), so
    #: the adapter opens one short unit of work around just that call.
    SESSION_METHODS: frozenset[str] = frozenset()

    def __init__(self, *, resolver: Any = None) -> None:
        self._resolver = resolver or _resolve
        self._cache: dict[str, Any] = {}

    def available(self) -> bool:
        try:
            importlib.import_module(f"{self.PROVIDER_PACKAGE}.api")
        except ImportError:
            return False
        return True

    def __getattr__(self, name: str) -> Any:
        """Bind a port method onto the provider function it delegates to."""
        if name.startswith("_") or name in {
            "PROVIDER_PACKAGE",
            "PROVIDER_LABEL",
            "METHOD_MAP",
            "EXTERNAL_IO_METHODS",
            "SESSION_METHODS",
        }:
            raise AttributeError(name)
        mapping = self.METHOD_MAP.get(name)
        if mapping is None:
            raise AttributeError(
                f"{type(self).__name__} does not implement port method {name!r}"
            )
        function_name, result_model = mapping
        return self._bind(name, function_name, result_model)

    def _bind(self, method: str, function_name: str, result_model: type[BaseModel] | None) -> Any:
        def call(*args: Any, **kwargs: Any) -> Any:
            return self._call(method, function_name, result_model, args, kwargs)

        call.__name__ = method
        return call

    def _call(
        self,
        method: str,
        function_name: str,
        result_model: type[BaseModel] | None,
        args: tuple[Any, ...],
        kwargs: dict[str, Any],
    ) -> Any:
        """The one place the three transaction regimes are told apart."""
        target = self._cached(function_name)
        if method in self.EXTERNAL_IO_METHODS:
            self._assert_outside_transaction(method)
            return _coerce(target(*args, **kwargs), result_model)
        if method in self.SESSION_METHODS:
            # A caller that passed ``session=None`` meant "you choose", not "I have
            # one", so the key is dropped before the ambient session is injected.
            if kwargs.get("session") is None:
                kwargs.pop("session", None)
                from factory_control.foundation import unit_of_work

                with unit_of_work() as session:
                    return _coerce(target(*args, session=session, **kwargs), result_model)
            return _coerce(target(*args, **kwargs), result_model)
        if kwargs.get("session") is None:
            kwargs.pop("session", None)
        return _coerce(target(*args, **kwargs), result_model)

    def _function(self, function_name: str) -> Any:
        """Resolve a provider function by name, for the explicit translations."""
        return self._cached(function_name)

    def _cached(self, function_name: str) -> Any:
        if function_name not in self._cache:
            self._cache[function_name] = self._resolver(self.PROVIDER_PACKAGE, function_name)
        return self._cache[function_name]

    @staticmethod
    def _assert_outside_transaction(method: str) -> None:
        from factory_control.foundation import has_active_uow

        if has_active_uow():
            raise FactoryError(
                f"{method} performs external I/O and must be called with no unit of work open "
                "(00 doc 6.2)",
                code="TRANSACTION_MISUSE",
                details={"method": method},
            )

__all__ = [
    "DelegatingAdapter",
    "ProviderMethodUnavailableError",
    "ProviderUnavailableError",
]
