import assert from "node:assert/strict";
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
const { BusMonitorView } = await import("../../custom_components/myhome/frontend/panel/panel-bus-monitor-view.js");
customElements.define("myhome-panel-bus-monitor", class extends BusMonitorView {});

const tick = () => new Promise((resolve) => setImmediate(resolve));
const deferred = () => {
  let resolve;
  const promise = new Promise((done) => { resolve = done; });
  return { promise, resolve };
};

function inventory() {
  return {
    version: "2.0.0b9",
    panel_version: "0.20.0",
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
      subscribeMessage: async () => () => {},
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

test("secondary entities follow the main entity and buttons never render or format timestamps", async () => {
  const { panel, root, hass } = await mount({ prepare: (data) => {
    const first = data.entities[0];
    data.entities.push(
      { ...first, entity_id: "button.lock", domain: "button", name: "Blocca <sala>", entity_category: "config" },
      { ...first, entity_id: "button.unlock", domain: "button", name: "Sblocca", entity_category: "config" },
      { ...first, entity_id: "sensor.signal", domain: "sensor", name: "Segnale", entity_category: "diagnostic" },
    );
  } });
  const group = root.querySelector('.device-group[data-device="device-one"]');
  group.querySelector('[data-action="toggle-device"]').click();
  assert.equal(group.querySelector('.entity-list > .entity-row [data-action="details"]').dataset.id, "light.sala");
  assert.equal(group.querySelectorAll(".secondary-entities .entity-row").length, 3);
  const name = group.querySelector('[data-action="details"][data-id="button.lock"]');
  assert.equal(name.textContent, "Blocca <sala>"); assert.equal(name.querySelector("sala"), null);
  assert.equal(group.querySelectorAll('.is-button [data-state]').length, 0);
  let details;
  panel.addEventListener("hass-more-info", (event) => { details = event.detail; });
  name.click(); assert.equal(details.entityId, "button.lock");
  name.focus();
  const formatted = [];
  panel.hass = { ...hass, states: { ...hass.states,
    "button.lock": { state: "2026-09-15T12:34:56Z", attributes: {} },
    "button.unlock": { state: "unknown", attributes: {} },
    "sensor.signal": { state: "72", attributes: { unit_of_measurement: "%" } },
  }, formatEntityState: (state) => { formatted.push(state.state); return state.state; } };
  assert.equal(root.activeElement, name);
  assert.equal(group.querySelector('[data-state="sensor.signal"]').textContent, "72");
  assert.equal(group.querySelector('[data-state="light.sala"]').textContent, "on");
  assert.equal(formatted.includes("2026-09-15T12:34:56Z"), false);
  assert.equal(formatted.includes("unknown"), false);
  assert.doesNotMatch(group.textContent, /2026-09-15/);
  group.querySelector('[data-action="edit-entity"][data-id="button.lock"]').click();
  assert.equal(root.querySelector("dialog").open, true);
  assert.equal(root.querySelector('input[name="name"]').value, "Blocca <sala>");
});

test("secondary-only searches retain disabled flags, area overrides and gateway separation", async () => {
  const { panel, root, hass } = await mount({ prepare: (data) => {
    const first = data.entities[0];
    data.entities.push(
      { ...first, entity_id: "button.one", domain: "button", name: "Comando", area_id: "outside", disabled_by: "user", hidden_by: "user" },
      { ...first, entity_id: "button.two", entry_id: "two", domain: "button", name: "Comando" },
      { ...first, entity_id: "button.orphan", device_id: null, domain: "button", name: "Comando" },
      { ...first, entity_id: "switch.setting", domain: "switch", name: "Impostazione", entity_category: "config" },
    );
  } });
  change(root.getElementById("category"), "button");
  assert.equal(root.querySelectorAll(".entity-secondary").length, 3);
  assert.equal(root.querySelectorAll(".entity-list > .entity-row").length, 0);
  const other = root.querySelector('[data-id="button.two"]').closest(".device-group");
  assert.equal(other.dataset.entry, "two");
  change(root.getElementById("gateway"), "one");
  change(root.getElementById("search"), "button.one");
  assert.equal(root.querySelectorAll(".entity-row").length, 1);
  const row = root.querySelector(".entity-secondary");
  for (const text of ["Disabilitato", "Nascosta", "Esterno"]) assert.ok(row.textContent.includes(text));
  panel.hass = { ...hass, language: "en" };
  assert.equal(root.querySelector(".secondary-entities h3").textContent, "Additional entities");
  assert.equal(root.querySelectorAll('[data-state^="button."]').length, 0);
  change(root.getElementById("search"), ""); change(root.getElementById("category"), "switch");
  assert.ok(root.querySelector('.entity-secondary [data-state="switch.setting"]'));
});

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
  assert.equal(root.getElementById("panel-version").textContent, "Pannello v0.20.0");
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
  assert.equal(group("device-one").querySelector('.device-states [data-state="light.sala"]').textContent, "on");
  assert.equal(group("device-two").querySelector('.device-states [data-state="light.garage"]').textContent, "Disabilitato");
  assert.equal(group("cen").querySelector(".device-states"), null);
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
  const header = toggle("device-one"); header.focus();
  panel.hass = { ...hass, states: { "light.sala": { state: "off", attributes: {} } } };
  assert.equal(group("device-one").querySelector('.device-states [data-state="light.sala"]').textContent, "off");
  assert.equal(group("device-one").querySelector(".entity-list").hidden, true);
  assert.equal(root.activeElement, header);
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

test("header states label multiple primary entities and respect secondary categories and gateway filters", async () => {
  const { panel, root, hass } = await mount({ prepare: (data) => {
    const first = data.entities[0];
    data.devices[0].entry_ids.push("two");
    data.entities.push(
      { ...first, entity_id: "sensor.level", domain: "sensor", name: "Livello <sala>" },
      { ...first, entity_id: "sensor.diagnostic", domain: "sensor", entity_category: "diagnostic" },
      { ...first, entity_id: "button.lock", domain: "button" },
      { ...first, entity_id: "switch.config", domain: "switch", entity_category: "config" },
      { ...first, entity_id: "light.other", entry_id: "two", name: "Altro gateway" },
      { ...first, entity_id: "sensor.orphan", device_id: null, domain: "sensor" },
    );
  } });
  const group = () => root.querySelector('.device-group[data-device="device-one"][data-entry="one"]');
  assert.deepEqual([...group().querySelectorAll('.device-states [data-state]')].map(el => el.dataset.state), ["sensor.level", "light.sala"]);
  assert.deepEqual([...group().querySelectorAll('.device-state-name')].map(el => el.textContent), ["Livello <sala>:", "Luce sala:"]);
  assert.equal(group().querySelector('.device-state-name sala'), null);
  assert.equal(root.querySelector('.device-group[data-device=""] .device-states'), null);
  panel.hass = { ...hass, states: { ...hass.states, "sensor.level": { state: "42", attributes: { unit_of_measurement: "%" } } } };
  assert.equal(group().querySelector('.device-states [data-state="sensor.level"]').textContent, "42 %");
  panel.hass = { ...hass, states: {}, formatEntityState: (state) => `HA: ${state.state}` };
  assert.equal(group().querySelector('.device-states [data-state="light.sala"]').textContent, "Non disponibile");
  panel.hass = { ...panel.hass, states: { "light.sala": { state: "<off>", attributes: {} } } };
  assert.equal(group().querySelector('.device-states [data-state="light.sala"]').textContent, "HA: <off>");
  assert.equal(group().querySelector('.device-states off'), null);
  change(root.getElementById("gateway"), "two");
  assert.equal(root.querySelector('.device-states [data-state="light.sala"]'), null);
  assert.ok(root.querySelector('.device-states [data-state="light.other"]'));
  change(root.getElementById("gateway"), "one");
  change(root.getElementById("category"), "button");
  assert.equal(root.querySelector('.device-states'), null);
  assert.ok(root.querySelector('[data-id="button.lock"]'));
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
  assert.equal(root.querySelector("myhome-panel-bus-monitor"), null);
});

test("bus view unsubscribes a late stream after removal and can reconnect", async () => {
  const card = document.createElement("myhome-panel-bus-monitor");
  const pending = deferred();
  let unsubscribed = 0;
  let requests = 0;
  const hass = {
    connection: { subscribeMessage: async () => { requests++; return pending.promise; } },
    callWS: async () => ({ frames: [] }),
  };
  card.configure({ mac: "00:03:50:00:00:01" });
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
  const card = document.createElement("myhome-panel-bus-monitor");
  const calls = [];
  card.hass = { callService: async (...args) => { calls.push(structuredClone(args)); } };
  for (const mac of [" 00:03:50:00:00:01 ", "00:03:50:00:00:02", null]) {
    card.configure({ mac });
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

test("native bus section ignores the legacy resource and isolates gateway subscriptions after navigation", async () => {
  const resourceUrl = "https://invalid.example/removed-card.js";
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
  await tick();
  assert.deepEqual(streams.map((stream) => stream.mac), ["00:03:50:00:00:02"]);
  change(root.getElementById("gateway"), "one");
  await tick();
  assert.equal(streams[0].stopped, true);
  assert.equal(streams[1].mac, "00:03:50:00:00:01");
  root.querySelector('[data-view="entities"]').click();
  assert.equal(streams[1].stopped, true);
  assert.equal(root.querySelector("myhome-panel-bus-monitor"), null);
});

function coverProfileData(extra = {}) {
  return {
    entry_id: "one", entity_id: "cover.shutter", revision: 3,
    assigned_profile_id: "timed", profiles: [{ id: "timed", name: "Standard", travel_time: 35, uses: 1 }],
    writable: true, reason: null, default_travel_time: 30, effective_travel_time: 35, pending: false,
    ...extra,
  };
}

async function mountProfiles({ read, write, exportProfiles, targets } = {}) {
  return mount({ prepare: (data) => {
    data.devices.push({ id: "shutter", entry_ids: ["one"], name: "Tapparella", who: "2" });
    data.entities.push({ entity_id: "cover.shutter", device_id: "shutter", entry_id: "one",
      domain: "cover", who: "2", original_name: "Tapparella", unique_id: "00:03:50:00:00:01-2-11" });
  }, callWS: async (message, data) => {
    if (message.type === "myhome/panel/inventory") return structuredClone(data);
    if (message.type === "myhome/cover_profiles/read") return read ? read(message) : coverProfileData();
    if (message.type === "myhome/cover_profiles/write") return write ? write(message) : coverProfileData({ revision: 4 });
    if (message.type === "myhome/cover_calibration/targets") return targets(message);
    if (message.type === "myhome/cover_profiles/export") return exportProfiles(message);
    throw new Error(`Unexpected command: ${message.type}`);
  } });
}

const openProfile = (root) => {
  root.querySelector('.device-group[data-device="shutter"] [data-action="toggle-device"]').click();
  root.querySelector('[data-action="cover-profile"]').click();
};

function captureDownloads(t) {
  const blobs = [], downloads = [], revoked = [];
  t.mock.method(URL, "createObjectURL", (blob) => { blobs.push(blob); return "blob:test-export"; });
  t.mock.method(URL, "revokeObjectURL", (url) => revoked.push(url));
  t.mock.method(dom.window.HTMLAnchorElement.prototype, "click", function () {
    downloads.push({ name: this.download, href: this.href, connected: this.isConnected });
  });
  return { blobs, downloads, revoked };
}

test("calibration download uses a fresh committed gateway export and preserves the editor draft", async (t) => {
  const { blobs, downloads, revoked } = captureDownloads(t);
  const exported = { format: "myhome.cover_calibration", format_version: 1, revision: 9,
    profiles: [{ name: "Saved", opening_time: 25, closing_time: 31 }], assignments: [], covers: [] };
  const { root, calls } = await mountProfiles({ exportProfiles: () => exported });
  openProfile(root); await tick();
  const form = root.querySelector("#profile-form");
  form.elements.profile_name.value = "Unsaved draft";
  form.elements.opening_time.value = "99";
  root.querySelector("#profile-export").click();
  await tick();
  assert.deepEqual(calls.find((m) => m.type.endsWith("/export")), {
    type: "myhome/cover_profiles/export", entry_id: "one",
  });
  assert.equal(blobs[0].type, "application/json");
  assert.deepEqual(JSON.parse(await blobs[0].text()), exported);
  assert.deepEqual(downloads, [{ name: "myhome-calibration-one-r9.json", href: "blob:test-export", connected: true }]);
  assert.equal(document.querySelector('a[download]'), null);
  assert.equal(form.elements.profile_name.value, "Unsaved draft");
  assert.equal(form.elements.opening_time.value, "99");
  assert.equal(calls.filter((m) => m.type.endsWith("/write")).length, 0);
  assert.match(root.querySelector("#profile-export-status").textContent, /Download avviato/);
  await new Promise((resolve) => setTimeout(resolve, 1100));
  assert.deepEqual(revoked, ["blob:test-export"]);
});

test("calibration export prevents duplicate downloads and discards results after gateway navigation", async (t) => {
  const { downloads } = captureDownloads(t);
  const pending = deferred();
  const { root, calls } = await mountProfiles({ exportProfiles: () => pending.promise });
  openProfile(root); await tick();
  const button = root.querySelector("#profile-export");
  button.click(); button.click();
  assert.equal(button.disabled, true);
  assert.equal(calls.filter((m) => m.type.endsWith("/export")).length, 1);
  change(root.getElementById("gateway"), "two");
  pending.resolve({ revision: 3 });
  await tick();
  assert.equal(downloads.length, 0);
  assert.equal(root.querySelector("#profile-export"), null);
});

test("read-only gateways can export empty saved data and failed requests allow retry", async (t) => {
  const { downloads } = captureDownloads(t);
  let attempts = 0;
  const { root } = await mountProfiles({ read: () => coverProfileData({ writable: false, reason: "cover_unavailable" }),
    exportProfiles: () => { if (!attempts++) throw new Error("offline"); return { revision: 0, profiles: [], covers: [], assignments: [] }; } });
  openProfile(root); await tick();
  const button = root.querySelector("#profile-export");
  assert.equal(button.disabled, false);
  button.click(); await tick();
  assert.match(root.querySelector("#profile-export-status").textContent, /non riuscita/);
  assert.equal(button.disabled, false);
  assert.equal(downloads.length, 0);
  button.click(); await tick();
  assert.equal(downloads.length, 1);
  await new Promise((resolve) => setTimeout(resolve, 1100));
});

test("WHO 2 editor saves a single cover profile with explicit gateway and revision, including decimals", async () => {
  const { root, calls } = await mountProfiles({ write: () => coverProfileData({ revision: 4, pending: true }) });
  openProfile(root);
  await tick();
  const form = root.querySelector("#profile-form");
  assert.equal(form.elements.opening_time.value, "35");
  form.elements.profile_name.value = "Preciso";
  form.elements.opening_time.value = "42.5";
  form.querySelector('[data-profile-action="update"]').click();
  await tick();
  assert.deepEqual(calls.find((call) => call.type === "myhome/cover_profiles/write"), {
    type: "myhome/cover_profiles/write", entry_id: "one", entity_id: "cover.shutter",
    revision: 3, action: "save", profile_id: "timed", profile: { name: "Preciso", opening_time: 42.5, closing_time: 35 },
  });
  assert.match(root.querySelector(".cover-profile-dialog").textContent, /quando la tapparella si ferma/);
  assert.equal(root.querySelector("dialog").open, true);
  root.querySelector("#profile-close").click();
  assert.equal(root.querySelector("dialog"), null);
});

test("shared profiles require an explicit new copy and the default choice removes the assignment", async () => {
  const { root, calls } = await mountProfiles({ read: () => coverProfileData({ profiles: [
    { id: "timed", name: '<img src=x onerror="alert(1)">', travel_time: 35, uses: 2 },
  ] }) });
  openProfile(root);
  await tick();
  let form = root.querySelector("#profile-form");
  assert.equal(root.querySelector("img"), null);
  assert.equal(form.querySelector('[data-profile-action="update"]').hidden, true);
  form.elements.profile_name.value = "Copia";
  form.querySelector('[data-profile-action="new"]').click();
  await tick();
  const create = calls.find((call) => call.type === "myhome/cover_profiles/write");
  assert.equal(create.profile_id, null);
  assert.equal(create.profile.name, "Copia");
  assert.equal(create.copy_from_profile_id, "timed");
  assert.equal("provenance" in create.profile, false);
  form = root.querySelector("#profile-form");
  change(form.elements.profile, "");
  form.querySelector('[data-profile-action="assign"]').click();
  await tick();
  const reset = calls.filter((call) => call.type === "myhome/cover_profiles/write").at(-1);
  assert.equal(reset.profile_id, null);
  assert.equal(reset.action, "assign");
  assert.equal(reset.revision, 4);
  assert.equal("profile" in reset, false);
});

test("profile provenance distinguishes inherited measurements and manual values without relabeling drafts", async () => {
  const date = "2026-09-15T10:00:00+00:00";
  const { root, hass } = await mountProfiles({ read: () => coverProfileData({ profiles: [
    { id: "timed", name: "Kitchen copy", opening_time: 20, closing_time: 40, uses: 1,
      provenance: {
        opening: { source: "guided", recorded_at: date, inherited: true,
          origin_entity_id: "cover.kitchen", origin_name: "<img src=x onerror=alert(1)>" },
        closing: { source: "manual", recorded_at: date, inherited: false,
          origin_entity_id: "cover.shutter", origin_name: "Bedroom" },
      } },
    { id: "old", name: "Legacy", opening_time: 21, closing_time: 41, uses: 0 },
  ] }) });
  openProfile(root);
  await tick();
  const opening = root.querySelector('[data-origin-direction="opening"]');
  const closing = root.querySelector('[data-origin-direction="closing"]');
  assert.match(opening.textContent, /Misurata con il wizard guidato/);
  assert.match(opening.textContent, /Ereditata dal profilo: Kitchen copy/);
  assert.match(opening.textContent, /<img src=x/);
  assert.equal(root.querySelector("img"), null);
  const formattedDate = new Intl.DateTimeFormat(hass.language, { dateStyle: "medium", timeStyle: "short" }).format(new Date(date));
  assert.ok(opening.textContent.includes(formattedDate));
  assert.match(closing.textContent, /Inserita manualmente/);
  assert.doesNotMatch(closing.textContent, /Ereditata/);
  const form = root.querySelector("#profile-form");
  const savedEvidence = opening.textContent;
  change(form.elements.opening_time, "55");
  assert.equal(opening.textContent, savedEvidence);
  change(form.elements.profile, "old");
  const unknown = root.querySelector("#profile-provenance").textContent;
  assert.match(unknown, /Origine non disponibile/);
  assert.doesNotMatch(unknown, /2026|Misurata il|Modificata il/);
  change(form.elements.profile, "");
  assert.match(root.querySelector("#profile-provenance").textContent, /Configurazione YAML \/ predefinita/);
});

test("profile conflicts retain the draft and require reload before another write", async () => {
  let revision = 3;
  const { root, calls } = await mountProfiles({ read: () => coverProfileData({ revision }), write: () => {
    revision = 4;
    throw { code: "revision_conflict" };
  } });
  openProfile(root);
  await tick();
  const form = root.querySelector("#profile-form");
  form.elements.profile_name.value = "Unsaved";
  form.elements.opening_time.value = "52";
  form.querySelector('[data-profile-action="update"]').click();
  await tick();
  assert.equal(form.elements.profile_name.value, "Unsaved");
  assert.match(form.querySelector("#profile-error").textContent, /bozza è conservata/);
  assert.equal(form.querySelector('[data-profile-action="update"]').disabled, true);
  form.querySelector('[data-profile-action="update"]').click();
  assert.equal(calls.filter((call) => call.type.endsWith("/write")).length, 1);
  form.querySelector("#profile-reload").click();
  await tick();
  assert.equal(root.querySelector("#profile-form").elements.profile_name.value, "Standard");
  assert.equal(root.querySelector('[data-profile-action="update"]').disabled, false);
});

test("late profile reads and saves cannot overwrite another gateway or survive disconnect", async () => {
  const pendingRead = deferred();
  const pendingSave = deferred();
  let reads = 0;
  const { panel, root } = await mountProfiles({ read: () => ++reads === 1 ? pendingRead.promise : coverProfileData(),
    write: () => pendingSave.promise });
  openProfile(root);
  change(root.getElementById("gateway"), "two");
  pendingRead.resolve(coverProfileData());
  await tick();
  assert.equal(root.querySelector("dialog"), null);
  change(root.getElementById("gateway"), "one");
  root.querySelector('[data-action="cover-profile"]').click();
  await tick();
  root.querySelector('[data-profile-action="update"]').click();
  panel.remove();
  pendingSave.resolve(coverProfileData({ revision: 99 }));
  await tick();
  assert.equal(root.querySelector("dialog"), null);
  assert.equal(root.querySelector("#toast").textContent, "");
});

test("unavailable and advanced covers show a read-only explanation", async () => {
  for (const reason of ["cover_unavailable", "advanced_cover"]) {
    const { panel, root, calls } = await mountProfiles({ read: () => coverProfileData({ writable: false, reason }) });
    openProfile(root);
    await tick();
    assert.equal(root.querySelector('[data-profile-action="assign"]').disabled, true);
    assert.equal(root.querySelector('[data-profile-action="new"]').disabled, true);
    assert.equal(root.querySelector('[data-profile-action="update"]').hidden, true);
    assert.match(root.querySelector(".cover-profile-dialog .notice").textContent, reason === "advanced_cover" ? /feedback/ : /gateway/);
    assert.equal(calls.filter((call) => call.type.endsWith("/write")).length, 0);
    panel.remove();
  }
});

test("live profile application updates pending status without replacing an editor draft", async () => {
  const { panel, root, hass } = await mountProfiles({ read: () => coverProfileData({ pending: true }) });
  openProfile(root);
  await tick();
  const input = root.querySelector('#profile-form input[name="profile_name"]');
  input.value = "My next edit";
  input.focus();
  panel.hass = { ...hass, states: { ...hass.states, "cover.shutter": { state: "open",
    attributes: { travel_time: 42.5, cover_profile: "Standard", cover_profile_pending: false } } } };
  assert.equal(root.querySelector("#profile-effective-opening").textContent, "42.5");
  assert.equal(root.querySelector("#profile-pending").hidden, true);
  assert.equal(input.value, "My next edit");
  assert.equal(root.activeElement, input);
});

test("directional profile fields keep distinct saved values and live timings", async () => {
  const { panel, root, calls, hass } = await mountProfiles({ read: () => coverProfileData({
    profiles: [{ id: "timed", name: "Different", opening_time: 20, closing_time: 40, uses: 1 }],
    effective_opening_time: 20, effective_closing_time: 40,
  }) });
  openProfile(root);
  await tick();
  const form = root.querySelector("#profile-form");
  assert.equal(form.elements.opening_time.value, "20");
  assert.equal(form.elements.closing_time.value, "40");
  form.elements.closing_time.value = "45.5";
  panel.hass = { ...hass, states: { ...hass.states, "cover.shutter": { state: "open",
    attributes: { opening_time: 21, closing_time: 41, cover_profile_pending: false } } } };
  assert.equal(root.querySelector("#profile-effective-opening").textContent, "21");
  assert.equal(root.querySelector("#profile-effective-closing").textContent, "41");
  assert.equal(form.elements.closing_time.value, "45.5");
  form.querySelector('[data-profile-action="update"]').click();
  await tick();
  assert.deepEqual(calls.find((call) => call.type.endsWith("/write")).profile,
    { name: "Different", opening_time: 20, closing_time: 45.5 });
});

const unusedProfile = { id: "unused", name: '<img src=x onerror="alert(1)">', opening_time: 22,
  closing_time: 44, uses: 0, assigned_to: [] };

test("unused profile deletion requires confirmation and leaves the current assignment intact", async () => {
  const { root, calls } = await mountProfiles({ read: () => coverProfileData({ profiles: [
    ...coverProfileData().profiles, unusedProfile,
  ] }) });
  openProfile(root);
  await tick();
  const form = root.querySelector("#profile-form");
  assert.equal(form.querySelector("#profile-delete").hidden, true);
  change(form.elements.profile, "unused");
  form.elements.profile_name.value = ""; // Deletion must not validate unsaved timing/name edits.
  assert.equal(form.querySelector("#profile-delete").hidden, false);
  form.querySelector('[data-profile-action="delete"]').click();
  assert.equal(calls.filter((call) => call.type.endsWith("/write")).length, 0);
  form.querySelector("#profile-delete").click();
  assert.equal(form.querySelector("#profile-delete-confirmation").hidden, false);
  assert.match(form.querySelector("#profile-delete-prompt").textContent, /<img/);
  assert.equal(root.querySelector("img"), null);
  form.querySelector("#profile-delete-cancel").click();
  assert.equal(form.querySelector("#profile-delete-confirmation").hidden, true);
  form.querySelector("#profile-delete").click();
  form.querySelector('[data-profile-action="delete"]').click();
  await tick();
  assert.deepEqual(calls.find((call) => call.type.endsWith("/write")), {
    type: "myhome/cover_profiles/write", entry_id: "one", entity_id: "cover.shutter",
    revision: 3, action: "delete", profile_id: "unused",
  });
  assert.equal(root.querySelector("#profile-form").elements.profile.value, "timed");
  assert.equal(root.querySelector('option[value="unused"]'), null);
  assert.match(root.querySelector("#toast").textContent, /Profilo eliminato/);
});

test("profile usage explains deletion blocks and changing selection cancels confirmation", async () => {
  const { root, calls } = await mountProfiles({ read: () => coverProfileData({ profiles: [
    { ...coverProfileData().profiles[0], uses: 2, assigned_to: [
      { entity_id: "cover.living", name: "Soggiorno" }, { entity_id: "cover.kitchen", name: "Cucina" },
    ] }, unusedProfile,
  ] }) });
  openProfile(root);
  await tick();
  const form = root.querySelector("#profile-form");
  assert.match(form.querySelector("#profile-usage").textContent, /Soggiorno, Cucina/);
  assert.equal(form.querySelector("#profile-delete").hidden, true);
  form.querySelector("#profile-delete").click();
  assert.equal(form.querySelector("#profile-delete-confirmation").hidden, true);
  change(form.elements.profile, "unused");
  form.querySelector("#profile-delete").click();
  change(form.elements.profile, "timed");
  assert.equal(form.querySelector("#profile-delete-confirmation").hidden, true);
  form.querySelector('[data-profile-action="delete"]').click();
  assert.equal(calls.filter((call) => call.type.endsWith("/write")).length, 0);
});

test("failed deletion keeps the profile and displays storage or concurrent assignment errors", async () => {
  for (const code of ["storage_error", "profile_in_use", "revision_conflict"]) {
    const { panel, root } = await mountProfiles({ read: () => coverProfileData({ profiles: [unusedProfile] }),
      write: () => { throw { code }; } });
    openProfile(root);
    await tick();
    const form = root.querySelector("#profile-form");
    change(form.elements.profile, "unused");
    form.querySelector("#profile-delete").click();
    form.querySelector('[data-profile-action="delete"]').click();
    await tick();
    assert.ok(form.querySelector('option[value="unused"]'));
    assert.equal(form.querySelector("#profile-error").hidden, false);
    assert.match(form.querySelector("#profile-error").textContent,
      code === "storage_error" ? /Salvataggio fallito/ : code === "profile_in_use" ? /ancora assegnato/ : /bozza è conservata/);
    panel.remove();
  }
});

test("profile dialog mounts calibration and gateway navigation cancels only its session", async () => {
  const { root, hass } = await mountProfiles();
  const requests = [];
  let stopped = 0;
  const state = { entry_id: "one", entity_id: "cover.shutter", session_id: "measuring-one", sequence: 1,
    phase: "confirm_closed", revision: 3, values: {}, reason: null, stop_requested: false };
  hass.connection.subscribeMessage = async (callback, request) => {
    if (request.type === "myhome/cover_profiles/subscribe") return () => {};
    requests.push(request); callback(state); return () => { stopped++; };
  };
  const original = hass.callWS;
  hass.callWS = async (message) => {
    if (message.type === "myhome/cover_calibration/action") { requests.push(message); return state; }
    return original(message);
  };
  openProfile(root);
  await tick();
  root.querySelector("#profile-calibrate").click();
  await tick();
  assert.match(root.querySelector("#cal-phase").textContent, /completamente chiusa/);
  assert.deepEqual(requests[0], { type: "myhome/cover_calibration/start", entry_id: "one", entity_id: "cover.shutter", revision: 3 });
  change(root.querySelector("#gateway"), "two");
  await tick();
  assert.equal(root.querySelector("dialog"), null);
  assert.equal(stopped, 1);
  assert.deepEqual(requests[1], { type: "myhome/cover_calibration/action", entry_id: "one", session_id: "measuring-one", action: "cancel" });
});

test("panel language changes keep the native monitor capture and command draft", async () => {
  const { panel, root, hass } = await mount({ callWS: async (message, data) => message.type === "myhome/panel/inventory" ? structuredClone(data) : { frames: [] } });
  change(root.getElementById("gateway"), "one"); root.querySelector('[data-view="bus"]').click(); await tick();
  const view = root.querySelector("myhome-panel-bus-monitor");
  const input = view.shadowRoot.getElementById("send-frame"); input.value = "*1*0*11##";
  view._onNewFrame({ raw: "*1*1*11##", who: "1", timestamp: 100, direction: "rx" });
  const pending = deferred(); hass.callService = () => pending.promise;
  const sweeping = view._handleSweepBus();
  assert.equal(view.shadowRoot.getElementById("btn-sweep").disabled, true);
  panel.hass = { ...hass, language: "en" }; await tick();
  pending.resolve(); await sweeping;
  assert.equal(view.shadowRoot.getElementById("btn-sweep").disabled, false);
  assert.equal(root.querySelector("myhome-panel-bus-monitor"), view);
  assert.equal(view._frames.length, 1); assert.equal(view.shadowRoot.getElementById("send-frame"), input);
  assert.equal(input.value, "*1*0*11##"); assert.equal(view.shadowRoot.getElementById("btn-clear").textContent, "Clear");
});


test("profile editor passes the selected automatic mode without starting movement", async () => {
  const { root, hass, calls } = await mountProfiles();
  let request;
  hass.connection.subscribeMessage = async (_callback, message) => { request = message; return () => {}; };
  openProfile(root); await tick();
  change(root.querySelector("#cal-mode"), "automatic");
  root.querySelector("#profile-calibrate").click(); await tick();
  assert.equal(request.type, "myhome/cover_calibration/start");
  assert.equal(request.mode, "automatic");
  assert.equal(calls.filter((c) => c.type === "myhome/cover_calibration/action").length, 0);
});


test("batch selector is explicit, gateway-scoped, bounded and submits only chosen eligible covers", async () => {
  const { root, hass, calls } = await mountProfiles({ targets: () => ({ revision: 8, max_batch: 1,
    targets: [ { entity_id: "cover.one", name: "Kitchen", reason: null },
      { entity_id: "cover.two", name: "Bedroom", reason: null },
      { entity_id: "cover.offline", name: "Offline", reason: "cover_unavailable" } ] }) });
  let subscribed;
  hass.connection.subscribeMessage = async (_cb, request) => { subscribed = request; return () => {}; };
  openProfile(root); await tick();
  root.querySelector("#profile-calibrate-batch").click(); await tick();
  assert.deepEqual(calls.find((c) => c.type.endsWith("/targets")), { type: "myhome/cover_calibration/targets", entry_id: "one" });
  const checks = [...root.querySelectorAll(".batch-target input")];
  assert.equal(checks.length, 3); assert.ok(checks.every((c) => !c.checked));
  assert.equal(checks[2].disabled, true);
  const next = root.querySelector("#batch-continue"); assert.equal(next.disabled, true);
  checks[0].click(); checks[1].click();
  assert.equal(next.disabled, true);
  assert.match(root.querySelector("#batch-error").textContent, /massimo/);
  checks[0].click();
  assert.equal(next.disabled, false);
  next.click(); await tick();
  assert.deepEqual(subscribed, { type: "myhome/cover_calibration/batch_start", entry_id: "one", entity_ids: ["cover.two"], revision: 8 });
  assert.equal(calls.filter((c) => c.type.endsWith("/action")).length, 0);
});

test("batch selector ignores a late target response after navigation", async () => {
  const pending = deferred();
  const { root } = await mountProfiles({ targets: () => pending.promise });
  openProfile(root); await tick();
  root.querySelector("#profile-calibrate-batch").click();
  change(root.querySelector("#gateway"), "two");
  pending.resolve({ targets: [], max_batch: 20, revision: 3 }); await tick();
  assert.equal(root.querySelector("dialog"), null);
});


test("single-direction choice uses saved assignment and resets when switching to automatic", async () => {
  const { root, hass, calls } = await mountProfiles();
  let request;
  hass.connection.subscribeMessage = async (_callback, message) => { request = message; return () => {}; };
  openProfile(root); await tick();
  const scope = root.querySelector("#cal-direction");
  assert.equal(scope.value, "");
  change(scope, "closing"); change(root.querySelector("#cal-mode"), "automatic");
  assert.equal(scope.value, ""); assert.equal(scope.disabled, true);
  change(root.querySelector("#cal-mode"), "guided");
  assert.equal(scope.disabled, false); change(scope, "opening");
  // An unsaved editor draft never becomes the calibration source.
  root.querySelector('#profile-form [name="closing_time"]').value = "99";
  root.querySelector("#profile-calibrate").click(); await tick();
  assert.equal(request.direction, "opening"); assert.equal(request.revision, 3);
  assert.equal("profile" in request, false); assert.equal("values" in request, false);
  assert.equal(calls.filter((c) => c.type === "myhome/cover_calibration/action").length, 0);
});

test("single-direction options require an assigned saved profile", async () => {
  const { root } = await mountProfiles({ read: () => coverProfileData({ assigned_profile_id: null }) });
  openProfile(root); await tick();
  const scope = root.querySelector("#cal-direction");
  assert.equal(scope.querySelector('[value="opening"]').disabled, true);
  assert.equal(scope.querySelector('[value="closing"]').disabled, true);
  assert.equal(scope.querySelector('[value=""]').disabled, false);
});
