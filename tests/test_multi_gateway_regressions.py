"""Regression tests for the #459 audit of multi-gateway support (#453).

Each test pins a behaviour the first implementation got wrong: the reload after
a role change, delegation on the primary side, failover issue timing, double
bridging, validation of the primary, reply bridging, shared-bus detection
false positives, and a primary that disappears.
"""
import asyncio
import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, create_autospec, patch

from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers import issue_registry as ir
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from OWNd.connection import OWNCommandSession
from OWNd.message import OWNCommand, OWNLightingEvent, OWNMessage
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.myhome.const import (
    CONF_BUS_TOPOLOGY,
    CONF_DECODER_ENTITY,
    CONF_DELEGATED_WHOS,
    CONF_GATEWAY_ROLE,
    CONF_PRIMARY_GATEWAY,
    DOMAIN,
    ISSUE_GATEWAY_FAILOVER,
    ISSUE_PRIMARY_GATEWAY_MISSING,
    ISSUE_SHARED_BUS_DETECTED,
    ROLE_PRIMARY,
    ROLE_SECONDARY,
    ROLE_STANDBY,
    TOPOLOGY_SHARED,
    TOPOLOGY_STANDALONE,
)
from custom_components.myhome.discovery import PlatformDiscovery
from custom_components.myhome.gateway import AVAILABILITY_GRACE
from custom_components.myhome.topology import (
    async_check_primary_links,
    entry_delegated_whos,
    entry_for_mac,
    entry_mac,
    peer_unique_id,
    topology_signature,
)
from tests.test_multi_gateway import _create_mock_gateway

PRI = "00:03:50:aa:bb:01"
SB = "00:03:50:aa:bb:02"
SEC = "00:03:50:aa:bb:03"
FAILOVER_ISSUE = f"{ISSUE_GATEWAY_FAILOVER}_000350aabb01"
TRACES = Path(__file__).parent / "fixtures" / "traces" / "issue_453"


def _issue(hass: HomeAssistant, issue_id: str) -> ir.IssueEntry | None:
    return ir.async_get(hass).async_get_issue(DOMAIN, issue_id)


def _pair(hass: HomeAssistant) -> tuple:
    """A shared primary (set up long ago) and its connected warm standby."""
    _, gw_p = _create_mock_gateway(hass, PRI, topology=TOPOLOGY_SHARED, role=ROLE_PRIMARY)
    _, gw_s = _create_mock_gateway(hass, SB, topology=TOPOLOGY_SHARED, role=ROLE_STANDBY, primary_gateway=PRI)
    gw_p._setup_at -= 2 * AVAILABILITY_GRACE
    gw_s.is_connected = gw_s._available = True
    return gw_p, gw_s


# ── failover issue timing ─────────────────────────────────────────────────


async def test_no_failover_issue_when_the_standby_is_down_too(hass: HomeAssistant) -> None:
    """An outage past the grace with the standby offline is a plain outage, not a failover."""
    gw_p, gw_s = _pair(hass)
    gw_s.is_connected = gw_s._available = False
    gw_p.is_connected = gw_p._available = True
    gw_p._on_event_connection_state_change(False)
    gw_p._mark_unavailable(None)
    assert gw_p.failover_active is False
    assert gw_p.available is False
    assert _issue(hass, FAILOVER_ISSUE) is None


async def test_startup_before_the_primary_connects_is_not_a_failover(hass: HomeAssistant) -> None:
    """The standby connecting first at startup must not raise the issue; a primary that stays down does."""
    gw_p, gw_s = _pair(hass)
    gw_p._setup_at += 2 * AVAILABILITY_GRACE  # just set up, never connected yet
    gw_s._notify_availability()
    assert gw_p.available is True  # carried by the standby meanwhile
    assert gw_p.failover_active is False

    gw_p._setup_at -= 2 * AVAILABILITY_GRACE  # still not up a grace period later
    await gw_s._process_message(OWNLightingEvent.parse("*1*1*21##"))
    assert gw_p.failover_active is True
    assert _issue(hass, FAILOVER_ISSUE) is not None


