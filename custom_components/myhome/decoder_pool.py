"""Decoder pool manager for the MyHOME Dynamic Proxy.

Manages a pool of streaming decoders (squeezelite / Cambridge Audio) that are
physically connected to the BTicino F441M matrix source inputs.  The proxy
intercepts Music Assistant / Spotify play_media commands, claims an idle decoder
from this pool, routes the BTicino matrix to the correct source input, and
forwards the stream URL to the backend decoder.

Architecture
------------
- One ``DecoderPool`` instance per gateway, keyed by MAC address in
  ``hass.data[DOMAIN][mac]["decoder_pool"]``.
- Survives entity reloads (lives in hass.data, not inside an entity).
- Thread-safe: all claim/release operations are serialised with a single
  ``asyncio.Lock`` to prevent race conditions when multiple zones compete for
  the last available decoder.
- State-aware: inspects the live HA entity state of each decoder to determine
  whether it is truly idle before claiming.

Gain staging (anti-hiss)
------------------------
Each decoder carries an optional ``pre_gain`` offset (0–50 %).  When the user
adjusts the BTicino zone volume the proxy also sets the decoder volume to
``zone_volume + pre_gain``, capped at 1.0.  This keeps the analog signal level
high and the BTicino amplifier gain low, which reduces the inherent noise floor
of the 2-wire bus.

Typical values
--------------
- Cambridge Audio with Pre-Amp OFF: ``pre_gain = 0`` (already at full line level)
- Squeezelite / piCorePlayer:       ``pre_gain = 20``
"""
import asyncio

from homeassistant.components.media_player import MediaPlayerState
from homeassistant.core import HomeAssistant

from .const import LOGGER


