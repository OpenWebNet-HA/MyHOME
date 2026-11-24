"""Impulse cover and access controller: the cases of the PR 986 review.

Every cover here is built with values ``validate.cover_schema`` accepts (``min_cycle_time``
1 - 120 s, default 5 s; ``pulse_duration`` 0.2 - 2 s), and the gateway double hands back one
delivery future per frame, resolved with the monotonic write time like the command worker's
(``gateway_sessions._resolve_written``) or cancelled like a frame it gave up on
(``gateway_sessions._cancel_written``).
"""

from __future__ import annotations

import asyncio
import json
import time
from datetime import timedelta
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from homeassistant.components.cover import CoverDeviceClass
from homeassistant.const import CONF_HOST, CONF_MAC, CONF_PASSWORD, CONF_PORT, STATE_OFF, STATE_ON
from homeassistant.core import Context, HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.util import dt as dt_util
from OWNd.message import OWNMessage
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    async_fire_time_changed_exact,
    async_mock_service,
)
from voluptuous import Invalid

from custom_components.myhome import cover as cover_module
from custom_components.myhome.access_policy import AccessController, Intent, Phase, PulseOutcome
from custom_components.myhome.const import DOMAIN
from custom_components.myhome.cover import OFF_SETTLE_DELAY, MyHOMEImpulseCover
from custom_components.myhome.gateway import MyHOMEGatewayHandler
from custom_components.myhome.validate import cover_schema
from tests.test_access_policy import SENSOR, _approve_action, _config, _press, _user, _wait_phase

TIMED = "*1*18*24##"  # hardware-timed pulse, pulse_duration 0.5 s
ON = "*1*1*24##"
OFF = "*1*0*24##"


class _Bus:
    """A gateway handler double: one delivery future per frame, held or confirmed at once."""

    def __init__(self, hass: HomeAssistant) -> None:
        self.hass = hass
        self.frames: list[str] = []
        self.futures: list[asyncio.Future[float]] = []
        self.queued_at: list[float] = []
        self.hold = False
        self.gateway = MagicMock()
        self.gateway.mac = "00:03:50:00:88:88"
        self.gateway.unique_id = "00:03:50:00:88:88"
        self.gateway.log_id = "[Test Gateway]"
        self.gateway.device_registry_id = "mock_gw_dev_id"
        self.gateway.available = True
        self.gateway.availability_signal = "myhome_gateway_availability_000350008888"
        self.gateway.send = AsyncMock(side_effect=self._send)

    async def _send(self, message: Any) -> asyncio.Future[float]:
        fut: asyncio.Future[float] = self.hass.loop.create_future()
        self.frames.append(str(message))
        self.futures.append(fut)
        self.queued_at.append(time.monotonic())
        if not self.hold:
            fut.set_result(time.monotonic())
        return fut

    def confirm(self, index: int = -1) -> None:
        self.futures[index].set_result(time.monotonic())

    def reject(self, index: int = -1) -> None:
        """What the worker does with a NACKed, timed-out or unwritten frame."""
        self.futures[index].cancel()

    async def until(self, count: int) -> None:
        for _ in range(400):
            if len(self.frames) >= count:
                return
            await asyncio.sleep(0.005)
        raise AssertionError(f"{len(self.frames)} frame(s) queued, expected {count}: {self.frames}")


@pytest.fixture
def bus(hass: HomeAssistant) -> _Bus:
    return _Bus(hass)


@pytest.fixture
def make_gate(hass: HomeAssistant, bus: _Bus):
    built: list[MyHOMEImpulseCover] = []

    def build(
        *,
        state_sensor: str | None = None,
        pulse_duration: float = 0.5,
        min_cycle_time: float = 5.0,
        access: bool = False,
    ) -> MyHOMEImpulseCover:
        with patch("custom_components.myhome.myhome_device.Entity.__init__", return_value=None):
            gate = MyHOMEImpulseCover(
                hass=hass,
                name="Garage",
                entity_name=None,
                device_id="24",
                who="1",
                where="24",
                interface=None,
                device_class=CoverDeviceClass.GARAGE,
                travel_time=10.0,
                pulse_duration=pulse_duration,
                min_cycle_time=min_cycle_time,
                pin_code=None,
                state_sensor=state_sensor,
                manufacturer="BTicino",
                model="Impulse Cover",
                gateway=bus.gateway,
                access_config={} if access else None,
            )
        gate.hass = hass
        gate.entity_id = "cover.garage"
        gate.async_write_ha_state = MagicMock()
        built.append(gate)
        return gate

    yield build
    for gate in built:
        gate._cancel_travel_timer()
        if gate._pulse_off_cancel is not None:
            gate._pulse_off_cancel()


async def _supervised(hass: HomeAssistant, make_gate, *, contact: str = STATE_OFF, **kwargs):
    """A gate with `access:` and a contact sensor, its user and the notify calls."""
    gate = make_gate(access=True, state_sensor=SENSOR, **kwargs)
    user = await _user(hass, "Parent")
    gate._context = Context(user_id=user.id)
    gate._access.config = _config(user.id)
    hass.states.async_set(SENSOR, contact)
    gate._handle_sensor_update(contact)
    calls = async_mock_service(hass, "notify", "mobile_app_parent")
    return gate, user, calls


def _relay_on(gate: MyHOMEImpulseCover) -> None:
    gate.handle_event(OWNMessage.parse(ON))


