import assert from "node:assert/strict";
import { after, afterEach, test } from "node:test";

import { translations } from "../../custom_components/myhome/frontend/panel/panel-translations.js";
import { dom, mount, move, reset, settle, tick } from "./wizard-harness.mjs";
import { panelCss } from "./panel-css.mjs";

afterEach(reset);
after(() => dom.window.close());

/**
 * The basic calibration of a cover fine-tuned with the tape (Kitchen shutter 2: its rolls measured) is
 * path C's former «times only»: C-35 without the slats question, the guided runs without the tap, C-36 saved on the
 * cover. C-01 and C-02 are no longer entered: A-02 holds the choice.
 */
async function toCase(context) {
  const { startFor, code, backend } = context;
  // A times-only measurement starts at once only from a cover Home Assistant reports closed at a known 0.
  backend.hass.states["cover.kitchen_two"] = { state: "closed", attributes: { current_position: 0 } };
  await startFor("cover.kitchen_two", "base");
  assert.equal(code(), "C-35");
  // Ricalibra asks about the slats on a fine-tuned cover too; these tests run it without.
  const radio = context.wiz().querySelector('input[name="slats"][value="no"]');
  radio.checked = true; radio.dispatchEvent(new Event("change", { bubbles: true })); await tick();
}

/**
 * The basic calibration's session closes the cover itself (`home`); the panel's own positioning (the close before the
 * run, its automatic Stop) is left to path C's «Solo i tempi», entered here by its ids as the other hidden paths.
 */
async function toTimesOnly(context) {
  await toCase(context);
  Object.assign(context.panel._calibrationTab._wizard._local, { path: "C", scope: "times_only" });
}

test("on a cover fine-tuned with the tape the basic calibration measures the times only, with the guided session; saved into a profile, for a follower always a new one", async () => {
  const context = await mount();
  const { backend, wiz, code, press, actions, title } = context;
  await toCase(context);
  assert.equal(code(), "C-35");
  assert.ok(wiz().querySelector('input[name="slats"]'), "the slats question, on a fine-tuned cover too");
  assert.equal(wiz().querySelector(".wiz-steps-line").textContent, "Preparation · 2 of 5");
  assert.equal(wiz().querySelector(".wiz-steps > .wiz-steps-head > .wiz-steps-variant").textContent, "Basic", "under the process");
  assert.equal(wiz().querySelector(".wiz-sheet-steps > .wiz-steps-head > .wiz-steps-variant").textContent, "Basic", "the sheet's head too");
  assert.equal(wiz().querySelector(".wiz-body").textContent, translations.en.wiz_T637.replace(/\*\*/g, "").replace(/\n+/g, ""), "the basic calibration's briefing");
  await press("begin");
  const start = backend.subscriptions.find((message) => message.type === "myhome/cover_calibration/start");
  assert.equal(start.mode, "guided", "the guided measurement, said");
  assert.equal("direction" in start, false, "both directions");
  assert.equal(start.slats, false, "«without slats» answered: no lift-off run");
  assert.deepEqual(actions(), [], "the guided protocol starts nothing by itself");
  // Our own screens and texts, never the maintainer's phase texts.
  assert.equal(code(), "C-11");
  assert.equal(title(), "Full ascent");
  assert.match(wiz().querySelector(".wiz-body").textContent, /Get ready to press button ② when the motor stops by itself at the top \(its sound stops completely\)\./);
  assert.doesNotMatch(wiz().textContent, /Confirm that the cover is fully closed and stopped before starting/);
  assert.deepEqual([...wiz().querySelectorAll(".wiz-actions .wiz-action")].map((item) => [item.dataset.wiz, item.disabled]), [["run:open", false], ["endpoint", true], ["stop", true]], "at rest Stop is drawn, last, off");
  await press("run:open");
  await move(backend, { seconds: 12.6 });
  assert.ok(wiz().querySelector('.wiz-actions [data-wiz="stop"]'), "Stop while it runs");
  assert.equal(code(), "C-11");
  assert.equal(wiz().querySelector(".wiz-stopwatch").hidden, false);
  await press("endpoint");
  assert.equal(code(), "C-12");
  assert.equal(title(), "Ascent measured");
  assert.deepEqual([...wiz().querySelectorAll(".wiz-results .wiz-result-label")].map((item) => item.textContent), ["Opening time"], "the glossary's label");
  // The ascent's «Ripeti» brings the cover back down by itself and repeats only the ascent.
  assert.deepEqual([...wiz().querySelectorAll(".wiz-actions .wiz-action")].map((item) => item.dataset.wiz), ["ack:ascent", "repeat:opening"]);
  await press("ack:ascent");
  assert.equal(code(), "C-14");
  assert.equal(title(), "Full descent");
  assert.deepEqual([...wiz().querySelectorAll(".wiz-actions .wiz-action")].map((item) => item.textContent), ["①  Start movement", "②  Stopped at the bottom", "Stop the cover"], "Stop drawn, last, off at rest");
  await press("run:close");
  await move(backend, { seconds: 12.2 });
  assert.ok(wiz().querySelector('.wiz-actions [data-wiz="stop"]'), "Stop while it runs");
  assert.equal(code(), "C-14");
  await press("endpoint");
  assert.equal(code(), "C-15");
  assert.equal(title(), "Descent measured");
  await press("ack:descent");
  // Always a profile; a follower does not update the profile measured elsewhere: no choice,
  // a new one named after the cover.
  assert.equal(code(), "A-21b");
  assert.equal(wiz().querySelector('[name="save_profile"]'), null);
  assert.equal(wiz().querySelector('[name="save_choice"]'), null);
  assert.match(wiz().querySelector(".wiz-profile-block").textContent, /This cover now uses a profile of its own; the others stay on the profile "tall_two_leaf"\./);
  assert.equal(wiz().querySelector('[name="profile_name"]').value, "Kitchen shutter 2", "the cover's name, editable");
  assert.equal(wiz().querySelector('[data-wiz="save"]').textContent, "Save as profile \"Kitchen shutter 2\"");
  assert.equal(wiz().querySelector('[data-wiz="refine"]'), null);
  await press("save");
  const save = backend.calls.filter((message) => message.action === "save").at(-1);
  assert.deepEqual([save.save_mode ?? "new", save.name], ["new", "Kitchen shutter 2"]);
  assert.equal(code(), "A-34");
  assert.deepEqual(actions(), ["open", "endpoint", "close", "endpoint", "save"]);
});

