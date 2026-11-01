"""WHO=16 sound sources: the tuner half of the BTicino sound system.

A sound source (`WHERE` 101-109) is the device feeding one input of the audio
matrix. Two kinds exist in practice:

* a line interface such as the L4561 stereo control, which only reports whether
  it is active, and
* a tuner such as the F500, which additionally reports the frequency it is
  listening to, the stored station in use, and the RDS text broadcast by that
  station.

Nothing on the bus distinguishes the two until the device speaks, and a tuner
that is off says nothing, so the user declares which matrix inputs are tuners in
the integration options. Only those get an entity here.

Frames
------
Taken from `WHO_16.pdf` v1.0.1 and the OpenWebNet Encyclopedia page for WHO 16:

==========================  ==========================================
Operation                   Frame
==========================  ==========================================
Power on / off              ``*16*3*10S##`` / ``*16*13*10S##``
Next / previous station     ``*16*6001*10S##`` / ``*16*6101*10S##``
Seek up / down              ``*16*5000*10S##`` / ``*16*5100*10S##``
Start / stop RDS reporting  ``*16*101*10S##`` / ``*16*102*10S##``
Select stored station       ``*#16*10S*#7*<STATION>##``
Set frequency               ``*#16*10S*#6*0*<KHZ>##``
Frequency report            ``*#16*10S*6*0*<KHZ>##``
Station report              ``*#16*10S*7*0*<STATION>##``
RDS report                  ``*#16*10S*8*<8 ASCII codes>##``
==========================  ==========================================

The station *write* carries its parameter directly while the station *report*
prefixes it with ``0``. That asymmetry is in the specification and is preserved
here rather than normalised away.

Frequencies are documented as "expressed in Hz ... composed by 6 digits", but
every example in the same document uses kHz (``107000`` is 107.00 MHz). This
module follows the examples, as the Encyclopedia does. It does not zero-pad
below 100 MHz: an F500N took ``*#16*101*#6*0*96200##`` (#427).

Scope
-----
Tested against a live installation (MH200N gateway + F500N tuner with antenna,
contributed by @manfredgittmaier-afk on PR #427):
* Power on/off (``*16*3*10S##`` / ``*16*13*10S##``)
* Next / previous station advance (``*16*6001*10S##`` / ``*16*6101*10S##``)
* Hardware seek up / down (``*16*5000*10S##`` / ``*16*5100*10S##``)
* Direct frequency write with leading zero (``*#16*10S*#6*0*<KHZ>##``; write without zero is ignored)
* Station selection without leading zero (``*#16*10S*#7*<STATION>##``)
* Station report with leading zero (``*#16*10S*7*0*<STATION>##``)
* Frequency report in kHz (``*#16*10S*6*0*<KHZ>##``)
* Autonomous RDS station name reporting (``*#16*10S*8*...##``) and blanking transition
* Dynamic station list expansion up to 15 presets for F500N
"""
from __future__ import annotations

import math
from collections.abc import Callable
from typing import TYPE_CHECKING, Any

from homeassistant.components.media_player import (  # type: ignore[attr-defined, unused-ignore]
    BrowseMedia,
    MediaClass,
    MediaPlayerDeviceClass,
    MediaPlayerEntity,
)
from homeassistant.components.media_player.const import (
    MediaPlayerEntityFeature,
    MediaPlayerState,
    MediaType,
)
from homeassistant.const import Platform
from homeassistant.core import callback
from homeassistant.exceptions import HomeAssistantError
from OWNd.message import OWNCommand, OWNSoundCommand, OWNSoundEvent

from .const import (
    DOMAIN,
    LOGGER,
    TUNER_MAX_STATION_COUNT,
    TUNER_STATION_COUNT,
)
from .data import get_runtime_data
from .myhome_device import MyHOMEEntity
from .tuner_decode import FM_MAX_KHZ, FM_MIN_KHZ, decode_who22_tuner, rds_text

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant

    from .gateway import MyHOMEGatewayHandler

__all__ = [
    "FM_MAX_KHZ",
    "FM_MIN_KHZ",
    "MyHOMESoundSource",
    "rds_text",
    "source_address",
    "who22_source_id",
    "who22_tuner_listener",
]


