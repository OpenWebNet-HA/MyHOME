import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import { after, afterEach, test } from "node:test";
import { JSDOM } from "jsdom";

import { effectiveArea, filterItems, registryChanges, scopedInventory } from "../../custom_components/myhome/frontend/panel/panel-model.js";
import { translations } from "../../custom_components/myhome/frontend/panel/panel-translations.js";

const dom = new JSDOM("<!doctype html><html><body></body></html>", {
  url: "http://localhost/", pretendToBeVisual: true, runScripts: "outside-only",
});
for (const key of ["window", "document", "HTMLElement", "customElements", "CustomEvent", "Event", "history"]) {
  globalThis[key] = key === "window" ? dom.window : dom.window[key];
}
// jsdom has no dialog layout; preserve native open/close behavior for DOM tests.
dom.window.HTMLDialogElement.prototype.showModal = function () { this.open = true; };
dom.window.HTMLDialogElement.prototype.close = function () { this.open = false; };
await import("../../custom_components/myhome/frontend/panel/myhome-panel.js");
dom.window.eval(await readFile(new URL("../../custom_components/myhome/frontend/myhome-bus-card.js", import.meta.url), "utf8"));

const tick = () => new Promise((resolve) => setImmediate(resolve));
const deferred = () => {
  let resolve;
  const promise = new Promise((done) => { resolve = done; });
  return { promise, resolve };
};

function inventory() {
  return {
    version: "2.0.0b9",
    panel_version: "0.7.1",
    gateways: [
      { entry_id: "one", title: "Casa", mac: "00:03:50:00:00:01", model: "F454", host: "192.0.2.1", state: "loaded", connected: true, monitor_available: true },
      { entry_id: "two", title: "Garage", mac: "00:03:50:00:00:02", model: "F453", host: "192.0.2.2", state: "setup_retry", connected: false, monitor_available: false },
    ],
    devices: [
      { id: "device-one", entry_ids: ["one"], name: "Luce sala", name_by_user: null, area_id: "living", who: "1", address: { raw: "11", a: "1", pl: "1", interface: null }, identifiers: ["00:03:50:00:00:01-1-11"] },
      { id: "cen", entry_ids: ["one"], name: "CEN ingresso", area_id: null, who: "25", address: { raw: "21", a: null, pl: null, interface: null }, identifiers: ["00:03:50:00:00:01-25-21"] },
      { id: "device-two", entry_ids: ["two"], name: "Luce garage", area_id: null, who: "1", address: { raw: "11", a: "1", pl: "1", interface: null }, identifiers: ["00:03:50:00:00:02-1-11"] },
    ],
    entities: [
      { entity_id: "light.sala", entry_id: "one", domain: "light", who: "1", address: { raw: "11", a: "1", pl: "1", interface: null }, name: null, original_name: "Luce sala", device_id: "device-one", area_id: null, unique_id: "00:03:50:00:00:01-1-11" },
      { entity_id: "light.garage", entry_id: "two", domain: "light", who: "1", address: { raw: "11", a: "1", pl: "1", interface: null }, original_name: "Luce garage", device_id: "device-two", area_id: null, disabled_by: "user", unique_id: "00:03:50:00:00:02-1-11" },
    ],
    areas: [{ id: "living", name: "Soggiorno" }, { id: "outside", name: "Esterno" }],
  };
}

async function mount(options = {}) {
  const data = inventory();
  options.prepare?.(data);
  const calls = [];
  const subscriptions = [];
  const hass = {
    language: "it",
    states: { "light.sala": { state: "on", attributes: { friendly_name: "Luce sala" } } },
    connection: {
      subscribeEvents: async (callback, type) => {
        const subscription = { callback, type, stopped: false };
        subscriptions.push(subscription);
        return () => { subscription.stopped = true; };
      },
    },
    callWS: async (message) => {
      calls.push(message);
      if (options.callWS) return options.callWS(message, data);
      if (message.type === "myhome/panel/inventory") return structuredClone(data);
      if (message.type === "config/entity_registry/update") {
        Object.assign(data.entities.find((entity) => entity.entity_id === message.entity_id), message);
        return {};
      }
      if (message.type === "config/device_registry/update") {
        Object.assign(data.devices.find((device) => device.id === message.device_id), message);
        return {};
      }
      throw new Error(`Unexpected command: ${message.type}`);
    },
  };
  const panel = document.createElement("myhome-panel");
  panel.hass = hass;
  document.body.append(panel);
  await tick();
  return { panel, root: panel.shadowRoot, data, calls, subscriptions, hass };
}

