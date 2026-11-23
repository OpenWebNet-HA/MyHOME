/**
 * Perfeziona in the wizard: the tape readings on the saved times and the automatic runs.
 */
import assert from "node:assert/strict";
import { after, afterEach, describe, test } from "node:test";

import { translations } from "../../custom_components/myhome/frontend/panel/panel-translations.js";
import { dom, mount, move, refineCover, reset, tick } from "./wizard-harness.mjs";

afterEach(reset);
after(() => dom.window.close());

describe("the readings", async () => {
  const { translations } = await import("../../custom_components/myhome/frontend/panel/panel-translations.js");
  const en = translations.en, it = translations.it;
  const headLine = (wiz) => wiz().querySelector(".wiz-head-phase").textContent;

  test("Perfeziona runs the tape readings on the saved times: the lift-off by itself, then up, half down, down, half up; the runs to the end stops are positionings, the times never change", async () => {
    const context = await mount({ language: "it" });
    const { tab, wiz, code, press, type, backend, actions } = context;
    backend.hass.states["cover.kitchen_two"] = { state: "closed", attributes: { current_position: 0 } };
    await refineCover(context, "cover.kitchen_two");
    assert.ok(wiz().querySelector(".wiz-body").textContent.startsWith("Il perfezionamento dura qualche minuto"), "the preparation says the readings only");
    await press("begin");
    const kept = { opening_time: 12.3, closing_time: 12.1 };
    assert.deepEqual(backend.session.values, kept);
    // The close to the rest and the rise for the slat time saved, by themselves: one waiting screen, two stages.
    backend.started(); await tick();
    assert.deepEqual([code(), [...wiz().querySelectorAll(".wiz-wait-stages li")].map((item) => item.dataset.stepState)], ["A-41", ["current", "future"]]);
    assert.equal(wiz().querySelector(".wiz-main .wiz-illustration"), null, "no drawing of a button to press");
    assert.ok(!wiz().querySelector('[data-wiz="endpoint"], [data-wiz="lift"]'), "nothing to press");
    backend.restReached(); await tick();
    backend.riseStarted(); await tick();
    assert.deepEqual([...wiz().querySelectorAll(".wiz-wait-stages li")].map((item) => item.dataset.stepState), ["done", "current"]);
    backend.tick(4.4); backend.autoStop(); await tick(); backend.stopped(); await tick();
    assert.equal(code(), "A-08");
    await type("0");
    assert.deepEqual([backend.session.step, backend.session.readings.gap, actions().at(-1)], ["opening", 0, "next"], "0 is a reading; straight up, by itself");
    await move(backend, { seconds: 13 });
    assert.equal(code(), "A-09");
    assert.ok(wiz().textContent.includes(it.wiz_T668), "where it goes and what comes next");
    await press("endpoint"); backend.stopped(); await tick();
    assert.equal(code(), "A-13", "the travel's field");
    await type("110");
    // From the top, half the time down, after the readings' briefing.
    assert.deepEqual([backend.session.step, code()], ["half_close", "A-16"]);
    await press("ack:readings");
    await move(backend, { seconds: 4, auto: true, stop: true });
    assert.equal(code(), "A-20");
    await type("70");
    assert.deepEqual([backend.session.step, actions().at(-1)], ["closing", "next"], "back to the bottom by itself");
    await move(backend, { seconds: 13 });
    assert.equal(code(), "A-04", "a positioning down, confirmed at its end stop");
    await press("endpoint"); backend.stopped(); await tick();
    assert.equal(backend.session.step, "half_open");
    assert.deepEqual(backend.session.values, kept, "the saved times, untouched");
    assert.equal(headLine(wiz), "Perfeziona · Letture col metro · 2 di 4");
    // The stepper lists the readings in the order they are taken.
    const readings = [...wiz().querySelectorAll(".wiz-steps > ol > li")].find((item) => item.textContent.startsWith("Letture col metro"));
    assert.deepEqual([...readings.querySelectorAll("ul > li")].map((item) => item.textContent),
      ["Sollevamento: 0 cm", "Corsa del telo: 110 cm", "Metà tempo discesa: 70 cm", "Metà tempo salita: —"]);
  });
});

