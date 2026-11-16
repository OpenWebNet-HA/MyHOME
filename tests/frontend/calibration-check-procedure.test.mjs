/**
 * «Qualcosa non va?»: the question, its three answers and their outcomes, on the phone and from 1024 px.
 */
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { after, afterEach, describe, test } from "node:test";

import { block, cascadeCss } from "./css-cascade.mjs";
import { sampleOverview } from "./fake-calibration.mjs";
import { dom, mount, move, pageTab, reset, tick } from "./wizard-harness.mjs";

const original = globalThis.matchMedia;
afterEach(() => { globalThis.matchMedia = original; reset(); });
after(() => dom.window.close());

describe("the question and its outcomes", async () => {
  const { translations } = await import("../../custom_components/myhome/frontend/panel/panel-translations.js");
  const { checkContext, answersOf, outcomeOf, checkAtHalf } = await import("../../custom_components/myhome/frontend/panel/panel-calibration-tab.js");
  const { footer } = await import("../../custom_components/myhome/frontend/panel/panel-calibration-wizard.js");
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
  const state = (page) => [page.querySelector(".cal-pos-state-title")?.textContent ?? null, page.querySelector(".cal-pos-state-desc")?.textContent ?? null];
  const ctas = (page) => [...page.querySelectorAll(".cal-cta")].map((cta) => [cta.querySelector("button").dataset.action, cta.querySelector("button").textContent.trim(), cta.querySelector(".cal-caption").textContent]);
  const choices = (page) => [...page.querySelectorAll(".cal-check .cal-rows > .cal-choice")].map((choice) => choice.dataset.id);
  const plain = (value) => String(value).replace(/ /g, " ");
  const head = (page) => page.querySelector(".cal-page-head .muted").textContent;
  /** Ricalibra from the card to A-34: the basic calibration's runs (the lift-off where it has slats), then Save. */
  async function recalibrated(context, entity, answer) {
    await open(context, entity);
    await click(context, "cal-tab-check-start");
    if (answer) await click(context, "cal-tab-check-answer", answer);
    await click(context, "cal-tab-measure-cover");
    const { backend, press, code, button } = context;
    backend.hass.states[entity] = { state: "closed", attributes: { current_position: 0 } };
    assert.equal(code(), "C-35", "the basic calibration's first step, as Calibra");
    await press("begin");
    await press("run:open"); await move(backend, { seconds: 14.2 }); await press("endpoint");
    await press("ack:ascent"); await press("run:close"); await move(backend, { seconds: 14 }); await press("endpoint"); await press("ack:descent");
    if (button("lift")) { await press("run:open"); await move(backend, { seconds: 2.7 }); await press("lift"); await press("ack:lift"); }
    await press("save"); await tick(8);
    assert.equal(code(), "A-34");
    return context.panel._calibrationTab._wizard;
  }

  test("one question «Cosa vuoi sistemare?», three answers with their lines and the actuator's note under them; no «Non ne sono sicuro», no hint; ‹ back to «Posizionamento»", async () => {
    const context = await mount({ language: "it", overview: family() });
    await open(context, "cover.living_one");
    await click(context, "cal-tab-check-start");
    let page = card(context);
    assert.equal(page.querySelector(".cal-page-tabs"), null, "the tabs hidden");
    assert.equal(head(page), "Qualcosa non va?", "the procedure's name, no counter (the fold's title)");
    assert.equal(page.querySelector(".cal-page-head .cal-page-back").getAttribute("aria-label"), "Indietro");
    assert.equal(context.root.activeElement, page.querySelector(".cal-page-back"));
    assert.equal(page.querySelector(".cal-step-title").textContent, "Cosa vuoi sistemare?");
    assert.equal(page.querySelector(".cal-step-lead"), null);
    assert.deepEqual(choices(page), ["full", "middle", "unsure"]);
    assert.deepEqual([...page.querySelectorAll(".cal-check .cal-rows > .cal-choice")].map((choice) => [choice.querySelector(".cal-choice-title").textContent, choice.querySelector(".cal-choice-desc").textContent]), [
      ["Apertura e chiusura complete", "Home Assistant segna aperta o chiusa troppo presto o troppo tardi."],
      ["Si ferma nel punto sbagliato", "Quando scegli una posizione tra aperta e chiusa, per esempio 30% o 80%."],
      ["Non so, vorrei ricontrollare tutto", "Misuriamo di nuovo i tempi; alla fine puoi continuare con il metro."]], "the third under the other two");
    assert.equal(page.querySelector(".cal-check .cal-rows + .cal-caption").textContent, it.cal_P1_note, "the note under the answers");
    assert.doesNotMatch(page.textContent, /Non ne sono sicuro|Se non sai quale/);
    await click(context, "cal-tab-check-back");
    assert.equal(card(context).querySelector(".cal-page-body").dataset.tab, "overview");
    assert.equal(context.root.activeElement, card(context).querySelector('[data-action="cal-tab-check-start"]'));
    card(context).querySelector(".cal-page-close").click(); await tick(8);
    reset();
    // A cover without a profile: the same question; English.
    const data = family();
    data.covers[2].profile_id = null;
    data.profiles[0].assigned_to = ["cover.living_one", "cover.kitchen_two"];
    const english = await mount({ language: "en", overview: data });
    page = await open(english, "cover.living_three");
    assert.equal(page.querySelector(".cal-pos-help .cal-choice-desc").textContent, "Answer one question to find out what to check.");
    await click(english, "cal-tab-check-start");
    page = card(english);
    assert.equal(head(page), "Something wrong?", "the procedure's name");
    assert.equal(page.querySelector(".cal-step-title").textContent, "What do you want to fix?");
    assert.deepEqual([...page.querySelectorAll(".cal-check .cal-choice-title")].map((title) => title.textContent), ["Full opening and closing", "It stops at the wrong point", "I’m not sure, I’d like to check everything again"]);
    assert.equal(page.querySelectorAll(".cal-check .cal-choice-desc")[2].textContent, "We record the times again; at the end you can continue with the tape measure.");
    // Escape closes the card from inside the procedure too.
    english.tab().dispatchEvent(new dom.window.KeyboardEvent("keydown", { key: "Escape", bubbles: true, composed: true })); await tick(8);
    assert.equal(card(english), null);
    const { readFileSync } = await import("node:fs");
    const css = readFileSync(new URL("../../custom_components/myhome/frontend/panel/panel-calibration.css", import.meta.url), "utf8");
    assert.match(css, /\.cal-rows > \* \+ \* \{ border-top: 1px solid var\(--panel-border\); \}/);
    assert.match(css, /\.cal-pairs > div \+ div \{ border-top: 1px solid var\(--panel-border\); \}/, "the same line");
    assert.doesNotMatch(css, /cal-pos-noref/);
  });

  test("outcomes on the source: «Si ferma nel punto sbagliato» Perfeziona, «Apertura e chiusura complete» Ricalibra, both with the source's warning (who uses the profile, who may lose a check); «Cambia risposta» and ‹", async () => {
    const context = await mount({ language: "it", overview: family({ check: ["cover.kitchen_two"] }) });
    await open(context, "cover.living_one");
    await click(context, "cal-tab-check-start");
    await click(context, "cal-tab-check-answer", "middle");
    let page = card(context);
    assert.equal(head(page), "Qualcosa non va?", "the procedure's name");
    assert.equal(page.querySelector(".cal-answer span").textContent, "Si ferma nel punto sbagliato", "the answer as it was chosen");
    assert.equal(page.querySelector(".cal-answer .cal-answer-change").textContent, "Cambia risposta");
    assert.equal(page.querySelector(".cal-check .cal-section-title").textContent, "Cosa possiamo fare");
    assert.equal(page.querySelector(".cal-check .cal-card-body p").textContent, it.cal_D1_todo);
    const warning = "Se aggiorni il profilo attuale, anche le tapparelle Kitchen shutter 2 e Living room shutter 3 useranno i nuovi valori, mantenendo le proprie impostazioni locali. "
      + "Potrebbe essere necessario ripetere la verifica del posizionamento per la tapparella Kitchen shutter 2. Se salvi un nuovo profilo, le altre tapparelle non cambiano.";
    assert.equal(plain(page.querySelector(".cal-note.is-warning").textContent), warning);
    assert.deepEqual([...page.querySelectorAll(".cal-check-actions button")].map((button) => [button.dataset.action, button.textContent.trim(), button.className]),
      [["cal-tab-refine", "Perfeziona", "primary"], ["cal-tab-check-back", "Indietro", "cal-check-back"]]);
    // «Cambia risposta»: the question again.
    await click(context, "cal-tab-check-change");
    assert.deepEqual(choices(card(context)), ["full", "middle", "unsure"]);
    await click(context, "cal-tab-check-answer", "full");
    page = card(context);
    assert.equal(page.querySelector(".cal-answer span").textContent, "Apertura e chiusura complete");
    assert.equal(plain(page.querySelector(".cal-step-title").textContent), "Controlliamo i tempi di questa tapparella");
    assert.equal(page.querySelector(".cal-step-lead").textContent, it.cal_D2_lead);
    assert.equal(plain(page.querySelector(".cal-check .cal-card-body p").textContent), "Misuriamo quanto impiega la tapparella ad aprirsi e a chiudersi del tutto, poi il tempo necessario ad aprire gli spazi tra le lamelle. La procedura ti guida con i pulsanti di avvio e arresto.");
    assert.equal(plain(page.querySelector(".cal-note.is-warning").textContent), warning);
    // «Non so, vorrei ricontrollare tutto»: Ricalibra too, the same outcome as «Apertura e chiusura complete» but for its
    // title and introduction.
    const full = [".cal-check .cal-card-body p", ".cal-note.is-warning", ".cal-check-actions"].map((selector) => page.querySelector(selector).outerHTML);
    await click(context, "cal-tab-check-change");
    await click(context, "cal-tab-check-answer", "unsure");
    page = card(context);
    assert.equal(page.querySelector(".cal-answer span").textContent, "Non so, vorrei ricontrollare tutto", "the answer as it was chosen");
    assert.equal(plain(page.querySelector(".cal-step-title").textContent), "Ricontrolliamo questa tapparella dall’inizio");
    assert.equal(page.querySelector(".cal-step-lead").textContent, it.cal_D2_lead_unsure);
    assert.deepEqual([".cal-check .cal-card-body p", ".cal-note.is-warning", ".cal-check-actions"].map((selector) => page.querySelector(selector).outerHTML), full, "D2 as for the first answer");
    await click(context, "cal-tab-check-change");
    await click(context, "cal-tab-check-answer", "full");
    // ‹ and «Indietro»: the question, then the card.
    card(context).querySelector('.cal-check-actions [data-action="cal-tab-check-back"]').click(); await tick(6);
    assert.deepEqual(choices(card(context)), ["full", "middle", "unsure"]);
    await click(context, "cal-tab-check-answer", "full");
    card(context).querySelector(".cal-page-head .cal-page-back").click(); await tick(6);
    assert.deepEqual(choices(card(context)), ["full", "middle", "unsure"]);
    card(context).querySelector(".cal-page-head .cal-page-back").click(); await tick(6);
    assert.equal(card(context).querySelector(".cal-page-body").dataset.tab, "overview");
    // Ricalibra: the wizard of today; cancelled, the card on «Posizionamento».
    await click(context, "cal-tab-check-start");
    await click(context, "cal-tab-check-answer", "full");
    const recalibrate = card(context).querySelector('.cal-check-actions [data-action="cal-tab-measure-cover"]');
    assert.equal(recalibrate.textContent.trim(), "Misura di nuovo i tempi");
    recalibrate.click(); await tick(6);
    assert.ok(context.wiz(), "Ricalibra: the wizard of today");
    await context.press("cancel-flow"); await tick(8);
    assert.equal(card(context).querySelector(".cal-page-body").dataset.tab, "overview", "cancelled: the card on «Posizionamento», the procedure closed");
    reset();
    // Alone on its profile (nobody else): no warning at all.
    const lonely = family();
    lonely.profiles[0].assigned_to = ["cover.living_one"];
    for (const cover of lonely.covers.slice(1)) cover.profile_id = null;
    const one = await mount({ language: "en", overview: lonely });
    await open(one, "cover.living_one");
    await click(one, "cal-tab-check-start"); await click(one, "cal-tab-check-answer", "middle");
    assert.equal(card(one).querySelector(".cal-note"), null);
    await click(one, "cal-tab-check-change"); await click(one, "cal-tab-check-answer", "full");
    assert.equal(card(one).querySelector(".cal-note"), null);
  });

  test("a follower has no question: «Controlla il posizionamento» opens Ricalibra at once, its warning only what is true today; ‹ and «Indietro» back to the card", async () => {
    const context = await mount({ language: "it", overview: family({ rolls: true }) });
    await open(context, "cover.kitchen_two");
    await click(context, "cal-tab-check-start");
    let page = card(context);
    assert.equal(head(page), "Qualcosa non va?", "the procedure's name");
    assert.equal(context.root.activeElement, page.querySelector(".cal-page-back"));
    assert.deepEqual(choices(page), [], "no question");
    assert.equal(page.querySelector(".cal-answer"), null, "no answer, no «Cambia risposta»");
    assert.equal(plain(page.querySelector(".cal-step-title").textContent), "Controlliamo i tempi di questa tapparella");
    assert.equal(plain(page.querySelector(".cal-check .cal-card-body p").textContent), plain(it.cal_D2_todo), "slats: D2-todo, without the name");
    assert.equal(plain(page.querySelector(".cal-note.is-warning").textContent),
      "Le nuove misure verranno salvate in un profilo nuovo, solo per questa tapparella. Non riceverà più gli aggiornamenti del profilo attuale.");
    assert.doesNotMatch(page.textContent, /curva/, "D0-warn-follower (the curve's warning) not shown yet");
    assert.equal(it.cal_D0_warn_follower, undefined, "not even a key yet");
    assert.deepEqual([...page.querySelectorAll(".cal-check-actions button")].map((button) => [button.dataset.action, button.textContent.trim()]),
      [["cal-tab-measure-cover", "Misura di nuovo i tempi"], ["cal-tab-check-back", "Indietro"]]);
    card(context).querySelector('.cal-check-actions [data-action="cal-tab-check-back"]').click(); await tick(6);
    page = card(context);
    assert.equal(page.querySelector(".cal-page-body").dataset.tab, "overview", "«Indietro»: the card, never a question that is not there");
    assert.ok(page.querySelector(".cal-page-tabs"), "the tabs back");
    assert.equal(context.root.activeElement, page.querySelector('.cal-pos-help [data-action="cal-tab-check-start"]'));
    await click(context, "cal-tab-check-start");
    card(context).querySelector(".cal-page-head .cal-page-back").click(); await tick(6);
    assert.equal(card(context).querySelector(".cal-page-body").dataset.tab, "overview", "‹ too");
    // Ricalibra from there: the wizard of today.
    await click(context, "cal-tab-check-start");
    await click(context, "cal-tab-measure-cover");
    assert.ok(context.wiz());
    await context.press("cancel-flow"); await tick(8);
    card(context).querySelector(".cal-page-close").click(); await tick(8);
    // No slats: D2-todo without them.
    await open(context, "cover.living_three");
    await click(context, "cal-tab-check-start");
    assert.equal(card(context).querySelector(".cal-check .cal-card-body p").textContent, it.cal_D2_todo_noslats);
    reset();
    // The source no longer on the profile: the same, in English.
    const data = family();
    data.covers[0].profile_id = null;
    data.profiles[0].assigned_to = ["cover.kitchen_two", "cover.living_three"];
    const away = await mount({ language: "en", overview: data });
    await open(away, "cover.kitchen_two");
    await click(away, "cal-tab-check-start");
    assert.equal(head(card(away)), "Something wrong?", "the procedure's name");
    assert.deepEqual(choices(card(away)), []);
    assert.equal(plain(card(away).querySelector(".cal-step-title").textContent), "Check this cover’s movement times");
  });

  test("at the end of Ricalibra A-34 offers Perfeziona as after Calibra (T647, «Perfeziona» in view, T319 never); a follower's Ricalibra saves a profile of its own measured there, so Perfeziona is offered there too", async () => {
    // The source alone on its profile («Apertura e chiusura complete»), a cover without a profile («Non so…»).
    const lonely = family();
    lonely.profiles[0].assigned_to = ["cover.living_one"];
    for (const cover of lonely.covers.slice(1)) cover.profile_id = null;
    for (const [entity, answer] of [["cover.living_one", "full"], ["cover.living_three", "unsure"]]) {
      const context = await mount({ language: "it", overview: lonely });
      const wizard = await recalibrated(context, entity, answer);
      const screen = wizard._current;
      assert.equal(screen.code, "A-34", entity);
      assert.ok(screen.body.endsWith(`\n\n${it.wiz_T647}`), `${entity}: T647, as after Calibra`);
      assert.doesNotMatch(screen.body, /Vuoi perfezionare/, `${entity}: not T319`);
      assert.deepEqual(footer(screen.actions).shown.map((action) => action.id), ["close", "refine-tape"], `${entity}: the primary and Perfeziona in view`);
      assert.equal(screen.actions.find((action) => action.id === "refine-tape").label, it.calTabActionRefine);
      await context.press("refine-tape");
      assert.equal(context.code(), "A-03", `${entity}: Perfeziona in the same frame`);
      reset();
    }
    // A follower: no question, Ricalibra at once; Home Assistant saves a profile of its own (`save_modes` ["new"]),
    // its times measured on it: no longer a follower, Perfeziona offered as on the source.
    const context = await mount({ language: "it", overview: family(), persist: true });
    const wizard = await recalibrated(context, "cover.kitchen_two", null);
    assert.equal(wizard._local.saved.target, "profile_new");
    assert.ok(wizard._current.body.endsWith(`\n\n${it.wiz_T647}`));
    assert.deepEqual(footer(wizard._current.actions).shown.map((action) => action.id), ["close", "refine-tape"], "the same A-34 as after Calibra");
    await context.press("close"); await tick(8);
    const page = await open(context, "cover.kitchen_two");
    assert.deepEqual(ctas(page).map(([action]) => action), ["cal-tab-refine"], "on its card too: the source of its own profile");
    assert.equal(checkContext(context.panel._calibrationTab._covers().find((item) => item.cover.entity_id === "cover.kitchen_two"), context.panel._calibrationTab._covers()).role, "source");
  });

  test("the procedure's logic as data: roles, answers and outcomes, the check at half the travel", () => {
    const items = (data) => data.covers.map((cover) => ({ cover, entry: { entry_id: "one" }, name: cover.name, profile: data.profiles.find((profile) => profile.id === cover.profile_id) || null,
      status: cover.precision === "none" ? "to_measure" : "calibrated" }));
    const all = items(family({ reference: null }));
    const [source, office, kitchen] = all;
    const ctx = (item) => checkContext(item, all);
    assert.deepEqual([ctx(source).role, ctx(office).role, ctx(kitchen).role], ["source", "follower", "follower"]);
    assert.deepEqual([answersOf(ctx(source)), answersOf(ctx(office))], [["full", "middle", "unsure"], []]);
    assert.deepEqual([outcomeOf(ctx(source), "full"), outcomeOf(ctx(source), "middle"), outcomeOf(ctx(source), "unsure"), outcomeOf(ctx(office), null), outcomeOf(ctx(office), "middle")], ["D2", "D1", "D2", "D2", "D2"]);
    assert.deepEqual(ctx(source).users.map((user) => user.id), ["cover.kitchen_two", "cover.living_three"]);
    assert.equal(ctx(office).sourceName, "Living room shutter 1");
    const alone = { ...source, profile: null };
    assert.deepEqual([checkContext(alone, all).role, answersOf(checkContext(alone, all))], ["alone", ["full", "middle", "unsure"]]);
    const none = items(sampleOverview())[0];
    assert.deepEqual([checkContext(none, [none]).role, answersOf(checkContext(none, [none]))], ["none", []]);
    assert.equal(checkAtHalf({ check_expected_cm: 55, travel_cm: 110 }, null), true);
    assert.equal(checkAtHalf({ check_expected_cm: 82.5, travel_cm: 110 }, null), false, "the tape's 75 %");
    assert.equal(checkAtHalf({ check_cm: 1 }, null), false, "not deduced from check_cm alone");
  });
});

