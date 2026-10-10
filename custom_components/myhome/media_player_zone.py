"""State and pool access shared by the layers of a MyHOME audio zone entity.

``MyHOMEMediaPlayer`` is one class cut into layers, each in its own module and
each extending the one below it: :class:`ZoneBase` (this module) holds the
state every layer reads, ``media_player_source`` the source and matrix routing,
``media_player_decoder`` the mirroring of the decoder's state and the anti-hiss
auto-off, and ``media_player_group`` multi-room grouping.  ``media_player`` adds
the Home Assistant service entry points on top.  The layers are not
independent mixins: each needs the ones below it, and they reach each other
through ``self``.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from typing import TYPE_CHECKING

from homeassistant.components.media_player import (  # type: ignore[attr-defined, unused-ignore]
    MediaPlayerDeviceClass,
    MediaPlayerEntity,
)
from homeassistant.components.media_player.const import MediaPlayerEntityFeature, MediaPlayerState
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er

from .data import MyHOMERuntimeData, get_runtime_data
from .decoder_pool import DecoderPool
from .myhome_device import MyHOMEEntity

if TYPE_CHECKING:
    from .gateway import MyHOMEGatewayHandler


class ZoneBase(MyHOMEEntity, MediaPlayerEntity):
    """State of one audio zone, shared by every layer of the entity."""

    # Audio zones are amplified speaker outputs of the SCS sound system.
    _attr_device_class = MediaPlayerDeviceClass.SPEAKER

    def __init__(
        self,
        hass: HomeAssistant,
        name: str,
        entity_name: str | None,
        device_id: str,
        who: str,
        where: str,
        manufacturer: str,
        model: str,
        gateway: MyHOMEGatewayHandler,
    ) -> None:
        """Initialise the MyHOME media player entity."""
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

        # ── Base hardware state ────────────────────────────────────────────
        self._attr_state: MediaPlayerState | None = MediaPlayerState.OFF
        self._attr_source: str | None = None
        self._warned_sources: set[int] = set()
        self._attr_volume_level: float | None = None
        self._attr_is_volume_muted: bool = False

        # ── Proxy state ────────────────────────────────────────────────────
        self._active_decoder: str | None = None  # entity_id of the claimed decoder
        self._syncing_volume: bool = False  # guard flag — prevents volume feedback loop
        self._pre_mute_volume: float | None = None  # volume to restore on unmute
        self._turning_off: bool = False  # guard flag — dampens bus-OFF echo loops
        self._parked: bool = False  # amplifier off by anti-hiss, group kept in the books
        self._wake_pending: bool = False  # a parked room whose wake-up has been asked for
        self._wake_off_sent_at: float | None = None  # monotonic time of the wake sequence's OFF
        self._unsub_decoders: Callable[[], None] | None = None  # decoder state watch
        self._auto_off_unsub: Callable[[], None] | None = None  # auto-off when decoder stops (anti-hiss)
        self._auto_off_key: tuple[float, str | None] | None = None  # (delay, decoder) of that timer
        self._companion_cache: dict[str, str] = {}  # cached decoder_id -> companion_id mapping
        self._pending_off_task: asyncio.Task[None] | None = None  # grace-period group-leave OFF
        self._status_seen: bool = False  # first bus status report received since being added
        self._auto_joining: bool = False  # guard flag — prevents overlapping auto-join runs

        # ── Base hardware features (always available) ──────────────────────
        self._attr_supported_features = (
            MediaPlayerEntityFeature.TURN_ON
            | MediaPlayerEntityFeature.TURN_OFF
            | MediaPlayerEntityFeature.VOLUME_STEP
            | MediaPlayerEntityFeature.VOLUME_SET
            | MediaPlayerEntityFeature.VOLUME_MUTE
            | MediaPlayerEntityFeature.SELECT_SOURCE
            | MediaPlayerEntityFeature.GROUPING
        )

    @property
    def active_decoder(self) -> str | None:
        """Return the active decoder entity ID claimed by this zone, if any."""
        return self._active_decoder

    @property
    def where(self) -> str:
        """Return the zone OpenWebNet address."""
        return self._where

    @property
    def _runtime_data(self) -> MyHOMERuntimeData | None:
        """Return the runtime data for this gateway entry."""
        entry = getattr(getattr(self, "platform", None), "config_entry", None)
        return get_runtime_data(entry) if entry is not None else None

    def _get_pool(self) -> DecoderPool | None:
        """Return the shared :class:`DecoderPool` from the entry's runtime data.

        Returns ``None`` if the pool has not yet been initialised (e.g.
        during early startup) or if no decoders are configured.
        """
        entry = getattr(getattr(self, "platform", None), "config_entry", None)
        runtime = get_runtime_data(entry) if entry is not None else None
        return runtime.decoder_pool if runtime is not None else None

    def _streaming_target(self, decoder_id: str | None) -> str | None:
        """Return the streaming decoder target (companion if present, else decoder_id)."""
        if not decoder_id:
            return None
        if hasattr(self, "_companion_cache") and self._companion_cache:
            return self._companion_cache.get(decoder_id, decoder_id)
        pool = self._get_pool()
        if pool and hasattr(pool, "companion_map") and isinstance(pool.companion_map, dict):
            return pool.companion_map.get(decoder_id, decoder_id)
        return decoder_id

    def _decoder_platform(self, decoder_id: str) -> str | None:
        """Return the integration providing ``decoder_id``, from the entity registry."""
        reg_entry = er.async_get(self.hass).async_get(decoder_id)
        return reg_entry.platform if reg_entry else None

    def _write_zone_state(self, entity_id: str | None) -> None:
        """Republish another zone of this gateway, e.g. after its group changed."""
        runtime = self._runtime_data
        zone = runtime.media_players.get(entity_id) if runtime and entity_id else None
        if zone is not None and zone is not self:
            zone.async_write_ha_state()

    # ── Hooks implemented further up the chain ────────────────────────────────
    # A lower layer calls these; the layer that owns them sits above it, so they
    # are declared here where every layer can see them. mypy checks each override
    # against the signature below, and a class built without its upper layers
    # fails with a clear error rather than an AttributeError.

    async def _async_park_group(self) -> None:
        """Switch a leader's and its members' amplifiers off but keep the group (group layer)."""
        raise NotImplementedError

    async def _async_wake_zone(self) -> None:
        """Wake the amplifier with the OFF -> ON sequence (entity)."""
        raise NotImplementedError

    async def _async_handle_turn_off(self, from_bus: bool = False) -> None:
        """Coordinated turn-off of a zone, its group and its decoder (entity)."""
        raise NotImplementedError

    async def _async_auto_join_active_stream(self) -> None:
        """Auto-join an active streaming group when this room turns on or adjusts volume (group layer)."""
        raise NotImplementedError
