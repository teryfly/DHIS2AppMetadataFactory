"""Unit branches this round added: the feature gate, the download verdict, the error shapes,
the transitional provider wiring (CP-UI-020~023, SRS 8.3, 附录 B).

The wire behaviour lives in ``tests/api/test_cp_ui_020_023.py``; what is exercised here is
the pure-function half, so a regression names the rule it broke rather than a request.
"""

from __future__ import annotations

import pytest

from factory_control import iam
from factory_control.domain.actions import (
    FEATURE_NOT_ENABLED,
    M4_GATED_ACTIONS,
    PACKAGE_NOT_GENERATED,
    ActionContext,
    _apply_feature_gate,
    _ok,
    forwarding_actions,
)
from factory_control.errors import (
    ERROR_HTTP_STATUS,
    S1_ERROR_RETRYABLE,
    FeatureNotEnabledError,
    NotFoundControlError,
    http_status_for,
)

pytestmark = pytest.mark.unit


# --- CP-UI-023: the gate itself ------------------------------------------------


def test_the_gate_moves_only_the_m4_actions_and_says_which_milestone() -> None:
    actions = [
        _ok(iam.MEMBER_MANAGE),
        _ok(iam.RESTRICTED_GRANT_MANAGE),
        _ok(iam.PRODUCTION_EXEMPTION_MANAGE),
        _ok("project.edit"),
    ]

    gated = {action.name: action for action in _apply_feature_gate(actions, ActionContext())}

    for feature in M4_GATED_ACTIONS:
        assert gated[feature].available is False, feature
        assert gated[feature].reason_code == FEATURE_NOT_ENABLED, feature
        assert gated[feature].details == {"feature": feature, "milestone": "M4"}
    assert gated["project.edit"].available is True, "the gate touches nothing else"


def test_the_gate_is_inert_once_the_feature_is_delivered() -> None:
    actions = [_ok(iam.MEMBER_MANAGE)]
    live = ActionContext(m4_enabled=True)

    assert _apply_feature_gate(actions, live) is actions, (
        "an enabled deployment must keep the state and role answers, not a second opinion"
    )


# --- 附录 B: frontend_package.download follows S1's own index ------------------


def test_the_download_action_is_off_until_a_package_is_indexed() -> None:
    before = {action.name: action for action in forwarding_actions(ActionContext())}
    refused = before[iam.FRONTEND_PACKAGE_DOWNLOAD]
    assert refused.available is False
    assert refused.reason_code == PACKAGE_NOT_GENERATED

    after = {
        action.name: action
        for action in forwarding_actions(ActionContext(frontend_package_available=True))
    }
    assert after[iam.FRONTEND_PACKAGE_DOWNLOAD].available is True


# --- SRS 8.3: the two new details shapes ---------------------------------------


def test_feature_not_enabled_carries_the_feature_and_the_milestone() -> None:
    error = FeatureNotEnabledError("not yet", feature="member.manage")
    assert error.error_code == "FEATURE_NOT_ENABLED"
    assert error.details == {"feature": "member.manage", "milestone": "M4"}
    assert ERROR_HTTP_STATUS[error.error_code] == 409
    assert http_status_for(error.error_code) == 409
    assert S1_ERROR_RETRYABLE[error.error_code] is False and error.retryable is False


def test_not_found_says_whether_the_thing_is_gone_or_merely_unproduced() -> None:
    plain = NotFoundControlError("gone")
    assert plain.details["reason"] == "NOT_EXIST", "the CP-UI-022 default for a dead reference"

    guided = NotFoundControlError(
        "not yet", reason="NOT_GENERATED", required_stage="BASELINE_COMPILE", run_id="run-7"
    )
    assert guided.details == {
        "reason": "NOT_GENERATED",
        "requiredStage": "BASELINE_COMPILE",
        "runId": "run-7",
    }

    site_wins = NotFoundControlError("kept", details={"reason": "NOT_GENERATED"})
    assert site_wins.details["reason"] == "NOT_GENERATED", "the raise site outranks the default"


# --- the shipped provider reads (CP-UI-020/021, CP-RT-005 conformance) --------


def test_the_shipped_s2_reads_match_the_port_declarations() -> None:
    """S2 exported ``artifact_reads`` — S1's port, mirrors and adapter wiring follow it.

    This is the "S2 发布后兼容性测试必须通过" gate for the three product reads: the
    provider must keep exporting them with the arity S1 forwards positionally, and S1's
    mirrors must dump cleanly from the provider models (field kinds: the mirror test).
    The SRS table spells the package argument ``package_artifact_id`` while S2 names it
    ``artifact_id`` — S1 follows the provider, hence the arity-only comparison here.
    """
    import importlib.util
    from inspect import signature

    if importlib.util.find_spec("factory_blueprint") is None:  # pragma: no cover - env guard
        pytest.skip("factory_blueprint is not installed")
    api = __import__("factory_blueprint.api", fromlist=["x"])
    for name, arity in (
        ("get_dependency_graph", 2),
        ("list_package_files", 1),
        ("read_package_file", 2),
    ):
        fn = getattr(api, name, None)
        assert callable(fn), f"factory_blueprint.api no longer exports {name}"
        params = list(signature(fn).parameters)
        assert len(params) == arity, f"{name}: expected {arity} parameters, found {params}"


def test_package_file_filename_follows_rule_three_with_the_path_extension() -> None:
    from factory_control.rest.download import package_file_filename

    assert package_file_filename("art-9", "api/openapi.yaml") == "FRONTEND_PACKAGE-art-9.yaml"
    assert package_file_filename("art-9", "README.md") == "FRONTEND_PACKAGE-art-9.md"
    assert package_file_filename("art-9", "LICENSE") == "FRONTEND_PACKAGE-art-9.bin"
