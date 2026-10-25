"""Tests for Issue #785: WHO=4 probe-less thermo actuator / towel-warmer switches.

Covers:
1. WHO=4 thermo actuator WHERE & trigger command grammar (``where_grammar.py``).
2. YAML ``switch_schema`` / ``config_schema`` & ``legacy_yaml.py`` non-collision for ``who: "4"``.
3. ``ThermoSwitchSubentryFlowHandler`` UI add/reconfigure flows & ``thermo_switch.py`` helpers.
4. ``MyHOMEThermoSwitch`` direct Dimension 20 writes, CEN+ / WHO=1 custom triggers,
   ``MyHomeServer1`` ``bt_thermo_manager`` revert detection, ``watch_zone_actuators``,
   state restoration across restarts, ``config_subentry_id`` linkage/removal,
   routed F422 isolation, and live ``MyHomeServer1`` 3.87.13 trace replay.
5. Coexistence with real thermostats on ``Z#N``, suppression & registry pruning of phantom
   Dimension-20-only ``climate`` entities, pre-discovery actuator caching, and ``light`` /
   ``button`` WHO=1 isolation when a ``WHO=4`` switch shares numeric address ``11``.
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import voluptuous as vol
from homeassistant.components.switch import SwitchDeviceClass
from homeassistant.const import CONF_MAC, CONF_NAME
from homeassistant.core import HomeAssistant, State
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.dispatcher import async_dispatcher_send
from OWNd.message import OWNHeatingEvent, OWNLightingEvent, OWNMessage
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    mock_restore_cache,
)

from custom_components.myhome.button import async_setup_entry as button_setup_entry
from custom_components.myhome.climate import (
    _calling_zones,
)
from custom_components.myhome.climate import (
    async_setup_entry as climate_setup_entry,
)
from custom_components.myhome.config_flow import MyhomeFlowHandler
from custom_components.myhome.config_flow_thermo_switch import (
    ThermoSwitchSubentryFlowHandler,
    load_thermo_switch_subentries,
)
from custom_components.myhome.const import (
    CONF_BUS_INTERFACE,
    CONF_TURN_OFF_COMMAND,
    CONF_TURN_ON_COMMAND,
    CONF_WHERE,
    CONF_WHO,
    DOMAIN,
    PLATFORMS,
    SUBENTRY_TYPE_THERMO_SWITCH,
)
from custom_components.myhome.data import MyHOMERuntimeData
from custom_components.myhome.legacy_yaml import _apply_yaml_platforms
from custom_components.myhome.light import async_setup_entry as light_setup_entry
from custom_components.myhome.switch import (
    MyHOMESwitch,
    MyHOMEThermoSwitch,
    _supports_config_subentry_id,
)
from custom_components.myhome.switch import (
    async_setup_entry as switch_setup_entry,
)
from custom_components.myhome.thermo_switch import has_thermo_switch_subentry
from custom_components.myhome.validate import config_schema, switch_schema
from custom_components.myhome.where_grammar import (
    parse_thermo_actuator_where,
    thermo_event_actuator,
    thermo_event_interface,
    thermo_event_zone,
    validate_thermo_trigger_command,
)

MAC = "00:03:50:78:50:01"
LIVE_TRACE_PATH = (
    Path(__file__).resolve().parent
    / "fixtures"
    / "traces"
    / "myhomeserver1_live_2026-10-25"
    / "myhome_trace_MyHomeServer1_live_2026-10-25.json"
)


def _mock_gateway(
    mac: str = MAC,
    *,
    actuator_refused: bool = False,
    with_watch: bool = True,
) -> MagicMock:
    gw = MagicMock()
    gw.name = "MHS1"
    gw.mac = mac
    gw.unique_id = mac
    gw.log_id = f"[MyHomeGateway - {mac}]"
    gw.model = "MyHomeServer1"
    gw.manufacturer = "BTicino S.p.A."
    gw.firmware = "3.87.13"
    gw.is_connected = True
    gw.has_central_unit = False
    gw.is_follower = False
    gw.primary_gateway_mac = None
    gw.delegated_whos = set()
    gw.delegated_away_whos = set()
    gw.sending_workers = []
    gw.availability_signal = f"myhome_gw_available_{mac}"
    gw.is_who_available = MagicMock(return_value=True)
    gw.actuator_request_refused = actuator_refused
    gw.send = AsyncMock(return_value=True)
    gw.send_status_request = AsyncMock(return_value=True)
    gw.close_listener = AsyncMock(return_value=True)
    if with_watch:
        unwatch = MagicMock()
        gw.watch_zone_actuators = MagicMock(return_value=unwatch)
    else:
        del gw.watch_zone_actuators
    return gw


def _mock_entry(
    hass: HomeAssistant,
    gateway: MagicMock | None = None,
    *,
    platforms: dict | None = None,
    subentries: list[dict] | None = None,
) -> MockConfigEntry:
    gw = gateway or _mock_gateway()
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={CONF_MAC: gw.mac, "host": "192.0.2.78", "port": 20000},
        unique_id=gw.mac,
        subentries_data=subentries or [],
    )
    entry.add_to_hass(hass)
    plat_dict = {p: {} for p in PLATFORMS}
    if platforms:
        for k, v in platforms.items():
            plat_dict[k] = dict(v)
    entry.runtime_data = MyHOMERuntimeData(
        gateway=gw,
        platforms=plat_dict,
        entities={p: {} for p in PLATFORMS},
    )
    return entry


# ---------------------------------------------------------------------------
# 1. Grammar & Command Validation Tests (where_grammar.py)
# ---------------------------------------------------------------------------


def test_parse_thermo_actuator_where_valid_and_invalid() -> None:
    """Verify WHO=4 thermo actuator WHERE parsing, normalization, and rejections."""
    assert parse_thermo_actuator_where("7#1") == ("7#1", "7", "1", None)
    assert parse_thermo_actuator_where("07") == ("7", "7", None, None)
    assert parse_thermo_actuator_where(12) == ("12", "12", None, None)
    assert parse_thermo_actuator_where("4-99#9") == ("99#9", "99", "9", None)
    assert parse_thermo_actuator_where("7#1#4#1") == ("7#1", "7", "1", "01")
    assert parse_thermo_actuator_where("7#2", "0") == ("7#2", "7", "2", "00")
    assert parse_thermo_actuator_where("7#1#4#05", "5") == ("7#1", "7", "1", "05")

    for bad_where in (
        None,
        True,
        False,
        "",
        "0",
        "#0",
        "#7",
        "0#1",
        "100",
        "105",
        "007",
        "7#0",
        "7#10",
        "7#1#4#16",
        "abc",
    ):
        with pytest.raises(ValueError):
            parse_thermo_actuator_where(bad_where)  # type: ignore[arg-type]

    for bad_if in (True, False, "16", "-1", "bus1"):
        with pytest.raises(ValueError):
            parse_thermo_actuator_where("7#1", bad_if)  # type: ignore[arg-type]

    with pytest.raises(ValueError, match="Conflicting F422"):
        parse_thermo_actuator_where("7#1#4#01", "02")


def test_thermo_event_helpers() -> None:
    """Verify thermo_event_interface, thermo_event_zone, and thermo_event_actuator."""
    local_evt = OWNHeatingEvent("*#4*7#2*20*1##")
    assert thermo_event_interface(local_evt) is None
    assert thermo_event_zone(local_evt) == "7"
    assert thermo_event_actuator(local_evt) == "2"

    routed_act_evt = OWNHeatingEvent("*#4*7#3#4#1*20*1##")
    assert thermo_event_interface(routed_act_evt) == "01"
    assert thermo_event_zone(routed_act_evt) == "7"
    assert thermo_event_actuator(routed_act_evt) == "3"

    routed_zone_evt = OWNHeatingEvent("*#4*7#4#02*0*0215##")
    assert thermo_event_interface(routed_zone_evt) == "02"
    assert thermo_event_zone(routed_zone_evt) == "7"
    assert thermo_event_actuator(routed_zone_evt) == "1"

    # Explicit interface attribute and non-digit interface fallback
    fake_if = MagicMock(interface="3", _where_param=[], where_param=[])
    assert thermo_event_interface(fake_if) == "03"
    fake_alpha_if = MagicMock(interface="busA", _where_param=[], where_param=[])
    assert thermo_event_interface(fake_alpha_if) == "busA"

    # Explicit .actuator attribute and fallback tail actuator when .actuator is absent
    assert thermo_event_actuator(MagicMock(actuator=2)) == "2"
    fake_tail_act = MagicMock(actuator=None, where_param=["4_no"], _where_param=[])
    assert thermo_event_actuator(fake_tail_act) == "1"
    fake_tail_digit = MagicMock(actuator=None, where_param=["5"], _where_param=[])
    assert thermo_event_actuator(fake_tail_digit) == "5"

    # Pumps, probes, and central unit return None for thermo_event_zone
    assert thermo_event_zone(OWNHeatingEvent("*#4*0#1*20*1##")) is None
    assert thermo_event_zone(OWNHeatingEvent("*#4*105*0*0210##")) is None
    assert thermo_event_zone(OWNHeatingEvent("*4*1*#0##")) is None


def test_validate_thermo_trigger_command() -> None:
    """Verify custom OpenWebNet command frame validation for thermo switches."""
    assert validate_thermo_trigger_command(" *25*21#1*21## ") == "*25*21#1*21##"
    assert validate_thermo_trigger_command("*25*21#2*21##") == "*25*21#2*21##"
    assert validate_thermo_trigger_command("*1*1*09##") == "*1*1*09##"
    assert validate_thermo_trigger_command("*#4*7#1*#20*1##") == "*#4*7#1*#20*1##"

    for bad_cmd in (
        None,
        123,
        "",
        "25*21#1*21##",
        "*25*21#1*21",
        "*#*1##",
        "*#*0##",
        "*#4*7#1*20##",
        "*#1*09##",
        "*#4*7*#22*1##",
        "*invalid_frame##",
    ):
        with pytest.raises(ValueError):
            validate_thermo_trigger_command(bad_cmd)  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# 2. YAML Schema & legacy_yaml.py Non-Collision Tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_yaml_switch_schema_who1_and_who4_without_key_collision(
    hass: HomeAssistant,
) -> None:
    """Verify switch_schema, config_schema, and _apply_yaml_platforms isolate WHO=1 vs WHO=4."""
    parsed = config_schema(
        {
            "gw": {
                "mac": MAC,
                "switch": {
                    "socket": {"where": "11", "name": "Hall Socket"},
                    "towel_same_num": {
                        "who": 4,
                        "where": "11",
                        "name": "Zone 11 Towel",
                    },
                    "towel": {
                        "who": 4,
                        "where": "07#1",
                        "name": "Bathroom Towel Warmer",
                        "turn_on_command": "*25*21#1*21##",
                        "turn_off_command": "*25*21#2*21##",
                    },
                    "routed_towel": {
                        "who": "4",
                        "where": "8#2",
                        "interface": "1",
                        "name": "Annex Towel Warmer",
                    },
                },
            }
        }
    )
    gw_platforms = parsed[MAC]["platforms"]
    assert "1-11" in gw_platforms["switch"]
    assert "4-11" in gw_platforms["switch"]
    assert "4-7#1" in gw_platforms["switch"]
    assert gw_platforms["switch"]["4-7#1"][CONF_TURN_ON_COMMAND] == "*25*21#1*21##"
    assert "4-8#2#4#01" in gw_platforms["switch"]
    # Only WHO=1 switch is copied into button platform
    assert set(gw_platforms["button"].keys()) == {"1-11"}

    # Verify _apply_yaml_platforms keeps WHO=1 '11' and WHO=4 '4-11' completely distinct
    configured: dict[str, dict] = {p: {} for p in PLATFORMS}
    _apply_yaml_platforms(gw_platforms, configured)
    assert configured["switch"]["1-11"][CONF_WHO] == "1"
    assert configured["switch"]["11"][CONF_WHO] == "1"
    assert configured["switch"]["4-11"][CONF_WHO] == "4"
    assert configured["switch"]["4-8#2#4#01"][CONF_WHO] == "4"

    entry = _mock_entry(hass, platforms=configured)
    added: list = []
    await switch_setup_entry(hass, entry, added.extend)
    assert [e._where for e in added if isinstance(e, MyHOMESwitch)] == ["11"]
    assert sorted(e._full_where for e in added if isinstance(e, MyHOMEThermoSwitch)) == [
        "11",
        "7#1",
        "8#2#4#01",
    ]

    # WHO=1 rejects turn_on_command
    with pytest.raises(vol.Invalid, match="only supported on WHO=4"):
        switch_schema(
            {"s": {"who": "1", "where": "11", "name": "S", "turn_on_command": "*1*1*11##"}}
        )

    # WHO=4 invalid WHERE
    with pytest.raises(vol.Invalid):
        switch_schema({"t": {"who": "4", "where": "105", "name": "Probe"}})

    # WHO=4 unpaired trigger command
    with pytest.raises(vol.Invalid, match="both be provided together"):
        switch_schema(
            {"t": {"who": "4", "where": "7#1", "name": "T", "turn_on_command": "*25*21#1*21##"}}
        )

    # WHO=4 invalid trigger command
    with pytest.raises(vol.Invalid):
        switch_schema(
            {
                "t": {
                    "who": "4",
                    "where": "7#1",
                    "name": "T",
                    "turn_on_command": "*#4*7#1*20##",
                    "turn_off_command": "*25*21#2*21##",
                }
            }
        )


# ---------------------------------------------------------------------------
# 3. ConfigSubentryFlow & Subentry Loader Tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_thermo_switch_subentry_flow_user_and_reconfigure(
    hass: HomeAssistant,
) -> None:
    """Verify ThermoSwitchSubentryFlowHandler add, reconfigure, and error branches."""
    gw = _mock_gateway()
    entry = _mock_entry(
        hass,
        gw,
        platforms={
            "switch": {
                "1-11": {"who": "1", "where": "11", "name": "Wall Switch"},
                "4-bad": {"who": "4", "where": "999", "name": "Invalid"},
                "4-9#1": {"who": "4", "where": "9#1", "name": "YAML Towel"},
            }
        },
        subentries=[
            {
                "subentry_type": "other",
                "subentry_id": "sub_other",
                "title": "Other",
                "unique_id": None,
                "data": {},
            },
            {
                "subentry_type": SUBENTRY_TYPE_THERMO_SWITCH,
                "subentry_id": "sub_corrupt",
                "title": "Corrupt",
                "unique_id": None,
                "data": {"where": "999"},
            },
            {
                "subentry_type": SUBENTRY_TYPE_THERMO_SWITCH,
                "subentry_id": "sub_existing",
                "title": "Existing (WHO 4 / WHERE 7#1#4#01)",
                "unique_id": None,
                "data": {
                    "who": "4",
                    "where": "7#1",
                    "interface": "01",
                    "name": "Existing",
                },
            },
        ],
    )

    supported = MyhomeFlowHandler.async_get_supported_subentry_types(entry)
    assert supported[SUBENTRY_TYPE_THERMO_SWITCH] is ThermoSwitchSubentryFlowHandler

    flow = ThermoSwitchSubentryFlowHandler()
    flow.hass = hass
    flow.handler = (entry.entry_id, SUBENTRY_TYPE_THERMO_SWITCH)
    flow.context = {"source": "user"}

    res = await flow.async_step_user(None)
    assert res["type"] == FlowResultType.FORM
    assert res["step_id"] == "user"

    # Empty / bool where
    res_empty = await flow.async_step_user({CONF_WHERE: ""})
    assert res_empty["errors"][CONF_WHERE] == "where_required"
    res_bool = await flow.async_step_user({CONF_WHERE: True, CONF_NAME: True})
    assert res_bool["errors"][CONF_WHERE] == "where_required"

    # Invalid where
    res_bad_where = await flow.async_step_user({CONF_WHERE: "0#1"})
    assert res_bad_where["errors"][CONF_WHERE] == "invalid_where"

    # Bool commands
    res_bool_cmd = await flow.async_step_user(
        {CONF_WHERE: "6#1", CONF_TURN_ON_COMMAND: True, CONF_TURN_OFF_COMMAND: False}
    )
    assert res_bool_cmd["errors"][CONF_TURN_ON_COMMAND] == "invalid_command"
    assert res_bool_cmd["errors"][CONF_TURN_OFF_COMMAND] == "invalid_command"

    # Unpaired commands (missing off & missing on)
    res_unpaired_off = await flow.async_step_user(
        {CONF_WHERE: "6#1", CONF_TURN_ON_COMMAND: "*25*21#1*21##"}
    )
    assert res_unpaired_off["errors"][CONF_TURN_OFF_COMMAND] == "paired_commands_required"
    res_unpaired_on = await flow.async_step_user(
        {CONF_WHERE: "6#1", CONF_TURN_OFF_COMMAND: "*25*21#2*21##"}
    )
    assert res_unpaired_on["errors"][CONF_TURN_ON_COMMAND] == "paired_commands_required"

    # Invalid ON & OFF frames
    res_bad_cmds = await flow.async_step_user(
        {
            CONF_WHERE: "6#1",
            CONF_TURN_ON_COMMAND: "*#*1##",
            CONF_TURN_OFF_COMMAND: "*#4*6#1*20##",
        }
    )
    assert res_bad_cmds["errors"][CONF_TURN_ON_COMMAND] == "invalid_command"
    assert res_bad_cmds["errors"][CONF_TURN_OFF_COMMAND] == "invalid_command"

    # Duplicate against existing subentry & against YAML runtime switch
    res_dup_sub = await flow.async_step_user({CONF_WHERE: "7#1#4#01"})
    assert res_dup_sub["errors"]["base"] == "already_configured"
    res_dup_yaml = await flow.async_step_user({CONF_WHERE: "9#1"})
    assert res_dup_yaml["errors"]["base"] == "already_configured"

    # Successful unnamed routed creation + named CEN+ creation
    with patch.object(hass.config_entries, "async_schedule_reload") as mock_reload:
        res_ok_default = await flow.async_step_user({CONF_WHERE: "8#4#1"})
        assert res_ok_default["type"] == FlowResultType.CREATE_ENTRY
        assert res_ok_default["title"] == "Thermo Switch (WHO 4 / WHERE 8#4#01)"
        assert res_ok_default["data"] == {
            CONF_WHO: "4",
            CONF_WHERE: "8",
            CONF_BUS_INTERFACE: "01",
        }

        res_ok_named = await flow.async_step_user(
            {
                CONF_WHERE: "07#1",
                CONF_NAME: "Master Bath Towel",
                CONF_TURN_ON_COMMAND: "*25*21#1*21##",
                CONF_TURN_OFF_COMMAND: "*25*21#2*21##",
            }
        )
        assert res_ok_named["type"] == FlowResultType.CREATE_ENTRY
        assert res_ok_named["title"] == "Master Bath Towel (WHO 4 / WHERE 7#1)"
        assert res_ok_named["data"] == {
            CONF_WHO: "4",
            CONF_WHERE: "7#1",
            CONF_NAME: "Master Bath Towel",
            CONF_TURN_ON_COMMAND: "*25*21#1*21##",
            CONF_TURN_OFF_COMMAND: "*25*21#2*21##",
        }
        assert mock_reload.call_count == 2

    # Reconfigure step (initial form, validation error preserving input, and success)
    load_thermo_switch_subentries(entry, entry.runtime_data.platforms)
    reconf_flow = ThermoSwitchSubentryFlowHandler()
    reconf_flow.hass = hass
    reconf_flow.handler = (entry.entry_id, SUBENTRY_TYPE_THERMO_SWITCH)
    reconf_flow.context = {"source": "reconfigure", "subentry_id": "sub_existing"}

    reconf_form = await reconf_flow.async_step_reconfigure(None)
    assert reconf_form["type"] == FlowResultType.FORM
    assert reconf_form["step_id"] == "reconfigure"

    reconf_err = await reconf_flow.async_step_reconfigure({CONF_WHERE: ""})
    assert reconf_err["type"] == FlowResultType.FORM
    assert reconf_err["errors"][CONF_WHERE] == "where_required"

    with patch.object(hass.config_entries, "async_schedule_reload") as mock_reload:
        reconf_ok = await reconf_flow.async_step_reconfigure(
            {CONF_WHERE: "7#1#4#01", CONF_NAME: "Updated Towel"}
        )
        assert reconf_ok["type"] == FlowResultType.ABORT
        assert reconf_ok["reason"] == "reconfigure_successful"
        mock_reload.assert_called_once_with(entry.entry_id)


def test_load_and_has_thermo_switch_subentry_edge_cases() -> None:
    """Verify load_thermo_switch_subentries and has_thermo_switch_subentry edge cases."""
    platforms: dict = {"switch": {"4-5#1": {"who": "4", "where": "5#1", "name": "YAML"}}}

    # Non-mapping subentries attribute
    bad_entry = MagicMock(subentries=123)
    load_thermo_switch_subentries(bad_entry, platforms)
    assert list(platforms["switch"].keys()) == ["4-5#1"]
    assert has_thermo_switch_subentry(bad_entry, "5#1", None) is False

    # Invalid where argument to has_thermo_switch_subentry
    assert has_thermo_switch_subentry(MagicMock(subentries={}), "0", None) is False

    # Subentries with other types, invalid data, unpaired commands, or colliding with YAML
    sub_other = MagicMock(subentry_type="other", data={"where": "7#1"})
    sub_yaml_clash = MagicMock(
        subentry_type=SUBENTRY_TYPE_THERMO_SWITCH,
        data={"where": "5#1", "name": "Sub Clash"},
    )
    sub_unpaired_cmd = MagicMock(
        subentry_type=SUBENTRY_TYPE_THERMO_SWITCH,
        data={"where": "6#1", "turn_on_command": "*25*21#1*21##"},
    )
    sub_corrupt_where = MagicMock(
        subentry_type=SUBENTRY_TYPE_THERMO_SWITCH,
        data={"where": "999"},
    )
    sub_valid = MagicMock(
        subentry_type=SUBENTRY_TYPE_THERMO_SWITCH,
        data={
            "where": "10#2",
            "interface": "1",
            "turn_on_command": "*1*1*09##",
            "turn_off_command": "*1*0*09##",
        },
    )
    entry = MagicMock(
        subentries={
            "s0": sub_other,
            "s1": sub_yaml_clash,
            "s2": sub_unpaired_cmd,
            "s_bad": sub_corrupt_where,
            "s3": sub_valid,
        }
    )
    load_thermo_switch_subentries(entry, platforms)
    assert platforms["switch"]["4-5#1"]["name"] == "YAML"
    assert "4-6#1" not in platforms["switch"]
    assert platforms["switch"]["4-10#2#4#01"][CONF_TURN_ON_COMMAND] == "*1*1*09##"
    assert platforms["switch"]["4-10#2#4#01"][CONF_NAME] == "Thermo Actuator 10#2I01"

    assert has_thermo_switch_subentry(entry, "6#1", None) is True
    assert has_thermo_switch_subentry(entry, "10#2", "01") is True
    assert has_thermo_switch_subentry(entry, "10#2", "01", subentry_id="s3") is True
    assert has_thermo_switch_subentry(entry, "10#2", "01", subentry_id="wrong") is False
    assert has_thermo_switch_subentry(entry, "99#1", None, subentry_id="s_bad") is True
    assert has_thermo_switch_subentry(entry, "99#1", None) is False


# ---------------------------------------------------------------------------
# 4. Switch Entity, State Restore, Subentry Linkage & Live Trace Tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_thermo_switch_direct_commands_revert_warning_and_custom_triggers(
    hass: HomeAssistant, caplog: pytest.LogCaptureFixture
) -> None:
    """Verify direct #20 control, MyHomeServer1 revert warning, CEN+ triggers, and timers."""
    gw = _mock_gateway()
    entry = _mock_entry(
        hass,
        gw,
        platforms={
            "switch": {
                "1-11": {
                    "who": "1",
                    "where": "11",
                    "name": "Hall Relay",
                },
                "4-7#1": {
                    "who": "4",
                    "where": "7#1",
                    "name": "Guest Towel Warmer",
                    "icon": "mdi:radiator-off",
                    "icon_on": "mdi:radiator",
                    "class": "outlet",
                },
                "4-8": {
                    "who": "4",
                    "where": "8",
                    "name": "Master Towel Warmer",
                    "turn_on_command": "*25*21#1*21##",
                    "turn_off_command": "*25*21#2*21##",
                },
                "4-7#1#4#01": {
                    "who": "4",
                    "where": "7#1",
                    "interface": "01",
                    "name": "Routed Towel Warmer",
                },
            }
        },
    )

    # Seed a stale WHO=4 switch registry entry
    ent_reg = er.async_get(hass)
    stale_reg = ent_reg.async_get_or_create(
        "switch", DOMAIN, f"{MAC}-4-99#1", config_entry=entry
    )

    added: list = []
    await switch_setup_entry(hass, entry, added.extend)

    # Stale unconfigured WHO=4 switch was pruned from the entity registry
    assert ent_reg.async_get(stale_reg.entity_id) is None

    who1_switches = [e for e in added if isinstance(e, MyHOMESwitch)]
    thermo_switches = {e._full_where: e for e in added if isinstance(e, MyHOMEThermoSwitch)}
    assert len(who1_switches) == 1
    assert set(thermo_switches.keys()) == {"7#1", "8", "7#1#4#01"}

    for ent in added:
        ent.hass = hass
        ent.entity_id = f"switch.test_{ent._device_id.replace('#', '_')}"
        ent.async_write_ha_state = MagicMock()
        await ent.async_added_to_hass()

    # watch_zone_actuators subscribed for each WHO=4 thermo switch
    watched = {c.args[0] for c in gw.watch_zone_actuators.call_args_list}
    assert watched == {"7#1", "8", "7#1#4#01"}

    sw_direct = thermo_switches["7#1"]
    sw_cen = thermo_switches["8"]
    sw_routed = thermo_switches["7#1#4#01"]

    assert sw_direct.device_class == SwitchDeviceClass.OUTLET
    assert sw_direct.icon == "mdi:radiator-off"
    assert sw_direct.extra_state_attributes == {"zone": 7, "actuator": 1}
    assert sw_routed.extra_state_attributes == {"zone": 7, "actuator": 1, "Int": "01"}

    # Explicit async_update sends *#4*7#1*20## unless actuator_request_refused
    gw.send_status_request.reset_mock()
    await sw_direct.async_update()
    assert [str(c.args[0]) for c in gw.send_status_request.call_args_list] == [
        "*#4*7#1*20##"
    ]
    gw.actuator_request_refused = True
    await sw_direct.async_update()
    assert len(gw.send_status_request.call_args_list) == 1
    gw.actuator_request_refused = False
    gw.interface_zone_request_refused = True
    await sw_routed.async_update()
    assert len(gw.send_status_request.call_args_list) == 1

    # Direct turn_on -> *#4*7#1*#20*1##, followed by MyHomeServer1 BNTR auto-revert (< 2.0s)
    caplog.clear()
    await sw_direct.async_turn_on()
    assert str(gw.send.call_args.args[0]) == "*#4*7#1*#20*1##"

    async_dispatcher_send(hass, f"myhome_message_{MAC}", OWNHeatingEvent("*#4*7#1*20*1##"))
    assert sw_direct.is_on is True
    assert sw_direct.icon == "mdi:radiator"
    assert sw_routed.is_on is None  # Routed 7#1#4#01 isolated from local 7#1!

    async_dispatcher_send(hass, f"myhome_message_{MAC}", OWNHeatingEvent("*#4*7#1*20*0##"))
    assert sw_direct.is_on is False
    assert sw_direct.icon == "mdi:radiator-off"
    assert "bt_thermo_manager locks the relay state" in caplog.text

    # Direct turn_off -> *#4*7#1*#20*0##, and expired revert window (> 2.0s) does not warn again
    await sw_direct.async_turn_off()
    assert str(gw.send.call_args.args[0]) == "*#4*7#1*#20*0##"
    sw_direct._pending_direct_write = (False, -100.0, True)
    async_dispatcher_send(hass, f"myhome_message_{MAC}", OWNHeatingEvent("*#4*7#1*20*1##"))
    assert sw_direct._pending_direct_write is None

    # Routed frame updates ONLY routed switch
    async_dispatcher_send(
        hass, f"myhome_message_{MAC}", OWNHeatingEvent("*#4*7#1#4#01*20*1##")
    )
    assert sw_routed.is_on is True

    # Ignored / non-matching frames on handle_event
    sw_direct.handle_event(MagicMock(is_translation=True))
    sw_direct.handle_event(OWNHeatingEvent("*#4*7#1#4#01*20*0##"))
    sw_direct.handle_event(OWNHeatingEvent("*#4*9#1*20*0##"))
    sw_direct.handle_event(OWNHeatingEvent("*#4*7*19*0*0##"))
    assert sw_direct.is_on is True
    # Actuator 2 of zone 7 does not affect 7#1
    async_dispatcher_send(hass, f"myhome_message_{MAC}", OWNHeatingEvent("*#4*7#2*20*0##"))
    assert sw_direct.is_on is True
    # Temperature / pump / probe frames ignored cleanly
    async_dispatcher_send(hass, f"myhome_message_{MAC}", OWNHeatingEvent("*#4*7*0*0210##"))
    async_dispatcher_send(hass, f"myhome_message_{MAC}", OWNHeatingEvent("*#4*0#1*20*0##"))
    async_dispatcher_send(hass, f"myhome_message_{MAC}", OWNHeatingEvent("*#4*107*0*0210##"))
    fake_action = MagicMock(
        is_translation=False,
        interface=None,
        where="7",
        where_param=[],
        zone=7,
        message_type="action",
        dimension=99,
    )
    sw_direct.handle_event(fake_action)
    sw_direct.handle_event(
        MagicMock(
            is_translation=False,
            interface=None,
            where="9",
            where_param=[],
            zone=9,
            message_type="action",
            dimension=20,
        )
    )

    # Custom CEN+ trigger switch (zone-wide WHERE="8"): tracks multi-actuator + Dimension 19
    gw.send.reset_mock()
    await sw_cen.async_turn_on()
    assert str(gw.send.call_args.args[0]) == "*25*21#1*21##"
    async_dispatcher_send(hass, f"myhome_message_{MAC}", OWNHeatingEvent("*#4*8#1*20*1##"))
    async_dispatcher_send(hass, f"myhome_message_{MAC}", OWNHeatingEvent("*#4*8#2*20*0##"))
    assert sw_cen.is_on is True
    assert sw_cen.extra_state_attributes == {
        "zone": 8,
        "actuators": {"1": "on", "2": "off"},
    }

    await sw_cen.async_turn_off()
    assert str(gw.send.call_args.args[0]) == "*25*21#2*21##"
    # Dimension 19 zone-off clears all actuators on zone-wide thermo switch
    async_dispatcher_send(hass, f"myhome_message_{MAC}", OWNHeatingEvent("*#4*8*19*0*0##"))
    assert sw_cen.is_on is False
    assert sw_cen.extra_state_attributes == {
        "zone": 8,
        "actuators": {"1": "off", "2": "off"},
    }

    # Hardware bus timer rejected cleanly with translated HomeAssistantError
    with pytest.raises(HomeAssistantError):
        await sw_direct.async_turn_on_timed(duration=30)
    with pytest.raises(HomeAssistantError):
        await sw_direct.async_turn_on(duration=30)


