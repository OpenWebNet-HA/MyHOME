"""Regression tests for Issue #1017 WHO=4, dispatcher MAC casing, translations, workflow, and packaging fixes."""

from __future__ import annotations

import json
import time
import tomllib
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from homeassistant.components.climate.const import HVACAction, HVACMode
from homeassistant.components.sensor import SensorDeviceClass
from homeassistant.const import CONF_MAC, CONF_NAME
from homeassistant.core import HomeAssistant, State
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.dispatcher import async_dispatcher_send
from OWNd.message import (
    MESSAGE_TYPE_ILLUMINANCE,
    MESSAGE_TYPE_MAIN_TEMPERATURE,
    OWNEvent,
    OWNHeatingEvent,
    OWNLightingEvent,
    OWNMessage,
)
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.myhome import button as button_platform
from custom_components.myhome import number as number_platform
from custom_components.myhome import sensor as sensor_platform
from custom_components.myhome.climate import MyHOMEClimate
from custom_components.myhome.climate_discovery import CANDIDATE_MAX_AGE, ZoneDiscovery
from custom_components.myhome.const import (
    CONF_BUS_INTERFACE,
    CONF_DEVICE_CLASS,
    CONF_DEVICE_MODEL,
    CONF_ENTITY,
    CONF_MANUFACTURER,
    CONF_PLATFORMS,
    CONF_WHERE,
    CONF_WHO,
    DOMAIN,
)
from custom_components.myhome.discovery import (
    Address,
    PlatformDiscovery,
    announce_new_device,
)
from custom_components.myhome.number import MyHOMECoverMinPositionDeltaNumber
from custom_components.myhome.sensor import (
    MyHOMETemperatureSensor,
    _canonical_temperature_where,
    _spellings,
)
from custom_components.myhome.switch import MyHOMEThermoSwitch
from tests.conftest import attach_runtime

REPO_ROOT = Path(__file__).resolve().parent.parent
MAC_LOWER = "00:03:50:10:17:ab"
MAC_UPPER = "00:03:50:10:17:AB"


def _make_gateway(mac: str = MAC_LOWER) -> MagicMock:
    gateway = MagicMock()
    gateway.mac = mac
    gateway.unique_id = mac
    gateway.log_id = "[Test Gateway]"
    gateway.model = "F461"
    gateway.device_registry_id = None
    gateway.is_connected = True
    gateway.available = True
    gateway.has_central_unit = False
    gateway.supports_zone_status = True
    gateway.interface_zone_request_refused = False
    gateway.availability_signal = f"myhome_{mac}_availability"
    gateway.is_who_available.return_value = True
    gateway.send = AsyncMock()
    gateway.send_status_request = AsyncMock()
    return gateway


def _setup_sensor_entry(
    hass: HomeAssistant,
    *,
    mac: str = MAC_LOWER,
    sensors: dict[str, dict[str, object]] | None = None,
) -> tuple[MockConfigEntry, MagicMock]:
    gateway = _make_gateway(mac)
    entry = MockConfigEntry(domain=DOMAIN, data={CONF_MAC: mac}, unique_id=mac)
    entry.add_to_hass(hass)
    hass.data.setdefault(DOMAIN, {})[mac] = {
        CONF_PLATFORMS: {"sensor": sensors or {}},
        CONF_ENTITY: gateway,
    }
    attach_runtime(hass, entry)
    return entry, gateway


# ── Item 1 (#995): leading-zero YAML WHO=4 temperature sensors ("01".."09") ──


def test_who4_spellings_normalizes_leading_zero_zones_without_changing_canonical_uid() -> None:
    """_spellings() adds unpadded zone aliases for WHO=4 while _canonical_temperature_where keeps '01'..'09' intact."""
    assert _canonical_temperature_where("02") == "02"
    assert _canonical_temperature_where("02#4#01") == "02#4#01"

    assert "2" in _spellings("02", "4")
    assert "02" in _spellings("02", "4")
    assert "2" in _spellings("4-02", "4")
    assert "2#4#01" in _spellings("02#4#01", "4")
    assert "02#4#01" in _spellings("02#4#01", "4")
    assert "2#4#01" in _spellings("4-02#4#01", "4")

    # 3-digit all-probes-in-zone selector "002" must not collapse to zone "2".
    assert "2" not in _spellings("002", "4")
    # Non-WHO=4 addresses keep existing behavior.
    assert "2" not in _spellings("02", "1")


