"""Issue #433: a general / area cover stayed "opening" after a general UP.

An MH201 plant: eight shutters 11-18 (area 1) and a "general" cover entity. A
keypad's general UP (``*2*0*0##`` then ``*2*1*0##``) starts every actuator; each
one ends its run with its own stop ~126 s later (``*2*0*11##`` ... ``*2*0*18##``)
and no stop ever arrives for the scope WHERE. That is the published flow:
WHO_2.pdf §3.0.1.2-3, for ``<where>=GEN,A,GR`` "you will have as many frames as
automation objects" (Encyclopedia functional/who-2-automation/what.md and
addressing.md "Event expansion"). A scope cover must therefore follow its
members instead of waiting for a frame of its own.

The trace is the reporter's bus-card capture from the issue, anonymized: only
the frames and their spacing are kept (seconds from the first frame), the
entity names are the fixture's ``Cover <where>``, the gateway is synthetic.
The two TX frames are HA's own area-1 stops (``*2*0*1##``), the tell that the
reporter's "general" cover is addressed as area 1; both addressings are
replayed.
"""

from __future__ import annotations

import time
from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from homeassistant.const import (
    CONF_FRIENDLY_NAME,
    CONF_HOST,
    CONF_MAC,
    CONF_NAME,
    CONF_PASSWORD,
    CONF_PORT,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.dispatcher import async_dispatcher_send
from homeassistant.util import dt as dt_util
from OWNd.message import OWNEvent, OWNMessage
from pytest_homeassistant_custom_component.common import MockConfigEntry, async_fire_time_changed
from voluptuous import Invalid

import custom_components.myhome.cover as cover_module
from custom_components.myhome.const import (
    CONF_DEVICE_TYPE,
    CONF_FILE_PATH,
    CONF_FIRMWARE,
    CONF_MANUFACTURER,
    CONF_MEMBERS,
    CONF_WHERE,
    DOMAIN,
)
from custom_components.myhome.cover import CoverFamily, CoverScope, MyHOMECover, MyHOMEScopeCover
from custom_components.myhome.router import FrameRouter
from custom_components.myhome.validate import cover_schema

MAC = "00:03:50:00:04:33"

# (seconds from the first frame, direction, frame) - MyHOME#433, MH201, OWNd 2.0.0b8.
TRACE_433: list[tuple[float, str, str]] = [
    # Morning: local keypad runs; every press is stop-then-direction ~40 ms apart.
    (0.000, "rx", "*2*1*14##"),
    (9.840, "rx", "*2*0*14##"),
    (9.880, "rx", "*2*1*14##"),
    (15.589, "rx", "*2*0*17##"),
    (15.629, "rx", "*2*1*17##"),
    (24.019, "rx", "*2*0*17##"),
    (24.069, "rx", "*2*1*17##"),
    (24.219, "rx", "*2*0*17##"),
    (33.399, "rx", "*2*0*18##"),
    (33.439, "rx", "*2*1*18##"),
    (43.318, "rx", "*2*0*18##"),
    (43.368, "rx", "*2*1*18##"),
    (104.685, "rx", "*2*0*17##"),
    (104.735, "rx", "*2*1*17##"),
    (109.235, "rx", "*2*0*17##"),
    (109.284, "rx", "*2*1*17##"),
    (122.764, "rx", "*2*0*15##"),
    (122.804, "rx", "*2*1*15##"),
    (248.258, "rx", "*2*0*15##"),  # the actuator's own end-of-run stop, 125.5 s later
    (350.523, "rx", "*2*0*14##"),
    (350.562, "rx", "*2*1*14##"),
    (423.339, "rx", "*2*0*18##"),
    (423.389, "rx", "*2*1*18##"),
    (429.238, "rx", "*2*0*18##"),
    (429.288, "rx", "*2*1*18##"),
    (476.886, "rx", "*2*0*14##"),
    # Home Assistant stops the area-1 cover; the gateway echoes it, no per-object frames.
    (14286.915, "tx", "*2*0*1##"),
    (14286.989, "rx", "*2*0*1##"),
    # A keypad general UP; each actuator reports its own stop ~126 s later.
    (15387.348, "rx", "*2*0*0##"),
    (15387.878, "rx", "*2*1*0##"),
    (15513.772, "rx", "*2*0*11##"),
    (15513.992, "rx", "*2*0*13##"),
    (15514.082, "rx", "*2*0*14##"),
    (15514.122, "rx", "*2*0*16##"),
    (15514.382, "rx", "*2*0*15##"),
    (15514.422, "rx", "*2*0*17##"),
    (15514.472, "rx", "*2*0*12##"),
    (15514.992, "rx", "*2*0*18##"),
    (15896.369, "tx", "*2*0*1##"),
    (15896.434, "rx", "*2*0*1##"),
    # The reporter's run: "I PRESSED GENERAL COVER BUTTON UP".
    (18312.748, "rx", "*2*0*0##"),
    (18313.279, "rx", "*2*1*0##"),
    (18439.202, "rx", "*2*0*12##"),
    (18439.412, "rx", "*2*0*11##"),
    (18439.657, "rx", "*2*0*15##"),
    (18439.702, "rx", "*2*0*17##"),
    (18439.792, "rx", "*2*0*16##"),
    (18439.911, "rx", "*2*0*14##"),
    (18440.022, "rx", "*2*0*13##"),
    (18440.142, "rx", "*2*0*18##"),
]

POINTS = [str(w) for w in range(11, 19)]


def _plant_yaml(scope_where: str) -> str:
    lines = [f"mh201:\n  mac: '{MAC}'\n  cover:\n"]
    for where in [*POINTS, scope_where]:
        lines.append(f"    cover_{where}:\n      where: '{where}'\n      name: Cover {where}\n")
    return "".join(lines)


async def _setup_plant(hass: HomeAssistant, tmp_path, scope_where: str) -> MockConfigEntry:
    plant_yaml = tmp_path / "myhome.yaml"
    plant_yaml.write_text(_plant_yaml(scope_where), encoding="utf-8")
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={
            CONF_HOST: "192.0.2.1",
            CONF_PORT: 20000,
            CONF_PASSWORD: "pass",
            CONF_MAC: MAC,
            CONF_NAME: "MH201",
            CONF_DEVICE_TYPE: "urn:schemas-bticino-it:device:IP scenario module:1",
            CONF_FRIENDLY_NAME: "MH201 Gateway",
            CONF_MANUFACTURER: "BTicino S.p.A.",
            CONF_FIRMWARE: "1.0",
        },
        options={CONF_FILE_PATH: str(plant_yaml)},
        unique_id=MAC,
    )
    entry.add_to_hass(hass)
    with (
        patch(
            "custom_components.myhome.gateway.OWNSession.test_connection",
            return_value={"Success": True, "Message": None},
        ),
        patch("custom_components.myhome.gateway.MyHOMEGatewayHandler.listening_loop"),
        patch("custom_components.myhome.gateway.MyHOMEGatewayHandler.sending_loop"),
    ):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
    entry.runtime_data.gateway._on_event_connection_state_change(True)
    return entry


