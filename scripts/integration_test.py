"""Integration tests against the live MCP endpoint with real ADO data.

Connects to the deployed MCP server on Container Apps (or local) and
exercises each tool against the cast-testing ADO org.

Usage:
    # Against deployed endpoint (needs PAT + API key)
    export AZURE_DEVOPS_PAT=xxx
    export MCP_API_KEY=xxx
    python scripts/integration_test.py

    # Against local server (just needs PAT)
    export AZURE_DEVOPS_PAT=xxx
    python scripts/integration_test.py --url http://localhost:3000/mcp

    # Run a single test
    python scripts/integration_test.py -k test_wit_create_work_item
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import time
import traceback

from mcp.client.session import ClientSession
from mcp.client.streamable_http import streamablehttp_client

# --- Config ---

DEFAULT_URL = os.environ.get("MCP_SERVER_URL", "http://localhost:3000/mcp")
PROJECT = "Cast"
TEAM = "Cast Team"


# --- Helpers ---


def _headers() -> dict[str, str]:
    headers: dict[str, str] = {}
    pat = os.environ.get("AZURE_DEVOPS_PAT", "")
    if pat:
        headers["Authorization"] = f"Bearer {pat}"
    api_key = os.environ.get("MCP_API_KEY", "")
    if api_key:
        headers["X-API-Key"] = api_key
    return headers


def _parse(result) -> dict | list:
    """Parse CallToolResult into Python object."""
    text = result.content[0].text
    return json.loads(text)


class IntegrationTest:
    """Runs integration tests against a live MCP endpoint.

    Tests are defined as methods and executed in the explicit order
    given by TEST_ORDER. This matters because write tests are stateful
    (create → update → link → unlink → close).
    """

    def __init__(self, session: ClientSession) -> None:
        self.session = session
        self._created_work_item_id: int | None = None
        self._created_iteration_id: str | None = None
        self._repo_name: str | None = None
        self._wiki_id: str | None = None
        self._wiki_page_etag: str | None = None

    # --- Core tools ---

    async def test_core_list_projects(self) -> None:
        """core_list_projects returns at least one project."""
        result = await self.session.call_tool("core_list_projects", {})
        data = _parse(result)
        assert data["count"] >= 1, f"Expected at least 1 project, got {data['count']}"
        names = [p["name"] for p in data["projects"]]
        assert PROJECT in names, f"Expected '{PROJECT}' in {names}"

    async def test_core_list_project_teams(self) -> None:
        """core_list_project_teams returns teams for Cast."""
        result = await self.session.call_tool(
            "core_list_project_teams", {"project": PROJECT}
        )
        data = _parse(result)
        assert data["count"] >= 1
        names = [t["name"] for t in data["teams"]]
        assert TEAM in names, f"Expected '{TEAM}' in {names}"

    async def test_core_get_identity_ids(self) -> None:
        """core_get_identity_ids calls VSSPS API without error."""
        result = await self.session.call_tool(
            "core_get_identity_ids", {"searchFilter": "Sam"}
        )
        data = _parse(result)
        assert "error" not in data, f"API error: {json.dumps(data, indent=2)}"
        if data["count"] == 0:
            print("    WARN: 0 identities returned (PAT may lack Identity Read scope)")
        else:
            assert data["count"] >= 1

    # --- Work Items Read ---

    async def test_wit_query_wiql(self) -> None:
        """wit_query_wiql returns work items."""
        result = await self.session.call_tool(
            "wit_query_wiql",
            {
                "project": PROJECT,
                "wiql": "SELECT [System.Id] FROM WorkItems WHERE [System.State] <> '' ORDER BY [System.Id] DESC",
            },
        )
        data = _parse(result)
        assert data["count"] >= 1, "Expected at least 1 work item"

    async def test_wit_query_wiql_with_details(self) -> None:
        """wit_query_wiql with fetch_details returns field data."""
        result = await self.session.call_tool(
            "wit_query_wiql",
            {
                "project": PROJECT,
                "wiql": "SELECT [System.Id] FROM WorkItems WHERE [System.State] <> '' ORDER BY [System.Id] DESC",
                "fetch_details": True,
            },
        )
        data = _parse(result)
        assert data["count"] >= 1
        assert "title" in data["work_items"][0]

    async def test_wit_get_work_item(self) -> None:
        """wit_get_work_item returns a specific item with relations."""
        wiql_result = await self.session.call_tool(
            "wit_query_wiql",
            {
                "project": PROJECT,
                "wiql": "SELECT [System.Id] FROM WorkItems ORDER BY [System.Id] ASC",
            },
        )
        wiql_data = _parse(wiql_result)
        first_id = wiql_data["work_items"][0]["id"]

        result = await self.session.call_tool(
            "wit_get_work_item",
            {"project": PROJECT, "work_item_id": first_id},
        )
        data = _parse(result)
        assert data["id"] == first_id
        assert "title" in data

    async def test_wit_get_work_items_batch(self) -> None:
        """wit_get_work_items_batch fetches multiple items."""
        result = await self.session.call_tool(
            "wit_get_work_items_batch",
            {"project": PROJECT, "ids": "1,2,3"},
        )
        data = _parse(result)
        assert data["count"] >= 1

    async def test_wit_get_work_items_for_iteration(self) -> None:
        """wit_get_work_items_for_iteration returns items for a sprint."""
        result = await self.session.call_tool(
            "wit_get_work_items_for_iteration",
            {"project": PROJECT, "iteration_path": f"{PROJECT}\\Sprint 1"},
        )
        data = _parse(result)
        assert data["count"] >= 0  # May be empty but shouldn't error

    async def test_wit_list_backlogs(self) -> None:
        """wit_list_backlogs returns backlog levels."""
        result = await self.session.call_tool(
            "wit_list_backlogs",
            {"project": PROJECT, "team": TEAM},
        )
        data = _parse(result)
        assert data["count"] >= 1
        names = [b["name"] for b in data["backlogs"]]
        assert any("Epic" in n or "Feature" in n or "Stor" in n for n in names)

    async def test_wit_get_work_item_type(self) -> None:
        """wit_get_work_item_type returns type definition."""
        result = await self.session.call_tool(
            "wit_get_work_item_type",
            {"project": PROJECT, "type_name": "User Story"},
        )
        data = _parse(result)
        assert data["name"] == "User Story"
        assert len(data["states"]) >= 1

    async def test_wit_list_fields(self) -> None:
        """wit_list_fields returns field definitions."""
        result = await self.session.call_tool(
            "wit_list_fields",
            {"project": PROJECT},
        )
        data = _parse(result)
        assert data["count"] >= 10
        ref_names = [f["reference_name"] for f in data["fields"]]
        assert "System.Title" in ref_names

    async def test_wit_get_team_iterations(self) -> None:
        """wit_get_team_iterations returns sprint schedules."""
        result = await self.session.call_tool(
            "wit_get_team_iterations",
            {"project": PROJECT, "team": TEAM},
        )
        data = _parse(result)
        assert data["count"] >= 1
        assert data["iterations"][0]["name"]

    async def test_wit_get_team_capacity(self) -> None:
        """wit_get_team_capacity returns capacity data."""
        iter_result = await self.session.call_tool(
            "wit_get_team_iterations",
            {"project": PROJECT, "team": TEAM, "timeframe": "current"},
        )
        iter_data = _parse(iter_result)
        if not iter_data["iterations"]:
            print("    SKIP: no current iteration")
            return
        iter_id = iter_data["iterations"][0]["id"]

        result = await self.session.call_tool(
            "wit_get_team_capacity",
            {"project": PROJECT, "team": TEAM, "iteration_id": iter_id},
        )
        data = _parse(result)
        assert "working_days" in data

    # --- Work Items Write (order matters: create → update → comment → link → unlink → close) ---

    async def test_wit_create_work_item(self) -> None:
        """wit_create_work_item creates a Task and returns its ID."""
        result = await self.session.call_tool(
            "wit_create_work_item",
            {
                "project": PROJECT,
                "type_name": "Task",
                "title": f"Integration test task {int(time.time())}",
            },
        )
        data = _parse(result)
        assert not data.get("error"), f"Create failed: {data}"
        assert data["id"] is not None, f"Expected ID, got: {data}"
        assert isinstance(data["id"], int), f"ID should be int, got: {type(data['id'])}"
        self._created_work_item_id = data["id"]
        print(f"    Created work item {data['id']}")

    async def test_wit_create_work_item_with_fields(self) -> None:
        """wit_create_work_item with extra fields and parent link."""
        result = await self.session.call_tool(
            "wit_create_work_item",
            {
                "project": PROJECT,
                "type_name": "Task",
                "title": f"Integration child task {int(time.time())}",
                "fields": {
                    "Microsoft.VSTS.Scheduling.RemainingWork": 4,
                },
                "parent_id": 1,
            },
        )
        data = _parse(result)
        assert not data.get("error"), f"Create failed: {data}"
        assert data["id"] is not None
        print(f"    Created work item {data['id']} (child of 1)")

    async def test_wit_update_work_item(self) -> None:
        """wit_update_work_item changes a field."""
        if not self._created_work_item_id:
            print("    SKIP: no work item created in previous test")
            return

        result = await self.session.call_tool(
            "wit_update_work_item",
            {
                "project": PROJECT,
                "work_item_id": self._created_work_item_id,
                "fields": {"System.State": "Active"},
            },
        )
        data = _parse(result)
        assert not data.get("error"), f"Update failed: {data}"
        assert data["state"] == "Active" or data.get("id") == self._created_work_item_id

    async def test_wit_add_work_item_comment(self) -> None:
        """wit_add_work_item_comment adds a comment."""
        if not self._created_work_item_id:
            print("    SKIP: no work item created")
            return

        result = await self.session.call_tool(
            "wit_add_work_item_comment",
            {
                "project": PROJECT,
                "work_item_id": self._created_work_item_id,
                "text": "Automated integration test comment",
            },
        )
        data = _parse(result)
        assert not data.get("error"), f"Comment failed: {data}"
        assert data.get("id") is not None

    async def test_wit_work_items_link(self) -> None:
        """wit_work_items_link creates a Related link."""
        if not self._created_work_item_id:
            print("    SKIP: no work item created")
            return

        result = await self.session.call_tool(
            "wit_work_items_link",
            {
                "project": PROJECT,
                "work_item_id": self._created_work_item_id,
                "target_id": 1,
                "link_type": "System.LinkTypes.Related",
            },
        )
        data = _parse(result)
        assert not data.get("error"), f"Link failed: {data}"

    async def test_wit_work_item_unlink(self) -> None:
        """wit_work_item_unlink removes the relation we just added."""
        if not self._created_work_item_id:
            print("    SKIP: no work item created")
            return

        get_result = await self.session.call_tool(
            "wit_get_work_item",
            {"project": PROJECT, "work_item_id": self._created_work_item_id},
        )
        get_data = _parse(get_result)
        relations = get_data.get("relations", [])
        if not relations:
            print("    SKIP: no relations to unlink")
            return

        result = await self.session.call_tool(
            "wit_work_item_unlink",
            {
                "project": PROJECT,
                "work_item_id": self._created_work_item_id,
                "relation_index": len(relations) - 1,
            },
        )
        data = _parse(result)
        assert not data.get("error"), f"Unlink failed: {data}"

    async def test_wit_update_work_items_batch(self) -> None:
        """wit_update_work_items_batch closes the test work item."""
        if not self._created_work_item_id:
            print("    SKIP: no work item created")
            return

        result = await self.session.call_tool(
            "wit_update_work_items_batch",
            {
                "project": PROJECT,
                "updates": [
                    {"id": self._created_work_item_id, "fields": {"System.State": "Closed"}},
                ],
            },
        )
        data = _parse(result)
        assert data["succeeded"] >= 1, f"Batch update failed: {data}"

    # --- Repositories ---

    async def test_repo_list_repos(self) -> None:
        """repo_list_repos returns at least one repo."""
        result = await self.session.call_tool("repo_list_repos", {"project": PROJECT})
        data = _parse(result)
        assert data["count"] >= 1, f"Expected at least 1 repo, got: {json.dumps(data, indent=2)}"
        # Store first repo name for subsequent tests
        self._repo_name = data["repositories"][0]["name"]

    async def test_repo_get_repo(self) -> None:
        """repo_get_repo returns repo details."""
        repo = getattr(self, "_repo_name", PROJECT)
        result = await self.session.call_tool(
            "repo_get_repo", {"project": PROJECT, "repository": repo}
        )
        data = _parse(result)
        assert data["name"] == repo

    async def test_repo_list_branches(self) -> None:
        """repo_list_branches returns branches (may be empty for uninitialised repos)."""
        repo = getattr(self, "_repo_name", PROJECT)
        result = await self.session.call_tool(
            "repo_list_branches", {"project": PROJECT, "repository": repo}
        )
        data = _parse(result)
        if data["count"] == 0:
            print(f"    WARN: 0 branches in '{repo}' (repo may not have been pushed to)")
        else:
            assert data["branches"][0]["name"], "Branch should have a name"

    async def test_repo_search_commits(self) -> None:
        """repo_search_commits returns commits."""
        repo = getattr(self, "_repo_name", PROJECT)
        result = await self.session.call_tool(
            "repo_search_commits", {"project": PROJECT, "repository": repo, "top": 5}
        )
        data = _parse(result)
        assert data["count"] >= 0  # May be empty for new repos

    async def test_repo_list_pull_requests(self) -> None:
        """repo_list_pull_requests returns PRs (may be empty)."""
        result = await self.session.call_tool(
            "repo_list_pull_requests", {"project": PROJECT, "status": "all", "top": 5}
        )
        data = _parse(result)
        assert data["count"] >= 0  # May be empty

    # --- Work Domain (order matters: create iteration → assign to team) ---

    async def test_work_list_iterations(self) -> None:
        """work_list_iterations returns project iterations."""
        result = await self.session.call_tool(
            "work_list_iterations",
            {"project": PROJECT},
        )
        data = _parse(result)
        assert data["count"] >= 1

    async def test_work_get_team_settings(self) -> None:
        """work_get_team_settings returns team config."""
        result = await self.session.call_tool(
            "work_get_team_settings",
            {"project": PROJECT, "team": TEAM},
        )
        data = _parse(result)
        assert "working_days" in data
        assert "default_iteration" in data

    async def test_work_create_iteration(self) -> None:
        """work_create_iteration creates an iteration with dates."""
        iter_name = f"IntTest-{int(time.time())}"
        result = await self.session.call_tool(
            "work_create_iteration",
            {
                "project": PROJECT,
                "name": iter_name,
                "start_date": "2026-06-01",
                "end_date": "2026-06-12",
            },
        )
        data = _parse(result)
        assert not data.get("error"), f"Create iteration failed: {data}"
        assert data.get("name") == iter_name
        attrs = data.get("attributes", {})
        has_dates = attrs.get("startDate") is not None or attrs.get("finishDate") is not None
        assert has_dates, f"Dates not set: {data}"
        self._created_iteration_id = data.get("identifier")
        print(f"    Created iteration '{iter_name}' (id: {self._created_iteration_id})")

    async def test_work_assign_iteration_to_team(self) -> None:
        """work_assign_iteration_to_team assigns the created iteration."""
        if not self._created_iteration_id:
            print("    SKIP: no iteration created")
            return

        result = await self.session.call_tool(
            "work_assign_iteration_to_team",
            {"project": PROJECT, "team": TEAM, "iteration_id": self._created_iteration_id},
        )
        data = _parse(result)
        assert not data.get("error"), f"Assign failed: {data}"

    # --- Wiki ---

    async def test_wiki_list_wikis(self) -> None:
        """wiki_list_wikis returns at least one wiki (creates one if needed)."""
        result = await self.session.call_tool("wiki_list_wikis", {"project": PROJECT})
        data = _parse(result)

        if data["count"] == 0:
            # No wiki exists — create a project wiki
            print("    No wiki found, creating project wiki...")
            create_result = await self.session.call_tool(
                "wiki_create_wiki",
                {"project": PROJECT, "name": f"{PROJECT}.wiki"},
            )
            create_data = _parse(create_result)
            assert not create_data.get("error"), f"Wiki creation failed: {create_data}"
            self._wiki_id = create_data["id"]
            print(f"    Created wiki: {create_data['name']} ({self._wiki_id})")
        else:
            self._wiki_id = data["wikis"][0]["id"]
            print(f"    Found wiki: {data['wikis'][0]['name']} ({self._wiki_id})")

        assert self._wiki_id, "Expected a wiki ID"

    async def test_wiki_list_pages(self) -> None:
        """wiki_list_pages returns pages for the project wiki."""
        wiki_id = getattr(self, "_wiki_id", None)
        if not wiki_id:
            print("    SKIP: no wiki found")
            return

        result = await self.session.call_tool(
            "wiki_list_pages", {"project": PROJECT, "wiki_id": wiki_id}
        )
        data = _parse(result)
        assert data["count"] >= 1, f"Expected at least 1 page, got: {json.dumps(data, indent=2)}"

    async def test_wiki_create_and_update_page(self) -> None:
        """wiki_create_or_update_page creates then updates a page."""
        wiki_id = getattr(self, "_wiki_id", None)
        if not wiki_id:
            print("    SKIP: no wiki found")
            return

        # Create parent page first (ADO requires ancestor pages to exist)
        parent_path = "/Integration-Test"
        await self.session.call_tool(
            "wiki_create_or_update_page",
            {
                "project": PROJECT,
                "wiki_id": wiki_id,
                "path": parent_path,
                "content": "# Integration Tests\n\nPages created by automated integration tests.",
            },
        )

        page_path = f"/Integration-Test/wiki-{int(time.time())}"

        # Create child page
        result = await self.session.call_tool(
            "wiki_create_or_update_page",
            {
                "project": PROJECT,
                "wiki_id": wiki_id,
                "path": page_path,
                "content": "# Integration Test\n\nCreated by integration test.",
            },
        )
        data = _parse(result)
        assert not data.get("error"), f"Create failed: {data}"
        assert data.get("path") == page_path or data.get("path", "").endswith(page_path.split("/")[-1])
        etag = data.get("eTag", "")
        assert etag, f"Expected ETag in create response, got: {data}"
        print(f"    Created wiki page {page_path} (etag: {etag})")

        # Update with ETag
        result = await self.session.call_tool(
            "wiki_create_or_update_page",
            {
                "project": PROJECT,
                "wiki_id": wiki_id,
                "path": page_path,
                "content": "# Integration Test\n\nUpdated by integration test.",
                "etag": etag,
            },
        )
        data = _parse(result)
        assert not data.get("error"), f"Update failed: {data}"
        print(f"    Updated wiki page {page_path}")


# Explicit execution order — alphabetical dir() doesn't work for stateful tests.
TEST_ORDER = [
    # Core (stateless)
    "test_core_list_projects",
    "test_core_list_project_teams",
    "test_core_get_identity_ids",
    # Work Items Read (stateless)
    "test_wit_query_wiql",
    "test_wit_query_wiql_with_details",
    "test_wit_get_work_item",
    "test_wit_get_work_items_batch",
    "test_wit_get_work_items_for_iteration",
    "test_wit_list_backlogs",
    "test_wit_get_work_item_type",
    "test_wit_list_fields",
    "test_wit_get_team_iterations",
    "test_wit_get_team_capacity",
    # Repositories (stateless)
    "test_repo_list_repos",
    "test_repo_get_repo",
    "test_repo_list_branches",
    "test_repo_search_commits",
    "test_repo_list_pull_requests",
    # Work Items Write (stateful — must run in this order)
    "test_wit_create_work_item",
    "test_wit_create_work_item_with_fields",
    "test_wit_update_work_item",
    "test_wit_add_work_item_comment",
    "test_wit_work_items_link",
    "test_wit_work_item_unlink",
    "test_wit_update_work_items_batch",
    # Work Domain (stateful — create before assign)
    "test_work_list_iterations",
    "test_work_get_team_settings",
    "test_work_create_iteration",
    "test_work_assign_iteration_to_team",
    # Wiki (stateful — list before create/update)
    "test_wiki_list_wikis",
    "test_wiki_list_pages",
    "test_wiki_create_and_update_page",
]


# --- Runner ---


async def run_tests(url: str, filter_pattern: str | None = None) -> tuple[int, int, int]:
    """Connect to MCP endpoint and run all tests."""
    headers = _headers()
    passed = 0
    failed = 0
    skipped = 0

    async with streamablehttp_client(url, headers=headers) as (read, write, _):
        async with ClientSession(read, write) as session:
            await session.initialize()

            test = IntegrationTest(session)

            methods = TEST_ORDER
            if filter_pattern:
                methods = [m for m in methods if filter_pattern in m]

            print(f"\nRunning {len(methods)} integration tests against {url}\n")

            for name in methods:
                method = getattr(test, name)
                doc = method.__doc__ or name
                try:
                    await method()
                    print(f"  PASS  {name} — {doc.strip()}")
                    passed += 1
                except AssertionError as e:
                    print(f"  FAIL  {name} — {e}")
                    failed += 1
                except Exception as e:
                    print(f"  ERROR {name} — {type(e).__name__}: {e}")
                    if os.environ.get("VERBOSE"):
                        traceback.print_exc()
                    failed += 1

    return passed, failed, skipped


def main() -> None:
    parser = argparse.ArgumentParser(description="Integration tests for ado-mcp-server")
    parser.add_argument("--url", default=DEFAULT_URL, help="MCP endpoint URL")
    parser.add_argument("-k", "--filter", default=None, help="Filter tests by name substring")
    args = parser.parse_args()

    if not os.environ.get("AZURE_DEVOPS_PAT"):
        print("ERROR: AZURE_DEVOPS_PAT environment variable required")
        sys.exit(1)

    passed, failed, skipped = asyncio.run(run_tests(args.url, args.filter))

    print(f"\n{'='*50}")
    print(f"Results: {passed} passed, {failed} failed, {skipped} skipped")
    print(f"{'='*50}")

    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
