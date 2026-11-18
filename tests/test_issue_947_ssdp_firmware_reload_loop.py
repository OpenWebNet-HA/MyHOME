"""Regression tests for #947: an SSDP announcement reloaded the entry again and again.

On MyHOME 2.0.0 the SSDP step wrote the announced UPnP ``modelNumber`` into the ``firmware``
of the existing entry, and the gateway handler wrote the firmware of the WHO=13 dimension 16
answer (``*#13**16*M*m*b##``) into the same key after every start. Where the two values
differ, each announcement changed the entry, and Home Assistant reloads a loaded entry whose
data an SSDP step has changed.

The two values of the tests are the ones of the MH200N fixtures of issue 466: the entry
created over SSDP stores ``1.0`` (``config_entry-myhome-80a1577fb7ae6f68f05e0cc5a1ead27d.json``)
and the gateway answers ``*#13**16*1*1*8##``
(``myhome_sweep_MH200N_all_2026-11-02T12-53-15.json``).
"""

from __future__ import annotations

from typing import Any
from unittest.mock import patch

import pytest
from homeassistant import config_entries
from homeassistant.config_entries import ConfigEntryState
from homeassistant.const import CONF_FRIENDLY_NAME, CONF_HOST, CONF_MAC, CONF_NAME, CONF_PORT
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from OWNd.message import OWNMessage
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.myhome.const import CONF_FIRMWARE, CONF_UDN, DOMAIN
from custom_components.myhome.gateway import MyHOMEGatewayHandler

MAC = "00:03:50:00:12:34"
HOST = "192.0.2.10"
UPNP_MODEL_NUMBER = "1.0"
FIRMWARE_ANSWER = "*#13**16*1*1*8##"
REPORTED_FIRMWARE = "1.1.8"


class _Announcement:
    """The fields of an SSDP announcement that the step reads."""

    def __init__(self, host: str = HOST, **upnp: Any) -> None:
        self.ssdp_usn = "mock_usn"
        self.ssdp_st = "mock_st"
        self.ssdp_location = f"http://{host}:49153/description.xml"
        self.ssdp_headers = {"_host": host}
        self.upnp = {
            "modelName": "MH200N",
            "serialNumber": MAC,
            "friendlyName": "MH200N",
            "UDN": "uuid:mock",
            "modelNumber": UPNP_MODEL_NUMBER,
            **upnp,
        }


def _entry(hass: HomeAssistant, **data: Any) -> MockConfigEntry:
    """An entry as the SSDP flow creates it, already loaded."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        source=config_entries.SOURCE_SSDP,
        data={
            CONF_HOST: HOST,
            CONF_PORT: 20000,
            CONF_MAC: MAC,
            CONF_NAME: "MH200N",
            CONF_FRIENDLY_NAME: "MH200N",
            CONF_UDN: "uuid:mock",
            CONF_FIRMWARE: UPNP_MODEL_NUMBER,
            **data,
        },
        unique_id=MAC,
    )
    entry.add_to_hass(hass)
    entry.mock_state(hass, ConfigEntryState.LOADED)
    return entry


async def _announce(hass: HomeAssistant, announcement: _Announcement) -> dict[str, Any]:
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_SSDP}, data=announcement
    )
    await hass.async_block_till_done()
    return result


@pytest.fixture
def reloads(hass: HomeAssistant):
    """The entry ids Home Assistant was asked to reload."""
    with patch.object(hass.config_entries, "async_schedule_reload") as schedule:
        yield schedule


async def test_announcements_do_not_reload_after_the_firmware_answer(
    hass: HomeAssistant, reloads
) -> None:
    """The loop of #947: firmware answer, announcement, reload, firmware answer, ..."""
    entry = _entry(hass)
    handler = MyHOMEGatewayHandler(hass, entry)

    # What every start does: the gateway answers *#13**16## and the handler stores it.
    await handler._event_dispatcher.process_message(OWNMessage.parse(FIRMWARE_ANSWER))
    assert entry.data[CONF_FIRMWARE] == REPORTED_FIRMWARE

    for _ in range(3):
        result = await _announce(hass, _Announcement())
        assert result["type"] == FlowResultType.ABORT
        assert result["reason"] == "already_configured"
        assert entry.data[CONF_FIRMWARE] == REPORTED_FIRMWARE

    reloads.assert_not_called()


async def test_announced_model_number_fills_an_entry_without_firmware(
    hass: HomeAssistant, reloads
) -> None:
    """An entry that stores no firmware takes the announced value, once."""
    entry = _entry(hass, **{CONF_FIRMWARE: None})

    await _announce(hass, _Announcement())
    assert entry.data[CONF_FIRMWARE] == UPNP_MODEL_NUMBER
    reloads.assert_called_once_with(entry.entry_id)

    await _announce(hass, _Announcement())
    reloads.assert_called_once_with(entry.entry_id)


async def test_new_address_still_updates_and_reloads(hass: HomeAssistant, reloads) -> None:
    """The reason the step updates the entry at all: the gateway got a new address."""
    entry = _entry(hass, **{CONF_FIRMWARE: REPORTED_FIRMWARE})

    result = await _announce(hass, _Announcement(host="192.0.2.77"))

    assert result["reason"] == "already_configured"
    assert entry.data[CONF_HOST] == "192.0.2.77"
    assert entry.data[CONF_FIRMWARE] == REPORTED_FIRMWARE
    reloads.assert_called_once_with(entry.entry_id)


async def test_new_friendly_name_and_udn_are_still_stored(hass: HomeAssistant, reloads) -> None:
    """The other two announced fields keep following the announcement."""
    entry = _entry(hass, **{CONF_FIRMWARE: REPORTED_FIRMWARE})

    await _announce(hass, _Announcement(friendlyName="Scenario programmer", UDN="uuid:other"))

    assert entry.data[CONF_FRIENDLY_NAME] == "Scenario programmer"
    assert entry.data[CONF_UDN] == "uuid:other"
    assert entry.data[CONF_FIRMWARE] == REPORTED_FIRMWARE
    reloads.assert_called_once_with(entry.entry_id)


async def test_ignored_gateway_is_not_written(hass: HomeAssistant, reloads) -> None:
    """An ignored gateway stays ignored and its entry is left as it is."""
    entry = MockConfigEntry(
        domain=DOMAIN, source=config_entries.SOURCE_IGNORE, data={}, unique_id=MAC
    )
    entry.add_to_hass(hass)
    modified_at = entry.modified_at

    result = await _announce(hass, _Announcement())

    assert result["type"] == FlowResultType.ABORT
    assert result["reason"] == "already_configured"
    assert entry.data == {}
    assert entry.modified_at == modified_at
    reloads.assert_not_called()