const change = (element, value) => {
  element.value = value;
  element.dispatchEvent(new Event(element.type === "search" ? "input" : "change", { bubbles: true }));
};
afterEach(() => { document.body.replaceChildren(); delete document.hidden; window.localStorage.clear(); window.history.replaceState(null, "", "/"); });
after(() => dom.window.close());

test("gateway, category and inherited area filters retain trigger-only and disabled items", () => {
  const data = inventory();
  const one = scopedInventory(data, "one");
  assert.deepEqual(one.devices.map((item) => item.id), ["device-one", "cen"]);
  assert.equal(effectiveArea(one.entities[0], data.devices), "living");
  const filters = { query: "soggiorno 1-11", category: "light", area: "living" };
  assert.deepEqual(filterItems(data, one, "entities", filters, {}).map((item) => item.entity_id), ["light.sala"]);
  assert.equal(scopedInventory(data, "two").entities[0].disabled_by, "user");
  assert.deepEqual(registryChanges("entity", one.entities[0], "Lettura", ""), { name: "Lettura" });
  assert.deepEqual(registryChanges("device", { name_by_user: "Old", area_id: "living" }, " ", ""), { name_by_user: null, area_id: null });
  assert.deepEqual(Object.keys(translations.it).sort(), Object.keys(translations.en).sort());
});

test("DOM search and gateway selection expose the expected devices and disabled entities", async () => {
  const { root } = await mount();
  assert.equal(root.querySelector('[data-view="entities"]').getAttribute("aria-pressed"), "true");
  assert.equal(root.getElementById("panel-version").textContent, "Pannello v0.7.1");
  assert.equal(root.getElementById("version").textContent, "Integrazione v2.0.0b9");
  root.querySelector('[data-view="entities"]').click();
  assert.equal(root.querySelectorAll(".device-group").length, 3);
  change(root.getElementById("gateway"), "one");
  assert.equal(root.querySelectorAll(".device-group").length, 2);
  change(root.getElementById("search"), "25-21");
  assert.equal(root.querySelector(".device-name").textContent, "CEN ingresso");
  change(root.getElementById("search"), "");
  change(root.getElementById("gateway"), "two");
  root.querySelector('[data-view="entities"]').click();
  assert.equal(root.querySelector(".state").textContent, "Disabilitato");
});

test("home groups mixed sensor WHOs numerically and filters categories without losing unclassified items", async () => {
  const { root } = await mount({ prepare: (data) => {
    for (const [id, who] of [["power", "18"], ["temperature", "4"], ["diagnostic", null], ["scenario", "0"], ["future", "99"]]) {
      data.entities.push({ entity_id: `sensor.${id}`, entry_id: "one", domain: "sensor", who, original_name: id, unique_id: id });
    }
  } });
  const groups = () => [...root.querySelectorAll(".who-group")].map((group) => group.dataset.who);
  assert.deepEqual(groups(), ["0", "1", "4", "18", "25", "99", "__unknown__"]);
  assert.match(root.querySelector('[data-who="18"] h2').textContent, /WHO 18 · Gestione energia/);
  assert.equal(root.querySelector('[data-who="99"] h2').textContent, "WHO 99");
  assert.equal(root.querySelector('[data-who="__unknown__"] h2').textContent, "Senza categoria WHO");
  assert.equal(root.querySelectorAll(".item-card").length, 7);
  assert.deepEqual([...root.querySelectorAll("#who-buttons button")].map((button) => button.dataset.who), groups());
  root.querySelector('#who-buttons [data-who="__unknown__"]').click();
  assert.deepEqual(groups(), ["__unknown__"]);
  root.querySelector('#who-buttons [data-who="99"]').click();
  assert.deepEqual(groups(), ["99"]);
  root.querySelector('#who-buttons [data-who="18"]').click();
  change(root.getElementById("category"), "sensor");
  assert.deepEqual(groups(), ["18"]);
  assert.equal(root.querySelectorAll(".item-card").length, 1);
  change(root.getElementById("search"), "temperature");
  assert.equal(root.querySelectorAll(".item-card").length, 0);
  change(root.getElementById("search"), "");
  change(root.getElementById("gateway"), "two");
  assert.equal(root.querySelector('#who-buttons [aria-pressed="true"]').dataset.who, "1");
  assert.deepEqual(groups(), ["1"]);
});

