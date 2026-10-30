import assert from "node:assert/strict";
import { after, afterEach, test } from "node:test";

import { translations } from "../../custom_components/myhome/frontend/panel/panel-translations.js";
import { dom, mount, reset, tick } from "./wizard-harness.mjs";

const { buildScreen } = await import("../../custom_components/myhome/frontend/panel/panel-calibration-screens.js");

afterEach(reset);
after(() => dom.window.close());

/*
 * L-119 «same step, same bar»: the set and order of the actions in the bar are decided when a step is entered and never
 * change while the user stays on that step. Automatic events (the motor starting or stopping, the clock, a new view
 * from Home Assistant, the settling hold) only turn existing actions on or off. The one substitution allowed is Stop
 * replaced in place by the state line «La corsa è finita: la tapparella è ferma» (and back, when a new leg moves).
 * Each family below walks the states one step goes through and compares the bar's ids in order, for the tab that
 * guides the calibration and for a reader.
 */
const t = (key) => translations.it[key] || key;
const cover = { name: "Hall shutter", effective: { closing: { value: 21.7, origin: "native_fallback" }, opening: { value: 22.4, origin: "native_fallback" } }, travel_cm: 198 };
const base = { mode: "geometry", owner: true, read_only: false, sequence: 3, values: {}, samples: {}, readings: {}, slats: true, travel_cm: null };
const timed = { samples: { lift: 4.4 }, readings: { gap: 3 }, values: { opening_time: 22.2, closing_time: 21.7 }, travel_cm: 198 };
const halfRead = { ...timed, readings: { gap: 3, half_open: 97 } };
const acks = { ackLift: true, ackAscent: true, ackDescent: true, ackReadings: true };

/** The bar as ids in order; the finished line counts as the Stop whose slot it takes. */
export const barOf = (screen) => screen.actions.map(({ id }) => (id === "stopped" ? "stop" : id));
const screenAt = ({ path = "A", scope = null, view, local = {}, elapsed = null, status = {} }) => buildScreen({ t, language: "it", stage: "session", path, scope, cover,
  view: view && { ...base, ...view }, local, elapsed, status, previews: false });
const asReader = (state) => ({ ...state, view: { ...state.view, owner: false, read_only: true } });

