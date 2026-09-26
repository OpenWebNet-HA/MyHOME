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
module follows the examples, as the Encyclopedia does.

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

from typing import TYPE_CHECKING, Any

from homeassistant.components.media_player import (
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
from OWNd.message import OWNSoundCommand, OWNSoundEvent

from .const import DOMAIN, LOGGER, TUNER_MAX_STATION_COUNT, TUNER_STATION_COUNT
from .myhome_device import MyHOMEEntity

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant

    from .gateway import MyHOMEGatewayHandler

#: Lowest and highest FM frequency accepted, in kHz. Outside this the value is
#: almost certainly a mistake (a preset number, or MHz passed as kHz).
FM_MIN_KHZ = 87500
FM_MAX_KHZ = 108000


def source_address(source: int) -> str:
    """Return the bus address of source device ``source`` (1-9)."""
    return str(100 + int(source))


def rds_text(values: list[str] | tuple[str, ...]) -> str | None:
    """Decode an RDS dimension payload into readable text.

    The payload is eight decimal ASCII codes rather than characters. Codes
    outside the printable range are dropped instead of rendering control
    characters into the media title.
    """
    chars = [
        chr(int(value))
        for value in values
        if str(value).isdigit() and 32 <= int(value) <= 126
    ]
    text = "".join(chars).strip()
    return text or None


class MyHOMESoundSource(MyHOMEEntity, MediaPlayerEntity):
    """A WHO=16 tuner source device.

    Presets are exposed as the entity's source list, because that is what a
    listener picks. The frequency is an attribute rather than a source, since
    it is continuous.
    """

    _attr_device_class = MediaPlayerDeviceClass.RECEIVER
    _attr_supported_features = (
        MediaPlayerEntityFeature.TURN_ON
        | MediaPlayerEntityFeature.TURN_OFF
        | MediaPlayerEntityFeature.NEXT_TRACK
        | MediaPlayerEntityFeature.PREVIOUS_TRACK
        | MediaPlayerEntityFeature.SELECT_SOURCE
        | MediaPlayerEntityFeature.PLAY_MEDIA
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
    ) -> None:
        """Initialise a tuner source entity."""
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
    def extra_state_attributes(self) -> dict[str, Any]:
        """Expose the tuning state that has no standard media_player attribute."""
        attributes: dict[str, Any] = {}
        if self._frequency_khz is not None:
            attributes["frequency"] = round(self._frequency_khz / 1000.0, 2)
        if self._station is not None:
            attributes["station"] = self._station
        return attributes

    # ── Lifecycle ─────────────────────────────────────────────────────────────

    async def async_added_to_hass(self) -> None:
        """Register listeners and ask the tuner to report RDS.

        A tuner does not broadcast its RDS text until asked (`WHAT` 101), so
        without this the media title stays empty. Gateways that do not support
        it answer NACK, which costs nothing.
        """
        self._register_availability_listener()
        await self._gateway_handler.send(
            OWNSoundCommand(f"*16*101*{self._where}##")
        )

    async def async_update(self) -> None:
        """Request the tuner's frequency, station and RDS text."""
        for dimension in (6, 7, 8):
            await self._gateway_handler.send_status_request(
                OWNSoundCommand(f"*#16*{self._where}*{dimension}##")
            )

    # ── Commands ──────────────────────────────────────────────────────────────

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Switch the source device on."""
        await self._gateway_handler.send(OWNSoundCommand(f"*16*3*{self._where}##"))

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Switch the source device to standby.

        Rooms listening to this input fall silent; the matrix routing is not
        changed, so they stay pointed at it.
        """
        await self._gateway_handler.send(OWNSoundCommand(f"*16*13*{self._where}##"))

    async def async_media_next_track(self) -> None:
        """Advance to the next station."""
        await self._gateway_handler.send(OWNSoundCommand(f"*16*6001*{self._where}##"))

    async def async_media_previous_track(self) -> None:
        """Return to the previous station."""
        await self._gateway_handler.send(OWNSoundCommand(f"*16*6101*{self._where}##"))

    async def async_seek_up(self) -> None:
        """Seek forward to the next receivable FM frequency."""
        await self._gateway_handler.send(OWNSoundCommand(f"*16*5000*{self._where}##"))

    async def async_seek_down(self) -> None:
        """Seek backward to the previous receivable FM frequency."""
        await self._gateway_handler.send(OWNSoundCommand(f"*16*5100*{self._where}##"))

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
        if not 1 <= int(station) <= TUNER_MAX_STATION_COUNT:
            raise HomeAssistantError(
                f"{self.entity_id}: station {station} is outside the valid range (1-{TUNER_MAX_STATION_COUNT})",
                translation_domain=DOMAIN,
                translation_key="unknown_station",
                translation_placeholders={
                    "entity_id": str(self.entity_id),
                    "station": str(station),
                },
            )
        await self._gateway_handler.send(
            OWNSoundCommand(f"*#16*{self._where}*#7*{station}##")
        )
        self._station = station
        if station > self._station_count:
            self._station_count = station
        self._attr_source = f"Station {station}"
        self.async_schedule_update_ha_state()

    async def async_set_frequency(self, megahertz: float) -> None:
        """Tune to ``megahertz``, e.g. ``107.0``.

        Raises:
            HomeAssistantError: If the frequency is outside the FM band.
        """
        kilohertz = int(round(float(megahertz) * 1000))
        if not FM_MIN_KHZ <= kilohertz <= FM_MAX_KHZ:
            raise HomeAssistantError(
                f"{self.entity_id}: {megahertz} MHz is outside the FM band",
                translation_domain=DOMAIN,
                translation_key="frequency_out_of_range",
                translation_placeholders={
                    "entity_id": str(self.entity_id), "frequency": str(megahertz),
                },
            )
        await self._gateway_handler.send(
            OWNSoundCommand(f"*#16*{self._where}*#6*0*{kilohertz:06d}##")
        )
        self._frequency_khz = kilohertz
        self._station = None
        self._attr_source = None
        self.async_schedule_update_ha_state()

    async def async_play_media(self, media_type: str, media_id: str, **kwargs: Any) -> None:
        """Tune by station number or by frequency.

        ``media_id`` of ``"1"`` to ``"5"`` selects that stored station; anything
        else is read as a frequency in MHz, so ``"107.0"`` tunes to 107.0 MHz.

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
        dimension = getattr(message, "dimension", None)
        values = [str(v) for v in (getattr(message, "dimension_value", None) or [])]

        if dimension == 6 and values:
            self._set_frequency_from_bus(values[-1])
        elif dimension == 7 and values:
            self._set_station_from_bus(values[-1])
        elif dimension == 8 and values:
            self._attr_media_title = rds_text(values)
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
            if self._frequency_khz != kilohertz:
                self._frequency_khz = kilohertz
                self._station = None
                self._attr_source = None
        else:
            LOGGER.debug(
                "%s: ignoring reported frequency %s kHz, outside the FM band",
                self.entity_id,
                kilohertz,
            )

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

    def _publish_state(self) -> None:
        """Push the current state to Home Assistant."""
        self.async_schedule_update_ha_state()

    # ── Helpers ───────────────────────────────────────────────────────────────

    def _station_number(self, source: str) -> int | None:
        """Resolve a station label such as ``"Station 3"`` to its number."""
        prefix = "Station "
        if source.startswith(prefix):
            candidate = source[len(prefix):].strip()
            if candidate.isdigit() and 1 <= int(candidate) <= self._station_count:
                return int(candidate)
        return None
