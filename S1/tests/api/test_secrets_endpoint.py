"""``/api/v1/secrets`` and the vendor catalogue (S5IF-SEC01…03, S5IF-GOV07).

The interesting assertions here are the negative ones.  A credential endpoint that works is easy;
what a deployment has to be able to trust is that the value it accepted is nowhere it can be read
back — not in a response, not in an audit row, not in a validation error that quotes the input, not
in the idempotency replay table — and that the feature is honestly switched off when the master key
is missing rather than quietly storing something weaker.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from factory_control.credentials import store as SECRET_STORE
from factory_control.credentials.backend import install_master_key_backend
from factory_control.foundation import resolve_secret, store_secret
from factory_control.services import secrets as SECRET
from factory_control.testing.harness import ControlHarness

KEY = "unit-test-master-key-at-least-32-chars-long"
OTHER_KEY = "a-completely-different-master-key-32-chars"
SENTINEL = "sk-live-Abc123-Zhang-Wei-token"


@pytest.fixture
def store(harness: ControlHarness) -> ControlHarness:
    """The harness with the credential store switched on, exactly as bootstrap would."""
    harness.runtime.settings.credential_master_key = KEY
    assert install_master_key_backend(harness.runtime.settings)
    return harness


@pytest.fixture(autouse=True)
def _forget_the_key(harness: ControlHarness) -> Iterator[None]:
    """The resolver is process-wide, so a key left registered by one test leaks into the next."""
    yield
    harness.runtime.settings.credential_master_key = ""


def _headers() -> dict[str, str]:
    # auth is off in the harness, so a mutation has to declare who made it (CP-API-002)
    return {"X-Operator": "ada", "Idempotency-Key": str(uuid4())}


def _environment(client: TestClient, key: str, ref: str) -> None:
    created = client.post(
        "/api/v1/environments",
        json={
            "environmentKey": key,
            "baseUrl": f"https://{key}.example.org",
            "versionConstraint": "2.41.x",
            "environmentClass": "TEST",
            "credentials": [
                {"scope": "discovery", "secretRef": ref},
                {"scope": "import", "secretRef": ref},
            ],
        },
        headers=_headers(),
    )
    assert created.status_code == 201, created.text


def test_the_store_is_switched_off_until_a_master_key_exists(harness: ControlHarness, client: TestClient) -> None:
    """503 naming the setting, because a silent no-op would look to an operator like a saved credential."""
    refused = client.put("/api/v1/secrets/dhis2-dev", json={"material": SENTINEL}, headers=_headers())
    assert refused.status_code == 503, refused.text
    body = refused.json()
    assert body["errorCode"] == "SECRET_UNAVAILABLE"
    assert body["details"]["setting"] == "FACTORY_CREDENTIAL_MASTER_KEY"
    assert SENTINEL not in refused.text
    assert client.get("/api/v1/secrets").status_code == 503

    section = client.get("/api/v1/setup").json()["credentials"]
    assert section["storeAvailable"] is False and section["masterKeyConfigured"] is False


def test_a_write_returns_a_reference_and_nothing_else(store: ControlHarness, client: TestClient) -> None:
    created = client.put(
        "/api/v1/secrets/dhis2-dev-discovery", json={"material": SENTINEL}, headers=_headers()
    )
    assert created.status_code == 200, created.text
    body = created.json()
    assert body["ref"] == "db://dhis2-dev-discovery"
    assert body["rotatedAt"] is None  # the first value ever held under this name
    assert set(body) == {
        "name", "ref", "algo", "createdAt", "updatedAt", "rotatedAt", "createdBy",
    }
    assert SENTINEL not in created.text
    # Nothing key-derived is in there either.  The row's own key fingerprint stays in the table: with
    # a fresh salt per encryption it identifies one blob rather than one master key, so it answers no
    # question an operator can ask while giving an offline guess something cheap to test against.
    assert body["algo"] == SECRET.ALGO

    listed = client.get("/api/v1/secrets").json()
    assert [item["name"] for item in listed["items"]] == ["dhis2-dev-discovery"]
    assert listed["total"] == 1
    assert SENTINEL not in json.dumps(listed)
    assert "ciphertext" not in json.dumps(listed)


def test_the_stored_value_resolves_back_through_the_secret_resolver(store: ControlHarness, client: TestClient) -> None:
    """What S4 and S3 actually call: a reference in, the same characters out."""
    client.put("/api/v1/secrets/round-trip", json={"material": SENTINEL}, headers=_headers())
    assert resolve_secret("db://round-trip").reveal() == SENTINEL
    # and the object that carries it still masks itself, because that is the only reason a Secret
    # can be passed through a logging path at all
    secret = resolve_secret("db://round-trip")
    assert SENTINEL not in repr(secret) and SENTINEL not in str(secret)


def test_a_ciphertext_that_was_not_produced_here_is_not_handed_out(store: ControlHarness) -> None:
    from factory_control.foundation import SecretUnavailableError

    with store.runtime.transaction(operator="ada") as ctx:
        SECRET_STORE.put(
            ctx.session,
            name="placeholder",
            ciphertext="v1$not$really$encryptable$ffff",
            algo="aes-256-gcm+hkdf-sha256",
            key_fingerprint="ffff",
            operator="ada",
        )
    with pytest.raises(SecretUnavailableError):
        resolve_secret("db://placeholder")


def test_writing_the_same_name_again_rotates_rather_than_duplicates(store: ControlHarness, client: TestClient) -> None:
    first = client.put("/api/v1/secrets/llm-key", json={"material": "one"}, headers=_headers()).json()
    second = client.put("/api/v1/secrets/llm-key", json={"material": "two"}, headers=_headers()).json()

    assert first["createdAt"] == second["createdAt"]
    assert second["rotatedAt"] is not None
    assert client.get("/api/v1/secrets").json()["total"] == 1, "a rotation must not leave two live rows"
    assert resolve_secret("db://llm-key").reveal() == "two"


@pytest.mark.parametrize("bad", ["Zhang Wei", "a", "UPPER", "with space", "x" * 70, "zh-CN"])
def test_a_name_that_could_leak_someone_is_refused(store: ControlHarness, client: TestClient, bad: str) -> None:
    """Names are listed by ``GET /secrets``, so a name is not a place to put a username."""
    refused = client.put(f"/api/v1/secrets/{bad}", json={"material": "v"}, headers=_headers())
    assert refused.status_code in (400, 422), (bad, refused.text)


def test_a_validation_failure_must_not_quote_the_value_it_rejected(store: ControlHarness, client: TestClient) -> None:
    """Pydantic puts the submitted input into its own rendering; the envelope must not repeat it.

    A credential sent as an object is the shape that would otherwise echo it: the type error names
    the field, and pydantic appends ``input_value={'secret': 'the-token'}`` to the same sentence.
    """
    response = client.put(
        "/api/v1/secrets/echo-check",
        json={"material": {"secret": SENTINEL}},
        headers=_headers(),
    )
    assert response.status_code == 422, response.text
    assert SENTINEL not in response.text, "the 422 body echoed the credential back"
    assert response.json()["details"]["fields"][0]["path"] == "material"


def test_an_empty_value_is_refused_naming_the_field_not_the_value(store: ControlHarness, client: TestClient) -> None:
    refused = client.put("/api/v1/secrets/empty", json={"material": "   "}, headers=_headers())
    assert refused.status_code == 422, refused.text
    assert refused.json()["details"]["field"] == "material"


def test_the_audit_row_records_the_reference_only(store: ControlHarness, client: TestClient) -> None:
    client.put("/api/v1/secrets/audited", json={"material": SENTINEL}, headers=_headers())
    rows = client.get("/api/v1/audit").json()["items"]
    written = [row for row in rows if row.get("eventType") == "cp.secret.written"]
    assert written, "a credential write nobody can be held to is a credential write with no owner"
    assert SENTINEL not in json.dumps(written)


def test_a_credential_still_referenced_cannot_be_deleted(store: ControlHarness, client: TestClient) -> None:
    client.put("/api/v1/secrets/dhis2-import", json={"material": "imp"}, headers=_headers())
    _environment(client, "clinic", "db://dhis2-import")

    refused = client.delete("/api/v1/secrets/dhis2-import", headers=_headers())
    assert refused.status_code == 409, refused.text
    assert refused.json()["errorCode"] == "SECRET_IN_USE"
    holders = refused.json()["details"]["referencedBy"]
    assert any("clinic" in holder for holder in holders), holders

    assert client.delete("/api/v1/secrets/never-written", headers=_headers()).status_code == 404


def test_moving_the_references_out_is_what_frees_the_credential(store: ControlHarness, client: TestClient) -> None:
    """CP-SEC-003's second half: un-point it, then it deletes.

    Measured live on a fakes deployment on 2026-10-04, where this refused forever: the environment
    view kept a ``secretRefs`` map that a credentials ``PATCH`` never refreshed, so the guard went on
    naming a reference that no row resolves with any more. S4 derives both from one list at read time
    (``factory_dhis2/environment/service.py:213``); the stand-in has to, or it teaches a flow that
    only works on a machine with no real subsystem wired in.
    """
    client.put("/api/v1/secrets/old", json={"material": "a"}, headers=_headers())
    client.put("/api/v1/secrets/new", json={"material": "b"}, headers=_headers())
    _environment(client, "clinic", "db://old")

    moved = client.patch(
        "/api/v1/environments/clinic",
        json={
            "credentials": [
                {"scope": "discovery", "secretRef": "db://new"},
                {"scope": "import", "secretRef": "db://new"},
            ]
        },
        headers=_headers(),
    )
    assert moved.status_code == 200, moved.text
    view = moved.json()
    # the two spellings of the same fact must say the same thing
    assert view["secretRefs"] == {"discovery": "db://new", "import": "db://new"}, view["secretRefs"]
    assert {c["secretRef"] for c in view["credentials"]} == {"db://new"}

    freed = client.delete("/api/v1/secrets/old", headers=_headers())
    assert freed.status_code == 200, freed.text
    assert client.get("/api/v1/setup").json()["credentials"]["named"] == 1


def test_setup_names_a_reference_that_no_longer_resolves(store: ControlHarness, client: TestClient) -> None:
    """The failure this section exists for: configured, saved, and dead before a stage ever runs."""
    client.put("/api/v1/secrets/dhis2-solo", json={"material": "tok"}, headers=_headers())
    _environment(client, "solo", "db://dhis2-solo")
    assert client.get("/api/v1/setup").json()["credentials"]["unresolvable"] == []

    # the deployment restores a backup, or someone rotates the master key
    store.runtime.settings.credential_master_key = OTHER_KEY
    install_master_key_backend(store.runtime.settings)

    section = client.get("/api/v1/setup").json()["credentials"]
    assert section["named"] == 1
    broken = section["unresolvable"]
    assert [entry["ref"] for entry in broken] == ["db://dhis2-solo"]
    assert broken[0]["reason"] == "keyMismatch"
    assert any("solo" in holder for holder in broken[0]["holders"])


def test_adding_the_store_does_not_take_away_the_writable_backend(store: ControlHarness) -> None:
    """``store_secret()`` without a scheme infers *the* writable backend; two of them break it.

    The credential backend is read-only on purpose, and this assertion is what keeps it that way:
    FD-SEC-004's one-time demo credentials still need an unambiguous write target.
    """
    ref = store_secret("demo-credential", "generated-once")
    assert ref.startswith("memory://")
    assert resolve_secret(ref).reveal() == "generated-once"


def test_secret_manage_is_answered_by_the_platform_list(store: ControlHarness, client: TestClient) -> None:
    actions = client.get("/api/v1/allowed-actions").json()
    named = {row["action"] for row in actions.get("allowed", [])} | {
        row["action"] for row in actions.get("unavailable", [])
    }
    assert "secret.manage" in named, sorted(named)


def test_an_environment_may_name_a_stored_credential(store: ControlHarness, client: TestClient) -> None:
    """``db://`` is an accepted reference scheme end to end (S5IF-G09's amendment)."""
    client.put("/api/v1/secrets/dhis2-pair", json={"material": "pat"}, headers=_headers())
    _environment(client, "paired", "db://dhis2-pair")
    row = next(i for i in client.get("/api/v1/environments").json()["items"] if i["environmentKey"] == "paired")
    assert {c["secretRef"] for c in row["credentials"]} == {"db://dhis2-pair"}


