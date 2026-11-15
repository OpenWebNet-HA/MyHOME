/**
 * A cover's card, «Dettagli» and the values: the table of the profile and of this cover, «Profilo usato anche da», the dates, the travel edited in its row.
 */
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { after, afterEach, describe, test } from "node:test";

import { sampleOverview } from "./fake-calibration.mjs";
import { panelCss as css } from "./panel-css.mjs";
import { dom, mount, pageTab, reset, tick } from "./wizard-harness.mjs";

const original = globalThis.matchMedia;
afterEach(() => { globalThis.matchMedia = original; reset(); });
after(() => dom.window.close());

describe("a travel from the profile", async () => {
  const row = (tab, id) => [...tab().querySelectorAll(".cal-row")].find((item) => item.dataset.entity === id);
  const { translations } = await import("../../custom_components/myhome/frontend/panel/panel-translations.js");
  const { buildScreen, valueRows, travelText } = await import("../../custom_components/myhome/frontend/panel/panel-calibration-screens.js");
  const { createFakeBackend, sampleOverview } = await import("./fake-calibration.mjs");
  const tIt = (key) => translations.it[key] ?? key, tEn = (key) => translations.en[key] ?? key;

  test("a travel taken from the profile is said: «110 cm · dal profilo», «Come · Collegata al profilo», the help under the table; the status says «Dal profilo»", async () => {
    const overview = sampleOverview();
    Object.assign(overview.covers.find((cover) => cover.entity_id === "cover.kitchen_two"), { travel_cm: null });
    const kitchen = overview.covers.find((cover) => cover.entity_id === "cover.kitchen_two");
    for (const key of ["opening", "closing"]) kitchen.effective[key].provenance = { ...kitchen.effective[key].provenance, inherited: true };
    const { tab, startFor, wiz, code, choosePath, press } = await mount({ language: "it", overview });
    row(tab, "cover.kitchen_two").querySelector(".cal-row-toggle").click(); await tick();
    const pairsOf = () => Object.fromEntries([...tab().querySelectorAll(".cal-page .cal-pairs > div")].map((item) => [item.querySelector("dt").textContent, item.querySelector("dd").textContent.replace(/\u00a0/g, " ")]));
    assert.equal(pairsOf()["Corsa del telo"], "198 cm · dal profilo");
    assert.equal(tab().querySelector(".cal-page .cal-pos-state-chip .cal-badge").textContent, "Dal profilo", "a follower without a check of its own");
    await pageTab(tab, "details");
    assert.equal(pairsOf().Come, "Collegata al profilo");
    const tails = [...tab().querySelectorAll(".cal-page .cal-page-values .cal-card-tail")].map((tail) => tail.textContent.replace(/\u00a0/g, " "));
    assert.deepEqual(tails, ["La corsa del telo è presa dal profilo. Se il telo di questa tapparella è più alto o più basso, puoi modificare la misura riportata qui sopra."]);
    assert.equal(travelText(tIt, (value) => String(value), 120, 198), "120 cm", "a travel measured is the cover's own");
    // («Perfeziona» opens fine-tuning with the tape on its «Prima di iniziare»; C-01's recap is no longer entered.)
    void startFor; void wiz; void code; void choosePath; void press;
  });
});