test("times only: a repeat starts the times again from the briefing, and a run the bus ended without ② is not valid", async () => {
  const context = await mount();
  const { backend, wiz, code, press } = context;
  await toCase(context);
  await press("begin");
  await press("run:open");
  await move(backend, { seconds: 12.6 });
  await press("endpoint");
  assert.equal(code(), "C-12");
  // «Ricomincia» (the start again, from the briefing) stays for a run not valid and for the other ways out.
  context.panel._calibrationTab._wizard._act("restart"); await tick(8);
  assert.equal(backend.session.phase, "cancelled", "the attempt is cancelled, nothing saved");
  assert.equal(code(), "C-35");
  const panel = context.panel ?? document.querySelector("myhome-panel");
  const wizard = panel._calibrationTab._wizard;
  // A guided run is tracked as a full run, for the same validity rule.
  wizard._local.fullRun = null;
  wizard._trackFullRun({ mode: "guided", phase: "opening", owner: true, attachment: "a" });
  assert.equal(wizard._local.fullRun.step, "opening");
});

test("«Ricomincia» keeps the session when Home Assistant refuses the cancel: no detach, the error shown, the cover still held", async () => {
  const context = await mount();
  const { backend, wiz, code, press } = context;
  await toCase(context);
  await press("begin");
  await press("run:open");
  await move(backend, { seconds: 12.6 });
  await press("endpoint");
  assert.equal(code(), "C-12");
  const original = backend.hass.callWS;
  backend.hass.callWS = async (message) => {
    if (message.action === "cancel") { backend.calls.push(message); throw { code: "cover_unavailable", message: "refused" }; }
    return original(message);
  };
  context.panel._calibrationTab._wizard._act("restart"); await tick(8);
  assert.equal(backend.session.phase !== "cancelled", true);
  assert.equal(code(), "C-12", "still on the session's screen");
  assert.equal(backend.calls.some((message) => message.action === "detach"), false, "not detached: the cover stays this tab's");
  assert.ok(wiz().querySelector(".wiz-notices").textContent.length > 0, "the refusal is shown");
  backend.hass.callWS = original;
  context.panel._calibrationTab._wizard._act("restart"); await tick(8);
  assert.equal(backend.session.phase, "cancelled");
  assert.equal(code(), "C-35", "confirmed: from the briefing again");
});

