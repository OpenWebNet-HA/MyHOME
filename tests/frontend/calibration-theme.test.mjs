/**
 * The look of the Calibration tab: colours, the dark theme, chips and icons.
 */
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { after, afterEach, describe, test } from "node:test";

import { cascadeCss, winner } from "./css-cascade.mjs";
import { sampleOverview } from "./fake-calibration.mjs";
import { panelCss as css } from "./panel-css.mjs";
import { dom, mount, move, pageTab, reset, tick } from "./wizard-harness.mjs";

const original = globalThis.matchMedia;
afterEach(() => { globalThis.matchMedia = original; reset(); });
after(() => dom.window.close());

describe("colours and the dark theme", async () => {
  const html = (markup) => { const host = dom.window.document.createElement("div"); host.innerHTML = markup; dom.window.document.body.append(host); return host; };

  test("a disabled filled button is the same neutral grey without opacity, the red «Ferma la tapparella» too", () => {
    for (const theme of ["light", "dark"]) {
      const host = html(`<div class="wiz" data-theme="${theme}"><div class="wiz-actions"><button class="wiz-action primary" disabled></button>
      <button class="wiz-action stop" disabled></button><button class="wiz-action stop is-finished" disabled></button></div></div>`);
      for (const button of host.querySelectorAll("button")) {
        assert.equal(winner(button, "background"), "color-mix(in srgb, var(--wiz-off) var(--wiz-off-fill), var(--panel-surface))", `${theme}: ${button.className}`);
        assert.equal(winner(button, "opacity"), "1", `${theme}: ${button.className}: no transparency`);
        assert.equal(winner(button, "border-color"), "transparent");
      }
      host.remove();
    }
    // Enabled, the emergency stays filled red.
    const live = html('<div class="wiz"><div class="wiz-actions"><button class="wiz-action stop"></button></div></div>');
    assert.equal(winner(live.querySelector("button"), "background"), "var(--panel-error)");
  });

  test("on a dark theme an overlay's base is the cards' grey and its white parts one step lighter; light unchanged", () => {
    for (const theme of ["light", "dark"]) {
      const host = html(`<div class="cal-tab" data-theme="${theme}"><dialog class="cal-flow"><div class="cal-flow-box"></div></dialog></div>
      <div id="dialog-host" data-theme="${theme}"><dialog class="cal-dialog"></dialog></div>`);
      for (const box of host.parentElement.querySelectorAll(".cal-flow-box, dialog.cal-dialog")) {
        const base = winner(box, "--primary-background-color"), surface = winner(box, "--panel-surface");
        if (theme === "light") assert.deepEqual([base, surface], ["", ""], "light: Home Assistant's own (FAFAFA and white)");
        else {
          assert.equal(base, "var(--card-background-color, #1c1c1c)", "dark: the content on the cards' grey");
          assert.match(surface, /^color-mix\(in srgb, var\(--primary-text-color[^)]*\) 6%, var\(--card-background-color[^)]*\)\)$/, "dark: header, footer, cards a step lighter");
          assert.match(winner(box, "--cal-tail"), /var\(--panel-surface\)\)$/, "the tints worked out on the new surface");
        }
      }
      host.remove(); dom.window.document.getElementById("dialog-host")?.remove();
    }
  });

  test("on a dark theme a disabled option's text is about 40 % of the text colour; light keeps 60 %", () => {
    for (const [theme, value] of [["light", ".6"], ["dark", ".4"]]) {
      const host = html(`<div class="wiz" data-theme="${theme}"><label class="wiz-radio is-disabled"><input type="radio" disabled><span class="wiz-radio-text"><span class="wiz-radio-label">B</span>
      <span class="cal-note wiz-radio-notice">soon</span></span></label></div>`);
      assert.equal(winner(host.querySelector(".wiz-radio-label"), "opacity"), value, theme);
      assert.equal(winner(host.querySelector("input"), "opacity"), value);
      assert.equal(winner(host.querySelector(".wiz-radio-notice"), "opacity"), "", "the note saying why stays at full strength");
      host.remove();
    }
  });

  test("texts and borders of the reds are the red ink, lighter on a dark theme; the filled Stop keeps the error colour", () => {
    const host = html(`<div class="wiz" data-theme="dark"><div class="wiz-actions"><button class="wiz-action danger-outline"></button><button class="wiz-action stop"></button></div>
    <form class="wiz-field"><p class="cal-note is-error wiz-field-message wiz-field-error">x</p></form><div class="wiz-dialog"><button class="wiz-leave"></button></div></div>`);
    assert.equal(winner(host.querySelector(".wiz"), "--cal-red-ink"), "color-mix(in srgb, var(--panel-error) 75%, #fff)");
    for (const element of host.querySelectorAll(".danger-outline, .wiz-leave")) assert.equal(winner(element, "color"), "var(--cal-red-ink)", element.className);
    // The field's refusal is an error note (the warning's shape): its text and bar in the red ink on dark.
    assert.equal(winner(host.querySelector(".wiz-field-error"), "--note-text"), "var(--cal-red-ink)");
    assert.equal(winner(host.querySelector(".stop"), "background"), "var(--panel-error)", "the fill unchanged");
  });

  test("every overlay of ours has the shell's scrim, rgb(15 23 30 / 55 %) with a 3 px blur", () => {
    assert.match(cascadeCss, /dialog::backdrop \{ background: rgb\(15 23 30 \/ 55%\); backdrop-filter: blur\(3px\); \}/, "the shell's own");
    for (const selector of ["dialog.cal-flow::backdrop", "dialog.cal-dialog::backdrop"]) {
      assert.ok(cascadeCss.includes(`${selector} { background: rgb(15 23 30 / 55%); -webkit-backdrop-filter: blur(3px); backdrop-filter: blur(3px); }`), selector);
    }
    const host = html('<div class="wiz"><div class="wiz-sheet"></div><div class="wiz-dialog"></div></div>');
    for (const layer of host.querySelectorAll(".wiz-sheet, .wiz-dialog")) {
      assert.equal(winner(layer, "background"), "var(--cal-scrim)");
      assert.equal(winner(layer, "backdrop-filter"), "var(--cal-scrim-blur)");
    }
  });
});

