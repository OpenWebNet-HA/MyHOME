"""Cover calibration: measure up/down travel on the bus, store it, apply it (myhome.calibrate_cover)."""
import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from homeassistant.components.cover import ATTR_POSITION
from homeassistant.exceptions import HomeAssistantError
from OWNd.message import OWNEvent

from custom_components.myhome.const import CONF_COVER_TRAVEL_TIMES, EVENT_COVER_CALIBRATION
from custom_components.myhome.cover import (
    _LAST_CALIBRATION_TRACE,
    CalibrationInterrupted,
    MyHOMECover,
    _stored_calibration,
    async_stop_cover_calibration,
    get_last_calibration_trace,
)
from tests.conftest import attach_runtime, bind_entity


@pytest.fixture(autouse=True)
def _fresh_calibration_trace():
    """The trace buffer is module-level; one test's runs must not leak into another's export."""
    _LAST_CALIBRATION_TRACE.clear()
    yield
    _LAST_CALIBRATION_TRACE.clear()


class Clock:
    def __init__(self):
        self.now = 0.0


@pytest.fixture
def clock():
    return Clock()


@pytest.fixture
def fake_time(clock):
    with patch("custom_components.myhome.cover.time") as mock_time:
        mock_time.monotonic.side_effect = lambda: clock.now
        yield mock_time


@pytest.fixture
def sleeps(clock):
    """asyncio.sleep in the cover module advances the fake clock instead of waiting."""
    real_sleep = asyncio.sleep
    recorded = []

    async def fake_sleep(delay, *args, **kwargs):
        recorded.append(delay)
        clock.now += delay
        await real_sleep(0)

    with patch("custom_components.myhome.cover.asyncio.sleep", side_effect=fake_sleep):
        yield recorded


async def _yield(n=4):
    for _ in range(n):
        await asyncio.sleep(0)


@pytest.fixture
def gateway():
    gw = MagicMock()
    gw.mac = "00:03:50:00:00:01"
    gw.log_id = "[MH200 gateway - test]"
    gw.availability_signal = "myhome_avail"
    gw.available = True
    gw.device_registry_id = None
    gw.send_status_request = AsyncMock()
    gw.config_entry = MagicMock()
    gw.config_entry.options = {}
    gw.deliveries = []

    async def _send(message):
        fut = asyncio.get_running_loop().create_future()
        gw.deliveries.append((str(message), fut))
        return fut

    gw.send = AsyncMock(side_effect=_send)
    return gw


def _make_cover(hass, gateway, **kwargs):
    kwargs.setdefault("name", "Bedroom shutter")
    kwargs.setdefault("entity_name", None)
    kwargs.setdefault("device_id", "21")
    kwargs.setdefault("who", "2")
    kwargs.setdefault("where", "21")
    kwargs.setdefault("interface", None)
    kwargs.setdefault("advanced", False)
    kwargs.setdefault("manufacturer", "BTicino")
    kwargs.setdefault("model", "Shutter")
    kwargs.setdefault("travel_time", 25)
    c = MyHOMECover(
        hass=hass,
        gateway=gateway,
        **kwargs,
    )
    c.hass = hass
    c.entity_id = "cover.bedroom_shutter"
    c.async_write_ha_state = MagicMock()
    c.async_schedule_update_ha_state = MagicMock()
    return c


async def _drive_run(cover, gateway, clock, *, direction_frame: str, write_delay: float, motor_delay: float, run: float):
    """Play the bus for one calibration run: write, motor-start echo, stop after `run` seconds."""
    await _yield()
    _, written = gateway.deliveries[-1]
    clock.now += write_delay
    written.set_result(clock.now)
    await _yield()
    clock.now += motor_delay
    cover.handle_event(OWNEvent.parse(f"*2*{direction_frame}*21##"))  # motor start echo -> anchor
    await _yield()
    clock.now += run
    cover.handle_event(OWNEvent.parse("*2*0*21##"))  # actuator stop status
    await _yield()


# ── the measurement itself ───────────────────────────────────────────────


async def test_calibration_measures_down_and_up_and_persists(hass, gateway, clock, fake_time, sleeps):
    cover = _make_cover(hass, gateway)
    events = []
    hass.bus.async_listen(EVENT_COVER_CALIBRATION, lambda ev: events.append(ev.data))
    task = asyncio.create_task(cover.async_calibrate())

    # run 1: up to the end stop (not timed), run 2: down 18.4 s, run 3: up 20.1 s
    await _drive_run(cover, gateway, clock, direction_frame="1", write_delay=0.3, motor_delay=0.55, run=30.0)
    await _drive_run(cover, gateway, clock, direction_frame="2", write_delay=0.2, motor_delay=0.55, run=18.4)
    await _drive_run(cover, gateway, clock, direction_frame="1", write_delay=0.2, motor_delay=0.55, run=20.1)
    result = await asyncio.wait_for(task, 5)

    assert result["down"] == pytest.approx(18.4, abs=0.01)
    assert result["up"] == pytest.approx(20.1, abs=0.01)
    assert result["measured_at"]
    assert [f for f, _ in gateway.deliveries] == ["*2*1*21##", "*2*2*21##", "*2*1*21##"]

    attrs = cover.extra_state_attributes
    assert attrs["travel_time"] == 18 and attrs["travel_time_down"] == pytest.approx(18.4)
    assert attrs["travel_time_up"] == pytest.approx(20.1)
    assert attrs["calibration_source"] == "measured" and attrs["calibrated_at"] == result["measured_at"]
    assert cover.current_cover_position == 100 and cover.is_closed is False
    assert cover._calibrating is False

    # persisted into the config entry options under the cover's device id
    hass.config_entries.async_update_entry = MagicMock()
    cover._persist_calibration(result)
    entry, kwargs = hass.config_entries.async_update_entry.call_args.args[0], hass.config_entries.async_update_entry.call_args.kwargs
    assert entry is gateway.config_entry
    assert kwargs["options"][CONF_COVER_TRAVEL_TIMES]["21"] == result

    await hass.async_block_till_done()
    phases = [(e["phase"], e.get("direction")) for e in events]
    assert phases == [("start", None), ("run", "open"), ("run", "close"), ("run", "open"), ("done", None)]
    assert events[-1]["down"] == result["down"] and events[-1]["entity_id"] == "cover.bedroom_shutter"