test("a «solo tempi» session the wizard started opens in the wizard's reader view from a second tab; one from Devices keeps the maintainer's dialog", async () => {
  // The wizard marks its own start.
  const first = await mount();
  await toCase(first);
  await first.press("begin");
  assert.equal(first.backend.subscriptions.find((message) => message.type === "myhome/cover_calibration/start").origin, "wizard");
  reset();
  const guided = (extra) => ({ entry_id: "one", entity_id: "cover.living_one", session_id: "session-1", sequence: 5, revision: 7, mode: "guided",
    phase: "opening", reason: null, values: {}, stop_requested: false, save_modes: ["new", "cover", "shared"], _started: 0, ...extra });
  const second = await mount({ session: guided({ origin: "wizard" }), owner: false });
  second.backend.tick(8);
  second.tab().querySelector('[data-action="cal-tab-session"]').click(); await tick(8);
  assert.equal(second.root.querySelector("#dialog-host dialog.cal-dialog"), null, "not the maintainer's dialog");
  assert.equal(second.code(), "C-11");
  assert.ok(second.wiz().querySelector(".wiz-readonly"), "the reader view");
  assert.deepEqual([...second.wiz().querySelectorAll(".wiz-actions .wiz-action")].map((button) => button.dataset.wiz).at(-1), "stop", "with its Stop (after the step's own buttons, off)");
  assert.deepEqual(second.wiz().querySelector(".wiz-steps") && [...second.wiz().querySelectorAll(".wiz-steps > ol > li")].length, 5, "the «solo tempi» steps");
  reset();
  const devices = await mount({ session: guided({}), owner: false });
  devices.tab().querySelector('[data-action="cal-tab-session"]').click(); await tick(8);
  assert.ok(devices.root.querySelector("#dialog-host dialog.cal-dialog"), "started from Devices: the maintainer's dialog");
  assert.equal(devices.wiz(), null);
});

test("a cover shown closed at an unknown position is positioned for real: its old «closed» does not end the run", async () => {
  const context = await mount();
  const { backend, code, press, panel } = context;
  await toTimesOnly(context);
  backend.hass.states["cover.kitchen_two"] = { state: "closed", attributes: { current_position: null } };
  await press("begin");
  assert.equal(code(), "C-04");
  const wizard = panel._calibrationTab._wizard;
  wizard.updateHass(backend.hass); await tick(4);
  const started = () => backend.subscriptions.some((message) => message.type === "myhome/cover_calibration/start");
  assert.equal(started(), false, "the state it had before the close does not count");
  backend.hass.states["cover.kitchen_two"] = { state: "closing", attributes: { current_position: 40 } };
  wizard.updateHass(backend.hass); await tick(4);
  backend.hass.states["cover.kitchen_two"] = { state: "closed", attributes: { current_position: 0 } };
  wizard.updateHass(backend.hass); await tick(6);
  assert.equal(started(), true, "closed after the run: the session starts");
  assert.equal(code(), "C-11");
});

test("a cover with no known closing time that never moves is positioned for two minutes at most", async () => {
  const context = await mount();
  const { backend, code, press, panel } = context;
  await toTimesOnly(context);
  const item = panel._calibrationTab._covers().find((entry) => entry.cover.entity_id === "cover.kitchen_two");
  item.cover.effective.closing.value = null;
  backend.hass.states["cover.kitchen_two"] = { state: "closed", attributes: { current_position: null } };
  await press("begin");
  assert.equal(code(), "C-04");
  const wizard = panel._calibrationTab._wizard, homing = wizard._local.homing;
  assert.equal(homing.seconds, null, "no closing time to wait for");
  const started = () => backend.subscriptions.some((message) => message.type === "myhome/cover_calibration/start");
  // Home Assistant never reports it moving (it was already down): «closed» alone does not end the positioning…
  homing.started = Date.now() - 119000;
  wizard.updateHass(backend.hass); await tick(4);
  assert.equal(started(), false, "still positioning before two minutes");
  // …two minutes do: the session starts on «check that it is fully closed».
  homing.started = Date.now() - 121000;
  wizard.updateHass(backend.hass); await tick(6);
  assert.equal(started(), true);
  assert.equal(code(), "C-11");
});