@pytest.mark.parametrize("scope_where", ["1", "0"], ids=["area_1", "general"])
async def test_issue_433_trace_scope_cover_follows_its_members(hass: HomeAssistant, tmp_path, scope_where: str) -> None:
    """Replay the capture: the scope cover ends open, not "opening" forever."""
    entry = await _setup_plant(hass, tmp_path, scope_where)
    scope_id = f"cover.cover_{scope_where}"
    assert hass.states.get(scope_id) is not None

    # The estimate runs on the trace's own clock (cover.py reads time.monotonic).
    clock = SimpleNamespace(now=0.0)
    fake_time = SimpleNamespace(monotonic=lambda: clock.now, time=time.time)

    seen_opening = seen_partial = False
    with patch.object(cover_module, "time", fake_time):
        for offset, direction, raw in TRACE_433:
            if direction != "rx":
                continue
            clock.now = 1000.0 + offset
            async_dispatcher_send(hass, f"myhome_message_{MAC}", OWNMessage.parse(raw))
            await hass.async_block_till_done()
            if raw == "*2*1*0##":
                assert hass.states.get(scope_id).state == "opening"
                seen_opening = True
            if offset in (15513.772, 18439.202):
                # One actuator reported its stop; the other seven still run.
                assert hass.states.get(scope_id).state == "opening"
                seen_partial = True

    assert seen_opening and seen_partial
    for where in POINTS:
        state = hass.states.get(f"cover.cover_{where}")
        assert state.state == "open", where
        assert state.attributes["current_position"] == 100, where
    scope = hass.states.get(scope_id)
    assert scope.state == "open"  # was "opening" for good before the fix
    assert scope.attributes["current_position"] == 100
    assert sorted(scope.attributes["members"]) == [f"cover.cover_{w}" for w in POINTS]

    await hass.config_entries.async_unload(entry.entry_id)