@pytest.mark.asyncio
async def test_who4_yaml_leading_zero_sensor_routes_live_local_and_f422_frames(
    hass: HomeAssistant,
) -> None:
    """Live *#4*2*0*0236## and *#4*2#4#01*0*0245## route to YAML where: '02' and '02#4#01' keeping unique IDs."""
    yaml_sensors: dict[str, dict[str, object]] = {
        "4-02": {
            CONF_WHO: "4",
            CONF_WHERE: "02",
            CONF_NAME: "Living Probe",
            CONF_DEVICE_CLASS: SensorDeviceClass.TEMPERATURE,
            CONF_MANUFACTURER: "BTicino",
            CONF_DEVICE_MODEL: "LN4691",
        },
        "4-02#4#01": {
            CONF_WHO: "4",
            CONF_WHERE: "02#4#01",
            CONF_NAME: "Annex Probe",
            CONF_DEVICE_CLASS: SensorDeviceClass.TEMPERATURE,
            CONF_MANUFACTURER: "BTicino",
            CONF_DEVICE_MODEL: "LN4691",
        },
        "4-03#4#02": {
            CONF_WHO: "4",
            CONF_WHERE: "03",
            CONF_BUS_INTERFACE: "02",
            CONF_NAME: "Cellar Probe",
            CONF_DEVICE_CLASS: SensorDeviceClass.TEMPERATURE,
            CONF_MANUFACTURER: "BTicino",
            CONF_DEVICE_MODEL: "LN4691",
        },
    }
    entry, _gateway = _setup_sensor_entry(hass, sensors=yaml_sensors)
    # Pre-existing registry entry under unpadded "2" must not duplicate YAML "02".
    er.async_get(hass).async_get_or_create(
        "sensor",
        DOMAIN,
        f"{MAC_LOWER}-4-2-temperature",
        suggested_object_id="zone_2_temperature",
        config_entry=entry,
        original_device_class=SensorDeviceClass.TEMPERATURE,
    )

    added: list[MyHOMETemperatureSensor] = []
    assert await sensor_platform.async_setup_entry(hass, entry, added.extend) is True
    await hass.async_block_till_done()

    by_uid = {s.unique_id: s for s in added}
    assert set(by_uid) == {
        f"{MAC_LOWER}-4-02-temperature",
        f"{MAC_LOWER}-4-02#4#01-temperature",
        f"{MAC_LOWER}-4-03#4#02-temperature",
    }

    local_sensor = by_uid[f"{MAC_LOWER}-4-02-temperature"]
    routed_inline_sensor = by_uid[f"{MAC_LOWER}-4-02#4#01-temperature"]
    routed_opt_sensor = by_uid[f"{MAC_LOWER}-4-03#4#02-temperature"]

    # Live local frame *#4*2*0*0236## updates YAML where: "02" and does not touch "02#4#01".
    async_dispatcher_send(
        hass, f"myhome_message_{MAC_LOWER}", OWNEvent.parse("*#4*2*0*0236##")
    )
    await hass.async_block_till_done()
    assert local_sensor.native_value == 23.6
    assert routed_inline_sensor.native_value is None

    # Live F422 routed frame *#4*2#4#01*0*0245## updates YAML where: "02#4#01" and leaves local "02" alone.
    async_dispatcher_send(
        hass, f"myhome_message_{MAC_LOWER}", OWNEvent.parse("*#4*2#4#01*0*0245##")
    )
    await hass.async_block_till_done()
    assert routed_inline_sensor.native_value == 24.5
    assert local_sensor.native_value == 23.6

    # Live F422 routed frame *#4*3#4#02*0*0182## updates YAML where: "03" + bus_interface: "02".
    async_dispatcher_send(
        hass, f"myhome_message_{MAC_LOWER}", OWNEvent.parse("*#4*3#4#02*0*0182##")
    )
    await hass.async_block_till_done()
    assert routed_opt_sensor.native_value == 18.2
    assert len(added) == 3

    # Active fallback polls use canonical unpadded zone WHEREs (*#4*2*0##, *#4*2#4#01*0##, *#4*3#4#02*0##)
    # while leaving probes (105) and 3-digit selectors (002) untouched.
    assert str(local_sensor._fallback_read()) == "*#4*2*0##"
    assert str(routed_inline_sensor._fallback_read()) == "*#4*2#4#01*0##"
    assert str(routed_opt_sensor._fallback_read()) == "*#4*3#4#02*0##"
    probe_sensor = MyHOMETemperatureSensor(
        hass=hass,
        device_id="4-105",
        who="4",
        where="105",
        name="Probe 105",
        device_class=SensorDeviceClass.TEMPERATURE,
        manufacturer="BTicino",
        model="Temperature Probe",
        gateway=_gateway,
    )
    assert str(probe_sensor._fallback_read()) == "*#4*105*0##"
    selector_sensor = MyHOMETemperatureSensor(
        hass=hass,
        device_id="4-002",
        who="4",
        where="002",
        name="Selector 002",
        device_class=SensorDeviceClass.TEMPERATURE,
        manufacturer="BTicino",
        model="Temperature Probe",
        gateway=_gateway,
    )
    assert str(selector_sensor._fallback_read()) == "*#4*002*0##"


