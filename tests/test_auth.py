"""Tests for auth middleware and token extraction."""

from __future__ import annotations

from collections.abc import Generator

import pytest
from starlette.testclient import TestClient

from ado_mcp_server.server import create_app


@pytest.fixture
def client() -> Generator[TestClient, None, None]:
    app = create_app()
    with TestClient(app) as c:
        yield c


def test_health_no_auth_required(client: TestClient) -> None:
    """Health endpoints should work without any auth."""
    resp = client.get("/")
    assert resp.status_code == 200
    assert resp.json()["status"] == "ok"


def test_health_endpoint_alias(client: TestClient) -> None:
    resp = client.get("/health")
    assert resp.status_code == 200


def test_mcp_no_api_key_allowed_when_not_configured(client: TestClient) -> None:
    """When MCP_API_KEY is not set, MCP endpoint should be accessible."""
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
    assert resp.json()["result"]["serverInfo"]["name"] == "ado-mcp-server"


def test_mcp_rejects_missing_api_key(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    """When MCP_API_KEY is set, requests without the key should be rejected."""
    monkeypatch.setenv("MCP_API_KEY", "test-secret-key-12345")

    resp = client.post(
        "/mcp",
        json={"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}},
        headers={"Content-Type": "application/json"},
    )
    assert resp.status_code == 401
    assert "API key" in resp.json()["error"]


def test_mcp_rejects_wrong_api_key(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    """Wrong API key should be rejected."""
    monkeypatch.setenv("MCP_API_KEY", "correct-key")

    resp = client.post(
        "/mcp",
        json={"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}},
        headers={"Content-Type": "application/json", "X-API-Key": "wrong-key"},
    )
    assert resp.status_code == 401


def test_mcp_accepts_correct_api_key(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    """Correct API key should be accepted."""
    monkeypatch.setenv("MCP_API_KEY", "correct-key")

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
        headers={
            "Content-Type": "application/json",
            "Accept": "application/json",
            "X-API-Key": "correct-key",
        },
    )
    assert resp.status_code == 200
    assert resp.json()["result"]["serverInfo"]["name"] == "ado-mcp-server"


def test_mcp_accepts_api_key_via_query_param(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    """API key in query parameter should be accepted (for Foundry MCP connections)."""
    monkeypatch.setenv("MCP_API_KEY", "correct-key")

    resp = client.post(
        "/mcp?api_key=correct-key",
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
        headers={
            "Content-Type": "application/json",
            "Accept": "application/json",
        },
    )
    assert resp.status_code == 200
    assert resp.json()["result"]["serverInfo"]["name"] == "ado-mcp-server"


def test_mcp_rejects_wrong_query_param_key(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    """Wrong API key in query parameter should be rejected."""
    monkeypatch.setenv("MCP_API_KEY", "correct-key")

    resp = client.post(
        "/mcp?api_key=wrong-key",
        json={"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}},
        headers={"Content-Type": "application/json"},
    )
    assert resp.status_code == 401


def test_health_still_works_with_api_key_configured(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Health should work even when API key is enforced on MCP."""
    monkeypatch.setenv("MCP_API_KEY", "some-secret")

    resp = client.get("/")
    assert resp.status_code == 200
    assert resp.json()["status"] == "ok"
