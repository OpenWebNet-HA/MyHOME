"""Tests for MyHOME device triggers."""
from unittest.mock import AsyncMock, MagicMock

import pytest
from homeassistant.components.device_automation import (
    DeviceAutomationType,
    async_get_device_automations,
)
from homeassistant.const import (
    CONF_DEVICE_ID,
    CONF_DOMAIN,
    CONF_PLATFORM,
    CONF_TYPE,
)
from homeassistant.core import Context, HomeAssistant
from homeassistant.helpers import device_registry as dr
from homeassistant.setup import async_setup_component
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.myhome.const import (
    CONF_CENTRALIZED_SHUTTER_CLOSE,
    CONF_CENTRALIZED_SHUTTER_OPEN,
    CONF_CENTRALIZED_SHUTTER_STOP,
    CONF_LONG_PRESS,
    CONF_LONG_PRESS_REPEAT,
    CONF_ROTARY_CCW_FAST,
    CONF_ROTARY_CCW_SLOW,
    CONF_ROTARY_CW_FAST,
    CONF_ROTARY_CW_SLOW,
    CONF_SHORT_PRESS,
    CONF_SHORT_RELEASE,
    DOMAIN,
)
from custom_components.myhome.device_trigger import (
    CONF_ADDRESS,
    CONF_SUBTYPE,
    GATEWAY_TRIGGER_TYPES,
    TRIGGER_SUBTYPES,
    TRIGGER_TYPES,
    async_attach_trigger,
    async_get_triggers,
)


@pytest.mark.asyncio
async def test_async_get_triggers_device_not_found(hass: HomeAssistant):
    """Test async_get_triggers returns empty list when device is not found."""
    mock_registry = MagicMock()
    mock_registry.async_get.return_value = None

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr("homeassistant.helpers.device_registry.async_get", lambda h: mock_registry)
        triggers = await async_get_triggers(hass, "non_existent_device_id")
        assert triggers == []


@pytest.mark.asyncio
async def test_async_get_triggers_non_myhome_device(hass: HomeAssistant):
    """Test async_get_triggers returns empty list for devices not from myhome domain."""
    mock_device = MagicMock()
    mock_device.identifiers = {("other_domain", "12345")}
    mock_registry = MagicMock()
    mock_registry.async_get.return_value = mock_device

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr("homeassistant.helpers.device_registry.async_get", lambda h: mock_registry)
        triggers = await async_get_triggers(hass, "other_device_id")
        assert triggers == []


@pytest.mark.asyncio
async def test_async_get_triggers_success(hass: HomeAssistant):
    """Test async_get_triggers returns 36 triggers (4 types x 9 buttons)."""
    mock_device = MagicMock()
    mock_device.identifiers = {(DOMAIN, "00:03:50:aa:bb:cc")}
    mock_registry = MagicMock()
    mock_registry.async_get.return_value = mock_device

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr("homeassistant.helpers.device_registry.async_get", lambda h: mock_registry)
        triggers = await async_get_triggers(hass, "myhome_device_id")

        assert len(triggers) == len(TRIGGER_TYPES) * len(TRIGGER_SUBTYPES) + len(GATEWAY_TRIGGER_TYPES)
        first_trigger = triggers[0]
        assert first_trigger[CONF_PLATFORM] == "device"
        assert first_trigger[CONF_DOMAIN] == DOMAIN
        assert first_trigger[CONF_DEVICE_ID] == "myhome_device_id"
        assert first_trigger[CONF_TYPE] in TRIGGER_TYPES
        assert first_trigger[CONF_SUBTYPE] in TRIGGER_SUBTYPES


@pytest.mark.asyncio
async def test_async_attach_trigger_and_dispatch(hass: HomeAssistant):
    """Test attaching a trigger and receiving matched/unmatched events."""
    config = {
        CONF_TYPE: CONF_SHORT_PRESS,
        CONF_SUBTYPE: "button_3",
    }
    action = AsyncMock()
    trigger_info = {"extra_info": 123}

    unsub = await async_attach_trigger(hass, config, action, trigger_info)
    assert callable(unsub)

    # Fire unmatched event (wrong button)
    hass.bus.async_fire("myhome_cen_event", {"event": CONF_SHORT_PRESS, "pushbutton": 2})
    await hass.async_block_till_done()
    action.assert_not_called()

    # Fire unmatched event (wrong event type)
    hass.bus.async_fire("myhome_cen_event", {"event": CONF_LONG_PRESS, "pushbutton": 3})
    await hass.async_block_till_done()
    action.assert_not_called()

    # Fire matched CEN event
    hass.bus.async_fire("myhome_cen_event", {"event": CONF_SHORT_PRESS, "pushbutton": 3})
    await hass.async_block_till_done()
    action.assert_called_once()
    call_arg = action.call_args[0][0]
    assert call_arg["trigger"]["platform"] == "device"
    assert call_arg["trigger"]["extra_info"] == 123
    assert call_arg["trigger"]["event"]["pushbutton"] == 3

    action.reset_mock()

    # Fire matched CEN+ event
    hass.bus.async_fire("myhome_cenplus_event", {"event": CONF_SHORT_PRESS, "pushbutton": 3})
    await hass.async_block_till_done()
    action.assert_called_once()

    # Unsubscribe
    unsub()
    action.reset_mock()

    # Fire event after unsubscribing -> should not be called
    hass.bus.async_fire("myhome_cen_event", {"event": CONF_SHORT_PRESS, "pushbutton": 3})
    await hass.async_block_till_done()
    action.assert_not_called()