/** Every state of one step: [label, state]. */
export const FAMILIES = {
  // A-03 → A-04 → A-05: the first closing, from the session's start (Home Assistant starts it by itself) to the end stop.
  "A-03/A-05 first closing": [
    ["session started", { view: { step: "home", phase: "briefing" }, local: { autoStart: "home" } }],
    ["starting", { view: { step: "home", phase: "starting_close" }, local: { autoStart: "home" }, elapsed: 0 }],
    ["closing 5 s", { view: { step: "home", phase: "closing" }, local: { autoStart: "home" }, elapsed: 5 }],
    ["past the closing time", { view: { step: "home", phase: "closing" }, local: { autoStart: "home" }, elapsed: 23 }],
    ["run over", { view: { step: "home", phase: "closing" }, local: { autoStart: "home" }, elapsed: 30 }],
    ["motor stopped", { view: { step: "home", phase: "closing", motor_stopped: true, stopped_elapsed: 21 }, local: { autoStart: "home" }, elapsed: 26 }],
    ["Stop pressed", { view: { step: "home", phase: "geometry_wait_stop" }, local: { autoStart: "home" }, elapsed: 8 }],
  ],
  "A-04/A-05 return before the readings are repeated": [
    ["started", { view: { step: "home", phase: "briefing", ...halfRead }, local: { ...acks, autoStart: "home" } }],
    ["starting", { view: { step: "home", phase: "starting_close", ...halfRead }, local: { ...acks, autoStart: "home" }, elapsed: 0 }],
    ["closing 5 s", { view: { step: "home", phase: "closing", ...halfRead }, local: { ...acks, autoStart: "home" }, elapsed: 5 }],
    ["past the closing time", { view: { step: "home", phase: "closing", ...halfRead }, local: { ...acks, autoStart: "home" }, elapsed: 23 }],
    ["motor stopped", { view: { step: "home", phase: "closing", motor_stopped: true, stopped_elapsed: 21, ...halfRead }, local: { ...acks, autoStart: "home" }, elapsed: 26 }],
  ],
  // A-09 → A-10: the return to the rest after the lift-off reading, started by itself after «Fatto, avanti».
  "A-09/A-10 return to the rest": [
    ["after the reading", { view: { step: "reset", phase: "briefing", ...timed, values: {} }, local: { ackLift: true, autoStart: "reset" } }],
    ["starting", { view: { step: "reset", phase: "starting_close", ...timed, values: {} }, local: { ackLift: true, autoStart: "reset" }, elapsed: 0 }],
    ["closing", { view: { step: "reset", phase: "closing", ...timed, values: {} }, local: { ackLift: true, autoStart: "reset" }, elapsed: 3 }],
    ["past the closing time", { view: { step: "reset", phase: "closing", ...timed, values: {} }, local: { ackLift: true, autoStart: "reset" }, elapsed: 23 }],
    ["run over", { view: { step: "reset", phase: "closing", ...timed, values: {} }, local: { ackLift: true, autoStart: "reset" }, elapsed: 30 }],
    ["motor stopped", { view: { step: "reset", phase: "closing", motor_stopped: true, stopped_elapsed: 19, ...timed, values: {} }, local: { ackLift: true, autoStart: "reset" }, elapsed: 20 }],
    ["Stop pressed", { view: { step: "reset", phase: "geometry_wait_stop", ...timed, values: {} }, local: { ackLift: true, autoStart: "reset" }, elapsed: 4 }],
  ],
  // The same return on a session reopened at its briefing: its start goes first and stays, off, once pressed.
  "A-09/A-10 return to the rest, started from its button": [
    ["reopened", { view: { step: "reset", phase: "briefing", ...timed, values: {} }, local: { ackLift: true } }],
    ["start pressed", { view: { step: "reset", phase: "starting_close", ...timed, values: {} }, local: { ackLift: true, manualStart: "reset" }, elapsed: 0 }],
    ["closing", { view: { step: "reset", phase: "closing", ...timed, values: {} }, local: { ackLift: true, manualStart: "reset" }, elapsed: 3 }],
    ["motor stopped", { view: { step: "reset", phase: "closing", motor_stopped: true, stopped_elapsed: 19, ...timed, values: {} }, local: { ackLift: true, manualStart: "reset" }, elapsed: 20 }],
  ],
  // A-09 (top): the return to the top before a repeated descent.
  "A-09 return to the top": [
    ["after the press", { view: { step: "top", phase: "briefing", ...timed }, local: { ackLift: true, ackAscent: true, autoStart: "top" } }],
    ["starting", { view: { step: "top", phase: "starting_open", ...timed }, local: { ackLift: true, ackAscent: true, autoStart: "top" }, elapsed: 0 }],
    ["opening", { view: { step: "top", phase: "opening", ...timed }, local: { ackLift: true, ackAscent: true, autoStart: "top" }, elapsed: 5 }],
    ["run over", { view: { step: "top", phase: "opening", ...timed }, local: { ackLift: true, ackAscent: true, autoStart: "top" }, elapsed: 40 }],
    ["motor stopped", { view: { step: "top", phase: "opening", motor_stopped: true, stopped_elapsed: 21, ...timed }, local: { ackLift: true, ackAscent: true, autoStart: "top" }, elapsed: 22 }],
    ["Stop pressed", { view: { step: "top", phase: "geometry_wait_stop", ...timed }, local: { ackLift: true, ackAscent: true, autoStart: "top" }, elapsed: 6 }],
  ],
  // A-17: the run to half the travel before the half-ascent reading, started by itself after «Ho capito».
  "A-17 run to half the travel": [
    ["after the press", { view: { step: "half_open", phase: "briefing", ...timed }, local: { ...acks, autoStart: "half_open" } }],
    ["starting", { view: { step: "half_open", phase: "starting_open", ...timed }, local: { ...acks, autoStart: "half_open" }, elapsed: 0 }],
    ["moving", { view: { step: "half_open", phase: "opening", ...timed }, local: { ...acks, autoStart: "half_open" }, elapsed: 5 }],
    ["stopping", { view: { step: "half_open", phase: "geometry_wait_stop", ...timed }, local: { ...acks, autoStart: "half_open" }, elapsed: 11 }],
  ],
  "A-17 started from its button": [
    ["reopened", { view: { step: "half_open", phase: "briefing", ...timed }, local: acks }],
    ["start pressed", { view: { step: "half_open", phase: "starting_open", ...timed }, local: { ...acks, manualStart: "half_open" }, elapsed: 0 }],
    ["moving", { view: { step: "half_open", phase: "opening", ...timed }, local: { ...acks, manualStart: "half_open" }, elapsed: 5 }],
    ["stopping", { view: { step: "half_open", phase: "geometry_wait_stop", ...timed }, local: { ...acks, manualStart: "half_open" }, elapsed: 11 }],
  ],
  // A-19: the readings' two legs (to the top, confirmed; then down to half), one card, one bar.
  "A-19 to the top, then down to half": [
    ["after the reading", { view: { step: "top", phase: "briefing", ...halfRead }, local: { ...acks, autoStart: "top" } }],
    ["starting", { view: { step: "top", phase: "starting_open", ...halfRead }, local: { ...acks, autoStart: "top" }, elapsed: 0 }],
    ["opening", { view: { step: "top", phase: "opening", ...halfRead }, local: { ...acks, autoStart: "top" }, elapsed: 5 }],
    ["arrived", { view: { step: "top", phase: "opening", ...halfRead }, local: { ...acks, autoStart: "top" }, elapsed: 23 }],
    ["run over", { view: { step: "top", phase: "opening", ...halfRead }, local: { ...acks, autoStart: "top" }, elapsed: 40 }],
    ["motor stopped", { view: { step: "top", phase: "opening", motor_stopped: true, stopped_elapsed: 21, ...halfRead }, local: { ...acks, autoStart: "top" }, elapsed: 22 }],
    ["top confirmed", { view: { step: "half_close", phase: "briefing", ...halfRead }, local: { ...acks, autoStart: "half_close" } }],
    ["down to half", { view: { step: "half_close", phase: "closing", ...halfRead }, local: { ...acks, autoStart: "half_close" }, elapsed: 3 }],
    ["stopping", { view: { step: "half_close", phase: "geometry_wait_stop", ...halfRead }, local: { ...acks, autoStart: "half_close" }, elapsed: 9 }],
  ],
  // A-30: the check's legs (to the top, confirmed; then the timed run to its target).
  "A-30 check legs": [
    ["check started", { view: { step: "top", phase: "briefing", ...halfRead, check: { direction: "closing", planned_seconds: 9 } }, local: { ...acks, autoStart: "top" } }],
    ["opening", { view: { step: "top", phase: "opening", ...halfRead, check: { direction: "closing", planned_seconds: 9 } }, local: { ...acks, autoStart: "top" }, elapsed: 5 }],
    ["motor stopped", { view: { step: "top", phase: "opening", motor_stopped: true, stopped_elapsed: 21, ...halfRead, check: { direction: "closing", planned_seconds: 9 } },
      local: { ...acks, autoStart: "top" }, elapsed: 22 }],
    ["top confirmed", { view: { step: "check", phase: "briefing", ...halfRead, check: { direction: "closing", planned_seconds: 9 } }, local: { ...acks, autoStart: "check" } }],
    ["to its target", { view: { step: "check", phase: "closing", ...halfRead, check: { direction: "closing", planned_seconds: 9 } }, local: { ...acks, autoStart: "check" }, elapsed: 3 }],
    ["stopping", { view: { step: "check", phase: "geometry_wait_stop", ...halfRead, check: { direction: "closing", planned_seconds: 9 } }, local: { ...acks, autoStart: "check" }, elapsed: 9 }],
  ],
  // A-07, A-11, A-14: ① · ② · Stop from the briefing to the stop that follows ② (G2-10's wait included).
  "A-07 lift-off run": [
    ["briefing", { view: { step: "lift", phase: "briefing" }, local: { ackLift: true } }],
    ["① sent", { view: { step: "lift", phase: "briefing" }, local: { ackLift: true }, status: { starting: true } }],
    ["starting", { view: { step: "lift", phase: "starting_open" }, local: { ackLift: true }, elapsed: 0 }],
    ["moving", { view: { step: "lift", phase: "opening" }, local: { ackLift: true }, elapsed: 3 }],
    ["② pressed, stopping", { view: { step: "lift", phase: "geometry_wait_stop", samples: { lift: 4.4 } }, local: { ackLift: true }, elapsed: 4 }],
  ],
  "A-11 full ascent": [
    ["briefing", { view: { step: "opening", phase: "briefing", ...timed, values: {} }, local: { ackLift: true } }],
    ["starting", { view: { step: "opening", phase: "starting_open", ...timed, values: {} }, local: { ackLift: true }, elapsed: 0 }],
    ["moving", { view: { step: "opening", phase: "opening", ...timed, values: {} }, local: { ackLift: true }, elapsed: 10 }],
    ["② pressed, stopping", { view: { step: "opening", phase: "geometry_wait_stop", ...timed, values: { opening_time: 22.2 } }, local: { ackLift: true, fullRun: { step: "opening", pressed: true } }, elapsed: 23 }],
    ["② pressed, motor stopped", { view: { step: "opening", phase: "opening", motor_stopped: true, stopped_elapsed: 22.2, ...timed, values: {} },
      local: { ackLift: true, fullRun: { step: "opening", pressed: true } }, elapsed: 23 }],
  ],
  "A-14 full descent": [
    ["briefing", { view: { step: "closing", phase: "briefing", ...timed }, local: { ackLift: true, ackAscent: true } }],
    ["starting", { view: { step: "closing", phase: "starting_close", ...timed }, local: { ackLift: true, ackAscent: true }, elapsed: 0 }],
    ["moving", { view: { step: "closing", phase: "closing", ...timed }, local: { ackLift: true, ackAscent: true }, elapsed: 10 }],
    ["② pressed, stopping", { view: { step: "closing", phase: "geometry_wait_stop", ...timed }, local: { ackLift: true, ackAscent: true, fullRun: { step: "closing", pressed: true } }, elapsed: 22 }],
  ],
  // Path C (times and rolls) runs the same steps on its own codes.
  "C-04/C-05 first closing": [
    ["session started", { path: "C", scope: "times_and_rolls", view: { step: "home", phase: "briefing" }, local: { autoStart: "home" } }],
    ["closing 5 s", { path: "C", scope: "times_and_rolls", view: { step: "home", phase: "closing" }, local: { autoStart: "home" }, elapsed: 5 }],
    ["run over", { path: "C", scope: "times_and_rolls", view: { step: "home", phase: "closing" }, local: { autoStart: "home" }, elapsed: 30 }],
  ],
  "C-19 to the top, then down to half": [
    ["opening", { path: "C", scope: "times_and_rolls", view: { step: "top", phase: "opening", ...halfRead }, local: { ...acks, autoStart: "top" }, elapsed: 5 }],
    ["motor stopped", { path: "C", scope: "times_and_rolls", view: { step: "top", phase: "opening", motor_stopped: true, stopped_elapsed: 21, ...halfRead }, local: { ...acks, autoStart: "top" }, elapsed: 22 }],
    ["down to half", { path: "C", scope: "times_and_rolls", view: { step: "half_close", phase: "closing", ...halfRead }, local: { ...acks, autoStart: "half_close" }, elapsed: 3 }],
  ],
  "C-11 guided ascent": [
    ["ready", { path: "C", scope: "times_only", view: { mode: "guided", phase: "confirm_closed" } }],
    ["① sent", { path: "C", scope: "times_only", view: { mode: "guided", phase: "confirm_closed" }, status: { starting: true } }],
    ["starting", { path: "C", scope: "times_only", view: { mode: "guided", phase: "starting_open" }, elapsed: 0 }],
    ["moving", { path: "C", scope: "times_only", view: { mode: "guided", phase: "opening" }, elapsed: 5 }],
    ["locked", { path: "C", scope: "times_only", view: { mode: "guided", phase: "opening" }, elapsed: 6, status: { locked: true } }],
    ["② pressed, motor stopped", { path: "C", scope: "times_only", view: { mode: "guided", phase: "opening", motor_stopped: true, stopped_elapsed: 12 },
      local: { fullRun: { step: "opening", pressed: true } }, elapsed: 13 }],
  ],
  "C-14 guided descent": [
    ["ready", { path: "C", scope: "times_only", view: { mode: "guided", phase: "confirm_open" }, local: { ackAscent: true } }],
    ["starting", { path: "C", scope: "times_only", view: { mode: "guided", phase: "starting_close" }, local: { ackAscent: true }, elapsed: 0 }],
    ["moving", { path: "C", scope: "times_only", view: { mode: "guided", phase: "closing" }, local: { ackAscent: true }, elapsed: 4 }],
    ["② pressed, motor stopped", { path: "C", scope: "times_only", view: { mode: "guided", phase: "closing", motor_stopped: true, stopped_elapsed: 21 },
      local: { ackAscent: true, fullRun: { step: "closing", pressed: true } }, elapsed: 22 }],
  ],
};