# ── Item 12 (#972/#983): temperature_bus_address foreign '#' and out-of-range interface guard ──


@pytest.mark.asyncio
async def test_temperature_bus_address_rejects_foreign_hash_and_out_of_range_interface(
    hass: HomeAssistant,
) -> None:
    """Reject foreign '#' suffixes (105#1) and out-of-range interfaces (105#4#99) in temperature_bus_address()."""
    entry, _gateway = _setup_sensor_entry(hass, mac="00:03:50:10:17:12")
    added: list[MyHOMETemperatureSensor] = []

    with patch(
        "custom_components.myhome.sensor.PlatformDiscovery", wraps=PlatformDiscovery
    ) as mock_pd:
        assert await sensor_platform.async_setup_entry(hass, entry, added.extend) is True
        await hass.async_block_till_done()

    pd_4 = next(call.kwargs for call in mock_pd.call_args_list if call.kwargs.get("who") == "4")
    temp_bus_address = pd_4["address"]

    # 1. Real OWNd frames with foreign '#' suffix or out-of-range interface -> rejected by bus_interface_from_message.
    assert temp_bus_address(OWNEvent.parse("*#4*105#1*0*0210##")) is None
    assert temp_bus_address(OWNEvent.parse("*#4*105#4#99*0*0210##")) is None

    # 2. Synthetic message with inline foreign '#' (no BUS_ROUTING) -> rejected by '#' in where guard.
    foreign_inline = SimpleNamespace(
        where="105#1",
        where_param=[],
        interface=None,
        dimension=0,
        message_type=MESSAGE_TYPE_MAIN_TEMPERATURE,
    )
    assert temp_bus_address(foreign_inline) is None

    # 3. Synthetic messages containing '#4#' where Address.from_device_id leaves '#' in addr.where.
    out_of_range_inline = SimpleNamespace(
        where="105#4#99",
        where_param=[],
        interface=None,
        dimension=0,
        message_type=MESSAGE_TYPE_MAIN_TEMPERATURE,
    )
    assert temp_bus_address(out_of_range_inline) is None

    compound_foreign_inline = SimpleNamespace(
        where="105#1#4#01",
        where_param=[],
        interface=None,
        dimension=0,
        message_type=MESSAGE_TYPE_MAIN_TEMPERATURE,
    )
    assert temp_bus_address(compound_foreign_inline) is None

    # 4. Valid routed probe frames (real OWNd and inline synthetic) remain accepted.
    assert temp_bus_address(OWNEvent.parse("*#4*105#4#01*0*0210##")) == Address("105", "01")
    valid_inline = SimpleNamespace(
        where="0105#4#02",
        where_param=[],
        interface=None,
        dimension=0,
        message_type=MESSAGE_TYPE_MAIN_TEMPERATURE,
    )
    assert temp_bus_address(valid_inline) == Address("105", "02")

    # End-to-end bus dispatch of malformed frames never creates a phantom sensor.
    for raw in ("*#4*105#1*0*0210##", "*#4*105#1*0*0215##", "*#4*105#4#99*0*0210##", "*#4*105#4#99*0*0215##"):
        async_dispatcher_send(hass, "myhome_message_00:03:50:10:17:12", OWNEvent.parse(raw))
    await hass.async_block_till_done()
    assert added == []


# ── Item 23 (#972/#983): empty WHERE guard on *#1**6*250## in illuminance_bus_address ──


