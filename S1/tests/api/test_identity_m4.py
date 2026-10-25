"""M4 identity and permissions at the wire (CP-IAM-002, FR-SEC-001~005, AC-GATE-04).

Three properties are why this file exists:

* **``off`` is the MVP unchanged.**  The declared ``X-Operator`` name stays the identity and the
  hook allows everything, so the existing suite and S5's current secure keep passing — the switch
  has to be inert, not merely undocumented (CP-IAM-001).
* **enforcement changes the answer, not the route.**  The same call is a 403 for a person without
  the environment-qualified role and a 200 for one with it; the person who executed a Run cannot
  approve its DEPLOY_GATE, and one person cannot fill two required approvals (AC-GATE-04).
* **a session behaves like a session.**  Logout and disabling the account end it immediately, and
  a cookie-authenticated change without its CSRF value is refused (FR-SEC-005).
"""

from __future__ import annotations

from typing import Any

import pytest
from fastapi.testclient import TestClient

from factory_control import iam
from factory_control.config import Settings
from factory_control.foundation import unit_of_work
from factory_control.identity import service
from factory_control.identity.policy import RolePermissionPolicy
from factory_control.identity.roles import DEPLOYMENT_APPROVER, SOLUTION_ARCHITECT

pytestmark = pytest.mark.api

OPERATOR = {"X-Operator": "ada"}
PASSWORD = "a-correct-horse-battery"

# `secure` and `enforced` come from tests/api/conftest.py, where they live beside the other file
# that exercises a real session (test_identity_lockout.py).

_POLICY = RolePermissionPolicy()


def _create(subject: str, *, role: str | None = None, project_id: str | None = None) -> dict[str, Any]:
    with unit_of_work() as session:
        created = service.create_user(session, "provisioner", subject=subject, password=PASSWORD)
        if role is not None:
            service.assign_role(session, "provisioner", created["id"], role, project_id=project_id)
            if project_id:
                service.add_member(session, "provisioner", project_id, created["id"], role=role)
        return created


def _sign_in(secure: TestClient, subject: str) -> dict[str, Any]:
    response = secure.post("/api/v1/auth/login", json={"subject": subject, "password": PASSWORD})
    assert response.status_code == 200, response.text
    return response.json()


# --- the switch -------------------------------------------------------------------


def test_off_mode_keeps_the_declared_name_as_the_identity(secure: TestClient, seeded: dict) -> None:
    """Nothing in M4 may start refusing an MVP request while the switch is off."""
    assert secure.get(f"/api/v1/projects/{seeded['id']}", headers=OPERATOR).status_code == 200
    who = secure.get("/api/v1/auth/session", headers=OPERATOR).json()
    assert who["authMode"] == "off" and who["authenticated"] is False


def test_users_are_listable_in_off_mode_so_the_screen_is_not_a_404(secure: TestClient) -> None:
    """The endpoints exist in every mode; only the policy behind them changes."""
    assert secure.get("/api/v1/users", headers=OPERATOR).status_code == 200


def test_off_mode_refuses_the_m4_writes_while_the_reads_stay_open(
    secure: TestClient, seeded: dict
) -> None:
    """CP-UI-023 / CP-AC-32: adding a project member in MVP answers 409 FEATURE_NOT_ENABLED.

    The ``enforced`` fixture sets ``auth_mode=local`` — which is M4 delivered — so the 1.2.0
    write tests pass untouched; this pins the other half: off mode refuses the write and the
    read side of the same table still answers, because reads decide nothing (CP-IAM-001).
    """
    pid = seeded["id"]

    refused = secure.put(f"/api/v1/projects/{pid}/members", json={"members": []}, headers=OPERATOR)
    assert refused.status_code == 409, refused.text
    assert refused.json()["errorCode"] == "FEATURE_NOT_ENABLED"
    assert refused.json()["details"] == {"feature": "member.manage", "milestone": "M4"}

    assert secure.get(f"/api/v1/projects/{pid}/members", headers=OPERATOR).status_code == 200


