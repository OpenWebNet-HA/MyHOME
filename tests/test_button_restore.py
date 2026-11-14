"""Regression tests for lock/unlock buttons after discovery and restart."""

import logging
from datetime import timedelta
from unittest.mock import AsyncMock, MagicMock

import pytest
from homeassistant.const import STATE_UNAVAILABLE
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.dispatcher import async_dispatcher_send
from homeassistant.helpers.entity_platform import EntityPlatform
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.myhome import button
from custom_components.myhome.const import CONF_BUS_INTERFACE, DOMAIN
from tests.conftest import attach_runtime

MAC = "00:03:50:81:17:76"


def _lock_unlock(entities):
    """The lock / unlock buttons only: the cover calibration buttons are not under test here."""
    return [e for e in entities if e.unique_id.endswith(("-disable", "-enable"))]


@pytest.fixture
def unload_callbacks(monkeypatch):
    """Exercise registered cleanup callbacks without a private ConfigEntry API."""
    callbacks = []
    monkeypatch.setattr(
        MockConfigEntry, "async_on_unload", lambda self, callback: callbacks.append(callback)
    )

    def cleanup():
        while callbacks:
            callbacks.pop()()

    yield cleanup
    cleanup()


@pytest.mark.parametrize(
    ("domain", "who", "address"),
    [("light", "1", "01"), ("switch", "1", "0015"),
     ("cover", "2", "01"), ("cover", "2", "01#4#02")],
)
async def test_registered_actuator_buttons_survive_reload(hass, domain, who, address, unload_callbacks):
    """Restore the same HA entities without requiring a new discovery event."""
    entry = MockConfigEntry(domain=DOMAIN, data={"mac": MAC})
    entry.add_to_hass(hass)
    registry = er.async_get(hass)
    devices = dr.async_get(hass)
    device = devices.async_get_or_create(
        config_entry_id=entry.entry_id,
        identifiers={(DOMAIN, f"{MAC}-{who}-{address}")},
        name="Actuator", manufacturer="Legrand", model="Existing model",
    )
    registry.async_get_or_create(
        domain, DOMAIN, f"{MAC}-{who}-{address}",
        config_entry=entry, original_name="Actuator", device_id=device.id,
    )
    expected_ids = []
    for suffix in ("disable", "enable"):
        registered = registry.async_get_or_create(
            "button", DOMAIN, f"{MAC}-{who}-{address}-{suffix}",
            config_entry=entry, suggested_object_id=f"custom_{suffix}", device_id=device.id,
        )
        expected_ids.append(registered.entity_id)

    gateway = MagicMock(
        mac=MAC, unique_id=MAC, available=True,
        availability_signal=f"myhome_{MAC}_availability", device_registry_id=None,
    )
    gateway.is_who_available.side_effect = lambda who: gateway.available
    for _ in range(2):
        # Only the entity registry survives a fresh setup: no YAML/discovery cache.
        hass.data[DOMAIN] = {MAC: {"entity": gateway, "platforms": {"button": {}}}}
        attach_runtime(hass, entry)
        platform = EntityPlatform(
            hass=hass, logger=logging.getLogger(__name__), domain="button",
            platform_name=DOMAIN, platform=button, scan_interval=timedelta(seconds=30),
            entity_namespace=None,
        )
        assert await platform.async_setup_entry(entry)
        await hass.async_block_till_done()
        try:
            assert {e.entity_id for e in _lock_unlock(platform.entities.values())} == set(expected_ids)
            assert devices.async_get(device.id).model == "Existing model"
            assert devices.async_get(device.id).manufacturer == "Legrand"
            for entity_id in expected_ids:
                state = hass.states.get(entity_id)
                assert state is not None
                assert state.state != STATE_UNAVAILABLE
                assert not state.attributes.get("restored")
                assert registry.async_get(entity_id).device_id == device.id

            where, _, interface = address.partition("#4#")
            async_dispatcher_send(hass, f"myhome_new_device_{MAC}", {
                "who": who, "where": where, "interface": interface or None,
                "name": "Actuator", "device_id": address,
            })
            await hass.async_block_till_done()
            assert {e.entity_id for e in _lock_unlock(platform.entities.values())} == set(expected_ids)

            gateway.available = False
            async_dispatcher_send(hass, gateway.availability_signal)
            await hass.async_block_till_done()
            assert all(hass.states.get(e).state == STATE_UNAVAILABLE for e in expected_ids)
            gateway.available = True
            async_dispatcher_send(hass, gateway.availability_signal)
            await hass.async_block_till_done()
            assert all(hass.states.get(e).state != STATE_UNAVAILABLE for e in expected_ids)
        finally:
            await platform.async_reset()
            unload_callbacks()


