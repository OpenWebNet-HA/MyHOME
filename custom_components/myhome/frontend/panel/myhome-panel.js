/** MyHOME sidepanel. Configuration stays in Home Assistant's native registries. */
const MODULE_VERSION = new URL(import.meta.url).searchParams.get("v");
const assetUrl = (name) => {
  const url = new URL(name, import.meta.url);
  url.search = new URL(import.meta.url).search;
  return url.href;
};
const [{ translations }, model] = await Promise.all([
  import(assetUrl("panel-translations.js")), import(assetUrl("panel-model.js")),
]);
const escapeHtml = (value) => String(value ?? "").replace(/[&<>"']/g, (char) => ({
  "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
})[char]);
const SETTINGS_URL = "/config/integrations/integration/myhome";
const CATEGORY_VIEW_STORAGE_KEY = "myhome-panel-category-view-v1";
const deviceUrl = (id) => `/config/devices/device/${encodeURIComponent(id)}`;

class MyHomePanel extends HTMLElement {
  constructor() {
    super();
    this.attachShadow({ mode: "open" });
    this.shadowRoot.addEventListener("click", (event) => this._onClick(event));
    this._entryId = "";
    this._view = "entities";
    this._filters = { query: "", category: "", area: "" };
    this._categoryMode = "all";
    this._selectedWho = "";
    try {
      const saved = JSON.parse(window.localStorage.getItem(CATEGORY_VIEW_STORAGE_KEY));
      if (["all", "single"].includes(saved?.mode)) this._categoryMode = saved.mode;
      if (typeof saved?.who === "string" && (/^\d+$/.test(saved.who) || saved.who === model.WHO_UNKNOWN)) this._selectedWho = saved.who;
    } catch { /* Navigation remains available when browser storage is blocked. */ }
    this._session = 0;
    this._unsubs = [];
    this._monitorToken = 0;
  }

  set hass(value) {
    const previous = this._hass;
    this._hass = value;
    if (!this.isConnected || !value) return;
    if (!previous || previous.connection !== value.connection) {
      this._stop();
      this._start();
    } else if (previous.language !== value.language) {
      this._buildShell();
      this._renderInventory();
    } else {
      this._updateStates();
    }
    this._updateMenu();
    if (this._monitor) this._monitor.hass = value;
  }

  get hass() { return this._hass; }
  set panel(value) { this._panel = value; this._renderVersions(); }
  set narrow(value) { this._narrow = value; this._updateMenu(); }

  connectedCallback() {
    // HA can assign properties while this module is still awaiting its imports.
    // Replay those own properties through the setters after the element upgrades,
    // otherwise they shadow the setters and the first mount never starts.
    // Restore panel/menu configuration before hass can initiate rendering.
    for (const property of ["panel", "narrow", "hass"]) {
      if (!Object.prototype.hasOwnProperty.call(this, property)) continue;
      const value = this[property];
      delete this[property];
      this[property] = value;
    }
    if (this._hass) this._start();
  }
  disconnectedCallback() { this._stop(); }

  _t(key) {
    const language = (this._hass?.language || "en").split("-")[0];
    return translations[language]?.[key] || translations.en[key] || key;
  }

  _start() {
    if (this._timer) return;
    this._buildShell();
    this._renderInventory();
    this._refresh();
    // Registry events update configuration; polling also catches runtime status
    // and missed events after reconnects without adding a custom event protocol.
    this._timer = setInterval(() => this._refresh(), 15000);
    const session = this._session;
    for (const type of ["entity_registry_updated", "device_registry_updated", "area_registry_updated"]) {
      this._hass.connection.subscribeEvents(() => {
        clearTimeout(this._refreshTimer);
        this._refreshTimer = setTimeout(() => this._refresh(), 150);
      }, type).then((unsub) => {
        if (!this.isConnected || session !== this._session) unsub();
        else this._unsubs.push(unsub);
      }).catch(() => { /* Polling remains available if event subscription fails. */ });
    }
  }

  _stop() {
    this._session++;
    clearInterval(this._timer);
    clearTimeout(this._refreshTimer);
    this._timer = null;
    this._loading = false;
    this._refreshAgain = false;
    for (const unsub of this._unsubs.splice(0)) unsub();
    this._removeMonitor();
    this.shadowRoot.querySelector("dialog")?.close();
  }

