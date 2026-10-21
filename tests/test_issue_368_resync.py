"""Test issue #368 PR A: debounced re-sync for group / area / general frames."""

from unittest.mock import AsyncMock, MagicMock, patch

import homeassistant.util.dt as dt_util
import pytest
from homeassistant.const import CONF_HOST, CONF_MAC, CONF_PASSWORD, CONF_PORT
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from OWNd.message import OWNMessage
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    async_fire_time_changed,
)

from custom_components.myhome.const import (
    CONF_BROADCAST_RESYNC,
    DOMAIN,
    RESYNC_DEBOUNCE_S,
    RESYNC_LEADING_WINDOW_S,
    area_of_where,
)
from custom_components.myhome.gateway import MyHOMEGatewayHandler

MAC = "00:03:50:00:00:01"


def test_area_of_where():
    """Test area_of_where logic."""
    assert area_of_where("12") == "1"
    assert area_of_where("0115") == "1"
    assert area_of_where("0015") == "00"
    assert area_of_where("1003") == "100"
    assert area_of_where("12#4#01") == "1"
    assert area_of_where("invalid") is None
    assert area_of_where(None) is None


def test_resync_timing_constants():
    """Ensure timing constants are exposed."""
    assert RESYNC_DEBOUNCE_S == 0.5
    assert RESYNC_LEADING_WINDOW_S == 1.5


def _entry(hass: HomeAssistant, *, broadcast_resync: bool = True) -> MockConfigEntry:
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={
            CONF_HOST: "192.168.1.5",
            CONF_PORT: 20000,
            CONF_PASSWORD: "open",
            CONF_MAC: MAC,
        },
        options={CONF_BROADCAST_RESYNC: broadcast_resync},
    )
    entry.add_to_hass(hass)
    return entry


def _handler(hass: HomeAssistant, entry: MockConfigEntry, *, broadcast_resync: bool = True) -> MyHOMEGatewayHandler:
    with patch("custom_components.myhome.gateway.OWNGateway"):
        handler = MyHOMEGatewayHandler(hass, entry, generate_events=False, broadcast_resync=broadcast_resync)
        # Only the serial: OWNGateway has no `mac`, and setting one on the mock hid
        # that `_known_light_areas` read it (the listener died on `*1*0*0##` live).
        handler.gateway.serial = MAC
        handler.send_status_request = AsyncMock()
        return handler


async def _advance(hass: HomeAssistant) -> None:
    async_fire_time_changed(hass, dt_util.utcnow() + dt_util.dt.timedelta(seconds=1))
    await hass.async_block_till_done()


@pytest.fixture
def entry(hass: HomeAssistant) -> MockConfigEntry:
    return _entry(hass)


@pytest.fixture
def handler(hass: HomeAssistant, entry: MockConfigEntry) -> MyHOMEGatewayHandler:
    return _handler(hass, entry)


@pytest.mark.asyncio
async def test_resync_group(hass: HomeAssistant, handler: MyHOMEGatewayHandler):
    """A group frame, followed by silence, sweeps the group's own status."""
    msg = OWNMessage.parse("*1*1*#6##")

    await handler._process_message(msg)
    await _advance(hass)

    handler.send_status_request.assert_called_once()
    arg = handler.send_status_request.call_args[0][0]
    assert str(arg) == "*#1*#6##"


@pytest.mark.asyncio
async def test_resync_group_with_echo(hass: HomeAssistant, handler: MyHOMEGatewayHandler):
    """Multiple point-to-point member echoes inside the window cancel a group sweep."""
    msg_grp = OWNMessage.parse("*1*1*#6##")
    await handler._process_message(msg_grp)

    # First echo
    await handler._process_message(OWNMessage.parse("*1*1*11##"))
    # Second echo (reaches the threshold of >= 2 member echoes)
    await handler._process_message(OWNMessage.parse("*1*1*12##"))

    await _advance(hass)

    handler.send_status_request.assert_not_called()


@pytest.mark.asyncio
async def test_resync_group_single_ptp_does_not_cancel(hass: HomeAssistant, handler: MyHOMEGatewayHandler):
    """A single isolated PTP frame does not cancel a group sweep."""
    msg_grp = OWNMessage.parse("*1*1*#6##")
    await handler._process_message(msg_grp)

    # Only one echo: below the threshold of >= 2
    await handler._process_message(OWNMessage.parse("*1*1*11##"))

    await _advance(hass)

    handler.send_status_request.assert_called_once()
    arg = handler.send_status_request.call_args[0][0]
    assert str(arg) == "*#1*#6##"


@pytest.mark.asyncio
async def test_resync_ptp_echo_only_cancels_matching_area(hass: HomeAssistant, handler: MyHOMEGatewayHandler):
    """A PTP echo in area 1 cancels area 1 only; area 00 still sweeps."""
    msg_gen = OWNMessage.parse("*1*1*0##")

    with patch.object(handler, "_known_light_areas", return_value=["1", "00"]):
        await handler._process_message(msg_gen)

        # Area 1 echo arrives
        await handler._process_message(OWNMessage.parse("*1*1*12##"))
        await _advance(hass)

    handler.send_status_request.assert_called_once()
    arg = handler.send_status_request.call_args[0][0]
    assert str(arg) == "*#1*00##"


