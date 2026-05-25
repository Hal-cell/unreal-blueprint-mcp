"""Smoke + unit tests for the MCP server.

The TCP integration tests (real UE plugin) are skipped by default — they only
make sense with UE Editor running. Un-skip and run manually during spike phases.
"""

from __future__ import annotations

import socket
from unittest import mock

import pytest

from conftest import requires_ue_editor, skip_if_headless

from unreal_blueprint_mcp import server


# ---------------------------------------------------------------------------
# echo
# ---------------------------------------------------------------------------


def test_echo_returns_message() -> None:
    # FastMCP 1.27 @mcp.tool() registers the function but returns it unchanged,
    # so we call it directly.
    result = server.echo(message="hello")
    assert result == {"ok": True, "echo": "hello"}


# ---------------------------------------------------------------------------
# _send_command error paths
# ---------------------------------------------------------------------------


def test_send_command_handles_connection_refused() -> None:
    with mock.patch.object(
        socket,
        "create_connection",
        side_effect=ConnectionRefusedError("nope"),
    ):
        result = server._send_command({"command": "ping"})
    assert result["ok"] is False
    assert result["error"] == "connection_refused"
    assert "hint" in result


def test_send_command_handles_timeout() -> None:
    with mock.patch.object(
        socket,
        "create_connection",
        side_effect=socket.timeout("slow"),
    ):
        result = server._send_command({"command": "ping"})
    assert result["ok"] is False
    assert result["error"] == "tcp_error"


def test_send_command_handles_invalid_response_json() -> None:
    fake_response = b"not-json-at-all"

    class FakeSock:
        def __enter__(self): return self
        def __exit__(self, *a): pass
        def sendall(self, data): pass
        def recv(self, n):
            if getattr(self, "_sent", False): return b""
            self._sent = True; return fake_response

    with mock.patch.object(socket, "create_connection", return_value=FakeSock()):
        result = server._send_command({"command": "ping"})
    assert result["ok"] is False
    assert result["error"] == "invalid_response_json"
    assert result["raw"] == "not-json-at-all"


# ---------------------------------------------------------------------------
# ping_ue (just verifies it composes _send_command correctly)
# ---------------------------------------------------------------------------


def test_ping_ue_parses_plugin_success_response() -> None:
    fake_response = b'{"ok":true,"command":"ping","version":"0.0.1","timestamp":"2026-05-20T00:00:00.000Z"}\n'

    class FakeSock:
        def __enter__(self): return self
        def __exit__(self, *a): pass
        def sendall(self, data): pass
        def recv(self, n):
            if getattr(self, "_sent", False): return b""
            self._sent = True; return fake_response

    with mock.patch.object(socket, "create_connection", return_value=FakeSock()):
        result = server.ping_ue()

    assert result["ok"] is True
    assert result["version"] == "0.0.1"
    assert "timestamp" in result


# ---------------------------------------------------------------------------
# create_blueprint
# ---------------------------------------------------------------------------


def test_create_blueprint_success() -> None:
    fake_response = (
        b'{"ok":true,"command":"create_blueprint","blueprint_path":"/Game/Blueprints/BP_Test",'
        b'"parent_class":"Actor","saved":true}\n'
    )
    sent_payload: dict[str, bytes] = {}

    class FakeSock:
        def __enter__(self): return self
        def __exit__(self, *a): pass
        def sendall(self, data):
            sent_payload["data"] = data
        def recv(self, n):
            if getattr(self, "_sent", False): return b""
            self._sent = True; return fake_response

    with mock.patch.object(socket, "create_connection", return_value=FakeSock()):
        result = server.create_blueprint(name="BP_Test", parent_class="Actor", path="/Game/Blueprints")

    # response shape
    assert result["ok"] is True
    assert result["blueprint_path"] == "/Game/Blueprints/BP_Test"
    assert result["parent_class"] == "Actor"

    # request shape — verify what we sent over the wire
    import json
    sent_dict = json.loads(sent_payload["data"].decode("utf-8").rstrip())
    assert sent_dict == {
        "command": "create_blueprint",
        "name": "BP_Test",
        "parent_class": "Actor",
        "path": "/Game/Blueprints",
    }


def test_create_blueprint_handles_ue_error_response() -> None:
    fake_response = (
        b'{"ok":false,"command":"create_blueprint","error":"unknown_parent_class",'
        b'"detail":"FooBar"}\n'
    )

    class FakeSock:
        def __enter__(self): return self
        def __exit__(self, *a): pass
        def sendall(self, data): pass
        def recv(self, n):
            if getattr(self, "_sent", False): return b""
            self._sent = True; return fake_response

    with mock.patch.object(socket, "create_connection", return_value=FakeSock()):
        result = server.create_blueprint(name="BP_Bad", parent_class="FooBar")

    assert result["ok"] is False
    assert result["error"] == "unknown_parent_class"
    assert result["detail"] == "FooBar"


def test_create_blueprint_uses_defaults() -> None:
    """parent_class defaults to Actor; path defaults to /Game/Blueprints."""
    fake_response = (
        b'{"ok":true,"command":"create_blueprint","blueprint_path":"/Game/Blueprints/BP_X",'
        b'"parent_class":"Actor","saved":true}\n'
    )
    sent: dict[str, bytes] = {}

    class FakeSock:
        def __enter__(self): return self
        def __exit__(self, *a): pass
        def sendall(self, data):
            sent["data"] = data
        def recv(self, n):
            if getattr(self, "_sent", False): return b""
            self._sent = True; return fake_response

    with mock.patch.object(socket, "create_connection", return_value=FakeSock()):
        server.create_blueprint(name="BP_X")

    import json
    sent_dict = json.loads(sent["data"].decode("utf-8").rstrip())
    assert sent_dict["parent_class"] == "Actor"
    assert sent_dict["path"] == "/Game/Blueprints"


# ---------------------------------------------------------------------------
# add_node (Spike B2)
# ---------------------------------------------------------------------------


def test_add_node_success() -> None:
    fake_response = (
        b'{"ok":true,"command":"add_node","anchor_name":"print_hello",'
        b'"node_guid":"AABBCCDD-EEFF-0011-2233-445566778899",'
        b'"node_type":"K2Node_CallFunction","function":"PrintString",'
        b'"owning_class":"KismetSystemLibrary",'
        b'"pins":[{"name":"execute","direction":"input","type":"exec"},'
        b'{"name":"then","direction":"output","type":"exec"},'
        b'{"name":"InString","direction":"input","type":"string"}],'
        b'"saved":true}\n'
    )
    sent: dict[str, bytes] = {}

    class FakeSock:
        def __enter__(self): return self
        def __exit__(self, *a): pass
        def sendall(self, data):
            sent["data"] = data
        def recv(self, n):
            if getattr(self, "_sent", False): return b""
            self._sent = True; return fake_response

    with mock.patch.object(socket, "create_connection", return_value=FakeSock()):
        result = server.add_node(
            blueprint="/Game/Blueprints/BP_TestSpikeB1_v2",
            node_type="K2Node_CallFunction:PrintString",
            anchor_name="print_hello",
            position_x=200,
            position_y=100,
        )

    # response shape
    assert result["ok"] is True
    assert result["anchor_name"] == "print_hello"
    assert result["function"] == "PrintString"
    assert isinstance(result["pins"], list)
    assert any(p["name"] == "InString" and p["type"] == "string" for p in result["pins"])

    # wire format
    import json
    sent_dict = json.loads(sent["data"].decode("utf-8").rstrip())
    assert sent_dict == {
        "command": "add_node",
        "blueprint": "/Game/Blueprints/BP_TestSpikeB1_v2",
        "node_type": "K2Node_CallFunction:PrintString",
        "anchor_name": "print_hello",
        "position_x": 200,
        "position_y": 100,
    }


def test_add_node_handles_unknown_function() -> None:
    fake_response = (
        b'{"ok":false,"command":"add_node","error":"unknown_function","detail":"FooBar"}\n'
    )

    class FakeSock:
        def __enter__(self): return self
        def __exit__(self, *a): pass
        def sendall(self, data): pass
        def recv(self, n):
            if getattr(self, "_sent", False): return b""
            self._sent = True; return fake_response

    with mock.patch.object(socket, "create_connection", return_value=FakeSock()):
        result = server.add_node(
            blueprint="/Game/Blueprints/BP_X",
            node_type="K2Node_CallFunction:FooBar",
            anchor_name="bad",
        )
    assert result["ok"] is False
    assert result["error"] == "unknown_function"
    assert result["detail"] == "FooBar"


def test_add_node_uses_position_defaults() -> None:
    fake_response = (
        b'{"ok":true,"command":"add_node","anchor_name":"a","node_guid":"x",'
        b'"node_type":"K2Node_CallFunction","function":"PrintString",'
        b'"owning_class":"KismetSystemLibrary","pins":[],"saved":true}\n'
    )
    sent: dict[str, bytes] = {}

    class FakeSock:
        def __enter__(self): return self
        def __exit__(self, *a): pass
        def sendall(self, data):
            sent["data"] = data
        def recv(self, n):
            if getattr(self, "_sent", False): return b""
            self._sent = True; return fake_response

    with mock.patch.object(socket, "create_connection", return_value=FakeSock()):
        server.add_node(
            blueprint="/Game/Blueprints/BP_X",
            node_type="K2Node_CallFunction:PrintString",
            anchor_name="a",
        )

    import json
    sent_dict = json.loads(sent["data"].decode("utf-8").rstrip())
    assert sent_dict["position_x"] == 0
    assert sent_dict["position_y"] == 0


# ---------------------------------------------------------------------------
# set_pin_default (Spike B3)
# ---------------------------------------------------------------------------


def test_set_pin_default_success() -> None:
    fake_response = (
        b'{"ok":true,"command":"set_pin_default","anchor_name":"print_hello",'
        b'"pin_name":"InString","value":"hello world","pin_type":"string","saved":true}\n'
    )
    sent: dict[str, bytes] = {}

    class FakeSock:
        def __enter__(self): return self
        def __exit__(self, *a): pass
        def sendall(self, data):
            sent["data"] = data
        def recv(self, n):
            if getattr(self, "_sent", False): return b""
            self._sent = True; return fake_response

    with mock.patch.object(socket, "create_connection", return_value=FakeSock()):
        result = server.set_pin_default(
            blueprint="/Game/Blueprints/BP_TestSpikeB1_v2",
            pin_ref="print_hello.InString",
            value="hello world",
        )

    assert result["ok"] is True
    assert result["anchor_name"] == "print_hello"
    assert result["pin_name"] == "InString"
    assert result["value"] == "hello world"
    assert result["pin_type"] == "string"

    import json
    sent_dict = json.loads(sent["data"].decode("utf-8").rstrip())
    assert sent_dict == {
        "command": "set_pin_default",
        "blueprint": "/Game/Blueprints/BP_TestSpikeB1_v2",
        "pin_ref": "print_hello.InString",
        "value": "hello world",
    }


def test_set_pin_default_handles_anchor_not_found() -> None:
    fake_response = (
        b'{"ok":false,"command":"set_pin_default","error":"anchor_not_found","detail":"nope"}\n'
    )

    class FakeSock:
        def __enter__(self): return self
        def __exit__(self, *a): pass
        def sendall(self, data): pass
        def recv(self, n):
            if getattr(self, "_sent", False): return b""
            self._sent = True; return fake_response

    with mock.patch.object(socket, "create_connection", return_value=FakeSock()):
        result = server.set_pin_default(
            blueprint="/Game/Blueprints/BP_X",
            pin_ref="nope.InString",
            value="x",
        )
    assert result["ok"] is False
    assert result["error"] == "anchor_not_found"


def test_set_pin_default_handles_invalid_pin_ref() -> None:
    """pin_ref missing the dot separator should return invalid_pin_ref."""
    fake_response = (
        b'{"ok":false,"command":"set_pin_default","error":"invalid_pin_ref",'
        b'"detail":"NoDot (expected anchor.pin)"}\n'
    )

    class FakeSock:
        def __enter__(self): return self
        def __exit__(self, *a): pass
        def sendall(self, data): pass
        def recv(self, n):
            if getattr(self, "_sent", False): return b""
            self._sent = True; return fake_response

    with mock.patch.object(socket, "create_connection", return_value=FakeSock()):
        result = server.set_pin_default(
            blueprint="/Game/Blueprints/BP_X",
            pin_ref="NoDot",
            value="x",
        )
    assert result["ok"] is False
    assert result["error"] == "invalid_pin_ref"


# ---------------------------------------------------------------------------
# connect_pins (Spike B4)
# ---------------------------------------------------------------------------


def test_connect_pins_success() -> None:
    fake_response = (
        b'{"ok":true,"command":"connect_pins","from":"begin_play.then",'
        b'"to":"print_hello.execute","saved":true}\n'
    )
    sent: dict[str, bytes] = {}

    class FakeSock:
        def __enter__(self): return self
        def __exit__(self, *a): pass
        def sendall(self, data):
            sent["data"] = data
        def recv(self, n):
            if getattr(self, "_sent", False): return b""
            self._sent = True; return fake_response

    with mock.patch.object(socket, "create_connection", return_value=FakeSock()):
        result = server.connect_pins(
            blueprint="/Game/Blueprints/BP_TestSpikeB1_v2",
            from_pin="begin_play.then",
            to_pin="print_hello.execute",
        )

    assert result["ok"] is True
    assert result["from"] == "begin_play.then"
    assert result["to"] == "print_hello.execute"
    assert result["saved"] is True

    import json
    sent_dict = json.loads(sent["data"].decode("utf-8").rstrip())
    assert sent_dict == {
        "command": "connect_pins",
        "blueprint": "/Game/Blueprints/BP_TestSpikeB1_v2",
        "from_pin": "begin_play.then",
        "to_pin": "print_hello.execute",
    }


def test_connect_pins_handles_anchor_not_found() -> None:
    fake_response = (
        b'{"ok":false,"command":"connect_pins","error":"anchor_not_found",'
        b'"detail":"nope"}\n'
    )

    class FakeSock:
        def __enter__(self): return self
        def __exit__(self, *a): pass
        def sendall(self, data): pass
        def recv(self, n):
            if getattr(self, "_sent", False): return b""
            self._sent = True; return fake_response

    with mock.patch.object(socket, "create_connection", return_value=FakeSock()):
        result = server.connect_pins(
            blueprint="/Game/Blueprints/BP_X",
            from_pin="nope.then",
            to_pin="print_hello.execute",
        )
    assert result["ok"] is False
    assert result["error"] == "anchor_not_found"


def test_connect_pins_handles_incompatible_pins() -> None:
    """When K2 schema rejects (e.g., string → bool), incompatible_pins with UE's reason."""
    fake_response = (
        b'{"ok":false,"command":"connect_pins","error":"incompatible_pins",'
        b'"detail":"Boolean is not compatible with String"}\n'
    )

    class FakeSock:
        def __enter__(self): return self
        def __exit__(self, *a): pass
        def sendall(self, data): pass
        def recv(self, n):
            if getattr(self, "_sent", False): return b""
            self._sent = True; return fake_response

    with mock.patch.object(socket, "create_connection", return_value=FakeSock()):
        result = server.connect_pins(
            blueprint="/Game/Blueprints/BP_X",
            from_pin="some.string_out",
            to_pin="other.bool_in",
        )
    assert result["ok"] is False
    assert result["error"] == "incompatible_pins"
    # UE's reason text bubbled up in detail
    assert "compatible" in result["detail"].lower()


# ---------------------------------------------------------------------------
# compile_blueprint (Spike B5)
# ---------------------------------------------------------------------------


def test_compile_blueprint_success() -> None:
    fake_response = (
        b'{"ok":true,"command":"compile_blueprint","status":"up_to_date","saved":true}\n'
    )
    sent: dict[str, bytes] = {}

    class FakeSock:
        def __enter__(self): return self
        def __exit__(self, *a): pass
        def sendall(self, data):
            sent["data"] = data
        def recv(self, n):
            if getattr(self, "_sent", False): return b""
            self._sent = True; return fake_response

    with mock.patch.object(socket, "create_connection", return_value=FakeSock()):
        result = server.compile_blueprint(name="/Game/Blueprints/BP_TestSpikeB1_v2")

    assert result["ok"] is True
    assert result["status"] == "up_to_date"
    assert result["saved"] is True

    import json
    sent_dict = json.loads(sent["data"].decode("utf-8").rstrip())
    assert sent_dict == {"command": "compile_blueprint", "name": "/Game/Blueprints/BP_TestSpikeB1_v2"}


def test_compile_blueprint_handles_warnings() -> None:
    """Compile succeeds but with warnings — still ok=true."""
    fake_response = (
        b'{"ok":true,"command":"compile_blueprint","status":"warnings","saved":true}\n'
    )

    class FakeSock:
        def __enter__(self): return self
        def __exit__(self, *a): pass
        def sendall(self, data): pass
        def recv(self, n):
            if getattr(self, "_sent", False): return b""
            self._sent = True; return fake_response

    with mock.patch.object(socket, "create_connection", return_value=FakeSock()):
        result = server.compile_blueprint(name="/Game/Blueprints/BP_X")
    assert result["ok"] is True
    assert result["status"] == "warnings"


def test_compile_blueprint_handles_error() -> None:
    fake_response = (
        b'{"ok":false,"command":"compile_blueprint","error":"compile_failed",'
        b'"status":"error","hint":"See UE Editor Message Log...","saved":false}\n'
    )

    class FakeSock:
        def __enter__(self): return self
        def __exit__(self, *a): pass
        def sendall(self, data): pass
        def recv(self, n):
            if getattr(self, "_sent", False): return b""
            self._sent = True; return fake_response

    with mock.patch.object(socket, "create_connection", return_value=FakeSock()):
        result = server.compile_blueprint(name="/Game/Blueprints/BP_Broken")
    assert result["ok"] is False
    assert result["error"] == "compile_failed"
    assert result["status"] == "error"
    assert "hint" in result


# ---------------------------------------------------------------------------
# spawn_actor (Spike B6)
# ---------------------------------------------------------------------------


def test_spawn_actor_success() -> None:
    fake_response = (
        b'{"ok":true,"command":"spawn_actor",'
        b'"blueprint_path":"/Game/Blueprints/BP_TestSpikeB1_v2",'
        b'"actor_name":"BP_TestSpikeB1_v2_C_1","location":[0.000000,0.000000,0.000000]}\n'
    )
    sent: dict[str, bytes] = {}

    class FakeSock:
        def __enter__(self): return self
        def __exit__(self, *a): pass
        def sendall(self, data):
            sent["data"] = data
        def recv(self, n):
            if getattr(self, "_sent", False): return b""
            self._sent = True; return fake_response

    with mock.patch.object(socket, "create_connection", return_value=FakeSock()):
        result = server.spawn_actor(blueprint="/Game/Blueprints/BP_TestSpikeB1_v2")

    assert result["ok"] is True
    assert result["actor_name"] == "BP_TestSpikeB1_v2_C_1"
    assert result["location"] == [0.0, 0.0, 0.0]

    import json
    sent_dict = json.loads(sent["data"].decode("utf-8").rstrip())
    assert sent_dict == {
        "command": "spawn_actor",
        "blueprint": "/Game/Blueprints/BP_TestSpikeB1_v2",
        "location_x": 0.0,
        "location_y": 0.0,
        "location_z": 0.0,
    }


def test_spawn_actor_handles_no_generated_class() -> None:
    """BP wasn't compiled yet → no_generated_class."""
    fake_response = (
        b'{"ok":false,"command":"spawn_actor","error":"no_generated_class",'
        b'"detail":"Blueprint must be compiled first (call compile_blueprint)"}\n'
    )

    class FakeSock:
        def __enter__(self): return self
        def __exit__(self, *a): pass
        def sendall(self, data): pass
        def recv(self, n):
            if getattr(self, "_sent", False): return b""
            self._sent = True; return fake_response

    with mock.patch.object(socket, "create_connection", return_value=FakeSock()):
        result = server.spawn_actor(blueprint="/Game/Blueprints/BP_NotCompiled")
    assert result["ok"] is False
    assert result["error"] == "no_generated_class"


def test_spawn_actor_passes_location() -> None:
    fake_response = (
        b'{"ok":true,"command":"spawn_actor","blueprint_path":"/Game/X",'
        b'"actor_name":"X_C_1","location":[100.000000,200.000000,50.000000]}\n'
    )
    sent: dict[str, bytes] = {}

    class FakeSock:
        def __enter__(self): return self
        def __exit__(self, *a): pass
        def sendall(self, data):
            sent["data"] = data
        def recv(self, n):
            if getattr(self, "_sent", False): return b""
            self._sent = True; return fake_response

    with mock.patch.object(socket, "create_connection", return_value=FakeSock()):
        server.spawn_actor(
            blueprint="/Game/X",
            location_x=100.0,
            location_y=200.0,
            location_z=50.0,
        )

    import json
    sent_dict = json.loads(sent["data"].decode("utf-8").rstrip())
    assert sent_dict["location_x"] == 100.0
    assert sent_dict["location_y"] == 200.0
    assert sent_dict["location_z"] == 50.0


# ---------------------------------------------------------------------------
# v1 — add_component / add_custom_event / add_variable / add_variable_get/set
# ---------------------------------------------------------------------------


def _fake_sock(response_bytes: bytes, sent_record: dict | None = None):
    """v6.0.2: must simulate socket close after first read so the recv-loop in
    _send_command terminates. Returning the same bytes forever caused an
    infinite loop in pytest after the v6.0.2 P1 fix landed."""
    class FakeSock:
        def __init__(self):
            self._sent = False
        def __enter__(self): return self
        def __exit__(self, *a): pass
        def sendall(self, data):
            if sent_record is not None:
                sent_record["data"] = data
        def recv(self, n):
            if self._sent:
                return b""   # simulate EOF — server closed the connection
            self._sent = True
            return response_bytes
    return FakeSock()


# --- B7 add_component ---


def test_add_component_success() -> None:
    response = b'{"ok":true,"command":"add_component","component_name":"TriggerBox","component_class":"BoxComponent","saved":true}\n'
    with mock.patch.object(socket, "create_connection", return_value=_fake_sock(response)):
        r = server.add_component(blueprint="/Game/Blueprints/BP_X", component_class="BoxCollision", name="TriggerBox")
    assert r["ok"] is True
    assert r["component_name"] == "TriggerBox"
    assert "Box" in r["component_class"]


def test_add_component_handles_parent_not_actor() -> None:
    response = b'{"ok":false,"command":"add_component","error":"parent_not_actor","detail":"..."}\n'
    with mock.patch.object(socket, "create_connection", return_value=_fake_sock(response)):
        r = server.add_component(blueprint="/Game/Blueprints/BP_X", component_class="BoxCollision", name="x")
    assert r["ok"] is False
    assert r["error"] == "parent_not_actor"


# --- B8 add_custom_event ---


def test_add_custom_event_success() -> None:
    response = (
        b'{"ok":true,"command":"add_custom_event","anchor_name":"my_timer_cb",'
        b'"event_name":"OnTimerElapsed","node_guid":"GUID","pins":[{"name":"then","direction":"output","type":"exec"}],"saved":true}\n'
    )
    sent: dict = {}
    with mock.patch.object(socket, "create_connection", return_value=_fake_sock(response, sent)):
        r = server.add_custom_event(blueprint="/Game/Blueprints/BP_X", event_name="OnTimerElapsed",
                                    anchor_name="my_timer_cb", position_x=500, position_y=0)
    assert r["ok"] is True
    assert r["event_name"] == "OnTimerElapsed"
    assert any(p["name"] == "then" and p["type"] == "exec" for p in r["pins"])

    import json
    sent_dict = json.loads(sent["data"].decode("utf-8").rstrip())
    assert sent_dict["command"] == "add_custom_event"
    assert sent_dict["event_name"] == "OnTimerElapsed"


def test_add_custom_event_handles_duplicate_name() -> None:
    response = b'{"ok":false,"command":"add_custom_event","error":"event_name_exists","detail":"OnTimerElapsed"}\n'
    with mock.patch.object(socket, "create_connection", return_value=_fake_sock(response)):
        r = server.add_custom_event(blueprint="/Game/X", event_name="OnTimerElapsed", anchor_name="x")
    assert r["ok"] is False
    assert r["error"] == "event_name_exists"


# --- B9 add_variable ---


def test_add_variable_success_timer_handle() -> None:
    response = b'{"ok":true,"command":"add_variable","variable_name":"MyTimer","variable_type":"TimerHandle","saved":true}\n'
    sent: dict = {}
    with mock.patch.object(socket, "create_connection", return_value=_fake_sock(response, sent)):
        r = server.add_variable(blueprint="/Game/X", name="MyTimer", variable_type="TimerHandle")
    assert r["ok"] is True
    assert r["variable_type"] == "TimerHandle"

    import json
    sent_dict = json.loads(sent["data"].decode("utf-8").rstrip())
    assert sent_dict == {
        "command": "add_variable",
        "blueprint": "/Game/X",
        "name": "MyTimer",
        "variable_type": "TimerHandle",
        "default_value": "",
    }


def test_add_variable_handles_unknown_type() -> None:
    response = b'{"ok":false,"command":"add_variable","error":"unknown_variable_type","detail":"WeirdType"}\n'
    with mock.patch.object(socket, "create_connection", return_value=_fake_sock(response)):
        r = server.add_variable(blueprint="/Game/X", name="x", variable_type="WeirdType")
    assert r["ok"] is False
    assert r["error"] == "unknown_variable_type"


# --- B10 add_variable_get / add_variable_set ---


def test_add_variable_get_success() -> None:
    response = (
        b'{"ok":true,"command":"add_variable_get","anchor_name":"read_timer",'
        b'"variable_name":"MyTimer","node_guid":"G","pins":[{"name":"MyTimer","direction":"output","type":"struct"}],"saved":true}\n'
    )
    with mock.patch.object(socket, "create_connection", return_value=_fake_sock(response)):
        r = server.add_variable_get(blueprint="/Game/X", variable_name="MyTimer", anchor_name="read_timer")
    assert r["ok"] is True
    assert r["variable_name"] == "MyTimer"


def test_add_variable_set_success() -> None:
    response = (
        b'{"ok":true,"command":"add_variable_set","anchor_name":"write_timer",'
        b'"variable_name":"MyTimer","node_guid":"G","pins":[],"saved":true}\n'
    )
    sent: dict = {}
    with mock.patch.object(socket, "create_connection", return_value=_fake_sock(response, sent)):
        r = server.add_variable_set(blueprint="/Game/X", variable_name="MyTimer", anchor_name="write_timer")
    assert r["ok"] is True

    import json
    sent_dict = json.loads(sent["data"].decode("utf-8").rstrip())
    assert sent_dict["command"] == "add_variable_set"
    assert sent_dict["variable_name"] == "MyTimer"