  async _refresh() {
    if (!this.isConnected || !this._hass) return;
    if (this._loading) { this._refreshAgain = true; return; }
    const session = this._session;
    this._loading = true;
    this._setBusy(true);
    try {
      const data = await this._hass.callWS({ type: "myhome/panel/inventory" });
      if (session !== this._session || !this.isConnected) return;
      const changed = JSON.stringify(this._data) !== JSON.stringify(data);
      this._data = data;
      this._showError("");
      if (!this._selectedInitially && data.gateways.length) {
        this._entryId = data.gateways.length === 1 ? data.gateways[0].entry_id : "";
        this._selectedInitially = true;
      }
      if (this._entryId && !data.gateways.some((entry) => entry.entry_id === this._entryId)) this._entryId = "";
      if (changed) this._renderInventory();
    } catch (error) {
      if (session === this._session) this._showError(this._t("loadError"));
    } finally {
      if (session === this._session) {
        this._loading = false;
        this._setBusy(false);
        if (this._refreshAgain) { this._refreshAgain = false; this._refresh(); }
      }
    }
  }

  _setBusy(busy) {
    const button = this.shadowRoot.querySelector('[data-action="refresh"]');
    if (button) button.disabled = busy;
  }

  _showError(message) {
    const element = this.shadowRoot.getElementById("error");
    if (element) { element.textContent = message; element.hidden = !message; }
  }

  _updateMenu() {
    const menu = this.shadowRoot.querySelector("ha-menu-button");
    if (menu) { menu.hass = this._hass; menu.narrow = this._narrow; }
  }

  _renderVersions() {
    // Show the bundle actually loaded by this tab, even after a backend update.
    const version = MODULE_VERSION || this._panel?.config?.panel_version || this._data?.panel_version;
    const label = this.shadowRoot.getElementById("panel-version");
    if (label) label.textContent = `${this._t("panelVersion")}${version ? ` v${version}` : ""}`;
    const integration = this.shadowRoot.getElementById("version");
    if (integration) integration.textContent = this._data?.version ? `${this._t("integrationVersion")} v${this._data.version}` : "";
  }

  _buildShell() {
    this._removeMonitor();
    const t = (key) => escapeHtml(this._t(key));
    this.shadowRoot.innerHTML = `
      <link rel="stylesheet" href="${escapeHtml(assetUrl("myhome-panel.css"))}">
      <header class="topbar"><ha-menu-button></ha-menu-button>
        <div class="brand-group"><div class="brand">My<span>HOME</span></div><span class="version" id="panel-version"></span></div>
        <a class="button" href="${SETTINGS_URL}">${t("settings")}</a>
      </header>
      <main>
        <div class="heading"><div><h1>${t("subtitle")}</h1><p class="muted" id="totals"></p></div>
          <label class="gateway-select">${t("gateway")}<select id="gateway" aria-label="${t("gateway")}"></select></label>
        </div>
        <div id="error" class="notice error" role="alert" hidden></div>
        <section id="gateways" class="gateway-grid" aria-label="${t("gateway")}"></section>
        <nav class="tabs" aria-label="MyHOME">
          ${["entities", "devices", "bus"].map((view) => `<button data-view="${view}" aria-pressed="${view === this._view}">${t(view)} <span class="count" id="count-${view}" ${view === "bus" ? "hidden" : ""}></span></button>`).join("")}
          <button data-action="refresh">${t("refresh")}</button>
        </nav>
        <section id="who-navigation" class="who-navigation" hidden>
          <div class="who-toolbar"><p id="category-view-label" class="muted" aria-live="polite"></p>
            <button type="button" data-action="toggle-category-view" aria-controls="items"></button>
          </div>
          <nav id="who-buttons" class="who-buttons" aria-label="${t("whoCategory")}"></nav>
        </section>
        <div class="filters" id="filters">
          <label>${t("search")}<input id="search" type="search" value="${escapeHtml(this._filters.query)}"></label>
          <label>${t("category")}<select id="category"></select></label>
          <label>${t("area")}<select id="area"></select></label>
        </div>
        <p class="notice muted" id="discovery-help">${t("discoveryHelp")}</p>
        <section id="items" class="who-groups"></section>
        <section id="monitor" hidden></section>
        <p id="toast" class="muted" role="status"></p>
        <p id="version" class="muted"></p>
      </main><div id="dialog-host"></div>`;
    this.shadowRoot.getElementById("gateway").onchange = (event) => {
      this._entryId = event.target.value;
      this._renderInventory();
    };
    for (const [id, key] of [["search", "query"], ["category", "category"], ["area", "area"]]) {
      this.shadowRoot.getElementById(id).addEventListener(id === "search" ? "input" : "change", (event) => {
        this._filters[key] = event.target.value;
        this._renderContent();
      });
    }
    this._updateMenu();
    this._renderVersions();
  }