# ── 1. A stop that waited for the approved write stops the door ──────────


async def test_stop_that_waited_for_the_approved_write_stops_the_door(hass: HomeAssistant, bus: _Bus, make_gate):
    """With `access:` and the default 5 s deadband the waiting stop pulses once the relay is released."""
    gate, user, calls = await _supervised(hass, make_gate)
    bus.hold = True
    await gate.async_open_cover()
    await _press(hass, _approve_action(calls)["action"], user.id, wait=False)
    await bus.until(1)

    stop = asyncio.create_task(gate.async_stop_cover())
    await asyncio.sleep(0)
    bus.hold = False
    confirmed_at = time.monotonic()
    bus.confirm(0)
    await stop

    assert bus.frames == [TIMED, TIMED]
    assert bus.queued_at[1] - confirmed_at >= 0.5 + OFF_SETTLE_DELAY - 0.02, "the relay had not been released yet"
    assert gate._access.phase is Phase.IDLE
    assert gate.is_opening is False
    await gate._access.async_shutdown()


async def test_two_stops_of_a_supervised_door_send_one_pulse(hass: HomeAssistant, bus: _Bus, make_gate):
    """A second stop while the first is being written is the same stop: no error, no second pulse."""
    gate, user, calls = await _supervised(hass, make_gate, contact=STATE_ON)
    await gate.async_close_cover()
    await _press(hass, _approve_action(calls)["action"], user.id)
    await _wait_phase(gate._access, Phase.MONITORING)
    assert bus.frames == [TIMED]

    bus.hold = True
    first = asyncio.create_task(gate.async_stop_cover())
    await bus.until(2)
    second = asyncio.create_task(gate.async_stop_cover())
    await asyncio.sleep(0)
    bus.confirm(1)
    await asyncio.gather(first, second)

    assert bus.frames == [TIMED, TIMED]
    assert gate._access.phase is Phase.IDLE
    assert sum("Garage: stopped" in str(c.data.get("message", "")) for c in calls) == 1
    await gate._access.async_shutdown()


# ── 2. A write the gateway did not confirm ───────────────────────────────


async def test_unconfirmed_open_does_not_withhold_the_stop(hass: HomeAssistant, bus: _Bus, make_gate):
    """The relay may have switched: the contact still reads closed, and STOP goes out all the same."""
    gate = make_gate(state_sensor=SENSOR)
    gate._handle_sensor_update(STATE_OFF)
    bus.hold = True
    opening = asyncio.create_task(gate.async_open_cover())
    await bus.until(1)
    bus.reject(0)
    with pytest.raises(HomeAssistantError, match="pulse was not confirmed"):
        await opening
    assert gate.is_opening is False

    bus.hold = False
    await gate.async_stop_cover()
    assert bus.frames == [TIMED, TIMED]


async def test_unconfirmed_approved_pulse_is_supervised_and_not_called_dropped(hass: HomeAssistant, bus: _Bus, make_gate):
    """With `access:` the door is watched as if it had started, and the phone is told the truth."""
    gate, user, calls = await _supervised(hass, make_gate)
    bus.hold = True
    await gate.async_open_cover()
    await _press(hass, _approve_action(calls)["action"], user.id, wait=False)
    await bus.until(1)
    bus.reject(0)
    await _wait_phase(gate._access, Phase.MONITORING)
    await hass.async_block_till_done()

    messages = [str(c.data.get("message", "")) for c in calls]
    assert not any("Not executed" in m or "dropped" in m for m in messages)
    assert any("did not confirm" in m for m in messages)

    # The relay's own status right after the failed write is this pulse, not a wall button.
    _relay_on(gate)
    assert gate._access.phase is Phase.MONITORING

    # A stop is immediate under supervision, and goes out: the door may be moving.
    bus.hold = False
    await gate.async_stop_cover()
    assert bus.frames == [TIMED, TIMED]
    assert gate._access.phase is Phase.IDLE
    await gate._access.async_shutdown()


async def test_stop_that_waited_for_an_unconfirmed_write_is_not_a_silent_success(hass: HomeAssistant, bus: _Bus, make_gate):
    """ "It was not sent, nothing moves" is only true for a pulse that never left Home Assistant."""
    gate, user, calls = await _supervised(hass, make_gate)
    bus.hold = True
    await gate.async_open_cover()
    await _press(hass, _approve_action(calls)["action"], user.id, wait=False)
    await bus.until(1)
    stop = asyncio.create_task(gate.async_stop_cover())
    await asyncio.sleep(0)
    bus.hold = False
    bus.reject(0)
    await stop

    assert bus.frames == [TIMED, TIMED]
    await gate._access.async_shutdown()


async def test_unconfirmed_close_that_does_not_close_latches_no_fault(hass: HomeAssistant, bus: _Bus, make_gate):
    """A fault means an obstacle; a pulse nobody confirmed that moved nothing is reported, not latched."""
    gate, user, calls = await _supervised(hass, make_gate, contact=STATE_ON)
    bus.hold = True
    await gate.async_close_cover()
    await _press(hass, _approve_action(calls)["action"], user.id, wait=False)
    await bus.until(1)
    bus.reject(0)
    await _wait_phase(gate._access, Phase.MONITORING)

    async_fire_time_changed_exact(hass, dt_util.utcnow() + timedelta(seconds=10.0 + 5.0 + 1.0))
    await hass.async_block_till_done()
    assert gate._access.phase is Phase.IDLE
    assert gate._access.fault_reason is None
    assert any("did not close" in str(c.data.get("message", "")) for c in calls)
    await gate._access.async_shutdown()


