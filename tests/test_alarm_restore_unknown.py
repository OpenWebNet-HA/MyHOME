"""The alarm panel is unknown until the bus says otherwise, and restores its last state."""
import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest
from homeassistant.components.alarm_control_panel import AlarmControlPanelState
from homeassistant.core import HomeAssistant, State
from OWNd.message import OWNEvent
from OWNd.profiles import GenericGatewayProfile, get_gateway_profile

from custom_components.myhome.alarm_control_panel import MyHOMEAlarmControlPanel
from custom_components.myhome.gateway import MyHOMEGatewayHandler

FIXTURES = Path(__file__).resolve().parent / "fixtures"
# Emulated `nack` / `silent` rows for `*#5*0##` in own-firmware-oracle trace-corpus.tsv
# (F450 020010, F453AV 030014, F454 020051, F459 020105, F460 020012, F461 020011,
# MyHomeServer1 028206): factory-blank emulations, not a model capability.
ORACLE_NACK_MODELS = ["F450", "F453AV", "F454", "F459", "F460", "F461", "MyHomeServer1"]


def _gateway(model: str) -> MagicMock:
    gw = MagicMock(spec=MyHOMEGatewayHandler)
    gw.mac = "00:03:50:00:55:55"
    gw.unique_id = gw.mac
    gw.log_id = "[Test Alarm Gateway]"
    gw.model = model
    gw.profile = None
    gw.availability_signal = "myhome_avail_alarm"
    gw.sweep_active = False
    gw.initial_discovery_pending = False
    gw.send = AsyncMock()
    gw.send_status_request = AsyncMock()
    # A single gateway: nothing is delegated or failed over
    gw._delegated_target = MagicMock(return_value=None)
    gw._failover_target = MagicMock(return_value=None)
    return gw


def _refusing(model: str) -> MagicMock:
    """A gateway whose OWNd profile states that it refuses the WHO 5 status request."""
    gw = _gateway(model)
    gw.profile = MagicMock()
    gw.profile.model_name = model
    gw.profile.supports_alarm_status_request = False
    return gw


def _panel(hass: HomeAssistant, gateway: MagicMock) -> MyHOMEAlarmControlPanel:
    panel = MyHOMEAlarmControlPanel(
        hass=hass,
        name="Central Burglar Alarm",
        entity_name="Central Burglar Alarm",
        device_id="0",
        who="5",
        where="0",
        manufacturer="BTicino",
        model="Burglar Alarm 3486",
        gateway=gateway,
    )
    panel.async_write_ha_state = MagicMock()
    panel.async_schedule_update_ha_state = MagicMock()
    return panel


def _frames(path: Path) -> list[tuple[str, str]]:
    """The (direction, raw) frames of a diagnostics dump, a bus-monitor capture or a live-run trace."""
    data = json.loads(path.read_text(encoding="utf-8"))
    if "rows" in data:
        return [(r["dir"], r["frame"]) for r in data["rows"] if r.get("dir") in ("tx", "rx")]
    frames = data["frames"] if "frames" in data else data["data"]["bus_monitor"]["recent_frames"]
    return [(f["direction"], f["raw"]) for f in frames]


def _replay_central_unit(panel: MyHOMEAlarmControlPanel, frames: list[tuple[str, str]]) -> int:
    """Feed the received WHO 5 frames the central unit routes to the panel (empty WHERE or 0, like route_keys)."""
    fed = 0
    for direction, raw in frames:
        if direction != "rx" or not raw.startswith("*5*"):
            continue
        event = OWNEvent.parse(raw)
        if event.where in ("", "0"):
            panel.handle_event(event)
            fed += 1
    return fed


async def test_new_panel_is_unknown_not_disarmed(hass: HomeAssistant) -> None:
    """With no bus frame and nothing to restore the panel does not claim `disarmed`."""
    panel = _panel(hass, _gateway("MH202"))
    panel.async_get_last_state = AsyncMock(return_value=None)

    await panel.async_added_to_hass()

    assert panel.alarm_state is None
    assert panel.extra_state_attributes.get("raw_state") is None
    # MH202 answers *#5*0## (oracle: ack): it is polled
    panel._gateway_handler.send_status_request.assert_awaited_once()


async def test_last_state_is_restored_and_flagged(hass: HomeAssistant) -> None:
    """A restart restores the last known state, flagged as restored until the bus confirms it."""
    panel = _panel(hass, _gateway("MH202"))
    panel.async_get_last_state = AsyncMock(
        return_value=State(
            "alarm_control_panel.central",
            AlarmControlPanelState.ARMED_AWAY,
            {"raw_state": "engage", "state_code": 8},
        )
    )

    await panel.async_added_to_hass()

    assert panel.alarm_state == AlarmControlPanelState.ARMED_AWAY
    assert panel.extra_state_attributes["restored"] is True
    assert panel.extra_state_attributes["raw_state"] == "engage"

    # Activation names no state: the restored state stays flagged as restored
    panel.handle_event(OWNEvent.parse("*5*1*0##"))
    assert panel.extra_state_attributes["restored"] is True

    panel.handle_event(OWNEvent.parse("*5*2*0##"))
    assert panel.alarm_state == AlarmControlPanelState.DISARMED
    assert "restored" not in panel.extra_state_attributes