describe("the briefing", async () => {
  const { translations } = await import("../../custom_components/myhome/frontend/panel/panel-translations.js");
  const i18n = translations;

  test("Perfeziona's briefing counts this cover's readings when its slats are known, and says the slat time may be refined", async () => {
    const { buildScreen } = await import("../../custom_components/myhome/frontend/panel/panel-calibration-screens.js");
    const t = (key) => i18n.it[key] ?? key;
    const before = (slats_state, local = {}) => buildScreen({ t, language: "it", stage: "before", path: "A", local: { keepTimes: true, ...local },
      cover: { entity_id: "cover.x", name: "X", slats_state, effective: {}, followers: [] } }).body;
    assert.equal(before("yes_measured"), i18n.it.wiz_T700);
    assert.equal(before("no_measured"), i18n.it.wiz_T701);
    assert.equal(before("unknown"), i18n.it.wiz_T684, "not known: both counts");
    assert.equal(before("unknown", { slats: false }), i18n.it.wiz_T701, "answered here: its count");
    const briefing = (slats) => buildScreen({ t, language: "it", stage: "session", path: "A", local: { keepTimes: true },
      cover: { entity_id: "cover.x", name: "X", effective: {}, followers: [] },
      view: { mode: "geometry", phase: "briefing", step: "home", keep_times: true, slats, values: {}, samples: {}, readings: {}, owner: true, sequence: 1 } }).body;
    assert.deepEqual([briefing(true), briefing(false)], [i18n.it.wiz_T700, i18n.it.wiz_T701]);
    assert.match(i18n.it.wiz_T700, /quattro letture, poi una verifica\. I tempi di salita e di discesa salvati restano quelli; il tempo lamelle può essere affinato dalla lettura del distacco\./);
    assert.match(i18n.it.wiz_T701, /tre letture, poi una verifica\. I tempi salvati restano quelli\./);
    assert.match(i18n.en.wiz_T700, /four readings, then a check\. The opening and closing times saved stay as they are; the slat time can be refined by the lift-off reading\./);
    assert.match(i18n.en.wiz_T701, /three readings, then a check\./);
    assert.match(i18n.it.wiz_T684, /con le lamelle la lettura del distacco può affinare il tempo lamelle/, "not known: the slat time said too");
  });
});

