"""A shutter's direction translation stands in for the movement status the gateway did not relay.

Live case (cover 81 behind a MyHOMEServer1): after the panel's `*2*1*81##` the monitor carried
only `*2*1000#0*81##`, `*2*0*81##`, `*2*1000#1*81##`, never `*2*1*81##`, while the motor ran.
The guided step waited 10 s and discarded the run (`start_timeout`) and the cover stayed
"stopped". The translation is now held for TRANSLATION_GRACE seconds and, when no status of
that shutter follows, delivered as its movement, dated when the translation arrived.
"""
import asyncio
import logging
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
    EVENT_STATE_CHANGED,
)
from homeassistant.core import Event, callback
from homeassistant.helpers import entity_platform
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.dispatcher import async_dispatcher_connect, async_dispatcher_send
from homeassistant.util import dt as dt_util
from OWNd.message import OWNMessage
from pytest_homeassistant_custom_component.common import MockConfigEntry, async_fire_time_changed

from custom_components.myhome.const import (
    CONF_DEVICE_TYPE,
    CONF_ENTITY,
    CONF_FILE_PATH,
    CONF_FIRMWARE,
    CONF_MANUFACTURER,
    CONF_PLATFORMS,
    DOMAIN,
)
from custom_components.myhome.cover import MyHOMECover
from custom_components.myhome.cover_translation import (
    TRANSLATION_GRACE,
    CoverTranslationRelay,
    TranslatedMovement,
    event_age,
    movement_translation,
)
from custom_components.myhome.gateway_events import GatewayEventDispatcher
from tests.conftest import attach_runtime
from tests.test_cover_calibration_geometry import endpoint, lift
from tests.test_cover_calibration_geometry import geometry as geometry_fixture
from tests.test_cover_profiles import plant as plant_fixture
from tests.test_panel_cover_calibration import act
from tests.test_panel_cover_calibration import calibration as calibration_fixture

plant = plant_fixture
calibration = calibration_fixture
geometry = geometry_fixture

CLOCK = "custom_components.myhome.cover_translation.monotonic"
# The live sequence of a direction command whose status never came.
LIVE_UP = ("*2*1000#0*81##", "*2*0*81##", "*2*1000#1*81##")


def frame(raw):
    return OWNMessage.parse(raw)


class Relay:
    """A relay on a hand-driven clock, recording what it delivers."""

    def __init__(self, hass, clock):
        self.clock = clock
        self.delivered = []
        self.relay = CoverTranslationRelay(hass, self.delivered.append)

    def send(self, raw):
        with patch(CLOCK, side_effect=lambda: self.clock[0]):
            self.relay.handle_message(frame(raw) if isinstance(raw, str) else raw)

    def held(self):
        return sorted(self.relay._held)

    def release(self, key="81"):
        """Run the grace timer of `key` at its due time."""
        held = self.relay._held[key]
        self.clock[0] = held.arrived + TRANSLATION_GRACE
        with patch(CLOCK, side_effect=lambda: self.clock[0]):
            held.timer._run()
        held.timer.cancel()  # Run by hand: the loop must not run it again.


@pytest.fixture
async def relay(hass):
    relay = Relay(hass, [1000.0])
    yield relay
    relay.relay.close()


# ── what a translation stands for ───────────────────────────────────


@pytest.mark.parametrize(("raw", "opening", "where", "interface"), [
    ("*2*1000#1*81##", True, "81", None),
    ("*2*1000#2*81##", False, "81", None),
    ("*2*1000#1*10##", True, "10", None),  # Area 1, point 0: one actuator.
    ("*2*1000#2*81#4#01##", False, "81", "01"),
])
def test_a_point_to_point_direction_stands_for_its_movement(raw, opening, where, interface):
    movement = movement_translation(frame(raw))
    assert isinstance(movement, TranslatedMovement)
    assert (movement.is_opening, movement.is_closing) == (opening, not opening)
    assert movement.state == (1 if opening else 2)
    assert (movement.where, movement.interface) == (where, interface)
    assert movement.translated is True and movement.is_translation is False
    assert str(movement) == raw  # Traces and logs show the frame that was received.
    assert movement.human_readable_log.endswith("(command translation).")
    assert movement.age == 0.0


