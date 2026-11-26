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

After the review of #948 the step writes a configured entry only where it must: a new
address always, the friendly name / UDN / firmware only while the entry has none, and
nothing at all for an ignored gateway or one that was added by hand.
"""

from __future__ import annotations

import logging
from typing import Any
from unittest.mock import patch

import pytest
from homeassistant import config_entries
from homeassistant.config_entries import ConfigEntryState
from homeassistant.const import CONF_FRIENDLY_NAME, CONF_HOST, CONF_MAC, CONF_NAME, CONF_PORT
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.helpers.discovery_flow import DiscoveryKey
from OWNd.message import OWNMessage
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.myhome.const import CONF_FIRMWARE, CONF_SSDP_LOCATION, CONF_UDN, DOMAIN
from custom_components.myhome.gateway import MyHOMEGatewayHandler
from custom_components.myhome.identity import IDENTIFICATION_MANUAL

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
            CONF_SSDP_LOCATION: f"http://{HOST}:49153/description.xml",
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
    """Start the flow as Home Assistant's SSDP scanner does, with the discovery key of the UDN."""
    key = DiscoveryKey(domain="ssdp", key=str(announcement.upnp.get("UDN") or "uuid:mock"), version=1)
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_SSDP, "discovery_key": key}, data=announcement
    )
    await hass.async_block_till_done()
    return result


def _found_gateway_at_info(caplog: pytest.LogCaptureFixture) -> list[str]:
    return [r.getMessage() for r in caplog.records if r.levelno >= logging.INFO and "Found gateway" in r.getMessage()]


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


@pytest.mark.parametrize("stored", [None, "", "Unknown", "none", "null"])
async def test_announced_model_number_fills_an_entry_without_firmware(
    hass: HomeAssistant, reloads, stored: str | None
) -> None:
    """An entry that stores no firmware (or a placeholder of an older version) takes the announced value, once."""
    entry = _entry(hass, **{CONF_FIRMWARE: stored})

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


async def test_an_entry_without_the_firmware_key_takes_the_announced_value(hass: HomeAssistant, reloads) -> None:
    entry = _entry(hass)
    hass.config_entries.async_update_entry(
        entry, data={key: value for key, value in entry.data.items() if key != CONF_FIRMWARE}
    )

    await _announce(hass, _Announcement())

    assert entry.data[CONF_FIRMWARE] == UPNP_MODEL_NUMBER
    reloads.assert_called_once_with(entry.entry_id)


async def test_another_friendly_name_or_udn_does_not_reload(hass: HomeAssistant, reloads) -> None:
    """A gateway that announces more than one name or UDN for the same MAC: the stored ones stay."""
    entry = _entry(hass, **{CONF_FIRMWARE: REPORTED_FIRMWARE})

    for friendly_name, udn in (("Scenario programmer", "uuid:other"), ("MH200N", "uuid:mock"), ("Web server", "uuid:third")):
        result = await _announce(hass, _Announcement(friendlyName=friendly_name, UDN=udn))
        assert result["reason"] == "already_configured"
        assert entry.data[CONF_FRIENDLY_NAME] == "MH200N"
        assert entry.data[CONF_UDN] == "uuid:mock"

    reloads.assert_not_called()


async def test_a_missing_friendly_name_and_udn_are_filled_once(hass: HomeAssistant, reloads) -> None:
    """An entry found over SSDP (it has the location) that stores neither takes them, once."""
    entry = _entry(hass, **{CONF_FIRMWARE: REPORTED_FIRMWARE, CONF_FRIENDLY_NAME: None, CONF_UDN: None})

    await _announce(hass, _Announcement(friendlyName="Scenario programmer", UDN="uuid:other"))
    assert entry.data[CONF_FRIENDLY_NAME] == "Scenario programmer"
    assert entry.data[CONF_UDN] == "uuid:other"
    reloads.assert_called_once_with(entry.entry_id)

    await _announce(hass, _Announcement(friendlyName="Web server", UDN="uuid:third"))
    assert entry.data[CONF_FRIENDLY_NAME] == "Scenario programmer"
    assert entry.data[CONF_UDN] == "uuid:other"
    reloads.assert_called_once_with(entry.entry_id)


async def test_fields_the_announcement_does_not_carry_are_not_written(hass: HomeAssistant, reloads) -> None:
    """An empty ``<friendlyName/>``, ``<UDN/>`` or ``<modelNumber/>`` does not empty the stored value."""
    entry = _entry(hass, **{CONF_FIRMWARE: REPORTED_FIRMWARE})
    before = dict(entry.data)

    result = await _announce(hass, _Announcement(friendlyName=None, UDN=None, modelNumber=None))

    assert result["reason"] == "already_configured"
    assert dict(entry.data) == before
    reloads.assert_not_called()


