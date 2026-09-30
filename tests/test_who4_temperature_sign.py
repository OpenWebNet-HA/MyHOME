"""WHO 4 temperatures: freezing, non-freezing and exactly zero (#566 follow-up).

The bus sends ``SXXX`` (S = 1 for a negative reading, tenths of a degree).
Three things must hold for every path that reads one (zone sensor, probe on
DIMENSION 15, climate entity):

* below zero the value is negative,
* above zero it is left alone,
* exactly zero is ``0.0`` and never ``-0.0`` (which Home Assistant would render
  as "-0.0 °C", and which ``1000`` used to produce).
"""
import math
from unittest.mock import AsyncMock, MagicMock

import pytest
from homeassistant.components.sensor import SensorDeviceClass
from homeassistant.core import HomeAssistant
from OWNd.message import OWNMessage

from custom_components.myhome.climate import MyHOMEClimate
from custom_components.myhome.const import signed_who4_temperature
from custom_components.myhome.sensor import MyHOMETemperatureSensor

# (four-digit bus value, expected °C)
FREEZING = [("1001", -0.1), ("1055", -5.5), ("1123", -12.3), ("1200", -20.0), ("1500", -50.0)]
NON_FREEZING = [("0001", 0.1), ("0055", 5.5), ("0215", 21.5), ("0999", 99.9)]
EXACTLY_ZERO = [("0000", 0.0), ("1000", 0.0)]  # "1000" is a negative sign on nothing
ALL_CASES = FREEZING + NON_FREEZING + EXACTLY_ZERO


def _is_positive_zero(value: float) -> bool:
    return value == 0 and math.copysign(1.0, value) == 1.0


def _assert_temperature(value: float | None, expected: float) -> None:
    assert value == expected
    if expected == 0:
        assert _is_positive_zero(value), f"negative zero: {value!r}"
        assert str(value) == "0.0"


@pytest.fixture
def gateway() -> MagicMock:
    gw = MagicMock()
    gw.mac = "00:03:50:00:55:55"
    gw.unique_id = gw.mac
    gw.log_id = "[Test]"
    gw.send = AsyncMock()
    gw.send_status_request = AsyncMock()
    return gw


def _sensor(hass: HomeAssistant, gateway: MagicMock, where: str = "1") -> MyHOMETemperatureSensor:
    sensor = MyHOMETemperatureSensor(
        hass=hass, name="T", device_id=f"4-{where}", who="4", where=where,
        device_class=SensorDeviceClass.TEMPERATURE, manufacturer="BTicino", model="Probe", gateway=gateway,
    )
    sensor.async_schedule_update_ha_state = MagicMock()
    return sensor


# --- the helper itself -----------------------------------------------------


@pytest.mark.parametrize(("raw", "expected"), ALL_CASES)
def test_helper_on_real_zone_frames(raw: str, expected: float) -> None:
    message = OWNMessage.parse(f"*#4*1*0*{raw}##")
    _assert_temperature(signed_who4_temperature(message, message.main_temperature), expected)


@pytest.mark.parametrize(("raw", "expected"), ALL_CASES)
def test_helper_on_real_probe_frames(raw: str, expected: float) -> None:
    message = OWNMessage.parse(f"*#4*1*15*01*{raw}*3##")
    _assert_temperature(signed_who4_temperature(message, message.secondary_temperature[1]), expected)


def test_helper_passes_through_what_it_cannot_judge() -> None:
    assert signed_who4_temperature(MagicMock(), 3.0) == 3.0  # a mock has no raw value
    assert signed_who4_temperature(MagicMock(_dimension_value=["1055"]), None) is None
    assert signed_who4_temperature(MagicMock(_dimension_value=[]), 3.0) == 3.0
    assert signed_who4_temperature(MagicMock(_dimension_value=["105"]), 3.0) == 3.0  # not four digits


def test_helper_does_not_flip_an_already_negative_value() -> None:
    """OWNd decodes DIMENSION 15 with its sign; applying the sign again must not turn it positive."""
    assert signed_who4_temperature(MagicMock(_dimension_value=["1055"]), -5.5) == -5.5


