"""Replay and regression tests for physical BTicino H/L4684 trace (Issue #466).

Contributed by @andrea-mag in https://github.com/OpenWebNet-HA/MyHOME/issues/466#issuecomment-6398442412
(H/L4684 firmware 6.0.11, kernel 2.6.2, distro 6.0.0, WHO 13 device type 23).
"""
from __future__ import annotations

import asyncio
import hashlib
import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from homeassistant.components.climate import ClimateEntityFeature, HVACAction, HVACMode
from homeassistant.components.media_player import MediaPlayerState
from homeassistant.const import CONF_HOST, CONF_MAC, CONF_NAME, CONF_PASSWORD, CONF_PORT
from homeassistant.core import HomeAssistant, State
from homeassistant.helpers import entity_registry as er
from OWNd.message import (
    OWNCommand,
    OWNEvent,
    OWNGatewayDiagnosticEvent,
    OWNGatewayEvent,
    OWNHeatingEvent,
    OWNLoadEvent,
    OWNMessage,
)
from OWNd.profiles import GenericGatewayProfile
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.myhome.climate import MyHOMEClimate, _zone_address
from custom_components.myhome.const import (
    CONF_DEVICE_TYPE,
    CONF_ENTITY,
    CONF_FILE_PATH,
    CONF_FIRMWARE,
    CONF_SOURCE_TUNER,
    CONF_SSDP_LOCATION,
    CONF_SSDP_ST,
    DOMAIN,
    IDENTIFICATION_SSDP,
)
from custom_components.myhome.diagnostics import TO_REDACT, async_get_config_entry_diagnostics
from custom_components.myhome.gateway import (
    MyHOMEGatewayHandler,
    _profile_for_model,
    command_session_default,
    command_session_limit,
)
from custom_components.myhome.topology import gateway_supported_whos

TRACE_PATH = (
    Path(__file__).resolve().parent
    / "fixtures"
    / "traces"
    / "issue_466"
    / "config_entry-myhome_HL4684_fw6_0_11.json"
)


def _load_fixture() -> dict:
    return json.loads(TRACE_PATH.read_text(encoding="utf-8"))


def test_hl4684_trace_fixture_integrity_and_parser_coverage() -> None:
    """Every frame in the H/L4684 diagnostics trace parses cleanly and redacts file paths."""
    doc = _load_fixture()
    assert CONF_FILE_PATH in TO_REDACT
    assert doc["data"]["config_entry"]["options"][CONF_FILE_PATH] == "**REDACTED**"

    frames = doc["data"]["bus_monitor"]["recent_frames"]
    assert len(frames) == 257

    rx_frames = [f["raw"] for f in frames if f["direction"] == "rx"]
    tx_frames = [f["raw"] for f in frames if f["direction"] == "tx"]
    assert len(rx_frames) == 212
    assert len(tx_frames) == 45

    rx_whos: set[int] = set()
    who3_events: list[OWNLoadEvent] = []
    for raw in rx_frames:
        msg = OWNEvent.parse(raw) or OWNMessage.parse(raw)
        assert msg is not None, f"Failed to parse rx frame: {raw}"
        if getattr(msg, "who", None) is not None:
            rx_whos.add(int(msg.who))
        if isinstance(msg, OWNLoadEvent):
            who3_events.append(msg)

    assert rx_whos == {1, 2, 3, 4, 13, 16, 22}
    assert len(who3_events) == 29
    assert {e.where for e in who3_events} == {f"#{i}" for i in range(1, 9)}
    assert {str(e.what) for e in who3_events} == {"1"}