@pytest.mark.asyncio
async def test_resync_group_leading_echoes_skip_sweep(hass: HomeAssistant, handler: MyHOMEGatewayHandler):
    """Leading member echoes before the group frame (e.g. MyHomeServer1) skip the sweep."""
    # Member echoes arrive first (~0.9s before group frame on MyHomeServer1)
    await handler._process_message(OWNMessage.parse("*1*1*11##"))
    await handler._process_message(OWNMessage.parse("*1*1*12##"))

    # Group frame arrives after members have already reported
    await handler._process_message(OWNMessage.parse("*1*1*#6##"))
    await _advance(hass)

    handler.send_status_request.assert_not_called()


@pytest.mark.asyncio
async def test_resync_area_leading_echoes_skip_sweep(hass: HomeAssistant, handler: MyHOMEGatewayHandler):
    """A leading member echo before the area frame skips the sweep for that area."""
    # Area 3 member echoes before area 3 broadcast
    await handler._process_message(OWNMessage.parse("*1*1*32##"))
    await handler._process_message(OWNMessage.parse("*1*0*3##"))
    await _advance(hass)

    handler.send_status_request.assert_not_called()


@pytest.mark.asyncio
async def test_resync_general_leading_echo_skips_matching_area(hass: HomeAssistant, handler: MyHOMEGatewayHandler):
    """A leading echo in area 1 causes general sweep to skip area 1 while still sweeping area 00."""
    # Area 1 echo arrives before general command
    await handler._process_message(OWNMessage.parse("*1*1*12##"))

    msg_gen = OWNMessage.parse("*1*1*0##")
    with patch.object(handler, "_known_light_areas", return_value=["1", "00"]):
        await handler._process_message(msg_gen)
        await _advance(hass)

    # Only area 00 is swept; area 1 had leading echo
    handler.send_status_request.assert_called_once()
    arg = handler.send_status_request.call_args[0][0]
    assert str(arg) == "*#1*00##"


@pytest.mark.asyncio
async def test_resync_area(hass: HomeAssistant, handler: MyHOMEGatewayHandler):
    """A plain area frame ('3') sweeps its own raw WHERE."""
    msg = OWNMessage.parse("*1*0*3##")

    await handler._process_message(msg)
    await _advance(hass)

    handler.send_status_request.assert_called_once()
    arg = handler.send_status_request.call_args[0][0]
    assert str(arg) == "*#1*3##"


@pytest.mark.asyncio
async def test_resync_area_00_never_sends_banned_general_request(hass: HomeAssistant, handler: MyHOMEGatewayHandler):
    """Area '00' must sweep '*#1*00##', never the banned '*#1*0##'."""
    msg = OWNMessage.parse("*1*1*00##")

    await handler._process_message(msg)
    await _advance(hass)

    handler.send_status_request.assert_called_once()
    arg = handler.send_status_request.call_args[0][0]
    assert str(arg) == "*#1*00##"
    assert str(arg) != "*#1*0##"


@pytest.mark.asyncio
async def test_resync_area_100_targets_area_ten(hass: HomeAssistant, handler: MyHOMEGatewayHandler):
    """Area '100' (area 10) must sweep '*#1*100##', not the point address '*#1*10##'."""
    msg = OWNMessage.parse("*1*1*100##")

    await handler._process_message(msg)
    await _advance(hass)

    handler.send_status_request.assert_called_once()
    arg = handler.send_status_request.call_args[0][0]
    assert str(arg) == "*#1*100##"
    assert str(arg) != "*#1*10##"


@pytest.mark.asyncio
async def test_resync_general(hass: HomeAssistant, handler: MyHOMEGatewayHandler):
    """A general frame sweeps every known light area, never the general itself."""
    msg = OWNMessage.parse("*1*1*0##")

    with patch.object(handler, "_known_light_areas", return_value=["1", "00", "100"]):
        await handler._process_message(msg)
        await _advance(hass)

    assert handler.send_status_request.call_count == 3
    frames = {str(call[0][0]) for call in handler.send_status_request.call_args_list}
    assert frames == {"*#1*1##", "*#1*00##", "*#1*100##"}
    assert "*#1*0##" not in frames


@pytest.mark.asyncio
async def test_known_light_areas_from_registry(hass: HomeAssistant, entry: MockConfigEntry):
    """_known_light_areas reads real light registry entries for this config entry."""
    handler = _handler(hass, entry)
    registry = er.async_get(hass)
    for where in ("12", "0015", "1003", "#6"):
        registry.async_get_or_create(
            "light", DOMAIN, f"{MAC}-1-{where}", config_entry=entry,
        )
    # Routed address: Address.from_device_id("12#4#01") extracts where="12", yielding area "1".
    registry.async_get_or_create(
        "light", DOMAIN, f"{MAC}-1-12#4#01", config_entry=entry,
    )
    # WHO=1 switch/relay actuator (e.g. F411U2 outlet) also contributes its area.
    registry.async_get_or_create(
        "switch", DOMAIN, f"{MAC}-1-23", config_entry=entry,
    )
    # A sensor sharing WHO=1 addressing must not be treated as a light area.
    registry.async_get_or_create(
        "binary_sensor", DOMAIN, f"{MAC}-1-14", config_entry=entry,
    )
    # A unique id with no device-id part (parse_unique_id -> empty key) is skipped.
    registry.async_get_or_create(
        "light", DOMAIN, f"{MAC}-", config_entry=entry,
    )

    assert handler._known_light_areas() == ["00", "1", "100", "2"]


