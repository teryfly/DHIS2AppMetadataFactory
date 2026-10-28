"""The role table has to cover every action a route asks for (FR-SEC-002, CP-IAM-001).

`tests/api/test_identity_m2.py`-style acceptance runs reach the export, the delete and the rest
under ``auth=off``, where the policy allows everything — so an action that no built-in role holds
passes every test in the suite and then answers 403 for the first real, authenticated operator.
That is not hypothetical: `project.export` was unreachable that way, while CP-PRJ-007 makes the
export a precondition of the delete it was granted next to.
"""

from __future__ import annotations

import ast
from pathlib import Path

from factory_control import iam
from factory_control.identity import roles as R

ROUTES = Path(__file__).resolve().parents[2] / "src" / "factory_control" / "rest" / "routes"

#: ``iam.NAME`` in a route means the string that constant carries, so the scan reads the module
#: rather than the identifier: ``iam.PROJECT_EXPORT`` is "project.export", and a renamed constant
#: would otherwise look like an action nobody granted.
_CONSTANTS = {k: v for k, v in vars(iam).items() if k.isupper() and isinstance(v, str)}


def _authorized_actions() -> dict[str, set[str]]:
    """Every action name a route hands to an authorize hook, with the route files that ask for it."""
    found: dict[str, set[str]] = {}
    # The project-scoped helpers take the action last, so a scan for `authorize` alone would
    # quietly stop covering every route that moved to them — and this test's whole purpose is
    # to notice an action that no role holds.
    positions = {"authorize": 1, "authorize_stage": 2, "authorize_workflow": 2, "authorize_run": 2, "authorize_gate": 2}
    for path in sorted(ROUTES.glob("*.py")):
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if not (isinstance(node, ast.Call) and getattr(node.func, "id", "") in positions):
                continue
            index = positions[node.func.id]
            if len(node.args) < index + 1:
                continue
            action = node.args[index]
            # ``iam.execution_commit_action("PRODUCTION")`` and friends are expanded per environment
            # class, and are covered by the role table's own spelled-out entries.
            if isinstance(action, ast.Attribute) and getattr(action.value, "id", "") == "iam":
                name = _CONSTANTS.get(action.attr, "?unknown-constant")
                found.setdefault(name, set()).add(path.name)
    return found


def test_every_gate_type_has_an_approver() -> None:
    """总体 SRS 16.1 names a default approver for each of the seven Gates, so each needs a holder.

    The route scan cannot see this: a Gate's action is built at request time from the body
    (``iam.gate_approve_action(payload.gate_type)``), so no literal appears in a route.  When
    ``roles.py`` spelled its Gate list by hand it drifted — it granted
    ``gate.approve.METADATA_GATE`` and ``gate.approve.FIXTURE_GATE``, which are not GateTypes,
    and omitted ``DESIGN_GATE`` and ``PACKAGE_GATE``, which are.  A Run therefore reached
    STATIC_VALIDATION with no identity able to approve it, and the first real deployment proved
    it: 403 for the Gate the fakes always waved through.
    """
    from factory_control.domain.enums import GateType

    held: set[str] = set()
    for granted in R.ROLE_ACTIONS.values():
        held |= set(granted)

    for gate in GateType:
        if gate is GateType.DEPLOY_GATE:
            # 总体 SRS 16.1: DEPLOY_GATE is approved per environment class, never by bare name.
            by_class = [
                f"{iam.GATE_APPROVE_PREFIX}.{gate.value}.{class_name}"
                for class_name in ("TEST", "STAGING", "PRODUCTION")
            ]
            unheld = [name for name in by_class if name not in held]
            assert not unheld, f"{unheld} have no approver role"
            continue
        assert iam.GATE_APPROVE_PREFIX + "." + gate.value in held, (
            f"{gate.value} has no approver role; 总体 SRS 16.1 names one for every Gate"
        )


def test_domain_gate_is_granted_to_the_solution_architect_only() -> None:
    """SRS 16.1: domain design is approved by the architect, not by any other built-in role."""
    action = "gate.approve.DOMAIN_GATE"
    holders = {role for role, granted in R.ROLE_ACTIONS.items() if action in granted}
    assert holders == {R.SOLUTION_ARCHITECT}, holders


def test_every_authorized_action_is_grantable() -> None:
    actions = _authorized_actions()
    assert actions, "the scan found no authorize() calls; the guard would be vacuous"
    held: set[str] = set()
    for granted in R.ROLE_ACTIONS.values():
        held |= set(granted)
    ungrantable = sorted(name for name in actions if name not in held)
    assert not ungrantable, [
        f"{name} is authorized by {sorted(actions[name])} but held by no built-in role"
        for name in ungrantable
    ]


def test_a_role_that_may_delete_may_also_produce_what_the_delete_requires() -> None:
    """CP-PRJ-007: the deletion of an archived project is refused until a PROJECT_EXPORT exists.

    Granting one half of a two-step rule is how the other half becomes a dead end, and under
    ``auth=off`` it looks fine: the state machine answers 409 ``requires: PROJECT_EXPORT`` while
    every identity in the system is told 403 that it cannot make one.
    """
    for role, granted in R.ROLE_ACTIONS.items():
        if iam.PROJECT_DELETE in granted:
            assert iam.PROJECT_EXPORT in granted, f"{role} may delete projects but cannot export one"