test("category buttons and layout toggle preserve selection, focus, filters and browser preference", async () => {
  const { panel, root, hass, data } = await mount();
  const toggle = root.querySelector('[data-action="toggle-category-view"]');
  const groups = () => [...root.querySelectorAll(".who-group")].map((group) => group.dataset.who);
  assert.equal(toggle.textContent, "Mostra solo categoria");
  root.querySelector('[data-view="entities"]').click();
  const cen = root.querySelector('#who-buttons [data-who="25"]');
  cen.focus();
  cen.click();
  assert.deepEqual(groups(), ["25"]);
  assert.equal(root.activeElement.dataset.who, "25");
  assert.equal(toggle.textContent, "Mostra tutto");
  toggle.click();
  assert.deepEqual(groups(), ["1", "25"]);
  assert.equal(root.querySelector('#who-buttons [aria-pressed="true"]'), null);
  toggle.click();
  assert.deepEqual(groups(), ["25"]);

  // Unrelated live updates must not change the user's chosen category.
  data.devices[0].name_by_user = "Nuovo nome";
  await panel._refresh();
  assert.deepEqual(groups(), ["25"]);
  change(root.getElementById("search"), "missing");
  assert.equal(root.querySelectorAll(".item-card").length, 0);
  assert.equal(root.querySelectorAll("#who-buttons button").length, 2);
  toggle.click();
  assert.equal(root.getElementById("search").value, "missing");
  assert.equal(root.querySelectorAll(".item-card").length, 0);
  change(root.getElementById("search"), "");
  assert.deepEqual(groups(), ["1", "25"]);

  root.querySelector('#who-buttons [data-who="25"]').click();
  root.querySelector('[data-view="bus"]').click();
  assert.equal(root.getElementById("who-navigation").hidden, true);
  root.querySelector('[data-view="entities"]').click();
  assert.deepEqual(groups(), ["25"]);
  panel.hass = { ...hass, language: "en" };
  assert.match(root.querySelector('[data-action="toggle-category-view"]').textContent, /Show all/);
  assert.deepEqual(groups(), ["25"]);

  // Reopening the panel restores the preference if the category is available.
  panel.remove();
  const reopened = await mount({ prepare: (inventory) => {
    inventory.entities.push({ entity_id: "sensor.cen", entry_id: "one", domain: "sensor", who: "25", unique_id: "cen" });
  } });
  assert.equal(reopened.root.querySelector('#who-buttons [aria-pressed="true"]').dataset.who, "25");
  assert.equal(reopened.root.querySelectorAll(".item-card").length, 1);
});

test("category navigation recovers from removed categories, empty inventories and unavailable storage", async () => {
  const storage = Object.getOwnPropertyDescriptor(window, "localStorage");
  Object.defineProperty(window, "localStorage", { configurable: true, get() { throw new Error("Storage blocked"); } });
  try {
    const { panel, root, data } = await mount();
    root.querySelector('[data-view="entities"]').click();
    root.querySelector('#who-buttons [data-who="25"]').click();
    data.devices = data.devices.filter((device) => device.id !== "cen");
    await panel._refresh();
    assert.equal(root.querySelector('#who-buttons [aria-pressed="true"]').dataset.who, "1");
    assert.equal(root.querySelectorAll(".item-card").length, 2);
    data.devices = [];
    await panel._refresh();
    // Registry orphans remain available even when no devices are registered.
    assert.equal(root.querySelectorAll(".entity-row").length, 2);
    data.entities = [];
    await panel._refresh();
    assert.equal(root.getElementById("who-navigation").hidden, true);
    assert.equal(root.querySelectorAll(".item-card").length, 0);
    data.devices = inventory().devices;
    data.entities = inventory().entities;
    await panel._refresh();
    assert.equal(root.getElementById("who-navigation").hidden, false);
    root.querySelector('[data-action="toggle-category-view"]').click();
    assert.equal(root.querySelectorAll(".device-group").length, 3);
  } finally {
    Object.defineProperty(window, "localStorage", storage);
  }
});

