"""The real S4 boundary: S2's protocol satisfied by ``factory_dhis2.api``.

This is the only S2 module that imports S4.  The import is deferred to the first call
so S2 stays importable where S4 is not installed (S1 and S3 install S2 without S4);
when a call is made anyway it fails with ``INTERNAL_ERROR`` naming the missing package,
never with an invented answer.

Every call is bound against S4's real signature by a test
(``tests/integration/test_s4_interop.py``), so a rename or a reordered parameter on
either side fails S2's build rather than surfacing at a customer's freeze.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from factory_foundation.api import FactoryError

from factory_blueprint.s4.protocol import CodeOccupancy, ReadonlyDhis2, UidOccupancy

#: S4 answers its existence questions with a mapping that also names the occupant.
Occupancy = Mapping[str, Any]


def _occupancies(
    answer: Any, identifier: str, requested: Sequence[str]
) -> dict[str, Occupancy]:
    """Validate the shape S4 returned, and make the answer total over what was asked.

    An unreadable entry raises rather than being dropped, and a silent gap raises rather
    than being read as "free": either one would let a freeze hand out a number some
    object already holds.  A partial answer is a broken provider, not a negative result.
    """
    if not isinstance(answer, Mapping):
        raise FactoryError(
            f"S4 answered the {identifier} check with {type(answer).__name__}, "
            "expected a mapping of identifier to occupancy",
            code="INTERNAL_ERROR",
            details={"expected": "mapping", "actual": type(answer).__name__},
        )
    for key, value in answer.items():
        if not isinstance(value, Mapping):
            raise FactoryError(
                f"S4's {identifier} answer for {key!r} is {type(value).__name__}, "
                "expected an occupancy mapping",
                code="INTERNAL_ERROR",
                details={"identifier": str(key), "actual": type(value).__name__},
            )
    out = {str(key): value for key, value in answer.items()}
    missing = [str(item) for item in requested if str(item) not in out]
    if missing:
        raise FactoryError(
            f"S4 answered the {identifier} check for {len(out)} of the {len(requested)} "
            f"identifiers asked about; {missing[0]!r} and any other gap would be read as free",
            code="INTERNAL_ERROR",
            details={"missing": missing[:20], "asked": len(requested)},
        )
    return out


def _text(value: Any) -> str | None:
    return value if isinstance(value, str) and value else None


def _uid_occupancy(found: Occupancy) -> UidOccupancy:
    return UidOccupancy(
        exists=bool(found.get("exists")),
        dhis2_type=_text(found.get("dhis2Type")),
        code=_text(found.get("code")),
        name=_text(found.get("name")),
    )


def _code_occupancy(found: Occupancy) -> CodeOccupancy:
    return CodeOccupancy(
        exists=bool(found.get("exists")),
        uid=_text(found.get("uid")),
        name=_text(found.get("name")),
    )


class S4Adapter(ReadonlyDhis2):
    """Delegates the four read-only questions to ``factory_dhis2.api``."""

    def __init__(self, module: Any | None = None) -> None:
        self._module = module

    @property
    def module(self) -> Any:
        if self._module is None:
            try:
                import factory_dhis2.api as api  # noqa: PLC0415 - optional dependency
            except ImportError as exc:
                raise FactoryError(
                    "S4 (factory_dhis2) is not installed, so S2 cannot ask DHIS2 the "
                    "read-only questions that need a real instance: install "
                    "factory-blueprint[s4], or set an explicit implementation with "
                    "factory_blueprint.api.set_dhis2.",
                    code="INTERNAL_ERROR",
                    details={"dependency": "factory_dhis2"},
                ) from exc
            self._module = api
        return self._module

    def generate_uids(self, environment_key: str, count: int) -> list[str]:
        return [str(uid) for uid in self.module.generate_uids(environment_key, count)]

    def uid_exists(self, environment_key: str, uids: Sequence[str]) -> dict[str, UidOccupancy]:
        requested = list(uids)
        answer = _occupancies(
            self.module.uid_exists(environment_key, requested), "UID", requested
        )
        return {uid: _uid_occupancy(found) for uid, found in answer.items()}

    def code_exists(
        self, environment_key: str, dhis2_type: str, codes: Sequence[str]
    ) -> dict[str, CodeOccupancy]:
        requested = list(codes)
        answer = _occupancies(
            self.module.code_exists(environment_key, dhis2_type, requested), "code", requested
        )
        return {code: _code_occupancy(found) for code, found in answer.items()}

    def has_associated_data(self, environment_key: str, dhis2_type: str, uid: str) -> bool:
        verdict = self.module.has_associated_data(environment_key, dhis2_type, uid)
        # S4's verdict mapping is truthy exactly when the object still holds data; the
        # field is read rather than the truthiness so a plain dict double behaves too.
        if isinstance(verdict, Mapping):
            return bool(verdict.get("hasData"))
        return bool(verdict)
