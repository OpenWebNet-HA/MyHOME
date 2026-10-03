import assert from "node:assert/strict";
import { after, afterEach, mock, test } from "node:test";
import { JSDOM } from "jsdom";
import { CoverCalibration, calibrationClient } from "../../custom_components/myhome/frontend/panel/panel-cover-calibration.js";
import { translations } from "../../custom_components/myhome/frontend/panel/panel-translations.js";
import { calibrationScene } from "../../custom_components/myhome/frontend/panel/panel-calibration-visual.js";
import { shown } from "../../custom_components/myhome/frontend/panel/panel-cover-calibration.js";

const dom = new JSDOM("<!doctype html><body></body>", { pretendToBeVisual: true });
const { document } = dom.window;
const instances = [];
const tick = () => new Promise((resolve) => setImmediate(resolve));
const deferred = () => { let resolve; const promise = new Promise((r) => { resolve = r; }); return { promise, resolve }; };
const t = (key) => translations.it[key] || translations.en[key] || key;

test("visual guide covers the geometry steps and measures only after confirmed Stop", () => {
  for (const [step, shape] of Object.entries({ home: "closed", reset: "closed", closing: "closed", opening: "open", top: "open", lift: "slats", half_open: "middle", half_close: "middle" })) {
    const scene = calibrationScene({ mode: "geometry", phase: "briefing", step });
    assert.equal(scene.shape, shape, step);
    assert.equal(scene.measure, false, step);
    assert.equal(scene.icon, "", "briefing must not imply movement");
  }
  for (const [reading_kind, shape] of Object.entries({ lift: "gap", travel: "open", half_open: "middle", half_close: "middle" })) {
    assert.equal(calibrationScene({ mode: "geometry", phase: "geometry_wait_stop", reading_kind }).measure, false);
    const scene = calibrationScene({ mode: "geometry", phase: "reading", reading_kind });
    assert.equal(scene.shape, shape);
    assert.equal(scene.measure, true);
    assert.equal(scene.icon, "");
  }
  assert.equal(calibrationScene({ mode: "geometry", phase: "future_phase", step: "future_step" }), null);
});

test("visual guide follows directions without sending commands or inferring position from elapsed time", async () => {
  const { host, push, calls } = await mount({ direction: "closing" });
  const figure = host.querySelector(".cal-visual");
  assert.equal(figure.dataset.scene, "open");
  assert.match(figure.textContent, /non è la posizione reale/);
  assert.equal(host.querySelector(".cal-guide").open, false);
  push({ phase: "starting_close" });
  assert.equal(figure.dataset.scene, "neutral");
  assert.equal(figure.querySelector("ha-icon").getAttribute("icon"), "mdi:timer-sand");
  push({ phase: "closing", elapsed: 1 });
  const drawing = figure.querySelector(".cal-visual-drawing");
  assert.equal(figure.querySelector("ha-icon").getAttribute("icon"), "mdi:arrow-down-bold");
  const illustration = drawing.innerHTML;
  push({ phase: "closing", elapsed: 120 });
  assert.equal(drawing.innerHTML, illustration, "elapsed time is not a position estimate");
  assert.equal(host.querySelector("#cal-stop").disabled, false);
  assert.equal(calls.length, 0);
});

test("geometry illustration preserves reading focus and clears stale motion after disconnection", async () => {
  const { host, push, calls } = await mount({ mode: "geometry" });
  const figure = host.querySelector(".cal-visual");
  push({ phase: "opening", step: "lift" });
  assert.equal(figure.dataset.scene, "slats");
  assert.match(figure.textContent, /bordo inferiore/);
  push({ phase: "geometry_wait_stop", stop_requested: true });
  assert.equal(figure.dataset.scene, "neutral");
  assert.equal(figure.querySelector(".cal-visual-measure").hidden, true);
  push({ phase: "reading", reading_kind: "lift", stop_requested: false });
  assert.equal(figure.dataset.scene, "gap");
  assert.equal(figure.querySelector(".cal-visual-measure").hidden, false);
  const input = host.querySelector('[name="reading_cm"]');
  input.value = "2.5"; input.focus();
  push({});
  assert.equal(input.value, "2.5");
  assert.equal(document.activeElement, input);
  push({ recoverable: true, attached: false, attachment: "old" });
  assert.equal(figure.dataset.scene, "neutral");
  assert.equal(figure.querySelector(".cal-visual-measure").hidden, true);
  assert.match(figure.textContent, /Connessione persa/);
  assert.equal(host.querySelector("#cal-stop").disabled, false);
  push({ attached: true, attachment: "new" });
  assert.equal(figure.dataset.scene, "gap");
  assert.equal(input.value, "2.5");
  assert.equal(calls.length, 0, "recovery must not issue a movement");
});

test("automatic and interrupted phases never invite endpoint recording or measurement", async () => {
  const { host, push, calls } = await mount({ mode: "automatic", entity_ids: ["cover.one", "cover.two"] });
  const figure = host.querySelector(".cal-visual");
  for (const phase of ["opening", "closing", "settling", "between_covers", "interrupted"]) {
    push({ phase });
    assert.equal(figure.querySelector(".cal-visual-measure").hidden, true);
    assert.doesNotMatch(figure.textContent, /conferma al finecorsa|conferma con tutte/);
    if (["settling", "between_covers", "interrupted"].includes(phase)) assert.equal(figure.dataset.scene, "neutral");
  }
  assert.equal(calls.length, 0);
});

async function mount({ call, subscribe, entity_ids, direction, resume, mode = "guided", slats, language, translate = t, Calibration = CoverCalibration } = {}) {
  const host = document.createElement("section");
  document.body.append(host);
  const controller = new Calibration();
  instances.push(controller);
  const calls = [], starts = [];
  let callback, stopped = 0, saved = 0, cancelled = 0;
  let state = { entry_id: "one", entity_id: "cover.bedroom", session_id: "session-one", sequence: 1,
    revision: 4, mode, run_index: 0, phase: mode === "automatic" ? "confirm_automatic" : "confirm_closed", reason: null, values: {}, elapsed: null, stop_requested: false };
  if (direction) Object.assign(state, { direction, phase: direction === "closing" ? "confirm_open" : "confirm_closed",
    values: direction === "closing" ? { opening_time: 25.5 } : { closing_time: 32.25 } });
  if (entity_ids) Object.assign(state, { batch: true, cover_index: 0, results: [],
    targets: entity_ids.map((id) => ({ entity_id: id, name: id })) });
  if (resume) Object.assign(state, resume, { recoverable: true, attached: true, attachment: "new-controller" });
  const push = (extra) => { state = { ...state, sequence: state.sequence + 1, ...extra }; callback(state); };
  const hass = { language, connection: { subscribeMessage: async (cb, request) => {
    callback = cb; starts.push(request); cb(state);
    return subscribe ? subscribe(() => { stopped++; }) : () => { stopped++; };
  } }, callWS: async (message) => {
    calls.push(message);
    if (call) return call(message, state);
    const phases = { run: "starting_open", open: "starting_open", close: "starting_close", save: "saved", cancel: "cancelled", stop: "interrupted" };
    return { ...state, sequence: state.sequence + (message.action === "heartbeat" ? 0 : 1),
      phase: phases[message.action] || state.phase };
  } };
  await controller.open({ host, hass, entity: { entry_id: "one", entity_id: "cover.bedroom" }, revision: 4, mode, entity_ids, direction, resume, slats,
    t: translate, onSaved: () => { saved++; }, onCancel: () => { cancelled++; } });
  return { host, controller, calls, starts, push, counts: () => ({ stopped, saved, cancelled }) };
}

function chooseSave(host, mode) {
  const selector = host.querySelector("#cal-save-mode");
  selector.value = mode;
  selector.dispatchEvent(new dom.window.Event("change"));
}

test("measurement review shows the saved travel used by new and shared profiles", async () => {
  const { host, push } = await mount();
  push({ phase: "review", save_modes: ["new", "cover", "shared"], travel_cm: 150, reference_travel_cm: 200,
    values: { opening_time: 15, closing_time: 20 } });
  assert.match(host.querySelector("#cal-save-help").textContent, /150 cm/);
  chooseSave(host, "shared");
  assert.match(host.querySelector("#cal-save-help").textContent, /200 cm/);
  assert.match(host.querySelector("#cal-save-help").textContent, /riportata alla corsa/);
});

function submitReview(host) {
  host.querySelector("#cal-save").dispatchEvent(new dom.window.Event("submit", { cancelable: true }));
}

test("cover-only review saves without a name or browser timings and retains the selected direction", async () => {
  const { host, push, calls } = await mount({ direction: "opening" });
  push({ phase: "review", save_modes: ["new", "cover"], values: { opening_time: 12, closing_time: 30 } });
  chooseSave(host, "cover");
  assert.equal(host.querySelector('[value="shared"]').disabled, true);
  assert.equal(host.querySelector('[name="profile_name"]').disabled, true);
  assert.match(host.querySelector("#cal-save-help").textContent, /Solo le direzioni misurate/);
  submitReview(host); await tick();
  const save = calls.find((message) => message.action === "save");
  assert.equal(save.save_mode, "cover");
  for (const key of ["values", "name", "provenance", "direction", "profile_id"]) assert.equal(key in save, false);
});

const sharedImpact = { confirmation: "exact-proposal", after: { name: "<img src=x>" }, followers: [
  { entity_id: "cover.one", name: "<b>Camera</b>", available: true, changes: {
    opening: { before: 50, after: 12, overridden: false, override_removed: true },
    closing: { before: 30, after: 30, overridden: false } } },
  { entity_id: null, name: null, available: false, changes: {
    opening: { before: 44, after: 44, overridden: true },
    closing: { before: 30, after: 30, overridden: false } } },
] };

test("shared measurement needs a separate preview and confirmation; heartbeat preserves the preview", async () => {
  const { host, push, calls, controller } = await mount({ call: async (message, state) =>
    message.action === "preview_save" ? { ...state, save_preview: sharedImpact } : message.action === "save" ? { ...state, phase: "saved" } : state });
  push({ phase: "review", save_modes: ["new", "cover", "shared"], values: { opening_time: 12, closing_time: 30 } });
  chooseSave(host, "shared"); submitReview(host); await tick();
  assert.equal(calls.filter((m) => m.action === "save").length, 0);
  const box = host.querySelector("#cal-save-impact");
  assert.equal(box.hidden, false);
  assert.equal(box.querySelector("img"), null);
  assert.equal(box.querySelector("b"), null);
  assert.match(box.textContent, /valore personale rimosso/);
  assert.match(box.textContent, /valore personale conservato/);
  await controller._perform("heartbeat");
  assert.equal(box.hidden, false);
  assert.match(host.querySelector('#cal-save button[type="submit"]').textContent, /Conferma/);
  submitReview(host); await tick();
  const save = calls.find((m) => m.action === "save");
  assert.equal(save.confirmation, "exact-proposal");
  assert.equal(save.save_mode, "shared");
  assert.equal("profile" in save, false);
});

test("switching destination discards confirmation and preserves the new-profile name draft", async () => {
  const { host, push } = await mount({ call: async (_message, state) => ({ ...state, save_preview: sharedImpact }) });
  push({ phase: "review", save_modes: ["new", "cover", "shared"] });
  host.querySelector('[name="profile_name"]').value = "Bozza";
  chooseSave(host, "shared"); submitReview(host); await tick();
  chooseSave(host, "cover"); chooseSave(host, "shared");
  assert.equal(host.querySelector("#cal-save-impact").hidden, true);
  assert.match(host.querySelector('#cal-save button[type="submit"]').textContent, /Visualizza/);
  chooseSave(host, "new");
  assert.equal(host.querySelector('[name="profile_name"]').value, "Bozza");
});