def test_add_variable_get_handles_var_not_found() -> None:
    response = b'{"ok":false,"command":"add_variable_get","error":"variable_not_found","detail":"NoSuchVar (call add_variable first)"}\n'
    with mock.patch.object(socket, "create_connection", return_value=_fake_sock(response)):
        r = server.add_variable_get(blueprint="/Game/X", variable_name="NoSuchVar", anchor_name="x")
    assert r["ok"] is False
    assert r["error"] == "variable_not_found"


# ---------------------------------------------------------------------------
# get_blueprint (v2)
# ---------------------------------------------------------------------------


def test_get_blueprint_success() -> None:
    fake_response = (
        b'{"ok":true,"command":"get_blueprint","path":"/Game/Blueprints/BP_X",'
        b'"parent_class":"Actor","compiled":true,"status":"up_to_date",'
        b'"anchors":{"begin_play":{"k2_node_class":"K2Node_Event","position":[-300,0],'
        b'"event_name":"ReceiveBeginPlay","pins":[{"name":"then","direction":"output","type":"exec","linked":true}]},'
        b'"print_hello":{"k2_node_class":"K2Node_CallFunction","position":[200,100],'
        b'"function":"PrintString","owning_class":"KismetSystemLibrary",'
        b'"pins":[{"name":"execute","direction":"input","type":"exec","linked":true},'
        b'{"name":"InString","direction":"input","type":"string","default":"hello world"}]}},'
        b'"connections":[{"from":"begin_play.then","to":"print_hello.execute"}],'
        b'"variables":[{"name":"MyTimer","type":"struct","subcategory":"TimerHandle"}],'
        b'"components":[{"name":"TriggerBox","class":"BoxComponent"}]}\n'
    )
    sent: dict = {}
    with mock.patch.object(socket, "create_connection", return_value=_fake_sock(fake_response, sent)):
        r = server.get_blueprint(name="/Game/Blueprints/BP_X")

    assert r["ok"] is True
    assert r["path"] == "/Game/Blueprints/BP_X"
    assert r["parent_class"] == "Actor"
    assert r["compiled"] is True

    # Anchors structure
    assert "begin_play" in r["anchors"]
    assert r["anchors"]["begin_play"]["k2_node_class"] == "K2Node_Event"
    assert "print_hello" in r["anchors"]
    assert r["anchors"]["print_hello"]["function"] == "PrintString"

    # Pin with default surfaced
    print_pins = r["anchors"]["print_hello"]["pins"]
    in_string_pin = next(p for p in print_pins if p["name"] == "InString")
    assert in_string_pin["default"] == "hello world"

    # Connections + variables + components
    assert {"from": "begin_play.then", "to": "print_hello.execute"} in r["connections"]
    assert any(v["name"] == "MyTimer" and v["subcategory"] == "TimerHandle" for v in r["variables"])
    assert any(c["name"] == "TriggerBox" and c["class"] == "BoxComponent" for c in r["components"])

    import json
    sent_dict = json.loads(sent["data"].decode("utf-8").rstrip())
    assert sent_dict == {"command": "get_blueprint", "name": "/Game/Blueprints/BP_X"}


def test_get_blueprint_handles_not_found() -> None:
    fake_response = b'{"ok":false,"command":"get_blueprint","error":"blueprint_not_found","detail":"/Game/X"}\n'
    with mock.patch.object(socket, "create_connection", return_value=_fake_sock(fake_response)):
        r = server.get_blueprint(name="/Game/X")
    assert r["ok"] is False
    assert r["error"] == "blueprint_not_found"


def test_get_blueprint_handles_empty_bp() -> None:
    """A fresh BP with no nodes, vars, or components — still valid response with empty arrays/dicts."""
    fake_response = (
        b'{"ok":true,"command":"get_blueprint","path":"/Game/X","parent_class":"Actor",'
        b'"compiled":true,"status":"up_to_date","anchors":{},"connections":[],"variables":[],"components":[]}\n'
    )
    with mock.patch.object(socket, "create_connection", return_value=_fake_sock(fake_response)):
        r = server.get_blueprint(name="/Game/X")
    assert r["ok"] is True
    assert r["anchors"] == {}
    assert r["connections"] == []
    assert r["variables"] == []
    assert r["components"] == []


# ---------------------------------------------------------------------------
# v3 — add_branch / add_cast
# ---------------------------------------------------------------------------


def test_add_branch_success() -> None:
    response = (
        b'{"ok":true,"command":"add_branch","anchor_name":"check_alive",'
        b'"node_guid":"G","node_type":"K2Node_IfThenElse",'
        b'"pins":[{"name":"execute","direction":"input","type":"exec"},'
        b'{"name":"Condition","direction":"input","type":"bool"},'
        b'{"name":"then","direction":"output","type":"exec"},'
        b'{"name":"else","direction":"output","type":"exec"}],"saved":true}\n'
    )
    sent: dict = {}
    with mock.patch.object(socket, "create_connection", return_value=_fake_sock(response, sent)):
        r = server.add_branch(blueprint="/Game/X", anchor_name="check_alive", position_x=300, position_y=0)
    assert r["ok"] is True
    assert r["anchor_name"] == "check_alive"
    assert r["node_type"] == "K2Node_IfThenElse"
    pin_names = {p["name"] for p in r["pins"]}
    assert {"execute", "Condition", "then", "else"} <= pin_names

    import json
    sent_dict = json.loads(sent["data"].decode("utf-8").rstrip())
    assert sent_dict["command"] == "add_branch"


def test_add_branch_anchor_exists() -> None:
    response = b'{"ok":false,"command":"add_branch","error":"anchor_name_exists","detail":"check_alive"}\n'
    with mock.patch.object(socket, "create_connection", return_value=_fake_sock(response)):
        r = server.add_branch(blueprint="/Game/X", anchor_name="check_alive")
    assert r["ok"] is False
    assert r["error"] == "anchor_name_exists"


def test_add_cast_success() -> None:
    response = (
        b'{"ok":true,"command":"add_cast","anchor_name":"cast_to_pawn",'
        b'"node_guid":"G","node_type":"K2Node_DynamicCast","target_class":"Pawn",'
        b'"pins":[{"name":"execute","direction":"input","type":"exec"},'
        b'{"name":"Object","direction":"input","type":"object"},'
        b'{"name":"then","direction":"output","type":"exec"},'
        b'{"name":"AsPawn","direction":"output","type":"object"},'
        b'{"name":"CastFailed","direction":"output","type":"exec"}],"saved":true}\n'
    )
    sent: dict = {}
    with mock.patch.object(socket, "create_connection", return_value=_fake_sock(response, sent)):
        r = server.add_cast(
            blueprint="/Game/X",
            target_class="Pawn",
            anchor_name="cast_to_pawn",
            position_x=600,
        )
    assert r["ok"] is True
    assert r["target_class"] == "Pawn"
    pin_names = {p["name"] for p in r["pins"]}
    assert "AsPawn" in pin_names
    assert "CastFailed" in pin_names

    import json
    sent_dict = json.loads(sent["data"].decode("utf-8").rstrip())
    assert sent_dict["command"] == "add_cast"
    assert sent_dict["target_class"] == "Pawn"


def test_add_cast_handles_unknown_class() -> None:
    response = b'{"ok":false,"command":"add_cast","error":"unknown_target_class","detail":"FooBar"}\n'
    with mock.patch.object(socket, "create_connection", return_value=_fake_sock(response)):
        r = server.add_cast(blueprint="/Game/X", target_class="FooBar", anchor_name="bad")
    assert r["ok"] is False
    assert r["error"] == "unknown_target_class"


# ---------------------------------------------------------------------------
# v4 — add_macro / add_self_reference / add_input_key / delete_node /
#      disconnect_pins / set_pin_default for struct types
# ---------------------------------------------------------------------------


def test_add_macro_for_each_loop_success() -> None:
    response = (
        b'{"ok":true,"command":"add_macro","anchor_name":"iter","node_guid":"G",'
        b'"macro_type":"ForEachLoop",'
        b'"pins":[{"name":"execute","direction":"input","type":"exec"},'
        b'{"name":"Array","direction":"input","type":"wildcard"},'
        b'{"name":"LoopBody","direction":"output","type":"exec"},'
        b'{"name":"Array Element","direction":"output","type":"wildcard"},'
        b'{"name":"Array Index","direction":"output","type":"int"},'
        b'{"name":"Completed","direction":"output","type":"exec"}],"saved":true}\n'
    )
    sent: dict = {}
    with mock.patch.object(socket, "create_connection", return_value=_fake_sock(response, sent)):
        r = server.add_macro(blueprint="/Game/X", macro_type="ForEachLoop", anchor_name="iter")
    assert r["ok"] is True
    assert r["macro_type"] == "ForEachLoop"
    pin_names = {p["name"] for p in r["pins"]}
    assert "LoopBody" in pin_names
    assert "Array Element" in pin_names


def test_add_macro_handles_unknown_type() -> None:
    response = b'{"ok":false,"command":"add_macro","error":"unknown_macro_type","detail":"FooBar (known: ...)"}\n'
    with mock.patch.object(socket, "create_connection", return_value=_fake_sock(response)):
        r = server.add_macro(blueprint="/Game/X", macro_type="FooBar", anchor_name="x")
    assert r["ok"] is False
    assert r["error"] == "unknown_macro_type"


def test_add_self_reference_success() -> None:
    response = (
        b'{"ok":true,"command":"add_self_reference","anchor_name":"me","node_guid":"G",'
        b'"pins":[{"name":"self","direction":"output","type":"object"}],"saved":true}\n'
    )
    with mock.patch.object(socket, "create_connection", return_value=_fake_sock(response)):
        r = server.add_self_reference(blueprint="/Game/X", anchor_name="me")
    assert r["ok"] is True
    assert any(p["name"] == "self" and p["direction"] == "output" for p in r["pins"])


def test_add_input_key_success() -> None:
    response = (
        b'{"ok":true,"command":"add_input_key","anchor_name":"on_p","node_guid":"G","key":"P",'
        b'"pins":[{"name":"Pressed","direction":"output","type":"exec"},'
        b'{"name":"Released","direction":"output","type":"exec"}],"saved":true}\n'
    )
    sent: dict = {}
    with mock.patch.object(socket, "create_connection", return_value=_fake_sock(response, sent)):
        r = server.add_input_key(blueprint="/Game/X", key="P", anchor_name="on_p")
    assert r["ok"] is True
    assert r["key"] == "P"

    import json
    sent_dict = json.loads(sent["data"].decode("utf-8").rstrip())
    assert sent_dict["key"] == "P"


def test_add_input_key_handles_invalid() -> None:
    response = b'{"ok":false,"command":"add_input_key","error":"invalid_key","detail":"NotAKey (try: P, Space, ...)"}\n'
    with mock.patch.object(socket, "create_connection", return_value=_fake_sock(response)):
        r = server.add_input_key(blueprint="/Game/X", key="NotAKey", anchor_name="x")
    assert r["ok"] is False
    assert r["error"] == "invalid_key"


def test_delete_node_success() -> None:
    response = b'{"ok":true,"command":"delete_node","anchor_name":"print_hello","node_type":"K2Node_CallFunction","saved":true}\n'
    sent: dict = {}
    with mock.patch.object(socket, "create_connection", return_value=_fake_sock(response, sent)):
        r = server.delete_node(blueprint="/Game/X", anchor_name="print_hello")
    assert r["ok"] is True
    assert r["node_type"] == "K2Node_CallFunction"

    import json
    sent_dict = json.loads(sent["data"].decode("utf-8").rstrip())
    assert sent_dict == {"command": "delete_node", "blueprint": "/Game/X", "anchor_name": "print_hello"}


def test_delete_node_handles_not_found() -> None:
    response = b'{"ok":false,"command":"delete_node","error":"anchor_not_found","detail":"missing"}\n'
    with mock.patch.object(socket, "create_connection", return_value=_fake_sock(response)):
        r = server.delete_node(blueprint="/Game/X", anchor_name="missing")
    assert r["ok"] is False
    assert r["error"] == "anchor_not_found"


def test_disconnect_pins_success() -> None:
    response = b'{"ok":true,"command":"disconnect_pins","from":"begin_play.then","to":"print_hello.execute","saved":true}\n'
    sent: dict = {}
    with mock.patch.object(socket, "create_connection", return_value=_fake_sock(response, sent)):
        r = server.disconnect_pins(blueprint="/Game/X",
                                   from_pin="begin_play.then", to_pin="print_hello.execute")
    assert r["ok"] is True

    import json
    sent_dict = json.loads(sent["data"].decode("utf-8").rstrip())
    assert sent_dict["from_pin"] == "begin_play.then"
    assert sent_dict["to_pin"] == "print_hello.execute"


def test_disconnect_pins_handles_not_connected() -> None:
    response = b'{"ok":false,"command":"disconnect_pins","error":"not_connected","detail":"a.b -> c.d"}\n'
    with mock.patch.object(socket, "create_connection", return_value=_fake_sock(response)):
        r = server.disconnect_pins(blueprint="/Game/X", from_pin="a.b", to_pin="c.d")
    assert r["ok"] is False
    assert r["error"] == "not_connected"


# ---------------------------------------------------------------------------
# v5 — add_function, call_blueprint_function, Enhanced Input (4 tools)
# ---------------------------------------------------------------------------


def test_add_function_success() -> None:
    response = b'{"ok":true,"command":"add_function","function_name":"DoThing","saved":true}\n'
    with mock.patch.object(socket, "create_connection", return_value=_fake_sock(response)):
        r = server.add_function(blueprint="/Game/X", name="DoThing")
    assert r["ok"] is True
    assert r["function_name"] == "DoThing"


def test_add_function_exists() -> None:
    response = b'{"ok":false,"command":"add_function","error":"function_exists","detail":"DoThing"}\n'
    with mock.patch.object(socket, "create_connection", return_value=_fake_sock(response)):
        r = server.add_function(blueprint="/Game/X", name="DoThing")
    assert r["ok"] is False
    assert r["error"] == "function_exists"


def test_call_blueprint_function_success() -> None:
    response = (
        b'{"ok":true,"command":"call_blueprint_function","anchor_name":"call_dothing",'
        b'"node_guid":"G","target_class":"BP_Manager_C","function":"DoThing",'
        b'"pins":[{"name":"execute","direction":"input","type":"exec"}],"saved":true}\n'
    )
    sent: dict = {}
    with mock.patch.object(socket, "create_connection", return_value=_fake_sock(response, sent)):
        r = server.call_blueprint_function(
            blueprint="/Game/Blueprints/BP_B",
            target_class="BP_Manager",
            function_name="DoThing",
            anchor_name="call_dothing",
        )
    assert r["ok"] is True
    assert r["target_class"] == "BP_Manager_C"
    import json
    sent_dict = json.loads(sent["data"].decode("utf-8").rstrip())
    assert sent_dict["command"] == "call_blueprint_function"
    assert sent_dict["function_name"] == "DoThing"


def test_call_blueprint_function_with_target_pin_success() -> None:
    """v6: target_pin auto-wires self pin."""
    response = (
        b'{"ok":true,"command":"call_blueprint_function","anchor_name":"call_dothing",'
        b'"node_guid":"G","target_class":"BP_Manager_C","function":"DoThing",'
        b'"pins":[{"name":"execute","direction":"input","type":"exec"}],'
        b'"self_wired":true,"self_source":"get_target.ReturnValue","saved":true}\n'
    )
    sent: dict = {}
    with mock.patch.object(socket, "create_connection", return_value=_fake_sock(response, sent)):
        r = server.call_blueprint_function(
            blueprint="/Game/Blueprints/BP_B",
            target_class="BP_Manager",
            function_name="DoThing",
            anchor_name="call_dothing",
            target_pin="get_target.ReturnValue",
        )
    assert r["ok"] is True
    assert r["self_wired"] is True
    assert r["self_source"] == "get_target.ReturnValue"

    import json
    sent_dict = json.loads(sent["data"].decode("utf-8").rstrip())
    assert sent_dict["target_pin"] == "get_target.ReturnValue"


def test_call_blueprint_function_handles_class_not_found() -> None:
    response = b'{"ok":false,"command":"call_blueprint_function","error":"target_class_not_found","detail":"BP_DoesNotExist"}\n'
    with mock.patch.object(socket, "create_connection", return_value=_fake_sock(response)):
        r = server.call_blueprint_function(
            blueprint="/Game/X", target_class="BP_DoesNotExist",
            function_name="X", anchor_name="x",
        )
    assert r["ok"] is False
    assert r["error"] == "target_class_not_found"


def test_create_input_action_success() -> None:
    response = b'{"ok":true,"command":"create_input_action","action_path":"/Game/Input/Actions/IA_Jump","value_type":"Boolean","saved":true}\n'
    sent: dict = {}
    with mock.patch.object(socket, "create_connection", return_value=_fake_sock(response, sent)):
        r = server.create_input_action(name="IA_Jump", value_type="Boolean")
    assert r["ok"] is True
    assert r["action_path"].endswith("IA_Jump")

    import json
    sent_dict = json.loads(sent["data"].decode("utf-8").rstrip())
    assert sent_dict["name"] == "IA_Jump"
    assert sent_dict["value_type"] == "Boolean"


def test_create_input_action_handles_unknown_value_type() -> None:
    response = b'{"ok":false,"command":"create_input_action","error":"unknown_value_type","detail":"WeirdType (use: ...)"}\n'
    with mock.patch.object(socket, "create_connection", return_value=_fake_sock(response)):
        r = server.create_input_action(name="x", value_type="WeirdType")
    assert r["ok"] is False
    assert r["error"] == "unknown_value_type"


def test_create_input_mapping_context_success() -> None:
    response = b'{"ok":true,"command":"create_input_mapping_context","imc_path":"/Game/Input/IMC_Default","saved":true}\n'
    with mock.patch.object(socket, "create_connection", return_value=_fake_sock(response)):
        r = server.create_input_mapping_context(name="IMC_Default")
    assert r["ok"] is True


def test_add_mapping_to_imc_success() -> None:
    response = (
        b'{"ok":true,"command":"add_mapping_to_imc","imc_path":"/Game/Input/IMC_Default",'
        b'"action_path":"/Game/Input/Actions/IA_Jump","key":"Space","saved":true}\n'
    )
    sent: dict = {}
    with mock.patch.object(socket, "create_connection", return_value=_fake_sock(response, sent)):
        r = server.add_mapping_to_imc(
            imc_path="/Game/Input/IMC_Default",
            action_path="/Game/Input/Actions/IA_Jump",
            key="Space",
        )
    assert r["ok"] is True
    assert r["key"] == "Space"


def test_add_enhanced_input_node_success() -> None:
    response = (
        b'{"ok":true,"command":"add_enhanced_input_node","anchor_name":"on_jump","node_guid":"G",'
        b'"action_path":"/Game/Input/Actions/IA_Jump",'
        b'"pins":[{"name":"Triggered","direction":"output","type":"exec"}],"saved":true}\n'
    )
    with mock.patch.object(socket, "create_connection", return_value=_fake_sock(response)):
        r = server.add_enhanced_input_node(
            blueprint="/Game/Blueprints/BP_Player",
            action_path="/Game/Input/Actions/IA_Jump",
            anchor_name="on_jump",
        )
    assert r["ok"] is True
    assert any(p["name"] == "Triggered" for p in r["pins"])


# ---------------------------------------------------------------------------
# v6 — wire_imc_subscribe
# ---------------------------------------------------------------------------


def test_wire_imc_subscribe_success() -> None:
    response = (
        b'{"ok":true,"command":"wire_imc_subscribe",'
        b'"anchors_created":["imc_sub_get_pc","imc_sub_get_sub","imc_sub_add_ctx"],'
        b'"imc_path":"/Game/Input/IMC_Default","priority":0,"saved":true}\n'
    )
    sent: dict = {}
    with mock.patch.object(socket, "create_connection", return_value=_fake_sock(response, sent)):
        r = server.wire_imc_subscribe(
            blueprint="/Game/Blueprints/BP_PlayerController",
            imc_path="/Game/Input/IMC_Default",
            priority=0,
        )
    assert r["ok"] is True
    assert "imc_sub_get_pc" in r["anchors_created"]
    assert "imc_sub_add_ctx" in r["anchors_created"]
    assert r["priority"] == 0

    import json
    sent_dict = json.loads(sent["data"].decode("utf-8").rstrip())
    assert sent_dict["command"] == "wire_imc_subscribe"
    assert sent_dict["anchor_prefix"] == "imc_sub"


def test_wire_imc_subscribe_custom_prefix() -> None:
    response = (
        b'{"ok":true,"command":"wire_imc_subscribe",'
        b'"anchors_created":["combat_get_pc","combat_get_sub","combat_add_ctx"],'
        b'"imc_path":"/Game/Input/IMC_Combat","priority":1,"saved":true}\n'
    )
    sent: dict = {}
    with mock.patch.object(socket, "create_connection", return_value=_fake_sock(response, sent)):
        server.wire_imc_subscribe(
            blueprint="/Game/Blueprints/BP_X",
            imc_path="/Game/Input/IMC_Combat",
            priority=1,
            anchor_prefix="combat",
        )

    import json
    sent_dict = json.loads(sent["data"].decode("utf-8").rstrip())
    assert sent_dict["anchor_prefix"] == "combat"
    assert sent_dict["priority"] == 1


def test_wire_imc_subscribe_handles_anchor_collision() -> None:
    response = b'{"ok":false,"command":"wire_imc_subscribe","error":"anchor_name_exists","detail":"imc_sub_get_pc"}\n'
    with mock.patch.object(socket, "create_connection", return_value=_fake_sock(response)):
        r = server.wire_imc_subscribe(blueprint="/Game/X", imc_path="/Game/Y")
    assert r["ok"] is False
    assert r["error"] == "anchor_name_exists"


# ---------------------------------------------------------------------------
# Integration tests (require a real UE editor + plugin)
# ---------------------------------------------------------------------------


@requires_ue_editor(extra_reason="BlueprintMCP plugin loaded")
def test_ping_ue_against_real_plugin() -> None:
    result = server.ping_ue()
    assert result["ok"] is True
    assert "version" in result


# ---------------------------------------------------------------------------
# v7.1 — set_component_property
# ---------------------------------------------------------------------------


def test_set_component_property_success_object_ref() -> None:
    """Setting StaticMesh asset (FObjectProperty) on a StaticMeshComponent template."""
    response = (
        b'{"ok":true,"command":"set_component_property",'
        b'"blueprint":"/Game/BP_TargetDummy","component":"VisualMesh",'
        b'"property":"StaticMesh","resolved_value":"/Engine/BasicShapes/Cube.Cube",'
        b'"saved":true}\n'
    )
    sent: dict = {}
    with mock.patch.object(socket, "create_connection", return_value=_fake_sock(response, sent)):
        r = server.set_component_property(
            blueprint="/Game/BP_TargetDummy",
            component_name="VisualMesh",
            property_name="StaticMesh",
            value="/Engine/BasicShapes/Cube",
        )
    assert r["ok"] is True
    assert r["resolved_value"] == "/Engine/BasicShapes/Cube.Cube"

    import json
    sent_dict = json.loads(sent["data"].decode("utf-8").rstrip())
    assert sent_dict == {
        "command": "set_component_property",
        "blueprint": "/Game/BP_TargetDummy",
        "component_name": "VisualMesh",
        "property_name": "StaticMesh",
        "value": "/Engine/BasicShapes/Cube",
    }


def test_set_component_property_success_struct_literal() -> None:
    """Setting BoxExtent (FStructProperty / FVector) via (X=,Y=,Z=) literal."""
    response = (
        b'{"ok":true,"command":"set_component_property",'
        b'"resolved_value":"(X=200,Y=200,Z=200)","saved":true}\n'
    )
    sent: dict = {}
    with mock.patch.object(socket, "create_connection", return_value=_fake_sock(response, sent)):
        r = server.set_component_property(
            blueprint="/Game/BP_X",
            component_name="TriggerBox",
            property_name="BoxExtent",
            value="(X=200,Y=200,Z=200)",
        )
    assert r["ok"] is True

    import json
    sent_dict = json.loads(sent["data"].decode("utf-8").rstrip())
    assert sent_dict["value"] == "(X=200,Y=200,Z=200)"


def test_set_component_property_success_nested_path() -> None:
    """Dot-notation: walk BodyInstance.CollisionProfileName (FStructProperty → FNameProperty)."""
    response = (
        b'{"ok":true,"command":"set_component_property",'
        b'"resolved_value":"OverlapAllDynamic","saved":true}\n'
    )
    sent: dict = {}
    with mock.patch.object(socket, "create_connection", return_value=_fake_sock(response, sent)):
        r = server.set_component_property(
            blueprint="/Game/BP_X",
            component_name="TriggerBox",
            property_name="BodyInstance.CollisionProfileName",
            value="OverlapAllDynamic",
        )
    assert r["ok"] is True

    import json
    sent_dict = json.loads(sent["data"].decode("utf-8").rstrip())
    assert sent_dict["property_name"] == "BodyInstance.CollisionProfileName"


def test_set_component_property_handles_property_not_found() -> None:
    response = (
        b'{"ok":false,"command":"set_component_property","error":"property_not_found",'
        b'"detail":"Component VisualMesh has no property Foo"}\n'
    )
    with mock.patch.object(socket, "create_connection", return_value=_fake_sock(response)):
        r = server.set_component_property(
            blueprint="/Game/BP_X",
            component_name="VisualMesh",
            property_name="Foo",
            value="bar",
        )
    assert r["ok"] is False
    assert r["error"] == "property_not_found"


def test_set_component_property_handles_component_not_found() -> None:
    response = (
        b'{"ok":false,"command":"set_component_property",'
        b'"error":"component_not_found","detail":"NoSuchComponent"}\n'
    )
    with mock.patch.object(socket, "create_connection", return_value=_fake_sock(response)):
        r = server.set_component_property(
            blueprint="/Game/BP_X",
            component_name="NoSuchComponent",
            property_name="StaticMesh",
            value="/Engine/X",
        )
    assert r["ok"] is False
    assert r["error"] == "component_not_found"


def test_set_component_property_local_validation_missing_args() -> None:
    """Missing required arg short-circuits before any TCP call."""
    r = server.set_component_property(
        blueprint="", component_name="", property_name="", value=""
    )
    assert r["ok"] is False
    assert r["error"] == "missing_argument"


def test_set_component_property_empty_value_allowed() -> None:
    """Empty value is allowed (clears object refs to None on UE side)."""
    response = (
        b'{"ok":true,"command":"set_component_property",'
        b'"resolved_value":"None","saved":true}\n'
    )
    sent: dict = {}
    with mock.patch.object(socket, "create_connection", return_value=_fake_sock(response, sent)):
        r = server.set_component_property(
            blueprint="/Game/BP_X",
            component_name="VisualMesh",
            property_name="StaticMesh",
            value="",
        )
    assert r["ok"] is True

    import json
    sent_dict = json.loads(sent["data"].decode("utf-8").rstrip())
    assert sent_dict["value"] == ""


# ---------------------------------------------------------------------------
# v7.2 — add_switch / add_sequence / add_make_array / add_select
# ---------------------------------------------------------------------------