async def test_model_is_direction_aware_after_calibration(hass, gateway, clock, fake_time, sleeps):
    cover = _make_cover(hass, gateway, calibration={"down": 10.0, "up": 20.0, "measured_at": "2026-09-13T12:00:00+00:00"})
    assert cover.extra_state_attributes["calibration_source"] == "measured"
    assert cover._travel_time == 10

    # closing from 100 to 50 uses the down time (5 s), opening 0 -> 50 the up time (10 s)
    cover._attr_current_cover_position = 100
    cover._start_position = 100
    await cover.async_set_cover_position(**{ATTR_POSITION: 50})
    _, written = gateway.deliveries[-1]
    written.set_result(clock.now)
    cover._motor_started.set()
    await _yield(6)
    assert pytest.approx(5.0) in [d for d in sleeps if d]

    cover._cancel_stop_task()
    cover._run_generation += 1
    cover._attr_current_cover_position = 0
    cover._start_position = 0
    cover._attr_is_opening = cover._attr_is_closing = False
    cover._move_start_time = None
    await cover.async_set_cover_position(**{ATTR_POSITION: 50})
    _, written = gateway.deliveries[-1]
    written.set_result(clock.now)
    cover._motor_started.set()
    await _yield(6)
    assert pytest.approx(10.0) in [d for d in sleeps if d]


def test_stored_calibration_lookup_and_defaults():
    entry = MagicMock()
    entry.options = {CONF_COVER_TRAVEL_TIMES: {"21": {"down": 18.4, "up": 20.1, "measured_at": "x"}, "22": "junk"}}
    assert _stored_calibration(entry, "21") == {"down": 18.4, "up": 20.1, "measured_at": "x"}
    assert _stored_calibration(entry, "22") is None
    assert _stored_calibration(entry, "99") is None
    entry.options = None
    assert _stored_calibration(entry, "21") is None


def test_yaml_travel_time_is_reported_as_yaml_source(hass, gateway):
    cover = _make_cover(hass, gateway, travel_time_source="yaml")
    attrs = cover.extra_state_attributes
    assert attrs["calibration_source"] == "yaml"
    assert attrs["travel_time_down"] == 25 and attrs["travel_time_up"] == 25 and attrs["calibrated_at"] is None


# ── refusals and failures ────────────────────────────────────────────────


async def test_advanced_cover_refuses_calibration(hass, gateway):
    cover = MyHOMECover(hass=hass, name="Pos", entity_name=None, device_id="31", who="2", where="31", interface=None,
                        advanced=True, manufacturer="BTicino", model="F401", gateway=gateway)
    with pytest.raises(HomeAssistantError, match="reports its position"):
        await cover.async_calibrate()


async def test_set_position_refused_while_calibrating(hass, gateway, clock, fake_time, sleeps):
    cover = _make_cover(hass, gateway)
    task = asyncio.create_task(cover.async_calibrate())
    await _yield()
    assert cover._calibrating is True
    with pytest.raises(HomeAssistantError, match="being calibrated"):
        await cover.async_set_cover_position(**{ATTR_POSITION: 20})
    with pytest.raises(HomeAssistantError, match="already being calibrated"):
        await cover.async_calibrate()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task


async def test_no_stop_status_times_out_with_a_clear_message(hass, gateway, clock, fake_time, sleeps):
    cover = _make_cover(hass, gateway)
    events = []
    hass.bus.async_listen(EVENT_COVER_CALIBRATION, lambda ev: events.append(ev.data))
    with patch("custom_components.myhome.cover.CALIBRATION_RUN_TIMEOUT", 0.05):
        task = asyncio.create_task(cover.async_calibrate())
        await _yield()
        _, written = gateway.deliveries[-1]
        written.set_result(clock.now)
        cover._motor_started.set()
        with pytest.raises(HomeAssistantError, match="no stop status"):
            await asyncio.wait_for(task, 5)
    assert cover._calibrating is False
    await hass.async_block_till_done()
    assert events[-1]["phase"] == "failed" and "no stop status" in events[-1]["error"]


async def test_external_command_interrupts_calibration(hass, gateway, clock, fake_time, sleeps):
    cover = _make_cover(hass, gateway)
    task = asyncio.create_task(cover.async_calibrate())
    await _yield()
    _, written = gateway.deliveries[-1]
    written.set_result(clock.now)
    await _yield()
    clock.now += 0.55
    cover.handle_event(OWNEvent.parse("*2*1*21##"))  # our motor start
    await _yield()
    clock.now += 3.0
    cover.handle_event(OWNEvent.parse("*2*2*21##"))  # somebody closes it from the wall
    with pytest.raises(CalibrationInterrupted, match="external close"):
        await asyncio.wait_for(task, 5)
    assert cover._calibrating is False


