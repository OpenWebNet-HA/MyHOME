"""Support for MyHome lights."""
import asyncio

from homeassistant.components.light import (
    ATTR_BRIGHTNESS,
    ATTR_BRIGHTNESS_PCT,
    ATTR_FLASH,
    ATTR_TRANSITION,
    FLASH_LONG,
    FLASH_SHORT,
    ColorMode,
    LightEntity,
    LightEntityFeature,
)
from homeassistant.components.light import (
    DOMAIN as PLATFORM,
)
from homeassistant.const import (
    CONF_MAC,
    CONF_NAME,
)
from homeassistant.core import callback
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.dispatcher import async_dispatcher_connect, async_dispatcher_send
from OWNd.message import (
    OWNLightingCommand,
    OWNLightingEvent,
)

from .const import (
    CONF_BUS_INTERFACE,
    CONF_DEVICE_MODEL,
    CONF_DIMMABLE,
    CONF_ENTITY,
    CONF_ENTITY_NAME,
    CONF_ICON,
    CONF_ICON_ON,
    CONF_MANUFACTURER,
    CONF_PLATFORMS,
    CONF_TRANSITION_MODE,
    CONF_WHERE,
    CONF_WHO,
    CONF_WORKER_COUNT,
    DEFAULT_TRANSITION_MODE,
    DOMAIN,
    LOGGER,
    SOFTWARE_TRANSITION_MAX_STEPS,
    SOFTWARE_TRANSITION_MIN_STEPS,
    SOFTWARE_TRANSITION_STEP_INTERVAL,
    TRANSITION_MODE_AUTO,
    TRANSITION_MODE_NATIVE,
    TRANSITION_MODE_SOFTWARE,
    normalize_where,
)
from .gateway import MyHOMEGatewayHandler
from .myhome_device import MyHOMEEntity


