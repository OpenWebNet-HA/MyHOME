/**
 * How the wizard starts: A-01, A-02 and its advice, the process it names, the slats question asked once.
 */
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { after, afterEach, describe, test } from "node:test";

import { sampleOverview } from "./fake-calibration.mjs";
import { panelCss as css } from "./panel-css.mjs";
import { dom, mount, pageTab, refineCover, reset, tick } from "./wizard-harness.mjs";

const original = globalThis.matchMedia;
afterEach(() => { globalThis.matchMedia = original; reset(); });
after(() => dom.window.close());

describe("Ricalibra on a fine-tuned cover", async () => {
  const starts = (backend) => backend.subscriptions.filter((message) => message.type === "myhome/cover_calibration/start");
  const rowOf = (tab, id) => tab().querySelector(`.cal-row[data-entity="${id}"]`);

  test("«Ricalibra» on a fine-tuned cover goes straight to C-35, the slats question preselected from the saved slat time, and runs the lift-off too", async () => {
    const { tab, wiz, code, press, backend } = await mount();
    backend.hass.states["cover.kitchen_two"] = { state: "closed", attributes: { current_position: 0 } };
    rowOf(tab, "cover.kitchen_two").querySelector('[data-action="cal-tab-measure-cover"]').click(); await tick();
    assert.equal(code(), "C-35", "no A-02: the action decided the path");
    assert.equal(wiz().querySelector('input[name="slats"]:checked').value, "yes", "a slat time is saved: «with slats»");
    await press("begin");
    assert.equal(starts(backend).at(-1).slats, true);
  });
});