@pytest.mark.asyncio
async def test_resync_debounces_repeated_group_frames(hass: HomeAssistant, handler: MyHOMEGatewayHandler):
    """Two group frames for the same group inside the window produce one sweep."""
    await handler._process_message(OWNMessage.parse("*1*1*#6##"))
    await handler._process_message(OWNMessage.parse("*1*0*#6##"))

    await _advance(hass)

    handler.send_status_request.assert_called_once()


@pytest.mark.asyncio
async def test_resync_off(hass: HomeAssistant):
    """The option disables scheduling entirely."""
    entry = _entry(hass, broadcast_resync=False)
    handler = _handler(hass, entry, broadcast_resync=False)

    await handler._process_message(OWNMessage.parse("*1*1*#6##"))
    await _advance(hass)

    handler.send_status_request.assert_not_called()


@pytest.mark.asyncio
async def test_close_listener_cancels_pending_resync(hass: HomeAssistant, handler: MyHOMEGatewayHandler):
    """Closing the listener cancels a pending sweep so it never fires."""
    await handler._process_message(OWNMessage.parse("*1*1*#6##"))
    assert handler._resync_timers

    await handler.close_listener()
    await _advance(hass)

    handler.send_status_request.assert_not_called()
    assert not handler._resync_timers

    # An in-flight _resync_broadcast task after listener teardown is a no-op
    await handler._resync_broadcast("1")
    handler.send_status_request.assert_not_called()


def test_known_light_areas_without_valid_entry_id(hass: HomeAssistant):
    """A config entry without a usable entry_id yields no areas."""
    entry = MagicMock()
    entry.entry_id = None
    with patch("custom_components.myhome.gateway.OWNGateway"):
        handler = MyHOMEGatewayHandler(hass, entry, generate_events=False)
        handler.gateway.serial = MAC

    assert handler._known_light_areas() == []


GOLDEN_MH200_BURST = [
    "*1*0*11##",
    "*2*0*11#4#02##",
    "*1*0*12##",
    "*1*0*21##",
    "*1*0*31##",
    "*1*0*29##",
    "*1*0*32##",
    "*1*0*41##",
    "*1*0*51##",
    "*1*0*42##",
    "*1*0*52##",
    "*1*0*61##",
    "*1*0*71##",
    "*1*1*81##",
    "*1*0*82##",
    "*1*0*83##",
    "*1*10*62##",
    "*#1001*74*11*111110111111111111110111##",
    "*#13**15*4##",
    "*2*0*15#4#02##",
]


@pytest.mark.asyncio
async def test_resync_golden_mh200_sweep_burst(hass: HomeAssistant, handler: MyHOMEGatewayHandler):
    """
    Test that a real-world burst of responses from an MH200 during an area status sweep
    (which can include interleaved WHO=2 automation events, WHO=13 gateway events,
    and WHO=1001 diagnostic events) is processed safely without triggering further resync loops.
    """
    for frame in GOLDEN_MH200_BURST:
        msg = OWNMessage.parse(frame)
        if msg is not None:
            await handler._process_message(msg)

    await _advance(hass)

    # Point-to-point status reports and interleaved events must NOT spawn broad sweeps.
    handler.send_status_request.assert_not_called()


@pytest.mark.asyncio
async def test_resync_cascade_replay_with_golden_burst(hass: HomeAssistant, handler: MyHOMEGatewayHandler):
    """
    Replay: A general lighting command schedules sweeps for areas 1 and 00.
    The golden MH200 burst arrives during the debounce window.
    Frames in the burst cancel area 1, while area 00 (silent in the burst) still sweeps.
    """
    msg_gen = OWNMessage.parse("*1*1*0##")

    with patch.object(handler, "_known_light_areas", return_value=["1", "00"]):
        await handler._process_message(msg_gen)
        assert "1" in handler._resync_timers
        assert "00" in handler._resync_timers

        for frame in GOLDEN_MH200_BURST:
            msg = OWNMessage.parse(frame)
            if msg is not None:
                await handler._process_message(msg)

        # Area 1 had echoes in the burst (*1*0*11##, *1*0*12##), so its timer was popped.
        assert "1" not in handler._resync_timers
        # Area 00 had no echoes in the burst, so its timer remains armed.
        assert "00" in handler._resync_timers

        await _advance(hass)

    handler.send_status_request.assert_called_once()
    arg = handler.send_status_request.call_args[0][0]
    assert str(arg) == "*#1*00##"


@pytest.mark.asyncio
async def test_resync_ptp_evicts_stale_leading_echoes(hass: HomeAssistant, handler: MyHOMEGatewayHandler):
    """Point-to-point echoes older than the leading window are evicted when a new PTP echo arrives."""
    stale_time = 100.0
    handler._recent_ptp.append((stale_time, "11", "1"))

    with patch("custom_components.myhome.gateway.time.monotonic", return_value=stale_time + RESYNC_LEADING_WINDOW_S + 1.0):
        await handler._process_message(OWNMessage.parse("*1*1*12##"))

    assert len(handler._recent_ptp) == 1
    assert handler._recent_ptp[0][1] == "12"