async def test_issue_433_area_command_moves_the_covers_in_the_area(hass: HomeAssistant, tmp_path) -> None:
    """A keypad area UP reached no cover at all: area frames were dropped."""
    entry = await _setup_plant(hass, tmp_path, "1")
    # A cover outside area 1 and one behind an interface: neither is moved by *2*1*1##.
    async_dispatcher_send(hass, f"myhome_message_{MAC}", OWNEvent.parse("*2*0*21##"))
    async_dispatcher_send(hass, f"myhome_message_{MAC}", OWNEvent.parse("*2*0*12#4#02##"))
    await hass.async_block_till_done()

    async_dispatcher_send(hass, f"myhome_message_{MAC}", OWNEvent.parse("*2*1*1##"))
    await hass.async_block_till_done()
    assert all(hass.states.get(f"cover.cover_{w}").state == "opening" for w in POINTS)
    assert hass.states.get("cover.cover_1").state == "opening"
    assert hass.states.get("cover.cover_21").state != "opening"
    assert hass.states.get("cover.cover_12i02").state != "opening"

    for where in POINTS:
        async_dispatcher_send(hass, f"myhome_message_{MAC}", OWNEvent.parse(f"*2*0*{where}##"))
    await hass.async_block_till_done()
    assert hass.states.get("cover.cover_1").state != "opening"

    await hass.config_entries.async_unload(entry.entry_id)


@pytest.mark.parametrize(
    ("where", "interface", "expected"),
    [
        ("0", None, "general"),
        ("1", None, "area"),
        ("00", None, "area"),
        ("100", None, "area"),
        ("#3", None, "group"),
        ("11", None, None),
        ("03", None, None),  # A=0 two-digit points exist on real plants (MH200, #378)
        ("0003", None, None),
        ("1015", None, None),
        ("14", "02", None),
    ],
)
def test_cover_scope_of(where: str, interface: str | None, expected: str | None) -> None:
    scope = CoverScope.of(where, interface)
    assert (scope.kind if scope else None) == expected


def _cover(gateway, where: str, interface: str | None = None, **kwargs) -> MyHOMECover:
    cls = MyHOMEScopeCover if "scope" in kwargs else MyHOMECover
    key = f"{where}#4#{interface}" if interface else where
    with patch("custom_components.myhome.myhome_device.Entity.__init__", return_value=None):
        cover = cls(
            hass=None, name=f"Cover {where}", entity_name=None, device_id=key, who="2", where=where,
            interface=interface, advanced=False, manufacturer="BTicino", model="Shutter",
            gateway=gateway, travel_time=20, **kwargs,
        )
    cover.entity_id = f"cover.cover_{key.replace('#', '_')}"
    cover.async_schedule_update_ha_state = MagicMock()
    return cover


@pytest.fixture
def gateway():
    gw = MagicMock()
    gw.mac = MAC
    gw.log_id = "[Test Gateway]"
    gw.send = AsyncMock()
    gw.send_status_request = AsyncMock()
    return gw