test("stale or failed shared confirmation keeps measurements and requires another preview", async () => {
  const { host, push } = await mount({ call: async (message, state) => {
    if (message.action === "preview_save") return { ...state, save_preview: sharedImpact };
    if (message.action === "save") throw { code: "revision_conflict" };
    return state;
  } });
  push({ phase: "review", save_modes: ["new", "cover", "shared"], values: { opening_time: 12, closing_time: 30 } });
  chooseSave(host, "shared"); submitReview(host); await tick();
  submitReview(host); await tick();
  assert.equal(host.querySelector("#cal-save").hidden, false);
  assert.equal(host.querySelector("#cal-save-impact").hidden, true);
  assert.match(host.querySelector("#cal-values").textContent, /12.*30/);
  assert.equal(host.querySelector("#cal-reason").hidden, false);
});

test("late preview after Stop cannot restore a confirmation or review", async () => {
  const waiting = deferred();
  const { host, push } = await mount({ call: (message, state) => message.action === "preview_save" ? waiting.promise : state });
  push({ phase: "review", save_modes: ["new", "cover", "shared"] });
  chooseSave(host, "shared"); submitReview(host);
  assert.equal(host.querySelector("#cal-save-mode").disabled, true);
  push({ phase: "interrupted", sequence: 10 });
  waiting.resolve({ entry_id: "one", session_id: "session-one", sequence: 2, phase: "review", values: {}, save_preview: sharedImpact });
  await tick();
  assert.equal(host.querySelector("#cal-save").hidden, true);
  assert.equal(host.querySelector("#cal-save-impact").hidden, true);
});

afterEach(() => { for (const controller of instances.splice(0)) controller.close(); document.body.replaceChildren(); });
after(() => dom.window.close());

test("wizard starts a gateway-scoped subscription and waits for backend movement feedback", async () => {
  const { host, starts, calls, push } = await mount();
  assert.match(starts[0].client_id, /^\d+-\d+-\d+-\d+$/);
  assert.deepEqual(starts, [{ client_id: starts[0].client_id, type: "myhome/cover_calibration/start", entry_id: "one", entity_id: "cover.bedroom", revision: 4 }]);
  assert.equal(calls.length, 0);
  host.querySelector('[data-cal-action="open"]').click();
  await tick();
  assert.deepEqual(calls[0], { type: "myhome/cover_calibration/action", entry_id: "one", session_id: "session-one", sequence: 1, action: "open" });
  assert.equal(host.querySelector('[data-cal-action="endpoint"]').hidden, true);
  push({ phase: "opening", elapsed: 12.25 });
  assert.equal(host.querySelector('[data-cal-action="endpoint"]').hidden, false);
  assert.match(host.querySelector("#cal-elapsed").textContent, /: 12\.3 s$/); // Shown to a tenth of a second.
  assert.match(host.querySelector('[data-cal-action="endpoint"]').textContent, /Completamente aperta/);
});

test("review shows server times and only saves after explicit named confirmation", async () => {
  const { host, calls, push, counts } = await mount();
  push({ phase: "review", values: { opening_time: 20.5, closing_time: 40.5 }, stop_requested: true });
  assert.equal(calls.length, 0);
  assert.match(host.querySelector("#cal-values").textContent, /20.5.*40.5/);
  assert.equal(host.querySelector("#cal-stop-status").hidden, false);
  const form = host.querySelector("#cal-save");
  form.elements.profile_name.value = "Camera";
  form.dispatchEvent(new dom.window.Event("submit", { cancelable: true }));
  await tick();
  const save = calls.find((call) => call.action === "save");
  assert.equal(save.name, "Camera");
  assert.equal("values" in save, false);
  assert.equal(counts().saved, 1);
  assert.equal(counts().stopped, 1);
});

test("Stop stays available while another action waits and heartbeat cannot clear its busy state", async () => {
  const waiting = deferred();
  const { host, calls, controller, push } = await mount({ call: (message, state) =>
    message.action === "open" ? waiting.promise : Promise.resolve({ ...state }) });
  host.querySelector('[data-cal-action="open"]').click();
  await controller._perform("heartbeat");
  assert.equal(host.querySelector('[data-cal-action="open"]').disabled, true);
  host.querySelector("#cal-stop").click();
  await tick();
  assert.ok(calls.some((call) => call.action === "stop"));
  push({ phase: "interrupted", reason: "stopped", sequence: 5, values: {}, stop_requested: true });
  waiting.resolve({ entry_id: "one", session_id: "session-one", sequence: 2, phase: "starting_open", values: {} });
  await tick();
  assert.match(host.querySelector("#cal-phase").textContent, /interrotta/);
});

test("storage failure preserves the review and the user's draft", async () => {
  const { host, push } = await mount({ call: (message, state) => {
    if (message.action === "save") throw { code: "storage_error" };
    return state;
  } });
  push({ phase: "review", values: { opening_time: 20, closing_time: 40 } });
  const form = host.querySelector("#cal-save");
  form.elements.profile_name.value = "Da riprovare";
  form.elements.profile_name.focus();
  form.dispatchEvent(new dom.window.Event("submit", { cancelable: true }));
  await tick();
  assert.equal(form.elements.profile_name.value, "Da riprovare");
  assert.equal(document.activeElement, form.elements.profile_name);
  assert.match(host.querySelector("#cal-reason").textContent, /Salvataggio fallito/);
  assert.equal(form.hidden, false);
});

test("cancel invalidates a late subscription and queued responses", async () => {
  const waiting = deferred();
  // open() waits for subscribe; exercise the lifetime without awaiting that completion.
  const host = document.createElement("section"); document.body.append(host);
  const controller = new CoverCalibration(); instances.push(controller);
  let stopped = 0, saved = 0;
  const opening = controller.open({ host, entity: { entry_id: "one", entity_id: "cover.bedroom" }, revision: 0, t,
    hass: { connection: { subscribeMessage: () => waiting.promise }, callWS: async () => ({}) },
    onCancel: () => {}, onSaved: () => { saved++; } });
  assert.equal(host.querySelector("#cal-cancel").disabled, true);
  controller.close();
  waiting.resolve(() => { stopped++; });
  await opening;
  assert.equal(stopped, 1);
  assert.equal(saved, 0);
  assert.equal(controller._heartbeat, null);
});

test("lost heartbeat disables movement and retains Stop and Cancel", async () => {
  const { host, controller, calls, counts } = await mount({ call: (message, state) => {
    if (message.action === "heartbeat") throw { code: "disconnected" };
    return state;
  } });
  await controller._perform("heartbeat");
  assert.equal(host.querySelector('[data-cal-action="open"]').disabled, true);
  assert.equal(host.querySelector("#cal-stop").disabled, false);
  assert.match(host.querySelector("#cal-reason").textContent, /Connessione/);
  host.querySelector("#cal-cancel").click();
  await tick();
  assert.ok(calls.some((call) => call.action === "cancel"));
  assert.equal(counts().cancelled, 1);
  assert.equal(counts().stopped, 1);
});

test("every backend phase/refusal has English and Italian text", () => {
  for (const language of ["it", "en"]) {
    for (const key of Object.keys(translations.en).filter((key) => key.startsWith("cal") || key.includes("calibration_"))) {
      assert.ok(translations[language][key], `${language}.${key}`);
    }
  }
});


test("automatic mode requires explicit start, follows bus phases and never saves before review", async () => {
  const { host, starts, calls, push } = await mount({ mode: "automatic" });
  assert.equal(starts[0].mode, "automatic");
  assert.equal(calls.length, 0);
  const run = host.querySelector('[data-cal-action="run"]');
  assert.equal(run.hidden, false);
  assert.match(host.querySelector(".cal-help").textContent, /59.*65/);
  run.click(); await tick();
  assert.equal(calls[0].action, "run");
  push({ phase: "opening", run_index: 0, elapsed: 5 });
  assert.match(host.querySelector("#cal-phase").textContent, /1\/3/);
  assert.equal(host.querySelector('[data-cal-action="endpoint"]').hidden, true);
  push({ phase: "settling", run_index: 1, elapsed: null });
  assert.match(host.querySelector("#cal-phase").textContent, /Pausa/);
  push({ phase: "closing", run_index: 1, elapsed: 12 });
  assert.match(host.querySelector("#cal-phase").textContent, /2\/3/);
  push({ phase: "review", run_index: 2, elapsed: null, values: { opening_time: 20, closing_time: 22 } });
  assert.equal(host.querySelector("#cal-save").hidden, false);
  assert.equal(calls.filter((c) => c.action === "save").length, 0);
  const form = host.querySelector("#cal-save"); form.elements.profile_name.value = "Automatica";
  form.dispatchEvent(new dom.window.Event("submit", { cancelable: true })); await tick();
  assert.equal(calls.find((c) => c.action === "save").name, "Automatica");
});

test("automatic cutoff removes save controls and retains Stop and Cancel", async () => {
  const { host, calls, push } = await mount({ mode: "automatic" });
  push({ phase: "interrupted", reason: "automatic_cutoff", values: {}, stop_requested: true });
  assert.match(host.querySelector("#cal-reason").textContent, /Misure scartate/);
  assert.equal(host.querySelector("#cal-save").hidden, true);
  assert.equal(host.querySelector('[data-cal-action="run"]').hidden, true);
  assert.equal(host.querySelector("#cal-stop").disabled, false);
  host.querySelector("#cal-cancel").click(); await tick();
  assert.ok(calls.some((c) => c.action === "cancel"));
});


test("batch review preserves profile names through heartbeat and failed atomic save", async () => {
  let fail = true;
  const ids = ["cover.kitchen", "cover.bedroom"];
  const { host, starts, calls, push, controller } = await mount({ mode: "automatic", entity_ids: ids,
    call: (message, state) => {
      if (message.action === "save" && fail) throw { code: "storage_error" };
      return { ...state, phase: message.action === "save" ? "saved" : state.phase };
    } });
  assert.deepEqual(starts[0], { client_id: starts[0].client_id, type: "myhome/cover_calibration/batch_start", entry_id: "one", entity_ids: ids, revision: 4 });
  assert.equal(calls.length, 0);
  assert.match(host.querySelector("#cal-targets").textContent, /cover.kitchen.*cover.bedroom/);
  push({ phase: "between_covers", results: [{ index: 0, values: { opening_time: 20, closing_time: 22 } }] });
  assert.match(host.querySelector("#cal-phase").textContent, /prossima tapparella/);
  assert.equal(host.querySelector("#cal-save").hidden, true);
  push({ phase: "review", cover_index: 1, results: [
    { index: 0, values: { opening_time: 20, closing_time: 22 } },
    { index: 1, values: { opening_time: 21, closing_time: 23 } },
  ] });
  const form = host.querySelector("#cal-save");
  const inputs = [...form.querySelectorAll("[data-batch-name]")];
  assert.equal(inputs.length, 2);
  assert.equal(form.elements.profile_name.disabled, true);
  inputs[0].value = "Cucina"; inputs[1].value = "Camera"; inputs[1].focus();
  await controller._perform("heartbeat");
  assert.equal(document.activeElement, inputs[1]);
  form.dispatchEvent(new dom.window.Event("submit", { cancelable: true })); await tick();
  assert.equal(form.hidden, false);
  assert.equal(inputs[1].value, "Camera");
  const saved = calls.find((call) => call.action === "save");
  assert.deepEqual(saved.names, ["Cucina", "Camera"]);
  assert.equal("values" in saved, false); assert.equal("entity_ids" in saved, false);
  fail = false;
  form.dispatchEvent(new dom.window.Event("submit", { cancelable: true })); await tick();
  assert.equal(calls.filter((call) => call.action === "save").length, 2);
});

test("batch interruptions discard the review and escape target names", async () => {
  const { host, push } = await mount({ mode: "automatic", entity_ids: ["cover.one"] });
  push({ targets: [{ entity_id: "cover.one", name: "<img src=x onerror=alert(1)>" }],
    phase: "review", results: [{ index: 0, values: { opening_time: 20, closing_time: 22 } }] });
  assert.equal(host.querySelector("img"), null);
  push({ phase: "interrupted", reason: "automatic_cutoff", results: [], values: {}, stop_requested: true });
  assert.equal(host.querySelector("#cal-save").hidden, true);
  assert.match(host.querySelector("#cal-targets").textContent, /Misura scartata/);
  assert.equal(host.querySelector("#cal-stop").disabled, false);
});


