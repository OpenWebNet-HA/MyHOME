"""A secondary or standby whose primary was removed gets a primary again.

Removing the config entry of a shared primary used to leave its followers as they were: each
kept the role of a follower of a gateway that no longer exists, so it skipped the startup sweep,
asked its devices nothing and created no entities for the subsystems it had left to that primary.
The ``primary_gateway_missing`` repair told the owner to open Configure, but the options form
shows the topology fields only while another MyHOME entry exists, so the last remaining gateway
could not be changed at all.

Now the only follower becomes a standalone gateway, of several followers the warm standby becomes
the shared primary and the secondaries point at it, and several secondaries without a standby are
left to the owner (the repair issue stays). The same happens at setup for a follower whose primary
was removed earlier.
"""

from __future__ import annotations

from unittest.mock import patch

from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant
from homeassistant.helpers import issue_registry as ir
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.myhome.const import (
    CONF_BUS_TOPOLOGY,
    CONF_DELEGATED_WHOS,
    CONF_GATEWAY_ROLE,
    CONF_PRIMARY_GATEWAY,
    DOMAIN,
    ISSUE_PRIMARY_GATEWAY_MISSING,
    ROLE_PRIMARY,
    ROLE_SECONDARY,
    ROLE_STANDBY,
    TOPOLOGY_SHARED,
    TOPOLOGY_STANDALONE,
)
from custom_components.myhome.topology import (
    async_check_primary_links,
    async_promote_orphaned_followers,
    entry_delegated_whos,
    entry_is_follower,
    entry_primary_mac,
    entry_role,
    entry_topology,
)

PRI = "00:03:50:aa:bb:01"
SB = "00:03:50:aa:bb:02"
SEC = "00:03:50:aa:bb:03"
SEC2 = "00:03:50:aa:bb:04"
SHARED_PRIMARY = {CONF_BUS_TOPOLOGY: TOPOLOGY_SHARED, CONF_GATEWAY_ROLE: ROLE_PRIMARY}
STANDBY = {CONF_BUS_TOPOLOGY: TOPOLOGY_SHARED, CONF_GATEWAY_ROLE: ROLE_STANDBY, CONF_PRIMARY_GATEWAY: PRI}


def _secondary(*whos: int) -> dict:
    return {
        CONF_BUS_TOPOLOGY: TOPOLOGY_SHARED,
        CONF_GATEWAY_ROLE: ROLE_SECONDARY,
        CONF_PRIMARY_GATEWAY: PRI,
        CONF_DELEGATED_WHOS: list(whos),
    }


def _patches():
    return (
        patch("custom_components.myhome.gateway.OWNSession.test_connection", return_value={"Success": True, "Message": None}),
        patch("custom_components.myhome.gateway.MyHOMEGatewayHandler.listening_loop"),
        patch("custom_components.myhome.gateway.MyHOMEGatewayHandler.sending_loop"),
    )


def _entry(hass: HomeAssistant, mac: str, options: dict) -> MockConfigEntry:
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={"host": f"192.168.0.{int(mac[-2:], 16) + 30}", "port": 20000, "password": "pass", "mac": mac},
        options=options,
        unique_id=mac,
        title=f"{mac} Gateway",
    )
    entry.add_to_hass(hass)
    return entry


async def _set_up(hass: HomeAssistant, mac: str, options: dict) -> MockConfigEntry:
    entry = _entry(hass, mac, options)
    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return entry


def _issue(hass: HomeAssistant, entry: MockConfigEntry) -> ir.IssueEntry | None:
    return ir.async_get(hass).async_get_issue(DOMAIN, f"{ISSUE_PRIMARY_GATEWAY_MISSING}_{entry.entry_id}")


# ------------------------------------------------------------------ the primary is removed


async def test_the_only_standby_becomes_a_standalone_gateway(hass: HomeAssistant) -> None:
    p1, p2, p3 = _patches()
    with p1, p2, p3:
        primary = await _set_up(hass, PRI, SHARED_PRIMARY)
        standby = await _set_up(hass, SB, STANDBY)
        before = standby.runtime_data.gateway
        assert before.is_follower is True

        await hass.config_entries.async_remove(primary.entry_id)
        await hass.async_block_till_done()

        assert dict(standby.options) == {CONF_BUS_TOPOLOGY: TOPOLOGY_STANDALONE, CONF_GATEWAY_ROLE: ROLE_PRIMARY}
        assert standby.state is ConfigEntryState.LOADED
        gateway = standby.runtime_data.gateway
        assert gateway is not before  # reloaded: a new handler started with the new role
        assert gateway.is_follower is False and gateway.is_primary is True
        assert _issue(hass, standby) is None


async def test_the_only_secondary_becomes_a_standalone_gateway_and_drops_its_delegation(hass: HomeAssistant) -> None:
    p1, p2, p3 = _patches()
    with p1, p2, p3:
        primary = await _set_up(hass, PRI, SHARED_PRIMARY)
        secondary = await _set_up(hass, SEC, {**_secondary(4), "generate_events": True})

        await hass.config_entries.async_remove(primary.entry_id)
        await hass.async_block_till_done()

        assert dict(secondary.options) == {
            CONF_BUS_TOPOLOGY: TOPOLOGY_STANDALONE,
            CONF_GATEWAY_ROLE: ROLE_PRIMARY,
            "generate_events": True,  # every other option is kept
        }
        assert secondary.runtime_data.gateway.is_follower is False
        assert secondary.runtime_data.gateway.delegated_whos == set()
        assert _issue(hass, secondary) is None