@pytest.mark.asyncio
async def test_resync_broadcast_evicts_stale_leading_echoes(hass: HomeAssistant, handler: MyHOMEGatewayHandler):
    """Point-to-point echoes older than the leading window are evicted before scheduling a resync."""
    stale_time = 100.0
    handler._recent_ptp.append((stale_time, "32", "3"))

    # An area 3 command arrives after the leading window has elapsed.
    # The stale echo for area 3 must be evicted so the area 3 sweep is NOT skipped.
    with patch("custom_components.myhome.gateway.time.monotonic", return_value=stale_time + RESYNC_LEADING_WINDOW_S + 1.0):
        await handler._process_message(OWNMessage.parse("*1*0*3##"))
        await _advance(hass)

    assert len(handler._recent_ptp) == 0
    handler.send_status_request.assert_called_once()
    arg = handler.send_status_request.call_args[0][0]
    assert str(arg) == "*#1*3##"


@pytest.mark.parametrize(
    "frame",
    [
        "*#1*0*7*0*1*21##",  # PIR timeout report (WHERE=0 parsed as is_general)
        "*#1*0*5*3##",       # PIR sensitivity report (WHERE=0 parsed as is_general)
        "*#1*1*5*3##",       # PIR sensitivity report (WHERE=1 parsed as is_area)
        "*#1*1*7*0*1*21##",  # PIR timeout report (WHERE=1 parsed as is_area)
    ],
)
@pytest.mark.asyncio
async def test_dimension_frames_do_not_trigger_resync_sweep(
    hass: HomeAssistant, handler: MyHOMEGatewayHandler, frame: str
):
    """Dimension responses (e.g. PIR sensitivity / timeout) must never schedule sweeps (Issue #612)."""
    with patch.object(handler, "_known_light_areas", return_value=["1", "00"]):
        await handler._process_message(OWNMessage.parse(frame))
        await _advance(hass)

    assert not handler._resync_timers
    handler.send_status_request.assert_not_called()


@pytest.mark.parametrize(
    "frame",
    [
        "*1*34*1##",   # Motion detected in area 1
        "*1*34*00##",  # Motion detected in area 00
    ],
)
@pytest.mark.asyncio
async def test_motion_sensor_frames_do_not_trigger_resync_sweep(
    hass: HomeAssistant, handler: MyHOMEGatewayHandler, frame: str
):
    """Motion sensor detection events (WHAT=34) must never schedule sweeps (Issue #612)."""
    with patch.object(handler, "_known_light_areas", return_value=["1", "00"]):
        await handler._process_message(OWNMessage.parse(frame))
        await _advance(hass)

    assert not handler._resync_timers
    handler.send_status_request.assert_not_called()


@pytest.mark.parametrize(
    ("frame", "expected_targets"),
    [
        ("*1*2*0##", {"*#1*1##", "*#1*00##"}),   # General dimmer level 2 (20%)
        ("*1*10*0##", {"*#1*1##", "*#1*00##"}),  # General dimmer level 10 (100%)
        ("*1*2*1##", {"*#1*1##"}),                # Area 1 dimmer level 2
        ("*1*10*#2##", {"*#1*#2##"}),             # Group #2 dimmer level 10
    ],
)
@pytest.mark.asyncio
async def test_stepped_dimmer_commands_trigger_resync_sweep(
    hass: HomeAssistant, handler: MyHOMEGatewayHandler, frame: str, expected_targets: set[str]
):
    """Stepped dimming level commands (WHAT 2..10) must schedule sweeps for general, area, or group."""
    with patch.object(handler, "_known_light_areas", return_value=["1", "00"]):
        await handler._process_message(OWNMessage.parse(frame))
        await _advance(hass)

    frames = {str(call[0][0]) for call in handler.send_status_request.call_args_list}
    assert frames == expected_targets


@pytest.mark.asyncio
async def test_dimension_frames_do_not_fire_light_bus_events(
    hass: HomeAssistant, handler: MyHOMEGatewayHandler
):
    """Dimension frames with WHERE=0 must not fire myhome_general_light_event."""
    events = []
    hass.bus.async_listen("myhome_general_light_event", events.append)

    await handler._process_message(OWNMessage.parse("*#1*0*7*0*1*21##"))
    await hass.async_block_till_done()

    assert len(events) == 0


@pytest.mark.parametrize(
    "frame",
    [
        "*1*34*1##",    # Motion frame: is_on is None
        "*#1*0*5*3##",  # Dimension frame: is_dimension is True
    ],
)
def test_lighting_resync_manager_schedule_resync_guards(
    handler: MyHOMEGatewayHandler, frame: str
) -> None:
    """LightingResyncManager.schedule_resync rejects non-actuation and dimension messages defensively."""
    handler._resync_manager.schedule_resync(OWNMessage.parse(frame))
    assert not handler._resync_timers