test("times only first closes an open cover, as path A does, then starts on «check that it is fully closed»", async () => {
  const context = await mount();
  const { backend, wiz, code, press, panel } = context;
  await toTimesOnly(context);
  backend.hass.states["cover.kitchen_two"] = { state: "open", attributes: { current_position: 100 } };
  await press("begin");
  assert.equal(code(), "C-04", "the positioning first");
  assert.equal(wiz().querySelector(".wiz-title").textContent, "Positioning cover");
  assert.deepEqual(backend.hass.services.at(-1), { domain: "cover", service: "close_cover", data: { entity_id: "cover.kitchen_two" } });
  assert.equal(backend.subscriptions.some((message) => message.type === "myhome/cover_calibration/start"), false, "no session yet");
  assert.ok(wiz().querySelector('[data-wiz="stop"]'), "Stop while it closes");
  // Home Assistant reports it closed: the session starts on its confirmation.
  backend.hass.states["cover.kitchen_two"] = { state: "closed", attributes: { current_position: 0 } };
  panel._calibrationTab._wizard.updateHass(backend.hass); await tick(6);
  assert.equal(backend.subscriptions.filter((message) => message.type === "myhome/cover_calibration/start").at(-1).mode, "guided", "the guided session");
  assert.equal(code(), "C-11");
  // Stop during the positioning: Home Assistant stops the cover, nothing has started.
  reset();
  const again = await mount();
  await toTimesOnly(again);
  // After a restart Home Assistant knows no position for a modelled cover: positioned first too.
  again.backend.hass.states["cover.kitchen_two"] = { state: "open", attributes: { current_position: null } };
  await again.press("begin");
  again.wiz().querySelector('[data-wiz="stop"]').click(); await tick(4);
  assert.deepEqual(again.backend.hass.services.at(-1), { domain: "cover", service: "stop_cover", data: { entity_id: "cover.kitchen_two" } });
  assert.equal(again.backend.subscriptions.some((message) => message.type === "myhome/cover_calibration/start"), false);
  // From «Ricalibra» (start: path, the basic calibration preselected): the session closes the cover
  // itself, whatever Home Assistant's state says; the panel moves nothing of its own.
  reset();
  const third = await mount();
  const tabView = third.panel._calibrationTab, item = tabView._covers().find((entry) => entry.cover.entity_id === "cover.kitchen_two");
  third.backend.hass.states["cover.kitchen_two"] = { state: "closed", attributes: { current_position: 0 } };
  tabView._openWizard({ start: "path", item }); await tick();
  assert.equal(third.code(), "C-35", "straight to the first step");
  await third.press("begin");
  assert.equal(third.backend.subscriptions.filter((message) => message.type === "myhome/cover_calibration/start").at(-1).home, true);
  assert.equal(third.backend.hass.services.some((call) => call.service === "close_cover"), false, "no close of the panel's own");
});