describe("the travel's pencil", async () => {
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
  const tail = (page) => page.querySelector(".cal-page-values .cal-card-tail").textContent;
  const travelRow = (page) => page.querySelector(".cal-pairs-travel");
  const writes = (backend) => backend.calls.filter((message) => message.type === "myhome/cover_profiles/write");

  test("the details' tail says what the cover is to its profile: the source with or without followers, a follower whose travel is the profile's, one of a profile without a reference travel", async () => {
    const shared = await mount({ overview: sourceOverview(), language: "it" });
    assert.equal(tail(await details(shared, "cover.living_one", "details")), it.calTabNoteSource);
    assert.equal(it.calTabNoteSource, "I valori sono misurati su questa tapparella e il profilo viene condiviso: se questa tapparella viene ricalibrata, si aggiornano anche le tapparelle che seguono lo stesso profilo.");
    shared.tab().querySelector(".cal-page-close").click(); await tick(6);
    // The follower without a travel of its own: «198 cm · dal profilo», and it can be changed above.
    const presumed = await details(shared, "cover.living_three", "details");
    assert.equal(tail(presumed), "La corsa del telo è presa dal profilo. Se il telo di questa tapparella è più alto o più basso, puoi modificare la misura riportata qui sopra.");
    await pageTab(shared.tab, "overview");
    assert.match(travelRow(shared.tab().querySelector(".cal-flow .cal-page")).textContent, /198\s*cm · dal profilo/);
    reset();
    const alone = sourceOverview();
    alone.profiles[0].assigned_to = ["cover.living_one"];
    for (const cover of alone.covers.slice(1)) cover.profile_id = null;
    const single = await mount({ overview: alone, language: "it" });
    assert.equal(tail(await details(single, "cover.living_one", "details")), "I valori sono misurati su questa tapparella e il profilo può essere condiviso.");
    reset();
    const none = await mount({ overview: sourceOverview({ reference: null }) });
    assert.equal(tail(await details(none, "cover.kitchen_two", "details")), en.calTabTravelNoReference, "no reference travel: the honest note, the pencil on");
    await pageTab(none.tab, "overview");
    assert.ok(travelRow(none.tab().querySelector(".cal-flow .cal-page")).querySelector('[data-action="cal-tab-travel-edit"]'));
    for (const key of ["calTabNoteSourceAlone", "calTabTravelNoReference", "calTabTravelEdit", "calTabTravelClear", "calTabTravelRemove", "calTabTravelConfirm", "calTabTravelConfirmOne",
      "calTabTravelConfirmAction", "calTabTravelSave", "calTabTravelInvalid"]) assert.ok(en[key] && it[key], key);
    assert.ok(!/Come una tapparella già misurata|Like a cover already measured/.test(it.calTabTravelFromProfileHelp + en.calTabTravelFromProfileHelp));
  });

  test("the thin pencil edits the travel in its row (field with «cm», tick, ✕, comma accepted, no spin buttons); a refusal under the row; the chain «Usa la corsa del profilo» only on a follower whose own travel differs, asked first", async () => {
    const context = await mount({ overview: sourceOverview(), language: "it" });
    const { tab, backend } = context;
    let page = await details(context, "cover.living_three");
    const icons = (root) => [...travelRow(root).querySelectorAll(".cal-inline-icon")].map((button) => [button.dataset.action, button.querySelector("ha-icon").getAttribute("icon")]);
    assert.deepEqual(icons(page), [["cal-tab-travel-edit", "mdi:pencil-outline"]], "the profile's travel: the pencil only");
    travelRow(page).querySelector('[data-action="cal-tab-travel-edit"]').click(); await tick();
    const field = () => tab().querySelector('.cal-inline-field input[name="travel_cm"]');
    assert.deepEqual([field().type, field().inputMode, field().value], ["text", "decimal", "198"], "a text field with the decimal keyboard: «,» works");
    assert.ok(travelRow(tab()).classList.contains("is-editing"));
    assert.deepEqual(icons(tab()), [["cal-tab-travel-save", "mdi:check"], ["cal-tab-travel-cancel", "mdi:close"]]);
    // Under the row, only the note on what the field takes (a follower: back to the profile's travel).
    assert.equal(travelRow(tab()).querySelector(".cal-inline-below").textContent.trim(), it.calTabTravelHelpFollower);
    assert.equal(tab().querySelector(".cal-travel-buttons"), null, "no buttons under the row");
    field().value = "5"; tab().querySelector('[data-action="cal-tab-travel-save"]').click(); await tick();
    assert.equal(travelRow(tab()).querySelector(".cal-inline-below .cal-travel-error").textContent, it.calTabTravelInvalid);
    assert.equal(writes(backend).length, 0);
    field().value = "121,5"; field().dispatchEvent(new KeyboardEvent("keydown", { key: "Enter", bubbles: true })); await tick(8);
    assert.deepEqual(writes(backend).map(({ action, travel_cm, entity_id }) => [action, travel_cm, entity_id]), [["manual_travel", 121.5, "cover.living_three"]]);
    page = tab().querySelector(".cal-flow .cal-page");
    assert.match(travelRow(page).textContent, /121,5\s*cm · impostata qui/, "its own travel: «impostata qui»");
    assert.deepEqual(icons(page), [["cal-tab-travel-edit", "mdi:pencil-outline"], ["cal-tab-travel-unlink", "mdi:link-variant"]], "its own travel, other than the profile's: the chain");
    travelRow(page).querySelector('[data-action="cal-tab-travel-unlink"]').click(); await tick();
    assert.equal(travelRow(tab()).querySelector(".cal-travel-confirm").textContent, "La corsa torna quella del profilo (198 cm) e i tempi si adattano di conseguenza.");
    assert.equal(writes(backend).length, 1, "nothing written before the confirmation");
    tab().querySelector('[data-action="cal-tab-travel-unlink-confirm"]').click(); await tick(8);
    assert.equal(writes(backend).at(-1).travel_cm, null, "back to the profile's travel");
    // Escape leaves the field, not the page.
    travelRow(tab().querySelector(".cal-flow .cal-page")).querySelector('[data-action="cal-tab-travel-edit"]').click(); await tick();
    field().dispatchEvent(new KeyboardEvent("keydown", { key: "Escape", bubbles: true })); await tick();
    assert.ok(tab().querySelector(".cal-flow .cal-page") && !field());
    const css = readFileSync(new URL("../../custom_components/myhome/frontend/panel/panel-calibration.css", import.meta.url), "utf8");
    assert.match(css, /appearance: textfield;/);
    assert.match(css, /::-webkit-inner-spin-button \{ -webkit-appearance: none; margin: 0; \}/);
    assert.match(css, /\.cal-inline-icons \{ display: inline-flex; align-items: center; margin-block: calc\(-1 \* var\(--cal-sp-12\)\);/, "44 px targets that do not raise the row");
    assert.match(css, /\.cal-inline-field input \{[^}]*height: 32px; min-height: 0; margin: -6px 0;/, "nor does the field");
  });

  test("on the source with followers that have a travel of their own the tick asks first: «Cambia anche i tempi di N tapparelle…», Annulla / Conferma; the source has no way to remove it", async () => {
    const context = await mount({ overview: sourceOverview({ followerTravel: [110, 150] }), language: "it" });
    const { tab, backend } = context;
    const page = await details(context, "cover.living_one");
    assert.equal(travelRow(page).querySelector('[data-action="cal-tab-travel-unlink"], [data-action="cal-tab-travel-clear"]'), null);
    travelRow(page).querySelector('[data-action="cal-tab-travel-edit"]').click(); await tick();
    tab().querySelector('.cal-inline-field input[name="travel_cm"]').value = "200";
    tab().querySelector('[data-action="cal-tab-travel-save"]').click(); await tick();
    assert.equal(tab().querySelector(".cal-travel-confirm").textContent, "Cambia anche i tempi di 2 tapparelle che seguono il profilo.");
    assert.deepEqual([...tab().querySelectorAll(".cal-travel-buttons button")].map((button) => button.textContent), ["Annulla", "Conferma"]);
    assert.equal(writes(backend).length, 0, "nothing written before the confirmation");
    tab().querySelector('.cal-travel-buttons [data-action="cal-tab-travel-cancel"]').click(); await tick();
    assert.equal(writes(backend).length, 0);
    travelRow(tab().querySelector(".cal-flow .cal-page")).querySelector('[data-action="cal-tab-travel-edit"]').click(); await tick();
    const field = tab().querySelector('.cal-inline-field input[name="travel_cm"]');
    field.value = "200";
    field.dispatchEvent(new KeyboardEvent("keydown", { key: "Enter", bubbles: true })); await tick();
    tab().querySelector('[data-action="cal-tab-travel-confirm"]').click(); await tick(8);
    assert.deepEqual(writes(backend).map(({ action, travel_cm }) => [action, travel_cm]), [["manual_travel", 200]]);
    assert.equal(backend.overview.profiles[0].reference_travel_cm, 200, "the profile's reference travel too (Home Assistant updates it for the source)");
    reset();
    // Followers without a travel of their own keep their times: nothing to ask.
    const quiet = await mount({ overview: sourceOverview({ followerTravel: [null, null] }) });
    const own = await details(quiet, "cover.living_one");
    travelRow(own).querySelector('[data-action="cal-tab-travel-edit"]').click(); await tick();
    quiet.tab().querySelector('.cal-inline-field input[name="travel_cm"]').value = "190";
    quiet.tab().querySelector('[data-action="cal-tab-travel-save"]').click(); await tick(8);
    assert.equal(writes(quiet.backend).at(-1).travel_cm, 190);
  });
});

describe("the travel's field", async () => {
  const { translations } = await import("../../custom_components/myhome/frontend/panel/panel-translations.js");
  const i18n = translations;
  const PANEL = new URL("../../custom_components/myhome/frontend/panel/", import.meta.url);
  const read = (name) => readFileSync(new URL(name, PANEL), "utf8");
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
  const travelRow = (root) => root.querySelector(".cal-pairs-travel:not(.cal-pairs-slats)");
  const writes = (backend) => backend.calls.filter((message) => message.type === "myhome/cover_profiles/write");
  async function clearTravel(context, id) {
    const shown = await page(context, id);
    travelRow(shown).querySelector('[data-action="cal-tab-travel-edit"]').click(); await tick();
    const field = context.tab().querySelector('.cal-inline-field input[name="travel_cm"]');
    field.value = "";
    context.tab().querySelector('[data-action="cal-tab-travel-save"]').click(); await tick(8);
  }

  test("from 1024 px the travel field stands 8 px from «cm»; the phone keeps its 4 px", () => {
    const css = read("panel-calibration.css");
    assert.match(css, /\.cal-inline-field \{ display: inline-flex; align-items: center; gap: var\(--cal-sp-4\); \}/);
    assert.match(css, /@media \(min-width: 1024px\) \{ \.cal-inline-field \{ gap: var\(--cal-sp-8\); \} \}/);
  });

  test("the «Lamelle» select keeps the arrow's room, as wide as its longest choice in IT and EN, the travel field's height", () => {
    const css = read("panel-calibration.css");
    const rule = css.match(/\.cal-inline-field select \{([^}]*)\}/)[1];
    assert.match(rule, /padding: 0 34px 0 var\(--cal-sp-8\);/, "the shell's 34 px for the arrow, never under it");
    assert.match(rule, /min-width: calc\(12ch \+ 34px \+ var\(--cal-sp-8\)\);/);
    assert.match(rule, /height: 32px;/);
    assert.match(css, /\.cal-inline-field input \{[^}]*height: 32px;/);
    const longest = Math.max(...["it", "en"].flatMap((lang) => ["calTabSlatsMarkYes", "calTabSlatsMarkNo", "calTabSlatsMarkUnknown"].map((key) => i18n[lang][key].length)));
    assert.equal(longest, 12, "«Non indicato»: 12 characters, the 12ch");
  });

  test("the travel's field left empty and confirmed clears it, by case: without a reference at once, a follower back to the profile's (asked), the source asked only with followers of their own travel; 0 still refused", async () => {
    // (a) No reference: cleared at once.
    let context = await mount({ overview: sourceData({ reference: null, followerTravel: [110, null] }), language: "it" });
    await clearTravel(context, "cover.kitchen_two");
    assert.deepEqual(writes(context.backend).map(({ action, travel_cm, entity_id }) => [action, travel_cm, entity_id]), [["manual_travel", null, "cover.kitchen_two"]]);
    reset();
    // (b) A follower: back to the profile's travel, asked as the chain.
    context = await mount({ overview: sourceData(), language: "it" });
    await clearTravel(context, "cover.kitchen_two");
    assert.equal(context.tab().querySelector(".cal-travel-confirm").textContent, "La corsa torna quella del profilo (198 cm) e i tempi si adattano di conseguenza.");
    assert.equal(writes(context.backend).length, 0);
    context.tab().querySelector('[data-action="cal-tab-travel-unlink-confirm"]').click(); await tick(8);
    assert.deepEqual(writes(context.backend).map(({ travel_cm }) => travel_cm), [null]);
    reset();
    // (c) The source with followers that have a travel of their own: asked.
    context = await mount({ overview: sourceData({ followerTravel: [110, 150] }), language: "it" });
    await clearTravel(context, "cover.living_one");
    assert.equal(context.tab().querySelector(".cal-travel-confirm").textContent, "Le 2 tapparelle che seguono il profilo smettono di adattare i tempi alla loro corsa.");
    assert.deepEqual([...context.tab().querySelectorAll(".cal-travel-buttons button")].map((button) => button.textContent), ["Annulla", "Conferma"]);
    context.tab().querySelector('[data-action="cal-tab-travel-confirm"]').click(); await tick(8);
    assert.deepEqual(writes(context.backend).map(({ travel_cm, entity_id }) => [travel_cm, entity_id]), [[null, "cover.living_one"]]);
    reset();
    // (c) without followers of their own travel: no question; one follower: the singular.
    context = await mount({ overview: sourceData({ followerTravel: [null, null] }), language: "it" });
    await clearTravel(context, "cover.living_one");
    assert.deepEqual(writes(context.backend).map(({ travel_cm }) => travel_cm), [null]);
    reset();
    context = await mount({ overview: sourceData({ followerTravel: [110, null] }), language: "en" });
    await clearTravel(context, "cover.living_one");
    assert.equal(context.tab().querySelector(".cal-travel-confirm").textContent, "The cover that follows the profile stops adapting its times to its travel.");
    // The note under the row while it is edited; 0 still refused.
    context.tab().querySelector('.cal-travel-buttons [data-action="cal-tab-travel-cancel"]').click(); await tick();
    travelRow(context.tab().querySelector(".cal-flow .cal-page")).querySelector('[data-action="cal-tab-travel-edit"]').click(); await tick();
    assert.equal(context.tab().querySelector(".cal-travel-help").textContent, i18n.en.calTabTravelHelp);
    context.tab().querySelector('.cal-inline-field input[name="travel_cm"]').value = "0";
    context.tab().querySelector('[data-action="cal-tab-travel-save"]').click(); await tick();
    assert.equal(context.tab().querySelector(".cal-travel-error").textContent, i18n.en.calTabTravelInvalid);
    assert.equal(writes(context.backend).length, 0);
  });

  test("with the keyboard up for a row being edited, the details' footer steps aside and comes back when the edit ends; the keyboard counts only with a field focused; 16 px around the row", async () => {
    const viewport = new dom.window.EventTarget();
    Object.assign(viewport, { height: dom.window.innerHeight, offsetTop: 0 });
    Object.defineProperty(dom.window, "visualViewport", { configurable: true, value: viewport });
    const css = read("panel-calibration.css");
    assert.match(css, /\[data-keyboard\] \.cal-page:has\(\.is-editing\) \.cal-page-actions \{ display: none; \}/);
    assert.match(css, /dialog :is\(\.cal-pairs > div, \.wiz-field, input, select\) \{ scroll-margin-block: var\(--cal-sp-16\); \}/);
    const resize = async (height) => { viewport.height = height; viewport.dispatchEvent(new dom.window.Event("resize")); await new Promise((resolve) => setTimeout(resolve, 30)); };
    try {
      const context = await mount({ overview: sourceData(), language: "it" });
      const shown = await page(context, "cover.living_three");
      await resize(380);
      assert.equal(context.tab().hasAttribute("data-keyboard"), false, "no field focused: no keyboard (a browser's bar is not one)");
      await resize(dom.window.innerHeight);
      travelRow(shown).querySelector('[data-action="cal-tab-travel-edit"]').click(); await tick();
      const field = context.tab().querySelector('.cal-inline-field input[name="travel_cm"]');
      field.focus();
      await resize(380);
      const hidden = () => context.tab().hasAttribute("data-keyboard") && !!context.tab().querySelector(".cal-page .is-editing");
      assert.ok(hidden(), "keyboard up and a row edited: the footer's rule applies");
      field.dispatchEvent(new KeyboardEvent("keydown", { key: "Escape", bubbles: true })); await tick();
      await new Promise((resolve) => setTimeout(resolve, 30));
      assert.equal(hidden(), false, "the edit ended: the footer back");
      // The card's tabs have no footer (the rule still serves the procedure's outcome).
      assert.equal(context.tab().querySelector(".cal-page .cal-page-actions"), null);
    } finally {
      delete dom.window.visualViewport;
    }
  });
});

