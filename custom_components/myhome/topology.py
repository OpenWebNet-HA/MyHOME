"""Shared-bus topology of the configured gateways (#453).

Read straight from the config entries, not from loaded handlers, so the answers
do not depend on the order in which the gateways were set up.
"""
from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from homeassistant.const import CONF_MAC
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import device_registry as dr

from .const import (
    CONF_BUS_TOPOLOGY,
    CONF_DELEGATED_WHOS,
    CONF_GATEWAY_ROLE,
    CONF_PRIMARY_GATEWAY,
    DOMAIN,
    ROLE_PRIMARY,
    ROLE_SECONDARY,
    ROLE_STANDBY,
    TOPOLOGY_SHARED,
    TOPOLOGY_STANDALONE,
)


def _setting(entry: Any, key: str) -> Any:
    """An entry setting, options first, then data."""
    for source in (getattr(entry, "options", None), getattr(entry, "data", None)):
        if isinstance(source, Mapping) and key in source:
            return source[key]
    return None


def entry_mac(entry: Any) -> str | None:
    """The normalised MAC of a gateway entry."""
    data = getattr(entry, "data", None)
    raw = (data.get(CONF_MAC) if isinstance(data, Mapping) else None) or getattr(entry, "unique_id", None)
    return dr.format_mac(str(raw)) if raw else None


def entry_topology(entry: Any) -> str:
    return str(_setting(entry, CONF_BUS_TOPOLOGY) or TOPOLOGY_STANDALONE)


def entry_role(entry: Any) -> str:
    return str(_setting(entry, CONF_GATEWAY_ROLE) or ROLE_PRIMARY)


def entry_is_follower(entry: Any) -> bool:
    """Secondary or standby on a shared bus."""
    return entry_topology(entry) == TOPOLOGY_SHARED and entry_role(entry) in (ROLE_SECONDARY, ROLE_STANDBY)



def entry_primary_mac(entry: Any) -> str | None:
    """The primary a secondary/standby entry points at."""
    if not entry_is_follower(entry):
        return None
    raw = _setting(entry, CONF_PRIMARY_GATEWAY)
    return dr.format_mac(str(raw)) if raw else None


def entry_delegated_whos(entry: Any) -> set[int]:
    """WHOs delegated to a secondary entry (never to a standby)."""
    if entry_topology(entry) != TOPOLOGY_SHARED or entry_role(entry) != ROLE_SECONDARY:
        return set()
    whos: set[int] = set()
    for item in _setting(entry, CONF_DELEGATED_WHOS) or []:
        try:
            whos.add(int(item))
        except (ValueError, TypeError):
            pass
    return whos


def dependents(hass: HomeAssistant, mac: str) -> list[Any]:
    """The secondary/standby entries that point at ``mac`` as their primary."""
    return [e for e in hass.config_entries.async_entries(DOMAIN) if entry_primary_mac(e) == mac]


def delegated_away_whos(hass: HomeAssistant, mac: str) -> set[int]:
    """WHOs a primary leaves to its secondaries."""
    whos: set[int] = set()
    for entry in dependents(hass, mac):
        whos |= entry_delegated_whos(entry)
    return whos


def topology_signature(entry: Any) -> tuple[Any, ...]:
    """What a reload has to pick up when it changes."""
    return (
        entry_topology(entry),
        entry_role(entry),
        entry_primary_mac(entry),
        tuple(sorted(entry_delegated_whos(entry))),
    )


@callback
def async_check_primary_links(hass: HomeAssistant, *, removed: str | None = None) -> None:
    """Raise a repair issue for each secondary/standby left without a shared primary.

    Such a gateway keeps suppressing discovery for a primary that is gone, so the
    user has to reconfigure it. ``removed`` is an entry being deleted right now.
    """
    from .repairs import async_create_primary_missing_issue, async_delete_primary_missing_issue

    for entry in hass.config_entries.async_entries(DOMAIN):
        if entry.entry_id == removed:
            continue
        primary = entry_primary_mac(entry)
        target = entry_for_mac(hass, primary, exclude=removed) if primary else None
        if not entry_is_follower(entry) or (
            target is not None and entry_topology(target) == TOPOLOGY_SHARED and entry_role(target) == ROLE_PRIMARY
        ):
            async_delete_primary_missing_issue(hass, entry.entry_id)
        else:
            async_create_primary_missing_issue(hass, entry.entry_id, entry.title, primary or "-")


def entry_for_mac(hass: HomeAssistant, mac: str, *, exclude: str | None = None) -> Any | None:
    """The config entry of the gateway with ``mac`` (ignoring entry id ``exclude``)."""
    for entry in hass.config_entries.async_entries(DOMAIN):
        if entry.entry_id != exclude and entry_mac(entry) == mac:
            return entry
    return None


def peer_unique_id(unique_id: str, own_mac: str, peer_mac: str) -> str | None:
    """``unique_id`` rewritten onto ``peer_mac`` (entity unique ids start with the gateway MAC)."""
    if unique_id.startswith(own_mac):
        return f"{peer_mac}{unique_id[len(own_mac):]}"
    clean_own = own_mac.replace(":", "").lower()
    if unique_id.lower().startswith(clean_own):
        return f"{peer_mac.replace(':', '').lower()}{unique_id[len(clean_own):]}"
    return None
