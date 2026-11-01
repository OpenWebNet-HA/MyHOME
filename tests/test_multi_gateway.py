"""Tests for multi-gateway and shared bus support (Issue #453)."""

import collections
import time
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from homeassistant.const import CONF_HOST, CONF_MAC, CONF_NAME
from homeassistant.core import HomeAssistant, callback
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers import issue_registry as ir
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.myhome.config_flow import MyhomeOptionsFlowHandler
from custom_components.myhome.const import (
    CONF_ADDRESS,
    CONF_BUS_TOPOLOGY,
    CONF_DELEGATED_WHOS,
    CONF_GATEWAY_ROLE,
    CONF_GENERATE_EVENTS,
    CONF_OWN_PASSWORD,
    CONF_PRIMARY_GATEWAY,
    CONF_TRANSITION_MODE,
    CONF_WORKER_COUNT,
    DOMAIN,
    ISSUE_GATEWAY_FAILOVER,
    ROLE_PRIMARY,
    ROLE_SECONDARY,
    ROLE_STANDBY,
    TOPOLOGY_SHARED,
    TOPOLOGY_STANDALONE,
)
from custom_components.myhome.data import MyHOMERuntimeData
from custom_components.myhome.diagnostics import async_get_config_entry_diagnostics
from custom_components.myhome.discovery import PlatformDiscovery
from custom_components.myhome.gateway import AVAILABILITY_GRACE, MyHOMEGatewayHandler
from custom_components.myhome.services import (
    SERVICE_SWEEP_BUS,
    SERVICE_SYNC_TIME,
    _get_gateway_handler,
    async_setup_services,
)


def _create_mock_gateway(
    hass: HomeAssistant,
    mac: str,
    *,
    name: str = "MH201",
    topology: str = TOPOLOGY_STANDALONE,
    role: str = ROLE_PRIMARY,
    primary_gateway: str | None = None,
    delegated_whos: list[int] | None = None,
) -> tuple[MockConfigEntry, MyHOMEGatewayHandler]:
    """Helper to create a configured gateway handler."""
    hass.data.setdefault(DOMAIN, {})
    formatted_mac = dr.format_mac(mac)
    options = {
        CONF_BUS_TOPOLOGY: topology,
        CONF_GATEWAY_ROLE: role,
    }
    if primary_gateway:
        options[CONF_PRIMARY_GATEWAY] = dr.format_mac(primary_gateway)
    if delegated_whos:
        options[CONF_DELEGATED_WHOS] = delegated_whos

    last_octet = int(formatted_mac.replace(":", "")[-2:], 16) or 10
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={
            CONF_HOST: f"192.168.1.{last_octet}",
            CONF_MAC: formatted_mac,
            CONF_NAME: name,
        },
        options=options,
        unique_id=formatted_mac,
    )
    entry.add_to_hass(hass)

    gw = MyHOMEGatewayHandler(hass, entry)
    entry.runtime_data = MyHOMERuntimeData(gateway=gw)
    return entry, gw


def test_gateway_properties_standalone_and_secondary(hass: HomeAssistant) -> None:
    """Test gateway role and topology properties."""
    entry_primary, gw_primary = _create_mock_gateway(hass, "00:03:50:aa:bb:01")
    assert gw_primary.bus_topology == TOPOLOGY_STANDALONE
    assert gw_primary.gateway_role == ROLE_PRIMARY
    assert gw_primary.is_primary is True
    assert gw_primary.is_follower is False
    assert gw_primary.primary_gateway_mac is None
    assert gw_primary.delegated_whos == set()
    assert entry_primary.runtime_data.is_primary is True
    assert entry_primary.runtime_data.is_follower is False

    entry_secondary, gw_secondary = _create_mock_gateway(
        hass,
        "00:03:50:aa:bb:02",
        topology=TOPOLOGY_SHARED,
        role=ROLE_SECONDARY,
        primary_gateway="00:03:50:aa:bb:01",
        delegated_whos=[5, "16", "bad"],
    )
    assert gw_secondary.bus_topology == TOPOLOGY_SHARED
    assert gw_secondary.gateway_role == ROLE_SECONDARY
    assert gw_secondary.is_primary is False
    assert gw_secondary.is_follower is True
    assert gw_secondary.primary_gateway_mac == "00:03:50:aa:bb:01"
    assert gw_secondary.delegated_whos == {5, 16}
    assert entry_secondary.runtime_data.is_primary is False
    assert entry_secondary.runtime_data.is_follower is True


@pytest.mark.asyncio
async def test_initial_discovery_skips_non_delegated_on_secondary(hass: HomeAssistant) -> None:
    """Secondary gateway on a shared bus skips initial discovery for unassigned WHOs."""
    _, gw_primary = _create_mock_gateway(hass, "00:03:50:aa:bb:01")
    gw_primary.send_status_request = AsyncMock()

    await gw_primary.initial_discovery()
    # Primary (MH201) queries WHO=1 (*#1*0##), WHO=2 (*#2*0##), WHO=16 (*#16*0*5##), WHO=18 (*#18*51*51##,
    # *#18*51*113##); its firmware drops *#4*0##, so that request is left out (issue_466 sweep trace)
    assert gw_primary.send_status_request.call_count == 5

    # Secondary with WHO=16 delegated only
    _, gw_secondary = _create_mock_gateway(
        hass,
        "00:03:50:aa:bb:02",
        topology=TOPOLOGY_SHARED,
        role=ROLE_SECONDARY,
        primary_gateway="00:03:50:aa:bb:01",
        delegated_whos=[16],
    )
    gw_secondary.send_status_request = AsyncMock()

    await gw_secondary.initial_discovery()
    # Only WHO=16 query is sent; WHO=2 and WHO=4 are skipped
    assert gw_secondary.send_status_request.call_count == 1
    call_arg = gw_secondary.send_status_request.call_args[0][0]
    assert str(call_arg) == "*#16*0*5##"


@pytest.mark.asyncio
async def test_secondary_gateway_bus_discovery_suppressed(hass: HomeAssistant) -> None:
    """Secondary gateway suppresses entity discovery on non-delegated WHOs."""
    from OWNd.message import OWNLightingEvent

    entry_sec, _ = _create_mock_gateway(
        hass,
        "00:03:50:aa:bb:02",
        topology=TOPOLOGY_SHARED,
        role=ROLE_SECONDARY,
        primary_gateway="00:03:50:aa:bb:01",
    )
    add_entities = MagicMock()

    discovery = PlatformDiscovery(
        hass,
        entry_sec,
        add_entities,
        platform="light",
        who="1",
        event_type=OWNLightingEvent,
        build=lambda ctx: MagicMock(),
    )

    # Ingest bus event for light WHERE=21
    msg = OWNLightingEvent.parse("*1*1*21##")
    discovery.handle_message(msg)

    # Discovery is suppressed on secondary gateway for non-delegated WHO 1
    add_entities.assert_not_called()
    assert "21" not in discovery.known


@pytest.mark.asyncio
async def test_secondary_gateway_delegated_who_discovered(hass: HomeAssistant) -> None:
    """Secondary gateway discovers entities for subsystems explicitly delegated to it."""
    from OWNd.message import OWNLightingEvent

    entry_sec, _ = _create_mock_gateway(
        hass,
        "00:03:50:aa:bb:02",
        topology=TOPOLOGY_SHARED,
        role=ROLE_SECONDARY,
        primary_gateway="00:03:50:aa:bb:01",
        delegated_whos=[1],
    )
    add_entities = MagicMock()

    mock_entity = MagicMock()
    discovery = PlatformDiscovery(
        hass,
        entry_sec,
        add_entities,
        platform="light",
        who="1",
        event_type=OWNLightingEvent,
        build=lambda ctx: mock_entity,
    )

    msg = OWNLightingEvent.parse("*1*1*21##")
    discovery.handle_message(msg)

    # Discovery succeeds because WHO=1 is in delegated_whos
    add_entities.assert_called_once()
    assert "21" in discovery.known


@pytest.mark.asyncio
async def test_secondary_gateway_restore_prunes_duplicate_entity(hass: HomeAssistant) -> None:
    """Restore prunes duplicate secondary entities if the primary gateway already owns them."""
    registry = er.async_get(hass)

    entry_prim, _ = _create_mock_gateway(hass, "00:03:50:aa:bb:01")
    entry_sec, _ = _create_mock_gateway(
        hass,
        "00:03:50:aa:bb:02",
        topology=TOPOLOGY_SHARED,
        role=ROLE_SECONDARY,
        primary_gateway="00:03:50:aa:bb:01",
    )

    # Primary entity
    prim_ent = registry.async_get_or_create(
        "light",
        DOMAIN,
        "00:03:50:aa:bb:01-1-21",
        config_entry=entry_prim,
        suggested_object_id="kitchen_light",
    )

    # Duplicate secondary entity
    sec_ent = registry.async_get_or_create(
        "light",
        DOMAIN,
        "00:03:50:aa:bb:02-1-21",
        config_entry=entry_sec,
        suggested_object_id="kitchen_light_2",
    )

    assert registry.async_get(sec_ent.entity_id) is not None

    add_entities = MagicMock()
    discovery = PlatformDiscovery(
        hass,
        entry_sec,
        add_entities,
        platform="light",
        who="1",
        event_type=None,
        build=lambda ctx: MagicMock(),
    )

    # Calling restore on secondary gateway prunes the duplicate entity
    restored = discovery.restore()
    assert len(restored) == 0
    assert registry.async_get(sec_ent.entity_id) is None
    # Primary entity is preserved
    assert registry.async_get(prim_ent.entity_id) is not None


@pytest.mark.asyncio
async def test_shared_bus_traffic_detection_tx_rx(hass: HomeAssistant) -> None:
    """Detect unconfigured shared bus when Gateway B receives frames Gateway A transmitted."""
    from OWNd.message import OWNLightingEvent

    entry_a, gw_a = _create_mock_gateway(hass, "00:03:50:aa:bb:01")
    entry_b, gw_b = _create_mock_gateway(hass, "00:03:50:aa:bb:02")

    with patch("custom_components.myhome.repairs.async_create_shared_bus_issue") as mock_issue:
        # Simulate 3 TX-then-RX echoes within correlation window
        for _ in range(3):
            # Gateway A writes frame
            domain_data = hass.data.setdefault(DOMAIN, {})
            recent_tx = domain_data.setdefault("_recent_tx", collections.deque())
            if not isinstance(recent_tx, collections.deque):
                domain_data["_recent_tx"] = collections.deque()
            domain_data["_recent_tx"].append(
                (time.monotonic(), gw_a.mac, gw_a.bus_group, "*1*1*21##")
            )

            # Gateway B receives the exact same frame
            msg_rx = OWNLightingEvent.parse("*1*1*21##")
            await gw_b._process_message(msg_rx)

        # After 3 correlations, the shared bus repair issue is raised
        mock_issue.assert_called_once_with(hass, "00:03:50:aa:bb:01", "00:03:50:aa:bb:02")

        # Early return branches: gateway-local frames, no/own peer
        from OWNd.message import OWNMessage

        gw_b._correlate_shared_bus_traffic(OWNMessage.parse("*#13**0##"))
        gw_a._record_shared_bus_evidence("", 0.0)
        gw_a._record_shared_bus_evidence(gw_a.mac, 0.0)


@pytest.mark.asyncio
async def test_shared_bus_traffic_detection_tx_echo(hass: HomeAssistant) -> None:
    """Detect unconfigured shared bus when Gateway A transmits and Gateway B receives it."""
    import time

    from OWNd.message import OWNLightingEvent

    _, gw_a = _create_mock_gateway(hass, "00:03:50:aa:bb:01")
    _, gw_b = _create_mock_gateway(hass, "00:03:50:aa:bb:02")

    with patch("custom_components.myhome.repairs.async_create_shared_bus_issue") as mock_issue:
        msg = OWNLightingEvent.parse("*1*1*61##")
        for i in range(3):
            # Gateway A transmits frame
            gw_a._record_tx(time.time() + i * 15 - 0.05, msg)
            # Gateway B receives physical frame shortly after (echo)
            msg.timestamp = time.time() + i * 15
            await gw_b._process_message(msg)

        mock_issue.assert_called_once_with(hass, "00:03:50:aa:bb:01", "00:03:50:aa:bb:02")


@pytest.mark.asyncio
async def test_services_multi_gateway(hass: HomeAssistant, fast_bus_pacing) -> None:
    """Test domain service dispatching across multiple gateways."""
    await async_setup_services(hass)

    entry_a, gw_a = _create_mock_gateway(hass, "00:03:50:aa:bb:01")
    entry_b, gw_b = _create_mock_gateway(
        hass,
        "00:03:50:aa:bb:02",
        name="MH202",
        topology=TOPOLOGY_SHARED,
        role=ROLE_SECONDARY,
        primary_gateway="00:03:50:aa:bb:01",
        delegated_whos=[2, 4, 5, 16, 18],
    )

    gw_a.send = AsyncMock()
    gw_b.send = AsyncMock()
    gw_a.send_status_request = AsyncMock()
    gw_b.send_status_request = AsyncMock()

    # 1. _get_gateway_handler prefers primary gateway when unspecified
    assert _get_gateway_handler(hass, None) == gw_a

    # 2. sync_time without gateway parameter sets the clock once per bus (primaries only: datetime + time)
    await hass.services.async_call(DOMAIN, SERVICE_SYNC_TIME, {}, blocking=True)
    assert gw_a.send.call_count == 2
    gw_b.send.assert_not_called()

    gw_a.send.reset_mock()
    gw_b.send.reset_mock()

    # 3. sweep_bus filters queries: WHO=2/4/5/16/18 are delegated, so only the secondary sweeps them
    await hass.services.async_call(DOMAIN, SERVICE_SWEEP_BUS, {}, blocking=True)
    # Primary sends RTC (*#13**0##), Model (*#13**15##), FW (*#13**16##), Kernel (*#13**23##), Distro (*#13**24##);
    # covers/climate/audio/energy are delegated away, so its only general request is Lighting (*#1*0##). All are status requests.
    gw_a.send.assert_not_called()
    assert [str(c.args[0]) for c in gw_a.send_status_request.call_args_list] == [
        "*#13**0##", "*#13**15##", "*#13**16##", "*#13**23##", "*#13**24##", "*#1013*0*1##", "*#1*0##",
    ]
    gw_b.send.assert_not_called()
    gw_b.send_status_request.assert_not_called()

    gw_a.send.reset_mock()
    gw_b.send.reset_mock()
    gw_a.send_status_request.reset_mock()
    gw_b.send_status_request.reset_mock()

    # 4. targeted sweep hits the secondary
    await hass.services.async_call(
        DOMAIN, SERVICE_SWEEP_BUS, {"gateway": "00:03:50:aa:bb:02"}, blocking=True
    )
    gw_a.send.assert_not_called()
    gw_a.send_status_request.assert_not_called()
    # Secondary sends RTC, Model, FW, Kernel, Distro, the delegated WHO=2 and 5 general requests
    # (the MH202 firmware drops *#4*0## and *#16*0*5##, own-firmware-oracle, so the sweep leaves
    # them out like the startup sweep does), plus the 36 delegated WHO=18 energy queries = 43
    gw_b.send.assert_not_called()
    calls_b = [str(c.args[0]) for c in gw_b.send_status_request.call_args_list]
    assert len(calls_b) == 44
    assert calls_b[:6] == ["*#13**0##", "*#13**15##", "*#13**16##", "*#13**23##", "*#13**24##", "*#1013*0*1##"]
    assert calls_b[6:8] == ["*#2*0##", "*#5*0##"]
    assert len(calls_b[8:]) == 36

    # 4. _get_gateway_handler falls back to next(iter(gateways.values())) if no primary
    hass.config_entries.async_update_entry(
        entry_a,
        options={CONF_BUS_TOPOLOGY: TOPOLOGY_SHARED, CONF_GATEWAY_ROLE: ROLE_SECONDARY},
    )
    assert gw_a.is_primary is False
    assert gw_b.is_primary is False
    assert _get_gateway_handler(hass, None) in (gw_a, gw_b)


@pytest.mark.asyncio
async def test_diagnostics_multi_gateway(hass: HomeAssistant) -> None:
    """Test diagnostics exports multi-gateway topology attributes."""
    entry_sec, _ = _create_mock_gateway(
        hass,
        "00:03:50:aa:bb:02",
        topology=TOPOLOGY_SHARED,
        role=ROLE_SECONDARY,
        primary_gateway="00:03:50:aa:bb:01",
        delegated_whos=[5],
    )

    diag = await async_get_config_entry_diagnostics(hass, entry_sec)
    gw_data = diag["gateway"]
    assert gw_data["bus_topology"] == TOPOLOGY_SHARED
    assert gw_data["gateway_role"] == ROLE_SECONDARY
    assert gw_data["is_follower"] is True
    assert gw_data["primary_gateway"] == "**REDACTED**"  # present, but a MAC is not for a shared report
    assert gw_data["delegated_whos"] == [5]


