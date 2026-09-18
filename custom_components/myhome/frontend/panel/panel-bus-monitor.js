/** Native bus section. The shell owns navigation; this module owns its view/stream. */
const viewUrl = new URL("panel-bus-monitor-view.js", import.meta.url);
viewUrl.search = new URL(import.meta.url).search;
export class BusMonitorSection {
  constructor(loadView = () => import(viewUrl.href)) {
    this._loadView = loadView;
    this._generation = 0;
    this.view = null;
    this._key = null;
  }

  set hass(value) {
    this._hass = value;
    if (this.view) this.view.hass = value;
  }

  clear() {
    this._generation++;
    this.view?.remove();
    this.view = null;
    this._key = null;
  }

  async render({ container, entry, t, empty }) {
    const key = JSON.stringify([entry?.entry_id, entry?.mac, entry?.monitor_available]);
    if (key === this._key) {
      this.view?.setHeading(`${t("bus")} · ${entry.title}`);
      return;
    }
    this.clear();
    this._key = key;
    if (!entry?.monitor_available || !entry?.mac?.trim()) {
      container.innerHTML = empty(t(entry ? "monitorOffline" : "monitorSelect"));
      return;
    }
    const generation = this._generation;
    container.innerHTML = empty(t("monitorLoading"));
    try {
      const { BusMonitorView } = await this._loadView();
      if (generation !== this._generation || !container.isConnected) return;
      if (!customElements.get("myhome-panel-bus-monitor")) {
        customElements.define("myhome-panel-bus-monitor", class extends BusMonitorView {});
      }
      const view = document.createElement("myhome-panel-bus-monitor");
      view.configure({ mac: entry.mac, title: `${t("bus")} · ${entry.title}`, max_frames: 200 });
      container.replaceChildren(view);
      this.view = view;
      view.hass = this._hass;
    } catch {
      if (generation === this._generation && container.isConnected) {
        this.view?.remove();
        this.view = null;
        container.innerHTML = empty(t("monitorError"));
        this._key = null;
      }
    }
  }
}