class DecoderPool:
    """Thread-safe pool of streaming decoders mapped to BTicino source inputs.

    Each decoder (squeezelite, Cambridge Audio, etc.) is physically connected
    to one of the 4 BTicino source inputs.  This class handles:

    - Thread-safe allocation via ``asyncio.Lock``
    - State-aware idle detection (inspects live HA entity state)
    - Pre-gain configuration per decoder (gain staging / anti-hiss)
    - Graceful release on zone turn-off or options reload
    """

    # HA states that mean "this decoder is available for claiming".
    # UNAVAILABLE is intentionally excluded: treat an offline Cambridge as busy
    # rather than risking a claim on a device that cannot actually play.
    _IDLE_STATES: frozenset = frozenset({
        MediaPlayerState.IDLE,
        MediaPlayerState.OFF,
        None,  # entity not yet registered / state unknown
    })

    def __init__(
        self,
        hass: HomeAssistant,
        decoder_map: dict[str, int],
        pre_gain_map: dict[str, int] | None = None,
    ) -> None:
        """Initialise the decoder pool.

        Args:
            hass: Home Assistant instance (used to read entity states).
            decoder_map: Mapping of ``{entity_id: source_num (int)}``, e.g.::

                {
                    "media_player.cambridge_audio_cxn": 1,
                    "media_player.hifiberry_zone": 2,
                }

                The source number tells the integration which physical F441M input
                the decoder is wired to. For normal streaming the integration activates
                the zone with a simple OFF→ON sequence and trusts the matrix routing
                (set physically or by gateway scenario). The number is available if
                explicit routing commands are ever needed.

            pre_gain_map: Optional mapping of ``{entity_id: pre_gain_pct}``
                where ``pre_gain_pct`` is an integer between 0 and 50.
                Defaults to 0 for any decoder not listed.

        Example::

            pool = DecoderPool(
                hass,
                decoder_map={"media_player.cambridge_audio_cxn": 1},
                pre_gain_map={"media_player.cambridge_audio_cxn": 0},
            )
        """
        self._hass = hass
        self._decoder_map: dict[str, int] = decoder_map          # entity_id → source_num
        self._pre_gain_map: dict[str, int] = pre_gain_map or {}  # entity_id → pre_gain %
        self._assignments: dict[str, str | None] = {             # entity_id → zone_entity_id or None
            entity_id: None for entity_id in decoder_map
        }
        self._lock = asyncio.Lock()

    # ── Public API ────────────────────────────────────────────────────────────

    @property
    def is_configured(self) -> bool:
        """Return ``True`` if at least one decoder has been mapped."""
        return len(self._decoder_map) > 0

    async def claim(self, zone_entity_id: str) -> tuple[str, int] | None:
        """Claim an idle decoder for *zone_entity_id*.

        Thread-safe: uses ``asyncio.Lock`` to prevent two zones from claiming
        the same decoder simultaneously.

        If *zone_entity_id* already owns a decoder (e.g. song change), the
        existing assignment is returned immediately without re-locking.

        Args:
            zone_entity_id: The ``entity_id`` of the BTicino zone requesting
                a decoder (e.g. ``"media_player.audio_zone_3"``).

        Returns:
            ``(decoder_entity_id, source_num: int)`` if an idle decoder was
            found and claimed, or ``None`` if all decoders are busy.

        Example::

            result = await pool.claim("media_player.audio_zone_3")
            if result is None:
                raise HomeAssistantError("All inputs are busy!")
            decoder_id, source_num = result
        """
        async with self._lock:
            # If this zone already owns a decoder, reuse it (idempotent).
            for dec_id, owner in self._assignments.items():
                if owner == zone_entity_id:
                    LOGGER.debug("Decoder %s already claimed by %s", dec_id, zone_entity_id)
                    return (dec_id, self._decoder_map[dec_id])

            # Find the first decoder that is unassigned AND idle.
            for dec_id, owner in self._assignments.items():
                if owner is not None:
                    continue  # already in use by another zone

                state = self._hass.states.get(dec_id)
                state_val = state.state if state else None

                if state_val in self._IDLE_STATES:
                    self._assignments[dec_id] = zone_entity_id
                    LOGGER.info(
                        "DecoderPool: %s claimed by zone %s (source %s)",
                        dec_id,
                        zone_entity_id,
                        self._decoder_map[dec_id],
                    )
                    return (dec_id, self._decoder_map[dec_id])

            # All decoders are busy.
            LOGGER.warning(
                "DecoderPool: all decoders busy — zone %s cannot play", zone_entity_id
            )
            return None

    async def release(self, zone_entity_id: str) -> str | None:
        """Release the decoder assigned to *zone_entity_id*.

        Args:
            zone_entity_id: The ``entity_id`` of the BTicino zone releasing
                its decoder.

        Returns:
            The freed ``decoder_entity_id``, or ``None`` if the zone had no
            active assignment.

        Example::

            freed = await pool.release("media_player.audio_zone_3")
        """
        async with self._lock:
            for dec_id, owner in self._assignments.items():
                if owner == zone_entity_id:
                    self._assignments[dec_id] = None
                    LOGGER.info(
                        "DecoderPool: %s released by zone %s",
                        dec_id,
                        zone_entity_id,
                    )
                    return dec_id
        return None

    async def release_all(self) -> None:
        """Release every decoder assignment.

        Called when the user updates options via the UI so that the pool can be
        rebuilt cleanly.  Any zone currently streaming will lose its decoder;
        the user must re-trigger playback.

        Example::

            await pool.release_all()
        """
        async with self._lock:
            for dec_id in self._assignments:
                self._assignments[dec_id] = None
            LOGGER.info("DecoderPool: all assignments released (options reload)")

    def get_assignment(self, zone_entity_id: str) -> str | None:
        """Return the decoder entity_id assigned to *zone_entity_id*, or ``None``.

        Lock-free read — safe because ``_assignments`` mutations only happen
        inside the asyncio event loop under the lock.

        Args:
            zone_entity_id: The ``entity_id`` of the zone to query.

        Returns:
            The ``decoder_entity_id`` currently assigned to the zone, or
            ``None`` if the zone has no active decoder.
        """
        for dec_id, owner in self._assignments.items():
            if owner == zone_entity_id:
                return dec_id
        return None

    def get_pre_gain(self, decoder_entity_id: str) -> int:
        """Return the configured pre-gain offset for *decoder_entity_id*.

        Args:
            decoder_entity_id: The ``entity_id`` of the decoder.

        Returns:
            The pre-gain percent (0–50).  Defaults to ``0`` if not configured.

        Example::

            gain_pct = pool.get_pre_gain("media_player.cambridge_audio_cxn")
            decoder_volume = min(1.0, zone_volume + gain_pct / 100.0)
        """
        return self._pre_gain_map.get(decoder_entity_id, 0)

    # ── Introspection (for listeners and tests) ───────────────────────────────

    @property
    def decoder_entity_ids(self) -> list[str]:
        """Return all configured decoder entity IDs."""
        return list(self._decoder_map.keys())

    def __repr__(self) -> str:  # pragma: no cover
        busy = sum(1 for v in self._assignments.values() if v is not None)
        return (
            f"<DecoderPool decoders={len(self._decoder_map)} "
            f"busy={busy}/{len(self._decoder_map)}>"
        )