def test_a_request_without_a_session_is_refused_once_permissions_are_enforced(
    secure: TestClient, enforced: Settings, seeded: dict
) -> None:
    anonymous = secure.get(f"/api/v1/projects/{seeded['id']}")
    assert anonymous.status_code == 401
    assert anonymous.json()["errorCode"] == "UNAUTHENTICATED"


def test_a_signed_in_request_carries_the_roles_it_was_granted(
    secure: TestClient, enforced: Settings, seeded: dict, harness: Any
) -> None:
    _create("architect@example.org", role=SOLUTION_ARCHITECT, project_id=seeded["id"])
    session_view = _sign_in(secure, "architect@example.org")
    assert session_view["authenticated"] is True
    # the login answer carries the organisation-wide roles; the project-scoped grant
    # shows up when the same person acts inside that project
    assert session_view["authMode"] == "local"
    assert session_view["csrfToken"], "the CSRF value is handed out exactly once, to the owner"

    cookie = secure.cookies.get(enforced.cookie_name)
    assert cookie, "login set a session cookie"
    readable = secure.get(f"/api/v1/projects/{seeded['id']}", headers={"Cookie": f"{enforced.cookie_name}={cookie}"})
    assert readable.status_code == 200, readable.text


def test_creating_a_project_grants_the_creator_membership(
    secure: TestClient, enforced: Settings, seeded: dict
) -> None:
    """FR-SEC-004 isolates projects by membership, so ``project.create`` has to grant it.

    Without this row the author of a brand-new project is locked out of it by the very rule that
    protects everybody else's: ``project.create`` is a platform action, while every action inside
    the project is decided by membership.  Isolation must still hold for the projects they did not
    create, or the grant would be a skeleton key.
    """
    created = _create("solo@example.org", role=SOLUTION_ARCHITECT)
    session_view = _sign_in(secure, "solo@example.org")
    headers = {
        "Cookie": f"{enforced.cookie_name}={secure.cookies.get(enforced.cookie_name)}",
        "X-CSRF-Token": session_view["csrfToken"],
    }

    made = secure.post(
        "/api/v1/projects",
        json={
            "name": "solo project",
            "ownerName": "solo",
            "targetEnvironments": [
                {"environmentKey": "dev", "promotionOrder": 100, "isDesignReference": True},
                {"environmentKey": "prod", "promotionOrder": 200, "isDesignReference": False},
            ],
        },
        headers=headers,
    )
    assert made.status_code == 201, made.text
    project_id = made.json()["id"]

    assert secure.get(f"/api/v1/projects/{project_id}", headers=headers).status_code == 200
    members = secure.get(f"/api/v1/projects/{project_id}/members", headers=headers)
    assert members.status_code == 200, members.text
    rows = members.json()["items"]
    assert [row["userId"] for row in rows] == [created["id"]]
    assert rows[0]["subject"] == "solo@example.org"

    refused = secure.get(f"/api/v1/projects/{seeded['id']}", headers=headers)
    assert refused.status_code == 403, refused.text


