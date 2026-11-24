"""An area status reply must not be taken for a new area command (resync poll loop).

Live capture, 2026-11-18, physical F461 (2.0.11) and F455 (1.0.76) on one SCS bus,
three MyHOME 2.0.0 instances listening. One `*1*1*0#4#01##` made every instance ask
`*#1*00##` 0.5 s later, and the reply to that request carries, next to the point
statuses, frames with the area's own WHERE:

    02:47:23.348  *1*0*0011##
    02:47:23.389  *1*0*05##
    02:47:23.422  *1*0*07##
    02:47:23.469  *1*0*00##
    02:47:23.510  *2*0*06##
    02:47:23.558  *1*0*00##
    02:47:23.605  *1*0*0010##
    02:47:23.654  *1*0*00##
    02:47:23.717  *1*0*00##
    02:47:23.781  *1*0*00##
    02:47:23.845  *1*0*00##
    02:47:23.909  *1*0*00##

(one reply as seen on the F461 event session with a single instance still asking).
`*1*0*00##` is also what a real "area 00 off" command looks like on the event session
(`tests/fixtures/traces/f461_live_2026-10-15`), and it is the reply form of the WHO 1
status request (`*#1*<where>##` -> `*1*<status>*<where>##`, on the command and the
event session). Each of those frames armed a new `*#1*00##`: about 30 requests a
minute from every instance, for as long as it ran.
"""

from __future__ import annotations

import asyncio
import heapq
import itertools
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest
from homeassistant.const import CONF_HOST, CONF_MAC, CONF_PASSWORD, CONF_PORT
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from OWNd.message import OWNMessage
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.myhome.const import (
    CONF_BROADCAST_RESYNC,
    DOMAIN,
    RESYNC_DEBOUNCE_S,
    RESYNC_LEADING_WINDOW_S,
    RESYNC_REPLY_GRACE_S,
    RESYNC_STORM_SWEEPS,
    RESYNC_STORM_WINDOW_S,
)
from custom_components.myhome.gateway import MyHOMEGatewayHandler
from tests.test_issue_368_resync import MAC

#: The reply to one `*#1*00##`, offsets in seconds from its first frame (capture above).
AREA_00_REPLY: tuple[tuple[float, str], ...] = (
    (0.000, "*1*0*0011##"),
    (0.041, "*1*0*05##"),
    (0.074, "*1*0*07##"),
    (0.121, "*1*0*00##"),
    (0.162, "*2*0*06##"),
    (0.210, "*1*0*00##"),
    (0.257, "*1*0*0010##"),
    (0.306, "*1*0*00##"),
    (0.369, "*1*0*00##"),
    (0.433, "*1*0*00##"),
    (0.497, "*1*0*00##"),
    (0.561, "*1*0*00##"),
)
#: WHO 1 entities of the plant the capture comes from (two of them behind F422 interface 01).
PLANT_LIGHTS: tuple[str, ...] = ("05", "07", "0010", "0011", "09#4#01", "03#4#01")


def _make_handler(hass: HomeAssistant, mac: str, lights: tuple[str, ...]) -> MyHOMEGatewayHandler:
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={CONF_HOST: "192.168.1.5", CONF_PORT: 20000, CONF_PASSWORD: "open", CONF_MAC: mac},
        options={CONF_BROADCAST_RESYNC: True},
    )
    entry.add_to_hass(hass)
    with patch("custom_components.myhome.gateway.OWNGateway"):
        handler = MyHOMEGatewayHandler(hass, entry, generate_events=False, broadcast_resync=True)
    handler.gateway.serial = mac
    handler.send_status_request = AsyncMock()
    reg = er.async_get(hass)
    for where in lights:
        reg.async_get_or_create("light", DOMAIN, f"{handler.mac}-1-{where}", config_entry=entry)
    return handler


