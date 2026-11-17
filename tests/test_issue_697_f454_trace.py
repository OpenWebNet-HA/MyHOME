"""Replay and verification tests for #697: BTicino F454 with 99-zone central plant.

Fixture: tests/fixtures/traces/issue_697/f454_99zone_central_subordinate_zones.json
Contributed by @ricdijk on https://github.com/OpenWebNet-HA/MyHOME/issues/697
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from homeassistant.components.climate.const import HVACMode
from OWNd.message import OWNEvent, OWNMessage

from custom_components.myhome.climate import MyHOMEClimate
from custom_components.myhome.gateway_events import GatewayEventDispatcher

FIXTURE_PATH = (
    Path(__file__).parent
    / "fixtures"
    / "traces"
    / "issue_697"
    / "f454_99zone_central_subordinate_zones.json"
)


def _load_fixture() -> dict:
    return json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))


@pytest.fixture(autouse=True)
def _entity_init():
    with patch("custom_components.myhome.myhome_device.Entity.__init__", return_value=None):
        yield


def _make_climate(
    gateway: MagicMock,
    where: str = "3",
    standalone: bool | None = None,
    central: bool = False,
    local_offset: float = 0.0,
) -> MyHOMEClimate:
    hass = MagicMock()
    hass.data = {}
    c = MyHOMEClimate(
        hass=hass,
        name=f"Zone {where}" if not central else "Central Unit",
        device_id=f"4-{where}",
        who="4",
        where=where,
        heating=True,
        cooling=True,
        fan=False,
        standalone=standalone,
        central=central,
        manufacturer="BTicino",
        model="Heating Zone" if not central else "Central Unit",
        gateway=gateway,
    )
    c.platform = MagicMock()
    c.entity_id = f"climate.zone_{where}"
    c._local_offset = local_offset
    c.async_schedule_update_ha_state = MagicMock()
    c.async_write_ha_state = MagicMock()
    return c


def test_fixture_schema_and_metadata() -> None:
    data = _load_fixture()
    assert data["gateway"]["model"] == "F454"
    assert data["gateway"]["firmware"] == "1.0.34"
    assert data["plant"]["central_unit"] == "99-zone (WHERE #0)"
    assert data["plant"]["zone_offsets"]["11"] == -3.0
    assert len(data["frames"]) == 23


def test_every_rx_frame_parses_cleanly_as_own_event() -> None:
    data = _load_fixture()
    for item in data["frames"]:
        if item["direction"] == "rx":
            msg = OWNMessage.parse(item["raw"])
            assert isinstance(msg, OWNEvent)
            assert msg.who == 4


@pytest.mark.asyncio
async def test_central_frame_reception_activates_has_central_unit() -> None:
    data = _load_fixture()
    gateway = MagicMock()
    gateway.has_central_unit = False
    gateway.is_standby = False
    gateway.is_secondary = False
    gateway.delegated_away_whos = set()
    gateway.log_id = "[Test Gateway]"

    dispatcher = GatewayEventDispatcher(gateway)

    # Initial state: no central unit known
    assert gateway.has_central_unit is False

    # Feed frames until central unit speaks (first frame is *4*110#0170*#0##)
    central_frame = data["frames"][0]
    assert central_frame["raw"] == "*4*110#0170*#0##"

    with patch("custom_components.myhome.gateway_events.async_dispatcher_send", MagicMock(), create=True):
        await dispatcher.process_message(OWNEvent.parse(central_frame["raw"]))

    assert gateway.has_central_unit is True


@pytest.mark.asyncio
async def test_subordinate_zone_commands_match_hardware_tested_hash_form() -> None:
    gateway = MagicMock()
    gateway.has_central_unit = True  # Discovered central on plant
    gateway.send = AsyncMock()

    zone3 = _make_climate(gateway, where="3", standalone=None)
    assert zone3._standalone is False

    # 1. Target temperature set: sends *#4*#3*#14*...##
    await zone3.async_set_temperature(temperature=14.5)
    sent_setpoint = str(gateway.send.call_args.args[0])
    assert sent_setpoint == "*#4*#3*#14*0145*3##"

    # 2. HVACMode.OFF: sends *4*303*#3##
    gateway.send.reset_mock()
    await zone3.async_set_hvac_mode(HVACMode.OFF)
    sent_off = str(gateway.send.call_args.args[0])
    assert sent_off == "*4*303*#3##"

    # 3. HVACMode.AUTO: sends *4*311*#3##
    gateway.send.reset_mock()
    await zone3.async_set_hvac_mode(HVACMode.AUTO)
    sent_auto = str(gateway.send.call_args.args[0])
    assert sent_auto == "*4*311*#3##"


@pytest.mark.asyncio
async def test_zone_11_applies_offset_to_target_temperature() -> None:
    gateway = MagicMock()
    gateway.has_central_unit = True
    gateway.send = AsyncMock()

    # Zone 11 has -3.0 offset: HA target 14.0 C -> wire target 17.0 C (0170)
    zone11 = _make_climate(gateway, where="11", standalone=None, local_offset=-3.0)
    await zone11.async_set_temperature(temperature=14.0)

    sent = str(gateway.send.call_args.args[0])
    assert sent == "*#4*#11*#14*0170*3##"


@pytest.mark.asyncio
async def test_explicit_standalone_true_overrides_central_plant() -> None:
    gateway = MagicMock()
    gateway.has_central_unit = True
    gateway.send = AsyncMock()

    zone_standalone = _make_climate(gateway, where="3", standalone=True)
    assert zone_standalone._standalone is True

    await zone_standalone.async_set_hvac_mode(HVACMode.OFF)
    sent = str(gateway.send.call_args.args[0])
    assert sent == "*4*303*3##"


@pytest.mark.asyncio
async def test_rx_event_sequence_updates_zone_and_central_state() -> None:
    gateway = MagicMock()
    gateway.has_central_unit = True
    gateway.send = AsyncMock()

    zone3 = _make_climate(gateway, where="3")
    central = _make_climate(gateway, where="#0", central=True)

    # 1. Zone 3 receives OFF confirmation from hardware trace (*4*303*3## / *4*103*#3##)
    zone3.handle_event(OWNEvent.parse("*4*303*3##"))
    assert zone3.hvac_mode == HVACMode.OFF

    # 2. Zone 3 receives setpoint confirmation (*#4*3*12*0145*3##)
    zone3.handle_event(OWNEvent.parse("*#4*3*12*0145*3##"))
    assert zone3.target_temperature == 14.5

    # 3. Central unit receives heat mode report (*4*110#0165*#0##)
    central.handle_event(OWNEvent.parse("*4*110#0165*#0##"))
    assert central.hvac_mode == HVACMode.HEAT
    assert central.target_temperature == 16.5