def test_a_cross_project_listing_shows_only_what_membership_covers(
    secure: TestClient, enforced: Settings, seeded: dict
) -> None:
    """FR-SEC-004: the two cross-project listings used to answer the whole organization to anyone.

    Neither route carries a project in its path, so all it could ask was the organization-scope
    question — and it then returned every project whatever that question answered.  Isolation on
    these two pages rested on the coincidence that no non-admin role is ever granted at organization
    scope.  The boundary is now in the query: someone whose only grant is a project-scoped one lists
    the projects they belong to, and `total` counts what they may see.

    What still sees everything is a *platform-scope* grant of ``project.view``.  That is the
    administration surface — the organization deliberately holds that person org-wide — and this
    test pins both sides so the two are never confused again.
    """
    _create("architect2@example.org", role=SOLUTION_ARCHITECT)
    architect = _sign_in(secure, "architect2@example.org")
    headers = {
        "Cookie": f"{enforced.cookie_name}={secure.cookies.get(enforced.cookie_name)}",
        "X-CSRF-Token": architect["csrfToken"],
    }
    made = secure.post(
        "/api/v1/projects",
        json={
            "name": "the architect's own",
            "ownerName": "architect2",
            "targetEnvironments": [
                {"environmentKey": "dev", "promotionOrder": 100, "isDesignReference": True},
                {"environmentKey": "prod", "promotionOrder": 200, "isDesignReference": False},
            ],
        },
        headers=headers,
    )
    assert made.status_code == 201, made.text
    other = made.json()["id"]
    whole = secure.get("/api/v1/projects", headers=headers)
    assert whole.status_code == 200, whole.text
    assert {seeded["id"], other} <= {item["id"] for item in whole.json()["items"]}, (
        "a platform-scope grant is the administration view and still lists everything"
    )
    secure.post("/api/v1/auth/logout", headers={enforced.csrf_header: architect["csrfToken"]}, json={})

    _create("member@example.org", role=SOLUTION_ARCHITECT, project_id=seeded["id"])
    view = _sign_in(secure, "member@example.org")
    member_headers = {
        "Cookie": f"{enforced.cookie_name}={secure.cookies.get(enforced.cookie_name)}",
        "X-CSRF-Token": view["csrfToken"],
    }

    listed = secure.get("/api/v1/projects", headers=member_headers)
    assert listed.status_code == 200, listed.text
    assert [item["id"] for item in listed.json()["items"]] == [seeded["id"]]
    assert listed.json()["total"] == 1, "`total` must not count projects the caller may not see"

    runs = secure.get("/api/v1/deployment-runs", headers=member_headers)
    assert runs.status_code == 200, runs.text
    assert all(item["projectId"] == seeded["id"] for item in runs.json()["items"]), (
        "a Run belongs to a project through its workflow, so it follows the same boundary"
    )


def test_the_creator_can_work_in_the_project_they_created(
    secure: TestClient, enforced: Settings, seeded: dict
) -> None:
    """CP-PRJ-001: the creator has to be able to *use* the project, not only make it.

    Permissions come from the role on the membership row, not from the row's existence, and the
    account a fresh deployment hands out holds only PLATFORM_ADMINISTRATOR — which grants no
    project-scoped action.  Recording the creator with no role therefore changed nothing: the
    requirements document uploaded fine and its parse answered ``403 input.parse``, which is the
    first step of the design line.  This is how seeding the live deployment stopped.
    """
    from factory_control.identity.roles import PLATFORM_ADMINISTRATOR, SOLUTION_ARCHITECT

    creator = _create("root@example.org", role=PLATFORM_ADMINISTRATOR)
    view = _sign_in(secure, "root@example.org")
    headers = {
        "Cookie": f"{enforced.cookie_name}={secure.cookies.get(enforced.cookie_name)}",
        "X-CSRF-Token": view["csrfToken"],
    }

    made = secure.post(
        "/api/v1/projects",
        json={
            "name": "root project",
            "ownerName": "root",
            "targetEnvironments": [
                {"environmentKey": "dev", "promotionOrder": 100, "isDesignReference": True},
                {"environmentKey": "prod", "promotionOrder": 200, "isDesignReference": False},
            ],
        },
        headers=headers,
    )
    assert made.status_code == 201, made.text
    project_id, workflow_id = made.json()["id"], made.json()["workflowId"]

    rows = secure.get(f"/api/v1/projects/{project_id}/members", headers=headers).json()["items"]
    assert [row["role"] for row in rows] == [SOLUTION_ARCHITECT], rows

    allowed = {
        entry["action"]
        for entry in secure.get(
            f"/api/v1/workflows/{workflow_id}/allowed-actions", headers=headers
        ).json()["allowed"]
    }
    assert "input.parse" in allowed, sorted(allowed)
    assert "patch.submit" in allowed, sorted(allowed)
    # Membership is not deployment authority: the creator must still not be able to approve the
    # Gate that lets a COMMIT through (AC-GATE-04 keeps that with the approver roles).
    assert "gate.approve.DEPLOY_GATE.TEST" not in allowed, sorted(allowed)
    assert creator["subject"] == "root@example.org"


