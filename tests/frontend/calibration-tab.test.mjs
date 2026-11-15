import assert from "node:assert/strict";
import { after, afterEach, test } from "node:test";
import { JSDOM } from "jsdom";

import { translations } from "../../custom_components/myhome/frontend/panel/panel-translations.js";
import { PANEL_CSS_FILES, panelCss } from "./panel-css.mjs";

const dom = new JSDOM("<!doctype html><html><body></body></html>", { url: "http://localhost/", pretendToBeVisual: true });
for (const key of ["window", "document", "HTMLElement", "customElements", "CustomEvent", "Event", "KeyboardEvent", "MouseEvent", "history"]) {
  globalThis[key] = key === "window" ? dom.window : dom.window[key];
}
dom.window.HTMLDialogElement.prototype.showModal = function () { this.open = true; };
dom.window.HTMLDialogElement.prototype.close = function () { this.open = false; };
await import("../../custom_components/myhome/frontend/panel/myhome-panel.js");
const { coverStatus, statusBadge, timedCovers } = await import("../../custom_components/myhome/frontend/panel/panel-calibration-tab.js");
const { dateTimeText } = await import("../../custom_components/myhome/frontend/panel/panel-calibration-text.js");

const tick = async (count = 3) => { for (let i = 0; i < count; i++) await new Promise((resolve) => setImmediate(resolve)); };
// A cover's details hold the page on every window (a test may end with them open): nothing held leaks.
afterEach(() => { document.body.replaceChildren(); window.localStorage.clear(); window.history.replaceState(null, "", "/");
  for (const element of [document.documentElement, document.body]) { element.style.overflow = ""; element.style.overscrollBehaviorY = ""; } });
after(() => dom.window.close());

const guided = (date, extra = {}) => ({ source: "guided", recorded_at: date, origin_entity_id: null, origin_name: null, inherited: false, ...extra });
const unknown = { source: "unknown", recorded_at: null, origin_entity_id: null, origin_name: null, inherited: false };
const timing = (value, provenance, origin = "override") => ({ value, origin, provenance, scaled: false });