async def async_setup_entry(hass, config_entry, async_add_entities):
    """Set up the MyHOME light platform dynamically via Discovery."""
    known_lights = set()

    # Restore previously discovered entities from the Entity Registry so they
    # are available immediately on restart, even before the gateway responds.
    try:
        entity_registry = er.async_get(hass)
        existing_entries = er.async_entries_for_config_entry(entity_registry, config_entry.entry_id)
    except Exception:
        entity_registry = None
        existing_entries = []
    restored_lights = []

    gateway = hass.data[DOMAIN][config_entry.data[CONF_MAC]][CONF_ENTITY]
    _configured_lights = hass.data[DOMAIN][config_entry.data[CONF_MAC]].get(CONF_PLATFORMS, {}).get(PLATFORM, {})

    # Collect all WHERE addresses configured or registered as switches so dynamic discovery
    # of WHO=1 never auto-creates a duplicate Light entity for switch/outlet devices.
    switch_wheres = set()
    _configured_switches = hass.data[DOMAIN][config_entry.data[CONF_MAC]].get(CONF_PLATFORMS, {}).get("switch", {})
    for dev_id, sw_cfg in _configured_switches.items():
        sw_where = str(sw_cfg.get(CONF_WHERE, dev_id))
        sw_clean = sw_where.split("-")[-1]
        sw_interface = sw_cfg.get(CONF_BUS_INTERFACE) if CONF_BUS_INTERFACE in sw_cfg else sw_cfg.get("interface")
        sw_dev_where = f"{sw_clean}#4#{sw_interface}" if sw_interface else str(sw_clean)
        switch_wheres.add(str(dev_id))
        switch_wheres.add(str(sw_where))
        switch_wheres.add(sw_dev_where)
        switch_wheres.add(sw_clean)

    for entry in existing_entries:
        if entry.domain == "switch":
            unique_id = entry.unique_id
            after_mac = unique_id.replace(f"{gateway.mac}-", "", 1).replace(f"{config_entry.data[CONF_MAC]}-", "", 1)
            parts_who = after_mac.split("-", 1)
            dev_id = parts_who[-1] if len(parts_who) > 1 else after_mac
            clean_sw = dev_id.split("#4#")[0].split("-")[-1]
            switch_wheres.add(dev_id)
            switch_wheres.add(clean_sw)

    # Collect all WHERE addresses configured or registered as sensors/binary_sensors so dynamic discovery
    # of WHO=1 never auto-creates a duplicate Light entity for motion/illuminance sensors.
    sensor_wheres = set()
    _configured_bs = hass.data[DOMAIN][config_entry.data[CONF_MAC]].get(CONF_PLATFORMS, {}).get("binary_sensor", {})
    for dev_id, bs_cfg in _configured_bs.items():
        if str(bs_cfg.get(CONF_WHO, "25")) == "1":
            bs_where = str(bs_cfg.get(CONF_WHERE, dev_id))
            bs_clean = bs_where.split("-")[-1]
            sensor_wheres.add(str(dev_id))
            sensor_wheres.add(str(bs_where))
            sensor_wheres.add(bs_clean)
            sensor_wheres.add(normalize_where(bs_where))
            sensor_wheres.add(normalize_where(bs_clean))

    _configured_s = hass.data[DOMAIN][config_entry.data[CONF_MAC]].get(CONF_PLATFORMS, {}).get("sensor", {})
    for dev_id, s_cfg in _configured_s.items():
        if str(s_cfg.get(CONF_WHO, "1")) == "1":
            s_where = str(s_cfg.get(CONF_WHERE, dev_id))
            s_clean = s_where.split("-")[-1]
            sensor_wheres.add(str(dev_id))
            sensor_wheres.add(str(s_where))
            sensor_wheres.add(s_clean)
            sensor_wheres.add(normalize_where(s_where))
            sensor_wheres.add(normalize_where(s_clean))

    for entry in existing_entries:
        if entry.domain in ("binary_sensor", "sensor"):
            unique_id = entry.unique_id
            after_mac = unique_id.replace(f"{gateway.mac}-", "", 1).replace(f"{config_entry.data[CONF_MAC]}-", "", 1)
            parts_who = after_mac.split("-", 1)
            if len(parts_who) > 1 and parts_who[0] == "1":
                dev_id = parts_who[1].split("-")[0]
                clean_s = dev_id.split("#4#")[0].split("-")[-1]
                sensor_wheres.add(dev_id)
                sensor_wheres.add(clean_s)
                sensor_wheres.add(normalize_where(dev_id))
                sensor_wheres.add(normalize_where(clean_s))
            elif "-motion" in unique_id or "-illuminance" in unique_id:
                # E.g. {mac}-{device_id}-{device_class}
                dev_id = after_mac.split("-")[0]
                clean_s = dev_id.split("#4#")[0].split("-")[-1]
                sensor_wheres.add(dev_id)
                sensor_wheres.add(clean_s)
                sensor_wheres.add(normalize_where(dev_id))
                sensor_wheres.add(normalize_where(clean_s))

    for entry in existing_entries:
        if entry.domain == PLATFORM:
            unique_id = entry.unique_id
            # unique_id format: "{mac}-{who}-{device_id}"
            # device_id is "{where}" or "{where}#4#{interface}"
            after_mac = unique_id.replace(f"{gateway.mac}-", "", 1).replace(f"{config_entry.data[CONF_MAC]}-", "", 1)
            # Strip the WHO prefix: "1-55" -> "55", "1-18#4#02" -> "18#4#02"
            parts_who = after_mac.split("-", 1)
            device_id = parts_who[-1] if len(parts_who) > 1 else after_mac
            if "#4#" in device_id:
                parts = device_id.split("#4#")
                where = parts[0]
                interface = parts[1] if len(parts) > 1 else None
            else:
                where = device_id
                interface = None

            clean_where = where.split('-')[-1]
            norm_where = normalize_where(where)
            clean_norm = normalize_where(clean_where)
            if (
                clean_where in switch_wheres
                or device_id in switch_wheres
                or where in switch_wheres
                or clean_where in sensor_wheres
                or device_id in sensor_wheres
                or where in sensor_wheres
                or norm_where in sensor_wheres
                or clean_norm in sensor_wheres
            ):
                # Ghost light erroneously created in a previous session for a switch or sensor device
                if entity_registry:
                    entity_registry.async_remove(entry.entity_id)
                continue

            cfg = _configured_lights.get(device_id) or _configured_lights.get(where) or _configured_lights.get(clean_where) or {}

            default_suffix = f"{clean_where}I{interface}" if interface else clean_where
            _customs = hass.data.get(DOMAIN, {}).get("customizations", {})
            _predicted_id = f"light.light_{default_suffix.lower().replace(' ', '_')}"
            _custom_entry = _customs.get(entry.entity_id, {}) or _customs.get(_predicted_id, {})
            _is_dimmable = cfg.get(CONF_DIMMABLE, _custom_entry.get("dimmable", False))
            _name = cfg.get(CONF_NAME, f"Light {default_suffix}")
            _entity_name = cfg.get(CONF_ENTITY_NAME)
            _icon = cfg.get(CONF_ICON)
            _icon_on = cfg.get(CONF_ICON_ON)
            _manufacturer = cfg.get(CONF_MANUFACTURER, "BTicino")
            _model = cfg.get(CONF_DEVICE_MODEL, "Lighting Device")

            _light = MyHOMELight(
                hass=hass,
                name=_name,
                entity_name=_entity_name,
                icon=_icon,
                icon_on=_icon_on,
                device_id=device_id,
                who="1",
                where=where,
                interface=interface,
                dimmable=_is_dimmable,
                manufacturer=_manufacturer,
                model=_model,
                gateway=gateway,
            )
            known_lights.add(device_id)
            restored_lights.append(_light)

    # Also instantiate any configured lights from myhome.yaml not yet in registry
    seen_configured_where = set()
    for dev_id, cfg in _configured_lights.items():
        where = str(cfg.get(CONF_WHERE, dev_id))
        interface = cfg.get(CONF_BUS_INTERFACE)
        device_where_id = f"{where}#4#{interface}" if interface else str(where)
        clean_where = where.split("-")[-1]
        clean_unique_id = f"{clean_where}#4#{interface}" if interface else clean_where
        default_suffix = f"{clean_where}I{interface}" if interface else clean_where

        if clean_unique_id in seen_configured_where or device_where_id in known_lights or dev_id in known_lights:
            continue
        if (
            clean_where in switch_wheres
            or device_where_id in switch_wheres
            or dev_id in switch_wheres
            or clean_where in sensor_wheres
            or device_where_id in sensor_wheres
            or dev_id in sensor_wheres
        ):
            continue
        seen_configured_where.add(clean_unique_id)

        _name = cfg.get(CONF_NAME, f"Light {default_suffix}")
        _light = MyHOMELight(
            hass=hass,
            name=_name,
            entity_name=cfg.get(CONF_ENTITY_NAME),
            icon=cfg.get(CONF_ICON),
            icon_on=cfg.get(CONF_ICON_ON),
            device_id=device_where_id,
            who=str(cfg.get(CONF_WHO, "1")),
            where=where,
            interface=interface,
            dimmable=cfg.get(CONF_DIMMABLE, False),
            manufacturer=cfg.get(CONF_MANUFACTURER, "BTicino"),
            model=cfg.get(CONF_DEVICE_MODEL, "Lighting Device"),
            gateway=gateway,
        )
        known_lights.add(device_where_id)
        known_lights.add(dev_id)
        if not interface:
            known_lights.add(clean_where)
        restored_lights.append(_light)

        # Signal button platform to create Lock/Unlock buttons
        async_dispatcher_send(
            hass,
            f"myhome_new_device_{config_entry.data[CONF_MAC]}",
            {
                "who": str(cfg.get(CONF_WHO, "1")),
                "where": where,
                "interface": interface,
                "name": _name,
                "device_id": device_where_id,
            },
        )

    if restored_lights:
        async_add_entities(restored_lights)

    @callback
    def async_add_light(message):
        """Add a light from a discovered message."""
        if getattr(message, "is_translation", None) is True:
            return

        if not hasattr(message, "where") or not message.where:
            return

        # Skip groups, areas and general for now, as they represent many physical devices
        if getattr(message, "is_group", False) or getattr(message, "is_area", False) or getattr(message, "is_general", False):
            return

        where = message.where
        clean_where = where.split('-')[-1]
        norm_where = normalize_where(where)
        clean_norm = normalize_where(clean_where)
        interface = getattr(message, "interface", None)
        unique_id = f"{where}#4#{interface}" if interface else str(where)

        # Ignore sensor messages (motion, illuminance, sensitivity, timeout)
        if (
            getattr(message, "is_sensor", False) is True
            or getattr(message, "motion", False) is True
            or isinstance(getattr(message, "illuminance", None), int)
            or getattr(message, "message_type", None) in (
                "motion_detected",
                "illuminance_value",
                "pir_sensitivity",
                "motion_timeout",
            )
            or getattr(message, "dimension", None) in (5, 6, 7)
            or getattr(message, "_state", None) == 34
        ):
            sensor_wheres.add(where)
            sensor_wheres.add(unique_id)
            sensor_wheres.add(clean_where)
            sensor_wheres.add(norm_where)
            sensor_wheres.add(clean_norm)
            if unique_id in known_lights:
                known_lights.remove(unique_id)
            async_dispatcher_send(hass, f"myhome_update_{config_entry.data[CONF_MAC]}_1_{unique_id}", message)
            if unique_id != where:
                async_dispatcher_send(hass, f"myhome_update_{config_entry.data[CONF_MAC]}_1_{where}", message)
            if norm_where != where:
                async_dispatcher_send(hass, f"myhome_update_{config_entry.data[CONF_MAC]}_1_{norm_where}", message)
            return

        if (
            clean_where in switch_wheres
            or unique_id in switch_wheres
            or where in switch_wheres
            or clean_where in sensor_wheres
            or unique_id in sensor_wheres
            or where in sensor_wheres
            or norm_where in sensor_wheres
            or clean_norm in sensor_wheres
        ):
            # Route to switch or sensor entities, do not auto-create a light entity
            async_dispatcher_send(hass, f"myhome_update_{config_entry.data[CONF_MAC]}_1_{unique_id}", message)
            if unique_id != where:
                async_dispatcher_send(hass, f"myhome_update_{config_entry.data[CONF_MAC]}_1_{where}", message)
            if norm_where != where:
                async_dispatcher_send(hass, f"myhome_update_{config_entry.data[CONF_MAC]}_1_{norm_where}", message)
            return

        if unique_id not in known_lights:
            # We found a new light!
            clean_where = where.split('-')[-1]
            default_suffix = f"{clean_where}I{interface}" if interface else clean_where
            cfg = _configured_lights.get(unique_id) or _configured_lights.get(where) or _configured_lights.get(clean_where) or {}

            _customs = hass.data.get(DOMAIN, {}).get("customizations", {})
            _predicted_id = f"light.light_{default_suffix.lower().replace(' ', '_')}"
            _custom_entry = _customs.get(_predicted_id, {})
            _is_dimmable = cfg.get(CONF_DIMMABLE, _custom_entry.get("dimmable", False))

            # Auto-detect dimmer from the first protocol message
            if not _is_dimmable:
                _is_dimmable = (
                    message.brightness is not None
                    or message.brightness_preset is not None
                )

            _name = cfg.get(CONF_NAME, f"Light {default_suffix}")
            _entity_name = cfg.get(CONF_ENTITY_NAME)
            _icon = cfg.get(CONF_ICON)
            _icon_on = cfg.get(CONF_ICON_ON)
            _manufacturer = cfg.get(CONF_MANUFACTURER, "BTicino")
            _model = cfg.get(CONF_DEVICE_MODEL, "Lighting Device")

            _light = MyHOMELight(
                hass=hass,
                name=_name,
                entity_name=_entity_name,
                icon=_icon,
                icon_on=_icon_on,
                device_id=unique_id,
                who=str(message.who),
                where=where,
                interface=interface,
                dimmable=_is_dimmable,
                manufacturer=_manufacturer,
                model=_model,
                gateway=hass.data[DOMAIN][config_entry.data[CONF_MAC]][CONF_ENTITY],
            )
            known_lights.add(unique_id)
            async_add_entities([_light])
            _light.handle_event(message)

            # Signal button platform to create Lock/Unlock buttons if not present
            async_dispatcher_send(
                hass,
                f"myhome_new_device_{config_entry.data[CONF_MAC]}",
                {"who": "1", "where": where, "interface": interface, "name": _name, "device_id": unique_id}
            )

        async_dispatcher_send(hass, f"myhome_update_{config_entry.data[CONF_MAC]}_1_{unique_id}", message)

    @callback
    def _handle_light_message(msg):
        """Filter and forward light messages."""
        if isinstance(msg, OWNLightingEvent):
            if getattr(msg, "is_translation", None) is True:
                return
            async_add_light(msg)

    # Listen to all incoming gateway messages
    config_entry.async_on_unload(
        async_dispatcher_connect(
            hass,
            f"myhome_message_{config_entry.data[CONF_MAC]}",
            _handle_light_message,
        )
    )