test("the close before times only is over at its estimated closing time and margin, not at a stop status that comes at the actuator's timer", async () => {
  const context = await mount();
  const { backend, wiz, code, press, panel } = context;
  await toTimesOnly(context);
  backend.hass.states["cover.kitchen_two"] = { state: "open", attributes: { current_position: 100 } };
  await press("begin");
  assert.equal(code(), "C-04");
  const wizard = panel._calibrationTab._wizard, homing = wizard._local.homing;
  assert.equal(homing.seconds, 12.1, "its closing time in use, from fully open");
  const started = () => backend.subscriptions.some((message) => message.type === "myhome/cover_calibration/start");
  const stops = () => backend.hass.services.filter((call) => call.service === "stop_cover").length;
  // A MyHOMEServer1: Home Assistant keeps showing it closing until the actuator's timer (about 60 s).
  backend.hass.states["cover.kitchen_two"] = { state: "closing", attributes: { current_position: 20 } };
  // The Stop is an action on the motor: 12.1 s + max(4 s, 25 %) = 16.1 s.
  homing.started = Date.now() - 12200;
  wizard.updateHass(backend.hass); await tick(4);
  assert.deepEqual([started(), stops(), code()], [false, 0, "C-04"], "past its closing time but within the margin: no Stop");
  homing.started = Date.now() - 15900;
  wizard.updateHass(backend.hass); await tick(4);
  assert.deepEqual([started(), stops(), code()], [false, 0, "C-04"], "just before the margin ends: no Stop");
  assert.match(wiz().querySelector(".wiz-wait-remaining").textContent, /1 s/, "the bar runs to the Stop");
  // The estimate and its margin are over: the Stop is written, as path A's confirmed end stop…
  homing.started = Date.now() - 16200;
  wizard.updateHass(backend.hass); await tick(4);
  assert.equal(wiz().querySelector(".wiz-wait-remaining").hidden, true, "the bar has ended with the Stop");
  assert.deepEqual([stops(), started()], [1, false], "the Stop, once; no session on a cover Home Assistant still sees moving");
  assert.equal(backend.hass.services.at(-1).data.entity_id, "cover.kitchen_two");
  // …and once Home Assistant shows it still the session starts on «check that it is fully closed», long before 60 s.
  backend.hass.states["cover.kitchen_two"] = { state: "closed", attributes: { current_position: 0 } };
  homing.moved = false;
  wizard.updateHass(backend.hass); await tick(6);
  assert.deepEqual([started(), stops(), code()], [true, 1, "C-11"]);
  assert.deepEqual(wiz().querySelector('[data-wiz="run:open"]').disabled, false, "nothing moves before the user's ①");
});

test("the automatic Stop before times only is planned from a known, coherent position; on the full closing time otherwise", async () => {
  // The live failure: after a restart an open cover at half height, its position published as 0 (or unknown): the full
  // run. A position Home Assistant knows and that agrees with the state plans the close.
  for (const [label, attributes, state, seconds, remaining] of [["position 0 on an open cover", { current_position: 0 }, "open", 12.1, /17 s/], ["no position", { current_position: null }, "open", 12.1, /17 s/],
    ["no attribute", {}, "open", 12.1, /17 s/], ["position 50", { current_position: 50 }, "open", 6.05, /11 s/]]) {
    reset();
    const context = await mount();
    const { backend, wiz, code, press, panel } = context;
    await toTimesOnly(context);
    backend.hass.states["cover.kitchen_two"] = { state, attributes };
    await press("begin");
    assert.equal(code(), "C-04", label);
    assert.deepEqual(backend.hass.services.at(-1), { domain: "cover", service: "close_cover", data: { entity_id: "cover.kitchen_two" } }, `${label}: the close`);
    const wizard = panel._calibrationTab._wizard, homing = wizard._local.homing;
    assert.equal(homing.seconds, seconds, `${label}: ${seconds === 12.1 ? "the full closing time in use" : "the share of the closing time still ahead"}`);
    const stops = () => backend.hass.services.filter((call) => call.service === "stop_cover").length;
    // Home Assistant shows it closing (a MyHOMEServer1 reports the stop only at the actuator's timer).
    backend.hass.states["cover.kitchen_two"] = { state: "closing", attributes: { current_position: 0 } };
    wizard.updateHass(backend.hass); await tick(4);
    assert.equal(stops(), 0, `${label}: no Stop at once`);
    assert.match(wiz().querySelector(".wiz-wait-remaining").textContent, remaining, `${label}: the bar counts the run and its margin (4 s)`);
    homing.started = Date.now() - (seconds + 4 - 0.1) * 1000;
    wizard.updateHass(backend.hass); await tick(4);
    assert.equal(stops(), 0, `${label}: none before ${seconds} s + 4 s`);
    homing.started = Date.now() - (seconds + 4 + 0.1) * 1000;
    wizard.updateHass(backend.hass); await tick(4);
    assert.equal(stops(), 1, `${label}: the Stop after the full run and its margin`);
  }
});

test("Home Assistant's position counts for a positioning only when it is known and agrees with the state", async () => {
  const { knownPosition } = await import("../../custom_components/myhome/frontend/panel/panel-calibration-wizard.js");
  assert.deepEqual([{ state: "open", attributes: { current_position: 0 } }, { state: "closed", attributes: { current_position: 100 } },
    { state: "open", attributes: { current_position: null } }, { state: "open", attributes: {} }, { state: "open", attributes: { current_position: "50" } }, undefined]
    .map(knownPosition), [null, null, null, null, null, null], "never 0 for an unknown or incoherent position");
  assert.deepEqual([{ state: "open", attributes: { current_position: 50 } }, { state: "closed", attributes: { current_position: 0 } }, { state: "open", attributes: { current_position: 100 } }]
    .map(knownPosition), [50, 0, 100]);
});