@pytest.mark.asyncio
async def test_async_get_triggers_cen_device(hass: HomeAssistant):
    """Test async_get_triggers returns triggers with address for dedicated CEN device."""
    mock_device = MagicMock()
    mock_device.identifiers = {(DOMAIN, "00:03:50:aa:bb:cc-15-5")}
    mock_registry = MagicMock()
    mock_registry.async_get.return_value = mock_device

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr("homeassistant.helpers.device_registry.async_get", lambda h: mock_registry)
        triggers = await async_get_triggers(hass, "cen_device_id")

        # WHO 15 has no rotary events and no separate repeat frame.
        assert {t[CONF_TYPE] for t in triggers} == TRIGGER_TYPES - CEN_UNSUPPORTED
        assert len(triggers) == (len(TRIGGER_TYPES) - len(CEN_UNSUPPORTED)) * len(TRIGGER_SUBTYPES)
        for trigger in triggers:
            assert trigger[CONF_ADDRESS] == 5
            assert trigger[CONF_DEVICE_ID] == "cen_device_id"


@pytest.mark.asyncio
async def test_async_get_triggers_cenplus_device(hass: HomeAssistant):
    """Test async_get_triggers returns triggers with address for dedicated CEN+ device."""
    mock_device = MagicMock()
    mock_device.identifiers = {(DOMAIN, "00:03:50:aa:bb:cc-25-12")}
    mock_registry = MagicMock()
    mock_registry.async_get.return_value = mock_device

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr("homeassistant.helpers.device_registry.async_get", lambda h: mock_registry)
        triggers = await async_get_triggers(hass, "cenplus_device_id")

        # CEN+ sends no short-release frame, so short_release is not offered.
        assert {t[CONF_TYPE] for t in triggers} == TRIGGER_TYPES - {CONF_SHORT_RELEASE}
        assert len(triggers) == (len(TRIGGER_TYPES) - 1) * len(TRIGGER_SUBTYPES)
        for trigger in triggers:
            assert trigger[CONF_ADDRESS] == 12
            assert trigger[CONF_DEVICE_ID] == "cenplus_device_id"


@pytest.mark.asyncio
async def test_home_assistant_discovers_cenplus_device_triggers(hass: HomeAssistant):
    """Test CEN+ triggers are discoverable through Home Assistant's device UI path."""
    entry = MockConfigEntry(domain=DOMAIN)
    entry.add_to_hass(hass)
    device = dr.async_get(hass).async_get_or_create(
        config_entry_id=entry.entry_id,
        identifiers={(DOMAIN, "00:03:50:00:00:01-25-1")},
        name="CEN+ Unit 1",
    )

    triggers = await async_get_device_automations(
        hass, DeviceAutomationType.TRIGGER, [device.id]
    )

    assert len(triggers[device.id]) == (len(TRIGGER_TYPES) - 1) * len(TRIGGER_SUBTYPES)


ROTARY_TYPES = {CONF_ROTARY_CW_SLOW, CONF_ROTARY_CW_FAST, CONF_ROTARY_CCW_SLOW, CONF_ROTARY_CCW_FAST}
CEN_UNSUPPORTED = ROTARY_TYPES | {CONF_LONG_PRESS_REPEAT}


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("identifier", "expected"),
    [
        ("cen_31", TRIGGER_TYPES - CEN_UNSUPPORTED),
        ("00:03:50:aa:bb:cc-cen-31", TRIGGER_TYPES - CEN_UNSUPPORTED),
        ("cenplus_2101", TRIGGER_TYPES - {CONF_SHORT_RELEASE}),
        ("00:03:50:aa:bb:cc-cenplus-2101", TRIGGER_TYPES - {CONF_SHORT_RELEASE}),
    ],
)
async def test_async_get_triggers_filters_by_legacy_identifier(
    hass: HomeAssistant, identifier: str, expected: set[str]
):
    """Older CEN/CEN+ identifier forms are filtered by family too."""
    mock_device = MagicMock()
    # A list, not a set: the foreign identifier must come first on every run.
    mock_device.identifiers = [("other_domain", "x"), (DOMAIN, identifier)]
    mock_registry = MagicMock()
    mock_registry.async_get.return_value = mock_device

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr("homeassistant.helpers.device_registry.async_get", lambda h: mock_registry)
        triggers = await async_get_triggers(hass, "legacy_device_id")

    assert {t[CONF_TYPE] for t in triggers} == expected


@pytest.mark.asyncio
async def test_async_get_triggers_gateway_with_foreign_identifier_gets_all_types(hass: HomeAssistant):
    """Identifiers of other integrations are skipped; a gateway keeps every trigger type."""
    mock_device = MagicMock()
    mock_device.identifiers = {("other_domain", "00:03:50:aa:bb:cc-15-5"), (DOMAIN, "00:03:50:aa:bb:cc")}
    mock_registry = MagicMock()
    mock_registry.async_get.return_value = mock_device

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr("homeassistant.helpers.device_registry.async_get", lambda h: mock_registry)
        triggers = await async_get_triggers(hass, "gateway_device_id")

    assert {t[CONF_TYPE] for t in triggers} == TRIGGER_TYPES | GATEWAY_TRIGGER_TYPES


@pytest.mark.asyncio
async def test_async_get_triggers_ignores_standard_entities(hass: HomeAssistant):
    """Test async_get_triggers rejects non-scenario entities like lights and covers."""
    mock_registry = MagicMock()

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr("homeassistant.helpers.device_registry.async_get", lambda h: mock_registry)

        # Test Light device (WHO=1)
        light_dev = MagicMock()
        light_dev.identifiers = {(DOMAIN, "00:03:50:aa:bb:cc-1-12")}
        mock_registry.async_get.return_value = light_dev
        assert await async_get_triggers(hass, "light_id") == []

        # Test Cover device (WHO=2)
        cover_dev = MagicMock()
        cover_dev.identifiers = {(DOMAIN, "00:03:50:aa:bb:cc-2-21")}
        mock_registry.async_get.return_value = cover_dev
        assert await async_get_triggers(hass, "cover_id") == []

        # Test Climate device (WHO=4)
        climate_dev = MagicMock()
        climate_dev.identifiers = {(DOMAIN, "00:03:50:aa:bb:cc-4-1")}
        mock_registry.async_get.return_value = climate_dev
        assert await async_get_triggers(hass, "climate_id") == []


