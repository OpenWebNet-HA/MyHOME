"""The shared platform skeleton (custom_components/myhome/discovery.py).

Frames follow the OpenWebNet WHERE conventions: point-to-point ``APL``
(``12``), area ``A`` (``1``), group ``#G`` (``#5``), general ``0`` and the
F422 bus-routing form ``APL#4#<bus>`` (``0311#4#01``).
"""
from unittest.mock import MagicMock, patch

from homeassistant.core import callback
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from OWNd.message import OWNAutomationEvent, OWNEvent, OWNLightingEvent
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.myhome.const import DOMAIN
from custom_components.myhome.discovery import (
    Address,
    DeviceContext,
    KnownDevices,
    PlatformDiscovery,
    config_for,
    parse_unique_id,
    update_signal,
)
from tests.conftest import attach_runtime

MAC = "00:03:50:00:00:01"


def test_address_forms_follow_openwebnet_where_conventions():
    plain = Address.from_device_id("12")
    assert (plain.where, plain.interface, plain.key, plain.suffix) == ("12", None, "12", "12")
    routed = Address.from_device_id("0311#4#01")
    assert (routed.where, routed.interface, routed.key, routed.suffix) == ("0311", "01", "0311#4#01", "0311I01")
    legacy = Address.from_device_id("1-12")  # who-prefixed ids written by old versions
    assert (legacy.clean_where, legacy.clean_key) == ("12", "12")
    zone = Address.from_device_id("3#16", key_suffix="#16")
    assert (zone.where, zone.key) == ("3", "3#16")
    assert Address.from_config("kitchen", {"where": "12", "bus_interface": "02"}).key == "12#4#02"
    assert Address.from_config("12", {}).where == "12"  # the yaml key is the WHERE when omitted
    assert Address.from_message(MagicMock(where="", interface=None)) is None
    assert Address.from_message(OWNEvent.parse("*1*1*0311#4#01##")) == Address("0311", "01")


def test_unique_id_parsing_handles_old_and_new_forms():
    assert parse_unique_id(f"{MAC}-1-12#4#01", MAC) == ("1", "12#4#01")
    assert parse_unique_id(f"{MAC}-12", MAC) == (None, "12")  # ids from before the WHO part
    assert parse_unique_id("AA:BB-2-31", MAC, entry_mac="AA:BB") == ("2", "31")


def test_config_lookup_and_known_devices():
    configured = {"12": {"name": "By where"}, "12#4#01": {"name": "By key"}, "extra": {"name": "Extra"}}
    assert config_for(configured, Address("12", "01"))["name"] == "By key"
    assert config_for(configured, Address("1-12"))["name"] == "By where"  # falls back to the clean WHERE
    assert config_for(configured, Address("99"), "extra")["name"] == "Extra"
    assert config_for(configured, Address("99")) == {}
    known = KnownDevices()
    known.add("12", None, "13")
    assert "12" in known and "13" in known and None not in known and len(known) == 2
    known.discard("12")
    assert sorted(known) == ["13"]
    assert update_signal(MAC, "1", "12#4#01") == f"myhome_update_{MAC}_1_12#4#01"


def _entry(hass, platforms):
    entry = MockConfigEntry(domain=DOMAIN, data={"mac": MAC}, unique_id=MAC)
    entry.add_to_hass(hass)
    gateway = MagicMock()
    gateway.mac = MAC
    runtime = attach_runtime(hass, entry, MAC, gateway)
    runtime.platforms.update(platforms)
    return entry


def _entity(name):
    entity = MagicMock()
    entity._device_name = name
    return entity


