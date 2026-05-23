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


def _send_command(
    command_payload: dict[str, Any],
    timeout_sec: float | None = None,
) -> dict[str, Any]:
    """Send a JSON command to the UE plugin via TCP, return the parsed response.

    Lower-level helper. Tools should call this rather than open sockets directly,
    so the error-handling shape stays consistent.

    Args:
        command_payload: The JSON command dict (must include a "command" field).
        timeout_sec: **v9.16.0** — optional per-call timeout override (in seconds).
            Default ``UE_PLUGIN_TIMEOUT_SEC=12``. Use a larger value (e.g. 75) for
            slow ops like ``compile_material`` (shader compile can be 30+s).

    Returns:
        On UE plugin success: the parsed JSON dict (e.g., {"ok": true, ...}).
        On connection / parse failure: a synthetic error dict — never raises.
    """
    payload_bytes = (json.dumps(command_payload) + "\n").encode("utf-8")
    log.info("→ UE plugin: %s", command_payload.get("command", "<no-command>"))

    effective_timeout = timeout_sec if timeout_sec is not None else UE_PLUGIN_TIMEOUT_SEC
    try:
        with socket.create_connection(
            (UE_PLUGIN_HOST, UE_PLUGIN_PORT), timeout=effective_timeout
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

    **v9.15.0** — array-index syntax ``Name[N]`` on any token. Auto-grows
    the array when N is past the current size. The most common use is
    setting material slots on a mesh component::

        property_name="OverrideMaterials[0]"  value="/Game/Materials/M_HeightColor"
        property_name="OverrideMaterials[2]"  value="/Game/Materials/M_Detail"

    Closes 2026-05-23 feature request #4.

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
    """Add a Select node (``K2Node_Select``) — v7.2 + v9.14.0 num_options fix.

    N-way chooser: ``Index`` picks one of N option inputs and outputs its
    value. Value type is wildcard until you connect the first option.

    **v9.14.0**: ``num_options`` now actually grows past 2. Previous
    behavior silently capped at 2 (rev8 ISSUE-1). Uses
    ``UK2Node_Select::AddInputPin()`` in a loop, which automatically
    flips the index pin from bool → int once you exceed 2.

    Args:
        blueprint: BP asset path.
        anchor_name: Unique label.
        num_options: Number of option input pins (``Option 0``, ``Option 1``,
            …, ``Option N-1``). Default 2. Clamped to [2, 64].
        position_x, position_y: Graph coordinates.

    Returns:
        ``{"ok": True, "anchor_name": "...", "num_options": N,
            "node_guid": "...", "pins": [...], "saved": True}``
        ``num_options`` in the response is the ACTUAL count after
        ``CanAddPin`` gating (will equal the request unless UE refused
        a particular add).
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
def migrate_dispatchers(
    blueprint: str,
    recreate_ghosts: bool = False,
) -> dict[str, Any]:
    """Repair old-format event dispatchers in-place — v8.0.2 + v8.1.0 ghost recreate.

    Three damage modes are now covered:

    **Mode 1 — "graph present, variable missing"** (pre-v7.1.2 partial damage)
        Signature graph exists in ``Blueprint->DelegateSignatureGraphs`` but no
        matching ``PC_MCDelegate`` member variable. Pass 1 back-fills the variable.
        Reported in ``migrated`` array.

    **Mode 2 — "variable present, graph missing"** (rare, opposite imbalance)
        Member variable of type ``PC_MCDelegate`` exists but no signature graph.
        Pass 2 detects but doesn't auto-clean — use ``delete_event_dispatcher``.
        Reported in ``orphan_variables`` array.

    **Mode 3 — "ghost dispatcher" — both missing** (pre-v7.1.2 full damage) — v8.1.0
        Neither signature graph nor member variable survived, but the BP still
        contains ``K2Node_CallDelegate`` / ``K2Node_AddDelegate`` / ``K2Node_RemoveDelegate``
        nodes referencing the lost dispatcher by name. Pass 3 scans every graph
        (EventGraph + UbergraphPages + FunctionGraphs + MacroGraphs) for these
        orphan references and collects unique names.

        With ``recreate_ghosts=True``, Pass 4 recreates each ghost via the full
        v7.1.2 flow (`AddMemberVariable PC_MCDelegate` + signature graph + schema
        setup), **with an empty parameter signature** — caller adds params later
        via direct editor work if needed. The old nodes' pin layout is NOT
        inferred (documented limit). Re-running ``add_call_dispatcher`` etc.
        after this will succeed.

    Healthy BPs pass through unchanged (``compiled=false``, ``saved=false``).
    Compile + save are batched once at end, only if anything was actually
    changed (Mode 1 backfill or Mode 3 recreate).

    Args:
        blueprint: BP asset path to scan.
        recreate_ghosts: v8.1.0 — opt-in. If True, ghost dispatchers detected in
            Pass 3 are recreated with empty signatures. Default False so a
            "dry run" just reports what would be done.

    Returns:
        ``{"ok": True, "blueprint": "...",
           "migrated_count": N,            "migrated": [...names...],
           "already_healthy_count": N,     "already_healthy": [...names...],
           "orphan_variable_count": N,     "orphan_variables": [...names...],
           "ghosts_detected_count": N,     "ghosts_detected": [...names...],
           "ghosts_recreated_count": N,    "ghosts_recreated": [...names...],
           "recreate_ghosts_requested": bool,
           "compiled": bool, "saved": bool}``

        ``ghosts_detected`` ⊇ ``ghosts_recreated`` (recreated is a subset of detected
        when ``recreate_ghosts=True``).

    Idempotent: re-running with ``recreate_ghosts=True`` after a successful migration
    is safe — all dispatchers now have both pieces, so subsequent passes report
    everything as ``already_healthy`` with no further changes.
    """
    if not blueprint:
        return {"ok": False, "error": "missing_argument"}
    payload: dict[str, Any] = {
        "command": "migrate_dispatchers",
        "blueprint": blueprint,
    }
    if recreate_ghosts:
        payload["recreate_ghosts"] = True
    return _send_command(payload)


# ---------------------------------------------------------------------------
# v9.5.0 — silent dispatcher auto-migration (Python-only conveniences)
# ---------------------------------------------------------------------------
# These wrap the existing v8.1.0 ``migrate_dispatchers`` C++ machinery —
# no plugin changes needed. They flip the default from "dry-run" to
# "silently fix everything," and add a project-wide variant.


@mcp.tool()
def auto_migrate_dispatchers(blueprint: str) -> dict[str, Any]:
    """Silent migration: fix ALL dispatcher damage modes in one call — v9.5.0.

    Convenience alias for ``migrate_dispatchers(blueprint, recreate_ghosts=True)``.
    Where the v8.1.0 default is "dry-run + report," this one actually
    applies every fix:
      - Mode 1 (graph w/o variable)        → back-fills the PC_MCDelegate variable
      - Mode 2 (variable w/o graph)        → reported as orphan_variables (delete via delete_event_dispatcher)
      - Mode 3 (ghost — both missing)      → recreates with empty signature

    Idempotent — re-running on an already-healthy BP is a no-op
    (``compiled=false``, ``saved=false``).

    Args:
        blueprint: BP asset path to fix.

    Returns:
        Same shape as ``migrate_dispatchers``. Most fields will be 0 /
        empty arrays after the first successful run.
    """
    return migrate_dispatchers(blueprint=blueprint, recreate_ghosts=True)


@mcp.tool()
def auto_migrate_all_dispatchers(
    folder: str = "/Game",
    dry_run: bool = False,
) -> dict[str, Any]:
    """Project-wide silent dispatcher migration — v9.5.0.

    Walks every Blueprint under ``folder`` (via ``list_blueprints``) and
    runs ``auto_migrate_dispatchers`` on each. Returns a per-BP summary
    plus aggregate totals. Designed for a one-shot "fix everything in
    my project" command after upgrading the plugin.

    Args:
        folder: Root /Game-relative folder to scan. Default ``/Game``.
        dry_run: If True, runs ``migrate_dispatchers`` in detect-only
            mode (same as the v8.1.0 default) — useful to preview what
            WOULD be changed. Default False (apply all fixes).

    Returns:
        ``{"ok": True,
           "folder": "...",
           "dry_run": bool,
           "blueprint_count": N,
           "total_migrated": N,         "total_ghosts_recreated": N,
           "total_orphan_variables": N, "total_ghosts_detected": N,
           "compiled_count": N,         "saved_count": N,
           "results": [{"blueprint": "...", "migrated_count": N, ...}, ...],
           "errors": [{"blueprint": "...", "error": "...", "detail": "..."}, ...]}``

        ``results`` contains the full per-BP migrate_dispatchers response.
        ``errors`` collects any per-BP failures so a single bad BP doesn't
        abort the whole sweep.
    """
    blueprints_r = list_blueprints(folder=folder, max_results=10000)
    if not blueprints_r.get("ok"):
        return {
            "ok": False,
            "error": "list_blueprints_failed",
            "detail": blueprints_r.get("error", "<unknown>"),
        }

    results: list[dict[str, Any]] = []
    errors: list[dict[str, Any]] = []
    totals = {
        "migrated": 0,
        "ghosts_recreated": 0,
        "orphan_variables": 0,
        "ghosts_detected": 0,
        "compiled": 0,
        "saved": 0,
    }

    for asset in blueprints_r.get("assets", []):
        # asset["path"] is the full /Game/.../Name.Name form; we want just
        # the package path for migrate_dispatchers.
        bp_path = asset["path"].split(".")[0]
        r = (
            migrate_dispatchers(blueprint=bp_path)
            if dry_run
            else auto_migrate_dispatchers(blueprint=bp_path)
        )
        if not r.get("ok"):
            errors.append({
                "blueprint": bp_path,
                "error": r.get("error", "<unknown>"),
                "detail": r.get("detail", ""),
            })
            continue
        results.append(r)
        totals["migrated"] += r.get("migrated_count", 0)
        totals["ghosts_recreated"] += r.get("ghosts_recreated_count", 0)
        totals["orphan_variables"] += r.get("orphan_variable_count", 0)
        totals["ghosts_detected"] += r.get("ghosts_detected_count", 0)
        if r.get("compiled"):
            totals["compiled"] += 1
        if r.get("saved"):
            totals["saved"] += 1

    return {
        "ok": True,
        "command": "auto_migrate_all_dispatchers",
        "folder": folder,
        "dry_run": dry_run,
        "blueprint_count": len(blueprints_r.get("assets", [])),
        "total_migrated": totals["migrated"],
        "total_ghosts_recreated": totals["ghosts_recreated"],
        "total_orphan_variables": totals["orphan_variables"],
        "total_ghosts_detected": totals["ghosts_detected"],
        "compiled_count": totals["compiled"],
        "saved_count": totals["saved"],
        "results": results,
        "errors": errors,
    }


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

    **Coverage limit:** Returns ``dispatcher_not_found`` for "ghost dispatcher"
    state (pre-v7.1.2 BPs where neither piece survived). That's not an error
    state to fix — there's literally nothing on the BP referencing the
    dispatcher anymore. Just ``add_event_dispatcher`` to recreate.

    Args:
        blueprint: BP asset path.
        dispatcher_name: Name of the dispatcher to remove.

    Returns:
        ``{"ok": True, "dispatcher_name": ..., "removed_graph": bool,
            "removed_variable": bool, "compiled": True, "saved": True}``

    Errors:
        dispatcher_not_found — neither a signature graph nor a member variable
            of that name exists. For "ghost dispatcher" state this is expected;
            use ``add_event_dispatcher`` to recreate from scratch.
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
    rotation: list[float] | None = None,
    scale: list[float] | None = None,
) -> dict[str, Any]:
    """Spawn a Blueprint instance into the current level — v0 + v9.11.0 + v9.12.0.

    Use this after `compile_blueprint` to place the Blueprint into the world so
    its BeginPlay etc. will fire when the user presses Play.

    **v9.11.0 persistence fix**: the level package is now marked dirty after
    spawn so ``save_all()`` persists the spawn.

    **v9.12.0 full-transform spawn**: now accepts both ``rotation`` AND
    ``scale``. One call places the actor at full pose — no intermediate
    ``(1,1,1)`` scale state for ``save_all`` / PIE / re-compile to capture.

    Args:
        blueprint: Full Blueprint asset path. MUST have been compiled (BS_UpToDate);
            spawning an unbuilt or error-state BP returns `no_generated_class`.
        location_x/y/z: World-space spawn location (default 0,0,0).
            Doesn't matter for non-spatial BPs like PrintString demos.
        rotation: **v9.11.0** — optional ``[Pitch, Yaw, Roll]`` in degrees.
            Default ``None`` = identity rotation.
        scale: **v9.12.0** — optional ``[X, Y, Z]`` scale. Default ``None``
            = whatever the BP CDO's RootComponent.RelativeScale3D is
            (usually ``(1, 1, 1)``). For "an 11×3×2 wall" the right move
            is now one ``spawn_actor(..., scale=[11, 3, 2])`` instead
            of spawn + ``set_actor_transform``.

    Returns:
        On success: ``{"ok": True, "blueprint_path": ..., "actor_name": "<UE-assigned>",
                       "actor_label": "<Outliner label>",
                       "location": [x, y, z], "rotation": [P, Y, R],
                       "scale": [X, Y, Z]}``
        On error:   ``{"ok": False, "error": "...", "detail": "..."}``

    After spawning, the user must press the **Play** button (top toolbar) to
    enter PIE (Play In Editor) mode. The BP's BeginPlay (if any) fires there.

    **Gotchas to know**:
    - ``compile_blueprint`` triggers REINSTANCE of all spawned actors of that
      BP — the underlying UObject gets replaced and ``actor_name`` changes.
      After recompile, re-fetch the current name via ``list_level_actors``
      before using ``set_actor_transform`` / ``set_actor_property`` /
      ``delete_actor``. Don't cache the post-spawn name across recompiles.
    - The level must be writable (no checkout/source-control block).

    Common errors:
        blueprint_not_found  - path doesn't exist
        no_generated_class   - BP not compiled yet (call compile_blueprint first)
        not_actor_subclass   - BP's parent is not AActor; can't spawn into world
        no_actor_subsystem   - GEditor / subsystem unavailable (shouldn't happen in editor)
        spawn_failed         - UE refused to spawn (rare; e.g., level not writable)
        game_thread_timeout  - 10s deadline exceeded
    """
    payload: dict[str, Any] = {
        "command": "spawn_actor",
        "blueprint": blueprint,
        "location_x": location_x,
        "location_y": location_y,
        "location_z": location_z,
    }
    if rotation is not None and len(rotation) >= 3:
        payload["rotation"] = list(rotation)[:3]
    if scale is not None and len(scale) >= 3:
        payload["scale"] = list(scale)[:3]
    return _send_command(payload)


# ---------------------------------------------------------------------------
# v9.7.0 — Level / instance manipulation
# ---------------------------------------------------------------------------
# Closes feature-request gaps #2/#3/#6 (the highest-priority block):
# read what's in the level, move spawned actors, set per-instance properties.
#
# Actor lookup accepts either ``GetName()`` (returned by spawn_actor) OR
# ``GetActorLabel()`` (the Outliner display name).


@mcp.tool()
def list_level_actors(
    class_filter: str = "",
    name_contains: str = "",
    max_results: int = 500,
    include_bounds: bool = False,
) -> dict[str, Any]:
    """List actors in the current editor level — v9.7.0 + v9.11.0 bounds.

    Lets the LLM see the level layout instead of being blind to the scene.
    Each actor is reported with its `name` (canonical UE name, what
    ``spawn_actor`` returns), `label` (Outliner display), `class`, and
    world-space `location` [X, Y, Z].

    Args:
        class_filter: Restrict to actors of this class. Accepts a bare
            class name (e.g. ``"StaticMeshActor"``) or a full
            ``"/Script/Engine.StaticMeshActor"`` path.
        name_contains: Case-insensitive substring match against both
            `name` and `label`.
        max_results: Cap on number of actors returned (default 500).
        include_bounds: **v9.11.0** — if True, each actor includes
            ``bounds_origin`` and ``bounds_extent`` (world-space OBB,
            same shape as ``AActor::GetActorBounds``). Useful for
            scanning a level for "what can I place stuff against."
            Off by default — bounds query has a per-actor cost.

    Returns:
        ``{"ok": True, "actors": [{"name", "label", "class", "location",
                                    + "bounds_origin", "bounds_extent" if include_bounds}, ...],
            "count": N, "class_filter": "..."}``

    Common errors:
        class_not_found       — class_filter didn't resolve to a UClass
        no_editor / no_actor_subsystem — editor unavailable
    """
    payload: dict[str, Any] = {
        "command": "list_level_actors",
        "max_results": max_results,
    }
    if class_filter:
        payload["class_filter"] = class_filter
    if name_contains:
        payload["name_contains"] = name_contains
    if include_bounds:
        payload["include_bounds"] = True
    return _send_command(payload)


@mcp.tool()
def get_actor_transform(actor: str) -> dict[str, Any]:
    """Get the world-space transform + bounds of a level actor —
    v9.7.0 + v9.11.0 bounds.

    Args:
        actor: Actor name (`GetName()`) or label (`GetActorLabel()`).

    Returns:
        ``{"ok": True, "actor": "...", "label": "...", "class": "...",
            "location": [X, Y, Z], "rotation": [Pitch, Yaw, Roll],
            "scale": [X, Y, Z],
            "bounds_origin": [X, Y, Z], "bounds_extent": [X, Y, Z]}``

        ``bounds_origin/extent`` are the world-space OBB (same shape as
        ``AActor::GetActorBounds``) — origin is the bounds center,
        extent is the HALF-size on each axis. World min = origin - extent,
        world max = origin + extent. Useful for vertex/edge/face math.

    Common errors:
        actor_not_found — no actor with that name or label
    """
    if not actor:
        return {"ok": False, "error": "missing_argument"}
    return _send_command({"command": "get_actor_transform", "actor": actor})


@mcp.tool()
def get_actor_bounds(actor: str) -> dict[str, Any]:
    """Get an actor's bounds without the transform overhead — v9.11.0.

    Same world-space OBB as ``get_actor_transform`` but isolated. Also
    returns ``world_min`` / ``world_max`` pre-computed (origin ± extent)
    so callers don't have to do the arithmetic, plus ``mesh_local_extent``
    when the root is a StaticMeshComponent — the asset's intrinsic
    pre-scale size, useful for "what does scale=(2,2,2) mean in cm?"
    reasoning.

    Args:
        actor: Actor name or label.

    Returns:
        ``{"ok": True, "actor": "...",
            "world_origin": [X, Y, Z], "world_extent": [X, Y, Z],
            "world_min": [X, Y, Z],    "world_max": [X, Y, Z],
            "mesh_local_extent": [X, Y, Z] (zero if root isn't a StaticMesh),
            "mesh_asset": "/Game/..." or ""}``

    Common errors:
        actor_not_found
    """
    if not actor:
        return {"ok": False, "error": "missing_argument"}
    return _send_command({"command": "get_actor_bounds", "actor": actor})


@mcp.tool()
def set_actor_transform(
    actor: str,
    location: list[float] | None = None,
    rotation: list[float] | None = None,
    scale: list[float] | None = None,
) -> dict[str, Any]:
    """Move / rotate / scale a level actor (no re-spawn) — v9.7.0.

    Any of the three components can be omitted — only fields that are
    passed get applied. Marks the level package dirty so ``save_all()``
    persists the move.

    Args:
        actor: Actor name or label.
        location: ``[X, Y, Z]`` — world position. None = leave unchanged.
        rotation: ``[Pitch, Yaw, Roll]`` (degrees). None = leave unchanged.
        scale: ``[X, Y, Z]`` — relative scale. None = leave unchanged.

    Returns:
        ``{"ok": True, "actor": "...", "moved": bool,
            "location": [...], "rotation": [...], "scale": [...]}``
        Note: returned location/rotation/scale are the actor's final state,
        which includes any unchanged components.

    Common errors:
        actor_not_found
        no_change_specified — passed none of location/rotation/scale
    """
    if not actor:
        return {"ok": False, "error": "missing_argument"}
    payload: dict[str, Any] = {"command": "set_actor_transform", "actor": actor}
    if location is not None:
        payload["location"] = list(location)
    if rotation is not None:
        payload["rotation"] = list(rotation)
    if scale is not None:
        payload["scale"] = list(scale)
    return _send_command(payload)


@mcp.tool()
def set_actor_property(
    actor: str,
    property: str,
    value: str = "",
) -> dict[str, Any]:
    """Set a property on a level actor INSTANCE — v9.7.0.

    Per-instance setter via FProperty reflection. **Different from
    ``set_component_property``** — that one writes to the Blueprint CDO
    (every spawned instance gets the value). This one writes only to the
    specific actor in the level.

    For ``AActor``-typed properties (e.g. ``LinkedPortal: ABP_Portal*``),
    ``value`` can be **another actor's name or label** — it's resolved
    against the level before falling back to asset-path lookup. This is
    the canonical "double portal" wiring: ``set_actor_property("PortalA",
    "LinkedPortal", "PortalB")``.

    Dot-notation walks into struct fields:
    ``"BodyInstance.CollisionProfileName"``.

    Args:
        actor: Actor name or label.
        property: Property name or dot-separated path.
        value: New value. Empty / "None" / "null" clears object refs.
            For struct types accepts shorthand (e.g. ``"1,2,3"`` for
            Vector). For object refs: another actor's name OR an asset
            path.

    Returns:
        ``{"ok": True, "actor": "...", "property": "...",
            "resolved_value": "..."}``

    Common errors:
        actor_not_found
        property_not_found
        set_failed         — value couldn't be parsed/resolved (see `detail`)
    """
    if not actor or not property:
        return {"ok": False, "error": "missing_argument"}
    return _send_command({
        "command": "set_actor_property",
        "actor": actor,
        "property": property,
        "value": value,
    })


@mcp.tool()
def delete_actor(actor: str) -> dict[str, Any]:
    """Destroy a level actor — v9.7.0.

    Uses ``UEditorActorSubsystem::DestroyActor``. The actor is removed
    from the level and freed; level package is marked dirty.

    Args:
        actor: Actor name or label.

    Returns:
        ``{"ok": True, "actor": "...", "destroyed": True}``

    Common errors:
        actor_not_found
    """
    if not actor:
        return {"ok": False, "error": "missing_argument"}
    return _send_command({"command": "delete_actor", "actor": actor})


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
            - **Object ref** (v6.0.2+): asset path like "/Engine/BasicShapes/Cube.Cube"
            - **Class ref** (v6.0.2+, **rev7-verified**): class path like
              "/Script/Engine.InstancedStaticMeshComponent". Works for both
              ``FClassProperty`` (TSubclassOf) and the Class pin on
              ``GetComponentByClass`` etc.

    Returns:
        On success: {"ok": True, "anchor_name": ..., "pin_name": ...,
                     "value": "<stored value as UE has it>", "pin_type": "...",
                     "saved": True}
        On error:   {"ok": False, "error": "...", "detail": "..."}

    Supported pin categories (current — was over-restrictive in v0 docstring):
        primitive types (string/name/text/int/int64/real/bool/byte) ✓
        struct (Vector / Rotator / Color / LinearColor — others via raw UE
        export string) ✓
        object refs (asset path) ✓
        class refs (class path) ✓
        Output pins → `pin_not_input` (defaults apply only to inputs)
        Exec pins → `exec_pin_no_default`
        delegate / wildcard / unknown struct → `unsupported_pin_type`

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
    instance_editable: bool = False,
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
            - **v7 object/class refs:** `object:Actor`, `class:Pawn`, `object:Actor[]`.
        default_value: Optional initial value as string (e.g., "true", "5.0", "hello").
            For TimerHandle and other structs, leave empty (will be a default-constructed struct).
        instance_editable: **v9.8.0** — if True, the variable will appear in the
            Details panel of every spawned instance and be editable per-instance.
            Internally clears the ``CPF_DisableEditOnInstance`` flag. Defaults
            False (private to the BP class, like UE's default).

    Returns:
        On success: ``{"ok": True, "variable_name": "...", "variable_type": "...",
                        "instance_editable": bool, "saved": True}``

    After this, use `add_variable_get` / `add_variable_set` to read/write the
    variable inside the EventGraph. To flip flags on an EXISTING variable,
    use ``set_variable_flags`` (v9.8.0).

    Common errors:
        variable_exists          - another BP variable already has this name
        unknown_variable_type    - type not in v1 whitelist
        add_failed               - UE refused to add (rare)
    """
    payload: dict[str, Any] = {
        "command": "add_variable",
        "blueprint": blueprint,
        "name": name,
        "variable_type": variable_type,
        "default_value": default_value,
    }
    if instance_editable:
        payload["instance_editable"] = True
    return _send_command(payload)


# ---------------------------------------------------------------------------
# v9.8.0 — Blueprint / variable lifecycle
# ---------------------------------------------------------------------------
# Closes feature-request gaps #1, #5, #8 from the 2026-05-21 review.


@mcp.tool()
def set_variable_flags(
    blueprint: str,
    name: str,
    instance_editable: bool | None = None,
    blueprint_read_only: bool | None = None,
    expose_on_spawn: bool | None = None,
) -> dict[str, Any]:
    """Flip flags on an existing BP variable — v9.8.0.

    Each argument is tri-state: ``None`` = leave unchanged. Pass a real
    bool to set or clear. Recompiles the BP so flag changes propagate to
    the generated FProperty.

    Args:
        blueprint: BP asset path.
        name: Variable name (must already exist — use ``add_variable`` first).
        instance_editable: Show in per-instance Details panel
            (clears ``CPF_DisableEditOnInstance``).
        blueprint_read_only: Sets ``CPF_BlueprintReadOnly`` — variable can't
            be written from BP graphs (still readable).
        expose_on_spawn: Sets metadata ``ExposeOnSpawn``=true — variable
            shows up as a pin on ``SpawnActor`` nodes that target this BP.

    Returns:
        ``{"ok": True, "variable_name": "...", "instance_editable": bool,
            "blueprint_read_only": bool, "expose_on_spawn": bool|null,
            "saved": True}``
        ``expose_on_spawn`` is ``null`` when not modified by this call.

    Common errors:
        blueprint_not_found
        variable_not_found
        no_flag_specified   - all three args were None
    """
    if not blueprint or not name:
        return {"ok": False, "error": "missing_argument"}
    payload: dict[str, Any] = {
        "command": "set_variable_flags",
        "blueprint": blueprint,
        "name": name,
    }
    if instance_editable is not None:
        payload["instance_editable"] = instance_editable
    if blueprint_read_only is not None:
        payload["blueprint_read_only"] = blueprint_read_only
    if expose_on_spawn is not None:
        payload["expose_on_spawn"] = expose_on_spawn
    return _send_command(payload)


@mcp.tool()
def delete_variable(blueprint: str, name: str) -> dict[str, Any]:
    """Remove a member variable from a Blueprint — v9.8.0.

    Uses ``FBlueprintEditorUtils::RemoveMemberVariable``. BP is marked
    structurally modified, recompiled, and saved.

    Note: this removes regular member variables only. To remove an event
    dispatcher, use ``delete_event_dispatcher`` (separate path through
    ``DelegateSignatureGraphs``).

    Args:
        blueprint: BP asset path.
        name: Variable name.

    Returns:
        ``{"ok": True, "variable_name": "...", "saved": True}``

    Common errors:
        blueprint_not_found
        variable_not_found
    """
    if not blueprint or not name:
        return {"ok": False, "error": "missing_argument"}
    return _send_command({
        "command": "delete_variable",
        "blueprint": blueprint,
        "name": name,
    })


@mcp.tool()
def delete_blueprint(path: str) -> dict[str, Any]:
    """Delete an entire Blueprint asset from disk — v9.8.0.

    Uses ``UEditorAssetLibrary::DeleteAsset`` after a sanity check that
    the asset is actually a ``UBlueprint`` (defensive against accidental
    deletion of textures / meshes through this tool).

    Warning: this is destructive and only affects the asset on disk.
    Any actor instances spawned from this BP that are still in a level
    will become invalid references — delete them first with
    ``delete_actor`` if needed.

    Args:
        path: Full /Game-relative BP asset path (e.g.
            ``"/Game/Tests/BP_Portal"``).

    Returns:
        ``{"ok": True, "blueprint_path": "...", "deleted": True}``

    Common errors:
        asset_not_found  — path doesn't resolve
        not_a_blueprint  — asset exists but isn't a UBlueprint (use a
            future ``delete_asset`` tool when it's needed)
    """
    if not path:
        return {"ok": False, "error": "missing_argument"}
    return _send_command({"command": "delete_blueprint", "path": path})


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
def add_component_get(
    blueprint: str,
    component_name: str,
    anchor_name: str,
    position_x: int = 0,
    position_y: int = 0,
    graph_name: str = "",
) -> dict[str, Any]:
    """Add a Get node for one of the BP's own components — v9.13.0.

    Closes rev7 ISSUE-1. Previously the only way to reference a BP's
    component in its EventGraph was ``GetComponentByClass``, which
    returns the FIRST component of that class — useless when the BP
    has two components of the same class. ``add_component_get`` is
    the by-name equivalent — it drops a ``K2Node_VariableGet``
    referencing the named component on ``self``, exactly like
    dragging the component from UE's Components panel into the graph.

    The output pin's type is the component's class. Wire it into any
    function's Target/Self pin that expects that class (e.g. the ISM's
    ``Target`` pin on ``AddInstance``).

    Args:
        blueprint: Full BP asset path.
        component_name: Component name as added via ``add_component`` (or
            as it appears in UE's Components panel). Inherited / native
            components are also accepted if they're UPROPERTY-declared
            on the parent class.
        anchor_name: User-given label (unique within the target graph).
        position_x, position_y: Graph position. Defaults 0, 0.
        graph_name: Function/macro graph name (default empty = EventGraph).

    Returns:
        On success: ``{"ok": True, "command": "add_component_get",
                       "anchor_name": "...", "component_name": "...",
                       "component_class": "/Script/Engine.X" (full path),
                       "node_guid": "...", "pins": [...], "saved": True}``

    Common errors:
        blueprint_not_found
        graph_not_found / no_event_graph
        component_not_found  — not in SCS AND not an inherited UPROPERTY
                                of a UActorComponent subclass
        anchor_name_exists
    """
    if not blueprint or not component_name or not anchor_name:
        return {"ok": False, "error": "missing_argument"}
    payload: dict[str, Any] = {
        "command": "add_component_get",
        "blueprint": blueprint,
        "component_name": component_name,
        "anchor_name": anchor_name,
        "position_x": position_x,
        "position_y": position_y,
    }
    if graph_name:
        payload["graph_name"] = graph_name
    return _send_command(payload)


@mcp.tool()
def list_assets(
    folder: str = "/Game",
    asset_class: str = "",
    recursive: bool = True,
    max_results: int = 500,
) -> dict[str, Any]:
    """List assets via IAssetRegistry — v9.1.0 generic discovery.

    Use this when you don't know the exact path of a needed asset. Returns up
    to ``max_results`` entries from the asset registry, optionally filtered by
    folder and/or class.

    Args:
        folder: ``/Game``-rooted folder to scan (default ``/Game``).
        asset_class: Class to filter by. Either bare name (``"StaticMesh"``,
            ``"Skeleton"``, ``"Material"``) or fully-qualified
            ``"/Script/Engine.StaticMesh"``. Empty = no class filter.
        recursive: Whether to descend into subfolders (default True).
        max_results: Cap on returned entries. Use 0 for no cap (slow on big projects).

    Returns:
        ``{"ok": True, "folder": ..., "asset_class": ..., "recursive": bool,
            "count": N, "assets": [{"name", "path", "package_path", "class"}, ...]}``

    See also: ``list_skeletons``, ``list_meshes``, ``list_blueprints``,
    ``list_classes`` for class-specific convenience wrappers.
    """
    return _send_command({
        "command": "list_assets",
        "folder": folder,
        "asset_class": asset_class,
        "recursive": recursive,
        "max_results": max_results,
    })


@mcp.tool()
def list_skeletons(
    folder: str = "/Game",
    max_results: int = 100,
) -> dict[str, Any]:
    """List USkeleton assets in the project — v9.1.0.

    Useful when ``create_anim_blueprint`` needs a skeleton path but you don't
    know what's in the project.

    Returns the standard ``list_assets`` JSON shape, scoped to ``Skeleton`` class.
    """
    return _send_command({
        "command": "list_skeletons",
        "folder": folder,
        "max_results": max_results,
    })


@mcp.tool()
def list_meshes(
    folder: str = "/Game",
    max_results: int = 200,
) -> dict[str, Any]:
    """List StaticMesh + SkeletalMesh assets — v9.1.0.

    Useful for ``set_component_property`` on a StaticMeshComponent (which needs
    a mesh asset path) — you don't have to guess `/Engine/BasicShapes/Cube` etc.

    Returns ``{"ok": True, "static_count": N, "skeletal_count": M, "count": N+M,
                "assets": [...both types merged...]}``.
    Each entry's ``class`` field tells you which kind.
    """
    return _send_command({
        "command": "list_meshes",
        "folder": folder,
        "max_results": max_results,
    })


@mcp.tool()
def list_blueprints(
    folder: str = "/Game",
    max_results: int = 200,
) -> dict[str, Any]:
    """List UBlueprint assets in the project — v9.1.0.

    Quick "what BPs do I have" probe. Standard ``list_assets`` shape, scoped
    to ``Blueprint`` class.
    """
    return _send_command({
        "command": "list_blueprints",
        "folder": folder,
        "max_results": max_results,
    })


@mcp.tool()
def list_classes(
    parent_class: str = "",
    native_only: bool = False,
    name_contains: str = "",
    max_results: int = 200,
) -> dict[str, Any]:
    """List loaded UClass objects — v9.1.0 class discovery.

    Iterates the UObject hierarchy (TObjectIterator<UClass>) and filters by
    parent / native-only / name substring.

    Args:
        parent_class: Restrict to subclasses of this class. Accepts the same
            whitelist + qualified-path syntax as ``add_cast`` (Pawn, Actor,
            PlayerController, /Script/Engine.Actor, /Game/BP_X.BP_X_C, etc.).
            Empty = no parent filter (warning: returns thousands).
        native_only: True = engine/plugin C++ classes only, no Blueprint classes.
        name_contains: Optional case-insensitive substring filter on class name.
        max_results: Cap on returned entries (default 200).

    Returns:
        ``{"ok": True, "parent_class": ..., "native_only": bool,
            "name_contains": ..., "count": N,
            "classes": [{"name", "path", "native": bool, "super"}, ...]}``

    Common use cases:
        - "what subclasses of Pawn are available?" — ``list_classes(parent_class="Pawn")``
        - "what AnimInstance subclasses?" — ``list_classes(parent_class="AnimInstance")``
        - "find a class with 'Movement' in its name" — ``list_classes(name_contains="Movement")``
    """
    payload: dict[str, Any] = {
        "command": "list_classes",
        "native_only": native_only,
        "max_results": max_results,
    }
    if parent_class:
        payload["parent_class"] = parent_class
    if name_contains:
        payload["name_contains"] = name_contains
    return _send_command(payload)


@mcp.tool()
def create_anim_blueprint(
    name: str,
    skeleton: str,
    path: str = "/Game/Blueprints",
) -> dict[str, Any]:
    """Create a new Animation Blueprint asset — v9.0.0.

    Animation Blueprints drive skeletal mesh animation: state machines,
    blend spaces, sequence playback, IK, etc. This tool creates a blank
    AnimBlueprint with `UAnimInstance` as parent class and the user-provided
    skeleton as target. The asset opens in the AnimGraph editor where
    state machines + states can be added manually.

    **v9.0.0 scope is asset creation only.** Programmatic editing of the
    AnimGraph (state machines, state nodes, transitions, sequence-player pose
    setting) is planned for v9.0.x follow-ups. The created BP is fully
    functional in-editor.

    Args:
        name: Asset name (e.g. ``"ABP_Mannequin"``).
        skeleton: Path to the target ``USkeleton`` asset. Required.
            Common engine skeletons:
              ``/Engine/Mannequin/Mesh/SK_Mannequin_Skeleton``
              ``/Game/Mannequin/Mesh/UE4_Mannequin_Skeleton``
            Or any USkeleton in your project.
        path: /Game-relative folder. Defaults to ``/Game/Blueprints``.

    Returns:
        ``{"ok": True, "blueprint_path": "/Game/...", "skeleton": "...",
            "parent_class": "AnimInstance", "saved": True}``

    Common errors:
        skeleton_not_found     — path doesn't resolve to a USkeleton
        asset_exists           — name already taken at that path
        creation_failed        — IAssetTools::CreateAsset returned null
        wrong_asset_type       — sanity check; would mean factory misconfigured
    """
    if not name or not skeleton:
        return {"ok": False, "error": "missing_argument"}
    return _send_command({
        "command": "create_anim_blueprint",
        "name": name,
        "skeleton": skeleton,
        "path": path,
    })


# ---------------------------------------------------------------------------
# v9.2.0 — AnimGraph state-machine tools
# ---------------------------------------------------------------------------
# Four tools that take ``create_anim_blueprint`` (v9.0.0) from an empty asset
# to a fully-wired skeletal animation FSM:
#
#   1. ``add_anim_state_machine`` — spawn a state-machine node in the main
#      AnimGraph. UE auto-creates the interior ``EditorStateMachineGraph``.
#   2. ``add_anim_state`` — spawn a state inside a state machine. UE
#      auto-creates the interior ``BoundGraph`` (a mini AnimGraph for the
#      state's pose).
#   3. ``add_anim_transition`` — wire one state to another via a
#      ``UAnimStateTransitionNode``. UE provides ``CreateConnections(From,To)``
#      as the canonical API.
#   4. ``set_anim_state_pose`` — populate a state's interior pose by binding
#      it to a ``UAnimSequence``. Validates skeleton compatibility.
#
# Naming: state machines + states are addressed by user-given names (stored
# as ``NodeComment``), consistent with the AnchorName convention used since v0.


@mcp.tool()
def add_anim_state_machine(
    blueprint: str,
    name: str,
    pos_x: int = 0,
    pos_y: int = 0,
) -> dict[str, Any]:
    """Add a state-machine node to an AnimBlueprint's AnimGraph — v9.2.0.

    Spawns a ``UAnimGraphNode_StateMachine`` in the AnimBlueprint's main
    AnimGraph. UE's ``PostPlacedNewNode`` automatically creates the interior
    ``EditorStateMachineGraph`` where states + transitions live. The name
    is stored as ``NodeComment`` so subsequent calls (``add_anim_state``,
    ``add_anim_transition``, ``set_anim_state_pose``) can address it.

    Args:
        blueprint: Path to the AnimBlueprint (e.g. ``"/Game/Blueprints/ABP_X"``).
        name: User-chosen state-machine name. Must be unique within the
            AnimGraph. Used as anchor for follow-up calls.
        pos_x: Node X position in the graph. Default 0.
        pos_y: Node Y position in the graph. Default 0.

    Returns:
        ``{"ok": True, "state_machine": "...", "interior_graph": "...",
            "node_guid": "...", "saved": True}``

    Common errors:
        anim_blueprint_not_found — blueprint path doesn't resolve to a UAnimBlueprint
        no_anim_graph            — AnimBP has no FunctionGraph named "AnimGraph"
        state_machine_exists     — name already used
    """
    if not blueprint or not name:
        return {"ok": False, "error": "missing_argument"}
    return _send_command({
        "command": "add_anim_state_machine",
        "blueprint": blueprint,
        "name": name,
        "pos_x": pos_x,
        "pos_y": pos_y,
    })


@mcp.tool()
def add_anim_state(
    blueprint: str,
    state_machine: str,
    name: str,
    pos_x: int = 0,
    pos_y: int = 0,
) -> dict[str, Any]:
    """Add a state to a state-machine's interior graph — v9.2.0.

    Spawns a ``UAnimStateNode`` inside the named state machine's
    ``EditorStateMachineGraph``. UE's ``PostPlacedNewNode`` automatically
    creates the state's interior ``BoundGraph`` (a mini AnimGraph for the
    pose). Wire a sequence into it via ``set_anim_state_pose``.

    Args:
        blueprint: Path to the AnimBlueprint.
        state_machine: Name (anchor) of the parent state machine, as set by
            ``add_anim_state_machine``.
        name: User-chosen state name. Must be unique within the state machine.
        pos_x: Node X position. Default 0.
        pos_y: Node Y position. Default 0.

    Returns:
        ``{"ok": True, "state": "...", "state_machine": "...",
            "bound_graph": "...", "node_guid": "...", "saved": True}``

    Common errors:
        anim_blueprint_not_found
        no_anim_graph
        state_machine_not_found  — no state machine with that NodeComment
        no_state_machine_graph   — state machine exists but has no interior graph (unusual)
        state_exists             — name already used in this state machine
    """
    if not blueprint or not state_machine or not name:
        return {"ok": False, "error": "missing_argument"}
    return _send_command({
        "command": "add_anim_state",
        "blueprint": blueprint,
        "state_machine": state_machine,
        "name": name,
        "pos_x": pos_x,
        "pos_y": pos_y,
    })


@mcp.tool()
def add_anim_transition(
    blueprint: str,
    state_machine: str,
    from_state: str,
    to_state: str,
) -> dict[str, Any]:
    """Wire a transition between two states — v9.2.0.

    Spawns a ``UAnimStateTransitionNode`` and calls the canonical
    ``CreateConnections(From, To)`` API to link two ``UAnimStateNode``s.
    The transition rule itself (a bool expression) lives in the transition's
    auto-created ``BoundGraph`` — populate it manually in-editor for now,
    or accept the default (always-fire) rule.

    Args:
        blueprint: Path to the AnimBlueprint.
        state_machine: Name (anchor) of the parent state machine.
        from_state: Source state name (anchor).
        to_state: Destination state name (anchor).

    Returns:
        ``{"ok": True, "from_state": "...", "to_state": "...",
            "state_machine": "...", "node_guid": "...", "saved": True}``

    Common errors:
        anim_blueprint_not_found
        state_machine_not_found
        from_state_not_found
        to_state_not_found
    """
    if not blueprint or not state_machine or not from_state or not to_state:
        return {"ok": False, "error": "missing_argument"}
    return _send_command({
        "command": "add_anim_transition",
        "blueprint": blueprint,
        "state_machine": state_machine,
        "from_state": from_state,
        "to_state": to_state,
    })


@mcp.tool()
def set_anim_state_pose(
    blueprint: str,
    state_machine: str,
    state: str,
    sequence: str,
) -> dict[str, Any]:
    """Bind a state's pose to an animation sequence — v9.2.0.

    Loads the ``UAnimSequence`` at ``sequence``, validates its skeleton
    matches the AnimBlueprint's ``TargetSkeleton``, then finds or creates a
    ``UAnimGraphNode_SequencePlayer`` in the state's interior ``BoundGraph``
    and wires its output pose pin to the state's pose-sink pin (via
    ``GetPoseSinkPinInsideState``).

    Args:
        blueprint: Path to the AnimBlueprint.
        state_machine: Name (anchor) of the parent state machine.
        state: Name (anchor) of the state to populate.
        sequence: Path to the ``UAnimSequence`` asset.

    Returns:
        ``{"ok": True, "state": "...", "state_machine": "...",
            "sequence": "...", "wired": True, "saved": True}``

    Common errors:
        anim_blueprint_not_found
        state_machine_not_found
        state_not_found
        no_bound_graph        — state exists but has no interior BoundGraph (unusual)
        sequence_not_found    — path doesn't resolve to a UAnimSequence
        skeleton_mismatch     — sequence and AnimBP have different skeletons
    """
    if not blueprint or not state_machine or not state or not sequence:
        return {"ok": False, "error": "missing_argument"}
    return _send_command({
        "command": "set_anim_state_pose",
        "blueprint": blueprint,
        "state_machine": state_machine,
        "state": state,
        "sequence": sequence,
    })


# ---------------------------------------------------------------------------
# v9.3.0 — Niagara door-opener
# ---------------------------------------------------------------------------
# Opens the Niagara VFX surface. v9.3.0 scope is asset creation only —
# emitter authoring, module parameters, etc. are planned follow-ups.


@mcp.tool()
def create_niagara_system(
    name: str,
    path: str = "/Game/VFX",
) -> dict[str, Any]:
    """Create a blank Niagara System asset — v9.3.0.

    Niagara is UE's modern VFX system. ``UNiagaraSystem`` is the top-level
    asset that hosts one or more emitters. This tool creates an empty
    system via ``UNiagaraSystemFactoryNew`` (with no source/template),
    which runs the factory's default ``InitializeSystem`` path: sets up
    SystemSpawnScript/SystemUpdateScript and the default effect type.

    The system opens in the Niagara editor where emitters and modules can
    be added manually. Programmatic emitter authoring is a planned
    v9.3.x follow-up.

    Args:
        name: Asset name (e.g. ``"NS_Sparkles"``).
        path: /Game-relative folder. Defaults to ``/Game/VFX``.

    Returns:
        ``{"ok": True, "system_path": "/Game/VFX/NS_Sparkles", "saved": True}``

    Common errors:
        asset_exists      — name already taken at that path
        creation_failed   — IAssetTools::CreateAsset returned null
        wrong_asset_type  — sanity check (factory misconfigured)
    """
    if not name:
        return {"ok": False, "error": "missing_argument"}
    return _send_command({
        "command": "create_niagara_system",
        "name": name,
        "path": path,
    })


# ---------------------------------------------------------------------------
# v9.4.0 — UMG door-opener + save_all
# ---------------------------------------------------------------------------


@mcp.tool()
def create_widget_blueprint(
    name: str,
    parent_class: str = "",
    path: str = "/Game/UI",
) -> dict[str, Any]:
    """Create a blank Widget Blueprint (UMG) asset — v9.4.0.

    UMG is UE's editor surface for UI. ``UWidgetBlueprint`` is the
    Blueprint-graph + designer-canvas asset; it inherits from
    ``UUserWidget`` by default. v9.4.0 ships only asset creation; the
    widget tree (Canvas / Button / Text / etc.) must be authored
    manually for now. Programmatic widget composition is a planned
    follow-up.

    Args:
        name: Asset name (e.g. ``"WBP_Menu"``).
        parent_class: Optional path to a ``UUserWidget`` subclass to use
            as the parent. Default is ``UUserWidget`` itself. Use a
            Blueprint Generated Class path like
            ``"/Game/UI/WBP_MenuBase_C"`` to extend an existing widget.
        path: /Game-relative folder. Defaults to ``/Game/UI``.

    Returns:
        ``{"ok": True, "widget_path": "/Game/UI/WBP_Menu",
            "parent_class": "/Script/UMG.UserWidget", "saved": True}``

    Common errors:
        asset_exists          — name already taken
        invalid_parent_class  — parent isn't a UUserWidget subclass
        creation_failed       — IAssetTools::CreateAsset returned null
        wrong_asset_type      — sanity check (factory misconfigured)
    """
    if not name:
        return {"ok": False, "error": "missing_argument"}
    payload: dict[str, Any] = {
        "command": "create_widget_blueprint",
        "name": name,
        "path": path,
    }
    if parent_class:
        payload["parent_class"] = parent_class
    return _send_command(payload)


# ---------------------------------------------------------------------------
# v9.15.0 — Material subsystem (closes 2026-05-23 feature request #1/#2)
# ---------------------------------------------------------------------------
# Five tools that open the Material editing surface end-to-end.
#
# Anchoring: each expression's ``Desc`` UPROPERTY is the user-given label —
# the same pattern as ``NodeComment`` on K2 nodes.
#
# Pin reference format mirrors v0's "<anchor>.<pin>" but for materials:
#   "myExpr"      — first output of the expression named "myExpr"
#   "myExpr.0"    — output index 0 (same as above)
#   "myExpr.A"    — for to_pin: the input named "A" on the expression
#
# Material outputs are addressed by name (BaseColor / EmissiveColor /
# Metallic / Roughness / Normal / Opacity / WorldPositionOffset / etc.).
#
# **Batch flow**: ``add_material_expression`` / ``set_material_expression_property``
# / ``connect_material_pins`` / ``connect_material_output`` only mark the
# material package dirty — they do NOT call ``PostEditChange`` or save. That
# avoids per-op shader recompile (which can easily exceed the 12s TCP timeout).
# Call ``save_all()`` after your batch is complete to actually persist. UE
# recompiles the shader when the material is next loaded or opened.


@mcp.tool()
def create_material(
    name: str,
    path: str = "/Game/Materials",
) -> dict[str, Any]:
    """Create a new blank Material asset — v9.15.0.

    Creates via ``UMaterialFactoryNew`` → ``UMaterial``. Domain defaults
    to Surface, shading model DefaultLit (UE factory defaults).

    Args:
        name: Asset name (e.g. ``"M_HeightColor"``).
        path: /Game-relative folder. Default ``/Game/Materials``.

    Returns:
        ``{"ok": True, "material_path": "/Game/...", "saved": True}``

    Common errors:
        asset_exists, creation_failed, wrong_asset_type
    """
    if not name:
        return {"ok": False, "error": "missing_argument"}
    return _send_command({"command": "create_material", "name": name, "path": path})


@mcp.tool()
def add_material_expression(
    material: str,
    expression_type: str,
    anchor_name: str,
    position_x: int = 0,
    position_y: int = 0,
) -> dict[str, Any]:
    """Add a UMaterialExpression node to a material's graph — v9.15.0.

    Args:
        material: Material asset path.
        expression_type: Short name or aliases. Common ones:
            - ``"Constant"`` (1 scalar), ``"Constant3Vector"`` /
              ``"Vec3"`` (RGB), ``"Constant4Vector"`` / ``"Vec4"`` (RGBA)
            - ``"Add"`` / ``"Subtract"`` / ``"Multiply"`` / ``"Divide"``
            - ``"Lerp"`` (alias of ``"LinearInterpolate"``)
            - ``"Saturate"``, ``"Power"``, ``"Sine"``, ``"Cosine"``,
              ``"Abs"``, ``"Frac"``, ``"Floor"``, ``"Ceil"``
            - ``"WorldPosition"`` / ``"WorldPos"`` (no inputs)
            - ``"ComponentMask"`` / ``"Mask"`` (channel picker)
            - ``"ScalarParameter"`` / ``"ScalarParam"``,
              ``"VectorParameter"`` / ``"VectorParam"``
            - ``"TextureSample"``, ``"TextureSampleParameter2D"``
            - Or full path ``"/Script/Engine.MaterialExpressionFoo"``.
        anchor_name: User-given label (becomes ``Desc`` on the expression).
            Must be unique within the material.
        position_x, position_y: Material-editor canvas position.

    Returns:
        ``{"ok": True, "anchor_name": ..., "expression_class": "...",
            "node_guid": "...", "saved": True}``

    Common errors:
        material_not_found, unknown_expression_type, anchor_name_exists
    """
    if not material or not expression_type or not anchor_name:
        return {"ok": False, "error": "missing_argument"}
    return _send_command({
        "command": "add_material_expression",
        "material": material,
        "expression_type": expression_type,
        "anchor_name": anchor_name,
        "position_x": position_x,
        "position_y": position_y,
    })


@mcp.tool()
def set_material_expression_property(
    material: str,
    anchor_name: str,
    property: str,
    value: str = "",
) -> dict[str, Any]:
    """Set a UPROPERTY on a material expression — v9.15.0.

    Reflection-based — works on any UPROPERTY of any UMaterialExpression
    subclass. Useful for:

      - ``Constant.R = 0.5`` (single scalar)
      - ``Constant3Vector.Constant = "(R=1,G=0.5,B=0.2)"`` (FLinearColor)
      - ``ComponentMask.R = "true"``, ``ComponentMask.G = "false"``
        (which channels to extract)
      - ``ScalarParameter.ParameterName = "Tint"``
      - ``ScalarParameter.DefaultValue = "1.0"``
      - ``VectorParameter.DefaultValue = "(R=1,G=0,B=0,A=1)"``

    Args:
        material: Material asset path.
        anchor_name: Expression's anchor (the ``Desc`` you gave to
            ``add_material_expression``).
        property: Property name or dot-notation path
            (e.g. ``"Constant"`` for Constant3Vector, ``"R"`` for ComponentMask).
        value: New value as a string. UE's standard import-text formats
            apply: ``"true"``/``"false"`` for bools, numbers for scalars,
            ``"(R=...,G=...,B=...)"`` for FLinearColor.

    Returns:
        ``{"ok": True, "anchor_name": ..., "property": ...,
            "resolved_value": ..., "saved": True}``

    Common errors:
        material_not_found, expression_not_found, property_not_found, set_failed
    """
    if not material or not anchor_name or not property:
        return {"ok": False, "error": "missing_argument"}
    return _send_command({
        "command": "set_material_expression_property",
        "material": material,
        "anchor_name": anchor_name,
        "property": property,
        "value": value,
    })


@mcp.tool()
def connect_material_pins(
    material: str,
    from_pin: str,
    to_pin: str,
) -> dict[str, Any]:
    """Wire two material expressions — v9.15.0.

    Args:
        material: Material asset path.
        from_pin: Source. ``"<anchor>"`` (default output 0) or
            ``"<anchor>.<index>"`` (e.g. ``"mask.0"``).
        to_pin: Destination. **Must** include the input name —
            ``"<anchor>.<InputName>"`` (e.g. ``"lerp.A"``, ``"add.B"``,
            ``"mask.Input"``, ``"saturate.Input"``). The input name is
            the UPROPERTY name on the expression class (look at the UE
            source or material editor pin label).

    Returns:
        ``{"ok": True, "from": ..., "to": ..., "output_index": N,
            "saved": True}``

    Common errors:
        material_not_found, from_expression_not_found,
        to_expression_not_found, input_not_found, missing_to_input_name
    """
    if not material or not from_pin or not to_pin:
        return {"ok": False, "error": "missing_argument"}
    return _send_command({
        "command": "connect_material_pins",
        "material": material,
        "from_pin": from_pin,
        "to_pin": to_pin,
    })


@mcp.tool()
def connect_material_output(
    material: str,
    from_pin: str,
    output: str,
) -> dict[str, Any]:
    """Wire an expression into one of the material's outputs — v9.15.0.

    The 'last step' that makes a material graph actually shade.

    Args:
        material: Material asset path.
        from_pin: Source. ``"<anchor>"`` or ``"<anchor>.<index>"``.
        output: One of the material output names (UPROPERTY on
            UMaterialEditorOnlyData). Common picks:
              ``"BaseColor"``, ``"Metallic"``, ``"Specular"``,
              ``"Roughness"``, ``"Normal"``, ``"Tangent"``,
              ``"EmissiveColor"``, ``"Opacity"``, ``"OpacityMask"``,
              ``"WorldPositionOffset"``, ``"Refraction"``,
              ``"AmbientOcclusion"``, ``"ClearCoat"``,
              ``"SubsurfaceColor"``.

    Returns:
        ``{"ok": True, "from": ..., "output": ..., "output_index": N,
            "saved": True}``

    Common errors:
        material_not_found, from_expression_not_found, unknown_output,
        not_a_material_input
    """
    if not material or not from_pin or not output:
        return {"ok": False, "error": "missing_argument"}
    return _send_command({
        "command": "connect_material_output",
        "material": material,
        "from_pin": from_pin,
        "output": output,
    })


@mcp.tool()
def compile_material(material: str) -> dict[str, Any]:
    """Apply + recompile a material's shader — v9.16.0.

    The "Apply" button in the material editor. Closes rev9 ISSUE-1 —
    v9.15.0's batch material ops (`add_material_expression` /
    `connect_material_pins` / etc.) only mark the package dirty; they
    do NOT call `PostEditChange` because per-op shader recompile was
    hitting the 12s TCP timeout.

    This tool does the recompile explicitly with a 75s timeout. Call
    it after your batch material editing is complete to make the
    material actually render correctly.

    Internally calls ``Mat->PreEditChange(nullptr)`` →
    ``PostEditChange()`` → ``ForceRecompileForRendering()`` →
    ``SaveAsset``.

    Args:
        material: Material asset path.

    Returns:
        ``{"ok": True, "material_path": ..., "saved": True, "recompiled": True}``

    Common errors:
        material_not_found, game_thread_timeout (shader compile > 60s server-side)
    """
    if not material:
        return {"ok": False, "error": "missing_argument"}
    # Custom 75s Python timeout — compile_material's server-side budget is 60s,
    # plus margin for TCP round-trip.
    return _send_command({"command": "compile_material", "material": material}, timeout_sec=75.0)


@mcp.tool()
def set_material_property(
    material: str,
    property: str,
    value: str = "",
) -> dict[str, Any]:
    """Set a material-level UPROPERTY — v9.16.0.

    For material-level flags / settings (NOT inside the graph — those
    go through ``set_material_expression_property``). The classic ISM
    gotcha: ``bUsedWithInstancedStaticMeshes`` must be ``true`` for an
    ISM to render with the material correctly.

    Args:
        material: Material asset path.
        property: Property name or dot-path. Common picks:
            - ``"bUsedWithInstancedStaticMeshes"`` (bool) — required for ISM
            - ``"bUsedWithStaticLighting"``
            - ``"bUsedWithSkeletalMesh"``
            - ``"TwoSided"`` — two-sided rendering (UE 5.4 dropped the ``b`` prefix)
            - ``"BlendMode"`` — ``"BLEND_Translucent"`` / ``"BLEND_Masked"`` / etc.
            - ``"ShadingModel"`` — ``"MSM_Unlit"`` / ``"MSM_DefaultLit"`` / etc.
            - ``"MaterialDomain"`` — ``"MD_Surface"`` / ``"MD_DeferredDecal"`` / etc.
            - ``"OpacityMaskClipValue"`` — float for masked materials
            - ``"DitheredLODTransition"``
        value: New value. ``"true"``/``"false"`` for bools, ``"BLEND_X"`` /
            ``"MSM_X"`` / ``"MD_X"`` enum literals for enum properties.

    Returns:
        ``{"ok": True, "material_path": ..., "property": ...,
            "resolved_value": ..., "saved": False}``
        ``saved=false`` — call ``compile_material`` + ``save_all`` to persist.

    Common errors:
        material_not_found, property_not_found, set_failed
    """
    if not material or not property:
        return {"ok": False, "error": "missing_argument"}
    return _send_command({
        "command": "set_material_property",
        "material": material,
        "property": property,
        "value": value,
    })


@mcp.tool()
def delete_material_expression(
    material: str,
    anchor_name: str,
) -> dict[str, Any]:
    """Remove a material expression — v9.16.0.

    Also cleans up dangling references — walks all other expressions
    and material outputs, clears any FExpressionInput that pointed to
    the removed expression. So you don't end up with broken wires.

    Closes rev9 ISSUE-3 partial 1/2: material tools were "additive
    only" before this.

    Args:
        material: Material asset path.
        anchor_name: The expression's anchor (its ``Desc``).

    Returns:
        ``{"ok": True, "anchor_name": ..., "saved": False}``

    Common errors:
        material_not_found, expression_not_found
    """
    if not material or not anchor_name:
        return {"ok": False, "error": "missing_argument"}
    return _send_command({
        "command": "delete_material_expression",
        "material": material,
        "anchor_name": anchor_name,
    })


@mcp.tool()
def disconnect_material_pins(
    material: str,
    to_pin: str,
) -> dict[str, Any]:
    """Break a single material connection — v9.16.0.

    Two forms supported (closes rev9 ISSUE-3 partial 2/2):

    - Expression input: ``to_pin="lerp.A"`` — clears Lerp's A input.
    - Material output: ``to_pin="output:BaseColor"`` — clears the
      material's BaseColor output (or EmissiveColor, Normal, etc.).

    The ``output:`` prefix disambiguates from a regular anchor named
    "output".

    Args:
        material: Material asset path.
        to_pin: ``"<anchor>.<InputName>"`` or ``"output:<OutputName>"``.

    Returns:
        ``{"ok": True, "to": ..., "saved": False}``

    Common errors:
        material_not_found, to_expression_not_found,
        input_not_found, unknown_output, missing_to_input_name
    """
    if not material or not to_pin:
        return {"ok": False, "error": "missing_argument"}
    return _send_command({
        "command": "disconnect_material_pins",
        "material": material,
        "to_pin": to_pin,
    })


@mcp.tool()
def save_all() -> dict[str, Any]:
    """Silently save every dirty package — v9.4.0.

    Mirrors UE's File → Save All menu item but with no prompts. Use
    this before any UE editor kill or restart to prevent the "Save
    changes?" dialog on next launch. Safe to call at any time — if
    nothing is dirty, it returns ``saved=true`` with
    ``packages_needed_saving=false``.

    Returns:
        ``{"ok": True, "saved": True, "packages_needed_saving": True}``
    """
    return _send_command({"command": "save_all"})


@mcp.tool()
def shutdown_editor() -> dict[str, Any]:
    """Request a clean editor exit — v9.6.0.

    Works in BOTH headless (BlueprintMCPRun commandlet) and GUI modes:
      - Headless: flips the commandlet's exit flag → process returns
        from Main() with exit code 0 on the next tick (~250ms).
      - GUI: schedules FPlatformMisc::RequestExit(false), the same
        exit path used by File → Exit. Dirty packages will prompt
        unless ``save_all()`` ran first.

    Returns immediately — does NOT wait for the editor to actually
    finish exiting. Poll TCP port 55558 (e.g. ``nc -z``) to confirm
    shutdown completed.

    Pair with ``save_all()`` for a clean shutdown sequence:

        server.save_all()
        server.shutdown_editor()
        # poll: until ! nc -z 127.0.0.1 55558 ...

    Returns:
        ``{"ok": True, "requested": True}``
    """
    return _send_command({"command": "shutdown_editor"})


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
        category: If non-empty, return only lines whose **extracted category token**
            (the contents of the first ``[...]`` in the line) contains this string,
            case-insensitive. So ``"BlueprintMCP"`` matches ``[LogBlueprintMCP_TCP]``,
            ``"PlayLevel"`` matches ``[LogPlayLevel]``, etc. v8.0.3 fix — previously
            this was effectively a prefix match.
            Useful category names: ``BlueprintMCP_TCP`` (MCP commands), ``BlueprintUserMessages``
            (PrintString), ``PlayLevel`` (PIE start/stop), ``BlueprintCompile`` (compile errors).
        verbosity: If non-empty, return only lines whose **extracted verbosity token**
            (second ``[...]``) contains this string, case-insensitive. E.g. ``"Warning"``,
            ``"Error"``, ``"Log"``.
        contains: If non-empty, only return lines containing this substring (anywhere
            in the line, case-insensitive).

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
def pie_press_key(
    key: str,
    player_index: int = 0,
    duration_sec: float = 0.0,
) -> dict[str, Any]:
    """Simulate a key press on the PIE PlayerController — v8.3 + v9.9.0 hold.

    Routes through ``APlayerController::InputKey(FInputKeyParams)`` so it works
    for both legacy input and Enhanced Input (whichever is bound).

    Args:
        key: Key name (`"Space"`, `"P"`, `"LeftMouseButton"`, `"F1"`, etc.).
            Aliases applied via the same ``ResolveFKeyWithAliases`` helper
            that ``add_input_key`` uses, so ``"Space"`` → ``"SpaceBar"`` etc.
        player_index: Which local player to target (default 0 — single-player).
        duration_sec: **v9.9.0** — if > 0, press now and schedule release
            after this many seconds (via FTSTicker, non-blocking). Returns
            immediately with ``held=true``. Default 0 = press+release
            immediately (original v8.3 behavior).

    Returns:
        ``{"ok": True, "key": "<canonical key>", "player_index": N,
            "held": bool, "duration_sec": float}``
        Errors: ``pie_not_running``, ``no_player_controller``, ``invalid_key``.

    Note: PIE must already be running (``start_pie`` + wait for the tick).
    For continuous movement (hold WASD to walk), use ``pie_move_player``
    instead — uses native AddMovementInput which is more reliable than
    raw key holds for character-controller pawns.
    """
    if not key:
        return {"ok": False, "error": "missing_argument"}
    payload: dict[str, Any] = {
        "command": "pie_press_key",
        "key": key,
        "player_index": player_index,
    }
    if duration_sec > 0:
        payload["duration_sec"] = duration_sec
    return _send_command(payload)


# ---------------------------------------------------------------------------
# v9.9.0 — Player movement in PIE
# ---------------------------------------------------------------------------
# Closes feature-request gap #7 ("can't drive character into trigger box").
# pie_press_key with WASD doesn't reliably trigger movement on character
# pawns — they expect axis input via AddMovementInput. These tools target
# the pawn directly.


@mcp.tool()
def pie_set_player_location(
    location: list[float],
    player_index: int = 0,
    snap_to_ground: bool = False,
    trace_up_height: float = 200.0,
    trace_down_dist: float = 10000.0,
) -> dict[str, Any]:
    """Teleport the PIE pawn to a world-space location — v9.9.0 + v9.12.0 snap.

    Calls ``APlayerController::GetPawn()->SetActorLocation(loc,
    bSweep=false, ..., ETeleportType::TeleportPhysics)``. Useful to
    drop the player at a specific test position before driving into a
    trigger volume, or to reset a stuck test.

    **v9.12.0 snap-to-ground**: with ``snap_to_ground=True`` the
    location's Z becomes a STARTING POINT — the server does a downward
    line trace from ``(X, Y, Z + trace_up_height)`` to
    ``(X, Y, Z - trace_down_dist)`` and lands the pawn at
    ``ground_z + capsule_half_height``. No more guessing Z. Closes
    rev6 ISSUE-3.

    Args:
        location: ``[X, Y, Z]`` world coordinates. With
            ``snap_to_ground=True`` only X/Y matter — Z is the trace
            anchor, the final Z is computed from the ground hit.
        player_index: Which local player (default 0).
        snap_to_ground: **v9.12.0** — line-trace down for ground +
            offset by capsule half-height. Default False
            (backwards-compatible).
        trace_up_height: How far above the supplied Z to START the
            trace (in case Z is inside a wall). Default 200.
        trace_down_dist: How far below to trace. Default 10000.

    Returns:
        ``{"ok": True, "player_index": N, "requested": [X,Y,Z],
            "actual": [X,Y,Z], "moved": True,
            "snapped_to_ground": bool, "ground_z": Z,
            "capsule_half_height": H, "ground_hit": "<actor name>"}``
        ``snapped_to_ground=false`` if no ground was found in the trace
        range — pawn is placed at the requested Z instead.

    Common errors:
        pie_not_running, no_player_controller, no_pawn
    """
    if not location or len(location) < 3:
        return {"ok": False, "error": "missing_argument"}
    payload: dict[str, Any] = {
        "command": "pie_set_player_location",
        "location": list(location)[:3],
        "player_index": player_index,
    }
    if snap_to_ground:
        payload["snap_to_ground"] = True
        payload["trace_up_height"] = trace_up_height
        payload["trace_down_dist"] = trace_down_dist
    return _send_command(payload)


@mcp.tool()
def get_player_capsule(player_index: int = 0) -> dict[str, Any]:
    """Read the PIE player's collision capsule dimensions — v9.12.0.

    Closes rev6 ISSUE-1 — the LLM is no longer blind to player size
    when programmatically laying out corridors / doors / cover.

    The character only exists in the PIE world (a Character pawn at
    PlayerStart, spawned by ``start_pie``), NOT in the editor world.
    So ``start_pie`` must already be running.

    For ``ACharacter``-derived pawns (the FP/TP template defaults),
    reads ``UCapsuleComponent::GetScaledCapsuleRadius()`` and
    ``GetScaledCapsuleHalfHeight()`` directly. For non-Character
    pawns, falls back to ``GetSimpleCollisionCylinder`` (approximate
    cylinder).

    Args:
        player_index: Which local player (default 0).

    Returns:
        ``{"ok": True, "player_index": N,
            "pawn_name": "...", "pawn_class": "...",
            "is_character": bool, "has_capsule": bool,
            "radius": R, "half_height": H,
            "diameter": 2R, "full_height": 2H,
            "location": [X,Y,Z], "rotation": [P,Y,R]}``

        Use ``diameter`` for "how wide a corridor needs to be" math
        (plus a margin), ``full_height`` for "how tall a doorway."

    Common errors:
        pie_not_running, no_player_controller, no_pawn
    """
    return _send_command({
        "command": "get_player_capsule",
        "player_index": player_index,
    })


@mcp.tool()
def pie_move_player(
    direction: list[float],
    duration_sec: float = 1.0,
    scale: float = 1.0,
    player_index: int = 0,
    face_movement: bool = False,
) -> dict[str, Any]:
    """Simulate continuous movement input on the PIE pawn — v9.9.0 + v9.10.0 face.

    Equivalent to "holding WASD" for ``duration_sec``. Each game-thread
    tick the pawn receives ``AddMovementInput(direction.Normal, scale)``.
    Uses FTSTicker so it doesn't block — returns immediately with
    ``queued=true``. The user is expected to sleep ``duration_sec`` (or
    longer) before checking PIE state.

    This is the right tool for "walk into the trigger box" tests on
    character pawns. ``pie_press_key("W", duration_sec=N)`` works only
    if the pawn has a Pressed-binding on W; character-class pawns
    typically use axis bindings, which only respond to
    ``AddMovementInput``.

    Args:
        direction: World-space direction vector ``[X, Y, Z]``.
            Forward = ``[1, 0, 0]``, right = ``[0, 1, 0]``, up = ``[0, 0, 1]``.
            Normalized server-side; magnitude is ignored.
        duration_sec: How long to keep applying the input (default 1.0).
        scale: Input scalar (default 1.0). Use < 1.0 for slower motion.
        player_index: Which local player (default 0).
        face_movement: **v9.10.0** — if True, set the controller's yaw to
            face ``direction`` BEFORE starting movement. Fixes the
            first-person "strafe sideways into the portal" weirdness:
            character actually turns to face where they're walking.
            Pitch and Roll always 0 (don't tilt the camera).

    Returns:
        ``{"ok": True, "player_index": N, "direction": [normalized X,Y,Z],
            "duration_sec": N, "scale": N, "faced_movement": bool,
            "applied_yaw": deg, "queued": True}``

    Common errors:
        pie_not_running, no_player_controller, no_pawn,
        zero_direction, invalid_duration
    """
    if not direction or len(direction) < 3:
        return {"ok": False, "error": "missing_argument"}
    payload: dict[str, Any] = {
        "command": "pie_move_player",
        "direction": list(direction)[:3],
        "duration_sec": duration_sec,
        "scale": scale,
        "player_index": player_index,
    }
    if face_movement:
        payload["face_movement"] = True
    return _send_command(payload)


@mcp.tool()
def pie_set_player_rotation(
    rotation: list[float],
    player_index: int = 0,
) -> dict[str, Any]:
    """Set the PIE player's view rotation — v9.10.0.

    Writes to ``APlayerController::SetControlRotation`` — the
    source-of-truth for first-person camera direction (mouse-look writes
    here). On Character pawns with ``bUseControllerRotationYaw=true``
    (the default for both First-Person and Third-Person templates),
    the pawn mesh follows yaw on the next tick.

    Pair with ``pie_set_player_location`` to position + orient the player
    in two calls, or use ``pie_move_player(..., face_movement=True)`` to
    handle "turn-then-walk" in one call.

    Args:
        rotation: ``[Pitch, Yaw, Roll]`` in degrees.
            - Pitch: look up (positive) / down (negative). FPS templates
              typically clamp to ±89°.
            - Yaw: rotate around vertical axis. 0 = +X, 90 = +Y, etc.
            - Roll: tilt camera (usually 0).
        player_index: Which local player (default 0).

    Returns:
        ``{"ok": True, "player_index": N, "requested": [P,Y,R],
            "applied": [P,Y,R]}``
        ``applied`` may differ from ``requested`` if the controller's
        Pitch/Yaw clamps engage (e.g. FPS Pitch limits).

    Common errors:
        pie_not_running, no_player_controller
    """
    if not rotation or len(rotation) < 3:
        return {"ok": False, "error": "missing_argument"}
    return _send_command({
        "command": "pie_set_player_rotation",
        "rotation": list(rotation)[:3],
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