async def test_implausible_run_is_not_stored(hass, gateway, clock, fake_time, sleeps):
    cover = _make_cover(hass, gateway)
    task = asyncio.create_task(cover.async_calibrate())
    await _drive_run(cover, gateway, clock, direction_frame="1", write_delay=0.1, motor_delay=0.5, run=30.0)
    await _drive_run(cover, gateway, clock, direction_frame="2", write_delay=0.1, motor_delay=0.5, run=0.2)  # 0.2 s "travel"
    await _drive_run(cover, gateway, clock, direction_frame="1", write_delay=0.1, motor_delay=0.5, run=20.0)
    with pytest.raises(HomeAssistantError, match="implausible down run"):
        await asyncio.wait_for(task, 5)
    assert cover.extra_state_attributes["calibration_source"] == "default"
    assert cover._travel_time_down == 25.0


async def test_calibrations_on_one_gateway_run_sequentially(hass, gateway, clock, fake_time, sleeps):
    a = _make_cover(hass, gateway)
    b = _make_cover(hass, gateway)
    b.entity_id = "cover.other"
    b._device_id = "22"
    events = []
    hass.bus.async_listen(EVENT_COVER_CALIBRATION, lambda ev: events.append((ev.data["entity_id"], ev.data["phase"])))
    t_a = asyncio.create_task(a.async_calibrate())
    t_b = asyncio.create_task(b.async_calibrate())
    await _yield()
    assert a._calibrating is True and b._calibrating is False  # b waits for the gateway lock
    await hass.async_block_till_done()
    assert ("cover.bedroom_shutter", "start") in events
    assert ("cover.other", "queued") in events  # the UI shows "waiting", not "starting"
    t_a.cancel()
    t_b.cancel()
    for t in (t_a, t_b):
        with pytest.raises(asyncio.CancelledError):
            await t


def test_persist_is_a_noop_without_config_entry(hass, gateway):
    cover = _make_cover(hass, gateway)
    gateway.config_entry = None
    hass.config_entries.async_update_entry = MagicMock()
    cover._persist_calibration({"down": 1, "up": 1, "measured_at": "x"})
    hass.config_entries.async_update_entry.assert_not_called()


def test_calibration_event_is_skipped_without_hass(gateway):
    cover = _make_cover(None, gateway)
    cover.hass = None
    cover._fire_calibration_event("start")  # must not raise


# ── button entities ──────────────────────────────────────────────────────


async def test_calibrate_button_calls_the_service_for_its_cover(hass, gateway):
    from homeassistant.helpers import entity_registry as er

    from custom_components.myhome.button import CalibrateCoverButtonEntity
    from custom_components.myhome.const import DOMAIN

    registry = er.async_get(hass)
    registry.async_get_or_create("cover", DOMAIN, f"{gateway.mac}-2-21", suggested_object_id="bedroom_shutter")

    btn = CalibrateCoverButtonEntity(hass=hass, platform="button", device_id="21", where="21", interface=None, name="Bedroom shutter", gateway=gateway)
    btn.hass = hass
    assert btn.unique_id == f"{gateway.mac}-2-21-calibrate"
    assert btn.entity_id == "button.bedroom_shutter_calibrate_travel_time"
    await btn.async_update()  # no-op

    calls = []
    hass.services.async_register(DOMAIN, "calibrate_cover", lambda call: calls.append(dict(call.data)))
    await btn.async_press()
    await hass.async_block_till_done()
    assert calls == [{"entity_id": "cover.bedroom_shutter"}]

    # no cover entity registered for this device: nothing is called
    other = CalibrateCoverButtonEntity(hass=hass, platform="button", device_id="99", where="99", interface=None, name="Ghost", gateway=gateway)
    other.hass = hass
    await other.async_press()
    await hass.async_block_till_done()
    assert len(calls) == 1


async def test_calibrate_all_button_targets_every_enabled_cover_of_the_entry(hass, gateway):
    from homeassistant.helpers import entity_registry as er
    from pytest_homeassistant_custom_component.common import MockConfigEntry

    from custom_components.myhome.button import CalibrateAllCoversButtonEntity
    from custom_components.myhome.const import DOMAIN

    entry = MockConfigEntry(domain=DOMAIN, data={"mac": gateway.mac}, unique_id=gateway.mac)
    entry.add_to_hass(hass)
    registry = er.async_get(hass)
    registry.async_get_or_create("cover", DOMAIN, f"{gateway.mac}-2-21", suggested_object_id="a", config_entry=entry)
    registry.async_get_or_create("cover", DOMAIN, f"{gateway.mac}-2-22", suggested_object_id="b", config_entry=entry)
    registry.async_get_or_create("light", DOMAIN, f"{gateway.mac}-1-12", suggested_object_id="l", config_entry=entry)
    disabled = registry.async_get_or_create("cover", DOMAIN, f"{gateway.mac}-2-23", suggested_object_id="c", config_entry=entry)
    registry.async_update_entity(disabled.entity_id, disabled_by=er.RegistryEntryDisabler.USER)

    gateway.unique_id = gateway.mac
    btn = CalibrateAllCoversButtonEntity(hass=hass, config_entry=entry, gateway=gateway)
    assert btn.unique_id == f"{gateway.mac}-calibrate-all-covers"
    assert btn.available is True

    calls = []
    hass.services.async_register(DOMAIN, "calibrate_cover", lambda call: calls.append(dict(call.data)))
    await btn.async_press()
    await hass.async_block_till_done()
    assert calls == [{"entity_id": ["cover.a", "cover.b"]}]

    # nothing to calibrate -> no call
    for eid in ("cover.a", "cover.b", "cover.c"):
        registry.async_remove(eid)
    await btn.async_press()
    await hass.async_block_till_done()
    assert len(calls) == 1