async def async_unload_entry(hass, config_entry):
    """Unload light platform."""
    return True


def eight_bits_to_percent(value: int) -> int:
    return int(round((value * 100) / 255, 0))


def percent_to_eight_bits(value: int) -> int:
    return int(round((value * 255) / 100, 0))


class MyHOMELight(MyHOMEEntity, LightEntity):
    def __init__(
        self,
        hass,
        name: str,
        entity_name: str,
        icon: str,
        icon_on: str,
        device_id: str,
        who: str,
        where: str,
        interface: str,
        dimmable: bool,
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
        )



        self._interface = interface
        self._full_where = f"{self._where}#4#{self._interface}" if self._interface is not None else self._where

        self._attr_supported_features = 0
        self._attr_supported_color_modes: set[ColorMode] = set()

        if dimmable:
            self._attr_supported_color_modes.add(ColorMode.BRIGHTNESS)
            self._attr_color_mode = ColorMode.BRIGHTNESS
            self._attr_supported_features |= LightEntityFeature.TRANSITION
        else:
            self._attr_supported_color_modes.add(ColorMode.ONOFF)
            self._attr_color_mode = ColorMode.ONOFF
            self._attr_supported_features |= LightEntityFeature.FLASH

        self._attr_extra_state_attributes = {
            "A": where[: len(where) // 2],
            "PL": where[len(where) // 2 :],
        }
        if self._interface is not None:
            self._attr_extra_state_attributes["Int"] = self._interface

        self._on_icon = icon_on
        self._off_icon = icon

        if self._off_icon is not None:
            self._attr_icon = self._off_icon

        self._attr_is_on = None
        self._attr_brightness = None
        self._attr_brightness_pct = None

        # Software stepped transition support
        self._fade_task: asyncio.Task | None = None
        self._fade_id: int = 0
        self._cmd_lock: asyncio.Lock = asyncio.Lock()
        self._last_brightness_pct: int = 100

    async def async_added_to_hass(self):
        """Run when entity about to be added to hass."""
        self._register_availability_listener()
        self.async_on_remove(
            async_dispatcher_connect(
                self.hass,
                f"myhome_update_{self._gateway_handler.mac}_1_{self._full_where}",
                self.handle_event,
            )
        )
        await self.async_update()

    async def async_update(self):
        """Update the entity.

        Only used by the generic entity update service.
        """
        if ColorMode.BRIGHTNESS in self._attr_supported_color_modes:
            await self._gateway_handler.send_status_request(OWNLightingCommand.get_brightness(self._full_where))
        else:
            await self._gateway_handler.send_status_request(OWNLightingCommand.status(self._full_where))

    # ── Transition helpers (software stepped dimming) ────────────────────────

    def _get_transition_mode(self) -> str:
        if not self._gateway_handler or not self._gateway_handler.config_entry:
            return DEFAULT_TRANSITION_MODE
        raw = self._gateway_handler.config_entry.options.get(
            CONF_TRANSITION_MODE, DEFAULT_TRANSITION_MODE
        )
        if raw == TRANSITION_MODE_AUTO:
            return TRANSITION_MODE_SOFTWARE
        if raw not in (TRANSITION_MODE_SOFTWARE, TRANSITION_MODE_NATIVE):
            return DEFAULT_TRANSITION_MODE
        return raw

    def _should_use_software_stepped(self, transition: float | None) -> bool:
        if transition is None or transition <= 0:
            return False
        mode = self._get_transition_mode()
        return mode != TRANSITION_MODE_NATIVE

    async def _set_brightness_instant(self, pct: int) -> None:
        """Send set_brightness with transition=0. Uses per-light lock to help ordering."""
        pct = max(0, min(100, int(pct)))
        async with self._cmd_lock:
            await self._gateway_handler.send(
                OWNLightingCommand.set_brightness(self._full_where, pct, 0)
            )

    def _apply_brightness_state(self, pct: int, is_on: bool | None = None) -> None:
        pct = max(0, min(100, int(pct)))
        self._attr_brightness_pct = pct
        self._attr_brightness = percent_to_eight_bits(pct)
        if is_on is not None:
            self._attr_is_on = is_on
        else:
            self._attr_is_on = pct > 0
        if pct > 0:
            self._last_brightness_pct = pct

    def _next_fade_id(self) -> int:
        self._fade_id += 1
        return self._fade_id

    def _cancel_fade_if_active(self) -> None:
        """Simple cancel for @callback contexts (e.g. handle_event)."""
        if self._fade_task and not self._fade_task.done():
            self._fade_task.cancel()
            self._fade_task = None

    async def _cancel_fade_robustly(self) -> None:
        """Robust cancel + drain for async contexts."""
        if self._fade_task and not self._fade_task.done():
            self._fade_task.cancel()
            try:
                await asyncio.wait_for(asyncio.shield(self._fade_task), timeout=0.15)
            except (Exception, asyncio.CancelledError):
                pass
            self._fade_task = None

    async def async_will_remove_from_hass(self):
        await self._cancel_fade_robustly()
        await super().async_will_remove_from_hass()

    async def _async_fade_to(self, start_pct: int, target_pct: int, duration: float, fade_id: int) -> None:
        """Background software stepped fade using instant brightness commands."""
        start_pct = max(0, min(100, int(start_pct or 0)))
        target_pct = max(0, min(100, int(target_pct or 0)))
        duration = max(0.0, float(duration))

        if fade_id != self._fade_id:
            return

        # Warn if using multiple workers (can interleave steps for this light)
        try:
            if self._gateway_handler and self._gateway_handler.config_entry:
                wc = self._gateway_handler.config_entry.options.get(CONF_WORKER_COUNT, 1)
                if int(wc) > 1:
                    LOGGER.warning(
                        "%s: Using software stepped fade with command_worker_count=%s. "
                        "Step reordering is possible. Recommend =1 for reliable fades.",
                        self._where, wc
                    )
        except Exception:
            pass

        if duration < 0.05 or abs(target_pct - start_pct) < 1:
            await self._set_brightness_instant(target_pct)
            self._apply_brightness_state(target_pct)
            self.async_schedule_update_ha_state()
            if fade_id == self._fade_id:
                self._fade_task = None
            return

        num_steps = max(
            SOFTWARE_TRANSITION_MIN_STEPS,
            min(
                SOFTWARE_TRANSITION_MAX_STEPS,
                int(duration / SOFTWARE_TRANSITION_STEP_INTERVAL + 0.5),
            ),
        )
        step_time = duration / num_steps
        delta = (target_pct - start_pct) / num_steps

        try:
            for i in range(1, num_steps + 1):
                if fade_id != self._fade_id:
                    LOGGER.debug("%s Aborting stale fade step", self._where)
                    return
                current = int(round(start_pct + delta * i))
                current = max(0, min(100, current))

                await self._set_brightness_instant(current)
                self._apply_brightness_state(current)
                self.async_schedule_update_ha_state()

                if i < num_steps:
                    await asyncio.sleep(step_time)

            if fade_id == self._fade_id:
                self._apply_brightness_state(target_pct)
                self.async_schedule_update_ha_state()
        except asyncio.CancelledError:
            LOGGER.debug("%s Software fade cancelled (id=%s)", self._where, fade_id)
            raise
        except Exception as err:  # prevent "Task exception was never retrieved"
            LOGGER.warning("%s Fade task error (id=%s): %s", self._where, fade_id, err)
        finally:
            if fade_id == self._fade_id:
                self._fade_task = None

    async def async_turn_on(self, **kwargs):
        """Turn the device on."""

        if ATTR_FLASH in kwargs and self._attr_supported_features & LightEntityFeature.FLASH:
            if kwargs[ATTR_FLASH] == FLASH_SHORT:
                return await self._gateway_handler.send(OWNLightingCommand.flash(self._full_where, 0.5))
            elif kwargs[ATTR_FLASH] == FLASH_LONG:
                return await self._gateway_handler.send(OWNLightingCommand.flash(self._full_where, 1.5))

        # Original combined condition preserved for compatibility
        if ((ATTR_BRIGHTNESS in kwargs or ATTR_BRIGHTNESS_PCT in kwargs) and ColorMode.BRIGHTNESS in self._attr_supported_color_modes) or (
            ATTR_TRANSITION in kwargs and self._attr_supported_features & LightEntityFeature.TRANSITION
        ):
            transition = float(kwargs.get(ATTR_TRANSITION, 0.0))

            if ATTR_BRIGHTNESS in kwargs or ATTR_BRIGHTNESS_PCT in kwargs:
                _percent_brightness = eight_bits_to_percent(kwargs[ATTR_BRIGHTNESS]) if ATTR_BRIGHTNESS in kwargs else None
                _percent_brightness = kwargs[ATTR_BRIGHTNESS_PCT] if ATTR_BRIGHTNESS_PCT in kwargs else _percent_brightness

                if _percent_brightness == 0:
                    return await self.async_turn_off(**kwargs)
                else:
                    target_pct = _percent_brightness
                    start_pct = (self._attr_brightness_pct if self._attr_is_on and self._attr_brightness_pct is not None else 0)

                    await self._cancel_fade_robustly()

                    if self._should_use_software_stepped(transition):
                        fid = self._next_fade_id()
                        self._fade_task = self.hass.async_create_task(
                            self._async_fade_to(start_pct, target_pct, transition, fid)
                        )
                        return

                    # native path (exact pre-existing)
                    if ATTR_TRANSITION in kwargs:
                        await self._gateway_handler.send(
                            OWNLightingCommand.set_brightness(self._full_where, target_pct, int(transition))
                        )
                    else:
                        await self._gateway_handler.send(OWNLightingCommand.set_brightness(self._full_where, target_pct))
                    if target_pct > 0:
                        self._last_brightness_pct = target_pct
                    return
            else:
                # transition-only (no brightness kwarg)
                target_pct = self._last_brightness_pct or 100
                start_pct = (self._attr_brightness_pct if self._attr_is_on and self._attr_brightness_pct is not None else 0)

                await self._cancel_fade_robustly()

                if self._should_use_software_stepped(transition):
                    fid = self._next_fade_id()
                    self._fade_task = self.hass.async_create_task(
                        self._async_fade_to(start_pct, target_pct, transition, fid)
                    )
                    return

                # native switch_on with speed
                return await self._gateway_handler.send(OWNLightingCommand.switch_on(self._full_where, int(transition)))
        else:
            # plain on path (preserved)
            await self._gateway_handler.send(OWNLightingCommand.switch_on(self._full_where))
            if ColorMode.BRIGHTNESS in self._attr_supported_color_modes:
                await self.async_update()

    async def async_turn_off(self, **kwargs):
        """Turn the device off."""

        if ATTR_TRANSITION in kwargs and self._attr_supported_features & LightEntityFeature.TRANSITION:
            transition = float(kwargs[ATTR_TRANSITION])
            start_pct = (self._attr_brightness_pct if self._attr_is_on and self._attr_brightness_pct is not None else 0)

            await self._cancel_fade_robustly()

            if self._should_use_software_stepped(transition):
                fid = self._next_fade_id()
                self._fade_task = self.hass.async_create_task(
                    self._async_fade_to(start_pct, 0, transition, fid)
                )
                return

            # native
            return await self._gateway_handler.send(OWNLightingCommand.switch_off(self._full_where, int(transition)))

        if ATTR_FLASH in kwargs and self._attr_supported_features & LightEntityFeature.FLASH:
            if kwargs[ATTR_FLASH] == FLASH_SHORT:
                return await self._gateway_handler.send(OWNLightingCommand.flash(self._full_where, 0.5))
            elif kwargs[ATTR_FLASH] == FLASH_LONG:
                return await self._gateway_handler.send(OWNLightingCommand.flash(self._full_where, 1.5))

        # plain off (preserved)
        return await self._gateway_handler.send(OWNLightingCommand.switch_off(self._full_where))

    @callback
    def handle_event(self, message: OWNLightingEvent):
        """Handle an event message.

        During an active software fade we keep optimistic state unless the bus
        reports a significant change (is_on=False or brightness diff >=10pp or 0).
        This prevents wall switches or echoes from ruining the visible fade.
        """
        if getattr(message, "is_translation", None) is True:
            return

        LOGGER.debug(
            "%s %s",
            self._gateway_handler.log_id,
            message.human_readable_log,
        )
        self._attr_is_on = message.is_on

        is_fading = bool(self._fade_task and not self._fade_task.done())

        # Always cancel fade on physical off (pure on/off events or brightness=0)
        if is_fading and not self._attr_is_on:
            self._cancel_fade_if_active()

        # Auto-promote to dimmable when brightness data is received (always)
        if (message.brightness is not None or message.brightness_preset is not None):
            if ColorMode.BRIGHTNESS not in self._attr_supported_color_modes:
                LOGGER.info(
                    "Auto-detected dimmer for light %s, upgrading to BRIGHTNESS mode.",
                    self._where,
                )
                self._attr_supported_color_modes = {ColorMode.BRIGHTNESS}
                self._attr_color_mode = ColorMode.BRIGHTNESS
                self._attr_supported_features |= LightEntityFeature.TRANSITION
                self._attr_supported_features &= ~LightEntityFeature.FLASH

        if ColorMode.BRIGHTNESS in self._attr_supported_color_modes and message.brightness is not None:
            if is_fading:
                # Precise policy during fade: only apply significant physical changes
                current_opt = self._attr_brightness_pct or 0
                reported = message.brightness
                if reported == 0 or abs(reported - current_opt) >= 10:
                    self._cancel_fade_if_active()
                    self._apply_brightness_state(reported)
                # else: keep optimistic state
            else:
                self._attr_brightness_pct = message.brightness
                self._attr_brightness = percent_to_eight_bits(message.brightness)
                if message.brightness > 0:
                    self._last_brightness_pct = message.brightness

        if self._off_icon is not None and self._on_icon is not None:
            self._attr_icon = self._on_icon if self._attr_is_on else self._off_icon

        if self.hass is not None or hasattr(self.async_schedule_update_ha_state, "assert_called"):
            try:
                self.async_schedule_update_ha_state()
            except RuntimeError:
                pass
