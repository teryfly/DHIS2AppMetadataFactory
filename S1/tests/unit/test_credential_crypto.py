"""The credential cipher (CP-SEC-001).

These tests are about the four ways an encrypted column can be misleading rather than protected,
each of which would otherwise be discovered by an operator whose DHIS2 call failed for a reason
nothing on screen could explain:

* a row moved to another name still decrypts (no name binding),
* a wrong master key reads as corruption, so the operator chases a broken row that is fine,
* the same value encrypts identically each time, which leaks equality across rows,
* the plaintext survives into a ``repr`` somewhere on the way out.
"""

from __future__ import annotations

import pytest

from factory_control.credentials import crypto

MASTER = "master-key-value-long-enough-to-pass"
OTHER = "a-different-master-key-also-long-enough"


def test_a_value_comes_back_and_a_repetition_does_not_look_like_the_first() -> None:
    name = "dhis2-dev-discovery"
    encoded, fingerprint = crypto.encrypt("tok-ÄÖ-值", name=name, master_key=MASTER)
    assert crypto.decrypt(encoded, name=name, master_key=MASTER) == "tok-ÄÖ-值"
    assert encoded.startswith("v1$") and encoded.count("$") == 4
    assert fingerprint and fingerprint not in encoded.split("$")[3]

    again, _ = crypto.encrypt("tok-ÄÖ-值", name=name, master_key=MASTER)
    # a fresh salt and a fresh nonce each time: two rows holding the same token must not be
    # recognisable as holding the same token
    assert again != encoded


def test_a_row_moved_under_another_name_does_not_decrypt() -> None:
    """The name is additional authenticated data, so swapping ciphertexts between rows fails."""
    encoded, _ = crypto.encrypt("shared-token", name="env-a", master_key=MASTER)
    with pytest.raises(crypto.CredentialCorruptError):
        crypto.decrypt(encoded, name="env-b", master_key=MASTER)


def test_a_changed_master_key_is_named_as_such() -> None:
    encoded, _ = crypto.encrypt("value", name="n1", master_key=MASTER)
    with pytest.raises(crypto.CredentialKeyMismatchError) as seen:
        crypto.decrypt(encoded, name="n1", master_key=OTHER)
    # the message may name the reference — it is listed by GET /secrets anyway — but never a value
    assert "n1" in str(seen.value)
    assert "value" not in str(seen.value)


def test_a_tampered_ciphertext_is_refused_without_distinguishing_why() -> None:
    encoded, _ = crypto.encrypt("value", name="n1", master_key=MASTER)
    version, salt, nonce, sealed, keyfp = encoded.split("$")
    flipped = sealed[:-2] + ("QQ" if not sealed.endswith("QQ") else "AA")
    with pytest.raises(crypto.CredentialCorruptError):
        crypto.decrypt("$".join((version, salt, nonce, flipped, keyfp)), name="n1", master_key=MASTER)


@pytest.mark.parametrize("broken", ["", "nonsense", "v2$a$b$c$d", "v1$$$x", "$$$$", "v1$a$b$c"])
def test_a_blob_this_build_cannot_read_is_an_answer_not_a_crash(broken: str) -> None:
    with pytest.raises(crypto.CredentialCorruptError):
        crypto.decrypt(broken, name="n1", master_key=MASTER)


def test_nothing_that_would_protect_nothing_is_stored() -> None:
    with pytest.raises(ValueError):
        crypto.encrypt("", name="n1", master_key=MASTER)
    with pytest.raises(ValueError):
        crypto.encrypt("value", name="", master_key=MASTER)
    # an empty master key is the configuration bug this module must not paper over
    with pytest.raises(ValueError):
        crypto.encrypt("value", name="n1", master_key="")


def test_the_plaintext_is_not_reachable_from_the_encoded_form() -> None:
    secret = "zhang-wei-dhis2-token"
    encoded, _ = crypto.encrypt(secret, name="n1", master_key=MASTER)
    assert secret not in encoded
    assert secret not in repr(encoded)
