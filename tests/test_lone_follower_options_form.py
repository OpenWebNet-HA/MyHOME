"""The Configure form of a secondary / warm standby that is the last MyHOME entry (#453).

When the primary of a follower is removed, the follower is flagged with the
``primary_gateway_missing`` repair and nothing else changes: which gateway is the primary is
the owner's choice. The repair says "Open Configure on {gateway} and either select another
Primary gateway or set its Bus Topology to Standalone". With no other MyHOME entry left the form
had no topology field at all, and saving it was refused with ``primary_gateway_not_found`` on a
field it did not show, so the advice could not be followed.

The form of such an entry now has the Bus Topology field, and only that one. Nothing changes
until the owner saves ``standalone``.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType

from custom_components.myhome.const import (
    CONF_BUS_TOPOLOGY,
    CONF_DELEGATED_WHOS,
    CONF_GATEWAY_ROLE,
    CONF_PRIMARY_GATEWAY,
    ISSUE_PRIMARY_GATEWAY_MISSING,
    ROLE_PRIMARY,
    ROLE_SECONDARY,
    ROLE_STANDBY,
    TOPOLOGY_SHARED,
    TOPOLOGY_STANDALONE,
)
from tests.test_config_flow import _open_gateway_options
from tests.test_multi_gateway_regressions import PRI, SB, _issue, _set_up, _setup_patches

TOPOLOGY_FIELDS = {CONF_BUS_TOPOLOGY, CONF_GATEWAY_ROLE, CONF_PRIMARY_GATEWAY, CONF_DELEGATED_WHOS}
STANDBY = {CONF_BUS_TOPOLOGY: TOPOLOGY_SHARED, CONF_GATEWAY_ROLE: ROLE_STANDBY, CONF_PRIMARY_GATEWAY: PRI}
SECONDARY = {
    CONF_BUS_TOPOLOGY: TOPOLOGY_SHARED,
    CONF_GATEWAY_ROLE: ROLE_SECONDARY,
    CONF_PRIMARY_GATEWAY: PRI,
    CONF_DELEGATED_WHOS: [16],
}
OTHER_SETTINGS = {
    "address": "192.168.0.36",
    "name": "Generic gateway",
    "command_worker_count": 1,
    "generate_events": False,
    "transition_mode": "software_stepped",
}


def _topology_fields(form) -> dict[str, object]:
    """The topology fields of a form, with the value each one suggests."""
    return {
        str(key): (key.description or {}).get("suggested_value")
        for key in form["data_schema"].schema
        if str(key) in TOPOLOGY_FIELDS
    }


@pytest.mark.parametrize("stored", [STANDBY, SECONDARY], ids=["standby", "secondary"])
async def test_the_last_entry_that_is_a_follower_gets_the_bus_topology_field(hass: HomeAssistant, stored) -> None:
    p1, p2, p3 = _setup_patches()
    with p1, p2, p3:
        follower = await _set_up(hass, SB, "192.168.0.36", dict(stored))
        form = await _open_gateway_options(hass, follower.entry_id)
        assert form["type"] == FlowResultType.FORM
        assert _topology_fields(form) == {CONF_BUS_TOPOLOGY: TOPOLOGY_SHARED}  # the stored value, nothing else
        assert dict(follower.options) == stored  # opening the form changes nothing


@pytest.mark.parametrize(
    "stored",
    [{}, {CONF_BUS_TOPOLOGY: TOPOLOGY_STANDALONE, CONF_GATEWAY_ROLE: ROLE_PRIMARY}, {CONF_BUS_TOPOLOGY: TOPOLOGY_SHARED, CONF_GATEWAY_ROLE: ROLE_PRIMARY}],
    ids=["no topology stored", "standalone", "shared primary"],
)
async def test_a_last_entry_that_is_no_follower_gets_no_topology_field(hass: HomeAssistant, stored) -> None:
    """As before: one gateway alone has nothing to choose."""
    p1, p2, p3 = _setup_patches()
    with p1, p2, p3:
        entry = await _set_up(hass, SB, "192.168.0.36", dict(stored))
        form = await _open_gateway_options(hass, entry.entry_id)
        assert _topology_fields(form) == {}


async def test_with_another_gateway_the_form_has_all_four_fields(hass: HomeAssistant) -> None:
    """As before: a follower whose primary is still configured chooses among the gateways."""
    p1, p2, p3 = _setup_patches()
    with p1, p2, p3:
        await _set_up(hass, PRI, "192.168.0.35", {CONF_BUS_TOPOLOGY: TOPOLOGY_SHARED, CONF_GATEWAY_ROLE: ROLE_PRIMARY})
        follower = await _set_up(hass, SB, "192.168.0.36", dict(STANDBY))
        form = await _open_gateway_options(hass, follower.entry_id)
        assert set(_topology_fields(form)) == TOPOLOGY_FIELDS


@pytest.mark.parametrize("stored", [STANDBY, SECONDARY], ids=["standby", "secondary"])
async def test_saving_standalone_makes_the_last_entry_its_own_primary_and_clears_the_repair(
    hass: HomeAssistant, stored
) -> None:
    """The whole way: the primary is removed, the follower is flagged, its owner sets Standalone."""
    p1, p2, p3 = _setup_patches()
    with p1, p2, p3:
        primary = await _set_up(
            hass, PRI, "192.168.0.35", {CONF_BUS_TOPOLOGY: TOPOLOGY_SHARED, CONF_GATEWAY_ROLE: ROLE_PRIMARY}
        )
        follower = await _set_up(hass, SB, "192.168.0.36", dict(stored))
        issue_id = f"{ISSUE_PRIMARY_GATEWAY_MISSING}_{follower.entry_id}"
        await hass.config_entries.async_remove(primary.entry_id)
        await hass.async_block_till_done()
        assert _issue(hass, issue_id) is not None
        assert dict(follower.options) == stored  # flagged, not changed (#453)

        form = await _open_gateway_options(hass, follower.entry_id)
        result = await hass.config_entries.options.async_configure(
            form["flow_id"], {**OTHER_SETTINGS, CONF_BUS_TOPOLOGY: TOPOLOGY_STANDALONE}
        )
        await hass.async_block_till_done()  # the options changed: the entry is reloaded
        assert result["type"] == FlowResultType.CREATE_ENTRY
        assert follower.options[CONF_BUS_TOPOLOGY] == TOPOLOGY_STANDALONE
        assert follower.options[CONF_GATEWAY_ROLE] == ROLE_PRIMARY
        assert CONF_PRIMARY_GATEWAY not in follower.options and CONF_DELEGATED_WHOS not in follower.options
        assert _issue(hass, issue_id) is None
        gateway = follower.runtime_data.gateway
        assert gateway.is_primary is True and gateway.is_follower is False

        again = await _open_gateway_options(hass, follower.entry_id)
        assert _topology_fields(again) == {}  # a standalone gateway alone: nothing to choose


@pytest.mark.parametrize(
    "chosen",
    [{CONF_BUS_TOPOLOGY: TOPOLOGY_SHARED}, {}],
    ids=["shared kept", "field cleared"],
)
async def test_saving_shared_is_refused_on_the_field_the_form_has(hass: HomeAssistant, chosen) -> None:
    """The primary has no entry: the follower cannot stay one. The error was on a field not shown."""
    p1, p2, p3 = _setup_patches()
    with p1, p2, p3:
        follower = await _set_up(hass, SB, "192.168.0.36", dict(STANDBY))
        form = await _open_gateway_options(hass, follower.entry_id)
        with patch.object(hass.config_entries, "async_reload", return_value=True) as reload:
            result = await hass.config_entries.options.async_configure(form["flow_id"], {**OTHER_SETTINGS, **chosen})
            await hass.async_block_till_done()
        assert result["type"] == FlowResultType.FORM
        assert result["errors"] == {CONF_BUS_TOPOLOGY: "primary_gateway_not_found"}
        assert _topology_fields(result) == {CONF_BUS_TOPOLOGY: TOPOLOGY_SHARED}
        assert dict(follower.options) == STANDBY and not reload.called


async def test_with_another_gateway_the_error_stays_on_the_primary_gateway_field(hass: HomeAssistant) -> None:
    """As before: where the form shows the primary gateway field, its error is shown there."""
    p1, p2, p3 = _setup_patches()
    with p1, p2, p3:
        await _set_up(hass, PRI, "192.168.0.35", {})  # a standalone gateway: not a shared primary
        follower = await _set_up(hass, SB, "192.168.0.36", {})
        form = await _open_gateway_options(hass, follower.entry_id)
        result = await hass.config_entries.options.async_configure(
            form["flow_id"],
            {
                **OTHER_SETTINGS,
                CONF_BUS_TOPOLOGY: TOPOLOGY_SHARED,
                CONF_GATEWAY_ROLE: ROLE_STANDBY,
                CONF_PRIMARY_GATEWAY: PRI,
            },
        )
        assert result["type"] == FlowResultType.FORM
        assert result["errors"] == {CONF_PRIMARY_GATEWAY: "primary_gateway_not_shared_primary"}
