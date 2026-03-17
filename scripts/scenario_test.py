"""Scenario tests — chained MCP tool calls that simulate LLM agent workflows.

Each scenario is a multi-step prompt chain where the output of one tool feeds
the input of the next, just like an LLM agent would reason through a task.
Full tool outputs are saved to test-outputs/ as timestamped JSON for inspection.

Every one of the 44 MCP tools is exercised by at least one scenario.

Usage:
    export AZURE_DEVOPS_PAT=xxx
    python scripts/scenario_test.py --url https://...mcp

    # Run a single scenario
    python scripts/scenario_test.py -k epic_hierarchy

    # Verbose — print full tool outputs to console
    VERBOSE=1 python scripts/scenario_test.py
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import time
import traceback
from datetime import datetime, timezone
from pathlib import Path

from mcp.client.session import ClientSession
from mcp.client.streamable_http import streamablehttp_client

# --- Config ---

DEFAULT_URL = os.environ.get("MCP_SERVER_URL", "http://localhost:3000/mcp")
PROJECT = "Cast"
TEAM = "Cast Team"
VERBOSE = bool(os.environ.get("VERBOSE"))
OUTPUT_DIR = Path(__file__).parent.parent / "test-outputs"


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


class Step:
    """Records a single tool call within a scenario."""

    def __init__(self, tool: str, args: dict, reasoning: str) -> None:
        self.tool = tool
        self.args = args
        self.reasoning = reasoning
        self.result: dict | list | None = None
        self.duration_ms: float = 0
        self.error: str | None = None

    def to_dict(self) -> dict:
        return {
            "tool": self.tool,
            "args": self.args,
            "reasoning": self.reasoning,
            "result": self.result,
            "duration_ms": round(self.duration_ms, 1),
            "error": self.error,
        }


class ScenarioRunner:
    """Runs scenario chains against a live MCP endpoint."""

    def __init__(self, session: ClientSession) -> None:
        self.session = session
        self.steps: list[Step] = []

    async def call(self, tool: str, args: dict, reasoning: str = "") -> dict | list:
        """Call an MCP tool, record it as a step, and return parsed result."""
        step = Step(tool, args, reasoning)
        t0 = time.monotonic()
        try:
            raw = await self.session.call_tool(tool, args)
            step.duration_ms = (time.monotonic() - t0) * 1000
            data = _parse(raw)
            step.result = data
            if VERBOSE:
                print(f"      [{tool}] {json.dumps(data, indent=2)[:500]}")
            return data
        except Exception as e:
            step.duration_ms = (time.monotonic() - t0) * 1000
            step.error = f"{type(e).__name__}: {e}"
            raise
        finally:
            self.steps.append(step)

    def reset(self) -> None:
        self.steps = []


# =============================================================================
# Scenario definitions
#
# Each scenario is an async function that takes a ScenarioRunner, chains
# multiple tool calls, and asserts on the results. The function docstring
# becomes the scenario description. The function name becomes the scenario ID.
#
# Tools covered by each scenario are listed in a TOOLS comment.
# =============================================================================


async def epic_hierarchy_drilldown(r: ScenarioRunner) -> None:
    """Drill down the full Epic → Feature → Story → Task hierarchy.

    Simulates: "Show me all epics, pick the first one, list its features,
    then list stories under the first feature, then tasks under the first story."
    """
    # TOOLS: wit_query_wiql, wit_get_work_item, wit_get_work_items_batch

    # Step 1: Find all Epics
    epics = await r.call(
        "wit_query_wiql",
        {"project": PROJECT, "wiql": "SELECT [System.Id] FROM WorkItems WHERE [System.WorkItemType] = 'Epic' ORDER BY [System.Id] ASC", "fetch_details": True},
        "Find all Epics in the project",
    )
    assert epics["count"] >= 1, "Expected at least 1 Epic"
    epic_id = epics["work_items"][0]["id"]
    print(f"    Found {epics['count']} Epics, drilling into Epic #{epic_id}: {epics['work_items'][0].get('title', '?')}")

    # Step 2: Get Epic with relations to find child Features
    epic_detail = await r.call(
        "wit_get_work_item",
        {"project": PROJECT, "work_item_id": epic_id},
        f"Get Epic #{epic_id} with relations to find child Features",
    )
    child_ids = [
        rel["target_id"]
        for rel in epic_detail.get("relations", [])
        if rel.get("type") == "Child" and rel.get("target_id")
    ]
    assert len(child_ids) >= 1, f"Epic #{epic_id} has no child Features"
    print(f"    Epic #{epic_id} has {len(child_ids)} children: {child_ids}")

    # Step 3: Batch-fetch the child Features
    features = await r.call(
        "wit_get_work_items_batch",
        {"project": PROJECT, "ids": ",".join(str(i) for i in child_ids[:10])},
        "Batch-fetch child Features",
    )
    assert features["count"] >= 1
    feature_types = [wi.get("type") for wi in features["work_items"]]
    print(f"    Fetched {features['count']} children, types: {feature_types}")

    # Step 4: Pick first Feature, get its children (Stories)
    feature_id = child_ids[0]
    feature_detail = await r.call(
        "wit_get_work_item",
        {"project": PROJECT, "work_item_id": feature_id},
        f"Get Feature #{feature_id} to find child Stories",
    )
    story_ids = [
        rel["target_id"]
        for rel in feature_detail.get("relations", [])
        if rel.get("type") == "Child" and rel.get("target_id")
    ]
    print(f"    Feature #{feature_id} has {len(story_ids)} child Stories")

    # Step 5: If stories exist, drill into the first one for Tasks
    if story_ids:
        story_detail = await r.call(
            "wit_get_work_item",
            {"project": PROJECT, "work_item_id": story_ids[0]},
            f"Get Story #{story_ids[0]} to find child Tasks",
        )
        task_ids = [
            rel["target_id"]
            for rel in story_detail.get("relations", [])
            if rel.get("type") == "Child" and rel.get("target_id")
        ]
        print(f"    Story #{story_ids[0]} has {len(task_ids)} child Tasks")


async def sprint_capacity_analysis(r: ScenarioRunner) -> None:
    """Analyse a sprint's capacity, work items, and team settings.

    Simulates: "What's the current sprint? Show me team settings, capacity,
    and all work items assigned to it."
    """
    # TOOLS: wit_get_team_iterations, wit_get_team_capacity, wit_get_work_items_for_iteration,
    #        work_get_team_settings, work_list_iterations

    # Step 1: Get team settings
    settings = await r.call(
        "work_get_team_settings",
        {"project": PROJECT, "team": TEAM},
        "Get team settings (working days, default iteration, backlog visibility)",
    )
    assert "working_days" in settings
    print(f"    Working days: {settings['working_days']}")

    # Step 2: List all project iterations
    all_iters = await r.call(
        "work_list_iterations",
        {"project": PROJECT},
        "List all project iterations to understand sprint structure",
    )
    assert all_iters["count"] >= 1
    print(f"    Project has {all_iters['count']} iterations total")

    # Step 3: Get current team iteration
    current = await r.call(
        "wit_get_team_iterations",
        {"project": PROJECT, "team": TEAM, "timeframe": "current"},
        "Get the current sprint iteration for the team",
    )
    if not current["iterations"]:
        print("    No current sprint — skipping capacity and work item checks")
        return

    sprint = current["iterations"][0]
    sprint_name = sprint["name"]
    sprint_id = sprint["id"]
    print(f"    Current sprint: {sprint_name} (id: {sprint_id})")

    # Step 4: Get capacity for current sprint
    capacity = await r.call(
        "wit_get_team_capacity",
        {"project": PROJECT, "team": TEAM, "iteration_id": sprint_id},
        f"Get team capacity for sprint '{sprint_name}'",
    )
    assert "working_days" in capacity
    print(f"    Capacity — {capacity.get('total_capacity_hours', 0)}h across {len(capacity.get('members', []))} members, {capacity['working_days']} working days")

    # Step 5: Get work items in the sprint
    sprint_items = await r.call(
        "wit_get_work_items_for_iteration",
        {"project": PROJECT, "iteration_path": sprint["path"]},
        f"Get all work items assigned to sprint '{sprint_name}'",
    )
    print(f"    Sprint has {sprint_items['count']} work items")


async def backlog_exploration(r: ScenarioRunner) -> None:
    """Explore backlog levels and their work items.

    Simulates: "List the backlog levels, then show me work items in each one."
    """
    # TOOLS: wit_list_backlogs, wit_list_backlog_work_items

    # Step 1: List all backlog levels
    backlogs = await r.call(
        "wit_list_backlogs",
        {"project": PROJECT, "team": TEAM},
        "List all backlog levels for the team",
    )
    assert backlogs["count"] >= 1
    print(f"    Found {backlogs['count']} backlog levels: {[b['name'] for b in backlogs['backlogs']]}")

    # Step 2: For each backlog level, list its work items
    for bl in backlogs["backlogs"][:3]:
        items = await r.call(
            "wit_list_backlog_work_items",
            {"project": PROJECT, "team": TEAM, "backlog_id": bl["id"]},
            f"List work items in '{bl['name']}' backlog",
        )
        count = items.get("count", 0)
        print(f"    {bl['name']}: {count} items")


async def my_work_items_review(r: ScenarioRunner) -> None:
    """Review work items assigned to the current user.

    Simulates: "What's assigned to me? Show details of the first one."
    """
    # TOOLS: wit_my_work_items, wit_get_work_item

    # Step 1: Get my work items
    my_items = await r.call(
        "wit_my_work_items",
        {},
        "Get all work items assigned to me that are not Closed",
    )
    count = my_items.get("count", 0)
    print(f"    Found {count} items assigned to current user")

    # Step 2: If any items, get full details of the first
    if count > 0 and my_items.get("work_items"):
        first_id = my_items["work_items"][0]["id"]
        detail = await r.call(
            "wit_get_work_item",
            {"project": PROJECT, "work_item_id": first_id},
            f"Get full details of my work item #{first_id}",
        )
        print(f"    First item: #{detail['id']} '{detail.get('title', '?')}' ({detail.get('state', '?')})")


async def work_item_type_and_fields(r: ScenarioRunner) -> None:
    """Explore work item type definitions and available fields.

    Simulates: "What fields does a User Story have? What states can it be in?"
    """
    # TOOLS: wit_get_work_item_type, wit_list_fields

    # Step 1: Get all fields
    fields = await r.call(
        "wit_list_fields",
        {"project": PROJECT},
        "List all available work item fields",
    )
    assert fields["count"] >= 10
    print(f"    {fields['count']} fields available")

    # Step 2: Get User Story type definition
    us_type = await r.call(
        "wit_get_work_item_type",
        {"project": PROJECT, "type_name": "User Story"},
        "Get User Story type definition (states, transitions)",
    )
    assert us_type["name"] == "User Story"
    states = [s["name"] for s in us_type.get("states", [])]
    print(f"    User Story states: {states}")

    # Step 3: Get Task type definition
    task_type = await r.call(
        "wit_get_work_item_type",
        {"project": PROJECT, "type_name": "Task"},
        "Get Task type definition for comparison",
    )
    task_states = [s["name"] for s in task_type.get("states", [])]
    print(f"    Task states: {task_states}")


async def work_item_lifecycle(r: ScenarioRunner) -> None:
    """Full work item lifecycle: create parent, add children, link, comment, close.

    Simulates: "Create a User Story, add two child Tasks, link the story to
    an Epic, add a comment, then close everything."
    """
    # TOOLS: wit_create_work_item, wit_add_child_work_items, wit_update_work_item,
    #        wit_add_work_item_comment, wit_work_items_link, wit_work_item_unlink,
    #        wit_update_work_items_batch, wit_get_work_item

    ts = int(time.time())

    # Step 1: Create a User Story
    story = await r.call(
        "wit_create_work_item",
        {"project": PROJECT, "type_name": "User Story", "title": f"Scenario test story {ts}"},
        "Create a new User Story for testing the lifecycle",
    )
    story_id = story["id"]
    assert story_id, f"Failed to create story: {story}"
    print(f"    Created User Story #{story_id}")

    # Step 2: Create two child Tasks
    task1 = await r.call(
        "wit_create_work_item",
        {"project": PROJECT, "type_name": "Task", "title": f"Scenario child task 1 {ts}"},
        "Create first child Task",
    )
    task1_id = task1["id"]
    print(f"    Created Task #{task1_id}")

    task2 = await r.call(
        "wit_create_work_item",
        {"project": PROJECT, "type_name": "Task", "title": f"Scenario child task 2 {ts}"},
        "Create second child Task",
    )
    task2_id = task2["id"]
    print(f"    Created Task #{task2_id}")

    # Step 3: Add both Tasks as children of the Story
    add_children = await r.call(
        "wit_add_child_work_items",
        {"project": PROJECT, "parent_id": story_id, "child_ids": f"{task1_id},{task2_id}"},
        f"Link Tasks #{task1_id} and #{task2_id} as children of Story #{story_id}",
    )
    assert not add_children.get("error"), f"Add children failed: {add_children}"
    print(f"    Added children to Story #{story_id}")

    # Step 4: Link Story to Epic #1 (Related)
    link = await r.call(
        "wit_work_items_link",
        {"project": PROJECT, "work_item_id": story_id, "target_id": 1, "link_type": "System.LinkTypes.Related"},
        f"Create Related link between Story #{story_id} and Epic #1",
    )
    assert not link.get("error"), f"Link failed: {link}"

    # Step 5: Add a comment
    comment = await r.call(
        "wit_add_work_item_comment",
        {"project": PROJECT, "work_item_id": story_id, "text": f"Scenario test comment — lifecycle test at {ts}"},
        f"Add a comment to Story #{story_id}",
    )
    assert comment.get("id"), f"Comment failed: {comment}"

    # Step 6: Verify relations are present
    detail = await r.call(
        "wit_get_work_item",
        {"project": PROJECT, "work_item_id": story_id},
        f"Verify Story #{story_id} has the expected relations",
    )
    rel_types = [rel.get("type") for rel in detail.get("relations", [])]
    assert "Child" in rel_types, f"Expected Child relation, got: {rel_types}"
    print(f"    Story #{story_id} relations: {rel_types}")

    # Step 7: Unlink the Related relation
    relations = detail.get("relations", [])
    related_idx = next((i for i, rel in enumerate(relations) if rel.get("type") == "Related"), None)
    if related_idx is not None:
        await r.call(
            "wit_work_item_unlink",
            {"project": PROJECT, "work_item_id": story_id, "relation_index": related_idx},
            f"Remove Related link from Story #{story_id}",
        )

    # Step 8: Activate then close all items via batch
    await r.call(
        "wit_update_work_item",
        {"project": PROJECT, "work_item_id": story_id, "fields": {"System.State": "Active"}},
        f"Activate Story #{story_id}",
    )
    batch_close = await r.call(
        "wit_update_work_items_batch",
        {
            "project": PROJECT,
            "updates": [
                {"id": task1_id, "fields": {"System.State": "Closed"}},
                {"id": task2_id, "fields": {"System.State": "Closed"}},
                {"id": story_id, "fields": {"System.State": "Closed"}},
            ],
        },
        "Batch-close all test work items",
    )
    assert batch_close.get("succeeded", 0) >= 3, f"Batch close failed: {batch_close}"
    print(f"    Closed {batch_close['succeeded']} work items")


async def sprint_setup_workflow(r: ScenarioRunner) -> None:
    """Set up a new sprint: create iteration, assign to team, set capacity.

    Simulates: "Create a new sprint, assign it to the team, set capacity
    for a team member."
    """
    # TOOLS: work_create_iteration, work_assign_iteration_to_team, work_update_team_capacity,
    #        core_get_identity_ids, core_list_project_teams

    ts = int(time.time())

    # Step 1: List teams to confirm our team exists
    teams = await r.call(
        "core_list_project_teams",
        {"project": PROJECT},
        "List teams to verify our target team",
    )
    assert any(t["name"] == TEAM for t in teams["teams"])
    print(f"    Confirmed team '{TEAM}' exists")

    # Step 2: Search for an identity to use for capacity
    identities = await r.call(
        "core_get_identity_ids",
        {"searchFilter": "Sam"},
        "Search for a team member identity for capacity assignment",
    )
    member_id = None
    if identities.get("count", 0) > 0:
        member_id = identities["identities"][0]["id"]
        print(f"    Found identity: {identities['identities'][0].get('displayName', '?')} ({member_id})")
    else:
        print("    WARN: No identities found — will skip capacity update")

    # Step 3: Create a new sprint
    iter_name = f"ScenarioTest-{ts}"
    iteration = await r.call(
        "work_create_iteration",
        {"project": PROJECT, "name": iter_name, "start_date": "2026-07-01", "end_date": "2026-07-14"},
        f"Create new sprint iteration '{iter_name}'",
    )
    assert not iteration.get("error"), f"Create iteration failed: {iteration}"
    iter_id = iteration["identifier"]
    print(f"    Created iteration '{iter_name}' (id: {iter_id})")

    # Step 4: Assign iteration to team
    assign = await r.call(
        "work_assign_iteration_to_team",
        {"project": PROJECT, "team": TEAM, "iteration_id": iter_id},
        f"Assign iteration '{iter_name}' to team '{TEAM}'",
    )
    assert not assign.get("error"), f"Assign failed: {assign}"
    print(f"    Assigned to team '{TEAM}'")

    # Step 5: Set capacity for the member (if found)
    if member_id:
        cap = await r.call(
            "work_update_team_capacity",
            {
                "project": PROJECT,
                "team": TEAM,
                "iteration_id": iter_id,
                "member_id": member_id,
                "activities": [{"name": "Development", "capacityPerDay": 6}],
                "days_off": [{"start": "2026-07-07", "end": "2026-07-07"}],
            },
            f"Set capacity for member {member_id}: 6h/day Development, 1 day off",
        )
        assert not cap.get("error"), f"Capacity update failed: {cap}"
        print(f"    Capacity set for iteration '{iter_name}'")


async def repo_exploration(r: ScenarioRunner) -> None:
    """Explore repositories, branches, and commit history.

    Simulates: "List repos, inspect the main one, check branches and recent commits."
    """
    # TOOLS: repo_list_repos, repo_get_repo, repo_list_branches, repo_search_commits,
    #        core_list_projects

    # Step 1: Confirm project
    projects = await r.call(
        "core_list_projects",
        {},
        "List projects to pick the target",
    )
    assert any(p["name"] == PROJECT for p in projects["projects"])

    # Step 2: List repos
    repos = await r.call(
        "repo_list_repos",
        {"project": PROJECT},
        "List all Git repositories in the project",
    )
    assert repos["count"] >= 1
    repo_name = repos["repositories"][0]["name"]
    print(f"    Found {repos['count']} repos, inspecting '{repo_name}'")

    # Step 3: Get repo details
    repo = await r.call(
        "repo_get_repo",
        {"project": PROJECT, "repository": repo_name},
        f"Get detailed info for repo '{repo_name}'",
    )
    print(f"    Repo '{repo['name']}': default branch={repo.get('default_branch', 'none')}, size={repo.get('size', '?')}")

    # Step 4: List branches
    branches = await r.call(
        "repo_list_branches",
        {"project": PROJECT, "repository": repo_name},
        f"List branches in '{repo_name}'",
    )
    branch_names = [b["name"] for b in branches.get("branches", [])]
    print(f"    Branches: {branch_names or '(none)'}")

    # Step 5: Search recent commits
    commits = await r.call(
        "repo_search_commits",
        {"project": PROJECT, "repository": repo_name, "top": 5},
        "Get 5 most recent commits",
    )
    print(f"    Recent commits: {commits['count']}")
    for c in commits.get("commits", [])[:3]:
        print(f"      {c.get('commit_id', '?')[:8]} — {c.get('comment', '?')[:60]}")


async def pr_lifecycle(r: ScenarioRunner) -> None:
    """Full pull request lifecycle: branch → PR → comment → thread → reply → abandon.

    Simulates: "Create a feature branch, open a PR, add review comments,
    then abandon it."
    """
    # TOOLS: repo_create_branch, repo_create_pull_request, repo_get_pull_request,
    #        repo_list_pull_requests, repo_update_pull_request, repo_list_pr_threads,
    #        repo_create_pr_thread, repo_reply_to_comment

    ts = int(time.time())
    repo_name = PROJECT

    # Step 1: List branches to find one to branch from
    branches = await r.call(
        "repo_list_branches",
        {"project": PROJECT, "repository": repo_name},
        "List branches to find a source branch",
    )
    if not branches.get("branches"):
        print("    SKIP: No branches in repo — can't create PR lifecycle")
        return

    source = branches["branches"][0]["name"]
    new_branch = f"scenario-test/{ts}"

    # Step 2: Create feature branch
    branch = await r.call(
        "repo_create_branch",
        {"project": PROJECT, "repository": repo_name, "name": new_branch, "source_branch": source},
        f"Create feature branch '{new_branch}' from '{source}'",
    )
    assert not branch.get("error"), f"Create branch failed: {branch}"
    print(f"    Created branch '{new_branch}'")

    # Step 3: Create pull request
    pr = await r.call(
        "repo_create_pull_request",
        {
            "project": PROJECT,
            "repository": repo_name,
            "source_ref": new_branch,
            "target_ref": source,
            "title": f"Scenario test PR {ts}",
            "description": "Automated scenario test — will be abandoned.",
            "is_draft": True,
        },
        f"Create draft PR from '{new_branch}' → '{source}'",
    )
    assert not pr.get("error"), f"Create PR failed: {pr}"
    pr_id = pr["pull_request_id"]
    print(f"    Created PR #{pr_id}")

    # Step 4: Get PR details
    pr_detail = await r.call(
        "repo_get_pull_request",
        {"project": PROJECT, "repository": repo_name, "pull_request_id": pr_id},
        f"Get full details of PR #{pr_id}",
    )
    assert pr_detail["pull_request_id"] == pr_id
    print(f"    PR #{pr_id}: '{pr_detail.get('title', '?')}' status={pr_detail.get('status', '?')}")

    # Step 5: List PRs to confirm ours appears
    prs = await r.call(
        "repo_list_pull_requests",
        {"project": PROJECT, "repository": repo_name, "status": "all", "top": 10},
        "List recent PRs to verify ours appears",
    )
    pr_ids = [p.get("pull_request_id") for p in prs.get("pull_requests", [])]
    assert pr_id in pr_ids, f"PR #{pr_id} not found in list: {pr_ids}"

    # Step 6: Create a comment thread on the PR
    thread = await r.call(
        "repo_create_pr_thread",
        {
            "project": PROJECT,
            "repository": repo_name,
            "pull_request_id": pr_id,
            "content": "Scenario test: general review comment.",
            "status": "active",
        },
        f"Create general review thread on PR #{pr_id}",
    )
    assert not thread.get("error"), f"Create thread failed: {thread}"
    thread_id = thread.get("id")
    print(f"    Created thread #{thread_id}")

    # Step 7: Reply to the thread
    reply = await r.call(
        "repo_reply_to_comment",
        {
            "project": PROJECT,
            "repository": repo_name,
            "pull_request_id": pr_id,
            "thread_id": thread_id,
            "content": "Scenario test: this is a reply to the thread.",
        },
        f"Reply to thread #{thread_id} on PR #{pr_id}",
    )
    assert not reply.get("error"), f"Reply failed: {reply}"
    print(f"    Replied to thread #{thread_id}")

    # Step 8: List PR threads to verify
    threads = await r.call(
        "repo_list_pr_threads",
        {"project": PROJECT, "repository": repo_name, "pull_request_id": pr_id},
        f"List all threads on PR #{pr_id}",
    )
    assert threads.get("count", 0) >= 1
    print(f"    PR #{pr_id} has {threads['count']} threads")

    # Step 9: Abandon the PR (cleanup)
    abandon = await r.call(
        "repo_update_pull_request",
        {"project": PROJECT, "repository": repo_name, "pull_request_id": pr_id, "status": "abandoned"},
        f"Abandon PR #{pr_id} (cleanup)",
    )
    assert not abandon.get("error"), f"Abandon failed: {abandon}"
    print(f"    Abandoned PR #{pr_id}")


async def wiki_documentation_workflow(r: ScenarioRunner) -> None:
    """Create and manage wiki documentation pages.

    Simulates: "List wikis, inspect one, create a page, read it back, update it."
    """
    # TOOLS: wiki_list_wikis, wiki_get_wiki, wiki_list_pages, wiki_create_or_update_page,
    #        wiki_get_page_content

    ts = int(time.time())

    # Step 1: List wikis
    wikis = await r.call(
        "wiki_list_wikis",
        {"project": PROJECT},
        "List all wikis in the project",
    )
    assert wikis["count"] >= 1, "No wikis found"
    wiki_id = wikis["wikis"][0]["id"]
    wiki_name = wikis["wikis"][0]["name"]
    print(f"    Found wiki: {wiki_name} ({wiki_id})")

    # Step 2: Get wiki details
    wiki = await r.call(
        "wiki_get_wiki",
        {"project": PROJECT, "wiki_id": wiki_id},
        f"Get detailed info for wiki '{wiki_name}'",
    )
    assert wiki.get("name") or wiki.get("id"), f"Wiki detail empty: {wiki}"
    print(f"    Wiki type: {wiki.get('type', '?')}")

    # Step 3: List pages
    pages = await r.call(
        "wiki_list_pages",
        {"project": PROJECT, "wiki_id": wiki_id},
        "List all pages in the wiki",
    )
    print(f"    Wiki has {pages['count']} pages")

    # Step 4: Create a test page
    page_path = f"/Scenario-Test/page-{ts}"
    # Ensure parent
    await r.call(
        "wiki_create_or_update_page",
        {"project": PROJECT, "wiki_id": wiki_id, "path": "/Scenario-Test", "content": "# Scenario Tests\n\nAutomated test pages."},
        "Ensure parent page exists",
    )
    create = await r.call(
        "wiki_create_or_update_page",
        {"project": PROJECT, "wiki_id": wiki_id, "path": page_path, "content": f"# Scenario Test Page\n\nCreated at {ts}."},
        f"Create wiki page at {page_path}",
    )
    assert not create.get("error"), f"Create page failed: {create}"
    etag = create.get("eTag", "")
    print(f"    Created page {page_path} (etag: {etag})")

    # Step 5: Read the page content back
    content = await r.call(
        "wiki_get_page_content",
        {"project": PROJECT, "wiki_id": wiki_id, "path": page_path},
        f"Read back wiki page content at {page_path}",
    )
    assert "Scenario Test Page" in content.get("content", ""), f"Content mismatch: {content}"
    print(f"    Read back: {len(content.get('content', ''))} chars")

    # Step 6: Update the page
    update = await r.call(
        "wiki_create_or_update_page",
        {
            "project": PROJECT,
            "wiki_id": wiki_id,
            "path": page_path,
            "content": f"# Scenario Test Page\n\nCreated at {ts}.\n\nUpdated by scenario test.",
            "etag": etag,
        },
        f"Update wiki page at {page_path}",
    )
    assert not update.get("error"), f"Update page failed: {update}"
    print(f"    Updated page {page_path}")


async def cross_domain_project_overview(r: ScenarioRunner) -> None:
    """Cross-domain scenario: project overview pulling from all domains.

    Simulates: "Give me a complete overview of this project — teams, repos,
    sprints, wiki, and work item stats."
    """
    # TOOLS: core_list_projects, core_list_project_teams, repo_list_repos,
    #        wit_query_wiql, wit_get_team_iterations, wiki_list_wikis, wiki_list_pages

    # Step 1: Project info
    projects = await r.call(
        "core_list_projects",
        {},
        "List projects",
    )
    proj = next(p for p in projects["projects"] if p["name"] == PROJECT)
    print(f"    Project: {proj['name']} (id: {proj['id']})")

    # Step 2: Teams
    teams = await r.call(
        "core_list_project_teams",
        {"project": PROJECT},
        "List teams in the project",
    )
    print(f"    Teams: {[t['name'] for t in teams['teams']]}")

    # Step 3: Repos
    repos = await r.call(
        "repo_list_repos",
        {"project": PROJECT},
        "List repositories",
    )
    print(f"    Repos: {[r_['name'] for r_ in repos['repositories']]}")

    # Step 4: Sprint iterations
    iterations = await r.call(
        "wit_get_team_iterations",
        {"project": PROJECT, "team": TEAM},
        "List team sprint iterations",
    )
    print(f"    Sprints: {iterations['count']}")

    # Step 5: Work item counts by type
    counts = await r.call(
        "wit_query_wiql",
        {
            "project": PROJECT,
            "wiql": "SELECT [System.Id] FROM WorkItems WHERE [System.State] <> '' ORDER BY [System.Id]",
            "fetch_details": True,
        },
        "Get all work items with details for type breakdown",
    )
    types: dict[str, int] = {}
    for wi in counts.get("work_items", []):
        t = wi.get("type", "Unknown")
        types[t] = types.get(t, 0) + 1
    print(f"    Work items: {counts['count']} total — {types}")

    # Step 6: Wiki
    wikis = await r.call(
        "wiki_list_wikis",
        {"project": PROJECT},
        "List wikis",
    )
    if wikis["count"] > 0:
        wiki_id = wikis["wikis"][0]["id"]
        pages = await r.call(
            "wiki_list_pages",
            {"project": PROJECT, "wiki_id": wiki_id},
            "Count wiki pages",
        )
        print(f"    Wiki: {wikis['wikis'][0]['name']} ({pages['count']} pages)")


async def wiql_query_patterns(r: ScenarioRunner) -> None:
    """Test various WIQL query patterns an LLM might generate.

    Simulates: different query patterns for filtering, sorting, and grouping.
    """
    # TOOLS: wit_query_wiql

    # Pattern 1: Active items only
    active = await r.call(
        "wit_query_wiql",
        {"project": PROJECT, "wiql": "SELECT [System.Id] FROM WorkItems WHERE [System.State] = 'Active'"},
        "Query: Active work items only",
    )
    print(f"    Active items: {active['count']}")

    # Pattern 2: Items in a specific sprint
    sprint = await r.call(
        "wit_query_wiql",
        {"project": PROJECT, "wiql": f"SELECT [System.Id] FROM WorkItems WHERE [System.IterationPath] UNDER '{PROJECT}\\Sprint 1'", "fetch_details": True},
        "Query: Items in Sprint 1 with details",
    )
    print(f"    Sprint 1 items: {sprint['count']}")

    # Pattern 3: Unassigned items
    unassigned = await r.call(
        "wit_query_wiql",
        {"project": PROJECT, "wiql": "SELECT [System.Id] FROM WorkItems WHERE [System.AssignedTo] = '' AND [System.State] <> 'Closed'"},
        "Query: Unassigned non-closed items",
    )
    print(f"    Unassigned items: {unassigned['count']}")

    # Pattern 4: Recently changed
    recent = await r.call(
        "wit_query_wiql",
        {"project": PROJECT, "wiql": "SELECT [System.Id] FROM WorkItems WHERE [System.ChangedDate] > @Today - 30 ORDER BY [System.ChangedDate] DESC"},
        "Query: Items changed in last 30 days",
    )
    print(f"    Recently changed: {recent['count']}")

    # Pattern 5: High-level items only (Epics + Features)
    highlevel = await r.call(
        "wit_query_wiql",
        {"project": PROJECT, "wiql": "SELECT [System.Id] FROM WorkItems WHERE [System.WorkItemType] IN ('Epic', 'Feature') ORDER BY [System.WorkItemType]", "fetch_details": True},
        "Query: Epics and Features only",
    )
    print(f"    Epics + Features: {highlevel['count']}")


# =============================================================================
# Scenario registry and runner
# =============================================================================

SCENARIOS = [
    epic_hierarchy_drilldown,
    sprint_capacity_analysis,
    backlog_exploration,
    my_work_items_review,
    work_item_type_and_fields,
    work_item_lifecycle,
    sprint_setup_workflow,
    repo_exploration,
    pr_lifecycle,
    wiki_documentation_workflow,
    cross_domain_project_overview,
    wiql_query_patterns,
]

# Tool coverage map — every tool must appear at least once
TOOL_COVERAGE = {
    # Core (3)
    "core_list_projects": ["cross_domain_project_overview", "repo_exploration"],
    "core_list_project_teams": ["sprint_setup_workflow", "cross_domain_project_overview"],
    "core_get_identity_ids": ["sprint_setup_workflow"],
    # WIT (18)
    "wit_query_wiql": ["epic_hierarchy_drilldown", "cross_domain_project_overview", "wiql_query_patterns"],
    "wit_get_work_item": ["epic_hierarchy_drilldown", "my_work_items_review", "work_item_lifecycle"],
    "wit_get_work_items_batch": ["epic_hierarchy_drilldown"],
    "wit_get_work_items_for_iteration": ["sprint_capacity_analysis"],
    "wit_my_work_items": ["my_work_items_review"],
    "wit_list_backlogs": ["backlog_exploration"],
    "wit_list_backlog_work_items": ["backlog_exploration"],
    "wit_get_work_item_type": ["work_item_type_and_fields"],
    "wit_list_fields": ["work_item_type_and_fields"],
    "wit_get_team_iterations": ["sprint_capacity_analysis", "cross_domain_project_overview"],
    "wit_get_team_capacity": ["sprint_capacity_analysis"],
    "wit_create_work_item": ["work_item_lifecycle"],
    "wit_update_work_item": ["work_item_lifecycle"],
    "wit_update_work_items_batch": ["work_item_lifecycle"],
    "wit_add_work_item_comment": ["work_item_lifecycle"],
    "wit_add_child_work_items": ["work_item_lifecycle"],
    "wit_work_items_link": ["work_item_lifecycle"],
    "wit_work_item_unlink": ["work_item_lifecycle"],
    # Work (5)
    "work_list_iterations": ["sprint_capacity_analysis"],
    "work_create_iteration": ["sprint_setup_workflow"],
    "work_assign_iteration_to_team": ["sprint_setup_workflow"],
    "work_update_team_capacity": ["sprint_setup_workflow"],
    "work_get_team_settings": ["sprint_capacity_analysis"],
    # Repos (14) — note: 2 tools are listed under repo_list_repos but it's actually 12 unique
    "repo_list_repos": ["repo_exploration", "cross_domain_project_overview"],
    "repo_get_repo": ["repo_exploration"],
    "repo_list_branches": ["repo_exploration", "pr_lifecycle"],
    "repo_create_branch": ["pr_lifecycle"],
    "repo_search_commits": ["repo_exploration"],
    "repo_list_pull_requests": ["pr_lifecycle"],
    "repo_get_pull_request": ["pr_lifecycle"],
    "repo_create_pull_request": ["pr_lifecycle"],
    "repo_update_pull_request": ["pr_lifecycle"],
    "repo_list_pr_threads": ["pr_lifecycle"],
    "repo_create_pr_thread": ["pr_lifecycle"],
    "repo_reply_to_comment": ["pr_lifecycle"],
    # Wiki (6)
    "wiki_list_wikis": ["wiki_documentation_workflow", "cross_domain_project_overview"],
    "wiki_get_wiki": ["wiki_documentation_workflow"],
    "wiki_create_wiki": [],  # Only called if no wiki exists — covered by integration_test.py fallback
    "wiki_list_pages": ["wiki_documentation_workflow", "cross_domain_project_overview"],
    "wiki_get_page_content": ["wiki_documentation_workflow"],
    "wiki_create_or_update_page": ["wiki_documentation_workflow"],
}


def _save_output(scenario_name: str, steps: list[Step], duration_ms: float, error: str | None, url: str) -> Path:
    """Save scenario output to test-outputs/ as JSON."""
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    filename = f"{timestamp}_{scenario_name}.json"
    path = OUTPUT_DIR / filename

    data = {
        "scenario": scenario_name,
        "timestamp": timestamp,
        "endpoint": url,
        "duration_ms": round(duration_ms, 1),
        "error": error,
        "steps": [s.to_dict() for s in steps],
        "tools_used": list(dict.fromkeys(s.tool for s in steps)),
        "step_count": len(steps),
    }

    path.write_text(json.dumps(data, indent=2, default=str))
    return path


async def run_scenarios(url: str, filter_pattern: str | None = None) -> tuple[int, int]:
    """Connect and run all scenarios."""
    headers = _headers()
    passed = 0
    failed = 0

    scenarios = SCENARIOS
    if filter_pattern:
        scenarios = [s for s in scenarios if filter_pattern in s.__name__]

    print(f"\nRunning {len(scenarios)} scenarios against {url}")
    print(f"Outputs saved to: {OUTPUT_DIR}/\n")

    async with streamablehttp_client(url, headers=headers) as (read, write, _):
        async with ClientSession(read, write) as session:
            await session.initialize()

            runner = ScenarioRunner(session)

            for scenario in scenarios:
                name = scenario.__name__
                doc = (scenario.__doc__ or "").strip().split("\n")[0]
                runner.reset()

                t0 = time.monotonic()
                error = None
                try:
                    await scenario(runner)
                    elapsed = (time.monotonic() - t0) * 1000
                    print(f"\n  PASS  {name} ({len(runner.steps)} steps, {elapsed:.0f}ms)")
                    print(f"        {doc}")
                    passed += 1
                except Exception as e:
                    elapsed = (time.monotonic() - t0) * 1000
                    error = f"{type(e).__name__}: {e}"
                    print(f"\n  FAIL  {name} — {error}")
                    if VERBOSE:
                        traceback.print_exc()
                    failed += 1
                finally:
                    path = _save_output(name, runner.steps, elapsed, error, url)
                    print(f"        → {path.name}")

    return passed, failed


def _check_coverage() -> None:
    """Verify every tool is covered by at least one scenario."""
    uncovered = [tool for tool, scenarios in TOOL_COVERAGE.items() if not scenarios]
    # wiki_create_wiki is a special case — only called when no wiki exists
    uncovered = [t for t in uncovered if t != "wiki_create_wiki"]
    if uncovered:
        print(f"\nWARN: {len(uncovered)} tools not covered by scenarios: {uncovered}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Scenario tests — chained MCP tool calls")
    parser.add_argument("--url", default=DEFAULT_URL, help="MCP endpoint URL")
    parser.add_argument("-k", "--filter", default=None, help="Filter scenarios by name substring")
    args = parser.parse_args()

    if not os.environ.get("AZURE_DEVOPS_PAT"):
        print("ERROR: AZURE_DEVOPS_PAT environment variable required")
        sys.exit(1)

    _check_coverage()

    passed, failed = asyncio.run(run_scenarios(args.url, args.filter))

    # Summary
    print(f"\n{'='*60}")
    print(f"Scenarios: {passed} passed, {failed} failed")
    print(f"Outputs:   {OUTPUT_DIR}/")

    # Count unique tools exercised across all output files
    all_tools: set[str] = set()
    if OUTPUT_DIR.exists():
        for f in OUTPUT_DIR.glob("*.json"):
            try:
                data = json.loads(f.read_text())
                all_tools.update(data.get("tools_used", []))
            except Exception:
                pass
    print(f"Tools:     {len(all_tools)}/44 exercised")
    print(f"{'='*60}")

    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