describe("the values beside the profile's", async () => {
  const { translations } = await import("../../custom_components/myhome/frontend/panel/panel-translations.js");
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
  const followersOf = (page) => [...page.querySelectorAll(".cal-page-users .cal-user")].map((item) => [item.querySelector(".cal-user-link, .cal-user-name").textContent,
    item.querySelector(".cal-user-mark ha-icon").getAttribute("icon"), item.querySelector(".cal-user-role").textContent]);
  const compareOf = (page) => [...page.querySelectorAll(".cal-page-values .cal-compare tbody tr")].map((item) => [item.querySelector("th").textContent,
    item.querySelector(".cal-compare-profile").textContent.replace(/ /g, " "), item.querySelector(".cal-compare-mark [aria-hidden]").textContent,
    item.querySelector(".cal-compare-cover .cal-compare-value").textContent.replace(/ /g, " ")]);

  test("«Profilo usato anche da» lists the source first with its crown, then the others that follow with the chain, on the source too; a card never lists its own cover", async () => {
    const context = await mount({ overview: overview(), language: "it" });
    const three = await details(context, "cover.living_three", "details");
    assert.equal(three.querySelector(".cal-page-users > .cal-section-title").textContent, "Profilo usato anche da");
    assert.deepEqual(followersOf(three), [["Living room shutter 1", "mdi:crown-outline", "ha misurato il profilo"], ["Kitchen shutter 2", "mdi:link-variant", "segue il profilo"]],
      "the source on top even when another name comes first in the alphabet");
    await close(context);
    const source = await details(context, "cover.living_one", "details");
    assert.equal(source.querySelector(".cal-page-users > .cal-section-title").textContent, "Profilo usato anche da", "the same title on the source");
    assert.deepEqual(followersOf(source), [["Kitchen shutter 2", "mdi:link-variant", "segue il profilo"], ["Living room shutter 3", "mdi:link-variant", "segue il profilo"]]);
    await close(context);
    const two = await details(context, "cover.kitchen_two", "details");
    assert.ok(!followersOf(two).some(([name]) => name === "Kitchen shutter 2"), "never itself");
    assert.equal(en.calTabAlsoUsedBy, "Profile also used by");
    assert.deepEqual([en.calTabUserSource, en.calTabUserFollower], ["measured the profile", "follows the profile"]);
    assert.match(css, /\.cal-user-mark \{[^}]*color: var\(--panel-accent\)/);
  });

  // The mark in this cover's cell, left of its value (`.cal-compare-value`).
  test("«Profilo e questa tapparella» puts the profile's values beside the cover's: = same, → adapted to its travel, × its own, — missing; a replaced value grey with «sostituito qui»; labels and a legend", async () => {
    const data = overview();
    const two = data.covers.find((cover) => cover.entity_id === "cover.kitchen_two");
    Object.assign(two.effective, { opening: timing(15, "override"), closing: timing(12.1, "profile", true), slat_time_s: timing(2.4, "profile", true),
      opening_roll: timing(1.83, "profile", true), closing_roll: timing(1.91, "profile", true) });
    two.overrides = { opening: two.effective.opening };
    const context = await mount({ overview: data, language: "it" });
    const page = await details(context, "cover.kitchen_two", "details");
    // A section of «Dettagli», always open, its title over its card.
    const box = page.querySelector(".cal-page-values .cal-card .cal-compare");
    assert.equal(box.tagName, "DIV", "no disclosure");
    assert.equal(page.querySelector(".cal-page-values > .cal-section-title").textContent, "Valori del profilo e di questa tapparella");
    assert.deepEqual([...box.querySelectorAll("thead th")].map((th) => th.textContent), ["Profilo", "Questa tapparella"]);
    assert.deepEqual(compareOf(page), [
      ["Tempo di salita", "22,4 sNon usato qui", "×", "15,0 s"],
      ["Tempo di discesa", "21,9 s", "→", "12,1 s"],
      ["Tempo lamelle", "4,4 s", "→", "2,4 s"],
      ["Corsa del telo", "198 cm", "×", "110 cm"],
      ["Coefficiente in salita", "1,83", "=", "1,83"],
      ["Coefficiente in discesa", "1,91", "=", "1,91"]]);
    assert.ok(box.querySelector("tbody tr .cal-compare-profile").classList.contains("is-replaced"));
    assert.deepEqual([...box.querySelectorAll("tbody .cal-compare-mark")].map((mark) => [mark.title, mark.querySelector(".visually-hidden").textContent]).slice(0, 2),
      [["Impostato su questa tapparella", "Impostato su questa tapparella"], ["Adattato alla corsa di questa tapparella", "Adattato alla corsa di questa tapparella"]]);
    assert.equal(box.querySelector(".cal-compare-legend").textContent, "= Usato senza modifiche→ Adattato alla corsa di questa tapparella× Impostato su questa tapparella",
      "only the marks shown");
    assert.match(css, /\.cal-compare-table \.cal-compare-profile\.is-replaced \{ color: var\(--panel-text-2\); \}/);
    await close(context);
    // The source: its travel is the reference, its times the profile's: «=»; a slat time this view lacks «—»; the rolls left
    // out (not measured).
    const source = await details(context, "cover.living_one", "details");
    assert.deepEqual(compareOf(source), [["Tempo di salita", "22,4 s", "=", "22,4 s"], ["Tempo di discesa", "21,9 s", "=", "21,9 s"],
      ["Tempo lamelle", "4,4 s", "—", "—"], ["Corsa del telo", "198 cm", "=", "198 cm"]]);
    reset();
    // A profile without a reference travel: the travel row says «—»; a cover without a profile has no comparison.
    const loose = overview();
    loose.profiles[0].reference_travel_cm = null;
    const other = await mount({ overview: loose, language: "en" });
    const page_ = await details(other, "cover.kitchen_two", "details");
    assert.deepEqual(compareOf(page_).find(([label]) => label === "Curtain travel"), ["Curtain travel", "—", "—", "110 cm"]);
    assert.equal(page_.querySelector(".cal-page-values > .cal-section-title").textContent, "Profile values and this cover’s values");
    reset();
    const none = sampleOverview();
    const plain = await mount({ overview: none, language: "it" });
    assert.equal((await details(plain, "cover.living_one")).querySelector(".cal-compare"), null);
  });
});

