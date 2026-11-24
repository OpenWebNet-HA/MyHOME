"""One dry contact, two spellings: an F455 writes ``301`` where an F461 writes ``31`` (live, 2026-11-18).

Plant: an F461 2.0.11 and an F455 1.0.76 on one SCS bus, an automation dry-contact interface
with contacts 1 and 2. The same answer of contact 1 reached the two event sessions 13 ms apart
as ``*25*31#0*301##`` (F455) and ``*25*31#0*31##`` (F461); each gateway put the other's
spelling of the status request on the bus. The WHO 25 PDF writes contact 1 as ``31`` (WHERE = ``3`` +
``[1-201]``), like the F454, MH200N and MH202 captures of issue_466.

* Entities were keyed on the raw WHERE, so with the F461 as primary and the F455 as warm
  standby the standby's frame did not reach ``binary_sensor ...-31-opening`` during a failover
  and created a second device "Dry Contact 301" on the primary's entry instead.
* An F455 on its own registered the contacts as ``301`` / ``302``: those entities keep their
  unique id and answer to both spellings.

Frames: tests/fixtures/traces/dry_contact_where_f455_f461_2026_11_18/frames.json
([time, gateway monitor, frame] per window).
"""

import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest
from homeassistant.components.binary_sensor import BinarySensorDeviceClass
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from OWNd.message import OWNMessage
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.myhome.binary_sensor import (
    MyHOMEDryContact,
    _aux_address,
    _bus_interface_from_message,
    _contact_address,
    _contact_peer_spellings,
    _contact_spellings,
    _motion_address,
    _spellings,
    binary_sensor_canonical_where,
    resolve_binary_sensor_inverted,
)
from custom_components.myhome.const import (
    CONF_BUS_TOPOLOGY,
    CONF_FILE_PATH,
    CONF_GATEWAY_ROLE,
    CONF_IGNORED_ADDRESSES,
    CONF_INVERTED_BINARY_SENSORS,
    CONF_PRIMARY_GATEWAY,
    DOMAIN,
    ROLE_PRIMARY,
    ROLE_STANDBY,
    TOPOLOGY_SHARED,
)
from custom_components.myhome.discovery import Address, binary_sensor_only_on_primary, prune_entity
from custom_components.myhome.gateway import AVAILABILITY_GRACE
from custom_components.myhome.ignored import IgnoredAddresses
from custom_components.myhome.legacy_yaml import CONF_MIGRATED_YAML_PLATFORMS
from custom_components.myhome.where_grammar import (
    canonical_contact_where,
    contact_where_aliases,
    padded_contact_where,
)
from tests.test_multi_gateway_regressions import PRI, SB, _set_up, _setup_patches

FRAMES = json.loads(
    (
        Path(__file__).parent
        / "fixtures"
        / "traces"
        / "dry_contact_where_f455_f461_2026_11_18"
        / "frames.json"
    ).read_text()
)
# Contact 1 opening as an F454 and an MH200N reported it (issue_466 traces) and an MH202
# (mh202_sound_intercom_ducking trace)
CONTACT_1_OFF = "*25*32#1*31##"
SHARED_PRIMARY = {CONF_BUS_TOPOLOGY: TOPOLOGY_SHARED, CONF_GATEWAY_ROLE: ROLE_PRIMARY}
SHARED_STANDBY = {CONF_BUS_TOPOLOGY: TOPOLOGY_SHARED, CONF_GATEWAY_ROLE: ROLE_STANDBY, CONF_PRIMARY_GATEWAY: PRI}


def _rx(window: str, gateway: str) -> list[str]:
    return [raw for _t, gw, raw in FRAMES[window] if gw == gateway]


def _unique_ids(hass: HomeAssistant, entry: MockConfigEntry, domain: str = "binary_sensor") -> list[str]:
    registry = er.async_get(hass)
    return sorted(e.unique_id for e in er.async_entries_for_config_entry(registry, entry.entry_id) if e.domain == domain)


def _contact_devices(hass: HomeAssistant, entry: MockConfigEntry) -> list[str]:
    return sorted(
        ident
        for device in dr.async_entries_for_config_entry(dr.async_get(hass), entry.entry_id)
        for domain, ident in device.identifiers
        if domain == DOMAIN and "-25-" in ident
    )


def _state(hass: HomeAssistant, unique_id: str) -> str | None:
    entity_id = er.async_get(hass).async_get_entity_id("binary_sensor", DOMAIN, unique_id)
    state = hass.states.get(entity_id) if entity_id else None
    return state.state if state else None


def _connected(entry: MockConfigEntry) -> None:
    gateway = entry.runtime_data.gateway
    gateway.is_connected = gateway._available = True
    gateway._notify_availability()


async def _feed(hass: HomeAssistant, entry: MockConfigEntry, *frames: str) -> None:
    for raw in frames:
        await entry.runtime_data.gateway._process_message(OWNMessage.parse(raw))
    await hass.async_block_till_done()


@pytest.mark.parametrize(
    ("where", "canonical"),
    [
        ("301", "31"),  # live F455, contact 1
        ("302", "32"),  # live F455, contact 2
        ("309", "39"),
        ("301#4#01", "31#4#01"),
        ("31", "31"),  # F461, F454, MH200N, MH202; WHO 25 PDF
        ("3201", "3201"),  # WHO 25 PDF: contact 201
        ("344", "344"),  # alarm / IR interface, Z 4 N 4 (F455, issue_466)
        ("339", "339"),  # H4890 (issue_466)
        ("310", "310"),  # contact 10: nothing captured, left alone
        ("300", "300"),
        ("3001", "3001"),  # another padding width: nothing captured, left alone
        ("", ""),
    ],
)
def test_canonical_contact_where(where: str, canonical: str) -> None:
    assert canonical_contact_where(where) == canonical


def test_contact_spellings_and_peer_unique_ids() -> None:
    assert padded_contact_where("31") == "301"
    assert padded_contact_where("31#4#1") == "301#4#1"
    assert padded_contact_where("344") == "344"
    assert contact_where_aliases("301") == ["301", "31"]
    assert contact_where_aliases("31") == ["31", "301"]
    assert contact_where_aliases("344") == ["344"]

    assert _contact_spellings("301") == ["301", "31"]
    assert _contact_spellings("31") == ["31", "301"]
    assert _contact_spellings("25-301") == ["25-301", "301", "31"]  # a legacy ``25-`` registry id
    assert _contact_spellings("25-31") == ["25-31", "31", "301"]
    assert _contact_spellings("gate-301") == ["gate-301"]  # named YAML key: not rewritten to 301 or 31

    assert _contact_peer_spellings(f"{PRI}-301-opening") == [
        f"{PRI}-31-opening",
        f"{PRI}-25-301-opening",
        f"{PRI}-25-31-opening",
    ]
    assert _contact_peer_spellings(f"{PRI}-31-opening") == [
        f"{PRI}-301-opening",
        f"{PRI}-25-31-opening",
        f"{PRI}-25-301-opening",
    ]
    assert _contact_peer_spellings(f"{PRI}-25-301-motion") == [
        f"{PRI}-25-31-motion",
        f"{PRI}-301-motion",
        f"{PRI}-31-motion",
    ]
    assert _contact_peer_spellings(f"{PRI}-gate-301-opening") == []
    assert _contact_peer_spellings(f"{PRI}-gate-25-301-opening") == []
    assert _contact_peer_spellings("301") == []


