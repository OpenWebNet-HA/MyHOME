"""The alarm panel is unknown until the bus says otherwise, and restores its last state (#177)."""
from unittest.mock import AsyncMock, MagicMock

import pytest
from homeassistant.components.alarm_control_panel import AlarmControlPanelState
from homeassistant.core import HomeAssistant, State
from OWNd.message import OWNEvent

from custom_components.myhome.alarm_control_panel import MyHOMEAlarmControlPanel


def _gateway(model: str) -> MagicMock:
    gw = MagicMock()
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


@pytest.mark.parametrize("model", ["F454", "F459", "F460", "F461", "MyHomeServer1", "F450", "F453AV"])
async def test_no_status_poll_where_the_gateway_refuses_it(hass: HomeAssistant, model: str) -> None:
    """`*#5*0##` is NACKed by these gateways (own-firmware-oracle): no poll, state stays restored."""
    panel = _panel(hass, _gateway(model))
    panel.async_get_last_state = AsyncMock(return_value=None)

    await panel.async_added_to_hass()
    await panel.async_update()

    panel._gateway_handler.send_status_request.assert_not_awaited()
    assert panel.alarm_state is None


@pytest.mark.parametrize(
    ("model", "flag", "polled"),
    [("MH202", False, False), ("F461", True, True), ("F461", None, False), ("MH202", None, True)],
)
async def test_ownd_profile_flag_decides_before_the_model_table(hass: HomeAssistant, model, flag, polled) -> None:
    """A `supports_alarm_status_request` flag on the OWNd profile wins; `None` (unverified) falls back to the table."""
    gateway = _gateway(model)
    gateway.profile = MagicMock()
    gateway.profile.supports_alarm_status_request = flag
    panel = _panel(hass, gateway)
    panel.async_get_last_state = AsyncMock(return_value=None)

    await panel.async_added_to_hass()

    assert panel._gateway_handler.send_status_request.await_count == (1 if polled else 0)
