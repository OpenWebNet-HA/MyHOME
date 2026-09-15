"""Support for MyHome binary sensors (dry contacts and motion sensors)."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from homeassistant.components.binary_sensor import (
    DOMAIN as PLATFORM,
)
from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
)
from homeassistant.const import (
    CONF_MAC,
    CONF_NAME,
    STATE_ON,
)
from homeassistant.core import callback
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity import Entity
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
    CONF_DEVICE_CLASS,
    CONF_DEVICE_MODEL,
    CONF_ENTITY_NAME,
    CONF_INVERTED,
    CONF_MANUFACTURER,
    CONF_WHERE,
    CONF_WHO,
    LOGGER,
    normalize_where,
)
from .discovery import Address, DeviceContext, PlatformDiscovery
from .gateway import MyHOMEGatewayHandler
from .myhome_device import MyHOMEEntity

PARALLEL_UPDATES = 0

SCAN_INTERVAL = timedelta(seconds=30)
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


async def async_setup_entry(hass, config_entry, async_add_entities):
    """Set up the binary sensors of a gateway: dry contacts (WHO=25), auxiliary
    channels (WHO=9) and motion sensors (WHO=1), each restored from the registry,
    then created from myhome.yaml, then discovered from the bus.

    Unique ids carry the device class (``{mac}-25-31-opening``), and the same
    contact may be spelled ``0031`` or ``31``: every entity is remembered under
    all of its spellings, and frames are routed under all of theirs.
    """
    runtime = config_entry.runtime_data
    gateway = runtime.gateway
    mac = config_entry.data[CONF_MAC]
    configured = runtime.platforms.get(PLATFORM, {})

    def registry_address(who: str):
        def address_of(entry) -> Address | None:
            classified = _classify_registry_entry(entry, gateway.mac, mac)
            if classified is None or classified[0] != who:
                return None
            return Address(classified[1])

        return address_of

    def duplicate(entry, ctx: DeviceContext) -> bool:
        # A second registry entry for a contact already restored under another spelling
        return any(key in ctx_discovery[ctx.who].known for key in _spellings(ctx.address.where))

    ctx_discovery: dict[str, PlatformDiscovery] = {}

    # ── WHO 25: dry contacts ────────────────────────────────────────────
    def build_dry_contact(ctx: DeviceContext) -> MyHOMEDryContact:
        if ctx.source == "yaml":
            cfg, where = ctx.cfg, ctx.address.where
            device_id, device_class = ctx.config_id or ctx.key, cfg.get(CONF_DEVICE_CLASS) or cfg.get("device_class")
            name, model = cfg[CONF_NAME], "Dry Contact"
        elif ctx.source == "registry":
            candidate = ctx.address.where
            clean_candidate = candidate.split("-")[-1]
            cfg = _first_config(
                configured, f"25-{candidate}", candidate, clean_candidate,
                normalize_where(candidate), normalize_where(clean_candidate),
            )
            where = str(cfg.get(CONF_WHERE, candidate))
            norm_where, clean_norm = normalize_where(where), normalize_where(where.split("-")[-1])
            device_id = candidate if candidate in configured else (norm_where or clean_norm or where.split("-")[-1])
            device_class = cfg.get(CONF_DEVICE_CLASS, getattr(ctx.registry_entry, "original_device_class", None) or BinarySensorDeviceClass.OPENING)
            name, model = cfg.get(CONF_NAME, f"Dry Contact {clean_norm or where.split('-')[-1]}"), "Dry Contact Interface"
        else:
            cfg, where = {}, ctx.address.where
            device_id, device_class = where, BinarySensorDeviceClass.OPENING
            name, model = f"Dry Contact {where}", "Dry Contact Interface"
        contact = MyHOMEDryContact(
            hass=hass,
            device_id=device_id,
            who="25",
            where=normalize_where(where) or where,
            name=name,
            entity_name=cfg.get(CONF_ENTITY_NAME),
            inverted=cfg.get(CONF_INVERTED, False),
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
            inverted=cfg.get(CONF_INVERTED, False),
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
            cfg, where, device_id = ctx.cfg, ctx.address.where, ctx.config_id or ctx.key
            name = cfg[CONF_NAME]
        elif ctx.source == "registry":
            raw = ctx.address.where
            clean_raw = raw.split("-")[-1]
            norm_raw, clean_norm = normalize_where(raw), normalize_where(clean_raw)
            cfg = _first_config(configured, f"1-{norm_raw}", f"1-{raw}", norm_raw, raw, clean_norm, clean_raw)
            where = str(cfg.get(CONF_WHERE, norm_raw or raw))
            device_id = norm_raw or clean_norm or clean_raw
            name = cfg.get(CONF_NAME, f"Motion Sensor {clean_norm or clean_raw}")
        else:
            cfg, where, device_id = {}, ctx.address.where, ctx.address.where
            name = f"Motion Sensor {where}"
        motion = MyHOMEMotionSensor(
            hass=hass,
            device_id=device_id,
            who="1",
            where=normalize_where(where) or where,
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

    def yaml_who(who: int, device_class=None):
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
        keys = [ctx.key, ctx.config_id or "", *_spellings(ctx.address.where)]
        cfg_where = ctx.cfg.get(CONF_WHERE) if ctx.source != "bus" else None
        if cfg_where:
            keys.extend(_spellings(str(cfg_where)))
        return [k for k in keys if k]

    def route_keys(message, address: Address | None) -> list[str]:
        return _spellings(address.where) if address is not None else []

    common = dict(hass=hass, config_entry=config_entry, async_add_entities=async_add_entities, platform=PLATFORM)
    ctx_discovery["25"] = PlatformDiscovery(
        who="25", event_type=OWNDryContactEvent, build=build_dry_contact,
        registry_address=registry_address("25"), reject_registry_entry=duplicate, accept=yaml_who(25),
        known_keys=known_keys, route_keys=route_keys, address=_contact_address, **common,
    )
    ctx_discovery["9"] = PlatformDiscovery(
        who="9", event_type=OWNAuxEvent, build=build_auxiliary,
        registry_address=registry_address("9"), reject_registry_entry=duplicate, accept=yaml_who(9),
        known_keys=known_keys, route_keys=route_keys, address=_aux_address, **common,
    )
    ctx_discovery["1"] = PlatformDiscovery(
        who="1", event_type=OWNLightingEvent, build=build_motion,
        registry_address=registry_address("1"), reject_registry_entry=duplicate,
        accept=yaml_who(1, BinarySensorDeviceClass.MOTION),
        known_keys=known_keys, route_keys=route_keys, address=_motion_address, **common,
    )

    entities: list[Entity] = []
    for discovery in ctx_discovery.values():
        entities.extend(discovery.start(listen=False, add=False))
    if entities:
        async_add_entities(entities)

    @callback
    def _handle_binary_sensor_message(msg):
        """Forward incoming bus messages to binary sensor entities."""
        for discovery in ctx_discovery.values():
            discovery.handle_message(msg)

    config_entry.async_on_unload(
        async_dispatcher_connect(hass, f"myhome_message_{mac}", _handle_binary_sensor_message)
    )
    return True


def _spellings(where: str) -> list[str]:
    """``0031`` may also appear as ``31``, and a legacy ``25-0031`` as either."""
    clean = where.split("-")[-1]
    return list(dict.fromkeys(k for k in (where, normalize_where(where), clean, normalize_where(clean)) if k))


def _first_config(configured: dict[str, Any], *keys: str) -> dict[str, Any]:
    for key in keys:
        cfg = configured.get(key)
        if cfg:
            return dict(cfg)
    return {}


def _strip_class_suffix(candidate: str) -> str:
    for suffix in ALL_DEVICE_CLASS_SUFFIXES:
        if candidate.endswith(suffix):
            return candidate[: -len(suffix)]
    return candidate


def _classify_registry_entry(entry, gateway_mac: str, entry_mac: str) -> tuple[str, str] | None:
    """``(who, candidate id)`` of a registry entry, from its unique id or device class.

    Auxiliary channels first (an aux channel may carry the motion class), then
    dry contacts, then motion sensors.
    """
    unique_id = entry.unique_id
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


def _primary(where: str) -> str:
    clean = where.split("-")[-1]
    return normalize_where(where) or normalize_where(clean) or clean


def _contact_address(message) -> Address | None:
    return Address(_primary(str(message.where)))


def _aux_address(message) -> Address | None:
    return Address(str(message.channel))


def _motion_address(message) -> Address | None:
    """Motion / PIR frames of a WHO=1 sensor; ``None`` for anything else on WHO=1."""
    is_motion = (
        getattr(message, "is_sensor", False) is True
        or getattr(message, "motion", False) is True
        or getattr(message, "message_type", None) in (MESSAGE_TYPE_MOTION, MESSAGE_TYPE_MOTION_TIMEOUT, MESSAGE_TYPE_PIR_SENSITIVITY)
        or getattr(message, "dimension", None) in (5, 7)
        or getattr(message, "_state", None) == 34
    )
    if not is_motion or getattr(message, "where", None) is None:
        return None
    return Address(_primary(str(message.where)))


async def async_unload_entry(hass, config_entry):
    runtime = config_entry.runtime_data

    if PLATFORM not in runtime.platforms:
        return True

    _configured_binary_sensors = runtime.platforms[PLATFORM]

    for _binary_sensor in list(_configured_binary_sensors.keys()):
        del runtime.platforms[PLATFORM][_binary_sensor]


class MyHOMEDryContact(MyHOMEEntity, BinarySensorEntity):
    _name_from_device_class = True

    def __init__(
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

        self._attr_device_class = device_class

        self._attr_unique_id = f"{gateway.mac}-{self._device_id}-{self._attr_device_class}"

        self._attr_is_on = False
        sensor_attr = f"({self._where[0]}){self._where[1:]}" if self._where else ""
        self._attr_extra_state_attributes = {"Sensor": sensor_attr}

    async def async_restore_last_state(self, last_state) -> None:
        """Restore dry contact state."""
        if last_state is not None and last_state.state not in ("unknown", "unavailable"):
            self._attr_is_on = last_state.state == "on"

    async def async_added_to_hass(self):
        """When entity is added to hass."""
        self._register_entity_ref(self._attr_device_class)
        await super().async_added_to_hass()

    async def async_will_remove_from_hass(self):
        """When entity is removed from hass."""
        self._unregister_entity_ref(self._attr_device_class)

    async def async_update(self):
        """Update the entity.

        Only used by the generic entity update service.
        """
        await self._gateway_handler.send_status_request(OWNDryContactCommand.status(self._where))

    @callback
    def handle_event(self, message: OWNDryContactEvent):
        """Handle an event message."""
        LOGGER.debug(
            "%s %s",
            self._gateway_handler.log_id,
            message.human_readable_log,
        )
        self._attr_is_on = message.is_on != self._inverted
        self._publish_state()


class MyHOMEAuxiliary(MyHOMEEntity, BinarySensorEntity):
    _name_from_device_class = True

    def __init__(
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

        self._attr_device_class = device_class
        if not device_class and not entity_name:
            self._attr_name = None  # no class to name it after: the entity is the device

        if self._attr_device_class:
            self._attr_unique_id = f"{gateway.mac}-{self._device_id}-{self._attr_device_class}"
        else:
            self._attr_unique_id = f"{gateway.mac}-{self._device_id}"

        self._attr_is_on = False
        self._attr_extra_state_attributes = {"Auxiliary channel": self._where}

    async def async_restore_last_state(self, last_state) -> None:
        """Restore auxiliary state."""
        if last_state is not None and last_state.state not in ("unknown", "unavailable"):
            self._attr_is_on = last_state.state == "on"

    async def async_added_to_hass(self):
        """When entity is added to hass."""
        self._register_entity_ref(self._attr_device_class)
        await super().async_added_to_hass()

    async def async_will_remove_from_hass(self):
        """When entity is removed from hass."""
        self._unregister_entity_ref(self._attr_device_class)

    async def async_update(self):
        """AUX sensors are read only and cannot be queried, no async_update implementation."""

    @callback
    def handle_event(self, message: OWNDryContactEvent):
        """Handle an event message."""
        LOGGER.debug(
            "%s %s",
            self._gateway_handler.log_id,
            message.human_readable_log,
        )
        self._attr_is_on = message.is_on != self._inverted
        self._publish_state()


class MyHOMEMotionSensor(MyHOMEEntity, BinarySensorEntity):
    _name_from_device_class = True

    def __init__(
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
        self._attr_force_update = False
        self._last_updated = None
        self._timeout = timedelta(seconds=315)

        self._attr_device_class = device_class

        self._attr_unique_id = f"{gateway.mac}-{self._device_id}-{self._attr_device_class}"
        self._attr_should_poll = True
        self._attr_is_on = False
        where_str = str(self._where)
        half = len(where_str) // 2
        a_val = where_str[:half] if half > 0 else "0"
        pl_val = where_str[half:] if half > 0 else where_str
        self._attr_extra_state_attributes = {
            "A": a_val,
            "PL": pl_val,
            "Timeout": self._timeout.total_seconds(),
            "Sensitivity": PIR_SENSITIVITY[1],
        }

    async def async_restore_last_state(self, last_state) -> None:
        """Restore motion sensor state."""
        if last_state is not None and last_state.state not in ("unknown", "unavailable"):
            self._attr_is_on = last_state.state == STATE_ON
            self._last_updated = last_state.last_updated

    async def async_added_to_hass(self):
        """When entity is added to hass."""
        self._register_entity_ref(self._attr_device_class)
        await self._gateway_handler.send_status_request(OWNLightingCommand.get_pir_sensitivity(self._where))
        await self._gateway_handler.send_status_request(OWNLightingCommand.get_motion_timeout(self._where))
        await super().async_added_to_hass()

    async def async_will_remove_from_hass(self):
        """When entity is removed from hass."""
        self._unregister_entity_ref(self._attr_device_class)

    async def async_update(self):
        """Update the entity.

        Only used by the generic entity update service.
        """
        if self._attr_is_on and self._last_updated and self._last_updated + self._timeout < datetime.now(timezone.utc):
            self._attr_is_on = False
            self._last_updated = datetime.now(timezone.utc)
            self.async_schedule_update_ha_state()

    @callback
    def handle_event(self, message: OWNLightingEvent):
        """Handle an event message."""
        if message.message_type not in [
            MESSAGE_TYPE_MOTION,
            MESSAGE_TYPE_MOTION_TIMEOUT,
            MESSAGE_TYPE_PIR_SENSITIVITY,
        ]:
            return True

        LOGGER.debug(
            "%s %s",
            self._gateway_handler.log_id,
            message.human_readable_log,
        )
        if message.message_type == MESSAGE_TYPE_MOTION and message.motion:
            self._attr_is_on = message.motion != self._inverted
        elif message.message_type == MESSAGE_TYPE_MOTION_TIMEOUT:
            self._timeout = message.motion_timeout + timedelta(seconds=15)
            self._attr_extra_state_attributes["Timeout"] = self._timeout.total_seconds()
        elif message.message_type == MESSAGE_TYPE_PIR_SENSITIVITY:
            self._attr_extra_state_attributes["Sensitivity"] = PIR_SENSITIVITY[message.pir_sensitivity]
        self._last_updated = datetime.now(timezone.utc)
        self._attr_force_update = True
        try:
            self.async_write_ha_state()
        except Exception:
            try:
                self.async_schedule_update_ha_state()
            except Exception:
                pass
        self._attr_force_update = False