# ── 3. stop_cover of a direct cover under a schema-valid deadband ────────


async def test_direct_stop_right_after_open_goes_out_inside_the_deadband(hass: HomeAssistant, bus: _Bus, make_gate):
    """The deadband is for a double tap; a stop of the door Home Assistant just started is not one."""
    gate = make_gate()
    await gate.async_open_cover()
    started = time.monotonic()
    assert gate.is_opening is True

    await gate.async_stop_cover()
    assert bus.frames == [TIMED, TIMED]
    assert bus.queued_at[1] - started >= 0.5 + OFF_SETTLE_DELAY - 0.02
    assert gate.is_opening is False

    # A second stop inside the deadband is the stop that was just sent: a third pulse would start the door again.
    await gate.async_stop_cover()
    assert bus.frames == [TIMED, TIMED]
    # Open and close keep the deadband.
    with pytest.raises(HomeAssistantError, match="pulse was not sent") as err:
        await gate.async_open_cover()
    assert err.value.translation_key == "impulse_pulse_not_sent"
    assert bus.frames == [TIMED, TIMED]


async def test_direct_stop_during_the_open_write_waits_and_stops(hass: HomeAssistant, bus: _Bus, make_gate):
    """A stop that arrives while the open pulse is with the gateway is neither refused nor lost."""
    gate = make_gate(state_sensor=SENSOR)
    gate._handle_sensor_update(STATE_OFF)
    bus.hold = True
    opening = asyncio.create_task(gate.async_open_cover())
    await bus.until(1)
    stop = asyncio.create_task(gate.async_stop_cover())
    again = asyncio.create_task(gate.async_stop_cover())
    await asyncio.sleep(0)
    bus.hold = False
    bus.confirm(0)
    await asyncio.gather(opening, stop, again)

    assert bus.frames == [TIMED, TIMED]
    assert gate.is_opening is False


async def test_direct_stop_that_waited_for_a_pulse_that_was_not_sent_sends_nothing(
    hass: HomeAssistant, bus: _Bus, make_gate, monkeypatch
):
    """The open never left Home Assistant: the door is closed and at rest, a stop pulse would open it."""
    monkeypatch.setattr(cover_module, "OFF_ATTEMPTS", 1)
    gate = make_gate(state_sensor=SENSOR, pulse_duration=1.0)
    gate._handle_sensor_update(STATE_OFF)
    gate._off_pending = True  # the OFF of an earlier pulse is still owed
    bus.hold = True
    opening = asyncio.create_task(gate.async_open_cover())
    await bus.until(1)
    assert bus.frames == [OFF]
    stop = asyncio.create_task(gate.async_stop_cover())
    await asyncio.sleep(0)
    bus.reject(0)
    with pytest.raises(HomeAssistantError, match="could not be released") as err:
        await opening
    assert err.value.translation_key == "impulse_relay_not_released"
    assert "not confirmed by the gateway" not in str(err.value)
    await stop
    assert bus.frames == [OFF]


async def test_direct_stop_that_joined_an_unconfirmed_stop_shares_its_error(hass: HomeAssistant, bus: _Bus, make_gate):
    """The first stop may have stopped the door: a second pulse for the same stop could start it again."""
    gate = make_gate()
    bus.hold = True
    first = asyncio.create_task(gate.async_stop_cover())
    await bus.until(1)
    second = asyncio.create_task(gate.async_stop_cover())
    await asyncio.sleep(0)
    bus.reject(0)
    results = await asyncio.gather(first, second, return_exceptions=True)

    assert bus.frames == [TIMED]
    assert [getattr(r, "translation_key", None) for r in results] == ["impulse_pulse_unconfirmed"] * 2


async def test_unconfirmed_stop_of_a_supervised_door_keeps_the_watchdog(hass: HomeAssistant, bus: _Bus, make_gate):
    """Nobody knows whether the door stopped: the error says so and the supervision goes on."""
    gate, user, calls = await _supervised(hass, make_gate, contact=STATE_ON)
    await gate.async_close_cover()
    await _press(hass, _approve_action(calls)["action"], user.id)
    await _wait_phase(gate._access, Phase.MONITORING)

    bus.hold = True
    stop = asyncio.create_task(gate.async_stop_cover())
    await bus.until(2)
    bus.reject(1)
    with pytest.raises(HomeAssistantError, match="stop pulse was not confirmed") as err:
        await stop
    assert err.value.translation_key == "impulse_stop_unconfirmed"
    assert gate._access.phase is Phase.MONITORING
    await gate._access.async_shutdown()


async def test_stop_that_joined_an_unconfirmed_approved_stop_shares_its_error(hass: HomeAssistant, bus: _Bus, make_gate):
    """With `access:` the stop that waited for an approved stop gets that stop's fate, and no approval request."""
    gate, user, calls = await _supervised(hass, make_gate, contact=STATE_ON)
    bus.hold = True
    await gate.async_stop_cover()  # a stop of a door not known to move needs an approval
    await _press(hass, _approve_action(calls)["action"], user.id, wait=False)
    await bus.until(1)
    waiting = asyncio.create_task(gate.async_stop_cover())
    await asyncio.sleep(0)
    bus.reject(0)
    with pytest.raises(HomeAssistantError, match="stop pulse was not confirmed"):
        await waiting
    await hass.async_block_till_done()

    assert bus.frames == [TIMED]
    assert gate._access.phase is Phase.IDLE
    await gate._access.async_shutdown()


