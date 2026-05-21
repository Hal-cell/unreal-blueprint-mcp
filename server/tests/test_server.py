"""Smoke + unit tests for the MCP server.

The TCP integration tests (real UE plugin) are skipped by default — they only
make sense with UE Editor running. Un-skip and run manually during spike phases.
"""

from __future__ import annotations

import socket
from unittest import mock

import pytest

from conftest import requires_ue_editor

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