def test_a_project_scoped_role_grant_reaches_the_permission_check(
    harness: Any, seeded: dict
) -> None:
    """FR-SEC-002: granting a role *in a project* has to land, not just be recorded.

    ``directory.roles_of`` reads a project's role off the **membership row** and never off a
    ``user_roles`` row carrying a project_id, so a grant that wrote only the role row appeared
    correct in the console's user list while the policy still refused everything.  Every earlier
    test of this path called ``add_member(role=...)`` alongside it — the half that does work — so
    the hole survived the whole suite and only surfaced when the live deployment's approver was
    granted through the REST route.
    """
    from sqlalchemy import select

    from factory_control.foundation import unit_of_work
    from factory_control.identity import service
    from factory_control.identity.roles import DEPLOYMENT_APPROVER

    created = _create("deploy-gate@example.org")
    with unit_of_work() as session:
        service.assign_role(
            session, "provisioner", created["id"], DEPLOYMENT_APPROVER, project_id=seeded["id"]
        )

    with unit_of_work() as session:
        from factory_control.models import ProjectMember

        member = session.scalar(
            select(ProjectMember).where(
                ProjectMember.project_id == seeded["id"], ProjectMember.user_id == created["id"]
            )
        )
        assert member is not None and member.role == DEPLOYMENT_APPROVER, (
            "the grant wrote a role row the policy never reads"
        )

    assert _POLICY.is_allowed(
        operator="deploy-gate@example.org",
        project_id=seeded["id"],
        action=iam.gate_approve_action("DEPLOY_GATE", "TEST"),
    )
    # and it is the grant, not the account: DEPLOYMENT_APPROVER does not raise Runs
    assert not _POLICY.is_allowed(
        operator="deploy-gate@example.org", project_id=seeded["id"], action=iam.DEPLOYMENT_CREATE
    )


def test_a_wrong_password_and_an_unknown_account_are_indistinguishable(
    secure: TestClient, enforced: Settings, harness: Any
) -> None:
    _create("someone@example.org")
    wrong = secure.post("/api/v1/auth/login", json={"subject": "someone@example.org", "password": "not-it-at-all"})
    absent = secure.post("/api/v1/auth/login", json={"subject": "nobody@example.org", "password": "not-it-at-all"})
    assert wrong.status_code == absent.status_code == 401
    assert wrong.json()["errorCode"] == absent.json()["errorCode"] == "UNAUTHENTICATED"


def test_logout_ends_the_session_it_issued(
    secure: TestClient, enforced: Settings, seeded: dict, harness: Any
) -> None:
    _create("leaver@example.org", role=SOLUTION_ARCHITECT, project_id=seeded["id"])
    signed_in = _sign_in(secure, "leaver@example.org")
    cookie = secure.cookies.get(enforced.cookie_name)
    assert secure.get("/api/v1/auth/session").status_code == 200

    assert secure.post("/api/v1/auth/logout", headers={enforced.csrf_header: signed_in["csrfToken"]}, json={}).status_code == 200
    after = secure.get(
        f"/api/v1/projects/{seeded['id']}", headers={"Cookie": f"{enforced.cookie_name}={cookie}"}
    )
    assert after.status_code == 401, "a revoked token must stop working at once"


def test_disabling_an_account_ends_its_live_sessions(
    secure: TestClient, enforced: Settings, seeded: dict, harness: Any
) -> None:
    account = _create("toblock@example.org", role=SOLUTION_ARCHITECT, project_id=seeded["id"])
    _sign_in(secure, "toblock@example.org")
    cookie = secure.cookies.get(enforced.cookie_name)

    with unit_of_work() as session:
        service.update_user(session, "admin", account["id"], status="DISABLED")

    refused = secure.get(f"/api/v1/projects/{seeded['id']}", headers={"Cookie": f"{enforced.cookie_name}={cookie}"})
    assert refused.status_code == 401