test("entity and device cards display searchable A/PL, bus routes and unknown addresses safely", async () => {
  const { panel, root, hass } = await mount({ prepare: (data) => {
    const address = { raw: "0015#4#02", a: "00", pl: "15", interface: "02" };
    const shortAddress = { raw: "01", a: "0", pl: "1", interface: null };
    data.devices[2].address = shortAddress;
    data.entities[1].address = shortAddress;
    data.devices.push({ id: "lux-device", entry_ids: ["one"], name: "Lux", who: "1", identifiers: ["legacy-lux-device"], address });
    data.entities.push({ entity_id: "sensor.lux", entry_id: "one", domain: "sensor", who: "1", device_id: "lux-device", unique_id: "legacy-lux", address });
    data.entities.push({ entity_id: "sensor.unknown", entry_id: "one", domain: "sensor", who: null, unique_id: "legacy-unknown", address: null });
    data.entities.push({ entity_id: "sensor.energy", entry_id: "one", domain: "sensor", who: "18", unique_id: "legacy-energy", address: { raw: "52", a: null, pl: null, interface: null } });
  } });
  const card = (id) => root.querySelector(`[data-id="${id}"]`).closest(".item-card, .device-group");
  const fields = (id) => [...(card(id).querySelector(".address") || card(id).closest(".device-group")?.querySelector(".device-group-header .address")).querySelectorAll("div")].map((field) => [field.querySelector("dt").textContent, field.querySelector("dd").textContent]);
  assert.deepEqual(fields("sensor.lux"), [["Indirizzo:", "0015#4#02"], ["A:", "00"], ["PL:", "15"], ["Interfaccia:", "02"]]);
  assert.deepEqual(fields("light.garage"), [["Indirizzo:", "01"], ["A:", "0"], ["PL:", "1"]]);
  assert.match(card("sensor.unknown").querySelector(".address").textContent, /Indirizzo: Non disponibile/);
  assert.deepEqual(fields("sensor.energy"), [["Indirizzo:", "52"]]);
  change(root.getElementById("search"), "A:00 PL:15");
  assert.equal(root.querySelectorAll(".item-card").length, 1);
  assert.ok(card("sensor.lux"));
  root.querySelector('[data-view="entities"]').click();
  assert.equal(root.querySelectorAll(".item-card").length, 1);
  assert.deepEqual(fields("lux-device"), [["Indirizzo:", "0015#4#02"], ["A:", "00"], ["PL:", "15"], ["Interfaccia:", "02"]]);
  change(root.getElementById("search"), "0015#4#02");
  assert.equal(root.querySelectorAll(".item-card").length, 1);
  panel.hass = { ...hass, language: "en" };
  assert.equal(fields("lux-device")[3][0], "Interface:");
});

test("entity groups use device and gateway identity, share metadata and retain filtered orphans", async () => {
  const { panel, root, data } = await mount({ prepare: (data) => {
    data.devices[0].name_by_user = "Attuatore <sala>";
    data.devices[2].name_by_user = "Attuatore <sala>";
    const first = data.entities[0];
    data.entities.push(
      { ...first, entity_id: "sensor.diagnostic", domain: "sensor", name: "Diagnostica", disabled_by: "user", hidden_by: "user", area_id: "outside" },
      { ...first, entity_id: "sensor.other_bus", entry_id: "two", domain: "sensor", address: { raw: "22", a: "2", pl: "2", interface: null } },
      { ...first, entity_id: "sensor.orphan", domain: "sensor", device_id: null },
      { ...first, entity_id: "sensor.missing_device", domain: "sensor", device_id: "deleted" },
    );
  } });
  const group = (device, entry) => root.querySelector(`.device-group[data-device="${device}"][data-entry="${entry}"]`);
  assert.equal(root.querySelectorAll(".device-group").length, 5);
  const living = group("device-one", "one");
  assert.equal(living.querySelector(".device-name").textContent, "Attuatore <sala>");
  assert.equal(living.querySelector("sala"), null);
  assert.equal(living.querySelectorAll(".entity-row").length, 2);
  assert.equal(living.querySelectorAll(".address").length, 1);
  assert.match(living.querySelector(".device-group-header").textContent, /Soggiorno · Casa/);
  const diagnostic = living.querySelector('[data-id="sensor.diagnostic"]').closest(".entity-row");
  assert.match(diagnostic.textContent, /Esterno/);
  assert.match(diagnostic.textContent, /Disabilitato/);
  assert.match(diagnostic.textContent, /Nascosta/);
  assert.doesNotMatch(living.textContent, /00:03:50/);
  assert.match(group("device-one", "two").querySelector(".address").textContent, /22/);
  assert.equal(group("", "one").querySelectorAll(".entity-row").length, 2);
  assert.match(group("", "one").querySelector(".device-name").textContent, /senza dispositivo/);
  data.entities.find((entity) => entity.entity_id === "sensor.diagnostic").address = { raw: "12", a: "1", pl: "2", interface: null };
  await panel._refresh();
  assert.equal(group("device-one", "one").querySelectorAll(".entity-row .address").length, 2);
  assert.equal(group("device-one", "one").querySelector(".device-group-header .address"), null);
  change(root.getElementById("search"), "Attuatore");
  assert.equal(root.querySelectorAll(".device-group").length, 3);
  change(root.getElementById("category"), "sensor");
  assert.equal(root.querySelectorAll(".entity-row").length, 2);
  change(root.getElementById("area"), "outside");
  assert.equal(root.querySelectorAll(".device-group").length, 1);
  assert.equal(root.querySelector(".device-group .count").textContent, "1 Entità");
  assert.ok(root.querySelector('[data-id="sensor.diagnostic"]'));
});