def test_the_log_line_names_the_cover_and_the_translation():
    assert movement_translation(frame("*2*1000#1*81##")).human_readable_log == (
        "Cover 81 is opening (command translation).")
    assert movement_translation(frame("*2*1000#2*81#4#01##")).human_readable_log == (
        "Cover 81 on interface 01 is closing (command translation).")


@pytest.mark.parametrize("raw", [
    "*2*1000#0*81##",  # A Stop: sent around every direction command, before the motor starts.
    "*2*1000#1*0##",  # General.
    "*2*1000#1*0#4#01##",  # General on one interface.
    "*2*1000#1*5##",  # Area.
    "*2*1000#2*00##",  # Area 00.
    "*2*1000#1*100##",  # Area 10.
    "*2*1000#1*#5##",  # Group.
    "*2*1000#1#3*81##",  # A translation with more than a direction.
    "*2*1*81##",  # The status itself.
    "*1*1000#1*53##",  # WHO 1 (lighting) translations stay as they were.
])
def test_nothing_else_stands_for_a_movement(raw):
    assert movement_translation(frame(raw)) is None


def test_only_a_translated_movement_has_an_age():
    movement = movement_translation(frame("*2*1000#1*81##"))
    movement.age = 0.7
    assert event_age(movement) == 0.7
    assert event_age(frame("*2*1*81##")) == 0.0
    assert event_age(MagicMock()) == 0.0
    assert movement_translation(MagicMock()) is None


# ── the relay ───────────────────────────────────────────────────────


async def test_the_live_sequence_delivers_the_movement_dated_at_the_translation(relay):
    relay.send(LIVE_UP[0])
    relay.send(LIVE_UP[1])
    relay.clock[0] += .02
    relay.send(LIVE_UP[2])
    assert relay.held() == ["81"] and relay.delivered == []
    relay.release()
    (movement,) = relay.delivered
    assert movement.is_opening and str(movement) == "*2*1000#1*81##"
    assert movement.age == pytest.approx(TRANSLATION_GRACE)
    assert relay.held() == []


@pytest.mark.parametrize("status", ["*2*1*81##", "*2*2*81##", "*2*0*81##", "*#2*81*10*10*40*001*0##"])
async def test_any_status_of_the_shutter_supersedes_the_translation(relay, status):
    """The usual sequence: the actuator's status follows ~0.4 s later and is handled as before."""
    for raw in LIVE_UP:
        relay.send(raw)
    timer = relay.relay._held["81"].timer
    relay.clock[0] += .4
    relay.send(status)
    assert relay.held() == [] and timer.cancelled()
    assert relay.delivered == []


async def test_a_stop_translated_after_the_direction_ends_the_run(relay):
    relay.send("*2*1000#2*81##")
    relay.send("*2*1000#0*81##")
    assert relay.held() == [] and relay.delivered == []


async def test_the_first_translation_of_a_direction_dates_it(relay):
    relay.send("*2*1000#1*81##")
    arrived = relay.relay._held["81"].arrived
    relay.clock[0] += .5
    relay.send("*2*1000#1*81##")
    assert relay.relay._held["81"].arrived == arrived
    relay.release()
    assert len(relay.delivered) == 1


async def test_the_opposite_direction_replaces_the_held_one(relay):
    relay.send("*2*1000#1*81##")
    first = relay.relay._held["81"].timer
    relay.clock[0] += .5
    relay.send("*2*1000#2*81##")
    assert first.cancelled()
    relay.release()
    (movement,) = relay.delivered
    assert movement.is_closing


@pytest.mark.parametrize("raw", ["*2*1000#1*0##", "*2*1000#1*5##", "*2*1000#1*#5##", "*2*1000#12#10#001#1*21##",
                                 "*1*1000#1*81##", "*1*1*81##"])
async def test_scope_translations_and_other_frames_are_never_held(relay, raw):
    relay.send(raw)
    assert relay.held() == []
    relay.send(MagicMock())
    assert relay.held() == []


