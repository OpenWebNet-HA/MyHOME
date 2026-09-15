/** Bus section adapter. The shell owns navigation; this module owns its stream. */
export class BusMonitorSection {
  constructor() {
    this._generation = 0;
    this.card = null;
    this._key = null;
  }

  set hass(value) {
    this._hass = value;
    if (this.card) this.card.hass = value;
  }

  clear() {
    this._generation++;
    this.card?.remove();
    this.card = null;
    this._key = null;
  }

  async render({ container, entry, resourceUrl, t, empty }) {
    const key = JSON.stringify([entry?.entry_id, entry?.mac, entry?.monitor_available, resourceUrl]);
    if (key === this._key) return;
    this.clear();
    this._key = key;
    if (!entry?.monitor_available) {
      container.innerHTML = empty(t(entry ? "monitorOffline" : "monitorSelect"));
      return;
    }
    const generation = this._generation;
    container.innerHTML = empty(t("monitorLoading"));
    try {
      // Reuse the integration's versioned resource until its standalone card retires.
      await import(resourceUrl);
      if (generation !== this._generation || !container.isConnected) return;
      const card = document.createElement("myhome-openwebnet-bus-monitor");
      card.setConfig({ mac: entry.mac, title: `${t("bus")} · ${entry.title}`, max_frames: 200 });
      container.replaceChildren(card);
      this.card = card;
      card.hass = this._hass;
    } catch {
      if (generation === this._generation && container.isConnected) {
        this.card?.remove();
        this.card = null;
        container.innerHTML = empty(t("monitorError"));
        this._key = null;
      }
    }
  }
}