@pytest.mark.asyncio
async def test_thermo_switch_restore_state_and_icons(hass: HomeAssistant) -> None:
    """Verify MyHOMEThermoSwitch restores relay state, actuators map, and custom icons."""
    gw = _mock_gateway(actuator_refused=True)
    entry = _mock_entry(
        hass,
        gw,
        platforms={
            "switch": {
                "4-8": {
                    "who": "4",
                    "where": "8",
                    "name": "Zone 8 Towel",
                    "icon": "mdi:radiator-off",
                    "icon_on": "mdi:radiator",
                },
                "4-7#1": {
                    "who": "4",
                    "where": "7#1",
                    "name": "Zone 7_1 Towel",
                    "icon": "mdi:radiator-off",
                    "icon_on": "mdi:radiator",
                },
            }
        },
    )

    added: list = []
    await switch_setup_entry(hass, entry, added.extend)
    by_where = {e._where: e for e in added}

    sw8 = by_where["8"]
    sw8.hass = hass
    sw8.entity_id = "switch.zone_8_towel"
    sw8.async_write_ha_state = MagicMock()
    sw8.async_get_last_state = AsyncMock(
        return_value=State(
            "switch.zone_8_towel",
            "on",
            {"actuators": {"1": "on", "2": "off", "3": "unknown"}},
        )
    )
    await sw8.async_added_to_hass()
    assert sw8.is_on is True
    assert sw8.icon == "mdi:radiator"
    assert sw8.extra_state_attributes == {
        "zone": 8,
        "actuators": {"1": "on", "2": "off"},
    }

    sw7 = by_where["7#1"]
    sw7.hass = hass
    sw7.entity_id = "switch.zone_7_1_towel"
    sw7.async_write_ha_state = MagicMock()
    sw7.async_get_last_state = AsyncMock(
        return_value=State("switch.zone_7_1_towel", "off", {})
    )
    await sw7.async_added_to_hass()
    assert sw7.is_on is False
    assert sw7.icon == "mdi:radiator-off"


