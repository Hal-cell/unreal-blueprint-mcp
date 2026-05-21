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
            # v6.0.2 P1 fix: loop recv until UE closes the connection (no 8KB cap).
            # UE plugin closes the socket after sending the full response per HandleClient.
            chunks: list[bytes] = []
            while True:
                chunk = sock.recv(65536)
                if not chunk:
                    break
                chunks.append(chunk)
            data = b"".join(chunks)
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
    log.info("← UE plugin: %d bytes, head=%s", len(response_str), response_str[:200])

    try:
        return json.loads(response_str)
    except json.JSONDecodeError as e:
        # Could be: actual non-JSON, OR a parse error we want to debug.
        # The 8KB-truncation case from v6.0.1 is gone (recv loop now drains the socket),
        # so this hint is now accurate.
        return {
            "ok": False,
            "error": "invalid_response_json",
            "raw": response_str,
            "raw_length": len(response_str),
            "parse_error": str(e),
            "hint": "UE plugin returned non-JSON or malformed JSON. Check Output Log on UE side.",
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
def set_component_property(
    blueprint: str,
    component_name: str,
    property_name: str,
    value: str = "",
) -> dict[str, Any]:
    """Set a property on a component's template instance inside a Blueprint.

    This is the v7.1 escape hatch for component-level defaults that aren't K2Node
    pins — things like ``StaticMeshComponent::StaticMesh`` (which mesh asset),
    ``BoxComponent::BoxExtent`` (trigger volume size), ``PrimitiveComponent::BodyInstance``
    (collision preset, generate overlap events).

    Property categories supported:
    - **Object reference** (StaticMesh, Material, Texture, …): pass an asset path
      like ``/Engine/BasicShapes/Cube`` or full ``/Engine/BasicShapes/Cube.Cube``.
      Pass ``""`` or ``"None"`` to clear the reference.
    - **Class reference** (``TSubclassOf<X>``): pass a class path like
      ``/Script/Engine.Actor`` or a BP class path like ``/Game/BP_X.BP_X_C``.
    - **Struct** (FVector, FRotator, FColor, FBodyInstance, …): pass an FString-style
      literal like ``(X=200,Y=200,Z=200)``. For Vector / Rotator / Color you can also
      pass shorthand ``200,200,200`` (auto-normalized).
    - **Primitive** (int, float, bool, FName, enum, FString): pass a stringified value
      like ``True``, ``42``, ``OverlapAllDynamic``.

    Dot-separated paths supported for nested struct fields::

        property_name="BodyInstance.CollisionProfileName"  value="OverlapAllDynamic"
        property_name="BodyInstance.bGenerateOverlapEvents"  value="True"

    Examples::

        # Make a StaticMeshComponent visible
        set_component_property(
            blueprint="/Game/BP_TargetDummy", component_name="VisualMesh",
            property_name="StaticMesh", value="/Engine/BasicShapes/Cube",
        )

        # Resize a BoxCollision trigger so the player can walk into it
        set_component_property(
            blueprint="/Game/BP_TargetDummy", component_name="TriggerBox",
            property_name="BoxExtent", value="(X=200,Y=200,Z=200)",
        )

        # Set collision preset so overlap events fire
        set_component_property(
            blueprint="/Game/BP_TargetDummy", component_name="TriggerBox",
            property_name="BodyInstance.CollisionProfileName", value="OverlapAllDynamic",
        )

    Args:
        blueprint: Asset path of the Blueprint (e.g. ``/Game/BP_TargetDummy``).
        component_name: Variable name of the component (same name passed to
            ``add_component``, case-sensitive).
        property_name: Property name on the component template, or dot-separated
            path for nested struct fields.
        value: Stringified new value. Empty string clears object/class refs to None.

    Returns:
        On success: ``{"ok": True, "blueprint": "...", "component": "...",
        "property": "...", "resolved_value": "...", "saved": True}``
        On error:   ``{"ok": False, "error": "...", "detail": "..."}``

    Common errors:
        blueprint_not_found  — BP path doesn't exist
        parent_not_actor     — BP parent class isn't AActor (no SCS)
        component_not_found  — no component with that name in the BP's SCS
        property_not_found   — component class has no property by that name (or a
                               mid-path token isn't a struct field)
        set_failed           — asset/class lookup failed, or struct/primitive literal
                               couldn't be parsed by FProperty::ImportText
    """
    if not blueprint or not component_name or not property_name:
        return {"ok": False, "error": "missing_argument"}
    return _send_command({
        "command": "set_component_property",
        "blueprint": blueprint,
        "component_name": component_name,
        "property_name": property_name,
        "value": value,
    })


@mcp.tool()
def add_switch(
    blueprint: str,
    anchor_name: str,
    switch_type: str,
    position_x: int = 0,
    position_y: int = 0,
    enum_class: str = "",
    case_count: int = 2,
    case_labels: str = "",
    graph_name: str = "",
) -> dict[str, Any]:
    """Add a Switch node (multi-way branch) — v7.2.

    Four flavors keyed by ``switch_type``:
    - ``"int"`` / ``"integer"`` → ``K2Node_SwitchInteger``. Use ``case_count`` for
      total number of case pins (will be labelled ``0, 1, …, case_count-1``).
    - ``"string"`` → ``K2Node_SwitchString``. Use ``case_labels`` (comma-separated)
      for the case labels, e.g. ``"red,green,blue"``.
    - ``"name"`` → ``K2Node_SwitchName``. Same ``case_labels`` convention.
    - ``"enum"`` → ``K2Node_SwitchEnum``. ``enum_class`` REQUIRED; e.g.
      ``"/Script/Engine.EAxis"`` or a custom enum's asset path. AllocateDefaultPins
      generates one case pin per enum value automatically (no case_count needed).

    Args:
        blueprint: BP asset path.
        anchor_name: Unique label for the node.
        switch_type: One of ``"int" | "string" | "name" | "enum"``.
        enum_class: Required when ``switch_type="enum"``; ignored otherwise.
        case_count: For int switch — total case pins. Default 2.
        case_labels: For string/name switch — comma-separated case labels.
            Ignored for int/enum.
        position_x, position_y: Graph coordinates.

    Returns:
        {"ok": True, "anchor_name": ..., "switch_type": ..., "node_type": ...,
         "node_guid": ..., "pins": [...], "saved": True}
    """
    if not blueprint or not anchor_name or not switch_type:
        return {"ok": False, "error": "missing_argument"}
    payload: dict[str, Any] = {
        "command": "add_switch",
        "blueprint": blueprint,
        "switch_type": switch_type,
        "anchor_name": anchor_name,
        "position_x": position_x,
        "position_y": position_y,
        "enum_class": enum_class,
        "case_count": case_count,
        "case_labels": case_labels,
    }
    if graph_name:
        payload["graph_name"] = graph_name
    return _send_command(payload)


@mcp.tool()
def add_sequence(
    blueprint: str,
    anchor_name: str,
    position_x: int = 0,
    position_y: int = 0,
    then_count: int = 2,
    graph_name: str = "",
) -> dict[str, Any]:
    """Add an Execution Sequence node (``K2Node_ExecutionSequence``) — v7.2.

    One input exec fires N output exec pins in order (``Then 0``, ``Then 1``, …).
    Use this when you want a single trigger to drive multiple independent action
    chains.

    Args:
        blueprint: BP asset path.
        anchor_name: Unique label.
        then_count: Total number of "Then N" output exec pins. Default 2.
        position_x, position_y: Graph coordinates.

    Returns:
        Standard node-creation JSON with ``pins`` array (one ``execute`` input plus
        ``Then 0``, ``Then 1``, … outputs).
    """
    if not blueprint or not anchor_name:
        return {"ok": False, "error": "missing_argument"}
    payload: dict[str, Any] = {
        "command": "add_sequence",
        "blueprint": blueprint,
        "anchor_name": anchor_name,
        "position_x": position_x,
        "position_y": position_y,
        "then_count": then_count,
    }
    if graph_name:
        payload["graph_name"] = graph_name
    return _send_command(payload)


@mcp.tool()
def add_make_array(
    blueprint: str,
    anchor_name: str,
    position_x: int = 0,
    position_y: int = 0,
    num_inputs: int = 1,
    graph_name: str = "",
) -> dict[str, Any]:
    """Add a Make Array node (``K2Node_MakeArray``) — v7.2.

    Constructs an array literal from N input pins. Element type is wildcard
    until you connect the first input — UE then infers the array element type.

    Args:
        blueprint: BP asset path.
        anchor_name: Unique label.
        num_inputs: Number of element input pins (``[0]``, ``[1]``, …). Default 1.
        position_x, position_y: Graph coordinates.

    Returns:
        Standard node-creation JSON.
    """
    if not blueprint or not anchor_name:
        return {"ok": False, "error": "missing_argument"}
    payload: dict[str, Any] = {
        "command": "add_make_array",
        "blueprint": blueprint,
        "anchor_name": anchor_name,
        "position_x": position_x,
        "position_y": position_y,
        "num_inputs": num_inputs,
    }
    if graph_name:
        payload["graph_name"] = graph_name
    return _send_command(payload)


@mcp.tool()
def add_make_struct(
    blueprint: str,
    anchor_name: str,
    struct_type: str,
    position_x: int = 0,
    position_y: int = 0,
    graph_name: str = "",
) -> dict[str, Any]:
    """Add a Make Struct node (``K2Node_MakeStruct``) — v7.3.

    Constructs a struct value from its member fields. Pins are generated
    dynamically based on the struct type's visible (BlueprintReadWrite) members.

    Struct type whitelist:
    - ``"Vector"`` (FVector) — X, Y, Z
    - ``"Vector2D"`` (FVector2D) — X, Y
    - ``"Rotator"`` (FRotator) — Pitch, Yaw, Roll
    - ``"Transform"`` (FTransform) — Location, Rotation, Scale
    - ``"LinearColor"`` / ``"Color"`` — RGBA
    - ``"Quat"`` (FQuat)
    - ``"Box"`` (FBox)
    - ``"HitResult"`` (FHitResult, engine struct)
    - ``"OverlapResult"`` (FOverlapResult)
    - ``"CollisionQueryParams"``
    - Or any fully qualified path: ``/Script/Engine.HitResult``, or BP-defined struct
      ``/Game/Structs/MyStruct``

    Args:
        blueprint: BP asset path.
        anchor_name: Unique label.
        struct_type: Short name (from whitelist) or qualified struct path.
        position_x, position_y: Graph coordinates.

    Returns:
        Standard node-creation JSON. ``pins`` array contains one input pin per
        struct member plus one output pin of the struct type.
    """
    if not blueprint or not anchor_name or not struct_type:
        return {"ok": False, "error": "missing_argument"}
    payload: dict[str, Any] = {
        "command": "add_make_struct",
        "blueprint": blueprint,
        "struct_type": struct_type,
        "anchor_name": anchor_name,
        "position_x": position_x,
        "position_y": position_y,
    }
    if graph_name:
        payload["graph_name"] = graph_name
    return _send_command(payload)


@mcp.tool()
def add_break_struct(
    blueprint: str,
    anchor_name: str,
    struct_type: str,
    position_x: int = 0,
    position_y: int = 0,
    graph_name: str = "",
) -> dict[str, Any]:
    """Add a Break Struct node (``K2Node_BreakStruct``) — v7.3.

    Decomposes a struct value into its member fields. Inverse of ``add_make_struct``.
    Common pattern: connect a ``HitResult`` output from a trace function into
    this node's input, then read individual fields (Location, ImpactNormal, etc).

    Args:
        blueprint: BP asset path.
        anchor_name: Unique label.
        struct_type: See ``add_make_struct`` for whitelist + path syntax.
        position_x, position_y: Graph coordinates.

    Returns:
        Standard node-creation JSON. ``pins`` array contains one input pin of
        the struct type plus one output pin per struct member.
    """
    if not blueprint or not anchor_name or not struct_type:
        return {"ok": False, "error": "missing_argument"}
    payload: dict[str, Any] = {
        "command": "add_break_struct",
        "blueprint": blueprint,
        "struct_type": struct_type,
        "anchor_name": anchor_name,
        "position_x": position_x,
        "position_y": position_y,
    }
    if graph_name:
        payload["graph_name"] = graph_name
    return _send_command(payload)


@mcp.tool()
def add_select(
    blueprint: str,
    anchor_name: str,
    position_x: int = 0,
    position_y: int = 0,
    num_options: int = 2,
    graph_name: str = "",
) -> dict[str, Any]:
    """Add a Select node (``K2Node_Select``) — v7.2.

    Three-way+ chooser: ``Index`` picks one of N option inputs and outputs its
    value. Value type is wildcard until you connect the first option.

    Args:
        blueprint: BP asset path.
        anchor_name: Unique label.
        num_options: Number of option input pins (``Option 0``, ``Option 1``, …).
            Default 2.
        position_x, position_y: Graph coordinates.

    Returns:
        Standard node-creation JSON.
    """
    if not blueprint or not anchor_name:
        return {"ok": False, "error": "missing_argument"}
    payload: dict[str, Any] = {
        "command": "add_select",
        "blueprint": blueprint,
        "anchor_name": anchor_name,
        "position_x": position_x,
        "position_y": position_y,
        "num_options": num_options,
    }
    if graph_name:
        payload["graph_name"] = graph_name
    return _send_command(payload)


@mcp.tool()
def save_blueprint(blueprint: str) -> dict[str, Any]:
    """Explicitly save a Blueprint asset to disk — v7.8.

    All v7 write tools (``add_node``, ``set_pin_default``, etc.) already call
    ``UEditorAssetLibrary::SaveAsset`` after each mutation, so the typical
    workflow doesn't NEED this. Use it when:
        - You want to be absolutely sure changes are on disk before closing UE
        - You're in a session where ``MarkBlueprintAsModified`` was called by
          third-party code and you want a forced save
        - Debugging "did my changes persist?" questions

    Args:
        blueprint: Full Blueprint asset path.

    Returns:
        On success: ``{"ok": True, "blueprint": "...", "package": "...", "saved": True}``
        The ``saved`` boolean reflects ``UEditorAssetLibrary::SaveAsset``'s return value.
        If False, check UE Output Log — typically means the package is read-only or
        source-control checked out by another user.
    """
    if not blueprint:
        return {"ok": False, "error": "missing_argument"}
    return _send_command({"command": "save_blueprint", "blueprint": blueprint})


@mcp.tool()
def add_event_dispatcher(
    blueprint: str,
    dispatcher_name: str,
    params: list[dict[str, str]] | None = None,
) -> dict[str, Any]:
    """Create an event dispatcher (multicast delegate) on a Blueprint — v7.6.

    Editor equivalent: "Event Dispatchers" panel → "+". The dispatcher becomes
    available as a delegate property on instances of this BP; other BPs (or this
    BP itself) can call it (``add_call_dispatcher``), bind to it
    (``add_bind_dispatcher``), or unbind (``add_unbind_dispatcher``).

    Params use the same syntax as ``add_custom_event``::

        params=[
            {"name": "Damage", "type": "float"},
            {"name": "Source", "type": "object:Actor"},
        ]

    When binding a custom event to this dispatcher, the custom event's parameter
    list MUST match this signature exactly (name + order + types).

    Args:
        blueprint: BP asset path.
        dispatcher_name: Logical name (must be unique among this BP's dispatchers).
        params: Optional list of ``{"name", "type"}`` dicts defining the
            multicast delegate signature.

    Returns:
        {"ok": True, "dispatcher_name": ..., "param_count": N, "saved": True}
    """
    if not blueprint or not dispatcher_name:
        return {"ok": False, "error": "missing_argument"}
    payload: dict[str, Any] = {
        "command": "add_event_dispatcher",
        "blueprint": blueprint,
        "dispatcher_name": dispatcher_name,
    }
    if params:
        payload["params"] = [
            {"name": str(p["name"]), "type": str(p["type"])}
            for p in params
            if "name" in p and "type" in p
        ]
    return _send_command(payload)


@mcp.tool()
def migrate_dispatchers(blueprint: str) -> dict[str, Any]:
    """Repair old-format event dispatchers in-place — v8.0.2 (ISSUE-1).

    Scans the Blueprint for dispatcher signature graphs that were created by a
    pre-v7.1.2 plugin version (missing the PC_MCDelegate member variable) and
    back-fills the missing variable. Recompiles + saves only if anything was
    actually changed.

    Use this once per old project after upgrading the plugin. Healthy BPs
    pass through unchanged.

    Args:
        blueprint: BP asset path to scan.

    Returns:
        ``{"ok": True, "blueprint": "...",
           "migrated_count": N, "migrated": [...names...],
           "already_healthy_count": N, "already_healthy": [...],
           "orphan_variable_count": N, "orphan_variables": [...names...],
           "compiled": bool, "saved": bool}``

        - ``migrated`` = dispatchers that were repaired (variable added)
        - ``already_healthy`` = dispatchers that were already correct
        - ``orphan_variables`` = PC_MCDelegate variables WITHOUT a signature graph
          (rare; use ``delete_event_dispatcher`` to clean those)
        - ``compiled`` / ``saved`` only true when ``migrated_count > 0``
    """
    if not blueprint:
        return {"ok": False, "error": "missing_argument"}
    return _send_command({
        "command": "migrate_dispatchers",
        "blueprint": blueprint,
    })


@mcp.tool()
def delete_event_dispatcher(
    blueprint: str,
    dispatcher_name: str,
) -> dict[str, Any]:
    """Delete an event dispatcher (signature graph + member variable) — v8.0.1.

    Provides a recovery path for dispatchers that were created with **pre-v7.1.2
    plugin versions**, which were missing the PC_MCDelegate member variable.
    Those broken dispatchers can't be repaired in place — `add_call_dispatcher`
    won't resolve their signature. Use this to delete them, then recreate with
    `add_event_dispatcher` on the current dylib.

    Also useful for runtime renames / cleanup of healthy dispatchers.

    Removes whichever of the two pieces is present:
    - Signature graph (in ``Blueprint->DelegateSignatureGraphs``)
    - Member variable (PC_MCDelegate)

    Args:
        blueprint: BP asset path.
        dispatcher_name: Name of the dispatcher to remove.

    Returns:
        ``{"ok": True, "dispatcher_name": ..., "removed_graph": bool,
            "removed_variable": bool, "compiled": True, "saved": True}``

    Errors:
        dispatcher_not_found — neither a signature graph nor a member variable
            of that name exists.
    """
    if not blueprint or not dispatcher_name:
        return {"ok": False, "error": "missing_argument"}
    return _send_command({
        "command": "delete_event_dispatcher",
        "blueprint": blueprint,
        "dispatcher_name": dispatcher_name,
    })


@mcp.tool()
def add_call_dispatcher(
    blueprint: str,
    dispatcher_name: str,
    anchor_name: str,
    position_x: int = 0,
    position_y: int = 0,
    graph_name: str = "",
) -> dict[str, Any]:
    """Add a ``K2Node_CallDelegate`` — broadcasts a dispatcher to all bound listeners — v7.6.

    Targets a dispatcher defined on ``self`` (this BP). To call a dispatcher
    on another actor, after creating this node connect that actor to its
    ``self`` input pin via ``connect_pins``.

    Args:
        blueprint: BP asset path.
        dispatcher_name: Name of the dispatcher to call (must exist on self by
            default — created via ``add_event_dispatcher``).
        anchor_name: Unique label.
        position_x, position_y: Graph coordinates.

    Returns:
        Standard node-creation JSON. Output pins include ``execute`` exec input,
        ``then`` exec output, plus one input pin per dispatcher param.
    """
    if not blueprint or not dispatcher_name or not anchor_name:
        return {"ok": False, "error": "missing_argument"}
    payload: dict[str, Any] = {
        "command": "add_call_dispatcher",
        "blueprint": blueprint,
        "dispatcher_name": dispatcher_name,
        "anchor_name": anchor_name,
        "position_x": position_x,
        "position_y": position_y,
    }
    if graph_name:
        payload["graph_name"] = graph_name
    return _send_command(payload)


@mcp.tool()
def add_bind_dispatcher(
    blueprint: str,
    dispatcher_name: str,
    anchor_name: str,
    position_x: int = 0,
    position_y: int = 0,
    graph_name: str = "",
) -> dict[str, Any]:
    """Add a ``K2Node_AddDelegate`` — binds a custom event to a dispatcher — v7.6.

    Workflow:
        1. ``add_event_dispatcher(name="OnDeath", params=[{...}])``
        2. ``add_custom_event(event_name="HandleDeath", anchor_name="...",
           params=[same signature as dispatcher])``
        3. ``add_bind_dispatcher(dispatcher_name="OnDeath", anchor_name="bind_death")``
        4. ``connect_pins("HandleDeath.delegate", "bind_death.Event")``

    The bind node has a ``self`` input pin defaulting to the current BP. To
    bind on a different actor, wire that actor to ``self``.

    Args:
        blueprint: BP asset path.
        dispatcher_name: Name of the dispatcher to bind to.
        anchor_name: Unique label.
        position_x, position_y: Graph coordinates.

    Returns:
        Standard node-creation JSON. Pins include ``execute``, ``then``,
        ``self`` (input), and ``Event`` (delegate input).
    """
    if not blueprint or not dispatcher_name or not anchor_name:
        return {"ok": False, "error": "missing_argument"}
    payload: dict[str, Any] = {
        "command": "add_bind_dispatcher",
        "blueprint": blueprint,
        "dispatcher_name": dispatcher_name,
        "anchor_name": anchor_name,
        "position_x": position_x,
        "position_y": position_y,
    }
    if graph_name:
        payload["graph_name"] = graph_name
    return _send_command(payload)


@mcp.tool()
def add_unbind_dispatcher(
    blueprint: str,
    dispatcher_name: str,
    anchor_name: str,
    position_x: int = 0,
    position_y: int = 0,
    graph_name: str = "",
) -> dict[str, Any]:
    """Add a ``K2Node_RemoveDelegate`` — unbinds an event from a dispatcher — v7.6.

    Inverse of ``add_bind_dispatcher``. Must reference the same dispatcher +
    event pair used at bind time.

    Args:
        blueprint: BP asset path.
        dispatcher_name: Name of the dispatcher to unbind from.
        anchor_name: Unique label.
        position_x, position_y: Graph coordinates.

    Returns:
        Standard node-creation JSON.
    """
    if not blueprint or not dispatcher_name or not anchor_name:
        return {"ok": False, "error": "missing_argument"}
    payload: dict[str, Any] = {
        "command": "add_unbind_dispatcher",
        "blueprint": blueprint,
        "dispatcher_name": dispatcher_name,
        "anchor_name": anchor_name,
        "position_x": position_x,
        "position_y": position_y,
    }
    if graph_name:
        payload["graph_name"] = graph_name
    return _send_command(payload)


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
    graph_name: str = "",
) -> dict[str, Any]:
    """Add a `K2Node_CallFunction` node to a Blueprint graph.

    **Scope:** Use this ONLY for function-call nodes (calling existing UE/BP functions).
    For other node kinds, use the specialized tools:
        - Custom events (red nodes)        → `add_custom_event`
        - Variable get/set                  → `add_variable_get` / `add_variable_set`

    **v7.7 — graph targeting**: by default, nodes go into the BP's EventGraph.
    Pass ``graph_name="MyFunc"`` (the user-function created by ``add_function``)
    to put the node inside that function's body graph instead.

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
            connect_pins, ...). Must be unique within the target graph.
        position_x, position_y: Graph position (default 0, 0).
        graph_name: v7.7 — name of the function/macro graph to add the node to
            (default = EventGraph). The graph must already exist (use ``add_function``
            first for user functions).

    Returns:
        On success: {"ok": True, "anchor_name": "...", "node_guid": "...",
                     "node_type": "K2Node_CallFunction", "function": "PrintString",
                     "owning_class": "KismetSystemLibrary",
                     "pins": [{"name": "...", "direction": "input|output", "type": "exec|string|..."}, ...],
                     "saved": True}
        On error:   {"ok": False, "error": "...", "detail": "..."}

    Common errors:
        blueprint_not_found    - blueprint path doesn't exist
        graph_not_found        - graph_name doesn't match any graph in this BP (v7.7)
        invalid_node_type      - node_type missing the ":" separator
        unknown_function       - bare function name not in v0 whitelist
        class_not_found        - qualified ClassName doesn't resolve to a UClass
        function_not_found     - FunctionName not found on that class
        unsupported_node_class - K2NodeClass not yet supported (v0: only K2Node_CallFunction)
        anchor_name_exists     - another node in the same graph already has this anchor
        game_thread_timeout    - 10s deadline exceeded
    """
    payload: dict[str, Any] = {
        "command": "add_node",
        "blueprint": blueprint,
        "node_type": node_type,
        "anchor_name": anchor_name,
        "position_x": position_x,
        "position_y": position_y,
    }
    if graph_name:
        payload["graph_name"] = graph_name
    return _send_command(payload)


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
    graph_name: str = "",
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
    payload: dict[str, Any] = {
        "command": "connect_pins",
        "blueprint": blueprint,
        "from_pin": from_pin,
        "to_pin": to_pin,
    }
    if graph_name:
        payload["graph_name"] = graph_name
    return _send_command(payload)