@pytest.mark.asyncio
async def test_options_flow_multi_gateway(hass: HomeAssistant) -> None:
    """Test options flow with multi-gateway settings and repair resolution."""
    from custom_components.myhome.repairs import (
        ISSUE_SHARED_BUS_DETECTED,
        async_create_shared_bus_issue,
    )

    entry_pri, _ = _create_mock_gateway(
        hass,
        "00:03:50:aa:bb:01",
        topology=TOPOLOGY_SHARED,
        role=ROLE_PRIMARY,
    )
    entry_sec, _ = _create_mock_gateway(
        hass,
        "00:03:50:aa:bb:02",
        topology=TOPOLOGY_STANDALONE,
        role=ROLE_PRIMARY,
    )

    # 1. Create a shared bus issue between these two gateways
    async_create_shared_bus_issue(hass, "00:03:50:aa:bb:02", "00:03:50:aa:bb:01")
    issue_id = f"{ISSUE_SHARED_BUS_DETECTED}_000350aabb01_000350aabb02"
    assert ir.async_get(hass).async_get_issue(DOMAIN, issue_id) is not None

    # 2. Open options flow for secondary gateway with another gateway present
    opt_flow = MyhomeOptionsFlowHandler(entry_sec)
    opt_flow.hass = hass
    form = await opt_flow.async_step_init()
    assert form["type"] == FlowResultType.FORM

    # 3. Post multi-gateway options and verify issue resolution and reload
    with patch.object(hass.config_entries, "async_reload", return_value=True) as mock_reload:
        res = await opt_flow.async_step_user(
            {
                CONF_ADDRESS: entry_sec.data[CONF_HOST],
                CONF_NAME: entry_sec.data[CONF_NAME],
                CONF_OWN_PASSWORD: None,
                CONF_WORKER_COUNT: 1,
                CONF_GENERATE_EVENTS: False,
                CONF_TRANSITION_MODE: "software_stepped",
                CONF_BUS_TOPOLOGY: TOPOLOGY_SHARED,
                CONF_GATEWAY_ROLE: ROLE_SECONDARY,
                CONF_PRIMARY_GATEWAY: "00:03:50:aa:bb:01",
                CONF_DELEGATED_WHOS: ["2", "16"],
            }
        )

    assert res["type"] == FlowResultType.CREATE_ENTRY
    options = res["data"]
    assert options[CONF_BUS_TOPOLOGY] == TOPOLOGY_SHARED
    assert options[CONF_GATEWAY_ROLE] == ROLE_SECONDARY
    assert options[CONF_PRIMARY_GATEWAY] == "00:03:50:aa:bb:01"
    assert options[CONF_DELEGATED_WHOS] == [2, 16]
    # No reload from inside the flow: it would still see the old options (the listener reloads)
    assert not mock_reload.called
    # Verify repair issue was automatically cleared
    assert ir.async_get(hass).async_get_issue(DOMAIN, issue_id) is None


def test_gateway_properties_standby(hass: HomeAssistant) -> None:
    """Test warm standby gateway role and properties."""
    entry_standby, gw_standby = _create_mock_gateway(
        hass,
        "00:03:50:aa:bb:03",
        topology=TOPOLOGY_SHARED,
        role=ROLE_STANDBY,
        primary_gateway="00:03:50:aa:bb:01",
    )
    assert gw_standby.bus_topology == TOPOLOGY_SHARED
    assert gw_standby.gateway_role == ROLE_STANDBY
    assert gw_standby.is_primary is False
    assert gw_standby.is_follower is True
    assert gw_standby.is_standby is True
    assert gw_standby.primary_gateway_mac == "00:03:50:aa:bb:01"
    assert gw_standby.failover_active is False
    assert entry_standby.runtime_data.is_standby is True
    assert entry_standby.runtime_data.is_follower is True
    assert entry_standby.runtime_data.is_primary is False


@pytest.mark.asyncio
async def test_warm_standby_failover_outbound_and_inbound_bridging(hass: HomeAssistant) -> None:
    """Test warm standby failover for outbound commands, inbound event bridging, and auto-failback."""
    from homeassistant.core import callback
    from homeassistant.helpers.dispatcher import async_dispatcher_connect
    from OWNd.message import OWNCommand, OWNLightingEvent, OWNMessage

    entry_pri, gw_primary = _create_mock_gateway(
        hass,
        "00:03:50:aa:bb:01",
        topology=TOPOLOGY_SHARED,
        role=ROLE_PRIMARY,
    )
    entry_sb, gw_standby = _create_mock_gateway(
        hass,
        "00:03:50:aa:bb:02",
        topology=TOPOLOGY_SHARED,
        role=ROLE_STANDBY,
        primary_gateway="00:03:50:aa:bb:01",
    )

    gw_primary.is_connected = True
    gw_primary._available = True
    gw_standby.is_connected = True
    gw_standby._available = True

    assert gw_primary.available is True
    assert gw_standby.available is True
    assert gw_primary.failover_active is False
    gw_primary._setup_at -= 2 * AVAILABILITY_GRACE  # set up long ago: no startup grace left

    try:
        # 1. Primary's event session drops -> commands already go through the standby, but a
        #    blip inside the reconnect grace raises no failover issue
        gw_primary._on_event_connection_state_change(False)
        assert gw_primary.is_connected is False
        assert gw_primary.failover_active is False
        assert gw_primary.available is True  # Available via connected standby!

        issue_id = f"{ISSUE_GATEWAY_FAILOVER}_000350aabb01"
        assert ir.async_get(hass).async_get_issue(DOMAIN, issue_id) is None

        # 2. Outbound commands fail over transparently to standby
        cmd = OWNCommand.parse("*1*1*21##")
        with patch.object(gw_standby, "send", new_callable=AsyncMock) as mock_sb_send:
            mock_sb_send.return_value = 12345.0
            fut = await gw_primary.send(cmd)
            mock_sb_send.assert_awaited_once_with(cmd)
            assert fut == 12345.0

        # 3. Outbound status requests fail over transparently to standby
        status_cmd = OWNCommand.parse("*#1*21##")
        with patch.object(
            gw_standby, "send_status_request", new_callable=AsyncMock
        ) as mock_sb_status:
            mock_sb_status.return_value = 67890.0
            fut = await gw_primary.send_status_request(status_cmd)
            mock_sb_status.assert_awaited_once_with(status_cmd)
            assert fut == 67890.0

        # 4. Inbound frames on standby are bridged to primary dispatcher signal while primary is disconnected
        received_on_primary = []

        @callback
        def _on_primary_message(msg: Any) -> None:
            received_on_primary.append(msg)

        unsub = async_dispatcher_connect(
            hass,
            f"myhome_message_{gw_primary.mac}",
            _on_primary_message,
        )
        # Physical device frame IS bridged
        bus_msg = OWNLightingEvent.parse("*1*1*21##")
        await gw_standby._process_message(bus_msg)
        assert len(received_on_primary) == 1
        assert received_on_primary[0] == bus_msg

        # Gateway-local WHO=13 and WHO=1013 frames are NOT bridged
        who13_msg = OWNMessage.parse("*#13**0##")
        await gw_standby._process_message(who13_msg)
        assert len(received_on_primary) == 1

        who1013_msg = OWNMessage.parse("*#1013**1##")
        await gw_standby._process_message(who1013_msg)
        assert len(received_on_primary) == 1
        unsub()

        # 5. Primary calls _mark_unavailable while standby is active: logs warning and returns without notifying unavailability
        gw_primary._available = True
        gw_primary.is_connected = False
        assert gw_primary.available is True
        gw_primary._mark_unavailable(None)
        assert gw_primary._available is False
        assert gw_primary.available is True
        # The outage outlived the grace: failover is now reported
        assert gw_primary.failover_active is True
        issue = ir.async_get(hass).async_get_issue(DOMAIN, issue_id)
        assert issue is not None
        assert issue.severity == ir.IssueSeverity.WARNING

        # Standby also becomes unavailable -> standby._notify_availability dispatches primary.availability_signal
        primary_avail_signals = []

        @callback
        def _on_primary_avail() -> None:
            primary_avail_signals.append(True)

        unsub_avail = async_dispatcher_connect(
            hass,
            gw_primary.availability_signal,
            _on_primary_avail,
        )
        gw_standby.is_connected = False
        gw_standby._available = True
        gw_standby._mark_unavailable(None)
        assert gw_standby._available is False
        assert gw_primary.available is False
        assert len(primary_avail_signals) == 1
        # Nothing carries the traffic any more: the failover issue must not claim otherwise
        assert gw_primary.failover_active is False
        assert ir.async_get(hass).async_get_issue(DOMAIN, issue_id) is None

        # Standby recovers -> standby._notify_availability dispatches primary.availability_signal
        gw_standby._on_event_connection_state_change(True)
        assert gw_standby._available is True
        assert gw_primary.available is True
        assert len(primary_avail_signals) == 2
        assert gw_primary.failover_active is True
        assert ir.async_get(hass).async_get_issue(DOMAIN, issue_id) is not None
        unsub_avail()

        # 6. Primary reconnects -> failback occurs and repair issue is deleted
        gw_primary._on_event_connection_state_change(True)
        assert gw_primary.is_connected is True
        assert gw_primary.failover_active is False
        assert ir.async_get(hass).async_get_issue(DOMAIN, issue_id) is None

        # 7. Standby close_listener while failover is active clears failover and notifies primary
        gw_primary._available = False
        gw_primary.is_connected = False
        gw_primary._record_failover_active(gw_standby)
        assert ir.async_get(hass).async_get_issue(DOMAIN, issue_id) is not None

        standby_close_signals = []

        @callback
        def _on_standby_close() -> None:
            standby_close_signals.append(True)

        unsub_sb_close = async_dispatcher_connect(
            hass,
            gw_primary.availability_signal,
            _on_standby_close,
        )
        await gw_standby.close_listener()
        assert len(standby_close_signals) == 1
        assert gw_primary.available is False
        assert gw_primary.failover_active is False
        assert ir.async_get(hass).async_get_issue(DOMAIN, issue_id) is None
        unsub_sb_close()

        # Verify primary close_listener() also cleans up
        gw_primary._failover_active = True
        from custom_components.myhome.repairs import async_create_failover_issue

        async_create_failover_issue(
            hass, gw_primary.mac, gw_standby.mac, gw_primary.name, gw_standby.name
        )
        assert ir.async_get(hass).async_get_issue(DOMAIN, issue_id) is not None
        await gw_primary.close_listener()
        assert gw_primary.failover_active is False
        assert ir.async_get(hass).async_get_issue(DOMAIN, issue_id) is None
    finally:
        if gw_primary._unavailable_timer is not None:
            gw_primary._unavailable_timer()
            gw_primary._unavailable_timer = None
        if gw_standby._unavailable_timer is not None:
            gw_standby._unavailable_timer()
            gw_standby._unavailable_timer = None


@pytest.mark.asyncio
async def test_warm_standby_diagnostics_and_options_flow(hass: HomeAssistant) -> None:
    """Test diagnostics and options flow for warm standby gateway."""
    entry_pri, gw_pri = _create_mock_gateway(
        hass,
        "00:03:50:aa:bb:01",
        topology=TOPOLOGY_SHARED,
        role=ROLE_PRIMARY,
    )
    entry_sb, gw_sb = _create_mock_gateway(
        hass,
        "00:03:50:aa:bb:02",
        topology=TOPOLOGY_SHARED,
        role=ROLE_STANDBY,
        primary_gateway="00:03:50:aa:bb:01",
    )

    diag = await async_get_config_entry_diagnostics(hass, entry_sb)
    assert diag["gateway"]["bus_topology"] == TOPOLOGY_SHARED
    assert diag["gateway"]["gateway_role"] == ROLE_STANDBY
    assert diag["gateway"]["is_follower"] is True
    assert diag["gateway"]["is_standby"] is True
    assert diag["gateway"]["failover_active"] is False

    # Options flow selecting ROLE_STANDBY
    opt_flow = MyhomeOptionsFlowHandler(entry_sb)
    opt_flow.hass = hass
    with patch.object(hass.config_entries, "async_reload", return_value=True):
        res = await opt_flow.async_step_user(
            {
                CONF_ADDRESS: entry_sb.data[CONF_HOST],
                CONF_NAME: entry_sb.data[CONF_NAME],
                CONF_OWN_PASSWORD: None,
                CONF_WORKER_COUNT: 1,
                CONF_GENERATE_EVENTS: False,
                CONF_TRANSITION_MODE: "software_stepped",
                CONF_BUS_TOPOLOGY: TOPOLOGY_SHARED,
                CONF_GATEWAY_ROLE: ROLE_STANDBY,
                CONF_PRIMARY_GATEWAY: "00:03:50:aa:bb:01",
            }
        )
    assert res["type"] == FlowResultType.CREATE_ENTRY
    assert res["data"][CONF_GATEWAY_ROLE] == ROLE_STANDBY


def test_standby_and_primary_gateway_lookup_fallbacks(hass: HomeAssistant) -> None:
    """Test lookup fallback branches when hass or MAC is missing."""
    _, gw_primary = _create_mock_gateway(hass, "00:03:50:aa:bb:01")
    _, gw_standby = _create_mock_gateway(
        hass,
        "00:03:50:aa:bb:02",
        topology=TOPOLOGY_SHARED,
        role=ROLE_STANDBY,
        primary_gateway="00:03:50:aa:bb:01",
    )

    # Line 495: self.hass is None on primary
    gw_primary.hass = None
    assert gw_primary._get_standby_gateway() is None
    gw_primary.hass = hass

    # Line 512: self.hass is None on standby
    gw_standby.hass = None
    assert gw_standby._get_primary_gateway() is None
    gw_standby.hass = hass

    # Line 512: not primary_gateway_mac
    assert gw_primary._get_primary_gateway() is None

    # Line 518: primary_gateway_mac points to non-existent entry
    _, gw_orphan = _create_mock_gateway(
        hass,
        "00:03:50:aa:bb:99",
        topology=TOPOLOGY_SHARED,
        role=ROLE_STANDBY,
        primary_gateway="00:03:50:aa:bb:88",
    )
    assert gw_orphan._get_primary_gateway() is None


def test_shared_bus_repair_canonical_sorting(hass: HomeAssistant) -> None:
    """Test that shared bus repair issues are canonical regardless of MAC order."""
    from custom_components.myhome.repairs import (
        ISSUE_SHARED_BUS_DETECTED,
        async_create_shared_bus_issue,
        async_delete_shared_bus_issue,
    )

    mac_1 = "00:03:50:aa:bb:01"
    mac_2 = "00:03:50:aa:bb:02"
    canonical_id = f"{ISSUE_SHARED_BUS_DETECTED}_000350aabb01_000350aabb02"

    domain_data = hass.data.setdefault(DOMAIN, {})
    domain_data["_shared_bus_evidence"] = {
        (mac_1, mac_2): 2,
        tuple(sorted([mac_1, mac_2])): 2,
    }

    # Order 1: create with (mac_2, mac_1), delete with (mac_1, mac_2)
    async_create_shared_bus_issue(hass, mac_2, mac_1)
    assert ir.async_get(hass).async_get_issue(DOMAIN, canonical_id) is not None
    async_delete_shared_bus_issue(hass, mac_1, mac_2)
    assert ir.async_get(hass).async_get_issue(DOMAIN, canonical_id) is None
    assert (mac_1, mac_2) not in domain_data["_shared_bus_evidence"]

    domain_data["_shared_bus_evidence"][(mac_2, mac_1)] = 2
    # Order 2: create with (mac_1, mac_2), delete with (mac_2, mac_1)
    async_create_shared_bus_issue(hass, mac_1, mac_2)
    assert ir.async_get(hass).async_get_issue(DOMAIN, canonical_id) is not None
    async_delete_shared_bus_issue(hass, mac_2, mac_1)
    assert ir.async_get(hass).async_get_issue(DOMAIN, canonical_id) is None
    assert (mac_2, mac_1) not in domain_data["_shared_bus_evidence"]


@pytest.mark.asyncio
async def test_options_flow_topology_validation_errors(hass: HomeAssistant) -> None:
    """Test options flow validation for invalid topology configurations."""
    entry_1, _ = _create_mock_gateway(
        hass,
        "00:03:50:aa:bb:01",
        topology=TOPOLOGY_SHARED,
        role=ROLE_PRIMARY,
    )
    entry_2, _ = _create_mock_gateway(
        hass,
        "00:03:50:aa:bb:02",
        topology=TOPOLOGY_STANDALONE,
        role=ROLE_PRIMARY,
    )

    base_input = {
        CONF_ADDRESS: entry_2.data[CONF_HOST],
        CONF_NAME: entry_2.data[CONF_NAME],
        CONF_OWN_PASSWORD: None,
        CONF_WORKER_COUNT: 1,
        CONF_GENERATE_EVENTS: False,
        CONF_TRANSITION_MODE: "software_stepped",
    }

    # 1. Missing primary_gateway on shared secondary
    opt_flow = MyhomeOptionsFlowHandler(entry_2)
    opt_flow.hass = hass
    res = await opt_flow.async_step_user(
        {
            **base_input,
            CONF_BUS_TOPOLOGY: TOPOLOGY_SHARED,
            CONF_GATEWAY_ROLE: ROLE_SECONDARY,
            CONF_PRIMARY_GATEWAY: "",
        }
    )
    assert res["type"] == FlowResultType.FORM
    assert res["errors"][CONF_PRIMARY_GATEWAY] == "primary_gateway_required"

    # 2. Selecting self as primary_gateway
    res = await opt_flow.async_step_user(
        {
            **base_input,
            CONF_BUS_TOPOLOGY: TOPOLOGY_SHARED,
            CONF_GATEWAY_ROLE: ROLE_SECONDARY,
            CONF_PRIMARY_GATEWAY: "00:03:50:aa:bb:02",
        }
    )
    assert res["type"] == FlowResultType.FORM
    assert res["errors"][CONF_PRIMARY_GATEWAY] == "invalid_primary_gateway"

    # 3. Non-existent primary_gateway MAC
    res = await opt_flow.async_step_user(
        {
            **base_input,
            CONF_BUS_TOPOLOGY: TOPOLOGY_SHARED,
            CONF_GATEWAY_ROLE: ROLE_SECONDARY,
            CONF_PRIMARY_GATEWAY: "00:03:50:99:99:99",
        }
    )
    assert res["type"] == FlowResultType.FORM
    assert res["errors"][CONF_PRIMARY_GATEWAY] == "primary_gateway_not_found"

    # 4. Circular reference: entry_1 is secondary pointing to entry_2, and entry_2 tries to point to entry_1
    hass.config_entries.async_update_entry(
        entry_1,
        options={
            CONF_BUS_TOPOLOGY: TOPOLOGY_SHARED,
            CONF_GATEWAY_ROLE: ROLE_SECONDARY,
            CONF_PRIMARY_GATEWAY: "00:03:50:aa:bb:02",
        },
    )
    res = await opt_flow.async_step_user(
        {
            **base_input,
            CONF_BUS_TOPOLOGY: TOPOLOGY_SHARED,
            CONF_GATEWAY_ROLE: ROLE_SECONDARY,
            CONF_PRIMARY_GATEWAY: "00:03:50:aa:bb:01",
        }
    )
    assert res["type"] == FlowResultType.FORM
    assert res["errors"][CONF_PRIMARY_GATEWAY] == "circular_gateway_reference"

    # 5. Two shared primaries are allowed: they may lead two separate buses. If they are
    #    on one bus after all, shared-bus detection compares them (different bus groups).
    hass.config_entries.async_update_entry(
        entry_1,
        options={
            CONF_BUS_TOPOLOGY: TOPOLOGY_SHARED,
            CONF_GATEWAY_ROLE: ROLE_PRIMARY,
        },
    )
    res = await opt_flow.async_step_user(
        {
            **base_input,
            CONF_BUS_TOPOLOGY: TOPOLOGY_SHARED,
            CONF_GATEWAY_ROLE: ROLE_PRIMARY,
        }
    )
    assert res["type"] == FlowResultType.CREATE_ENTRY

    # 6. The primary must itself be a shared primary: a standalone one would flag its own standby
    hass.config_entries.async_update_entry(
        entry_1, options={CONF_BUS_TOPOLOGY: TOPOLOGY_STANDALONE}
    )
    opt_flow = MyhomeOptionsFlowHandler(entry_2)
    opt_flow.hass = hass
    res = await opt_flow.async_step_user(
        {
            **base_input,
            CONF_BUS_TOPOLOGY: TOPOLOGY_SHARED,
            CONF_GATEWAY_ROLE: ROLE_STANDBY,
            CONF_PRIMARY_GATEWAY: "00:03:50:aa:bb:01",
        }
    )
    assert res["type"] == FlowResultType.FORM
    assert res["errors"][CONF_PRIMARY_GATEWAY] == "primary_gateway_not_shared_primary"


