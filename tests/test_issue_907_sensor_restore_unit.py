"""Regression and gateway trace tests for issue #907 (sensor native-unit state restoration).

Verifies that MyHOME sensors (`MyHOMEEnergySensor`, `MyHOMEPowerSensor`,
`MyHOMETemperatureSensor`, `MyHOMEIlluminanceSensor`) inherit `RestoreSensor`
and restore `native_value` in their native unit (`Wh`, `W`, `°C`, `lx`) rather
than writing the display-unit `last_state.state` (`kWh`, `MWh`, `kW`, `°F`)
into `_attr_native_value`, preventing 1000x dips and phantom `total_increasing`
meter resets in Home Assistant long-term statistics.
"""

from __future__ import annotations

import json
from decimal import Decimal
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from homeassistant.components.sensor import DOMAIN as SENSOR_DOMAIN
from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorExtraStoredData,
)
from homeassistant.const import (
    ATTR_UNIT_OF_MEASUREMENT,
    CONF_MAC,
    EVENT_STATE_CHANGED,
    STATE_UNAVAILABLE,
    STATE_UNKNOWN,
    UnitOfEnergy,
    UnitOfPower,
    UnitOfTemperature,
)
from homeassistant.core import Event, HomeAssistant, State, callback
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.dispatcher import async_dispatcher_send
from OWNd.message import OWNEnergyEvent, OWNMessage
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    mock_restore_cache_with_extra_data,
)

from custom_components.myhome.const import CONF_ENTITY, DOMAIN
from custom_components.myhome.myhome_device import MyHOMEEntity
from custom_components.myhome.sensor import (
    MyHOMEEnergySensor,
    MyHOMEIlluminanceSensor,
    MyHOMEPowerSensor,
    MyHOMESensorEntity,
    MyHOMETemperatureSensor,
)

TRACES_DIR = Path(__file__).resolve().parent / "fixtures" / "traces" / "issue_907"
F454_TRACE_PATH = TRACES_DIR / "myhome_trace_oracle_f454_who18_totalizer_delayed_poll.json"
H4684_TRACE_PATH = (
    TRACES_DIR / "myhome_trace_oracle_h4684_who18_poll_refused_unsolicited_only.json"
)
ORACLE_JSON_PATH = Path(__file__).resolve().parent / "golden" / "firmware_oracle.json"
MAC = "00:03:50:90:70:01"


def _make_gateway(model: str = "F459", firmware: str = "2.1.5") -> MagicMock:
    gw = MagicMock()
    gw.name = model
    gw.mac = MAC
    gw.unique_id = MAC
    gw.log_id = f"[{model} - {MAC}]"
    gw.model = model
    gw.manufacturer = "BTicino S.p.A."
    gw.firmware = firmware
    gw.device_registry_id = None
    gw.is_connected = True
    gw.is_follower = False
    gw.primary_gateway_mac = None
    gw.delegated_whos = set()
    gw.delegated_away_whos = set()
    gw.availability_signal = f"myhome_gw_available_{MAC}"
    gw.is_who_available = MagicMock(return_value=True)
    gw.initial_discovery_pending = False
    gw.sweep_active = False
    gw.send = AsyncMock(return_value=True)
    gw.send_status_request = AsyncMock(return_value=True)
    return gw


def test_issue_907_oracle_gateway_divergence_on_who18_dim51() -> None:
    """Verify the firmware oracle contract backing the two Issue #907 gateway traces."""
    assert F454_TRACE_PATH.is_file()
    assert H4684_TRACE_PATH.is_file()
    oracle = json.loads(ORACLE_JSON_PATH.read_text(encoding="utf-8"))["verdicts"]
    by_product = {row["product"]: row for row in oracle["*#18*51*51##"]}

    # Forwarding gateways ACK and transmit SCS $06D1A1023350150000\r
    for model in ("F459", "F454", "F461", "MyHomeServer1", "MH200N", "MH202"):
        assert by_product[model]["reply"] == "ack"
        assert by_product[model]["verdict"] == "out"

    # Poll-dropping gateways NACK and emit zero SCS bus bytes
    for model in ("H4684", "MH200", "F450"):
        assert by_product[model]["reply"] == "nack"
        assert by_product[model]["verdict"] == "silent"


