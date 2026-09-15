"""Support for common values for MyHome devices."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .gateway import MyHOMEGatewayHandler

from homeassistant.core import HomeAssistant, State, callback
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity import Entity
from homeassistant.helpers.restore_state import RestoreEntity
from homeassistant.helpers.typing import UNDEFINED

from .const import CONF_ENTITIES, DOMAIN, LOGGER
from .data import get_runtime_data

__all__ = ["Entity", "MyHOMEEntity"]


class MyHOMEEntity(RestoreEntity):
    """Base of every MyHOME entity.

    Naming follows Home Assistant's device/entity model (``has_entity_name``):
    ``name`` names the *device* (the actuator, probe or zone on the bus). The
    entity's own name is the ``translation_key`` a subclass passes (buttons,
    energy counters), or - for sensors and binary sensors - ``entity_name`` from
    ``myhome.yaml`` and otherwise the device class, resolved by Home Assistant.
    Every other entity *is* its device and carries no name of its own. Entity
    ids are assigned by the entity registry; existing entries keep theirs.
    """

    # Whether to request a status update from the bus right after being added.
    # Push-only devices set this to False to avoid a useless (NACKed) query.
    _poll_on_add: bool = True
    # Sensors / binary sensors: let Home Assistant name the entity after its device class.
    _name_from_device_class: bool = False

    _attr_has_entity_name = True

    def __init__(
        self,
        hass: HomeAssistant | None,
        name: str,
        platform: str,
        device_id: str,
        who: str,
        where: str,
        manufacturer: str,
        model: str,
        gateway: MyHOMEGatewayHandler,
        entity_name: str | None = None,
        translation_key: str | None = None,
    ):
        self._hass = hass
        self._platform = platform
        self._who = who
        self._where = where
        self._device_id = device_id
        clean_dev_id = str(self._device_id)
        if clean_dev_id.startswith(f"{self._who}-"):
            clean_dev_id = clean_dev_id[len(f"{self._who}-") :]

        self._attr_unique_id = f"{gateway.mac}-{self._who}-{clean_dev_id}"
        self._manufacturer = manufacturer or "BTicino S.p.A."
        self._model = model
        self._gateway_handler = gateway
        self._availability_listener_registered = False
        self._device_name = name
        if translation_key:
            self._attr_translation_key = translation_key
        elif not self._name_from_device_class:
            # The entity is the device: light, switch, cover, climate, audio zone, alarm
            # panel. entity_name has never named these and is ignored.
            self._attr_name = None
        elif entity_name and entity_name.strip().lower() == str(name).strip().lower():
            # Sensors / binary sensors with entity_name equal to the device name (the
            # old "same name twice" habit): the entity is the device.
            self._attr_name = None
        elif entity_name:
            # Sensors / binary sensors: entity_name names the entity within its device.
            self._attr_name = entity_name
        # else: Home Assistant names the entity after its device class

        self._attr_entity_registry_enabled_default = True
        self._attr_should_poll = False

        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, f"{gateway.mac}-{self._who}-{clean_dev_id}")},
            name=name,
            manufacturer=self._manufacturer,
            model=self._model,
        )
        # Link to the gateway device (via_device_id; via_device is gone since core 2026.8).
        if gateway.device_registry_id:
            self._attr_device_info["via_device_id"] = gateway.device_registry_id

    @property
    def _display_name(self) -> str:
        """Device name plus entity name, for log lines and error messages.

        Mirrors the friendly name Home Assistant builds; before the platform's
        translations are loaded the entity part falls back to the translation
        key or device class it will be named after.
        """
        try:
            own = self.name
        except AttributeError:  # translation lookup needs a platform; not added yet
            own = UNDEFINED
        if own is UNDEFINED or (own is None and not hasattr(self, "_attr_name")):
            key = getattr(self, "_attr_translation_key", None)
            device_class = getattr(self, "device_class", None) if self._name_from_device_class else None
            raw = key or (str(device_class) if device_class else None)
            own = raw.replace("_", " ").capitalize() if raw else None
        return f"{self._device_name} {own}" if own else self._device_name

    def _publish_state(self) -> None:
        """Write the entity state once the entity is live in Home Assistant.

        Discovery seeds an entity with the frame that revealed it before the
        entity platform has added it; there is nothing to write then (the
        platform writes the initial state when it adds the entity, and current
        cores warn about writes from entities without a platform).
        """
        if self.hass is None or self.platform is None or not self.entity_id:
            return
        try:
            self.async_schedule_update_ha_state()
        except RuntimeError as err:
            # A frame can still arrive for an entity that is being removed.
            LOGGER.debug("%s: state not written (%s)", self.entity_id, err)

    def _device_config(self) -> dict[str, Any] | None:
        """Return this device's configuration mapping from the entry's runtime data.

        ``None`` when the entity is not attached to a config-entry platform (or the
        device is not known to it), which is the case for entities built directly
        in tests.
        """
        entry = getattr(getattr(self, "platform", None), "config_entry", None)
        runtime = get_runtime_data(entry) if entry is not None else None
        if runtime is None:
            return None
        device_dict = runtime.platforms.get(self._platform, {}).get(self._device_id)
        return device_dict if isinstance(device_dict, dict) else None

    def _register_entity_ref(self, key: str) -> None:
        """Expose this entity under ``key`` in the device's ``entities`` mapping."""
        device_dict = self._device_config()
        if device_dict is None:
            return
        if not isinstance(device_dict.get(CONF_ENTITIES), dict):
            device_dict[CONF_ENTITIES] = {}
        device_dict[CONF_ENTITIES][key] = self

    def _unregister_entity_ref(self, key: str) -> None:
        """Remove the ``key`` reference added by :meth:`_register_entity_ref`."""
        device_dict = self._device_config()
        if device_dict is None:
            return
        entities = device_dict.get(CONF_ENTITIES)
        if isinstance(entities, dict):
            entities.pop(key, None)

    @property
    def via_device_id(self) -> str:
        """Return gateway unique ID associated with this device."""
        return self._gateway_handler.unique_id

    @property
    def available(self) -> bool:
        """Return whether the gateway is available."""
        return self._gateway_handler.available

    @callback
    def _handle_availability_update(self) -> None:
        """Write state when the gateway availability changes."""
        self.async_write_ha_state()

    @callback
    def _register_availability_listener(self) -> None:
        """Register the gateway availability listener once."""
        if self._availability_listener_registered:
            return
        target_hass = self.hass or self._hass
        if target_hass is None:
            return
        self.async_on_remove(
            async_dispatcher_connect(
                target_hass,
                self._gateway_handler.availability_signal,
                self._handle_availability_update,
            )
        )
        self._availability_listener_registered = True

    async def async_added_to_hass(self) -> None:
        """When entity is added to hass."""
        self._register_availability_listener()
        await super().async_added_to_hass()
        try:
            last_state = await self.async_get_last_state()
        except Exception:
            last_state = None
        if last_state is not None:
            await self.async_restore_last_state(last_state)
        if self._poll_on_add:
            await self.async_update()

    async def async_update(self) -> None:
        """Request the device's status from the bus; platforms override."""

    async def async_restore_last_state(self, last_state: State) -> None:
        """Hook for entities to restore specific attributes and modes."""
        if hasattr(self, "_attr_is_on") and getattr(self, "_attr_is_on") is None:
            if last_state.state == "on":
                setattr(self, "_attr_is_on", True)
            elif last_state.state == "off":
                setattr(self, "_attr_is_on", False)
        if hasattr(self, "_attr_native_value") and getattr(self, "_attr_native_value") is None:
            if last_state.state not in ("unknown", "unavailable"):
                try:
                    setattr(self, "_attr_native_value", float(last_state.state))
                except (ValueError, TypeError):
                    setattr(self, "_attr_native_value", last_state.state)

    async def async_will_remove_from_hass(self) -> None:
        """When entity is removed from hass."""
