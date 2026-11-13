"""3550 battery flag flip, F455 zone AUTO flip, central echo window: live F461 + F455 replay, 2026-11-09.

Same plant as tests/test_climate_mode_state_2026_11_08.py: a BTicino 3550 99-zone central unit
(#0) without a battery, zone 4 a fan coil zone of the 3550 in manual cooling, an F461 and an
F455 on the same bus, one Home Assistant entity per gateway.

* The 3550 answers ``*#4*#0##`` with ``*4*210#0240*#0##``, ``*4*21*#0##``, ``*4*31*#0##`` within
  4 ms. Both central entities wrote ``battery_fault`` False on the mode frame and True on the
  31, 1.5 ms later, on every reply (Home Assistant history and state_changed events).
* At every quarter-hour push of the 3550 the F455 puts ``*4*311*#4##`` on its event session,
  59 ms before ``*4*210*#4##``; the F461 never shows a 311 and shows ``*#4*#4*14*0240*2##``
  instead. The F455 entity of zone 4 went AUTO and back to COOL every 15 minutes.
* The central echo window starts when the command is written, not when it is queued.

Frames: tests/fixtures/traces/central_flags_f455_poll_2026_11_09/frames.json
([time UTC, gateway monitor, frame] per window).
"""

import asyncio
import json
from collections.abc import Iterator
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from homeassistant.components.climate.const import HVACMode
from OWNd.message import OWNEvent, OWNHeatingCommand

from custom_components.myhome import climate as climate_module
from custom_components.myhome.where_grammar import is_central_unit_report

FRAMES = json.loads(
    (
        Path(__file__).parent
        / "fixtures"
        / "traces"
        / "central_flags_f455_poll_2026_11_09"
        / "frames.json"
    ).read_text()
)


def _rx(window: str, gateway: str) -> list[str]:
    return [raw for _t, gw, raw in FRAMES[window] if gw == gateway]


@pytest.fixture(autouse=True)
def _entity_base_init() -> Iterator[None]:
    with patch("custom_components.myhome.myhome_device.Entity.__init__", return_value=None):
        yield


@pytest.fixture
def timers() -> Iterator[list[Any]]:
    """Capture the climate timers (flag cycle, held generic frame) instead of scheduling them.

    A cancelled timer leaves the list, so ``timers`` holds only the pending ones.
    """
    pending: list[Any] = []

    def _later(_hass: Any, _delay: float, action: Any) -> Any:
        pending.append(action)

        def _cancel() -> None:
            if action in pending:
                pending.remove(action)

        return _cancel

    with patch("custom_components.myhome.climate.async_call_later", side_effect=_later):
        yield pending


def _gateway() -> MagicMock:
    gw = MagicMock()
    gw.mac = "00:03:50:00:00:01"
    gw.unique_id = gw.mac
    gw.log_id = "[Test Gateway]"
    gw.send = AsyncMock()
    gw.send_status_request = AsyncMock()
    gw.has_central_unit = True
    return gw


def _climate(
    gateway: MagicMock, where: str, *, central: bool = False, fan: bool = False
) -> tuple[Any, list[dict[str, Any]]]:
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
        fan=fan,
        standalone=False,
        central=central,
        manufacturer="BTicino",
        model="Thermostat",
        gateway=gateway,
    )
    entity.platform = MagicMock()
    entity.entity_id = "climate.test"
    writes: list[dict[str, Any]] = []

    def _write(*_args: Any, **_kwargs: Any) -> None:
        writes.append({"hvac_mode": entity.hvac_mode, **entity.extra_state_attributes})

    setattr(entity, "async_schedule_update_ha_state", _write)
    setattr(entity, "async_write_ha_state", _write)
    return entity, writes


def _replay(entity: Any, frames: list[str], *, twice: bool = False) -> None:
    for raw in frames:
        message = OWNEvent.parse(raw)
        entity.handle_event(message)
        if twice:  # the climate and the sensor platform both route WHO 4 frames to the entity
            entity.handle_event(OWNEvent.parse(raw))


def _central_without_battery(timers: list[Any]) -> tuple[Any, list[dict[str, Any]]]:
    central, writes = _climate(_gateway(), "#0", central=True)
    _replay(central, _rx("central_reply_via_f461_0747", "f461"))
    timers.pop()(None)
    assert central.extra_state_attributes["battery_fault"] is True
    writes.clear()
    return central, writes


# A: the 3550's battery flag is not written False and True again on each reply.