describe("the section of the values", async () => {
  const { translations } = await import("../../custom_components/myhome/frontend/panel/panel-translations.js");
  const it = translations.it, en = translations.en;
  const original = globalThis.matchMedia;
  /** A window `width` px wide and 800 high for `matchMedia`; motion reduced. */
  const windowOf = (width) => (query) => ({ matches: query.split(",").some((part) => [...part.matchAll(/\(\s*([a-z-]+)\s*:\s*([a-z0-9-]+)\s*\)/g)].every(([, name, value]) =>
    name === "max-width" ? width <= parseFloat(value) : name === "min-width" ? width >= parseFloat(value) : name === "max-height" ? 800 <= parseFloat(value)
      : name === "min-height" ? 800 >= parseFloat(value) : name === "orientation" ? value === "landscape" : name === "prefers-reduced-motion")), addEventListener() {}, removeEventListener() {} });
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
  const reads = async (context, count = 2) => { for (let i = 0; i < count; i++) { context.panel._calibrationTab.refresh(); await tick(8); } };

  test("«Valori del profilo e di questa tapparella» is a section of «Dettagli», always open at every width: its title over its card, no toggle, no chevron, the table in sight; the 15 s reads change nothing", async () => {
    for (const rules of [null, windowOf(390), windowOf(1023), windowOf(1024), windowOf(1280)]) {
      globalThis.matchMedia = rules || original;
      const context = await mount({ language: "it", overview: family() });
      await open(context, "cover.kitchen_two");
      await pageTab(context.tab, "details");
      const box = card(context).querySelector(".cal-page-compare > .cal-compare");
      assert.equal(box.tagName, "DIV", "not a disclosure");
      assert.equal(box.querySelector("summary, details, button, ha-icon, [aria-expanded]"), null, "no header to click, no chevron");
      assert.equal(box.classList.contains("cal-disclosure"), false);
      assert.equal(card(context).querySelector(".cal-page-values > .cal-section-title").textContent, it.calTabCompare);
      assert.ok(box.querySelector(".cal-compare-table tbody tr"), "the table is there");
      assert.ok(box.querySelector(".cal-compare-legend"));
      const before = box.outerHTML;
      await reads(context);
      const after = card(context).querySelector(".cal-page-compare > .cal-compare");
      assert.equal(after, box, "the same node, morphed");
      assert.equal(after.outerHTML, before, "nothing changed by the reads");
      reset();
    }
  });
});

