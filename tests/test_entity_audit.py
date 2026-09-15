"""Entity audit for the Integration Quality Scale Gold rules.

One table for every entity class the integration creates: its device class,
entity category and whether it is enabled by default. ``entity-device-class``,
``entity-category`` and ``entity-disabled-by-default`` are audited here so a
change in one platform cannot silently regress the manifest claims.
"""
from unittest.mock import MagicMock

import pytest
from homeassistant.components.binary_sensor import BinarySensorDeviceClass
from homeassistant.components.cover import CoverDeviceClass
from homeassistant.components.media_player import MediaPlayerDeviceClass
from homeassistant.components.sensor import SensorDeviceClass
from homeassistant.components.switch import SwitchDeviceClass
from homeassistant.helpers.entity import EntityCategory

from custom_components.myhome.alarm_control_panel import MyHOMEAlarmControlPanel
from custom_components.myhome.binary_sensor import (
    MyHOMEAuxiliary,
    MyHOMEDryContact,
    MyHOMEMotionSensor,
)
from custom_components.myhome.button import (
    CalibrateAllCoversButtonEntity,
    CalibrateCoverButtonEntity,
    DisableCommandButtonEntity,
    EnableCommandButtonEntity,
)
from custom_components.myhome.climate import MyHOMEClimate
from custom_components.myhome.cover import MyHOMECover
from custom_components.myhome.light import MyHOMELight
from custom_components.myhome.media_player import MyHOMEMediaPlayer
from custom_components.myhome.sensor import (
    MyHOMEEnergySensor,
    MyHOMEIlluminanceSensor,
    MyHOMEPowerSensor,
    MyHOMETemperatureSensor,
)
from custom_components.myhome.switch import MyHOMESwitch


@pytest.fixture
def gateway():
    gw = MagicMock()
    gw.mac = "00:03:50:00:00:01"
    gw.unique_id = gw.mac
    gw.device_registry_id = "gw-device"
    gw.config_entry = MagicMock()
    gw.config_entry.options = {}
    gw.config_entry.entry_id = "entry"
    gw.config_entry.data = {"mac": gw.mac}
    return gw


COMMON = dict(manufacturer="BTicino", model="M")


def _light(hass, gw):
    return MyHOMELight(hass=hass, name="L", entity_name=None, icon=None, icon_on=None, device_id="12", who="1",
                       where="12", interface=None, dimmable=False, gateway=gw, **COMMON)


def _switch(hass, gw, device_class):
    return MyHOMESwitch(hass=hass, name="S", entity_name=None, icon=None, icon_on=None, device_id="13", who="1",
                        where="13", interface=None, device_class=device_class, gateway=gw, **COMMON)


def _cover(hass, gw):
    return MyHOMECover(hass=hass, name="C", entity_name=None, device_id="21", who="2", where="21", interface=None,
                       advanced=False, gateway=gw, **COMMON)


def _climate(hass, gw):
    return MyHOMEClimate(hass=hass, name="Z", device_id="1", who="4", where="1", heating=True, cooling=False,
                         fan=False, standalone=False, central=False, gateway=gw, **COMMON)


def _binary(cls, hass, gw, device_class, who):
    return cls(hass=hass, name="B", entity_name=None, device_id="31", who=who, where="31", inverted=False,
               device_class=device_class, gateway=gw, **COMMON)


def _sensor(cls, hass, gw, device_class, **extra):
    return cls(hass=hass, name="S", device_id="51", who="18", where="51", device_class=device_class, gateway=gw,
               **COMMON, **extra)


def _media(hass, gw):
    return MyHOMEMediaPlayer(hass=hass, name="A", entity_name=None, device_id="1#16", who="16", where="1",
                             gateway=gw, **COMMON)


def _button(cls, hass, gw):
    return cls(hass=hass, platform="button", name="B", device_id="12", who="1", where="12", interface=None,
               gateway=gw, **COMMON)


def _alarm(hass, gw):
    return MyHOMEAlarmControlPanel(hass=hass, name="Alarm", entity_name=None, device_id="0", who="5", where="0",
                                   gateway=gw, **COMMON)


