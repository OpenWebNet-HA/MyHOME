"""Support for MyHome covers."""
import asyncio
import time

from homeassistant.components.cover import (
    ATTR_CURRENT_POSITION,
    ATTR_POSITION,
    CoverDeviceClass,
    CoverEntity,
    CoverEntityFeature,
)
from homeassistant.components.cover import (
    DOMAIN as PLATFORM,
)
from homeassistant.const import (
    CONF_MAC,
    CONF_NAME,
    STATE_CLOSED,
    STATE_OPEN,
)
from homeassistant.core import callback
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.dispatcher import async_dispatcher_connect, async_dispatcher_send
from OWNd.message import (
    OWNAutomationCommand,
    OWNAutomationEvent,
)

from .const import (
    CONF_ADVANCED_SHUTTER,
    CONF_BUS_INTERFACE,
    CONF_DEVICE_MODEL,
    CONF_ENTITY,
    CONF_ENTITY_NAME,
    CONF_MANUFACTURER,
    CONF_PLATFORMS,
    CONF_TRAVEL_TIME,
    CONF_WHERE,
    CONF_WHO,
    DEFAULT_TRAVEL_TIME,
    DOMAIN,
    LOGGER,
)
from .gateway import MyHOMEGatewayHandler
from .myhome_device import MyHOMEEntity

PARALLEL_UPDATES = 0

# Timed-cover echo model (issue #302). After a direction/stop frame is written,
# the gateway relays our own command back on the monitor session: a stop status
# within ~0.1 s, the WHAT=1000 translation, and the real direction status once the
# motor starts (~0.55 s on a MyHOMEServer1). Frames for this cover inside the
# window are treated as echoes of our command, not as keypad presses.
ECHO_WINDOW = 1.5
# Upper bound on how long we wait for the send queue to write our frame before
# falling back to "now" as the motion anchor.
WRITE_TIMEOUT = 10.0