describe("«Dettagli» and its followers", async () => {
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
  async function click(context, selector) {
    const button = card(context).querySelector(selector);
    assert.ok(button, selector);
    button.click();
    await tick(6);
  }
  const plain = (value) => String(value ?? "").replace(/ /g, " ");
  const pairs = (scope) => [...scope.querySelectorAll(".cal-pairs > div")].map((line) => [line.querySelector("dt").textContent, plain(line.querySelector("dd").textContent).trim()]);

  test("«Dettagli»: the table with its legend and tail; «Ultima calibrazione» with «Come»; «Ultima verifica» (Data · Dove · Scarto, «Nessuna verifica» without one)", async () => {
    desktop();
    const context = await mount({ language: "it", overview: family() });
    await open(context, "cover.living_one");
    await pageTab(context.tab, "details");
    let page = card(context);
    const [left, right] = body(context).children;
    assert.ok(left.querySelector(".cal-page-values .cal-compare-table") && left.querySelector(".cal-compare-legend"), "the table on the left");
    assert.deepEqual([...right.querySelectorAll(":scope > section > .cal-section-title")].map((title) => title.textContent), ["Ultima calibrazione", "Ultima verifica", "Profilo usato anche da"]);
    assert.equal(pairs(page.querySelector(".cal-page-last"))[1][1], "Procedura guidata, con il metro");
    const check = pairs(page.querySelector(".cal-page-check"));
    assert.deepEqual(check.map(([label]) => label), ["Data", "Dove", "Scarto"]);
    assert.match(check[0][1], /^1 nov 2026 \d{2}:\d{2}$/);
    assert.deepEqual(check.slice(1), [["Dove", "a metà corsa"], ["Scarto", "2 cm"]]);
    card(context).querySelector(".cal-page-close").click(); await tick(8);
    await open(context, "cover.kitchen_two");
    await pageTab(context.tab, "details");
    page = card(context);
    assert.deepEqual(pairs(page.querySelector(".cal-page-last")), [["Come", "Collegata al profilo"]], "no date of its own");
    assert.deepEqual(pairs(page.querySelector(".cal-page-check")), [["Data", "Nessuna verifica"]]);
    reset();
    // A check elsewhere than half the travel: no «Dove».
    const other = await mount({ language: "it", overview: family({ half: false }) });
    await open(other, "cover.living_one");
    await pageTab(other.tab, "details");
    assert.deepEqual(pairs(card(other).querySelector(".cal-page-check")).map(([label]) => label), ["Data", "Scarto"]);
  });

  test("«Profilo usato anche da»: on the source too; the crown on the cover that measures the profile, the chain on those that follow it; nobody: «Nessun’altra tapparella lo segue»; a name opens that cover's details", async () => {
    phone();
    const context = await mount({ language: "it", overview: family() });
    await open(context, "cover.living_one");
    await pageTab(context.tab, "details");
    const users = () => [...card(context).querySelectorAll(".cal-page-users .cal-user")].map((user) => [user.querySelector(".cal-user-mark ha-icon").getAttribute("icon"),
      user.querySelector(".cal-user-mark").title, user.querySelector(".cal-user-link, .cal-user-name").textContent, user.querySelector(".cal-user-role").textContent]);
    assert.deepEqual(users(), [["mdi:link-variant", "Segue il profilo", "Kitchen shutter 2", "segue il profilo"]]);
    // The name: that cover's details in the same frame, on «Panoramica»; the history keeps one entry.
    const entry = window.history.state?.myhomeCalibration;
    await click(context, ".cal-page-users .cal-user-link");
    assert.equal(card(context).dataset.entity, "cover.kitchen_two");
    assert.equal(body(context).dataset.tab, "overview");
    assert.equal(window.history.state?.myhomeCalibration, entry, "no new entry");
    await pageTab(context.tab, "details");
    assert.deepEqual(users(), [["mdi:crown-outline", "Ha misurato il profilo", "Living room shutter 1", "ha misurato il profilo"]]);
    card(context).querySelector(".cal-page-close").click(); await tick(8);
    assert.equal(card(context), null, "the ✕: the list");
    reset();
    const alone = await mount({ language: "en", overview: family({ followers: false }) });
    await open(alone, "cover.living_one");
    await pageTab(alone.tab, "details");
    assert.equal(card(alone).querySelector(".cal-page-users .cal-page-users-none").textContent, "No other cover follows it");
    assert.equal(card(alone).querySelector(".cal-page-users > .cal-section-title").textContent, "Profile also used by");
  });
});

