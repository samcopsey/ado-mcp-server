"""Tests for JWT validation middleware and utility."""

from __future__ import annotations

import json
import time

import httpx
import jwt
import pytest
import respx
from cryptography.hazmat.primitives.asymmetric import rsa
from starlette.testclient import TestClient

from ado_mcp_server.utils.jwt_validator import JWKSCache, validate_jwt

# --- Test RSA key pair for signing JWTs ---

_private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
_public_key = _private_key.public_key()
_TEST_KID = "test-kid-123"
_TEST_TENANT = "test-tenant-id"
_TEST_AUDIENCE = "api://ado-mcp-server"
_TEST_ISSUER = f"https://login.microsoftonline.com/{_TEST_TENANT}/v2.0"


def _jwk_from_public_key() -> dict:
    """Convert the test public key to JWK format."""
    from jwt.algorithms import RSAAlgorithm

    jwk = json.loads(RSAAlgorithm.to_jwk(_public_key))
    jwk["kid"] = _TEST_KID
    jwk["use"] = "sig"
    jwk["alg"] = "RS256"
    return jwk


def _make_jwt(claims: dict | None = None, headers: dict | None = None, expired: bool = False) -> str:
    """Create a signed JWT with test key."""
    now = int(time.time())
    payload = {
        "iss": _TEST_ISSUER,
        "aud": _TEST_AUDIENCE,
        "exp": now - 10 if expired else now + 3600,
        "nbf": now - 10,
        "iat": now,
        "sub": "user-123",
    }
    if claims:
        payload.update(claims)
    hdr = {"kid": _TEST_KID}
    if headers:
        hdr.update(headers)
    return jwt.encode(payload, _private_key, algorithm="RS256", headers=hdr)


JWKS_URL = f"https://login.microsoftonline.com/{_TEST_TENANT}/discovery/v2.0/keys"
JWKS_RESPONSE = {"keys": [_jwk_from_public_key()]}


# --- JWKSCache tests ---


@respx.mock
@pytest.mark.asyncio
async def test_jwks_cache_fetches_and_caches() -> None:
    route = respx.get(JWKS_URL).mock(
        return_value=httpx.Response(200, json=JWKS_RESPONSE)
    )

    cache = JWKSCache(_TEST_TENANT)
    keys1 = await cache.get_signing_keys()
    keys2 = await cache.get_signing_keys()

    assert keys1 == JWKS_RESPONSE
    assert keys2 == JWKS_RESPONSE
    assert route.call_count == 1  # Only fetched once (cached)


@respx.mock
@pytest.mark.asyncio
async def test_jwks_cache_refreshes_after_ttl() -> None:
    route = respx.get(JWKS_URL).mock(
        return_value=httpx.Response(200, json=JWKS_RESPONSE)
    )

    cache = JWKSCache(_TEST_TENANT)
    await cache.get_signing_keys()
    # Force expiry
    cache._fetched_at = time.monotonic() - 7200
    await cache.get_signing_keys()

    assert route.call_count == 2


@respx.mock
@pytest.mark.asyncio
async def test_jwks_cache_uses_stale_on_failure() -> None:
    """If refresh fails but we have cached keys, use them."""
    respx.get(JWKS_URL).mock(
        return_value=httpx.Response(200, json=JWKS_RESPONSE)
    )
    cache = JWKSCache(_TEST_TENANT)
    await cache.get_signing_keys()

    # Force expiry and make next call fail
    cache._fetched_at = time.monotonic() - 7200
    respx.get(JWKS_URL).mock(
        return_value=httpx.Response(500, text="Server error")
    )

    keys = await cache.get_signing_keys()
    assert keys == JWKS_RESPONSE  # Stale but returned


@respx.mock
@pytest.mark.asyncio
async def test_jwks_cache_fails_open_when_no_cache() -> None:
    """If JWKS is unreachable and no cache, raise (caller handles fail-open)."""
    respx.get(JWKS_URL).mock(
        return_value=httpx.Response(500, text="Server error")
    )
    cache = JWKSCache(_TEST_TENANT)

    with pytest.raises(Exception):
        await cache.get_signing_keys()


# --- validate_jwt tests ---


@pytest.mark.asyncio
async def test_pat_token_bypasses_validation() -> None:
    """PAT tokens (not starting with 'ey') bypass JWT validation."""
    cache = JWKSCache(_TEST_TENANT)  # Won't be used
    result = await validate_jwt("pat-token-abc123", _TEST_TENANT, _TEST_AUDIENCE, cache)
    assert result is None


@respx.mock
@pytest.mark.asyncio
async def test_valid_jwt_accepted() -> None:
    respx.get(JWKS_URL).mock(return_value=httpx.Response(200, json=JWKS_RESPONSE))

    cache = JWKSCache(_TEST_TENANT)
    token = _make_jwt()
    result = await validate_jwt(token, _TEST_TENANT, _TEST_AUDIENCE, cache)
    assert result is None


