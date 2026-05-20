# unreal-blueprint-mcp (Python MCP server)

FastMCP-based server that talks to the BlueprintMCP UE plugin (`../plugin/`) via TCP.

## Install (dev)

```bash
cd server
uv sync                              # installs deps from pyproject.toml
```

## Run with Claude Desktop

Add to `~/Library/Application Support/Claude/claude_desktop_config.json`:

```json
{
  "mcpServers": {
    "unreal-blueprint-mcp": {
      "command": "uv",
      "args": [
        "run",
        "--directory", "/Users/xuchenghao/Workspace/unreal-blueprint-mcp/server",
        "python", "-m", "unreal_blueprint_mcp.server"
      ]
    }
  }
}
```

Restart Claude Desktop. Then ask Claude to call `echo` or `ping_ue`.

## Run with MCP Inspector (no Claude needed for debugging)

```bash
uv run mcp dev unreal_blueprint_mcp/server.py
```

Opens a browser UI where you can invoke tools manually.

## v0 tools

- `echo(message)` — sanity-test the MCP stdio plumbing
- `ping_ue()` — TCP-ping the BlueprintMCP UE plugin on `127.0.0.1:55558`
