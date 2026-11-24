"""Tests for Issue #981 / Discussion #978: passive WHO=4 companion Temperature & dynamic Humidity sensors.

Covers:
1. Companion temperature sensor lifecycle, device linkage, metadata/naming, strict passivity
   (zero polling, ``async_update()`` no-op), positive/negative ``Dimension 0`` decoding,
   ``message_type=None`` fallback, secondary probe ``PZZ`` isolation, first-frame bus
   seeding, unchanged-value deduplication, and invalid/non-finite reading guards.
2. Dynamic companion humidity sensor lifecycle (created only on ``Dimension 60``, YAML
   ``humidity: true``, or restored humidity state), HA 2026.10 / 2026.11+ restore detection,
   strict passivity, range clamping ``0..100 %``, and registry reload pruning/restoration.
3. Complete exclusions: central units (``#0``, ``#0#1..#0#4``, ``central: true``), probe-less
   thermo switches (``#785``), exposed virtual probes (``expose.py``), ``ignored_addresses``,
   and unconfirmed single-frame actuator-only zones.
4. ``F422`` ``#4#II`` interface isolation, inline ``where`` config parsing, and reload parsing.
5. Shared-bus primary/follower ownership (``#453``) and ``prune_entity()`` cleanup (``#524``).
6. ``RestoreSensor`` native-unit round-trip (``#907``, including ``°F`` climate restore guard)
   and ``myhome.yaml`` ``sensor:`` compatibility & upgrade rebind (``#441``).
7. Live hardware trace replays:
   - ``F453`` (2.0.10) + ``3550`` + ``N4692`` sweep (Discussion #770)
   - ``F461`` (2.0.11) Zone 2 ``Dimension 0`` + ``Dimension 60`` live capture
   - ``MyHomeServer1`` (2.87.13) ``H4691`` humidity + ``F430V10`` 0–10 V full cycle
"""

from __future__ import annotations

import json
import math
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from homeassistant.components.climate import HVACMode
from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorExtraStoredData,
    SensorStateClass,
)
from homeassistant.const import (
    ATTR_UNIT_OF_MEASUREMENT,
    CONF_HOST,
    CONF_MAC,
    CONF_NAME,
    CONF_PASSWORD,
    CONF_PORT,
    PERCENTAGE,
    UnitOfTemperature,
)
from homeassistant.core import HomeAssistant, State
from homeassistant.helpers import area_registry as ar
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.dispatcher import async_dispatcher_send
from homeassistant.helpers.restore_state import DATA_RESTORE_STATE
from homeassistant.util.unit_system import US_CUSTOMARY_SYSTEM
from OWNd.message import OWNEvent, OWNHeatingEvent, OWNMessage
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    mock_restore_cache_with_extra_data,
)

from custom_components.myhome.climate import MyHOMEClimate
from custom_components.myhome.climate import async_setup_entry as climate_setup_entry
from custom_components.myhome.climate import async_unload_entry as climate_unload_entry
from custom_components.myhome.climate_discovery import has_humidity_state, has_thermostat_state
from custom_components.myhome.const import (
    CONF_BROADCAST_RESYNC,
    CONF_BUS_INTERFACE,
    CONF_BUS_TOPOLOGY,
    CONF_CENTRAL,
    CONF_DEVICE_CLASS,
    CONF_DEVICE_MODEL,
    CONF_ENTITY,
    CONF_EXPOSE,
    CONF_FILE_PATH,
    CONF_GATEWAY_ROLE,
    CONF_HUMIDITY_SUPPORT,
    CONF_IGNORED_ADDRESSES,
    CONF_MANUFACTURER,
    CONF_PRIMARY_GATEWAY,
    CONF_STANDALONE,
    CONF_WHERE,
    CONF_WHO,
    DOMAIN,
    PLATFORMS,
    ROLE_PRIMARY,
    ROLE_STANDBY,
    SIGNAL_CLIMATE_HUMIDITY,
    SIGNAL_NEW_CLIMATE_ZONE,
    SUBENTRY_TYPE_EXPOSE,
    SUBENTRY_TYPE_THERMO_SWITCH,
    TOPOLOGY_SHARED,
)
from custom_components.myhome.data import MyHOMERuntimeData
from custom_components.myhome.discovery import prune_entity
from custom_components.myhome.expose import ExposeManager
from custom_components.myhome.router import FrameRouter
from custom_components.myhome.sensor import (
    MyHOMEActuatorOutputSensor,
    MyHOMEClimateHumiditySensor,
    MyHOMEClimateTemperatureSensor,
    MyHOMETemperatureSensor,
)
from custom_components.myhome.sensor import async_setup_entry as sensor_setup_entry
from custom_components.myhome.sensor import async_unload_entry as sensor_unload_entry
from custom_components.myhome.typing_compat import UNDEFINED, device_info_to_dict
from custom_components.myhome.where_grammar import thermo_zone_sensor_target

TRACES_DIR = Path(__file__).resolve().parent / "fixtures" / "traces"
F453_TRACE_PATH = (
    TRACES_DIR / "discussion_770_f453_n4692" / "myhome_sweep_F453_all_2026-11-14T13-34-42.json"
)
F461_TRACE_PATH = (
    TRACES_DIR / "f461_live_2026-10-23" / "myhome_trace_F461_live_2026-10-23.json"
)
MHS1_FULL_CYCLE_TRACE_PATH = (
    TRACES_DIR / "issue_949" / "myhome_trace_MyHomeServer1_fancoil_dim20_full_cycle_2026-11-18.json"
)
MAC = "00:03:50:00:09:81"


def _make_gateway(
    model: str = "MyHomeServer1",
    firmware: str = "2.87.13",
    *,
    mac: str = MAC,
    is_follower: bool = False,
    primary_mac: str | None = None,
    delegated_whos: set[int] | None = None,
    delegated_away_whos: set[int] | None = None,
    supports_zone_status: bool = True,
) -> MagicMock:
    gw = MagicMock()
    gw.name = model
    gw.mac = mac
    gw.unique_id = mac
    gw.log_id = f"[{model} - {mac}]"
    gw.model = model
    gw.manufacturer = "BTicino S.p.A."
    gw.firmware = firmware
    gw.device_registry_id = None
    gw.is_connected = True
    gw.has_central_unit = False
    gw.is_follower = is_follower
    gw.primary_gateway_mac = primary_mac
    gw.delegated_whos = set(delegated_whos or set())
    gw.delegated_away_whos = set(delegated_away_whos or set())
    gw.sending_workers = []
    gw.availability_signal = f"myhome_gw_available_{mac}"
    gw.is_who_available = MagicMock(return_value=True)
    gw.initial_discovery_pending = False
    gw.sweep_active = False
    gw.silent_zone_poll_needed = False
    gw.supports_zone_status = supports_zone_status
    gw.actuator_request_refused = False
    gw.zone_actuator_request_refused = False
    gw.interface_zone_request_refused = True
    gw._who4_target = MagicMock(return_value=gw)
    gw._who4_startup_poll_lock = None
    gw.pace_startup_status_write = None
    gw.wait_for_initial_discovery = None
    gw.send = AsyncMock(return_value=True)
    gw.send_status_request = AsyncMock(return_value=True)
    gw.request_zone_dimension = MagicMock(return_value=False)
    gw.watch_zone_actuators = MagicMock(return_value=MagicMock())
    return gw


def _make_entry(
    hass: HomeAssistant,
    gw: MagicMock,
    *,
    mac_override: str | None = None,
    platforms: dict[str, dict[str, Any]] | None = None,
    subentries_data: list[dict[str, Any]] | None = None,
    options: dict[str, Any] | None = None,
    extra_data: dict[str, Any] | None = None,
) -> MockConfigEntry:
    data: dict[str, Any] = {CONF_MAC: mac_override or gw.mac, **(extra_data or {})}
    entry = MockConfigEntry(
        domain=DOMAIN,
        data=data,
        options=options or {},
        unique_id=gw.mac,
        subentries_data=subentries_data or [],
    )
    entry.add_to_hass(hass)
    plat_map: dict[str, dict[str, Any]] = {p: {} for p in PLATFORMS}
    if platforms:
        for p, cfg in platforms.items():
            plat_map[p] = cfg
    entry.runtime_data = MyHOMERuntimeData(
        gateway=gw,
        platforms=plat_map,
        router=FrameRouter(),
        expose=list((extra_data or {}).get(CONF_EXPOSE, [])),
    )
    has_expose_sub = any(
        s.get("subentry_type") == SUBENTRY_TYPE_EXPOSE for s in (subentries_data or [])
    )
    if has_expose_sub or (extra_data or {}).get(CONF_EXPOSE):
        entry.runtime_data.expose_manager = ExposeManager(hass, entry, gw)
    return entry


def _make_entity_adders(
    hass: HomeAssistant,
    climates: list[MyHOMEClimate],
    sensors: list[Any],
    *,
    auto_entity_id: bool = True,
) -> tuple[Any, Any]:
    async def _add_entities_sync_safe(ent: Any, eid: str, bucket: list[Any]) -> None:
        ent.hass = hass
        ent.platform = MagicMock()
        ent.entity_id = eid
        ent.async_schedule_update_ha_state = MagicMock()
        ent.async_write_ha_state = MagicMock()
        bucket.append(ent)
        await ent.async_added_to_hass()

    def _add_climates(entities: list[Any], update_before_add: bool = False) -> None:
        for idx, ent in enumerate(entities):
            eid = f"climate.mock_zone_{len(climates) + idx}"
            if auto_entity_id:
                ent.hass = hass
                ent.platform = MagicMock()
                ent.entity_id = eid
                ent.async_schedule_update_ha_state = MagicMock()
                ent.async_write_ha_state = MagicMock()
                climates.append(ent)
                hass.async_create_task(ent.async_added_to_hass())
            else:
                hass.async_create_task(_add_entities_sync_safe(ent, eid, climates))

    def _add_sensors(entities: list[Any], update_before_add: bool = False) -> None:
        for idx, ent in enumerate(entities):
            ent.hass = hass
            ent.platform = MagicMock()
            ent.entity_id = f"sensor.mock_sensor_{len(sensors) + idx}"
            ent.async_schedule_update_ha_state = MagicMock()
            ent.async_write_ha_state = MagicMock()
            sensors.append(ent)
            hass.async_create_task(ent.async_added_to_hass())

    return _add_climates, _add_sensors


# ---------------------------------------------------------------------------
# 1. Companion Temperature Sensor Lifecycle & Strict Passivity
# ---------------------------------------------------------------------------


async def test_companion_temperature_metadata_device_linkage_and_passivity(
    hass: HomeAssistant,
) -> None:
    """Confirmed climate zone creates strictly passive Temperature sensor on the same device."""
    gw = _make_gateway()
    entry = _make_entry(hass, gw)
    ent_reg = er.async_get(hass)
    ent_reg.async_get_or_create("climate", DOMAIN, f"{gw.mac}-4-1", config_entry=entry)

    climates: list[MyHOMEClimate] = []
    sensors: list[Any] = []
    add_c, add_s = _make_entity_adders(hass, climates, sensors)

    await climate_setup_entry(hass, entry, add_c)
    await sensor_setup_entry(hass, entry, add_s)
    await hass.async_block_till_done()

    assert len(climates) == 1
    temps = [s for s in sensors if isinstance(s, MyHOMEClimateTemperatureSensor)]
    assert len(temps) == 1
    assert not any(isinstance(s, MyHOMEClimateHumiditySensor) for s in sensors)

    climate_ent = climates[0]
    temp = temps[0]
    assert temp.unique_id == f"{gw.mac}-4-1-temperature"
    assert temp.device_class == SensorDeviceClass.TEMPERATURE
    assert temp.state_class == SensorStateClass.MEASUREMENT
    assert temp.native_unit_of_measurement == UnitOfTemperature.CELSIUS
    assert temp.has_entity_name is True
    assert getattr(temp, "_attr_name", UNDEFINED) is UNDEFINED
    assert temp.entity_registry_enabled_default is True
    assert temp.should_poll is False
    assert temp._poll_on_add is False
    assert temp.extra_state_attributes == {"zone": 1}
    assert device_info_to_dict(temp.device_info)["identifiers"] == device_info_to_dict(
        climate_ent.device_info
    )["identifiers"]

    # Strict passivity: async_update() sends zero frames
    gw.send.reset_mock()
    gw.send_status_request.reset_mock()
    gw.request_zone_dimension.reset_mock()
    await temp.async_update()
    gw.send.assert_not_called()
    gw.send_status_request.assert_not_called()
    gw.request_zone_dimension.assert_not_called()

    signal = f"myhome_message_{gw.mac}"

    # Positive & negative Dimension 0
    async_dispatcher_send(hass, signal, OWNMessage.parse("*#4*1*0*0215##"))
    await hass.async_block_till_done()
    assert temp.native_value == 21.5

    async_dispatcher_send(hass, signal, OWNMessage.parse("*#4*1*0*1045##"))
    await hass.async_block_till_done()
    assert temp.native_value == -4.5

    # Fallback when message_type is None + dimension=0
    fallback_evt = OWNHeatingEvent("*#4*1*0*0235##")
    with patch.object(OWNHeatingEvent, "message_type", new=None, create=True):
        temp.handle_event(fallback_evt)
    assert temp.native_value == 23.5

    # Unchanged reading does not republish
    temp.async_schedule_update_ha_state.reset_mock()
    async_dispatcher_send(hass, signal, OWNMessage.parse("*#4*1*0*0235##"))
    await hass.async_block_till_done()
    temp.async_schedule_update_ha_state.assert_not_called()

    # Empty dimension_value in fallback path and invalid/non-finite readings are ignored
    empty_fallback = SimpleNamespace(
        where="1",
        interface=None,
        zone=1,
        message_type=None,
        dimension=0,
        dimension_value=[],
    )
    temp.handle_event(empty_fallback)  # type: ignore[arg-type]
    assert temp.native_value == 23.5

    temp._apply_reading(fallback_evt, None)
    temp._apply_reading(fallback_evt, math.nan)
    temp._apply_reading(fallback_evt, math.inf)
    bool_msg = SimpleNamespace(
        where="1",
        interface=None,
        zone=1,
        message_type="main_temperature",
        main_temperature=True,
    )
    temp.handle_event(bool_msg)  # type: ignore[arg-type]
    assert temp.native_value == 23.5

    # is_translation=True ignored by handle_message; secondary probe 101 & Zone 2 ignored
    translated = OWNHeatingEvent("*#4*1*0*0100##")
    object.__setattr__(translated, "is_translation", True) if hasattr(translated, "__slots__") else None
    with patch.object(OWNHeatingEvent, "is_translation", new=True, create=True):
        async_dispatcher_send(hass, signal, OWNMessage.parse("*#4*1*0*0100##"))
    async_dispatcher_send(hass, signal, OWNMessage.parse("*#4*101*0*0180##"))
    async_dispatcher_send(hass, signal, OWNMessage.parse("*#4*2*0*0190##"))
    temp.handle_event(OWNHeatingEvent("*#4*2*0*0190##"))
    await hass.async_block_till_done()
    assert temp.native_value == 23.5

    assert await sensor_unload_entry(hass, entry)