@pytest.mark.asyncio
async def test_thermo_switch_subentry_linkage_removal_and_invalid_preservation(
    hass: HomeAssistant,
) -> None:
    """Verify config_subentry_id linkage, immediate removal, and invalid subentry preservation."""
    assert _supports_config_subentry_id(123) is False
    assert _supports_config_subentry_id([].extend) is False
    assert (
        _supports_config_subentry_id(lambda ents, config_subentry_id=None: None)
        is True
    )

    gw = _mock_gateway()
    entry = _mock_entry(
        hass,
        gw,
        platforms={"switch": {"1-12": {"who": "1", "where": "12", "name": "Relay 12"}}},
        subentries=[
            {
                "subentry_type": SUBENTRY_TYPE_THERMO_SWITCH,
                "subentry_id": "sub_towel_7_1",
                "title": "Bath Towel (WHO 4 / WHERE 7#1)",
                "unique_id": None,
                "data": {"who": "4", "where": "7#1", "name": "Bath Towel"},
            },
            {
                "subentry_type": SUBENTRY_TYPE_THERMO_SWITCH,
                "subentry_id": "sub_temp_invalid",
                "title": "Temporarily Invalid",
                "unique_id": None,
                "data": {"who": "4", "where": "999"},
            },
        ],
    )

    ent_reg = er.async_get(hass)
    preserved_reg = ent_reg.async_get_or_create(
        "switch",
        DOMAIN,
        f"{MAC}-4-15#1",
        config_entry=entry,
        config_subentry_id="sub_temp_invalid",
    )

    calls: list[tuple[list[str], str | None]] = []

    def subentry_aware_add(
        entities: list,
        update_before_add: bool = False,
        *,
        config_subentry_id: str | None = None,
    ) -> None:
        calls.append(([e._device_id for e in entities], config_subentry_id))

    await switch_setup_entry(hass, entry, subentry_aware_add)  # type: ignore[arg-type]
    assert (["12"], None) in calls
    assert (["7#1"], "sub_towel_7_1") in calls
    # Registry entry belonging to temporarily invalid subentry was NOT wiped!
    assert ent_reg.async_get(preserved_reg.entity_id) is not None