@pytest.mark.asyncio
async def test_illuminance_bus_address_rejects_empty_and_whitespace_where(
    hass: HomeAssistant,
) -> None:
    """*#1**6*250## and empty/whitespace/None WHERE frames never create phantom illuminance sensors."""
    mac = "00:03:50:10:17:23"
    entry, _gateway = _setup_sensor_entry(hass, mac=mac)
    added: list[object] = []

    with patch(
        "custom_components.myhome.sensor.PlatformDiscovery", wraps=PlatformDiscovery
    ) as mock_pd:
        assert await sensor_platform.async_setup_entry(hass, entry, added.extend) is True
        await hass.async_block_till_done()

    pd_1 = next(call.kwargs for call in mock_pd.call_args_list if call.kwargs.get("who") == "1")
    illum_bus_address = pd_1["address"]

    # Real OWNd frame *#1**6*250##
    parsed_empty = OWNEvent.parse("*#1**6*250##")
    assert parsed_empty is not None
    assert illum_bus_address(parsed_empty) is None

    # Synthetic frames with is_general=False and empty / whitespace / None WHERE
    for bad_where in ("", "   ", None):
        synthetic = SimpleNamespace(
            who="1",
            where=bad_where,
            where_param=[],
            interface=None,
            is_general=False,
            dimension=6,
            message_type=MESSAGE_TYPE_ILLUMINANCE,
            illuminance=250,
        )
        assert illum_bus_address(synthetic) is None

    # Dispatching real *#1**6*250## end-to-end creates nothing.
    async_dispatcher_send(hass, f"myhome_message_{mac}", parsed_empty)
    await hass.async_block_till_done()
    assert added == []

    # Valid illuminance frame still discovers normally.
    valid_evt = OWNLightingEvent("*#1*21*6*0250##")
    assert illum_bus_address(valid_evt) == Address("21", None)
    async_dispatcher_send(hass, f"myhome_message_{mac}", valid_evt)
    await hass.async_block_till_done()
    assert len(added) == 1


# ── Item 10 (#955): dynamic cool mode learning from WHO=4 Dimension 7 ──


def test_climate_dimension_7_cooling_context_learns_cool_mode(hass: HomeAssistant) -> None:
    """Dimension 7 with zone_context == 'cooling' dynamically enables HVACMode.COOL on an unconfigured zone."""
    gateway = _make_gateway("00:03:50:10:17:10")
    zone = MyHOMEClimate(
        hass=hass,
        name="Climate Zone 2",
        device_id="2",
        who="4",
        where="2",
        heating=True,
        cooling=None,
        fan=False,
        standalone=None,
        central=False,
        manufacturer="BTicino",
        model="Heating Zone",
        gateway=gateway,
    )
    zone.entity_id = "climate.zone_2"
    zone.async_write_ha_state = MagicMock()

    assert zone._cooling_configured is False
    assert zone._cooling is False
    assert HVACMode.COOL not in zone.hvac_modes

    # Live F461 Dimension 7 frame: cooling comfort (*#4*2*7*2*3##)
    cooling_dim7 = OWNHeatingEvent("*#4*2*7*2*3##")
    assert cooling_dim7.zone_context == "cooling"
    assert cooling_dim7.zone_state == "comfort"

    zone.handle_event(cooling_dim7)

    assert zone._cooling is True
    assert HVACMode.COOL in zone.hvac_modes
    assert zone.hvac_mode == HVACMode.COOL
    assert zone.extra_state_attributes.get("cooling_seen") is True

    # Already-cooling zone handles a second Dimension 7 cooling frame without error.
    zone.handle_event(OWNHeatingEvent("*#4*2*7*2*1*0240##"))
    assert zone.hvac_mode == HVACMode.COOL
    assert zone.native_target_temperature == 24.0

    # Explicit cool=False in config must NOT be overridden by Dimension 7 cooling context.
    heating_only = MyHOMEClimate(
        hass=hass,
        name="Heating Only Zone 3",
        device_id="3",
        who="4",
        where="3",
        heating=True,
        cooling=False,
        fan=False,
        standalone=None,
        central=False,
        manufacturer="BTicino",
        model="Heating Zone",
        gateway=gateway,
    )
    heating_only.entity_id = "climate.zone_3"
    heating_only.async_write_ha_state = MagicMock()
    heating_only.handle_event(OWNHeatingEvent("*#4*3*7*2*3##"))
    assert heating_only._cooling_configured is True
    assert heating_only._cooling is False
    assert HVACMode.COOL not in heating_only.hvac_modes
    assert heating_only.hvac_mode is None

    # Central unit calling _apply_zone_state directly never mutates _cooling.
    central = MyHOMEClimate(
        hass=hass,
        name="Central Unit",
        device_id="#0",
        who="4",
        where="#0",
        heating=True,
        cooling=None,
        fan=False,
        standalone=False,
        central=True,
        manufacturer="BTicino",
        model="3550",
        gateway=gateway,
    )
    central._cooling = False
    central._cooling_configured = False
    central._apply_zone_state(cooling_dim7)
    assert central._cooling is False
    assert central._attr_hvac_action != HVACAction.COOLING


