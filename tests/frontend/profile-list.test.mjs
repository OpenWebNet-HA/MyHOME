import assert from "node:assert/strict";
import { after, afterEach, test } from "node:test";
import { JSDOM } from "jsdom";
import { CoverProfileList } from "../../custom_components/myhome/frontend/panel/panel-cover-profile-list.js";
import { translations } from "../../custom_components/myhome/frontend/panel/panel-translations.js";

const dom = new JSDOM("<!doctype html><body></body>", { pretendToBeVisual: true });
const { document } = dom.window;
globalThis.document = document;
const tick = () => new Promise((resolve) => setImmediate(resolve));
const deferred = () => { let resolve; const promise = new Promise((done) => { resolve = done; }); return { resolve, promise }; };
const t = (key) => translations.it[key] || translations.en[key] || key;
const instances = [];

function overview(entry_id = "one") {
  const evidence = { source: "guided", recorded_at: "2026-09-17T12:00:00+00:00", origin_entity_id: "cover.kitchen", origin_name: "Cucina" };
  return { entry_id, revision: 1, profiles: [
    { id: "shared", name: "Alluminio", opening_time: 20, closing_time: 30,
      assigned_to: ["cover.kitchen", "cover.bedroom", null], provenance: { opening: evidence, closing: { ...evidence, source: "automatic" } } },
    { id: "unused", name: "Legno", opening_time: 40, closing_time: 50, assigned_to: [] },
  ], covers: [
    { entity_id: "cover.kitchen", name: "Cucina", available: true, overrides: {}, effective: { opening: { value: 20 }, closing: { value: 30 } } },
    { entity_id: "cover.bedroom", name: "Camera", available: false, pending: true, overrides: { opening: { value: 12 } },
      effective: { opening: { value: 15 }, closing: { value: 25 } }, configured: { opening: { value: 12 }, closing: { value: 30 } } },
  ] };
}

async function mount({ read, subscribe, gateways = [{ entry_id: "one", title: "Casa" }] } = {}) {
  const host = document.createElement("div"); document.body.append(host);
  const view = new CoverProfileList(); instances.push(view);
  const calls = [], subscriptions = [];
  const context = { host, gateways, t, filters: { query: "", category: "", area: "" },
    inventory: { devices: [{ id: "kitchen", area_id: "living" }], areas: [{ id: "living", name: "Soggiorno" }], entities: [
      { entity_id: "cover.kitchen", entry_id: "one", domain: "cover", name: "Cucina", device_id: "kitchen", address: { raw: "11", a: "1", pl: "1" } },
      { entity_id: "cover.bedroom", entry_id: "one", domain: "cover", name: "Camera", address: { raw: "12", a: "1", pl: "2" } },
    ] },
    addressDetails: (entity) => `<p>A: ${entity.address.a} PL: ${entity.address.pl}</p>`,
    hass: { language: "it", states: {}, callWS: async (message) => {
      calls.push(message); return read ? read(message) : overview(message.entry_id);
    }, connection: { subscribeMessage: async (callback, request) => {
      const sub = { callback, request, stopped: 0 }; subscriptions.push(sub);
      const stop = () => { sub.stopped++; };
      return subscribe ? subscribe(sub, stop) : stop;
    } } } };
  view.show(context); await tick();
  return { host, view, context, calls, subscriptions };
}
function expand(view, entry = "one", id = "shared") { view.toggle(JSON.stringify([entry, id])); }
afterEach(() => { instances.splice(0).forEach((view) => view.clear()); document.body.replaceChildren(); delete document.hidden; });
after(() => dom.window.close());

