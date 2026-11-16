/**
 * The basic calibration in the wizard: the runs, «Lamelle», «Ripeti», the end stops, the summary and its save into a profile.
 */
import assert from "node:assert/strict";
import { after, afterEach, describe, test } from "node:test";

import { translations } from "../../custom_components/myhome/frontend/panel/panel-translations.js";
import { sampleOverview } from "./fake-calibration.mjs";
import { dom, mount, move, reset, settle, tick } from "./wizard-harness.mjs";

afterEach(reset);
after(() => dom.window.close());

describe("without slats, A-21b, A-34", async () => {
  const { translations } = await import("../../custom_components/myhome/frontend/panel/panel-translations.js");
  const { buildScreen } = await import("../../custom_components/myhome/frontend/panel/panel-calibration-screens.js");
  const { footer } = await import("../../custom_components/myhome/frontend/panel/panel-calibration-wizard.js");
  const en = translations.en;
  const plain = (text) => text.replace(/\*\*/g, "").replace(/\n+/g, "");
  const ids = (wiz) => [...wiz().querySelectorAll(".wiz-actions .wiz-action")].map((button) => button.dataset.wiz);
  const labels = (wiz) => [...wiz().querySelectorAll(".wiz-actions .wiz-action")].map((button) => button.textContent);
  const starts = (backend) => backend.subscriptions.filter((message) => message.type === "myhome/cover_calibration/start");
  const tiles = (wiz) => [...wiz().querySelectorAll(".wiz-results-block .cal-measure")].map((item) => [item.querySelector(".cal-measure-label").textContent, item.querySelector(".cal-measure-number").textContent]);
  const tableRows = (wiz) => [...wiz().querySelectorAll(".wiz-main .wiz-table-card > .wiz-table tbody tr")].map((row) => [...row.children].map((cell) => cell.textContent));
  /** Living room shutter 1 (never calibrated): A-02 with the basic calibration, closed at a known 0 (no positioning first). */
  async function toBasic(context, { slats = "yes" } = {}) {
    const { backend, startFor, code, wiz } = context;
    backend.hass.states["cover.living_one"] = { state: "closed", attributes: { current_position: 0 } };
    await startFor("cover.living_one", "base");
    assert.equal(code(), "C-35");
    const radio = wiz().querySelector(`input[name="slats"][value="${slats}"]`);
    radio.checked = true; radio.dispatchEvent(new Event("change", { bubbles: true })); await tick();
  }
  /** The ascent: ① (the bus confirms), ② at `opening` seconds. */
  async function ascent(context, { opening = 14.2 } = {}) {
    const { backend, press } = context;
    await press("run:open");
    await move(backend, { seconds: opening });
    await press("endpoint");
  }
  async function descent(context, seconds = 14.0) {
    const { backend, press } = context;
    await press("ack:ascent");
    await press("run:close");
    await move(backend, { seconds });
    await press("endpoint");
    await press("ack:descent");
  }
  /** The step «Lamelle» with slats: the run, the press at the lift-off, the session's own close, the result taken. */
  async function lifted(context, seconds = 2.7) {
    const { backend, press } = context;
    await press("run:open");
    await move(backend, { seconds });
    await press("lift");
    await press("ack:lift");
  }

  test("without slats the basic calibration is the guided times: two buttons, no tap, «Nessuna lamella», no technical details", async () => {
    const context = await mount({ language: "it" });
    const { backend, wiz, code, press } = context;
    await toBasic(context, { slats: "no" });
    await press("begin");
    assert.equal(starts(backend).at(-1).slats, false);
    assert.equal(wiz().querySelector(".wiz-body").textContent, plain(translations.it.wiz_T477));
    assert.deepEqual(labels(wiz), ["①  Avvia il movimento", "②  Ferma in alto", "Ferma la tapparella"]);
    await ascent(context, { lift: null, opening: 14.2 });
    assert.deepEqual(tiles(wiz), [["Tempo di salita", "14,2"]]);
    await descent(context, 14.0);
    assert.equal(code(), "A-21b");
    assert.deepEqual(tableRows(wiz).at(-1), ["Tempo lamelle", "—", "Nessuna lamella"]);
    assert.equal(wiz().querySelector(".wiz-technical"), null);
    await press("save");
    assert.equal(backend.calls.filter((message) => message.action === "save").at(-1).save_mode, "new");
    assert.equal(code(), "A-34");
  });

  test("A-21b keeps the guard of a new profile with slats (a lift-off missing or not shorter than both runs): said before Save, which stays off, with «Ricomincia»; on the cover alone Save comes on", () => {
    const t = (key) => en[key] || key;
    const view = (samples) => ({ mode: "guided", phase: "review", slats: true, samples, lift_taken: samples.lift != null, values: { opening_time: 14, closing_time: 2 }, owner: true, sequence: 9,
      save_modes: ["new", "cover", "shared"] });
    const cover = { name: "K", status: "to_measure", effective: { opening: { value: 22 }, closing: { value: 21.7 } } };
    const screen = (samples, local = {}) => buildScreen({ t, stage: "session", path: "base", local: { ackAscent: true, ackDescent: true, ackLift: true, ...local }, view: view(samples), cover });
    const refusals = (shown) => shown.notices.filter((notice) => notice.tone === "error").map((notice) => notice.text);
    assert.deepEqual(refusals(screen({})), [en.profileError_lift_missing]);
    const late = screen({ lift: 2.7 });
    assert.deepEqual(refusals(late), [en.profileError_lift_not_shorter]);
    assert.deepEqual(late.actions.map((action) => [action.id, !!action.disabled]), [["save", true], ["restart", false]]);
    // Saving only on the cover ignores the lift-off: no refusal, Save on, the slat time kept.
    const own = screen({ lift: 2.7 }, { saveProfile: false });
    assert.deepEqual([refusals(own), own.actions[0].disabled ?? false], [[], false]);
    assert.deepEqual(own.table.rows.at(-1), { label: "Slat time", before: "—", after: "=", changed: false });
    // Home Assistant's refusal at Save, in our words, once.
    const refused = buildScreen({ t, stage: "session", path: "base", local: { ackAscent: true, ackDescent: true, ackLift: true }, view: view({}), status: { error: { code: "lift_missing" } }, cover });
    assert.deepEqual(refusals(refused), [en.profileError_lift_missing]);
  });

  test("A-34 after a basic calibration: T647, «Perfeziona col metro» the secondary in view, «Assegna» under the fold; it opens fine-tuning with the tape in the same frame", async () => {
    const context = await mount();
    const { backend, wiz, code, press, panel } = context;
    await toBasic(context);
    await press("begin");
    await ascent(context);
    await descent(context);
    await lifted(context);
    await press("save");
    assert.equal(code(), "A-34");
    assert.match(wiz().querySelector(".wiz-body").textContent, /Need more precision\? Fine-tuning with a tape measure adds a few readings and a final check/);
    const screen = panel._calibrationTab._wizard._current;
    assert.deepEqual(screen.actions.map((action) => action.id), ["close", "refine-tape", "assign-profile", "another", "open-cover"]);
    assert.deepEqual(footer(screen.actions).shown.map((action) => action.id), ["close", "refine-tape"], "the primary and one secondary in view");
    assert.deepEqual(footer(screen.actions).more.map((action) => action.id), ["assign-profile", "another", "open-cover"]);
    assert.equal(wiz().querySelector(".wiz-head-phase").textContent, "Calibrate · Summary · 5 of 5", "an epilogue, not a step (no choice of the path)");
    assert.equal(wiz().hasAttribute("data-closable"), true, "saved: closing costs nothing");
    await press("refine-tape");
    assert.equal(code(), "A-03");
    // The fine-tuning on the times just saved: Preparation, Readings, Check, Summary.
    assert.equal(wiz().querySelector(".wiz-head-phase").textContent, "Fine-tune · Preparation · 1 of 4");
    // The base calibration just saved answers the slats question: not asked again.
    assert.equal(wiz().querySelector('input[name="slats"]'), null, "known from the base just saved: not asked");
    assert.equal(wiz().querySelector('[data-wiz="back"]'), null, "no way back to the calibration just closed");
    await press("begin");
    assert.equal(starts(backend).at(-1).mode, "geometry", "today's path A, live");
    assert.notEqual(starts(backend).at(-1).slats, false, "with the base's answer (with slats: Home Assistant's default)");
  });

  test("following another device the basic calibration's C-11 is inert: only Stop, no ①/②; with slats the steps are the six of the basic calibration", async () => {
    const session = { entry_id: "one", entity_id: "cover.living_one", session_id: "session-1", sequence: 5, revision: 7, mode: "guided", origin: "wizard",
      phase: "opening", reason: null, values: {}, stop_requested: false, save_modes: ["new", "cover", "shared"], slats: true, samples: {}, lift_taken: false, _started: 0,
      screen: { code: "C-11", path: "base", scope: null, slats: true, local: {} } };
    const { tab, wiz, code } = await mount({ session, owner: false });
    tab().querySelector('[data-action="cal-tab-session"]').click(); await tick(8);
    assert.equal(code(), "C-11");
    assert.ok(wiz().hasAttribute("data-following"));
    assert.deepEqual(ids(wiz), ["stop"], "the reader's bar: Stop only");
    assert.ok(wiz().querySelector(".wiz-readonly"));
    assert.equal([...wiz().querySelectorAll(".wiz-steps > ol > li")].length, 6);
    assert.equal(wiz().querySelector(".wiz-body").textContent, plain(en.wiz_T521));
  });
});

