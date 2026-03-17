# Contributing

## Development setup

```bash
# Clone and create virtualenv
git clone https://github.com/samcopsey/ado-mcp-server.git
cd ado-mcp-server
uv venv && source .venv/bin/activate
uv pip install -e ".[dev]"

# Configure environment
cp .env.example .env
# Edit .env with your PAT and org
```

## Running tests

```bash
# Unit tests (mocked ADO API)
pytest tests/ -v

# Single test file
pytest tests/test_wiki.py -v

# Integration tests (requires live ADO)
export AZURE_DEVOPS_PAT=xxx
python scripts/integration_test.py
```

## Code style

This project uses [ruff](https://docs.astral.sh/ruff/) for linting:

```bash
ruff check src/ tests/
ruff format src/ tests/
```

Configuration in `pyproject.toml`: line length 120, target Python 3.11.

## Adding a new tool

1. Add the tool function to the appropriate file in `src/ado_mcp_server/tools/`
2. Apply both decorators — `@mcp.tool()` outer, `@handle_tool_errors` inner:

    ```python
    @mcp.tool()
    @handle_tool_errors
    async def my_new_tool(ctx: Context, project: str) -> str:
        """Tool description for the LLM."""
        client = ADOClient(get_org(ctx), get_token(ctx))
        try:
            result = await client.get(f"/{project}/_apis/...")
            return json.dumps(result, indent=2)
        finally:
            await client.close()
    ```

3. Add unit tests with mocked ADO responses via `respx`
4. Add at least one error test (4xx response)
5. Update the tool count in `README.md`

## Pull requests

- Keep PRs focused. One feature or fix per PR
- All tests must pass (`pytest tests/ -v`)
- Lint must pass (`ruff check src/ tests/`)
- Update README tool tables if adding/removing tools