describe("long names", async () => {
  const html = (markup) => { const host = dom.window.document.createElement("div"); host.innerHTML = markup; dom.window.document.body.append(host); return host; };
  const { createFakeBackend, sampleOverview } = await import("./fake-calibration.mjs");

  test("a long name inside a sentence wraps anywhere, never cut (the wizard's content, the details, the sheets); «…» only in one-line rows", () => {
    assert.match(cascadeCss, /:is\(\.wiz-main, \.cal-page-body, dialog\.cal-dialog \.cal-dialog-body\) \{ overflow-wrap: anywhere; \}/);
    const host = html('<div class="wiz"><div class="wiz-main"><p class="wiz-name-line">x</p></div></div>');
    assert.equal(winner(host.querySelector(".wiz-main"), "overflow-wrap"), "anywhere");
    host.remove();
    assert.match(cascadeCss, /\.cal-chip-text \{ min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; \}/, "the list's one-line chips keep their «…»");
    void createFakeBackend;
  });
});

describe("the processes' icons", async () => {
  const measured = { source: "guided", recorded_at: "2026-10-30T10:00:00+00:00", origin_entity_id: null, origin_name: null, inherited: false };
  const timing = (value, origin = "profile") => ({ value, origin, provenance: measured, scaled: false });
  /** Three covers: «tall» measured on Living room shutter 1 (its source), followed by the other two. */
  function sourceOverview({ reference = 198, followerTravel = [110, null] } = {}) {
    const data = sampleOverview();
    const profile = data.profiles[0];
    profile.reference_travel_cm = reference;
    profile.assigned_to = ["cover.living_one", "cover.kitchen_two", "cover.living_three"];
    const [one, two, three] = data.covers;
    Object.assign(one, { profile_id: "tall", travel_cm: reference, precision: "basic", effective: { opening: timing(22.4), closing: timing(21.9) } });
    if (reference == null) delete one.travel_cm;
    for (const [cover, travel] of [[two, followerTravel[0]], [three, followerTravel[1]]]) {
      if (travel == null) delete cover.travel_cm; else cover.travel_cm = travel;
    }
    return data;
  }
  const row = (tab, entity) => tab().querySelector(`.cal-row[data-entity="${entity}"]`);
  async function details(context, entity, view = "overview") {
    row(context.tab, entity).querySelector('[data-action="cal-tab-toggle"]').click();
    await tick(6);
    // The details open on «Panoramica» (the travel, the slats, the profile); the table and its tail are on «Dettagli».
    if (view !== "overview") await pageTab(context.tab, view);
    return context.tab().querySelector(".cal-flow .cal-page");
  }
  /** Living room shutter 1 (never calibrated, closed at 0): the basic calibration from A-02, the slats answer given. */
  async function toBasic(context, { slats = "no" } = {}) {
    const { backend, startFor, wiz } = context;
    backend.hass.states["cover.living_one"] = { state: "closed", attributes: { current_position: 0 } };
    await startFor("cover.living_one", "base");
    const radio = wiz().querySelector(`input[name="slats"][value="${slats}"]`);
    radio.checked = true; radio.dispatchEvent(new Event("change", { bubbles: true })); await tick();
  }
  /** Both full runs, each result acknowledged. */
  async function bothRuns(context, { opening = 14.2, closing = 14.0 } = {}) {
    const { backend, press } = context;
    await press("run:open"); await move(backend, { seconds: opening }); await press("endpoint");
    await press("ack:ascent");
    await press("run:close"); await move(backend, { seconds: closing }); await press("endpoint");
  }

  test("«Perfeziona» carries the ruler, Calibra / Ricalibra the stopwatch: the details' «Posizionamento», the rows' ⋮, the tab's button and A-34", async () => {
    // Perfeziona is the main action of «Posizionamento» on the profile's source, its caption under it.
    const source = await mount({ language: "it", overview: sourceOverview() });
    const page = await details(source, "cover.living_one");
    // «Perfeziona» alone.
    assert.deepEqual([...page.querySelectorAll(".cal-cta button")].map((button) => [button.dataset.action, button.textContent.trim(), button.className,
      button.querySelector("ha-icon").getAttribute("icon")]), [["cal-tab-refine", "Perfeziona", "primary", "mdi:arrow-expand-vertical"]], "the straight ruler");
    reset();
    const context = await mount({ language: "it" });
    const { tab, wiz, code, press, panel } = context;
    const icons = (selector) => [...tab().querySelectorAll(`${selector} ha-icon`)].map((icon) => icon.getAttribute("icon"));
    assert.ok(icons('.cal-row [data-action="cal-tab-measure-cover"]').every((icon) => icon === "mdi:timer-outline"));
    assert.deepEqual(icons('[data-action="cal-tab-measure"]'), ["mdi:timer-outline"], "«Calibra una tapparella»");
    await toBasic(context);
    await press("begin");
    await bothRuns(context);
    await press("ack:descent");
    await press("save");
    assert.equal(code(), "A-34");
    const refine = wiz().querySelector('[data-wiz="refine-tape"]');
    assert.deepEqual([refine.textContent, refine.querySelector("ha-icon").getAttribute("icon")], ["Perfeziona", "mdi:arrow-expand-vertical"], "A-34: «Perfeziona» with the straight ruler");
    void panel;
  });
});