async def async_setup_entry(hass, config_entry, async_add_entities):
    """Set up the MyHOME cover platform dynamically via Discovery."""
    known_covers = set()

    # Restore previously discovered entities from the Entity Registry so they
    # are available immediately on restart, even before the gateway responds.
    try:
        entity_registry = er.async_get(hass)
        existing_entries = er.async_entries_for_config_entry(entity_registry, config_entry.entry_id)
    except Exception:
        entity_registry = None
        existing_entries = []
    restored_covers = []

    gateway = hass.data[DOMAIN][config_entry.data[CONF_MAC]][CONF_ENTITY]
    _configured_covers = hass.data[DOMAIN][config_entry.data[CONF_MAC]].get(CONF_PLATFORMS, {}).get(PLATFORM, {})

    for entry in existing_entries:
        if entry.domain == PLATFORM:
            unique_id = entry.unique_id
            # unique_id format: "{mac}-{who}-{device_id}"
            # device_id is "{where}" or "{where}#4#{interface}"
            after_mac = unique_id.replace(f"{gateway.mac}-", "", 1).replace(f"{config_entry.data[CONF_MAC]}-", "", 1)
            # Strip the WHO prefix: "2-85" -> "85", "2-18#4#02" -> "18#4#02"
            parts_who = after_mac.split("-", 1)
            device_id = parts_who[-1] if len(parts_who) > 1 else after_mac
            if "#4#" in device_id:
                parts = device_id.split("#4#")
                where = parts[0]
                interface = parts[1] if len(parts) > 1 else None
            else:
                where = device_id
                interface = None

            clean_where = where.split('-')[-1]
            default_suffix = f"{clean_where}I{interface}" if interface else clean_where
            cfg = _configured_covers.get(device_id) or _configured_covers.get(where) or _configured_covers.get(clean_where) or {}
            _advanced = cfg.get(
                CONF_ADVANCED_SHUTTER, cfg.get("advanced_shutter", False)
            )
            _travel_time = int(cfg.get(CONF_TRAVEL_TIME, DEFAULT_TRAVEL_TIME))
            _name = cfg.get(CONF_NAME) or f"Cover {default_suffix}"
            _cover = MyHOMECover(
                hass=hass,
                name=_name,
                entity_name=cfg.get(CONF_ENTITY_NAME),
                device_id=device_id,
                who="2",
                where=where,
                interface=interface,
                advanced=_advanced,
                manufacturer=cfg.get(CONF_MANUFACTURER, "BTicino"),
                model=cfg.get(CONF_DEVICE_MODEL, "Shutter / Cover"),
                gateway=gateway,
                travel_time=_travel_time,
            )
            known_covers.add(device_id)
            restored_covers.append(_cover)

    # Also instantiate any configured covers from myhome.yaml not yet in registry
    seen_configured_where = set()
    for dev_id, cfg in _configured_covers.items():
        where = str(cfg.get(CONF_WHERE, dev_id))
        interface = cfg.get(CONF_BUS_INTERFACE)
        device_where_id = f"{where}#4#{interface}" if interface else str(where)
        clean_where = where.split("-")[-1]
        clean_unique_id = f"{clean_where}#4#{interface}" if interface else clean_where
        default_suffix = f"{clean_where}I{interface}" if interface else clean_where

        if clean_unique_id in seen_configured_where or device_where_id in known_covers or dev_id in known_covers:
            continue
        seen_configured_where.add(clean_unique_id)

        _name = cfg.get(CONF_NAME) or f"Cover {default_suffix}"
        _advanced = cfg.get(
            CONF_ADVANCED_SHUTTER, cfg.get("advanced_shutter", False)
        )
        _travel_time = int(cfg.get(CONF_TRAVEL_TIME, DEFAULT_TRAVEL_TIME))
        _cover = MyHOMECover(
            hass=hass,
            name=_name,
            entity_name=cfg.get(CONF_ENTITY_NAME),
            device_id=device_where_id,
            who=str(cfg.get(CONF_WHO, "2")),
            where=where,
            interface=interface,
            advanced=_advanced,
            manufacturer=cfg.get(CONF_MANUFACTURER, "BTicino"),
            model=cfg.get(CONF_DEVICE_MODEL, "Shutter / Cover"),
            gateway=gateway,
            travel_time=_travel_time,
        )
        known_covers.add(device_where_id)
        known_covers.add(dev_id)
        if not interface:
            known_covers.add(clean_where)
        restored_covers.append(_cover)

        # Signal button platform to create Lock/Unlock buttons
        async_dispatcher_send(
            hass,
            f"myhome_new_device_{config_entry.data[CONF_MAC]}",
            {
                "who": str(cfg.get(CONF_WHO, "2")),
                "where": where,
                "interface": interface,
                "name": _name,
                "device_id": device_where_id,
            },
        )

    if restored_covers:
        async_add_entities(restored_covers)

    @callback
    def async_add_cover(message):
        """Add a cover from a discovered message."""
        if getattr(message, "is_translation", None) is True:
            return

        # Handle general cover commands (WHERE=0, is_general=True)
        if getattr(message, "is_general", False) or str(getattr(message, "where", "")) == "0":
            async_dispatcher_send(
                hass,
                f"myhome_update_{config_entry.data[CONF_MAC]}_2_general",
                message,
            )
            return

        if not hasattr(message, "where") or not message.where:
            return

        # Skip groups and areas for now, as they represent many physical devices
        if getattr(message, "is_group", False) or getattr(message, "is_area", False):
            return

        where = message.where
        interface = getattr(message, "interface", None)
        unique_id = f"{where}#4#{interface}" if interface else str(where)

        if unique_id not in known_covers:
            # We found a new cover!
            clean_where = where.split('-')[-1]
            default_suffix = f"{clean_where}I{interface}" if interface else clean_where
            cfg = _configured_covers.get(unique_id) or _configured_covers.get(where) or _configured_covers.get(clean_where) or {}
            _advanced = cfg.get(
                CONF_ADVANCED_SHUTTER, cfg.get("advanced_shutter", False)
            )
            _travel_time = int(cfg.get(CONF_TRAVEL_TIME, DEFAULT_TRAVEL_TIME))
            _name = cfg.get(CONF_NAME) or f"Cover {default_suffix}"
            _cover = MyHOMECover(
                hass=hass,
                name=_name,
                entity_name=cfg.get(CONF_ENTITY_NAME),
                device_id=unique_id,
                who=str(message.who),
                where=where,
                interface=interface,
                advanced=_advanced,
                manufacturer=cfg.get(CONF_MANUFACTURER, "BTicino"),
                model=cfg.get(CONF_DEVICE_MODEL, "Shutter / Cover"),
                gateway=hass.data[DOMAIN][config_entry.data[CONF_MAC]][CONF_ENTITY],
                travel_time=_travel_time,
            )
            known_covers.add(unique_id)
            async_add_entities([_cover])
            _cover.handle_event(message)

            # Signal button platform to create Lock/Unlock buttons if not present
            async_dispatcher_send(
                hass,
                f"myhome_new_device_{config_entry.data[CONF_MAC]}",
                {"who": "2", "where": where, "interface": interface, "name": _name, "device_id": unique_id}
            )

        async_dispatcher_send(hass, f"myhome_update_{config_entry.data[CONF_MAC]}_2_{unique_id}", message)

    @callback
    def _handle_cover_message(msg):
        """Filter and forward cover messages."""
        if isinstance(msg, OWNAutomationEvent):
            async_add_cover(msg)

    # Listen to all incoming gateway messages
    config_entry.async_on_unload(
        async_dispatcher_connect(
            hass,
            f"myhome_message_{config_entry.data[CONF_MAC]}",
            _handle_cover_message,
        )
    )