async def test_commands_during_a_blip_go_through_the_standby_without_an_issue(hass: HomeAssistant) -> None:
    gw_p, gw_s = _pair(hass)
    gw_p.is_connected = gw_p._available = True
    gw_p._on_event_connection_state_change(False)
    with patch.object(gw_s, "send", new_callable=AsyncMock) as sb_send:
        await gw_p.send(OWNCommand.parse("*1*1*21##"))
    sb_send.assert_awaited_once()
    assert _issue(hass, FAILOVER_ISSUE) is None
    gw_p._on_event_connection_state_change(True)  # cancels the grace timer


# ── bridging ──────────────────────────────────────────────────────────────


async def test_a_frame_reaches_the_offline_primary_once(hass: HomeAssistant) -> None:
    """Standby and secondary both see the frame; only the standby bridges it."""
    gw_p, gw_s = _pair(hass)
    _, gw_x = _create_mock_gateway(hass, SEC, topology=TOPOLOGY_SHARED, role=ROLE_SECONDARY, primary_gateway=PRI)
    got: list = []
    unsub = async_dispatcher_connect(hass, f"myhome_message_{gw_p.mac}", got.append)
    await gw_s._process_message(OWNLightingEvent.parse("*1*1*21##"))
    await gw_x._process_message(OWNLightingEvent.parse("*1*1*21##"))
    assert len(got) == 1

    # A connected primary hears the bus itself: nothing is bridged
    gw_p.is_connected = True
    await gw_s._process_message(OWNLightingEvent.parse("*1*0*21##"))
    unsub()
    assert len(got) == 1


async def test_a_secondary_without_standby_does_not_bridge(hass: HomeAssistant) -> None:
    _create_mock_gateway(hass, PRI, topology=TOPOLOGY_SHARED, role=ROLE_PRIMARY)
    _, gw_x = _create_mock_gateway(hass, SEC, topology=TOPOLOGY_SHARED, role=ROLE_SECONDARY, primary_gateway=PRI)
    got: list = []
    unsub = async_dispatcher_connect(hass, f"myhome_message_{PRI}", got.append)
    await gw_x._process_message(OWNLightingEvent.parse("*1*1*21##"))
    unsub()
    assert got == []


async def test_reply_on_the_standby_command_session_reaches_the_primary(hass: HomeAssistant) -> None:
    """A status request sent for the offline primary: its reply comes back on the standby's command session."""
    gw_p, gw_s = _pair(hass)
    reply = OWNMessage.parse("*1*1*21##")
    session = create_autospec(OWNCommandSession, instance=True)
    session.connect.return_value = {"Success": True}
    session.send.return_value = [reply]
    session._stream_reader = object()
    session._stream_writer = object()

    got: list = []
    unsub = async_dispatcher_connect(hass, f"myhome_message_{gw_p.mac}", got.append)
    gw_s._event_session_ready.set()
    with patch("custom_components.myhome.gateway.OWNCommandSession", return_value=session):
        worker = asyncio.create_task(gw_s.sending_loop(0))
        try:
            written = await gw_p.send_status_request(OWNCommand.parse("*#1*21##"))
            await asyncio.wait_for(asyncio.shield(written), timeout=5)
        finally:
            await gw_s.send_buffer.put(None)
            await asyncio.wait_for(worker, timeout=5)
    unsub()
    assert [str(m) for m in got] == ["*1*1*21##"]


# ── delegation ────────────────────────────────────────────────────────────


def _light_discovery(hass: HomeAssistant, entry, build) -> tuple[PlatformDiscovery, MagicMock]:
    add = MagicMock()
    return PlatformDiscovery(
        hass, entry, add, platform="light", who="1", event_type=OWNLightingEvent, build=build,
    ), add


async def test_primary_leaves_a_delegated_who_to_its_secondary(hass: HomeAssistant) -> None:
    entry_p, gw_p = _create_mock_gateway(hass, PRI, topology=TOPOLOGY_SHARED, role=ROLE_PRIMARY)
    _create_mock_gateway(
        hass, SEC, topology=TOPOLOGY_SHARED, role=ROLE_SECONDARY, primary_gateway=PRI, delegated_whos=[1, 2]
    )
    assert gw_p.delegated_away_whos == {1, 2}

    discovery, add = _light_discovery(hass, entry_p, lambda ctx: MagicMock())
    discovery.handle_message(OWNLightingEvent.parse("*1*1*21##"))
    add.assert_not_called()

    gw_p.send_status_request = AsyncMock()
    await gw_p.initial_discovery()
    assert [str(c.args[0]) for c in gw_p.send_status_request.await_args_list] == ["*#4*0##", "*#16*0*5##"]


