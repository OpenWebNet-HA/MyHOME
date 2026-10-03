"""Source names and matrix routing of a MyHOME audio zone."""

from __future__ import annotations

import time
from typing import Any

from homeassistant.exceptions import HomeAssistantError
from OWNd.message import OWNSoundCommand

from .const import (
    CONF_SOURCE_DEFAULTS,
    CONF_SOURCE_NAME,
    CONF_SOURCE_SLOTS,
    DOMAIN,
    LOGGER,
    SOURCE_UNCONFIGURED_SUFFIX,
)
from .media_player_routing import routing_address, zone_environment
from .media_player_zone import ZoneBase

# Music Assistant turns on and joins the rooms of a group one call at a time. A routing
# frame is not sent again while the previous copy is younger than this (seconds); a
# skipped repeat refreshes the stamp, so a burst with gaps shorter than this stays merged.
_ROUTE_REPEAT_WINDOW = 8.0


class ZoneSourceLayer(ZoneBase):
    """Which matrix input a zone listens to, and how it is switched."""

    def _options(self) -> dict[str, Any]:
        """Return the config entry options, or an empty mapping when unavailable."""
        entry = getattr(getattr(self, "platform", None), "config_entry", None)
        return dict(getattr(entry, "options", None) or {})

    def _source_names(self) -> dict[int, str]:
        """Return ``{source_number: name}`` for every source the user configured.

        An empty mapping means the installation has not been described yet; the
        entity then falls back to the legacy ``Source N`` labels and assumes
        nothing about which matrix inputs are wired.
        """
        options = self._options()
        names: dict[int, str] = {}
        for i in range(1, CONF_SOURCE_SLOTS + 1):
            name = str(options.get(CONF_SOURCE_NAME.format(i), "") or "").strip()
            if name:
                names[i] = name
        return names

    def _source_label(self, source_num: int) -> str:
        """Return the label to show for ``source_num``.

        Once the user has named their sources, a zone routed to an input that
        was left blank is labelled as unconfigured rather than as a plausible
        looking "Source N" — a wall panel can route a room to an input that has
        nothing wired to it, and the resulting silence or hiss should be
        visible in Home Assistant instead of unexplained.
        """
        names = self._source_names()
        if source_num in names:
            return names[source_num]
        if names:
            return f"Source {source_num}{SOURCE_UNCONFIGURED_SUFFIX}"
        return f"Source {source_num}"

    def _warn_unconfigured_source(self, source_num: int) -> None:
        """Log once when this zone is routed to an input that has no source.

        A wall panel can route a room to a matrix input that nothing is wired
        to; the room then plays silence or amplified noise with no indication
        of why.  The integration deliberately does not "fix" this — the user
        made that choice at the panel — but it does say so, once per source,
        so the cause is findable.
        """
        names = self._source_names()
        if not names or source_num in names:
            return
        if source_num in self._warned_sources:
            return
        self._warned_sources.add(source_num)
        LOGGER.warning(
            "%s: routed to matrix source %d, which is not configured in the "
            "MyHOME options. If nothing is wired to that input the zone will "
            "play silence or noise. Select a configured source, or name this "
            "input in the integration options if it does exist.",
            self.entity_id,
            source_num,
        )

    def _source_number(self, source: str) -> int | None:
        """Resolve a source label back to its BTicino source number.

        Once sources are named only those names resolve, so an input left
        blank — nothing wired to it — cannot be selected under its legacy
        ``Source N`` label either.
        """
        names = self._source_names()
        if names:
            for number, name in names.items():
                if name == source:
                    return number
            return None
        prefix = "Source "
        if source.startswith(prefix):
            candidate = source[len(prefix) :]
            if candidate.isdigit() and 1 <= int(candidate) <= CONF_SOURCE_SLOTS:
                return int(candidate)
        return None

    def _default_source(self) -> int | None:
        """Return the source this zone's environment should default to.

        Configured per environment rather than per zone: the matrix routes per
        output and an output serves a whole environment, so two amplifiers in
        the same room cannot sit on different inputs. ``None`` means "leave the
        routing alone", which is the default.
        """
        defaults = self._options().get(CONF_SOURCE_DEFAULTS) or {}
        environment = zone_environment(self._where)
        if not isinstance(defaults, dict) or environment is None:
            return None
        value = defaults.get(environment)
        try:
            source = int(value)  # type: ignore[arg-type]
        except (TypeError, ValueError):
            return None
        return source if 1 <= source <= CONF_SOURCE_SLOTS else None

    def _routing_configured(self) -> bool:
        """Return ``True`` once the user has described the matrix in the options.

        Naming a source or setting an environment default is the opt-in for
        automatic routing.  Until then the integration keeps its original
        behaviour and trusts the routing set at the wall panels, so upgrading
        does not start switching rooms on decoder slot numbers nobody checked.
        """
        return bool(self._source_names()) or self._default_source() is not None

    def _routing_frames(self, source_num: int) -> list[str] | None:
        """Return the activate + route frames for ``source_num``.

        ``None`` when no valid pair exists: the source is outside S1-S4 (for
        instance a decoder slot saved as ``0`` by an older options form), or
        the zone has no routing address (see :func:`routing_address`).
        """
        if not 1 <= source_num <= CONF_SOURCE_SLOTS:
            return None
        route = routing_address(self._where, source_num)
        if route is None:
            return None
        return [f"*16*3*{100 + source_num}##", f"*16*3*{route}##"]

    async def _route_to(
        self, source_num: int, sent: set[str] | None = None, *, coalesce: bool = False
    ) -> bool:
        """Send the routing frames for ``source_num``; ``False`` if impossible.

        ``sent`` collects the frames already sent while waking a group. The
        source-on frame is the same for every room and the route is the same
        for every room of an environment, and each frame costs a full gateway
        round trip (~0.8 s on the MH200), so a frame is sent once per group.
        ``coalesce`` (implied by ``sent``) also skips a frame the gateway was given
        within ``_ROUTE_REPEAT_WINDOW`` by an earlier call, whichever zone sent it.
        """
        frames = self._routing_frames(source_num)
        if frames is None:
            LOGGER.warning(
                "%s: cannot route amplifier %s to matrix source %s; leaving the routing unchanged",
                self.entity_id,
                self._where,
                source_num,
            )
            return False
        for frame in frames:
            if sent is not None:
                if frame in sent:
                    continue
                sent.add(frame)
            if (coalesce or sent is not None) and self._runtime_data is not None:
                recent = self._runtime_data.routing_recent
                now = time.monotonic()
                last = recent.get(frame)
                recent[frame] = now
                if last is not None and now - last < _ROUTE_REPEAT_WINDOW:
                    continue
            await self._gateway_handler.send(OWNSoundCommand(frame))
        self._attr_source = self._source_label(source_num)
        return True

    def _environment_streamer(self) -> str | None:
        """Return another zone that streams from a decoder in this environment."""
        pool = self._get_pool()
        environment = zone_environment(self._where)
        if pool is None or environment is None:
            return None
        return pool.environment_owner(environment, exclude=self.entity_id)

    def _environment_busy_error(self, owner: str, environment: str) -> HomeAssistantError:
        """Build the refusal for a zone whose environment already streams elsewhere.

        Music Assistant only shows this text, so it names the rooms instead of
        talking about entity ids and matrix inputs: the zones of one environment
        hang off one matrix output and cannot hear two different streams.
        """

        def label(entity_id: str) -> str:
            state = self.hass.states.get(entity_id)
            return str(state.attributes.get("friendly_name") or entity_id) if state else entity_id

        runtime = self._runtime_data
        sharing = sorted(
            {
                label(entity_id)
                for entity_id, zone in (runtime.media_players.items() if runtime else ())
                if entity_id not in (self.entity_id, owner)
                and zone_environment(getattr(zone, "_where", "")) == environment
            }
        )
        rooms = ", ".join(sharing) if sharing else "no other room"
        return HomeAssistantError(
            f"{self.entity_id}: {label(owner)} is already streaming in environment "
            f"{environment}, and zones in one environment share a matrix input "
            f"(also on it: {rooms})",
            translation_domain=DOMAIN,
            translation_key="environment_busy",
            translation_placeholders={
                "entity_id": str(self.entity_id),
                "owner": owner,
                "owner_name": label(owner),
                "environment": environment,
                "rooms": rooms,
            },
        )

    async def _apply_default_source(self) -> None:
        """Route this zone's environment to its default source, if one is set.

        Only called when the zone is switched on from Home Assistant. Routing
        announced by a wall panel is left untouched — see
        :func:`_warn_unconfigured_source` — and so is an environment where
        another zone is streaming: the route is shared, and switching it would
        take that zone off its stream.
        """
        target = self._default_source()
        if target is None:
            return
        streamer = self._environment_streamer()
        if streamer is not None:
            LOGGER.info(
                "%s: not applying default source %d, %s is streaming in the same environment",
                self.entity_id,
                target,
                streamer,
            )
            return
        await self._route_to(target, coalesce=True)

    @property
    def source_list(self) -> list[str]:
        """Return the sources that can be selected.

        Once sources are named in the options only those are offered: an input
        with nothing wired to it is not a valid destination, and offering it
        would let the user route a room to silence or tuner hiss.  Without any
        configuration the legacy ``Source 1..4`` list is returned unchanged.
        """
        names = self._source_names()
        if names:
            return [names[number] for number in sorted(names)]
        return [f"Source {number}" for number in range(1, CONF_SOURCE_SLOTS + 1)]

    async def async_select_source(self, source: str) -> None:
        """Route this zone's environment to ``source``.

        Two frames are sent, the same pair a wall panel puts on the bus:
        ``*16*3*10S##`` activates the source device and ``*16*3*1ES##`` routes
        environment ``E`` to it.  The F441M switches per output, so every
        amplifier sharing this zone's environment follows along — that is
        matrix hardware behaviour, not a limitation of this integration.
        For the same reason the switch is refused while another zone of the
        environment streams from a decoder: it would take that zone off its
        stream while Home Assistant still showed it playing.

        Raises:
            HomeAssistantError: If ``source`` is not a known source label, the
                zone has no routing address (environment 0, or not a two-digit
                amplifier), or another zone of the environment is streaming.
        """
        source_num = self._source_number(source)
        if source_num is None:
            raise HomeAssistantError(
                f'{self.entity_id}: unknown source "{source}"',
                translation_domain=DOMAIN,
                translation_key="unknown_source",
                translation_placeholders={
                    "entity_id": str(self.entity_id),
                    "source": str(source),
                },
            )
        if self._routing_frames(source_num) is None:
            raise HomeAssistantError(
                f"{self.entity_id}: amplifier {self._where} has no matrix routing address",
                translation_domain=DOMAIN,
                translation_key="routing_unsupported",
                translation_placeholders={
                    "entity_id": str(self.entity_id),
                    "where": str(self._where),
                },
            )
        streamer = self._environment_streamer()
        if streamer is not None:
            environment = str(zone_environment(self._where))
            raise self._environment_busy_error(streamer, environment)

        await self._route_to(source_num)
        self.async_schedule_update_ha_state()