for (const direction of ["opening", "closing"]) {
  test(`quick ${direction} confirms only the selected leg and distinguishes the retained value`, async () => {
    const { host, starts, calls, push } = await mount({ direction });
    assert.equal(starts[0].direction, direction);
    assert.equal(calls.length, 0);
    assert.equal(host.querySelector(".cal-steps").hidden, true);
    assert.match(host.querySelector(".cal-help").textContent, direction === "opening" ? /completamente chiusa/ : /completamente aperta/);
    assert.equal(host.querySelector('[data-cal-action="open"]').hidden, direction !== "opening");
    assert.equal(host.querySelector('[data-cal-action="close"]').hidden, direction !== "closing");
    push({ phase: direction, elapsed: 12.75 });
    host.querySelector('[data-cal-action="endpoint"]').click(); await tick();
    assert.equal(calls[0].action, "endpoint");
    push({ phase: "review", values: { opening_time: 12.75, closing_time: 32.25 } });
    assert.match(host.querySelector("#cal-values").textContent, /Misurato in questa sessione/);
    assert.match(host.querySelector("#cal-values").textContent, /Valore configurato conservato/);
    assert.equal(host.querySelector('[data-cal-action="open"]').hidden, true);
    assert.equal(host.querySelector('[data-cal-action="close"]').hidden, true);
    const form = host.querySelector("#cal-save"); form.elements.profile_name.value = "Nuova copia";
    form.dispatchEvent(new dom.window.Event("submit", { cancelable: true })); await tick();
    const save = calls.find((message) => message.action === "save");
    assert.equal(save.name, "Nuova copia");
    assert.equal("values" in save, false); assert.equal("direction" in save, false);
  });
}

test("navigation detaches a recoverable session; explicit cancel releases it using its attachment", async () => {
  const { controller, push, calls, counts } = await mount();
  push({ recoverable: true, attached: true, attachment: "current-owner", phase: "review", values: { opening_time: 20, closing_time: 30 } });
  await controller.close();
  assert.equal(calls.at(-1).action, "detach");
  assert.equal(calls.at(-1).attachment, "current-owner");
  assert.equal(counts().stopped, 1);
  const other = await mount();
  other.push({ recoverable: true, attached: true, attachment: "other-owner" });
  other.host.querySelector("#cal-cancel").click(); await tick();
  assert.equal(other.calls.at(-1).action, "cancel");
  assert.equal(other.calls.at(-1).attachment, "other-owner");
  assert.equal(other.counts().cancelled, 1);
});

test("recover partial review from backend without starting or replaying movement", async () => {
  const { host, starts, calls } = await mount({ resume: { session_id: "retained", mode: "guided", direction: "closing",
    phase: "review", values: { opening_time: 25, closing_time: 29 }, save_modes: ["new", "cover"] } });
  assert.equal(starts[0].type, "myhome/cover_calibration/resume");
  assert.equal(starts[0].session_id, "retained");
  assert.equal("revision" in starts[0], false);
  assert.equal(host.querySelector("#cal-save").hidden, false);
  assert.match(host.querySelector("#cal-values").textContent, /25.*29/);
  assert.equal(calls.length, 0);
  chooseSave(host, "cover"); submitReview(host); await tick();
  assert.equal(calls[0].attachment, "new-controller");
  assert.equal(calls[0].save_mode, "cover");
});

test("recover automatic batch review and keep all target names and measurements", async () => {
  const { host, starts, calls } = await mount({ resume: { session_id: "batch-retained", mode: "automatic", batch: true,
    phase: "review", targets: [{ entity_id: "cover.a", name: "Kitchen" }, { entity_id: "cover.b", name: "Bedroom" }],
    results: [{ index: 0, values: { opening_time: 20, closing_time: 22 } }, { index: 1, values: { opening_time: 21, closing_time: 23 } }] } });
  assert.equal(starts[0].type, "myhome/cover_calibration/resume");
  assert.equal(host.querySelectorAll("[data-batch-name]").length, 2);
  assert.equal(host.querySelector("#cal-save-mode").closest("label").hidden, true);
  assert.equal(calls.length, 0);
  submitReview(host); await tick();
  assert.deepEqual(calls[0].names, ["Kitchen", "Bedroom"]);
});

test("heartbeat expiry disables controls and exposes explicit recovery without auto-run", async () => {
  const { host, push, calls, controller } = await mount();
  push({ recoverable: true, attached: false, attachment: "expired-owner" });
  assert.equal(host.querySelector('[data-cal-action="open"]').disabled, true);
  assert.equal(host.querySelector("#cal-reconnect").hidden, false);
  assert.equal(calls.length, 0);
  push({ attached: true, attachment: "fresh-owner" });
  assert.equal(host.querySelector('[data-cal-action="open"]').disabled, false);
  assert.equal(host.querySelector("#cal-reconnect").hidden, true);
  await controller._perform("heartbeat");
  assert.equal(calls.at(-1).attachment, "fresh-owner");
});

test("a late heartbeat error from the old attachment cannot disable the recovered controller", async () => {
  let reject;
  const waiting = new Promise((_resolve, fail) => { reject = fail; });
  const { host, push, controller } = await mount({ call: () => waiting });
  push({ recoverable: true, attached: true, attachment: "old" });
  const heartbeat = controller._perform("heartbeat");
  push({ attachment: "new", attached: true });
  reject({ code: "calibration_expired" }); await heartbeat;
  assert.equal(host.querySelector('[data-cal-action="open"]').disabled, false);
  assert.equal(host.querySelector("#cal-reconnect").hidden, true);
  assert.equal(host.querySelector("#cal-reason").hidden, true);
});

test("geometry wizard sends only user observations, waits for Stop, and keeps the reading draft", async () => {
  const { host, push, calls, starts } = await mount({ mode: "geometry" });
  assert.equal(starts[0].mode, "geometry");
  push({ phase: "briefing", step: "lift", save_modes: ["new"], can_repeat: false });
  assert.equal(host.querySelector('[data-cal-action="next"]').hidden, false);
  assert.equal(host.querySelector('[data-cal-action="open"]').hidden, true);
  assert.match(host.querySelector("#cal-phase").textContent, /lamelle/);
  host.querySelector('[data-cal-action="next"]').click(); await tick();
  assert.equal(calls.at(-1).action, "next");
  push({ phase: "opening", step: "lift" });
  assert.equal(host.querySelector('[data-cal-action="lift"]').hidden, false);
  assert.equal(host.querySelector('[data-cal-action="endpoint"]').hidden, true);
  host.querySelector('[data-cal-action="lift"]').click(); await tick();
  assert.equal(calls.at(-1).action, "lift");
  assert.equal("elapsed" in calls.at(-1), false);
  push({ phase: "geometry_wait_stop", stop_requested: true });
  assert.equal(host.querySelector("#cal-reading").hidden, true);
  push({ phase: "reading", reading_kind: "lift", can_repeat: true });
  const form = host.querySelector("#cal-reading");
  assert.equal(form.hidden, false);
  assert.equal(form.elements.reading_cm.min, "0");
  form.elements.reading_cm.value = "2.5";
  push({});
  assert.equal(form.elements.reading_cm.value, "2.5");
  form.dispatchEvent(new dom.window.Event("submit", { cancelable: true })); await tick();
  assert.equal(calls.at(-1).action, "reading");
  assert.equal(calls.at(-1).reading_cm, 2.5);
  for (const key of ["geometry", "values", "elapsed", "provenance"]) assert.equal(key in calls.at(-1), false);
  host.querySelector("#cal-repeat").click(); await tick();
  assert.equal(calls.at(-1).action, "repeat");
  push({ phase: "reading", step: "half_open", reading_kind: "half_open", expected_cm: 100, reading_range: { min_cm: 80, max_cm: 100 } });
  assert.equal(form.elements.reading_cm.value, "");
  assert.equal(form.elements.reading_cm.min, "0.1");
  assert.match(host.querySelector("#cal-expected").textContent, /fra 80 e 100 cm/);
  assert.match(host.querySelector("#cal-expected").textContent, /Scrivi quello che dice il metro/);
});

test("geometry review exposes all measured values, limits save destinations and names unverified accuracy", async () => {
  const { host, push, calls } = await mount({ mode: "geometry" });
  push({ phase: "review", step: "half_close", values: { opening_time: 22, closing_time: 20 },
    geometry: { slat_time_s: 2, opening_roll: 2, closing_roll: 3 }, travel_cm: 200,
    save_modes: ["new"], can_repeat: true, independent_check: false, accuracy: null });
  assert.match(host.querySelector("#cal-values").textContent, /Tempo lamelle.*2.*Rullo in apertura.*2.*Rullo in chiusura.*3/);
  assert.match(host.querySelector("#cal-save-help").textContent, /Precisione non verificata/);
  assert.equal(host.querySelector('#cal-save-mode [value="cover"]').disabled, true);
  assert.equal(host.querySelector('#cal-save-mode [value="shared"]').disabled, true);
  host.querySelector('[name="profile_name"]').value = "Camera";
  submitReview(host); await tick();
  const save = calls.find((row) => row.action === "save");
  assert.equal(save.name, "Camera");
  assert.equal(save.save_mode, "new");
  assert.equal("geometry" in save, false);
});

test("geometry recovery restores its reading screen without starting any movement", async () => {
  const { host, starts, calls } = await mount({ resume: {
    session_id: "geometry-session", mode: "geometry", step: "opening", phase: "reading",
    reading_kind: "travel", save_modes: ["new"], can_repeat: true } });
  assert.equal(starts[0].type, "myhome/cover_calibration/resume");
  assert.equal(host.querySelector("#cal-reading").hidden, false);
  assert.match(host.querySelector("#cal-phase").textContent, /corsa completa/);
  assert.equal(calls.length, 0);
});

test("a cover without slats is started as such and never shown slat steps or a slat time", async () => {
  assert.equal("slats" in (await mount({ mode: "geometry" })).starts[0], false, "existing clients keep their start message");
  assert.equal("slats" in (await mount({ mode: "guided", slats: false })).starts[0], false);
  const { host, push, starts } = await mount({ mode: "geometry", slats: false });
  assert.equal(starts[0].slats, false);
  const phase = host.querySelector("#cal-phase"), figure = host.querySelector(".cal-visual");
  push({ phase: "briefing", step: "home", slats: false, save_modes: ["new"], can_repeat: false });
  assert.equal(phase.textContent, t("calBrief_home_no_slats"));
  assert.equal(figure.querySelector(".cal-visual-label").textContent, t("calVisual_targetClosedNoSlats"));
  push({ step: "closing" });
  assert.equal(phase.textContent, t("calBrief_closing_no_slats"));
  for (const step of ["home", "closing"]) {
    push({ phase: "closing", step });
    assert.equal(figure.querySelector(".cal-visual-label").textContent, t("calVisual_closingNoSlats"));
  }
  push({ phase: "closing", step: "home", slats: true });
  assert.equal(figure.querySelector(".cal-visual-label").textContent, t("calVisual_closing"));
  push({ phase: "briefing", slats: false });
  push({ step: "half_open" });
  assert.equal(phase.textContent, t("calBrief_half_open"));
  for (const language of ["en", "it"]) {
    for (const key of ["calBrief_home_no_slats", "calBrief_closing_no_slats", "calVisual_targetClosedNoSlats", "calVisual_closingNoSlats"]) {
      assert.doesNotMatch(translations[language][key], /slat|lamell/i, `${language}.${key}`);
    }
  }
  push({ phase: "review", step: "half_close", values: { opening_time: 22, closing_time: 20 },
    geometry: { slat_time_s: 0, opening_roll: 2, closing_roll: 3 }, travel_cm: 200, can_repeat: true });
  const values = host.querySelector("#cal-values").textContent;
  assert.match(values, /Senza lamelle · Rullo in apertura: 2 · Rullo in chiusura: 3/);
  assert.doesNotMatch(values, /Tempo lamelle/);
  assert.equal(host.querySelector("#cal-save-help").textContent, t("calGeometryReviewNoSlats"));
  push({ slats: true, geometry: { slat_time_s: 2, opening_roll: 2, closing_roll: 3 } });
  assert.match(host.querySelector("#cal-values").textContent, /Tempo lamelle \(s\): 2 · Rullo/);
  assert.equal(host.querySelector("#cal-save-help").textContent, t("calGeometryReview"));
});