class _Bus:
    """One SCS bus on a virtual clock, shared by every handler given to it.

    A status request a handler queues is written `latency` seconds later, answered
    with `reply` (seen by every handler, as on a real bus) and ACKed `ack_delay`
    seconds after the last reply frame, which resolves the request's future as the
    command pool does.
    """

    def __init__(
        self,
        hass: HomeAssistant,
        handlers: list[MyHOMEGatewayHandler],
        *,
        replies: dict[str, tuple[tuple[float, str], ...]],
        latency: dict[int, float] | None = None,
        ack_delay: float = 1.0,
    ) -> None:
        self.hass = hass
        self.handlers = handlers
        self.replies = replies
        self.latency = latency or {}
        self.ack_delay = ack_delay
        self.now = 0.0
        self.sent: list[tuple[float, int, str]] = []
        self._queue: list[tuple[float, int, Any]] = []
        self._seq = itertools.count()
        for index, handler in enumerate(handlers):
            handler.send_status_request.side_effect = self._sender(index)

    def at(self, when: float, action: Any) -> Any:
        item = (when, next(self._seq), action)
        heapq.heappush(self._queue, item)
        return item

    def call_later(self, _hass: HomeAssistant, delay: float, cb: Any) -> Any:
        state = {"live": True}

        def _fire() -> None:
            if state["live"]:
                cb(None)

        self.at(self.now + delay, _fire)
        return lambda: state.update(live=False)

    def frame(self, when: float, raw: str) -> None:
        """Put one frame on the bus: every handler's event session delivers it."""
        msg = OWNMessage.parse(raw)
        assert msg is not None, raw

        async def _deliver() -> None:
            for handler in self.handlers:
                await handler._process_message(OWNMessage.parse(raw))

        self.at(when, _deliver)

    def _sender(self, index: int) -> Any:
        async def _send(cmd: Any) -> asyncio.Future[float]:
            raw = str(cmd)
            self.sent.append((round(self.now, 3), index, raw))
            written: asyncio.Future[float] = asyncio.get_running_loop().create_future()
            start = self.now + self.latency.get(index, 0.09)
            reply = self.replies.get(raw, ())
            for offset, frame in reply:
                self.frame(start + offset, frame)
            last = reply[-1][0] if reply else 0.0
            self.at(start + last + self.ack_delay, lambda: written.done() or written.set_result(start))
            return written

        return _send

    async def run(self, until: float) -> None:
        with (
            patch("custom_components.myhome.gateway.time", SimpleNamespace(monotonic=lambda: self.now)),
            patch("custom_components.myhome.gateway.async_call_later", self.call_later),
        ):
            while self._queue and self._queue[0][0] <= until:
                when, _, action = heapq.heappop(self._queue)
                self.now = when
                result = action()
                if asyncio.iscoroutine(result):
                    await result
                await self.hass.async_block_till_done()
            self.now = until

    def polls(self, index: int, raw: str = "*#1*00##") -> list[float]:
        return [t for t, i, frame in self.sent if i == index and frame == raw]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("trigger", "expected_polls"),
    [
        # The frame that started the loop live: the general of the bus behind
        # interface 01 no longer asks the gateway's own area 00 at all.
        ("*1*1*0#4#01##", []),
        # A general ON of the system. It commands ON and the area answers `*1*0*00##`:
        # that is read as a new (OFF) command, which the four local lights have just
        # echoed (the two behind the interface are no longer waited for), so no
        # second request goes out.
        ("*1*1*0##", [RESYNC_DEBOUNCE_S]),
        # The wall General OFF of #746, and a real area command.
        ("*1*0*0##", [RESYNC_DEBOUNCE_S]),
        ("*1*0*00##", [RESYNC_DEBOUNCE_S]),
    ],
)
async def test_area_status_reply_does_not_restart_the_sweep(
    hass: HomeAssistant, trigger: str, expected_polls: list[float]
) -> None:
    """The `*1*0*00##` frames in the answer to `*#1*00##` are not new area commands.

    Before the fix this asked `*#1*00##` 52 times in the 60 s below, for each trigger.
    """
    handler = _make_handler(hass, "00:03:50:00:00:01", PLANT_LIGHTS)
    bus = _Bus(hass, [handler], replies={"*#1*00##": AREA_00_REPLY})
    bus.frame(0.0, trigger)

    await bus.run(60.0)

    assert bus.polls(0) == expected_polls
    assert not handler._resync_timers