describe("tables and dates", async () => {
  const { translations } = await import("../../custom_components/myhome/frontend/panel/panel-translations.js");
  const text = await import("../../custom_components/myhome/frontend/panel/panel-calibration-text.js");
  const it = translations.it, en = translations.en;
  /** A window `width` px wide and 800 high for `matchMedia`, motion reduced. */
  const windowOf = (width) => (query) => ({ matches: query.split(",").some((part) => [...part.matchAll(/\(\s*([a-z-]+)\s*:\s*([a-z0-9-]+)\s*\)/g)].every(([, name, value]) =>
    name === "max-width" ? width <= parseFloat(value) : name === "min-width" ? width >= parseFloat(value) : name === "max-height" ? 800 <= parseFloat(value)
      : name === "min-height" ? 800 >= parseFloat(value) : name === "orientation" ? value === "landscape" : name === "prefers-reduced-motion")), addEventListener() {}, removeEventListener() {} });
  const desktop = () => { globalThis.matchMedia = windowOf(1280); };
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
  const pairs = (scope) => [...scope.querySelectorAll(".cal-pairs > div")].map((line) => [line.querySelector("dt").textContent, plain(line.querySelector("dd").textContent)]);
  // The mark in this cover's cell, before its value: read as two columns, as before.
  const table = (scope) => [...scope.querySelectorAll("tbody tr")].map((line) => [...line.children].flatMap((cell) => cell.querySelector(".cal-compare-mark")
    ? [plain(cell.querySelector('.cal-compare-mark [aria-hidden="true"]').textContent), plain(cell.querySelector(".cal-compare-value").textContent)]
    : [plain(cell.querySelector('[aria-hidden="true"]')?.textContent ?? cell.textContent)]));

  test("a cover calibrated without a profile: «Valori di questa tapparella» in the table's place, one column, the same rows and format, no legend nor marks", async () => {
    desktop();
    const context = await mount({ language: "it", overview: family({ alone: true }) });
    await open(context, "cover.kitchen_two", "details");
    const values = card(context).querySelector(".cal-page-values");
    assert.ok(values, "a section «values», as the profile's table");
    assert.equal(values.closest(".cal-page-column"), card(context).querySelector(".cal-page-body > .cal-page-column"), "on the left, where the profile's table is");
    assert.equal(values.querySelector(".cal-section-title").textContent, "Valori di questa tapparella");
    assert.deepEqual(table(values), [["Tempo di salita", "12,0 s"], ["Tempo di discesa", "11,5 s"], ["Tempo lamelle", "2,0 s"], ["Corsa del telo", "120 cm"],
      ["Coefficiente in salita", "1,0"], ["Coefficiente in discesa", "1,25"]]);
    assert.equal(values.querySelector("thead, .cal-compare-legend, .cal-compare-mark, .cal-card-tail"), null, "no header, legend, marks nor tail");
    assert.equal(card(context).querySelector(".cal-page-users"), null, "nobody else without a profile");
    assert.equal(en.calTabOwnValues, "This cover’s values");
    await close(context);
    // A cover never calibrated has no «Dettagli»; a cover with a profile keeps the comparison.
    await open(context, "cover.living_one", "details");
    assert.equal(card(context).querySelector(".cal-page-values .cal-section-title").textContent, it.calTabCompare);
  });

  test("the details' tables in the format of «Caratteristiche»: «22,0 s», «3,0 s», «1,0» in every column", async () => {
    assert.deepEqual([text.decimals(3, "it", 1), text.decimals(1, "it", 1, 2), text.decimals(1.05, "it", 1, 2), text.decimals(1.1, "en", 1, 2), text.decimals(null, "it")],
      ["3,0", "1,0", "1,05", "1.1", "—"]);
    phone();
    const context = await mount({ language: "it", overview: family() });
    const source = await open(context, "cover.living_one", "details");
    assert.deepEqual(table(source.querySelector(".cal-page-values")), [["Tempo di salita", "22,0 s", "=", "22,0 s"], ["Tempo di discesa", "21,6 s", "=", "21,6 s"],
      ["Tempo lamelle", "3,0 s", "=", "3,0 s"], ["Corsa del telo", "110 cm", "=", "110 cm"], ["Coefficiente in salita", "1,0", "=", "1,0"], ["Coefficiente in discesa", "1,05", "=", "1,05"]]);
    await close(context);
    const follower = await open(context, "cover.kitchen_two", "details");
    assert.deepEqual(table(follower.querySelector(".cal-page-values"))[0], ["Tempo di salita", "22,0 s", "→", "23,0 s"], "the follower's column too");
    await close(context);
    // «Caratteristiche» says the same.
    const overview = await open(context, "cover.living_one");
    assert.deepEqual([...overview.querySelectorAll(".cal-page-times .cal-measure-number")].map((number) => number.textContent), ["22,0", "21,6", "3,0"]);
  });

  test("«Profilo usato anche da» puts the crown and the chain in a tinted circle; the follower's tail has no chain before it", async () => {
    assert.match(css, /\.cal-user-mark \{ display: inline-flex; align-items: center; justify-content: center; flex: none; width: 32px; height: 32px; border-radius: 50%;\s*background: var\(--panel-accent-soft\); color: var\(--panel-accent\); --mdc-icon-size: 18px; \}/);
    phone();
    const context = await mount({ language: "it", overview: family() });
    const page = await open(context, "cover.kitchen_two", "details");
    assert.equal(page.querySelector(".cal-user-mark ha-icon").getAttribute("icon"), "mdi:crown-outline");
    const tail = page.querySelector(".cal-page-values .cal-card-tail");
    assert.equal(tail.textContent, it.calTabNoteFollower);
    assert.equal(tail.querySelector("ha-icon"), null, "no chain before the tail");
  });

  test("«Ultima calibrazione» is what was done on this cover: the source its own date (with the time), a follower «Come» only; the list says no «Calibrata il…» on a follower", async () => {
    phone();
    const context = await mount({ language: "it", overview: family() });
    const source = pairs((await open(context, "cover.living_one", "details")).querySelector(".cal-page-last"));
    assert.deepEqual(source.map(([label]) => label), ["Data", "Come"]);
    assert.equal(source[0][1], plain(text.dateTimeText(REFINED_AT, "it")), "the last thing done here (its Perfeziona)");
    assert.match(source[0][1], /^9 nov 2026 \d{2}:\d{2}$/, "the date with its time");
    await close(context);
    assert.deepEqual(pairs((await open(context, "cover.kitchen_two", "details")).querySelector(".cal-page-last")), [["Come", "Collegata al profilo"]]);
    await close(context);
    const line = (entity) => row(context, entity).querySelector(".cal-row-last-inline")?.textContent ?? null;
    assert.equal(line("cover.kitchen_two"), null, "a follower: its chip «Dal profilo» says enough");
    assert.equal(line("cover.living_one"), `Calibrata · ${text.dateText(REFINED_AT, "it")} ${text.timeText(REFINED_AT, "it")}`, "no article, the time after the date");
    assert.equal(line("cover.living_three"), it.wiz_T418, "never calibrated: as before");
  });
});