def test_area_membership_follows_the_address_and_interface(gateway) -> None:
    family = CoverFamily()
    covers = {k: _cover(gateway, *k) for k in [("11",), ("19",), ("21",), ("0110",), ("11", "02"), ("0011",), ("1005",)]}
    for c in covers.values():
        family.add(c)
    area_1 = CoverScope.of("1")
    assert {c._device_id for c in family.members(area_1)} == {"11", "19", "0110"}
    assert {c._device_id for c in family.members(CoverScope.of("00"))} == {"0011"}
    assert {c._device_id for c in family.members(CoverScope.of("100"))} == {"1005"}
    assert {c._device_id for c in family.members(CoverScope.of("1", "02"))} == {"11#4#02"}
    assert len(family.members(CoverScope.of("0"))) == len(covers)
    assert family.keys_moved_by("1", None) == ["11", "19", "0110"]


def test_group_moves_only_its_declared_members(gateway) -> None:
    """A group's membership lives in the actuators: without members none is known."""
    family = CoverFamily()
    points = [_cover(gateway, w) for w in ("11", "12", "13")]
    group = _cover(gateway, "#3", scope=CoverScope.of("#3", None, ["11", "13"]))
    for c in (*points, group):
        family.add(c)
    assert family.keys_moved_by("#3", None) == ["11", "13"]
    assert family.keys_moved_by("#4", None) == []

    router = FrameRouter()
    for c in (*points, group):
        router.subscribe("2", [c._device_id, "general"], c.handle_event)
    router.publish("2", ["#3", *family.keys_moved_by("#3", None)], OWNEvent.parse("*2*2*#3##"))
    assert [p.is_closing for p in points] == [True, False, True]
    assert group.is_closing is True
    for where in ("11", "13"):
        router.publish("2", [where], OWNEvent.parse(f"*2*0*{where}##"))
    assert group.is_closing is False
    assert group.extra_state_attributes["members"] == ["cover.cover_11", "cover.cover_13"]


async def test_scope_cover_without_members_ends_its_run_after_the_travel_time(hass: HomeAssistant, gateway) -> None:
    """Nothing reports the end of a memberless scope's run: the travel time does."""
    group = _cover(gateway, "#7", scope=CoverScope.of("#7"))
    group.hass = hass
    group.handle_event(OWNEvent.parse("*2*1*#7##"))
    assert group.is_opening is True

    async_fire_time_changed(hass, dt_util.utcnow() + timedelta(seconds=10))
    await hass.async_block_till_done()
    assert group.is_opening is True

    async_fire_time_changed(hass, dt_util.utcnow() + timedelta(seconds=21))
    await hass.async_block_till_done()
    assert group.is_opening is False
    assert group.current_cover_position == 100
    assert group.is_closed is False

    # A stop before the travel time cancels the timer; a later timer never fires on it.
    group.handle_event(OWNEvent.parse("*2*2*#7##"))
    group.handle_event(OWNEvent.parse("*2*0*#7##"))
    assert group._run_timeout is None
    group._cancel_run_timeout()


def test_members_only_on_a_group_cover() -> None:
    base = {CONF_WHERE: "1", CONF_NAME: "Area 1", CONF_MEMBERS: ["11"]}
    with pytest.raises(Invalid, match="group cover"):
        cover_schema({"area_1": base})
    ok = cover_schema({"group_3": {**base, CONF_WHERE: "#3"}})
    assert [cfg[CONF_MEMBERS] for cfg in ok.values()] == [["11"]]


def _sent(gateway) -> list[str]:
    return [str(call.args[0]) for call in gateway.send.await_args_list]