@pytest.mark.parametrize(
    ("window", "gateway"),
    [
        ("central_reply_via_f461_0747", "f461"),
        ("central_reply_via_f461_0747", "f455"),
        ("central_reply_via_f455_0748", "f461"),
        ("central_reply_via_f455_0748", "f455"),
    ],
)
def test_battery_flag_stays_set_through_each_central_reply(
    timers: list[Any], window: str, gateway: str
) -> None:
    central, writes = _central_without_battery(timers)
    frames = _rx(window, gateway)
    assert frames == ["*4*210#0240*#0##", "*4*21*#0##", "*4*31*#0##"]
    with patch.object(central, "_clear_fault") as clear_fault:
        _replay(central, frames, twice=True)
        assert len(timers) == 1  # the second copy of 21 does not restart the cycle
        timers.pop()(None)
    assert writes, "the reply is written"
    assert all(w["battery_fault"] is True for w in writes)
    assert central.extra_state_attributes["battery_fault"] is True
    assert central.hvac_mode == HVACMode.COOL
    assert all(
        call.args[0] != climate_module.FaultKind.CENTRAL_BATTERY_FAULT
        for call in clear_fault.call_args_list
    )


def test_battery_flag_clears_once_a_burst_settles_without_it(timers: list[Any]) -> None:
    central, writes = _central_without_battery(timers)
    _replay(central, ["*4*210#0240*#0##", "*4*21*#0##"])
    assert all(w["battery_fault"] is True for w in writes)  # not cleared mid-burst
    timers.pop()(None)
    assert central.extra_state_attributes["battery_fault"] is False
    assert writes[-1]["battery_fault"] is False


def test_a_mode_frame_alone_does_not_touch_the_flags(timers: list[Any]) -> None:
    # Another client's season command on the bus, or the F455 handing one back, has no 20 / 21.
    central, writes = _central_without_battery(timers)
    _replay(central, ["*4*0*#0##", "*4*202*#0##", "*4*303*#0##"])
    assert timers == []
    assert all(w["battery_fault"] is True for w in writes)


def test_a_late_second_copy_of_the_remote_control_frame_keeps_the_flag(timers: list[Any]) -> None:
    central, _writes = _central_without_battery(timers)
    _replay(central, ["*4*210#0240*#0##", "*4*21*#0##", "*4*31*#0##", "*4*21*#0##"])
    timers.pop()(None)
    assert central.extra_state_attributes["battery_fault"] is True


def test_flag_cycle_timer_is_cancelled_on_removal(timers: list[Any]) -> None:
    central, _writes = _central_without_battery(timers)
    unsub = MagicMock()
    with patch("custom_components.myhome.climate.async_call_later", return_value=unsub):
        _replay(central, ["*4*21*#0##"])
    central._cancel_central_flag_cycle()
    unsub.assert_called_once()
    central._cancel_central_flag_cycle()  # idempotent
    unsub.assert_called_once()


# B: the F455's generic *4*311*#4## is not the zone's state when its seasonal status follows.


@pytest.mark.parametrize("window", ["quarter_0745", "quarter_0800"])
def test_f455_quarter_hour_push_does_not_turn_zone_4_auto(timers: list[Any], window: str) -> None:
    zone4, writes = _climate(_gateway(), "4", fan=True)
    zone4._attr_hvac_mode = HVACMode.COOL
    frames = _rx(window, "f455")
    assert frames.index("*4*311*#4##") == frames.index("*4*210*#4##") - 1
    _replay(zone4, frames, twice=True)
    assert timers == []  # the held 311 was dropped by *4*210*#4##
    assert zone4.hvac_mode == HVACMode.COOL
    assert all(w["hvac_mode"] == HVACMode.COOL for w in writes)
    assert zone4.target_temperature == 24.0


def test_f461_quarter_hour_push_was_already_right(timers: list[Any]) -> None:
    zone4, writes = _climate(_gateway(), "4", fan=True)
    zone4._attr_hvac_mode = HVACMode.COOL
    frames = _rx("quarter_0745", "f461")
    assert not any(raw.startswith("*4*311*") for raw in frames)
    _replay(zone4, frames)
    assert timers == []
    assert all(w["hvac_mode"] == HVACMode.COOL for w in writes)


@pytest.mark.parametrize(
    ("raw", "mode"),
    [
        ("*4*210*#4##", HVACMode.COOL),
        ("*4*110*#4##", HVACMode.HEAT),
        ("*4*111*#4##", HVACMode.HEAT),  # unchanged (#819): OWNd maps 111 to heat
        ("*4*203*#4##", HVACMode.OFF),
    ],
)
def test_seasonal_zone_statuses_of_the_central_still_set_the_mode(
    timers: list[Any], raw: str, mode: HVACMode
) -> None:
    zone4, _writes = _climate(_gateway(), "4", fan=True)
    zone4._attr_hvac_mode = HVACMode.AUTO
    _replay(zone4, [raw])
    assert zone4.hvac_mode == mode
    assert timers == []