async def test_restore_filters_and_deduplicates_actuators(hass, unload_callbacks):
    """Keep gateway/WHO/address boundaries and ignore unrelated or deleted devices."""
    entry = MockConfigEntry(domain=DOMAIN, data={"mac": MAC})
    entry.add_to_hass(hass)
    other_entry = MockConfigEntry(domain=DOMAIN, data={"mac": "00:03:50:00:00:02"})
    other_entry.add_to_hass(hass)
    registry = er.async_get(hass)
    for domain, unique_id, config in [
        ("light", f"{MAC}-1-01", entry),
        ("cover", f"{MAC}-2-01", entry),
        ("sensor", f"{MAC}-1-12", entry),
        ("light", f"{MAC}-1-#1", entry),
        ("light", "foreign-1-99", entry),
        ("cover", f"{MAC}-1-42", entry),
        ("light", f"{MAC}-1-31", other_entry),
        ("button", f"{MAC}-1-88-disable", entry),
    ]:
        registry.async_get_or_create(domain, DOMAIN, unique_id, config_entry=config)
    gateway = MagicMock(mac=MAC, unique_id=MAC)
    hass.data[DOMAIN] = {MAC: {
        "entity": gateway,
        "platforms": {"button": {
            "configured": {"who": "1", "where": "01", "name": "Configured light"},
        }},
    }}
    attach_runtime(hass, entry)
    added = []
    await button.async_setup_entry(hass, entry, added.extend)
    try:
        lock_unlock = _lock_unlock(added)
        assert {e.unique_id for e in lock_unlock} == {
            f"{MAC}-{who}-01-{suffix}"
            for who in ("1", "2") for suffix in ("disable", "enable")
        }
        assert len(lock_unlock) == 4
        assert lock_unlock[0]._display_name == "Configured light Lock"
    finally:
        unload_callbacks()


@pytest.mark.parametrize("reverse", [False, True])
@pytest.mark.parametrize("configured_sensor", [False, True])
async def test_restore_before_parent_cleanup(hass, unload_callbacks, reverse, configured_sensor):
    """Ignore corrupt/ghost parents regardless of registry and platform setup order."""
    entry = MockConfigEntry(domain=DOMAIN, data={"mac": MAC})
    entry.add_to_hass(hass)
    registry = er.async_get(hass)
    parents = [
        ("switch", "1-1-06"),  # Old corrupted identity; never send WHERE=1-06.
        ("switch", "1-06"),
        ("light", "1-06"),  # Ghost of the switch above.
        ("light", "1-12#4#02"),  # Ghost of a WHO 1 sensor.
        ("light", "1-12#4#03"),  # Same WHERE on another bus is a real actuator.
        ("light", "1-0015"),  # Distinct from the legacy sensor at WHERE=15.
        ("light", "1-15"),
        ("binary_sensor", "15-motion"),
        ("light", "1-21"),  # WHO 18 telemetry does not claim WHO 1.
        ("sensor", "18-21-power"),
        ("cover", "2-12#4#02"),  # WHO 2 remains independent of WHO 1.
    ]
    if not configured_sensor:
        parents.append(("sensor", "1-12#4#02-illuminance"))
    for domain, suffix in reversed(parents) if reverse else parents:
        registry.async_get_or_create(
            domain, DOMAIN, f"{MAC}-{suffix}", config_entry=entry,
            original_name=f"{domain} {suffix}",
        )
    gateway = MagicMock(mac=MAC, unique_id=MAC, send=AsyncMock())
    platforms = {"button": {}}
    if configured_sensor:
        platforms["binary_sensor"] = {
            "pir": {"who": "1", "where": "12", CONF_BUS_INTERFACE: "02"},
        }
    hass.data[DOMAIN] = {MAC: {"entity": gateway, "platforms": platforms}}
    attach_runtime(hass, entry)
    added = []
    await button.async_setup_entry(hass, entry, added.extend)
    expected = {"1-06", "1-12#4#03", "1-0015", "1-21", "2-12#4#02"}
    lock_unlock = _lock_unlock(added)
    assert {e.unique_id for e in lock_unlock} == {
        f"{MAC}-{address}-{suffix}"
        for address in expected for suffix in ("disable", "enable")
    }
    assert len(lock_unlock) == 2 * len(expected)
    for entity in lock_unlock:
        await entity.async_press()
    assert {str(call.args[0]) for call in gateway.send.await_args_list} == {
        f"*14*{command}*{address.split('-', 1)[1]}##"
        for address in expected for command in ("0", "1")
    }
    # No parent platform ran: restoration must not depend on its cleanup.
    assert registry.async_get_entity_id("switch", DOMAIN, f"{MAC}-1-1-06")


async def test_restore_accepts_configured_mac_prefix(hass, unload_callbacks):
    """Allow the config entry MAC spelling as well as the normalized gateway MAC."""
    raw_mac = MAC.replace(":", "")
    entry = MockConfigEntry(domain=DOMAIN, data={"mac": raw_mac})
    entry.add_to_hass(hass)
    registry = er.async_get(hass)
    for prefix, where in ((raw_mac, "01"), (raw_mac, "02"), (MAC, "02")):
        registry.async_get_or_create(
            "light", DOMAIN, f"{prefix}-1-{where}", config_entry=entry,
        )
    gateway = MagicMock(mac=MAC, unique_id=MAC)
    hass.data[DOMAIN] = {raw_mac: {"entity": gateway, "platforms": {"button": {}}}}
    attach_runtime(hass, entry)
    added = []
    await button.async_setup_entry(hass, entry, added.extend)
    assert [e.unique_id for e in _lock_unlock(added)] == [
        f"{MAC}-1-{where}-{suffix}"
        for where in ("01", "02") for suffix in ("disable", "enable")
    ]