async def test_both_gateways_frames_are_one_contact(hass: HomeAssistant) -> None:
    """The live window, F461 and F455 frames in the order they arrived: two contacts, not four."""
    p1, p2, p3 = _setup_patches()
    with p1, p2, p3:
        entry = await _set_up(hass, PRI, "192.168.0.35", {})
        _connected(entry)
        await _feed(hass, entry, *(raw for _t, _gw, raw in FRAMES["status_read_301_302"]))

        assert _unique_ids(hass, entry) == [f"{PRI}-31-opening", f"{PRI}-32-opening"]
        assert _contact_devices(hass, entry) == [f"{PRI}-25-31", f"{PRI}-25-32"]
        assert _unique_ids(hass, entry, "switch") == [f"{PRI}-25-31-invert", f"{PRI}-25-32-invert"]
        assert _state(hass, f"{PRI}-31-opening") == "on"


async def test_an_f455_on_its_own_discovers_the_contact_as_31(hass: HomeAssistant) -> None:
    p1, p2, p3 = _setup_patches()
    with p1, p2, p3:
        entry = await _set_up(hass, PRI, "192.168.0.35", {})
        _connected(entry)
        await _feed(hass, entry, *_rx("status_read_301_302", "f455"))

        assert _unique_ids(hass, entry) == [f"{PRI}-31-opening", f"{PRI}-32-opening"]
        state = hass.states.get(er.async_get(hass).async_get_entity_id("binary_sensor", DOMAIN, f"{PRI}-31-opening"))
        assert state.state == "on"
        assert state.attributes["Sensor"] == "(3)1"


async def test_a_contact_registered_as_301_keeps_its_unique_id_and_answers_to_both_spellings(
    hass: HomeAssistant,
) -> None:
    """What an F455 created before: no rename, no second entity, and it follows either spelling."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={"host": "192.168.0.35", "port": 20000, "password": "pass", "mac": PRI},
        unique_id=PRI,
        title="F455",
    )
    entry.add_to_hass(hass)
    registry = er.async_get(hass)
    device = dr.async_get(hass).async_get_or_create(
        config_entry_id=entry.entry_id, identifiers={(DOMAIN, f"{PRI}-25-301")}, name="Dry Contact 301"
    )
    legacy = registry.async_get_or_create(
        "binary_sensor", DOMAIN, f"{PRI}-301-opening", config_entry=entry, device_id=device.id,
        original_device_class="opening",
    )
    p1, p2, p3 = _setup_patches()
    with p1, p2, p3:
        await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
        _connected(entry)

        await _feed(hass, entry, *_rx("status_read_301_302", "f455")[:2])
        assert _unique_ids(hass, entry) == [f"{PRI}-301-opening"]
        assert hass.states.get(legacy.entity_id).state == "on"

        await _feed(hass, entry, CONTACT_1_OFF)  # the F461's spelling, e.g. bridged during a failover
        assert hass.states.get(legacy.entity_id).state == "off"
        assert _unique_ids(hass, entry) == [f"{PRI}-301-opening"]
        assert _contact_devices(hass, entry) == [f"{PRI}-25-301"]


async def test_a_failover_to_the_f455_updates_the_primarys_contact_and_creates_no_device(
    hass: HomeAssistant,
) -> None:
    """Live 01:19:47: ``*#25*31##`` went out through the standby F455, which answered ``*25*31#0*301##``."""
    p1, p2, p3 = _setup_patches()
    with p1, p2, p3:
        primary = await _set_up(hass, PRI, "192.168.0.35", SHARED_PRIMARY)
        standby = await _set_up(hass, SB, "192.168.0.36", SHARED_STANDBY)
        gw_p, gw_s = primary.runtime_data.gateway, standby.runtime_data.gateway
        _connected(primary)
        await _feed(hass, primary, *_rx("status_read_301_302", "f461")[:1], CONTACT_1_OFF)
        assert _unique_ids(hass, primary) == [f"{PRI}-31-opening"]
        assert _state(hass, f"{PRI}-31-opening") == "off"

        gw_p._setup_at -= 2 * AVAILABILITY_GRACE
        gw_p.is_connected = gw_p._available = False
        gw_s.is_connected = gw_s._available = True
        await _feed(hass, standby, *_rx("failover_read_31_through_f455", "f455"))

        assert gw_p.failover_active is True
        assert _state(hass, f"{PRI}-31-opening") == "on"
        assert _unique_ids(hass, primary) == [f"{PRI}-31-opening"]
        assert _contact_devices(hass, primary) == [f"{PRI}-25-31"]
        assert _unique_ids(hass, primary, "switch") == [f"{PRI}-25-31-invert"]
        assert _unique_ids(hass, standby) == []

        # A contact first seen through the standby gets the id the primary would give it itself
        await _feed(hass, standby, "*25*31#0*302##")
        assert _unique_ids(hass, primary) == [f"{PRI}-31-opening", f"{PRI}-32-opening"]
        assert _unique_ids(hass, standby) == []