def test_a_cookie_authenticated_change_needs_the_csrf_value(
    secure: TestClient, enforced: Settings, seeded: dict, harness: Any
) -> None:
    """FR-SEC-005: the cookie is sent by the browser on its own, so it is not consent."""
    _create("csrf@example.org", role=SOLUTION_ARCHITECT, project_id=seeded["id"])
    signed_in = _sign_in(secure, "csrf@example.org")
    cookie = secure.cookies.get(enforced.cookie_name)
    assert cookie

    refused = secure.post(
        f"/api/v1/projects/{seeded['id']}/archive",
        headers={"Cookie": f"{enforced.cookie_name}={cookie}"},
        json={"reason": "test"},
    )
    assert refused.status_code == 403, refused.text
    assert refused.json()["errorCode"] == "CSRF_FAILED"

    allowed = secure.post(
        f"/api/v1/projects/{seeded['id']}/archive",
        headers={"Cookie": f"{enforced.cookie_name}={cookie}", enforced.csrf_header: signed_in["csrfToken"]},
        json={"reason": "test"},
    )
    assert allowed.json()["errorCode"] != "CSRF_FAILED", allowed.text


def test_the_session_read_answers_before_there_is_a_session(
    secure: TestClient, enforced: Settings, seeded: dict
) -> None:
    """``authMode`` is only useful if a signed-out caller can read it (UI-AUTH-001).

    The route exists to let S5 tell "this deployment has no accounts" from "you must sign in"
    without inferring it from an error code — which needs the call to be reachable anonymously.  If
    the guard refuses it, the only signal left to a fresh page is a 401 with no regime attached.
    """
    anonymous = secure.get("/api/v1/auth/session")
    assert anonymous.status_code == 200, anonymous.text
    who = anonymous.json()
    assert who["authenticated"] is False
    assert who["authMode"] == "local"


def test_a_reloaded_page_still_knows_how_to_write(
    secure: TestClient, enforced: Settings, seeded: dict
) -> None:
    """FR-SEC-005: the CSRF value has to survive a reload, not only a sign-in.

    ``issue`` gives the clear value once and stores only its digest, so a page that reloads keeps
    a live session cookie and has nothing to name in ``X-CSRF-Token`` — every write would answer
    403 until the person signed in again.  The value is therefore mirrored into a readable
    same-origin cookie beside the unreadable session cookie, and this test reads *only* the
    cookies, never the sign-in response, because that is what a reload leaves.
    """
    _create("reload@example.org", role=SOLUTION_ARCHITECT, project_id=seeded["id"])
    _sign_in(secure, "reload@example.org")
    cookie = secure.cookies.get(enforced.cookie_name)
    csrf = secure.cookies.get(enforced.csrf_cookie_name)
    assert cookie, "sign-in set no session cookie"
    assert csrf, "sign-in mirrored no readable CSRF value"

    written = secure.post(
        f"/api/v1/projects/{seeded['id']}/archive",
        headers={
            "Cookie": f"{enforced.cookie_name}={cookie}; {enforced.csrf_cookie_name}={csrf}",
            enforced.csrf_header: csrf,
        },
        json={"reason": "after a reload"},
    )
    assert written.json().get("errorCode") != "CSRF_FAILED", written.text


def test_the_session_read_carries_the_clocks_a_reload_cannot_remember(
    secure: TestClient, enforced: Settings, seeded: dict
) -> None:
    """``expiresAt`` and ``mustChangePassword`` come from the session read, not from memory."""
    _create("clocks@example.org", role=SOLUTION_ARCHITECT, project_id=seeded["id"])
    signed_in = _sign_in(secure, "clocks@example.org")
    who = secure.get("/api/v1/auth/session").json()
    assert who["authenticated"] is True
    assert who["expiresAt"], sorted(who)
    assert who["mustChangePassword"] == signed_in["mustChangePassword"]
    # every read reports a deadline still ahead of now: the middleware slides the idle clock, so
    # the value moves — what must not happen is a read that leaves the page unable to write
    again = secure.get("/api/v1/auth/session").json()
    assert again["expiresAt"], again
    # the session read hands out no CSRF value of its own; the readable cookie carries it, so
    # there is exactly one source for the header rather than two that can disagree
    assert "csrfToken" not in again, sorted(again)