@pytest.mark.asyncio
async def test_forwarding_gateway_trace_replay_kwh_display_unit_no_phantom_meter_reset(
    hass: HomeAssistant,
) -> None:
    """Replay the delayed-poll forwarding trace with kWh display unit: zero 1000x dip or reset."""
    trace = json.loads(F454_TRACE_PATH.read_text(encoding="utf-8"))
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={
            CONF_MAC: MAC,
            "host": "192.0.2.90",
            "port": 20000,
            "name": trace["gateway"]["model"],
        },
        unique_id=MAC,
    )
    entry.add_to_hass(hass)

    ent_reg = er.async_get(hass)
    reg_entry = ent_reg.async_get_or_create(
        SENSOR_DOMAIN,
        DOMAIN,
        f"{MAC}-18-51-total-energy",
        suggested_object_id="total_power_energy",
        config_entry=entry,
    )
    entity_id = reg_entry.entity_id
    ent_reg.async_update_entity_options(
        entity_id, SENSOR_DOMAIN, {ATTR_UNIT_OF_MEASUREMENT: UnitOfEnergy.KILO_WATT_HOUR}
    )

    # Restore cache holds display state "8133.608" (kWh) + RestoreSensor native 8133608 (Wh)
    mock_restore_cache_with_extra_data(
        hass,
        [
            (
                State(
                    entity_id,
                    "8133.608",
                    {ATTR_UNIT_OF_MEASUREMENT: UnitOfEnergy.KILO_WATT_HOUR},
                ),
                SensorExtraStoredData(
                    native_value=8133608,
                    native_unit_of_measurement=UnitOfEnergy.WATT_HOUR,
                ),
            )
        ],
    )

    observed_states: list[float] = []

    @callback
    def _record_state_change(event: Event[Any]) -> None:
        if event.data.get("entity_id") != entity_id:
            return
        new_state: State | None = event.data.get("new_state")
        if new_state is not None and new_state.state not in (
            STATE_UNKNOWN,
            STATE_UNAVAILABLE,
        ):
            observed_states.append(float(new_state.state))

    unsub = hass.bus.async_listen(EVENT_STATE_CHANGED, _record_state_change)
    with (
        patch(
            "custom_components.myhome.gateway.OWNSession.test_connection",
            return_value={"Success": True},
        ),
        patch("custom_components.myhome.gateway.MyHOMEGatewayHandler.listening_loop"),
        patch("custom_components.myhome.gateway.MyHOMEGatewayHandler.sending_loop"),
    ):
        try:
            assert await hass.config_entries.async_setup(entry.entry_id)
            await hass.async_block_till_done()
            hass.data[DOMAIN][MAC][CONF_ENTITY]._on_event_connection_state_change(True)
            await hass.async_block_till_done()

            # T+0s: Restored state in HA state machine is already 8133.608 kWh (NEVER 8.133608!)
            state_t0 = hass.states.get(entity_id)
            assert state_t0 is not None
            assert (
                state_t0.attributes[ATTR_UNIT_OF_MEASUREMENT]
                == UnitOfEnergy.KILO_WATT_HOUR
            )
            assert float(state_t0.state) == pytest.approx(8133.608)

            # Replay all rx frames from the forwarding gateway trace (WHO 13 + WHO 18 dim 51 & 113)
            for frame in trace["frames"]:
                if frame["direction"] != "rx":
                    continue
                parsed = OWNMessage.parse(frame["raw"])
                assert parsed is not None
                async_dispatcher_send(hass, f"myhome_message_{MAC}", parsed)
            await hass.async_block_till_done()

            state_end = hass.states.get(entity_id)
            assert state_end is not None
            assert float(state_end.state) == pytest.approx(8133.615)

            # Strictly non-decreasing across the entire boot + poll + streaming window:
            # zero drops >10% (which would trigger a total_increasing meter reset in recorder).
            assert observed_states
            assert min(observed_states) >= 8133.608
            for prev, curr in zip(observed_states, observed_states[1:], strict=False):
                assert curr >= prev
        finally:
            unsub()
            await hass.config_entries.async_unload(entry.entry_id)
            await hass.async_block_till_done()