def source_address(source: int) -> str:
    """Return the bus address of source device ``source`` (1-9)."""
    return str(100 + int(source))


def _build_cmd(raw: str) -> Any:
    """Build an OpenWebNet command frame across OWNd versions."""
    return OWNCommand.parse(raw) or OWNCommand(raw)


class MyHOMESoundSource(MyHOMEEntity, MediaPlayerEntity):
    """A WHO=16 / WHO=22 tuner source device.

    Presets are exposed as the entity's source list, because that is what a
    listener picks. The frequency is exposed as an attribute and via a
    companion ``number.<source>_frequency`` entity.
    """

    _attr_device_class = MediaPlayerDeviceClass.RECEIVER
    _attr_supported_features = (
        MediaPlayerEntityFeature.TURN_ON
        | MediaPlayerEntityFeature.TURN_OFF
        | MediaPlayerEntityFeature.NEXT_TRACK
        | MediaPlayerEntityFeature.PREVIOUS_TRACK
        | MediaPlayerEntityFeature.SELECT_SOURCE
        | MediaPlayerEntityFeature.PLAY_MEDIA
        | MediaPlayerEntityFeature.BROWSE_MEDIA
    )

    def __init__(
        self,
        hass: HomeAssistant,
        name: str,
        device_id: str,
        who: str,
        where: str,
        manufacturer: str,
        model: str,
        gateway: MyHOMEGatewayHandler,
        entity_name: str | None = None,
        pictures: dict[int, str] | None = None,
    ) -> None:
        """Initialise a tuner source entity.

        `pictures` maps a frequency in kHz to the image shown while the tuner
        is on it (see :func:`tuner_decode.parse_tuner_pictures`); none are bundled.
        """
        super().__init__(
            hass=hass,
            name=name,
            platform=Platform.MEDIA_PLAYER,
            device_id=device_id,
            who=who,
            where=where,
            manufacturer=manufacturer,
            model=model,
            gateway=gateway,
            entity_name=entity_name,
        )
        #: Router key this entity subscribes under, matching its unique id tail.
        self.device_key = f"{where}#16"
        self._attr_state: MediaPlayerState | None = None
        self._attr_source: str | None = None
        self._attr_media_title: str | None = None
        self._frequency_khz: int | None = None
        self._station: int | None = None
        self._station_count: int = TUNER_STATION_COUNT
        self._pictures: dict[int, str] = dict(pictures or {})

    # ── Presentation ──────────────────────────────────────────────────────────

    @property
    def source_list(self) -> list[str]:
        """Return the stored stations this tuner can be switched to."""
        return [f"Station {n}" for n in range(1, self._station_count + 1)]

    @property
    def media_content_type(self) -> str | None:
        """Report playing content as a channel while the tuner is on."""
        return MediaType.CHANNEL if self._attr_state == MediaPlayerState.ON else None

    @property
    def entity_picture(self) -> str | None:
        """Return the user's picture for the frequency the tuner is on, if any."""
        if self._frequency_khz is None:
            return None
        return self._pictures.get(self._frequency_khz)

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Expose the tuning state that has no standard media_player attribute."""
        attributes: dict[str, Any] = {}
        if self._frequency_khz is not None:
            attributes["frequency"] = round(self._frequency_khz / 1000.0, 2)
        if self._station is not None:
            attributes["station"] = self._station
        return attributes

    # ── Lifecycle ─────────────────────────────────────────────────────────────

    def _who22_addr(self) -> str:
        """Return the WHO=22 source address ``2#<S>`` for this tuner."""
        return f"2#{int(self._where) - 100}"

    def _uses_who22_commands(self) -> bool:
        """Return True on WHO=22-only gateways (MyHomeServer1, F459, F460, F461)."""
        supports = getattr(self._gateway_handler, "profile_supports_who", None)
        if not callable(supports):
            return False
        return supports(16) is False and supports(22) is True

    @property
    def available(self) -> bool:
        """Return True if this tuner's active WHO subsystem is available."""
        who = "22" if self._uses_who22_commands() else self._who
        return self._gateway_handler.is_who_available(who)

    def _notify_frequency(self) -> None:
        """Notify companion frequency number entities registered on runtime_data."""
        if self._frequency_khz is None:
            return
        entry = getattr(getattr(self, "platform", None), "config_entry", None)
        runtime = get_runtime_data(entry) if entry is not None else None
        if runtime is None:
            return
        for cb in list(runtime.tuner_frequency_callbacks.get(self._where, ())):
            cb(self._frequency_khz)

    async def async_added_to_hass(self) -> None:
        """Register listeners and ask the tuner to report RDS.

        A tuner does not broadcast its RDS text until asked (`WHAT` 101 on
        WHO=16 or `WHAT` 31 on WHO=22), so without this the media title stays
        empty. Gateways that do not support it answer NACK, which costs nothing.
        """
        self._register_availability_listener()
        if self._uses_who22_commands():
            await self._gateway_handler.send(
                _build_cmd(f"*22*31*{self._who22_addr()}##")
            )
        else:
            await self._gateway_handler.send(
                OWNSoundCommand.start_rds(self._where)
            )

    async def async_update(self) -> None:
        """Request the tuner's frequency, station and status/RDS text."""
        if self._uses_who22_commands():
            addr = self._who22_addr()
            commands: tuple[Any, ...] = (
                _build_cmd(f"*#22*{addr}*5##"),
                _build_cmd(f"*#22*{addr}*6##"),
                _build_cmd(f"*#22*{addr}*12##"),
            )
        else:
            commands = (
                OWNSoundCommand.request_frequency(self._where),
                OWNSoundCommand.request_track(self._where),
                OWNSoundCommand.request_rds(self._where),
            )
        for cmd in commands:
            await self._gateway_handler.send_status_request(cmd)

    # ── Commands ──────────────────────────────────────────────────────────────

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Switch the source device on."""
        if self._uses_who22_commands():
            await self._gateway_handler.send(
                _build_cmd(f"*22*1*{self._who22_addr()}##")
            )
        else:
            await self._gateway_handler.send(OWNSoundCommand.turn_on(self._where))

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Switch the source device to standby.

        Rooms listening to this input fall silent; the matrix routing is not
        changed, so they stay pointed at it.
        """
        if self._uses_who22_commands():
            await self._gateway_handler.send(
                _build_cmd(f"*22*0*{self._who22_addr()}##")
            )
        else:
            await self._gateway_handler.send(OWNSoundCommand.turn_off(self._where))

    async def async_media_next_track(self) -> None:
        """Advance to the next station."""
        if self._uses_who22_commands():
            await self._gateway_handler.send(
                _build_cmd(f"*22*9*{self._who22_addr()}##")
            )
        else:
            await self._gateway_handler.send(
                OWNSoundCommand.next_track(self._where)
            )

    async def async_media_previous_track(self) -> None:
        """Return to the previous station."""
        if self._uses_who22_commands():
            await self._gateway_handler.send(
                _build_cmd(f"*22*10*{self._who22_addr()}##")
            )
        else:
            await self._gateway_handler.send(
                OWNSoundCommand.previous_track(self._where)
            )

    async def async_seek_up(self) -> None:
        """Seek forward to the next receivable FM frequency."""
        if self._uses_who22_commands():
            await self._gateway_handler.send(
                _build_cmd(f"*22*5*{self._who22_addr()}##")
            )
        else:
            await self._gateway_handler.send(
                OWNSoundCommand.seek_up(self._where)
            )

    async def async_seek_down(self) -> None:
        """Seek backward to the previous receivable FM frequency."""
        if self._uses_who22_commands():
            await self._gateway_handler.send(
                _build_cmd(f"*22*6*{self._who22_addr()}##")
            )
        else:
            await self._gateway_handler.send(
                OWNSoundCommand.seek_down(self._where)
            )

    async def async_select_source(self, source: str) -> None:
        """Switch to a stored station.

        Raises:
            HomeAssistantError: If ``source`` is not one of the stored stations.
        """
        station = self._station_number(source)
        if station is None:
            raise HomeAssistantError(
                f"{self.entity_id}: unknown station {source!r}",
                translation_domain=DOMAIN,
                translation_key="unknown_station",
                translation_placeholders={
                    "entity_id": str(self.entity_id), "station": str(source),
                },
            )
        await self.async_select_station(station)

    async def async_select_station(self, station: int) -> None:
        """Switch to stored station ``station`` (1-15)."""
        st = int(station)
        if not 1 <= st <= TUNER_MAX_STATION_COUNT:
            raise HomeAssistantError(
                f"{self.entity_id}: station {station} is outside the valid range (1-{TUNER_MAX_STATION_COUNT})",
                translation_domain=DOMAIN,
                translation_key="unknown_station",
                translation_placeholders={
                    "entity_id": str(self.entity_id),
                    "station": str(station),
                },
            )
        if self._uses_who22_commands():
            await self._gateway_handler.send(
                _build_cmd(f"*#22*{self._who22_addr()}*#6*{st}##")
            )
        else:
            await self._gateway_handler.send(
                OWNSoundCommand.select_track(self._where, st)
            )
        self._station = st
        if st > self._station_count:
            self._station_count = st
        self._attr_source = f"Station {st}"
        self.async_schedule_update_ha_state()

    async def async_store_station(self, station: int) -> None:
        """Memorise the currently tuned frequency into preset ``station`` (1-15)."""
        st = int(station)
        if not 1 <= st <= TUNER_MAX_STATION_COUNT:
            raise HomeAssistantError(
                f"{self.entity_id}: station {station} is outside the valid range (1-{TUNER_MAX_STATION_COUNT})",
                translation_domain=DOMAIN,
                translation_key="unknown_station",
                translation_placeholders={
                    "entity_id": str(self.entity_id),
                    "station": str(station),
                },
            )
        if self._uses_who22_commands():
            cmd = _build_cmd(f"*22*33#{st}*{self._who22_addr()}##")
        else:
            store_fn = getattr(OWNSoundCommand, "store_station", None)
            cmd = (
                store_fn(self._where, st)
                if callable(store_fn)
                else _build_cmd(f"*#16*{self._where}*#10*{st}##")
            )
        await self._gateway_handler.send(cmd)
        self._station = st
        if st > self._station_count:
            self._station_count = st
        self._attr_source = f"Station {st}"
        self.async_schedule_update_ha_state()

    async def async_set_frequency(self, frequency: float) -> None:
        """Tune to ``frequency`` in MHz, e.g. ``105.3``.

        Raises:
            HomeAssistantError: If the frequency is outside the FM band.
        """
        val = float(frequency)
        kilohertz = int(round(val * 1000)) if math.isfinite(val) else 0
        if not FM_MIN_KHZ <= kilohertz <= FM_MAX_KHZ:
            raise HomeAssistantError(
                f"{self.entity_id}: {frequency} MHz is outside the FM band",
                translation_domain=DOMAIN,
                translation_key="frequency_out_of_range",
                translation_placeholders={
                    "entity_id": str(self.entity_id), "frequency": str(frequency),
                },
            )
        if self._uses_who22_commands():
            await self._gateway_handler.send(
                _build_cmd(f"*#22*{self._who22_addr()}*#5*1*{kilohertz // 10}##")
            )
        else:
            await self._gateway_handler.send(
                OWNSoundCommand.set_frequency(self._where, kilohertz)
            )
        self._frequency_khz = kilohertz
        self._station = None
        self._attr_source = None
        self._attr_media_title = None
        self._notify_frequency()
        self.async_schedule_update_ha_state()

    async def async_browse_media(
        self,
        media_content_type: str | None = None,
        media_content_id: str | None = None,
    ) -> BrowseMedia:
        """Expose stored presets and configured station-logo frequencies in Media Browser."""
        children: list[BrowseMedia] = [
            BrowseMedia(
                title=f"Station {n}",
                media_class=MediaClass.CHANNEL,
                media_content_id=str(n),
                media_content_type=MediaType.CHANNEL,
                can_play=True,
                can_expand=False,
            )
            for n in range(1, self._station_count + 1)
        ]
        for khz, url in sorted(self._pictures.items()):
            mhz_str = f"{khz / 1000.0:.2f}"
            children.append(
                BrowseMedia(
                    title=f"{mhz_str} MHz",
                    media_class=MediaClass.CHANNEL,
                    media_content_id=mhz_str,
                    media_content_type=MediaType.CHANNEL,
                    can_play=True,
                    can_expand=False,
                    thumbnail=url,
                )
            )
        return BrowseMedia(
            title=str(getattr(self, "name", None) or "FM Tuner"),
            media_class=MediaClass.DIRECTORY,
            media_content_id="root",
            media_content_type=MediaType.CHANNELS,
            can_play=False,
            can_expand=True,
            children=children,
            children_media_class=MediaClass.CHANNEL,
        )

    async def async_play_media(self, media_type: str, media_id: str, **kwargs: Any) -> None:
        """Tune by station number or by frequency.

        ``media_id`` matching an integer from ``"1"`` up to the available station
        count (1–5 for F500, up to 15 for F500N) selects that stored station;
        anything else is read as a frequency in MHz, so ``"107.0"`` tunes to 107.0 MHz.

        Raises:
            HomeAssistantError: If ``media_id`` is neither.
        """
        candidate = str(media_id).strip()
        if candidate.isdigit() and 1 <= int(candidate) <= self._station_count:
            await self.async_select_station(int(candidate))
            return
        try:
            megahertz = float(candidate)
        except ValueError:
            raise HomeAssistantError(
                f"{self.entity_id}: {media_id!r} is neither a station nor a frequency",
                translation_domain=DOMAIN,
                translation_key="invalid_tuner_media",
                translation_placeholders={
                    "entity_id": str(self.entity_id), "media_id": str(media_id),
                },
            ) from None
        await self.async_set_frequency(megahertz)

    # ── Bus events ────────────────────────────────────────────────────────────

    @callback
    def handle_event(self, message: OWNSoundEvent) -> None:
        """Apply a WHO=16 event addressed to this source device."""
        raw_dim = getattr(message, "dimension", None)
        try:
            dimension = int(raw_dim) if raw_dim is not None else None
        except (ValueError, TypeError):
            dimension = None

        values = [
            str(v)
            for v in (
                getattr(message, "dimension_value", None)
                or getattr(message, "dimension_values", None)
                or []
            )
        ]

        if dimension == 6 and values:
            self._set_frequency_from_bus(values[-1])
        elif dimension in (7, 10) and values:
            self._set_station_from_bus(values[-1])
        elif dimension == 8 and values:
            if len(values) == 8:
                self._attr_media_title = rds_text(values)
            else:
                LOGGER.debug(
                    "%s: ignoring malformed RDS frame with %d values: %s",
                    self.entity_id,
                    len(values),
                    values,
                )
        step_forward = getattr(message, "track_step_forward", None)
        step_backward = getattr(message, "track_step_backward", None)

        if isinstance(step_forward, int):
            if self._station is not None:
                new_st = min(
                    self._station + step_forward, self._station_count
                )
                self._station = new_st
                self._attr_source = f"Station {new_st}"
        elif isinstance(step_backward, int):
            if self._station is not None:
                new_st = max(self._station - step_backward, 1)
                self._station = new_st
                self._attr_source = f"Station {new_st}"
        elif getattr(message, "is_on", False):
            self._attr_state = MediaPlayerState.ON
        elif getattr(message, "is_off", False):
            self._attr_state = MediaPlayerState.OFF
            # A tuner in standby is not listening to anything.
            self._attr_media_title = None

        self._publish_state()

    def _set_frequency_from_bus(self, raw: str) -> None:
        """Record a reported frequency, ignoring a payload that cannot be one."""
        if not raw.isdigit():
            return
        kilohertz = int(raw)
        if FM_MIN_KHZ <= kilohertz <= FM_MAX_KHZ:
            self._apply_frequency(kilohertz)
        else:
            LOGGER.debug(
                "%s: ignoring reported frequency %s kHz, outside the FM band",
                self.entity_id,
                kilohertz,
            )

    def _apply_frequency(self, kilohertz: int) -> None:
        """Adopt a frequency; a new one drops the station and text of the old."""
        if self._frequency_khz != kilohertz:
            self._frequency_khz = kilohertz
            self._station = None
            self._attr_source = None
            self._attr_media_title = None
            self._notify_frequency()

    @callback
    def handle_who22_event(self, message: Any) -> None:
        """Apply a WHO=22 report of this tuner (source address ``5#2#S`` / ``2#S``).

        Dual-stack gateways (MH200N, F454, F453AV, H/L4684 FW >= 6.0) run both
        ``bt_difson`` (WHO=16) and ``bt_multi`` (WHO=22), emitting both families
        in non-deterministic order for every SCS tuner frame; each update is
        idempotent, so the repeat changes nothing. On WHO=22-only gateways
        (MyHomeServer1, F459, F460, F461), ``bt_multi`` is the sole translator
        of the tuner's power state, frequency, stored preset and RDS text.
        """
        raw_dim = getattr(message, "dimension", None)
        try:
            dimension = int(raw_dim) if raw_dim is not None else None
        except (ValueError, TypeError):
            return
        values = [
            str(v)
            for v in (
                getattr(message, "dimension_value", None)
                or getattr(message, "dimension_values", None)
                or []
            )
        ]

        raw_what = getattr(message, "what", None)
        what_base = str(raw_what).split("#", 1)[0] if isinstance(raw_what, (str, int)) else ""
        is_on = (
            getattr(message, "is_on", False) is True
            or what_base in ("1", "2")
            or (dimension == 12 and len(values) >= 1 and values[0] == "1")
        )
        is_off = (
            getattr(message, "is_off", False) is True
            or what_base == "0"
            or (dimension == 12 and len(values) >= 1 and values[0] == "0")
        )
        if is_on:
            self._attr_state = MediaPlayerState.ON
        elif is_off:
            self._attr_state = MediaPlayerState.OFF
            self._attr_media_title = None

        report = decode_who22_tuner(dimension, values) if dimension is not None else None
        if report is None and not is_on and not is_off:
            return
        if report is not None:
            if report.frequency_khz is not None:
                self._apply_frequency(report.frequency_khz)
                if dimension == 11 and report.preset is None:
                    self._station = None
                    self._attr_source = None
            if report.preset is not None:
                self._set_station_from_bus(str(report.preset))
            if report.rds is not None or report.rds_blank:
                self._attr_media_title = report.rds
        self._publish_state()

    def _set_station_from_bus(self, raw: str) -> None:
        """Record a reported stored station."""
        if not raw.isdigit():
            return
        station = int(raw)
        if 1 <= station <= TUNER_MAX_STATION_COUNT:
            self._station = station
            if station > self._station_count:
                self._station_count = station
            self._attr_source = f"Station {station}"

    # ── Helpers ───────────────────────────────────────────────────────────────

    def _station_number(self, source: str) -> int | None:
        """Resolve a station label such as ``"Station 3"`` to its number."""
        prefix = "Station "
        if source.startswith(prefix):
            candidate = source[len(prefix):].strip()
            if candidate.isdigit() and 1 <= int(candidate) <= self._station_count:
                return int(candidate)
        return None


