"""OIDC ID-token validation — every rejection path is exercised.

These tests sign real RS256 tokens with a locally generated RSA key and verify
them against a JWKS built from that key, entirely offline. The security promise
is fail-closed: a token is accepted only when signature, issuer, audience,
expiry and nonce all check out.
"""

import base64
import json
import time

import pytest

from latticeai.core.oidc import OIDCValidationError, verify_id_token

cryptography = pytest.importorskip("cryptography")
from cryptography.hazmat.primitives import hashes  # noqa: E402
from cryptography.hazmat.primitives.asymmetric import padding, rsa  # noqa: E402

ISSUER = "https://idp.example.com"
AUDIENCE = "lattice-client-id"


def _b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _int_b64url(value: int) -> str:
    return _b64url(value.to_bytes((value.bit_length() + 7) // 8, "big"))


def _make_key():
    return rsa.generate_private_key(public_exponent=65537, key_size=2048)


def _jwks(public_key, kid="key-1"):
    nums = public_key.public_numbers()
    return {"keys": [{
        "kty": "RSA", "kid": kid, "use": "sig", "alg": "RS256",
        "n": _int_b64url(nums.n), "e": _int_b64url(nums.e),
    }]}


_HASHES = {"RS256": hashes.SHA256(), "RS384": hashes.SHA384(), "RS512": hashes.SHA512()}


def _sign(claims, key, *, kid="key-1", alg="RS256", raw_sig=None):
    header = {"alg": alg, "typ": "JWT"}
    if kid is not None:
        header["kid"] = kid
    signing_input = f"{_b64url(json.dumps(header).encode())}.{_b64url(json.dumps(claims).encode())}"
    if raw_sig is not None:
        sig = raw_sig
    elif alg in _HASHES:
        sig = key.sign(signing_input.encode("ascii"), padding.PKCS1v15(), _HASHES[alg])
    else:
        sig = b"unsigned"
    return f"{signing_input}.{_b64url(sig)}"


def _claims(**overrides):
    now = int(time.time())
    base = {"iss": ISSUER, "aud": AUDIENCE, "sub": "user-123",
            "email": "alice@example.com", "exp": now + 600, "iat": now, "nonce": "n-abc"}
    base.update(overrides)
    return base


@pytest.fixture
def key():
    return _make_key()


# ── happy path ────────────────────────────────────────────────────────────────
def test_valid_token_accepted(key):
    token = _sign(_claims(), key)
    claims = verify_id_token(token, jwks=_jwks(key.public_key()),
                             issuer=ISSUER, audience=AUDIENCE, nonce="n-abc")
    assert claims["email"] == "alice@example.com"


def test_valid_token_with_list_audience(key):
    token = _sign(_claims(aud=[AUDIENCE, "other"], azp=AUDIENCE), key)
    claims = verify_id_token(token, jwks=_jwks(key.public_key()),
                             issuer=ISSUER, audience=AUDIENCE, nonce="n-abc")
    assert claims["sub"] == "user-123"


# ── rejection paths (required by the release security checklist) ──────────────
def test_invalid_signature_rejected(key):
    attacker = _make_key()
    token = _sign(_claims(email="evil@example.com"), attacker)  # signed by wrong key
    with pytest.raises(OIDCValidationError):
        verify_id_token(token, jwks=_jwks(key.public_key()),
                        issuer=ISSUER, audience=AUDIENCE, nonce="n-abc")


def test_forged_payload_rejected(key):
    """A token whose payload is swapped after signing must fail (the core attack)."""
    token = _sign(_claims(email="alice@example.com"), key)
    head, _payload, sig = token.split(".")
    forged_payload = _b64url(json.dumps(_claims(email="admin@example.com")).encode())
    forged = f"{head}.{forged_payload}.{sig}"
    with pytest.raises(OIDCValidationError):
        verify_id_token(forged, jwks=_jwks(key.public_key()),
                        issuer=ISSUER, audience=AUDIENCE, nonce="n-abc")


def test_invalid_issuer_rejected(key):
    token = _sign(_claims(iss="https://evil.example.com"), key)
    with pytest.raises(OIDCValidationError):
        verify_id_token(token, jwks=_jwks(key.public_key()),
                        issuer=ISSUER, audience=AUDIENCE, nonce="n-abc")


def test_invalid_audience_rejected(key):
    token = _sign(_claims(aud="someone-else"), key)
    with pytest.raises(OIDCValidationError):
        verify_id_token(token, jwks=_jwks(key.public_key()),
                        issuer=ISSUER, audience=AUDIENCE, nonce="n-abc")


def test_expired_token_rejected(key):
    now = int(time.time())
    token = _sign(_claims(exp=now - 3600, iat=now - 7200), key)
    with pytest.raises(OIDCValidationError):
        verify_id_token(token, jwks=_jwks(key.public_key()),
                        issuer=ISSUER, audience=AUDIENCE, nonce="n-abc")


def test_missing_exp_rejected(key):
    claims = _claims()
    claims.pop("exp")
    token = _sign(claims, key)
    with pytest.raises(OIDCValidationError):
        verify_id_token(token, jwks=_jwks(key.public_key()),
                        issuer=ISSUER, audience=AUDIENCE, nonce="n-abc")


def test_nonce_mismatch_rejected(key):
    token = _sign(_claims(nonce="attacker-nonce"), key)
    with pytest.raises(OIDCValidationError):
        verify_id_token(token, jwks=_jwks(key.public_key()),
                        issuer=ISSUER, audience=AUDIENCE, nonce="n-abc")


def test_alg_none_rejected(key):
    token = _sign(_claims(), key, alg="none", raw_sig=b"")
    with pytest.raises(OIDCValidationError):
        verify_id_token(token, jwks=_jwks(key.public_key()),
                        issuer=ISSUER, audience=AUDIENCE, nonce="n-abc")


def test_symmetric_hs256_rejected(key):
    # Header claims HS256; an attacker would hope the verifier uses the public
    # key (or JWKS modulus) as an HMAC secret. We reject all symmetric algs.
    token = _sign(_claims(), key, alg="HS256", raw_sig=b"forged-hmac")
    with pytest.raises(OIDCValidationError):
        verify_id_token(token, jwks=_jwks(key.public_key()),
                        issuer=ISSUER, audience=AUDIENCE, nonce="n-abc")


def test_unknown_kid_rejected(key):
    token = _sign(_claims(), key, kid="rotated-away")
    with pytest.raises(OIDCValidationError):
        verify_id_token(token, jwks=_jwks(key.public_key(), kid="key-1"),
                        issuer=ISSUER, audience=AUDIENCE, nonce="n-abc")


def test_malformed_token_rejected(key):
    with pytest.raises(OIDCValidationError):
        verify_id_token("not-a-jwt", jwks=_jwks(key.public_key()),
                        issuer=ISSUER, audience=AUDIENCE)


def test_empty_jwks_rejected(key):
    token = _sign(_claims(), key)
    with pytest.raises(OIDCValidationError):
        verify_id_token(token, jwks={"keys": []},
                        issuer=ISSUER, audience=AUDIENCE, nonce="n-abc")


def test_leeway_allows_small_clock_skew(key):
    now = int(time.time())
    token = _sign(_claims(exp=now - 10), key)  # just expired
    # Default leeway (60s) accepts it; zero leeway rejects it.
    verify_id_token(token, jwks=_jwks(key.public_key()),
                    issuer=ISSUER, audience=AUDIENCE, nonce="n-abc")
    with pytest.raises(OIDCValidationError):
        verify_id_token(token, jwks=_jwks(key.public_key()),
                        issuer=ISSUER, audience=AUDIENCE, nonce="n-abc", leeway=0)