test("in C-01 a value not known is «—» without its unit", async () => {
  const { buildScreen } = await import("../../custom_components/myhome/frontend/panel/panel-calibration-screens.js");
  const { translations } = await import("../../custom_components/myhome/frontend/panel/panel-translations.js");
  const t = (key) => translations.it[key] || key;
  const cover = { name: "Study", profile: { name: "standard_three_leaf" }, travel_cm: 150, effective: { opening: { value: 20 }, closing: { value: 19 }, slat_time_s: { value: null } } };
  const screen = buildScreen({ t, language: "it", stage: "current", path: "C", cover });
  assert.equal(screen.code, "C-01");
  // Label/value rows; the slats and the rolls not known read «—», without a unit.
  assert.deepEqual(screen.box.rows[2], ["Tempo lamelle", "—"]);
  assert.deepEqual(screen.box.rows.slice(4, 6), [["Coefficiente in salita", "—"], ["Coefficiente in discesa", "—"]]);
  assert.deepEqual(screen.box.rows[3], ["Corsa del telo", "150 cm"], "known values keep their unit");
});

test("C-01 and C-02 are the preamble: no stepper until the case is chosen, then the case's own steps", async () => {
  const { buildScreen } = await import("../../custom_components/myhome/frontend/panel/panel-calibration-screens.js");
  const { translations } = await import("../../custom_components/myhome/frontend/panel/panel-translations.js");
  const t = (key) => translations.en[key] || key;
  const cover = { name: "Study", profile: { name: "standard_three_leaf" }, travel_cm: 150, effective: { opening: { value: 20 }, closing: { value: 19 } } };
  const at = (stage, scope = null, local = {}) => buildScreen({ t, stage, path: "C", scope, cover, local });
  const keys = (screen) => screen.stepper.phases.map((phase) => phase.key);
  // C-01 is a preamble (nothing to choose): no stepper. C-02 shows its case's path as soon as one is
  // chosen, the preselected one included («Choosing the path» current); with nothing chosen (previews off) none.
  for (const screen of [at("current"), buildScreen({ t, stage: "case", path: "C", cover, local: {}, previews: false })]) {
    const { stepper } = screen;
    assert.equal(stepper.hidden, true, `${screen.code}: no stepper`);
    assert.deepEqual([stepper.line, stepper.barLine, stepper.segments], ["", "", []]);
    assert.deepEqual(stepper.phases.map((phase) => [phase.label, phase.state]), [["Choosing the path", "current"]], "the medium column's one step");
  }
  const preselected = at("case");
  assert.deepEqual([!!preselected.stepper.hidden, preselected.stepper.line, keys(preselected), preselected.stepper.segments],
    [false, "Choosing the path · 1 of 4", ["intro", "prep", "readings", "summary"], ["current", "future", "future", "future"]]);
  assert.deepEqual(keys(at("case", "times_only")), ["intro", "prep", "ascent", "descent", "summary"], "another case, its own steps");
  assert.deepEqual(keys(at("before", "points_only")), ["intro", "prep", "readings", "summary"], "once chosen, the case's steps");
  assert.equal(at("before", "points_only").stepper.line, "Preparation · 2 of 4");
  assert.deepEqual(keys(at("before", "times_only")), ["intro", "prep", "ascent", "descent", "summary"]);
});

test("C-01's «Currently in use» is a card of 44 px label/value rows, as the details' values, in both themes", async () => {
  const css = panelCss;
  assert.doesNotMatch(css, /\.wiz-box \{/, "no grey box any more");
  assert.match(css, /\.cal-pairs > div \{ display: flex; align-items: center; justify-content: space-between; gap: var\(--cal-sp-12\); box-sizing: border-box; min-height: var\(--cal-target\);/);
  assert.match(css, /\.cal-card \{[^}]*border-radius: var\(--cal-r-14\);\s*background: var\(--panel-surface\);/);
});
