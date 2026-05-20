"""MCP server for Unreal Engine Blueprint editing.

Spike B1 status — tools: echo, ping_ue, create_blueprint.

Architecture:
    Claude Desktop / Code
        ↓ MCP stdio (JSON-RPC)
    this server (FastMCP)
        ↓ TCP localhost:55558 (newline-terminated JSON)
    BlueprintMCP UE plugin (in editor process)
        ↓ C++ calls to UE engine (marshaled to game thread for asset ops)
    Unreal Engine 5.4 Editor

References:
    - Inspired by chongdashu/unreal-mcp's TCP dispatch pattern (MIT, GitHub)
      but re-derived; no copied code. See vault notes/prior-art.md.
"""

from __future__ import annotations

import json
import logging
import socket
import sys
from typing import Any

from mcp.server.fastmcp import FastMCP

# IMPORTANT: stdio MCP transport uses stdout for protocol.
# All logging MUST go to stderr or a file — never stdout.
logging.basicConfig(
    level=logging.INFO,
    stream=sys.stderr,
    format="[%(asctime)s] %(levelname)s %(name)s: %(message)s",
)
log = logging.getLogger("unreal-blueprint-mcp")

# UE plugin TCP endpoint. Port 55558 chosen to coexist with chongdashu's 55557.
UE_PLUGIN_HOST = "127.0.0.1"
UE_PLUGIN_PORT = 55558
UE_PLUGIN_TIMEOUT_SEC = 12.0  # game-thread ops have 10s budget on UE side; allow margin

mcp = FastMCP("unreal-blueprint-mcp")


# ---------------------------------------------------------------------------
# TCP helper — every UE-touching tool goes through this
# ---------------------------------------------------------------------------


def _send_command(command_payload: dict[str, Any]) -> dict[str, Any]:
    """Send a JSON command to the UE plugin via TCP, return the parsed response.

    Lower-level helper. Tools should call this rather than open sockets directly,
    so the error-handling shape stays consistent.

    Returns:
        On UE plugin success: the parsed JSON dict (e.g., {"ok": true, ...}).
        On connection / parse failure: a synthetic error dict — never raises.
    """
    payload_bytes = (json.dumps(command_payload) + "\n").encode("utf-8")
    log.info("→ UE plugin: %s", command_payload.get("command", "<no-command>"))

    try:
        with socket.create_connection(
            (UE_PLUGIN_HOST, UE_PLUGIN_PORT), timeout=UE_PLUGIN_TIMEOUT_SEC
        ) as sock:
            sock.sendall(payload_bytes)
            data = sock.recv(8192)
    except ConnectionRefusedError as e:
        return {
            "ok": False,
            "error": "connection_refused",
            "detail": str(e),
            "hint": (
                "UE editor isn't running, OR the BlueprintMCP plugin isn't loaded. "
                "Open UE Editor and check Output Log for 'BlueprintMCP starting'."
            ),
        }
    except (socket.timeout, OSError) as e:
        return {
            "ok": False,
            "error": "tcp_error",
            "detail": str(e),
            "hint": "Network / socket problem — see detail.",
        }

    response_str = data.decode("utf-8", errors="replace").strip()
    log.info("← UE plugin: %s", response_str[:200])

    try:
        return json.loads(response_str)
    except json.JSONDecodeError:
        return {
            "ok": False,
            "error": "invalid_response_json",
            "raw": response_str,
            "hint": "UE plugin returned non-JSON. Check Output Log on UE side.",
        }


# ---------------------------------------------------------------------------
# Tools
# ---------------------------------------------------------------------------


@mcp.tool()
def echo(message: str) -> dict[str, Any]:
    """Echo a message back. Sanity-test for the MCP stdio plumbing.

    Use this to verify Claude can reach this MCP server. It does NOT touch
    Unreal Engine; if this works but `ping_ue` doesn't, the UE side is the
    problem.
    """
    log.info("echo called with message of length %d", len(message))
    return {"ok": True, "echo": message}


