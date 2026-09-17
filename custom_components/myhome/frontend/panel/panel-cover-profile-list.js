/** Read-only shared-profile view. Saved settings and associations come from HA. */
const asset = (name) => { const url = new URL(name, import.meta.url); url.search = new URL(import.meta.url).search; return url.href; };
const [{ escapeHtml: esc, replacePreservingFocus }, model] = await Promise.all([
  import(asset("panel-dom.js")), import(asset("panel-model.js")),
]);

export class CoverProfileList {
  constructor() { this._generation = 0; this._expanded = new Set(); this._unsubs = []; this._rows = new Map(); }

  clear() {
    this._generation++;
    for (const unsubscribe of this._unsubs.splice(0)) Promise.resolve(unsubscribe()).catch(() => {});
    this._rows.clear();
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
      return `<div><strong>${esc(t(direction === "opening" ? "profileOpeningTime" : "profileClosingTime"))}</strong>
        <p class="muted">${esc(t(`profileSource_${source}`))} · ${esc(date)}</p>
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
      ${cover.advanced ? `<p class="muted">${esc(t("profileError_advanced_cover"))}</p>` : `
        <p class="muted">${esc(t("profileEffectiveTimes"))}: ${esc(t("profileOpeningTime"))} ${esc(cover.effective?.opening?.value ?? "—")} s · ${esc(t("profileClosingTime"))} ${esc(cover.effective?.closing?.value ?? "—")} s</p>
        <p class="${personal.length ? "notice" : "muted"}">${esc(personal.length
          ? `${t("profilePersonalDirections")}: ${personal.map((direction) => t(direction === "opening" ? "calOnlyOpening" : "calOnlyClosing")).join(", ")}` : t("profileFollowsAll"))}</p>
        ${cover.pending ? `<p class="profile-pending">${esc(t("profilePending"))} · ${esc(t("profileConfiguredTimes"))}: ${esc(cover.configured?.opening?.value ?? "—")} / ${esc(cover.configured?.closing?.value ?? "—")} s</p>` : ""}`}
    </li>`;
  }

  _card(entry, profile, followers) {
    const { t } = this._context;
    const key = JSON.stringify([entry.entry_id, profile.id]);
    const expanded = this._expanded.has(key);
    const id = `shared-profile-${encodeURIComponent(key)}`;
    return `<section class="device-group shared-profile-card" data-entry="${esc(entry.entry_id)}" data-profile="${esc(profile.id)}">
      <header class="device-group-header"><button type="button" class="device-group-title" data-action="toggle-shared-profile" data-group="${esc(key)}" aria-expanded="${expanded}" aria-controls="${esc(id)}">
        <ha-icon class="device-chevron" icon="mdi:chevron-down" aria-hidden="true"></ha-icon><span class="device-label"><span class="device-name">${esc(profile.name)}</span>
        <span class="shared-profile-times muted">${esc(t("profileOpeningTime"))}: ${esc(profile.opening_time)} s · ${esc(t("profileClosingTime"))}: ${esc(profile.closing_time)} s</span></span>
        <span class="count">${profile.assigned_to.length} ${esc(t("profileAssociatedCovers"))}</span></button></header>
      <div id="${esc(id)}" class="entity-list shared-profile-body" ${expanded ? "" : "hidden"}>
        ${this._rows.get(entry.entry_id).data.capabilities?.profile_management ? `<div class="actions catalogue-actions">
          ${["edit", "duplicate", "delete"].map((action) => `<button type="button" data-action="manage-profile" data-id="${esc(profile.id)}" data-operation="${action}" data-entry="${esc(entry.entry_id)}" ${action === "delete" && profile.assigned_to.length ? `disabled title="${esc(t("profileDeleteHelp"))}"` : ""}>${esc(t(action === "edit" ? "edit" : action === "duplicate" ? "catalogueDuplicate" : "catalogueDelete"))}</button>`).join("")}
        </div>` : ""}
        ${this._provenance(profile)}
        ${followers.length ? `<ul class="shared-profile-followers">${followers.map((item) => this._follower(item)).join("")}</ul>`
          : `<p class="muted">${esc(t("profileListUnused"))}</p>`}
      </div></section>`;
  }

  _render() {
    if (!this._context?.host.isConnected) return;
    const { host, gateways, inventory, filters, t } = this._context;
    const html = `<p class="muted">${esc(t("profileListHelp"))}</p>
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
