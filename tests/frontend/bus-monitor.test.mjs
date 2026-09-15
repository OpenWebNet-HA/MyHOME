import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import { after, afterEach, test } from "node:test";
import { JSDOM } from "jsdom";

const dom = new JSDOM("<!doctype html><body></body>", { url: "http://localhost/", pretendToBeVisual: true });
for (const key of ["window", "document", "HTMLElement", "customElements"]) globalThis[key] = key === "window" ? dom.window : dom.window[key];
const { BusMonitorView } = await import("../../custom_components/myhome/frontend/panel/panel-bus-monitor-view.js");
const { BusMonitorSection } = await import("../../custom_components/myhome/frontend/panel/panel-bus-monitor.js");
customElements.define("test-native-monitor", class extends BusMonitorView {});
const tick = () => new Promise((resolve) => setImmediate(resolve));
const deferred = () => { let resolve; const promise = new Promise((r) => { resolve = r; }); return { promise, resolve }; };
const frame = (n, extra = {}) => ({ timestamp: 100 + n, direction: "rx", raw: `*1*1*${n}##`, who: "1", what: "1", where: String(n), ...extra });
const views = [];
function mount({ history = { frames: [] }, call, subscribe } = {}) {
  const view = document.createElement("test-native-monitor"); views.push(view);
  const calls = [], streams = [];
  const hass = { config: { version: "2026.9.1" }, connection: { subscribeMessage: async (callback, request) => {
    const stream = { callback, request, stopped: false }; streams.push(stream);
    if (subscribe) return subscribe(stream);
    return () => { stream.stopped = true; };
  } }, callService: async (...args) => { calls.push(args); }, callWS: async (request) => {
    calls.push(request);
    if (call) return call(request);
    if (request.type.endsWith("/history")) return history;
    return { gateway: { model: "F454", mac_prefix: "00:03:50", host: "192.0.2.1", password: "secret", integration_version: "2.0.0b12" } };
  } };
  view.configure({ mac: "00:03:50:00:00:01", title: '<img src=x onerror="bad()">', max_frames: 3 });
  document.body.append(view); view.hass = hass;
  return { view, hass, calls, streams, root: view.shadowRoot };
}
afterEach(() => { for (const view of views.splice(0)) { view.remove(); view.disconnectedCallback(); } document.body.replaceChildren(); });
after(() => dom.window.close());

const filter = (root, id, value) => { const control = root.getElementById(id); control.value = value; control.dispatchEvent(new dom.window.Event(control.tagName === "SELECT" ? "change" : "input")); };

