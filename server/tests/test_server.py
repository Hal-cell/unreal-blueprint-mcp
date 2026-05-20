"""Smoke + unit tests for the MCP server.

The TCP integration tests (real UE plugin) are skipped by default — they only
make sense with UE Editor running. Un-skip and run manually during spike phases.
"""

from __future__ import annotations

import socket
from unittest import mock

import pytest

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


@pytest.mark.skip(reason="Requires UE editor + BlueprintMCP plugin loaded")
def test_ping_ue_against_real_plugin() -> None:
    result = server.ping_ue()
    assert result["ok"] is True
    assert "version" in result


@pytest.mark.skip(reason="Requires UE editor + BlueprintMCP plugin loaded")
def test_create_blueprint_against_real_plugin() -> None:
    """Manual spike test: creates an asset in the running editor. Un-skip in spike B1."""
    result = server.create_blueprint(name="BP_TestSpikeB1", parent_class="Actor")
    assert result["ok"] is True
    assert result["blueprint_path"].endswith("BP_TestSpikeB1")


@pytest.mark.skip(reason="Requires UE editor + BlueprintMCP plugin + a Blueprint to add to")
def test_add_node_against_real_plugin() -> None:
    """Manual spike test: adds a PrintString node to BP_TestSpikeB1_v2. Un-skip in spike B2."""
    result = server.add_node(
        blueprint="/Game/Blueprints/BP_TestSpikeB1_v2",
        node_type="K2Node_CallFunction:PrintString",
        anchor_name="print_hello_b2_test",
        position_x=400,
        position_y=200,
    )
    assert result["ok"] is True
    assert result["function"] == "PrintString"


@pytest.mark.skip(reason="Requires UE editor + plugin + a node with anchor 'print_hello' on it")
def test_set_pin_default_against_real_plugin() -> None:
    """Manual spike test: changes print_hello.InString default to 'hello world'."""
    result = server.set_pin_default(
        blueprint="/Game/Blueprints/BP_TestSpikeB1_v2",
        pin_ref="print_hello.InString",
        value="hello world",
    )
    assert result["ok"] is True
    assert result["value"] == "hello world"


@pytest.mark.skip(reason="Requires UE editor + plugin + BP with begin_play and print_hello nodes")
def test_connect_pins_against_real_plugin() -> None:
    """Manual spike test: wires BeginPlay.then -> print_hello.execute."""
    result = server.connect_pins(
        blueprint="/Game/Blueprints/BP_TestSpikeB1_v2",
        from_pin="begin_play.then",
        to_pin="print_hello.execute",
    )
    assert result["ok"] is True


@pytest.mark.skip(reason="Requires UE editor + plugin + a complete wired BP to compile")
def test_compile_blueprint_against_real_plugin() -> None:
    """Manual spike test: compiles BP_TestSpikeB1_v2 (should be wired by B4)."""
    result = server.compile_blueprint(name="/Game/Blueprints/BP_TestSpikeB1_v2")
    assert result["ok"] is True
    assert result["status"] in ("up_to_date", "warnings")


@pytest.mark.skip(reason="Requires UE editor + plugin + a compiled Actor BP")
def test_spawn_actor_against_real_plugin() -> None:
    """Manual spike test: spawns BP_TestSpikeB1_v2 into current level."""
    result = server.spawn_actor(blueprint="/Game/Blueprints/BP_TestSpikeB1_v2")
    assert result["ok"] is True
    assert "actor_name" in result