# ── Item 22 (#971/#983): _accept_candidate_actuator clears _candidates_unanswered ──


def test_accept_candidate_actuator_clears_candidates_unanswered_on_confirm_and_expiry(
    hass: HomeAssistant,
) -> None:
    """_accept_candidate_actuator discards zone_pair from _candidates_unanswered on both confirm and expiry."""
    entry = MagicMock(subentries={}, options={})
    gateway = _make_gateway("00:03:50:10:17:22")
    tracker = ZoneDiscovery(
        hass,
        entry,
        SimpleNamespace(gateway=gateway, platforms={"climate": {}}),
        lambda ctx, cfg: MagicMock(),
    )

    now = time.monotonic()
    confirm_pair = ("5", None)
    expire_pair = ("6", None)

    # Confirmation path: non-expired candidate in _candidates_unanswered
    tracker._candidates[confirm_pair] = (now, "*#4*5*0*0210##")
    tracker._candidates_unanswered.add(confirm_pair)
    assert tracker._accept_candidate_actuator(confirm_pair) is True
    assert confirm_pair not in tracker._candidates
    assert confirm_pair not in tracker._candidates_unanswered

    # Expiry path: expired candidate in _candidates_unanswered with held non-actuator frame
    tracker._candidates[expire_pair] = (now - CANDIDATE_MAX_AGE - 10.0, "*#4*6*0*0210##")
    tracker._candidates_unanswered.add(expire_pair)
    tracker._held[expire_pair] = {"dim:14": OWNHeatingEvent("*#4*6*14*0210*1##")}
    assert tracker._accept_candidate_actuator(expire_pair) is False
    assert expire_pair not in tracker._candidates
    assert expire_pair not in tracker._candidates_unanswered
    assert expire_pair not in tracker._held

    # Non-candidate pair returns False cleanly
    assert tracker._accept_candidate_actuator(("7", None)) is False


# ── Item 11 (#983): MyHOMEThermoSwitch.async_restore_last_state preserves live Z#0 ON/OFF ──


def _make_thermo_switch(hass: HomeAssistant, gateway: MagicMock) -> MyHOMEThermoSwitch:
    sw = MyHOMEThermoSwitch(
        hass=hass,
        name="Towel Warmer",
        entity_name=None,
        icon="mdi:radiator-off",
        icon_on="mdi:radiator",
        device_id="4-3",
        where="3",
        zone="3",
        actuator=None,
        interface=None,
        device_class=None,
        manufacturer="BTicino",
        model="F430",
        turn_on_command=None,
        turn_off_command=None,
        gateway=gateway,
    )
    sw.hass = hass
    sw.entity_id = "switch.towel_warmer"
    sw.async_write_ha_state = MagicMock()
    return sw