async def test_button_platform_creates_calibration_buttons_for_registered_and_discovered_covers(hass, gateway):
    from homeassistant.helpers import entity_registry as er
    from homeassistant.helpers.dispatcher import async_dispatcher_send
    from pytest_homeassistant_custom_component.common import MockConfigEntry

    from custom_components.myhome.button import (
        CalibrateAllCoversButtonEntity,
        CalibrateCoverButtonEntity,
        async_setup_entry,
    )
    from custom_components.myhome.const import CONF_ENTITY, CONF_PLATFORMS, DOMAIN

    entry = MockConfigEntry(domain=DOMAIN, data={"mac": gateway.mac}, unique_id=gateway.mac)
    entry.add_to_hass(hass)
    registry = er.async_get(hass)
    registry.async_get_or_create("cover", DOMAIN, f"{gateway.mac}-2-21", suggested_object_id="bedroom", config_entry=entry, original_name="Bedroom")
    registry.async_get_or_create("cover", DOMAIN, f"{gateway.mac}-2-22#4#02", suggested_object_id="kitchen", config_entry=entry, original_name="Kitchen")
    hass.data[DOMAIN] = {gateway.mac: {CONF_PLATFORMS: {"button": {}}, CONF_ENTITY: gateway}}

    added = []
    attach_runtime(hass, entry)
    await async_setup_entry(hass, entry, lambda ents: added.extend(ents))
    calib = [e for e in added if isinstance(e, CalibrateCoverButtonEntity)]
    assert sorted(e.unique_id for e in calib) == sorted([f"{gateway.mac}-2-21-calibrate", f"{gateway.mac}-2-22#4#02-calibrate"])
    assert sum(isinstance(e, CalibrateAllCoversButtonEntity) for e in added) == 1
    assert calib[1]._interface == "02" or calib[0]._interface == "02"

    # a cover discovered later announces itself; a light does not get one; duplicates are ignored
    async_dispatcher_send(hass, f"myhome_new_device_{gateway.mac}", {"who": "2", "where": "23", "name": "Attic", "device_id": "23"})
    async_dispatcher_send(hass, f"myhome_new_device_{gateway.mac}", {"who": "2", "where": "23", "name": "Attic", "device_id": "23"})
    async_dispatcher_send(hass, f"myhome_new_device_{gateway.mac}", {"who": "1", "where": "12", "name": "Lamp", "device_id": "12"})
    calib = [e for e in added if isinstance(e, CalibrateCoverButtonEntity)]
    assert len(calib) == 3
    assert any(e.unique_id == f"{gateway.mac}-2-23-calibrate" for e in calib)


async def test_external_open_during_a_closing_run_interrupts_calibration(hass, gateway, clock, fake_time, sleeps):
    cover = _make_cover(hass, gateway)
    task = asyncio.create_task(cover.async_calibrate())
    await _drive_run(cover, gateway, clock, direction_frame="1", write_delay=0.1, motor_delay=0.5, run=25.0)
    # run 2 (close) starts; after the motor-start echo somebody presses "up" on the wall
    await _yield()
    _, written = gateway.deliveries[-1]
    written.set_result(clock.now)
    await _yield()
    clock.now += 0.5
    cover.handle_event(OWNEvent.parse("*2*2*21##"))
    await _yield()
    clock.now += 2.0
    cover.handle_event(OWNEvent.parse("*2*1*21##"))
    with pytest.raises(CalibrationInterrupted, match="external open"):
        await asyncio.wait_for(task, 5)


async def test_trailing_stop_echo_does_not_abort_calibration(hass, gateway, clock, fake_time, sleeps):
    """MH200 sends direction status then a stop echo ~0.1s later; calibration must ignore the echo."""
    cover = _make_cover(hass, gateway)
    task = asyncio.create_task(cover.async_calibrate())

    # Run 1: up
    await _drive_run(cover, gateway, clock, direction_frame="1", write_delay=0.1, motor_delay=0.5, run=25.0)

    # Run 2: down with trailing stop echo 0.1s after motor start
    await _yield()
    _, written = gateway.deliveries[-1]
    written.set_result(clock.now)
    await _yield()
    clock.now += 0.5
    cover.handle_event(OWNEvent.parse("*2*2*21##"))  # motor start echo
    await _yield()
    clock.now += 0.1
    cover.handle_event(OWNEvent.parse("*2*0*21##"))  # trailing stop echo (< 0.15s)
    await _yield()
    assert not task.done()  # must not have aborted or finished early!
    clock.now += 20.0
    cover.handle_event(OWNEvent.parse("*2*0*21##"))  # actual actuator stop
    await _yield()

    # Run 3: up
    await _drive_run(cover, gateway, clock, direction_frame="1", write_delay=0.1, motor_delay=0.5, run=22.0)
    result = await asyncio.wait_for(task, 5)
    assert result["down"] == pytest.approx(20.1, abs=0.01)
    assert result["up"] == pytest.approx(22.0, abs=0.01)


