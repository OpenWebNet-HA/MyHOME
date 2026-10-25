"""Zone humidity (dimension 60) is not polled on gateways whose firmware drops the request.

own-firmware-oracle thermo-zone-requests suite (``findings/thermo-zone-requests.md`` on main,
a ``firmware_observed`` verdict on emulated firmware): ``*#4*2*60##`` and ``*#4*0*60##`` were NACKed with nothing
on the bus by the emulated F450, F453, F453AV, F454, H4684, MH200, MH200N and MH202, and
forwarded by F459, F460, F461 and MyHomeServer1. The OWNd profile flag wins when it is a bool.
A live F461 2.0.11 answered ``*#4*2*60##`` with ``*#4*2*60*39##``. On the refusing gateways the
request only costs a command-session round trip; the zone's own pushes keep the humidity fresh.
"""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from homeassistant.const import CONF_HOST, CONF_MAC, CONF_NAME, CONF_PASSWORD, CONF_PORT
from OWNd.profiles import GenericGatewayProfile, get_gateway_profile

from custom_components.myhome.climate import MyHOMEClimate
from custom_components.myhome.const import (
    CONF_FIRMWARE,
    ROLE_PRIMARY,
    ROLE_SECONDARY,
    ROLE_STANDBY,
    TOPOLOGY_SHARED,
)
from custom_components.myhome.gateway import (
    HUMIDITY_REQUEST_REFUSED,
    KNOWN_GATEWAY_MODELS,
    MyHOMEGatewayHandler,
)

MAC = "00:03:50:00:02:57"


def _entry(model: str) -> MagicMock:
    entry = MagicMock()
    entry.entry_id = f"entry-{model}"
    entry.data = {
        CONF_HOST: "192.0.2.57", CONF_PORT: 20000, CONF_PASSWORD: "x",
        CONF_NAME: model, CONF_FIRMWARE: "2.0.11", CONF_MAC: MAC,
    }
    entry.runtime_data = None
    return entry


def _handler(hass, model: str) -> MyHOMEGatewayHandler:
    handler = MyHOMEGatewayHandler(hass, _entry(model))
    handler.gateway.profile = get_gateway_profile(model)
    handler.send_status_request = AsyncMock()
    return handler


def _humidity_zone(hass, gateway) -> MyHOMEClimate:
    zone = MyHOMEClimate(
        hass=hass, name="Zone 2", device_id="4-2", who="4", where="2",
        heating=True, cooling=True, fan=False, humidity=True, standalone=True, central=False,
        manufacturer="BTicino", model="Heating Zone", gateway=gateway,
    )
    zone.hass = hass
    zone.entity_id = "climate.zone_2"
    zone.async_write_ha_state = MagicMock()
    return zone


def _sent(gateway) -> list[str]:
    return [str(call.args[0]) for call in gateway.send_status_request.await_args_list]


@pytest.mark.parametrize(
    ("model", "refused"),
    [
        ("F450", True), ("F453", True), ("F453AV", True), ("F454", True), ("H4684", True),
        ("MH200", True), ("MH200N", True), ("MH202", True),
        ("F459", False), ("F460", False), ("F461", False), ("MyHomeServer1", False),
        ("F455", False), ("MH201", False), ("Generic", False),  # not in the oracle: still asked
    ],
)
def test_humidity_request_refused_follows_the_oracle(hass, model, refused):
    assert _handler(hass, model).humidity_request_refused is refused


@pytest.mark.parametrize("flag", [True, False])
def test_profile_flag_wins_over_the_table(hass, flag):
    """An OWNd profile that states the flag is believed, whatever the fallback table says."""
    handler = _handler(hass, "MH202" if flag else "F461")
    handler.gateway.profile = SimpleNamespace(
        model_name=handler.gateway.profile.model_name, supports_heating_humidity_request=flag
    )
    assert handler.humidity_request_refused is (not flag)


async def test_f461_zone_still_asks_for_its_humidity(hass):
    handler = _handler(hass, "F461")
    await _humidity_zone(hass, handler).async_update()
    assert _sent(handler) == ["*#4*2##", "*#4*2*60##"]


@pytest.mark.parametrize("model", ["F454", "MH202"])
async def test_refusing_gateway_zone_does_not_ask(hass, model):
    handler = _handler(hass, model)
    await _humidity_zone(hass, handler).async_update()
    assert _sent(handler) == []


async def test_gateway_without_the_property_is_still_asked(hass):
    gateway = MagicMock()
    gateway.mac = MAC
    gateway.send_status_request = AsyncMock()
    gateway.has_central_unit = False
    gateway.supports_zone_status = True
    del gateway.humidity_request_refused
    await _humidity_zone(hass, gateway).async_update()
    assert _sent(gateway) == ["*#4*2##", "*#4*2*60##"]


