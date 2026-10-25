"""CP-NFR-09: the two language catalogues must hold exactly the same codes.

A code S1 can raise but one language lacks makes that language fall back to the
raiser's raw message, which for a provider-supplied error is English prose written
for a log, not for a user.  Nothing tested this before, so the drift was silent.
"""

from __future__ import annotations

from factory_control.errors import ERROR_HTTP_STATUS, PROVIDER_ERROR_HTTP_STATUS, S1_ERROR_RETRYABLE
from factory_control.i18n import _CATALOGS, message_for


def test_zh_and_en_hold_the_same_codes() -> None:
    zh, en = set(_CATALOGS["zh-CN"]), set(_CATALOGS["en"])
    assert zh == en, f"zh-only={sorted(zh - en)} en-only={sorted(en - zh)}"


def test_every_code_that_can_reach_the_envelope_is_translatable() -> None:
    raised = set(ERROR_HTTP_STATUS) | set(PROVIDER_ERROR_HTTP_STATUS) | set(S1_ERROR_RETRYABLE)
    missing = sorted(raised - set(_CATALOGS["en"]))
    assert not missing, f"codes with no localised message: {missing}"


def test_provider_status_table_covers_the_forwarded_families() -> None:
    """S5IF-G07.2: nothing a provider raises may fall through to a 500."""
    from factory_control.errors import http_status_for

    for code in ("DX_OUTSIDE_COMMIT_WINDOW", "AI_PROMPT_ACTIVATION_BLOCKED", "BP_UNSUPPORTED_SCHEMA_VERSION"):
        assert http_status_for(code) != 500, code
    assert http_status_for("SOMETHING_UNEXPECTED") == 500


def test_every_code_a_provider_registers_has_a_status() -> None:
    """The same rule, checked against the providers' own registries rather than a sample list.

    Twenty-five codes were unlisted this way — ``DX_ENVIRONMENT_INVALID`` among them, which is
    what S4 raises for *any* operation against a disabled environment. Each one answered as an
    S1 crash (CP-API-008 hides the reason), so an operator pressing a legitimate button got a
    500 and nothing to act on. Enumerating the registries is the only way this stays closed:
    the sample list above can pass while a whole family is missing.
    """
    from factory_control.errors import DEFAULT_HTTP_STATUS, http_status_for

    registered: set[str] = set()
    for module, table in (
        ("factory_blueprint.registries", "BP_ERROR_CODES"),
        ("factory_intelligence.registries", "AI_ERROR_CODES"),
        ("factory_dhis2.registries", "DX_ERROR_CODES"),
    ):
        try:
            registered |= set(getattr(__import__(module, fromlist=["x"]), table))
        except ImportError:
            continue  # not installed in this environment

    # A provider's own unclassified crash *is* an S1 crash: ``INTERNAL_ERROR`` is S4's base and
    # ``AI_ERROR`` S3's, and mapping either to a 4xx would promise the caller something the
    # provider did not actually say.
    own_crash = {"INTERNAL_ERROR", "AI_ERROR"}
    unhandled = sorted(
        code
        for code in registered
        if code not in own_crash and http_status_for(code) == DEFAULT_HTTP_STATUS
    )
    assert not unhandled, f"provider codes S1 would answer 500 for: {unhandled}"


def test_the_provider_tables_name_codes_the_providers_actually_register() -> None:
    """S5IF-G07.2: the catalogue S1 maintains must be the providers' catalogue, not a copy of guesses.

    Both directions are checked against the registries the providers own: a code S1
    lists that no provider raises would be dead weight S5 has been told to map, and an
    alias pointing at a code S1 does not define would fall through to 500 on the way
    out.  A subsystem that is not installed contributes nothing, which is why the
    lookup is guarded.
    """
    from factory_control.errors import (
        ERROR_HTTP_STATUS,
        PROVIDER_CODE_ALIASES,
        PROVIDER_ERROR_HTTP_STATUS,
    )

    registered: dict[str, set[str] | None] = {}
    for owner, (module, table) in {
        "S2": ("factory_blueprint.registries", "BP_ERROR_CODES"),
        "S3": ("factory_intelligence.registries", "AI_ERROR_CODES"),
        "S4": ("factory_dhis2.registries", "DX_ERROR_CODES"),
    }.items():
        try:
            registered[owner] = set(getattr(__import__(module, fromlist=["x"]), table))
        except ImportError:
            registered[owner] = None  # not installed yet: its codes cannot be checked

    # A code's owner is readable from its prefix, which is what makes the check
    # meaningful while S4 is still missing from this environment.
    prefixes = {"BP_": "S2", "UID_": "S2", "AI_": "S3", "DX_": "S4"}
    ours = set(PROVIDER_CODE_ALIASES) | set(PROVIDER_ERROR_HTTP_STATUS)
    invented: list[str] = []
    for code in sorted(ours):
        owner = next((found for prefix, found in prefixes.items() if code.startswith(prefix)), None)
        if owner is not None and registered[owner] is not None and code not in registered[owner]:
            invented.append(code)
    assert not invented, f"S1 tables a provider code that no provider raises: {invented}"

    unresolved = sorted(
        target
        for target in PROVIDER_CODE_ALIASES.values()
        if target not in ERROR_HTTP_STATUS and target not in PROVIDER_ERROR_HTTP_STATUS
    )
    assert not unresolved, f"aliases pointing at a code with no status: {unresolved}"

    self_aliases = sorted(code for code, target in PROVIDER_CODE_ALIASES.items() if code == target)
    assert not self_aliases, f"an alias rewrites a code to itself: {self_aliases}"