async def test_status_request_does_not_stop_calibration(hass, gateway, clock, fake_time, sleeps):
    """Status polls (*#2*21##, *#2*0##) on the bus must not abort ongoing calibration."""
    cover = _make_cover(hass, gateway)
    task = asyncio.create_task(cover.async_calibrate())

    # Run 1: up
    await _yield()
    _, written = gateway.deliveries[-1]
    written.set_result(clock.now)
    await _yield()
    clock.now += 0.5
    cover.handle_event(OWNEvent.parse("*2*1*21##"))
    await _yield()

    # Periodic poll arrives on bus
    clock.now += 5.0
    cover.handle_event(OWNEvent.parse("*#2*21##"))
    await _yield()
    assert not task.done()

    # General poll arrives on bus
    clock.now += 5.0
    cover.handle_event(OWNEvent.parse("*#2*0##"))
    await _yield()
    assert not task.done()

    clock.now += 15.0
    cover.handle_event(OWNEvent.parse("*2*0*21##"))  # actual stop
    await _yield()

    # Run 2 & 3
    await _drive_run(cover, gateway, clock, direction_frame="2", write_delay=0.1, motor_delay=0.5, run=20.0)
    await _drive_run(cover, gateway, clock, direction_frame="1", write_delay=0.1, motor_delay=0.5, run=22.0)
    result = await asyncio.wait_for(task, 5)
    assert result["down"] == pytest.approx(20.0, abs=0.01)


async def test_stop_cover_calibration_service(hass, gateway, clock, fake_time, sleeps):
    """Calling stop_cover_calibration halts the motor and interrupts queued runs."""
    a = _make_cover(hass, gateway)
    b = _make_cover(hass, gateway)
    b.entity_id = "cover.kitchen"
    b._device_id = "22"
    b._full_where = "22"

    task_a = asyncio.create_task(a.async_calibrate())
    task_b = asyncio.create_task(b.async_calibrate())
    await _yield()
    assert a._calibrating is True
    assert not task_a.done()
    _, written = gateway.deliveries[-1]
    written.set_result(clock.now)
    await _yield()

    stopped = await async_stop_cover_calibration(hass, gateway.mac)
    assert stopped is True
    with pytest.raises(CalibrationInterrupted):
        await asyncio.wait_for(task_a, 2)
    with pytest.raises(CalibrationInterrupted):
        await asyncio.wait_for(task_b, 2)
    assert a._calibrating is False
    assert b._calibrating is False


async def test_backend_calibration_trace(hass, gateway, clock, fake_time, sleeps):
    """Recent calibration trace captures TX, RX, and lifecycle events."""
    cover = _make_cover(hass, gateway)
    task = asyncio.create_task(cover.async_calibrate())
    await _drive_run(cover, gateway, clock, direction_frame="1", write_delay=0.1, motor_delay=0.5, run=25.0)
    await _drive_run(cover, gateway, clock, direction_frame="2", write_delay=0.1, motor_delay=0.5, run=20.0)
    await _drive_run(cover, gateway, clock, direction_frame="1", write_delay=0.1, motor_delay=0.5, run=22.0)
    await asyncio.wait_for(task, 5)

    trace = get_last_calibration_trace()
    assert len(trace) > 0
    raws = [f["raw"] for f in trace]
    assert "*2*1*21##" in raws
    assert "*2*2*21##" in raws
    assert any("phase:start" in r or "phase:run" in r for r in raws)


async def test_set_cover_travel_time_manual(hass, gateway):
    """Setting manual travel time persists and updates attributes."""
    from pytest_homeassistant_custom_component.common import MockConfigEntry

    from custom_components.myhome.const import DOMAIN

    entry = MockConfigEntry(domain=DOMAIN, data={"mac": gateway.mac}, unique_id="entry_manual_test", options={})
    entry.add_to_hass(hass)
    gateway.config_entry = entry

    cover = _make_cover(hass, gateway)
    events = []
    hass.bus.async_listen(EVENT_COVER_CALIBRATION, lambda ev: events.append(ev.data))

    res = await cover.async_set_travel_time(travel_time=18.5, travel_time_up=19.2)
    assert res["down"] == 18.5
    assert res["up"] == 19.2
    assert res["source"] == "manual"
    assert cover.extra_state_attributes["travel_time_down"] == 18.5
    assert cover.extra_state_attributes["travel_time_up"] == 19.2
    assert cover.extra_state_attributes["calibration_source"] == "manual"

    stored = _stored_calibration(gateway.config_entry, cover._device_id)
    assert stored["down"] == 18.5
    assert stored["up"] == 19.2
    assert stored["source"] == "manual"
    assert any(ev.get("phase") == "done" and ev.get("source") == "manual" for ev in events)


async def test_reset_cover_travel_time(hass, gateway):
    """Resetting travel time clears stored calibration and restores default/YAML."""
    from pytest_homeassistant_custom_component.common import MockConfigEntry

    from custom_components.myhome.const import DOMAIN

    entry = MockConfigEntry(domain=DOMAIN, data={"mac": gateway.mac}, unique_id="entry_reset_test", options={})
    entry.add_to_hass(hass)
    gateway.config_entry = entry

    cover = _make_cover(hass, gateway)
    await cover.async_set_travel_time(travel_time=18.5)
    assert cover.extra_state_attributes["calibration_source"] == "manual"

    await cover.async_reset_travel_time()
    assert cover._travel_time_down == 25.0
    assert cover._travel_time_up == 25.0
    assert cover.extra_state_attributes["calibration_source"] == "default"
    assert cover.extra_state_attributes["calibrated_at"] is None

    stored = _stored_calibration(gateway.config_entry, cover._device_id)
    assert stored is None