@pytest.mark.parametrize(
    "model",
    [
        "H/L4684",
        "HL4684",
        "HC4684",
        "H4684",
        "L4684",
        "BTicino H/L4684",
        "003594",  # F455
        "003565",  # MH200N
        "573992",  # F453AV
    ],
)
def test_scs_gateway_aliases_disable_point_to_point_zone_status(
    hass: HomeAssistant, model: str
) -> None:
    """All H/L4684 spellings and MyHOME_Suite catalogue codes disable *#4*Z## zone polling."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={
            CONF_HOST: "192.0.2.10",
            CONF_PORT: 20000,
            CONF_PASSWORD: "12345",
            CONF_MAC: "00:03:50:aa:bb:cc",
            CONF_NAME: model,
        },
    )
    handler = MyHOMEGatewayHandler(hass, entry)
    assert handler.supports_zone_status is False


@pytest.mark.parametrize(
    "model",
    [
        "003598",  # F454
        "003535",  # MH202 (#866)
        "03535",   # MH202 alternate catalogue code (#866)
    ],
)
def test_catalogue_codes_enable_point_to_point_zone_status(
    hass: HomeAssistant, model: str
) -> None:
    """F454 and MH202 catalogue codes enable *#4*Z## zone polling (#866)."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={
            CONF_HOST: "192.0.2.10",
            CONF_PORT: 20000,
            CONF_PASSWORD: "12345",
            CONF_MAC: "00:03:50:aa:bb:cc",
            CONF_NAME: model,
        },
    )
    handler = MyHOMEGatewayHandler(hass, entry)
    assert handler.supports_zone_status is True


def test_hl4684_ssdp_profile_identity_and_fw6_who22(hass: HomeAssistant) -> None:
    """SSDP H/L4684 binds H4684Profile, dynamically enables WHO 22 on FW >= 6.0.0, and corroborates code 23."""
    # Start without CONF_FIRMWARE so we also verify dynamic profile refresh on *#13**16*6*0*11##!
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="H/L4684 Gateway",
        data={
            CONF_HOST: "192.0.2.10",
            CONF_PORT: 20000,
            CONF_PASSWORD: "12345",
            CONF_MAC: "00:03:50:aa:bb:cc",
            CONF_NAME: "H/L4684",
            CONF_DEVICE_TYPE: "urn:schemas-bticino-it:device:touchscreen:1",
            CONF_SSDP_LOCATION: "http://192.0.2.10:49152/description.xml",
            CONF_SSDP_ST: "upnp:rootdevice",
        },
        options={CONF_FILE_PATH: "/config/myhome.yaml"},
    )
    entry.add_to_hass(hass)
    handler = MyHOMEGatewayHandler(hass, entry)

    assert handler.model == "H/L4684"
    assert not isinstance(handler.gateway.profile, GenericGatewayProfile)
    assert handler.gateway.profile.model_name == "H4684"
    assert command_session_limit("H/L4684") == 1
    assert command_session_default("H/L4684") == 1

    # Before firmware is known (or on 2.0.54), WHO 22 is False; WHO 16 is True; WHO 5/25 are False
    assert handler.profile_supports_who(16) is True
    assert handler.profile_supports_who(22) is False
    assert handler.profile_supports_who(5) is False
    assert handler.profile_supports_who(25) is False
    assert gateway_supported_whos("H/L4684", "6.0.11") == {1, 2, 4, 15, 16, 18, 22}
    assert gateway_supported_whos("H/L4684", "2.0.54") == {1, 2, 4, 15, 16, 18}
    # The pinned OWNd (2.0.0b13, OWNd#147) aliases the SSDP spellings itself: no MyHOME shim.
    for spelling in ("H/L4684", "HL4684", "HC4684"):
        assert _profile_for_model(spelling, "6.0.11").model_name == "H4684"
        assert _profile_for_model(spelling, "6.0.11").supports_who(22) is True
        assert _profile_for_model(spelling, "2.0.54").supports_who(22) is False

    # Replay the real WHO 13 identification sequence from the H/L4684 trace
    for raw in (
        "*#13**15*23##",
        "*#13**16*6*0*11##",
        "*#13**23*2*6*2##",
        "*#13**24*6*0*0##",
    ):
        evt = OWNEvent.parse(raw)
        assert isinstance(evt, OWNGatewayEvent)
        handler._handle_gateway_diagnostics(evt)

    # After *#13**16*6*0*11##, firmware 6.0.11 upgrades the bound H4684Profile to support WHO 22!
    assert handler.firmware == "6.0.11"
    assert handler.profile_supports_who(22) is True

    ident = handler.identification()
    assert ident["model"] == "H/L4684"
    assert ident["source"] == IDENTIFICATION_SSDP
    assert ident["who13_code"] == "23"
    assert ident["who13_model"] == "H4684"
    assert ident["who13_firmware"] == "6.0.11"
    assert ident["who13_kernel"] == "2.6.2"
    assert ident["who13_distribution"] == "6.0.0"
    assert ident["conflict"] is None

    # Official 2006 code 13 and WHO 1013 OBJECT_MODEL 19 must also corroborate H/L4684 without conflict
    evt_official = OWNEvent.parse("*#13**15*13##")
    assert isinstance(evt_official, OWNGatewayEvent)
    handler._handle_gateway_diagnostics(evt_official)
    assert handler.identification()["conflict"] is None

    evt_1013 = OWNEvent.parse("*#1013**1*19*15*5*0##")
    assert isinstance(evt_1013, OWNGatewayDiagnosticEvent)
    handler._handle_gateway_identity_diagnostics(evt_1013)
    assert handler.identification()["who1013_code"] == "19"
    assert handler.identification()["conflict"] is None


