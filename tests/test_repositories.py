"""Tests for repository tools."""

from __future__ import annotations

import json

import httpx
import pytest
import respx
from mcp.server.fastmcp import FastMCP

from ado_mcp_server.tools.repositories import register_repository_tools


@pytest.fixture
def mcp_server() -> FastMCP:
    server = FastMCP("test-server", stateless_http=True, json_response=True)
    register_repository_tools(server)
    return server


def parse_tool_result(result: tuple) -> dict:
    """Extract JSON from FastMCP call_tool result tuple."""
    contents, _meta = result
    return json.loads(contents[0].text)


BASE = "https://dev.azure.com/test-org"


# --- repo_list_repos ---


@respx.mock
@pytest.mark.asyncio
async def test_repo_list_repos(mcp_server: FastMCP) -> None:
    respx.get(f"{BASE}/Cast/_apis/git/repositories").mock(
        return_value=httpx.Response(200, json={
            "value": [
                {
                    "id": "abc-123", "name": "Cast", "defaultBranch": "refs/heads/main",
                    "webUrl": "https://example.com", "size": 1024,
                },
            ],
        })
    )

    result = await mcp_server.call_tool("repo_list_repos", {"project": "Cast"})
    data = parse_tool_result(result)

    assert data["count"] == 1
    assert data["repositories"][0]["name"] == "Cast"
    assert data["repositories"][0]["defaultBranch"] == "refs/heads/main"


# --- repo_get_repo ---


@respx.mock
@pytest.mark.asyncio
async def test_repo_get_repo(mcp_server: FastMCP) -> None:
    respx.get(f"{BASE}/Cast/_apis/git/repositories/Cast").mock(
        return_value=httpx.Response(200, json={
            "id": "abc-123", "name": "Cast", "defaultBranch": "refs/heads/main",
            "webUrl": "https://example.com", "size": 1024,
            "remoteUrl": "https://dev.azure.com/test-org/Cast/_git/Cast",
            "sshUrl": "git@ssh.dev.azure.com:v3/test-org/Cast/Cast",
        })
    )

    result = await mcp_server.call_tool("repo_get_repo", {"project": "Cast", "repository": "Cast"})
    data = parse_tool_result(result)

    assert data["id"] == "abc-123"
    assert data["name"] == "Cast"
    assert data["remoteUrl"] == "https://dev.azure.com/test-org/Cast/_git/Cast"


# --- repo_list_branches ---


@respx.mock
@pytest.mark.asyncio
async def test_repo_list_branches(mcp_server: FastMCP) -> None:
    respx.get(f"{BASE}/Cast/_apis/git/repositories/Cast/refs").mock(
        return_value=httpx.Response(200, json={
            "value": [
                {"name": "refs/heads/main", "objectId": "aaa111"},
                {"name": "refs/heads/feature", "objectId": "bbb222"},
            ],
        })
    )

    result = await mcp_server.call_tool("repo_list_branches", {"project": "Cast", "repository": "Cast"})
    data = parse_tool_result(result)

    assert data["count"] == 2
    assert data["branches"][0]["name"] == "main"
    assert data["branches"][1]["name"] == "feature"


# --- repo_create_branch ---


@respx.mock
@pytest.mark.asyncio
async def test_repo_create_branch(mcp_server: FastMCP) -> None:
    # Mock source branch lookup
    respx.get(f"{BASE}/Cast/_apis/git/repositories/Cast/refs").mock(
        return_value=httpx.Response(200, json={
            "value": [{"name": "refs/heads/main", "objectId": "aaa111"}],
        })
    )
    # Mock branch creation
    respx.post(f"{BASE}/Cast/_apis/git/repositories/Cast/refs").mock(
        return_value=httpx.Response(200, json={
            "value": [{"name": "refs/heads/new-branch", "newObjectId": "aaa111", "success": True}],
        })
    )

    result = await mcp_server.call_tool(
        "repo_create_branch",
        {"project": "Cast", "repository": "Cast", "name": "new-branch", "source_branch": "main"},
    )
    data = parse_tool_result(result)

    assert data["success"] is True
    assert data["name"] == "new-branch"
    assert data["objectId"] == "aaa111"