@mcp.tool()
def ping_ue() -> dict[str, Any]:
    """Ping the BlueprintMCP UE plugin via TCP. Verify UE side is alive.

    Returns the plugin's response directly. On success the dict contains
    `ok=True`, `version`, `timestamp`. On failure it contains `ok=False`,
    `error`, and a `hint` for the most likely cause.
    """
    return _send_command({"command": "ping"})


@mcp.tool()
def get_blueprint(name: str) -> dict[str, Any]:
    """Get a snapshot of a Blueprint's current state. **Call this BEFORE writing.**

    This is the "look before you leap" tool. Use it to:
        - Avoid anchor-name collisions (you can see what anchors already exist)
        - Use the EXACT pin name UE has (not your guess, e.g. "InString" not "in_string")
        - Avoid recreating existing variables / components
        - See current connections so you don't double-wire
        - Check if compiled status before spawn_actor

    Args:
        name: Full Blueprint asset path (e.g., "/Game/Blueprints/BP_X").

    Returns:
        On success: snapshot dict with this shape:
        ```
        {
          "ok": true,
          "path": "/Game/Blueprints/BP_X",
          "parent_class": "Actor",
          "compiled": true,
          "status": "up_to_date" | "warnings" | "error" | "dirty" | ...,
          "anchors": {
            "<anchor_name>": {
              "k2_node_class": "K2Node_Event" | "K2Node_CallFunction" | "K2Node_CustomEvent" |
                                "K2Node_VariableGet" | "K2Node_VariableSet" | ...,
              "position": [x, y],
              "pins": [
                {"name": "...", "direction": "input" | "output", "type": "exec" | "string" | ...,
                 "default": "...",          # only if input pin has a default
                 "linked": true}            # only if pin has links
              ],
              # node-type-specific extra fields:
              "event_name": "..." (events / custom events),
              "function": "...", "owning_class": "..." (K2Node_CallFunction),
              "variable_name": "..." (K2Node_VariableGet / VariableSet)
            },
            ...
          },
          "connections": [{"from": "anchor.pin", "to": "anchor.pin"}, ...],
          "variables": [{"name": "...", "type": "...", "subcategory": "..."}],
          "components": [{"name": "TriggerBox", "class": "BoxComponent"}, ...]
        }
        ```

    Anchor derivation rules (so you can predict what anchors look like):
        1. NodeComment if set (i.e., the anchor_name you gave to add_node / add_*)
        2. K2Node_Event: well-known short name (begin_play / tick / actor_end_overlap / ...)
        3. Fallback: "node_<8-char-guid>" — stable across sessions

    Common errors:
        blueprint_not_found  - path doesn't exist
        game_thread_timeout  - 10s deadline exceeded (rare; large BPs)
    """
    return _send_command({
        "command": "get_blueprint",
        "name": name,
    })


@mcp.tool()
def add_node(
    blueprint: str,
    node_type: str,
    anchor_name: str,
    position_x: int = 0,
    position_y: int = 0,
) -> dict[str, Any]:
    """Add a `K2Node_CallFunction` node to a Blueprint's EventGraph.

    **Scope:** Use this ONLY for function-call nodes (calling existing UE/BP functions).
    For other node kinds, use the specialized tools:
        - Custom events (red nodes)        → `add_custom_event`
        - Variable get/set                  → `add_variable_get` / `add_variable_set`

    Use this after `create_blueprint`, when the user asks to "add a print node",
    "spawn a function call", etc.

    Args:
        blueprint: Full Blueprint asset path (e.g., "/Game/Blueprints/BP_TestSpikeB1_v2").
        node_type: Format "K2Node_CallFunction:<param>":
            - Short name (v0+v1 whitelist): `PrintString`, `Delay`,
              `SetTimerByEvent`, `ClearAndInvalidateTimerByHandle`
            - Fully qualified: `K2Node_CallFunction:KismetSystemLibrary.PrintString`
        anchor_name: User-given label. **Stored as the node's comment in the editor**
            and used to reference this node in subsequent tools (set_pin_default,
            connect_pins, ...). Must be unique within the Blueprint.
        position_x, position_y: Graph position (default 0, 0).

    Returns:
        On success: {"ok": True, "anchor_name": "...", "node_guid": "...",
                     "node_type": "K2Node_CallFunction", "function": "PrintString",
                     "owning_class": "KismetSystemLibrary",
                     "pins": [{"name": "...", "direction": "input|output", "type": "exec|string|..."}, ...],
                     "saved": True}
        On error:   {"ok": False, "error": "...", "detail": "..."}

    Common errors:
        blueprint_not_found    - blueprint path doesn't exist
        no_event_graph         - Blueprint has no UbergraphPages
        invalid_node_type      - node_type missing the ":" separator
        unknown_function       - bare function name not in v0 whitelist
        class_not_found        - qualified ClassName doesn't resolve to a UClass
        function_not_found     - FunctionName not found on that class
        unsupported_node_class - K2NodeClass not yet supported (v0: only K2Node_CallFunction)
        anchor_name_exists     - another node in the same EventGraph already has this anchor
        game_thread_timeout    - 10s deadline exceeded
    """
    return _send_command({
        "command": "add_node",
        "blueprint": blueprint,
        "node_type": node_type,
        "anchor_name": anchor_name,
        "position_x": position_x,
        "position_y": position_y,
    })