async def test_secondary_does_not_duplicate_a_device_the_primary_already_has(hass: HomeAssistant) -> None:
    registry = er.async_get(hass)
    entry_p, _ = _create_mock_gateway(hass, PRI, topology=TOPOLOGY_SHARED, role=ROLE_PRIMARY)
    entry_x, _ = _create_mock_gateway(
        hass, SEC, topology=TOPOLOGY_SHARED, role=ROLE_SECONDARY, primary_gateway=PRI, delegated_whos=[1]
    )
    registry.async_get_or_create("light", DOMAIN, f"{PRI}-1-21", config_entry=entry_p)

    def build(ctx):
        entity = MagicMock()
        entity.unique_id = f"{SEC}-1-{ctx.address.where}"
        return entity

    discovery, add = _light_discovery(hass, entry_x, build)
    discovery.handle_message(OWNLightingEvent.parse("*1*1*21##"))
    add.assert_not_called()
    assert "21" in discovery.known  # decided once, not on every frame

    discovery.handle_message(OWNLightingEvent.parse("*1*1*22##"))
    add.assert_called_once()  # a new device of the delegated WHO


async def test_restore_prunes_a_delegated_duplicate_in_favour_of_the_primary(hass: HomeAssistant) -> None:
    registry = er.async_get(hass)
    entry_p, _ = _create_mock_gateway(hass, PRI, topology=TOPOLOGY_SHARED, role=ROLE_PRIMARY)
    entry_x, _ = _create_mock_gateway(
        hass, SEC, topology=TOPOLOGY_SHARED, role=ROLE_SECONDARY, primary_gateway=PRI, delegated_whos=[1]
    )
    registry.async_get_or_create("light", DOMAIN, f"{PRI}-1-21", config_entry=entry_p)
    dup = registry.async_get_or_create("light", DOMAIN, f"{SEC}-1-21", config_entry=entry_x)
    discovery, _ = _light_discovery(hass, entry_x, lambda ctx: MagicMock())
    assert discovery.restore() == []
    assert registry.async_get(dup.entity_id) is None


async def test_non_numeric_platform_who_always_discovers(hass: HomeAssistant) -> None:
    entry_x, _ = _create_mock_gateway(hass, SEC, topology=TOPOLOGY_SHARED, role=ROLE_SECONDARY, primary_gateway=PRI)
    discovery = PlatformDiscovery(
        hass, entry_x, MagicMock(), platform="light", who="x", event_type=None, build=lambda ctx: None
    )
    assert discovery._discovers() is True


# ── shared-bus detection ──────────────────────────────────────────────────


def _replay(hass: HomeAssistant, first, second) -> None:
    """Feed both #453 traces, interleaved on their real timestamps, to two handlers."""
    events = []
    for gw, name in ((first, "myhome_trace_F454_all_2026-09-24T19-12-29.json"),
                     (second, "myhome_trace_MH202_all_2026-09-24T19-12-34.json")):
        for frame in json.loads((TRACES / name).read_text(encoding="utf-8"))["frames"]:
            events.append((frame["timestamp"], gw, frame))
    events.sort(key=lambda e: e[0])
    with patch("custom_components.myhome.gateway.time") as clock:
        for ts, gw, frame in events:
            clock.monotonic.return_value = ts
            if frame["direction"] == "tx":
                gw._record_tx(ts, frame["raw"])
            else:
                gw._correlate_shared_bus_traffic(OWNMessage.parse(frame["raw"]))