@pytest.mark.asyncio
async def test_real_area_commands_are_still_swept_around_an_answered_request(hass: HomeAssistant) -> None:
    """Only a frame that states the command being read back is its answer (#746)."""
    handler = _make_handler(hass, "00:03:50:00:00:01", ("04",))
    # An area that answers with its members only, as the MH200N of #746 is expected to
    # (a fixed answer: the bus model has no device state).
    bus = _Bus(hass, [handler], replies={"*#1*00##": ((0.0, "*1*0*04##"),)})
    bus.frame(0.0, "*1*0*00##")
    # Area 00 switched on again while the first request is still open: a new command.
    bus.frame(0.8, "*1*1*00##")
    # The second request is ACKed at 2.39 s. The same state inside its 0.5 s grace is
    # taken as its answer; the same command later is swept.
    bus.frame(2.6, "*1*1*00##")
    bus.frame(10.0, "*1*1*00##")

    await bus.run(20.0)

    assert bus.polls(0) == [RESYNC_DEBOUNCE_S, 0.8 + RESYNC_DEBOUNCE_S, 10.0 + RESYNC_DEBOUNCE_S]


@pytest.mark.asyncio
async def test_group_status_reply_does_not_restart_the_group_sweep(hass: HomeAssistant) -> None:
    """The same rule for a group, should a gateway answer `*#1*#G##` with the group's WHERE.

    Not captured: the reply form of the WHO 1 PDF (`*1*<status>*<where>##`) applied
    to a group. A physical F461 answers `*#1*#1##` with the members only
    (`tests/fixtures/traces/f461_live_2026-10-18/README.md`).
    """
    handler = _make_handler(hass, "00:03:50:00:00:01", ("11", "12"))
    handler._resync_manager.register_group_members("#6", ["11", "12"])
    reply = ((0.0, "*1*1*11##"), (0.05, "*1*1*#6##"), (0.1, "*1*1*#6##"))
    bus = _Bus(hass, [handler], replies={"*#1*#6##": reply})
    bus.frame(0.0, "*1*1*#6##")

    await bus.run(30.0)

    assert bus.polls(0, "*#1*#6##") == [RESYNC_DEBOUNCE_S]


@pytest.mark.asyncio
async def test_request_that_never_closes_does_not_leave_the_scope_deaf(hass: HomeAssistant) -> None:
    """A status request whose future is never resolved stops hiding scope frames after PACED_WRITE_TIMEOUT."""
    from custom_components.myhome.gateway import PACED_WRITE_TIMEOUT

    handler = _make_handler(hass, "00:03:50:00:00:01", ("04",))
    bus = _Bus(hass, [handler], replies={})
    bus.ack_delay = 10_000.0
    bus.frame(0.0, "*1*0*00##")
    bus.frame(5.0, "*1*0*00##")
    bus.frame(RESYNC_STORM_WINDOW_S + 1.0, "*1*0*00##")  # still hidden while queued within 60 s
    bus.frame(PACED_WRITE_TIMEOUT + 1.0, "*1*0*00##")

    await bus.run(PACED_WRITE_TIMEOUT + 10.0)

    assert bus.polls(0) == [RESYNC_DEBOUNCE_S, PACED_WRITE_TIMEOUT + 1.0 + RESYNC_DEBOUNCE_S]


@pytest.mark.asyncio
async def test_two_instances_on_one_bus_do_not_keep_each_other_polling(
    hass: HomeAssistant, caplog: pytest.LogCaptureFixture
) -> None:
    """The answer to another client's sweep looks like a command; the storm guard ends the exchange.

    Two MyHOME instances on one bus (as live: the two local test instances and the
    remote one). Timing chosen so that each one's answer lands after the other's own
    reply window: a slow command session on the second gateway and an early ACK.
    Before the fix both asked `*#1*00##` for as long as they ran.
    """
    # `08` is registered and never answers (a removed light still in the registry),
    # so the echo check of area 00 cannot complete and every scope frame arms a sweep.
    first = _make_handler(hass, "00:03:50:00:00:01", (*PLANT_LIGHTS, "08"))
    second = _make_handler(hass, "00:03:50:00:00:02", (*PLANT_LIGHTS, "08"))
    bus = _Bus(
        hass,
        [first, second],
        replies={"*#1*00##": AREA_00_REPLY},
        latency={0: 0.09, 1: 1.5},
        ack_delay=0.05,
    )
    bus.frame(0.0, "*1*0*0##")

    await bus.run(600.0)

    # Three sweeps each, then one late sweep once the scope has been quiet for the
    # window (the first instance), answered once by the other, and silence.
    assert len(bus.polls(0)) == RESYNC_STORM_SWEEPS + 1
    assert len(bus.polls(1)) == RESYNC_STORM_SWEEPS + 1
    assert max(bus.polls(0) + bus.polls(1)) < 60.0
    assert not first._resync_timers and not second._resync_timers
    assert any("was swept 3 times" in record.getMessage() for record in caplog.records)