test("device headers collapse independently and preserve their state through refresh and navigation", async () => {
  const { panel, root, hass } = await mount();
  const group = (id) => root.querySelector(`.device-group[data-device="${id}"]`);
  const toggle = (id) => group(id).querySelector('[data-action="toggle-device"]');
  assert.equal(root.querySelector('[data-view="devices"]'), null);
  assert.ok([...root.querySelectorAll(".entity-list")].every((list) => list.hidden));
  toggle("device-one").click();
  assert.equal(toggle("device-one").getAttribute("aria-expanded"), "true");
  assert.equal(group("device-one").querySelector(".entity-list").hidden, false);
  toggle("device-one").click();
  assert.equal(toggle("device-one").getAttribute("aria-expanded"), "false");
  assert.equal(group("device-one").querySelector(".entity-list").hidden, true);
  assert.equal(group("device-two").querySelector(".entity-list").hidden, true);
  assert.ok(group("device-one").querySelector(".device-group-header .address"));
  group("device-one").querySelector('[data-action="edit-device"]').click();
  const form = root.querySelector("dialog form");
  form.elements.name.value = "Attuatore rinominato";
  form.dispatchEvent(new Event("submit", { bubbles: true, cancelable: true }));
  await tick();
  assert.equal(group("device-one").querySelector(".entity-list").hidden, true);
  assert.equal(group("device-one").querySelector(".device-name").textContent, "Attuatore rinominato");
  change(root.getElementById("gateway"), "two");
  change(root.getElementById("gateway"), "");
  change(root.getElementById("search"), "missing");
  change(root.getElementById("search"), "");
  await panel._refresh();
  root.querySelector('[data-view="bus"]').click();
  root.querySelector('[data-view="entities"]').click();
  assert.equal(group("device-one").querySelector(".entity-list").hidden, true);
  panel.hass = { ...hass, states: { "light.sala": { state: "off", attributes: {} } } };
  toggle("device-one").click();
  assert.equal(toggle("device-one").getAttribute("aria-expanded"), "true");
  assert.equal(group("device-one").querySelector(".entity-list").hidden, false);
  assert.equal(group("device-one").querySelector(".state").textContent, "off");
  await panel._refresh();
  assert.equal(group("device-one").querySelector(".entity-list").hidden, false);
  assert.match(group("cen").textContent, /Nessuna entità registrata/);
  assert.ok(group("cen").querySelector('[data-action="edit-device"]'));
  toggle("cen").click();
  assert.equal(group("cen").querySelector(".entity-list").hidden, false);
});

