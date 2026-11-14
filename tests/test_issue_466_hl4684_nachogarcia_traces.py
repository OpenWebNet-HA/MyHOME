"""Replay and firmware-boundary tests for @nachogarcia's BTicino H/L4684 (FW 6.0.11) traces (#466).

Contributed on Issue #466:
- https://github.com/OpenWebNet-HA/MyHOME/issues/466#issuecomment-6682636224 (WHO 22 / WHO 16 F500 audio report)
- https://github.com/OpenWebNet-HA/MyHOME/issues/466#issuecomment-6682939809 (WHO 5, WHO 9, WHO 6 trace exports)
- https://github.com/OpenWebNet-HA/MyHOME/issues/466#issuecomment-6718123941 (art. H/L4684, FW 6.0.11, PIC 12.40.5, HW 3.0.3)
"""
from __future__ import annotations

import asyncio
import hashlib
import json
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from homeassistant.components.binary_sensor import BinarySensorDeviceClass
from homeassistant.const import (
    CONF_DEVICE_CLASS,
    CONF_HOST,
    CONF_MAC,
    CONF_NAME,
    CONF_PASSWORD,
    CONF_PORT,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.dispatcher import async_dispatcher_send
from OWNd.message import OWNCommand, OWNEvent, OWNMessage
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.myhome import alarm_control_panel, binary_sensor, lock
from custom_components.myhome.alarm_control_panel import (
    STATE_ARMED_AWAY,
    STATE_DISARMED,
    STATE_TRIGGERED,
    MyHOMEAlarmControlPanel,
)
from custom_components.myhome.const import (
    CONF_FIRMWARE,
    CONF_PLATFORMS,
    CONF_SSDP_LOCATION,
    CONF_UDN,
    CONF_WHERE,
    CONF_WHO,
    DOMAIN,
)
from custom_components.myhome.gateway import MyHOMEGatewayHandler
from tests.conftest import attach_runtime

TRACES_DIR = Path(__file__).resolve().parent / "fixtures" / "traces" / "issue_466"

WHO5_TRACE = TRACES_DIR / "myhome_trace_HL4684_who5_2026-11-06T10-37-42.json"
WHO6_TRACE = TRACES_DIR / "myhome_trace_HL4684_who6_2026-11-06T10-37-42.json"
WHO9_TRACE = TRACES_DIR / "myhome_trace_HL4684_who9_2026-11-06T10-37-42.json"


def _load_trace(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


@pytest.mark.parametrize(
    ("path", "expected_size", "expected_sha256", "expected_who", "expected_count"),
    [
        (
            WHO5_TRACE,
            14697,
            "e739549ce06037e7058d9dab167ac7acbbd469f96b1a0bb90688529c46f66d4b",
            5,
            46,
        ),
        (
            WHO6_TRACE,
            2726,
            "574b0bec4749d2748abbb4253691b56206db8c7eb38673117c2dd1bb9b499b62",
            6,
            4,
        ),
        (
            WHO9_TRACE,
            12594,
            "dfdeb462760e9861edabf257ecdec1b08c869e66f64bedd2a27798947c943e78",
            9,
            39,
        ),
    ],
)
def test_nachogarcia_hl4684_trace_integrity_and_metadata(
    path: Path,
    expected_size: int,
    expected_sha256: str,
    expected_who: int,
    expected_count: int,
) -> None:
    """Verify byte-for-byte SHA-256 integrity, UPnP metadata, and 100% parser coverage."""
    raw_bytes = path.read_bytes().replace(b"\r\n", b"\n")
    assert len(raw_bytes) == expected_size
    assert hashlib.sha256(raw_bytes).hexdigest() == expected_sha256

    doc = json.loads(raw_bytes.decode("utf-8"))
    assert doc["capture"]["filters"]["who"] == str(expected_who)
    assert doc["capture"]["window"]["truncated"] is False
    assert doc["capture"]["window"]["frames"] == expected_count
    assert len(doc["frames"]) == expected_count

    gw = doc["gateway"]
    assert gw["model"] == "HL4684"
    assert gw["firmware"] == "Unknown"
    assert gw["identification"]["source"] == "ssdp"
    assert gw["identification"]["who13_code"] == "23"
    assert gw["identification"]["profile"] == "H4684Profile"

    for frame in doc["frames"]:
        raw = frame["raw"]
        parsed = (
            (OWNCommand.parse(raw) or OWNMessage.parse(raw))
            if frame["direction"] == "tx"
            else (OWNEvent.parse(raw) or OWNMessage.parse(raw))
        )
        assert parsed is not None, f"Failed to parse {frame['direction']} frame {raw}"
        assert getattr(parsed, "who", None) == expected_who


async def test_nachogarcia_hl4684_who5_alarm_trace_replay(hass: HomeAssistant) -> None:
    """Replay all 46 WHO 5 frames: Away arming -> Zone #1 technical alarm (*5*12*#1##) -> Disarming."""
    doc = _load_trace(WHO5_TRACE)
    frames = doc["frames"]
    mac = "00:03:50:46:84:05"

    mock_gw = MagicMock()
    mock_gw.mac = mac
    mock_gw.unique_id = mac
    mock_gw.log_id = "[H/L4684 gateway - 192.0.2.84]"
    mock_gw.send = AsyncMock()
    mock_gw.send_status_request = AsyncMock()

    hass.data = {DOMAIN: {mac: {"entity": mock_gw, CONF_PLATFORMS: {alarm_control_panel.PLATFORM: {}}}}}
    entry = MagicMock()
    entry.data = {"mac": mac}
    entry.entry_id = "hl4684_who5_replay"

    added: list[MyHOMEAlarmControlPanel] = []
    with (
        patch("homeassistant.helpers.entity_registry.async_get", return_value=MagicMock()),
        patch("homeassistant.helpers.entity_registry.async_entries_for_config_entry", return_value=[]),
    ):
        attach_runtime(hass, entry)
        await alarm_control_panel.async_setup_entry(hass, entry, added.extend)

        # frames[0..10]: Away arming burst (*5*8*0##, *5*1*0##, zones #1..#8, *5*4*## battery fault)
        for frame in frames[:11]:
            msg = OWNEvent.parse(frame["raw"])
            assert msg is not None
            async_dispatcher_send(hass, f"myhome_message_{mac}", msg)
        assert len(added) == 1
        central = added[0]
        assert central.alarm_state == STATE_ARMED_AWAY

        # frames[11..22]: Pre-alarm system/zone dump keeps armed_away
        for frame in frames[11:23]:
            msg = OWNEvent.parse(frame["raw"])
            assert msg is not None
            async_dispatcher_send(hass, f"myhome_message_{mac}", msg)
        assert len(added) == 1
        assert central.alarm_state == STATE_ARMED_AWAY

        # frames[23]: *5*12*#1## (Technical alarm on Zone #1) triggers central panel without phantom #1 entity
        assert frames[23]["raw"] == "*5*12*#1##"
        async_dispatcher_send(hass, f"myhome_message_{mac}", OWNEvent.parse(frames[23]["raw"]))
        assert len(added) == 1
        assert central.alarm_state == STATE_TRIGGERED
        assert central.extra_state_attributes["raw_state"] == "technical alarm"
        assert central.extra_state_attributes["state_code"] == 12

        # frames[24..45]: Disarming burst (*5*2*0## -> *5*9*0##) + follow-up disarmed dump
        for frame in frames[24:]:
            msg = OWNEvent.parse(frame["raw"])
            assert msg is not None
            async_dispatcher_send(hass, f"myhome_message_{mac}", msg)
        assert len(added) == 1
        assert central.alarm_state == STATE_DISARMED


async def test_nachogarcia_hl4684_who9_aux_motion_trace_replay(hass: HomeAssistant) -> None:
    """Replay all 39 WHO 9 AUX frames: zero bus auto-discovery; clean ON/OFF pulses when configured."""
    doc = _load_trace(WHO9_TRACE)
    frames = doc["frames"]
    mac = "00:03:50:46:84:09"

    mock_gw = MagicMock()
    mock_gw.mac = mac
    mock_gw.unique_id = mac
    mock_gw.log_id = "[H/L4684 gateway - 192.0.2.84]"
    mock_gw.send = AsyncMock()
    mock_gw.send_status_request = AsyncMock()

    # 1. Unconfigured gateway: WHO 9 AUX frames must never auto-discover phantom entities
    hass.data = {DOMAIN: {mac: {"entity": mock_gw, CONF_PLATFORMS: {binary_sensor.PLATFORM: {}}}}}
    unconfigured_entry = MagicMock()
    unconfigured_entry.data = {CONF_MAC: mac}
    unconfigured_entry.options = {}
    unconfigured_entry.entry_id = "hl4684_who9_unconfigured"

    unconfigured_entities: list[Any] = []
    with (
        patch("homeassistant.helpers.entity_registry.async_get", return_value=MagicMock()),
        patch("homeassistant.helpers.entity_registry.async_entries_for_config_entry", return_value=[]),
    ):
        attach_runtime(hass, unconfigured_entry)
        await binary_sensor.async_setup_entry(hass, unconfigured_entry, unconfigured_entities.extend)
        for frame in frames:
            async_dispatcher_send(hass, f"myhome_message_{mac}", OWNEvent.parse(frame["raw"]))
        assert unconfigured_entities == []

    # 2. Configured YAML AUX motion sensors on WHERE="2" and WHERE="4"
    mac_cfg = "00:03:50:46:84:19"
    mock_gw_cfg = MagicMock()
    mock_gw_cfg.mac = mac_cfg
    mock_gw_cfg.unique_id = mac_cfg
    mock_gw_cfg.log_id = "[H/L4684 gateway - 192.0.2.84]"
    mock_gw_cfg.send = AsyncMock()
    mock_gw_cfg.send_status_request = AsyncMock()

    hass.data[DOMAIN][mac_cfg] = {
        "entity": mock_gw_cfg,
        CONF_PLATFORMS: {
            binary_sensor.PLATFORM: {
                "aux_2": {
                    CONF_WHO: "9",
                    CONF_WHERE: "2",
                    CONF_NAME: "Hallway Motion",
                    CONF_DEVICE_CLASS: BinarySensorDeviceClass.MOTION,
                },
                "aux_4": {
                    CONF_WHO: "9",
                    CONF_WHERE: "4",
                    CONF_NAME: "Landing Motion",
                    CONF_DEVICE_CLASS: BinarySensorDeviceClass.MOTION,
                },
            }
        },
    }
    cfg_entry = MagicMock()
    cfg_entry.data = {CONF_MAC: mac_cfg}
    cfg_entry.options = {}
    cfg_entry.entry_id = "hl4684_who9_configured"

    configured_entities: list[Any] = []
    with (
        patch("homeassistant.helpers.entity_registry.async_get", return_value=MagicMock()),
        patch("homeassistant.helpers.entity_registry.async_entries_for_config_entry", return_value=[]),
    ):
        attach_runtime(hass, cfg_entry)
        await binary_sensor.async_setup_entry(hass, cfg_entry, configured_entities.extend)
        assert len(configured_entities) == 2
        by_where = {ent._where: ent for ent in configured_entities}
        assert set(by_where) == {"2", "4"}

        on_transitions = {"2": 0, "4": 0}
        for frame in frames:
            msg = OWNEvent.parse(frame["raw"])
            assert msg is not None
            prev_2 = by_where["2"].is_on
            prev_4 = by_where["4"].is_on
            async_dispatcher_send(hass, f"myhome_message_{mac_cfg}", msg)
            if not prev_2 and by_where["2"].is_on:
                on_transitions["2"] += 1
            if not prev_4 and by_where["4"].is_on:
                on_transitions["4"] += 1

        assert on_transitions == {"2": 12, "4": 7}
        assert by_where["2"].is_on is False
        assert by_where["4"].is_on is False


async def test_nachogarcia_hl4684_who6_door_lock_trace_replay(hass: HomeAssistant) -> None:
    """Replay WHO 6 lock-release trace (*6*10*4004## & *6*10*4003##): zero bus discovery; clean unlock on echo."""
    doc = _load_trace(WHO6_TRACE)
    frames = doc["frames"]
    assert [(f["direction"], f["raw"]) for f in frames] == [
        ("tx", "*6*10*4004##"),
        ("rx", "*6*10*4004##"),
        ("tx", "*6*10*4003##"),
        ("rx", "*6*10*4003##"),
    ]

    # 1. Unconfigured bus traffic never creates unauthenticated lock entities
    mac = "00:03:50:46:84:06"
    mock_gw = MagicMock()
    mock_gw.mac = mac
    mock_gw.unique_id = mac
    mock_gw.log_id = "[H/L4684 gateway - 192.0.2.84]"
    mock_gw.profile_supports_who = MagicMock(return_value=True)

    hass.data = {DOMAIN: {mac: {"entity": mock_gw, CONF_PLATFORMS: {lock.PLATFORM: {}}}}}
    unconfigured_entry = MagicMock()
    unconfigured_entry.data = {CONF_MAC: mac}
    unconfigured_entry.entry_id = "hl4684_who6_unconfigured"

    unconfigured_locks: list[Any] = []
    with (
        patch("homeassistant.helpers.entity_registry.async_get", return_value=MagicMock()),
        patch("homeassistant.helpers.entity_registry.async_entries_for_config_entry", return_value=[]),
    ):
        attach_runtime(hass, unconfigured_entry)
        await lock.async_setup_entry(hass, unconfigured_entry, unconfigured_locks.extend)
        for frame in frames:
            if frame["direction"] == "rx":
                async_dispatcher_send(hass, f"myhome_message_{mac}", OWNEvent.parse(frame["raw"]))
        assert unconfigured_locks == []

    # 2. Configured locks on 4004 (PE5) and 4003 (PE4) unlock on the monitor echo
    mac_cfg = "00:03:50:46:84:16"
    mock_gw_cfg = MagicMock()
    mock_gw_cfg.mac = mac_cfg
    mock_gw_cfg.unique_id = mac_cfg
    mock_gw_cfg.log_id = "[H/L4684 gateway - 192.0.2.84]"
    mock_gw_cfg.profile_supports_who = MagicMock(return_value=True)

    hass.data[DOMAIN][mac_cfg] = {
        "entity": mock_gw_cfg,
        CONF_PLATFORMS: {
            lock.PLATFORM: {
                "pe5": {CONF_WHO: "6", CONF_WHERE: "4004", CONF_NAME: "Pedestrian Gate"},
                "pe4": {CONF_WHO: "6", CONF_WHERE: "4003", CONF_NAME: "Main Entrance"},
            }
        },
    }
    cfg_entry = MagicMock()
    cfg_entry.data = {CONF_MAC: mac_cfg}
    cfg_entry.entry_id = "hl4684_who6_configured"

    configured_locks: list[lock.MyHOMELock] = []
    with (
        patch("homeassistant.helpers.entity_registry.async_get", return_value=MagicMock()),
        patch("homeassistant.helpers.entity_registry.async_entries_for_config_entry", return_value=[]),
    ):
        attach_runtime(hass, cfg_entry)
        await lock.async_setup_entry(hass, cfg_entry, configured_locks.extend)
        assert len(configured_locks) == 2
        by_where = {ent._where: ent for ent in configured_locks}
        assert by_where["4004"].is_locked is True
        assert by_where["4003"].is_locked is True

        try:
            async_dispatcher_send(hass, f"myhome_message_{mac_cfg}", OWNEvent.parse(frames[1]["raw"]))
            assert by_where["4004"].is_locked is False
            assert by_where["4003"].is_locked is True

            async_dispatcher_send(hass, f"myhome_message_{mac_cfg}", OWNEvent.parse(frames[3]["raw"]))
            assert by_where["4003"].is_locked is False
        finally:
            for ent in configured_locks:
                ent._cancel_auto_relock()


@pytest.mark.parametrize(
    ("ssdp_model", "mac", "who13_code", "fw_reply", "expected_fw", "expected_who22"),
    [
        # @nachogarcia's H/L4684 6.0.11: UPnP omits modelNumber; startup probe unlocks WHO 22
        ("HL4684", "00:03:50:46:84:60", "23", "*#13**16*6*0*11##", "6.0.11", True),
        # Lower-firmware H4684 2.0.54 (deployed alongside MH200): WHO 22 stays disabled, zero MAC probe
        ("H4684", "00:03:50:46:84:20", "13", "*#13**16*2*0*54##", "2.0.54", False),
        # MH200 2.1.2: WHO 22 stays disabled, zero MAC probe, zero WHO 1013 probe
        ("MH200", "00:03:50:00:02:00", "4", "*#13**16*2*1*2##", "2.1.2", False),
    ],
)
async def test_startup_auto_detects_firmware_and_respects_h4684_mh200_safety_boundaries(
    hass: HomeAssistant,
    ssdp_model: str,
    mac: str,
    who13_code: str,
    fw_reply: str,
    expected_fw: str,
    expected_who22: bool,
) -> None:
    """SSDP/IP gateways with unknown firmware automatically probe *#13**15## + *#13**16## on startup.

    Verifies that command-session-only WHO=13 replies update CONF_FIRMWARE, device registry sw_version,
    handler.identification(), and GatewayProfile.supports_who(22), while NEVER sending *#13**12## (MAC)
    or *#1013**1## on H4684 / MH200.
    """
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={
            CONF_HOST: "192.0.2.84",
            CONF_PORT: 20000,
            CONF_PASSWORD: None,
            CONF_MAC: mac,
            CONF_NAME: ssdp_model,
            CONF_FIRMWARE: None,
            CONF_SSDP_LOCATION: "http://192.0.2.84:49152/description.xml",
            CONF_UDN: f"uuid:upnp-{ssdp_model}-{mac}",
        },
        options={},
        unique_id=mac,
    )
    entry.add_to_hass(hass)

    dev_reg = dr.async_get(hass)
    device = dev_reg.async_get_or_create(
        config_entry_id=entry.entry_id,
        connections={(dr.CONNECTION_NETWORK_MAC, mac)},
        identifiers={(DOMAIN, mac)},
        manufacturer="BTicino S.p.A.",
        name=f"{ssdp_model} Gateway",
        model=ssdp_model,
        sw_version=None,
    )

    handler = MyHOMEGatewayHandler(hass=hass, config_entry=entry)
    handler.device_registry_id = device.id
    # Before startup probe, firmware is unknown -> WHO 22 must be conservatively False on all three
    assert handler.profile.supports_who(22) is False

    sent_wire_frames: list[str] = []
    replies = {
        "*#13**15##": [OWNMessage.parse(f"*#13**15*{who13_code}##")],
        "*#13**16##": [OWNMessage.parse(fw_reply)],
    }

    cmd_session = MagicMock()
    cmd_session.is_connected = True
    cmd_session._stream_reader = object()
    cmd_session._stream_writer = object()
    cmd_session.connect = AsyncMock(return_value={"Success": True})
    cmd_session.close = AsyncMock()

    async def _send(message: Any, is_status_request: bool = False) -> list[Any]:
        raw = str(message)
        sent_wire_frames.append(raw)
        return replies[raw]

    cmd_session.send = AsyncMock(side_effect=_send)

    evt_session = MagicMock()
    evt_session.is_connected = True
    evt_session.connect = AsyncMock(return_value={"Success": True})

    async def _stop_evt() -> None:
        handler._event_runner._terminate_listener = True
        return None

    evt_session.get_next = AsyncMock(side_effect=_stop_evt)

    dummy_worker = asyncio.create_task(asyncio.sleep(0))
    handler.sending_workers.append(dummy_worker)
    try:
        with patch("custom_components.myhome.gateway.OWNCommandSession", return_value=cmd_session):
            await handler._event_runner._read_event_session(evt_session)
            await handler.send_buffer.put(None)
            await handler._command_pool.sending_loop(0)
    finally:
        await dummy_worker

    assert sent_wire_frames == ["*#13**15##", "*#13**16##"]
    assert "*#13**12##" not in sent_wire_frames
    assert "*#1013**1##" not in sent_wire_frames

    assert entry.data[CONF_FIRMWARE] == expected_fw
    assert handler.gateway.firmware == expected_fw
    assert handler.profile.supports_who(22) is expected_who22

    ident = handler.identification()
    assert ident["who13_code"] == who13_code
    assert ident["who13_firmware"] == expected_fw

    updated_device = dev_reg.async_get(device.id)
    assert updated_device is not None
    assert updated_device.sw_version == expected_fw


async def test_request_gateway_identity_skips_without_workers_and_already_known_values(hass: HomeAssistant) -> None:
    """Gateways with no active command workers or whose WHO=13 code + firmware are already populated send no startup probes."""
    idle_entry = MockConfigEntry(
        domain=DOMAIN,
        data={
            CONF_HOST: "192.0.2.84",
            CONF_PORT: 20000,
            CONF_MAC: "00:03:50:aa:bb:cc",
            CONF_NAME: "H4684",
        },
        unique_id="00:03:50:aa:bb:cc",
    )
    idle_entry.add_to_hass(hass)
    idle_handler = MyHOMEGatewayHandler(hass=hass, config_entry=idle_entry)
    idle_handler._request_gateway_identity()
    assert idle_handler.send_buffer.empty()

    ip_entry = MockConfigEntry(
        domain=DOMAIN,
        data={
            CONF_HOST: "192.0.2.85",
            CONF_PORT: 20000,
            CONF_MAC: "00:03:50:aa:bb:dd",
            CONF_NAME: "H4684",
            CONF_FIRMWARE: "2.0.54",
        },
        unique_id="00:03:50:aa:bb:dd",
    )
    ip_entry.add_to_hass(hass)
    ip_handler = MyHOMEGatewayHandler(hass=hass, config_entry=ip_entry)
    ip_handler._who13["code"] = "13"
    ip_handler._who13["firmware"] = "2.0.54"
    dummy2 = asyncio.create_task(asyncio.sleep(0))
    ip_handler.sending_workers.append(dummy2)
    try:
        ip_handler._request_gateway_identity()
        assert ip_handler.send_buffer.empty()

        # Defensive guards: unparseable command (None) and full send_buffer (QueueFull)
        ip_handler._who13["code"] = None
        ip_handler._who13["firmware"] = None
        with patch("custom_components.myhome.gateway.OWNCommand.parse", return_value=None):
            ip_handler._request_gateway_identity()
        assert ip_handler.send_buffer.empty()
        assert ip_handler._who13["pending_code"] is False

        with patch.object(ip_handler.send_buffer, "put_nowait", side_effect=asyncio.QueueFull):
            ip_handler._request_gateway_identity()
        assert ip_handler._who13["pending_code"] is False
    finally:
        await dummy2