# --- zone sensor (DIMENSION 0) ---------------------------------------------


@pytest.mark.parametrize(("raw", "expected"), ALL_CASES)
def test_zone_sensor(hass: HomeAssistant, gateway: MagicMock, raw: str, expected: float) -> None:
    sensor = _sensor(hass, gateway)
    sensor.handle_event(OWNMessage.parse(f"*#4*1*0*{raw}##"))
    _assert_temperature(sensor._attr_native_value, expected)


# --- external probe (DIMENSION 15) -----------------------------------------


@pytest.mark.parametrize(("raw", "expected"), ALL_CASES)
def test_probe_sensor(hass: HomeAssistant, gateway: MagicMock, raw: str, expected: float) -> None:
    sensor = _sensor(hass, gateway, where="0#1")
    sensor.handle_event(OWNMessage.parse(f"*#4*0#1*15*01*{raw}*3##"))
    _assert_temperature(sensor._attr_native_value, expected)


@pytest.mark.parametrize(("raw", "expected"), ALL_CASES)
def test_probe_sensor_from_a_bare_message_without_a_sensor_number(
    hass: HomeAssistant, gateway: MagicMock, raw: str, expected: float
) -> None:
    """The raw-dimension fallbacks (messages that carry only ``dimension_value``)."""
    sensor = _sensor(hass, gateway)
    for dimension in (0, 15):
        message = MagicMock(spec=["dimension", "dimension_value", "human_readable_log", "message_type"])
        message.message_type = "other"
        message.dimension = dimension
        message.dimension_value = [raw]
        message.human_readable_log = ""
        sensor._attr_native_value = None
        sensor.handle_event(message)
        _assert_temperature(sensor._attr_native_value, expected)


# --- climate entity --------------------------------------------------------


@pytest.mark.parametrize(("raw", "expected"), ALL_CASES)
def test_climate_current_temperature(hass: HomeAssistant, gateway: MagicMock, raw: str, expected: float) -> None:
    climate = MyHOMEClimate(
        hass=hass, name="Zone", device_id="1", who="4", where="1", heating=True, cooling=False,
        fan=False, standalone=True, central=False, manufacturer="B", model="M", gateway=gateway,
    )
    climate.entity_id = "climate.zone"
    climate.async_schedule_update_ha_state = MagicMock()
    climate.handle_event(OWNMessage.parse(f"*#4*1*0*{raw}##"))
    _assert_temperature(climate.current_temperature, expected)


def test_a_reading_that_crosses_zero_follows_the_bus(hass: HomeAssistant, gateway: MagicMock) -> None:
    sensor = _sensor(hass, gateway)
    for raw, expected in [("0010", 1.0), ("0000", 0.0), ("1010", -1.0), ("1000", 0.0), ("0010", 1.0)]:
        sensor.handle_event(OWNMessage.parse(f"*#4*1*0*{raw}##"))
        _assert_temperature(sensor._attr_native_value, expected)


# --- raw decoder -----------------------------------------------------------


@pytest.mark.parametrize(("raw", "expected"), ALL_CASES)
def test_raw_decoder(raw: str, expected: float) -> None:
    from custom_components.myhome.const import who4_raw_to_celsius

    _assert_temperature(who4_raw_to_celsius(raw), expected)


def test_raw_decoder_rejects_garbage_and_the_sensor_ignores_it(hass: HomeAssistant, gateway: MagicMock) -> None:
    from custom_components.myhome.const import who4_raw_to_celsius

    with pytest.raises(ValueError):
        who4_raw_to_celsius("n/a")

    sensor = _sensor(hass, gateway)
    sensor._attr_native_value = 4.2
    message = MagicMock(spec=["dimension", "dimension_value", "human_readable_log", "message_type"])
    message.message_type = "other"
    message.dimension = 0
    message.dimension_value = ["n/a"]
    sensor.handle_event(message)
    assert sensor._attr_native_value == 4.2