async def test_shutters_are_held_apart(relay):
    relay.send("*2*1000#1*81##")
    relay.send("*2*1000#2*81#4#01##")
    relay.send("*2*1000#2*91##")
    relay.send("*2*1*81##")  # Supersedes only 81 on the main bus.
    relay.send("*2*7*91##")  # A WHAT that states no movement supersedes nothing.
    assert relay.held() == ["81#4#01", "91"]
    relay.release("81#4#01")
    assert [str(m) for m in relay.delivered] == ["*2*1000#2*81#4#01##"]
    relay.relay.close()
    assert relay.held() == []
    relay.relay.discard("91")  # Nothing held: nothing to do.


# ── the gateway handler ─────────────────────────────────────────────


def _dispatcher(hass):
    handler = MagicMock()
    handler.hass = hass
    handler.mac = "00:03:50:00:00:81"
    handler.log_id = "[gw]"
    handler.generate_events = False
    handler.is_standby = False
    handler.is_secondary = False
    handler.delegated_away_whos = set()
    handler.config_entry = SimpleNamespace(entry_id="entry")
    handler.send_status_request = AsyncMock()
    return GatewayEventDispatcher(handler)


async def test_the_gateway_hands_every_translation_on_and_names_the_held_ones(hass, caplog):
    dispatcher = _dispatcher(hass)
    seen, events = [], []
    relay = Relay(hass, [1000.0])
    async_dispatcher_connect(hass, "myhome_message_00:03:50:00:00:81", callback(lambda message: seen.append(message)))
    async_dispatcher_connect(hass, "myhome_message_00:03:50:00:00:81", callback(lambda message: relay.send(message)))
    for event_type in ("myhome_general_automation_event", "myhome_area_automation_event",
                       "myhome_group_automation_event", "myhome_general_light_event"):
        hass.bus.async_listen(event_type, events.append)
    caplog.set_level(logging.DEBUG, logger="custom_components.myhome")
    for raw in (*LIVE_UP, "*2*1000#1*0##", "*2*1000#1*5##", "*2*1000#2*#5##", "*1*1000#1*53##"):
        await dispatcher.process_message(frame(raw))
    await hass.async_block_till_done()
    assert [str(m) for m in seen] == [*LIVE_UP, "*2*1000#1*0##", "*2*1000#1*5##", "*2*1000#2*#5##", "*1*1000#1*53##"]
    assert events == []  # No translation fires a bus event, as before.
    assert relay.held() == ["81"]  # Only the point-to-point direction is held.
    held = [r.getMessage() for r in caplog.records if "Holding command translation" in r.getMessage()]
    assert held == ["[gw] Holding command translation `*2*1000#1*81##` in case no status follows: "
                    "Cover 81 is opening (command translation)."]
    ignored = [r.getMessage() for r in caplog.records if "Ignoring translation message" in r.getMessage()]
    assert ignored == [f"[gw] Ignoring translation message `{raw}`" for raw in
                       ("*2*1000#0*81##", "*2*1000#1*0##", "*2*1000#1*5##", "*2*1000#2*#5##", "*1*1000#1*53##")]
    relay.release()
    assert [str(m) for m in relay.delivered] == ["*2*1000#1*81##"]


# ── the cover platform ──────────────────────────────────────────────