test("native monitor imports without Lovelace registration and retains bounded stream, filters and pause", async () => {
  assert.equal(window.customCards, undefined); assert.equal(customElements.get("myhome-openwebnet-bus-monitor"), undefined);
  const { view, root, streams } = mount({ history: { frames: [frame(1)] } }); await tick();
  assert.equal(root.querySelector(".title img"), null);
  for (const item of [frame(2, { direction: "tx" }), frame(3, { who: "2", raw: "*2*0*3##" }), frame(4, { is_nack: true, raw: "*#*0##" })]) streams[0].callback(item);
  assert.equal(view._frames.length, 3);
  filter(root, "filter-dir", "tx"); assert.equal(root.querySelectorAll(".frame-line").length, 1);
  filter(root, "filter-dir", "nack"); assert.match(root.getElementById("stream").textContent, /\*#\*0##/);
  filter(root, "filter-dir", "all"); filter(root, "filter-who", "2"); assert.equal(root.querySelectorAll(".frame-line").length, 1);
  filter(root, "filter-who", "all"); filter(root, "filter-where", "raw:*2*"); assert.equal(root.querySelectorAll(".frame-line").length, 1);
  root.getElementById("btn-pause").click(); streams[0].callback(frame(5)); assert.equal(view._frames.at(-1).timestamp, 104);
  root.getElementById("btn-pause").click(); streams[0].callback(frame(6)); assert.equal(view._frames.at(-1).timestamp, 106);
});

test("native actions scope commands, clear and sweep to the selected gateway", async () => {
  const { view, root, calls, streams } = mount(); await tick();
  streams[0].callback(frame(1)); root.getElementById("send-frame").value = "*2*0*11##";
  await view._sendCustomFrame(); await view._handleSweepBus(); await view._clearBuffer();
  assert.equal(root.getElementById("send-frame").value, ""); assert.equal(view._frames.length, 0);
  for (const call of calls.filter((call) => call.type)) assert.equal(call.mac, "00:03:50:00:00:01");
  assert.ok(calls.some((call) => call.type === "myhome/bus_monitor/send" && call.frame === "*2*0*11##"));
  assert.ok(calls.some((call) => call.type === "myhome/bus_monitor/clear"));
  assert.deepEqual(calls.find(Array.isArray), ["myhome", "sweep_bus", { gateway: "00:03:50:00:00:01" }]);
  view.remove(); assert.equal(streams[0].stopped, true); assert.equal(view._timers.size, 0);
});

test("trace JSON preserves direction and description and excludes gateway credentials; clipboard stays available", async (context) => {
  const { view, streams } = mount(); await tick(); streams[0].callback(frame(1, { direction: "tx", description: "Light on" }));
  const blobs = [], downloads = [], opened = [];
  context.mock.method(URL, "createObjectURL", (blob) => { blobs.push(blob); return "blob:trace"; });
  context.mock.method(URL, "revokeObjectURL", () => {});
  context.mock.method(dom.window.HTMLAnchorElement.prototype, "click", function () { downloads.push(this.download); });
  context.mock.method(window, "open", (...args) => { opened.push(args); });
  let copied;
  view._copyToClipboard = async (text) => { copied = text; return true; };
  await view._handleExportTrace(); const json = JSON.parse(await blobs[0].text());
  assert.equal(json.frames[0].direction, "tx"); assert.equal(json.frames[0].description, "Light on");
  assert.equal("host" in json.gateway, false); assert.equal("password" in json.gateway, false);
  assert.match(downloads[0], /^myhome_gateway_trace_.*\.json$/);
  await view._handleReportIssue(); assert.match(copied, /\*1\*1\*1##/); assert.equal(copied.includes("secret"), false);
  assert.match(opened[0][0], /issues\/new/);
});

test("old history and callbacks cannot enter a replacement connection, and clear invalidates pending history", async () => {
  const oldHistory = deferred(); const { view, hass, streams } = mount({ history: oldHistory.promise }); await tick();
  const oldCallback = streams[0].callback;
  view.hass = { ...hass, connection: { subscribeMessage: async () => () => {} }, callWS: async () => ({ frames: [frame(9)] }) };
  await tick(); oldHistory.resolve({ frames: [frame(2)] }); oldCallback(frame(3)); await tick();
  assert.deepEqual(view._frames.map((f) => f.where), ["9"]); assert.equal(streams[0].stopped, true);
  const pending = deferred(); const other = mount({ history: pending.promise }); await tick();
  await other.view._clearBuffer(); pending.resolve({ frames: [frame(1)] }); await tick(); assert.equal(other.view._frames.length, 0);
});

test("gateway reconfiguration resets capture and late export cannot download after removal", async (context) => {
  const pending = deferred(); const { view, streams } = mount({ call: (request) => request.type.endsWith("/info") ? pending.promise : { frames: [] } }); await tick();
  streams[0].callback(frame(1)); view.configure({ mac: "00:03:50:00:00:02", title: "Other" }); await tick();
  assert.equal(streams[0].stopped, true); assert.equal(streams[1].request.mac, "00:03:50:00:00:02"); assert.equal(view._frames.length, 0);
  let downloads = 0; context.mock.method(URL, "createObjectURL", () => { downloads++; });
  const exporting = view._handleExportTrace(); view.remove(); pending.resolve({ gateway: { model: "Old" } }); await exporting;
  assert.equal(downloads, 0); assert.equal(view._timers.size, 0);
});

test("native section discards late imports and retries failed imports without a registry watchdog", async () => {
  const host = document.createElement("section"); document.body.append(host);
  const pending = deferred(); const section = new BusMonitorSection(() => pending.promise);
  const args = { container: host, entry: { entry_id: "one", mac: "00:03:50:00:00:01", title: "First", monitor_available: true }, t: (k) => k, empty: (text) => text };
  const rendering = section.render(args); section.clear(); pending.resolve({ BusMonitorView }); await rendering; assert.equal(section.view, null);
  let fail = true; const retry = new BusMonitorSection(async () => { if (fail) throw new Error("network"); return { BusMonitorView }; });
  await retry.render(args); assert.equal(host.textContent, "monitorError"); fail = false; await retry.render(args);
  assert.equal(retry.view.localName, "myhome-panel-bus-monitor"); const view = retry.view; await retry.render(args); assert.equal(retry.view, view);
  retry.clear(); await retry.render({ ...args, entry: { ...args.entry, mac: "" } }); assert.equal(retry.view, null);
});

test("stream failure retries once while mounted and removal cancels its timer", async (context) => {
  context.mock.timers.enable({ apis: ["setTimeout"] });
  let attempts = 0;
  const { view } = mount({ subscribe: async () => { attempts++; throw new Error("offline"); } }); await tick();
  assert.equal(attempts, 1); context.mock.timers.tick(1000); await tick(); assert.equal(attempts, 2);
  view.remove(); context.mock.timers.tick(30000); await tick(); assert.equal(attempts, 2); assert.equal(view._timers.size, 0);
});

test("legacy card adapter uses the same view and keeps both names and Lovelace configuration", async () => {
  // Resolve the HA static route to the actual local module for this Node test.
  const core = new URL("../../custom_components/myhome/frontend/panel/panel-bus-monitor-view.js?v=0.12.0", import.meta.url).href;
  const source = await readFile(new URL("../../custom_components/myhome/frontend/myhome-bus-card.js", import.meta.url), "utf8");
  assert.match(source, /from "\/myhome_static\/panel\/panel-bus-monitor-view.js\?v=0.12.0"/);
  await import(`data:text/javascript,${encodeURIComponent(source.replace('/myhome_static/panel/panel-bus-monitor-view.js?v=0.12.0', core))}`);
  for (const tag of ["myhome-openwebnet-bus-monitor", "myhome-bus-card"]) {
    const card = document.createElement(tag); views.push(card); card.setConfig({ title: "Existing dashboard" });
    assert.equal(card._config.title, "Existing dashboard"); assert.equal(card.getCardSize(), 6);
    assert.ok(card.shadowRoot.getElementById("btn-export")); assert.ok(card.constructor.getStubConfig());
  }
  assert.equal(window.customCards.filter((card) => card.type === "myhome-openwebnet-bus-monitor").length, 1);
});
