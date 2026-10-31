"""A shutter's direction translation stands in for the movement status the gateway did not relay.

Live case (cover 81 behind a MyHOMEServer1): after the panel's `*2*1*81##` the monitor carried
only `*2*1000#0*81##`, `*2*0*81##`, `*2*1000#1*81##`, never `*2*1*81##`, while the motor ran.
The guided step waited 10 s and discarded the run (`start_timeout`) and the cover stayed
"stopped". The translation is now held for TRANSLATION_GRACE seconds and, when no status of
that shutter follows, delivered as its movement, dated when the translation arrived.
"""
import logging
from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from homeassistant.const import CONF_MAC
from homeassistant.core import callback
from homeassistant.helpers.dispatcher import async_dispatcher_connect, async_dispatcher_send
from homeassistant.util import dt as dt_util
from OWNd.message import OWNMessage
from pytest_homeassistant_custom_component.common import async_fire_time_changed

from custom_components.myhome.const import CONF_ENTITY, CONF_PLATFORMS, DOMAIN
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
        assert translated.is_opening and not translated.is_closed
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
