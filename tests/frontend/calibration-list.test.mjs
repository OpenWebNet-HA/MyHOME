/**
 * The list of covers in the Calibration tab: bands and columns, chips, the action in view and ⋮, the row under the pointer.
 */
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { after, afterEach, describe, test } from "node:test";

import { translations } from "../../custom_components/myhome/frontend/panel/panel-translations.js";
import { block, cascadeCss, rule, winner } from "./css-cascade.mjs";
import { sampleOverview } from "./fake-calibration.mjs";
import { panelCss as css } from "./panel-css.mjs";
import { dom, mount, pageTab, reset, tick } from "./wizard-harness.mjs";

const original = globalThis.matchMedia;
afterEach(() => { globalThis.matchMedia = original; reset(); });
after(() => dom.window.close());

describe("rows and actions", async () => {
  const html = (markup) => { const host = dom.window.document.createElement("div"); host.innerHTML = markup; dom.window.document.body.append(host); return host; };
  const row = (tab, id) => [...tab().querySelectorAll(".cal-row")].find((item) => item.dataset.entity === id);

  test("after a change of profile the row is brought into view only if its new place left it out of view, gently and to the nearest edge", async () => {
    const { tab, root, backend } = await mount({ language: "it" });
    backend.hass.callWS = ((original) => async (message) => message.type === "myhome/cover_profiles/write" ? { revision: 8 } : original(message))(backend.hass.callWS);
    const scrolled = [];
    const proto = dom.window.HTMLElement.prototype;
    const rect = proto.getBoundingClientRect, into = proto.scrollIntoView;
    let place = { top: 2000, bottom: 2060 };
    proto.getBoundingClientRect = function () {
      return this.classList?.contains("cal-row") && this.dataset.entity === "cover.living_one" ? { ...place, left: 0, right: 300, width: 300, height: 60 } : rect.call(this);
    };
    proto.scrollIntoView = function (option) { scrolled.push([this.dataset.entity, option]); };
    try {
      const assign = async () => {
        row(tab, "cover.living_one").querySelector('[data-action="cal-tab-menu"]').click(); await tick();
        row(tab, "cover.living_one").querySelector('.cal-menu [data-action="cal-tab-assign"]').click(); await tick();
        // In the tab's frame.
        const dialog = tab().querySelector("dialog.cal-flow .cal-assign-page");
        void root;
        const tall = dialog.querySelector('input[value="tall"]');
        tall.checked = true; tall.dispatchEvent(new Event("change", { bubbles: true }));
        dialog.querySelector("[data-assign]").click(); await tick(8);
      };
      place = { top: 100, bottom: 160 };
      await assign();
      assert.deepEqual(scrolled, [], "still in view: the list does not move at all");
      place = { top: 2000, bottom: 2060 };
      await assign();
      assert.deepEqual(scrolled, [["cover.living_one", { block: "nearest", behavior: "smooth" }]], "out of view: to it, gently");
    } finally { proto.getBoundingClientRect = rect; proto.scrollIntoView = into; }
  });

  test("row actions without duplicates: at most one in the row and ⋮ with the others, in every band; never a menu of one", async () => {
    const { tab, panel } = await mount({ language: "it" });
    const inRow = (id) => [...row(tab, id).querySelectorAll(".cal-row-ctas > .cal-row-action")].map((button) => [button.dataset.action, button.querySelector("ha-icon")?.getAttribute("icon")]);
    const menu = (id) => [...row(tab, id).querySelectorAll(".cal-menu [role=menuitem]")].map((item) => item.dataset.action);
    // The same in the table's band as elsewhere («up to three» is the footers' rule).
    for (const band of ["cards", "rows", "table"]) {
      tab().dataset.band = band;
      panel._calibrationTab._render(); await tick();
      // A follower without a check has nothing in view (no «Controlla»), Ricalibra first in ⋮; Calibra on a cover never
      // calibrated (profiles in the catalogue: it opens the card).
      // The stopwatch for Calibra / Ricalibra.
      assert.deepEqual(inRow("cover.kitchen_two"), [], band);
      assert.deepEqual(menu("cover.kitchen_two"), ["cal-tab-measure-cover", "cal-tab-assign", "cal-tab-toggle"], `${band}: the others, never the one in view`);
      assert.deepEqual(inRow("cover.living_one"), [["cal-tab-calibrate", "mdi:timer-outline"]], band);
      assert.deepEqual(menu("cover.living_one"), ["cal-tab-assign", "cal-tab-toggle"], "never calibrated: no Perfeziona");
      for (const element of tab().querySelectorAll(".cal-row")) {
        const shown = [...element.querySelectorAll(".cal-row-action")].map((button) => button.dataset.action);
        const folded = [...element.querySelectorAll(".cal-menu [role=menuitem]")].map((item) => item.dataset.action);
        assert.ok(shown.length <= 1, `${band}: at most one action in the row`);
        assert.ok(folded.length >= 2, "never a menu of one");
        assert.ok(shown.every((action) => !folded.includes(action)), "never twice");
      }
    }
    // Icons in the table's band only; the middle band's rows keep the text alone.
    assert.match(cascadeCss, /\.cal-row-action > ha-icon \{ display: none; \}\s*@container caltab \(min-width: 1016px\) \{ \.cal-row-action > ha-icon \{ display: inline-flex; \} \}/);
  });

  test("on a selected or tinted row a chip keeps its colour on the card's surface (light); dark keeps the tint", () => {
    for (const theme of ["light", "dark"]) {
      const host = html(`<div class="wiz" data-theme="${theme}"><div class="cal-batch-rows"><label class="cal-batch-row wiz-pick-row is-selected"><span class="cal-batch-chips">
      <span class="badge cal-badge" data-status="verified" data-tone="success">P</span><span class="badge cal-badge" data-status="calibrated">C</span><span class="badge cal-badge" data-status="to_measure">D</span>
      <span class="chip cal-profile-chip"></span></span></label><label class="cal-batch-row wiz-pick-row"><span class="badge cal-badge" data-status="calibrated">C</span></label></div></div>`);
      const [verified, calibrated, toMeasure, profile, plain] = host.querySelectorAll(".cal-badge, .cal-profile-chip");
      for (const chip of [verified, calibrated, profile]) {
        assert.equal(winner(chip, "background"), theme === "light" ? "var(--panel-surface)" : "color-mix(in srgb, var(--cal-chip) 12%, var(--panel-surface))", `${theme}: ${chip.textContent || "profile"}`);
        assert.notEqual(winner(chip, "border"), "", "its coloured border kept");
      }
      assert.equal(winner(toMeasure, "background"), "var(--panel-surface)", "«Da calibrare» white in both");
      assert.equal(winner(plain, "background"), "color-mix(in srgb, var(--cal-chip) 12%, var(--panel-surface))", "an unselected row keeps the tinted chip");
      host.remove();
    }
  });
});

describe("chips from the precision", async () => {
  const { translations } = await import("../../custom_components/myhome/frontend/panel/panel-translations.js");
  const { sampleOverview } = await import("./fake-calibration.mjs");
  const rowOf = (tab, id) => tab().querySelector(`.cal-row[data-entity="${id}"]`);
  const chipOf = (tab, id) => rowOf(tab, id).querySelector(".cal-badge");
  /** Living room shutter 3 after a basic calibration with slats (the tap's slat time, neutral rolls unknown). */
  function basicOverview(extra = {}) {
    const overview = sampleOverview();
    const three = overview.covers.find((cover) => cover.entity_id === "cover.living_three");
    const guided = { source: "guided", recorded_at: "2026-11-02T08:00:00+00:00", origin_entity_id: null, origin_name: null, inherited: false };
    const unknown = { source: "unknown", recorded_at: null, origin_entity_id: null, origin_name: null, inherited: false };
    Object.assign(three, { profile_id: null, precision: "basic", ...extra });
    Object.assign(three.effective, { opening: { value: 14.26, origin: "override", provenance: guided, scaled: false }, closing: { value: 14.03, origin: "override", provenance: guided, scaled: false },
      slat_time_s: { value: 2.7, origin: "override", provenance: guided, scaled: false }, opening_roll: { value: 1, origin: "override", provenance: unknown, scaled: false },
      closing_roll: { value: 1, origin: "override", provenance: unknown, scaled: false } });
    overview.profiles[0].assigned_to = ["cover.kitchen_two"];
    return overview;
  }

  test("the chips follow `precision`: «Da calibrare», «Stima di base» (accent, as «Calibrata»), «Calibrata», «Precisione N cm»; the filters and counts keep working", async () => {
    const overview = basicOverview();
    const { tab } = await mount({ language: "it", overview });
    assert.deepEqual(["cover.living_one", "cover.living_three", "cover.kitchen_two"].map((id) => [chipOf(tab, id).dataset.status, chipOf(tab, id).textContent]),
      // Kitchen shutter 2 follows a profile measured on another cover, without a check: «Dal profilo».
      [["to_measure", "Da calibrare"], ["basic", "Stima di base"], ["profile", "Dal profilo"]]);
    assert.match(tab().querySelector(".cal-tab-summary").textContent, /3 tapparelle · 2 calibrate · 1 da calibrare/, "a basic estimate is calibrated");
    tab().querySelector('[data-action="cal-tab-filter"][data-id="calibrated"]').click(); await tick();
    assert.deepEqual([...tab().querySelectorAll(".cal-row")].map((row) => row.dataset.entity), ["cover.kitchen_two", "cover.living_three"]);
    tab().querySelector('[data-action="cal-tab-filter"][data-id="to_measure"]').click(); await tick();
    assert.deepEqual([...tab().querySelectorAll(".cal-row")].map((row) => row.dataset.entity), ["cover.living_one"]);
    // A number only from a saved check.
    const t = (key) => translations.it[key] || key;
    const { statusBadge, coverStatus } = await import("../../custom_components/myhome/frontend/panel/panel-calibration-tab.js");
    assert.match(statusBadge(coverStatus({ effective: overview.covers[1].effective, precision: "verified", check_cm: 2, check_measured_cm: 97, check_expected_cm: 99 }), t, "it"), /data-status="verified" data-tone="success"[^>]*><ha-icon icon="mdi:check" aria-hidden="true"><\/ha-icon>Calibrata · 2 cm/);
    // Its title says what it means.
    assert.match(statusBadge(coverStatus({ effective: overview.covers[1].effective, precision: "basic" }), t, "it"), /data-status="basic" title="Tempi misurati\. Le posizioni intermedie sono stimate\.">Stima di base</);
  });
});