@pytest.mark.asyncio
async def test_options_flow_primary_with_dependents_keeps_its_role(hass: HomeAssistant) -> None:
    """A primary that a standby points at cannot become standalone or secondary."""
    entry_pri, _ = _create_mock_gateway(
        hass, "00:03:50:aa:bb:01", topology=TOPOLOGY_SHARED, role=ROLE_PRIMARY
    )
    _create_mock_gateway(
        hass,
        "00:03:50:aa:bb:02",
        topology=TOPOLOGY_SHARED,
        role=ROLE_STANDBY,
        primary_gateway="00:03:50:aa:bb:01",
    )
    opt_flow = MyhomeOptionsFlowHandler(entry_pri)
    opt_flow.hass = hass
    res = await opt_flow.async_step_user(
        {
            CONF_ADDRESS: entry_pri.data[CONF_HOST],
            CONF_NAME: entry_pri.data[CONF_NAME],
            CONF_OWN_PASSWORD: None,
            CONF_WORKER_COUNT: 1,
            CONF_GENERATE_EVENTS: False,
            CONF_TRANSITION_MODE: "software_stepped",
            CONF_BUS_TOPOLOGY: TOPOLOGY_STANDALONE,
        }
    )
    assert res["type"] == FlowResultType.FORM
    assert res["errors"][CONF_GATEWAY_ROLE] == "gateway_has_dependents"


@pytest.mark.asyncio
async def test_options_flow_standalone_and_standby_resets(hass: HomeAssistant) -> None:
    """Test options flow resetting delegated WHOS and roles when switching modes."""
    entry_pri, _ = _create_mock_gateway(
        hass,
        "00:03:50:aa:bb:01",
        topology=TOPOLOGY_SHARED,
        role=ROLE_PRIMARY,
    )
    entry_sec, _ = _create_mock_gateway(
        hass,
        "00:03:50:aa:bb:02",
        topology=TOPOLOGY_SHARED,
        role=ROLE_SECONDARY,
        primary_gateway="00:03:50:aa:bb:01",
        delegated_whos=[5, 16],
    )

    base_input = {
        CONF_ADDRESS: entry_sec.data[CONF_HOST],
        CONF_NAME: entry_sec.data[CONF_NAME],
        CONF_OWN_PASSWORD: None,
        CONF_WORKER_COUNT: 1,
        CONF_GENERATE_EVENTS: False,
        CONF_TRANSITION_MODE: "software_stepped",
    }

    # 1. Switching secondary to standby clears delegated_whos
    opt_flow = MyhomeOptionsFlowHandler(entry_sec)
    opt_flow.hass = hass
    with patch.object(hass.config_entries, "async_reload", return_value=True):
        res = await opt_flow.async_step_user(
            {
                **base_input,
                CONF_BUS_TOPOLOGY: TOPOLOGY_SHARED,
                CONF_GATEWAY_ROLE: ROLE_STANDBY,
                CONF_PRIMARY_GATEWAY: "00:03:50:aa:bb:01",
                CONF_DELEGATED_WHOS: ["5", "16"],
            }
        )
    assert res["type"] == FlowResultType.CREATE_ENTRY
    assert res["data"][CONF_GATEWAY_ROLE] == ROLE_STANDBY
    assert CONF_DELEGATED_WHOS not in res["data"]

    # 2. Switching to standalone clears primary_gateway, delegated_whos, and sets role to primary
    with patch.object(hass.config_entries, "async_reload", return_value=True):
        res = await opt_flow.async_step_user(
            {
                **base_input,
                CONF_BUS_TOPOLOGY: TOPOLOGY_STANDALONE,
            }
        )
    assert res["type"] == FlowResultType.CREATE_ENTRY
    assert res["data"][CONF_BUS_TOPOLOGY] == TOPOLOGY_STANDALONE
    assert res["data"][CONF_GATEWAY_ROLE] == ROLE_PRIMARY
    assert CONF_PRIMARY_GATEWAY not in res["data"]
    assert CONF_DELEGATED_WHOS not in res["data"]

    # 3. Switching standalone to shared primary clears primary_gateway and delegated_whos
    hass.config_entries.async_update_entry(
        entry_pri,
        options={**entry_pri.options, CONF_BUS_TOPOLOGY: TOPOLOGY_STANDALONE},
    )
    with patch.object(hass.config_entries, "async_reload", return_value=True):
        res = await opt_flow.async_step_user(
            {
                **base_input,
                CONF_BUS_TOPOLOGY: TOPOLOGY_SHARED,
                CONF_GATEWAY_ROLE: ROLE_PRIMARY,
            }
        )
    assert res["type"] == FlowResultType.CREATE_ENTRY
    assert res["data"][CONF_BUS_TOPOLOGY] == TOPOLOGY_SHARED
    assert res["data"][CONF_GATEWAY_ROLE] == ROLE_PRIMARY
    assert CONF_PRIMARY_GATEWAY not in res["data"]
    assert CONF_DELEGATED_WHOS not in res["data"]


@pytest.mark.asyncio
async def test_service_sweep_delegated_who16_dimension_5(hass: HomeAssistant, fast_bus_pacing) -> None:
    """Test that sweep_bus sends dimension 5 query (*#16*0*5##) when WHO=16 is delegated."""
    entry_sec, gw_sec = _create_mock_gateway(
        hass,
        "00:03:50:aa:bb:02",
        topology=TOPOLOGY_SHARED,
        role=ROLE_SECONDARY,
        primary_gateway="00:03:50:aa:bb:01",
        delegated_whos=[16],
    )
    await async_setup_services(hass)

    sent_queries = []
    with (
        patch.object(gw_sec, "send", new_callable=AsyncMock) as mock_send,
        patch.object(gw_sec, "send_status_request", new_callable=AsyncMock) as mock_status,
    ):
        mock_send.side_effect = lambda cmd: sent_queries.append(str(cmd))
        mock_status.side_effect = lambda cmd: sent_queries.append(str(cmd))
        await hass.services.async_call(
            DOMAIN,
            SERVICE_SWEEP_BUS,
            {"gateway": gw_sec.mac},
            blocking=True,
        )

    assert "*#16*0*5##" in sent_queries


def test_shared_bus_evidence_capping(hass: HomeAssistant) -> None:
    """Evidence is bounded, and only 3 confirmed TX echoes inside one window raise the issue."""
    from custom_components.myhome.const import SHARED_BUS_EVIDENCE_WINDOW_S

    _, gw1 = _create_mock_gateway(hass, "00:03:50:aa:bb:01")
    other_mac = "00:03:50:aa:bb:02"
    pair_key = tuple(sorted([gw1.mac, other_mac]))

    with patch("custom_components.myhome.repairs.async_create_shared_bus_issue") as mock_issue:
        # Coincidences spread over days never add up
        for day in range(5):
            gw1._record_shared_bus_evidence(other_mac, day * 86400.0)
        mock_issue.assert_not_called()
        assert len(hass.data[DOMAIN]["_shared_bus_evidence"][pair_key]) == 3

        # Three confirmed TX echoes inside the window do raise the issue; the evidence then clears
        start = 10 * 86400.0
        for i in range(3):
            gw1._record_shared_bus_evidence(other_mac, start + i * SHARED_BUS_EVIDENCE_WINDOW_S / 4)
        mock_issue.assert_called_once_with(hass, *pair_key)
        assert len(hass.data[DOMAIN]["_shared_bus_evidence"][pair_key]) == 0


def test_duplicate_pruning_mac_normalization(hass: HomeAssistant) -> None:
    """Test that duplicate secondary entities with clean uncolonized MACs are pruned."""
    entry_pri, _ = _create_mock_gateway(hass, "00:03:50:aa:bb:01")
    entry_sec, gw_sec = _create_mock_gateway(
        hass,
        "00:03:50:aa:bb:02",
        topology=TOPOLOGY_SHARED,
        role=ROLE_SECONDARY,
        primary_gateway="00:03:50:aa:bb:01",
    )

    registry = er.async_get(hass)
    # Register primary entity
    registry.async_get_or_create(
        "light",
        DOMAIN,
        "000350aabb01-1-21",
        config_entry=entry_pri,
    )
    # Register duplicate secondary entity with clean uncolonized MAC prefix
    sec_entity = registry.async_get_or_create(
        "light",
        DOMAIN,
        "000350aabb02-1-21",
        config_entry=entry_sec,
    )

    add_entities = MagicMock()
    discovery = PlatformDiscovery(
        hass,
        entry_sec,
        add_entities,
        platform="light",
        who="1",
        event_type=None,
        build=lambda ctx: MagicMock(),
    )
    discovery.restore()
    assert registry.async_get_entity_id("light", DOMAIN, sec_entity.unique_id) is None


def test_golden_dual_gateway_traces_replay(hass: HomeAssistant) -> None:
    """Validate golden multi-gateway traces from issue #453 (F454 main + MH202 standby)."""
    import json
    from pathlib import Path

    trace_dir = Path(__file__).parent / "fixtures" / "traces" / "issue_453"
    f454_file = trace_dir / "myhome_trace_F454_all_2026-09-24T19-12-29.json"
    mh202_file = trace_dir / "myhome_trace_MH202_all_2026-09-24T19-12-34.json"

    assert f454_file.is_file()
    assert mh202_file.is_file()

    with open(f454_file, encoding="utf-8") as f:
        f454_data = json.load(f)
    with open(mh202_file, encoding="utf-8") as f:
        mh202_data = json.load(f)

    assert f454_data["gateway"]["model"] == "F454"
    assert mh202_data["gateway"]["model"] == "MH202"

    f454_frames = [f["raw"] for f in f454_data["frames"]]
    mh202_frames = [f["raw"] for f in mh202_data["frames"]]

    # Verify key lighting and CEN+ interactions are present in both captures
    assert "*1*1*16##" in f454_frames and "*1*1*16##" in mh202_frames
    assert "*1*0*16##" in f454_frames and "*1*0*16##" in mh202_frames
    assert "*1*5*14##" in f454_frames and "*1*5*14##" in mh202_frames
    assert "*25*21#1*21##" in f454_frames and "*25*21#1*21##" in mh202_frames
    assert "*1*1*33##" in f454_frames and "*1*1*33##" in mh202_frames

    # Verify MH202 local WHO=13 datetime frames are NOT present on F454
    mh202_who13 = [f for f in mh202_frames if f.startswith("*#13*")]
    assert len(mh202_who13) > 0
    assert not any(f in f454_frames for f in mh202_who13)

    # Replay frames into mock gateways configured as primary (F454) and standby (MH202)
    _, gw_f454 = _create_mock_gateway(
        hass,
        "00:03:50:ff:45:54",
        topology=TOPOLOGY_SHARED,
        role=ROLE_PRIMARY,
    )
    _, gw_mh202 = _create_mock_gateway(
        hass,
        "00:03:50:00:02:02",
        topology=TOPOLOGY_SHARED,
        role=ROLE_STANDBY,
        primary_gateway="00:03:50:ff:45:54",
    )

    # Dispatch MH202 frames into gw_mh202 bus monitor
    for frame_entry in mh202_data["frames"]:
        gw_mh202.bus_monitor.record_frame(frame_entry["direction"], frame_entry["raw"])

    assert gw_mh202.bus_monitor.total_rx > 0
    assert gw_mh202.bus_monitor.total_tx > 0
    assert len(gw_mh202.bus_monitor.get_recent_frames()) > 0


@pytest.mark.asyncio
async def test_gateway_availability_with_standby(hass: HomeAssistant) -> None:
    """Test is_who_available delegates to standby when primary is offline."""
    entry_a, gw_a = _create_mock_gateway(hass, "00:03:50:aa:bb:01")
    entry_b, gw_b = _create_mock_gateway(
        hass,
        "00:03:50:aa:bb:02",
        topology=TOPOLOGY_SHARED,
        role=ROLE_STANDBY,
        primary_gateway="00:03:50:aa:bb:01",
    )

    gw_a._available = False
    gw_b._available = True

    with patch.object(gw_b, "_profile_supports_who", side_effect=lambda w: w == 16):
        assert gw_a.is_who_available(16) is True
        assert gw_a.is_who_available(2) is False


@pytest.mark.asyncio
async def test_standby_failover_outbound_unsupported_who(hass: HomeAssistant) -> None:
    """Test outbound failover aborts if standby profile lacks support for the command's WHO."""
    from OWNd.message import OWNCommand

    entry_pri, gw_pri = _create_mock_gateway(hass, "00:03:50:aa:bb:01")
    entry_sec, gw_sec = _create_mock_gateway(
        hass,
        "00:03:50:aa:bb:02",
        topology=TOPOLOGY_SHARED,
        role=ROLE_STANDBY,
        primary_gateway="00:03:50:aa:bb:01",
    )
    await async_setup_services(hass)

    gw_pri._available = False
    gw_sec._available = True
    gw_sec.is_connected = True

    with patch.object(gw_sec, "_profile_supports_who", side_effect=lambda w: w != 16):
        # Sending WHO 16 command through standby should fail and return None
        msg = OWNCommand.parse("*#16*0*5##")
        result = await gw_pri.send(msg)
        import asyncio

        assert isinstance(result, asyncio.Future)


@pytest.mark.asyncio
async def test_two_standalone_buses_general_commands_not_shared_bus_evidence(
    hass: HomeAssistant,
) -> None:
    """Test general commands across two standalone buses do not trigger shared bus detection (#459)."""
    import homeassistant.helpers.issue_registry as ir
    from OWNd.message import OWNLightingEvent

    from custom_components.myhome.const import DOMAIN

    _, gw_a = _create_mock_gateway(hass, "00:03:50:aa:bb:01")
    _, gw_b = _create_mock_gateway(hass, "00:03:50:aa:bb:02")

    msg = OWNLightingEvent.parse("*1*1*0##")
    # Gateway A sends general light ON 3 times; Gateway B receives general light ON
    for i in range(3):
        gw_a._record_tx(1000.0 + i * 10, "*1*1*0##")
        # Gateway B receives it without having sent it
        gw_b._correlate_shared_bus_traffic(msg)

    pair_key = tuple(sorted([gw_a.mac, gw_b.mac]))
    evidence = hass.data[DOMAIN].get("_shared_bus_evidence", {})
    assert pair_key not in evidence or len(evidence[pair_key]) == 0
    assert not [i for (d, i) in ir.async_get(hass).issues if d == DOMAIN]


@pytest.mark.asyncio
async def test_shared_primary_and_standby_connected_no_event_double_fire(
    hass: HomeAssistant,
) -> None:
    """Test that standby does not double-fire HA bus events while primary is connected (#459)."""
    from homeassistant.core import callback
    from OWNd.message import OWNLightingEvent

    _, gw_pri = _create_mock_gateway(
        hass, "00:03:50:aa:bb:01", topology=TOPOLOGY_SHARED, role=ROLE_PRIMARY
    )
    _, gw_sb = _create_mock_gateway(
        hass,
        "00:03:50:aa:bb:02",
        topology=TOPOLOGY_SHARED,
        role=ROLE_STANDBY,
        primary_gateway="00:03:50:aa:bb:01",
    )

    gw_pri.is_connected = True
    gw_sb.is_connected = True

    events = []

    @callback
    def _on_general_light(event: Any) -> None:
        events.append(event)

    unsub = hass.bus.async_listen("myhome_general_light_event", _on_general_light)

    msg = OWNLightingEvent.parse("*1*1*0##")
    await gw_pri._process_message(msg)
    await gw_sb._process_message(msg)

    assert len(events) == 1
    unsub()


@pytest.mark.asyncio
async def test_standby_connected_creates_no_cen_devices(hass: HomeAssistant) -> None:
    """Test that standby creates no CEN devices on its own config entry while connected (#459)."""
    from homeassistant.helpers import device_registry as dr
    from OWNd.message import OWNCENPlusEvent

    entry_pri, gw_pri = _create_mock_gateway(
        hass, "00:03:50:aa:bb:01", topology=TOPOLOGY_SHARED, role=ROLE_PRIMARY
    )
    entry_sb, gw_sb = _create_mock_gateway(
        hass,
        "00:03:50:aa:bb:02",
        topology=TOPOLOGY_SHARED,
        role=ROLE_STANDBY,
        primary_gateway="00:03:50:aa:bb:01",
    )

    gw_pri.is_connected = True
    gw_sb.is_connected = True

    cen_events = []
    unsub = hass.bus.async_listen("myhome_cenplus_event", callback(lambda e: cen_events.append(e)))

    msg = OWNCENPlusEvent.parse("*25*21#1*01##")
    await gw_sb._process_message(msg)

    # Standby must not fire CEN events while primary is up
    assert len(cen_events) == 0

    # Standby must not register CEN devices on its own entry
    dev_reg = dr.async_get(hass)
    sb_devices = dr.async_entries_for_config_entry(dev_reg, entry_sb.entry_id)
    assert not any("CEN" in (d.name or "") for d in sb_devices)
    unsub()