@mcp.tool()
def set_pin_default(
    blueprint: str,
    pin_ref: str,
    value: str,
    graph_name: str = "",
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
            - **Vector** (v4): "1.0,2.0,3.0" or "(X=1,Y=2,Z=3)"
            - **Rotator** (v4): "P,Y,R" e.g. "0,90,0" or "(P=0,Y=90,R=0)"
            - **Color / LinearColor** (v4): "R,G,B" (A=1) or "R,G,B,A" or "(R=...,G=...,B=...,A=...)"

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
    payload: dict[str, Any] = {
        "command": "set_pin_default",
        "blueprint": blueprint,
        "pin_ref": pin_ref,
        "value": value,
    }
    if graph_name:
        payload["graph_name"] = graph_name
    return _send_command(payload)


@mcp.tool()
def add_function(blueprint: str, name: str) -> dict[str, Any]:
    """Create a new user function graph in a Blueprint.

    Adds an empty function to the BP's Functions list. After creation, the
    function can be called from elsewhere via `call_blueprint_function`.

    **v5 limitation:** parameters / return values are not yet exposed; the
    function is empty + no-arg. Add nodes to the body manually in editor (or
    wait for v6).

    Args:
        blueprint: Full Blueprint asset path.
        name: Function name (must be unique within this BP).

    Returns:
        On success: {"ok": True, "function_name": "...", "saved": True}

    Common errors:
        function_exists       - a function with this name already exists
        graph_create_failed   - UE refused to create the graph
    """
    return _send_command({"command": "add_function", "blueprint": blueprint, "name": name})


@mcp.tool()
def call_blueprint_function(
    blueprint: str,
    target_class: str,
    function_name: str,
    anchor_name: str,
    position_x: int = 0,
    position_y: int = 0,
    target_pin: str = "",
    graph_name: str = "",
) -> dict[str, Any]:
    """Call a function on another class / Blueprint from inside this BP's EventGraph.

    Use for cross-BP communication ("call BP_Manager.DoThing"), or for native
    classes when you want explicit target-class syntax instead of the
    `add_node` short-name whitelist.

    Args:
        blueprint: BP that will contain the call node.
        target_class: Where the function lives. Accepts:
            - Native class:  "Pawn", "PlayerController", "Actor", ...
            - Bare BP name:  "BP_Manager"  (auto-resolves to /Game/Blueprints/BP_Manager)
            - Game path:     "/Game/X/BP_Y"
            - Class path:    "/Game/X/BP_Y.BP_Y_C"
        function_name: Name of the function on that class.
        anchor_name: Label for the new node.
        position_x, position_y: Graph position.
        target_pin: **v6 optional** — `<anchor>.<pin>` of a pin that produces an
            object reference compatible with `target_class`. If provided, this
            tool will auto-wire that pin → the call node's `self` input pin,
            saving a `connect_pins` call.
            Example: target_pin="get_target.ReturnValue"

    Returns:
        On success: {"ok": True, "anchor_name", "node_guid", "target_class", "function",
                     "pins": [...], "saved": True,
                     # if target_pin was provided:
                     "self_wired": True | False,
                     "self_source": "<target_pin>"  # if wired
                     "self_wire_error": "..."        # if not wired}

    Common errors:
        target_class_not_found  - couldn't resolve class via native lookup or BP load
        function_not_found      - function doesn't exist on the resolved class
        anchor_name_exists      - anchor already used
    """
    payload: dict[str, Any] = {
        "command": "call_blueprint_function",
        "blueprint": blueprint,
        "target_class": target_class,
        "function_name": function_name,
        "anchor_name": anchor_name,
        "position_x": position_x,
        "position_y": position_y,
        "target_pin": target_pin,
    }
    if graph_name:
        payload["graph_name"] = graph_name
    return _send_command(payload)


@mcp.tool()
def wire_imc_subscribe(
    blueprint: str,
    imc_path: str,
    priority: int = 0,
    anchor_prefix: str = "imc_sub",
) -> dict[str, Any]:
    """Wire the runtime "subscribe an InputMappingContext" chain in a BP's EventGraph.

    The full canonical chain for Enhanced Input to actually fire at runtime is:

        BeginPlay → AddMappingContext(MappingContext=IMC, Priority=N)
                    .self ← GetSubsystem<UEnhancedInputLocalPlayerSubsystem>
                              .PlayerController ← GetPlayerController(0)

    This tool builds **all four nodes + three connections + two pin defaults**
    in one shot. After running it, the IMC is actually active and any
    `add_enhanced_input_node` events on the same BP (or anywhere) will fire
    when their bound keys are pressed.

    Use this:
      - In a PlayerController BP, Pawn BP, or anywhere with BeginPlay access.
      - Right after `create_input_mapping_context` + `add_mapping_to_imc`.

    **v6.0.3 chain-tail insertion:** If BeginPlay.then already has an existing
    downstream chain (e.g. EnableInput), the IMC subscribe chain is appended
    AFTER the existing chain's leaf node rather than overwriting it. Walks
    up to 32 nodes; if a node along the chain has no `then` pin, falls back
    to overwriting at that point.

    Args:
        blueprint: BP where the subscribe chain is placed (typically the
            controlling BP, e.g. a PlayerController or Pawn).
        imc_path: Path to the UInputMappingContext to subscribe.
        priority: AddMappingContext priority (default 0).
        anchor_prefix: Prefix for generated anchor names. The three created
            anchors are <prefix>_get_pc / <prefix>_get_sub / <prefix>_add_ctx.
            Default "imc_sub".

    Returns:
        On success: {"ok": True, "anchors_created": [...], "imc_path", "priority", "saved": True}

    Common errors:
        blueprint_not_found / imc_not_found
        anchor_name_exists    - one of the prefix-derived anchors collides
        begin_play_unavailable - BP parent class doesn't support ReceiveBeginPlay
    """
    return _send_command({
        "command": "wire_imc_subscribe",
        "blueprint": blueprint,
        "imc_path": imc_path,
        "priority": priority,
        "anchor_prefix": anchor_prefix,
    })


@mcp.tool()
def create_input_action(
    name: str,
    value_type: str = "Boolean",
    path: str = "/Game/Input/Actions",
) -> dict[str, Any]:
    """Create a new UInputAction asset (Enhanced Input system).

    Args:
        name: Asset name (typical convention: "IA_Jump", "IA_Look").
        value_type: One of (case-insensitive):
            - "Boolean" / "bool"     - on/off (default)
            - "Axis1D" / "float"     - 1D scalar (analog trigger / scroll)
            - "Axis2D" / "Vector2D"  - 2D vector (look / movement)
            - "Axis3D" / "Vector"    - 3D vector
        path: /Game-relative folder. Defaults to /Game/Input/Actions.

    Returns:
        On success: {"ok": True, "action_path": "...", "value_type": "...", "saved": True}

    Common errors:
        unknown_value_type  - value_type not in whitelist
        asset_exists        - an asset with this name already exists at the path
    """
    return _send_command({
        "command": "create_input_action",
        "name": name,
        "value_type": value_type,
        "path": path,
    })


@mcp.tool()
def create_input_mapping_context(
    name: str,
    path: str = "/Game/Input",
) -> dict[str, Any]:
    """Create a new UInputMappingContext asset (Enhanced Input system).

    An IMC maps physical keys to UInputAction assets. After creation,
    use `add_mapping_to_imc` to add key bindings.

    Args:
        name: Asset name (typical convention: "IMC_Default").
        path: /Game-relative folder. Defaults to /Game/Input.

    Returns:
        On success: {"ok": True, "imc_path": "...", "saved": True}

    Note: To actually receive input, the player must subscribe to this IMC at
    runtime, typically via:
        `EnhancedInputLocalPlayerSubsystem.AddMappingContext(IMC, Priority)`
    in BeginPlay of the PlayerController or Pawn.
    """
    return _send_command({
        "command": "create_input_mapping_context",
        "name": name,
        "path": path,
    })


@mcp.tool()
def add_mapping_to_imc(
    imc_path: str,
    action_path: str,
    key: str,
) -> dict[str, Any]:
    """Bind a physical key to a UInputAction inside an InputMappingContext.

    Args:
        imc_path: Full path to a UInputMappingContext asset.
        action_path: Full path to a UInputAction asset.
        key: FKey name (same syntax as `add_input_key`): "P" / "Space" /
            "LeftMouseButton" / "Gamepad_FaceButton_Bottom" / ...

    Returns:
        On success: {"ok": True, "imc_path", "action_path", "key", "saved": True}

    Common errors:
        imc_not_found / action_not_found  - one of the paths failed to load
        invalid_key                       - FKey rejected the name
    """
    return _send_command({
        "command": "add_mapping_to_imc",
        "imc_path": imc_path,
        "action_path": action_path,
        "key": key,
    })


@mcp.tool()
def add_enhanced_input_node(
    blueprint: str,
    action_path: str,
    anchor_name: str,
    position_x: int = 0,
    position_y: int = 0,
) -> dict[str, Any]:
    """Add an Enhanced Input action event node to a Blueprint's EventGraph.

    The K2Node_EnhancedInputAction node listens for triggers/state changes
    on a UInputAction asset. Output exec pins: Triggered, Started, Ongoing,
    Completed, Canceled. Output value pin (type depends on the action's
    ValueType: bool / float / Vector2D / Vector).

    Args:
        blueprint: BP to add the node to.
        action_path: Full path to a UInputAction asset (create via `create_input_action`).
        anchor_name: Label for the new node.
        position_x, position_y: Graph position.

    Returns:
        On success: {"ok": True, "anchor_name", "node_guid", "action_path", "pins": [...], "saved": True}

    Note: Without IMC subscribed at runtime, this event never fires. See
    `create_input_mapping_context` + `add_mapping_to_imc` + manual IMC subscribe.
    """
    return _send_command({
        "command": "add_enhanced_input_node",
        "blueprint": blueprint,
        "action_path": action_path,
        "anchor_name": anchor_name,
        "position_x": position_x,
        "position_y": position_y,
    })


@mcp.tool()
def add_macro(
    blueprint: str,
    macro_type: str,
    anchor_name: str,
    position_x: int = 0,
    position_y: int = 0,
    graph_name: str = "",
) -> dict[str, Any]:
    """Add a macro node from UE's StandardMacros library (loops, gates, etc.).

    Use for iteration (ForEachLoop / ForLoop / WhileLoop) and flow control
    (FlipFlop / DoOnce / Gate / IsValid).

    Args:
        blueprint: Full Blueprint asset path.
        macro_type: One of (case-insensitive):
            - "ForEachLoop"  — iterate array; pins: execute / Array / LoopBody / Array Element / Array Index / Completed
            - "ForLoop"      — N times; pins: execute / FirstIndex / LastIndex / LoopBody / Index / Completed
            - "WhileLoop"    — while bool; pins: execute / Condition / LoopBody / Completed
            - "FlipFlop"     — alternates between A and B exec outs each call
            - "DoOnce"       — fires once then blocks until Reset
            - "Gate"         — Open / Close / Toggle / Enter inputs; gated exec output
            - "IsValid"      — bool branch on input ref (IsValid / IsNotValid)
        anchor_name: User-given label (unique within EventGraph).
        position_x, position_y: Graph position.

    Returns:
        On success: {"ok": True, "anchor_name", "node_guid", "macro_type", "pins": [...], "saved": True}

    Common errors:
        unknown_macro_type     - not in v4 whitelist
        macro_graph_not_found  - StandardMacros library missing (engine install issue?)
        anchor_name_exists     - anchor already used
    """
    payload: dict[str, Any] = {
        "command": "add_macro",
        "blueprint": blueprint,
        "macro_type": macro_type,
        "anchor_name": anchor_name,
        "position_x": position_x,
        "position_y": position_y,
    }
    if graph_name:
        payload["graph_name"] = graph_name
    return _send_command(payload)


@mcp.tool()
def add_self_reference(
    blueprint: str,
    anchor_name: str,
    position_x: int = 0,
    position_y: int = 0,
    graph_name: str = "",
) -> dict[str, Any]:
    """Add a `K2Node_Self` node — outputs a "self" reference to the owning Blueprint.

    Use when the user needs to pass `this` to a function (e.g., "OnHit, register
    self with a manager"). It's a single-output node, no inputs.

    Args:
        blueprint: Full Blueprint asset path.
        anchor_name: User-given label.
        position_x, position_y: Graph position.

    Returns:
        On success: {"ok": True, "anchor_name", "node_guid", "pins": [{"name": "self", "direction": "output", ...}], "saved": True}
    """
    payload: dict[str, Any] = {
        "command": "add_self_reference",
        "blueprint": blueprint,
        "anchor_name": anchor_name,
        "position_x": position_x,
        "position_y": position_y,
    }
    if graph_name:
        payload["graph_name"] = graph_name
    return _send_command(payload)


@mcp.tool()
def add_input_key(
    blueprint: str,
    key: str,
    anchor_name: str,
    position_x: int = 0,
    position_y: int = 0,
) -> dict[str, Any]:
    """Add a `K2Node_InputKey` node — fires when a specific keyboard / mouse / gamepad key is pressed.

    This is the **legacy** input system (works in any BP that captures input).
    For modern UE projects, EnhancedInput is preferred but requires more setup
    (UInputAction assets + IMC) — not covered here in v4.

    Args:
        blueprint: Full Blueprint asset path.
        key: UE FKey name. Examples:
            - Letter keys: "P", "Q", "A", ...
            - "Space" (auto-aliased to UE's "SpaceBar"), "Enter", "Escape" (or "Esc"),
              "Tab", "BackSpace", "Delete"
            - Modifier shortcuts (auto-aliased to Left variant): "Ctrl", "Alt", "Shift", "Cmd"
              — pass "LeftControl" / "RightControl" explicitly for the right-side modifier
            - "LeftMouseButton", "RightMouseButton", "MiddleMouseButton"
            - "Up", "Down", "Left", "Right" (arrow keys)
            - "F1" through "F12"
            - Gamepad: "Gamepad_FaceButton_Bottom", "Gamepad_LeftThumbstick_X", ...
        anchor_name: User-given label.
        position_x, position_y: Graph position.

    Returns:
        On success: {"ok": True, "anchor_name", "node_guid", "key": "...",
                     "pins": [
                       {"name": "Pressed", "direction": "output", "type": "exec"},
                       {"name": "Released", "direction": "output", "type": "exec"},
                       {"name": "Key", "direction": "output", "type": "struct"}
                     ], "saved": True}

    Common errors:
        invalid_key  - FKey constructor rejected the name; check spelling
    """
    return _send_command({
        "command": "add_input_key",
        "blueprint": blueprint,
        "key": key,
        "anchor_name": anchor_name,
        "position_x": position_x,
        "position_y": position_y,
    })


@mcp.tool()
def delete_node(
    blueprint: str,
    anchor_name: str,
    graph_name: str = "",
) -> dict[str, Any]:
    """Delete a node from a Blueprint's EventGraph (breaks all its pin links).

    Use for refactoring: "remove the print node", "delete the timer call".
    Auto-spawned well-known events (begin_play / tick / ...) can be re-summoned
    later by `connect_pins` or `set_pin_default` referencing the same short
    name (auto-spawn-on-demand re-creates them).

    Args:
        blueprint: Full Blueprint asset path.
        anchor_name: The anchor of the node to delete.

    Returns:
        On success: {"ok": True, "anchor_name": "...", "node_type": "K2Node_...", "saved": True}

    Common errors:
        anchor_not_found  - the anchor doesn't match any existing node (strict lookup; no auto-spawn here)
    """
    payload: dict[str, Any] = {
        "command": "delete_node",
        "blueprint": blueprint,
        "anchor_name": anchor_name,
    }
    if graph_name:
        payload["graph_name"] = graph_name
    return _send_command(payload)


@mcp.tool()
def disconnect_pins(
    blueprint: str,
    from_pin: str,
    to_pin: str,
    graph_name: str = "",
) -> dict[str, Any]:
    """Break the connection between two pins. Inverse of `connect_pins`.

    Use when refactoring graph topology: "disconnect that wire" / "unwire
    BeginPlay from Print" / "remove this connection".

    Args:
        blueprint: Full Blueprint asset path.
        from_pin: Source pin "<anchor>.<pin>". Same syntax as connect_pins.
        to_pin: Target pin "<anchor>.<pin>".

    Returns:
        On success: {"ok": True, "from": "...", "to": "...", "saved": True}

    Common errors:
        anchor_not_found  - either anchor doesn't exist
        pin_not_found     - pin name typo on one side
        not_connected     - the pins were never connected to begin with
    """
    payload: dict[str, Any] = {
        "command": "disconnect_pins",
        "blueprint": blueprint,
        "from_pin": from_pin,
        "to_pin": to_pin,
    }
    if graph_name:
        payload["graph_name"] = graph_name
    return _send_command(payload)


@mcp.tool()
def add_branch(
    blueprint: str,
    anchor_name: str,
    position_x: int = 0,
    position_y: int = 0,
    graph_name: str = "",
) -> dict[str, Any]:
    """Add a `K2Node_IfThenElse` (Branch) node — the if/else of Blueprints.

    Use when the user asks for "if condition then X else Y" / "branch on bool".

    Args:
        blueprint: Full Blueprint asset path.
        anchor_name: User-given label (unique within EventGraph).
        position_x, position_y: Graph position.

    Returns:
        On success: {"ok": True, "anchor_name", "node_guid", "node_type": "K2Node_IfThenElse",
                     "pins": [...], "saved": True}

    Pins generated by UE:
        - execute (exec input)
        - Condition (bool input) — the boolean to test
        - then (exec output) — fires if Condition is True
        - else (exec output) — fires if Condition is False

    Wire pattern:
        connect_pins(bp, "some_event.then",        "my_branch.execute")
        connect_pins(bp, "some_get_var.MyVar",     "my_branch.Condition")
        connect_pins(bp, "my_branch.then",         "do_if_true.execute")
        connect_pins(bp, "my_branch.else",         "do_if_false.execute")
    """
    payload: dict[str, Any] = {
        "command": "add_branch",
        "blueprint": blueprint,
        "anchor_name": anchor_name,
        "position_x": position_x,
        "position_y": position_y,
    }
    if graph_name:
        payload["graph_name"] = graph_name
    return _send_command(payload)


@mcp.tool()
def add_cast(
    blueprint: str,
    target_class: str,
    anchor_name: str,
    position_x: int = 0,
    position_y: int = 0,
    graph_name: str = "",
) -> dict[str, Any]:
    """Add a `K2Node_DynamicCast` (Cast To <Class>) node.

    Use to narrow a generic Object reference to a specific class. Common pattern:
    "On ActorBeginOverlap, cast OtherActor to Pawn — if cast succeeded, do X."

    Args:
        blueprint: Full Blueprint asset path.
        target_class: Class to cast to. v3 whitelist (case-insensitive):
            - "Actor" / "Pawn" / "Character"
            - "PlayerController" / "PlayerCameraManager" / "PlayerState"
            - "GameMode" / "GameModeBase" / "HUD"
          Or any fully-qualified UClass name.
        anchor_name: User-given label (unique within EventGraph).
        position_x, position_y: Graph position.

    Returns:
        On success: {"ok": True, "anchor_name", "node_guid",
                     "node_type": "K2Node_DynamicCast", "target_class": "Pawn",
                     "pins": [...], "saved": True}

    Pins generated by UE:
        - execute (exec input)
        - Object (object input) — the value to cast
        - then (exec output) — fires if cast succeeded
        - As<TargetClass> (object output) — the casted reference (typed!)
        - CastFailed (exec output) — fires if cast failed (Object was wrong type)

    Common errors:
        unknown_target_class  - not in v3 whitelist + not a loaded UClass name
        anchor_name_exists    - anchor already used
    """
    payload: dict[str, Any] = {
        "command": "add_cast",
        "blueprint": blueprint,
        "target_class": target_class,
        "anchor_name": anchor_name,
        "position_x": position_x,
        "position_y": position_y,
    }
    if graph_name:
        payload["graph_name"] = graph_name
    return _send_command(payload)


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
    params: list[dict[str, str]] | None = None,
    graph_name: str = "",
) -> dict[str, Any]:
    """Add a `K2Node_CustomEvent` (the **red event** node) to the EventGraph.

    Custom events are user-defined entry points that other nodes (or delegates,
    like `Set Timer by Event`'s `Event` pin) can fire. They have an **output exec
    pin** ("then") and **no input exec** (they ARE the entry).

    Use this when the user asks to "make a custom event", "define a callback",
    or when wiring `Set Timer by Event` / delegate pins that need a target event.

    **v7.5 — parameters**: pass ``params`` to add typed output pins that downstream
    nodes can read. Param ``type`` uses the same syntax as ``add_variable``::

        params=[
            {"name": "Damage", "type": "float"},
            {"name": "HitActor", "type": "object:Actor"},
            {"name": "WasCrit", "type": "bool"},
        ]

    For event-dispatcher binding (v7.6), the param list MUST match the dispatcher's
    delegate signature exactly (name + order + types).

    Args:
        blueprint: Full Blueprint asset path.
        event_name: The custom event's logical name (becomes its `CustomFunctionName`).
            Must be unique within this Blueprint's EventGraph.
        anchor_name: User-given label (visible as NodeComment). Must be unique
            across all nodes in the EventGraph.
        position_x, position_y: Graph position (default 0, 0).
        params: Optional list of ``{"name": str, "type": str}`` dicts. Param ``type``
            supports primitives (``bool``, ``int``, ``float``, ``string``, ``name``),
            ``TimerHandle``, object refs (``object:Actor``), class refs (``class:Pawn``),
            and arrays (``int[]``, ``object:Actor[]``).

    Returns:
        On success: {"ok": True, "anchor_name": ..., "event_name": ...,
                     "node_guid": ..., "param_count": N, "pins": [...], "saved": True}

    Common errors:
        anchor_name_exists       - another node already uses this anchor
        event_name_exists        - another custom event in this BP has the same name
        param_arity_mismatch     - (shouldn't happen via Python; only via direct JSON)
        unknown_param_type       - a param's ``type`` is unknown
    """
    payload: dict[str, Any] = {
        "command": "add_custom_event",
        "blueprint": blueprint,
        "event_name": event_name,
        "anchor_name": anchor_name,
        "position_x": position_x,
        "position_y": position_y,
    }
    if params:
        # Normalize each param dict to ensure only name/type keys hit the wire
        payload["params"] = [
            {"name": str(p["name"]), "type": str(p["type"])}
            for p in params
            if "name" in p and "type" in p
        ]
    if graph_name:
        payload["graph_name"] = graph_name
    return _send_command(payload)


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
        variable_type: v1+v5 whitelist (case-insensitive):
            - `bool` / `int` / `float` (alias: `double`, `real`) / `string` / `name` / `text`
            - **`TimerHandle`** — the FTimerHandle struct (essential for timer cancellation)
            - **v5 arrays:** append `[]` to any primitive — `int[]`, `float[]`, `string[]`,
              `bool[]`, `name[]`. (TimerHandle[] is not supported.)
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
    graph_name: str = "",
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
    payload: dict[str, Any] = {
        "command": "add_variable_get",
        "blueprint": blueprint,
        "variable_name": variable_name,
        "anchor_name": anchor_name,
        "position_x": position_x,
        "position_y": position_y,
    }
    if graph_name:
        payload["graph_name"] = graph_name
    return _send_command(payload)