  _scope() { return model.scopedInventory(this._data, this._entryId); }

  _renderInventory() {
    const root = this.shadowRoot;
    if (!root.getElementById("items")) return;
    if (!this._data) {
      root.getElementById("items").innerHTML = this._empty(this._t("loading"));
      return;
    }
    const data = this._data;
    const scope = this._scope();
    const t = (key) => escapeHtml(this._t(key));
    this._renderVersions();
    root.getElementById("totals").textContent = `${scope.devices.length} ${this._t("devices").toLocaleLowerCase()} · ${scope.entities.length} ${this._t("entities").toLocaleLowerCase()}`;
    root.getElementById("gateway").innerHTML = `<option value="">${t("allGateways")}</option>` + data.gateways.map((item) => `<option value="${escapeHtml(item.entry_id)}">${escapeHtml(item.title)}</option>`).join("");
    root.getElementById("gateway").value = this._entryId;
    root.getElementById("gateways").innerHTML = scope.gateways.map((item) => {
      const status = item.disabled_by ? "disabled" : item.state === "loaded" ? (item.connected ? "connected" : "disconnected") : item.state;
      const devices = data.devices.filter((device) => device.entry_ids.includes(item.entry_id)).length;
      const entities = data.entities.filter((entity) => entity.entry_id === item.entry_id).length;
      return `<article class="gateway-card"><div class="card-head"><h2>${escapeHtml(item.title)}</h2>
        <span class="badge ${item.connected ? "online" : "offline"}">${t(status)}</span></div>
        <p class="muted">${escapeHtml([item.model, item.host ? `${item.host}${item.port ? `:${item.port}` : ""}` : item.serial_port].filter(Boolean).join(" · "))}</p>
        <div class="gateway-meta"><span>${devices} ${t("devices")}</span><span>${entities} ${t("entities")}</span>
        ${item.firmware ? `<span>${t("firmware")} ${escapeHtml(item.firmware)}</span>` : ""}</div></article>`;
    }).join("");
    for (const view of ["devices", "entities"]) root.getElementById(`count-${view}`).textContent = scope[view].length;
    const categories = [...new Set(scope.entities.map((item) => item.domain))].sort();
    if (this._filters.category && !categories.includes(this._filters.category)) this._filters.category = "";
    root.getElementById("category").innerHTML = `<option value="">${t("allCategories")}</option>` + categories.map((category) => `<option value="${escapeHtml(category)}">${t(category)}</option>`).join("");
    root.getElementById("category").value = this._filters.category;
    if (this._filters.area && this._filters.area !== "__none__" && !data.areas.some((area) => area.id === this._filters.area)) this._filters.area = "";
    root.getElementById("area").innerHTML = `<option value="">${t("allAreas")}</option><option value="__none__">${t("noArea")}</option>` + this._areaOptions();
    root.getElementById("area").value = this._filters.area;
    this._renderContent();
  }

  _areaOptions() {
    return [...this._data.areas].sort((a, b) => a.name.localeCompare(b.name)).map((item) => `<option value="${escapeHtml(item.id)}">${escapeHtml(item.name)}</option>`).join("");
  }

  _empty(title, description = "") {
    return `<div class="empty"><h2>${escapeHtml(title)}</h2><p class="muted">${escapeHtml(description)}</p></div>`;
  }

  _whoLabel(who) {
    if (who === model.WHO_UNKNOWN) return this._t("whoUnknown");
    const key = `who_${who}`;
    const label = this._t(key);
    return label === key ? `WHO ${who}` : `WHO ${who} · ${label}`;
  }

  _setCategoryView(mode, who = this._selectedWho) {
    this._categoryMode = mode;
    this._selectedWho = who;
    try {
      window.localStorage.setItem(CATEGORY_VIEW_STORAGE_KEY, JSON.stringify({ mode, who }));
    } catch { /* This preference is optional; it never changes HA configuration. */ }
    this._renderContent();
  }