# ── 4. Software pulse: the OFF ─────────────────────────────────────────────


async def test_software_pulse_is_timed_from_the_write_not_from_the_queue(hass: HomeAssistant, bus: _Bus, make_gate):
    """An ON that waits in the queue longer than `pulse_duration` is not followed at once by its OFF."""
    gate = make_gate(pulse_duration=1.0)
    bus.hold = True
    opening = asyncio.create_task(gate.async_open_cover())
    await bus.until(1)
    async_fire_time_changed_exact(hass, dt_util.utcnow() + timedelta(seconds=2))
    await asyncio.sleep(0.01)
    assert bus.frames == [ON], "the OFF was queued behind an ON that is not on the bus yet"

    bus.hold = False
    bus.confirm(0)
    await opening
    assert bus.frames == [ON]
    async_fire_time_changed_exact(hass, dt_util.utcnow() + timedelta(seconds=0.5))
    await hass.async_block_till_done()
    assert bus.frames == [ON]
    async_fire_time_changed_exact(hass, dt_util.utcnow() + timedelta(seconds=1.1))
    await hass.async_block_till_done()
    assert bus.frames == [ON, OFF]
    assert gate._off_pending is False


async def test_running_off_is_awaited_before_the_next_pulse(hass: HomeAssistant, bus: _Bus, make_gate):
    """One OFF at a time: a later pulse does not start while the OFF of the pulse before is still retried."""
    gate = make_gate(pulse_duration=1.0, min_cycle_time=1.5)
    await gate.async_open_cover()
    bus.hold = True
    async_fire_time_changed_exact(hass, dt_util.utcnow() + timedelta(seconds=1.1))
    await bus.until(2)
    assert bus.frames == [ON, OFF]

    gate._last_pulse_time -= 2.0  # past the deadband and the relay hold
    stopping = asyncio.create_task(gate.async_stop_cover())
    await asyncio.sleep(0.01)
    assert bus.frames == [ON, OFF], "a second OFF was written next to the one still on its way"
    bus.hold = False
    bus.confirm(1)
    await stopping
    assert bus.frames == [ON, OFF, ON]


async def test_removal_waits_for_the_running_off(hass: HomeAssistant, bus: _Bus, make_gate):
    """The OFF task is the cover's: removal waits for it instead of writing a second OFF and leaving it behind."""
    gate = make_gate(pulse_duration=1.0)
    await gate.async_open_cover()
    bus.hold = True
    async_fire_time_changed_exact(hass, dt_util.utcnow() + timedelta(seconds=1.1))
    await bus.until(2)

    removal = asyncio.create_task(gate.async_will_remove_from_hass())
    await asyncio.sleep(0.01)
    assert not removal.done()
    assert bus.frames == [ON, OFF]
    bus.confirm(1)
    await removal
    assert bus.frames == [ON, OFF]
    assert gate._off_pending is False


async def test_pulse_cancelled_while_it_releases_the_relay_keeps_the_off_going(hass: HomeAssistant, bus: _Bus, make_gate):
    """A service call cancelled during the pre-pulse OFF does not leave the relay owed with nobody to send it."""
    gate = make_gate(pulse_duration=1.0)
    gate._off_pending = True
    bus.hold = True
    opening = asyncio.create_task(gate.async_open_cover())
    await bus.until(1)
    bus.hold = False
    opening.cancel()
    with pytest.raises(asyncio.CancelledError):
        await opening
    bus.reject(0)
    await hass.async_block_till_done()
    assert bus.frames == [OFF, OFF]
    assert gate._off_pending is False


# ── 5. Errors carry translation keys ───────────────────────────────────────


async def test_unconfirmed_pulse_error_is_translated(hass: HomeAssistant, bus: _Bus, make_gate):
    gate = make_gate()
    bus.hold = True
    opening = asyncio.create_task(gate.async_open_cover())
    await bus.until(1)
    bus.reject(0)
    with pytest.raises(HomeAssistantError, match="pulse was not confirmed") as err:
        await opening
    assert err.value.translation_key == "impulse_pulse_unconfirmed"
    assert err.value.translation_domain == "myhome"


def test_impulse_error_keys_exist_in_every_language():
    root = Path("custom_components/myhome")
    keys = {
        "impulse_pulse_not_sent",
        "impulse_pulse_send_failed",
        "impulse_pulse_unconfirmed",
        "impulse_relay_not_released",
        "impulse_stop_not_sent",
        "impulse_stop_unconfirmed",
    }
    for name in ("strings.json", "translations/en.json", "translations/fr.json", "translations/it.json", "translations/nl.json"):
        defined = set(json.loads((root / name).read_text(encoding="utf-8"))["exceptions"])
        assert not keys - defined, f"{name} lacks {sorted(keys - defined)}"


# ── 6. Delivery confirmation ───────────────────────────────────────────────