describe("A-01 and A-02", async () => {
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
  const headLine = (wiz) => wiz().querySelector(".wiz-head-phase").textContent;
  const stepLabels = (wiz) => [...wiz().querySelectorAll(".wiz-steps > ol > li .wiz-step-label")].map((item) => item.textContent);
  const barIds = (wiz) => [...wiz().querySelectorAll(".wiz-actions .wiz-action")].map((button) => button.dataset.wiz);
  const starts = (backend) => backend.subscriptions.filter((message) => message.type === "myhome/cover_calibration/start");

  test("A-02 is offered only on a cover already calibrated: «Ricalibra» and «Perfezionamento con il metro», no icons, their texts; path B hidden, nothing «non disponibile»", async () => {
    const context = await mount({ language: "it" });
    const { tab, wiz, code, press, pickCover, backend } = context;
    tab().querySelector('[data-action="cal-tab-measure"]').click(); await tick();
    assert.deepEqual(barIds(wiz), ["choose", "cancel-flow"], "23: A-01, the first screen, has «Annulla»");
    await pickCover("cover.kitchen_two");
    await press("choose");
    assert.equal(code(), "A-02");
    const options = [...wiz().querySelectorAll(".wiz-radio")].map((label) => [label.querySelector("input").value, label.querySelector(".wiz-radio-icon")?.getAttribute("icon") ?? null,
      label.querySelector(".wiz-radio-label").textContent.replace("Consigliato", "").trim(), label.querySelector("small").textContent]);
    // No icons beside the options (they stay on the action buttons).
    assert.deepEqual(options, [["base", null, "Ricalibra", it.wiz_T665], ["R", null, "Perfezionamento con il metro", it.wiz_T666]]);
    assert.equal(it.wiz_T666, "Prevede 3–4 letture col metro e una verifica finale: il risultato è una posizione verificata, con lo scarto misurato su questa tapparella.");
    assert.doesNotMatch(it.wiz_T665 + it.wiz_T666, /lamelle/, "no «lamelle» in the descriptions (said later)");
    // Kitchen shutter 2 follows «tall_two_leaf»: the paragraph names the profile whose times it uses.
    assert.equal(wiz().querySelector(".wiz-body").textContent, it.wiz_A02_follower);
    assert.doesNotMatch(wiz().textContent, /non disponibile|prossima versione|Come una tapparella già misurata/);
    assert.deepEqual(barIds(wiz), ["path", "back"], "from A-01, «Indietro»");
    const r = wiz().querySelector('input[name="path"][value="R"]');
    r.checked = true; r.dispatchEvent(new Event("change", { bubbles: true })); await tick();
    assert.deepEqual(stepLabels(wiz), ["Scelta del percorso", "Preparazione", "Letture col metro", "Verifica", "Riepilogo"]);
    await press("path");
    assert.deepEqual([code(), headLine(wiz)], ["A-03", "Perfeziona · Preparazione · 2 di 5"]);
    backend.hass.states["cover.kitchen_two"] = { state: "closed", attributes: { current_position: 0 } };
    await press("begin");
    assert.deepEqual([starts(backend).at(-1).mode, starts(backend).at(-1).keep_times], ["geometry", true], "the fine-tuning on the saved times");
  });

  test("a cover never calibrated goes from A-01 straight to Preparazione (no A-02, no «Scelta del percorso»); a cover's own Calibra / Ricalibra / Perfeziona open on their first step with «Annulla», which leads back where they came from", async () => {
    const context = await mount({ language: "it" });
    const { tab, wiz, code, press, pickCover } = context;
    tab().querySelector('[data-action="cal-tab-measure"]').click(); await tick();
    await pickCover("cover.living_one");
    await press("choose");
    assert.equal(code(), "C-35");
    assert.equal(stepLabels(wiz)[0], "Preparazione", "no «Scelta del percorso»");
    assert.equal(headLine(wiz), "Calibra · Preparazione · 1 di 5");
    assert.deepEqual(barIds(wiz), ["begin", "back"], "not the first screen: «Indietro» to A-01");
    await press("back");
    assert.equal(code(), "A-01");
    wiz().querySelector(".wiz-head .wiz-x").click(); await tick();
    // From the list's row: Ricalibra on its first step, «Annulla» back to the list.
    row(tab, "cover.kitchen_two").querySelector('.cal-menu [data-action="cal-tab-measure-cover"]').click(); await tick();
    assert.deepEqual([code(), barIds(wiz)], ["C-35", ["begin", "cancel-flow"]]);
    assert.equal(headLine(wiz), "Ricalibra · Preparazione · 1 di 5");
    await press("cancel-flow"); await tick(8);
    assert.equal(wiz(), null);
    assert.ok(tab().querySelector(".cal-list-rows") && !tab().querySelector(".cal-flow .cal-page"), "back to the list");
    // From the details: Perfeziona, then «Annulla»: the details again, in the same frame (on a cover without a profile; a
    // follower has none yet).
    context.backend.overview.covers.find((cover) => cover.entity_id === "cover.living_three").profile_id = null;
    context.backend.overview.profiles[0].assigned_to = ["cover.kitchen_two"];
    context.backend.publishOverview(); await tick(8);
    const page = await details(context, "cover.living_three");
    const frame = page.closest("dialog");
    page.querySelector('[data-action="cal-tab-refine"]').click(); await tick();
    assert.deepEqual([code(), headLine(wiz), barIds(wiz)], ["A-03", "Perfeziona · Preparazione · 1 di 4", ["begin", "cancel-flow"]]);
    await press("cancel-flow"); await tick(8);
    const again = tab().querySelector(".cal-flow .cal-page");
    assert.ok(again && again.dataset.entity === "cover.living_three", "the details of the same cover");
    assert.equal(again.closest("dialog"), frame, "in the same frame");
  });

  test("the slats question is preselected from what is saved (a slat time: with; none: without), on Perfeziona and Ricalibra; a cover never calibrated keeps «with slats»", async () => {
    const context = await mount({ language: "it" });
    const { tab, wiz, code } = context;
    const answer = () => wiz().querySelector('input[name="slats"]:checked').value;
    await refineCover(context, "cover.living_three");
    assert.deepEqual([code(), answer()], ["A-03", "no"], "a basic estimate without a slat time: without slats");
    wiz().querySelector(".wiz-head .wiz-x").click(); await tick();
    row(tab, "cover.living_three").querySelector('.cal-menu [data-action="cal-tab-measure-cover"]').click(); await tick();
    assert.deepEqual([code(), answer()], ["C-35", "no"], "Ricalibra too");
    wiz().querySelector(".wiz-head .wiz-x").click(); await tick();
    await refineCover(context, "cover.kitchen_two");
    assert.equal(answer(), "yes", "a slat time saved: with slats");
    wiz().querySelector(".wiz-head .wiz-x").click(); await tick();
    // With profiles in the catalogue the row's Calibra opens the card: «Misura i tempi» there.
    row(tab, "cover.living_one").querySelector('[data-action="cal-tab-calibrate"]').click(); await tick();
    tab().querySelector('.cal-page .cal-pos-how [data-action="cal-tab-measure-cover"]').click(); await tick();
    assert.equal(answer(), "yes", "never calibrated: the default");
  });

  test("a slats answer known is not asked again; the session starts with it", async () => {
    const data = sourceOverview();
    data.covers[1].slats_state = "no_profile";
    const context = await mount({ overview: data, language: "it" });
    await refineCover(context, "cover.kitchen_two");
    assert.equal(context.wiz().querySelector('input[name="slats"]'), null, "its profile says «No»: not asked");
    await context.press("begin");
    assert.equal(context.backend.subscriptions.filter((message) => message.type === "myhome/cover_calibration/start").at(-1).slats, false);
  });
});