  _renderCategoryNavigation(groups) {
    const root = this.shadowRoot;
    if (!groups.some(([who]) => who === this._selectedWho)) this._selectedWho = groups[0]?.[0] || "";
    root.getElementById("who-navigation").hidden = !groups.length;
    const all = this._categoryMode === "all";
    root.getElementById("category-view-label").textContent = all ? this._t("allWhoCategories") : this._whoLabel(this._selectedWho);
    const toggle = root.querySelector('[data-action="toggle-category-view"]');
    toggle.innerHTML = `<ha-icon icon="mdi:${all ? "tab" : "view-sequential"}" aria-hidden="true"></ha-icon><span>${escapeHtml(this._t(all ? "showSelectedCategory" : "showAllCategories"))}</span>`;
    const nav = root.getElementById("who-buttons");
    const buttons = groups.map(([who, members]) => `<button type="button" data-action="select-who" data-who="${escapeHtml(who)}" aria-pressed="${!all && who === this._selectedWho}" aria-controls="items">
      <span>${escapeHtml(this._whoLabel(who))}</span><span class="count">${members.length}</span></button>`).join("");
    // Keep keyboard focus and horizontal position when registry updates rebuild buttons.
    if (nav.innerHTML !== buttons) {
      const focusedWho = nav.contains(root.activeElement) ? root.activeElement.dataset.who : null;
      const scrollLeft = nav.scrollLeft;
      nav.innerHTML = buttons;
      if (focusedWho) [...nav.children].find((button) => button.dataset.who === focusedWho)?.focus({ preventScroll: true });
      nav.scrollLeft = scrollLeft;
    }
  }

  _renderContent() {
    if (!this._data) return;
    const root = this.shadowRoot;
    const isBus = this._view === "bus";
    root.getElementById("who-navigation").hidden = isBus || !this._data.gateways.length;
    for (const button of root.querySelectorAll("[data-view]")) button.setAttribute("aria-pressed", String(button.dataset.view === this._view));
    root.getElementById("filters").hidden = isBus || !this._data.gateways.length;
    root.getElementById("discovery-help").hidden = isBus || !this._data.gateways.length;
    root.getElementById("items").hidden = isBus;
    root.getElementById("monitor").hidden = !isBus;
    if (isBus) { this._renderMonitor(); return; }
    this._removeMonitor();
    if (!this._data.gateways.length) {
      root.getElementById("items").innerHTML = this._empty(this._t("noGateways"), this._t("noGatewaysHelp"));
      return;
    }
    const scope = this._scope();
    const groups = model.groupByWho(scope[this._view]);
    this._renderCategoryNavigation(groups);
    const filters = { ...this._filters, who: this._categoryMode === "single" ? this._selectedWho : "" };
    const items = model.filterItems(this._data, scope, this._view, filters, this._hass);
    items.sort((a, b) => this._itemName(a).localeCompare(this._itemName(b)));
    root.getElementById("items").innerHTML = model.groupByWho(items).map(([who, members]) => `
      <section class="who-group" data-who="${escapeHtml(who)}" aria-labelledby="who-title-${escapeHtml(who)}">
        <div class="who-heading"><h2 id="who-title-${escapeHtml(who)}">${escapeHtml(this._whoLabel(who))}</h2>
          <span class="count">${members.length} ${escapeHtml(this._t(this._view))}</span></div>
        <div class="item-grid">${members.map((item) => this._itemCard(item, scope)).join("")}</div>
      </section>`).join("") || this._empty(this._t("noResults"), this._t("noResultsHelp"));
    this._updateStates();
  }

  _itemName(item) {
    return item.entity_id ? model.entityName(item, this._hass) : item.name_by_user || item.name || item.id;
  }

  _addressDetails(item) {
    const address = item.address;
    if (!address) return `<p class="address muted">${escapeHtml(this._t("address"))}: ${escapeHtml(this._t("addressUnknown"))}</p>`;
    const fields = [[this._t("address"), address.raw]];
    if (address.a != null && address.pl != null) fields.push(["A", address.a], ["PL", address.pl]);
    if (address.interface != null) fields.push([this._t("busInterface"), address.interface]);
    return `<dl class="address">${fields.map(([label, value]) => `<div><dt>${escapeHtml(label)}:</dt><dd>${escapeHtml(value)}</dd></div>`).join("")}</dl>`;
  }