async def test_hl4684_diagnostics_redacts_config_file_path(hass: HomeAssistant) -> None:
    """Diagnostics redacts options['config_file_path'] alongside legacy 'file_path'."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={CONF_HOST: "192.0.2.10", CONF_MAC: "00:03:50:aa:bb:cc", CONF_NAME: "H/L4684"},
        options={CONF_FILE_PATH: "/config/myhome.yaml"},
    )
    entry.add_to_hass(hass)
    hass.data[DOMAIN] = {}

    diag = await async_get_config_entry_diagnostics(hass, entry)
    assert diag["config_entry"]["options"][CONF_FILE_PATH] == "**REDACTED**"


async def test_hl4684_trace_f430_4_pure_valve_vs_fancoil_zones(hass: HomeAssistant) -> None:
    """Replay WHO 4 frames from H/L4684 trace: F430/4 pure-valve zones retract tentative fan_mode."""
    doc = _load_fixture()
    rx_who4 = [
        f["raw"]
        for f in doc["data"]["bus_monitor"]["recent_frames"]
        if f["direction"] == "rx" and f["who"] == "4"
    ]
    assert len(rx_who4) == 64

    gateway = MagicMock()
    gateway.mac = "00:03:50:aa:bb:cc"
    gateway.log_id = "[H/L4684 gateway]"
    gateway.send = AsyncMock()
    gateway.send_status_request = AsyncMock()
    gateway.request_zone_dimension = MagicMock()
    gateway.has_central_unit = True
    gateway.supports_zone_status = False

    # Match @andrea-mag's plant config: zones 11, 22, 44, 55, 66, 77, 88 have fan=False;
    # zone 33 (PTE - Soggiorno) is explicitly configured with fan=True.
    zone_addrs = ("11", "22", "33", "44", "55", "66", "77", "88")
    zones: dict[str, MyHOMEClimate] = {}
    for where in zone_addrs:
        entity = MyHOMEClimate(
            hass=hass,
            name=f"Zone {where}",
            device_id=f"4-{where}",
            who="4",
            where=where,
            heating=True,
            cooling=False,
            fan=(where == "33"),
            standalone=False,
            central=False,
            manufacturer="BTicino",
            model="F430/4",
            gateway=gateway,
        )
        entity.hass = hass
        entity.entity_id = f"climate.zone_{where}"
        entity.async_write_ha_state = MagicMock()
        zones[where] = entity

    central = MyHOMEClimate(
        hass=hass,
        name="Central Unit",
        device_id="4-#0",
        who="4",
        where="#0",
        heating=True,
        cooling=False,
        fan=False,
        standalone=False,
        central=True,
        manufacturer="BTicino",
        model="3550",
        gateway=gateway,
    )
    central.hass = hass
    central.entity_id = "climate.central_unit"
    central.async_write_ha_state = MagicMock()

    for raw in rx_who4:
        evt = OWNEvent.parse(raw)
        assert isinstance(evt, OWNHeatingEvent)
        addr_obj = _zone_address(evt)
        addr = addr_obj.where if addr_obj else None
        if addr in ("#0", "0"):
            central.handle_event(evt)
        elif addr in zones:
            zones[addr].handle_event(evt)

    expected_temps = {
        "11": 20.9,
        "22": 22.4,
        "33": 21.4,
        "44": 23.3,
        "55": 22.6,
        "66": 22.8,
        "77": 22.4,
        "88": 22.1,
    }
    expected_setpoints = {
        "11": 19.5,
        "22": 17.0,
        "33": 16.0,
        "44": 18.0,
        "55": 18.0,
        "66": 17.0,
        "77": 18.0,
        "88": 18.0,
    }
    expected_actuators = {
        "11": {"1": "off"},
        "22": {"1": "off"},
        "33": {"1": "off", "2": "off"},
        "44": {"1": "off"},
        "55": {"1": "off"},
        "66": {"2": "off", "3": "off"},
        "77": {"1": "off", "2": "off"},
        "88": {"1": "off"},
    }
    for where, entity in zones.items():
        assert entity.current_temperature == expected_temps[where]
        assert entity.target_temperature == expected_setpoints[where]
        assert entity.extra_state_attributes["actuators"] == expected_actuators[where]

    # Receiving 20*5 on each zone triggered _ask_fan_coil_sides() (*#4*ZZ*19##) via request_zone_dimension:
    asked_dim19 = {
        call.args[0]
        for call in gateway.request_zone_dimension.call_args_list
        if len(call.args) >= 2 and call.args[1] == 19
    }
    assert asked_dim19 == set(zone_addrs)

    # Once Dimension 19 answers *#4*ZZ*19*0*0## (no fan-coil sides, as captured on MyHomeServer1 zones
    # 35..70 and F455 issue #674), unconfigured pure-valve zones (11, 22, 44, 55, 66, 77, 88) retract
    # tentative FAN_MODE, while explicitly configured Zone 33 (fan=True) keeps FAN_MODE:
    for where, entity in zones.items():
        entity.handle_event(OWNHeatingEvent(f"*#4*{where}*19*0*0##"))
        attrs = entity.extra_state_attributes
        assert attrs["fan_coil_heating"] is False
        assert attrs["fan_coil_cooling"] is False
        if where == "33":
            assert bool(entity.supported_features & ClimateEntityFeature.FAN_MODE)
            assert entity.fan_modes == ["auto", "low", "medium", "high"]
            assert "fan_mode" in attrs
        else:
            assert not bool(entity.supported_features & ClimateEntityFeature.FAN_MODE)
            assert entity.fan_modes is None
            assert entity.fan_mode is None
            assert "fan_mode" not in attrs

    # A subsequent *#4*11#1*20*5## or active heating valve report on pure-valve Zone 11
    # must NOT re-enable FAN_MODE, while still tracking actuator state and hvac_action.
    z11 = zones["11"]
    z11.handle_event(OWNHeatingEvent("*#4*11#1*20*5##"))
    assert not bool(z11.supported_features & ClimateEntityFeature.FAN_MODE)

    z11._attr_hvac_mode = HVACMode.HEAT
    z11.handle_event(OWNHeatingEvent("*#4*11#1*20*1##"))
    assert z11.extra_state_attributes["actuators"] == {"1": "on"}
    assert z11.hvac_action == HVACAction.HEATING
    assert not bool(z11.supported_features & ClimateEntityFeature.FAN_MODE)

    # Genuine auto-discovered fan-coil zone (e.g. F461 reporting 19*5*0 + 20*5 with fan=False)
    # MUST keep FAN_MODE enabled.
    fancoil_zone = MyHOMEClimate(
        hass=hass,
        name="Fan-Coil Zone 99",
        device_id="4-99",
        who="4",
        where="99",
        heating=True,
        cooling=False,
        fan=False,
        standalone=False,
        central=False,
        manufacturer="BTicino",
        model="F461",
        gateway=gateway,
    )
    fancoil_zone.hass = hass
    fancoil_zone.entity_id = "climate.zone_99"
    fancoil_zone.async_write_ha_state = MagicMock()
    fancoil_zone.handle_event(OWNHeatingEvent("*#4*99*19*5*0##"))
    fancoil_zone.handle_event(OWNHeatingEvent("*#4*99#1*20*5##"))
    assert bool(fancoil_zone.supported_features & ClimateEntityFeature.FAN_MODE)
    assert fancoil_zone.fan_mode == "auto"

    # Restoring stale fan_mode attributes alongside fan_coil_heating=False, fan_coil_cooling=False
    # on an unconfigured zone must NOT re-enable FAN_MODE.
    fresh_z11 = MyHOMEClimate(
        hass=hass,
        name="Zone 11",
        device_id="4-11",
        who="4",
        where="11",
        heating=True,
        cooling=False,
        fan=False,
        standalone=False,
        central=False,
        manufacturer="BTicino",
        model="F430/4",
        gateway=gateway,
    )
    await fresh_z11.async_restore_last_state(
        State(
            "climate.zone_11",
            HVACMode.HEAT,
            {
                "temperature": 19.5,
                "fan_coil_heating": False,
                "fan_coil_cooling": False,
                "fan_mode": "auto",
                "supported_features": int(
                    ClimateEntityFeature.TARGET_TEMPERATURE
                    | ClimateEntityFeature.TURN_ON
                    | ClimateEntityFeature.TURN_OFF
                    | ClimateEntityFeature.FAN_MODE
                ),
            },
        )
    )
    assert not bool(fresh_z11.supported_features & ClimateEntityFeature.FAN_MODE)
    assert fresh_z11.fan_mode is None


async def test_hl4684_trace_dual_dialect_sound_inventory_deduplication(
    hass: HomeAssistant,
) -> None:
    """Replay all 28 WHO 16 + WHO 22 frames from H/L4684 FW 6.0.11: 12 amplifier zones deduplicate."""
    doc = _load_fixture()
    sound_rx = [
        f["raw"]
        for f in doc["data"]["bus_monitor"]["recent_frames"]
        if f["direction"] == "rx" and f["who"] in ("16", "22")
    ]
    assert len(sound_rx) == 28

    mac = "00:03:50:aa:bb:cc"
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="H/L4684 Gateway",
        data={
            CONF_HOST: "192.0.2.10",
            CONF_PORT: 20000,
            CONF_PASSWORD: "12345",
            CONF_MAC: mac,
            CONF_NAME: "H/L4684",
            CONF_FIRMWARE: "6.0.11",
        },
        options={"enable_auto_learning": True},
        unique_id=mac,
    )
    entry.add_to_hass(hass)

    with (
        patch(
            "custom_components.myhome.gateway.OWNSession.test_connection",
            return_value={"Success": True},
        ),
        patch("custom_components.myhome.gateway.MyHOMEGatewayHandler.listening_loop"),
        patch("custom_components.myhome.gateway.MyHOMEGatewayHandler.sending_loop"),
    ):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

        handler: MyHOMEGatewayHandler = hass.data[DOMAIN][mac][CONF_ENTITY]
        handler._on_event_connection_state_change(True)
        await hass.async_block_till_done()

        for raw in sound_rx:
            msg = OWNEvent.parse(raw)
            assert msg is not None
            await handler._process_message(msg)
        await hass.async_block_till_done()

        ent_reg = er.async_get(hass)
        mp_entries = [
            e
            for e in er.async_entries_for_config_entry(ent_reg, entry.entry_id)
            if e.domain == "media_player"
        ]
        # 24 zone frames + 4 stereo-source frames (101/2#1, 102/2#2 are source inputs, not entities)
        # register 12 deduplicated amplifier-zone media_player entities (11..13, 21..22, 31..33, 41..42, 51, 61).
        assert len(mp_entries) == 12


async def test_hl4684_followup_traces_zone66_recovery_who1004_and_f500_tuner(
    hass: HomeAssistant,
) -> None:
    """Replay both follow-up H/L4684 traces from #466#issuecomment-6559432736."""
    trace_dir = TRACE_PATH.parent
    trace_1130 = trace_dir / "myhome_trace_HL4684_all_2026-11-01T11-30-19.json"
    trace_1148 = trace_dir / "myhome_trace_HL4684_all_2026-11-01T11-48-15.json"

    text_1130 = trace_1130.read_text(encoding="utf-8")
    text_1148 = trace_1148.read_text(encoding="utf-8")
    assert (
        hashlib.sha256(text_1130.encode("utf-8")).hexdigest()
        == "ca615a981f99d7d0e6f754f2f55b20b82f6afa043a069278232abca12bb6ae83"
    )
    assert (
        hashlib.sha256(text_1148.encode("utf-8")).hexdigest()
        == "ac5c96c9029668eeab1586a0a70c7ebd74304cff85b783ab8bc26e1348d70e18"
    )

    doc_1130 = json.loads(text_1130)
    doc_1148 = json.loads(text_1148)
    frames_1130 = doc_1130["frames"]
    frames_1148 = doc_1148["frames"]
    assert len(frames_1130) == 150
    assert len(frames_1148) == 150

    # 1. Trace 11-30-19: Zone 66#1 F430/4 actuator online (*#4*66#1*20*5##), zero retries after recovery,
    #    and physical WHO 1004 diagnostic bitmasks for probe, #0 central unit, and #ZZ actuators.
    raws_1130 = [f["raw"] for f in frames_1130]
    assert "*#4*66#1*20*5##" in raws_1130
    idx_recovery = raws_1130.index("*#4*66#1*20*5##")
    assert "*4*303*66##" in raws_1130[:idx_recovery]
    assert "*4*303*66##" not in raws_1130[idx_recovery:]
    who1004_raws = [f["raw"] for f in frames_1130 if f["who"] == "1004"]
    assert who1004_raws == [
        "*#1004*66*11*111111111111111111111111##",
        "*#1004*#66*22*1111111111111111##",
        "*#1004*#0*11*111111111111111111111111##",
    ]

    # 2. Trace 11-48-15: First physical WHO 1001 frame on H/L4684 + dual-stack F500 FM tuner session.
    raws_1148 = [f["raw"] for f in frames_1148]
    assert "*#1001*91*11*111111111111111111111111##" in raws_1148

    mac = "00:03:50:aa:bb:cc"
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="H/L4684 Gateway",
        data={
            CONF_HOST: "192.0.2.10",
            CONF_PORT: 20000,
            CONF_PASSWORD: "12345",
            CONF_MAC: mac,
            CONF_NAME: "H/L4684",
            CONF_FIRMWARE: "6.0.11",
        },
        options={
            "enable_auto_learning": True,
            CONF_SOURCE_TUNER.format(1): True,
        },
        unique_id=mac,
    )
    entry.add_to_hass(hass)

    with (
        patch(
            "custom_components.myhome.gateway.OWNSession.test_connection",
            return_value={"Success": True},
        ),
        patch("custom_components.myhome.gateway.MyHOMEGatewayHandler.listening_loop"),
        patch("custom_components.myhome.gateway.MyHOMEGatewayHandler.sending_loop"),
        patch("custom_components.myhome.gateway.MyHOMEGatewayHandler.send", new_callable=AsyncMock),
    ):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

        handler: MyHOMEGatewayHandler = hass.data[DOMAIN][mac][CONF_ENTITY]
        handler._on_event_connection_state_change(True)
        await hass.async_block_till_done()

        rds_titles_seen: list[str] = []
        tuner_states_seen: list[str] = []
        for f in frames_1148:
            if f["direction"] != "rx":
                continue
            msg = OWNMessage.parse(f["raw"])
            if isinstance(msg, OWNEvent):
                await handler._process_message(msg)
                await asyncio.sleep(0)
                tuner_state = hass.states.get("media_player.audio_source_1")
                if tuner_state is not None:
                    tuner_states_seen.append(tuner_state.state)
                    title = tuner_state.attributes.get("media_title")
                    if title and (not rds_titles_seen or rds_titles_seen[-1] != title):
                        rds_titles_seen.append(title)
        await hass.async_block_till_done()

        # Verify RDS progression ("MY HOME" -> "DIM*SOFT"), frequency 105.3 MHz, Station 5,
        # synchronized number entity, and clean standby OFF at the end of the session:
        assert "MY HOME" in rds_titles_seen
        assert "DIM*SOFT" in rds_titles_seen
        assert MediaPlayerState.ON in tuner_states_seen

        final_tuner = hass.states.get("media_player.audio_source_1")
        assert final_tuner is not None
        assert final_tuner.state == MediaPlayerState.OFF
        assert final_tuner.attributes.get("frequency") == 105.3
        assert final_tuner.attributes.get("station") == 5

        freq_number = hass.states.get("number.audio_source_1_frequency")
        assert freq_number is not None
        assert float(freq_number.state) == pytest.approx(105.3)