/** The walk: one code family (the step), one bar. */
export function assertOneBar(name, states) {
  const screens = states.map(([label, state]) => [label, screenAt(state)]);
  const [firstLabel, first] = screens[0];
  for (const [label, screen] of screens.slice(1)) {
    assert.deepEqual(barOf(screen), barOf(first), `${name}: «${label}» (${screen.code}) changes the bar of «${firstLabel}» (${first.code})`);
  }
  return screens;
}

for (const [name, states] of Object.entries(FAMILIES)) {
  test(`L-119: ${name} keeps one bar for the guide and for a reader`, () => {
    assertOneBar(name, states);
    assertOneBar(`${name} (reader)`, states.map(([label, state]) => [label, asReader(state)]));
  });
}

test("L-119: the walk tells a bar that changes from one that only turns actions on or off", () => {
  const states = FAMILIES["C-11 guided ascent"];
  const screens = assertOneBar("C-11", states);
  assert.ok(screens.some(([, screen]) => screen.actions.some((action) => action.disabled)) && screens.some(([, screen]) => screen.actions.every((action) => action.id !== "run:open" || !action.disabled)),
    "the same ids, on and off");
  assert.throws(() => assertOneBar("broken", [["a", { view: { step: "lift", phase: "opening" }, local: { ackLift: true }, elapsed: 2 }],
    ["b", { view: { step: "lift", phase: "reading" }, local: { ackLift: true } }]]), /changes the bar/);
});