def test_the_platform_list_reports_the_rights_its_own_screens_need(
    secure: TestClient, enforced: Settings, harness: Any
) -> None:
    """FR-UI-009: an Administration page has no workflow to ask about.

    The platform list is therefore the only source its controls may take availability from — which
    means it has to answer with the caller's actual right.  Offering `user.manage` to everyone would
    let the screen draw a button the guard is going to refuse, and a blanket refusal would hide a
    capability the caller has.
    """
    from factory_control.identity.roles import BUSINESS_ANALYST, PLATFORM_ADMINISTRATOR

    _create("plain@example.org", role=BUSINESS_ANALYST)
    plain = _sign_in(secure, "plain@example.org")
    view = secure.get("/api/v1/allowed-actions").json()
    allowed = {item["action"] for item in view["allowed"]}
    unavailable = {item["action"]: item for item in view["unavailable"]}
    assert "user.manage" not in allowed, sorted(allowed)
    assert "user.manage" in unavailable, sorted(unavailable)
    assert unavailable["user.manage"]["reasonCode"] == "PERMISSION_DENIED"

    # ending the first session before the second sign-in: a cookie-authenticated POST has to name
    # its CSRF value, and login is a POST (FR-SEC-005 does not distinguish the two)
    secure.post("/api/v1/auth/logout", headers={enforced.csrf_header: plain["csrfToken"]}, json={})
    _create("admin@example.org", role=PLATFORM_ADMINISTRATOR)
    admin = _sign_in(secure, "admin@example.org")
    assert admin["authenticated"] is True
    as_admin = secure.get("/api/v1/allowed-actions").json()
    assert "user.manage" in {item["action"] for item in as_admin["allowed"]}, as_admin
    del harness


# --- roles and membership ---------------------------------------------------------

def test_a_member_without_the_role_is_refused_and_the_payload_agrees(
    secure: TestClient, enforced: Settings, seeded: dict, harness: Any
) -> None:
    """403 and an unavailable control come from one policy (S5IF-G10, CP-UI-019)."""
    _create("runner@example.org", role="BUSINESS_ANALYST", project_id=seeded["id"])
    _sign_in(secure, "runner@example.org")
    cookie = secure.cookies.get(enforced.cookie_name)
    headers = {"Cookie": f"{enforced.cookie_name}={cookie}"}

    assert secure.get(f"/api/v1/projects/{seeded['id']}", headers=headers).status_code == 200
    actions = secure.get(f"/api/v1/workflows/{seeded['workflowId']}/allowed-actions", headers=headers).json()
    refused_by_role = {
        entry["action"] for entry in actions["unavailable"] if entry.get("reasonCode") == "PERMISSION_DENIED"
    }
    assert "stage.run" in refused_by_role, sorted(refused_by_role)
    assert "input.upload" not in refused_by_role, "an analyst may upload: it is their job"


def test_the_delete_path_stops_at_the_role_that_cannot_export(
    secure: TestClient, enforced: Settings, seeded: dict, harness: Any
) -> None:
    """CP-PRJ-007 makes the export a precondition of the delete, so both halves have to be reachable.

    Granting one and not the other is invisible while ``auth=off`` — the state machine answers
    409 `requires: PROJECT_EXPORT` and the export answers 403 for *every* identity in the system,
    which is how `project.export` came to be held by nobody. The payload is checked too: an
    enabled button that 403s is the failure S5IF-G10 exists to prevent.
    """
    from factory_control.identity.roles import PLATFORM_ADMINISTRATOR

    _create("ops@example.org", role=PLATFORM_ADMINISTRATOR, project_id=seeded["id"])
    as_ops = _sign_in(secure, "ops@example.org")
    cookie = secure.cookies.get(enforced.cookie_name)
    headers = {"Cookie": f"{enforced.cookie_name}={cookie}"}

    actions = secure.get(f"/api/v1/workflows/{seeded['workflowId']}/allowed-actions", headers=headers).json()
    assert "project.export" in {e["action"] for e in actions["allowed"]}, actions
    assert secure.get(f"/api/v1/projects/{seeded['id']}/export", headers=headers).status_code == 202

    secure.post("/api/v1/auth/logout", headers={enforced.csrf_header: as_ops["csrfToken"]}, json={})
    _create("ba@example.org", role="BUSINESS_ANALYST", project_id=seeded["id"])
    _sign_in(secure, "ba@example.org")
    analyst = {"Cookie": f"{enforced.cookie_name}={secure.cookies.get(enforced.cookie_name)}"}
    refused = secure.get(f"/api/v1/projects/{seeded['id']}/export", headers=analyst)
    assert refused.status_code == 403, refused.text
    payload = secure.get(f"/api/v1/workflows/{seeded['workflowId']}/allowed-actions", headers=analyst).json()
    denied = {e["action"] for e in payload["unavailable"] if e.get("reasonCode") == "PERMISSION_DENIED"}
    assert "project.export" in denied, sorted(denied)


