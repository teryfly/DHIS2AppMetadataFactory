"""Everything either console gates on must be a name `allowed-actions` can actually answer.

Why this test lives in S1 rather than in a front end: the availability rule is that a control is
enabled only when `allowed-actions` lists its action (UI-BR-01 / UM-BR-01), and a name the server
never lists resolves to "unavailable, no reason given".  A console cannot see that mistake from the
inside — the button is simply always grey, which looks like a permission problem and gets filed
as one.

Two sources of that bug have already been caught here:

* Three names in S5's table (`project.member.manage`, `artifact.grant.manage`,
  `production.exemption.manage`) that S1 has never emitted under those spellings, leaving the
  members, grants and exemptions screens permanently disabled even for a platform administrator.
* `PLATFORM_ACTIONS` listing `settings.update` — a suffix copied from the `cp.settings.updated`
  audit event — while `PUT /settings` authorizes `settings.manage`, which is also the spelling the
  SRS's own vocabulary gives (CP-UI-019).

That second one is why the authority is :mod:`factory_control.domain.actions`, the module that
*builds the answer*, and not `iam.py`.  `iam` is the set of rights a request is checked against;
a right that no group lists is precisely the defect above, so a guard that also accepted every
`iam` constant whitewashed it for a whole round.

The consoles' mock harnesses are checked in the same pass.  A fixture that invents a name keeps
every unit and end-to-end test green while teaching the next author that the name is real — the MSW
answer is not the server, and only this test reads it against the server.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from factory_control.domain import actions as ACTIONS

REPO = Path(__file__).resolve().parents[3]

#: An action name as it appears on the wire: lower-case dotted, no `cp.` audit prefix.
_ACTION_RE = re.compile(r"[a-z][a-z0-9_]*(?:\.[a-z0-9_]+)+")

#: Sets that *classify* names rather than introduce them. Reading them would widen the accepted
#: vocabulary without ever appearing in a response.
_FILTER_SETS = ("POLICY_GATED_ACTIONS", "M4_GATED_ACTIONS", "DISABLED_REFUSED_ACTIONS")

#: The groups `allowed-actions` is assembled from — PROJECT, WORKFLOW, CHANGE_REQUEST, FORWARDING,
#: ENVIRONMENT, PLATFORM, STAGE — collected by shape so a group added later is covered the day it
#: is written.
def _listable_actions() -> set[str]:
    found: set[str] = set()
    for name in dir(ACTIONS):
        if not name.isupper() or name in _FILTER_SETS:
            continue
        value = getattr(ACTIONS, name)
        if not isinstance(value, (tuple, frozenset, set, list)):
            continue
        for candidate in value:
            if isinstance(candidate, str) and _ACTION_RE.fullmatch(candidate):
                found.add(candidate)
    return found


_TABLE = re.compile(r"export const ACTION = \{(.*?)\n\} as const", re.S)
_ENTRY = re.compile(r"([A-Za-z0-9_]+)\s*:\s*'([a-z0-9_.]+)'")
#: An entry in a mocked `allowed` / `unavailable` list, in either console's harness.
_MOCKED = re.compile(r"""action:\s*['"]([a-z0-9_.]+)['"]""")

CONSOLE_TABLES = ("S5/src/api/actions.ts", "S5M/src/api/actions.ts")
# The desktop's fixtures live in `src/mocks/db.ts`; the mobile one answers from its own dev server
# script (`scripts/mock-s1.mjs`), which is what the Playwright suites run against.
MOCK_HARNESSES = ("S5/src/mocks/db.ts", "S5M/scripts/mock-s1.mjs")


def _source(relative: str) -> str:
    path = REPO / relative
    assert path.is_file(), f"{relative} is gone — a console harness moved without this test"
    return path.read_text(encoding="utf-8")


def _console_actions(relative: str) -> dict[str, str]:
    match = _TABLE.search(_source(relative))
    assert match, f"{relative}: no `export const ACTION = { ... } as const` table found"
    return {key: value for key, value in _ENTRY.findall(match.group(1))}


@pytest.mark.parametrize("relative", CONSOLE_TABLES)
def test_every_action_a_console_gates_on_is_one_s1_can_list(relative: str) -> None:
    table = _console_actions(relative)
    assert table, f"{relative}: the ACTION table is empty"
    listable = _listable_actions()
    # a vocabulary that collapsed to nothing would make the assertion below vacuously true
    assert len(listable) > 40, f"S1's listable vocabulary is only {sorted(listable)}"
    unknown = {key: value for key, value in table.items() if value not in listable}
    assert not unknown, f"{relative} gates on names allowed-actions cannot list: {unknown}"


@pytest.mark.parametrize("relative", MOCK_HARNESSES)
def test_every_action_a_mock_answers_is_one_s1_can_list(relative: str) -> None:
    names = set(_MOCKED.findall(_source(relative)))
    assert names, f"{relative}: no `action: '<name>'` entries found — did the harness move?"
    unknown = sorted(names - _listable_actions())
    assert not unknown, f"{relative} invents action names S1 never lists: {unknown}"


def test_the_two_consoles_gate_on_the_same_vocabulary() -> None:
    """The same button on both consoles must be gated by the same server answer.

    Divergence here is not cosmetic: a control missing from one table cannot be gated at all, so
    that console decides availability locally — which is the rule UM-BR-01 exists to forbid.
    """
    desktop = set(_console_actions("S5/src/api/actions.ts").values())
    mobile = set(_console_actions("S5M/src/api/actions.ts").values())
    assert desktop - mobile == set(), f"desktop-only actions: {sorted(desktop - mobile)}"


def test_the_settings_right_is_listable_under_the_name_the_route_checks() -> None:
    """`PUT /settings` authorizes `settings.manage`, so that is the name the answer must carry.

    Pinned on its own because the two spellings differ by an audit event's suffix, and an audit
    event type is not a vocabulary: `cp.settings.updated` is an event, `settings.manage` is a right,
    and only one of them can ever appear in `allowed-actions`.
    """
    from factory_control import iam

    assert iam.SETTINGS_MANAGE in _listable_actions()