test("the lift-off reading states its limit from the session and a repeat says why, with its attempt", async () => {
  const { host, push, calls } = await mount({ mode: "geometry", call: (message, state) => {
    if (message.action === "reading") throw { code: "invalid_gap" };
    return state;
  } });
  const phase = host.querySelector("#cal-phase"), notice = host.querySelector("#cal-lift-repeat");
  const limits = { touching_cm: 1, gap_warn_cm: 10, max_gap_cm: 20, lift_repeat: true, lift_attempts: 1, still_resting: false, gap_warning: false };
  push({ phase: "reading", step: "lift", reading_kind: "lift", can_repeat: true, save_modes: ["new"], ...limits });
  assert.equal(phase.textContent, "Misura in cm il distacco del bordo inferiore dalla base. Se il bordo non si è sollevato di almeno 1 cm, inserisci 0: il bordo conta come ancora appoggiato e la corsa si ripete.");
  assert.equal(notice.hidden, true);
  push({ touching_cm: 1.5 });
  assert.match(phase.textContent, /almeno 1\.5 cm, inserisci 0/);
  push({ touching_cm: 1, lift_repeat: false });
  assert.match(phase.textContent, /Sotto 1 cm il bordo conta come ancora appoggiato: ripeti la misura\./);
  const form = host.querySelector("#cal-reading");
  form.elements.reading_cm.value = "60";
  form.dispatchEvent(new dom.window.Event("submit", { cancelable: true })); await tick();
  assert.equal(calls.at(-1).reading_cm, 60);
  assert.equal(host.querySelector("#cal-reason").textContent, "Inserisci un distacco fra 1 e 20 cm, misurato dalla base al bordo inferiore. Se il bordo è salito di più, usa Ripeti.");
  push({ phase: "briefing", step: "reset", lift_repeat: true, lift_attempts: 2, still_resting: true });
  assert.equal(notice.hidden, false);
  assert.equal(notice.textContent, "Il bordo era ancora appoggiato: la corsa di distacco si ripete (tentativo 2)");
  const announced = notice.firstChild;
  push({});  // A heartbeat or an unchanged view does not announce it again.
  assert.equal(notice.firstChild, announced);
  push({ lift_attempts: 3 });  // Another run still resting, back at the same briefing.
  assert.match(notice.textContent, /\(tentativo 3\)$/);
  push({ phase: "briefing", step: "reset", still_resting: false });
  assert.equal(notice.hidden, true);
  assert.equal(notice.textContent, "");
  assert.equal(translations.en.calLiftRepeated.replace("{lift_attempts}", "2"), "The edge was still resting: the lift-off run is repeated (attempt 2)");
  assert.match(translations.en.profileError_invalid_gap, /If the edge rose further, use Repeat\.$/);
});

test("a wide lift-off gap is accepted with a warning that offers Repeat and goes away with it", async () => {
  const { host, push, calls } = await mount({ mode: "geometry" });
  const notice = host.querySelector("#cal-lift-repeat"), repeat = host.querySelector("#cal-repeat");
  const limits = { touching_cm: 1, gap_warn_cm: 10, max_gap_cm: 20, lift_repeat: true, lift_attempts: 1, still_resting: false };
  push({ phase: "briefing", step: "reset", save_modes: ["new"], can_repeat: false, gap_warning: false, ...limits });
  assert.equal(notice.hidden, true);
  push({ can_repeat: true, gap_warning: true });
  assert.equal(notice.hidden, false);
  assert.equal(notice.textContent, "Il bordo si è sollevato di 10 cm o più prima dell’arresto: il tempo lamelle sarà meno preciso. Ripeti la corsa di distacco per un risultato migliore, oppure continua.");
  assert.equal(host.querySelector('[data-cal-action="next"]').hidden, false, "continuing stays possible");
  assert.equal(repeat.hidden, false);
  repeat.click(); await tick();
  assert.equal(calls.at(-1).action, "repeat");
  push({ can_repeat: false, gap_warning: false, lift_attempts: 2 });
  assert.equal(notice.hidden, true);
  assert.equal(notice.textContent, "");
  assert.match(translations.en.calGapWarning, /^The edge rose \{gap_warn_cm\} cm or more before the stop/);
});

test("tape readings take a comma or a point and never send an empty or partial number", async () => {
  const { host, push, calls } = await mount({ mode: "geometry" });
  const form = host.querySelector("#cal-reading"), input = form.elements.reading_cm, reason = host.querySelector("#cal-reason");
  assert.equal(input.type, "text");
  assert.equal(input.inputMode, "decimal");
  const submit = async (value) => {
    input.value = value;
    form.dispatchEvent(new dom.window.Event("submit", { cancelable: true })); await tick();
  };
  for (const [step, reading_kind] of [["lift", "lift"], ["opening", "travel"], ["half_open", "half_open"]]) {
    push({ phase: "reading", step, reading_kind, can_repeat: true, save_modes: ["new"] });
    for (const [value, expected] of [["2,8", 2.8], ["2.8", 2.8], [" 12,5 ", 12.5], ["0", 0], [",5", .5]]) {
      await submit(value);
      assert.equal(calls.at(-1).action, "reading");
      assert.equal(calls.at(-1).reading_cm, expected, `${step} ${value}`);
      assert.equal(input.getAttribute("aria-invalid"), "false");
    }
    for (const value of ["", "   ", "abc", "2,8 cm", "1e3", "0x10", "1,2,3", "-"]) {
      const sent = calls.length;
      await submit(value);
      assert.equal(calls.length, sent, `${step} "${value}" must not be sent`);
      assert.equal(input.getAttribute("aria-invalid"), "true");
      assert.equal(reason.hidden, false);
      assert.equal(reason.textContent, "Inserisci la lettura in cm come numero; i decimali possono seguire una virgola o un punto.");
    }
  }
});

test("the lift-off gap is checked while typed; the button waits for a value in range", async () => {
  const { host, push, calls } = await mount({ mode: "geometry" });
  const form = host.querySelector("#cal-reading"), input = form.elements.reading_cm;
  const button = form.querySelector('button[type="submit"]'), range = host.querySelector("#cal-reading-range");
  const type = (value) => { input.value = value; input.dispatchEvent(new dom.window.Event("input", { bubbles: true })); };
  push({ phase: "reading", step: "lift", reading_kind: "lift", can_repeat: true, save_modes: ["new"], touching_cm: 1, max_gap_cm: 20 });
  for (const [value, outside] of [["25", true], ["12", false], ["0", false], ["20,0", false], ["20,1", true], ["-1", true], ["", false], ["2,", false]]) {
    type(value);
    assert.equal(button.disabled, outside, value);
    assert.equal(range.hidden, !outside, value);
    assert.equal(range.textContent, outside ? "Inserisci un distacco fra 1 e 20 cm, misurato dalla base al bordo inferiore. Se il bordo è salito di più, usa Ripeti." : "", value);
  }
  type("25");
  push({});  // A heartbeat keeps the check.
  assert.equal(button.disabled, true);
  type("12");
  push({ recoverable: true, attached: false, attachment: "old" });  // A lost connection still disables it.
  assert.equal(button.disabled, true);
  push({ attached: true, attachment: "new" });
  assert.equal(button.disabled, false);
  push({ step: "opening", reading_kind: "travel" });
  type("25");
  assert.equal(button.disabled, false, "other readings are not range-checked here");
  assert.equal(range.hidden, true);
  assert.equal(calls.length, 0);
});

test("an intermediate reading shows the range the fit accepts, inside its true ends, instead of a halfway reference", async () => {
  const { host, push } = await mount({ mode: "geometry", language: "it" });
  const hint = host.querySelector("#cal-expected");
  const reading = { phase: "reading", reading_kind: "half_close", step: "half_close", can_repeat: true, save_modes: ["new"], expected_cm: 55 };
  push({ ...reading, reading_range: { min_cm: 36.666666666666664, max_cm: 54.87 } });
  assert.equal(hint.textContent, "Per questa tapparella la lettura attesa è fra 36,7 e 54,8 cm. Scrivi quello che dice il metro.");
  push({ reading_range: { min_cm: 110 / 3, max_cm: 55 } });
  assert.equal(hint.textContent, "Per questa tapparella la lettura attesa è fra 36,7 e 55 cm. Scrivi quello che dice il metro.");
  assert.doesNotMatch(hint.textContent, /metà corsa|Riferimento/);
  // Narrower than a tenth: hundredths, inside the true ends; narrower than a hundredth: the exact ends.
  for (const [range, text] of [[{ min_cm: 55.02, max_cm: 55.08 }, "fra 55,02 e 55,08 cm"], [{ min_cm: 55.01, max_cm: 55.1 }, "fra 55,01 e 55,1 cm"],
    [{ min_cm: 55.023, max_cm: 55.027 }, "fra 55,023 e 55,027 cm"], [{ min_cm: 54.95, max_cm: 55.05 }, "fra 54,95 e 55,05 cm"]]) {
    push({ reading_range: range });
    assert.match(hint.textContent, new RegExp(text), JSON.stringify(range));
  }
  push({ reading_range: null });
  assert.equal(hint.textContent, "", "no range, no hint: the old halfway reference is gone");
  push({ step: "lift", reading_kind: "lift" });
  assert.equal(hint.textContent, "");
  const english = await mount({ mode: "geometry", language: "en", translate: (key) => translations.en[key] || key });
  english.push({ ...reading, step: "half_open", reading_kind: "half_open", reading_range: { min_cm: 36.666666666666664, max_cm: 54.87 } });
  assert.equal(english.host.querySelector("#cal-expected").textContent, "For this cover the reading should be between 36.7 and 54.8 cm. Enter what the tape says.");
});

test("an intermediate reading outside the range is held back while typed and the refusal names the range", async () => {
  const { host, push, calls } = await mount({ mode: "geometry", language: "it",
    call: (message, state) => { if (message.action === "reading") throw { code: "reading_out_of_range" }; return state; } });
  const form = host.querySelector("#cal-reading"), input = form.elements.reading_cm;
  const button = form.querySelector('button[type="submit"]'), range = host.querySelector("#cal-reading-range");
  const type = (value) => { input.value = value; input.dispatchEvent(new dom.window.Event("input", { bubbles: true })); };
  const refusal = (value) => `La lettura (${value} cm) è fuori dall’intervallo ammesso (36,7–54,8 cm). Controlla il riferimento e ripeti il passaggio. Se i tempi misurati prima erano errati, annulla e riparti con la misura guidata.`;
  push({ phase: "reading", step: "half_open", reading_kind: "half_open", can_repeat: true, save_modes: ["new"],
    reading_range: { min_cm: 36.666666666666664, max_cm: 54.87 } });
  for (const [value, outside] of [["55", true], ["54,87", false], ["54,85", false], ["36,6", true], ["36,666666666666664", false],
    ["45", false], ["120,5", true], ["54,95", true], ["", false], ["45,", false]]) {
    type(value);
    assert.equal(button.disabled, outside, value);
    assert.equal(range.hidden, !outside, value);
    assert.equal(range.textContent, outside ? refusal(value) : "", value);
  }
  type("55");
  push({});  // A heartbeat keeps the check.
  assert.equal(button.disabled, true);
  type("45");
  form.dispatchEvent(new dom.window.Event("submit", { cancelable: true })); await tick();
  assert.equal(calls.at(-1).reading_cm, 45);
  // Home Assistant stays the authority: its refusal names the reading sent and the same range.
  assert.equal(host.querySelector("#cal-reason").hidden, false);
  assert.equal(host.querySelector("#cal-reason").textContent, refusal("45"));
  assert.match(translations.en.profileError_reading_out_of_range, /^The reading \({reading_cm} cm\) is outside the accepted range \({min_cm}–{max_cm} cm\)\. Check the measurement reference and repeat the step\./);
  push({ reading_range: null });
  type("500");
  assert.equal(button.disabled, false, "without a range only Home Assistant checks the reading");
  assert.equal(range.hidden, true);
});