describe("the lift-off run", async () => {
  const { translations } = await import("../../custom_components/myhome/frontend/panel/panel-translations.js");
  const en = translations.en, it = translations.it;
  const plain = (text) => text.replace(/\*\*/g, "").replace(/\n+/g, "");
  const bar = (wiz) => [...wiz().querySelectorAll(".wiz-actions .wiz-action")].map((button) => [button.dataset.wiz, button.textContent, button.disabled]);
  const head = (wiz) => wiz().querySelector(".wiz-head-phase").textContent;
  const body = (wiz) => wiz().querySelector(".wiz-body").textContent;
  const tiles = (wiz) => [...wiz().querySelectorAll(".wiz-results-block .cal-measure")].map((item) => [item.querySelector(".cal-measure-label").textContent, item.querySelector(".cal-measure-number").textContent]);
  const tableRows = (wiz) => [...wiz().querySelectorAll(".wiz-main .wiz-table-card > .wiz-table tbody tr")].map((row) => [...row.children].map((cell) => cell.textContent));
  const steps = (wiz) => [...wiz().querySelectorAll(".wiz-steps > ol > li")].map((item) => [item.querySelector(".wiz-step-label").textContent, item.dataset.stepState,
    [...item.querySelectorAll("ul > li")].map((sub) => sub.textContent)]);
  const errors = (wiz) => [...wiz().querySelectorAll(".wiz-main .wiz-notice[data-tone=error]")].map((notice) => notice.textContent);
  /** Living room shutter 1 (never calibrated, closed at a known 0): C-35 of the basic calibration. */
  async function toBasic(context) {
    context.backend.hass.states["cover.living_one"] = { state: "closed", attributes: { current_position: 0 } };
    await context.startFor("cover.living_one", "base");
  }
  /** The two full runs, the results acknowledged: the step «Lamelle» waits closed (`confirm_lift`). */
  async function runs(context, { opening = 14.2, closing = 14.0 } = {}) {
    const { backend, press } = context;
    await press("run:open"); await move(backend, { seconds: opening }); await press("endpoint");
    await press("ack:ascent");
    await press("run:close"); await move(backend, { seconds: closing }); await press("endpoint");
  }
  const tick_ = async (wiz, name, value) => {
    const radio = wiz().querySelector(`input[name="${name}"][value="${value}"]`);
    radio.checked = true; radio.dispatchEvent(new Event("change", { bubbles: true })); await tick();
  };

  test("with slats the basic calibration has six steps; ascent and descent are «Solo i tempi»'s two buttons; «Lamelle» is a run of its own: one primary that follows it, Stop pinned, the session's own close, a result that can be repeated; A-21b shows the three times", async () => {
    const context = await mount();
    const { backend, wiz, code, press, title, actions } = context;
    await toBasic(context);
    assert.equal(code(), "C-35");
    assert.equal(head(wiz), "Calibrate · Preparation · 1 of 5", "never measured: no choice of the path, «with slats» preselected, so «Lamelle» counts");
    await tick_(wiz, "slats", "no");
    assert.equal(head(wiz), "Calibrate · Preparation · 1 of 4", "without slats, four steps at once");
    await tick_(wiz, "slats", "yes");
    assert.equal(head(wiz), "Calibrate · Preparation · 1 of 5");
    assert.equal(body(wiz), plain(en.wiz_T637));
    await press("begin");
    assert.equal(backend.subscriptions.filter((message) => message.type === "myhome/cover_calibration/start").at(-1).slats, true);
    // C-11: the two buttons of «Solo i tempi» (no third, no tap).
    assert.deepEqual([code(), title(), body(wiz)], ["C-11", "Full ascent", plain(en.wiz_T477)]);
    assert.deepEqual(bar(wiz), [["run:open", "①  Start movement", false], ["endpoint", "②  Stopped at the top", true], ["stop", "Stop the cover", true]]);
    await press("run:open"); await move(backend, { seconds: 14.2 });
    assert.equal(body(wiz), plain(en.wiz_T521));
    await press("endpoint");
    assert.deepEqual([code(), tiles(wiz), body(wiz)], ["C-12", [["Opening time", "14.2"]], plain(en.wiz_T478)]);
    await press("ack:ascent");
    await press("run:close"); await move(backend, { seconds: 14.0 }); await press("endpoint");
    // The measured descent reaches `confirm_lift`: its result first (C-15), as without slats.
    assert.deepEqual([backend.session.phase, code(), head(wiz)], ["confirm_lift", "C-15", "Calibrate · Descent · 3 of 5"]);
    await press("ack:descent");
    // C-07 at rest: the lift-off drawing of A-07, the body, ① and ② as in the ascent and descent, Stop pinned, off.
    assert.deepEqual([code(), title(), head(wiz), body(wiz)], ["C-07", "Edge lift-off", "Calibrate · Slats · 4 of 5", plain(en.wiz_T658)]);
    assert.equal(wiz().querySelector(".wiz-main .wiz-illustration").dataset.illustration, "lift-sequence");
    const order = [".wiz-title", ".wiz-illustration", ".wiz-body"].map((selector) => wiz().querySelector(`.wiz-main ${selector}`));
    assert.ok(order[0].compareDocumentPosition(order[1]) & 4 && order[1].compareDocumentPosition(order[2]) & 4, "title → drawing → body");
    assert.deepEqual(bar(wiz), [["run:open", "①  Start movement", false], ["lift", "②  Lifted off", true], ["stop", "Stop the cover", true]]);
    assert.deepEqual(steps(wiz).map(([label, state]) => [label, state]),
      [["Preparation", "done"], ["Ascent", "done"], ["Descent", "done"], ["Slats", "current"], ["Summary", "future"]]);
    assert.deepEqual(steps(wiz)[3][2], ["Slat time: —"], "«—» under «Lamelle» before the lift-off, as the ascent and the descent");
    // Asked: «Si è staccato» off and settling until the bus reports the start; Stop on.
    await press("run:open");
    assert.equal(backend.session.phase, "starting_lift");
    const lift = () => wiz().querySelector('.wiz-actions [data-wiz="lift"]');
    assert.deepEqual([lift().textContent, lift().disabled], ["②  Lifted off", true]);
    assert.equal(wiz().querySelector('.wiz-actions [data-wiz="stop"]').disabled, false);
    await move(backend, { seconds: 2.7 });
    assert.deepEqual(bar(wiz), [["run:open", "①  Start movement", true], ["lift", "②  Lifted off", false], ["stop", "Stop the cover", false]]);
    assert.equal(wiz().querySelector(".wiz-stopwatch").hidden, false, "the motor's time while it runs");
    // The press is the Stop: the cover stays where it stopped; its result at once.
    await press("lift");
    assert.deepEqual([backend.session.phase, backend.session.samples], ["review", { lift: 2.7 }]);
    assert.deepEqual(steps(wiz)[3][2], ["Slat time: 2.7 s"], "the stepper says the lift-off once taken");
    // C-08b: the result, provisional, with its repeat, and the note on a few centimetres.
    assert.deepEqual([code(), title(), tiles(wiz), body(wiz)], ["C-08b", "Lift-off measured", [["Slat time", "2.7"]], en.wiz_T664 + en.wiz_T670]);
    assert.equal(wiz().querySelector(".wiz-results-block .cal-section-title").textContent, "Lift-off measured", "«Stacco misurato» over the «Misura»");
    assert.deepEqual(bar(wiz).map(([id, label]) => [id, label]), [["ack:lift", "OK, continue"], ["repeat", "Repeat the movement"]]);
    // «Ripeti il movimento»: the cover goes back down by itself, then waits for ① again.
    await press("repeat");
    // The waiting screen of the movement back, its bar, Stop in its slot; no switched-off primary.
    assert.deepEqual([backend.session.phase, backend.session.samples, code(), title(), wiz().querySelector(".wiz-wait").textContent.includes(en.wiz_T683)],
      ["reclosing", {}, "C-40", en.wiz_T682, true]);
    assert.deepEqual(bar(wiz).map(([id]) => id), ["stop"]);
    assert.equal(wiz().querySelector('.wiz-actions [data-wiz="stop"]').disabled, false, "Stop stays while the cover moves");
    assert.deepEqual(steps(wiz)[3][2], ["Slat time: —"], "the repeated lift-off leaves the stepper: «—» again");
    backend.reclosed(); await tick();
    assert.deepEqual([backend.session.phase, code()], ["confirm_lift", "C-07"], "nothing starts by itself");
    assert.deepEqual(bar(wiz), [["run:open", "①  Start movement", false], ["lift", "②  Lifted off", true], ["stop", "Stop the cover", true]]);
    await press("run:open"); await move(backend, { seconds: 2.9 }); await press("lift");
    await press("ack:lift");
    // A-21b: the three times.
    assert.equal(code(), "A-21b");
    assert.deepEqual(tableRows(wiz), [["Opening time", "22.0 s", "14.2 s"], ["Closing time", "21.7 s", "14.0 s"], ["Slat time", "—", "2.9 s"]]);
    assert.equal(head(wiz), "Calibrate · Summary · 5 of 5");
    await press("save");
    assert.deepEqual(actions(), ["open", "endpoint", "close", "endpoint", "open", "lift", "repeat", "open", "lift", "save"]);
    assert.deepEqual([code(), head(wiz)], ["A-34", "Calibrate · Summary · 5 of 5"]);
  });

  test("a lift-off not shorter than both runs is refused in the run: Home Assistant's reason in the notice slot, once, through the close and at rest, until the run starts again", async () => {
    const context = await mount({ language: "it" });
    const { backend, wiz, code, press } = context;
    await toBasic(context);
    await press("begin");
    await runs(context, { opening: 14.2, closing: 2.0 });
    await press("ack:descent");
    await press("run:open"); await move(backend, { seconds: 2.5 });
    await press("lift");
    assert.deepEqual([backend.session.phase, backend.session.reason, backend.session.samples], ["reclosing", "lift_not_shorter", {}]);
    assert.deepEqual(errors(wiz), [it.calReason_lift_not_shorter], "said once (the action's refusal is the same sentence)");
    backend.reclosed(); await tick();
    assert.deepEqual([backend.session.phase, code()], ["confirm_lift", "C-07"]);
    assert.deepEqual(errors(wiz), [it.calReason_lift_not_shorter]);
    assert.ok(wiz().querySelector(".wiz-result-notices .wiz-notice[data-tone=error]"), "in the slot under the title");
    assert.deepEqual(bar(wiz), [["run:open", "①  Avvia il movimento", false], ["lift", "②  Si è staccato", true], ["stop", "Ferma la tapparella", true]]);
    await press("run:open");
    assert.deepEqual(errors(wiz), [], "a new start clears the reason");
  });

  test("a stop the bus reported in the lift-off run: «Si è staccato» gives way to «Ripeti il movimento» (the backend's repeat), which closes the cover again first", async () => {
    const context = await mount({ language: "it" });
    const { backend, wiz, press } = context;
    await toBasic(context);
    await press("begin");
    await runs(context);
    await press("ack:descent");
    await press("run:open"); await move(backend, { seconds: 3 });
    backend.busStop(); await tick();
    assert.deepEqual(bar(wiz), [["run:open", "①  Avvia il movimento", true], ["repeat", "Ripeti il movimento", false], ["stop", "Ferma la tapparella", true]]);
    await press("repeat");
    assert.equal(backend.session.phase, "reclosing");
    backend.reclosed(); await tick();
    assert.equal(backend.session.phase, "confirm_lift", "then ① again");
  });

  test("Stop during the lift-off run or its close ends the session as in the other runs", async () => {
    const context = await mount();
    const { backend, wiz, code, press } = context;
    await toBasic(context);
    await press("begin");
    await runs(context);
    await press("ack:descent");
    await press("run:open"); await move(backend, { seconds: 2.7 }); await press("lift");
    // No close after the lift-off; the close of a repeat is the one Stop can end.
    await press("repeat");
    await press("stop"); await tick(6);
    assert.deepEqual([backend.session.phase, backend.session.reason, code()], ["interrupted", "stopped", "S-01"]);
    assert.ok(wiz().querySelector(".wiz-steps [data-interrupted]") || wiz().querySelector(".wiz-steps[data-interrupted]"));
  });

  test("following another device the step «Lamelle» is inert: the reader's bar, Stop only while the cover moves", async () => {
    const session = { entry_id: "one", entity_id: "cover.living_one", session_id: "session-1", sequence: 5, revision: 7, mode: "guided", origin: "wizard",
      phase: "lifting", reason: null, values: { opening_time: 14.2, closing_time: 14 }, stop_requested: false, save_modes: ["new", "cover", "shared"], slats: true,
      samples: {}, lift_taken: false, reclose_seconds: null, _started: 0,
      screen: { code: "C-07", path: "base", scope: null, slats: true, local: { ackAscent: true, ackDescent: true } } };
    const { tab, wiz, code, backend } = await mount({ session, owner: false });
    tab().querySelector('[data-action="cal-tab-session"]').click(); await tick(8);
    assert.equal(code(), "C-07");
    assert.ok(wiz().hasAttribute("data-following"));
    assert.ok(wiz().querySelector(".wiz-readonly"));
    const stop = () => bar(wiz).map(([id, , disabled]) => [id, disabled]);
    assert.deepEqual(stop(), [["stop", false]]);
    assert.equal(head(wiz), "Follow · Slats · 5 of 6");
    backend.session.phase = "reclosing"; backend.emit(); await tick();
    assert.deepEqual(stop(), [["stop", false]]);
    backend.session.phase = "confirm_lift"; backend.emit(); await tick();
    assert.deepEqual(stop(), [["stop", true]], "at rest Stop stays in its slot, off");
  });
});