def who22_source_id(message: Any) -> int | None:
    """Return the matrix source a WHO=22 frame is about, or ``None``.

    A tuner is addressed ``5#2#S`` in its own reports and ``2#S`` in queries
    and their replies. Prefers the number OWNd has already worked out and
    falls back to reading the address, so a frame is placed the same way
    whichever OWNd version parsed it.
    """
    if str(getattr(message, "who", "")) != "22":
        return None
    known = getattr(message, "source_id", None)
    if isinstance(known, int):
        return known
    where = getattr(message, "where", None)
    if where is None:
        return None
    param = getattr(message, "where_param", None)
    if param is None:
        param = getattr(message, "_where_param", None)
    parts = [str(where), *(str(p) for p in (param or []))]
    if len(parts) == 2 and parts[0] == "2" and parts[1].isdigit():
        return int(parts[1])
    if len(parts) == 3 and parts[0] == "5" and parts[1] == "2" and parts[2].isdigit():
        return int(parts[2])
    return None


def who22_tuner_listener(sources: list[MyHOMESoundSource]) -> Callable[[Any], None]:
    """Build the gateway-message listener that feeds WHO=22 frames to tuners.

    Only declared tuners ever receive them: WHO=22 frames never create
    entities, so a plant gains no phantom device from this traffic.
    """
    by_source = {int(source._where) - 100: source for source in sources}

    @callback
    def listener(message: Any) -> None:
        source_id = who22_source_id(message)
        if source_id is None:
            return
        source = by_source.get(source_id)
        if source is not None:
            source.handle_who22_event(message)

    return listener