async def test_awaitable_that_is_not_a_future_times_out_with_a_reason(hass: HomeAssistant, bus: _Bus, make_gate, monkeypatch):
    """A bare awaitable gets the same wait as a future: a message, not `()`."""
    gate = make_gate()
    release = asyncio.Event()

    async def never() -> float:
        await release.wait()
        return 0.0

    bus.gateway.send = AsyncMock(side_effect=lambda message: never())
    monkeypatch.setattr(cover_module, "IMPULSE_WRITE_TIMEOUT", 0.05)
    with pytest.raises(HomeAssistantError, match=r"not confirmed by the gateway within"):
        await gate.async_open_cover()
    release.set()


async def test_off_awaitable_is_not_cancelled_by_the_timeout(hass: HomeAssistant, bus: _Bus, make_gate, monkeypatch):
    """An OFF is never withdrawn, whatever kind of awaitable the handler returned."""
    gate = make_gate(pulse_duration=1.0)
    gate._off_pending = True
    release = asyncio.Event()
    cancelled = False

    async def slow() -> float:
        nonlocal cancelled
        try:
            await release.wait()
        except asyncio.CancelledError:
            cancelled = True
            raise
        return 0.0

    bus.gateway.send = AsyncMock(side_effect=lambda message: slow())
    monkeypatch.setattr(cover_module, "IMPULSE_WRITE_TIMEOUT", 0.05)
    monkeypatch.setattr(cover_module, "OFF_ATTEMPTS", 1)
    assert await gate._async_send_off() is False
    assert cancelled is False
    release.set()
    await asyncio.sleep(0.01)


async def test_software_on_that_was_never_queued_owes_no_off(hass: HomeAssistant, bus: _Bus, make_gate):
    """`send()` raised before the frame was queued: nothing reached the relay, no OFF is retried."""
    gate = make_gate(pulse_duration=1.0)
    bus.gateway.send = AsyncMock(side_effect=RuntimeError("command queue is closed"))
    with pytest.raises(HomeAssistantError, match="pulse was not sent .*command queue is closed") as err:
        await gate.async_open_cover()
    assert err.value.translation_key == "impulse_pulse_send_failed"
    assert gate._off_pending is False
    assert gate._pulse_off_cancel is None


# ── 7. Echo window, schema, contact, tasks ─────────────────────────────────


async def test_status_read_back_during_a_long_pulse_is_not_a_wall_button(hass: HomeAssistant, bus: _Bus, make_gate):
    """`pulse_duration` 2.0 s: the relay is still closed 1.6 s after the write (`gateway_confirm` reads back at 1.5 s)."""
    gate, user, calls = await _supervised(hass, make_gate, contact=STATE_ON, pulse_duration=2.0)
    await gate.async_close_cover()
    await _press(hass, _approve_action(calls)["action"], user.id)
    await _wait_phase(gate._access, Phase.MONITORING)

    gate._last_pulse_time -= 1.6
    _relay_on(gate)
    assert gate._access.phase is Phase.MONITORING

    gate._last_pulse_time -= 1.0  # 2.6 s after the write the relay is released: this one is a button
    _relay_on(gate)
    assert gate._access.phase is Phase.IDLE
    await gate.async_will_remove_from_hass()


def test_travel_time_0_is_rejected_for_an_impulse_cover():
    with pytest.raises(Invalid, match="travel_time"):
        cover_schema({"garage": {"name": "Garage", "who": "1", "where": "24", "travel_time": 0}})
    assert cover_schema({"garage": {"name": "Garage", "who": "1", "where": "24"}})["1-24"]["travel_time"] == 25


async def test_contact_back_to_closed_from_unknown_forgets_the_wall_button(hass: HomeAssistant, bus: _Bus, make_gate):
    """on -> unavailable -> off: the door came to closed, an earlier relay actuation is over."""
    gate = make_gate(state_sensor=SENSOR)
    gate._handle_sensor_update(STATE_ON)
    _relay_on(gate)  # wall button closes the door
    gate._handle_sensor_update("unavailable")
    gate._handle_sensor_update(STATE_OFF)

    await gate.async_stop_cover()
    assert bus.frames == []


async def test_shutdown_cancels_every_task_the_controller_started(hass: HomeAssistant):
    started = 0
    cancelled = 0

    async def _long() -> None:
        nonlocal started, cancelled
        started += 1
        try:
            await asyncio.sleep(60)
        except asyncio.CancelledError:
            cancelled += 1
            raise

    user = await _user(hass, "Parent")

    async def send_pulse(intent: Intent, effect: Any) -> PulseOutcome:
        return PulseOutcome.CONFIRMED

    ctrl = AccessController(
        hass,
        name="Garage 1",
        config=_config(user.id),
        state_sensor=SENSOR,
        travel_time=15.0,
        send_pulse=send_pulse,
        publish=lambda: None,
        entity_id=lambda: "cover.garage_1",
    )
    ctrl._spawn(_long())
    ctrl._spawn(_long())
    await asyncio.sleep(0)
    assert started == 2
    await ctrl.async_shutdown()
    assert cancelled == 2