async def async_unload_entry(hass, config_entry):  # pylint: disable=unused-argument
    """Unload cover platform."""
    return True


class MyHOMECover(MyHOMEEntity, CoverEntity):
    device_class = CoverDeviceClass.SHUTTER

    def __init__(
        self,
        hass,
        name: str,
        entity_name: str,
        device_id: str,
        who: str,
        where: str,
        interface: str,
        advanced: bool,
        manufacturer: str,
        model: str,
        gateway: MyHOMEGatewayHandler,
        travel_time: int = DEFAULT_TRAVEL_TIME,
    ):
        super().__init__(
            hass=hass,
            name=name,
            platform=PLATFORM,
            device_id=device_id,
            who=who,
            where=where,
            manufacturer=manufacturer,
            model=model,
            gateway=gateway,
        )

        self._interface = interface
        self._full_where = f"{self._where}#4#{self._interface}" if self._interface is not None else self._where
        self._advanced = advanced
        self._travel_time = travel_time

        # Both advanced and standard covers support SET_POSITION (standard via travel time estimation)
        self._attr_supported_features = (
            CoverEntityFeature.OPEN
            | CoverEntityFeature.CLOSE
            | CoverEntityFeature.STOP
            | CoverEntityFeature.SET_POSITION
        )
        self._gateway_handler = gateway

        self._attr_extra_state_attributes = {
            "A": where[: len(where) // 2],
            "PL": where[len(where) // 2 :],
        }
        if self._interface is not None:
            self._attr_extra_state_attributes["Int"] = self._interface
        if not self._advanced:
            self._attr_extra_state_attributes["travel_time"] = self._travel_time

        self._attr_current_cover_position = 50
        self._attr_is_opening = False
        self._attr_is_closing = False
        self._attr_is_closed = False

        self._move_start_time = None
        self._start_position = 50
        self._stop_task = None

        # Echo model state (see ECHO_WINDOW): which command of ours is in
        # flight, until when relayed frames count as its echo, when the motor
        # was observed to start, and a generation counter so a stale auto-stop
        # timer never acts on a later run.
        self._pending_cmd: str | None = None
        self._echo_until: float | None = None
        self._motor_started: asyncio.Event = asyncio.Event()
        self._run_generation: int = 0

    # ── Echo model helpers ───────────────────────────────────────────────

    def _begin_command(self, cmd: str) -> None:
        """Open an echo window for a command we are about to queue."""
        self._run_generation += 1
        self._pending_cmd = cmd
        self._echo_until = float("inf")  # until the write time is known
        self._motor_started = asyncio.Event()

    def _track_write(self, written) -> None:
        """Anchor the echo window (and the clock) on the real write time."""
        if not isinstance(written, asyncio.Future):
            # No delivery information (legacy gateway object): bound the window
            # from enqueue so an external frame can never be mistaken for an
            # echo indefinitely.
            self._echo_until = time.monotonic() + ECHO_WINDOW
            return
        generation = self._run_generation

        @callback
        def _on_written(fut: asyncio.Future) -> None:
            if generation != self._run_generation or fut.cancelled() or fut.exception() is not None:
                return
            write_ts = fut.result()
            self._echo_until = write_ts + ECHO_WINDOW
            if self._pending_cmd in ("open", "close") and not self._motor_started.is_set():
                # Provisional anchor: the motor starts shortly after the write.
                # The direction echo re-anchors precisely if the gateway relays it.
                self._move_start_time = write_ts
            elif self._pending_cmd == "stop":
                # Motion stops within ~0.1 s of the write, not at enqueue time.
                self._freeze_position(write_ts)
                self._motor_started.set()

        written.add_done_callback(_on_written)

    def _in_echo_window(self, now: float) -> bool:
        return self._echo_until is not None and now < self._echo_until

    def _end_echo_window(self) -> None:
        self._pending_cmd = None
        self._echo_until = None

    def _freeze_position(self, at: float) -> None:
        """Turn the running estimate into a fixed position as of ``at``."""
        if self._move_start_time is not None:
            elapsed = max(0.0, at - self._move_start_time)
            delta = (elapsed / self._travel_time) * 100
            if self._attr_is_opening:
                self._attr_current_cover_position = min(100, int(round(self._start_position + delta)))
            elif self._attr_is_closing:
                self._attr_current_cover_position = max(0, int(round(self._start_position - delta)))
            self._start_position = self._attr_current_cover_position
            self._move_start_time = None
        self._attr_is_opening = False
        self._attr_is_closing = False
        if self._attr_current_cover_position is not None:
            self._attr_is_closed = (self._attr_current_cover_position == 0)

    async def _await_motion_anchor(self, written) -> float:
        """Wait for our frame to be written and for the motor-start echo.

        Returns the monotonic time motion is anchored on: the direction echo
        if the gateway relayed one inside the window, else the write time,
        else (no delivery within WRITE_TIMEOUT) now.
        """
        if isinstance(written, asyncio.Future):
            try:
                await asyncio.wait_for(asyncio.shield(written), WRITE_TIMEOUT)
            except (TimeoutError, asyncio.CancelledError):
                pass
        deadline = self._echo_until if self._echo_until not in (None, float("inf")) else time.monotonic() + ECHO_WINDOW
        remaining = deadline - time.monotonic()
        if remaining > 0 and not self._motor_started.is_set():
            try:
                await asyncio.wait_for(self._motor_started.wait(), remaining)
            except TimeoutError:
                pass
        if self._move_start_time is None:
            self._move_start_time = time.monotonic()
        return self._move_start_time

    def _cancel_stop_task(self):
        """Cancel any running scheduled auto-stop task."""
        if self._stop_task is not None:
            current = asyncio.current_task()
            if self._stop_task is not current and not self._stop_task.done():
                self._stop_task.cancel()
            self._stop_task = None

    @property
    def current_cover_position(self):
        """Return current cover position (interpolated if moving)."""
        if not self._advanced and self._move_start_time is not None:
            elapsed = time.monotonic() - self._move_start_time
            delta = (elapsed / self._travel_time) * 100
            if self._attr_is_opening:
                return min(100, int(round(self._start_position + delta)))
            if self._attr_is_closing:
                return max(0, int(round(self._start_position - delta)))
        return self._attr_current_cover_position

    @property
    def is_opening(self):
        """Return if the cover is opening."""
        return self._attr_is_opening

    @property
    def is_closing(self):
        """Return if the cover is closing."""
        return self._attr_is_closing

    @property
    def is_closed(self):
        """Return if the cover is closed."""
        if self.current_cover_position is not None:
            return self.current_cover_position == 0
        return self._attr_is_closed

    async def async_added_to_hass(self):
        """Run when entity about to be added to hass."""
        target_hass = self.hass or self._hass
        if target_hass is not None:
            self.async_on_remove(
                async_dispatcher_connect(
                    target_hass,
                    f"myhome_update_{self._gateway_handler.mac}_2_{self._full_where}",
                    self.handle_event,
                )
            )
            self.async_on_remove(
                async_dispatcher_connect(
                    target_hass,
                    f"myhome_update_{self._gateway_handler.mac}_2_general",
                    self.handle_event,
                )
            )
        # Subscribe before requesting the current status so the reply cannot
        # arrive before this entity is ready to handle it.
        if self._advanced:
            # Advanced covers query live position from bus; do not restore stale state
            self._register_availability_listener()
            await self.async_update()
            return
        await super().async_added_to_hass()

    async def async_restore_last_state(self, last_state) -> None:
        """Restore cover position and closure state."""
        if not self._advanced:
            restored = False
            last_pos = last_state.attributes.get(ATTR_CURRENT_POSITION)
            if last_pos is not None:
                try:
                    self._attr_current_cover_position = max(0, min(100, int(round(float(last_pos)))))
                    self._start_position = self._attr_current_cover_position
                    self._attr_is_closed = (self._attr_current_cover_position == 0)
                    restored = True
                except (ValueError, TypeError):
                    restored = False
            if not restored:
                if last_state.state in (STATE_CLOSED, "closed"):
                    self._attr_current_cover_position = 0
                    self._start_position = 0
                    self._attr_is_closed = True
                elif last_state.state in (STATE_OPEN, "open"):
                    self._attr_current_cover_position = 100
                    self._start_position = 100
                    self._attr_is_closed = False

    async def async_will_remove_from_hass(self):
        """Run when entity will be removed from hass."""
        self._cancel_stop_task()
        await super().async_will_remove_from_hass()

    async def async_update(self):
        """Update the entity.

        Only used by the generic entity update service.
        """
        if self._advanced:
            await self._gateway_handler.send_status_request(
                OWNAutomationCommand.get_shutter_status(self._full_where)
            )
        else:
            await self._gateway_handler.send_status_request(
                OWNAutomationCommand.status(self._full_where)
            )

    async def async_open_cover(self, **kwargs):  # pylint: disable=unused-argument
        """Open the cover."""
        await self._async_move("open")

    async def async_close_cover(self, **kwargs):  # pylint: disable=unused-argument
        """Close cover."""
        await self._async_move("close")

    async def _async_move(self, direction: str):
        """Queue a direction command and return its delivery future."""
        self._cancel_stop_task()
        if direction == "open":
            command = OWNAutomationCommand.raise_shutter(self._full_where)
        else:
            command = OWNAutomationCommand.lower_shutter(self._full_where)
        if not self._advanced:
            self._start_position = self.current_cover_position if self.current_cover_position is not None else (0 if direction == "open" else 100)
            # The clock starts when the frame is written (see _track_write),
            # not now: with a busy queue the motor is still idle for a while.
            self._move_start_time = None
            self._attr_is_opening = direction == "open"
            self._attr_is_closing = direction == "close"
            self._attr_is_closed = False
            self._begin_command(direction)
        written = await self._gateway_handler.send(command)
        if not self._advanced:
            self._track_write(written)
        if self.hass is not None:
            self.async_write_ha_state()
        return written

    async def async_set_cover_position(self, **kwargs):
        """Move the cover to a specific position."""
        if ATTR_POSITION not in kwargs:
            return
        target_position = kwargs[ATTR_POSITION]
        if self._advanced:
            if target_position <= 0:
                await self._gateway_handler.send(
                    OWNAutomationCommand.lower_shutter(self._full_where)
                )
            else:
                await self._gateway_handler.send(
                    OWNAutomationCommand.set_shutter_level(
                        self._full_where, target_position
                    )
                )
            return

        self._cancel_stop_task()
        curr_pos = self.current_cover_position if self.current_cover_position is not None else 50
        diff = target_position - curr_pos
        if diff == 0:
            return

        travel_fraction = abs(diff) / 100.0
        run_duration = travel_fraction * self._travel_time

        written = await self._async_move("open" if diff > 0 else "close")
        generation = self._run_generation

        async def _auto_stop():
            try:
                anchor = await self._await_motion_anchor(written)
                if generation != self._run_generation:
                    return  # a newer command superseded this run
                await asyncio.sleep(max(0.0, run_duration - (time.monotonic() - anchor)))
                if generation != self._run_generation:
                    return
                # By the model we are at the target now; the motor keeps
                # running until the stop frame is written, so re-anchor the
                # run here and let the stop's write time freeze the estimate
                # (target plus whatever the queue delay added).
                self._start_position = target_position
                self._attr_current_cover_position = target_position
                self._move_start_time = time.monotonic()
                await self.async_stop_cover()
                if self.hass is not None:
                    self.async_write_ha_state()
            except asyncio.CancelledError:
                pass

        self._stop_task = asyncio.create_task(_auto_stop())

    async def async_stop_cover(self, **kwargs):  # pylint: disable=unused-argument
        """Stop the cover."""
        self._cancel_stop_task()
        if not self._advanced:
            # The estimate keeps running until the stop frame is actually
            # written (_track_write freezes it then); if delivery never
            # happens the next status frame will correct us.
            self._begin_command("stop")
        written = await self._gateway_handler.send(OWNAutomationCommand.stop_shutter(self._full_where))
        if not self._advanced:
            self._track_write(written)
            if not isinstance(written, asyncio.Future):
                self._freeze_position(time.monotonic())
        if self.hass is not None:
            self.async_write_ha_state()

    def _handle_echo(self, message: OWNAutomationEvent, now: float) -> bool:
        """Consume frames the gateway relays for our own in-flight command.

        Returns True when the frame was an echo and needs no further handling.
        """
        if self._advanced or not self._in_echo_window(now) or self._pending_cmd is None:
            return False
        is_stop = not message.is_opening and not message.is_closing
        if self._pending_cmd in ("open", "close"):
            matches = (message.is_opening and self._pending_cmd == "open") or (
                message.is_closing and self._pending_cmd == "close"
            )
            if matches:
                # The relayed direction status marks the real motor start.
                self._move_start_time = now
                self._motor_started.set()
                self._end_echo_window()
                LOGGER.debug("%s Motor start echo for %s; clock anchored.", self._gateway_handler.log_id, self._full_where)
                return True
            if is_stop:
                # Gateway relays a stop status ~0.1 s after our direction frame,
                # before the motor starts: an echo, not a keypad stop.
                LOGGER.debug("%s Ignoring stop echo for %s.", self._gateway_handler.log_id, self._full_where)
                return True
            # Opposite direction inside the window: somebody else took over.
            self._end_echo_window()
            return False
        # Pending stop: the relayed stop confirms it, anything else is external.
        self._end_echo_window()
        return False

    @callback
    def handle_event(self, message: OWNAutomationEvent):
        """Handle an event message."""
        if getattr(message, "is_translation", None) is True:
            return
        LOGGER.debug(
            "%s %s",
            self._gateway_handler.log_id,
            message.human_readable_log,
        )
        now = time.monotonic()
        if message.current_position is None and self._handle_echo(message, now):
            if self.hass is not None or hasattr(self.async_schedule_update_ha_state, "assert_called"):
                try:
                    self.async_schedule_update_ha_state()
                except RuntimeError:
                    pass
            return
        if message.current_position is not None:
            self._cancel_stop_task()
            self._attr_current_cover_position = message.current_position
            if not self._advanced:
                self._start_position = message.current_position
            self._move_start_time = None
            self._attr_is_opening = False
            self._attr_is_closing = False
            if message.is_closed is not None:
                self._attr_is_closed = message.is_closed
            else:
                self._attr_is_closed = (self._attr_current_cover_position == 0)
        elif message.is_opening:
            if self._attr_is_closing:
                self._cancel_stop_task()
                self._run_generation += 1
            if not self._advanced and not self._attr_is_opening:
                self._start_position = self.current_cover_position if self.current_cover_position is not None else 0
                self._move_start_time = now
            self._attr_is_opening = True
            self._attr_is_closing = False
            self._attr_is_closed = False
        elif message.is_closing:
            if self._attr_is_opening:
                self._cancel_stop_task()
                self._run_generation += 1
            if not self._advanced and not self._attr_is_closing:
                self._start_position = self.current_cover_position if self.current_cover_position is not None else 100
                self._move_start_time = now
            self._attr_is_opening = False
            self._attr_is_closing = True
        else:
            # Stopped (state == 0 or other): a genuine stop ends any timed run.
            self._cancel_stop_task()
            self._run_generation += 1
            if not self._advanced:
                self._freeze_position(now)
            self._attr_is_opening = False
            self._attr_is_closing = False
            if message.is_closed is not None:
                self._attr_is_closed = message.is_closed
            elif self._attr_current_cover_position is not None:
                self._attr_is_closed = (self._attr_current_cover_position == 0)

        self._publish_state()
