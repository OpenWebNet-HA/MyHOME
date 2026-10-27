import assert from "node:assert/strict";
import { after, afterEach, test } from "node:test";
import { JSDOM } from "jsdom";

import { translations } from "../../custom_components/myhome/frontend/panel/panel-translations.js";

const dom = new JSDOM("<!doctype html><html><body></body></html>", { url: "http://localhost/", pretendToBeVisual: true });
for (const key of ["window", "document", "HTMLElement", "customElements", "CustomEvent", "Event", "KeyboardEvent", "MouseEvent", "history"]) {
  globalThis[key] = key === "window" ? dom.window : dom.window[key];
}
dom.window.HTMLDialogElement.prototype.showModal = function () { this.open = true; };
dom.window.HTMLDialogElement.prototype.close = function () { this.open = false; };
await import("../../custom_components/myhome/frontend/panel/myhome-panel.js");
const { coverStatus, timedCovers } = await import("../../custom_components/myhome/frontend/panel/panel-calibration-tab.js");

const tick = async (count = 3) => { for (let i = 0; i < count; i++) await new Promise((resolve) => setImmediate(resolve)); };
afterEach(() => { document.body.replaceChildren(); window.localStorage.clear(); window.history.replaceState(null, "", "/"); });
after(() => dom.window.close());

const guided = (date, extra = {}) => ({ source: "guided", recorded_at: date, origin_entity_id: null, origin_name: null, inherited: false, ...extra });
const unknown = { source: "unknown", recorded_at: null, origin_entity_id: null, origin_name: null, inherited: false };
const timing = (value, provenance, origin = "override") => ({ value, origin, provenance, scaled: false });

function overview() {
  return { entry_id: "one", revision: 7, capabilities: { profile_management: true, profile_assignment: true },
    profiles: [{ id: "tall", name: "tall_two_leaf", opening_time: 22.4, closing_time: 21.9, assigned_to: ["cover.living_two", "cover.kitchen_two"],
      provenance: { opening: guided("2026-09-29T10:00:00+00:00"), closing: guided("2026-09-29T10:00:00+00:00") } }],
    covers: [
      // L-73: calibrated with its check kept (2 cm: verified, green).
      { entity_id: "cover.living_one", name: "Living room shutter 1", profile_id: null, travel_cm: 198, available: true, advanced: false, overrides: {},
        check_cm: 2, check_measured_cm: 97, check_expected_cm: 99, check_at: "2026-10-01T09:10:00+00:00",
        effective: { opening: timing(22.24, guided("2026-10-01T09:00:00+00:00")), closing: timing(21.71, guided("2026-10-01T09:00:00+00:00")) } },
      { entity_id: "cover.living_two", name: "Living room shutter 2", profile_id: "tall", travel_cm: 196, available: true, advanced: false, overrides: {},
        effective: { opening: timing(22.4, guided("2026-09-29T10:00:00+00:00", { inherited: true, origin_name: "Living room shutter 1" }), "profile"),
          closing: timing(21.9, guided("2026-09-29T10:00:00+00:00", { inherited: true, origin_name: "Living room shutter 1" }), "profile"),
          slat_time_s: timing(4.4, guided("2026-09-29T10:00:00+00:00"), "profile"), opening_roll: timing(1.8312, guided("2026-09-29T10:00:00+00:00"), "profile"),
          closing_roll: timing(1.9071, guided("2026-09-29T10:00:00+00:00"), "profile") } },
      { entity_id: "cover.kitchen_two", name: "Kitchen shutter 2", profile_id: "tall", travel_cm: null, available: true, advanced: false, overrides: {},
        effective: { opening: timing(22.4, guided("2026-09-29T10:00:00+00:00", { inherited: true }), "profile"), closing: timing(21.9, guided("2026-09-29T10:00:00+00:00", { inherited: true }), "profile") } },
      { entity_id: "cover.bedroom", name: "Bedroom", profile_id: null, travel_cm: null, available: true, advanced: false, overrides: {},
        effective: { opening: timing(30, unknown, "native_fallback"), closing: timing(30, unknown, "native_fallback") } },
      { entity_id: "cover.sensor", name: "Skylight", profile_id: null, available: true, advanced: true, overrides: {}, effective: null },
    ] };
}

function inventory() {
  const cover = (entity_id, name, area_id = "living") => ({ entity_id, entry_id: "one", domain: "cover", who: "2", name: null, original_name: name,
    device_id: null, area_id, unique_id: `uid-${entity_id}`, address: { raw: "91", a: "9", pl: "1", interface: null } });
  return { version: "2.0.0b13", panel_version: "0.41.0",
    gateways: [{ entry_id: "one", title: "Casa", mac: "00:03:50:00:00:01", model: "F454", host: "192.0.2.1", state: "loaded", connected: true, monitor_available: true }],
    devices: [],
    entities: [cover("cover.living_one", "Living room shutter 1"), cover("cover.living_two", "Living room shutter 2"),
      cover("cover.kitchen_two", "Kitchen shutter 2", "kitchen"), cover("cover.bedroom", "Bedroom", "bedroom"), cover("cover.sensor", "Skylight", "attic")],
    areas: [{ id: "living", name: "Living room" }, { id: "kitchen", name: "Kitchen" }, { id: "bedroom", name: "Bedroom" }] };
}

async function mount({ data = overview(), session = null, language = "en", callWS } = {}) {
  const calls = [], subscriptions = [];
  const hass = {
    language, states: {},
    connection: {
      subscribeMessage: async (callback, request) => { subscriptions.push({ callback, request }); return () => {}; },
      subscribeEvents: async () => () => {},
    },
    callWS: async (message) => {
      calls.push(message);
      if (callWS) { const result = await callWS(message); if (result !== undefined) return result; }
      if (message.type === "myhome/panel/inventory") return inventory();
      if (message.type === "myhome/cover_profiles/overview") return structuredClone(data);
      if (message.type === "myhome/cover_profiles/read") return { revision: 7, calibration: session };
      throw new Error(`Unexpected command: ${message.type}`);
    },
  };
  const panel = document.createElement("myhome-panel");
  panel.hass = hass;
  document.body.append(panel);
  await tick();
  const root = panel.shadowRoot;
  root.querySelector('[data-action="cover-view"][data-id="calibration"]').click();
  await tick(6);
  return { panel, root, calls, subscriptions, hass, tab: root.getElementById("cover-calibration-tab") };
}

const rows = (tab) => [...tab.querySelectorAll(".cal-row")];
const row = (tab, id) => tab.querySelector(`.cal-row[data-entity="${id}"]`);
const commands = (calls) => calls.filter((message) => message.type.startsWith("myhome/cover_calibration/"));

test("L-73: status comes from the saved evidence and the kept check: to calibrate, calibrated, verified", () => {
  const data = overview();
  const [one, two, kitchen, bedroom] = data.covers;
  assert.deepEqual(coverStatus(one), { status: "verified", detail: "times", check: 2, rolls: false, last: "2026-10-01T09:00:00+00:00", method: "guided", origin: null });
  assert.deepEqual([coverStatus(two).status, coverStatus(two).detail, coverStatus(two).check], ["calibrated", "rolls", null], "a followed profile carries no check");
  assert.equal(coverStatus(two).method, "inherited");
  assert.equal(coverStatus(two).origin, "Living room shutter 1");
  assert.deepEqual([coverStatus(kitchen).status, coverStatus(kitchen).detail], ["calibrated", "times"]);
  assert.equal(coverStatus({ ...one, check_cm: null }).status, "calibrated", "measured times without a check");
  assert.equal(coverStatus({ ...bedroom, check_cm: 3 }).status, "to_measure", "a check without measured times says nothing");
  assert.equal(coverStatus(bedroom).status, "to_measure");
  assert.equal(coverStatus(bedroom).last, null);
  assert.deepEqual(timedCovers(data).map((cover) => cover.entity_id), ["cover.living_one", "cover.living_two", "cover.kitchen_two", "cover.bedroom"]);
});

test("the Calibration sub-tab sits next to Devices and Profiles and lists timed covers only, without commands", async () => {
  const { root, tab, calls } = await mount();
  const views = [...root.querySelectorAll('[data-action="cover-view"]')].map((button) => [button.dataset.id, button.textContent.trim()]);
  assert.deepEqual(views, [["devices", "Devices"], ["profiles", "Profiles"], ["calibration", "Calibration"]]);
  assert.equal(root.querySelector('[data-action="cover-view"][data-id="calibration"]').getAttribute("aria-pressed"), "true");
  assert.equal(tab.querySelector("h3").textContent, "Calibration");
  assert.deepEqual(rows(tab).map((item) => item.dataset.entity), ["cover.bedroom", "cover.kitchen_two", "cover.living_one", "cover.living_two"]);
  assert.equal(row(tab, "cover.sensor"), null, "covers with a position sensor need no calibration");
  const badge = (id) => row(tab, id).querySelector(".cal-badge");
  assert.deepEqual(["cover.bedroom", "cover.living_one", "cover.living_two"].map((id) => [badge(id).dataset.status, badge(id).textContent]),
    [["to_measure", "To calibrate"], ["verified", "Verified · 2 cm"], ["calibrated", "Calibrated"]], "L-73: three states");
  assert.equal(badge("cover.living_one").dataset.tone, "success", "within the check's threshold: green");
  assert.equal(row(tab, "cover.living_two").querySelector(".cal-profile-chip").textContent, "tall_two_leaf");
  assert.equal(row(tab, "cover.living_one").querySelector(".cal-row-profile").textContent.trim(), "—");
  assert.deepEqual([...tab.querySelectorAll(".cal-list-head > span")].map((cell) => cell.querySelector("button")?.firstChild.textContent ?? cell.textContent),
    ["", "Cover", "Status", "Profile", ""], "no column for the last measurement: its date is in the details");
  assert.equal(tab.querySelector(".cal-row-last"), null);
  assert.equal(row(tab, "cover.living_one").querySelector(".cal-row-last-inline").textContent, "Calibrated on Oct 1, 2026", "phones show it on its own line under the name");
  assert.equal(row(tab, "cover.bedroom").querySelector(".cal-row-last-inline").textContent, "Never calibrated");
  assert.equal(row(tab, "cover.bedroom").querySelector(".cal-row-name > .muted .cal-row-last-inline"), null, "not inside the address line");
  assert.equal(tab.querySelector(".cal-tab-summary").textContent, "4 covers · 3 calibrated · 1 to calibrate");
  assert.equal(commands(calls).length, 0, "rendering the list never starts a measurement");
  assert.deepEqual(calls.filter((message) => message.type.startsWith("myhome/cover_profiles/")).map((message) => [message.type, message.entity_id]),
    [["myhome/cover_profiles/overview", undefined], ["myhome/cover_profiles/read", "cover.living_one"]]);
});

test("filters keep their counts, explain covers to measure and follow the panel search", async () => {
  const { root, tab } = await mount();
  const chips = () => Object.fromEntries([...tab.querySelectorAll('[data-action="cal-tab-filter"]')].map((chip) => [chip.dataset.id, chip.querySelector(".count").textContent]));
  assert.deepEqual(chips(), { all: "4", to_measure: "1", calibrated: "3", profile: "2" });
  tab.querySelector('[data-action="cal-tab-filter"][data-id="to_measure"]').click();
  assert.deepEqual(rows(tab).map((item) => item.dataset.entity), ["cover.bedroom"]);
  assert.match(tab.querySelector(".cal-tab-help").textContent, /default travel times/);
  assert.equal(tab.querySelector('[data-action="cal-tab-filter"][data-id="to_measure"]').getAttribute("aria-pressed"), "true");
  tab.querySelector('[data-action="cal-tab-filter"][data-id="profile"]').click();
  assert.deepEqual(rows(tab).map((item) => item.dataset.entity), ["cover.kitchen_two", "cover.living_two"]);
  assert.equal(tab.querySelector(".cal-tab-help"), null);
  tab.querySelector('[data-action="cal-tab-filter"][data-id="all"]').click();
  const search = root.getElementById("search");
  search.value = "tall_two"; search.dispatchEvent(new Event("input", { bubbles: true }));
  await tick();
  assert.deepEqual(rows(root.getElementById("cover-calibration-tab")).map((item) => item.dataset.entity), ["cover.kitchen_two", "cover.living_two"]);
});

test("the chevron expands a row in place with the values in use, the last measurement and the profile", async () => {
  const { tab } = await mount();
  const toggle = row(tab, "cover.living_two").querySelector('[data-action="cal-tab-toggle"]');
  assert.equal(toggle.getAttribute("aria-expanded"), "false");
  assert.equal(row(tab, "cover.living_two").querySelector(".cal-row-details").hidden, true);
  toggle.click();
  const expanded = row(tab, "cover.living_two");
  assert.equal(expanded.querySelector('[data-action="cal-tab-toggle"]').getAttribute("aria-expanded"), "true");
  const details = expanded.querySelector(".cal-row-details");
  assert.equal(details.hidden, false);
  const facts = Object.fromEntries([...details.querySelectorAll(".cal-facts div")].map((item) => [item.querySelector("dt").textContent, item.querySelector("dd").textContent]));
  // D-04: values keep their unit and dates their year with no-break spaces.
  const nb = "\u00a0";
  assert.deepEqual(facts, { "Opening time": `22.4${nb}s`, "Closing time": `21.9${nb}s`, Slats: `Yes · 4.4${nb}s`, "Curtain travel": `196${nb}cm`,
    "Roll on ascent": "1.83", "Roll on descent": "1.91", Date: facts.Date, Method: "From the profile · times and roll" }, "L-73: what was measured, now on the Method line");
  // L-33: the date with its time (in the browser's time zone).
  assert.match(facts.Date, new RegExp(`^Sep${nb}29,${nb}2026${nb}\\d{2}:\\d{2}$`));
  // L-100: no plural to agree: «Also followed by: A, B», commas only.
  assert.match(details.textContent, /Also followed by: Kitchen shutter\u00a02(?![.,])/);
  assert.match(details.textContent, /adapted to the curtain travel of this cover/);
  const blockActions = (entity) => {
    const toggle = row(tab, entity).querySelector('[data-action="cal-tab-toggle"]');
    if (toggle.getAttribute("aria-expanded") !== "true") toggle.click();
    return [...row(tab, entity).querySelectorAll(".cal-details-grid > section")]
      .map((section) => [...section.querySelectorAll(".cal-block-action button")].map((button) => [button.dataset.action, button.textContent.trim()]));
  };
  assert.deepEqual(blockActions("cover.living_two"), [[["cal-tab-refine", "Fine-tune"]], [["cal-tab-measure-cover", "Recalibrate"]], [["cal-tab-assign", "Change profile"]]],
    "each block ends with its own action");
  assert.deepEqual(blockActions("cover.living_one"), [[], [["cal-tab-measure-cover", "Recalibrate"]], [["cal-tab-assign", "Assign profile"]]],
    "no Fine-tune without measured rolls");
  assert.deepEqual(blockActions("cover.bedroom"), [[], [["cal-tab-measure-cover", "Calibrate"]], [["cal-tab-assign", "Assign profile"]]], "a cover without a profile is assigned one");
  const menuLabel = (id) => row(tab, id).querySelector('.cal-menu [data-action="cal-tab-assign"]').textContent.trim();
  assert.deepEqual([menuLabel("cover.living_two"), menuLabel("cover.bedroom")], ["Change profile", "Assign profile"], "the ⋮ menu says the same");
  assert.equal(tab.querySelector(".cal-details-actions"), null, "no shared row of buttons");
  assert.ok([...tab.querySelectorAll('[data-action="cal-tab-assign"] ha-icon')].every((icon) => icon.getAttribute("icon") === "mdi:link-variant"),
    "Assign profile shows the link of the profile's covers, not a pencil");
  const measureIcon = (id) => row(tab, id).querySelector('.cal-block-action [data-action="cal-tab-measure-cover"] ha-icon').getAttribute("icon");
  assert.deepEqual([measureIcon("cover.bedroom"), measureIcon("cover.living_one")], ["mdi:ruler", "mdi:reload"], "Measure a ruler, Remeasure a reload");
  assert.equal(tab.querySelector('[data-action="cal-tab-measure"] ha-icon').getAttribute("icon"), "mdi:ruler", "the tab's Calibrate a cover too");
  const refineIcons = [...tab.querySelectorAll('[data-action="cal-tab-refine"] ha-icon')];
  assert.ok(refineIcons.length && refineIcons.every((icon) => icon.getAttribute("icon") === "mdi:target"), "Fine-tune shows a target");
  row(tab, "cover.living_two").querySelector('[data-action="cal-tab-toggle"]').click();
  assert.equal(row(tab, "cover.living_two").querySelector(".cal-row-details").hidden, true);
});

