# unreal-blueprint-mcp

> Write Unreal Engine 5 Blueprints by talking to Claude. A small but complete MCP server + UE plugin.

Say it: *"Make a Blueprint that prints 'hello world' on BeginPlay, then spawn it."*
Get it: an actual `.uasset`, wired graph, compiled, and an instance sitting in your level — ready to PIE.

[![v8.0.3](https://img.shields.io/badge/version-v8.0.3-brightgreen)](#status)
[![48 tools](https://img.shields.io/badge/tools-48-blue)](#tools)
[![125 tests](https://img.shields.io/badge/tests-125%20passing-success)](#requirements)
[![UE 5.4](https://img.shields.io/badge/UE-5.4-orange)](#requirements)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

See [CHANGELOG.md](CHANGELOG.md) for the full version history.

---

## What this is

A Model Context Protocol (MCP) server that lets an LLM (e.g. Claude Desktop, Claude Code, Cursor) **author Blueprints in a running Unreal Engine editor** — create assets, add nodes, wire pins, set defaults, compile, spawn, **and now author whole function bodies / event-dispatcher patterns / component-property defaults** — by chaining a small set of tools.

```
[Claude Desktop] ── MCP stdio ──→ [Python FastMCP server] ── TCP ──→ [C++ UE plugin] ── in-editor ──→ Unreal Engine 5.4
```

Built **from scratch** (no fork of existing UE-MCP projects) for full ownership, UE 5.4 native support, and a deliberately small, well-tested tool surface.

## Why another UE-MCP?

There are larger projects in this space ([`chongdashu/unreal-mcp`](https://github.com/chongdashu/unreal-mcp), [`flopperam/unreal-engine-mcp`](https://github.com/flopperam/unreal-engine-mcp)). This one trades feature breadth for:

- **UE 5.4 native** support (most others target 5.5+)
- **Anchor-name system** — every node carries a human-readable label as its `NodeComment` (the LLM's label becomes the UI label, persists across sessions)
- **Auto-spawn well-known events** — `begin_play` / `tick` / `actor_end_overlap` / `hit` / `destroyed` / etc. always work even in a fresh BP
- **Function-body editing** — every graph-writing tool accepts `graph_name=` to target a user function instead of EventGraph (v7.7+)
- **FProperty reflection for component defaults** — `set_component_property` configures mesh asset / box extent / collision preset via dot-notation paths (v7.1+)
- **Native struct break/make** — `add_break_struct HitResult` returns full member pins instead of an empty K2Node (v7.1.0+)
- **Agentic closed loop** — `start_pie` / `pie_press_key` / `read_log_capture` lets the LLM run + verify its own work (v8+)
- **Quality over breadth** — fewer tools, every one with docstrings written for an LLM consumer
- **Game-thread safety** as a first-class invariant — all UObject ops marshaled via `TPromise`/`TFuture` with a 10s deadline

## Status

| | |
|---|---|
| **v0** | ✅ End-to-end: BP creation → node ops → pin wiring → compile → spawn → PIE prints "hello world" |
| **v1** | ✅ Components, custom events, variables, variable get/set, auto-spawn well-known events — full collision-timer demo working |
| **v2** | ✅ `get_blueprint` — full BP introspection (anchors / connections / variables / components / **functions**) so LLMs stop blind-writing |
| **v3** | ✅ `add_branch` (K2Node_IfThenElse) + `add_cast` (K2Node_DynamicCast) — conditional & type-narrowing flow |
| **v4** | ✅ `add_macro` (ForEachLoop / WhileLoop / FlipFlop / DoOnce / Gate / ...) + `add_self_reference` + `add_input_key` + `delete_node` + `disconnect_pins` + struct types in `set_pin_default` (Vector / Rotator / Color) |
| **v5** | ✅ Enhanced Input (`create_input_action` + `create_input_mapping_context` + `add_mapping_to_imc` + `add_enhanced_input_node`) + `add_function` + `call_blueprint_function` + **array variable types** + **+30 math/system/array short-names** in `add_node` whitelist |
| **v6** | ✅ `wire_imc_subscribe` (one-shot Enhanced Input runtime subscribe chain) + `call_blueprint_function` `target_pin` auto-wire + P0–P7 hotfix bundle |
| **v7.0** | ✅ 12 new tools: `set_component_property` + `add_switch`/`add_sequence`/`add_make_array`/`add_select` + `add_make_struct`/`add_break_struct` + 4 event-dispatcher tools + `save_blueprint`. Extended: object/class ref variables, custom event params, function-body editing via `graph_name=` |
| **v7.1** | ✅ 4 hotfixes (`add_call_dispatcher` real fix via `AddMemberVariable PC_MCDelegate`, native-break struct → `K2Node_CallFunction` substitution, function entry well-known anchor, switch off-by-one + hidden-pin filter), `wire_imc_subscribe` splice-mode fallback, `call_blueprint_function` auto-compile on miss |
| **v7.7.1** | ✅ `graph_name=` extended to **all 18 graph-writing tools** (was 5 in v7.0) |
| **v8.0** | ✅ Agentic closed loop: `start_pie` / `stop_pie` / `is_pie_running` + `pie_press_key` + `read_log_capture` / `clear_log_capture` |
| **v8.0.1** | ✅ MCP command stream visible to `read_log_capture` + `delete_event_dispatcher` recovery tool |
| **v8.0.2** | ✅ `migrate_dispatchers` (programmatic legacy repair) + `ping` reports `plugin_version` + `build_date` |
| **v8.0.3** | ✅ `read_log_capture` category filter is **real substring match** as documented |
| **Unit tests** | **125 passing**, 16 integration tests gated on a running UE editor |
| **Plugin binary** | **~717 KB** dylib on macOS / UE 5.4.4 |

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
uv run pytest    # → 125 passed, 16 skipped
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

**48 total.** Tools marked **(v7)** are new or significantly extended in v7; **(v8)** are the agentic-loop primitives. Almost every graph-writing tool below accepts an optional `graph_name=` kwarg (v7.7.1+) — default empty = EventGraph, pass a function/macro name to operate inside that graph's body.

### Asset & project

| Tool | What it does |
|------|--------------|
| `ping_ue` | Health check: are UE + the plugin alive? Returns `plugin_version` + `build_date` (v8.0.2+) |
| `echo` | MCP stdio plumbing sanity test |
| `create_blueprint` | New BP asset in `/Game/...`, parent class from whitelist |
| `compile_blueprint` | `FKismetEditorUtilities::CompileBlueprint` |
| `spawn_actor` | Place a compiled BP instance into the current level |
| **`save_blueprint`** (v7) | Explicit `UEditorAssetLibrary::SaveAsset` |

### Introspection

| Tool | What it does |
|------|--------------|
| **`get_blueprint`** (v2, **v7-extended**) | **Snapshot of a BP — anchors / pins / connections / variables / components / `functions`. Call this BEFORE writing.** |

### Component & variable management

| Tool | What it does |
|------|--------------|
| `add_component` | Add a component to the BP's SCS (BoxCollision, StaticMesh, Camera, ...) |
| **`set_component_property`** (v7) | **Set component template defaults via FProperty reflection: mesh asset, box extent, collision preset, with dot-notation for nested struct fields (`BodyInstance.CollisionProfileName`)** |
| `add_variable` | Add a member variable. Types: primitives, `TimerHandle`, arrays, **`object:Actor` / `class:Pawn` references (v7)** |
| `add_variable_get` / `add_variable_set` | Read/write nodes for BP variables |

### Function & dispatcher authoring

| Tool | What it does |
|------|--------------|
| `add_function` | Create an empty user function graph in the BP (entry anchor `entry`) |
| `call_blueprint_function` | Call a function on another class — native or BP class path. **Auto-compiles target BP on function miss (v7.1.3)** |
| **`add_event_dispatcher`** (v7) | Create a multicast delegate on the BP (signature graph + member variable + auto-compile) |
| **`add_call_dispatcher`** (v7) | `K2Node_CallDelegate` — broadcast the dispatcher |
| **`add_bind_dispatcher`** (v7) | `K2Node_AddDelegate` — bind a custom event to the dispatcher |
| **`add_unbind_dispatcher`** (v7) | `K2Node_RemoveDelegate` — unbind |
| **`delete_event_dispatcher`** (v8) | Remove a dispatcher's signature graph + member variable (legacy recovery + cleanup) |
| **`migrate_dispatchers`** (v8) | Programmatic repair: backfill missing PC_MCDelegate member variable on pre-v7.1.2 dispatchers |

### Node creation

| Tool | What it does |
|------|--------------|
| `add_node` | Add a `K2Node_CallFunction` node (whitelist or fully qualified) |
| `add_custom_event` | Add a `K2Node_CustomEvent` (red node) — **with params (v7.5):** `params=[{name,type}]` |
| `add_branch` (v3) | `K2Node_IfThenElse` — if/else flow |
| `add_cast` (v3) | `K2Node_DynamicCast` — Cast To X |
| `add_macro` (v4) | `K2Node_MacroInstance` — ForEachLoop / ForLoop / WhileLoop / FlipFlop / DoOnce / Gate / IsValid |
| `add_self_reference` (v4) | `K2Node_Self` |
| `add_input_key` (v4) | `K2Node_InputKey` — legacy keyboard/mouse/gamepad |
| **`add_switch`** (v7) | `K2Node_SwitchInteger` / `SwitchString` / `SwitchName` / `SwitchEnum` |
| **`add_sequence`** (v7) | `K2Node_ExecutionSequence` |
| **`add_make_array`** (v7) | `K2Node_MakeArray` |
| **`add_select`** (v7) | `K2Node_Select` |
| **`add_make_struct`** (v7) | `K2Node_MakeStruct` — Vector / Rotator / Transform / HitResult / etc. **Native-make detection** subs in `K2Node_CallFunction` |
| **`add_break_struct`** (v7) | `K2Node_BreakStruct` — same native-break detection |

### Enhanced Input

| Tool | What it does |
|------|--------------|
| `create_input_action` (v5) | UInputAction asset (Boolean / Axis1D / Axis2D / Axis3D) |
| `create_input_mapping_context` (v5) | UInputMappingContext asset |
| `add_mapping_to_imc` (v5) | Bind a key (with `"Space"`→`"SpaceBar"` aliases) to a UInputAction in an IMC |
| `add_enhanced_input_node` (v5) | `K2Node_EnhancedInputAction` event listener |
| `wire_imc_subscribe` (v6) | One-shot: builds the runtime IMC-subscribe chain (BeginPlay → GetPlayerController → GetSubsystem → Cast → AddMappingContext). **Splice-mode preserves existing BeginPlay chain (v7.1.1).** |

### Pin & connection ops

| Tool | What it does |
|------|--------------|
| `set_pin_default` | Override a pin's default value: primitives + struct (Vector/Rotator/Color) + **object/class refs (v6.0.2)** |
| `connect_pins` | Wire two pins; **auto-spawns well-known events on demand** |
| `disconnect_pins` (v4) | Break a single pin link |
| `delete_node` (v4) | Delete a node and break all its connections |

### Agentic closed loop (v8)

| Tool | What it does |
|------|--------------|
| **`start_pie`** (v8) | Begin a PIE session (`GEditor->RequestPlaySession`). Returns `queued:true` — wait a tick before `pie_press_key` |
| **`stop_pie`** (v8) | End the active PIE session (`RequestEndPlayMap`) |
| **`is_pie_running`** (v8) | Query PIE state — `running` (active) + `start_queued` (requested but not yet ticked) |
| **`pie_press_key`** (v8) | Simulate press + release on `APlayerController` (works for legacy + Enhanced Input) |
| **`read_log_capture`** (v8) | Read recent UE log lines from a thread-safe FOutputDevice buffer. Filter by `category` (substring) / `verbosity` / `contains` / `max_lines`. **Sees MCP commands at category `BlueprintMCP_TCP` (v8.0.1+)** |
| **`clear_log_capture`** (v8) | Drop the log buffer before triggering an action |

## v1 Collision-Timer demo

One prompt to Claude:

> "Make a BP `BP_CollisionTimer` (Actor). Add a BoxCollision named `TriggerBox`. Add a `TimerHandle` variable named `MyTimer`. Add a custom event `OnStayed3Sec` that prints 'stayed 3 seconds'. On ActorBeginOverlap, SetTimerByEvent (3 sec) targeting OnStayed3Sec, store the handle in MyTimer. On ActorEndOverlap, ClearAndInvalidateTimerByHandle using MyTimer. Compile and spawn."

→ Claude chains ~15-20 tool calls → press **Play** → walk into the trigger → stay 3 seconds → `"stayed 3 seconds"`. Leave early → no print (timer canceled).

## v7 Target-Dummy demo

Showcasing the new component-property + event-dispatcher tools:

> "Make a BP `BP_TargetDummy` (Actor). Add a `StaticMesh` component called `VisualMesh` and assign `/Engine/BasicShapes/Cube` as its StaticMesh. Add a `BoxCollision` called `TriggerBox` with BoxExtent `(X=200,Y=200,Z=200)` and `BodyInstance.CollisionProfileName=OverlapAllDynamic`. Define an event dispatcher `OnHit` with float Damage and Actor Source. On ActorBeginOverlap, broadcast OnHit with Damage=10 and Source=OtherActor. Compile and spawn."

→ Walk into the trigger → `OnHit` fires for every listener bound to the dummy.

## v8 Agentic-loop demo

```python
# All from inside one MCP session, no human:
create_blueprint("BP_HelloAuto") + add_node(PrintString) + ...   # 1. author
compile_blueprint() + spawn_actor()                              # 2. deploy
clear_log_capture()
start_pie()
# poll is_pie_running until running == True
pie_press_key("Space")                                           # 3. drive
log = read_log_capture(category="BlueprintUserMessages", contains="hello")
assert log["returned"] >= 1, "BP didn't print — fall into self-debug"
stop_pie()                                                       # 4. teardown
```

The LLM writes a BP, runs it, presses keys, reads what UE logged, and verifies its own work. If the assertion fails, `read_log_capture(verbosity="Warning")` and `read_log_capture(category="BlueprintMCP_TCP", contains="set_pin_default")` give it the diagnostic surface to figure out what went wrong.

## Project layout

```
.
├── plugin/BlueprintMCP/       # UE C++ plugin (drop into <UE_PROJECT>/Plugins/)
│   ├── BlueprintMCP.uplugin
│   └── Source/BlueprintMCP/   # ~5300 lines C++ (v8.0.3)
└── server/                     # Python MCP server (FastMCP)
    ├── pyproject.toml
    ├── unreal_blueprint_mcp/
    │   └── server.py          # ~2200 lines Python
    └── tests/test_server.py   # 125 unit tests + 16 integration (skipped)
```

## Design notes

- **Game-thread marshaling.** Every UObject-touching operation hops back to the game thread via `AsyncTask(ENamedThreads::GameThread, ...)` and uses `TPromise<FString>` / `TFuture<FString>` for thread-safe result return + a 10-second deadline. Pattern documented in `TCPServer.cpp` and reused identically across all tools.
- **JSON contract.** Every response is `{"ok": bool, ...}`. Errors carry `error` and `detail` fields, sometimes `hint` for the most likely cause. The plugin uses UE's built-in `EscapeJsonString` from `Serialization/JsonWriter.h` (which also adds quotes — a real gotcha caught during development).
- **Anchor names.** LLMs reference nodes by user-given anchor names (e.g., `"print_hello"`), never by GUID. Anchors live in `UEdGraphNode::NodeComment`, which means the LLM's labels are visible in the editor and persist across sessions. Function entry anchors default to `"entry"` (v7.1.0).
- **Graph routing.** Almost every graph-writing tool accepts `graph_name=` (v7.7.1). Empty = EventGraph. Otherwise the `ResolveTargetGraph` helper looks the graph up by FName in `FunctionGraphs` / `MacroGraphs` / `UbergraphPages`.
- **FProperty reflection.** v7.1's `set_component_property` is the first reflection-driven tool. `WalkPropertyPath` walks dot-notation into nested struct fields. Dispatch by `FObjectProperty` / `FClassProperty` / `FStructProperty` / else `ImportText_Direct`.
- **Schema-driven dispatcher creation.** v7.1.2's `add_event_dispatcher` mirrors UE's `FBlueprintEditor::OnAddNewDelegate` exactly — `AddMemberVariable(PC_MCDelegate)` + `K2Schema->CreateFunctionGraphTerminators` + `Blueprint->DelegateSignatureGraphs.Add` + compile. Without all six steps the compiler doesn't materialize a `FMulticastDelegateProperty`.
- **No upstream fork.** Inspired by chongdashu/flopperam for architecture choices, re-derived from scratch for ownership and 5.4 support.

## Roadmap

**v8** delivered the agentic closed loop — PIE control + simulated input + log capture
+ MCP command stream visible to the log reader. The LLM-writes-runs-verifies story
fits in one MCP session. See the v8 Agentic-loop demo above and
`tests/test_server.py::test_v8_agentic_loop_against_real_plugin` for the canonical
end-to-end shape.

Possible future directions:
- **Auto-migration of legacy dispatchers** — auto-detect + repair pre-v7.1.2
  damaged dispatchers on BP load, removing the manual `migrate_dispatchers`
  step (currently the only standing limitation, documented as the
  "ghost dispatcher" case).
- **Animation Blueprint surface** — author AnimGraph state machines, blend spaces.
- **UMG Widget Blueprint surface** — author UI hierarchies + bindings.
- **Niagara node surface** — emitter modules, parameter bindings.
- **Headless integration test harness** — run the test BPs in CI without a human-launched
  editor window.

## Acknowledgments

- [`chongdashu/unreal-mcp`](https://github.com/chongdashu/unreal-mcp) and [`flopperam/unreal-engine-mcp`](https://github.com/flopperam/unreal-engine-mcp) for architectural reference. This project is independently written (no copied code) but informed by their public designs.
- The [MCP Python SDK](https://github.com/modelcontextprotocol/python-sdk) team — FastMCP made the server side a small file.

## License

MIT — see [LICENSE](LICENSE).