describe("the outcome beside the answers", async () => {
  const { translations } = await import("../../custom_components/myhome/frontend/panel/panel-translations.js");
  const it = translations.it, en = translations.en;
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
  const key = async (context, name) => {
    context.root.activeElement.dispatchEvent(new dom.window.KeyboardEvent("keydown", { key: name, bubbles: true, composed: true }));
    await tick(6);
  };
  const tabs = (context) => [...card(context).querySelectorAll('.cal-check-answers [role="tab"]')].map((tab) => [tab.dataset.id, tab.getAttribute("aria-selected"), tab.getAttribute("tabindex")]);
  /** The rules of the 1024 px block that holds `marker`. */
  const wideCss = (marker) => { const start = cascadeCss.indexOf(marker); return block("@media (min-width: 1024px)", cascadeCss.slice(cascadeCss.lastIndexOf("@media (min-width: 1024px)", start))); };

  test("from 1024 px an answer opens its outcome beside the three answers (vertical tabs, the one chosen selected), no «Cambia risposta»; another answer changes the outcome on the same screen; ‹ and «Indietro» back to «Posizionamento»", async () => {
    desktop();
    const context = await mount({ language: "it", overview: family() });
    await open(context, "cover.living_one");
    await click(context, '.cal-pos-help [data-id="middle"]');
    assert.ok(body(context).matches(".cal-check.is-wide[data-tab=\"check\"]"));
    assert.equal(card(context).querySelector(".cal-page-head .muted").textContent, it.cal_S1_fold, "the title of the fold that opens it");
    assert.equal(card(context).querySelector(".cal-page-tabs"), null, "the card's tabs give way, as on the phone");
    const list = card(context).querySelector(".cal-check-answers");
    assert.deepEqual([list.getAttribute("role"), list.getAttribute("aria-orientation"), list.getAttribute("aria-label")], ["tablist", "vertical", it.cal_P1_title]);
    assert.deepEqual(tabs(context), [["full", "false", "-1"], ["middle", "true", "0"], ["unsure", "false", "-1"]]);
    assert.deepEqual([...list.querySelectorAll('[role="tab"]')].map((tab) => tab.textContent), ["full", "middle", "unsure"].map((id) => it[`cal_P1_${id}_title`]));
    const detail = card(context).querySelector(".cal-check-detail");
    assert.deepEqual([detail.getAttribute("role"), detail.getAttribute("aria-labelledby"), list.querySelector('[aria-selected="true"]').getAttribute("aria-controls")], ["tabpanel", "cal-check-tab-middle", "cal-check-detail"]);
    assert.equal(card(context).querySelector(".cal-answer, .cal-answer-change"), null, "no «Cambia risposta» (note 5)");
    assert.equal(detail.querySelector(".cal-section-title").textContent, it.cal_D0_heading);
    assert.equal(detail.querySelector(".cal-card-body p").textContent, it.cal_D1_todo, "Perfeziona");
    assert.deepEqual([...card(context).querySelectorAll(".cal-check-actions button")].map((button) => button.textContent), [it.cal_D1_button, it.cal_D0_back]);
    assert.equal(context.root.activeElement, card(context).querySelector(".cal-page-back"), "‹ focused, as on the phone");
    // Another answer: its outcome beside it, the same screen (the body keeps its id), the focus on its tab.
    const id = body(context).id;
    await click(context, '.cal-check-answers [data-id="full"]');
    assert.equal(body(context).id, id);
    assert.deepEqual(tabs(context).map(([answer, selected]) => [answer, selected]), [["full", "true"], ["middle", "false"], ["unsure", "false"]]);
    assert.equal(card(context).querySelector(".cal-check-detail .cal-step-title").textContent, it.cal_D2_title.replace("{cover}", "Living room shutter 1"), "Ricalibra");
    assert.equal(context.root.activeElement, card(context).querySelector("#cal-check-tab-full"));
    // ‹ and «Indietro»: «Posizionamento», the answer given focused.
    await click(context, ".cal-page-back");
    assert.equal(body(context).dataset.tab, "overview");
    assert.equal(context.root.activeElement, card(context).querySelector('.cal-pos-help [data-id="full"]'));
    await click(context, '.cal-pos-help [data-id="unsure"]');
    await click(context, ".cal-check-back");
    assert.equal(body(context).dataset.tab, "overview");
    assert.equal(context.root.activeElement, card(context).querySelector('.cal-pos-help [data-id="unsure"]'));
  });

  test("the answers beside the outcome move with ↑ ↓ (← →, Home, End), wrapping, their outcome following; English too", async () => {
    desktop();
    const context = await mount({ language: "en", overview: family() });
    await open(context, "cover.living_one");
    await click(context, '.cal-pos-help [data-id="full"]');
    card(context).querySelector("#cal-check-tab-full").focus();
    const selected = () => tabs(context).find(([, value]) => value === "true")[0];
    const steps = [["ArrowDown", "middle"], ["ArrowDown", "unsure"], ["ArrowDown", "full"], ["ArrowUp", "unsure"], ["Home", "full"], ["End", "unsure"], ["ArrowLeft", "middle"], ["ArrowRight", "unsure"]];
    for (const [name, expected] of steps) {
      await key(context, name);
      assert.equal(selected(), expected, name);
      assert.equal(context.root.activeElement, card(context).querySelector(`#cal-check-tab-${expected}`), `${name}: the focus follows`);
      assert.equal(card(context).querySelector(".cal-check-detail").getAttribute("aria-labelledby"), `cal-check-tab-${expected}`);
    }
    assert.equal(card(context).querySelector('.cal-check-answers [aria-selected="true"]').textContent, en.cal_P1_unsure_title);
    // «Not sure» leads to Ricalibra with a title of its own.
    assert.equal(card(context).querySelector(".cal-check-detail .cal-step-title").textContent, en.cal_D2_title_unsure.replace("{cover}", "Living room shutter 1"));
  });

  test("the window crossing 1024 px with the card open takes the other layout: the question on the phone becomes «Posizionamento», an outcome keeps its answer (with «Cambia risposta» back under 1024 px)", async () => {
    globalThis.matchMedia = windowOf(390);
    const context = await mount({ language: "it", overview: family() });
    await open(context, "cover.living_one");
    await click(context, '[data-action="cal-tab-check-start"]');
    desktop();
    context.panel._calibrationTab._render(); await tick(6);
    assert.ok(body(context).matches('.is-wide[data-tab="overview"]'), "the question is «Panoramica» on a desktop");
    assert.equal(card(context).querySelector("details.cal-fold").open, true, "its fold open on the answers");
    await click(context, '.cal-pos-help [data-id="middle"]');
    globalThis.matchMedia = windowOf(390);
    context.panel._calibrationTab._render(); await tick(6);
    assert.equal(card(context).querySelector(".cal-check-answers"), null);
    assert.equal(card(context).querySelector(".cal-answer span").textContent, it.cal_P1_middle_title, "the answer given, «Cambia risposta» beside it");
    await click(context, ".cal-page-back");
    assert.equal(card(context).querySelector(".cal-step-title").textContent, it.cal_P1_title, "on the phone ‹ goes to the question");
  });

  test("the stylesheet: the outcome's two columns as the wizard's steps column (260 px, white, a hairline on its right, 8 px rows, the chosen one 600 on the selected tint), the outcome 24 px in", () => {
    const css = wideCss(".cal-page-body.cal-check.is-wide");
    assert.match(css, /\.cal-page-body\.cal-check\.is-wide \{ display: grid; grid-template-columns: 260px minmax\(0, 1fr\); align-items: stretch; gap: 0; padding: 0; \}/);
    assert.match(css, /\.cal-check-answers \{ display: flex; flex-direction: column; gap: 2px; box-sizing: border-box; padding: var\(--cal-sp-16\) var\(--cal-sp-12\);\s*border-inline-end: 1px solid var\(--panel-border\); background: var\(--panel-surface\); \}/);
    assert.match(css, /\.cal-check-answers > button\[role="tab"\] \{[^}]*padding: var\(--cal-sp-8\);[^}]*border-radius: var\(--cal-r-8\);[^}]*font-weight: 500;/);
    assert.match(css, /\.cal-check-answers > button\[aria-selected="true"\] \{ background: var\(--cal-tint-selected\); font-weight: 600; \}/);
    // The outcomes stacked in one place, 24 px in (16 px more under the tallest).
    assert.match(css, /\.cal-check-detail \{ display: grid; grid-template-areas: "outcome"; align-content: start; min-width: 0; padding: 24px 24px calc\(24px \+ var\(--cal-sp-16\)\); \}/);
    assert.match(css, /\.cal-check-detail > \.cal-check-outcome \{ grid-area: outcome; display: flex; flex-direction: column; gap: var\(--cal-sp-32\); min-width: 0; \}/);
  });
});

