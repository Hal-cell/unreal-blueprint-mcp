# Changelog

All notable changes to **unreal-blueprint-mcp** are documented here. Format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/) loosely; versioning is informal (v0…v8) tracking the spike sequence rather than strict SemVer.

Each entry lists the **growth in tool surface**, **bugs fixed**, and **翻车点 (gotchas)** discovered — the last is intentional, the spike-results.md vault notes archive them in detail.

## [Unreleased]

Everything shipped through v9.14.0.

---

## [v9.14.0] — 2026-05-23 — add_select num_options actually grows (closes rev8 ISSUE-1)

### Fixed

`add_select` silently capped `num_options` at 2 since v7.0.1 — the
note "UK2Node_Select has no public AddOptionPinToNode() in UE 5.4"
was incorrect. `UK2Node_Select::AddInputPin()` IS public (overrides
`IK2Node_AddPinInterface`), and internally:

  - Increments `NumOptionPins`
  - Flips `IndexPin` from `bool` → `int` once we exceed 2
  - Calls `ReconstructNode` to materialize the new Option pin

Loop from 2 (default) up to `NumOptions`, respecting `CanAddPin`
gating. Clamp range `[2, 64]`. `NumOptionPins` itself is private, so
the actual count back from the call comes from `GetOptionPins`
(BLUEPRINTGRAPH_API).

rev8 use case: F-key cycling among 6 floor-animation modes. Previously
required `Switch on Int` + 6 `K2Node_VariableSet` + an intermediate
variable. Now a single `Select` node with `num_options=6`.

### `ping.plugin_version`
"9.13.0" → **"9.14.0"**.

---

## [v9.13.0] — 2026-05-22 — add_component_get + WP-aware spawn persistence + docs/hints (closes rev7 ISSUE-1/2)

### Closes rev7 issues