async def test_unavailable_last_state_is_not_restored(hass: HomeAssistant) -> None:
    panel = _panel(hass, _gateway("MH202"))
    panel.async_get_last_state = AsyncMock(return_value=State("alarm_control_panel.central", "unavailable"))

    await panel.async_added_to_hass()

    assert panel.alarm_state is None
    assert "restored" not in panel.extra_state_attributes


@pytest.mark.parametrize("model", ORACLE_NACK_MODELS)
async def test_oracle_nack_rows_do_not_gate_the_poll(hass: HomeAssistant, model: str) -> None:
    """An emulated `nack` row is not a capability fact (the physical F454 disproves it): with no profile flag, poll."""
    panel = _panel(hass, _gateway(model))
    panel.async_get_last_state = AsyncMock(return_value=None)

    await panel.async_added_to_hass()

    panel._gateway_handler.send_status_request.assert_awaited_once()
    assert str(panel._gateway_handler.send_status_request.await_args.args[0]) == "*#5*0##"
    assert panel.alarm_state is None


@pytest.mark.parametrize(
    ("model", "flag", "polled"),
    [("MH202", False, False), ("F461", False, False), ("F461", True, True), ("F461", None, True), ("MH202", None, True)],
)
async def test_only_an_explicit_profile_flag_skips_the_poll(hass: HomeAssistant, model, flag, polled) -> None:
    """`supports_alarm_status_request` False on the OWNd profile skips; True or None (unverified) polls."""
    gateway = _gateway(model)
    gateway.profile = MagicMock()
    gateway.profile.supports_alarm_status_request = flag
    panel = _panel(hass, gateway)
    panel.async_get_last_state = AsyncMock(return_value=None)

    await panel.async_added_to_hass()
    await panel.async_update()

    assert panel._gateway_handler.send_status_request.await_count == (2 if polled else 0)


@pytest.mark.parametrize(
    ("fixture", "model", "request_frame", "first_reply"),
    [
        # #311: physical F454 2.0 with a burglar alarm; *#5*#1## is answered on the bus
        ("plants/issue_311_f454/diagnostic_summary.json", "F454", "*#5*#1##", "*5*11*#1##"),
        # #629: physical F454 1.0.34 sweep; *#5*0## is answered with the central unit's frames
        ("traces/issue_629/myhome_sweep_F454_all_2026-10-05T10-51-55.json", "F454", "*#5*0##", "*5*0*##"),
        # #619: physical F453AV 3.0.14 sweep; *#5*0## is answered with the central unit's frames
        ("traces/issue_619/myhome_sweep_F453AV_all_2026-10-04T19-45-11.json", "F453AV", "*#5*0##", "*5*1*##"),
    ],
)
async def test_physical_f454_and_f453av_answer_the_status_request(
    hass: HomeAssistant, fixture: str, model: str, request_frame: str, first_reply: str
) -> None:
    """The emulated F454 / F453AV rows say `nack`; the physical plants answer, and their frames update the panel."""
    frames = _frames(FIXTURES / fixture)
    tx_index = frames.index(("tx", request_frame))
    replies = [raw for direction, raw in frames[tx_index + 1 :] if direction == "rx" and raw.startswith("*5*")]
    assert replies[0] == first_reply, f"{model} trace: {request_frame} is answered on the bus"

    panel = _panel(hass, _gateway(model))
    panel.async_get_last_state = AsyncMock(
        return_value=State("alarm_control_panel.alarm_0", AlarmControlPanelState.ARMED_AWAY, {"raw_state": "engage"})
    )
    await panel.async_added_to_hass()

    # No profile flag: the F454 / F453AV is polled, and the central unit answers on these plants
    panel._gateway_handler.send_status_request.assert_awaited_once()
    assert str(panel._gateway_handler.send_status_request.await_args.args[0]) == "*#5*0##"
    assert panel.extra_state_attributes["restored"] is True

    assert _replay_central_unit(panel, frames) > 0

    # *5*9*## (disengage) confirmed the restored state: the panel is live again
    assert panel.alarm_state == AlarmControlPanelState.DISARMED
    assert "restored" not in panel.extra_state_attributes


