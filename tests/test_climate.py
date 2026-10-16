"""Unit tests for climate platform blank-start quality and phantom entity suppression (#681)."""
from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from OWNd.message import OWNMessage
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.myhome.climate import (
    _calling_zones,
    _zone_address,
    async_setup_entry,
)
from custom_components.myhome.discovery import Address
from tests.conftest import attach_runtime


def test_climate_calling_zones_rejects_zero_and_double_zero() -> None:
    """_calling_zones must reject 0 and 00 to avoid discovering phantom zone 0 / 00."""
    # Heating broadcast for area 00
    msg_00 = OWNMessage.parse("*#4*00*15*0215##")
    assert msg_00 is not None
    zones_00, iface_00 = _calling_zones(msg_00)
    assert "00" not in zones_00
    assert zones_00 == []

    # Heating general 0
    msg_0 = OWNMessage.parse("*4*0*0##")
    assert msg_0 is not None
    zones_0, iface_0 = _calling_zones(msg_0)
    assert "0" not in zones_0
    assert zones_0 == []

    # Routed area 00 frame behind an F422 interface
    msg_routed_00 = MagicMock(where="00#4#01", zone=None, what=None, what_param=None, interface="01")
    zones_r00, iface_r00 = _calling_zones(msg_routed_00)
    assert zones_r00 == []

    # Routed general 0 frame behind an F422 interface
    msg_routed_0 = MagicMock(where="0#4#01", zone=None, what=None, what_param=None, interface="01")
    zones_r0, iface_r0 = _calling_zones(msg_routed_0)
    assert zones_r0 == []

    # Legitimate zone 1
    msg_zone1 = OWNMessage.parse("*#4*1*14*0215##")
    assert msg_zone1 is not None
    zones_1, iface_1 = _calling_zones(msg_zone1)
    assert zones_1 == ["1"]
    assert _zone_address(msg_zone1) == Address("1")


@pytest.mark.asyncio
async def test_climate_registry_restore_prunes_phantom_zone_00(hass: HomeAssistant) -> None:
    """Existing phantom climate.climate_zone_00 in entity registry is pruned on startup."""
    mac = "00:11:22:33:44:55"
    config_entry = MockConfigEntry(
        domain="myhome",
        data={"mac": mac},
        entry_id="test_entry",
        unique_id=mac,
    )
    config_entry.add_to_hass(hass)

    registry = er.async_get(hass)
    phantom = registry.async_get_or_create(
        domain="climate",
        platform="myhome",
        unique_id=f"{mac}-4-00",
        config_entry=config_entry,
        suggested_object_id="climate_zone_00",
    )
    phantom_routed = registry.async_get_or_create(
        domain="climate",
        platform="myhome",
        unique_id=f"{mac}-4-00#4#01",
        config_entry=config_entry,
        suggested_object_id="climate_zone_00_01",
    )
    legit = registry.async_get_or_create(
        domain="climate",
        platform="myhome",
        unique_id=f"{mac}-4-1",
        config_entry=config_entry,
        suggested_object_id="climate_zone_1",
    )

    hass.data = {
        "myhome": {
            mac: {
                "platforms": {"climate": {}},
                "entity": MagicMock(mac=mac),
            }
        }
    }
    attach_runtime(hass, config_entry)

    mock_add_entities = MagicMock()
    await async_setup_entry(hass, config_entry, mock_add_entities)

    # Phantom zones 00 and routed 00#4#01 were pruned
    assert registry.async_get(phantom.entity_id) is None
    assert registry.async_get(phantom_routed.entity_id) is None
    # Legitimate zone 1 was kept and restored
    assert registry.async_get(legit.entity_id) is not None