test("entity editor saves through the native API without overwriting an externally changed area", async () => {
  const { root, calls, data } = await mount();
  root.querySelector('[data-view="entities"]').click();
  root.querySelector('[data-action="edit-entity"][data-id="light.sala"]').click();
  const form = root.querySelector("dialog form");
  form.elements.name.value = "Lettura";
  data.entities[0].area_id = "outside";
  form.dispatchEvent(new Event("submit", { bubbles: true, cancelable: true }));
  await tick();
  assert.deepEqual(calls.find((call) => call.type === "config/entity_registry/update"), {
    type: "config/entity_registry/update", entity_id: "light.sala", name: "Lettura",
  });
  assert.equal(data.entities[0].area_id, "outside");
  assert.equal(root.querySelector("dialog").open, false);
  assert.equal(root.getElementById("toast").textContent, "Salvato");
});

test("failed saves keep the dialog usable and server text is escaped", async () => {
  const { root } = await mount({ callWS: async (message, data) => {
    if (message.type === "myhome/panel/inventory") {
      data.devices[0].name = '<img src=x onerror="alert(1)">';
      data.devices[0].address.raw = '<img src=x onerror="alert(2)">';
      return structuredClone(data);
    }
    throw new Error("Entity removed");
  } });
  root.querySelector('[data-view="entities"]').click();
  assert.equal(root.querySelector("img"), null);
  root.querySelector('[data-action="edit-device"][data-id="device-one"]').click();
  const form = root.querySelector("form");
  form.elements.name.value = "New name";
  form.dispatchEvent(new Event("submit", { bubbles: true, cancelable: true }));
  await tick();
  assert.equal(root.querySelector("dialog").open, true);
  assert.equal(form.querySelector('[type="submit"]').disabled, false);
  assert.match(root.getElementById("save-error").textContent, /Entity removed/);
  root.getElementById("cancel").click();
  assert.equal(root.querySelector("dialog").open, false);
});

test("native more-info settings and live states preserve an open editor", async () => {
  const { panel, root, hass } = await mount();
  root.querySelector('[data-view="entities"]').click();
  root.querySelector('[data-action="edit-entity"][data-id="light.sala"]').click();
  const form = root.querySelector("form");
  form.elements.name.value = "Unsaved";
  panel.hass = { ...hass, states: { "light.sala": { state: "off", attributes: {} } } };
  assert.equal(root.querySelector("form"), form);
  assert.equal(form.elements.name.value, "Unsaved");
  assert.equal(root.querySelector('[data-state="light.sala"]').textContent, "off");
  let detail;
  panel.addEventListener("hass-more-info", (event) => { detail = event.detail; });
  root.querySelector('[data-action="entity-settings"]').click();
  assert.equal(detail.entityId, "light.sala");
  assert.equal(detail.view, "settings");
});

test("disconnect discards pending inventory and cleans late registry subscriptions", async () => {
  const pending = deferred();
  const { panel, root, subscriptions } = await mount({ callWS: () => pending.promise });
  panel.remove();
  pending.resolve(inventory());
  await tick();
  assert.equal(root.querySelectorAll(".item-card").length, 0);
  assert.ok(subscriptions.every((subscription) => subscription.stopped));
});

test("bus selection never opens the monitor for an unloaded or ambiguous gateway", async () => {
  const { root } = await mount();
  root.querySelector('[data-view="bus"]').click();
  assert.match(root.getElementById("monitor").textContent, /Seleziona un gateway/);
  change(root.getElementById("gateway"), "two");
  assert.match(root.getElementById("monitor").textContent, /non è caricato/);
  assert.equal(root.querySelector("myhome-openwebnet-bus-monitor"), null);
});

test("bus card unsubscribes a late stream after removal and can reconnect", async () => {
  const card = document.createElement("myhome-openwebnet-bus-monitor");
  const pending = deferred();
  let unsubscribed = 0;
  let requests = 0;
  const hass = {
    connection: { subscribeMessage: async () => { requests++; return pending.promise; } },
    callWS: async () => ({ frames: [] }),
  };
  card.setConfig({ mac: "00:03:50:00:00:01" });
  card.hass = hass;
  assert.equal(requests, 0); // Do not subscribe until attached.
  document.body.append(card);
  assert.equal(requests, 1);
  card.remove();
  pending.resolve(() => { unsubscribed++; });
  await tick();
  assert.equal(unsubscribed, 1);
  document.body.append(card);
  await tick();
  assert.equal(requests, 2);
  card.remove();
  assert.equal(unsubscribed, 2);
});