async def test_companion_temperature_first_frame_bus_discovery_seeds_initial_value(
    hass: HomeAssistant,
) -> None:
    """Held Frame 1 Dim 0 seeds Temperature sensor upon Frame 2 confirmation; bit-flip creates zero (#966)."""
    gw = _make_gateway()
    entry = _make_entry(hass, gw)

    climates: list[MyHOMEClimate] = []
    sensors: list[Any] = []
    add_c, add_s = _make_entity_adders(hass, climates, sensors)

    await climate_setup_entry(hass, entry, add_c)
    await sensor_setup_entry(hass, entry, add_s)
    await hass.async_block_till_done()
    assert sensors == []

    signal = f"myhome_message_{gw.mac}"
    # Single bit-flipped frame on Zone 65 (#966) creates zero climate and zero companion sensors
    async_dispatcher_send(hass, signal, OWNMessage.parse("*#4*65*0*0190##"))
    # Frame 1 on Zone 4 is held pending confirmation
    async_dispatcher_send(hass, signal, OWNMessage.parse("*#4*4*0*0221##"))
    await hass.async_block_till_done()
    assert climates == []
    assert sensors == []

    # Confirming Frame 2 on Zone 4 creates climate + companion seeded with held Frame 1 (22.1 °C)
    async_dispatcher_send(hass, signal, OWNMessage.parse("*4*1*4##"))
    await hass.async_block_till_done()

    assert [c._where for c in climates] == ["4"]
    temps = [s for s in sensors if isinstance(s, MyHOMEClimateTemperatureSensor)]
    assert len(temps) == 1
    assert temps[0].unique_id == f"{gw.mac}-4-4-temperature"
    assert temps[0].native_value == 22.1
    assert not any("65" in s.unique_id for s in sensors)


# ---------------------------------------------------------------------------
# 2. Dynamic Companion Humidity Sensor Lifecycle
# ---------------------------------------------------------------------------


async def test_late_dim60_dynamically_adds_and_updates_humidity_sensor(
    hass: HomeAssistant,
) -> None:
    """Late Dimension 60 creates MyHOMEClimateHumiditySensor once on same zone device and updates."""
    gw = _make_gateway()
    entry = _make_entry(hass, gw)
    ent_reg = er.async_get(hass)
    ent_reg.async_get_or_create("climate", DOMAIN, f"{gw.mac}-4-1", config_entry=entry)

    climates: list[MyHOMEClimate] = []
    sensors: list[Any] = []
    add_c, add_s = _make_entity_adders(hass, climates, sensors)

    await sensor_setup_entry(hass, entry, add_s)
    await climate_setup_entry(hass, entry, add_c)
    await hass.async_block_till_done()

    assert [type(s) for s in sensors] == [MyHOMEClimateTemperatureSensor]

    signal = f"myhome_message_{gw.mac}"
    async_dispatcher_send(hass, signal, OWNMessage.parse("*#4*1*60*55##"))
    await hass.async_block_till_done()

    hums = [s for s in sensors if isinstance(s, MyHOMEClimateHumiditySensor)]
    assert len(hums) == 1
    hum = hums[0]
    assert hum.unique_id == f"{gw.mac}-4-1-humidity"
    assert hum.device_class == SensorDeviceClass.HUMIDITY
    assert hum.state_class == SensorStateClass.MEASUREMENT
    assert hum.native_unit_of_measurement == PERCENTAGE
    assert hum.should_poll is False
    assert hum._poll_on_add is False
    assert hum.native_value == 55.0
    assert hum.extra_state_attributes == {"zone": 1}
    assert device_info_to_dict(hum.device_info)["identifiers"] == device_info_to_dict(
        climates[0].device_info
    )["identifiers"]

    # Strict passivity: async_update() is a no-op
    gw.send.reset_mock()
    gw.send_status_request.reset_mock()
    await hum.async_update()
    gw.send.assert_not_called()
    gw.send_status_request.assert_not_called()

    # Updates cleanly & does NOT create duplicate entity on subsequent Dim 60
    async_dispatcher_send(hass, signal, OWNMessage.parse("*#4*1*60*62##"))
    await hass.async_block_till_done()
    assert len([s for s in sensors if isinstance(s, MyHOMEClimateHumiditySensor)]) == 1
    assert hum.native_value == 62.0

    # Duplicate value skipped
    hum.async_schedule_update_ha_state.reset_mock()
    async_dispatcher_send(hass, signal, OWNMessage.parse("*#4*1*60*62##"))
    await hass.async_block_till_done()
    hum.async_schedule_update_ha_state.assert_not_called()

    # Fallback message_type=None + dimension=60 valid, invalid, out-of-range, and wrong zone
    with patch.object(OWNHeatingEvent, "message_type", new=None, create=True):
        hum.handle_event(OWNHeatingEvent("*#4*1*60*48##"))
    assert hum.native_value == 48.0

    bad_str = SimpleNamespace(
        where="1",
        interface=None,
        zone=1,
        message_type=None,
        dimension=60,
        dimension_value=["bad"],
    )
    hum.handle_event(bad_str)  # type: ignore[arg-type]
    assert hum.native_value == 48.0

    out_of_range = SimpleNamespace(
        where="1",
        interface=None,
        zone=1,
        message_type="main_humidity",
        main_humidity=105.0,
    )
    hum.handle_event(out_of_range)  # type: ignore[arg-type]
    assert hum.native_value == 48.0

    neg_range = SimpleNamespace(
        where="1",
        interface=None,
        zone=1,
        message_type="main_humidity",
        main_humidity=-1.0,
    )
    hum.handle_event(neg_range)  # type: ignore[arg-type]
    assert hum.native_value == 48.0

    hum.handle_event(OWNHeatingEvent("*#4*2*60*70##"))
    assert hum.native_value == 48.0


async def test_zone_discovered_from_dim60_and_yaml_humidity_true(
    hass: HomeAssistant,
) -> None:
    """Zone discovered from Dim 60 or configured with humidity: true spawns both companions."""
    gw = _make_gateway()
    entry = _make_entry(
        hass,
        gw,
        platforms={
            "climate": {
                "zone_2": {
                    CONF_WHO: "4",
                    CONF_WHERE: "2",
                    CONF_NAME: "Living Humid",
                    CONF_HUMIDITY_SUPPORT: True,
                }
            }
        },
    )

    climates: list[MyHOMEClimate] = []
    sensors: list[Any] = []
    add_c, add_s = _make_entity_adders(hass, climates, sensors)

    await sensor_setup_entry(hass, entry, add_s)
    await climate_setup_entry(hass, entry, add_c)
    await hass.async_block_till_done()

    # YAML Zone 2 has both temperature and humidity companion sensors immediately
    z2_uids = {s.unique_id for s in sensors}
    assert f"{gw.mac}-4-2-temperature" in z2_uids
    assert f"{gw.mac}-4-2-humidity" in z2_uids

    # Bus discovery from held Dim 60 + confirming Dim 0 on Zone 3 (#966)
    signal = f"myhome_message_{gw.mac}"
    async_dispatcher_send(hass, signal, OWNMessage.parse("*#4*3*60*49##"))
    await hass.async_block_till_done()
    assert not any("4-3-" in s.unique_id for s in sensors)

    async_dispatcher_send(hass, signal, OWNMessage.parse("*#4*3*0*0215##"))
    await hass.async_block_till_done()

    z3_hums = [
        s
        for s in sensors
        if isinstance(s, MyHOMEClimateHumiditySensor) and s.unique_id == f"{gw.mac}-4-3-humidity"
    ]
    z3_temps = [
        s
        for s in sensors
        if isinstance(s, MyHOMEClimateTemperatureSensor) and s.unique_id == f"{gw.mac}-4-3-temperature"
    ]
    assert len(z3_temps) == 1
    assert z3_temps[0].native_value == 21.5
    assert len(z3_hums) == 1
    assert z3_hums[0].native_value == 49.0

    # Subsequent Dim 60 does not duplicate
    async_dispatcher_send(hass, signal, OWNMessage.parse("*#4*3*60*52##"))
    await hass.async_block_till_done()
    assert len(
        [s for s in sensors if s.unique_id == f"{gw.mac}-4-3-humidity"]
    ) == 1
    assert z3_hums[0].native_value == 52.0


async def test_humidity_restore_detection_ha2026_10_2026_11_and_registry(
    hass: HomeAssistant,
) -> None:
    """Verify startup humidity detection across HA <=2026.10, >=2026.11, live state, and Dormant Skip."""
    # Direct unit checks of has_thermostat_state / has_humidity_state across both HA APIs
    legacy_restore = SimpleNamespace(
        last_states={
            "climate.z1": SimpleNamespace(
                state=State("climate.z1", "off", {"current_humidity": 51.0})
            )
        }
    )
    hass.data[DATA_RESTORE_STATE] = legacy_restore
    assert has_humidity_state(hass, "climate.z1") is True
    assert has_thermostat_state(hass, "climate.z1") is True
    assert has_humidity_state(hass, "climate.missing") is False

    modern_restore = SimpleNamespace(
        async_get_stored_state=lambda eid: (
            SimpleNamespace(state=State(eid, "heat", {"current_humidity": 46.0}))
            if eid == "climate.z2"
            else None
        )
    )
    hass.data[DATA_RESTORE_STATE] = modern_restore
    assert has_humidity_state(hass, "climate.z2") is True
    assert has_thermostat_state(hass, "climate.z2") is True

    # End-to-end startup:
    # - z2: confirmed registry climate + RestoreStateData humidity
    # - z4: confirmed registry climate (no RestoreStateData) + registry 4-4-humidity
    # - z5: parentless registry 4-5-humidity (no live climate yet -> Dormant Skip until z5 confirms)
    gw = _make_gateway()
    entry = _make_entry(hass, gw)
    ent_reg = er.async_get(hass)
    z2_entry = ent_reg.async_get_or_create("climate", DOMAIN, f"{gw.mac}-4-2", config_entry=entry)
    ent_reg.async_get_or_create("climate", DOMAIN, f"{gw.mac}-4-4", config_entry=entry)
    ent_reg.async_get_or_create("sensor", DOMAIN, f"{gw.mac}-4-4-humidity", config_entry=entry)
    z5_hum_reg = ent_reg.async_get_or_create("sensor", DOMAIN, f"{gw.mac}-4-5-humidity", config_entry=entry)

    hass.data[DATA_RESTORE_STATE] = SimpleNamespace(
        async_get_stored_state=lambda eid: (
            SimpleNamespace(state=State(eid, "heat", {"current_humidity": 53.0}))
            if eid == z2_entry.entity_id
            else None
        )
    )

    sensors: list[Any] = []
    _, add_s = _make_entity_adders(hass, [], sensors)
    await sensor_setup_entry(hass, entry, add_s)
    await hass.async_block_till_done()

    uids = {s.unique_id for s in sensors}
    assert uids == {
        f"{gw.mac}-4-2-temperature",
        f"{gw.mac}-4-2-humidity",
        f"{gw.mac}-4-4-temperature",
        f"{gw.mac}-4-4-humidity",
    }
    # Parentless 4-5-humidity is NOT created at startup yet stays dormant in entity_registry
    assert f"{gw.mac}-4-5-humidity" not in uids
    assert ent_reg.async_get(z5_hum_reg.entity_id) is not None

    # Once Zone 5 confirms via SIGNAL_NEW_CLIMATE_ZONE (even with humidity=False), restored_humidity_zones rebinds it
    async_dispatcher_send(
        hass,
        SIGNAL_NEW_CLIMATE_ZONE.format(gw.mac),
        {"zone": "5", "temperature": 20.5, "humidity": False},
    )
    await hass.async_block_till_done()
    uids_after = {s.unique_id for s in sensors}
    assert f"{gw.mac}-4-5-temperature" in uids_after
    assert f"{gw.mac}-4-5-humidity" in uids_after


# ---------------------------------------------------------------------------
# 3. Exclusions: Central, Thermo Switch, Expose, Ignored, Unconfirmed
# ---------------------------------------------------------------------------


