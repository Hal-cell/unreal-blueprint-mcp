# Changelog

All notable changes to **unreal-blueprint-mcp** are documented here. Format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/) loosely; versioning is informal (v0…v8) tracking the spike sequence rather than strict SemVer.

Each entry lists the **growth in tool surface**, **bugs fixed**, and **翻车点 (gotchas)** discovered — the last is intentional, the spike-results.md vault notes archive them in detail.

## [Unreleased]

Likely directions for **v8**:
- PIE control (start / stop / read state)
- Simulated input (keyboard / mouse / gamepad press during PIE)
- Output Log capture (read what `PrintString` produced, surface compile warnings)
- Goal: agentic loop — LLM writes → runs → reads log → iterates without human

---

## [v7.7.1] — 2026-05-21

### Added
- **18 graph-writing tools now support `graph_name`** — extended from v7.0.0's
  core 5 (`add_node`, `connect_pins`, `set_pin_default`, `add_branch`, `add_cast`)
  to all 13 remaining: `add_custom_event`, `add_variable_get`, `add_variable_set`,
  `add_macro`, `add_self_reference`, `delete_node`, `disconnect_pins`,
  `call_blueprint_function`, `add_switch`, `add_sequence`, `add_make_array`,
  `add_select`, `add_make_struct`, `add_break_struct`, and the 3 dispatcher tools.

### Internal
- Pattern B body check (`if (UbergraphPages.Num() == 0) ... UbergraphPages[0]`)
  consolidated via `replace_all` across 7 CmdName-using functions.
- Python tool transformations batched via inline script (16/17 auto, 1 hand-edited).

### Not in scope
- `add_input_key`, `add_enhanced_input_node`, `wire_imc_subscribe` remain
  event-graph-only — input events register at SCS construction time, not in
  function bodies.

### Stats
- C++: +207 lines / -116 lines in `TCPServer.cpp`
- Python: +131 lines / -29 lines in `server.py`
- Tool count: **40** (unchanged — feature extension, not new surface)
- Tests: 106 passing (unchanged — UE-runtime behavior covered by integration tests)

---

## [v7.1.3] — 2026-05-21 — call_blueprint_function auto-compile polish

### Fixed
- **call_blueprint_function** missed function on BPs that hadn't been compiled
  since the function was added (common right after `add_function`). Now detects
  BP-generated `TargetClass` via `ClassGeneratedBy` and auto-compiles the owning
  BP, then retries the lookup once. Native classes skip the fallback.
- Response gains `auto_compiled: true|false` so callers see when the retry fired.

---

## [v7.1.2] — 2026-05-21 — add_event_dispatcher real fix (BUG-1)

### Fixed
- **add_event_dispatcher** REAL fix. v7.1.0's hotfix only added `CompileBlueprint`
  but missed the critical first step: adding a `PC_MCDelegate` member variable.
  Without it the compiler doesn't materialize a `FMulticastDelegateProperty` on
  the GeneratedClass, so downstream `K2Node_CallDelegate` AllocateDefaultPins
  can't resolve the signature → no parameter pins on broadcast nodes.
- Now mirrors UE 5.4's `FBlueprintEditor::OnAddNewDelegate` 6-step flow:
  AddMemberVariable → CreateNewGraph → schema CreateFunctionGraphTerminators →
  AddExtraFunctionFlags → MarkFunctionEntryAsEditable → CompileBlueprint.
- Rollback on partial failure: removes the added variable if CreateNewGraph fails.

### 翻车点 #36 (cumulative)
Reading UE source instead of inferring API: the obvious `AddMemberVariable` step
was missing because UE's editor flow isn't documented anywhere outside the
source itself. Always read `OnAddXxx` editor menu callbacks when shadowing
editor behavior from a plugin.

---

## [v7.1.1] — 2026-05-21 — wire_imc_subscribe chain preservation + cast pin (BUG-5, BUG-6)

### Fixed
- **wire_imc_subscribe** overwrote `begin_play.then` instead of appending when
  the chain walk failed (e.g. mid-chain node has no `then` pin). Now snapshots
  `OriginalNext` before walking; if walk fails, accepts the overwrite then
  reattaches `OriginalNext` at the tail of the subscribe chain. Either way
  the user's chain is preserved.