- **ISSUE-1** (medium) — no way to reference a BP's own components in
  the graph by name. The only workaround was `GetComponentByClass`
  which returns the FIRST component of that class (useless for "two
  StaticMesh components on the same BP").
- **ISSUE-2** (low/docs) — `set_pin_default` docstring said class pins
  return `unsupported_pin_type`; class pins have actually worked since
  v6.0.2. Docs were over-restrictive.

Plus the rev6 ISSUE-1 (spawn persistence) re-confirmed in rev7:
v9.11.0's `MarkPackageDirty` on the LEVEL package wasn't enough on
World Partition maps (FirstPerson template default), because WP saves
actors as per-actor external files. Fix is to mark the ACTOR's
package — `AActor::MarkPackageDirty()` gets the right one in both
WP and non-WP cases.

### Added — 1 new tool + 3 fixes (77 → 78)

- **`add_component_get(blueprint, component_name, anchor_name,
   position_x=0, position_y=0, graph_name="")`** — drops a
   `K2Node_VariableGet` referencing a named SCS component on `self`.
   Identical to dragging the component from UE's Components panel.
   Lookup: SCS first via `Blueprint->SimpleConstructionScript->
   FindSCSNode`, falls back to `Blueprint->GeneratedClass->
   FindPropertyByName` for inherited / native components. Output pin
   is the component's class — wire into `AddInstance.Target` etc.
- **WP-aware `spawn_actor` persistence** — additionally call
  `AActor::MarkPackageDirty()` after spawn. This is the right entry
  point for both WP (marks the per-actor external file) and non-WP
  (marks the level package, same as before). v9.11.0's level-only
  approach was a partial fix that wasn't catching the WP case.
- **`add_node` error hint** — `invalid_node_type` detail now reads:
  `"Got 'PrintString' — node_type must use '<K2NodeClass>:<param>'
  format. Example: 'K2Node_CallFunction:PrintString' or
  fully-qualified 'K2Node_CallFunction:KismetSystemLibrary.PrintString'."`
  Saves a round-trip when the LLM forgets the prefix.
- **`set_pin_default` docstring rewrite** — accurately lists what's
  supported (primitives, common structs, object refs, class refs) and
  what's not (delegate, wildcard, unknown structs). Previously claimed
  `object/class/struct/delegate/wildcard` ALL return error — only the
  last two are still true.

### `ping.plugin_version`
"9.12.0" → **"9.13.0"**.

---

## [v9.12.0] — 2026-05-22 — Sizing tools (closes rev6 ISSUE-1/2/3)

### Three rev6 issues — same root cause

The LLM was BLIND TO SIZE when programmatically building levels:
- **ISSUE-1** — no way to read character capsule → corridor width was
  a guess. User built a 55-unit gap, character is 68 wide, stuck.
- **ISSUE-2** — `spawn_actor` couldn't set scale → 2-call pattern
  (spawn + `set_actor_transform`) with a `(1,1,1)` intermediate state
  that could leak through `save_all` / re-compile.
- **ISSUE-3** — no ground snap → Z was a guess too (corridor floor
  height + capsule half-height = ?).

### Added — 1 new tool + 2 extensions (76 → 77)

- **`spawn_actor(..., scale=None)`** — optional `[X, Y, Z]` scale
  kwarg. Applied immediately after spawn, BEFORE the level package is
  marked dirty — no `(1,1,1)` intermediate state. Server also accepts
  `scale_x/y/z` individually for JSON RPC ergonomics. Response now
  includes `scale`.
- **`get_player_capsule(player_index=0)`** — reads
  `UCapsuleComponent::GetScaledCapsuleRadius/HalfHeight` for Character
  pawns, `GetSimpleCollisionCylinder` fallback for non-Character.
  Returns `radius` / `half_height` plus pre-computed `diameter` and
  `full_height` (no math required by caller). PIE-only — the
  character pawn doesn't exist in the editor world.
- **`pie_set_player_location(..., snap_to_ground=False)`** — with
  `snap_to_ground=True`, line-traces down from
  `(X, Y, Z + trace_up_height)` by `trace_down_dist` (defaults
  200 / 10000) on `ECC_Visibility` (ignoring the pawn itself). On
  hit: place pawn at `ground_z + capsule_half_height`. Response
  includes `snapped_to_ground` / `ground_z` / `capsule_half_height` /
  `ground_hit` (the actor name we hit, for verification). No-snap
  path unchanged.

### Verification (live smoke test)

- `spawn_actor(scale=[11,3,2], rotation=[0,45,0])` → `bounds_extent =
  [494.97, 494.97, 100]` — exact AABB math for rotated 1100×300 box.
- `get_player_capsule` on FP template → `radius=55, half_height=96,
  diameter=110, full_height=192` — this is what rev6 needed to set
  corridor width correctly the first time.
- `snap_to_ground` from `Z=5000` → snapped to `ground_z=400.5 +
  half_height=96 = 496.5`; `ground_hit: StaticMeshActor_2`.

### `ping.plugin_version`
"9.11.0" → **"9.12.0"**.

---

## [v9.11.0] — 2026-05-22 — spawn_actor persistence + rotation + actor bounds

### Closes rev5 ISSUE-1 + ISSUE-2

- **ISSUE-1** (medium): `spawn_actor` instances were lost on next editor
  restart because the level package wasn't dirty-marked. The actor was
  in memory but never persisted by `save_all`.
- **ISSUE-2** (low/enhancement): no way to query an actor's world-space
  bounding box → "stick BP_Portal flat against this Cube wall" math
  required guessing the base mesh size.

Plus drive-by from rev5 §二: `spawn_actor` didn't accept a rotation
parameter.

### Added — 1 new tool + 3 extensions (75 → 76)

- **`spawn_actor(..., rotation=None)`** — optional `[Pitch, Yaw, Roll]`
  kwarg (also accepts `rotation_pitch/yaw/roll` individually for JSON
  RPC ergonomics). Response now also includes `actor_label`
  (Outliner display) + applied rotation.
- **`spawn_actor` persistence fix** — after a successful spawn, the
  level's outermost package is marked dirty so `save_all` actually
  persists the spawn to disk.
- **`get_actor_transform`** — response now includes `bounds_origin` and
  `bounds_extent` (world-space OBB, half-extent) from
  `AActor::GetActorBounds`. Existing fields unchanged.
- **`get_actor_bounds(actor)`** — new standalone bounds tool. Returns
  `world_origin/extent`, pre-computed `world_min/max`,
  `mesh_local_extent` (pre-scale asset bounds, when root is a
  StaticMeshComponent), and `mesh_asset` path. Distinguishes
  "100³ cube scaled 3×" from "300³ cube scaled 1×".
- **`list_level_actors(..., include_bounds=False)`** — opt-in flag adds
  per-actor bounds to scan results. Off by default — bounds query has
  per-actor cost.

### Documentation update

`spawn_actor` docstring now warns: `compile_blueprint` triggers
reinstancing of all BP-spawned actors → the underlying UObject is
replaced and `actor_name` changes. After recompile, re-fetch the
current name via `list_level_actors` before using
`set_actor_transform` / `set_actor_property` / `delete_actor`. Don't
cache the post-spawn name across recompiles.

### `ping.plugin_version`
"9.10.0" → **"9.11.0"**.

---

## [v9.10.0] — 2026-05-22 — PIE player rotation control

### Closes the "FPS character strafes sideways into the portal" UX gap

v9.9.0's `pie_move_player` only `AddMovementInput` — no rotation. So
calling `pie_move_player(direction=[0,1,0])` on a first-person character
facing +X made the character strafe sideways into the +Y direction
instead of turning to walk forward. Looks weird.

### Added — 1 new tool + 1 extension (74 → 75)

- **`pie_set_player_rotation(rotation, player_index=0)`** — calls
  `APlayerController::SetControlRotation(FRotator(Pitch,Yaw,Roll))`,
  the source-of-truth for first-person view direction (where mouse-look
  writes). On Character pawns with `bUseControllerRotationYaw=true`
  (the default for FP/TP templates), the mesh follows yaw on the next
  tick. Returns `{requested, applied}` so callers can see when FPS
  Pitch clamps engage.
- **`pie_move_player(..., face_movement=False)`** — extended kwarg.
  `face_movement=True` sets the controller's yaw to face the movement
  direction BEFORE starting the AddMovementInput ticker. Pitch/Roll
  forced to 0 (don't tilt the camera). Single-call "turn-then-walk."

### `ping.plugin_version`
"9.9.0" → **"9.10.0"**.

---

(Originally noted in v9.9 wrap-up as "all 7 feature-request gaps shipped" —
v9.10 is a UX patch on top of #7, not a new gap.)

All four rev4 roadmap items + all 7 feature-request gaps from 2026-05-21
review shipped:
- (Done in v9.0.0–v9.3.0) AnimGraph / UMG / Niagara door-openers — every editor
  subsystem has asset-creation parity.
- (Done in v8.1.0 + v9.5.0) Auto-migration of legacy dispatchers — detect +
  recreate + project-wide silent sweep.
- (Done in v9.6.0) Headless CI test harness — commandlet boots in `-nullrhi`
  mode, pumps the game thread, runs the integration suite, exits cleanly.
- (Done in v9.7.0–v9.9.0) Feature-request gaps from agentic-loop reviews —
  level/instance manipulation, BP/variable lifecycle, PIE input enhancements.

Future work: complete the AnimGraph / UMG / Niagara surfaces (parameter bindings,
widget trees, emitter modules) beyond the door-opener asset creation.

---

## [v9.9.0] — 2026-05-21 — PIE input enhancements

### Closes feature-request gap #7

The LLM can now actually drive a character pawn into a trigger volume to
verify "walk in" gameplay loops — previously `pie_press_key` couldn't
simulate a held W, and there was no way to position the player.

### Added — 2 new tools + 1 extension (72 → 74)

- **`pie_press_key(..., duration_sec=0.0)`** — extended. `duration_sec > 0`
  presses now, then schedules the release via `FTSTicker` after the
  given duration. Non-blocking — returns immediately with `held=true`.
- **`pie_set_player_location(location, player_index=0)`** — teleport the
  PIE pawn via `Pawn->SetActorLocation(loc, TeleportPhysics)`. The
  "drop the player at a test position" tool.
- **`pie_move_player(direction, duration_sec=1.0, scale=1.0)`** —
  simulate continuous movement input. Each game-thread tick calls
  `Pawn->AddMovementInput(direction.Normal, scale)`. Uses an FTSTicker
  that re-arms each tick until duration elapses. Returns immediately
  with `queued=true`. **This is the right tool for character pawns**
  because they use axis bindings — `pie_press_key("W")` only works for
  pawns with explicit Pressed-bindings on W.

### Implementation notes

`WeakObjectPtr<APawn>` in the ticker lambda — if PIE ends between tick
fires (`stop_pie` or pawn destruction) the lambda no-ops instead of
dereferencing a dangling pointer.

### `ping.plugin_version`
"9.8.0" → **"9.9.0"**.

---

## [v9.8.0] — 2026-05-21 — Blueprint / variable lifecycle

### Closes feature-request gaps #1, #5, #8

- #1 — delete an entire BP asset
- #5 — mark variables as Instance Editable (visible in per-instance
  Details panel)
- #8 — delete unwanted variables

### Added — 3 new tools + 1 extension (69 → 72)

- **`add_variable(..., instance_editable=False)`** — extended kwarg.
  When `True`, clears `CPF_DisableEditOnInstance` so the variable
  appears in per-instance Details. Backwards compatible (default False).
- **`set_variable_flags(blueprint, name, instance_editable=None,
   blueprint_read_only=None, expose_on_spawn=None)`** — tri-state flag
   editor for existing variables. `None` = leave unchanged. Recompiles
   so flags propagate to the generated FProperty. `ExposeOnSpawn` is
   metadata; the other two are CPF_* bits.
- **`delete_variable(blueprint, name)`** —
  `FBlueprintEditorUtils::RemoveMemberVariable` + structural-modify +
  recompile + save. For regular member variables; event dispatchers
  go through `delete_event_dispatcher`.
- **`delete_blueprint(path)`** — `UEditorAssetLibrary::DeleteAsset`
  with a defensive class check (refuses to delete non-UBlueprint
  assets — won't fat-finger a texture).

### `ping.plugin_version`
"9.7.0" → **"9.8.0"**.

---

## [v9.7.0] — 2026-05-21 — Level / instance manipulation

### Closes feature-request gaps #2, #3, #6 (the highest-priority block)

Before v9.7.0 the LLM was "blind" to the scene — it could create BPs
and spawn them, but couldn't read what was already in the level or
reposition existing instances without re-spawning duplicates.

### Added — 5 new tools (64 → 69)

- **`list_level_actors(class_filter, name_contains, max_results=500)`** —
  `UEditorActorSubsystem::GetAllLevelActors()` + class + name filter.
  Each result includes `{name, label, class, location}`. The LLM is
  no longer blind to the scene.
- **`get_actor_transform(actor)`** — world-space location / rotation /
  scale of a level actor.
- **`set_actor_transform(actor, location, rotation, scale)`** — move /
  rotate / scale a SINGLE level instance (no re-spawn → no duplicates,
  no `GetActorOfClass-returns-first-instance` trap). Any of the three
  components may be omitted.
- **`set_actor_property(actor, property, value)`** — per-instance
  FProperty setter (DIFFERENT from v7's `set_component_property` which
  writes to the BP CDO). For AActor-typed properties, value can be
  **another actor's name or label** — resolved against the level
  before asset-path fallback. The canonical "double portal" wiring.
- **`delete_actor(actor)`** — `UEditorActorSubsystem::DestroyActor`.

### Actor lookup

All five accept either `GetName()` (returned by `spawn_actor`) or
`GetActorLabel()` (Outliner display).

### `ping.plugin_version`
"9.6.0" → **"9.7.0"**.

---

## [v9.6.0] — 2026-05-21 — Headless CI test harness

### Added — 1 new tool (63 → 64)

- **`UBlueprintMCPRunCommandlet`** — new C++ commandlet (`-run=BlueprintMCPRun`).
  Boots minimal editor environment (`IsEditor=true`), forces a synchronous
  `IAssetRegistry::SearchAllAssets` on /Game so list_*/skeleton lookups work,
  then enters a 60 Hz pump loop that drains the `ENamedThreads::GameThread`
  task queue (critical — `AsyncTask` payloads never run otherwise in commandlet
  mode) + ticks `FTSTicker::GetCoreTicker`.
- **`shutdown_editor`** — TCP command + Python wrapper. Flips
  `UBlueprintMCPRunCommandlet::bShouldExit` AND schedules
  `FPlatformMisc::RequestExit(false)` so it works in BOTH headless commandlet
  AND GUI editor modes. Returns immediately without waiting.
- **`scripts/run_headless_ci.sh`** — boots UnrealEditor-Cmd with
  `-run=BlueprintMCPRun -nullrhi -unattended -nopause -nosplash -nosound`,
  polls TCP up to 180s, runs pytest with `BLUEPRINTMCP_HEADLESS=1` +
  `BLUEPRINTMCP_INTEGRATION=1`, then sends `shutdown_editor` and reaps the
  process (graceful → SIGTERM → SIGKILL fallbacks).
- **`conftest.skip_if_headless(reason)`** — new pytest marker for tests that
  fundamentally need a GUI editor.

### Test results

- **GUI mode**: 8/8 integration tests pass (no regression)
- **Headless mode**: 6/8 pass + 2 explicit skips:
  - `test_v8_agentic_loop_against_real_plugin` (PIE needs game world)
  - `test_create_niagara_system_against_real_plugin` (shader compile races
    Python's 12s socket timeout in cold-boot headless)

### Documented headless limitations

- PIE doesn't tick under `-nullrhi`
- `save_all` returns `saved=false` in commandlet mode even when packages persist
  (UI-notification path skipped; integration test now asserts `ok=true` only)
- Cold-boot Niagara shader compile can exceed Python timeout

### `ping.plugin_version`
"9.4.0" → **"9.6.0"** (skips 9.5.0 — that was Python-only).

---

## [v9.5.0] — 2026-05-21 — Silent dispatcher auto-migration (Python-only)

### Added — 2 new tools (61 → 63)

- **`auto_migrate_dispatchers(blueprint)`** — convenience alias for
  `migrate_dispatchers(blueprint, recreate_ghosts=True)`. Where the v8.1.0
  default is "dry-run + report ghosts," this one actually rebuilds them.
- **`auto_migrate_all_dispatchers(folder="/Game", dry_run=False)`** —
  project-wide sweep: `list_blueprints(folder)` → per-BP fix → aggregate.
  Returns per-BP results + totals + errors[]. Single bad BP doesn't abort.
  Designed for the upgrade scenario: "I just bumped the plugin, fix every
  legacy dispatcher in my project in one shot."

### Pure Python — no plugin changes

`plugin_version` stays at 9.4.0. Server.py + tests only.

### Tests

167 → **172** unit tests (+5: recreate-flag propagation / aggregation /
dry-run flag / per-BP errors / list-failure propagation).

---

## [v9.4.0] — 2026-05-21 — UMG door-opener + save_all

### Added — 2 new tools (59 → 61)

- **`create_widget_blueprint(name, parent_class="", path="/Game/UI")`** —
  opens the UMG surface. Creates a blank `UWidgetBlueprint` via
  `UWidgetBlueprintFactory` (parent = `UUserWidget` by default, or a
  user-supplied subclass). UMGEditor's factory is `MinimalAPI` so we
  link directly (no FindObject dance like Niagara). v9.4.0 scope is
  asset creation only.
- **`save_all()`** — mirrors UE's File → Save All but with no prompts.
  Calls `FEditorFileUtils::SaveDirtyPackages(bPromptUserToSave=false,
  bSaveMapPackages=true, bSaveContentPackages=true)`. Returns
  `{saved, packages_needed_saving}`. Call before any UE editor kill or
  restart to prevent the "Save changes?" dialog on next launch.

### Build deps

`+UMG` `+UMGEditor` in Build.cs PrivateDependencyModuleNames. UMG is an
Engine module (not plugin) — no .uplugin change needed.

### `ping.plugin_version`
"9.3.0" → **"9.4.0"**.

---

## [v9.3.0] — 2026-05-21 — Niagara door-opener

### Added — 1 new tool (58 → 59)

- **`create_niagara_system(name, path="/Game/VFX")`** — opens the Niagara VFX
  surface. Creates a blank `UNiagaraSystem` via `UNiagaraSystemFactoryNew`
  with no `SystemToCopy` / `EmittersToAddToNewSystem` — runs the factory's
  default `InitializeSystem` path (SystemSpawnScript + SystemUpdateScript +
  default effect type).

### Implementation notes (gotchas)

- `UNiagaraSystemFactoryNew` is NOT `NIAGARAEDITOR_API`-exported, so we
  cannot link to its `StaticClass()` symbol directly. **Resolved at runtime
  via `FindObject("/Script/NiagaraEditor.NiagaraSystemFactoryNew")`** and
  instantiated through the `UFactory` base — `IAssetTools::CreateAsset`
  only needs a valid `UFactory*`. Build.cs lists only `"Niagara"` (not
  `"NiagaraEditor"`), avoiding the link error entirely.
- `.uplugin` lists `"Niagara"` as a plugin dependency for proper load
  ordering. (Niagara IS a plugin, unlike UMG which is an Engine module.)

### Drive-by fix surfaced by v9.3.0 integration test

- **`list_assets(asset_class="X")` non-Engine class fallback**. Previously
  assumed the class lived in `/Script/Engine.` — broke for Niagara (and
  would have for UMG/etc.). Now falls back to enumerating assets in path
  and matching by class name when the `/Script/Engine.X` lookup returns empty.

### `ping.plugin_version`
"9.2.0" → **"9.3.0"**.

---

## [v9.2.0] — 2026-05-21 — AnimGraph FSM tools

### Added — 4 new tools (54 → 58)

Builds on v9.0.0's `create_anim_blueprint` to take an AnimBP from empty
asset to fully-wired skeletal animation state machine:

- **`add_anim_state_machine(blueprint, name, pos_x=0, pos_y=0)`** —
  spawn `UAnimGraphNode_StateMachine` in the main AnimGraph. UE's
  `PostPlacedNewNode` auto-creates the interior `EditorStateMachineGraph`.
- **`add_anim_state(blueprint, state_machine, name, pos_x=0, pos_y=0)`** —
  spawn `UAnimStateNode` inside a state machine. Auto-creates the state's
  interior `BoundGraph` (mini AnimGraph for pose).
- **`add_anim_transition(blueprint, state_machine, from_state, to_state)`** —
  `UAnimStateTransitionNode` + canonical `CreateConnections(From, To)`.
- **`set_anim_state_pose(blueprint, state_machine, state, sequence)`** —
  load `UAnimSequence`, validate skeleton matches AnimBP's `TargetSkeleton`,
  find/create `UAnimGraphNode_SequencePlayer` in state's `BoundGraph`,
  wire pose pin to state's `GetPoseSinkPinInsideState`.

### Naming convention

State machines + states are addressed by user-given names stored as
`NodeComment`, consistent with the AnchorName convention from v0.

### Build deps

`+AnimGraph` in Build.cs PrivateDependencyModuleNames.

### `ping.plugin_version`
"9.1.0" → **"9.2.0"**.

---

## [v9.1.0] — 2026-05-21 — Asset/class discovery tools

### Added — 5 new tools (49 → 54)

- **`list_assets(folder, asset_class="", recursive=true, max_results=200)`** —
  base discovery via `IAssetRegistry::GetAssetsByClass` + path filter.
- **`list_skeletons(folder)`** — `USkeleton` shortcut.
- **`list_meshes(folder)`** — `StaticMesh + SkeletalMesh` (batched in one
  game-thread hop).
- **`list_blueprints(folder)`** — `Blueprint` shortcut.
- **`list_classes(parent_class, name_contains, native_only, max_results)`** —
  walk loaded `UClass`es via `TObjectIterator`.

### Bug found + fixed during initial v9.1.0 testing

- **IAssetRegistry game-thread assertion crash**. First attempt called
  `GetAssetsByClass` from the TCP thread → UE crashed with
  `Assertion failed: IsInGameThread() ... Enumerating in-memory assets can
  only be done on the game thread`. Wrapped all 4 asset-listing dispatch
  branches in `AsyncTask(ENamedThreads::GameThread, ...)` with the
  `TPromise/TFuture` pattern. `list_meshes` batches static + skeletal in
  ONE game-thread hop.

### Build deps

`+AssetRegistry` in Build.cs PrivateDependencyModuleNames.

### `ping.plugin_version`
"9.0.0" → **"9.1.0"**.

---

## [v9.0.0] — 2026-05-21 — AnimGraph domain opens

### Added — 1 new tool (48 → 49)

- **`create_anim_blueprint(name, skeleton, path="/Game/Blueprints")`** —
  creates a blank Animation Blueprint via `UAnimBlueprintFactory`
  (parent = `UAnimInstance`, target = user-supplied `USkeleton`).
  Asset opens in the AnimGraph editor. v9.0.0 scope is asset creation
  only — state machine authoring shipped in v9.2.0.

### `ping.plugin_version`
"8.2.1" → **"9.0.0"**.

---

## [v8.2.1] — 2026-05-21 — Integration test cleanup

### Test suite hardening (no functional change)

Swept **14 stale `_against_real_plugin` tests** that were left over from
the v6/v7 mock era and had drifted into "always fail" since the test bed
moved to live UE. Kept the 3 tests that ARE live-meaningful:

- `test_ping_ue_against_real_plugin`
- `test_v8_agentic_loop_against_real_plugin`
- `test_create_anim_blueprint_against_real_plugin` (added later in v9.0)

### Four bugs caught in `test_v8_agentic_loop_against_real_plugin`

1. `add_node` requires `node_type="K2Node_CallFunction:PrintString"` — not
   bare `"PrintString"`. Format is `<K2NodeClass>:<param>`.
2. `start_pie` fails with `pie_already_running` if a previous test left PIE
   running → defensive `is_pie_running` + `stop_pie` + 1s sleep at test start.
3. `start_pie` returns `queued:true` initially → sleep 3s before reading log.
4. `spawn_actor` must be BEFORE `start_pie` — `UEditorActorSubsystem` targets
   the editor world, which is suspended during PIE.

### No dylib changes

`ping.plugin_version` stays at 8.1.0.

---

## [v8.2.0] — 2026-05-21 — Integration test harness

### Added
- **`server/tests/conftest.py`** with `requires_ue_editor()` decorator. Two gates:
  - `BLUEPRINTMCP_INTEGRATION=1` env var (opt-in)
  - UE editor reachable on `127.0.0.1:55558` (cached probe)
  Replaces 16 ad-hoc `@pytest.mark.skip(reason="Requires UE editor + ...")`
  decorators throughout the test file.
- **`scripts/run_integration_tests.sh`** — probes UE first, sets the env var,
  runs pytest filtered to `*_against_real_plugin` tests. Forwards any extra
  pytest flags (`-v`, `--tb=long`, `-k`...).
- **`scripts/README.md`** — usage docs + sketch of a future self-hosted-runner
  GitHub Actions workflow (UE binaries too big / licensed for cloud CI).

### Tests
- 127 unit (unchanged), 16 integration (gated; same set, now reachable via
  the harness rather than per-test `skip(...)`).
- Confirmed: env-var-off run still produces 16 cleanly-skipped tests with
  precise reason strings.

### No dylib changes
This release is server-side + scripts. `ping.plugin_version` stays `"8.1.0"`.

---

## [v8.1.0] — 2026-05-21 — migrate_dispatchers: ghost detection + recreate

### Extended `migrate_dispatchers`
Closes the only remaining roadmap item from rev4 — legacy dispatcher recovery
now covers all three damage modes:

  - **Mode 1 — "graph present, variable missing"** (pre-v7.1.2 partial damage):
    Pass 1 back-fills member variable. (Existed in v8.0.2.)
  - **Mode 2 — "variable present, graph missing"**: Pass 2 detects + reports
    only. (Existed in v8.0.2.)
  - **Mode 3 — "ghost dispatcher" — both missing, but K2Node_CallDelegate /
    AddDelegate / RemoveDelegate nodes still reference the dead name**:
    Pass 3 scans every graph (UbergraphPages + FunctionGraphs + MacroGraphs)
    for `UK2Node_BaseMCDelegate` instances whose
    `DelegateReference.GetMemberName()` doesn't resolve. Collects unique names
    into `ghosts_detected`. With `recreate_ghosts=True`, Pass 4 recreates
    each ghost with empty signature via `CreateDispatcherInternal` (extracted
    from `add_event_dispatcher`).

### Refactoring
- `add_event_dispatcher` core logic extracted into private
  `CreateDispatcherInternal(BP, name, params, types, &OutError)` helper.
  Caller batches `MarkStructurallyModified` + compile + save.

### Response gains 4 fields
`ghosts_detected_count`, `ghosts_detected`, `ghosts_recreated_count`,
`ghosts_recreated`, `recreate_ghosts_requested`.

### Tool count
48 (unchanged — feature extension on existing tool).

### Tests
125 → 127 (+2: dry-run + active recreate).

### `ping.plugin_version`
"8.0.3" → **"8.1.0"**.

### Caveats
- Recreated ghosts have **empty signatures** — pin types of the old caller nodes
  are NOT inferred (documented limit). User can add params manually after.

---

## [v8.0.3] — 2026-05-21 — BUG-A: read_log_capture category substring match

### Fixed
- **`read_log_capture`** category filter now does **real substring match** against
  the extracted `[Category]` token instead of prefix-matching `[%s]` against
  the whole line. So `category="BlueprintMCP"` matches `[LogBlueprintMCP_TCP]`
  as documented. Same fix applied to verbosity filter.

### Doc
- `read_log_capture` docstring lists useful category names
  (`BlueprintMCP_TCP`, `BlueprintUserMessages`, `PlayLevel`, `BlueprintCompile`).
- `migrate_dispatchers` + `delete_event_dispatcher` document the
  "ghost dispatcher" coverage limit (some pre-v7.1.2 BPs lost both signature
  graph and member variable; recovery = `add_event_dispatcher` recreate).

### Internal
- `ping.plugin_version` → `"8.0.3"`.

---

## [v8.0.2] — 2026-05-21 — migrate_dispatchers + plugin_version

### Added
- **`migrate_dispatchers(blueprint=...)`** — scans a BP for pre-v7.1.2 dispatcher
  signature graphs missing the PC_MCDelegate member variable; back-fills the
  variable + recompiles. Idempotent — healthy BPs return `compiled=false` /
  `saved=false`. Reports `migrated` / `already_healthy` / `orphan_variables`
  arrays for full visibility.
- **`ping`** response now includes `plugin_version` (semver, baked into source)
  and `build_date` (`__DATE__` + `__TIME__`) — answers "which dylib is loaded?"
  in one tool call.

### Tool count
46 → **48**

---

## [v8.0.1] — 2026-05-21 — MCP command logging + delete_event_dispatcher

### Added
- **`delete_event_dispatcher(blueprint, dispatcher_name)`** — removes the
  signature graph (via `FBlueprintEditorUtils::RemoveGraph(Recompile)`) AND the
  PC_MCDelegate member variable for a dispatcher. Either piece is optional;
  returns `removed_graph` / `removed_variable` flags showing what was actually
  cleaned. Provides the recovery path for legacy broken dispatchers.

### Fixed
- **OPEN-2** `read_log_capture` couldn't see MCP traffic. Root cause: HandleClient
  used `Verbose` log level which GLog filters out before reaching FOutputDevice.
  Changed both `MCP recv:` and `MCP send:` to `Log` level + 800-char truncation
  to keep buffer readable. LLM self-diagnostic loop now sees every MCP call.

### Tool count
46 → **47**

---

## [v8.0.0] — 2026-05-21 — Agentic closed loop

The big one: LLM writes a Blueprint → spawns → starts PIE → presses keys →
reads the log → verifies its own work — all from one MCP session, no human.

### Added — 6 new tools (40 → 46)

**v8.1 — Log capture (`FBlueprintMCPLogCapture : public FOutputDevice`):**
- Installed by the module at startup as a global on GLog. Captures every
  `UE_LOG` line (including `PrintString` output via `LogBlueprintUserMessages`)
  into a thread-safe 1000-line circular buffer.
- **`read_log_capture(max_lines, category, verbosity, contains)`** — snapshot
  the buffer with optional filters. Each line formatted `[Category][Verbosity] message`.
- **`clear_log_capture()`** — drop the buffer (typical pattern: clear, trigger
  action, read).

**v8.2 — PIE control:**
- **`start_pie()`** — `GEditor->RequestPlaySession(FRequestPlaySessionParams{WorldType=PlayInEditor})`.
  Returns `queued=true` because actual start fires on next editor tick.
- **`stop_pie()`** — `GEditor->RequestEndPlayMap()`.
- **`is_pie_running()`** — checks `GEditor->PlayWorld != nullptr`; also surfaces
  `start_queued` so callers can poll between request and actual start.

**v8.3 — Simulated input:**
- **`pie_press_key(key, player_index=0)`** — press + release a key on the active
  PlayerController via `APlayerController::InputKey(FInputKeyParams)`. Works for
  both legacy and Enhanced Input. Reuses `ResolveFKeyWithAliases` so
  `"Space"` → `"SpaceBar"` etc.

### Stats
- Tools: **40 → 46** (+6)
- Unit tests: **106 → 116** (+10)
- Integration tests: **13 → 14** (+1: full agentic-loop demo)
- Plugin dylib: **688 KB → 717 KB**

### Caveats
- `start_pie` is queued — `is_pie_running` may briefly disagree with what was
  just requested. Poll `is_pie_running` before `pie_press_key`.
- Log capture is always-on; no need to start/stop. Use `clear_log_capture`
  before triggering an action to make `read_log_capture` show only new lines.

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