def test_passive_golden_traces_do_not_flag_shared_bus(hass: HomeAssistant) -> None:
    """Replay MHS1 and H4890 traces that contain 87 concurrent RX frames but no cross-gateway TX echoes."""
    _, gw_a = _create_mock_gateway(hass, PRI)
    _, gw_b = _create_mock_gateway(hass, SB)

    events = []
    import json

    for gw, name in ((gw_a, "myhome_trace_MyHomeServer1_all_2026-09-25T19-40-15.json"),
                     (gw_b, "myhome_trace_H4890_all_2026-09-25T19-40-19.json")):
        for frame in json.loads((TRACES / name).read_text(encoding="utf-8"))["frames"]:
            events.append((frame["timestamp"], gw, frame))
    events.sort(key=lambda e: e[0])

    with patch("custom_components.myhome.gateway.time") as clock:
        for ts, gw, frame in events:
            clock.monotonic.return_value = ts
            from OWNd.message import OWNMessage
            if frame["direction"] == "tx":
                gw._record_tx(ts, frame["raw"])
            elif frame["direction"] == "rx":
                gw._correlate_shared_bus_traffic(OWNMessage.parse(frame["raw"]))

    # Even though they share 87 frames identically timed, there were no TX->RX echoes
    # from HA (the user just pressed physical buttons), so we MUST NOT flag it.
    from custom_components.myhome.const import ISSUE_SHARED_BUS_DETECTED
    assert _issue(hass, f"{ISSUE_SHARED_BUS_DETECTED}_{gw_a.mac.replace(":", "")}_{gw_b.mac.replace(":", "")}") is None
    assert _issue(hass, f"{ISSUE_SHARED_BUS_DETECTED}_{gw_b.mac.replace(":", "")}_{gw_a.mac.replace(":", "")}") is None

def test_golden_traces_flag_an_unconfigured_shared_bus(hass: HomeAssistant) -> None:
    _, gw_a = _create_mock_gateway(hass, PRI)
    _, gw_b = _create_mock_gateway(hass, SB)
    _replay(hass, gw_a, gw_b)
    assert _issue(hass, f"{ISSUE_SHARED_BUS_DETECTED}_000350aabb01_000350aabb02") is not None


def test_golden_traces_of_a_configured_pair_raise_nothing(hass: HomeAssistant) -> None:
    _, gw_a = _create_mock_gateway(hass, PRI, topology=TOPOLOGY_SHARED, role=ROLE_PRIMARY)
    _, gw_b = _create_mock_gateway(hass, SB, topology=TOPOLOGY_SHARED, role=ROLE_STANDBY, primary_gateway=PRI)
    _replay(hass, gw_a, gw_b)
    assert not hass.data[DOMAIN].get("_shared_bus_evidence")
    assert not [i for (d, i) in ir.async_get(hass).issues if d == DOMAIN]


def test_same_command_sent_to_two_separate_buses_is_not_evidence(hass: HomeAssistant) -> None:
    """An automation switching address 11 on both buses: each gateway only sees its own echo."""
    _, gw_a = _create_mock_gateway(hass, PRI)
    _, gw_b = _create_mock_gateway(hass, SB)
    with patch("custom_components.myhome.gateway.time") as clock:
        for n in range(5):
            clock.monotonic.return_value = 100.0 + n
            gw_a._record_tx(100.0 + n, "*1*1*0##")
            gw_b._record_tx(100.0 + n, "*1*1*0##")
            gw_a._correlate_shared_bus_traffic(OWNLightingEvent.parse("*1*1*0##"))
            gw_b._correlate_shared_bus_traffic(OWNLightingEvent.parse("*1*1*0##"))
    assert not hass.data[DOMAIN].get("_shared_bus_evidence")


def test_golden_standby_bridges_bus_frames_but_not_its_own(hass: HomeAssistant) -> None:
    gw_p, gw_s = _pair(hass)
    got: list = []
    unsub = async_dispatcher_connect(hass, f"myhome_message_{gw_p.mac}", got.append)
    frames = json.loads((TRACES / "myhome_trace_MH202_all_2026-09-24T19-12-34.json").read_text(encoding="utf-8"))
    for frame in frames["frames"]:
        if frame["direction"] == "rx":
            gw_s._bridge_to_primary(OWNMessage.parse(frame["raw"]))
    unsub()
    raws = [str(m) for m in got]
    assert raws and not any(r.startswith("*#13*") for r in raws)
    assert "*25*21#1*21##" in raws


# ── reload and missing primary (full setup) ───────────────────────────────


