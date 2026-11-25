"""Software stepped dimming transition engine for MyHOME lights."""

from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable, Callable, Coroutine
from typing import Any

from .const import (
    DEFAULT_TRANSITION_MODE,
    LOGGER,
    SOFTWARE_TRANSITION_MAX_STEPS,
    SOFTWARE_TRANSITION_MIN_STEPS,
    SOFTWARE_TRANSITION_STEP_INTERVAL,
    TRANSITION_MODE_NATIVE,
)
from .gateway_confirm import ECHO_CONFIRM_DELAY_S

DEFAULT_FIRST_ECHO_TIMEOUT = ECHO_CONFIRM_DELAY_S + 1.0
_STEP1_WRITE_TIMEOUT_S = 10.0


class SoftwareFadeEngine:
    """Manages software stepped transition fades using instant brightness commands."""

    def __init__(
        self,
        *,
        where: str,
        create_task_cb: Callable[[Coroutine[Any, Any, None]], asyncio.Task[None]],
        send_instant_cb: Callable[[int], Awaitable[Any]],
        apply_state_cb: Callable[[int, bool | None], None],
        update_ha_state_cb: Callable[[], None],
        get_worker_count_cb: Callable[[], int],
        get_transition_mode_cb: Callable[[], str],
        is_on_cb: Callable[[], bool],
        first_echo_timeout: float = DEFAULT_FIRST_ECHO_TIMEOUT,
    ) -> None:
        """Initialize the software fade engine."""
        self.where = where
        self.create_task_cb = create_task_cb
        self.send_instant_cb = send_instant_cb
        self.apply_state_cb = apply_state_cb
        self.update_ha_state_cb = update_ha_state_cb
        self.get_worker_count_cb = get_worker_count_cb
        self.get_transition_mode_cb = get_transition_mode_cb
        self.is_on_cb = is_on_cb
        self.first_echo_timeout: float = first_echo_timeout

        self.fade_task: asyncio.Task[None] | None = None
        self.fade_id: int = 0
        self.warned_multi_worker: bool = False
        self.cmd_lock: asyncio.Lock = asyncio.Lock()
        self._step1_echo_event: asyncio.Event | None = None
        self.pending_first_step_pct: int | None = None
        self.fade_start_pct: int | None = None
        self.last_instant_written: Any = None

    def notify_bus_echo(self) -> None:
        """Signal that a non-translation bus status or brightness echo arrived."""
        if self._step1_echo_event is not None:
            self._step1_echo_event.set()

    @property
    def is_fading(self) -> bool:
        """Return whether a software fade task is currently active."""
        return bool(
            (self.fade_task and not self.fade_task.done())
            or self._step1_echo_event is not None
        )

    def get_transition_mode(self) -> str:
        """Return the effective transition mode for the light."""
        try:
            return self.get_transition_mode_cb()
        except Exception:
            return DEFAULT_TRANSITION_MODE

    def should_use_software_stepped(self, transition: float | None) -> bool:
        """Return whether software-stepped dimming should be used."""
        if transition is None or transition <= 0:
            return False
        mode = self.get_transition_mode()
        return mode != TRANSITION_MODE_NATIVE

    async def set_brightness_instant(self, pct: int) -> Any:
        """Send set_brightness with transition=0. Uses per-light lock to help ordering."""
        pct = max(0, min(100, int(pct)))
        async with self.cmd_lock:
            return await self.send_instant_cb(pct)

    async def maybe_instant_brightness(
        self, start_pct: int, target_pct: int, is_on: bool | None = None
    ) -> bool:
        """Execute instant brightness if delta <= 1%, skipping bus if already at target."""
        _ = is_on
        self.last_instant_written = None
        delta_pct = abs(target_pct - start_pct)
        if delta_pct <= 1:
            if not (self.is_on_cb() and target_pct == start_pct):
                self.last_instant_written = await self.set_brightness_instant(target_pct)
            return True
        return False

    def next_fade_id(self) -> int:
        """Increment and return the next fade sequence id."""
        self.fade_id += 1
        return self.fade_id

    def cancel_fade_if_active(self) -> None:
        """Simple cancel for @callback contexts (e.g. handle_event)."""
        self._step1_echo_event = None
        self.pending_first_step_pct = None
        self.fade_start_pct = None
        if self.fade_task and not self.fade_task.done():
            self.fade_task.cancel()
            self.fade_task = None

    async def cancel_fade_robustly(self) -> None:
        """Robust cancel + drain for async contexts."""
        self._step1_echo_event = None
        self.pending_first_step_pct = None
        self.fade_start_pct = None
        task = self.fade_task
        if task and not task.done():
            task.cancel()
            try:
                # shield() prevents wait_for from double-cancelling the task
                # when the 0.15 s timeout fires (we already called .cancel()).
                await asyncio.wait_for(asyncio.shield(task), timeout=0.15)
            except asyncio.CancelledError:
                caller = asyncio.current_task()
                if caller is not None and caller.cancelling() > 0:
                    if self.fade_task is task:
                        self.fade_task = None
                    raise
            except Exception:
                pass
            if self.fade_task is task:
                self.fade_task = None

    def start_fade(self, start_pct: int, target_pct: int, duration: float) -> asyncio.Task[None]:
        """Start a software fade background task on the entity's Home Assistant loop."""
        self._step1_echo_event = None
        self.pending_first_step_pct = None
        self.fade_start_pct = None
        fid = self.next_fade_id()
        task: asyncio.Task[None] = self.create_task_cb(
            self.async_fade_to(start_pct, target_pct, duration, fid)
        )
        self.fade_task = task
        return task

    async def _await_step1_echo(self, echo_event: asyncio.Event, written: Any) -> bool:
        """Anchor the step-1 echo timeout on socket write completion rather than queue enqueue."""
        write_ts: Any = None
        if isinstance(written, asyncio.Future):
            if not written.done() and not echo_event.is_set():
                echo_wait = asyncio.create_task(echo_event.wait())
                write_shield = asyncio.ensure_future(asyncio.shield(written))
                try:
                    await asyncio.wait(
                        {echo_wait, write_shield},
                        timeout=_STEP1_WRITE_TIMEOUT_S,
                        return_when=asyncio.FIRST_COMPLETED,
                    )
                finally:
                    echo_wait.cancel()
                    write_shield.cancel()
                    if write_shield.done() and not write_shield.cancelled():
                        write_shield.exception()
            if written.done():
                if written.cancelled() or written.exception() is not None:
                    return False
                write_ts = written.result()
            elif not echo_event.is_set():
                return False
        if echo_event.is_set():
            return True
        anchor = (
            float(write_ts)
            if isinstance(write_ts, (int, float)) and not isinstance(write_ts, bool)
            else time.monotonic()
        )
        remaining = (anchor + self.first_echo_timeout) - time.monotonic()
        if remaining > 0:
            try:
                await asyncio.wait_for(echo_event.wait(), timeout=remaining)
            except TimeoutError:
                return False
        return echo_event.is_set()

    async def async_fade_to(
        self, start_pct: int, target_pct: int, duration: float, fade_id: int
    ) -> None:
        """Background software stepped fade using instant brightness commands."""
        start_pct = max(0, min(100, int(start_pct or 0)))
        target_pct = max(0, min(100, int(target_pct or 0)))
        duration = max(0.0, float(duration))

        if fade_id != self.fade_id:
            return

        # Warn once if using multiple workers (can interleave steps for this light)
        try:
            if not self.warned_multi_worker:
                wc = self.get_worker_count_cb()
                if int(wc) > 1:
                    LOGGER.warning(
                        "%s: Using software stepped fade with command_worker_count=%s. "
                        "Step reordering is possible. Recommend =1 for reliable fades.",
                        self.where,
                        wc,
                    )
                    self.warned_multi_worker = True
        except Exception:
            pass

        if await self.maybe_instant_brightness(start_pct, target_pct):
            if fade_id == self.fade_id:
                self.fade_task = None
            return

        if duration < 0.05:
            await self.set_brightness_instant(target_pct)
            if fade_id == self.fade_id:
                self.fade_task = None
            return

        delta_pct = abs(target_pct - start_pct)
        calc_steps = int(duration / SOFTWARE_TRANSITION_STEP_INTERVAL + 0.5)
        num_steps = max(
            min(SOFTWARE_TRANSITION_MIN_STEPS, delta_pct),
            min(
                SOFTWARE_TRANSITION_MAX_STEPS,
                calc_steps,
                delta_pct,
            ),
        )
        step_time = duration / num_steps
        delta = (target_pct - start_pct) / num_steps

        last_sent = start_pct
        step1_confirmed = False
        try:
            for i in range(1, num_steps + 1):
                if fade_id != self.fade_id:
                    LOGGER.debug("%s Aborting stale fade step", self.where)
                    return
                current = int(round(start_pct + delta * i))
                current = max(0, min(100, current))

                if current != last_sent:
                    if not step1_confirmed:
                        echo_event = asyncio.Event()
                        self._step1_echo_event = echo_event
                        self.fade_start_pct = start_pct
                        self.pending_first_step_pct = current
                        written = await self.set_brightness_instant(current)
                        if fade_id != self.fade_id:
                            LOGGER.debug("%s Aborting stale fade step", self.where)
                            return
                        try:
                            confirmed = await self._await_step1_echo(echo_event, written)
                        finally:
                            if self._step1_echo_event is echo_event:
                                self._step1_echo_event = None
                            if fade_id == self.fade_id:
                                self.pending_first_step_pct = None
                                self.fade_start_pct = None
                        if not confirmed:
                            LOGGER.debug(
                                "%s Aborting software fade (id=%s): no bus echo within %.2fs",
                                self.where,
                                fade_id,
                                self.first_echo_timeout,
                            )
                            return
                        step1_confirmed = True
                    else:
                        await self.set_brightness_instant(current)
                    self.apply_state_cb(current, None)
                    self.update_ha_state_cb()
                    last_sent = current

                if i < num_steps:
                    await asyncio.sleep(step_time)

            if fade_id == self.fade_id:
                if last_sent != target_pct:  # pragma: no cover - defensive guarantee
                    await self.set_brightness_instant(target_pct)
                self.apply_state_cb(target_pct, None)
                self.update_ha_state_cb()
        except asyncio.CancelledError:
            LOGGER.debug("%s Software fade cancelled (id=%s)", self.where, fade_id)
            raise
        except Exception as err:  # prevent "Task exception was never retrieved"
            LOGGER.warning("%s Fade task error (id=%s): %s", self.where, fade_id, err)
        finally:
            if fade_id == self.fade_id:
                self._step1_echo_event = None
                self.pending_first_step_pct = None
                self.fade_start_pct = None
                self.fade_task = None
