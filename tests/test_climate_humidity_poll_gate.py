"""Zone humidity (dimension 60) is not polled on gateways whose firmware drops the request.

own-firmware-oracle thermo-zone-requests suite (``findings/thermo-zone-requests.md`` on main,
a ``firmware_observed`` verdict on emulated firmware): ``*#4*2*60##`` and ``*#4*0*60##`` were NACKed with nothing
on the bus by the emulated F450, F453, F453AV, F454, H4684, MH200, MH200N and MH202, and
forwarded by F459, F460, F461 and MyHomeServer1. The OWNd profile flag wins when it is a bool.
A live F461 2.0.11 answered ``*#4*2*60##`` with ``*#4*2*60*39##``. On the refusing gateways the
request only costs a command-session round trip; the zone's own pushes keep the humidity fresh.

The "captured frames" section replays rows of two committed traces (the loaders the other
trace tests use): ``f461_live_2026-10-23`` (``*#4*2*60##`` answered ``*#4*2*60*39##`` on the
command session, echoed on the event session, and three spontaneous ``*#4*2*60*38##`` pushes)
and ``myhomeserver1_live_2026-10-24`` (``*#4*39*60##`` answered ``*#4*39*60*51##``).
"""
from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from homeassistant.const import CONF_HOST, CONF_MAC, CONF_NAME, CONF_PASSWORD, CONF_PORT
from OWNd.message import OWNHeatingEvent
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
TRACES = Path(__file__).resolve().parent / "fixtures" / "traces"
F461_LIVE = TRACES / "f461_live_2026-10-23" / "myhome_trace_F461_live_2026-10-23.json"
MHS1_LIVE = TRACES / "myhomeserver1_live_2026-10-24" / "myhome_trace_MyHomeServer1_live_2026-10-24.json"
F461_RUN2 = "run2_reviewer_reads"
F461_RUN6 = "run6_event_session_ha_fixed_integration"
MHS1_RUN3 = "run3_reads_lights_addresses_humidity"


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


# ------------------------------------------------------------------ gateway: SSDP and aliased model strings (#794, review 5644219403, 3)

@pytest.mark.parametrize(
    ("model", "profile_model"),
    [("L4684", "H4684"), ("NT4684", "H4684"), ("003598", "F454"), ("03535", "MH202")],
)
def test_aliased_model_strings_follow_their_profile(hass, model, profile_model):
    """An OWNd alias (L4684, NT4684) or MyHOME_Suite item number (003598, 03535) is keyed on its model."""
    handler = _handler(hass, model)
    assert handler.gateway.profile.model_name == profile_model
    assert handler._model_key == profile_model


def test_ssdp_model_string_hl4684_is_keyed_as_h4684(hass):
    """SSDP announces the H4684 as ``HL4684`` (manifest.json); OWNd has no alias for it yet."""
    handler = _handler(hass, "HL4684")
    assert handler.gateway.profile.model_name == "HL4684"  # the Generic profile keeps the SSDP string
    assert handler._model_key == "H4684"


@pytest.mark.parametrize(
    ("model", "key", "refused"),
    [
        ("HL4684", "H4684", True),  # the SSDP modelName, never overwritten by WHO 13 / 1013
        ("HC4684", "H4684", True),
        ("BTicino L4684", "H4684", True),  # OWNd aliases the word, not the brand-prefixed string
        ("BTicino HL4684", "H4684", True),
        ("Legrand 003598", "F454", True),  # the MyHOME_Suite item number of the F454
        ("Legrand 003565", "MH200N", True),  # ... of the MH200N: not collapsed to MH200
        ("Legrand 003535", "MH202", True),
        ("BTicino MH200", "MH200", True),
        ("BTicino MH200N", "MH200N", True),
        ("Legrand 003594", "F455", False),  # the F455 is not in the oracle: still asked
        ("BTicino L4684 gateway", "H4684", True),
    ],
)
def test_brand_prefixed_aliases_are_resolved_before_the_table_lookup(hass, model, key, refused):
    handler = _handler(hass, model)
    assert isinstance(handler.gateway.profile, GenericGatewayProfile), model
    assert handler._model_key == key
    assert handler.humidity_request_refused is refused