@pytest.mark.asyncio
async def test_async_attach_trigger_with_address_isolation(hass: HomeAssistant):
    """Test that triggers with an address filter only fire when event object matches."""
    config = {
        CONF_TYPE: CONF_SHORT_PRESS,
        CONF_SUBTYPE: "button_1",
        CONF_ADDRESS: 5,
    }
    action = AsyncMock()
    trigger_info = {"name": "test_trigger"}

    unsub = await async_attach_trigger(hass, config, action, trigger_info)

    # 1. Fire CEN event from DIFFERENT object (e.g. object 9) -> should be ignored!
    hass.bus.async_fire(
        "myhome_cen_event",
        {
            "event": CONF_SHORT_PRESS,
            "pushbutton": 1,
            "object": 9,
        },
    )
    await hass.async_block_till_done()
    action.assert_not_called()

    # 2. Fire CEN event from MATCHING object (object 5) -> should fire!
    cen_context = Context()
    hass.bus.async_fire(
        "myhome_cen_event",
        {
            "event": CONF_SHORT_PRESS,
            "pushbutton": 1,
            "object": 5,
        },
        context=cen_context,
    )
    await hass.async_block_till_done()
    action.assert_called_once()
    assert action.call_args[0][1] == cen_context
    action.reset_mock()

    # 3. Fire CEN+ event from DIFFERENT object (object 2) -> should be ignored!
    hass.bus.async_fire(
        "myhome_cenplus_event",
        {
            "event": CONF_SHORT_PRESS,
            "pushbutton": 1,
            "object": 2,
        },
    )
    await hass.async_block_till_done()
    action.assert_not_called()

    # 4. Fire CEN+ event from MATCHING object (object 5) -> should fire!
    cenplus_context = Context()
    hass.bus.async_fire(
        "myhome_cenplus_event",
        {
            "event": CONF_SHORT_PRESS,
            "pushbutton": 1,
            "object": 5,
        },
        context=cenplus_context,
    )
    await hass.async_block_till_done()
    action.assert_called_once()
    assert action.call_args[0][1] == cenplus_context

    unsub()


@pytest.mark.asyncio
async def test_async_attach_trigger_cenplus_wire_address_tolerance(hass: HomeAssistant):
    """Test that CEN+ triggers match both physical wire WHERE (e.g. 21) and virtual object (1)."""
    # 1. Trigger configured with physical wire address 21 (as seen in trace & Living Now KW8011)
    config_wire = {
        CONF_TYPE: CONF_SHORT_PRESS,
        CONF_SUBTYPE: "button_1",
        CONF_ADDRESS: 21,
    }
    action_wire = AsyncMock()
    unsub_wire = await async_attach_trigger(hass, config_wire, action_wire, {"name": "wire_trigger"})

    # 2. Trigger configured with virtual object 1
    config_obj = {
        CONF_TYPE: CONF_SHORT_PRESS,
        CONF_SUBTYPE: "button_1",
        CONF_ADDRESS: 1,
    }
    action_obj = AsyncMock()
    unsub_obj = await async_attach_trigger(hass, config_obj, action_obj, {"name": "obj_trigger"})

    # Fire event representing *25*21#1*21## (Object 1, Button 1, Wire WHERE 21)
    hass.bus.async_fire(
        "myhome_cenplus_event",
        {
            "event": CONF_SHORT_PRESS,
            "pushbutton": 1,
            "object": 1,
            "where": "1",
        },
    )
    await hass.async_block_till_done()

    # Both wire address trigger (21) and object trigger (1) must fire!
    action_wire.assert_called_once()
    action_obj.assert_called_once()

    unsub_wire()
    unsub_obj()


@pytest.mark.asyncio
async def test_async_attach_trigger_resolves_address_from_device(hass: HomeAssistant):
    """Test that address is automatically resolved from device registry when not in config."""
    mock_device = MagicMock()
    mock_device.identifiers = {(DOMAIN, "00:03:50:aa:bb:cc-15-7")}
    mock_registry = MagicMock()
    mock_registry.async_get.return_value = mock_device

    config = {
        CONF_DEVICE_ID: "dev_cen_7",
        CONF_TYPE: CONF_SHORT_PRESS,
        CONF_SUBTYPE: "button_2",
    }
    action = AsyncMock()
    trigger_info = {}

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr("homeassistant.helpers.device_registry.async_get", lambda h: mock_registry)
        unsub = await async_attach_trigger(hass, config, action, trigger_info)

        # Mismatch address (object 3) -> rejected
        hass.bus.async_fire(
            "myhome_cen_event",
            {
                "event": CONF_SHORT_PRESS,
                "pushbutton": 2,
                "object": 3,
            },
        )
        await hass.async_block_till_done()
        action.assert_not_called()

        # Matching address (object 7) -> accepted
        hass.bus.async_fire(
            "myhome_cen_event",
            {
                "event": CONF_SHORT_PRESS,
                "pushbutton": 2,
                "object": 7,
            },
        )
        await hass.async_block_till_done()
        action.assert_called_once()

        unsub()


