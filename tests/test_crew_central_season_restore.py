"""Season selection on a 99-zone central: service output and report-driven state.

These are composed replay scenarios, not a single recorded transaction.
"""

from collections.abc import Iterator
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from homeassistant.components.climate.const import HVACMode
from homeassistant.core import State
from OWNd.message import OWNEvent

HEAT = "*4*1*#0##"  # tests/fixtures/traces/climate_mode_2026_11_08/frames.json:98
COOL = "*4*0*#0##"  # tests/fixtures/traces/climate_mode_2026_11_08/frames.json:127
RESTORE = "*4*3100*#0##"  # kb/openwebnet-who-4.txt:1598
ANTIFREEZE = "*4*102*#0##"  # tests/fixtures/traces/climate_mode_2026_11_08/frames.json:103
THERMAL_PROTECTION = "*4*202*#0##"  # tests/fixtures/traces/climate_mode_2026_11_08/frames.json:132
HEATING_OFF = "*4*103*#0##"  # tests/fixtures/traces/who4_four_gateways_2026_11_18/frames.json:7222
HEATING_PROGRAM = "*4*1101*#0##"  # tests/fixtures/traces/issue_466/myhome_sweep_MH200N_all_2026-11-02T12-53-15.json:1423
COOLING_PROGRAM = "*4*2101*#0##"  # tests/fixtures/traces/central_restore_3100_live_2026-11-12/README.md:91
FLAGS = [
    "*4*21*#0##",  # tests/fixtures/traces/climate_mode_2026_11_08/frames.json:108
    "*4*31*#0##",  # tests/fixtures/traces/climate_mode_2026_11_08/frames.json:113
]


@pytest.fixture(autouse=True)
def _entity_base_init() -> Iterator[None]:
    with patch("custom_components.myhome.myhome_device.Entity.__init__", return_value=None):
        yield


def _gateway(has_central_unit: bool = True) -> MagicMock:
    gw = MagicMock()
    gw.mac = "<mac>"
    gw.unique_id = gw.mac
    gw.log_id = "[Test Gateway]"
    gw.send = AsyncMock()
    gw.send_status_request = AsyncMock()
    gw.has_central_unit = has_central_unit
    return gw


def _climate(gateway: MagicMock, where: str, *, standalone: bool | None, central: bool = False) -> Any:
    from custom_components.myhome.climate import MyHOMEClimate

    hass = MagicMock()
    hass.data = {}
    entity = MyHOMEClimate(
        hass=hass,
        name="Climate",
        device_id=f"4-{where}",
        who="4",
        where=where,
        heating=True,
        cooling=True,
        fan=False,
        standalone=standalone,
        central=central,
        manufacturer="BTicino",
        model="Thermostat",
        gateway=gateway,
    )
    entity.platform = MagicMock()
    entity.entity_id = "climate.test"
    setattr(entity, "async_schedule_update_ha_state", MagicMock())
    setattr(entity, "async_write_ha_state", MagicMock())
    return entity


def _replay(entity: Any, frames: list[str]) -> None:
    for raw in frames:
        entity.handle_event(OWNEvent.parse(raw))


def _sent(gateway: MagicMock) -> list[str]:
    return [str(call.args[0]) for call in gateway.send.await_args_list]


@pytest.mark.parametrize(
    ("initial", "shown", "operation"),
    [
        pytest.param(THERMAL_PROTECTION, HVACMode.COOL, "thermal_protection", id="thermal-protection"),
        pytest.param(ANTIFREEZE, HVACMode.HEAT, "antifreeze", id="antifreeze"),
        pytest.param(HEATING_OFF, HVACMode.OFF, None, id="off"),
    ],
)
async def test_heat_leaves_reported_protection_or_off(initial: str, shown: HVACMode, operation: str | None) -> None:
    gateway = _gateway()
    central = _climate(gateway, "#0", standalone=None, central=True)
    _replay(central, [initial, *FLAGS])
    assert central.hvac_mode == shown
    assert central.extra_state_attributes.get("central_operation") == operation

    await central.async_set_hvac_mode(HVACMode.HEAT)
    assert _sent(gateway) == [HEAT, RESTORE]
    assert central.hvac_mode == shown
    if initial == THERMAL_PROTECTION:
        _replay(central, [ANTIFREEZE, *FLAGS])
        assert central.extra_state_attributes["central_operation"] == "antifreeze"
    _replay(central, [HEATING_PROGRAM, *FLAGS])
    assert central.hvac_mode == HVACMode.HEAT
    attrs = central.extra_state_attributes
    assert "central_operation" not in attrs
    assert attrs["season"] == "heating"
    assert attrs["program"] == 1


@pytest.mark.parametrize(
    "reports",
    [
        pytest.param([], id="no-report"),
        pytest.param([HEATING_PROGRAM], id="heating-program"),
        pytest.param(["*4*110#0180*#0##"], id="manual-heating"),  # tests/fixtures/traces/issue_466/README.md:30
    ],
)
async def test_heat_does_not_replace_an_active_or_unknown_program_or_setpoint(reports: list[str]) -> None:
    gateway = _gateway()
    central = _climate(gateway, "#0", standalone=None, central=True)
    _replay(central, reports)
    before = central.hvac_mode
    await central.async_set_hvac_mode(HVACMode.HEAT)
    assert _sent(gateway) == [HEAT]
    assert central.hvac_mode == before


