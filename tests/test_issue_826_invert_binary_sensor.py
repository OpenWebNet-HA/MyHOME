"""Tests for UI-configurable binary sensor state inversion (GitHub issue #826)."""

from __future__ import annotations

from unittest.mock import patch

import pytest
from homeassistant.components.binary_sensor import BinarySensorDeviceClass
from homeassistant.const import (
    CONF_HOST,
    CONF_MAC,
    CONF_PASSWORD,
    CONF_PORT,
    EntityCategory,
)
from homeassistant.core import HomeAssistant, State
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.dispatcher import async_dispatcher_send
from OWNd.message import OWNAuxEvent, OWNEvent
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    mock_restore_cache,
)

from custom_components.myhome.binary_sensor import (
    binary_sensor_canonical_where,
    binary_sensor_option_key,
    resolve_binary_sensor_inverted,
)
from custom_components.myhome.const import (
    CONF_IGNORED_ADDRESSES,
    CONF_INVERTED_BINARY_SENSORS,
    DOMAIN,
    SIGNAL_NEW_BINARY_SENSOR,
)

MAC = "00:03:50:00:08:26"


def _gateway_patches() -> list[object]:
    return [
        patch(
            "custom_components.myhome.gateway.OWNSession.test_connection",
            return_value={"Success": True, "Message": None},
        ),
        patch("custom_components.myhome.gateway.MyHOMEGatewayHandler.listening_loop"),
        patch("custom_components.myhome.gateway.MyHOMEGatewayHandler.sending_loop"),
        patch("custom_components.myhome.gateway.MyHOMEGatewayHandler.send"),
        patch(
            "custom_components.myhome.gateway.MyHOMEGatewayHandler.send_status_request"
        ),
    ]


async def _setup_entry(hass: HomeAssistant, entry: MockConfigEntry) -> None:
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    entry.runtime_data.gateway._on_event_connection_state_change(True)
    await hass.async_block_till_done()


async def _reload_entry(hass: HomeAssistant, entry: MockConfigEntry) -> None:
    assert await hass.config_entries.async_reload(entry.entry_id)
    entry.runtime_data.gateway._on_event_connection_state_change(True)
    await hass.async_block_till_done()


@pytest.fixture
def patched_gateway():
    """Patch network loops and command sending on MyHOMEGatewayHandler."""
    patches = _gateway_patches()
    mocks = [p.start() for p in patches]  # type: ignore[attr-defined]
    try:
        yield mocks
    finally:
        for p in patches:
            p.stop()  # type: ignore[attr-defined]


async def test_dynamic_who25_dry_contact_ui_invert_live_and_persisted(
    hass: HomeAssistant, patched_gateway
) -> None:
    """Discover a WHO=25 contact, invert it from UI without reload, and persist across reload."""
    send_status_mock = patched_gateway[4]
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={
            CONF_HOST: "192.168.1.50",
            CONF_PORT: 20000,
            CONF_PASSWORD: "12345",
            CONF_MAC: MAC,
            "name": "MH202",
        },
        options={},
        unique_id=MAC,
    )
    await _setup_entry(hass, entry)

    # 1. Dynamic WHO=25 discovery (*25*31#1*42## -> NC contact closed at rest)
    async_dispatcher_send(hass, f"myhome_message_{MAC}", OWNEvent.parse("*25*31#1*42##"))
    await hass.async_block_till_done()

    bs_id = "binary_sensor.dry_contact_42_opening"
    sw_id = "switch.dry_contact_42_invert_state"

    bs_state = hass.states.get(bs_id)
    sw_state = hass.states.get(sw_id)
    assert bs_state is not None and bs_state.state == "on"
    assert sw_state is not None and sw_state.state == "off"

    # Both entities share the exact same DeviceEntry and proper EntityCategory
    ent_reg = er.async_get(hass)
    bs_entry = ent_reg.async_get(bs_id)
    sw_entry = ent_reg.async_get(sw_id)
    assert bs_entry is not None and sw_entry is not None
    assert bs_entry.device_id == sw_entry.device_id
    assert sw_entry.entity_category == EntityCategory.CONFIG
    assert sw_entry.original_device_class is None

    # 2. WHO=1 PIR motion sensor does NOT create an Invert state switch
    async_dispatcher_send(hass, f"myhome_message_{MAC}", OWNEvent.parse("*1*34*15##"))
    await hass.async_block_till_done()
    assert hass.states.get("binary_sensor.motion_sensor_15_motion") is not None
    assert hass.states.get("switch.motion_sensor_15_invert_state") is None

    # 3. Toggle Invert state ON -> flips live state immediately with zero bus polls
    send_status_mock.reset_mock()
    await hass.services.async_call(
        "switch", "turn_on", {"entity_id": sw_id}, blocking=True
    )
    await hass.async_block_till_done()

    assert send_status_mock.call_count == 0
    assert hass.states.get(sw_id).state == "on"
    assert hass.states.get(bs_id).state == "off"
    assert entry.options[CONF_INVERTED_BINARY_SENSORS] == {"25-42": True}

    # 4. Subsequent NC contact events are inverted in real time
    # Loop opens (*25*32#1*42##, raw False) -> reported "on" (motion/opening detected)
    async_dispatcher_send(hass, f"myhome_message_{MAC}", OWNEvent.parse("*25*32#1*42##"))
    await hass.async_block_till_done()
    assert hass.states.get(bs_id).state == "on"

    # Loop closes again (*25*31#1*42##, raw True) -> reported "off" (clear)
    async_dispatcher_send(hass, f"myhome_message_{MAC}", OWNEvent.parse("*25*31#1*42##"))
    await hass.async_block_till_done()
    assert hass.states.get(bs_id).state == "off"

    # 5. Toggle Invert state OFF -> immediately flips live back to "on"
    await hass.services.async_call(
        "switch", "turn_off", {"entity_id": sw_id}, blocking=True
    )
    await hass.async_block_till_done()
    assert hass.states.get(sw_id).state == "off"
    assert hass.states.get(bs_id).state == "on"
    assert entry.options[CONF_INVERTED_BINARY_SENSORS] == {"25-42": False}

    # 6. Turn Invert state back ON and reload config entry -> persists across reload
    await hass.services.async_call(
        "switch", "turn_on", {"entity_id": sw_id}, blocking=True
    )
    await hass.async_block_till_done()
    assert hass.states.get(bs_id).state == "off"

    await _reload_entry(hass, entry)

    assert hass.states.get(sw_id).state == "on"
    bs_entry_after = ent_reg.async_get(bs_id)
    sw_entry_after = ent_reg.async_get(sw_id)
    assert bs_entry_after is not None and sw_entry_after is not None
    assert bs_entry_after.device_id == sw_entry_after.device_id