def test_aliasing_a_provider_code_keeps_the_original_in_details() -> None:
    from factory_control.errors import normalize_provider_error
    from factory_control.foundation import FactoryError

    error = FactoryError("nope", code="DX_ENVIRONMENT_IN_USE", details={"environmentKey": "prod"})
    normalised = normalize_provider_error(error)
    assert normalised.error_code == "INVALID_STATE"
    assert normalised.details["providerErrorCode"] == "DX_ENVIRONMENT_IN_USE"
    assert normalised.details["environmentKey"] == "prod"


def test_messages_stay_language_specific() -> None:
    assert message_for("PROVIDER_UNAVAILABLE", "zh-CN") != message_for("PROVIDER_UNAVAILABLE", "en")


def test_s1_registers_only_the_codes_it_owns() -> None:
    """FD-COM-001a (S0 V1.0.1): one owner per code, and the catalog enforces it at boot.

    ``bootstrap.initialize`` calls ``register_error_codes`` with this mapping on every
    start-up; when S1 claimed NOT_FOUND or SCHEMA_ERROR under its own name, the strict
    catalog raised ``RegistryConflictError`` and the API could not boot.  S1 still
    *answers* those codes — it raises them directly, which is what FD-COM-001a exists
    for — so only its own judgments plus the two codes S0 attributes to S1 are
    registered.  Re-running the registration is the boot path: it must not raise.
    """
    from factory_control.foundation import error_catalog, register_error_codes
    from factory_control.registrations import s1_error_codes

    claimed_codes = s1_error_codes()
    assert claimed_codes, "an empty registration would hide a filtering bug"
    # The catalog can only refuse a clash after the fact; this is the case where S1 must not
    # claim a code just because nobody had registered it yet (the S4 fakes never do).
    assert "ENVIRONMENT_LOCKED" not in claimed_codes, "S4 owns this code; S1 only answers with it"
    for code, (owner, _retryable) in claimed_codes.items():
        meta = error_catalog.get(code)
        assert meta is None or meta.owner_subsystem == owner, (
            f"{code} is owned by {meta.owner_subsystem if meta else '?'}, S1 must not re-register it"
        )
    register_error_codes(claimed_codes)  # idempotent; a conflict raises

    from factory_control.errors import S1_ERROR_RETRYABLE

    claimed = set(claimed_codes)
    for code in S1_ERROR_RETRYABLE:
        meta = error_catalog.get(code)
        if meta is not None and meta.owner_subsystem == "S1":
            assert code in claimed, f"S1 owns {code} but registers neither — Temporal would misretry"


def test_retryability_claims_match_the_catalog() -> None:
    """S1's table feeds Temporal-retry expectations; the catalog is the single truth.

    ``CONCURRENCY_ERROR`` (retryable per FD-COM-001a) and ``SECRET_UNAVAILABLE`` (not) were
    the two claims that had drifted; an envelope or a worker asking either table must get
    the same answer.
    """
    from factory_control.foundation import error_catalog

    mismatches = []
    for code, claim in S1_ERROR_RETRYABLE.items():
        meta = error_catalog.get(code)
        if meta is not None and meta.retryable != claim:
            mismatches.append((code, f"catalog={meta.retryable} S1={claim}"))
    assert not mismatches, f"retryability drifted from the catalog: {mismatches}"