test("L-119: A-03 → A-05 in the page: the bar drawn when the closing starts is the bar at its end stop", async () => {
  const { backend, wiz, code, press, startFor } = await mount();
  const drawn = () => [...wiz().querySelectorAll(".wiz-actions [data-wiz]")].map((item) => item.dataset.wiz).map((id) => (id === "stopped" ? "stop" : id));
  const state = (id) => wiz().querySelector(`.wiz-actions [data-wiz="${id}"]`);
  await startFor("cover.living_one");
  await press("begin");
  const bars = [[code(), drawn()]];
  backend.started(); backend.tick(5); backend.emit(); await tick();
  bars.push([code(), drawn()]);
  assert.equal(code(), "A-04");
  assert.equal(state("endpoint").disabled, true, "the end stop waits for the cover to arrive");
  assert.equal(state("misbehaved").disabled, false, "«Non ha fatto quello che doveva» from the start");
  assert.equal(state("stop").disabled, false);
  backend.tick(18); backend.emit(); await tick();
  bars.push([code(), drawn()]);
  assert.equal(code(), "A-05");
  assert.match(wiz().querySelector(".wiz-body").textContent, /si sta chiudendo completamente|is closing fully/, "still moving: not «closed» yet");
  assert.equal(state("endpoint").disabled, false);
  backend.session.motor_stopped = true; backend.emit(); await tick();
  bars.push([code(), drawn()]);
  assert.equal(state("stopped").tagName, "P", "the finished line in Stop's slot");
  assert.match(wiz().querySelector(".wiz-body").textContent, /si è chiusa completamente|has closed fully/);
  for (const [at, bar] of bars) assert.deepEqual(bar, ["endpoint", "misbehaved", "stop"], `the same bar on ${at}`);
});