async def test_the_standby_takes_over_and_the_secondary_follows_it(hass: HomeAssistant) -> None:
    p1, p2, p3 = _patches()
    with p1, p2, p3:
        primary = await _set_up(hass, PRI, SHARED_PRIMARY)
        secondary = await _set_up(hass, SEC, _secondary(4))
        standby = await _set_up(hass, SB, STANDBY)

        await hass.config_entries.async_remove(primary.entry_id)
        await hass.async_block_till_done()

        assert dict(standby.options) == SHARED_PRIMARY
        assert entry_primary_mac(secondary) == SB
        assert entry_role(secondary) == ROLE_SECONDARY and entry_delegated_whos(secondary) == {4}
        assert standby.runtime_data.gateway.is_primary is True
        assert standby.runtime_data.gateway.delegated_away_whos == {4}
        assert secondary.runtime_data.gateway.primary_gateway_mac == SB
        assert _issue(hass, standby) is None and _issue(hass, secondary) is None


async def test_two_secondaries_without_a_standby_are_left_to_the_owner(hass: HomeAssistant) -> None:
    p1, p2, p3 = _patches()
    with p1, p2, p3:
        primary = await _set_up(hass, PRI, SHARED_PRIMARY)
        first = await _set_up(hass, SEC, _secondary(4))
        second = await _set_up(hass, SEC2, _secondary(18))

        await hass.config_entries.async_remove(primary.entry_id)
        await hass.async_block_till_done()

        assert dict(first.options) == _secondary(4) and dict(second.options) == _secondary(18)
        assert _issue(hass, first) is not None and _issue(hass, second) is not None


async def test_removing_a_follower_changes_nothing(hass: HomeAssistant) -> None:
    p1, p2, p3 = _patches()
    with p1, p2, p3:
        primary = await _set_up(hass, PRI, SHARED_PRIMARY)
        standby = await _set_up(hass, SB, STANDBY)

        await hass.config_entries.async_remove(standby.entry_id)
        await hass.async_block_till_done()

        assert dict(primary.options) == SHARED_PRIMARY


# ------------------------------------------------------------------ the primary was removed earlier


async def test_a_follower_found_without_its_primary_at_setup_is_promoted_before_it_starts(hass: HomeAssistant) -> None:
    """The state an installation is in when the primary was removed before this fix."""
    p1, p2, p3 = _patches()
    with p1, p2, p3, patch.object(hass.config_entries, "async_schedule_reload") as reload:
        standby = await _set_up(hass, SB, STANDBY)

        assert dict(standby.options) == {CONF_BUS_TOPOLOGY: TOPOLOGY_STANDALONE, CONF_GATEWAY_ROLE: ROLE_PRIMARY}
        assert standby.state is ConfigEntryState.LOADED
        assert standby.runtime_data.gateway.is_follower is False
        reload.assert_not_called()  # its own setup read the new role
        assert _issue(hass, standby) is None


async def test_a_loaded_follower_is_reloaded_and_one_not_loaded_is_not(hass: HomeAssistant) -> None:
    p1, p2, p3 = _patches()
    with p1, p2, p3:
        primary = await _set_up(hass, PRI, SHARED_PRIMARY)
        loaded = await _set_up(hass, SEC, _secondary(4))
        standby = _entry(hass, SB, STANDBY)  # configured, not set up yet
        assert standby.state is ConfigEntryState.NOT_LOADED
        with patch.object(hass.config_entries, "async_schedule_reload") as reload:
            changed = async_promote_orphaned_followers(hass, removed=primary.entry_id)
        assert changed == [standby, loaded]
        reload.assert_called_once_with(loaded.entry_id)


# ------------------------------------------------------------------ what is not touched


def test_a_primary_that_still_has_an_entry_is_not_replaced(hass: HomeAssistant) -> None:
    """Reconfigured to standalone, disabled or failing: the owner decides, the repair issue says so."""
    _entry(hass, PRI, {CONF_BUS_TOPOLOGY: TOPOLOGY_STANDALONE})
    standby = _entry(hass, SB, STANDBY)
    assert async_promote_orphaned_followers(hass) == []
    assert entry_is_follower(standby) is True
    async_check_primary_links(hass)
    assert _issue(hass, standby) is not None


def test_gateways_that_are_no_followers_are_not_touched(hass: HomeAssistant) -> None:
    standalone = _entry(hass, PRI, {})
    shared_primary = _entry(hass, SB, SHARED_PRIMARY)
    assert async_promote_orphaned_followers(hass) == []
    assert dict(standalone.options) == {} and dict(shared_primary.options) == SHARED_PRIMARY
    assert entry_topology(standalone) == TOPOLOGY_STANDALONE