describe("the chips' corners", async () => {
  const PANEL = new URL("../../custom_components/myhome/frontend/panel/", import.meta.url);
  const read = (name) => readFileSync(new URL(name, PANEL), "utf8");

  test("the chips (status, profile, badge, the sort chip) round 12 px, half a one-line chip: a pill on one line, a rounded box on two; 999 px only where nothing wraps", () => {
    const css = read("panel-calibration.css");
    assert.match(css, /--cal-r-chip: 12px;/);
    assert.match(css, /\.cal-chip, \.cal-badge, \.cal-profile-chip \{[^}]*border-radius: var\(--cal-r-chip\);/);
    assert.match(css, /\.cal-filterbar\.is-chips \.cal-filterbar-sort \{[^}]*border-radius: var\(--cal-r-chip\);/);
    const pills = [...css.matchAll(/([^{}]+)\{[^}]*border-radius: 999px/g)].map(([, selector]) => selector.trim().split("\n").at(-1));
    assert.ok(pills.length >= 4);
    for (const pill of pills) assert.match(pill, /count|progress|switch|handle/, `${pill}: never wraps`);
  });
});

describe("Perfeziona's ruler", async () => {
  const { checkSentence, statusBadge, ICON } = await import("../../custom_components/myhome/frontend/panel/panel-calibration-tab.js");
  const text = await import("../../custom_components/myhome/frontend/panel/panel-calibration-text.js");
  /** A window `width` px wide and 800 high for `matchMedia`, motion reduced. */
  const windowOf = (width) => (query) => ({ matches: query.split(",").some((part) => [...part.matchAll(/\(\s*([a-z-]+)\s*:\s*([a-z0-9-]+)\s*\)/g)].every(([, name, value]) =>
    name === "max-width" ? width <= parseFloat(value) : name === "min-width" ? width >= parseFloat(value) : name === "max-height" ? 800 <= parseFloat(value)
      : name === "min-height" ? 800 >= parseFloat(value) : name === "orientation" ? value === "landscape" : name === "prefers-reduced-motion")), addEventListener() {}, removeEventListener() {} });
  const phone = () => { globalThis.matchMedia = windowOf(390); };
  const MEASURED_AT = "2026-11-01T16:09:00+00:00", CHECKED_AT = "2026-11-01T18:01:00+00:00", REFINED_AT = "2026-11-09T01:57:00+00:00";
  const measured = { source: "guided", recorded_at: MEASURED_AT, origin_entity_id: null, origin_name: null, inherited: false };
  const inherited = { ...measured, inherited: true, origin_entity_id: "cover.living_one", origin_name: "Living room shutter 1" };
  const timing = (value, origin = "profile", scaled = false, provenance = measured) => ({ value, origin, provenance, scaled });
  /**
   * «tall» measured on Living room shutter 1 (the source, whole numbers in its times and coefficients, a check at half the
   * travel `gap` cm away), followed by Kitchen shutter 2 (115 cm, adapted); Living room shutter 3 never calibrated.
   * `alone`: Kitchen shutter 2 without a profile, calibrated with the tape (its own values, whole numbers too).
   */
  function family({ gap = 2, alone = false } = {}) {
    const data = sampleOverview();
    const profile = data.profiles[0];
    Object.assign(profile, { opening_time: 22, closing_time: 21.6, reference_travel_cm: 110 });
    profile.geometry = { slat_time_s: 3, opening_roll: 1, closing_roll: 1.05 };
    profile.assigned_to = alone ? ["cover.living_one"] : ["cover.living_one", "cover.kitchen_two"];
    profile.provenance.opening.recorded_at = MEASURED_AT;
    const [one, two, three] = data.covers;
    Object.assign(one, { profile_id: "tall", travel_cm: 110, precision: "verified", slats_state: "yes_measured", check_cm: Math.round(gap),
      check_expected_cm: 55, check_measured_cm: 55 + gap, check_at: CHECKED_AT,
      effective: { opening: timing(22), closing: timing(21.6), slat_time_s: timing(3), opening_roll: timing(1, "profile", false, { ...measured, recorded_at: REFINED_AT }),
        closing_roll: timing(1.05, "profile", false, { ...measured, recorded_at: REFINED_AT }) } });
    if (alone) {
      Object.assign(two, { profile_id: null, travel_cm: 120, precision: "refined", slats_state: "yes_measured",
        effective: { opening: timing(12, "cover"), closing: timing(11.5, "cover"), slat_time_s: timing(2, "cover"), opening_roll: timing(1, "cover"), closing_roll: timing(1.25, "cover") } });
    } else {
      Object.assign(two, { profile_id: "tall", travel_cm: 115, precision: "refined", slats_state: "yes_profile", travel_edit: "scaled",
        effective: { opening: timing(23, "profile", true, inherited), closing: timing(22.6, "profile", true, inherited), slat_time_s: timing(3.1, "profile", true, inherited),
          opening_roll: timing(1, "profile", false, inherited), closing_roll: timing(1.05, "profile", false, inherited) } });
    }
    Object.assign(three, { profile_id: null, precision: "none", slats_state: "unknown", overrides: {},
      effective: { opening: { value: 20, origin: "default", provenance: { source: "unknown" } }, closing: { value: 20, origin: "default", provenance: { source: "unknown" } } } });
    delete three.travel_cm;
    return data;
  }
  const row = (context, entity) => context.tab().querySelector(`.cal-row[data-entity="${entity}"]`);
  const card = (context) => context.tab().querySelector(".cal-flow .cal-page:not(.cal-assign-page)");
  async function open(context, entity, tab = "overview") {
    row(context, entity).querySelector('[data-action="cal-tab-toggle"]').click();
    await tick(6);
    if (tab !== "overview") await pageTab(context.tab, tab);
    return card(context);
  }
  async function close(context) { card(context).querySelector(".cal-page-close").click(); await tick(8); }

  test("«Perfeziona» carries the straight ruler: the overview's button, the outcome's, the row's ⋮", async () => {
    assert.equal(ICON.refine, text.REFINE_ICON);
    assert.equal(text.iconMarkup(ICON.refine), '<ha-icon class="cal-refine-icon" icon="mdi:arrow-expand-vertical" aria-hidden="true"></ha-icon>');
    assert.equal(text.iconMarkup("mdi:timer-outline"), '<ha-icon icon="mdi:timer-outline" aria-hidden="true"></ha-icon>', "every other icon as before");
    assert.doesNotMatch(css, /cal-svg-icon/, "the ruler's own rules are gone");
    phone();
    const context = await mount({ language: "it", overview: family() });
    const page = await open(context, "cover.living_one");
    const button = page.querySelector('.cal-cta [data-action="cal-tab-refine"]');
    assert.deepEqual([button.textContent.trim(), button.querySelector("ha-icon").getAttribute("icon"), button.querySelector("svg")], ["Perfeziona", "mdi:arrow-expand-vertical", null]);
    await close(context);
    row(context, "cover.living_one").querySelector('[data-action="cal-tab-menu"]').click(); await tick(4);
    assert.equal(row(context, "cover.living_one").querySelector('.cal-menu [data-action="cal-tab-refine"] ha-icon').getAttribute("icon"), "mdi:arrow-expand-vertical");
  });
});