test("L-119: every bar drawn from the lift-off reading to A-10 is the same one, the start by itself included", async () => {
  const { backend, wiz, code, press, type, startFor } = await mount();
  await startFor("cover.living_one");
  await press("begin"); backend.started(); backend.tick(25); backend.emit(); await tick();
  await press("endpoint"); backend.stopped(); await tick();
  await press("ack:lift"); await press("next"); backend.started(); backend.tick(4); backend.emit(); await tick();
  await press("lift"); backend.stopped(); await tick();
  assert.equal(code(), "A-08");
  // Every bar the page draws, however briefly (a view in between included).
  const bars = [];
  const record = () => { const bar = wiz().querySelector(".wiz-actions"); if (bar) bars.push(`${code()} ${[...bar.querySelectorAll("[data-wiz]")].map((item) => item.dataset.wiz).join(" · ")}`); };
  const observer = new window.MutationObserver(record);
  observer.observe(wiz(), { subtree: true, childList: true, attributes: true, attributeFilter: ["data-code"] });
  try {
    await type("3");
    record();
    backend.started(); backend.tick(5); backend.emit(); await tick(); record();
    backend.tick(30); backend.emit(); await tick(); record();
    assert.equal(code(), "A-10");
  } finally { observer.disconnect(); }
  const after = bars.slice(bars.findIndex((bar) => bar.startsWith("A-09")));
  assert.ok(after.length >= 3, after.join("\n"));
  assert.deepEqual([...new Set(after.map((bar) => bar.replace(/^\S+ /, "").replace("stopped", "stop")))], ["endpoint · stop"], after.join("\n"));
});