@pytest.mark.parametrize("model", ["HL4684", "BTicino L4684"])
async def test_an_ssdp_or_brand_prefixed_h4684_zone_does_not_ask_for_humidity(hass, model):
    """The zone status poll (``*#4*2##``) is ``supports_zone_status``'s business (#794); dimension 60 is gated here."""
    handler = _handler(hass, model)
    await _humidity_zone(hass, handler).async_update()
    assert "*#4*2*60##" not in _sent(handler)


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


# ------------------------------------------------------------------ climate: nothing to send, nothing to count (review 5644219403, 4)

@pytest.mark.parametrize("model", ["MH202", "F454", "Legrand MH200N"])
async def test_a_refusing_scs_gateway_s_humidity_zone_never_reaches_poll_health(hass, model):
    """No zone status (``*#4*Z##``), no fan, humidity refused: ``async_update`` returns before ``_poll_health``."""
    handler = _handler(hass, model)
    zone = _humidity_zone(hass, handler)
    zone._poll_health.should_skip = MagicMock(side_effect=AssertionError("should_skip must not run"))
    zone._raise_unresponsive_issue = MagicMock()
    await zone.async_update()
    assert _sent(handler) == []
    zone._raise_unresponsive_issue.assert_not_called()


async def test_a_refusing_gateway_with_zone_status_still_counts_its_zone_poll(hass):
    """An F450 refuses dimension 60 but answers ``*#4*Z##``: that poll is still sent and counted."""
    handler = _handler(hass, "F450")
    assert handler.supports_zone_status is True
    zone = _humidity_zone(hass, handler)
    zone._poll_health.should_skip = MagicMock(return_value=False)
    await zone.async_update()
    zone._poll_health.should_skip.assert_called_once()
    assert _sent(handler) == ["*#4*2##"]


async def test_a_forwarding_gateway_s_humidity_zone_goes_through_poll_health(hass):
    handler = _handler(hass, "F461")
    zone = _humidity_zone(hass, handler)
    zone._poll_health.should_skip = MagicMock(return_value=False)
    await zone.async_update()
    zone._poll_health.should_skip.assert_called_once()
    assert _sent(handler) == ["*#4*2##", "*#4*2*60##"]


async def test_a_skipping_zone_on_a_refusing_gateway_is_still_held_back(hass):
    """``should_skip`` still rules where a request would be sent (an F461 zone that stopped answering)."""
    handler = _handler(hass, "F461")
    zone = _humidity_zone(hass, handler)
    zone._poll_health.should_skip = MagicMock(return_value=True)
    zone._raise_unresponsive_issue = MagicMock()
    await zone.async_update()
    assert _sent(handler) == []
    zone._raise_unresponsive_issue.assert_called_once()


def test_humidity_enabled_later_still_polls_on_add_on_a_refusing_gateway(hass):
    """``_poll_on_add`` is untouched: the first update runs so the identity can settle before the gate decides."""
    handler = _handler(hass, "MH202")
    zone = MyHOMEClimate(
        hass=hass, name="Zone 2", device_id="4-2", who="4", where="2",
        heating=True, cooling=True, fan=False, humidity=False, standalone=True, central=False,
        manufacturer="BTicino", model="Heating Zone", gateway=handler,
    )
    assert zone._poll_on_add is False
    zone._enable_humidity()
    assert zone._poll_on_add is True


# ------------------------------------------------------------------ captured frames (review 5644219403, 4.2)

@pytest.fixture(scope="module")
def f461_rows() -> list[dict]:
    assert F461_LIVE.is_file(), F461_LIVE
    return json.loads(F461_LIVE.read_text(encoding="utf-8"))["rows"]


@pytest.fixture(scope="module")
def mhs1_rows() -> list[dict]:
    assert MHS1_LIVE.is_file(), MHS1_LIVE
    return json.loads(MHS1_LIVE.read_text(encoding="utf-8"))["rows"]


def _result(rows: list[dict], run: str, frame: str) -> dict:
    found = [r for r in rows if r["dir"] == "result" and r["run"] == run and r["frame"] == frame]
    assert len(found) == 1, (run, frame, len(found))
    return found[0]


def _evt(rows: list[dict], run: str, frame: str) -> list[dict]:
    return [r for r in rows if r["run"] == run and r["dir"] == "rx" and r["session"] == "evt" and r["frame"] == frame]


