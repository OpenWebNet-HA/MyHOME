"""Startup requests the firmware forwards, per gateway (own-firmware-oracle verdicts, #185, #257, #674).

- ``*#4*0##`` is refused (not forwarded to the bus) by F450, F454, F459, F460, F461, H4684,
  MH200, MH202 and MyHomeServer1; F453, F453AV and MH200N forward it
  (``results/<P>/<V>/oracle/full/trace-corpus.tsv``). The MH201 is not in the oracle: a
  physical MH201 3.3.6 answered ``*#4*0##`` with no zone frame (``issue_466`` sweep trace). Where it is refused, an SCS-only
  gateway's zones ask for their temperature one by one (``*#4*Z*0##``), which every
  emulated gateway forwards.
- ``*#16*0*5##`` is refused by F450, F459, F460, F461, MH202 and MyHomeServer1
  (``results/<P>/<V>/oracle/full/sound-who16-source.tsv``).
- A restored ``current_temperature`` is flagged ``current_temperature_restored`` until the
  zone reports again.
"""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from homeassistant.const import CONF_HOST, CONF_MAC, CONF_NAME, CONF_PASSWORD, CONF_PORT
from homeassistant.core import State
from OWNd.message import OWNHeatingEvent
from OWNd.profiles import get_gateway_profile

from custom_components.myhome.climate import MyHOMEClimate
from custom_components.myhome.const import CONF_FIRMWARE
from custom_components.myhome.discovery import message_has_state
from custom_components.myhome.gateway import MyHOMEGatewayHandler

MAC = "00:03:50:00:01:85"


def _entry(model: str) -> MagicMock:
    entry = MagicMock()
    entry.entry_id = f"entry-{model}"
    entry.data = {
        CONF_HOST: "192.0.2.185", CONF_PORT: 20000, CONF_PASSWORD: "x",
        CONF_NAME: model, CONF_FIRMWARE: "2.0.11", CONF_MAC: MAC,
    }
    entry.runtime_data = None
    return entry


def _handler(hass, model: str) -> MyHOMEGatewayHandler:
    handler = MyHOMEGatewayHandler(hass, _entry(model))
    handler.gateway.profile = get_gateway_profile(model)
    return handler


def _zone(hass, handler, where="3", **kwargs) -> MyHOMEClimate:
    zone = MyHOMEClimate(
        hass=hass, name=f"Zone {where}", device_id=f"4-{where}", who="4", where=where,
        heating=True, cooling=False, fan=False, standalone=True, central=False,
        manufacturer="BTicino", model="Heating Zone", gateway=handler, **kwargs,
    )
    zone.hass = hass
    zone.entity_id = f"climate.zone_{where}"
    zone.async_write_ha_state = MagicMock()
    return zone


# ------------------------------------------------------------------ startup sweep

@pytest.mark.parametrize(
    ("model", "zone_sweep", "sound"),
    [
        ("F461", False, False), ("MyHomeServer1", False, False), ("MH202", False, False),
        ("F459", False, False), ("F460", False, False), ("F450", False, False),
        ("F454", False, True), ("MH200", False, True), ("MH201", False, True),
        ("H4684", False, True), ("HL4684", False, True),
        ("F453", True, True), ("F453AV", True, True), ("MH200N", True, True), ("Generic", True, True),
    ],
)
def test_sweep_skips_requests_the_gateway_refuses(hass, model, zone_sweep, sound):
    frames = _handler(hass, model)._discovery_frames()
    assert ("*#4*0##" in frames) is zone_sweep
    assert ("*#16*0*5##" in frames) is sound
    assert frames[:2] == ["*#1*0##", "*#2*0##"]


def test_f461_sweep_in_full(hass):
    assert _handler(hass, "F461")._discovery_frames() == ["*#1*0##", "*#2*0##", "*#18*51*51##", "*#18*51*113##"]


@pytest.mark.parametrize(
    ("force", "general"),
    [
        (False, ["*#1*0##", "*#2*0##"]),  # the F461 firmware drops *#4*0## and *#16*0*5##, no WHO 5 in the profile
        (True, ["*#1*0##", "*#2*0##", "*#4*0##", "*#5*0##", "*#16*0*5##"]),
    ],
)
async def test_sweep_bus_leaves_out_the_refused_requests_unless_forced(hass, attach_gateway, force, general):
    """``myhome.sweep_bus`` applies the startup sweep's firmware gate; ``force`` sends everything."""
    from custom_components.myhome.const import ATTR_GATEWAY, DOMAIN
    from custom_components.myhome.services import SERVICE_SWEEP_BUS, async_setup_services

    await async_setup_services(hass)
    handler = _handler(hass, "F461")
    handler.send_status_request = AsyncMock()
    handler.send_paced = AsyncMock()
    attach_gateway(MAC, handler)
    await hass.services.async_call(DOMAIN, SERVICE_SWEEP_BUS, {ATTR_GATEWAY: MAC, "force": force}, blocking=True)
    handler.send_paced.assert_awaited_once_with(general)