async def test_the_duplicate_an_earlier_failover_left_behind_is_removed_at_the_next_start(
    hass: HomeAssistant,
) -> None:
    """"Dry Contact 301" next to the primary's own contact 31: the older registry entry stays."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={"host": "192.168.0.35", "port": 20000, "password": "pass", "mac": PRI},
        unique_id=PRI,
        title="F461",
    )
    entry.add_to_hass(hass)
    registry, devices = er.async_get(hass), dr.async_get(hass)
    for where in ("31", "301"):
        device = devices.async_get_or_create(
            config_entry_id=entry.entry_id, identifiers={(DOMAIN, f"{PRI}-25-{where}")}, name=f"Dry Contact {where}"
        )
        registry.async_get_or_create(
            "binary_sensor", DOMAIN, f"{PRI}-{where}-opening", config_entry=entry, device_id=device.id,
            original_device_class="opening",
        )
        registry.async_get_or_create("switch", DOMAIN, f"{PRI}-25-{where}-invert", config_entry=entry, device_id=device.id)
    p1, p2, p3 = _setup_patches()
    with p1, p2, p3:
        await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
        _connected(entry)

        assert _unique_ids(hass, entry) == [f"{PRI}-31-opening"]
        assert _unique_ids(hass, entry, "switch") == [f"{PRI}-25-31-invert"]
        assert _contact_devices(hass, entry) == [f"{PRI}-25-31"]
        await _feed(hass, entry, *_rx("failover_read_31_through_f455", "f455"))
        assert _unique_ids(hass, entry) == [f"{PRI}-31-opening"]
        assert _state(hass, f"{PRI}-31-opening") == "on"


async def test_a_standby_f455_drops_its_301_once_the_primary_has_the_contact(hass: HomeAssistant) -> None:
    """Shared bus (#453): the follower's ``301`` is the primary's ``31``, pruned like any other duplicate."""
    p1, p2, p3 = _setup_patches()
    with p1, p2, p3:
        primary = await _set_up(hass, PRI, "192.168.0.35", SHARED_PRIMARY)
        _connected(primary)
        await _feed(hass, primary, *_rx("status_read_301_302", "f461")[:1])
        assert _unique_ids(hass, primary) == [f"{PRI}-31-opening"]

        standby = MockConfigEntry(
            domain=DOMAIN,
            data={"host": "192.168.0.36", "port": 20000, "password": "pass", "mac": SB},
            options=SHARED_STANDBY,
            unique_id=SB,
            title="F455",
        )
        standby.add_to_hass(hass)
        registry, devices = er.async_get(hass), dr.async_get(hass)
        for where in ("301", "344"):  # 344: an IR sensor only the F455 registered
            device = devices.async_get_or_create(
                config_entry_id=standby.entry_id, identifiers={(DOMAIN, f"{SB}-25-{where}")}, name=f"Dry Contact {where}"
            )
            registry.async_get_or_create(
                "binary_sensor", DOMAIN, f"{SB}-{where}-opening", config_entry=standby, device_id=device.id,
                original_device_class="opening",
            )
            registry.async_get_or_create(
                "switch", DOMAIN, f"{SB}-25-{where}-invert", config_entry=standby, device_id=device.id
            )
        await hass.config_entries.async_setup(standby.entry_id)
        await hass.async_block_till_done()

        assert _unique_ids(hass, standby) == [f"{SB}-344-opening"]
        assert _unique_ids(hass, standby, "switch") == [f"{SB}-25-344-invert"]
        assert _contact_devices(hass, standby) == [f"{SB}-25-344"]
        assert _unique_ids(hass, primary) == [f"{PRI}-31-opening"]


@pytest.mark.parametrize("typed", ["25/301", "25/31"])
async def test_an_ignored_contact_stays_ignored_in_either_spelling(hass: HomeAssistant, typed: str) -> None:
    p1, p2, p3 = _setup_patches()
    with p1, p2, p3:
        entry = await _set_up(hass, PRI, "192.168.0.35", {CONF_IGNORED_ADDRESSES: [typed]})
        await _feed(hass, entry, *(raw for _t, _gw, raw in FRAMES["status_read_301_302"]))

        assert _unique_ids(hass, entry) == [f"{PRI}-32-opening"]


@pytest.mark.parametrize(
    ("where", "request_frame"),
    [("301", "*#25*31##"), ("302", "*#25*32##"), ("31", "*#25*31##"), ("344", "*#25*344##")],
)
async def test_the_status_request_of_a_contact_uses_the_unpadded_where(
    hass: HomeAssistant, where: str, request_frame: str
) -> None:
    """A contact an F455 registered as ``301`` is read with ``*#25*31##``.

    Live 2026-11-22: an F461 2.0.11 answered ``*#25*301##`` on its command session with NACK
    (four reads of four) and acknowledged ``*#25*31##``; an F455 1.0.76 relayed the short request
    and the contact answered (fixture README, ``failover_read_31_through_f455``).
    """
    gateway = MagicMock()
    gateway.mac = "mac"
    gateway.send_status_request = AsyncMock()
    hass.data = {DOMAIN: {"mac": {"platforms": {"binary_sensor": {f"dry_{where}": {"entities": {}}}}}}}
    sensor = MyHOMEDryContact(
        hass=hass,
        name="Device",
        entity_name="Sensor",
        device_id=f"dry_{where}",
        who="25",
        where=where,
        inverted=False,
        device_class=BinarySensorDeviceClass.OPENING,
        manufacturer="M",
        model="M",
        gateway=gateway,
    )

    await sensor.async_update()

    (sent,), _kwargs = gateway.send_status_request.call_args
    assert str(sent) == request_frame


def test_only_the_whole_padded_where_is_rewritten() -> None:
    """Anything after the contact number other than a ``#4#II`` suffix leaves the WHERE as given.

    A negative input, not a captured address.
    """
    event = OWNMessage.parse(FRAMES["status_read_301_302"][1][2])
    opaque = f"{event.where}#1"

    assert canonical_contact_where(opaque) == opaque
    assert canonical_contact_where("301#4#01") == "31#4#01"


async def test_a_named_device_key_ending_in_301_is_not_contact_1(hass: HomeAssistant) -> None:
    """A migrated yaml key such as ``gate-301`` is a name, not a WHERE.

    The sensor is configured at WHERE ``329`` (an F453 capture has ``*25*32#1*329##``,
    ``tests/fixtures/traces/discussion_770_f453_n4692``); contact 1's report must not move it.
    """
    name = "gate-301"
    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id=PRI,
        data={"host": "127.0.0.1", "mac": PRI, "port": 20000, "password": "pass"},
        options={
            CONF_MIGRATED_YAML_PLATFORMS: {
                "binary_sensor": {name: {"who": "25", "where": "329", "name": "Gate", "class": "opening"}}
            }
        },
    )
    entry.add_to_hass(hass)
    unique_id = f"{PRI}-{name}-opening"
    er.async_get(hass).async_get_or_create(
        "binary_sensor", DOMAIN, unique_id, config_entry=entry, original_device_class="opening"
    )
    p1, p2, p3 = _setup_patches()
    with p1, p2, p3:
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
        _connected(entry)
        await _feed(hass, entry, "*25*32#1*329##")
        assert _state(hass, unique_id) == "off"

        await _feed(hass, entry, FRAMES["status_read_301_302"][1][2], FRAMES["status_read_301_302"][2][2])

        assert _state(hass, unique_id) == "off"
        assert f"{PRI}-31-opening" in _unique_ids(hass, entry)


async def test_a_standby_f461_drops_its_31_when_primary_f455_has_301(hass: HomeAssistant) -> None:
    """Reverse shared bus: F455 primary registered ``301`` (or legacy ``-25-301``), F461 standby drops ``31``."""
    primary = MockConfigEntry(
        domain=DOMAIN,
        data={"host": "192.168.0.35", "port": 20000, "password": "pass", "mac": PRI},
        options=SHARED_PRIMARY,
        unique_id=PRI,
        title="F455 Primary",
    )
    primary.add_to_hass(hass)
    registry, devices = er.async_get(hass), dr.async_get(hass)
    pri_dev = devices.async_get_or_create(
        config_entry_id=primary.entry_id, identifiers={(DOMAIN, f"{PRI}-25-301")}, name="Dry Contact 301"
    )
    registry.async_get_or_create(
        "binary_sensor", DOMAIN, f"{PRI}-301-opening", config_entry=primary, device_id=pri_dev.id,
        original_device_class="opening",
    )
    pri_legacy_dev = devices.async_get_or_create(
        config_entry_id=primary.entry_id, identifiers={(DOMAIN, f"{PRI}-25-302")}, name="Dry Contact 302"
    )
    registry.async_get_or_create(
        "binary_sensor", DOMAIN, f"{PRI}-25-302-opening", config_entry=primary, device_id=pri_legacy_dev.id,
        original_device_class="opening",
    )

    p1, p2, p3 = _setup_patches()
    with p1, p2, p3:
        assert await hass.config_entries.async_setup(primary.entry_id)
        await hass.async_block_till_done()

        standby = MockConfigEntry(
            domain=DOMAIN,
            data={"host": "192.168.0.36", "port": 20000, "password": "pass", "mac": SB},
            options=SHARED_STANDBY,
            unique_id=SB,
            title="F461 Standby",
        )
        standby.add_to_hass(hass)
        for where in ("31", "32", "344"):
            dev = devices.async_get_or_create(
                config_entry_id=standby.entry_id, identifiers={(DOMAIN, f"{SB}-25-{where}")}, name=f"Dry Contact {where}"
            )
            registry.async_get_or_create(
                "binary_sensor", DOMAIN, f"{SB}-{where}-opening", config_entry=standby, device_id=dev.id,
                original_device_class="opening",
            )
            registry.async_get_or_create(
                "switch", DOMAIN, f"{SB}-25-{where}-invert", config_entry=standby, device_id=dev.id
            )

        assert await hass.config_entries.async_setup(standby.entry_id)
        await hass.async_block_till_done()

        assert _unique_ids(hass, standby) == [f"{SB}-344-opening"]
        assert _unique_ids(hass, standby, "switch") == [f"{SB}-25-344-invert"]
        assert _contact_devices(hass, standby) == [f"{SB}-25-344"]
        assert _unique_ids(hass, primary) == [f"{PRI}-25-302-opening", f"{PRI}-301-opening"]