async def test_exclusions_central_thermo_switch_expose_ignored_and_unconfirmed(
    hass: HomeAssistant,
) -> None:
    """Central units, probe-less thermo switches, exposed probes, ignored & unconfirmed zones create zero companions."""
    gw = _make_gateway()
    entry = _make_entry(
        hass,
        gw,
        platforms={
            "climate": {
                "central_#0": {CONF_WHO: "4", CONF_WHERE: "#0", CONF_CENTRAL: True, CONF_NAME: "Central"},
                "real_2": {CONF_WHO: "4", CONF_WHERE: "2", CONF_NAME: "Bedroom Real"},
            },
            "switch": {
                "whole_zone_8": {CONF_WHO: "4", CONF_WHERE: "8"},
            },
        },
        subentries_data=[
            {
                "data": {CONF_WHO: "4", CONF_WHERE: "7#1"},
                "subentry_type": SUBENTRY_TYPE_THERMO_SWITCH,
                "title": "Towel Warmer 7#1",
                "unique_id": "ts_7_1",
            },
            {
                "data": {CONF_WHO: "4", CONF_WHERE: "2#1"},
                "subentry_type": SUBENTRY_TYPE_THERMO_SWITCH,
                "title": "Bedroom Aux Switch 2#1",
                "unique_id": "ts_2_1",
            },
            {
                "data": {CONF_WHO: "4", CONF_WHERE: "11", "entity_id": "sensor.virtual_zone_11"},
                "subentry_type": SUBENTRY_TYPE_EXPOSE,
                "title": "Expose Zone 11",
                "unique_id": "exp_4_11",
            },
        ],
        options={
            CONF_IGNORED_ADDRESSES: [{"who": 4, "where": "12"}],
        },
    )

    ent_reg = er.async_get(hass)
    # Central entities (#0, #0#1, 0)
    ent_reg.async_get_or_create("climate", DOMAIN, f"{gw.mac}-4-#0", config_entry=entry)
    ent_reg.async_get_or_create("climate", DOMAIN, f"{gw.mac}-4-#0#1", config_entry=entry)
    ent_reg.async_get_or_create("climate", DOMAIN, f"{gw.mac}-4-0", config_entry=entry)
    # Probe-less Zone 7 climate entity coerced to bare "off" with no thermostat telemetry (#785)
    z7_cli = ent_reg.async_get_or_create("climate", DOMAIN, f"{gw.mac}-4-7", config_entry=entry)
    hass.states.async_set(z7_cli.entity_id, HVACMode.OFF, {})
    # Stale companion registry entries that MUST be pruned
    stale_7_t = ent_reg.async_get_or_create("sensor", DOMAIN, f"{gw.mac}-4-7-temperature", config_entry=entry)
    stale_7_h = ent_reg.async_get_or_create("sensor", DOMAIN, f"{gw.mac}-4-7-humidity", config_entry=entry)
    stale_8_h = ent_reg.async_get_or_create("sensor", DOMAIN, f"{gw.mac}-4-8-humidity", config_entry=entry)
    stale_11_h = ent_reg.async_get_or_create("sensor", DOMAIN, f"{gw.mac}-4-11-humidity", config_entry=entry)
    stale_12_h = ent_reg.async_get_or_create("sensor", DOMAIN, f"{gw.mac}-4-12-humidity", config_entry=entry)

    # Whole-zone switch 8 somehow having a stale climate registry entry
    ent_reg.async_get_or_create("climate", DOMAIN, f"{gw.mac}-4-8", config_entry=entry)
    # Exposed Zone 11 and Ignored Zone 12 climates
    ent_reg.async_get_or_create("climate", DOMAIN, f"{gw.mac}-4-11", config_entry=entry)
    ent_reg.async_get_or_create("climate", DOMAIN, f"{gw.mac}-4-12", config_entry=entry)
    # Probe registry entry (WHO=4)
    ent_reg.async_get_or_create("climate", DOMAIN, f"{gw.mac}-4-101", config_entry=entry)

    climates: list[MyHOMEClimate] = []
    sensors: list[Any] = []
    add_c, add_s = _make_entity_adders(hass, climates, sensors)

    await sensor_setup_entry(hass, entry, add_s)
    await climate_setup_entry(hass, entry, add_c)
    await hass.async_block_till_done()

    # ONLY Real Zone 2 gets a companion temperature sensor!
    assert [s.unique_id for s in sensors] == [f"{gw.mac}-4-2-temperature"]
    # All stale entries were pruned
    for stale in (stale_7_t, stale_7_h, stale_8_h, stale_11_h, stale_12_h):
        assert ent_reg.async_get(stale.entity_id) is None

    # Excluded zones on live signals never spawn companions either
    async_dispatcher_send(
        hass,
        SIGNAL_NEW_CLIMATE_ZONE.format(gw.mac),
        {"zone": "11", "temperature": 21.0, "humidity": True, "current_humidity": 50.0},
    )
    async_dispatcher_send(
        hass,
        SIGNAL_NEW_CLIMATE_ZONE.format(gw.mac),
        {"zone": "12", "temperature": 21.0, "humidity": False},
    )
    async_dispatcher_send(
        hass,
        SIGNAL_CLIMATE_HUMIDITY.format(gw.mac),
        {"zone": "8", "current_humidity": 50.0},
    )
    await hass.async_block_till_done()
    assert [s.unique_id for s in sensors] == [f"{gw.mac}-4-2-temperature"]

    # Unconfirmed single-frame actuator-only Zone 14 creates zero companions
    signal = f"myhome_message_{gw.mac}"
    async_dispatcher_send(hass, signal, OWNMessage.parse("*#4*14#1*20*1##"))
    await hass.async_block_till_done()
    assert [s.unique_id for s in sensors] == [f"{gw.mac}-4-2-temperature"]

    # Non-WHO-4 climate registry entry (e.g. 1-15) is ignored by sensor companion scan
    assert await sensor_unload_entry(hass, entry)
    gw_non_who4 = _make_gateway(mac="00:03:50:00:09:88")
    entry_non_who4 = _make_entry(hass, gw_non_who4)
    ent_reg.async_get_or_create("climate", DOMAIN, f"{gw_non_who4.mac}-1-15", config_entry=entry_non_who4)
    sensors_non_who4: list[Any] = []
    _, add_s_non = _make_entity_adders(hass, [], sensors_non_who4)
    await sensor_setup_entry(hass, entry_non_who4, add_s_non)
    await hass.async_block_till_done()
    assert sensors_non_who4 == []


# ---------------------------------------------------------------------------
# 4. F422 #4#II Interface Isolation
# ---------------------------------------------------------------------------


async def test_f422_interface_isolation_device_linkage_routing_and_reload(
    hass: HomeAssistant,
) -> None:
    """Zone 1 and 1#4#01 stay strictly isolated on separate HA devices and routes."""
    gw = _make_gateway()
    entry = _make_entry(
        hass,
        gw,
        platforms={
            "climate": {
                "sub_5": {
                    CONF_WHO: "4",
                    CONF_WHERE: "5#4#01",
                    CONF_NAME: "Remote Wing 5",
                }
            }
        },
    )
    ent_reg = er.async_get(hass)
    ent_reg.async_get_or_create("climate", DOMAIN, f"{gw.mac}-4-1", config_entry=entry)
    ent_reg.async_get_or_create("climate", DOMAIN, f"{gw.mac}-4-1#4#01", config_entry=entry)

    climates: list[MyHOMEClimate] = []
    sensors: list[Any] = []
    add_c, add_s = _make_entity_adders(hass, climates, sensors)

    await climate_setup_entry(hass, entry, add_c)
    await sensor_setup_entry(hass, entry, add_s)
    await hass.async_block_till_done()

    by_uid = {s.unique_id: s for s in sensors}
    assert f"{gw.mac}-4-1-temperature" in by_uid
    assert f"{gw.mac}-4-1#4#01-temperature" in by_uid
    assert f"{gw.mac}-4-5#4#01-temperature" in by_uid

    local_t = by_uid[f"{gw.mac}-4-1-temperature"]
    routed_t = by_uid[f"{gw.mac}-4-1#4#01-temperature"]
    sub5_t = by_uid[f"{gw.mac}-4-5#4#01-temperature"]

    assert sub5_t._device_id == "5#4#01"
    assert routed_t.extra_state_attributes == {"zone": 1, "Int": "01"}
    assert local_t.extra_state_attributes == {"zone": 1}
    assert device_info_to_dict(local_t.device_info)["identifiers"] == {(DOMAIN, f"{gw.mac}-4-1")}
    assert device_info_to_dict(routed_t.device_info)["identifiers"] == {
        (DOMAIN, f"{gw.mac}-4-1#4#01")
    }

    signal = f"myhome_message_{gw.mac}"
    async_dispatcher_send(hass, signal, OWNMessage.parse("*#4*1*0*0210##"))
    async_dispatcher_send(hass, signal, OWNMessage.parse("*#4*1#4#01*0*0185##"))
    async_dispatcher_send(hass, signal, OWNMessage.parse("*#4*1*60*45##"))
    async_dispatcher_send(hass, signal, OWNMessage.parse("*#4*1#4#01*60*68##"))
    await hass.async_block_till_done()

    assert local_t.native_value == 21.0
    assert routed_t.native_value == 18.5
    by_uid = {s.unique_id: s for s in sensors}
    assert by_uid[f"{gw.mac}-4-1-humidity"].native_value == 45.0
    assert by_uid[f"{gw.mac}-4-1#4#01-humidity"].native_value == 68.0
    assert by_uid[f"{gw.mac}-4-1#4#01-humidity"].extra_state_attributes == {
        "zone": 1,
        "Int": "01",
    }

    # Companion reload keeps local & #4#II interface companions distinct with zero duplicates
    assert await sensor_unload_entry(hass, entry)
    gw2 = _make_gateway(mac="00:03:50:00:09:82")
    entry2 = _make_entry(hass, gw2)
    ent_reg.async_get_or_create("climate", DOMAIN, f"{gw2.mac}-4-1", config_entry=entry2)
    ent_reg.async_get_or_create("climate", DOMAIN, f"{gw2.mac}-4-1#4#01", config_entry=entry2)
    ent_reg.async_get_or_create(
        "sensor",
        DOMAIN,
        f"{gw2.mac}-4-1-temperature",
        config_entry=entry2,
        original_device_class=SensorDeviceClass.TEMPERATURE,
    )
    ent_reg.async_get_or_create(
        "sensor",
        DOMAIN,
        f"{gw2.mac}-4-1#4#01-temperature",
        config_entry=entry2,
        original_device_class=SensorDeviceClass.TEMPERATURE,
    )
    ent_reg.async_get_or_create(
        "sensor",
        DOMAIN,
        f"{gw2.mac}-4-1#4#01-humidity",
        config_entry=entry2,
    )
    sensors2: list[Any] = []
    _, add_s2 = _make_entity_adders(hass, [], sensors2)
    await sensor_setup_entry(hass, entry2, add_s2)
    await hass.async_block_till_done()
    assert {s.unique_id for s in sensors2} == {
        f"{gw2.mac}-4-1-temperature",
        f"{gw2.mac}-4-1#4#01-temperature",
        f"{gw2.mac}-4-1#4#01-humidity",
    }
    assert all(
        isinstance(s, (MyHOMEClimateTemperatureSensor, MyHOMEClimateHumiditySensor))
        for s in sensors2
    )
    assert await sensor_unload_entry(hass, entry2)

    # Orphaned routed {mac}-4-1#4#01-temperature stays dormant and never collides with local Zone 1
    gw3 = _make_gateway(mac="00:03:50:00:09:83")
    entry3 = _make_entry(hass, gw3)
    ent_reg.async_get_or_create("climate", DOMAIN, f"{gw3.mac}-4-1", config_entry=entry3)
    orphan_routed = ent_reg.async_get_or_create(
        "sensor",
        DOMAIN,
        f"{gw3.mac}-4-1#4#01-temperature",
        config_entry=entry3,
        original_device_class=SensorDeviceClass.TEMPERATURE,
    )
    sensors3: list[Any] = []
    _, add_s3 = _make_entity_adders(hass, [], sensors3)
    await sensor_setup_entry(hass, entry3, add_s3)
    await hass.async_block_till_done()
    assert len(sensors3) == 1
    assert isinstance(sensors3[0], MyHOMEClimateTemperatureSensor)
    assert sensors3[0].unique_id == f"{gw3.mac}-4-1-temperature"
    assert not any(isinstance(s, MyHOMETemperatureSensor) for s in sensors3)
    assert ent_reg.async_get(orphan_routed.entity_id) is not None


# ---------------------------------------------------------------------------
# 5. Shared-Bus Primary/Follower Ownership (#453) & prune_entity() (#524)
# ---------------------------------------------------------------------------


async def test_shared_bus_primary_follower_ownership_and_pruning(
    hass: HomeAssistant,
) -> None:
    """Follower skips & prunes companions owned by primary, while delegated WHO=4 keeps them."""
    pri_mac = "00:03:50:aa:00:01"
    fol_mac = "00:03:50:aa:00:02"

    pri_gw = _make_gateway(mac=pri_mac)
    pri_entry = _make_entry(hass, pri_gw)
    ent_reg = er.async_get(hass)
    # Primary owns Zone 1 climate + Zone 2 peer sensor directly
    ent_reg.async_get_or_create("climate", DOMAIN, f"{pri_mac}-4-1", config_entry=pri_entry)
    ent_reg.async_get_or_create("sensor", DOMAIN, f"{pri_mac}-4-2-temperature", config_entry=pri_entry)
    ent_reg.async_get_or_create("sensor", DOMAIN, f"{pri_mac}-4-2-humidity", config_entry=pri_entry)

    fol_gw = _make_gateway(mac=fol_mac, is_follower=True, primary_mac=pri_mac)
    fol_entry = _make_entry(
        hass,
        fol_gw,
        platforms={
            "climate": {
                "zone_1": {CONF_WHO: "4", CONF_WHERE: "1", CONF_HUMIDITY_SUPPORT: True},
                "zone_3": {CONF_WHO: "4", CONF_WHERE: "3", CONF_HUMIDITY_SUPPORT: True},
            }
        },
    )
    # Stale follower entries on primary-owned zones 1 & 2
    stale_fol_z1_t = ent_reg.async_get_or_create(
        "sensor",
        DOMAIN,
        f"{fol_mac}-4-1-temperature",
        config_entry=fol_entry,
        original_device_class=SensorDeviceClass.TEMPERATURE,
    )
    stale_fol_z1_h = ent_reg.async_get_or_create(
        "sensor", DOMAIN, f"{fol_mac}-4-1-humidity", config_entry=fol_entry
    )
    stale_fol_z2_h = ent_reg.async_get_or_create(
        "sensor", DOMAIN, f"{fol_mac}-4-2-humidity", config_entry=fol_entry
    )

    sensors: list[Any] = []
    _, add_s = _make_entity_adders(hass, [], sensors)
    await sensor_setup_entry(hass, fol_entry, add_s)
    await hass.async_block_till_done()

    # Follower creates Zone 3 (not on primary) and prunes Zones 1 & 2 (owned by primary)
    assert {s.unique_id for s in sensors} == {
        f"{fol_mac}-4-3-temperature",
        f"{fol_mac}-4-3-humidity",
    }
    assert ent_reg.async_get(stale_fol_z1_t.entity_id) is None
    assert ent_reg.async_get(stale_fol_z1_h.entity_id) is None
    assert ent_reg.async_get(stale_fol_z2_h.entity_id) is None