test("cards start collapsed and show complete associations, effective times, evidence and pending overrides", async () => {
  const { host, view, calls } = await mount();
  assert.deepEqual(calls, [{ type: "myhome/cover_profiles/overview", entry_id: "one" }]);
  assert.equal(host.querySelectorAll('.shared-profile-card [aria-expanded="false"]').length, 2);
  const summary = host.querySelector('[data-profile="shared"] header');
  assert.match(summary.textContent, /Alluminio.*20 s.*30 s.*3 tapparelle/s);
  expand(view);
  const card = host.querySelector('[data-profile="shared"]');
  assert.equal(card.querySelector(".shared-profile-body").hidden, false);
  assert.equal(card.querySelectorAll(".shared-profile-follower").length, 3);
  assert.match(card.textContent, /A: 1 PL: 1/);
  assert.match(card.textContent, /Segue il profilo/);
  assert.match(card.textContent, /Valori personali.*Solo apertura/s);
  assert.match(card.textContent, /15 s.*25 s/s);
  assert.match(card.textContent, /12 \/ 30 s/);
  assert.match(card.textContent, /Cucina/);
  assert.match(card.textContent, /17 set 2026/);
  assert.equal(card.querySelectorAll('[data-action="cover-profile"]').length, 2);
  expand(view, "one", "unused");
  assert.match(host.querySelector('[data-profile="unused"]').textContent, /Nessuna tapparella associata/);
  assert.equal(host.querySelector('[data-action="calibrate"]'), null);
});

test("search matches profile names, native cover names and A-PL; matching profiles keep every association", async () => {
  const { host, view, context } = await mount();
  for (const query of ["Alluminio", "Cucina", "A:1 PL:2", "Soggiorno"]) {
    context.filters.query = query; view.show(context);
    assert.equal(host.querySelectorAll(".shared-profile-card").length, 1, query);
    assert.equal(host.querySelector('[data-profile="shared"] .count').textContent, "3 tapparelle");
    assert.equal(host.querySelectorAll(".shared-profile-follower").length, 3);
  }
  context.filters = { query: "Legno", area: "living", category: "" }; view.show(context);
  assert.equal(host.querySelectorAll(".shared-profile-card").length, 0);
  context.filters.query = ""; view.show(context);
  assert.equal(host.querySelectorAll(".shared-profile-card").length, 1);
  context.filters.area = "__none__"; view.show(context);
  assert.equal(host.querySelectorAll(".shared-profile-card").length, 1);
  context.filters.category = "light"; view.show(context);
  assert.equal(host.querySelectorAll(".shared-profile-card").length, 0);
});

test("same profile ID on two gateways keeps independent expansion and association data", async () => {
  const { host, view, calls } = await mount({ gateways: [{ entry_id: "one", title: "Casa" }, { entry_id: "two", title: "Garage" }],
    read: (message) => message.entry_id === "one" ? overview() : { ...overview("two"), profiles: [{ ...overview().profiles[0], assigned_to: [] }], covers: [] } });
  assert.equal(calls.length, 2);
  expand(view);
  assert.equal(host.querySelector('.shared-profile-card[data-entry="one"] .shared-profile-body').hidden, false);
  assert.equal(host.querySelector('.shared-profile-card[data-entry="two"] .shared-profile-body').hidden, true);
  assert.equal(host.querySelector('.shared-profile-card[data-entry="two"] [data-action="cover-profile"]'), null);
  assert.match(host.textContent, /Casa.*Garage/s);
});

test("notifications and refresh preserve expansion and focus while updating backend values", async () => {
  let data = overview();
  const { host, view, subscriptions } = await mount({ read: () => structuredClone(data) });
  expand(view);
  host.querySelector('[data-action="toggle-shared-profile"]').focus();
  data.revision++; data.profiles[0].opening_time = 27;
  subscriptions[0].callback({ entry_id: "one", revision: 2 }); await tick();
  assert.match(host.querySelector('[data-profile="shared"]').textContent, /27 s/);
  assert.equal(host.querySelector('.shared-profile-body').hidden, false);
  assert.equal(document.activeElement.dataset.group, JSON.stringify(["one", "shared"]));
  assert.equal(subscriptions.length, 1);
});

