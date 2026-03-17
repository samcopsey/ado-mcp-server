# ado-mcp-server

> **Status: Alpha** — Under active development. APIs may change before v1.0.

Azure DevOps MCP server with Streamable HTTP transport, built for [Microsoft Foundry](https://ai.azure.com). Runs on Container Apps with OAuth Identity Passthrough — each user authenticates with their own ADO credentials.

Built for the [Cast](https://github.com/samcopsey/cast-ado-agent) project.

## Why not the official server?

Microsoft's official [`@azure-devops/mcp`](https://github.com/microsoft/azure-devops-mcp) package has 87 tools across 9 domains — broad coverage. It supports stdio (local) and a [Remote MCP Server](https://devblogs.microsoft.com/devops/azure-devops-remote-mcp-server-public-preview/) (streamable HTTP, public preview). But the remote server currently only works with VS/VS Code via GitHub Copilot — **Microsoft Foundry, Claude Desktop, Claude Code, and ChatGPT are not yet supported**.

For Foundry-based agent systems, you need a server that:
1. Serves MCP over Streamable HTTP on your own infrastructure (Container Apps)
2. Accepts Bearer tokens via the `Authorization` header (Foundry's OAuth Identity Passthrough)

This server does both. For local development, it falls back to the `AZURE_DEVOPS_PAT` environment variable.

## Comparison with official `@azure-devops/mcp`

| Capability | Official (`@azure-devops/mcp`) | This server (`ado-mcp-server`) |
|---|---|---|
| **Transport** | stdio + remote (preview, VS/VS Code only) | Streamable HTTP (Container Apps) |
| **Auth** | Entra interactive / remote OAuth | OAuth Identity Passthrough + PAT fallback |
| **Foundry compatible** | No (remote server doesn't support Foundry yet) | Yes |
| **Per-user ADO permissions** | Yes | Yes |
| **Headless deployment** | Remote preview (Microsoft-hosted, limited clients) | Yes — Docker / Container Apps (any client) |
| **WIQL queries** | No (saved queries only) | Yes — ad-hoc WIQL with detail fetching |
| **Custom field discovery** | Yes | Yes — `wit_list_fields` for runtime discovery |
| **Total tools** | 87 | 45 |
| **Domains** | 9 (core, work items, repos, wiki, pipelines, search, test plans, advanced security, work) | 5 (core, work items, work, repositories, wiki) |

### Where this server is ahead

- **Ad-hoc WIQL** — the official server only supports saved queries. This server lets agents write and execute arbitrary WIQL, which is essential for dynamic work item queries.
- **Capacity calculation** — `wit_get_team_capacity` returns pre-calculated total available hours (capacity × working days − days off), not just raw API data.
- **Foundry-native** — designed from the start for Microsoft Foundry's MCP connection model with OAuth Identity Passthrough. The official remote server doesn't support Foundry yet.
- **Any MCP client** — works with any client that supports streamable HTTP (Claude Desktop, Claude Code, Foundry, custom agents). The official remote server is currently limited to VS/VS Code with GitHub Copilot.

### Where the official server is ahead

- **Breadth** — 87 tools across 9 domains, including pipelines (14), repositories/PRs (21), test plans (9), search (3), and advanced security (2) which this server doesn't cover.
- **Remote hosting** — the official remote server is Microsoft-hosted with zero infrastructure to manage, though currently limited to VS/VS Code with GitHub Copilot.

## Quick start

```bash
# Install
uv venv && source .venv/bin/activate
uv pip install -e ".[dev]"

# Configure
cp .env.example .env
# Edit .env with your PAT and org

# Run
python -m ado_mcp_server
```

Server starts on `http://localhost:3000`. MCP endpoint at `/mcp`, health at `/`.

## Docker

```bash
docker build -t ado-mcp-server .
docker run -p 3000:3000 -e AZURE_DEVOPS_PAT=xxx -e AZURE_DEVOPS_ORG=myorg ado-mcp-server
```

## Security

**Production (Foundry):** Security relies on OAuth Identity Passthrough. Foundry sends an `Authorization: Bearer` token with each MCP request — without a valid Azure AD token, ADO API calls fail. Foundry's MCP connection form doesn't support custom headers or query parameters, so API key auth can't be used with Foundry.

**Non-Foundry clients:** The server includes API key middleware activated by setting `MCP_API_KEY`. When set, requests to `/mcp` must include either an `X-API-Key` header or `?api_key=` query parameter.

- Health endpoints (`/`, `/health`) are always accessible (for Container Apps probes)
- API key validation uses constant-time comparison (`hmac.compare_digest`)
- When `MCP_API_KEY` is not set, all requests are allowed

**Roadmap:** JWT validation middleware to verify Bearer token signatures against Azure AD's JWKS endpoint.

## Deployment to Container Apps

Infrastructure is managed by Terraform in the [cast-ado-agent](https://github.com/samcopsey/cast-ado-agent) repo. The deploy process:

```bash
# From the cast-ado-agent/infra directory
ACR_NAME=$(terraform output -raw acr_name)
ACR_SERVER=$(terraform output -raw acr_login_server)
az acr login --name $ACR_NAME

# From this repo
docker build --platform linux/amd64 -t $ACR_SERVER/ado-mcp-server:v0.8.0 .
docker push $ACR_SERVER/ado-mcp-server:v0.8.0

# Back in cast-ado-agent/infra — deploy with versioned tag
terraform apply -var-file=environments/dev.tfvars -var='mcp_server_image_tag=v0.8.0'
```

**Important:** Use versioned image tags (not `:latest`) — Container Apps won't pull a new image if the tag hasn't changed.

## Testing

```bash
uv pip install -e ".[dev]"
pytest tests/ -v
```

Tests cover API key middleware (9 tests), core tools (6 tests), work item read tools (26 tests), work item write tools (12 tests), work domain tools (11 tests), ADO client methods (5 tests), repository tools (22 tests), wiki tools (12 tests), decorator unit tests (5 tests), and JWT validation (14 tests) — 123 tests total, all with mocked ADO API responses via `respx`.

### Integration tests

Integration tests run against the live MCP endpoint with real ADO data:

```bash
# Against deployed endpoint
export AZURE_DEVOPS_PAT=xxx
export MCP_API_KEY=xxx
python scripts/integration_test.py

# Against local server
export AZURE_DEVOPS_PAT=xxx
python scripts/integration_test.py --url http://localhost:3000/mcp

# Run a single test
python scripts/integration_test.py -k test_wit_create_work_item

# Verbose mode (full tracebacks)
VERBOSE=1 python scripts/integration_test.py
```

Integration tests exercise every tool against the `cast-testing` ADO org — creating work items, updating fields, managing iterations, creating wiki pages, and verifying responses. Write tests clean up after themselves (created items are closed).

## Tools (v0.8.0)

### Core (3 tools)

| Tool | Description |
|------|-------------|
| `core_list_projects` | List projects in the organisation |
| `core_list_project_teams` | List teams for a project |
| `core_get_identity_ids` | Search for user identities |

### Work Items — Read (11 tools)

| Tool | Description |
|------|-------------|
| `wit_query_wiql` | Execute ad-hoc WIQL queries, optionally fetch full details. Supports custom `fields` param. |
| `wit_get_work_item` | Get a single work item with relations (parent/child links). Supports custom `fields`. |
| `wit_get_work_items_batch` | Get multiple work items by IDs (batched at 200). Supports custom `fields`. |
| `wit_get_work_items_for_iteration` | Get all work items for a sprint iteration path |
| `wit_my_work_items` | Get non-closed work items assigned to the current user (`@Me`) |
| `wit_list_backlogs` | List backlog levels (Epics, Features, Stories, etc.) for a team |
| `wit_list_backlog_work_items` | List work items in a specific backlog level |
| `wit_get_work_item_type` | Get field definitions, state transitions, and rules for a work item type |
| `wit_list_fields` | List all fields (system + custom) for a project — discover `Custom.*` fields dynamically |
| `wit_get_team_iterations` | Get sprint schedules for a team. Optional `timeframe` filter (past/current/future). |
| `wit_get_team_capacity` | Get team capacity per member with pre-calculated total available hours |

### Work Items — Write (7 tools)

| Tool | Description |
|------|-------------|
| `wit_create_work_item` | Create a new work item (User Story, Task, Bug, etc.) with optional fields and parent link |
| `wit_update_work_item` | Update fields on an existing work item |
| `wit_update_work_items_batch` | Update multiple work items in a single call (returns success/failure per item) |
| `wit_add_work_item_comment` | Add a comment to a work item |
| `wit_add_child_work_items` | Add child links to a parent work item |
| `wit_work_items_link` | Add a link between two work items (Related, Dependency, etc.) |
| `wit_work_item_unlink` | Remove a relation from a work item by index |

### Work Domain (6 tools)

| Tool | Description |
|------|-------------|
| `work_list_iterations` | List all iterations defined in a project |
| `work_create_iteration` | Create a new iteration (sprint) with optional start/end dates |
| `work_update_iteration` | Update an existing iteration's start/end dates |
| `work_assign_iteration_to_team` | Assign an existing iteration to a team's sprint schedule |
| `work_update_team_capacity` | Update a team member's capacity and days off for a sprint |
| `work_get_team_settings` | Get team settings (default iteration, working days, bug behaviour) |

### Repositories (12 tools)

| Tool | Description |
|------|-------------|
| `repo_list_repos` | List all Git repositories in a project |
| `repo_get_repo` | Get details of a specific repository |
| `repo_list_branches` | List all branches in a repository |
| `repo_create_branch` | Create a new branch from a source branch |
| `repo_search_commits` | Search commits with optional filters (author, date, path) |
| `repo_list_pull_requests` | List pull requests in a project or repository |
| `repo_get_pull_request` | Get details of a specific pull request |
| `repo_create_pull_request` | Create a pull request |
| `repo_update_pull_request` | Update a pull request (status, title, description, draft) |
| `repo_list_pr_threads` | List comment threads on a pull request |
| `repo_create_pr_thread` | Create a comment thread on a pull request |
| `repo_reply_to_comment` | Reply to a comment thread |

### Wiki (6 tools)

| Tool | Description |
|------|-------------|
| `wiki_list_wikis` | List all wikis in a project |
| `wiki_get_wiki` | Get details of a specific wiki |
| `wiki_create_wiki` | Create a new project or code wiki |
| `wiki_list_pages` | List all pages in a wiki as a flat list with depth |
| `wiki_get_page_content` | Get a wiki page's markdown content and ETag |
| `wiki_create_or_update_page` | Create or update a wiki page (uses ETag for concurrency) |

### Custom field extensibility

Agents can discover and query custom fields without code changes:

1. Call `wit_list_fields` to discover available fields (including `Custom.TShirtSize`, etc.)
2. Pass discovered reference names to `wit_query_wiql`, `wit_get_work_item`, or `wit_get_work_items_batch` via the `fields` parameter
3. Custom fields appear in the response with their reference names as keys

### Architecture

```
src/ado_mcp_server/
├── __init__.py
├── __main__.py          # Entry point (uvicorn)
├── server.py            # ASGI app, middleware (API key, JWT)
├── tools/
│   ├── core.py          # 3 tools — projects, teams, identities
│   ├── work_items.py    # 18 tools — WIQL, work items, backlogs, capacity, writes
│   ├── work.py          # 6 tools — iterations, team settings, capacity
│   ├── repositories.py  # 12 tools — repos, branches, PRs, threads
│   └── wiki.py          # 6 tools — wiki pages, content, ETag handling
└── utils/
    ├── ado_client.py    # Async httpx wrapper for ADO REST API
    ├── auth.py          # Token extraction and API key validation
    ├── jwt_validator.py # JWT validation against Azure AD JWKS
    └── tool_handler.py  # @handle_tool_errors decorator
```

Every tool function uses the `@handle_tool_errors` decorator which catches `httpx.HTTPStatusError` and returns structured JSON error responses. New tools should follow this pattern:

```python
@mcp.tool()
@handle_tool_errors
async def my_new_tool(ctx: Context, ...) -> str:
    client = ADOClient(get_org(ctx), get_token(ctx))
    try:
        result = await client.get(...)
        return json.dumps(result, indent=2)
    finally:
        await client.close()
```

### Error handling

All tools use the `@handle_tool_errors` decorator (`utils/tool_handler.py`) which:

- Catches `httpx.HTTPStatusError` and returns `{"error": true, "status": <code>, "message": "<truncated>"}`
- Truncates error messages to 500 characters to protect LLM token context
- Lets non-HTTP exceptions propagate to FastMCP's built-in error handling

### JWT validation

Optional JWT validation middleware for verifying Azure AD Bearer tokens. Disabled by default.

| Variable | Default | Description |
|----------|---------|-------------|
| `JWT_VALIDATION_DISABLED` | `true` | Set to `false` to enable JWT validation |
| `JWT_TENANT_ID` | — | Azure AD tenant ID for JWKS endpoint and issuer validation |
| `JWT_AUDIENCE` | — | Expected `aud` claim in JWT tokens |

When enabled, the middleware:

- Fetches and caches Azure AD JWKS public keys (1-hour TTL)
- Validates RS256 signature, `iss`, `aud`, `exp` claims
- PAT tokens (not starting with `ey`) bypass validation entirely
- Fails open if JWKS endpoint is unreachable (availability over security for dev)

### Roadmap

| Version | Tier | Tools | Status |
|---------|------|-------|--------|
| v0.3.0 | Core | 3 core read tools | Deployed |
| v0.4.0 | Tier 1 | 12 work item read tools | Deployed |
| v0.5.0 | Tier 2 | 12 work item write + work domain tools | Deployed |
| v0.6.0 | Tier 3 | 12 repository/PR tools | Deployed |
| v0.7.0 | Tier 4 | 6 wiki tools | Deployed |
| v0.8.0 | Hardening | Error handling abstraction, JWT validation, public release prep | Deployed |

## Environment variables

| Variable | Required | Description |
|----------|----------|-------------|
| `AZURE_DEVOPS_PAT` | For local dev | Personal access token for ADO API |
| `AZURE_DEVOPS_ORG` | Yes | Default ADO organisation name |
| `MCP_SERVER_PORT` | No | Server port (default: 3000) |
| `MCP_API_KEY` | No | API key for endpoint protection (not used with Foundry — see Security) |
| `JWT_VALIDATION_DISABLED` | No | Set to `false` to enable JWT validation (default: `true`) |
| `JWT_TENANT_ID` | No | Azure AD tenant ID for JWKS and issuer validation |
| `JWT_AUDIENCE` | No | Expected `aud` claim in JWT tokens |