@pytest.mark.parametrize(
    ("model", "refused"),
    [("MH202", True), ("F454", True), ("F461", False), ("F455", False), ("MH201", False)],
)
def test_profile_flag_none_means_unverified_and_falls_back_to_the_table(hass, model, refused):
    """``None`` = unverified: a listed model is skipped by the table, an unlisted one is polled."""
    handler = _handler(hass, model)
    handler.gateway.profile = SimpleNamespace(model_name=model, supports_heating_humidity_request=None)
    assert handler.humidity_request_refused is refused


@pytest.mark.parametrize(("model", "refused"), [("MH202", True), ("F455", False)])
def test_legacy_profile_without_the_attribute_falls_back_to_the_table(hass, model, refused):
    """An OWNd release whose profiles have no such attribute at all (the pinned one) uses the table."""
    handler = _handler(hass, model)
    handler.gateway.profile = SimpleNamespace(model_name=model)
    assert not hasattr(handler.gateway.profile, "supports_heating_humidity_request")
    assert handler.humidity_request_refused is refused


async def test_unverified_flag_on_an_unlisted_model_still_polls(hass):
    handler = _handler(hass, "F455")
    handler.gateway.profile = SimpleNamespace(model_name="F455", supports_heating_humidity_request=None)
    await _humidity_zone(hass, handler).async_update()
    assert "*#4*2*60##" in _sent(handler)


# ------------------------------------------------------------------ gateway: profile class and model lookup (#792 review, 5)

def test_a_generic_profile_with_a_vendor_prefixed_name_still_finds_its_table(hass):
    """``get_gateway_profile("BTicino F461")`` is a GenericGatewayProfile named "BTicino F461"."""
    handler = _handler(hass, "BTicino F461")
    assert isinstance(handler.gateway.profile, GenericGatewayProfile)
    assert handler._model_key == "F461"  # the same key as #792's tables give it
    assert handler._model_key not in HUMIDITY_REQUEST_REFUSED
    assert handler.humidity_request_refused is False


@pytest.mark.parametrize("model", sorted(KNOWN_GATEWAY_MODELS))
def test_every_known_model_is_keyed_as_itself_behind_a_brand_prefix(hass, model):
    """Forwarding models included: the key is the model, whatever table it is looked up in."""
    handler = _handler(hass, f"BTicino {model}")
    handler.gateway.profile = GenericGatewayProfile(model_name=f"BTicino {model}")
    assert handler._model_key == model
    assert handler.humidity_request_refused is (model in HUMIDITY_REQUEST_REFUSED)


def test_the_oracle_s_twelve_images_are_known_models():
    assert HUMIDITY_REQUEST_REFUSED <= KNOWN_GATEWAY_MODELS
    assert {"F459", "F460", "F461", "MYHOMESERVER1"} <= KNOWN_GATEWAY_MODELS


@pytest.mark.parametrize(
    ("model", "key", "refused"),
    [("Legrand MH200N", "MH200N", True), ("MH200", "MH200", True), ("BTicino F453AV", "F453AV", True), ("Unknown", "UNKNOWN", False)],
)
def test_model_key_reads_the_longest_known_model(hass, model, key, refused):
    handler = _handler(hass, model)
    handler.gateway.profile = GenericGatewayProfile(model_name=model)
    assert handler._model_key == key
    assert handler.humidity_request_refused is refused


def test_a_generic_profile_reads_the_configured_model_not_its_own_name(hass):
    """``get_gateway_profile("Generic")`` names nothing: the configured model decides, and an unknown one is polled."""
    handler = _handler(hass, "Generic")
    assert isinstance(handler.gateway.profile, GenericGatewayProfile)
    assert handler._model_key == "GENERIC"
    assert handler.humidity_request_refused is False


async def test_vendor_prefixed_refusing_gateway_zone_does_not_ask(hass):
    handler = _handler(hass, "Legrand MH200N")
    await _humidity_zone(hass, handler).async_update()
    assert _sent(handler) == []


# ------------------------------------------------------------------ gateway: WHO 4 delegation and warm standby (review 5644219403, 1)

@pytest.mark.parametrize(("primary_model", "secondary_model"), [("MH202", "F461"), ("F454", "MyHomeServer1")])
async def test_who4_delegated_to_a_forwarding_secondary_is_asked(hass, primary_model, secondary_model):
    """The request goes through the secondary WHO 4 is delegated to: its table decides, not the primary's."""
    primary = _handler(hass, primary_model)
    secondary = _handler(hass, secondary_model)
    primary._delegated_target = MagicMock(return_value=secondary)
    assert primary._who4_target() is secondary
    assert primary.humidity_request_refused is False
    await _humidity_zone(hass, primary).async_update()
    assert "*#4*2*60##" in _sent(primary)  # send_status_request itself routes it to the secondary