async def test_state_restoration_reconstructs_raw_polarity_for_live_flip(
    hass: HomeAssistant, patched_gateway
) -> None:
    """Restored state reconstructs _raw_is_on so toggling Invert state flips without a bus frame."""
    bs_id = "binary_sensor.dry_contact_42_opening"
    sw_id = "switch.dry_contact_42_invert_state"

    mock_restore_cache(hass, [State(bs_id, "off")])

    entry = MockConfigEntry(
        domain=DOMAIN,
        data={
            CONF_HOST: "192.168.1.50",
            CONF_PORT: 20000,
            CONF_PASSWORD: "12345",
            CONF_MAC: MAC,
            "name": "MH202",
        },
        options={CONF_INVERTED_BINARY_SENSORS: {"25-42": True}},
        unique_id=MAC,
    )
    entry.add_to_hass(hass)

    ent_reg = er.async_get(hass)
    ent_reg.async_get_or_create(
        domain="binary_sensor",
        platform=DOMAIN,
        unique_id=f"{MAC}-25-42-opening",
        config_entry=entry,
        suggested_object_id="dry_contact_42_opening",
        original_device_class=BinarySensorDeviceClass.OPENING,
    )

    assert await hass.config_entries.async_setup(entry.entry_id)
    entry.runtime_data.gateway._on_event_connection_state_change(True)
    await hass.async_block_till_done()

    # Restored "off" while inverted=True -> underlying raw contact is True (closed)
    assert hass.states.get(bs_id).state == "off"
    assert hass.states.get(sw_id).state == "on"

    # Toggle Invert state OFF before any bus event arrives -> immediately flips to "on"
    await hass.services.async_call(
        "switch", "turn_off", {"entity_id": sw_id}, blocking=True
    )
    await hass.async_block_till_done()
    assert hass.states.get(bs_id).state == "on"