# (label, factory, device_class, entity_category, enabled_by_default)
AUDIT = [
    ("light", _light, None, None, True),
    ("switch", lambda h, g: _switch(h, g, SwitchDeviceClass.SWITCH), SwitchDeviceClass.SWITCH, None, True),
    ("outlet", lambda h, g: _switch(h, g, SwitchDeviceClass.OUTLET), SwitchDeviceClass.OUTLET, None, True),
    ("cover", _cover, CoverDeviceClass.SHUTTER, None, True),
    ("climate", _climate, None, None, True),
    ("dry contact", lambda h, g: _binary(MyHOMEDryContact, h, g, BinarySensorDeviceClass.WINDOW, "25"),
     BinarySensorDeviceClass.WINDOW, None, True),
    ("auxiliary", lambda h, g: _binary(MyHOMEAuxiliary, h, g, BinarySensorDeviceClass.DOOR, "9"),
     BinarySensorDeviceClass.DOOR, None, True),
    ("motion", lambda h, g: _binary(MyHOMEMotionSensor, h, g, BinarySensorDeviceClass.MOTION, "1"),
     BinarySensorDeviceClass.MOTION, None, True),
    ("power", lambda h, g: _sensor(MyHOMEPowerSensor, h, g, SensorDeviceClass.POWER), SensorDeviceClass.POWER, None, True),
    ("energy total", lambda h, g: _sensor(MyHOMEEnergySensor, h, g, SensorDeviceClass.ENERGY, entity_specific_id="total_energy"),
     SensorDeviceClass.ENERGY, None, True),
    ("energy today", lambda h, g: _sensor(MyHOMEEnergySensor, h, g, SensorDeviceClass.ENERGY, entity_specific_id="daily_energy"),
     SensorDeviceClass.ENERGY, None, False),
    ("energy month", lambda h, g: _sensor(MyHOMEEnergySensor, h, g, SensorDeviceClass.ENERGY, entity_specific_id="monthly_energy"),
     SensorDeviceClass.ENERGY, None, False),
    ("temperature", lambda h, g: _sensor(MyHOMETemperatureSensor, h, g, SensorDeviceClass.TEMPERATURE),
     SensorDeviceClass.TEMPERATURE, None, True),
    ("illuminance", lambda h, g: _sensor(MyHOMEIlluminanceSensor, h, g, SensorDeviceClass.ILLUMINANCE),
     SensorDeviceClass.ILLUMINANCE, None, True),
    ("media player", _media, MediaPlayerDeviceClass.SPEAKER, None, True),
    ("lock button", lambda h, g: _button(DisableCommandButtonEntity, h, g), None, EntityCategory.CONFIG, True),
    ("unlock button", lambda h, g: _button(EnableCommandButtonEntity, h, g), None, EntityCategory.CONFIG, True),
    ("calibrate cover button",
     lambda h, g: CalibrateCoverButtonEntity(h, platform="button", device_id="21", where="21", interface=None, name="C", gateway=g),
     None, EntityCategory.CONFIG, True),
    ("calibrate all button", lambda h, g: CalibrateAllCoversButtonEntity(h, g.config_entry, g), None, EntityCategory.CONFIG, True),
    ("alarm", _alarm, None, None, True),
]


@pytest.mark.parametrize("label, factory, device_class, category, enabled", AUDIT, ids=[row[0] for row in AUDIT])
def test_entity_audit(hass, gateway, label, factory, device_class, category, enabled):
    entity = factory(hass, gateway)
    assert entity.device_class == device_class, f"{label}: device_class"
    assert entity.entity_category == category, f"{label}: entity_category"
    assert entity.entity_registry_enabled_default is enabled, f"{label}: enabled by default"


def test_entities_link_to_the_gateway_device(hass, gateway):
    """Every entity's device points at the gateway device through via_device_id."""
    entity = _light(hass, gateway)
    assert entity.device_info["via_device_id"] == gateway.device_registry_id
    assert "via_device" not in entity.device_info
