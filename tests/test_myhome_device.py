"""Tests for MyHOMEEntity display naming and live DeviceHealth rename tracking."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from homeassistant.components.sensor import SensorDeviceClass
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import issue_registry as ir
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.myhome.button import CalibrateCoverButtonEntity
from custom_components.myhome.const import DOMAIN
from custom_components.myhome.cover import MyHOMECover
from custom_components.myhome.device_health import DeviceHealth, Fault, FaultKind, fault_issue_id
from custom_components.myhome.light import MyHOMELight
from custom_components.myhome.myhome_device import MyHOMEEntity
from custom_components.myhome.sensor import MyHOMETemperatureSensor

MAC = "00:03:50:00:12:44"


def _mock_gateway(hass: HomeAssistant | None = None, entry: MockConfigEntry | None = None) -> MagicMock:
    gw = MagicMock()
    gw.hass = hass
    gw.mac = MAC
    gw.unique_id = MAC
    gw.name = "MH200 Gateway"
    gw.log_id = "[myhome-device-test]"
    gw.device_registry_id = "gw-device"
    gw.availability_signal = "myhome_test_availability"
    gw.config_entry = entry or SimpleNamespace(entry_id="entry-test", options={}, data={"mac": MAC})
    gw.send_status_request = AsyncMock()
    gw.device_health = DeviceHealth(gw)
    return gw


def test_display_name_prefers_registry_and_device_user_renames() -> None:
    """_display_name resolves registry name, compat_name, device renames, and unregistered fallbacks."""
    gw = _mock_gateway()

    cover = MyHOMECover(
        hass=None,
        name="Rolluik Woonkamer",
        entity_name=None,
        device_id="22#4#02",
        who="2",
        where="22",
        interface="02",
        advanced=False,
        manufacturer="BTicino",
        model="F411",
        gateway=gw,
    )
    button = CalibrateCoverButtonEntity(
        None,
        platform="button",
        device_id="22#4#02",
        where="22",
        interface="02",
        name="Rolluik Woonkamer",
        gateway=gw,
    )
    sensor = MyHOMETemperatureSensor(
        hass=None,
        name="Zone 1",
        device_id="4-1",
        who="4",
        where="1",
        device_class=SensorDeviceClass.TEMPERATURE,
        manufacturer="BTicino",
        model="Probe",
        gateway=gw,
    )

    # (v) Unregistered fallback to self._device_name (main entity own=None vs companion own name)
    assert cover._display_name == "Rolluik Woonkamer"
    assert button._display_name == "Rolluik Woonkamer Calibrate travel time"
    assert sensor._display_name == "Zone 1 Temperature"

    # Defensive: bare MagicMock registry/device entries without string fields still fall back safely
    cover.registry_entry = MagicMock()
    cover.device_entry = MagicMock()
    assert cover._display_name == "Rolluik Woonkamer"
    cover.registry_entry = None
    cover.device_entry = None

    # (iv) device_entry.name when name_by_user is None
    cover.device_entry = SimpleNamespace(id="dev-1", name="Registry Cover", name_by_user=None)
    button.device_entry = SimpleNamespace(id="dev-1", name="Registry Cover", name_by_user=None)
    sensor.device_entry = SimpleNamespace(id="dev-2", name="Registry Zone", name_by_user=None)
    assert cover._display_name == "Registry Cover"
    assert button._display_name == "Registry Cover Calibrate travel time"
    assert sensor._display_name == "Registry Zone Temperature"

    # (iii) device_entry.name_by_user overrides device_entry.name while preserving companion own name
    cover.device_entry = SimpleNamespace(id="dev-1", name="Registry Cover", name_by_user="Gordijn Woonkamer")
    button.device_entry = SimpleNamespace(id="dev-1", name="Registry Cover", name_by_user="Gordijn Woonkamer")
    sensor.device_entry = SimpleNamespace(id="dev-2", name="Registry Zone", name_by_user="Living Room")
    assert cover._display_name == "Gordijn Woonkamer"
    assert button._display_name == "Gordijn Woonkamer Calibrate travel time"
    assert sensor._display_name == "Living Room Temperature"

    # (ii) Mocked HA 2026.11+ registry_entry.compat_name returned directly when registry_entry.name is unset/empty
    cover.registry_entry = SimpleNamespace(name=None, compat_name="Legacy Woonkamer Gordijn")
    button.registry_entry = SimpleNamespace(name="", compat_name="Legacy Calibrate Button")
    assert cover._display_name == "Legacy Woonkamer Gordijn"
    assert button._display_name == "Legacy Calibrate Button"

    # (i) Non-empty registry_entry.name takes highest precedence over compat_name and device_entry
    cover.registry_entry = SimpleNamespace(name="Custom Curtain Entity", compat_name="Legacy Woonkamer Gordijn")
    button.registry_entry = SimpleNamespace(name="Custom Calibrate Action", compat_name="Legacy Calibrate Button")
    assert cover._display_name == "Custom Curtain Entity"
    assert button._display_name == "Custom Calibrate Action"

    # HA 2026.8-2026.10 registry entries without compat_name attribute fall through cleanly to device_entry
    cover.registry_entry = SimpleNamespace(name=None)
    assert cover._display_name == "Gordijn Woonkamer"


async def test_device_health_updates_name_on_startup_and_live_device_rename(hass: HomeAssistant) -> None:
    """DeviceHealth picks up name_by_user on startup and refreshes live on device registry renames."""
    entry = MockConfigEntry(domain=DOMAIN, data={"mac": MAC}, unique_id=MAC)
    entry.add_to_hass(hass)
    gw = _mock_gateway(hass=hass, entry=entry)
    health: DeviceHealth = gw.device_health

    dev_reg = dr.async_get(hass)
    device = dev_reg.async_get_or_create(
        config_entry_id=entry.entry_id,
        identifiers={(DOMAIN, f"{MAC}-1-74")},
        name="Rolluik Woonkamer",
    )
    device = dev_reg.async_update_device(device.id, name_by_user="Gordijn Woonkamer")
    assert device is not None

    other_device = dev_reg.async_get_or_create(
        config_entry_id=entry.entry_id,
        identifiers={(DOMAIN, f"{MAC}-1-75")},
        name="Other Device",
    )

    # Raise a fault before the entity attaches so DeviceHealth has an active repair issue to keep synced
    health.report(Fault(1, "74", FaultKind.UNMAPPED_STATUS, "99"))
    issue_id = fault_issue_id(entry.entry_id, FaultKind.UNMAPPED_STATUS, 1, "74")
    issue_reg = ir.async_get(hass)
    assert issue_reg.async_get_issue(DOMAIN, issue_id).translation_placeholders["device"] == "WHO 1 WHERE 74"

    light = MyHOMELight(
        hass=hass,
        name="Rolluik Woonkamer",
        entity_name=None,
        icon="mdi:lightbulb-off",
        icon_on="mdi:lightbulb-on",
        device_id="74",
        who="1",
        where="74",
        interface=None,
        dimmable=False,
        manufacturer="BTicino",
        model="F411",
        gateway=gw,
    )
    light.hass = hass
    light.entity_id = "light.rolluik_woonkamer"
    light.device_entry = device

    # Startup naming picks up device_entry.name_by_user
    await light.async_added_to_hass()
    assert health._names[(1, "74")] == "Gordijn Woonkamer"
    assert issue_reg.async_get_issue(DOMAIN, issue_id).translation_placeholders["device"] == "Gordijn Woonkamer"

    # Non-name update on the same device and rename on another device do not rewrite health name
    with patch.object(health, "name_address", wraps=health.name_address) as name_spy:
        dev_reg.async_update_device(device.id, sw_version="2.0.2")
        dev_reg.async_update_device(other_device.id, name_by_user="Unrelated Rename")
        await hass.async_block_till_done()
    name_spy.assert_not_called()
    assert issue_reg.async_get_issue(DOMAIN, issue_id).translation_placeholders["device"] == "Gordijn Woonkamer"

    # Live device rename updates DeviceHealth and active repair issue immediately
    dev_reg.async_update_device(device.id, name_by_user="Gordijn Oost")
    await hass.async_block_till_done()
    assert health._names[(1, "74")] == "Gordijn Oost"
    assert issue_reg.async_get_issue(DOMAIN, issue_id).translation_placeholders["device"] == "Gordijn Oost"

    # Clearing name_by_user falls back live to device_entry.name
    dev_reg.async_update_device(device.id, name_by_user=None)
    await hass.async_block_till_done()
    assert health._names[(1, "74")] == "Rolluik Woonkamer"
    assert issue_reg.async_get_issue(DOMAIN, issue_id).translation_placeholders["device"] == "Rolluik Woonkamer"

    # Running async_on_remove cleanup detaches the device registry listener
    assert light._on_remove
    for unsub in list(light._on_remove):
        unsub()
    dev_reg.async_update_device(device.id, name_by_user="Renamed After Remove")
    await hass.async_block_till_done()
    assert health._names[(1, "74")] == "Rolluik Woonkamer"


async def test_device_rename_listener_is_defensive_with_mocks() -> None:
    """async_added_to_hass tolerates missing or mocked hass, bus, and device_entry."""
    gw = _mock_gateway(hass=None)
    gw.device_health = DeviceHealth(gw)

    # Case 1: hass=None, device_entry=SimpleNamespace(...)
    entity_no_hass = MyHOMEEntity(
        hass=None,
        name="Fallback Device",
        platform="light",
        device_id="74",
        who="1",
        where="74",
        manufacturer="BTicino",
        model="F411",
        gateway=gw,
    )
    entity_no_hass._poll_on_add = False
    entity_no_hass.device_entry = SimpleNamespace(id="dev-no-hass", name="Reg Name", name_by_user=None)
    await entity_no_hass.async_added_to_hass()
    assert gw.device_health._names[(1, "74")] == "Reg Name"

    # Case 2: SimpleNamespace hass without a bus + MagicMock device_entry
    entity_stub_hass = MyHOMEEntity(
        hass=SimpleNamespace(bus=None, data={}),  # type: ignore[arg-type]
        name="Stub Device",
        platform="light",
        device_id="75",
        who="1",
        where="75",
        manufacturer="BTicino",
        model="F411",
        gateway=gw,
    )
    entity_stub_hass._poll_on_add = False
    entity_stub_hass.device_entry = MagicMock()
    await entity_stub_hass.async_added_to_hass()
    assert gw.device_health._names[(1, "75")] == "Stub Device"

    # Case 3: Direct listener registration guard with stub bus=None or non-callable async_listen
    entity_stub_hass.device_entry = SimpleNamespace(id="dev-stub", name="Stub Reg", name_by_user=None)
    entity_stub_hass.hass = SimpleNamespace(bus=None)  # type: ignore[assignment]
    entity_stub_hass._register_device_rename_listener("Stub Reg")
    entity_stub_hass.hass = SimpleNamespace(bus=SimpleNamespace(async_listen=None))  # type: ignore[assignment]
    entity_stub_hass._register_device_rename_listener("Stub Reg")