@pytest.mark.asyncio
async def test_partial_area_echoes_do_not_skip_or_cancel_area_sweep(
    hass: HomeAssistant, handler: MyHOMEGatewayHandler
) -> None:
    """Issue #746: Partial point-to-point echoes in an area must not skip or cancel `*#1*<area>##` until all registered entities in that area have echoed."""
    reg = er.async_get(hass)
    mac = handler.mac
    entry_id = handler.config_entry.entry_id
    for domain, where in (
        ("light", "01"),
        ("light", "03"),
        ("switch", "04"),
        ("light", "09"),
        ("light", "0011"),
    ):
        reg.async_get_or_create(
            domain,
            DOMAIN,
            f"myhome-{mac}-1-{where}",
            config_entry=handler.config_entry,
        )
    assert handler._resync_manager.known_area_entities("00") == {
        "01",
        "03",
        "04",
        "09",
        "0011",
    }
    assert entry_id == handler.config_entry.entry_id

    # Leading echo from 01 before General OFF (*1*0*0##), followed by trailing
    # echoes from 03, 09, 0011 — but 04 (switch) never echoes.
    await handler._process_message(OWNMessage.parse("*1*0*01##"))
    await handler._process_message(OWNMessage.parse("*1*0*0##"))
    assert "00" in handler._resync_timers

    await handler._process_message(OWNMessage.parse("*1*0*03##"))
    await handler._process_message(OWNMessage.parse("*1*0*09##"))
    await handler._process_message(OWNMessage.parse("*1*0*0011##"))
    # Area 00 timer must still be armed because 04 has not echoed!
    assert "00" in handler._resync_timers

    await _advance(hass)
    frames = [str(call[0][0]) for call in handler.send_status_request.call_args_list]
    assert frames == ["*#1*00##"]

    # Now test when all registered entities (including 04) echo: sweep is cancelled!
    handler.send_status_request.reset_mock()
    await handler._process_message(OWNMessage.parse("*1*0*00##"))
    assert "00" in handler._resync_timers
    for where in ("01", "03", "04", "09", "0011"):
        await handler._process_message(OWNMessage.parse(f"*1*0*{where}##"))
    assert "00" not in handler._resync_timers
    await _advance(hass)
    handler.send_status_request.assert_not_called()


@pytest.mark.asyncio
async def test_single_registered_entity_in_area_not_cancelled_by_unregistered_sibling_echo(
    hass: HomeAssistant, handler: MyHOMEGatewayHandler
) -> None:
    """Issue #746 edge case: When only 1 entity (`04`) is registered in Area 00, an echo from unregistered `01` must not cancel `*#1*00##`."""
    reg = er.async_get(hass)
    mac = handler.mac
    reg.async_get_or_create(
        "switch",
        DOMAIN,
        f"myhome-{mac}-1-04",
        config_entry=handler.config_entry,
    )
    assert handler._resync_manager.known_area_entities("00") == {"04"}

    await handler._process_message(OWNMessage.parse("*1*0*0##"))
    assert "00" in handler._resync_timers

    # Unregistered actuator 01 in Area 00 echoes OFF; 04 has not echoed yet
    await handler._process_message(OWNMessage.parse("*1*0*01##"))
    assert "00" in handler._resync_timers

    await _advance(hass)
    frames = [str(call[0][0]) for call in handler.send_status_request.call_args_list]
    assert frames == ["*#1*00##"]


@pytest.mark.asyncio
async def test_opposite_polarity_leading_echo_does_not_skip_area_sweep(
    hass: HomeAssistant, handler: MyHOMEGatewayHandler
) -> None:
    """Turning an entity ON right before General OFF must not count as a leading OFF echo."""
    reg = er.async_get(hass)
    mac = handler.mac
    reg.async_get_or_create(
        "switch",
        DOMAIN,
        f"myhome-{mac}-1-04",
        config_entry=handler.config_entry,
    )

    # 04 turns ON within the 1.5s leading window right before General OFF
    await handler._process_message(OWNMessage.parse("*1*1*04##"))
    await handler._process_message(OWNMessage.parse("*1*0*0##"))
    assert "00" in handler._resync_timers

    await _advance(hass)
    frames = [str(call[0][0]) for call in handler.send_status_request.call_args_list]
    assert frames == ["*#1*00##"]