@pytest.mark.parametrize(
    (
        "filename",
        "expected_size",
        "expected_sha256",
        "expected_fw",
        "expected_profile",
        "expected_tx",
        "expected_rx",
    ),
    [
        (
            "myhome_trace_HL4684_all_2026-11-05T22-15-31.json",
            44949,
            "073b38d435329e5f0e8948a2d8207904c103624005974178c4b98eecf4ad8b8d",
            "6.0.11",
            "GenericGatewayProfile",
            0,
            150,
        ),
        (
            "myhome_trace_HL4684_all_2026-11-07T08-54-06.json",
            45143,
            "566179f3fda3e6bfb552c23a74f2bbbab7a326e6ecbd5270888ef2c4fccfc5ad",
            "5.0",
            "H4684Profile",
            16,
            134,
        ),
        (
            "myhome_trace_HL4684_all_2026-11-07T09-11-53.json",
            44972,
            "a31fd777b57cf73b1a7f6a6c28990fd0cdae2b7bd0ed50a89d66698d5bab2c88",
            "5.0",
            "H4684Profile",
            7,
            143,
        ),
        (
            "myhome_trace_HL4684_all_2026-11-07T09-13-19.json",
            45064,
            "e680374eb03d71adceb34e0ac5456407f29a1034afdb4f9e5100be61bdf1aae3",
            "5.0",
            "H4684Profile",
            13,
            137,
        ),
    ],
)
def test_hl4684_comment_6704994284_traces_integrity_and_parser_coverage(
    filename: str,
    expected_size: int,
    expected_sha256: str,
    expected_fw: str,
    expected_profile: str,
    expected_tx: int,
    expected_rx: int,
) -> None:
    """Verify SHA-256 integrity, gateway metadata, and 100% parser coverage for #466#issuecomment-6704994284."""
    path = TRACE_PATH.parent / filename
    raw_bytes = path.read_bytes().replace(b"\r\n", b"\n")
    assert len(raw_bytes) == expected_size
    assert hashlib.sha256(raw_bytes).hexdigest() == expected_sha256

    doc = json.loads(raw_bytes.decode("utf-8"))
    assert doc["gateway"]["model"] == "H/L4684"
    assert doc["gateway"]["firmware"] == expected_fw
    assert doc["gateway"]["identification"]["source"] == "ssdp"
    assert doc["gateway"]["identification"]["ssdp_model"] == "H/L4684"
    assert doc["gateway"]["identification"]["profile"] == expected_profile

    frames = doc["frames"]
    assert len(frames) == 150
    tx_count = sum(1 for f in frames if f["direction"] == "tx")
    rx_count = sum(1 for f in frames if f["direction"] == "rx")
    assert tx_count == expected_tx
    assert rx_count == expected_rx

    for frame in frames:
        raw = frame["raw"]
        parsed = (
            (OWNCommand.parse(raw) or OWNMessage.parse(raw))
            if frame["direction"] == "tx"
            else (OWNEvent.parse(raw) or OWNMessage.parse(raw))
        )
        assert parsed is not None, f"Failed to parse {frame['direction']} frame {raw} in {filename}"


