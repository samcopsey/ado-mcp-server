"""Shared test fixtures."""

from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def _set_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Set required env vars for all tests."""
    monkeypatch.setenv("AZURE_DEVOPS_PAT", "test-pat-token")
    monkeypatch.setenv("AZURE_DEVOPS_ORG", "test-org")