def test_a_local_model_server_can_be_registered_without_any_key(store: ControlHarness, client: TestClient) -> None:
    """An empty `apiKeySecretRef` means "this endpoint authenticates with nothing".

    S3 already models it that way — no reference, no secret, no `Authorization` header — so a
    `min_length` here was the only thing standing between an operator and their own Ollama, and the
    workaround was to invent an environment variable whose absence then surfaced as
    `LlmSecretMissing` at the first generation.
    """
    registered = client.post(
        "/api/v1/governance/providers",
        json={"name": "local-ollama", "providerClass": "LOCAL_ONLY", "endpoint": "http://localhost:11434/v1"},
        headers=_headers(),
    )
    assert registered.status_code in (200, 201), registered.text
    assert registered.json()["apiKeySecretRef"] == ""

    # a value that is not a reference is still refused: "optional" is not "anything goes"
    refused = client.post(
        "/api/v1/governance/providers",
        json={
            "name": "not-a-ref",
            "providerClass": "LOCAL_ONLY",
            "endpoint": "http://localhost:11434/v1",
            "apiKeySecretRef": "sk-live-abc",
        },
        headers=_headers(),
    )
    assert refused.status_code == 422, refused.text
    assert refused.json()["errorCode"] == "VALIDATION_ERROR"