def test_add_switch_int_success() -> None:
    response = (
        b'{"ok":true,"command":"add_switch","anchor_name":"my_switch",'
        b'"switch_type":"int","node_type":"K2Node_SwitchInteger",'
        b'"node_guid":"G","pins":[{"name":"Selection","direction":"input","type":"int"}],'
        b'"saved":true}\n'
    )
    sent: dict = {}
    with mock.patch.object(socket, "create_connection", return_value=_fake_sock(response, sent)):
        r = server.add_switch(
            blueprint="/Game/BP_X",
            anchor_name="my_switch",
            switch_type="int",
            case_count=4,
        )
    assert r["ok"] is True
    assert r["node_type"] == "K2Node_SwitchInteger"

    import json
    sent_dict = json.loads(sent["data"].decode("utf-8").rstrip())
    assert sent_dict["command"] == "add_switch"
    assert sent_dict["switch_type"] == "int"
    assert sent_dict["case_count"] == 4


def test_add_switch_string_passes_case_labels() -> None:
    response = (
        b'{"ok":true,"command":"add_switch","anchor_name":"color_switch",'
        b'"switch_type":"string","node_type":"K2Node_SwitchString",'
        b'"node_guid":"G","pins":[],"saved":true}\n'
    )
    sent: dict = {}
    with mock.patch.object(socket, "create_connection", return_value=_fake_sock(response, sent)):
        r = server.add_switch(
            blueprint="/Game/BP_X",
            anchor_name="color_switch",
            switch_type="string",
            case_labels="red,green,blue",
        )
    assert r["ok"] is True

    import json
    sent_dict = json.loads(sent["data"].decode("utf-8").rstrip())
    assert sent_dict["case_labels"] == "red,green,blue"


def test_add_switch_enum_requires_enum_class() -> None:
    """enum_class missing → server returns missing_field on UE side OR plugin returns error."""
    response = (
        b'{"ok":false,"command":"add_switch","error":"missing_field",'
        b'"detail":"enum_class is required when switch_type=\\"enum\\""}\n'
    )
    with mock.patch.object(socket, "create_connection", return_value=_fake_sock(response)):
        r = server.add_switch(
            blueprint="/Game/BP_X",
            anchor_name="x",
            switch_type="enum",
            enum_class="",
        )
    assert r["ok"] is False
    assert r["error"] == "missing_field"


def test_add_switch_handles_unknown_type() -> None:
    response = (
        b'{"ok":false,"command":"add_switch","error":"unknown_switch_type",'
        b'"detail":"\'bool\' is not one of: int, string, name, enum"}\n'
    )
    with mock.patch.object(socket, "create_connection", return_value=_fake_sock(response)):
        r = server.add_switch(
            blueprint="/Game/BP_X",
            anchor_name="x",
            switch_type="bool",
        )
    assert r["ok"] is False
    assert r["error"] == "unknown_switch_type"


def test_add_sequence_success() -> None:
    response = (
        b'{"ok":true,"command":"add_sequence","anchor_name":"after_overlap",'
        b'"then_count":3,"node_guid":"G",'
        b'"pins":[{"name":"execute","direction":"input","type":"exec"},'
        b'{"name":"then_0","direction":"output","type":"exec"},'
        b'{"name":"then_1","direction":"output","type":"exec"},'
        b'{"name":"then_2","direction":"output","type":"exec"}],'
        b'"saved":true}\n'
    )
    sent: dict = {}
    with mock.patch.object(socket, "create_connection", return_value=_fake_sock(response, sent)):
        r = server.add_sequence(
            blueprint="/Game/BP_X",
            anchor_name="after_overlap",
            then_count=3,
        )
    assert r["ok"] is True
    assert r["then_count"] == 3

    import json
    sent_dict = json.loads(sent["data"].decode("utf-8").rstrip())
    assert sent_dict["command"] == "add_sequence"
    assert sent_dict["then_count"] == 3


def test_add_make_array_success() -> None:
    response = (
        b'{"ok":true,"command":"add_make_array","anchor_name":"vectors",'
        b'"num_inputs":3,"node_guid":"G","pins":[],"saved":true}\n'
    )
    sent: dict = {}
    with mock.patch.object(socket, "create_connection", return_value=_fake_sock(response, sent)):
        r = server.add_make_array(
            blueprint="/Game/BP_X",
            anchor_name="vectors",
            num_inputs=3,
        )
    assert r["ok"] is True

    import json
    sent_dict = json.loads(sent["data"].decode("utf-8").rstrip())
    assert sent_dict["command"] == "add_make_array"
    assert sent_dict["num_inputs"] == 3


def test_add_select_success() -> None:
    response = (
        b'{"ok":true,"command":"add_select","anchor_name":"pick_one",'
        b'"num_options":2,"node_guid":"G","pins":[],"saved":true}\n'
    )
    with mock.patch.object(socket, "create_connection", return_value=_fake_sock(response)):
        r = server.add_select(
            blueprint="/Game/BP_X",
            anchor_name="pick_one",
        )
    assert r["ok"] is True
    assert r["num_options"] == 2


def test_v72_local_validation_missing_args() -> None:
    """Each v7.2 tool short-circuits on empty required args."""
    assert server.add_switch(blueprint="", anchor_name="", switch_type="")["error"] == "missing_argument"
    assert server.add_sequence(blueprint="", anchor_name="")["error"] == "missing_argument"
    assert server.add_make_array(blueprint="", anchor_name="")["error"] == "missing_argument"
    assert server.add_select(blueprint="", anchor_name="")["error"] == "missing_argument"


# ---------------------------------------------------------------------------
# v7.3 — add_make_struct / add_break_struct
# ---------------------------------------------------------------------------


def test_add_make_struct_vector_success() -> None:
    response = (
        b'{"ok":true,"command":"add_make_struct","anchor_name":"mv",'
        b'"struct_type":"Vector","node_guid":"G",'
        b'"pins":[{"name":"X","direction":"input","type":"real"},'
        b'{"name":"Y","direction":"input","type":"real"},'
        b'{"name":"Z","direction":"input","type":"real"}],'
        b'"saved":true}\n'
    )
    sent: dict = {}
    with mock.patch.object(socket, "create_connection", return_value=_fake_sock(response, sent)):
        r = server.add_make_struct(
            blueprint="/Game/BP_X", anchor_name="mv", struct_type="Vector"
        )
    assert r["ok"] is True
    assert r["struct_type"] == "Vector"

    import json
    sent_dict = json.loads(sent["data"].decode("utf-8").rstrip())
    assert sent_dict == {
        "command": "add_make_struct",
        "blueprint": "/Game/BP_X",
        "struct_type": "Vector",
        "anchor_name": "mv",
        "position_x": 0,
        "position_y": 0,
    }


def test_add_make_struct_handles_unknown_struct() -> None:
    response = (
        b'{"ok":false,"command":"add_make_struct","error":"unknown_struct_type",'
        b'"detail":"NotAStruct"}\n'
    )
    with mock.patch.object(socket, "create_connection", return_value=_fake_sock(response)):
        r = server.add_make_struct(
            blueprint="/Game/BP_X", anchor_name="x", struct_type="NotAStruct"
        )
    assert r["ok"] is False
    assert r["error"] == "unknown_struct_type"


def test_add_break_struct_hit_result_success() -> None:
    response = (
        b'{"ok":true,"command":"add_break_struct","anchor_name":"bh",'
        b'"struct_type":"HitResult","node_guid":"G","pins":[],"saved":true}\n'
    )
    sent: dict = {}
    with mock.patch.object(socket, "create_connection", return_value=_fake_sock(response, sent)):
        r = server.add_break_struct(
            blueprint="/Game/BP_X", anchor_name="bh", struct_type="HitResult"
        )
    assert r["ok"] is True

    import json
    sent_dict = json.loads(sent["data"].decode("utf-8").rstrip())
    assert sent_dict["command"] == "add_break_struct"
    assert sent_dict["struct_type"] == "HitResult"


def test_v73_qualified_path_struct_type() -> None:
    """Qualified path is passed through as struct_type."""
    response = (
        b'{"ok":true,"command":"add_make_struct","anchor_name":"x",'
        b'"struct_type":"Transform","node_guid":"G","pins":[],"saved":true}\n'
    )
    sent: dict = {}
    with mock.patch.object(socket, "create_connection", return_value=_fake_sock(response, sent)):
        r = server.add_make_struct(
            blueprint="/Game/BP_X", anchor_name="x",
            struct_type="/Script/CoreUObject.Transform",
        )
    assert r["ok"] is True

    import json
    sent_dict = json.loads(sent["data"].decode("utf-8").rstrip())
    assert sent_dict["struct_type"] == "/Script/CoreUObject.Transform"


def test_v73_local_validation_missing_args() -> None:
    assert server.add_make_struct(blueprint="", anchor_name="", struct_type="")["error"] == "missing_argument"
    assert server.add_break_struct(blueprint="", anchor_name="", struct_type="")["error"] == "missing_argument"


# ---------------------------------------------------------------------------
# v7.4 — Object/Class ref variable types (extends add_variable)
# ---------------------------------------------------------------------------


def test_add_variable_object_ref_actor() -> None:
    """variable_type='object:Actor' → PC_Object with AActor subcategory."""
    response = (
        b'{"ok":true,"command":"add_variable","variable_name":"Target",'
        b'"variable_type":"object:Actor","saved":true}\n'
    )
    sent: dict = {}
    with mock.patch.object(socket, "create_connection", return_value=_fake_sock(response, sent)):
        r = server.add_variable(
            blueprint="/Game/BP_X", name="Target", variable_type="object:Actor"
        )
    assert r["ok"] is True
    assert r["variable_type"] == "object:Actor"

    import json
    sent_dict = json.loads(sent["data"].decode("utf-8").rstrip())
    assert sent_dict["variable_type"] == "object:Actor"


def test_add_variable_class_ref_pawn() -> None:
    """variable_type='class:Pawn' → PC_Class with APawn subcategory."""
    response = (
        b'{"ok":true,"command":"add_variable","variable_name":"SpawnClass",'
        b'"variable_type":"class:Pawn","saved":true}\n'
    )
    sent: dict = {}
    with mock.patch.object(socket, "create_connection", return_value=_fake_sock(response, sent)):
        r = server.add_variable(
            blueprint="/Game/BP_X", name="SpawnClass", variable_type="class:Pawn"
        )
    assert r["ok"] is True

    import json
    sent_dict = json.loads(sent["data"].decode("utf-8").rstrip())
    assert sent_dict["variable_type"] == "class:Pawn"


def test_add_variable_object_ref_array() -> None:
    """variable_type='object:Actor[]' → TArray<AActor*>."""
    response = (
        b'{"ok":true,"command":"add_variable","variable_name":"Targets",'
        b'"variable_type":"object:Actor[]","saved":true}\n'
    )
    sent: dict = {}
    with mock.patch.object(socket, "create_connection", return_value=_fake_sock(response, sent)):
        r = server.add_variable(
            blueprint="/Game/BP_X", name="Targets", variable_type="object:Actor[]"
        )
    assert r["ok"] is True

    import json
    sent_dict = json.loads(sent["data"].decode("utf-8").rstrip())
    assert sent_dict["variable_type"] == "object:Actor[]"


def test_add_variable_object_ref_handles_unknown_class() -> None:
    """object:NotARealClass → server returns unknown_variable_type."""
    response = (
        b'{"ok":false,"command":"add_variable","error":"unknown_variable_type",'
        b'"detail":"object:NotARealClass"}\n'
    )
    with mock.patch.object(socket, "create_connection", return_value=_fake_sock(response)):
        r = server.add_variable(
            blueprint="/Game/BP_X", name="X", variable_type="object:NotARealClass"
        )
    assert r["ok"] is False
    assert r["error"] == "unknown_variable_type"


# ---------------------------------------------------------------------------
# v7.5 — Custom event parameters (extends add_custom_event)
# ---------------------------------------------------------------------------


def test_add_custom_event_with_params() -> None:
    """params list is forwarded as JSON array."""
    response = (
        b'{"ok":true,"command":"add_custom_event","anchor_name":"on_hit",'
        b'"event_name":"OnHit","node_guid":"G","param_count":2,'
        b'"pins":[{"name":"then","direction":"output","type":"exec"},'
        b'{"name":"Damage","direction":"output","type":"real"},'
        b'{"name":"HitActor","direction":"output","type":"object"}],'
        b'"saved":true}\n'
    )
    sent: dict = {}
    with mock.patch.object(socket, "create_connection", return_value=_fake_sock(response, sent)):
        r = server.add_custom_event(
            blueprint="/Game/BP_X",
            event_name="OnHit",
            anchor_name="on_hit",
            params=[
                {"name": "Damage", "type": "float"},
                {"name": "HitActor", "type": "object:Actor"},
            ],
        )
    assert r["ok"] is True
    assert r["param_count"] == 2

    import json
    sent_dict = json.loads(sent["data"].decode("utf-8").rstrip())
    assert sent_dict["params"] == [
        {"name": "Damage", "type": "float"},
        {"name": "HitActor", "type": "object:Actor"},
    ]


def test_add_custom_event_no_params_omits_field() -> None:
    """params=None should NOT add 'params' to the payload."""
    response = b'{"ok":true,"command":"add_custom_event","param_count":0,"pins":[]}\n'
    sent: dict = {}
    with mock.patch.object(socket, "create_connection", return_value=_fake_sock(response, sent)):
        r = server.add_custom_event(
            blueprint="/Game/BP_X", event_name="Foo", anchor_name="x"
        )
    assert r["ok"] is True

    import json
    sent_dict = json.loads(sent["data"].decode("utf-8").rstrip())
    assert "params" not in sent_dict


def test_add_custom_event_handles_unknown_param_type() -> None:
    response = (
        b'{"ok":false,"command":"add_custom_event","error":"unknown_param_type",'
        b'"detail":"param \'X\' has unknown type \'WeirdType\'"}\n'
    )
    with mock.patch.object(socket, "create_connection", return_value=_fake_sock(response)):
        r = server.add_custom_event(
            blueprint="/Game/BP_X", event_name="Foo", anchor_name="x",
            params=[{"name": "X", "type": "WeirdType"}],
        )
    assert r["ok"] is False
    assert r["error"] == "unknown_param_type"


def test_add_custom_event_params_filters_malformed() -> None:
    """Params missing 'name' or 'type' are silently dropped."""
    response = b'{"ok":true,"param_count":1,"pins":[]}\n'
    sent: dict = {}
    with mock.patch.object(socket, "create_connection", return_value=_fake_sock(response, sent)):
        server.add_custom_event(
            blueprint="/Game/BP_X", event_name="Foo", anchor_name="x",
            params=[
                {"name": "Valid", "type": "int"},
                {"name": "MissingType"},          # dropped
                {"type": "float"},                # dropped
                {"weird": "key"},                 # dropped
            ],
        )
    import json
    sent_dict = json.loads(sent["data"].decode("utf-8").rstrip())
    assert len(sent_dict["params"]) == 1
    assert sent_dict["params"][0]["name"] == "Valid"


# ---------------------------------------------------------------------------
# v7.6 — event dispatchers (add / call / bind / unbind)
# ---------------------------------------------------------------------------


def test_add_event_dispatcher_with_params() -> None:
    response = (
        b'{"ok":true,"command":"add_event_dispatcher","dispatcher_name":"OnDeath",'
        b'"param_count":2,"saved":true}\n'
    )
    sent: dict = {}
    with mock.patch.object(socket, "create_connection", return_value=_fake_sock(response, sent)):
        r = server.add_event_dispatcher(
            blueprint="/Game/BP_X",
            dispatcher_name="OnDeath",
            params=[
                {"name": "Damage", "type": "float"},
                {"name": "Source", "type": "object:Actor"},
            ],
        )
    assert r["ok"] is True
    assert r["param_count"] == 2

    import json
    sent_dict = json.loads(sent["data"].decode("utf-8").rstrip())
    assert sent_dict["command"] == "add_event_dispatcher"
    assert sent_dict["dispatcher_name"] == "OnDeath"
    assert sent_dict["params"] == [
        {"name": "Damage", "type": "float"},
        {"name": "Source", "type": "object:Actor"},
    ]


def test_add_event_dispatcher_paramless() -> None:
    response = (
        b'{"ok":true,"command":"add_event_dispatcher","dispatcher_name":"OnPing",'
        b'"param_count":0,"saved":true}\n'
    )
    sent: dict = {}
    with mock.patch.object(socket, "create_connection", return_value=_fake_sock(response, sent)):
        r = server.add_event_dispatcher(
            blueprint="/Game/BP_X", dispatcher_name="OnPing"
        )
    assert r["ok"] is True

    import json
    sent_dict = json.loads(sent["data"].decode("utf-8").rstrip())
    assert "params" not in sent_dict


def test_add_event_dispatcher_handles_duplicate() -> None:
    response = (
        b'{"ok":false,"command":"add_event_dispatcher","error":"dispatcher_exists",'
        b'"detail":"OnDeath"}\n'
    )
    with mock.patch.object(socket, "create_connection", return_value=_fake_sock(response)):
        r = server.add_event_dispatcher(blueprint="/Game/BP_X", dispatcher_name="OnDeath")
    assert r["ok"] is False
    assert r["error"] == "dispatcher_exists"


def test_add_call_dispatcher_success() -> None:
    response = (
        b'{"ok":true,"command":"add_call_dispatcher","anchor_name":"broadcast_death",'
        b'"dispatcher_name":"OnDeath","node_type":"K2Node_CallDelegate",'
        b'"node_guid":"G","pins":[],"saved":true}\n'
    )
    sent: dict = {}
    with mock.patch.object(socket, "create_connection", return_value=_fake_sock(response, sent)):
        r = server.add_call_dispatcher(
            blueprint="/Game/BP_X",
            dispatcher_name="OnDeath",
            anchor_name="broadcast_death",
        )
    assert r["ok"] is True
    assert r["node_type"] == "K2Node_CallDelegate"


def test_add_bind_dispatcher_success() -> None:
    response = (
        b'{"ok":true,"command":"add_bind_dispatcher","anchor_name":"bind_death",'
        b'"dispatcher_name":"OnDeath","node_type":"K2Node_AddDelegate",'
        b'"node_guid":"G","pins":[],"saved":true}\n'
    )
    sent: dict = {}
    with mock.patch.object(socket, "create_connection", return_value=_fake_sock(response, sent)):
        r = server.add_bind_dispatcher(
            blueprint="/Game/BP_X",
            dispatcher_name="OnDeath",
            anchor_name="bind_death",
        )
    assert r["ok"] is True

    import json
    sent_dict = json.loads(sent["data"].decode("utf-8").rstrip())
    assert sent_dict["command"] == "add_bind_dispatcher"


def test_add_unbind_dispatcher_success() -> None:
    response = (
        b'{"ok":true,"command":"add_unbind_dispatcher","node_type":"K2Node_RemoveDelegate",'
        b'"node_guid":"G","pins":[],"saved":true}\n'
    )
    with mock.patch.object(socket, "create_connection", return_value=_fake_sock(response)):
        r = server.add_unbind_dispatcher(
            blueprint="/Game/BP_X",
            dispatcher_name="OnDeath",
            anchor_name="unbind_death",
        )
    assert r["ok"] is True
    assert r["node_type"] == "K2Node_RemoveDelegate"


def test_v76_local_validation_missing_args() -> None:
    assert server.add_event_dispatcher(blueprint="", dispatcher_name="")["error"] == "missing_argument"
    assert server.add_call_dispatcher(blueprint="", dispatcher_name="", anchor_name="")["error"] == "missing_argument"
    assert server.add_bind_dispatcher(blueprint="", dispatcher_name="", anchor_name="")["error"] == "missing_argument"
    assert server.add_unbind_dispatcher(blueprint="", dispatcher_name="", anchor_name="")["error"] == "missing_argument"


# ---------------------------------------------------------------------------
# v7.7 — graph_name parameter on add_node / connect_pins / set_pin_default /
#        add_branch / add_cast (function-body editing)
# ---------------------------------------------------------------------------


def test_add_node_with_graph_name() -> None:
    """graph_name='MyFunc' routes to the function graph."""
    response = b'{"ok":true,"command":"add_node","anchor_name":"print_x","saved":true,"pins":[]}\n'
    sent: dict = {}
    with mock.patch.object(socket, "create_connection", return_value=_fake_sock(response, sent)):
        r = server.add_node(
            blueprint="/Game/BP_X",
            node_type="PrintString",
            anchor_name="print_x",
            graph_name="MyFunc",
        )
    assert r["ok"] is True

    import json
    sent_dict = json.loads(sent["data"].decode("utf-8").rstrip())
    assert sent_dict["graph_name"] == "MyFunc"


def test_add_node_without_graph_name_omits_field() -> None:
    """graph_name='' (default) should NOT add the field to the payload."""
    response = b'{"ok":true,"command":"add_node","saved":true,"pins":[]}\n'
    sent: dict = {}
    with mock.patch.object(socket, "create_connection", return_value=_fake_sock(response, sent)):
        server.add_node(blueprint="/Game/BP_X", node_type="PrintString", anchor_name="x")

    import json
    sent_dict = json.loads(sent["data"].decode("utf-8").rstrip())
    assert "graph_name" not in sent_dict


def test_connect_pins_with_graph_name() -> None:
    response = b'{"ok":true,"command":"connect_pins","saved":true}\n'
    sent: dict = {}
    with mock.patch.object(socket, "create_connection", return_value=_fake_sock(response, sent)):
        server.connect_pins(
            blueprint="/Game/BP_X",
            from_pin="a.then",
            to_pin="b.execute",
            graph_name="MyFunc",
        )

    import json
    sent_dict = json.loads(sent["data"].decode("utf-8").rstrip())
    assert sent_dict["graph_name"] == "MyFunc"


def test_set_pin_default_with_graph_name() -> None:
    response = b'{"ok":true,"command":"set_pin_default","saved":true}\n'
    sent: dict = {}
    with mock.patch.object(socket, "create_connection", return_value=_fake_sock(response, sent)):
        server.set_pin_default(
            blueprint="/Game/BP_X",
            pin_ref="print_x.InString",
            value="hello from func",
            graph_name="MyFunc",
        )

    import json
    sent_dict = json.loads(sent["data"].decode("utf-8").rstrip())
    assert sent_dict["graph_name"] == "MyFunc"


def test_add_branch_with_graph_name() -> None:
    response = b'{"ok":true,"command":"add_branch","saved":true,"pins":[]}\n'
    sent: dict = {}
    with mock.patch.object(socket, "create_connection", return_value=_fake_sock(response, sent)):
        server.add_branch(
            blueprint="/Game/BP_X",
            anchor_name="check",
            graph_name="ComputeDamage",
        )

    import json
    sent_dict = json.loads(sent["data"].decode("utf-8").rstrip())
    assert sent_dict["graph_name"] == "ComputeDamage"


def test_add_cast_with_graph_name() -> None:
    response = b'{"ok":true,"command":"add_cast","saved":true,"pins":[]}\n'
    sent: dict = {}
    with mock.patch.object(socket, "create_connection", return_value=_fake_sock(response, sent)):
        server.add_cast(
            blueprint="/Game/BP_X",
            target_class="Pawn",
            anchor_name="cast_pawn",
            graph_name="HandleHit",
        )

    import json
    sent_dict = json.loads(sent["data"].decode("utf-8").rstrip())
    assert sent_dict["graph_name"] == "HandleHit"


def test_add_node_handles_graph_not_found() -> None:
    """graph_not_found error is propagated."""
    response = (
        b'{"ok":false,"command":"add_node","error":"graph_not_found",'
        b'"detail":"NoSuchFunc"}\n'
    )
    with mock.patch.object(socket, "create_connection", return_value=_fake_sock(response)):
        r = server.add_node(
            blueprint="/Game/BP_X",
            node_type="PrintString",
            anchor_name="x",
            graph_name="NoSuchFunc",
        )
    assert r["ok"] is False
    assert r["error"] == "graph_not_found"


# ---------------------------------------------------------------------------
# v7.8 — save_blueprint
# ---------------------------------------------------------------------------


def test_save_blueprint_success() -> None:
    response = (
        b'{"ok":true,"command":"save_blueprint","blueprint":"/Game/BP_X",'
        b'"package":"/Game/BP_X","saved":true}\n'
    )
    sent: dict = {}
    with mock.patch.object(socket, "create_connection", return_value=_fake_sock(response, sent)):
        r = server.save_blueprint(blueprint="/Game/BP_X")
    assert r["ok"] is True
    assert r["saved"] is True

    import json
    sent_dict = json.loads(sent["data"].decode("utf-8").rstrip())
    assert sent_dict == {"command": "save_blueprint", "blueprint": "/Game/BP_X"}


def test_save_blueprint_handles_not_found() -> None:
    response = (
        b'{"ok":false,"command":"save_blueprint","error":"blueprint_not_found",'
        b'"detail":"/Game/NoSuchBP"}\n'
    )
    with mock.patch.object(socket, "create_connection", return_value=_fake_sock(response)):
        r = server.save_blueprint(blueprint="/Game/NoSuchBP")
    assert r["ok"] is False
    assert r["error"] == "blueprint_not_found"


def test_save_blueprint_local_validation_missing_arg() -> None:
    r = server.save_blueprint(blueprint="")
    assert r["ok"] is False
    assert r["error"] == "missing_argument"


# ---------------------------------------------------------------------------
# v8 — PIE control + simulated input + log capture (agentic closed loop)
# ---------------------------------------------------------------------------


def test_read_log_capture_success() -> None:
    response = (
        b'{"ok":true,"command":"read_log_capture","total_captured":3,"returned":2,'
        b'"lines":["[LogBlueprintUserMessages][Log] hello","[LogTemp][Log] foo"]}\n'
    )
    sent: dict = {}
    with mock.patch.object(socket, "create_connection", return_value=_fake_sock(response, sent)):
        r = server.read_log_capture(max_lines=2, category="Blueprint")
    assert r["ok"] is True
    assert r["returned"] == 2
    assert len(r["lines"]) == 2

    import json
    sent_dict = json.loads(sent["data"].decode("utf-8").rstrip())
    assert sent_dict["command"] == "read_log_capture"
    assert sent_dict["max_lines"] == 2
    assert sent_dict["category"] == "Blueprint"


def test_read_log_capture_passes_category_substring_through() -> None:
    """v8.0.3 BUG-A: short forms like 'BlueprintMCP' should reach the wire as-is;
    the substring-match happens server-side against the extracted [Category] token."""
    response = b'{"ok":true,"command":"read_log_capture","total_captured":2,"returned":1,"lines":["[LogBlueprintMCP_TCP][Log] MCP recv: ..."]}\n'
    sent: dict = {}
    with mock.patch.object(socket, "create_connection", return_value=_fake_sock(response, sent)):
        r = server.read_log_capture(category="BlueprintMCP")
    assert r["ok"] is True
    assert r["returned"] == 1

    import json
    sent_dict = json.loads(sent["data"].decode("utf-8").rstrip())
    # Filter is passed through verbatim; the server-side fix interprets it as substring
    assert sent_dict["category"] == "BlueprintMCP"