async def test_a_gateway_added_by_hand_is_not_written(hass: HomeAssistant, reloads) -> None:
    """Custom (IP Gateway): no SSDP location and no UDN. Its address, port and source stay the user's."""
    entry = _entry(
        hass,
        **{
            CONF_HOST: "10.20.30.40",  # a routed address, not the one the announcement comes from
            CONF_PORT: 20001,
            CONF_SSDP_LOCATION: None,
            CONF_FRIENDLY_NAME: None,
            CONF_UDN: None,
            CONF_FIRMWARE: REPORTED_FIRMWARE,
            "model_source": IDENTIFICATION_MANUAL,
        },
    )
    before, modified_at = dict(entry.data), entry.modified_at
    handler = MyHOMEGatewayHandler(hass, entry)
    assert handler.identification_source == IDENTIFICATION_MANUAL

    for _ in range(2):
        result = await _announce(hass, _Announcement())
        assert result["type"] == FlowResultType.ABORT
        assert result["reason"] == "already_configured"

    assert dict(entry.data) == before
    assert entry.data[CONF_HOST] == "10.20.30.40"
    assert entry.data[CONF_PORT] == 20001
    assert handler.identification_source == IDENTIFICATION_MANUAL
    reloads.assert_not_called()
    # Home Assistant itself notes the discovery key of the first sighting; nothing else changes.
    assert entry.discovery_keys["ssdp"] == (DiscoveryKey(domain="ssdp", key="uuid:mock", version=1),)
    assert entry.modified_at >= modified_at


async def test_ignored_gateway_is_not_written(hass: HomeAssistant, reloads) -> None:
    """An ignored gateway stays ignored and its data is left as it is.

    Home Assistant appends the discovery key of a first sighting to the entry (and so changes
    ``modified_at`` once per new UDN); the step itself writes nothing, and a second
    announcement with the same key changes nothing at all.
    """
    entry = MockConfigEntry(
        domain=DOMAIN, source=config_entries.SOURCE_IGNORE, data={}, unique_id=MAC
    )
    entry.add_to_hass(hass)

    result = await _announce(hass, _Announcement())

    assert result["type"] == FlowResultType.ABORT
    assert result["reason"] == "already_configured"
    assert entry.data == {}
    assert entry.discovery_keys["ssdp"] == (DiscoveryKey(domain="ssdp", key="uuid:mock", version=1),)
    modified_at = entry.modified_at

    result = await _announce(hass, _Announcement())

    assert result["reason"] == "already_configured"
    assert entry.data == {}
    assert entry.modified_at == modified_at
    reloads.assert_not_called()


async def test_found_gateway_is_logged_at_info_for_a_new_gateway_only(
    hass: HomeAssistant, reloads, caplog: pytest.LogCaptureFixture
) -> None:
    """A configured or ignored gateway is announced every few minutes: DEBUG for those."""
    caplog.set_level(logging.DEBUG, logger="custom_components.myhome")
    _entry(hass, **{CONF_FIRMWARE: REPORTED_FIRMWARE})
    await _announce(hass, _Announcement())
    assert _found_gateway_at_info(caplog) == []
    assert any("SSDP announcement of gateway" in record.getMessage() for record in caplog.records)

    ignored = MockConfigEntry(domain=DOMAIN, source=config_entries.SOURCE_IGNORE, data={}, unique_id="00:03:50:00:56:78")
    ignored.add_to_hass(hass)
    await _announce(hass, _Announcement(serialNumber="00:03:50:00:56:78"))
    assert _found_gateway_at_info(caplog) == []

    result = await _announce(hass, _Announcement(host="192.0.2.99", serialNumber="00:03:50:00:9A:BC"))
    assert result["type"] == FlowResultType.FORM
    assert _found_gateway_at_info(caplog) == ["Found gateway: 192.0.2.99"]


async def test_a_new_address_is_logged_at_info(
    hass: HomeAssistant, reloads, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.INFO, logger="custom_components.myhome")
    _entry(hass, **{CONF_FIRMWARE: REPORTED_FIRMWARE})
    await _announce(hass, _Announcement(host="192.0.2.77"))
    assert any("is announced at 192.0.2.77" in record.getMessage() for record in caplog.records)
