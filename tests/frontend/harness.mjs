/**
 * Minimal DOM stub for testing the bus-monitor card's logic under node:test.
 *
 * The card only needs `shadowRoot.getElementById`, a few element properties
 * (innerHTML, textContent, disabled, style, classList, value) and the download
 * plumbing (document.createElement, Blob, URL). Everything is recorded so tests
 * can assert on labels, files and frames without a browser.
 */
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import path from "node:path";

const here = path.dirname(fileURLToPath(import.meta.url));
export const CARD_PATH = path.resolve(here, "../../custom_components/myhome/frontend/myhome-bus-card.js");

function makeElement(id) {
  const classes = new Set();
  return {
    id,
    innerHTML: "",
    textContent: "",
    className: "",
    value: "",
    checked: false,
    disabled: false,
    style: {},
    classList: {
      add: (c) => classes.add(c),
      remove: (c) => classes.delete(c),
      toggle: (c, force) => (force ? classes.add(c) : classes.delete(c)),
      contains: (c) => classes.has(c),
    },
    children: [],
    options: [],
    firstElementChild: null,
    scrollTop: 0,
    scrollHeight: 0,
    querySelector: () => null,
    addEventListener() {},
    appendChild(child) { this.children.push(child); this.firstElementChild = this.children[0]; },
    removeChild(child) { this.children = this.children.filter((c) => c !== child); this.firstElementChild = this.children[0] || null; },
    focus() {},
    click() {},
  };
}

/**
 * Load the card source once into a fresh set of globals and return a factory.
 * `downloads` collects every export the card performs ({ fileName, payload }).
 */
export function loadCard() {
  const downloads = [];
  const elements = {};
  const byId = (id) => elements[id] || (elements[id] = makeElement(id));

  globalThis.window = globalThis;
  Object.defineProperty(globalThis, "navigator", { value: { userAgent: "node-test" }, configurable: true });
  globalThis.HTMLElement = class {
    attachShadow() {
      this.shadowRoot = { getElementById: byId, querySelector: () => null, querySelectorAll: () => [] };
      return this.shadowRoot;
    }
  };
  globalThis.customElements = {
    _registry: {},
    define(name, cls) { this._registry[name] = cls; },
    get(name) { return this._registry[name]; },
  };
  let pendingPayload = null;
  globalThis.Blob = class { constructor(parts) { pendingPayload = JSON.parse(parts[0]); } };
  globalThis.URL = { createObjectURL: () => "blob:test", revokeObjectURL() {} };
  const anchor = makeElement("download-anchor");
  Object.defineProperty(anchor, "download", {
    set(fileName) { downloads.push({ fileName, payload: pendingPayload }); },
    get() { return downloads.length ? downloads[downloads.length - 1].fileName : ""; },
  });
  globalThis.document = {
    createElement: (tag) => (tag === "a" ? anchor : makeElement(`${tag}-${Math.random()}`)),
    body: { appendChild() {}, removeChild() {} },
  };

  // The card installs a module-level 45 s registry watchdog (setInterval), UI
  // banner / flash timeouts (1.5-6 s) and a 100 ms stopwatch interval. Unref the
  // intervals and the long timeouts so the test process exits when the tests do;
  // node still sees them, so a timer-related crash would surface. Short timeouts
  // stay referenced: a test that awaits `setTimeout(r, 0)` must not find the loop
  // already drained (node 22 then cancels every remaining test).
  const realSetTimeout = globalThis.setTimeout;
  const realSetInterval = globalThis.setInterval;
  globalThis.setTimeout = (fn, ms, ...args) => {
    const t = realSetTimeout(fn, ms, ...args);
    if ((ms || 0) >= 1000) t.unref?.();
    return t;
  };
  globalThis.setInterval = (fn, ms, ...args) => { const t = realSetInterval(fn, ms, ...args); t.unref?.(); return t; };

  const silence = { info() {}, debug() {}, log() {}, warn() {}, error() {} };
  const realConsole = globalThis.console;
  globalThis.console = { ...realConsole, ...silence };
  try {
    new Function(readFileSync(CARD_PATH, "utf8"))();
  } finally {
    globalThis.console = realConsole;
  }

  const Card = customElements.get("myhome-bus-card");

  return {
    downloads,
    el: byId,
    create({ model = "MH200N", connected = true } = {}) {
      const card = new Card();
      card.attachShadow();
      card._config = { title: "test" };
      card._gatewayInfo = { model, integration_version: "2.0.0b12", ownd_version: "2.0.0b6" };
      card._stats = { total_rx: 0, total_tx: 0 };
      card._maxDisplayFrames = 200;
      card._connectionStatus = connected ? "connected" : "disconnected";
      card.sent = [];
      card.services = [];
      card._hass = {
        config: { version: "2026.9.2" },
        callWS: async (payload) => {
          if (payload.type === "myhome/bus_monitor/send") card.sent.push(payload.frame);
          return {};
        },
        callService: async (domain, service, data) => { card.services.push(`${domain}.${service}`); return data; },
      };
      return card;
    },
  };
}

/** A parsed-looking frame as the backend streams it. */
export function frame(raw, { timestamp = 1000, direction = "rx", who = "2", where = "21" } = {}) {
  return { timestamp, iso_time: new Date(timestamp * 1000).toISOString(), direction, raw, who, where, what: null, dimension: null, is_ack: false, is_nack: false };
}