test("the travel reading offers the travel already saved for the cover, and sends only on confirmation", async () => {
  const { host, push, calls } = await mount({ mode: "geometry" });
  const form = host.querySelector("#cal-reading"), input = form.elements.reading_cm;
  push({ phase: "reading", step: "opening", reading_kind: "travel", can_repeat: true, save_modes: ["new"], saved_travel_cm: 110 });
  assert.equal(input.value, "110");
  assert.equal(calls.length, 0, "a prefilled value is never sent by itself");
  input.value = "112,5";
  push({});
  assert.equal(input.value, "112,5");
  form.dispatchEvent(new dom.window.Event("submit", { cancelable: true })); await tick();
  assert.equal(calls.at(-1).reading_cm, 112.5);
  push({ step: "half_open", reading_kind: "half_open" });
  assert.equal(input.value, "", "only the travel is prefilled");
  push({ step: "opening", reading_kind: "travel", saved_travel_cm: null });
  assert.equal(input.value, "");
});

test("review values are displayed rounded while the view keeps full precision", async () => {
  for (const [value, unit, text] of [[14.538674880051985, "s", "14.5"], [14.477566485991701, "s", "14.5"], [20, "s", "20"],
    [110, "cm", "110"], [49.5, "cm", "49.5"], [42.46, "cm", "42.5"], [2.6638, "s", "2.7"], [1.7397, "roll", "1.74"],
    [2.6596, "roll", "2.66"], [3, "roll", "3"], [null, "s", "—"], [undefined, "cm", "—"]]) {
    assert.equal(shown(value, unit), text, `${value} ${unit}`);
  }
  const { host, push } = await mount({ mode: "geometry" });
  push({ phase: "review", step: "half_close", values: { opening_time: 14.538674880051985, closing_time: 14.477566485991701 },
    geometry: { slat_time_s: 2.6638, opening_roll: 1.7397, closing_roll: 2.6596 }, travel_cm: 112.46, save_modes: ["new"], can_repeat: true });
  const values = host.querySelector("#cal-values").textContent;
  assert.match(values, /: 14\.5 · .*: 14\.5 · .*: 112\.5 cm · Tempo lamelle \(s\): 2\.7 · Rullo in apertura: 1\.74 · Rullo in chiusura: 2\.66$/);
  const batch = await mount({ mode: "automatic", entity_ids: ["cover.one"] });
  batch.push({ phase: "review", results: [{ index: 0, values: { opening_time: 14.538674880051985, closing_time: 14.477566485991701 } }] });
  assert.match(batch.host.querySelector("#cal-targets").textContent, /14\.5 \/ 14\.5 s/);
  assert.match(batch.host.querySelector("#cal-batch-review").textContent, /14\.5 \/ 14\.5 s/);
});

test("rounded values take a decimal comma in Italian", async () => {
  for (const [value, unit, text] of [[14.65187786286696, "s", "14,7"], [1.0819, "roll", "1,08"], [112.46, "cm", "112,5"], [110, "cm", "110"], [null, "s", "—"]]) {
    assert.equal(shown(value, unit, "it"), text, `${value} ${unit}`);
  }
  assert.equal(shown(14.65187786286696, "s", "en"), "14.7");
  assert.equal(shown(14.65187786286696, "s", "not a language"), "14.7");
  const { host, push } = await mount({ mode: "geometry", language: "it" });
  push({ phase: "review", step: "half_close", values: { opening_time: 14.538674880051985, closing_time: 14.477566485991701 },
    geometry: { slat_time_s: 2.6638, opening_roll: 1.7397, closing_roll: 2.6596 }, travel_cm: 112.46, save_modes: ["new"], can_repeat: true });
  assert.match(host.querySelector("#cal-values").textContent, /: 14,5 · .*: 14,5 · .*: 112,5 cm · Tempo lamelle \(s\): 2,7 · Rullo in apertura: 1,74 · Rullo in chiusura: 2,66$/);
});

test("positioning runs show what to confirm and no elapsed time; measured runs keep it", async () => {
  const { host, push } = await mount({ mode: "geometry" });
  const phase = host.querySelector("#cal-phase"), elapsed = host.querySelector("#cal-elapsed");
  push({ phase: "briefing", step: "home", save_modes: ["new"], can_repeat: false });
  for (const [state, text] of [[{ phase: "closing", step: "home" }, "calPositionClose"], [{ phase: "closing", step: "reset" }, "calPositionClose"],
    [{ phase: "opening", step: "top" }, "calPositionOpen"], [{ phase: "closing", step: "home", slats: false }, "calPositionClose_no_slats"],
    [{ phase: "opening", step: "half_open" }, "calHalfPositioning"], [{ phase: "closing", step: "half_close" }, "calHalfPositioning"],
    [{ phase: "opening", step: "half_open", slats: false }, "calHalfPositioning"],
    [{ phase: "geometry_wait_stop", step: "half_open" }, "calGeometryWaitStop"], [{ phase: "geometry_wait_stop", step: "half_close" }, "calGeometryWaitStop"]]) {
    push({ ...state, elapsed: 3.5 });
    assert.equal(elapsed.hidden, true, `${state.step} measures nothing`);
    assert.equal(phase.textContent, t(text));
  }
  for (const [state, text] of [[{ phase: "opening", step: "opening" }, "calEndpointRunning"], [{ phase: "closing", step: "closing" }, "calEndpointRunning"],
    [{ phase: "opening", step: "lift" }, "calLiftRunning"], [{ phase: "geometry_wait_stop", step: "lift" }, "calGeometryWaitStop"]]) {
    push({ ...state, slats: true, elapsed: 3.5 });
    assert.equal(elapsed.hidden, false, `${state.step} is measured`);
    assert.match(elapsed.textContent, /3\.5 s/);
    assert.equal(phase.textContent, t(text));
  }
  assert.doesNotMatch(translations.en.calPositionClose_no_slats + translations.it.calPositionClose_no_slats, /slat|lamell/i);
  assert.equal(translations.it.calHalfPositioning, "La tapparella si sta posizionando e si ferma da sola: non c’è nulla da premere. Attendi che si sia fermata prima di avvicinarti con il metro.");
  assert.doesNotMatch(translations.en.calHalfPositioning + translations.it.calHalfPositioning, /slat|lamell/i);
  const guided = await mount();
  guided.push({ phase: "closing", elapsed: 3.5 });
  assert.equal(guided.host.querySelector("#cal-elapsed").hidden, false, "other modes are unchanged");
});

for (const mode of ["geometry", "guided", "automatic"]) {
  test(`${mode} renders with HA's non-iterable scoped form controls`, async () => {
    // @webcomponents/scoped-custom-element-registry supplies named/indexed form
    // controls but its Symbol.iterator throws "Method not implemented.".
    const prototype = dom.window.HTMLFormElement.prototype;
    const original = Object.getOwnPropertyDescriptor(prototype, "elements");
    Object.defineProperty(prototype, "elements", { ...original, get() {
      return new Proxy(original.get.call(this), { get(target, key) {
        if (key === Symbol.iterator) return () => { throw new Error("Method not implemented."); };
        return Reflect.get(target, key, target);
      } });
    } });
    try {
      const { host, push, controller, calls } = await mount({ mode });
      assert.equal(host.querySelector("#cal-reason").hidden, true, "initial subscription must render without an exception");
      if (mode !== "geometry") return;
      push({ phase: "briefing", step: "lift", can_repeat: false, save_modes: ["new"] });
      assert.equal(host.querySelector('[data-cal-action="next"]').hidden, false);
      push({ phase: "reading", step: "lift", reading_kind: "lift", can_repeat: true });
      const form = host.querySelector("#cal-reading");
      const input = form.querySelector("input"), submit = form.querySelector('button[type="submit"]');
      input.value = "2.5";
      controller._busy = true; controller._render();
      assert.equal(input.disabled, true); assert.equal(submit.disabled, true);
      controller._busy = false; controller._lost = true; controller._render();
      assert.equal(input.disabled, true); assert.equal(submit.disabled, true);
      controller._lost = false; controller._render();
      assert.equal(input.disabled, false); assert.equal(submit.disabled, false);
      assert.equal(input.value, "2.5");
      form.dispatchEvent(new dom.window.Event("submit", { cancelable: true })); await tick();
      assert.equal(calls.at(-1).action, "reading");
      assert.equal(calls.at(-1).reading_cm, 2.5);
    } finally {
      Object.defineProperty(prototype, "elements", original);
    }
  });
}

function withStorage(storage, run) {
  const original = Object.getOwnPropertyDescriptor(globalThis, "sessionStorage");
  Object.defineProperty(globalThis, "sessionStorage", { configurable: true, get: () => {
    if (storage instanceof Error) throw storage;
    return storage;
  } });
  const restore = () => original ? Object.defineProperty(globalThis, "sessionStorage", original) : delete globalThis.sessionStorage;
  return Promise.resolve().then(run).finally(restore);
}

test("one client_id per browser tab survives reopening and a storage failure", async () => {
  const values = new Map();
  const storage = { getItem: (key) => values.get(key) ?? null, setItem: (key, value) => values.set(key, value) };
  await withStorage(storage, async () => {
    const first = await mount(), second = await mount({ direction: "opening" });
    assert.match(first.starts[0].client_id, /^\d+-\d+-\d+-\d+$/);
    assert.equal(second.starts[0].client_id, first.starts[0].client_id);
    assert.equal(values.get("myhome-calibration-client"), first.starts[0].client_id);
    await first.controller.open({ ...first.controller._context, resume: { session_id: "again", mode: "guided" } });
    assert.equal(first.starts.at(-1).client_id, first.starts[0].client_id);
  });
  await withStorage(new Error("storage blocked"), () => {
    const id = calibrationClient();
    assert.match(id, /^\d+-\d+-\d+-\d+$/);
    assert.equal(calibrationClient(), id);
  });
});

/** Same-origin tabs: what one posts reaches every other open channel of the same name, never itself. */
class TabChannel {
  static open = new Set();
  // A busy or throttled tab: what it posts arrives this many milliseconds later.
  static delay = new Map();
  constructor(name) { this.name = name; TabChannel.open.add(this); }
  postMessage(data) {
    const delay = TabChannel.delay.get(this);
    for (const other of TabChannel.open) {
      if (other === this || other.name !== this.name) continue;
      const deliver = () => other.onmessage?.({ data: structuredClone(data) });
      if (delay) setTimeout(deliver, delay); else setImmediate(deliver);
    }
  }
  close() { TabChannel.open.delete(this); }
}

const tabStorage = (id) => {
  const values = new Map(id ? [["myhome-calibration-client", id]] : []);
  return { values, getItem: (key) => values.get(key) ?? null, setItem: (key, value) => values.set(key, value) };
};

/** A browser tab is a fresh copy of the module with its own sessionStorage; `nonce` fixes its tie-break. */
async function browserTab(name, nonce) {
  const random = globalThis.crypto.getRandomValues;
  if (nonce != null) globalThis.crypto.getRandomValues = (array) => array.length === 1 ? array.fill(nonce) : random.call(globalThis.crypto, array);
  try {
    return await import(`../../custom_components/myhome/frontend/panel/panel-cover-calibration.js?tab=${name}`);
  } finally {
    globalThis.crypto.getRandomValues = random;
  }
}

async function withTabs(run) {
  const original = Object.getOwnPropertyDescriptor(globalThis, "BroadcastChannel");
  Object.defineProperty(globalThis, "BroadcastChannel", { configurable: true, writable: true, value: TabChannel });
  try {
    await run();
  } finally {
    for (const channel of TabChannel.open) channel.close();
    TabChannel.delay.clear();
    if (original) Object.defineProperty(globalThis, "BroadcastChannel", original);
    else delete globalThis.BroadcastChannel;
  }
}