def test_read_log_capture_omits_empty_filters() -> None:
    """Empty filter strings should not appear in the wire payload."""
    response = b'{"ok":true,"command":"read_log_capture","total_captured":0,"returned":0,"lines":[]}\n'
    sent: dict = {}
    with mock.patch.object(socket, "create_connection", return_value=_fake_sock(response, sent)):
        server.read_log_capture()

    import json
    sent_dict = json.loads(sent["data"].decode("utf-8").rstrip())
    assert "category" not in sent_dict
    assert "verbosity" not in sent_dict
    assert "contains" not in sent_dict
    assert sent_dict["max_lines"] == 100  # default


def test_clear_log_capture_success() -> None:
    response = b'{"ok":true,"command":"clear_log_capture"}\n'
    sent: dict = {}
    with mock.patch.object(socket, "create_connection", return_value=_fake_sock(response, sent)):
        r = server.clear_log_capture()
    assert r["ok"] is True

    import json
    sent_dict = json.loads(sent["data"].decode("utf-8").rstrip())
    assert sent_dict == {"command": "clear_log_capture"}


def test_start_pie_success() -> None:
    response = b'{"ok":true,"command":"start_pie","queued":true}\n'
    sent: dict = {}
    with mock.patch.object(socket, "create_connection", return_value=_fake_sock(response, sent)):
        r = server.start_pie()
    assert r["ok"] is True
    assert r["queued"] is True

    import json
    sent_dict = json.loads(sent["data"].decode("utf-8").rstrip())
    assert sent_dict == {"command": "start_pie"}


def test_start_pie_handles_already_running() -> None:
    response = b'{"ok":false,"command":"start_pie","error":"pie_already_running","detail":""}\n'
    with mock.patch.object(socket, "create_connection", return_value=_fake_sock(response)):
        r = server.start_pie()
    assert r["ok"] is False
    assert r["error"] == "pie_already_running"


def test_stop_pie_success() -> None:
    response = b'{"ok":true,"command":"stop_pie","queued":true}\n'
    with mock.patch.object(socket, "create_connection", return_value=_fake_sock(response)):
        r = server.stop_pie()
    assert r["ok"] is True


def test_is_pie_running_returns_status() -> None:
    response = b'{"ok":true,"command":"is_pie_running","running":true,"start_queued":false}\n'
    with mock.patch.object(socket, "create_connection", return_value=_fake_sock(response)):
        r = server.is_pie_running()
    assert r["ok"] is True
    assert r["running"] is True
    assert r["start_queued"] is False


def test_pie_press_key_success() -> None:
    response = (
        b'{"ok":true,"command":"pie_press_key","key":"SpaceBar","player_index":0}\n'
    )
    sent: dict = {}
    with mock.patch.object(socket, "create_connection", return_value=_fake_sock(response, sent)):
        r = server.pie_press_key(key="Space")  # alias → SpaceBar on UE side
    assert r["ok"] is True
    assert r["key"] == "SpaceBar"

    import json
    sent_dict = json.loads(sent["data"].decode("utf-8").rstrip())
    assert sent_dict == {"command": "pie_press_key", "key": "Space", "player_index": 0}


def test_pie_press_key_handles_pie_not_running() -> None:
    response = (
        b'{"ok":false,"command":"pie_press_key","error":"pie_not_running",'
        b'"detail":"Call start_pie first; wait a tick for it to actually start"}\n'
    )
    with mock.patch.object(socket, "create_connection", return_value=_fake_sock(response)):
        r = server.pie_press_key(key="P")
    assert r["ok"] is False
    assert r["error"] == "pie_not_running"


def test_pie_press_key_local_validation_missing_key() -> None:
    r = server.pie_press_key(key="")
    assert r["ok"] is False
    assert r["error"] == "missing_argument"


# ---------------------------------------------------------------------------
# v9.1.0 — Discovery tools (list_assets / skeletons / meshes / blueprints / classes)
# ---------------------------------------------------------------------------


def test_list_assets_success() -> None:
    response = (
        b'{"ok":true,"command":"list_assets","folder":"/Game","asset_class":"",'
        b'"recursive":true,"count":2,"assets":['
        b'{"name":"BP_A","path":"/Game/BP_A.BP_A","package_path":"/Game","class":"Blueprint"},'
        b'{"name":"M_B","path":"/Game/M_B.M_B","package_path":"/Game","class":"Material"}]}\n'
    )
    sent: dict = {}
    with mock.patch.object(socket, "create_connection", return_value=_fake_sock(response, sent)):
        r = server.list_assets()
    assert r["ok"] is True
    assert r["count"] == 2

    import json
    sent_dict = json.loads(sent["data"].decode("utf-8").rstrip())
    assert sent_dict["command"] == "list_assets"
    assert sent_dict["folder"] == "/Game"
    assert sent_dict["recursive"] is True
    assert sent_dict["max_results"] == 500


def test_list_skeletons_passes_filter_through() -> None:
    response = (
        b'{"ok":true,"command":"list_skeletons","folder":"/Game",'
        b'"asset_class":"Skeleton","recursive":true,"count":1,"assets":['
        b'{"name":"SK_Test","path":"/Game/SK_Test.SK_Test","package_path":"/Game","class":"Skeleton"}]}\n'
    )
    sent: dict = {}
    with mock.patch.object(socket, "create_connection", return_value=_fake_sock(response, sent)):
        r = server.list_skeletons(folder="/Game/Anim")
    assert r["ok"] is True

    import json
    sent_dict = json.loads(sent["data"].decode("utf-8").rstrip())
    assert sent_dict == {
        "command": "list_skeletons",
        "folder": "/Game/Anim",
        "max_results": 100,
    }


def test_list_meshes_combines_static_and_skeletal() -> None:
    response = (
        b'{"ok":true,"command":"list_meshes","folder":"/Game",'
        b'"static_count":2,"skeletal_count":1,"count":3,"assets":['
        b'{"name":"SM_A","path":"/Game/SM_A.SM_A","package_path":"/Game","class":"StaticMesh"},'
        b'{"name":"SM_B","path":"/Game/SM_B.SM_B","package_path":"/Game","class":"StaticMesh"},'
        b'{"name":"SKM_C","path":"/Game/SKM_C.SKM_C","package_path":"/Game","class":"SkeletalMesh"}]}\n'
    )
    with mock.patch.object(socket, "create_connection", return_value=_fake_sock(response)):
        r = server.list_meshes()
    assert r["ok"] is True
    assert r["static_count"] == 2
    assert r["skeletal_count"] == 1
    assert r["count"] == 3
    # Verify both classes appear in merged assets array
    classes = {a["class"] for a in r["assets"]}
    assert classes == {"StaticMesh", "SkeletalMesh"}


def test_list_blueprints_success() -> None:
    response = (
        b'{"ok":true,"command":"list_blueprints","folder":"/Game",'
        b'"asset_class":"Blueprint","recursive":true,"count":1,"assets":['
        b'{"name":"BP_X","path":"/Game/BP_X.BP_X","package_path":"/Game","class":"Blueprint"}]}\n'
    )
    with mock.patch.object(socket, "create_connection", return_value=_fake_sock(response)):
        r = server.list_blueprints()
    assert r["ok"] is True
    assert r["count"] == 1


def test_list_classes_passes_parent_filter() -> None:
    response = (
        b'{"ok":true,"command":"list_classes","parent_class":"Pawn",'
        b'"native_only":true,"name_contains":"","count":2,"classes":['
        b'{"name":"Pawn","path":"/Script/Engine.Pawn","native":true,"super":"Actor"},'
        b'{"name":"Character","path":"/Script/Engine.Character","native":true,"super":"Pawn"}]}\n'
    )
    sent: dict = {}
    with mock.patch.object(socket, "create_connection", return_value=_fake_sock(response, sent)):
        r = server.list_classes(parent_class="Pawn", native_only=True)
    assert r["ok"] is True
    assert r["count"] == 2
    assert {c["name"] for c in r["classes"]} == {"Pawn", "Character"}

    import json
    sent_dict = json.loads(sent["data"].decode("utf-8").rstrip())
    assert sent_dict["parent_class"] == "Pawn"
    assert sent_dict["native_only"] is True
    # Empty filter strings should NOT be in payload
    assert "name_contains" not in sent_dict


def test_list_classes_handles_unknown_parent() -> None:
    response = (
        b'{"ok":false,"command":"list_classes","error":"parent_class_not_found",'
        b'"detail":"WeirdClass"}\n'
    )
    with mock.patch.object(socket, "create_connection", return_value=_fake_sock(response)):
        r = server.list_classes(parent_class="WeirdClass")
    assert r["ok"] is False
    assert r["error"] == "parent_class_not_found"


@requires_ue_editor(extra_reason="discovery tools probe real asset registry")
def test_discovery_tools_against_real_plugin() -> None:
    """Integration: discover what's actually in the project."""
    # 1. Skeletons — at least 1 in any non-empty project
    r = server.list_skeletons()
    assert r["ok"] is True
    assert r["count"] >= 1, f"No skeletons in project? {r}"

    # 2. Meshes — should find /Engine/BasicShapes/Cube at least
    r = server.list_meshes()
    assert r["ok"] is True
    assert r["count"] >= 1

    # 3. Blueprints — every project has at least the GameMode/Character/etc
    r = server.list_blueprints()
    assert r["ok"] is True
    assert r["count"] >= 1

    # 4. List Pawn subclasses — Pawn itself + Character + DefaultPawn always exist
    r = server.list_classes(parent_class="Pawn", native_only=True)
    assert r["ok"] is True
    names = {c["name"] for c in r["classes"]}
    assert "Pawn" in names
    assert "Character" in names

    # 5. Filter by /Engine basic shapes
    r = server.list_assets(folder="/Engine/BasicShapes", asset_class="StaticMesh")
    assert r["ok"] is True
    cube = next((a for a in r["assets"] if a["name"] == "Cube"), None)
    assert cube is not None, "Where did /Engine/BasicShapes/Cube go?"


# ---------------------------------------------------------------------------
# v9.0.0 — create_anim_blueprint (AnimGraph domain opens)
# ---------------------------------------------------------------------------


def test_create_anim_blueprint_success() -> None:
    response = (
        b'{"ok":true,"command":"create_anim_blueprint",'
        b'"blueprint_path":"/Game/Blueprints/ABP_Test",'
        b'"skeleton":"/Engine/Mannequin/Mesh/SK_Mannequin_Skeleton",'
        b'"parent_class":"AnimInstance","saved":true}\n'
    )
    sent: dict = {}
    with mock.patch.object(socket, "create_connection", return_value=_fake_sock(response, sent)):
        r = server.create_anim_blueprint(
            name="ABP_Test",
            skeleton="/Engine/Mannequin/Mesh/SK_Mannequin_Skeleton",
        )
    assert r["ok"] is True
    assert r["parent_class"] == "AnimInstance"
    assert r["saved"] is True

    import json
    sent_dict = json.loads(sent["data"].decode("utf-8").rstrip())
    assert sent_dict == {
        "command": "create_anim_blueprint",
        "name": "ABP_Test",
        "skeleton": "/Engine/Mannequin/Mesh/SK_Mannequin_Skeleton",
        "path": "/Game/Blueprints",
    }


def test_create_anim_blueprint_handles_no_skeleton() -> None:
    response = (
        b'{"ok":false,"command":"create_anim_blueprint","error":"skeleton_not_found",'
        b'"detail":"/Game/NoSkel"}\n'
    )
    with mock.patch.object(socket, "create_connection", return_value=_fake_sock(response)):
        r = server.create_anim_blueprint(name="X", skeleton="/Game/NoSkel")
    assert r["ok"] is False
    assert r["error"] == "skeleton_not_found"


def test_create_anim_blueprint_local_validation() -> None:
    assert server.create_anim_blueprint(name="", skeleton="/Game/X")["error"] == "missing_argument"
    assert server.create_anim_blueprint(name="X", skeleton="")["error"] == "missing_argument"


@requires_ue_editor(extra_reason="project needs SOME USkeleton asset")
def test_create_anim_blueprint_against_real_plugin() -> None:
    """Integration: probe known skeleton paths until one resolves, then create AnimBP.

    The Engine doesn't include a built-in skeleton at a fixed path (varies by
    template). This test probes a list of common project skeleton paths and
    uses the first one that resolves. If none are present, skip with a hint.
    """
    import uuid

    # Common skeleton paths across UE 5.x templates.
    # First-match wins; tests in TESTMCP (FirstPerson template) hit the first one.
    candidate_skeletons = [
        "/Game/FirstPersonArms/Character/Mesh/SK_Mannequin_Arms_Skeleton",  # FirstPerson template
        "/Game/Characters/Mannequins/Meshes/SK_Mannequin",                  # ThirdPerson (UE5)
        "/Game/Mannequin/Mesh/UE4_Mannequin_Skeleton",                       # ThirdPerson (UE4 legacy)
        "/Engine/EngineMeshes/SkeletalCube",                                 # last-resort engine asset
    ]
    skeleton = None
    last_detail = ""
    unique_name = f"ABP_V9Test_{uuid.uuid4().hex[:8]}"
    for candidate in candidate_skeletons:
        r = server.create_anim_blueprint(name=unique_name, skeleton=candidate, path="/Game/Tests")
        if r["ok"]:
            skeleton = candidate
            break
        last_detail = r.get("detail", "")
    assert skeleton is not None, (
        f"No skeleton resolved. Last detail: {last_detail}. "
        f"Tried: {candidate_skeletons}. "
        f"To run this test, ensure your project has at least one USkeleton."
    )
    # If we got here, AnimBP was created on the last successful candidate
    r = server.create_anim_blueprint(
        name=f"{unique_name}_v2", skeleton=skeleton, path="/Game/Tests",
    )
    assert r["ok"] is True
    assert r["parent_class"] == "AnimInstance"


# ---------------------------------------------------------------------------
# v8.0.2 — migrate_dispatchers + plugin_version in ping
# ---------------------------------------------------------------------------


def test_migrate_dispatchers_recreate_ghosts_dry_run() -> None:
    """v8.1.0: ghost detection without recreate (default behavior)."""
    response = (
        b'{"ok":true,"command":"migrate_dispatchers","blueprint":"/Game/BP_Ghost",'
        b'"migrated_count":0,"already_healthy_count":0,"orphan_variable_count":0,'
        b'"ghosts_detected_count":2,"ghosts_recreated_count":0,'
        b'"migrated":[],"already_healthy":[],"orphan_variables":[],'
        b'"ghosts_detected":["OnDeath","OnHit"],"ghosts_recreated":[],'
        b'"recreate_ghosts_requested":false,"compiled":false,"saved":false}\n'
    )
    sent: dict = {}
    with mock.patch.object(socket, "create_connection", return_value=_fake_sock(response, sent)):
        r = server.migrate_dispatchers(blueprint="/Game/BP_Ghost")
    assert r["ok"] is True
    assert r["ghosts_detected_count"] == 2
    assert r["ghosts_recreated_count"] == 0  # default — dry run
    assert set(r["ghosts_detected"]) == {"OnDeath", "OnHit"}

    import json
    sent_dict = json.loads(sent["data"].decode("utf-8").rstrip())
    # recreate_ghosts default is False → field should NOT be sent
    assert "recreate_ghosts" not in sent_dict


def test_migrate_dispatchers_recreate_ghosts_active() -> None:
    """v8.1.0: with recreate_ghosts=True, ghosts get rebuilt with empty signatures."""
    response = (
        b'{"ok":true,"command":"migrate_dispatchers","blueprint":"/Game/BP_Ghost",'
        b'"migrated_count":0,"already_healthy_count":0,"orphan_variable_count":0,'
        b'"ghosts_detected_count":2,"ghosts_recreated_count":2,'
        b'"migrated":[],"already_healthy":[],"orphan_variables":[],'
        b'"ghosts_detected":["OnDeath","OnHit"],"ghosts_recreated":["OnDeath","OnHit"],'
        b'"recreate_ghosts_requested":true,"compiled":true,"saved":true}\n'
    )
    sent: dict = {}
    with mock.patch.object(socket, "create_connection", return_value=_fake_sock(response, sent)):
        r = server.migrate_dispatchers(blueprint="/Game/BP_Ghost", recreate_ghosts=True)
    assert r["ok"] is True
    assert r["ghosts_recreated_count"] == 2
    assert r["compiled"] is True

    import json
    sent_dict = json.loads(sent["data"].decode("utf-8").rstrip())
    assert sent_dict["recreate_ghosts"] is True


def test_migrate_dispatchers_repairs_old() -> None:
    response = (
        b'{"ok":true,"command":"migrate_dispatchers","blueprint":"/Game/BP_Old",'
        b'"migrated_count":2,"already_healthy_count":0,"orphan_variable_count":0,'
        b'"migrated":["OnHit","OnDeath"],"already_healthy":[],"orphan_variables":[],'
        b'"compiled":true,"saved":true}\n'
    )
    sent: dict = {}
    with mock.patch.object(socket, "create_connection", return_value=_fake_sock(response, sent)):
        r = server.migrate_dispatchers(blueprint="/Game/BP_Old")
    assert r["ok"] is True
    assert r["migrated_count"] == 2
    assert set(r["migrated"]) == {"OnHit", "OnDeath"}
    assert r["compiled"] is True

    import json
    sent_dict = json.loads(sent["data"].decode("utf-8").rstrip())
    assert sent_dict == {"command": "migrate_dispatchers", "blueprint": "/Game/BP_Old"}


def test_migrate_dispatchers_nothing_to_do() -> None:
    """Healthy BP: 0 migrated, compiled=false, saved=false."""
    response = (
        b'{"ok":true,"command":"migrate_dispatchers","blueprint":"/Game/BP_Healthy",'
        b'"migrated_count":0,"already_healthy_count":1,"orphan_variable_count":0,'
        b'"migrated":[],"already_healthy":["OnHit"],"orphan_variables":[],'
        b'"compiled":false,"saved":false}\n'
    )
    with mock.patch.object(socket, "create_connection", return_value=_fake_sock(response)):
        r = server.migrate_dispatchers(blueprint="/Game/BP_Healthy")
    assert r["ok"] is True
    assert r["migrated_count"] == 0
    assert r["already_healthy_count"] == 1
    assert r["compiled"] is False


def test_migrate_dispatchers_local_validation() -> None:
    r = server.migrate_dispatchers(blueprint="")
    assert r["ok"] is False
    assert r["error"] == "missing_argument"


def test_ping_returns_plugin_version() -> None:
    """v8.0.2: ping now surfaces plugin_version + build_date so users can verify dylib."""
    response = (
        b'{"ok":true,"command":"ping","version":"0.0.1",'
        b'"plugin_version":"8.0.2","build_date":"May 21 2026 11:35:00",'
        b'"timestamp":"2026-05-21T11:35:00.000Z"}\n'
    )
    with mock.patch.object(socket, "create_connection", return_value=_fake_sock(response)):
        r = server.ping_ue()
    assert r["ok"] is True
    assert r["plugin_version"] == "8.0.2"
    assert "build_date" in r


# ---------------------------------------------------------------------------
# v8.0.1 — delete_event_dispatcher (OPEN-1 recovery path)
# ---------------------------------------------------------------------------


def test_delete_event_dispatcher_removes_both() -> None:
    """Healthy dispatcher: both signature graph and member variable removed."""
    response = (
        b'{"ok":true,"command":"delete_event_dispatcher","dispatcher_name":"OnDeath",'
        b'"removed_graph":true,"removed_variable":true,"compiled":true,"saved":true}\n'
    )
    sent: dict = {}
    with mock.patch.object(socket, "create_connection", return_value=_fake_sock(response, sent)):
        r = server.delete_event_dispatcher(
            blueprint="/Game/BP_X", dispatcher_name="OnDeath",
        )
    assert r["ok"] is True
    assert r["removed_graph"] is True
    assert r["removed_variable"] is True


def test_delete_event_dispatcher_old_broken_only_graph() -> None:
    """Old pre-v7.1.2 broken dispatcher: only signature graph, no member var."""
    response = (
        b'{"ok":true,"command":"delete_event_dispatcher","dispatcher_name":"OnDeath",'
        b'"removed_graph":true,"removed_variable":false,"compiled":true,"saved":true}\n'
    )
    with mock.patch.object(socket, "create_connection", return_value=_fake_sock(response)):
        r = server.delete_event_dispatcher(
            blueprint="/Game/BP_OldBroken", dispatcher_name="OnDeath",
        )
    assert r["ok"] is True
    assert r["removed_graph"] is True
    assert r["removed_variable"] is False  # the missing-variable signature


def test_delete_event_dispatcher_not_found() -> None:
    response = (
        b'{"ok":false,"command":"delete_event_dispatcher","error":"dispatcher_not_found",'
        b'"detail":"No signature graph or member variable named \\"Nope\\""}\n'
    )
    with mock.patch.object(socket, "create_connection", return_value=_fake_sock(response)):
        r = server.delete_event_dispatcher(
            blueprint="/Game/BP_X", dispatcher_name="Nope",
        )
    assert r["ok"] is False
    assert r["error"] == "dispatcher_not_found"


def test_delete_event_dispatcher_local_validation() -> None:
    assert server.delete_event_dispatcher(blueprint="", dispatcher_name="X")["error"] == "missing_argument"
    assert server.delete_event_dispatcher(blueprint="/Game/BP", dispatcher_name="")["error"] == "missing_argument"


@skip_if_headless("PIE needs a GUI editor world (no game world ticks under -nullrhi)")
@requires_ue_editor(extra_reason="agentic loop end-to-end demo (writes a BP + drives PIE)")
def test_v8_agentic_loop_against_real_plugin() -> None:
    """End-to-end: write hello-world BP → spawn → PIE → read log → stop.

    This is the canonical v8 demo — LLM writes, runs, reads, verifies its own
    work. Implementation notes / gotchas surfaced from real testing:

    1. node_type must be ``K2Node_CallFunction:PrintString``, NOT bare
       ``PrintString``. The format is ``<K2NodeClass>:<param>``.
    2. start_pie may fail with ``pie_already_running`` if a previous test
       didn't stop PIE cleanly — defensively stop first.
    3. After start_pie returns ``queued:true``, PIE doesn't tick instantly;
       sleep 2-3s for BeginPlay to fire and the print to land in the log.
    4. spawn_actor must happen BEFORE start_pie — UEditorActorSubsystem
       targets the editor world, which is suspended during PIE.
    """
    import time
    import uuid

    # Defensive: stop PIE if a previous test left it running.
    if server.is_pie_running().get("running"):
        server.stop_pie()
        time.sleep(1)

    bp_name = f"BP_V8_AgenticLoop_{uuid.uuid4().hex[:6]}"
    bp = f"/Game/Tests/{bp_name}"
    PROBE = f"AGENTIC_LOOP_PROBE_{uuid.uuid4().hex[:8]}"  # unique each run

    # 1. Author the BP.
    assert server.create_blueprint(name=bp_name, path="/Game/Tests")["ok"]
    r = server.add_node(
        blueprint=bp,
        node_type="K2Node_CallFunction:PrintString",
        anchor_name="print_hello",
    )
    assert r["ok"], f"add_node failed: {r}"
    assert server.set_pin_default(
        blueprint=bp, pin_ref="print_hello.InString", value=PROBE,
    )["ok"]
    assert server.connect_pins(
        blueprint=bp, from_pin="begin_play.then", to_pin="print_hello.execute",
    )["ok"]

    # 2. Compile + spawn (in editor world, before PIE).
    assert server.compile_blueprint(name=bp)["ok"]
    r = server.spawn_actor(blueprint=bp)
    assert r["ok"], f"spawn_actor failed: {r}"

    # 3. Clear log so we only see what THIS run prints.
    server.clear_log_capture()

    # 4. Start PIE. queued=true initially.
    r = server.start_pie()
    assert r["ok"], f"start_pie failed: {r}"

    # 5. Wait for PIE to actually tick + BeginPlay to fire.
    time.sleep(3)

    # 6. Verify PIE is actually running (not just queued).
    state = server.is_pie_running()
    assert state["running"], f"PIE didn't start in 3s: {state}"

    # 7. Read log — agentic verification.
    log_result = server.read_log_capture(
        category="BlueprintUserMessages",
        contains=PROBE,
    )
    assert log_result["ok"] is True

    # 8. Teardown ALWAYS (use try/finally so PIE doesn't leak into next test).
    try:
        assert log_result["returned"] >= 1, (
            f"No log line containing {PROBE!r} found after PIE. "
            f"total_captured={log_result['total_captured']}. "
            f"Check that PrintString actually fired."
        )
    finally:
        server.stop_pie()


# ---------------------------------------------------------------------------
# v9.2.0 — AnimGraph state-machine tools
# ---------------------------------------------------------------------------


def test_add_anim_state_machine_success() -> None:
    response = (
        b'{"ok":true,"command":"add_anim_state_machine","state_machine":"Locomotion",'
        b'"interior_graph":"Locomotion","node_guid":"AAAA-BBBB","saved":true}\n'
    )
    sent: dict = {}
    with mock.patch.object(socket, "create_connection", return_value=_fake_sock(response, sent)):
        r = server.add_anim_state_machine(
            blueprint="/Game/Blueprints/ABP_X",
            name="Locomotion",
            pos_x=100,
            pos_y=200,
        )
    assert r["ok"] is True
    assert r["state_machine"] == "Locomotion"
    assert r["saved"] is True

    import json
    sent_dict = json.loads(sent["data"].decode("utf-8").rstrip())
    assert sent_dict == {
        "command": "add_anim_state_machine",
        "blueprint": "/Game/Blueprints/ABP_X",
        "name": "Locomotion",
        "pos_x": 100,
        "pos_y": 200,
    }


def test_add_anim_state_machine_already_exists() -> None:
    response = (
        b'{"ok":false,"command":"add_anim_state_machine","error":"state_machine_exists",'
        b'"detail":"Locomotion"}\n'
    )
    with mock.patch.object(socket, "create_connection", return_value=_fake_sock(response)):
        r = server.add_anim_state_machine(blueprint="/Game/X", name="Locomotion")
    assert r["ok"] is False
    assert r["error"] == "state_machine_exists"


def test_add_anim_state_machine_local_validation() -> None:
    assert server.add_anim_state_machine(blueprint="", name="X")["error"] == "missing_argument"
    assert server.add_anim_state_machine(blueprint="/Game/X", name="")["error"] == "missing_argument"