describe("«Ripeti» and the save", async () => {
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

  test("«Ripeti» on the descent opens the cover back by itself and repeats only the descent, the ascent kept; its text says it starts again with the cover open", async () => {
    const context = await mount({ language: "it" });
    const { backend, wiz, code, press, actions } = context;
    await toBasic(context, { slats: "yes" });
    await press("begin");
    await bothRuns(context, { opening: 14.2, closing: 16.5 });
    assert.equal(code(), "C-15");
    assert.equal(wiz().querySelector(".wiz-body").textContent, "Se hai premuto ② al momento giusto, continua. Altrimenti ripeti la corsa: si ricomincerà con la tapparella aperta.");
    await press("repeat:closing");
    const sent = backend.calls.filter((message) => message.action === "repeat").at(-1);
    assert.equal(sent.direction, "closing", "the descent only");
    // The waiting screen of the movement back up, then the descent's step as the first time.
    assert.deepEqual([backend.session.phase, backend.session.values, code(), wiz().querySelector(".wiz-title").textContent],
      ["reopening", { opening_time: 14.2 }, "C-40", it.wiz_T681]);
    assert.ok(wiz().querySelector(".wiz-wait").textContent.includes(it.wiz_T683));
    assert.deepEqual([...wiz().querySelectorAll(".wiz-actions .wiz-action")].map((button) => [button.dataset.wiz, button.disabled]), [["stop", false]]);
    backend.reopened(); await tick();
    assert.deepEqual([backend.session.phase, code()], ["confirm_open", "C-14"], "the descent's step again, from the top");
    await press("run:close"); await move(backend, { seconds: 14.0 }); await press("endpoint");
    assert.equal(code(), "C-15", "its result to acknowledge again");
    await press("ack:descent");
    assert.equal(code(), "C-07");
    assert.equal(backend.session.values.opening_time, 14.2, "the ascent kept");
    assert.ok(Math.abs(backend.session.values.closing_time - 14.0) < 1e-9, "the descent measured again");
    assert.deepEqual(actions().slice(0, 6), ["open", "endpoint", "close", "endpoint", "repeat", "close"], "no new ascent");
  });

  test("after «Si è staccato» the cover stays where it is: C-08b with the time and «Se il bordo si è alzato di 2–3 cm va bene»; «Ripeti il movimento» closes back, then ①", async () => {
    const context = await mount({ language: "it" });
    const { backend, wiz, code, press } = context;
    await toBasic(context, { slats: "yes" });
    await press("begin");
    await bothRuns(context);
    await press("ack:descent");
    await press("run:open"); await move(backend, { seconds: 2.6 }); await press("lift");
    assert.deepEqual([backend.session.phase, code()], ["review", "C-08b"], "no wait for a close back");
    assert.match(wiz().querySelector(".wiz-body").textContent, /Se il bordo si è alzato di 2–3 cm va bene\.$/);
    assert.deepEqual([...wiz().querySelectorAll(".wiz-results-block .cal-measure")].map((item) => item.querySelector(".cal-measure-number").textContent), ["2,6"]);
    await press("repeat");
    assert.deepEqual([backend.session.phase, wiz().querySelector(".wiz-title").textContent], ["reclosing", it.wiz_T682]);
    assert.deepEqual([...wiz().querySelectorAll(".wiz-actions .wiz-action")].map((button) => button.dataset.wiz), ["stop"], "no button more");
    backend.reclosed(); await tick();
    assert.equal(backend.session.phase, "confirm_lift");
    await press("run:open"); await move(backend, { seconds: 2.4 });
    assert.equal(wiz().querySelector('[data-wiz="lift"]').disabled, false, "«Si è staccato» ready again");
  });

  test("the basic calibration always saves into a profile: «Aggiorna il profilo» (its impact in yellow before Save, one press) or «Nuovo profilo»; no switch, no «Solo questa tapparella»; the profile card keeps its 16 px", async () => {
    const context = await mount({ overview: sourceOverview(), language: "it" });
    const { backend, wiz, code, press, tab } = context;
    backend.hass.states["cover.living_one"] = { state: "closed", attributes: { current_position: 0 } };
    row(tab, "cover.living_one").querySelector('.cal-menu [data-action="cal-tab-measure-cover"]').click(); await tick();
    const no = wiz().querySelector('input[name="slats"][value="no"]'); no.checked = true; no.dispatchEvent(new Event("change", { bubbles: true })); await tick();
    await press("begin");
    await bothRuns(context);
    await press("ack:descent");
    assert.equal(code(), "A-21b");
    assert.equal(wiz().querySelector('[name="save_profile"]'), null, "no switch: always a profile");
    assert.doesNotMatch(wiz().textContent, /Solo questa tapparella|Salva anche come profilo/);
    assert.deepEqual([...wiz().querySelectorAll('[name="save_choice"]')].map((input) => [input.value, input.disabled, input.checked]), [["update", false, true], ["new", false, false]],
      "on the profile's source: updating it, now available (no «non disponibile»)");
    assert.deepEqual([...wiz().querySelectorAll('[name="save_choice"]')].map((input) => input.closest("label").querySelector(".wiz-radio-label")?.textContent.trim() ?? ""),
      ["Aggiorna il profilo «tall_two_leaf»", "Nuovo profilo"]);
    assert.doesNotMatch(wiz().textContent, /prossima versione|non disponibile/i);
    // The preview asked once on arrival: who and what changes, in yellow under «Aggiorna», before any press.
    await tick(4);
    const previews = () => backend.calls.filter((message) => message.action === "preview_save").length;
    const option = (value) => wiz().querySelector(`[name="save_choice"][value="${value}"]`).closest("label");
    const yellow = () => option("update").querySelector(".cal-note.is-warning")?.textContent.replace(/\u00a0/g, " ") ?? null;
    assert.equal(previews(), 1, "asked once, on arrival");
    assert.equal(yellow(), "Cambiano anche i tempi delle 2 tapparelle che lo seguono: Kitchen shutter 2, Living room shutter 3.");
    assert.equal(option("update").querySelector("small.muted"), null, "the yellow notice in the grey line's place");
    assert.equal(wiz().querySelector(".wiz-result-notices").textContent, "", "nothing said at the top");
    // «Nuovo profilo»: the same note under «Aggiorna», same words, in grey; back on «Aggiorna», yellow again. One preview.
    const nw = wiz().querySelector('[name="save_choice"][value="new"]'); nw.checked = true; nw.dispatchEvent(new Event("change", { bubbles: true })); await tick(4);
    assert.equal(yellow(), null);
    assert.equal(option("update").querySelector(".cal-note.is-muted.wiz-option-note").textContent.replace(/\u00a0/g, " "), "Cambiano anche i tempi delle 2 tapparelle che lo seguono: Kitchen shutter 2, Living room shutter 3.");
    assert.equal(option("update").querySelector("small.muted"), null, "the same note, not another line");
    const up = wiz().querySelector('[name="save_choice"][value="update"]'); up.checked = true; up.dispatchEvent(new Event("change", { bubbles: true })); await tick(4);
    assert.equal(previews(), 1, "the preview kept: the choice does not change it");
    assert.match(yellow(), /^Cambiano anche i tempi delle 2 tapparelle/);
    // One press saves, with the preview's confirmation: no «Conferma e salva», no «Annulla».
    assert.equal(wiz().querySelector('[data-wiz="save"]').textContent, "Salva nel profilo «tall_two_leaf»");
    assert.equal(wiz().querySelector('[data-wiz="shared-cancel"]'), null);
    await press("save"); await tick(4);
    const save = backend.calls.filter((message) => message.action === "save").at(-1);
    assert.deepEqual([save.save_mode, save.confirmation], ["shared", "shared:session-1"]);
    assert.equal(previews(), 1, "Save asks nothing more");
    assert.equal(code(), "A-34");
    const css = (await import("node:fs")).readFileSync(new URL("../../custom_components/myhome/frontend/panel/panel-calibration.css", import.meta.url), "utf8");
    assert.match(css, /\.wiz \.wiz-profile-section \.cal-card > \.wiz-profile-block \{ box-sizing: border-box; padding: var\(--cal-sp-12\) var\(--cal-sp-16\) var\(--cal-sp-16\); \}/);
  });
});

