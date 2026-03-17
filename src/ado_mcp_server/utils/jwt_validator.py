"""JWT validation for Azure AD Bearer tokens."""

from __future__ import annotations

import logging
import time

import httpx
import jwt

logger = logging.getLogger(__name__)

# Cache JWKS keys for 1 hour
JWKS_CACHE_TTL = 3600


class JWKSCache:
    """Fetches and caches Azure AD JWKS public keys with TTL."""

    def __init__(self, tenant_id: str) -> None:
        self.tenant_id = tenant_id
        self.jwks_url = f"https://login.microsoftonline.com/{tenant_id}/discovery/v2.0/keys"
        self._keys: dict | None = None
        self._fetched_at: float = 0

    async def get_signing_keys(self) -> dict:
        """Return cached JWKS keys, refreshing if expired."""
        now = time.monotonic()
        if self._keys is not None and (now - self._fetched_at) < JWKS_CACHE_TTL:
            return self._keys

        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                resp = await client.get(self.jwks_url)
                resp.raise_for_status()
                self._keys = resp.json()
                self._fetched_at = now
                logger.info("Refreshed JWKS keys from %s", self.jwks_url)
                return self._keys
        except Exception:
            if self._keys is not None:
                logger.warning("JWKS refresh failed, using cached keys")
                return self._keys
            logger.warning("JWKS endpoint unavailable and no cached keys — failing open")
            raise


async def validate_jwt(token: str, tenant_id: str, audience: str, jwks_cache: JWKSCache) -> str | None:
    """Validate a JWT token against Azure AD's JWKS endpoint.

    Returns None if valid, or an error message string if invalid.
    PAT tokens (not starting with 'ey') bypass validation entirely.
    """
    # PAT tokens bypass JWT validation
    if not token.startswith("ey"):
        return None

    try:
        jwks_data = await jwks_cache.get_signing_keys()
    except Exception:
        # Fail open if JWKS endpoint is unavailable — availability over security for dev
        return None

    # Decode JWT header to find the key ID
    try:
        unverified_header = jwt.get_unverified_header(token)
    except jwt.exceptions.DecodeError as e:
        return f"Invalid JWT header: {e}"

    kid = unverified_header.get("kid")
    if not kid:
        return "JWT missing 'kid' header"

    # Find matching key in JWKS
    signing_key = None
    for key in jwks_data.get("keys", []):
        if key.get("kid") == kid:
            signing_key = jwt.algorithms.RSAAlgorithm.from_jwk(key)
            break

    if signing_key is None:
        return f"No matching key found for kid '{kid}'"

    # Verify the token
    expected_issuer = f"https://login.microsoftonline.com/{tenant_id}/v2.0"
    try:
        jwt.decode(
            token,
            signing_key,
            algorithms=["RS256"],
            audience=audience,
            issuer=expected_issuer,
            options={"require": ["exp", "iss", "aud"]},
        )
    except jwt.ExpiredSignatureError:
        return "JWT has expired"
    except jwt.InvalidAudienceError:
        return "JWT audience mismatch"
    except jwt.InvalidIssuerError:
        return "JWT issuer mismatch"
    except jwt.InvalidSignatureError:
        return "JWT signature verification failed"
    except jwt.DecodeError as e:
        return f"JWT decode error: {e}"

    return None