@respx.mock
@pytest.mark.asyncio
async def test_repo_create_branch_source_not_found(mcp_server: FastMCP) -> None:
    respx.get(f"{BASE}/Cast/_apis/git/repositories/Cast/refs").mock(
        return_value=httpx.Response(200, json={"value": []})
    )

    result = await mcp_server.call_tool(
        "repo_create_branch",
        {"project": "Cast", "repository": "Cast", "name": "new-branch", "source_branch": "nonexistent"},
    )
    data = parse_tool_result(result)

    assert data["error"] is True
    assert "not found" in data["message"]


# --- repo_search_commits ---


@respx.mock
@pytest.mark.asyncio
async def test_repo_search_commits(mcp_server: FastMCP) -> None:
    respx.get(f"{BASE}/Cast/_apis/git/repositories/Cast/commits").mock(
        return_value=httpx.Response(200, json={
            "value": [
                {
                    "commitId": "abc123def456789",
                    "comment": "Initial commit",
                    "author": {"name": "Sam", "date": "2026-03-01T10:00:00Z"},
                },
            ],
        })
    )

    result = await mcp_server.call_tool(
        "repo_search_commits",
        {"project": "Cast", "repository": "Cast", "author": "Sam"},
    )
    data = parse_tool_result(result)

    assert data["count"] == 1
    assert data["commits"][0]["commitId"] == "abc123def456"  # Truncated to 12 chars
    assert data["commits"][0]["author"] == "Sam"


# --- repo_list_pull_requests ---


@respx.mock
@pytest.mark.asyncio
async def test_repo_list_pull_requests_project(mcp_server: FastMCP) -> None:
    respx.get(f"{BASE}/Cast/_apis/git/pullrequests").mock(
        return_value=httpx.Response(200, json={
            "value": [
                {
                    "pullRequestId": 1,
                    "title": "Add feature",
                    "status": "active",
                    "createdBy": {"displayName": "Sam"},
                    "sourceRefName": "refs/heads/feature",
                    "targetRefName": "refs/heads/main",
                    "creationDate": "2026-03-01T10:00:00Z",
                },
            ],
        })
    )

    result = await mcp_server.call_tool("repo_list_pull_requests", {"project": "Cast"})
    data = parse_tool_result(result)

    assert data["count"] == 1
    assert data["pull_requests"][0]["title"] == "Add feature"


@respx.mock
@pytest.mark.asyncio
async def test_repo_list_pull_requests_repo(mcp_server: FastMCP) -> None:
    respx.get(f"{BASE}/Cast/_apis/git/repositories/Cast/pullrequests").mock(
        return_value=httpx.Response(200, json={"value": []})
    )

    result = await mcp_server.call_tool(
        "repo_list_pull_requests", {"project": "Cast", "repository": "Cast", "status": "completed"}
    )
    data = parse_tool_result(result)

    assert data["count"] == 0


# --- repo_get_pull_request ---


@respx.mock
@pytest.mark.asyncio
async def test_repo_get_pull_request(mcp_server: FastMCP) -> None:
    respx.get(f"{BASE}/Cast/_apis/git/repositories/Cast/pullrequests/1").mock(
        return_value=httpx.Response(200, json={
            "pullRequestId": 1,
            "title": "Add feature",
            "description": "This adds a feature",
            "status": "active",
            "createdBy": {"displayName": "Sam"},
            "sourceRefName": "refs/heads/feature",
            "targetRefName": "refs/heads/main",
            "mergeStatus": "succeeded",
            "creationDate": "2026-03-01T10:00:00Z",
            "closedDate": None,
            "reviewers": [{"displayName": "Alice", "vote": 10, "id": "reviewer-1"}],
            "isDraft": False,
        })
    )

    result = await mcp_server.call_tool(
        "repo_get_pull_request", {"project": "Cast", "repository": "Cast", "pull_request_id": 1}
    )
    data = parse_tool_result(result)

    assert data["pullRequestId"] == 1
    assert data["description"] == "This adds a feature"
    assert len(data["reviewers"]) == 1
    assert data["reviewers"][0]["vote"] == 10


# --- repo_create_pull_request ---