test("L-119: a return reopened at its briefing keeps its own start, off, once pressed", async () => {
  const { backend, wiz, press, panel, startFor } = await mount();
  await startFor("cover.living_one");
  await press("begin"); backend.started(); backend.tick(25); backend.emit(); await tick();
  await press("endpoint"); backend.stopped(); await tick();
  await press("ack:lift"); await press("next"); backend.started(); backend.tick(4); backend.emit(); await tick();
  await press("lift"); backend.stopped(); await tick();
  // The reading is sent but the automatic start does not happen (as on a session reopened at the briefing).
  const wizard = panel._calibrationTab._wizard;
  const autoNext = wizard._autoNext;
  wizard._autoNext = () => {};
  try {
    const input = wiz().querySelector('[name="reading_cm"]'); input.value = "3"; input.dispatchEvent(new Event("input", { bubbles: true }));
    await press("reading");
  } finally { wizard._autoNext = autoNext; }
  const bar = () => [...wiz().querySelectorAll(".wiz-actions [data-wiz]")].map((item) => [item.dataset.wiz, item.disabled]);
  assert.deepEqual(bar(), [["next", false], ["endpoint", true], ["stop", true]]);
  await press("next");
  backend.started(); backend.tick(3); backend.emit(); await tick();
  assert.deepEqual(bar(), [["next", true], ["endpoint", true], ["stop", false]], "L-120: the end stop waits for the cover to arrive");
  backend.tick(19); backend.emit(); await tick();
  assert.deepEqual(bar(), [["next", true], ["endpoint", false], ["stop", false]]);
});

test("L-119b: Stop and the finished line are one slot of the bar: the same node, its class toggled, the other actions untouched", async () => {
  const { backend, wiz, code, press, startFor } = await mount();
  await startFor("cover.living_one");
  await press("begin");
  backend.started(); backend.tick(10); backend.emit(); await tick();
  assert.equal(code(), "A-04");
  const column = wiz().querySelector(".wiz-actions");
  const slot = column.querySelector(":scope > .wiz-stop-slot");
  const ctas = [...column.children].filter((item) => item !== slot);
  assert.deepEqual([slot.className, slot.children.length, slot.firstElementChild.dataset.wiz, column.lastElementChild === slot], ["wiz-stop-slot", 1, "stop", true]);
  backend.session.motor_stopped = true; backend.emit(); await tick();
  assert.equal(code(), "A-05");
  assert.equal(column.querySelector(":scope > .wiz-stop-slot"), slot, "the same slot node");
  assert.deepEqual([slot.className, slot.children.length, slot.firstElementChild.dataset.wiz, slot.firstElementChild.getAttribute("role")], ["wiz-stop-slot is-finished", 1, "stopped", "status"]);
  assert.deepEqual([...column.children].filter((item) => item !== slot), ctas, "the actions above are the same nodes, in the same places");
  // A reader keeps the slot too.
  const view = backend.view;
  backend.view = (extra = {}) => view({ owner: false, read_only: true, ...extra });
  backend.emit(); await tick();
  const reader = wiz().querySelector(".wiz-actions > .wiz-stop-slot");
  assert.deepEqual([reader.className, reader.firstElementChild.dataset.wiz, reader.previousElementSibling.className], ["wiz-stop-slot is-finished", "stopped", "wiz-readonly"]);
  backend.session.motor_stopped = false; backend.session._started = null; backend.started(); backend.tick(1); backend.emit(); await tick();
  assert.equal(wiz().querySelector(".wiz-actions > .wiz-stop-slot"), reader, "back to Stop in the same slot");
  assert.deepEqual([reader.className, reader.firstElementChild.dataset.wiz], ["wiz-stop-slot", "stop"]);
});