def test_binary_sensor_only_on_primary_matches_contact_aliases() -> None:
    assert binary_sensor_only_on_primary({f"{PRI}-25-31"}, f"{SB}-25-31", SB, PRI) is True
    assert binary_sensor_only_on_primary({f"{PRI}-25-31"}, f"{SB}-25-301", SB, PRI) is True
    assert binary_sensor_only_on_primary({f"{PRI}-25-301"}, f"{SB}-25-31", SB, PRI) is True
    assert binary_sensor_only_on_primary({f"{PRI}-25-31", f"{SB}-25-31"}, f"{SB}-25-31", SB, PRI) is False
    assert binary_sensor_only_on_primary({f"{PRI}-25-31"}, "unrelated-25-31", SB, PRI) is False
    assert binary_sensor_only_on_primary({f"{PRI}-25-344"}, f"{SB}-25-31", SB, PRI) is False
    assert binary_sensor_only_on_primary({f"{PRI}-9-1"}, f"{SB}-9-2", SB, PRI) is False


@pytest.mark.parametrize(("reg_where", "yaml_where"), [("301", "31"), ("31", "301")])
async def test_registry_and_yaml_contact_spellings_merge_on_one_device(
    hass: HomeAssistant, reg_where: str, yaml_where: str
) -> None:
    """Registry ``301`` + YAML ``31`` (and reverse) merge onto the existing device and honor legacy ``25-301`` option."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id=PRI,
        data={"host": "127.0.0.1", "mac": PRI, "port": 20000, "password": "pass"},
        options={
            CONF_MIGRATED_YAML_PLATFORMS: {
                "binary_sensor": {
                    yaml_where: {"who": "25", "where": yaml_where, "name": "Front Gate", "class": "opening"}
                }
            },
            CONF_INVERTED_BINARY_SENSORS: {"25-301": True},
        },
    )
    entry.add_to_hass(hass)
    registry, devices = er.async_get(hass), dr.async_get(hass)
    dev = devices.async_get_or_create(
        config_entry_id=entry.entry_id, identifiers={(DOMAIN, f"{PRI}-25-{reg_where}")}, name=f"Dry Contact {reg_where}"
    )
    sensor_entry = registry.async_get_or_create(
        "binary_sensor", DOMAIN, f"{PRI}-{reg_where}-opening", config_entry=entry, device_id=dev.id,
        original_device_class="opening",
    )
    p1, p2, p3 = _setup_patches()
    with p1, p2, p3:
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
        _connected(entry)

        assert _unique_ids(hass, entry) == [f"{PRI}-{reg_where}-opening"]
        assert _unique_ids(hass, entry, "switch") == [f"{PRI}-25-{reg_where}-invert"]
        assert _contact_devices(hass, entry) == [f"{PRI}-25-{reg_where}"]
        assert resolve_binary_sensor_inverted(entry, "25", "31") is True
        assert resolve_binary_sensor_inverted(entry, "25", "301") is True

        # Inverted: closed (*25*31#0*31##) reports "off"
        await _feed(hass, entry, "*25*31#0*31##")
        assert hass.states.get(sensor_entry.entity_id).state == "off"


def test_prune_entity_preserves_companion_buttons_when_another_actuator_shares_unique_id(hass: HomeAssistant) -> None:
    """Pruning a ghost light whose unique_id matches a live switch keeps the switch's lock/unlock buttons."""
    entry = MockConfigEntry(domain=DOMAIN, unique_id=PRI, data={"host": "127.0.0.1", "mac": PRI})
    entry.add_to_hass(hass)
    registry, devices = er.async_get(hass), dr.async_get(hass)
    dev = devices.async_get_or_create(config_entry_id=entry.entry_id, identifiers={(DOMAIN, f"{PRI}-1-11")})
    ghost_light = registry.async_get_or_create("light", DOMAIN, f"{PRI}-1-11", config_entry=entry, device_id=dev.id)
    live_switch = registry.async_get_or_create("switch", DOMAIN, f"{PRI}-1-11", config_entry=entry, device_id=dev.id)
    disable_btn = registry.async_get_or_create("button", DOMAIN, f"{PRI}-1-11-disable", config_entry=entry, device_id=dev.id)

    prune_entity(hass, registry, ghost_light, entry.entry_id)

    assert registry.async_get(ghost_light.entity_id) is None
    assert registry.async_get(live_switch.entity_id) is not None
    assert registry.async_get(disable_btn.entity_id) is not None

    # Once the sole remaining actuator is pruned too, its companion button goes with it
    prune_entity(hass, registry, live_switch, entry.entry_id)
    assert registry.async_get(live_switch.entity_id) is None
    assert registry.async_get(disable_btn.entity_id) is None


def test_ignored_contact_normalizes_routed_and_leading_zero_spellings() -> None:
    ignored_padded = IgnoredAddresses(["25/301#4#1"])
    assert ignored_padded.is_ignored(25, "31", "01") is True
    assert ignored_padded.is_ignored(25, "31#4#01") is True
    assert ignored_padded.is_ignored(25, "0301", "1") is True

    ignored_short = IgnoredAddresses(["25/31#4#01"])
    assert ignored_short.is_ignored(25, "301", "1") is True
    assert ignored_short.is_ignored(25, "301#4#1") is True
    assert ignored_short.is_ignored(25, "0301#4#01") is True
    assert ignored_short.is_ignored(25, "32", "01") is False


def test_leading_zero_and_single_digit_routed_contact_aliases() -> None:
    """Leading-zero legacy spellings (``0301`` / ``0031``) and ``#4#1`` <-> ``#4#01`` align symmetrically."""
    assert contact_where_aliases("0301") == ["0301", "301", "31"]
    assert contact_where_aliases("0031") == ["0031", "31", "301"]
    assert contact_where_aliases("0031#4#01") == [
        "0031#4#01",
        "31#4#01",
        "301#4#01",
        "31#4#1",
        "301#4#1",
    ]
    assert contact_where_aliases("0301#4#1") == [
        "0301#4#1",
        "301#4#01",
        "31#4#01",
        "301#4#1",
        "31#4#1",
    ]
    assert contact_where_aliases("301#4#01") == ["301#4#01", "31#4#01", "301#4#1", "31#4#1"]
    assert contact_where_aliases("31#4#12") == ["31#4#12", "301#4#12"]
    assert _contact_spellings("0301") == ["0301", "301", "31"]
    assert f"{PRI}-31-opening" in _contact_peer_spellings(f"{PRI}-0301-opening")
    assert f"{PRI}-301-opening" in _contact_peer_spellings(f"{PRI}-0301-opening")
    assert f"{PRI}-301#4#1-opening" in _contact_peer_spellings(f"{PRI}-31#4#01-opening")
    assert binary_sensor_only_on_primary({f"{PRI}-25-31"}, f"{SB}-25-0301", SB, PRI) is True
    assert binary_sensor_only_on_primary({f"{PRI}-25-31#4#01"}, f"{SB}-25-0301#4#1", SB, PRI) is True
    assert binary_sensor_only_on_primary({f"{PRI}-25-301#4#1"}, f"{SB}-25-31#4#01", SB, PRI) is True
    assert binary_sensor_canonical_where("25", "301#4#1") == "31#4#01"
    assert binary_sensor_canonical_where("25", "0301") == "31"
    assert binary_sensor_canonical_where("25", "0301#4#1") == "31#4#01"
    assert binary_sensor_canonical_where("1", "04#4#1") == "04#4#01"