@pytest.mark.asyncio
async def test_group_resync_ignores_cross_room_and_opposite_polarity_echoes(
    hass: HomeAssistant, handler: MyHOMEGatewayHandler
) -> None:
    """Echoes from another room or opposite polarity must never suppress a group's sweep (#734)."""
    mgr = handler._resync_manager
    mgr.register_group_members("#6", ["11", "12"])
    assert mgr.group_members["#6"] == {"11", "12"}

    # Leading echoes for #6's members with opposite polarity (OFF before ON) do NOT skip #6
    await handler._process_message(OWNMessage.parse("*1*0*11##"))
    await handler._process_message(OWNMessage.parse("*1*0*12##"))
    # Unrelated Room 2 leading ON echoes do NOT skip #6 either
    await handler._process_message(OWNMessage.parse("*1*1*21##"))
    await handler._process_message(OWNMessage.parse("*1*1*22##"))

    # Group #6 turned ON
    await handler._process_message(OWNMessage.parse("*1*1*#6##"))
    assert "#6" in handler._resync_timers

    # Unrelated Room 2 echoes arrive -> must NOT cancel #6
    await handler._process_message(OWNMessage.parse("*1*1*21##"))
    await handler._process_message(OWNMessage.parse("*1*1*22##"))
    assert "#6" in handler._resync_timers

    # Member 11 echoes ON, then flips OFF before member 12 echoes ON -> must NOT cancel #6
    await handler._process_message(OWNMessage.parse("*1*1*11##"))
    await handler._process_message(OWNMessage.parse("*1*0*11##"))
    await handler._process_message(OWNMessage.parse("*1*1*12##"))
    assert "#6" in handler._resync_timers

    # Opposite polarity frames for #6's members arrive -> must NOT cancel #6
    await handler._process_message(OWNMessage.parse("*1*0*11##"))
    await handler._process_message(OWNMessage.parse("*1*0*12##"))
    assert "#6" in handler._resync_timers

    await _advance(hass)
    handler.send_status_request.assert_called_once()
    assert str(handler.send_status_request.call_args[0][0]) == "*#1*#6##"

    # Clearing group members removes the entry
    mgr.register_group_members("6", [])
    assert "#6" not in mgr.group_members

    # Split leading + trailing echoes for #6 = {11, 12}:
    # 11 echoes ON before *1*1*#6## (seeded), duplicate 11 ON after *1*1*#6## stays 1,
    # 11 flipping OFF discards the seed so 12 ON alone does not cancel, and 11 ON
    # again completes {11, 12} and cancels #6.
    handler.send_status_request.reset_mock()
    mgr.register_group_members("#6", ["11", "12"])
    await handler._process_message(OWNMessage.parse("*1*1*11##"))
    await handler._process_message(OWNMessage.parse("*1*1*#6##"))
    assert "#6" in handler._resync_timers
    assert mgr.resync_group_echoes["#6"] == 1
    await handler._process_message(OWNMessage.parse("*1*1*11##"))
    assert "#6" in handler._resync_timers
    assert mgr.resync_group_echoes["#6"] == 1
    await handler._process_message(OWNMessage.parse("*1*0*11##"))
    assert mgr.resync_group_echoes["#6"] == 0
    await handler._process_message(OWNMessage.parse("*1*1*12##"))
    assert "#6" in handler._resync_timers
    await handler._process_message(OWNMessage.parse("*1*1*11##"))
    assert "#6" not in handler._resync_timers
    await _advance(hass)
    handler.send_status_request.assert_not_called()


@pytest.mark.asyncio
async def test_opposite_polarity_trailing_echo_does_not_cancel_area_sweep(
    hass: HomeAssistant, entry: MockConfigEntry
) -> None:
    """Trailing opposite-polarity echoes must not cancel an area or general sweep (#740)."""
    handler = _handler(hass, entry)
    registry = er.async_get(hass)
    registry.async_get_or_create("light", DOMAIN, f"{MAC}-1-11", config_entry=entry)

    # Area 1 ON followed by the only known entity echoing OFF must NOT cancel the sweep
    await handler._process_message(OWNMessage.parse("*1*1*1##"))
    assert "1" in handler._resync_timers
    await handler._process_message(OWNMessage.parse("*1*0*11##"))
    assert "1" in handler._resync_timers

    await _advance(hass)
    handler.send_status_request.assert_called_once()
    assert str(handler.send_status_request.call_args[0][0]) == "*#1*1##"

    # Multi-entity area: 11 echoes ON before Area 1 ON (seeded), then 11 turns OFF
    # during the debounce window before 12 echoes ON -> sweep must NOT be cancelled
    handler.send_status_request.reset_mock()
    registry.async_get_or_create("light", DOMAIN, f"{MAC}-1-12", config_entry=entry)
    await handler._process_message(OWNMessage.parse("*1*1*11##"))
    await handler._process_message(OWNMessage.parse("*1*1*1##"))
    assert "1" in handler._resync_timers
    await handler._process_message(OWNMessage.parse("*1*0*11##"))
    await handler._process_message(OWNMessage.parse("*1*1*12##"))
    assert "1" in handler._resync_timers

    # Once 11 echoes ON again inside the window, both 11 and 12 are ON -> cancelled
    await handler._process_message(OWNMessage.parse("*1*1*11##"))
    assert "1" not in handler._resync_timers
    await _advance(hass)
    handler.send_status_request.assert_not_called()