async def test_prune_entity_climate_removes_companions_and_empty_device(
    hass: HomeAssistant,
) -> None:
    """prune_entity() removes companion -temperature/-humidity and deletes empty climate devices (#524)."""
    dev_reg = dr.async_get(hass)
    ent_reg = er.async_get(hass)
    entry1 = MockConfigEntry(domain=DOMAIN, data={CONF_MAC: MAC}, unique_id=MAC)
    entry1.add_to_hass(hass)
    other_entry = MockConfigEntry(domain=DOMAIN, data={CONF_MAC: "00:03:50:00:00:99"}, unique_id="00:03:50:00:00:99")
    other_entry.add_to_hass(hass)

    # Case 1: canonical {mac}-4-1 + device-matched {mac}-4-1-temperature / -humidity + foreign entry sensor
    dev1 = dev_reg.async_get_or_create(
        config_entry_id=entry1.entry_id,
        identifiers={(DOMAIN, f"{MAC}-4-1")},
        name="Zone 1",
    )
    cli1 = ent_reg.async_get_or_create("climate", DOMAIN, f"{MAC}-4-01", config_entry=entry1, device_id=dev1.id)
    cli1_other = ent_reg.async_get_or_create("climate", DOMAIN, f"{MAC}-4-#1", config_entry=entry1, device_id=dev1.id)
    comp_t = ent_reg.async_get_or_create("sensor", DOMAIN, f"{MAC}-4-1-temperature", config_entry=entry1, device_id=dev1.id)
    comp_h = ent_reg.async_get_or_create("sensor", DOMAIN, f"{MAC}-4-1-humidity", config_entry=entry1, device_id=dev1.id)

    # Removing one climate while another climate remains on dev1 keeps companions alive
    prune_entity(hass, ent_reg, cli1, entry1.entry_id)
    assert ent_reg.async_get(comp_t.entity_id) is not None
    assert ent_reg.async_get(comp_h.entity_id) is not None
    assert dev_reg.async_get(dev1.id) is not None

    # Removing the last climate removes companions and deletes empty dev1
    prune_entity(hass, ent_reg, cli1_other, entry1.entry_id)
    assert ent_reg.async_get(comp_t.entity_id) is None
    assert ent_reg.async_get(comp_h.entity_id) is None
    assert dev_reg.async_get(dev1.id) is None

    # Case 2: exact unique_id suffix match + other config_entry isolation
    dev2 = dev_reg.async_get_or_create(
        config_entry_id=entry1.entry_id,
        identifiers={(DOMAIN, f"{MAC}-4-2")},
        name="Zone 2",
    )
    dev_reg.async_get_or_create(
        config_entry_id=other_entry.entry_id,
        identifiers={(DOMAIN, f"{MAC}-4-2")},
        name="Zone 2",
    )
    cli2 = ent_reg.async_get_or_create("climate", DOMAIN, f"{MAC}-4-2", config_entry=entry1, device_id=dev2.id)
    comp2_t = ent_reg.async_get_or_create("sensor", DOMAIN, f"{MAC}-4-2-temperature", config_entry=entry1, device_id=dev2.id)
    foreign_t = ent_reg.async_get_or_create("sensor", DOMAIN, "foreign-4-2-temperature", config_entry=other_entry, device_id=dev2.id)

    prune_entity(hass, ent_reg, cli2, entry1.entry_id)
    assert ent_reg.async_get(comp2_t.entity_id) is None
    assert ent_reg.async_get(foreign_t.entity_id) is not None
    assert dev_reg.async_get(dev2.id) is not None


# ---------------------------------------------------------------------------
# 6. RestoreSensor Native-Unit Round-Trip (#907) & myhome.yaml Compatibility (#441)
# ---------------------------------------------------------------------------


async def test_restore_sensor_native_unit_round_trip_and_fahrenheit_climate_guard(
    hass: HomeAssistant,
) -> None:
    """RestoreSensor keeps native °C / % across restarts even under US Imperial °F display units."""
    hass.config.units = US_CUSTOMARY_SYSTEM
    gw = _make_gateway()
    entry = _make_entry(hass, gw)

    # Pre-seed climate + companion temp + companion humidity stored states in °F / °C / %
    ent_reg = er.async_get(hass)
    cli_entry = ent_reg.async_get_or_create("climate", DOMAIN, f"{gw.mac}-4-1", config_entry=entry)
    temp_entry = ent_reg.async_get_or_create("sensor", DOMAIN, f"{gw.mac}-4-1-temperature", config_entry=entry)
    hum_entry = ent_reg.async_get_or_create("sensor", DOMAIN, f"{gw.mac}-4-1-humidity", config_entry=entry)

    # Mock HA restore cache for RestoreEntity (climate) + RestoreSensor (sensors)
    mock_restore_cache_with_extra_data(
        hass,
        [
            (
                State(cli_entry.entity_id, "heat", {"current_temperature": 68.0, "current_humidity": 52.0}),
                {},
            ),
            (
                State(temp_entry.entity_id, "68.0", {ATTR_UNIT_OF_MEASUREMENT: UnitOfTemperature.FAHRENHEIT}),
                SensorExtraStoredData(20.0, UnitOfTemperature.CELSIUS).as_dict(),
            ),
            (
                State(hum_entry.entity_id, "52.0", {ATTR_UNIT_OF_MEASUREMENT: PERCENTAGE}),
                SensorExtraStoredData(52.0, PERCENTAGE).as_dict(),
            ),
        ],
    )

    climates: list[MyHOMEClimate] = []
    sensors: list[Any] = []

    def _add_climates(entities: list[Any], update_before_add: bool = False) -> None:
        for ent in entities:
            ent.hass = hass
            ent.platform = MagicMock()
            ent.entity_id = cli_entry.entity_id
            ent.async_schedule_update_ha_state = MagicMock()
            ent.async_write_ha_state = MagicMock()
            climates.append(ent)
            hass.async_create_task(ent.async_added_to_hass())

    def _add_sensors(entities: list[Any], update_before_add: bool = False) -> None:
        for ent in entities:
            ent.hass = hass
            ent.platform = MagicMock()
            ent.entity_id = (
                hum_entry.entity_id
                if isinstance(ent, MyHOMEClimateHumiditySensor)
                else temp_entry.entity_id
            )
            ent.async_schedule_update_ha_state = MagicMock()
            ent.async_write_ha_state = MagicMock()
            sensors.append(ent)
            hass.async_create_task(ent.async_added_to_hass())

    # Set up sensor first so it receives SIGNAL_NEW_CLIMATE_ZONE from climate.async_added_to_hass()
    await sensor_setup_entry(hass, entry, _add_sensors)
    await climate_setup_entry(hass, entry, _add_climates)
    await hass.async_block_till_done()

    assert len(climates) == 1
    assert climates[0]._current_temperature_restored is True
    by_uid = {s.unique_id: s for s in sensors}
    assert by_uid[f"{gw.mac}-4-1-temperature"].native_value == 20.0
    assert by_uid[f"{gw.mac}-4-1-humidity"].native_value == 52.0

    # Also verify migration fallback when SensorExtraStoredData is absent (68.0 °F -> 20.0 °C)
    legacy_temp = MyHOMEClimateTemperatureSensor(
        hass=hass,
        name="Zone 9",
        device_id="9",
        zone="9",
        interface=None,
        manufacturer="BTicino",
        model="Heating Zone",
        gateway=gw,
    )
    await legacy_temp.async_restore_last_state(
        State("sensor.z9_temperature", "68.0", {ATTR_UNIT_OF_MEASUREMENT: UnitOfTemperature.FAHRENHEIT})
    )
    assert legacy_temp.native_value == 20.0


async def test_yaml_sensor_compatibility_and_removal_upgrade_rebind(
    hass: HomeAssistant,
) -> None:
    """Explicit myhome.yaml zone sensor prevents duplicates; removing YAML rebinds to passive companion."""
    gw = _make_gateway()
    entry = _make_entry(
        hass,
        gw,
        platforms={
            "climate": {
                "z1": {CONF_WHO: "4", CONF_WHERE: "1", CONF_NAME: "Living"},
            },
            "sensor": {
                "bad_item": "not_a_dict",  # type: ignore[dict-item]
                "4-01": {
                    CONF_WHO: "4",
                    CONF_WHERE: "01",
                    CONF_NAME: "Explicit Zone 1 Temp",
                    CONF_DEVICE_CLASS: SensorDeviceClass.TEMPERATURE,
                    CONF_MANUFACTURER: "BTicino",
                    CONF_DEVICE_MODEL: "L4692",
                },
                "probe_101": {
                    CONF_WHO: "4",
                    CONF_WHERE: "101",
                    CONF_NAME: "Probe 101",
                    CONF_DEVICE_CLASS: SensorDeviceClass.TEMPERATURE,
                    CONF_MANUFACTURER: "BTicino",
                    CONF_DEVICE_MODEL: "L4692",
                },
                "act_1_1": {
                    CONF_WHO: "4",
                    CONF_WHERE: "1#1",
                    CONF_NAME: "Actuator 1#1",
                    CONF_DEVICE_CLASS: SensorDeviceClass.TEMPERATURE,
                    CONF_MANUFACTURER: "BTicino",
                    CONF_DEVICE_MODEL: "L4692",
                },
                "non_zone_0": {
                    CONF_WHO: "4",
                    CONF_WHERE: "0",
                    CONF_NAME: "Broadcast",
                    CONF_DEVICE_CLASS: SensorDeviceClass.TEMPERATURE,
                    CONF_MANUFACTURER: "BTicino",
                    CONF_DEVICE_MODEL: "L4692",
                },
                "humidity_yaml": {
                    CONF_WHO: "4",
                    CONF_WHERE: "2",
                    CONF_NAME: "Hum 2",
                    CONF_DEVICE_CLASS: SensorDeviceClass.HUMIDITY,
                    CONF_MANUFACTURER: "BTicino",
                    CONF_DEVICE_MODEL: "H4691",
                },
            },
        },
    )
    ent_reg = er.async_get(hass)
    stale_bcast = ent_reg.async_get_or_create(
        "sensor",
        DOMAIN,
        f"{gw.mac}-4-0-temperature",
        config_entry=entry,
        original_device_class=SensorDeviceClass.TEMPERATURE,
    )
    # Alias keyed under "01" matches restored unique_id "{mac}-4-01-temperature" in duplicate_temperature (ctx.cfg truthy)
    entry.runtime_data.platforms["sensor"]["01"] = entry.runtime_data.platforms["sensor"]["4-01"]
    entry.runtime_data.platforms["sensor"]["non_who4"] = {CONF_WHO: "1", CONF_WHERE: "12"}
    area_reg = ar.async_get(hass)
    living_area = area_reg.async_get_or_create("Living Area")
    z01_reg = ent_reg.async_get_or_create(
        "sensor",
        DOMAIN,
        f"{gw.mac}-4-01-temperature",
        suggested_object_id="living_room_lts",
        config_entry=entry,
        original_device_class=SensorDeviceClass.TEMPERATURE,
    )
    ent_reg.async_update_entity(
        z01_reg.entity_id,
        name="Custom Living Temp",
        area_id=living_area.id,
    )
    mock_restore_cache_with_extra_data(
        hass,
        [
            (
                State(z01_reg.entity_id, "21.4", {ATTR_UNIT_OF_MEASUREMENT: UnitOfTemperature.CELSIUS}),
                SensorExtraStoredData(21.4, UnitOfTemperature.CELSIUS).as_dict(),
            )
        ],
    )

    # Phase 1: while explicit YAML where: "01" is configured, unique_id stays {mac}-4-01-temperature
    # and no duplicate companion is created for Zone 1.
    sensors: list[Any] = []
    _, add_s = _make_entity_adders(hass, [], sensors)
    await sensor_setup_entry(hass, entry, add_s)
    await hass.async_block_till_done()

    assert ent_reg.async_get(stale_bcast.entity_id) is None
    assert ent_reg.async_get(z01_reg.entity_id) is not None
    assert ent_reg.async_get(z01_reg.entity_id).unique_id == f"{gw.mac}-4-01-temperature"
    z1_sensors = [s for s in sensors if "4-01-temperature" in s.unique_id or "4-1-temperature" in s.unique_id]
    assert len(z1_sensors) == 1
    assert isinstance(z1_sensors[0], MyHOMETemperatureSensor)
    assert await sensor_unload_entry(hass, entry)

    # Phase 2: remove YAML sensor block WITHOUT manually seeding 4-1-temperature; also seed
    # legacy #2, zero-padded F422 03#4#2, and duplicate 04 alongside canonical 4.
    ent_reg.async_get_or_create("climate", DOMAIN, f"{gw.mac}-4-1", config_entry=entry)
    ent_reg.async_get_or_create("climate", DOMAIN, f"{gw.mac}-4-2", config_entry=entry)
    ent_reg.async_get_or_create("climate", DOMAIN, f"{gw.mac}-4-3#4#02", config_entry=entry)
    ent_reg.async_get_or_create("climate", DOMAIN, f"{gw.mac}-4-4", config_entry=entry)

    hash2_reg = ent_reg.async_get_or_create(
        "sensor",
        DOMAIN,
        f"{gw.mac}-4-#2-temperature",
        suggested_object_id="bed_2_lts",
        config_entry=entry,
        original_device_class=SensorDeviceClass.TEMPERATURE,
    )
    ent_reg.async_update_entity(hash2_reg.entity_id, name="Custom Bed 2")

    routed03_reg = ent_reg.async_get_or_create(
        "sensor",
        DOMAIN,
        f"{gw.mac}-4-03#4#2-temperature",
        suggested_object_id="routed_3_lts",
        config_entry=entry,
        original_device_class=SensorDeviceClass.TEMPERATURE,
    )

    canon4_reg = ent_reg.async_get_or_create(
        "sensor",
        DOMAIN,
        f"{gw.mac}-4-4-temperature",
        suggested_object_id="zone_4_canon",
        config_entry=entry,
        original_device_class=SensorDeviceClass.TEMPERATURE,
    )
    dup04_reg = ent_reg.async_get_or_create(
        "sensor",
        DOMAIN,
        f"{gw.mac}-4-04-temperature",
        suggested_object_id="zone_4_dup04",
        config_entry=entry,
        original_device_class=SensorDeviceClass.TEMPERATURE,
    )
    ent_reg.async_update_entity(dup04_reg.entity_id, name="Carried Zone 4 Name")

    entry.runtime_data.platforms["sensor"] = {}
    sensors_after: list[Any] = []

    def _add_s2_real_ids(entities: list[Any], update_before_add: bool = False) -> None:
        for ent in entities:
            ent.hass = hass
            ent.platform = MagicMock()
            existing_eid = ent_reg.async_get_entity_id("sensor", DOMAIN, ent.unique_id)
            ent.entity_id = existing_eid or f"sensor.mock_after_{len(sensors_after)}"
            ent.async_schedule_update_ha_state = MagicMock()
            ent.async_write_ha_state = MagicMock()
            sensors_after.append(ent)
            hass.async_create_task(ent.async_added_to_hass())

    await sensor_setup_entry(hass, entry, _add_s2_real_ids)
    await hass.async_block_till_done()

    # 4-01-temperature migrated in-place -> 4-1-temperature preserving entity_id, custom name, area, and LTS!
    migrated_z1 = ent_reg.async_get(z01_reg.entity_id)
    assert migrated_z1 is not None
    assert migrated_z1.entity_id == "sensor.living_room_lts"
    assert migrated_z1.unique_id == f"{gw.mac}-4-1-temperature"
    assert migrated_z1.name == "Custom Living Temp"
    assert migrated_z1.area_id == living_area.id
    assert ent_reg.async_get_entity_id("sensor", DOMAIN, f"{gw.mac}-4-01-temperature") is None

    # #2 and 03#4#2 migrated in-place; duplicate 04 carried customisations to canonical 4 and was removed
    assert ent_reg.async_get(hash2_reg.entity_id).unique_id == f"{gw.mac}-4-2-temperature"
    assert ent_reg.async_get(hash2_reg.entity_id).name == "Custom Bed 2"
    assert ent_reg.async_get(routed03_reg.entity_id).unique_id == f"{gw.mac}-4-3#4#02-temperature"
    assert ent_reg.async_get(dup04_reg.entity_id) is None
    assert ent_reg.async_get(canon4_reg.entity_id).name == "Carried Zone 4 Name"

    rebound = [s for s in sensors_after if s.unique_id == f"{gw.mac}-4-1-temperature"]
    assert len(rebound) == 1
    assert isinstance(rebound[0], MyHOMEClimateTemperatureSensor)
    assert rebound[0].should_poll is False
    assert rebound[0].entity_id == "sensor.living_room_lts"
    assert rebound[0].native_value == 21.4