@pytest.mark.asyncio
async def test_thermo_switch_restore_preserves_live_pre_restore_aggregate_on_and_off(
    hass: HomeAssistant,
) -> None:
    """Live pre-restore Z#0 aggregate ON/OFF survives async_restore_last_state while recovering proven actuators."""
    # Case A: Live pre-restore aggregate ON (*#4*3#0*20*1##), saved proven actuator "1" OFF.
    gw_on = _make_gateway("00:03:50:10:17:11")
    sw_on = _make_thermo_switch(hass, gw_on)
    sw_on.handle_event(OWNMessage.parse("*#4*3#0*20*1##"))
    assert sw_on.is_on is True
    assert sw_on._actuator_states == {"1": True}
    assert sw_on._aggregate_actuators == {"1"}

    await sw_on.async_restore_last_state(
        State(
            "switch.towel_warmer",
            "off",
            {"actuators": {"1": "off", "9": "bogus"}, "aggregate_actuators": []},
        )
    )
    assert sw_on.is_on is True
    assert sw_on._actuator_states == {"1": True}
    assert sw_on._aggregate_actuators == set()
    assert sw_on._attr_icon == "mdi:radiator"

    await sw_on.async_turn_off()
    assert [str(c.args[0]) for c in gw_on.send.await_args_list] == ["*#4*3#1*#20*0##"]

    # Case B: Live pre-restore aggregate OFF (*#4*3#0*20*0##), saved proven actuators "1" & "2" ON.
    gw_off = _make_gateway("00:03:50:10:17:11")
    sw_off = _make_thermo_switch(hass, gw_off)
    sw_off.handle_event(OWNMessage.parse("*#4*3#0*20*0##"))
    assert sw_off.is_on is False
    assert sw_off._actuator_states == {"1": False}
    assert sw_off._aggregate_actuators == {"1"}

    await sw_off.async_restore_last_state(
        State(
            "switch.towel_warmer",
            "on",
            {"actuators": {"1": "on", "2": "on"}, "aggregate_actuators": []},
        )
    )
    assert sw_off.is_on is False
    assert sw_off._actuator_states == {"1": False, "2": False}
    assert sw_off._aggregate_actuators == set()
    assert sw_off._attr_icon == "mdi:radiator-off"

    await sw_off.async_turn_on()
    assert [str(c.args[0]) for c in gw_off.send.await_args_list] == [
        "*#4*3#1*#20*1##",
        "*#4*3#2*#20*1##",
    ]

    # Case C: Live pre-restore aggregate ON + saved aggregate "1" stays aggregate, saved proven "2" recovered ON.
    gw_mix = _make_gateway("00:03:50:10:17:11")
    sw_mix = _make_thermo_switch(hass, gw_mix)
    sw_mix.handle_event(OWNMessage.parse("*#4*3#0*20*1##"))
    await sw_mix.async_restore_last_state(
        State(
            "switch.towel_warmer",
            "off",
            {"actuators": {"1": "off", "2": True}, "aggregate_actuators": ["1"]},
        )
    )
    assert sw_mix.is_on is True
    assert sw_mix._actuator_states == {"1": True, "2": True}
    assert sw_mix._aggregate_actuators == {"1"}

    # Case D: Cold restore (no live report) preserves aggregate flags and booleans.
    gw_cold = _make_gateway("00:03:50:10:17:11")
    sw_cold = _make_thermo_switch(hass, gw_cold)
    await sw_cold.async_restore_last_state(
        State(
            "switch.towel_warmer",
            "on",
            {"actuators": {"1": "on", "2": False}, "aggregate_actuators": ["2"]},
        )
    )
    assert sw_cold.is_on is True
    assert sw_cold._actuator_states == {"1": True, "2": False}
    assert sw_cold._aggregate_actuators == {"2"}

    # Case E: Live per-actuator report before restore is never overwritten.
    gw_live = _make_gateway("00:03:50:10:17:11")
    sw_live = _make_thermo_switch(hass, gw_live)
    sw_live.handle_event(OWNMessage.parse("*#4*3#1*20*1##"))
    await sw_live.async_restore_last_state(
        State(
            "switch.towel_warmer",
            "off",
            {"actuators": {"1": "off"}, "aggregate_actuators": []},
        )
    )
    assert sw_live._actuator_states == {"1": True}
    assert sw_live._aggregate_actuators == set()


