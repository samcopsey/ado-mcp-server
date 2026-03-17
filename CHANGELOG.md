# Changelog

## v0.8.0 — Error handling abstraction, JWT validation, public release prep

- **Error handling decorator** — `@handle_tool_errors` wraps all 45 tools with consistent `httpx.HTTPStatusError` catching. Returns structured JSON errors with message truncation at 500 chars to protect LLM token context. Replaces per-tool try/except blocks.
- **JWT validation middleware** — Optional RS256 JWT validation against Azure AD JWKS endpoint. Disabled by default (`JWT_VALIDATION_DISABLED=true`). Supports JWKS key caching with 1-hour TTL. PAT tokens bypass validation.
- **Test coverage** — 123 unit tests (up from 82). Added error handling tests for every tool domain, decorator unit tests, and JWT validation tests.
- **Documentation** — Added architecture overview, error handling docs, JWT configuration guide, CONTRIBUTING.md, CHANGELOG.md.

## v0.7.2 — Security fixes

- Non-root Docker user (`appuser`)
- glibc CVE patches in Dockerfile (`apt-get upgrade`)
- Constant-time API key comparison (`hmac.compare_digest`)

## v0.7.0 — Wiki tools

- 6 wiki tools: list wikis, get wiki, create wiki, list pages, get page content, create/update page
- ETag-based optimistic concurrency for wiki page updates
- Recursive page tree flattening

## v0.6.0 — Repository tools

- 12 repository tools: repos, branches, commits, pull requests, PR threads
- Branch creation with source branch HEAD resolution
- PR comment threads with file path targeting

## v0.5.0 — Work item writes and work domain

- 7 work item write tools: create, update, batch update, comments, linking
- 6 work domain tools: iterations, team settings, capacity management
- JSON Patch support for work item creates/updates
- Batch update with per-item success/failure tracking

## v0.4.0 — Work item read tools

- 11 work item read tools: WIQL, get/batch, backlogs, iterations, capacity
- Custom field discovery via `wit_list_fields`
- Field normalization with short key mapping
- Pre-calculated capacity hours

## v0.3.0 — Core tools

- 3 core tools: list projects, list teams, get identities
- Streamable HTTP transport via FastMCP + Starlette
- API key middleware for non-Foundry clients
- ADOClient with JWT/PAT auto-detection