@pytest.mark.asyncio
async def test_group_resync_preserves_bus_interface_on_members_and_group_key(
    hass: HomeAssistant, handler: MyHOMEGatewayHandler
) -> None:
    """Group keys and member addresses preserve F422 #4#<interface> routing suffixes (#740)."""
    from types import SimpleNamespace

    from custom_components.myhome.light_group import MyHOMELightGroup

    mgr = handler._resync_manager
    mgr.register_group_members("#6", ["11#4#1"])
    assert mgr.group_members["#6"] == {"11#4#01"}

    # Local-bus leading echo 11 must not skip #6 when member is on private bus 11#4#01
    await handler._process_message(OWNMessage.parse("*1*1*11##"))
    await handler._process_message(OWNMessage.parse("*1*1*#6##"))
    assert "#6" in handler._resync_timers

    # Local-bus trailing echo 11 must not cancel #6
    await handler._process_message(OWNMessage.parse("*1*1*11##"))
    assert "#6" in handler._resync_timers

    # Private-bus trailing echo 11#4#01 cancels #6
    await handler._process_message(OWNMessage.parse("*1*1*11#4#01##"))
    assert "#6" not in handler._resync_timers

    # Unscoped routed group #6#4#01 ignores local-bus leading and trailing echoes
    mgr.register_group_members("#6", [])
    await handler._process_message(OWNMessage.parse("*1*1*11##"))
    await handler._process_message(OWNMessage.parse("*1*1*12##"))
    await handler._process_message(OWNMessage.parse("*1*1*#6#4#01##"))
    assert "#6#4#01" in handler._resync_timers
    await handler._process_message(OWNMessage.parse("*1*1*11##"))
    await handler._process_message(OWNMessage.parse("*1*1*12##"))
    assert "#6#4#01" in handler._resync_timers

    await _advance(hass)
    handler.send_status_request.assert_called_once()
    assert str(handler.send_status_request.call_args[0][0]) == "*#1*#6#4#01##"

    # Unscoped routed group #6#4#01 is skipped by 2 leading echoes on the same bus 01
    handler.send_status_request.reset_mock()
    await handler._process_message(OWNMessage.parse("*1*1*11#4#01##"))
    await handler._process_message(OWNMessage.parse("*1*1*12#4#01##"))
    await handler._process_message(OWNMessage.parse("*1*1*#6#4#01##"))
    assert "#6#4#01" not in handler._resync_timers

    # _message_interface also supports messages exposing where_param without .interface
    synthetic_msg = SimpleNamespace(where="11", where_param=["4", "02"])
    assert mgr._message_interface(synthetic_msg) == "02"

    # A routed group (#6#4#01) with bare PointToPoint members (["11", "12"] from myhome.yaml)
    # inherits the group's bus interface for both resync registration and entity resolution.
    reg = er.async_get(hass)
    m1 = reg.async_get_or_create(
        "light", DOMAIN, f"{handler.mac}-1-11#4#01", config_entry=handler.config_entry
    )
    m2 = reg.async_get_or_create(
        "light", DOMAIN, f"{handler.mac}-1-12#4#01", config_entry=handler.config_entry
    )
    grp_entity = MyHOMELightGroup(
        hass=hass,
        name="Riser 01 Group 6",
        device_id="1-#6#4#1",
        group=6,
        gateway=handler,
        members=["11", "12"],
        dimmable=False,
        color_temp=False,
        rgb=False,
        hs=False,
    )
    grp_entity.hass = hass
    grp_entity.entity_id = "light.riser_01_group_6"
    grp_entity.async_write_ha_state = MagicMock()
    assert grp_entity._full_where == "#6#4#01"
    await grp_entity.async_added_to_hass()
    assert mgr.group_members["#6#4#01"] == {"11#4#01", "12#4#01"}
    assert grp_entity._member_entity_ids == [m1.entity_id, m2.entity_id]

    # Assumed-state routed group ignores group frames from the local bus
    assumed_routed = MyHOMELightGroup(
        hass=hass,
        name="Assumed Riser 01 Group 7",
        device_id="1-#7#4#01",
        group=7,
        gateway=handler,
        members=[],
        dimmable=False,
        color_temp=False,
        rgb=False,
        hs=False,
    )
    assumed_routed.hass = hass
    assumed_routed.entity_id = "light.assumed_riser_01_group_7"
    assumed_routed.async_write_ha_state = MagicMock()
    assumed_routed.handle_event(OWNMessage.parse("*1*1*#7##"))
    assert assumed_routed.is_on is None
    assumed_routed.handle_event(OWNMessage.parse("*1*1*#7#4#01##"))
    assert assumed_routed.is_on is True


@pytest.mark.asyncio
async def test_multi_entity_area_sweep_waits_for_all_known_entities(
    hass: HomeAssistant, entry: MockConfigEntry
) -> None:
    """An area sweep only skips/cancels once all known entities in that area have echoed (#734)."""
    handler = _handler(hass, entry)
    registry = er.async_get(hass)
    for where in ("11", "12", "13"):
        registry.async_get_or_create(
            "light", DOMAIN, f"{MAC}-1-{where}", config_entry=entry
        )
    # Invalid unique_id is ignored by known_area_entities
    registry.async_get_or_create("light", DOMAIN, f"{MAC}-", config_entry=entry)

    # Area 1 OFF broadcast followed by only 1 of 3 known entities echoing
    await handler._process_message(OWNMessage.parse("*1*0*1##"))
    await handler._process_message(OWNMessage.parse("*1*0*11##"))
    assert "1" in handler._resync_timers

    await _advance(hass)
    handler.send_status_request.assert_called_once()
    assert str(handler.send_status_request.call_args[0][0]) == "*#1*1##"

    # Now when all 3 known entities echo, the sweep IS cancelled
    handler.send_status_request.reset_mock()
    await handler._process_message(OWNMessage.parse("*1*0*1##"))
    await handler._process_message(OWNMessage.parse("*1*0*11##"))
    await handler._process_message(OWNMessage.parse("*1*0*12##"))
    await handler._process_message(OWNMessage.parse("*1*0*13##"))
    assert "1" not in handler._resync_timers
    await _advance(hass)
    handler.send_status_request.assert_not_called()