async def test_routed_bus_contacts_honor_ignored_and_do_not_collide_with_local(
    hass: HomeAssistant, caplog: pytest.LogCaptureFixture
) -> None:
    """F422-routed WHO 25 frames keep ``#4#II`` through discovery, ignore filtering, and routing."""
    p1, p2, p3 = _setup_patches()
    with p1, p2, p3:
        entry = await _set_up(hass, PRI, "192.168.0.35", {CONF_IGNORED_ADDRESSES: ["25/302#4#1"]})
        _connected(entry)

        # Ignored routed contact 2 behind interface 01 must not create any entity
        await _feed(hass, entry, "*25*31#0*302#4#01##")
        assert _unique_ids(hass, entry) == []

        # Routed contact 1 behind interface 01 and local contact 1 are two distinct entities
        await _feed(hass, entry, "*25*31#0*301#4#01##", "*25*31#0*31##")
        assert _unique_ids(hass, entry) == [f"{PRI}-31#4#01-opening", f"{PRI}-31-opening"]
        assert _contact_devices(hass, entry) == [f"{PRI}-25-31", f"{PRI}-25-31#4#01"]
        assert _unique_ids(hass, entry, "switch") == [f"{PRI}-25-31#4#01-invert", f"{PRI}-25-31-invert"]
        assert _state(hass, f"{PRI}-31#4#01-opening") == "on"
        assert _state(hass, f"{PRI}-31-opening") == "on"

        # Opening local contact 1 must leave routed contact 1 on, and single-digit #4#1 routes to #4#01
        await _feed(hass, entry, "*25*32#1*31##")
        assert _state(hass, f"{PRI}-31-opening") == "off"
        assert _state(hass, f"{PRI}-31#4#01-opening") == "on"
        await _feed(hass, entry, "*25*32#1*301#4#1##")
        assert _state(hass, f"{PRI}-31#4#01-opening") == "off"
        assert "does not generate unique IDs" not in caplog.text


async def test_routed_yaml_contact_preserves_interface_for_invert_update_and_reload(
    hass: HomeAssistant, caplog: pytest.LogCaptureFixture
) -> None:
    """YAML routed contacts (inline ``#4#01`` and separate ``interface``) keep ``#4#01`` end-to-end."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id=PRI,
        data={"host": "127.0.0.1", "mac": PRI, "port": 20000, "password": "pass"},
        options={
            CONF_MIGRATED_YAML_PLATFORMS: {
                "binary_sensor": {
                    "garage": {
                        "who": "25",
                        "where": "301#4#01",
                        "name": "Garage",
                        "class": "garage_door",
                    },
                    "side_gate": {
                        "who": "25",
                        "where": "302",
                        "interface": "01",
                        "name": "Side Gate",
                        "class": "door",
                    },
                }
            }
        },
    )
    entry.add_to_hass(hass)
    p1, p2, p3 = _setup_patches()
    with p1, p2, p3:
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
        _connected(entry)
        assert "does not generate unique IDs" not in caplog.text

        # Routed frame updates Garage; local frame discovers local 31 independently
        await _feed(hass, entry, "*25*31#0*301#4#01##", "*25*31#0*31##")
        assert _state(hass, f"{PRI}-garage-garage_door") == "on"
        assert _state(hass, f"{PRI}-31-opening") == "on"

        # Toggling Garage's invert switch flips Garage immediately and leaves local 31 alone
        registry = er.async_get(hass)
        inv_eid = registry.async_get_entity_id("switch", DOMAIN, f"{PRI}-25-garage-invert")
        assert inv_eid is not None
        await hass.services.async_call("switch", "turn_on", {"entity_id": inv_eid}, blocking=True)
        await hass.async_block_till_done()
        assert entry.options[CONF_INVERTED_BINARY_SENSORS]["25-31#4#01"] is True
        assert _state(hass, f"{PRI}-garage-garage_door") == "off"
        assert _state(hass, f"{PRI}-31-opening") == "on"

        # async_update() polls *#25*31#4#01## and *#25*32#4#01## (not local *#25*31##)
        from homeassistant.helpers.entity_component import async_update_entity

        sent: list[str] = []
        entry.runtime_data.gateway.send_status_request = AsyncMock(
            side_effect=lambda cmd: sent.append(str(cmd))
        )
        garage_eid = registry.async_get_entity_id("binary_sensor", DOMAIN, f"{PRI}-garage-garage_door")
        side_eid = registry.async_get_entity_id("binary_sensor", DOMAIN, f"{PRI}-side_gate-door")
        assert garage_eid is not None and side_eid is not None
        await async_update_entity(hass, garage_eid)
        await async_update_entity(hass, side_eid)
        await hass.async_block_till_done()
        assert "*#25*31#4#01##" in sent
        assert "*#25*32#4#01##" in sent

        # Reload preserves routed interface + persisted inversion on both inline and separate-interface entries
        assert await hass.config_entries.async_reload(entry.entry_id)
        await hass.async_block_till_done()
        _connected(entry)
        assert "does not generate unique IDs" not in caplog.text
        await _feed(hass, entry, "*25*31#0*301#4#01##", "*25*31#0*302#4#01##")
        assert _state(hass, f"{PRI}-garage-garage_door") == "off"
        assert _state(hass, f"{PRI}-side_gate-door") == "on"


async def test_named_yaml_key_gate_301_coexists_with_contact_31_and_does_not_hijack_on_removal(
    hass: HomeAssistant, caplog: pytest.LogCaptureFixture
) -> None:
    """Named hyphenated YAML key ``gate-301`` never steals ``25-31`` / ``31`` config or invert switches."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id=PRI,
        data={"host": "127.0.0.1", "mac": PRI, "port": 20000, "password": "pass"},
        options={
            CONF_MIGRATED_YAML_PLATFORMS: {
                "binary_sensor": {
                    "gate-301": {"who": "25", "where": "329", "name": "Gate", "class": "opening"},
                    "25-31": {"who": "25", "where": "31", "name": "Front Window", "class": "window"},
                }
            }
        },
    )
    entry.add_to_hass(hass)
    registry = er.async_get(hass)
    gate_reg = registry.async_get_or_create(
        "binary_sensor", DOMAIN, f"{PRI}-gate-301-opening", config_entry=entry, original_device_class="opening"
    )
    p1, p2, p3 = _setup_patches()
    with p1, p2, p3:
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
        _connected(entry)
        assert "does not generate unique IDs" not in caplog.text

        gate_state = hass.states.get(gate_reg.entity_id)
        assert gate_state is not None
        assert gate_state.attributes["Sensor"] == "(3)29"
        assert gate_state.attributes["device_class"] == "opening"

        win_eid = registry.async_get_entity_id("binary_sensor", DOMAIN, f"{PRI}-25-31-window")
        assert win_eid is not None
        win_state = hass.states.get(win_eid)
        assert win_state is not None
        assert win_state.attributes["Sensor"] == "(3)1"
        assert win_state.attributes["device_class"] == "window"
        assert sorted(_unique_ids(hass, entry, "switch")) == [
            f"{PRI}-25-31-invert",
            f"{PRI}-25-gate-301-invert",
        ]