@pytest.mark.asyncio
async def test_thermo_switch_registry_restore_and_no_watch_fallback(
    hass: HomeAssistant,
) -> None:
    """Verify WHO=1/WHO=4 registry restore partitioning and fallback without watch_zone_actuators."""
    gw = _mock_gateway(with_watch=False)
    entry = _mock_entry(
        hass,
        gw,
        platforms={
            "switch": {
                "1-12": {"who": "1", "where": "12", "name": "Relay 12"},
                "4-7#1": {"who": "4", "where": "7#1"},
            }
        },
    )
    ent_reg = er.async_get(hass)
    ent_reg.async_get_or_create("switch", DOMAIN, f"{MAC}-1-12", config_entry=entry)
    ent_reg.async_get_or_create("switch", DOMAIN, f"{MAC}-4-7#1", config_entry=entry)
    # Invalid WHO=4 registry entry whose cfg was manually injected -> build_thermo returns None
    entry.runtime_data.platforms["switch"]["4-0"] = {"who": "4", "where": "0"}
    ent_reg.async_get_or_create("switch", DOMAIN, f"{MAC}-4-0", config_entry=entry)

    added: list = []
    await switch_setup_entry(hass, entry, added.extend)
    thermo = [e for e in added if isinstance(e, MyHOMEThermoSwitch)]
    assert len(thermo) == 1
    assert thermo[0]._device_name == "Thermo Actuator 7#1"
    assert thermo[0]._poll_on_add is True

    # Guard branches when runtime is None or switch platform missing
    empty_entry = MockConfigEntry(domain=DOMAIN, data={CONF_MAC: MAC})
    empty_entry.runtime_data = None
    assert await switch_setup_entry(hass, empty_entry, added.extend) is True
    no_sw_entry = _mock_entry(hass, gw)
    del no_sw_entry.runtime_data.platforms["switch"]
    assert await switch_setup_entry(hass, no_sw_entry, added.extend) is True


