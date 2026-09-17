/** Compatibility entry point for existing Lovelace dashboards.
 * The panel loads BusMonitorView directly and does not import this adapter.
 * Use the integration route so the /local/ fallback works without copying dependencies.
 */
import { BusMonitorView } from "/myhome_static/panel/panel-bus-monitor-view.js?v=0.29.0";

class MyHomeBusCard extends BusMonitorView {
  static getStubConfig() {
    return {
      title: "MyHOME OpenWebNet Bus Monitor",
      max_frames: 200,
    };
  }

  static getConfigForm() {
    return {
      schema: [
        { name: "title", label: "Title", selector: { text: {} } },
        { name: "max_frames", label: "Max Frames in Buffer", selector: { number: { min: 50, max: 1000, step: 50, mode: "box" } } },
      ],
    };
  }

  setConfig(config) { this.configure(config); }
  getCardSize() { return 6; }
}

window.MyHomeBusCard = MyHomeBusCard;

function registerCardElements() {
  const ce = (typeof window !== "undefined" && window.customElements) || customElements;
  if (!ce) return;
  if (!ce.get("myhome-openwebnet-bus-monitor")) {
    try {
      ce.define("myhome-openwebnet-bus-monitor", MyHomeBusCard);
    } catch (e) {
      // Ignore if already registered in active scope
    }
  }
  if (!ce.get("myhome-bus-card")) {
    try {
      customElements.define("myhome-bus-card", class extends MyHomeBusCard {});
    } catch (e) {
      // Ignore if already registered in active scope
    }
  }
}

// 1. Initial immediate registration
registerCardElements();

// 2. Active self-healing watchdog for scoped-custom-element-registry replacements
if (typeof window !== "undefined") {
  let lastRegistry = window.customElements;
  const watcher = window.setInterval(() => {
    if (
      window.customElements !== lastRegistry ||
      (window.customElements && !window.customElements.get("myhome-openwebnet-bus-monitor"))
    ) {
      lastRegistry = window.customElements;
      registerCardElements();
    }
  }, 100);
  window.setTimeout(() => window.clearInterval(watcher), 45000);
}

console.info(
  "%c MYHOME-BUS-CARD %c compatibility 0.29.0 ",
  "background:#03a9f4;color:#fff;font-weight:bold;padding:2px 4px;border-radius:3px 0 0 3px;",
  "background:#263238;color:#fff;padding:2px 4px;border-radius:0 3px 3px 0;"
);

window.customCards = (window.customCards || []).filter((c) => c.type !== "myhome-bus-card");

const cardDefinition = {
  type: "myhome-openwebnet-bus-monitor",
  name: "MyHOME OpenWebNet Bus Monitor",
  description: "Real-time BTicino / Legrand SCS OpenWebNet bus traffic stream, packet inspector, and diagnostic frame sender.",
  preview: true,
};

// Guarantee registration whenever Lovelace card picker accesses card.type
Object.defineProperty(cardDefinition, "type", {
  get() {
    registerCardElements();
    return "myhome-openwebnet-bus-monitor";
  },
  set(val) {
    // allow assignment if needed
  },
  enumerable: true,
  configurable: true,
});

const existingIndex = window.customCards.findIndex(
  (c) => c && c.type === "myhome-openwebnet-bus-monitor"
);
if (existingIndex >= 0) {
  window.customCards[existingIndex] = cardDefinition;
} else {
  window.customCards.push(cardDefinition);
}
