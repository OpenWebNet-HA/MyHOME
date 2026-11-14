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
from datetime import UTC, date, datetime, time
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
from homeassistant.components.sensor.recorder import reset_detected
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
from OWNd.message import (
    OWNEnergyCommand,
    OWNEnergyEvent,
    OWNGatewayCommand,
    OWNGatewayDiagnosticCommand,
    OWNGatewayDiagnosticEvent,
    OWNGatewayEvent,
    OWNMessage,
)
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
MHS1_LIVE_TRACE_PATH = (
    TRACES_DIR / "myhome_trace_MyHomeServer1_who18_meter51_2026-11-14.json"
)
F454_TRACE_PATH = TRACES_DIR / "myhome_trace_oracle_f454_who18_totalizer_delayed_poll.json"
H4684_TRACE_PATH = (
    TRACES_DIR / "myhome_trace_oracle_h4684_who18_poll_refused_unsolicited_only.json"
)
ORACLE_JSON_PATH = Path(__file__).resolve().parent / "golden" / "firmware_oracle.json"
CORPUS_JSON_PATH = Path(__file__).resolve().parent / "golden" / "corpus.json"
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
    assert MHS1_LIVE_TRACE_PATH.is_file()
    assert json.loads(MHS1_LIVE_TRACE_PATH.read_text(encoding="utf-8"))["synthetic"] is False
    assert F454_TRACE_PATH.is_file()
    assert H4684_TRACE_PATH.is_file()
    oracle = json.loads(ORACLE_JSON_PATH.read_text(encoding="utf-8"))["verdicts"]
    by_product = {row["product"]: row for row in oracle["*#18*51*51##"]}

    # Forwarding gateways ACK and transmit SCS $06D1A1023350150000\r
    for model in (
        "F459",
        "F454",
        "F461",
        "MyHomeServer1",
        "MH200N",
        "MH202",
        "F453",
        "F453AV",
    ):
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
            # Home Assistant's own recorder reset_detected() sees zero total_increasing resets.
            assert observed_states
            assert min(observed_states) >= 8133.608
            prev_val = 8133.608
            for curr in observed_states:
                assert curr >= prev_val
                assert not reset_detected(
                    hass, entity_id, curr, prev_val, state_end
                )
                prev_val = curr
        finally:
            unsub()
            await hass.config_entries.async_unload(entry.entry_id)
            await hass.async_block_till_done()


