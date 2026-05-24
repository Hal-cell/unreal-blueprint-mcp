# unreal-blueprint-mcp

> Write Unreal Engine 5 Blueprints by talking to Claude. A small but complete MCP server + UE plugin.

Say it: *"Make a Blueprint that prints 'hello world' on BeginPlay, then spawn it."*
Get it: an actual `.uasset`, wired graph, compiled, and an instance sitting in your level — ready to PIE.

[![v9.17.0](https://img.shields.io/badge/version-v9.17.0-brightgreen)](#status)
[![89 tools](https://img.shields.io/badge/tools-89-blue)](#tools)
[![274 tests](https://img.shields.io/badge/tests-274%20passing-success)](#requirements)
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
- **Multi-surface coverage** — door-openers for **AnimGraph** (v9.0/v9.2) + **Niagara** (v9.3) + **UMG** (v9.4) so the LLM can author beyond just gameplay BPs
- **Headless CI test harness** — `scripts/run_headless_ci.sh` boots a commandlet in `-nullrhi` mode, runs the integration suite, exits cleanly (v9.6+)
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
| **v8.1.0** | ✅ `migrate_dispatchers` ghost-detection + opt-in recreate (full 3-mode coverage) |
| **v8.2.0** | ✅ Integration test harness (`requires_ue_editor()` decorator + `scripts/run_integration_tests.sh`) |
| **v8.2.1** | ✅ Swept 14 stale `_against_real_plugin` tests; hardened agentic-loop test |
| **v9.0.0** | ✅ `create_anim_blueprint` — AnimGraph domain opens |
| **v9.1.0** | ✅ Asset/class discovery: `list_assets` / `list_skeletons` / `list_meshes` / `list_blueprints` / `list_classes` |
| **v9.2.0** | ✅ AnimGraph FSM: `add_anim_state_machine` / `add_anim_state` / `add_anim_transition` / `set_anim_state_pose` |
| **v9.3.0** | ✅ `create_niagara_system` — Niagara VFX domain opens + `list_assets` non-Engine class fallback |
| **v9.4.0** | ✅ `create_widget_blueprint` (UMG door-opener) + `save_all` (no-prompt save-all-dirty) |
| **v9.5.0** | ✅ Silent dispatcher migration: `auto_migrate_dispatchers` + `auto_migrate_all_dispatchers` (Python-only) |
| **v9.6.0** | ✅ Headless CI: `BlueprintMCPRun` commandlet + `shutdown_editor` + `scripts/run_headless_ci.sh` |
| **v9.7.0** | ✅ Level/instance ops: `list_level_actors` / `get_actor_transform` / `set_actor_transform` / `set_actor_property` / `delete_actor` — LLM no longer blind to the scene |
| **v9.8.0** | ✅ BP/variable lifecycle: `delete_blueprint` / `delete_variable` / `set_variable_flags` + `add_variable(instance_editable=)` |
| **v9.9.0** | ✅ PIE input enhancements: `pie_press_key(duration_sec=)` + `pie_set_player_location` + `pie_move_player` — LLM can now actually walk into a trigger box |
| **v9.10.0** | ✅ PIE player rotation: `pie_set_player_rotation` (SetControlRotation = FPS look) + `pie_move_player(face_movement=)` — character turns to face direction instead of strafing |
| **v9.11.0** | ✅ `spawn_actor` persistence fix (level pkg now marked dirty) + `rotation=` kwarg + actor bounds in `get_actor_transform` + new `get_actor_bounds` (precise placement against existing geometry) |
| **v9.12.0** | ✅ Sizing tools: `get_player_capsule` (radius / half_height / diameter / full_height) + `spawn_actor(scale=)` (full-pose one-call) + `pie_set_player_location(snap_to_ground=True)` (line trace + capsule offset). LLM no longer blind to size when laying out corridors/doors |
| **v9.13.0** | ✅ `add_component_get` (by-name SCS component ref node — closes "GetComponentByClass-only-finds-first" gap) + WP-aware spawn persistence (`AActor::MarkPackageDirty` for external actor files) + `add_node` invalid_node_type format hint + `set_pin_default` docs fix (class pins always worked) |
| **v9.14.0** | ✅ `add_select` `num_options` actually grows past 2 (was silently capped — closes rev8 ISSUE-1). N-way data Select now usable in one node instead of Switch + N VariableSet workaround |
| **v9.15.0** | ✅ Material subsystem door-opener: `create_material` + `add_material_expression` + `set_material_expression_property` + `connect_material_pins` + `connect_material_output`. Plus `set_component_property` array-index syntax (`OverrideMaterials[0]`). LLM can now build height-color / param materials end-to-end |
| **v9.16.0** | ✅ Material subsystem completion: `compile_material` (= "Apply" button, 75s timeout) + `set_material_property` (material-level UPROPERTYs incl. `bUsedWithInstancedStaticMeshes` for ISM) + `delete_material_expression` (with auto-cleanup of dangling refs) + `disconnect_material_pins` (input form + `output:Name` form). Closes rev9 ISSUE-1/2/3 |
| **v9.17.0** | ✅ `add_function(params, returns)` (functions can now have inputs/outputs) + `add_property_set`/`add_property_get` (Set/Get nodes for properties on EXTERNAL objects like `PlayerController.bShowMouseCursor`) + `add_node` "did you mean?" hint on function_not_found (catches UE-version renames). Closes rev10 ISSUE-1/2/3 |
| **Unit tests** | **274 passing**, 10 integration tests gated on a running UE editor (GUI 10/10, headless 8/10 + 2 explicit skips) |
| **Plugin binary** | **~1.0 MB** dylib on macOS / UE 5.4.4 |

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
uv run pytest    # → 203 passed, 10 skipped (integration tests)
```

### 4. Optional — headless CI mode

For CI pipelines or clean local test cycles, `scripts/run_headless_ci.sh` boots
UE via the `BlueprintMCPRun` commandlet in `-nullrhi -unattended` mode, runs
the integration suite, and exits cleanly. No GUI editor required.

```bash
./scripts/run_headless_ci.sh
# → boots UE-Cmd → polls TCP → pytest → shutdown_editor → exit
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

**74 total.** Tools marked **(v7)** are new or significantly extended in v7; **(v8)** are the agentic-loop primitives; **(v9)** opens new editor surfaces (AnimGraph / UMG / Niagara) + headless CI + closes the 7 feature-request gaps from the 2026-05-21 review (level ops / BP lifecycle / PIE input). Almost every graph-writing tool below accepts an optional `graph_name=` kwarg (v7.7.1+) — default empty = EventGraph, pass a function/macro name to operate inside that graph's body.

### Asset & project

| Tool | What it does |
|------|--------------|
| `ping_ue` | Health check: are UE + the plugin alive? Returns `plugin_version` + `build_date` (v8.0.2+) |
| `echo` | MCP stdio plumbing sanity test |
| `create_blueprint` | New BP asset in `/Game/...`, parent class from whitelist |
| `compile_blueprint` | `FKismetEditorUtilities::CompileBlueprint` |
| `spawn_actor` | Place a compiled BP instance into the current level. **v9.11** adds optional `rotation=[P,Y,R]` kwarg + marks level dirty so `save_all` persists the spawn. **v9.12** adds optional `scale=[X,Y,Z]` for full-pose one-call spawn (no `(1,1,1)` intermediate) |
| **`save_blueprint`** (v7) | Explicit `UEditorAssetLibrary::SaveAsset` |
| **`save_all`** (v9.4) | Silently save every dirty package — call before any UE kill/restart |
| **`shutdown_editor`** (v9.6) | Clean editor exit — works in BOTH GUI and headless commandlet modes |
| **`delete_blueprint`** (v9.8) | Delete an entire BP asset (defensive class check refuses non-UBlueprint assets) |

### Introspection

| Tool | What it does |
|------|--------------|
| **`get_blueprint`** (v2, **v7-extended**) | **Snapshot of a BP — anchors / pins / connections / variables / components / `functions`. Call this BEFORE writing.** |

### Component & variable management

| Tool | What it does |
|------|--------------|
| `add_component` | Add a component to the BP's SCS (BoxCollision, StaticMesh, Camera, ...) |
| **`set_component_property`** (v7) | **Set component template defaults via FProperty reflection: mesh asset, box extent, collision preset, with dot-notation for nested struct fields (`BodyInstance.CollisionProfileName`)** |
| `add_variable` | Add a member variable. Types: primitives, `TimerHandle`, arrays, **`object:Actor` / `class:Pawn` references (v7)**. v9.8 adds `instance_editable=` kwarg |
| **`set_variable_flags`** (v9.8) | Flip flags on an existing var: `instance_editable` / `blueprint_read_only` / `expose_on_spawn` (tri-state — None = unchanged) |
| **`delete_variable`** (v9.8) | Remove a member variable (recompile + save) |
| `add_variable_get` / `add_variable_set` | Read/write nodes for BP variables |
| **`add_component_get`** (v9.13) | By-name Get node for one of the BP's own SCS components (or inherited UPROPERTY component). Closes the "GetComponentByClass only finds the first instance" gap |

### Function & dispatcher authoring

| Tool | What it does |
|------|--------------|
| `add_function` | Create a user function graph in the BP (entry anchor `entry`). **v9.17 adds `params=[{name,type}]` + `returns=[{name,type}]`** — function-result node is anchored `result` |
| **`add_property_set`** (v9.17) | Set a UPROPERTY on an EXTERNAL object (e.g. `PlayerController.bShowMouseCursor`). `K2Node_VariableSet` with `SetExternalMember`. Pin layout includes a `Target` input |
| **`add_property_get`** (v9.17) | Symmetric Get node — read a UPROPERTY from an external object |
| `call_blueprint_function` | Call a function on another class — native or BP class path. **Auto-compiles target BP on function miss (v7.1.3)** |
| **`add_event_dispatcher`** (v7) | Create a multicast delegate on the BP (signature graph + member variable + auto-compile) |
| **`add_call_dispatcher`** (v7) | `K2Node_CallDelegate` — broadcast the dispatcher |
| **`add_bind_dispatcher`** (v7) | `K2Node_AddDelegate` — bind a custom event to the dispatcher |
| **`add_unbind_dispatcher`** (v7) | `K2Node_RemoveDelegate` — unbind |
| **`delete_event_dispatcher`** (v8) | Remove a dispatcher's signature graph + member variable (legacy recovery + cleanup) |
| **`migrate_dispatchers`** (v8) | Programmatic repair: backfill missing PC_MCDelegate member variable on pre-v7.1.2 dispatchers. v8.1+ also detects + opt-in recreates "ghost" dispatchers |
| **`auto_migrate_dispatchers`** (v9.5) | Convenience alias — silently fix all 3 dispatcher damage modes in one BP |
| **`auto_migrate_all_dispatchers`** (v9.5) | Project-wide sweep: list_blueprints → fix each → aggregate report. The "I just upgraded the plugin" command |

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

### Agentic closed loop (v8 + v9.9)

| Tool | What it does |
|------|--------------|
| **`start_pie`** (v8) | Begin a PIE session (`GEditor->RequestPlaySession`). Returns `queued:true` — wait a tick before `pie_press_key` |
| **`stop_pie`** (v8) | End the active PIE session (`RequestEndPlayMap`) |
| **`is_pie_running`** (v8) | Query PIE state — `running` (active) + `start_queued` (requested but not yet ticked) |
| **`pie_press_key`** (v8, **v9.9-extended**) | Simulate key on `APlayerController`. **v9.9 adds `duration_sec=` for held keys** (non-blocking — release scheduled via FTSTicker) |
| **`pie_set_player_location`** (v9.9, **v9.12-extended**) | Teleport the controlled pawn to a world-space location. **v9.12 adds `snap_to_ground=True`** — line-traces down for the floor and offsets by capsule half-height, so the LLM doesn't have to guess Z |
| **`get_player_capsule`** (v9.12) | Read the PIE player's `UCapsuleComponent` (or `GetSimpleCollisionCylinder` fallback) — returns `radius` / `half_height` / `diameter` / `full_height`. The "how wide a corridor needs to be" answer |
| **`pie_set_player_rotation`** (v9.10) | Set the FPS view direction via `APlayerController::SetControlRotation`. On Character pawns with `bUseControllerRotationYaw=true`, the mesh follows yaw next tick |
| **`pie_move_player`** (v9.9, **v9.10-extended**) | Simulate continuous movement input — equivalent to holding WASD. Per-tick `AddMovementInput(dir, scale)` via FTSTicker. **v9.10 adds `face_movement=` to turn the controller to face direction first** (fixes FPS strafe-walk weirdness) |
| **`read_log_capture`** (v8) | Read recent UE log lines from a thread-safe FOutputDevice buffer. Filter by `category` (substring) / `verbosity` / `contains` / `max_lines`. **Sees MCP commands at category `BlueprintMCP_TCP` (v8.0.1+)** |
| **`clear_log_capture`** (v8) | Drop the log buffer before triggering an action |

### Level / instance manipulation (v9.7)

| Tool | What it does |
|------|--------------|
| **`list_level_actors`** (v9.7) | `UEditorActorSubsystem::GetAllLevelActors` + class + name filter. Returns `[{name, label, class, location}, ...]`. The LLM is no longer blind to the scene |
| **`get_actor_transform`** (v9.7, **v9.11-extended**) | World-space location / rotation / scale + **`bounds_origin` / `bounds_extent`** (v9.11 — world OBB half-extent) of an actor |
| **`get_actor_bounds`** (v9.11) | Standalone bounds query: `world_origin/extent`, pre-computed `world_min/max`, `mesh_local_extent` (pre-scale asset bounds), `mesh_asset` path. For precise placement against existing geometry |
| **`set_actor_transform`** (v9.7) | Move / rotate / scale a single instance (no re-spawn). Any of location/rotation/scale may be omitted |
| **`set_actor_property`** (v9.7) | Per-instance FProperty setter (different from `set_component_property` — that writes to the BP CDO). For AActor-typed properties, value can be **another actor's name** (resolved against the level) — canonical "double portal" wiring |
| **`delete_actor`** (v9.7) | Remove an actor from the level (`DestroyActor`) |

### Asset/class discovery (v9.1)

| Tool | What it does |
|------|--------------|
| **`list_assets`** (v9.1) | `IAssetRegistry::GetAssetsByClass` + path filter. Class arg is class name (`StaticMesh`, `NiagaraSystem`) or `/Script/Module.Class`. v9.3+ falls back to name-match for non-Engine classes |
| **`list_skeletons`** (v9.1) | `USkeleton` shortcut — use to find a skeleton for `create_anim_blueprint` |
| **`list_meshes`** (v9.1) | `StaticMesh + SkeletalMesh` (batched in one game-thread hop) |
| **`list_blueprints`** (v9.1) | `Blueprint` shortcut |
| **`list_classes`** (v9.1) | Walk loaded `UClass`es via `TObjectIterator`. Filter by `parent_class` / `name_contains` / `native_only` |

### AnimGraph (v9.0 + v9.2)

| Tool | What it does |
|------|--------------|
| **`create_anim_blueprint`** (v9.0) | Blank `UAnimBlueprint` via `UAnimBlueprintFactory` (parent = `UAnimInstance`, target = user-supplied `USkeleton`) |
| **`add_anim_state_machine`** (v9.2) | Spawn `UAnimGraphNode_StateMachine` in the AnimGraph; UE auto-creates interior `EditorStateMachineGraph` |
| **`add_anim_state`** (v9.2) | Spawn `UAnimStateNode` inside a named state machine; auto-creates the state's interior `BoundGraph` |
| **`add_anim_transition`** (v9.2) | `UAnimStateTransitionNode` + canonical `CreateConnections(From, To)` |
| **`set_anim_state_pose`** (v9.2) | Load `UAnimSequence`, validate skeleton match, wire SequencePlayer pose into state's `GetPoseSinkPinInsideState` |

### UMG / Widget Blueprint (v9.4)

| Tool | What it does |
|------|--------------|
| **`create_widget_blueprint`** (v9.4) | Blank `UWidgetBlueprint` via `UWidgetBlueprintFactory` (parent = `UUserWidget` by default, or a user-supplied subclass) |

### Niagara VFX (v9.3)

| Tool | What it does |
|------|--------------|
| **`create_niagara_system`** (v9.3) | Blank `UNiagaraSystem` via `UNiagaraSystemFactoryNew` (resolved at runtime — factory class is not `NIAGARAEDITOR_API`-exported) |

### Material subsystem (v9.15)

Anchoring + pin-ref scheme mirrors v0 K2 BPs but applied to material graphs.
Batch flow: ops mark dirty only — caller runs `save_all()` at the end of a
batch to trigger one shader recompile + save (avoids the per-op 12s timeout).

| Tool | What it does |
|------|--------------|
| **`create_material`** (v9.15) | Blank `UMaterial` via `UMaterialFactoryNew`. Domain Surface / DefaultLit |
| **`add_material_expression`** (v9.15) | Add a `UMaterialExpression` subclass node. Short names (`Lerp` / `Mask` / `WorldPos` / `Constant3Vector` / `ScalarParameter` / etc.) or `/Script/Engine.MaterialExpressionX` full path |
| **`set_material_expression_property`** (v9.15) | Set a UPROPERTY on an expression — FProperty reflection. `ComponentMask.R/G/B/A`, `Constant3Vector.Constant`, `ScalarParameter.DefaultValue`, etc. |
| **`connect_material_pins`** (v9.15) | Wire `from_pin` ("anchor" / "anchor.0") into `to_pin` ("anchor.InputName"). Uses `FExpressionInput::Connect` |
| **`connect_material_output`** (v9.15) | Wire into one of UMaterial's outputs: `BaseColor` / `EmissiveColor` / `Metallic` / `Roughness` / `Normal` / `Opacity` / `WorldPositionOffset` / etc. |
| **`compile_material`** (v9.16) | The "Apply" button. `PostEditChange` + `ForceRecompileForRendering` + save. Server budget 60s (shader compile is slow); Python wrapper uses 75s socket timeout |
| **`set_material_property`** (v9.16) | Material-level UPROPERTY (NOT inside the graph). Critical for ISM: `bUsedWithInstancedStaticMeshes=true`. Also `TwoSided` / `BlendMode` / `ShadingModel` / `MaterialDomain` |
| **`delete_material_expression`** (v9.16) | Remove an expression. Auto-cleans dangling refs — walks all other expressions + material outputs, clears FExpressionInput pointing to the target |
| **`disconnect_material_pins`** (v9.16) | Break a connection. `to_pin="anchor.InputName"` for an expression input, `to_pin="output:BaseColor"` for a material output |

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
├── plugin/BlueprintMCP/                            # UE C++ plugin (drop into <UE_PROJECT>/Plugins/)
│   ├── BlueprintMCP.uplugin
│   └── Source/BlueprintMCP/                        # ~7400 lines C++ (v9.9.0)
│       ├── Private/TCPServer.cpp                   # Main dispatch + all OnGameThread helpers
│       ├── Private/BlueprintMCPRunCommandlet.cpp   # v9.6 — headless CI entry point
│       └── Public/BlueprintMCPRunCommandlet.h
├── scripts/
│   ├── run_integration_tests.sh                    # v8.2 — pytest against running GUI editor
│   └── run_headless_ci.sh                          # v9.6 — boots UE-Cmd, runs tests, cleans up
└── server/                                         # Python MCP server (FastMCP)
    ├── pyproject.toml
    ├── unreal_blueprint_mcp/
    │   └── server.py                               # ~3000 lines Python (74 @mcp.tool decorators)
    └── tests/
        ├── conftest.py                             # requires_ue_editor() + skip_if_headless()
        └── test_server.py                          # 203 unit + 10 integration tests
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
+ MCP command stream visible to the log reader.

**v9** opened three new editor surfaces (AnimGraph / UMG / Niagara), shipped
the headless CI test harness, and closed all 7 feature-request gaps from the
2026-05-21 agentic-loop review:
- **v9.0/v9.2** — `create_anim_blueprint` + FSM authoring (`add_anim_state_machine`,
  `add_anim_state`, `add_anim_transition`, `set_anim_state_pose`)
- **v9.1** — asset/class discovery (`list_assets`, `list_skeletons`, `list_meshes`,
  `list_blueprints`, `list_classes`)
- **v9.3** — `create_niagara_system` + drive-by `list_assets` non-Engine class fallback
- **v9.4** — `create_widget_blueprint` (UMG) + `save_all`
- **v9.5** — silent dispatcher auto-migration (`auto_migrate_dispatchers`,
  `auto_migrate_all_dispatchers`)
- **v9.6** — headless CI: `BlueprintMCPRun` commandlet + `shutdown_editor` +
  `scripts/run_headless_ci.sh`
- **v9.7** — level/instance manipulation: `list_level_actors` /
  `get_actor_transform` / `set_actor_transform` / `set_actor_property` /
  `delete_actor` (closes feature gaps #2 / #3 / #6 — the LLM is no longer
  blind to the scene)
- **v9.8** — BP/variable lifecycle: `delete_blueprint` / `delete_variable` /
  `set_variable_flags` + `add_variable(instance_editable=)` (closes gaps
  #1 / #5 / #8)
- **v9.9** — PIE input enhancements: `pie_press_key(duration_sec=)` /
  `pie_set_player_location` / `pie_move_player` (closes gap #7 — the LLM
  can now actually walk a character into a trigger box)

Possible future directions:
- **Widget tree composition** — beyond the v9.4 door-opener: programmatic
  Canvas / Button / Text widget authoring, binding events.
- **Niagara module authoring** — beyond the v9.3 door-opener: add emitters,
  set module parameters, wire material inputs.
- **AnimGraph blend spaces & IK** — extend v9.2's FSM authoring with
  blend-space nodes, two-bone IK, control rigs.
- **Multi-PIE / dedicated-server testing** — extend v8/v9.9's PIE tools to
  support multi-window play + dedicated-server PIE.

## Acknowledgments

- [`chongdashu/unreal-mcp`](https://github.com/chongdashu/unreal-mcp) and [`flopperam/unreal-engine-mcp`](https://github.com/flopperam/unreal-engine-mcp) for architectural reference. This project is independently written (no copied code) but informed by their public designs.
- The [MCP Python SDK](https://github.com/modelcontextprotocol/python-sdk) team — FastMCP made the server side a small file.

## License

MIT — see [LICENSE](LICENSE).