def test_f461_capture_answers_the_humidity_request(f461_rows):
    """F461 2.0.11, zone 2: ``*#4*2*60##`` is ACKed with ``*#4*2*60*39##`` in 0.14 s, echoed on the event session."""
    result = _result(f461_rows, F461_RUN2, "*#4*2*60##")
    assert result["ack"] is True
    assert result["replies"] == ["*#4*2*60*39##"]
    sent = next(r for r in f461_rows if r["run"] == F461_RUN2 and r["dir"] == "tx" and r["frame"] == "*#4*2*60##")
    assert 0 < result["t"] - sent["t"] < 0.5
    assert len(_evt(f461_rows, F461_RUN2, "*#4*2*60*39##")) == 2  # one echo per request of the run
    assert len(_evt(f461_rows, F461_RUN6, "*#4*2*60*38##")) == 3  # spontaneous pushes, 30 minutes of HA


async def test_captured_f461_reply_and_pushes_update_the_zone(hass, f461_rows):
    """The captured reply and the three spontaneous pushes replayed through the entity."""
    handler = _handler(hass, "F461")
    zone = _humidity_zone(hass, handler)
    await zone.async_update()
    assert _sent(handler) == ["*#4*2##", "*#4*2*60##"]
    for frame in _result(f461_rows, F461_RUN2, "*#4*2*60##")["replies"]:
        zone.handle_event(OWNHeatingEvent(frame))
    assert zone.current_humidity == 39.0
    for row in _evt(f461_rows, F461_RUN6, "*#4*2*60*38##"):
        zone.handle_event(OWNHeatingEvent(row["frame"]))
    assert zone.current_humidity == 38.0


async def test_captured_pushes_keep_a_refusing_gateway_s_zone_fresh_without_a_poll(hass, f461_rows):
    """What the gate relies on: the probe's own ``*#4*Z*60*HH##`` pushes, here the F461's, enable and refresh humidity."""
    handler = _handler(hass, "MH202")
    zone = MyHOMEClimate(
        hass=hass, name="Zone 2", device_id="4-2", who="4", where="2",
        heating=True, cooling=True, fan=False, humidity=False, standalone=True, central=False,
        manufacturer="BTicino", model="Heating Zone", gateway=handler,
    )
    zone.hass = hass
    zone.entity_id = "climate.zone_2"
    zone.async_write_ha_state = MagicMock()
    assert zone.current_humidity is None
    for row in _evt(f461_rows, F461_RUN6, "*#4*2*60*38##"):
        zone.handle_event(OWNHeatingEvent(row["frame"]))
    assert zone._humidity is True
    assert zone.current_humidity == 38.0
    await zone.async_update()
    assert _sent(handler) == []  # humidity enabled by the push, still not polled on the MH202


def test_myhomeserver1_capture_answers_the_humidity_request(mhs1_rows):
    """MyHomeServer1 3.87.13, zone 39: ``*#4*39*60##`` is ACKed with ``*#4*39*60*51##`` in 0.2 s."""
    result = _result(mhs1_rows, MHS1_RUN3, "*#4*39*60##")
    assert result["ack"] is True
    assert result["replies"] == ["*#4*39*60*51##"]
    assert len(_evt(mhs1_rows, MHS1_RUN3, "*#4*39*60*51##")) == 1


async def test_captured_myhomeserver1_reply_updates_zone_39(hass, mhs1_rows):
    handler = _handler(hass, "MyHomeServer1")
    zone = MyHOMEClimate(
        hass=hass, name="Zone 39", device_id="4-39", who="4", where="39",
        heating=True, cooling=True, fan=False, humidity=True, standalone=True, central=False,
        manufacturer="BTicino", model="Heating Zone", gateway=handler,
    )
    zone.hass = hass
    zone.entity_id = "climate.zone_39"
    zone.async_write_ha_state = MagicMock()
    await zone.async_update()
    assert _sent(handler) == ["*#4*39##", "*#4*39*60##"]
    for frame in _result(mhs1_rows, MHS1_RUN3, "*#4*39*60##")["replies"]:
        zone.handle_event(OWNHeatingEvent(frame))
    assert zone.current_humidity == 51.0
