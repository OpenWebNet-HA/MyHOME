"""Tests for MyHOME device triggers."""
from unittest.mock import AsyncMock, MagicMock

import pytest
from homeassistant.const import (
    CONF_DEVICE_ID,
    CONF_DOMAIN,
    CONF_PLATFORM,
    CONF_TYPE,
)
from homeassistant.core import HomeAssistant

from custom_components.myhome.const import (
    CONF_LONG_PRESS,
    CONF_ROTARY_CCW_FAST,
    CONF_ROTARY_CW_FAST,
    CONF_SHORT_PRESS,
    DOMAIN,
)
from custom_components.myhome.device_trigger import (
    CONF_ADDRESS,
    CONF_SUBTYPE,
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

        assert len(triggers) == len(TRIGGER_TYPES) * len(TRIGGER_SUBTYPES)
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

        assert len(triggers) == len(TRIGGER_TYPES) * len(TRIGGER_SUBTYPES)
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

        assert len(triggers) == len(TRIGGER_TYPES) * len(TRIGGER_SUBTYPES)
        for trigger in triggers:
            assert trigger[CONF_ADDRESS] == 12
            assert trigger[CONF_DEVICE_ID] == "cenplus_device_id"


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
    hass.bus.async_fire(
        "myhome_cen_event",
        {
            "event": CONF_SHORT_PRESS,
            "pushbutton": 1,
            "object": 5,
        },
    )
    await hass.async_block_till_done()
    action.assert_called_once()
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
    hass.bus.async_fire(
        "myhome_cenplus_event",
        {
            "event": CONF_SHORT_PRESS,
            "pushbutton": 1,
            "object": 5,
        },
    )
    await hass.async_block_till_done()
    action.assert_called_once()

    unsub()


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