async def test_skeleton_configures_discovers_and_routes(hass):
    entry = _entry(hass, {"light": {"kitchen": {"where": "12", "name": "Kitchen"}, "bad": "not a dict"}})
    added, built, routed, announced = [], [], [], []

    def build(ctx: DeviceContext):
        built.append(ctx)
        if ctx.address.where == "99":
            return None  # a platform may decline an address
        return _entity(ctx.cfg.get("name", f"Light {ctx.suffix}"))

    @callback
    def on_announced(device):
        announced.append(device)

    @callback
    def on_routed(msg):
        routed.append(msg)

    async_dispatcher_connect(hass, f"myhome_new_device_{MAC}", on_announced)
    async_dispatcher_connect(hass, update_signal(MAC, "1", "12"), on_routed)

    discovery = PlatformDiscovery(
        hass, entry, added.extend, platform="light", who="1", event_type=OWNLightingEvent, build=build, announce=True,
    )
    entities = discovery.start()
    # the yaml device is created (the non-dict entry skipped) and announced to the button platform
    assert len(entities) == 1 and built[0].source == "yaml" and built[0].cfg["name"] == "Kitchen"
    assert announced[-1]["device_id"] == "12" and announced[-1]["name"] == "Kitchen"
    assert "12" in discovery.known and "kitchen" in discovery.known

    # a frame for a known address is routed, not re-created
    discovery.handle_message(OWNEvent.parse("*1*1*12##"))
    assert len(routed) == 1 and len(built) == 1

    # an unknown address is discovered from its first frame, then routed
    discovery.handle_message(OWNEvent.parse("*1*1*13##"))
    assert built[-1].source == "bus" and built[-1].key == "13" and "13" in discovery.known
    assert added[-1]._device_name == "Light 13"
    added[-1].handle_event.assert_called_once()

    # a declined address is not remembered, so it can be offered again later
    discovery.handle_message(OWNEvent.parse("*1*1*99##"))
    assert "99" not in discovery.known

    # translation, general, group and area frames never create entities
    for frame in ("*1*1000#1*14##", "*1*1*0##", "*1*1*#5##", "*1*1*1##"):
        discovery.handle_message(OWNEvent.parse(frame))
    assert not {"14", "0", "#5", "1"} & set(discovery.known)
    # frames of another subsystem are ignored
    discovery.handle_message(OWNEvent.parse("*2*1*21##"))
    assert "21" not in discovery.known
    assert discovery.mac == MAC


async def test_skeleton_hooks(hass):
    entry = _entry(hass, {"cover": {"c1": {"where": "31"}, "c2": {"where": "32"}, "c3": {"where": "33"}}})
    registry_entry = MagicMock(domain="cover", unique_id=f"{MAC}-2-40", entity_id="cover.old")
    ghost_entry = MagicMock(domain="cover", unique_id=f"{MAC}-2-41", entity_id="cover.ghost")
    foreign_entry = MagicMock(domain="cover", unique_id=f"{MAC}-2-32", entity_id="cover.foreign")
    declined_entry = MagicMock(domain="cover", unique_id=f"{MAC}-2-42", entity_id="cover.declined")
    other_domain = MagicMock(domain="light", unique_id=f"{MAC}-1-12", entity_id="light.x")
    registry = MagicMock()
    generals, pre = [], []

    def build(ctx: DeviceContext):
        if ctx.address.where in ("42", "33"):
            return None
        return _entity(f"Cover {ctx.suffix}")

    def pre_message(message, address, known):
        pre.append(address.where)
        return address.where == "50"

    with patch("custom_components.myhome.discovery.er.async_get", return_value=registry), patch(
        "custom_components.myhome.discovery.er.async_entries_for_config_entry",
        return_value=[registry_entry, ghost_entry, foreign_entry, declined_entry, other_domain],
    ):
        discovery = PlatformDiscovery(
            hass, entry, lambda ents: None, platform="cover", who="2", event_type=OWNAutomationEvent, build=build,
            reject_registry_entry=lambda e, ctx: e.entity_id == "cover.ghost",
            accept=lambda ctx: ctx.address.where != "32",
            pre_message=pre_message,
            on_general=generals.append,
            yaml_device_id=lambda address: address.clean_key,
        )
        entities = discovery.start(listen=False)
    registry.async_remove.assert_called_once_with("cover.ghost")  # a ghost is removed, not restored
    assert "40" in discovery.known and "41" not in discovery.known
    assert "42" not in discovery.known  # build() declined the registry entry
    assert "31" in discovery.known and "32" not in discovery.known  # accept() vetoed 32 twice
    assert "33" not in discovery.known  # build() declined the yaml device
    assert [e._device_name for e in entities] == ["Cover 40", "Cover 31"]

    discovery.handle_message(OWNEvent.parse("*2*1*0##"))
    assert len(generals) == 1  # general frames go to on_general
    discovery.handle_message(OWNEvent.parse("*2*1*50##"))
    assert pre == ["50"] and "50" not in discovery.known  # pre_message handled the frame
    discovery.handle_message(OWNEvent.parse("*2*1*32##"))
    assert "32" not in discovery.known  # accept() also vetoes bus discovery
    discovery.handle_message(OWNEvent.parse("*2*1*51##"))
    assert "51" in discovery.known


