"""Tests for issue #368 PR B: declared groups as an assumed-state light."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from homeassistant.components.light import ATTR_BRIGHTNESS
from homeassistant.const import CONF_NAME, STATE_ON, STATE_UNAVAILABLE
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import entity_registry as er
from OWNd.message import OWNMessage
from voluptuous.error import Invalid

from custom_components.myhome.const import CONF_WHERE, DOMAIN
from custom_components.myhome.light import async_setup_entry
from custom_components.myhome.light_group import MyHOMELightGroup
from custom_components.myhome.validate import config_schema, light_schema
from tests.conftest import attach_runtime

MAC = "00:03:50:00:03:68"


def _gateway() -> MagicMock:
    gateway = MagicMock()
    gateway.mac = MAC
    gateway.log_id = "GATEWAY"
    gateway.device_registry_id = None
    gateway.send = AsyncMock()
    gateway.send_status_request = AsyncMock()
    return gateway


def _group(hass: HomeAssistant, gateway: MagicMock, *, members: list[str] | None = None, entity_id: str = "light.group_6", **flags) -> MyHOMELightGroup:
    entity = MyHOMELightGroup(
        hass,
        "Group 6",
        "dev1",
        6,
        gateway,
        members or [],
        dimmable=flags.get("dimmable", True),
        color_temp=flags.get("color_temp", True),
        rgb=flags.get("rgb", True),
        hs=flags.get("hs", False),
        icon=flags.get("icon"),
        icon_on=flags.get("icon_on"),
    )
    entity.hass = hass
    entity.entity_id = entity_id
    entity.async_schedule_update_ha_state = MagicMock()
    return entity


def _fire(group: MyHOMELightGroup, frame: str) -> None:
    group.handle_event(OWNMessage.parse(frame))


# ── schema ──────────────────────────────────────────────────────────────


def test_schema_validates_group():
    """The schema validates a group with members and rekeys it like any light."""
    data = {
        "light_1": {
            CONF_WHERE: "#6",
            CONF_NAME: "Group 6",
            "members": ["11", "12"],
        }
    }
    validated = light_schema(data)
    assert "1-#6" in validated
    assert validated["1-#6"]["members"] == ["11", "12"]


def test_schema_rejects_members_on_point_to_point():
    """Members are only meaningful on a group WHERE."""
    data = {
        "light_1": {
            CONF_WHERE: "11",
            CONF_NAME: "Light 11",
            "members": ["12"],
        }
    }
    with pytest.raises(Invalid):
        light_schema(data)


def test_schema_rejects_invalid_members():
    """A member must itself be a point-to-point WHERE."""
    data = {
        "light_1": {
            CONF_WHERE: "#6",
            CONF_NAME: "Group 6",
            "members": ["#7"],
        }
    }
    with pytest.raises(Invalid):
        light_schema(data)


# ── build() from yaml / registry ───────────────────────────────────────


async def test_build_group_from_yaml(hass: HomeAssistant):
    """A `where: '#6'` yaml light becomes a MyHOMELightGroup, not a broken point light."""
    from homeassistant.util.yaml.loader import parse_yaml

    validated = config_schema(parse_yaml(
        f"""
{MAC}:
  light:
    group_6:
      where: '#6'
      name: Kitchen Group
      dimmable: true
