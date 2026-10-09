"""Opt-in entity ID updates after an explicit Home Assistant device rename.

Never migrate IDs at startup or react to names supplied by discovery/YAML.
Only default device/entity IDs, optionally followed by Home Assistant's numeric
collision suffix, are eligible. Arbitrary prefix matches would capture custom IDs.
"""

import re

from homeassistant.core import Event, HomeAssistant, callback
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from homeassistant.util import slugify

from .const import CONF_SYNC_ENTITY_IDS, DOMAIN, LOGGER
from .data import MyHOMEConfigEntry


def _matches_default_id(entity_id: str, expected_id: str) -> bool:
    """Match the default spelling, including HA collision suffixes (_2, _3, ...).

    A manually chosen ID with exactly this spelling is indistinguishable from
    an automatically generated one, just as with an unsuffixed default ID.
    """
    return (
        entity_id == expected_id
        or re.fullmatch(rf"{re.escape(expected_id)}_(?:[2-9]|[1-9][0-9]+)", entity_id) is not None
    )


@callback
def async_setup_entity_id_sync(hass: HomeAssistant, entry: MyHOMEConfigEntry) -> None:
    """Listen for future user renames, with config-entry scoped cleanup."""
    if not entry.options.get(CONF_SYNC_ENTITY_IDS, False):
        return

    device_registry = dr.async_get(hass)
    entity_registry = er.async_get(hass)

    @callback
    def device_updated(event: Event[dr.EventDeviceRegistryUpdatedData]) -> None:
        """Rename only this gateway's eligible entities, leaving their identity intact."""
        data = event.data
        if data["action"] != "update" or "name_by_user" not in data["changes"]:
            return
        device = device_registry.async_get(data["device_id"])
        if device is None:
            return

        old_name = data["changes"]["name_by_user"] or data["changes"].get("name", device.name)
        new_name = device.name_by_user or device.name
        if (
            not old_name
            or not new_name
            or not any(char.isalnum() for char in old_name)
            or not any(char.isalnum() for char in new_name)
        ):
            return
        if slugify(old_name) == slugify(new_name):
            return

        for entity in er.async_entries_for_device(
            entity_registry, device.id, include_disabled_entities=True
        ):
            if (
                entity.platform != DOMAIN
                or entity.config_entry_id != entry.entry_id
                or not entity.has_entity_name
                or entity.name is not None
            ):
                continue
            suffix = f" {entity.original_name}" if entity.original_name else ""
            expected_id = f"{entity.domain}.{slugify(f'{old_name}{suffix}')}"
            if not _matches_default_id(entity.entity_id, expected_id):
                continue
            new_id = f"{entity.domain}.{slugify(f'{new_name}{suffix}')}"
            if new_id == entity.entity_id:
                continue
            if entity_registry.async_get(new_id) is not None or hass.states.get(new_id) is not None:
                LOGGER.warning(
                    "Keeping %s: device rename would collide with %s", entity.entity_id, new_id
                )
                continue
            try:
                entity_registry.async_update_entity(entity.entity_id, new_entity_id=new_id)
            except ValueError as err:
                # Invalid/overlong names or a registry conflict must never break
                # the device rename or other entities on the gateway.
                LOGGER.warning("Keeping %s after device rename: %s", entity.entity_id, err)

    entry.async_on_unload(hass.bus.async_listen(dr.EVENT_DEVICE_REGISTRY_UPDATED, device_updated))
