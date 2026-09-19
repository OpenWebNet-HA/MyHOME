"""Typed per-entry runtime state (Integration Quality Scale rule ``runtime-data``).

Everything a config entry needs at runtime lives on ``entry.runtime_data`` as a
:class:`MyHOMERuntimeData`; platforms, services, the WebSocket API and diagnostics
read it from there. ``hass.data[DOMAIN]`` only holds integration-wide singletons
(OWNd version, frontend registration flags, customize.yaml names).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from homeassistant.config_entries import ConfigEntry

from .router import FrameRouter

if TYPE_CHECKING:
    from .bus_monitor import BusMonitor
    from .decoder_pool import DecoderPool
    from .gateway import MyHOMEGatewayHandler


@dataclass
class MyHOMERuntimeData:
    """Runtime state of one gateway config entry."""

    gateway: MyHOMEGatewayHandler
    # Device configurations per platform: {platform: {device_id: {...}}}, filled from
    # myhome.yaml at setup and by bus discovery while running.
    platforms: dict[str, dict[str, dict[str, Any]]] = field(default_factory=dict)
    # Entity objects per platform, registered by the entities themselves.
    entities: dict[str, dict[str, Any]] = field(default_factory=dict)
    # Shared multi-room audio decoder pool (media_player), rebuilt on options update.
    decoder_pool: DecoderPool | None = None
    # Delivers bus frames to the entities owning their addresses (see router.py).
    router: FrameRouter = field(default_factory=FrameRouter)

    @property
    def mac(self) -> str:
        """Gateway MAC address, the key used throughout the integration."""
        return self.gateway.mac

    @property
    def bus_monitor(self) -> BusMonitor | None:
        """The gateway's bus monitor, if the handler exposes one."""
        return getattr(self.gateway, "bus_monitor", None)


MyHOMEConfigEntry = ConfigEntry[MyHOMERuntimeData]


def get_runtime_data(entry: ConfigEntry) -> MyHOMERuntimeData | None:
    """Return the entry's runtime data, or ``None`` when the entry is not set up."""
    data = getattr(entry, "runtime_data", None)
    return data if isinstance(data, MyHOMERuntimeData) else None