  _itemCard(item, scope) {
    const isEntity = !!item.entity_id;
    const t = (key) => escapeHtml(this._t(key));
    const id = escapeHtml(isEntity ? item.entity_id : item.id);
    const linked = isEntity ? [item] : scope.entities.filter((entity) => entity.device_id === item.id);
    const areaId = isEntity ? model.effectiveArea(item, this._data.devices) : item.area_id;
    const area = this._data.areas.find((entry) => entry.id === areaId)?.name || this._t("noArea");
    const categories = [...new Set(linked.map((entity) => this._t(entity.domain)))];
    const description = isEntity ? item.entity_id : [item.manufacturer, item.model].filter(Boolean).join(" · ");
    return `<article class="item-card"><div class="card-head"><h2>${escapeHtml(this._itemName(item))}</h2>${item.disabled_by ? `<span class="badge">${t("disabled")}</span>` : ""}</div>
      <p class="muted">${escapeHtml(description)}</p><div class="chips"><span class="chip">${escapeHtml(area)}</span>${categories.map((category) => `<span class="chip">${escapeHtml(category)}</span>`).join("")}${item.hidden_by ? `<span class="chip">${t("hidden")}</span>` : ""}</div>
      ${this._addressDetails(item)}
      <p class="muted">${escapeHtml(isEntity ? item.unique_id : item.identifiers.join(" · "))}</p>
      ${isEntity ? `<p class="state" data-state="${id}" aria-label="${t("state")}"></p>` : `<p class="muted">${linked.length ? `${linked.length} ${t("entities")}` : t("noEntities")}</p>`}
      <div class="actions"><button data-action="edit-${isEntity ? "entity" : "device"}" data-id="${id}">${t("edit")}</button>
      ${isEntity ? `<button data-action="details" data-id="${id}">${t("details")}</button>` : `<a class="button" href="${escapeHtml(deviceUrl(item.id))}">${t("openDevice")}</a>`}</div></article>`;
  }

  _updateStates() {
    if (!this._data) return;
    for (const element of this.shadowRoot.querySelectorAll("[data-state]")) {
      const entity = this._data.entities.find((item) => item.entity_id === element.dataset.state);
      const state = this._hass?.states?.[element.dataset.state];
      const text = entity?.disabled_by ? this._t("disabled") : !state ? this._t("unavailable")
        : this._hass.formatEntityState ? this._hass.formatEntityState(state)
          : `${this._t(state.state)}${state.attributes?.unit_of_measurement ? ` ${state.attributes.unit_of_measurement}` : ""}`;
      if (element.textContent !== text) element.textContent = text;
    }
  }

  _onClick(event) {
    const target = event.target.closest("button, a");
    if (!target) return;
    if (target.matches("a") && !event.ctrlKey && !event.metaKey && !event.shiftKey && !event.altKey) {
      event.preventDefault();
      history.pushState(null, "", target.getAttribute("href"));
      window.dispatchEvent(new Event("location-changed"));
    } else if (target.dataset.view) {
      this._view = target.dataset.view;
      this._renderContent();
    } else if (target.dataset.action === "select-who") {
      this._setCategoryView("single", target.dataset.who);
    } else if (target.dataset.action === "toggle-category-view") {
      this._setCategoryView(this._categoryMode === "all" ? "single" : "all");
    } else if (target.dataset.action === "refresh") {
      this._refresh();
      if (this._view === "bus" && !this._monitor) { this._monitorKey = null; this._renderMonitor(); }
    } else if (target.dataset.action?.startsWith("edit-")) {
      this._openEditor(target.dataset.action.slice(5), target.dataset.id);
    } else if (["details", "entity-settings"].includes(target.dataset.action)) {
      this.shadowRoot.querySelector("dialog")?.close();
      this.dispatchEvent(new CustomEvent("hass-more-info", {
        detail: {
          entityId: target.dataset.id,
          ...(target.dataset.action === "entity-settings" ? { view: "settings", tab: "settings" } : {}),
        }, bubbles: true, composed: true,
      }));
    }
  }

