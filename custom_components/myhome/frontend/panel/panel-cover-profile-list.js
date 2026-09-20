/** Read-only shared-profile view. Saved settings and associations come from HA. */
const asset = (name) => { const url = new URL(name, import.meta.url); url.search = new URL(import.meta.url).search; return url.href; };
const [{ escapeHtml: esc, replacePreservingFocus }, model] = await Promise.all([
  import(asset("panel-dom.js")), import(asset("panel-model.js")),
]);

const geometry = await import(asset("panel-cover-geometry.js"));
const { duration, usageLabel } = await import(asset("panel-profile-presentation.js"));

export class CoverProfileList {
  constructor() { this._generation = 0; this._expanded = new Set(); this._unsubs = []; this._rows = new Map(); }

  clear() {
    this._generation++;
    for (const unsubscribe of this._unsubs.splice(0)) Promise.resolve(unsubscribe()).catch(() => {});
    this._rows.clear();
    this._exportMessage = "";
    this._context = null;
    this._key = null;
  }

  show(context) {
    const key = JSON.stringify(context.gateways.map((entry) => entry.entry_id).sort());
    const changed = key !== this._key || context.hass.connection !== this._context?.hass.connection;
    if (changed) this.clear();
    this._context = context;
    this._key = key;
    if (changed) {
      const generation = this._generation;
      for (const entry of context.gateways) this._rows.set(entry.entry_id, { loading: false, data: null, error: false, again: false });
      for (const entry of context.gateways) {
        this._subscribe(entry.entry_id, generation);
        this._read(entry.entry_id, generation);
      }
    }
    this._render();
  }

  _current(generation) { return generation === this._generation && this._context?.host.isConnected; }

  updateHass(hass) { if (this._context) this._context.hass = hass; }

  async _subscribe(entryId, generation) {
    try {
      const unsubscribe = await this._context.hass.connection.subscribeMessage((event) => {
        if (!this._current(generation) || event.entry_id !== entryId) return;
        const row = this._rows.get(entryId);
        if (event.kind === "removed") { row.removed = true; row.data = null; this._render(); return; }
        if (Number.isInteger(event.revision) && event.revision > (row.data?.revision ?? -1)) this._read(entryId, generation);
      }, { type: "myhome/cover_profiles/subscribe", entry_id: entryId });
      if (!this._current(generation)) { await unsubscribe(); return; }
      this._unsubs.push(unsubscribe);
    } catch { /* The panel's visible-only refresh reconciles missed notifications. */ }
  }

  refresh() {
    if (!this._context || document.hidden) return;
    for (const entryId of this._rows.keys()) this._read(entryId, this._generation);
  }

  async _read(entryId, generation) {
    if (!this._current(generation) || document.hidden) return;
    const row = this._rows.get(entryId);
    if (row.removed) return;
    if (row.loading) { row.again = true; return; }
    row.loading = true;
    try {
      const data = await this._context.hass.callWS({ type: "myhome/cover_profiles/overview", entry_id: entryId });
      if (!this._current(generation) || row.removed) return;
      if (data.entry_id !== entryId) throw new Error("Wrong gateway");
      if (!row.data || data.revision >= row.data.revision) row.data = data;
      row.error = false;
    } catch {
      if (this._current(generation)) row.error = true;
    } finally {
      if (this._current(generation)) {
        row.loading = false;
        this._render();
        if (row.again) { row.again = false; this._read(entryId, generation); }
      }
    }
  }

  toggle(key) {
    if (this._expanded.has(key)) this._expanded.delete(key);
    else this._expanded.add(key);
    this._render();
  }

  _matches(profile, followers) {
    const { filters, inventory, hass } = this._context;
    if (filters.category && filters.category !== "cover") return false;
    const eligible = followers.filter(({ entity }) => entity && (!filters.area
      || (filters.area === "__none__" ? !model.effectiveArea(entity, inventory.devices)
        : model.effectiveArea(entity, inventory.devices) === filters.area)));
    if (filters.area && !eligible.length) return false;
    const terms = filters.query.trim().toLocaleLowerCase().split(/\s+/).filter(Boolean);
    const profileName = profile.name.toLocaleLowerCase();
    return !terms.length || terms.every((term) => profileName.includes(term)) || eligible.some(({ cover, entity }) => {
      const address = entity.address;
      const area = inventory.areas.find((item) => item.id === model.effectiveArea(entity, inventory.devices));
      const text = [profile.name, model.entityName(entity, hass), cover?.name, entity.entity_id, area?.name,
        address?.raw, address?.a == null ? "" : `A:${address.a} A: ${address.a}`,
        address?.pl == null ? "" : `PL:${address.pl} PL: ${address.pl}`].filter(Boolean).join(" ").toLocaleLowerCase();
      return terms.every((term) => text.includes(term));
    });
  }