@mcp.tool()
def spawn_actor(
    blueprint: str,
    location_x: float = 0.0,
    location_y: float = 0.0,
    location_z: float = 0.0,
) -> dict[str, Any]:
    """Spawn a Blueprint instance into the current level (the final step before PIE).

    Use this after `compile_blueprint` to place the Blueprint into the world so
    its BeginPlay etc. will fire when the user presses Play.

    Args:
        blueprint: Full Blueprint asset path. MUST have been compiled (BS_UpToDate);
            spawning an unbuilt or error-state BP returns `no_generated_class`.
        location_x/y/z: World-space spawn location (default 0,0,0).
            Doesn't matter for non-spatial BPs like PrintString demos.

    Returns:
        On success: {"ok": True, "blueprint_path": ..., "actor_name": "<UE-assigned>",
                     "location": [x, y, z]}
        On error:   {"ok": False, "error": "...", "detail": "..."}

    After spawning, the user must press the **Play** button (top toolbar) to
    enter PIE (Play In Editor) mode. The BP's BeginPlay (if any) fires there.

    Common errors:
        blueprint_not_found  - path doesn't exist
        no_generated_class   - BP not compiled yet (call compile_blueprint first)
        not_actor_subclass   - BP's parent is not AActor; can't spawn into world
        no_actor_subsystem   - GEditor / subsystem unavailable (shouldn't happen in editor)
        spawn_failed         - UE refused to spawn (rare; e.g., level not writable)
        game_thread_timeout  - 10s deadline exceeded
    """
    return _send_command({
        "command": "spawn_actor",
        "blueprint": blueprint,
        "location_x": location_x,
        "location_y": location_y,
        "location_z": location_z,
    })


@mcp.tool()
def compile_blueprint(name: str) -> dict[str, Any]:
    """Compile a Blueprint after modifying its graph.

    Use this after `add_node` / `set_pin_default` / `connect_pins` to make UE
    actually generate the bytecode. Without compile, the BP shows "Compile"
    button highlighted in orange in the editor and won't run.

    Args:
        name: Full Blueprint asset path (e.g., "/Game/Blueprints/BP_TestSpikeB1_v2").

    Returns:
        On success: {"ok": True, "status": "up_to_date" | "warnings", "saved": True}
        On failure: {"ok": False, "error": "compile_failed", "status": "error" | "dirty" | ...,
                     "hint": "...check Message Log...", "saved": True/False}

    For detailed compile errors, check the UE Editor's Message Log:
    Window → Developer Tools → Message Log → "Blueprint Log" tab.

    Common errors:
        blueprint_not_found  - path doesn't exist
        compile_failed       - BP has errors; status field tells which kind
        game_thread_timeout  - 30s deadline (compile is slower; bigger BPs take longer)
    """
    return _send_command({
        "command": "compile_blueprint",
        "name": name,
    })