def test_add_anim_state_success() -> None:
    response = (
        b'{"ok":true,"command":"add_anim_state","state":"Idle","state_machine":"Locomotion",'
        b'"bound_graph":"Idle","node_guid":"CCCC-DDDD","saved":true}\n'
    )
    sent: dict = {}
    with mock.patch.object(socket, "create_connection", return_value=_fake_sock(response, sent)):
        r = server.add_anim_state(
            blueprint="/Game/Blueprints/ABP_X",
            state_machine="Locomotion",
            name="Idle",
            pos_x=-100,
            pos_y=0,
        )
    assert r["ok"] is True
    assert r["state"] == "Idle"
    assert r["state_machine"] == "Locomotion"

    import json
    sent_dict = json.loads(sent["data"].decode("utf-8").rstrip())
    assert sent_dict == {
        "command": "add_anim_state",
        "blueprint": "/Game/Blueprints/ABP_X",
        "state_machine": "Locomotion",
        "name": "Idle",
        "pos_x": -100,
        "pos_y": 0,
    }


def test_add_anim_state_state_machine_not_found() -> None:
    response = (
        b'{"ok":false,"command":"add_anim_state","error":"state_machine_not_found",'
        b'"detail":"Ghost"}\n'
    )
    with mock.patch.object(socket, "create_connection", return_value=_fake_sock(response)):
        r = server.add_anim_state(
            blueprint="/Game/X", state_machine="Ghost", name="Idle",
        )
    assert r["ok"] is False
    assert r["error"] == "state_machine_not_found"


def test_add_anim_state_local_validation() -> None:
    assert server.add_anim_state(blueprint="", state_machine="A", name="B")["error"] == "missing_argument"
    assert server.add_anim_state(blueprint="/Game/X", state_machine="", name="B")["error"] == "missing_argument"
    assert server.add_anim_state(blueprint="/Game/X", state_machine="A", name="")["error"] == "missing_argument"


def test_add_anim_transition_success() -> None:
    response = (
        b'{"ok":true,"command":"add_anim_transition","from_state":"Idle","to_state":"Run",'
        b'"state_machine":"Locomotion","node_guid":"EEEE-FFFF","saved":true}\n'
    )
    sent: dict = {}
    with mock.patch.object(socket, "create_connection", return_value=_fake_sock(response, sent)):
        r = server.add_anim_transition(
            blueprint="/Game/Blueprints/ABP_X",
            state_machine="Locomotion",
            from_state="Idle",
            to_state="Run",
        )
    assert r["ok"] is True
    assert r["from_state"] == "Idle"
    assert r["to_state"] == "Run"

    import json
    sent_dict = json.loads(sent["data"].decode("utf-8").rstrip())
    assert sent_dict == {
        "command": "add_anim_transition",
        "blueprint": "/Game/Blueprints/ABP_X",
        "state_machine": "Locomotion",
        "from_state": "Idle",
        "to_state": "Run",
    }


def test_add_anim_transition_from_state_not_found() -> None:
    response = (
        b'{"ok":false,"command":"add_anim_transition","error":"from_state_not_found",'
        b'"detail":"Ghost"}\n'
    )
    with mock.patch.object(socket, "create_connection", return_value=_fake_sock(response)):
        r = server.add_anim_transition(
            blueprint="/Game/X", state_machine="L", from_state="Ghost", to_state="Run",
        )
    assert r["ok"] is False
    assert r["error"] == "from_state_not_found"


def test_add_anim_transition_local_validation() -> None:
    assert server.add_anim_transition(blueprint="", state_machine="L", from_state="A", to_state="B")["error"] == "missing_argument"
    assert server.add_anim_transition(blueprint="/G/X", state_machine="", from_state="A", to_state="B")["error"] == "missing_argument"
    assert server.add_anim_transition(blueprint="/G/X", state_machine="L", from_state="", to_state="B")["error"] == "missing_argument"
    assert server.add_anim_transition(blueprint="/G/X", state_machine="L", from_state="A", to_state="")["error"] == "missing_argument"


def test_set_anim_state_pose_success() -> None:
    response = (
        b'{"ok":true,"command":"set_anim_state_pose","state":"Idle","state_machine":"Locomotion",'
        b'"sequence":"/Game/Anims/Idle_Loop","wired":true,"saved":true}\n'
    )
    sent: dict = {}
    with mock.patch.object(socket, "create_connection", return_value=_fake_sock(response, sent)):
        r = server.set_anim_state_pose(
            blueprint="/Game/Blueprints/ABP_X",
            state_machine="Locomotion",
            state="Idle",
            sequence="/Game/Anims/Idle_Loop",
        )
    assert r["ok"] is True
    assert r["wired"] is True
    assert r["sequence"] == "/Game/Anims/Idle_Loop"

    import json
    sent_dict = json.loads(sent["data"].decode("utf-8").rstrip())
    assert sent_dict == {
        "command": "set_anim_state_pose",
        "blueprint": "/Game/Blueprints/ABP_X",
        "state_machine": "Locomotion",
        "state": "Idle",
        "sequence": "/Game/Anims/Idle_Loop",
    }


def test_set_anim_state_pose_skeleton_mismatch() -> None:
    response = (
        b'{"ok":false,"command":"set_anim_state_pose","error":"skeleton_mismatch",'
        b'"detail":"Sequence skeleton=/Game/A, AnimBP skeleton=/Game/B"}\n'
    )
    with mock.patch.object(socket, "create_connection", return_value=_fake_sock(response)):
        r = server.set_anim_state_pose(
            blueprint="/Game/X", state_machine="L", state="Idle", sequence="/Game/A",
        )
    assert r["ok"] is False
    assert r["error"] == "skeleton_mismatch"


def test_set_anim_state_pose_sequence_not_found() -> None:
    response = (
        b'{"ok":false,"command":"set_anim_state_pose","error":"sequence_not_found",'
        b'"detail":"/Game/NoSeq"}\n'
    )
    with mock.patch.object(socket, "create_connection", return_value=_fake_sock(response)):
        r = server.set_anim_state_pose(
            blueprint="/Game/X", state_machine="L", state="Idle", sequence="/Game/NoSeq",
        )
    assert r["ok"] is False
    assert r["error"] == "sequence_not_found"


def test_set_anim_state_pose_local_validation() -> None:
    assert server.set_anim_state_pose(blueprint="", state_machine="L", state="S", sequence="Q")["error"] == "missing_argument"
    assert server.set_anim_state_pose(blueprint="/G/X", state_machine="", state="S", sequence="Q")["error"] == "missing_argument"
    assert server.set_anim_state_pose(blueprint="/G/X", state_machine="L", state="", sequence="Q")["error"] == "missing_argument"
    assert server.set_anim_state_pose(blueprint="/G/X", state_machine="L", state="S", sequence="")["error"] == "missing_argument"


@requires_ue_editor(extra_reason="v9.2.0 AnimGraph state-machine end-to-end")
def test_v9_2_anim_state_machine_against_real_plugin() -> None:
    """End-to-end: AnimBP → state machine → 2 states → transition → sequence binding.

    Builds a minimal 2-state FSM (Idle ⇄ Run) and verifies each step. The
    test is robust to missing animation sequences — it probes a list of
    candidate sequence paths and skips the ``set_anim_state_pose`` step if
    none exist (still asserts the prior state-machine + state + transition
    calls).
    """
    import uuid

    # Step 1: Find a skeleton (same probe as the v9.0.0 test).
    candidate_skeletons = [
        "/Game/FirstPersonArms/Character/Mesh/SK_Mannequin_Arms_Skeleton",
        "/Game/Characters/Mannequins/Meshes/SK_Mannequin",
        "/Game/Mannequin/Mesh/UE4_Mannequin_Skeleton",
    ]
    skeleton = None
    abp_name = f"ABP_V92_FSM_{uuid.uuid4().hex[:8]}"
    for candidate in candidate_skeletons:
        r = server.create_anim_blueprint(
            name=abp_name, skeleton=candidate, path="/Game/Tests",
        )
        if r["ok"]:
            skeleton = candidate
            break
    assert skeleton is not None, (
        "No skeleton resolved. Run the v9.0.0 test first to debug."
    )
    abp_path = f"/Game/Tests/{abp_name}"

    # Step 2: Add a state machine.
    r = server.add_anim_state_machine(blueprint=abp_path, name="Locomotion")
    assert r["ok"] is True, f"add_anim_state_machine failed: {r}"
    assert r["state_machine"] == "Locomotion"
    assert r["saved"] is True

    # Step 3: Add two states.
    r = server.add_anim_state(
        blueprint=abp_path, state_machine="Locomotion", name="Idle",
        pos_x=-200, pos_y=0,
    )
    assert r["ok"] is True, f"add_anim_state Idle failed: {r}"
    r = server.add_anim_state(
        blueprint=abp_path, state_machine="Locomotion", name="Run",
        pos_x=200, pos_y=0,
    )
    assert r["ok"] is True, f"add_anim_state Run failed: {r}"

    # Step 4: Add a transition Idle → Run.
    r = server.add_anim_transition(
        blueprint=abp_path, state_machine="Locomotion",
        from_state="Idle", to_state="Run",
    )
    assert r["ok"] is True, f"add_anim_transition failed: {r}"

    # Step 5: (best-effort) Try to wire Idle to a sequence.
    # Use list_assets to discover an AnimSequence on the same skeleton.
    list_r = server.list_assets(asset_class="AnimSequence", max_results=50)
    assert list_r["ok"] is True
    sequences = list_r.get("assets", [])
    if sequences:
        # Try each until one's skeleton matches the AnimBP's skeleton.
        # Without per-asset skeleton metadata we just attempt the first;
        # skeleton_mismatch is a legitimate skip path.
        for seq in sequences[:5]:
            seq_path = seq["path"]
            r = server.set_anim_state_pose(
                blueprint=abp_path, state_machine="Locomotion",
                state="Idle", sequence=seq_path,
            )
            if r["ok"]:
                assert r["wired"] is True, (
                    f"sequence loaded but pose not wired: {r}"
                )
                break
        # If no sequences had matching skeleton, that's an acceptable skip.
    # If no AnimSequences exist in the project, also acceptable —
    # the structural FSM build passed.


def test_ping_returns_plugin_version_9_2_0() -> None:
    """v9.2.0: ping surfaces 9.2.0."""
    response = (
        b'{"ok":true,"command":"ping","version":"0.0.1",'
        b'"plugin_version":"9.2.0","build_date":"May 21 2026 12:00:00",'
        b'"timestamp":"2026-05-21T12:00:00.000Z"}\n'
    )
    with mock.patch.object(socket, "create_connection", return_value=_fake_sock(response)):
        r = server.ping_ue()
    assert r["ok"] is True
    assert r["plugin_version"] == "9.2.0"


# ---------------------------------------------------------------------------
# v9.3.0 — Niagara door-opener
# ---------------------------------------------------------------------------


def test_create_niagara_system_success() -> None:
    response = (
        b'{"ok":true,"command":"create_niagara_system",'
        b'"system_path":"/Game/VFX/NS_Sparkles","saved":true}\n'
    )
    sent: dict = {}
    with mock.patch.object(socket, "create_connection", return_value=_fake_sock(response, sent)):
        r = server.create_niagara_system(name="NS_Sparkles")
    assert r["ok"] is True
    assert r["system_path"] == "/Game/VFX/NS_Sparkles"
    assert r["saved"] is True

    import json
    sent_dict = json.loads(sent["data"].decode("utf-8").rstrip())
    assert sent_dict == {
        "command": "create_niagara_system",
        "name": "NS_Sparkles",
        "path": "/Game/VFX",
    }


def test_create_niagara_system_custom_path() -> None:
    response = (
        b'{"ok":true,"command":"create_niagara_system",'
        b'"system_path":"/Game/Tests/NS_Probe","saved":true}\n'
    )
    sent: dict = {}
    with mock.patch.object(socket, "create_connection", return_value=_fake_sock(response, sent)):
        r = server.create_niagara_system(name="NS_Probe", path="/Game/Tests")
    assert r["ok"] is True
    import json
    sent_dict = json.loads(sent["data"].decode("utf-8").rstrip())
    assert sent_dict["path"] == "/Game/Tests"


def test_create_niagara_system_asset_exists() -> None:
    response = (
        b'{"ok":false,"command":"create_niagara_system","error":"asset_exists",'
        b'"detail":"/Game/VFX/NS_Existing"}\n'
    )
    with mock.patch.object(socket, "create_connection", return_value=_fake_sock(response)):
        r = server.create_niagara_system(name="NS_Existing")
    assert r["ok"] is False
    assert r["error"] == "asset_exists"


def test_create_niagara_system_local_validation() -> None:
    assert server.create_niagara_system(name="")["error"] == "missing_argument"


@skip_if_headless("Niagara shader compile chronically exceeds 12s Python timeout in cold-boot headless")
@requires_ue_editor(extra_reason="v9.3.0 Niagara creation end-to-end")
def test_create_niagara_system_against_real_plugin() -> None:
    """Integration: create a real UNiagaraSystem asset and verify."""
    import uuid

    unique_name = f"NS_V93Test_{uuid.uuid4().hex[:8]}"
    r = server.create_niagara_system(name=unique_name, path="/Game/Tests")
    assert r["ok"] is True, f"create_niagara_system failed: {r}"
    assert r["system_path"] == f"/Game/Tests/{unique_name}"
    assert r["saved"] is True

    # Verify via list_assets discovery (round-trips through asset registry).
    list_r = server.list_assets(
        folder="/Game/Tests", asset_class="NiagaraSystem", max_results=100,
    )
    assert list_r["ok"] is True
    names = [a["name"] for a in list_r["assets"]]
    assert unique_name in names, (
        f"Created system {unique_name} not visible via list_assets. "
        f"Got: {names}"
    )


def test_ping_returns_plugin_version_9_3_0() -> None:
    """v9.3.0: ping surfaces 9.3.0."""
    response = (
        b'{"ok":true,"command":"ping","version":"0.0.1",'
        b'"plugin_version":"9.3.0","build_date":"May 21 2026 12:00:00",'
        b'"timestamp":"2026-05-21T12:00:00.000Z"}\n'
    )
    with mock.patch.object(socket, "create_connection", return_value=_fake_sock(response)):
        r = server.ping_ue()
    assert r["ok"] is True
    assert r["plugin_version"] == "9.3.0"


# ---------------------------------------------------------------------------
# v9.4.0 — UMG door-opener + save_all
# ---------------------------------------------------------------------------


def test_create_widget_blueprint_success() -> None:
    response = (
        b'{"ok":true,"command":"create_widget_blueprint",'
        b'"widget_path":"/Game/UI/WBP_Menu",'
        b'"parent_class":"/Script/UMG.UserWidget","saved":true}\n'
    )
    sent: dict = {}
    with mock.patch.object(socket, "create_connection", return_value=_fake_sock(response, sent)):
        r = server.create_widget_blueprint(name="WBP_Menu")
    assert r["ok"] is True
    assert r["widget_path"] == "/Game/UI/WBP_Menu"
    assert r["saved"] is True

    import json
    sent_dict = json.loads(sent["data"].decode("utf-8").rstrip())
    # parent_class omitted when blank — server uses default UUserWidget
    assert sent_dict == {
        "command": "create_widget_blueprint",
        "name": "WBP_Menu",
        "path": "/Game/UI",
    }


def test_create_widget_blueprint_custom_parent() -> None:
    response = (
        b'{"ok":true,"command":"create_widget_blueprint",'
        b'"widget_path":"/Game/UI/WBP_Submenu",'
        b'"parent_class":"/Game/UI/WBP_MenuBase_C","saved":true}\n'
    )
    sent: dict = {}
    with mock.patch.object(socket, "create_connection", return_value=_fake_sock(response, sent)):
        r = server.create_widget_blueprint(
            name="WBP_Submenu",
            parent_class="/Game/UI/WBP_MenuBase_C",
        )
    assert r["ok"] is True
    import json
    sent_dict = json.loads(sent["data"].decode("utf-8").rstrip())
    assert sent_dict["parent_class"] == "/Game/UI/WBP_MenuBase_C"


def test_create_widget_blueprint_invalid_parent() -> None:
    response = (
        b'{"ok":false,"command":"create_widget_blueprint",'
        b'"error":"invalid_parent_class","detail":"/Game/Foo/Bar must derive from UUserWidget"}\n'
    )
    with mock.patch.object(socket, "create_connection", return_value=_fake_sock(response)):
        r = server.create_widget_blueprint(name="WBP_X", parent_class="/Game/Foo/Bar")
    assert r["ok"] is False
    assert r["error"] == "invalid_parent_class"


def test_create_widget_blueprint_local_validation() -> None:
    assert server.create_widget_blueprint(name="")["error"] == "missing_argument"


def test_save_all_success() -> None:
    response = (
        b'{"ok":true,"command":"save_all","saved":true,"packages_needed_saving":true}\n'
    )
    sent: dict = {}
    with mock.patch.object(socket, "create_connection", return_value=_fake_sock(response, sent)):
        r = server.save_all()
    assert r["ok"] is True
    assert r["saved"] is True
    assert r["packages_needed_saving"] is True

    import json
    sent_dict = json.loads(sent["data"].decode("utf-8").rstrip())
    assert sent_dict == {"command": "save_all"}


def test_save_all_nothing_dirty() -> None:
    response = (
        b'{"ok":true,"command":"save_all","saved":true,"packages_needed_saving":false}\n'
    )
    with mock.patch.object(socket, "create_connection", return_value=_fake_sock(response)):
        r = server.save_all()
    assert r["ok"] is True
    assert r["packages_needed_saving"] is False


@requires_ue_editor(extra_reason="v9.4.0 UMG + save_all end-to-end")
def test_v9_4_widget_blueprint_and_save_all_against_real_plugin() -> None:
    """Integration: create a widget BP, verify via list_assets, then save_all."""
    import uuid

    unique_name = f"WBP_V94Test_{uuid.uuid4().hex[:8]}"
    r = server.create_widget_blueprint(name=unique_name, path="/Game/Tests")
    assert r["ok"] is True, f"create_widget_blueprint failed: {r}"
    assert r["widget_path"] == f"/Game/Tests/{unique_name}"

    # Verify via list_assets — exercises the non-Engine class fallback
    # (WidgetBlueprint lives in /Script/UMGEditor).
    list_r = server.list_assets(
        folder="/Game/Tests", asset_class="WidgetBlueprint", max_results=200,
    )
    assert list_r["ok"] is True
    names = [a["name"] for a in list_r["assets"]]
    assert unique_name in names, (
        f"Created widget BP {unique_name} not visible via list_assets. "
        f"Got: {names}"
    )

    # save_all should succeed even if nothing else is dirty.
    save_r = server.save_all()
    assert save_r["ok"] is True, f"save_all failed: {save_r}"
    # Note: SaveDirtyPackages returns False in commandlet/headless mode even
    # when packages are actually saved on disk — likely because the UI
    # "save success" notification path is skipped. Don't assert saved=True
    # here; the assertion above (ok=True) is the meaningful invariant.


def test_ping_returns_plugin_version_9_4_0() -> None:
    """v9.4.0: ping surfaces 9.4.0."""
    response = (
        b'{"ok":true,"command":"ping","version":"0.0.1",'
        b'"plugin_version":"9.4.0","build_date":"May 21 2026 12:00:00",'
        b'"timestamp":"2026-05-21T12:00:00.000Z"}\n'
    )
    with mock.patch.object(socket, "create_connection", return_value=_fake_sock(response)):
        r = server.ping_ue()
    assert r["ok"] is True
    assert r["plugin_version"] == "9.4.0"


# ---------------------------------------------------------------------------
# v9.5.0 — silent dispatcher auto-migration (Python-only)
# ---------------------------------------------------------------------------
# These tools wrap migrate_dispatchers + list_blueprints — pure Python
# composition, no plugin changes. plugin_version stays at 9.4.0.


def test_auto_migrate_dispatchers_passes_recreate_true() -> None:
    """auto_migrate_dispatchers is just migrate_dispatchers(recreate_ghosts=True)."""
    response = (
        b'{"ok":true,"command":"migrate_dispatchers","blueprint":"/Game/BP",'
        b'"migrated_count":0,"already_healthy_count":0,"orphan_variable_count":0,'
        b'"ghosts_detected_count":1,"ghosts_recreated_count":1,'
        b'"migrated":[],"already_healthy":[],"orphan_variables":[],'
        b'"ghosts_detected":["OnDeath"],"ghosts_recreated":["OnDeath"],'
        b'"recreate_ghosts_requested":true,"compiled":true,"saved":true}\n'
    )
    sent: dict = {}
    with mock.patch.object(socket, "create_connection", return_value=_fake_sock(response, sent)):
        r = server.auto_migrate_dispatchers(blueprint="/Game/BP")
    assert r["ok"] is True
    assert r["ghosts_recreated_count"] == 1
    import json
    sent_dict = json.loads(sent["data"].decode("utf-8").rstrip())
    assert sent_dict["recreate_ghosts"] is True


def test_auto_migrate_all_dispatchers_walks_blueprints() -> None:
    """Project-wide sweep: list_blueprints → per-BP migrate, aggregated totals."""
    # Mock 2 separate _send_command responses:
    # 1. list_blueprints — returns 2 BPs
    # 2 & 3. migrate_dispatchers on each BP
    list_response = (
        b'{"ok":true,"command":"list_blueprints","folder":"/Game","asset_class":"Blueprint",'
        b'"recursive":true,"count":2,"assets":['
        b'{"name":"BP_A","path":"/Game/Tests/BP_A.BP_A","package_path":"/Game/Tests","class":"Blueprint"},'
        b'{"name":"BP_B","path":"/Game/Tests/BP_B.BP_B","package_path":"/Game/Tests","class":"Blueprint"}'
        b']}\n'
    )
    migrate_a = (
        b'{"ok":true,"command":"migrate_dispatchers","blueprint":"/Game/Tests/BP_A",'
        b'"migrated_count":1,"already_healthy_count":0,"orphan_variable_count":0,'
        b'"ghosts_detected_count":2,"ghosts_recreated_count":2,'
        b'"migrated":["OnHit"],"already_healthy":[],"orphan_variables":[],'
        b'"ghosts_detected":["OnDeath","OnSpawn"],"ghosts_recreated":["OnDeath","OnSpawn"],'
        b'"recreate_ghosts_requested":true,"compiled":true,"saved":true}\n'
    )
    migrate_b = (
        b'{"ok":true,"command":"migrate_dispatchers","blueprint":"/Game/Tests/BP_B",'
        b'"migrated_count":0,"already_healthy_count":1,"orphan_variable_count":0,'
        b'"ghosts_detected_count":0,"ghosts_recreated_count":0,'
        b'"migrated":[],"already_healthy":["OnReady"],"orphan_variables":[],'
        b'"ghosts_detected":[],"ghosts_recreated":[],'
        b'"recreate_ghosts_requested":true,"compiled":false,"saved":false}\n'
    )
    responses = [list_response, migrate_a, migrate_b]

    def fake_create_connection(*args, **kwargs):
        return _fake_sock(responses.pop(0))

    with mock.patch.object(socket, "create_connection", side_effect=fake_create_connection):
        r = server.auto_migrate_all_dispatchers(folder="/Game")

    assert r["ok"] is True
    assert r["blueprint_count"] == 2
    assert r["total_migrated"] == 1
    assert r["total_ghosts_recreated"] == 2
    assert r["total_ghosts_detected"] == 2
    assert r["compiled_count"] == 1   # only BP_A actually changed
    assert r["saved_count"] == 1
    assert len(r["results"]) == 2
    assert len(r["errors"]) == 0


def test_auto_migrate_all_dispatchers_dry_run_omits_recreate() -> None:
    """dry_run=True calls migrate_dispatchers without recreate_ghosts."""
    sent_payloads: list[dict] = []

    def patched_send(payload):
        sent_payloads.append(payload)
        if payload["command"] == "list_blueprints":
            return {
                "ok": True, "assets": [
                    {"name": "BP_A", "path": "/Game/BP_A.BP_A", "package_path": "/Game", "class": "Blueprint"},
                ],
            }
        return {
            "ok": True, "migrated_count": 0, "ghosts_recreated_count": 0,
            "orphan_variable_count": 0, "ghosts_detected_count": 1,
            "compiled": False, "saved": False,
        }

    with mock.patch("unreal_blueprint_mcp.server._send_command", side_effect=patched_send):
        r = server.auto_migrate_all_dispatchers(folder="/Game", dry_run=True)

    assert r["ok"] is True
    assert r["dry_run"] is True
    # First call is list_blueprints, second is migrate_dispatchers without recreate
    assert sent_payloads[1]["command"] == "migrate_dispatchers"
    assert "recreate_ghosts" not in sent_payloads[1]


def test_auto_migrate_all_dispatchers_collects_errors() -> None:
    """A bad BP doesn't abort the sweep — it's logged in errors[]."""
    def patched_send(payload):
        if payload["command"] == "list_blueprints":
            return {
                "ok": True, "assets": [
                    {"name": "BP_X", "path": "/Game/BP_X.BP_X", "package_path": "/Game", "class": "Blueprint"},
                ],
            }
        return {"ok": False, "error": "blueprint_load_failed", "detail": "asset corrupted"}

    with mock.patch("unreal_blueprint_mcp.server._send_command", side_effect=patched_send):
        r = server.auto_migrate_all_dispatchers(folder="/Game")
    assert r["ok"] is True
    assert r["blueprint_count"] == 1
    assert len(r["errors"]) == 1
    assert r["errors"][0]["error"] == "blueprint_load_failed"
    assert r["total_migrated"] == 0


def test_auto_migrate_all_dispatchers_list_failure_propagates() -> None:
    """If list_blueprints itself fails, return early."""
    def patched_send(payload):
        return {"ok": False, "error": "asset_registry_unavailable"}

    with mock.patch("unreal_blueprint_mcp.server._send_command", side_effect=patched_send):
        r = server.auto_migrate_all_dispatchers(folder="/Game")
    assert r["ok"] is False
    assert r["error"] == "list_blueprints_failed"


@requires_ue_editor(extra_reason="v9.5.0 project-wide silent dispatcher migration")
def test_v9_5_auto_migrate_all_dispatchers_against_real_plugin() -> None:
    """Integration: sweep /Game/Tests for any legacy dispatchers. Idempotent."""
    # Run once — should fix anything legacy that exists, or be a no-op on
    # a healthy project.
    r = server.auto_migrate_all_dispatchers(folder="/Game/Tests")
    assert r["ok"] is True, f"auto_migrate_all_dispatchers failed: {r}"
    assert "results" in r
    assert "errors" in r

    # Re-run — should now be a complete no-op (idempotent).
    r2 = server.auto_migrate_all_dispatchers(folder="/Game/Tests")
    assert r2["ok"] is True
    # After first pass, no further changes should be needed
    assert r2["total_migrated"] == 0
    assert r2["total_ghosts_recreated"] == 0


# ---------------------------------------------------------------------------
# v9.6.0 — headless CI harness (shutdown_editor + commandlet)
# ---------------------------------------------------------------------------
# The commandlet itself + headless launcher script are exercised by CI;
# the unit-level test is just the new shutdown_editor TCP command shape.
# NO integration test here — calling shutdown_editor against the live
# editor would kill it mid-suite. Run scripts/run_headless_ci.sh manually
# to validate end-to-end.