def test_calibration_lock_outside_running_loop(gateway):
    """Acquiring calibration lock when no loop is running gracefully handles RuntimeError."""
    from custom_components.myhome.cover import _CALIBRATION_LOCKS, _calibration_lock

    key = gateway.mac
    _CALIBRATION_LOCKS.pop(key, None)
    with patch("asyncio.get_running_loop", side_effect=RuntimeError("no running loop")):
        lock = _calibration_lock(gateway)
        assert lock is not None


async def test_stop_cover_calibration_filtering_and_error(hass, gateway):
    """Stopping calibration ignores mismatched gateways and catches errors during stop."""
    from custom_components.myhome.cover import _CALIBRATION_ACTIVE, async_stop_cover_calibration

    cover = _make_cover(hass, gateway)
    cover._calibrating = True
    cover.async_stop_cover = AsyncMock(side_effect=RuntimeError("stop boom"))
    _CALIBRATION_ACTIVE[gateway.mac] = cover

    # 1. Stop for a different gateway should continue past this cover
    assert await async_stop_cover_calibration(hass, gateway_mac="other_gw_mac") is False

    # 2. Stop for this gateway catches the stop exception and logs a warning
    assert await async_stop_cover_calibration(hass, gateway_mac=gateway.mac) is True
    _CALIBRATION_ACTIVE.pop(gateway.mac, None)


async def test_cover_async_stop_calibration_method(hass, gateway):
    """Cover entity method async_stop_calibration delegates to gateway."""
    cover = _make_cover(hass, gateway)
    with patch("custom_components.myhome.cover.async_stop_cover_calibration", new_callable=AsyncMock) as mock_stop:
        await cover.async_stop_calibration()
        mock_stop.assert_awaited_once_with(hass, gateway_mac=gateway.mac)


async def test_reset_travel_time_advanced_and_yaml(hass, gateway):
    """Reset travel time refuses advanced covers and honors YAML config."""
    from custom_components.myhome.const import CONF_PLATFORMS, CONF_TRAVEL_TIME, DOMAIN

    adv_cover = _make_cover(hass, gateway, advanced=True)
    with pytest.raises(HomeAssistantError, match="reports its position"):
        await adv_cover.async_reset_travel_time()

    # With YAML configuration on the entry's runtime data (seeded through the legacy mapping)
    cover = _make_cover(hass, gateway)
    hass.data[DOMAIN] = {
        gateway.mac: {
            CONF_PLATFORMS: {
                "cover": {
                    cover._device_id: {CONF_TRAVEL_TIME: 32.0}
                }
            }
        }
    }
    bind_entity(hass, cover, gateway.mac, gateway)
    await cover.async_reset_travel_time()
    assert cover._travel_time_down == 32.0
    assert cover._travel_time_up == 32.0
    assert cover.extra_state_attributes["calibration_source"] == "yaml"


async def test_calibration_ignores_general_frame_and_stop_closed_attr(hass, gateway):
    """Calibration ignores general frames and stop events respect message.is_closed."""
    cover = _make_cover(hass, gateway)
    cover._calibrating = True

    # General command is ignored during calibration
    cover.handle_event(OWNEvent.parse("*2*1*0##"))
    assert cover._calibrating is True

    # Stop frame with is_closed attribute
    cover._calibrating = False
    stop_event = MagicMock(
        spec=OWNEvent,
        is_closed=True,
        current_position=None,
        is_opening=False,
        is_closing=False,
        where="21",
        who=2,
        what=0,
        _what=0,
        human_readable_log="Stop",
        raw="*2*0*21##",
    )
    cover.handle_event(stop_event)
    assert cover._attr_is_closed is True


async def test_stop_cover_calibration_service_handler(hass, gateway):
    """Domain service myhome.stop_cover_calibration delegates to handler."""
    from custom_components.myhome.const import ATTR_GATEWAY, DOMAIN, SERVICE_STOP_COVER_CALIBRATION
    from custom_components.myhome.services import async_setup_services

    await async_setup_services(hass)
    with patch("custom_components.myhome.cover.async_stop_cover_calibration", new_callable=AsyncMock, return_value=True) as mock_stop:
        await hass.services.async_call(
            DOMAIN,
            SERVICE_STOP_COVER_CALIBRATION,
            {ATTR_GATEWAY: gateway.mac},
            blocking=True,
        )
        mock_stop.assert_awaited_once_with(hass, gateway_mac=gateway.mac)


# ── calibration trace is scoped to one gateway (#319 review) ──────────────


def _second_gateway(gateway):
    other = MagicMock()
    other.mac = "00:03:50:00:00:02"
    other.log_id = "[MH200 gateway - other]"
    other.availability_signal = gateway.availability_signal
    other.available = True
    other.device_registry_id = None
    other.config_entry = MagicMock()
    other.config_entry.options = {}
    other.deliveries = []

    async def _send(message):
        fut = asyncio.get_running_loop().create_future()
        other.deliveries.append((str(message), fut))
        return fut

    other.send = AsyncMock(side_effect=_send)
    return other


async def _calibrate_on(hass, gateway, clock, *, entity_id, run):
    cover = _make_cover(hass, gateway)
    cover.entity_id = entity_id
    task = asyncio.create_task(cover.async_calibrate())
    for direction_frame in ("1", "2", "1"):
        await _drive_run(cover, gateway, clock, direction_frame=direction_frame, write_delay=0.1, motor_delay=0.5, run=run)
    await asyncio.wait_for(task, 5)
    return cover