# ── Item 16 (#982): myhome_new_device_ dispatcher signal MAC casing normalization ──


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("setup_mac", "announce_mac"),
    [
        ("00:03:50:aa:bb:cc", "00:03:50:AA:BB:CC"),
        ("00:03:50:AA:BB:DD", "00:03:50:aa:bb:dd"),
    ],
)
async def test_announce_new_device_normalizes_mac_casing_for_button_and_number(
    hass: HomeAssistant,
    setup_mac: str,
    announce_mac: str,
) -> None:
    """Uppercase vs lowercase MACs deliver myhome_new_device_ signals to both button.py and number.py."""
    gateway = _make_gateway(setup_mac)
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={CONF_MAC: announce_mac},
        unique_id=dr.format_mac(setup_mac),
    )
    entry.add_to_hass(hass)
    hass.data.setdefault(DOMAIN, {})[announce_mac] = {
        CONF_PLATFORMS: {"button": {}, "cover": {}},
        CONF_ENTITY: gateway,
    }
    runtime = attach_runtime(hass, entry)
    assert runtime.mac == setup_mac

    # Register native point cover 31 in registry + state so number.py accepts it on announcement.
    ent_reg = er.async_get(hass)
    cover_entry = ent_reg.async_get_or_create(
        "cover",
        DOMAIN,
        f"{gateway.mac}-2-31",
        suggested_object_id="native_blind_31",
        config_entry=entry,
    )
    hass.states.async_set(
        cover_entry.entity_id,
        "open",
        {"min_position_delta": 2, "position_strategy": "native"},
    )

    added_buttons: list[object] = []
    added_numbers: list[object] = []
    assert await button_platform.async_setup_entry(hass, entry, added_buttons.extend) is True
    assert await number_platform.async_setup_entry(hass, entry, added_numbers.extend) is True
    await hass.async_block_till_done()

    # Clear startup entities to isolate dynamic dispatcher announcements.
    added_buttons.clear()
    added_numbers.clear()

    # Announce a new light (buttons) and a new native cover 32 (buttons + number) with opposite MAC casing.
    cover_32_entry = ent_reg.async_get_or_create(
        "cover",
        DOMAIN,
        f"{gateway.mac}-2-32",
        suggested_object_id="native_blind_32",
        config_entry=entry,
    )
    hass.states.async_set(
        cover_32_entry.entity_id,
        "open",
        {"min_position_delta": 2, "position_strategy": "native"},
    )

    announce_new_device(hass, announce_mac, "1", Address("11"), "Lamp 11")
    announce_new_device(hass, announce_mac, "2", Address("32"), "Native Blind 32")
    await hass.async_block_till_done()

    btn_uids = {getattr(b, "unique_id", None) for b in added_buttons}
    assert f"{gateway.mac}-1-11-disable" in btn_uids
    assert f"{gateway.mac}-1-11-enable" in btn_uids
    assert f"{gateway.mac}-2-32-disable" in btn_uids
    assert f"{gateway.mac}-2-32-enable" in btn_uids

    delta_numbers = [
        n for n in added_numbers if isinstance(n, MyHOMECoverMinPositionDeltaNumber)
    ]
    assert [n.unique_id for n in delta_numbers] == [f"{gateway.mac}-2-32-min_position_delta"]


# ── Item 19: crowdin.yml branch update ──


def test_crowdin_workflow_targets_master_branch() -> None:
    """Crowdin workflow pushes and opens PRs against 'master', never stale 'v2-phase1-architecture'."""
    text = (REPO_ROOT / ".github" / "workflows" / "crowdin.yml").read_text(encoding="utf-8")
    assert 'branches: ["master"]' in text
    assert 'pull_request_base_branch_name: "master"' in text
    assert "v2-phase1-architecture" not in text


# ── Item 20 (#988): sync_time_unsupported_offset translations ──


@pytest.mark.parametrize("locale", ["fr", "it", "nl"])
def test_translations_include_sync_time_unsupported_offset(locale: str) -> None:
    """fr/it/nl translation catalogs define exceptions.sync_time_unsupported_offset with matching placeholders."""
    data = json.loads(
        (REPO_ROOT / "custom_components" / "myhome" / "translations" / f"{locale}.json").read_text(
            encoding="utf-8"
        )
    )
    msg = data["exceptions"]["sync_time_unsupported_offset"]["message"]
    assert "{timezone}" in msg
    assert "{error}" in msg


# ── Item 21 (#958): pyproject.toml package-data includes brand/*.png and frontend/*.js ──


def test_pyproject_package_data_includes_brand_and_frontend_assets() -> None:
    """pyproject.toml ships brand PNGs and frontend JS in setuptools package-data."""
    config = tomllib.loads((REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    patterns = config["tool"]["setuptools"]["package-data"]["*"]
    assert "brand/*.png" in patterns
    assert "frontend/*.js" in patterns
    assert list((REPO_ROOT / "custom_components" / "myhome" / "brand").glob("*.png"))
    assert list((REPO_ROOT / "custom_components" / "myhome" / "frontend").glob("*.js"))