async def test_general_where_can_be_a_device_and_registry_absence_is_tolerated(hass):
    entry = _entry(hass, {"alarm_control_panel": {}})
    built = []
    with patch("custom_components.myhome.discovery.er.async_get", side_effect=RuntimeError("no registry")):
        discovery = PlatformDiscovery(
            hass, entry, lambda ents: None, platform="alarm_control_panel", who="5", event_type=None,
            build=lambda ctx: built.append(ctx) or _entity("Central"), general_is_device=True,
        )
        assert discovery.registry_entries() == (None, [])
        assert discovery.start(listen=False) == []
    discovery.handle_message(MagicMock(where="0", interface=None, who="5", is_translation=False))
    assert built and built[0].address.where == "0" and "0" in discovery.known


async def test_key_suffix_and_custom_address(hass):
    entry = _entry(hass, {"media_player": {"3": {}}})
    discovery = PlatformDiscovery(
        hass, entry, lambda ents: None, platform="media_player", who="16", event_type=None,
        build=lambda ctx: _entity(f"Zone {ctx.suffix}"), key_suffix="#16",
        address=lambda msg: Address(str(msg.zone), key_suffix="#16") if msg.zone else None,
    )
    discovery.start(listen=False)
    assert "3#16" in discovery.known
    discovery.handle_message(MagicMock(zone=None, where="4"))
    assert "4#16" not in discovery.known  # the address hook ignored the frame
    discovery.handle_message(MagicMock(zone=4, where="4"))
    assert "4#16" in discovery.known


async def test_build_may_return_several_entities_or_none(hass):
    """A meter is one address with one entity per measurement; an empty list creates nothing."""
    entry = _entry(hass, {"sensor": {"m1": {"where": "51"}, "m2": {"where": "52"}}})
    fed = []

    def build(ctx: DeviceContext):
        if ctx.address.where == "52":
            return []
        a, b = _entity("Power"), _entity("Energy")
        a.handle_event.side_effect = lambda m: fed.append(("a", m.where))
        b.handle_event.side_effect = lambda m: fed.append(("b", m.where))
        return [a, b]

    discovery = PlatformDiscovery(
        hass, entry, lambda ents: None, platform="sensor", who="18", event_type=None, build=build,
        direct=True, route_keys=lambda msg, address: [str(msg.where)],
    )
    entities = discovery.start(listen=False)
    assert len(entities) == 2 and "51" in discovery.known and "52" not in discovery.known
    assert [e._device_name for e in discovery.entities["51"]] == ["Power", "Energy"]
    discovery.handle_message(MagicMock(where="51", interface=None, is_translation=False))
    assert fed == [("a", "51"), ("b", "51")]  # both entities of the address, once each
    discovery.handle_message(MagicMock(where="53", interface=None, is_translation=False))  # discovered: fed once
    assert fed[2:] == [("a", "53"), ("b", "53")]