  _provenance(profile) {
    const { t, hass } = this._context;
    return `<div class="profile-origin-grid">${["opening", "closing"].map((direction) => {
      const evidence = profile.provenance?.[direction];
      const source = ["manual", "guided", "automatic"].includes(evidence?.source) ? evidence.source : "unknown";
      let date = t("profileDateUnknown");
      if (evidence?.recorded_at && !Number.isNaN(Date.parse(evidence.recorded_at))) {
        try { date = new Intl.DateTimeFormat(hass.language || "en", { dateStyle: "medium", timeStyle: "short" }).format(new Date(evidence.recorded_at)); }
        catch { date = new Date(evidence.recorded_at).toISOString(); }
      }
      return `<div><strong>${esc(t(direction === "opening" ? "calStepOpening" : "calStepClosing"))}</strong>
        <p class="muted">${esc(t(`profileSource_${source}`))}${evidence?.inherited ? ` · ${esc(t("profileEvidenceInherited"))}` : ""} · ${esc(date)}</p>
        <p class="muted">${esc(evidence?.origin_name || evidence?.origin_entity_id || t(source === "manual" && !evidence?.inherited ? "catalogueManualOrigin" : "profileOriginUnknown"))}</p></div>`;
    }).join("")}</div>`;
  }

  _follower({ id, cover, entity }) {
    const { t, hass, addressDetails } = this._context;
    if (!id || !cover) return `<li class="shared-profile-follower"><p class="muted">${esc(t("profileMissingCover"))}</p></li>`;
    const name = entity ? model.entityName(entity, hass) : cover.name;
    const personal = Object.keys(cover.overrides || {});
    return `<li class="shared-profile-follower"><div class="shared-profile-follower-head"><strong>${esc(name)}</strong>
      ${entity ? `<button type="button" data-action="cover-profile" data-id="${esc(id)}">${esc(t("profileManageCover"))}</button>` : ""}</div>
      ${entity ? addressDetails(entity) : `<p class="muted">${esc(id)}</p>`}
      ${!cover.available ? `<span class="badge offline">${esc(t("unavailable"))}</span>` : ""}
      ${personal.length ? `<p class="muted">${esc(t("profilePersonalDirections"))}: ${personal.map((direction) => esc(t(direction === "opening" ? "calStepOpening" : "calStepClosing"))).join(", ")}</p>` : ""}
      ${cover.pending ? `<p class="profile-pending">${esc(t("profilePending"))}</p>` : ""}
    </li>`;
  }

  _card(entry, profile, followers) {
    const { t, hass } = this._context;
    const key = JSON.stringify([entry.entry_id, profile.id]);
    const detailsKey = JSON.stringify([entry.entry_id, profile.id, "details"]);
    const menuKey = JSON.stringify([entry.entry_id, profile.id, "menu"]);
    const id = `shared-profile-${encodeURIComponent(key)}`;
    const count = profile.assigned_to.length;
    const capabilities = this._rows.get(entry.entry_id).data.capabilities;
    const toggle = (stateKey, targetId, label, icon = "mdi:chevron-down") => `<button type="button" class="ghost" data-action="toggle-shared-profile" data-profile-part="${stateKey === key ? "associations" : stateKey === detailsKey ? "details" : "menu"}" data-group="${esc(stateKey)}" aria-expanded="${this._expanded.has(stateKey)}" aria-controls="${esc(targetId)}"><span>${esc(label)}</span><ha-icon icon="${icon}" aria-hidden="true"></ha-icon></button>`;
    const action = (operation, label, icon = "") => `<button type="button" class="${operation === "assign" ? "primary" : operation === "edit" ? "icon-button" : "ghost"}" data-action="manage-profile" data-id="${esc(profile.id)}" data-operation="${operation}" data-entry="${esc(entry.entry_id)}" ${operation === "delete" && count ? `disabled title="${esc(t("profileDeleteHelp"))}"` : `title="${esc(label)}"`} aria-label="${esc(label)}">${icon ? `<ha-icon icon="${icon}" aria-hidden="true"></ha-icon>` : esc(label)}</button>`;
    return `<section class="device-group shared-profile-card" data-entry="${esc(entry.entry_id)}" data-profile="${esc(profile.id)}">
      <header class="shared-profile-heading"><strong class="device-name">${esc(profile.name)}</strong>
        <span class="count">${count ? esc(usageLabel(count, t)) : esc(t("profileUnusedShort"))}</span></header>
      <div class="shared-profile-summary">
        <p class="profile-times-inline"><span><ha-icon icon="mdi:arrow-up" aria-hidden="true"></ha-icon><span class="muted">${esc(t("calStepOpening"))}</span> <strong>${esc(duration(profile.opening_time ?? profile.travel_time, hass.language))} s</strong></span>
          <span><ha-icon icon="mdi:arrow-down" aria-hidden="true"></ha-icon><span class="muted">${esc(t("calStepClosing"))}</span> <strong>${esc(duration(profile.closing_time ?? profile.travel_time, hass.language))} s</strong></span></p>
        ${capabilities?.profile_management ? `<div class="actions catalogue-actions">
          ${capabilities.profile_assignment ? action("assign", t("catalogueAssign")) : ""}${action("edit", t("edit"), "mdi:pencil-outline")}
          ${toggle(menuKey, `${id}-menu`, t("profileMore"), "mdi:dots-vertical")}
        </div><div class="profile-overflow" id="${esc(id)}-menu" ${this._expanded.has(menuKey) ? "" : "hidden"}>
          ${action("duplicate", t("catalogueDuplicate"))}
          <button type="button" class="ghost" data-action="export-profiles" data-entry="${esc(entry.entry_id)}">${esc(t("profileExportGateway"))}</button>
          ${action("delete", t("catalogueDelete"))}
          ${count ? `<p class="muted">${esc(t("profileDeleteHelp"))}</p>` : ""}
        </div>` : ""}
        <div class="profile-disclosures">
          ${count ? toggle(key, id, `${t("profileAssociations")} (${count})`) : ""}
          ${toggle(detailsKey, `${id}-details`, t("profileTechnicalDetails"))}
        </div>
      </div>
      ${count ? `<div id="${esc(id)}" class="entity-list shared-profile-body" ${this._expanded.has(key) ? "" : "hidden"}>
        <ul class="shared-profile-followers">${followers.map((item) => this._follower(item)).join("")}</ul></div>` : ""}
      <div id="${esc(id)}-details" class="shared-profile-details" ${this._expanded.has(detailsKey) ? "" : "hidden"}>
        ${this._provenance(profile)}
        ${profile.geometry ? `<p class="muted">${geometry.geometrySummary(Object.fromEntries(Object.entries(profile.geometry).map(([key, value]) => [key, { value }])), t)}</p>` : ""}
        ${profile.reference_travel_cm != null ? `<p class="muted">${esc(t("profileReferenceTravel"))}: ${esc(duration(profile.reference_travel_cm, hass.language))} cm</p>` : ""}
      </div></section>`;
  }

