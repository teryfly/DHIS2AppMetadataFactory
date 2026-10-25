"""The second layer of "a 422 must not quote a credential back" (S5IF-SEC01, FR-SEC-008).

The API-level test in `tests/api/test_secrets_endpoint.py` proves the *response* is clean, but the
response is clean mostly because `error_body` replaces pydantic's text with the catalogue's own
sentence. That makes the response a proxy for the guarantee, not the guarantee: the raw rendering is
also what a log line, a support ticket, or a future code path with no catalogue entry would carry.

So this file tests the scrubber itself, against the rendering it exists to defuse — including the
shape that a "just mask the field name" implementation misses, which is a credential smuggled
inside a wrongly-typed body at a depth the first version of this code did not walk.
"""

from __future__ import annotations

from fastapi.exceptions import RequestValidationError

from factory_control.rest.app import _rejected_fields, _without_secret_input

SENTINEL = "sk-deep-nested-Do-Not-Echo-9e1c"


def _error(*, loc: tuple[str, ...], input_value: object) -> RequestValidationError:
    """One error exactly as pydantic v2 hands it over: the value under `input`, not under `ctx`."""
    return RequestValidationError(
        [{"type": "string_type", "loc": loc, "msg": "Input should be a valid string", "input": input_value}]
    )


def test_pydantic_really_does_render_the_value_we_are_promising_to_hide() -> None:
    """If this ever fails, the scrubber below has stopped defending anything real."""
    assert SENTINEL in str(_error(loc=("body", "material"), input_value=SENTINEL))


def test_a_plain_string_input_is_masked() -> None:
    error = _error(loc=("body", "material"), input_value=SENTINEL)
    assert SENTINEL not in _without_secret_input(str(error), error)


def test_an_account_password_is_masked_too() -> None:
    """``password`` joined the opaque list with ``material``.

    The account endpoints have the same exposure as the credential store: a ``password`` submitted
    as the wrong type arrives here with the typed value in the summary, and a 422 is the one body an
    operator copies out of devtools and pastes into a chat (FR-SEC-008).
    """
    error = _error(loc=("body", "password"), input_value=SENTINEL)
    assert SENTINEL in str(error)
    assert SENTINEL not in _without_secret_input(str(error), error)


def test_a_value_nested_two_levels_inside_a_wrongly_typed_body_is_masked() -> None:
    error = _error(loc=("body", "material"), input_value={"k": {"deep": SENTINEL}})
    assert SENTINEL in str(error)
    assert SENTINEL not in _without_secret_input(str(error), error)


def test_a_value_inside_a_list_inside_a_dict_is_masked() -> None:
    error = _error(loc=("body", "material"), input_value={"k": [{"tokens": [SENTINEL]}]})
    assert SENTINEL not in _without_secret_input(str(error), error)


def test_an_ordinary_field_keeps_its_useful_input_text() -> None:
    """The masking is scoped to credential fields; a normal form error still says what was sent."""
    error = _error(loc=("body", "name"), input_value="prod-new")
    assert "prod-new" in _without_secret_input(str(error), error)


def test_a_field_actually_named_path_is_not_erased_from_the_field_list() -> None:
    """`body`/`path` are location *kinds* and get dropped; a resource field called `path` does not."""
    error = RequestValidationError(
        [{"type": "string_type", "loc": ("body", "path"), "msg": "Input should be a valid string", "input": "x"}]
    )
    assert _rejected_fields(error) == [{"path": "path", "message": "Input should be a valid string"}]


def test_a_missing_catalogue_text_still_cannot_smuggle_a_value() -> None:
    """`message_for` falls back to the text it is handed, so the fallback must already be clean.

    This is the case the API-level test cannot see: the response is only clean because the
    catalogue had a sentence to put there.
    """
    error = _error(loc=("body", "material"), input_value=SENTINEL)
    assert SENTINEL not in _without_secret_input(str(error), error)
