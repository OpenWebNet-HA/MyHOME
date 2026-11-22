"""One dry contact, two spellings: an F455 writes ``301`` where an F461 writes ``31`` (live, 2026-11-18).

Plant: an F461 2.0.11 and an F455 1.0.76 on one SCS bus, an automation dry-contact interface
with contacts 1 and 2. The same answer of contact 1 reached the two event sessions 13 ms apart
as ``*25*31#0*301##`` (F455) and ``*25*31#0*31##`` (F461); each gateway accepted the other's
spelling in ``*#25*WHERE##``. The WHO 25 PDF writes contact 1 as ``31`` (WHERE = ``3`` +
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

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from OWNd.message import OWNMessage
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.myhome.binary_sensor import _contact_peer_spellings, _contact_spellings
from custom_components.myhome.const import (
    CONF_BUS_TOPOLOGY,
    CONF_GATEWAY_ROLE,
    CONF_IGNORED_ADDRESSES,
    CONF_PRIMARY_GATEWAY,
    DOMAIN,
    ROLE_PRIMARY,
    ROLE_STANDBY,
    TOPOLOGY_SHARED,
)
from custom_components.myhome.gateway import AVAILABILITY_GRACE
from custom_components.myhome.where_grammar import canonical_contact_where
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
# Contact 1 opening as an MH200N, an MH202 and an F454 reported it (issue_466 traces)
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
    assert _contact_spellings("301") == ["301", "31"]
    assert _contact_spellings("31") == ["31"]
    assert _contact_spellings("25-301") == ["25-301", "301", "31"]  # a legacy ``25-`` registry id
    assert _contact_peer_spellings(f"{PRI}-301-opening") == [f"{PRI}-31-opening"]
    assert _contact_peer_spellings(f"{PRI}-25-301-motion") == [f"{PRI}-25-31-motion"]
    assert _contact_peer_spellings(f"{PRI}-31-opening") == []
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
        await _feed(hass, entry, *_rx("failover_read_31_through_f455", "f455"))
        assert _unique_ids(hass, entry) == [f"{PRI}-31-opening"]
        assert _state(hass, f"{PRI}-31-opening") == "on"

        # Its "Invert state" switch and its device follow at this start or at the next one,
        # depending on whether the switch platform was set up before the binary sensors.
        assert await hass.config_entries.async_reload(entry.entry_id)
        await hass.async_block_till_done()
        assert _unique_ids(hass, entry) == [f"{PRI}-31-opening"]
        assert _unique_ids(hass, entry, "switch") == [f"{PRI}-25-31-invert"]
        assert _contact_devices(hass, entry) == [f"{PRI}-25-31"]


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
        await hass.config_entries.async_setup(standby.entry_id)
        await hass.async_block_till_done()

        assert _unique_ids(hass, standby) == [f"{SB}-344-opening"]
        assert _contact_devices(hass, standby) == [f"{SB}-25-344"]
        assert _unique_ids(hass, primary) == [f"{PRI}-31-opening"]


@pytest.mark.parametrize("typed", ["25/301", "25/31"])
async def test_an_ignored_contact_stays_ignored_in_either_spelling(hass: HomeAssistant, typed: str) -> None:
    p1, p2, p3 = _setup_patches()
    with p1, p2, p3:
        entry = await _set_up(hass, PRI, "192.168.0.35", {CONF_IGNORED_ADDRESSES: [typed]})
        await _feed(hass, entry, *(raw for _t, _gw, raw in FRAMES["status_read_301_302"]))

        assert _unique_ids(hass, entry) == [f"{PRI}-32-opening"]
