"""Startup requests the firmware forwards, per gateway (own-firmware-oracle verdicts, #185, #257, #674).

- ``*#4*0##`` is refused (not forwarded to the bus) by F450, F454, F459, F460, F461, H4684,
  MH200, MH202 and MyHomeServer1; F453, F453AV and MH200N forward it
  (``results/<P>/<V>/oracle/full/trace-corpus.tsv``). Where it is refused, an SCS-only
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
        ("F454", False, True), ("MH200", False, True), ("H4684", False, True),
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
        ("F459", True),
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
    """*#4*Z#4#II*0## is nack/silent on these SCS-only gateways (own-firmware-oracle PR #60)."""
    handler = _handler(hass, model)
    handler.send_status_request = AsyncMock()
    with caplog.at_level("DEBUG", logger="custom_components.myhome"):
        zone = _zone(hass, handler, interface="01")
        assert zone._polls_temperature is False
        assert zone._polls_temperature is False
    assert zone._poll_on_add is False
    assert handler.interface_zone_request_refused is True
    await zone.async_update()
    handler.send_status_request.assert_not_awaited()
    skipped = [record for record in caplog.records if "drops `*#4*3#4#01*0##`" in record.message]
    assert len(skipped) == 1 and skipped[0].levelname == "DEBUG"


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


async def test_f455_zone_still_sends_nothing(hass):
    handler = _handler(hass, "F455")
    handler.send_status_request = AsyncMock()
    zone = _zone(hass, handler)
    assert zone._poll_on_add is False
    await zone.async_update()
    handler.send_status_request.assert_not_awaited()


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