@pytest.mark.asyncio
async def test_climate_remembers_humidity_and_queries_on_startup(hass: HomeAssistant) -> None:
    """Climate zones that support humidity remember it across restarts and query *#4*Z*60## on startup (#710)."""
    from unittest.mock import AsyncMock, patch

    from homeassistant.core import State
    from OWNd.message import OWNEvent

    from custom_components.myhome.climate import MyHOMEClimate

    # Physical gateway (e.g. F461) where dimensionless *#4*Z## is not supported
    gateway = MagicMock()
    gateway.mac = "00:03:50:11:22:33"
    gateway.log_id = "[test_710]"
    gateway.supports_zone_status = False
    gateway.initial_discovery_pending = False
    gateway.send_status_request = AsyncMock()

    # 1. Zone without humidity does not poll on startup on an F461
    zone_no_hum = MyHOMEClimate(
        hass=hass,
        name="Zone 1",
        device_id="4-1",
        who="4",
        where="1",
        heating=True,
        cooling=True,
        fan=False,
        standalone=True,
        central=False,
        manufacturer="BTicino",
        model="Heating Zone",
        gateway=gateway,
    )
    zone_no_hum.entity_id = "climate.zone_1"
    assert zone_no_hum._humidity is False
    assert zone_no_hum._poll_on_add is False

    state_no_hum = State("climate.zone_1", "heat", {"temperature": 21.0})
    with patch.object(zone_no_hum, "async_get_last_state", return_value=state_no_hum):
        await zone_no_hum.async_added_to_hass()
    gateway.send_status_request.assert_not_awaited()

    # Receiving a humidity frame dynamically enables humidity support
    hum_event = OWNEvent.parse("*#4*1*60*55##")
    assert hum_event is not None
    zone_no_hum.handle_event(hum_event)
    assert zone_no_hum._humidity is True
    assert zone_no_hum.current_humidity == 55

    # 2. On restart, a zone whose prior state had current_humidity restores it and queries *#4*Z*60##
    zone_restored = MyHOMEClimate(
        hass=hass,
        name="Zone 2",
        device_id="4-2",
        who="4",
        where="2",
        heating=True,
        cooling=True,
        fan=False,
        standalone=True,
        central=False,
        manufacturer="BTicino",
        model="Heating Zone",
        gateway=gateway,
    )
    zone_restored.entity_id = "climate.zone_2"
    state_with_hum = State("climate.zone_2", "heat", {"temperature": 22.0, "current_humidity": 48.0})
    with patch.object(zone_restored, "async_get_last_state", return_value=state_with_hum):
        await zone_restored.async_added_to_hass()

    assert zone_restored._humidity is True
    assert zone_restored.current_humidity == 48.0
    sent = [str(call.args[0]) for call in gateway.send_status_request.await_args_list]
    assert sent == ["*#4*2*60##"]

    # 3. Invalid current_humidity value still enables humidity polling without crashing
    gateway.send_status_request.reset_mock()
    zone_bad_hum = MyHOMEClimate(
        hass=hass,
        name="Zone 3",
        device_id="4-3",
        who="4",
        where="3",
        heating=True,
        cooling=True,
        fan=False,
        standalone=True,
        central=False,
        manufacturer="BTicino",
        model="Heating Zone",
        gateway=gateway,
    )
    zone_bad_hum.entity_id = "climate.zone_3"
    state_bad_hum = State("climate.zone_3", "heat", {"current_humidity": "invalid"})
    with patch.object(zone_bad_hum, "async_get_last_state", return_value=state_bad_hum):
        await zone_bad_hum.async_added_to_hass()

    assert zone_bad_hum._humidity is True
    assert zone_bad_hum.current_humidity is None
    sent_bad = [str(call.args[0]) for call in gateway.send_status_request.await_args_list]
    assert sent_bad == ["*#4*3*60##"]

    # 4. Explicit humidity=True in configuration queries *#4*Z*60## on startup, and central unit ignores it
    gateway.send_status_request.reset_mock()
    zone_cfg_hum = MyHOMEClimate(
        hass=hass,
        name="Zone 4",
        device_id="4-4",
        who="4",
        where="4",
        heating=True,
        cooling=True,
        fan=False,
        humidity=True,
        standalone=True,
        central=False,
        manufacturer="BTicino",
        model="Heating Zone",
        gateway=gateway,
    )
    zone_cfg_hum.entity_id = "climate.zone_4"
    assert zone_cfg_hum._humidity is True
    assert zone_cfg_hum._poll_on_add is True
    await zone_cfg_hum.async_update()
    assert [str(call.args[0]) for call in gateway.send_status_request.await_args_list] == ["*#4*4*60##"]

    central = MyHOMEClimate(
        hass=hass,
        name="Central Unit",
        device_id="4-#0",
        who="4",
        where="#0",
        heating=True,
        cooling=True,
        fan=False,
        humidity=True,
        standalone=False,
        central=True,
        manufacturer="BTicino",
        model="Central Unit (3550)",
        gateway=gateway,
    )
    central._enable_humidity()
    assert central._humidity is False