# ---------------------------------------------------------------------------
# 7. Live Hardware Trace Replays & Branch Coverage Closure
# ---------------------------------------------------------------------------


async def test_live_replay_discussion_770_f453_n4692_companion_sensors(
    hass: HomeAssistant,
) -> None:
    """Replay Discussion #770 F453 + 3550 + N4692 sweep: passive temps, no #0 companion, no humidity."""
    doc = json.loads(F453_TRACE_PATH.read_text(encoding="utf-8"))
    gw = _make_gateway("F453", "2.0.10", mac="00:03:50:00:07:70", supports_zone_status=False)
    entry = _make_entry(
        hass,
        gw,
        platforms={
            "climate": {
                "4-#0": {CONF_WHO: "4", CONF_WHERE: "#0", CONF_CENTRAL: True, CONF_NAME: "Central Unit"},
                "4-1": {CONF_WHO: "4", CONF_WHERE: "1", CONF_NAME: "Zone 1", CONF_STANDALONE: False},
            }
        },
        options={CONF_BROADCAST_RESYNC: True},
    )

    climates: list[MyHOMEClimate] = []
    sensors: list[Any] = []
    add_c, add_s = _make_entity_adders(hass, climates, sensors)
    await sensor_setup_entry(hass, entry, add_s)
    await climate_setup_entry(hass, entry, add_c)
    await hass.async_block_till_done()

    gw.send.reset_mock()
    gw.send_status_request.reset_mock()

    signal = f"myhome_message_{gw.mac}"
    for frame in doc["frames"]:
        if frame["direction"] != "rx":
            continue
        msg = OWNEvent.parse(frame["raw"]) or OWNMessage.parse(frame["raw"])
        assert msg is not None
        async_dispatcher_send(hass, signal, msg)
    await hass.async_block_till_done()

    temps = [s for s in sensors if isinstance(s, MyHOMEClimateTemperatureSensor)]
    hums = [s for s in sensors if isinstance(s, MyHOMEClimateHumiditySensor)]
    uids = {t.unique_id for t in temps}

    assert hums == []
    assert f"{gw.mac}-4-#0-temperature" not in uids
    assert f"{gw.mac}-4-1-temperature" in uids
    by_uid = {t.unique_id: t for t in temps}
    assert by_uid[f"{gw.mac}-4-1-temperature"].native_value == 13.0
    assert by_uid[f"{gw.mac}-4-36-temperature"].native_value == 13.3

    # Verify calling async_update across all created temperature companions sends 0 frames
    gw.send.reset_mock()
    gw.send_status_request.reset_mock()
    for t in temps:
        await t.async_update()
    gw.send.assert_not_called()
    gw.send_status_request.assert_not_called()

    for ent in climates + sensors:
        for remove_cb in list(getattr(ent, "_on_remove", ())):
            remove_cb()
    assert await sensor_unload_entry(hass, entry)
    assert await climate_unload_entry(hass, entry)


async def test_live_replay_f461_2026_10_23_temp_and_dynamic_humidity(
    hass: HomeAssistant,
) -> None:
    """Replay live F461 2.0.11 trace: Zone 2 gets companion Temperature (24.0 °C) and dynamic Humidity (38.0 %)."""
    doc = json.loads(F461_TRACE_PATH.read_text(encoding="utf-8"))
    f461_mac = doc["gateway"]["mac"]
    gw = _make_gateway("F461", "2.0.11", mac=f461_mac)
    entry = _make_entry(hass, gw)

    climates: list[MyHOMEClimate] = []
    sensors: list[Any] = []
    add_c, add_s = _make_entity_adders(hass, climates, sensors)
    await sensor_setup_entry(hass, entry, add_s)
    await climate_setup_entry(hass, entry, add_c)
    await hass.async_block_till_done()

    signal = f"myhome_message_{gw.mac}"
    for frame in doc["frames"]:
        if frame["direction"] != "rx" or frame.get("run") == "run5_injection_flags":
            continue
        raw = frame["raw"]
        if not raw.startswith(("*4*", "*#4*")):
            continue
        msg = OWNEvent.parse(raw) or OWNMessage.parse(raw)
        if msg is not None:
            async_dispatcher_send(hass, signal, msg)
    await hass.async_block_till_done()

    by_uid = {s.unique_id: s for s in sensors}
    assert f"{f461_mac}-4-2-temperature" in by_uid
    assert f"{f461_mac}-4-2-humidity" in by_uid
    assert by_uid[f"{f461_mac}-4-2-temperature"].native_value == 24.0
    assert by_uid[f"{f461_mac}-4-2-humidity"].native_value == 38.0
    assert device_info_to_dict(by_uid[f"{f461_mac}-4-2-temperature"].device_info)["identifiers"] == {
        (DOMAIN, f"{f461_mac}-4-2")
    }
    assert device_info_to_dict(by_uid[f"{f461_mac}-4-2-humidity"].device_info)["identifiers"] == {
        (DOMAIN, f"{f461_mac}-4-2")
    }


async def test_live_replay_issue_949_mhs1_temp_humidity_and_output_level(
    hass: HomeAssistant,
) -> None:
    """Replay live MHS1 full-cycle trace: Zone 1 unites climate + temp + humidity + 1#3 output-level."""
    doc = json.loads(MHS1_FULL_CYCLE_TRACE_PATH.read_text(encoding="utf-8"))
    mhs_mac = doc["gateway"]["mac"]
    gw = _make_gateway("MyHomeServer1", "2.87.13", mac=mhs_mac)
    entry = _make_entry(hass, gw)

    climates: list[MyHOMEClimate] = []
    sensors: list[Any] = []
    add_c, add_s = _make_entity_adders(hass, climates, sensors)
    await sensor_setup_entry(hass, entry, add_s)
    await climate_setup_entry(hass, entry, add_c)
    await hass.async_block_till_done()

    signal = f"myhome_message_{gw.mac}"
    for frame in doc["frames"]:
        if frame["direction"] != "rx":
            continue
        msg = OWNEvent.parse(frame["raw"]) or OWNMessage.parse(frame["raw"])
        if msg is not None:
            async_dispatcher_send(hass, signal, msg)
    await hass.async_block_till_done()

    by_uid = {s.unique_id: s for s in sensors}
    z1_temp = by_uid[f"{mhs_mac}-4-1-temperature"]
    z1_hum = by_uid[f"{mhs_mac}-4-1-humidity"]
    z1_act3 = by_uid[f"{mhs_mac}-4-1#3-output-level"]

    assert isinstance(z1_temp, MyHOMEClimateTemperatureSensor)
    assert isinstance(z1_hum, MyHOMEClimateHumiditySensor)
    assert isinstance(z1_act3, MyHOMEActuatorOutputSensor)
    assert z1_temp.native_value == 19.6
    assert z1_hum.native_value == 61.0
    assert z1_act3.native_value == 0

    expected_ident = {(DOMAIN, f"{mhs_mac}-4-1")}
    assert device_info_to_dict(z1_temp.device_info)["identifiers"] == expected_ident
    assert device_info_to_dict(z1_hum.device_info)["identifiers"] == expected_ident
    assert device_info_to_dict(z1_act3.device_info)["identifiers"] == expected_ident


async def test_edge_cases_branch_coverage(hass: HomeAssistant) -> None:
    """Exercise defensive edge branches across climate.py, climate_discovery.py, discovery.py, sensor.py."""
    gw = _make_gateway()
    alt_mac = "00:03:50:00:09:99"
    entry = _make_entry(
        hass,
        gw,
        mac_override=alt_mac,
        platforms={
            "climate": {
                "bad": "not_a_dict",  # type: ignore[dict-item]
                "no_where": {CONF_WHO: "4", CONF_NAME: "NoWhere"},
                "probe_101": {CONF_WHO: "4", CONF_WHERE: "101", CONF_NAME: "Probe"},
                "central_cfg": {CONF_WHO: "4", CONF_WHERE: "#0", CONF_CENTRAL: True},
                "zero_zone": {CONF_WHO: "4", CONF_WHERE: "0", CONF_NAME: "Invalid0"},
                "central_zone_9": {CONF_WHO: "4", CONF_WHERE: "9", CONF_CENTRAL: True},
                "inline_if": {
                    CONF_WHO: "4",
                    CONF_WHERE: "3#4#02",
                    CONF_BUS_INTERFACE: "02",
                    CONF_NAME: "Inline",
                },
            }
        },
    )

    ent_reg = er.async_get(hass)
    # Registry entries exercising alt_entry_mac replacement & invalid/central skips
    ent_reg.async_get_or_create("climate", DOMAIN, f"{gw.mac}-4-101", config_entry=entry)
    ent_reg.async_get_or_create("climate", DOMAIN, f"{gw.mac}-4-#0", config_entry=entry)
    ent_reg.async_get_or_create("climate", DOMAIN, f"{gw.mac}-4-0", config_entry=entry)
    ent_reg.async_get_or_create("climate", DOMAIN, f"{gw.mac}-4-9", config_entry=entry)
    ent_reg.async_get_or_create("climate", DOMAIN, f"{gw.mac}-4-04", config_entry=entry)
    ent_reg.async_get_or_create("sensor", DOMAIN, f"{alt_mac}-4-6-humidity", config_entry=entry)
    # Force fallback match loop in _climate_zone_config
    entry.runtime_data.platforms["climate"]["alt_key"] = {
        CONF_WHO: "4",
        CONF_WHERE: "04",
        CONF_BUS_INTERFACE: 3,  # non-existent on local bus -> covers loop w/ int bus_interface
    }
    entry.runtime_data.platforms["climate"]["alt_z4"] = {
        CONF_WHO: "4",
        CONF_WHERE: "4",
        CONF_HUMIDITY_SUPPORT: False,
    }

    sensors: list[Any] = []
    _, add_s = _make_entity_adders(hass, [], sensors)
    await sensor_setup_entry(hass, entry, add_s)
    await hass.async_block_till_done()

    # Central, probe, and actuator zones return None payload from _companion_zone_payload
    central_c = MyHOMEClimate(
        hass=hass,
        name="Central",
        device_id="4-#0",
        who="4",
        where="#0",
        heating=True,
        cooling=False,
        fan=False,
        standalone=False,
        central=True,
        manufacturer="BTicino",
        model="Heating Zone",
        gateway=gw,
    )
    assert central_c._companion_zone_payload(include_temperature=True) is None
    central_c._announce_climate_zone()
    central_c._announce_climate_humidity()

    invalid_c = MyHOMEClimate(
        hass=None,
        name="Invalid",
        device_id="4-bad",
        who="4",
        where="bad",
        heating=True,
        cooling=False,
        fan=False,
        standalone=False,
        central=False,
        manufacturer="BTicino",
        model="Heating Zone",
        gateway=gw,
    )
    assert invalid_c._companion_zone_payload(include_temperature=False) is None
    invalid_c._announce_climate_zone()
    invalid_c._climate_announced = True
    invalid_c._announce_climate_humidity()

    hass_no_payload = MyHOMEClimate(
        hass=hass,
        name="InvalidZone",
        device_id="4-bad",
        who="4",
        where="bad",
        heating=True,
        cooling=False,
        fan=False,
        standalone=False,
        central=False,
        manufacturer="BTicino",
        model="Heating Zone",
        gateway=gw,
    )
    hass_no_payload._announce_climate_zone()
    hass_no_payload._climate_announced = True
    hass_no_payload._announce_climate_humidity()

    valid_prefixed_c = MyHOMEClimate(
        hass=None,
        name="Z8",
        device_id="4-8",
        who="4",
        where="8",
        heating=True,
        cooling=False,
        fan=False,
        standalone=False,
        central=False,
        manufacturer="BTicino",
        model="Heating Zone",
        gateway=gw,
    )
    payload8 = valid_prefixed_c._companion_zone_payload(include_temperature=True)
    assert payload8 is not None
    assert payload8["device_id"] == "8"
    valid_prefixed_c._announce_climate_zone()

    # Live dispatcher edge inputs: invalid zone, out-of-range humidity, duplicate zone/humidity,
    # and simultaneous temp+humidity creation via SIGNAL_NEW_CLIMATE_ZONE
    async_dispatcher_send(
        hass,
        SIGNAL_NEW_CLIMATE_ZONE.format(gw.mac),
        {"zone": "#0", "humidity": True, "current_humidity": 120},
    )
    async_dispatcher_send(
        hass,
        SIGNAL_NEW_CLIMATE_ZONE.format(gw.mac),
        {"zone": "10", "temperature": 22.0, "humidity": True, "current_humidity": 51.0},
    )
    async_dispatcher_send(
        hass,
        SIGNAL_NEW_CLIMATE_ZONE.format(gw.mac),
        {"zone": "8", "humidity": False, "current_humidity": 150},
    )
    async_dispatcher_send(
        hass,
        SIGNAL_NEW_CLIMATE_ZONE.format(gw.mac),
        {"zone": "8", "humidity": False},
    )
    async_dispatcher_send(
        hass,
        SIGNAL_CLIMATE_HUMIDITY.format(gw.mac),
        {"zone": "#0", "current_humidity": 50},
    )
    for invalid_hum in (-5, 150, math.nan, True):
        async_dispatcher_send(
            hass,
            SIGNAL_CLIMATE_HUMIDITY.format(gw.mac),
            {"zone": "8", "current_humidity": invalid_hum},
        )
    await hass.async_block_till_done()
    assert not any(
        isinstance(s, MyHOMEClimateHumiditySensor)
        and s.unique_id == f"{gw.mac}-4-8-humidity"
        for s in sensors
    )

    async_dispatcher_send(
        hass,
        SIGNAL_CLIMATE_HUMIDITY.format(gw.mac),
        {"zone": "8", "current_humidity": 55},
    )
    async_dispatcher_send(
        hass,
        SIGNAL_CLIMATE_HUMIDITY.format(gw.mac),
        {"zone": "8", "current_humidity": 56},
    )
    await hass.async_block_till_done()
    z8_hums = [
        s
        for s in sensors
        if isinstance(s, MyHOMEClimateHumiditySensor)
        and s.unique_id == f"{gw.mac}-4-8-humidity"
    ]
    assert len(z8_hums) == 1
    assert z8_hums[0].native_value == 55.0

    # _safe_registry_entries exception fallback & has_humidity_state with live attribute & None state
    hass.states.async_set("climate.live_hum", "heat", {"current_humidity": 44})
    assert has_humidity_state(hass, "climate.live_hum") is True
    with patch("custom_components.myhome.sensor.er.async_get", side_effect=RuntimeError("boom")):
        gw_err = _make_gateway(mac="00:03:50:00:09:77")
        entry_err = _make_entry(hass, gw_err)
        await sensor_setup_entry(hass, entry_err, lambda _e, update_before_add=False: None)
        async_dispatcher_send(
            hass,
            SIGNAL_NEW_CLIMATE_ZONE.format(gw_err.mac),
            {"zone": "1", "temperature": 21.0, "humidity": True, "current_humidity": 40.0},
        )
        await hass.async_block_till_done()