@pytest.mark.parametrize("seasonal", ["*4*210*#4##", "*4*203*#4##", "*4*202*#4##", "*4*0*4##"])
def test_a_seasonal_status_after_the_generic_frame_replaces_it(
    timers: list[Any], seasonal: str
) -> None:
    zone4, writes = _climate(_gateway(), "4", fan=True)
    zone4._attr_hvac_mode = HVACMode.COOL
    _replay(zone4, ["*4*311*#4##"])
    assert len(timers) == 1 and writes == []  # held, nothing written
    _replay(zone4, [seasonal])
    assert timers == []
    assert all(w["hvac_mode"] != HVACMode.AUTO for w in writes)


def test_a_generic_frame_with_no_seasonal_status_is_the_zone_state(timers: list[Any]) -> None:
    # WHO 4 p. 9: 311 is also a monitor frame; a central that reports only it still shows AUTO.
    zone4, writes = _climate(_gateway(), "4", fan=True)
    zone4._attr_hvac_mode = HVACMode.COOL
    _replay(zone4, ["*4*311*#4##"])
    assert zone4.hvac_mode == HVACMode.COOL
    timers.pop()(None)
    assert zone4.hvac_mode == HVACMode.AUTO
    assert writes[-1]["hvac_mode"] == HVACMode.AUTO


def test_a_newer_generic_frame_replaces_the_held_one(timers: list[Any]) -> None:
    zone4, _writes = _climate(_gateway(), "4", fan=True)
    zone4._attr_hvac_mode = HVACMode.COOL
    _replay(zone4, ["*4*311*#4##", "*4*311*#4##"])
    assert len(timers) == 1
    zone4._cancel_generic_zone_frame()  # entity removed
    assert timers == []
    zone4._cancel_generic_zone_frame()  # idempotent
    assert zone4.hvac_mode == HVACMode.COOL


def test_a_generic_frame_before_the_entity_is_live_is_applied_at_once() -> None:
    zone4, _writes = _climate(_gateway(), "4", fan=True)
    zone4.platform = None
    zone4._attr_hvac_mode = HVACMode.COOL
    _replay(zone4, ["*4*311*#4##"])
    assert zone4.hvac_mode == HVACMode.AUTO


@pytest.mark.parametrize("raw", ["*4*311*#4##", "*4*310*#4##"])
def test_generic_zone_frames_do_not_mark_a_central(raw: str) -> None:
    assert is_central_unit_report(OWNEvent.parse(raw)) is False


def test_the_central_itself_still_takes_its_generic_frame() -> None:
    # *4*311*#0## on the central entity is handled by the central path (echo filter, #880).
    central, _writes = _climate(_gateway(), "#0", central=True)
    central._attr_hvac_mode = HVACMode.OFF
    assert central._is_generic_central_zone_frame(OWNEvent.parse("*4*311*#0##")) is False


# C: the central echo window starts when the command is written.


async def _set_central_mode(central: Any, future: Any, hvac_mode: HVACMode = HVACMode.OFF) -> None:
    central._gateway_handler.send = AsyncMock(return_value=future)
    await central.async_set_hvac_mode(hvac_mode)


async def test_echo_while_the_command_is_still_queued_is_swallowed() -> None:
    central, _writes = _climate(_gateway(), "#0", central=True)
    central._attr_hvac_mode = HVACMode.COOL
    written: asyncio.Future[float] = asyncio.get_running_loop().create_future()
    await _set_central_mode(central, written)
    start = climate_module.time.monotonic()
    with patch("custom_components.myhome.climate.time.monotonic", return_value=start + 5.0):
        assert central._is_own_central_echo(OWNEvent.parse("*4*303*#0##")) is True


async def test_window_is_anchored_on_the_write_time_of_a_busy_pool() -> None:
    central, _writes = _climate(_gateway(), "#0", central=True)
    central._attr_hvac_mode = HVACMode.COOL
    written: asyncio.Future[float] = asyncio.get_running_loop().create_future()
    await _set_central_mode(central, written)
    written_at = climate_module.time.monotonic() + 5.0  # the pool wrote it 5 s after the call
    written.set_result(written_at)
    await asyncio.sleep(0)
    with patch("custom_components.myhome.climate.time.monotonic", return_value=written_at + 0.05):
        assert central._is_own_central_echo(OWNEvent.parse("*4*303*#0##")) is True
    with patch("custom_components.myhome.climate.time.monotonic", return_value=written_at + 0.15):
        assert central._is_own_central_echo(OWNEvent.parse("*4*303*#0##")) is False