@pytest.mark.asyncio
async def test_h4684_poll_refused_trace_no_compounding_across_restarts(
    hass: HomeAssistant,
) -> None:
    """On H4684/MH200/F450 (*#18*51*51## NACKed/SILENT), consecutive restarts never divide by 1000."""
    trace = json.loads(H4684_TRACE_PATH.read_text(encoding="utf-8"))
    gw = _make_gateway(model=trace["gateway"]["model"], firmware=trace["gateway"]["firmware"])

    # Restart 1: Upgrading from 2.0.0b18 (RestoreEntity only -> extra_data is None!)
    energy_r1 = MyHOMEEnergySensor(
        hass=hass,
        name="Total Power",
        device_id="18-51",
        who="18",
        where="51",
        entity_specific_id="total-energy",
        device_class=SensorDeviceClass.ENERGY,
        manufacturer="BTicino",
        model="F520",
        gateway=gw,
    )
    energy_r1.async_get_last_state = AsyncMock(
        return_value=State(
            "sensor.total_power_energy",
            "8133.608",
            {ATTR_UNIT_OF_MEASUREMENT: UnitOfEnergy.KILO_WATT_HOUR},
        )
    )
    energy_r1.async_get_last_sensor_data = AsyncMock(return_value=None)
    await energy_r1.async_added_to_hass()

    # Migration fallback converted 8133.608 kWh -> 8133608.0 Wh despite NACKed startup poll!
    assert energy_r1.native_value == pytest.approx(8133608.0)
    dumped_extra = energy_r1.extra_restore_state_data
    assert isinstance(dumped_extra, SensorExtraStoredData)
    assert dumped_extra.native_value == pytest.approx(8133608.0)
    assert dumped_extra.native_unit_of_measurement == UnitOfEnergy.WATT_HOUR

    # Restart 2 (still no bus response because H4684 drops *#18*51*51## silently):
    energy_r2 = MyHOMEEnergySensor(
        hass=hass,
        name="Total Power",
        device_id="18-51",
        who="18",
        where="51",
        entity_specific_id="total-energy",
        device_class=SensorDeviceClass.ENERGY,
        manufacturer="BTicino",
        model="F520",
        gateway=gw,
    )
    energy_r2.async_write_ha_state = MagicMock()
    energy_r2.async_get_last_state = AsyncMock(
        return_value=State(
            "sensor.total_power_energy",
            "8133.608",
            {ATTR_UNIT_OF_MEASUREMENT: UnitOfEnergy.KILO_WATT_HOUR},
        )
    )
    energy_r2.async_get_last_sensor_data = AsyncMock(return_value=dumped_extra)
    await energy_r2.async_added_to_hass()
    assert energy_r2.native_value == pytest.approx(8133608.0)

    # Unsolicited report arrives 2 minutes later from the trace
    for frame in trace["frames"]:
        if frame["direction"] == "rx" and frame["who"] == "18":
            parsed = OWNMessage.parse(frame["raw"])
            assert isinstance(parsed, OWNEnergyEvent)
            energy_r2.handle_event(parsed)

    assert energy_r2.native_value == 8133620