@pytest.mark.parametrize("contact_where", ["31", "301"])
async def test_removed_named_yaml_key_gate_301_does_not_steal_contact_31_invert_switch(
    hass: HomeAssistant, caplog: pytest.LogCaptureFixture, contact_where: str
) -> None:
    """After ``gate-301`` leaves YAML, its stale registry entry must not hijack another contact at WHERE ``31``/``301``."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id=PRI,
        data={"host": "127.0.0.1", "mac": PRI, "port": 20000, "password": "pass"},
        options={
            CONF_MIGRATED_YAML_PLATFORMS: {
                "binary_sensor": {
                    "front_window": {
                        "who": "25",
                        "where": contact_where,
                        "name": "Front Window",
                        "class": "window",
                    },
                }
            }
        },
    )
    entry.add_to_hass(hass)
    registry = er.async_get(hass)
    gate_reg = registry.async_get_or_create(
        "binary_sensor", DOMAIN, f"{PRI}-gate-301-opening", config_entry=entry, original_device_class="opening"
    )
    p1, p2, p3 = _setup_patches()
    with p1, p2, p3:
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
        _connected(entry)
        assert "does not generate unique IDs" not in caplog.text

        fw_eid = registry.async_get_entity_id("binary_sensor", DOMAIN, f"{PRI}-front_window-window")
        assert fw_eid is not None
        fw_inv = registry.async_get_entity_id("switch", DOMAIN, f"{PRI}-25-front_window-invert")
        assert fw_inv is not None
        assert f"{PRI}-25-31" not in _contact_devices(hass, entry)
        await _feed(hass, entry, "*25*31#0*31##")
        assert hass.states.get(fw_eid).state == "on"
        assert hass.states.get(gate_reg.entity_id).state == "off"


async def test_live_myhome_yaml_file_loads_routed_contacts_and_motion_sensors(
    hass: HomeAssistant, tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """Real ``myhome.yaml`` parsing through ``validate.config_schema`` supports ``#4#II`` and ``interface:``."""
    yaml_file = tmp_path / "myhome.yaml"
    yaml_file.write_text(
        f"""
gateway:
  mac: {PRI}
  binary_sensor:
    garage:
      who: 25
      where: 301#4#01
      name: Garage
      class: garage_door
    side_gate:
      who: 25
      where: 302
      interface: 1
      name: Side Gate
      class: door
    hall_pir:
      who: 1
      where: 0004#4#1
      name: Hall PIR
      class: motion
""",
        encoding="utf-8",
    )
    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id=PRI,
        data={"host": "127.0.0.1", "mac": PRI, "port": 20000, "password": "pass"},
        options={CONF_FILE_PATH: str(yaml_file)},
    )
    entry.add_to_hass(hass)
    p1, p2, p3 = _setup_patches()
    with p1, p2, p3:
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
        _connected(entry)
        assert "does not generate unique IDs" not in caplog.text

        await _feed(hass, entry, "*25*31#0*301#4#01##", "*25*31#0*302#4#01##", "*1*34*04#4#01##")
        assert _state(hass, f"{PRI}-25-301#4#01-garage_door") == "on"
        assert _state(hass, f"{PRI}-25-302#4#01-door") == "on"
        assert _state(hass, f"{PRI}-1-04#4#01-motion") == "on"

        pir_eid = er.async_get(hass).async_get_entity_id("binary_sensor", DOMAIN, f"{PRI}-1-04#4#01-motion")
        assert pir_eid is not None
        pir_state = hass.states.get(pir_eid)
        assert pir_state is not None
        assert pir_state.attributes["A"] == "0"
        assert pir_state.attributes["PL"] == "4"