@respx.mock
@pytest.mark.asyncio
async def test_repo_create_pull_request(mcp_server: FastMCP) -> None:
    route = respx.post(f"{BASE}/Cast/_apis/git/repositories/Cast/pullrequests").mock(
        return_value=httpx.Response(200, json={
            "pullRequestId": 42,
            "title": "New PR",
            "status": "active",
            "sourceRefName": "refs/heads/feature",
            "targetRefName": "refs/heads/main",
            "url": "https://dev.azure.com/test-org/Cast/_apis/git/repositories/Cast/pullrequests/42",
        })
    )

    result = await mcp_server.call_tool(
        "repo_create_pull_request",
        {
            "project": "Cast",
            "repository": "Cast",
            "source_ref": "feature",
            "target_ref": "main",
            "title": "New PR",
            "description": "A new PR",
        },
    )
    data = parse_tool_result(result)

    assert data["pullRequestId"] == 42
    assert data["status"] == "active"

    # Verify refs/heads/ prefix was added
    request_body = json.loads(route.calls[0].request.content)
    assert request_body["sourceRefName"] == "refs/heads/feature"
    assert request_body["targetRefName"] == "refs/heads/main"


@respx.mock
@pytest.mark.asyncio
async def test_repo_create_pull_request_error(mcp_server: FastMCP) -> None:
    respx.post(f"{BASE}/Cast/_apis/git/repositories/Cast/pullrequests").mock(
        return_value=httpx.Response(409, text="TF401179: A pull request already exists")
    )

    result = await mcp_server.call_tool(
        "repo_create_pull_request",
        {
            "project": "Cast",
            "repository": "Cast",
            "source_ref": "refs/heads/feature",
            "target_ref": "refs/heads/main",
            "title": "Duplicate PR",
        },
    )
    data = parse_tool_result(result)

    assert data["error"] is True
    assert data["status"] == 409


# --- repo_update_pull_request ---


@respx.mock
@pytest.mark.asyncio
async def test_repo_update_pull_request(mcp_server: FastMCP) -> None:
    route = respx.patch(f"{BASE}/Cast/_apis/git/repositories/Cast/pullrequests/1").mock(
        return_value=httpx.Response(200, json={
            "pullRequestId": 1,
            "title": "Updated PR",
            "status": "completed",
            "mergeStatus": "succeeded",
        })
    )

    result = await mcp_server.call_tool(
        "repo_update_pull_request",
        {"project": "Cast", "repository": "Cast", "pull_request_id": 1, "status": "completed"},
    )
    data = parse_tool_result(result)

    assert data["status"] == "completed"

    request_body = json.loads(route.calls[0].request.content)
    assert request_body["status"] == "completed"


# --- repo_list_pr_threads ---


@respx.mock
@pytest.mark.asyncio
async def test_repo_list_pr_threads(mcp_server: FastMCP) -> None:
    respx.get(f"{BASE}/Cast/_apis/git/repositories/Cast/pullrequests/1/threads").mock(
        return_value=httpx.Response(200, json={
            "value": [
                {
                    "id": 100,
                    "status": "active",
                    "comments": [
                        {"id": 1, "author": {"displayName": "Sam"}, "content": "Please fix this"},
                    ],
                    "threadContext": {"filePath": "/src/main.py"},
                },
            ],
        })
    )

    result = await mcp_server.call_tool(
        "repo_list_pr_threads", {"project": "Cast", "repository": "Cast", "pull_request_id": 1}
    )
    data = parse_tool_result(result)

    assert data["count"] == 1
    assert data["threads"][0]["id"] == 100
    assert data["threads"][0]["comments"][0]["content"] == "Please fix this"
    assert data["threads"][0]["threadContext"]["filePath"] == "/src/main.py"


# --- repo_create_pr_thread ---


@respx.mock
@pytest.mark.asyncio
async def test_repo_create_pr_thread(mcp_server: FastMCP) -> None:
    route = respx.post(f"{BASE}/Cast/_apis/git/repositories/Cast/pullrequests/1/threads").mock(
        return_value=httpx.Response(200, json={"id": 200, "status": "active"})
    )

    result = await mcp_server.call_tool(
        "repo_create_pr_thread",
        {
            "project": "Cast",
            "repository": "Cast",
            "pull_request_id": 1,
            "content": "Code review comment",
            "file_path": "/src/main.py",
        },
    )
    data = parse_tool_result(result)

    assert data["id"] == 200
    assert data["status"] == "active"

    request_body = json.loads(route.calls[0].request.content)
    assert request_body["threadContext"]["filePath"] == "/src/main.py"
    assert request_body["comments"][0]["content"] == "Code review comment"


# --- repo_reply_to_comment ---