async def test_calibration_trace_frames_carry_their_gateway(hass, gateway, clock, fake_time, sleeps):
    """Two gateways calibrate; each frame names the gateway that recorded it and reads filter on it."""
    other = _second_gateway(gateway)
    await _calibrate_on(hass, gateway, clock, entity_id="cover.a_shutter", run=20.0)
    await _calibrate_on(hass, other, clock, entity_id="cover.b_shutter", run=30.0)

    everything = get_last_calibration_trace()
    macs = {f["gateway_mac"] for f in everything}
    assert macs == {"00:03:50:00:00:01", "00:03:50:00:00:02"}

    only_a = get_last_calibration_trace(gateway_mac="00:03:50:00:00:01")
    assert only_a and all(f["gateway_mac"] == "00:03:50:00:00:01" for f in only_a)
    assert {f["entity_id"] for f in only_a} == {"cover.a_shutter"}
    # any spelling of the MAC selects the same frames
    assert get_last_calibration_trace(gateway_mac="000350000001") == only_a
    only_b = get_last_calibration_trace(gateway_mac="00:03:50:00:00:02")
    assert {f["entity_id"] for f in only_b} == {"cover.b_shutter"}
    assert len(only_a) + len(only_b) == len(everything)
    # a gateway without a MAC is not "all gateways"
    assert get_last_calibration_trace(gateway_mac="") == []


async def _ws_trace(hass, msg):
    from custom_components.myhome.websocket import ws_cover_calibration_trace

    conn = MagicMock()
    ws_cover_calibration_trace(hass, conn, {"type": "myhome/cover/calibration_trace", **msg})
    await hass.async_block_till_done()
    return conn


async def test_websocket_calibration_trace_is_scoped_to_the_requested_gateway(
    hass, gateway, clock, fake_time, sleeps, attach_gateway
):
    """Exporting for gateway A never carries gateway B's frames; omitted mac is the primary gateway; unknown is not_found."""
    other = _second_gateway(gateway)
    attach_gateway(gateway.mac, gateway)
    attach_gateway(other.mac, other)
    await _calibrate_on(hass, gateway, clock, entity_id="cover.a_shutter", run=20.0)
    await _calibrate_on(hass, other, clock, entity_id="cover.b_shutter", run=30.0)

    conn = await _ws_trace(hass, {"id": 1, "mac": other.mac})
    msg_id, result = conn.send_result.call_args[0]
    assert msg_id == 1 and result["mac"] == "00:03:50:00:00:02"
    assert result["frames"] and {f["entity_id"] for f in result["frames"]} == {"cover.b_shutter"}
    assert all(f["gateway_mac"] == "00:03:50:00:00:02" for f in result["frames"])

    conn = await _ws_trace(hass, {"id": 2, "mac": "000350000001"})
    _, result = conn.send_result.call_args[0]
    assert result["mac"] == "00:03:50:00:00:01"
    assert {f["entity_id"] for f in result["frames"]} == {"cover.a_shutter"}

    # mac omitted: the primary (first configured) gateway, as for every other command
    conn = await _ws_trace(hass, {"id": 3})
    _, result = conn.send_result.call_args[0]
    assert result["mac"] == "00:03:50:00:00:01"
    assert {f["entity_id"] for f in result["frames"]} == {"cover.a_shutter"}

    # unknown mac: an error, never a substitute gateway's frames
    conn = await _ws_trace(hass, {"id": 4, "mac": "00:03:50:ff:ff:ff"})
    conn.send_result.assert_not_called()
    assert conn.send_error.call_args[0][:2] == (4, "not_found")


async def test_websocket_calibration_trace_without_a_gateway_is_not_found(hass):
    conn = await _ws_trace(hass, {"id": 42})
    conn.send_result.assert_not_called()
    assert conn.send_error.call_args[0][0] == 42


# ── the actuator's 60 s run-time limit is refused, not stored (#319 review) ─


async def test_run_ending_at_the_actuator_cutoff_fails_at_once(hass, gateway, clock, fake_time, sleeps):
    """A 14 s shutter on an actuator with the 60 s limit: the first 61.5 s run fails, nothing is stored, no more runs."""
    cover = _make_cover(hass, gateway)
    events = []
    hass.bus.async_listen(EVENT_COVER_CALIBRATION, lambda ev: events.append(ev.data))
    task = asyncio.create_task(cover.async_calibrate())
    await _drive_run(cover, gateway, clock, direction_frame="1", write_delay=0.3, motor_delay=0.55, run=61.5)
    with pytest.raises(HomeAssistantError, match="60 s run-time limit") as err:
        await asyncio.wait_for(task, 5)
    assert "61.5 s" in str(err.value) and "stopwatch" in str(err.value)

    assert [f for f, _ in gateway.deliveries] == ["*2*1*21##"]  # no second and third run
    assert cover.extra_state_attributes["calibration_source"] == "default"
    assert cover._travel_time_down == 25.0 and cover._travel_time_up == 25.0
    assert cover._calibrating is False
    await hass.async_block_till_done()
    assert events[-1]["phase"] == "failed" and "60 s run-time limit" in events[-1]["error"]


async def test_run_just_outside_the_cutoff_window_is_stored(hass, gateway, clock, fake_time, sleeps):
    """A genuine 58 s or 66 s run is a long shutter, not the limit."""
    cover = _make_cover(hass, gateway)
    task = asyncio.create_task(cover.async_calibrate())
    await _drive_run(cover, gateway, clock, direction_frame="1", write_delay=0.1, motor_delay=0.5, run=30.0)
    await _drive_run(cover, gateway, clock, direction_frame="2", write_delay=0.1, motor_delay=0.5, run=58.0)
    await _drive_run(cover, gateway, clock, direction_frame="1", write_delay=0.1, motor_delay=0.5, run=66.0)
    result = await asyncio.wait_for(task, 5)
    assert result["down"] == pytest.approx(58.0) and result["up"] == pytest.approx(66.0)


