"""Auto-discovered dimmer (no myhome.yaml entry) with the fine-dimmer preset echo (#851).

The light is created from bus traffic alone, promoted to brightness by its first
exact Dimension 1 frame, and a later coarse WHAT 2..10 echo shows the mapped
preset and asks Dimension 1 once, exactly as for a configured light. A dimmer
discovered through a preset first keeps the #466 / #612 behaviour: mapped value,
bus state incomplete, no request of its own (the startup sweep asks).
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, patch

from homeassistant.components.light import ATTR_BRIGHTNESS
from homeassistant.components.light.const import ColorMode
from homeassistant.const import (
    CONF_HOST,
    CONF_MAC,
    CONF_NAME,
    CONF_PASSWORD,
    CONF_PORT,
    STATE_ON,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from OWNd.message import OWNEvent, OWNMessage
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.myhome.const import (
    CONF_DEVICE_TYPE,
    CONF_ENTITY,
    CONF_FIRMWARE,
    CONF_MANUFACTURER,
    CONF_SSDP_LOCATION,
    DOMAIN,
    percent_to_eight_bits,
)
from custom_components.myhome.gateway import MyHOMEGatewayHandler

MAC = "00:03:50:00:08:51"


async def _setup(hass: HomeAssistant) -> MyHOMEGatewayHandler:
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="F461 Gateway",
        data={
            CONF_HOST: "192.0.2.92",
            CONF_PORT: 20000,
            CONF_PASSWORD: None,
            CONF_MAC: MAC,
            CONF_NAME: "F461",
            CONF_DEVICE_TYPE: "urn:schemas-bticino-it:device:webserver:1",
            CONF_MANUFACTURER: "BTicino S.p.A.",
            CONF_FIRMWARE: "2.0.11",
            CONF_SSDP_LOCATION: "http://192.0.2.92:49152/description.xml",
        },
        unique_id=MAC,
    )
    entry.add_to_hass(hass)
    with (
        patch(
            "custom_components.myhome.gateway.OWNSession.test_connection",
            return_value={"Success": True, "Message": None},
        ),
        patch("custom_components.myhome.gateway.MyHOMEGatewayHandler.listening_loop"),
        patch("custom_components.myhome.gateway.MyHOMEGatewayHandler.sending_loop"),
    ):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
    handler: MyHOMEGatewayHandler = hass.data[DOMAIN][MAC][CONF_ENTITY]
    handler._on_event_connection_state_change(True)
    return handler


async def _feed(hass: HomeAssistant, handler: MyHOMEGatewayHandler, raw: str) -> None:
    msg = OWNEvent.parse(raw) or OWNMessage.parse(raw)
    assert msg is not None
    await handler._event_dispatcher.process_message(msg)
    await hass.async_block_till_done()


def _light_0010(hass: HomeAssistant):
    eid = er.async_get(hass).async_get_entity_id("light", DOMAIN, f"{MAC}-1-0010")
    assert eid is not None
    state = hass.states.get(eid)
    assert state is not None
    return eid, state


async def test_autodiscovered_dimmer_follows_the_live_f461_sequence(hass: HomeAssistant) -> None:
    handler = await _setup(hass)
    sent: list[str] = []

    async def _record(cmd: Any, *_: Any, **__: Any) -> bool:
        sent.append(str(cmd))
        return True

    with patch.object(handler, "send_status_request", AsyncMock(side_effect=_record)):
        # the sweep's on/off reply creates the light; no brightness yet
        await _feed(hass, handler, "*1*1*0010##")
        eid, state = _light_0010(hass)
        assert state.state == STATE_ON

        # the exact level promotes it to a brightness light (bus-learned dimmer)
        await _feed(hass, handler, "*#1*0010*1*153*4##")
        state = hass.states.get(eid)
        assert state is not None
        assert state.attributes[ATTR_BRIGHTNESS] == percent_to_eight_bits(53)
        assert ColorMode.BRIGHTNESS in state.attributes["supported_color_modes"]
        before = len(sent)

        # the status reply's coarse preset: mapped value shown, Dimension 1 asked once
        await _feed(hass, handler, "*1*7*0010##")
        state = hass.states.get(eid)
        assert state is not None
        assert state.attributes[ATTR_BRIGHTNESS] == percent_to_eight_bits(50)
        assert sent[before:] == ["*#1*0010*1##"]

        # the reply the refresh asked for restores the exact level
        await _feed(hass, handler, "*#1*0010*1*153*4##")
        state = hass.states.get(eid)
        assert state is not None
        assert state.attributes[ATTR_BRIGHTNESS] == percent_to_eight_bits(53)
        assert sent[before:] == ["*#1*0010*1##"]


async def test_dimmer_discovered_through_a_preset_first_is_unchanged(hass: HomeAssistant) -> None:
    handler = await _setup(hass)
    sent: list[str] = []

    async def _record(cmd: Any, *_: Any, **__: Any) -> bool:
        sent.append(str(cmd))
        return True

    with patch.object(handler, "send_status_request", AsyncMock(side_effect=_record)):
        await _feed(hass, handler, "*1*7*0010##")
        eid, state = _light_0010(hass)
        assert state.state == STATE_ON
        assert state.attributes[ATTR_BRIGHTNESS] == percent_to_eight_bits(50)
        assert ColorMode.BRIGHTNESS in state.attributes["supported_color_modes"]
        # no exact level yet: nothing marks it fine, the startup sweep polls (#466, #612)
        assert "*#1*0010*1##" not in sent