# ---------------------------------------------------------------------------
# 8. Real EntityPlatform, Startup Seeding & OWNEvent.parse() Wire Hardening
# ---------------------------------------------------------------------------


async def test_real_entity_platform_dormant_skip_rebind_and_exclusions(
    hass: HomeAssistant,
    tmp_path: Path,
) -> None:
    """Real EntityPlatform setup: zero collisions, Dormant Skip rebind, exclusions & #453 follower pruning."""
    pri_mac = "00:03:50:00:09:90"
    fol_mac = "00:03:50:00:09:91"
    hass.states.async_set("sensor.ext_probe_11", "18.5", {"unit_of_measurement": "°C"})

    yaml_path = tmp_path / "myhome.yaml"
    yaml_path.write_text(
        f"{pri_mac}:\n"
        "  climate:\n"
        "    central_zone_1:\n"
        '      zone: "1"\n'
        '      name: "Central 4Z"\n'
        "      central: true\n"
        "  switch:\n"
        "    whole_zone_8:\n"
        "      who: 4\n"
        '      where: "8"\n'
        '      name: "Towel 8"\n'
        "  sensor:\n"
        "    f520_meter_8:\n"
        "      who: 18\n"
        '      where: "8"\n'
        '      name: "Meter 8"\n'
        "      class: power\n"
        "    lux_12:\n"
        "      who: 1\n"
        '      where: "12"\n'
        '      name: "Lux 12"\n'
        "      class: illuminance\n",
        encoding="utf-8",
    )

    pri_entry = MockConfigEntry(
        domain=DOMAIN,
        title="Primary MHS1",
        data={
            CONF_HOST: "192.0.2.90",
            CONF_PORT: 20000,
            CONF_PASSWORD: "pass",
            CONF_MAC: pri_mac,
            CONF_NAME: "MyHomeServer1",
        },
        options={
            CONF_FILE_PATH: str(yaml_path),
            CONF_BUS_TOPOLOGY: TOPOLOGY_SHARED,
            CONF_GATEWAY_ROLE: ROLE_PRIMARY,
            CONF_IGNORED_ADDRESSES: [{"who": 4, "where": "12"}],
        },
        subentries_data=[
            {
                "data": {CONF_WHO: "4", CONF_WHERE: "11", "entity_id": "sensor.ext_probe_11"},
                "subentry_type": SUBENTRY_TYPE_EXPOSE,
                "title": "Expose Zone 11",
                "unique_id": "exp_4_11",
            },
            {
                "data": {CONF_WHO: "4", CONF_WHERE: "7#1"},
                "subentry_type": SUBENTRY_TYPE_THERMO_SWITCH,
                "title": "Switch 7#1",
                "unique_id": "sw_7_1",
            },
        ],
        unique_id=pri_mac,
    )
    pri_entry.add_to_hass(hass)

    fol_entry = MockConfigEntry(
        domain=DOMAIN,
        title="Follower Standby",
        data={
            CONF_HOST: "192.0.2.91",
            CONF_PORT: 20000,
            CONF_PASSWORD: "pass",
            CONF_MAC: fol_mac,
            CONF_NAME: "F454",
        },
        options={
            CONF_BUS_TOPOLOGY: TOPOLOGY_SHARED,
            CONF_GATEWAY_ROLE: ROLE_STANDBY,
            CONF_PRIMARY_GATEWAY: pri_mac,
        },
        unique_id=fol_mac,
    )
    fol_entry.add_to_hass(hass)

    ent_reg = er.async_get(hass)
    # Local Zone 1 climate + humidity (coexisting with 4-zone central unit #0#1!)
    # + orphaned routed 1#4#01-temperature + UI-deleted Zone 5 companions
    ent_reg.async_get_or_create("climate", DOMAIN, f"{pri_mac}-4-1", config_entry=pri_entry)
    z1_hum_reg = ent_reg.async_get_or_create(
        "sensor",
        DOMAIN,
        f"{pri_mac}-4-1-humidity",
        config_entry=pri_entry,
        original_device_class=SensorDeviceClass.HUMIDITY,
    )
    orphan_routed = ent_reg.async_get_or_create(
        "sensor",
        DOMAIN,
        f"{pri_mac}-4-1#4#01-temperature",
        suggested_object_id="routed_1_01_temp",
        config_entry=pri_entry,
        original_device_class=SensorDeviceClass.TEMPERATURE,
    )
    dormant_z5_t = ent_reg.async_get_or_create(
        "sensor",
        DOMAIN,
        f"{pri_mac}-4-5-temperature",
        suggested_object_id="bedroom_5_temp",
        config_entry=pri_entry,
        original_device_class=SensorDeviceClass.TEMPERATURE,
    )
    dormant_z5_h = ent_reg.async_get_or_create(
        "sensor",
        DOMAIN,
        f"{pri_mac}-4-5-humidity",
        suggested_object_id="bedroom_5_hum",
        config_entry=pri_entry,
        original_device_class=SensorDeviceClass.HUMIDITY,
    )

    # Excluded companion entries that MUST be pruned on real EntityPlatform setup
    # (including Zone 8 and Zone 12 despite WHO=18/WHO=1 sensors sharing bare WHERE "8"/"12")
    stale_broadcast_t = ent_reg.async_get_or_create(
        "sensor",
        DOMAIN,
        f"{pri_mac}-4-0-temperature",
        config_entry=pri_entry,
        original_device_class=SensorDeviceClass.TEMPERATURE,
    )
    stale_ignored_t = ent_reg.async_get_or_create(
        "sensor",
        DOMAIN,
        f"{pri_mac}-4-12-temperature",
        config_entry=pri_entry,
        original_device_class=SensorDeviceClass.TEMPERATURE,
    )
    stale_expose_t = ent_reg.async_get_or_create(
        "sensor",
        DOMAIN,
        f"{pri_mac}-4-11-temperature",
        config_entry=pri_entry,
        original_device_class=SensorDeviceClass.TEMPERATURE,
    )
    stale_whole_sw_t = ent_reg.async_get_or_create(
        "sensor",
        DOMAIN,
        f"{pri_mac}-4-8-temperature",
        config_entry=pri_entry,
        original_device_class=SensorDeviceClass.TEMPERATURE,
    )
    stale_act_sw_t = ent_reg.async_get_or_create(
        "sensor",
        DOMAIN,
        f"{pri_mac}-4-7-temperature",
        config_entry=pri_entry,
        original_device_class=SensorDeviceClass.TEMPERATURE,
    )
    stale_fol_z1_t = ent_reg.async_get_or_create(
        "sensor",
        DOMAIN,
        f"{fol_mac}-4-1-temperature",
        config_entry=fol_entry,
        original_device_class=SensorDeviceClass.TEMPERATURE,
    )

    sent_status_frames: list[str] = []

    async def _record_status(cmd: Any, *_args: Any, **_kwargs: Any) -> bool:
        sent_status_frames.append(str(cmd))
        return True

    with (
        patch(
            "custom_components.myhome.gateway.OWNSession.test_connection",
            return_value={"Success": True, "Message": None},
        ),
        patch("custom_components.myhome.gateway.MyHOMEGatewayHandler.listening_loop"),
        patch("custom_components.myhome.gateway.MyHOMEGatewayHandler.sending_loop"),
        patch("custom_components.myhome.gateway.MyHOMEGatewayHandler.initial_discovery", AsyncMock()),
        patch(
            "custom_components.myhome.gateway.MyHOMEGatewayHandler.send_status_request",
            side_effect=_record_status,
        ),
    ):
        assert await hass.config_entries.async_setup(pri_entry.entry_id)
        await hass.async_block_till_done()
        pri_gw = hass.data[DOMAIN][pri_mac][CONF_ENTITY]
        pri_gw._on_event_connection_state_change(True)
        await hass.async_block_till_done()

        # 4-zone central #0#1 creates zero companions, while real Zone 1 companions stay live!
        assert ent_reg.async_get_entity_id("climate", DOMAIN, f"{pri_mac}-4-#0#1") is not None
        assert ent_reg.async_get_entity_id("sensor", DOMAIN, f"{pri_mac}-4-#0#1-temperature") is None
        assert ent_reg.async_get_entity_id("sensor", DOMAIN, f"{pri_mac}-4-#0#1-humidity") is None

        z1_eid = ent_reg.async_get_entity_id("sensor", DOMAIN, f"{pri_mac}-4-1-temperature")
        assert z1_eid is not None
        assert hass.states.get(z1_eid) is not None
        assert ent_reg.async_get(z1_hum_reg.entity_id) is not None
        assert hass.states.get(z1_hum_reg.entity_id) is not None

        for dormant in (orphan_routed, dormant_z5_t, dormant_z5_h):
            assert ent_reg.async_get(dormant.entity_id) is not None
            assert hass.states.get(dormant.entity_id) is None

        assert "*#4*5*0##" not in sent_status_frames
        assert "*#4*1#4#01*0##" not in sent_status_frames

        # Excluded & follower-duplicate companions were pruned from entity_registry
        for pruned in (
            stale_broadcast_t,
            stale_ignored_t,
            stale_expose_t,
            stale_whole_sw_t,
            stale_act_sw_t,
            stale_fol_z1_t,
        ):
            assert ent_reg.async_get(pruned.entity_id) is None

        # Confirming Zone 5 on the bus rebinds BOTH dormant sensor.bedroom_5_temp and sensor.bedroom_5_hum!
        for raw in ("*#4*5*0*0213##", "*4*1*5##"):
            msg = OWNEvent.parse(raw)
            assert msg is not None
            await pri_gw._event_dispatcher.process_message(msg)
        await hass.async_block_till_done()

        s5_temp = hass.states.get("sensor.bedroom_5_temp")
        s5_hum = hass.states.get("sensor.bedroom_5_hum")
        assert s5_temp is not None
        assert s5_temp.state == "21.3"
        assert s5_hum is not None

        assert await hass.config_entries.async_unload(fol_entry.entry_id)
        assert await hass.config_entries.async_unload(pri_entry.entry_id)


