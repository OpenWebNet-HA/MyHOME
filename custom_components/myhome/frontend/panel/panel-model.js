/** Pure inventory helpers shared by the panel and its regression tests. */
export function scopedInventory(data, entryId) {
  return {
    gateways: data.gateways.filter((item) => !entryId || item.entry_id === entryId),
    devices: data.devices.filter((item) => !entryId || item.entry_ids.includes(entryId)),
    entities: data.entities.filter((item) => !entryId || item.entry_id === entryId),
  };
}

export function entityName(entity, hass) {
  return entity.name || hass?.states?.[entity.entity_id]?.attributes?.friendly_name
    || entity.original_name || entity.entity_id;
}

export function effectiveArea(entity, devices) {
  return entity.area_id || devices.find((device) => device.id === entity.device_id)?.area_id || "";
}

export function filterItems(data, scope, view, { query, category, area }, hass) {
  const terms = query.trim().toLocaleLowerCase().split(/\s+/).filter(Boolean);
  const areaName = (id) => data.areas.find((item) => item.id === id)?.name || "";
  return scope[view].filter((item) => {
    const isEntity = view === "entities";
    const linked = isEntity ? [item] : scope.entities.filter((entity) => entity.device_id === item.id);
    const areaId = isEntity ? effectiveArea(item, data.devices) : item.area_id || "";
    if (area && (area === "__none__" ? !!areaId : areaId !== area)) return false;
    if (category && !linked.some((entity) => entity.domain === category)) return false;
    const text = [
      item.name_by_user, item.name, item.model, item.manufacturer, areaName(areaId),
      ...(item.identifiers || []), ...linked.flatMap((entity) => [
        entityName(entity, hass), entity.entity_id, entity.unique_id,
      ]),
    ].filter(Boolean).join(" ").toLocaleLowerCase();
    return terms.every((term) => text.includes(term));
  });
}

export function registryChanges(kind, original, name, areaId) {
  const nameKey = kind === "device" ? "name_by_user" : "name";
  const result = {};
  const normalizedName = name.trim() || null;
  if (normalizedName !== (original[nameKey] || null)) result[nameKey] = normalizedName;
  if ((areaId || null) !== (original.area_id || null)) result.area_id = areaId || null;
  return result;
}