  async exportProfiles(entryId) {
    if (!this._context || !this._rows.get(entryId)?.data || this._exporting === this._generation) return;
    const generation = this._generation, { hass, t } = this._context;
    this._exporting = generation;
    this._exportMessage = t("profileExporting"); this._render();
    try {
      const data = await hass.callWS({ type: "myhome/cover_profiles/export", entry_id: entryId });
      if (!this._current(generation)) return;
      const url = URL.createObjectURL(new Blob([JSON.stringify(data, null, 2) + "\n"], { type: "application/json" }));
      const link = document.createElement("a");
      try {
        link.href = url; link.download = `myhome-calibration-${entryId.replace(/[^a-zA-Z0-9_-]/g, "_")}-r${data.revision}.json`;
        document.body.append(link); link.click();
      } finally { link.remove(); setTimeout(() => URL.revokeObjectURL(url), 1000); }
      this._exportMessage = t("profileExported");
    } catch { if (this._current(generation)) this._exportMessage = t("profileExportError"); }
    finally {
      if (this._exporting === generation) this._exporting = null;
      if (this._current(generation)) this._render();
    }
  }

  _render() {
    if (!this._context?.host.isConnected) return;
    const { host, gateways, inventory, filters, t } = this._context;
    const html = `<p class="muted">${esc(t("profileListHelp"))}</p><p class="muted" role="status">${esc(this._exportMessage || "")}</p>
      ${filters.query || filters.area || filters.category ? `<p class="muted">${esc(t("profileListFilterHelp"))}</p>` : ""}
      ${gateways.map((entry) => {
        const row = this._rows.get(entry.entry_id);
        let body;
        if (row.removed) body = `<p class="notice">${esc(t("gatewayNotFound"))}</p>`;
        else if (row.error) body = `<p class="notice error" role="alert">${esc(t("profileListError"))}</p><button type="button" data-action="refresh-profiles">${esc(t("refresh"))}</button>`;
        else if (!row.data) body = `<p class="muted" role="status">${esc(t("loading"))}</p>`;
        else {
          const cards = [...row.data.profiles].sort((a, b) => a.name.localeCompare(b.name)).map((profile) => {
            const followers = profile.assigned_to.map((id) => ({ id,
              cover: row.data.covers.find((cover) => cover.entity_id === id),
              entity: inventory.entities.find((entity) => entity.entity_id === id && entity.entry_id === entry.entry_id),
            }));
            return this._matches(profile, followers) ? this._card(entry, profile, followers) : "";
          }).join("");
          body = cards ? `<div class="device-groups">${cards}</div>` : `<p class="muted">${esc(t(row.data.profiles.length ? "noResults" : "profileListEmpty"))}</p>`;
        }
        return `<section class="shared-profile-gateway" data-entry="${esc(entry.entry_id)}">${gateways.length > 1 ? `<h3>${esc(entry.title)}</h3>` : ""}${body}</section>`;
      }).join("")}`;
    if (host.innerHTML !== html) replacePreservingFocus(host, html);
  }
}