@pytest.mark.parametrize(
    ("model", "profile_model"),
    [("L4684", "H4684"), ("NT4684", "H4684"), ("003598", "F454"), ("03535", "MH202")],
)
def test_aliased_model_strings_follow_their_profile(hass, model, profile_model):
    """OWNd resolves these spellings to a profile; the SCS-only gate follows the profile's model name."""
    handler = _handler(hass, model)
    assert handler.gateway.profile.model_name == profile_model
    assert handler._model_key == profile_model
    assert handler.supports_zone_status is False
    assert handler.zone_temperature_poll_needed is True


def test_ssdp_model_string_hl4684_is_keyed_as_h4684(hass):
    """SSDP announces the H4684 as ``HL4684`` (manifest.json); OWNd has no alias for it yet."""
    handler = _handler(hass, "HL4684")
    assert handler.gateway.profile.model_name == "HL4684"  # the Generic profile keeps the SSDP string
    assert handler._model_key == "H4684"
    assert handler.supports_zone_status is False
    assert handler.general_zone_request_refused is True
    assert handler.sound_status_request_refused is False
    assert handler.interface_zone_request_refused is False


@pytest.mark.parametrize("flag", [True, False])
def test_profile_flag_for_the_general_heating_status_wins(hass, flag):
    """An OWNd profile that states ``supports_general_heating_status`` is believed over the table."""
    handler = _handler(hass, "F453" if not flag else "F461")
    handler.gateway.profile = SimpleNamespace(
        model_name=handler.gateway.profile.model_name,
        supports_general_heating_status=flag,
        supports_who=lambda who: True,
    )
    assert handler.general_zone_request_refused is (not flag)
    assert ("*#4*0##" in handler._discovery_frames()) is flag


def test_profile_without_who_16_skips_the_sound_request(hass):
    handler = _handler(hass, "F454")
    handler.gateway.profile = SimpleNamespace(model_name="F454", supports_who=lambda who: who != 16)
    assert handler.sound_status_request_refused is True
    assert "*#16*0*5##" not in handler._discovery_frames()


# ------------------------------------------------------------------ zone temperature (#185)

@pytest.mark.parametrize(
    ("model", "expected"),
    [
        ("MH201", True), ("MH202", True), ("F450", True), ("F454", True), ("MH200", True), ("H4684", True),
        ("HL4684", True), ("F459", True),
        ("F455", False), ("F453", False), ("MH200N", False),  # unknown, or the sweep answers
        ("F460", False), ("F461", False), ("MyHomeServer1", False), ("Generic", False),  # polled with *#4*Z##
    ],
)
def test_which_gateways_need_a_zone_temperature_poll(hass, model, expected):
    assert _handler(hass, model).zone_temperature_poll_needed is expected


@pytest.mark.parametrize("model", ["MH201", "MH202"])
async def test_scs_only_zone_asks_for_its_temperature(hass, model):
    handler = _handler(hass, model)
    handler.send_status_request = AsyncMock()
    zone = _zone(hass, handler)
    assert zone._poll_on_add is True
    await zone.async_update()
    assert [str(call.args[0]) for call in handler.send_status_request.await_args_list] == ["*#4*3*0##"]


@pytest.mark.parametrize("model", ["F450", "F454", "F459", "MH202"])
async def test_interface_zone_is_not_polled_where_the_firmware_drops_the_request(hass, model, caplog):
    """*#4*Z#4#II*0## is nack/silent on these SCS-only gateways (own-firmware-oracle PR #60).

    ``_polls_temperature`` is a pure predicate: construction and reading it log nothing; the
    one-shot DEBUG line is written by ``async_update`` when it actually withholds the poll.
    """
    handler = _handler(hass, model)
    handler.send_status_request = AsyncMock()

    def skipped() -> list:
        return [record for record in caplog.records if "drops `*#4*3#4#01*0##`" in record.message]

    with caplog.at_level("DEBUG", logger="custom_components.myhome"):
        zone = _zone(hass, handler, interface="01")
        assert zone._polls_temperature is False
        assert zone._interface_poll_refused is True
        assert zone._poll_on_add is False
        assert handler.interface_zone_request_refused is True
        assert skipped() == []  # nothing logged in __init__ or by the property
        await zone.async_update()
        handler.send_status_request.assert_not_awaited()
        assert len(skipped()) == 1 and skipped()[0].levelname == "DEBUG"
        await zone.async_update()
        handler.send_status_request.assert_not_awaited()
        assert len(skipped()) == 1  # once per entity


async def test_withheld_interface_poll_is_logged_even_when_humidity_keeps_the_update_going(hass, caplog):
    """A humidity zone behind an F422 on an F454 still sends its *60## request; the withheld *0## is logged once."""
    handler = _handler(hass, "F454")
    handler.send_status_request = AsyncMock()
    zone = _zone(hass, handler, interface="01", humidity=True)
    assert zone._polls_temperature is False and zone._interface_poll_refused is True
    with caplog.at_level("DEBUG", logger="custom_components.myhome"):
        await zone.async_update()
        await zone.async_update()
    sent = [str(call.args[0]) for call in handler.send_status_request.await_args_list]
    assert sent == ["*#4*3#4#01*60##", "*#4*3#4#01*60##"]
    assert len([record for record in caplog.records if "drops `*#4*3#4#01*0##`" in record.message]) == 1