_ORG_OBJECT = "一个环境/凭据是组织级对象（S4 的 dx 与 core.secret 都没有 project 列），无从指认项目"
_ANY_PROJECT_PAGE = "跨项目的列表或可用性页，在有任何项目之前就要能问"

#: Every route that asks for a project role's action while naming no project, with the reason it
#: may stay that way.  A stale entry fails the test as loudly as a new one, so this cannot quietly
#: rot into a list of excuses.
UNSCOPED_ALLOWLIST: dict[tuple[str, str, str], str] = {
    ("environments.py", "/environments", "project.view"): _ORG_OBJECT,
    ("environments.py", "/environments", "environment.create"): _ORG_OBJECT,
    ("environments.py", "/environments/{environment_key}", "project.view"): _ORG_OBJECT,
    ("environments.py", "/environments/{environment_key}", "environment.edit"): _ORG_OBJECT,
    ("environments.py", "/environments/{environment_key}/disable", "environment.disable"): _ORG_OBJECT,
    ("environments.py", "/environments/{environment_key}/protection", "project.view"): _ORG_OBJECT,
    (
        "environments.py",
        "/environments/{environment_key}/test-connection",
        "environment.test_connection",
    ): _ORG_OBJECT,
    ("environments.py", "/environments/{environment_key}/capability-profile", "project.view"): _ORG_OBJECT,
    (
        "environments.py",
        "/environments/{environment_key}/probe-capabilities",
        "environment.probe_capabilities",
    ): _ORG_OBJECT,
    ("environments.py", "/environments/{environment_key}/discovery-results", "project.view"): _ORG_OBJECT,
    ("secrets.py", "/secrets", "secret.manage"): _ORG_OBJECT,
    ("secrets.py", "/secrets/{name}", "secret.manage"): _ORG_OBJECT,
    ("governance.py", "/governance/policy/relaxations", "governance.policy.relax"):
        "一次放宽是对整个 Factory 生效的，审计行也是平台级的",
    ("projects.py", "/projects", "project.edit"): "建项目无法预先属于某个还不存在的项目",
    ("catalog.py", "/allowed-actions", "project.view"):
        f"{_ANY_PROJECT_PAGE}；它自己的 docstring 就写了『在任何项目存在之前』",
    ("catalog.py", "/audit", "audit.view"):
        "全厂审计查询；按项目过滤是查询参数，不是授权作用域",
    ("catalog.py", "/audit/verify", "audit.verify"):
        "哈希链校验针对整条日志，没有项目能界定它",
    ("identity.py", "/roles", "project.view"): f"{_ANY_PROJECT_PAGE}；角色目录是参照数据",
    ("setup.py", "/setup", "project.view"):
        f"{_ANY_PROJECT_PAGE}；『还没有任何项目』正是这一页要显示的状态",
}


def test_a_project_role_action_is_never_asked_without_a_project() -> None:
    """Every action a project role holds must be authorized *with* a project (FR-SEC-002/004).

    ``directory.roles_of`` returns a project's role only when the check names the project, so a
    bare ``authorize(request, action)`` silently restricts the action to organisation-wide roles.
    Under ``auth=off`` that is invisible and every test passes; against the live deployment it was
    a 403 on ``patch.submit``, on ``gate.approve.DEPLOY_GATE``, on the whole Change Request flow and
    on the artifact download — all from this one cause.
    """
    import ast as _ast

    project_held: set[str] = set()
    for role, granted in R.ROLE_ACTIONS.items():
        if role != R.PLATFORM_ADMINISTRATOR:
            project_held |= set(granted)

    found: set[tuple[str, str, str]] = set()
    for path in sorted(ROUTES.glob("*.py")):
        for fn in _ast.walk(_ast.parse(path.read_text(encoding="utf-8"))):
            if not isinstance(fn, _ast.FunctionDef):
                continue
            route = next(
                (
                    str(d.args[0].value)
                    for d in fn.decorator_list
                    if isinstance(d, _ast.Call) and d.args and isinstance(d.args[0], _ast.Constant)
                ),
                fn.name,
            )
            for node in _ast.walk(fn):
                if not (isinstance(node, _ast.Call) and getattr(node.func, "id", "") == "authorize"):
                    continue
                if len(node.args) != 2:  # a third argument is the project, which is the point
                    continue
                action = node.args[1]
                if not (isinstance(action, _ast.Attribute) and getattr(action.value, "id", "") == "iam"):
                    continue
                name = _CONSTANTS.get(action.attr)
                if name and name in project_held:
                    found.add((path.name, route, name))

    unexplained = found - set(UNSCOPED_ALLOWLIST)
    assert not unexplained, [
        f"{file} {route} asks {action} with no project and no recorded reason"
        for file, route, action in sorted(unexplained)
    ]
    stale = set(UNSCOPED_ALLOWLIST) - found
    assert not stale, [f"{key} is allowed but is no longer unscoped" for key in sorted(stale)]