describe("chips and the phone's card", async () => {
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

  test("the list's profile chip has a crown on its source and a chain on a follower; the details' «Profilo» has the profile's own symbol on every cover, the role under the name; «Profilo usato anche da» lists the covers one under another", async () => {
    const context = await mount({ overview: sourceOverview(), language: "it" });
    const { tab } = context;
    const chipIcon = (root) => root.querySelector(".cal-profile-chip ha-icon").getAttribute("icon");
    assert.equal(chipIcon(row(tab, "cover.living_one")), "mdi:crown-outline", "the source: the crown");
    assert.equal(chipIcon(row(tab, "cover.kitchen_two")), "mdi:link-variant", "a follower: the chain");
    const symbol = (page) => page.querySelector(".cal-page-profile-head .cal-profile-icon ha-icon").getAttribute("icon");
    const source = await details(context, "cover.living_one");
    assert.equal(symbol(source), "mdi:clipboard-text-outline");
    assert.equal(source.querySelector(".cal-page-profile-head .cal-profile-chip"), null, "no chip in the card «Profilo»");
    assert.match(source.querySelector(".cal-page-profile-head .cal-page-role").textContent, /^Tempi misurati su questa tapparella · /);
    await pageTab(context.tab, "details");
    const followed = tab().querySelector(".cal-flow .cal-page-users");
    assert.equal(followed.querySelector(".cal-section-title").textContent, "Profilo usato anche da");
    assert.deepEqual([...followed.querySelectorAll(".cal-user .cal-user-link")].map((name) => name.textContent), ["Kitchen shutter 2", "Living room shutter 3"]);
    assert.equal(tab().querySelector(".cal-flow .cal-card-tail ha-icon"), null, "the source's tail has no chain: nothing comes from the profile");
    tab().querySelector(".cal-page-close").click(); await tick(6);
    const follower = await details(context, "cover.kitchen_two");
    assert.equal(symbol(follower), "mdi:clipboard-text-outline", "the same symbol on a follower");
    assert.match(follower.querySelector(".cal-page-role").textContent, /^Tempi misurati sulla tapparella Living room shutter 1/);
    const css = (await import("node:fs")).readFileSync(new URL("../../custom_components/myhome/frontend/panel/panel-calibration.css", import.meta.url), "utf8");
    assert.match(css, /\.cal-page-profile-head \.cal-profile-text \.cal-page-role \{[^}]*font-size: var\(--cal-fs-14\)/);
    assert.match(css, /\.cal-users > \.cal-user \{ display: flex; align-items: center;/);
  });

  test("the phone's card: no shutter icon, status and profile stacked at the left (20 + 4 + 20), the action 44 px and ⋮ centred on them; the profile's «…» only there", () => {
    const css = readFileSync(new URL("../../custom_components/myhome/frontend/panel/panel-calibration.css", import.meta.url), "utf8");
    const phone = css.slice(css.indexOf("@container caltab (max-width: 639px)"));
    assert.match(phone, /\.cal-row-lead \{ display: none; \}/);
    assert.match(phone, /\.cal-row-chips \{ flex: 999 1 auto; display: flex; flex-direction: column; align-items: flex-start; gap: 4px; min-width: 0; \}/);
    assert.match(phone, /\.cal-row-chips :is\(\.cal-badge, \.cal-profile-chip\) \{ min-height: 20px; padding-block: 1px; \}/);
    assert.match(phone, /\.cal-row-action \{ min-height: var\(--cal-target\); \}/);
    // The action beside the chips when it fits, else under them across the card (one flex flow).
    assert.match(phone, /\.cal-row-ctas \{ flex: 1 1 var\(--cal-cta-w\); width: auto; \}/);
  });

  test("a profile chip cut short runs its text in a loop while a fine pointer stays on it, back at the start when it leaves; nothing on touch; reduced motion keeps «…»; its whole name in title and label", async () => {
    const data = sourceOverview();
    data.profiles[0].name = "french_window_of_the_guest_room_on_the_garden_side";
    const proto = dom.window.HTMLElement.prototype;
    const widths = ["scrollWidth", "clientWidth"].map((name) => [name, Object.getOwnPropertyDescriptor(dom.window.Element.prototype, name)]);
    Object.defineProperty(proto, "scrollWidth", { configurable: true, get() { return this.classList?.contains("cal-chip-text") ? 300 : 0; } });
    Object.defineProperty(proto, "clientWidth", { configurable: true, get() { return this.classList?.contains("cal-chip-text") ? 180 : 0; } });
    const original = globalThis.matchMedia;
    globalThis.matchMedia = dom.window.matchMedia = (query) => ({ matches: query.includes("hover: hover"), addEventListener() {}, removeEventListener() {} });
    try {
      const context = await mount({ overview: data, language: "it" });
      const chip = row(context.tab, "cover.kitchen_two").querySelector(".cal-profile-chip");
      context.panel._calibrationTab._render(); await tick();
      const live = () => row(context.tab, "cover.kitchen_two").querySelector(".cal-profile-chip");
      assert.ok(live().hasAttribute("data-cut"), "cut short: marked");
      assert.deepEqual([live().getAttribute("title"), live().getAttribute("aria-label")], [data.profiles[0].name, data.profiles[0].name]);
      assert.equal(live().classList.contains("is-marquee"), false, "nothing moves by itself");
      live().querySelector(".cal-chip-run").dispatchEvent(new dom.window.MouseEvent("mouseover", { bubbles: true }));
      assert.ok(live().classList.contains("is-marquee"), "a pointer's passage: the text runs");
      assert.equal(live().style.getPropertyValue("--cal-chip-shift"), "-124px", "to the end (and back)");
      assert.equal(live().style.getPropertyValue("--cal-chip-dur"), "8.9s", "each way 30 % of the loop, about 45 px/s");
      const end = new dom.window.Event("animationend", { bubbles: true }); end.animationName = "cal-chip-marquee";
      live().querySelector(".cal-chip-run").dispatchEvent(end);
      assert.ok(live().classList.contains("is-marquee"), "a loop: it goes on while the pointer stays");
      live().querySelector(".cal-chip-run").dispatchEvent(new dom.window.MouseEvent("mouseout", { bubbles: true, relatedTarget: live() }));
      assert.ok(live().classList.contains("is-marquee"), "moving inside the chip is still on it");
      live().querySelector(".cal-chip-run").dispatchEvent(new dom.window.MouseEvent("mouseout", { bubbles: true, relatedTarget: context.tab() }));
      assert.equal(live().classList.contains("is-marquee"), false, "the pointer left: back at the start");
      void chip;
      const css = readFileSync(new URL("../../custom_components/myhome/frontend/panel/panel-calibration.css", import.meta.url), "utf8");
      assert.match(css, /@media \(hover: hover\) and \(pointer: fine\) \{[^@]*\.cal-profile-chip\.is-marquee \.cal-chip-run \{ display: inline-block; animation: cal-chip-marquee var\(--cal-chip-dur, 3s\) ease-in-out infinite; \}/);
      assert.match(css, /@keyframes cal-chip-marquee \{ 0%, 15% \{ transform: translateX\(0\); \} 45%, 60% \{ transform: translateX\(var\(--cal-chip-shift, 0px\)\); \} 90%, 100% \{ transform: translateX\(0\); \} \}/);
      assert.doesNotMatch(css, /\.cal-profile-chip\[data-cut\] \.cal-chip-text \{ overflow-x: auto/, "no sideways scroll under a finger any more");
      assert.match(css, /@media \(prefers-reduced-motion: reduce\) \{ \.cal-profile-chip\.is-marquee \.cal-chip-run \{ animation: none; \} \}/);
      // A touch screen (no fine pointer): nothing runs.
      live().dispatchEvent(new dom.window.MouseEvent("mouseout", { bubbles: true, relatedTarget: context.tab() }));
      globalThis.matchMedia = dom.window.matchMedia = (query) => ({ matches: query === "(hover: none)" });
      live().querySelector(".cal-chip-run").dispatchEvent(new dom.window.MouseEvent("mouseover", { bubbles: true }));
      assert.equal(live().classList.contains("is-marquee"), false, "touch: the «…» only");
      // Reduced motion: no run.
      globalThis.matchMedia = dom.window.matchMedia = (query) => ({ matches: true });
      live().querySelector(".cal-chip-run").dispatchEvent(new dom.window.MouseEvent("mouseover", { bubbles: true }));
      assert.equal(live().classList.contains("is-marquee"), false);
    } finally {
      for (const [name, descriptor] of widths) { delete proto[name]; if (descriptor) Object.defineProperty(dom.window.Element.prototype, name, descriptor); }
      globalThis.matchMedia = original; dom.window.matchMedia = original;
    }
  });
});

describe("a follower's chip", async () => {
  const { translations } = await import("../../custom_components/myhome/frontend/panel/panel-translations.js");
  const i18n = translations;
  /** «tall» measured on Living room shutter 1 (198 cm, its source), followed by the other two (110 cm / none of their own). */
  function sourceData({ followerTravel = [110, null], reference = 198 } = {}) {
    const data = sampleOverview();
    const profile = data.profiles[0];
    profile.reference_travel_cm = reference;
    profile.assigned_to = ["cover.living_one", "cover.kitchen_two", "cover.living_three"];
    const [one, two, three] = data.covers;
    Object.assign(one, { profile_id: "tall", travel_cm: reference, travel_edit: "reference" });
    for (const [cover, travel] of [[two, followerTravel[0]], [three, followerTravel[1]]]) {
      Object.assign(cover, { profile_id: "tall", travel_edit: reference ? "scaled" : "display" });
      if (travel == null) delete cover.travel_cm; else cover.travel_cm = travel;
    }
    profile.provenance.opening.origin_entity_id = "cover.living_one";
    return data;
  }
  async function page(context, id) {
    const target = [...context.tab().querySelectorAll("[data-action=cal-tab-toggle]")].find((button) => button.dataset.id.includes(id));
    target.click(); await tick(6);
    // The travel, the slats and the profile are on «Panoramica», the tab the card opens on.
    return context.tab().querySelector(".cal-flow .cal-page");
  }

  test("a follower of a profile measured elsewhere, without a check of its own, says «Dal profilo» in the list and the details; with its own check «Precisione N cm»; the source as it is; the counts as before", async () => {
    const data = sourceData();
    const [source, follower, other] = data.covers;
    Object.assign(source, { precision: "basic" });
    Object.assign(follower, { precision: "refined" });
    Object.assign(other, { precision: "verified", check_cm: 2, check_measured_cm: 98, check_expected_cm: 100 });
    const context = await mount({ overview: data, language: "it" });
    const chip = (id) => context.tab().querySelector(`.cal-row[data-entity="${id}"] .cal-badge`);
    assert.deepEqual(["cover.living_one", "cover.kitchen_two", "cover.living_three"].map((id) => [chip(id).dataset.status, chip(id).textContent]),
      [["basic", "Stima di base"], ["profile", "Dal profilo"], ["verified", "Calibrata · 2 cm"]]);
    assert.equal(i18n.en.calTabStatus_profile, "From profile");
    assert.match(context.tab().querySelector(".cal-tab-summary").textContent, /3 tapparelle · 3 calibrate · 0 da calibrare/);
    const shown = await page(context, "cover.kitchen_two");
    assert.equal(shown.querySelector(".cal-badge").textContent, "Dal profilo");
  });
});

describe("the empty list", async () => {
  const MEASURED_AT = "2026-11-01T16:09:00+00:00";
  const measured = { source: "guided", recorded_at: MEASURED_AT, origin_entity_id: null, origin_name: null, inherited: false };
  const timing = (value, origin = "profile", scaled = false) => ({ value, origin, provenance: measured, scaled });
  /** «tall» measured on Living room shutter 1 (its source), used by the three covers (listed out of order). */
  function overview() {
    const data = sampleOverview();
    const profile = data.profiles[0];
    profile.assigned_to = ["cover.living_three", "cover.kitchen_two", "cover.living_one"];
    profile.provenance.opening.recorded_at = MEASURED_AT;
    const [one] = data.covers;
    Object.assign(one, { profile_id: "tall", travel_cm: 198, precision: "basic", effective: { opening: timing(22.4), closing: timing(21.9) } });
    return data;
  }

  // «Valori del profilo e di questa tapparella» is no disclosure any more (a section of «Dettagli», always open);
  // what a reader opens and the reads keep is «Qualcosa non va?» from 1024 px (tested elsewhere).
  test("«Come funziona» under the empty list, opened, stays open through the tab's refreshes", async () => {
    const data = sampleOverview();
    data.covers = [];
    data.profiles = [];
    const context = await mount({ overview: data, language: "it" });
    const how = () => context.tab().querySelector(".cal-tab-empty .cal-empty-how");
    assert.ok(how(), "the empty state's disclosure");
    how().open = true;
    context.panel._calibrationTab.refresh();
    await tick(8);
    assert.equal(how().open, true);
  });
});

describe("the action in view", async () => {
  const { translations } = await import("../../custom_components/myhome/frontend/panel/panel-translations.js");
  const it = translations.it, en = translations.en;
  const MEASURED_AT = "2026-11-01T16:09:00+00:00";
  const measured = { source: "guided", recorded_at: MEASURED_AT, origin_entity_id: null, origin_name: null, inherited: false };
  const timing = (value, origin = "profile", scaled = false) => ({ value, origin, provenance: measured, scaled });
  /**
   * The render's family on the fake backend's covers: «tall» measured on Living room shutter 1 (the source), followed by
   * Kitchen shutter 2 (a travel of its own, 130 cm, slats) and Living room shutter 3 (no travel of its own, no slats).
   * `reference`: the profile's travel (null: unknown); `rolls`: the source fine-tuned with the tape; `check`: who keeps one.
   */
  function family({ reference = 110, rolls = false, check = [], half = true } = {}) {
    const data = sampleOverview();
    const profile = data.profiles[0];
    profile.reference_travel_cm = reference;
    profile.assigned_to = ["cover.living_one", "cover.kitchen_two", "cover.living_three"];
    profile.provenance.opening.recorded_at = MEASURED_AT;
    const [one, two, three] = data.covers;
    const roll = (value) => rolls ? { opening_roll: timing(value), closing_roll: timing(value + 0.1) } : {};
    Object.assign(one, { profile_id: "tall", travel_cm: reference, precision: rolls ? "refined" : "basic", slats_state: "yes_measured",
      effective: { opening: timing(21.8), closing: timing(20.4), slat_time_s: timing(1.4), ...roll(1.9) } });
    if (reference == null) delete one.travel_cm;
    Object.assign(two, { profile_id: "tall", travel_cm: 130, precision: rolls ? "refined" : "basic", slats_state: "yes_profile",
      effective: { opening: timing(24.6, "profile", reference != null), closing: timing(23.1, "profile", reference != null), slat_time_s: timing(1.4), ...roll(1.9) } });
    Object.assign(three, { profile_id: "tall", precision: "basic", slats_state: "no_profile", effective: { opening: timing(21.8), closing: timing(20.4) } });
    delete three.travel_cm;
    for (const cover of data.covers) {
      if (!check.includes(cover.entity_id)) continue;
      const travel = cover.travel_cm ?? reference ?? 100;
      Object.assign(cover, { check_cm: 1, check_measured_cm: travel * (half ? 0.5 : 0.75) + 1, check_expected_cm: travel * (half ? 0.5 : 0.75), check_at: "2026-11-04T09:30:00+00:00" });
      if (cover.precision !== "refined") cover.precision = "verified";
    }
    return data;
  }
  const row = (context, entity) => context.tab().querySelector(`.cal-row[data-entity="${entity}"]`);
  const card = (context) => context.tab().querySelector(".cal-flow .cal-page:not(.cal-assign-page)");
  async function open(context, entity) {
    row(context, entity).querySelector('[data-action="cal-tab-toggle"]').click();
    await tick(6);
    return card(context);
  }
  const click = async (context, action, id = null) => {
    const button = card(context).querySelector(`[data-action="${action}"]${id ? `[data-id="${id}"]` : ""}`);
    assert.ok(button, `${action} ${id ?? ""}`);
    button.click();
    await tick(6);
  };

  test("no «Controlla» in the list; a follower without a check has no action in view, ⋮ as before", async () => {
    const context = await mount({ language: "it", overview: family() });
    assert.equal(context.tab().querySelector('[data-action="cal-tab-fix"]'), null);
    assert.equal(row(context, "cover.kitchen_two").querySelector(".cal-row-action"), null);
    assert.deepEqual([...row(context, "cover.kitchen_two").querySelectorAll(".cal-menu [role=menuitem]")].map((item) => item.textContent.trim()), ["Ricalibra", "Cambia profilo", "Dettagli"]);
    assert.equal(it.calTabActionCheck, undefined);
    assert.equal(it.cal_S1_list_fix, undefined);
  });

  test("«Perfeziona» in view where the card offers it (the source, a cover without a profile), never in ⋮ then; never on a follower nor on a cover never calibrated; it opens the card's own flow", async () => {
    const data = family();
    data.covers[2].profile_id = null;
    data.profiles[0].assigned_to = ["cover.living_one", "cover.kitchen_two"];
    const context = await mount({ language: "it", overview: data });
    const menu = (entity) => [...row(context, entity).querySelectorAll(".cal-menu [role=menuitem]")].map((item) => [item.dataset.action, item.textContent.trim()]);
    const inView = (entity) => [...row(context, entity).querySelectorAll(".cal-row-action")].map((item) => [item.dataset.action, item.textContent.trim()]);
    const refine = ["cal-tab-refine", "Perfeziona"];
    assert.deepEqual([inView("cover.living_one"), menu("cover.living_one")], [[refine], [["cal-tab-measure-cover", "Ricalibra"], ["cal-tab-assign", "Cambia profilo"], ["cal-tab-toggle", "Dettagli"]]], "the source");
    assert.deepEqual([inView("cover.living_three"), menu("cover.living_three")], [[refine], [["cal-tab-measure-cover", "Ricalibra"], ["cal-tab-assign", "Assegna profilo"], ["cal-tab-toggle", "Dettagli"]]], "without a profile");
    assert.deepEqual([inView("cover.kitchen_two"), menu("cover.kitchen_two")], [[], [["cal-tab-measure-cover", "Ricalibra"], ["cal-tab-assign", "Cambia profilo"], ["cal-tab-toggle", "Dettagli"]]], "a follower: none");
    assert.equal(row(context, "cover.living_one").querySelector('.cal-row-action[data-action="cal-tab-refine"] ha-icon').getAttribute("icon"), "mdi:arrow-expand-vertical", "the straight ruler");
    // The same flow as the card's Perfeziona.
    await open(context, "cover.living_one");
    await click(context, "cal-tab-refine");
    const fromCard = context.code();
    context.wiz().querySelector(".wiz-head .wiz-x").click(); await tick(8);
    if (card(context)) { card(context).querySelector(".cal-page-close").click(); await tick(8); }
    const inRow = row(context, "cover.living_one").querySelector('.cal-row-action[data-action="cal-tab-refine"]');
    inRow.focus(); inRow.click(); await tick(8);
    assert.ok(context.wiz(), "the wizard");
    assert.equal(context.code(), fromCard, "the card's Perfeziona");
    assert.equal(context.tab().querySelector(".cal-flow .cal-page:not(.cal-assign-page)"), null, "straight to the flow, no card");
    context.wiz().querySelector(".wiz-head .wiz-x").click(); await tick(8);
    assert.equal(context.root.activeElement, row(context, "cover.living_one").querySelector('.cal-row-action[data-action="cal-tab-refine"]'), "back on its row's Perfeziona");
    reset();
    // Never calibrated: Calibra in view (profiles in the catalogue: it opens the card), no Perfeziona in ⋮.
    const fresh = await mount({ language: "it" });
    assert.equal(fresh.tab().querySelector('.cal-row[data-entity="cover.living_one"] .cal-menu [data-action="cal-tab-refine"]'), null);
    assert.equal(fresh.tab().querySelector('.cal-row[data-entity="cover.living_one"] .cal-row-action').dataset.action, "cal-tab-calibrate");
  });
});

describe("the check's chip and the sort", async () => {
  const { translations } = await import("../../custom_components/myhome/frontend/panel/panel-translations.js");
  const { coverStatus, statusBadge } = await import("../../custom_components/myhome/frontend/panel/panel-calibration-tab.js");
  const css = readFileSync(new URL("../../custom_components/myhome/frontend/panel/panel-calibration.css", import.meta.url), "utf8");
  const it = translations.it, en = translations.en;
  const MEASURED_AT = "2026-11-01T16:09:00+00:00";
  const measured = { source: "guided", recorded_at: MEASURED_AT, origin_entity_id: null, origin_name: null, inherited: false };
  const timing = (value, origin = "profile", scaled = false) => ({ value, origin, provenance: measured, scaled });
  /** «tall» measured on Living room shutter 1 (the source, slats), followed by Kitchen shutter 2 and Living room shutter 3. */
  function family() {
    const data = sampleOverview();
    const profile = data.profiles[0];
    profile.reference_travel_cm = 110;
    profile.assigned_to = ["cover.living_one", "cover.kitchen_two", "cover.living_three"];
    profile.provenance.opening.recorded_at = MEASURED_AT;
    const [one, two, three] = data.covers;
    Object.assign(one, { profile_id: "tall", travel_cm: 110, precision: "basic", slats_state: "yes_measured", effective: { opening: timing(21.8), closing: timing(20.4), slat_time_s: timing(1.4) } });
    Object.assign(two, { profile_id: "tall", travel_cm: 130, precision: "basic", slats_state: "yes_profile", effective: { opening: timing(24.6, "profile", true), closing: timing(23.1, "profile", true), slat_time_s: timing(1.4) } });
    Object.assign(three, { profile_id: "tall", precision: "basic", slats_state: "no_profile", effective: { opening: timing(21.8), closing: timing(20.4) } });
    delete three.travel_cm;
    return data;
  }
  const row = (context, entity) => context.tab().querySelector(`.cal-row[data-entity="${entity}"]`);

  test("a check reads «Calibrata · N cm», green within 4 cm, orange beyond; without a check «Calibrata» in the accent as before", () => {
    const t = (key) => it[key];
    const effective = { opening: timing(21.8, "cover"), closing: timing(20.4, "cover") };
    const chip = (cover) => statusBadge(coverStatus({ effective, ...cover }, null), t, "it");
    // The check's sentence in the chip's title (here no travel known: «a metà corsa» not said).
    assert.equal(chip({ precision: "verified", check_cm: 2, check_measured_cm: 57, check_expected_cm: 55 }),
      '<span class="badge cal-badge" data-status="verified" data-tone="success" title="Alla verifica si è fermata a 2 cm dal punto atteso."><ha-icon icon="mdi:check" aria-hidden="true"></ha-icon>Calibrata · 2 cm</span>');
    assert.equal(chip({ precision: "verified", check_cm: 6, check_measured_cm: 61, check_expected_cm: 55 }),
      '<span class="badge cal-badge" data-status="verified" data-tone="warning" title="Alla verifica si è fermata a 6 cm dal punto atteso. Oltre 4 cm conviene perfezionare il posizionamento."><ha-icon icon="mdi:check" aria-hidden="true"></ha-icon>Calibrata · 6 cm</span>');
    // Without a check the chip's title says what it means.
    assert.equal(statusBadge({ status: "calibrated" }, t, "it"), '<span class="badge cal-badge" data-status="calibrated" title="Tempi e posizioni misurati con il metro."><ha-icon icon="mdi:check" aria-hidden="true"></ha-icon>Calibrata</span>');
    assert.equal(statusBadge({ status: "verified", check: 2 }, (key) => en[key]), '<span class="badge cal-badge" data-status="verified" data-tone="success" title="At the check it stopped 2 cm from the expected point."><ha-icon icon="mdi:check" aria-hidden="true"></ha-icon>Calibrated · 2 cm</span>');
    // The tones are today's: the chip's status and tone rules unchanged.
    assert.match(css, /\.cal-badge\[data-status="verified"\] \{ --cal-chip: var\(--panel-success\); white-space: nowrap; \}/);
    assert.match(css, /\.cal-badge\[data-status="verified"\]\[data-tone="warning"\] \{ --cal-chip: var\(--panel-warning\);/);
  });

  test("in the list a checked cover counts among «Calibrate» and sorts after the calibrated ones by status", async () => {
    const data = family();
    Object.assign(data.covers[0], { precision: "verified", check_cm: 2, check_measured_cm: 57, check_expected_cm: 55, check_at: MEASURED_AT });
    const context = await mount({ language: "it", overview: data });
    await context.view?.("calibration");
    assert.equal(row(context, "cover.living_one").querySelector(".cal-badge").textContent, "Calibrata · 2 cm");
    const chip = [...context.tab().querySelectorAll('[data-action="cal-tab-filter"]')].find((button) => button.dataset.id === "calibrated");
    chip.click(); await tick(4);
    assert.ok(row(context, "cover.living_one"), "«Calibrate» lists it");
    const head = context.tab().querySelector('[data-action="cal-tab-sort"][data-id="status"]');
    head.click(); await tick(4);
    const order = [...context.tab().querySelectorAll(".cal-row")].map((line) => line.dataset.entity);
    assert.equal(order.at(-1), "cover.living_one", "the check last in the ascending order of status");
  });

  // The chips and the action no longer take the surface's white under the pointer.
  test("on the row under the pointer the chips and the row's action keep their own fill and a stronger border", () => {
    const start = css.indexOf("/* On the row under the pointer the whole band");
    assert.ok(start > 0);
    const rules = css.slice(start, css.indexOf("\n}\n", start) + 2);
    assert.match(rules, /@media \(hover: hover\) \{/);
    assert.match(rules, /\.cal-row \.cal-row-main:hover :is\(\.cal-badge, \.cal-profile-chip\) \{ border-color: color-mix\(in srgb, var\(--cal-chip\) 45%, var\(--panel-surface\)\); \}/);
    assert.match(rules, /\.cal-row \.cal-row-main:hover \.cal-row-action:not\(:hover, :disabled\) \{ border-color: color-mix\(in srgb, var\(--primary-text-color, #182b37\) 45%, var\(--panel-surface\)\); \}/);
    assert.doesNotMatch(rules, /background: var\(--panel-surface\)/, "no white fill");
  });
});

describe("the profile's chip", async () => {
  const MEASURED_AT = "2026-11-01T16:09:00+00:00", CHECKED_AT = "2026-11-01T18:01:00+00:00";
  const measured = { source: "guided", recorded_at: MEASURED_AT, origin_entity_id: null, origin_name: null, inherited: false };
  const inherited = { ...measured, inherited: true, origin_entity_id: "cover.living_one", origin_name: "Living room shutter 1" };
  const timing = (value, origin = "profile", scaled = false, provenance = measured) => ({ value, origin, provenance, scaled });
  /**
   * «tall» measured on Living room shutter 1 (the source, fine-tuned with the tape, slats, a check at half the travel
   * `gap` cm away), followed by Kitchen shutter 2 (a travel of its own, 115 cm, its times adapted); Living room shutter 3
   * never calibrated. `followers: false`: nobody follows the source.
   */
  function family({ gap = 2, half = true, followers = true } = {}) {
    const data = sampleOverview();
    const profile = data.profiles[0];
    profile.reference_travel_cm = 110;
    profile.geometry = { ...(profile.geometry || {}), slat_time_s: 1.4, opening_roll: 1.57, closing_roll: 2.54 };
    profile.assigned_to = followers ? ["cover.living_one", "cover.kitchen_two"] : ["cover.living_one"];
    profile.provenance.opening.recorded_at = MEASURED_AT;
    const [one, two, three] = data.covers;
    const expected = half ? 55 : 82.5;
    Object.assign(one, { profile_id: "tall", travel_cm: 110, precision: "verified", slats_state: "yes_measured", check_cm: Math.round(gap),
      check_expected_cm: expected, check_measured_cm: expected + gap, check_at: CHECKED_AT,
      effective: { opening: timing(21.8), closing: timing(20.4), slat_time_s: timing(1.4), opening_roll: timing(1.57), closing_roll: timing(2.54) } });
    if (followers) {
      Object.assign(two, { profile_id: "tall", travel_cm: 115, precision: "refined", slats_state: "yes_profile", travel_edit: "scaled",
        effective: { opening: timing(22.8, "profile", true, inherited), closing: timing(21.3, "profile", true, inherited), slat_time_s: timing(1.5, "profile", true, inherited),
          opening_roll: timing(1.59, "profile", true, inherited), closing_roll: timing(2.59, "profile", true, inherited) } });
    } else Object.assign(two, { profile_id: null });
    Object.assign(three, { profile_id: null, precision: "none", slats_state: "unknown", overrides: {},
      effective: { opening: { value: 20, origin: "default", provenance: { source: "unknown" } }, closing: { value: 20, origin: "default", provenance: { source: "unknown" } } } });
    delete three.travel_cm;
    return data;
  }
  const row = (context, entity) => context.tab().querySelector(`.cal-row[data-entity="${entity}"]`);

  test("the list's profile chip: the crown on the profile's source, the chain on a follower", async () => {
    const context = await mount({ language: "it", overview: family() });
    const mark = (entity) => row(context, entity).querySelector(".cal-profile-chip ha-icon").getAttribute("icon");
    assert.deepEqual([mark("cover.living_one"), mark("cover.kitchen_two")], ["mdi:crown-outline", "mdi:link-variant"]);
    assert.equal(row(context, "cover.living_three").querySelector(".cal-profile-chip"), null);
  });
});

describe("the status chips' icons", async () => {
  const { translations } = await import("../../custom_components/myhome/frontend/panel/panel-translations.js");
  const { checkSentence, statusBadge, ICON } = await import("../../custom_components/myhome/frontend/panel/panel-calibration-tab.js");
  const it = translations.it, en = translations.en;
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

  test("the status chips carry their icons: ✓ on «Calibrata…», the chain on «Dal profilo», none on «Da calibrare»; in the list and in the details", async () => {
    const t = (key) => it[key];
    const icon = (html) => html.match(/<ha-icon icon="([^"]+)"/)?.[1] ?? null;
    assert.equal(icon(statusBadge({ status: "calibrated" }, t, "it")), "mdi:check");
    assert.equal(icon(statusBadge({ status: "verified", check: 2 }, t, "it")), "mdi:check");
    assert.equal(icon(statusBadge({ status: "profile" }, t, "it")), "mdi:link-variant");
    assert.equal(icon(statusBadge({ status: "to_measure" }, t, "it")), null);
    assert.equal(icon(statusBadge({ status: "basic" }, t, "it")), null, "«Stima di base» has no icon");
    assert.match(css, /\.cal-badge > ha-icon \{ --mdc-icon-size: 14px; flex: none; \}/);
    phone();
    const data = family();
    data.covers[1].precision = "basic";
    const context = await mount({ language: "it", overview: data });
    const listed = (entity) => row(context, entity).querySelector(".cal-row-status .cal-badge ha-icon")?.getAttribute("icon") ?? null;
    // In the list «Dal profilo» goes without the chain (it stays on the profile's chip); in the details with it.
    assert.deepEqual([listed("cover.living_one"), listed("cover.kitchen_two"), listed("cover.living_three")], ["mdi:check", null, null]);
    const page = await open(context, "cover.kitchen_two");
    assert.deepEqual([page.querySelector(".cal-pos-state-chip .cal-badge").textContent, page.querySelector(".cal-pos-state-chip .cal-badge ha-icon").getAttribute("icon")],
      ["Adattata dal profilo", "mdi:link-variant"], "its own travel, adapted");
  });
});

describe("chips on one line", async () => {
  const PANEL = "../../custom_components/myhome/frontend/panel/";
  const { translations } = await import(`${PANEL}panel-translations.js`);
  const { statusBadge, adaptedFromProfile, sourceTimesDate, SAME_CALIBRATION_MS, ICON } = await import(`${PANEL}panel-calibration-tab.js`);
  const { fitStatus } = await import(`${PANEL}panel-calibration-layout.js`);
  const it = translations.it, en = translations.en;
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
   * 110 of the reference: adapted when they differ); Living room shutter 3 never calibrated.
   */
  function family({ travel = 130, refinedAfter = null } = {}) {
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
  async function open(context, entity, tab = "overview") {
    row(context, entity).querySelector('[data-action="cal-tab-toggle"]').click();
    await tick(6);
    if (tab !== "overview") await pageTab(context.tab, tab);
    return card(context);
  }

  test("a follower with its times adapted to its own travel says «Adattata dal profilo», in the list and the details; without adapting «Dal profilo»", async () => {
    const t = (key) => it[key];
    const data = family({ travel: 130 });
    const item = (entity, overview = data) => {
      const cover = overview.covers.find((entry) => entry.entity_id === entity), profile = overview.profiles[0];
      return { cover, profile: cover.profile_id ? profile : null, status: "profile", check: null };
    };
    assert.equal(adaptedFromProfile(item("cover.kitchen_two"), "it"), true);
    assert.match(statusBadge(item("cover.kitchen_two"), t, "it"), />Adattata dal profilo<\/span>$/);
    assert.equal(adaptedFromProfile(item("cover.kitchen_two", family({ travel: 110 })), "it"), false, "the same travel: nothing adapted");
    assert.match(statusBadge(item("cover.kitchen_two", family({ travel: 110 })), t, "it"), />Dal profilo<\/span>$/);
    assert.deepEqual([it.calTabStatus_profile_adapted, en.calTabStatus_profile_adapted], ["Adattata dal profilo", "Adapted from profile"]);
    phone();
    const context = await mount({ language: "it", overview: data });
    assert.equal(row(context, "cover.kitchen_two").querySelector(".cal-row-status .cal-badge").textContent, "Adattata dal profilo");
    const page = await open(context, "cover.kitchen_two");
    assert.equal(page.querySelector(".cal-pos-state-chip .cal-badge").textContent, "Adattata dal profilo");
  });

  test("in the list the status chip «Dal profilo» / «Adattata dal profilo» has no chain (the profile's chip keeps it); in the details it has", async () => {
    const t = (key) => it[key];
    assert.doesNotMatch(statusBadge({ status: "profile" }, t, "it", { chain: false }), /ha-icon/);
    assert.match(statusBadge({ status: "profile" }, t, "it"), /mdi:link-variant/);
    assert.match(statusBadge({ status: "calibrated" }, t, "it", { chain: false }), /mdi:check/, "the ✓ stays");
    phone();
    const context = await mount({ language: "it", overview: family() });
    const line = row(context, "cover.kitchen_two");
    assert.equal(line.querySelector(".cal-row-status .cal-badge ha-icon"), null);
    assert.equal(line.querySelector(".cal-row-profile .cal-profile-chip ha-icon").getAttribute("icon"), "mdi:link-variant");
    const page = await open(context, "cover.kitchen_two");
    assert.equal(page.querySelector(".cal-pos-state-chip .cal-badge ha-icon").getAttribute("icon"), "mdi:link-variant");
  });

  test("no chip wraps where it fits: one line, the list's status column as wide as its widest chip", () => {
    assert.match(css, /\.cal-chip, \.cal-badge, \.cal-profile-chip \{ white-space: nowrap; \}/);
    for (const selector of [".cal-row-status > .cal-badge", ".cal-batch-status .badge", ".cal-pos-state-chip > .cal-badge"]) {
      assert.match(css, new RegExp(`${selector.replace(/[.>]/g, (c) => `\\${c}`)} \\{ white-space: nowrap; \\}`), selector);
    }
    // Only on the phone's card, where the chip has the card's whole width, may a status chip wider than it wrap.
    const phoneAt = css.indexOf("@container caltab (max-width: 639px)"), phone = css.slice(phoneAt, css.indexOf("\n}\n", phoneAt));
    assert.doesNotMatch(css.replace(phone, ""), /\.cal-row-status > \.cal-badge \{[^}]*white-space: normal/);
    assert.match(css, /--cal-cols: minmax\(236px, 416px\) max\(124px, var\(--cal-status-w, 0px\)\) minmax\(240px, 1fr\)/, "the table: Stato 124 px or its widest chip");
    assert.match(css, /--cal-cols: minmax\(200px, 360px\) minmax\(max\(240px, var\(--cal-status-w, 0px\)\), 1fr\)/, "the rows: the chips' column 240 px or more");
    assert.doesNotMatch(css, /data-status-wide/, "no measured fallback on the phone's card any more");
    // Measured: the widest chip sets the column.
    const document = dom.window.document, host = document.createElement("div");
    host.innerHTML = `<div class="cal-list"><div class="cal-list-rows">${["a", "b"].map((id) => `<div class="cal-row" data-entity="${id}"><div class="cal-row-main" style="padding: 0px; column-gap: 0px">
    <span class="cal-row-chips"><span class="cal-row-status"><span class="badge cal-badge">x</span></span></span>
    <span class="cal-row-actions"><span class="cal-row-ctas"></span><span class="cal-menu-wrap"></span></span></div></div>`).join("")}</div></div>`;
    document.body.append(host);
    const widths = { a: 150, b: 170 };
    for (const element of host.querySelectorAll(".cal-row")) {
      const id = element.dataset.entity;
      element.querySelector(".cal-badge").getBoundingClientRect = () => ({ width: widths[id] });
      element.querySelector(".cal-row-ctas").getBoundingClientRect = () => ({ width: 92 });
      element.querySelector(".cal-menu-wrap").getBoundingClientRect = () => ({ width: 44 });
      Object.defineProperty(element.querySelector(".cal-row-main"), "clientWidth", { value: 300 });
    }
    fitStatus(host);
    const list = host.querySelector(".cal-list");
    assert.equal(list.style.getPropertyValue("--cal-status-w"), "170px");
    assert.deepEqual([...host.querySelectorAll(".cal-row")].map((element) => element.hasAttribute("data-status-wide")), [false, false], "no row marked wide");
    widths.b = 120;
    fitStatus(host);
    assert.equal(list.style.getPropertyValue("--cal-status-w"), "150px");
    host.remove();
  });
});

describe("the phone's card", async () => {
  const PANEL = "../../custom_components/myhome/frontend/panel/";
  const { fitStatus } = await import(`${PANEL}panel-calibration-layout.js`);
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
  const PHONE = block(/@container caltab \(max-width: 639px\) \{\s*\.cal-list \{/);

  test("the phone's card: the name across; the chips stacked at the left with the action and ⋮ beside them, or under them across the card", () => {
    assert.ok(PHONE);
    assert.match(PHONE, /\.cal-row-main \{ display: flex; flex-wrap: wrap; align-items: center; gap: 8px 12px;/);
    assert.match(PHONE, /\.cal-row-toggle \{ flex: 1 0 100%;/, "the name block on its own line");
    assert.match(PHONE, /\.cal-row-chips \{ flex: 999 1 auto; display: flex; flex-direction: column; align-items: flex-start; gap: 4px; min-width: 0; \}/, "status over profile, the line's spare room theirs");
    assert.match(PHONE, /\.cal-row-actions \{ display: flex; flex: 1 1 auto;/, "the action grows only alone on its line");
    assert.match(PHONE, /\.cal-row-ctas \{ flex: 1 1 var\(--cal-cta-w\); width: auto; \}/);
    assert.match(PHONE, /\.cal-menu-wrap \{ flex: none; \}/);
    assert.doesNotMatch(PHONE, /grid-area|grid-template-areas/, "no fixed places on the card");
    assert.match(cascadeCss, /\.cal-row-action \{ width: 100%;/, "the button fills its cell");
    assert.doesNotMatch(css, /data-status-wide/, "the measured fallback is gone");
    const source = readFileSync(new URL(`${PANEL}panel-calibration-layout.js`, import.meta.url), "utf8");
    assert.doesNotMatch(source.replace(/\/\*[\s\S]*?\*\//g, ""), /data-status-wide/);
  });

  test("no chip is ever cut: on the phone a chip wider than the card wraps inside it; in the table and the rows the profile's name wraps rather than «…»", async () => {
    phone();
    const context = await mount({ language: "it", overview: family({ travel: 130 }) });
    const wide = cascadeCss.replace(PHONE, "");
    const line = row(context, "cover.kitchen_two");
    const status = line.querySelector(".cal-row-status > .cal-badge"), chip = line.querySelector(".cal-row-profile .cal-profile-chip");
    const name = chip.querySelector(".cal-chip-text");
    assert.equal(status.textContent, "Adattata dal profilo");
    // The phone's card (the cascade ends on its rules).
    assert.deepEqual([winner(status, "white-space"), winner(status, "max-width")], ["normal", "100%"], "status: the card's width at most, wrapped beyond");
    assert.equal(winner(chip, "max-width"), "100%");
    assert.deepEqual([winner(name, "white-space"), winner(name, "overflow-wrap")], ["normal", "anywhere"], "profile: wrapped, never «…»");
    // The table and the rows.
    assert.equal(winner(status, "white-space", wide), "nowrap", "status: one line, its column grows");
    assert.deepEqual([winner(name, "white-space", wide), winner(name, "overflow-wrap", wide)], ["normal", "anywhere"], "profile: wrapped in its column, never «…»");
    assert.equal(winner(chip, "max-width", wide), "100%");
    // Measured by hand: a card 296 px wide, the profile's name 400 px long, the status chip 170: nothing moves to another
    // line by script any more, and the status column still follows the widest chip (the table, the rows).
    const document = dom.window.document, host = document.createElement("div");
    host.innerHTML = `<div class="cal-list"><div class="cal-list-rows"><div class="cal-row" data-entity="a"><div class="cal-row-main" style="padding: 0px; column-gap: 0px">
    <span class="cal-row-chips"><span class="cal-row-status"><span class="badge cal-badge">x</span></span><span class="cal-row-profile"><span class="chip cal-profile-chip"><span class="cal-chip-text">y</span></span></span></span>
    <span class="cal-row-actions"><span class="cal-row-ctas"></span><span class="cal-menu-wrap"></span></span></div></div></div></div>`;
    document.body.append(host);
    host.querySelector(".cal-badge").getBoundingClientRect = () => ({ width: 170 });
    host.querySelector(".cal-chip-text").getBoundingClientRect = () => ({ width: 400 });
    host.querySelector(".cal-row-ctas").getBoundingClientRect = () => ({ width: 252 });
    host.querySelector(".cal-menu-wrap").getBoundingClientRect = () => ({ width: 44 });
    Object.defineProperty(host.querySelector(".cal-row-main"), "clientWidth", { value: 296 });
    fitStatus(host);
    assert.equal(host.querySelector(".cal-list").style.getPropertyValue("--cal-status-w"), "170px");
    assert.equal(host.querySelector(".cal-row").hasAttribute("data-status-wide"), false);
    host.remove();
  });
});

describe("one action in view, ⋮ for the rest", async () => {
  const PANEL = "../../custom_components/myhome/frontend/panel/";
  const { translations } = await import(`${PANEL}panel-translations.js`);
  const { statusBadge } = await import(`${PANEL}panel-calibration-tab.js`);
  const it = translations.it, en = translations.en;
  const source = readFileSync(new URL(`${PANEL}panel-calibration-tab.js`, import.meta.url), "utf8");
  /** A window `width` px wide and 800 high for `matchMedia`, motion reduced. */
  const windowOf = (width) => (query) => ({ matches: query.split(",").some((part) => [...part.matchAll(/\(\s*([a-z-]+)\s*:\s*([a-z0-9-]+)\s*\)/g)].every(([, name, value]) =>
    name === "max-width" ? width <= parseFloat(value) : name === "min-width" ? width >= parseFloat(value) : name === "max-height" ? 800 <= parseFloat(value)
      : name === "min-height" ? 800 >= parseFloat(value) : name === "orientation" ? value === "landscape" : name === "prefers-reduced-motion")), addEventListener() {}, removeEventListener() {} });
  const row = (context, entity) => context.tab().querySelector(`.cal-row[data-entity="${entity}"]`);
  const card = (context) => context.tab().querySelector(".cal-flow .cal-page:not(.cal-assign-page)");
  async function open(context, entity) {
    row(context, entity).querySelector('[data-action="cal-tab-toggle"]').click();
    await tick(6);
    return card(context);
  }
  const plain = (value) => String(value ?? "").replace(/[  ]/g, " ").trim();
  /** A row's action in view and its ⋮, by their labels. */
  const actionsOf = (context, entity) => [[...row(context, entity).querySelectorAll(".cal-row-action")].map((button) => plain(button.textContent)),
    [...row(context, entity).querySelectorAll(".cal-menu [role=menuitem]")].map((item) => plain(item.textContent))];
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

  test("one action in view, only where a step is advised; ⋮ holds the rest, in a fixed order", async () => {
    const cases = [
      // [world, entity, in view, ⋮]
      [{ source: "basic" }, "cover.kitchen_two", ["Perfeziona"], ["Ricalibra", "Cambia profilo", "Dettagli"]],
      [{ alone: "basic" }, "cover.living_one", ["Perfeziona"], ["Ricalibra", "Assegna profilo", "Dettagli"]],
      [{ source: "refined" }, "cover.kitchen_two", ["Perfeziona"], ["Ricalibra", "Cambia profilo", "Dettagli"]],
      [{ sourceGap: 1 }, "cover.kitchen_two", [], ["Perfeziona", "Ricalibra", "Cambia profilo", "Dettagli"]],
      [{ sourceGap: 4 }, "cover.kitchen_two", [], ["Perfeziona", "Ricalibra", "Cambia profilo", "Dettagli"]],
      [{ sourceGap: 6 }, "cover.kitchen_two", ["Perfeziona"], ["Ricalibra", "Cambia profilo", "Dettagli"]],
      [{ alone: "basic", aloneGap: 6 }, "cover.living_one", ["Perfeziona"], ["Ricalibra", "Assegna profilo", "Dettagli"]],
      [{}, "cover.living_three", [], ["Ricalibra", "Cambia profilo", "Dettagli"]],
      [{ followerGap: 1 }, "cover.living_three", [], ["Ricalibra", "Cambia profilo", "Dettagli"]],
      [{ followerGap: 6 }, "cover.living_three", ["Ricalibra"], ["Cambia profilo", "Dettagli"]],
    ];
    for (const [options, entity, shown, menu] of cases) {
      const context = await mount({ language: "it", overview: world(options) });
      assert.deepEqual(actionsOf(context, entity), [shown, menu], `${JSON.stringify(options)} ${entity}`);
      reset();
    }
    // «Controlla» is gone: no action, no key.
    assert.doesNotMatch(source, /cal-tab-fix|calTabActionCheck|cal_S1_list_fix/);
    for (const table of [it, en]) assert.deepEqual([table.calTabActionCheck, table.cal_S1_list_fix], [undefined, undefined]);
  });

  test("a cover never calibrated: Calibra opens the wizard without a profile in the catalogue; with one, the card on «Come vuoi calibrarla?», its first choice focused; ⋮ Assegna profilo · Dettagli", async () => {
    const bare = await mount({ language: "it", overview: world({ profiles: false }) });
    assert.deepEqual(actionsOf(bare, "cover.living_one"), [["Calibra"], ["Assegna profilo", "Dettagli"]]);
    row(bare, "cover.living_one").querySelector('.cal-row-action[data-action="cal-tab-measure-cover"]').click(); await tick(6);
    assert.equal(bare.code(), "C-35", "the wizard, on this cover");
    assert.equal(card(bare), null, "no card");
    reset();
    for (const width of [390, 1280]) {
      globalThis.matchMedia = windowOf(width);
      const context = await mount({ language: "it", overview: world() });
      assert.deepEqual(actionsOf(context, "cover.living_one"), [["Calibra"], ["Assegna profilo", "Dettagli"]]);
      const calibrate = row(context, "cover.living_one").querySelector('.cal-row-action[data-action="cal-tab-calibrate"]');
      assert.equal(calibrate.querySelector("ha-icon").getAttribute("icon"), "mdi:timer-outline", "Calibra's stopwatch");
      calibrate.click(); await tick(6);
      assert.equal(context.wiz(), null, `${width}: no wizard`);
      const page = card(context);
      assert.equal(page.querySelector(".cal-page-body").dataset.tab, "overview");
      const first = page.querySelector(".cal-pos-how .cal-choice");
      assert.equal(first.dataset.action, "cal-tab-measure-cover");
      assert.equal(context.root.activeElement, first, `${width}: the focus on «Misura i tempi»`);
      reset();
    }
  });

  test("without the wizard no Calibra, Ricalibra nor Perfeziona: no action where none is left; a session's row as before", async () => {
    const context = await mount({ language: "it", overview: world({ source: "basic", followerGap: 6 }) });
    context.panel._calibrationTab.useWizard(null); context.panel._calibrationTab._render(); await tick();
    assert.deepEqual(actionsOf(context, "cover.kitchen_two"), [[], ["Cambia profilo", "Dettagli"]]);
    assert.deepEqual(actionsOf(context, "cover.living_three"), [[], ["Cambia profilo", "Dettagli"]]);
    assert.deepEqual(actionsOf(context, "cover.living_one"), [[], ["Assegna profilo", "Dettagli"]]);
    reset();
    const session = { entry_id: "one", entity_id: "cover.living_one", session_id: "session-1", sequence: 9, revision: 7, mode: "geometry", phase: "opening", step: "opening",
      reason: null, values: {}, samples: { lift: 4 }, readings: { gap: 3 }, geometry: {}, travel_cm: null, stop_requested: false, slats: true, lift_attempts: 1,
      still_resting: false, gap_warning: false, touching_cm: 1, max_gap_cm: 20, gap_warn_cm: 10, saved_travel_cm: 195, expected_cm: null, can_repeat: true, save_modes: ["new"] };
    const running = await mount({ language: "it", session, owner: false });
    const line = row(running, "cover.living_one");
    assert.deepEqual([...line.querySelectorAll(".cal-row-action")].map((button) => button.dataset.action), ["cal-tab-session"], "Segui, as today");
    assert.deepEqual([...line.querySelectorAll(".cal-menu [role=menuitem]")].map((item) => [item.dataset.action, item.disabled]),
      [["cal-tab-assign", true], ["cal-tab-toggle", false], ["cal-tab-abort", false]], "⋮ as today, the profile's action off");
  });

  test("without an action the row keeps its place: the actions' column (empty) in the table, ⋮ at the right on the phone's card", async () => {
    const context = await mount({ language: "it", overview: world() });
    const line = row(context, "cover.living_three");
    const actions = line.querySelector(".cal-row-main > .cal-row-actions");
    assert.ok(actions, "the actions' place");
    assert.equal(actions.querySelector(".cal-row-ctas"), null, "no action");
    assert.equal(actions.lastElementChild.className, "cal-menu-wrap", "⋮ in it");
    // The table's and the rows' grid keep the last column for it; it holds ⋮ at its end in every band.
    assert.match(cascadeCss, /\.cal-row-actions \{ grid-column: -2; \}/);
    assert.match(cascadeCss, /\.cal-row-actions \{ display: flex; gap: var\(--cal-sp-4\); align-items: center; justify-content: end; min-width: 0; \}/);
    assert.match(cascadeCss, /\.cal-row-actions \{ display: flex; flex: 1 1 auto; align-items: center; gap: 8px; \}/, "the phone: beside the chips, ⋮ at the end");
  });

  test("each status chip says in its title what it means (Italian, English); the check's chip keeps its sentence", async () => {
    assert.deepEqual([it.calTabStatusHint_to_measure, it.calTabStatusHint_basic, it.calTabStatusHint_calibrated, it.calTabStatusHint_profile, it.calTabStatusHint_profile_adapted], [
      "I tempi non sono ancora stati misurati.", "Tempi misurati. Le posizioni intermedie sono stimate.", "Tempi e posizioni misurati con il metro.",
      "Usa i tempi del profilo «{profile}».", "Usa i tempi del profilo «{profile}», adattati alla sua corsa."]);
    assert.deepEqual([en.calTabStatusHint_to_measure, en.calTabStatusHint_basic, en.calTabStatusHint_calibrated, en.calTabStatusHint_profile, en.calTabStatusHint_profile_adapted], [
      "The times have not been measured yet.", "Times measured. The intermediate positions are estimated.", "Times and positions measured with the tape measure.",
      "Uses the times of the profile \"{profile}\".", "Uses the times of the profile \"{profile}\", adapted to its travel."]);
    const t = (key) => it[key] ?? key;
    const title = (item) => new dom.window.DOMParser().parseFromString(statusBadge(item, t, "it"), "text/html").body.firstElementChild.getAttribute("title");
    assert.equal(title({ status: "to_measure" }), "I tempi non sono ancora stati misurati.");
    assert.equal(title({ status: "basic" }), "Tempi misurati. Le posizioni intermedie sono stimate.");
    assert.equal(title({ status: "calibrated" }), "Tempi e posizioni misurati con il metro.");
    // In the list and on the card: the follower's chip with its profile; the source's check its sentence.
    const context = await mount({ language: "it", overview: world({ sourceGap: 1 }) });
    const chip = (scope, entity) => row(scope, entity).querySelector(".cal-row-status .cal-badge");
    assert.equal(chip(context, "cover.living_one").title, "I tempi non sono ancora stati misurati.");
    assert.deepEqual([chip(context, "cover.living_three").textContent, chip(context, "cover.living_three").title], ["Dal profilo", "Usa i tempi del profilo «tall_two_leaf»."]);
    assert.match(chip(context, "cover.kitchen_two").title, /^Alla verifica .*si è fermata a 1 cm dal punto atteso\.$/, "the check's sentence, as today");
    reset();
    // «Adattata dal profilo»: the profile's times scaled to the follower's own travel.
    const adapted = world();
    for (const direction of ["opening", "closing"]) adapted.covers[2].effective[direction].scaled = true;
    const scaled = await mount({ language: "it", overview: adapted });
    assert.deepEqual([chip(scaled, "cover.living_three").textContent, chip(scaled, "cover.living_three").title],
      ["Adattata dal profilo", "Usa i tempi del profilo «tall_two_leaf», adattati alla sua corsa."]);
    const page = await open(scaled, "cover.living_three");
    assert.equal(page.querySelector(".cal-pos-state-chip .cal-badge").title, "Usa i tempi del profilo «tall_two_leaf», adattati alla sua corsa.", "on the card too");
  });

  test("under the pointer the row is one tint; chips and action keep their fill, a stronger border at 45 %; the rows in progress too", () => {
    assert.match(css, /@media \(hover: hover\) \{ \.cal-row-main:hover \{ background: color-mix\(in srgb, var\(--panel-accent\) 12%, var\(--panel-surface\)\); \} \}/, "the band at 12 %, as today");
    assert.match(css, /\.cal-row\.is-running \.cal-row-main:hover \{ background: color-mix\(in srgb, var\(--panel-warning\) 14%, transparent\); \}/, "a row in progress in its tone");
    const start = css.indexOf("/* On the row under the pointer the whole band");
    const rules = css.slice(start, css.indexOf("\n}\n", start) + 2);
    assert.match(rules, /\.cal-row \.cal-row-main:hover :is\(\.cal-badge, \.cal-profile-chip\) \{ border-color: color-mix\(in srgb, var\(--cal-chip\) 45%, var\(--panel-surface\)\); \}/);
    assert.match(rules, /\.cal-row \.cal-row-main:hover \.cal-row-action:not\(:hover, :disabled\) \{ border-color: color-mix\(in srgb, var\(--primary-text-color, #182b37\) 45%, var\(--panel-surface\)\); \}/);
    assert.match(rules, /\[data-theme="dark"\] \.cal-row \.cal-row-main:hover \.cal-badge\[data-status="running"\] \{ border-color: var\(--panel-warning\); \}/, "dark keeps the running chip's full border");
    assert.doesNotMatch(rules, /background/, "no fill of their own under the pointer: never the surface's white");
    assert.doesNotMatch(css, /\.cal-row-main:hover[^{]*\{[^}]*background: var\(--panel-surface\)/, "no rule left that whitens them");
  });
});

describe("bands, columns and rhythm", async () => {
  const layout = await import("../../custom_components/myhome/frontend/panel/panel-calibration-layout.js");
  const source = (name) => readFileSync(new URL(`../../custom_components/myhome/frontend/panel/${name}`, import.meta.url), "utf8");
  const rows = (tab) => [...tab().querySelectorAll(".cal-list-rows > .cal-row")];
  const row = (tab, id) => rows(tab).find((item) => item.dataset.entity === id);
  /** The list's section of the stylesheet: from its bands to the details. */
  const WIDE_HEAD = /@container caltab \(min-width: 640px\) \{\s*\.cal-tab \.cal-list:not\(\.cal-batch-list\) \{ border/;
  const listCss = cascadeCss.slice(cascadeCss.search(WIDE_HEAD), cascadeCss.indexOf(".cal-page { display: flex;"));
  const TABLE = block(/@container caltab \(min-width: 1016px\) \{\s*\.cal-tab \.cal-list:not/);
  const ROWS = block(/@container caltab \(min-width: 640px\) and \(max-width: 1015px\) \{\s*\.cal-tab \.cal-list:not/);
  const WIDE = block(WIDE_HEAD);
  const PHONE = block(/@container caltab \(max-width: 639px\) \{\s*\.cal-list \{/);
  const session = { entry_id: "one", entity_id: "cover.living_one", session_id: "session-1", sequence: 9, revision: 7, mode: "geometry", phase: "opening", step: "opening",
    reason: null, values: {}, samples: { lift: 4 }, readings: { gap: 3 }, geometry: {}, travel_cm: null, stop_requested: false, slats: true, lift_attempts: 1,
    still_resting: false, gap_warning: false, touching_cm: 1, max_gap_cm: 20, gap_warn_cm: 10, saved_travel_cm: 195, expected_cm: null, can_repeat: true, save_modes: ["new"] };

  test("two band breakpoints by the tab's content width (640 and 1016, measured), the script's and the stylesheet's the same numbers", () => {
    // 640: the rows' band holds the name at 200 px, a 34-character profile whole and the action (26 + 200 + 16 + 240 + 16 + 140).
    assert.deepEqual(layout.LIST_BANDS, { table: 1016, rows: 640 });
    assert.ok(TABLE && ROWS && WIDE && PHONE, "one block per band, plus the rules the two wide bands share");
    assert.deepEqual([320, 639, 640, 1015, 1016, 1600].map(layout.listBand), ["cards", "cards", "rows", "rows", "table", "table"]);
    const queries = [...listCss.matchAll(/@container caltab ([^{]+)\{/g)].map(([, query]) => query.trim());
    assert.deepEqual(queries, ["(min-width: 640px)", "(min-width: 1016px)", "(min-width: 640px) and (max-width: 1015px)", "(max-width: 639px)"]);
  });

  test("one column template and one gap per band, shared by the header and the rows; the name between a minimum and a maximum, the chips' column takes the rest", () => {
    assert.match(TABLE, /\.cal-tab \.cal-list:not\(\.cal-batch-list\) \{ --cal-cta-w: var\(--cal-cta-icon\); --cal-cols: minmax\(236px, 416px\) max\(124px, var\(--cal-status-w, 0px\)\) minmax\(240px, 1fr\) calc\(var\(--cal-cta-w\) \+ 48px\); --cal-col-gap: 24px; --cal-lead-gap: 16px; \}/,
      "Tapparella (icon 40 + 16 + name 180–360) · Stato 124 or its widest chip · Profilo ≥ 240 (a 34-character profile whole) · the action with its icon + 4 + ⋮ 44; 24 px between all");
    assert.match(ROWS, /\.cal-tab \.cal-list:not\(\.cal-batch-list\) \{ --cal-cols: minmax\(200px, 360px\) minmax\(max\(240px, var\(--cal-status-w, 0px\)\), 1fr\) calc\(var\(--cal-cta-w\) \+ 48px\); --cal-col-gap: 16px; \}/,
      "the name 200–360 · the stacked chips ≥ 240 · the action (its label alone) and ⋮; 16 px between all");
    assert.equal((cascadeCss.match(/--cal-cols:/g) || []).length, 2, "defined once per wide band, nowhere else");
    assert.equal((cascadeCss.match(/var\(--cal-cols\)/g) || []).length, 1, "used once");
    assert.equal((cascadeCss.match(/--cal-col-gap:/g) || []).length, 2, "one gap per wide band");
    assert.match(WIDE, /\.cal-list-head, \.cal-row-main \{ grid-template-columns: var\(--cal-cols\); grid-template-areas: none; column-gap: var\(--cal-col-gap\); \}/, "the header and every row: the same columns, one gap between all of them");
    // Nothing sized by its content: no subgrid, no max-content or auto track, nothing of the viewport or the container.
    for (const banned of [/subgrid/, /max-content/, /min-content/, /fit-content/]) assert.doesNotMatch(listCss, banned);
    for (const template of [...cascadeCss.matchAll(/--cal-cols: ([^;]+);/g)].map(([, value]) => value)) {
      assert.doesNotMatch(template, /\bauto\b|cqi|clamp/);
      const [name] = template.match(/^minmax\((\d+)px, (\d+)px\)/) ? [template.match(/^minmax\((\d+)px, (\d+)px\)/)] : [];
      assert.ok(name && Number(name[2]) <= 416, `the name's column first, with a maximum (it wraps past it): ${template}`);
      assert.equal((template.match(/1fr/g) || []).length, 1, "one flexible track: the chips', after the name's maximum");
      assert.match(template, /minmax\((240px|max\(240px, var\(--cal-status-w, 0px\)\)), 1fr\)/, "the chips' column at least 240 (or the widest status chip)");
    }
    // The phone's card: one flow, the content decides: the name across, then the chips and the action beside them
    // or under them.
    assert.match(PHONE, /\.cal-row-main \{ display: flex; flex-wrap: wrap; align-items: center; gap: 8px 12px;/);
  });

  test("the action's width is its language's longest label (Calibra, Perfeziona, Segui, Riprendi), padding 12, the icon in the table only; never «…», never two lines", async () => {
    assert.match(cascadeCss, /\.cal-list \{ --cal-cta-text: 84px; --cal-cta-icon: 110px; --cal-cta-w: var\(--cal-cta-text\); \}/, "English, and every language without its own pair");
    assert.match(cascadeCss, /\.cal-list:lang\(it\) \{ --cal-cta-text: 92px; --cal-cta-icon: 118px; \}/, "Italian: «Perfeziona»");
    for (const language of Object.keys(translations).filter((code) => code !== "en")) assert.match(cascadeCss, new RegExp(`\\.cal-list:lang\\(${language}\\) \\{ --cal-cta-text: \\d+px; --cal-cta-icon: \\d+px; \\}`), `${language}: its own widths`);
    assert.match(rule(cascadeCss, ".cal-row-action"), /width: 100%; min-height: 40px; padding-inline: var\(--cal-sp-12\); background: transparent; font-weight: 600; white-space: nowrap;/);
    assert.doesNotMatch(cascadeCss, /--cal-cta-wrap/, "the label never wraps, in any band");
    // The list says its language (the widths follow it); a session's row shows one word, its whole label in the title.
    const { tab } = await mount({ language: "it", session, owner: false });
    assert.equal(tab().querySelector(".cal-list").getAttribute("lang"), "it");
    const follow = row(tab, "cover.living_one").querySelector(".cal-row-action");
    assert.deepEqual([follow.dataset.action, follow.textContent, follow.title, follow.getAttribute("aria-label")], ["cal-tab-session", "Segui", "Segui sessione", "Segui sessione"]);
    // The labels the action can show, each a single word: none longer than «Perfeziona» / «Fine-tune» decides the widths.
    // («Controlla», calTabActionCheck, left the list.)
    for (const language of ["it", "en"]) for (const key of ["calTabActionMeasure", "calTabActionRefine", "calTabRowFollow", "calTabRowResume"]) assert.doesNotMatch(translations[language][key], /\s/, `${language} ${key}: one word`);
  });

  test("the action in view is «Calibra» on a cover never calibrated; a follower without a check has none; «Ricalibra» in ⋮", async () => {
    const { tab } = await mount({ language: "it" });
    for (const element of rows(tab)) {
      const action = element.querySelector(".cal-row-action"), menu = [...element.querySelectorAll(".cal-menu [role=menuitem]")].map((item) => item.dataset.action);
      const status = element.querySelector(".cal-badge").dataset.status;
      if (status === "to_measure") { assert.deepEqual([action.dataset.action, action.textContent], ["cal-tab-calibrate", "Calibra"]); assert.ok(!menu.includes("cal-tab-refine")); }
      else {
        assert.equal(action, null, element.dataset.entity);
        assert.deepEqual(menu, ["cal-tab-measure-cover", "cal-tab-assign", "cal-tab-toggle"], "⋮ as before: Ricalibra first");
      }
    }
    assert.equal(tab().querySelector('[data-action="cal-tab-fix"]'), null);
  });

  test("nothing in the list is measured by a script (no actions' width, no chips' box, no per-row class or inline size)", async () => {
    assert.equal(layout.fitActions, undefined, "the actions' column is a length, not the widest row's");
    const tabSource = source("panel-calibration-tab.js");
    for (const gone of ["fitActions", "_measureCards", "_clearMeasured", "--cal-actions-w"]) assert.ok(!tabSource.includes(gone), gone);
    assert.doesNotMatch(cascadeCss, /--cal-actions-w|--cal-chips-w/);
    const { tab } = await mount({ language: "it" });
    const styled = () => [tab().querySelector(".cal-list"), ...tab().querySelectorAll(".cal-list *")].filter((element) => element.hasAttribute("style"));
    assert.deepEqual(styled(), [], "no inline size anywhere in the list");
    for (const element of rows(tab)) assert.deepEqual([...element.classList], ["cal-row"], "no class chosen per row");
    // A rotation switches the tab's container off and on; afterwards nothing is left inline.
    window.dispatchEvent(new Event("orientationchange"));
    await new Promise((resolve) => setTimeout(resolve, 600)); await tick();
    assert.equal(tab().getAttribute("style"), null, "the container is back to the stylesheet's");
    assert.deepEqual(styled(), [], "the list's own layer is gone after one frame, nothing measured");
  });

  test("a cover's name wraps (a long word anywhere), never «…»; the line under it keeps each address pair whole", async () => {
    const { tab } = await mount({ language: "it" });
    const strong = row(tab, "cover.kitchen_two").querySelector(".cal-row-name strong");
    assert.equal(winner(strong, "white-space"), "normal");
    assert.equal(winner(strong, "overflow-wrap"), "anywhere");
    assert.equal(winner(strong, "text-overflow"), "", "no ellipsis in any band");
    assert.equal(winner(strong, "line-height"), "var(--cal-lh-14)");
    assert.doesNotMatch(cascadeCss, /\.cal-row-name strong \{[^}]*(nowrap|ellipsis)/);
    assert.doesNotMatch(cascadeCss, /hyphens: auto/, "no hyphenation of ordinary words");
    const parts = [...row(tab, "cover.kitchen_two").querySelectorAll(".cal-row-address-part")];
    assert.ok(parts.length === 3 && parts.every((part) => winner(part, "white-space") === "nowrap"), "«Indirizzo: 91 ·», «A: 9 ·», «PL: 1»");
  });

  test("no chip is ever cut: a profile's name wider than its place wraps in its chip, whole in the title; the status chip on one line in the table and the rows (its column grows), on the phone's card on a line of its own", async () => {
    const { tab } = await mount({ language: "it", session, owner: false });
    const wide = cascadeCss.replace(PHONE, "");
    for (const chip of tab().querySelectorAll(".cal-row-profile .cal-profile-chip")) {
      const text = chip.querySelector(".cal-chip-text");
      assert.equal(chip.getAttribute("title"), text.textContent, "the whole name in the title");
      for (const css of [wide, cascadeCss]) assert.deepEqual([winner(text, "white-space", css), winner(text, "overflow-wrap", css)], ["normal", "anywhere"], "wraps, never «…»");
      assert.equal(winner(chip, "max-width"), "100%", "never wider than its place");
    }
    const badges = [...tab().querySelectorAll(".cal-row-status > .cal-badge")];
    assert.ok(badges.some((badge) => badge.dataset.status === "running"), "the session's row is drawn");
    for (const badge of badges) {
      assert.equal(winner(badge, "white-space", wide), "nowrap", `${badge.dataset.status}: one line in the table and the rows`);
      assert.notEqual(winner(badge, "max-width", wide), "100%", "never squeezed into a narrower column");
      assert.deepEqual([winner(badge, "white-space"), winner(badge, "max-width")], ["normal", "100%"], `${badge.dataset.status}: on the phone, wider than the card, it wraps`);
      assert.notEqual(winner(badge, "text-overflow"), "ellipsis");
    }
    // The rule that cuts with «…» in the list: the profile chip's text only.
    const cutting = [...listCss.matchAll(/(?<=^|[{}])\s*([^{}@]+?)\s*\{([^{}]*text-overflow: ellipsis[^{}]*)\}/g)].map(([, selector]) => selector);
    assert.deepEqual(cutting, [], "no «…» among the list's rules");
    assert.match(rule(cascadeCss, ".cal-chip-text"), /overflow: hidden; text-overflow: ellipsis; white-space: nowrap;/);
  });

  test("at most one action and always ⋮ on every row of every band (a session's row included, and without the wizard), the action in a column of fixed width", async () => {
    const { tab, panel } = await mount({ language: "it", session, owner: false });
    const check = (label) => {
      for (const element of rows(tab)) {
        const actions = element.querySelectorAll(".cal-row-ctas > .cal-row-action"), menu = element.querySelectorAll('[data-action="cal-tab-menu"]');
        assert.ok(actions.length <= 1, `${label} ${element.dataset.entity}: at most one action`);
        assert.equal(menu.length, 1, `${label} ${element.dataset.entity}: ⋮`);
        assert.ok(element.querySelector(".cal-row-main > .cal-row-actions"), `${label} ${element.dataset.entity}: the actions' place stays`);
        const shown = actions[0]?.dataset.action;
        assert.ok(![...element.querySelectorAll(".cal-menu [role=menuitem]")].some((item) => item.dataset.action === shown), "never twice");
      }
    };
    for (const band of ["cards", "rows", "table"]) { tab().dataset.band = band; panel._calibrationTab._render(); await tick(); check(band); }
    assert.equal(row(tab, "cover.living_one").querySelector(".cal-row-action").dataset.action, "cal-tab-session", "Segui in the session's row");
    panel._calibrationTab.useWizard(null); panel._calibrationTab._render(); await tick();
    check("no wizard");
    // In the table and the rows the action fills its language's width, so its edges match on every row (on the
    // phone's card, the card's width beside ⋮).
    assert.match(rule(cascadeCss, ".cal-row-ctas"), /display: flex; flex: none; width: var\(--cal-cta-w\); min-width: 0;/);
    assert.match(rule(cascadeCss, ".cal-row-action"), /width: 100%;/);
    assert.match(WIDE, /\.cal-row-actions \{ grid-column: -2; \}/, "always the last column");
    assert.match(rule(cascadeCss, ".cal-row-actions"), /gap: var\(--cal-sp-4\); align-items: center; justify-content: end;/);
  });

  test("chips at the start of their column: inline in the table, stacked in the rows' band and on the phone", () => {
    assert.match(TABLE, /\.cal-row-chips \{ display: contents; \}/);
    assert.match(TABLE, /\.cal-row-status \{ grid-column: 2; justify-self: start; \}/);
    assert.match(TABLE, /\.cal-row-profile, \.cal-row-profile:not\(:has\(\.cal-profile-chip\)\) \{ display: block; grid-column: 3; justify-self: start; \}/, "«—» keeps the column when there is no profile");
    assert.match(ROWS, /\.cal-row-chips \{ grid-column: 2; display: flex; flex-direction: column; align-items: flex-start; justify-content: center; gap: 6px; min-width: 0; \}/, "status over profile, even one alone");
    assert.match(ROWS, /\.cal-row-lead \{ display: none; \}/, "no icon: the name keeps the room");
    // On the phone the status and the profile stacked at the left, 20 + 4 + 20 = 44 px, no shutter icon; the action
    // (44 px) across the card under them, ⋮ at its right.
    assert.match(PHONE, /\.cal-row-chips \{ flex: 999 1 auto; display: flex; flex-direction: column; align-items: flex-start; gap: 4px; min-width: 0; \}/);
    assert.match(PHONE, /\.cal-row-chips :is\(\.cal-badge, \.cal-profile-chip\) \{ min-height: 20px; padding-block: 1px; \}/);
    assert.match(PHONE, /\.cal-row-ctas \{ flex: 1 1 var\(--cal-cta-w\); width: auto; \}\s*\.cal-row-action \{ min-height: var\(--cal-target\); \}\s*\.cal-menu-wrap \{ flex: none; \}/);
    assert.match(PHONE, /\.cal-row-lead \{ display: none; \}/, "no icon on the phone either");
    assert.match(rule(cascadeCss, ".cal-row-profile:not(:has(.cal-profile-chip))"), /display: none;/, "no profile: no third line");
    assert.doesNotMatch(ROWS + TABLE, /flex-wrap: wrap/, "nothing wraps row by row in the table's bands");
    assert.match(PHONE, /\.cal-row-main \{ display: flex; flex-wrap: wrap;/, "on the phone the action wraps under the chips only when it does not fit beside them");
  });

  test("the rhythm (12 px around every row, 68 px at least from 640) and the header in the table's band only, over its columns", () => {
    assert.match(rule(cascadeCss, ".cal-row-main"), /padding: var\(--cal-sp-12\);/);
    assert.match(WIDE, /\.cal-row-main \{ min-height: 68px; row-gap: 0; \}/);
    assert.match(rule(cascadeCss, ".cal-list-head"), /^display: none;/, "no header outside the table");
    assert.match(TABLE, /\.cal-list-head \{ display: grid; align-items: center; box-sizing: border-box; min-height: var\(--cal-target\); padding: 0 var\(--cal-sp-12\);[^}]*background: var\(--cal-tail\);/);
    assert.match(TABLE, /\.cal-list-head > \.cal-sort-cell:nth-child\(2\) \{ padding-inline-start: calc\(40px \+ var\(--cal-lead-gap\)\); \}/, "«Tapparella» over the names");
    assert.doesNotMatch(ROWS + PHONE, /\.cal-list-head \{[^}]*display: grid/);
  });
});