describe("runs, end stops and the summary", async () => {
  const { translations } = await import("../../custom_components/myhome/frontend/panel/panel-translations.js");
  const it = translations.it;
  const i18n = translations;
  const bar = (wiz) => [...wiz().querySelectorAll(".wiz-actions .wiz-action")].map((button) => [button.dataset.wiz, button.textContent.trim(), button.disabled]);
  const moves = (context) => context.actions().filter((action) => ["open", "close", "next", "run"].includes(action));
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

  test("«Lamelle» has ① and ② as the ascent and descent; nothing moves on entering it, nor after «Ripeti»", async () => {
    const context = await mount({ language: "it" });
    const { backend, wiz, code, press } = context;
    await toBasic(context, { slats: "yes" });
    await press("begin");
    await bothRuns(context);
    await press("ack:descent");
    const before = moves(context);
    await tick(8);
    assert.deepEqual([backend.session.phase, code(), moves(context)], ["confirm_lift", "C-07", before], "no movement on entering the step");
    assert.deepEqual(bar(wiz), [["run:open", "①  Avvia il movimento", false], ["lift", "②  Si è staccato", true], ["stop", "Ferma la tapparella", true]]);
    await press("run:open");
    assert.deepEqual(bar(wiz).map(([id, , off]) => [id, off]), [["run:open", true], ["lift", true], ["stop", false]], "② on once the bus reports the start");
    await move(backend, { seconds: 2.6 });
    assert.deepEqual(bar(wiz).map(([id, , off]) => [id, off]), [["run:open", true], ["lift", false], ["stop", false]]);
    await press("lift");
    await press("repeat");
    backend.reclosed(); await tick(8);
    assert.deepEqual([backend.session.phase, moves(context).slice(before.length)], ["confirm_lift", ["open"]], "after the close back, ① again: no second start");
    assert.equal(wiz().querySelector('[data-wiz="run:open"]').disabled, false);
    assert.equal(it.wiz_T680, "②  Si è staccato");
  });

  test("«Ripeti» goes back through the waiting screen with its bar (the movement as title, Stop in its slot), then the step as the first time", async () => {
    const context = await mount({ language: "it" });
    const { backend, wiz, code, press } = context;
    await toBasic(context, { slats: "yes" });
    await press("begin");
    await bothRuns(context, { opening: 14.2, closing: 16.5 });
    await press("repeat:closing");
    const wait = () => wiz().querySelector(".wiz-wait");
    const progress = () => wait().querySelector(".wiz-progress");
    assert.deepEqual([code(), wiz().querySelector(".wiz-title").textContent, wait().querySelector("p").textContent],
      ["C-40", "La tapparella torna su", "La tapparella torna in posizione per ripetere il movimento."]);
    assert.equal(wiz().querySelector(".wiz-main .wiz-illustration"), null, "no drawing, as every positioning");
    assert.equal(progress().getAttribute("aria-valuenow"), "0", "known length (reopen_seconds), nothing run yet");
    assert.deepEqual(bar(wiz), [["stop", "Ferma la tapparella", false]]);
    assert.equal(wiz().querySelector(".wiz-steps [data-step-state=current]")?.textContent.includes("Discesa"), true, "the stepper stays on the descent");
    backend.backRunning(); backend.tick(9.0); backend.emit(); await tick();
    assert.equal(progress().getAttribute("aria-valuenow"), String(Math.round(9 / backend.session.reopen_seconds * 100)));
    backend.reopened(); await tick();
    assert.deepEqual([backend.session.phase, code()], ["confirm_open", "C-14"]);
    assert.deepEqual(bar(wiz).map(([id, , off]) => [id, off]), [["run:close", false], ["endpoint", true], ["stop", true]], "the descent as the first time");
    // The lift-off: the same screen going back down.
    await press("run:close"); await move(backend, { seconds: 16.0 }); await press("endpoint");
    await press("ack:descent");
    await press("run:open"); await move(backend, { seconds: 2.6 }); await press("lift");
    await press("repeat");
    assert.deepEqual([code(), wiz().querySelector(".wiz-title").textContent], ["C-40", "La tapparella torna giù"]);
    assert.equal(wiz().querySelector(".wiz-steps [data-step-state=current]")?.textContent.includes("Lamelle"), true);
  });

  test("without a profile the summary saves a new profile named after the cover (editable); no switch, no «Solo questa tapparella»; the box says it", async () => {
    const context = await mount({ language: "it" });
    const { backend, wiz, code, press } = context;
    await toBasic(context, { slats: "no" });
    await press("begin");
    await bothRuns(context);
    await press("ack:descent");
    assert.equal(code(), "A-21b");
    assert.equal(wiz().querySelector('[name="save_profile"]'), null);
    assert.equal(wiz().querySelector('[name="save_choice"]'), null, "nothing to update: only a new profile");
    assert.doesNotMatch(wiz().textContent, /Solo questa tapparella|Salva anche come profilo|solo su di lei/);
    const name = wiz().querySelector('[name="profile_name"]');
    assert.equal(name.value, "Living room shutter 1");
    assert.match(wiz().querySelector(".wiz-profile-block").textContent, /diventano un profilo, col nome della tapparella \(puoi cambiarlo qui sotto\)/);
    name.value = "hall_door"; name.dispatchEvent(new Event("input", { bubbles: true })); await tick();
    assert.equal(wiz().querySelector('[data-wiz="save"]').textContent, "Salva come profilo «hall_door»");
    await press("save");
    const save = backend.calls.filter((message) => message.action === "save").at(-1);
    assert.deepEqual([save.save_mode ?? "new", save.name], ["new", "hall_door"]);
  });

  test("a new profile is proposed with the cover's visible name, a readable variant when a profile has it, within 64 characters; the Save button says it", async () => {
    const { freeName } = await import("../../custom_components/myhome/frontend/panel/panel-calibration-screens.js");
    assert.equal(freeName("Living room shutter 1", []), "Living room shutter 1");
    assert.equal(freeName("Living room shutter 1", [{ name: "living room shutter 1" }]), "Living room shutter 1 (2)");
    assert.equal(freeName("Hall", [{ name: "Hall" }, { name: "Hall (2)" }]), "Hall (3)");
    const long = "x".repeat(70);
    assert.equal(freeName(long, []).length, 64);
    assert.equal(freeName(long, [{ name: "x".repeat(64) }]), `${"x".repeat(60)} (2)`);
    const data = sampleOverview();
    data.profiles.push({ id: "same", name: "Living room shutter 1", opening_time: 20, closing_time: 20, assigned_to: [], provenance: data.profiles[0].provenance });
    const context = await mount({ overview: data, language: "it" });
    await toBasic(context, { slats: "no" });
    await context.press("begin");
    await bothRuns(context);
    await context.press("ack:descent");
    assert.equal(context.wiz().querySelector('[name="profile_name"]').value, "Living room shutter 1 (2)");
    assert.equal(context.wiz().querySelector('[data-wiz="save"]').textContent, "Salva come profilo «Living room shutter 1 (2)»");
    assert.equal(context.wiz().querySelector('[data-wiz="save"]').disabled, false, "a free name: Save on");
  });

  test("«Lamelle» says it as the ascent and descent do: how to start with ①, then in bold to be ready for ② the moment the edge lifts off; IT and EN", async () => {
    assert.equal(i18n.it.wiz_T658, "Per iniziare la corsa dello stacco premi il pulsante\u00a0①.\n\n**Preparati a premere velocemente il pulsante ②** non appena il bordo inferiore della tapparella si stacca dall'appoggio.");
    assert.equal(i18n.en.wiz_T658, "To start the lift-off run, press button\u00a0①.\n\n**Get ready to press button ② quickly** as soon as the roller shutter's bottom edge lifts off the rest.");
    const context = await mount({ language: "it" });
    await toBasic(context, { slats: "yes" });
    await context.press("begin");
    await bothRuns(context);
    await context.press("ack:descent");
    const body = context.wiz().querySelector(".wiz-body");
    assert.equal(context.code(), "C-07");
    assert.equal(body.querySelector("strong").textContent, "Preparati a premere velocemente il pulsante ②");
    assert.match(body.textContent, /^Per iniziare la corsa dello stacco premi il pulsante\u00a0①\.\s*Preparati a premere velocemente il pulsante ② non appena il bordo inferiore della tapparella si stacca dall'appoggio\.$/);
  });

  // ---------------------------------------------------------------------------------------------------------------------
  // End stops, «Ripeti», and the summary's save and update of a profile.
  // ---------------------------------------------------------------------------------------------------------------------
  test("an actuator that stops by itself at the end stop ends the run: its result is valid, «Va bene, avanti» on", async () => {
    const context = await mount({ language: "it" });
    const { backend, wiz, code, press } = context;
    await toBasic(context, { slats: "no" });
    await press("begin");
    await press("run:open");
    backend.started(); backend.tick(18.3); backend.busStop(); await tick();
    assert.deepEqual([backend.session.phase, backend.session.values.opening_time, code()], ["confirm_open", 18.3, "C-12"]);
    assert.equal(wiz().querySelector(".wiz-result-notices .wiz-notice[data-tone=error]"), null, "not «non valida»");
    assert.equal(wiz().querySelector('[data-wiz="ack:ascent"]').disabled, false);
    await press("ack:ascent");
    await press("run:close");
    backend.started(); backend.tick(17.9); backend.busStop(); await tick();
    assert.deepEqual([backend.session.phase, code()], ["review", "C-15"]);
    assert.equal(wiz().querySelector('[data-wiz="ack:descent"]').disabled, false);
  });

  test("the basic calibration's session closes the cover itself first (its waiting screen), never trusting a «closed» kept by Home Assistant", async () => {
    const context = await mount({ language: "it", slowHome: true });
    const { backend, wiz, code, press } = context;
    await toBasic(context, { slats: "no" });
    await press("begin");
    assert.equal(backend.subscriptions.filter((message) => message.type === "myhome/cover_calibration/start").at(-1).home, true);
    assert.equal(backend.hass.services.some((call) => call.service === "close_cover"), false, "no close of the panel's own");
    // A positioning as the others (C-04).
    assert.deepEqual([backend.session.phase, code(), wiz().querySelector(".wiz-title").textContent], ["rehoming", "C-04", "La tapparella si sta posizionando"]);
    assert.ok(wiz().querySelector(".wiz-wait-meter .wiz-progress"));
    backend.rehomed(); await tick();
    assert.deepEqual([backend.session.phase, code()], ["confirm_closed", "C-11"]);
    assert.equal(wiz().querySelector('[data-wiz="run:open"]').disabled, false);
  });

  for (const direction of ["opening", "closing"]) {
    test(`a run that stops as it starts (${direction}) was already at its end stop: refused, said so, «Ripeti» brings it back by itself`, async () => {
      const context = await mount({ language: "it" });
      const { backend, wiz, code, press } = context;
      await toBasic(context, { slats: "no" });
      await press("begin");
      if (direction === "closing") {
        await press("run:open"); await move(backend, { seconds: 14.2 }); await press("endpoint"); await press("ack:ascent");
        await press("run:close");
      } else await press("run:open");
      backend.started(); backend.tick(0.3); backend.busStop(); await tick();
      assert.equal(backend.session.reason, "already_at_end");
      assert.equal(code(), direction === "opening" ? "C-12" : "C-15");
      assert.equal(wiz().querySelector(".wiz-result-notices .wiz-notice[data-tone=error]").textContent,
        direction === "opening" ? i18n.it.wiz_T693 : i18n.it.wiz_T694);
      assert.deepEqual([...wiz().querySelectorAll(".wiz-actions .wiz-action")].map((button) => button.dataset.wiz), [`repeat:${direction}`]);
      await press(`repeat:${direction}`);
      assert.equal(context.actions().at(-1), "repeat");
      assert.equal(backend.session.phase, direction === "opening" ? "rehoming" : "reopening");
      assert.equal(wiz().querySelector(".wiz-title").textContent, direction === "opening" ? "La tapparella torna giù" : "La tapparella torna su");
      if (direction === "opening") backend.rehomed(); else backend.reopened();
      await tick();
      assert.equal(code(), direction === "opening" ? "C-11" : "C-14");
      assert.equal(wiz().querySelector(`[data-wiz="${direction === "opening" ? "run:open" : "run:close"}"]`).disabled, false, "the run as the first time");
    });
  }

  test("«Ripeti» after the ascent brings the cover back down by itself (its waiting screen), then the ascent with ① ready; never the preparation again", async () => {
    const context = await mount({ language: "it" });
    const { backend, wiz, code, press } = context;
    await toBasic(context, { slats: "no" });
    await press("begin");
    await press("run:open"); await move(backend, { seconds: 14.2 }); await press("endpoint");
    assert.equal(code(), "C-12");
    await press("repeat:opening");
    assert.deepEqual([context.actions().at(-1), backend.session.phase, backend.session.values], ["repeat", "rehoming", {}]);
    assert.deepEqual([code(), wiz().querySelector(".wiz-title").textContent], ["C-40", "La tapparella torna giù"]);
    assert.equal(backend.subscriptions.filter((message) => message.type === "myhome/cover_calibration/start").length, 1, "the same session");
    backend.rehomed(); await tick();
    assert.deepEqual([code(), wiz().querySelector('[data-wiz="run:open"]').disabled], ["C-11", false]);
  });

  test("a refusal of «Aggiorna il profilo»'s preview is said in the summary's notices, in view, never silence; Save then saves nothing", async () => {
    const overview = sampleOverview();
    overview.profiles[0].provenance.opening.origin_entity_id = "cover.kitchen_two";  // «Aggiorna» on the source only.
    const context = await mount({ overview, language: "it" });
    const { backend, wiz, code, press } = context;
    backend.hass.states["cover.kitchen_two"] = { state: "closed", attributes: { current_position: 0 } };
    await context.startFor("cover.kitchen_two", "base");
    const no = wiz().querySelector('input[name="slats"][value="no"]'); if (no) { no.checked = true; no.dispatchEvent(new Event("change", { bubbles: true })); await tick(); }
    await press("begin");
    await bothRuns(context);
    const original = backend.hass.callWS, scrolled = [];
    const proto = dom.window.HTMLElement.prototype, saved = proto.scrollIntoView;
    proto.scrollIntoView = function () { scrolled.push(this); };
    backend.hass.callWS = async (message) => {
      if (message.action === "preview_save") { backend.calls.push(message); throw { code: "calibration_busy", message: "busy" }; }
      return original(message);
    };
    const error = () => wiz().querySelector(".wiz-result-notices .wiz-notice[data-tone=error], .wiz-notices .wiz-notice[data-tone=error]");
    try {
      // Asked on arrival, refused: said and brought into view.
      await press("ack:descent"); await tick(6);
      assert.equal(code(), "A-21b");
      assert.ok(wiz().querySelector('[name="save_choice"][value="update"]').checked);
      assert.ok(error(), "the refusal is said");
      assert.equal(error().textContent, i18n.it.profileError_calibration_busy);
      assert.ok(scrolled.some((element) => element.contains?.(error()) || element === error()), "brought into view");
      // Until a preview is back the yellow notice says who follows the profile.
      assert.match(wiz().querySelector(".wiz-profile-block .cal-note.is-warning").textContent.replace(/\u00a0/g, " "), /^Cambierà anche l’altra tapparella che lo segue: Living room shutter 3\.$/);
      // Save asks once more, refused again: nothing saved, the refusal said, the button as it was.
      await press("save"); await tick(6);
      assert.deepEqual(backend.calls.filter((message) => ["preview_save", "save"].includes(message.action)).map((message) => message.action), ["preview_save", "preview_save"]);
      assert.equal(error().textContent, i18n.it.profileError_calibration_busy);
      assert.equal(wiz().querySelector('[data-wiz="save"]').textContent, "Salva nel profilo «tall_two_leaf»");
      assert.equal(wiz().querySelector('[data-wiz="save"]').disabled, false);
      assert.equal(code(), "A-21b");
    } finally {
      backend.hass.callWS = original;
      proto.scrollIntoView = saved;
    }
  });

  test("«Aggiorna il profilo» with a lift-off measured shows the slat time it will save (not «=»)", async () => {
    const overview = sampleOverview();
    overview.profiles[0].provenance.opening.origin_entity_id = "cover.kitchen_two";  // «Aggiorna» on the source only.
    const context = await mount({ overview, language: "it" });
    const { backend, wiz, code, press } = context;
    backend.hass.states["cover.kitchen_two"] = { state: "closed", attributes: { current_position: 0 } };
    await context.startFor("cover.kitchen_two", "base");
    const yes = wiz().querySelector('input[name="slats"][value="yes"]'); yes.checked = true; yes.dispatchEvent(new Event("change", { bubbles: true })); await tick();
    await press("begin");
    await bothRuns(context);
    await press("ack:descent");
    await press("run:open"); await move(backend, { seconds: 2.9 }); await press("lift");
    await press("ack:lift");
    assert.equal(code(), "A-21b");
    const update = wiz().querySelector('[name="save_choice"][value="update"]');
    update.checked = true; update.dispatchEvent(new Event("change", { bubbles: true })); await tick();
    const row = [...wiz().querySelectorAll(".wiz-table tbody tr")].find((item) => item.textContent.startsWith("Tempo lamelle"));
    assert.match(row.textContent, /2,9 s/, "the lift-off just measured");
  });

  test("after «Aggiorna il profilo» the end says the profile was updated and who else uses it (never «non è cambiato»)", async () => {
    const data = sourceData();
    const context = await mount({ overview: data, language: "it" });
    const { backend, wiz, code, press } = context;
    backend.hass.states["cover.living_one"] = { state: "closed", attributes: { current_position: 0 } };
    await context.startFor("cover.living_one", "base");
    const radio = wiz().querySelector('input[name="slats"][value="no"]'); if (radio) { radio.checked = true; radio.dispatchEvent(new Event("change", { bubbles: true })); await tick(); }
    await press("begin");
    await bothRuns(context);
    await press("ack:descent");
    const update = wiz().querySelector('[name="save_choice"][value="update"]');
    update.checked = true; update.dispatchEvent(new Event("change", { bubbles: true })); await tick();
    await press("save"); await tick(4);
    if (wiz().querySelector('[data-wiz="save"]')) { await press("save"); await tick(4); }
    assert.equal(code(), "A-34");
    const body = wiz().querySelector(".wiz-body").textContent;
    assert.match(body, /^Il profilo «tall_two_leaf» è stato aggiornato con i valori misurati: lo usano già questa tapparella e le tapparelle Kitchen\sshutter\s2 e Living\sroom\sshutter\s3\./, "the others by name");
    assert.doesNotMatch(body, /non è cambiato/);
  });

  test("Ricalibra's summary on a cover fine-tuned with the tape shows the class it will save: «Calibrata», the rolls kept", async () => {
    const data = sampleOverview();
    data.covers.find((cover) => cover.entity_id === "cover.kitchen_two").precision = "refined";
    const context = await mount({ overview: data, language: "it" });
    const { backend, wiz, code, press } = context;
    backend.hass.states["cover.kitchen_two"] = { state: "closed", attributes: { current_position: 0 } };
    await context.startFor("cover.kitchen_two", "base");
    const radio = wiz().querySelector('input[name="slats"][value="no"]'); if (radio) { radio.checked = true; radio.dispatchEvent(new Event("change", { bubbles: true })); await tick(); }
    await press("begin");
    await bothRuns(context);
    await press("ack:descent");
    assert.equal(code(), "A-21b");
    const tile = wiz().querySelector(".wiz-results .cal-measure, .wiz-results-block .cal-measure");
    assert.match(tile.textContent, /Calibrata/);
    assert.doesNotMatch(tile.textContent, /Stima di base/);
  });

  test("while Home Assistant writes the store, Save says «Salvataggio…», off; once written, A-34", async () => {
    const context = await mount({ language: "it" });
    const { backend, wiz, code, press } = context;
    backend.hass.states["cover.kitchen_two"] = { state: "closed", attributes: { current_position: 0 } };
    await context.startFor("cover.kitchen_two", "base");
    const no = wiz().querySelector('input[name="slats"][value="no"]'); if (no) { no.checked = true; no.dispatchEvent(new Event("change", { bubbles: true })); await tick(); }
    await press("begin");
    await bothRuns(context);
    await press("ack:descent");
    assert.equal(code(), "A-21b");
    const save = () => wiz().querySelector('.wiz-actions [data-wiz="save"]');
    const label = save().textContent;
    assert.notEqual(label, i18n.it.saving);
    const original = backend.hass.callWS;
    let release = null;
    backend.hass.callWS = (message) => message.action === "save" ? new Promise((resolve) => { release = () => resolve(original(message)); }) : original(message);
    try {
      await press("save"); await tick(4);
      assert.ok(release, "the save is on its way");
      assert.deepEqual([save().textContent, save().disabled, save().classList.contains("is-settling")], ["Salvataggio…", true, true]);
      // The session's own «saving» (a reader's view of the owner's save) reads the same.
      backend.session.phase = "saving"; backend.emit(); await tick();
      assert.equal(save().textContent, "Salvataggio…");
      backend.session.phase = "review";
      release(); await tick(8);
      assert.equal(code(), "A-34");
    } finally {
      backend.hass.callWS = original;
    }
    assert.deepEqual([i18n.it.saving, i18n.en.saving], ["Salvataggio…", "Saving…"]);
  });

  test("the descent's ① right after «Avanti» is off (passing look) while a tap would be dropped, then on by itself, and a tap then starts the run", async () => {
    const context = await mount({ language: "it" });
    const { backend, wiz, code } = context;
    const wait = (ms) => new Promise((resolve) => setTimeout(resolve, ms));
    const button = (id) => wiz().querySelector(`.wiz-actions [data-wiz="${id}"]`);
    const tap = async (id) => { await wait(720); await tick(); assert.equal(button(id).disabled, false, id); button(id).click(); await tick(); };
    backend.hass.states["cover.living_one"] = { state: "closed", attributes: { current_position: 0 } };
    await context.startFor("cover.living_one", "base");
    const radio = wiz().querySelector('input[name="slats"][value="no"]');
    radio.checked = true; radio.dispatchEvent(new Event("change", { bubbles: true })); await tick();
    // The real hold from here on (the harness has none).
    context.panel._calibrationTab._wizard._guardMs = 700;
    await tap("begin");
    await tap("run:open"); await move(backend, { seconds: 14.2 }); await tap("endpoint");
    await tap("ack:ascent");
    assert.equal(code(), "C-14");
    // The tap on «Avanti» brought ①: within the hold it is off, with the passing look, and a tap does nothing.
    assert.deepEqual([button("run:close").disabled, button("run:close").classList.contains("is-settling")], [true, true]);
    button("run:close").click(); await tick();
    assert.equal(context.actions().includes("close"), false);
    await wait(720); await tick();
    assert.equal(button("run:close").disabled, false, "ready: on by itself");
    button("run:close").click(); await tick();
    assert.equal(context.actions().at(-1), "close");
  });

  test("the descent of a cover without slats says nothing about slats, at rest and while it runs; with slats as before", async () => {
    for (const slats of ["no", "yes"]) {
      const context = await mount({ language: "it" });
      const { backend, wiz, code, press } = context;
      await toBasic(context, { slats });
      await press("begin");
      await press("run:open"); await move(backend, { seconds: 14.2 }); await press("endpoint");
      if (slats === "yes") assert.equal(code(), "C-12");
      await press("ack:ascent");
      assert.equal(code(), "C-14");
      const body = () => wiz().querySelector(".wiz-body").textContent;
      if (slats === "no") {
        assert.doesNotMatch(body(), /lamell/i, "at rest");
        assert.match(body(), /scenderà fino in fondo e si fermerà da sola/);
      } else assert.match(body(), /lamelle comprese/);
      await press("run:close"); backend.started(); await tick();
      if (slats === "no") assert.doesNotMatch(body(), /lamell/i, "while it runs");
      else assert.match(body(), /le lamelle si chiudono ancora/);
      await reset();
    }
    assert.doesNotMatch(i18n.en.wiz_T699, /slat/i);
  });

  test("the lift-off's result says the cover goes back down and ① is pressed again, never that it starts by itself", () => {
    assert.equal(i18n.it.wiz_T664, "Se hai premuto nell'istante in cui il bordo si è staccato, continua al riepilogo. Altrimenti ripeti il movimento: la tapparella torna giù, poi premi di nuovo ①.");
    assert.equal(i18n.en.wiz_T664, "If you pressed the moment the edge lifted off, continue to the summary. Otherwise repeat the movement: the cover goes back down, then press ① again.");
  });

  test("the travel of the cloth takes 10–1000 cm in the wizard as on the card, and says so", async () => {
    assert.equal(i18n.it.wiz_T066, "In centimetri, da 10 a 1000. I decimali si scrivono con la virgola o con il punto.");
    assert.equal(i18n.en.wiz_T066, "In centimetres, from 10 to 1000. Enter decimals with a comma or a point.");
    assert.match(i18n.it.calTabTravelHelp, /tra 10 e 1000/);
    const { buildScreen } = await import("../../custom_components/myhome/frontend/panel/panel-calibration-screens.js");
    const t = (key) => i18n.it[key] ?? key;
    const field = buildScreen({ t, language: "it", stage: "session", path: "A", local: {}, cover: { entity_id: "cover.x", name: "X", effective: {}, followers: [] },
      view: { mode: "geometry", phase: "reading", step: "opening", slats: false, values: { opening_time: 14.2 }, samples: {}, readings: {}, owner: true, sequence: 3 } }).field;
    assert.deepEqual([field.kind, field.min, field.max], ["travel", 10, 1000]);
  });

  test("the cover a profile was measured on is never «una tapparella che segue»: its summary, the «Aggiorna» option, the confirmation, «Seguito da»", async () => {
    // Kitchen shutter 2 follows «tall_two_leaf», measured on Living room shutter 1 (assigned too) and followed by Living room shutter 3.
    let context = await mount({ overview: sourceData(), language: "it" });
    let { backend, wiz, code, press } = context;
    backend.hass.states["cover.kitchen_two"] = { state: "closed", attributes: { current_position: 0 } };
    await context.startFor("cover.kitchen_two", "base");
    let no = wiz().querySelector('input[name="slats"][value="no"]'); if (no) { no.checked = true; no.dispatchEvent(new Event("change", { bubbles: true })); await tick(); }
    await press("begin");
    await bothRuns(context);
    await press("ack:descent");
    assert.equal(code(), "A-21b");
    // A cover that follows a profile measured elsewhere does not update it: no choice, a profile of its own.
    assert.equal(wiz().querySelector('[name="save_choice"]'), null);
    const follower = wiz().querySelector(".wiz-frame").textContent.replace(/\u00a0/g, " ");
    assert.match(follower, /Questa tapparella ora usa un profilo suo; le altre restano sul profilo «tall_two_leaf»\./);
    assert.doesNotMatch(follower, /Questa tapparella segue il profilo|che lo segu/);
    await reset();

    // On the source itself: «Il profilo … è stato misurato su questa tapparella», never «Questa tapparella segue il profilo».
    context = await mount({ overview: sourceData(), language: "it" });
    ({ backend, wiz, code, press } = context);
    backend.hass.states["cover.living_one"] = { state: "closed", attributes: { current_position: 0 } };
    await context.startFor("cover.living_one", "base");
    no = wiz().querySelector('input[name="slats"][value="no"]'); if (no) { no.checked = true; no.dispatchEvent(new Event("change", { bubbles: true })); await tick(); }
    await press("begin");
    await bothRuns(context);
    await press("ack:descent");
    const text = wiz().querySelector(".wiz-frame").textContent.replace(/\u00a0/g, " ");
    assert.match(text, /Il profilo «tall_two_leaf» è stato misurato su questa tapparella/);
    assert.doesNotMatch(text, /Questa tapparella segue il profilo/);
    await reset();

    // «Cambia profilo»: «Seguito da» lists the followers only.
    context = await mount({ overview: sourceData(), language: "it" });
    const shown = await page(context, "cover.living_three");
    shown.querySelector('.cal-page-link [data-action="cal-tab-assign"]').click(); await tick(6);
    const sheet = context.tab().querySelector(".cal-assign-page");
    const row = sheet.querySelector('input[name="assign_profile"][value="tall"]').closest("label");
    assert.equal(row.querySelector(".cal-assign-followers").textContent.replace(/\u00a0/g, " "), "Seguito da questa tapparella e dalla tapparella Kitchen shutter 2", "the sheet's own cover is «questa tapparella»");
    assert.equal(i18n.en.wiz_T702, "The profile \"{profile}\" was measured on this cover: the values measured can update it, or become a new profile.");
  });

  test("«Aggiorna»'s impact names the covers as the panel shows them, never by entity id", async () => {
    // From the source, the only cover that updates its profile.
    const context = await mount({ overview: sourceData(), language: "it" });
    const { backend, wiz, code, press } = context;
    backend.hass.states["cover.living_one"] = { state: "closed", attributes: { current_position: 0 } };
    await context.startFor("cover.living_one", "base");
    const no = wiz().querySelector('input[name="slats"][value="no"]'); if (no) { no.checked = true; no.dispatchEvent(new Event("change", { bubbles: true })); await tick(); }
    await press("begin");
    await bothRuns(context);
    // Home Assistant's preview names a cover by its entity id (no name of its own in the store).
    const original = backend.hass.callWS;
    backend.hass.callWS = async (message) => {
      const result = await original(message);
      if (message.action === "preview_save") for (const item of result.save_preview.followers) item.name = item.entity_id;
      return result;
    };
    try {
      await press("ack:descent"); await tick(4);
    } finally { backend.hass.callWS = original; }
    assert.equal(code(), "A-21b");
    assert.ok(wiz().querySelector('[name="save_choice"][value="update"]').checked, "updating is the default on the source");
    const said = wiz().querySelector(".wiz-profile-block .cal-note.is-warning").textContent.replace(/\u00a0/g, " ");
    assert.equal(said, "Cambiano anche i tempi delle 2 tapparelle che lo seguono: Kitchen shutter 2, Living room shutter 3.");
    assert.doesNotMatch(said, /cover\./);
  });

  test("with no other cover on the profile no yellow notice (its grey line as before), and «Salva nel profilo» saves at once with the preview's confirmation", async () => {
    const data = sampleOverview();
    Object.assign(data.profiles[0], { assigned_to: ["cover.kitchen_two"] });
    data.profiles[0].provenance.opening.origin_entity_id = "cover.kitchen_two";
    data.covers[2].profile_id = null;
    const context = await mount({ overview: data, language: "it" });
    const { backend, wiz, code, press } = context;
    backend.hass.states["cover.kitchen_two"] = { state: "closed", attributes: { current_position: 0 } };
    await context.startFor("cover.kitchen_two", "base");
    const no = wiz().querySelector('input[name="slats"][value="no"]'); if (no) { no.checked = true; no.dispatchEvent(new Event("change", { bubbles: true })); await tick(); }
    await press("begin");
    await bothRuns(context);
    await press("ack:descent"); await tick(4);
    assert.equal(code(), "A-21b");
    const update = wiz().querySelector('[name="save_choice"][value="update"]');
    assert.ok(update.checked);
    assert.equal(update.closest("label").querySelector(".cal-note.is-warning"), null, "no yellow notice: no other cover changes");
    assert.equal(update.closest("label").querySelector(".cal-note.is-muted").textContent, "Nessun’altra tapparella lo segue.");
    assert.match(wiz().querySelector('.wiz-actions [data-wiz="save"]').textContent, /Salva nel profilo/);
    await press("save"); await tick(6);
    const sent = backend.calls.filter((message) => ["preview_save", "save"].includes(message.action)).map((message) => [message.action, message.save_mode, message.confirmation ?? null]);
    assert.deepEqual(sent, [["preview_save", "shared", null], ["save", "shared", sent[1]?.[2]]]);
    assert.ok(sent[1][2], "the preview's confirmation carried");
    assert.equal(code(), "A-34");
    assert.equal(wiz().querySelector('[data-wiz="shared-cancel"]'), null);
  });
});

describe("Stop, the close and who ended a run", async () => {
  const { buildScreen, IN_MOTION } = await import("../../custom_components/myhome/frontend/panel/panel-calibration-screens.js");
  const { CONFIRM_GUARD_MS } = await import("../../custom_components/myhome/frontend/panel/panel-calibration-wizard.js");
  const { fill } = await import("../../custom_components/myhome/frontend/panel/panel-calibration-text.js");
  const it = translations.it;
  const t = (key) => it[key] || key;
  const cover = { entity_id: "cover.hall_window", name: "Hall window", effective: { closing: { value: 21.7, origin: "guided" }, opening: { value: 22.4, origin: "guided" },
    slat_time_s: { value: 2.7 } }, travel_cm: 160, followers: [] };
  const owned = { owner: true, read_only: false, sequence: 7, reason: null, stop_requested: false };
  const guided = { ...owned, mode: "guided", origin: "wizard", slats: true, values: {}, samples: {}, lift_taken: false, back_stage: "running", elapsed: 1 };
  const measured = { values: { opening_time: 22.4, closing_time: 21.7 } };
  const geometry = { ...owned, mode: "geometry", slats: true, keep_times: true, auto_lift: true, saved_slat_s: 2.7, values: { opening_time: 22.4, closing_time: 21.7 },
    samples: {}, readings: {}, geometry: {}, travel_cm: null, saved_travel_cm: 160, elapsed: 1 };
  const fineTuned = { samples: { lift: 2.7 }, readings: { gap: 0.4 }, travel_cm: 160 };
  /**
   * Safety: every movement the session makes, by itself or after ①, with the red Stop on. The inventory of the
   * movements the session makes by itself: the close before the first ascent (`home`) and before a repeated one, the
   * open back before a repeated descent, the close back before a repeated lift-off
   * (or after one refused), Perfeziona's lift-off run by itself (the close to the rest, the pause, the rise for the saved
   * slat time), the runs before the readings and the check (to an end stop, half the time, the check's own), and the wait
   * for the bus to confirm a Stop.
   */
  const MOVEMENTS = [
    ["Base: the close before the first ascent, its Stop being written", "base", { ...guided, phase: "rehoming", back_stage: "stopping", rehome_seconds: 27.5, elapsed: null }],
    ["Base: the close before the first ascent, running", "base", { ...guided, phase: "rehoming", rehome_seconds: 27.5 }],
    ["Base: the close before a repeated ascent", "base", { ...guided, phase: "rehoming", rehome_seconds: 27.5 }, { rehomeRepeat: true }],
    ["Base: the open back before a repeated descent", "base", { ...guided, ...measured, phase: "reopening", reopen_seconds: 28 }, { ackAscent: true }],
    ["Base: the close back before a repeated lift-off", "base", { ...guided, ...measured, phase: "reclosing", reclose_seconds: 8 }, { ackAscent: true, ackDescent: true }],
    ["Base: the ascent", "base", { ...guided, phase: "opening", back_stage: null }],
    ["Base: the descent", "base", { ...guided, phase: "closing", back_stage: null, values: { opening_time: 22.4 } }, { ackAscent: true }],
    ["Base: the lift-off run", "base", { ...guided, ...measured, phase: "lifting", back_stage: null }, { ackAscent: true, ackDescent: true }],
    ["Perfeziona: the close to the rest before the lift-off (1 of 2)", "A", { ...geometry, step: "home", phase: "closing", lift_pair: 1, planned_seconds: 25.7 }, { autoStart: "home" }],
    ["Perfeziona: the pause at the rest", "A", { ...geometry, step: "lift", phase: "settling", lift_pair: 2 }],
    ["Perfeziona: the rise for the saved slat time (2 of 2)", "A", { ...geometry, step: "lift", phase: "opening", lift_pair: 2, planned_seconds: 2.7 }],
    ["Perfeziona: up to the top for the travel", "A", { ...geometry, ...fineTuned, travel_cm: null, step: "opening", phase: "opening" }, { autoStart: "opening" }],
    ["Perfeziona: half the time down", "A", { ...geometry, ...fineTuned, step: "half_close", phase: "closing", planned_seconds: 9.5 }, { ackReadings: true, autoStart: "half_close" }],
    ["Perfeziona: down to the bottom", "A", { ...geometry, ...fineTuned, step: "closing", phase: "closing" }, { ackReadings: true, autoStart: "closing" }],
    ["Perfeziona: half the time up", "A", { ...geometry, ...fineTuned, step: "half_open", phase: "opening", planned_seconds: 12.5 }, { ackReadings: true, autoStart: "half_open" }],
    ["Perfeziona: the check's way to its end stop", "A", { ...geometry, ...fineTuned, step: "top", phase: "opening",
      check: { direction: "closing", target_cm: 80, planned_seconds: 10.4 } }, { ackReadings: true, autoStart: "top" }],
    ["Perfeziona: the check's run", "A", { ...geometry, ...fineTuned, step: "check", phase: "closing", planned_seconds: 10.4,
      check: { direction: "closing", target_cm: 80, planned_seconds: 10.4 } }, { ackReadings: true, autoStart: "check" }],
    // The runs that start by themselves right after an arrival the actuator reported.
    ["Perfeziona: half the time up, right after the arrival at the bottom", "A", { ...geometry, ...fineTuned, step: "half_open", phase: "starting_open", planned_seconds: 12.5 },
      { ackReadings: true, autoStart: "half_open" }],
    ["Perfeziona: the check's run, right after the arrival at the top", "A", { ...geometry, ...fineTuned, step: "check", phase: "starting_close", planned_seconds: 10.4,
      check: { direction: "closing", target_cm: 80, planned_seconds: 10.4 } }, { ackReadings: true, autoStart: "check" }],
    ["Perfeziona: the bus confirming the session's Stop", "A", { ...geometry, ...fineTuned, step: "half_close", phase: "geometry_wait_stop" }, { ackReadings: true }],
  ];
  /** Living room shutter 1 (never calibrated): the basic calibration from A-02, the slats answer given. */
  async function toBasic(context, { slats = "no" } = {}) {
    const { backend, startFor, wiz } = context;
    backend.hass.states["cover.living_one"] = { state: "closed", attributes: { current_position: 0 } };
    await startFor("cover.living_one", "base");
    const radio = wiz().querySelector(`input[name="slats"][value="${slats}"]`);
    radio.checked = true; radio.dispatchEvent(new Event("change", { bubbles: true })); await tick();
  }
  const meter = (wiz) => Number(wiz().querySelector(".wiz-wait-meter .wiz-progress").getAttribute("aria-valuenow"));
  const current = (wiz) => wiz().querySelector(".wiz-steps [data-step-state=current]")?.textContent ?? "";

  test("«Ferma la tapparella» is on in every movement of the session, for the owner and for a reader", () => {
    for (const [label, path, view, local = {}] of MOVEMENTS) {
      for (const reader of [false, true]) {
        const shown = reader ? { ...view, owner: false, read_only: true } : view;
        assert.ok(IN_MOTION(shown), `${label}: a movement`);
        const screen = buildScreen({ t, language: "it", stage: "session", path, local, cover, view: shown, elapsed: shown.elapsed, previews: false });
        const stop = screen.actions.find((action) => action.id === "stop");
        assert.ok(stop, `${label}${reader ? " (reader)" : ""}: Stop drawn on ${screen.code}`);
        assert.equal(stop.disabled ?? false, false, `${label}${reader ? " (reader)" : ""}: Stop on, on ${screen.code}`);
      }
    }
  });

  test("the close before the first ascent is a positioning: its waiting screen, its bar filling, Stop on; then the ascent", async () => {
    const context = await mount({ language: "it", slowHome: true });
    const { backend, wiz, code, press } = context;
    await toBasic(context);
    await press("begin");
    assert.deepEqual([backend.session.phase, code(), wiz().querySelector(".wiz-title").textContent], ["rehoming", "C-04", it.wiz_T027]);
    assert.equal(wiz().querySelector(".wiz-wait-lead").textContent, "Questa tapparella viene chiusa completamente");
    assert.equal(wiz().querySelector(".wiz-wait-note").textContent, it.wiz_T605);
    assert.match(current(wiz), /Preparazione/, "not yet the ascent");
    assert.equal(wiz().querySelector('[data-wiz="run:open"]'), null, "no ① while it closes");
    assert.equal(wiz().querySelector('[data-wiz="stop"]').disabled, false);
    backend.backRunning(); backend.tick(11); backend.emit(); await tick();
    assert.equal(meter(wiz), Math.round(11 / 27.5 * 100), "the bar fills on Home Assistant's rehome_seconds");
    assert.equal(wiz().querySelector('[data-wiz="stop"]').disabled, false);
    backend.rehomed(); await tick();
    assert.deepEqual([code(), wiz().querySelector('[data-wiz="run:open"]').disabled], ["C-11", false]);
    assert.match(current(wiz), /Salita/);
  });

  test("the close before a repeated ascent fills its bar too", async () => {
    const context = await mount({ language: "it" });
    const { backend, wiz, code, press } = context;
    await toBasic(context);
    await press("begin");
    await press("run:open"); backend.started(); backend.tick(14.2); await press("endpoint");
    await press("repeat:opening");
    assert.deepEqual([backend.session.phase, code(), wiz().querySelector(".wiz-title").textContent], ["rehoming", "C-40", it.wiz_T682]);
    backend.backRunning(); backend.tick(5.5); backend.emit(); await tick();
    assert.equal(meter(wiz), 20);
    assert.equal(wiz().querySelector('[data-wiz="stop"]').disabled, false);
  });

  test("a full run the actuator's own stop ended says so, never «Se hai premuto ② al momento giusto»", async () => {
    const context = await mount({ language: "it" });
    const { backend, wiz, code, press } = context;
    await toBasic(context);
    await press("begin");
    await press("run:open");
    backend.started(); backend.tick(18.3); backend.busStop(); await tick();
    assert.deepEqual([code(), backend.session.ended_by], ["C-12", { opening: "bus_stop" }]);
    assert.equal(wiz().querySelector(".wiz-body").textContent, it.wiz_T706);
    assert.doesNotMatch(wiz().textContent, /al momento giusto/);
    await press("ack:ascent");
    await press("run:close");
    backend.started(); backend.tick(17.8); backend.busStop(); await tick();
    assert.equal(code(), "C-15");
    assert.equal(wiz().querySelector(".wiz-body").textContent, it.wiz_T707);
    assert.doesNotMatch(wiz().textContent, /al momento giusto/);
  });

  test("a run ended by ② keeps its text", async () => {
    const context = await mount({ language: "it" });
    const { backend, wiz, code, press } = context;
    await toBasic(context);
    await press("begin");
    await press("run:open"); backend.started(); backend.tick(18.1); await press("endpoint");
    assert.deepEqual([code(), wiz().querySelector(".wiz-body").textContent], ["C-12", it.wiz_T478]);
  });

  test("the texts of a run the actuator ended, in Italian and in English", () => {
    assert.equal(it.wiz_T706, "La tapparella si è fermata da sola in alto: il tempo è quello segnalato dall'attuatore. Se è arrivata fino in cima, continua. Altrimenti ripeti la corsa: si ricomincerà con la tapparella chiusa.");
    assert.equal(translations.en.wiz_T707, "The cover stopped by itself at the bottom: the time is the one the actuator reported. If it reached the bottom, continue. Otherwise repeat the run: it will start again with the cover open.");
  });

  test("① pressed at once after «Ripeti il movimento» closed the cover back starts the lift-off run (no hold left by that press)", async () => {
    const context = await mount({ language: "it" });
    const { backend, panel, wiz, code, press, actions } = context;
    backend.hass.states["cover.living_one"] = { state: "closed", attributes: { current_position: 0 } };
    await context.startFor("cover.living_one", "base");
    await press("begin");
    await press("run:open"); await move(backend, { seconds: 14.2 }); await press("endpoint");
    await press("ack:ascent");
    await press("run:close"); await move(backend, { seconds: 14 }); await press("endpoint");
    await press("ack:descent");
    await press("run:open"); await move(backend, { seconds: 2.7 }); await press("lift");
    assert.equal(code(), "C-08b");
    // The panel's own hold after a change of the bar (CONFIRM_GUARD_MS), as in Home Assistant.
    const wizard = panel._calibrationTab._wizard;
    wizard._guardMs = CONFIRM_GUARD_MS;
    const tap = (id) => { const button = wiz().querySelector(`.wiz-frame [data-wiz="${id}"]`); button.dispatchEvent(new window.MouseEvent("mousedown", { bubbles: true, composed: true })); button.click(); };
    tap("repeat"); await tick();
    assert.deepEqual([backend.session.phase, code()], ["reclosing", "C-40"]);
    backend.backRunning(); await tick();
    await new Promise((resolve) => setTimeout(resolve, CONFIRM_GUARD_MS + 50));
    // Closed back by the session itself: ① on, a change by data alone. Pressed at once (within the hold, by a click whose
    // press the page did not see begin, as the trial's), it starts the run.
    backend.reclosed(); await tick();
    const button = wiz().querySelector('.wiz-frame [data-wiz="run:open"]');
    assert.deepEqual([backend.session.phase, button.disabled], ["confirm_lift", false]);
    const sent = actions().length;
    button.click(); await tick();
    assert.deepEqual(actions().slice(sent), ["open"], "the first press sends ①");
    assert.equal(backend.session.phase, "starting_lift");
  });

  test("after «Aggiorna il profilo» from its source the end names the other cover that uses it", () => {
    const cover = { name: "_Fake_13", entity_id: "cover.fake_13", profile: { name: "fake_11" }, followers: ["_Fake_11"], effective: {} };
    const screen = buildScreen({ t, language: "it", path: "base", stage: "saved", cover, followerNames: "_Fake_11",
      local: { saved: { target: "profile_update" } } });
    assert.match(screen.body, /^Il profilo «fake_11» è stato aggiornato con i valori misurati: lo usano già questa tapparella e la tapparella _Fake_11\./);
    assert.equal(fill(translations.en.wiz_T697, { profile: "p", cover: "A", names: "B and C" }), 'The profile "p" was updated with the measured values: this cover and the covers B and C already use it.');
  });
});

describe("updating the profile from its source", async () => {
  const { translations: i18n } = await import("../../custom_components/myhome/frontend/panel/panel-translations.js");
  /** «tall_two_leaf» measured on Living room shutter 1, followed by Kitchen shutter 2 (checked) and Living room shutter 3. */
  function sourceData({ origin = "cover.living_one" } = {}) {
    const data = sampleOverview();
    const profile = data.profiles[0];
    profile.assigned_to = ["cover.living_one", "cover.kitchen_two", "cover.living_three"];
    profile.provenance.opening.origin_entity_id = origin;
    if (!origin) profile.provenance.opening = { ...profile.provenance.opening, source: "manual", origin_name: null };
    const [one, two] = data.covers;
    Object.assign(one, { profile_id: "tall", travel_cm: 198 });
    two.precision = "verified";
    return data;
  }
  /** The basic calibration without slats of `entity`, both runs made: its summary (A-21b). */
  async function summary(context, entity) {
    const { backend, wiz, press, startFor } = context;
    backend.hass.states[entity] = { state: "closed", attributes: { current_position: 0 } };
    await startFor(entity, "base");
    const no = wiz().querySelector('input[name="slats"][value="no"]');
    no.checked = true; no.dispatchEvent(new Event("change", { bubbles: true })); await tick();
    await press("begin");
    await press("run:open"); await move(backend, { seconds: 14.2 }); await press("endpoint");
    await press("ack:ascent");
    await press("run:close"); await move(backend, { seconds: 14.0 }); await press("endpoint");
    await press("ack:descent");
    assert.equal(context.code(), "A-21b");
  }
  const text = (wiz) => wiz().querySelector(".wiz-frame").textContent.replace(/ /g, " ");
  const saves = (backend) => backend.calls.filter((message) => ["preview_save", "save"].includes(message.action))
    .map((message) => [message.action, message.save_mode ?? null, message.name ?? null]);

  /** The yellow notice under «Aggiorna il profilo «X»» (null when there is none), and the grey line under it. */
  const yellow = (wiz) => wiz().querySelector('[name="save_choice"][value="update"]').closest("label").querySelector(".cal-note.is-warning")?.textContent.replace(/\u00a0/g, " ") ?? null;
  const grey = (wiz) => wiz().querySelector('[name="save_choice"][value="update"]').closest("label").querySelector(".cal-note.is-muted")?.textContent.replace(/\u00a0/g, " ") ?? null;
  const previews = (backend) => saves(backend).filter(([action]) => action === "preview_save").length;

  test("on the source «Aggiorna il profilo» is the default, «Nuovo profilo» the other choice; the yellow notice under it names, before Save, whose times change and, apart, who loses the check; one press saves", async () => {
    const context = await mount({ overview: sourceData(), language: "it" });
    const { backend, wiz, press } = context;
    await summary(context, "cover.living_one"); await tick(4);
    const options = [...wiz().querySelectorAll('[name="save_choice"]')];
    assert.deepEqual(options.map((option) => [option.value, option.checked]), [["update", true], ["new", false]]);
    assert.match(options[0].closest("label").textContent, /Aggiorna il profilo «tall_two_leaf»/);
    assert.match(options[1].closest("label").textContent, /Nuovo profilo/);
    assert.deepEqual(saves(backend).map(([action, mode]) => [action, mode]), [["preview_save", "shared"]], "asked on arrival, once");
    assert.equal(yellow(wiz), "Cambiano anche i tempi delle 2 tapparelle che lo seguono: Kitchen shutter 2, Living room shutter 3. Perderà la verifica la tapparella Kitchen shutter 2.");
    assert.equal(grey(wiz), null);
    assert.equal(wiz().querySelectorAll(".wiz-notices .wiz-notice, .wiz-result-notices .wiz-notice").length, 0, "no notice at the top");
    assert.deepEqual([...wiz().querySelectorAll(".wiz-actions .wiz-action")].map((button) => [button.dataset.wiz, button.textContent]), [["save", "Salva nel profilo «tall_two_leaf»"]],
      "no «Conferma e salva», no «Annulla»");
    await press("save"); await tick(6);
    assert.deepEqual(saves(backend).map(([action, mode]) => [action, mode]), [["preview_save", "shared"], ["save", "shared"]], "saved at the first press");
    assert.equal(backend.calls.find((message) => message.action === "save").confirmation, "shared:session-1");
    assert.equal(context.code(), "A-34");
  });

  /**
   * Home Assistant's preview of the update, reshaped for each follower (`shape(item)`): the fake changes every time
   * and keeps the check of a verified cover, so each case says what it changes. Returns the restore.
   */
  function previewAs(backend, shape) {
    const original = backend.hass.callWS;
    backend.hass.callWS = async (message) => {
      const result = await original(message);
      if (message.action === "preview_save") for (const item of result.save_preview.followers) shape(item);
      return result;
    };
    return () => { backend.hass.callWS = original; };
  }
  const same = { opening: { before: 22, after: 22, scaled: false, overridden: false }, closing: { before: 21.6, after: 21.6, scaled: false, overridden: false } };
  const geometry = (roll) => ({ slat_time_s: { before: 3, after: 3, scaled: false }, opening_roll: { before: 1, after: roll, scaled: false },
    closing_roll: { before: 1, after: 1, scaled: false } });

  test("times the same but the intermediate positions changed (the roll, or the model), no check saved: said in yellow before Save, naming them; one press saves", async () => {
    const context = await mount({ overview: sourceData(), language: "it" });
    const { backend, wiz, press } = context;
    // Kitchen shutter 2: a roll changes; Living room shutter 3: the model only.
    const restore = previewAs(backend, (item) => Object.assign(item, { changes: structuredClone(same), loses_check: false },
      item.entity_id === "cover.living_three" ? { model_before: "linear_time", model_after: "slat_roll", geometry_changes: geometry(1) }
        : { model_before: "slat_roll", model_after: "slat_roll", geometry_changes: geometry(1.4) }));
    try {
      await summary(context, "cover.living_one"); await tick(4);
    } finally { restore(); }
    // The geometry alone changes (the maintainer's #18, point 2): its impact is in view before the press.
    assert.equal(yellow(wiz), "Cambiano anche le posizioni intermedie delle 2 tapparelle che lo seguono: Kitchen shutter 2, Living room shutter 3.");
    assert.deepEqual(saves(backend).map(([action]) => action), ["preview_save"], "nothing saved before the press");
    await press("save"); await tick(6);
    assert.deepEqual(saves(backend).map(([action, mode]) => [action, mode]), [["preview_save", "shared"], ["save", "shared"]], "saved at the first press");
  });

  test("the geometry listed but the same, times the same, no check saved: no yellow (the grey line as before), «Salva nel profilo» saves at once", async () => {
    const context = await mount({ overview: sourceData(), language: "it" });
    const { backend, wiz, press } = context;
    const restore = previewAs(backend, (item) => Object.assign(item, { changes: structuredClone(same), loses_check: false,
      model_before: "slat_roll", model_after: "slat_roll", geometry_changes: geometry(1) }));
    try {
      await summary(context, "cover.living_one"); await tick(4);
    } finally { restore(); }
    assert.equal(yellow(wiz), null, "nothing changes for the others");
    assert.equal(grey(wiz), "Cambierà anche le 2 tapparelle che lo seguono: Kitchen shutter 2 e Living room shutter 3.");
    await press("save"); await tick(6);
    assert.deepEqual(saves(backend).map(([action, mode]) => [action, mode]), [["preview_save", "shared"], ["save", "shared"]]);
    assert.equal(wiz()?.querySelector('[data-wiz="shared-cancel"]') ?? null, null, "no confirmation");
  });

  test("times and intermediate positions both changed: one sentence for both, the cover not named twice", async () => {
    const context = await mount({ overview: sourceData(), language: "it" });
    const { backend, wiz } = context;
    const restore = previewAs(backend, (item) => Object.assign(item, { loses_check: false,
      model_before: "slat_roll", model_after: "slat_roll", geometry_changes: geometry(1.4) }));
    try {
      await summary(context, "cover.living_one"); await tick(4);
    } finally { restore(); }
    assert.deepEqual(saves(backend).map(([action]) => action), ["preview_save"]);
    assert.equal(yellow(wiz), "Cambiano anche i tempi e le posizioni intermedie delle 2 tapparelle che lo seguono: Kitchen shutter 2, Living room shutter 3.");
  });

  test("Save pressed before the preview is back: «Salvataggio…» while it comes (the yellow notice on who), then saved with its confirmation, no second press", async () => {
    const context = await mount({ overview: sourceData(), language: "it" });
    const { backend, wiz, press } = context;
    const original = backend.hass.callWS, held = [];
    backend.hass.callWS = async (message) => {
      if (message.action === "preview_save") await new Promise((resolve) => held.push(resolve));
      return original(message);
    };
    const button = () => wiz().querySelector('.wiz-actions [data-wiz="save"]');
    try {
      await summary(context, "cover.living_one"); await tick(4);
      assert.equal(held.length, 1, "asked on arrival, not answered yet");
      assert.equal(yellow(wiz), "Cambierà anche le 2 tapparelle che lo seguono: Kitchen shutter 2 e Living room shutter 3.", "who, until the preview is back");
      assert.deepEqual([button().textContent, button().disabled], ["Salva nel profilo «tall_two_leaf»", false], "Save is not held by the preview");
      await press("save"); await tick(4);
      assert.equal(held.length, 1, "the press waits for the same preview");
      assert.deepEqual([button().textContent, button().disabled], ["Salvataggio…", true]);
      assert.equal(backend.calls.some((message) => message.action === "save"), false, "nothing saved yet");
      held.splice(0).forEach((release) => release()); await tick(8);
    } finally { backend.hass.callWS = original; }
    assert.deepEqual(saves(backend).map(([action, mode]) => [action, mode]), [["preview_save", "shared"], ["save", "shared"]]);
    assert.equal(backend.calls.find((message) => message.action === "save").confirmation, "shared:session-1");
    assert.equal(context.code(), "A-34");
  });

  test("a new view of the session (new values, a check repeated) makes the preview stale: asked again, the yellow notice on who until it is back", async () => {
    const context = await mount({ overview: sourceData(), language: "it" });
    const { backend, wiz } = context;
    await summary(context, "cover.living_one"); await tick(4);
    assert.equal(previews(backend), 1);
    assert.match(yellow(wiz), /^Cambiano anche i tempi delle 2 tapparelle/);
    backend.emit(); await tick(6);
    assert.equal(previews(backend), 2, "asked again for the new view");
    assert.match(yellow(wiz), /^Cambiano anche i tempi delle 2 tapparelle/);
    // The same view drawn again asks nothing.
    const nw = wiz().querySelector('[name="save_choice"][value="new"]'); nw.checked = true; nw.dispatchEvent(new Event("change", { bubbles: true })); await tick(4);
    assert.equal(yellow(wiz), null, "«Nuovo profilo»: no yellow");
    assert.equal(previews(backend), 2);
  });

  for (const [label, data, entity] of [
    ["a cover that follows a profile measured on another one", () => sourceData(), "cover.kitchen_two"],
    ["a cover that follows a profile made by hand (no source)", () => sourceData({ origin: null }), "cover.living_one"],
  ]) {
    test(`${label} has no choice: a profile of its own, named as the cover is shown, and the others stay`, async () => {
      const context = await mount({ overview: data(), language: "it" });
      const { backend, wiz, press } = context;
      await summary(context, entity);
      assert.equal(wiz().querySelector('[name="save_choice"]'), null, "no «Aggiorna»");
      assert.match(text(wiz), /Questa tapparella ora usa un profilo suo; le altre restano sul profilo «tall_two_leaf»\./);
      assert.doesNotMatch(text(wiz), /Aggiorna il profilo|Questa tapparella seguirà il nuovo profilo/);
      const field = wiz().querySelector('input[name="profile_name"]');
      const name = entity === "cover.kitchen_two" ? "Kitchen shutter 2" : "Living room shutter 1";
      assert.equal(field.value, name);
      assert.match(wiz().querySelector('.wiz-actions [data-wiz="save"]').textContent, new RegExp(`Salva come profilo «${name}»`));
      // The name is the user's to change, under today's rules (an empty one is refused).
      field.value = ""; field.dispatchEvent(new Event("input", { bubbles: true })); await tick();
      assert.equal(wiz().querySelector('.wiz-actions [data-wiz="save"]').disabled, true);
      field.value = "Kitchen own"; field.dispatchEvent(new Event("input", { bubbles: true })); await tick();
      await press("save"); await tick(6);
      assert.deepEqual(saves(backend), [["save", "new", "Kitchen own"]], "no preview of the profile followed");
    });
  }

  test("the update's texts in English, and the refusal of an update from a cover that is not the source", async () => {
    assert.equal(i18n.en.wiz_T708, "This cover now uses a profile of its own; the others stay on the profile \"{old}\".");
    assert.equal(i18n.it.wiz_T708, "Questa tapparella ora usa un profilo suo; le altre restano sul profilo «{old}».");
    assert.deepEqual([i18n.it.wiz_T709, i18n.it.wiz_T710], ["Cambiano i tempi delle tapparelle {names}.", "Perderanno la verifica le tapparelle {names}."]);
    assert.deepEqual([i18n.en.wiz_T709, i18n.en.wiz_T710], ["Times change for the covers {names}.", "The covers {names} will lose their check."]);
    assert.deepEqual([i18n.it.wiz_T721, i18n.it.wiz_T721_one], ["Cambiano le posizioni intermedie delle tapparelle {names}.", "Cambiano le posizioni intermedie della tapparella {names}."]);
    assert.deepEqual([i18n.en.wiz_T721, i18n.en.wiz_T721_one], ["Intermediate positions change for the covers {names}.", "Intermediate positions change for the cover {names}."]);
    assert.ok(i18n.it.profileError_profile_not_source && i18n.en.profileError_profile_not_source);
    // Gone now that only the source updates its profile: the update offered to a follower and its notices. The old
    // refusal of a follower without its travel stays among the shared `profileError_*` texts the profile dialogs read.
    for (const key of ["wiz_T674", "wiz_T676", "wiz_T677", "wiz_T704", "wiz_T705", "wiz_T519"]) {
      assert.equal(i18n.it[key], undefined, key);
      assert.equal(i18n.en[key], undefined, key);
    }
    const context = await mount({ overview: sourceData(), language: "en" });
    await summary(context, "cover.kitchen_two");
    assert.match(text(context.wiz), /This cover now uses a profile of its own; the others stay on the profile "tall_two_leaf"\./);
  });
});

describe("the steps' values", async () => {
  /** Each step of the column: its label, its state, its lines. */
  const steps = (wiz) => Object.fromEntries([...wiz().querySelectorAll(".wiz-steps > ol > li")].map((item) => [
    item.querySelector(".wiz-step-label").textContent, [item.dataset.stepState, [...item.querySelectorAll("ul > li")].map((sub) => sub.textContent)]]));

  test("«Lamelle» shows «—» before the lift-off as «Salita» and «Discesa» before their runs, by the same rule (only once the step is reached)", async () => {
    const context = await mount();
    const { backend, wiz, code, press } = context;
    context.backend.hass.states["cover.living_one"] = { state: "closed", attributes: { current_position: 0 } };
    await context.startFor("cover.living_one", "base");
    await press("begin");
    assert.equal(code(), "C-11");
    // The ascent reached, not measured: «—»; the steps ahead say nothing yet.
    let column = steps(wiz);
    assert.deepEqual(column.Ascent, ["current", ["Opening time: —"]]);
    assert.deepEqual([column.Descent, column.Slats], [["future", []], ["future", []]]);
    await press("run:open"); await move(backend, { seconds: 14.2 }); await press("endpoint");
    await press("ack:ascent");
    assert.deepEqual(steps(wiz).Descent, ["current", ["Closing time: —"]]);
    await press("run:close"); await move(backend, { seconds: 14.0 }); await press("endpoint");
    await press("ack:descent");
    assert.equal(code(), "C-07");
    column = steps(wiz);
    assert.deepEqual([column.Ascent, column.Descent], [["done", ["Opening time: 14.2 s"]], ["done", ["Closing time: 14.0 s"]]]);
    assert.deepEqual(column.Slats, ["current", ["Slat time: —"]], "the same «—», in the same line");
    // The markup of the line as the ascent's: label, then the value in bold.
    const line = [...wiz().querySelectorAll(".wiz-steps > ol > li")].find((item) => item.querySelector(".wiz-step-label").textContent === "Slats");
    assert.equal(line.querySelector("ul > li").innerHTML, "Slat time: <strong>—</strong>");
    await press("run:open"); await move(backend, { seconds: 2.7 }); await press("lift");
    assert.deepEqual(steps(wiz).Slats, ["current", ["Slat time: 2.7 s"]]);
    await press("repeat");
    assert.deepEqual(steps(wiz).Slats, ["current", ["Slat time: —"]], "the repeated lift-off: «—» again");
    backend.reclosed(); await tick();
    assert.deepEqual(steps(wiz).Slats, ["current", ["Slat time: —"]]);
  });
});

describe("requests waiting for an answer", async () => {
  /**
   * Home Assistant takes the next `action` at once (its views reach the subscription) but its answer waits for
   * `release()`, as when Home Assistant is slow to answer.
   */
  function holdNextAnswer(backend, action) {
    const original = backend.hass.callWS;
    let release, held = false;
    const gate = new Promise((resolve) => { release = resolve; });
    backend.hass.callWS = async (message) => {
      if (held || message.type !== "myhome/cover_calibration/action" || message.action !== action) return original(message);
      held = true;
      const answer = await original(message);
      await gate;
      return answer;
    };
    return async () => { release(); await tick(6); };
  }
  const bar = (wiz) => Object.fromEntries([...wiz().querySelectorAll(".wiz-actions .wiz-action")].map((button) => [button.dataset.wiz, [button.textContent, button.disabled]]));

  test("on «Lamelle» ② is off from the tap on ① until Home Assistant answers it, a tap on it sends nothing, Stop stays on; then ② turns on by itself", async () => {
    const context = await mount();
    const { backend, wiz, code, press, actions } = context;
    backend.hass.states["cover.living_one"] = { state: "closed", attributes: { current_position: 0 } };
    await context.startFor("cover.living_one", "base");
    await press("begin");
    await press("run:open"); await move(backend, { seconds: 14.2 }); await press("endpoint");
    await press("ack:ascent");
    await press("run:close"); await move(backend, { seconds: 14.0 }); await press("endpoint");
    await press("ack:descent");
    assert.equal(code(), "C-07");
    const label = bar(wiz).lift[0];
    const release = holdNextAnswer(backend, "open");
    await press("run:open");
    // The motor runs (`lifting`) before the answer to ① arrives.
    await move(backend, { seconds: 0.5 });
    assert.equal(backend.session.phase, "lifting");
    assert.deepEqual(bar(wiz).lift, [label, true], "② off while ① waits for its answer");
    assert.equal(bar(wiz).stop[1], false, "Stop on while it waits");
    wiz().querySelector('.wiz-actions [data-wiz="lift"]').click(); await tick();
    assert.equal(actions().includes("lift"), false, "a tap on ② off sends nothing");
    await release();
    assert.deepEqual(bar(wiz).lift, [label, false], "② on by itself with the answer, the same label");
    await press("lift");
    assert.deepEqual([actions().at(-1), backend.session.phase], ["lift", "review"], "the lift-off taken");
  });

  test("«Non ha fatto quello che doveva» and Stop stay on while a request waits for its answer; the tap sends Stop", async () => {
    const context = await mount();
    const { backend, wiz, code, actions, startFor } = context;
    await startFor("cover.living_one");
    const release = holdNextAnswer(backend, "next");
    wiz().querySelector('.wiz-frame [data-wiz="begin"]').click(); await tick();
    // The positioning runs before the answer to «Avanti» arrives.
    backend.started(); backend.tick(5); backend.emit(); await tick();
    assert.equal(code(), "A-04");
    assert.equal(bar(wiz).endpoint[1], true, "the end stop off");
    assert.equal(bar(wiz).misbehaved[1], false, "«Non ha fatto quello che doveva» on while «Avanti» waits");
    assert.equal(bar(wiz).stop[1], false, "Stop on while «Avanti» waits");
    wiz().querySelector('.wiz-actions [data-wiz="misbehaved"]').click(); await tick();
    assert.equal(actions().at(-1), "stop", "sent at once, a Stop");
    await release();
  });
});

describe("commands refused", async () => {
  /** From now on Home Assistant refuses every command of the session (the cancel too); heartbeats still answer. */
  function refuseCommands(backend) {
    const original = backend.hass.callWS;
    backend.hass.callWS = async (message) => {
      if (message.type === "myhome/cover_calibration/action" && !["heartbeat", "show"].includes(message.action)) {
        backend.calls.push(message);
        throw Object.assign(new Error("no answer"), { code: "timeout" });
      }
      return original(message);
    };
    return () => { backend.hass.callWS = original; };
  }
  /** The connection's `ready` event (home-assistant-js-websocket), fired by the test when the connection returns. */
  function readyEvents(backend) {
    const listeners = new Set();
    backend.hass.connection.addEventListener = (type, callback) => { if (type === "ready") listeners.add(callback); };
    backend.hass.connection.removeEventListener = (type, callback) => { if (type === "ready") listeners.delete(callback); };
    return { listeners, fire: async () => { for (const callback of [...listeners]) callback(backend.hass.connection); await settle(); } };
  }

  test("Home Assistant refuses every command, the cancel too: «Esci senza salvare» closes the wizard back to the list; the cancel goes again when the connection returns", async () => {
    const { backend, wiz, tab, press, startFor, actions } = await mount();
    const ready = readyEvents(backend);
    await startFor("cover.living_one");
    await press("begin"); await move(backend);
    const restore = refuseCommands(backend);
    wiz().querySelector('.wiz-head [data-wiz="exit"]').click(); await tick();
    wiz().querySelector('[data-wiz="leave"]').click(); await settle();
    assert.ok(actions().includes("cancel"), "the cancel was sent");
    assert.notEqual(backend.session.phase, "cancelled", "and refused");
    assert.equal(wiz(), null, "the wizard closed all the same");
    assert.ok(tab().querySelector(".cal-list") && !tab().querySelector("dialog.cal-flow"), "back on the list it was opened from");
    assert.equal(ready.listeners.size, 1, "the cancel waits for the connection to return");
    // The connection returns, Home Assistant answers again: the cancel queued goes through, once.
    restore();
    await ready.fire();
    assert.equal(backend.session.phase, "cancelled", "the session cancelled when the connection returned");
    assert.equal(ready.listeners.size, 0, "nothing left queued");
  });

  for (const taken of [true, false]) {
    test(taken ? "the cancel queued is dropped when another device took the session meanwhile: never claimed back, never cancelled, nothing said"
      : "the cancel queued still goes through when the session is still this device's (the read says `client_owner`)", async () => {
      const { backend, wiz, press, startFor } = await mount();
      const ready = readyEvents(backend);
      await startFor("cover.living_one");
      await press("begin"); await move(backend);
      const restore = refuseCommands(backend);
      wiz().querySelector('.wiz-head [data-wiz="exit"]').click(); await tick();
      wiz().querySelector('[data-wiz="leave"]').click(); await settle();
      assert.equal(wiz(), null, "the wizard closed");
      assert.equal(ready.listeners.size, 1, "the cancel queued");
      restore();
      // Another device takes the session and carries on with it.
      if (taken) backend.takenElsewhere();
      const calls = backend.calls.length, subscriptions = backend.subscriptions.length;
      await ready.fire();
      const read = backend.calls.slice(calls).find((message) => message.type === "myhome/cover_profiles/read");
      assert.ok(read?.client_id, "the session read again, as this client");
      const claims = backend.subscriptions.slice(subscriptions).filter((message) => message.type === "myhome/cover_calibration/resume" && message.claim);
      const cancels = backend.calls.slice(calls).filter((message) => message.action === "cancel");
      if (taken) {
        assert.deepEqual([claims.length, cancels.length], [0, 0], "no claim, no cancel");
        assert.notEqual(backend.session.phase, "cancelled", "the other device's session goes on");
        assert.equal(wiz(), null, "nothing said");
      } else {
        assert.deepEqual([claims.length, cancels.length], [1, 1], "claimed as its owner, then cancelled");
        assert.equal(backend.session.phase, "cancelled");
      }
      assert.equal(ready.listeners.size, 0, "nothing left queued");
    });
  }

  test("still refused when the connection returns, the cancel stays queued for the next time; a session already over is dropped", async () => {
    const { backend, wiz, press, startFor } = await mount();
    const ready = readyEvents(backend);
    await startFor("cover.living_one");
    await press("begin"); await move(backend);
    refuseCommands(backend);
    wiz().querySelector('.wiz-head [data-wiz="exit"]').click(); await tick();
    wiz().querySelector('[data-wiz="leave"]').click(); await settle();
    assert.equal(wiz(), null);
    // The claim still answers but every command is refused: the cancel waits again.
    await ready.fire();
    assert.notEqual(backend.session.phase, "cancelled");
    assert.equal(ready.listeners.size, 1, "still queued");
    // Home Assistant handed the cover back by itself (the lease ran out): nothing left to cancel.
    backend.session.phase = "cancelled";
    await ready.fire();
    assert.equal(ready.listeners.size, 0, "dropped");
  });
});