async def test_yaml_inverted_default_ui_override_and_who9_auxiliary(
    hass: HomeAssistant, patched_gateway, tmp_path
) -> None:
    """YAML inverted: true initializes UI switch, UI False overrides YAML, and WHO=9 preserves channel."""
    yaml_file = tmp_path / "myhome.yaml"
    yaml_file.write_text(
        f"""
{MAC}:
  binary_sensor:
    nc_pir:
      who: "25"
      where: "31"
      name: "Hallway NC PIR"
      class: motion
      inverted: true
    aux_tamper:
      who: "9"
      where: "1"
      name: "Alarm Aux 1"
      class: safety
      inverted: false
    bus_pir:
      who: "1"
      where: "14"
      name: "Native SCS PIR"
      class: motion
      inverted: true
""",
        encoding="utf-8",
    )

    entry = MockConfigEntry(
        domain=DOMAIN,
        data={
            CONF_HOST: "192.168.1.50",
            CONF_PORT: 20000,
            CONF_PASSWORD: "12345",
            CONF_MAC: MAC,
            "name": "F454",
        },
        options={"file_path": str(yaml_file)},
        unique_id=MAC,
    )
    await _setup_entry(hass, entry)

    pir_sw = "switch.hallway_nc_pir_invert_state"
    aux_sw = "switch.alarm_aux_1_invert_state"
    aux_bs = "binary_sensor.alarm_aux_1_safety"

    # Seeded from YAML inverted: true / false; WHO=1 has no invert switch
    assert hass.states.get(pir_sw).state == "on"
    assert hass.states.get(aux_sw).state == "off"
    assert hass.states.get("switch.native_scs_pir_invert_state") is None

    # Turn OFF Hallway NC PIR invert switch -> explicit False overrides YAML inverted: true across reload
    await hass.services.async_call(
        "switch", "turn_off", {"entity_id": pir_sw}, blocking=True
    )
    await hass.async_block_till_done()
    assert entry.options[CONF_INVERTED_BINARY_SENSORS]["25-31"] is False

    # Turn ON WHO=9 Auxiliary invert switch and send *9*1*1## (ON) -> inverted to "off"
    await hass.services.async_call(
        "switch", "turn_on", {"entity_id": aux_sw}, blocking=True
    )
    await hass.async_block_till_done()
    assert entry.options[CONF_INVERTED_BINARY_SENSORS]["9-1"] is True

    aux_msg = OWNEvent.parse("*9*1*1##")
    assert isinstance(aux_msg, OWNAuxEvent)
    async_dispatcher_send(hass, f"myhome_message_{MAC}", aux_msg)
    await hass.async_block_till_done()
    assert hass.states.get(aux_bs).state == "off"

    # Reload (exercises registry restoration for both WHO=25 with config and WHO=9)
    await _reload_entry(hass, entry)

    assert hass.states.get(pir_sw).state == "off"
    assert hass.states.get(aux_sw).state == "on"


async def test_invert_switch_rejects_timed_turn_on_and_resync_ignores_it(
    hass: HomeAssistant, patched_gateway
) -> None:
    """MyHOMEInvertSwitch rejects WHO=1 timers and is excluded from WHO=1 lighting area resyncs."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={
            CONF_HOST: "192.168.1.50",
            CONF_PORT: 20000,
            CONF_PASSWORD: "12345",
            CONF_MAC: MAC,
            "name": "MH202",
        },
        options={},
        unique_id=MAC,
    )
    await _setup_entry(hass, entry)

    # Address 31 looks like Lighting Area 3 PL 1 if WHO=25 were not filtered out
    async_dispatcher_send(hass, f"myhome_message_{MAC}", OWNEvent.parse("*25*31#1*31##"))
    await hass.async_block_till_done()

    sw_id = "switch.dry_contact_31_invert_state"
    assert hass.states.get(sw_id) is not None

    # 1. LightingResyncManager must NOT treat WHO=25 invert switch as a WHO=1 lighting area
    gateway = entry.runtime_data.gateway
    assert gateway._resync_manager.known_light_areas() == []

    # 2. Calling async_turn_on_timed or async_turn_on(duration=...) raises HomeAssistantError
    entity_comp = hass.data["entity_components"]["switch"]
    inv_entity = entity_comp.get_entity(sw_id)
    assert inv_entity is not None
    with pytest.raises(HomeAssistantError) as exc_info:
        await inv_entity.async_turn_on_timed(duration=5)
    assert exc_info.value.translation_key == "invert_switch_no_timer"

    with pytest.raises(HomeAssistantError) as exc_info2:
        await inv_entity.async_turn_on(duration=5)
    assert exc_info2.value.translation_key == "invert_switch_no_timer"


async def test_orphan_and_ignored_invert_switch_pruning_and_helpers(
    hass: HomeAssistant, patched_gateway, tmp_path
) -> None:
    """Pruning an ignored binary sensor or orphaned -invert switch cleans up switch and empty device."""
    # Check canonical helper preserves WHO=9 zero-padded strings while normalizing WHO=25
    assert binary_sensor_canonical_where("9", "9-01") == "01"
    assert binary_sensor_canonical_where("25", "25-0031") == "31"
    assert binary_sensor_canonical_where("25", "31#4#01") == "31#4#01"
    assert binary_sensor_option_key("9", "01") == "9-01"
    assert resolve_binary_sensor_inverted(None, "25", "31", {"inverted": True}) is True

    yaml_file = tmp_path / "myhome.yaml"
    yaml_file.write_text(
        f"""
{MAC}:
  binary_sensor:
    ignored_contact:
      who: "25"
      where: "55"
      name: "Ignored Contact"
      class: door