describe("one time for one calibration", async () => {
  const PANEL = "../../custom_components/myhome/frontend/panel/";
  const { statusBadge, adaptedFromProfile, sourceTimesDate, SAME_CALIBRATION_MS, ICON } = await import(`${PANEL}panel-calibration-tab.js`);
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
  async function close(context) { card(context).querySelector(".cal-page-close").click(); await tick(8); }
  const plain = (value) => String(value ?? "").replace(/[  ]/g, " ").trim();

  test("the source's «Tempi misurati su questa tapparella · …» and «Ultima calibrazione» say the same time for the same calibration", async () => {
    const one = (overview) => { const cover = overview.covers[0]; return { cover, profile: overview.profiles[0], last: [AT(0), AT(1), cover.effective.opening_roll.provenance.recorded_at].filter(Boolean).sort().at(-1) }; };
    assert.equal(SAME_CALIBRATION_MS, 30 * 60000, "a session's idle lease (IDLE_LEASE_SECONDS)");
    assert.equal(sourceTimesDate(one(family())), AT(1), "opening 18:39, closing and lift-off 18:40: one calibration, 18:40");
    assert.equal(sourceTimesDate(one(family({ refinedAfter: 5 }))), AT(6), "runs a few minutes apart: still one");
    assert.equal(sourceTimesDate(one(family({ refinedAfter: 60 * 24 }))), AT(0), "a Perfeziona the next day: the times keep their own date");
    phone();
    const context = await mount({ language: "it", overview: family() });
    const role = plain((await open(context, "cover.living_one")).querySelector(".cal-page-profile-head .cal-page-role").textContent);
    await close(context);
    const details = await open(context, "cover.living_one", "details");
    const date = plain([...details.querySelectorAll(".cal-page-last .cal-pairs > div")].find((line) => line.querySelector("dt").textContent === "Data").querySelector("dd").textContent);
    assert.equal(role, `Tempi misurati su questa tapparella · ${date}`);
    assert.equal(date, plain(text.dateTimeText(AT(1), "it")));
  });
});

describe("dating a profile's times", async () => {
  const PANEL = "../../custom_components/myhome/frontend/panel/";
  const { profileTimesDate, sourceTimesDate, ICON } = await import(`${PANEL}panel-calibration-tab.js`);
  const text = await import(`${PANEL}panel-calibration-text.js`);
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
  const sheet = (context) => context.tab().querySelector(".cal-flow .cal-assign-page");
  async function open(context, entity) {
    row(context, entity).querySelector('[data-action="cal-tab-toggle"]').click();
    await tick(6);
    return card(context);
  }
  async function close(context) { card(context).querySelector(".cal-page-close").click(); await tick(8); }

  test("one function dates a profile's times, read on its source: the same calibration one time; a later one, the profile's own", () => {
    const data = family(), profile = data.profiles[0], [one, two] = data.covers;
    assert.equal(profileTimesDate(profile, one), AT(1), "the opening 18:39, the closing and the lift-off 18:40: 18:40");
    assert.equal(sourceTimesDate({ profile, cover: one }), AT(1), "the source's page: the same function");
    assert.equal(profileTimesDate(profile, two), AT(0), "a follower is not the source: the profile's own date");
    assert.equal(profileTimesDate(profile, null), AT(0), "the source gone: the profile's own date");
    assert.equal(profileTimesDate(profile, { ...one, profile_id: "other" }), AT(0), "the source now follows another profile");
    const later = family({ refinedAfter: 60 * 24 });
    assert.equal(profileTimesDate(later.profiles[0], later.covers[0]), AT(0), "a Perfeziona the next day: the times keep their own date");
    assert.equal(profileTimesDate({ provenance: {} }, one), null);
  });

});