@mcp.tool()
def add_variable_set(
    blueprint: str,
    variable_name: str,
    anchor_name: str,
    position_x: int = 0,
    position_y: int = 0,
    graph_name: str = "",
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
    payload: dict[str, Any] = {
        "command": "add_variable_set",
        "blueprint": blueprint,
        "variable_name": variable_name,
        "anchor_name": anchor_name,
        "position_x": position_x,
        "position_y": position_y,
    }
    if graph_name:
        payload["graph_name"] = graph_name
    return _send_command(payload)


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
# v8 — agentic closed loop: PIE control, simulated input, log capture
# ---------------------------------------------------------------------------


@mcp.tool()
def read_log_capture(
    max_lines: int = 100,
    category: str = "",
    verbosity: str = "",
    contains: str = "",
) -> dict[str, Any]:
    """Read recent UE log lines captured by the plugin's FOutputDevice — v8.1.

    The plugin installs a global log capture at module startup. Every
    ``UE_LOG`` / ``PrintString`` line goes into a thread-safe circular buffer
    (default cap: 1000 lines). This tool reads + filters the buffer.

    Args:
        max_lines: Limit on returned lines (default 100). 0 = no cap.
        category: If non-empty, only return lines whose log category contains
            this string (case-insensitive). E.g. ``"BlueprintUserMessages"``
            for PrintString output.
        verbosity: If non-empty, only return lines whose verbosity contains this
            string. E.g. ``"Warning"``, ``"Error"``.
        contains: If non-empty, only return lines containing this substring
            (case-insensitive).

    Returns:
        ``{"ok": True, "total_captured": N, "returned": M, "lines": [...]}``
        Each line is formatted ``[Category][Verbosity] message``.

    Use this after triggering an action to see what UE logged.
    """
    payload: dict[str, Any] = {
        "command": "read_log_capture",
        "max_lines": max_lines,
    }
    if category:
        payload["category"] = category
    if verbosity:
        payload["verbosity"] = verbosity
    if contains:
        payload["contains"] = contains
    return _send_command(payload)


@mcp.tool()
def clear_log_capture() -> dict[str, Any]:
    """Empty the log capture buffer — v8.1.

    Use this before triggering an action to make sure subsequent
    ``read_log_capture`` only shows new output.
    """
    return _send_command({"command": "clear_log_capture"})


@mcp.tool()
def start_pie() -> dict[str, Any]:
    """Start a PIE (Play In Editor) session — v8.2.

    Equivalent to clicking the "Play" toolbar button. The actual start is
    queued and processed on the next editor tick — ``is_pie_running`` will
    return ``running=false`` for one tick after this returns, even on success.

    Returns:
        ``{"ok": True, "queued": True}`` if request accepted.
        ``{"ok": False, "error": "pie_already_running"}`` if a session is active.
    """
    return _send_command({"command": "start_pie"})


@mcp.tool()
def stop_pie() -> dict[str, Any]:
    """End the active PIE session — v8.2.

    Equivalent to pressing Esc in PIE or clicking "Stop" on the toolbar.

    Returns:
        ``{"ok": True, "queued": True}`` if request accepted.
        ``{"ok": False, "error": "pie_not_running"}`` if no session.
    """
    return _send_command({"command": "stop_pie"})


@mcp.tool()
def is_pie_running() -> dict[str, Any]:
    """Query whether a PIE session is currently active — v8.2.

    Returns:
        ``{"ok": True, "running": bool, "start_queued": bool}``
        ``running=True`` iff GEditor->PlayWorld is non-null (session has actually
        started). ``start_queued=True`` iff a start was requested but hasn't
        ticked through yet.
    """
    return _send_command({"command": "is_pie_running"})


@mcp.tool()
def pie_press_key(key: str, player_index: int = 0) -> dict[str, Any]:
    """Simulate a key press (press + release) on the PIE PlayerController — v8.3.

    Routes through ``APlayerController::InputKey(FInputKeyParams)`` so it works
    for both legacy input and Enhanced Input (whichever is bound).

    Args:
        key: Key name (`"Space"`, `"P"`, `"LeftMouseButton"`, `"F1"`, etc.).
            Aliases applied via the same ``ResolveFKeyWithAliases`` helper
            that ``add_input_key`` uses, so ``"Space"`` → ``"SpaceBar"`` etc.
        player_index: Which local player to target (default 0 — single-player).

    Returns:
        ``{"ok": True, "key": "<canonical key>", "player_index": N}``
        Errors: ``pie_not_running``, ``no_player_controller``, ``invalid_key``.

    Note: PIE must already be running (``start_pie`` + wait for the tick).
    """
    if not key:
        return {"ok": False, "error": "missing_argument"}
    return _send_command({
        "command": "pie_press_key",
        "key": key,
        "player_index": player_index,
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