async def test_the_platform_delivers_a_lone_translation_to_its_cover_only(hass):
    gateway = MagicMock()
    gateway.mac = "mac_translation"
    hass.data = {DOMAIN: {"mac_translation": {CONF_ENTITY: gateway, CONF_PLATFORMS: {}}}}
    entry = MagicMock()
    entry.data = {CONF_MAC: "mac_translation"}
    entry.entry_id = "entry_translation"
    unload = []
    entry.async_on_unload = unload.append
    attach_runtime(hass, entry)
    added = []
    from custom_components.myhome.cover import async_setup_entry

    with patch("custom_components.myhome.discovery.er.async_entries_for_config_entry", return_value=[]), \
         patch("custom_components.myhome.discovery.er.async_get"):
        await async_setup_entry(hass, entry, added.extend)
    signal = "myhome_message_mac_translation"
    async_dispatcher_send(hass, signal, frame("*2*0*81##"))
    (cover,) = added
    assert not cover.is_closing
    async_dispatcher_send(hass, signal, frame("*2*1000#2*81##"))
    async_dispatcher_send(hass, signal, frame("*2*1000#1*82##"))  # No cover there: none is discovered.
    async_dispatcher_send(hass, signal, frame("*2*1000#1*0##"))
    assert not cover.is_closing  # Held for the grace.
    async_fire_time_changed(hass, dt_util.utcnow() + timedelta(seconds=TRANSLATION_GRACE + .5))
    await hass.async_block_till_done()
    assert cover.is_closing and not cover.is_opening
    assert added == [cover]
    # Unloading forgets what is still held.
    async_dispatcher_send(hass, signal, frame("*2*1000#1*81##"))
    for undo in unload:
        undo()
    async_fire_time_changed(hass, dt_util.utcnow() + timedelta(seconds=2 * TRANSLATION_GRACE + 1))
    await hass.async_block_till_done()
    assert cover.is_closing


async def test_an_ignored_address_stays_silent(hass):
    from custom_components.myhome.discovery import Address, PlatformDiscovery

    routed = []
    discovery = SimpleNamespace(address_of=Address.from_message, known={"81"},
                                _is_ignored=lambda address: True, route=lambda *a: routed.append(a))
    PlatformDiscovery.relay(discovery, movement_translation(frame("*2*1000#1*81##")))
    assert routed == []
    discovery._is_ignored = lambda address: False
    PlatformDiscovery.relay(discovery, movement_translation(frame("*2*1000#1*81##")))
    assert len(routed) == 1


# ── the cover entity ────────────────────────────────────────────────


def _cover(hass, address="81"):
    gateway = SimpleNamespace(mac="00:03:50:00:00:81", unique_id="00:03:50:00:00:81", available=True,
                              device_registry_id=None, send=AsyncMock(), log_id="test")
    gateway.is_who_available = lambda who: True
    cover = MyHOMECover(hass, "Test cover", "Test cover", address, "2", address,
                        None, False, "BTicino", "Standard", gateway, travel_time=30)
    cover.hass = hass
    cover.entity_id = f"cover.test_{address}"
    cover.async_write_ha_state = MagicMock()
    cover.async_schedule_update_ha_state = MagicMock()
    cover._motion.confirm(0)
    cover._attr_current_cover_position = cover._start_position = 0
    cover._attr_is_closed = True
    return cover


def _at(seconds, call, *args):
    with patch("custom_components.myhome.cover.time.monotonic", return_value=seconds):
        return call(*args)


async def test_the_cover_moves_from_the_translation_as_if_its_status_had_come_then(hass):
    translated, reported = _cover(hass), _cover(hass, "82")
    movement = movement_translation(frame("*2*1000#1*81##"))
    movement.age = TRANSLATION_GRACE
    with patch.object(translated._strategy, "on_run_started", wraps=translated._strategy.on_run_started) as runs:
        _at(1000 + TRANSLATION_GRACE, translated.handle_event, movement)
        # Read on the test's clock: the host's monotonic clock may be below 1000 (a fresh CI runner).
        assert translated.is_opening and not _at(1000 + TRANSLATION_GRACE, lambda: translated.is_closed)
        assert translated._move_start_time == translated._run_started_at == 1000
        # The status relayed late is the same movement: no second anchor, no second run.
        _at(1001.4, translated.handle_event, frame("*2*1*81##"))
        assert translated._move_start_time == translated._run_started_at == 1000
        assert runs.call_count == 1
    _at(1000, reported.handle_event, frame("*2*1*82##"))
    for seconds in (1005, 1015, 1024):
        assert _at(seconds, lambda: translated.current_cover_position) == \
            _at(seconds, lambda: reported.current_cover_position)
    assert _at(1015, lambda: translated.current_cover_position) == 50
    _at(1015, translated.handle_event, frame("*2*0*81##"))
    assert not translated.is_opening and translated.current_cover_position == 50


# ── the guided session ──────────────────────────────────────────────