  _openEditor(kind, id) {
    const item = kind === "device" ? this._data.devices.find((entry) => entry.id === id)
      : this._data.entities.find((entry) => entry.entity_id === id);
    if (!item) return;
    const t = (key) => escapeHtml(this._t(key));
    const host = this.shadowRoot.getElementById("dialog-host");
    host.innerHTML = `<dialog aria-labelledby="editor-title"><form>
      <h2 id="editor-title">${t(kind === "device" ? "editDevice" : "editEntity")}</h2>
      <p class="muted">${escapeHtml(this._itemName(item))}</p>
      <label>${t("name")}<input name="name" autocomplete="off" value="${escapeHtml(kind === "device" ? item.name_by_user : item.name)}" placeholder="${escapeHtml(kind === "device" ? item.name : item.original_name)}"></label>
      <p class="muted">${t("nameHelp")}</p>
      <label>${t("area")}<select name="area"><option value="">${t(kind === "device" ? "noArea" : "inheritedArea")}</option>${this._areaOptions()}</select></label>
      ${kind === "entity" ? `<button type="button" data-action="entity-settings" data-id="${escapeHtml(id)}">${t("nativeSettings")}</button>` : ""}
      <p class="error" role="alert" id="save-error" hidden></p>
      <div class="actions"><button type="button" id="cancel">${t("cancel")}</button><button type="submit" class="primary">${t("save")}</button></div>
    </form></dialog>`;
    const dialog = host.querySelector("dialog");
    const form = host.querySelector("form");
    form.elements.area.value = item.area_id || "";
    host.querySelector("#cancel").onclick = () => dialog.close();
    dialog.oncancel = (event) => { if (form.dataset.saving) event.preventDefault(); };
    form.onsubmit = async (event) => {
      event.preventDefault();
      if (form.dataset.saving) return;
      const changes = model.registryChanges(kind, item, form.elements.name.value, form.elements.area.value);
      if (!Object.keys(changes).length) { dialog.close(); return; }
      form.dataset.saving = "true";
      for (const field of form.querySelectorAll("input, select, button")) field.disabled = true;
      const save = form.querySelector('[type="submit"]');
      save.textContent = this._t("saving");
      try {
        await this._hass.callWS({
          type: `config/${kind}_registry/update`,
          [kind === "device" ? "device_id" : "entity_id"]: id,
          ...changes,
        });
        dialog.close();
        this.shadowRoot.getElementById("toast").textContent = this._t("saved");
        await this._refresh();
      } catch (error) {
        const message = host.querySelector("#save-error");
        message.textContent = `${this._t("saveError")} ${error.message || error.code || ""}`;
        message.hidden = false;
      } finally {
        delete form.dataset.saving;
        for (const field of form.querySelectorAll("input, select, button")) field.disabled = false;
        save.textContent = this._t("save");
      }
    };
    dialog.showModal();
  }

  _removeMonitor() {
    this._monitorToken++;
    this._monitor?.remove();
    this._monitor = null;
    this._monitorKey = null;
  }

  async _renderMonitor() {
    if (!this._data) return;
    const entry = this._data.gateways.find((item) => item.entry_id === this._entryId);
    const key = `${entry?.entry_id || "all"}:${entry?.monitor_available}`;
    if (key === this._monitorKey) return;
    this._removeMonitor();
    this._monitorKey = key;
    const container = this.shadowRoot.getElementById("monitor");
    if (!entry || !entry.monitor_available) {
      container.innerHTML = this._empty(this._t(entry ? "monitorOffline" : "monitorSelect"));
      return;
    }
    const token = this._monitorToken;
    container.innerHTML = this._empty(this._t("monitorLoading"));
    try {
      // Import the same content-versioned resource registered by the integration.
      // A separate card instance per gateway isolates history and subscriptions.
      await import(this._panel?.config?.bus_card_url || "/myhome_static/myhome-bus-card.js");
      if (!this.isConnected || token !== this._monitorToken) return;
      const card = document.createElement("myhome-openwebnet-bus-monitor");
      card.setConfig({ mac: entry.mac, title: `${this._t("bus")} · ${entry.title}`, max_frames: 200 });
      container.replaceChildren(card);
      this._monitor = card;
      card.hass = this._hass;
    } catch (error) {
      if (token === this._monitorToken) {
        container.innerHTML = this._empty(this._t("monitorError"));
        this._monitorKey = null;
      }
    }
  }
}

if (!customElements.get("myhome-panel")) customElements.define("myhome-panel", MyHomePanel);
