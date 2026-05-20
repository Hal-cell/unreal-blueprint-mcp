# unreal-blueprint-mcp

> Write Unreal Engine 5 Blueprints by talking to Claude. A small but complete MCP server + UE plugin.

Say it: *"Make a Blueprint that prints 'hello world' on BeginPlay, then spawn it."*
Get it: an actual `.uasset`, wired graph, compiled, and an instance sitting in your level — ready to PIE.

[![v4](https://img.shields.io/badge/version-v4-brightgreen)](#status)
[![21 tools](https://img.shields.io/badge/tools-21-blue)](#tools)
[![UE 5.4](https://img.shields.io/badge/UE-5.4-orange)](#requirements)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

---

## What this is

A Model Context Protocol (MCP) server that lets an LLM (e.g. Claude Desktop, Claude Code, Cursor) **author Blueprints in a running Unreal Engine editor** — create assets, add nodes, wire pins, set defaults, compile, and spawn — by chaining a small set of tools.

```
[Claude Desktop] ── MCP stdio ──→ [Python FastMCP server] ── TCP ──→ [C++ UE plugin] ── in-editor ──→ Unreal Engine 5.4
```

Built **from scratch** (no fork of existing UE-MCP projects) for full ownership, UE 5.4 native support, and a deliberately small, well-tested tool surface.

## Why another UE-MCP?

There are larger projects in this space ([`chongdashu/unreal-mcp`](https://github.com/chongdashu/unreal-mcp), [`flopperam/unreal-engine-mcp`](https://github.com/flopperam/unreal-engine-mcp)). This one trades feature breadth for:

- **UE 5.4 native** support (most others target 5.5+)
- **Anchor-name system** — every node carries a human-readable label as its `NodeComment` (the LLM's label becomes the UI label, persists across sessions)
- **Auto-spawn well-known events** — `begin_play` / `tick` / `actor_end_overlap` / `hit` / `destroyed` / etc. always work even in a fresh BP
- **Quality over breadth** — fewer tools, every one with docstrings written for an LLM consumer
- **Game-thread safety** as a first-class invariant — all UObject ops marshaled via `TPromise`/`TFuture` with a 10s deadline

## Status

| | |
|---|---|
| **v0** | ✅ End-to-end: BP creation → node ops → pin wiring → compile → spawn → PIE prints "hello world" |
| **v1** | ✅ Components, custom events, variables, variable get/set, auto-spawn well-known events — full collision-timer demo working |
| **v2** | ✅ `get_blueprint` — full BP introspection (anchors / connections / variables / components) so LLMs stop blind-writing |
| **v3** | ✅ `add_branch` (K2Node_IfThenElse) + `add_cast` (K2Node_DynamicCast) — conditional & type-narrowing flow |
| **v4** | ✅ `add_macro` (ForEachLoop / WhileLoop / FlipFlop / DoOnce / Gate / ...) + `add_self_reference` + `add_input_key` + `delete_node` + `disconnect_pins` + struct types in `set_pin_default` (Vector / Rotator / Color) |
| **Unit tests** | 48 passing, 7 integration tests gated on a running UE editor |
| **Plugin binary** | ~466 KB dylib on macOS / UE 5.4.4 |

## Requirements

- **Unreal Engine 5.4** (verified on 5.4.4 macOS)
- **Python 3.10+** (3.11 recommended — uv will install it for you)
- [**uv**](https://docs.astral.sh/uv/) for Python env management
- An MCP client (Claude Desktop, Claude Code, Cursor, etc.)

## Install

### 1. UE plugin

Symlink (or copy) `plugin/BlueprintMCP/` into your UE project's `Plugins/` folder:

```bash
cd <YOUR_UE_PROJECT>/Plugins
ln -s /absolute/path/to/unreal-blueprint-mcp/plugin/BlueprintMCP BlueprintMCP
```

Then open the `.uproject`. UE will prompt to build the missing plugin — click **Yes**. After build, check the Output Log for:

```
LogBlueprintMCP: BlueprintMCP starting
LogBlueprintMCP_TCP: TCP server listening on 0.0.0.0:55558
```

(Port `55558` was chosen to coexist with `chongdashu/unreal-mcp`'s `55557`.)

### 2. Python MCP server

```bash
cd server
uv sync          # installs core deps
uv sync --extra dev    # if you want to run the tests
```

Verify:

```bash
uv run pytest    # → 32 passed, 7 skipped
```

### 3. Wire to Claude Desktop

Edit `~/Library/Application Support/Claude/claude_desktop_config.json`:

```json
{
  "mcpServers": {
    "unreal-blueprint-mcp": {
      "command": "/Users/YOU/.local/bin/uv",
      "args": [
        "run",
        "--directory", "/absolute/path/to/unreal-blueprint-mcp/server",
        "python", "-m", "unreal_blueprint_mcp.server"
      ]
    }
  }
}
```

Use absolute path to `uv` — macOS GUI processes don't inherit shell PATH.

Quit Claude Desktop completely (Cmd+Q, not just close the window) and reopen.

## Tools

| Tool | What it does |
|------|--------------|
| `ping_ue` | Health check: are UE + the plugin alive? |
| `echo` | MCP stdio plumbing sanity test |
| **`get_blueprint`** (v2) | **Snapshot of a BP: anchors / pins / connections / variables / components — call this BEFORE writing** |
| `create_blueprint` | New BP asset in `/Game/...`, parent class from whitelist |
| `add_component` | Add a component to the BP's SCS (BoxCollision, StaticMesh, Camera, ...) |
| `add_node` | Add a `K2Node_CallFunction` node (whitelist or fully qualified) |
| `add_custom_event` | Add a `K2Node_CustomEvent` (red node) for delegate targets |
| `add_variable` | Add a member variable (incl. **TimerHandle** for timer cancel patterns) |
| `add_variable_get` / `add_variable_set` | Read/write nodes for BP variables |
| **`add_branch`** (v3) | Add a `K2Node_IfThenElse` (Branch) — if/else flow |
| **`add_cast`** (v3) | Add a `K2Node_DynamicCast` (Cast To X) — type narrowing |
| **`add_macro`** (v4) | Add a `K2Node_MacroInstance` — ForEachLoop / ForLoop / WhileLoop / FlipFlop / DoOnce / Gate / IsValid |
| **`add_self_reference`** (v4) | Add a `K2Node_Self` — self reference |
| **`add_input_key`** (v4) | Add a `K2Node_InputKey` — keyboard / mouse / gamepad key event |
| **`delete_node`** (v4) | Delete a node and break all its connections |
| **`disconnect_pins`** (v4) | Break a single pin link (inverse of `connect_pins`) |
| `set_pin_default` | Override a pin's default value (primitives + **Vector / Rotator / Color** since v4) |
| `connect_pins` | Wire two pins; **auto-spawns well-known events on demand** |
| `compile_blueprint` | `FKismetEditorUtilities::CompileBlueprint` |
| `spawn_actor` | Place a compiled BP instance into the current level |

## v1 Collision-Timer demo

One prompt to Claude:

> "Make a BP `BP_CollisionTimer` (Actor). Add a BoxCollision named `TriggerBox`. Add a `TimerHandle` variable named `MyTimer`. Add a custom event `OnStayed3Sec` that prints 'stayed 3 seconds'. On ActorBeginOverlap, SetTimerByEvent (3 sec) targeting OnStayed3Sec, store the handle in MyTimer. On ActorEndOverlap, ClearAndInvalidateTimerByHandle using MyTimer. Compile and spawn."

→ Claude chains ~15-20 tool calls → press **Play** → walk into the trigger → stay 3 seconds → `"stayed 3 seconds"`. Leave early → no print (timer canceled).

## Project layout

```
.
├── plugin/BlueprintMCP/       # UE C++ plugin (drop into <UE_PROJECT>/Plugins/)
│   ├── BlueprintMCP.uplugin
│   └── Source/BlueprintMCP/   # ~700 lines C++
└── server/                     # Python MCP server (FastMCP)
    ├── pyproject.toml
    ├── unreal_blueprint_mcp/
    │   └── server.py          # ~250 lines Python
    └── tests/test_server.py
```

## Design notes

- **Game-thread marshaling.** Every UObject-touching operation hops back to the game thread via `AsyncTask(ENamedThreads::GameThread, ...)` and uses `TPromise<FString>` / `TFuture<FString>` for thread-safe result return + a 10-second deadline. Pattern documented in `TCPServer.cpp` and reused identically across all tools.
- **JSON contract.** Every response is `{"ok": bool, ...}`. Errors carry `error` and `detail` fields, sometimes `hint` for the most likely cause. The plugin uses UE's built-in `EscapeJsonString` from `Serialization/JsonWriter.h` (which also adds quotes — a real gotcha caught during development).
- **Anchor names.** LLMs reference nodes by user-given anchor names (e.g., `"print_hello"`), never by GUID. Anchors live in `UEdGraphNode::NodeComment`, which means the LLM's labels are visible in the editor and persist across sessions.
- **No upstream fork.** Inspired by chongdashu/flopperam for architecture choices, re-derived from scratch for ownership and 5.4 support.

## Roadmap

Likely directions for v2:

- `get_blueprint(name)` — return a graph snapshot so the LLM can "look before writing" (currently it's blind-writing)
- Extend node types: `K2Node_IfThenElse`, `K2Node_ForEachLoop`, more
- Extend component whitelist (SkeletalMesh, Niagara, PostProcess, ...)
- Extend variable types (Vector, Rotator, Object refs, Class refs)
- Integration test harness (headless UE editor)

## Acknowledgments

- [`chongdashu/unreal-mcp`](https://github.com/chongdashu/unreal-mcp) and [`flopperam/unreal-engine-mcp`](https://github.com/flopperam/unreal-engine-mcp) for architectural reference. This project is independently written (no copied code) but informed by their public designs.
- The [MCP Python SDK](https://github.com/modelcontextprotocol/python-sdk) team — FastMCP made the server side a small file.

## License

MIT — see [LICENSE](LICENSE).