def _wire(cal, relay, raw):
    """What the platform does with a frame: the relay sees it, discovery routes the statuses."""
    message = frame(raw)
    relay.send(message)
    if not message.is_translation:
        cal.cover.handle_event(message)


async def test_a_run_confirmed_only_by_its_translation_is_measured(hass, calibration):
    cal = calibration
    relay = Relay(hass, cal.clock)
    relay.relay.deliver = cal.cover.handle_event
    await act(cal, "open")
    assert cal.queue[-1][1]()  # Written now.
    cal.clock[0] += .05
    _wire(cal, relay, "*2*1000#0*11##")
    _wire(cal, relay, "*2*0*11##")  # The gateway's stop around the command: not the run's end.
    cal.clock[0] += .07
    _wire(cal, relay, "*2*1000#1*11##")
    assert cal.session.phase == "starting_open"
    arrived = cal.clock[0]
    relay.release("11")
    assert cal.session.phase == "opening"
    assert cal.session.started_at == pytest.approx(arrived)
    assert cal.session.deadline._args == (cal.session.travel_reason,)  # No start_timeout armed.
    # The status relayed after the grace changes nothing.
    cal.clock[0] += .3
    _wire(cal, relay, "*2*1*11##")
    assert cal.session.phase == "opening" and cal.session.started_at == pytest.approx(arrived)
    cal.clock[0] = arrived + 20.5
    await act(cal, "endpoint")
    assert cal.session.values["opening_time"] == 20.5
    relay.relay.close()


async def test_a_status_that_follows_still_dates_the_run(hass, calibration):
    cal = calibration
    relay = Relay(hass, cal.clock)
    relay.relay.deliver = cal.cover.handle_event
    await act(cal, "open")
    assert cal.queue[-1][1]()
    for raw in ("*2*1000#0*11##", "*2*0*11##", "*2*1000#1*11##"):
        _wire(cal, relay, raw)
    cal.clock[0] += .4
    _wire(cal, relay, "*2*1*11##")
    assert relay.held() == []  # Superseded: never delivered.
    assert cal.session.phase == "opening" and cal.session.started_at == cal.clock[0]


# ── the geometry session ────────────────────────────────────────────


async def test_a_half_run_confirmed_by_its_translation_arms_its_stop_once(hass, geometry):
    cal = geometry
    relay = Relay(hass, cal.clock)
    relay.relay.deliver = cal.cover.handle_event
    await lift(cal)
    await act(cal, "reading", reading_cm=2.0)
    await endpoint(cal, 8)
    await endpoint(cal, 22)
    await act(cal, "reading", reading_cm=200)
    await endpoint(cal, 20)
    assert (cal.session.phase, cal.session.step) == ("briefing", "half_open")
    await act(cal, "next")
    _, guard, _, written = cal.queue[-1]
    assert guard()
    written.set_result(cal.clock[0])
    await asyncio.sleep(0)
    cal.clock[0] += .1
    _wire(cal, relay, "*2*1000#1*11##")
    relay.release("11")
    assert cal.session.phase == "opening"
    sample = cal.session.deadline
    assert sample._callback == cal.session._sample_stop
    seconds = (cal.session.samples["lift"] + cal.session.values["opening_time"]) / 2
    # Timed from the translation, a grace before the delivery.
    assert sample.when() - hass.loop.time() == pytest.approx(seconds - TRANSLATION_GRACE, abs=.1)
    cal.clock[0] += .3
    _wire(cal, relay, "*2*1*11##")
    assert cal.session.deadline is sample and not sample.cancelled()


# ── the bus, end to end ─────────────────────────────────────────────
#
# A loaded config entry with two shutters: every frame goes through the gateway's dispatcher
# signal, the relay and discovery as on a live plant, the grace runs on the loop's timer and
# the state machine records what the cover shows. The clocks of the relay and of the cover
# are the same hand-driven one.

MAC = "00:03:50:00:00:81"
SIGNAL = f"myhome_message_{MAC}"


