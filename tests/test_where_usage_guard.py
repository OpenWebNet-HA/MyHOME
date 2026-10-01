"""No raw WHERE read outside a reviewed place, and one definition of a probe.

Three times a fragment of a heating WHERE was routed as if it were a zone:
actuator ``Z#N`` (#333), pump ``0#N`` (#431), probe ``PZZ`` (#549). Each fix was
one more special case in one more place. ``where_grammar`` now holds the WHO 4
grammar; this test keeps it that way.

* Reading ``message.where`` (or ``getattr(x, "where")``) is allowed only in the
  functions listed in ``REVIEWED``, each with the reason it is safe. A new read
  fails here until someone has looked at it - then add it with a reason.
* ``PZZ`` (WHERE >= 100) is spelled out only in ``where_grammar``.
"""

from __future__ import annotations

import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent / "custom_components" / "myhome"
MESSAGE_NAMES = {"message", "msg", "parsed", "frame", "event"}

REVIEWED: dict[str, str] = {
    # Heating (WHO 4): the grammar helpers are the one place that reads a heating WHERE.
    "climate.py::_bus_zone": "WHO 4 grammar: probe and pump frames name no zone (where_grammar)",
    "climate.py::_calling_zones": "WHO 4 grammar: the zones a frame concerns",
    "climate.py::_zone_route_keys": "WHO 4 grammar: probe and pump WHERE are never route keys",
    "sensor.py::async_setup_entry.temperature_bus_address": "probe test is where_grammar.is_probe; sensors take the probe's frames",
    "sensor.py::async_setup_entry.route_keys": "sensors filter by measurement type, so a heating actuator frame reaches none",
    "gateway_events.py::GatewayEventDispatcher.process_message": "heating DIM 14 command: asks the WHERE it was sent to for its status",
    # Address derivation: the WHERE is the device's address, whichever WHO.
    "discovery.py::Address.from_message": "the address of a frame; each platform's address hook narrows it",
    "discovery.py::PlatformDiscovery.handle_message": "general (WHERE 0) broadcast test",
    "binary_sensor.py::_contact_address": "WHO 25 contact: WHERE is the address",
    "binary_sensor.py::_motion_address": "WHO 1 motion sensor: WHERE is the address",
    "sensor.py::async_setup_entry.energy_bus_address": "WHO 18: WHERE is the meter address",
    "sensor.py::async_setup_entry.illuminance_bus_address": "WHO 1 light sensor: WHERE is the address",
    "media_player.py::_zone_address": "WHO 16: WHERE is the sound zone",
    "media_player.py::MyHOMEMediaPlayer.handle_event": "WHO 16: WHERE is the sound zone",
    "cover.py::MyHOMECover.handle_event": "WHO 2 calibration: general frame test and the cover's own WHERE",
    "gateway_resync.py::LightingResyncManager.handle_ptp_echo": "WHO 1 point-to-point echo: WHERE is the APL address",
    "gateway_resync.py::LightingResyncManager.schedule_resync": "WHO 1 point-to-point echo: WHERE is the APL address",
    "gateway.py::MyHOMEGatewayHandler._correlate_shared_bus_traffic": "general-frame test for shared-bus correlation",
    "device_health.py::message_where": "WHO 1 / 1001 point frames: WHERE (+ F422 interface) is the actuator address",
    # Diagnostics only: the WHERE is shown or filtered on, never routed.
    "bus_monitor.py::BusFrame.__init__": "diagnostics record",
    "websocket.py::_matches_filter": "diagnostics filter",
}


def _functions(tree: ast.AST):
    """Yield ``(qualified name, node)`` for every node, named by its enclosing function."""

    def walk(node: ast.AST, scope: tuple[str, ...]):
        for child in ast.iter_child_nodes(node):
            inner = (
                (*scope, child.name)
                if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
                else scope
            )
            yield ".".join(inner) or "<module>", child
            yield from walk(child, inner)

    yield from walk(tree, ())


def _raw_where_reads() -> set[str]:
    found: set[str] = set()
    for path in sorted(ROOT.glob("*.py")):
        if path.name == "where_grammar.py":
            continue
        for scope, node in _functions(ast.parse(path.read_text(encoding="utf-8"))):
            attr = (
                isinstance(node, ast.Attribute)
                and node.attr == "where"
                and isinstance(node.value, ast.Name)
            )
            if attr and node.value.id in MESSAGE_NAMES:
                found.add(f"{path.name}::{scope}")
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Name)
                and node.func.id == "getattr"
                and len(node.args) >= 2
                and isinstance(node.args[1], ast.Constant)
                and node.args[1].value == "where"
            ):
                found.add(f"{path.name}::{scope}")
    return found


def test_raw_where_reads_are_reviewed():
    found = _raw_where_reads()
    unreviewed = sorted(found - REVIEWED.keys())
    assert not unreviewed, (
        "raw WHERE read outside where_grammar - route through it, or add the function to REVIEWED with a reason:\n"
        + "\n".join(unreviewed)
    )
    stale = sorted(REVIEWED.keys() - found)
    assert not stale, f"REVIEWED lists reads that no longer exist: {stale}"
    assert all(REVIEWED.values()), "every reviewed read needs a reason"


def test_probe_is_defined_once():
    """``int(x) >= 100`` on a WHERE is the probe test; it lives in where_grammar only."""
    offenders = []
    for path in sorted(ROOT.glob("*.py")):
        if path.name == "where_grammar.py":
            continue
        for _, node in _functions(ast.parse(path.read_text(encoding="utf-8"))):
            if (
                isinstance(node, ast.Compare)
                and isinstance(node.left, ast.Call)
                and getattr(node.left.func, "id", None) == "int"
                and isinstance(node.ops[0], ast.GtE)
                and isinstance(node.comparators[0], ast.Constant)
                and node.comparators[0].value == 100
            ):
                offenders.append(f"{path.name}:{node.lineno}")
    assert not offenders, f"probe test (>= 100) outside where_grammar: {offenders}"