test("bus sweep follows the selected gateway and preserves unscoped Lovelace usage", async () => {
  const card = document.createElement("myhome-openwebnet-bus-monitor");
  const calls = [];
  card.hass = { callService: async (...args) => { calls.push(structuredClone(args)); } };
  for (const mac of [" 00:03:50:00:00:01 ", "00:03:50:00:00:02", null]) {
    card.setConfig({ mac });
    assert.ok(card.shadowRoot.getElementById("btn-export"));
    card.shadowRoot.getElementById("btn-sweep").click();
    await tick();
  }
  assert.deepEqual(calls, [
    ["myhome", "sweep_bus", { gateway: "00:03:50:00:00:01" }],
    ["myhome", "sweep_bus", { gateway: "00:03:50:00:00:02" }],
    ["myhome", "sweep_bus", {}],
  ]);
});

test("gateway deep links work on first load, route changes and browser navigation", async () => {
  window.history.replaceState(null, "", "/myhome?entry_id=two");
  const { panel, root } = await mount();
  assert.equal(root.getElementById("gateway").value, "two");
  assert.equal(root.querySelectorAll(".device-group").length, 1);
  await panel._refresh();
  assert.equal(root.getElementById("gateway").value, "two");
  window.history.pushState(null, "", "/myhome?entry_id=one");
  window.dispatchEvent(new Event("location-changed"));
  assert.equal(root.getElementById("gateway").value, "one");
  assert.equal(root.querySelectorAll(".device-group").length, 2);
  window.history.replaceState(null, "", "/myhome?entry_id=two");
  window.dispatchEvent(new Event("popstate"));
  assert.equal(root.getElementById("gateway").value, "two");
  change(root.getElementById("gateway"), "");
  assert.equal(new URL(window.location.href).searchParams.get("entry_id"), "");
  await panel._refresh();
  assert.equal(root.querySelectorAll(".device-group").length, 3);
  window.history.replaceState(null, "", "/myhome?entry_id=one");
  panel.route = { path: "" };
  assert.equal(root.getElementById("gateway").value, "one");
  panel.remove();
  window.history.replaceState(null, "", "/myhome?entry_id=two");
  window.dispatchEvent(new Event("location-changed"));
  assert.equal(panel._entryId, "one");
  document.body.append(panel);
  await tick();
  assert.equal(root.getElementById("gateway").value, "two");
});

test("unknown gateway links never fall back to another installation", async () => {
  window.history.replaceState(null, "", "/myhome?entry_id=removed");
  const { panel, root } = await mount();
  assert.equal(root.getElementById("gateway").value, "removed");
  assert.equal(root.querySelectorAll(".device-group").length, 0);
  assert.ok(root.textContent.includes(translations.it.gatewayNotFound));
  await panel._refresh();
  assert.equal(panel._entryId, "removed");
  change(root.getElementById("gateway"), "one");
  assert.equal(root.querySelectorAll(".device-group").length, 2);
});

test("inventory updates retain keyboard focus on the same WHO, gateway and action", async () => {
  const { panel, root, data } = await mount({ prepare: (data) => {
    data.devices[0].entry_ids.push("two");
    data.entities.push({ ...data.entities[0], entity_id: "sensor.other_who", domain: "sensor", who: "18" });
    data.entities.push({ ...data.entities[0], entity_id: "light.other_gateway", entry_id: "two" });
  } });
  const select = '[data-who="18"] [data-entry="one"] [data-action="edit-device"]';
  root.querySelector(select).focus();
  data.devices[0].name_by_user = "Updated name";
  await panel._refresh();
  assert.equal(root.activeElement, root.querySelector(select));
  const toggle = root.querySelector('[data-who="1"] [data-entry="one"] [data-action="toggle-device"]');
  toggle.click();
  const entityAction = '[data-action="details"][data-id="light.sala"]';
  root.querySelector(entityAction).focus();
  data.entities[0].name = "Updated entity";
  await panel._refresh();
  assert.equal(root.activeElement, root.querySelector(entityAction));
  assert.equal(root.activeElement.closest(".entity-list").hidden, false);
  root.querySelector('[data-action="edit-entity"][data-id="light.sala"]').click();
  const input = root.querySelector('dialog input');
  input.value = "Unsaved draft";
  input.focus();
  data.devices[0].name_by_user = "Changed elsewhere";
  await panel._refresh();
  assert.equal(root.activeElement, input);
  assert.equal(input.value, "Unsaved draft");
  root.getElementById("cancel").click();
  root.querySelector(entityAction).focus();
  data.entities = data.entities.filter((entity) => entity.entity_id !== "light.sala");
  await panel._refresh();
  assert.equal(root.activeElement, null, "a removed action must not move focus to another entity");
});