describe("the marks", async () => {
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

  test("the mark (=, →, ×) inside «Questa tapparella», left of the value, its title and hidden label; no column of its own; a hairline over the legend", async () => {
    phone();
    const context = await mount({ language: "it", overview: world() });
    await open(context, "cover.living_three");
    await pageTab(context.tab, "details");
    const box = card(context).querySelector(".cal-page-values .cal-compare");
    assert.deepEqual([...box.querySelectorAll("thead tr > *")].map((cell) => [cell.tagName, cell.textContent]), [["TD", ""], ["TH", "Profilo"], ["TH", "Questa tapparella"]]);
    for (const line of box.querySelectorAll("tbody tr")) {
      assert.deepEqual([...line.children].map((cell) => cell.className.split(" ")[0]), ["", "cal-compare-profile", "cal-compare-cover"], "three cells");
      const cover = line.querySelector(".cal-compare-cover");
      assert.deepEqual([...cover.children].map((child) => child.className), ["cal-compare-mark", "cal-compare-value"], "the mark, then the value");
      const mark = cover.querySelector(".cal-compare-mark");
      assert.equal(mark.title, mark.querySelector(".visually-hidden").textContent, "its title, said to a screen reader too");
      assert.ok(["=", "→", "×", "—"].includes(mark.querySelector('[aria-hidden="true"]').textContent));
    }
    assert.equal(box.querySelector("td.cal-compare-mark"), null, "no column of its own");
    assert.match(css, /\.cal-compare-table \.cal-compare-mark \{ display: inline-block; inline-size: 1\.5em; text-align: center; font-weight: 400; color: var\(--panel-text-2\); \}/, "a fixed width");
    assert.ok(box.querySelector(".cal-compare-table + .cal-compare-legend"), "the legend under the table");
    assert.match(css, /\.cal-page \.cal-compare-table \+ \.cal-compare-legend \{ margin: var\(--cal-sp-8\) calc\(-1 \* var\(--cal-sp-16\)\) 0; padding: var\(--cal-sp-12\) var\(--cal-sp-16\);\s+border-top: 1px solid var\(--panel-border\); \}/, "the card's hairline, across it");
  });

  test("«Nessun’altra tapparella lo segue» in italics, by the stylesheet (the text unchanged)", async () => {
    assert.equal(it.calTabUsersNone, "Nessun’altra tapparella lo segue");
    assert.match(css, /\.cal-page-users-none \{ font-style: italic; \}/);
    const data = world();
    data.profiles[0].assigned_to = ["cover.kitchen_two"];
    data.covers[2].profile_id = null;
    const context = await mount({ language: "it", overview: data });
    await open(context, "cover.kitchen_two");
    await pageTab(context.tab, "details");
    const none = card(context).querySelector(".cal-page-users-none");
    assert.equal(none.textContent, "Nessun’altra tapparella lo segue");
    assert.equal(none.querySelector("em, i"), null, "no markup for it");
  });
});

describe("the marks' cells", async () => {
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
  const check = (cover, gap) => Object.assign(cover, { precision: "verified", check_cm: gap, check_expected_cm: 55, check_measured_cm: 55 + gap, check_at: "2026-11-04T09:30:00+00:00" });
  const own = (number) => ({ value: number, origin: "override", provenance: { source: "guided", recorded_at: "2026-09-29T10:00:00+00:00", origin_entity_id: null, origin_name: null, inherited: false }, scaled: false });
  /**
   * As the world above: Kitchen shutter 2 measures «tall_two_leaf» (its source), Living room shutter 3 follows it, Living
   * room shutter 1 is never calibrated (or, `alone`, calibrated with times of its own and no profile).
   */
  function world({ source = "refined", sourceGap = null, alone = null, aloneGap = null, followerGap = null } = {}) {
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
    return data;
  }

  test("«Questa tapparella»: the mark flush at the cell's start, the value at its end; the legend one entry per line, each mark in a fixed box", async () => {
    phone();
    const context = await mount({ language: "it", overview: world() });
    await open(context, "cover.living_three");
    await pageTab(context.tab, "details");
    const box = card(context).querySelector(".cal-page-values .cal-compare");
    for (const cover of box.querySelectorAll("tbody .cal-compare-cover")) {
      assert.deepEqual([...cover.children].map((child) => child.className), ["cal-compare-mark", "cal-compare-value"]);
      const mark = cover.querySelector(".cal-compare-mark");
      assert.equal(mark.title, mark.querySelector(".visually-hidden").textContent, "its title and hidden label stay");
    }
    assert.match(css, /\.cal-compare-table \.cal-compare-mark \{ display: inline-block; inline-size: 1\.5em; text-align: center;/, "the mark's fixed box");
    assert.match(css, /\.cal-compare-table \.cal-compare-cover > \.cal-compare-mark \{ float: left; \}/, "flush left in its cell");
    assert.match(css, /\.cal-compare-table :is\(th, td\) \{ padding: var\(--cal-sp-4\) 0; text-align: end;/, "the value flush right");
    assert.match(css, /\.cal-compare-table:not\(\.is-own\) :is\(th, td\):last-child \{ padding-inline-start: var\(--cal-sp-12\); \}/);
    const legend = box.querySelector(".cal-compare-legend");
    assert.ok(legend.children.length >= 1);
    for (const entry of legend.children) assert.equal(entry.firstElementChild.tagName, "B", "the mark first");
    assert.match(css, /\.cal-compare-legend \{ flex-direction: column; flex-wrap: nowrap; \}/, "one per line");
    assert.match(css, /\.cal-compare-legend > span \{ display: flex; align-items: baseline; \}/);
    assert.match(css, /\.cal-compare-legend b \{ flex: none; inline-size: 1\.5em; text-align: center; \}/, "the table's box");
  });
});