describe("icons in chips", async () => {
  const PANEL = "../../custom_components/myhome/frontend/panel/";
  const { statusBadge, adaptedFromProfile, sourceTimesDate, SAME_CALIBRATION_MS, ICON } = await import(`${PANEL}panel-calibration-tab.js`);
  const text = await import(`${PANEL}panel-calibration-text.js`);

  test("Perfeziona's icon, everywhere it means Perfeziona", () => {
    assert.deepEqual([ICON.refine, text.REFINE_ICON], ["mdi:arrow-expand-vertical", "mdi:arrow-expand-vertical"]);
    assert.equal(text.iconMarkup(ICON.refine), '<ha-icon class="cal-refine-icon" icon="mdi:arrow-expand-vertical" aria-hidden="true"></ha-icon>');
  });

  test("every chip's icon sits on the text's axis: a box the icon's size, centred in the chip", () => {
    assert.match(css, /:is\(\.cal-chip, \.cal-badge, \.cal-profile-chip\) > ha-icon \{ display: inline-flex; align-items: center; justify-content: center; align-self: center;\s+inline-size: var\(--mdc-icon-size\); block-size: var\(--mdc-icon-size\); line-height: 0; vertical-align: middle; --ha-icon-display: block; \}/);
    assert.match(css, /\.cal-chip, \.cal-badge, \.cal-profile-chip \{[^}]*display: inline-flex; align-items: center;/, "the chip itself centres its line");
    assert.match(css, /\.cal-profile-chip ha-icon \{ --mdc-icon-size: 12px;/);
    assert.match(css, /\.cal-badge > ha-icon \{ --mdc-icon-size: 14px;/);
  });
});

describe("Perfeziona's icon", async () => {
  const PANEL = "../../custom_components/myhome/frontend/panel/";
  const { profileTimesDate, sourceTimesDate, ICON } = await import(`${PANEL}panel-calibration-tab.js`);
  const text = await import(`${PANEL}panel-calibration-text.js`);
  /** A window `width` px wide and 800 high for `matchMedia`, motion reduced. */
  const windowOf = (width) => (query) => ({ matches: query.split(",").some((part) => [...part.matchAll(/\(\s*([a-z-]+)\s*:\s*([a-z0-9-]+)\s*\)/g)].every(([, name, value]) =>
    name === "max-width" ? width <= parseFloat(value) : name === "min-width" ? width >= parseFloat(value) : name === "max-height" ? 800 <= parseFloat(value)
      : name === "min-height" ? 800 >= parseFloat(value) : name === "orientation" ? value === "landscape" : name === "prefers-reduced-motion")), addEventListener() {}, removeEventListener() {} });
  const phone = () => { globalThis.matchMedia = windowOf(390); };
  const AT = (minutes) => new Date(Date.parse("2026-11-03T17:39:00+00:00") + minutes * 60000).toISOString().replace(".000Z", "+00:00");
  const evidence = (recorded_at, extra = {}) => ({ source: "guided", recorded_at, origin_entity_id: null, origin_name: null, inherited: false, ...extra });
  const timing = (value, provenance, origin = "profile", scaled = false) => ({ value, origin, provenance, scaled });
  /**
   * «tall_two_leaf» measured on Living room shutter 1 in one calibration (the opening at 18:39, the closing and the lift-off
   * at 18:40; `refinedAfter` minutes later a Perfeziona, when given), followed by Kitchen shutter 2 (`travel` cm against the
   * 110 of the reference); Living room shutter 3 never calibrated, without a profile.
   */
  function family({ travel = 110, refinedAfter = null } = {}) {
    const data = sampleOverview();
    const profile = data.profiles[0];
    Object.assign(profile, { opening_time: 22, closing_time: 21.6, reference_travel_cm: 110, assigned_to: ["cover.living_one", "cover.kitchen_two"] });
    profile.geometry = { slat_time_s: 3, opening_roll: 1, closing_roll: 1 };
    profile.provenance.opening = evidence(AT(0), { origin_entity_id: "cover.living_one", origin_name: "Living room shutter 1" });
    profile.provenance.closing = evidence(AT(1), { origin_entity_id: "cover.living_one", origin_name: "Living room shutter 1" });
    const rolled = refinedAfter == null ? { source: "unknown", recorded_at: null } : evidence(AT(1 + refinedAfter));
    const [one, two, three] = data.covers;
    Object.assign(one, { profile_id: "tall", travel_cm: 110, precision: refinedAfter == null ? "basic" : "refined", slats_state: "yes_measured",
      effective: { opening: timing(22, profile.provenance.opening), closing: timing(21.6, profile.provenance.closing), slat_time_s: timing(3, evidence(AT(1))),
        opening_roll: timing(1, rolled), closing_roll: timing(1, rolled) } });
    const inherited = evidence(AT(0), { inherited: true, origin_entity_id: "cover.living_one", origin_name: "Living room shutter 1" });
    const scaled = travel !== 110;
    Object.assign(two, { profile_id: "tall", travel_cm: travel, precision: "basic", slats_state: "yes_profile", overrides: {},
      effective: { opening: timing(22 * travel / 110, inherited, "profile", scaled), closing: timing(21.6 * travel / 110, inherited, "profile", scaled),
        slat_time_s: timing(3, inherited, "profile", scaled) } });
    Object.assign(three, { profile_id: null, precision: "none", slats_state: "unknown", overrides: {},
      effective: { opening: { value: 20, origin: "default", provenance: { source: "unknown" } }, closing: { value: 20, origin: "default", provenance: { source: "unknown" } } } });
    delete three.travel_cm;
    return data;
  }
  const row = (context, entity) => context.tab().querySelector(`.cal-row[data-entity="${entity}"]`);
  const card = (context) => context.tab().querySelector(".cal-flow .cal-page:not(.cal-assign-page)");
  async function open(context, entity) {
    row(context, entity).querySelector('[data-action="cal-tab-toggle"]').click();
    await tick(6);
    return card(context);
  }

  test("Perfeziona's icon (`mdi:arrow-expand-vertical`, upright), everywhere it means Perfeziona: the button, the row's ⋮, the outcome", async () => {
    assert.deepEqual([ICON.refine, text.REFINE_ICON], ["mdi:arrow-expand-vertical", "mdi:arrow-expand-vertical"]);
    assert.equal(text.iconMarkup(ICON.refine), '<ha-icon class="cal-refine-icon" icon="mdi:arrow-expand-vertical" aria-hidden="true"></ha-icon>');
    assert.equal(text.iconMarkup("mdi:timer-outline"), '<ha-icon icon="mdi:timer-outline" aria-hidden="true"></ha-icon>', "no other icon turned");
    assert.doesNotMatch(css, /cal-refine-icon \{ transform/, "the arrows stand upright, nothing turns them");
    const source = readFileSync(new URL(`${PANEL}panel-calibration-tab.js`, import.meta.url), "utf8") + readFileSync(new URL(`${PANEL}panel-calibration-screens.js`, import.meta.url), "utf8");
    assert.doesNotMatch(source, /tape-measure/, "no tape measure left");
    phone();
    const context = await mount({ language: "it", overview: family() });
    const icon = (element) => [element.querySelector("ha-icon").getAttribute("icon"), element.querySelector("ha-icon").classList.contains("cal-refine-icon")];
    // Without a check the source's Perfeziona is the row's action in view, not in ⋮.
    assert.deepEqual(icon(row(context, "cover.living_one").querySelector('.cal-row-action[data-action="cal-tab-refine"]')), ["mdi:arrow-expand-vertical", true], "the row's action");
    const page = await open(context, "cover.living_one");
    assert.deepEqual(icon(page.querySelector('.cal-cta [data-action="cal-tab-refine"]')), ["mdi:arrow-expand-vertical", true], "the overview's button");
    page.querySelector('.cal-pos-help [data-action="cal-tab-check-start"]').click(); await tick(6);
    card(context).querySelector('[data-action="cal-tab-check-answer"][data-id="middle"]').click(); await tick(6);
    assert.deepEqual(icon(card(context).querySelector('[data-action="cal-tab-refine"]')), ["mdi:arrow-expand-vertical", true], "the outcome of «Si ferma nel punto sbagliato»");
  });
});

describe("Perfeziona's label", async () => {
  const PANEL = "../../custom_components/myhome/frontend/panel/";
  const { translations } = await import(`${PANEL}panel-translations.js`);
  const it = translations.it, en = translations.en;
  const source = readFileSync(new URL(`${PANEL}panel-calibration-tab.js`, import.meta.url), "utf8");
  /** A window `width` px wide and 800 high for `matchMedia`, motion reduced. */
  const windowOf = (width) => (query) => ({ matches: query.split(",").some((part) => [...part.matchAll(/\(\s*([a-z-]+)\s*:\s*([a-z0-9-]+)\s*\)/g)].every(([, name, value]) =>
    name === "max-width" ? width <= parseFloat(value) : name === "min-width" ? width >= parseFloat(value) : name === "max-height" ? 800 <= parseFloat(value)
      : name === "min-height" ? 800 >= parseFloat(value) : name === "orientation" ? value === "landscape" : name === "prefers-reduced-motion")), addEventListener() {}, removeEventListener() {} });
  const phone = () => { globalThis.matchMedia = windowOf(390); };
  const row = (context, entity) => context.tab().querySelector(`.cal-row[data-entity="${entity}"]`);
  const card = (context) => context.tab().querySelector(".cal-flow .cal-page:not(.cal-assign-page)");
  async function open(context, entity) {
    row(context, entity).querySelector('[data-action="cal-tab-toggle"]').click();
    await tick(6);
    return card(context);
  }
  const plain = (value) => String(value ?? "").replace(/[  ]/g, " ").trim();
  const CHECKED_AT = "2026-11-04T09:30:00+00:00";
  const check = (cover, gap) => Object.assign(cover, { precision: "verified", check_cm: gap, check_expected_cm: 55, check_measured_cm: 55 + gap, check_at: CHECKED_AT });
  const own = (number) => ({ value: number, origin: "override", provenance: { source: "guided", recorded_at: "2026-09-29T10:00:00+00:00", origin_entity_id: null, origin_name: null, inherited: false }, scaled: false });
  /**
   * The world: Kitchen shutter 2 measures «tall_two_leaf» (its source), Living room shutter 3 follows it, Living room
   * shutter 1 is never calibrated (or, `alone`, calibrated with times of its own and no profile).
   */
  function world({ source = "refined", sourceGap = null, alone = null, aloneGap = null, followerGap = null, profiles = true } = {}) {
    const data = sampleOverview();
    data.profiles[0].provenance.opening.origin_entity_id = "cover.kitchen_two";
    const [one, two, three] = data.covers;
    two.precision = source;
    if (sourceGap != null) check(two, sourceGap);
    if (alone) {
      Object.assign(one, { overrides: { opening: 18, closing: 17.2 }, precision: alone, effective: { opening: own(18), closing: own(17.2) } });
      if (aloneGap != null) check(one, aloneGap);
    }
    if (followerGap != null) check(three, followerGap);
    if (!profiles) { data.profiles = []; for (const cover of data.covers) cover.profile_id = null; }
    return data;
  }

  test("«Perfeziona» / «Fine-tune», the same as calTabActionRefine: the overview's button, ⋮ and the outcome", async () => {
    assert.deepEqual([it.cal_D1_button, en.cal_D1_button], ["Perfeziona", "Fine-tune"]);
    assert.deepEqual([it.cal_D1_button, en.cal_D1_button], [it.calTabActionRefine, en.calTabActionRefine]);
    phone();
    const context = await mount({ language: "it", overview: world({ sourceGap: 1 }) });
    assert.equal(plain(row(context, "cover.kitchen_two").querySelector('.cal-menu [data-action="cal-tab-refine"]').textContent), "Perfeziona", "⋮");
    const page = await open(context, "cover.kitchen_two");
    assert.equal(plain(page.querySelector('.cal-cta [data-action="cal-tab-refine"]').textContent), "Perfeziona", "the overview's button");
    page.querySelector('.cal-pos-help [data-action="cal-tab-check-start"]').click(); await tick(6);
    card(context).querySelector('[data-action="cal-tab-check-answer"][data-id="middle"]').click(); await tick(6);
    assert.equal(plain(card(context).querySelector('.cal-check-actions [data-action="cal-tab-refine"]').textContent), "Perfeziona", "the outcome of «Si ferma nel punto sbagliato»");
  });
});
