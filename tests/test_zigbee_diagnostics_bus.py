# privacy-check: allow-samples - tests redaction and anonymization of EUI-64s and serial device paths
"""Unit tests for Wave 2b ZigBee BusMonitor, WebSocket, Diagnostics, and Fixture Anonymizer."""
from __future__ import annotations

import collections
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from homeassistant.components import websocket_api
from homeassistant.components.diagnostics import REDACTED
from homeassistant.const import CONF_MAC
from homeassistant.core import HomeAssistant
from OWNd.message import OWNMessage, OWNSignaling
from OWNd.profiles import F454Profile, GatewayProfile, Legrand3578Profile
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.myhome.bus_monitor import BusFrame
from custom_components.myhome.const import CONF_ENTITY, CONF_PLATFORMS, CONF_SERIAL_PORT, DOMAIN
from custom_components.myhome.diagnostics import (
    _build_zigbee_inventory_diagnostics,
    _redact_serial_paths,
    async_get_config_entry_diagnostics,
)
from custom_components.myhome.websocket import _extract_gateway_info, ws_bus_monitor_send
from scripts.anonymize_plant_fixture import Anonymizer, findings
from tests.conftest import attach_runtime


def test_bus_frame_zigbee_where_and_busy_nack() -> None:
    """BusFrame extracts zigbee_where and marks *#*6## BUSY NACK as is_nack=True."""
    profile = Legrand3578Profile()

    unicast = OWNMessage.parse("*1*1*32818401#9##", gateway=profile)
    frame_u = BusFrame(direction="rx", raw="*1*1*32818401#9##", parsed=unicast)
    assert frame_u.where == "32818401#9"
    assert frame_u.is_nack is False

    grp = OWNMessage.parse("*1*1*#25#9##", gateway=profile)
    frame_g = BusFrame(direction="rx", raw="*1*1*#25#9##", parsed=grp)
    assert frame_g.where == "#25#9"

    bcast = OWNMessage.parse("*1*1*0#00#9##", gateway=profile)
    frame_b = BusFrame(direction="rx", raw="*1*1*0#00#9##", parsed=bcast)
    assert frame_b.where == "0#00#9"

    scs_act = OWNMessage.parse("*4*1*012#9##", gateway=F454Profile())
    frame_scs = BusFrame(direction="rx", raw="*4*1*012#9##", parsed=scs_act)
    assert frame_scs.where == "012"

    busy_parsed = OWNSignaling("*#*6##")
    frame_busy = BusFrame(direction="rx", raw="*#*6##", parsed=busy_parsed)
    assert frame_busy.is_ack is False
    assert frame_busy.is_nack is True

    frame_busy_raw = BusFrame(direction="rx", raw="*#*6##", parsed=None)
    assert frame_busy_raw.is_ack is False
    assert frame_busy_raw.is_nack is True