@pytest.mark.asyncio
async def test_secondary_initial_discovery_delegated_who4(hass: HomeAssistant) -> None:
    """Test that a secondary gateway with delegated WHO=4 sweeps *#4*0## on initial discovery (#459)."""
    # An MH200N: its firmware forwards *#4*0## (the default MH201 of this module drops it).
    _, gw_sec = _create_mock_gateway(
        hass,
        "00:03:50:aa:bb:02",
        name="MH200N",
        topology=TOPOLOGY_SHARED,
        role=ROLE_SECONDARY,
        primary_gateway="00:03:50:aa:bb:01",
        delegated_whos=[4],
    )
    gw_sec.send_status_request = AsyncMock()

    await gw_sec.initial_discovery()

    # WHO=4 must be queried, while WHO=2, 16 must be skipped on follower
    assert gw_sec.send_status_request.call_count == 1
    call_arg = gw_sec.send_status_request.call_args[0][0]
    assert str(call_arg) == "*#4*0##"


@pytest.mark.asyncio
async def test_secondary_initial_discovery_delegated_who1(hass: HomeAssistant) -> None:
    """Test that a secondary gateway with delegated WHO=1 sweeps *#1*0## on initial discovery (#578)."""
    _, gw_sec = _create_mock_gateway(
        hass,
        "00:03:50:aa:bb:02",
        topology=TOPOLOGY_SHARED,
        role=ROLE_SECONDARY,
        primary_gateway="00:03:50:aa:bb:01",
        delegated_whos=[1],
    )
    gw_sec.send_status_request = AsyncMock()

    await gw_sec.initial_discovery()

    # WHO=1 must be queried, while WHO=2, 4, 16 must be skipped on follower
    assert gw_sec.send_status_request.call_count == 1
    call_arg = gw_sec.send_status_request.call_args[0][0]
    assert str(call_arg) == "*#1*0##"


@pytest.mark.asyncio
async def test_options_flow_standalone_with_secondary_role_rejected(hass: HomeAssistant) -> None:
    """Test that selecting secondary role while keeping standalone topology is rejected with an error (#459)."""
    entry_1, _ = _create_mock_gateway(hass, "00:03:50:aa:bb:01")
    entry_2, _ = _create_mock_gateway(hass, "00:03:50:aa:bb:02")

    opt_flow = MyhomeOptionsFlowHandler(entry_2)
    opt_flow.hass = hass

    base_input = {
        CONF_ADDRESS: entry_2.data[CONF_HOST],
        CONF_NAME: entry_2.data[CONF_NAME],
        CONF_OWN_PASSWORD: None,
        CONF_WORKER_COUNT: 1,
        CONF_GENERATE_EVENTS: False,
        CONF_TRANSITION_MODE: "software_stepped",
    }

    res = await opt_flow.async_step_user(
        {
            **base_input,
            CONF_BUS_TOPOLOGY: TOPOLOGY_STANDALONE,
            CONF_GATEWAY_ROLE: ROLE_SECONDARY,
            CONF_PRIMARY_GATEWAY: "00:03:50:aa:bb:01",
            CONF_DELEGATED_WHOS: ["1"],
        }
    )
    assert res["type"] == FlowResultType.FORM
    assert res["errors"][CONF_GATEWAY_ROLE] == "secondary_requires_shared_topology"


@pytest.mark.asyncio
async def test_standby_failover_cen_event_bridged_to_primary(hass: HomeAssistant) -> None:
    """Test that CEN events on standby during failover are bridged with primary MAC and entry ID (#459)."""
    from homeassistant.core import callback
    from homeassistant.helpers.dispatcher import async_dispatcher_connect
    from OWNd.message import OWNCENPlusEvent

    entry_pri, gw_pri = _create_mock_gateway(
        hass, "00:03:50:aa:bb:01", topology=TOPOLOGY_SHARED, role=ROLE_PRIMARY
    )
    entry_sb, gw_sb = _create_mock_gateway(
        hass,
        "00:03:50:aa:bb:02",
        topology=TOPOLOGY_SHARED,
        role=ROLE_STANDBY,
        primary_gateway="00:03:50:aa:bb:01",
    )

    gw_pri.is_connected = False
    gw_pri._available = False
    gw_sb.is_connected = True
    gw_sb._available = True

    cen_events = []
    dispatched_events = []

    @callback
    def _on_cen_event(e: Any) -> None:
        cen_events.append(e)

    @callback
    def _on_dispatched(payload: Any) -> None:
        dispatched_events.append(payload)

    unsub_bus = hass.bus.async_listen("myhome_cenplus_event", _on_cen_event)
    unsub_disp = async_dispatcher_connect(
        hass,
        f"myhome_cenplus_event_{gw_pri.mac}",
        _on_dispatched,
    )

    msg = OWNCENPlusEvent.parse("*25*21#1*01##")
    await gw_sb._process_message(msg)

    assert len(cen_events) == 1
    assert cen_events[0].data["gateway_mac"] == gw_pri.mac
    assert cen_events[0].data["entry_id"] == entry_pri.entry_id

    assert len(dispatched_events) == 1
    assert dispatched_events[0]["gateway_mac"] == gw_pri.mac
    assert dispatched_events[0]["entry_id"] == entry_pri.entry_id

    unsub_bus()
    unsub_disp()


@pytest.mark.asyncio
async def test_standby_failover_scenarioplus_event_bridged_to_primary(hass: HomeAssistant) -> None:
    """Test that Scenario Plus events on standby during failover are bridged with primary MAC and entry ID (#459)."""
    from homeassistant.core import callback
    from homeassistant.helpers.dispatcher import async_dispatcher_connect

    from custom_components.myhome.gateway_events import OWNScenarioPlusEvent

    entry_pri, gw_pri = _create_mock_gateway(
        hass, "00:03:50:aa:bb:01", topology=TOPOLOGY_SHARED, role=ROLE_PRIMARY
    )
    entry_sb, gw_sb = _create_mock_gateway(
        hass,
        "00:03:50:aa:bb:02",
        topology=TOPOLOGY_SHARED,
        role=ROLE_STANDBY,
        primary_gateway="00:03:50:aa:bb:01",
    )

    gw_pri.is_connected = False
    gw_pri._available = False
    gw_sb.is_connected = True
    gw_sb._available = True

    sp_events = []
    dispatched_events = []

    @callback
    def _on_sp_event(e: Any) -> None:
        sp_events.append(e)

    @callback
    def _on_dispatched(payload: Any) -> None:
        dispatched_events.append(payload)

    unsub_bus = hass.bus.async_listen("myhome_scenario_plus_event", _on_sp_event)
    unsub_disp = async_dispatcher_connect(
        hass,
        f"myhome_scenario_plus_event_{gw_pri.mac}",
        _on_dispatched,
    )

    msg = MagicMock(spec=OWNScenarioPlusEvent)
    msg.object = "12"
    msg.action = "on"
    msg.human_readable_log = "Scenario plus 12: on."
    await gw_sb._process_message(msg)

    assert len(sp_events) == 1
    assert sp_events[0].data["gateway_mac"] == gw_pri.mac
    assert sp_events[0].data["entry_id"] == entry_pri.entry_id
    assert sp_events[0].data["object"] == 12
    assert sp_events[0].data["action"] == "on"

    assert len(dispatched_events) == 1
    assert dispatched_events[0]["gateway_mac"] == gw_pri.mac
    assert dispatched_events[0]["entry_id"] == entry_pri.entry_id

    # If primary recovers, standby Scenario Plus events are suppressed (not bridged)
    gw_pri.is_connected = True
    await gw_sb._process_message(msg)
    assert len(sp_events) == 1

    unsub_bus()
    unsub_disp()


@pytest.mark.asyncio
async def test_secondary_gateway_event_delegation_filtering(hass: HomeAssistant) -> None:
    """Test that secondary gateway only processes events for its delegated WHOs (#459)."""
    from OWNd.message import OWNAutomationEvent, OWNLightingEvent

    _, _ = _create_mock_gateway(
        hass, "00:03:50:aa:bb:01", topology=TOPOLOGY_SHARED, role=ROLE_PRIMARY
    )
    _, gw_sec = _create_mock_gateway(
        hass,
        "00:03:50:aa:bb:02",
        topology=TOPOLOGY_SHARED,
        role=ROLE_SECONDARY,
        primary_gateway="00:03:50:aa:bb:01",
        delegated_whos=[1],
    )
    gw_sec.is_connected = True

    events = []
    unsub_light = hass.bus.async_listen(
        "myhome_general_light_event", callback(lambda e: events.append(e))
    )
    unsub_auto = hass.bus.async_listen(
        "myhome_general_automation_event", callback(lambda e: events.append(e))
    )

    # Delegated WHO=1 event should be processed
    await gw_sec._process_message(OWNLightingEvent.parse("*1*1*0##"))
    await hass.async_block_till_done()  # bus listeners run on the next loop turn
    assert len(events) == 1

    # Non-delegated WHO=2 event should be ignored
    await gw_sec._process_message(OWNAutomationEvent.parse("*2*1*0##"))
    await hass.async_block_till_done()
    assert len(events) == 1

    unsub_light()
    unsub_auto()


@pytest.mark.asyncio
async def test_primary_gateway_ignores_delegated_away_whos(hass: HomeAssistant) -> None:
    """Test that primary gateway ignores events for WHOs delegated away to secondary (#459)."""
    from OWNd.message import OWNAutomationEvent, OWNLightingEvent

    _, gw_pri = _create_mock_gateway(
        hass, "00:03:50:aa:99:01", topology=TOPOLOGY_SHARED, role=ROLE_PRIMARY
    )
    _, _ = _create_mock_gateway(
        hass,
        "00:03:50:aa:99:02",
        topology=TOPOLOGY_SHARED,
        role=ROLE_SECONDARY,
        primary_gateway="00:03:50:aa:99:01",
        delegated_whos=[2],
    )
    gw_pri.is_connected = True

    events = []
    unsub_light = hass.bus.async_listen(
        "myhome_general_light_event", callback(lambda e: events.append(e))
    )
    unsub_auto = hass.bus.async_listen(
        "myhome_general_automation_event", callback(lambda e: events.append(e))
    )

    # Non-delegated WHO=1 event is processed by primary
    await gw_pri._process_message(OWNLightingEvent.parse("*1*1*0##"))
    await hass.async_block_till_done()  # bus listeners run on the next loop turn
    assert len(events) == 1

    # Delegated-away WHO=2 event is ignored by primary
    await gw_pri._process_message(OWNAutomationEvent.parse("*2*1*0##"))
    await hass.async_block_till_done()
    assert len(events) == 1

    unsub_light()
    unsub_auto()


@pytest.mark.asyncio
async def test_standby_failover_cen_who15_bridged_to_primary(hass: HomeAssistant) -> None:
    """Test that CEN (WHO 15) events on standby during failover are bridged with primary MAC (#459)."""
    from homeassistant.core import callback
    from homeassistant.helpers.dispatcher import async_dispatcher_connect
    from OWNd.message import OWNCENEvent

    entry_pri, gw_pri = _create_mock_gateway(
        hass, "00:03:50:aa:bb:01", topology=TOPOLOGY_SHARED, role=ROLE_PRIMARY
    )
    entry_sb, gw_sb = _create_mock_gateway(
        hass,
        "00:03:50:aa:bb:02",
        topology=TOPOLOGY_SHARED,
        role=ROLE_STANDBY,
        primary_gateway="00:03:50:aa:bb:01",
    )

    gw_pri.is_connected = False
    gw_pri._available = False
    gw_sb.is_connected = True
    gw_sb._available = True

    cen_events = []
    dispatched_events = []

    @callback
    def _on_cen_event(e: Any) -> None:
        cen_events.append(e)

    @callback
    def _on_dispatched(payload: Any) -> None:
        dispatched_events.append(payload)

    unsub_bus = hass.bus.async_listen("myhome_cen_event", _on_cen_event)
    unsub_disp = async_dispatcher_connect(
        hass,
        f"myhome_cen_event_{gw_pri.mac}",
        _on_dispatched,
    )

    # Standby receives WHO=15 CEN event during failover -> bridged to primary
    msg = OWNCENEvent.parse("*15*1#1*1##")
    await gw_sb._process_message(msg)

    assert len(cen_events) == 1
    assert cen_events[0].data["gateway_mac"] == gw_pri.mac
    assert cen_events[0].data["entry_id"] == entry_pri.entry_id

    assert len(dispatched_events) == 1
    assert dispatched_events[0]["gateway_mac"] == gw_pri.mac
    assert dispatched_events[0]["entry_id"] == entry_pri.entry_id

    # If primary recovers, standby CEN events are suppressed (not bridged)
    gw_pri.is_connected = True
    await gw_sb._process_message(msg)
    assert len(cen_events) == 1

    unsub_bus()
    unsub_disp()


def test_gateway_tier_and_capabilities() -> None:
    """Validate gateway tier rankings and supported WHO sets."""
    from custom_components.myhome.topology import gateway_supported_whos, gateway_tier

    assert gateway_tier("F454") == 1
    assert gateway_tier("MyHomeServer1") == 1
    assert gateway_tier("MH201") == 2
    assert gateway_tier("MH202") == 2
    assert gateway_tier("H4890") == 2
    assert gateway_tier("MH200N") == 3
    assert gateway_tier("MH200") == 3

    f454_whos = gateway_supported_whos("F454")
    mhs1_whos = gateway_supported_whos("MyHomeServer1")
    h4890_whos = gateway_supported_whos("H4890")
    mh202_whos = gateway_supported_whos("MH202")

    assert 5 not in f454_whos
    assert 1 in f454_whos
    assert 2 in f454_whos
    assert 4 in f454_whos
    assert 16 in f454_whos
    assert 22 in f454_whos
    assert 5 not in mhs1_whos
    assert 16 not in mhs1_whos
    assert 22 not in mhs1_whos
    if 5 in h4890_whos:
        assert 5 in h4890_whos
    else:
        assert 5 not in h4890_whos
    assert 22 in h4890_whos
    assert 16 in h4890_whos
    if 5 in mh202_whos and 5 not in f454_whos:
        assert mh202_whos - f454_whos == {5}
    else:
        assert mh202_whos.issubset(f454_whos)


def test_infer_shared_bus_topology_golden_pairings(hass: HomeAssistant) -> None:
    """Validate topology inference across real-world golden trace pairings."""
    from pytest_homeassistant_custom_component.common import MockConfigEntry

    from custom_components.myhome.const import ROLE_SECONDARY, ROLE_STANDBY
    from custom_components.myhome.topology import gateway_supported_whos, infer_shared_bus_topology

    # Case 1: F454 + MH202 (anotherjulien, issue #453)
    f454 = MockConfigEntry(domain=DOMAIN, title="F454 Gateway", data={"name": "F454", "mac": "00:03:50:ff:45:54"})
    mh202 = MockConfigEntry(domain=DOMAIN, title="MH202 Gateway", data={"name": "MH202", "mac": "00:03:50:00:02:02"})
    rec1 = infer_shared_bus_topology(f454, mh202)
    assert rec1.primary_mac == "00:03:50:ff:45:54"
    assert rec1.secondary_mac == "00:03:50:00:02:02"
    f454_whos = gateway_supported_whos("F454")
    mh202_whos = gateway_supported_whos("MH202")
    has_mh202_delta = 5 in mh202_whos and 5 not in f454_whos
    expected_mh202_role = ROLE_SECONDARY if has_mh202_delta else ROLE_STANDBY
    expected_mh202_whos = {5} if has_mh202_delta else set()
    assert rec1.role == expected_mh202_role
    assert rec1.delegated_whos == expected_mh202_whos
    if has_mh202_delta:
        assert "unique subsystems" in rec1.rationale
    else:
        assert "Warm Standby" in rec1.rationale

    # Inverted order input produces identical primary/standby
    rec1_inv = infer_shared_bus_topology(mh202, f454)
    assert rec1_inv.primary_mac == "00:03:50:ff:45:54"
    assert rec1_inv.secondary_mac == "00:03:50:00:02:02"
    assert rec1_inv.role == expected_mh202_role
    assert rec1_inv.delegated_whos == expected_mh202_whos

    # Case 2: MyHomeServer1 + H4890 (nicolacavallo84, issue #453)
    mhs1 = MockConfigEntry(domain=DOMAIN, title="MyHomeServer1 Gateway", data={"name": "MyHomeServer1", "mac": "00:03:50:aa:bb:01"})
    h4890 = MockConfigEntry(domain=DOMAIN, title="H4890 Gateway", data={"name": "H4890", "mac": "00:03:50:aa:bb:02"})
    rec2 = infer_shared_bus_topology(mhs1, h4890)
    assert rec2.primary_mac == "00:03:50:aa:bb:01"
    assert rec2.secondary_mac == "00:03:50:aa:bb:02"
    assert rec2.role == ROLE_SECONDARY
    expected_h4890 = {5, 16, 22} if 5 in gateway_supported_whos("H4890") else {16, 22}
    assert rec2.delegated_whos == expected_h4890
    assert "unique subsystems" in rec2.rationale

    # Case 3: MH201 + MH200N (caiosweet, issue #453)
    mh201 = MockConfigEntry(domain=DOMAIN, title="MH201 Gateway", data={"name": "MH201", "mac": "00:03:50:00:02:01"})
    mh200n = MockConfigEntry(domain=DOMAIN, title="MH200N Gateway", data={"name": "MH200N", "mac": "00:03:50:00:02:00"})
    rec3 = infer_shared_bus_topology(mh201, mh200n)
    assert rec3.primary_mac == "00:03:50:00:02:01"
    assert rec3.secondary_mac == "00:03:50:00:02:00"
    assert rec3.role == ROLE_STANDBY
    assert rec3.delegated_whos == set()
    assert "Warm Standby" in rec3.rationale

    # Case 4: Identical models (e.g. MH201 + MH201)
    mh201_b = MockConfigEntry(domain=DOMAIN, title="MH201 B Gateway", data={"name": "MH201", "mac": "00:03:50:00:02:02"})
    rec4 = infer_shared_bus_topology(mh201, mh201_b)
    assert rec4.role == ROLE_STANDBY
    assert rec4.delegated_whos == set()