@pytest.mark.asyncio
async def test_async_attach_trigger_cenplus_rotary_and_press(hass: HomeAssistant):
    """Test rotary and press events on CEN+ scenario triggers."""
    config = {
        CONF_TYPE: CONF_ROTARY_CW_FAST,
        CONF_SUBTYPE: "button_0",
        CONF_ADDRESS: 14,
    }
    action = AsyncMock()
    unsub = await async_attach_trigger(hass, config, action, {})

    # Fire matching rotary event
    hass.bus.async_fire(
        "myhome_cenplus_event",
        {
            "event": CONF_ROTARY_CW_FAST,
            "pushbutton": 0,
            "object": 14,
        },
    )
    await hass.async_block_till_done()
    action.assert_called_once()
    action.reset_mock()

    # Fire opposite rotary direction (CCW) -> rejected
    hass.bus.async_fire(
        "myhome_cenplus_event",
        {
            "event": CONF_ROTARY_CCW_FAST,
            "pushbutton": 0,
            "object": 14,
        },
    )
    await hass.async_block_till_done()
    unsub()


@pytest.mark.asyncio
async def test_long_press_and_repeat_triggers_are_separate(hass: HomeAssistant):
    """A hold fires long_press once (WHAT 22) and long_press_repeat per WHAT 23."""
    assert CONF_LONG_PRESS_REPEAT in TRIGGER_TYPES
    on_press = AsyncMock()
    on_repeat = AsyncMock()
    unsub_press = await async_attach_trigger(
        hass, {CONF_TYPE: CONF_LONG_PRESS, CONF_SUBTYPE: "button_1", CONF_ADDRESS: 1}, on_press, {}
    )
    unsub_repeat = await async_attach_trigger(
        hass, {CONF_TYPE: CONF_LONG_PRESS_REPEAT, CONF_SUBTYPE: "button_1", CONF_ADDRESS: 1}, on_repeat, {}
    )

    for event in (CONF_LONG_PRESS, CONF_LONG_PRESS_REPEAT, CONF_LONG_PRESS_REPEAT):
        hass.bus.async_fire("myhome_cenplus_event", {"event": event, "pushbutton": 1, "object": 1})
    await hass.async_block_till_done()

    assert on_press.call_count == 1
    assert on_repeat.call_count == 2
    unsub_press()
    unsub_repeat()


def test_get_cen_info_from_device_branches():
    """Test _get_cen_info_from_device edge cases and identifier parsing."""
    from custom_components.myhome.device_trigger import _get_cen_info_from_device

    # 1. Non-integer suffix on CEN/CEN+ identifier with mixed domain
    dev1 = MagicMock()
    dev1.identifiers = [("other", "123"), (DOMAIN, "00:03:50:aa:bb:cc-15-notanint")]
    is_cen, addr = _get_cen_info_from_device(dev1)
    assert is_cen is True
    assert addr is None

    # 2. cen_ prefix valid int
    dev2 = MagicMock()
    dev2.identifiers = [(DOMAIN, "cen_42")]
    is_cen, addr = _get_cen_info_from_device(dev2)
    assert is_cen is True
    assert addr == 42

    # 3. cenplus_ prefix invalid int
    dev3 = MagicMock()
    dev3.identifiers = [(DOMAIN, "cenplus_invalid")]
    is_cen, addr = _get_cen_info_from_device(dev3)
    assert is_cen is True
    assert addr is None

    # 4. Standard non-button device (e.g. light WHO=1) with mixed domain
    dev4 = MagicMock()
    dev4.identifiers = [("other", "123"), (DOMAIN, "00:03:50:aa:bb:cc-1-21")]
    is_cen, addr = _get_cen_info_from_device(dev4)
    assert is_cen is False
    assert addr is None


def test_get_gateway_mac_from_device_branches():
    """Test all branches of _get_gateway_mac_from_device for 100% test coverage."""
    from custom_components.myhome.device_trigger import _get_gateway_mac_from_device

    # 1. MAC address in connections
    dev1 = MagicMock()
    dev1.connections = {(dr.CONNECTION_NETWORK_MAC, "00:03:50:11:22:33")}
    dev1.identifiers = set()
    assert _get_gateway_mac_from_device(dev1) == "00:03:50:11:22:33"

    # 2. Other connection type skipped, falls back to CEN identifier
    dev2 = MagicMock()
    dev2.connections = {("ip", "192.168.1.50")}
    dev2.identifiers = {(DOMAIN, "00:03:50:44:55:66-15-1")}
    assert _get_gateway_mac_from_device(dev2) == "00:03:50:44:55:66"

    # 3. Single-part gateway identifier
    dev3 = MagicMock()
    dev3.connections = set()
    dev3.identifiers = {(DOMAIN, "00:03:50:77:88:99")}
    assert _get_gateway_mac_from_device(dev3) == "00:03:50:77:88:99"

    # 4. Foreign domain identifier skipped
    dev4 = MagicMock()
    dev4.connections = set()
    dev4.identifiers = {("other_domain", "00:03:50:aa:bb:cc")}
    assert _get_gateway_mac_from_device(dev4) is None

    # 5. Multi-part non-CEN identifier (e.g. Light WHO=1) falls through to None
    dev5 = MagicMock()
    dev5.connections = set()
    dev5.identifiers = {(DOMAIN, "00:03:50:aa:bb:cc-1-12")}
    assert _get_gateway_mac_from_device(dev5) is None

    # 6. A child device has no connections to read; only its identifiers count
    child = MagicMock(spec=dr.ChildDeviceEntry)
    child.identifiers = {(DOMAIN, "00:03:50:dd:ee:ff")}
    assert _get_gateway_mac_from_device(child) == "00:03:50:dd:ee:ff"


