"""Helper for cross-integration decoder discovery and companion resolution.

The MyHOME BTicino F441M analog matrix connects to physical hardware decoders
(such as Cambridge Audio, Squeezelite, WiiM, Sonos, etc.). Some vendor integrations
(e.g. ``cambridge_audio``) expose device controls but refuse direct HTTP stream
URLs via ``play_media``. However, the underlying hardware also exposes standard
UPnP / DLNA DMR, which accepts stream URLs.

This module provides registry lookups to find companion streaming entities
for a given decoder entity.
"""
from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass

from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er

STREAMING_COMPANION_PLATFORMS: frozenset[str] = frozenset({"dlna_dmr", "upnp", "cast"})

# Platforms a decoder must never be: a MyHOME zone or a Music Assistant clone
# routed back into the pool would loop the audio.
FORBIDDEN_DECODER_PLATFORMS: frozenset[str] = frozenset({"myhome", "mass"})

# When several streaming entities hang off one device, prefer the renderer that
# takes an arbitrary stream URL most reliably.
_PLATFORM_PRIORITY: dict[str, int] = {"dlna_dmr": 0, "upnp": 1, "cast": 2}


@dataclass(frozen=True)
class CompanionMatch:
    """Outcome of a companion lookup: what matched, how, and what it competed with."""

    entity_id: str | None
    """The companion, or ``None`` when there is none or the match is ambiguous."""
    step: str | None
    """``override``, ``same_device``, ``mac``, ``host`` or ``name``; ``None`` if nothing matched."""
    ambiguous: tuple[str, ...] = ()
    """Entities of different devices that all matched at ``step``, when we refuse to pick one."""


def _rank(cand: er.RegistryEntry) -> tuple[int, str]:
    return (_PLATFORM_PRIORITY.get(cand.platform, len(_PLATFORM_PRIORITY)), cand.entity_id)


def _pick(cands: Iterable[er.RegistryEntry], step: str) -> CompanionMatch | None:
    """Resolve the candidates that matched at one step.

    Entities of one device are ranked by platform, deterministically. Entities
    of *different* devices are not: guessing which streamer is meant is how the
    wrong stick ends up glued to the wrong box, so the caller gets the list.
    """
    by_device: dict[str | None, list[er.RegistryEntry]] = {}
    for cand in cands:
        by_device.setdefault(cand.device_id, []).append(cand)
    if not by_device:
        return None
    best = sorted((min(group, key=_rank) for group in by_device.values()), key=_rank)
    if len(best) == 1:
        return CompanionMatch(best[0].entity_id, step)
    return CompanionMatch(None, step, tuple(cand.entity_id for cand in best))


def _is_streaming_player(cand: er.RegistryEntry) -> bool:
    return cand.domain == "media_player" and cand.platform in STREAMING_COMPANION_PLATFORMS


def _mac_set(device: dr.DeviceEntry) -> set[str]:
    return {conn[1] for conn in device.connections if conn[0] == dr.CONNECTION_NETWORK_MAC}


def _device_name(device: dr.DeviceEntry | dr.ChildDeviceEntry) -> str:
    return (device.name_by_user or device.name or "").lower().strip()