async def test_identical_frame_after_the_anchored_window_is_a_report() -> None:
    central, _writes = _climate(_gateway(), "#0", central=True)
    central._attr_hvac_mode = HVACMode.COOL
    written: asyncio.Future[float] = asyncio.get_running_loop().create_future()
    await _set_central_mode(central, written)
    written_at = climate_module.time.monotonic()
    written.set_result(written_at)
    await asyncio.sleep(0)
    with patch("custom_components.myhome.climate.time.monotonic", return_value=written_at + 0.121):
        assert central._is_own_central_echo(OWNEvent.parse("*4*303*#0##")) is False
    assert central._central_echo is None


async def test_a_command_never_written_disarms_the_echo() -> None:
    central, _writes = _climate(_gateway(), "#0", central=True)
    central._attr_hvac_mode = HVACMode.COOL
    written: asyncio.Future[float] = asyncio.get_running_loop().create_future()
    await _set_central_mode(central, written)
    written.cancel()
    await asyncio.sleep(0)
    assert central._central_echo is None
    assert central._is_own_central_echo(OWNEvent.parse("*4*303*#0##")) is False


async def test_a_failed_write_disarms_the_echo() -> None:
    central, _writes = _climate(_gateway(), "#0", central=True)
    central._attr_hvac_mode = HVACMode.COOL
    written: asyncio.Future[float] = asyncio.get_running_loop().create_future()
    await _set_central_mode(central, written)
    written.set_exception(OSError("socket closed"))
    await asyncio.sleep(0)
    assert central._central_echo is None


async def test_a_pending_echo_expires_after_the_pool_gives_up() -> None:
    central, _writes = _climate(_gateway(), "#0", central=True)
    central._attr_hvac_mode = HVACMode.COOL
    written: asyncio.Future[float] = asyncio.get_running_loop().create_future()
    await _set_central_mode(central, written)
    later = climate_module.time.monotonic() + climate_module._CENTRAL_ECHO_PENDING_LIMIT + 1
    with patch("custom_components.myhome.climate.time.monotonic", return_value=later):
        assert central._is_own_central_echo(OWNEvent.parse("*4*303*#0##")) is False
    written.cancel()


async def test_a_newer_command_is_not_reanchored_by_an_older_future() -> None:
    central, _writes = _climate(_gateway(), "#0", central=True)
    central._attr_hvac_mode = HVACMode.COOL
    first: asyncio.Future[float] = asyncio.get_running_loop().create_future()
    await _set_central_mode(central, first, HVACMode.OFF)
    second: asyncio.Future[float] = asyncio.get_running_loop().create_future()
    await _set_central_mode(central, second, HVACMode.AUTO)
    first.set_result(climate_module.time.monotonic() - 10)  # the OFF frame's late result
    await asyncio.sleep(0)
    assert central._central_echo is not None
    assert central._central_echo[0] == str(OWNHeatingCommand.set_central_mode("#0", "auto"))
    assert central._central_echo[0] in {"*4*311*#0##", "*4*3100*#0##"}
    assert central._central_echo[1] is None  # still waiting for its own write
    second.cancel()


async def test_without_a_future_the_window_starts_at_the_call() -> None:
    central, _writes = _climate(_gateway(), "#0", central=True)
    central._attr_hvac_mode = HVACMode.COOL
    await _set_central_mode(central, None)
    echo = central._central_echo
    assert echo is not None and echo[1] is not None
    assert echo[1] - climate_module.time.monotonic() <= climate_module._CENTRAL_ECHO_WINDOW


async def test_the_same_command_twice_is_anchored_on_its_own_write() -> None:
    central, _writes = _climate(_gateway(), "#0", central=True)
    central._attr_hvac_mode = HVACMode.COOL
    first: asyncio.Future[float] = asyncio.get_running_loop().create_future()
    second: asyncio.Future[float] = asyncio.get_running_loop().create_future()
    with patch("custom_components.myhome.climate.time.monotonic", return_value=100.0):
        await _set_central_mode(central, first, HVACMode.OFF)
    with patch("custom_components.myhome.climate.time.monotonic", return_value=101.0):
        await _set_central_mode(central, second, HVACMode.OFF)
    first.set_result(100.0)  # the first OFF's write: its window ended long ago
    await asyncio.sleep(0)
    assert central._central_echo is not None
    assert central._central_echo[1] is None  # the second OFF still waits for its own write
    second.set_result(106.0)
    await asyncio.sleep(0)
    with patch("custom_components.myhome.climate.time.monotonic", return_value=106.05):
        assert central._is_own_central_echo(OWNEvent.parse("*4*303*#0##")) is True


def test_a_zone_entity_leaves_the_central_s_own_generic_frame_to_the_central_path() -> None:
    zone4, _writes = _climate(_gateway(), "4", fan=True)
    assert zone4._is_generic_central_zone_frame(OWNEvent.parse("*4*311*#0##")) is False
    assert zone4._is_generic_central_zone_frame(OWNEvent.parse("*4*311*#4##")) is True