@pytest.mark.asyncio
async def test_h4684_poll_refused_config_entry_reload_and_migration_no_meter_reset(
    hass: HomeAssistant,
) -> None:
    """Full config-entry setup + async_reload on H4684 preserves kWh across NACKed polls."""
    from pytest_homeassistant_custom_component.common import mock_restore_cache

    trace = json.loads(H4684_TRACE_PATH.read_text(encoding="utf-8"))
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={
            CONF_MAC: MAC,
            "host": "192.0.2.91",
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

    # Boot 1: Upgrade from <= 2.0.0b18 — RestoreEntity State only (no SensorExtraStoredData!)
    mock_restore_cache(
        hass,
        [
            State(
                entity_id,
                "8133.608",
                {ATTR_UNIT_OF_MEASUREMENT: UnitOfEnergy.KILO_WATT_HOUR},
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

            s1 = hass.states.get(entity_id)
            assert s1 is not None
            assert float(s1.state) == pytest.approx(8133.608)

            # Reload while H4684 still NACKs *#18*51*51## (no WHO=18 frame arrived yet):
            assert await hass.config_entries.async_reload(entry.entry_id)
            await hass.async_block_till_done()
            hass.data[DOMAIN][MAC][CONF_ENTITY]._on_event_connection_state_change(True)
            await hass.async_block_till_done()

            s2 = hass.states.get(entity_id)
            assert s2 is not None
            assert float(s2.state) == pytest.approx(8133.608)

            # Unsolicited cyclic meter push arrives later
            for frame in trace["frames"]:
                if frame["direction"] != "rx":
                    continue
                parsed = OWNMessage.parse(frame["raw"])
                assert parsed is not None
                async_dispatcher_send(hass, f"myhome_message_{MAC}", parsed)
            await hass.async_block_till_done()

            s3 = hass.states.get(entity_id)
            assert s3 is not None
            assert float(s3.state) == pytest.approx(8133.62)

            prev_val = 8133.608
            for curr in observed_states:
                assert not reset_detected(hass, entity_id, curr, prev_val, s3)
                prev_val = curr
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

    # Migration fallback converted 8133.608 kWh -> exact int 8133608 Wh despite NACKed startup poll!
    assert energy_r1.native_value == 8133608
    assert type(energy_r1.native_value) is int
    assert energy_r1.state == 8133608 and str(energy_r1.state) == "8133608"
    dumped_extra = energy_r1.extra_restore_state_data
    assert isinstance(dumped_extra, SensorExtraStoredData)
    assert dumped_extra.native_value == 8133608
    assert type(dumped_extra.native_value) is int
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
    assert energy_r2.native_value == 8133608
    assert type(energy_r2.native_value) is int

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
    assert power.native_value == 1250
    assert type(power.native_value) is int
    assert power.state == 1250 and str(power.state) == "1250"

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

    # 4. SensorExtraStoredData with non-finite Decimal / float / bool / str / list:
    #    Every restored numeric sensor MUST allow reading `.state` without raising ValueError!
    for raw_val, expected, expected_type in (
        (Decimal("NaN"), None, type(None)),
        (Decimal("1E+400"), None, type(None)),
        (Decimal("8133608"), 8133608, int),
        (Decimal("425.5"), 425.5, float),
        (float("inf"), None, type(None)),
        (425.5, 425.5, float),
        (8133608, 8133608, int),
        (10**400, None, type(None)),
        (True, None, type(None)),
        ("raw_text", None, type(None)),
        ("nan", None, type(None)),
        ("8133608", 8133608, int),
        ("9" * 400, None, type(None)),
        ("9" * 5000, None, type(None)),
        ("425.5", 425.5, float),
        (["bad_list"], None, type(None)),
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
        assert type(s_types.native_value) is expected_type
        # Accessing `.state` triggers SensorEntity's numeric value validation
        _ = s_types.state

    # 4b. Non-numeric unitless MyHOMESensorEntity subclass preserves string native_value
    #     and rejects mismatched unitful stored/fallback states.
    class _TextSensor(MyHOMESensorEntity):
        def __init__(self) -> None:
            super().__init__(
                hass=hass,
                name="Mode",
                platform="sensor",
                device_id="18-98",
                who="18",
                where="98",
                manufacturer="BTicino",
                model="Text",
                gateway=gw,
            )

    t_ok = _TextSensor()
    t_ok.async_get_last_sensor_data = AsyncMock(
        return_value=SensorExtraStoredData(
            native_value="tariff_f1",
            native_unit_of_measurement=None,
        )
    )
    await t_ok.async_restore_last_state(State("sensor.mode", "tariff_f1"))
    assert t_ok.native_value == "tariff_f1"
    assert t_ok.state == "tariff_f1"

    t_unit_mismatch = _TextSensor()
    t_unit_mismatch.async_get_last_sensor_data = AsyncMock(
        return_value=SensorExtraStoredData(
            native_value="tariff_f1",
            native_unit_of_measurement="Wh",
        )
    )
    await t_unit_mismatch.async_restore_last_state(State("sensor.mode", "tariff_f1"))
    assert t_unit_mismatch.native_value is None

    t_fb_unit_mismatch = _TextSensor()
    t_fb_unit_mismatch.async_get_last_sensor_data = AsyncMock(return_value=None)
    await t_fb_unit_mismatch.async_restore_last_state(
        State("sensor.mode", "tariff_f1", {ATTR_UNIT_OF_MEASUREMENT: "Wh"})
    )
    assert t_fb_unit_mismatch.native_value is None

    # 5. SensorExtraStoredData with stored unit != native unit (convertible, None, vs incompatible)
    s_unitless_extra = _new_energy()
    s_unitless_extra.async_get_last_sensor_data = AsyncMock(
        return_value=SensorExtraStoredData(
            native_value=8133.608,
            native_unit_of_measurement=None,
        )
    )
    await s_unitless_extra.async_restore_last_state(State("sensor.e", "8133.608"))
    assert s_unitless_extra.native_value is None

    s_conv_extra = _new_energy()
    s_conv_extra.async_get_last_sensor_data = AsyncMock(
        return_value=SensorExtraStoredData(
            native_value=2.5,
            native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        )
    )
    await s_conv_extra.async_restore_last_state(State("sensor.e", "2.5"))
    assert s_conv_extra.native_value == 2500
    assert type(s_conv_extra.native_value) is int
    assert s_conv_extra.state == 2500 and str(s_conv_extra.state) == "2500"

    s_conv_frac = _new_energy()
    s_conv_frac.async_get_last_sensor_data = AsyncMock(
        return_value=SensorExtraStoredData(
            native_value=2.5005,
            native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        )
    )
    await s_conv_frac.async_restore_last_state(State("sensor.e", "2.5005"))
    assert s_conv_frac.native_value == pytest.approx(2500.5)
    assert type(s_conv_frac.native_value) is float

    s_bad_extra = _new_energy()
    s_bad_extra.async_get_last_sensor_data = AsyncMock(
        return_value=SensorExtraStoredData(
            native_value=2.5,
            native_unit_of_measurement="bananas",
        )
    )
    await s_bad_extra.async_restore_last_state(State("sensor.e", "2.5"))
    assert s_bad_extra.native_value is None

    # 6. _convert_to_native_unit edge inputs (bool, non-numeric, OverflowError, NaN, scaled inf, no converter)
    helper = _new_energy()
    assert helper._convert_to_native_unit(True, "kWh", "Wh") is None
    assert helper._convert_to_native_unit(["bad"], "kWh", "Wh") is None
    assert helper._convert_to_native_unit("not-a-number", "kWh", "Wh") is None
    assert helper._convert_to_native_unit(10**400, "kWh", "Wh") is None
    assert helper._convert_to_native_unit("nan", "kWh", "Wh") is None
    assert helper._convert_to_native_unit(1e308, "MWh", "Wh") is None
    helper._attr_device_class = None
    assert helper._convert_to_native_unit(1.0, "kWh", "Wh") is None

    # 6b. Unitless numeric device_class (POWER_FACTOR) and non-numeric device classes (ENUM, DATE, TIMESTAMP)
    pf_sensor = _TextSensor()
    pf_sensor._attr_device_class = SensorDeviceClass.POWER_FACTOR
    assert pf_sensor._is_numeric_sensor() is True
    pf_sensor.async_get_last_sensor_data = AsyncMock(return_value=None)
    await pf_sensor.async_restore_last_state(State("sensor.pf", "corrupt_text"))
    assert pf_sensor.native_value is None
    assert pf_sensor.state is None
    await pf_sensor.async_restore_last_state(State("sensor.pf", "0.98"))
    assert pf_sensor.native_value == pytest.approx(0.98)
    assert pf_sensor.state == 0.98

    enum_sensor = _TextSensor()
    enum_sensor._attr_device_class = SensorDeviceClass.ENUM
    enum_sensor._attr_options = ["tariff_f1", "tariff_f2"]
    assert enum_sensor._is_numeric_sensor() is False
    enum_sensor.async_get_last_sensor_data = AsyncMock(return_value=None)
    await enum_sensor.async_restore_last_state(State("sensor.enum", "invalid_option"))
    assert enum_sensor.native_value is None
    assert enum_sensor.state is None
    enum_sensor.async_get_last_sensor_data = AsyncMock(
        return_value=SensorExtraStoredData(native_value=123, native_unit_of_measurement=None)
    )
    await enum_sensor.async_restore_last_state(State("sensor.enum", "tariff_f1"))
    assert enum_sensor.native_value is None
    enum_sensor.async_get_last_sensor_data = AsyncMock(return_value=None)
    await enum_sensor.async_restore_last_state(State("sensor.enum", "tariff_f1"))
    assert enum_sensor.native_value == "tariff_f1"
    assert enum_sensor.state == "tariff_f1"

    date_sensor = _TextSensor()
    date_sensor._attr_device_class = SensorDeviceClass.DATE
    date_sensor.async_get_last_sensor_data = AsyncMock(return_value=None)
    await date_sensor.async_restore_last_state(State("sensor.dt", "not-a-date"))
    assert date_sensor.native_value is None
    assert date_sensor.state is None
    date_sensor.async_get_last_sensor_data = AsyncMock(
        return_value=SensorExtraStoredData(
            native_value=datetime(2026, 11, 14, 12, 0, tzinfo=UTC),
            native_unit_of_measurement=None,
        )
    )
    await date_sensor.async_restore_last_state(State("sensor.dt", "2026-11-14"))
    assert date_sensor.native_value is None
    date_sensor.async_get_last_sensor_data = AsyncMock(return_value=None)
    await date_sensor.async_restore_last_state(State("sensor.dt", "2026-11-14"))
    assert date_sensor.native_value == date(2026, 11, 14)
    assert date_sensor.state == "2026-11-14"
    date_sensor._attr_native_value = None
    date_sensor.async_get_last_sensor_data = AsyncMock(
        return_value=SensorExtraStoredData(
            native_value=date(2026, 11, 14),
            native_unit_of_measurement=None,
        )
    )
    await date_sensor.async_restore_last_state(State("sensor.dt", "2026-11-14"))
    assert date_sensor.native_value == date(2026, 11, 14)

    ts_sensor = _TextSensor()
    ts_sensor._attr_device_class = SensorDeviceClass.TIMESTAMP
    ts_sensor.async_get_last_sensor_data = AsyncMock(
        return_value=SensorExtraStoredData(
            native_value=datetime(2026, 11, 14, 12, 0),  # naive -> rejected
            native_unit_of_measurement=None,
        )
    )
    await ts_sensor.async_restore_last_state(State("sensor.ts", "2026-11-14T12:00:00"))
    assert ts_sensor.native_value is None
    assert ts_sensor.state is None
    ts_sensor.async_get_last_sensor_data = AsyncMock(return_value=None)
    await ts_sensor.async_restore_last_state(
        State("sensor.ts", "2026-11-14T12:00:00+00:00")
    )
    assert ts_sensor.native_value == datetime(2026, 11, 14, 12, 0, tzinfo=UTC)
    assert ts_sensor.state == "2026-11-14T12:00:00+00:00"
    ts_sensor._attr_native_value = None
    ts_sensor.async_get_last_sensor_data = AsyncMock(
        return_value=SensorExtraStoredData(
            native_value=datetime(2026, 11, 14, 12, 0, tzinfo=UTC),
            native_unit_of_measurement=None,
        )
    )
    await ts_sensor.async_restore_last_state(
        State("sensor.ts", "2026-11-14T12:00:00+00:00")
    )
    assert ts_sensor.native_value == datetime(2026, 11, 14, 12, 0, tzinfo=UTC)

    # 7. Migration fallback: unknown/unavailable, non-finite float, non-numeric on BOTH
    #    total_increasing (Energy) and measurement (Power) sensors, ambiguous unitless state
    #    when display unit is overridden to kWh, valid unitless state when display == native,
    #    and incompatible display unit on last_state.attributes.
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
        assert s_fb.state is None

    p_corrupt = MyHOMEPowerSensor(
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
    p_corrupt.async_get_last_sensor_data = AsyncMock(return_value=None)
    await p_corrupt.async_restore_last_state(State("sensor.p", "corrupt_text"))
    assert p_corrupt.native_value is None
    assert p_corrupt.state is None

    p_unitless_same = MyHOMEPowerSensor(
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
    p_unitless_same.async_get_last_sensor_data = AsyncMock(return_value=None)
    await p_unitless_same.async_restore_last_state(State("sensor.p", "250"))
    assert p_unitless_same.native_value == pytest.approx(250.0)
    assert p_unitless_same.state == pytest.approx(250.0)

    # Unitless State("8133.608") when Entity Registry display unit is overridden to kWh
    # MUST be skipped rather than divided by 1000 into 8.133608 kWh!
    e_overridden = _new_energy()
    e_overridden._sensor_option_unit_of_measurement = UnitOfEnergy.KILO_WATT_HOUR
    e_overridden.async_get_last_sensor_data = AsyncMock(return_value=None)
    await e_overridden.async_restore_last_state(State("sensor.e", "8133.608"))
    assert e_overridden.native_value is None
    assert e_overridden.state is None

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


@pytest.mark.asyncio
async def test_live_myhomeserver1_meter51_trace_replay_wh_and_kwh_preserves_int_and_no_reset(
    hass: HomeAssistant,
) -> None:
    """Replay @xmavgithub's live MyHomeServer1 (2.87.13) meter 51 capture in Wh and kWh.

    Verifies both Issue #907 requirements end-to-end across startup, live bus replay, and
    config-entry reload:
    1. Overridden kWh totalizer restores 8151.646 kWh (8151646 Wh int) with zero meter reset.
    2. Native Wh sensors (daily 3017 Wh via RestoreSensor, monthly 88973 Wh via migration
       fallback, plus totalizer 8133608 Wh in native Wh) preserve exact ``int`` ``native_value``
       so ``hass.states.get(entity_id).state`` is ``"3017"`` / ``"88973"`` / ``"8133608"``
       rather than ``"3017.0"`` / ``"88973.0"`` / ``"8133608.0"``.
    """
    trace = json.loads(MHS1_LIVE_TRACE_PATH.read_text(encoding="utf-8"))
    live_mac = trace["gateway"]["mac"]
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={
            CONF_MAC: live_mac,
            "host": trace["gateway"]["host"],
            "port": 20000,
            "name": trace["gateway"]["model"],
        },
        unique_id=live_mac,
    )
    entry.add_to_hass(hass)

    ent_reg = er.async_get(hass)
    total_reg = ent_reg.async_get_or_create(
        SENSOR_DOMAIN,
        DOMAIN,
        f"{live_mac}-18-51-total-energy",
        suggested_object_id="meter_51_total_energy",
        config_entry=entry,
    )
    daily_reg = ent_reg.async_get_or_create(
        SENSOR_DOMAIN,
        DOMAIN,
        f"{live_mac}-18-51-daily-energy",
        suggested_object_id="meter_51_daily_energy",
        config_entry=entry,
    )
    monthly_reg = ent_reg.async_get_or_create(
        SENSOR_DOMAIN,
        DOMAIN,
        f"{live_mac}-18-51-monthly-energy",
        suggested_object_id="meter_51_monthly_energy",
        config_entry=entry,
    )
    native_wh_total_reg = ent_reg.async_get_or_create(
        SENSOR_DOMAIN,
        DOMAIN,
        f"{live_mac}-18-52-total-energy",
        suggested_object_id="meter_52_native_wh_energy",
        config_entry=entry,
    )
    total_id = total_reg.entity_id
    daily_id = daily_reg.entity_id
    monthly_id = monthly_reg.entity_id
    native_wh_id = native_wh_total_reg.entity_id

    # Override meter 51 totalizer display unit to kWh; keep daily, monthly, and meter 52 in native Wh!
    ent_reg.async_update_entity_options(
        total_id, SENSOR_DOMAIN, {ATTR_UNIT_OF_MEASUREMENT: UnitOfEnergy.KILO_WATT_HOUR}
    )

    mock_restore_cache_with_extra_data(
        hass,
        [
            (
                State(
                    total_id,
                    "8151.646",
                    {ATTR_UNIT_OF_MEASUREMENT: UnitOfEnergy.KILO_WATT_HOUR},
                ),
                SensorExtraStoredData(
                    native_value=8151646,
                    native_unit_of_measurement=UnitOfEnergy.WATT_HOUR,
                ),
            ),
            (
                State(
                    daily_id,
                    "3017",
                    {ATTR_UNIT_OF_MEASUREMENT: UnitOfEnergy.WATT_HOUR},
                ),
                SensorExtraStoredData(
                    native_value=3017,
                    native_unit_of_measurement=UnitOfEnergy.WATT_HOUR,
                ),
            ),
            (
                State(
                    monthly_id,
                    "88973",
                    {ATTR_UNIT_OF_MEASUREMENT: UnitOfEnergy.WATT_HOUR},
                ),
                None,
            ),
            (
                State(
                    native_wh_id,
                    "8133608",
                    {ATTR_UNIT_OF_MEASUREMENT: UnitOfEnergy.WATT_HOUR},
                ),
                SensorExtraStoredData(
                    native_value=8133608,
                    native_unit_of_measurement=UnitOfEnergy.WATT_HOUR,
                ),
            ),
        ],
    )

    observed_total_kwh: list[float] = []

    @callback
    def _record_total(event: Event[Any]) -> None:
        if event.data.get("entity_id") != total_id:
            return
        new_state: State | None = event.data.get("new_state")
        if new_state is not None and new_state.state not in (
            STATE_UNKNOWN,
            STATE_UNAVAILABLE,
        ):
            observed_total_kwh.append(float(new_state.state))

    unsub = hass.bus.async_listen(EVENT_STATE_CHANGED, _record_total)
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
            hass.data[DOMAIN][live_mac][CONF_ENTITY]._on_event_connection_state_change(True)
            await hass.async_block_till_done()

            # T+0s before any bus frame arrives:
            s_total_t0 = hass.states.get(total_id)
            s_daily_t0 = hass.states.get(daily_id)
            s_monthly_t0 = hass.states.get(monthly_id)
            s_wh_t0 = hass.states.get(native_wh_id)
            assert s_total_t0 is not None and s_total_t0.state == "8151.646"
            assert s_daily_t0 is not None and s_daily_t0.state == "3017"
            assert s_monthly_t0 is not None and s_monthly_t0.state == "88973"
            assert s_wh_t0 is not None and s_wh_t0.state == "8133608"

            # Replay all 96 frames from @xmavgithub's live MyHomeServer1 (2.87.13) capture
            rx_count = 0
            for frame in trace["frames"]:
                if frame["direction"] != "rx":
                    continue
                rx_count += 1
                parsed = OWNMessage.parse(frame["raw"])
                assert parsed is not None
                async_dispatcher_send(hass, f"myhome_message_{live_mac}", parsed)
            await hass.async_block_till_done()
            assert rx_count == 74

            # Reload config entry so RestoreSensor persists & restores live int native_values
            assert await hass.config_entries.async_reload(entry.entry_id)
            await hass.async_block_till_done()
            hass.data[DOMAIN][live_mac][CONF_ENTITY]._on_event_connection_state_change(True)
            await hass.async_block_till_done()

            s_total_r = hass.states.get(total_id)
            s_daily_r = hass.states.get(daily_id)
            s_monthly_r = hass.states.get(monthly_id)
            s_wh_r = hass.states.get(native_wh_id)
            assert s_total_r is not None and s_total_r.state == "8151.646"
            assert s_daily_r is not None and s_daily_r.state == "3017"
            assert s_monthly_r is not None and s_monthly_r.state == "88973"
            assert s_wh_r is not None and s_wh_r.state == "8133608"

            # Underlying entity native_value remains exact int (not float!)
            sensor_comp = hass.data[SENSOR_DOMAIN]
            for eid in (total_id, daily_id, monthly_id, native_wh_id):
                ent_obj = sensor_comp.get_entity(eid)
                assert ent_obj is not None
                assert type(ent_obj.native_value) is int

            assert observed_total_kwh
            prev_val = 8151.646
            for curr in observed_total_kwh:
                assert curr == pytest.approx(8151.646)
                assert not reset_detected(hass, total_id, curr, prev_val, s_total_r)
                prev_val = curr
        finally:
            unsub()
            await hass.config_entries.async_unload(entry.entry_id)
            await hass.async_block_till_done()


def test_issue_907_golden_corpus_conformance_entries() -> None:
    """Verify Issue #907 MyHomeServer1 meter 51 Wh frames are registered in Golden Corpus CI."""
    corpus_by_id = {
        rec["id"]: rec
        for rec in json.loads(CORPUS_JSON_PATH.read_text(encoding="utf-8"))
    }
    live_raw_frames = {
        f["raw"]
        for f in json.loads(MHS1_LIVE_TRACE_PATH.read_text(encoding="utf-8"))["frames"]
    }

    expected_capture_ids = {
        "energy.req.cumulative_totalizer.meter51": "*#18*51*51##",
        "energy.capture.cumulative_totalizer.meter51.8151646": "*#18*51*51*8151646##",
        "energy.capture.req.monthly_totalizer.meter51": "*#18*51*53##",
        "energy.capture.resp.monthly_totalizer.meter51.88973": "*#18*51*53*88973##",
        "energy.capture.req.daily_totalizer.meter51": "*#18*51*54##",
        "energy.capture.resp.daily_totalizer.meter51.3017": "*#18*51*54*3017##",
    }
    for rec_id, raw_frame in expected_capture_ids.items():
        assert rec_id in corpus_by_id
        rec = corpus_by_id[rec_id]
        assert rec["frame"] == raw_frame
        assert rec["source"] == "community-plant-capture"
        assert raw_frame in live_raw_frames
        parsed = OWNMessage.parse(raw_frame)
        if rec["direction"] == "dimension_request":
            assert isinstance(parsed, OWNEnergyCommand)
        else:
            assert isinstance(parsed, OWNEnergyEvent)

    m_total = OWNMessage.parse("*#18*51*51*8151646##")
    m_month = OWNMessage.parse("*#18*51*53*88973##")
    m_day = OWNMessage.parse("*#18*51*54*3017##")
    assert isinstance(m_total, OWNEnergyEvent) and m_total.total_consumption == 8151646
    assert (
        isinstance(m_month, OWNEnergyEvent)
        and m_month.current_month_partial_consumption == 88973
    )
    assert (
        isinstance(m_day, OWNEnergyEvent)
        and m_day.current_day_partial_consumption == 3017
    )


def test_issue_907_908_myhomeserver1_gateway_golden_corpus_entries() -> None:
    """Verify MyHomeServer1 (2.87.13) WHO=13 & WHO=1013 sweep/clock frames in Golden Corpus CI."""
    corpus_by_id = {
        rec["id"]: rec
        for rec in json.loads(CORPUS_JSON_PATH.read_text(encoding="utf-8"))
    }
    expected_gateway_ids = {
        "gateway.capture.req.diagnostic_object_model.where0": "*#1013*0*1##",
        "gateway.capture.resp.diagnostic_object_model.myhomeserver1": "*#1013**1*67*15*5*0##",
        "gateway.capture.resp.firmware.version.myhomeserver1.2_87_13": "*#13**16*2*87*13##",
        "gateway.capture.req.kernel_version": "*#13**23##",
        "gateway.resp.kernel.version.f461": "*#13**23*5*10*35##",
        "gateway.capture.req.distribution_version": "*#13**24##",
        "gateway.capture.resp.distribution_version.myhomeserver1.2_26_3": "*#13**24*2*26*3##",
        "gateway.capture.write.clock_sync.empty_tz.myhomeserver1": "*#13**#0*14*59*05*##",
        "gateway.capture.resp.time.unconfigured_tz.999": "*#13**0*15*13*25*999##",
        "gateway.capture.write.date_sync.2026_11_14": "*#13**#1*06*14*11*2026##",
    }
    for rec_id, raw_frame in expected_gateway_ids.items():
        assert rec_id in corpus_by_id
        rec = corpus_by_id[rec_id]
        assert rec["frame"] == raw_frame
        assert rec["source"] == "community-plant-capture"
        assert str(OWNMessage.parse(raw_frame)) == raw_frame

    diag_req = OWNMessage.parse("*#1013*0*1##")
    assert isinstance(diag_req, OWNGatewayDiagnosticCommand) and diag_req.where == "0"

    diag_resp = OWNMessage.parse("*#1013**1*67*15*5*0##")
    assert isinstance(diag_resp, OWNGatewayDiagnosticEvent)
    assert diag_resp.object_model == "67" and diag_resp.device_type == "MyHomeServer1"

    fw_resp = OWNMessage.parse("*#13**16*2*87*13##")
    assert isinstance(fw_resp, OWNGatewayEvent) and fw_resp.firmware_version == "2.87.13"

    kern_req = OWNMessage.parse("*#13**23##")
    assert isinstance(kern_req, OWNGatewayCommand) and kern_req.dimension == 23
    kern_resp = OWNMessage.parse("*#13**23*5*10*35##")
    assert isinstance(kern_resp, OWNGatewayEvent) and kern_resp.kernel_version == "5.10.35"

    dist_req = OWNMessage.parse("*#13**24##")
    assert isinstance(dist_req, OWNGatewayCommand) and dist_req.dimension == 24
    dist_resp = OWNMessage.parse("*#13**24*2*26*3##")
    assert isinstance(dist_resp, OWNGatewayEvent) and dist_resp.distribution_version == "2.26.3"

    clk_write = OWNMessage.parse("*#13**#0*14*59*05*##")
    assert isinstance(clk_write, OWNGatewayCommand)
    assert clk_write.dimension_value == ["14", "59", "05", ""]
    assert clk_write.time == time(14, 59, 5)

    clk_resp = OWNMessage.parse("*#13**0*15*13*25*999##")
    assert isinstance(clk_resp, OWNGatewayEvent)
    assert clk_resp.time == time(15, 13, 25) and clk_resp.is_timezone_unconfigured is True

    date_write = OWNMessage.parse("*#13**#1*06*14*11*2026##")
    assert isinstance(date_write, OWNGatewayCommand) and date_write.date == date(2026, 11, 14)

