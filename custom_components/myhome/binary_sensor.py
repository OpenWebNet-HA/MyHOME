"""Support for MyHome binary sensors (dry contacts and motion sensors)."""
from __future__ import annotations

import typing
from collections.abc import Mapping
from datetime import datetime, timedelta
from typing import Any

from homeassistant.components.binary_sensor import (  # type: ignore[attr-defined, unused-ignore]
    DOMAIN as PLATFORM,
)
from homeassistant.components.binary_sensor import (  # type: ignore[attr-defined, unused-ignore]
    BinarySensorDeviceClass,
    BinarySensorEntity,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import (
    CONF_MAC,
    CONF_NAME,
    STATE_ON,
)
from homeassistant.core import CALLBACK_TYPE, HassJob, HomeAssistant, callback
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.dispatcher import (
    async_dispatcher_connect,
    async_dispatcher_send,
)
from homeassistant.helpers.entity import Entity
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.event import async_call_later
from homeassistant.util import dt as dt_util
from OWNd.message import (
    MESSAGE_TYPE_MOTION,
    MESSAGE_TYPE_MOTION_TIMEOUT,
    MESSAGE_TYPE_PIR_SENSITIVITY,
    OWNAuxEvent,
    OWNDryContactCommand,
    OWNDryContactEvent,
    OWNLightingCommand,
    OWNLightingEvent,
)

from .const import (
    BUS_ROUTING,
    CONF_DEVICE_CLASS,
    CONF_DEVICE_MODEL,
    CONF_ENTITY_NAME,
    CONF_INVERTED,
    CONF_INVERTED_BINARY_SENSORS,
    CONF_MANUFACTURER,
    CONF_WHERE,
    CONF_WHO,
    DOMAIN,
    LOGGER,
    SIGNAL_BINARY_SENSOR_INVERTED,
    SIGNAL_NEW_BINARY_SENSOR,
    normalize_where,
)
from .discovery import Address, DeviceContext, PlatformDiscovery, is_motion_end
from .gateway import MyHOMEGatewayHandler
from .myhome_device import MyHOMEEntity
from .where_grammar import (
    bus_interface_from_message,
    canonical_contact_where,
    contact_where_aliases,
    padded_contact_where,
    where_param,
)

PARALLEL_UPDATES = 0

# Grace margin above the detector's reported DIMENSION 7 timeout so continuous
# motion re-triggers (e.g. K4659 in Scenarios mode, ~0.55 s jitter) do not flap.
MOTION_TIMEOUT_GRACE = timedelta(seconds=3)
DEFAULT_MOTION_TIMEOUT = timedelta(seconds=300) + MOTION_TIMEOUT_GRACE
# Safety-net hold for Stand-alone detectors known to emit WHAT 39 (#777):
# those detectors may stay silent for several minutes during continuous occupancy
# (243.3 s observed on Legrand 067226) before emitting WHAT 39 at actual vacancy.
MOTION_END_SAFETY_TIMEOUT = timedelta(minutes=10)
# Window after a timer-driven clear within which a fresh WHAT 34 indicates the
# device's configured Time delay is longer than our cached _timeout.
STALE_TIMEOUT_RETRIGGER_WINDOW = timedelta(seconds=60)
PIR_SENSITIVITY = ["low", "medium", "high", "very high"]

ALL_DEVICE_CLASS_SUFFIXES = tuple(
    sorted(
        list(
            {
                f"-{getattr(dc, 'value', dc)}"
                for dc in BinarySensorDeviceClass
            }
            | {
                "-opening",
                "-door",
                "-garage_door",
                "-window",
                "-moving",
                "-motion",
                "-safety",
                "-moisture",
                "-smoke",
                "-gas",
                "-heat",
                "-cold",
                "-light",
                "-lock",
                "-occupancy",
                "-plug",
                "-power",
                "-presence",
                "-problem",
                "-running",
                "-sound",
                "-tamper",
                "-update",
                "-vibration",
                "-battery",
                "-battery_charging",
                "-connectivity",
            }
        ),
        key=len,
        reverse=True,
    )
)


async def async_setup_entry(hass: HomeAssistant, config_entry: ConfigEntry, async_add_entities: AddEntitiesCallback) -> None:
    """Set up the binary sensors of a gateway: dry contacts (WHO=25), auxiliary
    channels (WHO=9) and motion sensors (WHO=1), each restored from the registry,
    then created from myhome.yaml, then discovered from the bus.

    Unique ids carry the device class (``{mac}-25-31-opening``), and the same
    contact may be spelled ``0031`` or ``31``: every entity is remembered under
    all of its spellings, and frames are routed under all of theirs. A dry contact
    an F455 spells ``301`` is the ``31`` of every other gateway: its frames are read
    as ``31``, and an entity already registered as ``301`` keeps its unique id and
    answers to both.
    """
    runtime = config_entry.runtime_data
    gateway = runtime.gateway
    mac = config_entry.data[CONF_MAC]
    configured = runtime.platforms.get(PLATFORM, {})

    def registry_address(who: str):  # type: ignore
        def address_of(entry) -> Address | None:  # type: ignore
            classified = _classify_registry_entry(entry, gateway.mac, mac)
            if classified is None or classified[0] != who:
                return None
            return Address.from_device_id(classified[1])

        return address_of

    def duplicate(entry, ctx: DeviceContext) -> bool:  # type: ignore
        # A second registry entry for a contact already restored under another spelling
        return any(key in ctx_discovery[ctx.who].known for key in _spellings_for(ctx.who)(ctx.address.key))

    ctx_discovery: dict[str, PlatformDiscovery] = {}

    # ── WHO 25: dry contacts ────────────────────────────────────────────
    def build_dry_contact(ctx: DeviceContext) -> MyHOMEDryContact:
        if ctx.source == "yaml":
            cfg, where = ctx.cfg, ctx.address.key
            device_id, device_class = ctx.config_id or ctx.key, cfg.get(CONF_DEVICE_CLASS) or cfg.get("device_class")
            name, model = cfg[CONF_NAME], "Dry Contact"
        elif ctx.source == "registry":
            candidate = ctx.address.key
            cfg = _first_config(configured, *dry_contact_config_keys(candidate))
            where = Address.from_config(candidate, cfg).key if cfg else candidate
            norm_where, clean_norm = normalize_where(where), normalize_where(where.split("-")[-1])
            device_id = candidate if candidate in configured else (norm_where or clean_norm or where.split("-")[-1])
            reg_dev_id = getattr(ctx.registry_entry, "device_id", None)
            if isinstance(reg_dev_id, str) and reg_dev_id:
                existing_dev = dr.async_get(hass).async_get(reg_dev_id)
                if existing_dev is not None:
                    for dom, ident in existing_dev.identifiers:
                        if dom == DOMAIN and "-25-" in ident:
                            device_id = ident.split("-25-", 1)[1]
                            break
            device_class = cfg.get(CONF_DEVICE_CLASS, getattr(ctx.registry_entry, "original_device_class", None) or BinarySensorDeviceClass.OPENING)
            where_addr = Address.from_device_id(where.split("-")[-1])
            norm_base = normalize_where(where_addr.clean_where) or where_addr.clean_where
            default_label = Address(norm_base, where_addr.interface).suffix
            name, model = cfg.get(CONF_NAME, f"Dry Contact {default_label}"), "Dry Contact Interface"
        else:
            cfg, where = {}, ctx.address.key
            device_id, device_class = where, BinarySensorDeviceClass.OPENING
            name, model = f"Dry Contact {ctx.address.suffix}", "Dry Contact Interface"
        contact = MyHOMEDryContact(
            hass=hass,
            device_id=device_id,
            who="25",
            where=normalize_where(where) or where,
            name=name,
            entity_name=cfg.get(CONF_ENTITY_NAME),
            inverted=resolve_binary_sensor_inverted(config_entry, "25", where, cfg),
            device_class=device_class or BinarySensorDeviceClass.OPENING,
            manufacturer=cfg.get(CONF_MANUFACTURER, "BTicino"),
            model=cfg.get(CONF_DEVICE_MODEL, model),
            gateway=gateway,
        )
        if ctx.registry_entry is not None:
            contact._attr_unique_id = ctx.registry_entry.unique_id
        return contact

    # ── WHO 9: auxiliary channels (configured or restored, never discovered) ──
    def build_auxiliary(ctx: DeviceContext) -> MyHOMEAuxiliary:
        if ctx.source == "yaml":
            cfg, where, device_id = ctx.cfg, ctx.address.where, ctx.config_id or ctx.key
            device_class, name = cfg.get(CONF_DEVICE_CLASS) or cfg.get("device_class"), cfg[CONF_NAME]
        else:
            candidate = ctx.address.where
            clean_candidate = candidate.split("-")[-1]
            cfg = _first_config(configured, f"9-{candidate}", f"9-{clean_candidate}", candidate, clean_candidate)
            where = str(cfg.get(CONF_WHERE, clean_candidate or candidate))
            clean_where = where.split("-")[-1]
            device_id = clean_where or clean_candidate or candidate
            device_class = cfg.get(CONF_DEVICE_CLASS) or getattr(ctx.registry_entry, "original_device_class", None)
            name = cfg.get(CONF_NAME, f"Auxiliary Channel {clean_where}")
        auxiliary = MyHOMEAuxiliary(
            hass=hass,
            device_id=device_id,
            who="9",
            where=where,
            name=name,
            entity_name=cfg.get(CONF_ENTITY_NAME),
            inverted=resolve_binary_sensor_inverted(config_entry, "9", where, cfg),
            device_class=device_class,
            manufacturer=cfg.get(CONF_MANUFACTURER, "BTicino"),
            model=cfg.get(CONF_DEVICE_MODEL, "Auxiliary Channel"),
            gateway=gateway,
        )
        if ctx.registry_entry is not None:
            auxiliary._attr_unique_id = ctx.registry_entry.unique_id
        return auxiliary

    # ── WHO 1: motion sensors ───────────────────────────────────────────
    def build_motion(ctx: DeviceContext) -> MyHOMEMotionSensor:
        if ctx.source == "yaml":
            cfg, where, device_id = ctx.cfg, ctx.address.key, ctx.config_id or ctx.key
            name = cfg[CONF_NAME]
        elif ctx.source == "registry":
            raw = ctx.address.key
            clean_raw = raw.split("-")[-1]
            norm_raw = binary_sensor_canonical_where("1", raw)
            clean_norm = binary_sensor_canonical_where("1", clean_raw)
            cfg = _first_config(configured, f"1-{norm_raw}", f"1-{raw}", norm_raw, raw, clean_norm, clean_raw)
            where = Address.from_config(raw, cfg).key if cfg else (norm_raw or raw)
            device_id = raw if raw in configured else (norm_raw or clean_norm or clean_raw)
            reg_dev_id = getattr(ctx.registry_entry, "device_id", None)
            if isinstance(reg_dev_id, str) and reg_dev_id:
                existing_dev = dr.async_get(hass).async_get(reg_dev_id)
                if existing_dev is not None:
                    for dom, ident in existing_dev.identifiers:
                        if dom == DOMAIN and "-1-" in ident:
                            device_id = ident.split("-1-", 1)[1]
                            break
            default_label = ctx.address.suffix if ctx.address.interface else (clean_norm or clean_raw)
            name = cfg.get(CONF_NAME, f"Motion Sensor {default_label}")
        else:
            cfg, where, device_id = {}, ctx.address.key, ctx.address.key
            name = f"Motion Sensor {ctx.address.suffix}"
        motion = MyHOMEMotionSensor(
            hass=hass,
            device_id=device_id,
            who="1",
            where=binary_sensor_canonical_where("1", where) or where,
            name=name,
            entity_name=cfg.get(CONF_ENTITY_NAME),
            inverted=cfg.get(CONF_INVERTED, False),
            device_class=BinarySensorDeviceClass.MOTION,
            manufacturer=cfg.get(CONF_MANUFACTURER, "BTicino"),
            model=cfg.get(CONF_DEVICE_MODEL, "Motion Sensor"),
            gateway=gateway,
        )
        if ctx.registry_entry is not None:
            motion._attr_unique_id = ctx.registry_entry.unique_id
        return motion

    def yaml_who(who: int, device_class=None):  # type: ignore
        def accept(ctx: DeviceContext) -> bool:
            if ctx.source == "yaml":
                cfg = ctx.cfg
                return int(cfg[CONF_WHO]) == who and (
                    device_class is None or (cfg.get(CONF_DEVICE_CLASS) or cfg.get("device_class")) == device_class
                )
            return ctx.source == "registry" or who != 9  # aux channels are never discovered

        return accept

    def known_keys(ctx: DeviceContext) -> list[str]:
        """Every spelling of the contact: the frame's, the configured, the normalized, the entity's."""
        spellings = _spellings_for(ctx.who)
        keys = [ctx.key, ctx.config_id or "", *spellings(ctx.address.key)]
        if ctx.source != "bus":
            cfg = (
                _first_config(configured, *dry_contact_config_keys(ctx.address.key))
                if ctx.source == "registry" and ctx.who == "25"
                else ctx.cfg
            )
            if cfg.get(CONF_WHERE):
                keys.extend(spellings(Address.from_config(ctx.key, cfg).key))
        return [k for k in keys if k]

    def route_keys(message, address: Address | None) -> list[str]:  # type: ignore
        return _spellings_for(str(getattr(message, "who", "")))(address.key) if address is not None else []

    @callback
    def anonymous_sensor_reply(message: typing.Any) -> None:
        """Route a WHERE=0 PIR dimension reply to the sensor whose request is in flight (#777)."""
        dimension = getattr(message, "dimension", None)
        if dimension not in (5, 7):
            return
        claim = getattr(gateway, "claim_in_flight_dimension_where", None)
        where = claim(1, dimension) if callable(claim) else None
        if isinstance(where, str) and where and any(k in ctx_discovery["1"].known for k in _spellings(where)):
            ctx_discovery["1"].route(message, Address.from_device_id(where))

    common = dict(hass=hass, config_entry=config_entry, async_add_entities=async_add_entities, platform=PLATFORM)
    ctx_discovery["25"] = PlatformDiscovery(
        who="25", event_type=OWNDryContactEvent, build=build_dry_contact,
        registry_address=registry_address("25"), reject_registry_entry=duplicate, accept=yaml_who(25),
        known_keys=known_keys, route_keys=route_keys, address=_contact_address,
        peer_spellings=_contact_peer_spellings, **common,  # type: ignore
    )
    ctx_discovery["9"] = PlatformDiscovery(
        who="9", event_type=OWNAuxEvent, build=build_auxiliary,
        registry_address=registry_address("9"), reject_registry_entry=duplicate, accept=yaml_who(9),
        known_keys=known_keys, route_keys=route_keys, address=_aux_address, **common,  # type: ignore
    )
    ctx_discovery["1"] = PlatformDiscovery(
        who="1", event_type=OWNLightingEvent, build=build_motion,
        registry_address=registry_address("1"), reject_registry_entry=duplicate,
        accept=yaml_who(1, BinarySensorDeviceClass.MOTION),
        known_keys=known_keys, route_keys=route_keys, address=_motion_address,
        on_general=anonymous_sensor_reply, **common,  # type: ignore
    )

    entities: list[Entity] = []
    for discovery in ctx_discovery.values():
        entities.extend(discovery.start(listen=False, add=False))
    if entities:
        async_add_entities(entities)

    @callback
    def _handle_binary_sensor_message(msg: typing.Any) -> None:
        """Forward incoming bus messages to binary sensor entities."""
        for discovery in ctx_discovery.values():
            discovery.handle_message(msg)

    config_entry.async_on_unload(
        async_dispatcher_connect(hass, f"myhome_message_{mac}", _handle_binary_sensor_message)
    )
    return True  # type: ignore


def _spellings(where: str) -> list[str]:
    """``0031`` may also appear as ``31``, ``04#4#1`` as ``04#4#01``, and a legacy ``25-0031`` as either."""
    clean = where.split("-")[-1]
    base, sep, iface = clean.partition(BUS_ROUTING)
    norm_clean = normalize_where(clean)
    if sep:
        norm_base = normalize_where(base) or base
        norm_iface = (
            iface.zfill(2)
            if iface.isascii() and iface.isdigit() and 1 <= len(iface) <= 2 and int(iface) <= 15
            else iface
        )
        norm_clean = f"{norm_base}{BUS_ROUTING}{norm_iface}"
    return list(
        dict.fromkeys(
            k for k in (where, normalize_where(where), clean, normalize_where(clean), norm_clean) if k
        )
    )


def _contact_spellings(where: str) -> list[str]:
    """:func:`_spellings` of a WHO 25 contact, plus both ``31`` and ``301`` forms (a WHERE or legacy ``25-`` id only)."""
    head, sep, _tail = where.rpartition("-")
    if sep and head != "25":
        return [where]
    keys = _spellings(where)
    return list(dict.fromkeys([*keys, *(alias for key in keys for alias in contact_where_aliases(key))]))


def _contact_peer_spellings(unique_id: str) -> list[str]:
    """Symmetric peer spellings for WHO 25 dry contacts (``301`` <-> ``31``, with or without ``-25-``)."""
    stem = _strip_class_suffix(unique_id)
    head, sep, where = stem.rpartition("-")
    if not sep or not head:
        return []
    # Only rewrite gateway-MAC or gateway-MAC + "-25" stems; never touch named YAML keys like "{mac}-gate-301-opening".
    if head.endswith("-25"):
        mac_part = head[:-3]
        if not mac_part or "-" in mac_part:
            return []
        alt_heads = (head, mac_part)
    elif "-" not in head:
        alt_heads = (head, f"{head}-25")
    else:
        return []
    aliases = contact_where_aliases(where)
    suffix = unique_id[len(stem) :]
    peers = [
        f"{h}-{alias}{suffix}"
        for h in alt_heads
        for alias in aliases
        if f"{h}-{alias}{suffix}" != unique_id
    ]
    return list(dict.fromkeys(peers))


def _spellings_for(who: str) -> typing.Callable[[str], list[str]]:
    """The spellings of an address of ``who``: only a WHO 25 contact has a padded one."""
    return _contact_spellings if str(who) == "25" else _spellings


def dry_contact_config_keys(candidate: str) -> tuple[str, ...]:
    """Lookup keys in ``configured`` for a WHO 25 registry entry.

    Custom hyphenated YAML keys (such as ``gate-301``) are matched only under their
    exact key, never stripped to ``301`` or expanded to contact aliases.
    """
    head, sep, _tail = candidate.rpartition("-")
    if sep and head != "25":
        return (f"25-{candidate}", candidate)
    clean_candidate = candidate.split("-")[-1]
    contact_aliases = contact_where_aliases(clean_candidate)
    return tuple(
        dict.fromkeys(
            k
            for k in (
                f"25-{candidate}",
                candidate,
                *(f"25-{alias}" for alias in contact_aliases),
                clean_candidate,
                *contact_aliases,
                normalize_where(candidate),
                normalize_where(clean_candidate),
            )
            if k
        )
    )


def _first_config(configured: dict[str, Any], *keys: str) -> dict[str, Any]:
    for key in keys:
        cfg = configured.get(key)
        if cfg:
            return dict(cfg)
    return {}


def binary_sensor_canonical_where(who: str, where: str) -> str:
    """Return canonical WHERE key for binary sensor options without normalizing WHO=9."""
    raw = str(where).removeprefix(f"{who}-")
    if str(who) == "9":
        return raw
    base, sep, iface = raw.partition(BUS_ROUTING)
    if str(who) == "25" and base.isascii() and base.isdigit():
        norm_base = base.lstrip("0") or "0"
    else:
        norm_base = normalize_where(base) or base
    if sep:
        norm_iface = (
            iface.zfill(2)
            if iface.isascii() and iface.isdigit() and 1 <= len(iface) <= 2 and int(iface) <= 15
            else iface
        )
        combined = f"{norm_base}{BUS_ROUTING}{norm_iface}"
    else:
        combined = norm_base
    return canonical_contact_where(combined) if str(who) == "25" else combined


def binary_sensor_option_key(who: str, where: str) -> str:
    """Return the ``inverted_binary_sensors`` option key ``"{who}-{canonical_where}"``."""
    return f"{who}-{binary_sensor_canonical_where(who, where)}"


def resolve_binary_sensor_inverted(
    config_entry: Any,
    who: str,
    where: str,
    cfg: Mapping[str, Any] | None = None,
) -> bool:
    """Resolve effective ``inverted`` polarity (UI options override YAML ``inverted``)."""
    options = getattr(config_entry, "options", None)
    if isinstance(options, Mapping):
        stored = options.get(CONF_INVERTED_BINARY_SENSORS)
        if isinstance(stored, Mapping):
            opt_key = binary_sensor_option_key(who, where)
            if opt_key in stored:
                return bool(stored[opt_key])
            if str(who) == "25":
                padded = padded_contact_where(binary_sensor_canonical_where(who, where))
                legacy_key = f"25-{padded}"
                if legacy_key in stored:
                    return bool(stored[legacy_key])
    return bool(cfg.get(CONF_INVERTED, False)) if cfg else False


def _strip_class_suffix(candidate: str) -> str:
    for suffix in ALL_DEVICE_CLASS_SUFFIXES:
        if candidate.endswith(suffix):
            return candidate[: -len(suffix)]
    return candidate


def classify_binary_sensor_registry_entry(
    entry: Any, gateway_mac: str, entry_mac: str
) -> tuple[str, str] | None:
    """``(who, candidate id)`` of a registry entry, from its unique id or device class.

    Auxiliary channels first (an aux channel may carry the motion class), then
    dry contacts, then motion sensors.
    """
    unique_id = str(entry.unique_id or "")
    after_mac = unique_id.replace(f"{gateway_mac}-", "", 1).replace(f"{entry_mac}-", "", 1)
    if after_mac.startswith("9-") or "-9-" in unique_id:
        raw = after_mac.replace("9-", "", 1) if after_mac.startswith("9-") else after_mac
        return "9", _strip_class_suffix(raw)
    if (
        after_mac.startswith("25-")
        or "-25-" in unique_id
        or entry.original_device_class in (
            BinarySensorDeviceClass.OPENING,
            BinarySensorDeviceClass.DOOR,
            BinarySensorDeviceClass.GARAGE_DOOR,
            BinarySensorDeviceClass.WINDOW,
            BinarySensorDeviceClass.MOVING,
        )
        or any(unique_id.endswith(s) for s in ALL_DEVICE_CLASS_SUFFIXES if s != "-motion")
    ):
        raw = after_mac.replace("25-", "", 1) if after_mac.startswith("25-") else after_mac
        return "25", _strip_class_suffix(raw)
    if "-motion" in unique_id or entry.original_device_class == BinarySensorDeviceClass.MOTION:
        where = after_mac.replace("-motion", "")
        parts = where.split("-", 1)
        return "1", parts[-1] if len(parts) > 1 else where
    return None


_classify_registry_entry = classify_binary_sensor_registry_entry


def _primary(where: str) -> str:
    clean = where.split("-")[-1]
    return normalize_where(where) or normalize_where(clean) or clean


_bus_interface_from_message = bus_interface_from_message


def _contact_address(message) -> Address | None:  # type: ignore
    # An F455 pads the contact number (301), every other gateway and the WHO 25 PDF do not (31).
    # OWNd leaves F422 routing (``301#4#01``) in ``where_param == ["4", "01"]`` for WHO 25.
    if not message.where:
        return None
    valid_iface, interface = _bus_interface_from_message(message)
    if not valid_iface:
        return None
    return Address(canonical_contact_where(_primary(str(message.where))), interface)


def _aux_address(message) -> Address | None:  # type: ignore
    if not message.channel:
        return None
    raw_params = where_param(message)
    if isinstance(raw_params, (list, tuple)) and raw_params:
        return None
    return Address(str(message.channel))


def _motion_address(message) -> Address | None:  # type: ignore
    """Motion / PIR frames of a WHO=1 sensor; ``None`` for anything else on WHO=1."""
    # Do not check ``message.is_sensor`` here: OWNLightingEvent.is_sensor is also True
    # for WHO 1 illuminance (DIMENSION 6 / MESSAGE_TYPE_ILLUMINANCE), which is owned by
    # sensor.py; matching it here spawns a phantom Motion Sensor that races with the
    # Illuminance device name and queues unsolicited PIR sensitivity/timeout polls.
    is_motion = (
        getattr(message, "motion", False) is True
        or getattr(message, "message_type", None) in (MESSAGE_TYPE_MOTION, MESSAGE_TYPE_MOTION_TIMEOUT, MESSAGE_TYPE_PIR_SENSITIVITY)
        or getattr(message, "dimension", None) in (5, 7)
        or getattr(message, "_state", None) == 34
        or is_motion_end(message)
    )
    raw_where = getattr(message, "where", None)
    where_str = str(raw_where).strip() if raw_where is not None else ""
    if not is_motion or not where_str:
        return None
    valid_iface, interface = _bus_interface_from_message(message)
    if not valid_iface:
        return None
    return Address(_primary(where_str), interface)


async def async_unload_entry(hass: HomeAssistant, config_entry: ConfigEntry) -> bool:  # type: ignore
    runtime = config_entry.runtime_data

    if PLATFORM not in runtime.platforms:
        return True

    _configured_binary_sensors = runtime.platforms[PLATFORM]

    for _binary_sensor in list(_configured_binary_sensors.keys()):
        del runtime.platforms[PLATFORM][_binary_sensor]


class _MyHOMEInvertibleBinarySensor(MyHOMEEntity, BinarySensorEntity):
    """Shared base for WHO=25 dry contacts and WHO=9 auxiliary binary sensors."""

    _name_from_device_class = True
    _inverted: bool
    _raw_is_on: bool | None

    async def async_restore_last_state(self, last_state: typing.Any) -> None:
        """Restore binary sensor state and reconstruct raw contact polarity."""
        if (
            self._raw_is_on is None
            and last_state is not None
            and last_state.state not in ("unknown", "unavailable")
        ):
            restored_on = last_state.state == "on"
            self._attr_is_on = restored_on
            self._raw_is_on = restored_on != self._inverted

    @callback
    def set_inverted(self, inverted: bool) -> None:
        """Update contact inversion polarity and recompute live state."""
        self._inverted = inverted
        if self._raw_is_on is not None:
            self._attr_is_on = self._raw_is_on != self._inverted
        self._publish_state()

    @callback
    def _handle_inverted_signal(
        self, who: str, canonical_where: str, inverted: bool
    ) -> None:
        """Apply an invert-state toggle targeting this contact."""
        if (
            who == self._who
            and canonical_where == binary_sensor_canonical_where(self._who, self._where)
        ):
            self.set_inverted(inverted)

    async def async_added_to_hass(self) -> None:
        """When entity is added to hass."""
        self._register_entity_ref(self._attr_device_class)  # type: ignore
        target_hass = self.hass or self._hass
        if target_hass is not None:
            gw_mac = str(self._gateway_handler.mac)
            self.async_on_remove(
                async_dispatcher_connect(
                    target_hass,
                    SIGNAL_BINARY_SENSOR_INVERTED.format(gw_mac),
                    self._handle_inverted_signal,
                )
            )
            async_dispatcher_send(
                target_hass,
                SIGNAL_NEW_BINARY_SENSOR.format(gw_mac),
                {
                    "who": self._who,
                    "where": self._where,
                    "device_id": self._device_id,
                    "name": self._device_name,
                    "manufacturer": self._manufacturer,
                    "model": self._model,
                    "inverted": self._inverted,
                },
            )
        await super().async_added_to_hass()

    async def async_will_remove_from_hass(self) -> None:
        """When entity is removed from hass."""
        self._unregister_entity_ref(self._attr_device_class)  # type: ignore

    @callback
    def handle_event(self, message: typing.Any) -> None:
        """Handle an event message."""
        LOGGER.debug(
            "%s %s",
            self._gateway_handler.log_id,
            message.human_readable_log,
        )
        self._raw_is_on = bool(message.is_on)
        self._attr_is_on = self._raw_is_on != self._inverted
        self._publish_state()


class MyHOMEDryContact(_MyHOMEInvertibleBinarySensor):
    def __init__(  # type: ignore
        self,
        hass,
        name: str,
        entity_name: str | None,
        device_id: str,
        who: str,
        where: str,
        inverted: bool,
        device_class: str,
        manufacturer: str,
        model: str,
        gateway: MyHOMEGatewayHandler,
    ):
        norm_where = normalize_where(where) or where
        super().__init__(
            hass=hass,
            name=name,
            platform=PLATFORM,
            device_id=device_id,
            who=who,
            where=norm_where,
            manufacturer=manufacturer,
            model=model,
            gateway=gateway,
            entity_name=entity_name,
        )

        self._inverted = inverted
        self._raw_is_on = None

        self._attr_device_class = device_class  # type: ignore

        self._attr_unique_id = f"{gateway.mac}-{self._device_id}-{self._attr_device_class}"

        self._attr_is_on = False
        sensor_attr = f"({self._where[0]}){self._where[1:]}" if self._where else ""
        self._attr_extra_state_attributes = {"Sensor": sensor_attr}

    async def async_update(self) -> None:
        """Update the entity.

        Only used by the generic entity update service.
        """
        await self._gateway_handler.send_status_request(
            OWNDryContactCommand.status(binary_sensor_canonical_where("25", self._where))
        )


class MyHOMEAuxiliary(_MyHOMEInvertibleBinarySensor):
    def __init__(  # type: ignore
        self,
        hass,
        name: str,
        entity_name: str | None,
        device_id: str,
        who: str,
        where: str,
        inverted: bool,
        device_class: str | None,
        manufacturer: str,
        model: str,
        gateway: MyHOMEGatewayHandler,
    ):
        super().__init__(
            hass=hass,
            name=name,
            platform=PLATFORM,
            device_id=device_id,
            who=who,
            where=where,
            manufacturer=manufacturer,
            model=model,
            gateway=gateway,
            entity_name=entity_name,
        )

        self._inverted = inverted
        self._raw_is_on = None

        self._attr_device_class = device_class  # type: ignore
        if not device_class and not entity_name:
            self._attr_name = None  # no class to name it after: the entity is the device

        if self._attr_device_class:
            self._attr_unique_id = f"{gateway.mac}-{self._device_id}-{self._attr_device_class}"
        else:
            self._attr_unique_id = f"{gateway.mac}-{self._device_id}"

        self._attr_is_on = False
        self._attr_extra_state_attributes = {"Auxiliary channel": self._where}

    async def async_update(self) -> None:
        """AUX sensors are read only and cannot be queried, no async_update implementation."""


class MyHOMEMotionSensor(MyHOMEEntity, BinarySensorEntity):
    _name_from_device_class = True

    def __init__(  # type: ignore
        self,
        hass,
        name: str,
        entity_name: str | None,
        device_id: str,
        who: str,
        where: str,
        inverted: bool,
        device_class: str,
        manufacturer: str,
        model: str,
        gateway: MyHOMEGatewayHandler,
    ):
        norm_where = binary_sensor_canonical_where("1", where) or where
        super().__init__(
            hass=hass,
            name=name,
            platform=PLATFORM,
            device_id=device_id,
            who=who,
            where=norm_where,
            manufacturer=manufacturer,
            model=model,
            gateway=gateway,
            entity_name=entity_name,
        )

        self._inverted = inverted
        self._attr_force_update = False
        self._last_updated: datetime | None = None
        self._last_motion_at: datetime | None = None
        self._cleared_by_timeout_at: datetime | None = None
        self._last_timeout_query_at: datetime | None = None
        self._supports_motion_end: bool = False
        self._timeout = DEFAULT_MOTION_TIMEOUT
        self._timeout_unsub: CALLBACK_TYPE | None = None

        self._attr_device_class = device_class  # type: ignore

        self._attr_unique_id = f"{gateway.mac}-{self._device_id}-{self._attr_device_class}"
        self._attr_should_poll = False
        self._attr_is_on = False
        where_str = str(self._where).partition(BUS_ROUTING)[0]
        half = len(where_str) // 2
        a_val = where_str[:half] if half > 0 else "0"
        pl_val = where_str[half:] if half > 0 else where_str
        self._attr_extra_state_attributes = {
            "A": a_val,
            "PL": pl_val,
            "Timeout": self._timeout.total_seconds(),
            "Sensitivity": PIR_SENSITIVITY[1],
        }

    @property
    def _effective_timeout(self) -> timedelta:
        """Hold duration before fallback clear (extended once WHAT 39 is known)."""
        if self._supports_motion_end:
            return max(self._timeout, MOTION_END_SAFETY_TIMEOUT)
        return self._timeout

    @callback
    def _cancel_timeout_timer(self) -> None:
        """Cancel any pending motion-clear timer."""
        if self._timeout_unsub is not None:
            self._timeout_unsub()
            self._timeout_unsub = None

    @callback
    def _schedule_timeout_timer(self, delay_seconds: float | None = None) -> None:
        """Schedule (or reschedule) the fallback off-timer."""
        self._cancel_timeout_timer()
        target_hass = self.hass or self._hass
        if target_hass is None:
            return
        delay = max(
            0.0,
            self._effective_timeout.total_seconds() if delay_seconds is None else delay_seconds,
        )
        self._timeout_unsub = async_call_later(
            target_hass,
            delay,
            HassJob(
                self._async_timeout_expired,
                f"myhome motion timeout {self._where}",
                cancel_on_shutdown=True,
            ),
        )

    @callback
    def _sync_active_timeout(self, now: datetime, *, schedule_on_expire: bool = False) -> None:
        """Clear immediately if the active hold has elapsed, otherwise re-arm for the remainder."""
        if self._attr_is_on == self._inverted or self._last_updated is None:
            return
        remaining = (self._last_updated + self._effective_timeout) - now
        if remaining <= timedelta(0):
            self._cancel_timeout_timer()
            self._attr_is_on = self._inverted
            self._last_updated = now
            self._cleared_by_timeout_at = now
            if schedule_on_expire:
                self.async_schedule_update_ha_state()
        else:
            self._schedule_timeout_timer(remaining.total_seconds())

    @callback
    def _write_motion_state(self, *, force_update: bool = False) -> None:
        """Publish the current motion sensor state to Home Assistant."""
        self._attr_force_update = force_update
        try:
            self.async_write_ha_state()
        except Exception as err:
            LOGGER.debug("Direct state write deferred for %s: %s", self._where, err)
            try:
                self.async_schedule_update_ha_state()
            except Exception as sched_err:
                LOGGER.debug("Scheduled state update skipped for %s: %s", self._where, sched_err)
        self._attr_force_update = False

    @callback
    def _async_timeout_expired(self, _now: Any = None) -> None:
        """Clear motion when the occupancy hold timer expires without WHAT 39 (#941)."""
        self._timeout_unsub = None
        if self._attr_is_on == self._inverted:
            return
        now = dt_util.utcnow()
        LOGGER.debug(
            "%s Motion sensor %s cleared after %ss timeout expiry",
            self._gateway_handler.log_id,
            self._where,
            self._effective_timeout.total_seconds(),
        )
        self._attr_is_on = self._inverted
        self._last_updated = now
        self._cleared_by_timeout_at = now
        self._write_motion_state()

    async def async_restore_last_state(self, last_state: typing.Any) -> None:
        """Restore motion sensor state and immediately arm or expire its hold timer."""
        if last_state is not None and last_state.state not in ("unknown", "unavailable"):
            self._attr_is_on = last_state.state == STATE_ON
            self._last_updated = last_state.last_updated
            attrs = getattr(last_state, "attributes", None)
            if isinstance(attrs, Mapping) and attrs.get("Supports motion end") is True:
                self._supports_motion_end = True
                self._attr_extra_state_attributes["Supports motion end"] = True
            self._sync_active_timeout(dt_util.utcnow(), schedule_on_expire=True)

    async def async_added_to_hass(self) -> None:
        """When entity is added to hass."""
        self._register_entity_ref(self._attr_device_class)  # type: ignore
        self.async_on_remove(self._cancel_timeout_timer)
        await self._gateway_handler.send_status_request(OWNLightingCommand.get_pir_sensitivity(self._where))
        await self._gateway_handler.send_status_request(OWNLightingCommand.get_motion_timeout(self._where))
        self._last_timeout_query_at = dt_util.utcnow()
        await super().async_added_to_hass()

    async def async_will_remove_from_hass(self) -> None:
        """When entity is removed from hass."""
        self._cancel_timeout_timer()
        self._unregister_entity_ref(self._attr_device_class)  # type: ignore

    async def async_update(self) -> None:
        """Clear expired motion state or arm the timer for restored active state."""
        self._sync_active_timeout(dt_util.utcnow(), schedule_on_expire=True)

    @callback
    def _maybe_adapt_stale_timeout(self, now: datetime) -> None:
        """Adapt _timeout and re-query DIMENSION 7 if a WHAT 34 repeat arrives right after a timer clear."""
        if (
            self._supports_motion_end
            or self._cleared_by_timeout_at is None
            or self._last_motion_at is None
        ):
            return
        since_clear = now - self._cleared_by_timeout_at
        observed_cycle = now - self._last_motion_at
        if timedelta(0) < since_clear <= STALE_TIMEOUT_RETRIGGER_WINDOW and observed_cycle > self._timeout:
            previous_timeout = self._timeout.total_seconds()
            self._timeout = observed_cycle + MOTION_TIMEOUT_GRACE
            self._attr_extra_state_attributes["Timeout"] = self._timeout.total_seconds()
            LOGGER.debug(
                "%s Motion sensor %s adapted timeout from %ss to %ss after post-clear retrigger",
                self._gateway_handler.log_id,
                self._where,
                previous_timeout,
                self._timeout.total_seconds(),
            )
            target_hass = self.hass or self._hass
            if target_hass is not None and (
                self._last_timeout_query_at is None
                or (now - self._last_timeout_query_at) >= STALE_TIMEOUT_RETRIGGER_WINDOW
            ):
                self._last_timeout_query_at = now
                target_hass.async_create_task(
                    self._gateway_handler.send_status_request(
                        OWNLightingCommand.get_motion_timeout(self._where)
                    )
                )

    @callback
    def handle_event(self, message: OWNLightingEvent) -> None:
        """Handle an event message."""
        motion_end = is_motion_end(message)
        if not motion_end and message.message_type not in [
            MESSAGE_TYPE_MOTION,
            MESSAGE_TYPE_MOTION_TIMEOUT,
            MESSAGE_TYPE_PIR_SENSITIVITY,
        ]:
            return True  # type: ignore

        LOGGER.debug(
            "%s %s",
            self._gateway_handler.log_id,
            message.human_readable_log,
        )
        now = dt_util.utcnow()
        if motion_end:
            # WHAT 39: the detector's occupancy timer ran out (#777).
            self._supports_motion_end = True
            self._attr_extra_state_attributes["Supports motion end"] = True
            self._cancel_timeout_timer()
            self._cleared_by_timeout_at = None
            self._attr_is_on = self._inverted
            self._last_updated = now
        elif message.message_type == MESSAGE_TYPE_MOTION and message.motion:
            self._maybe_adapt_stale_timeout(now)
            self._cleared_by_timeout_at = None
            self._attr_is_on = message.motion != self._inverted
            self._last_updated = now
            self._last_motion_at = now
            self._schedule_timeout_timer()
        elif message.message_type == MESSAGE_TYPE_MOTION_TIMEOUT:
            if message.motion_timeout is not None:
                self._timeout = message.motion_timeout + MOTION_TIMEOUT_GRACE
                self._attr_extra_state_attributes["Timeout"] = self._timeout.total_seconds()
                self._sync_active_timeout(now)
        elif message.message_type == MESSAGE_TYPE_PIR_SENSITIVITY:
            if message.pir_sensitivity is not None and 0 <= message.pir_sensitivity < len(PIR_SENSITIVITY):
                self._attr_extra_state_attributes["Sensitivity"] = PIR_SENSITIVITY[message.pir_sensitivity]
        self._write_motion_state(force_update=True)