@pytest.mark.asyncio
async def test_live_myhomeserver1_trace_dimension_20_frames(
    hass: HomeAssistant,
) -> None:
    """Replay live MyHomeServer1 3.87.13 Dimension 20 frames through switch & climate."""
    trace = json.loads(LIVE_TRACE_PATH.read_text(encoding="utf-8"))
    dim20_events = [
        row["frame"]
        for row in trace["rows"]
        if row.get("dir") == "rx"
        and row.get("session") == "evt"
        and isinstance(row.get("frame"), str)
        and row["frame"].startswith("*#4*")
        and "*20*" in row["frame"]
    ]
    assert len(dim20_events) >= 40

    gw = _mock_gateway()
    entry = _mock_entry(
        hass,
        gw,
        subentries=[
            {
                "subentry_type": SUBENTRY_TYPE_THERMO_SWITCH,
                "subentry_id": "sub_z35_a1",
                "title": "Zone 35 Towel",
                "unique_id": None,
                "data": {"who": "4", "where": "35#1", "name": "Zone 35 Towel"},
            }
        ],
    )

    added_switches: list = []
    added_climates: list = []
    await switch_setup_entry(hass, entry, added_switches.extend)
    await climate_setup_entry(hass, entry, added_climates.extend)

    for sw in added_switches:
        sw.hass = hass
        sw.entity_id = "switch.zone_35_towel"
        sw.async_write_ha_state = MagicMock()
        await sw.async_added_to_hass()

    assert added_switches[0].is_on is None
    for raw in dim20_events:
        msg = OWNMessage.parse(raw)
        if isinstance(msg, OWNHeatingEvent):
            async_dispatcher_send(hass, f"myhome_message_{MAC}", msg)

    # Zero phantom climate entities created for the 29 binary ON/OFF actuator zones
    # or 4 pumps; only the 5 genuine fan-coil zones (Z#2 value >= 5) discover as climate!
    assert sorted(c._where for c in added_climates) == ["47", "48", "57", "60", "70"]
    assert len(added_switches) == 1
    assert added_switches[0].is_on is False
    assert added_switches[0].extra_state_attributes == {"zone": 35, "actuator": 1}


