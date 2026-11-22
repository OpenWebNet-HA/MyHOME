/**
 * The wizard's screens: header and steps column, the action bar, fields and their notes, drawings, a reader's inert screens.
 */
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { after, afterEach, describe, test } from "node:test";

import { cascadeCss, rule, winner } from "./css-cascade.mjs";
import { dom, mount, move, refineCover, reset, tick } from "./wizard-harness.mjs";

afterEach(() => { reset(); for (const element of [document.documentElement, document.body]) { element.style.overflow = ""; element.style.overscrollBehaviorY = ""; } });
after(() => dom.window.close());

describe("the medium dialog", async () => {
  test("the medium footer has «Ferma» on the left with no divider; the primary above its secondary when they wrap; the phone's column elsewhere", () => {
    const medium = cascadeCss.slice(cascadeCss.indexOf("@media (min-width: 1024px) {\n  .cal-flow-box"));
    assert.match(medium, /\.cal-flow \.wiz-actions > \.wiz-stop-slot \{ order: -2; flex: none; display: flex; height: auto; margin: 0; \}/);
    assert.doesNotMatch(cascadeCss, /wiz-stop-slot[^{]*\{[^}]*border-inline-end/, "30: no divider after Stop");
    assert.match(medium, /\.cal-flow \.wiz-footer-main \{ order: 1; flex: 1 1 auto; min-width: 0; margin-inline-start: auto; display: flex; flex-wrap: wrap-reverse; align-items: center; justify-content: flex-end;/,
      "wrapped, the first line (the secondary) at the bottom: the primary above it");
    assert.match(medium, /\.cal-flow \.wiz-footer-main > :is\(\.wiz-action\.secondary, \.wiz-action\.text, \.wiz-action\.link\) \{ order: 1; \}\s*\.cal-flow \.wiz-footer-main > :is\(\.wiz-action\.primary, \.wiz-take\) \{ order: 2; \}/);
    assert.doesNotMatch(cascadeCss, /\.cal-flow \.wiz-actions > \.wiz-readonly|\.cal-flow \.wiz-actions > \.wiz-action-note/, "38b: no note in the footer");
  });

  test("an action's note (a preview saves nothing) is in the content, under the notices; the button still names it", async () => {
    const { wiz, startFor, code } = await mount();
    await startFor("cover.kitchen_two", "B");
    assert.equal(code(), "B-01");
    const button = wiz().querySelector('.wiz-actions [data-wiz="profile"]');
    const note = wiz().querySelector(`.wiz-main .wiz-notices #${button.getAttribute("aria-describedby")}`);
    assert.ok(note?.classList.contains("cal-note"), "a note of the content");
    assert.equal(wiz().querySelector(".wiz-actions p"), null, "the footer holds only buttons");
  });

  test("the medium dialog's steps column on the content's scale: «Passi» 12/500, steps 14/500 (current 14/600), sub-lines 12/400 with values 12/600", () => {
    const medium = cascadeCss.slice(cascadeCss.indexOf("@media (min-width: 1024px) {\n  .cal-flow-box"));
    // The column's head is the process 14/600 in the text colour (no longer «Passi» 12/500 grey).
    assert.match(cascadeCss, /\.wiz-steps-head \.wiz-steps-title \{ margin: 0; font-size: var\(--cal-fs-14\); line-height: var\(--cal-lh-14\); font-weight: 600; color: var\(--primary-text-color\); \}/);
    assert.match(medium, /\.cal-flow \.wiz-steps ol > li \{[^}]*font-size: var\(--cal-fs-14\); line-height: var\(--cal-lh-14\); font-weight: 500; \}/);
    assert.match(medium, /\.cal-flow \.wiz-steps ol > li\[data-step-state="current"\] > \.wiz-step-label \{ font-weight: 600; \}/);
    assert.match(medium, /\.cal-flow \.wiz-steps ul \{[^}]*font-size: var\(--cal-fs-12\); line-height: var\(--cal-lh-12\); font-weight: 400; color: var\(--panel-text-2\); \}/);
    assert.match(medium, /\.cal-flow \.wiz-steps ul strong \{ font-weight: 600; color: var\(--primary-text-color\); \}/);
    assert.doesNotMatch(medium, /\.cal-flow \.wiz-steps[^{]*\{[^}]*font-size: var\(--cal-fs-16\)/, "no 16 px left in the column");
  });

  test("a narrow field (128 px, the unit inside its end), its notes and refusal full width; drawings at most 40 % of the area, gone under 300 px", () => {
    assert.match(cascadeCss, /\.wiz-field \{ display: grid; gap: 6px; max-width: none; \}/, "the notes the content's width");
    assert.match(cascadeCss, /\.wiz-field \.input-suffix \{ display: block; box-sizing: border-box; width: 128px; max-width: 100%; \}/);
    assert.doesNotMatch(cascadeCss, /\.wiz-main \.wiz-field \{ max-width: none; \}/, "GM2's full-width field is gone");
    assert.match(cascadeCss, /\.wiz-illustration \{ display: block; width: auto; max-width: 100%; height: auto; max-height: max\(120px, 40cqh\); margin: 0; \}/);
    assert.match(cascadeCss, /\.wiz \.wiz-main \{ container: wizcontent \/ size; \}[^@]*@container wizcontent \(max-height: 299px\) \{ \.wiz-illustration \{ display: none; \} \}/);
  });

  test("four «Misure» go two by two, all four in a row where the card is 520 px or more; never three and one", () => {
    assert.match(cascadeCss, /\.wiz-results-block > \.cal-card \{ container: calmeasures \/ inline-size; \}/);
    assert.match(cascadeCss, /\.cal-measures:has\(> :nth-child\(4\)\) \{ grid-template-columns: repeat\(2, minmax\(0, 1fr\)\);/);
    assert.match(cascadeCss, /@container calmeasures \(min-width: 520px\) \{ \.cal-measures:has\(> :nth-child\(4\)\) \{ grid-template-columns: repeat\(4, minmax\(0, 1fr\)\); \} \}/);
  });
});

describe("fields, drawings and the action rule", async () => {
  const { CalibrationWizard } = await import("../../custom_components/myhome/frontend/panel/panel-calibration-wizard.js");
  const { ILLUSTRATED } = await import("../../custom_components/myhome/frontend/panel/panel-calibration-screens.js");
  const { move } = await import("./wizard-harness.mjs");
  /** Path A up to the half-ascent reading (A-18), whose field has a band: far readings are warned, not refused. */
  async function toA18(mounted) {
    const { backend, press, type, startFor } = mounted;
    await startFor("cover.living_one");
    await press("begin"); await move(backend); await press("endpoint"); backend.stopped(); await tick();
    await press("ack:lift"); await press("next"); await move(backend, { seconds: 4.4 }); await press("lift"); backend.stopped(); await tick();
    await type("3"); await move(backend); await press("endpoint"); backend.stopped(); await tick();
    await press("next"); await move(backend, { seconds: 22.2 }); await press("endpoint"); backend.stopped(); await tick();
    await type("198"); await press("ack:ascent");
    await press("next"); await move(backend, { seconds: 21.7 }); await press("endpoint"); backend.stopped(); await tick();
    await press("ack:descent"); await press("ack:readings"); await move(backend, { auto: true, stop: true });
  }
  const wait = (ms) => new Promise((resolve) => setTimeout(resolve, ms));
  const { translations } = await import("../../custom_components/myhome/frontend/panel/panel-translations.js");
  const { buildScreen, valueRows, travelText } = await import("../../custom_components/myhome/frontend/panel/panel-calibration-screens.js");
  const { footer } = await import("../../custom_components/myhome/frontend/panel/panel-calibration-wizard.js");
  const tIt = (key) => translations.it[key] ?? key, tEn = (key) => translations.en[key] ?? key;

  test("a reading is judged once still (800 ms without a key, the field left, or Enter), never while the number is being typed", async () => {
    const mounted = await mount();
    mounted.panel._calibrationTab.useWizard(new CalibrationWizard({ stageMs: 0, guardMs: 0, settleMs: 30, previews: true }));
    const { wiz, code, actions } = mounted;
    await toA18(mounted);
    assert.equal(code(), "A-18");
    const input = wiz().querySelector('[name="reading_cm"]'), warning = () => wiz().querySelector("#wiz-field-warning"), error = () => wiz().querySelector("#wiz-field-error");
    const button = () => wiz().querySelector('.wiz-actions [data-wiz="reading"]');
    const key = async (value) => { input.value = value; input.dispatchEvent(new Event("input", { bubbles: true })); await tick(); };
    // «72»: the «7» on its way is far from the model; nothing shows while the number grows.
    await key("4"); assert.equal(warning().hidden, true, "not at the first key");
    await key("40"); assert.equal(warning().hidden, true, "nor at the second");
    await wait(60); await tick();
    assert.equal(warning().hidden, false, "still for the settle time: the warning");
    assert.equal(button().disabled, false, "yellow never holds «Fatto, avanti» back");
    // The red refusal follows the same rule; once what is typed is accepted it goes at once.
    const sent = actions().length;
    await key("205"); assert.deepEqual([error().hidden, warning().hidden], [true, true], "nothing while typing, the old warning gone");
    await wait(60); await tick();
    assert.equal(error().hidden, false, "refused once still");
    assert.equal(button().disabled, true, "red holds it back");
    await key("97"); assert.deepEqual([error().hidden, button().disabled], [true, false], "accepted: the refusal goes at once");
    // Enter on a value never judged yet judges it (and, accepted, confirms it as «Fatto» would).
    await key("206");
    input.closest("form").dispatchEvent(new Event("submit", { bubbles: true, cancelable: true })); await tick();
    assert.equal(error().hidden, false, "Enter judged it: refused, nothing sent");
    assert.equal(actions().length, sent);
  });

  test("the refusal and the warning share one slot right under the field, the same note in its own tone, before the help", async () => {
    const mounted = await mount();
    const { wiz } = mounted;
    await toA18(mounted);
    const form = wiz().querySelector(".wiz-field");
    const slot = form.querySelector(":scope > label + .wiz-field-slot");
    assert.ok(slot, "the slot follows the field");
    assert.deepEqual([...slot.children].map((note) => [note.id, note.className]),
      [["wiz-field-error", "cal-note is-error wiz-field-message wiz-field-error"], ["wiz-field-warning", "cal-note is-warning wiz-field-message wiz-field-warning"]]);
    assert.equal(slot.nextElementSibling.id, "wiz-field-hint", "the help after the slot");
    assert.equal(form.querySelector("input").getAttribute("aria-describedby"), "wiz-field-error wiz-field-warning wiz-field-hint");
    assert.equal(winner(slot, "display"), "contents", "its notes are the field's own rows (no empty gap when silent)");
    // One at a time: a refusal hides the warning.
    const input = form.querySelector("input");
    input.value = "205"; input.dispatchEvent(new Event("focusout", { bubbles: true })); await tick();
    assert.deepEqual([wiz().querySelector("#wiz-field-error").hidden, wiz().querySelector("#wiz-field-warning").hidden], [false, true]);
  });

  test("the drawings: every illustrated step draws its drawing after its title, then text, field, message, help", async () => {
    const codes = Object.keys(ILLUSTRATED);
    assert.ok(codes.length >= 30, "A, B and the C clones");
    for (const code of ["A-05", "A-06", "A-07", "A-08", "A-10", "A-11", "A-13", "A-14", "A-16", "A-18", "A-20", "A-23", "A-31", "A-34", "B-02", "B-06", "C-08", "C-18"]) {
      assert.equal(ILLUSTRATED[code]?.position, "after-title", code);
    }
    assert.ok(Object.values(ILLUSTRATED).every((entry) => entry.position === "after-title"), "one place for every drawing");
    // The frame has a single place for it, after the title, before the text (A-18: a field under them).
    const mounted = await mount();
    await toA18(mounted);
    const main = mounted.wiz().querySelector(".wiz-main");
    const order = [...main.querySelectorAll(".wiz-title, .wiz-illustration, .wiz-body, .wiz-field")].map((item) => item.getAttribute("class").split(" ")[0]);
    assert.deepEqual(order, ["wiz-title", "wiz-illustration", "wiz-body", "wiz-field"], "title → drawing → text → field");
    assert.equal(main.querySelectorAll(".wiz-illustration").length, 1);
    assert.equal(main.querySelector(".wiz-illustration.is-small"), null, "only A-08's is smaller");
    assert.match(cascadeCss, /\.wiz-illustration\.is-small \{ max-height: min\(160px, max\(96px, 30cqh\)\); \}/);
  });

  test("four «Misure» on two rows get 16 px between the rows (4 more); four in one row are unchanged", () => {
    assert.match(cascadeCss, /\.cal-measures:has\(> :nth-child\(4\)\) \{ grid-template-columns: repeat\(2, minmax\(0, 1fr\)\); row-gap: calc\(var\(--cal-sp-12\) \+ var\(--cal-sp-4\)\); \}/);
    assert.match(cascadeCss, /@container calmeasures \(min-width: 520px\) \{ \.cal-measures:has\(> :nth-child\(4\)\) \{ grid-template-columns: repeat\(4, minmax\(0, 1fr\)\); \} \}/);
  });

  test("the process before the step in the header (Calibra / Ricalibra / Perfeziona / Segui) and as the medium column's title with its variant", async () => {
    // Built alone: a cover never measured is calibrated, a measured one recalibrated; path C fine-tunes; a reader follows.
    const view = { mode: "geometry", phase: "briefing", step: "opening", values: {}, samples: {}, readings: {}, slats: true, owner: true, sequence: 4 };
    const process = (extra) => buildScreen({ t: tIt, language: "it", stage: "session", path: "A", view, local: {}, cover: { name: "K", status: "to_measure" }, ...extra }).stepper;
    assert.equal(process().process, "Calibra");
    assert.equal(process({ cover: { name: "K", status: "calibrated" } }).process, "Ricalibra");
    assert.equal(process({ local: { startStatus: "to_measure" }, cover: { name: "K", status: "calibrated" } }).process, "Calibra", "the state when it was chosen, to the end");
    assert.equal(process({ path: "C", scope: "times_only" }).process, "Perfeziona");
    assert.equal(process({ path: "C", scope: "times_only" }).variant, "Solo i tempi", "the variant under the process");
    assert.equal(process({ view: { ...view, owner: false, read_only: true } }).process, "Segui");
    // The basic calibration is the cover's Calibra / Ricalibra («Base»); fine-tuning with the tape from A-02 too
    // («Col metro»), «Perfeziona» when entered from the cover's own action or from A-34.
    assert.deepEqual([process({ path: "base" }).process, process({ path: "base" }).variant], ["Calibra", "Base"]);
    assert.deepEqual([process({ cover: { name: "K", status: "calibrated" } }).variant], ["Col metro"]);
    assert.equal(process({ local: { refineEntry: true }, cover: { name: "K", status: "calibrated" } }).process, "Perfeziona");
    // Drawn: the header's line, and the medium column's title in place of «Passi».
    const context = await mount({ language: "it" });
    const { tab, wiz, startFor, choosePath } = context;
    await startFor("cover.kitchen_two", "base");
    assert.equal(wiz().dataset.code, "C-35");
    // Ricalibra asks about the slats on a fine-tuned cover too (its saved slat time: «Lamelle»).
    assert.match(wiz().querySelector(".wiz-head-phase").textContent, /^Ricalibra · Preparazione · 2 di 6$/);
    assert.equal(wiz().querySelector(".wiz-steps > .wiz-steps-head > .wiz-steps-title").textContent, "Ricalibra");
    assert.equal(wiz().querySelector(".wiz-steps > .wiz-steps-head > .wiz-steps-variant").textContent, "Base");
    assert.equal(wiz().querySelector(".wiz-steps").getAttribute("aria-label"), "Passi", "the column is still named «Passi»");
    assert.equal(wiz().querySelector(".wiz-sheet .wiz-sheet-title").textContent, "Passi", "the phone's sheet keeps its title");
    wiz().querySelector(".wiz-head .wiz-x").click(); await tick();
    await refineCover(context, "cover.kitchen_two");
    // Straight to the fine-tuning on the saved times: Preparazione, Letture, Verifica, Riepilogo.
    assert.match(wiz().querySelector(".wiz-head-phase").textContent, /^Perfeziona · Preparazione · 1 di 4$/);
    assert.equal(wiz().querySelector(".wiz-steps > .wiz-steps-head > .wiz-steps-variant").textContent, "Col metro");
    void tab; void choosePath;
  });

  test("following another device the fields and choices are inert: off, out of Tab, no pointer (no hover, focus, text cursor)", async () => {
    const session = { entry_id: "one", entity_id: "cover.living_one", session_id: "session-1", sequence: 9, revision: 7, mode: "geometry", reason: null,
      values: { opening_time: 22, closing_time: 21 }, samples: { lift: 4 }, readings: { gap: 3, half_open: 99, half_close: 98 }, geometry: { slat_time_s: 4, opening_roll: 1.8, closing_roll: 1.9 },
      travel_cm: 198, stop_requested: false, slats: true, lift_attempts: 1, still_resting: false, gap_warning: false, touching_cm: 1, max_gap_cm: 20, gap_warn_cm: 10, saved_travel_cm: 195,
      can_repeat: true, save_modes: ["new"], phase: "review", step: "half_close", check: { deviation_cm: 1, passed: true, measured_cm: 99, expected_cm: 98, check_seconds: 10 },
      screen: { code: "A-21", path: "A", scope: null, local: { ackLift: true, ackAscent: true, ackDescent: true, ackReadings: true } } };
    const { tab, wiz } = await mount({ session, owner: false });
    tab().querySelector('[data-action="cal-tab-session"]').click(); await tick(6);
    assert.equal(wiz().dataset.code, "A-21");
    assert.ok(wiz().hasAttribute("data-following"));
    const controls = [...wiz().querySelectorAll(".wiz-main :is(input, select, textarea)")];
    assert.ok(controls.length >= 1, "the profile's name at least (no switch on the tape's summary)");
    for (const control of controls) assert.deepEqual([control.disabled, control.tabIndex], [true, -1], control.name);
    for (const button of wiz().querySelectorAll(".wiz-actions button:disabled")) assert.equal(button.tabIndex, -1, button.dataset.wiz);
    assert.ok(wiz().querySelector('.wiz-actions [data-wiz="take"]:not(:disabled)'), "«Prendi il controllo» stays usable");
    assert.match(cascadeCss, /\.wiz\[data-following\] :is\(\.wiz-main :is\(input, select, textarea, label, \.wiz-radio, \.wiz-switch\), \.wiz-actions button:disabled\) \{ pointer-events: none; cursor: default; \}/);
  });

  test("the action rule, counted on every screen the wizard can draw: up to three in view, from four two and the fold, never a fold of one", () => {
    const counted = [];
    const check = (screen, label) => {
      const actions = screen.actions.filter((action) => !["stop", "stopped"].includes(action.id));
      const { shown, more, red } = footer(actions);
      counted.push(screen.code);
      assert.ok(shown.length <= 3, `${label} ${screen.code}: at most three in view`);
      assert.ok(more.length === 0 || more.length >= 2, `${label} ${screen.code}: never a fold of one`);
      assert.ok(actions.length <= 3 ? more.length === 0 : shown.length === 2, `${label} ${screen.code}: three all in view, four or more two in view`);
      assert.equal(shown.length + more.length + red.length, actions.length);
    };
    const steps = ["home", "lift", "reset", "opening", "closing", "half_open", "top", "half_close", "check"];
    const phases = ["briefing", "starting_open", "starting_close", "opening", "closing", "geometry_wait_stop", "reading", "review"];
    const locals = [{}, { ackLift: true }, { ackLift: true, ackAscent: true }, { ackDescent: true }, { ackDescent: true, ackReadings: true }, { ackReadings: true }, { travelTyped: 198 },
      { invalidRun: { step: "opening", reason: "late" } }, { autoStart: "reset" }];
    for (const path of ["A", "C"]) for (const step of steps) for (const phase of phases) for (const local of locals) for (const slats of [true, false]) {
      const view = { mode: "geometry", step, phase, values: { opening_time: 22, closing_time: 21 }, samples: { lift: 4 }, readings: { gap: 3 }, geometry: { slat_time_s: 4 },
        travel_cm: 198, slats, owner: true, sequence: 4, gap_warning: step === "reset" && phase === "briefing" && !!local.autoStart,
        ...(step === "check" ? { check: { direction: "closing", target_cm: 99, planned_seconds: 10 } } : {}) };
      check(buildScreen({ t: tEn, stage: "session", path, scope: path === "C" ? "times_and_rolls" : null, view, local, elapsed: 2, cover: { name: "K", effective: {} } }), `${path} ${step}/${phase}`);
    }
    for (const phase of ["confirm_closed", "starting_open", "opening", "confirm_open", "starting_close", "closing", "review"]) for (const local of [{}, { ackAscent: true }, { ackAscent: true, ackDescent: true }]) {
      check(buildScreen({ t: tEn, stage: "session", path: "C", scope: "times_only", view: { mode: "guided", phase, values: { opening_time: 22, closing_time: 21 }, owner: true, sequence: 3 }, local,
        cover: { name: "K", effective: {} } }), `guided ${phase}`);
    }
    // The ends: A-34 / C-34 for every way of saving, with and without fine-tuning; the interrupted session; the checks of the previews.
    for (const path of ["A", "C"]) for (const target of ["profile_new", "profile_update", "cover_only"]) for (const previews of [true, false]) {
      check(buildScreen({ t: tEn, stage: "saved", path, previews, local: { saved: { target, name: "x" } }, cover: { name: "K", effective: {} } }), `saved ${path} ${target}`);
    }
    check(buildScreen({ t: tEn, stage: "session", path: "A", view: { mode: "geometry", phase: "interrupted", recoverable: true, reason: "stopped", owner: true }, local: {}, cover: { name: "K" } }), "S-01");
    for (const path of ["B", "C"]) for (const passed of [true, false]) {
      check(buildScreen({ t: tEn, stage: "session", path, scope: path === "C" ? "points_only" : null, view: { preview: true, path: path === "B" ? "profile" : "rolls", phase: "check_result",
        check: { passed, gap_cm: passed ? 1 : 7, check_seconds: 10, predicted_cm: 99, measured_cm: 92 } }, local: {}, cover: { name: "K" } }), `preview ${path}`);
    }
    assert.ok(new Set(counted).size >= 30, `screens counted: ${new Set(counted).size}`);
    assert.ok(counted.includes("A-34") && counted.includes("B-07b"), "the busiest ends are among them");
  });
});

describe("steps column, drawings and red actions", async () => {
  /** A `matchMedia` for a window `width` px wide (and 800 high), motion reduced. */
  const windowOf = (width) => (query) => ({ matches: query.split(",").some((part) => [...part.matchAll(/\(\s*([a-z-]+)\s*:\s*([a-z0-9-]+)\s*\)/g)].every(([, name, value]) =>
    name === "max-width" ? width <= parseFloat(value) : name === "min-width" ? width >= parseFloat(value) : name === "max-height" ? 800 <= parseFloat(value)
      : name === "min-height" ? 800 >= parseFloat(value) : name === "orientation" ? value === "portrait" : name === "prefers-reduced-motion")), addEventListener() {}, removeEventListener() {} });
  /**
   * Walk path A from the first close to A-34 (and an interrupted run for «Annulla la misurazione»), checking every screen:
   * the order of an illustrated step's parts and where the red actions sit in the bar.
   */
  async function walk(context, check) {
    const { backend, press, type, startFor } = context;
    await startFor("cover.living_one"); check();
    await press("begin"); check(); await move(backend); check(); await press("endpoint"); check(); backend.stopped(); await tick(); check();
    await press("ack:lift"); check(); await press("next"); check(); await move(backend, { seconds: 4.4 }); check(); await press("lift"); check(); backend.stopped(); await tick(); check();
    await type("3"); check(); await move(backend); check(); await press("endpoint"); backend.stopped(); await tick(); check();
    await press("next"); check(); await move(backend, { seconds: 22.2 }); check(); await press("endpoint"); backend.stopped(); await tick(); check();
    await type("198"); check(); await press("ack:ascent"); check();
    await press("next"); check(); await move(backend, { seconds: 21.7 }); check(); await press("endpoint"); backend.stopped(); await tick(); check();
    await press("ack:descent"); check(); await press("ack:readings"); check(); await move(backend, { auto: true, stop: true }); check();
    await type("97"); check(); await move(backend, { seconds: 24 }); check(); await press("endpoint"); backend.stopped(); await tick(); check();
    await move(backend, { auto: true, stop: true }); check(); await type("101"); check(); await press("check-start"); check();
    await press("save"); check();
  }

  test("the steps column and the phone's «Passi» sheet are headed by the process 14/600 in the text colour, the variant 12/400 grey, 4 px apart, 16 px over the first step", async () => {
    const { wiz, startFor } = await mount({ language: "it" });
    await startFor("cover.living_one");
    for (const where of [".wiz-steps", ".wiz-sheet-steps"]) {
      const head = wiz().querySelector(`${where} > .wiz-steps-head`);
      assert.ok(head, where);
      assert.equal(head.querySelector(".wiz-steps-title").textContent, "Calibra");
      assert.equal(head.querySelector(".wiz-steps-variant").textContent, "Col metro");
      assert.equal(head.nextElementSibling.className, "wiz-steps-line");
    }
    assert.equal(rule(cascadeCss, ".wiz-steps-head .wiz-steps-title"), "margin: 0; font-size: var(--cal-fs-14); line-height: var(--cal-lh-14); font-weight: 600; color: var(--primary-text-color);");
    assert.equal(rule(cascadeCss, ".wiz-steps-head .wiz-steps-variant"), "margin: 0; font-size: var(--cal-fs-12); line-height: var(--cal-lh-12); font-weight: 400; color: var(--panel-text-2);");
    assert.match(rule(cascadeCss, ".wiz .wiz-sheet .wiz-sheet-steps .wiz-steps-head"), /display: grid; gap: var\(--cal-sp-4\); margin: 0 0 var\(--cal-sp-16\);/);
    assert.match(rule(cascadeCss, ".cal-flow .wiz-steps .wiz-steps-head"), /display: grid; gap: var\(--cal-sp-4\); margin: 0 0 var\(--cal-sp-16\);/);
    assert.match(rule(cascadeCss, ".wiz .wiz-sheet .wiz-sheet-head"), /\| display: none;$/, "the sheet's «Passi» title gives way to the head (the later rule)");
    // The steps' list starts right under the head (no margin of its own in the column and the sheet).
    assert.match(rule(cascadeCss, ".cal-flow .wiz-steps ol"), /margin: 0;/);
    assert.match(rule(cascadeCss, ".wiz .wiz-sheet .wiz-sheet-steps ol"), /margin: 0;/);
    void move;
  });

  test("a name the bar cuts («…») is read whole over the process in the phone's «Passi» sheet, measured at each opening; one the bar shows whole adds nothing", async () => {
    const { wiz, startFor, press, panel } = await mount({ language: "it" });
    await startFor("cover.living_one");
    const cover = wiz().querySelector(".wiz-head .wiz-cover"), name = cover.textContent;
    // jsdom lays nothing out (both widths 0): the bar's widths are set by hand on the name's own node.
    const widths = (scroll, client) => {
      Object.defineProperty(cover, "scrollWidth", { configurable: true, get: () => scroll });
      Object.defineProperty(cover, "clientWidth", { configurable: true, get: () => client });
    };
    const head = () => wiz().querySelector(".wiz-sheet-steps > .wiz-steps-head");
    const open = async () => { wiz().querySelector('.wiz-head [data-wiz="steps"]').click(); await tick(); assert.equal(wiz().querySelector(".wiz-sheet").hidden, false); };
    const close = async () => { await press("steps-close"); await new Promise((resolve) => setTimeout(resolve, 260)); };
    // Cut in the bar: the whole name first in the sheet's head, the process under it.
    widths(320, 180);
    await open();
    assert.deepEqual([...head().children].map((element) => [element.className, element.textContent]),
      [["wiz-steps-cover", name], ["wiz-steps-title", "Calibra"], ["wiz-steps-variant", "Col metro"]]);
    assert.equal(wiz().querySelector(".wiz-steps .wiz-steps-cover"), null, "the medium dialog's column never carries it");
    // The sheet patched while open keeps it.
    panel._calibrationTab._wizard.relayout();
    assert.equal(head().querySelector(".wiz-steps-cover")?.textContent, name);
    await close();
    // A wider window (or a rotation): the bar shows it whole; at the next opening the sheet starts from the process.
    widths(180, 180);
    await open();
    assert.equal(head().querySelector(".wiz-steps-cover"), null);
    assert.equal(head().firstElementChild.className, "wiz-steps-title");
    await close();
    // Narrower again: measured again.
    widths(320, 120);
    await open();
    assert.equal(head().firstElementChild.textContent, name);
    // 14/600 in the text colour, as the process under it; the name wraps on as many lines as it takes.
    assert.equal(rule(cascadeCss, ".wiz-steps-head .wiz-steps-cover"), "margin: 0; font-size: var(--cal-fs-14); line-height: var(--cal-lh-14); font-weight: 600; color: var(--primary-text-color); overflow-wrap: anywhere;");
  });

  test("every illustrated step reads title → drawing → text → field → its message → its help, on every screen that has a drawing", async () => {
    const { ILLUSTRATED } = await import("../../custom_components/myhome/frontend/panel/panel-calibration-screens.js");
    assert.ok(Object.values(ILLUSTRATED).every((entry) => entry.position === "after-title"), "one place in the inventory");
    const context = await mount({ language: "it" });
    const drawn = new Set();
    const before = (a, b) => Boolean(a.compareDocumentPosition(b) & dom.window.Node.DOCUMENT_POSITION_FOLLOWING);
    await walk(context, () => {
      const main = context.wiz()?.querySelector(".wiz-main"), drawing = main?.querySelector(".wiz-illustration");
      if (!drawing) return;
      drawn.add(context.code());
      const parts = [".wiz-title", ".wiz-illustration", ".wiz-body", ".wiz-field input", "#wiz-field-error", "#wiz-field-hint"].map((selector) => main.querySelector(selector)).filter(Boolean);
      assert.equal(parts[0], main.querySelector(".wiz-title"), `${context.code()}: the title first`);
      assert.equal(parts[1], drawing, `${context.code()}: then the drawing`);
      for (let i = 1; i < parts.length; i++) assert.ok(before(parts[i - 1], parts[i]), `${context.code()}: ${i - 1} before ${i}`);
    });
    for (const code of ["A-05", "A-06", "A-07", "A-08", "A-11", "A-13", "A-14", "A-16", "A-18", "A-20", "A-21v"]) assert.ok(drawn.has(code), `${code} walked`);
  });

  test("the red actions keep the fixed left slot of the bar on every step and band: never folded, never in the right group", async () => {
    // The stylesheet: the medium footer starts at the left; the slot and the red ways out first, the primary's group and ⋮ pushed right.
    const medium = cascadeCss.slice(cascadeCss.indexOf("@media (min-width: 1024px) {\n  .cal-flow-box"));
    assert.match(medium, /\.cal-flow \.wiz \.wiz-actions \{ flex: none; display: flex; flex-wrap: nowrap; align-items: center; justify-content: flex-start;/);
    assert.match(medium, /\.cal-flow \.wiz-actions > \.wiz-stop-slot \{ order: -2;/);
    assert.match(medium, /\.cal-flow \.wiz-actions > :is\(\.wiz-action\.danger-outline, \.wiz-action\.danger\) \{ order: -1; \}/);
    assert.match(medium, /\.cal-flow \.wiz-footer-main \{ order: 1; flex: 1 1 auto; min-width: 0; margin-inline-start: auto;/);
    assert.match(medium, /\.cal-flow \.wiz-actions > \.wiz-overflow \{ order: 3; margin-inline-start: auto; \}/);
    assert.doesNotMatch(cascadeCss, /wiz-stop-slot[^{]*\{[^}]*border-inline-end/, "no divider after it");
    const original = globalThis.matchMedia;
    const seen = { medium: new Set(), phone: new Set() };
    try {
      for (const [band, width] of [["medium", 1280], ["phone", 390]]) {
        globalThis.matchMedia = band === "medium" ? undefined : windowOf(width);
        const context = await mount({ language: "it" });
        const check = () => {
          const bar = context.wiz()?.querySelector(".wiz-actions");
          if (!bar) return;
          const reds = [...bar.querySelectorAll(".wiz-stop-slot, .wiz-action.stop, .wiz-action.danger-outline, .wiz-action.danger")].filter((item) => !item.parentElement.matches(".wiz-stop-slot"));
          for (const red of reds) {
            assert.equal(red.parentElement, bar, `${band} ${context.code()}: ${red.className} in the bar itself, its own slot`);
            assert.equal(red.closest(".wiz-footer-main, .wiz-overflow, .wiz-more"), null, `${band} ${context.code()}: never folded nor in the right group`);
          }
          if (reds.length) seen[band].add(context.code());
          // On the phone's column the slot and the red ways out close the bar (the last ones, after the gap).
          if (band === "phone" && reds.length) assert.deepEqual([...bar.children].slice(-reds.length), reds, `${context.code()}: last in the column`);
        };
        await walk(context, check);
        // An interrupted run: «Annulla la misurazione» (danger-outline) beside «Ricomincia».
        await context.press("another"); await tick();
        await context.startFor("cover.living_one"); await context.press("begin"); await move(context.backend);
        context.backend.stopFromElsewhere(); await tick(6); check();
        assert.ok(context.wiz().querySelector('.wiz-actions > .wiz-action.danger-outline[data-wiz="abort"]'), `${band}: the interrupted run's way out, red`);
      }
    } finally { globalThis.matchMedia = original; }
    for (const band of ["medium", "phone"]) for (const code of ["A-05", "A-07", "A-09", "A-11", "A-14"]) assert.ok(seen[band].has(code), `${band}: ${code} has Stop`);
  });
});

describe("the expiry notice", async () => {
  const { translations } = await import("../../custom_components/myhome/frontend/panel/panel-translations.js");
  const i18n = translations;
  const PANEL = new URL("../../custom_components/myhome/frontend/panel/", import.meta.url);
  const read = (name) => readFileSync(new URL(name, PANEL), "utf8");

  test("the expiry notice says the new sentence; its time an element of its own (bold, a step larger, baseline, tabular figures), the only node the countdown changes", async () => {
    assert.equal(i18n.it.wiz_T541, "La sessione scade fra {time}. Se non prosegui, la tapparella verrà liberata e le misure andranno perse.");
    assert.equal(i18n.en.wiz_T541, "The session expires in {time}. If you do not continue, the cover will be released and the measurements lost.");
    const css = read("panel-calibration.css");
    assert.match(css, /\.wiz-countdown-time \{ font-size: var\(--cal-fs-16\); font-weight: 700; line-height: 0; vertical-align: baseline; font-variant-numeric: tabular-nums; \}/);
    const { backend, wiz, startFor, press } = await mount({ language: "it" });
    await startFor("cover.living_one");
    await press("begin"); await move(backend); await press("endpoint"); backend.stopped(); await tick();
    backend.session.idle_expires_at = new Date(Date.now() + 42000).toISOString(); backend.emit(); await tick();
    const notice = wiz().querySelector(".wiz-notice[data-expires]"), time = notice.querySelector("strong.wiz-countdown-time");
    assert.match(time.textContent, /^0:4[12]$/);
    assert.equal(notice.textContent, `La sessione scade fra ${time.textContent}. Se non prosegui, la tapparella verrà liberata e le misure andranno perse.`);
    const before = notice.firstChild;
    await new Promise((resolve) => setTimeout(resolve, 1100));
    assert.equal(notice.querySelector(".wiz-countdown-time"), time, "the same element, its text counted down");
    assert.equal(notice.firstChild, before);
  });
});