test("L-120: every «arrived» confirm comes on at the earliest of the stop status and the estimated run, no margin, as its bar ends", () => {
  // [screen, path, view, local, estimate (s)]: the first closing and the returns use the times known at that point (the
  // closing time in use before anything is measured; the measured ascent afterwards); A-19 starts where the half-ascent
  // run stopped: 22.2 − (4.4 + 22.2) / 2 = 8.9 s.
  const cases = [
    ["A-04/A-05", "A", { step: "home", phase: "closing" }, {}, 21.7],
    ["C-04/C-05", "C", { step: "home", phase: "closing" }, {}, 21.7],
    ["A-09/A-10", "A", { step: "reset", phase: "closing", ...timed, values: {} }, { ackLift: true }, 21.7],
    ["A-09 to the top", "A", { step: "top", phase: "opening", ...timed }, { ackLift: true, ackAscent: true }, 22.2],
    ["A-19 first leg", "A", { step: "top", phase: "opening", ...halfRead }, acks, 8.9],
    ["C-19 first leg", "C", { step: "top", phase: "opening", ...halfRead }, acks, 8.9],
    ["A-30 first leg", "A", { step: "top", phase: "opening", ...halfRead, check: { direction: "closing", planned_seconds: 9 } }, acks, 22.2],
  ];
  for (const [name, path, view, local, estimate] of cases) {
    const at = (elapsed, extra = {}) => screenAt({ path, scope: path === "C" ? "times_and_rolls" : null, view: { ...view, ...extra }, local, elapsed });
    const endpoint = (screen) => screen.actions.find((action) => action.id === "endpoint");
    const before = at(estimate - 0.05), on = at(estimate);
    assert.equal(endpoint(before).disabled, true, `${name}: off before the estimate`);
    assert.ok(before.wait.progress.value < 1 && before.wait.progress.remaining >= 1, `${name}: the bar still runs`);
    assert.equal(!!endpoint(on).disabled, false, `${name}: on at ${estimate} s, no margin`);
    assert.ok(!on.wait || (on.wait.progress.value === 1 && on.wait.progress.remaining == null), `${name}: the bar has ended at that instant`);
    // The stop status ends it earlier: on at once, the bar complete.
    const stopped = at(3, { motor_stopped: true, stopped_elapsed: 3 });
    assert.equal(!!endpoint(stopped).disabled, false, `${name}: on at the stop status`);
    assert.ok(!stopped.wait || (stopped.wait.progress.value === 1 && stopped.wait.progress.remaining == null), `${name}: the bar complete at the stop status`);
  }
  // Not an «arrived» confirm: ② of a full run stays the press of the moment, and a run stopped before it is still not valid.
  const unpressed = screenAt({ view: { step: "opening", phase: "opening", ...timed, values: {}, motor_stopped: true, stopped_elapsed: 21.4 }, local: { ackLift: true }, elapsed: 22 });
  assert.deepEqual([unpressed.code, barOf(unpressed)], ["A-12", ["repeat", "invalid-next"]]);
  const running = screenAt({ view: { step: "opening", phase: "opening", ...timed, values: {} }, local: { ackLift: true }, elapsed: 1 });
  assert.equal(!!running.actions.find((action) => action.id === "endpoint").disabled, false, "② on at once while the motor runs");
});

test("L-121: A-04/A-05 with a position Home Assistant does not know, or shows 0 on an open cover: the end stop at the full closing time", async () => {
  for (const [label, entity, at, expected] of [["position 0 on an open cover", { state: "open", attributes: { current_position: 0 } }, 21.6, 21.7],
    ["no position", { state: "open", attributes: { current_position: null } }, 21.6, 21.7], ["position 50", { state: "open", attributes: { current_position: 50 } }, 10.8, 10.85]]) {
    reset();
    const { backend, wiz, code, press, startFor } = await mount();
    backend.hass.states["cover.living_one"] = entity;
    await startFor("cover.living_one");
    await press("begin");
    backend.started(); backend.tick(at); backend.emit(); await tick();
    assert.equal(code(), "A-04", `${label}: still positioning at ${at} s`);
    assert.equal(wiz().querySelector('[data-wiz="endpoint"]').disabled, true, `${label}: off before ${expected} s`);
    backend.tick(expected - at + 0.05); backend.emit(); await tick();
    assert.equal(wiz().querySelector('[data-wiz="endpoint"]').disabled, false, `${label}: on at ${expected} s`);
  }
});