@pytest.mark.asyncio
async def test_all_sensor_classes_restore_with_converted_display_units(
    hass: HomeAssistant,
) -> None:
    """Power (W/kW), Energy (Wh/MWh), Temperature (°C/°F), and Illuminance (lx) restore native units."""
    gw = _make_gateway()

    # 1. Power sensor: native W, displayed in kW (both RestoreSensor and upgrade fallback)
    power = MyHOMEPowerSensor(
        hass=hass,
        name="Meter",
        device_id="18-51",
        who="18",
        where="51",
        device_class=SensorDeviceClass.POWER,
        manufacturer="BTicino",
        model="F520",
        gateway=gw,
    )
    assert isinstance(power, MyHOMESensorEntity)
    power.async_get_last_state = AsyncMock(
        return_value=State(
            "sensor.meter_power",
            "1.25",
            {ATTR_UNIT_OF_MEASUREMENT: UnitOfPower.KILO_WATT},
        )
    )
    power.async_get_last_sensor_data = AsyncMock(return_value=None)
    await power.async_added_to_hass()
    assert power.native_value == pytest.approx(1250.0)

    # 2. Energy sensor: native Wh, displayed in MWh via RestoreSensor extra_data
    energy_mwh = MyHOMEEnergySensor(
        hass=hass,
        name="Meter",
        device_id="18-51",
        who="18",
        where="51",
        entity_specific_id="total-energy",
        device_class=SensorDeviceClass.ENERGY,
        manufacturer="BTicino",
        model="F520",
        gateway=gw,
    )
    energy_mwh.async_get_last_state = AsyncMock(
        return_value=State(
            "sensor.meter_energy",
            "8.133608",
            {ATTR_UNIT_OF_MEASUREMENT: UnitOfEnergy.MEGA_WATT_HOUR},
        )
    )
    energy_mwh.async_get_last_sensor_data = AsyncMock(
        return_value=SensorExtraStoredData(
            native_value=8133608,
            native_unit_of_measurement=UnitOfEnergy.WATT_HOUR,
        )
    )
    await energy_mwh.async_added_to_hass()
    assert energy_mwh.native_value == 8133608

    # 3. Temperature sensor: native °C, displayed in °F on upgrade fallback
    temp = MyHOMETemperatureSensor(
        hass=hass,
        name="Zone 1",
        device_id="4-1",
        who="4",
        where="1",
        device_class=SensorDeviceClass.TEMPERATURE,
        manufacturer="BTicino",
        model="Probe",
        gateway=gw,
    )
    temp.async_get_last_state = AsyncMock(
        return_value=State(
            "sensor.zone_1_temperature",
            "68.0",
            {ATTR_UNIT_OF_MEASUREMENT: UnitOfTemperature.FAHRENHEIT},
        )
    )
    temp.async_get_last_sensor_data = AsyncMock(return_value=None)
    await temp.async_added_to_hass()
    assert temp.native_value == pytest.approx(20.0)

    # 4. Illuminance sensor: RestoreSensor extra_data with Decimal
    lux = MyHOMEIlluminanceSensor(
        hass=hass,
        name="Hallway PIR",
        device_id="1-15",
        who="1",
        where="15",
        device_class=SensorDeviceClass.ILLUMINANCE,
        manufacturer="BTicino",
        model="PIR",
        gateway=gw,
    )
    lux.async_get_last_state = AsyncMock(
        return_value=State("sensor.hallway_pir_illuminance", "425")
    )
    lux.async_get_last_sensor_data = AsyncMock(
        return_value=SensorExtraStoredData(
            native_value=Decimal("425.5"),
            native_unit_of_measurement="lx",
        )
    )
    await lux.async_added_to_hass()
    assert lux.native_value == pytest.approx(425.5)