test("an update received during a read is reconciled after it completes", async () => {
  const waiting = deferred(); let reads = 0;
  const { host, calls, subscriptions } = await mount({ read: () => ++reads === 1 ? waiting.promise : { ...overview(), revision: 2, profiles: [] } });
  subscriptions[0].callback({ entry_id: "one", revision: 2 });
  waiting.resolve(overview()); await tick(); await tick();
  assert.equal(calls.length, 2);
  assert.equal(host.querySelectorAll('.shared-profile-card').length, 0);
  assert.match(host.textContent, /Nessun profilo salvato/);
});

test("failed gateway reads do not hide other gateways or display stale data as current", async () => {
  let failed = true;
  const { host, view } = await mount({ gateways: [{ entry_id: "one", title: "Casa" }, { entry_id: "two", title: "Garage" }], read: (message) => {
    if (failed && message.entry_id === "one") throw new Error("offline");
    return overview(message.entry_id);
  } });
  assert.match(host.querySelector('[data-entry="one"]').textContent, /Impossibile aggiornare/);
  assert.ok(host.querySelector('.shared-profile-card[data-entry="two"]'));
  failed = false; view.refresh(); await tick();
  assert.ok(host.querySelector('.shared-profile-card[data-entry="one"]'));
});

test("late reads and subscriptions cannot populate another gateway or a hidden view", async () => {
  const reading = deferred(), subscribing = deferred();
  const { host, view, context, subscriptions } = await mount({ read: () => reading.promise, subscribe: (_sub, stop) => subscribing.promise.then(() => stop) });
  view.clear();
  context.gateways = [{ entry_id: "two", title: "Garage" }];
  context.hass = { ...context.hass, callWS: async () => ({ ...overview("two"), profiles: [], covers: [] }),
    connection: { subscribeMessage: async () => () => {} } };
  view.show(context); await tick();
  reading.resolve(overview()); subscribing.resolve(); await tick();
  assert.equal(host.querySelector('.shared-profile-card'), null);
  assert.equal(subscriptions[0].stopped, 1);
  assert.match(host.textContent, /Nessun profilo salvato/);
});

test("removed gateways and missing origins remain explicit; removed responses cannot reappear", async () => {
  const waiting = deferred();
  const { host, view, subscriptions } = await mount({ read: () => waiting.promise });
  subscriptions[0].callback({ entry_id: "one", kind: "removed" });
  waiting.resolve(overview()); await tick();
  view.refresh(); await tick();
  assert.equal(host.querySelector('.shared-profile-card'), null);
  assert.match(host.textContent, /Gateway/);
});

test("HTML from profiles, names and provenance is escaped; unknown and advanced values are not invented", async () => {
  const data = overview(); data.profiles[0].name = '<img src=x onerror=alert(1)>';
  data.profiles[0].provenance.opening.origin_name = '<script>bad</script>';
  data.profiles[0].provenance.closing.recorded_at = 'not a date';
  data.covers[0].advanced = true; data.covers[1].effective = null;
  const { host, view } = await mount({ read: () => data }); expand(view);
  assert.equal(host.querySelector('img,script'), null);
  assert.match(host.textContent, /<img src=x/);
  assert.match(host.textContent, /Data non disponibile/);
  assert.match(host.textContent, /— s/);
});

test("hidden tabs skip refresh and failed event subscriptions still allow visible refresh", async () => {
  const { calls, view } = await mount({ subscribe: () => { throw new Error("no subscription"); } });
  Object.defineProperty(document, "hidden", { value: true, configurable: true });
  view.refresh(); await tick(); assert.equal(calls.length, 1);
  delete document.hidden;
  view.refresh(); await tick(); assert.equal(calls.length, 2);
});

test("wrong-gateway responses are rejected rather than shown under another gateway", async () => {
  const { host } = await mount({ read: () => overview("two") });
  assert.equal(host.querySelector('.shared-profile-card'), null);
  assert.match(host.textContent, /Impossibile aggiornare/);
});