function overview() {
  return { entry_id: "one", revision: 7, capabilities: { profile_management: true, profile_assignment: true },
    profiles: [{ id: "tall", name: "tall_two_leaf", opening_time: 22.4, closing_time: 21.9, assigned_to: ["cover.living_two", "cover.kitchen_two"],
      provenance: { opening: guided("2026-09-29T10:00:00+00:00"), closing: guided("2026-09-29T10:00:00+00:00") } }],
    covers: [
      // Calibrated with its check kept (2 cm: verified, green).
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
/** «Cambia profilo» in the tab's one frame, in the place of what it shows; never a dialog over it. */
const sheetOf = (tab) => tab.querySelector("dialog.cal-flow .cal-assign-page");
const frameOf = (tab) => tab.querySelector("dialog.cal-flow");
const commands = (calls) => calls.filter((message) => message.type.startsWith("myhome/cover_calibration/"));
/** A cover's details in their dialog over the list, on every window. */
const detailsOf = (tab) => tab.querySelector("dialog.cal-flow.cal-page-dialog[open] .cal-page");
/** The details open on their first tab, «Panoramica» (`overview`); «Dettagli» is `details` (`view`: the tab to show). */
const showTab = async (tab, view) => { detailsOf(tab)?.querySelector(`#cal-page-tab-${view}`)?.click(); await tick(); };
const openDetails = async (tab, id, view = "overview") => { row(tab, id).querySelector(".cal-row-toggle").click(); await tick(); if (view !== "overview") await showTab(tab, view); return detailsOf(tab); };
/** Ricalibra from the card through «Controlla il posizionamento» and its answer «Apertura e chiusura complete». */
const recalibrateFromCard = async (tab) => {
  for (const [action, id] of [["cal-tab-check-start"], ["cal-tab-check-answer", "full"], ["cal-tab-measure-cover"]]) {
    tab.querySelector(`.cal-page [data-action="${action}"]${id ? `[data-id="${id}"]` : ""}`).click();
    await tick();
  }
};
const pairsOf = (page) => Object.fromEntries([...page.querySelectorAll(".cal-pairs > div")].map((item) => [item.querySelector("dt").textContent, item.querySelector("dd").textContent]));

test("status comes from the saved evidence and the kept check: to calibrate, calibrated, verified", () => {
  const data = overview();
  const [one, two, kitchen, bedroom] = data.covers;
  assert.deepEqual(coverStatus(one), { precision: "verified", status: "verified", detail: "times", check: 2, rolls: false, last: "2026-10-01T09:00:00+00:00", method: "guided", origin: null });
  assert.deepEqual([coverStatus(two).status, coverStatus(two).detail, coverStatus(two).check], ["calibrated", "rolls", null], "a followed profile carries no check");
  assert.equal(coverStatus(two).method, "inherited");
  assert.equal(coverStatus(two).origin, "Living room shutter 1");
  // Measured times without rolls measured with the tape are a basic estimate (today's times-only profiles too).
  assert.deepEqual([coverStatus(kitchen).status, coverStatus(kitchen).detail, coverStatus(kitchen).precision], ["basic", "times", "basic"]);
  assert.equal(coverStatus({ ...one, check_cm: null }).status, "basic", "measured times without a check");
  assert.deepEqual([coverStatus(two).precision, coverStatus(two).rolls], ["refined", true], "rolls measured with the tape: «Calibrata»");
  // A basic calibration with slats: the slat time of the tap, the neutral rolls `unknown`.
  const base = { ...one, check_cm: null, effective: { ...one.effective, slat_time_s: timing(2.7, guided("2026-11-02T09:00:00+00:00")),
    opening_roll: timing(1, unknown), closing_roll: timing(1, unknown) } };
  assert.deepEqual([coverStatus(base).status, coverStatus(base).detail, coverStatus(base).rolls], ["basic", "slat", false]);
  // Home Assistant's `precision` decides when it is there (the rule above only for a view without it).
  assert.deepEqual([coverStatus({ ...base, precision: "refined" }).status, coverStatus({ ...kitchen, precision: "none" }).status], ["calibrated", "to_measure"]);
  assert.equal(coverStatus({ ...one, precision: "verified" }).status, "verified");
  assert.equal(coverStatus({ ...bedroom, check_cm: 3 }).status, "to_measure", "a check without measured times says nothing");
  assert.equal(coverStatus(bedroom).status, "to_measure");
  assert.equal(coverStatus(bedroom).last, null);
  assert.deepEqual(timedCovers(data).map((cover) => cover.entity_id), ["cover.living_one", "cover.living_two", "cover.kitchen_two", "cover.bedroom"]);
});

test("a check kept to the tenth of a centimetre is still verified, judged on its tenth", () => {
  const [one] = overview().covers;
  // Only the kept gap: without the two heights the tab reads `check_cm` itself (with them, their difference).
  const kept = (cm) => ({ ...one, check_cm: cm, check_measured_cm: null, check_expected_cm: null });
  assert.deepEqual([coverStatus(kept(0.3)).status, coverStatus(kept(0.3)).check], ["verified", 0.3]);
  assert.equal(coverStatus(kept(0)).status, "verified", "no gap at all");
  const t = (key) => ({ calTabStatus_verified: "Verified · {cm} cm" })[key] ?? key;
  // The chip carries its ✓ before the label.
  assert.match(statusBadge(coverStatus(kept(4.0)), t), /data-tone="success"[^>]*><ha-icon icon="mdi:check"[^>]*><\/ha-icon>Verified · 4 cm</);
  assert.match(statusBadge(coverStatus(kept(4.1)), t), /data-tone="warning"[^>]*><ha-icon icon="mdi:check"[^>]*><\/ha-icon>Verified · 4.1 cm</);
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
    [["to_measure", "To calibrate"], ["verified", "Calibrated · 2 cm"], ["calibrated", "Calibrated"]], "three states");
  assert.equal(badge("cover.living_one").dataset.tone, "success", "within the check's threshold: green");
  assert.equal(row(tab, "cover.living_two").querySelector(".cal-profile-chip").textContent, "tall_two_leaf");
  assert.equal(row(tab, "cover.living_one").querySelector(".cal-row-profile").textContent.trim(), "—");
  assert.deepEqual([...tab.querySelectorAll(".cal-list-head > span")].map((cell) => cell.querySelector("button")?.firstChild.textContent ?? cell.textContent),
    ["", "Cover", "Status", "Profile", ""], "no column for the last measurement: its date is in the details");
  assert.equal(tab.querySelector(".cal-row-last"), null);
  assert.equal(row(tab, "cover.living_one").querySelector(".cal-row-last-inline").textContent.replace(/ \d{2}:\d{2}$/, ""), "Calibrated · Oct 1, 2026", "no article; phones show it on its own line under the name");
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
  // «Come» in plain words (its keys are named in the code; «Metodo» is gone).
  for (const value of ["guided", "guided_tape", "automatic", "linked"]) keys.add(`calTabHow_${value}`);
  for (const value of ["unexpected_movement", "external_command", "unexpected_stop", "stopped", "not_delivered", "start_timeout",
    "not_stopped", "stop_queue_full", "invalid_measurement", "travel_timeout", "cover_unavailable"]) keys.add(`calReason_${value}`);
  assert.ok(keys.size > 150, `${keys.size} keys found`);
  for (const key of keys) {
    assert.ok(translations.en[key], `en ${key}`);
    assert.ok(translations.it[key], `it ${key}`);
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

test("no accordion left: no «Expand all» in the table's head, no row open in place, one details' dialog at a time", async () => {
  const { tab } = await mount();
  assert.equal(tab.querySelector(".cal-list-head .cal-expand-all"), null, "no expand-all");
  assert.equal(tab.querySelector(".cal-list-head > .cal-head-lead").children.length, 0, "its cell left empty");
  await openDetails(tab, "cover.bedroom");
  assert.equal(tab.querySelectorAll(".cal-row.is-expanded").length, 0, "the row stays as it is");
  assert.equal(detailsOf(tab).dataset.entity, "cover.bedroom");
  assert.equal(tab.querySelectorAll("dialog.cal-page-dialog").length, 1, "one details' dialog");
  const { readFileSync } = await import("node:fs");
  const source = readFileSync(new URL("../../custom_components/myhome/frontend/panel/panel-calibration-tab.js", import.meta.url), "utf8");
  assert.doesNotMatch(source, /cal-tab-expand-all|_expanded|_details\(|cal-details-grid/, "no in-place details in the code");
});

test("the page does not pull to refresh while the wizard is shown", async () => {
  const { tab } = await mount();
  const value = () => document.documentElement.style.overscrollBehaviorY;
  assert.equal(value(), "");
  tab.querySelector('[data-action="cal-tab-measure"]').click(); await tick();
  assert.equal(value(), "none", "wizard");
  tab.querySelector('.wiz-head [data-wiz="exit"]').click(); await tick();
  assert.equal(value(), "", "back to the list: as before");
});

test("a note is one recipe: light a box (tone 12 %, border 45 % on three sides, 2 px bar), dark the bar only; A-01's chosen row is neutral", async () => {
  const css = panelCss;
  assert.match(css, /:is\(\.cal-note, \.cal-tab \.notice, dialog\.cal-dialog \.notice\) \{ --note-tone: var\(--panel-accent\); --note-text: var\(--panel-accent\);[^}]*border-radius: var\(--cal-r-8\); box-shadow: none; font-size: var\(--cal-fs-14\);[^}]*background: color-mix\(in srgb, var\(--note-tone\) 12%, transparent\);\s*border: 1px solid color-mix\(in srgb, var\(--note-tone\) 45%, transparent\);/, "light: the box, 8 px, 1 px on every side (the trial)");
  assert.match(css, /\[data-theme="dark"\] :is\(\.cal-note, \.cal-tab \.notice, dialog\.cal-dialog \.notice\) \{ background: none; border: 0 solid transparent; border-inline-start: 2px solid var\(--note-tone\);/, "dark: the bar only");
  assert.match(css, /\.wiz-pick-row\.is-selected \{ background: var\(--cal-tint-selected\); \}/, "the chosen row in the selection tint");
});

test("a cover in progress keeps yellow tints in every state of its row (no open state left)", async () => {
  const css = panelCss;
  assert.doesNotMatch(css, /is-expanded|cal-row-details/, "no open row");
  for (const selector of [".cal-row.is-running .cal-row-main:hover"]) {
    // The pointer's tints sit inside `@media (hover: hover)`.
    const rule = css.split("\n").find((line) => line.startsWith(`${selector} {`) || line.startsWith(`@media (hover: hover) { ${selector} {`));
    assert.ok(rule, selector);
    assert.match(rule, /var\(--panel-warning\)/, `${selector} is yellow`);
    assert.doesNotMatch(rule, /accent/, `${selector} has no blue`);
  }
});

test("every radio of the Assign dialog is the same circle, No profile included", async () => {
  const css = panelCss;
  // The trial: 20 px, drawn by the panel, centred on the first line (14/20).
  assert.match(css, /\.wiz-radio input \{ flex: none; box-sizing: border-box; width: var\(--cal-control\); height: var\(--cal-control\);/, "never shrunk by a long description");
  assert.doesNotMatch(css, /\.cal-assign-none input/, "No profile has no circle of its own");
  // Centred on the first line (checked at 1× and 2× in a browser: within half a pixel).
  assert.match(css, /\.wiz-radio \{[^}]*line-height: 20px;/);
  assert.match(css, /\.wiz-radio input \{[^}]*margin: -1px 0 0; padding: 0;\s*appearance: none;/, "the second trial: 1 px up, as seen on the phone");
  assert.match(css, /\.wiz-radio \{ display: flex; gap: 10px; align-items: start; padding: 6px 0; font-size: var\(--cal-fs-14\); line-height: 20px;/, "a 20 px first line");
  assert.match(css, /\.cal-assign-none strong \{ display: flex; align-items: center; gap: 6px; height: 20px; \}/);
});

test("a session in progress marks its cover, blocks new measurements and opens a sequence in the existing view, in its own dialog", async () => {
  const session = { entry_id: "one", entity_id: "cover.bedroom", session_id: "group", mode: "automatic", batch: true, phase: "paused",
    reason: "owner_absent", sequence: 4, values: {}, recoverable: true, attached: false, owner_present: false, cover_index: 0, results: [],
    targets: [{ entity_id: "cover.bedroom", name: "Bedroom" }, { entity_id: "cover.living_one", name: "Living room shutter 1" }],
    next_step: { step: "next_cover", cover_index: 1, entity_id: "cover.living_one" } };
  const { root, tab, subscriptions, calls } = await mount({ session });
  // Another tab's session (this tab does not own it): it is followed.
  // The row's action is one word, «Follow» (its whole label in its title); the notice names it as shown.
  assert.match(tab.querySelector(".cal-tab-busy").textContent, /follow it with «Follow», or cancel it from the ⋮ menu of its row/);
  assert.equal(tab.querySelector('[data-action="cal-tab-measure"]').disabled, true);
  for (const id of ["cover.bedroom", "cover.living_one"]) {
    assert.equal(row(tab, id).querySelector(".cal-badge").dataset.status, "running");
    assert.equal(row(tab, id).querySelector(".cal-badge").textContent, "In progress · paused");
  }
  assert.equal(row(tab, "cover.living_two").querySelector(".cal-badge").dataset.status, "calibrated");
  const open = row(tab, "cover.bedroom").querySelector('[data-action="cal-tab-session"]');
  assert.equal(open.textContent, "Follow");
  assert.equal(open.title, "Follow session");
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
  assert.match(dialog.querySelector("#cal-paused").textContent, /To resume it, take control here: then "Continue" starts/, "a reader is told to take control first");
  assert.ok(tab.querySelector(".cal-list"), "the list stays behind the dialog");
  assert.equal(commands(calls).length, 0);
  // One bar (the view's own Stop and Cancel/Close), closed by its ✕ at every size; no entity id line.
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
  // the tab's title, then a card with the shutter, its title, why, and how it works.
  assert.equal(tab.querySelector(".cal-tab-title").textContent, "Calibrazione");
  assert.equal(tab.querySelector(".cal-tab-empty h4").textContent, "Nessuna tapparella a tempo su questo gateway");
  assert.match(tab.querySelector(".cal-tab-empty > .muted").textContent, /sensore di posizione/);
  assert.equal(tab.querySelector(".cal-tab-empty .cal-empty-how summary").textContent, "Come funziona");
  assert.equal(tab.querySelector(".cal-list"), null);
  assert.equal(tab.querySelector(".cal-filterbar"), null, "no filters for nothing");
});

test("a filter without results says so and keeps the filters", async () => {
  const data = overview();
  data.covers = data.covers.map((cover) => ({ ...cover, profile_id: null }));
  const { tab } = await mount({ data, language: "it" });
  tab.querySelector('.cal-chip[data-id="profile"]').click(); await tick();
  assert.equal(tab.querySelector(".cal-list"), null);
  assert.equal(tab.querySelector(".cal-tab-no-match h4").textContent, "Nessuna tapparella corrisponde al filtro");
  assert.equal(tab.querySelector(".cal-tab-no-match > .muted").textContent, "Cambia o togli il filtro o la ricerca per vedere le altre tapparelle.");
  assert.ok(tab.querySelector(".cal-tab-filters"), "the filters stay, to change it");
  assert.equal(tab.querySelector(".cal-tab-no-match .cal-empty-how"), null, "«Come funziona» only in the head above");
  // The head action stays on (there are covers), and the card offers the way back.
  assert.equal(tab.querySelector('[data-action="cal-tab-measure"]').disabled, false);
  tab.querySelector('[data-action="cal-tab-clear"]').click(); await tick();
  assert.equal(tab.querySelector(".cal-tab-no-match"), null);
  assert.ok(tab.querySelector(".cal-list"));
});

test("a search without results is cleared from the empty list's own button", async () => {
  const { root, tab } = await mount({ language: "it" });
  const search = root.getElementById("search");
  search.value = "zzz"; search.dispatchEvent(new Event("input", { bubbles: true })); await tick();
  assert.ok(tab.querySelector(".cal-tab-no-match"));
  assert.equal(tab.querySelector('[data-action="cal-tab-measure"]').disabled, false, "not off because of the search");
  assert.equal(tab.querySelector('[data-action="cal-tab-clear"]').textContent, "Togli filtro e ricerca");
  assert.equal(tab.querySelector(".cal-tab-no-match h4").textContent, "Nessuna tapparella corrisponde alla ricerca", "only the search hides them");
  const filter = tab.querySelector("select[data-filter]"); filter.value = "to_measure"; filter.dispatchEvent(new Event("change", { bubbles: true })); await tick();
  assert.equal(tab.querySelector(".cal-tab-no-match h4").textContent, "Nessuna tapparella corrisponde al filtro e alla ricerca", "both");
  tab.querySelector('[data-action="cal-tab-clear"]').click(); await tick();
  assert.equal(search.value, "");
  assert.equal(tab.querySelector(".cal-tab-no-match"), null);
});

test("the wizard's lists draw their own boxes, 20 px on every layout: a circle for one choice, a square for several", () => {
  const css = panelCss.replace(/\/\*[\s\S]*?\*\//g, "");
  assert.match(css, /\.cal-batch-lead input \{ appearance: none; -webkit-appearance: none; box-sizing: border-box; flex: none; width: 20px; height: 20px;[^}]*border-radius: 50%;/,
    "drawn, not native; A-01 keeps its round radios");
  assert.match(css, /\.cal-batch-lead input\[type="checkbox"\] \{ border-radius: var\(--cal-r-4\); \}/, "a checkbox is a square, not a radio's circle");
});

test("Italian texts of the tab carry no placeholder", async () => {
  const { tab } = await mount({ language: "it" });
  row(tab, "cover.living_two").querySelector('[data-action="cal-tab-toggle"]').click(); await tick();
  assert.doesNotMatch(tab.textContent, /\{\w+\}|calTab|wiz_T|cal_[A-Z]/);
  await showTab(tab, "details");
  assert.doesNotMatch(tab.textContent, /\{\w+\}|calTab|wiz_T|cal_[A-Z]/);
  assert.match(tab.textContent, /Calibrata · 2 cm/);
  assert.match(tab.textContent, /Collegata al profilo/, "«Come» in plain words");
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

test("a click anywhere on a row opens its details (over the list), never from its buttons", async () => {
  const { tab } = await mount();
  row(tab, "cover.living_one").querySelector('[data-action="cal-tab-menu"]').click(); await tick();
  assert.equal(detailsOf(tab), null, "the menu button keeps its own action");
  row(tab, "cover.living_one").querySelector('[data-action="cal-tab-menu"]').click(); await tick();
  row(tab, "cover.living_one").querySelector(".cal-row-profile").click(); await tick();
  assert.equal(detailsOf(tab)?.dataset.entity, "cover.living_one");
  tab.querySelector(".cal-page-close").click(); await tick();
  row(tab, "cover.living_one").querySelector(".cal-row-status").click(); await tick();
  assert.equal(detailsOf(tab)?.dataset.entity, "cover.living_one", "from the status too");
});

test("each row names its room and bus address as the device cards do", async () => {
  const { tab } = await mount({ language: "it" });
  const address = row(tab, "cover.living_one").querySelector(".cal-row-address");
  assert.equal(address.textContent.replace(/\s+/g, " ").trim(), "Indirizzo: 91 · A: 9 · PL: 1");
  // Units that wrap only between them, the «·» with the unit before it; no «…» anywhere.
  assert.deepEqual([...row(tab, "cover.living_one").querySelectorAll(".cal-row-name > .muted > span")].map((unit) => [unit.className, unit.textContent.replace(/\u00a0/g, " ")]),
    [["cal-row-unit", "Living room ·"], ["cal-row-address", "Indirizzo: 91 · A: 9 · PL: 1"]]);
  const css = panelCss;
  assert.match(css, /\.cal-row-address \{ white-space: nowrap; \}/);
  assert.doesNotMatch(css, /\.cal-row-name > \.muted \{[^}]*ellipsis/);
  assert.deepEqual([...address.querySelectorAll(".cal-row-address-label")].map((item) => item.textContent), ["Indirizzo:", "A:", "PL:"]);
  assert.match(row(tab, "cover.living_one").querySelector(".cal-row-name .muted").textContent, /^Living room\u00a0· Indirizzo/, "the «·» held to the word before it");
});

test("every explanatory note of the tab shares one style; the details' notes are their cards' tails", async () => {
  const { tab } = await mount();
  const page = await openDetails(tab, "cover.living_two", "details");
  const tails = [...page.querySelectorAll(".cal-card-tail")].map((note) => note.textContent);
  // ONE tail, the follower's (the profile named once, in its chip): a profile without a reference travel.
  // Under the table of «Dettagli»; «Panoramica» has none.
  assert.deepEqual(tails, [translations.en.calTabTravelNoReference]);
  await showTab(tab, "overview");
  assert.equal(detailsOf(tab).querySelector(".cal-card-tail"), null);
  tab.querySelector(".cal-page-close").click(); await tick();
  tab.querySelector('[data-action="cal-tab-filter"][data-id="to_measure"]').click();
  assert.ok(tab.querySelector(".cal-tab-help.cal-note"));
});

test("a never measured cover says where its times come from", async () => {
  const data = overview();
  const bedroom = data.covers.find((cover) => cover.entity_id === "cover.bedroom");
  bedroom.effective.opening = timing(25, unknown, "default"); bedroom.effective.closing = timing(25, unknown, "default");
  // In the details' «Valori in uso» card, as its tail.
  const { tab } = await mount({ data, language: "it" });
  assert.equal((await openDetails(tab, "cover.bedroom")).querySelector(".cal-card-tail").textContent,
    "Valori predefiniti (25 s), mai misurati su questa tapparella: la posizione stimata può essere molto imprecisa. Con una calibrazione la precisione può migliorare sensibilmente.");
  bedroom.effective.opening = timing(30, unknown, "yaml"); bedroom.effective.closing = timing(28, unknown, "yaml");
  const yaml = await mount({ data });
  const tails = (page) => [...page.querySelectorAll(".cal-card-tail")].map((item) => item.textContent);
  assert.ok(tails(await openDetails(yaml.tab, "cover.bedroom")).some((text) => /^Values from the YAML configuration \(30\u00a0\/\u00a028 s\), never measured/.test(text)));
  yaml.tab.querySelector(".cal-page-close").click(); await tick();
  assert.ok(!tails(await openDetails(yaml.tab, "cover.living_one")).some((text) => /never measured/.test(text)), "a measured cover has no such note");
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
  assert.deepEqual(order(tab), ["bedroom", "kitchen_two", "living_two", "living_one"], "to calibrate, basic estimate, calibrated, verified");
  assert.deepEqual(arrows(tab), [["cover", "false", null], ["status", "true", "mdi:arrow-up"], ["profile", "false", null]]);
  sortBy(tab, "status");
  assert.deepEqual(order(tab), ["living_one", "living_two", "kitchen_two", "bedroom"], "reversed rank");
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
  assert.deepEqual(order(tab), ["living_one", "living_two", "kitchen_two", "bedroom"]);
  assert.deepEqual(arrows(tab)[1], ["status", "true", "mdi:arrow-down"]);
  tab.querySelector('[data-action="cal-tab-filter"][data-id="calibrated"]').click();
  assert.deepEqual(order(tab), ["living_one", "living_two", "kitchen_two"], "«Calibrate» counts the basic estimates, calibrated and verified");
  tab.querySelector('[data-action="cal-tab-filter"][data-id="profile"]').click();
  assert.deepEqual(order(tab), ["living_two", "kitchen_two"], "calibrated before a basic estimate, descending");
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
  assert.deepEqual(order(tab), ["living_one", "living_two", "kitchen_two", "bedroom"]);
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

test("a cover's details are the one overlay on every window, a landscape phone included: tendina, 480 px, 880 px with two by two", async () => {
  const original = globalThis.matchMedia;
  try {
    for (const [width, height] of [[1100, 900], [844, 390], [667, 375], [390, 844], [700, 900]]) {
      globalThis.matchMedia = viewport(width, height);
      const { tab } = await mount({ language: "it" });
      assert.ok(await openDetails(tab, "cover.living_one"), `${width}×${height}: the details' dialog`);
      tab.querySelector(".cal-page-close")?.click(); await tick();
      assert.equal(detailsOf(tab), null);
    }
  } finally { globalThis.matchMedia = original; }
  const css = panelCss;
  assert.doesNotMatch(css, /\.cal-page \{ display: none; \}/, "no window hides them");
  assert.match(css, /\.cal-flow-box\[data-kind="page"\] \{ height: auto; max-height: min\(86%, 860px\); \}/, "as high as their content");
  // Both tabs two by two from 1024 px (their columns).
  assert.match(css, /\.cal-page-body\.is-wide:not\(\.cal-check\) \{ display: grid; grid-template-columns: repeat\(2, minmax\(0, 1fr\)\);/, "from 1024 px: two by two");
});

test("a rotation keeps the cover's details open, the same dialog, whatever the layout", async () => {
  const original = globalThis.matchMedia;
  const settleRotation = async () => { window.dispatchEvent(new Event("orientationchange")); await new Promise((resolve) => setTimeout(resolve, 350)); await tick(); };
  try {
    globalThis.matchMedia = viewport(844, 390);
    const { tab } = await mount({ language: "it" });
    const page = await openDetails(tab, "cover.living_one");
    globalThis.matchMedia = viewport(390, 844);
    await settleRotation();
    assert.equal(detailsOf(tab), page, "portrait: the same details, not closed nor opened again");
    globalThis.matchMedia = viewport(844, 390);
    await settleRotation();
    assert.equal(detailsOf(tab), page, "landscape: still them");
    tab.querySelector(".cal-page-close").click(); await tick();
    assert.equal(detailsOf(tab), null);
    assert.equal(tab.querySelectorAll(".cal-row.is-expanded").length, 0, "nothing left open in place");
  } finally { globalThis.matchMedia = original; }
});

test("no update rebuilds what is on screen: filters, order, refreshes and the details page are patched in place", async () => {
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

test("focus rings are for the keyboard only; a touch choice lets the drop-down go", async () => {
  const css = panelCss;
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

test("the scripts use the stylesheet's rules: the list's phone layout, the tendina and the medium dialog by width, a landscape phone included", async () => {
  const original = globalThis.matchMedia;
  try {
    // [width, height, the list's phone layout, overlays as the tendina, the medium dialog]; the flow always in its dialog.
    for (const [width, height, phone, sheet, medium] of [[1400, 900, false, false, true], [1024, 700, false, false, true], [800, 900, false, false, false], [844, 390, false, false, false],
      [667, 375, false, false, false], [512, 900, true, false, false], [390, 844, true, true, false]]) {
      globalThis.matchMedia = viewport(width, height);
      const { layout } = await import("../../custom_components/myhome/frontend/panel/panel-calibration-layout.js");
      assert.deepEqual([layout.phone(), layout.sheets(), layout.medium()], [phone, sheet, medium], `${width}×${height}`);
      const { tab, panel } = await mount();
      tab.querySelector('[data-action="cal-tab-measure"]').click(); await tick();
      // The page under the flow is held.
      assert.equal(document.documentElement.style.overflow, "hidden", `${width}×${height}: the page held`);
      assert.ok(tab.querySelector("dialog.cal-flow > .cal-flow-box > .cal-flow-body > .wiz"), `${width}×${height}: in the dialog`);
      assert.equal(tab.querySelector(":scope > .wiz"), null, "never a page of its own");
      assert.equal(tab.querySelector(".wiz-actions").classList.contains("is-footer"), medium, `${width}×${height}: the footer row only in the medium dialog`);
      panel._calibrationTab._backToList(); await tick();
      assert.equal(document.documentElement.style.overflow, "");
    }
  } finally { globalThis.matchMedia = original; }
  const css = panelCss;
  const { PHONE, SHEETS, NARROW, MEDIUM } = await import("../../custom_components/myhome/frontend/panel/panel-calibration-layout.js");
  for (const query of [PHONE, SHEETS, NARROW, MEDIUM]) assert.ok(css.includes(`@media ${query} {`), `the stylesheet spells ${query}`);
  assert.doesNotMatch(css, /@media \(max-height: 499px\) and \(orientation: landscape\) \{/, "no landscape layout of the flows any more");
});

test("on phones the full-screen wizard holds the page under it and lets it go when it closes", async () => {
  const original = globalThis.matchMedia;
  // Reduced motion: the sheet's slide would only delay the checks.
  globalThis.matchMedia = (query) => ({ matches: query.includes("max-width") || query.includes("reduced-motion") });
  try {
    const { tab, panel } = await mount();
    document.body.style.overflow = "";
    tab.querySelector('[data-action="cal-tab-measure"]').click(); await tick();
    assert.equal(panel._calibrationTab._mode.kind, "wizard");
    assert.equal(document.documentElement.style.overflow, "hidden");
    assert.equal(document.body.style.overflow, "", "only the box that scrolls");
    panel._calibrationTab._backToList(); await tick();
    assert.equal(document.documentElement.style.overflow, "");
    assert.equal(document.body.style.overflow, "");
  } finally { globalThis.matchMedia = original; }
});

test("the stylesheet's braces balance (a stray one silently drops every rule after it)", async () => {
  // Each file on its own: a brace left open in the shell's would swallow the calibration's first rules.
  const { readFileSync } = await import("node:fs");
  for (const name of PANEL_CSS_FILES) {
    const css = readFileSync(new URL(`../../custom_components/myhome/frontend/panel/${name}`, import.meta.url), "utf8")
      .replace(/\/\*[\s\S]*?\*\//g, "").replace(/"[^"\n]*"/g, '""');
    let depth = 0;
    for (const char of css) { if (char === "{") depth += 1; else if (char === "}") { depth -= 1; assert.ok(depth >= 0, `${name}: no closing brace without its opening one`); } }
    assert.equal(depth, 0, name);
  }
});

test("the calibration's styles are a stylesheet of their own, linked after the shell's", async () => {
  const { readFileSync } = await import("node:fs");
  const read = (name) => readFileSync(new URL(`../../custom_components/myhome/frontend/panel/${name}`, import.meta.url), "utf8");
  const shell = read("myhome-panel.css"), calibration = read("panel-calibration.css");
  assert.ok(!shell.includes("/* ---------- WHO 2 · Calibration tab ---------- */"), "the tab's section left the shell's stylesheet");
  assert.ok(calibration.includes("/* ---------- WHO 2 · Calibration tab ---------- */") && calibration.includes("/* calibration tokens"));
  const script = read("myhome-panel.js");
  const links = [...script.matchAll(/<link rel="stylesheet" href="\$\{escapeHtml\(assetUrl\("([^"]+)"\)\)\}">/g)].map(([, name]) => name);
  assert.deepEqual(links, PANEL_CSS_FILES, "the shell's first, then the calibration's (its rules come later in the cascade, as before)");
  const { root } = await mount();
  assert.deepEqual([...root.querySelectorAll('link[rel="stylesheet"]')].map((link) => new URL(link.href).pathname.split("/").pop()), PANEL_CSS_FILES);
});

test("the list's bands by content width (640/1016) and the wizard's layouts, no ad-hoc breakpoints", async () => {
  const css = panelCss;
  const calibration = css.slice(css.indexOf("/* ---------- WHO 2 · Calibration tab ---------- */"));
  assert.match(calibration, /#cover-calibration-tab \{ container: caltab \/ inline-size; \}/, "the tab's width decides, not the window's");
  const widths = [...calibration.matchAll(/@(media|container caltab|container wizbar|container calblock|container calcards) ([^{]+)\{/g)].flatMap(([, kind, query]) =>
    [...query.matchAll(/(min|max)-width: (\d+)px/g)].map(([, edge, px]) => `${kind === "media" ? "window" : kind.endsWith("wizbar") ? "bar" : kind.endsWith("calblock") ? "block" : kind.endsWith("calcards") ? "cards" : "tab"} ${edge}-${px}`));
  // No details block (480) nor the old 699 px edge any more: no card opens in place.
  // One table from 1016 px (no 1200 variant any more).
  assert.deepEqual([...new Set(widths)].sort(), ["bar min-560", "tab max-1015", "tab max-639", "tab min-1016", "tab min-640",
    "window max-1023", "window max-511", "window max-719", "window min-1024", "window min-512"],
    "the list's content at 640 and 1016 only; the actions bar at 560; the filter bar measured; the window under 720 (the phone layout, with 500 px of height); the overlays' bands at 511/512 (the tendina, the centred dialog) and 1023/1024 (the medium dialog)");
  // Parity: the script's list bands are the stylesheet's container rules.
  const { LIST_BANDS, listBand } = await import("../../custom_components/myhome/frontend/panel/panel-calibration-layout.js");
  // Exactly two band breakpoints, the script's and the stylesheet's the same numbers.
  assert.deepEqual(LIST_BANDS, { table: 1016, rows: 640 });
  assert.match(calibration, new RegExp(`@container caltab \\(min-width: ${LIST_BANDS.table}px\\) \\{`));
  assert.match(calibration, new RegExp(`@container caltab \\(min-width: ${LIST_BANDS.rows}px\\) and \\(max-width: ${LIST_BANDS.table - 1}px\\) \\{`));
  assert.match(calibration, new RegExp(`@container caltab \\(max-width: ${LIST_BANDS.rows - 1}px\\) \\{`));
  assert.deepEqual([1434, 1016, 1015, 700, 640, 639, 366].map(listBand), ["table", "table", "rows", "rows", "rows", "cards", "cards"]);
  assert.doesNotMatch(calibration, /container: wizmain/, "the wizard's lists are cards at every width");
  const { tab } = await mount();
  // «Tapparella» starts at the names' x (after the icon and its gap; no chevron any more). The columns are the band's
  // one template (calibration-list.test.mjs).
  assert.match(calibration, /\.cal-list-head > \.cal-sort-cell:nth-child\(2\) \{ padding-inline-start: calc\(40px \+ var\(--cal-lead-gap\)\); \}/);
  assert.doesNotMatch(calibration, /cal-details-grid/, "no details under the columns any more");
  const address = row(tab, "cover.kitchen_two").querySelector(".cal-row-address");
  assert.equal(address.textContent.replace(/\u00a0/g, " "), "Address: 91 · A: 9 · PL: 1", "always whole");
});

test("the tendina slides down and the dialog fades away before they go, at once with reduced motion", async () => {
  const original = globalThis.matchMedia;
  try {
    // The phone (390 × 844, motion allowed): the tendina slides down first (220 ms).
    globalThis.matchMedia = viewport(390, 844, { reduce: false });
    const { tab, panel } = await mount();
    tab.querySelector('[data-action="cal-tab-measure"]').click(); await tick();
    const sheet = tab.querySelector("dialog.cal-flow");
    panel._calibrationTab._backToList();
    assert.ok(sheet.classList.contains("is-leaving"), "it slides down first");
    await new Promise((resolve) => setTimeout(resolve, 260)); await tick();
    assert.equal(panel._calibrationTab._mode.kind, "list");
    // The dialog (1400 × 900, motion allowed): a short fade first.
    globalThis.matchMedia = viewport(1400, 900, { reduce: false });
    tab.querySelector('[data-action="cal-tab-measure"]').click(); await tick();
    const flow = tab.querySelector("dialog.cal-flow");
    panel._calibrationTab._backToList();
    assert.ok(flow.classList.contains("is-leaving"), "it fades away first");
    assert.equal(panel._calibrationTab._mode.kind, "wizard");
    await new Promise((resolve) => setTimeout(resolve, 200)); await tick();
    assert.equal(panel._calibrationTab._mode.kind, "list");
    assert.equal(tab.querySelector("dialog.cal-flow"), null);
    globalThis.matchMedia = viewport(1400, 900);
    tab.querySelector('[data-action="cal-tab-measure"]').click(); await tick();
    panel._calibrationTab._backToList();
    assert.equal(panel._calibrationTab._mode.kind, "list", "reduced motion: at once");
  } finally { globalThis.matchMedia = original; }
  const css = panelCss;
  assert.doesNotMatch(css, /\.cal-tab > \.wiz, \.cal-page \{ animation/, "no slide for the pages");
  assert.match(css, /dialog\.cal-flow\.is-leaving \.cal-flow-box \{ animation: cal-flow-out 160ms ease-in forwards; \}/);
  assert.match(css, /@media \(max-width: 511px\) \{[^@]*dialog\.cal-flow\.is-leaving \.cal-flow-box \{ animation: cal-flow-down 220ms/, "the tendina slides down");
  assert.match(css, /@media \(prefers-reduced-motion: reduce\) \{ dialog\.cal-flow \.cal-flow-box, dialog\.cal-flow::backdrop \{ animation: none !important; \} \}/);
});

test("the full-screen views, their bars and the sheets keep clear of the iOS safe areas", async () => {
  const css = panelCss;
  assert.match(css, /:host \{ --cal-safe-top: env\(safe-area-inset-top, 0px\); --cal-safe-right: env\(safe-area-inset-right, 0px\);\s*--cal-safe-bottom: env\(safe-area-inset-bottom, 0px\); --cal-safe-left: env\(safe-area-inset-left, 0px\);[^}]*\}/);
  for (const rule of [/\.wiz-head \{ padding: calc\(12px \+ var\(--cal-safe-top\)\) calc\(16px \+ var\(--cal-safe-right\)\) 12px calc\(16px \+ var\(--cal-safe-left\)\); \}/,
    /\.wiz-actions \{ padding: 12px calc\(16px \+ var\(--cal-safe-right\)\) calc\(12px \+ var\(--cal-safe-bottom\)\) calc\(16px \+ var\(--cal-safe-left\)\);/,
    /\.cal-page-head \{[^}]*padding: calc\(var\(--cal-safe-top\) \+ var\(--cal-head-lift, 0px\)\) calc\(var\(--cal-sp-16\) \+ var\(--cal-safe-right\)\) 0 calc\(var\(--cal-sp-8\) \+ var\(--cal-safe-left\)\);/,
    /\.wiz-sheet \.wiz-sheet-card \{ padding: 0 var\(--cal-safe-right\) var\(--cal-safe-bottom\) var\(--cal-safe-left\); \}/,
    /dialog\.cal-dialog > \* \{ padding-inline: calc\(16px \+ var\(--cal-safe-left\)\) calc\(16px \+ var\(--cal-safe-right\)\); \}/]) assert.match(css, rule);
  assert.doesNotMatch(css.slice(css.indexOf("/* ---------- WHO 2 · Calibration tab ---------- */")).replace(/:host \{[^}]*\}/, ""), /env\(safe-area/, "always through the variables");
});

test("one filter and order bar for the list and A-01: chips and «Ordina per» from 640 px, two drop-downs when narrow", async () => {
  const { tab, panel } = await mount({ language: "it" });
  const shape = (bar) => [...bar.querySelector(".cal-filterbar-row").children].map((part) => part.classList[0]);
  const list = tab.querySelector(".cal-tab-filters.cal-filterbar");
  assert.deepEqual(shape(list), ["cal-tab-chips", "cal-tab-compact"]);
  assert.deepEqual([...list.querySelectorAll(".cal-tab-compact select")].map((select) => select.hasAttribute("data-filter") ? "filter" : select.dataset.sort), ["filter", "order"]);
  assert.deepEqual([...list.querySelectorAll(".cal-filterbar-sort > span:not(.cal-sort-value)")].map((span) => span.textContent), ["Ordina:", "Ordina per:"], "short when narrow, long when wide");
  const value = list.querySelector(".cal-filterbar-sort > .cal-sort-value");
  assert.equal(value.getAttribute("aria-hidden"), "true", "the chip's choice is drawn, the drop-down is what is read");
  assert.equal(value.textContent, list.querySelector(".cal-filterbar-sort select").selectedOptions[0].textContent, "the drawn choice is the selected one");
  const css = panelCss;
  // The state is measured (`is-chips`), not a container query; the drop-downs are equal unless the order needs more.
  assert.doesNotMatch(css, /container: calbar/);
  assert.match(css, /\.cal-filterbar\.is-chips \.cal-tab-summary \{ display: none; \}\s*\.cal-filterbar\.is-chips \.cal-filterbar-row \{ display: flex; align-items: center; justify-content: space-between;/,
    "chips left, order right, no counts (the chips have them)");
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
  assert.equal(measure(list, 450, 430, 0), true, "no order control (a table), only the chips count");
  assert.match(css, /@container caltab \(min-width: 1016px\) \{ \.cal-tab-filters \.cal-filterbar-sort \{ display: none; \} \}/);
  const chipSort = css.match(/\.cal-filterbar\.is-chips \.cal-filterbar-sort \{([^}]*)\}/)?.[1] || "";
  for (const part of ["height: 24px", "padding: 4px 8px 4px 10px", "gap: 6px", "border-radius: var(--cal-r-chip)", "font-size: var(--cal-fs-12)", "font-weight: 500"]) assert.ok(chipSort.includes(part), `the sort chip: ${part}`);
  for (const part of ["position: relative", "background: var(--panel-surface)", "-webkit-tap-highlight-color: transparent"]) assert.ok(chipSort.includes(part), `the sort chip: ${part}`);
  const chipSelect = css.match(/\.cal-filterbar\.is-chips \.cal-filterbar-sort select \{([^}]*)\}/)?.[1] || "";
  for (const part of ["position: absolute", "inset: 0", "z-index: 1", "opacity: 0", "-webkit-tap-highlight-color: transparent"]) assert.ok(chipSelect.includes(part), `the drop-down covers the chip, ${part}`);
  assert.match(css, /\.cal-filterbar\.is-chips \.cal-filterbar-sort > span \{ pointer-events: none; \}/, "the label takes no taps");
  assert.match(css, /\.cal-filterbar\.is-chips \.cal-filterbar-sort select:is\(:focus, :focus-visible, :active\) \{ outline: none; box-shadow: none; \}/, "no inner ring");
  assert.match(css, /\.cal-compact-sort:has\(select:focus-visible\) \{ outline: 2px solid var\(--panel-accent\);/, "the chip's outline, from the keyboard only");
  assert.match(css, /\.cal-filterbar\.is-chips \.cal-filterbar-sort::after \{ content: ""; flex: none; width: 16px; height: 16px;/, "a 16 px chevron");
  assert.ok(list.querySelector(":scope > .cal-tab-summary + .cal-filterbar-row"), "the counts above the drop-downs");
  void panel;
});

test("the phone's details page holds the page under it like the wizard, and lets it go when it closes", async () => {
  const original = globalThis.matchMedia;
  globalThis.matchMedia = viewport(390, 844);
  try {
    const { tab } = await mount({ language: "it" });
    document.documentElement.style.overflow = "";
    row(tab, "cover.living_one").querySelector(".cal-row-toggle").click(); await tick();
    assert.ok(tab.querySelector(".cal-page"));
    assert.equal(document.documentElement.style.overflow, "hidden");
    assert.equal(document.body.style.overflow, "", "only the box that scrolls");
    assert.equal(document.documentElement.style.overscrollBehaviorY, "none", "no pull to refresh under it");
    tab.querySelector(".cal-page-close").click(); await tick();
    assert.equal(tab.querySelector(".cal-page"), null);
    assert.equal(document.documentElement.style.overflow, "");
    assert.equal(document.body.style.overflow, "");
    assert.equal(document.documentElement.style.overscrollBehaviorY, "");
  } finally { globalThis.matchMedia = original; }
});

test("the ✕ of a wizard opened from a cover's page leads to the list, never back to the page", async () => {
  const original = globalThis.matchMedia;
  globalThis.matchMedia = viewport(390, 844);
  try {
    const { tab, panel } = await mount({ language: "it" });
    row(tab, "cover.living_one").querySelector(".cal-row-toggle").click(); await tick();
    await recalibrateFromCard(tab);
    assert.equal(panel._calibrationTab._mode.kind, "wizard");
    assert.equal(tab.querySelector(".cal-page"), null, "the wizard replaces it");
    tab.querySelector(".wiz .wiz-head .wiz-x").click(); await tick();
    assert.equal(panel._calibrationTab._mode.kind, "list");
    assert.equal(tab.querySelector(".cal-page"), null, "the list, not the page");
    assert.ok(tab.querySelector(".cal-list-rows"));
  } finally { globalThis.matchMedia = original; }
});

test("the details' values are label/value rows of 44 px in their cards, the same details on every window", async () => {
  const { tab } = await mount({ language: "it" });
  const page = await openDetails(tab, "cover.living_one");
  const counts = () => [...detailsOf(tab).querySelectorAll(".cal-section")].map((section) => section.querySelectorAll(".cal-pairs > div").length);
  assert.deepEqual(counts(), [0, 2, 0], "Stato, Caratteristiche (the travel, the slats), Profilo");
  assert.ok(page);
  await showTab(tab, "details");
  assert.deepEqual(counts(), [0, 2, 3], "a cover without a profile: its own values (a table), Ultima calibrazione, Ultima verifica (date, where, gap)");
  const css = panelCss;
  assert.match(css, /\.cal-section-title \{[^}]*font-size: var\(--cal-fs-16\); line-height: var\(--cal-lh-16\); font-weight: 700;/);
  assert.match(css, /\.cal-pairs \{ margin: 0; font-size: var\(--cal-fs-14\);/);
  assert.doesNotMatch(css, /\.cal-facts|calblock|is-aligned/, "the in-place blocks' pairs and the old 699 px edge are gone");
});

test("the success note is green: its tone for fill, border and bar, its text in the colour (a lighter tint on dark themes)", async () => {
  const css = panelCss;
  assert.match(css, /:is\(\.cal-note\.is-success, :is\(\.cal-tab, dialog\.cal-dialog\) \.notice:is\(\.cal-tab-done, \[data-tone="success"\]\)\) \{ --note-tone: var\(--panel-success\); --note-text: var\(--panel-success\); \}/);
  assert.match(css, /\[data-theme="dark"\] :is\(\.cal-note\.is-success, [^{]*\) \{ --note-text: color-mix\(in srgb, var\(--panel-success\) 70%/);
});

test("no row opens in place, so no chevron anywhere: every row opens the details' dialog", async () => {
  const css = panelCss;
  assert.doesNotMatch(css, /cal-row-chevron/);
  const { tab } = await mount();
  assert.equal(tab.querySelector(".cal-row-chevron"), null);
  assert.ok(rows(tab).every((item) => item.querySelector(".cal-row-toggle").getAttribute("aria-haspopup") === "dialog"));
});

test("every row and card shows the same 40 px cover icon", async () => {
  const css = panelCss;
  assert.match(css, /\.cal-row-icon \{ display: inline-grid; place-items: center; width: 40px; height: 40px;/);
  assert.doesNotMatch(css, /\.cal-batch-lead \.cal-row-icon \{/, "no smaller icon in the wizard's lists");
});

test("status, profile and filter chips share one shape: 24 px, 12 px text, their colour at 12 % with a 20 % border", async () => {
  const css = panelCss;
  const rule = css.match(/\.cal-chip, \.cal-badge, \.cal-profile-chip \{([^}]*)\}/)?.[1] || "";
  // The colour mixed with the surface, not with transparent: opaque on a tinted row and on a dark card.
  for (const part of ["min-height: 24px", "padding: 3px 10px", "border: 1px solid color-mix(in srgb, var(--cal-chip) 20%, var(--panel-surface))",
    "background: color-mix(in srgb, var(--cal-chip) 12%, var(--panel-surface))", "font-size: var(--cal-fs-12)", "line-height: 16px"]) assert.ok(rule.includes(part), part);
  assert.doesNotMatch(css, /--cal-chip\) \d+%, transparent/, "no chip colour made transparent");
  assert.match(css, /\.wiz-recommended \{ background: color-mix\(in srgb, var\(--panel-accent\) 12%, var\(--panel-surface\)\);/, "the advice badge too");
  // «Stima di base» in the tone of «Calibrata» (it is calibrated).
  assert.match(css, /\.cal-badge:is\(\[data-status="calibrated"\], \[data-status="basic"\], \[data-status="profile"\]\) \{ --cal-chip: var\(--panel-accent\); \}/);
  assert.match(css, /\.cal-badge\[data-status="verified"\] \{ --cal-chip: var\(--panel-success\); white-space: nowrap; \}/);
  assert.match(css, /\.cal-badge\[data-status="verified"\]\[data-tone="warning"\] \{ --cal-chip: var\(--panel-warning\);/);
  assert.match(css, /\.cal-profile-chip \{ --cal-chip: var\(--panel-accent\);/);
  assert.match(css, /\.cal-chip\[aria-pressed="true"\] \{ --cal-chip: var\(--panel-accent\); \}/);
});

test("a profile chip is cut at 180 px with its whole name in the tooltip, whole in the details' block", async () => {
  const { tab } = await mount();
  const chip = row(tab, "cover.kitchen_two").querySelector(".cal-row-profile .cal-profile-chip");
  assert.equal(chip.getAttribute("title"), "tall_two_leaf");
  assert.equal(chip.querySelector(".cal-chip-text").textContent, "tall_two_leaf");
  const css = panelCss;
  assert.match(css, /\.cal-profile-chip \{ --cal-chip: var\(--panel-accent\); max-width: 180px; \}/);
  assert.match(css, /\.cal-chip-text \{ min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; \}/);
  assert.match(css, /\.cal-page \.cal-card-body \.cal-chip-text \{ white-space: normal; overflow-wrap: break-word; \}/, "whole in the details' «Profilo», wrapping after «_»");
});

test("the tab's head: title, «Come funziona ›» under it, «Calibra una tapparella» (primary)", async () => {
  const { tab } = await mount({ language: "it" });
  const head = tab.querySelector(".cal-tab-head");
  assert.equal(head.querySelector(".cal-tab-heading > .cal-tab-title").textContent, "Calibrazione");
  assert.equal(head.querySelector(".cal-tab-heading > .cal-tab-how").textContent, "Come funziona");
  // The text opens under the whole title row (the buttons keep their place).
  const how = tab.querySelector(".cal-tab-top > .cal-tab-how-text");
  assert.equal(how.hidden, true);
  head.querySelector(".cal-tab-how").click(); await tick();
  assert.equal(tab.querySelector(".cal-tab-top > .cal-tab-how-text").hidden, false);
  assert.equal(tab.querySelector(".cal-tab-how").getAttribute("aria-expanded"), "true");
  assert.ok(tab.querySelector(".cal-tab-top > .cal-tab-head + .cal-tab-how-text"), "after the row, not inside it");
  assert.equal(tab.querySelector(".cal-tab-intro"), null, "no intro paragraph");
  assert.deepEqual([...head.querySelectorAll(".cal-tab-actions > button")].map((button) => [button.dataset.action, button.className, button.textContent.trim()]),
    [["cal-tab-measure", "primary", "Calibra una tapparella"]]);
  const css = panelCss;
  assert.match(css, /\.cal-tab-how-text \{ max-width: 720px; margin: 0; font-size: var\(--cal-fs-14\);/, "the text's measure and size");
  assert.match(css, /\.cal-tab-top \{[^}]*margin: 22px 0 18px; \}/, "32 px above (after the views bar) and below (with the tab's 14 px gap); 24 on the phone");
  // The panel's one disclosure: a chevron-down that turns when open, underline only from hover or the keyboard.
  assert.ok(head.querySelector(".cal-tab-how.cal-disclosure-toggle > ha-icon[icon='mdi:chevron-down']"));
  assert.match(css, /\.cal-disclosure\[open\] summary ha-icon, \.cal-disclosure-toggle\[aria-expanded="true"\] ha-icon \{ transform: rotate\(180deg\); \}/);
  assert.match(css, /@media \(hover: hover\) \{ :is\(\.cal-disclosure summary, \.cal-disclosure-toggle\):hover \.cal-disclosure-label \{ text-decoration: underline; \} \}/);
  assert.match(css, /:is\(\.cal-disclosure summary, \.cal-disclosure-toggle\):focus-visible \.cal-disclosure-label \{ text-decoration: underline; \}/);
  assert.match(css, /-webkit-tap-highlight-color: transparent; \}\n\.cal-disclosure summary::-webkit-details-marker/);
  assert.doesNotMatch(css, /\.cal-tab-how::after/);
});

test("the head holds the primary alone, with no way into a sequence; without the wizard the row is not drawn", async () => {
  const { panel, tab } = await mount({ language: "it" });
  assert.equal(tab.querySelector('[data-action="cal-tab-batch"]'), null);
  assert.deepEqual([...tab.querySelectorAll(".cal-tab-actions > *")].map((button) => [button.dataset.action, button.className]),
    [["cal-tab-measure", "primary"]], "the row holds the primary alone: no empty slot beside it");
  assert.doesNotMatch(tab.innerHTML, /cal-tab-batch|Calibra in sequenza/);
  // Without the wizard either, the row is not drawn at all (no empty block under the title).
  panel._calibrationTab.useWizard(null);
  panel._calibrationTab._render();
  assert.equal(tab.querySelector(".cal-tab-actions"), null);
  assert.ok(tab.querySelector(".cal-tab-head > .cal-tab-heading > .cal-tab-how"), "the title and «Come funziona» stay");
});

test("in a landscape sheet the maintainer's view keeps its body: compact bars, no second «Chiudi» under Stop", async () => {
  const { readFileSync } = await import("node:fs");
  const css = panelCss;
  // A short window keeps its band (centred from 512 px), the title row of the hosted view tight.
  const short = css.slice(css.indexOf("@media (max-height: 499px) {\n  dialog.cal-dialog { max-height"));
  const block = short.slice(0, short.indexOf("\n}\n"));
  assert.match(block, /dialog\.cal-dialog:has\(\.calibration-safety-actions\) \.dialog-head \{ padding-block: 8px; \}/);
  assert.match(css, /dialog\.cal-dialog \.cal-resume-entity \{ display: none; \}/, "no entity id line in a sheet");
  assert.match(css, /dialog\.cal-dialog \.calibration-safety-actions \{ bottom: -4px; padding-block: 8px; \}/, "flush with the sheet's bottom");
  assert.match(css, /dialog\.cal-dialog :is\(\.calibration-safety-actions, \.cal-actions\) button \{ min-height: 40px;/);
  // In every sheet the main action is held above the bar, in view without scrolling.
  assert.match(css, /dialog\.cal-dialog :is\(#cal-take-control, #cal-reconnect, \.cal-actions\) \{ position: sticky; bottom: 61px; z-index: 1;/);
  const sheets = css.slice(css.indexOf("@media (max-width: 511px) {\n  /* The tab's own dialogs"));
  assert.ok(sheets.indexOf("#cal-take-control, #cal-reconnect") < sheets.indexOf("\n}\n"), "a rule of every sheet, portrait included");
});

test("an overview that cannot be read shows its error, not an empty filter with zero counts", async () => {
  const { tab } = await mount({ callWS: async (message) => { if (message.type === "myhome/cover_profiles/overview") throw new Error("boom"); } });
  await tick(6);
  assert.ok(tab.querySelector(".notice.error[role=alert]"), "the error");
  assert.ok(tab.querySelector('[data-action="cal-tab-refresh"]'), "and a way to read it again");
  assert.equal(tab.querySelector(".cal-tab-filters"), null, "no filter bar with zero counts");
  assert.equal(tab.querySelector(".cal-tab-no-match"), null, "no «nothing matches the filter»");
  const { readFileSync } = await import("node:fs");
  const css = panelCss;
  assert.match(css, /\.cal-tab \[data-action="cal-tab-refresh"\] \{ justify-self: start; width: fit-content; \}/, "«Aggiorna» at its own width");
});

test("the tab's dialogs follow its theme; a head action off while the list loads is a passing state", async () => {
  const { root, tab } = await mount();
  assert.equal(root.getElementById("dialog-host").dataset.theme, tab.dataset.theme, "the dialogs follow the tab's theme");
  assert.equal(tab.querySelector('[data-action="cal-tab-measure"]').classList.contains("is-settling"), false, "loaded: no passing state");
});

test("native radios and drop-downs follow the theme (color-scheme)", async () => {
  const { readFileSync } = await import("node:fs");
  const css = panelCss;
  assert.match(css, /\[data-theme="dark"\] \{ color-scheme: dark; \}\s*\[data-theme="light"\] \{ color-scheme: light; \}/);
});

test("a long profile name stays inside its note, and the details' chip wraps only after «_», «-» or «.»", async () => {
  const { readFileSync } = await import("node:fs");
  const css = panelCss;
  assert.match(css, /\.cal-note \{ overflow-wrap: anywhere; \}/);
  assert.match(css, /\.cal-page \.cal-card-body \.cal-chip-text \{ white-space: normal; overflow-wrap: break-word; \}/);
  const data = overview();
  data.profiles[0].name = "garden_side_profile-north.v2";
  const { tab } = await mount({ data });
  const chip = [...tab.querySelectorAll(".cal-row-profile .cal-chip-text")].find((item) => item.textContent === "garden_side_profile-north.v2");
  assert.ok(chip, "in the row the name is one run, cut with «…»");
  assert.equal(chip.querySelector("wbr"), null);
  const toggle = chip.closest(".cal-row").querySelector('[data-action="cal-tab-toggle"]'); toggle.click(); await tick();
  // The name whole in the card «Profilo» of «Panoramica», beside its symbol.
  const whole = detailsOf(tab).querySelector(".cal-page-profile .cal-profile-name");
  assert.equal(whole.innerHTML, "garden_<wbr>side_<wbr>profile-<wbr>north.<wbr>v2");
});

test("«In corso · …» is one line like every chip, its text darkened to read on its fill", async () => {
  const { readFileSync } = await import("node:fs");
  const css = panelCss;
  assert.match(css, /\.cal-badge\[data-status="running"\] \{ --cal-chip: var\(--panel-warning\); white-space: nowrap; color: color-mix\(in srgb, var\(--panel-warning\) 55%, #000\); \}/);
  assert.match(css, /\[data-theme="dark"\] \.cal-badge\[data-status="running"\] \{ color: var\(--panel-warning\); border-color: var\(--panel-warning\); \}/, "the full warning colour on a dark theme");
});

test("the active filter chip's number is the accent mixed towards the text, as on the tabs", async () => {
  const { readFileSync } = await import("node:fs");
  const css = panelCss;
  assert.match(css, /\.cal-chip\[aria-pressed="true"\] \.count \{ color: color-mix\(in srgb, var\(--panel-accent\) 70%, var\(--primary-text-color, #182b37\)\); \}/);
});

test("the cards never wider than the list; no row measured on its own any more (the per-row chips box is gone with the per-row layouts)", async () => {
  const css = panelCss;
  assert.match(css, /\.cal-list-rows \{ display: grid; grid-template-columns: minmax\(0, 1fr\); gap: 12px; \}/);
  assert.doesNotMatch(css, /\.cal-row-toggle \{ flex: 1 1 200px; min-width: min-content; padding: 0; \}/, "no flexible card header");
  const { panel } = await mount();
  assert.equal(panel._calibrationTab._fitChips, undefined, "nothing measured per row");
  const { readFileSync } = await import("node:fs");
  const source = readFileSync(new URL("../../custom_components/myhome/frontend/panel/panel-calibration-tab.js", import.meta.url), "utf8");
  assert.doesNotMatch(source, /chips\.style\.flex = /);
});

test("a wizard list's chips sit beside the name while it keeps 200 px, side by side or stacked, under it otherwise", async () => {
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
  // The name's line (room, address) keeps one line beside the chips: a 400 px line needs 400 px.
  const name = document.createElement("span"); name.className = "cal-row-name"; name.innerHTML = '<span class="muted">Main bedroom · Indirizzo: 32</span>';
  row.querySelector(".cal-row-name").replaceWith(name);
  const createRange = document.createRange.bind(document);
  document.createRange = () => { const range = createRange(); range.getBoundingClientRect = () => rect(400); return range; };
  try { assert.deepEqual(at(800), ["is-beside", "is-stacked"], "366 px beside the two chips is too little for the line; stacked leaves 524"); }
  finally { document.createRange = createRange; }
  root.remove();
});

test("where the head stacks (phone), «Come funziona» opens right under its link, before the two buttons", async () => {
  const { readFileSync } = await import("node:fs");
  const css = panelCss;
  const phone = css.slice(css.indexOf("@media (max-width: 719px) and (min-height: 500px), (max-width: 719px) and (orientation: portrait) {\n  .cal-tab-top"));
  const block = phone.slice(0, phone.indexOf("\n}\n"));
  assert.match(block, /\.cal-tab-head \{ display: contents; \}\s*\.cal-tab-heading \{ order: 1; \}\s*\.cal-tab-how-text \{ order: 2; font-size: var\(--cal-fs-14\); \}\s*\.cal-tab-actions \{ order: 3; \}/);
  const { tab } = await mount();
  const top = tab.querySelector(".cal-tab-top");
  assert.deepEqual([...top.querySelectorAll(":scope > .cal-tab-head > *, :scope > .cal-tab-how-text")].map((item) => [...item.classList].find((name) => name.startsWith("cal-tab-"))),
    ["cal-tab-heading", "cal-tab-actions", "cal-tab-how-text"], "the three parts the stylesheet orders: title and link, buttons, text");
});

test("a filter's or an order's drop-down leaves every scrolling box where it was (iOS scrolls for its picker)", async () => {
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

test("choosing a cover in A-01 scrolls nothing: the row only shows as selected", async () => {
  const source = (await import("node:fs")).readFileSync(new URL("../../custom_components/myhome/frontend/panel/panel-calibration-wizard.js", import.meta.url), "utf8");
  assert.doesNotMatch(source, /chosen\.scrollIntoView|is-selected"\)[^;]*scrollIntoView/, "no scroll on a choice");
  assert.match(source, /column\.querySelector\(`\[data-wiz="\$\{focused\}"\]`\)\?\.focus\(\{ preventScroll: true \}\)/, "the actions' focus comes back without scrolling");
});

test("a sheet's fade above its buttons is the wizard bars' fade, anchored into the band with no gap", async () => {
  const { readFileSync } = await import("node:fs");
  const css = panelCss;
  const sheet = css.match(/dialog\.cal-dialog \.dialog-foot::before \{([^}]*)\}/)?.[1] || "";
  const bar = css.match(/\.wiz-actions::before \{ content: ""; position: absolute; inset-inline: 0; bottom: calc\(100% \+ 1px\);([^}]*)\}/)?.[1] || "";
  assert.match(sheet, /bottom: calc\(100% - 1px\); height: 40px;/);
  const gradient = (rule) => rule.match(/background: [^;]+;/)?.[0];
  assert.equal(gradient(sheet), gradient(bar), "the same gradient");
});

test("a centred dialog with a long list scrolls only its body: title row and buttons stay in view", async () => {
  const { readFileSync } = await import("node:fs");
  const css = panelCss;
  const outside = css.slice(0, css.indexOf("@media (max-width: 719px), (max-height: 499px) {"));
  const rest = css.slice(css.indexOf("/* Centred too,"));
  assert.ok(css.indexOf("/* Centred too,") > 0);
  assert.match(rest, /^\/\* Centred too,[^]*?dialog\.cal-dialog\[open\] \{ display: flex; flex-direction: column; max-height: calc\(100vh - 48px\); max-height: calc\(100dvh - 48px\); overflow: hidden; \}/);
  assert.match(rest, /dialog\.cal-dialog \.cal-dialog-body \{ flex: 1 1 auto; min-height: 0; overflow-y: auto; overscroll-behavior: contain; \}/);
  void outside;
});

test("on the phone a cover's page with its session open offers its action, as its row does", async () => {
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

test("the session's action says what it does: «Follow» for another tab's, «Resume» for this tab's own (one word in the row, the whole label in its title)", async () => {
  const base = { entry_id: "one", entity_id: "cover.bedroom", session_id: "s1", mode: "geometry", phase: "briefing", step: "home", sequence: 2, values: {},
    recoverable: true, attached: false, owner_present: true };
  for (const [owned, label, whole, notice, it, itShort] of [[false, "Follow", "Follow session", /follow it with «Follow»/, "Segui sessione", "Segui"],
    [true, "Resume", "Resume session", /resume it with «Resume»/, "Riprendi sessione", "Riprendi"]]) {
    // Home Assistant answers `client_owner` for the client named in the read.
    const { tab } = await mount({ callWS: (message) => (message.type === "myhome/cover_profiles/read"
      ? { revision: 7, calibration: { ...base, ...(message.client_id ? { client_owner: owned } : {}) } } : undefined) });
    const action = row(tab, "cover.bedroom").querySelector('[data-action="cal-tab-session"]');
    assert.equal(action.textContent, label);
    assert.deepEqual([action.title, action.getAttribute("aria-label")], [whole, whole], "the whole label in its title and for screen readers");
    assert.match(tab.querySelector(".cal-tab-busy").textContent, notice);
    assert.equal(translations.it[owned ? "wiz_T580" : "wiz_T579"], it);
    assert.equal(translations.it[owned ? "calTabRowResume" : "calTabRowFollow"], itShort);
  }
});

test("a session started, moved on or ended elsewhere reaches the list at once: a burst makes one read", async () => {
  const { subscriptions, calls } = await mount();
  const subscription = subscriptions.find((item) => item.request.type === "myhome/cover_profiles/subscribe" && item.request.entry_id === "one");
  const reads = () => calls.filter((message) => message.type === "myhome/cover_profiles/overview" && message.entry_id === "one").length;
  const before = reads();
  for (let i = 0; i < 3; i++) subscription.callback({ entry_id: "one", revision: 7, kind: "session" });
  await new Promise((resolve) => setTimeout(resolve, 400)); await tick(4);
  assert.equal(reads(), before + 1, "one read, within a quarter of a second, not at the next 15 s refresh");
});

test("the details' times carry one decimal, as the wizard's", async () => {
  const data = overview();
  data.covers[0].effective.opening.value = 22;
  const { tab } = await mount({ data });
  const page = await openDetails(tab, data.covers[0].entity_id);
  assert.equal(page.querySelector(".cal-page-status .cal-measure-number").textContent, "22.0", "the details' times as «Misure», one decimal");
});

test("«Torna all'elenco» always opens the list, even for a wizard opened from a cover's page", async () => {
  const original = globalThis.matchMedia;
  globalThis.matchMedia = viewport(390, 844);
  try {
    const { tab, panel } = await mount({ language: "it" });
    row(tab, "cover.living_one").querySelector(".cal-row-toggle").click(); await tick();
    await recalibrateFromCard(tab);
    assert.equal(panel._calibrationTab._mode.kind, "wizard");
    // The bar's «Torna all'elenco» (the close action) of any end screen.
    const back = document.createElement("button");
    back.type = "button"; back.dataset.wiz = "close"; back.textContent = translations.it.wiz_T390;
    tab.querySelector(".wiz .wiz-actions").append(back);
    await new Promise((resolve) => setTimeout(resolve, 720)); // past the hold after the wizard opened (CONFIRM_GUARD_MS)
    back.click(); await tick(6);
    assert.equal(panel._calibrationTab._mode.kind, "list");
    assert.equal(tab.querySelector(".cal-page"), null, "the list, not the cover's page");
    assert.ok(tab.querySelector(".cal-list-rows"));
  } finally { globalThis.matchMedia = original; }
});

test("no room under the tab nor page scroll for the wizard: --wiz-room and revealHead are gone with the inline layouts", async () => {
  const layoutModule = await import("../../custom_components/myhome/frontend/panel/panel-calibration-layout.js");
  assert.equal(layoutModule.revealHead, undefined);
  assert.equal(layoutModule.dockBar, undefined);
  const { readFileSync } = await import("node:fs");
  const read = (name) => readFileSync(new URL(`../../custom_components/myhome/frontend/panel/${name}`, import.meta.url), "utf8");
  for (const name of ["panel-calibration-layout.js", "panel-calibration-tab.js", "panel-calibration-wizard.js", "panel-calibration.css"]) {
    assert.doesNotMatch(read(name), /--wiz-room|revealHead|dockBar|is-docked/, name);
  }
});

test("long names: the bus address goes to the next line whole, before «Indirizzo», and a pair never parts", async () => {
  const { tab } = await mount({ language: "it" });
  const line = row(tab, "cover.living_one").querySelector(".cal-row-name .muted");
  const address = line.querySelector(".cal-row-address");
  assert.ok(address, "the address is one block");
  assert.deepEqual([...address.querySelectorAll(".cal-row-address-part")].map((part) => part.textContent), ["Indirizzo:\u00a091\u00a0·", "A:\u00a09\u00a0·", "PL:\u00a01"],
    "label and value held by a no-break space, the «·» with the pair before it");
  const css = panelCss.replace(/\/\*[\s\S]*?\*\//g, "");
  assert.match(css, /\.cal-row-address \{ display: inline-block; max-width: 100%; white-space: normal; \}/, "the block moves whole to the next line");
  assert.match(css, /\.cal-row-address-part \{ white-space: nowrap; \}/);
});