test("a duplicated tab takes a new identity and opens the session read-only; the original keeps it", async () => {
  await withTabs(async () => {
    const original = await browserTab("original"), first = tabStorage();
    const id = await withStorage(first, () => original.calibrationClient());
    assert.match(id, /^\d+-\d+-\d+-\d+$/);
    // "Duplicate tab" copies sessionStorage, so the copy starts with the same identity.
    const copy = await browserTab("copy"), copied = tabStorage(id);
    await withStorage(copied, async () => {
      assert.equal(copy.calibrationClient(), null, "a copied identity is not used before the check");
      const own = await copy.checkedCalibrationClient();
      assert.match(own, /^\d+-\d+-\d+-\d+$/);
      assert.notEqual(own, id);
      assert.equal(copied.values.get("myhome-calibration-client"), own, "a reload of the copy keeps its new identity");
      assert.equal(copy.calibrationClient(), own);
    });
    assert.equal(await withStorage(first, () => original.calibrationClient()), id);
    // The view of a copy waits for the check: its resume carries the new identity, so it only reads.
    const viewer = await browserTab("viewer");
    await withStorage(tabStorage(id), async () => {
      const { starts } = await mount({ Calibration: viewer.CoverCalibration, resume: { session_id: "session-one", mode: "guided" } });
      assert.equal(starts.length, 1);
      assert.equal(starts[0].type, "myhome/cover_calibration/resume");
      assert.match(starts[0].client_id, /^\d+-\d+-\d+-\d+$/);
      assert.notEqual(starts[0].client_id, id);
      assert.equal("claim" in starts[0], false);
    });
  });
});

test("a copy whose original answers too late gives the identity up and reads the open session", async () => {
  await withTabs(async () => {
    const original = await browserTab("slow-original"), first = tabStorage();
    const before = new Set(TabChannel.open);
    const id = await withStorage(first, () => original.calibrationClient());
    // The original tab is busy: its answer arrives after the copy has stopped waiting.
    TabChannel.delay.set([...TabChannel.open].find((channel) => !before.has(channel)), original.CLIENT_CHECK_MS + 100);
    const copy = await browserTab("late-copy"), copied = tabStorage(id);
    await withStorage(copied, async () => {
      const { starts, calls, counts, push } = await mount({ Calibration: copy.CoverCalibration });
      assert.equal(starts[0].client_id, id, "nobody answered in time");
      // Home Assistant takes the copy for the owner, since it sent the owner's identity.
      push({ recoverable: true, attached: true, attachment: "owner", owner: true, read_only: false });
      assert.equal(starts[0].type, "myhome/cover_calibration/start");
      await new Promise((resolve) => setTimeout(resolve, 250));
      const own = copy.calibrationClient();
      assert.match(own, /^\d+-\d+-\d+-\d+$/);
      assert.notEqual(own, id);
      assert.equal(copied.values.get("myhome-calibration-client"), own);
      assert.equal(starts.length, 2);
      assert.equal(starts[1].type, "myhome/cover_calibration/resume");
      assert.equal(starts[1].session_id, "session-one");
      assert.equal(starts[1].client_id, own);
      assert.equal("claim" in starts[1], false, "the copy only reads");
      assert.equal(counts().stopped, 1, "the old subscription is replaced");
      assert.equal(calls.length, 0, "nothing is sent: no detach, no cancel, no movement");
    });
    assert.equal(await withStorage(first, () => original.calibrationClient()), id);
  });
});

test("a channel that cannot be opened leaves the panel as without one", async () => {
  await withTabs(async () => {
    globalThis.BroadcastChannel = class { constructor() { throw new Error("SecurityError"); } };
    const tab = await browserTab("opaque");
    await withStorage(tabStorage("9-9-9-9"), async () => {
      const controller = new tab.CoverCalibration();
      assert.ok(controller);
      assert.equal(tab.calibrationClient(), "9-9-9-9");
      assert.equal(await tab.checkedCalibrationClient(), "9-9-9-9");
    });
  });
});

test("a reloaded tab keeps its identity when no other tab holds it, and answers for it once its panel loads", async () => {
  await withTabs(async () => {
    const reloaded = await browserTab("reloaded"), storage = tabStorage("11-22-33-44");
    const started = Date.now();
    // Building the panel is enough: no calibration view has been opened in this tab yet.
    await withStorage(storage, () => { new reloaded.CoverCalibration(); });
    assert.equal(await withStorage(storage, () => reloaded.calibrationClient()), null);
    await new Promise((resolve) => setTimeout(resolve, reloaded.CLIENT_CHECK_MS + 50));
    assert.ok(Date.now() - started >= reloaded.CLIENT_CHECK_MS, "the tab waited for an answer");
    assert.equal(await withStorage(storage, () => reloaded.calibrationClient()), "11-22-33-44");
    assert.equal(storage.values.get("myhome-calibration-client"), "11-22-33-44");
    const copy = await browserTab("reloaded-copy");
    assert.notEqual(await withStorage(tabStorage("11-22-33-44"), () => copy.checkedCalibrationClient()), "11-22-33-44");
  });
});

for (const [first, second, keeper, listening] of [[1, 2, "first", false], [2, 1, "second", false], [1, 2, "first", true], [2, 1, "second", true]]) {
  test(`two copies checking one identity at once: exactly one keeps it (nonces ${first}, ${second}${listening ? ", both listening" : ""})`, async () => {
    await withTabs(async () => {
      // Browser session restore can bring back two copies of one tab together.
      const name = `restored-${first}-${second}-${listening}`;
      const one = await browserTab(`${name}-a`, first), two = await browserTab(`${name}-b`, second);
      // Each copy has its own sessionStorage, both restored with the same identity.
      const stores = [tabStorage("5-6-7-8"), tabStorage("5-6-7-8")];
      let current = stores[0];
      const ids = await withStorage(new Error("unused"), async () => {
        Object.defineProperty(globalThis, "sessionStorage", { configurable: true, get: () => current });
        if (listening) {
          assert.equal(one.calibrationClient(), null);
          current = stores[1];
          assert.equal(two.calibrationClient(), null);
          current = stores[0];
        }
        const checks = [one.checkedCalibrationClient()];
        // Unless both already listen, the second copy starts only now: the first query reached nobody.
        current = stores[1];
        checks.push(two.checkedCalibrationClient());
        return Promise.all(checks);
      });
      const kept = keeper === "first" ? 0 : 1;
      assert.equal(ids[kept], "5-6-7-8");
      assert.deepEqual(stores.map((store) => store.values.get("myhome-calibration-client")), ids, "each copy stores its own");
      assert.notEqual(ids[1 - kept], "5-6-7-8");
      assert.match(ids[1 - kept], /^\d+-\d+-\d+-\d+$/);
    });
  });
}

test("a read-only tab keeps Stop, cannot move or save, and takes control only on request", async () => {
  const { host, push, calls, starts, controller, counts } = await mount();
  push({ recoverable: true, attached: true, attachment: "reader", read_only: true, owner: false,
    phase: "review", save_modes: ["new"], values: { opening_time: 20, closing_time: 30 } });
  assert.equal(host.querySelector("#cal-read-only").hidden, false);
  assert.match(host.querySelector("#cal-read-only").textContent, /Un’altra scheda o un altro dispositivo/);
  assert.equal(host.querySelector('#cal-save button[type="submit"]').disabled, true);
  assert.equal(host.querySelector("#cal-save-mode").disabled, true);
  assert.equal(host.querySelector("#cal-stop").disabled, false);
  assert.equal(host.querySelector("#cal-cancel").textContent, "Chiudi");
  host.querySelector("#cal-stop").click(); await tick();
  assert.deepEqual(calls.at(-1), { type: "myhome/cover_calibration/action", entry_id: "one", session_id: "session-one",
    sequence: 2, attachment: "reader", action: "stop" });
  const sequence = controller._state.sequence;
  host.querySelector("#cal-take-control").click(); await tick();
  assert.equal(calls.length, 1, "taking control sends no action and no movement");
  assert.deepEqual(starts.at(-1), { type: "myhome/cover_calibration/resume", entry_id: "one", session_id: "session-one",
    client_id: starts[0].client_id, claim: true, sequence });
  assert.equal(counts().stopped, 1, "the reading subscription is replaced, not left");
  push({ read_only: false, owner: true, attachment: "owner" });
  assert.equal(host.querySelector("#cal-read-only").hidden, true);
  assert.equal(host.querySelector("#cal-take-control").hidden, true);
  assert.equal(host.querySelector('#cal-save button[type="submit"]').disabled, false);
  assert.equal(host.querySelector("#cal-cancel").textContent, "Annulla misurazione");
});

test("closing a read-only tab detaches instead of cancelling the owner's session", async () => {
  const { host, push, calls, counts } = await mount();
  push({ recoverable: true, attached: true, attachment: "reader", read_only: true, phase: "opening" });
  host.querySelector("#cal-cancel").click(); await tick();
  assert.equal(calls.at(-1).action, "detach");
  assert.equal(calls.at(-1).attachment, "reader");
  assert.equal(counts().cancelled, 1);
});

test("reconnecting to the same session swaps the subscription without leaving it or claiming", async () => {
  const { host, push, calls, starts } = await mount({ call: (message) => {
    if (message.action === "heartbeat") throw { code: "disconnected" };
    return {};
  } });
  push({ recoverable: true, attached: true, attachment: "owner", read_only: false });
  await instances.at(-1)._perform("heartbeat");
  assert.equal(host.querySelector("#cal-reconnect").hidden, false);
  host.querySelector("#cal-reconnect").click(); await tick();
  assert.equal(calls.filter((call) => call.action !== "heartbeat").length, 0);
  assert.equal(starts.at(-1).type, "myhome/cover_calibration/resume");
  assert.equal("claim" in starts.at(-1), false);
});

test("a reader is told whether the owner is present and Take control stands out when nobody guides", async () => {
  const { host, push, calls } = await mount();
  push({ recoverable: true, attached: true, attachment: "reader", read_only: true, owner: false, owner_present: true, phase: "opening" });
  const notice = host.querySelector("#cal-read-only"), take = host.querySelector("#cal-take-control");
  assert.equal(notice.textContent, translations.it.calReadOnly);
  assert.equal(take.hidden, false);
  assert.equal(take.classList.contains("primary"), false);
  push({ owner_present: false });
  assert.equal(notice.textContent, translations.it.calReadOnlyAway);
  assert.equal(take.hidden, false);
  assert.equal(take.classList.contains("primary"), true);
  assert.equal(host.querySelector("#cal-reconnect").hidden, true, "this tab's own subscription is still live");
  assert.equal(calls.length, 0);
});

test("Take control asks for confirmation in place while the owner is present, never when it is absent", async () => {
  const { host, push, calls, starts, controller } = await mount();
  push({ recoverable: true, attached: true, attachment: "reader", read_only: true, owner: false, owner_present: true, phase: "opening" });
  const take = host.querySelector("#cal-take-control"), consequence = host.querySelector("#cal-take-consequence");
  assert.equal(take.textContent, translations.it.calTakeControl);
  assert.equal(consequence.hidden, true);
  take.click(); await tick();
  assert.equal(starts.length, 1, "the first tap sends nothing");
  assert.equal(calls.length, 0);
  assert.equal(take.textContent, translations.it.calConfirmTakeControl);
  assert.equal(consequence.hidden, false);
  assert.equal(consequence.textContent, translations.it.calTakeControlConsequence);
  push({});  // A heartbeat view keeps the question open.
  assert.equal(take.textContent, translations.it.calConfirmTakeControl);
  const sequence = controller._state.sequence;
  take.click(); await tick();
  assert.deepEqual(starts.at(-1), { type: "myhome/cover_calibration/resume", entry_id: "one", session_id: "session-one",
    client_id: starts[0].client_id, claim: true, sequence });
  assert.equal(calls.length, 0, "taking control sends no action and no movement");
  // Reopened read-only, then the owner goes away: one tap is enough again.
  push({ attachment: "reader-two", read_only: true, owner: false, owner_present: true });
  assert.equal(host.querySelector("#cal-take-control").textContent, translations.it.calTakeControl, "a new view asks again");
  host.querySelector("#cal-take-control").click(); await tick();
  assert.equal(host.querySelector("#cal-take-consequence").hidden, false);
  push({ owner_present: false });
  assert.equal(host.querySelector("#cal-take-control").textContent, translations.it.calTakeControl);
  assert.equal(host.querySelector("#cal-take-consequence").hidden, true);
  const before = starts.length;
  host.querySelector("#cal-take-control").click(); await tick();
  assert.equal(starts.length, before + 1);
  assert.equal(starts.at(-1).claim, true);
});