def test_a_non_member_may_not_see_another_project(
    secure: TestClient, enforced: Settings, seeded: dict, harness: Any
) -> None:
    """FR-SEC-004: in a single-organisation deployment, membership is the isolation."""
    _create("outsider@example.org", role=SOLUTION_ARCHITECT)  # organisation-wide, no membership
    _sign_in(secure, "outsider@example.org")
    cookie = secure.cookies.get(enforced.cookie_name)
    refused = secure.get(f"/api/v1/projects/{seeded['id']}", headers={"Cookie": f"{enforced.cookie_name}={cookie}"})
    assert refused.status_code == 403
    assert refused.json()["errorCode"] == "PERMISSION_DENIED"


# --- the Gate rules that needed an identity ---------------------------------------


def _production_deploy_gate(harness: Any, seeded: dict, *, run_id: str, executor: str | None) -> str:
    from factory_control.domain.enums import GateType, RunStatus
    from factory_control.models import DeploymentRun, Stage, Workflow
    from factory_control.services.gates import create_gate

    harness.execution.add_environment("prod-sod", environment_class="PRODUCTION")
    harness.execution.add_environment("dev-sod", environment_class="TEST")
    from factory_control.models import DeploymentRun as _Sib
    from factory_control.models import Workflow as _Wf

    with harness.runtime.transaction(operator="system") as pre:
        workflow = pre.session.get(_Wf, seeded["workflowId"])
        pre.session.add(
            _Sib(
                id=f"run-verified-{run_id}",
                workflow_id=workflow.id,
                environment_key="dev-sod",
                blueprint_version="1.0.0",
                status=RunStatus.VERIFIED.value,
            )
        )
    with harness.runtime.transaction(operator=executor or "system") as ctx:
        workflow = ctx.session.get(Workflow, seeded["workflowId"])
        stage = ctx.session.query(Stage).filter_by(workflow_id=workflow.id).first()
        run = DeploymentRun(
            id=run_id,
            workflow_id=workflow.id,
            environment_key="prod-sod",
            blueprint_version="1.0.0",
            status=RunStatus.IN_PROGRESS.value,
            validate_operator=executor,
        )
        ctx.session.add(run)
        ctx.session.flush()
        gate = create_gate(
            ctx,
            gate_type=GateType.DEPLOY_GATE,
            stage=stage,
            bound_refs=[],
            run=run,
            required_approvals=1,
        )
        gate.environment_key = "prod-sod"
        return str(gate.id)