@pytest.mark.parametrize(
    ("fixture", "model"),
    [
        # #799: live F461 2.0.11, no central unit: *#5*0## and *#5*#1## NACKed on the command session
        ("traces/f461_live_2026-10-23/myhome_trace_F461_live_2026-10-23.json", "F461"),
        # #799: live MyHomeServer1 3.87.13, no WHO 5 plant: *#5*0## NACKed on the command session
        ("traces/myhomeserver1_live_2026-10-24/myhome_trace_MyHomeServer1_live_2026-10-24.json", "MyHomeServer1"),
    ],
)
async def test_live_nack_leaves_the_restored_state_alone(hass: HomeAssistant, fixture: str, model: str) -> None:
    """A NACK on `*#5*0##` is the gateway's own refusal on that plant: nothing reaches the panel, no false state."""
    frames = _frames(FIXTURES / fixture)
    tx_index = frames.index(("tx", "*#5*0##"))
    assert frames[tx_index + 1] == ("rx", "*#*0##"), f"{model} trace: *#5*0## is NACKed"

    panel = _panel(hass, _gateway(model))
    panel.async_get_last_state = AsyncMock(
        return_value=State("alarm_control_panel.alarm_0", AlarmControlPanelState.ARMED_AWAY, {"raw_state": "engage"})
    )
    await panel.async_added_to_hass()

    # The plant decides, not the model: the request is sent, the gateway NACKs it
    panel._gateway_handler.send_status_request.assert_awaited_once()
    assert _replay_central_unit(panel, frames) == 0
    assert panel.alarm_state == AlarmControlPanelState.ARMED_AWAY
    assert panel.extra_state_attributes["restored"] is True
    assert panel.extra_state_attributes["raw_state"] == "engage"


@pytest.mark.parametrize(
    ("primary", "secondary", "polled"),
    [
        # MyHomeServer1 primary whose profile refuses, MH202 secondary carrying WHO 5: the secondary is asked
        (_refusing("MyHomeServer1"), _gateway("MH202"), True),
        # MH202 primary, a secondary carrying WHO 5 whose profile refuses: nothing is sent
        (_gateway("MH202"), _refusing("F461"), False),
    ],
)
async def test_shared_bus_delegation_decides_on_the_secondary(hass: HomeAssistant, primary, secondary, polled) -> None:
    """The gateway send_status_request would route to decides, not the one the entity was registered on."""
    primary._delegated_target = MagicMock(return_value=secondary)
    panel = _panel(hass, primary)
    panel.async_get_last_state = AsyncMock(return_value=None)

    await panel.async_added_to_hass()

    assert str(primary._delegated_target.call_args.args[0]) == "*#5*0##"
    # The route is resolved once and the request goes through the secondary
    primary.send_status_request.assert_not_awaited()
    assert secondary.send_status_request.await_count == (1 if polled else 0)


@pytest.mark.parametrize(
    ("primary", "standby", "polled"),
    [(_refusing("F461"), _gateway("MH202"), True), (_gateway("MH202"), _refusing("F461"), False)],
)
async def test_failover_target_decides_while_the_primary_is_down(hass: HomeAssistant, primary, standby, polled) -> None:
    """The warm standby that would carry the request decides, not the disconnected primary."""
    primary._failover_target = MagicMock(return_value=standby)
    panel = _panel(hass, primary)
    panel.async_get_last_state = AsyncMock(return_value=None)

    await panel.async_added_to_hass()

    primary.send_status_request.assert_not_awaited()
    assert standby.send_status_request.await_count == (1 if polled else 0)
    # The primary's own routing (and its failover bookkeeping) ran exactly once
    primary._failover_target.assert_called_once()


async def test_a_mock_or_foreign_route_result_falls_back_to_the_gateway(hass: HomeAssistant) -> None:
    """Only a real gateway handler is accepted as the route target; anything else means `no route`."""
    gateway = _gateway("MH202")
    gateway._delegated_target = MagicMock(return_value=object())
    panel = _panel(hass, gateway)
    panel.async_get_last_state = AsyncMock(return_value=None)

    await panel.async_added_to_hass()

    gateway.send_status_request.assert_awaited_once()


@pytest.mark.parametrize("configured_name", ["MHS1", "573992", " my-home-server-1 ", "f461"])
async def test_explicit_true_polls_whatever_the_configured_name(hass: HomeAssistant, configured_name: str) -> None:
    """An explicit True on the profile polls; the configured alias, SKU, case or spacing plays no part."""
    gateway = _gateway(configured_name)
    gateway.profile = MagicMock()
    gateway.profile.supports_alarm_status_request = True
    panel = _panel(hass, gateway)
    panel.async_get_last_state = AsyncMock(return_value=None)

    await panel.async_added_to_hass()

    gateway.send_status_request.assert_awaited_once()