describe("the slats question", async () => {
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

  test("the slats question only when unknown; Ricalibra on a follower asks it preset with the profile's answer; never a step more", async () => {
    const { slatsQuestion } = await import("../../custom_components/myhome/frontend/panel/panel-calibration-screens.js");
    const cover = (slats_state) => ({ slats_state, status: "calibrated", effective: {} });
    assert.deepEqual(slatsQuestion(cover("yes_measured"), "A"), { ask: false, value: true });
    assert.deepEqual(slatsQuestion(cover("no_measured"), "base"), { ask: false, value: false });
    assert.deepEqual(slatsQuestion(cover("no"), "base"), { ask: false, value: false }, "declared on its card (no profile)");
    assert.deepEqual(slatsQuestion(cover("yes_profile"), "A"), { ask: false, value: true }, "Perfeziona on a follower: its profile's");
    assert.deepEqual(slatsQuestion(cover("no_profile"), "base"), { ask: true, value: false }, "Ricalibra on a follower: asked, preset");
    assert.equal(slatsQuestion(cover("unknown"), "base").ask, true);
    assert.deepEqual(slatsQuestion(cover("unknown"), "A", { slatsKnown: false }), { ask: false, value: false }, "the base just saved in this frame");
    // On screen: a follower's Ricalibra shows the question preset; a declared cover goes on without it, its steps as they were.
    const data = sourceData();
    data.covers[1].slats_state = "no_profile";
    Object.assign(data.covers[2], { profile_id: null, slats_state: "no" });
    const context = await mount({ overview: data, language: "it" });
    context.backend.hass.states["cover.kitchen_two"] = { state: "closed", attributes: { current_position: 0 } };
    await context.startFor("cover.kitchen_two", "base");
    assert.equal(context.wiz().querySelector('input[name="slats"]:checked').value, "no");
    reset();
    const other = await mount({ overview: data, language: "it" });
    other.backend.hass.states["cover.living_three"] = { state: "closed", attributes: { current_position: 0 } };
    await other.startFor("cover.living_three", "base");
    assert.equal(other.wiz().querySelector('input[name="slats"]'), null);
    assert.equal(other.wiz().querySelector(".wiz-head-phase")?.textContent, "Ricalibra · Preparazione · 2 di 5",
      "the path's choice, preparation, ascent, descent, summary: no «Lamelle» and no step for the question");
  });

  test("a cover calibrated on its own times only, its slats never said: the question is preselected «Sì», no «No» deduced", async () => {
    const { savedSlats, slatsQuestion } = await import("../../custom_components/myhome/frontend/panel/panel-calibration-screens.js");
    // As such a cover is stored: no profile, two overrides measured, a travel, no `slats` mark.
    const ownTimes = { status: "calibrated", slats_state: "unknown",
      effective: { opening: { value: 15.49, origin: "override" }, closing: { value: 14.75, origin: "override" } } };
    assert.equal(savedSlats(ownTimes), null);
    assert.deepEqual(slatsQuestion(ownTimes, "base"), { ask: true, value: null });
    const data = sampleOverview();
    const cover = data.covers.find((item) => item.entity_id === "cover.living_three");
    Object.assign(cover, { profile_id: null, slats_state: "unknown", status: "calibrated", precision: "basic" });
    delete cover.effective.slat_time_s;
    data.profiles[0].assigned_to = data.profiles[0].assigned_to.filter((id) => id !== "cover.living_three");
    const context = await mount({ overview: data, language: "it" });
    context.backend.hass.states["cover.living_three"] = { state: "closed", attributes: { current_position: 0 } };
    await context.startFor("cover.living_three", "base");
    assert.equal(context.wiz().querySelector('input[name="slats"]:checked').value, "yes");
  });
});