async def test_runtime_dim15_non_probe_where_creates_no_collision(
    hass: HomeAssistant,
) -> None:
    """Runtime OWNEvent.parse('*#4*1*15*0215##') on non-probe WHERE=1 causes zero collision."""
    mac = "00:03:50:00:09:92"
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="MHS1 Dim15",
        data={
            CONF_HOST: "192.0.2.92",
            CONF_PORT: 20000,
            CONF_PASSWORD: "pass",
            CONF_MAC: mac,
            CONF_NAME: "MyHomeServer1",
        },
        unique_id=mac,
    )
    entry.add_to_hass(hass)
    ent_reg = er.async_get(hass)
    ent_reg.async_get_or_create("climate", DOMAIN, f"{mac}-4-1", config_entry=entry)

    with (
        patch(
            "custom_components.myhome.gateway.OWNSession.test_connection",
            return_value={"Success": True, "Message": None},
        ),
        patch("custom_components.myhome.gateway.MyHOMEGatewayHandler.listening_loop"),
        patch("custom_components.myhome.gateway.MyHOMEGatewayHandler.sending_loop"),
        patch("custom_components.myhome.gateway.MyHOMEGatewayHandler.initial_discovery", AsyncMock()),
        patch("custom_components.myhome.gateway.MyHOMEGatewayHandler.send_status_request", AsyncMock()),
    ):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
        gw = hass.data[DOMAIN][mac][CONF_ENTITY]
        gw._on_event_connection_state_change(True)
        await hass.async_block_till_done()

        await gw._event_dispatcher.process_message(OWNEvent.parse("*#4*1*0*0210##"))
        await gw._event_dispatcher.process_message(OWNEvent.parse("*#4*1*15*0250##"))
        await gw._event_dispatcher.process_message(OWNEvent.parse("*#4*1*15*01*0250*3##"))
        await gw._event_dispatcher.process_message(OWNEvent.parse("*#4*105*0*0190##"))
        await gw._event_dispatcher.process_message(OWNEvent.parse("*#4*105*0*0195##"))
        await hass.async_block_till_done()

        sensor_entries = [
            e
            for e in er.async_entries_for_config_entry(ent_reg, entry.entry_id)
            if e.domain == "sensor"
        ]
        assert {e.unique_id for e in sensor_entries} == {
            f"{mac}-4-1-temperature",
            f"{mac}-4-105-temperature",
        }
        z1_temp_id = ent_reg.async_get_entity_id("sensor", DOMAIN, f"{mac}-4-1-temperature")
        assert z1_temp_id is not None
        assert hass.states.get(z1_temp_id).state == "21.0"

        assert await hass.config_entries.async_unload(entry.entry_id)


async def test_climate_live_dim60_before_restore_preserves_live_humidity(
    hass: HomeAssistant,
) -> None:
    """Live Dimension 60 arriving before async_restore_last_state is never overwritten by stale cache."""
    gw = _make_gateway()
    entry = _make_entry(hass, gw)
    ent_reg = er.async_get(hass)
    ent_reg.async_get_or_create("climate", DOMAIN, f"{gw.mac}-4-1", config_entry=entry)

    climates: list[MyHOMEClimate] = []
    sensors: list[Any] = []

    def _add_climates_deferred(entities: list[Any], update_before_add: bool = False) -> None:
        for ent in entities:
            ent.hass = hass
            ent.platform = MagicMock()
            ent.entity_id = "climate.zone_1"
            ent.async_schedule_update_ha_state = MagicMock()
            ent.async_write_ha_state = MagicMock()
            climates.append(ent)

    _, add_s = _make_entity_adders(hass, [], sensors)
    await sensor_setup_entry(hass, entry, add_s)
    await climate_setup_entry(hass, entry, _add_climates_deferred)
    assert len(climates) == 1
    zone1 = climates[0]

    # Live Dim 60 arrives via FrameRouter BEFORE async_added_to_hass / async_restore_last_state runs
    signal = f"myhome_message_{gw.mac}"
    live_hum = OWNEvent.parse("*#4*1*60*64##")
    assert live_hum is not None
    async_dispatcher_send(hass, signal, live_hum)
    await hass.async_block_till_done()
    assert zone1.current_humidity == 64.0
    assert zone1._humidity_reported is True

    # Now async_added_to_hass restores stale 41.0 % from pre-restart state: live 64.0 % MUST win!
    stale_state = State("climate.zone_1", "heat", {"current_humidity": 41.0})
    with patch.object(zone1, "async_get_last_state", AsyncMock(return_value=stale_state)):
        await zone1.async_added_to_hass()
    await hass.async_block_till_done()

    assert zone1.current_humidity == 64.0
    hums = [s for s in sensors if isinstance(s, MyHOMEClimateHumiditySensor)]
    assert len(hums) == 1
    assert hums[0].native_value == 64.0


async def test_climate_before_sensor_startup_seeding_and_unload_cleanup(
    hass: HomeAssistant,
) -> None:
    """CLIMATE-before-SENSOR startup seeds live °C and 0..100% humidity from runtime.entities['climate']."""
    gw = _make_gateway()
    entry = _make_entry(hass, gw)
    ent_reg = er.async_get(hass)
    ent_reg.async_get_or_create("climate", DOMAIN, f"{gw.mac}-4-1", config_entry=entry)
    ent_reg.async_get_or_create("climate", DOMAIN, f"{gw.mac}-4-2#4#01", config_entry=entry)
    ent_reg.async_get_or_create("climate", DOMAIN, f"{gw.mac}-4-3", config_entry=entry)

    climates: list[MyHOMEClimate] = []
    sensors: list[Any] = []
    add_c, add_s = _make_entity_adders(hass, climates, sensors)

    # 1. CLIMATE sets up BEFORE SENSOR (matching PLATFORMS order)
    await climate_setup_entry(hass, entry, add_c)
    await hass.async_block_till_done()
    assert set(entry.runtime_data.entities["climate"]) == {"1", "2#4#01", "3"}

    # Zone 1 receives live bus readings; Zone 2#4#01 receives live temp; Zone 3 has restored-only temp
    by_key = entry.runtime_data.entities["climate"]
    by_key["1"].handle_event(OWNEvent.parse("*#4*1*0*0224##"))
    by_key["1"].handle_event(OWNEvent.parse("*#4*1*60*58##"))
    by_key["2#4#01"].handle_event(OWNEvent.parse("*#4*2#4#01*0*0192##"))
    by_key["3"]._attr_native_current_temperature = 17.5
    by_key["3"]._current_temperature_restored = True

    # 2. SENSOR sets up later and seeds directly from live runtime.entities["climate"]
    await sensor_setup_entry(hass, entry, add_s)
    await hass.async_block_till_done()

    by_uid = {s.unique_id: s for s in sensors}
    assert by_uid[f"{gw.mac}-4-1-temperature"].native_value == 22.4
    assert by_uid[f"{gw.mac}-4-1-humidity"].native_value == 58.0
    assert by_uid[f"{gw.mac}-4-2#4#01-temperature"].native_value == 19.2
    # Restored-only climate temperature is NOT treated as a live bus seed
    assert by_uid[f"{gw.mac}-4-3-temperature"].native_value is None

    # Removing one climate entity cleans up its key; unloading climate pops the platform bucket
    for cb in list(getattr(by_key["3"], "_on_remove", ())):
        cb()
    assert "3" not in entry.runtime_data.entities["climate"]

    assert await climate_unload_entry(hass, entry)
    assert "climate" not in entry.runtime_data.entities
    assert await sensor_unload_entry(hass, entry)


async def test_ownd_event_parse_wire_edge_cases_on_climate_and_companions(
    hass: HomeAssistant,
) -> None:
    """OWNEvent.parse() wire edge cases: probes, #0#1, #1, Z#N, #0/#60 writes, >100% hum, and +0.0 °C."""
    # Direct unit coverage of thermo_zone_sensor_target across all grammar branches
    assert thermo_zone_sensor_target(OWNEvent.parse("*#4*1*0*0215##")) == ("1", None)
    assert thermo_zone_sensor_target(OWNEvent.parse("*#4*1#4#01*0*0215##")) == ("1", "01")
    assert thermo_zone_sensor_target(OWNEvent.parse("*#4*1*#0*0215##")) is None
    assert thermo_zone_sensor_target(OWNEvent.parse("*#4*1*#60*55##")) is None
    assert thermo_zone_sensor_target(OWNEvent.parse("*#4*#0*0*0215##")) is None
    assert thermo_zone_sensor_target(OWNEvent.parse("*#4*#0#1*0*0215##")) is None
    assert thermo_zone_sensor_target(OWNEvent.parse("*#4*#1*0*0215##")) is None
    assert thermo_zone_sensor_target(OWNEvent.parse("*#4*101*60*55##")) is None
    assert thermo_zone_sensor_target(OWNEvent.parse("*#4*0#1*0*0215##")) is None
    assert thermo_zone_sensor_target(OWNEvent.parse("*#4*1#1*0*0215##")) is None
    assert thermo_zone_sensor_target(OWNEvent.parse("*#4*1#1#4#01*0*0215##")) is None
    assert thermo_zone_sensor_target(OWNEvent.parse("*#4*3#1#1*0*0215##")) is None
    # 3-digit 001..099 ("all probes in zone", WHO 4 p. 6) is NOT a zone master probe (1..99)
    assert thermo_zone_sensor_target(OWNEvent.parse("*#4*001*0*0215##")) is None
    assert thermo_zone_sensor_target(OWNEvent.parse("*#4*099*60*50##")) is None
    assert thermo_zone_sensor_target(OWNEvent.parse("*#4*001#4#01*0*0215##")) is None
    # Malformed #4# interface mock where where_param == ["4", "01"] yet interface is missing
    assert (
        thermo_zone_sensor_target(
            SimpleNamespace(
                where="1",
                zone=1,
                where_param=["4", "01"],
                interface=None,
                is_dimension_writing=False,
            )
        )
        is None
    )

    gw = _make_gateway()
    entry = _make_entry(hass, gw)
    ent_reg = er.async_get(hass)
    ent_reg.async_get_or_create("climate", DOMAIN, f"{gw.mac}-4-1", config_entry=entry)
    ent_reg.async_get_or_create("climate", DOMAIN, f"{gw.mac}-4-1#4#01", config_entry=entry)

    climates: list[MyHOMEClimate] = []
    sensors: list[Any] = []
    add_c, add_s = _make_entity_adders(hass, climates, sensors)
    await sensor_setup_entry(hass, entry, add_s)
    await climate_setup_entry(hass, entry, add_c)
    await hass.async_block_till_done()

    z1_cli = next(c for c in climates if c._where == "1" and c._interface is None)
    z1_temp = next(s for s in sensors if s.unique_id == f"{gw.mac}-4-1-temperature")
    z1_if_temp = next(s for s in sensors if s.unique_id == f"{gw.mac}-4-1#4#01-temperature")

    signal = f"myhome_message_{gw.mac}"
    # Seed baseline 20.5 °C on local Zone 1 and 19.0 °C on routed 1#4#01
    for seed_raw in ("*#4*1*0*0205##", "*#4*1#4#01*0*0190##"):
        evt = OWNEvent.parse(seed_raw)
        assert evt is not None
        async_dispatcher_send(hass, signal, evt)
    await hass.async_block_till_done()
    assert z1_cli.current_temperature == 20.5
    assert z1_temp.native_value == 20.5
    assert z1_if_temp.native_value == 19.0

    # All non-zone / write / out-of-range frames parsed via OWNEvent.parse() MUST be ignored!
    rejected_wire_frames = (
        "*#4*101*60*55##",
        "*#4*#0#1*0*0300##",
        "*#4*#0#1*60*65##",
        "*#4*#1*0*0310##",
        "*#4*#1*60*66##",
        "*#4*1#1*0*0320##",
        "*#4*1#1*60*67##",
        "*#4*1#1#4#01*0*0330##",
        "*#4*1*#0*0340##",
        "*#4*1*#60*68##",
        "*#4*1*60*150##",
        "*#4*001*0*0299##",
        "*#4*001*60*77##",
    )
    for raw in rejected_wire_frames:
        evt = OWNEvent.parse(raw)
        assert evt is not None
        async_dispatcher_send(hass, signal, evt)
        # Also call handle_event directly to verify entity-level self-defense bypassing FrameRouter
        z1_cli.handle_event(evt)
        z1_temp.handle_event(evt)
        z1_if_temp.handle_event(evt)
    await hass.async_block_till_done()

    assert z1_cli.current_temperature == 20.5
    assert z1_cli._humidity is False
    assert z1_cli.current_humidity is None
    assert z1_temp.native_value == 20.5
    assert z1_if_temp.native_value == 19.0
    assert not any(isinstance(s, MyHOMEClimateHumiditySensor) for s in sensors)

    # Exact zero ("1000" and "0000") produces +0.0 °C (never -0.0 °C)
    for zero_raw in ("*#4*1*0*1000##", "*#4*1*0*0000##"):
        evt = OWNEvent.parse(zero_raw)
        assert evt is not None
        async_dispatcher_send(hass, signal, evt)
        await hass.async_block_till_done()
        assert z1_cli.current_temperature == 0.0
        assert math.copysign(1.0, float(z1_cli.current_temperature)) == 1.0
        assert z1_temp.native_value == 0.0
        assert math.copysign(1.0, float(z1_temp.native_value)) == 1.0
        assert str(z1_temp.native_value) == "0.0"


@pytest.mark.parametrize(("refused", "expected_polls"), [(True, []), (False, ["*#4*1#4#01*0##"])])
async def test_temperature_sensor_f422_bus_routing_passivity_guard(
    hass: HomeAssistant,
    refused: bool,
    expected_polls: list[str],
) -> None:
    """MyHOMETemperatureSensor suppresses _poll_on_add and async_update on refused #4#II gateways."""
    from tests.conftest import bind_entity

    gw = _make_gateway()
    gw.interface_zone_request_refused = refused

    sensor = MyHOMETemperatureSensor(
        hass=hass,
        name="Routed Temp",
        device_id="4-1#4#01",
        who="4",
        where="1#4#01",
        device_class=SensorDeviceClass.TEMPERATURE,
        manufacturer="BTicino",
        model="L4692",
        gateway=gw,
    )
    sensor.entity_id = "sensor.routed_temp"
    bind_entity(hass, sensor, gw.mac, gw)
    await sensor.async_added_to_hass()
    assert sensor._poll_on_add is (not refused)
    assert [str(c.args[0]) for c in gw.send_status_request.await_args_list] == expected_polls

    gw.send_status_request.reset_mock()
    await sensor.async_update()
    assert [str(c.args[0]) for c in gw.send_status_request.await_args_list] == expected_polls

    # Delegated / failover _who4_target() decides whether #4#II is refused (not local gateway alone)
    serving_gw = _make_gateway(mac="00:03:50:00:09:98")
    serving_gw.interface_zone_request_refused = not refused
    gw._who4_target = lambda: serving_gw
    gw.send_status_request.reset_mock()
    await sensor.async_update()
    delegated_expected = ["*#4*1#4#01*0##"] if refused else []
    assert [str(c.args[0]) for c in gw.send_status_request.await_args_list] == delegated_expected


