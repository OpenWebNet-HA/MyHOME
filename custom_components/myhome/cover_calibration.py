"""Support for MyHome cover travel-time calibration (myhome.calibrate_cover)."""
from __future__ import annotations

import asyncio
import collections
import time
from typing import TYPE_CHECKING, Any

from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import device_registry as dr
from homeassistant.util import dt as dt_util

from .const import (
    CONF_COVER_TRAVEL_TIMES,
    DOMAIN,
    LOGGER,
)
from .data import MyHOMERuntimeData

if TYPE_CHECKING:
    from .gateway import MyHOMEGatewayHandler

# Deprecated module globals for backward compatibility; production state lives on CoverCalibrationHub






def _gateway_key(gateway: Any) -> str:
    """Return a unique key for the gateway (MAC address or object id)."""
    return str(getattr(gateway, "mac", "") or id(gateway))


def _normalize_mac(mac: Any) -> str | None:
    """One spelling for a gateway MAC so frames and requests compare equal."""
    if mac is None or str(mac).strip() == "":
        return None
    return dr.format_mac(str(mac))


def _calibration_lock(gateway: Any) -> asyncio.Lock:
    """Return the per-gateway calibration lock, rebinding if event loop changed."""
    return get_calibration_hub(gateway).lock


def _record_calibration_frame(gateway: Any, direction: str, raw: str, **extra: Any) -> None:
    """Record a frame during active calibration to the trace buffer."""
    hub = get_calibration_hub(gateway)
    hub.record_frame(direction, raw, **extra)


def get_last_calibration_trace(gateway_mac: str | None = None, hass: Any = None) -> list[dict[str, Any]]:
    """Return in-memory trace frames captured during recent cover calibrations.

    Traces are stored on each gateway's CoverCalibrationHub. When gateway_mac is
    provided, only that gateway's trace is returned (None is the only unfiltered
    read; a gateway without a MAC gets the frames recorded without one).
    """
    hubs = CoverCalibrationHub.all_hubs(hass)

    if gateway_mac is None:
        combined: list[dict[str, Any]] = []
        for hub in hubs:
            combined.extend(hub.get_trace())
        combined.sort(key=lambda f: f.get("timestamp", 0.0))
        return combined

    wanted = _normalize_mac(gateway_mac)
    for hub in hubs:
        if hub.mac == wanted:
            return hub.get_trace()
    return []


async def async_stop_cover_calibration(hass: Any = None, gateway_mac: str | None = None) -> bool:
    """Stop active and queued cover calibrations on one or all gateways."""
    stopped_any = False
    wanted_mac = _normalize_mac(gateway_mac) if gateway_mac else None

    for hub in CoverCalibrationHub.all_hubs(hass):
        if wanted_mac and hub.mac != wanted_mac:
            continue
        if await hub.async_stop():
            stopped_any = True

    return stopped_any


def _stored_calibration(config_entry: Any, device_id: str) -> dict[str, Any] | None:
    """Return the persisted calibration for a cover, if any."""
    options = getattr(config_entry, "options", None) or {}
    stored = options.get(CONF_COVER_TRAVEL_TIMES) or {}
    entry = stored.get(str(device_id))
    return dict(entry) if isinstance(entry, dict) else None


class CalibrationInterrupted(HomeAssistantError):
    """A wall-switch or scenario command interfered with a calibration run."""

    def __init__(self, name: str, cause: str) -> None:
        super().__init__(
            f"{name}: {cause}",
            translation_domain=DOMAIN,
            translation_key="calibration_interrupted",
            translation_placeholders={"name": name, "cause": cause},
        )