@respx.mock
@pytest.mark.asyncio
async def test_repo_reply_to_comment(mcp_server: FastMCP) -> None:
    respx.post(f"{BASE}/Cast/_apis/git/repositories/Cast/pullrequests/1/threads/100/comments").mock(
        return_value=httpx.Response(200, json={
            "id": 5,
            "content": "Fixed!",
            "author": {"displayName": "Sam"},
        })
    )

    result = await mcp_server.call_tool(
        "repo_reply_to_comment",
        {
            "project": "Cast",
            "repository": "Cast",
            "pull_request_id": 1,
            "thread_id": 100,
            "content": "Fixed!",
        },
    )
    data = parse_tool_result(result)

    assert data["id"] == 5
    assert data["content"] == "Fixed!"
    assert data["author"] == "Sam"


# --- Error handling (decorator) ---


@respx.mock
@pytest.mark.asyncio
async def test_repo_list_repos_unauthorized(mcp_server: FastMCP) -> None:
    respx.get(f"{BASE}/Cast/_apis/git/repositories").mock(
        return_value=httpx.Response(401, text="Unauthorized")
    )

    result = await mcp_server.call_tool("repo_list_repos", {"project": "Cast"})
    data = parse_tool_result(result)

    assert data["error"] is True
    assert data["status"] == 401


@respx.mock
@pytest.mark.asyncio
async def test_repo_get_repo_not_found(mcp_server: FastMCP) -> None:
    respx.get(f"{BASE}/Cast/_apis/git/repositories/Nonexistent").mock(
        return_value=httpx.Response(404, text="Repository not found")
    )

    result = await mcp_server.call_tool(
        "repo_get_repo", {"project": "Cast", "repository": "Nonexistent"}
    )
    data = parse_tool_result(result)

    assert data["error"] is True
    assert data["status"] == 404


@respx.mock
@pytest.mark.asyncio
async def test_repo_list_branches_not_found(mcp_server: FastMCP) -> None:
    respx.get(f"{BASE}/Cast/_apis/git/repositories/Nonexistent/refs").mock(
        return_value=httpx.Response(404, text="Repository not found")
    )

    result = await mcp_server.call_tool(
        "repo_list_branches", {"project": "Cast", "repository": "Nonexistent"}
    )
    data = parse_tool_result(result)

    assert data["error"] is True
    assert data["status"] == 404


@respx.mock
@pytest.mark.asyncio
async def test_repo_get_pull_request_not_found(mcp_server: FastMCP) -> None:
    respx.get(f"{BASE}/Cast/_apis/git/repositories/Cast/pullrequests/9999").mock(
        return_value=httpx.Response(404, text="PR not found")
    )

    result = await mcp_server.call_tool(
        "repo_get_pull_request", {"project": "Cast", "repository": "Cast", "pull_request_id": 9999}
    )
    data = parse_tool_result(result)

    assert data["error"] is True
    assert data["status"] == 404


@respx.mock
@pytest.mark.asyncio
async def test_repo_update_pull_request_error(mcp_server: FastMCP) -> None:
    respx.patch(f"{BASE}/Cast/_apis/git/repositories/Cast/pullrequests/1").mock(
        return_value=httpx.Response(409, text="Conflict")
    )

    result = await mcp_server.call_tool(
        "repo_update_pull_request",
        {"project": "Cast", "repository": "Cast", "pull_request_id": 1, "status": "completed"},
    )
    data = parse_tool_result(result)

    assert data["error"] is True
    assert data["status"] == 409


@respx.mock
@pytest.mark.asyncio
async def test_repo_list_pr_threads_not_found(mcp_server: FastMCP) -> None:
    respx.get(f"{BASE}/Cast/_apis/git/repositories/Cast/pullrequests/9999/threads").mock(
        return_value=httpx.Response(404, text="PR not found")
    )

    result = await mcp_server.call_tool(
        "repo_list_pr_threads", {"project": "Cast", "repository": "Cast", "pull_request_id": 9999}
    )
    data = parse_tool_result(result)

    assert data["error"] is True
    assert data["status"] == 404


@respx.mock
@pytest.mark.asyncio
async def test_repo_reply_to_comment_error(mcp_server: FastMCP) -> None:
    respx.post(f"{BASE}/Cast/_apis/git/repositories/Cast/pullrequests/1/threads/999/comments").mock(
        return_value=httpx.Response(404, text="Thread not found")
    )

    result = await mcp_server.call_tool(
        "repo_reply_to_comment",
        {"project": "Cast", "repository": "Cast", "pull_request_id": 1, "thread_id": 999, "content": "reply"},
    )
    data = parse_tool_result(result)

    assert data["error"] is True
    assert data["status"] == 404