def _setup_patches():
    return (
        patch("custom_components.myhome.gateway.OWNSession.test_connection", return_value={"Success": True, "Message": None}),
        patch("custom_components.myhome.gateway.MyHOMEGatewayHandler.listening_loop"),
        patch("custom_components.myhome.gateway.MyHOMEGatewayHandler.sending_loop"),
    )


async def _set_up(hass: HomeAssistant, mac: str, host: str, options: dict) -> MockConfigEntry:
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={"host": host, "port": 20000, "password": "pass", "mac": mac},
        options=options,
        unique_id=mac,
        title=f"{mac} Gateway",
    )
    entry.add_to_hass(hass)
    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return entry


async def test_role_change_reloads_once_the_options_are_saved(hass: HomeAssistant) -> None:
    p1, p2, p3 = _setup_patches()
    with p1, p2, p3:
        entry = await _set_up(hass, PRI, "192.168.0.35", {})
        with patch.object(hass.config_entries, "async_schedule_reload") as reload:
            hass.config_entries.async_update_entry(entry, options={CONF_DECODER_ENTITY.format(1): ""})
            await hass.async_block_till_done()
            reload.assert_not_called()  # not a topology change

            hass.config_entries.async_update_entry(
                entry, options={CONF_BUS_TOPOLOGY: TOPOLOGY_SHARED, CONF_GATEWAY_ROLE: ROLE_PRIMARY}
            )
            await hass.async_block_till_done()
            reload.assert_called_once_with(entry.entry_id)


async def test_removing_the_primary_flags_its_standby(hass: HomeAssistant) -> None:
    p1, p2, p3 = _setup_patches()
    with p1, p2, p3:
        primary = await _set_up(
            hass, PRI, "192.168.0.35", {CONF_BUS_TOPOLOGY: TOPOLOGY_SHARED, CONF_GATEWAY_ROLE: ROLE_PRIMARY}
        )
        standby = await _set_up(
            hass, SB, "192.168.0.36",
            {CONF_BUS_TOPOLOGY: TOPOLOGY_SHARED, CONF_GATEWAY_ROLE: ROLE_STANDBY, CONF_PRIMARY_GATEWAY: PRI},
        )
        issue_id = f"{ISSUE_PRIMARY_GATEWAY_MISSING}_{standby.entry_id}"
        assert _issue(hass, issue_id) is None

        await hass.config_entries.async_remove(primary.entry_id)
        await hass.async_block_till_done()
        assert _issue(hass, issue_id) is not None


# ── topology helpers ──────────────────────────────────────────────────────


def test_topology_helpers_edge_cases(hass: HomeAssistant) -> None:
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={},
        options={CONF_BUS_TOPOLOGY: TOPOLOGY_SHARED, CONF_GATEWAY_ROLE: ROLE_SECONDARY,
                 CONF_PRIMARY_GATEWAY: PRI, CONF_DELEGATED_WHOS: ["x", 5, "16"]},
    )
    assert entry_delegated_whos(entry) == {5, 16}
    assert entry_mac(entry) is None
    assert topology_signature(entry) == (TOPOLOGY_SHARED, ROLE_SECONDARY, PRI, (5, 16))
    assert entry_for_mac(hass, "00:00:00:00:00:00") is None
    assert peer_unique_id("something-else", PRI, SB) is None
    assert entry_delegated_whos(MagicMock(options=None, data=None)) == set()

    _, gw = _create_mock_gateway(hass, PRI, topology=TOPOLOGY_STANDALONE)
    assert gw.bus_group == gw.mac
    gw.hass = None
    assert gw.delegated_away_whos == set()
    gw._record_tx(0.0, "*1*1*21##")  # no hass: nothing to record into


def test_primary_links_skip_the_entry_being_removed(hass: HomeAssistant) -> None:
    entry_s, _ = _create_mock_gateway(hass, SB, topology=TOPOLOGY_SHARED, role=ROLE_STANDBY, primary_gateway=PRI)
    async_check_primary_links(hass, removed=entry_s.entry_id)
    assert _issue(hass, f"{ISSUE_PRIMARY_GATEWAY_MISSING}_{entry_s.entry_id}") is None
    async_check_primary_links(hass)  # its primary does not exist
    assert _issue(hass, f"{ISSUE_PRIMARY_GATEWAY_MISSING}_{entry_s.entry_id}") is not None
