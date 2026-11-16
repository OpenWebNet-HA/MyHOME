/**
 * A cover's card, «Panoramica»: the tabs, «Stato», «Caratteristiche», «Profilo», and the two columns from 1024 px.
 */
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { after, afterEach, describe, test } from "node:test";

import { block, cascadeCss, winner } from "./css-cascade.mjs";
import { sampleOverview } from "./fake-calibration.mjs";
import { panelCss as css } from "./panel-css.mjs";
import { dom, mount, pageTab, reset, tick } from "./wizard-harness.mjs";

const original = globalThis.matchMedia;
afterEach(() => { globalThis.matchMedia = original; reset(); });
after(() => dom.window.close());

describe("the card's blocks", async () => {
  const row = (tab, id) => [...tab().querySelectorAll(".cal-row")].find((item) => item.dataset.entity === id);

  test("the card's blocks: «Stato», «Caratteristiche», «Profilo» on «Panoramica», the table and the history on «Dettagli»; titles of one size (16/600)", async () => {
    const { tab } = await mount({ language: "it" });
    row(tab, "cover.kitchen_two").querySelector(".cal-row-toggle").click(); await tick();
    const titles = () => [...tab().querySelectorAll(".cal-page-body section > .cal-section-title")].map((title) => title.textContent);
    assert.deepEqual(titles(), ["Stato", "Caratteristiche", "Profilo"], "the phone's order, as the two columns read");
    await pageTab(tab, "details");
    assert.deepEqual(titles(), ["Valori del profilo e di questa tapparella", "Ultima calibrazione", "Ultima verifica", "Profilo usato anche da"]);
    const medium = cascadeCss.slice(cascadeCss.indexOf("@media (min-width: 1024px) {\n  .cal-page-body"));
    const block = medium.slice(0, medium.indexOf("\n}\n"));
    assert.match(block, /\.cal-page-body\.is-wide:not\(\.cal-check\) \{ display: grid; grid-template-columns: repeat\(2, minmax\(0, 1fr\)\); align-items: start;/);
    assert.doesNotMatch(block, /grid-template-areas: "overview values"|grid-area: (overview|values|last)/, "no area spanning rows");
    assert.doesNotMatch(block, /cal-page-link|"link/, "no group of the profile's connection");
    assert.match(block, /\.cal-page-body \.cal-section-title \{ font-size: var\(--cal-fs-16\); line-height: var\(--cal-lh-16\); font-weight: 600; \}/);
    assert.doesNotMatch(cascadeCss, /\.cal-page-title/, "no page title over the blocks any more");
  });
});

describe("a basic estimate", async () => {
  const { sampleOverview } = await import("./fake-calibration.mjs");
  const rowOf = (tab, id) => tab().querySelector(`.cal-row[data-entity="${id}"]`);
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

  test("the details of a basic estimate: «Come · Procedura guidata», no coefficients on «Panoramica», the chip under the text, «Perfeziona» opens fine-tuning with the tape", async () => {
    const { tab, wiz, code } = await mount({ language: "it", overview: basicOverview() });
    rowOf(tab, "cover.living_three").querySelector(".cal-row-toggle").click(); await tick();
    let page = tab().querySelector(".cal-page");
    // The slat time is the third «Misura» of «Caratteristiche».
    assert.deepEqual([...page.querySelectorAll(".cal-page-times .cal-measure")].map((item) => [item.querySelector(".cal-label-long").textContent, item.querySelector(".cal-measure-number").textContent]),
      [["Tempo di salita", "14,3"], ["Tempo di discesa", "14,0"], ["Tempo lamelle", "2,7"]]);
    assert.doesNotMatch(page.textContent, /Coefficiente/, "no coefficients on «Panoramica»");
    const chip = page.querySelector(".cal-pos-state-chip");
    assert.deepEqual([...chip.children].map((child) => child.className), ["badge cal-badge"], "the chip alone");
    assert.equal(chip.textContent, "Stima di base");
    // No footer; «Assegna profilo» in the card «Profilo», Perfeziona the main action.
    // One button.
    assert.deepEqual([...page.querySelectorAll(".cal-page-link button")].map((button) => [button.dataset.action, button.textContent]),
      [["cal-tab-assign", "Assegna profilo"]]);
    await pageTab(tab, "details");
    page = tab().querySelector(".cal-page");
    const pairs = Object.fromEntries([...page.querySelectorAll(".cal-page-last .cal-pairs > div")].map((item) => [item.querySelector("dt").textContent, item.querySelector("dd").textContent]));
    assert.equal(pairs.Come, "Procedura guidata");
    assert.match(pairs.Data, /2026/, "the date is the «Data» row");
    await pageTab(tab, "overview");
    tab().querySelector('.cal-page .cal-cta [data-action="cal-tab-refine"]').click(); await tick();
    assert.equal(code(), "A-03");
    assert.equal(wiz().querySelector(".wiz-head-phase").textContent, "Perfeziona · Preparazione · 1 di 4");
    // A times-only basic estimate (no slats): «tempi misurati»; a cover never calibrated keeps «—».
    const { coverStatus } = await import("../../custom_components/myhome/frontend/panel/panel-calibration-tab.js");
    const timesOnly = sampleOverview().covers.find((cover) => cover.entity_id === "cover.living_three");
    assert.deepEqual([coverStatus(timesOnly).status, coverStatus(timesOnly).detail], ["basic", "times"]);
  });
});

describe("source, follower, no profile", async () => {
  const { translations } = await import("../../custom_components/myhome/frontend/panel/panel-translations.js");
  const { dateTimeText } = await import("../../custom_components/myhome/frontend/panel/panel-calibration-text.js");
  const en = translations.en, it = translations.it;
  const head = (wiz) => wiz().querySelector(".wiz-head-phase").textContent;
  const body = (wiz) => wiz().querySelector(".wiz-body").textContent;
  const { sampleOverview } = await import("./fake-calibration.mjs");
  const { cascadeCss } = await import("./css-cascade.mjs");
  /** Living room shutter 1 measured «tall_two_leaf» (the source); Kitchen shutter 2 follows it, its travel and rolls its own. */
  function v1Overview({ slats = 4.3 } = {}) {
    const overview = sampleOverview(), profile = overview.profiles[0];
    const guided = { source: "guided", recorded_at: "2026-10-31T11:18:00+00:00", origin_entity_id: null, origin_name: null, inherited: false };
    const own = (value) => ({ value, origin: "override", provenance: guided, scaled: false });
    const one = overview.covers.find((cover) => cover.entity_id === "cover.living_one"), two = overview.covers.find((cover) => cover.entity_id === "cover.kitchen_two");
    profile.assigned_to = ["cover.living_one", "cover.kitchen_two", "cover.living_three"];
    profile.provenance.opening.recorded_at = "2026-10-31T11:18:00+00:00";
    Object.assign(one, { profile_id: "tall", travel_cm: 197, precision: "verified", check_cm: 0.3, check_measured_cm: 98.8, check_expected_cm: 98.5,
      // A calibration saved on the source clears its overrides: its times come from the profile it measured.
      effective: { opening: { ...own(22.3), origin: "profile" }, closing: { ...own(21.6), origin: "profile" }, slat_time_s: own(slats), opening_roll: own(1.94), closing_roll: own(2.58) } });
    Object.assign(two, { travel_cm: 184 });
    two.effective.slat_time_s.value = slats;
    two.effective.closing_roll = own(2.6);
    return overview;
  }
  async function details(entity, options = {}) {
    const context = await mount({ language: "it", overview: v1Overview(options), ...options });
    context.tab().querySelector(`.cal-row[data-entity="${entity}"] .cal-row-toggle`).click(); await tick();
    // The card opens on «Panoramica»; `more()` shows «Dettagli».
    const more = async () => { await pageTab(context.tab, "details"); return context.tab().querySelector(".cal-page"); };
    return { ...context, page: context.tab().querySelector(".cal-page"), more };
  }
  // This cover's values in «Caratteristiche» under the times (no coefficients there).
  const rowsOf = (page) => [...page.querySelectorAll(".cal-page-traits .cal-pairs > div")].map((item) => [item.querySelector("dt").textContent, item.querySelector("dd").textContent.replace(/\u00a0/g, " ")]);
  const timesOf = (page) => [...page.querySelectorAll(".cal-page-times .cal-measure")].map((item) => [item.querySelector(".cal-label-long").textContent,
    item.querySelector(".cal-measure-number").textContent, item.querySelector(".cal-measure-caption")?.textContent ?? ""]);
  const usersOf = (page) => [...page.querySelectorAll(".cal-page-users .cal-user")].map((user) => user.querySelector(".cal-user-link, .cal-user-name").textContent);
  // «Cambia profilo» names the profile in what it does: not counted.
  const count = (page, name) => { const body = page.querySelector(".cal-page-body").cloneNode(true); body.querySelector(".cal-page-link")?.remove(); return body.textContent.split(name).length - 1; };

  test("the details of the profile's source: «Stato» with its chip, «Caratteristiche» with the three times in a row, «Profilo» with its name and «Misurato su questa tapparella»; «Dettagli»: who follows it, the source's one tail; the profile named once", async () => {
    const { page, more } = await details("cover.living_one");
    assert.deepEqual([...page.querySelectorAll(".cal-page-body section > .cal-section-title")].map((title) => title.textContent),
      ["Stato", "Caratteristiche", "Profilo"], "the phone's order");
    assert.deepEqual(timesOf(page), [["Tempo di salita", "22,3", ""], ["Tempo di discesa", "21,6", ""], ["Tempo lamelle", "4,3", ""]]);
    assert.equal(page.querySelector(".cal-page-times-note"), null, "measured here: no «dal profilo»");
    assert.equal(page.querySelector(".cal-pos-state-chip .cal-badge").textContent, "Calibrata · 0,3 cm");
    const head = page.querySelector(".cal-page-profile .cal-page-profile-head");
    assert.deepEqual([head.querySelector(".cal-profile-name").textContent, head.querySelector(".cal-page-role").textContent], ["tall_two_leaf", `Tempi misurati su questa tapparella · ${dateTimeText("2026-10-31T11:18:00+00:00", "it")}`]);
    assert.equal(head.querySelector(".cal-page-role strong").textContent, "questa tapparella", "the cover in evidence, on the source too");
    assert.deepEqual(rowsOf(page), [["Corsa del telo", "197 cm"], ["Lamelle", "Non indicato"]], "no label on the source: everything is measured here");
    assert.equal(count(page, "tall_two_leaf"), 1, "the profile's name once, in «Profilo»");
    // No footer; the source's connection is «Cambia profilo» only (no «Scollega» on the source).
    assert.equal(page.querySelector(".cal-page-actions"), null);
    // One button, no rows.
    assert.deepEqual([...page.querySelectorAll(".cal-page-profile .cal-page-link button")].map((button) => button.dataset.action), ["cal-tab-assign"]);
    const details_ = await more();
    assert.deepEqual(usersOf(details_), ["Kitchen shutter 2", "Living room shutter 3"]);
    assert.deepEqual([...details_.querySelectorAll(".cal-card-tail")].map((tail) => tail.textContent), [it.calTabNoteSource], "ONE tail");
    assert.deepEqual([...details_.querySelectorAll(".cal-page-last .cal-pairs dt")].map((dt) => dt.textContent), ["Data", "Come"]);
  });

  test("the details of a follower: nothing under the times, «Misurato su {cover} · {data ora}», «· impostata qui» on its own travel, «Profilo usato anche da» and the follower's one tail in «Dettagli»", async () => {
    const { page, more } = await details("cover.kitchen_two");
    assert.deepEqual(timesOf(page), [["Tempo di salita", "12,3", ""], ["Tempo di discesa", "12,1", ""], ["Tempo lamelle", "4,3", ""]]);
    assert.equal(page.querySelector(".cal-page-times-note"), null, "no double «dal profilo»");
    const role = page.querySelector(".cal-page-profile-head .cal-page-role");
    assert.equal(role.textContent, `Tempi misurati sulla tapparella Living room shutter 1 · ${dateTimeText("2026-10-31T11:18:00+00:00", "it")}`);
    assert.equal(role.querySelector("strong").textContent, "Living room shutter 1", "the cover that measured it stands out");
    assert.deepEqual(rowsOf(page), [["Corsa del telo", "184 cm · impostata qui"], ["Lamelle", "Non indicato"]]);
    assert.ok(page.querySelector(".cal-page-traits dd .cal-pairs-note"), "the note in the grey of a secondary text");
    assert.equal(count(page, "tall_two_leaf"), 1);
    const details_ = await more();
    assert.deepEqual(usersOf(details_), ["Living room shutter 1", "Living room shutter 3"]);
    assert.deepEqual([...details_.querySelectorAll(".cal-card-tail")].map((tail) => tail.textContent), [it.calTabNoteFollower]);
    // English, the same rows.
    const english = await details("cover.kitchen_two", { language: "en" });
    assert.equal(english.page.querySelector(".cal-page-profile-head .cal-page-role").textContent, `Times measured on the cover Living room shutter 1 · ${dateTimeText("2026-10-31T11:18:00+00:00", "en")}`);
    assert.deepEqual(rowsOf(english.page)[0], ["Curtain travel", "184 cm · set here"]);
    assert.equal((await english.more()).querySelector(".cal-card-tail").textContent, en.calTabNoteFollower);
  });

  test("without slats the slat time is left out (two times); a narrow card takes the short labels by a container rule", async () => {
    const { page } = await details("cover.living_one", { slats: 0 });
    assert.deepEqual(timesOf(page).map(([label]) => label), ["Tempo di salita", "Tempo di discesa"]);
    // Three columns on every width; the short labels below the width the long ones need (3 × 98 px).
    assert.match(cascadeCss, /\.cal-page-status \{ container: caltimes \/ inline-size; \}/);
    assert.match(cascadeCss, /\.cal-measures\.cal-page-times \{ grid-template-columns: repeat\(3, minmax\(0, 1fr\)\);/);
    assert.match(cascadeCss, /\.cal-label-short \{ display: none; \}\s*@container caltimes \(max-width: 349px\) \{ \.cal-label-long \{ display: none; \} \.cal-label-short \{ display: inline; \} \}/);
    // The keys of the old double mentions are gone.
    for (const key of ["wiz_T447", "calTabInheritedNote", "calTabProfileNote", "calTabValues", "calTabFollowers", "calTabFollowersNone"]) assert.equal(it[key] ?? en[key], undefined, key);
  });

  test("a cover with no profile: «Nessun profilo» in «Profilo», no role, no tail", async () => {
    const overview = v1Overview();
    const context = await mount({ language: "it", overview });
    overview.covers.find((cover) => cover.entity_id === "cover.living_one").profile_id = null;
    overview.profiles[0].assigned_to = ["cover.kitchen_two"];
    context.backend.publishOverview(); await tick(8);
    context.tab().querySelector('.cal-row[data-entity="cover.living_one"] .cal-row-toggle').click(); await tick();
    const alone = context.tab().querySelector(".cal-page");
    assert.equal(alone.querySelector(".cal-page-profile-head").textContent, "Nessun profilo");
    assert.equal(alone.querySelector(".cal-card-tail, .cal-page-role"), null);
    assert.deepEqual(rowsOf(alone).map(([label]) => label), ["Corsa del telo", "Lamelle"]);
  });
});

describe("«Lamelle»", async () => {
  const { translations } = await import("../../custom_components/myhome/frontend/panel/panel-translations.js");
  const en = translations.en, it = translations.it;
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

  test("the details' «Lamelle»: «Sì» on the source, «Sì · dal profilo» on a follower, both read only; without a profile «Sì» / «No» / «Non indicato» edited in the row with a tick (no question); «Tempo lamelle» says «da misurare» in amber when slats are said but not measured; no standing notice", async () => {
    const data = sourceOverview();
    data.covers[0].slats_state = "yes_measured";
    data.covers[1].slats_state = "yes_profile";
    // Living room shutter 3 follows no profile: its card says its slats.
    Object.assign(data.covers[2], { profile_id: null, slats_state: "unknown" });
    data.profiles[0].assigned_to = data.profiles[0].assigned_to.filter((id) => id !== "cover.living_three");
    delete data.covers[2].effective.slat_time_s;
    const context = await mount({ overview: data, language: "it" });
    const { tab, backend } = context;
    const slatsRow = (page) => page.querySelector(".cal-pairs-slats");
    const cell = (page) => { const item = page.querySelectorAll(".cal-page-times .cal-measure")[2];
      return [item.querySelector(".cal-measure-number").textContent, item.querySelector(".cal-measure-caption")?.textContent ?? "", !!item.querySelector(".cal-measure-caption.is-todo .cal-todo-dot")]; };
    let page = await details(context, "cover.living_one");
    assert.equal(slatsRow(page).querySelector("dd").textContent.trim(), "Sì", "no label on the source's own values");
    assert.equal(slatsRow(page).querySelector("button"), null, "measured: certain, not editable");
    tab().querySelector(".cal-page-close").click(); await tick(6);
    // A follower: its profile's slats, read only; no standing notice (a contradiction is asked when a profile is given).
    page = await details(context, "cover.kitchen_two");
    assert.equal(slatsRow(page).querySelector("dd").textContent.trim(), "Sì · dal profilo");
    assert.equal(slatsRow(page).querySelector("button"), null, "inherited: read only");
    assert.equal(page.querySelector(".cal-slats-mismatch"), null);
    assert.equal(it.calTabSlatsMismatchYes, undefined, "the standing notice retired");
    tab().querySelector(".cal-page-close").click(); await tick(6);
    // Not said: «—» without a note; edited in the row, saved by the tick.
    page = await details(context, "cover.living_three");
    // The slat time only where the cover has slats.
    assert.deepEqual([slatsRow(page).querySelector("dd").textContent.trim(), page.querySelectorAll(".cal-page-times .cal-measure").length], ["Non indicato", 2]);
    slatsRow(page).querySelector('[data-action="cal-tab-slats-edit"]').click(); await tick();
    const select = tab().querySelector('.cal-pairs-slats select[name="slats_mark"]');
    assert.deepEqual([...select.options].map((option) => option.textContent), ["Sì", "No", "Non indicato"]);
    assert.deepEqual([...tab().querySelectorAll(".cal-pairs-slats .cal-inline-icon")].map((button) => button.querySelector("ha-icon").getAttribute("icon")), ["mdi:check", "mdi:close"]);
    select.value = "yes";
    tab().querySelector('[data-action="cal-tab-slats-save"]').click(); await tick(8);
    const sent = backend.calls.filter((message) => message.action === "manual_slats");
    assert.deepEqual(sent.map(({ slats, entity_id }) => [slats, entity_id]), [[true, "cover.living_three"]], "no question: a statement");
    page = tab().querySelector(".cal-flow .cal-page");
    assert.deepEqual([slatsRow(page).querySelector("dd").textContent.trim(), cell(page)], ["Sì", ["—", "da misurare", true]], "said, not measured: amber «da misurare»");
    slatsRow(page).querySelector('[data-action="cal-tab-slats-edit"]').click(); await tick();
    const again = tab().querySelector('.cal-pairs-slats select[name="slats_mark"]');
    again.value = "no";
    again.dispatchEvent(new KeyboardEvent("keydown", { key: "Enter", bubbles: true })); await tick(8);
    page = tab().querySelector(".cal-flow .cal-page");
    assert.equal(page.querySelectorAll(".cal-page-times .cal-measure").length, 2, "no slats: no slat time");
    const css = readFileSync(new URL("../../custom_components/myhome/frontend/panel/panel-calibration.css", import.meta.url), "utf8");
    assert.match(css, /\.cal-measure-caption\.is-todo \{[^}]*color: color-mix\(in srgb, var\(--panel-warning, #ffa600\) 55%, var\(--primary-text-color, #182b37\)\); \}/, "amber, darker on light and lighter on dark");
    assert.match(css, /\.cal-todo-dot \{[^}]*background: var\(--panel-warning, #ffa600\); \}/);
  });
});

describe("«Misurato su» and «impostato qui»", async () => {
  const { translations } = await import("../../custom_components/myhome/frontend/panel/panel-translations.js");
  const { dateTimeText } = await import("../../custom_components/myhome/frontend/panel/panel-calibration-text.js");
  const it = translations.it, en = translations.en;
  const css = readFileSync(new URL("../../custom_components/myhome/frontend/panel/panel-calibration.css", import.meta.url), "utf8");
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
  const row = (tab, entity) => tab().querySelector(`.cal-row[data-entity="${entity}"]`);
  /** A cover's card on «Panoramica» (the times, the profile) or «Dettagli» (the table, who else uses the profile). */
  async function details(context, entity, view = "overview") {
    row(context.tab, entity).querySelector('[data-action="cal-tab-toggle"]').click();
    await tick(6);
    if (view !== "overview") await pageTab(context.tab, view);
    return context.tab().querySelector(".cal-flow .cal-page");
  }
  async function close(context) { context.tab().querySelector(".cal-page-close").click(); await tick(6); }
  const timesOf = (page) => [...page.querySelectorAll(".cal-page-times .cal-measure")].map((item) => [item.querySelector(".cal-label-long").textContent,
    item.querySelector(".cal-measure-caption")?.textContent ?? ""]);

  test("«Misurato su **X** · data ora» under the chip, on the source «questa tapparella» with the date too; 7 px under the chip, the cover in the text's colour at 500", async () => {
    const context = await mount({ overview: overview(), language: "it" });
    const when = dateTimeText(MEASURED_AT, "it");
    assert.match(when, /^1 nov 2026 \d{2}:\d{2}$/);
    const follower = await details(context, "cover.kitchen_two");
    const role = follower.querySelector(".cal-page-role");
    assert.equal(role.textContent, `Tempi misurati sulla tapparella Living room shutter 1 · ${when}`);
    assert.equal(role.querySelector("strong").textContent, "Living room shutter 1");
    await close(context);
    const source = (await details(context, "cover.living_one")).querySelector(".cal-page-role");
    assert.equal(source.textContent, `Tempi misurati su questa tapparella · ${when}`);
    assert.equal(source.querySelector("strong").textContent, "questa tapparella");
    assert.match(css, /\.cal-page-profile-head:has\(\.cal-page-role\) \{ gap: 7px; \}/);
    assert.match(css, /\.cal-page-profile-head \.cal-page-role strong[^{]*\{ font-weight: 500; color: var\(--panel-text\); \}/);
    assert.deepEqual([en.calTabRoleSource, en.calTabRoleFollower, en.wiz_T371, it.wiz_T371],
      ["Times measured on **this cover** · {date}", "Times measured on the cover {cover} · {date}", "Measured on the cover {cover} · {date}", "Misurato sulla tapparella {cover} · {date}"]);
    assert.equal(it.calTabRoleFollowerNoDate, "Tempi misurati sulla tapparella {cover}", "unchanged when the date is missing");
  });

  test("on a follower a time set on it says «impostato qui» under its value; nothing under the profile's (no «dal profilo»); nothing on a cover alone", async () => {
    const data = overview();
    const two = data.covers.find((cover) => cover.entity_id === "cover.kitchen_two");
    two.effective.opening = timing(15, "override");
    two.overrides = { opening: two.effective.opening };
    const context = await mount({ overview: data, language: "it" });
    const page = await details(context, "cover.kitchen_two");
    assert.deepEqual(timesOf(page), [["Tempo di salita", "impostato qui"], ["Tempo di discesa", ""], ["Tempo lamelle", ""]]);
    assert.equal(page.querySelector(".cal-page-times-note"), null, "no «dal profilo»");
    await close(context);
    // Without personal times, nothing under the times.
    const three = await details(context, "cover.living_three");
    assert.deepEqual(timesOf(three).map(([, caption]) => caption).slice(0, 2), ["", ""]);
    assert.equal(three.querySelector(".cal-page-times-note"), null);
    reset();
    // A cover without a profile with a time of its own: no label (its values are its own).
    const alone = sampleOverview();
    alone.covers[0].effective.closing = timing(19.5, "override");
    const plain = await mount({ overview: alone, language: "en" });
    assert.deepEqual(timesOf(await details(plain, "cover.living_one")).slice(0, 2), [["Opening time", ""], ["Closing time", ""]]);
  });
});

describe("tabs and «Stato»", async () => {
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
  const tabs = (page) => [...page.querySelectorAll('.cal-page-tabs [role="tab"]')].map((tab) => [tab.textContent, tab.getAttribute("aria-selected"), tab.getAttribute("tabindex")]);
  const state = (page) => [page.querySelector(".cal-pos-state-title")?.textContent ?? null, page.querySelector(".cal-pos-state-desc")?.textContent ?? null];
  const ctas = (page) => [...page.querySelectorAll(".cal-cta")].map((cta) => [cta.querySelector("button").dataset.action, cta.querySelector("button").textContent.trim(), cta.querySelector(".cal-caption").textContent]);
  const plain = (value) => String(value).replace(/ /g, " ");
  const head = (page) => page.querySelector(".cal-page-head .muted").textContent;

  test("the details open on «Panoramica», two tabs under the head; «Dettagli» holds the table and the history; ← → move between them; the room and address stay under the name", async () => {
    const context = await mount({ language: "it", overview: family() });
    const page = await open(context, "cover.kitchen_two");
    assert.deepEqual(tabs(page), [["Panoramica", "true", "0"], ["Dettagli", "false", "-1"]]);
    assert.equal(page.querySelector(".cal-page-tabs").getAttribute("role"), "tablist");
    assert.match(plain(head(page)), /^Kitchen · Indirizzo: 91 · A: 9 · PL: 1$/, "the panel's subtitle, never «punto»");
    const body = page.querySelector(".cal-page-body");
    assert.deepEqual([body.getAttribute("role"), body.dataset.tab, body.getAttribute("aria-labelledby")], ["tabpanel", "overview", "cal-page-tab-overview"]);
    assert.deepEqual([...body.children].map((child) => child.classList[child.classList.length - 1]), ["cal-pos-current", "cal-page-traits", "cal-page-profile"], "Stato, Caratteristiche, Profilo");
    assert.ok(page.querySelector(".cal-page-traits .cal-page-times .cal-measure"), "the times");
    assert.ok(page.querySelector('.cal-page-traits [data-action="cal-tab-travel-edit"]'), "the travel's pencil");
    assert.equal(page.querySelector(".cal-page-actions"), null, "no footer");
    await pageTab(context.tab, "details");
    assert.deepEqual(tabs(card(context)).map(([, selected]) => selected), ["false", "true"]);
    assert.deepEqual([...card(context).querySelectorAll(".cal-page-body > section")].map((section) => section.classList[1]), ["cal-page-values", "cal-page-last", "cal-page-check", "cal-page-users"]);
    assert.ok(card(context).querySelector(".cal-page-values .cal-compare"), "«Valori del profilo e di questa tapparella»");
    assert.ok(card(context).querySelector(".cal-page-users .cal-user"), "«Profilo usato anche da»");
    // The keyboard: ← → and Home / End.
    const key = (name) => card(context).querySelector('[role="tab"][aria-selected="true"]').dispatchEvent(new dom.window.KeyboardEvent("keydown", { key: name, bubbles: true, composed: true }));
    key("ArrowLeft"); await tick(6);
    assert.equal(card(context).querySelector(".cal-page-body").dataset.tab, "overview");
    assert.equal(context.root.activeElement, card(context).querySelector("#cal-page-tab-overview"), "the focus on the tab chosen");
    key("End"); await tick(6);
    assert.equal(card(context).querySelector(".cal-page-body").dataset.tab, "details");
    key("ArrowRight"); await tick(6);
    assert.equal(card(context).querySelector(".cal-page-body").dataset.tab, "overview");
  });

  test("the tab chosen stays while the card is open (the 15 s reads, «Cambia profilo» and back, a flow cancelled back to the card); closed and opened again, «Panoramica»", async () => {
    const context = await mount({ language: "it", overview: family() });
    await open(context, "cover.living_one");
    await pageTab(context.tab, "details");
    for (let i = 0; i < 2; i++) { context.panel._calibrationTab.refresh(); await tick(8); }
    assert.equal(card(context).querySelector(".cal-page-body").dataset.tab, "details", "the reads redraw it on its tab");
    // «Cambia profilo» from the card «Profilo» (on «Panoramica») and «Annulla»: back there, the focus on «Cambia profilo».
    await pageTab(context.tab, "overview");
    card(context).querySelector('.cal-page-profile .cal-page-link [data-action="cal-tab-assign"]').click(); await tick(6);
    assert.ok(context.tab().querySelector(".cal-assign-page"));
    context.tab().querySelector('[data-action="cal-tab-assign-cancel"]').click(); await tick(6);
    assert.equal(card(context).querySelector(".cal-page-body").dataset.tab, "overview");
    assert.equal(context.root.activeElement, card(context).querySelector('.cal-page-link [data-action="cal-tab-assign"]'));
    // A flow from the card, cancelled on its first screen: the card on its tab.
    await click(context, "cal-tab-refine");
    assert.ok(context.wiz(), "Perfeziona");
    await context.press("cancel-flow"); await tick(8);
    assert.equal(card(context)?.querySelector(".cal-page-body").dataset.tab, "overview");
    await pageTab(context.tab, "details");
    card(context).querySelector(".cal-page-close").click(); await tick(8);
    const again = await open(context, "cover.living_one");
    assert.equal(again.querySelector(".cal-page-body").dataset.tab, "overview", "opened again: «Panoramica»");
  });

  test("a cover never calibrated: no tabs; the chip «Da calibrare» over its sentence, «Come vuoi calibrarla?» with Misura i tempi and Usa un profilo, its characteristics; no procedure", async () => {
    const context = await mount({ language: "it" });
    const page = await open(context, "cover.living_one");
    assert.equal(page.querySelector(".cal-page-tabs"), null, "no tabs");
    assert.deepEqual(state(page), [null, it.cal_POS_none_text]);
    assert.deepEqual([...page.querySelector(".cal-pos-state").children].map((child) => child.className), ["cal-pos-state-chip", "cal-pos-state-desc"], "the chip first");
    assert.equal(page.querySelector(".cal-pos-state-chip .cal-badge").textContent, "Da calibrare");
    assert.deepEqual(ctas(page), [], "no buttons");
    assert.equal(page.querySelector(".cal-pos-how > .cal-section-title").textContent, "Come vuoi calibrarla?");
    assert.deepEqual([...page.querySelectorAll(".cal-pos-how .cal-choice")].map((choice) => [choice.dataset.action, choice.querySelector(".cal-choice-title").textContent, choice.querySelector(".cal-choice-desc").textContent]),
      [["cal-tab-measure-cover", "Misura i tempi", it.cal_POS_none_measure_caption], ["cal-tab-assign", "Usa un profilo", it.cal_POS_none_assign_caption]]);
    assert.equal(page.querySelector(".cal-pos-help"), null, "no «Qualcosa non va?»");
    assert.ok(page.querySelector(".cal-page-traits .cal-page-times"), "its characteristics");
    assert.equal(page.querySelector(".cal-page-profile"), null, "no card «Profilo»: «Usa un profilo» is the way");
  });

  test("the profile's source: basic «Tempi di movimento misurati» with Perfeziona; fine-tuned «Tempi e altezze misurati»; the check's sentence after the text, its data in «Ultima verifica»", async () => {
    const basic = await mount({ language: "it", overview: family() });
    let page = await open(basic, "cover.living_one");
    assert.deepEqual(state(page), [it.cal_POS_basic_title, it.cal_POS_basic_text]);
    // «Perfeziona» alone.
    assert.deepEqual(ctas(page), [["cal-tab-refine", "Perfeziona", it.cal_POS_refine_caption]]);
    assert.equal(page.querySelector(".cal-pos-check"), null, "no row «Verifica del posizionamento»");
    const entry = page.querySelector('.cal-pos-status .cal-pos-help [data-action="cal-tab-check-start"]');
    assert.deepEqual([entry.querySelector(".cal-choice-title").textContent, entry.querySelector(".cal-choice-desc").textContent], ["Qualcosa non va?", it.cal_S1_entry_desc]);
    reset();
    const refined = await mount({ language: "it", overview: family({ rolls: true, check: ["cover.living_one"] }) });
    page = await open(refined, "cover.living_one");
    assert.deepEqual(state(page), [it.cal_POS_curve_title, `${it.cal_POS_curve_text} Alla verifica a metà corsa si è fermata a 1 cm dal punto atteso.`]);
    assert.deepEqual(ctas(page), [["cal-tab-refine", "Perfeziona", it.cal_POS_refine_caption_again]]);
    assert.equal(page.querySelector(".cal-pos-check"), null);
    await pageTab(refined.tab, "details");
    const check = [...card(refined).querySelectorAll(".cal-page-check .cal-pairs > div")].map((line) => [line.querySelector("dt").textContent, plain(line.querySelector("dd").textContent)]);
    assert.deepEqual(check.map(([label]) => label), ["Data", "Dove", "Scarto"]);
    assert.match(check[0][1], /^4 nov 2026 \d{2}:\d{2}$/);
    assert.deepEqual(check.slice(1), [["Dove", "a metà corsa"], ["Scarto", "1 cm"]]);
    reset();
    // A check taken elsewhere than half the travel (the tape's default, 75 %): no «Dove», the sentence without «a metà corsa».
    const other = await mount({ language: "en", overview: family({ rolls: true, check: ["cover.living_one"], half: false }) });
    page = await open(other, "cover.living_one");
    assert.equal(state(page)[1], `${en.cal_POS_curve_text} At the check it stopped 1 cm from the expected point.`);
    await pageTab(other.tab, "details");
    assert.deepEqual([...card(other).querySelectorAll(".cal-page-check .cal-pairs dt")].map((dt) => dt.textContent), ["Date", "Gap"]);
  });

  test("a follower: «Usa i tempi di un profilo» with what its times are, no main action, its entry without «Rispondi a una domanda»; the profile without its travel: no route through the source's card", async () => {
    const scaled = await mount({ language: "it", overview: family() });
    let page = await open(scaled, "cover.kitchen_two");
    // «Usa i tempi di un profilo», the two travels side by side.
    assert.deepEqual(state(page).map(plain), ["Usa i tempi di un profilo", "I tempi del profilo sono adattati alla corsa di questa tapparella: 130 cm invece di 110."]);
    assert.deepEqual(ctas(page), [], "no Perfeziona on a follower");
    const entry = page.querySelector('.cal-pos-help [data-action="cal-tab-check-start"]');
    assert.equal(entry.querySelector(".cal-choice-title").textContent, "Qualcosa non va?");
    assert.equal(entry.querySelector(".cal-choice-desc").textContent, "Ricalibriamo questa tapparella.", "no question on a follower: Ricalibra");
    card(scaled).querySelector(".cal-page-close").click(); await tick(8);
    page = await open(scaled, "cover.living_three");
    assert.deepEqual(state(page).map(plain), ["Usa i tempi di un profilo", "Usa senza modifiche i tempi misurati sulla tapparella Living room shutter 1."], "no travel of its own: the times as they are");
    reset();
    const curve = await mount({ language: "en", overview: family({ rolls: true }) });
    page = await open(curve, "cover.kitchen_two");
    assert.deepEqual(state(page).map(plain), ["Uses a profile’s times", "The profile’s times are adapted to this cover’s travel: 130 cm instead of 110."]);
    reset();
    const unknown = await mount({ language: "it", overview: family({ reference: null }) });
    page = await open(unknown, "cover.kitchen_two");
    assert.deepEqual(state(page).map(plain), ["Usa i tempi di un profilo", "Usa i tempi misurati sulla tapparella Living room shutter 1, senza adattarli alla corsa di questa tapparella."]);
    assert.equal(page.querySelector(".cal-pos-noref"), null, "no notice about the profile's unknown travel");
    assert.equal(page.querySelector('[data-action="cal-tab-check-source"]'), null);
    assert.deepEqual([...page.querySelector(".cal-page-body").children].map((child) => child.classList[child.classList.length - 1]), ["cal-pos-current", "cal-page-traits", "cal-page-profile"], "no Perfeziona");
  });

  test("«Cambia profilo» on every cover, one secondary button with its short line under it, at the bottom of the card «Profilo»; Scollega in its window, on a follower, its consequences said there before the confirmation", async () => {
    const data = family();
    data.covers[2].effective.opening = timing(15, "override"); data.covers[2].effective.closing = timing(14, "override");
    data.covers[2].overrides = { opening: data.covers[2].effective.opening, closing: data.covers[2].effective.closing };
    const context = await mount({ language: "it", overview: data });
    const lines = () => [...card(context).querySelectorAll(".cal-page-link button")].map((button) => [button.dataset.action, button.textContent, button.className,
      plain(button.nextElementSibling?.textContent)]);
    await open(context, "cover.living_one");
    assert.equal(card(context).querySelector("details.cal-link, .cal-page-body > .cal-page-link"), null, "no group of its own");
    // The card «Profilo» of «Panoramica»: its head, then the action (who else uses it is in «Dettagli»).
    assert.ok(card(context).querySelector(".cal-page-profile .cal-page-profile-card > .cal-page-profile-head + .cal-page-link:last-child"),
      "the profile's head, then the action at the bottom of the card");
    assert.doesNotMatch(card(context).textContent, /Collegamento al profilo/);
    assert.deepEqual(lines(), [["cal-tab-assign", "Cambia profilo", "cal-outlined", "Usa un altro profilo. La corsa del telo resta."]]);
    assert.equal(card(context).querySelector(".cal-page-link .cal-choice"), null, "no row with ›");
    assert.doesNotMatch(card(context).textContent, /Rendi indipendente/);
    card(context).querySelector(".cal-page-close").click(); await tick(8);
    await open(context, "cover.living_three");
    assert.deepEqual(lines(), [["cal-tab-assign", "Cambia profilo", "cal-outlined", "Usa un altro profilo. La corsa del telo resta."]], "a follower: the same, no Scollega in the card");
    assert.doesNotMatch(card(context).textContent, /Scollega/);
    // The window offers «Scollega il profilo», not chosen, its consequences under it (wiz_T405); chosen, its button.
    card(context).querySelector('.cal-page-link [data-action="cal-tab-assign"]').click(); await tick(6);
    const sheet = context.tab().querySelector(".cal-assign-page");
    const none = sheet.querySelector('input[value="__no_profile__"]');
    assert.deepEqual([none.checked, none.disabled], [false, false]);
    assert.match(sheet.querySelector(".cal-assign-none small").textContent, /^Questa tapparella verrà scollegata dal profilo «tall_two_leaf»\. Restano gli eventuali tempi impostati su questa tapparella/);
    none.click(); await tick(2);
    assert.equal(sheet.querySelector("[data-assign]").disabled, false);
    assert.equal(sheet.querySelector("[data-assign]").textContent, "Scollega il profilo");
    assert.equal(context.backend.calls.filter((message) => message.type === "myhome/cover_profiles/write").length, 0, "nothing written until confirmed");
    reset();
    const english = await mount({ language: "en", overview: family() });
    await open(english, "cover.living_three");
    assert.deepEqual([...card(english).querySelectorAll(".cal-page-link .cal-caption")].map((caption) => plain(caption.textContent)), ["Use another profile. The curtain travel stays."]);
  });
});

describe("two columns and the card «Profilo»", async () => {
  const { translations } = await import("../../custom_components/myhome/frontend/panel/panel-translations.js");
  const it = translations.it, en = translations.en;
  const original = globalThis.matchMedia;
  /** A window `width` px wide and 800 high for `matchMedia`; motion reduced unless `motion`. */
  const windowOf = (width, { motion = false } = {}) => (query) => ({ matches: query.split(",").some((part) => [...part.matchAll(/\(\s*([a-z-]+)\s*:\s*([a-z0-9-]+)\s*\)/g)].every(([, name, value]) =>
    name === "max-width" ? width <= parseFloat(value) : name === "min-width" ? width >= parseFloat(value) : name === "max-height" ? 800 <= parseFloat(value)
      : name === "min-height" ? 800 >= parseFloat(value) : name === "orientation" ? value === "landscape" : name === "prefers-reduced-motion" ? !motion : false)), addEventListener() {}, removeEventListener() {} });
  const desktop = (options) => { globalThis.matchMedia = windowOf(1280, options); };
  const MEASURED_AT = "2026-11-01T16:09:00+00:00";
  const measured = { source: "guided", recorded_at: MEASURED_AT, origin_entity_id: null, origin_name: null, inherited: false };
  const timing = (value, origin = "profile", scaled = false) => ({ value, origin, provenance: measured, scaled });
  /** «tall» measured on Living room shutter 1 (the source), followed by Kitchen shutter 2 (130 cm, slats) and Living room shutter 3. */
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
  const card = (context) => context.tab().querySelector(".cal-flow .cal-page:not(.cal-assign-page)");
  const body = (context) => card(context).querySelector(".cal-page-body");
  async function open(context, entity) {
    row(context, entity).querySelector('[data-action="cal-tab-toggle"]').click();
    await tick(6);
    return card(context);
  }
  const click = async (context, selector) => {
    const button = card(context).querySelector(selector);
    assert.ok(button, selector);
    button.click();
    await tick(6);
  };
  const answers = (scope) => [...scope.querySelectorAll(".cal-rows > .cal-choice[data-action=\"cal-tab-check-answer\"]")].map((choice) => choice.dataset.id);
  /** The rules of the 1024 px block that holds `marker`. */
  const wideCss = (marker) => { const start = cascadeCss.indexOf(marker); return block("@media (min-width: 1024px)", cascadeCss.slice(cascadeCss.lastIndexOf("@media (min-width: 1024px)", start))); };

  test("from 1024 px the source's «Panoramica» has two columns: «Stato» (with the fold «Qualcosa non va?»: the three answers and the actuator's note) and Perfeziona on the left, the cover and its profile on the right, no entry", async () => {
    desktop();
    const context = await mount({ language: "it", overview: family() });
    const page = await open(context, "cover.living_one");
    assert.ok(body(context).matches(".is-wide[data-tab=\"overview\"]"));
    assert.deepEqual([...body(context).children].map((child) => child.className), ["cal-page-column", "cal-page-column"]);
    const [left, right] = body(context).children;
    assert.ok(left.querySelector(".cal-pos-status") && left.querySelector('.cal-cta [data-action="cal-tab-refine"]'), "the state and the main action on the left");
    assert.ok(right.querySelector(".cal-page-traits") && right.querySelector(".cal-page-profile"), "the cover and its profile on the right");
    const help = page.querySelector(".cal-pos-status > details.cal-fold.cal-pos-help");
    assert.equal(help.querySelector("summary .cal-choice-title").textContent, it.cal_S1_fold, "the fold's title, not «Cosa vuoi sistemare?»");
    assert.doesNotMatch(help.textContent, new RegExp(it.cal_P1_title.replace("?", "\\?")));
    assert.equal(page.querySelector('[data-action="cal-tab-check-start"]'), null, "no «Controlla il posizionamento»");
    assert.deepEqual(answers(help), ["full", "middle", "unsure"]);
    assert.deepEqual([...help.querySelectorAll(".cal-choice")].map((choice) => [choice.querySelector(".cal-choice-title").textContent, choice.querySelector(".cal-choice-desc").textContent]),
      ["full", "middle", "unsure"].map((id) => [it[`cal_P1_${id}_title`], it[`cal_P1_${id}_desc`]]), "the same rows as the question");
    assert.equal(help.querySelector(".cal-rows + .cal-caption").textContent, it.cal_P1_note, "the actuator's note under them");
    // The list's «Controlla», which opened the fold, is gone.
    card(context).querySelector(".cal-page-close").click(); await tick(8);
    assert.equal(row(context, "cover.living_one").querySelector('[data-action="cal-tab-fix"]'), null);
  });

  test("from 1024 px a follower's fold is Ricalibra at once (what to do, the warning, the button), no answers; the button opens Ricalibra; a cover never calibrated has two columns too", async () => {
    desktop();
    const context = await mount({ language: "it", overview: family() });
    const opened = [];
    context.panel._calibrationTab._openWizard = (options) => opened.push([options.start, options.item?.cover.entity_id]);
    const page = await open(context, "cover.kitchen_two");
    assert.ok(body(context).matches(".is-wide"));
    const help = page.querySelector("details.cal-fold.cal-pos-help");
    assert.deepEqual([help.querySelector("summary .cal-choice-title").textContent, help.querySelector("summary .cal-choice-desc").textContent], [it.cal_S1_fold, it.cal_S1_fold_follower]);
    assert.deepEqual(answers(help), [], "no answers: a follower has no question");
    assert.equal(page.querySelector('[data-action="cal-tab-check-start"]'), null);
    const outcome = help.querySelector(".cal-fold-body > .cal-pos-outcome");
    assert.equal(outcome.querySelector(".cal-card-body p").textContent, it.cal_D2_todo);
    assert.equal(outcome.querySelector(".cal-note.is-warning").textContent, it.cal_D0_warn_follower_today);
    const button = outcome.querySelector(".cal-pos-action > button.primary");
    assert.equal(button.textContent, it.cal_D2_button);
    button.click(); await tick(6);
    assert.deepEqual(opened, [["path", "cover.kitchen_two"]]);
    reset();
    const fresh = await mount({ language: "it" });
    await open(fresh, "cover.living_one");
    // «Come vuoi calibrarla?» on the right.
    assert.ok(body(fresh).matches(".is-wide") && body(fresh).querySelectorAll(":scope > .cal-page-column").length === 2, "never calibrated: two columns");
    assert.equal(card(fresh).querySelector('.cal-pos-help [data-action="cal-tab-check-answer"], .cal-pos-outcome'), null, "no procedure");
  });

  test("under 1024 px (and in jsdom without window rules) «Panoramica» is the phone's: one column, the row «Qualcosa non va?» and the question behind it", async () => {
    for (const rules of [null, windowOf(1023), windowOf(390)]) {
      globalThis.matchMedia = rules || original;
      const context = await mount({ language: "it", overview: family() });
      const page = await open(context, "cover.living_one");
      assert.equal(body(context).classList.contains("is-wide"), false);
      assert.equal(page.querySelector(".cal-page-column, details.cal-fold"), null);
      assert.ok(page.querySelector('.cal-pos-help [data-action="cal-tab-check-start"]'), "the entry");
      assert.deepEqual(answers(page), []);
      await click(context, '[data-action="cal-tab-check-start"]');
      assert.equal(card(context).querySelector(".cal-step-title").textContent, it.cal_P1_title, "«Cosa vuoi sistemare?» on its screen");
      reset();
    }
  });

  test("the stylesheet: from 1024 px both tabs are two equal columns, and only a view drawn for the phone keeps the centred 560 px column", () => {
    const css = wideCss(".cal-page-body.is-wide:not(.cal-check)");
    assert.match(css, /\.cal-page-body\.is-wide:not\(\.cal-check\) \{ display: grid; grid-template-columns: repeat\(2, minmax\(0, 1fr\)\); align-items: start;/);
    assert.match(css, /\.cal-page-body > \.cal-page-notice \{ grid-column: 1 \/ -1; \}/);
    assert.match(css, /\.cal-page-body:not\(\.is-wide\) > \* \{ width: 100%; max-width: 560px; margin-inline: auto; \}/);
    assert.doesNotMatch(cascadeCss, /cal-pos-wide|cal-pos-main/, "the old two columns are gone");
    assert.doesNotMatch(cascadeCss.replace(/@media \(min-width: 1024px\) \{[\s\S]*?\n\}/g, ""), /\.is-wide:not\(\.cal-check\) \{|cal-check\.is-wide/, "nothing of it under 1024 px");
  });

  test("no group «Collegamento al profilo»; «Cambia profilo» (every cover, one button: Scollega is in its window) closes the card «Profilo» of «Panoramica»; Stato, Caratteristiche, Profilo in this order on every window", async () => {
    for (const rules of [null, windowOf(1280)]) {
      globalThis.matchMedia = rules || original;
      const context = await mount({ language: "it", overview: family() });
      for (const [entity, actions] of [["cover.living_one", ["cal-tab-assign"]], ["cover.kitchen_two", ["cal-tab-assign"]]]) {
        await open(context, entity);
        const page = card(context);
        assert.deepEqual([...page.querySelectorAll(".cal-page-body section")].map((section) => section.classList[1]), ["cal-pos-current", "cal-page-traits", "cal-page-profile"]);
        assert.equal(page.querySelector("details.cal-link"), null);
        assert.doesNotMatch(page.textContent, /Collegamento al profilo/);
        const profileCard = page.querySelector(".cal-page-profile > .cal-card");
        assert.deepEqual([...profileCard.children].map((child) => child.classList[child.classList.length - 1]), ["cal-page-profile-head", "cal-page-link"],
          "the profile's symbol, name and «Misurato su…», then the actions");
        assert.deepEqual([...profileCard.querySelectorAll(".cal-page-link > button")].map((button) => button.dataset.action), actions);
        assert.equal(profileCard.querySelector(".cal-page-link > button").textContent, it.cal_LINK_change);
        page.querySelector(".cal-page-close").click(); await tick(8);
      }
      reset();
    }
    assert.equal(it.cal_LINK_title, undefined, "its title is gone");
    assert.equal(en.cal_LINK_title, undefined);
  });

  test("the same destinations: «Cambia profilo» and «Annulla» back on «Panoramica» with the focus on it; «Scollega il profilo» chosen in it; without a profile «Usa un profilo»", async () => {
    const context = await mount({ language: "it", overview: family() });
    await open(context, "cover.kitchen_two");
    await click(context, '.cal-page-profile .cal-page-link [data-action="cal-tab-assign"]');
    assert.ok(context.tab().querySelector(".cal-assign-page"));
    context.tab().querySelector('[data-action="cal-tab-assign-cancel"]').click(); await tick(6);
    assert.equal(body(context).dataset.tab, "overview");
    assert.equal(context.root.activeElement, card(context).querySelector('.cal-page-link [data-action="cal-tab-assign"]'));
    await click(context, '.cal-page-profile .cal-page-link [data-action="cal-tab-assign"]');
    const sheet = context.tab().querySelector(".cal-assign-page");
    sheet.querySelector('input[value="__no_profile__"]').click(); await tick(2);
    assert.equal(sheet.querySelector("[data-assign]").textContent, "Scollega il profilo");
    assert.equal(context.backend.calls.filter((message) => message.type === "myhome/cover_profiles/write").length, 0);
    reset();
    // A cover never calibrated: «Come vuoi calibrarla?» › «Usa un profilo» (no card «Profilo»).
    const fresh = await mount({ language: "it" });
    await open(fresh, "cover.living_one");
    assert.equal(card(fresh).querySelector(".cal-page-profile"), null);
    assert.deepEqual([...card(fresh).querySelectorAll('.cal-pos-how [data-action="cal-tab-assign"]')].map((choice) => choice.querySelector(".cal-choice-title").textContent), [it.cal_POS_none_how_assign]);
  });

  test("the stylesheet: a hairline over the actions of the card «Profilo»; no rules left for the group", () => {
    assert.match(cascadeCss, /\.cal-page-profile-head \+ \.cal-page-link \{ border-top: 1px solid var\(--panel-border\); \}/);
    assert.doesNotMatch(cascadeCss, /\.cal-link\b|grid-area: link/);
  });
});

describe("a cover never calibrated, the columns", async () => {
  const { translations } = await import("../../custom_components/myhome/frontend/panel/panel-translations.js");
  const it = translations.it, en = translations.en;
  const original = globalThis.matchMedia;
  /** A window `width` px wide and 800 high for `matchMedia`; motion reduced unless `motion`. */
  const windowOf = (width, { motion = false } = {}) => (query) => ({ matches: query.split(",").some((part) => [...part.matchAll(/\(\s*([a-z-]+)\s*:\s*([a-z0-9-]+)\s*\)/g)].every(([, name, value]) =>
    name === "max-width" ? width <= parseFloat(value) : name === "min-width" ? width >= parseFloat(value) : name === "max-height" ? 800 <= parseFloat(value)
      : name === "min-height" ? 800 >= parseFloat(value) : name === "orientation" ? value === "landscape" : name === "prefers-reduced-motion" ? !motion : false)), addEventListener() {}, removeEventListener() {} });
  const desktop = (options) => { globalThis.matchMedia = windowOf(1280, options); };
  const MEASURED_AT = "2026-11-01T16:09:00+00:00";
  const measured = { source: "guided", recorded_at: MEASURED_AT, origin_entity_id: null, origin_name: null, inherited: false };
  const timing = (value, origin = "profile", scaled = false) => ({ value, origin, provenance: measured, scaled });
  /** «tall» measured on Living room shutter 1 (the source, slats), followed by Kitchen shutter 2 (slats) and Living room shutter 3 (no slats). */
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
  const card = (context) => context.tab().querySelector(".cal-flow .cal-page:not(.cal-assign-page)");
  const body = (context) => card(context).querySelector(".cal-page-body");
  async function open(context, entity) {
    row(context, entity).querySelector('[data-action="cal-tab-toggle"]').click();
    await tick(6);
    return card(context);
  }
  const click = async (context, selector) => {
    const button = card(context).querySelector(selector);
    assert.ok(button, selector);
    button.click();
    await tick(6);
  };
  const classes = (element) => [...element.children].map((child) => child.classList[child.classList.length - 1]);
  /** The rules of the 1024 px block that holds `marker`. */
  const wideCss = (marker) => { const start = cascadeCss.indexOf(marker); return block("@media (min-width: 1024px)", cascadeCss.slice(cascadeCss.lastIndexOf("@media (min-width: 1024px)", start))); };

  test("from 1024 px a cover never calibrated has «Stato» and «Come vuoi calibrarla?» on the left, «Caratteristiche» on the right: «Misura i tempi» → Calibra, «Usa un profilo» → Assegna profilo, rows as the answers", async () => {
    desktop();
    const context = await mount({ language: "it" });
    const opened = [];
    context.panel._calibrationTab._openWizard = (options) => opened.push([options.start, options.item?.cover.entity_id]);
    const page = await open(context, "cover.living_one");
    // No tabs. «Stato» (its title over the card) and «Come vuoi calibrarla?» on the left, «Caratteristiche» on the
    // right, as on the other covers.
    assert.ok(body(context).matches(".is-wide[data-tab=\"overview\"]"));
    assert.deepEqual([...body(context).children].map((child) => child.className), ["cal-page-column", "cal-page-column"]);
    const main = body(context).children[0];
    assert.deepEqual(classes(main), ["cal-pos-current", "cal-pos-how"], "«Stato» and «Come vuoi calibrarla?» on the left: no buttons");
    assert.deepEqual(classes(body(context).children[1]), ["cal-page-traits"], "«Caratteristiche» on the right");
    assert.equal(main.querySelector(".cal-pos-current > .cal-section-title").textContent, "Stato");
    assert.equal(main.querySelector(".cal-pos-state-title"), null);
    assert.equal(main.querySelector(".cal-pos-state-chip").textContent, "Da calibrare");
    assert.equal(main.querySelector(".cal-pos-state-desc").textContent, it.cal_POS_none_text);
    const how = page.querySelector(".cal-pos-how");
    assert.equal(how.querySelector(":scope > .cal-section-title").textContent, "Come vuoi calibrarla?");
    const rows = [...how.querySelectorAll(":scope > .cal-card.cal-rows > button.cal-choice")];
    assert.deepEqual(rows.map((choice) => [choice.dataset.action, choice.querySelector(".cal-choice-title").textContent, choice.querySelector(".cal-choice-desc").textContent,
      choice.querySelector(":scope > ha-icon").getAttribute("icon")]), [
      ["cal-tab-measure-cover", "Misura i tempi", "Misuriamo quanto impiega la tapparella ad aprirsi e a chiudersi del tutto.", "mdi:chevron-right"],
      ["cal-tab-assign", "Usa un profilo", "Usa un profilo con i tempi già misurati su una tapparella simile.", "mdi:chevron-right"]]);
    assert.equal(page.querySelector('[data-action="cal-tab-check-answer"], [data-action="cal-tab-check-start"]'), null, "no procedure");
    rows[0].click(); await tick(6);
    assert.deepEqual(opened, [["path", "cover.living_one"]], "Calibra");
    reset();
    const again = await mount({ language: "it" });
    await open(again, "cover.living_one");
    await click(again, '.cal-pos-how [data-action="cal-tab-assign"]');
    assert.ok(again.tab().querySelector(".cal-assign-page"), "Assegna profilo");
  });

  test("a cover never calibrated in English; on the phone (and in jsdom without window rules) the same two rows, one column", async () => {
    assert.deepEqual([en.cal_POS_none_how, en.cal_POS_none_how_measure, en.cal_POS_none_how_assign], ["How do you want to calibrate it?", "Record movement times", "Use a profile"]);
    assert.deepEqual([it.cal_POS_none_how, it.cal_POS_none_how_measure, it.cal_POS_none_how_assign], ["Come vuoi calibrarla?", "Misura i tempi", "Usa un profilo"]);
    desktop();
    const english = await mount({ language: "en" });
    await open(english, "cover.living_one");
    assert.deepEqual([...card(english).querySelectorAll(".cal-pos-how .cal-choice-title")].map((title) => title.textContent), ["Record movement times", "Use a profile"]);
    reset();
    for (const rules of [null, windowOf(1023), windowOf(390)]) {
      globalThis.matchMedia = rules || original;
      const context = await mount({ language: "it" });
      const page = await open(context, "cover.living_one");
      assert.equal(body(context).classList.contains("is-wide"), false);
      assert.equal(page.querySelector(".cal-page-column, .cal-cta"), null);
      assert.deepEqual(classes(body(context)), ["cal-pos-current", "cal-pos-how", "cal-page-traits"], "«Stato» (its title), the two rows, the characteristics");
      assert.deepEqual([...page.querySelectorAll(".cal-pos-how .cal-choice")].map((button) => button.dataset.action), ["cal-tab-measure-cover", "cal-tab-assign"], "the two rows");
      reset();
    }
  });

  test("«Panoramica» in two columns: «Stato» and Perfeziona; «Caratteristiche» (the times, then the travel and the slats) and «Profilo» (its symbol, name, «Misurato su…», the actions at the bottom); «Dettagli»: the table with its tail; the same order on the phone", async () => {
    for (const rules of [null, windowOf(1280)]) {
      globalThis.matchMedia = rules || original;
      const context = await mount({ language: "it", overview: family() });
      // One button on every cover, Scollega in its window.
      for (const [entity, actions] of [["cover.living_one", ["cal-tab-assign"]], ["cover.kitchen_two", ["cal-tab-assign"]]]) {
        await open(context, entity);
        const page = card(context), wide = !!rules;
        const sections = () => [...body(context).querySelectorAll("section")].map((section) => section.classList[1]);
        if (wide) assert.deepEqual([...body(context).children].map((child) => child.className), ["cal-page-column", "cal-page-column"], `${entity}: two columns of their own`);
        assert.deepEqual(sections(), ["cal-pos-current", "cal-page-traits", "cal-page-profile"]);
        const traits = page.querySelector(".cal-page-traits");
        assert.deepEqual(classes(traits.querySelector(".cal-card")), ["cal-page-times", "cal-pairs"], "the times, then this cover's values");
        assert.deepEqual([...traits.querySelectorAll(".cal-pairs > div > dt")].map((dt) => dt.textContent), ["Corsa del telo", "Lamelle"], "no coefficients here");
        assert.ok(traits.querySelector('[data-action="cal-tab-travel-edit"]'), "the travel's pencil");
        const profileCard = page.querySelector(".cal-page-profile .cal-page-profile-card");
        assert.deepEqual(classes(profileCard), ["cal-page-profile-head", "cal-page-link"], "the profile, then the actions");
        assert.equal(profileCard.querySelector(".cal-profile-icon ha-icon").getAttribute("icon"), "mdi:clipboard-text-outline", "the profile's own symbol, the same on every cover");
        assert.deepEqual([...profileCard.querySelectorAll(".cal-page-link > button")].map((button) => button.dataset.action), actions);
        await pageTab(context.tab, "details");
        const compare = card(context).querySelector(".cal-page-values .cal-page-compare");
        assert.deepEqual(classes(compare), ["cal-compare", "cal-card-tail"], "the box, then its tail");
        assert.equal(card(context).querySelector(".cal-page-values > .cal-section-title").textContent, it.calTabCompare, "its title over the card, always open");
        assert.equal(card(context).querySelector("details.cal-compare, .cal-compare-title"), null);
        assert.equal(compare.querySelector(".cal-card-tail").textContent, entity === "cover.living_one" ? it.calTabNoteSource : it.calTabNoteFollower);
        assert.equal(card(context).querySelectorAll(".cal-card-tail").length, 1, `${wide ? "desktop" : "phone"}: one tail`);
        card(context).querySelector(".cal-page-close").click(); await tick(8);
      }
      reset();
    }
  });

  test("«Cambia profilo» says one short line on every cover (IT, EN); the old lines are gone; without a profile «Usa un profilo»", async () => {
    assert.equal(it.cal_LINK_change_desc, "Usa un altro profilo. La corsa del telo resta.");
    assert.equal(en.cal_LINK_change_desc, "Use another profile. The curtain travel stays.");
    for (const key of ["cal_LINK_change_desc_personal", "cal_LINK_change_desc_source", "cal_LINK_change_desc_source_one"]) assert.equal(it[key] ?? en[key], undefined, key);
    const data = family();
    data.covers[1].effective.opening = timing(15, "override");
    data.covers[1].overrides = { opening: data.covers[1].effective.opening };
    const context = await mount({ language: "it", overview: data });
    for (const entity of ["cover.living_one", "cover.kitchen_two", "cover.living_three"]) {
      await open(context, entity);
      // The line under the button.
      assert.equal(card(context).querySelector('.cal-page-link > [data-action="cal-tab-assign"] + .cal-caption').textContent, it.cal_LINK_change_desc, entity);
      card(context).querySelector(".cal-page-close").click(); await tick(8);
    }
    reset();
    // Never calibrated, no profile: the default times' tail under its characteristics; «Usa un profilo».
    const fresh = await mount({ language: "it" });
    await open(fresh, "cover.living_one");
    assert.equal(card(fresh).querySelector(".cal-page-compare, .cal-page-profile"), null, "no box, no card «Profilo»");
    assert.deepEqual(classes(card(fresh).querySelector(".cal-page-traits .cal-card")), ["cal-page-times", "cal-pairs"], "its times (here not from the default nor YAML: no tail), the travel, the slats");
  });

  test("the stylesheet: the two columns side by side from 1024 px with no area spanning rows; the columns' blocks 32 px apart; a second card 16 px under the first; a hairline over the actions; the box's first line without one", () => {
    const css = wideCss(".cal-page-body.is-wide:not(.cal-check) { display: grid");
    assert.match(css, /\.cal-page-body\.is-wide:not\(\.cal-check\) \{ display: grid; grid-template-columns: repeat\(2, minmax\(0, 1fr\)\); align-items: start; gap: var\(--cal-sp-32\) 24px; \}/);
    assert.doesNotMatch(cascadeCss, /grid-template-areas: "overview values"|grid-area: (overview|values|last);|grid-template-rows: auto 1fr/, "no area overflowing its rows");
    assert.match(cascadeCss, /\.cal-page-column \{ display: flex; flex-direction: column; gap: var\(--cal-sp-32\); min-width: 0; \}/);
    assert.match(cascadeCss, /\.cal-section > \.cal-card \+ \.cal-card \{ margin-top: 6px; \}/);
    assert.match(cascadeCss, /\.cal-page-profile-head \+ \.cal-page-link \{ border-top: 1px solid var\(--panel-border\); \}/);
    assert.match(cascadeCss, /\.cal-page \.cal-page-compare > \.cal-compare:first-child \{ border-top: 0; \}/);
  });

  test("the body keeps its bottom padding on every window (24 px from 1024 px), and «Dettagli» is laid out by columns that grow with their content", async () => {
    assert.match(cascadeCss, /\.cal-page-body \{ display: flex; flex-direction: column;[^}]*padding: calc\(var\(--cal-sp-16\) \+ var\(--cal-sp-4\)\) calc\(var\(--cal-sp-16\) \+ var\(--cal-safe-right\)\) calc\(var\(--cal-sp-16\) \+ var\(--cal-sp-8\)\)/);
    assert.match(wideCss(".cal-page-body { padding: 24px; }"), /\.cal-page-body \{ padding: 24px; \}/);
    desktop();
    const context = await mount({ language: "it", overview: family() });
    await open(context, "cover.kitchen_two");
    await pageTab(context.tab, "details");
    // The box is always open (no disclosure); it stays inside its column across the reads.
    assert.equal(card(context).querySelector(".cal-page-values details.cal-compare"), null);
    context.panel._calibrationTab.refresh(); await tick(8);
    assert.ok(card(context).querySelector(".cal-page-values .cal-compare .cal-compare-table"));
    assert.deepEqual([...body(context).children].map((child) => child.className), ["cal-page-column", "cal-page-column"], "the table on the left, the history on the right");
    assert.ok(body(context).children[0].querySelector(".cal-page-values"));
  });
});

describe("«Tempi e altezze misurati»", async () => {
  const { translations } = await import("../../custom_components/myhome/frontend/panel/panel-translations.js");
  const it = translations.it, en = translations.en;
  const MEASURED_AT = "2026-11-01T16:09:00+00:00";
  const provenance = (inherited) => ({ source: "guided", recorded_at: MEASURED_AT, origin_entity_id: inherited ? "cover.elsewhere" : null, origin_name: inherited ? "Elsewhere" : null, inherited });
  const timing = (value, origin = "profile", scaled = false, inherited = false) => ({ value, origin, provenance: provenance(inherited), scaled });
  /** «tall» measured on Living room shutter 1 (the source), followed by Kitchen shutter 2; `rolls`: "here", "other" or none. */
  function family({ rolls = null } = {}) {
    const data = sampleOverview();
    const profile = data.profiles[0];
    profile.reference_travel_cm = 110;
    profile.assigned_to = ["cover.living_one", "cover.kitchen_two"];
    profile.provenance.opening.recorded_at = MEASURED_AT;
    const [one, two] = data.covers;
    const roll = (value) => rolls ? { opening_roll: timing(value, "profile", false, rolls === "other"), closing_roll: timing(value + 0.1, "profile", false, rolls === "other") } : {};
    Object.assign(one, { profile_id: "tall", travel_cm: 110, precision: rolls ? "refined" : "basic", slats_state: "yes_measured",
      effective: { opening: timing(21.8), closing: timing(20.4), slat_time_s: timing(1.4), ...roll(1.9) } });
    Object.assign(two, { profile_id: "tall", travel_cm: 130, precision: "basic", slats_state: "yes_profile",
      effective: { opening: timing(24.6, "profile", true), closing: timing(23.1, "profile", true), slat_time_s: timing(1.4) } });
    return data;
  }
  const row = (context, entity) => context.tab().querySelector(`.cal-row[data-entity="${entity}"]`);
  const card = (context) => context.tab().querySelector(".cal-flow .cal-page:not(.cal-assign-page)");
  async function open(context, entity) {
    row(context, entity).querySelector('[data-action="cal-tab-toggle"]').click();
    await tick(6);
    return card(context);
  }
  const state = (page) => [page.querySelector(".cal-pos-state-title")?.textContent ?? null, page.querySelector(".cal-pos-state-desc")?.textContent ?? null];

  test("«Tempi e altezze misurati»: heights measured here say «… su questa tapparella …»; heights taken from another cover the sentence without it (it, en)", async () => {
    for (const [language, strings] of [["it", it], ["en", en]]) {
      const here = await mount({ language, overview: family({ rolls: "here" }) });
      assert.deepEqual(state(await open(here, "cover.living_one")), [strings.cal_POS_curve_title, strings.cal_POS_curve_text]);
      reset();
      const other = await mount({ language, overview: family({ rolls: "other" }) });
      assert.deepEqual(state(await open(other, "cover.living_one")), [strings.cal_POS_curve_title, strings.cal_POS_curve_text_other]);
      reset();
    }
    // «Le fermate intermedie tengono conto anche…».
    assert.equal(it.cal_POS_curve_text_other, "Le fermate intermedie tengono conto anche delle altezze misurate con il metro.");
    assert.equal(en.cal_POS_curve_text_other, "Intermediate stops also take into account the heights measured with a tape measure.");
  });
});

describe("the sections' titles", async () => {
  const { translations } = await import("../../custom_components/myhome/frontend/panel/panel-translations.js");
  const it = translations.it;
  /** A window `width` px wide and 800 high for `matchMedia`, motion reduced. */
  const windowOf = (width) => (query) => ({ matches: query.split(",").some((part) => [...part.matchAll(/\(\s*([a-z-]+)\s*:\s*([a-z0-9-]+)\s*\)/g)].every(([, name, value]) =>
    name === "max-width" ? width <= parseFloat(value) : name === "min-width" ? width >= parseFloat(value) : name === "max-height" ? 800 <= parseFloat(value)
      : name === "min-height" ? 800 >= parseFloat(value) : name === "orientation" ? value === "landscape" : name === "prefers-reduced-motion")), addEventListener() {}, removeEventListener() {} });
  const MEASURED_AT = "2026-11-01T16:09:00+00:00";
  const measured = { source: "guided", recorded_at: MEASURED_AT, origin_entity_id: null, origin_name: null, inherited: false };
  const timing = (value) => ({ value, origin: "profile", provenance: measured, scaled: false });
  /** «tall» measured on Living room shutter 1 (`rolls`: fine-tuned with the tape), followed by Kitchen shutter 2. */
  function family({ rolls = false } = {}) {
    const data = sampleOverview();
    const profile = data.profiles[0];
    profile.reference_travel_cm = 110;
    profile.assigned_to = ["cover.living_one", "cover.kitchen_two"];
    profile.provenance.opening.recorded_at = MEASURED_AT;
    const [one, two] = data.covers;
    const roll = rolls ? { opening_roll: timing(1.9), closing_roll: timing(2.0) } : {};
    Object.assign(one, { profile_id: "tall", travel_cm: 110, precision: rolls ? "refined" : "basic", slats_state: "yes_measured",
      effective: { opening: timing(21.8), closing: timing(20.4), slat_time_s: timing(1.4), ...roll } });
    Object.assign(two, { profile_id: "tall", travel_cm: 130, precision: "basic", slats_state: "yes_profile",
      effective: { opening: timing(24.6), closing: timing(23.1), slat_time_s: timing(1.4) } });
    return data;
  }
  /**
   * Every state «Panoramica» says: never calibrated, the source basic and fine-tuned, a follower. The
   * section is «Stato»; a cover never calibrated has no state's title in its card (the chip opens it).
   */
  const STATES = [
    ["never calibrated", () => undefined, "cover.living_one", null],
    ["basic", () => family(), "cover.living_one", it.cal_POS_basic_title],
    ["fine-tuned", () => family({ rolls: true }), "cover.living_one", it.cal_POS_curve_title],
    ["follower", () => family(), "cover.kitchen_two", it.cal_POS_follow_title],
  ];
  const card = (context) => context.tab().querySelector(".cal-flow .cal-page:not(.cal-assign-page)");
  async function open(context, entity) {
    context.tab().querySelector(`.cal-row[data-entity="${entity}"] [data-action="cal-tab-toggle"]`).click();
    await tick(6);
    return card(context);
  }

  for (const width of [375, 1280]) {
    for (const [name, overview, entity, title] of STATES) {
      test(`${width} px, ${name}: «Stato» is the section's title over the card, outside it`, async () => {
        globalThis.matchMedia = windowOf(width);
        const data = overview();
        const context = await mount({ language: "it", ...(data ? { overview: data } : {}) });
        const page = await open(context, entity);
        const status = page.querySelector(".cal-card.cal-pos-status");
        assert.equal(page.querySelector(".cal-pos-state-label"), null, "no small label any more");
        assert.doesNotMatch(page.textContent, /Posizionamento attuale/, "the old title is gone");
        // A cover never calibrated too has «Stato» over its card.
        const section = page.querySelector(".cal-pos-current");
        assert.ok(section?.matches("section.cal-section"), "a section of its own");
        assert.deepEqual([...section.children].map((child) => `${child.tagName.toLowerCase()}.${child.classList[child.classList.length - 1]}`),
          ["h4.cal-section-title", "div.cal-pos-status"], "the title, then the card");
        assert.equal(section.querySelector(":scope > h4.cal-section-title").textContent, "Stato");
        // A cover never calibrated: the chip, then the sentence; no state's title in the card.
        if (!title) assert.equal(status.querySelector(".cal-pos-state-title"), null);
        else assert.equal(status.querySelector(".cal-pos-state-title").textContent, title, "the state's title in the card");
        // The same class as the titles of the right column (on the phone: under it).
        const right = page.querySelector(".cal-page-traits > .cal-section-title, .cal-pos-how > .cal-section-title");
        assert.equal(right.className, "cal-section-title");
        if (width === 1280) {
          const body = page.querySelector(".cal-page-body");
          assert.ok(body.matches(".is-wide"), "two columns");
          const [left, other] = body.querySelectorAll(":scope > .cal-page-column");
          // Both columns open with their block, so their titles stand level.
          assert.ok(left.firstElementChild === status || left.firstElementChild === page.querySelector(".cal-pos-current"), "the left column opens with «Stato»");
          assert.equal(other.firstElementChild.firstElementChild.className, "cal-section-title", "the right column opens with its title");
        } else {
          assert.equal(page.querySelector(".cal-page-column"), null, "one column on the phone");
        }
      });
    }
  }

  test("the left title has the right column's style: one rule for both, at every width; both columns start at the top", () => {
    const wide = block("@media (min-width: 1024px)", cascadeCss.slice(cascadeCss.lastIndexOf("@media (min-width: 1024px)", cascadeCss.indexOf(".cal-page-body.is-wide:not(.cal-check)"))));
    const body = dom.window.document.createElement("div");
    body.className = "cal-page-body is-wide";
    body.dataset.tab = "overview";
    body.innerHTML = `<div class="cal-page-column"><section class="cal-section cal-pos-current"><h4 class="cal-section-title">a</h4></section></div>
    <div class="cal-page-column"><section class="cal-section cal-page-traits"><h4 class="cal-section-title">b</h4></section></div>`;
    const [left, right] = body.querySelectorAll(".cal-section-title");
    for (const property of ["font-size", "line-height", "font-weight", "margin", "color"]) {
      assert.equal(winner(left, property), winner(right, property), `${property} on the phone`);
      assert.equal(winner(left, property, wide) || winner(left, property), winner(right, property, wide) || winner(right, property), `${property} from 1024 px`);
    }
    assert.match(wide, /\.cal-page-body\.is-wide:not\(\.cal-check\) \{[^}]*align-items: start;/);
    assert.match(cascadeCss, /\.cal-page-column > \* \{ flex-shrink: 0; margin: 0; \}/);
    assert.doesNotMatch(cascadeCss, /\.cal-pos-state-label/, "the small label's rule gone");
  });
});

describe("«Stato», «Caratteristiche», «Profilo»", async () => {
  const { translations } = await import("../../custom_components/myhome/frontend/panel/panel-translations.js");
  const { checkSentence, statusBadge } = await import("../../custom_components/myhome/frontend/panel/panel-calibration-tab.js");
  const it = translations.it, en = translations.en;
  /** A window `width` px wide and 800 high for `matchMedia`, motion reduced. */
  const windowOf = (width) => (query) => ({ matches: query.split(",").some((part) => [...part.matchAll(/\(\s*([a-z-]+)\s*:\s*([a-z0-9-]+)\s*\)/g)].every(([, name, value]) =>
    name === "max-width" ? width <= parseFloat(value) : name === "min-width" ? width >= parseFloat(value) : name === "max-height" ? 800 <= parseFloat(value)
      : name === "min-height" ? 800 >= parseFloat(value) : name === "orientation" ? value === "landscape" : name === "prefers-reduced-motion")), addEventListener() {}, removeEventListener() {} });
  const desktop = () => { globalThis.matchMedia = windowOf(1280); };
  const phone = () => { globalThis.matchMedia = windowOf(390); };
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
  const card = (context) => context.tab().querySelector(".cal-flow .cal-page:not(.cal-assign-page)");
  const body = (context) => card(context).querySelector(".cal-page-body");
  async function open(context, entity) {
    row(context, entity).querySelector('[data-action="cal-tab-toggle"]').click();
    await tick(6);
    return card(context);
  }
  const plain = (value) => String(value ?? "").replace(/ /g, " ");
  const pairs = (scope) => [...scope.querySelectorAll(".cal-pairs > div")].map((line) => [line.querySelector("dt").textContent, plain(line.querySelector("dd").textContent).trim()]);

  test("the tabs: «Panoramica» first, then «Dettagli»; a cover never calibrated has none: its chip over its sentence, «Come vuoi calibrarla?», its characteristics", async () => {
    phone();
    const context = await mount({ language: "it", overview: family() });
    let page = await open(context, "cover.living_one");
    assert.deepEqual([...page.querySelectorAll('.cal-page-tabs [role="tab"]')].map((tab) => [tab.id, tab.textContent, tab.getAttribute("aria-selected")]),
      [["cal-page-tab-overview", "Panoramica", "true"], ["cal-page-tab-details", "Dettagli", "false"]]);
    assert.equal(body(context).dataset.tab, "overview");
    card(context).querySelector(".cal-page-close").click(); await tick(8);
    page = await open(context, "cover.living_three");
    assert.equal(page.querySelector(".cal-page-tabs"), null, "no tabs");
    assert.equal(body(context).getAttribute("role"), null, "no tab panel without tabs");
    assert.deepEqual([...page.querySelector(".cal-pos-state").children].map((child) => child.className), ["cal-pos-state-chip", "cal-pos-state-desc"]);
    // «Stato» over its card, as on the other covers.
    assert.deepEqual([...body(context).children].map((child) => child.classList[child.classList.length - 1]), ["cal-pos-current", "cal-pos-how", "cal-page-traits"]);
    assert.equal(page.querySelector(".cal-page-traits .cal-card-tail").textContent.startsWith("Valori predefiniti (20 s)"), true, "where its times come from");
    assert.ok(page.querySelector('.cal-page-traits [data-action="cal-tab-travel-edit"]') && page.querySelector('.cal-page-traits [data-action="cal-tab-slats-edit"]'), "the travel's and the slats' pencils");
  });

  test("«Stato»: the title, the sentence with the check's sentence after it, the chip under them; the same sentence in the chip's title, here and in the list", async () => {
    phone();
    const context = await mount({ language: "it", overview: family() });
    const page = await open(context, "cover.living_one");
    const state = page.querySelector(".cal-pos-current > .cal-card.cal-pos-status > .cal-pos-state");
    assert.equal(page.querySelector(".cal-pos-current > .cal-section-title").textContent, "Stato");
    assert.deepEqual([...state.children].map((child) => child.className), ["cal-pos-state-title", "cal-pos-state-desc", "cal-pos-state-chip"], "the chip under title and text");
    const sentence = "Alla verifica a metà corsa si è fermata a 2 cm dal punto atteso.";
    assert.equal(state.querySelector(".cal-pos-state-title").textContent, "Tempi e altezze misurati");
    assert.equal(state.querySelector(".cal-pos-state-desc").textContent, `${it.cal_POS_curve_text} ${sentence}`);
    const chip = state.querySelector(".cal-badge");
    assert.deepEqual([chip.textContent, chip.dataset.tone, chip.title], ["Calibrata · 2 cm", "success", sentence]);
    assert.equal(row(context, "cover.living_one").querySelector(".cal-row-status .cal-badge").title, sentence, "the list's chip too");
    assert.equal(page.querySelector(".cal-pos-check"), null, "no row «Verifica del posizionamento»");
    // A follower: «Usa i tempi di un profilo», the two travels, «Dal profilo» under them; its chip's title says what
    // it means.
    card(context).querySelector(".cal-page-close").click(); await tick(8);
    const follower = await open(context, "cover.kitchen_two");
    assert.deepEqual([...follower.querySelectorAll(".cal-pos-state > *")].map((child) => plain(child.textContent)),
      ["Usa i tempi di un profilo", "I tempi del profilo sono adattati alla corsa di questa tapparella: 115 cm invece di 110.", "Adattata dal profilo"]);
    assert.equal(follower.querySelector(".cal-pos-state-chip .cal-badge").title, "Usa i tempi del profilo «tall_two_leaf», adattati alla sua corsa.");
  });

  test("the check's sentence: within 4 cm alone, green; beyond, «Oltre 4 cm conviene perfezionare il posizionamento.», orange; not at half the travel, without «a metà corsa»", async () => {
    const t = (key) => it[key];
    const item = (gap, half = true) => ({ status: "verified", check: gap, cover: { check_expected_cm: half ? 55 : 82.5, travel_cm: 110 }, profile: null });
    assert.equal(checkSentence(item(2), t, "it"), "Alla verifica a metà corsa si è fermata a 2 cm dal punto atteso.");
    assert.equal(checkSentence(item(4), t, "it"), "Alla verifica a metà corsa si è fermata a 4 cm dal punto atteso.", "the threshold itself passes");
    assert.equal(checkSentence(item(6), t, "it"), "Alla verifica a metà corsa si è fermata a 6 cm dal punto atteso. Oltre 4 cm conviene perfezionare il posizionamento.");
    assert.equal(checkSentence(item(0.3, false), t, "it"), "Alla verifica si è fermata a 0,3 cm dal punto atteso.");
    assert.equal(checkSentence(item(6, false), (key) => en[key], "en"), "At the check it stopped 6 cm from the expected point. Beyond 4 cm, refining the positioning is worthwhile.");
    assert.equal(checkSentence({ status: "calibrated", check: null }, t, "it"), "", "no check: no sentence");
    assert.match(statusBadge(item(6), t, "it"), /data-tone="warning" title="Alla verifica a metà corsa si è fermata a 6 cm dal punto atteso\. Oltre 4 cm conviene perfezionare il posizionamento\."><ha-icon icon="mdi:check" aria-hidden="true"><\/ha-icon>Calibrata · 6 cm</);
    // Without a check the title says what the chip means (none here: no profile to name).
    assert.equal(statusBadge({ status: "profile" }, t, "it"), '<span class="badge cal-badge" data-status="profile"><ha-icon icon="mdi:link-variant" aria-hidden="true"></ha-icon>Dal profilo</span>', "no profile, no title");
    desktop();
    const context = await mount({ language: "it", overview: family({ gap: 6 }) });
    const page = await open(context, "cover.living_one");
    assert.match(page.querySelector(".cal-pos-state-desc").textContent, /Oltre 4 cm conviene perfezionare il posizionamento\.$/);
    assert.equal(page.querySelector(".cal-pos-state-chip .cal-badge").dataset.tone, "warning");
  });

  test("«Caratteristiche»: no labels on the source (its slats «Sì»), «· impostata qui» on a follower's own travel and «Sì · dal profilo»; a time set on a follower «impostato qui»; no coefficients", async () => {
    phone();
    const data = family();
    const two = data.covers[1];
    two.effective.opening = timing(15, "override", false, measured);
    two.overrides = { opening: 15 };
    const context = await mount({ language: "it", overview: data });
    let page = await open(context, "cover.living_one");
    const times = (scope) => [...scope.querySelectorAll(".cal-page-times .cal-measure")].map((item) => [item.querySelector(".cal-label-long").textContent, item.querySelector(".cal-measure-caption")?.textContent ?? ""]);
    assert.deepEqual(times(page), [["Tempo di salita", ""], ["Tempo di discesa", ""], ["Tempo lamelle", ""]]);
    assert.deepEqual(pairs(page.querySelector(".cal-page-traits")), [["Corsa del telo", "110 cm"], ["Lamelle", "Sì"]]);
    assert.doesNotMatch(page.textContent, /Coefficiente/);
    card(context).querySelector(".cal-page-close").click(); await tick(8);
    page = await open(context, "cover.kitchen_two");
    assert.deepEqual(times(page), [["Tempo di salita", "impostato qui"], ["Tempo di discesa", ""], ["Tempo lamelle", ""]]);
    assert.deepEqual(pairs(page.querySelector(".cal-page-traits")), [["Corsa del telo", "115 cm · impostata qui"], ["Lamelle", "Sì · dal profilo"]]);
    assert.deepEqual([...page.querySelectorAll(".cal-page-traits .cal-inline-icon")].map((icon) => icon.dataset.action), ["cal-tab-travel-edit", "cal-tab-travel-unlink"], "the pencil and the chain");
  });

  test("«Profilo»: the profile's own symbol on every cover, its name, «Misurato su…», «Cambia profilo» (one button, Scollega in its window)", async () => {
    phone();
    const context = await mount({ language: "it", overview: family() });
    for (const [entity, measuredOn, actions] of [["cover.living_one", "Tempi misurati su questa tapparella", ["cal-tab-assign"]], ["cover.kitchen_two", "Tempi misurati sulla tapparella Living room shutter 1", ["cal-tab-assign"]]]) {
      const page = await open(context, entity);
      const head = page.querySelector(".cal-page-profile .cal-page-profile-head");
      assert.equal(head.querySelector(".cal-profile-icon ha-icon").getAttribute("icon"), "mdi:clipboard-text-outline");
      assert.equal(head.querySelector(".cal-profile-icon").title, "Profilo");
      assert.equal(head.querySelector(".cal-profile-name").textContent, "tall_two_leaf");
      assert.ok(head.querySelector(".cal-page-role").textContent.startsWith(measuredOn));
      assert.deepEqual([...page.querySelectorAll(".cal-page-profile .cal-page-link button")].map((button) => button.dataset.action), actions);
      card(context).querySelector(".cal-page-close").click(); await tick(8);
    }
  });
});

describe("sentences and dates", async () => {
  const { translations } = await import("../../custom_components/myhome/frontend/panel/panel-translations.js");
  const { checkSentence, statusBadge, ICON } = await import("../../custom_components/myhome/frontend/panel/panel-calibration-tab.js");
  const text = await import("../../custom_components/myhome/frontend/panel/panel-calibration-text.js");
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
  async function close(context) { card(context).querySelector(".cal-page-close").click(); await tick(8); }
  const plain = (value) => String(value ?? "").replace(/[  ]/g, " ").trim();

  test("beyond 4 cm a follower is told to measure its times again (it has no Perfeziona); the source and a cover alone as before", async () => {
    const t = (key) => it[key];
    const follower = { status: "verified", check: 6, cover: { entity_id: "cover.kitchen_two", check_expected_cm: 57.5, travel_cm: 115 },
      profile: { provenance: { opening: { origin_entity_id: "cover.living_one" } } } };
    const sentence = "Alla verifica a metà corsa si è fermata a 6 cm dal punto atteso. Oltre 4 cm conviene misurare di nuovo i tempi.";
    assert.equal(checkSentence(follower, t, "it"), sentence);
    assert.match(statusBadge(follower, t, "it"), new RegExp(`title="${sentence.replace(/\./g, "\\.")}"`), "the chip's title too");
    assert.equal(checkSentence({ ...follower, check: 3 }, t, "it"), "Alla verifica a metà corsa si è fermata a 3 cm dal punto atteso.", "within 4 cm: the sentence alone");
    assert.match(checkSentence({ ...follower, profile: { provenance: { opening: { origin_entity_id: "cover.kitchen_two" } } } }, t, "it"), /Oltre 4 cm conviene perfezionare il posizionamento\.$/, "the source");
    assert.match(checkSentence({ ...follower, profile: null }, t, "it"), /Oltre 4 cm conviene perfezionare il posizionamento\.$/, "a cover alone");
    assert.equal(it.cal_POS_check_beyond_follower, "Oltre {limit} cm conviene misurare di nuovo i tempi.");
    assert.equal(en.cal_POS_check_beyond_follower, "Beyond {limit} cm, measuring the times again is worthwhile.");
    // On the page: «Stato» and the chip of a follower with a check kept beyond 4 cm.
    phone();
    const data = family();
    Object.assign(data.covers[1], { precision: "verified", check_cm: 6, check_expected_cm: 57.5, check_measured_cm: 63.5, check_at: CHECKED_AT });
    const context = await mount({ language: "it", overview: data });
    const page = await open(context, "cover.kitchen_two");
    assert.match(page.querySelector(".cal-pos-state-desc").textContent, /Oltre 4 cm conviene misurare di nuovo i tempi\.$/);
    assert.match(page.querySelector(".cal-pos-state-chip .cal-badge").title, /Oltre 4 cm conviene misurare di nuovo i tempi\.$/);
  });

  test("«Profilo» in «Panoramica»: «Tempi misurati su **questa tapparella** · {date}», «Tempi misurati sulla tapparella **X** · {date}»", async () => {
    assert.deepEqual([it.calTabRoleSource, it.calTabRoleSourceNoDate, it.calTabRoleFollower, it.calTabRoleFollowerNoDate],
      ["Tempi misurati su **questa tapparella** · {date}", "Tempi misurati su **questa tapparella**", "Tempi misurati sulla tapparella {cover} · {date}", "Tempi misurati sulla tapparella {cover}"]);
    assert.deepEqual([en.calTabRoleSource, en.calTabRoleSourceNoDate, en.calTabRoleFollower, en.calTabRoleFollowerNoDate],
      ["Times measured on **this cover** · {date}", "Times measured on **this cover**", "Times measured on the cover {cover} · {date}", "Times measured on the cover {cover}"]);
    phone();
    const when = text.dateTimeText(MEASURED_AT, "it");
    const context = await mount({ language: "it", overview: family() });
    let role = (await open(context, "cover.living_one")).querySelector(".cal-page-profile-head .cal-page-role");
    assert.deepEqual([plain(role.textContent), role.querySelector("strong").textContent], [`Tempi misurati su questa tapparella · ${plain(when)}`, "questa tapparella"]);
    await close(context);
    role = (await open(context, "cover.kitchen_two")).querySelector(".cal-page-profile-head .cal-page-role");
    assert.deepEqual([plain(role.textContent), role.querySelector("strong").textContent], [`Tempi misurati sulla tapparella Living room shutter 1 · ${plain(when)}`, "Living room shutter 1"]);
  });
});

describe("chips never cut", async () => {
  /** A window `width` px wide and 800 high for `matchMedia`, motion reduced. */
  const windowOf = (width) => (query) => ({ matches: query.split(",").some((part) => [...part.matchAll(/\(\s*([a-z-]+)\s*:\s*([a-z0-9-]+)\s*\)/g)].every(([, name, value]) =>
    name === "max-width" ? width <= parseFloat(value) : name === "min-width" ? width >= parseFloat(value) : name === "max-height" ? 800 <= parseFloat(value)
      : name === "min-height" ? 800 >= parseFloat(value) : name === "orientation" ? value === "landscape" : name === "prefers-reduced-motion")), addEventListener() {}, removeEventListener() {} });
  const phone = () => { globalThis.matchMedia = windowOf(390); };
  const plain = (value) => String(value ?? "").replace(/[  ]/g, " ").trim();
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

  test("the card: the status chip in a line of its own, never cut; the profile's name whole in the card «Profilo»", async () => {
    assert.doesNotMatch(cascadeCss.match(/\.cal-pos-state-chip > \.cal-badge \{[^}]*\}/)[0], /overflow: hidden|text-overflow/);
    assert.match(cascadeCss, /\.cal-pos-state-chip \{ display: flex; \}/);
    phone();
    const context = await mount({ language: "it", overview: family({ travel: 130 }) });
    const page = await open(context, "cover.kitchen_two");
    const badge = page.querySelector(".cal-pos-state-chip > .cal-badge");
    assert.equal(badge.textContent, "Adattata dal profilo");
    assert.notEqual(winner(badge, "text-overflow"), "ellipsis");
    assert.equal(plain(page.querySelector(".cal-page-profile-head .cal-profile-name").textContent), "tall_two_leaf", "whole");
  });
});

describe("a cover never calibrated, «Cambia profilo»", async () => {
  const PANEL = "../../custom_components/myhome/frontend/panel/";
  const { translations } = await import(`${PANEL}panel-translations.js`);
  const it = translations.it, en = translations.en;
  const source = readFileSync(new URL(`${PANEL}panel-calibration-tab.js`, import.meta.url), "utf8");
  /** A window `width` px wide and 800 high for `matchMedia`, motion reduced. */
  const windowOf = (width) => (query) => ({ matches: query.split(",").some((part) => [...part.matchAll(/\(\s*([a-z-]+)\s*:\s*([a-z0-9-]+)\s*\)/g)].every(([, name, value]) =>
    name === "max-width" ? width <= parseFloat(value) : name === "min-width" ? width >= parseFloat(value) : name === "max-height" ? 800 <= parseFloat(value)
      : name === "min-height" ? 800 >= parseFloat(value) : name === "orientation" ? value === "landscape" : name === "prefers-reduced-motion")), addEventListener() {}, removeEventListener() {} });
  const desktop = () => { globalThis.matchMedia = windowOf(1280); };
  const phone = () => { globalThis.matchMedia = windowOf(390); };
  const row = (context, entity) => context.tab().querySelector(`.cal-row[data-entity="${entity}"]`);
  const card = (context) => context.tab().querySelector(".cal-flow .cal-page:not(.cal-assign-page)");
  async function open(context, entity) {
    row(context, entity).querySelector('[data-action="cal-tab-toggle"]').click();
    await tick(6);
    return card(context);
  }
  async function close(context) { card(context).querySelector(".cal-page-close").click(); await tick(8); }
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

  test("a cover never calibrated: «Stato» over its card; from 1024 px «Stato» then «Come vuoi calibrarla?» on the left, «Caratteristiche» on the right; the phone in that order", async () => {
    desktop();
    let context = await mount({ language: "it", overview: world() });
    let page = await open(context, "cover.living_one");
    const [left, right] = page.querySelectorAll(".cal-page-body.is-wide > .cal-page-column");
    assert.deepEqual([...left.children].map((child) => child.classList[1]), ["cal-pos-current", "cal-pos-how"]);
    assert.deepEqual([...right.children].map((child) => child.classList[1]), ["cal-page-traits"]);
    assert.equal(left.querySelector(".cal-pos-current > .cal-section-title").textContent, "Stato");
    assert.ok(left.querySelector(".cal-pos-current > .cal-card.cal-pos-status > .cal-pos-state"), "the card under its title");
    reset();
    phone();
    context = await mount({ language: "it", overview: world() });
    page = await open(context, "cover.living_one");
    assert.deepEqual([...page.querySelector(".cal-page-body").children].map((child) => child.classList[1]), ["cal-pos-current", "cal-pos-how", "cal-page-traits"]);
    assert.deepEqual([...page.querySelectorAll(".cal-section > .cal-section-title")].map((title) => title.textContent), ["Stato", "Come vuoi calibrarla?", "Caratteristiche"]);
  });

  test("«Cambia profilo» (without a profile «Assegna profilo») is one secondary button with its caption under it; no row with › in the card", async () => {
    phone();
    const context = await mount({ language: "it", overview: world({ alone: "basic" }) });
    const link = async (entity) => {
      const page = await open(context, entity);
      const group = page.querySelector(".cal-page-profile .cal-page-profile-card > .cal-page-profile-head + .cal-page-link:last-child");
      const shape = [[...group.children].map((child) => `${child.tagName.toLowerCase()}.${child.className}`), group.querySelector("button").textContent,
        group.querySelector("button").dataset.action, group.querySelector(".cal-caption").textContent, !!group.querySelector(".cal-choice")];
      await close(context);
      return shape;
    };
    const change = [["button.cal-outlined", "p.cal-caption"], "Cambia profilo", "cal-tab-assign", it.cal_LINK_change_desc, false];
    assert.deepEqual(await link("cover.kitchen_two"), change, "the source");
    assert.deepEqual(await link("cover.living_three"), change, "a follower: no Scollega in the card");
    assert.deepEqual(await link("cover.living_one"), [["button.cal-outlined", "p.cal-caption"], "Assegna profilo", "cal-tab-assign", it.cal_POS_none_assign_caption, false], "without a profile");
    assert.match(css, /\.cal-page-link > button \{ min-height: var\(--cal-target\); justify-content: center; font-size: var\(--cal-fs-16\); line-height: var\(--cal-lh-16\); \}/, "sized as Perfeziona's");
    assert.doesNotMatch(source, /cal-tab-unlink|cal_LINK_unlink/, "the card's Scollega is gone");
    for (const key of ["cal_LINK_unlink", "cal_LINK_unlink_desc_none", "cal_LINK_unlink_desc_one", "cal_LINK_unlink_desc_personal"]) assert.deepEqual([it[key], en[key]], [undefined, undefined], key);
  });
});