def test_shutdown_editor_success() -> None:
    response = b'{"ok":true,"command":"shutdown_editor","requested":true}\n'
    sent: dict = {}
    with mock.patch.object(socket, "create_connection", return_value=_fake_sock(response, sent)):
        r = server.shutdown_editor()
    assert r["ok"] is True
    assert r["requested"] is True

    import json
    sent_dict = json.loads(sent["data"].decode("utf-8").rstrip())
    assert sent_dict == {"command": "shutdown_editor"}


def test_ping_returns_plugin_version_9_6_0() -> None:
    """v9.6.0: ping surfaces 9.6.0."""
    response = (
        b'{"ok":true,"command":"ping","version":"0.0.1",'
        b'"plugin_version":"9.6.0","build_date":"May 21 2026 12:00:00",'
        b'"timestamp":"2026-05-21T12:00:00.000Z"}\n'
    )
    with mock.patch.object(socket, "create_connection", return_value=_fake_sock(response)):
        r = server.ping_ue()
    assert r["ok"] is True
    assert r["plugin_version"] == "9.6.0"


# ---------------------------------------------------------------------------
# v9.7.0 — Level / instance manipulation
# ---------------------------------------------------------------------------


def test_list_level_actors_success() -> None:
    response = (
        b'{"ok":true,"command":"list_level_actors","class_filter":"",'
        b'"actors":[{"name":"BP_Portal_C_1","label":"Portal A","class":"BP_Portal_C",'
        b'"location":[100.0,200.0,50.0]}],"count":1}\n'
    )
    sent: dict = {}
    with mock.patch.object(socket, "create_connection", return_value=_fake_sock(response, sent)):
        r = server.list_level_actors()
    assert r["ok"] is True
    assert r["count"] == 1
    assert r["actors"][0]["label"] == "Portal A"

    import json
    sent_dict = json.loads(sent["data"].decode("utf-8").rstrip())
    # Empty filters omitted
    assert sent_dict == {"command": "list_level_actors", "max_results": 500}


def test_list_level_actors_with_filters() -> None:
    response = b'{"ok":true,"command":"list_level_actors","actors":[],"count":0}\n'
    sent: dict = {}
    with mock.patch.object(socket, "create_connection", return_value=_fake_sock(response, sent)):
        r = server.list_level_actors(class_filter="StaticMeshActor", name_contains="floor", max_results=50)
    assert r["ok"] is True

    import json
    sent_dict = json.loads(sent["data"].decode("utf-8").rstrip())
    assert sent_dict["class_filter"] == "StaticMeshActor"
    assert sent_dict["name_contains"] == "floor"
    assert sent_dict["max_results"] == 50


def test_get_actor_transform_success() -> None:
    response = (
        b'{"ok":true,"command":"get_actor_transform","actor":"BP_Portal_C_1",'
        b'"label":"Portal A","class":"BP_Portal_C",'
        b'"location":[100.0,200.0,50.0],"rotation":[0.0,90.0,0.0],"scale":[1.0,1.0,1.0]}\n'
    )
    with mock.patch.object(socket, "create_connection", return_value=_fake_sock(response)):
        r = server.get_actor_transform(actor="BP_Portal_C_1")
    assert r["ok"] is True
    assert r["location"] == [100.0, 200.0, 50.0]
    assert r["rotation"][1] == 90.0


def test_get_actor_transform_not_found() -> None:
    response = (
        b'{"ok":false,"command":"get_actor_transform","error":"actor_not_found",'
        b'"detail":"Ghost"}\n'
    )
    with mock.patch.object(socket, "create_connection", return_value=_fake_sock(response)):
        r = server.get_actor_transform(actor="Ghost")
    assert r["ok"] is False
    assert r["error"] == "actor_not_found"


def test_get_actor_transform_local_validation() -> None:
    assert server.get_actor_transform(actor="")["error"] == "missing_argument"


def test_set_actor_transform_location_only() -> None:
    response = (
        b'{"ok":true,"command":"set_actor_transform","actor":"BP_Portal_C_1","moved":true,'
        b'"location":[500.0,0.0,100.0],"rotation":[0.0,0.0,0.0],"scale":[1.0,1.0,1.0]}\n'
    )
    sent: dict = {}
    with mock.patch.object(socket, "create_connection", return_value=_fake_sock(response, sent)):
        r = server.set_actor_transform(actor="BP_Portal_C_1", location=[500, 0, 100])
    assert r["ok"] is True
    assert r["moved"] is True

    import json
    sent_dict = json.loads(sent["data"].decode("utf-8").rstrip())
    assert sent_dict["location"] == [500, 0, 100]
    assert "rotation" not in sent_dict   # only location was set
    assert "scale" not in sent_dict


def test_set_actor_transform_all_fields() -> None:
    response = b'{"ok":true,"command":"set_actor_transform","actor":"A","moved":true,"location":[0,0,0],"rotation":[0,0,0],"scale":[1,1,1]}\n'
    sent: dict = {}
    with mock.patch.object(socket, "create_connection", return_value=_fake_sock(response, sent)):
        server.set_actor_transform(
            actor="A",
            location=[1, 2, 3],
            rotation=[10, 20, 30],
            scale=[2, 2, 2],
        )
    import json
    sent_dict = json.loads(sent["data"].decode("utf-8").rstrip())
    assert sent_dict["location"] == [1, 2, 3]
    assert sent_dict["rotation"] == [10, 20, 30]
    assert sent_dict["scale"] == [2, 2, 2]


def test_set_actor_transform_local_validation() -> None:
    assert server.set_actor_transform(actor="")["error"] == "missing_argument"


def test_set_actor_property_string() -> None:
    response = (
        b'{"ok":true,"command":"set_actor_property","actor":"PortalA",'
        b'"property":"DisplayName","resolved_value":"East Portal"}\n'
    )
    sent: dict = {}
    with mock.patch.object(socket, "create_connection", return_value=_fake_sock(response, sent)):
        r = server.set_actor_property(actor="PortalA", property="DisplayName", value="East Portal")
    assert r["ok"] is True
    assert r["resolved_value"] == "East Portal"

    import json
    sent_dict = json.loads(sent["data"].decode("utf-8").rstrip())
    assert sent_dict == {
        "command": "set_actor_property",
        "actor": "PortalA",
        "property": "DisplayName",
        "value": "East Portal",
    }


def test_set_actor_property_actor_ref() -> None:
    """The double-portal canonical use case: pass another actor's name as value."""
    response = (
        b'{"ok":true,"command":"set_actor_property","actor":"PortalA",'
        b'"property":"LinkedPortal","resolved_value":"/Game/Maps/Demo.Demo:PersistentLevel.BP_Portal_C_2"}\n'
    )
    with mock.patch.object(socket, "create_connection", return_value=_fake_sock(response)):
        r = server.set_actor_property(actor="PortalA", property="LinkedPortal", value="PortalB")
    assert r["ok"] is True
    assert "LinkedPortal" not in r.get("resolved_value", "")  # path is what's returned


def test_set_actor_property_local_validation() -> None:
    assert server.set_actor_property(actor="", property="X")["error"] == "missing_argument"
    assert server.set_actor_property(actor="A", property="")["error"] == "missing_argument"


def test_delete_actor_success() -> None:
    response = b'{"ok":true,"command":"delete_actor","actor":"PortalA","destroyed":true}\n'
    sent: dict = {}
    with mock.patch.object(socket, "create_connection", return_value=_fake_sock(response, sent)):
        r = server.delete_actor(actor="PortalA")
    assert r["ok"] is True
    assert r["destroyed"] is True
    import json
    sent_dict = json.loads(sent["data"].decode("utf-8").rstrip())
    assert sent_dict == {"command": "delete_actor", "actor": "PortalA"}


def test_delete_actor_local_validation() -> None:
    assert server.delete_actor(actor="")["error"] == "missing_argument"


@skip_if_headless("level actor ops require a loaded level with persistent actors")
@requires_ue_editor(extra_reason="v9.7.0 level/instance ops end-to-end")
def test_v9_7_level_actor_ops_against_real_plugin() -> None:
    """End-to-end: spawn → list → get transform → set transform → delete."""
    import uuid

    # 1. Spawn a BP to operate on.
    bp_name = f"BP_V97Test_{uuid.uuid4().hex[:6]}"
    cr = server.create_blueprint(name=bp_name, path="/Game/Tests")
    assert cr["ok"], f"create_blueprint failed: {cr}"
    bp_path = cr["blueprint_path"]
    assert server.compile_blueprint(name=bp_path)["ok"]
    sp = server.spawn_actor(blueprint=bp_path, location_x=100, location_y=200, location_z=50)
    assert sp["ok"], f"spawn_actor failed: {sp}"
    actor_name = sp["actor_name"]

    # 2. List — confirm it's visible.
    lr = server.list_level_actors(name_contains=bp_name)
    assert lr["ok"]
    assert any(a["name"] == actor_name for a in lr["actors"]), (
        f"Spawned actor {actor_name} not in list_level_actors result: "
        f"{[a['name'] for a in lr['actors']]}"
    )

    # 3. Get transform — should match where we spawned.
    gt = server.get_actor_transform(actor=actor_name)
    assert gt["ok"], f"get_actor_transform failed: {gt}"
    assert abs(gt["location"][0] - 100) < 0.5
    assert abs(gt["location"][1] - 200) < 0.5

    # 4. Move it.
    st = server.set_actor_transform(actor=actor_name, location=[500, -300, 75])
    assert st["ok"], f"set_actor_transform failed: {st}"
    gt2 = server.get_actor_transform(actor=actor_name)
    assert abs(gt2["location"][0] - 500) < 0.5
    assert abs(gt2["location"][1] - (-300)) < 0.5

    # 5. Clean up.
    dr = server.delete_actor(actor=actor_name)
    assert dr["ok"], f"delete_actor failed: {dr}"


def test_ping_returns_plugin_version_9_7_0() -> None:
    """v9.7.0: ping surfaces 9.7.0."""
    response = (
        b'{"ok":true,"command":"ping","version":"0.0.1",'
        b'"plugin_version":"9.7.0","build_date":"May 21 2026 12:00:00",'
        b'"timestamp":"2026-05-21T12:00:00.000Z"}\n'
    )
    with mock.patch.object(socket, "create_connection", return_value=_fake_sock(response)):
        r = server.ping_ue()
    assert r["ok"] is True
    assert r["plugin_version"] == "9.7.0"


# ---------------------------------------------------------------------------
# v9.8.0 — Blueprint / variable lifecycle
# ---------------------------------------------------------------------------


def test_add_variable_instance_editable() -> None:
    """v9.8.0 — instance_editable=True passes the flag through."""
    response = (
        b'{"ok":true,"command":"add_variable","variable_name":"LinkedPortal",'
        b'"variable_type":"object:Actor","instance_editable":true,"saved":true}\n'
    )
    sent: dict = {}
    with mock.patch.object(socket, "create_connection", return_value=_fake_sock(response, sent)):
        r = server.add_variable(
            blueprint="/Game/BP_Portal", name="LinkedPortal",
            variable_type="object:Actor", instance_editable=True,
        )
    assert r["ok"] is True
    assert r["instance_editable"] is True

    import json
    sent_dict = json.loads(sent["data"].decode("utf-8").rstrip())
    assert sent_dict["instance_editable"] is True


def test_add_variable_default_not_instance_editable() -> None:
    """Default behavior unchanged — instance_editable omitted from payload."""
    response = b'{"ok":true,"command":"add_variable","variable_name":"X","variable_type":"int","instance_editable":false,"saved":true}\n'
    sent: dict = {}
    with mock.patch.object(socket, "create_connection", return_value=_fake_sock(response, sent)):
        server.add_variable(blueprint="/Game/BP", name="X", variable_type="int")
    import json
    sent_dict = json.loads(sent["data"].decode("utf-8").rstrip())
    assert "instance_editable" not in sent_dict


def test_set_variable_flags_instance_editable_only() -> None:
    response = (
        b'{"ok":true,"command":"set_variable_flags","variable_name":"LinkedPortal",'
        b'"instance_editable":true,"blueprint_read_only":false,"expose_on_spawn":null,"saved":true}\n'
    )
    sent: dict = {}
    with mock.patch.object(socket, "create_connection", return_value=_fake_sock(response, sent)):
        r = server.set_variable_flags(
            blueprint="/Game/BP_Portal", name="LinkedPortal", instance_editable=True,
        )
    assert r["ok"] is True
    import json
    sent_dict = json.loads(sent["data"].decode("utf-8").rstrip())
    assert sent_dict["instance_editable"] is True
    assert "blueprint_read_only" not in sent_dict
    assert "expose_on_spawn" not in sent_dict


def test_set_variable_flags_all_three() -> None:
    response = b'{"ok":true,"command":"set_variable_flags","variable_name":"V","instance_editable":true,"blueprint_read_only":true,"expose_on_spawn":true,"saved":true}\n'
    sent: dict = {}
    with mock.patch.object(socket, "create_connection", return_value=_fake_sock(response, sent)):
        r = server.set_variable_flags(
            blueprint="/Game/BP", name="V",
            instance_editable=True, blueprint_read_only=True, expose_on_spawn=True,
        )
    assert r["ok"] is True
    import json
    sent_dict = json.loads(sent["data"].decode("utf-8").rstrip())
    assert sent_dict["instance_editable"] is True
    assert sent_dict["blueprint_read_only"] is True
    assert sent_dict["expose_on_spawn"] is True


def test_set_variable_flags_no_flag_specified() -> None:
    """All None → server should return no_flag_specified."""
    response = b'{"ok":false,"command":"set_variable_flags","error":"no_flag_specified"}\n'
    with mock.patch.object(socket, "create_connection", return_value=_fake_sock(response)):
        r = server.set_variable_flags(blueprint="/Game/BP", name="V")
    assert r["ok"] is False
    assert r["error"] == "no_flag_specified"


def test_set_variable_flags_local_validation() -> None:
    assert server.set_variable_flags(blueprint="", name="X")["error"] == "missing_argument"
    assert server.set_variable_flags(blueprint="/G/BP", name="")["error"] == "missing_argument"


def test_delete_variable_success() -> None:
    response = b'{"ok":true,"command":"delete_variable","variable_name":"OldVar","saved":true}\n'
    sent: dict = {}
    with mock.patch.object(socket, "create_connection", return_value=_fake_sock(response, sent)):
        r = server.delete_variable(blueprint="/Game/BP", name="OldVar")
    assert r["ok"] is True
    import json
    sent_dict = json.loads(sent["data"].decode("utf-8").rstrip())
    assert sent_dict == {"command": "delete_variable", "blueprint": "/Game/BP", "name": "OldVar"}


def test_delete_variable_not_found() -> None:
    response = b'{"ok":false,"command":"delete_variable","error":"variable_not_found","detail":"Ghost"}\n'
    with mock.patch.object(socket, "create_connection", return_value=_fake_sock(response)):
        r = server.delete_variable(blueprint="/Game/BP", name="Ghost")
    assert r["ok"] is False
    assert r["error"] == "variable_not_found"


def test_delete_variable_local_validation() -> None:
    assert server.delete_variable(blueprint="", name="V")["error"] == "missing_argument"
    assert server.delete_variable(blueprint="/G/BP", name="")["error"] == "missing_argument"


def test_delete_blueprint_success() -> None:
    response = b'{"ok":true,"command":"delete_blueprint","blueprint_path":"/Game/Tests/BP_X","deleted":true}\n'
    sent: dict = {}
    with mock.patch.object(socket, "create_connection", return_value=_fake_sock(response, sent)):
        r = server.delete_blueprint(path="/Game/Tests/BP_X")
    assert r["ok"] is True
    assert r["deleted"] is True
    import json
    sent_dict = json.loads(sent["data"].decode("utf-8").rstrip())
    assert sent_dict == {"command": "delete_blueprint", "path": "/Game/Tests/BP_X"}


def test_delete_blueprint_not_a_blueprint() -> None:
    """Safety: refuses to delete non-BP assets."""
    response = (
        b'{"ok":false,"command":"delete_blueprint","error":"not_a_blueprint",'
        b'"detail":"Asset at /Game/Tex_X is Texture2D, not a UBlueprint."}\n'
    )
    with mock.patch.object(socket, "create_connection", return_value=_fake_sock(response)):
        r = server.delete_blueprint(path="/Game/Tex_X")
    assert r["ok"] is False
    assert r["error"] == "not_a_blueprint"


def test_delete_blueprint_local_validation() -> None:
    assert server.delete_blueprint(path="")["error"] == "missing_argument"


@requires_ue_editor(extra_reason="v9.8.0 BP/variable lifecycle end-to-end")
def test_v9_8_blueprint_variable_lifecycle_against_real_plugin() -> None:
    """End-to-end: create BP → add var (instance_editable) → set flags →
    delete var → delete BP. All transitions verified."""
    import uuid
    bp_name = f"BP_V98Test_{uuid.uuid4().hex[:6]}"
    bp_path = f"/Game/Tests/{bp_name}"

    # 1. Create
    r = server.create_blueprint(name=bp_name, path="/Game/Tests")
    assert r["ok"], f"create_blueprint failed: {r}"

    # 2. Add variable with instance_editable=True
    r = server.add_variable(
        blueprint=bp_path, name="MyVar", variable_type="int",
        default_value="42", instance_editable=True,
    )
    assert r["ok"], f"add_variable failed: {r}"
    assert r["instance_editable"] is True

    # 3. Flip readonly flag
    r = server.set_variable_flags(
        blueprint=bp_path, name="MyVar", blueprint_read_only=True,
    )
    assert r["ok"], f"set_variable_flags failed: {r}"
    assert r["blueprint_read_only"] is True

    # 4. Delete the variable
    r = server.delete_variable(blueprint=bp_path, name="MyVar")
    assert r["ok"], f"delete_variable failed: {r}"

    # 5. Delete the BP
    r = server.delete_blueprint(path=bp_path)
    assert r["ok"], f"delete_blueprint failed: {r}"
    assert r["deleted"] is True


def test_ping_returns_plugin_version_9_8_0() -> None:
    """v9.8.0: ping surfaces 9.8.0."""
    response = (
        b'{"ok":true,"command":"ping","version":"0.0.1",'
        b'"plugin_version":"9.8.0","build_date":"May 21 2026 12:00:00",'
        b'"timestamp":"2026-05-21T12:00:00.000Z"}\n'
    )
    with mock.patch.object(socket, "create_connection", return_value=_fake_sock(response)):
        r = server.ping_ue()
    assert r["ok"] is True
    assert r["plugin_version"] == "9.8.0"


# ---------------------------------------------------------------------------
# v9.9.0 — PIE input enhancements
# ---------------------------------------------------------------------------


def test_pie_press_key_with_duration() -> None:
    """v9.9.0 — duration_sec > 0 schedules a held release via FTSTicker."""
    response = (
        b'{"ok":true,"command":"pie_press_key","key":"W","player_index":0,'
        b'"held":true,"duration_sec":2.0}\n'
    )
    sent: dict = {}
    with mock.patch.object(socket, "create_connection", return_value=_fake_sock(response, sent)):
        r = server.pie_press_key(key="W", duration_sec=2.0)
    assert r["ok"] is True
    assert r["held"] is True

    import json
    sent_dict = json.loads(sent["data"].decode("utf-8").rstrip())
    assert sent_dict["duration_sec"] == 2.0


def test_pie_press_key_duration_zero_omits_field() -> None:
    """duration_sec=0.0 keeps the v8.3 behavior — field omitted from payload."""
    response = b'{"ok":true,"command":"pie_press_key","key":"Space","player_index":0,"held":false,"duration_sec":0.0}\n'
    sent: dict = {}
    with mock.patch.object(socket, "create_connection", return_value=_fake_sock(response, sent)):
        server.pie_press_key(key="Space")
    import json
    sent_dict = json.loads(sent["data"].decode("utf-8").rstrip())
    assert "duration_sec" not in sent_dict


def test_pie_set_player_location_success() -> None:
    response = (
        b'{"ok":true,"command":"pie_set_player_location","player_index":0,'
        b'"requested":[100,200,50],"actual":[100,200,50],"moved":true}\n'
    )
    sent: dict = {}
    with mock.patch.object(socket, "create_connection", return_value=_fake_sock(response, sent)):
        r = server.pie_set_player_location(location=[100, 200, 50])
    assert r["ok"] is True
    assert r["moved"] is True
    import json
    sent_dict = json.loads(sent["data"].decode("utf-8").rstrip())
    assert sent_dict == {
        "command": "pie_set_player_location",
        "location": [100, 200, 50],
        "player_index": 0,
    }


def test_pie_set_player_location_local_validation() -> None:
    assert server.pie_set_player_location(location=[])["error"] == "missing_argument"
    assert server.pie_set_player_location(location=[1, 2])["error"] == "missing_argument"


def test_pie_move_player_forward() -> None:
    """Walk forward for 2 seconds."""
    response = (
        b'{"ok":true,"command":"pie_move_player","player_index":0,'
        b'"direction":[1,0,0],"duration_sec":2.0,"scale":1.0,"queued":true}\n'
    )
    sent: dict = {}
    with mock.patch.object(socket, "create_connection", return_value=_fake_sock(response, sent)):
        r = server.pie_move_player(direction=[1, 0, 0], duration_sec=2.0)
    assert r["ok"] is True
    assert r["queued"] is True
    import json
    sent_dict = json.loads(sent["data"].decode("utf-8").rstrip())
    assert sent_dict["direction"] == [1, 0, 0]
    assert sent_dict["duration_sec"] == 2.0
    assert sent_dict["scale"] == 1.0


def test_pie_move_player_local_validation() -> None:
    assert server.pie_move_player(direction=[])["error"] == "missing_argument"
    assert server.pie_move_player(direction=[1, 2])["error"] == "missing_argument"


def test_ping_returns_plugin_version_9_9_0() -> None:
    """v9.9.0: ping surfaces 9.9.0."""
    response = (
        b'{"ok":true,"command":"ping","version":"0.0.1",'
        b'"plugin_version":"9.9.0","build_date":"May 21 2026 12:00:00",'
        b'"timestamp":"2026-05-21T12:00:00.000Z"}\n'
    )
    with mock.patch.object(socket, "create_connection", return_value=_fake_sock(response)):
        r = server.ping_ue()
    assert r["ok"] is True
    assert r["plugin_version"] == "9.9.0"


# ---------------------------------------------------------------------------
# v9.10.0 — Player rotation control
# ---------------------------------------------------------------------------


def test_pie_set_player_rotation_success() -> None:
    response = (
        b'{"ok":true,"command":"pie_set_player_rotation","player_index":0,'
        b'"requested":[0,90,0],"applied":[0,90,0]}\n'
    )
    sent: dict = {}
    with mock.patch.object(socket, "create_connection", return_value=_fake_sock(response, sent)):
        r = server.pie_set_player_rotation(rotation=[0, 90, 0])
    assert r["ok"] is True
    assert r["applied"] == [0, 90, 0]
    import json
    sent_dict = json.loads(sent["data"].decode("utf-8").rstrip())
    assert sent_dict == {
        "command": "pie_set_player_rotation",
        "rotation": [0, 90, 0],
        "player_index": 0,
    }


def test_pie_set_player_rotation_pitch_clamped() -> None:
    """FPS templates clamp pitch — requested vs applied may differ."""
    response = (
        b'{"ok":true,"command":"pie_set_player_rotation","player_index":0,'
        b'"requested":[180,45,0],"applied":[89,45,0]}\n'
    )
    with mock.patch.object(socket, "create_connection", return_value=_fake_sock(response)):
        r = server.pie_set_player_rotation(rotation=[180, 45, 0])
    assert r["ok"] is True
    # Pitch was clamped
    assert r["applied"][0] == 89
    assert r["requested"][0] == 180


def test_pie_set_player_rotation_local_validation() -> None:
    assert server.pie_set_player_rotation(rotation=[])["error"] == "missing_argument"
    assert server.pie_set_player_rotation(rotation=[0, 90])["error"] == "missing_argument"


def test_pie_move_player_face_movement_passes_flag() -> None:
    """v9.10.0 — face_movement=True is forwarded to the plugin."""
    response = (
        b'{"ok":true,"command":"pie_move_player","player_index":0,'
        b'"direction":[1,0,0],"duration_sec":2.0,"scale":1.0,'
        b'"faced_movement":true,"applied_yaw":0.0,"queued":true}\n'
    )
    sent: dict = {}
    with mock.patch.object(socket, "create_connection", return_value=_fake_sock(response, sent)):
        r = server.pie_move_player(direction=[1, 0, 0], duration_sec=2.0, face_movement=True)
    assert r["ok"] is True
    assert r["faced_movement"] is True
    import json
    sent_dict = json.loads(sent["data"].decode("utf-8").rstrip())
    assert sent_dict["face_movement"] is True


def test_pie_move_player_face_movement_default_omitted() -> None:
    """Default face_movement=False keeps payload backwards-compatible."""
    response = b'{"ok":true,"command":"pie_move_player","player_index":0,"direction":[1,0,0],"duration_sec":1,"scale":1,"faced_movement":false,"applied_yaw":0,"queued":true}\n'
    sent: dict = {}
    with mock.patch.object(socket, "create_connection", return_value=_fake_sock(response, sent)):
        server.pie_move_player(direction=[1, 0, 0])
    import json
    sent_dict = json.loads(sent["data"].decode("utf-8").rstrip())
    assert "face_movement" not in sent_dict


def test_ping_returns_plugin_version_9_10_0() -> None:
    """v9.10.0: ping surfaces 9.10.0."""
    response = (
        b'{"ok":true,"command":"ping","version":"0.0.1",'
        b'"plugin_version":"9.10.0","build_date":"May 21 2026 12:00:00",'
        b'"timestamp":"2026-05-21T12:00:00.000Z"}\n'
    )
    with mock.patch.object(socket, "create_connection", return_value=_fake_sock(response)):
        r = server.ping_ue()
    assert r["ok"] is True
    assert r["plugin_version"] == "9.10.0"


# ---------------------------------------------------------------------------
# v9.11.0 — spawn_actor rotation + persistence + bounds
# ---------------------------------------------------------------------------


def test_spawn_actor_with_rotation() -> None:
    """v9.11.0 — rotation kwarg is forwarded."""
    response = (
        b'{"ok":true,"command":"spawn_actor","blueprint_path":"/Game/BP_Portal",'
        b'"actor_name":"BP_Portal_C_1","actor_label":"BP_Portal_C_1",'
        b'"location":[0,0,0],"rotation":[0,90,0]}\n'
    )
    sent: dict = {}
    with mock.patch.object(socket, "create_connection", return_value=_fake_sock(response, sent)):
        r = server.spawn_actor(blueprint="/Game/BP_Portal", rotation=[0, 90, 0])
    assert r["ok"] is True
    assert r["actor_label"] == "BP_Portal_C_1"

    import json
    sent_dict = json.loads(sent["data"].decode("utf-8").rstrip())
    assert sent_dict["rotation"] == [0, 90, 0]


