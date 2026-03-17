"""Async HTTP client for Azure DevOps REST API."""

from __future__ import annotations

import json
import os

import httpx


class ADOClient:
    """Wraps httpx.AsyncClient with ADO auth and base URL handling."""

    API_VERSION = "7.1"

    def __init__(self, org: str, token: str | None = None) -> None:
        self.org = org
        self.token = token or os.environ.get("AZURE_DEVOPS_PAT", "")
        self.base_url = f"https://dev.azure.com/{org}"
        self._client: httpx.AsyncClient | None = None

    async def _get_client(self) -> httpx.AsyncClient:
        if self._client is None or self._client.is_closed:
            headers: dict[str, str] = {"Accept": "application/json"}
            if self.token:
                headers["Authorization"] = f"Bearer {self.token}"
            self._client = httpx.AsyncClient(
                base_url=self.base_url,
                headers=headers,
                auth=httpx.BasicAuth("", self.token) if self.token and not self.token.startswith("ey") else None,
                timeout=30.0,
            )
            # If token looks like a JWT (starts with ey), use Bearer auth already set in headers.
            # If it looks like a PAT, use Basic auth.
        return self._client

    async def get(self, path: str, params: dict | None = None) -> dict:
        """GET request to ADO REST API."""
        client = await self._get_client()
        params = params or {}
        params.setdefault("api-version", self.API_VERSION)
        resp = await client.get(path, params=params)
        resp.raise_for_status()
        return resp.json()

    async def post(self, path: str, json: dict | list | None = None, params: dict | None = None) -> dict:
        """POST request to ADO REST API."""
        client = await self._get_client()
        params = params or {}
        params.setdefault("api-version", self.API_VERSION)
        resp = await client.post(path, json=json, params=params)
        resp.raise_for_status()
        return resp.json()

    async def patch(self, path: str, json_patch: list, params: dict | None = None) -> dict:
        """PATCH request with application/json-patch+json content type."""
        client = await self._get_client()
        params = params or {}
        params.setdefault("api-version", self.API_VERSION)
        resp = await client.patch(
            path,
            content=json.dumps(json_patch),
            headers={"Content-Type": "application/json-patch+json"},
            params=params,
        )
        resp.raise_for_status()
        return resp.json()

    async def post_json_patch(self, path: str, json_patch: list, params: dict | None = None) -> dict:
        """POST request with application/json-patch+json content type (for work item creation)."""
        client = await self._get_client()
        params = params or {}
        params.setdefault("api-version", self.API_VERSION)
        resp = await client.post(
            path,
            content=json.dumps(json_patch),
            headers={"Content-Type": "application/json-patch+json"},
            params=params,
        )
        resp.raise_for_status()
        return resp.json()

    async def patch_json(self, path: str, json: dict | list | None = None, params: dict | None = None) -> dict:
        """PATCH request with standard application/json content type."""
        client = await self._get_client()
        params = params or {}
        params.setdefault("api-version", self.API_VERSION)
        resp = await client.patch(path, json=json, params=params)
        resp.raise_for_status()
        return resp.json()

    async def put(
        self,
        path: str,
        json: dict | None = None,
        params: dict | None = None,
        extra_headers: dict[str, str] | None = None,
    ) -> httpx.Response:
        """PUT request to ADO REST API. Returns the full Response (for header access)."""
        client = await self._get_client()
        params = params or {}
        params.setdefault("api-version", self.API_VERSION)
        headers = extra_headers or {}
        resp = await client.put(path, json=json, params=params, headers=headers)
        resp.raise_for_status()
        return resp

    async def close(self) -> None:
        if self._client and not self._client.is_closed:
            await self._client.aclose()