test("sequences are compared within one session only; a new session is always shown", async () => {
  const { push, controller } = await mount();
  push({ recoverable: true, attached: true, attachment: "first", phase: "opening" });
  push({ phase: "confirm_open" });
  push({ sequence: 1, phase: "closing" });
  assert.equal(controller._state.phase, "confirm_open", "an older view of the same session is ignored");
  push({ session_id: "session-two", sequence: 1, attachment: "second", phase: "confirm_closed" });
  assert.equal(controller._state.session_id, "session-two");
  assert.equal(controller._state.phase, "confirm_closed");
});

test("a start replayed after the connection drops names its session; a deliberate start does not", async () => {
  const { controller, starts } = await mount();
  const listeners = new Map(), connection = controller._context.hass.connection;
  Object.assign(connection, { addEventListener: (type, callback) => listeners.set(type, callback),
    removeEventListener: (type, callback) => { if (listeners.get(type) === callback) listeners.delete(type); } });
  await controller.open({ ...controller._context });
  const start = starts.at(-1);
  assert.equal(start.type, "myhome/cover_calibration/start");
  assert.equal("session_id" in start, false);
  listeners.get("disconnected")();
  assert.equal(start.session_id, "session-one", "Home Assistant replays this object after reconnecting");
  await controller.open({ ...controller._context });
  assert.equal("session_id" in starts.at(-1), false);
  assert.equal(listeners.size, 1, "the closed view stopped listening");
  await controller.open({ ...controller._context, resume: controller._state });
  assert.equal(starts.at(-1).type, "myhome/cover_calibration/resume");
  assert.equal(listeners.size, 0, "a resume already names its session");
  assert.equal(starts.at(-2).session_id, "session-one", "closing a view pins its start too");
});

test("a claim made against an old sequence warns and invites to try again", async () => {
  const { host, push, controller } = await mount();
  push({ recoverable: true, attached: true, attachment: "reader", read_only: true, owner: false, phase: "opening" });
  host.querySelector("#cal-take-control").click(); await tick();
  const reason = host.querySelector("#cal-reason");
  assert.equal(controller._state.read_only, true);
  assert.equal(reason.hidden, false);
  assert.equal(reason.textContent, translations.it.calClaimStale);
  push({ read_only: false, owner: true, attachment: "owner" });
  await controller.open({ ...controller._context, claim: true, resume: controller._state });
  assert.equal(controller._state.owner, true);
  assert.equal(host.querySelector("#cal-reason").hidden, true);
  push({ read_only: true, owner: false }); // Another tab takes control later: that is no stale claim.
  assert.equal(host.querySelector("#cal-reason").hidden, true);
  assert.equal(host.querySelector("#cal-read-only").hidden, false);
});

test("a Cancel that does not reach Home Assistant keeps the view and is sent first after the reconnection", async () => {
  let online = false;
  const { host, push, calls, counts } = await mount({ call: (message, state) => {
    if (!online) throw 3; // ERR_CONNECTION_LOST from home-assistant-js-websocket.
    return { ...state, sequence: state.sequence + 1, phase: message.action === "cancel" ? "cancelled" : state.phase };
  } });
  push({ recoverable: true, attached: true, attachment: "before", owner: true, read_only: false, phase: "settling" });
  host.querySelector("#cal-cancel").click(); await tick();
  assert.equal(counts().cancelled, 0, "the view is not closed in silence");
  assert.equal(host.querySelector("#cal-reason").hidden, false);
  assert.equal(host.querySelector("#cal-reason").textContent, translations.it.calCancelFailed);
  online = true;
  push({ attachment: "after", attached: true }); await tick(); // Home Assistant replays the subscription.
  const sent = calls.filter((call) => call.attachment === "after");
  assert.deepEqual(sent.map((call) => call.action), ["cancel"], "no heartbeat before the Cancel");
  assert.equal(counts().cancelled, 1);
  assert.equal(counts().stopped, 1);
});

/** Answers the panel's `resume` check before closing: the session lives unless `gone()` says otherwise. */
function probing(controller, gone) {
  const connection = controller._context.hass.connection, subscribe = connection.subscribeMessage, probes = [];
  connection.subscribeMessage = async (callback, request) => {
    if (request.type !== "myhome/cover_calibration/resume") return subscribe(callback, request);
    probes.push(request);
    if (gone()) throw { code: "calibration_expired" };
    return () => {};
  };
  return probes;
}

test("Cancel pressed again while reconnecting keeps the view of a live session until the Cancel arrives", async () => {
  let network = "down";
  const { host, push, calls, counts, controller } = await mount({ call: (message, state) => {
    if (network === "down") throw 3; // ERR_CONNECTION_LOST.
    if (message.attachment === "before") throw { code: "calibration_expired" }; // Token of the dead socket.
    return { ...state, sequence: state.sequence + 1, phase: message.action === "cancel" ? "cancelled" : state.phase };
  } });
  const probes = probing(controller, () => false);
  push({ recoverable: true, attached: true, attachment: "before", owner: true, read_only: false, phase: "opening" });
  host.querySelector("#cal-cancel").click(); await tick();
  network = "reconnecting";
  host.querySelector("#cal-cancel").click(); await tick(); // Queued with the old token, sent first.
  assert.equal(probes.length, 1);
  assert.equal("claim" in probes[0], false);
  assert.equal(counts().cancelled, 0, "the session lives: the view stays");
  assert.equal(host.querySelector("#cal-reason").textContent, translations.it.calCancelFailed);
  push({ attachment: "after", attached: true }); await tick(); // The replayed subscription.
  assert.deepEqual(calls.filter((call) => call.attachment === "after").map((call) => call.action), ["cancel"]);
  assert.equal(counts().cancelled, 1);
});

test("a Cancel refused as expired closes the view once the session is confirmed gone", async () => {
  const { host, push, counts, controller } = await mount({ call: () => { throw { code: "calibration_expired" }; } });
  const probes = probing(controller, () => true);
  push({ recoverable: true, attached: true, attachment: "gone", owner: true, read_only: false });
  host.querySelector("#cal-cancel").click(); await tick();
  assert.equal(probes.length, 1);
  assert.equal(counts().cancelled, 1);
});

test("a failed check of the session keeps the view and the pending Cancel", async () => {
  const { host, push, counts, controller } = await mount({ call: () => { throw { code: "calibration_expired" }; } });
  const connection = controller._context.hass.connection, subscribe = connection.subscribeMessage;
  connection.subscribeMessage = async (callback, request) => {
    if (request.type === "myhome/cover_calibration/resume") throw 3;
    return subscribe(callback, request);
  };
  push({ recoverable: true, attached: true, attachment: "unknown", owner: true, read_only: false });
  host.querySelector("#cal-cancel").click(); await tick();
  assert.equal(counts().cancelled, 0);
  assert.equal(host.querySelector("#cal-reason").hidden, false);
});

test("a reconnected owner does not beat before a Cancel still in flight", async () => {
  let release;
  const { host, push, calls, controller } = await mount({ call: (message) =>
    message.action === "cancel" ? new Promise((resolve) => { release = resolve; }) : {} });
  push({ recoverable: true, attached: true, attachment: "first", owner: true, read_only: false });
  host.querySelector("#cal-cancel").click(); await tick();
  push({ attachment: "second" }); await tick();
  assert.equal(calls.some((call) => call.action === "heartbeat"), false);
  release({ ...controller._state, phase: "cancelled" }); await tick();
});

test("an owner's replayed subscription renews presence at once; a reader's does not", async () => {
  const { push, calls } = await mount();
  push({ recoverable: true, attached: true, attachment: "first", owner: true, read_only: false });
  assert.equal(calls.length, 0);
  push({ attachment: "second" }); await tick();
  assert.deepEqual(calls.map((call) => [call.action, call.attachment]), [["heartbeat", "second"]]);
  push({ attachment: "third", owner: false, read_only: true }); await tick();
  assert.equal(calls.length, 1);
});

test("after Home Assistant reports the session ended, Resume is not offered", async () => {
  const { host, push, controller } = await mount({ call: (message) => {
    if (message.action === "heartbeat") throw { code: "calibration_expired" };
    return {};
  } });
  push({ recoverable: true, attached: true, attachment: "owner", owner: true, read_only: false });
  await controller._perform("heartbeat");
  assert.equal(host.querySelector("#cal-reason").textContent, translations.it.profileError_calibration_expired);
  assert.equal(host.querySelector("#cal-reconnect").hidden, true);
  assert.equal(host.querySelector('[data-cal-action="open"]').disabled, true);
});

/** Fake intervals and a fake monotonic clock, advanced together. */
function fakeClock() {
  let now = 1000;
  mock.timers.enable({ apis: ["setInterval"] });
  mock.method(performance, "now", () => now);
  return { tick: (ms) => { now += ms; mock.timers.tick(ms); },
    restore: () => { mock.timers.reset(); mock.restoreAll(); } };
}

test("the elapsed time advances by tenths between two views, realigns to each view and always shows one decimal", async () => {
  const clock = fakeClock();
  try {
    const { host, push } = await mount();
    const shown = () => host.querySelector("#cal-elapsed").textContent.replace(`${translations.it.calElapsed}: `, "");
    push({ phase: "opening", elapsed: 1 });
    assert.equal(shown(), "1.0 s");
    clock.tick(100);
    assert.equal(shown(), "1.1 s");
    clock.tick(3400);
    assert.equal(shown(), "4.5 s");
    push({ elapsed: 4.53 }); // The next view wins over the local count, still to a tenth.
    assert.equal(shown(), "4.5 s");
    clock.tick(1000);
    assert.equal(shown(), "5.5 s");
    push({ elapsed: 10.88 });
    assert.equal(shown(), "10.9 s");
    clock.tick(1000);
    assert.equal(shown(), "11.9 s");
    for (let step = 0; step < 20; step++) {
      clock.tick(100);
      assert.match(shown(), /^\d+\.\d s$/);
    }
    push({ phase: "confirm_open", elapsed: null }); // End of the run.
    assert.equal(shown(), "");
    clock.tick(5000);
    assert.equal(shown(), "");
  } finally {
    clock.restore();
  }
});

test("no elapsed timer survives a closed view or a lost connection", async () => {
  const clock = fakeClock();
  try {
    const { host, push, controller } = await mount({ call: (message) => {
      if (message.action === "heartbeat") throw { code: "disconnected" };
      return {};
    } });
    push({ phase: "closing", elapsed: 4 });
    await controller._perform("heartbeat");
    assert.equal(controller._ticker, null);
    assert.equal(host.querySelector("#cal-elapsed").textContent, "");
    push({ recoverable: true, attached: true, attachment: "again", phase: "closing", elapsed: 6 });
    assert.notEqual(controller._ticker, null);
    controller.close();
    assert.equal(controller._ticker, null);
    clock.tick(3000);
    assert.equal(host.querySelector("#cal-elapsed").textContent, `${translations.it.calElapsed}: 6.0 s`);
  } finally {
    clock.restore();
  }
});

// Ten minutes after a fixed local noon: the deadline is shown as a time only, in the browser's time zone,
// as long as the clock reads the same day (the test below fixes it at that noon).
const NOON = new Date(2026, 9, 1, 12, 0);
const EXPIRES = new Date(NOON.getTime() + 600000).toISOString();
const pausedCycle = { mode: "automatic", phase: "paused", reason: "owner_absent", run_index: 2, values: { closing_time: 46 },
  owner: true, read_only: false, owner_present: false, paused_at: "2026-10-01T18:32:00+00:00", idle_expires_at: EXPIRES,
  next_step: { step: "opening", run_index: 2, entity_id: "cover.bedroom" } };