async def test_four_zone_central_4695_coexists_with_bus_and_restored_zone_companions(
    hass: HomeAssistant,
) -> None:
    """4-zone central unit (4695, central: true, zone: '1' -> #0#1) never suppresses or prunes real Zone 1."""
    from custom_components.myhome.legacy_yaml import _apply_yaml_platforms
    from custom_components.myhome.validate import climate_schema

    gw = _make_gateway()
    validated = climate_schema({"central_1": {"zone": "1", "central": True, "name": "Central 4695"}})
    configured: dict[str, dict[str, Any]] = {"climate": {}}
    _apply_yaml_platforms({"climate": validated}, configured)

    entry = _make_entry(hass, gw, platforms={"climate": configured["climate"]})
    climates: list[MyHOMEClimate] = []
    sensors: list[Any] = []
    add_c, add_s = _make_entity_adders(hass, climates, sensors)

    await climate_setup_entry(hass, entry, add_c)
    await sensor_setup_entry(hass, entry, add_s)
    await hass.async_block_till_done()

    # Only #0#1 central unit exists initially, with zero companion sensors
    assert [c._where for c in climates] == ["#0#1"]
    assert sensors == []

    # Bus frames confirm real Thermostat Zone 1 alongside #0#1 and report Dim 60 humidity
    signal = f"myhome_message_{gw.mac}"
    for raw in ("*#4*1*0*0216##", "*4*1*1##", "*#4*1*60*47##"):
        evt = OWNEvent.parse(raw)
        assert evt is not None
        async_dispatcher_send(hass, signal, evt)
    await hass.async_block_till_done()

    by_where = {c._where: c for c in climates}
    assert set(by_where) == {"#0#1", "1"}
    assert by_where["#0#1"]._central is True
    assert by_where["1"]._central is False
    assert by_where["1"]._device_name == "Climate Zone 1"

    by_uid = {s.unique_id: s for s in sensors}
    assert set(by_uid) == {f"{gw.mac}-4-1-temperature", f"{gw.mac}-4-1-humidity"}
    assert by_uid[f"{gw.mac}-4-1-temperature"].native_value == 21.6
    assert by_uid[f"{gw.mac}-4-1-humidity"].native_value == 47.0

    # Persist both climates and both companions in entity_registry and simulate restart
    ent_reg = er.async_get(hass)
    ent_reg.async_get_or_create("climate", DOMAIN, f"{gw.mac}-4-#0#1", config_entry=entry)
    ent_reg.async_get_or_create("climate", DOMAIN, f"{gw.mac}-4-1", config_entry=entry)
    ent_reg.async_get_or_create(
        "sensor",
        DOMAIN,
        f"{gw.mac}-4-1-temperature",
        config_entry=entry,
        original_device_class=SensorDeviceClass.TEMPERATURE,
    )
    ent_reg.async_get_or_create(
        "sensor",
        DOMAIN,
        f"{gw.mac}-4-1-humidity",
        config_entry=entry,
        original_device_class=SensorDeviceClass.HUMIDITY,
    )
    while entry._on_unload:
        entry._on_unload.pop()()
    entry.runtime_data.router = FrameRouter()
    entry.runtime_data.entities.clear()
    re_configured: dict[str, dict[str, Any]] = {"climate": {}}
    _apply_yaml_platforms({"climate": validated}, re_configured)
    entry.runtime_data.platforms["climate"] = re_configured["climate"]
    entry.runtime_data.platforms["sensor"] = {}

    climates_r: list[MyHOMEClimate] = []
    sensors_r: list[Any] = []
    add_cr, add_sr = _make_entity_adders(hass, climates_r, sensors_r)
    await climate_setup_entry(hass, entry, add_cr)
    await sensor_setup_entry(hass, entry, add_sr)
    await hass.async_block_till_done()

    restored_by_where = {c._where: c for c in climates_r}
    assert set(restored_by_where) == {"#0#1", "1"}
    assert restored_by_where["#0#1"]._central is True
    assert restored_by_where["1"]._central is False
    assert restored_by_where["1"]._device_name == "Climate Zone 1"
    assert {s.unique_id for s in sensors_r} == {
        f"{gw.mac}-4-1-temperature",
        f"{gw.mac}-4-1-humidity",
    }


async def test_unrelated_who18_and_who1_yaml_sensors_do_not_block_pruning_or_probe_gating(
    hass: HomeAssistant,
) -> None:
    """WHO=18 / WHO=1 YAML sensors indexed under bare WHERE neither block companion pruning nor bypass #967."""
    from custom_components.myhome.legacy_yaml import _apply_yaml_platforms
    from custom_components.myhome.validate import sensor_schema

    pri_mac = "00:03:50:00:09:70"
    fol_mac = "00:03:50:00:09:71"
    validated_sensors = sensor_schema(
        {
            "f520_51": {"who": 18, "where": "51", "name": "Meter 51", "class": "power"},
            "lux_12": {"who": 1, "where": "12", "name": "Lux 12", "class": "illuminance"},
            "f520_105": {"who": 18, "where": "105", "name": "Meter 105", "class": "power"},
            "zone_02": {"who": 4, "where": "02", "name": "Zone 2 YAML", "class": "temperature"},
        }
    )
    configured_fol: dict[str, dict[str, Any]] = {"sensor": {}}
    _apply_yaml_platforms({"sensor": validated_sensors}, configured_fol)
    # Also include a legacy #3 WHO=4 temperature snapshot entry
    configured_fol["sensor"]["4-#3"] = {
        CONF_WHO: "4",
        CONF_WHERE: "#3",
        CONF_NAME: "Zone 3 Hash",
        CONF_DEVICE_CLASS: SensorDeviceClass.TEMPERATURE,
        CONF_MANUFACTURER: "BTicino",
        CONF_DEVICE_MODEL: "L4692",
    }
    # Bare keys "51", "12", "105" exist in runtime.platforms["sensor"] pointing to WHO=18/WHO=1 dicts
    assert configured_fol["sensor"]["51"][CONF_WHO] == "18"
    assert configured_fol["sensor"]["12"][CONF_WHO] == "1"
    assert configured_fol["sensor"]["105"][CONF_WHO] == "18"

    pri_gw = _make_gateway(mac=pri_mac)
    pri_entry = _make_entry(hass, pri_gw)
    fol_gw = _make_gateway(mac=fol_mac, is_follower=True, primary_mac=pri_mac)
    fol_entry = _make_entry(
        hass,
        fol_gw,
        platforms={
            "sensor": configured_fol["sensor"],
            "switch": {"sw_51": {CONF_WHO: "4", CONF_WHERE: "51"}},
        },
    )

    ent_reg = er.async_get(hass)
    ent_reg.async_get_or_create("climate", DOMAIN, f"{pri_mac}-4-12", config_entry=pri_entry)
    stale_51 = ent_reg.async_get_or_create(
        "sensor",
        DOMAIN,
        f"{fol_mac}-4-51-temperature",
        config_entry=fol_entry,
        original_device_class=SensorDeviceClass.TEMPERATURE,
    )
    stale_12 = ent_reg.async_get_or_create(
        "sensor",
        DOMAIN,
        f"{fol_mac}-4-12-temperature",
        config_entry=fol_entry,
        original_device_class=SensorDeviceClass.TEMPERATURE,
    )
    kept_z2 = ent_reg.async_get_or_create(
        "sensor",
        DOMAIN,
        f"{fol_mac}-4-2-temperature",
        config_entry=fol_entry,
        original_device_class=SensorDeviceClass.TEMPERATURE,
    )
    kept_z3 = ent_reg.async_get_or_create(
        "sensor",
        DOMAIN,
        f"{fol_mac}-4-3-temperature",
        config_entry=fol_entry,
        original_device_class=SensorDeviceClass.TEMPERATURE,
    )

    fol_sensors: list[Any] = []
    _, add_fol_s = _make_entity_adders(hass, [], fol_sensors)
    await sensor_setup_entry(hass, fol_entry, add_fol_s)
    await hass.async_block_till_done()

    assert ent_reg.async_get(stale_51.entity_id) is None
    assert ent_reg.async_get(stale_12.entity_id) is None
    assert ent_reg.async_get(kept_z2.entity_id) is not None
    assert ent_reg.async_get(kept_z3.entity_id) is not None

    # Single bit-flipped WHO=4 probe frame on WHERE=105 still waits for 2nd reading (#967)
    signal = f"myhome_message_{fol_mac}"
    fol_gw.delegated_whos = {4}
    async_dispatcher_send(hass, signal, OWNEvent.parse("*#4*105*0*0215##"))
    await hass.async_block_till_done()
    assert not any(s.unique_id == f"{fol_mac}-4-105-temperature" for s in fol_sensors)


async def test_follower_discarded_and_actuator_climates_not_tracked_in_runtime_entities(
    hass: HomeAssistant,
) -> None:
    """Follower-discarded climate duplicates and legacy Z#N actuator entries never leak into runtime.entities."""
    pri_mac = "00:03:50:00:09:72"
    fol_mac = "00:03:50:00:09:73"
    pri_gw = _make_gateway(mac=pri_mac)
    pri_entry = _make_entry(hass, pri_gw)
    fol_gw = _make_gateway(mac=fol_mac, is_follower=True, primary_mac=pri_mac)
    fol_entry = _make_entry(
        hass,
        fol_gw,
        platforms={"climate": {"z1": {CONF_WHO: "4", CONF_WHERE: "1", CONF_NAME: "Dup Zone 1"}}},
    )
    fol_gw.delegated_whos = {4}

    ent_reg = er.async_get(hass)
    # Primary already owns Zone 1 -> follower's YAML Zone 1 will be discarded in PlatformDiscovery._create()
    ent_reg.async_get_or_create("climate", DOMAIN, f"{pri_mac}-4-1", config_entry=pri_entry)
    # Legacy actuator-shaped climate registry entry 2#1 on primary
    ent_reg.async_get_or_create("climate", DOMAIN, f"{pri_mac}-4-2#1", config_entry=pri_entry)

    fol_climates: list[MyHOMEClimate] = []
    fol_sensors: list[Any] = []
    add_fc, add_fs = _make_entity_adders(hass, fol_climates, fol_sensors)
    await climate_setup_entry(hass, fol_entry, add_fc)
    assert fol_climates == []
    assert fol_entry.runtime_data.entities.get("climate", {}) == {}
    await sensor_setup_entry(hass, fol_entry, add_fs)
    assert fol_sensors == []

    pri_climates: list[MyHOMEClimate] = []
    add_pc, _ = _make_entity_adders(hass, pri_climates, [])
    await climate_setup_entry(hass, pri_entry, add_pc)
    assert set(pri_entry.runtime_data.entities.get("climate", {})) == {"1"}


async def test_legacy_yaml_zone_temperature_sensor_preserves_compat_name(
    hass: HomeAssistant,
) -> None:
    """Legacy non-has_entity_name YAML zone temperature sensors preserve _legacy_compat_name before & after YAML removal."""
    gw = _make_gateway(mac="00:03:50:00:09:74")
    entry = _make_entry(
        hass,
        gw,
        platforms={
            "sensor": {
                "4-01": {
                    CONF_WHO: "4",
                    CONF_WHERE: "01",
                    CONF_NAME: "YAML Salotto",
                    CONF_DEVICE_CLASS: SensorDeviceClass.TEMPERATURE,
                    CONF_MANUFACTURER: "BTicino",
                    CONF_DEVICE_MODEL: "L4692",
                }
            }
        },
    )
    ent_reg = er.async_get(hass)
    legacy_reg = ent_reg.async_get_or_create(
        "sensor",
        DOMAIN,
        f"{gw.mac}-4-01-temperature",
        suggested_object_id="salotto_storico",
        config_entry=entry,
        has_entity_name=False,
        original_device_class=SensorDeviceClass.TEMPERATURE,
    )
    ent_reg.async_update_entity(legacy_reg.entity_id, name="Salotto Storico")

    sensors_yaml: list[Any] = []
    _, add_sy = _make_entity_adders(hass, [], sensors_yaml)
    await sensor_setup_entry(hass, entry, add_sy)
    await hass.async_block_till_done()
    assert len(sensors_yaml) == 1
    assert isinstance(sensors_yaml[0], MyHOMETemperatureSensor)
    assert sensors_yaml[0]._legacy_compat_name == "Salotto Storico"

    # Simulate direct upgrade where YAML entry is removed while registry still has has_entity_name=False
    gw2 = _make_gateway(mac="00:03:50:00:09:75")
    entry2 = _make_entry(hass, gw2)
    ent_reg.async_get_or_create("climate", DOMAIN, f"{gw2.mac}-4-1", config_entry=entry2)
    legacy_reg2 = ent_reg.async_get_or_create(
        "sensor",
        DOMAIN,
        f"{gw2.mac}-4-01-temperature",
        suggested_object_id="camera_storica",
        config_entry=entry2,
        has_entity_name=False,
        original_device_class=SensorDeviceClass.TEMPERATURE,
    )
    ent_reg.async_update_entity(legacy_reg2.entity_id, name="Camera Storica")

    sensors_comp: list[Any] = []
    _, add_sc = _make_entity_adders(hass, [], sensors_comp)
    await sensor_setup_entry(hass, entry2, add_sc)
    await hass.async_block_till_done()
    assert len(sensors_comp) == 1
    assert isinstance(sensors_comp[0], MyHOMEClimateTemperatureSensor)
    assert sensors_comp[0]._legacy_compat_name == "Camera Storica"