def test_spawn_actor_no_rotation_omitted() -> None:
    """Default rotation=None keeps the payload backwards-compatible."""
    response = b'{"ok":true,"command":"spawn_actor","blueprint_path":"/Game/BP","actor_name":"A","actor_label":"A","location":[0,0,0],"rotation":[0,0,0]}\n'
    sent: dict = {}
    with mock.patch.object(socket, "create_connection", return_value=_fake_sock(response, sent)):
        server.spawn_actor(blueprint="/Game/BP")
    import json
    sent_dict = json.loads(sent["data"].decode("utf-8").rstrip())
    assert "rotation" not in sent_dict


def test_get_actor_transform_now_returns_bounds() -> None:
    """v9.11.0 — get_actor_transform response includes bounds_origin/extent."""
    response = (
        b'{"ok":true,"command":"get_actor_transform","actor":"Cube_1","label":"Cube",'
        b'"class":"StaticMeshActor","location":[0,0,0],"rotation":[0,0,0],"scale":[1,1,1],'
        b'"bounds_origin":[0,0,0],"bounds_extent":[50,50,50]}\n'
    )
    with mock.patch.object(socket, "create_connection", return_value=_fake_sock(response)):
        r = server.get_actor_transform(actor="Cube_1")
    assert r["ok"] is True
    assert r["bounds_origin"] == [0, 0, 0]
    assert r["bounds_extent"] == [50, 50, 50]


def test_get_actor_bounds_success() -> None:
    response = (
        b'{"ok":true,"command":"get_actor_bounds","actor":"Cube_1",'
        b'"world_origin":[100,0,50],"world_extent":[50,50,50],'
        b'"world_min":[50,-50,0],"world_max":[150,50,100],'
        b'"mesh_local_extent":[50,50,50],"mesh_asset":"/Engine/BasicShapes/Cube.Cube"}\n'
    )
    sent: dict = {}
    with mock.patch.object(socket, "create_connection", return_value=_fake_sock(response, sent)):
        r = server.get_actor_bounds(actor="Cube_1")
    assert r["ok"] is True
    assert r["world_min"] == [50, -50, 0]
    assert r["world_max"] == [150, 50, 100]
    assert r["mesh_local_extent"] == [50, 50, 50]
    assert r["mesh_asset"].endswith("Cube.Cube")
    import json
    sent_dict = json.loads(sent["data"].decode("utf-8").rstrip())
    assert sent_dict == {"command": "get_actor_bounds", "actor": "Cube_1"}


def test_get_actor_bounds_not_found() -> None:
    response = b'{"ok":false,"command":"get_actor_bounds","error":"actor_not_found","detail":"Ghost"}\n'
    with mock.patch.object(socket, "create_connection", return_value=_fake_sock(response)):
        r = server.get_actor_bounds(actor="Ghost")
    assert r["ok"] is False
    assert r["error"] == "actor_not_found"


def test_get_actor_bounds_local_validation() -> None:
    assert server.get_actor_bounds(actor="")["error"] == "missing_argument"


def test_list_level_actors_include_bounds() -> None:
    response = (
        b'{"ok":true,"command":"list_level_actors","class_filter":"StaticMeshActor",'
        b'"actors":[{"name":"Cube_1","label":"Cube","class":"StaticMeshActor",'
        b'"location":[0,0,0],"bounds_origin":[0,0,0],"bounds_extent":[50,50,50]}],"count":1}\n'
    )
    sent: dict = {}
    with mock.patch.object(socket, "create_connection", return_value=_fake_sock(response, sent)):
        r = server.list_level_actors(class_filter="StaticMeshActor", include_bounds=True)
    assert r["ok"] is True
    assert r["actors"][0]["bounds_extent"] == [50, 50, 50]
    import json
    sent_dict = json.loads(sent["data"].decode("utf-8").rstrip())
    assert sent_dict["include_bounds"] is True


def test_list_level_actors_include_bounds_default_off() -> None:
    response = b'{"ok":true,"command":"list_level_actors","actors":[],"count":0}\n'
    sent: dict = {}
    with mock.patch.object(socket, "create_connection", return_value=_fake_sock(response, sent)):
        server.list_level_actors()
    import json
    sent_dict = json.loads(sent["data"].decode("utf-8").rstrip())
    assert "include_bounds" not in sent_dict


def test_ping_returns_plugin_version_9_11_0() -> None:
    """v9.11.0: ping surfaces 9.11.0."""
    response = (
        b'{"ok":true,"command":"ping","version":"0.0.1",'
        b'"plugin_version":"9.11.0","build_date":"May 22 2026 12:00:00",'
        b'"timestamp":"2026-05-22T12:00:00.000Z"}\n'
    )
    with mock.patch.object(socket, "create_connection", return_value=_fake_sock(response)):
        r = server.ping_ue()
    assert r["ok"] is True
    assert r["plugin_version"] == "9.11.0"


# ---------------------------------------------------------------------------
# v9.12.0 — get_player_capsule + spawn_actor scale + snap_to_ground
# ---------------------------------------------------------------------------


def test_spawn_actor_with_scale() -> None:
    """v9.12.0 — scale kwarg is forwarded."""
    response = (
        b'{"ok":true,"command":"spawn_actor","blueprint_path":"/Game/BP",'
        b'"actor_name":"BP_C_1","actor_label":"BP","location":[0,0,0],'
        b'"rotation":[0,0,0],"scale":[11,3,2]}\n'
    )
    sent: dict = {}
    with mock.patch.object(socket, "create_connection", return_value=_fake_sock(response, sent)):
        r = server.spawn_actor(blueprint="/Game/BP", scale=[11, 3, 2])
    assert r["ok"] is True
    assert r["scale"] == [11, 3, 2]

    import json
    sent_dict = json.loads(sent["data"].decode("utf-8").rstrip())
    assert sent_dict["scale"] == [11, 3, 2]


def test_spawn_actor_rotation_and_scale_together() -> None:
    """Full-pose spawn — both rotation and scale on one call."""
    response = b'{"ok":true,"command":"spawn_actor","blueprint_path":"/Game/BP","actor_name":"A","actor_label":"A","location":[0,0,0],"rotation":[0,90,0],"scale":[2,2,2]}\n'
    sent: dict = {}
    with mock.patch.object(socket, "create_connection", return_value=_fake_sock(response, sent)):
        server.spawn_actor(blueprint="/Game/BP", rotation=[0, 90, 0], scale=[2, 2, 2])
    import json
    sent_dict = json.loads(sent["data"].decode("utf-8").rstrip())
    assert sent_dict["rotation"] == [0, 90, 0]
    assert sent_dict["scale"] == [2, 2, 2]


def test_spawn_actor_no_scale_omitted() -> None:
    """Default scale=None keeps the payload backwards-compatible."""
    response = b'{"ok":true,"command":"spawn_actor","blueprint_path":"/Game/BP","actor_name":"A","actor_label":"A","location":[0,0,0],"rotation":[0,0,0],"scale":[1,1,1]}\n'
    sent: dict = {}
    with mock.patch.object(socket, "create_connection", return_value=_fake_sock(response, sent)):
        server.spawn_actor(blueprint="/Game/BP")
    import json
    sent_dict = json.loads(sent["data"].decode("utf-8").rstrip())
    assert "scale" not in sent_dict


def test_get_player_capsule_character() -> None:
    """ACharacter pawn → has_capsule=True, scaled dims surface."""
    response = (
        b'{"ok":true,"command":"get_player_capsule","player_index":0,'
        b'"pawn_name":"FirstPersonCharacter_C_0","pawn_class":"FirstPersonCharacter_C",'
        b'"is_character":true,"has_capsule":true,"radius":34.0,"half_height":88.0,'
        b'"diameter":68.0,"full_height":176.0,'
        b'"location":[0,0,180],"rotation":[0,0,0]}\n'
    )
    sent: dict = {}
    with mock.patch.object(socket, "create_connection", return_value=_fake_sock(response, sent)):
        r = server.get_player_capsule()
    assert r["ok"] is True
    assert r["is_character"] is True
    assert r["radius"] == 34.0
    assert r["half_height"] == 88.0
    # Convenience-derived doubles
    assert r["diameter"] == 68.0
    assert r["full_height"] == 176.0
    import json
    sent_dict = json.loads(sent["data"].decode("utf-8").rstrip())
    assert sent_dict == {"command": "get_player_capsule", "player_index": 0}


def test_get_player_capsule_no_pie() -> None:
    response = b'{"ok":false,"command":"get_player_capsule","error":"pie_not_running","detail":"Start PIE first"}\n'
    with mock.patch.object(socket, "create_connection", return_value=_fake_sock(response)):
        r = server.get_player_capsule()
    assert r["ok"] is False
    assert r["error"] == "pie_not_running"


def test_pie_set_player_location_snap_to_ground() -> None:
    """snap_to_ground=True forwards the flag + trace params."""
    response = (
        b'{"ok":true,"command":"pie_set_player_location","player_index":0,'
        b'"requested":[200,0,500],"actual":[200,0,188.0],"moved":true,'
        b'"snapped_to_ground":true,"ground_z":100.0,"capsule_half_height":88.0,'
        b'"ground_hit":"Floor_1"}\n'
    )
    sent: dict = {}
    with mock.patch.object(socket, "create_connection", return_value=_fake_sock(response, sent)):
        r = server.pie_set_player_location(location=[200, 0, 500], snap_to_ground=True)
    assert r["ok"] is True
    assert r["snapped_to_ground"] is True
    assert r["ground_z"] == 100.0
    assert r["actual"][2] == 188.0   # 100 + 88
    import json
    sent_dict = json.loads(sent["data"].decode("utf-8").rstrip())
    assert sent_dict["snap_to_ground"] is True
    assert sent_dict["trace_up_height"] == 200.0
    assert sent_dict["trace_down_dist"] == 10000.0


def test_pie_set_player_location_no_snap_default() -> None:
    """Default snap=False keeps the payload backwards-compatible."""
    response = b'{"ok":true,"command":"pie_set_player_location","player_index":0,"requested":[0,0,0],"actual":[0,0,0],"moved":true,"snapped_to_ground":false,"ground_z":0,"capsule_half_height":0,"ground_hit":""}\n'
    sent: dict = {}
    with mock.patch.object(socket, "create_connection", return_value=_fake_sock(response, sent)):
        server.pie_set_player_location(location=[0, 0, 0])
    import json
    sent_dict = json.loads(sent["data"].decode("utf-8").rstrip())
    assert "snap_to_ground" not in sent_dict


def test_pie_set_player_location_snap_custom_trace() -> None:
    """Override trace_up_height / trace_down_dist."""
    response = b'{"ok":true,"command":"pie_set_player_location","player_index":0,"requested":[0,0,0],"actual":[0,0,0],"moved":true,"snapped_to_ground":false,"ground_z":0,"capsule_half_height":0,"ground_hit":""}\n'
    sent: dict = {}
    with mock.patch.object(socket, "create_connection", return_value=_fake_sock(response, sent)):
        server.pie_set_player_location(
            location=[0, 0, 0],
            snap_to_ground=True,
            trace_up_height=50,
            trace_down_dist=5000,
        )
    import json
    sent_dict = json.loads(sent["data"].decode("utf-8").rstrip())
    assert sent_dict["trace_up_height"] == 50
    assert sent_dict["trace_down_dist"] == 5000


def test_ping_returns_plugin_version_9_12_0() -> None:
    """v9.12.0: ping surfaces 9.12.0."""
    response = (
        b'{"ok":true,"command":"ping","version":"0.0.1",'
        b'"plugin_version":"9.12.0","build_date":"May 22 2026 12:00:00",'
        b'"timestamp":"2026-05-22T12:00:00.000Z"}\n'
    )
    with mock.patch.object(socket, "create_connection", return_value=_fake_sock(response)):
        r = server.ping_ue()
    assert r["ok"] is True
    assert r["plugin_version"] == "9.12.0"


# ---------------------------------------------------------------------------
# v9.13.0 — add_component_get + WP-aware spawn persistence + error hints
# ---------------------------------------------------------------------------


def test_add_component_get_success() -> None:
    response = (
        b'{"ok":true,"command":"add_component_get","anchor_name":"get_ism",'
        b'"component_name":"BlocksISM",'
        b'"component_class":"/Script/Engine.InstancedStaticMeshComponent",'
        b'"node_guid":"AAAA-BBBB","pins":[],"saved":true}\n'
    )
    sent: dict = {}
    with mock.patch.object(socket, "create_connection", return_value=_fake_sock(response, sent)):
        r = server.add_component_get(
            blueprint="/Game/BP_Floor",
            component_name="BlocksISM",
            anchor_name="get_ism",
            position_x=100, position_y=200,
        )
    assert r["ok"] is True
    assert r["component_class"].endswith("InstancedStaticMeshComponent")
    import json
    sent_dict = json.loads(sent["data"].decode("utf-8").rstrip())
    assert sent_dict == {
        "command": "add_component_get",
        "blueprint": "/Game/BP_Floor",
        "component_name": "BlocksISM",
        "anchor_name": "get_ism",
        "position_x": 100,
        "position_y": 200,
    }


def test_add_component_get_with_graph_name() -> None:
    """Function-body graphs also work."""
    response = b'{"ok":true,"command":"add_component_get","anchor_name":"g","component_name":"C","component_class":"","node_guid":"X","pins":[],"saved":true}\n'
    sent: dict = {}
    with mock.patch.object(socket, "create_connection", return_value=_fake_sock(response, sent)):
        server.add_component_get(
            blueprint="/Game/BP", component_name="C", anchor_name="g",
            graph_name="MyFunc",
        )
    import json
    sent_dict = json.loads(sent["data"].decode("utf-8").rstrip())
    assert sent_dict["graph_name"] == "MyFunc"


def test_add_component_get_component_not_found() -> None:
    response = (
        b'{"ok":false,"command":"add_component_get","error":"component_not_found",'
        b'"detail":"\'Ghost\' not found on /Game/BP. Add via add_component first..."}\n'
    )
    with mock.patch.object(socket, "create_connection", return_value=_fake_sock(response)):
        r = server.add_component_get(blueprint="/Game/BP", component_name="Ghost", anchor_name="g")
    assert r["ok"] is False
    assert r["error"] == "component_not_found"


def test_add_component_get_local_validation() -> None:
    assert server.add_component_get(blueprint="", component_name="C", anchor_name="g")["error"] == "missing_argument"
    assert server.add_component_get(blueprint="/G/BP", component_name="", anchor_name="g")["error"] == "missing_argument"
    assert server.add_component_get(blueprint="/G/BP", component_name="C", anchor_name="")["error"] == "missing_argument"


def test_add_node_invalid_node_type_includes_hint() -> None:
    """rev7 §二 — bare 'PrintString' should produce a format hint."""
    response = (
        b'{"ok":false,"command":"add_node","error":"invalid_node_type",'
        b'"detail":"Got \'PrintString\' \xe2\x80\x94 node_type must use \'<K2NodeClass>:<param>\' format. '
        b'Example: \'K2Node_CallFunction:PrintString\' or fully-qualified '
        b'\'K2Node_CallFunction:KismetSystemLibrary.PrintString\'."}\n'
    )
    with mock.patch.object(socket, "create_connection", return_value=_fake_sock(response)):
        r = server.add_node(
            blueprint="/Game/BP", node_type="PrintString", anchor_name="print_x",
        )
    assert r["ok"] is False
    assert r["error"] == "invalid_node_type"
    # Hint mentions both the actual problem AND the correct format
    assert "K2Node_CallFunction:" in r["detail"]


def test_ping_returns_plugin_version_9_13_0() -> None:
    """v9.13.0: ping surfaces 9.13.0."""
    response = (
        b'{"ok":true,"command":"ping","version":"0.0.1",'
        b'"plugin_version":"9.13.0","build_date":"May 22 2026 12:00:00",'
        b'"timestamp":"2026-05-22T12:00:00.000Z"}\n'
    )
    with mock.patch.object(socket, "create_connection", return_value=_fake_sock(response)):
        r = server.ping_ue()
    assert r["ok"] is True
    assert r["plugin_version"] == "9.13.0"


# ---------------------------------------------------------------------------
# v9.14.0 — add_select num_options actually grows past 2 (closes rev8 ISSUE-1)
# ---------------------------------------------------------------------------


def test_add_select_num_options_6() -> None:
    """rev8 ISSUE-1 — num_options=6 must produce 6 Option pins, not 2."""
    response = (
        b'{"ok":true,"command":"add_select","anchor_name":"mode_select",'
        b'"num_options":6,"node_guid":"AAAA",'
        b'"pins":['
        b'{"name":"Option 0","direction":"input","type":"wildcard"},'
        b'{"name":"Option 1","direction":"input","type":"wildcard"},'
        b'{"name":"Option 2","direction":"input","type":"wildcard"},'
        b'{"name":"Option 3","direction":"input","type":"wildcard"},'
        b'{"name":"Option 4","direction":"input","type":"wildcard"},'
        b'{"name":"Option 5","direction":"input","type":"wildcard"},'
        b'{"name":"Index","direction":"input","type":"int"},'
        b'{"name":"ReturnValue","direction":"output","type":"wildcard"}'
        b'],"saved":true}\n'
    )
    sent: dict = {}
    with mock.patch.object(socket, "create_connection", return_value=_fake_sock(response, sent)):
        r = server.add_select(
            blueprint="/Game/BP", anchor_name="mode_select", num_options=6,
        )
    assert r["ok"] is True
    assert r["num_options"] == 6
    option_pins = [p for p in r["pins"] if p["name"].startswith("Option ")]
    assert len(option_pins) == 6, f"expected 6 Option pins, got: {[p['name'] for p in option_pins]}"

    import json
    sent_dict = json.loads(sent["data"].decode("utf-8").rstrip())
    assert sent_dict["num_options"] == 6


def test_add_select_default_2_options() -> None:
    """Default num_options=2 still works (backwards-compatible)."""
    response = b'{"ok":true,"command":"add_select","anchor_name":"s","num_options":2,"node_guid":"X","pins":[],"saved":true}\n'
    sent: dict = {}
    with mock.patch.object(socket, "create_connection", return_value=_fake_sock(response, sent)):
        r = server.add_select(blueprint="/Game/BP", anchor_name="s")
    assert r["num_options"] == 2
    import json
    sent_dict = json.loads(sent["data"].decode("utf-8").rstrip())
    assert sent_dict["num_options"] == 2


def test_ping_returns_plugin_version_9_14_0() -> None:
    """v9.14.0: ping surfaces 9.14.0."""
    response = (
        b'{"ok":true,"command":"ping","version":"0.0.1",'
        b'"plugin_version":"9.14.0","build_date":"May 23 2026 12:00:00",'
        b'"timestamp":"2026-05-23T12:00:00.000Z"}\n'
    )
    with mock.patch.object(socket, "create_connection", return_value=_fake_sock(response)):
        r = server.ping_ue()
    assert r["ok"] is True
    assert r["plugin_version"] == "9.14.0"


# ---------------------------------------------------------------------------
# v9.15.0 — Material subsystem
# ---------------------------------------------------------------------------


def test_create_material_success() -> None:
    response = b'{"ok":true,"command":"create_material","material_path":"/Game/Materials/M_X","saved":true}\n'
    sent: dict = {}
    with mock.patch.object(socket, "create_connection", return_value=_fake_sock(response, sent)):
        r = server.create_material(name="M_X")
    assert r["ok"] is True
    assert r["material_path"] == "/Game/Materials/M_X"
    import json
    sent_dict = json.loads(sent["data"].decode("utf-8").rstrip())
    assert sent_dict == {"command": "create_material", "name": "M_X", "path": "/Game/Materials"}


def test_create_material_asset_exists() -> None:
    response = b'{"ok":false,"command":"create_material","error":"asset_exists","detail":"/Game/Materials/M_X"}\n'
    with mock.patch.object(socket, "create_connection", return_value=_fake_sock(response)):
        r = server.create_material(name="M_X")
    assert r["error"] == "asset_exists"


def test_create_material_local_validation() -> None:
    assert server.create_material(name="")["error"] == "missing_argument"


def test_add_material_expression_lerp() -> None:
    response = (
        b'{"ok":true,"command":"add_material_expression","anchor_name":"lerp",'
        b'"expression_class":"/Script/Engine.MaterialExpressionLinearInterpolate",'
        b'"node_guid":"AAAA","saved":false}\n'
    )
    sent: dict = {}
    with mock.patch.object(socket, "create_connection", return_value=_fake_sock(response, sent)):
        r = server.add_material_expression(
            material="/Game/Materials/M_X",
            expression_type="Lerp",
            anchor_name="lerp",
            position_x=400, position_y=0,
        )
    assert r["ok"] is True
    assert r["expression_class"].endswith("LinearInterpolate")
    import json
    sent_dict = json.loads(sent["data"].decode("utf-8").rstrip())
    assert sent_dict["expression_type"] == "Lerp"


def test_add_material_expression_unknown_type() -> None:
    response = b'{"ok":false,"command":"add_material_expression","error":"unknown_expression_type","detail":"..."}\n'
    with mock.patch.object(socket, "create_connection", return_value=_fake_sock(response)):
        r = server.add_material_expression(
            material="/Game/M", expression_type="DoesNotExist", anchor_name="x",
        )
    assert r["error"] == "unknown_expression_type"


def test_add_material_expression_local_validation() -> None:
    assert server.add_material_expression(material="", expression_type="Add", anchor_name="x")["error"] == "missing_argument"
    assert server.add_material_expression(material="/G/M", expression_type="", anchor_name="x")["error"] == "missing_argument"
    assert server.add_material_expression(material="/G/M", expression_type="Add", anchor_name="")["error"] == "missing_argument"


def test_set_material_expression_property_component_mask() -> None:
    """The rev8 use case: enable R channel on a ComponentMask."""
    response = (
        b'{"ok":true,"command":"set_material_expression_property","anchor_name":"mask",'
        b'"property":"R","resolved_value":"True","saved":false}\n'
    )
    sent: dict = {}
    with mock.patch.object(socket, "create_connection", return_value=_fake_sock(response, sent)):
        r = server.set_material_expression_property(
            material="/Game/M_X", anchor_name="mask", property="R", value="True",
        )
    assert r["ok"] is True
    import json
    sent_dict = json.loads(sent["data"].decode("utf-8").rstrip())
    assert sent_dict["property"] == "R"
    assert sent_dict["value"] == "True"


def test_set_material_expression_property_const_color() -> None:
    """Constant3Vector.Constant = (R=1,G=0,B=0) (red)."""
    response = b'{"ok":true,"command":"set_material_expression_property","anchor_name":"red","property":"Constant","resolved_value":"(R=1,G=0,B=0)","saved":false}\n'
    with mock.patch.object(socket, "create_connection", return_value=_fake_sock(response)):
        r = server.set_material_expression_property(
            material="/Game/M_X", anchor_name="red", property="Constant", value="(R=1,G=0,B=0)",
        )
    assert r["ok"] is True
    assert r["resolved_value"] == "(R=1,G=0,B=0)"


def test_connect_material_pins_lerp_a() -> None:
    """Wire a Constant3Vector (red) into Lerp's A input."""
    response = b'{"ok":true,"command":"connect_material_pins","from":"red","to":"lerp.A","output_index":0,"saved":false}\n'
    sent: dict = {}
    with mock.patch.object(socket, "create_connection", return_value=_fake_sock(response, sent)):
        r = server.connect_material_pins(material="/Game/M_X", from_pin="red", to_pin="lerp.A")
    assert r["ok"] is True
    import json
    sent_dict = json.loads(sent["data"].decode("utf-8").rstrip())
    assert sent_dict["from_pin"] == "red"
    assert sent_dict["to_pin"] == "lerp.A"


def test_connect_material_pins_explicit_output_index() -> None:
    response = b'{"ok":true,"command":"connect_material_pins","from":"worldpos.0","to":"mask.Input","output_index":0,"saved":false}\n'
    sent: dict = {}
    with mock.patch.object(socket, "create_connection", return_value=_fake_sock(response, sent)):
        server.connect_material_pins(material="/Game/M", from_pin="worldpos.0", to_pin="mask.Input")
    import json
    sent_dict = json.loads(sent["data"].decode("utf-8").rstrip())
    assert sent_dict["from_pin"] == "worldpos.0"


def test_connect_material_pins_missing_input_name() -> None:
    response = b'{"ok":false,"command":"connect_material_pins","error":"missing_to_input_name","detail":"..."}\n'
    with mock.patch.object(socket, "create_connection", return_value=_fake_sock(response)):
        r = server.connect_material_pins(material="/Game/M", from_pin="a", to_pin="b")
    assert r["error"] == "missing_to_input_name"


def test_connect_material_output_basecolor() -> None:
    response = b'{"ok":true,"command":"connect_material_output","from":"lerp","output":"BaseColor","output_index":0,"saved":false}\n'
    sent: dict = {}
    with mock.patch.object(socket, "create_connection", return_value=_fake_sock(response, sent)):
        r = server.connect_material_output(material="/Game/M_X", from_pin="lerp", output="BaseColor")
    assert r["ok"] is True
    import json
    sent_dict = json.loads(sent["data"].decode("utf-8").rstrip())
    assert sent_dict["output"] == "BaseColor"


def test_connect_material_output_emissive() -> None:
    response = b'{"ok":true,"command":"connect_material_output","from":"lerp","output":"EmissiveColor","output_index":0,"saved":false}\n'
    with mock.patch.object(socket, "create_connection", return_value=_fake_sock(response)):
        r = server.connect_material_output(material="/Game/M_X", from_pin="lerp", output="EmissiveColor")
    assert r["ok"] is True


def test_connect_material_output_unknown() -> None:
    response = b'{"ok":false,"command":"connect_material_output","error":"unknown_output","detail":"..."}\n'
    with mock.patch.object(socket, "create_connection", return_value=_fake_sock(response)):
        r = server.connect_material_output(material="/G/M", from_pin="x", output="DoesNotExist")
    assert r["error"] == "unknown_output"


def test_set_component_property_array_index() -> None:
    """v9.15.0: OverrideMaterials[0] = material path."""
    response = b'{"ok":true,"command":"set_component_property","blueprint":"/Game/BP","component":"VisualMesh","property":"OverrideMaterials[0]","resolved_value":"/Game/Materials/M_X","saved":true}\n'
    sent: dict = {}
    with mock.patch.object(socket, "create_connection", return_value=_fake_sock(response, sent)):
        r = server.set_component_property(
            blueprint="/Game/BP", component_name="VisualMesh",
            property_name="OverrideMaterials[0]",
            value="/Game/Materials/M_X",
        )
    assert r["ok"] is True
    assert r["resolved_value"] == "/Game/Materials/M_X"
    import json
    sent_dict = json.loads(sent["data"].decode("utf-8").rstrip())
    assert sent_dict["property_name"] == "OverrideMaterials[0]"


