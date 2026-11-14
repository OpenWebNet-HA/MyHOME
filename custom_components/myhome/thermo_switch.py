"""Runtime helpers for WHO=4 probe-less thermo actuator / towel-warmer switches."""

from __future__ import annotations

from typing import Any

from homeassistant.const import CONF_NAME

from .const import (
    BUS_ROUTING,
    CONF_BUS_INTERFACE,
    CONF_DEVICE_MODEL,
    CONF_ENTITIES,
    CONF_MANUFACTURER,
    CONF_TURN_OFF_COMMAND,
    CONF_TURN_ON_COMMAND,
    CONF_WHERE,
    CONF_WHO,
    LOGGER,
    SUBENTRY_TYPE_THERMO_SWITCH,
)
from .where_grammar import parse_thermo_actuator_where, validate_thermo_trigger_command


def load_thermo_switch_subentries(
    config_entry: Any, platforms: dict[str, dict[str, Any]]
) -> None:
    """Populate ``platforms['switch']`` from ``thermo_switch`` ConfigSubentries."""
    subentries = getattr(config_entry, "subentries", None) or {}
    if not hasattr(subentries, "items"):
        return

    for sub_id, subentry in subentries.items():
        if getattr(subentry, "subentry_type", None) != SUBENTRY_TYPE_THERMO_SWITCH:
            continue
        switch_map = platforms.setdefault("switch", {})
        data = dict(getattr(subentry, "data", None) or {})
        try:
            norm_where, _zone, _act, norm_if = parse_thermo_actuator_where(
                data.get(CONF_WHERE), data.get(CONF_BUS_INTERFACE)
            )
        except ValueError as err:
            LOGGER.warning(
                "Skipping invalid thermo_switch subentry %s: %s", sub_id, err
            )
            continue

        dev_key = (
            f"4-{norm_where}{BUS_ROUTING}{norm_if}" if norm_if else f"4-{norm_where}"
        )
        existing = switch_map.get(dev_key)
        if isinstance(existing, dict) and existing.get("subentry_id") != str(sub_id):
            continue

        raw_on = data.get(CONF_TURN_ON_COMMAND)
        raw_off = data.get(CONF_TURN_OFF_COMMAND)
        on_cmd: str | None = None
        off_cmd: str | None = None
        if raw_on or raw_off:
            try:
                if not raw_on or not raw_off:
                    raise ValueError(
                        "turn_on_command and turn_off_command must both be set"
                    )
                on_cmd = validate_thermo_trigger_command(raw_on)
                off_cmd = validate_thermo_trigger_command(raw_off)
            except ValueError as err:
                LOGGER.warning(
                    "Skipping invalid trigger command on thermo_switch subentry %s: %s",
                    sub_id,
                    err,
                )
                continue

        suffix = f"{norm_where}I{norm_if}" if norm_if else norm_where
        raw_name = str(data.get(CONF_NAME) or "").strip()
        cfg: dict[str, Any] = {
            CONF_WHO: "4",
            CONF_WHERE: norm_where,
            CONF_NAME: raw_name or f"Thermo Actuator {suffix}",
            CONF_MANUFACTURER: "BTicino S.p.A.",
            CONF_DEVICE_MODEL: "Thermo Actuator",
            CONF_ENTITIES: {},
            "subentry_id": str(sub_id),
        }
        if norm_if is not None:
            cfg[CONF_BUS_INTERFACE] = norm_if
        if on_cmd is not None and off_cmd is not None:
            cfg[CONF_TURN_ON_COMMAND] = on_cmd
            cfg[CONF_TURN_OFF_COMMAND] = off_cmd
        switch_map[dev_key] = cfg


def has_thermo_switch_subentry(
    config_entry: Any,
    where: str,
    interface: str | None,
    *,
    subentry_id: str | None = None,
) -> bool:
    """Return True if ``config_entry`` still holds a ``thermo_switch`` subentry for ``(where, interface)``."""
    subentries = getattr(config_entry, "subentries", None) or {}
    if not hasattr(subentries, "items"):
        return False

    try:
        norm_where, _z, _a, norm_if = parse_thermo_actuator_where(where, interface)
    except ValueError:
        return False

    for sub_id, subentry in subentries.items():
        if getattr(subentry, "subentry_type", None) != SUBENTRY_TYPE_THERMO_SWITCH:
            continue
        if subentry_id is not None and str(sub_id) != subentry_id:
            continue
        data = dict(getattr(subentry, "data", None) or {})
        try:
            sub_where, _sz, _sa, sub_if = parse_thermo_actuator_where(
                data.get(CONF_WHERE), data.get(CONF_BUS_INTERFACE)
            )
        except ValueError:
            if subentry_id is not None and str(sub_id) == subentry_id:
                return True
            continue
        if (sub_where, sub_if) == (norm_where, norm_if):
            return True
    return False