# ── the backend measures every run for the stopwatch (#319 review) ─────────


async def test_plain_run_is_measured_from_motor_start_to_stop_write(hass, gateway, clock, fake_time):
    """open_cover then stop_cover: motion_started_at is the anchor, last_run_seconds the run to the stop's write."""
    cover = _make_cover(hass, gateway)
    attrs = cover.extra_state_attributes
    assert attrs["motion_started_at"] is None and attrs["last_run_seconds"] is None

    await cover.async_open_cover()
    assert cover.extra_state_attributes["motion_started_at"] is None  # not this run's anchor yet
    _, written = gateway.deliveries[-1]
    clock.now += 2.0  # queue wait: the click is 2 s before the write
    written.set_result(clock.now)
    await _yield()
    clock.now += 0.55
    cover.handle_event(OWNEvent.parse("*2*1*21##"))  # motor-start echo re-anchors
    await _yield()
    anchored = cover.extra_state_attributes["motion_started_at"]
    assert anchored and anchored.endswith("+00:00")

    clock.now += 14.0
    await cover.async_stop_cover()
    _, stop_written = gateway.deliveries[-1]
    clock.now += 1.0  # the stop frame leaves the queue a second after the click
    stop_written.set_result(clock.now)
    await _yield()

    attrs = cover.extra_state_attributes
    assert attrs["last_run_seconds"] == pytest.approx(15.0)  # motor start -> stop write, not click -> click
    assert attrs["last_run_direction"] == "open"
    assert attrs["last_run_ended_at"] and attrs["motion_started_at"] is None


async def test_actuator_stop_status_ends_the_measured_run(hass, gateway, clock, fake_time):
    """Without a stop command the actuator's own stop status ends the run."""
    cover = _make_cover(hass, gateway)
    await cover.async_close_cover()
    _, written = gateway.deliveries[-1]
    written.set_result(clock.now)
    await _yield()
    clock.now += 0.55
    cover.handle_event(OWNEvent.parse("*2*2*21##"))
    clock.now += 18.4
    cover.handle_event(OWNEvent.parse("*2*0*21##"))
    attrs = cover.extra_state_attributes
    assert attrs["last_run_seconds"] == pytest.approx(18.4) and attrs["last_run_direction"] == "close"


async def test_set_position_re_anchor_does_not_shorten_the_measured_run(hass, gateway, clock, fake_time, sleeps):
    """set_position re-anchors the estimate at the target; the measured run still starts at the motor start."""
    cover = _make_cover(hass, gateway)
    cover._attr_current_cover_position = 100
    cover._start_position = 100
    await cover.async_set_cover_position(**{ATTR_POSITION: 50})
    _, written = gateway.deliveries[-1]
    written.set_result(clock.now)
    cover._motor_started.set()
    await _yield(6)
    # the auto-stop slept the half travel (12.5 s) and wrote the stop
    _, stop_written = gateway.deliveries[-1]
    assert stop_written is not written
    stop_written.set_result(clock.now)
    await _yield()
    assert cover.extra_state_attributes["last_run_seconds"] == pytest.approx(12.5, abs=0.6)


# ── exception translations (quality-scale exception-translations) ────────


async def test_set_travel_time_validation_raises_translated_service_errors(hass, gateway):
    """Bad service input is a ServiceValidationError carrying a translation key."""
    from homeassistant.exceptions import ServiceValidationError

    cover = _make_cover(hass, gateway)

    with pytest.raises(ServiceValidationError, match="must be specified") as err:
        await cover.async_set_travel_time()
    assert err.value.translation_key == "travel_time_missing"

    with pytest.raises(ServiceValidationError, match="travel_time_down must be between") as err:
        await cover.async_set_travel_time(travel_time_down=0.2, travel_time_up=20)
    assert err.value.translation_placeholders["field"] == "travel_time_down"

    with pytest.raises(ServiceValidationError, match="travel_time_up must be between") as err:
        await cover.async_set_travel_time(travel_time_down=20, travel_time_up=999)
    assert err.value.translation_placeholders["field"] == "travel_time_up"

    advanced = MyHOMECover(hass=hass, name="Pos", entity_name=None, device_id="31", who="2", where="31",
                           interface=None, advanced=True, manufacturer="BTicino", model="F401", gateway=gateway)
    for coro in (advanced.async_set_travel_time(travel_time=10), advanced.async_reset_travel_time()):
        with pytest.raises(HomeAssistantError) as err:
            await coro
        assert err.value.translation_key == "cover_reports_position"


def test_every_raised_translation_key_is_defined():
    """Every literal translation_key used by a raised exception exists in strings.json and en.json."""
    import json
    import re
    from pathlib import Path

    root = Path("custom_components/myhome")
    # Repair issues pass their keys as constants; literal keys are only used by exceptions.
    raised = {
        key
        for source in root.glob("*.py")
        for key in re.findall(r'translation_key="([a-z_]+)"', source.read_text(encoding="utf-8"))
    }
    assert raised, "no translated exceptions found"
    for name in ("strings.json", "translations/en.json"):
        defined = set(json.loads((root / name).read_text(encoding="utf-8"))["exceptions"])
        missing = raised - defined
        assert not missing, f"{name} lacks exception translations for {sorted(missing)}"
