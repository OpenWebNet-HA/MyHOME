"""Entities link to their gateway with via_device_id; via_device on cores that predate it."""
from unittest.mock import MagicMock, patch

from homeassistant.helpers.device_registry import DeviceInfo

from custom_components.myhome.const import DOMAIN
from custom_components.myhome.switch import MyHOMESwitch


def _switch(gateway):
    return MyHOMESwitch(
        hass=None, name="Switch 12", entity_name=None, icon=None, icon_on=None, device_id="12", who="1",
        where="12", interface=None, device_class="switch", manufacturer="BTicino", model="F411",
        gateway=gateway,
    )


def test_gateway_link_follows_the_core_device_info():
    gateway = MagicMock(mac="00:03:50:00:00:01", unique_id="00:03:50:00:00:01", device_registry_id="dev-1")
    info = _switch(gateway)._attr_device_info
    assert info.get("via_device_id") == "dev-1" and "via_device" not in info  # core 2026.x
    with patch.dict(DeviceInfo.__annotations__, clear=True):  # a core without via_device_id
        info = _switch(gateway)._attr_device_info
    assert info.get("via_device") == (DOMAIN, "00:03:50:00:00:01") and "via_device_id" not in info