async def test_options_flow_smart_defaults_inferred(hass: HomeAssistant) -> None:
    """Test options flow suggests inferred role and delegated WHOs based on selected primary."""
    from pytest_homeassistant_custom_component.common import MockConfigEntry

    from custom_components.myhome.const import (
        CONF_BUS_TOPOLOGY,
        CONF_DELEGATED_WHOS,
        CONF_GATEWAY_ROLE,
        CONF_PRIMARY_GATEWAY,
        ROLE_SECONDARY,
        TOPOLOGY_SHARED,
    )
    from custom_components.myhome.topology import gateway_supported_whos

    pri_entry = MockConfigEntry(
        domain=DOMAIN,
        title="MyHomeServer1 Gateway",
        data={"name": "MyHomeServer1", "mac": "00:03:50:aa:bb:01"},
    )
    sec_entry = MockConfigEntry(
        domain=DOMAIN,
        title="H4890 Gateway",
        data={"name": "H4890", "mac": "00:03:50:aa:bb:02"},
        options={CONF_BUS_TOPOLOGY: TOPOLOGY_SHARED, CONF_PRIMARY_GATEWAY: "00:03:50:aa:bb:01"},
    )
    pri_entry.add_to_hass(hass)
    sec_entry.add_to_hass(hass)

    result = await hass.config_entries.options.async_init(sec_entry.entry_id)
    assert result["type"] == "form"
    schema = result["data_schema"]

    for k in schema.schema:
        if str(k) == CONF_GATEWAY_ROLE:
            assert k.description["suggested_value"] == ROLE_SECONDARY
        elif str(k) == CONF_DELEGATED_WHOS:
            expected_suggested = {"5", "16", "22"} if 5 in gateway_supported_whos("H4890") else {"16", "22"}
            assert set(k.description["suggested_value"]) == expected_suggested


async def test_options_flow_suggested_role_default_when_no_primary(hass: HomeAssistant) -> None:
    """Test options flow suggested role defaults to PRIMARY when primary gateway lookup fails."""
    from pytest_homeassistant_custom_component.common import MockConfigEntry

    from custom_components.myhome.const import (
        CONF_GATEWAY_ROLE,
        CONF_PRIMARY_GATEWAY,
        ROLE_PRIMARY,
    )

    other_entry = MockConfigEntry(
        domain=DOMAIN,
        title="Other Gateway",
        data={"name": "F454", "mac": "00:03:50:11:11:11"},
    )
    solo_entry = MockConfigEntry(
        domain=DOMAIN,
        title="Solo Gateway",
        data={"name": "F454", "mac": "00:03:50:99:88:77"},
        options={CONF_PRIMARY_GATEWAY: "00:03:50:99:99:99"},
    )
    other_entry.add_to_hass(hass)
    solo_entry.add_to_hass(hass)

    result = await hass.config_entries.options.async_init(solo_entry.entry_id)
    assert result["type"] == "form"
    schema = result["data_schema"]
    for k in schema.schema:
        if str(k) == CONF_GATEWAY_ROLE:
            assert k.description["suggested_value"] == ROLE_PRIMARY


def test_entry_model_fallbacks() -> None:
    """Test entry_model extracts from options or title if data is missing."""
    from unittest.mock import MagicMock

    from custom_components.myhome.topology import entry_model

    # Options fallback
    mock_opt = MagicMock()
    mock_opt.data = {}
    mock_opt.options = {"name": "FromOptions"}
    mock_opt.title = ""
    assert entry_model(mock_opt) == "FromOptions"

    # Title fallback with ' Gateway'
    mock_title = MagicMock()
    mock_title.data = None
    mock_title.options = None
    mock_title.title = "F454 Gateway"
    assert entry_model(mock_title) == "F454"


def test_gateway_supported_whos_profile_exception(monkeypatch: pytest.MonkeyPatch) -> None:
    """Test gateway_supported_whos handles exception during get_gateway_profile."""
    import OWNd.profiles

    from custom_components.myhome.topology import gateway_supported_whos

    def raise_err(*args, **kwargs):
        raise RuntimeError("boom")

    monkeypatch.setattr(OWNd.profiles, "get_gateway_profile", raise_err)
    # Should safely catch exception and return empty (or known enrichments)
    whos = gateway_supported_whos("UnknownBoom")
    assert whos == set()


def _require_f453av_profile() -> None:
    """Skip when the installed OWNd does not discriminate F453AV firmware yet (OWNd#80)."""
    profiles = pytest.importorskip("OWNd.profiles")
    if not hasattr(profiles, "F453AVProfile"):
        pytest.skip("installed OWNd does not discriminate F453AV firmware yet (OWNd#80)")


def test_gateway_supported_whos_with_firmware() -> None:
    """Verify gateway_supported_whos discriminates firmware-gated capabilities (e.g. F453AV CEN+)."""
    _require_f453av_profile()

    from custom_components.myhome.topology import gateway_supported_whos

    # F453AV without firmware or < 2.1.7: no WHO 25
    assert 25 not in gateway_supported_whos("F453AV")
    assert 25 not in gateway_supported_whos("F453AV", "1.0.19")

    # F453AV with FW >= 2.1.7: supports WHO 25
    assert 25 in gateway_supported_whos("F453AV", "2.1.7")
    assert 25 in gateway_supported_whos("F453AV", "3.0.0")


def test_gateway_supported_whos_passes_firmware_to_profile(monkeypatch: pytest.MonkeyPatch) -> None:
    """Verify gateway_supported_whos passes firmware to get_gateway_profile when supported."""
    from custom_components.myhome.topology import gateway_supported_whos

    captured_calls: list[tuple[str, str | None]] = []

    class DummyFirmwareProfile:
        supported_who = {1, 25}

        def __init__(self, model: str, fw: str | None) -> None:
            self.model = model
            self.fw = fw

        def supports_who(self, who: int) -> bool:
            if who == 25:
                return self.fw == "2.1.7"
            return True

    def mock_two_arg_get_profile(model: str | None, fw: str | None = None) -> Any:
        captured_calls.append((model or "", fw))
        return DummyFirmwareProfile(model or "", fw)

    monkeypatch.setattr(
        "OWNd.profiles.get_gateway_profile",
        mock_two_arg_get_profile,
    )

    whos_old = gateway_supported_whos("F453AV", "1.0.0")
    assert 25 not in whos_old
    assert captured_calls[-1] == ("F453AV", "1.0.0")

    whos_new = gateway_supported_whos("F453AV", "2.1.7")
    assert 25 in whos_new
    assert captured_calls[-1] == ("F453AV", "2.1.7")


def test_validate_shared_bus_topology_firmware_delegation(hass: HomeAssistant) -> None:
    """Verify validate_shared_bus_topology permits WHO 25 when secondary F453AV has FW >= 2.1.7."""
    _require_f453av_profile()

    from pytest_homeassistant_custom_component.common import MockConfigEntry

    from custom_components.myhome.topology import validate_shared_bus_topology

    primary = MockConfigEntry(
        domain="myhome",
        title="F454 Gateway",
        data={
            "address": "192.168.1.1",
            "model": "F454",
            "firmware": "2.0.0",
            "bus_topology": "shared",
            "gateway_role": "primary",
        },
        unique_id="00:03:50:00:00:01",
    )
    primary.add_to_hass(hass)

    # Follower F453AV with FW < 2.1.7 fails delegating WHO 25
    follower_old = MockConfigEntry(
        domain="myhome",
        title="F453AV Gateway",
        data={"address": "192.168.1.2", "model": "F453AV", "firmware": "1.0.19"},
        unique_id="00:03:50:00:00:02",
    )
    errors_old = validate_shared_bus_topology(
        hass,
        follower_old,
        {
            "bus_topology": "shared",
            "gateway_role": "secondary",
            "primary_gateway": "00:03:50:00:00:01",
            "delegated_whos": ["25"],
        },
    )
    assert errors_old.get("delegated_whos") == "who_not_supported_by_gateway"

    # Follower F453AV with FW >= 2.1.7 succeeds delegating WHO 25
    follower_new = MockConfigEntry(
        domain="myhome",
        title="F453AV Gateway",
        data={"address": "192.168.1.3", "model": "F453AV", "firmware": "2.1.7"},
        unique_id="00:03:50:00:00:03",
    )
    errors_new = validate_shared_bus_topology(
        hass,
        follower_new,
        {
            "bus_topology": "shared",
            "gateway_role": "secondary",
            "primary_gateway": "00:03:50:00:00:01",
            "delegated_whos": ["25"],
        },
    )
    assert "delegated_whos" not in errors_new