"""
    ))
    lights = validated[MAC]["platforms"]["light"]

    config_entry = MagicMock()
    config_entry.data = {"mac": MAC}
    config_entry.entry_id = "test_entry"
    gateway = _gateway()
    hass.data = {DOMAIN: {MAC: {"entity": gateway, "platforms": {"light": lights}}}}

    with (
        patch("custom_components.myhome.discovery.er.async_entries_for_config_entry", return_value=[]),
        patch("custom_components.myhome.discovery.er.async_get", return_value=MagicMock()),
    ):
        async_add_entities = MagicMock()
        attach_runtime(hass, config_entry)
        await async_setup_entry(hass, config_entry, async_add_entities)

    async_add_entities.assert_called_once()
    entities = list(async_add_entities.call_args[0][0])
    assert len(entities) == 1
    group = entities[0]
    assert isinstance(group, MyHOMELightGroup)
    assert group.unique_id == f"{MAC}-1-#6"


async def test_restore_group_from_registry(hass: HomeAssistant):
    """A registry entry for a group unique id restores a MyHOMELightGroup."""
    config_entry = MagicMock()
    config_entry.data = {"mac": MAC}
    config_entry.entry_id = "test_entry"
    gateway = _gateway()
    hass.data = {DOMAIN: {MAC: {"entity": gateway, "platforms": {"light": {}}}}}

    mock_entry = MagicMock()
    mock_entry.domain = "light"
    mock_entry.unique_id = f"{MAC}-1-#6"

    with (
        patch("custom_components.myhome.discovery.er.async_entries_for_config_entry", return_value=[mock_entry]),
        patch("custom_components.myhome.discovery.er.async_get", return_value=MagicMock()),
    ):
        async_add_entities = MagicMock()
        attach_runtime(hass, config_entry)
        await async_setup_entry(hass, config_entry, async_add_entities)

    entities = list(async_add_entities.call_args[0][0])
    assert len(entities) == 1
    assert isinstance(entities[0], MyHOMELightGroup)


async def test_build_refuses_area_and_general_where(hass: HomeAssistant, caplog):
    """Area/general yaml WHEREs never make a light entity (#368 point 3)."""
    from homeassistant.util.yaml.loader import parse_yaml

    validated = config_schema(parse_yaml(
        f"""
{MAC}:
  light:
    area_1:
      where: '1'
      name: Area Light
"""
    ))
    lights = validated[MAC]["platforms"]["light"]

    config_entry = MagicMock()
    config_entry.data = {"mac": MAC}
    config_entry.entry_id = "test_entry"
    gateway = _gateway()
    hass.data = {DOMAIN: {MAC: {"entity": gateway, "platforms": {"light": lights}}}}

    with (
        patch("custom_components.myhome.discovery.er.async_entries_for_config_entry", return_value=[]),
        patch("custom_components.myhome.discovery.er.async_get", return_value=MagicMock()),
    ):
        async_add_entities = MagicMock()
        attach_runtime(hass, config_entry)
        await async_setup_entry(hass, config_entry, async_add_entities)

    async_add_entities.assert_not_called()
    assert "Refusing to create a light entity for broadcast WHERE" in caplog.text


# ── assumed-state (no members) ─────────────────────────────────────────


async def test_assumed_state_without_members(hass: HomeAssistant):
    gateway = _gateway()
    group = _group(hass, gateway)
    await group.async_added_to_hass()

    assert group.assumed_state is True
    assert group.is_on is None


async def test_turn_on_plain(hass: HomeAssistant):
    gateway = _gateway()
    group = _group(hass, gateway)
    await group.async_added_to_hass()

    await group.async_turn_on()

    gateway.send.assert_awaited_once()
    assert str(gateway.send.call_args[0][0]) == "*1*1*#6##"
    assert group.is_on is True


async def test_turn_off_plain(hass: HomeAssistant):
    gateway = _gateway()
    group = _group(hass, gateway)
    await group.async_added_to_hass()

    await group.async_turn_off()

    gateway.send.assert_awaited_once()
    assert str(gateway.send.call_args[0][0]) == "*1*0*#6##"
    assert group.is_on is False


async def test_turn_on_brightness(hass: HomeAssistant):
    gateway = _gateway()
    group = _group(hass, gateway)
    await group.async_added_to_hass()

    await group.async_turn_on(**{ATTR_BRIGHTNESS: 128})

    assert str(gateway.send.call_args[0][0]) == "*#1*#6*#1*150*0##"
    assert group.is_on is True


async def test_turn_on_color_temp_kelvin(hass: HomeAssistant):
    gateway = _gateway()
    group = _group(hass, gateway)
    await group.async_added_to_hass()

    await group.async_turn_on(color_temp_kelvin=4000)

    frame = str(gateway.send.call_args[0][0])
    assert frame.startswith("*#1*#6*#14*")
    assert group.color_temp_kelvin == 4000


async def test_turn_on_hs_color_uses_last_brightness(hass: HomeAssistant):
    gateway = _gateway()
    group = _group(hass, gateway)
    await group.async_added_to_hass()

    # A brightness write first, so the HSV "value" below carries it forward.
    await group.async_turn_on(**{ATTR_BRIGHTNESS: 255})
    await group.async_turn_on(hs_color=(120.0, 50.0))

    frame = str(gateway.send.call_args[0][0])
    assert frame == "*#1*#6*#12*120*50*100##"
    assert group.hs_color == (120.0, 50.0)


async def test_turn_on_transition_raises(hass: HomeAssistant):
    gateway = _gateway()
    group = _group(hass, gateway)
    await group.async_added_to_hass()

    with pytest.raises(HomeAssistantError):
        await group.async_turn_on(transition=2)
    gateway.send.assert_not_called()


async def test_turn_on_timed_raises(hass: HomeAssistant):
    gateway = _gateway()
    group = _group(hass, gateway)
    await group.async_added_to_hass()

    with pytest.raises(HomeAssistantError):
        await group.async_turn_on_timed(duration=10)


async def test_icon_switches_on_off(hass: HomeAssistant):
    gateway = _gateway()
    group = _group(hass, gateway, icon="mdi:lightbulb-off", icon_on="mdi:lightbulb-on")
    await group.async_added_to_hass()

    await group.async_turn_on()
    assert group.icon == "mdi:lightbulb-on"

    await group.async_turn_off()
    assert group.icon == "mdi:lightbulb-off"


# ── bus frames (assumed-state mode) ────────────────────────────────────


async def test_bus_frame_on_off(hass: HomeAssistant):
    gateway = _gateway()
    group = _group(hass, gateway)
    await group.async_added_to_hass()

    _fire(group, "*1*1*#6##")
    assert group.is_on is True

    _fire(group, "*1*0*#6##")
    assert group.is_on is False


async def test_bus_frame_dimension_status(hass: HomeAssistant):
    gateway = _gateway()
    group = _group(hass, gateway)
    await group.async_added_to_hass()

    _fire(group, "*#1*#6*1*50*0##")
    assert group.brightness == int(50 / 100 * 255)

    _fire(group, "*#1*#6*14*153##")
    assert group.color_temp_kelvin == int(1000000 / 153)

    _fire(group, "*#1*#6*12*120*50*80##")
    assert group.hs_color == (120.0, 50.0)


async def test_bus_frame_group_dimension_write_echo(hass: HomeAssistant):
    """A group dimension write echoed by the gateway (*#1*#G*#D*...##) updates state too."""
    gateway = _gateway()
    group = _group(hass, gateway)
    await group.async_added_to_hass()

    _fire(group, "*#1*#6*#14*153##")
    assert group.color_temp_kelvin == int(1000000 / 153)


async def test_bus_frame_wrong_group_ignored(hass: HomeAssistant):
    gateway = _gateway()
    group = _group(hass, gateway)
    await group.async_added_to_hass()

    _fire(group, "*1*1*#7##")
    assert group.is_on is None


# ── membership mode ─────────────────────────────────────────────────────


async def test_members_resolve_and_track(hass: HomeAssistant):
    gateway = _gateway()
    registry = er.async_get(hass)
    entry1 = registry.async_get_or_create("light", DOMAIN, f"{MAC}-1-11", suggested_object_id="member_1")
    entry2 = registry.async_get_or_create("light", DOMAIN, f"{MAC}-1-12", suggested_object_id="member_2")

    group = _group(hass, gateway, members=["11", "12"], dimmable=True, color_temp=False, rgb=False)
    await group.async_added_to_hass()

    assert group.assumed_state is False
    # No member state yet -> unknown state, not a false "off".
    assert group.is_on is None
    assert group.available is False

    hass.states.async_set(entry1.entity_id, STATE_ON, {ATTR_BRIGHTNESS: 255})
    await hass.async_block_till_done()
    assert group.is_on is True
    assert group.brightness == 255

    hass.states.async_set(entry2.entity_id, STATE_ON, {ATTR_BRIGHTNESS: 127})
    await hass.async_block_till_done()
    assert group.is_on is True
    assert group.brightness == round((255 + 127) / 2)

    hass.states.async_set(entry1.entity_id, STATE_UNAVAILABLE)
    hass.states.async_set(entry2.entity_id, STATE_UNAVAILABLE)
    await hass.async_block_till_done()
    assert group.available is False


async def test_members_all_off_clears_derived_attributes(hass: HomeAssistant):
    gateway = _gateway()
    registry = er.async_get(hass)
    entry1 = registry.async_get_or_create("light", DOMAIN, f"{MAC}-1-11", suggested_object_id="member_1")

    group = _group(hass, gateway, members=["11"], dimmable=True, color_temp=False, rgb=False)
    await group.async_added_to_hass()

    hass.states.async_set(entry1.entity_id, STATE_ON, {ATTR_BRIGHTNESS: 200})
    await hass.async_block_till_done()
    assert group.is_on is True
    assert group.brightness == 200

    hass.states.async_set(entry1.entity_id, "off")
    await hass.async_block_till_done()
    assert group.is_on is False
    assert group.brightness is None


async def test_members_mode_ignores_group_broadcast_frames(hass: HomeAssistant):
    """With declared members, a group bus frame carries no per-member truth and is dropped."""
    gateway = _gateway()
    registry = er.async_get(hass)
    entry1 = registry.async_get_or_create("light", DOMAIN, f"{MAC}-1-11", suggested_object_id="member_1")
    hass.states.async_set(entry1.entity_id, "off")

    group = _group(hass, gateway, members=["11"])
    await group.async_added_to_hass()
    assert group.is_on is False

    _fire(group, "*1*1*#6##")
    # The broadcast frame must not override the member-derived state.
    assert group.is_on is False


async def test_unresolved_member_logs_and_is_skipped(hass: HomeAssistant, caplog):
    gateway = _gateway()
    group = _group(hass, gateway, members=["11"])
    await group.async_added_to_hass()

    assert group._member_entity_ids == []
    assert group.assumed_state is False
    assert "could not resolve member" in caplog.text


async def test_bus_frame_ignored_for_other_who_and_translation(hass: HomeAssistant):
    """A frame for a different WHO, or a translation frame, is dropped early."""
    gateway = _gateway()
    group = _group(hass, gateway)
    await group.async_added_to_hass()

    _fire(group, "*2*1*11##")  # WHO=2, not lighting
    assert group.is_on is None

    msg = MagicMock()
    msg.who = 1
    msg.is_translation = True
    msg.is_group = True
    msg.group = "6"
    group.handle_event(msg)
    assert group.is_on is None


async def test_bus_frame_updates_icon(hass: HomeAssistant):
    gateway = _gateway()
    group = _group(hass, gateway, icon="mdi:lightbulb-off", icon_on="mdi:lightbulb-on")
    await group.async_added_to_hass()

    _fire(group, "*1*1*#6##")
    assert group.icon == "mdi:lightbulb-on"

    _fire(group, "*1*0*#6##")
    assert group.icon == "mdi:lightbulb-off"


async def test_member_changed_updates_icon(hass: HomeAssistant):
    gateway = _gateway()
    registry = er.async_get(hass)
    entry1 = registry.async_get_or_create("light", DOMAIN, f"{MAC}-1-11", suggested_object_id="member_1")

    group = _group(hass, gateway, members=["11"], icon="mdi:lightbulb-off", icon_on="mdi:lightbulb-on")
    await group.async_added_to_hass()

    hass.states.async_set(entry1.entity_id, STATE_ON)
    await hass.async_block_till_done()
    assert group.icon == "mdi:lightbulb-on"


def test_update_from_members_noop_without_members(hass: HomeAssistant):
    """Defensive: calling the member aggregator with no resolved members is a no-op."""
    gateway = _gateway()
    group = _group(hass, gateway)
    group._update_from_members()
    assert group.is_on is None


async def test_members_on_without_reported_attributes_clears_derived_values(hass: HomeAssistant):
    """A member that is on but reports no brightness/colour clears the group's derived values."""
    gateway = _gateway()
    registry = er.async_get(hass)
    entry1 = registry.async_get_or_create("light", DOMAIN, f"{MAC}-1-11", suggested_object_id="member_1")

    group = _group(hass, gateway, members=["11"])
    await group.async_added_to_hass()

    hass.states.async_set(entry1.entity_id, STATE_ON)
    await hass.async_block_till_done()

    assert group.is_on is True
    assert group.brightness is None
    assert group.color_temp_kelvin is None


async def test_members_hs_color_is_averaged(hass: HomeAssistant):
    from homeassistant.components.light import ATTR_HS_COLOR

    gateway = _gateway()
    registry = er.async_get(hass)
    entry1 = registry.async_get_or_create("light", DOMAIN, f"{MAC}-1-11", suggested_object_id="member_1")
    entry2 = registry.async_get_or_create("light", DOMAIN, f"{MAC}-1-12", suggested_object_id="member_2")

    group = _group(hass, gateway, members=["11", "12"], dimmable=False, color_temp=False, rgb=True)
    await group.async_added_to_hass()

    hass.states.async_set(entry1.entity_id, STATE_ON, {ATTR_HS_COLOR: (100.0, 40.0)})
    hass.states.async_set(entry2.entity_id, STATE_ON, {ATTR_HS_COLOR: (120.0, 60.0)})
    await hass.async_block_till_done()

    assert group.hs_color == (110.0, 50.0)


async def test_members_color_temp_kelvin_is_averaged(hass: HomeAssistant):
    from homeassistant.components.light import ATTR_COLOR_TEMP_KELVIN

    gateway = _gateway()
    registry = er.async_get(hass)
    entry1 = registry.async_get_or_create("light", DOMAIN, f"{MAC}-1-11", suggested_object_id="member_1")
    entry2 = registry.async_get_or_create("light", DOMAIN, f"{MAC}-1-12", suggested_object_id="member_2")

    group = _group(hass, gateway, members=["11", "12"], dimmable=False, color_temp=True, rgb=False)
    await group.async_added_to_hass()

    hass.states.async_set(entry1.entity_id, STATE_ON, {ATTR_COLOR_TEMP_KELVIN: 3000})
    hass.states.async_set(entry2.entity_id, STATE_ON, {ATTR_COLOR_TEMP_KELVIN: 4000})
    await hass.async_block_till_done()

    assert group.color_temp_kelvin == 3500


async def test_async_update_skips_status_requests_with_members(hass: HomeAssistant):
    """A member-mode group never polls the bus, neither on add nor on demand."""
    gateway = _gateway()
    registry = er.async_get(hass)
    registry.async_get_or_create("light", DOMAIN, f"{MAC}-1-11", suggested_object_id="member_1")

    group = _group(hass, gateway, members=["11"])
    # _poll_on_add's implicit async_update() (via async_added_to_hass) must also
    # respect "has members" - assert immediately after, before any explicit call.
    await group.async_added_to_hass()
    gateway.send_status_request.assert_not_called()

    await group.async_update()
    gateway.send_status_request.assert_not_called()


async def test_async_update_without_members_queries_status(hass: HomeAssistant):
    """An assumed-state group polls its own status once as soon as it is added."""
    gateway = _gateway()
    group = _group(hass, gateway, dimmable=True, color_temp=True, rgb=True)

    await group.async_added_to_hass()

    assert gateway.send_status_request.await_count == 4
    frames = {str(c.args[0]) for c in gateway.send_status_request.call_args_list}
    assert frames == {"*#1*#6##", "*#1*#6*1##", "*#1*#6*14##", "*#1*#6*12##"}