class CoverCalibrationHub:
    """Encapsulates calibration state and serialization per gateway."""

    _registry: dict[str, CoverCalibrationHub] = {}

    def __init__(self, gateway: Any) -> None:
        self.gateway: MyHOMEGatewayHandler = gateway
        self.trace: collections.deque[dict[str, Any]] = collections.deque(maxlen=1000)
        self._lock: asyncio.Lock | None = None
        self._active_cover: Any | None = None
        self._queued_covers: set[Any] = set()
        self._register()

    def _register(self) -> None:
        CoverCalibrationHub._registry[self.key] = self
        if self.mac:
            CoverCalibrationHub._registry[self.mac] = self

    def _unregister(self) -> None:
        if CoverCalibrationHub._registry.get(self.key) is self:
            CoverCalibrationHub._registry.pop(self.key, None)
        if self.mac and CoverCalibrationHub._registry.get(self.mac) is self:
            CoverCalibrationHub._registry.pop(self.mac, None)

    @classmethod
    def all_hubs(cls, hass: Any = None) -> list[CoverCalibrationHub]:
        """Return all active hubs from hass config entries and local registry."""
        hubs: list[CoverCalibrationHub] = []
        seen: set[int] = set()
        if hass is not None and hasattr(hass, "config_entries"):
            for entry in hass.config_entries.async_entries(DOMAIN):
                runtime = getattr(entry, "runtime_data", None)
                hub = getattr(runtime, "calibration_hub", None)
                if isinstance(hub, CoverCalibrationHub) and id(hub) not in seen:
                    hubs.append(hub)
                    seen.add(id(hub))
        for hub in list(cls._registry.values()):
            if id(hub) not in seen:
                hubs.append(hub)
                seen.add(id(hub))
        return hubs

    @classmethod
    def reset_for_tests(cls) -> None:
        """Reset all hubs and clear the registry (for test fixtures)."""
        for hub in list(cls._registry.values()):
            hub.cleanup()
        cls._registry.clear()





    @property
    def mac(self) -> str | None:
        """Return the normalized gateway MAC address."""
        return _normalize_mac(getattr(self.gateway, "mac", None))

    @property
    def key(self) -> str:
        """Return a unique key for the gateway."""
        return _gateway_key(self.gateway)

    @property
    def lock(self) -> asyncio.Lock:
        """Return the asyncio.Lock, dynamically rebinding if the running loop changed."""
        try:
            current_loop = asyncio.get_running_loop()
        except RuntimeError:
            current_loop = None

        if self._lock is not None:
            bound_loop = getattr(self._lock, "_bound_loop", None) or getattr(self._lock, "_loop", None)
            if (bound_loop is not None and bound_loop.is_closed()) or (
                current_loop is not None and bound_loop is not current_loop
            ):
                self._lock = None

        if self._lock is None:
            self._lock = asyncio.Lock()
            if current_loop is not None:
                setattr(self._lock, "_bound_loop", current_loop)

        return self._lock

    @property
    def active_cover(self) -> Any | None:
        """Return the currently calibrating cover for this gateway."""
        return self._active_cover

    @active_cover.setter
    def active_cover(self, cover: Any | None) -> None:
        self._active_cover = cover

    @property
    def is_calibrating(self) -> bool:
        """Return True if any cover on this gateway is actively calibrating."""
        return bool(self._active_cover is not None and getattr(self._active_cover, "_calibrating", False))

    @property
    def queued_covers(self) -> set[Any]:
        """Return set of covers waiting for calibration lock on this gateway."""
        return self._queued_covers

    def record_frame(self, direction: str, raw: str, **extra: Any) -> None:
        """Record a calibration frame to this hub's trace buffer."""
        now = dt_util.utcnow()
        frame = {
            "timestamp": time.time(),
            "iso_time": now.isoformat(),
            "gateway_mac": self.mac,
            "direction": direction,
            "raw": str(raw).strip(),
            **extra,
        }
        self.trace.append(frame)

    def get_trace(self) -> list[dict[str, Any]]:
        """Return the in-memory trace frames recorded for this gateway."""
        return list(self.trace)

    async def async_stop(self) -> bool:
        """Stop active and queued calibrations on this gateway."""
        stopped_any = False
        queued = list(self.queued_covers)
        self.queued_covers.clear()
        for c in queued:
            c._calibration_interrupted = "Calibration stopped by user"
            c._fire_calibration_event("failed", error="Calibration stopped by user")
            stopped_any = True

        active = self.active_cover
        if active is not None and getattr(active, "_calibrating", False):
            active._calibration_interrupted = "Calibration stopped by user"
            active._motor_started.set()
            active._stopped_event.set()
            try:
                await active.async_stop_cover()
            except Exception as err:
                LOGGER.warning("Error stopping cover %s: %s", active.entity_id, err)
            stopped_any = True
            self.active_cover = None

        return stopped_any

    def cleanup(self) -> None:
        """Clean up all references when gateway unloads."""
        self._active_cover = None
        self._queued_covers.clear()
        self._lock = None
        self.trace.clear()
        self._unregister()


def get_calibration_hub(gateway: Any) -> CoverCalibrationHub:
    """Return or create the CoverCalibrationHub for a gateway handler."""
    entry = getattr(gateway, "config_entry", None)
    runtime = getattr(entry, "runtime_data", None) if entry is not None else None
    if isinstance(runtime, MyHOMERuntimeData):
        if runtime.calibration_hub is None:
            hub = getattr(gateway, "_calibration_hub", None)
            if not isinstance(hub, CoverCalibrationHub):
                hub = CoverCalibrationHub(gateway)
            runtime.calibration_hub = hub
        return runtime.calibration_hub

    if runtime is not None:
        raw_hub = getattr(runtime, "calibration_hub", None)
        if isinstance(raw_hub, CoverCalibrationHub):
            return raw_hub

    # Fallback when runtime_data is not available (e.g. mock objects in unit tests)
    hub = getattr(gateway, "_calibration_hub", None)
    if not isinstance(hub, CoverCalibrationHub):
        hub = CoverCalibrationHub(gateway)
        try:
            gateway._calibration_hub = hub
        except Exception:  # pragma: no cover - defensive
            pass
    return hub