async def test_stop_after_wall_button_or_sensor_change_is_not_swallowed_by_prior_stop(
    hass: HomeAssistant, bus: _Bus, make_gate
):
    """After a STOP inside min_cycle_time, a wall button or sensor change clears _last_pulse_intent so the next STOP goes out."""
    gate = make_gate(state_sensor=SENSOR, min_cycle_time=60.0)
    gate._handle_sensor_update(STATE_ON)
    await gate.async_stop_cover()
    assert bus.frames == [TIMED]

    # Wall button restarts the door 3 s later (outside echo window, inside 60 s min_cycle_time)
    gate._last_pulse_time -= 3.0
    _relay_on(gate)
    assert gate._last_pulse_intent is None
    await gate.async_stop_cover()
    assert bus.frames == [TIMED, TIMED]

    # Radio remote opens the door (sensor flips off -> on) inside min_cycle_time
    gate._last_pulse_time -= 3.0
    gate._handle_sensor_update(STATE_OFF)
    gate._handle_sensor_update(STATE_ON)
    assert gate._last_pulse_intent is None
    await gate.async_stop_cover()
    assert bus.frames == [TIMED, TIMED, TIMED]


async def test_access_controller_cancel_during_user_lookup_prevents_pulse(
    hass: HomeAssistant, bus: _Bus, make_gate
):
    """A STOP or wall button arriving while _async_execute awaits _user_name cancels the pulse instead of letting it fire."""
    gate, user, calls = await _supervised(hass, make_gate, contact=STATE_OFF)
    ctrl = gate._access
    assert ctrl is not None
    await ctrl.async_request(Intent.OPEN, Context(user_id=user.id))

    in_lookup = asyncio.Event()
    resume_lookup = asyncio.Event()
    real_user_name = ctrl._user_name

    async def _slow_user_name(uid: str) -> str:
        in_lookup.set()
        await resume_lookup.wait()
        return await real_user_name(uid)

    ctrl._user_name = _slow_user_name  # type: ignore[method-assign]
    await _press(hass, _approve_action(calls)["action"], user.id, wait=False)
    await asyncio.wait_for(in_lookup.wait(), timeout=2.0)

    # Cancel arrives in the pre-write gap before _pulse_outcome is armed
    assert ctrl.cancel("wall button pressed") is True
    resume_lookup.set()
    await _wait_phase(ctrl, Phase.IDLE)
    assert bus.frames == []


async def test_unexpected_exception_before_send_does_not_mark_unconfirmed(
    hass: HomeAssistant, bus: _Bus, make_gate
):
    """An exception before gateway.send() keeps outcome NOT_SENT, does not mark unconfirmed, and still sends an owed OFF."""
    gate = make_gate(pulse_duration=1.0)
    gate._off_pending = True
    gate._async_wait_off_task = AsyncMock(side_effect=RuntimeError("boom"))  # type: ignore[method-assign]
    outcome = await gate._async_send_impulse(Intent.OPEN)
    assert outcome is PulseOutcome.NOT_SENT
    assert gate._unconfirmed_at == -1e9
    assert gate._off_task is not None
    await gate._off_task
    assert bus.frames == [OFF]
    assert gate._off_pending is False


async def test_nack_retry_delay_does_not_collapse_software_pulse_to_zero(
    hass: HomeAssistant, bus: _Bus, make_gate
):
    """If written_at precedes ACK by >= pulse_duration (e.g. OWNd 2 s NACK retry), anchor OFF to now."""
    gate = make_gate(pulse_duration=1.0)
    bus.hold = True
    opening = asyncio.create_task(gate.async_open_cover())
    await bus.until(1)
    # Simulate written_at stamped 2.1 s before ACK (OWNd NACK retry before ACK)
    bus.futures[0].set_result(time.monotonic() - 2.1)
    bus.hold = False
    await opening
    assert bus.frames == [ON]
    # Halfway through pulse_duration, OFF must NOT have fired yet
    async_fire_time_changed_exact(hass, dt_util.utcnow() + timedelta(seconds=0.5))
    await hass.async_block_till_done()
    assert bus.frames == [ON]
    # After full pulse_duration, OFF fires cleanly
    async_fire_time_changed_exact(hass, dt_util.utcnow() + timedelta(seconds=1.1))
    await hass.async_block_till_done()
    assert bus.frames == [ON, OFF]


# ── 8. M-09: Pre-wire cancelled/withdrawn pool futures are NOT_SENT ────────


def _make_pool_gateway(hass: HomeAssistant, entry_id: str) -> MyHOMEGatewayHandler:
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={
            CONF_HOST: "192.0.2.93",
            CONF_PORT: 20000,
            CONF_PASSWORD: "open",
            CONF_MAC: "00:03:50:00:88:99",
        },
        entry_id=entry_id,
    )
    entry.add_to_hass(hass)
    gateway = MyHOMEGatewayHandler(hass, entry)
    gateway._event_session_ready.set()
    return gateway


def _make_pool_gate(
    hass: HomeAssistant,
    gateway: MyHOMEGatewayHandler,
    *,
    pulse_duration: float = 1.0,
    state_sensor: str | None = None,
    access: bool = False,
) -> MyHOMEImpulseCover:
    with patch("custom_components.myhome.myhome_device.Entity.__init__", return_value=None):
        gate = MyHOMEImpulseCover(
            hass=hass,
            name="Garage",
            entity_name=None,
            device_id="24",
            who="1",
            where="24",
            interface=None,
            device_class=CoverDeviceClass.GARAGE,
            travel_time=10.0,
            pulse_duration=pulse_duration,
            min_cycle_time=5.0,
            pin_code=None,
            state_sensor=state_sensor,
            manufacturer="BTicino",
            model="Impulse Cover",
            gateway=gateway,
            access_config={} if access else None,
        )
    gate.hass = hass
    gate.entity_id = "cover.garage"
    gate.async_write_ha_state = MagicMock()
    return gate