def test_websocket_extract_gateway_info_serial_and_tcp_variants() -> None:
    """Emit connection_type/context and scrub serial host/port/EUI-64."""
    zb_profile = Legrand3578Profile("1.2.4")
    serial_entry = MockConfigEntry(
        domain=DOMAIN,
        data={
            CONF_SERIAL_PORT: "/dev/serial/by-id/usb-BTicino_3578_12345-if00",
            CONF_MAC: "00:12:4b:00:01:02:03:04",
            "name": "Legrand 3578",
        },
    )
    serial_gw = SimpleNamespace(
        gateway=SimpleNamespace(
            model_name="3578",
            manufacturer="Legrand / BTicino",
            firmware="1.2.4",
            host="",
            port=0,
            profile=zb_profile,
        ),
        config_entry=serial_entry,
        mac="00:12:4b:00:01:02:03:04",
        is_serial_transport=True,
        is_zigbee_address_family=True,
        is_connected=True,
        sending_workers=[],
        send_buffer=None,
    )
    info = _extract_gateway_info(serial_gw, "2.1.0b1")
    assert info["connection_type"] == "serial"
    assert info["context"] == "zigbee"
    assert info["host"] == ""
    assert info["port"] == 0
    assert info["mac_prefix"] == "00:12:4b"
    assert "/dev/serial" not in str(info)

    # Non-colon serial MAC still emits 3-octet OUI prefix
    noncolon_serial = SimpleNamespace(
        gateway=None,
        config_entry=SimpleNamespace(data={CONF_SERIAL_PORT: "/dev/ttyUSB0"}),
        mac="00124B0001020304",
        is_serial_transport=True,
        is_zigbee_address_family=False,
        is_connected=True,
        sending_workers=[],
        send_buffer=None,
    )
    nc_info = _extract_gateway_info(noncolon_serial, "2.1.0b1")
    assert nc_info["connection_type"] == "serial"
    assert nc_info["context"] == "zigbee"
    assert nc_info["mac_prefix"] == "00:12:4b"

    # Future wired SCS serial gateway (transport_type="serial", address_family="scs")
    scs_serial_profile = GatewayProfile(
        model_name="L4686SDK",
        transport_type="serial",
        address_family="scs",
    )
    scs_serial_gw = SimpleNamespace(
        gateway=SimpleNamespace(
            model_name="L4686SDK",
            manufacturer="BTicino",
            firmware="1.2.0",
            host="",
            port=0,
            profile=scs_serial_profile,
        ),
        config_entry=SimpleNamespace(data={CONF_SERIAL_PORT: "/dev/ttyUSB1"}),
        mac="00:03:50:11:22:33",
        is_serial_transport=True,
        is_zigbee_address_family=False,
        is_connected=True,
        sending_workers=[],
        send_buffer=None,
    )
    scs_serial_info = _extract_gateway_info(scs_serial_gw, "2.1.0b1")
    assert scs_serial_info["connection_type"] == "serial"
    assert scs_serial_info["context"] == "scs"
    assert scs_serial_info["host"] == ""
    assert scs_serial_info["port"] == 0

    # Normal TCP gateway
    tcp_gw = SimpleNamespace(
        gateway=SimpleNamespace(
            model_name="F454",
            manufacturer="BTicino",
            firmware="2.0.0",
            host="192.0.2.50",
            port=20000,
            profile=F454Profile(),
        ),
        config_entry=SimpleNamespace(data={}),
        mac="00:03:50:aa:bb:cc",
        is_serial_transport=False,
        is_zigbee_address_family=False,
        is_connected=True,
        sending_workers=[],
        send_buffer=None,
    )
    tcp_info = _extract_gateway_info(tcp_gw, "2.1.0b1")
    assert tcp_info["connection_type"] == "tcp"
    assert tcp_info["context"] == "scs"
    assert tcp_info["host"] == "192.0.2.50"
    assert tcp_info["port"] == 20000


async def test_ws_bus_monitor_send_passes_gateway_profile(
    hass: HomeAssistant,
    attach_gateway,
) -> None:
    """ws_bus_monitor_send parses with gateway=gateway.profile so ZigBee frames carry context."""
    profile = Legrand3578Profile()
    gateway = MagicMock()
    gateway.profile = profile
    gateway.send = AsyncMock()
    attach_gateway("00:03:50:00:00:01:02:03", gateway)

    conn = MagicMock(spec=websocket_api.ActiveConnection)
    conn.send_result = MagicMock()
    conn.send_error = MagicMock()

    with patch(
        "custom_components.myhome.websocket.OWNMessage.parse",
        wraps=OWNMessage.parse,
    ) as parse_spy:
        ws_bus_monitor_send(
            hass,
            conn,
            {"id": 1, "type": "myhome/bus_monitor/send", "frame": "*1*1*32818401#9##"},
        )
        await hass.async_block_till_done()

    parse_spy.assert_called_once_with("*1*1*32818401#9##", gateway=profile)
    sent_msg = gateway.send.await_args.args[0]
    assert sent_msg.is_zigbee is True
    assert sent_msg.zigbee_where == "32818401#9"
    conn.send_result.assert_called_once_with(1, {"success": True, "frame": "*1*1*32818401#9##"})