@respx.mock
@pytest.mark.asyncio
async def test_expired_jwt_rejected() -> None:
    respx.get(JWKS_URL).mock(return_value=httpx.Response(200, json=JWKS_RESPONSE))

    cache = JWKSCache(_TEST_TENANT)
    token = _make_jwt(expired=True)
    result = await validate_jwt(token, _TEST_TENANT, _TEST_AUDIENCE, cache)
    assert result == "JWT has expired"


@respx.mock
@pytest.mark.asyncio
async def test_wrong_audience_rejected() -> None:
    respx.get(JWKS_URL).mock(return_value=httpx.Response(200, json=JWKS_RESPONSE))

    cache = JWKSCache(_TEST_TENANT)
    token = _make_jwt(claims={"aud": "wrong-audience"})
    result = await validate_jwt(token, _TEST_TENANT, _TEST_AUDIENCE, cache)
    assert result == "JWT audience mismatch"


@respx.mock
@pytest.mark.asyncio
async def test_wrong_issuer_rejected() -> None:
    respx.get(JWKS_URL).mock(return_value=httpx.Response(200, json=JWKS_RESPONSE))

    cache = JWKSCache(_TEST_TENANT)
    token = _make_jwt(claims={"iss": "https://evil.com/v2.0"})
    result = await validate_jwt(token, _TEST_TENANT, _TEST_AUDIENCE, cache)
    assert result == "JWT issuer mismatch"


@respx.mock
@pytest.mark.asyncio
async def test_bad_signature_rejected() -> None:
    respx.get(JWKS_URL).mock(return_value=httpx.Response(200, json=JWKS_RESPONSE))

    # Sign with a different key
    other_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    now = int(time.time())
    token = jwt.encode(
        {"iss": _TEST_ISSUER, "aud": _TEST_AUDIENCE, "exp": now + 3600, "sub": "x"},
        other_key,
        algorithm="RS256",
        headers={"kid": _TEST_KID},
    )

    cache = JWKSCache(_TEST_TENANT)
    result = await validate_jwt(token, _TEST_TENANT, _TEST_AUDIENCE, cache)
    assert result == "JWT signature verification failed"


@respx.mock
@pytest.mark.asyncio
async def test_jwks_unavailable_fails_open() -> None:
    """If JWKS is unreachable, fail open (return None = allow)."""
    respx.get(JWKS_URL).mock(return_value=httpx.Response(500, text="Down"))

    cache = JWKSCache(_TEST_TENANT)
    token = _make_jwt()
    result = await validate_jwt(token, _TEST_TENANT, _TEST_AUDIENCE, cache)
    assert result is None  # Fail open


# --- JWTMiddleware integration tests ---


def test_jwt_disabled_by_default(monkeypatch: pytest.MonkeyPatch) -> None:
    """JWT validation is disabled by default, so all tokens pass."""
    monkeypatch.delenv("JWT_VALIDATION_DISABLED", raising=False)
    monkeypatch.delenv("JWT_TENANT_ID", raising=False)

    from ado_mcp_server.server import create_app

    app = create_app()
    with TestClient(app) as client:
        resp = client.post(
            "/mcp",
            json={
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {
                    "protocolVersion": "2025-03-26",
                    "capabilities": {},
                    "clientInfo": {"name": "test", "version": "1.0"},
                },
            },
            headers={"Content-Type": "application/json", "Accept": "application/json"},
        )
        assert resp.status_code == 200


def test_jwt_enabled_rejects_missing_token(monkeypatch: pytest.MonkeyPatch) -> None:
    """When JWT is enabled, requests without a Bearer token are rejected."""
    monkeypatch.setenv("JWT_VALIDATION_DISABLED", "false")
    monkeypatch.setenv("JWT_TENANT_ID", _TEST_TENANT)
    monkeypatch.setenv("JWT_AUDIENCE", _TEST_AUDIENCE)

    from ado_mcp_server.server import create_app

    app = create_app()
    with TestClient(app) as client:
        resp = client.post(
            "/mcp",
            json={"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}},
            headers={"Content-Type": "application/json"},
        )
        assert resp.status_code == 401
        assert "JWT validation failed" in resp.json()["error"]


def test_jwt_enabled_allows_health(monkeypatch: pytest.MonkeyPatch) -> None:
    """Health endpoint is always accessible even with JWT enabled."""
    monkeypatch.setenv("JWT_VALIDATION_DISABLED", "false")
    monkeypatch.setenv("JWT_TENANT_ID", _TEST_TENANT)
    monkeypatch.setenv("JWT_AUDIENCE", _TEST_AUDIENCE)

    from ado_mcp_server.server import create_app

    app = create_app()
    with TestClient(app) as client:
        resp = client.get("/health")
        assert resp.status_code == 200