async def test_cool_exits_thermal_protection_even_in_the_cooling_season() -> None:
    gateway = _gateway()
    central = _climate(gateway, "#0", standalone=None, central=True)
    _replay(central, [THERMAL_PROTECTION, *FLAGS])
    await central.async_set_hvac_mode(HVACMode.COOL)
    assert _sent(gateway) == [COOL, RESTORE]
    assert central.extra_state_attributes["central_operation"] == "thermal_protection"
    _replay(central, [COOLING_PROGRAM, *FLAGS])
    assert central.hvac_mode == HVACMode.COOL
    assert "central_operation" not in central.extra_state_attributes
    assert central.extra_state_attributes["program"] == 1


async def test_cool_from_heating_program_restores_cooling_program() -> None:
    gateway = _gateway()
    central = _climate(gateway, "#0", standalone=None, central=True)
    _replay(central, [HEATING_PROGRAM])
    await central.async_set_hvac_mode(HVACMode.COOL)
    assert _sent(gateway) == [COOL, RESTORE]
    assert central.hvac_mode == HVACMode.HEAT
    _replay(central, [THERMAL_PROTECTION, *FLAGS, COOLING_PROGRAM, *FLAGS])
    assert central.hvac_mode == HVACMode.COOL
    assert central.extra_state_attributes["program"] == 1
    assert "central_operation" not in central.extra_state_attributes


async def test_f455_echoes_of_both_sends_are_not_reports() -> None:
    gateway = _gateway()
    central = _climate(gateway, "#0", standalone=None, central=True)
    _replay(central, [ANTIFREEZE, *FLAGS])
    await central.async_set_hvac_mode(HVACMode.COOL)
    # Echo both at once, before the protection/program reports, as specified in T7.
    _replay(central, [COOL, RESTORE])
    assert central.hvac_mode == HVACMode.HEAT
    assert central.extra_state_attributes["central_operation"] == "antifreeze"
    assert _sent(gateway) == [COOL, RESTORE]
    _replay(central, [THERMAL_PROTECTION, *FLAGS, COOLING_PROGRAM, *FLAGS])
    assert central.hvac_mode == HVACMode.COOL
    assert central.extra_state_attributes["program"] == 1
    assert "central_operation" not in central.extra_state_attributes


async def test_f455_restore_echo_while_send_is_pending_keeps_the_protection_report() -> None:
    gateway = _gateway()
    central = _climate(gateway, "#0", standalone=None, central=True)
    _replay(central, [HEATING_PROGRAM])

    async def monitor_traffic_during_send(command: Any) -> None:
        # An ordering test, not a captured timeline: real protection arrives before
        # the second send's echo. Neither echo may hide or replace the real report.
        if str(command) == COOL:
            _replay(central, [COOL, THERMAL_PROTECTION, *FLAGS])
        elif str(command) == RESTORE:
            _replay(central, [RESTORE])

    gateway.send.side_effect = monitor_traffic_during_send
    await central.async_set_hvac_mode(HVACMode.COOL)
    assert _sent(gateway) == [COOL, RESTORE]
    assert central.hvac_mode == HVACMode.COOL
    assert central.extra_state_attributes["central_operation"] == "thermal_protection"


async def test_failed_restore_reaches_caller_and_disarms_restore_echo() -> None:
    gateway = _gateway()
    central = _climate(gateway, "#0", standalone=None, central=True)
    _replay(central, [ANTIFREEZE, *FLAGS])
    gateway.send.side_effect = [None, RuntimeError("gateway gone")]
    with pytest.raises(RuntimeError, match="gateway gone"):
        await central.async_set_hvac_mode(HVACMode.HEAT)
    assert _sent(gateway) == [HEAT, RESTORE]
    assert central._central_restore_echo is None


async def test_failed_season_send_does_not_attempt_restore() -> None:
    gateway = _gateway()
    central = _climate(gateway, "#0", standalone=None, central=True)
    _replay(central, [ANTIFREEZE, *FLAGS])
    gateway.send.side_effect = RuntimeError("gateway gone")
    with pytest.raises(RuntimeError, match="gateway gone"):
        await central.async_set_hvac_mode(HVACMode.COOL)
    assert _sent(gateway) == [COOL]
    assert central.hvac_mode == HVACMode.HEAT
    assert central.extra_state_attributes["central_operation"] == "antifreeze"


async def test_four_zone_central_is_still_immediate_with_one_send() -> None:
    gateway = _gateway()
    central = _climate(gateway, "#0#1", standalone=None, central=True)
    await central.async_set_hvac_mode(HVACMode.HEAT)
    assert len(_sent(gateway)) == 1
    assert central.hvac_mode == HVACMode.HEAT


@pytest.mark.parametrize(
    ("state", "attributes"),
    [
        ("unknown", {}),
        ("unavailable", {}),
        ("cool", {"season": "conditioning"}),
        ("off", {}),
        ("heat", {"central_operation": "antifreeze"}),
    ],
    ids=["unknown", "unavailable", "opposite-season", "off", "antifreeze"],
)
async def test_only_reported_not_saved_state_can_trigger_restore(state: str, attributes: dict[str, str]) -> None:
    gateway = _gateway()
    central = _climate(gateway, "#0", standalone=None, central=True)
    await central.async_restore_last_state(State(central.entity_id, state, attributes))
    await central.async_set_hvac_mode(HVACMode.HEAT)
    assert _sent(gateway) == [HEAT]
    if "central_operation" in attributes:
        gateway.send.reset_mock()
        _replay(central, [ANTIFREEZE, *FLAGS])
        await central.async_set_hvac_mode(HVACMode.HEAT)
        assert _sent(gateway) == [HEAT, RESTORE]