async def test_prewire_connect_failure_is_not_sent_for_direct_and_access_covers(hass: HomeAssistant):
    """When CommandWorkerPool cannot open a command session, zero bytes were written: NOT_SENT, no supervision, no OFF retries."""
    sent_frames: list[str] = []

    class _UnavailableSession:
        def __init__(self, **_kwargs: Any) -> None:
            self._stream_reader = None
            self._stream_writer = None
            self.last_error = None

        async def connect(self) -> dict[str, Any]:
            return {"Success": False, "Message": "connection_unavailable"}

        async def send(self, message: Any, **_kwargs: Any) -> Any:
            sent_frames.append(str(message))
            return True

        async def close(self) -> None:
            return None

    gateway = _make_pool_gateway(hass, "test_m09_connect_unavailable")
    direct = _make_pool_gate(hass, gateway, pulse_duration=1.0, state_sensor=SENSOR)
    hass.states.async_set(SENSOR, STATE_OFF)
    direct._handle_sensor_update(STATE_OFF)

    supervised = _make_pool_gate(hass, gateway, pulse_duration=1.0, state_sensor=SENSOR, access=True)
    user = await _user(hass, "Parent")
    supervised._context = Context(user_id=user.id)
    assert supervised._access is not None
    supervised._access.config = _config(user.id)
    supervised._handle_sensor_update(STATE_OFF)
    calls = async_mock_service(hass, "notify", "mobile_app_parent")

    with patch("custom_components.myhome.gateway.OWNCommandSession", _UnavailableSession):
        worker = asyncio.create_task(gateway.sending_loop(0))
        try:
            with pytest.raises(HomeAssistantError, match="pulse was not sent") as err:
                await direct.async_open_cover()
            assert err.value.translation_key == "impulse_pulse_send_failed"
            assert sent_frames == []
            assert direct._off_pending is False
            assert direct._off_task is None
            assert direct._pulse_off_cancel is None
            assert direct._unconfirmed_at == -1e9
            assert direct._relay_seen_at == -1e9
            # Closed and at rest: stop_cover sends nothing because the failed connect did not stamp _relay_seen_at.
            await direct.async_stop_cover()
            assert sent_frames == []

            # Supervised access: settles to IDLE with "Not executed", never starts Phase.MONITORING or OFF retries.
            await supervised.async_open_cover()
            await _press(hass, _approve_action(calls)["action"], user.id)
            await _wait_phase(supervised._access, Phase.IDLE)
            await hass.async_block_till_done()
            messages = [str(c.data.get("message", "")) for c in calls]
            assert any("Not executed" in m and "was not sent" in m for m in messages)
            assert not any("did not confirm" in m for m in messages)
            assert supervised._off_pending is False
            assert supervised._off_task is None
            assert sent_frames == []
        finally:
            await supervised._access.async_shutdown()
            gateway._command_pool.close()
            await asyncio.wait_for(worker, 5)


async def test_prewire_connect_refused_and_connect_exception_are_not_sent(hass: HomeAssistant):
    """Auth refusal on reconnect or an exception raised inside connect() cancels pre-wire -> NOT_SENT."""
    attempts = 0

    class _RefusedOnReconnectSession:
        def __init__(self, **_kwargs: Any) -> None:
            self._stream_reader = None
            self._stream_writer = None
            self.last_error = None

        async def connect(self) -> dict[str, Any]:
            nonlocal attempts
            attempts += 1
            if attempts == 1:
                # Initial worker connect fails softly so worker enters loop with closed session.
                return {"Success": False, "Message": "connection_unavailable"}
            if attempts == 2:
                raise RuntimeError("socket setup boom")
            return {"Success": False, "Message": "password_error"}

        async def send(self, message: Any, **_kwargs: Any) -> Any:  # pragma: no cover - never reached
            raise AssertionError(f"must not send {message}")

        async def close(self) -> None:
            return None

    gateway = _make_pool_gateway(hass, "test_m09_connect_refused")
    gate = _make_pool_gate(hass, gateway, pulse_duration=1.0)

    with patch("custom_components.myhome.gateway.OWNCommandSession", _RefusedOnReconnectSession):
        worker = asyncio.create_task(gateway.sending_loop(0))
        try:
            # Attempt 2: connect() raises RuntimeError before send() -> NOT_SENT, no OFF task.
            with pytest.raises(HomeAssistantError, match="pulse was not sent") as err1:
                await gate.async_open_cover()
            assert err1.value.translation_key == "impulse_pulse_send_failed"
            assert gate._off_pending is False
            assert gate._off_task is None
            assert gate._unconfirmed_at == -1e9

            # Attempt 3: connect() returns password_error -> worker exits after pre-wire cancel -> NOT_SENT.
            with pytest.raises(HomeAssistantError, match="pulse was not sent") as err2:
                await gate.async_open_cover()
            assert err2.value.translation_key == "impulse_pulse_send_failed"
            assert gate._off_pending is False
            assert gate._off_task is None
            assert gate._unconfirmed_at == -1e9
            await asyncio.wait_for(worker, 5)
        finally:
            gateway._command_pool.close()
            if not worker.done():
                await asyncio.wait_for(worker, 5)