async def test_hl4684_comment_6704994284_zone11_valve_pulse_and_f500_seek_replay(
    hass: HomeAssistant,
) -> None:
    """Replay Zone 11 heating valve pulse (22-15-31.json) and F500 manual/seek tuning (08-54-06.json)."""
    trace_dir = TRACE_PATH.parent
    doc_2215 = json.loads(
        (trace_dir / "myhome_trace_HL4684_all_2026-11-05T22-15-31.json").read_text(encoding="utf-8")
    )
    doc_0854 = json.loads(
        (trace_dir / "myhome_trace_HL4684_all_2026-11-07T08-54-06.json").read_text(encoding="utf-8")
    )

    # 1. Zone 11 short heating valve pulse (22-15-31.json: *#4*11#1*20*1## -> *#4*11#1*20*5##)
    gateway = MagicMock()
    gateway.mac = "00:03:50:aa:bb:cc"
    gateway.log_id = "[H/L4684 gateway]"
    gateway.send = AsyncMock()
    gateway.send_status_request = AsyncMock()
    gateway.request_zone_dimension = MagicMock()
    gateway.has_central_unit = True
    gateway.supports_zone_status = False

    z11 = MyHOMEClimate(
        hass=hass,
        name="Zone 11",
        device_id="4-11",
        who="4",
        where="11",
        heating=True,
        cooling=False,
        fan=False,
        standalone=False,
        central=False,
        manufacturer="BTicino",
        model="F430/4",
        gateway=gateway,
    )
    z11.hass = hass
    z11.entity_id = "climate.zone_11"
    z11.async_write_ha_state = MagicMock()
    z11.handle_event(OWNHeatingEvent("*#4*11*19*0*0##"))

    actions_seen: list[HVACAction | None] = []
    for f in doc_2215["frames"]:
        if f["direction"] != "rx" or f["who"] != "4":
            continue
        evt = OWNEvent.parse(f["raw"])
        assert isinstance(evt, OWNHeatingEvent)
        addr_obj = _zone_address(evt)
        if addr_obj and addr_obj.where == "11":
            z11.handle_event(evt)
            actions_seen.append(z11.hvac_action)

    assert HVACAction.HEATING in actions_seen
    assert z11.hvac_action == HVACAction.IDLE
    assert z11.extra_state_attributes["actuators"] == {"1": "off"}
    assert not bool(z11.supported_features & ClimateEntityFeature.FAN_MODE)

    # 2. F500 manual tune to 96.5 MHz + auto-seek UP (*16*5000*101##) -> 101.9 MHz ("DIM*ROMA")
    mac = "00:03:50:aa:bb:cc"
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="H/L4684 Gateway",
        data={
            CONF_HOST: "192.0.2.10",
            CONF_PORT: 20000,
            CONF_PASSWORD: "12345",
            CONF_MAC: mac,
            CONF_NAME: "H/L4684",
            CONF_FIRMWARE: "6.0.11",
        },
        options={
            "enable_auto_learning": True,
            CONF_SOURCE_TUNER.format(1): True,
        },
        unique_id=mac,
    )
    entry.add_to_hass(hass)

    with (
        patch(
            "custom_components.myhome.gateway.OWNSession.test_connection",
            return_value={"Success": True},
        ),
        patch("custom_components.myhome.gateway.MyHOMEGatewayHandler.listening_loop"),
        patch("custom_components.myhome.gateway.MyHOMEGatewayHandler.sending_loop"),
        patch("custom_components.myhome.gateway.MyHOMEGatewayHandler.send", new_callable=AsyncMock),
    ):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

        handler: MyHOMEGatewayHandler = hass.data[DOMAIN][mac][CONF_ENTITY]
        handler._on_event_connection_state_change(True)
        await hass.async_block_till_done()

        frequencies_seen: list[float] = []
        rds_titles_seen: list[str] = []
        for f in doc_0854["frames"]:
            if f["direction"] != "rx":
                continue
            msg = OWNMessage.parse(f["raw"])
            if isinstance(msg, OWNEvent):
                await handler._process_message(msg)
                await asyncio.sleep(0)
                tuner_state = hass.states.get("media_player.audio_source_1")
                if tuner_state is not None:
                    freq = tuner_state.attributes.get("frequency")
                    if freq is not None and (not frequencies_seen or frequencies_seen[-1] != freq):
                        frequencies_seen.append(freq)
                    title = tuner_state.attributes.get("media_title")
                    if title and (not rds_titles_seen or rds_titles_seen[-1] != title):
                        rds_titles_seen.append(title)
        await hass.async_block_till_done()

        assert frequencies_seen == [96.5, 101.9]
        assert rds_titles_seen == ["DIM*SOFT", "DIM*ROMA"]

        final_tuner = hass.states.get("media_player.audio_source_1")
        assert final_tuner is not None
        assert final_tuner.state == MediaPlayerState.OFF
        assert final_tuner.attributes.get("frequency") == 101.9

        freq_number = hass.states.get("number.audio_source_1_frequency")
        assert freq_number is not None
        assert float(freq_number.state) == pytest.approx(101.9)