async def _plant(hass, tmp_path):
    plant_yaml = tmp_path / "myhome.yaml"
    plant_yaml.write_text(
        f"mh201:\n  mac: '{MAC}'\n  cover:\n"
        + "".join(f"    cover_{w}:\n      where: '{w}'\n      name: Cover {w}\n      travel_time: 30\n"
                  for w in ("81", "82")),
        encoding="utf-8")
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={CONF_HOST: "192.0.2.1", CONF_PORT: 20000, CONF_PASSWORD: "pass", CONF_MAC: MAC,
              CONF_NAME: "MH201", CONF_DEVICE_TYPE: "urn:schemas-bticino-it:device:IP scenario module:1",
              CONF_FRIENDLY_NAME: "MH201 Gateway", CONF_MANUFACTURER: "BTicino S.p.A.", CONF_FIRMWARE: "1.0"},
        options={CONF_FILE_PATH: str(plant_yaml)},
        unique_id=MAC,
    )
    entry.add_to_hass(hass)
    with patch("custom_components.myhome.gateway.OWNSession.test_connection",
               return_value={"Success": True, "Message": None}), \
         patch("custom_components.myhome.gateway.MyHOMEGatewayHandler.listening_loop"), \
         patch("custom_components.myhome.gateway.MyHOMEGatewayHandler.sending_loop"):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
    entry.runtime_data.gateway._on_event_connection_state_change(True)
    return entry


class Bus:
    """The loaded plant on a hand-driven clock, counting what cover 81 does."""

    def __init__(self, hass, entry):
        self.hass, self.entry, self.clock = hass, entry, [1000.0]
        (platform,) = [p for p in entity_platform.async_get_platforms(hass, DOMAIN) if p.domain == "cover"]
        self.cover = platform.entities["cover.cover_81"]
        self.anchors = []  # Every movement start of cover 81: when it was dated.
        self.states = []  # Every state cover 81 entered.

        @callback
        def entered(event: Event) -> None:
            old, new = event.data["old_state"], event.data["new_state"]
            if event.data["entity_id"] == "cover.cover_81" and new is not None and (old is None or old.state != new.state):
                self.states.append(new.state)

        self._undo = hass.bus.async_listen(EVENT_STATE_CHANGED, entered)
        anchor = MyHOMECover._anchor_run

        def counted(cover, at):
            if cover is self.cover:
                self.anchors.append(at)
            return anchor(cover, at)

        fake_time = SimpleNamespace(monotonic=lambda: self.clock[0], time=time.time)
        self._patches = [patch.object(MyHOMECover, "_anchor_run", counted),
                         patch("custom_components.myhome.cover.time", fake_time),
                         patch(CLOCK, side_effect=lambda: self.clock[0])]
        for p in self._patches:
            p.start()

    async def send(self, *raws, step=0.0):
        for raw in raws:
            self.clock[0] += step
            async_dispatcher_send(self.hass, SIGNAL, frame(raw))
        await self.hass.async_block_till_done()

    async def wait(self, seconds):
        """Let `seconds` pass on both clocks: the grace timer runs when it is due."""
        self.clock[0] += seconds
        async_fire_time_changed(self.hass, dt_util.utcnow() + timedelta(seconds=seconds))
        await self.hass.async_block_till_done()

    def state(self):
        return self.hass.states.get("cover.cover_81")

    def stop(self):
        self._undo()
        for p in self._patches:
            p.stop()


def _pending_releases(hass):
    """The grace timers still due on the loop."""
    return [h for h in hass.loop._scheduled
            if not h.cancelled() and getattr(h._callback, "__name__", None) == "_release"]


@pytest.fixture
async def bus(hass, tmp_path):
    entry = await _plant(hass, tmp_path)
    bus = Bus(hass, entry)
    await bus.send("*2*0*81##", "*2*0*82##")  # Both shutters known and at rest.
    cover = bus.cover  # Closed, as after a run to the end stop.
    cover._motion.confirm(0)
    cover._attr_current_cover_position = cover._start_position = 0
    cover._attr_is_closed = True
    cover.async_write_ha_state()
    bus.states.clear()
    yield bus
    bus.stop()
    if entry.state.recoverable:
        await hass.config_entries.async_unload(entry.entry_id)
    assert _pending_releases(hass) == []


