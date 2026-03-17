"""Tests for ADOClient HTTP methods."""

from __future__ import annotations

import json

import httpx
import pytest
import respx

from ado_mcp_server.utils.ado_client import ADOClient


@respx.mock
@pytest.mark.asyncio
async def test_patch_json_patch_content_type() -> None:
    """patch() sends application/json-patch+json content type."""
    route = respx.patch("https://dev.azure.com/test-org/Cast/_apis/wit/workitems/1").mock(
        return_value=httpx.Response(200, json={"id": 1})
    )

    client = ADOClient("test-org", "test-pat-token")
    try:
        ops = [{"op": "add", "path": "/fields/System.Title", "value": "Test"}]
        result = await client.patch("/Cast/_apis/wit/workitems/1", json_patch=ops)
        assert result["id"] == 1

        request = route.calls[0].request
        assert request.headers["content-type"] == "application/json-patch+json"
        assert json.loads(request.content) == ops
    finally:
        await client.close()


@respx.mock
@pytest.mark.asyncio
async def test_post_json_patch_content_type() -> None:
    """post_json_patch() sends application/json-patch+json content type."""
    route = respx.post("https://dev.azure.com/test-org/Cast/_apis/wit/workitems/$Task").mock(
        return_value=httpx.Response(200, json={"id": 2})
    )

    client = ADOClient("test-org", "test-pat-token")
    try:
        ops = [{"op": "add", "path": "/fields/System.Title", "value": "New Task"}]
        result = await client.post_json_patch("/Cast/_apis/wit/workitems/$Task", json_patch=ops)
        assert result["id"] == 2

        request = route.calls[0].request
        assert request.headers["content-type"] == "application/json-patch+json"
    finally:
        await client.close()


@respx.mock
@pytest.mark.asyncio
async def test_patch_json_standard_content_type() -> None:
    """patch_json() sends standard application/json content type."""
    route = respx.patch(
        "https://dev.azure.com/test-org/Cast/Team/_apis/work/teamsettings/iterations/iter-1/capacities/m-1"
    ).mock(
        return_value=httpx.Response(200, json={"teamMember": {"displayName": "Sam"}})
    )

    client = ADOClient("test-org", "test-pat-token")
    try:
        body = {"activities": [{"name": "Dev", "capacityPerDay": 6}]}
        result = await client.patch_json(
            "/Cast/Team/_apis/work/teamsettings/iterations/iter-1/capacities/m-1",
            json=body,
        )
        assert result["teamMember"]["displayName"] == "Sam"

        request = route.calls[0].request
        assert "application/json" in request.headers["content-type"]
        # Should NOT be json-patch
        assert "json-patch" not in request.headers["content-type"]
    finally:
        await client.close()


@respx.mock
@pytest.mark.asyncio
async def test_put_sends_json_and_extra_headers() -> None:
    """put() sends JSON body and merges extra_headers."""
    route = respx.put("https://dev.azure.com/test-org/Cast/_apis/wiki/wikis/wiki-1/pages").mock(
        return_value=httpx.Response(200, json={"path": "/Test"}, headers={"ETag": '"etag-1"'})
    )

    client = ADOClient("test-org", "test-pat-token")
    try:
        resp = await client.put(
            "/Cast/_apis/wiki/wikis/wiki-1/pages",
            json={"content": "# Hello"},
            params={"path": "/Test"},
            extra_headers={"If-Match": '"old-etag"'},
        )
        assert resp.json()["path"] == "/Test"
        assert resp.headers["ETag"] == '"etag-1"'

        request = route.calls[0].request
        assert request.headers["If-Match"] == '"old-etag"'
        assert json.loads(request.content) == {"content": "# Hello"}
    finally:
        await client.close()


@respx.mock
@pytest.mark.asyncio
async def test_patch_raises_on_error() -> None:
    """HTTP errors propagate as HTTPStatusError."""
    respx.patch("https://dev.azure.com/test-org/Cast/_apis/wit/workitems/1").mock(
        return_value=httpx.Response(404, text="Not found")
    )

    client = ADOClient("test-org", "test-pat-token")
    try:
        with pytest.raises(httpx.HTTPStatusError) as exc_info:
            await client.patch("/Cast/_apis/wit/workitems/1", json_patch=[])
        assert exc_info.value.response.status_code == 404
    finally:
        await client.close()