test("every text key the calibration screens use exists in English and Italian", async () => {
  const { readFileSync } = await import("node:fs");
  const base = new URL("../../custom_components/myhome/frontend/panel/", import.meta.url);
  const keys = new Set();
  for (const file of ["panel-calibration-tab.js", "panel-calibration-wizard.js", "panel-calibration-screens.js", "panel-calibration-illustration.js"]) {
    const source = readFileSync(new URL(file, base), "utf8");
    for (const match of source.matchAll(/\b(?:t|tx)\("([A-Za-z0-9_]+)"/g)) keys.add(match[1]);
  }
  // Keys built from a value: every value the views can carry.
  for (const value of ["all", "to_measure", "calibrated", "profile"]) keys.add(`calTabFilter_${value}`);
  for (const value of ["to_measure", "calibrated", "verified"]) keys.add(`calTabStatus_${value}`);
  for (const value of ["times", "rolls"]) keys.add(`calTabStatusDetail_${value}`);
  for (const value of ["guided", "automatic", "inherited", "none"]) keys.add(`calTabMethod_${value}`);
  for (const value of ["unexpected_movement", "external_command", "unexpected_stop", "stopped", "not_delivered", "start_timeout",
    "not_stopped", "stop_queue_full", "invalid_measurement", "travel_timeout", "cover_unavailable"]) keys.add(`calReason_${value}`);
  assert.ok(keys.size > 150, `${keys.size} keys found`);
  for (const key of keys) {
    assert.ok(translations.en[key], `en ${key}`);
    assert.ok(translations.it[key], `it ${key}`);
  }
});

test("row menu items move with the arrow keys", async () => {
  const { root, tab } = await mount();
  row(tab, "cover.bedroom").querySelector('[data-action="cal-tab-menu"]').click();
  const items = [...row(tab, "cover.bedroom").querySelectorAll('[role="menuitem"]')];
  assert.equal(root.activeElement, items[0]);
  items[0].dispatchEvent(new KeyboardEvent("keydown", { key: "ArrowDown", bubbles: true }));
  assert.equal(root.activeElement, items[1]);
  items[1].dispatchEvent(new KeyboardEvent("keydown", { key: "ArrowUp", bubbles: true }));
  assert.equal(root.activeElement, items[0]);
  items[0].dispatchEvent(new KeyboardEvent("keydown", { key: "ArrowUp", bubbles: true }));
  assert.equal(root.activeElement, items.at(-1), "the arrows wrap");
  items.at(-1).dispatchEvent(new KeyboardEvent("keydown", { key: "ArrowDown", bubbles: true }));
  assert.equal(root.activeElement, items[0]);
});

test("review S2: the menu's focus and arrows skip disabled items, on a cover in progress and on a gateway busy with another", async () => {
  const session = { entry_id: "one", entity_id: "cover.bedroom", session_id: "s1", mode: "geometry", phase: "briefing", step: "home", sequence: 2, values: {},
    recoverable: true, attached: false, owner_present: true };
  const { root, tab } = await mount({ session });
  for (const id of ["cover.bedroom", "cover.living_two"]) {
    row(tab, id).querySelector('[data-action="cal-tab-menu"]').click(); await tick();
    const all = [...row(tab, id).querySelectorAll('[role="menuitem"]')], items = all.filter((item) => !item.disabled);
    assert.ok(all.some((item) => item.disabled), `${id}: has a disabled item`);
    assert.ok(items.length >= 1);
    assert.equal(root.activeElement, items[0], `${id}: the focus opens on the first usable item`);
    for (let i = 1; i <= items.length; i++) {
      root.activeElement.dispatchEvent(new KeyboardEvent("keydown", { key: "ArrowDown", bubbles: true }));
      assert.equal(root.activeElement, items[i % items.length], `${id}: ArrowDown never stops on a disabled item`);
    }
    root.activeElement.dispatchEvent(new KeyboardEvent("keydown", { key: "ArrowUp", bubbles: true }));
    assert.equal(root.activeElement, items.at(-1), `${id}: ArrowUp wraps to the last usable item`);
    root.activeElement.dispatchEvent(new KeyboardEvent("keydown", { key: "Escape", bubbles: true })); await tick();
  }
});

test("the row menu closes with Escape back on its button", async () => {
  const { root, tab } = await mount();
  const menuButton = row(tab, "cover.bedroom").querySelector('[data-action="cal-tab-menu"]');
  menuButton.click();
  const menu = row(tab, "cover.bedroom").querySelector(".cal-menu");
  assert.equal(menu.hidden, false);
  assert.equal(root.activeElement, menu.querySelector('[role="menuitem"]'));
  menu.dispatchEvent(new KeyboardEvent("keydown", { key: "Escape", bubbles: true }));
  assert.equal(row(tab, "cover.bedroom").querySelector(".cal-menu").hidden, true);
  assert.equal(root.activeElement, row(tab, "cover.bedroom").querySelector('[data-action="cal-tab-menu"]'));
});

test("the chevron column's header closes the visible rows when any is open, otherwise opens them all, within the filter", async () => {
  const { tab } = await mount();
  const button = () => tab.querySelector(".cal-list-head .cal-expand-all");
  const open = () => [...tab.querySelectorAll(".cal-row")].filter((item) => item.classList.contains("is-expanded")).map((item) => item.dataset.entity);
  const state = () => [button().getAttribute("aria-label"), button().querySelector("ha-icon").getAttribute("icon")];
  assert.deepEqual(state(), ["Expand all", "mdi:unfold-more-horizontal"], "none open: expand");
  button().click();
  assert.deepEqual(open().sort(), ["cover.bedroom", "cover.kitchen_two", "cover.living_one", "cover.living_two"]);
  assert.deepEqual(state(), ["Collapse all", "mdi:unfold-less-horizontal"]);
  button().click();
  assert.deepEqual(open(), []);
  // One open row is enough to offer Collapse all.
  row(tab, "cover.bedroom").querySelector('[data-action="cal-tab-toggle"]').click();
  assert.equal(state()[0], "Collapse all", "one open: collapse");
  button().click();
  assert.deepEqual(open(), []);
  // With a filter, only the rows it shows count and move.
  row(tab, "cover.living_two").querySelector('[data-action="cal-tab-toggle"]').click();
  tab.querySelector('[data-action="cal-tab-filter"][data-id="to_measure"]').click();
  assert.equal(state()[0], "Expand all", "the open row is not among the visible ones");
  button().click();
  assert.deepEqual(open(), ["cover.bedroom"]);
  tab.querySelector('[data-action="cal-tab-filter"][data-id="all"]').click();
  assert.deepEqual(open().sort(), ["cover.bedroom", "cover.living_two"], "rows outside the filter were left as they were");
});

test("the page does not pull to refresh while the wizard or the sequence view is shown", async () => {
  const { tab } = await mount();
  const value = () => document.documentElement.style.overscrollBehaviorY;
  assert.equal(value(), "");
  tab.querySelector('[data-action="cal-tab-measure"]').click(); await tick();
  assert.equal(value(), "none", "wizard");
  tab.querySelector('.wiz-head [data-wiz="exit"]').click(); await tick();
  assert.equal(value(), "", "back to the list: as before");
  tab.querySelector('[data-action="cal-tab-batch"]').click(); await tick();
  assert.equal(value(), "none", "sequence view");
  assert.equal(document.body.style.overscrollBehaviorY, "none");
  tab.querySelector('[data-action="cal-tab-batch-back"]').click(); await tick();
  assert.equal(value(), "");
});

test("Assign profile lists the saved profiles as B-01 does and assigns with the existing write, never through the profile dialog", async () => {
  const data = overview();
  // As Home Assistant publishes it for a cover named by its device: the registry name is only the entity id.
  Object.assign(data.profiles[0].provenance.opening, { origin_entity_id: "cover.living_one", origin_name: "cover.living_one" });
  data.profiles[0].reference_travel_cm = 198;
  data.profiles[0].geometry = { slat_time_s: 4.4, opening_roll: 1.83, closing_roll: 1.91 };
  data.profiles.push({ id: "low", name: "low_one_leaf", opening_time: 12, closing_time: 11, assigned_to: [], provenance: {} });
  const { root, tab, calls } = await mount({ data, callWS: (message) => message.type === "myhome/cover_profiles/write" ? { revision: 8 } : undefined });
  row(tab, "cover.bedroom").querySelector('[data-action="cal-tab-menu"]').click();
  row(tab, "cover.bedroom").querySelector('.cal-menu [data-action="cal-tab-assign"]').click(); await tick();
  assert.equal(root.querySelector(".cover-profile-dialog:not(.cal-dialog)"), null, "not the maintainer's Travel profile dialog");
  const dialog = root.querySelector("#dialog-host dialog.cal-dialog");
  assert.equal(dialog.querySelector("h2").textContent, "Assign profile");
  assert.equal(dialog.querySelector(".dialog-head .muted").textContent, "Bedroom");
  const options = [...dialog.querySelectorAll(".wiz-radio")].map((label) => [...label.querySelectorAll("strong, small")].map((item) => item.textContent));
  assert.deepEqual(options, [
    ["low_one_leaf", "Times only", "No cover follows it"],
    ["tall_two_leaf", "With slats · Reference travel 198 cm", "Measured on Living room shutter 1 on Sep 29, 2026", "Followed by Living room shutter\u00a02 and Kitchen shutter\u00a02"]],
    "who follows each profile, by name");
  const assign = dialog.querySelector("[data-assign]");
  assert.equal(assign.disabled, true);
  assert.deepEqual([...dialog.querySelectorAll(".dialog-foot button")].map((button) => button.textContent), ["Cancel", "Assign"],
    "as in the design: Cancel, then Assign, on the footer's row");
  const tall = dialog.querySelector('input[value="tall"]');
  tall.checked = true; tall.dispatchEvent(new Event("change", { bubbles: true }));
  assert.equal(assign.disabled, false);
  assign.click(); await tick(4);
  const write = calls.find((message) => message.type === "myhome/cover_profiles/write");
  assert.deepEqual(write, { type: "myhome/cover_profiles/write", entry_id: "one", entity_id: "cover.bedroom", revision: 7, action: "assign", profile_id: "tall" });
  assert.equal(root.querySelector("#dialog-host dialog"), null);
  await tick(4);
  assert.match(tab.querySelector(".cal-tab-done").textContent.trim(), /now follows the profile "/, "D-28: confirmed as an unlinking is");
});

test("notes are opaque, so they never blend with the row that hosts them, and A-01's chosen row is neutral", async () => {
  const css = (await import("node:fs")).readFileSync(new URL("../../custom_components/myhome/frontend/panel/myhome-panel.css", import.meta.url), "utf8");
  assert.match(css, /background: color-mix\(in srgb, var\(--panel-accent\) 8%, var\(--panel-surface\)\); color: var\(--panel-text-2\);/, "info");
  assert.match(css, /background: color-mix\(in srgb, var\(--panel-warning\) 8%, var\(--panel-surface\)\);/, "warning");
  assert.match(css, /\.wiz-pick-row\.is-selected \{ background: color-mix\(in srgb, var\(--primary-text-color, #182b37\) 4%, var\(--panel-surface\)\); \}/, "the grey row stays");
  assert.match(css, /\.cal-note \{[^}]*border: 1px solid color-mix\(in srgb, var\(--panel-accent\) 75%, transparent\); border-inline-start-width: 3px;/, "info: 1 px border, 3 px bar");
  assert.match(css, /\.cal-note\.is-warning \{ border-color: color-mix\(in srgb, var\(--panel-warning\) 75%, transparent\);/, "warning: the same in yellow");
});

test("a cover in progress keeps yellow tints in every state of its row", async () => {
  const css = (await import("node:fs")).readFileSync(new URL("../../custom_components/myhome/frontend/panel/myhome-panel.css", import.meta.url), "utf8");
  for (const selector of [".cal-row.is-running .cal-row-main:hover", ".cal-row.is-running.is-expanded .cal-row-main",
    ".cal-row.is-running.is-expanded .cal-row-main:hover", ".cal-row.is-running.is-expanded .cal-row-details"]) {
    // L-7: the pointer's tints sit inside `@media (hover: hover)`.
    const rule = css.split("\n").find((line) => line.startsWith(`${selector} {`) || line.startsWith(`@media (hover: hover) { ${selector} {`));
    assert.ok(rule, selector);
    assert.match(rule, /var\(--panel-warning\)/, `${selector} is yellow`);
    assert.doesNotMatch(rule, /accent/, `${selector} has no blue`);
  }
});

test("Assign profile waits while a calibration is open on the gateway, and after a refusal", async () => {
  const session = { entry_id: "one", entity_id: "cover.living_one", session_id: "s", mode: "geometry", phase: "briefing", step: "lift", sequence: 3,
    values: {}, recoverable: true, attached: true, owner_present: true };
  const busy = await mount({ session });
  const open = (tab, id) => { row(tab, id).querySelector('[data-action="cal-tab-menu"]').click(); row(tab, id).querySelector('.cal-menu [data-action="cal-tab-assign"]').click(); };
  open(busy.tab, "cover.bedroom"); await tick();
  let dialog = busy.root.querySelector("#dialog-host dialog.cal-dialog");
  const note = dialog.querySelector(".cal-assign-busy");
  assert.equal(note.textContent, "A calibration is in progress: finish or cancel it before assigning a profile.");
  assert.ok(note.classList.contains("cal-note") && !note.classList.contains("error"), "an information, not an error");
  const tall = dialog.querySelector('input[value="tall"]');
  tall.checked = true; tall.dispatchEvent(new Event("change", { bubbles: true }));
  assert.equal(dialog.querySelector("[data-assign]").disabled, true, "Assign waits");
  // Without a session, a refusal turns Assign off too.
  const refusing = await mount({ callWS: (message) => { if (message.type === "myhome/cover_profiles/write") throw Object.assign(new Error("busy"), { code: "calibration_busy" }); } });
  open(refusing.tab, "cover.bedroom"); await tick();
  dialog = refusing.root.querySelector("#dialog-host dialog.cal-dialog");
  assert.equal(dialog.querySelector(".cal-assign-busy"), null);
  const choice = dialog.querySelector('input[value="tall"]');
  choice.checked = true; choice.dispatchEvent(new Event("change", { bubbles: true }));
  dialog.querySelector("[data-assign]").click(); await tick(4);
  const refusal = dialog.querySelector(".cal-assign-error");
  assert.equal(refusal.hidden, false);
  assert.ok(refusal.classList.contains("notice"), "D-29: an error notice, not bare red text");
  assert.equal(refusal.nextElementSibling, dialog.querySelector(".cal-assign-list"), "D-15: above the choices, in view");
  assert.equal(dialog.querySelector("[data-assign]").disabled, true, "off after the refusal");
  choice.dispatchEvent(new Event("change", { bubbles: true }));
  assert.equal(dialog.querySelector("[data-assign]").disabled, true, "and stays off");
});

test("every radio of the Assign dialog is the same circle, No profile included", async () => {
  const css = (await import("node:fs")).readFileSync(new URL("../../custom_components/myhome/frontend/panel/myhome-panel.css", import.meta.url), "utf8");
  assert.match(css, /\.wiz-radio input \{ flex: none; width: 16px; height: 16px;/, "never shrunk by a long description");
  assert.doesNotMatch(css, /\.cal-assign-none input/, "No profile has no circle of its own");
  // Centred on the first line (checked at 1× and 2× in a browser: within half a pixel).
  assert.match(css, /\.wiz-radio \{[^}]*line-height: 20px;/);
  assert.match(css, /\.wiz-radio input \{[^}]*margin: 2px 0 0;/);
  assert.match(css, /\.cal-assign-none strong \{ display: flex; align-items: center; gap: 6px; height: 20px; \}/);
});

test("a click on the backdrop closes our dialogs as Escape does, applying nothing", async () => {
  const { root, tab, calls } = await mount();
  row(tab, "cover.bedroom").querySelector('[data-action="cal-tab-menu"]').click();
  row(tab, "cover.bedroom").querySelector('.cal-menu [data-action="cal-tab-assign"]').click(); await tick();
  const dialog = root.querySelector("#dialog-host dialog.cal-dialog");
  const tall = dialog.querySelector('input[value="tall"]');
  tall.checked = true; tall.dispatchEvent(new Event("change", { bubbles: true }));
  dialog.querySelector(".wiz-radio").dispatchEvent(new MouseEvent("click", { bubbles: true }));
  assert.ok(root.querySelector("#dialog-host dialog"), "a click inside does not close it");
  dialog.dispatchEvent(new MouseEvent("click", { bubbles: true, clientX: 1, clientY: 1 }));
  assert.equal(root.querySelector("#dialog-host dialog"), null, "the backdrop closes it");
  assert.equal(calls.some((message) => message.type === "myhome/cover_profiles/write"), false, "nothing applied");
});

test("Assign profile offers No profile to unlink the followed profile, never on the cover it was measured on", async () => {
  const data = overview();
  Object.assign(data.profiles[0].provenance.opening, { origin_entity_id: "cover.living_two", origin_name: "cover.living_two" });
  const { root, tab, calls } = await mount({ data, callWS: (message) => message.type === "myhome/cover_profiles/write" ? { revision: 8 } : undefined });
  const open = (id) => { row(tab, id).querySelector('[data-action="cal-tab-menu"]').click(); row(tab, id).querySelector('.cal-menu [data-action="cal-tab-assign"]').click(); };
  open("cover.bedroom"); await tick();
  let dialog = root.querySelector("#dialog-host dialog.cal-dialog");
  assert.equal(dialog.querySelector('input[value="__no_profile__"]'), null, "a cover without a profile has nothing to unlink");
  dialog.querySelector(".cal-dialog-close").click(); await tick();
  open("cover.living_two"); await tick();
  dialog = root.querySelector("#dialog-host dialog.cal-dialog");
  const sourceNone = dialog.querySelector('input[value="__no_profile__"]');
  assert.equal(sourceNone.disabled, true, "the profile's source cover: shown, but disabled");
  assert.equal(sourceNone.closest(".wiz-radio").querySelector("small").textContent,
    'This cover is the source of the profile "tall_two_leaf": a coming update will let it unlink the profile and keep its measured values.');
  dialog.querySelector(".cal-dialog-close").click(); await tick();
  open("cover.kitchen_two"); await tick();
  dialog = root.querySelector("#dialog-host dialog.cal-dialog");
  assert.equal(dialog.querySelector("h2").textContent, "Change profile", "a cover that follows a profile changes it");
  const none = dialog.querySelector('input[value="__no_profile__"]');
  assert.ok(none, "a follower can unlink");
  assert.equal(none.closest(".wiz-radio"), dialog.querySelector(".wiz-radio"), "first in the list");
  assert.equal(none.closest(".wiz-radio").querySelector("ha-icon").getAttribute("icon"), "mdi:link-variant-off");
  assert.deepEqual([...none.closest(".wiz-radio").querySelectorAll("strong, small")].map((item) => item.textContent),
    ["No profile", 'Unlinks "tall_two_leaf": the cover will use its own values, if it has any, otherwise it will go back to "to calibrate". The profile will stay available for the other covers.']);
  const button = dialog.querySelector("[data-assign]");
  none.checked = true; none.dispatchEvent(new Event("change", { bubbles: true }));
  assert.equal(button.textContent, "Unlink profile");
  assert.equal(button.querySelector("ha-icon").getAttribute("icon"), "mdi:link-variant-off");
  button.click(); await tick(6);
  const write = calls.filter((message) => message.type === "myhome/cover_profiles/write").at(-1);
  assert.deepEqual(write, { type: "myhome/cover_profiles/write", entry_id: "one", entity_id: "cover.kitchen_two", revision: 7, action: "assign", profile_id: null });
  assert.equal(root.querySelector("#dialog-host dialog"), null);
  assert.equal(tab.querySelector(".cal-tab-done").textContent.trim(), 'Profile unlinked from "Kitchen shutter 2".');
});

test("a session in progress marks its cover, blocks new measurements and opens a sequence in the existing view, in its own dialog", async () => {
  const session = { entry_id: "one", entity_id: "cover.bedroom", session_id: "group", mode: "automatic", batch: true, phase: "paused",
    reason: "owner_absent", sequence: 4, values: {}, recoverable: true, attached: false, owner_present: false, cover_index: 0, results: [],
    targets: [{ entity_id: "cover.bedroom", name: "Bedroom" }, { entity_id: "cover.living_one", name: "Living room shutter 1" }],
    next_step: { step: "next_cover", cover_index: 1, entity_id: "cover.living_one" } };
  const { root, tab, subscriptions, calls } = await mount({ session });
  // L-93: another tab's session (this tab does not own it): it is followed.
  assert.match(tab.querySelector(".cal-tab-busy").textContent, /follow it with «Follow session», or cancel it from the ⋮ menu of its row/);
  assert.equal(tab.querySelector('[data-action="cal-tab-batch"]').disabled, true);
  for (const id of ["cover.bedroom", "cover.living_one"]) {
    assert.equal(row(tab, id).querySelector(".cal-badge").dataset.status, "running");
    assert.equal(row(tab, id).querySelector(".cal-badge").textContent, "In progress · paused");
  }
  assert.equal(row(tab, "cover.living_two").querySelector(".cal-badge").dataset.status, "calibrated");
  const open = row(tab, "cover.bedroom").querySelector('[data-action="cal-tab-session"]');
  assert.equal(open.textContent, "Follow session");
  assert.ok(calls.filter((message) => message.type === "myhome/cover_profiles/read").every((message) => typeof message.client_id === "string"),
    "the read names this tab, to learn whether it owns the session");
  open.click(); await tick();
  const resume = subscriptions.find((item) => item.request.type === "myhome/cover_calibration/resume");
  assert.equal(resume.request.session_id, "group");
  assert.equal("claim" in resume.request, false, "opening never takes control");
  resume.callback({ ...session, attachment: "reader", owner: false, read_only: true });
  const dialog = root.querySelector("#dialog-host dialog.cal-dialog");
  assert.equal(dialog.open, true);
  assert.equal(dialog.querySelector("h2").textContent, "Calibration in sequence");
  assert.equal(dialog.querySelector("#cal-paused").hidden, false);
  assert.match(dialog.querySelector("#cal-paused").textContent, /To resume it, take control here: then "Continue" starts/, "D-18: a reader is told to take control first");
  assert.ok(tab.querySelector(".cal-list"), "the list stays behind the dialog");
  assert.equal(commands(calls).length, 0);
  // D-17/D-18: one bar (the view's own Stop and Cancel/Close), closed by its ✕ at every size; no entity id line.
  assert.equal(dialog.querySelector(".dialog-foot").hidden, true);
  assert.equal(dialog.querySelector(".cal-dialog-x").hidden, false);
  assert.ok(dialog.querySelector(".cal-resume-entity"), "the view's entity id line is marked, and hidden by the stylesheet");
  dialog.querySelector(".cal-dialog-x").click(); await tick();
  assert.equal(root.querySelector("#dialog-host dialog"), null);
});

test("an empty gateway explains which covers need calibration", async () => {
  const data = overview();
  data.covers = data.covers.filter((cover) => cover.advanced);
  const { tab } = await mount({ data, language: "it" });
  // T-04: the tab's title, then a card with the shutter, its title, why, and how it works.
  assert.equal(tab.querySelector(".cal-tab-title").textContent, "Calibrazione");
  assert.equal(tab.querySelector(".cal-tab-empty h4").textContent, "Nessuna tapparella a tempo su questo gateway");
  assert.match(tab.querySelector(".cal-tab-empty > .muted").textContent, /sensore di posizione/);
  assert.equal(tab.querySelector(".cal-tab-empty .cal-empty-how summary").textContent, "Come funziona");
  assert.equal(tab.querySelector(".cal-list"), null);
  assert.equal(tab.querySelector(".cal-filterbar"), null, "no filters for nothing");
});

test("a filter without results says so and keeps the filters (T-04b)", async () => {
  const data = overview();
  data.covers = data.covers.map((cover) => ({ ...cover, profile_id: null }));
  const { tab } = await mount({ data, language: "it" });
  tab.querySelector('.cal-chip[data-id="profile"]').click(); await tick();
  assert.equal(tab.querySelector(".cal-list"), null);
  assert.equal(tab.querySelector(".cal-tab-no-match h4").textContent, "Nessuna tapparella corrisponde al filtro");
  assert.equal(tab.querySelector(".cal-tab-no-match > .muted").textContent, "Cambia o togli il filtro o la ricerca per vedere le altre tapparelle.");
  assert.ok(tab.querySelector(".cal-tab-filters"), "the filters stay, to change it");
  assert.equal(tab.querySelector(".cal-tab-no-match .cal-empty-how"), null, "N-07: «Come funziona» only in the head above");
  // D-09: the head actions stay on (there are covers), and the card offers the way back.
  assert.equal(tab.querySelector('[data-action="cal-tab-measure"]').disabled, false);
  assert.equal(tab.querySelector('[data-action="cal-tab-batch"]').disabled, false);
  tab.querySelector('[data-action="cal-tab-clear"]').click(); await tick();
  assert.equal(tab.querySelector(".cal-tab-no-match"), null);
  assert.ok(tab.querySelector(".cal-list"));
});

test("D-09: a search without results is cleared from the empty list's own button", async () => {
  const { root, tab } = await mount({ language: "it" });
  const search = root.getElementById("search");
  search.value = "zzz"; search.dispatchEvent(new Event("input", { bubbles: true })); await tick();
  assert.ok(tab.querySelector(".cal-tab-no-match"));
  assert.equal(tab.querySelector('[data-action="cal-tab-measure"]').disabled, false, "not off because of the search");
  assert.equal(tab.querySelector('[data-action="cal-tab-clear"]').textContent, "Togli filtro e ricerca");
  assert.equal(tab.querySelector(".cal-tab-no-match h4").textContent, "Nessuna tapparella corrisponde alla ricerca", "D-09: only the search hides them");
  const filter = tab.querySelector("select[data-filter]"); filter.value = "to_measure"; filter.dispatchEvent(new Event("change", { bubbles: true })); await tick();
  assert.equal(tab.querySelector(".cal-tab-no-match h4").textContent, "Nessuna tapparella corrisponde al filtro e alla ricerca", "both");
  tab.querySelector('[data-action="cal-tab-clear"]').click(); await tick();
  assert.equal(search.value, "");
  assert.equal(tab.querySelector(".cal-tab-no-match"), null);
});

const targets = (extra = {}) => ({ revision: 7, max_batch: 20, targets: [
  { entity_id: "cover.living_two", name: "living_two_raw", reason: null }, { entity_id: "cover.bedroom", name: "cover_bedroom_raw", reason: null },
  { entity_id: "cover.living_one", name: "Living room shutter 1", reason: "cover_unavailable" }, { entity_id: "cover.kitchen_two", name: "k", reason: null }], ...extra });
const openBatch = async (options = {}) => {
  const mounted = await mount({ language: "it", ...options, callWS: (message) => message.type === "myhome/cover_calibration/targets" ? targets(options.targets) : undefined });
  mounted.tab.querySelector('[data-action="cal-tab-batch"]').click(); await tick();
  return mounted;
};
const tick_ = (tab, id) => { const box = tab.querySelector(`.cal-batch-row input[value="${id}"]`); box.checked = !box.checked; box.dispatchEvent(new Event("change", { bubbles: true })); };
/** The sequence's «Select» menu (a native drop-down used as a menu of actions, M4-2/M4-5). */
const batchPick = (tab, id) => { const select = tab.querySelector("select[data-batch-pick]"); select.value = id; select.dispatchEvent(new Event("change", { bubbles: true })); };
const places = (tab) => [...tab.querySelectorAll(".cal-batch-row")].map((item) => [item.dataset.entity.replace("cover.", ""), item.querySelector(".cal-batch-order").textContent]);

test("several covers in sequence are chosen in a view of the tab, not a dialog, with the list's names, rooms and status", async () => {
  const { root, tab } = await openBatch();
  assert.equal(root.querySelector("#dialog-host dialog"), null, "no dialog");
  assert.equal(tab.querySelector(".cal-list-rows"), null, "the selection replaces the list");
  assert.equal(tab.querySelector(".cal-batch .wiz-cover").textContent, "Calibrazione in sequenza");
  assert.equal(tab.querySelector(".cal-batch .wiz-head-sub").textContent, "Massimo 20 tapparelle per sequenza", "no count (M5-2)");
  assert.equal(tab.querySelector(".cal-batch .wiz-title").textContent, "Quali tapparelle vuoi calibrare?");
  assert.equal(tab.querySelector(".cal-batch .wiz-body").textContent,
    "Scegli le tapparelle da calibrare: si calibreranno una dopo l'altra, nell'ordine in cui le selezioni, da sole, senza premere nulla durante le corse.");
  const note = tab.querySelector(".cal-batch-note");
  assert.equal(note.textContent, "Servono attuatori che segnalino l'arresto reale: se i tuoi non lo fanno, usa la calibrazione guidata, una tapparella alla volta. Annullare o un errore di misurazione scartano l'intera sequenza non salvata.",
    "the yellow note only around the actuators");
  assert.deepEqual([...note.querySelectorAll("strong")].map((bold) => bold.textContent), ["Servono attuatori che segnalino l'arresto reale"]);
  assert.ok(note.classList.contains("is-warning"));
  assert.doesNotMatch(note.textContent, /\*\*/);
  assert.deepEqual([...tab.querySelectorAll(".cal-batch-row .cal-row-name strong")].map((item) => item.textContent),
    ["Bedroom", "Kitchen shutter 2", "Living room shutter 1", "Living room shutter 2"], "inventory names, sorted by name");
  assert.doesNotMatch(tab.textContent, /cover\.bedroom|cover_bedroom_raw|living_two_raw|gruppo/i);
  assert.match(tab.querySelector('.cal-batch-row[data-entity="cover.kitchen_two"]').textContent.replace(/\u00a0/g, " "), /Kitchen · Indirizzo: 91 · A: 9 · PL: 1/);
  assert.equal(tab.querySelector('.cal-batch-row[data-entity="cover.living_two"] .cal-badge').textContent.replace(/\u00a0/g, " "), "Calibrata");
  assert.equal(tab.querySelector('.cal-batch-row[data-entity="cover.living_two"] .cal-profile-chip').textContent, "tall_two_leaf");
  assert.equal(tab.querySelector('.cal-batch-row input[value="cover.living_one"]').disabled, true);
  tab.querySelector(".cal-batch .wiz-back").click(); await tick();
  assert.ok(tab.querySelector(".cal-list-rows"), "back to the list");
});

test("covers run in the order they are ticked, shortcuts pick all or those to measure, and the limit is the backend's", async () => {
  const { tab } = await openBatch();
  const start = () => tab.querySelector('[data-action="cal-tab-batch-start"]');
  assert.equal(start().disabled, true);
  assert.equal(tab.querySelector(".cal-batch-summary").textContent, "Nessuna tapparella selezionata");
  tick_(tab, "cover.living_two"); tick_(tab, "cover.bedroom");
  assert.deepEqual(places(tab), [["bedroom", "2"], ["kitchen_two", ""], ["living_one", ""], ["living_two", "1"]]);
  assert.equal(tab.querySelector(".cal-batch-summary").textContent, "2 tapparelle selezionate");
  tick_(tab, "cover.living_two");
  assert.deepEqual(places(tab).map(([, place]) => place), ["1", "", "", ""], "unticking renumbers");
  assert.equal(tab.querySelector(".cal-batch-summary").textContent, "1 tapparella selezionata");
  batchPick(tab, "all");
  assert.deepEqual(places(tab).map(([, place]) => place), ["1", "2", "", "3"], "all, in list order, never the unavailable one");
  batchPick(tab, "to_measure");
  assert.deepEqual(places(tab).map(([, place]) => place), ["1", "", "", ""]);
  assert.equal(start().disabled, false);
  assert.equal(tab.querySelector('select[data-batch-pick] option[value="none"]').textContent, "Nessuna");
  batchPick(tab, "all");
  batchPick(tab, "none");
  assert.deepEqual(places(tab).map(([, place]) => place), ["", "", "", ""], "None clears every box and every place");
  assert.equal([...tab.querySelectorAll(".cal-batch-row input:checked")].length, 0);
  assert.equal(tab.querySelector(".cal-batch-summary").textContent, "Nessuna tapparella selezionata");
  assert.equal(start().disabled, true);
  tick_(tab, "cover.kitchen_two");
  assert.deepEqual(places(tab).map(([, place]) => place), ["", "1", "", ""], "the next tick starts again from 1");
});

test("choosing covers patches the sequence view in place: the rows, the spacer and the scroll stay put", async () => {
  const { tab } = await openBatch();
  const view = tab.querySelector(".cal-batch"), rows = [...tab.querySelectorAll(".cal-batch-row")], menu = tab.querySelector("select[data-batch-pick]");
  for (let i = 0; i < 3; i++) batchPick(tab, "all");
  tick_(tab, "cover.living_two");
  assert.equal(tab.querySelector(".cal-batch"), view, "the view is not rebuilt");
  assert.deepEqual([...tab.querySelectorAll(".cal-batch-row")], rows, "nor its rows");
  assert.equal(tab.querySelector("select[data-batch-pick]"), menu, "the menu is the same control");
  assert.deepEqual(places(tab).map(([, place]) => place), ["1", "2", "", ""]);
  assert.equal(tab.querySelector(".cal-batch-summary").textContent, "2 tapparelle selezionate");
  assert.equal(tab.querySelector('[data-action="cal-tab-batch-start"]').disabled, false);
  const css = (await import("node:fs")).readFileSync(new URL("../../custom_components/myhome/frontend/panel/myhome-panel.css", import.meta.url), "utf8");
  assert.match(css, /#items:has\(\.cal-tab\) ~ #discovery-help \{ display: none; \}/, "the Devices note is not shown in the Calibration tab");
});

test("more covers than the backend allows keep Start off and say the limit", async () => {
  const { tab } = await openBatch({ targets: { max_batch: 2 } });
  batchPick(tab, "all");
  assert.equal(tab.querySelector('[data-action="cal-tab-batch-start"]').disabled, true);
  assert.match(tab.querySelector(".cal-batch-limit").textContent, /: 2$/);
});

test("the sequence is a view with the wizard's chrome: header, steps Selection · Run · Results, Start in the actions column (M4-5)", async () => {
  const { tab } = await openBatch();
  const view = tab.querySelector(".cal-batch");
  assert.ok(view.classList.contains("wiz"), "the wizard's chrome");
  assert.equal(view.querySelector(".cal-batch-bar"), null, "no fixed bar in the page");
  assert.equal(view.querySelector(".wiz-discard").textContent, "Annulla la sequenza");
  assert.equal(view.querySelector(".wiz-x").getAttribute("aria-label"), "Annulla la sequenza");
  assert.equal(view.querySelector(".wiz-compact-line").textContent, "Selezione · 1 di 3");
  const steps = () => [...view.querySelectorAll(".wiz-steps > ol > li")].map((item) => [item.querySelector(".wiz-step-label").textContent, item.dataset.stepState, item.querySelector("ul")?.textContent ?? ""]);
  assert.deepEqual(steps(), [["Selezione", "current", "Nessuna selezionata"], ["Esecuzione", "future", ""], ["Risultati", "future", ""]]);
  tick_(tab, "cover.bedroom");
  assert.deepEqual(steps()[0], ["Selezione", "current", "1 selezionata"]);
  tick_(tab, "cover.kitchen_two");
  assert.deepEqual(steps()[0], ["Selezione", "current", "2 selezionate"]);
  const actions = view.querySelector(".wiz-actions");
  assert.deepEqual([actions.querySelector(".cal-batch-summary").textContent, actions.querySelector(".wiz-action.primary").textContent],
    ["2 tapparelle selezionate", "Avvia la calibrazione in sequenza"]);
  // PASSI opens the steps sheet on phones.
  view.querySelector('[data-action="cal-tab-batch-steps"]').click();
  assert.equal(view.querySelector(".wiz-sheet").hidden, false);
  view.querySelector('[data-action="cal-tab-batch-steps-close"]').click();
  assert.equal(view.querySelector(".wiz-sheet").hidden, true);
  // ✕ and «Cancel the sequence» go back to the list.
  view.querySelector(".wiz-x").click(); await tick();
  assert.ok(tab.querySelector(".cal-list-rows"));
});

test("Start sends batch_start once, with the chosen order and the targets' revision", async () => {
  const { root, tab, subscriptions, calls } = await openBatch();
  tick_(tab, "cover.kitchen_two"); tick_(tab, "cover.bedroom");
  const start = tab.querySelector('[data-action="cal-tab-batch-start"]');
  start.click(); start.click(); await tick();
  const starts = subscriptions.filter((item) => item.request.type === "myhome/cover_calibration/batch_start");
  assert.equal(starts.length, 1);
  assert.deepEqual(starts[0].request.entity_ids, ["cover.kitchen_two", "cover.bedroom"]);
  assert.equal(starts[0].request.revision, 7);
  assert.ok(root.querySelector("#dialog-host dialog.cal-dialog"), "the existing view runs the sequence, for now in its dialog");
  assert.ok(tab.querySelector(".cal-list-rows"), "the tab is back on the list behind it");
  assert.equal(commands(calls).filter((message) => message.type === "myhome/cover_calibration/action").length, 0);
});

test("Italian texts of the tab carry no placeholder", async () => {
  const { tab } = await mount({ language: "it" });
  row(tab, "cover.living_two").querySelector('[data-action="cal-tab-toggle"]').click();
  assert.doesNotMatch(tab.textContent, /\{\w+\}|calTab|wiz_T/);
  assert.match(tab.textContent, /Verificata · 2 cm/);
  assert.match(tab.textContent, /Dal profilo · tempi e rullo/, "L-73: the Method line says what was measured");
  assert.match(tab.querySelector(".cal-tab-summary").textContent, /^4 tapparelle · 3 calibrate · 1 da calibrare$/);
});

test("a gateway whose session cannot be read but is reserved keeps new measurements off; a failed read tries the next cover", async () => {
  const data = overview();
  for (const cover of data.covers) cover.assignment_reason = "calibration_busy";
  let reads = 0;
  const { tab, calls } = await mount({ data, callWS: (message) => {
    if (message.type === "myhome/cover_profiles/read") { reads++; throw Object.assign(new Error("x"), { code: "storage_error" }); }
    return undefined;
  } });
  assert.equal(reads, 3, "three covers are tried before giving up for this refresh");
  assert.match(tab.querySelector(".cal-tab-busy").textContent, /cannot read: try again in a moment/);
  assert.equal(tab.querySelector('[data-action="cal-tab-measure"]').disabled, true);
  assert.equal(tab.querySelector('[data-action="cal-tab-batch"]').disabled, true);
  assert.ok([...tab.querySelectorAll('[data-action="cal-tab-measure-cover"]')].every((button) => button.disabled));
  assert.equal(calls.filter((message) => message.type.startsWith("myhome/cover_calibration/")).length, 0);
});

test("the second read finds the session when the first one fails", async () => {
  let reads = 0;
  const session = { entry_id: "one", entity_id: "cover.bedroom", session_id: "s", mode: "geometry", phase: "briefing", step: "home", sequence: 2,
    values: {}, recoverable: true, attached: true, owner_present: true };
  const { tab } = await mount({ callWS: (message) => {
    if (message.type === "myhome/cover_profiles/read") { reads++; if (reads === 1) throw new Error("lost"); return { revision: 7, calibration: session }; }
    return undefined;
  } });
  assert.equal(row(tab, "cover.bedroom").querySelector(".cal-badge").dataset.status, "running");
  assert.ok(tab.querySelector(".cal-tab-busy"));
});

test("the summary uses singular forms for one cover", async () => {
  const data = overview();
  data.covers = [data.covers[0], data.covers[4]];
  const { tab } = await mount({ data, language: "it" });
  assert.equal(tab.querySelector(".cal-tab-summary").textContent, "1 tapparella · 1 calibrata · 0 da calibrare");
});

test("a click anywhere on a row opens and closes its details, never from its buttons", async () => {
  const { tab } = await mount();
  row(tab, "cover.living_one").querySelector(".cal-row-profile").click();
  assert.equal(row(tab, "cover.living_one").querySelector(".cal-row-details").hidden, false);
  row(tab, "cover.living_one").querySelector('[data-action="cal-tab-menu"]').click();
  assert.equal(row(tab, "cover.living_one").querySelector(".cal-row-details").hidden, false, "the menu button keeps its own action");
  row(tab, "cover.living_one").querySelector(".cal-row-status").click();
  assert.equal(row(tab, "cover.living_one").querySelector(".cal-row-details").hidden, true);
});

test("each row names its room and bus address as the device cards do", async () => {
  const { tab } = await mount({ language: "it" });
  const address = row(tab, "cover.living_one").querySelector(".cal-row-address");
  assert.equal(address.textContent.replace(/\s+/g, " ").trim(), "Indirizzo: 91 · A: 9 · PL: 1");
  // Responsive map §3.3: units that wrap only between them, the «·» with the unit before it; no «…» anywhere.
  assert.deepEqual([...row(tab, "cover.living_one").querySelectorAll(".cal-row-name > .muted > span")].map((unit) => [unit.className, unit.textContent.replace(/\u00a0/g, " ")]),
    [["cal-row-unit", "Living room ·"], ["cal-row-address", "Indirizzo: 91 · A: 9 · PL: 1"]]);
  const css = (await import("node:fs")).readFileSync(new URL("../../custom_components/myhome/frontend/panel/myhome-panel.css", import.meta.url), "utf8");
  assert.match(css, /\.cal-row-address \{ white-space: nowrap; \}/);
  assert.doesNotMatch(css, /\.cal-row-name > \.muted \{[^}]*ellipsis/);
  assert.deepEqual([...address.querySelectorAll(".cal-row-address-label")].map((item) => item.textContent), ["Indirizzo:", "A:", "PL:"]);
  assert.match(row(tab, "cover.living_one").querySelector(".cal-row-name .muted").textContent, /^Living room\u00a0· Indirizzo/, "D-07: the «·» held to the word before it");
});

test("the row menu holds every secondary action, the same as the expanded details", async () => {
  const { tab } = await mount();
  const menuActions = (id) => [...row(tab, id).querySelectorAll(".cal-menu [role=menuitem]")].map((item) => item.dataset.action);
  assert.deepEqual(menuActions("cover.living_two"), ["cal-tab-refine", "cal-tab-measure-cover", "cal-tab-assign", "cal-tab-toggle"]);
  assert.deepEqual(menuActions("cover.living_one"), ["cal-tab-measure-cover", "cal-tab-assign", "cal-tab-toggle"]);
  assert.match(row(tab, "cover.living_one").querySelector('.cal-menu [data-action="cal-tab-measure-cover"]').textContent, /Recalibrate/);
  assert.match(row(tab, "cover.bedroom").querySelector('.cal-menu [data-action="cal-tab-measure-cover"]').textContent, /Calibrate/);
});


test("every explanatory note of the tab shares one style", async () => {
  const { tab } = await mount();
  row(tab, "cover.living_two").querySelector('[data-action="cal-tab-toggle"]').click();
  const notes = [...tab.querySelectorAll(".cal-note")].map((note) => note.textContent);
  assert.equal(notes.length, 2);
  assert.match(notes[0], /adapted to the curtain travel/);
  assert.match(notes[1], /If the profile is recalibrated/);
  tab.querySelector('[data-action="cal-tab-filter"][data-id="to_measure"]').click();
  assert.ok(tab.querySelector(".cal-tab-help.cal-note"));
});

test("a never measured cover says where its times come from", async () => {
  const data = overview();
  const bedroom = data.covers.find((cover) => cover.entity_id === "cover.bedroom");
  bedroom.effective.opening = timing(25, unknown, "default"); bedroom.effective.closing = timing(25, unknown, "default");
  const { tab } = await mount({ data, language: "it" });
  row(tab, "cover.bedroom").querySelector('[data-action="cal-tab-toggle"]').click();
  assert.equal(row(tab, "cover.bedroom").querySelector(".cal-origin-note").textContent,
    "Valori predefiniti (25 s), mai misurati su questa tapparella: la posizione stimata può essere molto imprecisa. Con una calibrazione la precisione può migliorare sensibilmente.");
  bedroom.effective.opening = timing(30, unknown, "yaml"); bedroom.effective.closing = timing(28, unknown, "yaml");
  const yaml = await mount({ data });
  row(yaml.tab, "cover.bedroom").querySelector('[data-action="cal-tab-toggle"]').click();
  assert.match(row(yaml.tab, "cover.bedroom").querySelector(".cal-origin-note").textContent, /^Values from the YAML configuration \(30\u00a0\/\u00a028 s\), never measured/);
  row(yaml.tab, "cover.living_one").querySelector('[data-action="cal-tab-toggle"]').click();
  assert.equal(row(yaml.tab, "cover.living_one").querySelector(".cal-origin-note"), null, "a measured cover has no such note");
  assert.equal(row(tab, "cover.bedroom").querySelector(".cal-origin-note").classList.contains("is-warning"), false, "blue in a quiet row");
  // In the row of a cover in progress the note is yellow like the row; the icon and the primary action stay accent.
  const session = { entry_id: "one", entity_id: "cover.bedroom", session_id: "s", mode: "geometry", phase: "briefing", step: "lift", sequence: 3,
    values: {}, recoverable: true, attached: true, owner_present: true };
  const running = await mount({ data, session });
  row(running.tab, "cover.bedroom").querySelector('[data-action="cal-tab-toggle"]').click();
  assert.ok(row(running.tab, "cover.bedroom").classList.contains("is-running"));
  assert.equal(row(running.tab, "cover.bedroom").querySelector(".cal-origin-note").classList.contains("is-warning"), true);
});

const order = (tab) => rows(tab).map((item) => item.dataset.entity.replace("cover.", ""));
const sortBy = (tab, key) => tab.querySelector(`[data-action="cal-tab-sort"][data-id="${key}"]`).click();
const arrows = (tab) => [...tab.querySelectorAll(".cal-sort")].map((button) => [button.dataset.id, button.getAttribute("aria-pressed"), button.querySelector("ha-icon")?.getAttribute("icon") || null]);

test("the list opens sorted by name, ascending, with the arrow on Cover", async () => {
  const { tab } = await mount();
  assert.deepEqual(order(tab), ["bedroom", "kitchen_two", "living_one", "living_two"]);
  assert.deepEqual(arrows(tab), [["cover", "true", "mdi:arrow-up"], ["status", "false", null], ["profile", "false", null]]);
  assert.match(tab.querySelector('[data-id="cover"].cal-sort').textContent, /Cover, Ascending/, "readers hear the order");
  sortBy(tab, "cover");
  assert.deepEqual(order(tab), ["living_two", "living_one", "kitchen_two", "bedroom"]);
  assert.deepEqual(arrows(tab)[0], ["cover", "true", "mdi:arrow-down"]);
  sortBy(tab, "cover");
  assert.deepEqual(order(tab), ["bedroom", "kitchen_two", "living_one", "living_two"], "a third click goes back to the default order");
});

test("Status sorts by what needs attention, then reversed, then back to the default order", async () => {
  const { tab } = await mount();
  sortBy(tab, "status");
  assert.deepEqual(order(tab), ["bedroom", "kitchen_two", "living_two", "living_one"], "to calibrate, calibrated (by name), verified (L-73)");
  assert.deepEqual(arrows(tab), [["cover", "false", null], ["status", "true", "mdi:arrow-up"], ["profile", "false", null]]);
  sortBy(tab, "status");
  assert.deepEqual(order(tab), ["living_one", "kitchen_two", "living_two", "bedroom"], "reversed rank, ties still by name");
  assert.equal(arrows(tab)[1][2], "mdi:arrow-down");
  sortBy(tab, "status");
  assert.deepEqual(arrows(tab)[0], ["cover", "true", "mdi:arrow-up"]);
  assert.deepEqual(JSON.parse(window.localStorage.getItem("myhome-panel-calibration-sort-v1")), { key: "cover", dir: "asc" });
});

test("Profile sorts by profile name in both orders, covers without a profile always last", async () => {
  const data = overview();
  data.profiles.push({ id: "attic", name: "attic_one_leaf", opening_time: 20, closing_time: 20, assigned_to: ["cover.living_one"], provenance: {} });
  data.covers[0].profile_id = "attic";
  const { tab } = await mount({ data });
  sortBy(tab, "profile");
  assert.deepEqual(order(tab), ["living_one", "kitchen_two", "living_two", "bedroom"]);
  sortBy(tab, "profile");
  assert.deepEqual(order(tab), ["kitchen_two", "living_two", "living_one", "bedroom"], "the cover without a profile stays at the bottom");
});

test("the order is kept per browser, restored on opening, and applies with the active filter", async () => {
  window.localStorage.setItem("myhome-panel-calibration-sort-v1", JSON.stringify({ key: "status", dir: "desc" }));
  const { tab } = await mount();
  assert.deepEqual(order(tab), ["living_one", "kitchen_two", "living_two", "bedroom"]);
  assert.deepEqual(arrows(tab)[1], ["status", "true", "mdi:arrow-down"]);
  tab.querySelector('[data-action="cal-tab-filter"][data-id="calibrated"]').click();
  assert.deepEqual(order(tab), ["living_one", "kitchen_two", "living_two"], "«Calibrate» counts calibrated and verified (L-73)");
  tab.querySelector('[data-action="cal-tab-filter"][data-id="profile"]').click();
  assert.deepEqual(order(tab), ["kitchen_two", "living_two"], "same rank: by name");
});

test("a stored order that makes no sense falls back to the default", async () => {
  window.localStorage.setItem("myhome-panel-calibration-sort-v1", JSON.stringify({ key: "date", dir: "up" }));
  const { tab } = await mount();
  assert.deepEqual(order(tab), ["bedroom", "kitchen_two", "living_one", "living_two"]);
});

test("a running cover stays on top whatever the column and the order", async () => {
  const session = { entry_id: "one", entity_id: "cover.living_one", session_id: "s", mode: "guided", phase: "paused", reason: "owner_absent",
    sequence: 2, values: {}, recoverable: true, attached: false, owner_present: false };
  const { tab } = await mount({ session });
  assert.equal(order(tab)[0], "living_one");
  for (const key of ["cover", "status", "status", "profile", "profile"]) {
    sortBy(tab, key);
    assert.equal(order(tab)[0], "living_one", `${key} keeps the running cover first`);
  }
});

test("on phones the list has one row of two drop-downs: the filter opens a sheet, the order has six choices", async () => {
  const { tab, panel } = await mount({ language: "it" });
  const compact = tab.querySelector(".cal-tab-compact");
  assert.equal(tab.querySelector(".cal-list ~ .cal-sort-bar, .cal-sort-bar"), null, "no separate Sort by pill in the list");
  const pick = (value) => { const select = tab.querySelector(".cal-tab-compact select[data-sort]"); select.value = value; select.dispatchEvent(new Event("change", { bubbles: true })); };
  assert.equal(compact.querySelector(".cal-compact-sort:not(.cal-compact-filter) > span").textContent, "Ordina:");
  assert.deepEqual([...compact.querySelector("select[data-sort]").options].map((option) => option.textContent),
    ["Tapparella ↑", "Tapparella ↓", "Stato ↑", "Stato ↓", "Profilo ↑", "Profilo ↓"]);
  pick("status:asc");
  assert.deepEqual(order(tab), ["bedroom", "kitchen_two", "living_two", "living_one"]);
  pick("status:desc");
  assert.deepEqual(order(tab), ["living_one", "kitchen_two", "living_two", "bedroom"]);
  assert.equal(tab.querySelector('.cal-tab-compact select[data-sort="order"]').value, "status:desc");
  assert.equal(tab.querySelector('.cal-list-head [data-id="status"] ha-icon').getAttribute("icon"), "mdi:arrow-down", "the headers show the same choice");
  // The filter: a native drop-down whose options carry the counts.
  const filter = () => tab.querySelector(".cal-tab-compact select[data-filter]");
  assert.equal(tab.querySelector(".cal-tab-compact .cal-compact-filter > span").textContent, "Filtro:");
  assert.deepEqual([...filter().options].map((option) => option.textContent), ["Tutte (4)", "Da calibrare (1)", "Calibrate (3)", "Con profilo (2)"]);
  filter().value = "to_measure"; filter().dispatchEvent(new Event("change", { bubbles: true }));
  assert.deepEqual(order(tab), ["bedroom"]);
  assert.equal(filter().value, "to_measure");
  assert.equal(panel.shadowRoot.querySelector("dialog.cal-filter-sheet"), null, "no sheet");
  assert.equal(tab.querySelector('.cal-chip[data-id="to_measure"]').getAttribute("aria-pressed"), "true", "the desktop chips follow");
});

test("the column headers are buttons reached from the keyboard", async () => {
  const { tab } = await mount();
  const head = tab.querySelector(".cal-list-head");
  assert.equal(head.getAttribute("aria-hidden"), null);
  assert.equal(head.getAttribute("aria-label"), "Sort by");
  assert.ok([...head.querySelectorAll(".cal-sort")].every((button) => button.tagName === "BUTTON" && button.type === "button"));
});

test("the sequence list sorts like the tab, and reordering never changes the place in the sequence", async () => {
  const { tab, subscriptions } = await openBatch();
  const names = () => [...tab.querySelectorAll(".cal-batch-row")].map((item) => item.dataset.entity.replace("cover.", ""));
  // Cards at every width (responsive map): no column headers; «Ordina» sorts it, Cover ascending by default.
  assert.equal(tab.querySelector(".cal-batch .cal-list-head"), null);
  assert.equal(tab.querySelector('.cal-batch .cal-compact-sort select[data-sort="order"]').value, "cover:asc");
  tick_(tab, "cover.living_two"); tick_(tab, "cover.bedroom");
  const sort = (value) => { const select = tab.querySelector('.cal-batch .cal-compact-sort select[data-sort="order"]'); select.value = value; select.dispatchEvent(new Event("change", { bubbles: true })); };
  sort("status:asc");
  assert.deepEqual(names(), ["bedroom", "kitchen_two", "living_two", "living_one"], "to calibrate, calibrated, verified (L-73)");
  sort("status:desc");
  assert.deepEqual(names(), ["living_one", "kitchen_two", "living_two", "bedroom"]);
  assert.deepEqual(places(tab).filter(([, place]) => place).sort(), [["bedroom", "2"], ["living_two", "1"]], "the places follow the ticks, not the rows");
  sort("profile:asc");
  assert.deepEqual(names(), ["kitchen_two", "living_two", "bedroom", "living_one"], "covers without a profile last");
  assert.equal(tab.querySelector(".cal-batch .cal-sort-value").textContent, tab.querySelector('.cal-batch .cal-compact-sort select[data-sort="order"]').selectedOptions[0].textContent,
    "U-11: the chip draws the new choice");
  assert.deepEqual(JSON.parse(window.localStorage.getItem("myhome-panel-calibration-sort-v1")), { key: "profile", dir: "asc" }, "one choice shared with the list");
  const select = tab.querySelector('.cal-batch .cal-compact-sort select[data-sort="order"]');
  select.value = "cover:asc"; select.dispatchEvent(new Event("change", { bubbles: true }));
  assert.deepEqual(names(), ["bedroom", "kitchen_two", "living_one", "living_two"], "the phone's Sort by bar works here too");
  tab.querySelector('[data-action="cal-tab-batch-start"]').click(); await tick();
  assert.deepEqual(subscriptions.find((item) => item.request.type === "myhome/cover_calibration/batch_start").request.entity_ids,
    ["cover.living_two", "cover.bedroom"], "the sequence runs in the order of the ticks");
});

test("on phones a cover's details open as a page over the list, closed by its ✕ or Escape", async () => {
  const original = globalThis.matchMedia;
  // Reduced motion: the slide of M5-4 would only delay the checks.
  globalThis.matchMedia = (query) => ({ matches: query.includes("max-width") || query.includes("reduced-motion") });
  try {
    const { tab, root } = await mount({ language: "it" });
    const toggle = () => row(tab, "cover.living_one").querySelector(".cal-row-toggle");
    toggle().click(); await tick();
    const page = tab.querySelector(".cal-page");
    assert.ok(page, "the page opens");
    assert.equal(row(tab, "cover.living_one").querySelector(".cal-row-details").hidden, true, "the row itself stays closed");
    assert.equal(page.getAttribute("role"), "dialog");
    assert.equal(page.querySelector("#cal-page-name").textContent, "Living room shutter 1");
    assert.equal(page.querySelector(".cal-page-title h3").textContent, "Dettagli della calibrazione");
    assert.ok(page.querySelector(".cal-page-title .cal-badge"));
    assert.deepEqual([...page.querySelectorAll(".cal-details-grid h4")].map((h) => h.textContent), ["Valori in uso", "Ultima calibrazione", "Profilo"]);
    const close = page.querySelector(".cal-page-close");
    assert.equal(close.getAttribute("aria-label"), "Chiudi dettagli");
    assert.equal(root.activeElement, close, "the focus goes to the ✕");
    assert.ok(rows(tab).length > 1, "the list stays under the page");
    close.click(); await tick();
    assert.equal(tab.querySelector(".cal-page"), null);
    assert.equal(root.activeElement, toggle(), "back on the row that opened it");
    // The menu's Details opens it too; Escape closes it.
    row(tab, "cover.living_one").querySelector('[data-action="cal-tab-menu"]').click(); await tick();
    row(tab, "cover.living_one").querySelector('.cal-menu [data-action="cal-tab-toggle"]').click(); await tick();
    assert.ok(tab.querySelector(".cal-page"));
    tab.dispatchEvent(new KeyboardEvent("keydown", { key: "Escape", bubbles: true, composed: true })); await tick();
    assert.equal(tab.querySelector(".cal-page"), null);
  } finally { globalThis.matchMedia = original; }
});

/** A `matchMedia` for a window of `width` × `height` (lists of width, height and orientation features; reduced motion). */
function viewport(width, height, { reduce = true } = {}) {
  const feature = (text) => {
    const [, name, value] = text.match(/\(\s*([a-z-]+)\s*:\s*([a-z0-9-]+)\s*\)/) || [];
    const px = parseFloat(value);
    return { "max-width": width <= px, "min-width": width >= px, "max-height": height <= px, "min-height": height >= px,
      orientation: value === (width > height ? "landscape" : "portrait"), "prefers-reduced-motion": reduce, hover: value === "hover" }[name] ?? false;
  };
  return (query) => ({ matches: query.split(",").some((part) => part.split(/\)\s*and\s*\(/).every((one) => feature(`(${one.replace(/^\s*\(|\)\s*$/g, "")})`))) });
}

test("a cover's details open in place outside the phone layout, a landscape phone included (responsive map, D3, D13)", async () => {
  const original = globalThis.matchMedia;
  try {
    for (const [width, height, page] of [[1100, 900, false], [844, 390, false], [667, 375, false], [390, 844, true], [700, 900, true]]) {
      globalThis.matchMedia = viewport(width, height);
      const { tab } = await mount({ language: "it" });
      row(tab, "cover.living_one").querySelector(".cal-row-toggle").click(); await tick();
      assert.equal(!!tab.querySelector(".cal-page"), page, `${width}×${height}: ${page ? "a page" : "in place"}`);
      assert.equal(row(tab, "cover.living_one").querySelector(".cal-row-details").hidden, page);
      tab.querySelector(".cal-page-close")?.click(); await tick();
    }
  } finally { globalThis.matchMedia = original; }
  const css = (await import("node:fs")).readFileSync(new URL("../../custom_components/myhome/frontend/panel/myhome-panel.css", import.meta.url), "utf8");
  assert.match(css, /@media \(min-width: 720px\), \(max-height: 499px\) and \(orientation: landscape\) \{ \.cal-page \{ display: none; \} \}/, "never a page over the sidebar");
  assert.match(css, /@container caltab \(min-width: 700px\) and \(max-width: 1015px\) \{\s*\.cal-details-grid \{ grid-template-columns: repeat\(3, minmax\(0, 1fr\)\); \}/,
    "cards from 700 px of content: the three blocks in one row, equal (Q-3)");
});

test("L-6: a rotation redraws the list for its layout: no card left open in place on the phone, no page outside it", async () => {
  const original = globalThis.matchMedia;
  const settleRotation = async () => { window.dispatchEvent(new Event("orientationchange")); await new Promise((resolve) => setTimeout(resolve, 350)); await tick(); };
  try {
    // Landscape (accordion): a card open in place; then portrait (phone).
    globalThis.matchMedia = viewport(844, 390);
    const { tab } = await mount({ language: "it" });
    const card = () => row(tab, "cover.living_one");
    card().querySelector(".cal-row-toggle").click(); await tick();
    assert.equal(card().classList.contains("is-expanded"), true);
    globalThis.matchMedia = viewport(390, 844);
    await settleRotation();
    assert.equal(tab.querySelectorAll(".cal-row.is-expanded").length, 0, "portrait: no card open in place");
    assert.equal(card().querySelector(".cal-row-details").hidden, true);
    assert.equal(tab.querySelector(".cal-page"), null, "and no page opened by surprise");
    card().querySelector(".cal-row-toggle").click(); await tick();
    assert.ok(tab.querySelector(".cal-page"), "a tap opens the page");
    tab.querySelector(".cal-page-close").click(); await tick();
    assert.equal(tab.querySelector(".cal-page"), null);
    assert.equal(tab.querySelectorAll(".cal-row.is-expanded").length, 0, "closing it lands on a closed card");
    // The reverse: the page open in portrait becomes the card open in place in landscape, and its toggle closes it.
    card().querySelector(".cal-row-toggle").click(); await tick();
    assert.ok(tab.querySelector(".cal-page"));
    globalThis.matchMedia = viewport(844, 390);
    await settleRotation();
    assert.equal(tab.querySelector(".cal-page"), null, "landscape: no page");
    assert.equal(card().classList.contains("is-expanded"), true, "the card is open in place instead");
    card().querySelector(".cal-row-toggle").click(); await tick();
    assert.equal(card().classList.contains("is-expanded"), false, "the accordion toggle works");
  } finally { globalThis.matchMedia = original; }
});

test("L-7b: after a rotation has settled the list is laid out afresh: nothing measured before stays inline", async () => {
  const { tab } = await mount({ language: "it" });
  const card = row(tab, "cover.living_one"), chips = card.querySelector(".cal-row-main > .cal-row-chips");
  chips.style.flex = "0 0 180px"; chips.style.minWidth = "0";
  card.style.setProperty("--cal-col2", "300px"); card.classList.add("is-aligned");
  window.dispatchEvent(new Event("orientationchange"));
  await new Promise((resolve) => setTimeout(resolve, 600)); await tick();
  assert.equal(chips.getAttribute("style"), null, "the chips' measured width from the other layout is gone");
  assert.deepEqual([card.getAttribute("style"), card.classList.contains("is-aligned")], [null, false], "and the card's Q-14 edge");
  assert.equal(tab.getAttribute("style"), null, "the containers are back to the stylesheet's (switched off and on)");
  assert.equal(tab.querySelector(".cal-list").getAttribute("style"), null, "the list's own layer is gone after one frame");
});

test("L-8: the wizard opens with its head at the top of the window, under the bars that cover it, and stays in view", async () => {
  const { revealHead, coveredTop } = await import("../../custom_components/myhome/frontend/panel/panel-calibration-layout.js");
  const original = globalThis.matchMedia, scrollBy = window.scrollBy, height = window.innerHeight;
  const calls = [];
  window.scrollBy = (options) => calls.push(options);
  Object.defineProperty(window, "innerHeight", { value: 900, configurable: true });
  const rect = (top, h = 60) => () => ({ top, bottom: top + h, left: 0, right: 800, width: 800, height: h });
  const make = (headTop, actionsTop = headTop + 120) => {
    const wiz = document.createElement("section"); wiz.className = "wiz";
    wiz.innerHTML = '<header class="wiz-head"></header><div class="wiz-columns"><div class="wiz-actions"></div></div>';
    document.body.append(wiz);
    wiz.querySelector(".wiz-head").getBoundingClientRect = rect(headTop);
    wiz.querySelector(".wiz-actions").getBoundingClientRect = rect(actionsTop, 300);
    return wiz;
  };
  try {
    globalThis.matchMedia = viewport(1300, 900, { reduce: false });
    revealHead(make(1200), { always: true });
    assert.deepEqual(calls.pop(), { top: 1192, behavior: "smooth" }, "opening: the head at the top (8 px under it)");
    revealHead(make(300));
    assert.equal(calls.length, 0, "a new step with the head in view: no scroll (X-2/X-3)");
    revealHead(make(-300));
    assert.deepEqual(calls.pop(), { top: -308, behavior: "smooth" }, "a new step with the head above the window: back to it");
    revealHead(make(1200, 2100), { always: true });
    assert.deepEqual(calls.pop(), { top: 2100 - (900 - 96), behavior: "smooth" }, "a short window: the actions column's top stays in view");
    // A sticky bar at the window's top (Home Assistant's toolbar, the panel's header) is measured and left uncovered.
    const bar = document.createElement("div"); bar.style.position = "sticky"; bar.getBoundingClientRect = rect(0, 64);
    document.body.append(bar);
    const wiz = make(1200);
    document.elementsFromPoint = (x, y) => (y < 64 ? [bar, document.body] : [document.body]);
    try {
      assert.equal(coveredTop(wiz.querySelector(".wiz-head")), 64);
      revealHead(wiz, { always: true });
      assert.deepEqual(calls.pop(), { top: 1200 - 64 - 8, behavior: "smooth" });
    } finally { delete document.elementsFromPoint; }
    globalThis.matchMedia = viewport(1300, 900);
    revealHead(make(1200), { always: true });
    assert.equal(calls.pop().behavior, "auto", "reduced motion: no smooth scroll");
    globalThis.matchMedia = viewport(390, 844);
    revealHead(make(1200), { always: true });
    assert.equal(calls.length, 0, "full screen: the wizard is the window");
  } finally {
    globalThis.matchMedia = original; window.scrollBy = scrollBy;
    Object.defineProperty(window, "innerHeight", { value: height, configurable: true });
  }
});

test("the tab's dialogs are centred from 720 × 500 px of window and sheets below, a landscape phone included (responsive map, D14)", async () => {
  const original = globalThis.matchMedia;
  try {
    for (const [width, height, sheet] of [[1100, 900, false], [900, 700, false], [720, 500, false], [844, 390, true], [390, 844, true]]) {
      globalThis.matchMedia = viewport(width, height);
      const { tab, panel } = await mount();
      row(tab, "cover.living_one").querySelector('[data-action="cal-tab-menu"]').click(); await tick();
      row(tab, "cover.living_one").querySelector('.cal-menu [data-action="cal-tab-assign"]').click(); await tick();
      assert.ok(panel.shadowRoot.querySelector("dialog.cal-dialog")?.open);
      assert.equal(document.documentElement.style.overflow === "hidden", sheet, `${width}×${height}: ${sheet ? "a sheet holds the page" : "centred"}`);
      panel.shadowRoot.querySelector("dialog.cal-dialog .cal-dialog-cancel, dialog.cal-dialog .cal-dialog-close").click(); await tick();
      document.documentElement.style.overflow = "";
    }
  } finally { globalThis.matchMedia = original; }
  const css = (await import("node:fs")).readFileSync(new URL("../../custom_components/myhome/frontend/panel/myhome-panel.css", import.meta.url), "utf8");
  assert.match(css, /@media \(max-width: 719px\), \(max-height: 499px\) \{\s*\/\*[^*]*\*\/\s*dialog\.cal-dialog \{ width: 100%;/, "one rule for the sheets");
  assert.match(css, /@media \(max-height: 499px\) and \(orientation: landscape\) \{\s*dialog\.cal-dialog \{ max-height: calc\(100vh - 16px\); max-height: calc\(100dvh - 16px\); \}/);
  assert.doesNotMatch(css.slice(css.indexOf("/* ---------- WHO 2 · Calibration tab ---------- */")), /1023px|1024px/, "no window rule left at 1024 px");
});

test("on phones an open sheet holds the page under it and lets it go when it closes", async () => {
  const original = globalThis.matchMedia;
  globalThis.matchMedia = (query) => ({ matches: query.includes("max-width") || query.includes("reduced-motion") });
  try {
    const { tab, panel } = await mount();
    document.documentElement.style.overflow = "clip";
    row(tab, "cover.living_one").querySelector('[data-action="cal-tab-menu"]').click(); await tick();
    row(tab, "cover.living_one").querySelector('.cal-menu [data-action="cal-tab-assign"]').click(); await tick();
    const dialog = panel.shadowRoot.querySelector("dialog.cal-dialog");
    assert.ok(dialog?.open);
    assert.equal(document.documentElement.style.overflow, "hidden");
    assert.equal(document.body.style.overflow, "hidden");
    dialog.querySelector(".cal-dialog-cancel, .cal-dialog-close").click(); await tick();
    assert.equal(panel.shadowRoot.querySelector("dialog.cal-dialog"), null);
    assert.equal(document.documentElement.style.overflow, "clip", "the page's own value comes back");
    assert.equal(document.body.style.overflow, "");
  } finally { globalThis.matchMedia = original; document.documentElement.style.overflow = ""; }
});

test("no update rebuilds what is on screen: filters, order, refreshes and the details page are patched in place (M3)", async () => {
  const original = globalThis.matchMedia;
  globalThis.matchMedia = (query) => ({ matches: query.includes("max-width") && !query.includes("reduced-motion") });
  try {
    const { tab, panel, hass } = await mount({ language: "it" });
    const kept = row(tab, "cover.kitchen_two"), list = tab.querySelector(".cal-list");
    // The order: the same row elements, moved.
    const select = tab.querySelector(".cal-tab-compact select[data-sort]"); select.value = "status:desc"; select.dispatchEvent(new Event("change", { bubbles: true }));
    assert.equal(row(tab, "cover.kitchen_two"), kept);
    assert.equal(tab.querySelector(".cal-list"), list);
    // The filter: applying it keeps the rows that stay.
    const filter = tab.querySelector(".cal-tab-compact select[data-filter]");
    filter.value = "calibrated"; filter.dispatchEvent(new Event("change", { bubbles: true })); await tick();
    assert.equal(row(tab, "cover.kitchen_two"), kept);
    // A Home Assistant update (states, the profiles' revision) with nothing new: nothing is replaced.
    const html = tab.innerHTML;
    panel.hass = { ...hass, states: { ...hass.states } }; await tick(6);
    assert.equal(tab.innerHTML, html);
    assert.equal(row(tab, "cover.kitchen_two"), kept);
    // The details page under a refresh: the same page and body (its scroll stays).
    kept.querySelector(".cal-row-toggle").click(); await tick();
    const page = tab.querySelector(".cal-page"), body = page.querySelector(".cal-page-body");
    panel._calibrationTab.refresh(); await tick(8);
    assert.equal(tab.querySelector(".cal-page"), page);
    assert.equal(tab.querySelector(".cal-page-body"), body);
  } finally { globalThis.matchMedia = original; }
});

test("the sequence view keeps its rows when the order or the selection changes (M3)", async () => {
  const { tab } = await openBatch();
  const kept = tab.querySelector('.cal-batch-row[data-entity="cover.bedroom"]'), view = tab.querySelector(".cal-batch");
  tick_(tab, "cover.bedroom");
  assert.equal(tab.querySelector('.cal-batch-row[data-entity="cover.bedroom"]'), kept);
  const order = tab.querySelector('.cal-batch .cal-compact-sort select[data-sort="order"]');
  order.value = "status:asc"; order.dispatchEvent(new Event("change", { bubbles: true }));
  assert.equal(tab.querySelector('.cal-batch-row[data-entity="cover.bedroom"]'), kept, "a new order moves the same rows");
  assert.equal(tab.querySelector(".cal-batch"), view);
  assert.equal(kept.querySelector("input").checked, true);
});

test("on phones the sequence view has a native «Select ⌄» menu of actions and «Sort ⌄»; its circles are drawn, not native (M4-2, M3-6)", async () => {
  const { tab, panel } = await openBatch();
  const select = () => tab.querySelector(".cal-batch-compact select[data-batch-pick]");
  assert.deepEqual([...select().options].map((option) => [option.value, option.textContent]), [["", "Seleziona"], ["all", "Tutte"], ["to_measure", "Da calibrare"], ["none", "Nessuna"]],
    "actions without counts, under a placeholder");
  assert.ok(tab.querySelector('.cal-batch-compact .cal-compact-sort select[data-sort="order"]'), "the list's own order control");
  const ticked = () => [...tab.querySelectorAll(".cal-batch-row input")].filter((box) => box.checked).map((box) => box.value);
  select().value = "all"; select().dispatchEvent(new Event("change", { bubbles: true }));
  const fromMenu = ticked();
  assert.ok(fromMenu.length > 0);
  assert.equal(select().value, "", "the placeholder comes back");
  assert.equal(panel.shadowRoot.querySelector("dialog"), null, "no sheet");
  batchPick(tab, "none");
  batchPick(tab, "all");
  assert.deepEqual(fromMenu, ticked(), "the same action as the desktop shortcut");
  const css = (await import("node:fs")).readFileSync(new URL("../../custom_components/myhome/frontend/panel/myhome-panel.css", import.meta.url), "utf8");
  assert.match(css, /\.cal-batch-lead input \{ appearance: none;[^}]*border-radius: 50%/);
});

test("focus rings are for the keyboard only; a touch choice lets the drop-down go (M4-3)", async () => {
  const css = (await import("node:fs")).readFileSync(new URL("../../custom_components/myhome/frontend/panel/myhome-panel.css", import.meta.url), "utf8");
  assert.match(css, /-webkit-tap-highlight-color: transparent/);
  assert.match(css, /\.cal-compact-sort:has\(select:focus-visible\)[^{]*\{ outline: 2px solid var\(--panel-accent\)/);
  assert.match(css, /\.cal-compact-sort select:focus,[^{]*\{ outline: none; box-shadow: none;/);
  const original = globalThis.matchMedia;
  globalThis.matchMedia = (query) => ({ matches: query.includes("hover: none") || query.includes("max-width") });
  try {
    const { tab, root } = await mount();
    const select = tab.querySelector(".cal-tab-compact select[data-sort]");
    select.focus();
    select.value = "status:asc"; select.dispatchEvent(new Event("change", { bubbles: true }));
    assert.notEqual(root.activeElement, select);
  } finally { globalThis.matchMedia = original; }
});

test("the scripts use the stylesheet's rules: full screen, page and sheets by window, a landscape phone included (responsive map)", async () => {
  const original = globalThis.matchMedia;
  try {
    // [width, height, wizard full screen (page held), details as a page, dialogs as sheets]
    for (const [width, height, full, page, sheet] of [[1400, 900, false, false, false], [800, 900, false, false, false], [844, 390, true, false, true],
      [667, 375, true, false, true], [390, 844, true, true, true]]) {
      globalThis.matchMedia = viewport(width, height);
      const { layout } = await import("../../custom_components/myhome/frontend/panel/panel-calibration-layout.js");
      assert.deepEqual([layout.fullScreen(), layout.phone(), layout.sheets()], [full, page, sheet], `${width}×${height}`);
      const { tab, panel } = await mount();
      tab.querySelector('[data-action="cal-tab-measure"]').click(); await tick();
      assert.equal(document.documentElement.style.overflow === "hidden", full, `${width}×${height}: the page ${full ? "held" : "free"}`);
      panel._calibrationTab._backToList(); await tick();
      assert.equal(document.documentElement.style.overflow, "");
    }
  } finally { globalThis.matchMedia = original; }
  const css = (await import("node:fs")).readFileSync(new URL("../../custom_components/myhome/frontend/panel/myhome-panel.css", import.meta.url), "utf8");
  const { SHORT, PHONE, FULL_SCREEN, SHEETS } = await import("../../custom_components/myhome/frontend/panel/panel-calibration-layout.js");
  for (const query of [SHORT, PHONE, FULL_SCREEN, SHEETS]) assert.ok(css.includes(`@media ${query} {`), `the stylesheet spells ${query}`);
});

test("on phones the full-screen wizard holds the page under it and lets it go when it closes (M4-4)", async () => {
  const original = globalThis.matchMedia;
  // Reduced motion: the slide of M5-4 would only delay the checks.
  globalThis.matchMedia = (query) => ({ matches: query.includes("max-width") || query.includes("reduced-motion") });
  try {
    const { tab, panel } = await mount();
    document.body.style.overflow = "";
    tab.querySelector('[data-action="cal-tab-measure"]').click(); await tick();
    assert.equal(panel._calibrationTab._mode.kind, "wizard");
    assert.equal(document.documentElement.style.overflow, "hidden");
    assert.equal(document.body.style.overflow, "hidden");
    panel._calibrationTab._backToList(); await tick();
    assert.equal(document.documentElement.style.overflow, "");
    assert.equal(document.body.style.overflow, "");
  } finally { globalThis.matchMedia = original; }
});

test("cards: icon and texts, chips side by side when they fit, action and ⋮; the line under the name always whole (M4-6, Q-13, Q-15)", async () => {
  const css = (await import("node:fs")).readFileSync(new URL("../../custom_components/myhome/frontend/panel/myhome-panel.css", import.meta.url), "utf8");
  assert.match(css, /\.cal-row-main \{ grid-template-columns: minmax\(0, 1fr\) auto auto; grid-template-areas: "cover cover menu" "chips action action";/);
  assert.match(css, /\.cal-row-chips \{ grid-area: chips; display: flex; flex-wrap: wrap;/, "one wrapping row of chips");
  assert.match(css, /\.cal-batch-chips \{ grid-area: status; display: flex; flex-wrap: wrap;/, "the same in A-01 and the sequence");
  assert.match(css, /\.cal-row-chips \{ display: contents; \}/, "the table keeps its status and profile columns");
  assert.match(css, /\.cal-row-action \{ grid-area: action; align-self: center; justify-self: end; white-space: nowrap; \}/);
  const { tab } = await mount();
  assert.deepEqual([...row(tab, "cover.kitchen_two").querySelectorAll(".cal-row-chips > span")].map((part) => part.className), ["cal-row-status", "cal-row-profile"]);
  // Q-13/Q-15: outside the phone the header is icon and texts (filling), the chips, then the action and ⋮; from 900 px
  // it lies on the blocks' three columns. The line under the name is always whole.
  assert.match(css, /\.cal-row-main \{ display: flex; flex-wrap: nowrap; align-items: center; gap: 12px 16px; \}/);
  assert.match(css, /\.cal-row-chips \{ flex: 0 1 auto; min-width: min-content;/, "side by side while they fit, stacked when not");
  // Q-22: the three-column header needs more content than the cards ever have: they keep this header.
  const { CARD_HEADER_COLUMNS, LIST_BANDS } = await import("../../custom_components/myhome/frontend/panel/panel-calibration-layout.js");
  assert.equal(CARD_HEADER_COLUMNS, 1160);
  assert.ok(CARD_HEADER_COLUMNS >= LIST_BANDS.tableFull);
  assert.doesNotMatch(css, /@container caltab \(min-width: 900px\)/);
  assert.equal(row(tab, "cover.kitchen_two").querySelector(".cal-row-address-ap"), null, "no short form");
  assert.deepEqual([...row(tab, "cover.kitchen_two").querySelectorAll(".cal-row-address-part")].map((part) => part.textContent.replace(/\u00a0/g, " ")), ["Address: 91 ·", "A: 9 ·", "PL: 1"],
    "each pair a unit, the «·» with the one before");
  assert.match(css, /\.cal-row-address-part \{ white-space: nowrap; \}/);
  assert.doesNotMatch(css, /is-full/);
});

test("the stylesheet's braces balance (a stray one silently drops every rule after it)", async () => {
  const css = (await import("node:fs")).readFileSync(new URL("../../custom_components/myhome/frontend/panel/myhome-panel.css", import.meta.url), "utf8")
    .replace(/\/\*[\s\S]*?\*\//g, "").replace(/"[^"\n]*"/g, '""');
  let depth = 0;
  for (const char of css) { if (char === "{") depth += 1; else if (char === "}") { depth -= 1; assert.ok(depth >= 0, "no closing brace without its opening one"); } }
  assert.equal(depth, 0);
});

test("the list's bands by content width (1200/1016/700) and the wizard's layouts, no ad-hoc breakpoints (M5-1, Q-3)", async () => {
  const css = (await import("node:fs")).readFileSync(new URL("../../custom_components/myhome/frontend/panel/myhome-panel.css", import.meta.url), "utf8");
  const calibration = css.slice(css.indexOf("/* ---------- WHO 2 · Calibration tab ---------- */"));
  assert.match(calibration, /#cover-calibration-tab \{ container: caltab \/ inline-size; \}/, "the tab's width decides, not the window's");
  const widths = [...calibration.matchAll(/@(media|container caltab|container wizbar|container calblock|container calcards) ([^{]+)\{/g)].flatMap(([, kind, query]) =>
    [...query.matchAll(/(min|max)-width: (\d+)px/g)].map(([, edge, px]) => `${kind === "media" ? "window" : kind.endsWith("wizbar") ? "bar" : kind.endsWith("calblock") ? "block" : kind.endsWith("calcards") ? "cards" : "tab"} ${edge}-${px}`));
  assert.deepEqual([...new Set(widths)].sort(), ["bar min-560", "block min-480", "tab max-1015", "tab max-699", "tab max-999",
    "tab min-1000", "tab min-1016", "tab min-1200", "tab min-700", "window max-719", "window min-720"],
    "the content at 1200/1016/700 (list), 700 and 1000 (wizard); the actions bar at 560; a details block at 480 (pairs in two halves); the filter bar measured (Q-24); the window at 720 (phone, dialogs; with 500 px of height)");
  // Q-3 parity: the script's list bands are the stylesheet's container rules.
  const { LIST_BANDS, listBand } = await import("../../custom_components/myhome/frontend/panel/panel-calibration-layout.js");
  assert.match(calibration, new RegExp(`@container caltab \\(min-width: ${LIST_BANDS.table}px\\) \\{`));
  assert.match(calibration, new RegExp(`@container caltab \\(min-width: ${LIST_BANDS.tableFull}px\\) \\{`));
  assert.match(calibration, new RegExp(`@container caltab \\(max-width: ${LIST_BANDS.tableFull - 1}px\\) \\{`));
  assert.match(calibration, new RegExp(`@container caltab \\(min-width: ${LIST_BANDS.cardsRow}px\\) and \\(max-width: ${LIST_BANDS.tableFull - 1}px\\) \\{`));
  assert.deepEqual([1434, 1200, 1199, 1016, 1015, 700, 699, 366].map(listBand),
    ["table", "table", "table-full", "table-full", "cards-row", "cards-row", "cards-stacked", "cards-stacked"]);
  assert.doesNotMatch(calibration, /container: wizmain/, "the wizard's lists are cards at every width");
  const { tab } = await mount();
  // P-1: in the table the cover, status and profile columns are equal, and the details' blocks sit under them.
  // Q-21: «Tapparella» starts at the names' x (after the chevron, the icon and their two gaps).
  assert.match(calibration, /\.cal-list-head > \.cal-sort-cell:nth-child\(2\) \{ grid-column: 1; grid-row: 1; padding-inline-start: calc\(18px \+ 40px \+ 2 \* var\(--cal-lead-gap\)\); \}/);
  // Q-3/Q-5: three equal columns (cover with its chevron and icon, status, profile), from 1200 px the 158 px actions too.
  assert.match(calibration, /--cal-cols: repeat\(3, minmax\(0, 1fr\)\);/);
  assert.match(calibration, /@container caltab \(min-width: 1200px\) \{\s*(\/\*[\s\S]*?\*\/\s*)?\.cal-list \{ --cal-cols: repeat\(3, minmax\(0, 1fr\)\) max\(158px, var\(--cal-actions-w, 158px\)\); \}/);
  for (const n of [1, 2, 3]) assert.match(calibration, new RegExp(`\\.cal-details-grid > section:nth-child\\(${n}\\) \\{ grid-column: ${n}; \\}`));
  const address = row(tab, "cover.kitchen_two").querySelector(".cal-row-address");
  assert.equal(address.textContent.replace(/\u00a0/g, " "), "Address: 91 · A: 9 · PL: 1", "always whole (Q-15)");
});

test("the sequence's status and Start stay in view while the list scrolls; on cards the place sits at the row's edge (M5-2)", async () => {
  const css = (await import("node:fs")).readFileSync(new URL("../../custom_components/myhome/frontend/panel/myhome-panel.css", import.meta.url), "utf8");
  assert.match(css, /\.wiz\.cal-batch-wiz \{ overflow: clip; \}/, "not a scroll box of its own, or sticky would not stick");
  assert.match(css, /@media \(min-width: 720px\) \{\s*\.cal-batch-sticky \{ position: sticky; top: 76px; \}/);
  assert.match(css, /\.cal-batch-wiz \.cal-batch-order \{ position: absolute; inset-inline-end: 12px;/);
  const { tab } = await openBatch();
  assert.ok(tab.querySelector(".cal-batch .wiz-actions > .cal-batch-sticky > .cal-batch-summary"));
  // Q-28: Start first, the count under it, in every layout (one markup).
  assert.deepEqual([...tab.querySelectorAll(".cal-batch-sticky > *")].map((part) => part.dataset.action || part.className), ["cal-tab-batch-start", "cal-batch-summary"]);
});

test("on phones the full-screen views slide down before the list comes back, at once with reduced motion (M5-4)", async () => {
  const original = globalThis.matchMedia;
  globalThis.matchMedia = (query) => ({ matches: query.includes("max-width") && !query.includes("reduced-motion") });
  try {
    const { tab, panel } = await mount();
    tab.querySelector('[data-action="cal-tab-measure"]').click(); await tick();
    const view = tab.querySelector(":scope > .wiz");
    panel._calibrationTab._backToList();
    assert.ok(view.classList.contains("is-leaving"), "it slides away first");
    assert.equal(panel._calibrationTab._mode.kind, "wizard");
    await new Promise((resolve) => setTimeout(resolve, 260)); await tick();
    assert.equal(panel._calibrationTab._mode.kind, "list");
    globalThis.matchMedia = (query) => ({ matches: query.includes("max-width") || query.includes("reduced-motion") });
    tab.querySelector('[data-action="cal-tab-measure"]').click(); await tick();
    panel._calibrationTab._backToList();
    assert.equal(panel._calibrationTab._mode.kind, "list", "reduced motion: at once");
  } finally { globalThis.matchMedia = original; }
  const css = (await import("node:fs")).readFileSync(new URL("../../custom_components/myhome/frontend/panel/myhome-panel.css", import.meta.url), "utf8");
  assert.match(css, /\.cal-tab > \.wiz, \.cal-page \{ animation: wiz-sheet-in 220ms/);
});

test("the sequence's Steps sheet closes on a tap outside its card (M6-1, R7-7)", async () => {
  const { tab } = await openBatch();
  tab.querySelector('[data-action="cal-tab-batch-steps"]').click();
  const sheet = tab.querySelector(".cal-batch .wiz-sheet");
  assert.equal(sheet.hidden, false);
  sheet.querySelector(".wiz-sheet-card").click();
  assert.equal(sheet.hidden, false);
  sheet.click();
  assert.equal(sheet.hidden, true);
});

test("the full-screen views, their bars and the sheets keep clear of the iOS safe areas (M6-2)", async () => {
  const css = (await import("node:fs")).readFileSync(new URL("../../custom_components/myhome/frontend/panel/myhome-panel.css", import.meta.url), "utf8");
  assert.match(css, /:host \{ --cal-safe-top: env\(safe-area-inset-top, 0px\); --cal-safe-right: env\(safe-area-inset-right, 0px\);\s*--cal-safe-bottom: env\(safe-area-inset-bottom, 0px\); --cal-safe-left: env\(safe-area-inset-left, 0px\);[^}]*\}/);
  for (const rule of [/\.wiz-head \{ padding: calc\(12px \+ var\(--cal-safe-top\)\) calc\(16px \+ var\(--cal-safe-right\)\) 12px calc\(16px \+ var\(--cal-safe-left\)\); \}/,
    /\.wiz-actions \{ padding: 12px calc\(16px \+ var\(--cal-safe-right\)\) calc\(12px \+ var\(--cal-safe-bottom\)\) calc\(16px \+ var\(--cal-safe-left\)\);/,
    /\.cal-page-head \{[^}]*padding: calc\(16px \+ var\(--cal-safe-top\)\) calc\(16px \+ var\(--cal-safe-right\)\) 16px calc\(16px \+ var\(--cal-safe-left\)\);/,
    /\.wiz-sheet \.wiz-sheet-card \{ padding: 0 var\(--cal-safe-right\) var\(--cal-safe-bottom\) var\(--cal-safe-left\); \}/,
    /dialog\.cal-dialog > \* \{ padding-inline: calc\(16px \+ var\(--cal-safe-left\)\) calc\(16px \+ var\(--cal-safe-right\)\); \}/]) assert.match(css, rule);
  assert.doesNotMatch(css.slice(css.indexOf("/* ---------- WHO 2 · Calibration tab ---------- */")).replace(/:host \{[^}]*\}/, ""), /env\(safe-area/, "always through the variables");
});

test("one filter and order bar for the list, A-01 and the sequence: chips and «Ordina per» from 640 px, two drop-downs when narrow (P-3)", async () => {
  const { tab, panel } = await mount({ language: "it" });
  const shape = (bar) => [...bar.querySelector(".cal-filterbar-row").children].map((part) => part.classList[0]);
  const list = tab.querySelector(".cal-tab-filters.cal-filterbar");
  assert.deepEqual(shape(list), ["cal-tab-chips", "cal-tab-compact"]);
  assert.deepEqual([...list.querySelectorAll(".cal-tab-compact select")].map((select) => select.hasAttribute("data-filter") ? "filter" : select.dataset.sort), ["filter", "order"]);
  assert.deepEqual([...list.querySelectorAll(".cal-filterbar-sort > span:not(.cal-sort-value)")].map((span) => span.textContent), ["Ordina:", "Ordina per:"], "short when narrow, long when wide");
  const value = list.querySelector(".cal-filterbar-sort > .cal-sort-value");
  assert.equal(value.getAttribute("aria-hidden"), "true", "U-11: the chip's choice is drawn, the drop-down is what is read");
  assert.equal(value.textContent, list.querySelector(".cal-filterbar-sort select").selectedOptions[0].textContent, "the drawn choice is the selected one");
  // The sequence: «Seleziona:» with its three shortcuts as chips, the same «Seleziona ⌄» when narrow.
  const { tab: seq } = await openBatch();
  const batch = seq.querySelector(".cal-batch-filterbar.cal-filterbar");
  assert.deepEqual(shape(batch), ["cal-tab-chips", "cal-tab-compact"]);
  assert.equal(batch.querySelector(".cal-filterbar-label").textContent, "Seleziona:");
  assert.deepEqual([...batch.querySelectorAll(".cal-chip")].map((chip) => [chip.dataset.id, chip.textContent]), [["all", "Tutte"], ["to_measure", "Da calibrare"], ["none", "Nessuna"]]);
  batch.querySelector('.cal-chip[data-id="all"]').click(); await tick();
  assert.ok([...seq.querySelectorAll(".cal-batch-row input")].filter((input) => !input.disabled).every((input) => input.checked), "the chip ticks them all");
  seq.querySelector('.cal-batch-filterbar .cal-chip[data-id="none"]').click(); await tick();
  assert.equal(seq.querySelectorAll(".cal-batch-row input:checked").length, 0);
  const css = (await import("node:fs")).readFileSync(new URL("../../custom_components/myhome/frontend/panel/myhome-panel.css", import.meta.url), "utf8");
  // Q-24: the state is measured (`is-chips`), not a container query; the drop-downs are equal unless the order needs more.
  assert.doesNotMatch(css, /container: calbar/);
  assert.match(css, /\.cal-filterbar\.is-chips \.cal-tab-summary \{ display: none; \}\s*\.cal-filterbar\.is-chips \.cal-filterbar-row \{ display: flex; align-items: center; justify-content: space-between;/,
    "Q-10: chips left, order right, no counts (the chips have them)");
  assert.match(css, /\.cal-tab-compact > \.cal-compact-filter \{ flex: 1 1 0; min-width: 0; \}\s*\.cal-tab-compact > \.cal-filterbar-sort \{ flex: 1 1 0; min-width: max-content; \}/);
  const { fitBars, BAR_GAP } = await import("../../custom_components/myhome/frontend/panel/panel-calibration-layout.js");
  assert.equal(BAR_GAP, 16);
  const measure = (bar, room, chips, sort) => {
    Object.defineProperty(bar, "clientWidth", { configurable: true, get: () => room });
    bar.querySelector(".cal-tab-chips").getBoundingClientRect = () => ({ width: chips });
    bar.querySelector(".cal-tab-compact").getBoundingClientRect = () => ({ width: sort });
    fitBars(bar.parentElement);
    return bar.classList.contains("is-chips");
  };
  assert.equal(measure(list, 640, 430, 194), true, "430 + 16 + 194 = 640: the chips");
  assert.equal(measure(list, 639, 430, 194), false, "one pixel short: the drop-downs");
  assert.equal(list.classList.contains("is-measuring"), false);
  assert.equal(measure(list, 450, 430, 0), true, "Q-27: no order control (a table), only the chips count");
  assert.match(css, /@container caltab \(min-width: 1016px\) \{ \.cal-tab-filters \.cal-filterbar-sort \{ display: none; \} \}/);
  const chipSort = css.match(/\.cal-filterbar\.is-chips \.cal-filterbar-sort \{([^}]*)\}/)?.[1] || "";
  for (const part of ["height: 24px", "padding: 4px 8px 4px 10px", "gap: 6px", "border-radius: 999px", "font-size: 12px", "font-weight: 500"]) assert.ok(chipSort.includes(part), `Q-26 ${part}`);
  for (const part of ["position: relative", "background: var(--panel-surface)", "-webkit-tap-highlight-color: transparent"]) assert.ok(chipSort.includes(part), `U-11 ${part}`);
  const chipSelect = css.match(/\.cal-filterbar\.is-chips \.cal-filterbar-sort select \{([^}]*)\}/)?.[1] || "";
  for (const part of ["position: absolute", "inset: 0", "z-index: 1", "opacity: 0", "-webkit-tap-highlight-color: transparent"]) assert.ok(chipSelect.includes(part), `U-11: the drop-down covers the chip, ${part}`);
  assert.match(css, /\.cal-filterbar\.is-chips \.cal-filterbar-sort > span \{ pointer-events: none; \}/, "U-11: the label takes no taps");
  assert.match(css, /\.cal-filterbar\.is-chips \.cal-filterbar-sort select:is\(:focus, :focus-visible, :active\) \{ outline: none; box-shadow: none; \}/, "U-11: no inner ring");
  assert.match(css, /\.cal-compact-sort:has\(select:focus-visible\) \{ outline: 2px solid var\(--panel-accent\);/, "U-11: the chip's outline, from the keyboard only");
  assert.match(css, /\.cal-filterbar\.is-chips \.cal-filterbar-sort::after \{ content: ""; flex: none; width: 16px; height: 16px;/, "a 16 px chevron");
  assert.ok(list.querySelector(":scope > .cal-tab-summary + .cal-filterbar-row"), "the counts above the drop-downs");
  void panel;
});

test("the phone's details page holds the page under it like the wizard, and lets it go when it closes (P-5)", async () => {
  const original = globalThis.matchMedia;
  globalThis.matchMedia = viewport(390, 844);
  try {
    const { tab } = await mount({ language: "it" });
    document.documentElement.style.overflow = "";
    row(tab, "cover.living_one").querySelector(".cal-row-toggle").click(); await tick();
    assert.ok(tab.querySelector(".cal-page"));
    assert.equal(document.documentElement.style.overflow, "hidden");
    assert.equal(document.body.style.overflow, "hidden");
    assert.equal(document.documentElement.style.overscrollBehaviorY, "none", "no pull to refresh under it");
    tab.querySelector(".cal-page-close").click(); await tick();
    assert.equal(tab.querySelector(".cal-page"), null);
    assert.equal(document.documentElement.style.overflow, "");
    assert.equal(document.body.style.overflow, "");
    assert.equal(document.documentElement.style.overscrollBehaviorY, "");
  } finally { globalThis.matchMedia = original; }
});

test("back from a wizard opened on a cover's details page, the same page opens again", async () => {
  const original = globalThis.matchMedia;
  globalThis.matchMedia = viewport(390, 844);
  try {
    const { tab, panel } = await mount({ language: "it" });
    row(tab, "cover.living_one").querySelector(".cal-row-toggle").click(); await tick();
    tab.querySelector('.cal-page [data-action="cal-tab-measure-cover"]').click(); await tick();
    assert.equal(panel._calibrationTab._mode.kind, "wizard");
    assert.equal(tab.querySelector(".cal-page"), null, "the wizard replaces it");
    panel._calibrationTab._backToList(); await tick();
    assert.equal(tab.querySelector(".cal-page")?.dataset.entity, "cover.living_one");
    tab.querySelector(".cal-page-close").click(); await tick();
    // From the list, a wizard brings no page back.
    tab.querySelector('[data-action="cal-tab-measure"]').click(); await tick();
    panel._calibrationTab._backToList(); await tick();
    assert.equal(tab.querySelector(".cal-page"), null);
  } finally { globalThis.matchMedia = original; }
});

test("the details' pairs are compact: labels in one column, the value 26 px after, two halves side by side in a wide block (Q-2, Q-4, Q-6)", async () => {
  const { tab } = await mount({ language: "it" });
  row(tab, "cover.living_one").querySelector(".cal-row-toggle").click(); await tick();
  const values = row(tab, "cover.living_one").querySelector(".cal-details-grid > section .cal-facts");
  assert.deepEqual([...values.querySelectorAll(":scope > dl.cal-facts-col")].map((half) => half.querySelectorAll("dt").length), [3, 3], "six pairs in two halves of three");
  const css = (await import("node:fs")).readFileSync(new URL("../../custom_components/myhome/frontend/panel/myhome-panel.css", import.meta.url), "utf8");
  assert.match(css, /\.cal-facts \{ display: grid; grid-template-columns: max-content minmax\(0, 1fr\); column-gap: 26px;/);
  // Q-9: titles as the shell's device names (15 px semibold), pairs at 14 px, the phone's page included.
  assert.match(css, /\.cal-details-grid h4 \{[^}]*font-size: 15px; font-weight: 600;/);
  assert.match(css, /\.cal-facts \{[^}]*font-size: 14px; \}/);
  assert.match(css, /\.cal-page \.cal-details-grid h4 \{[^}]*font-size: 15px; font-weight: 600;/);
  assert.match(css, /\.cal-facts-col \{ display: grid; grid-column: 1 \/ -1; grid-template-columns: subgrid;/, "one label column per block");
  assert.match(css, /\.cal-facts dd \{[^}]*text-align: start;/);
  assert.match(css, /@container calblock \(min-width: 480px\) \{\s*\.cal-facts \{ grid-template-columns: repeat\(auto-fill, minmax\(220px, 1fr\)\); column-gap: 32px; \}/);
});

test("an open card with stacked blocks shares one edge between its chips and the pairs' second half (Q-14)", async () => {
  const css = (await import("node:fs")).readFileSync(new URL("../../custom_components/myhome/frontend/panel/myhome-panel.css", import.meta.url), "utf8");
  assert.match(css, /@container caltab \(max-width: 699px\) \{\s*\.cal-row\.is-aligned \.cal-row-toggle \{ flex: 0 0 calc\(var\(--cal-col2\) - 16px\); \}\s*\.cal-row\.is-aligned \.cal-facts \{ grid-template-columns: calc\(var\(--cal-col2\) - 32px\) minmax\(0, 1fr\); column-gap: 32px; \}/);
  // Without a layout (no width) nothing is measured nor set.
  const { tab, panel } = await mount();
  row(tab, "cover.living_one").querySelector(".cal-row-toggle").click(); await tick();
  tab.dataset.band = "cards-stacked";
  panel._calibrationTab._alignCards();
  assert.equal(row(tab, "cover.living_one").classList.contains("is-aligned"), false);
  assert.equal(row(tab, "cover.living_one").getAttribute("style"), null);
});

test("L-17: the success note is green: text and icon in the colour, 8 % behind, 20 % for the border and its bar", async () => {
  const css = (await import("node:fs")).readFileSync(new URL("../../custom_components/myhome/frontend/panel/myhome-panel.css", import.meta.url), "utf8");
  assert.match(css, /\.cal-tab-done, \.cal-note\.is-success \{ border: 1px solid color-mix\(in srgb, var\(--panel-success\) 20%, transparent\); border-inline-start-width: 3px;\s*background: color-mix\(in srgb, var\(--panel-success\) 8%, var\(--panel-surface\)\); color: var\(--panel-success\); \}/);
  assert.match(css, /\[data-theme="dark"\] \.cal-tab-done, \[data-theme="dark"\] \.cal-note\.is-success \{ color: color-mix/);
});

test("L-2: the cards that open in place keep the rows' chevron; only the phone, where a card opens its page, has none", async () => {
  const css = (await import("node:fs")).readFileSync(new URL("../../custom_components/myhome/frontend/panel/myhome-panel.css", import.meta.url), "utf8");
  assert.doesNotMatch(css, /@container caltab \(max-width: 1015px\) \{ \.cal-row-chevron \{ display: none; \} \}/);
  assert.match(css, /@media \(max-width: 719px\) and \(min-height: 500px\), \(max-width: 719px\) and \(orientation: portrait\) \{ \.cal-row-chevron \{ display: none; \} \}/,
    "the phone layout's own query (layout.PHONE)");
  assert.match(css, /\.cal-row-toggle\[aria-expanded="true"\] \.cal-row-chevron \{ transform: rotate\(90deg\); \}/, "the same turn");
});

test("every row and card shows the same 40 px cover icon, the sequence's too (Q-11)", async () => {
  const css = (await import("node:fs")).readFileSync(new URL("../../custom_components/myhome/frontend/panel/myhome-panel.css", import.meta.url), "utf8");
  assert.match(css, /\.cal-row-icon \{ display: inline-grid; place-items: center; width: 40px; height: 40px;/);
  assert.doesNotMatch(css, /\.cal-batch-lead \.cal-row-icon \{/, "no smaller icon in the wizard's lists");
});

test("status, profile and filter chips share one shape: 24 px, 12 px text, their colour at 12 % with a 20 % border (Q-12)", async () => {
  const css = (await import("node:fs")).readFileSync(new URL("../../custom_components/myhome/frontend/panel/myhome-panel.css", import.meta.url), "utf8");
  const rule = css.match(/\.cal-chip, \.cal-badge, \.cal-profile-chip \{([^}]*)\}/)?.[1] || "";
  for (const part of ["min-height: 24px", "padding: 3px 10px", "border: 1px solid color-mix(in srgb, var(--cal-chip) 20%, transparent)",
    "background: color-mix(in srgb, var(--cal-chip) 12%, transparent)", "font-size: 12px", "line-height: 16px"]) assert.ok(rule.includes(part), part);
  assert.match(css, /\.cal-badge\[data-status="calibrated"\] \{ --cal-chip: var\(--panel-accent\); \}/);
  assert.match(css, /\.cal-badge\[data-status="verified"\] \{ --cal-chip: var\(--panel-success\); white-space: nowrap; \}/);
  assert.match(css, /\.cal-badge\[data-status="verified"\]\[data-tone="warning"\] \{ --cal-chip: var\(--panel-warning\);/);
  assert.match(css, /\.cal-profile-chip \{ --cal-chip: var\(--panel-accent\);/);
  assert.match(css, /\.cal-chip\[aria-pressed="true"\] \{ --cal-chip: var\(--panel-accent\); \}/);
});

test("a profile chip is cut at 180 px with its whole name in the tooltip, whole in the details' block (Q-22)", async () => {
  const { tab } = await mount();
  const chip = row(tab, "cover.kitchen_two").querySelector(".cal-row-profile .cal-profile-chip");
  assert.equal(chip.getAttribute("title"), "tall_two_leaf");
  assert.equal(chip.querySelector(".cal-chip-text").textContent, "tall_two_leaf");
  const css = (await import("node:fs")).readFileSync(new URL("../../custom_components/myhome/frontend/panel/myhome-panel.css", import.meta.url), "utf8");
  assert.match(css, /\.cal-profile-chip \{ --cal-chip: var\(--panel-accent\); max-width: 180px; \}/);
  assert.match(css, /\.cal-chip-text \{ min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; \}/);
  assert.match(css, /\.cal-details-grid \.cal-chip-text \{ white-space: normal; overflow-wrap: break-word; \}/, "whole, wrapping after «_» (D-06)");
});

test("the lists in the wizard are cards, with the bar's counts above its drop-downs (R-6)", async () => {
  const { tab } = await openBatch();
  assert.match(tab.querySelector(".cal-batch-filterbar > .cal-tab-summary").textContent, /^\d+ tapparell[ae] · /);
  const css = (await import("node:fs")).readFileSync(new URL("../../custom_components/myhome/frontend/panel/myhome-panel.css", import.meta.url), "utf8");
  assert.match(css, /\.cal-batch-rows \{ display: grid; gap: 12px; \}/, "D-40: no fixed width any more");
  assert.match(css, /\.cal-batch-row \{[^}]*border: 1px solid var\(--panel-border\); border-radius: var\(--cal-btn-radius\); background: var\(--panel-surface\);/);
  assert.match(css, /\.cal-batch-row\.is-beside \{ grid-template-columns: auto minmax\(0, 1fr\) auto; grid-template-areas: "lead name status"; \}/);
  assert.match(css, /\.cal-batch-row\.is-stacked \.cal-batch-chips \{ flex-direction: column; align-items: end; \}/);
});

test("the tab's head: title, «Come funziona ›» under it, «Calibra una tapparella» (primary) then «Calibra in sequenza» (Q-25)", async () => {
  const { tab } = await mount({ language: "it" });
  const head = tab.querySelector(".cal-tab-head");
  assert.equal(head.querySelector(".cal-tab-heading > .cal-tab-title").textContent, "Calibrazione");
  assert.equal(head.querySelector(".cal-tab-heading > .cal-tab-how").textContent, "Come funziona");
  // U-2: the text opens under the whole title row (the buttons keep their place).
  const how = tab.querySelector(".cal-tab-top > .cal-tab-how-text");
  assert.equal(how.hidden, true);
  head.querySelector(".cal-tab-how").click(); await tick();
  assert.equal(tab.querySelector(".cal-tab-top > .cal-tab-how-text").hidden, false);
  assert.equal(tab.querySelector(".cal-tab-how").getAttribute("aria-expanded"), "true");
  assert.ok(tab.querySelector(".cal-tab-top > .cal-tab-head + .cal-tab-how-text"), "after the row, not inside it");
  assert.equal(tab.querySelector(".cal-tab-intro"), null, "no intro paragraph");
  assert.deepEqual([...head.querySelectorAll(".cal-tab-actions > button")].map((button) => [button.dataset.action, button.className, button.textContent.trim()]),
    [["cal-tab-measure", "primary", "Calibra una tapparella"], ["cal-tab-batch", "", "Calibra in sequenza"]]);
  const css = (await import("node:fs")).readFileSync(new URL("../../custom_components/myhome/frontend/panel/myhome-panel.css", import.meta.url), "utf8");
  assert.match(css, /\.cal-tab-how-text \{ max-width: 720px; margin: 0; font-size: 14px;/, "U-3");
  assert.match(css, /\.cal-tab-top \{[^}]*margin: 22px 0 18px; \}/, "32 px above (after the views bar) and below (with the tab's 14 px gap); 24 on the phone");
  // Q-32: the panel's one disclosure: a chevron-down that turns when open, underline only from hover or the keyboard.
  assert.ok(head.querySelector(".cal-tab-how.cal-disclosure-toggle > ha-icon[icon='mdi:chevron-down']"));
  assert.match(css, /\.cal-disclosure\[open\] summary ha-icon, \.cal-disclosure-toggle\[aria-expanded="true"\] ha-icon \{ transform: rotate\(180deg\); \}/);
  assert.match(css, /@media \(hover: hover\) \{ :is\(\.cal-disclosure summary, \.cal-disclosure-toggle\):hover \.cal-disclosure-label \{ text-decoration: underline; \} \}/);
  assert.match(css, /:is\(\.cal-disclosure summary, \.cal-disclosure-toggle\):focus-visible \.cal-disclosure-label \{ text-decoration: underline; \}/);
  assert.match(css, /-webkit-tap-highlight-color: transparent; \}\n\.cal-disclosure summary::-webkit-details-marker/);
  assert.doesNotMatch(css, /\.cal-tab-how::after/);
});

test("with nothing ticked the sequence's Start is a disabled primary (Q-29)", async () => {
  const { tab } = await openBatch();
  const start = tab.querySelector('[data-action="cal-tab-batch-start"]');
  assert.equal(start.disabled, true);
  assert.ok(start.classList.contains("primary"));
});

test("a dialog opened from a row's ⋮ menu closes that menu (U-4)", async () => {
  const { tab, panel } = await mount();
  row(tab, "cover.living_one").querySelector('[data-action="cal-tab-menu"]').click(); await tick();
  assert.equal(row(tab, "cover.living_one").querySelector(".cal-menu").hidden, false);
  row(tab, "cover.living_one").querySelector('.cal-menu [data-action="cal-tab-assign"]').click(); await tick();
  assert.ok(panel.shadowRoot.querySelector("dialog.cal-dialog")?.open);
  assert.equal(row(tab, "cover.living_one").querySelector(".cal-menu").hidden, true);
  panel.shadowRoot.querySelector("dialog.cal-dialog .cal-dialog-close").click(); await tick();
});


test("D-01: in a landscape sheet the maintainer's view keeps its body: compact bars, no second «Chiudi» under Stop", async () => {
  const { readFileSync } = await import("node:fs");
  const css = readFileSync(new URL("../../custom_components/myhome/frontend/panel/myhome-panel.css", import.meta.url), "utf8");
  const short = css.slice(css.indexOf("@media (max-height: 499px) and (orientation: landscape) {\n  dialog.cal-dialog { max-height"));
  const block = short.slice(0, short.indexOf("\n}\n"));
  assert.match(css, /dialog\.cal-dialog \.cal-resume-entity \{ display: none; \}/, "D-18");
  assert.match(css, /dialog\.cal-dialog \.calibration-safety-actions \{ bottom: -4px; padding-block: 8px; \}/, "flush with the sheet's bottom");
  assert.match(css, /dialog\.cal-dialog :is\(\.calibration-safety-actions, \.cal-actions\) button \{ min-height: 40px;/);
  // D-17/N-03: in every sheet the main action is held above the bar, in view without scrolling.
  assert.match(css, /dialog\.cal-dialog :is\(#cal-take-control, #cal-reconnect, \.cal-actions\) \{ position: sticky; bottom: 61px; z-index: 1;/);
  const sheets = css.slice(css.indexOf("@media (max-width: 719px), (max-height: 499px) {"));
  assert.ok(sheets.indexOf("#cal-take-control, #cal-reconnect") < sheets.indexOf("\n}\n"), "a rule of every sheet, portrait included");
});

test("D-08: an overview that cannot be read shows its error, not an empty filter with zero counts", async () => {
  const { tab } = await mount({ callWS: async (message) => { if (message.type === "myhome/cover_profiles/overview") throw new Error("boom"); } });
  await tick(6);
  assert.ok(tab.querySelector(".notice.error[role=alert]"), "the error");
  assert.ok(tab.querySelector('[data-action="cal-tab-refresh"]'), "and a way to read it again");
  assert.equal(tab.querySelector(".cal-tab-filters"), null, "no filter bar with zero counts");
  assert.equal(tab.querySelector(".cal-tab-no-match"), null, "no «nothing matches the filter»");
  const { readFileSync } = await import("node:fs");
  const css = readFileSync(new URL("../../custom_components/myhome/frontend/panel/myhome-panel.css", import.meta.url), "utf8");
  assert.match(css, /\.cal-tab \[data-action="cal-tab-refresh"\] \{ justify-self: start; width: fit-content; \}/, "N-08: «Aggiorna» at its own width");
});

test("D-03: from 1016 to 1199 the profile chip keeps clear of the CTA and ⋮ sharing its column", async () => {
  const { readFileSync } = await import("node:fs");
  const css = readFileSync(new URL("../../custom_components/myhome/frontend/panel/myhome-panel.css", import.meta.url), "utf8");
  const table = css.slice(css.indexOf("@container caltab (min-width: 1016px) {\n  /* One grid"));
  assert.match(table, /\.cal-row-profile \{ min-width: 0; max-width: calc\(100% - max\(158px, var\(--cal-actions-w, 158px\)\) - 12px\); \}\s*\.cal-row-profile \.cal-profile-chip \{ max-width: min\(180px, 100%\); \}/);
  const wide = css.slice(css.indexOf("@container caltab (min-width: 1200px) {"));
  assert.match(wide.slice(0, wide.indexOf("\n}\n")), /\.cal-row-profile \{ max-width: none; \}/, "with its own actions column the profile has the whole column");
});

test("D-13: the tab's dialogs follow its theme; a head action off while the list loads is a passing state", async () => {
  const { root, tab } = await mount();
  assert.equal(root.getElementById("dialog-host").dataset.theme, tab.dataset.theme, "the dialogs follow the tab's theme");
  assert.equal(tab.querySelector('[data-action="cal-tab-measure"]').classList.contains("is-settling"), false, "loaded: no passing state");
});

test("D-14: native radios and drop-downs follow the theme (color-scheme)", async () => {
  const { readFileSync } = await import("node:fs");
  const css = readFileSync(new URL("../../custom_components/myhome/frontend/panel/myhome-panel.css", import.meta.url), "utf8");
  assert.match(css, /\[data-theme="dark"\] \{ color-scheme: dark; \}\s*\[data-theme="light"\] \{ color-scheme: light; \}/);
});

test("D-05/D-06: a long profile name stays inside its note, and the details' chip wraps only after «_», «-» or «.»", async () => {
  const { readFileSync } = await import("node:fs");
  const css = readFileSync(new URL("../../custom_components/myhome/frontend/panel/myhome-panel.css", import.meta.url), "utf8");
  assert.match(css, /\.cal-note \{ overflow-wrap: anywhere; \}/);
  assert.match(css, /\.cal-details-grid \.cal-chip-text \{ white-space: normal; overflow-wrap: break-word; \}/);
  const data = overview();
  data.profiles[0].name = "garden_side_profile-north.v2";
  const { tab } = await mount({ data });
  const chip = [...tab.querySelectorAll(".cal-row-profile .cal-chip-text")].find((item) => item.textContent === "garden_side_profile-north.v2");
  assert.ok(chip, "in the row the name is one run, cut with «…»");
  assert.equal(chip.querySelector("wbr"), null);
  const toggle = chip.closest(".cal-row").querySelector('[data-action="cal-tab-toggle"]'); toggle.click(); await tick();
  const whole = chip.closest(".cal-row").querySelector(".cal-details-grid .cal-chip-text");
  assert.equal(whole.innerHTML, "garden_<wbr>side_<wbr>profile-<wbr>north.<wbr>v2");
});

test("D-19: «In corso · …» is one line like every chip, its text darkened to read on its fill", async () => {
  const { readFileSync } = await import("node:fs");
  const css = readFileSync(new URL("../../custom_components/myhome/frontend/panel/myhome-panel.css", import.meta.url), "utf8");
  assert.match(css, /\.cal-badge\[data-status="running"\] \{ --cal-chip: var\(--panel-warning\); white-space: nowrap; color: color-mix\(in srgb, var\(--panel-warning\) 55%, #000\); \}/);
  assert.match(css, /\[data-theme="dark"\] \.cal-badge\[data-status="running"\] \{ color: var\(--panel-warning\); border-color: var\(--panel-warning\); \}/, "L-63: the full warning colour on a dark theme");
});

test("D-38: the active filter chip's number is the accent mixed towards the text, as on the tabs", async () => {
  const { readFileSync } = await import("node:fs");
  const css = readFileSync(new URL("../../custom_components/myhome/frontend/panel/myhome-panel.css", import.meta.url), "utf8");
  assert.match(css, /\.cal-chip\[aria-pressed="true"\] \.count \{ color: color-mix\(in srgb, var\(--panel-accent\) 70%, var\(--primary-text-color, #182b37\)\); \}/);
});

test("D-07/D-39: one card layout: never wider than the list, the name's column never narrower than a word, the chips end 16 px before the action", async () => {
  const { readFileSync } = await import("node:fs");
  const css = readFileSync(new URL("../../custom_components/myhome/frontend/panel/myhome-panel.css", import.meta.url), "utf8");
  assert.match(css, /\.cal-list-rows \{ display: grid; grid-template-columns: minmax\(0, 1fr\); gap: 12px; \}/);
  assert.match(css, /\.cal-row-toggle \{ flex: 1 1 200px; min-width: min-content; padding: 0; \}/);
  assert.match(css, /\.cal-row-main \.cal-row-profile \{ max-width: 180px; \}/, "the cap as a length on the cell");
  // Stacked chips leave their box as wide as the widest chip (measured: no layout in jsdom, so the boxes are stubbed).
  const { panel } = await mount();
  const host = document.createElement("div"); host.dataset.band = "cards-row";
  host.innerHTML = '<div class="cal-row-main"><div class="cal-row-chips"><span class="cal-row-status"><span class="cal-badge"></span></span><span class="cal-row-profile"><span class="cal-profile-chip"></span></span></div></div>';
  const chips = host.querySelector(".cal-row-chips"), [badge, chip] = host.querySelectorAll(".cal-badge, .cal-profile-chip");
  const rect = (left, width) => ({ left, right: left + width, width, top: 0, bottom: 24, height: 24 });
  Object.defineProperty(chips, "clientWidth", { value: 234 });
  chips.getBoundingClientRect = () => rect(400, 234);
  badge.getBoundingClientRect = () => rect(400, 112); chip.getBoundingClientRect = () => rect(400, 180);
  badge.getClientRects = chip.getClientRects = () => [rect(0, 1)];
  panel._calibrationTab._fitChips(host);
  assert.equal(chips.style.flex, "0 0 180px");
  assert.ok(["0", "0px"].includes(chips.style.minWidth), "let go below the cut chip's whole name");
});

test("D-40: a wizard list's chips sit beside the name while it keeps 200 px, side by side or stacked, under it otherwise", async () => {
  const { fitRows, ROW_NAME_MIN } = await import("../../custom_components/myhome/frontend/panel/panel-calibration-layout.js");
  assert.equal(ROW_NAME_MIN, 200);
  const root = document.createElement("div");
  root.innerHTML = '<div class="cal-batch-rows"><label class="cal-batch-row"><span class="cal-batch-lead"></span><span class="cal-row-name"></span><span class="cal-batch-chips"><span class="badge"></span><span class="chip"></span></span></label></div>';
  document.body.append(root);
  const row = root.querySelector(".cal-batch-row"), [badge, chip] = row.querySelectorAll(".badge, .chip");
  const rect = (width) => ({ width, left: 0, right: width, top: 0, bottom: 24, height: 24 });
  row.querySelector(".cal-batch-lead").getBoundingClientRect = () => rect(72);
  badge.getBoundingClientRect = () => rect(150); chip.getBoundingClientRect = () => rect(180);
  badge.getClientRects = chip.getClientRects = () => [rect(1)];
  const at = (width) => { Object.defineProperty(row, "clientWidth", { value: width, configurable: true }); fitRows(root); return [...row.classList].filter((name) => name !== "cal-batch-row"); };
  // jsdom computes no padding or gap: room = width − 72 − 2 × 12.
  assert.deepEqual(at(800), ["is-beside"], "150 + 8 + 180 leave the name 266 px");
  assert.deepEqual(at(500), ["is-beside", "is-stacked"], "stacked: 180 leave the name 224 px");
  assert.deepEqual(at(400), [], "under the name");
  // D-40 (Y): the name's line (room, address) keeps one line beside the chips: a 400 px line needs 400 px.
  const name = document.createElement("span"); name.className = "cal-row-name"; name.innerHTML = '<span class="muted">Main bedroom · Indirizzo: 32</span>';
  row.querySelector(".cal-row-name").replaceWith(name);
  const createRange = document.createRange.bind(document);
  document.createRange = () => { const range = createRange(); range.getBoundingClientRect = () => rect(400); return range; };
  try { assert.deepEqual(at(800), ["is-beside", "is-stacked"], "366 px beside the two chips is too little for the line; stacked leaves 524"); }
  finally { document.createRange = createRange; }
  root.remove();
});

test("X-1: where the head stacks (phone), «Come funziona» opens right under its link, before the two buttons", async () => {
  const { readFileSync } = await import("node:fs");
  const css = readFileSync(new URL("../../custom_components/myhome/frontend/panel/myhome-panel.css", import.meta.url), "utf8");
  const phone = css.slice(css.indexOf("@media (max-width: 719px) and (min-height: 500px), (max-width: 719px) and (orientation: portrait) {\n  .cal-tab-top"));
  const block = phone.slice(0, phone.indexOf("\n}\n"));
  assert.match(block, /\.cal-tab-head \{ display: contents; \}\s*\.cal-tab-heading \{ order: 1; \}\s*\.cal-tab-how-text \{ order: 2; font-size: 13px; \}\s*\.cal-tab-actions \{ order: 3; \}/);
  const { tab } = await mount();
  const top = tab.querySelector(".cal-tab-top");
  assert.deepEqual([...top.querySelectorAll(":scope > .cal-tab-head > *, :scope > .cal-tab-how-text")].map((item) => [...item.classList].find((name) => name.startsWith("cal-tab-"))),
    ["cal-tab-heading", "cal-tab-actions", "cal-tab-how-text"], "the three parts the stylesheet orders: title and link, buttons, text");
});

test("X-2: a filter's or an order's drop-down leaves every scrolling box where it was (iOS scrolls for its picker)", async () => {
  const { keepScrollOnPick } = await import("../../custom_components/myhome/frontend/panel/panel-calibration-layout.js");
  const outer = document.createElement("div"), host = document.createElement("div");
  host.innerHTML = '<div class="cal-filterbar"><select data-sort><option>a</option><option>b</option></select></div>';
  outer.append(host); document.body.append(outer);
  Object.defineProperty(outer, "scrollHeight", { value: 3000 }); Object.defineProperty(outer, "clientHeight", { value: 800 });
  outer.scrollTop = 900;
  const stop = keepScrollOnPick(host), select = host.querySelector("select");
  select.dispatchEvent(new Event("focusin", { bubbles: true }));
  outer.scrollTop = 1300; // the picker opened: the page moved up
  select.dispatchEvent(new Event("change", { bubbles: true }));
  await new Promise((resolve) => setTimeout(resolve, 400));
  assert.equal(outer.scrollTop, 900, "back where it was");
  outer.scrollTop = 500; // later, the user's own scroll is never undone
  await new Promise((resolve) => setTimeout(resolve, 400));
  assert.equal(outer.scrollTop, 500);
  stop(); outer.remove();
});

test("X-3: choosing a cover in A-01 scrolls nothing: the row only shows as selected", async () => {
  const source = (await import("node:fs")).readFileSync(new URL("../../custom_components/myhome/frontend/panel/panel-calibration-wizard.js", import.meta.url), "utf8");
  assert.doesNotMatch(source, /chosen\.scrollIntoView|is-selected"\)[^;]*scrollIntoView/, "no scroll on a choice");
  assert.match(source, /column\.querySelector\(`\[data-wiz="\$\{focused\}"\]`\)\?\.focus\(\{ preventScroll: true \}\)/, "the actions' focus comes back without scrolling");
});

test("X-6: a sheet opened by a tap focuses its own box (no ring on its ✕); by a key, the browser's choice stays", async () => {
  const { readFileSync } = await import("node:fs");
  const css = readFileSync(new URL("../../custom_components/myhome/frontend/panel/myhome-panel.css", import.meta.url), "utf8");
  assert.match(css, /:is\(dialog\.cal-dialog, \.wiz-dialog-card, \.wiz-sheet-card\):focus \{ outline: none; \}/);
  assert.match(css, /dialog\.cal-dialog :is\(button, select, input\):focus:not\(:focus-visible\) \{ outline: none; \}/);
  const { root, tab } = await mount();
  const open = () => { tab.querySelector('[data-action="cal-tab-menu"]').click(); tab.querySelector('.cal-menu:not([hidden]) [data-action="cal-tab-assign"]').click(); };
  window.dispatchEvent(new Event("pointerdown"));
  open(); await tick();
  const dialog = root.querySelector("#dialog-host dialog.cal-dialog");
  assert.equal(dialog.getAttribute("tabindex"), "-1");
  assert.equal(root.activeElement, dialog, "after a tap: the sheet itself");
  dialog.querySelector(".cal-dialog-close").click(); await tick();
});

test("X-7: a sheet's fade above its buttons is the wizard bars' fade, anchored into the band with no gap", async () => {
  const { readFileSync } = await import("node:fs");
  const css = readFileSync(new URL("../../custom_components/myhome/frontend/panel/myhome-panel.css", import.meta.url), "utf8");
  const sheet = css.match(/dialog\.cal-dialog \.dialog-foot::before \{([^}]*)\}/)?.[1] || "";
  const bar = css.match(/\.wiz-actions::before \{ content: ""; position: absolute; inset-inline: 0; bottom: calc\(100% \+ 1px\);([^}]*)\}/)?.[1] || "";
  assert.match(sheet, /bottom: calc\(100% - 1px\); height: 40px;/);
  const gradient = (rule) => rule.match(/background: [^;]+;/)?.[0];
  assert.equal(gradient(sheet), gradient(bar), "the same gradient");
});

test("N-04: a centred dialog with a long list scrolls only its body: title row and buttons stay in view", async () => {
  const { readFileSync } = await import("node:fs");
  const css = readFileSync(new URL("../../custom_components/myhome/frontend/panel/myhome-panel.css", import.meta.url), "utf8");
  const outside = css.slice(0, css.indexOf("@media (max-width: 719px), (max-height: 499px) {"));
  const rest = css.slice(css.indexOf("/* N-04:"));
  assert.ok(css.indexOf("/* N-04:") > 0);
  assert.match(rest, /^\/\* N-04:[^]*?dialog\.cal-dialog\[open\] \{ display: flex; flex-direction: column; max-height: calc\(100vh - 48px\); max-height: calc\(100dvh - 48px\); overflow: hidden; \}/);
  assert.match(rest, /dialog\.cal-dialog \.cal-dialog-body \{ flex: 1 1 auto; min-height: 0; overflow-y: auto; overscroll-behavior: contain; \}/);
  void outside;
});

test("N-09: on the phone a cover's page with its session open offers its action, as its row does", async () => {
  const original = globalThis.matchMedia;
  globalThis.matchMedia = (query) => ({ matches: query.includes("max-width") || query.includes("reduced-motion") });
  try {
    const session = { entry_id: "one", entity_id: "cover.bedroom", session_id: "s1", mode: "geometry", phase: "briefing", step: "home", sequence: 2, values: {},
      recoverable: true, attached: false, owner_present: true };
    const { tab } = await mount({ session });
    row(tab, "cover.bedroom").querySelector(".cal-row-toggle").click(); await tick();
    const button = tab.querySelector('.cal-page .cal-page-session [data-action="cal-tab-session"]');
    assert.ok(button, "the session's action on the page");
    assert.equal(button.dataset.id, "one");
  } finally { globalThis.matchMedia = original; }
});

test("L-93: the session's action says what it does: «Follow session» for another tab's, «Resume session» for this tab's own", async () => {
  const base = { entry_id: "one", entity_id: "cover.bedroom", session_id: "s1", mode: "geometry", phase: "briefing", step: "home", sequence: 2, values: {},
    recoverable: true, attached: false, owner_present: true };
  for (const [owned, label, notice, it] of [[false, "Follow session", /follow it with «Follow session»/, "Segui sessione"],
    [true, "Resume session", /resume it with «Resume session»/, "Riprendi sessione"]]) {
    // Home Assistant answers `client_owner` for the client named in the read.
    const { tab } = await mount({ callWS: (message) => (message.type === "myhome/cover_profiles/read"
      ? { revision: 7, calibration: { ...base, ...(message.client_id ? { client_owner: owned } : {}) } } : undefined) });
    assert.equal(row(tab, "cover.bedroom").querySelector('[data-action="cal-tab-session"]').textContent, label);
    assert.match(tab.querySelector(".cal-tab-busy").textContent, notice);
    assert.equal(translations.it[owned ? "wiz_T580" : "wiz_T579"], it);
  }
});

test("L-105/L-110: on a cover with its session open «Change profile» / «Assign profile» stays, disabled, in its menu and its details", async () => {
  const session = { entry_id: "one", entity_id: "cover.bedroom", session_id: "s1", mode: "geometry", phase: "briefing", step: "home", sequence: 2, values: {},
    recoverable: true, attached: false, owner_present: true };
  const { tab, root, calls } = await mount({ session });
  const menu = (id) => { row(tab, id).querySelector('[data-action="cal-tab-menu"]').click(); return row(tab, id).querySelector(".cal-menu"); };
  const item = menu("cover.bedroom").querySelector('[data-action="cal-tab-assign"]');
  assert.ok(item, "still in the menu of the cover in progress");
  assert.equal(item.disabled, true, "but off");
  assert.ok(menu("cover.bedroom").querySelector('[data-action="cal-tab-abort"]'), "its cancel is still there");
  row(tab, "cover.bedroom").querySelector(".cal-row-toggle").click(); await tick();
  const block = row(tab, "cover.bedroom").querySelector('.cal-row-details [data-action="cal-tab-assign"]');
  assert.equal(block?.disabled, true, "in its details too, off as Refine is");
  // Even a click that reaches it opens nothing.
  block.disabled = false; block.click(); await tick();
  assert.equal(root.querySelector("#dialog-host dialog"), null);
  assert.equal(calls.filter((message) => message.type === "myhome/cover_profiles/write").length, 0);
  // Another cover keeps it enabled (its dialog says the gateway is reserved and waits, as before).
  assert.equal(menu("cover.living_two").querySelector('[data-action="cal-tab-assign"]').disabled, false);
  const css = (await import("node:fs")).readFileSync(new URL("../../custom_components/myhome/frontend/panel/myhome-panel.css", import.meta.url), "utf8");
  assert.match(css, /\.cal-menu button:disabled, \.cal-menu button:disabled:hover \{ background: transparent; cursor: not-allowed; \}/);
});
test("L-107: a session started, moved on or ended elsewhere reaches the list at once: a burst makes one read", async () => {
  const { subscriptions, calls } = await mount();
  const subscription = subscriptions.find((item) => item.request.type === "myhome/cover_profiles/subscribe" && item.request.entry_id === "one");
  const reads = () => calls.filter((message) => message.type === "myhome/cover_profiles/overview" && message.entry_id === "one").length;
  const before = reads();
  for (let i = 0; i < 3; i++) subscription.callback({ entry_id: "one", revision: 7, kind: "session" });
  await new Promise((resolve) => setTimeout(resolve, 400)); await tick(4);
  assert.equal(reads(), before + 1, "one read, within a quarter of a second, not at the next 15 s refresh");
});

test("L-109: the table's actions column is as wide as its widest row (158 px at least) and its labels never wrap", async () => {
  const { fitActions } = await import("../../custom_components/myhome/frontend/panel/panel-calibration-layout.js");
  const host = document.createElement("div");
  host.innerHTML = `<div class="cal-list">${["Segui sessione", "Calibra"].map((label) => `<div class="cal-row"><span class="cal-row-actions" style="column-gap: 8px">
    <button class="cal-row-action">${label}</button><span class="cal-menu-wrap"></span></span></div>`).join("")}</div>`;
  document.body.append(host);
  const size = (element, width) => { element.getBoundingClientRect = () => ({ width, height: 40 }); element.getClientRects = () => [{}]; };
  const [follow, measure] = host.querySelectorAll(".cal-row-action");
  size(follow, 118); size(measure, 71);
  for (const menu of host.querySelectorAll(".cal-menu-wrap")) size(menu, 40);
  fitActions(host);
  assert.equal(host.querySelector(".cal-list").style.getPropertyValue("--cal-actions-w"), "166px", "118 + 8 + 40");
  host.remove();
  const css = (await import("node:fs")).readFileSync(new URL("../../custom_components/myhome/frontend/panel/myhome-panel.css", import.meta.url), "utf8");
  assert.match(css, /\.cal-list \{ --cal-cols: repeat\(3, minmax\(0, 1fr\)\) max\(158px, var\(--cal-actions-w, 158px\)\); \}/);
  assert.match(css, /\.cal-row-action \{[^}]*white-space: nowrap;/);
  assert.match(css, /\.cal-row-profile \{ min-width: 0; max-width: calc\(100% - max\(158px, var\(--cal-actions-w, 158px\)\) - 12px\); \}/, "1016–1199: the profile keeps clear of the same width");
});

test("Z-1: the details' times carry one decimal, as the wizard's", async () => {
  const data = overview();
  data.covers[0].effective.opening.value = 22;
  const { tab } = await mount({ data });
  const first = row(tab, data.covers[0].entity_id);
  first.querySelector(".cal-row-toggle").click(); await tick();
  const facts = Object.fromEntries([...first.querySelectorAll(".cal-facts div")].map((item) => [item.querySelector("dt").textContent, item.querySelector("dd").textContent]));
  assert.equal(facts["Opening time"], "22.0 s");
});