@pytest.mark.asyncio
async def test_async_get_triggers_gateway_includes_centralized_shutter_triggers(hass: HomeAssistant):
    """Test that a gateway device returns centralized shutter triggers."""
    mock_device = MagicMock()
    mock_device.identifiers = {(DOMAIN, "00:03:50:aa:bb:cc")}
    mock_registry = MagicMock()
    mock_registry.async_get.return_value = mock_device

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr("homeassistant.helpers.device_registry.async_get", lambda h: mock_registry)
        triggers = await async_get_triggers(hass, "gw_device_id")

        gw_triggers = [t for t in triggers if t[CONF_TYPE] in GATEWAY_TRIGGER_TYPES]
        assert len(gw_triggers) == 3
        types = {t[CONF_TYPE] for t in gw_triggers}
        assert types == {
            CONF_CENTRALIZED_SHUTTER_OPEN,
            CONF_CENTRALIZED_SHUTTER_CLOSE,
            CONF_CENTRALIZED_SHUTTER_STOP,
        }
        for t in gw_triggers:
            assert t[CONF_PLATFORM] == "device"
            assert t[CONF_DOMAIN] == DOMAIN
            assert t[CONF_DEVICE_ID] == "gw_device_id"


@pytest.mark.asyncio
async def test_async_attach_trigger_centralized_shutter(hass: HomeAssistant):
    """Test attaching and firing centralized shutter triggers via general automation events."""
    mock_device = MagicMock()
    mock_device.identifiers = {(DOMAIN, "00:03:50:aa:bb:cc")}
    mock_device.connections = {(dr.CONNECTION_NETWORK_MAC, "00:03:50:aa:bb:cc")}
    mock_registry = MagicMock()
    mock_registry.async_get.return_value = mock_device

    action_open = AsyncMock()
    action_close = AsyncMock()
    action_stop = AsyncMock()

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr("homeassistant.helpers.device_registry.async_get", lambda h: mock_registry)

        unsub_open = await async_attach_trigger(
            hass,
            {
                CONF_DEVICE_ID: "gw_dev_id",
                CONF_TYPE: CONF_CENTRALIZED_SHUTTER_OPEN,
            },
            action_open,
            {"trigger_name": "open"},
        )
        unsub_close = await async_attach_trigger(
            hass,
            {
                CONF_DEVICE_ID: "gw_dev_id",
                CONF_TYPE: CONF_CENTRALIZED_SHUTTER_CLOSE,
            },
            action_close,
            {"trigger_name": "close"},
        )
        unsub_stop = await async_attach_trigger(
            hass,
            {
                CONF_DEVICE_ID: "gw_dev_id",
                CONF_TYPE: CONF_CENTRALIZED_SHUTTER_STOP,
            },
            action_stop,
            {"trigger_name": "stop"},
        )

        # 1. Fire open event with matching gateway MAC
        open_context = Context()
        hass.bus.async_fire(
            "myhome_general_automation_event",
            {
                "message": "*2*11#100#001#1*0##",
                "event": "open",
                "where": "0",
                "gateway_mac": "00:03:50:aa:bb:cc",
            },
            context=open_context,
        )
        await hass.async_block_till_done()
        action_open.assert_called_once()
        call_arg = action_open.call_args[0][0]
        assert call_arg["trigger"]["platform"] == "device"
        assert call_arg["trigger"]["trigger_name"] == "open"
        assert call_arg["trigger"]["event"]["event"] == "open"
        assert action_open.call_args[0][1] == open_context
        action_close.assert_not_called()
        action_stop.assert_not_called()
        action_open.reset_mock()

        # 2. Fire close event with DIFFERENT gateway MAC -> ignored
        hass.bus.async_fire(
            "myhome_general_automation_event",
            {
                "message": "*2*12#100#001#1*0##",
                "event": "close",
                "where": "0",
                "gateway_mac": "00:03:50:99:99:99",
            },
        )
        await hass.async_block_till_done()
        action_close.assert_not_called()

        # 3. Fire close event with MATCHING gateway MAC -> fired
        hass.bus.async_fire(
            "myhome_general_automation_event",
            {
                "message": "*2*12#100#001#1*0##",
                "event": "close",
                "where": "0",
                "gateway_mac": "00:03:50:aa:bb:cc",
            },
        )
        await hass.async_block_till_done()
        action_close.assert_called_once()
        action_close.reset_mock()

        # 4. Fire stop event with MATCHING gateway MAC -> fired
        hass.bus.async_fire(
            "myhome_general_automation_event",
            {
                "message": "*2*10#001#1*0##",
                "event": "stop",
                "where": "0",
                "gateway_mac": "00:03:50:aa:bb:cc",
            },
        )
        await hass.async_block_till_done()
        action_stop.assert_called_once()

        unsub_open()
        unsub_close()
        unsub_stop()