test("hidden tabs suspend polling and debounced refreshes, then reconcile once visible", async (context) => {
  context.mock.timers.enable({ apis: ["setInterval", "setTimeout"] });
  let hidden = false;
  Object.defineProperty(document, "hidden", { configurable: true, get: () => hidden });
  const { panel, calls, subscriptions, root, data } = await mount();
  assert.equal(calls.length, 1);
  subscriptions[0].callback();
  hidden = true;
  document.dispatchEvent(new Event("visibilitychange"));
  data.devices[0].name_by_user = "Changed while hidden";
  subscriptions[1].callback();
  context.mock.timers.tick(60000);
  await panel._refresh();
  assert.equal(calls.length, 1);
  hidden = false;
  document.dispatchEvent(new Event("visibilitychange"));
  await tick();
  assert.equal(calls.length, 2);
  assert.match(root.getElementById("items").textContent, /Changed while hidden/);
  context.mock.timers.tick(15000);
  await tick();
  assert.equal(calls.length, 3);
  panel.remove();
  document.dispatchEvent(new Event("visibilitychange"));
  subscriptions[0].callback(); // A queued callback from the old connection.
  context.mock.timers.tick(60000);
  assert.equal(calls.length, 3);
  assert.ok(subscriptions.every((subscription) => subscription.stopped));
});

test("a panel mounted hidden starts once on visibility and ignores an old connection response", async () => {
  let hidden = true;
  Object.defineProperty(document, "hidden", { configurable: true, get: () => hidden });
  const pending = deferred();
  const { panel, root, hass, calls, subscriptions } = await mount({ callWS: () => pending.promise });
  panel.connectedCallback();
  assert.equal(calls.length, 0);
  assert.equal(subscriptions.length, 3);
  hidden = false;
  document.dispatchEvent(new Event("visibilitychange"));
  assert.equal(calls.length, 1);
  const fresh = inventory();
  fresh.devices[0].name_by_user = "New connection";
  let reads = 0;
  panel.hass = {
    ...hass,
    connection: { subscribeEvents: async () => () => {} },
    callWS: async () => { reads++; return fresh; },
  };
  await tick();
  assert.equal(reads, 1);
  pending.resolve(inventory());
  await tick();
  assert.match(root.getElementById("items").textContent, /New connection/);
  assert.ok(subscriptions.every((subscription) => subscription.stopped));
});

test("bus section drops a late import and isolates gateway subscriptions after navigation", async () => {
  const pending = deferred();
  globalThis.__panelBusImport = pending.promise;
  const resourceUrl = 'data:text/javascript,await globalThis.__panelBusImport;';
  const streams = [];
  const { panel, root, hass } = await mount({ prepare: (data) => {
    data.gateways[1].monitor_available = true;
    data.gateways[1].state = "loaded";
  }, callWS: async (message, data) => message.type === "myhome/panel/inventory" ? structuredClone(data) : { frames: [] } });
  hass.connection.subscribeMessage = async (_callback, message) => {
    const stream = { mac: message.mac, stopped: false };
    streams.push(stream);
    return () => { stream.stopped = true; };
  };
  panel.panel = { config: { bus_card_url: resourceUrl } };
  change(root.getElementById("gateway"), "one");
  root.querySelector('[data-view="bus"]').click();
  change(root.getElementById("gateway"), "two");
  pending.resolve();
  await import(resourceUrl);
  await tick();
  delete globalThis.__panelBusImport;
  assert.deepEqual(streams.map((stream) => stream.mac), ["00:03:50:00:00:02"]);
  change(root.getElementById("gateway"), "one");
  await tick();
  assert.equal(streams[0].stopped, true);
  assert.equal(streams[1].mac, "00:03:50:00:00:01");
  root.querySelector('[data-view="entities"]').click();
  assert.equal(streams[1].stopped, true);
  assert.equal(root.querySelector("myhome-openwebnet-bus-monitor"), null);
});