describe("A-02's advice", async () => {
  const PANEL = "../../custom_components/myhome/frontend/panel/";
  const { translations } = await import(`${PANEL}panel-translations.js`);
  const text = await import(`${PANEL}panel-calibration-text.js`);
  const { pathAdvice } = await import(`${PANEL}panel-calibration-screens.js`);
  const it = translations.it, en = translations.en;
  const tabSource = readFileSync(new URL(`${PANEL}panel-calibration-tab.js`, import.meta.url), "utf8");
  const screensSource = readFileSync(new URL(`${PANEL}panel-calibration-screens.js`, import.meta.url), "utf8");
  const row = (context, entity) => context.tab().querySelector(`.cal-row[data-entity="${entity}"]`);
  const plain = (value) => String(value ?? "").replace(/[  ]/g, " ").trim();
  const shownAction = (context, entity) => [...row(context, entity).querySelectorAll(".cal-row-action")].map((button) => button.dataset.action);
  const check = (cover, gap) => Object.assign(cover, { precision: "verified", check_cm: gap, check_expected_cm: 55, check_measured_cm: 55 + gap, check_at: "2026-11-04T09:30:00+00:00" });
  const own = (number) => ({ value: number, origin: "override", provenance: { source: "guided", recorded_at: "2026-09-29T10:00:00+00:00", origin_entity_id: null, origin_name: null, inherited: false }, scaled: false });
  /**
   * Three covers: Kitchen shutter 2 measures «tall_two_leaf» (its source), Living room shutter 3 follows it, Living
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
  /** A-02 for `entity`, from «Calibra una tapparella». */
  async function pathScreen(context, entity) {
    context.tab().querySelector('[data-action="cal-tab-measure"]').click(); await tick();
    await context.pickCover(entity);
    await context.press("choose");
    assert.equal(context.code(), "A-02", entity);
  }
  const advised = (context) => [...context.wiz().querySelectorAll(".wiz-radio:has(.wiz-recommended) input")].map((input) => input.value);
  const preselected = (context) => context.wiz().querySelector('input[name="path"]:checked')?.value ?? "";

  test("A-02 on the profile's source says the profile was measured on this cover; a follower still «Segue il profilo»", async () => {
    assert.equal(it.wiz_A02_source, "Il profilo «{profile}» è stato misurato su questa tapparella.");
    assert.equal(en.wiz_A02_source, "The profile \"{profile}\" was measured on this cover.");
    const context = await mount({ language: "it", overview: world() });
    await pathScreen(context, "cover.kitchen_two");
    assert.equal(plain(context.wiz().querySelector(".wiz-subline").textContent), "Il profilo «tall_two_leaf» è stato misurato su questa tapparella.");
    assert.doesNotMatch(context.wiz().querySelector(".wiz-subline").textContent, /Segue il profilo/);
    assert.ok(context.wiz().textContent.includes("Kitchen shutter 2"), "the cover's name is on the screen: «questa tapparella»");
    reset();
    const follower = await mount({ language: "it", overview: world() });
    await pathScreen(follower, "cover.living_three");
    assert.match(plain(follower.wiz().querySelector(".wiz-subline").textContent), /^Segue il profilo «tall_two_leaf»/, "a follower as before");
  });

  test("one rule (advisedStep) for the list's action and A-02's advice: Perfeziona ⇄ R, Ricalibra and a follower ⇄ base", async () => {
    // [world, entity, list's action in view, A-02's advice and preselection]
    const cases = [
      [{ source: "basic" }, "cover.kitchen_two", ["cal-tab-refine"], "R"],
      [{ source: "refined" }, "cover.kitchen_two", ["cal-tab-refine"], "R"],
      [{ sourceGap: 6 }, "cover.kitchen_two", ["cal-tab-refine"], "R"],
      [{ sourceGap: 1 }, "cover.kitchen_two", [], "R"],
      [{ alone: "basic" }, "cover.living_one", ["cal-tab-refine"], "R"],
      [{ alone: "basic", aloneGap: 2 }, "cover.living_one", [], "R"],
      [{}, "cover.living_three", [], "base"],
      [{ followerGap: 6 }, "cover.living_three", ["cal-tab-measure-cover"], "base"],
    ];
    for (const [options, entity, shown, path] of cases) {
      const context = await mount({ language: "it", overview: world(options) });
      const label = `${JSON.stringify(options)} ${entity}`;
      assert.deepEqual(shownAction(context, entity), shown, `${label}: the list`);
      await pathScreen(context, entity);
      assert.deepEqual(advised(context), [path], `${label}: A-02 advises one option`);
      assert.equal(preselected(context), path, `${label}: and preselects it`);
      reset();
    }
    // The rule itself, and both callers on it.
    assert.deepEqual(text.advisedStep({ status: "to_measure", role: "none" }), { shown: "calibrate", path: "base" });
    assert.deepEqual(text.advisedStep({ status: "verified", role: "source", check: 4 }), { shown: null, path: "R" });
    assert.deepEqual(text.advisedStep({ status: "verified", role: "follower", check: 4.1 }), { shown: "remeasure", path: "base" });
    assert.equal(pathAdvice({ status: "calibrated", role: "alone" }).badge, "R");
    assert.equal(pathAdvice({ status: "profile", role: "follower" }).badge, "base");
    assert.match(tabSource, /text\.advisedStep\(/, "_rowActions on the rule");
    assert.match(screensSource, /advisedStep\(cover\)/, "pathAdvice on the rule");
  });

  test("A-02's R is the same procedure as Perfeziona from the list (path A on the times saved, `start: \"refine\"`)", async () => {
    const fromList = await mount({ language: "it", overview: world({ source: "basic" }) });
    row(fromList, "cover.kitchen_two").querySelector('[data-action="cal-tab-refine"]').click(); await tick(6);
    const listed = fromList.panel._calibrationTab._wizard._local;
    const refine = { path: listed.path, keepTimes: listed.keepTimes, refineEntry: listed.refineEntry, stage: listed.stage };
    reset();
    const fromPath = await mount({ language: "it", overview: world({ source: "basic" }) });
    await pathScreen(fromPath, "cover.kitchen_two");
    await fromPath.press("path");
    const chosen = fromPath.panel._calibrationTab._wizard._local;
    assert.deepEqual({ path: chosen.path, keepTimes: chosen.keepTimes, refineEntry: chosen.refineEntry, stage: chosen.stage }, refine);
    assert.deepEqual(refine, { path: "A", keepTimes: true, refineEntry: true, stage: "before" });
  });

  test("A-01's profile chips are never cut: no 180 px cap, the name wraps inside the chip; the status chip has nothing to cut", async () => {
    const context = await mount({ language: "it", overview: world() });
    context.tab().querySelector('[data-action="cal-tab-measure"]').click(); await tick();
    assert.equal(context.code(), "A-01");
    const chip = context.wiz().querySelector(".wiz-picker .cal-batch-profile .cal-profile-chip .cal-chip-text");
    assert.ok(chip, "a profile chip in A-01");
    assert.match(css, /\.wiz-picker \.cal-batch-profile \.cal-profile-chip \{ max-width: 100%; \}/);
    assert.match(css, /\.wiz-picker \.cal-batch-profile \.cal-chip-text \{ white-space: normal; overflow-wrap: anywhere; \}/);
    const badge = context.wiz().querySelector(".wiz-picker .cal-batch-status .cal-badge");
    assert.equal(badge.querySelector(".cal-chip-text"), null, "the status chip's label has no box that cuts it");
  });
});

describe("the process A-02 names", async () => {
  const PANEL = "../../custom_components/myhome/frontend/panel/";
  const { translations } = await import(`${PANEL}panel-translations.js`);
  const it = translations.it, en = translations.en;
  const plain = (value) => String(value ?? "").replace(/[  ]/g, " ").trim();
  const world = () => {
    const data = sampleOverview();
    data.profiles[0].provenance.opening.origin_entity_id = "cover.kitchen_two";
    return data;
  };
  async function pathScreen(context, entity) {
    context.tab().querySelector('[data-action="cal-tab-measure"]').click(); await tick();
    await context.pickCover(entity);
    await context.press("choose");
    assert.equal(context.code(), "A-02", entity);
  }
  const tickPath = async (context, value) => {
    const radio = context.wiz().querySelector(`input[name="path"][value="${value}"]`);
    radio.checked = true; radio.dispatchEvent(new dom.window.Event("change", { bubbles: true })); await tick();
  };
  const process = (context) => [plain(context.wiz().querySelector(".wiz-head-phase").textContent).split(" · ")[0], plain(context.wiz().querySelector(".wiz-steps-title").textContent)];

  test("A-02 with «Perfezionamento con il metro» preselected or chosen: the bar and the steps' column say «Perfeziona», as the list's button; «Ricalibra» chosen, «Ricalibra»", async () => {
    // The profile's source: fine-tuning advised and preselected.
    const context = await mount({ language: "it", overview: world() });
    await pathScreen(context, "cover.kitchen_two");
    assert.equal(context.wiz().querySelector('input[name="path"]:checked').value, "R");
    assert.deepEqual(process(context), [it.calTabActionRefine, it.calTabActionRefine], "preselected: Perfeziona");
    assert.equal(it.calTabActionRefine, "Perfeziona");
    await tickPath(context, "base");
    assert.deepEqual(process(context), [it.calTabActionRemeasure, it.calTabActionRemeasure], "Ricalibra chosen: Ricalibra");
    await tickPath(context, "R");
    assert.deepEqual(process(context), ["Perfeziona", "Perfeziona"], "chosen again: Perfeziona");
    reset();
    // A follower: the basic calibration preselected, «Ricalibra»; the fine-tuning ticked, «Perfeziona».
    const follower = await mount({ language: "en", overview: world() });
    await pathScreen(follower, "cover.living_three");
    assert.deepEqual(process(follower), [en.calTabActionRemeasure, en.calTabActionRemeasure]);
    await tickPath(follower, "R");
    assert.deepEqual(process(follower), [en.calTabActionRefine, en.calTabActionRefine]);
  });

  test("back on A-02 after choosing the fine-tuning, «Ricalibra» ticked says «Ricalibra» (the option decides, not the earlier entry)", async () => {
    const context = await mount({ language: "it", overview: world() });
    await pathScreen(context, "cover.kitchen_two");
    await context.press("path");
    assert.notEqual(context.code(), "A-02");
    await context.press("back");
    assert.equal(context.code(), "A-02");
    assert.deepEqual(process(context), ["Perfeziona", "Perfeziona"]);
    await tickPath(context, "base");
    assert.deepEqual(process(context), ["Ricalibra", "Ricalibra"]);
  });

  test("A-02 on a cover that follows a profile says it starts from the profile's times (the box above names the profile); the source and a cover without a profile keep T667", async () => {
    assert.equal(it.wiz_A02_follower, "Puoi rifare la calibrazione da zero oppure affinarne la precisione col metro, partendo dai tempi del profilo.");
    assert.equal(en.wiz_A02_follower, "You can calibrate it again from scratch, or refine its precision with a tape measure, starting from the profile's times.");
    const body = (context) => plain(context.wiz().querySelector(".wiz-body").textContent);
    const follower = await mount({ language: "it", overview: world() });
    await pathScreen(follower, "cover.living_three");
    assert.equal(body(follower), "Puoi rifare la calibrazione da zero oppure affinarne la precisione col metro, partendo dai tempi del profilo.");
    reset();
    const source = await mount({ language: "it", overview: world() });
    await pathScreen(source, "cover.kitchen_two");
    assert.equal(body(source), it.wiz_T667, "the source: its own times");
    reset();
    const data = world();
    const own = (number) => ({ value: number, origin: "override", provenance: { source: "guided", recorded_at: "2026-09-29T10:00:00+00:00", origin_entity_id: null, origin_name: null, inherited: false }, scaled: false });
    Object.assign(data.covers[0], { overrides: { opening: 18, closing: 17.2 }, precision: "basic", effective: { opening: own(18), closing: own(17.2) } });
    const alone = await mount({ language: "en", overview: data });
    await pathScreen(alone, "cover.living_one");
    assert.equal(body(alone), en.wiz_T667, "a cover without a profile");
  });
});

describe("the process A-01 names once a cover is chosen", async () => {
  const { translations } = await import("../../custom_components/myhome/frontend/panel/panel-translations.js");
  const it = translations.it;
  const plain = (value) => String(value ?? "").replace(/[  ]/g, " ").trim();
  /** Kitchen shutter 2 is the source of «tall_two_leaf», Living room shutter 3 follows it, Living room shutter 1 was never calibrated. */
  const world = () => {
    const data = sampleOverview();
    data.profiles[0].provenance.opening.origin_entity_id = "cover.kitchen_two";
    return data;
  };
  // The steps' column and the phone's «Passi» sheet are headed by the process; A-01's bar has no step line yet.
  const process = (context) => [plain(context.wiz().querySelector(".wiz-steps .wiz-steps-title").textContent),
    plain(context.wiz().querySelector(".wiz-sheet-steps .wiz-steps-title").textContent)];
  const tickPath = async (context, value) => {
    const radio = context.wiz().querySelector(`input[name="path"][value="${value}"]`);
    radio.checked = true; radio.dispatchEvent(new dom.window.Event("change", { bubbles: true })); await tick();
  };
  /** A-01, the cover, A-02 with the fine-tuning ticked, on to its first step, then «Indietro» twice: A-01 again. */
  async function fineTuneAndBack(context, entity) {
    context.tab().querySelector('[data-action="cal-tab-measure"]').click(); await tick();
    await context.pickCover(entity);
    await context.press("choose");
    assert.equal(context.code(), "A-02");
    await tickPath(context, "R");
    await context.press("path");
    assert.equal(context.code(), "A-03");
    await context.press("back");
    assert.equal(context.code(), "A-02");
    await context.press("back");
    assert.equal(context.code(), "A-01");
  }

  test("a profile's source: the fine-tuning chosen, then back to the cover's choice, the steps still say «Perfeziona» (the list advises it)", async () => {
    const context = await mount({ language: "it", overview: world() });
    await fineTuneAndBack(context, "cover.kitchen_two");
    assert.deepEqual(process(context), [it.calTabActionRefine, it.calTabActionRefine]);
    assert.equal(plain(context.wiz().querySelector(".wiz-head-phase").textContent), "", "no step line on the cover's choice");
  });

  test("a cover that follows a profile: the fine-tuning chosen, then back to the cover's choice, the steps say «Ricalibra», not «Perfeziona»", async () => {
    const context = await mount({ language: "it", overview: world() });
    await fineTuneAndBack(context, "cover.living_three");
    assert.deepEqual(process(context), [it.calTabActionRemeasure, it.calTabActionRemeasure]);
    assert.equal(it.calTabActionRemeasure, "Ricalibra");
  });

  test("a cover never calibrated, chosen on the cover's choice, says «Calibra»; with no cover chosen yet the steps say what they said at opening", async () => {
    const context = await mount({ language: "it", overview: world() });
    context.tab().querySelector('[data-action="cal-tab-measure"]').click(); await tick();
    assert.equal(context.code(), "A-01");
    assert.deepEqual(process(context), [it.calTabActionMeasure, it.calTabActionMeasure], "nothing chosen yet");
    await context.pickCover("cover.living_one");
    assert.deepEqual(process(context), [it.calTabActionMeasure, it.calTabActionMeasure]);
    assert.equal(it.calTabActionMeasure, "Calibra");
  });

  test("another cover picked on the cover's choice: the process follows it, and the choice of the path then preselects the new cover's advice", async () => {
    const context = await mount({ language: "it", overview: world() });
    await fineTuneAndBack(context, "cover.kitchen_two");
    assert.deepEqual(process(context), ["Perfeziona", "Perfeziona"]);
    await context.pickCover("cover.living_three");
    assert.deepEqual(process(context), ["Ricalibra", "Ricalibra"]);
    await context.pickCover("cover.living_one");
    assert.deepEqual(process(context), ["Calibra", "Calibra"]);
    await context.pickCover("cover.kitchen_two");
    assert.deepEqual(process(context), ["Perfeziona", "Perfeziona"]);
    // On to the choice of the path with the follower: its advice preselected and named, as on a first visit.
    await context.pickCover("cover.living_three");
    await context.press("choose");
    assert.equal(context.code(), "A-02");
    assert.equal(context.wiz().querySelector('input[name="path"]:checked').value, "base");
    assert.deepEqual(process(context), ["Ricalibra", "Ricalibra"]);
    await context.press("path");
    assert.equal(plain(context.wiz().querySelector(".wiz-head-phase").textContent).split(" · ")[0], "Ricalibra");
    const local = context.panel._calibrationTab._wizard._local;
    assert.deepEqual([local.keepTimes, local.refineEntry], [false, false], "the basic calibration, not the fine-tuning");
  });

  test("the list's «Perfeziona» opens on the fine-tuning's first step, never on the cover's choice, and still says «Perfeziona»", async () => {
    const context = await mount({ language: "it", overview: world() });
    await refineCover(context, "cover.living_three");
    assert.equal(context.code(), "A-03");
    assert.deepEqual(process(context), ["Perfeziona", "Perfeziona"]);
    assert.equal(plain(context.wiz().querySelector(".wiz-head-phase").textContent).split(" · ")[0], "Perfeziona");
  });
});