@pytest.mark.asyncio
async def test_centralized_shutter_trigger_in_automation_choose_condition(hass: HomeAssistant):
    """Test centralized shutter device triggers inside a real HA automation with choose/trigger.id (issue #445)."""
    entry = MockConfigEntry(domain=DOMAIN, data={})
    entry.add_to_hass(hass)
    dev_reg = dr.async_get(hass)
    dev = dev_reg.async_get_or_create(
        config_entry_id=entry.entry_id,
        identifiers={(DOMAIN, "00:03:50:aa:bb:cc")},
    )

    events_received: list[str] = []

    async def record_service(call):
        events_received.append(call.data["result"])

    hass.services.async_register("test", "record", record_service)

    config = {
        "automation": [
            {
                "alias": "Pulsante Centralizzato Tapparelle",
                "triggers": [
                    {
                        "trigger": "device",
                        "domain": "myhome",
                        "device_id": dev.id,
                        "type": "centralized_shutter_open",
                        "id": "open",
                    },
                    {
                        "trigger": "device",
                        "domain": "myhome",
                        "device_id": dev.id,
                        "type": "centralized_shutter_close",
                        "id": "close",
                    },
                    {
                        "trigger": "device",
                        "domain": "myhome",
                        "device_id": dev.id,
                        "type": "centralized_shutter_stop",
                        "id": "stop",
                    },
                ],
                "actions": [
                    {
                        "choose": [
                            {
                                "conditions": [
                                    {"condition": "trigger", "id": ["open"]}
                                ],
                                "sequence": [
                                    {"action": "test.record", "data": {"result": "matched_open"}}
                                ],
                            },
                            {
                                "conditions": [
                                    {"condition": "trigger", "id": ["close"]}
                                ],
                                "sequence": [
                                    {"action": "test.record", "data": {"result": "matched_close"}}
                                ],
                            },
                            {
                                "conditions": [
                                    {"condition": "trigger", "id": ["stop"]}
                                ],
                                "sequence": [
                                    {"action": "test.record", "data": {"result": "matched_stop"}}
                                ],
                            },
                        ],
                        "default": [
                            {"action": "test.record", "data": {"result": "default_fallback"}}
                        ],
                    }
                ],
            }
        ]
    }

    assert await async_setup_component(hass, "automation", config)
    await hass.async_block_till_done()

    # 1. Fire open event -> matches 'open'
    hass.bus.async_fire(
        "myhome_general_automation_event",
        {
            "message": "*2*11#100#001#1*0##",
            "event": "open",
            "where": "0",
            "gateway_mac": "00:03:50:aa:bb:cc",
        },
    )
    await hass.async_block_till_done()
    assert events_received == ["matched_open"]

    # 2. Fire close event -> matches 'close'
    hass.bus.async_fire(
        "myhome_general_automation_event",
        {
            "message": "*2*12#100#001#1*0##",
            "event": "close",
            "where": "0",
            "gateway_mac": "00:03:50:aa:bb:cc",
        },
    )
    await hass.async_block_till_done()
    assert events_received == ["matched_open", "matched_close"]

    # 3. Fire stop event -> matches 'stop'
    hass.bus.async_fire(
        "myhome_general_automation_event",
        {
            "message": "*2*10#001#1*0##",
            "event": "stop",
            "where": "0",
            "gateway_mac": "00:03:50:aa:bb:cc",
        },
    )
    await hass.async_block_till_done()
    assert events_received == ["matched_open", "matched_close", "matched_stop"]


@pytest.mark.asyncio
async def test_cen_scenario_trigger_in_automation_choose_condition(hass: HomeAssistant):
    """Test CEN scenario device triggers inside a real HA automation with choose/trigger.id."""
    entry = MockConfigEntry(domain=DOMAIN, data={})
    entry.add_to_hass(hass)
    dev_reg = dr.async_get(hass)
    dev = dev_reg.async_get_or_create(
        config_entry_id=entry.entry_id,
        identifiers={(DOMAIN, "00:03:50:aa:bb:cc-15-5")},
    )

    events_received: list[str] = []

    async def record_service(call):
        events_received.append(call.data["result"])

    hass.services.async_register("test", "record_cen", record_service)

    config = {
        "automation": [
            {
                "alias": "CEN Scenario Button Automation",
                "triggers": [
                    {
                        "trigger": "device",
                        "domain": "myhome",
                        "device_id": dev.id,
                        "type": CONF_SHORT_PRESS,
                        "subtype": "button_1",
                        "id": "btn1",
                    },
                    {
                        "trigger": "device",
                        "domain": "myhome",
                        "device_id": dev.id,
                        "type": CONF_SHORT_PRESS,
                        "subtype": "button_2",
                        "id": "btn2",
                    },
                    {
                        "trigger": "device",
                        "domain": "myhome",
                        "device_id": dev.id,
                        "type": CONF_SHORT_PRESS,
                        "subtype": "button_3",
                        "id": "btn3",
                    },
                ],
                "actions": [
                    {
                        "choose": [
                            {
                                "conditions": [
                                    {"condition": "trigger", "id": ["btn1"]}
                                ],
                                "sequence": [
                                    {"action": "test.record_cen", "data": {"result": "matched_btn1"}}
                                ],
                            },
                            {
                                "conditions": [
                                    {"condition": "trigger", "id": ["btn2"]}
                                ],
                                "sequence": [
                                    {"action": "test.record_cen", "data": {"result": "matched_btn2"}}
                                ],
                            },
                        ],
                        "default": [
                            {"action": "test.record_cen", "data": {"result": "default_fallback"}}
                        ],
                    }
                ],
            }
        ]
    }

    assert await async_setup_component(hass, "automation", config)
    await hass.async_block_till_done()

    # Fire CEN short press on button 1, object 5
    hass.bus.async_fire(
        "myhome_cen_event",
        {
            "event": CONF_SHORT_PRESS,
            "pushbutton": 1,
            "object": 5,
            "gateway_mac": "00:03:50:aa:bb:cc",
        },
    )
    await hass.async_block_till_done()
    assert events_received == ["matched_btn1"]

    # Fire CEN short press on button 2, object 5
    hass.bus.async_fire(
        "myhome_cen_event",
        {
            "event": CONF_SHORT_PRESS,
            "pushbutton": 2,
            "object": 5,
            "gateway_mac": "00:03:50:aa:bb:cc",
        },
    )
    await hass.async_block_till_done()
    assert events_received == ["matched_btn1", "matched_btn2"]

    # Button 3 has no trigger: nothing fires, so no default branch either
    hass.bus.async_fire(
        "myhome_cenplus_event",
        {
            "event": CONF_SHORT_PRESS,
            "pushbutton": 3,
            "object": 8,
            "gateway_mac": "00:03:50:aa:bb:cc",
        },
    )
    await hass.async_block_till_done()
    assert events_received == ["matched_btn1", "matched_btn2"]

    # Fire CEN short press on button 3, object 5 -> unhandled in choose, falls through to default
    hass.bus.async_fire(
        "myhome_cen_event",
        {
            "event": CONF_SHORT_PRESS,
            "pushbutton": 3,
            "object": 5,
            "gateway_mac": "00:03:50:aa:bb:cc",
        },
    )
    await hass.async_block_till_done()
    assert events_received == ["matched_btn1", "matched_btn2", "default_fallback"]