def test_a_deploy_gate_is_approvable_by_the_role_the_table_grants(
    secure: TestClient, enforced: Settings, harness: Any, seeded: dict
) -> None:
    """CP-GATE-012: the coarse hook has to name an action a role can actually hold.

    The route asked for ``gate.approve.DEPLOY_GATE`` while the class-bearing name is only formed
    further down, in the transaction.  A narrower grant never covers a coarser ask
    (``policy._prefix_granted`` walks the *asked* name's prefixes), so that first hook refused
    every DEPLOY_GATE a real approver could open — while the same account's PACKAGE_GATE went
    through.  That is what the live deployment showed: 403 with
    ``{action: "gate.approve.DEPLOY_GATE", projectId: "prj-…"}``.
    """
    approver = "signs@example.org"
    _create(approver, role=DEPLOYMENT_APPROVER, project_id=seeded["id"])
    gate_id = _production_deploy_gate(harness, seeded, run_id="run-gate-hook", executor=None)

    view = _sign_in(secure, approver)
    headers = {
        "Cookie": f"{enforced.cookie_name}={secure.cookies.get(enforced.cookie_name)}",
        "X-CSRF-Token": view["csrfToken"],
    }
    approved = secure.post(
        f"/api/v1/gates/{gate_id}/approve",
        headers=headers,
        json={
            "gateType": "DEPLOY_GATE",
            "seenBoundHashes": [],
            "confirmations": [{"id": "OPERATIONAL_BACKUP_CONFIRMED", "confirmed": True}],
        },
    )
    # Authorization is what this test is about, and it is decided before anything else the
    # approval does.  The fixture binds the Gate to whichever stage the harness happens to
    # return first, and that stage is LOCKED here, so the state machine refuses *after* the
    # permission check passed — a 409 is therefore proof of the fix, and a 403 would be the bug.
    assert approved.status_code != 403, approved.text
    body = approved.json()
    assert body.get("errorCode") == "INVALID_STATE", body
    assert "gate.approve" not in str(body), body


def test_ac_gate_04_the_executor_may_not_approve_their_own_gate(
    harness: Any, enforced: Settings, seeded: dict
) -> None:
    """AC-GATE-04, previously skipped for want of an identity: the case is now decidable."""
    from factory_control.errors import InvalidStateError
    from factory_control.services import gates as GATES

    executor = "implementer@example.org"
    _create(executor, role=DEPLOYMENT_APPROVER, project_id=seeded["id"])
    gate_id = _production_deploy_gate(harness, seeded, run_id="run-sod-1", executor=executor)

    with pytest.raises(InvalidStateError) as refused:
        GATES.approve_gate(
            harness.runtime,
            gate_id,
            operator=executor,
            seen_bound_hashes=[],
            warning_dispositions=None,
            confirmations=[{"id": "OPERATIONAL_BACKUP_CONFIRMED", "confirmed": True}],
            comment=None,
        )
    assert refused.value.details.get("reason") == "EXECUTOR_MAY_NOT_APPROVE"


def test_a_second_approval_by_the_same_person_does_not_count(
    harness: Any, enforced: Settings, seeded: dict
) -> None:
    """CP-GATE-012: the required count is a count of people, not of clicks."""
    from factory_control.errors import InvalidStateError
    from factory_control.services import gates as GATES

    approver = "twice@example.org"
    _create(approver, role=DEPLOYMENT_APPROVER, project_id=seeded["id"])
    gate_id = _production_deploy_gate(harness, seeded, run_id="run-twice-1", executor="someone-else")
    from factory_control.foundation import unit_of_work as uow

    with uow() as session:
        from factory_control.models import Gate

        session.get(Gate, gate_id).required_approvals = 2

    def _approve() -> Any:
        return GATES.approve_gate(
            harness.runtime,
            gate_id,
            operator=approver,
            seen_bound_hashes=[],
            warning_dispositions=None,
            confirmations=[{"id": "OPERATIONAL_BACKUP_CONFIRMED", "confirmed": True}],
            comment=None,
        )

    assert _approve()["status"] == "WAITING", "one of two"
    with pytest.raises(InvalidStateError) as again:
        _approve()
    assert again.value.details.get("reason") == "ALREADY_APPROVED"


def test_a_person_without_the_production_role_cannot_approve_a_production_gate(
    enforced: Settings, seeded: dict
) -> None:
    _create("staging-only@example.org", role=DEPLOYMENT_APPROVER, project_id=seeded["id"])
    with pytest.raises(Exception) as refused:
        iam.authorize(
            "nobody-here@example.org", seeded["id"], iam.gate_approve_action("DEPLOY_GATE", "PRODUCTION")
        )
    assert getattr(refused.value, "error_code", "") == "PERMISSION_DENIED"