@pytest.mark.asyncio
async def test_storm_guard_keeps_one_late_sweep_for_the_last_command(hass: HomeAssistant) -> None:
    """A held scope is still read back after its last command (#746: `04` never echoes)."""
    handler = _make_handler(hass, "00:03:50:00:00:01", ("04",))
    bus = _Bus(hass, [handler], replies={})
    for when in (0.0, 5.0, 10.0, 15.0, 20.0):
        bus.frame(when, "*1*0*0##")
    # After the late sweep the scope stays held while frames keep coming ...
    bus.frame(55.0, "*1*0*0##")
    # ... and is back to normal once it has been quiet for the window.
    bus.frame(55.0 + RESYNC_STORM_WINDOW_S + 1.0, "*1*0*0##")

    await bus.run(120.0)

    assert bus.polls(0) == [
        0.0 + RESYNC_DEBOUNCE_S,
        5.0 + RESYNC_DEBOUNCE_S,
        10.0 + RESYNC_DEBOUNCE_S,
        20.0 + RESYNC_STORM_WINDOW_S,
        55.0 + RESYNC_STORM_WINDOW_S + 1.0 + RESYNC_DEBOUNCE_S,
    ]


@pytest.mark.asyncio
async def test_storm_guard_counts_each_commanded_state_and_never_holds_dim_steps(hass: HomeAssistant) -> None:
    """ON / OFF presses in turn and a long dim press (#367) are swept every time."""
    handler = _make_handler(hass, "00:03:50:00:00:01", ("11",))
    bus = _Bus(hass, [handler], replies={})
    presses = ["*1*1*1##", "*1*0*1##", "*1*1*1##", "*1*0*1##", "*1*1*1##", "*1*0*1##"]
    for index, raw in enumerate(presses):
        bus.frame(index * 3.0, raw)
    for index in range(6):
        bus.frame(40.0 + index * 2.0, "*1*30*1##")

    await bus.run(80.0)

    assert bus.polls(0, "*#1*1##") == [index * 3.0 + RESYNC_DEBOUNCE_S for index in range(6)] + [
        40.0 + index * 2.0 + RESYNC_DEBOUNCE_S for index in range(6)
    ]


def test_storm_constants() -> None:
    """The storm window must outlast the leading window and the debounce it replaces."""
    assert RESYNC_REPLY_GRACE_S == 0.5
    assert RESYNC_STORM_SWEEPS == 3
    assert RESYNC_STORM_WINDOW_S == 30.0
    assert RESYNC_STORM_WINDOW_S > RESYNC_LEADING_WINDOW_S > RESYNC_DEBOUNCE_S


# A routed area cannot have been swept before its first light was registered.
async def test_empty_sweeps_do_not_hold_first_real_read(hass):
    handler = _make_handler(hass, MAC, ())
    bus = _Bus(hass, [handler], replies={})
    # The area command and its point read are those of
    # `test_area_sweep_stays_on_the_bus_of_the_area_frame` (tests/test_issue_368_resync.py).
    for when in (0, 1, 2):
        bus.frame(when, "*1*0*1#4#01##")
    await bus.run(2.9)
    assert bus.sent == []
    er.async_get(hass).async_get_or_create(
        "light", DOMAIN, f"{MAC}-1-11#4#01", config_entry=handler.config_entry
    )
    bus.frame(3, "*1*0*1#4#01##")
    await bus.run(4)
    assert bus.polls(0, "*#1*11#4#01##") == [3.5]