@pytest.mark.asyncio
async def test_cenplus_scenario_trigger_in_automation_choose_condition(hass: HomeAssistant):
    """Test CEN+ scenario device triggers inside a real HA automation with choose/trigger.id."""
    entry = MockConfigEntry(domain=DOMAIN, data={})
    entry.add_to_hass(hass)
    dev_reg = dr.async_get(hass)
    dev = dev_reg.async_get_or_create(
        config_entry_id=entry.entry_id,
        identifiers={(DOMAIN, "00:03:50:aa:bb:cc-25-8")},
    )

    events_received: list[str] = []

    async def record_service(call):
        events_received.append(call.data["result"])

    hass.services.async_register("test", "record_cenplus", record_service)

    config = {
        "automation": [
            {
                "alias": "CEN+ Scenario Button Automation",
                "triggers": [
                    {
                        "trigger": "device",
                        "domain": "myhome",
                        "device_id": dev.id,
                        "type": CONF_SHORT_PRESS,
                        "subtype": "button_1",
                        "id": "btn1",
                    },
                    {
                        "trigger": "device",
                        "domain": "myhome",
                        "device_id": dev.id,
                        "type": CONF_SHORT_PRESS,
                        "subtype": "button_2",
                        "id": "btn2",
                    },
                ],
                "actions": [
                    {
                        "choose": [
                            {
                                "conditions": [
                                    {"condition": "trigger", "id": ["btn1"]}
                                ],
                                "sequence": [
                                    {"action": "test.record_cenplus", "data": {"result": "matched_btn1"}}
                                ],
                            },
                            {
                                "conditions": [
                                    {"condition": "trigger", "id": ["btn2"]}
                                ],
                                "sequence": [
                                    {"action": "test.record_cenplus", "data": {"result": "matched_btn2"}}
                                ],
                            },
                        ],
                        "default": [
                            {"action": "test.record_cenplus", "data": {"result": "default_fallback"}}
                        ],
                    }
                ],
            }
        ]
    }

    assert await async_setup_component(hass, "automation", config)
    await hass.async_block_till_done()

    # Fire CEN+ short press on button 1, object 8
    hass.bus.async_fire(
        "myhome_cenplus_event",
        {
            "event": CONF_SHORT_PRESS,
            "pushbutton": 1,
            "object": 8,
            "gateway_mac": "00:03:50:aa:bb:cc",
        },
    )
    await hass.async_block_till_done()
    assert events_received == ["matched_btn1"]

    # Fire CEN+ short press on button 2, object 8
    hass.bus.async_fire(
        "myhome_cenplus_event",
        {
            "event": CONF_SHORT_PRESS,
            "pushbutton": 2,
            "object": 8,
            "gateway_mac": "00:03:50:aa:bb:cc",
        },
    )
    await hass.async_block_till_done()
    assert events_received == ["matched_btn1", "matched_btn2"]

    # Button 3 has no trigger: nothing fires, so no default branch either
    hass.bus.async_fire(
        "myhome_cenplus_event",
        {
            "event": CONF_SHORT_PRESS,
            "pushbutton": 3,
            "object": 8,
            "gateway_mac": "00:03:50:aa:bb:cc",
        },
    )
    await hass.async_block_till_done()
    assert events_received == ["matched_btn1", "matched_btn2"]


@pytest.mark.asyncio
@pytest.mark.parametrize("trigger_info", [{"trigger_data": None}, {"name": "legacy"}, {"trigger_data": {"id": "x"}}])
async def test_async_attach_trigger_tolerates_missing_trigger_data(hass: HomeAssistant, trigger_info):
    """A None, absent or populated trigger_data never stops the action from running (#445)."""
    action = AsyncMock()
    await async_attach_trigger(
        hass, {CONF_TYPE: CONF_SHORT_PRESS, CONF_SUBTYPE: "button_1", CONF_ADDRESS: 8}, action, trigger_info
    )
    context = Context()
    hass.bus.async_fire(
        "myhome_cenplus_event",
        {"event": CONF_SHORT_PRESS, "pushbutton": 1, "object": 8},
        context=context,
    )
    await hass.async_block_till_done()

    action.assert_called_once()
    trigger = action.call_args[0][0]["trigger"]
    assert trigger["platform"] == "device"
    assert action.call_args[0][1] == context
    if trigger_info.get("trigger_data"):
        assert trigger["id"] == "x"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("identifier", "own_event", "other_event"),
    [
        ("00:03:50:aa:bb:cc-15-8", "myhome_cen_event", "myhome_cenplus_event"),
        ("00:03:50:aa:bb:cc-25-8", "myhome_cenplus_event", "myhome_cen_event"),
        ("cen_8", "myhome_cen_event", "myhome_cenplus_event"),
        ("cenplus_8", "myhome_cenplus_event", "myhome_cen_event"),
        ("00:03:50:aa:bb:cc-cen-8", "myhome_cen_event", "myhome_cenplus_event"),
        ("00:03:50:aa:bb:cc-cenplus-8", "myhome_cenplus_event", "myhome_cen_event"),
    ],
)
async def test_device_trigger_only_fires_on_own_family(
    hass: HomeAssistant, identifier: str, own_event: str, other_event: str
):
    """CEN and CEN+ objects are separate address spaces (#601)."""
    mock_device = MagicMock()
    mock_device.identifiers = {(DOMAIN, identifier)}
    mock_device.connections = set()
    mock_registry = MagicMock()
    mock_registry.async_get.return_value = mock_device
    action = AsyncMock()
    payload = {"event": CONF_SHORT_PRESS, "pushbutton": 1, "object": 8}

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr("homeassistant.helpers.device_registry.async_get", lambda h: mock_registry)
        unsub = await async_attach_trigger(
            hass,
            {CONF_DEVICE_ID: "dev", CONF_TYPE: CONF_SHORT_PRESS, CONF_SUBTYPE: "button_1"},
            action,
            {"name": "t"},
        )

    hass.bus.async_fire(other_event, payload)
    await hass.async_block_till_done()
    action.assert_not_called()

    hass.bus.async_fire(own_event, payload)
    await hass.async_block_till_done()
    action.assert_called_once()

    action.reset_mock()
    unsub()
    hass.bus.async_fire(own_event, payload)
    await hass.async_block_till_done()
    action.assert_not_called()