const at = (iso, options = { timeStyle: "short" }) => new Intl.DateTimeFormat("en", options).format(new Date(iso));

test("a paused cycle opened by its owner says what Continue starts and until when, and sends nothing", async (t) => {
  // Only Date is fixed: timers stay real. A real clock near midnight would put the deadline on the next day.
  t.mock.timers.enable({ apis: ["Date"], now: NOON });
  const { host, calls, starts } = await mount({ mode: "automatic", resume: pausedCycle });
  assert.equal(starts[0].type, "myhome/cover_calibration/resume");
  assert.equal("claim" in starts[0], false);
  assert.equal(calls.length, 0, "opening a paused session sends no command");
  assert.equal(host.querySelector("#cal-phase").textContent, translations.it.calPhase_paused);
  const notice = host.querySelector("#cal-paused");
  assert.equal(notice.hidden, false);
  assert.equal(notice.textContent, translations.it.calPausedBody
    .replace("{step}", "la corsa 3 di 3 (apertura)").replace("{expires}", at(EXPIRES)));
  assert.match(notice.textContent, /Le misure già fatte restano/);
  assert.equal(host.querySelector("#cal-reason").hidden, true, "the pause explains itself, not as an error");
  const proceed = host.querySelector('[data-cal-action="continue"]');
  assert.equal(proceed.hidden, false);
  assert.equal(proceed.disabled, false);
  assert.equal(proceed.textContent, translations.it.calContinue);
  assert.equal(proceed.classList.contains("primary"), true);
  assert.equal(host.querySelector("#cal-cancel").textContent, translations.it.calPausedCancel);
  assert.equal(host.querySelector("#cal-stop").disabled, false);
  assert.equal(host.querySelector('[data-cal-action="run"]').hidden, true);
  assert.equal(host.querySelector("#cal-read-only").hidden, true);
  const figure = host.querySelector(".cal-visual");
  assert.equal(figure.dataset.scene, "neutral");
  assert.equal(figure.querySelector(".cal-visual-label").textContent, translations.it.calVisual_paused);
  assert.equal(calls.length, 0);
});

test("Continue sends one continue with the current sequence, and the view follows the run it starts", async () => {
  const answer = deferred();
  const { host, calls, controller } = await mount({ mode: "automatic", resume: pausedCycle,
    call: (message, state) => message.action === "continue" ? answer.promise.then(() => ({ ...state, sequence: state.sequence + 1,
      phase: "starting_open", reason: null, next_step: null, paused_at: null })) : state });
  const proceed = host.querySelector('[data-cal-action="continue"]');
  const sequence = controller._state.sequence;
  proceed.click();
  proceed.click();
  await tick();
  assert.deepEqual(calls, [{ type: "myhome/cover_calibration/action", entry_id: "one", session_id: "session-one",
    sequence, attachment: "new-controller", action: "continue" }]);
  assert.equal(proceed.disabled, true, "one tap only while Home Assistant answers");
  answer.resolve(); await tick(); await tick();
  assert.equal(controller._state.phase, "starting_open");
  assert.equal(proceed.hidden, true);
  assert.equal(host.querySelector("#cal-paused").hidden, true);
  assert.equal(host.querySelector("#cal-cancel").textContent, translations.it.calCancel);
  assert.match(host.querySelector("#cal-phase").textContent, /3\/3/);
});

test("a reader of a paused cycle takes control with one tap and then continues it", async () => {
  const { host, push, calls, starts, controller } = await mount({ mode: "automatic" });
  push({ ...pausedCycle, recoverable: true, attached: true, attachment: "reader", owner: false, read_only: true });
  assert.equal(host.querySelector("#cal-paused").hidden, false, "the same information as the owner");
  assert.equal(host.querySelector("#cal-read-only").textContent, translations.it.calReadOnlyAway);
  assert.equal(host.querySelector('[data-cal-action="continue"]').hidden, true);
  assert.equal(host.querySelector("#cal-cancel").textContent, translations.it.close);
  const take = host.querySelector("#cal-take-control");
  assert.equal(take.classList.contains("primary"), true);
  const sequence = controller._state.sequence;
  take.click(); await tick();
  assert.deepEqual(starts.at(-1), { type: "myhome/cover_calibration/resume", entry_id: "one", session_id: "session-one",
    client_id: starts[0].client_id, claim: true, sequence });
  assert.equal(calls.length, 0, "taking control moves nothing");
  push({ attachment: "owner-now", owner: true, read_only: false });
  const proceed = host.querySelector('[data-cal-action="continue"]');
  assert.equal(proceed.hidden, false);
  proceed.click(); await tick();
  const sent = calls.filter((call) => call.action !== "heartbeat");
  assert.equal(sent.length, 1);
  assert.equal(sent[0].action, "continue");
  assert.equal(sent[0].attachment, "owner-now");
});

test("a batch paused before the next cover names it, keeps the results, and Stop keeps the pause", async () => {
  const { host, push, calls } = await mount({ mode: "automatic", entity_ids: ["cover.one", "cover.two"],
    call: (message, state) => ({ ...state, sequence: state.sequence + 1, stop_requested: message.action === "stop" }) });
  push({ ...pausedCycle, recoverable: true, attached: true, attachment: "owner", run_index: 0, values: {},
    results: [{ index: 0, entity_id: "cover.one", values: { opening_time: 20, closing_time: 22 } }], idle_expires_at: null,
    next_step: { step: "next_cover", cover_index: 1, entity_id: "cover.two" } });
  const notice = host.querySelector("#cal-paused");
  assert.equal(notice.textContent, translations.it.calPausedBody
    .replace("{step}", "la misura della tapparella successiva, «cover.two»").replace("{expires}", "—"));
  const targets = host.querySelector("#cal-targets").textContent;
  assert.match(targets, /cover\.one · 20 \/ 22 s/);
  assert.doesNotMatch(targets, new RegExp(translations.it.calBatchDiscarded));
  host.querySelector("#cal-stop").click(); await tick();
  assert.equal(calls.at(-1).action, "stop");
  assert.equal(host.querySelector("#cal-stop-status").hidden, false);
  assert.equal(notice.hidden, false);
  assert.equal(host.querySelector('[data-cal-action="continue"]').hidden, false);
});

test("a refused Continue shows why and leaves the pause, its measurements and Continue in place", async () => {
  const { host, calls, controller } = await mount({ mode: "automatic", resume: pausedCycle, call: (message, state) => {
    if (message.action === "continue") throw { code: "calibration_moving" };
    return state;
  } });
  const proceed = host.querySelector('[data-cal-action="continue"]');
  proceed.click(); await tick(); await tick();
  assert.equal(calls.at(-1).action, "continue");
  const reason = host.querySelector("#cal-reason");
  assert.equal(reason.hidden, false);
  assert.equal(reason.textContent, translations.it.profileError_calibration_moving);
  assert.equal(controller._state.phase, "paused");
  assert.equal(host.querySelector("#cal-paused").hidden, false);
  assert.equal(proceed.hidden, false);
  assert.equal(proceed.disabled, false, "Continue can be tried again");
  controller._accept({ ...controller._state });  // A heartbeat view keeps the error on screen.
  assert.equal(reason.hidden, false);
});

test("a deadline on another day carries its date, and a cover name is shown exactly as it is", async (t) => {
  // Fixed day, timers real: "now + 24 h" could stay on the same day around a change of daylight saving time.
  t.mock.timers.enable({ apis: ["Date"], now: NOON });
  const tomorrow = new Date(NOON.getTime() + 36 * 3600000).toISOString();
  const { host, controller } = await mount({ mode: "automatic", entity_ids: ["cover.one", "cover.two"] });
  controller._accept({ ...controller._state, ...pausedCycle, sequence: 9, recoverable: true, attached: true, attachment: "owner",
    targets: [{ entity_id: "cover.one", name: "One" }, { entity_id: "cover.two", name: "Attic $& $' $1" }],
    results: [], idle_expires_at: tomorrow, next_step: { step: "next_cover", cover_index: 1, entity_id: "cover.two" } });
  assert.equal(host.querySelector("#cal-paused").textContent, translations.it.calPausedBody
    .replace("{step}", () => "la misura della tapparella successiva, «Attic $& $' $1»")
    .replace("{expires}", () => at(tomorrow, { dateStyle: "short", timeStyle: "short" })));
});

test("a paused cycle has its texts in English and Italian, with the step and the deadline as placeholders", () => {
  for (const language of ["en", "it"]) {
    const texts = translations[language];
    assert.match(texts.calPausedBody, /\{step\}.*\{expires\}/);
    assert.match(texts.calNextStep_opening, /\{run\}/);
    assert.match(texts.calNextStep_closing, /\{run\}/);
    assert.match(texts.calNextStep_next_cover, /\{name\}/);
    for (const key of ["calPhase_paused", "calContinue", "calPausedCancel", "calPausedBadge", "calVisual_paused"]) assert.ok(texts[key], `${language}.${key}`);
  }
  assert.equal(calibrationScene({ phase: "paused" }).icon, "mdi:pause-circle-outline");
});

test("the tape reading of a check travels as the same reading, with a comma or a point", async () => {
  const { host, push, calls } = await mount({ mode: "geometry" });
  const form = host.querySelector("#cal-reading"), input = form.elements.reading_cm;
  const check = { direction: "opening", target_cm: 100, check_seconds: 13.6, expected_cm: 100.2, measured_cm: null,
    deviation_cm: null, passed: null };
  push({ phase: "reading", step: "check", reading_kind: "check", can_repeat: true, save_modes: ["new"], check, check_threshold_cm: 4 });
  assert.equal(form.hidden, false);
  for (const [value, expected] of [["99,5", 99.5], ["99.5", 99.5], ["104", 104]]) {
    input.value = value;
    form.dispatchEvent(new dom.window.Event("submit", { cancelable: true })); await tick();
    const { sequence, ...message } = calls.at(-1);
    assert.equal(typeof sequence, "number");
    assert.deepEqual(message, { type: "myhome/cover_calibration/action", entry_id: "one", session_id: "session-one",
      action: "reading", reading_cm: expected }, value);
  }
});

test("a check run is timed like the intermediate runs: no elapsed time, the same texts and illustrations", async () => {
  const { host, push, calls } = await mount({ mode: "geometry" });
  const phase = host.querySelector("#cal-phase"), elapsed = host.querySelector("#cal-elapsed"), figure = host.querySelector(".cal-visual");
  push({ phase: "briefing", step: "check", save_modes: ["new"], can_repeat: false });
  assert.equal(figure.dataset.scene, "middle");
  assert.equal(figure.querySelector(".cal-visual-label").textContent, t("calVisual_intermediate"));
  for (const [state, text] of [[{ phase: "opening", step: "check" }, "calHalfPositioning"], [{ phase: "closing", step: "check" }, "calHalfPositioning"],
    [{ phase: "geometry_wait_stop", step: "check" }, "calGeometryWaitStop"]]) {
    push({ ...state, elapsed: 3.5 });
    assert.equal(elapsed.hidden, true, `${state.phase} of a check shows no elapsed time`);
    assert.equal(phase.textContent, t(text));
    assert.doesNotMatch(figure.textContent, new RegExp(t("calVisual_opening")));
  }
  assert.equal(calibrationScene({ mode: "geometry", phase: "opening", step: "check" }).label, "calVisual_autoStop");
  assert.equal(calibrationScene({ mode: "geometry", phase: "closing", step: "check" }).icon, "mdi:arrow-down-bold");
  const reading = calibrationScene({ mode: "geometry", phase: "reading", reading_kind: "check" });
  assert.deepEqual([reading.shape, reading.label, reading.measure], ["middle", "calVisual_measureHeight", true]);
  assert.equal(calls.length, 0);
});
