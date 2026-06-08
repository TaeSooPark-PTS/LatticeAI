"""Self-contained OIDC ID-token validation.

A JWT is ``base64url(header).base64url(claims).base64url(signature)``. The login
flow must *never* trust a decoded payload on its own — without verifying the
signature an attacker can forge any ``email``/``sub`` claim. This module verifies
the RSA signature against the provider's published JWKS and then validates the
standard registered claims plus the login ``nonce``.

Design goals:

* No third-party JWT dependency — only the standard library plus ``cryptography``
  (already a transitive dependency, and pinned explicitly in ``pyproject.toml``).
* **Fail-closed**: any anomaly raises :class:`OIDCValidationError`; the caller
  must reject the login. There is no "best effort accept".
* Asymmetric algorithms only (``RS256``/``RS384``/``RS512``). ``alg: none`` and
  symmetric ``HS*`` tokens are rejected outright — the classic OIDC bypasses.
* Pure and injectable: :func:`verify_id_token` takes the JWKS and clock as
  arguments so every rejection path is unit-testable offline.
"""

from __future__ import annotations

import base64
import json
import time
from typing import Any, Dict, List, Optional


class OIDCValidationError(Exception):
    """Raised when an OIDC ID token fails any validation step (fail-closed)."""


# Asymmetric RSA algorithms only. Excluding HS*/none is deliberate: an attacker
# who can set ``alg`` must not be able to downgrade to a symmetric or unsigned
# token. Maps JWT alg name → cryptography hash name.
_ALLOWED_ALGS: Dict[str, str] = {"RS256": "sha256", "RS384": "sha384", "RS512": "sha512"}


def _b64url_decode(segment: str) -> bytes:
    if not isinstance(segment, str) or not segment:
        raise OIDCValidationError("empty JWT segment")
    padding = "=" * (-len(segment) % 4)
    try:
        return base64.urlsafe_b64decode(segment + padding)
    except Exception as exc:  # malformed base64
        raise OIDCValidationError(f"invalid base64url segment: {exc}")


def _b64url_uint(value: str) -> int:
    return int.from_bytes(_b64url_decode(value), "big")


def _split(token: str) -> List[str]:
    parts = str(token or "").split(".")
    if len(parts) != 3 or not all(parts):
        raise OIDCValidationError("malformed JWT: expected three non-empty segments")
    return parts


def decode_unverified_header(token: str) -> Dict[str, Any]:
    """Decode the JWT header WITHOUT verifying it (used only to pick the key)."""
    header_b64 = _split(token)[0]
    try:
        header = json.loads(_b64url_decode(header_b64))
    except Exception as exc:
        raise OIDCValidationError(f"invalid JWT header: {exc}")
    if not isinstance(header, dict):
        raise OIDCValidationError("JWT header is not an object")
    return header


def _public_key_from_jwk(jwk: Dict[str, Any]):
    if not isinstance(jwk, dict) or jwk.get("kty") != "RSA":
        raise OIDCValidationError("unsupported JWK key type (RSA required)")
    if not jwk.get("n") or not jwk.get("e"):
        raise OIDCValidationError("JWK missing modulus/exponent")
    from cryptography.hazmat.primitives.asymmetric.rsa import RSAPublicNumbers

    numbers = RSAPublicNumbers(_b64url_uint(jwk["e"]), _b64url_uint(jwk["n"]))
    return numbers.public_key()


def _candidate_keys(jwks: Any, kid: Optional[str]) -> List[Dict[str, Any]]:
    keys = jwks.get("keys") if isinstance(jwks, dict) else jwks
    if not isinstance(keys, list) or not keys:
        raise OIDCValidationError("JWKS contains no keys")
    rsa_keys = [k for k in keys if isinstance(k, dict) and k.get("kty") == "RSA"]
    if kid:
        matched = [k for k in rsa_keys if k.get("kid") == kid]
        if not matched:
            raise OIDCValidationError("no JWKS key matches the token 'kid'")
        return matched
    return rsa_keys