@mcp.tool()
def connect_pins(
    blueprint: str,
    from_pin: str,
    to_pin: str,
) -> dict[str, Any]:
    """Connect two pins in a Blueprint's EventGraph.

    Use this to wire nodes together. For typical event-driven flow:
    `connect_pins(bp, "begin_play.then", "print_hello.execute")`.

    Args:
        blueprint: Full Blueprint asset path.
        from_pin: Source pin "<anchor>.<pin>".
        to_pin: Target pin "<anchor>.<pin>".

        For default event nodes the `anchor` part can use these **well-known
        short names** (case-insensitive). If the corresponding K2Node_Event
        doesn't exist yet in the EventGraph, **it is auto-spawned on first reference**
        (so these always work in fresh Actor-derived blueprints):
            - "begin_play"          → ReceiveBeginPlay
            - "tick"                → ReceiveTick
            - "end_play"            → ReceiveEndPlay
            - "actor_begin_overlap" → ReceiveActorBeginOverlap
            - "actor_end_overlap"   → ReceiveActorEndOverlap
            - "hit"                 → ReceiveHit (requires physics-enabled component)
            - "destroyed"           → ReceiveDestroyed

        Spawning requires the BP's parent class to actually have the event function
        (which is true for AActor and most subclasses). If the parent class doesn't
        have it, you'll get `anchor_not_found`.

        For nodes added via `add_node`, use the `anchor_name` you provided.

    Returns:
        On success: {"ok": True, "from": "...", "to": "...", "saved": True}
        On error:   {"ok": False, "error": "...", "detail": "..."}

    The K2 schema enforces direction + type compatibility:
        - For exec: from must be output, to must be input
        - For data: types must be compatible (UE may coerce automatically)

    Common errors:
        invalid_pin_ref       - missing "." in pin_ref
        anchor_not_found      - anchor doesn't match NodeComment or well-known event
        pin_not_found         - pin name not on that node (case-sensitive)
        incompatible_pins     - schema rejected the connection; UE's reason is in `detail`
        connection_failed     - schema allowed but TryCreateConnection returned false (rare)
        game_thread_timeout   - 10s deadline exceeded
    """
    return _send_command({
        "command": "connect_pins",
        "blueprint": blueprint,
        "from_pin": from_pin,
        "to_pin": to_pin,
    })


@mcp.tool()
def set_pin_default(
    blueprint: str,
    pin_ref: str,
    value: str,
) -> dict[str, Any]:
    """Set the default value of an input pin on a Blueprint node.

    Use this after `add_node`, when the user wants to set a pin's default value
    (e.g., "set the print string to 'hello world'", "set the delay duration to 2 seconds").

    Args:
        blueprint: Full Blueprint asset path (e.g., "/Game/Blueprints/BP_TestSpikeB1_v2").
        pin_ref: "<anchor_name>.<pin_name>" — e.g., "print_hello.InString".
            anchor_name is what you passed to add_node.
            pin_name comes from add_node's `pins[]` array (case-sensitive, FName).
        value: String representation. UE parses based on pin type:
            - string / name / text: stored as-is
            - int / int64 / byte: parsed as integer
            - real (float / double): parsed as float
            - bool: "true" / "false" (case-insensitive)

    Returns:
        On success: {"ok": True, "anchor_name": ..., "pin_name": ...,
                     "value": "<stored value as UE has it>", "pin_type": "...",
                     "saved": True}
        On error:   {"ok": False, "error": "...", "detail": "..."}

    v0 limitations:
        - Only primitive types (string/name/text/int/int64/real/bool/byte). Pins of type
          object/class/struct/delegate/wildcard return `unsupported_pin_type`.
        - Output pins return `pin_not_input` (defaults apply only to inputs).
        - Exec pins return `exec_pin_no_default`.

    Common errors:
        blueprint_not_found, no_event_graph, invalid_pin_ref (missing "."),
        anchor_not_found, pin_not_found, pin_not_input,
        exec_pin_no_default, unsupported_pin_type, game_thread_timeout
    """
    return _send_command({
        "command": "set_pin_default",
        "blueprint": blueprint,
        "pin_ref": pin_ref,
        "value": value,
    })