async def test_scope_cover_behind_an_interface_commands_each_member(gateway) -> None:
    """An MH200 does not pass a scope command through an F422: send point commands."""
    family = CoverFamily()
    points = [_cover(gateway, "11", "02"), _cover(gateway, "12", "02"), _cover(gateway, "13"), _cover(gateway, "21", "02")]
    area = _cover(gateway, "1", "02", scope=CoverScope.of("1", "02"))
    for c in (*points, area):
        family.add(c)

    await area.async_open_cover()
    assert sorted(_sent(gateway)) == ["*2*1*11#4#02##", "*2*1*12#4#02##"]
    assert all(p.is_opening for p in points[:2]) and area.is_opening

    gateway.send.reset_mock()
    await area.async_stop_cover()
    assert sorted(_sent(gateway)) == ["*2*0*11#4#02##", "*2*0*12#4#02##"]

    gateway.send.reset_mock()
    await area.async_close_cover()
    assert sorted(_sent(gateway)) == ["*2*2*11#4#02##", "*2*2*12#4#02##"]


async def test_scope_cover_on_the_main_bus_sends_one_scope_frame(gateway) -> None:
    """On the main bus the scope frame works (#433's MH201): one frame, not one per member."""
    family = CoverFamily()
    area = _cover(gateway, "1", scope=CoverScope.of("1"))
    for c in (_cover(gateway, "11"), _cover(gateway, "12"), area):
        family.add(c)
    await area.async_open_cover()
    assert _sent(gateway) == ["*2*1*1##"]


def test_general_scope_behind_an_interface_holds_that_interface_only(gateway) -> None:
    family = CoverFamily()
    for c in (_cover(gateway, "11", "02"), _cover(gateway, "11"), _cover(gateway, "85")):
        family.add(c)
    assert [c._device_id for c in family.members(CoverScope.of("0", "02"))] == ["11#4#02"]
    assert len(family.members(CoverScope.of("0"))) == 3


async def test_scope_cover_on_the_main_bus_close_stop_and_position(gateway) -> None:
    family = CoverFamily()
    area = _cover(gateway, "1", scope=CoverScope.of("1"))
    family.add(area)
    await area.async_close_cover()
    await area.async_stop_cover()
    assert _sent(gateway) == ["*2*2*1##", "*2*0*1##"]
    with patch.object(MyHOMECover, "async_set_cover_position", AsyncMock()) as timed:
        await area.async_set_cover_position(position=40)
    timed.assert_awaited_once_with(position=40)  # the scope's own timed run


async def test_scope_cover_behind_an_interface_positions_each_member(gateway) -> None:
    family = CoverFamily()
    points = [_cover(gateway, "11", "02"), _cover(gateway, "12", "02")]
    area = _cover(gateway, "1", "02", scope=CoverScope.of("1", "02"))
    for c in (*points, area):
        family.add(c)
    with patch.object(MyHOMECover, "async_set_cover_position", AsyncMock()) as timed:
        await area.async_set_cover_position(position=40)
    assert timed.await_count == 2  # one timed run per member, none for the scope


async def test_scope_cover_timeout_edges(hass: HomeAssistant, gateway) -> None:
    group = _cover(gateway, "#7", scope=CoverScope.of("#7"))
    group.hass = hass

    # Closing without members comes from the cover's own state.
    group.handle_event(OWNEvent.parse("*2*2*#7##"))
    assert group.is_closing is True
    armed = group._run_timeout
    group.handle_event(OWNEvent.parse("*2*2*#7##"))  # same direction: the timer is kept
    assert group._run_timeout is armed

    # Our own stop, frozen when written (gateways that do not echo it never reach
    # handle_event): the pending timer must not then force the endpoint.
    group._freeze_position(time.monotonic())
    position = group.current_cover_position
    async_fire_time_changed(hass, dt_util.utcnow() + timedelta(seconds=21))
    await hass.async_block_till_done()
    assert group.is_closing is False
    assert group.current_cover_position == position

    # Removal drops the member subscription and any timer.
    group.handle_event(OWNEvent.parse("*2*1*#7##"))
    unsub = group._unsub_members = MagicMock()
    await group.async_will_remove_from_hass()
    unsub.assert_called_once()
    assert group._unsub_members is None and group._run_timeout is None