async def test_routed_who1_motion_sensor_restoration_discovery_and_anonymous_reply(
    hass: HomeAssistant, caplog: pytest.LogCaptureFixture
) -> None:
    """Restored and bus-discovered F422 motion sensors keep ``#4#01``, poll routed WHERE, and ignore local ``04``."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id=PRI,
        data={"host": "127.0.0.1", "mac": PRI, "port": 20000, "password": "pass"},
        options={
            CONF_MIGRATED_YAML_PLATFORMS: {
                "binary_sensor": {
                    "1-04": {"who": "1", "where": "04", "name": "Local Hall PIR", "class": "motion"},
                }
            }
        },
    )
    entry.add_to_hass(hass)
    registry, devices = er.async_get(hass), dr.async_get(hass)
    routed_dev = devices.async_get_or_create(
        config_entry_id=entry.entry_id,
        identifiers={(DOMAIN, f"{PRI}-1-04#4#01")},
        name="Motion Sensor 04I01",
    )
    routed_reg = registry.async_get_or_create(
        "binary_sensor",
        DOMAIN,
        f"{PRI}-1-04#4#01-motion",
        config_entry=entry,
        device_id=routed_dev.id,
        original_device_class="motion",
    )
    # Also restore a second routed PIR without device_id to exercise default routed registry naming
    orphan_reg = registry.async_get_or_create(
        "binary_sensor",
        DOMAIN,
        f"{PRI}-05#4#1-motion",
        config_entry=entry,
        original_device_class="motion",
    )

    p1, p2, p3 = _setup_patches()
    with p1, p2, p3:
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
        _connected(entry)
        assert "does not generate unique IDs" not in caplog.text

        # Both routed device {PRI}-1-04#4#01 and local YAML {PRI}-1-04 exist separately
        who1_devs = sorted(
            ident
            for d in dr.async_entries_for_config_entry(devices, entry.entry_id)
            for dom, ident in d.identifiers
            if dom == DOMAIN and "-1-" in ident
        )
        assert f"{PRI}-1-04" in who1_devs
        assert f"{PRI}-1-04#4#01" in who1_devs
        assert f"{PRI}-1-05#4#01" in who1_devs
        assert registry.async_get(routed_reg.entity_id).device_id == routed_dev.id
        assert hass.states.get(orphan_reg.entity_id) is not None

        # Routed motion triggers only 04#4#01; local motion triggers only 04; bus-discovered 06#4#01 stays separate
        await _feed(hass, entry, "*1*34*04#4#01##", "*1*34*06#4#01##")
        assert _state(hass, f"{PRI}-1-04#4#01-motion") == "on"
        assert _state(hass, f"{PRI}-1-04-motion") == "off"
        assert _state(hass, f"{PRI}-06#4#01-motion") == "on"

        # Anonymous WHERE=0 dimension 5 reply routes to routed PIR when its request is in flight
        entry.runtime_data.gateway.claim_in_flight_dimension_where = MagicMock(return_value="04#4#1")
        await _feed(hass, entry, "*#1*0*5*3##")
        assert hass.states.get(routed_reg.entity_id).attributes["Sensitivity"] == "very high"


def test_foreign_where_params_and_out_of_range_interfaces_are_not_rewritten_as_local_contacts() -> None:
    """Non-F422 ``where_param`` (ZigBee ``#9``, ``#1``) and out-of-range ``#4#99`` never map onto local ``31``."""
    for raw in (
        "*25*31#0*301#1##",
        "*25*31#1*301#01#9##",
        "*25*31#0*301#4#99##",
    ):
        assert _contact_address(OWNMessage.parse(raw)) is None

    for raw in (
        "*1*34*04#9##",
        "*1*34*04#4#99##",
    ):
        assert _motion_address(OWNMessage.parse(raw)) is None

    assert _contact_address(OWNMessage.parse("*25*31#0*301#4#1##")) == Address("31", "01")
    assert _motion_address(OWNMessage.parse("*1*34*04#4#1##")) == Address("04", "01")

    synthetic_conflict = MagicMock(interface="01", where="301", where_param=["9"])
    assert _bus_interface_from_message(synthetic_conflict) == (False, None)

    assert canonical_contact_where("301#4#99") == "301#4#99"
    assert padded_contact_where("31#4#99") == "31#4#99"
    assert contact_where_aliases("301#4#99") == ["301#4#99"]
    assert binary_sensor_canonical_where("25", "301#4#99") == "301#4#99"
    assert binary_sensor_canonical_where("9", "01") == "01"
    assert _spellings("04#4#1") == ["04#4#1", "04#4#01"]
    assert _spellings("04#4#99") == ["04#4#99"]

    assert Address.from_device_id("301#4#99") == Address("301#4#99", None)
    assert Address.from_config("side", {"where": "301", "interface": 1}) == Address("301", "01")
    assert Address.from_config("side", {"where": "301", "interface": 0}) == Address("301", "00")
    assert Address.from_config("side", {"where": "301#4#1", "interface": "01"}) == Address("301", "01")
    assert Address.from_config("bad", {"where": "301#4#99"}) == Address("301#4#99", None)


async def test_single_digit_routed_registry_contact_and_invert_switch_share_one_device(
    hass: HomeAssistant, caplog: pytest.LogCaptureFixture
) -> None:
    """Single-digit ``#4#1`` registry entries and ``interface: '1'`` configs unify on ``{mac}-25-301#4#01``."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id=PRI,
        data={"host": "127.0.0.1", "mac": PRI, "port": 20000, "password": "pass"},
        options={
            CONF_MIGRATED_YAML_PLATFORMS: {
                "binary_sensor": {
                    "side": {"who": "25", "where": "302", "interface": "1", "name": "Side", "class": "door"},
                }
            }
        },
    )
    entry.add_to_hass(hass)
    er.async_get(hass).async_get_or_create(
        "binary_sensor",
        DOMAIN,
        f"{PRI}-301#4#1-opening",
        config_entry=entry,
        original_device_class="opening",
    )
    p1, p2, p3 = _setup_patches()
    with p1, p2, p3:
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
        _connected(entry)
        assert "does not generate unique IDs" not in caplog.text

        assert _contact_devices(hass, entry) == [f"{PRI}-25-301#4#01", f"{PRI}-25-side"]
        assert _unique_ids(hass, entry, "switch") == [
            f"{PRI}-25-301#4#01-invert",
            f"{PRI}-25-side-invert",
        ]

        # Foreign where_param frames on the live bus must not create or toggle local contact 31
        await _feed(hass, entry, "*25*31#0*301#1##", "*25*31#1*301#01#9##", "*25*31#0*301#4#99##")
        assert f"{PRI}-31-opening" not in _unique_ids(hass, entry)


async def test_standby_follower_deduplicates_against_primary_legacy_single_digit_routed_contact(
    hass: HomeAssistant,
) -> None:
    """Standby ``31#4#01`` deduplicates against primary legacy ``{PRI}-301#4#1-opening`` and ``-25-301#4#1``."""
    pri_entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id=PRI,
        data={"host": "192.168.0.35", "mac": PRI, "port": 20000, "password": "pass"},
        options=SHARED_PRIMARY,
    )
    sb_entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id=SB,
        data={"host": "192.168.0.36", "mac": SB, "port": 20000, "password": "pass"},
        options={
            **SHARED_STANDBY,
            CONF_MIGRATED_YAML_PLATFORMS: {
                "binary_sensor": {
                    "25-31#4#01": {
                        "who": "25",
                        "where": "31",
                        "interface": "01",
                        "name": "Routed Contact",
                        "class": "opening",
                    }
                }
            },
        },
    )
    pri_entry.add_to_hass(hass)
    registry, devices = er.async_get(hass), dr.async_get(hass)
    pri_dev = devices.async_get_or_create(
        config_entry_id=pri_entry.entry_id,
        identifiers={(DOMAIN, f"{PRI}-25-301#4#1")},
        name="Dry Contact 301I01",
    )
    registry.async_get_or_create(
        "binary_sensor",
        DOMAIN,
        f"{PRI}-301#4#1-opening",
        config_entry=pri_entry,
        device_id=pri_dev.id,
        original_device_class="opening",
    )

    p1, p2, p3 = _setup_patches()
    with p1, p2, p3:
        assert await hass.config_entries.async_setup(pri_entry.entry_id)
        await hass.async_block_till_done()

        sb_entry.add_to_hass(hass)
        sb_dev = devices.async_get_or_create(
            config_entry_id=sb_entry.entry_id,
            identifiers={(DOMAIN, f"{SB}-25-31#4#01")},
            name="Dry Contact 31I01",
        )
        registry.async_get_or_create(
            "binary_sensor",
            DOMAIN,
            f"{SB}-31#4#01-opening",
            config_entry=sb_entry,
            device_id=sb_dev.id,
            original_device_class="opening",
        )
        registry.async_get_or_create(
            "switch",
            DOMAIN,
            f"{SB}-25-31#4#01-invert",
            config_entry=sb_entry,
            device_id=sb_dev.id,
        )
        assert await hass.config_entries.async_setup(sb_entry.entry_id)
        await hass.async_block_till_done()

        assert _unique_ids(hass, pri_entry) == [f"{PRI}-301#4#1-opening"]
        assert _unique_ids(hass, sb_entry) == []
        assert _unique_ids(hass, sb_entry, "switch") == []
        assert _contact_devices(hass, sb_entry) == []