- **wire_imc_subscribe** internal `Cast<EnhancedInputLocalPlayerSubsystem>`
  wasn't applying the v6.0.2 P5 pin-name normalization, so it produced
  `As Enhanced Input Local Player Subsystem` with spaces. Now overrides
  both `PinName` AND `PinFriendlyName` so ReconstructNode can't regenerate
  the spaced form. Applied to `add_cast` too for consistency.

### Note
- **BUG-7** (`add_mapping_to_imc` not accepting `"Space"` alias) verified
  already fixed by v5.0.1's `ResolveFKeyWithAliases` — no code change.

---

## [v7.1.0] — 2026-05-21 — 4 smoke-test bugs (BUG-1..4)

### Fixed
- **BUG-1** `add_call_dispatcher` missing parameter pins on broadcast node →
  compile_failed. (Partial fix — v7.1.2 completed it.)
- **BUG-2** `add_break_struct` on `HitResult` returned zero member output pins.
  Native-break structs (FHitResult, etc.) need `K2Node_CallFunction` substitution.
  Now detects `HasNativeBreak`/`HasNativeMake` USTRUCT meta and spawns the native
  function node instead of K2Node_BreakStruct/MakeStruct.
- **BUG-3** function-body nodes couldn't connect to FunctionEntry; `get_blueprint`
  didn't show function graphs. Two-part fix:
  - `add_function` tags the auto-created `K2Node_FunctionEntry` with
    `NodeComment="entry"` so `connect_pins(graph_name="MyFunc", from_pin="entry.then", to_pin="X.execute")` works.
  - `get_blueprint` dumps `functions: { <name>: { anchors, connections } }` alongside
    the existing EventGraph dump.
- **BUG-4** `add_switch(int)` off-by-one + internal `NotEqual_IntInt` pin leaked into pins[]:
  - Switch case count now runtime-counts existing output exec pins (excluding `Default`)
    and adds (CaseCount - existing) more — doesn't trust assumed default.
  - `BuildPinsJsonArray` skips `Pin->bHidden`, hiding K2Node_Switch's internal function-ref
    pin (and similar internal pins on other K2Nodes).

---

## [v7.0.1] — 2026-05-20 — UE 5.4 build hotfixes

### Fixed
- `JsonError` forward-declaration so v7.7 `JsonGraphNotFound` could call it.
- Dropped `FBox` from struct whitelist (no `TBaseStructure` specialization in UE 5.4).
- `UK2Node_Select` has no public `AddOptionPinToNode()` in UE 5.4 — default 2 options,
  log warning if user requests more.