describe("the outcome's height and Ricalibra's lines", async () => {
  const { translations } = await import("../../custom_components/myhome/frontend/panel/panel-translations.js");
  const it = translations.it, en = translations.en;
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
  /** The rules of the 1024 px block that holds `marker`. */
  const wideCss = (marker) => { const start = cascadeCss.indexOf(marker); return block("@media (min-width: 1024px)", cascadeCss.slice(cascadeCss.lastIndexOf("@media (min-width: 1024px)", start))); };
  /** jsdom lays nothing out: the overlay's box is as high as `heights` says for its view; its slides are recorded. */
  function heightStub(heights) {
    const proto = dom.window.Element.prototype, saved = [proto.getBoundingClientRect, proto.animate];
    const slides = [];
    proto.getBoundingClientRect = function () {
      if (!this.classList?.contains("cal-flow-box")) return { top: 0, bottom: 0, height: 0, width: 0 };
      const view = this.querySelector(".cal-page-body"), name = view?.dataset.tab === "check" ? `check-${view.querySelector('[aria-selected="true"]')?.dataset.id ?? view.id}` : view?.dataset.tab;
      return { top: 0, bottom: heights[name] ?? 400, height: heights[name] ?? 400, width: 480 };
    };
    proto.animate = function (keyframes, options) {
      const animation = { keyframes, options, cancelled: false, cancel() { this.cancelled = true; this.oncancel?.(); }, onfinish: null, oncancel: null };
      if (this.classList?.contains("cal-flow-box")) slides.push(animation);
      return animation;
    };
    return { slides, restore() { [proto.getBoundingClientRect, proto.animate] = saved; } };
  }

  test("beside the outcome each answer has its chevron at the right, as the rows of «Qualcosa non va?»", async () => {
    desktop();
    const context = await mount({ language: "it", overview: family() });
    await open(context, "cover.living_one");
    assert.ok(card(context).querySelector('.cal-pos-help [data-id="full"] > ha-icon[icon="mdi:chevron-right"]'), "the row it comes from");
    await click(context, '.cal-pos-help [data-id="full"]');
    const tabs = [...card(context).querySelectorAll('.cal-check-answers > [role="tab"]')];
    assert.deepEqual(tabs.map((tab) => [...tab.children].map((child) => [child.tagName.toLowerCase(), child.className || child.getAttribute("icon"), child.getAttribute("aria-hidden")])),
      tabs.map(() => [["span", "cal-check-tab-title", null], ["ha-icon", "mdi:chevron-right", "true"]]), "the title, then the chevron");
    assert.deepEqual(tabs.map((tab) => tab.textContent), ["full", "middle", "unsure"].map((id) => it[`cal_P1_${id}_title`]), "the name read is the answer's only");
    const css = wideCss(".cal-check-answers > button[role=\"tab\"] > ha-icon");
    assert.match(css, /\.cal-check-answers > button\[role="tab"\] \{ display: grid; grid-template-columns: minmax\(0, 1fr\) 24px; align-items: center; gap: var\(--cal-sp-12\);/);
    assert.match(css, /\.cal-check-answers > button\[role="tab"\] > ha-icon \{ --mdc-icon-size: 24px; color: var\(--panel-text-2\); \}/);
  });

  test("the outcome keeps one height for the three answers: the other outcome lies in the same place, unseen and inert; another answer draws in place, no slide; a change of page still slides", async () => {
    desktop({ motion: true });
    const stub = heightStub({ overview: 480, "check-full": 560, "check-middle": 500, "check-unsure": 560, details: 700 });
    try {
      const context = await mount({ language: "it", overview: family() });
      await open(context, "cover.living_one");
      await click(context, '.cal-pos-help [data-id="middle"]');
      assert.equal(stub.slides.length, 1, "«Panoramica» → outcome: a new page");
      const detail = card(context).querySelector(".cal-check-detail");
      const outcomes = [...detail.children];
      assert.deepEqual(outcomes.map((outcome) => [outcome.className, outcome.getAttribute("aria-hidden"), outcome.hasAttribute("inert")]),
        [["cal-check-outcome", null, false], ["cal-check-outcome is-sizer", "true", true], ["cal-check-outcome is-sizer", "true", true]],
        "Perfeziona shown, Ricalibra for «full» and for «unsure» only giving their height (one per other answer)");
      assert.equal(outcomes[0].querySelector(".cal-card-body p").textContent, it.cal_D1_todo);
      assert.equal(outcomes[1].querySelector(".cal-step-title").textContent, it.cal_D2_title.replace("{cover}", "Living room shutter 1"));
      assert.equal(outcomes[2].querySelector(".cal-step-title").textContent, it.cal_D2_title_unsure.replace("{cover}", "Living room shutter 1"));
      assert.equal(detail.querySelectorAll("button, a, input").length, 0, "nothing to reach in it");
      await click(context, '.cal-check-answers [data-id="full"]');
      await click(context, '.cal-check-answers [data-id="unsure"]');
      assert.equal(stub.slides.length, 1, "another answer: the same page, no slide");
      const shown = card(context).querySelector(".cal-check-detail > .cal-check-outcome:not(.is-sizer)");
      assert.equal(shown.querySelector(".cal-step-title").textContent, it.cal_D2_title_unsure.replace("{cover}", "Living room shutter 1"));
      assert.deepEqual([...card(context).querySelectorAll(".cal-check-detail > .is-sizer .cal-step-title")].map((title) => title.textContent),
        [it.cal_D2_title, it.cal_D1_title].map((text) => text.replace("{cover}", "Living room shutter 1")), "the other two answers, in their order");
      await click(context, ".cal-check-back");
      assert.equal(stub.slides.length, 2, "back to «Panoramica»: a new page");
      await pageTab(context.tab, "details");
      assert.equal(stub.slides.length, 3, "a tab: a new page");
    } finally { stub.restore(); }
    const css = wideCss(".cal-check-outcome.is-sizer");
    assert.match(css, /\.cal-check-detail \{ display: grid; grid-template-areas: "outcome"; align-content: start; min-width: 0; padding: 24px 24px calc\(24px \+ var\(--cal-sp-16\)\); \}/);
    assert.match(css, /\.cal-check-outcome\.is-sizer \{ visibility: hidden; pointer-events: none; \}/);
  });

  test("under 1024 px the outcome is alone, with no unseen copy", async () => {
    globalThis.matchMedia = windowOf(390);
    const context = await mount({ language: "it", overview: family() });
    await open(context, "cover.living_one");
    await click(context, '[data-action="cal-tab-check-start"]');
    await click(context, '[data-action="cal-tab-check-answer"][data-id="middle"]');
    assert.equal(card(context).querySelector(".is-sizer, .cal-check-outcome"), null);
  });

  test("Ricalibra on a cover without slats says nothing of them; with slats the cover's own line; slats not known the line with «se ha le lamelle» (IT, EN)", async () => {
    assert.equal(it.cal_D2_todo_noslats, "Misuriamo quanto impiega la tapparella ad aprirsi e a chiudersi del tutto. La procedura ti guida con i pulsanti di avvio e arresto.");
    assert.equal(en.cal_D2_todo_noslats, "We record how long the cover takes to open and close fully. The procedure guides you with start and stop controls.");
    // A follower without slats (no_profile): Ricalibra at once, on the phone (behind the entry) and from 1024 px (in «Posizionamento»).
    globalThis.matchMedia = windowOf(390);
    const phone = await mount({ language: "it", overview: family() });
    await open(phone, "cover.living_three");
    await click(phone, '[data-action="cal-tab-check-start"]');
    assert.equal(card(phone).querySelector(".cal-check .cal-card-body p").textContent, it.cal_D2_todo_noslats);
    reset();
    desktop();
    const wide = await mount({ language: "it", overview: family() });
    await open(wide, "cover.living_three");
    assert.equal(card(wide).querySelector(".cal-pos-outcome .cal-card-body p").textContent, it.cal_D2_todo_noslats);
    reset();
    // A cover of its own without slats (slat time 0, nothing said): Ricalibra from the question; with slats, its name.
    const data = family();
    Object.assign(data.covers[0], { slats_state: "unknown" });
    data.covers[0].effective.slat_time_s = timing(0);
    globalThis.matchMedia = windowOf(390);
    const english = await mount({ language: "en", overview: data });
    await open(english, "cover.living_one");
    await click(english, '[data-action="cal-tab-check-start"]');
    await click(english, '[data-action="cal-tab-check-answer"][data-id="full"]');
    assert.equal(card(english).querySelector(".cal-check .cal-card-body p").textContent, en.cal_D2_todo_noslats);
    reset();
    const slats = await mount({ language: "it", overview: family() });
    await open(slats, "cover.kitchen_two");
    await click(slats, '[data-action="cal-tab-check-start"]');
    assert.equal(card(slats).querySelector(".cal-check .cal-card-body p").textContent, it.cal_D2_todo);
    reset();
    const unknown = family();
    Object.assign(unknown.covers[0], { slats_state: "unknown" });
    delete unknown.covers[0].effective.slat_time_s;
    const generic = await mount({ language: "it", overview: unknown });
    await open(generic, "cover.living_one");
    await click(generic, '[data-action="cal-tab-check-start"]');
    await click(generic, '[data-action="cal-tab-check-answer"][data-id="full"]');
    assert.equal(card(generic).querySelector(".cal-check .cal-card-body p").textContent, it.cal_D2_todo_generic);
  });
});

describe("titles and introductions", async () => {
  const { translations } = await import("../../custom_components/myhome/frontend/panel/panel-translations.js");
  const it = translations.it, en = translations.en;
  /** A window `width` px wide and 800 high for `matchMedia`, motion reduced. */
  const windowOf = (width) => (query) => ({ matches: query.split(",").some((part) => [...part.matchAll(/\(\s*([a-z-]+)\s*:\s*([a-z0-9-]+)\s*\)/g)].every(([, name, value]) =>
    name === "max-width" ? width <= parseFloat(value) : name === "min-width" ? width >= parseFloat(value) : name === "max-height" ? 800 <= parseFloat(value)
      : name === "min-height" ? 800 >= parseFloat(value) : name === "orientation" ? value === "landscape" : name === "prefers-reduced-motion")), addEventListener() {}, removeEventListener() {} });
  const desktop = () => { globalThis.matchMedia = windowOf(1280); };
  const phone = () => { globalThis.matchMedia = windowOf(390); };
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
  const COVER = "Living room shutter 1";
  const row = (context, entity) => context.tab().querySelector(`.cal-row[data-entity="${entity}"]`);
  const card = (context) => context.tab().querySelector(".cal-flow .cal-page:not(.cal-assign-page)");
  async function open(context, entity) {
    row(context, entity).querySelector('[data-action="cal-tab-toggle"]').click();
    await tick(6);
    return card(context);
  }
  async function click(context, selector) {
    const button = (card(context) || context.tab().querySelector(".cal-flow .cal-page")).querySelector(selector);
    assert.ok(button, selector);
    button.click();
    await tick(6);
  }
  /** The outcome's title, introduction, section title and paragraph, the unseen copies (`is-sizer`) left out. */
  function outcome(context) {
    const page = card(context);
    const seen = (selector) => [...page.querySelectorAll(selector)].find((element) => !element.closest(".is-sizer"))?.textContent ?? null;
    return { title: seen(".cal-step-title"), lead: seen(".cal-step-lead"), heading: seen(".cal-check .cal-section-title, .cal-check-detail .cal-section-title"), todo: seen(".cal-check .cal-card-body p") };
  }

  test("on the phone «full» and «unsure» lead to Ricalibra with different titles and introductions, the rest the same; «Si ferma nel punto sbagliato» has its own; «Cosa possiamo fare»", async () => {
    phone();
    const context = await mount({ language: "it", overview: family() });
    await open(context, "cover.living_one");
    await click(context, '[data-action="cal-tab-check-start"]');
    const seen = {};
    for (const answer of ["full", "middle", "unsure"]) {
      await click(context, `[data-action="cal-tab-check-answer"][data-id="${answer}"]`);
      seen[answer] = { ...outcome(context), button: card(context).querySelector(".cal-check-actions .primary").dataset.action };
      await click(context, '[data-action="cal-tab-check-change"]');
    }
    assert.deepEqual(seen.full, { title: "Controlliamo i tempi di questa tapparella", lead: it.cal_D2_lead, heading: "Cosa possiamo fare",
      todo: it.cal_D2_todo, button: "cal-tab-measure-cover" });
    assert.deepEqual(seen.unsure, { title: "Ricontrolliamo questa tapparella dall’inizio",
      lead: "Ripartiamo dai tempi di apertura e chiusura. Alla fine potrai continuare con il metro, per rendere più precise le posizioni intermedie.",
      heading: "Cosa possiamo fare", todo: seen.full.todo, button: "cal-tab-measure-cover" }, "Ricalibra, its own title and introduction");
    assert.deepEqual(seen.middle, { title: "Perfezioniamo le posizioni intermedie di questa tapparella",
      lead: "Home Assistant calcola le posizioni intermedie dai tempi. Se si ferma nel punto sbagliato, qualche misura col metro aiuta a correggerle.",
      heading: "Cosa possiamo fare", todo: it.cal_D1_todo, button: "cal-tab-refine" });
  });

  test("a follower (no question) keeps Ricalibra's own title and introduction", async () => {
    phone();
    const context = await mount({ language: "it", overview: family() });
    await open(context, "cover.kitchen_two");
    await click(context, '[data-action="cal-tab-check-start"]');
    const shown = outcome(context);
    assert.equal(shown.title, it.cal_D2_title.replace("{cover}", "Kitchen shutter 2"));
    assert.equal(shown.lead, it.cal_D2_lead);
  });

  test("from 1024 px the selected tab decides the title: «unsure» its own, «middle» the fine-tuning's; the other two answers give their height", async () => {
    desktop();
    const context = await mount({ language: "en", overview: family() });
    await open(context, "cover.living_one");
    await click(context, '.cal-pos-help [data-id="unsure"]');
    assert.deepEqual(outcome(context), { title: "Let’s check this cover again from the start", lead: en.cal_D2_lead_unsure, heading: "What we can do",
      todo: en.cal_D2_todo });
    assert.equal(card(context).querySelectorAll(".cal-check-detail > .is-sizer").length, 2, "one unseen copy per other answer");
    await click(context, '.cal-check-answers [data-id="middle"]');
    assert.deepEqual(outcome(context), { title: "Let’s refine this cover’s intermediate positions", lead: en.cal_D1_lead, heading: "What we can do", todo: en.cal_D1_todo });
    await click(context, '.cal-check-answers [data-id="full"]');
    assert.equal(outcome(context).title, en.cal_D2_title.replace("{cover}", COVER));
  });
});

describe("the fold «Qualcosa non va?»", async () => {
  const { translations } = await import("../../custom_components/myhome/frontend/panel/panel-translations.js");
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
  async function click(context, selector) {
    const button = card(context).querySelector(selector);
    assert.ok(button, selector);
    button.click();
    await tick(6);
  }

  test("from 1024 px «Qualcosa non va?» is a fold in «Stato», closed: opened, the three answers each with › and the actuator's note; an answer leads to the procedure's page with the answers beside it; back, the fold open", async () => {
    desktop();
    const context = await mount({ language: "it", overview: family() });
    const page = await open(context, "cover.living_one");
    const fold = page.querySelector(".cal-pos-status > details.cal-fold.cal-pos-help");
    assert.ok(fold, "a fold in the card «Stato»");
    assert.equal(fold.open, false, "closed");
    const summary = fold.querySelector(":scope > summary");
    assert.deepEqual([summary.querySelector(".cal-help-icon").getAttribute("icon"), summary.querySelector(".cal-choice-title").textContent, summary.querySelector(".cal-choice-desc").textContent,
      summary.querySelector(".cal-help-chevron").getAttribute("icon")], ["mdi:help-circle-outline", "Qualcosa non va?", it.cal_S1_entry_desc, "mdi:chevron-down"]);
    assert.equal(page.querySelector('[data-action="cal-tab-check-start"]'), null, "no row that opens a step");
    fold.open = true; fold.dispatchEvent(new dom.window.Event("toggle")); await tick(4);
    assert.deepEqual([...fold.querySelectorAll(".cal-rows > .cal-choice")].map((choice) => [choice.dataset.id, choice.querySelector(".cal-choice-title").textContent, choice.querySelector(":scope > ha-icon").getAttribute("icon")]),
      ["full", "middle", "unsure"].map((id) => [id, it[`cal_P1_${id}_title`], "mdi:chevron-right"]));
    assert.equal(fold.querySelector(".cal-rows + .cal-caption").textContent, it.cal_P1_note, "the note on the installer under them");
    // The reads redraw the card: the fold the reader opened stays open; closed by the reader, it stays closed.
    context.panel._calibrationTab.refresh(); await tick(8);
    assert.equal(card(context).querySelector("details.cal-fold").open, true, "kept open across the reads");
    await click(context, '.cal-pos-help [data-id="middle"]');
    assert.ok(body(context).matches('.cal-check.is-wide[data-tab="check"]'), "the procedure's page, the answers beside the outcome");
    assert.equal(card(context).querySelector('.cal-check-answers [aria-selected="true"]').dataset.id, "middle");
    await click(context, ".cal-check-back");
    assert.equal(body(context).dataset.tab, "overview");
    const again = card(context).querySelector("details.cal-fold");
    assert.equal(again.open, true, "back: the fold open");
    assert.equal(context.root.activeElement, again.querySelector('[data-id="middle"]'), "the answer given focused");
    again.open = false; again.dispatchEvent(new dom.window.Event("toggle")); await tick(4);
    context.panel._calibrationTab.refresh(); await tick(8);
    assert.equal(card(context).querySelector("details.cal-fold").open, false, "closed by the reader: stays closed");
  });

  test("from 1024 px a follower's fold says «Ricalibriamo questa tapparella.» and holds Ricalibra with its warning", async () => {
    desktop();
    const context = await mount({ language: "it", overview: family() });
    const page = await open(context, "cover.kitchen_two");
    const fold = page.querySelector("details.cal-fold");
    assert.equal(fold.querySelector("summary .cal-choice-desc").textContent, it.cal_S1_fold_follower);
    assert.equal(fold.querySelector(".cal-pos-outcome .cal-note.is-warning").textContent, it.cal_D0_warn_follower_today);
    assert.equal(fold.querySelector('.cal-pos-outcome [data-action="cal-tab-measure-cover"]').textContent, it.cal_D2_button);
    card(context).querySelector(".cal-page-close").click(); await tick(8);
    assert.equal(row(context, "cover.kitchen_two").querySelector('[data-action="cal-tab-fix"]'), null);
  });

  test("under 1024 px «Qualcosa non va?» is a row of the card «Stato» that opens its own step: the three answers, or a follower's Ricalibra at once", async () => {
    phone();
    const context = await mount({ language: "it", overview: family() });
    const page = await open(context, "cover.living_one");
    assert.equal(page.querySelector("details.cal-fold"), null);
    const entry = page.querySelector('.cal-pos-status > .cal-pos-help > button.cal-help-row[data-action="cal-tab-check-start"]');
    assert.deepEqual([entry.querySelector(".cal-choice-title").textContent, entry.querySelector(".cal-choice-desc").textContent, entry.querySelector(":scope > ha-icon:last-child").getAttribute("icon")],
      ["Qualcosa non va?", it.cal_S1_entry_desc, "mdi:chevron-right"]);
    entry.click(); await tick(6);
    assert.deepEqual([...card(context).querySelectorAll(".cal-check .cal-rows > .cal-choice")].map((choice) => choice.dataset.id), ["full", "middle", "unsure"]);
    card(context).querySelector(".cal-page-back").click(); await tick(6);
    card(context).querySelector(".cal-page-close").click(); await tick(8);
    const follower = await open(context, "cover.kitchen_two");
    assert.equal(follower.querySelector(".cal-help-row .cal-choice-desc").textContent, it.cal_S1_fold_follower);
    await click(context, '[data-action="cal-tab-check-start"]');
    assert.equal(card(context).querySelector(".cal-step-title").textContent, it.cal_D2_title.replace("{cover}", "Kitchen shutter 2"), "Ricalibra at once");
  });
});

describe("the texts on the page", async () => {
  const { translations } = await import("../../custom_components/myhome/frontend/panel/panel-translations.js");
  const it = translations.it, en = translations.en;
  /** A window `width` px wide and 800 high for `matchMedia`, motion reduced. */
  const windowOf = (width) => (query) => ({ matches: query.split(",").some((part) => [...part.matchAll(/\(\s*([a-z-]+)\s*:\s*([a-z0-9-]+)\s*\)/g)].every(([, name, value]) =>
    name === "max-width" ? width <= parseFloat(value) : name === "min-width" ? width >= parseFloat(value) : name === "max-height" ? 800 <= parseFloat(value)
      : name === "min-height" ? 800 >= parseFloat(value) : name === "orientation" ? value === "landscape" : name === "prefers-reduced-motion")), addEventListener() {}, removeEventListener() {} });
  const desktop = () => { globalThis.matchMedia = windowOf(1280); };
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

  test("the check's texts on the page: the follower's fold and its Ricalibra, «Non so», a cover never calibrated", async () => {
    desktop();
    const context = await mount({ language: "it", overview: family() });
    let page = await open(context, "cover.kitchen_two");
    const fold = page.querySelector("details.cal-fold");
    assert.equal(fold.querySelector("summary .cal-choice-desc").textContent, "Ricalibriamo questa tapparella.");
    assert.equal(fold.querySelector(".cal-pos-outcome .cal-card-body p").textContent, it.cal_D2_todo);
    assert.equal(fold.querySelector(".cal-pos-outcome .cal-note.is-warning").textContent, it.cal_D0_warn_follower_today);
    await close(context);
    page = await open(context, "cover.living_one");
    page.querySelector('.cal-pos-help [data-action="cal-tab-check-answer"][data-id="unsure"]').click(); await tick(6);
    assert.equal(card(context).querySelector(".cal-check-outcome:not(.is-sizer) .cal-step-lead").textContent, it.cal_D2_lead_unsure);
    card(context).querySelector('[data-action="cal-tab-check-back"]').click(); await tick(6);
    await close(context);
    page = await open(context, "cover.living_three");
    assert.equal(page.querySelector(".cal-pos-state-desc").textContent, "Home Assistant stima la posizione con i tempi attualmente impostati.");
  });
});

describe("the procedure's title", async () => {
  const PANEL = "../../custom_components/myhome/frontend/panel/";
  const { translations } = await import(`${PANEL}panel-translations.js`);
  const it = translations.it, en = translations.en;
  const tabSource = readFileSync(new URL(`${PANEL}panel-calibration-tab.js`, import.meta.url), "utf8");
  /** A window `width` px wide and 800 high for `matchMedia`, motion reduced. */
  const windowOf = (width) => (query) => ({ matches: query.split(",").some((part) => [...part.matchAll(/\(\s*([a-z-]+)\s*:\s*([a-z0-9-]+)\s*\)/g)].every(([, name, value]) =>
    name === "max-width" ? width <= parseFloat(value) : name === "min-width" ? width >= parseFloat(value) : name === "max-height" ? 800 <= parseFloat(value)
      : name === "min-height" ? 800 >= parseFloat(value) : name === "orientation" ? value === "landscape" : name === "prefers-reduced-motion")), addEventListener() {}, removeEventListener() {} });
  const desktop = () => { globalThis.matchMedia = windowOf(1280); };
  const row = (context, entity) => context.tab().querySelector(`.cal-row[data-entity="${entity}"]`);
  const card = (context) => context.tab().querySelector(".cal-flow .cal-page:not(.cal-assign-page)");
  async function open(context, entity) {
    row(context, entity).querySelector('[data-action="cal-tab-toggle"]').click();
    await tick(6);
    return card(context);
  }
  const plain = (value) => String(value ?? "").replace(/[  ]/g, " ").trim();
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

  test("the procedure opened by «Qualcosa non va?» is titled as the fold that opens it (cal_S1_fold, Italian and English); cal_D0_header is gone", async () => {
    for (const [language, table] of [["it", it], ["en", en]]) {
      desktop();
      const context = await mount({ language, overview: world() });
      await open(context, "cover.kitchen_two");
      const fold = card(context).querySelector(".cal-pos-help");
      assert.equal(plain(fold.querySelector("summary .cal-choice-title").textContent), table.cal_S1_fold);
      fold.querySelector('[data-id="middle"]').click(); await tick(6);
      assert.equal(plain(card(context).querySelector(".cal-page-head .muted").textContent), table.cal_S1_fold, `${language}: the bar`);
      reset();
    }
    assert.deepEqual([it.cal_S1_fold, en.cal_S1_fold], ["Qualcosa non va?", "Something wrong?"]);
    assert.deepEqual([it.cal_D0_header, en.cal_D0_header], [undefined, undefined], "the key is gone");
    assert.doesNotMatch(tabSource, /cal_D0_header/);
  });
});