async def test_routed_who1_sensors_and_foreign_where_params_do_not_poison_local_lights(
    hass: HomeAssistant, caplog: pytest.LogCaptureFixture
) -> None:
    """Routed ``#4#II`` WHO 1 sensors and foreign ``where_param`` frames keep local lights isolated."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id=PRI,
        data={"host": "127.0.0.1", "mac": PRI, "port": 20000, "password": "pass"},
        options={
            CONF_MIGRATED_YAML_PLATFORMS: {
                "binary_sensor": {
                    "1-04#4#01": {
                        "who": "1",
                        "where": "04",
                        "interface": "01",
                        "name": "Routed PIR 04",
                        "class": "motion",
                    }
                }
            }
        },
    )
    entry.add_to_hass(hass)
    registry = er.async_get(hass)
    # Unconfigured local light at 04 (must NOT be pruned by routed PIR 04#4#01)
    local_light_04 = registry.async_get_or_create("light", DOMAIN, f"{PRI}-1-04", config_entry=entry)
    # Unconfigured routed light at 05#4#01 (must NOT be pruned by local PIR 05)
    routed_light_05 = registry.async_get_or_create("light", DOMAIN, f"{PRI}-1-05#4#01", config_entry=entry)
    registry.async_get_or_create(
        "binary_sensor",
        DOMAIN,
        f"{PRI}-1-05-motion",
        config_entry=entry,
        original_device_class="motion",
    )
    # Unconfigured routed ghost light at 06#4#01 (MUST be pruned because routed PIR 06#4#01 exists)
    ghost_routed_06 = registry.async_get_or_create("light", DOMAIN, f"{PRI}-1-06#4#01", config_entry=entry)
    registry.async_get_or_create(
        "binary_sensor",
        DOMAIN,
        f"{PRI}-06#4#01-motion",
        config_entry=entry,
        original_device_class="motion",
    )
    # WHO 25 dry contact with motion device_class must NOT poison WHO 1 light 25
    light_25 = registry.async_get_or_create("light", DOMAIN, f"{PRI}-1-25", config_entry=entry)
    registry.async_get_or_create(
        "binary_sensor",
        DOMAIN,
        f"{PRI}-25-31-motion",
        config_entry=entry,
        original_device_class="motion",
    )

    p1, p2, p3 = _setup_patches()
    with p1, p2, p3:
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
        _connected(entry)

        light_uids = _unique_ids(hass, entry, "light")
        assert f"{PRI}-1-04" in light_uids
        assert f"{PRI}-1-05#4#01" in light_uids
        assert f"{PRI}-1-25" in light_uids
        assert f"{PRI}-1-06#4#01" not in light_uids
        assert registry.async_get(ghost_routed_06.entity_id) is None

        # Local light 04 turns on; routed 04#4#01 / 04#4#1 ON frames are swallowed by routed PIR 04#4#01
        await _feed(hass, entry, "*1*1*04##", "*1*1*05#4#01##", "*1*1*04#4#01##", "*1*1*04#4#1##")
        assert hass.states.get(local_light_04.entity_id).state == "on"
        assert hass.states.get(routed_light_05.entity_id).state == "on"
        assert hass.states.get(light_25.entity_id) is not None
        assert f"{PRI}-1-04#4#01" not in _unique_ids(hass, entry, "light")

        # Foreign where_param frames (ZigBee #9, out-of-range #4#99) do not poison or discover local 07
        await _feed(
            hass,
            entry,
            "*1*34*07#9##",
            "*1*34*07#4#99##",
            "*#1*07#9*6*120##",
            "*#1*07#4#99*6*120##",
            "*1*1*07#9##",
            "*1*1*07#4#99##",
        )
        assert f"{PRI}-1-07" not in _unique_ids(hass, entry, "light")
        assert f"{PRI}-07-illuminance" not in _unique_ids(hass, entry, "sensor")

        # Subsequent genuine local *1*1*07## still discovers local light 07 cleanly
        await _feed(hass, entry, "*1*1*07##")
        assert f"{PRI}-1-07" in _unique_ids(hass, entry, "light")

        # Routed illuminance *#1*08#4#01*6*250## and single-digit *1*34*09#4#1## keep local 08 / 09 isolated
        await _feed(
            hass,
            entry,
            "*#1*08#4#01*6*250##",
            "*1*1*08##",
            "*1*1*08#4#01##",
            "*1*34*09#4#1##",
            "*1*1*09#4#01##",
            "*1*1*09##",
        )
        assert f"{PRI}-08#4#01-illuminance" in _unique_ids(hass, entry, "sensor")
        assert f"{PRI}-08#4#01-motion" not in _unique_ids(hass, entry, "binary_sensor")
        assert f"{PRI}-09#4#01-motion" in _unique_ids(hass, entry, "binary_sensor")
        assert f"{PRI}-1-08" in _unique_ids(hass, entry, "light")
        assert f"{PRI}-1-08#4#01" not in _unique_ids(hass, entry, "light")
        assert f"{PRI}-1-09" in _unique_ids(hass, entry, "light")
        assert f"{PRI}-1-09#4#01" not in _unique_ids(hass, entry, "light")
        lux_eid = registry.async_get_entity_id("sensor", DOMAIN, f"{PRI}-08#4#01-illuminance")
        assert lux_eid is not None
        lux_entry = registry.async_get(lux_eid)
        assert lux_entry is not None and lux_entry.device_id is not None
        lux_dev = dr.async_get(hass).async_get(lux_entry.device_id)
        assert lux_dev is not None and lux_dev.name == "Illuminance 08I01"


def test_legacy_yaml_preserves_interface_zero_and_zero_pads_single_digit_interfaces() -> None:
    """``_apply_yaml_platforms`` preserves integer ``interface: 0`` as ``#4#00`` and pads ``1`` -> ``#4#01``."""
    from custom_components.myhome.legacy_yaml import _apply_yaml_platforms

    configured: dict[str, dict[str, dict[str, object]]] = {
        "binary_sensor": {},
        "climate": {},
    }
    _apply_yaml_platforms(
        {
            "binary_sensor": {
                "bus0_contact": {"who": "25", "where": "31", "interface": 0, "name": "Bus 0"},
                "bus1_contact": {"who": "25", "where": "31", "bus_interface": 1, "name": "Bus 1"},
            },
            "climate": {
                "bus0_zone": {"who": "4", "zone": "1", "interface": 0, "name": "Zone 1 Bus 0"},
            },
        },
        configured,
    )
    assert "31#4#00" in configured["binary_sensor"]
    assert "31#4#01" in configured["binary_sensor"]
    assert "31" not in configured["binary_sensor"]
    assert "1#4#00" in configured["climate"]
    assert "1" not in configured["climate"]
    assert "zone_1" not in configured["climate"]


async def test_aux_foreign_where_params_null_guards_and_routed_contact_default_names(
    hass: HomeAssistant,
) -> None:
    """WHO 9 rejects foreign ``where_param``, null guards return ``None``, and routed contact names use ``I<iface>``."""
    assert _aux_address(OWNMessage.parse("*9*1*1##")) == Address("1")
    assert _aux_address(OWNMessage.parse("*9*1*1#9##")) is None
    assert _aux_address(OWNMessage.parse("*9*1*1#4#01##")) is None
    assert _aux_address(MagicMock(channel=None, where_param=[])) is None
    assert _contact_address(MagicMock(where=None, interface=None, where_param=[])) is None

    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id=PRI,
        data={"host": "127.0.0.1", "mac": PRI, "port": 20000, "password": "pass"},
        options={
            CONF_MIGRATED_YAML_PLATFORMS: {
                "binary_sensor": {
                    "9-1": {"who": "9", "where": "1", "name": "Aux 1", "class": "running"},
                }
            }
        },
    )
    entry.add_to_hass(hass)
    registry = er.async_get(hass)
    restored_routed = registry.async_get_or_create(
        "binary_sensor",
        DOMAIN,
        f"{PRI}-301#4#01-opening",
        config_entry=entry,
        original_device_class="opening",
    )

    p1, p2, p3 = _setup_patches()
    with p1, p2, p3:
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
        _connected(entry)

        # Foreign-qualified WHO 9 frames do not toggle local Aux 1; genuine *9*1*1## does
        await _feed(hass, entry, "*9*1*1#9##", "*9*1*1#4#01##")
        assert _state(hass, f"{PRI}-9-1-running") == "off"
        await _feed(hass, entry, "*9*1*1##")
        assert _state(hass, f"{PRI}-9-1-running") == "on"

        # Restored routed contact 301#4#01 and bus-discovered 32#4#01 both use 'I01' default device names
        await _feed(hass, entry, "*25*31#0*32#4#01##")
        devices = dr.async_get(hass)
        restored_entry = registry.async_get(restored_routed.entity_id)
        assert restored_entry is not None and restored_entry.device_id is not None
        restored_dev = devices.async_get(restored_entry.device_id)
        assert restored_dev is not None and restored_dev.name == "Dry Contact 301I01"

        bus_eid = registry.async_get_entity_id("binary_sensor", DOMAIN, f"{PRI}-32#4#01-opening")
        assert bus_eid is not None
        bus_entry = registry.async_get(bus_eid)
        assert bus_entry is not None and bus_entry.device_id is not None
        bus_dev = devices.async_get(bus_entry.device_id)
        assert bus_dev is not None and bus_dev.name == "Dry Contact 32I01"
