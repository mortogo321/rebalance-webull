from __future__ import annotations

import base64

import pytest

from engine.crypto import (
    CryptoError,
    decrypt,
    encrypt,
    fingerprint,
    generate_key,
    hint_of,
    key_version_of,
    load_key,
)

USER_A = "a0000000-0000-4000-8000-000000000001"
USER_B = "a0000000-0000-4000-8000-000000000002"
SECRET = "wb_live_key_9f3c2a17b4e8"


@pytest.fixture
def key() -> bytes:
    return load_key(base64.b64encode(bytes(range(32))).decode())


class TestKeyLoading:
    def test_a_generated_key_round_trips(self):
        assert len(load_key(generate_key())) == 32

    def test_an_empty_key_is_rejected(self):
        with pytest.raises(CryptoError, match="not set"):
            load_key("")

    def test_a_short_key_is_rejected(self):
        with pytest.raises(CryptoError, match="must decode to 32 bytes"):
            load_key(base64.b64encode(b"too-short").decode())

    def test_non_base64_is_rejected(self):
        with pytest.raises(CryptoError):
            load_key("!!!not base64!!!")

    def test_urlsafe_and_standard_alphabets_both_work(self):
        raw = bytes(range(32))
        assert load_key(base64.b64encode(raw).decode()) == raw
        assert load_key(base64.urlsafe_b64encode(raw).decode()) == raw


class TestRoundTrip:
    def test_plaintext_survives_encryption(self, key):
        assert decrypt(encrypt(SECRET, key=key, aad=USER_A), key=key, aad=USER_A) == SECRET

    def test_ciphertext_does_not_contain_the_plaintext(self, key):
        assert SECRET not in encrypt(SECRET, key=key, aad=USER_A)

    def test_the_same_input_encrypts_differently_every_time(self, key):
        # A fresh IV per call. Deterministic ciphertext would leak that two
        # users registered the same key.
        a = encrypt(SECRET, key=key, aad=USER_A)
        b = encrypt(SECRET, key=key, aad=USER_A)
        assert a != b
        assert decrypt(a, key=key, aad=USER_A) == decrypt(b, key=key, aad=USER_A)

    def test_empty_values_are_refused(self, key):
        with pytest.raises(CryptoError, match="empty value"):
            encrypt("", key=key, aad=USER_A)

    def test_unicode_survives(self, key):
        value = "ключ-鍵-مفتاح-🔑"
        assert decrypt(encrypt(value, key=key, aad=USER_A), key=key, aad=USER_A) == value


class TestTamperResistance:
    def test_another_users_id_cannot_open_the_envelope(self, key):
        # The core guarantee: a stolen row is inert on any other user_id.
        sealed = encrypt(SECRET, key=key, aad=USER_A)
        with pytest.raises(CryptoError, match="could not be decrypted"):
            decrypt(sealed, key=key, aad=USER_B)

    def test_a_different_master_key_cannot_open_the_envelope(self, key):
        sealed = encrypt(SECRET, key=key, aad=USER_A)
        other = load_key(generate_key())
        with pytest.raises(CryptoError, match="could not be decrypted"):
            decrypt(sealed, key=other, aad=USER_A)

    def test_a_flipped_ciphertext_byte_is_detected(self, key):
        version, iv, payload = encrypt(SECRET, key=key, aad=USER_A).split(".")
        flipped = ("B" if payload[0] != "B" else "C") + payload[1:]
        with pytest.raises(CryptoError):
            decrypt(f"{version}.{iv}.{flipped}", key=key, aad=USER_A)

    def test_a_swapped_iv_is_detected(self, key):
        _, _, payload = encrypt(SECRET, key=key, aad=USER_A).split(".")
        other_iv = encrypt(SECRET, key=key, aad=USER_A).split(".")[1]
        with pytest.raises(CryptoError):
            decrypt(f"v1.{other_iv}.{payload}", key=key, aad=USER_A)

    def test_the_error_does_not_say_which_check_failed(self, key):
        # Wrong key and wrong user must be indistinguishable to a caller.
        sealed = encrypt(SECRET, key=key, aad=USER_A)
        with pytest.raises(CryptoError) as wrong_user:
            decrypt(sealed, key=key, aad=USER_B)
        with pytest.raises(CryptoError) as wrong_key:
            decrypt(sealed, key=load_key(generate_key()), aad=USER_A)
        assert str(wrong_user.value) == str(wrong_key.value)


class TestEnvelopeFormat:
    def test_the_envelope_has_three_dot_separated_parts(self, key):
        assert len(encrypt(SECRET, key=key, aad=USER_A).split(".")) == 3

    def test_the_version_prefix_is_readable_without_the_key(self, key):
        assert key_version_of(encrypt(SECRET, key=key, aad=USER_A, key_version=7)) == 7

    def test_base64url_has_no_padding_so_it_is_url_and_json_safe(self, key):
        _, iv, payload = encrypt(SECRET, key=key, aad=USER_A).split(".")
        assert "=" not in iv and "=" not in payload
        assert "+" not in payload and "/" not in payload

    def test_malformed_envelopes_are_rejected(self, key):
        for bad in ("", "nodots", "v1.onlytwo", "x1.aaaa.bbbb", "v.aaaa.bbbb"):
            with pytest.raises(CryptoError):
                decrypt(bad, key=key, aad=USER_A)



class TestHints:
    def test_the_hint_is_only_the_last_four_characters(self):
        assert hint_of(SECRET) == SECRET[-4:]

    def test_short_secrets_do_not_leak_themselves_as_a_hint(self):
        assert hint_of("ab") == "****"

    def test_the_fingerprint_is_stable_and_not_the_secret(self):
        assert fingerprint(SECRET) == fingerprint(SECRET)
        assert SECRET not in fingerprint(SECRET)
        assert fingerprint(SECRET) != fingerprint(SECRET + "x")