- `UK2Node_SwitchEnum::SetEnum` not `BLUEPRINTGRAPH_API` exported — direct-assign
  the public `Enum` field instead (same workaround pattern as v6's `GetSubsystemFromPC`).

---

## [v7.0.0] — 2026-05-20 — 12 new tools + function-body editing

### Added
- **`set_component_property`** (v7.1 spike) — FProperty reflection. Sets component
  template defaults like `StaticMeshComponent::StaticMesh` asset, `BoxComponent::BoxExtent`,
  `PrimitiveComponent::BodyInstance.CollisionProfileName`. Dot-notation for nested struct
  fields. Object/Class/Struct/primitive dispatch via FObjectProperty/FClassProperty/
  ImportText_Direct.
- **`add_switch`** + **`add_sequence`** + **`add_make_array`** + **`add_select`** (v7.2)
  — K2Node batch. Switch flavors: int / string / name / enum. Select limited to 2 options
  in UE 5.4 (`AddOptionPinToNode` not public).
- **`add_make_struct`** + **`add_break_struct`** (v7.3) — any struct via short-name
  whitelist (Vector / Rotator / Transform / HitResult / …) or qualified path
  `/Script/Engine.HitResult`. Native-break detection in v7.1.0.
- **`add_event_dispatcher`** + **`add_call_dispatcher`** + **`add_bind_dispatcher`**
  + **`add_unbind_dispatcher`** (v7.6) — multicast delegate / observer pattern.
  Mirrors `FBlueprintEditor::OnAddNewDelegate` 6-step flow (with v7.1.2 hotfix).
- **`save_blueprint`** (v7.8) — explicit `UEditorAssetLibrary::SaveAsset` wrapper.

### Extended
- **`add_variable`** (v7.4) — accepts `object:Actor`, `class:Pawn`, `object:Actor[]`
  via `object:` / `class:` prefix parsing in `ResolveVariablePinType`.
- **`add_custom_event`** (v7.5) — `params=[{name,type}]` list with full type syntax
  (primitives + arrays + object/class refs). Uses `CreateUserDefinedPin` with
  `EGPD_Output`.
- **`add_node`** / **`connect_pins`** / **`set_pin_default`** / **`add_branch`** /
  **`add_cast`** (v7.7) — `graph_name=` kwarg routes the operation into a named
  function/macro graph instead of EventGraph. Default empty = EventGraph (backwards
  compatible). `ResolveTargetGraph` helper handles lookup.

### Stats
- Tools: **28 → 40** (+12)
- Unit tests: **61 → 106** (+45)
- Integration tests: **7 → 13** (+6, gated on running UE editor)
- Plugin dylib: **568 KB → 688 KB** (+120 KB)
- C++ main file: ~3400 → ~4700 lines

### 翻车点 #28-35
v0.0 forward-decl JsonError · FBox no TBaseStructure · SetEnum not exported ·
AddOptionPinToNode doesn't exist · CallDispatcher needs full OnAddNewDelegate flow ·
BreakHitResult needs native function substitution · FunctionEntry needs explicit anchor ·
SwitchInt off-by-one + hidden pin filter

---

## [v6] — 2026-05-20 — wire_imc_subscribe + P0-P7 hotfix bundle

### Added
- **`wire_imc_subscribe`** — one-shot builder for IMC runtime subscribe chain
  (BeginPlay → GetPlayerController → GetSubsystem → Cast → AddMappingContext).
- **`call_blueprint_function`** `target_pin=` extension — auto-wires self pin from
  a source pin.

### Fixed (v6.0.2 - v6.0.4)
- **P0** `wire_imc_subscribe` Class pin / 3rd connection / Cast insertion
- **P1** recv loop drained beyond 8KB (TCP truncation gone)
- **P3** `set_pin_default` accepts class/object refs via `TrySetDefaultObject`
- **P4** `get_blueprint` surfaces object/class defaults + variable `container` tag
- **P5** `add_cast` pin names consistent (`AsClassName`, no spaces)
- **P6** `wire_imc_subscribe` appends to existing BeginPlay chain instead of overwriting
- **P7** `node_<guid>` anchors are bidirectional in `connect_pins`

### 翻车点
`GetSubsystemFromPC` K2Node not exported → use BP-library function directly ·
8KB recv truncation lost large `get_blueprint` JSON

---

## [v5] — 2026-05-20 — Enhanced Input + functions + array vars

### Added
- **Enhanced Input**: `create_input_action`, `create_input_mapping_context`,
  `add_mapping_to_imc`, `add_enhanced_input_node` (`K2Node_EnhancedInputAction`).
- **User functions**: `add_function` (empty graph), `call_blueprint_function`
  (cross-BP function call, native or BP target).
- **Array variable types**: `add_variable` accepts `int[]`, `float[]`, `string[]`,
  `bool[]`, `name[]`, `object:Actor[]` etc. via `[]` suffix.
- **+30 math/system/array short-names** in `add_node` whitelist:
  MakeVector, BreakVector, VectorLerp, RandomFloat, IsValid, PrintText,
  GetPlayerController, ApplyDamage, ArrayAdd, ArrayContains, … (full list in
  ResolveFunctionShortName).

### Fixed
- **v5.0.1** FKey alias: `"Space"` auto-resolves to `"SpaceBar"`, similar for
  Esc/Escape, Ctrl/Control, etc.

### 翻车点
EnhancedInputEditor module doesn't exist (use EnhancedInput) ·
K2Node_EnhancedInputAction is in InputBlueprintNodes module (not BlueprintGraph)

---

## [v4] — 2026-05-20 — macros, self, input, destructive ops, struct pin defaults

### Added
- **`add_macro`** — `K2Node_MacroInstance` for ForEachLoop / ForLoop / WhileLoop /
  FlipFlop / DoOnce / Gate / IsValid from StandardMacros library.
- **`add_self_reference`** — `K2Node_Self`.
- **`add_input_key`** — `K2Node_InputKey` (legacy input, keyboard/mouse/gamepad).
- **`delete_node`** — destroy node + break all connections.
- **`disconnect_pins`** — break a single pin link.
- **Struct types in `set_pin_default`** — Vector `(X=1,Y=2,Z=3)`, Rotator `(P=,Y=,R=)`,
  Color/LinearColor `(R=,G=,B=,A=)`. Also shorthand `1,2,3` accepted.

---

## [v3] — 2026-05-20 — flow control

### Added
- **`add_branch`** — `K2Node_IfThenElse` (the if/else of Blueprints).
- **`add_cast`** — `K2Node_DynamicCast`. Class whitelist: Pawn, Character, Actor,
  PlayerController, PlayerCameraManager, GameMode, GameModeBase, PlayerState, HUD.
  Or any UClass name.

---

## [v2] — 2026-05-20 — get_blueprint introspection

### Added
- **`get_blueprint`** — full snapshot of a BP: anchors, pin info, connections,
  variables, components. Use BEFORE writing so the LLM stops blind-writing.

### Fixed (v2.0.1)
- Disabled `tick` node's NodeComment is UE-set instructional text → for
  `K2Node_Event`, prefer reverse-mapped well-known name over NodeComment.
- `K2Node_CustomEvent` IS-A `K2Node_Event` → cast CustomEvent FIRST so
  CustomFunctionName reads correctly (event_name was null otherwise).

---

## [v1] — 2026-05-20 — components, custom events, variables

### Added
- **`add_component`** — SCS component (BoxCollision, SphereCollision, StaticMesh,
  Camera, PointLight, Audio, ...).
- **`add_custom_event`** — `K2Node_CustomEvent` (red node) for delegate targets.
- **`add_variable`** — member variable (bool, int, float, string, name, text, TimerHandle).
- **`add_variable_get` / `add_variable_set`** — read/write nodes for BP variables.
- **Auto-spawn well-known events** in `connect_pins`: `begin_play`, `tick`,
  `actor_begin_overlap`, `actor_end_overlap`, `hit`, `destroyed`, `end_play`.

### Demo
Collision-timer: `BP_CollisionTimer` (Actor) with BoxCollision trigger + TimerHandle var
+ ClearTimer-on-EndOverlap → walks-in-stays-3-sec triggers print, walks-out cancels.

---

## [v0] — 2026-05-20 — initial spike (B0–B6)

### Added
- **Architecture**: Python MCP server (FastMCP) ↔ TCP `localhost:55558` ↔
  UE 5.4 C++ plugin. Game-thread marshaling via `TPromise<FString>` / `TFuture<FString>` +
  `AsyncTask(ENamedThreads::GameThread, ...)` + 10s deadline.
- **`ping_ue`** — health check.
- **`echo`** — MCP stdio plumbing sanity test.
- **`create_blueprint`** — new BP asset in `/Game/...`, parent class from whitelist
  (Actor / Pawn / Character / Object / ActorComponent).
- **`add_node`** — `K2Node_CallFunction` from whitelist (PrintString, Delay,
  SetTimerByEvent, ClearAndInvalidateTimerByHandle) or fully qualified name.
- **`set_pin_default`** — primitives (string/name/text/int/int64/real/bool/byte).
- **`connect_pins`** — wire two pins (schema-validated via K2Schema).
- **`compile_blueprint`** — `FKismetEditorUtilities::CompileBlueprint`.
- **`spawn_actor`** — place compiled BP into current level (via
  `UEditorActorSubsystem`).

### 翻车点 (initial 10)
- FastMCP 1.27 doesn't have `.fn` attribute — call decorated function directly.
- pytest in optional `[dev]` deps → `uv sync --extra dev`.
- `.python-version` location matters (must be `server/`).
- System Python 3.8 too old → `uv python install 3.11`.
- Claude Desktop GUI doesn't inherit shell PATH → absolute path to `uv` in config.
- UE generic "could not be compiled" — run UBT manually to see real error.
- `EscapeJsonString` name collision with UE's built-in — drop custom.
- UE's `EscapeJsonString` "Also adds the quotes" — `%s` not `\"%s\"`.
- `.uplugin` needs `EditorScriptingUtilities` plugin dep.
- Symlink (not copy) `plugin/BlueprintMCP` into `<UE_PROJECT>/Plugins/` for fast iteration.