@pytest.mark.parametrize(("primary_model", "secondary_model"), [("F461", "MH202"), ("MyHomeServer1", "F454")])
async def test_who4_delegated_to_a_refusing_secondary_is_not_asked(hass, primary_model, secondary_model):
    primary = _handler(hass, primary_model)
    secondary = _handler(hass, secondary_model)
    primary._delegated_target = MagicMock(return_value=secondary)
    assert primary.humidity_request_refused is True
    await _humidity_zone(hass, primary).async_update()
    assert "*#4*2*60##" not in _sent(primary)


@pytest.mark.parametrize(("primary_model", "standby_model"), [("F461", "MH202"), ("MyHomeServer1", "F454")])
async def test_failover_to_a_refusing_standby_does_not_ask(hass, primary_model, standby_model):
    """A primary F461 failed over to a standby MH202: *#4*Z*60## would be NACKed by the MH202."""
    primary = _handler(hass, primary_model)
    standby = _handler(hass, standby_model)
    primary._failover_target = MagicMock(return_value=standby)
    assert primary._who4_target() is standby
    assert primary.humidity_request_refused is True
    await _humidity_zone(hass, primary).async_update()
    assert "*#4*2*60##" not in _sent(primary)


@pytest.mark.parametrize(("primary_model", "standby_model"), [("MH202", "F461"), ("F454", "MyHomeServer1")])
async def test_failover_to_a_forwarding_standby_still_asks(hass, primary_model, standby_model):
    primary = _handler(hass, primary_model)
    standby = _handler(hass, standby_model)
    primary._failover_target = MagicMock(return_value=standby)
    assert primary.humidity_request_refused is False
    await _humidity_zone(hass, primary).async_update()
    assert "*#4*2*60##" in _sent(primary)


def test_a_delegated_secondary_is_preferred_to_the_standby(hass):
    """``send_status_request`` asks the delegated secondary first, the standby only without one."""
    primary = _handler(hass, "F461")
    secondary = _handler(hass, "MH202")
    standby = _handler(hass, "MyHomeServer1")
    primary._delegated_target = MagicMock(return_value=secondary)
    primary._failover_target = MagicMock(return_value=standby)
    assert primary._who4_target() is secondary
    assert primary.humidity_request_refused is True


@pytest.mark.parametrize(("model", "refused"), [("MH202", True), ("F461", False)])
def test_without_delegation_or_failover_the_gateway_decides_itself(hass, model, refused):
    """A standalone gateway (no secondary, no standby) is its own WHO 4 target: nothing changes."""
    handler = _handler(hass, model)
    assert handler._who4_target() is handler
    assert handler.humidity_request_refused is refused


async def test_a_configured_secondary_decides_for_the_primary_it_serves(hass):
    """The real topology lookup: a secondary entry with WHO 4 delegated, connected, is the target."""
    from tests.test_multi_gateway import _create_mock_gateway

    _, primary = _create_mock_gateway(hass, "00:03:50:00:02:57", name="MH202", topology=TOPOLOGY_SHARED, role=ROLE_PRIMARY)
    _, secondary = _create_mock_gateway(
        hass, "00:03:50:00:02:58", name="F461", topology=TOPOLOGY_SHARED, role=ROLE_SECONDARY,
        primary_gateway="00:03:50:00:02:57", delegated_whos=[4],
    )
    primary.gateway.profile = get_gateway_profile("MH202")
    secondary.gateway.profile = get_gateway_profile("F461")
    assert primary.humidity_request_refused is True  # the secondary is not connected yet
    secondary.is_connected = True
    assert primary._who4_target() is secondary
    assert primary.humidity_request_refused is False


async def test_a_configured_standby_decides_while_the_primary_is_down(hass):
    from tests.test_multi_gateway import _create_mock_gateway

    _, primary = _create_mock_gateway(hass, "00:03:50:00:02:57", name="F461", topology=TOPOLOGY_SHARED, role=ROLE_PRIMARY)
    _, standby = _create_mock_gateway(
        hass, "00:03:50:00:02:59", name="MH202", topology=TOPOLOGY_SHARED, role=ROLE_STANDBY,
        primary_gateway="00:03:50:00:02:57",
    )
    primary.gateway.profile = get_gateway_profile("F461")
    standby.gateway.profile = get_gateway_profile("MH202")
    standby.is_connected = True
    assert primary.is_connected is False
    assert primary._who4_target() is standby
    assert primary.humidity_request_refused is True
    primary.is_connected = True  # back: its own table again
    assert primary._who4_target() is primary
    assert primary.humidity_request_refused is False