@pytest.mark.asyncio
async def test_replay_f461_live_2026_10_18_run1_lights_group_and_general(
    hass: HomeAssistant, handler: MyHOMEGatewayHandler
) -> None:
    """Replay the live F461 run1_lights_group_and_general trace on its own timeline (#734, #740).

    The frames are fed at their captured timestamps on a virtual clock, with a
    virtual ``async_call_later``, so the 1.5 s leading window and the 0.5 s
    debounce see the real bus spacing. Fed back to back, the whole trace falls
    inside one leading window and the replay passes on the pre-#740 logic too.
    """
    import itertools
    import json
    from pathlib import Path
    from types import SimpleNamespace
    from typing import Any

    trace_path = (
        Path(__file__).parent
        / "fixtures"
        / "traces"
        / "f461_live_2026-10-18"
        / "myhome_trace_F461_live_2026-10-18.json"
    )
    trace = json.loads(trace_path.read_text(encoding="utf-8"))
    run1_frames = [
        f
        for f in trace["frames"]
        if f.get("run") == "run1_lights_group_and_general"
        and f.get("direction") == "rx"
        and f["raw"].startswith(("*1*", "*#1*"))
    ]
    assert len(run1_frames) == 28

    reg = er.async_get(hass)
    mac = handler.mac
    for where in ("06", "07"):
        reg.async_get_or_create(
            "light",
            DOMAIN,
            f"{mac}-1-{where}",
            config_entry=handler.config_entry,
        )

    async def _run_timed_replay(members: list[str]) -> tuple[dict[str, bool], list[tuple[float, str]]]:
        handler._resync_manager.cancel_all()
        handler._resync_manager.register_group_members("#1", members)
        clock = SimpleNamespace(now=0.0)
        pending: dict[int, tuple[float, Any]] = {}
        keys = itertools.count()
        sent: list[tuple[float, str]] = []

        def _call_later(_hass: HomeAssistant, delay: float, cb: Any) -> Any:
            key = next(keys)
            pending[key] = (clock.now + delay, cb)
            return lambda: pending.pop(key, None)

        async def _run_due(until: float) -> None:
            for key, (due, cb) in sorted(pending.items(), key=lambda kv: kv[1][0]):
                if due <= until and key in pending:
                    del pending[key]
                    clock.now = due
                    cb(None)
                    await hass.async_block_till_done()

        async def _send(cmd: Any) -> None:
            sent.append((round(clock.now, 3), str(cmd)))

        handler.send_status_request.side_effect = _send
        armed_after: dict[str, bool] = {}
        with (
            patch("custom_components.myhome.gateway.time", SimpleNamespace(monotonic=lambda: clock.now)),
            patch("custom_components.myhome.gateway.async_call_later", _call_later),
        ):
            for frame in run1_frames:
                await _run_due(frame["timestamp"])
                clock.now = frame["timestamp"]
                msg = OWNMessage.parse(frame["raw"])
                assert msg is not None
                await handler._process_message(msg)
                if frame["raw"] in ("*1*1*#1##", "*1*0*#1##"):
                    armed_after[frame["raw"]] = "#1" in handler._resync_timers
            await _run_due(clock.now + 5.0)
        return armed_after, sent

    # 1. With the plant's two group #1 members (06, 07) registered (and with no
    # members registered, exercising the 2-distinct-address same-polarity fallback):
    # *1*0*#1## (9.447) follows the ON echoes of 06 (8.786) and 07 (8.898) inside the
    # 1.5 s leading window: those are the wrong polarity, so the OFF sweep is armed
    # rather than skipped, and the OFF echoes at 9.505 / 9.555 then cancel it.
    for declared in (["06", "07"], []):
        armed_after, sent = await _run_timed_replay(declared)
        assert armed_after == {"*1*1*#1##": True, "*1*0*#1##": True}
        assert not handler._resync_timers
        # The group #1 sweeps are cancelled by the member echoes, ~50 ms apart. After
        # the general *1*1*0## (10.183) and *1*0*0## (12.461) the F461 echoes 06 and
        # 07 0.7 s and 1.5 s later, past the 0.5 s debounce, so both area 00 sweeps go out.
        assert sent == [
            (round(10.183 + RESYNC_DEBOUNCE_S, 3), "*#1*00##"),
            (round(12.461 + RESYNC_DEBOUNCE_S, 3), "*#1*00##"),
        ]

    # 2. When a third member (08) is also declared in group #1 but never echoes in
    # the trace, the 06 and 07 echoes alone must NOT cancel the #1 sweeps.
    armed_missing, sent_missing = await _run_timed_replay(["06", "07", "08"])
    assert armed_missing == {"*1*1*#1##": True, "*1*0*#1##": True}
    assert sent_missing == [
        (round(8.738 + RESYNC_DEBOUNCE_S, 3), "*#1*#1##"),
        (round(9.447 + RESYNC_DEBOUNCE_S, 3), "*#1*#1##"),
        (round(10.183 + RESYNC_DEBOUNCE_S, 3), "*#1*00##"),
        (round(12.461 + RESYNC_DEBOUNCE_S, 3), "*#1*00##"),
    ]