def test_the_catalogue_offers_only_addresses_this_build_can_call(store: ControlHarness, client: TestClient) -> None:
    response = client.get("/api/v1/governance/provider-catalogue")
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["total"] == len(body["items"]) and body["total"] >= 8
    for entry in body["items"]:
        # one wire protocol exists, so every suggestion has to be speakable in it
        assert entry["wireProtocol"] == "openai_compat"
        assert entry["endpoint"].endswith(("/v1", "/v4")), entry
        assert entry["key"] and entry["displayName"] and entry["endpoint"]
        assert isinstance(entry["models"], list)
        if entry["local"]:
            # a local server usually authenticates with nothing, so suggesting env://OLLAMA_API_KEY
            # would be suggesting a reference that cannot resolve
            assert entry["keyRefSuggestion"] == "", entry
        else:
            assert entry["keyRefSuggestion"].startswith("env://"), entry
    endpoints = " ".join(entry["endpoint"] for entry in body["items"])
    # api.anthropic.com has no /chat/completions and needs x-api-key: listing it would be a lie
    assert "api.anthropic.com" not in endpoints
    assert "generativelanguage.googleapis.com" not in endpoints
    assert {"openai", "deepseek", "ollama"} <= {entry["key"] for entry in body["items"]}


def test_a_catalogue_entry_is_a_suggestion_not_a_verified_fact(store: ControlHarness, client: TestClient) -> None:
    """Nothing here has been dialled, so the console must not be able to read a green light out of it."""
    items = client.get("/api/v1/governance/provider-catalogue").json()["items"]
    assert all("status" not in entry and "reachable" not in entry for entry in items)
    assert any(entry["local"] for entry in items), "a local model server is a normal answer here"