# ---------------------------------------------------------------------------
# 5. Cross-Platform Coexistence & Isolation Tests (climate.py, light.py, button.py)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_climate_coexists_with_per_actuator_switch_and_prunes_ghosts(
    hass: HomeAssistant,
) -> None:
    """Verify climate preserves real Z#N zones, suppresses whole-zone Z, and prunes ghosts."""
    gw = _mock_gateway()
    entry = _mock_entry(
        hass,
        gw,
        platforms={
            "switch": {
                "1-11": {"who": "1", "where": "11", "name": "Relay"},
                "4-bad": {"who": "4", "where": "999", "name": "Bad"},
                "4-8": {"who": "4", "where": "8", "name": "Whole Zone 8 Towel"},
                "4-7#1": {"who": "4", "where": "7#1", "name": "Probe-less Towel 7#1"},
                "4-3#2": {"who": "4", "where": "3#2", "name": "Aux Towel on Thermostat 3"},
                "4-5#1": {"who": "4", "where": "5#1", "name": "Dual Zone 5"},
            },
            "climate": {
                "4-5": {"who": "4", "zone": "5", "name": "Explicit Climate 5"},
            },
        },
    )

    ent_reg = er.async_get(hass)
    # 1. Ghost climate on whole-zone thermo switch 8 -> must be pruned
    ghost_z8 = ent_reg.async_get_or_create(
        "climate", DOMAIN, f"{MAC}-4-8", config_entry=entry
    )
    # 2. Ghost climate on probe-less per-actuator 7#1 (unknown state, no temp) -> must be pruned
    ghost_z7 = ent_reg.async_get_or_create(
        "climate", DOMAIN, f"{MAC}-4-7", config_entry=entry
    )
    hass.states.async_set(ghost_z7.entity_id, "unknown", {})
    # 3. Real auto-discovered thermostat on Zone 3 alongside auxiliary switch 3#2 -> MUST BE KEPT!
    real_z3 = ent_reg.async_get_or_create(
        "climate", DOMAIN, f"{MAC}-4-3", config_entry=entry
    )
    mock_restore_cache(
        hass,
        [State(real_z3.entity_id, "heat", {"current_temperature": 21.5})],
    )
    # 4. Normal unconfigured climate zone 6 in registry -> MUST BE KEPT!
    normal_z6 = ent_reg.async_get_or_create(
        "climate", DOMAIN, f"{MAC}-4-6", config_entry=entry
    )

    added_climates: list = []
    await climate_setup_entry(hass, entry, added_climates.extend)

    assert ent_reg.async_get(ghost_z8.entity_id) is None
    assert ent_reg.async_get(ghost_z7.entity_id) is None
    assert ent_reg.async_get(real_z3.entity_id) is not None
    assert ent_reg.async_get(normal_z6.entity_id) is not None
    assert sorted(c._where for c in added_climates) == ["3", "5", "6"]

    # Whole-zone thermo switch 8 blocks ALL climate discovery (even temperature frames)
    async_dispatcher_send(hass, f"myhome_message_{MAC}", OWNHeatingEvent("*#4*8#1*20*1##"))
    async_dispatcher_send(hass, f"myhome_message_{MAC}", OWNHeatingEvent("*#4*8*0*0210##"))
    assert sorted(c._where for c in added_climates) == ["3", "5", "6"]

    # Per-actuator switch 7#1: Dimension 20 alone does NOT create a phantom climate entity...
    async_dispatcher_send(hass, f"myhome_message_{MAC}", OWNHeatingEvent("*#4*7#1*20*1##"))
    assert sorted(c._where for c in added_climates) == ["3", "5", "6"]

    # ...but if a real temperature probe exists on Zone 7, climate discovers Zone 7 & inherits {"1": True}!
    async_dispatcher_send(hass, f"myhome_message_{MAC}", OWNHeatingEvent("*#4*7*0*0215##"))
    assert sorted(c._where for c in added_climates) == ["3", "5", "6", "7"]
    zone7 = next(c for c in added_climates if c._where == "7")
    assert zone7._actuator_states == {"1": True}
    assert zone7._active_source == "actuators"

    # Routed actuator frame interface extraction in _calling_zones
    zones, iface = _calling_zones(OWNHeatingEvent("*#4*7#1#4#01*20*1##"))
    assert zones == ["7"]
    assert iface == "01"

    # Guard branches when runtime is None or climate platform missing
    none_entry = MockConfigEntry(domain=DOMAIN, data={CONF_MAC: MAC})
    none_entry.runtime_data = None
    assert await climate_setup_entry(hass, none_entry, added_climates.extend) is True
    no_clim_entry = _mock_entry(hass, gw)
    del no_clim_entry.runtime_data.platforms["climate"]
    assert await climate_setup_entry(hass, no_clim_entry, added_climates.extend) is True


