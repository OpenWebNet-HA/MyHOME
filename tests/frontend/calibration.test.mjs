import assert from "node:assert/strict";
import { after, afterEach, test } from "node:test";
import { JSDOM } from "jsdom";
import { CoverCalibration } from "../../custom_components/myhome/frontend/panel/panel-cover-calibration.js";
import { translations } from "../../custom_components/myhome/frontend/panel/panel-translations.js";

const dom = new JSDOM("<!doctype html><body></body>", { pretendToBeVisual: true });
const { document } = dom.window;
const instances = [];
const tick = () => new Promise((resolve) => setImmediate(resolve));
const deferred = () => { let resolve; const promise = new Promise((r) => { resolve = r; }); return { promise, resolve }; };
const t = (key) => translations.it[key] || translations.en[key] || key;

async function mount({ call, subscribe, entity_ids, direction, resume, mode = "guided" } = {}) {
  const host = document.createElement("section");
  document.body.append(host);
  const controller = new CoverCalibration();
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
  const hass = { connection: { subscribeMessage: async (cb, request) => {
    callback = cb; starts.push(request); cb(state);
    return subscribe ? subscribe(() => { stopped++; }) : () => { stopped++; };
  } }, callWS: async (message) => {
    calls.push(message);
    if (call) return call(message, state);
    const phases = { run: "starting_open", open: "starting_open", close: "starting_close", save: "saved", cancel: "cancelled", stop: "interrupted" };
    return { ...state, sequence: state.sequence + (message.action === "heartbeat" ? 0 : 1),
      phase: phases[message.action] || state.phase };
  } };
  await controller.open({ host, hass, entity: { entry_id: "one", entity_id: "cover.bedroom" }, revision: 4, mode, entity_ids, direction, resume,
    t, onSaved: () => { saved++; }, onCancel: () => { cancelled++; } });
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
  assert.match(host.querySelector("#cal-elapsed").textContent, /12.25/);
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