@pytest.mark.asyncio
async def test_bare_address_trigger_still_matches_both_families(hass: HomeAssistant):
    """Without a device there is no family to enforce, so both streams match."""
    action = AsyncMock()
    unsub = await async_attach_trigger(
        hass,
        {CONF_TYPE: CONF_SHORT_PRESS, CONF_SUBTYPE: "button_1", CONF_ADDRESS: 8},
        action,
        {"name": "t"},
    )
    payload = {"event": CONF_SHORT_PRESS, "pushbutton": 1, "object": 8}
    hass.bus.async_fire("myhome_cen_event", payload)
    hass.bus.async_fire("myhome_cenplus_event", payload)
    await hass.async_block_till_done()
    assert action.call_count == 2
    unsub()


@pytest.mark.asyncio
async def test_gateway_device_button_trigger_matches_both_families(hass: HomeAssistant):
    """A MAC-only gateway has no family: the button trigger keeps both streams."""
    mock_device = MagicMock()
    mock_device.identifiers = {(DOMAIN, "00:03:50:aa:bb:cc")}
    mock_device.connections = set()
    mock_registry = MagicMock()
    mock_registry.async_get.return_value = mock_device
    action = AsyncMock()
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr("homeassistant.helpers.device_registry.async_get", lambda h: mock_registry)
        unsub = await async_attach_trigger(
            hass,
            {CONF_DEVICE_ID: "gw", CONF_TYPE: CONF_SHORT_PRESS, CONF_SUBTYPE: "button_1"},
            action,
            {"name": "t"},
        )
    payload = {"event": CONF_SHORT_PRESS, "pushbutton": 1, "object": 8}
    hass.bus.async_fire("myhome_cen_event", payload)
    hass.bus.async_fire("myhome_cenplus_event", payload)
    await hass.async_block_till_done()
    assert action.call_count == 2
    unsub()


@pytest.mark.asyncio
async def test_missing_device_fails_closed(hass: HomeAssistant, caplog: pytest.LogCaptureFixture):
    """If the device cannot be resolved the family is unknown: fire on neither (#601)."""
    mock_registry = MagicMock()
    mock_registry.async_get.return_value = None
    action = AsyncMock()
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr("homeassistant.helpers.device_registry.async_get", lambda h: mock_registry)
        unsub = await async_attach_trigger(
            hass,
            {CONF_DEVICE_ID: "gone", CONF_TYPE: CONF_SHORT_PRESS, CONF_SUBTYPE: "button_1", CONF_ADDRESS: 8},
            action,
            {"name": "t"},
        )
    payload = {"event": CONF_SHORT_PRESS, "pushbutton": 1, "object": 8}
    hass.bus.async_fire("myhome_cen_event", payload)
    hass.bus.async_fire("myhome_cenplus_event", payload)
    await hass.async_block_till_done()
    action.assert_not_called()
    assert "device gone not found" in caplog.text
    unsub()


@pytest.mark.asyncio
async def test_family_filter_combines_with_gateway_mac_mismatch(hass: HomeAssistant):
    """A CEN+ device trigger ignores other gateways even on its own family (#601)."""
    mock_device = MagicMock()
    mock_device.identifiers = {(DOMAIN, "00:03:50:aa:bb:cc-25-8")}
    mock_device.connections = set()
    mock_registry = MagicMock()
    mock_registry.async_get.return_value = mock_device
    action = AsyncMock()

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr("homeassistant.helpers.device_registry.async_get", lambda h: mock_registry)
        unsub = await async_attach_trigger(
            hass,
            {CONF_DEVICE_ID: "dev", CONF_TYPE: CONF_SHORT_PRESS, CONF_SUBTYPE: "button_1"},
            action,
            {"name": "t"},
        )

    base = {"event": CONF_SHORT_PRESS, "pushbutton": 1, "object": 8}
    hass.bus.async_fire("myhome_cenplus_event", {**base, "gateway_mac": "00:03:50:11:11:11"})
    await hass.async_block_till_done()
    action.assert_not_called()

    hass.bus.async_fire("myhome_cenplus_event", {**base, "gateway_mac": "00:03:50:aa:bb:cc"})
    await hass.async_block_till_done()
    action.assert_called_once()
    unsub()