describe("automatic runs and arrivals", async () => {
  const { buildScreen, IN_MOTION } = await import("../../custom_components/myhome/frontend/panel/panel-calibration-screens.js");
  const { fill } = await import("../../custom_components/myhome/frontend/panel/panel-calibration-text.js");
  const it = translations.it;
  const t = (key) => it[key] || key;
  const cover = { entity_id: "cover.hall_window", name: "Hall window", effective: { closing: { value: 21.7, origin: "guided" }, opening: { value: 22.4, origin: "guided" },
    slat_time_s: { value: 2.7 } }, travel_cm: 160, followers: [] };
  const owned = { owner: true, read_only: false, sequence: 7, reason: null, stop_requested: false };
  const geometry = { ...owned, mode: "geometry", slats: true, keep_times: true, auto_lift: true, saved_slat_s: 2.7, values: { opening_time: 22.4, closing_time: 21.7 },
    samples: {}, readings: {}, geometry: {}, travel_cm: null, saved_travel_cm: 160, elapsed: 1 };
  const fineTuned = { samples: { lift: 2.7 }, readings: { gap: 0.4 }, travel_cm: 160 };
  /** Kitchen shutter 2 (calibrated, with times saved): Perfeziona without slats, on the saved times, from A-02. */
  async function toFineTuning(context) {
    const { backend, startFor, wiz, press } = context;
    backend.hass.states["cover.kitchen_two"] = { state: "closed", attributes: { current_position: 0 } };
    await startFor("cover.kitchen_two", "R");
    const radio = wiz().querySelector('input[name="slats"][value="no"]');
    if (radio) { radio.checked = true; radio.dispatchEvent(new Event("change", { bubbles: true })); await tick(); }
    await press("begin");
    assert.deepEqual([backend.session.keep_times, backend.session.step], [true, "opening"]);
  }
  const timeless = (wiz) => {
    assert.doesNotMatch(wiz().textContent, new RegExp(`${it.wiz_T502}|${it.wiz_T093}|${it.wiz_T444.slice(0, 40)}`), "no «salita non valida», no «Tempo di salita»");
    assert.equal(wiz().querySelector(".wiz-notice[data-tone=error]"), null);
  };
  /** The tape readings typed in the field, and the `next` the panel sends by itself. */
  const nexts = (context) => context.actions().filter((action) => action === "next").length;
  /** The stepper as drawn: each step's label, state, `aria-current` and sub-lines. */
  const stepper = (wiz) => [...wiz().querySelectorAll(".wiz-steps > ol > li")].map((item) => [item.querySelector(".wiz-step-label").textContent, item.dataset.stepState,
    item.getAttribute("aria-current"), [...item.querySelectorAll("ul > li")].map((sub) => sub.textContent)]);
  /** Perfeziona to its summary with the check passed (the fake's realistic check). */
  async function toCheckedSummary(context) {
    const { backend, type, press } = context;
    backend.realisticCheck = true;
    await toFineTuning(context);
    await tick();
    backend.started(); backend.tick(14.3); backend.busStop(); await tick(8);
    await type("160");
    await press("ack:readings"); await tick(8);
    backend.started(); backend.tick(7); backend.autoStop(); backend.stopped(); await tick(8);
    await type("80"); await tick(8);
    backend.started(); backend.tick(7.5); backend.busStop(); await tick(8);
    backend.started(); backend.tick(7); backend.autoStop(); backend.stopped(); await tick(8);
    await type("78"); await tick(8);
    await press("check-start"); await tick(8);
    backend.started(); backend.tick(7.5); backend.busStop(); await tick(8);
    backend.started(); backend.tick(10.4); backend.autoStop(); backend.stopped(); await tick(8);
    await type("81"); await tick(8);
    assert.equal(backend.session.phase, "review");
  }
  /**
   * An automatic run the panel does not start by itself (a page reloaded at its briefing, a claim from
   * another device) waits for «Avvia il movimento» and never says the cover is moving: no time left, no bar, no «Aspetta
   * che si fermi». Each briefing of an automatic run, with the text it says while waiting for that press.
   */
  const TO_START = [
    ["the return to the rest", { step: "reset", values: {} }, { ackLift: true }, "wiz_T711"],
    ["up to the top for the travel", { step: "opening" }, {}, "wiz_T716"],
    ["down to the bottom", { step: "closing" }, { ackReadings: true }, "wiz_T717"],
    ["half the time up", { step: "half_open" }, { ackReadings: true }, "wiz_T713"],
    ["half the time down", { step: "half_close", planned_seconds: 9.5 }, { ackReadings: true }, "wiz_T715"],
    ["the check's way to its end stop", { step: "top", check: { direction: "closing", target_cm: 80, planned_seconds: 10.4 } }, { ackReadings: true }, "wiz_T718"],
    ["the check's run up", { step: "check", check: { direction: "opening", target_cm: 80, planned_seconds: 10.4 } }, { ackReadings: true }, "wiz_T719"],
  ];

  test("Perfeziona on an actuator that stops by itself: its stop at the top is the arrival, the travel is read", async () => {
    const context = await mount({ language: "it" });
    const { backend, wiz, code } = context;
    await toFineTuning(context);
    await tick();
    assert.equal(backend.session.phase, "starting_open", "up by itself, as on the saved times");
    backend.started(); backend.tick(14.3); backend.busStop(); await tick(8);
    assert.deepEqual([backend.session.phase, backend.session.step, code()], ["reading", "opening", "A-13"]);
    assert.ok(wiz().querySelector('[name="reading_cm"]'), "the travel's field");
    timeless(wiz);
    assert.equal(context.actions().includes("endpoint"), false, "nothing to confirm");
  });

  test("a stop dated in a run of the saved times (an older Home Assistant) is never «non valida»: the cover arrived", async () => {
    const context = await mount({ language: "it" });
    const { backend, wiz, code, press } = context;
    await toFineTuning(context);
    await tick();
    backend.started(); backend.tick(14.3);
    Object.assign(backend.session, { motor_stopped: true, stopped_elapsed: 14.3 }); backend.emit(); await tick(8);
    assert.equal(backend.session.phase, "opening", "nothing sent by itself");
    timeless(wiz);
    await press("endpoint");
    backend.stopped(); await tick(8);
    assert.deepEqual([backend.session.phase, code()], ["reading", "A-13"]);
    timeless(wiz);
  });

  test("after an arrival the actuator reported, the next automatic run starts by itself, as after «È arrivata»", async () => {
    const context = await mount({ language: "it" });
    const { backend, wiz, code, type, press } = context;
    backend.realisticCheck = true;
    await toFineTuning(context);
    await tick();
    backend.started(); backend.tick(14.3); backend.busStop(); await tick(8);
    assert.deepEqual([backend.session.phase, code()], ["reading", "A-13"]);
    await type("160");
    assert.equal(code(), "A-16");
    await press("ack:readings"); await tick(8);
    assert.deepEqual([backend.session.step, backend.session.phase], ["half_close", "starting_close"], "half the time down");
    backend.started(); backend.tick(7); backend.autoStop(); backend.stopped(); await tick(8);
    await type("80");
    await tick(8);
    assert.deepEqual([backend.session.step, backend.session.phase], ["closing", "starting_close"], "down, by itself");
    backend.started(); backend.tick(7.5); backend.busStop(); await tick(8);
    // At the bottom by the actuator's stop: half the time up starts by itself, never a «sale da sola» standing still.
    const sent = nexts(context);
    assert.deepEqual([backend.session.step, backend.session.phase], ["half_open", "starting_open"], "half the time up, by itself");
    assert.equal(wiz().querySelector('[data-wiz="next"]'), null, "no start to press");
    assert.equal(wiz().querySelector('[data-wiz="stop"]').disabled, false, "Stop on");
    assert.ok(sent >= 3);
    backend.started(); backend.tick(7); backend.autoStop(); backend.stopped(); await tick(8);
    await type("78");
    await tick(8);
    assert.equal(backend.session.phase, "review");
    // The check: up to the top by itself, the actuator's stop there, then the check's run by itself.
    assert.equal(code(), "A-21v");
    await press("check-start"); await tick(8);
    assert.deepEqual([backend.session.step, backend.session.phase], ["top", "starting_open"], "to the top, by itself");
    backend.started(); backend.tick(7.5); backend.busStop(); await tick(8);
    assert.deepEqual([backend.session.step, backend.session.phase], ["check", "starting_close"], "the check's run, by itself");
    assert.equal(wiz().querySelector('[data-wiz="stop"]').disabled, false, "Stop on");
  });

  test("after Perfeziona's save the stepper stays as the summary left it: the travel, the check's precision, the summary current", async () => {
    const context = await mount({ language: "it" });
    const { backend, wiz, code, press } = context;
    await toCheckedSummary(context);
    const summary = stepper(wiz);
    assert.deepEqual(summary.at(-1).slice(1, 3), ["current", "step"]);
    assert.ok(summary.some(([, , , subs]) => subs.includes(`${it.wiz_T041}: 160 cm`)), "the travel under its step");
    assert.ok(summary.some(([, , , subs]) => subs.some((sub) => sub.startsWith(`${it.wiz_T570}: `))), "the precision under «Verifica»");
    // As Home Assistant: a closed session's view carries no check any more.
    const view = backend.view;
    backend.view = (extra) => { const shown = view(extra); return shown.phase === "saved" ? { ...shown, check: null } : shown; };
    await press("save"); await tick(6);
    if (wiz().querySelector('[data-wiz="save"]')) { await press("save"); await tick(6); }
    assert.equal(code(), "A-34");
    assert.deepEqual(stepper(wiz), summary, "the same steps, values and current step as at the summary");
  });

  test("an automatic run waiting for its start says to press «Avvia il movimento», with no time left and no bar", () => {
    for (const [label, extra, local, key] of TO_START) {
      const view = { ...geometry, ...fineTuned, phase: "briefing", ...extra };
      const screen = buildScreen({ t, language: "it", stage: "session", path: "A", local, cover, view, elapsed: null, previews: false });
      assert.equal(screen.wait.text, fill(it[key], { cover: "Hall window" }), `${label}: the text to start`);
      assert.match(screen.wait.text, /^Per iniziare premi «Avvia il movimento»: /, label);
      assert.doesNotMatch(screen.wait.text, /Aspetta che si fermi/, label);
      assert.equal(screen.wait.progress, false, `${label}: no bar`);
      assert.equal(screen.wait.remaining, "", `${label}: no time left`);
      assert.deepEqual([screen.actions[0].id, screen.actions[0].label, screen.actions[0].disabled ?? false], ["next", it.wiz_T659, false], `${label}: its start first`);
      assert.equal(screen.title, "Posizionamento della tapparella", `${label}: its title, not «si sta posizionando»`);
      // Started by the panel itself, the same briefing is today's: the run said as it goes, with its time left.
      const auto = buildScreen({ t, language: "it", stage: "session", path: "A", local: { ...local, autoStart: extra.step }, cover, view, elapsed: null, previews: false });
      assert.notEqual(auto.wait.text, screen.wait.text, `${label}: started by itself`);
      assert.equal(auto.actions.some((action) => action.id === "next"), false, `${label}: no start to press`);
      assert.equal(auto.title, it.wiz_T027, label);
    }
    // Path C's half ascent, and the English texts.
    const c = buildScreen({ t, language: "it", stage: "session", path: "C", local: { ackDescent: true, ackReadings: true }, cover, view: { ...geometry, ...fineTuned, keep_times: false, step: "half_open", phase: "briefing" }, elapsed: null });
    assert.equal(c.wait.text, fill(it.wiz_T714, { cover: "Hall window" }));
    assert.equal(translations.en.wiz_T713, "To begin, press «Start movement»: this cover will ascend by itself for half the opening time, then stop there.");
    assert.equal(translations.en.wiz_T659, "Start movement");
    assert.equal(translations.en.wiz_T720, "Positioning the cover");
  });

  test("A-16 says the runs in the order each path takes them: Perfeziona half down then half up, the full measure half up then half down", async () => {
    // Perfeziona (the saved times): from the top, half the closing time first.
    const context = await mount({ language: "it" });
    const { backend, wiz, code, type } = context;
    await toFineTuning(context);
    await tick();
    backend.started(); backend.tick(14.3); backend.busStop(); await tick(8);
    await type("160");
    assert.deepEqual([backend.session.step, code()], ["half_close", "A-16"]);
    assert.equal(wiz().querySelector(".wiz-body").textContent.trim(), it.wiz_T075_keep.replaceAll("**", ""));
    // Each path's text, in both languages; a Perfeziona briefing shown at the half ascent (a page reloaded there)
    // keeps Perfeziona's order.
    for (const [language, strings] of [["it", it], ["en", translations.en]]) {
      const tl = (key) => strings[key] || key;
      const body = (view, local = {}) => buildScreen({ t: tl, language, stage: "session", path: "A", local: { ackDescent: true, ...local }, cover, view: { ...geometry, ...fineTuned, phase: "briefing", ...view }, elapsed: null, previews: false });
      const refine = body({ step: "half_close" }), full = body({ keep_times: false, step: "half_open" }), reloaded = body({ step: "half_open" });
      assert.deepEqual([refine.code, refine.body], ["A-16", strings.wiz_T075_keep], `${language}: Perfeziona`);
      assert.deepEqual([full.code, full.body], ["A-16", strings.wiz_T075], `${language}: the full measure`);
      assert.equal(reloaded.body, strings.wiz_T075_keep, `${language}: Perfeziona at the half ascent`);
    }
    assert.match(it.wiz_T075_keep, /percorrerà metà del tempo di discesa e si fermerà; poi farà lo stesso con metà del tempo di salita\./);
    assert.match(it.wiz_T075, /percorrerà metà del tempo di salita e si fermerà; poi farà lo stesso con metà del tempo di discesa\./);
    assert.match(translations.en.wiz_T075_keep, /travel for half the closing time, then stop; then it will do the same for half the opening time\./);
    assert.match(translations.en.wiz_T075, /travel for half the opening time, then stop; then it will do the same for half the closing time\./);
  });

  test("once «Avvia il movimento» is pressed the run is said as it goes, with its time left", async () => {
    const context = await mount({ language: "it" });
    const { backend, panel, wiz, code, press } = context;
    // The run to the top does not start by itself (as on a page reloaded at its briefing).
    const wizard = panel._calibrationTab._wizard;
    const autoNext = wizard._autoNext;
    wizard._autoNext = () => {};
    try { await toFineTuning(context); await tick(); } finally { wizard._autoNext = autoNext; }
    assert.deepEqual([backend.session.step, backend.session.phase, code()], ["opening", "briefing", "A-09"]);
    const text = () => wiz().querySelector(".wiz-wait").textContent;
    const remaining = () => wiz().querySelector(".wiz-wait-remaining");
    assert.match(text(), /Per iniziare premi «Avvia il movimento»: la tapparella andrà al finecorsa in alto/);
    assert.doesNotMatch(text(), /Aspetta che si fermi/);
    assert.equal(remaining().hidden, true, "no time left");
    assert.equal(wiz().querySelector(".wiz-wait-meter").hidden, true, "no bar");
    const start = wiz().querySelector('[data-wiz="next"]');
    assert.deepEqual([start.textContent.trim(), start.disabled], ["Avvia il movimento", false]);
    const title = () => wiz().querySelector(".wiz-title").textContent.trim();
    assert.equal(title(), "Posizionamento della tapparella");
    const bar = () => [...wiz().querySelectorAll(".wiz-actions [data-wiz]")].map((item) => item.dataset.wiz);
    await press("next");
    backend.started(); backend.tick(3); backend.emit(); await tick();
    assert.equal(backend.session.phase, "opening");
    assert.equal(title(), "La tapparella si sta posizionando");
    assert.deepEqual(bar(), ["endpoint", "misbehaved", "stop"], "the bar of the run started by itself: no start left");
    assert.match(text(), new RegExp(it.wiz_T668));
    assert.doesNotMatch(text(), /Per iniziare/);
    assert.equal(remaining().hidden, false);
    assert.match(remaining().textContent, /^Tempo residuo circa \d+ s$/);
    assert.equal(wiz().querySelector(".wiz-wait-meter").hidden, false);
  });

  test("a two-leg run waiting for its start shows its stages as a preview, none in progress; once started, each stage as it runs", () => {
    const check = { direction: "closing", target_cm: 80, planned_seconds: 10.4 };
    const view = { ...geometry, ...fineTuned, step: "top", phase: "briefing", check };
    const states = (screen) => screen.wait.stages.map((stage) => stage.state);
    const waiting = buildScreen({ t, language: "it", stage: "session", path: "A", local: { ackReadings: true }, cover, view, elapsed: null, previews: false });
    assert.equal(waiting.wait.stages.length, 2);
    assert.match(waiting.wait.stages[0].text, /^1\/2 /);
    assert.equal(states(waiting).includes("current"), false, "no stage in progress");
    const started = buildScreen({ t, language: "it", stage: "session", path: "A", local: { ackReadings: true, manualStart: "top" }, cover,
      view: { ...view, phase: "opening" }, elapsed: 2, previews: false });
    assert.deepEqual(states(started), ["current", "future"]);
    assert.deepEqual(started.wait.stages.map((stage) => stage.text), waiting.wait.stages.map((stage) => stage.text), "the same stages");
  });
});