@pytest.mark.asyncio
async def test_who4_switch_at_11_does_not_block_who1_light_11_or_create_buttons(
    hass: HomeAssistant,
) -> None:
    """Verify WHO=4 switch at WHERE='11' never collides with WHO=1 light '11' or buttons."""
    gw = _mock_gateway()
    entry = _mock_entry(
        hass,
        gw,
        platforms={
            "switch": {
                "4-11": {"who": "4", "where": "11", "name": "Zone 11 Towel"},
            },
            "button": {
                "4-11": {"who": "4", "where": "11", "name": "Zone 11 Towel"},
            },
        },
    )
    ent_reg = er.async_get(hass)
    ent_reg.async_get_or_create("switch", DOMAIN, f"{MAC}-4-11", config_entry=entry)

    added_lights: list = []
    await light_setup_entry(hass, entry, added_lights.extend)

    # WHO=1 light at WHERE=11 discovers normally despite WHO=4 switch at WHERE=11!
    async_dispatcher_send(hass, f"myhome_message_{MAC}", OWNLightingEvent("*1*1*11##"))
    assert [lt._where for lt in added_lights] == ["11"]

    added_buttons: list = []
    await button_setup_entry(hass, entry, added_buttons.extend)
    # Only CalibrateAllCoversButtonEntity is created; zero lock/unlock buttons for WHO=4!
    assert [getattr(b, "_where", None) for b in added_buttons if getattr(b, "_where", None)] == []