@pytest.mark.asyncio
async def test_sensor_restore_defensive_edge_cases(hass: HomeAssistant) -> None:
    """Exercise defensive edge cases in MyHOMESensorEntity and MyHOMEEntity restoration."""
    gw = _make_gateway()

    def _new_energy() -> MyHOMEEnergySensor:
        return MyHOMEEnergySensor(
            hass=hass,
            name="Meter",
            device_id="18-51",
            who="18",
            where="51",
            entity_specific_id="total-energy",
            device_class=SensorDeviceClass.ENERGY,
            manufacturer="BTicino",
            model="F520",
            gateway=gw,
        )

    # 1. Pre-populated _attr_native_value (bus beat restore) is not overwritten
    s_live = _new_energy()
    s_live._attr_native_value = 999999
    await s_live.async_restore_last_state(State("sensor.e", "100"))
    assert s_live.native_value == 999999

    # 2. async_get_last_sensor_data raising exception falls back cleanly to migration path
    s_exc = _new_energy()
    s_exc.async_get_last_sensor_data = AsyncMock(side_effect=RuntimeError("corrupt storage"))
    await s_exc.async_restore_last_state(
        State("sensor.e", "500", {ATTR_UNIT_OF_MEASUREMENT: UnitOfEnergy.WATT_HOUR})
    )
    assert s_exc.native_value == pytest.approx(500.0)

    # 3. SensorExtraStoredData with native_value=None keeps None (does not fall through to state)
    s_none = _new_energy()
    s_none.async_get_last_sensor_data = AsyncMock(
        return_value=SensorExtraStoredData(native_value=None, native_unit_of_measurement="Wh")
    )
    await s_none.async_restore_last_state(State("sensor.e", "8133.608"))
    assert s_none.native_value is None

    # 4. SensorExtraStoredData with non-finite Decimal / float / bool / str
    for raw_val, expected in (
        (Decimal("NaN"), None),
        (float("inf"), None),
        (True, None),
        ("raw_text", "raw_text"),
    ):
        s_types = MyHOMEIlluminanceSensor(
            hass=hass,
            name="PIR",
            device_id="1-15",
            who="1",
            where="15",
            device_class=SensorDeviceClass.ILLUMINANCE,
            manufacturer="BTicino",
            model="PIR",
            gateway=gw,
        )
        s_types.async_get_last_sensor_data = AsyncMock(
            return_value=SensorExtraStoredData(
                native_value=raw_val,  # type: ignore[arg-type]
                native_unit_of_measurement="lx",
            )
        )
        await s_types.async_restore_last_state(State("sensor.pir", "0"))
        assert s_types.native_value == expected

    # 5. SensorExtraStoredData with stored unit != native unit (convertible vs incompatible)
    s_conv_extra = _new_energy()
    s_conv_extra.async_get_last_sensor_data = AsyncMock(
        return_value=SensorExtraStoredData(
            native_value=2.5,
            native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        )
    )
    await s_conv_extra.async_restore_last_state(State("sensor.e", "2.5"))
    assert s_conv_extra.native_value == pytest.approx(2500.0)

    s_bad_extra = _new_energy()
    s_bad_extra.async_get_last_sensor_data = AsyncMock(
        return_value=SensorExtraStoredData(
            native_value=2.5,
            native_unit_of_measurement="bananas",
        )
    )
    await s_bad_extra.async_restore_last_state(State("sensor.e", "2.5"))
    assert s_bad_extra.native_value is None

    # 6. _convert_to_native_unit edge inputs (bool, non-numeric, NaN, no converter)
    helper = _new_energy()
    assert helper._convert_to_native_unit(True, "kWh", "Wh") is None
    assert helper._convert_to_native_unit(["bad"], "kWh", "Wh") is None
    assert helper._convert_to_native_unit("not-a-number", "kWh", "Wh") is None
    assert helper._convert_to_native_unit("nan", "kWh", "Wh") is None
    helper._attr_device_class = None
    assert helper._convert_to_native_unit(1.0, "kWh", "Wh") is None

    # 7. Migration fallback: unknown/unavailable, non-finite float, non-numeric total_increasing,
    #    and incompatible display unit on last_state.attributes
    for state_obj in (
        State("sensor.e", STATE_UNKNOWN),
        State("sensor.e", STATE_UNAVAILABLE),
        State("sensor.e", "nan", {ATTR_UNIT_OF_MEASUREMENT: "Wh"}),
        State("sensor.e", "corrupt_text"),
        State("sensor.e", "8133.608", {ATTR_UNIT_OF_MEASUREMENT: "bananas"}),
    ):
        s_fb = _new_energy()
        s_fb.async_get_last_sensor_data = AsyncMock(return_value=None)
        await s_fb.async_restore_last_state(state_obj)
        assert s_fb.native_value is None

    # 8. Generic MyHOMEEntity no longer writes display-unit strings into _attr_native_value
    generic = MyHOMEEntity(
        hass=hass,
        name="Generic",
        platform="sensor",
        device_id="18-99",
        who="18",
        where="99",
        manufacturer="BTicino",
        model="Generic",
        gateway=gw,
    )
    generic.__dict__["_attr_native_value"] = None
    await generic.async_restore_last_state(
        State("sensor.generic", "8133.608", {ATTR_UNIT_OF_MEASUREMENT: "kWh"})
    )
    assert generic.__dict__["_attr_native_value"] is None