@pytest.mark.asyncio
async def test_diagnostics_zigbee_redaction_inventory_and_unparsed_frames(
    hass: HomeAssistant,
) -> None:
    """Diagnostics redact serial secrets/paths and include zigbee_inventory & unparsed_raw_frames."""
    eui64 = "00:12:4b:00:aa:bb:cc:dd"
    serial_by_id = "/dev/serial/by-id/usb-BTicino_3578_ABC123-if00"
    serial_by_path = "/dev/serial/by-path/pci-0000:00:14.0-usb-0:1:1.0-port0"

    entry = MockConfigEntry(
        domain=DOMAIN,
        title="Legrand 3578",
        data={
            CONF_SERIAL_PORT: serial_by_id,
            CONF_MAC: eui64,
            "device": serial_by_path,
            "serial_number": "USB-SN-99999",
            "ieee_address": eui64,
            "name": "Legrand 3578",
        },
        options={
            "custom_note": f"plugged at {serial_by_id}",
        },
    )
    entry.add_to_hass(hass)

    inventory = SimpleNamespace(
        data={
            "eui64": eui64,
            "firmware": "1.2.4",
            "last_scan_at": "2026-11-18T12:00:00+00:00",
            "product_count": 2,
            "products": {
                "328184": {
                    "addr": "328184",
                    "reachable": True,
                    "units": {
                        "01": {"unit": "01", "where": "32818401#9", "dev_id": 258, "model": "Dimmer Switch"},
                        "02": {"unit": "02", "where": "32818402#9", "dev_id": 258, "model": "Dimmer Switch"},
                    },
                },
                "700001": {
                    "addr": "700001",
                    "reachable": False,
                    "dev_id": 513,
                    "model": "Shutter Switch",
                    "units": {},
                },
                "bad": "not-a-mapping",
            },
        }
    )
    serial_runtime = SimpleNamespace(
        unparsed_raw_frames=collections.deque(
            ["*99*1*0##", f"note {serial_by_path}"]
        )
    )
    handler = SimpleNamespace(
        gateway=SimpleNamespace(
            model_name="3578",
            manufacturer="Legrand / BTicino",
            firmware="1.2.4",
            profile=Legrand3578Profile("1.2.4"),
        ),
        is_connected=True,
        sending_workers=[object()],
        identification=lambda: {
            "model": "3578",
            "source": "serial",
            "device": serial_by_id,
            "ieee_address": eui64,
            "serial_number": "SN-1",
        },
        send_buffer=None,
        device_health=None,
        bus_monitor=None,
        zigbee_inventory=inventory,
        serial_runtime=serial_runtime,
    )
    hass.data[DOMAIN] = {eui64: {CONF_ENTITY: handler, CONF_PLATFORMS: {}}}
    attach_runtime(hass, entry, eui64, handler)

    diag = await async_get_config_entry_diagnostics(hass, entry)

    entry_data = diag["config_entry"]["data"]
    assert entry_data[CONF_SERIAL_PORT] == REDACTED
    assert entry_data[CONF_MAC] == REDACTED
    assert entry_data["device"] == REDACTED
    assert entry_data["serial_number"] == REDACTED
    assert entry_data["ieee_address"] == REDACTED
    assert diag["config_entry"]["options"]["custom_note"] == f"plugged at {REDACTED}"

    assert diag["profile"]["transport_type"] == "serial"
    assert diag["profile"]["address_family"] == "zigbee"

    inv = diag["zigbee_inventory"]
    assert inv["eui64"] == REDACTED
    assert inv["product_count"] == 2
    assert inv["reachable_count"] == 1
    assert inv["unreachable_count"] == 1
    assert inv["last_scan_at"] == "2026-11-18T12:00:00+00:00"
    assert inv["products"]["328184"] == {
        "reachable": True,
        "unit_count": 2,
        "dev_id": 258,
        "model": "Dimmer Switch",
    }
    assert inv["products"]["700001"] == {
        "reachable": False,
        "unit_count": 0,
        "dev_id": 513,
        "model": "Shutter Switch",
    }
    assert diag["unparsed_raw_frames"] == ["*99*1*0##", f"note {REDACTED}"]

    serialized = json.dumps(diag)
    assert "USB-SN-99999" not in serialized
    assert "/dev/serial" not in serialized
    assert eui64 not in serialized

    # Verify helper variants (_redact_serial_paths, as_diagnostics, products fallback, None)
    assert _redact_serial_paths([serial_by_id, 42]) == [REDACTED, 42]
    assert _build_zigbee_inventory_diagnostics(None) is None
    assert _build_zigbee_inventory_diagnostics(
        SimpleNamespace(as_diagnostics=lambda: {"eui64": eui64, "product_count": 0})
    ) == {"eui64": REDACTED, "product_count": 0}
    assert _build_zigbee_inventory_diagnostics(
        SimpleNamespace(products={"10": {"reachable": True, "units": {}}})
    ) == {
        "eui64": REDACTED,
        "product_count": 1,
        "reachable_count": 1,
        "unreachable_count": 0,
        "last_scan_at": None,
        "products": {
            "10": {"reachable": True, "unit_count": 0, "dev_id": None, "model": None}
        },
    }