@mcp.tool()
def add_component(
    blueprint: str,
    component_class: str,
    name: str,
) -> dict[str, Any]:
    """Add a component to a Blueprint's Simple Construction Script (the "Components panel" in BP editor).

    **Required for any "collision area", "trigger", "mesh", "camera" use case** —
    most BP behaviour involves components. Without `add_component` you can't make
    things like `ActorBeginOverlap` fire (you need a collision component first).

    Use this immediately after `create_blueprint` and BEFORE attempting to
    `connect_pins` events that depend on the component.

    Args:
        blueprint: Full Blueprint asset path. MUST have an Actor-derived parent class.
        component_class: One of these short names (v1 whitelist):
            - `BoxCollision` / `Box` — for trigger volumes / overlap detection
            - `SphereCollision` / `Sphere`
            - `CapsuleCollision` / `Capsule`
            - `StaticMesh`
            - `Camera`
            - `PointLight` / `SpotLight`
            - `Audio`
          Or any fully-qualified UClass name that derives from UActorComponent.
        name: Component name (e.g., "TriggerBox"). Must be unique within this BP.

    Returns:
        On success: {"ok": True, "component_name": "...", "component_class": "...", "saved": True}
        On error:   {"ok": False, "error": "...", "detail": "..."}

    Common errors:
        blueprint_not_found       - path doesn't exist
        parent_not_actor          - BP parent class isn't AActor (components don't apply)
        unknown_component_class   - short name not in whitelist + not a UActorComponent subclass
        component_name_exists     - another component already has this name
    """
    return _send_command({
        "command": "add_component",
        "blueprint": blueprint,
        "component_class": component_class,
        "name": name,
    })


@mcp.tool()
def add_custom_event(
    blueprint: str,
    event_name: str,
    anchor_name: str,
    position_x: int = 0,
    position_y: int = 0,
) -> dict[str, Any]:
    """Add a `K2Node_CustomEvent` (the **red event** node) to the EventGraph.

    Custom events are user-defined entry points that other nodes (or delegates,
    like `Set Timer by Event`'s `Event` pin) can fire. They have an **output exec
    pin** ("then") and **no input exec** (they ARE the entry).

    Use this when the user asks to "make a custom event", "define a callback",
    or when wiring `Set Timer by Event` / delegate pins that need a target event.

    Args:
        blueprint: Full Blueprint asset path.
        event_name: The custom event's logical name (becomes its `CustomFunctionName`).
            Must be unique within this Blueprint's EventGraph.
        anchor_name: User-given label (visible as NodeComment). Must be unique
            across all nodes in the EventGraph.
        position_x, position_y: Graph position (default 0, 0).

    Returns:
        On success: {"ok": True, "anchor_name": ..., "event_name": ...,
                     "node_guid": ..., "pins": [...], "saved": True}

    Common errors:
        anchor_name_exists   - another node already uses this anchor
        event_name_exists    - another custom event in this BP has the same name
    """
    return _send_command({
        "command": "add_custom_event",
        "blueprint": blueprint,
        "event_name": event_name,
        "anchor_name": anchor_name,
        "position_x": position_x,
        "position_y": position_y,
    })


@mcp.tool()
def add_variable(
    blueprint: str,
    name: str,
    variable_type: str,
    default_value: str = "",
) -> dict[str, Any]:
    """Add a member variable to a Blueprint (visible in the "Variables" panel).

    Use this when the user asks to "add a variable" or when you need to store
    state across event firings — most commonly: a `TimerHandle` to remember a
    started timer so you can cancel it later.

    Args:
        blueprint: Full Blueprint asset path.
        name: Variable name (e.g., "MyTimerHandle"). Must be unique in this BP.
        variable_type: v1 whitelist (case-insensitive):
            - `bool` / `int` / `float` (alias: `double`, `real`) / `string` / `name` / `text`
            - **`TimerHandle`** — the FTimerHandle struct (essential for timer cancellation)
        default_value: Optional initial value as string (e.g., "true", "5.0", "hello").
            For TimerHandle and other structs, leave empty (will be a default-constructed struct).

    Returns:
        On success: {"ok": True, "variable_name": "...", "variable_type": "...", "saved": True}

    After this, use `add_variable_get` / `add_variable_set` to read/write the
    variable inside the EventGraph.

    Common errors:
        variable_exists          - another BP variable already has this name
        unknown_variable_type    - type not in v1 whitelist
        add_failed               - UE refused to add (rare)
    """
    return _send_command({
        "command": "add_variable",
        "blueprint": blueprint,
        "name": name,
        "variable_type": variable_type,
        "default_value": default_value,
    })