describe("a start refused because the cover is moving", () => {
  // Calibra (a cover never calibrated), Ricalibra and Perfeziona: each starts from «Prima di iniziare».
  const entries = {
    Calibra: async (context) => context.startFor("cover.living_one", "base"),
    Ricalibra: async (context) => context.startFor("cover.kitchen_two", "base"),
    Perfeziona: async (context) => refineCover(context, "cover.kitchen_two"),
  };
  for (const [entry, open] of Object.entries(entries)) {
    test(`${entry}: back to «Prima di iniziare» with the refusal above its buttons; «Ho capito, inizia» starts once the cover is still`, async () => {
      const context = await mount({ language: "it" });
      const { wiz, code, press, backend } = context;
      for (const entity of ["cover.living_one", "cover.kitchen_two"]) backend.hass.states[entity] = { state: "closed", attributes: { current_position: 0 } };
      await open(context);
      const before = code(), buttons = () => [...wiz().querySelectorAll(".wiz-actions [data-wiz]")].map((button) => button.dataset.wiz);
      const shown = buttons();
      assert.ok(shown.includes("begin"), `«Ho capito, inizia» on ${before}`);
      // Home Assistant refuses the first start: the cover still moves.
      const connection = backend.hass.connection, subscribe = connection.subscribeMessage;
      let refused = 0;
      connection.subscribeMessage = async (callback, message) => {
        if (message.type === "myhome/cover_calibration/start" && !refused++) throw { code: "calibration_moving" };
        return subscribe(callback, message);
      };
      await press("begin"); await tick();
      assert.equal(refused, 1);
      assert.equal(backend.session, null, "nothing started");
      assert.equal(code(), before, "«Prima di iniziare» again, not «Caricamento impianto…»");
      assert.deepEqual(buttons(), shown, "its own buttons, nothing new");
      const notices = [...wiz().querySelectorAll(".wiz-notices .wiz-notice[data-tone=error]")].map((notice) => notice.textContent.trim());
      assert.deepEqual(notices, [translations.it.profileError_calibration_moving]);
      // The cover is still now: the second press starts.
      await press("begin"); await tick();
      assert.equal(refused, 2);
      assert.ok(backend.session, "started");
      assert.notEqual(code(), before);
      assert.equal(wiz().querySelector(".wiz-notice[data-tone=error]"), null, "the refusal is gone");
    });
  }
});