def test_anonymize_plant_fixture_eui64_and_serial_paths() -> None:
    """8-octet EUI-64s and serial by-id/by-path strings are anonymized cleanly."""
    anon = Anonymizer("issue_233_3578")
    raw_text = (
        "eui=00:12:4b:00:01:02:03:04 dup=00:12:4B:00:01:02:03:04 "
        "other=00:12:4b:00:aa:bb:cc:dd mac=00:03:50:AB:CD:EF "
        "path=/dev/serial/by-id/usb-BTicino_3578_1234-if00 "
        "path2=/dev/serial/by-path/pci-0000:00:14.0-usb-0:1:1.0"
    )
    scrubbed = anon.scrub_text(raw_text)
    assert "00:03:50:00:00:00:02:33" in scrubbed
    assert "00:03:50:00:00:00:02:34" in scrubbed
    assert "mac=00:03:50:00:02:33" in scrubbed
    assert scrubbed.count("00:03:50:00:00:00:02:33") == 2
    assert "/dev/serial/by-id/usb-0000_0000-if00" in scrubbed
    assert "00:12:4b" not in scrubbed.lower()
    assert "BTicino_3578" not in scrubbed

    diag = {
        "data": {
            "config_entry": {
                "entry_id": "01REALULIDFROMTHEUSERSHOME",
                "data": {
                    "serial_port": "/dev/serial/by-id/usb-BTicino_3578-if00",
                    "serial_number": "SN-SECRET-99",
                    "mac": "00:12:4b:00:01:02:03:04",
                },
            },
            "zigbee_inventory": {
                "eui64": "00:12:4b:00:01:02:03:04",
            },
        }
    }
    clean_diag = json.loads(anon.json_text(json.dumps(diag)))
    cfg = clean_diag["data"]["config_entry"]["data"]
    assert cfg["serial_port"] == "/dev/ttyUSB0"
    assert cfg["serial_number"] is None
    assert cfg["mac"] == "00:03:50:00:00:00:02:33"
    assert clean_diag["data"]["zigbee_inventory"]["eui64"] == "00:03:50:00:00:00:02:33"
    assert findings(json.dumps(clean_diag)) == []

    # Preserves already-redacted serial_port in downloaded diagnostics
    redacted_diag = anon.json_data({"serial_port": REDACTED})
    assert redacted_diag["serial_port"] == REDACTED

    # findings() detects unredacted EUI-64 and serial by-id path
    dirty = json.dumps({
        "mac": "00:12:4b:00:01:02:03:04",
        "serial_port": "/dev/serial/by-path/pci-0000:00:14.0-usb-0:1",
    })
    hits = findings(dirty, data_file=True)
    assert "non-synthetic MAC" in hits
    assert "serial by-id path" in hits