@mcp.tool()
def add_variable_get(
    blueprint: str,
    variable_name: str,
    anchor_name: str,
    position_x: int = 0,
    position_y: int = 0,
) -> dict[str, Any]:
    """Add a `K2Node_VariableGet` (read) node referencing a BP variable.

    The variable MUST already exist (added via `add_variable`). Returns the
    node's pins so you can wire its `<var_name>` output pin somewhere.

    Args:
        blueprint: Full BP asset path.
        variable_name: Name of an existing BP variable.
        anchor_name: User-given label (unique within EventGraph).
        position_x, position_y: Graph position.

    Common errors:
        variable_not_found  - call `add_variable` first
        anchor_name_exists  - another node has this anchor
    """
    return _send_command({
        "command": "add_variable_get",
        "blueprint": blueprint,
        "variable_name": variable_name,
        "anchor_name": anchor_name,
        "position_x": position_x,
        "position_y": position_y,
    })


@mcp.tool()
def add_variable_set(
    blueprint: str,
    variable_name: str,
    anchor_name: str,
    position_x: int = 0,
    position_y: int = 0,
) -> dict[str, Any]:
    """Add a `K2Node_VariableSet` (write) node referencing a BP variable.

    Use when the user wants to assign to a variable. The set node has:
      - input exec pin (`execute`)
      - output exec pin (`then`)
      - input data pin named after the variable (the value to assign)
      - output data pin named after the variable (the post-assign value)

    The variable MUST already exist (added via `add_variable`).

    Args:
        blueprint: Full BP asset path.
        variable_name: Name of an existing BP variable.
        anchor_name: User-given label (unique within EventGraph).
        position_x, position_y: Graph position.

    Common errors:
        variable_not_found  - call `add_variable` first
        anchor_name_exists  - another node has this anchor
    """
    return _send_command({
        "command": "add_variable_set",
        "blueprint": blueprint,
        "variable_name": variable_name,
        "anchor_name": anchor_name,
        "position_x": position_x,
        "position_y": position_y,
    })


@mcp.tool()
def create_blueprint(
    name: str,
    parent_class: str = "Actor",
    path: str = "/Game/Blueprints",
) -> dict[str, Any]:
    """Create a new Unreal Engine Blueprint asset and save it.

    Use this when the user asks to "make a new blueprint" or "create a BP".
    Requires Unreal Editor running with BlueprintMCP plugin loaded.

    Args:
        name: Asset name without extension (e.g., "BP_HelloWorld").
        parent_class: Parent class to inherit from. Supported in v0 (case-insensitive):
            "Actor", "Pawn", "Character", "Object", "ActorComponent".
        path: /Game-relative folder. Defaults to "/Game/Blueprints".

    Returns:
        On success: {"ok": true, "blueprint_path": "/Game/...", "parent_class": "Actor", "saved": true}
        On error:   {"ok": false, "error": "<reason>", "detail": "<context>"}

    Common errors:
        unknown_parent_class — parent_class not in the v0 whitelist
        asset_exists         — the asset already exists at that path (no overwrite)
        creation_failed      — AssetTools::CreateAsset returned null (unusual; see UE Output Log)
        game_thread_timeout  — game thread didn't respond in 10s (Editor frozen?)
    """
    return _send_command({
        "command": "create_blueprint",
        "name": name,
        "parent_class": parent_class,
        "path": path,
    })


# ---------------------------------------------------------------------------
# Entrypoint
# ---------------------------------------------------------------------------


def main() -> None:
    """Run the server in stdio mode (for Claude Desktop / Code)."""
    log.info("unreal-blueprint-mcp server starting (stdio)")
    mcp.run()


if __name__ == "__main__":
    main()