""",
        encoding="utf-8",
    )

    entry = MockConfigEntry(
        domain=DOMAIN,
        data={
            CONF_HOST: "192.168.1.50",
            CONF_PORT: 20000,
            CONF_PASSWORD: "12345",
            CONF_MAC: MAC,
            "name": "MH202",
        },
        options={
            "file_path": str(yaml_file),
            CONF_IGNORED_ADDRESSES: ["25:55"],
        },
        unique_id=MAC,
    )
    entry.add_to_hass(hass)

    ent_reg = er.async_get(hass)
    dev_reg = dr.async_get(hass)

    # Pre-seed both:
    # (a) an ignored WHO=25 binary sensor + companion -invert switch sharing a device
    ignored_dev = dev_reg.async_get_or_create(
        config_entry_id=entry.entry_id,
        identifiers={(DOMAIN, f"{MAC}-25-44")},
        name="Contact 44",
    )
    ent_reg.async_get_or_create(
        domain="binary_sensor",
        platform=DOMAIN,
        unique_id=f"{MAC}-25-44-opening",
        config_entry=entry,
        device_id=ignored_dev.id,
        original_device_class=BinarySensorDeviceClass.OPENING,
    )
    ent_reg.async_get_or_create(
        domain="switch",
        platform=DOMAIN,
        unique_id=f"{MAC}-25-44-invert",
        config_entry=entry,
        device_id=ignored_dev.id,
    )

    # (b) a standalone orphaned -invert switch whose binary_sensor was already deleted
    orphan_dev = dev_reg.async_get_or_create(
        config_entry_id=entry.entry_id,
        identifiers={(DOMAIN, f"{MAC}-25-99")},
        name="Deleted Contact 99",
    )
    ent_reg.async_get_or_create(
        domain="switch",
        platform=DOMAIN,
        unique_id=f"{MAC}-25-99-invert",
        config_entry=entry,
        device_id=orphan_dev.id,
    )

    # (c) a registered WHO=9 auxiliary without YAML config (restores default name/model)
    ent_reg.async_get_or_create(
        domain="binary_sensor",
        platform=DOMAIN,
        unique_id=f"{MAC}-9-7-door",
        config_entry=entry,
        original_device_class=BinarySensorDeviceClass.DOOR,
    )

    # Also ignore 25:44 so both (a) and YAML 25:55 are skipped/pruned
    hass.config_entries.async_update_entry(
        entry,
        options={
            "file_path": str(yaml_file),
            CONF_IGNORED_ADDRESSES: ["25:44", "25:55"],
        },
    )

    assert await hass.config_entries.async_setup(entry.entry_id)
    entry.runtime_data.gateway._on_event_connection_state_change(True)
    await hass.async_block_till_done()

    # Ignored YAML contact 55 never created an invert switch
    assert hass.states.get("switch.ignored_contact_invert_state") is None

    # Pre-seeded ignored 44 and orphaned 99 switches and empty devices were pruned
    assert ent_reg.async_get_entity_id("switch", DOMAIN, f"{MAC}-25-44-invert") is None
    assert ent_reg.async_get_entity_id("switch", DOMAIN, f"{MAC}-25-99-invert") is None
    assert dev_reg.async_get(ignored_dev.id) is None
    assert dev_reg.async_get(orphan_dev.id) is None

    # Registered WHO=9 channel 7 without YAML restored its companion switch cleanly
    assert hass.states.get("switch.auxiliary_channel_7_invert_state") is not None

    # Direct prune_entity on a binary_sensor removes its companion -invert switch and empty device
    dup_dev = dev_reg.async_get_or_create(
        config_entry_id=entry.entry_id,
        identifiers={(DOMAIN, f"{MAC}-25-88")},
        name="Duplicate Contact 88",
    )
    dup_bs = ent_reg.async_get_or_create(
        domain="binary_sensor",
        platform=DOMAIN,
        unique_id=f"{MAC}-25-88-opening",
        config_entry=entry,
        device_id=dup_dev.id,
        original_device_class=BinarySensorDeviceClass.OPENING,
    )
    ent_reg.async_get_or_create(
        domain="switch",
        platform=DOMAIN,
        unique_id=f"{MAC}-25-88-invert",
        config_entry=entry,
        device_id=dup_dev.id,
    )
    from custom_components.myhome.discovery import prune_entity

    prune_entity(hass, ent_reg, dup_bs, entry.entry_id)
    assert ent_reg.async_get_entity_id("switch", DOMAIN, f"{MAC}-25-88-invert") is None
    assert dev_reg.async_get(dup_dev.id) is None

    # Sending an unrelated WHO in SIGNAL_NEW_BINARY_SENSOR is ignored cleanly
    gw_mac = str(entry.runtime_data.gateway.mac)
    async_dispatcher_send(
        hass,
        SIGNAL_NEW_BINARY_SENSOR.format(gw_mac),
        {"who": "1", "where": "12", "device_id": "12", "name": "Motion 12"},
    )
    await hass.async_block_till_done()
