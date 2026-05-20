# BlueprintMCP — UE Plugin

C++ plugin that runs an in-editor TCP server. The Python MCP server (`../../server/`) connects to it to drive Blueprint editing.

## Install

1. Copy this `BlueprintMCP/` folder into your UE 5.4 C++ project's `Plugins/` directory.
2. Right-click your `.uproject` → **Generate Xcode Project** (Mac) / **Generate Visual Studio project files** (Windows).
3. Build (Xcode / VS), then open the project in Unreal Editor.
4. The plugin loads automatically. Check Output Log for `BlueprintMCP starting`.

## v0 scope

- Opens TCP on **127.0.0.1:55558**
- Recognizes one command: `{"command":"ping"}` → returns `{"ok":true,"version":"0.0.1","timestamp":...}`

Everything else (create_blueprint, add_node, connect_pins, ...) is in subsequent spike blocks.

## Port choice

55558 (one above chongdashu/unreal-mcp's 55557) so the two can coexist if both are installed.

## Limitations (v0)

- Single-threaded handler — one client at a time
- Blocking recv (8KB max per request, one line)
- No game-thread marshaling yet (ping doesn't need it)
- No auth — localhost trust
