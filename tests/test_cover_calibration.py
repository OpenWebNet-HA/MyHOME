"""Cover calibration: measure up/down travel on the bus, store it, apply it (myhome.calibrate_cover)."""
import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from homeassistant.components.cover import ATTR_POSITION
from homeassistant.exceptions import HomeAssistantError
from OWNd.message import OWNEvent

from custom_components.myhome.const import CONF_COVER_TRAVEL_TIMES, EVENT_COVER_CALIBRATION
from custom_components.myhome.cover import CalibrationInterrupted, MyHOMECover, _stored_calibration


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
    c = MyHOMECover(
        hass=hass,
        name="Bedroom shutter",
        entity_name=None,
        device_id="21",
        who="2",
        where="21",
        interface=None,
        advanced=False,
        manufacturer="BTicino",
        model="Shutter",
        gateway=gateway,
        travel_time=25,
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
    t_a = asyncio.create_task(a.async_calibrate())
    t_b = asyncio.create_task(b.async_calibrate())
    await _yield()
    assert a._calibrating is True and b._calibrating is False  # b waits for the gateway lock
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