@pytest.mark.asyncio
async def test_integration_setup_and_subentry_immediate_removal(
    hass: HomeAssistant,
) -> None:
    """Verify full HA integration setup links subentry entities and removes them on delete."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="MHS1",
        data={CONF_MAC: MAC, "host": "192.0.2.78", "port": 20000},
        unique_id=MAC,
        subentries_data=[
            {
                "subentry_type": SUBENTRY_TYPE_THERMO_SWITCH,
                "subentry_id": "sub_bath",
                "title": "Bath Towel (WHO 4 / WHERE 7#1)",
                "unique_id": None,
                "data": {"who": "4", "where": "7#1", "name": "Bath Towel"},
            }
        ],
    )
    entry.add_to_hass(hass)

    fake_gw = _mock_gateway()
    fake_gw.test = AsyncMock(return_value={"Success": True})
    fake_gw.listening_loop = AsyncMock()
    fake_gw.sending_loop = AsyncMock()
    fake_gw.initial_discovery = AsyncMock()

    with patch("custom_components.myhome.MyHOMEGatewayHandler", return_value=fake_gw):
        assert await hass.config_entries.async_setup(entry.entry_id) is True
        await hass.async_block_till_done()

        assert "4-7#1" in entry.runtime_data.platforms["switch"]
        ent_reg = er.async_get(hass)
        sw_reg = ent_reg.async_get_entity_id("switch", DOMAIN, f"{MAC}-4-7#1")
        assert sw_reg is not None
        reg_entry = ent_reg.async_get(sw_reg)
        assert reg_entry is not None
        assert reg_entry.config_subentry_id == "sub_bath"

        # Deleting the subentry immediately cleans up the entity from the registry!
        assert hass.config_entries.async_remove_subentry(entry, "sub_bath") is True
        await hass.async_block_till_done()
        assert ent_reg.async_get_entity_id("switch", DOMAIN, f"{MAC}-4-7#1") is None

        assert await hass.config_entries.async_unload(entry.entry_id) is True