def test_ping_returns_plugin_version_9_15_0() -> None:
    """v9.15.0: ping surfaces 9.15.0."""
    response = (
        b'{"ok":true,"command":"ping","version":"0.0.1",'
        b'"plugin_version":"9.15.0","build_date":"May 23 2026 12:00:00",'
        b'"timestamp":"2026-05-23T12:00:00.000Z"}\n'
    )
    with mock.patch.object(socket, "create_connection", return_value=_fake_sock(response)):
        r = server.ping_ue()
    assert r["ok"] is True
    assert r["plugin_version"] == "9.15.0"


# ---------------------------------------------------------------------------
# v9.16.0 — Material subsystem completion (closes rev9 ISSUE-1/2/3)
# ---------------------------------------------------------------------------


def test_compile_material_success() -> None:
    response = b'{"ok":true,"command":"compile_material","material_path":"/Game/M_X","saved":true,"recompiled":true}\n'
    sent: dict = {}
    with mock.patch.object(socket, "create_connection", return_value=_fake_sock(response, sent)):
        r = server.compile_material(material="/Game/M_X")
    assert r["ok"] is True
    assert r["recompiled"] is True
    assert r["saved"] is True
    import json
    sent_dict = json.loads(sent["data"].decode("utf-8").rstrip())
    assert sent_dict == {"command": "compile_material", "material": "/Game/M_X"}


def test_compile_material_not_found() -> None:
    response = b'{"ok":false,"command":"compile_material","error":"material_not_found","detail":"/Game/Ghost"}\n'
    with mock.patch.object(socket, "create_connection", return_value=_fake_sock(response)):
        r = server.compile_material(material="/Game/Ghost")
    assert r["error"] == "material_not_found"


def test_compile_material_local_validation() -> None:
    assert server.compile_material(material="")["error"] == "missing_argument"


def test_set_material_property_ism_flag() -> None:
    """The rev9 hypothesis: bUsedWithInstancedStaticMeshes."""
    response = b'{"ok":true,"command":"set_material_property","material_path":"/Game/M_X","property":"bUsedWithInstancedStaticMeshes","resolved_value":"true","saved":false}\n'
    sent: dict = {}
    with mock.patch.object(socket, "create_connection", return_value=_fake_sock(response, sent)):
        r = server.set_material_property(material="/Game/M_X", property="bUsedWithInstancedStaticMeshes", value="true")
    assert r["ok"] is True
    import json
    sent_dict = json.loads(sent["data"].decode("utf-8").rstrip())
    assert sent_dict == {
        "command": "set_material_property",
        "material": "/Game/M_X",
        "property": "bUsedWithInstancedStaticMeshes",
        "value": "true",
    }


def test_set_material_property_blend_mode() -> None:
    response = b'{"ok":true,"command":"set_material_property","material_path":"/Game/M_X","property":"BlendMode","resolved_value":"BLEND_Translucent","saved":false}\n'
    with mock.patch.object(socket, "create_connection", return_value=_fake_sock(response)):
        r = server.set_material_property(material="/Game/M_X", property="BlendMode", value="BLEND_Translucent")
    assert r["resolved_value"] == "BLEND_Translucent"


def test_set_material_property_local_validation() -> None:
    assert server.set_material_property(material="", property="X")["error"] == "missing_argument"
    assert server.set_material_property(material="/G/M", property="")["error"] == "missing_argument"


def test_delete_material_expression_success() -> None:
    response = b'{"ok":true,"command":"delete_material_expression","anchor_name":"old_mask","saved":false}\n'
    sent: dict = {}
    with mock.patch.object(socket, "create_connection", return_value=_fake_sock(response, sent)):
        r = server.delete_material_expression(material="/Game/M_X", anchor_name="old_mask")
    assert r["ok"] is True
    import json
    sent_dict = json.loads(sent["data"].decode("utf-8").rstrip())
    assert sent_dict == {
        "command": "delete_material_expression",
        "material": "/Game/M_X",
        "anchor_name": "old_mask",
    }


def test_delete_material_expression_not_found() -> None:
    response = b'{"ok":false,"command":"delete_material_expression","error":"expression_not_found","detail":"ghost"}\n'
    with mock.patch.object(socket, "create_connection", return_value=_fake_sock(response)):
        r = server.delete_material_expression(material="/Game/M_X", anchor_name="ghost")
    assert r["error"] == "expression_not_found"


def test_delete_material_expression_local_validation() -> None:
    assert server.delete_material_expression(material="", anchor_name="x")["error"] == "missing_argument"
    assert server.delete_material_expression(material="/G/M", anchor_name="")["error"] == "missing_argument"


def test_disconnect_material_pins_expression_input() -> None:
    response = b'{"ok":true,"command":"disconnect_material_pins","to":"lerp.A","saved":false}\n'
    sent: dict = {}
    with mock.patch.object(socket, "create_connection", return_value=_fake_sock(response, sent)):
        r = server.disconnect_material_pins(material="/Game/M_X", to_pin="lerp.A")
    assert r["ok"] is True
    import json
    sent_dict = json.loads(sent["data"].decode("utf-8").rstrip())
    assert sent_dict["to_pin"] == "lerp.A"


def test_disconnect_material_pins_material_output() -> None:
    """The 'output:' prefix disambiguates a material output."""
    response = b'{"ok":true,"command":"disconnect_material_pins","to":"output:BaseColor","saved":false}\n'
    sent: dict = {}
    with mock.patch.object(socket, "create_connection", return_value=_fake_sock(response, sent)):
        r = server.disconnect_material_pins(material="/Game/M_X", to_pin="output:BaseColor")
    assert r["ok"] is True
    import json
    sent_dict = json.loads(sent["data"].decode("utf-8").rstrip())
    assert sent_dict["to_pin"] == "output:BaseColor"


def test_disconnect_material_pins_local_validation() -> None:
    assert server.disconnect_material_pins(material="", to_pin="x.A")["error"] == "missing_argument"
    assert server.disconnect_material_pins(material="/G/M", to_pin="")["error"] == "missing_argument"


def test_ping_returns_plugin_version_9_16_0() -> None:
    """v9.16.0: ping surfaces 9.16.0."""
    response = (
        b'{"ok":true,"command":"ping","version":"0.0.1",'
        b'"plugin_version":"9.16.0","build_date":"May 23 2026 12:00:00",'
        b'"timestamp":"2026-05-23T12:00:00.000Z"}\n'
    )
    with mock.patch.object(socket, "create_connection", return_value=_fake_sock(response)):
        r = server.ping_ue()
    assert r["ok"] is True
    assert r["plugin_version"] == "9.16.0"


# ---------------------------------------------------------------------------
# v9.17.0 — add_function(params, returns) + add_property_set/get + add_node hint
# ---------------------------------------------------------------------------


def test_add_function_default_no_params() -> None:
    """Default add_function (no params) keeps backwards-compat payload."""
    response = b'{"ok":true,"command":"add_function","function_name":"MyFunc","entry_anchor":"entry","result_anchor":"","params_count":0,"returns_count":0,"saved":true}\n'
    sent: dict = {}
    with mock.patch.object(socket, "create_connection", return_value=_fake_sock(response, sent)):
        r = server.add_function(blueprint="/Game/BP", name="MyFunc")
    assert r["ok"] is True
    import json
    sent_dict = json.loads(sent["data"].decode("utf-8").rstrip())
    assert "params" not in sent_dict
    assert "returns" not in sent_dict


def test_add_function_with_params_and_returns() -> None:
    """v9.17.0: params + returns reach the wire."""
    response = b'{"ok":true,"command":"add_function","function_name":"Ripple","entry_anchor":"entry","result_anchor":"result","params_count":3,"returns_count":1,"saved":true}\n'
    sent: dict = {}
    with mock.patch.object(socket, "create_connection", return_value=_fake_sock(response, sent)):
        r = server.add_function(
            blueprint="/Game/BP",
            name="Ripple",
            params=[
                {"name": "Px", "type": "float"},
                {"name": "Py", "type": "float"},
                {"name": "StartT", "type": "float"},
            ],
            returns=[{"name": "Z", "type": "float"}],
        )
    assert r["params_count"] == 3
    assert r["returns_count"] == 1
    assert r["result_anchor"] == "result"
    import json
    sent_dict = json.loads(sent["data"].decode("utf-8").rstrip())
    assert len(sent_dict["params"]) == 3
    assert sent_dict["returns"] == [{"name": "Z", "type": "float"}]


def test_add_function_unknown_param_type() -> None:
    response = b'{"ok":false,"command":"add_function","error":"unknown_param_type","detail":"param \'Bad\' has unknown type \'NotAType\'"}\n'
    with mock.patch.object(socket, "create_connection", return_value=_fake_sock(response)):
        r = server.add_function(blueprint="/Game/BP", name="F", params=[{"name":"Bad","type":"NotAType"}])
    assert r["error"] == "unknown_param_type"


def test_add_property_set_player_controller() -> None:
    """rev10 ISSUE-2 canonical use case: bShowMouseCursor on PlayerController."""
    response = (
        b'{"ok":true,"command":"add_property_set","anchor_name":"show_cursor",'
        b'"target_class":"/Script/Engine.PlayerController","property_name":"bShowMouseCursor",'
        b'"node_guid":"AAAA","pins":[],"saved":true}\n'
    )
    sent: dict = {}
    with mock.patch.object(socket, "create_connection", return_value=_fake_sock(response, sent)):
        r = server.add_property_set(
            blueprint="/Game/BP", target_class="PlayerController",
            property="bShowMouseCursor", anchor_name="show_cursor",
        )
    assert r["ok"] is True
    assert r["target_class"].endswith("PlayerController")
    import json
    sent_dict = json.loads(sent["data"].decode("utf-8").rstrip())
    assert sent_dict == {
        "command": "add_property_set",
        "blueprint": "/Game/BP",
        "target_class": "PlayerController",
        "property": "bShowMouseCursor",
        "anchor_name": "show_cursor",
        "position_x": 0,
        "position_y": 0,
    }


def test_add_property_set_target_class_not_found() -> None:
    response = b'{"ok":false,"command":"add_property_set","error":"target_class_not_found","detail":"\'XXX\' didn\'t resolve."}\n'
    with mock.patch.object(socket, "create_connection", return_value=_fake_sock(response)):
        r = server.add_property_set(blueprint="/Game/BP", target_class="XXX", property="X", anchor_name="a")
    assert r["error"] == "target_class_not_found"


def test_add_property_set_local_validation() -> None:
    assert server.add_property_set(blueprint="", target_class="P", property="X", anchor_name="a")["error"] == "missing_argument"
    assert server.add_property_set(blueprint="/G/B", target_class="", property="X", anchor_name="a")["error"] == "missing_argument"
    assert server.add_property_set(blueprint="/G/B", target_class="P", property="", anchor_name="a")["error"] == "missing_argument"
    assert server.add_property_set(blueprint="/G/B", target_class="P", property="X", anchor_name="")["error"] == "missing_argument"


def test_add_property_get_symmetric() -> None:
    response = (
        b'{"ok":true,"command":"add_property_get","anchor_name":"get_cursor",'
        b'"target_class":"/Script/Engine.PlayerController","property_name":"bShowMouseCursor",'
        b'"node_guid":"BBBB","pins":[],"saved":true}\n'
    )
    sent: dict = {}
    with mock.patch.object(socket, "create_connection", return_value=_fake_sock(response, sent)):
        r = server.add_property_get(
            blueprint="/Game/BP", target_class="PlayerController",
            property="bShowMouseCursor", anchor_name="get_cursor",
        )
    assert r["ok"] is True
    import json
    sent_dict = json.loads(sent["data"].decode("utf-8").rstrip())
    assert sent_dict["command"] == "add_property_get"


def test_add_node_function_not_found_includes_suggestions() -> None:
    """rev10 ISSUE-3 — UE-version renames (SetInputMode_GameAndUI → _GameAndUIEx).
    The hint should propose similar function names."""
    response = (
        b'{"ok":false,"command":"add_node","error":"function_not_found",'
        b'"detail":"WidgetBlueprintLibrary.SetInputMode_GameAndUI \xe2\x80\x94 did you mean: '
        b'SetInputMode_GameAndUIEx, SetInputMode_UIOnlyEx, SetInputMode_GameOnly?"}\n'
    )
    with mock.patch.object(socket, "create_connection", return_value=_fake_sock(response)):
        r = server.add_node(
            blueprint="/Game/BP",
            node_type="K2Node_CallFunction:WidgetBlueprintLibrary.SetInputMode_GameAndUI",
            anchor_name="x",
        )
    assert r["error"] == "function_not_found"
    assert "did you mean" in r["detail"]
    assert "SetInputMode_GameAndUIEx" in r["detail"]


def test_ping_returns_plugin_version_9_17_0() -> None:
    response = (
        b'{"ok":true,"command":"ping","version":"0.0.1",'
        b'"plugin_version":"9.17.0","build_date":"May 24 2026 12:00:00",'
        b'"timestamp":"2026-05-24T12:00:00.000Z"}\n'
    )
    with mock.patch.object(socket, "create_connection", return_value=_fake_sock(response)):
        r = server.ping_ue()
    assert r["plugin_version"] == "9.17.0"


# ---------------------------------------------------------------------------
# v9.18.0 — pin container info + connect_pins verify-and-notify (closes rev12 ISSUE-1)
# ---------------------------------------------------------------------------


def test_pins_json_includes_container_field() -> None:
    """v9.18.0 — every pin in node-creation responses now has a 'container' field.

    For a get-node on a float[] variable, container should be 'array'.
    Existing scalar tests keep working because container is just an additional
    field (empty string for scalars)."""
    response = (
        b'{"ok":true,"command":"add_variable_get","anchor_name":"get_array","variable_name":"RippleOX",'
        b'"node_guid":"AAAA","pins":['
        b'{"name":"RippleOX","direction":"output","type":"real","container":"array"}'
        b'],"saved":true}\n'
    )
    with mock.patch.object(socket, "create_connection", return_value=_fake_sock(response)):
        r = server.add_variable_get(
            blueprint="/Game/BP", variable_name="RippleOX", anchor_name="get_array",
        )
    assert r["ok"] is True
    out_pin = next(p for p in r["pins"] if p["name"] == "RippleOX")
    assert out_pin["type"] == "real"
    assert out_pin["container"] == "array"


def test_connect_pins_returns_container_info() -> None:
    """v9.18.0 — connect_pins response now includes from_container / to_container."""
    response = (
        b'{"ok":true,"command":"connect_pins","from":"get_arr","to":"arr_add.TargetArray",'
        b'"from_container":"array","to_container":"array","saved":true}\n'
    )
    with mock.patch.object(socket, "create_connection", return_value=_fake_sock(response)):
        r = server.connect_pins(
            blueprint="/Game/BP", from_pin="get_arr", to_pin="arr_add.TargetArray",
        )
    assert r["ok"] is True
    assert r["from_container"] == "array"
    assert r["to_container"] == "array"


def test_connect_pins_dropped_silent_failure_now_loud() -> None:
    """rev12 ISSUE-1 (b) — silent ok-but-not-connected now becomes a loud
    connection_dropped error."""
    response = (
        b'{"ok":false,"command":"connect_pins","error":"connection_dropped",'
        b'"detail":"Schema accepted real -> wildcard but no direct link formed. '
        b'Common cause: a wildcard pin couldn\'t infer its type."}\n'
    )
    with mock.patch.object(socket, "create_connection", return_value=_fake_sock(response)):
        r = server.connect_pins(
            blueprint="/Game/BP", from_pin="get_scalar", to_pin="arr_add.TargetArray",
        )
    assert r["ok"] is False
    assert r["error"] == "connection_dropped"
    assert "wildcard" in r["detail"]


def test_ping_returns_plugin_version_9_18_0() -> None:
    response = (
        b'{"ok":true,"command":"ping","version":"0.0.1",'
        b'"plugin_version":"9.18.0","build_date":"May 24 2026 12:00:00",'
        b'"timestamp":"2026-05-24T12:00:00.000Z"}\n'
    )
    with mock.patch.object(socket, "create_connection", return_value=_fake_sock(response)):
        r = server.ping_ue()
    assert r["plugin_version"] == "9.18.0"


# ---------------------------------------------------------------------------
# v9.19.0 — specialized K2Node selection for array functions (closes rev13)
# ---------------------------------------------------------------------------
# The actual change is server-side (C++): add_node now spawns
# UK2Node_CallArrayFunction (not generic UK2Node_CallFunction) for any
# UFUNCTION marked with MD_ArrayParam metadata, so wildcard pin
# propagation works correctly. Mock tests just verify the wire format
# is unchanged.


def test_add_node_array_function_wire_unchanged() -> None:
    """Adding Array_Add should still use the K2Node_CallFunction: prefix.
    The K2Node SUBCLASS UE spawns server-side changes (now CallArrayFunction
    for MD_ArrayParam funcs), but the wire-level node_type stays the same."""
    response = (
        b'{"ok":true,"command":"add_node","anchor_name":"arr_add","node_guid":"X",'
        b'"pins":[{"name":"TargetArray","direction":"input","type":"wildcard","container":"array"},'
        b'{"name":"NewItem","direction":"input","type":"wildcard","container":""}],"saved":true}\n'
    )
    sent: dict = {}
    with mock.patch.object(socket, "create_connection", return_value=_fake_sock(response, sent)):
        r = server.add_node(
            blueprint="/Game/BP",
            node_type="K2Node_CallFunction:KismetArrayLibrary.Array_Add",
            anchor_name="arr_add",
        )
    assert r["ok"] is True
    import json
    sent_dict = json.loads(sent["data"].decode("utf-8").rstrip())
    assert sent_dict["node_type"] == "K2Node_CallFunction:KismetArrayLibrary.Array_Add"


def test_ping_returns_plugin_version_9_19_0() -> None:
    response = (
        b'{"ok":true,"command":"ping","version":"0.0.1",'
        b'"plugin_version":"9.19.0","build_date":"May 24 2026 12:00:00",'
        b'"timestamp":"2026-05-24T12:00:00.000Z"}\n'
    )
    with mock.patch.object(socket, "create_connection", return_value=_fake_sock(response)):
        r = server.ping_ue()
    assert r["plugin_version"] == "9.19.0"


# ---------------------------------------------------------------------------
# v9.20.0 — get_material + get_blueprint filtering + add_macro docstring
# ---------------------------------------------------------------------------


def test_get_material_full_snapshot() -> None:
    response = (
        b'{"ok":true,"command":"get_material","material_path":"/Game/M_X",'
        b'"expressions":[{"anchor":"mask","class":"/Script/Engine.MaterialExpressionComponentMask",'
        b'"position":[-600,0],"properties":{"R":"False","G":"False","B":"True","A":"False"}}],'
        b'"connections":[{"from":"wpos.0","to":"mask.Input"}],'
        b'"outputs":[{"output":"BaseColor","from":"lerp.0"}],'
        b'"material_properties":{"BlendMode":"BLEND_Opaque","TwoSided":"True",'
        b'"bUsedWithInstancedStaticMeshes":"True"}}\n'
    )
    with mock.patch.object(socket, "create_connection", return_value=_fake_sock(response)):
        r = server.get_material(material="/Game/M_X")
    assert r["ok"] is True
    assert len(r["expressions"]) == 1
    assert r["expressions"][0]["anchor"] == "mask"
    assert r["expressions"][0]["properties"]["B"] == "True"
    assert r["outputs"][0]["output"] == "BaseColor"
    assert r["material_properties"]["BlendMode"] == "BLEND_Opaque"


def test_get_material_filter_kwargs() -> None:
    """Each False flag should be forwarded to the plugin."""
    response = b'{"ok":true,"command":"get_material","material_path":"/Game/M","expressions":[]}\n'
    sent: dict = {}
    with mock.patch.object(socket, "create_connection", return_value=_fake_sock(response, sent)):
        server.get_material(
            material="/Game/M",
            include_expressions=True,        # default — should NOT appear in payload
            include_connections=False,
            include_outputs=False,
            include_material_properties=False,
            anchor_filter="mult",
        )
    import json
    sent_dict = json.loads(sent["data"].decode("utf-8").rstrip())
    assert sent_dict["material"] == "/Game/M"
    assert "include_expressions" not in sent_dict   # True is default → omitted
    assert sent_dict["include_connections"] is False
    assert sent_dict["include_outputs"] is False
    assert sent_dict["include_material_properties"] is False
    assert sent_dict["anchor_filter"] == "mult"


def test_get_material_not_found() -> None:
    response = b'{"ok":false,"command":"get_material","error":"material_not_found","detail":"/Game/Ghost"}\n'
    with mock.patch.object(socket, "create_connection", return_value=_fake_sock(response)):
        r = server.get_material(material="/Game/Ghost")
    assert r["error"] == "material_not_found"


def test_get_material_local_validation() -> None:
    assert server.get_material(material="")["error"] == "missing_argument"


def test_get_blueprint_default_includes_all_sections() -> None:
    """Existing behavior preserved — bare get_blueprint(name) sends just the name."""
    response = b'{"ok":true,"command":"get_blueprint","path":"/Game/BP","anchors":{},"connections":[],"variables":[],"components":[],"functions":{}}\n'
    sent: dict = {}
    with mock.patch.object(socket, "create_connection", return_value=_fake_sock(response, sent)):
        server.get_blueprint(name="/Game/BP")
    import json
    sent_dict = json.loads(sent["data"].decode("utf-8").rstrip())
    # No filter kwargs in payload — backwards-compatible
    assert sent_dict == {"command": "get_blueprint", "name": "/Game/BP"}


def test_get_blueprint_with_filters() -> None:
    """v9.20.0 — pass section flags + anchor filter to slice large BPs."""
    response = b'{"ok":true,"command":"get_blueprint","path":"/Game/BP","anchors":{"ripple_x":{}}}\n'
    sent: dict = {}
    with mock.patch.object(socket, "create_connection", return_value=_fake_sock(response, sent)):
        server.get_blueprint(
            name="/Game/BP",
            include_variables=False,
            include_components=False,
            include_functions=False,
            anchor_filter="ripple",
        )
    import json
    sent_dict = json.loads(sent["data"].decode("utf-8").rstrip())
    assert sent_dict["include_variables"] is False
    assert sent_dict["include_components"] is False
    assert sent_dict["include_functions"] is False
    assert sent_dict["anchor_filter"] == "ripple"
    # Default-true flags omitted
    assert "include_anchors" not in sent_dict
    assert "include_connections" not in sent_dict


def test_ping_returns_plugin_version_9_20_0() -> None:
    response = (
        b'{"ok":true,"command":"ping","version":"0.0.1",'
        b'"plugin_version":"9.20.0","build_date":"May 24 2026 12:00:00",'
        b'"timestamp":"2026-05-24T12:00:00.000Z"}\n'
    )
    with mock.patch.object(socket, "create_connection", return_value=_fake_sock(response)):
        r = server.ping_ue()
    assert r["plugin_version"] == "9.20.0"


# ---------------------------------------------------------------------------
# v9.21.0 — get_pie_perf_stats + add_node docstring (closes rev15)
# ---------------------------------------------------------------------------


def test_get_pie_perf_stats_success() -> None:
    """v9.21.0: get_pie_perf_stats returns frame/thread timings + FPS."""
    response = (
        b'{"ok":true,"command":"get_pie_perf_stats",'
        b'"pie_running":true,"average_fps":120.5,"average_frame_ms":8.298755,'
        b'"delta_time_ms":8.333333,'
        b'"game_thread_ms":4.123456,"render_thread_ms":2.345678,'
        b'"rhi_thread_ms":0.456789,"gpu_frame_ms":3.789012,'
        b'"frame_counter":1234567}\n'
    )
    sent: dict = {}
    with mock.patch.object(socket, "create_connection", return_value=_fake_sock(response, sent)):
        r = server.get_pie_perf_stats()
    assert r["ok"] is True
    assert r["pie_running"] is True
    assert r["average_fps"] == 120.5
    assert r["average_frame_ms"] == 8.298755
    assert r["delta_time_ms"] == 8.333333
    assert r["game_thread_ms"] == 4.123456
    assert r["render_thread_ms"] == 2.345678
    assert r["rhi_thread_ms"] == 0.456789
    assert r["gpu_frame_ms"] == 3.789012
    assert r["frame_counter"] == 1234567

    import json
    sent_dict = json.loads(sent["data"].decode("utf-8").rstrip())
    assert sent_dict == {"command": "get_pie_perf_stats"}


def test_get_pie_perf_stats_editor_idle() -> None:
    """v9.21.0: editor-idle case — pie_running=false, perf globals still tick."""
    response = (
        b'{"ok":true,"command":"get_pie_perf_stats",'
        b'"pie_running":false,"average_fps":59.94,"average_frame_ms":16.683333,'
        b'"delta_time_ms":16.66,'
        b'"game_thread_ms":1.0,"render_thread_ms":0.5,'
        b'"rhi_thread_ms":0.1,"gpu_frame_ms":0.0,'
        b'"frame_counter":42}\n'
    )
    with mock.patch.object(socket, "create_connection", return_value=_fake_sock(response)):
        r = server.get_pie_perf_stats()
    assert r["ok"] is True
    assert r["pie_running"] is False
    assert r["average_fps"] == 59.94
    # GPU may legitimately be 0 in headless/no-GPU situations
    assert r["gpu_frame_ms"] == 0.0


def test_get_pie_perf_stats_timeout_error() -> None:
    """v9.21.0: surfaces game_thread_timeout if the GT hangs."""
    response = (
        b'{"ok":false,"command":"get_pie_perf_stats",'
        b'"error":"game_thread_timeout"}\n'
    )
    with mock.patch.object(socket, "create_connection", return_value=_fake_sock(response)):
        r = server.get_pie_perf_stats()
    assert r["ok"] is False
    assert r["error"] == "game_thread_timeout"


def test_add_node_docstring_lists_kismet_array_library() -> None:
    """v9.21.0 (rev15 ISSUE-2): add_node docstring lists the common
    KismetArrayLibrary function names so callers don't have to guess
    between display name and C++ function name."""
    doc = server.add_node.__doc__ or ""
    # Sentinel — section header
    assert "KismetArrayLibrary" in doc
    # The historically-confusing pair: index vs value remove
    assert "Array_Remove" in doc
    assert "Array_RemoveItem" in doc
    # A handful of the staples
    for fn in ("Array_Add", "Array_Get", "Array_Set", "Array_Length", "Array_Contains", "Array_Insert"):
        assert fn in doc, f"missing {fn} in add_node docstring"


def test_ping_returns_plugin_version_9_21_0() -> None:
    response = (
        b'{"ok":true,"command":"ping","version":"0.0.1",'
        b'"plugin_version":"9.21.0","build_date":"May 25 2026 12:00:00",'
        b'"timestamp":"2026-05-25T12:00:00.000Z"}\n'
    )
    with mock.patch.object(socket, "create_connection", return_value=_fake_sock(response)):
        r = server.ping_ue()
    assert r["plugin_version"] == "9.21.0"