async def test_prewire_pool_close_timeout_and_caller_cancel_are_not_sent(hass: HomeAssistant, monkeypatch):
    """Frames withdrawn from CommandWorkerPool before _written_under never reach the wire -> NOT_SENT, no OFF."""
    monkeypatch.setattr(cover_module, "IMPULSE_WRITE_TIMEOUT", 0.02)

    # 1. Timeout withdraws a frame still queued in the pool (no worker has written it).
    gateway1 = _make_pool_gateway(hass, "test_m09_pool_timeout")
    gate1 = _make_pool_gate(hass, gateway1, pulse_duration=1.0)
    with pytest.raises(HomeAssistantError, match="pulse was not sent.*withdrawn") as err:
        await gate1.async_open_cover()
    assert err.value.translation_key == "impulse_pulse_send_failed"
    assert gate1._off_pending is False
    assert gate1._off_task is None
    assert gate1._unconfirmed_at == -1e9
    gateway1._command_pool.close()

    # 2. Caller CancelledError withdraws a frame still queued in the pool -> NOT_SENT, no OFF task.
    monkeypatch.setattr(cover_module, "IMPULSE_WRITE_TIMEOUT", 5.0)
    gateway2 = _make_pool_gateway(hass, "test_m09_pool_cancel")
    gate2 = _make_pool_gate(hass, gateway2, pulse_duration=1.0)
    task = asyncio.create_task(gate2.async_open_cover())
    await asyncio.sleep(0.01)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert gate2._last_written == (Intent.OPEN, PulseOutcome.NOT_SENT)
    assert gate2._off_pending is False
    assert gate2._off_task is None
    assert gate2._unconfirmed_at == -1e9
    gateway2._command_pool.close()

    # 3. Pool close() cancels queued frames pre-wire -> NOT_SENT, no OFF task.
    gateway3 = _make_pool_gateway(hass, "test_m09_pool_close")
    gate3 = _make_pool_gate(hass, gateway3, pulse_duration=1.0)
    opening = asyncio.create_task(gate3.async_open_cover())
    await asyncio.sleep(0.01)
    gateway3._command_pool.close()
    with pytest.raises(HomeAssistantError, match="pulse was not sent") as err3:
        await opening
    assert err3.value.translation_key == "impulse_pulse_send_failed"
    assert gate3._off_pending is False
    assert gate3._off_task is None
    assert gate3._unconfirmed_at == -1e9


async def test_postwire_timeout_and_drop_in_pool_remain_unconfirmed_and_owe_off(hass: HomeAssistant, monkeypatch):
    """Once CommandWorkerPool stamps _written_under and enters send(), timeout or drop stays UNCONFIRMED and owes OFF."""
    # Exceed Generic gateway's 0.05 s command_queue_delay so the eagerly-started OFF task
    # completes once release_send unblocks the worker after the ON timeout.
    monkeypatch.setattr(cover_module, "IMPULSE_WRITE_TIMEOUT", 0.2)
    monkeypatch.setattr(cover_module, "OFF_ATTEMPTS", 1)
    in_send = asyncio.Event()
    release_send = asyncio.Event()
    seen_frames: list[str] = []

    class _StallThenDropSession:
        def __init__(self, **_kwargs: Any) -> None:
            self._stream_reader: Any = None
            self._stream_writer: Any = None
            self.last_error = None

        async def connect(self) -> dict[str, Any]:
            self._stream_reader = object()
            self._stream_writer = object()
            return {"Success": True}

        async def send(self, message: Any, **_kwargs: Any) -> Any:
            frame = str(message)
            seen_frames.append(frame)
            if len(seen_frames) == 1:
                in_send.set()
                await release_send.wait()
                return None
            if len(seen_frames) == 3:
                # Mid-flight socket drop after _written_under was stamped.
                return None
            return True

        async def close(self) -> None:
            self._stream_reader = None
            self._stream_writer = None

    gateway = _make_pool_gateway(hass, "test_m09_postwire")
    gate = _make_pool_gate(hass, gateway, pulse_duration=1.0)

    with patch("custom_components.myhome.gateway.OWNCommandSession", _StallThenDropSession):
        worker = asyncio.create_task(gateway.sending_loop(0))
        try:
            # 1. Timeout after _written_under is stamped -> UNCONFIRMED + immediate OFF task.
            with pytest.raises(HomeAssistantError, match="pulse was not confirmed") as err:
                await gate.async_open_cover()
            assert err.value.translation_key == "impulse_pulse_unconfirmed"
            assert in_send.is_set()
            assert gate._unconfirmed_at > 0
            assert gate._off_pending is True
            assert gate._off_task is not None
            release_send.set()
            await asyncio.wait_for(gate._off_task, 5)
            assert gate._off_pending is False
            assert seen_frames == [ON, OFF]

            # 2. Mid-flight drop inside send() after _written_under is stamped -> UNCONFIRMED + OFF task.
            monkeypatch.setattr(cover_module, "IMPULSE_WRITE_TIMEOUT", 5.0)
            with pytest.raises(HomeAssistantError, match="pulse was not confirmed") as err2:
                await gate.async_open_cover()
            assert err2.value.translation_key == "impulse_pulse_unconfirmed"
            assert gate._off_pending is True
            assert gate._off_task is not None
            await asyncio.wait_for(gate._off_task, 5)
            assert gate._off_pending is False
            assert seen_frames == [ON, OFF, ON, OFF]
        finally:
            release_send.set()
            gateway._command_pool.close()
            await asyncio.wait_for(worker, 5)