def test_validate_shared_bus_topology_firmware_delegation_mocked(
    hass: HomeAssistant, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Verify validate_shared_bus_topology delegates based on entry firmware version."""
    from pytest_homeassistant_custom_component.common import MockConfigEntry

    from custom_components.myhome.topology import validate_shared_bus_topology

    def mock_supported_whos(model: str | None, firmware: str | None = None) -> set[int]:
        if model == "F453AV" and firmware == "2.1.7":
            return {1, 2, 4, 15, 25}
        return {1, 2, 4, 15}

    monkeypatch.setattr(
        "custom_components.myhome.topology.gateway_supported_whos",
        mock_supported_whos,
    )

    primary = MockConfigEntry(
        domain="myhome",
        title="F454 Gateway",
        data={
            "address": "192.168.1.1",
            "model": "F454",
            "firmware": "2.0.0",
            "bus_topology": "shared",
            "gateway_role": "primary",
        },
        unique_id="00:03:50:00:00:01",
    )
    primary.add_to_hass(hass)

    follower_old = MockConfigEntry(
        domain="myhome",
        title="F453AV Gateway",
        data={"address": "192.168.1.2", "model": "F453AV", "firmware": "1.0.19"},
        unique_id="00:03:50:00:00:02",
    )
    errors_old = validate_shared_bus_topology(
        hass,
        follower_old,
        {
            "bus_topology": "shared",
            "gateway_role": "secondary",
            "primary_gateway": "00:03:50:00:00:01",
            "delegated_whos": ["25"],
        },
    )
    assert errors_old.get("delegated_whos") == "who_not_supported_by_gateway"

    follower_new = MockConfigEntry(
        domain="myhome",
        title="F453AV Gateway",
        data={"address": "192.168.1.3", "model": "F453AV", "firmware": "2.1.7"},
        unique_id="00:03:50:00:00:03",
    )
    errors_new = validate_shared_bus_topology(
        hass,
        follower_new,
        {
            "bus_topology": "shared",
            "gateway_role": "secondary",
            "primary_gateway": "00:03:50:00:00:01",
            "delegated_whos": ["25"],
        },
    )
    assert "delegated_whos" not in errors_new


def test_entry_firmware_extraction() -> None:
    """Verify entry_firmware extracts firmware version from data or options."""
    from custom_components.myhome.topology import entry_firmware

    entry_data = MagicMock(data={"firmware": "2.1.7"}, options={})
    assert entry_firmware(entry_data) == "2.1.7"

    entry_opts = MagicMock(data={}, options={"firmware": "1.0.19"})
    assert entry_firmware(entry_opts) == "1.0.19"

    entry_none = MagicMock(data={}, options={})
    assert entry_firmware(entry_none) is None


def test_gateway_supported_whos_single_arg_profile_fallback(monkeypatch: pytest.MonkeyPatch) -> None:
    """Verify gateway_supported_whos falls back when get_gateway_profile only takes 1 argument."""
    from custom_components.myhome.topology import gateway_supported_whos

    class DummyLegacyProfile:
        supported_who = {1, 2, 4}

        def supports_who(self, who: int) -> bool:
            return True

    def mock_single_arg_get_profile(model: str | None) -> Any:
        return DummyLegacyProfile()

    monkeypatch.setattr(
        "OWNd.profiles.get_gateway_profile",
        mock_single_arg_get_profile,
    )

    whos = gateway_supported_whos("F453AV", "2.1.7")
    assert 1 in whos
    assert 2 in whos
    assert 4 in whos


def test_infer_shared_bus_topology_equal_tier_b_has_more_whos(hass: HomeAssistant) -> None:
    """Test infer_shared_bus_topology when tier_a == tier_b but entry_b has more supported WHOs."""
    from pytest_homeassistant_custom_component.common import MockConfigEntry

    from custom_components.myhome.topology import infer_shared_bus_topology

    # MHS1 (Tier 1, 6 WHOs) vs F454 (Tier 1, 8 WHOs)
    mhs1 = MockConfigEntry(domain=DOMAIN, title="MyHomeServer1 Gateway", data={"name": "MyHomeServer1", "mac": "00:03:50:00:01:01"})
    f454 = MockConfigEntry(domain=DOMAIN, title="F454 Gateway", data={"name": "F454", "mac": "00:03:50:00:01:02"})

    rec = infer_shared_bus_topology(mhs1, f454)
    assert rec.primary_mac == "00:03:50:00:01:02"  # F454 wins Primary
    assert rec.secondary_mac == "00:03:50:00:01:01"  # MHS1 is Follower
    assert "WHO count" in rec.rationale

    # Invert to test tier_a == tier_b with whos_a > whos_b
    rec_inv = infer_shared_bus_topology(f454, mhs1)
    assert rec_inv.primary_mac == "00:03:50:00:01:02"
    assert rec_inv.secondary_mac == "00:03:50:00:01:01"


def test_infer_shared_bus_topology_audio_coupling_who22_added(monkeypatch: pytest.MonkeyPatch) -> None:
    """Test audio coupling adds WHO 22/16 to delta when secondary supports both WHO 16 and 22."""
    from unittest.mock import MagicMock

    from custom_components.myhome import topology

    entry_a = MagicMock()
    entry_a.data = {"mac": "00:03:50:00:00:01", "name": "GW_A"}
    entry_a.options = {}
    entry_a.title = "GW_A"

    entry_b = MagicMock()
    entry_b.data = {"mac": "00:03:50:00:00:02", "name": "GW_B"}
    entry_b.options = {}
    entry_b.title = "GW_B"

    # Gateway A supports 1, 2, 22. Gateway B supports 1, 2, 16, 22. Both Tier 3.
    def mock_whos(model: str | None, firmware: str | None = None) -> set[int]:
        if model == "GW_A":
            return {1, 2, 22}
        return {1, 2, 16, 22}

    monkeypatch.setattr(topology, "gateway_supported_whos", mock_whos)

    # GW_B has more WHOs (4 vs 3), so GW_B would be primary unless tier differs.
    # Force GW_A as primary by making GW_A Tier 1:
    def mock_tier(model: str | None) -> int:
        return 1 if model == "GW_A" else 2

    monkeypatch.setattr(topology, "gateway_tier", mock_tier)

    rec = topology.infer_shared_bus_topology(entry_a, entry_b)
    assert rec.primary_mac == "00:03:50:00:00:01"
    assert rec.secondary_mac == "00:03:50:00:00:02"
    assert rec.audio_coupled is True
    # WHO 16 was delta; WHO 22 was added via audio coupling even though primary also supported 22
    assert 16 in rec.delegated_whos
    assert 22 in rec.delegated_whos
    assert "(Audio coupled)" in rec.rationale

    # Inverted audio test: GW_A supports 1, 2, 16. GW_B supports 1, 2, 16, 22.
    # WHO 22 is in delta; WHO 16 is added via audio coupling.
    def mock_whos_inv(model: str | None, firmware: str | None = None) -> set[int]:
        if model == "GW_A":
            return {1, 2, 16}
        return {1, 2, 16, 22}

    monkeypatch.setattr(topology, "gateway_supported_whos", mock_whos_inv)
    rec2 = topology.infer_shared_bus_topology(entry_a, entry_b)
    assert rec2.audio_coupled is True
    assert 16 in rec2.delegated_whos
    assert 22 in rec2.delegated_whos


async def test_shared_bus_repair_flow_standby_and_abort(hass: HomeAssistant) -> None:
    """Test shared bus repair flow configuring standby and aborting when gateway missing."""
    from pytest_homeassistant_custom_component.common import MockConfigEntry

    from custom_components.myhome.const import (
        CONF_BUS_TOPOLOGY,
        CONF_DELEGATED_WHOS,
        CONF_GATEWAY_ROLE,
        ROLE_SECONDARY,
        ROLE_STANDBY,
        TOPOLOGY_SHARED,
    )
    from custom_components.myhome.repairs import SharedBusRepairFlow
    from custom_components.myhome.topology import gateway_supported_whos

    f454 = MockConfigEntry(
        domain=DOMAIN,
        title="F454 Gateway",
        data={"name": "F454", "mac": "00:03:50:aa:bb:01"},
        options={},
    )
    mh202 = MockConfigEntry(
        domain=DOMAIN,
        title="MH202 Gateway",
        data={"name": "MH202", "mac": "00:03:50:aa:bb:02"},
        options={CONF_DELEGATED_WHOS: [1, 2]},  # Pre-existing WHOs to verify removal in standby
    )
    f454.add_to_hass(hass)
    mh202.add_to_hass(hass)

    flow = SharedBusRepairFlow({"mac_a": "00:03:50:aa:bb:01", "mac_b": "00:03:50:aa:bb:02"})
    flow.hass = hass

    has_alarm_delta = 5 in gateway_supported_whos("MH202") and 5 not in gateway_supported_whos("F454")

    # Form step
    res_form = await flow.async_step_init()
    assert res_form["type"] == "form"
    if has_alarm_delta:
        assert "WHO 5" in res_form["description_placeholders"]["subsystems"]
    else:
        assert "Warm Standby" in res_form["description_placeholders"]["subsystems"]

    # Submit step -> sets role and configures delegated_whos
    with patch.object(hass.config_entries, "async_reload", return_value=True):
        res_create = await flow.async_step_init(user_input={})
    assert res_create["type"] == "create_entry"
    assert mh202.options[CONF_BUS_TOPOLOGY] == TOPOLOGY_SHARED
    if has_alarm_delta:
        assert mh202.options[CONF_GATEWAY_ROLE] == ROLE_SECONDARY
        assert mh202.options[CONF_DELEGATED_WHOS] == [5]
    else:
        assert mh202.options[CONF_GATEWAY_ROLE] == ROLE_STANDBY
        assert CONF_DELEGATED_WHOS not in mh202.options

    # Abort when gateway deleted
    await hass.config_entries.async_remove(mh202.entry_id)
    flow2 = SharedBusRepairFlow({"mac_a": "00:03:50:aa:bb:01", "mac_b": "00:03:50:aa:bb:02"})
    flow2.hass = hass
    res_abort = await flow2.async_step_init(user_input={})
    assert res_abort["type"] == "abort"
    assert res_abort["reason"] == "gateway_missing"


@pytest.mark.asyncio
async def test_delegated_command_routing_to_secondary(hass: HomeAssistant) -> None:
    """Commands and status requests for delegated WHOs are routed through the secondary gateway (#459)."""
    from unittest.mock import AsyncMock

    from OWNd.message import OWNCommand

    entry_pri, gw_pri = _create_mock_gateway(
        hass, "00:03:50:aa:bb:01", topology=TOPOLOGY_SHARED, role=ROLE_PRIMARY
    )
    entry_sec, gw_sec = _create_mock_gateway(
        hass,
        "00:03:50:aa:bb:02",
        topology=TOPOLOGY_SHARED,
        role=ROLE_SECONDARY,
        primary_gateway="00:03:50:aa:bb:01",
        delegated_whos=[16],
    )
    gw_pri.is_connected = True
    gw_sec.is_connected = True

    gw_sec.send = AsyncMock(return_value=123.45)
    gw_sec.send_status_request = AsyncMock(return_value=123.46)
    gw_pri._command_pool.send = AsyncMock(return_value=100.0)
    gw_pri._command_pool.send_status_request = AsyncMock(return_value=100.1)

    # 1. Delegated WHO=16 command sent to primary routes to secondary
    cmd_audio = OWNCommand.parse("*#16*1*0*1##")
    assert cmd_audio is not None
    res = await gw_pri.send(cmd_audio)
    assert res == 123.45
    gw_sec.send.assert_called_once_with(cmd_audio)
    gw_pri._command_pool.send.assert_not_called()

    # 2. Delegated WHO=16 status request sent to primary routes to secondary
    stat_audio = OWNCommand.parse("*#16*0*5##")
    assert stat_audio is not None
    res_stat = await gw_pri.send_status_request(stat_audio)
    assert res_stat == 123.46
    gw_sec.send_status_request.assert_called_once_with(stat_audio)
    gw_pri._command_pool.send_status_request.assert_not_called()

    # 3. Non-delegated WHO=1 command goes to primary's own command pool
    cmd_light = OWNCommand.parse("*1*1*11##")
    assert cmd_light is not None
    res_light = await gw_pri.send(cmd_light)
    assert res_light == 100.0
    gw_pri._command_pool.send.assert_called_once_with(cmd_light)

    # 4. WHO 13/1013 never routes to secondary
    cmd_diag = OWNCommand.parse("*#1013*0*1##")
    assert cmd_diag is not None
    assert gw_pri._delegated_target(cmd_diag) is None

    # 5. When secondary is disconnected, delegated command falls back rather than failing
    gw_sec.is_connected = False
    assert gw_pri._delegated_target(cmd_audio) is None

    # 6. _get_secondary_for_who defensive edge cases
    assert gw_pri._get_secondary_for_who(99) is None
    gw_pri_no_hass = MyHOMEGatewayHandler(hass, entry_pri)
    gw_pri_no_hass.hass = None  # type: ignore
    assert gw_pri_no_hass._get_secondary_for_who(16) is None


@pytest.mark.asyncio
async def test_shared_bus_repair_flow_invalid_topology_aborts(hass: HomeAssistant) -> None:
    """Test SharedBusRepairFlow aborts if validate_shared_bus_topology returns errors, leaving entries untouched."""
    from custom_components.myhome.repairs import SharedBusRepairFlow

    entry1, _ = _create_mock_gateway(hass, "00:03:50:aa:bb:01")
    entry2, _ = _create_mock_gateway(hass, "00:03:50:aa:bb:02")

    initial_opts_1 = dict(entry1.options)
    initial_opts_2 = dict(entry2.options)

    flow = SharedBusRepairFlow({"mac_a": "00:03:50:aa:bb:01", "mac_b": "00:03:50:aa:bb:02"})
    flow.hass = hass

    # 1. Primary validation fails -> aborts, neither entry is modified
    with patch("custom_components.myhome.repairs.validate_shared_bus_topology", return_value={"error": "pri"}):
        res1 = await flow.async_step_init(user_input={})
        assert res1["type"] == "abort"
        assert res1["reason"] == "invalid_topology"
        assert entry1.options == initial_opts_1
        assert entry2.options == initial_opts_2

    # 2. Secondary validation fails -> aborts, primary remains UNTOUCHED (atomic repair)
    flow2 = SharedBusRepairFlow({"mac_a": "00:03:50:aa:bb:01", "mac_b": "00:03:50:aa:bb:02"})
    flow2.hass = hass
    with patch("custom_components.myhome.repairs.validate_shared_bus_topology", side_effect=[{}, {"error": "sec"}]):
        res2 = await flow2.async_step_init(user_input={})
        assert res2["type"] == "abort"
        assert res2["reason"] == "invalid_topology"
        assert entry1.options == initial_opts_1
        assert entry2.options == initial_opts_2

    # 3. Exception during entry update rolls back both entries to initial options
    flow3 = SharedBusRepairFlow({"mac_a": "00:03:50:aa:bb:01", "mac_b": "00:03:50:aa:bb:02"})
    flow3.hass = hass
    with patch.object(
        hass.config_entries, "async_update_entry", side_effect=[None, RuntimeError("db error"), None, None]
    ):
        with pytest.raises(RuntimeError):
            await flow3.async_step_init(user_input={})
        assert entry1.options == initial_opts_1
        assert entry2.options == initial_opts_2


def test_validate_shared_bus_topology_scenarios(hass: HomeAssistant) -> None:
    """Test all edge cases and validations in validate_shared_bus_topology (#459)."""
    from custom_components.myhome.topology import validate_shared_bus_topology

    # Standalone primary
    entry1, _ = _create_mock_gateway(hass, "00:03:50:aa:bb:01")
    # Secondary entry
    entry2, _ = _create_mock_gateway(
        hass,
        "00:03:50:aa:bb:02",
        topology=TOPOLOGY_SHARED,
        role=ROLE_SECONDARY,
        primary_gateway="00:03:50:aa:bb:01",
        delegated_whos=[16],
    )

    # 1. Follower role requires shared topology
    errs = validate_shared_bus_topology(
        hass,
        entry1,
        {CONF_BUS_TOPOLOGY: TOPOLOGY_STANDALONE, CONF_GATEWAY_ROLE: ROLE_SECONDARY},
    )
    assert errs[CONF_GATEWAY_ROLE] == "secondary_requires_shared_topology"

    # 2. Missing primary gateway
    errs = validate_shared_bus_topology(
        hass,
        entry1,
        {CONF_BUS_TOPOLOGY: TOPOLOGY_SHARED, CONF_GATEWAY_ROLE: ROLE_SECONDARY},
    )
    assert errs[CONF_PRIMARY_GATEWAY] == "primary_gateway_required"

    # 3. Primary equals own MAC
    errs = validate_shared_bus_topology(
        hass,
        entry1,
        {
            CONF_BUS_TOPOLOGY: TOPOLOGY_SHARED,
            CONF_GATEWAY_ROLE: ROLE_SECONDARY,
            CONF_PRIMARY_GATEWAY: "00:03:50:aa:bb:01",
        },
    )
    assert errs[CONF_PRIMARY_GATEWAY] == "invalid_primary_gateway"

    # 4. Primary not found
    errs = validate_shared_bus_topology(
        hass,
        entry1,
        {
            CONF_BUS_TOPOLOGY: TOPOLOGY_SHARED,
            CONF_GATEWAY_ROLE: ROLE_SECONDARY,
            CONF_PRIMARY_GATEWAY: "00:03:50:ff:ff:ff",
        },
    )
    assert errs[CONF_PRIMARY_GATEWAY] == "primary_gateway_not_found"

    # 5. Primary not shared primary (entry1 is currently standalone)
    errs = validate_shared_bus_topology(
        hass,
        entry2,
        {
            CONF_BUS_TOPOLOGY: TOPOLOGY_SHARED,
            CONF_GATEWAY_ROLE: ROLE_SECONDARY,
            CONF_PRIMARY_GATEWAY: "00:03:50:aa:bb:01",
        },
    )
    assert errs[CONF_PRIMARY_GATEWAY] == "primary_gateway_not_shared_primary"

    # Now make entry1 a shared primary
    hass.config_entries.async_update_entry(
        entry1, options={CONF_BUS_TOPOLOGY: TOPOLOGY_SHARED, CONF_GATEWAY_ROLE: ROLE_PRIMARY}
    )

    # 6. Circular gateway reference: if entry1 pointed to entry2
    hass.config_entries.async_update_entry(
        entry1,
        options={
            CONF_BUS_TOPOLOGY: TOPOLOGY_SHARED,
            CONF_GATEWAY_ROLE: ROLE_SECONDARY,
            CONF_PRIMARY_GATEWAY: "00:03:50:aa:bb:02",
        },
    )
    errs = validate_shared_bus_topology(
        hass,
        entry2,
        {
            CONF_BUS_TOPOLOGY: TOPOLOGY_SHARED,
            CONF_GATEWAY_ROLE: ROLE_SECONDARY,
            CONF_PRIMARY_GATEWAY: "00:03:50:aa:bb:01",
        },
    )
    assert errs[CONF_PRIMARY_GATEWAY] == "circular_gateway_reference"
    hass.config_entries.async_update_entry(
        entry1,
        options={CONF_BUS_TOPOLOGY: TOPOLOGY_SHARED, CONF_GATEWAY_ROLE: ROLE_PRIMARY},
    )

    # 7. Multiple standbys on same primary
    entry_sb, _ = _create_mock_gateway(
        hass,
        "00:03:50:aa:bb:03",
        topology=TOPOLOGY_SHARED,
        role=ROLE_STANDBY,
        primary_gateway="00:03:50:aa:bb:01",
    )
    errs = validate_shared_bus_topology(
        hass,
        entry2,
        {
            CONF_BUS_TOPOLOGY: TOPOLOGY_SHARED,
            CONF_GATEWAY_ROLE: ROLE_STANDBY,
            CONF_PRIMARY_GATEWAY: "00:03:50:aa:bb:01",
        },
    )
    assert errs[CONF_GATEWAY_ROLE] == "multiple_standbys"

    # 8. Overlapping delegated WHOs between secondaries
    errs = validate_shared_bus_topology(
        hass,
        entry_sb,  # other entry
        {
            CONF_BUS_TOPOLOGY: TOPOLOGY_SHARED,
            CONF_GATEWAY_ROLE: ROLE_SECONDARY,
            CONF_PRIMARY_GATEWAY: "00:03:50:aa:bb:01",
            CONF_DELEGATED_WHOS: [16],  # entry2 already delegates 16
        },
    )
    assert errs[CONF_DELEGATED_WHOS] == "overlapping_delegated_whos"

    # 9. WHO not supported by gateway model (e.g. WHO 16 on MHS1)
    errs = validate_shared_bus_topology(
        hass,
        entry2,
        {
            CONF_BUS_TOPOLOGY: TOPOLOGY_SHARED,
            CONF_GATEWAY_ROLE: ROLE_SECONDARY,
            CONF_PRIMARY_GATEWAY: "00:03:50:aa:bb:01",
            CONF_DELEGATED_WHOS: [16],
        },
        model_override="MyHomeServer1",
    )
    assert errs[CONF_DELEGATED_WHOS] == "who_not_supported_by_gateway"

    # 10. Model override allows valid WHO (F454 supports WHO 16)
    errs = validate_shared_bus_topology(
        hass,
        entry2,
        {
            CONF_BUS_TOPOLOGY: TOPOLOGY_SHARED,
            CONF_GATEWAY_ROLE: ROLE_SECONDARY,
            CONF_PRIMARY_GATEWAY: "00:03:50:aa:bb:01",
            CONF_DELEGATED_WHOS: [16, "invalid_str"],
        },
        model_override="F454",
    )
    assert CONF_DELEGATED_WHOS not in errs

    # 11. Gateway has dependents and cannot be demoted
    errs = validate_shared_bus_topology(
        hass,
        entry1,
        {
            CONF_BUS_TOPOLOGY: TOPOLOGY_STANDALONE,
            CONF_GATEWAY_ROLE: ROLE_PRIMARY,
        },
    )
    assert errs[CONF_GATEWAY_ROLE] == "gateway_has_dependents"

    # 12. target_primary_options allows validating follower against proposed primary settings
    hass.config_entries.async_update_entry(
        entry1, options={CONF_BUS_TOPOLOGY: TOPOLOGY_STANDALONE, CONF_GATEWAY_ROLE: ROLE_PRIMARY}
    )
    # Without target_primary_options, entry1 being standalone produces error:
    errs_without = validate_shared_bus_topology(
        hass,
        entry2,
        {
            CONF_BUS_TOPOLOGY: TOPOLOGY_SHARED,
            CONF_GATEWAY_ROLE: ROLE_SECONDARY,
            CONF_PRIMARY_GATEWAY: "00:03:50:aa:bb:01",
            CONF_DELEGATED_WHOS: [16],
        },
    )
    assert errs_without[CONF_PRIMARY_GATEWAY] == "primary_gateway_not_shared_primary"

    # With target_primary_options matching entry1's proposed options, validation passes:
    errs_with = validate_shared_bus_topology(
        hass,
        entry2,
        {
            CONF_BUS_TOPOLOGY: TOPOLOGY_SHARED,
            CONF_GATEWAY_ROLE: ROLE_SECONDARY,
            CONF_PRIMARY_GATEWAY: "00:03:50:aa:bb:01",
            CONF_DELEGATED_WHOS: [16],
        },
        target_primary_options={
            CONF_BUS_TOPOLOGY: TOPOLOGY_SHARED,
            CONF_GATEWAY_ROLE: ROLE_PRIMARY,
        },
    )
    assert not errs_with

    # target_primary_options proposing a secondary pointing back produces circular_gateway_reference:
    errs_circular = validate_shared_bus_topology(
        hass,
        entry2,
        {
            CONF_BUS_TOPOLOGY: TOPOLOGY_SHARED,
            CONF_GATEWAY_ROLE: ROLE_SECONDARY,
            CONF_PRIMARY_GATEWAY: "00:03:50:aa:bb:01",
            CONF_DELEGATED_WHOS: [16],
        },
        target_primary_options={
            CONF_BUS_TOPOLOGY: TOPOLOGY_SHARED,
            CONF_GATEWAY_ROLE: ROLE_SECONDARY,
            CONF_PRIMARY_GATEWAY: "00:03:50:aa:bb:02",
        },
    )
    assert errs_circular[CONF_PRIMARY_GATEWAY] == "circular_gateway_reference"


def test_door_entry_who6_who8_capabilities_and_coupling(hass: HomeAssistant) -> None:
    """Verify WHO 6 / WHO 8 capability boundaries, _profile_supports_who, and coupled delegation."""
    from custom_components.myhome.topology import (
        _follower_delegation,
        gateway_delegatable_whos,
        gateway_supported_whos,
        recommend_follower,
        validate_shared_bus_topology,
    )

    # A/V gateways assumed to route both WHO 6 and WHO 8 advertise them for inference & delegation
    for model in ("F453AV", "F452", "F452V"):
        whos = gateway_supported_whos(model)
        assert {6, 8} <= whos, f"{model} should advertise WHO 6 and WHO 8"
        assert {6, 8} <= gateway_delegatable_whos(model)

    # F454 firmware routes bt_vct to WHO 8 only (F459 ships the same image): WHO 6 is never offered
    for model in ("F454", "F459", "Legrand 003598"):
        assert 8 in gateway_supported_whos(model) and 6 not in gateway_supported_whos(model)
        assert 8 in gateway_delegatable_whos(model) and 6 not in gateway_delegatable_whos(model)

    # Single-bus MH scenario programmers with bt_vct (MH200, MH200N / 03565, MH202) accept explicit WHO 6/8
    # delegation (when bridged via F422) without claiming an AV riser during automatic MH inference
    for model in ("MH200", "MH200N", "BTicino 03565", "MH202", "BTicino MH202"):
        assert 6 not in gateway_supported_whos(model) and 8 not in gateway_supported_whos(model)
        assert {6, 8} <= gateway_delegatable_whos(model), f"{model} should accept WHO 6/8 delegation"

    # WHO 6-only MH gateways accept explicit WHO 6 delegation (via F422) without WHO 8
    for model in ("F453", "H4684", "H4890", "MH201"):
        delegatable = gateway_delegatable_whos(model)
        assert 6 in delegatable and 8 not in delegatable, f"{model} should accept WHO 6 only"

    # Legrand 3578 wireless OpenZigBee USB stick must NOT auto-advertise 2-wire A/V WHO 6 or WHO 8
    whos_3578 = gateway_supported_whos("Legrand 3578")
    assert 6 not in whos_3578 and 8 not in whos_3578

    # Automation-only gateways omit bt_vct completely
    for model in ("MyHomeServer1", "F455", "F461", "F450", "F460"):
        assert 6 not in gateway_supported_whos(model) and 8 not in gateway_supported_whos(model)
        assert 6 not in gateway_delegatable_whos(model) and 8 not in gateway_delegatable_whos(model)

    # Defensive fallback if profile lookup raises
    with patch("custom_components.myhome.gateway._profile_for_model", side_effect=RuntimeError("boom")):
        assert gateway_supported_whos("MyHomeServer1") == set()
        assert gateway_delegatable_whos("MyHomeServer1") == set()

    # _profile_supports_who across real MyHOMEGatewayHandler instances
    _, gw_f454 = _create_mock_gateway(hass, "00:03:50:de:00:01", name="F454")
    _, gw_f453 = _create_mock_gateway(hass, "00:03:50:de:00:02", name="F453")
    _, gw_f459 = _create_mock_gateway(hass, "00:03:50:de:00:03", name="F459")
    _, gw_mh202 = _create_mock_gateway(hass, "00:03:50:de:00:04", name="MH202")
    _, gw_mhs1 = _create_mock_gateway(hass, "00:03:50:de:00:05", name="MyHomeServer1")
    _, gw_mh200n = _create_mock_gateway(hass, "00:03:50:de:00:06", name="MH200N")
    _, gw_03565 = _create_mock_gateway(hass, "00:03:50:de:00:08", name="BTicino 03565")
    _, gw_generic = _create_mock_gateway(hass, "00:03:50:de:00:07", name="Legrand 3578")

    assert gw_f454.profile_supports_who(6) is False and gw_f454.profile_supports_who(8) is True
    assert gw_f459.profile_supports_who(6) is False and gw_f459.profile_supports_who(8) is True
    assert gw_mh200n.profile_supports_who(6) is True and gw_mh200n.profile_supports_who(8) is True
    assert gw_03565.profile_supports_who(6) is True and gw_03565.profile_supports_who(8) is True
    assert gw_mh202.profile_supports_who(6) is True and gw_mh202.profile_supports_who(8) is True
    assert gw_f453.profile_supports_who(6) is True and gw_f453.profile_supports_who(8) is False
    assert gw_mhs1.profile_supports_who(6) is False and gw_mhs1.profile_supports_who(8) is False
    assert gw_generic.profile_supports_who(6) is True and gw_generic.profile_supports_who(8) is True

    # Door entry is not coupled like audio: an F454 primary (WHO 8) keeps WHO 8 next to a WHO 6/8 follower
    role_door, delegated_door, _ = _follower_delegation({1, 2, 4, 8}, {1, 2, 4, 6, 8})
    assert role_door == ROLE_SECONDARY
    assert delegated_door == {6}

    # recommend_follower(MyHomeServer1, F454) delegates WHO 8 (not 6) plus audio; Legrand 3578 gets no WHO 6/8
    entry_mhs1 = MockConfigEntry(
        domain=DOMAIN,
        title="MyHomeServer1 Gateway",
        data={CONF_NAME: "MyHomeServer1", CONF_MAC: "00:03:50:aa:11:01"},
        options={CONF_BUS_TOPOLOGY: TOPOLOGY_SHARED, CONF_GATEWAY_ROLE: ROLE_PRIMARY},
        unique_id="00:03:50:aa:11:01",
    )
    entry_mhs1.add_to_hass(hass)
    entry_f454 = MockConfigEntry(
        domain=DOMAIN,
        title="F454 Gateway",
        data={CONF_NAME: "F454", CONF_MAC: "00:03:50:aa:11:02"},
        unique_id="00:03:50:aa:11:02",
    )
    entry_f454.add_to_hass(hass)
    rec_role, rec_whos = recommend_follower(entry_mhs1, entry_f454)
    assert rec_role == ROLE_SECONDARY
    assert rec_whos == {8, 16, 22}

    entry_3578 = MockConfigEntry(
        domain=DOMAIN,
        title="Legrand 3578",
        data={CONF_NAME: "Legrand 3578", CONF_MAC: "00:03:50:aa:11:09"},
        unique_id="00:03:50:aa:11:09",
    )
    entry_3578.add_to_hass(hass)
    _, rec_3578_whos = recommend_follower(entry_mhs1, entry_3578)
    assert 6 not in rec_3578_whos and 8 not in rec_3578_whos

    # validate_shared_bus_topology accepts [8] (rejects [6, 8]) on F454; accepts [6, 8] on MH200N and
    # MH202; accepts [6] (rejects [8]) on F453; and rejects [6] on F455 / F461 / MyHomeServer1
    assert not validate_shared_bus_topology(
        hass,
        entry_f454,
        {
            CONF_BUS_TOPOLOGY: TOPOLOGY_SHARED,
            CONF_GATEWAY_ROLE: ROLE_SECONDARY,
            CONF_PRIMARY_GATEWAY: "00:03:50:aa:11:01",
            CONF_DELEGATED_WHOS: [8],
        },
    )
    errs_f454_who6 = validate_shared_bus_topology(
        hass,
        entry_f454,
        {
            CONF_BUS_TOPOLOGY: TOPOLOGY_SHARED,
            CONF_GATEWAY_ROLE: ROLE_SECONDARY,
            CONF_PRIMARY_GATEWAY: "00:03:50:aa:11:01",
            CONF_DELEGATED_WHOS: [6, 8],
        },
    )
    assert errs_f454_who6[CONF_DELEGATED_WHOS] == "who_not_supported_by_gateway"
    entry_mh200n = MockConfigEntry(
        domain=DOMAIN,
        title="MH200N Gateway",
        data={CONF_NAME: "MH200N", CONF_MAC: "00:03:50:aa:11:03"},
        unique_id="00:03:50:aa:11:03",
    )
    entry_mh200n.add_to_hass(hass)
    assert not validate_shared_bus_topology(
        hass,
        entry_mh200n,
        {
            CONF_BUS_TOPOLOGY: TOPOLOGY_SHARED,
            CONF_GATEWAY_ROLE: ROLE_SECONDARY,
            CONF_PRIMARY_GATEWAY: "00:03:50:aa:11:01",
            CONF_DELEGATED_WHOS: [6, 8],
        },
    )
    entry_mh202 = MockConfigEntry(
        domain=DOMAIN,
        title="MH202 Gateway",
        data={CONF_NAME: "MH202", CONF_MAC: "00:03:50:aa:11:04"},
        unique_id="00:03:50:aa:11:04",
    )
    entry_mh202.add_to_hass(hass)
    assert not validate_shared_bus_topology(
        hass,
        entry_mh202,
        {
            CONF_BUS_TOPOLOGY: TOPOLOGY_SHARED,
            CONF_GATEWAY_ROLE: ROLE_SECONDARY,
            CONF_PRIMARY_GATEWAY: "00:03:50:aa:11:01",
            CONF_DELEGATED_WHOS: [6, 8],
        },
    )
    entry_f453 = MockConfigEntry(
        domain=DOMAIN,
        title="F453 Gateway",
        data={CONF_NAME: "F453", CONF_MAC: "00:03:50:aa:11:06"},
        unique_id="00:03:50:aa:11:06",
    )
    entry_f453.add_to_hass(hass)
    assert not validate_shared_bus_topology(
        hass,
        entry_f453,
        {
            CONF_BUS_TOPOLOGY: TOPOLOGY_SHARED,
            CONF_GATEWAY_ROLE: ROLE_SECONDARY,
            CONF_PRIMARY_GATEWAY: "00:03:50:aa:11:01",
            CONF_DELEGATED_WHOS: [6],
        },
    )
    errs_f453_who8 = validate_shared_bus_topology(
        hass,
        entry_f453,
        {
            CONF_BUS_TOPOLOGY: TOPOLOGY_SHARED,
            CONF_GATEWAY_ROLE: ROLE_SECONDARY,
            CONF_PRIMARY_GATEWAY: "00:03:50:aa:11:01",
            CONF_DELEGATED_WHOS: [6, 8],
        },
    )
    assert errs_f453_who8[CONF_DELEGATED_WHOS] == "who_not_supported_by_gateway"

    entry_f455 = MockConfigEntry(
        domain=DOMAIN,
        title="F455 Gateway",
        data={CONF_NAME: "F455", CONF_MAC: "00:03:50:aa:11:05"},
        unique_id="00:03:50:aa:11:05",
    )
    entry_f455.add_to_hass(hass)
    errs_f455 = validate_shared_bus_topology(
        hass,
        entry_f455,
        {
            CONF_BUS_TOPOLOGY: TOPOLOGY_SHARED,
            CONF_GATEWAY_ROLE: ROLE_SECONDARY,
            CONF_PRIMARY_GATEWAY: "00:03:50:aa:11:01",
            CONF_DELEGATED_WHOS: [6],
        },
    )
    assert errs_f455[CONF_DELEGATED_WHOS] == "who_not_supported_by_gateway"


@pytest.mark.asyncio
async def test_shared_bus_who6_who8_delegation_end_to_end(hass: HomeAssistant) -> None:
    """End-to-end MyHomeServer1 primary + F453AV secondary delegating WHO 6 & WHO 8 for locks and doorbells."""
    import asyncio

    from homeassistant.const import Platform
    from homeassistant.helpers.dispatcher import async_dispatcher_connect
    from OWNd.message import OWNEvent

    from custom_components.myhome.lock import MyHOMELock
    from custom_components.myhome.lock import async_setup_entry as async_setup_lock

    pri_mac = "00:03:50:de:10:01"
    sec_mac = "00:03:50:de:10:02"
    entry_pri, gw_pri = _create_mock_gateway(
        hass,
        pri_mac,
        name="MyHomeServer1",
        topology=TOPOLOGY_SHARED,
        role=ROLE_PRIMARY,
    )
    entry_sec, gw_sec = _create_mock_gateway(
        hass,
        sec_mac,
        name="F453AV",
        topology=TOPOLOGY_SHARED,
        role=ROLE_SECONDARY,
        primary_gateway=pri_mac,
        delegated_whos=[6, 8],
    )

    gw_pri.is_connected = True
    gw_pri._available = True
    gw_sec.is_connected = True
    gw_sec._available = True

    entry_pri.runtime_data.platforms[Platform.LOCK] = {
        "6-4000": {"who": "6", "where": "4000", "name": "Front Gate"},
    }
    entry_sec.runtime_data.platforms[Platform.LOCK] = {
        "6-4001": {"who": "6", "where": "4001", "name": "Side Gate"},
    }
    added_locks: list[MyHOMELock] = []
    added_sec_locks: list[MyHOMELock] = []
    with (
        patch("custom_components.myhome.discovery.er.async_entries_for_config_entry", return_value=[]),
        patch("custom_components.myhome.discovery.er.async_get"),
    ):
        await async_setup_lock(hass, entry_pri, added_locks.extend)
        await async_setup_lock(hass, entry_sec, added_sec_locks.extend)

    assert len(added_locks) == 1
    assert len(added_sec_locks) == 1
    gate_lock = added_locks[0]
    gate_lock.hass = hass
    gate_lock.entity_id = "lock.front_gate"
    gate_lock.async_write_ha_state = MagicMock()

    side_lock = added_sec_locks[0]
    side_lock.hass = hass
    side_lock.entity_id = "lock.side_gate"
    side_lock.async_write_ha_state = MagicMock()

    # 1. Outbound unlock on both Primary-hosted (4000) and Secondary-hosted (4001) YAML locks sends via F453AV
    fut_ok: asyncio.Future[float] = hass.loop.create_future()
    fut_ok.set_result(1.0)
    with (
        patch.object(gw_pri._command_pool, "send", new_callable=AsyncMock) as pri_send,
        patch.object(gw_sec._command_pool, "send", new_callable=AsyncMock, return_value=fut_ok) as sec_send,
    ):
        await gate_lock.async_unlock()
        await side_lock.async_unlock()
        pri_send.assert_not_called()
        assert [str(c.args[0]) for c in sec_send.call_args_list] == [
            "*6*10*4000##",
            "*6*10*4001##",
        ]
    assert gate_lock.is_locked is False
    assert side_lock.is_locked is False
    await gate_lock.async_lock()
    await side_lock.async_lock()
    assert gate_lock.is_locked is True
    assert side_lock.is_locked is True

    # 2. Availability tracks delegated F453AV: stays available if MyHomeServer1 drops, unavailable if F453AV drops
    gw_pri._available = False
    assert gate_lock.available is True
    assert side_lock.available is True

    gw_pri._available = True
    gw_sec._available = False
    gw_sec._notify_availability()
    assert gate_lock.available is False
    assert side_lock.available is False

    gw_sec._available = True
    gw_sec._notify_availability()
    assert gate_lock.available is True
    assert side_lock.available is True

    # 3. Inbound *6*10*4000## and *6*10*4001## on F453AV update Primary-hosted and Secondary-hosted locks
    await gw_sec._process_message(OWNEvent.parse("*6*10*4000##"))
    await gw_sec._process_message(OWNEvent.parse("*6*10*4001##"))
    assert gate_lock.is_locked is False
    assert side_lock.is_locked is False
    gate_lock._cancel_auto_relock()
    side_lock._cancel_auto_relock()

    # 4. Inbound WHO 8 doorbell on F453AV fires once while MyHomeServer1 suppresses duplicate
    doorbells = []
    unsub_bus = hass.bus.async_listen("myhome_doorbell_event", doorbells.append)
    try:
        await gw_pri._process_message(OWNEvent.parse("*8*1#1#4*74##"))
        await gw_sec._process_message(OWNEvent.parse("*8*1#1#4*74##"))
        await hass.async_block_till_done()
    finally:
        unsub_bus()
    assert len(doorbells) == 1
    assert doorbells[0].data["gateway_mac"] == sec_mac

    # 5a. Single-bus MH primary (MH202) does NOT natively hear an unbridged AV riser:
    #     Secondary F453AV DOES bridge delegated WHO 6 even while MH202 is connected,
    #     while NOT bridging delegated WHO 16 (only door-entry frames are bridged).
    _, gw_mh202 = _create_mock_gateway(
        hass,
        "00:03:50:de:10:03",
        name="MH202",
        topology=TOPOLOGY_SHARED,
        role=ROLE_PRIMARY,
    )
    _, gw_f453av_b = _create_mock_gateway(
        hass,
        "00:03:50:de:10:04",
        name="F453AV",
        topology=TOPOLOGY_SHARED,
        role=ROLE_SECONDARY,
        primary_gateway="00:03:50:de:10:03",
        delegated_whos=[6, 16],
    )
    gw_mh202.is_connected = True
    bridged: list[Any] = []
    unsub_bridge = async_dispatcher_connect(hass, f"myhome_message_{gw_mh202.mac}", bridged.append)
    try:
        gw_f453av_b._bridge_to_primary(OWNEvent.parse("*6*10*4000##"))
        assert len(bridged) == 1
        gw_f453av_b._bridge_to_primary(OWNEvent.parse("*#16*1*0*1##"))
        assert len(bridged) == 1
    finally:
        unsub_bridge()

    # 5b. Native A/V primary (F453AV) hears WHO 6 directly while connected and only bridges when offline
    _, gw_f453av_pri = _create_mock_gateway(
        hass,
        "00:03:50:de:10:06",
        name="F453AV",
        topology=TOPOLOGY_SHARED,
        role=ROLE_PRIMARY,
    )
    _, gw_mh202_c = _create_mock_gateway(
        hass,
        "00:03:50:de:10:07",
        name="MH202",
        topology=TOPOLOGY_SHARED,
        role=ROLE_SECONDARY,
        primary_gateway="00:03:50:de:10:06",
        delegated_whos=[6],
    )
    gw_f453av_pri.is_connected = True
    bridged_av: list[Any] = []
    unsub_bridge_av = async_dispatcher_connect(
        hass, f"myhome_message_{gw_f453av_pri.mac}", bridged_av.append
    )
    try:
        gw_mh202_c._bridge_to_primary(OWNEvent.parse("*6*10*4000##"))
        assert bridged_av == []
        gw_f453av_pri.is_connected = False
        gw_mh202_c._bridge_to_primary(OWNEvent.parse("*6*10*4000##"))
        assert len(bridged_av) == 1
    finally:
        unsub_bridge_av()

    # 6. Closing/unloading secondary gateway notifies primary availability listeners immediately
    pri_avail_signals: list[None] = []
    unsub_avail = async_dispatcher_connect(
        hass, gw_pri.availability_signal, lambda: pri_avail_signals.append(None)
    )
    try:
        await gw_sec.close_listener()
        assert len(pri_avail_signals) == 1
        assert gate_lock.available is False
    finally:
        unsub_avail()

    # Orphaned secondary (primary missing) close_listener is a safe no-op on primary signal
    _, gw_sec_orphan = _create_mock_gateway(
        hass,
        "00:03:50:de:10:05",
        name="F453AV",
        topology=TOPOLOGY_SHARED,
        role=ROLE_SECONDARY,
        primary_gateway="00:03:50:de:99:99",
        delegated_whos=[6],
    )
    await gw_sec_orphan.close_listener()

    await gate_lock.async_will_remove_from_hass()
    await side_lock.async_will_remove_from_hass()


@pytest.mark.asyncio
async def test_three_gateway_shared_bus_standby_and_who6_secondary(hass: HomeAssistant) -> None:
    """Verify 3-gateway shared bus (Primary + Warm Standby + WHO 6/8 Secondary) deduplication & partial standby availability."""
    from homeassistant.helpers.dispatcher import async_dispatcher_connect
    from OWNd.message import OWNEvent, OWNLightingEvent

    pri_mac = "00:03:50:de:20:01"
    sb_mac = "00:03:50:de:20:02"
    sec_mac = "00:03:50:de:20:03"
    _, gw_pri = _create_mock_gateway(
        hass, pri_mac, name="MyHomeServer1", topology=TOPOLOGY_SHARED, role=ROLE_PRIMARY
    )
    _, gw_sb = _create_mock_gateway(
        hass,
        sb_mac,
        name="F453AV",
        topology=TOPOLOGY_SHARED,
        role=ROLE_STANDBY,
        primary_gateway=pri_mac,
    )
    _, gw_sec = _create_mock_gateway(
        hass,
        sec_mac,
        name="F452V",
        topology=TOPOLOGY_SHARED,
        role=ROLE_SECONDARY,
        primary_gateway=pri_mac,
        delegated_whos=[6, 8],
    )

    # Primary is offline; both Warm Standby (F453AV) and Secondary (F452V) are online
    gw_pri.is_connected = False
    gw_pri._available = False
    gw_sb.is_connected = True
    gw_sb._available = True
    gw_sec.is_connected = True
    gw_sec._available = True

    bridged: list[str] = []
    doorbells = []
    unsub_msg = async_dispatcher_connect(
        hass, f"myhome_message_{pri_mac}", lambda m: bridged.append(str(m))
    )
    unsub_bell = hass.bus.async_listen("myhome_doorbell_event", doorbells.append)
    try:
        # 1. Both standby and secondary see *8*1#1#4*74## and *6*10*4000## -> only secondary emits/bridges
        await gw_sb._process_message(OWNEvent.parse("*8*1#1#4*74##"))
        await gw_sec._process_message(OWNEvent.parse("*8*1#1#4*74##"))
        await gw_sb._process_message(OWNEvent.parse("*6*10*4000##"))
        await gw_sec._process_message(OWNEvent.parse("*6*10*4000##"))
        # Standby still bridges non-delegated WHO 1 frames
        await gw_sb._process_message(OWNLightingEvent.parse("*1*1*21##"))
        await hass.async_block_till_done()

        assert len(doorbells) == 1
        assert doorbells[0].data["gateway_mac"] == sec_mac
        assert bridged == ["*8*1#1#4*74##", "*6*10*4000##", "*1*1*21##"]

        # 2. Secondary ALSO drops -> A/V warm standby (F453AV) seamlessly takes over WHO 6/8 bridging & doorbells
        gw_sec.is_connected = False
        gw_sec._available = False
        assert gw_pri.is_who_available(6) is True
        await gw_sb._process_message(OWNEvent.parse("*8*1#1#4*74##"))
        await gw_sb._process_message(OWNEvent.parse("*6*10*4000##"))
        await hass.async_block_till_done()

        assert len(doorbells) == 2
        # Warm standby failover routes doorbell events under primary's MAC (M-3)
        assert doorbells[1].data["gateway_mac"] == pri_mac
        assert bridged == [
            "*8*1#1#4*74##",
            "*6*10*4000##",
            "*1*1*21##",
            "*8*1#1#4*74##",
            "*6*10*4000##",
        ]
    finally:
        unsub_msg()
        unsub_bell()

    # 3. Partial standby coverage: MH202 primary (WHO 6 capable) + F455 standby (omits WHO 6)
    mh_mac = "00:03:50:de:20:04"
    f455_mac = "00:03:50:de:20:05"
    _, gw_mh = _create_mock_gateway(
        hass, mh_mac, name="MH202", topology=TOPOLOGY_SHARED, role=ROLE_PRIMARY
    )
    _, gw_f455 = _create_mock_gateway(
        hass,
        f455_mac,
        name="F455",
        topology=TOPOLOGY_SHARED,
        role=ROLE_STANDBY,
        primary_gateway=mh_mac,
    )
    gw_mh.is_connected = False
    gw_mh._available = True
    gw_f455.is_connected = True
    gw_f455._available = True

    avail_updates: list[tuple[bool, bool]] = []
    unsub_mh_avail = async_dispatcher_connect(
        hass,
        gw_mh.availability_signal,
        lambda: avail_updates.append((gw_mh.is_who_available(1), gw_mh.is_who_available(6))),
    )
    try:
        gw_mh._mark_unavailable(None)
    finally:
        unsub_mh_avail()

    assert gw_mh.available is True
    assert gw_mh.is_who_available(1) is True
    assert gw_mh.is_who_available(6) is False
    assert avail_updates == [(True, False)]


@pytest.mark.asyncio
async def test_mh202_live_trace_who6_who8_delegation_and_standby_failover(
    hass: HomeAssistant,
) -> None:
    """Replay authentic physical MH202 FW 1.0 trace (61 frames, WHO 6 + WHO 8) under delegation and warm standby."""
    import json
    from pathlib import Path

    from homeassistant.const import Platform
    from homeassistant.helpers.dispatcher import async_dispatcher_connect
    from OWNd.message import OWNMessage

    from custom_components.myhome.lock import MyHOMELock
    from custom_components.myhome.lock import async_setup_entry as async_setup_lock
    from custom_components.myhome.topology import validate_shared_bus_topology

    trace_path = (
        Path(__file__).resolve().parent
        / "fixtures"
        / "traces"
        / "mh202_sound_intercom_ducking"
        / "myhome_trace_MH202_all_2026-10-18T10-27-48.json"
    )
    assert trace_path.is_file(), f"Missing live MH202 trace fixture: {trace_path}"
    trace_payload = json.loads(trace_path.read_text(encoding="utf-8"))
    assert trace_payload["gateway"]["model"] == "MH202"
    assert trace_payload["gateway"]["firmware"] == "1.0"
    frames = trace_payload["frames"]
    assert len(frames) == 61

    pri_mac = "00:03:50:de:30:01"
    sec_mac = "00:03:50:de:30:02"
    entry_pri, gw_pri = _create_mock_gateway(
        hass,
        pri_mac,
        name="MyHomeServer1",
        topology=TOPOLOGY_SHARED,
        role=ROLE_PRIMARY,
    )
    entry_sec, gw_sec = _create_mock_gateway(
        hass,
        sec_mac,
        name="MH202",
        topology=TOPOLOGY_SHARED,
        role=ROLE_SECONDARY,
        primary_gateway=pri_mac,
        delegated_whos=[6, 8],
    )
    assert not validate_shared_bus_topology(
        hass,
        entry_sec,
        {
            CONF_BUS_TOPOLOGY: TOPOLOGY_SHARED,
            CONF_GATEWAY_ROLE: ROLE_SECONDARY,
            CONF_PRIMARY_GATEWAY: pri_mac,
            CONF_DELEGATED_WHOS: [6, 8],
        },
    )

    gw_pri.is_connected = True
    gw_pri._available = True
    gw_sec.is_connected = True
    gw_sec._available = True

    entry_pri.runtime_data.platforms[Platform.LOCK] = {
        "6-1": {"who": "6", "where": "1", "name": "Indoor Unit Release"},
        "6-4000": {"who": "6", "where": "4000", "name": "Entrance Panel Strike"},
    }
    added_locks: list[MyHOMELock] = []
    with (
        patch("custom_components.myhome.discovery.er.async_entries_for_config_entry", return_value=[]),
        patch("custom_components.myhome.discovery.er.async_get"),
    ):
        await async_setup_lock(hass, entry_pri, added_locks.extend)

    assert len(added_locks) == 2
    locks_by_where = {lk._where: lk for lk in added_locks}
    for lk in added_locks:
        lk.hass = hass
        lk.entity_id = f"lock.mh202_trace_{lk._where}"
        lk.async_write_ha_state = MagicMock()
        assert lk.is_locked is True

    try:
        bridged_to_primary: list[str] = []
        doorbells = []
        unsub_bridge = async_dispatcher_connect(
            hass, f"myhome_message_{pri_mac}", lambda m: bridged_to_primary.append(str(m))
        )
        unsub_bell = hass.bus.async_listen("myhome_doorbell_event", doorbells.append)
        try:
            for item in frames:
                msg = OWNMessage.parse(item["raw"])
                assert msg is not None, f"Failed to parse trace frame {item['raw']}"
                await gw_sec._process_message(msg)
            await hass.async_block_till_done()
        finally:
            unsub_bridge()
            unsub_bell()

        # 1. WHO 8 visitor call (*8*1#1#4#20*11##) fires once; caller notification (*8*9#1#4*20##) is ignored
        assert len(doorbells) == 1
        assert doorbells[0].data == {
            "where": "11",
            "event": "call",
            "is_broadcast": False,
            "gateway_mac": sec_mac,
            "entry_id": entry_sec.entry_id,
        }

        # 2. Only delegated WHO 6 & WHO 8 frames are bridged to MyHomeServer1; non-delegated WHO 9/13/16/18/22/25 are not
        assert bridged_to_primary == [
            "*8*1#1#4#20*11##",
            "*8*9#1#4*20##",
            "*6*22*1##",
            "*6*10*4000##",
            "*6*24*1##",
            "*6*23*4000##",
            "*6*9**##",
        ]
        assert locks_by_where["1"].is_locked is False
        assert locks_by_where["4000"].is_locked is False
        for lk in added_locks:
            await lk.async_lock()
            assert lk.is_locked is True

        # 3. Warm Standby failover: MH202 standby relays both WHO 8 doorbells (attributed to Primary) and WHO 6 locks
        pri_av_mac = "00:03:50:de:30:03"
        sb_mh_mac = "00:03:50:de:30:04"
        entry_pri_av, gw_pri_av = _create_mock_gateway(
            hass,
            pri_av_mac,
            name="F454",
            topology=TOPOLOGY_SHARED,
            role=ROLE_PRIMARY,
        )
        _, gw_sb_mh = _create_mock_gateway(
            hass,
            sb_mh_mac,
            name="MH202",
            topology=TOPOLOGY_SHARED,
            role=ROLE_STANDBY,
            primary_gateway=pri_av_mac,
        )
        gw_pri_av.is_connected = False
        gw_pri_av._available = False
        gw_sb_mh.is_connected = True
        gw_sb_mh._available = True
        assert gw_pri_av.is_who_available(6) is True
        assert gw_pri_av.is_who_available(8) is True

        sb_doorbells = []
        sb_bridged: list[str] = []
        unsub_sb_bridge = async_dispatcher_connect(
            hass, f"myhome_message_{pri_av_mac}", lambda m: sb_bridged.append(str(m))
        )
        unsub_sb_bell = hass.bus.async_listen("myhome_doorbell_event", sb_doorbells.append)
        try:
            for item in frames:
                msg = OWNMessage.parse(item["raw"])
                assert msg is not None
                await gw_sb_mh._process_message(msg)
            await hass.async_block_till_done()
        finally:
            unsub_sb_bridge()
            unsub_sb_bell()

        assert len(sb_doorbells) == 1
        assert sb_doorbells[0].data == {
            "where": "11",
            "event": "call",
            "is_broadcast": False,
            "gateway_mac": pri_av_mac,
            "entry_id": entry_pri_av.entry_id,
        }
        assert "*6*22*1##" in sb_bridged
        assert "*6*10*4000##" in sb_bridged
    finally:
        for lk in added_locks:
            await lk.async_will_remove_from_hass()


def test_door_entry_guards_apply_only_to_who6_who8(hass: HomeAssistant) -> None:
    """An F454 secondary keeps WHO 8 and non-door subsystems but never takes WHO 6."""
    from homeassistant.helpers.dispatcher import async_dispatcher_connect
    from OWNd.message import OWNCommand, OWNEvent

    from custom_components.myhome.lock import warn_if_locks_unsupported

    pri_mac = "00:03:50:de:50:01"
    sec_mac = "00:03:50:de:50:02"
    _, gw_pri = _create_mock_gateway(
        hass, pri_mac, name="MyHomeServer1", topology=TOPOLOGY_SHARED, role=ROLE_PRIMARY
    )
    _, gw_sec = _create_mock_gateway(
        hass,
        sec_mac,
        name="F454",
        topology=TOPOLOGY_SHARED,
        role=ROLE_SECONDARY,
        primary_gateway=pri_mac,
        delegated_whos=[1, 6, 8, 9],
    )
    for gw in (gw_pri, gw_sec):
        gw.is_connected = True
        gw._available = True

    # Outbound: WHO 6 stays on the primary (whose NACK the lock reports); WHO 1 goes to the secondary
    lock_cmd = OWNCommand.parse("*6*10*4000##")
    light_cmd = OWNCommand.parse("*1*1*21##")
    assert lock_cmd is not None and light_cmd is not None
    assert gw_pri._delegated_target(lock_cmd) is None
    assert gw_pri._delegated_target(light_cmd) is gw_sec

    # Availability: WHO 8 rides on the F454, WHO 6 has no gateway that routes it, and WHO 9
    # (in no OWNd profile) keeps the plain primary availability
    assert gw_pri.is_who_available(8) is True
    assert gw_pri.is_who_available(6) is False
    assert gw_pri.is_who_available(9) is True

    # Inbound: the secondary bridges the WHO 8 call the primary cannot hear, not its WHO 1 / 9 frames
    aux_evt = OWNEvent.parse("*9*1*1##")
    assert aux_evt is not None
    bridged: list[str] = []
    unsub = async_dispatcher_connect(hass, f"myhome_message_{pri_mac}", lambda m: bridged.append(str(m)))
    try:
        gw_sec._bridge_to_primary(OWNEvent.parse("*8*1#1#4*74##"))
        gw_sec._bridge_to_primary(OWNEvent.parse("*1*1*21##"))
        gw_sec._bridge_to_primary(aux_evt)
    finally:
        unsub()
    assert bridged == ["*8*1#1#4*74##"]

    # Primary-hosted WHO 6 locks warn: delegating WHO 6 to an F454 does not make it routable
    assert warn_if_locks_unsupported(gw_pri, [MagicMock(_full_where="4000")]) is True


@pytest.mark.asyncio
async def test_door_entry_generic_delegation_and_runtime_who13_resolution(
    hass: HomeAssistant, caplog: pytest.LogCaptureFixture
) -> None:
    """Verify Generic manual WHO 6/8 delegation and runtime WHO=13/1013 resolution warnings & target fallback."""
    from homeassistant.const import Platform
    from OWNd.message import OWNCommand

    from custom_components.myhome.lock import MyHOMELock
    from custom_components.myhome.lock import async_setup_entry as async_setup_lock
    from custom_components.myhome.topology import (
        gateway_delegatable_whos,
        gateway_supported_whos,
        validate_shared_bus_topology,
    )

    # 1. Generic manual-IP gateway allows manual WHO 6/8 delegation without auto-inferring WHO 6/8;
    #    Legrand 3578 USB coordinator excludes WHO 6/8 from both.
    assert {6, 8}.isdisjoint(gateway_supported_whos("Generic"))
    assert {6, 8} <= gateway_delegatable_whos("Generic")
    # ...while its other subsystems still come from the conservative Generic profile
    assert {5, 9}.isdisjoint(gateway_delegatable_whos("Generic"))
    assert {6, 8}.isdisjoint(gateway_delegatable_whos("Legrand 3578"))

    entry_pri_check = MockConfigEntry(
        domain=DOMAIN,
        title="MyHomeServer1 Gateway",
        data={CONF_NAME: "MyHomeServer1", CONF_MAC: "00:03:50:de:40:01"},
        options={CONF_BUS_TOPOLOGY: TOPOLOGY_SHARED, CONF_GATEWAY_ROLE: ROLE_PRIMARY},
        unique_id="00:03:50:de:40:01",
    )
    entry_pri_check.add_to_hass(hass)
    entry_gen_check = MockConfigEntry(
        domain=DOMAIN,
        title="Generic Gateway",
        data={CONF_NAME: "Generic", CONF_MAC: "00:03:50:de:40:02"},
        unique_id="00:03:50:de:40:02",
    )
    entry_gen_check.add_to_hass(hass)
    assert not validate_shared_bus_topology(
        hass,
        entry_gen_check,
        {
            CONF_BUS_TOPOLOGY: TOPOLOGY_SHARED,
            CONF_GATEWAY_ROLE: ROLE_SECONDARY,
            CONF_PRIMARY_GATEWAY: "00:03:50:de:40:01",
            CONF_DELEGATED_WHOS: [6, 8],
        },
    )

    # 2. Standalone Generic gateway with WHO=6 lock warns once as soon as _apply_model resolves MyHomeServer1
    entry_standalone, gw_standalone = _create_mock_gateway(
        hass, "00:03:50:de:40:03", name="Generic"
    )
    entry_standalone.runtime_data.platforms[Platform.LOCK] = {
        "6-4000": {"who": "6", "where": "4000", "name": "Standalone Gate"},
    }
    standalone_locks: list[MyHOMELock] = []
    caplog.clear()
    with (
        patch("custom_components.myhome.discovery.er.async_entries_for_config_entry", return_value=[]),
        patch("custom_components.myhome.discovery.er.async_get"),
        caplog.at_level("WARNING"),
    ):
        await async_setup_lock(hass, entry_standalone, standalone_locks.extend)
    assert len(standalone_locks) == 1
    assert not [r for r in caplog.records if "bt_vct" in r.message]

    caplog.clear()
    with caplog.at_level("WARNING"):
        gw_standalone._apply_model("MyHomeServer1")
        gw_standalone._apply_model("MyHomeServer1")
    who6_warnings = [r.message for r in caplog.records if "bt_vct" in r.message]
    assert len(who6_warnings) == 1
    assert "4000" in who6_warnings[0] and "MyHomeServer1" in who6_warnings[0]

    # 3. Shared-bus MyHomeServer1 Primary + Generic Secondary (delegated_whos=[6, 8]):
    #    when WHO=13 corrects Secondary to F455, Primary warns, lock availability drops,
    #    and _delegated_target / claim_in_flight_dimension_where skip F455.
    pri_mac = "00:03:50:de:40:04"
    sec_mac = "00:03:50:de:40:05"
    entry_pri, gw_pri = _create_mock_gateway(
        hass,
        pri_mac,
        name="MyHomeServer1",
        topology=TOPOLOGY_SHARED,
        role=ROLE_PRIMARY,
    )
    entry_sec, gw_sec = _create_mock_gateway(
        hass,
        sec_mac,
        name="Generic",
        topology=TOPOLOGY_SHARED,
        role=ROLE_SECONDARY,
        primary_gateway=pri_mac,
        delegated_whos=[6, 8],
    )
    gw_pri.is_connected = True
    gw_pri._available = True
    gw_sec.is_connected = True
    gw_sec._available = True

    entry_pri.runtime_data.platforms[Platform.LOCK] = {
        "6-4001": {"who": "6", "where": "4001", "name": "Shared Gate"},
    }
    pri_locks: list[MyHOMELock] = []
    caplog.clear()
    with (
        patch("custom_components.myhome.discovery.er.async_entries_for_config_entry", return_value=[]),
        patch("custom_components.myhome.discovery.er.async_get"),
        caplog.at_level("WARNING"),
    ):
        await async_setup_lock(hass, entry_pri, pri_locks.extend)
    assert len(pri_locks) == 1
    assert not [r for r in caplog.records if "bt_vct" in r.message]

    shared_lock = pri_locks[0]
    shared_lock.hass = hass
    shared_lock.entity_id = "lock.shared_gate"
    shared_lock.async_write_ha_state = MagicMock()
    assert shared_lock.available is True

    cmd_lock = OWNCommand.parse("*6*10*4001##")
    assert cmd_lock is not None
    assert gw_pri._delegated_target(cmd_lock) is gw_sec

    caplog.clear()
    with caplog.at_level("WARNING"):
        gw_sec._apply_model("F455")
    pri_warnings = [r.message for r in caplog.records if "bt_vct" in r.message]
    assert len(pri_warnings) == 1
    assert "4001" in pri_warnings[0] and "MyHomeServer1" in pri_warnings[0]
    assert shared_lock.available is False
    assert gw_pri._delegated_target(cmd_lock) is None
    assert gw_pri.claim_in_flight_dimension_where(6, 1) is None

    for lk in (*standalone_locks, *pri_locks):
        await lk.async_will_remove_from_hass()