def async_resolve_streaming_companion(
    hass: HomeAssistant, entity_id: str, override: str | None = None
) -> CompanionMatch:
    """Find the streaming-capable companion (DLNA DMR, UPnP, Cast) of a decoder entity.

    Steps, most to least specific: an explicit ``override`` from the options;
    another entity of the exact same device; a device sharing a MAC address; a
    config entry sharing the host; a device with exactly the same name. The first
    step that matches wins. A step that matches several *different* devices is
    reported as ambiguous instead of guessed, so the user can pick with the
    companion option. Names are compared for equality, not containment: two
    ``Cambridge CXN`` boxes must not be glued together by a shared substring.

    Args:
        hass: Home Assistant instance.
        entity_id: The decoder entity (e.g. ``media_player.network_streamer``).
        override: The companion the user configured for this decoder, if any.
    """
    if override and override != entity_id:
        return CompanionMatch(override, "override")

    ent_reg = er.async_get(hass)
    dev_reg = dr.async_get(hass)

    entry = ent_reg.async_get(entity_id)
    if not entry:
        return CompanionMatch(None, None)

    # 1. Other entities on the EXACT same device.
    if entry.device_id:
        match = _pick(
            (
                cand
                for cand in er.async_entries_for_device(ent_reg, entry.device_id)
                if cand.entity_id != entity_id and _is_streaming_player(cand)
            ),
            "same_device",
        )
        if match:
            return match

    device = dev_reg.async_get(entry.device_id) if entry.device_id else None

    # Steps 2 and 4 look at other devices. Walk the streaming-capable entities
    # rather than ``dev_reg.devices``: reading that registry as a mapping is
    # deprecated in HA 2026.9, and a companion is by definition one of these.
    candidates = [
        (cand, other_dev)
        for cand in ent_reg.entities.values()
        if _is_streaming_player(cand)
        and cand.device_id
        and (device is None or cand.device_id != device.id)
        and (other_dev := dev_reg.async_get(cand.device_id)) is not None
    ]

    # 2. Same MAC address (if not merged by the device registry). Child devices
    # carry no connections of their own (reading them is deprecated in HA
    # 2026.9), so only main devices can match here.
    if isinstance(device, dr.DeviceEntry):
        macs = _mac_set(device)
        if macs:
            match = _pick(
                (
                    cand
                    for cand, other_dev in candidates
                    if isinstance(other_dev, dr.DeviceEntry) and macs & _mac_set(other_dev)
                ),
                "mac",
            )
            if match:
                return match

    # 3. Config entries sharing the same host/IP address.
    host = None
    if entry.config_entry_id:
        cfg = hass.config_entries.async_get_entry(entry.config_entry_id)
        if cfg:
            host = cfg.data.get("host") or cfg.data.get("ip_address")
    if host:
        by_host: list[er.RegistryEntry] = []
        for other_entry in hass.config_entries.async_entries():
            if other_entry.entry_id == entry.config_entry_id:
                continue
            if (other_entry.data.get("host") or other_entry.data.get("ip_address")) == host:
                by_host.extend(
                    cand
                    for cand in er.async_entries_for_config_entry(ent_reg, other_entry.entry_id)
                    if _is_streaming_player(cand)
                )
        match = _pick(by_host, "host")
        if match:
            return match

    # 4. Devices with exactly the same name (e.g. a user-named "Audio Decoder").
    if device is not None:
        dev_name = _device_name(device)
        if dev_name:
            match = _pick((cand for cand, other_dev in candidates if _device_name(other_dev) == dev_name), "name")
            if match:
                return match

    return CompanionMatch(None, None)


def async_find_streaming_companion(hass: HomeAssistant, entity_id: str, override: str | None = None) -> str | None:
    """Return the companion entity_id of ``entity_id``, or ``None`` if absent or ambiguous."""
    return async_resolve_streaming_companion(hass, entity_id, override).entity_id


def async_decoder_platform_problem(hass: HomeAssistant, entity_id: str) -> str | None:
    """Return the platform that disqualifies ``entity_id`` as a decoder, if any."""
    entry = er.async_get(hass).async_get(entity_id)
    if entry is not None and entry.platform in FORBIDDEN_DECODER_PLATFORMS:
        return entry.platform
    return None


def async_get_excluded_decoders(hass: HomeAssistant) -> list[str]:
    """Return entity IDs that must not be selected as decoders.

    Excludes internal MyHOME sound zones (preventing circular loops) and
    Music Assistant cloned entities.
    """
    ent_reg = er.async_get(hass)
    return [
        entry.entity_id
        for entry in ent_reg.entities.values()
        if entry.domain == "media_player" and entry.platform in FORBIDDEN_DECODER_PLATFORMS
    ]