@pytest.mark.parametrize(("what", "moving", "ended", "position"), [
    ("1", "opening", "open", 50),  # From closed, half the travel time up.
    ("2", "closing", "closed", 0),  # From half open, half the travel time down.
])
async def test_a_status_relayed_after_the_delivery_is_the_same_movement(bus, what, moving, ended, position):
    """1 and 4: the actuator's status comes after the grace, once the translation has been delivered."""
    if what == "2":  # Half open, so that it can close.
        bus.cover._motion.confirm(50)
        bus.cover._attr_current_cover_position = bus.cover._start_position = 50
        bus.cover._attr_is_closed = False
        bus.cover.async_write_ha_state()
        bus.states.clear()
    await bus.send("*2*1000#0*81##", "*2*0*81##")
    await bus.send(f"*2*1000#{what}*81##", step=.02)
    arrived = bus.clock[0]
    assert bus.state().state not in ("opening", "closing") and len(_pending_releases(bus.hass)) == 1
    await bus.wait(TRANSLATION_GRACE)
    assert bus.state().state == moving
    assert bus.anchors == [arrived] and bus.cover._move_start_time == arrived
    started, changed = bus.state().attributes["motion_started_at"], bus.state().last_changed
    # The status relayed late: the same movement, neither a second start nor a new date.
    await bus.wait(.4)
    await bus.send(f"*2*{what}*81##")
    assert bus.anchors == [arrived] and bus.cover._move_start_time == arrived
    assert bus.state().state == moving and bus.state().last_changed == changed
    assert bus.state().attributes["motion_started_at"] == started
    assert bus.states == [moving]  # One movement entered, once.
    # The run ends on its stop, timed from the translation.
    bus.clock[0] = arrived + 15
    await bus.send("*2*0*81##")
    assert bus.state().attributes["last_run_seconds"] == 15.0
    assert bus.state().attributes["current_position"] == position
    assert bus.states == [moving, ended]
    assert _pending_releases(bus.hass) == []


async def test_a_status_inside_the_window_is_the_only_start(bus):
    """4: the usual sequence, the status ~0.4 s after the translation, starts the movement once."""
    await bus.send(*LIVE_UP)
    await bus.send("*2*1*81##", step=.4)
    status_at = bus.clock[0]
    assert _pending_releases(bus.hass) == []
    await bus.wait(2 * TRANSLATION_GRACE)
    assert bus.state().state == "opening"
    assert bus.anchors == [status_at] and bus.cover._move_start_time == status_at
    assert bus.states == ["opening"]


@pytest.mark.parametrize(("follower", "outcome", "dated"), [
    ("*2*0*81##", [], None),  # The actuator reports it is stopped: it never moved.
    ("*2*1000#0*81##", [], None),  # A Stop translated after the direction: that run is over.
    ("*2*2*81##", ["closing"], "follower"),  # The actuator runs the other way.
    ("*2*1000#2*81##", ["closing"], "follower"),  # The other way, and no status follows that either.
])
async def test_a_stop_or_reversal_in_the_window_decides_once(bus, follower, outcome, dated):
    """2: whatever follows the held `1000#1` in the window leaves one outcome and no timer behind."""
    await bus.send(*LIVE_UP)
    (first,) = _pending_releases(bus.hass)
    await bus.send(follower, step=.3)
    follower_at = bus.clock[0]
    assert first.cancelled()
    await bus.wait(TRANSLATION_GRACE)
    await bus.wait(TRANSLATION_GRACE)  # Long after either window: nothing stale arrives.
    assert bus.states == outcome
    assert "opening" not in bus.states and not bus.cover.is_opening
    assert bus.anchors == ([follower_at] if dated else [])
    assert _pending_releases(bus.hass) == []


