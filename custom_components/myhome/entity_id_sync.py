"""Opt-in entity ID updates using Home Assistant's automatic naming rules.

Snapshot eligibility before device names change: registry update events arrive
with the new device name already applied. Never migrate IDs during setup.
"""

from homeassistant.core import Event, HomeAssistant, callback
from homeassistant.helpers import area_registry as ar
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er

from .const import CONF_SYNC_ENTITY_IDS, DOMAIN, LOGGER
from .data import MyHOMEConfigEntry


@callback
def async_setup_entity_id_sync(hass: HomeAssistant, entry: MyHOMEConfigEntry) -> None:
    """Listen for future user renames, with config-entry scoped cleanup."""
    if not entry.options.get(CONF_SYNC_ENTITY_IDS, False):
        return

    devices = dr.async_get(hass)
    entities = er.async_get(hass)
    automatic_ids: dict[str, tuple[er.RegistryEntry, str]] = {}

    @callback
    def remember(entity: er.RegistryEntry) -> None:
        """Ask Core whether this is still an automatic ID, without changing it."""
        automatic_ids.pop(entity.entity_id, None)
        if (
            entity.platform != DOMAIN
            or entity.config_entry_id != entry.entry_id
            or not entity.has_entity_name
            or entity.name is not None
            or entity.device_id is None
            or (device := devices.async_get(entity.device_id)) is None
            or not (name := device.name_by_user or device.name)
            or not any(char.isalnum() for char in name)
        ):
            return
        if entities.async_regenerate_entity_id(entity) == entity.entity_id:
            automatic_ids[entity.entity_id] = (entity, name)

    @callback
    def refresh(_event: Event[ar.EventAreaRegistryUpdatedData] | None = None) -> None:
        """Refresh eligibility after setup or area changes; never rename here."""
        automatic_ids.clear()
        for entity in er.async_entries_for_config_entry(entities, entry.entry_id):
            remember(entity)

    @callback
    def entity_updated(event: Event[er.EventEntityRegistryUpdatedData]) -> None:
        """Include discovery and discard stale IDs after manual edits/removal."""
        data = event.data
        if data["action"] == "update" and "old_entity_id" in data:
            automatic_ids.pop(data["old_entity_id"], None)
        automatic_ids.pop(data["entity_id"], None)
        if (entity := entities.async_get(data["entity_id"])) is not None:
            remember(entity)

    @callback
    def device_updated(event: Event[dr.EventDeviceRegistryUpdatedData]) -> None:
        """Apply Core-generated IDs only to this gateway's eligible entities."""
        data = event.data
        if data["action"] != "update" or (device := devices.async_get(data["device_id"])) is None:
            return
        changes = data["changes"]
        old_name = changes.get("name_by_user") or changes.get("name", device.name)
        new_name = device.name_by_user or device.name
        for entity in er.async_entries_for_device(entities, device.id, include_disabled_entities=True):
            if (
                "name_by_user" in changes
                and old_name
                and new_name
                and any(char.isalnum() for char in new_name)
                and automatic_ids.get(entity.entity_id) == (entity, old_name)
            ):
                # Core uses object_id_base (not the translated original_name),
                # configured name parts/areas and its own collision handling.
                try:
                    new_id = entities.async_regenerate_entity_id(entity)
                    if new_id != entity.entity_id:
                        automatic_ids.pop(entity.entity_id, None)
                        entity = entities.async_update_entity(entity.entity_id, new_entity_id=new_id)
                except ValueError as err:
                    LOGGER.warning("Keeping %s after device rename: %s", entity.entity_id, err)
            remember(entity)

    refresh()
    entry.async_on_unload(hass.bus.async_listen(er.EVENT_ENTITY_REGISTRY_UPDATED, entity_updated))
    entry.async_on_unload(hass.bus.async_listen(dr.EVENT_DEVICE_REGISTRY_UPDATED, device_updated))
    entry.async_on_unload(hass.bus.async_listen(ar.EVENT_AREA_REGISTRY_UPDATED, refresh))