def _verify_signature(token: str, jwks: Any) -> Dict[str, Any]:
    header_b64, payload_b64, sig_b64 = _split(token)
    header = decode_unverified_header(token)
    alg = header.get("alg")
    if alg not in _ALLOWED_ALGS:
        # Rejects 'none' and symmetric HS* — the canonical signature-bypass attacks.
        raise OIDCValidationError(f"unsupported or unsafe JWT alg: {alg!r}")

    from cryptography.exceptions import InvalidSignature
    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.asymmetric import padding

    hash_obj = {"sha256": hashes.SHA256(), "sha384": hashes.SHA384(), "sha512": hashes.SHA512()}[
        _ALLOWED_ALGS[alg]
    ]
    signature = _b64url_decode(sig_b64)
    signing_input = f"{header_b64}.{payload_b64}".encode("ascii")

    for jwk in _candidate_keys(jwks, header.get("kid")):
        try:
            public_key = _public_key_from_jwk(jwk)
            public_key.verify(signature, signing_input, padding.PKCS1v15(), hash_obj)
        except (InvalidSignature, OIDCValidationError):
            continue
        # Signature verified — now it is safe to parse the claims.
        try:
            claims = json.loads(_b64url_decode(payload_b64))
        except Exception as exc:
            raise OIDCValidationError(f"invalid JWT claims JSON: {exc}")
        if not isinstance(claims, dict):
            raise OIDCValidationError("JWT claims are not an object")
        return claims

    raise OIDCValidationError("signature verification failed against all JWKS keys")


def verify_id_token(
    id_token: str,
    *,
    jwks: Any,
    issuer: str,
    audience: str,
    nonce: Optional[str] = None,
    now: Optional[float] = None,
    leeway: int = 60,
) -> Dict[str, Any]:
    """Verify an OIDC ID token and return its claims, or raise ``OIDCValidationError``.

    Validates, in order: signature (against ``jwks``, RSA only), ``iss``, ``aud``
    (and ``azp`` when multi-audience), ``exp``, ``iat``/``nbf``, and ``nonce``.
    All checks are fail-closed.
    """
    if not id_token:
        raise OIDCValidationError("missing id_token")
    if not issuer:
        raise OIDCValidationError("issuer not configured")
    if not audience:
        raise OIDCValidationError("audience (client_id) not configured")

    claims = _verify_signature(id_token, jwks)
    current = int(now if now is not None else time.time())

    if claims.get("iss") != issuer:
        raise OIDCValidationError("issuer mismatch")

    aud = claims.get("aud")
    audiences = aud if isinstance(aud, list) else [aud]
    if audience not in audiences:
        raise OIDCValidationError("audience mismatch")
    if isinstance(aud, list) and len(aud) > 1 and claims.get("azp") not in (None, audience):
        raise OIDCValidationError("azp (authorized party) mismatch")

    exp = claims.get("exp")
    if exp is None:
        raise OIDCValidationError("token missing 'exp'")
    if current > int(exp) + leeway:
        raise OIDCValidationError("token expired")

    iat = claims.get("iat")
    if iat is not None and int(iat) - leeway > current:
        raise OIDCValidationError("token 'iat' is in the future")

    nbf = claims.get("nbf")
    if nbf is not None and int(nbf) - leeway > current:
        raise OIDCValidationError("token not yet valid ('nbf')")

    if nonce is not None and claims.get("nonce") != nonce:
        raise OIDCValidationError("nonce mismatch")

    return claims


async def fetch_jwks(jwks_uri: str, *, timeout: float = 15.0) -> Dict[str, Any]:
    """Fetch a provider JWKS document. Network-only; injectable in tests."""
    if not jwks_uri:
        raise OIDCValidationError("discovery document has no 'jwks_uri'")
    import httpx

    async with httpx.AsyncClient() as client:
        resp = await client.get(jwks_uri, timeout=timeout)
        resp.raise_for_status()
        return resp.json()


__all__ = [
    "OIDCValidationError",
    "verify_id_token",
    "fetch_jwks",
    "decode_unverified_header",
]