async def test_a_reversal_after_the_delivery_is_a_new_run(bus):
    """2: down pressed while the delivered up runs; the gateway's frames for it, then no status again."""
    await bus.send(*LIVE_UP)
    up = bus.clock[0]
    await bus.wait(TRANSLATION_GRACE)
    bus.clock[0] = up + 6
    await bus.send("*2*1000#0*81##", "*2*0*81##")
    stopped = bus.state().attributes["current_position"]
    assert bus.state().state == "open" and stopped == 20
    await bus.send("*2*1000#2*81##", step=.02)
    down = bus.clock[0]
    await bus.wait(TRANSLATION_GRACE)
    assert bus.states == ["opening", "open", "closing"]
    assert bus.anchors == [up, down]
    bus.clock[0] = down + 3
    assert bus.cover.current_cover_position == stopped - 10


async def test_unloading_the_entry_cancels_the_held_translation(hass, bus):
    """3: the held delivery is cancelled with the entry, never runs and leaves no handle."""
    await bus.send(*LIVE_UP)
    (held,) = _pending_releases(hass)
    assert await hass.config_entries.async_unload(bus.entry.entry_id)
    await hass.async_block_till_done()
    assert held.cancelled() and _pending_releases(hass) == []
    await bus.wait(2 * TRANSLATION_GRACE)
    assert bus.anchors == [] and "opening" not in bus.states
    # The relay no longer listens: a translation after the unload is not held.
    await bus.send("*2*1000#1*81##")
    assert _pending_releases(hass) == []


async def test_a_cover_removed_while_held_receives_nothing(hass, bus):
    """3: the entity goes, the entry stays: the delivery finds no one and raises nothing."""
    await bus.send(*LIVE_UP)
    er.async_get(hass).async_remove("cover.cover_81")
    await hass.async_block_till_done()
    assert hass.states.get("cover.cover_81") is None
    await bus.wait(TRANSLATION_GRACE)
    assert bus.anchors == [] and not bus.cover.is_opening
    assert _pending_releases(hass) == []


async def test_a_dropped_connection_does_not_cancel_the_held_translation(hass, bus):
    """3: the connection drops inside the window; the cover lives on and still gets its movement.

    The translation is the gateway's report of a command already on the bus. A dropped event
    session is one more way for the status to go missing, the case the translation stands in
    for; the entities stay available through the reconnect grace and nothing is torn down.
    """
    gateway = bus.entry.runtime_data.gateway
    await bus.send(*LIVE_UP)
    arrived = bus.clock[0]
    gateway._on_event_connection_state_change(False)
    await bus.wait(TRANSLATION_GRACE)
    assert bus.state().state == "opening" and bus.anchors == [arrived]
    gateway._on_event_connection_state_change(True)
    assert _pending_releases(hass) == []


@pytest.mark.parametrize(("confirmed_by", "anchors"), [
    ("status", [1000.0, 1000.4]),  # The usual sequence.
    ("translation", [1000.0, 1000.1]),  # No status in the window: the translation, dated on arrival.
])
async def test_our_own_command_starts_alike_whatever_confirms_it(hass, confirmed_by, anchors):
    """1 and 4: HA wrote the Up. The write anchors the run provisionally and its confirmation once
    more, precisely; the translation does what the status does, and the status relayed late adds
    nothing."""
    cover = _cover(hass)
    written = hass.loop.create_future()
    written.set_result(1000.0)
    cover._gateway_handler.send = AsyncMock(return_value=written)
    with patch.object(cover, "_anchor_run", wraps=cover._anchor_run) as anchor:
        with patch("custom_components.myhome.cover.time.monotonic", return_value=1000.0):
            await cover._async_move("open")
            await asyncio.sleep(0)  # The write is confirmed.
        _at(1000.05, cover.handle_event, frame("*2*0*81##"))  # The gateway's stop around the write.
        if confirmed_by == "status":
            _at(1000.4, cover.handle_event, frame("*2*1*81##"))
        else:
            movement = movement_translation(frame("*2*1000#1*81##"))
            movement.age = TRANSLATION_GRACE
            _at(1000.1 + TRANSLATION_GRACE, cover.handle_event, movement)
            _at(1001.5, cover.handle_event, frame("*2*1*81##"))  # Late.
        assert [c.args[0] for c in anchor.call_args_list] == pytest.approx(anchors)
    assert cover._pending_cmd is None and cover.is_opening
    assert cover._move_start_time == pytest.approx(anchors[-1])