@pytest.mark.asyncio
async def test_dim_step_own_sweep_reply_suppresses_echo(hass: HomeAssistant) -> None:
    """A dim-step sweep (WHAT 30/31, expected_on=None) recognizes ON/OFF replies as its own answer."""
    handler = _make_handler(hass, "00:03:50:00:00:01", (*PLANT_LIGHTS, "08"))
    bus = _Bus(hass, [handler], replies={"*#1*00##": AREA_00_REPLY})
    bus.frame(0.0, "*1*30*00##")

    await bus.run(10.0)

    assert bus.polls(0) == [RESYNC_DEBOUNCE_S]


@pytest.mark.asyncio
async def test_unwritten_cancelled_sweep_does_not_arm_reply_guard(hass: HomeAssistant) -> None:
    """A queued status request withdrawn before being written must not hide real scope frames."""
    handler = _make_handler(hass, "00:03:50:00:00:01", ("04",))
    futs: list[asyncio.Future[float]] = []

    async def _send(_cmd: Any) -> asyncio.Future[float]:
        fut: asyncio.Future[float] = asyncio.get_running_loop().create_future()
        futs.append(fut)
        return fut

    handler.send_status_request = AsyncMock(side_effect=_send)
    await handler._resync_broadcast("00")
    assert len(futs) == 1
    assert "00" in handler._resync_manager._own_sweeps

    futs[0].cancel()
    await asyncio.sleep(0)

    assert "00" not in handler._resync_manager._own_sweeps
    assert "00" not in handler._resync_manager._reply_guard


@pytest.mark.asyncio
async def test_storm_frame_does_not_displace_opposite_state_short_timer(hass: HomeAssistant) -> None:
    """A storming OFF frame must not overwrite an already-armed 0.5 s ON sweep with a 30 s timer."""
    handler = _make_handler(hass, "00:03:50:00:00:01", ("04",))
    bus = _Bus(hass, [handler], replies={})
    # Drive OFF into storm (3 sweeps)
    for when in (0.0, 5.0, 10.0):
        bus.frame(when, "*1*0*00##")
    # Real ON arrives at 15.0 (arms 0.5 s timer -> 15.5); storming OFF arrives at 15.2 (delay 30 s)
    bus.frame(15.0, "*1*1*00##")
    bus.frame(15.2, "*1*0*00##")

    await bus.run(20.0)

    assert 15.0 + RESYNC_DEBOUNCE_S in bus.polls(0)


@pytest.mark.asyncio
async def test_delegated_secondary_reads_primary_and_own_lights(hass: HomeAssistant) -> None:
    """A WHO=1-delegated secondary gateway sees both its own lights and lights on the primary entry."""
    from custom_components.myhome.const import (
        CONF_BUS_TOPOLOGY,
        CONF_GATEWAY_ROLE,
        CONF_PRIMARY_GATEWAY,
        ROLE_SECONDARY,
        TOPOLOGY_SHARED,
    )

    primary = _make_handler(hass, "00:03:50:00:00:01", ("05", "09#4#01"))
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={CONF_HOST: "192.168.1.6", CONF_PORT: 20000, CONF_PASSWORD: "open", CONF_MAC: "00:03:50:00:00:02"},
        options={
            CONF_BROADCAST_RESYNC: True,
            CONF_BUS_TOPOLOGY: TOPOLOGY_SHARED,
            CONF_GATEWAY_ROLE: ROLE_SECONDARY,
            CONF_PRIMARY_GATEWAY: primary.mac,
        },
    )
    entry.add_to_hass(hass)
    with patch("custom_components.myhome.gateway.OWNGateway"):
        secondary = MyHOMEGatewayHandler(hass, entry, generate_events=False, broadcast_resync=True)
    secondary.gateway.serial = "00:03:50:00:00:02"
    er.async_get(hass).async_get_or_create("light", DOMAIN, f"{secondary.mac}-1-07", config_entry=entry)
    secondary._get_primary_gateway = lambda: primary  # type: ignore[method-assign]

    assert secondary.is_follower is True
    area_map = secondary._resync_manager._known_area_map()
    assert area_map["00"] == {"05", "07"}
    assert area_map["00#4#01"] == {"09#4#01"}