@pytest.mark.parametrize(
    ("configured_name", "label"),
    [
        ("MHS1", "MyHomeServer1"),  # short alias: the OWNd profile's canonical model_name
        ("573992", "F453AV"),  # SKU alias: the OWNd profile's canonical model_name
        (" Unknown-GW ", "Unknown-GW"),  # Generic profile: the configured name, trimmed
    ],
)
async def test_refusal_log_names_the_canonical_model_and_the_frame(
    hass: HomeAssistant, caplog, configured_name: str, label: str
) -> None:
    """The DEBUG line carries the frame actually skipped and the canonical name of the gateway carrying it."""
    resolved = get_gateway_profile(configured_name)
    assert isinstance(resolved, GenericGatewayProfile) == (label == "Unknown-GW")
    primary = _gateway("MH202")
    standby = _gateway(configured_name)
    # The frozen OWNd profiles carry no flag yet: a profile of the resolved class that states one
    standby.profile = MagicMock(spec=type(resolved))
    standby.profile.model_name = resolved.model_name
    standby.profile.supports_alarm_status_request = False
    primary._failover_target = MagicMock(return_value=standby)
    panel = _panel(hass, primary)
    panel._where = "#1"
    panel.async_get_last_state = AsyncMock(return_value=None)

    with caplog.at_level("DEBUG", logger="custom_components.myhome"):
        await panel.async_added_to_hass()

    primary.send_status_request.assert_not_awaited()
    assert f"{label} refuses *#5*#1##" in caplog.text
    assert "*#5*0##" not in caplog.text


@pytest.mark.parametrize(
    "state", [AlarmControlPanelState.DISARMED, AlarmControlPanelState.ARMED_AWAY, AlarmControlPanelState.TRIGGERED]
)
async def test_each_state_the_panel_emits_is_restored(hass: HomeAssistant, state: AlarmControlPanelState) -> None:
    panel = _panel(hass, _gateway("MH202"))
    panel.async_get_last_state = AsyncMock(return_value=State("alarm_control_panel.alarm_0", state, {"raw_state": "x"}))

    await panel.async_added_to_hass()

    assert panel.alarm_state == state
    assert panel.extra_state_attributes["restored"] is True


@pytest.mark.parametrize("stale", ["armed_home", "arming", "pending", "armed_night", "unknown", "unavailable"])
async def test_states_the_panel_never_emits_are_not_restored(hass: HomeAssistant, stale: str) -> None:
    """A stale `armed_home` (removed in #609) or a transient state must not wedge the template alarm recipe."""
    panel = _panel(hass, _gateway("MH202"))
    panel.async_get_last_state = AsyncMock(
        return_value=State("alarm_control_panel.alarm_0", stale, {"raw_state": "engage", "state_code": 8})
    )

    await panel.async_added_to_hass()

    assert panel.alarm_state is None
    assert "restored" not in panel.extra_state_attributes
    assert panel.extra_state_attributes["raw_state"] is None
    assert "state_code" not in panel.extra_state_attributes


@pytest.mark.parametrize(
    "frame",
    [
        "*5*1*0##",  # activation
        "*5*11*#1##",  # active zone (routed to the central unit on an F454, #311)
        "*5*18*#2##",  # not-active zone
        "*5*5*##",  # battery OK, empty WHERE
        "*5*7*##",  # mains, empty WHERE
    ],
)
async def test_non_verdict_frames_keep_the_restored_raw_state(hass: HomeAssistant, frame: str) -> None:
    """While `restored` is set, telemetry that names no state leaves raw_state / state_code as restored."""
    panel = _panel(hass, _gateway("MH202"))
    panel.async_get_last_state = AsyncMock(
        return_value=State(
            "alarm_control_panel.alarm_0", AlarmControlPanelState.ARMED_AWAY, {"raw_state": "engage", "state_code": 8}
        )
    )
    await panel.async_added_to_hass()

    panel.handle_event(OWNEvent.parse(frame))

    assert panel.alarm_state == AlarmControlPanelState.ARMED_AWAY
    assert panel.extra_state_attributes["restored"] is True
    assert panel.extra_state_attributes["raw_state"] == "engage"
    assert panel.extra_state_attributes["state_code"] == 8

    # A verdict overwrites both and clears the flag
    panel.handle_event(OWNEvent.parse("*5*9*0##"))
    assert panel.alarm_state == AlarmControlPanelState.DISARMED
    assert "restored" not in panel.extra_state_attributes
    assert panel.extra_state_attributes["raw_state"] == "disengage"
    assert panel.extra_state_attributes["state_code"] == 9

    # Live again: telemetry updates the raw attributes as before
    panel.handle_event(OWNEvent.parse(frame))
    assert panel.extra_state_attributes["raw_state"] == OWNEvent.parse(frame).state_name