@pytest.mark.parametrize("model", ["H4684", "MH200", "MH201"])
async def test_interface_zone_is_polled_where_the_firmware_forwards_the_request(hass, model):
    """H4684 and MH200 forward *#4*Z#4#II*0## (own-firmware-oracle PR #60); the MH201 is not in the oracle."""
    handler = _handler(hass, model)
    handler.send_status_request = AsyncMock()
    zone = _zone(hass, handler, interface="01")
    assert zone._poll_on_add is True
    await zone.async_update()
    assert [str(call.args[0]) for call in handler.send_status_request.await_args_list] == ["*#4*3#4#01*0##"]


@pytest.mark.parametrize(
    ("model", "flag", "expected"),
    [("MH202", True, True), ("H4684", False, False)],
)
async def test_profile_flag_wins_over_the_interface_zone_table(hass, model, flag, expected):
    """An OWNd profile that states ``supports_interface_zone_requests`` is believed over the table."""
    handler = _handler(hass, model)
    handler.gateway.profile = SimpleNamespace(
        model_name=handler.gateway.profile.model_name,
        supports_interface_zone_requests=flag,
        supports_who=lambda who: True,
    )
    assert handler.interface_zone_request_refused is (not flag)
    zone = _zone(hass, handler, interface="01")
    assert zone._polls_temperature is expected


async def test_f455_zone_still_sends_nothing(hass, caplog):
    handler = _handler(hass, "F455")
    handler.send_status_request = AsyncMock()
    zone = _zone(hass, handler, interface="01")
    assert zone._poll_on_add is False
    assert zone._interface_poll_refused is False  # no temperature poll wanted, so nothing is "withheld"
    with caplog.at_level("DEBUG", logger="custom_components.myhome"):
        await zone.async_update()
    handler.send_status_request.assert_not_awaited()
    assert not [record for record in caplog.records if "firmware drops" in record.message]


async def test_central_unit_is_not_a_temperature_poll(hass):
    handler = _handler(hass, "MH202")
    handler.send_status_request = AsyncMock()
    central = MyHOMEClimate(
        hass=hass, name="Central", device_id="4-#0", who="4", where="#0",
        heating=True, cooling=False, fan=False, standalone=False, central=True,
        manufacturer="BTicino", model="Central Unit", gateway=handler,
    )
    assert central._polls_temperature is False


def test_bus_discovered_zone_keeps_its_poll_until_a_temperature_arrives(hass):
    polled = _zone(hass, _handler(hass, "MH202"))
    assert polled._polls_temperature is True
    assert message_has_state(polled, OWNHeatingEvent("*#4*3*14*0210*3##")) is False
    assert message_has_state(polled, OWNHeatingEvent("*#4*3*0*0215##")) is True
    silent = _zone(hass, _handler(hass, "F455"))
    assert message_has_state(silent, OWNHeatingEvent("*#4*3*14*0210*3##")) is True


# ------------------------------------------------------------------ restored temperature

async def test_current_temperature_is_restored_and_flagged_until_the_bus_reports(hass):
    zone = _zone(hass, _handler(hass, "MH202"))
    await zone.async_restore_last_state(
        State("climate.zone_3", "heat", {"temperature": 21.0, "current_temperature": 23.9})
    )
    assert zone.current_temperature == 23.9
    assert zone.extra_state_attributes["current_temperature_restored"] is True
    zone.handle_event(OWNHeatingEvent("*#4*3*0*0241##"))
    assert zone.current_temperature == 24.1
    assert "current_temperature_restored" not in zone.extra_state_attributes


async def test_a_malformed_restored_temperature_is_ignored(hass):
    zone = _zone(hass, _handler(hass, "MH202"))
    await zone.async_restore_last_state(State("climate.zone_3", "heat", {"current_temperature": "warm"}))
    assert zone.current_temperature is None
    assert "current_temperature_restored" not in zone.extra_state_attributes


@pytest.mark.parametrize(
    ("model", "flag", "expected"),
    [
        ("F454", True, False),  # listed in the fallback table, but the profile says the sweep is answered
        ("MH201", True, False),  # the #185 field-report entry yields to a stated profile flag too
        ("F453", False, True),  # not in the table; the profile says the sweep is refused
        ("F461", False, False),  # polled with *#4*Z##: never a temperature poll
    ],
)
def test_profile_flag_wins_over_the_zone_temperature_poll_table(hass, model, flag, expected):
    handler = _handler(hass, model)
    handler.gateway.profile = SimpleNamespace(
        model_name=handler.gateway.profile.model_name,
        supports_general_heating_status=flag,
        supports_who=lambda who: True,
    )
    assert handler.zone_temperature_poll_needed is expected
