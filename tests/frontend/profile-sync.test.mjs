import assert from "node:assert/strict";
import { after, afterEach, test } from "node:test";
import { JSDOM } from "jsdom";
import { CoverProfileEditor } from "../../custom_components/myhome/frontend/panel/panel-cover-profiles.js";
import { translations } from "../../custom_components/myhome/frontend/panel/panel-translations.js";

const dom = new JSDOM("<!doctype html><body></body>", { pretendToBeVisual: true });
globalThis.document = dom.window.document;
dom.window.HTMLDialogElement.prototype.showModal = function () { this.open = true; };
dom.window.HTMLDialogElement.prototype.close = function () { this.open = false; };
const editors = [];
const tick = () => new Promise((resolve) => setImmediate(resolve));
const deferred = () => { let resolve; const promise = new Promise((r) => { resolve = r; }); return { promise, resolve }; };
const t = (key) => translations.it[key] || key;
const snapshot = (revision = 0) => ({ entry_id: "one", entity_id: "cover.test", revision,
  assigned_profile_id: "p", profiles: [{ id: "p", name: `Saved ${revision}`, opening_time: 20, closing_time: 40, uses: 1 }],
  writable: true, default_travel_time: 30, effective_opening_time: 20, effective_closing_time: 40, pending: false });

function setup({ subscribe, read, write } = {}) {
  const host = document.createElement("section"); document.body.append(host);
  const editor = new CoverProfileEditor(); editors.push(editor);
  const calls = [], subscriptions = [];
  let data = snapshot();
  const hass = { states: {}, connection: { subscribeMessage: async (callback, request) => {
    const sub = { callback, request, stopped: false }; subscriptions.push(sub);
    callback({ entry_id: "one", revision: data.revision, kind: "ready" });
    const stop = () => { sub.stopped = true; };
    return subscribe ? subscribe(stop) : stop;
  } }, callWS: async (request) => {
    calls.push(request);
    if (request.type.endsWith("/read")) return read ? read(data) : structuredClone(data);
    if (request.type.endsWith("/write")) return write ? write(request) : snapshot(request.revision + 1);
    throw new Error(request.type);
  } };
  const context = { host, hass, entity: { entry_id: "one", entity_id: "cover.test" }, t, onSaved: () => {} };
  const push = (revision, extra = {}) => { data = snapshot(revision); subscriptions.at(-1).callback({ entry_id: "one", revision, kind: "changed", ...extra }); };
  return { host, editor, calls, subscriptions, context, push, setData: (value) => { data = value; }, open: () => editor.open(context) };
}
afterEach(() => { for (const editor of editors.splice(0)) editor.close(); document.body.replaceChildren(); delete document.hidden; });
after(() => dom.window.close());

const form = (view) => view.host.querySelector("#profile-form");

const geometrySnapshot = () => ({ ...snapshot(), height_scaling: true, travel_cm: 150, scaling: "height",
  profiles: [{ ...snapshot().profiles[0], reference_travel_cm: 200 }],
  effective_opening_time: 15, effective_closing_time: 19,
  effective: { opening: { value: 15, origin: "profile", scaled: true }, closing: { value: 19, origin: "override", scaled: false } } });

test("geometry remains in existing sections and saves cover travel independently of profile edits", async () => {
  const data = geometrySnapshot();
  const view = setup({ read: () => data, write: request => ({ ...data, revision: 1, travel_cm: request.travel_cm }) });
  await view.open();
  assert.equal(view.host.querySelectorAll(".profile-details").length, 3);
  assert.equal(form(view).elements.travel_cm.value, "150");
  assert.equal(form(view).elements.reference_travel_cm.value, "200");
  assert.match(view.host.querySelector("#profile-scaling-status").textContent, /adattati alla corsa/);
  assert.match(view.host.querySelector("#profile-scaling-status").textContent, /personale/);
  assert.match(view.host.querySelector("#profile-unscaled").textContent, /solo quando/);
  form(view).elements.travel_cm.value = "175.5";
  view.host.querySelector("#profile-calibrate").click();
  assert.match(view.host.querySelector("#profile-error").textContent, /Salva la corsa/);
  assert.equal(view.editor._calibrating, false);
  await view.editor._selectBatch();
  assert.equal(view.editor._calibrating, false);
  await view.editor._save("travel");
  assert.deepEqual(view.calls.at(-1), { type: "myhome/cover_profiles/write", entry_id: "one", entity_id: "cover.test", revision: 0, action: "travel", travel_cm: 175.5 });
  form(view).elements.travel_cm.value = "";
  await view.editor._save("travel");
  assert.equal(view.calls.at(-1).travel_cm, null);
});

test("profile reference edits include explicit clearing and invalid dimensions never reach the backend", async () => {
  const data = geometrySnapshot();
  const view = setup({ read: () => data, write: () => data }); await view.open();
  form(view).elements.reference_travel_cm.value = "250.5";
  await view.editor._save("update");
  assert.equal(view.calls.at(-1).profile.reference_travel_cm, 250.5);
  form(view).elements.reference_travel_cm.value = "";
  await view.editor._save("update");
  assert.equal(view.calls.at(-1).profile.reference_travel_cm, null);
  const count = view.calls.length;
  form(view).elements.reference_travel_cm.value = "0";
  await view.editor._save("update");
  form(view).elements.travel_cm.value = "10001";
  await view.editor._save("travel");
  assert.equal(view.calls.length, count);
});

test("a dirty travel remains a draft on concurrent revision and reference changes invalidate preview", async () => {
  let data = geometrySnapshot(); data.profiles[0].uses = 2;
  const view = setup({ read: () => data, write: request => ({ ...impact(request), before: data.profiles[0] }) });
  await view.open();
  await view.editor._save("shared");
  assert.match(view.host.querySelector("#profile-impact").textContent, /200 → 200 cm/);
  const input = form(view).elements.reference_travel_cm;
  input.value = "220"; input.dispatchEvent(new dom.window.Event("input", { bubbles: true }));
  assert.equal(view.host.querySelector("#profile-impact").hidden, true);
  data = { ...data, revision: 1 };
  view.push(1); await tick();
  assert.equal(form(view).elements.reference_travel_cm.value, "220");
  assert.match(view.host.querySelector("#profile-error").textContent, /bozza è conservata/);
});

test("same-revision completion updates effective scaling without discarding a geometry draft", async () => {
  let data = { ...geometrySnapshot(), pending: true };
  const view = setup({ read: () => data }); await view.open();
  form(view).elements.travel_cm.value = "175";
  data = { ...data, pending: false, effective_opening_time: 30, effective: { opening: { value: 30, origin: "profile", scaled: false } } };
  await view.editor._refresh(true);
  assert.equal(form(view).elements.travel_cm.value, "175");
  assert.equal(view.host.querySelector("#profile-effective-opening").textContent, "30");
  assert.doesNotMatch(view.host.querySelector("#profile-scaling-status").textContent, /adattati alla corsa/);
  assert.equal(view.host.querySelector("#profile-pending").hidden, true);
});

test("closed movement reservation blocks calibration without offering recovery until refresh confirms release", async () => {
  let calibration = { entity_id: "cover.test", waiting_for_stop: true, recoverable: false, attached: false };
  const view = setup({ read: () => ({ ...snapshot(), calibration }) });
  await view.open();
  assert.match(view.host.querySelector("#cal-recovery").textContent, /10 minuti/);
  assert.equal(view.host.querySelector("#cal-resume").hidden, true);
  assert.equal(view.host.querySelector("#profile-calibrate").disabled, true);
  calibration = null;
  view.host.querySelector("#cal-refresh").click(); await tick();
  assert.equal(view.host.querySelector("#cal-recovery").hidden, true);
  assert.equal(view.host.querySelector("#profile-calibrate").disabled, false);
});

test("two open editors reconcile a shared save, including usage, additions and deletion", async () => {
  const a = setup(), b = setup(); await a.open(); await b.open();
  assert.deepEqual(a.subscriptions[0].request, { type: "myhome/cover_profiles/subscribe", entry_id: "one" });
  const updated = { ...snapshot(1), assigned_profile_id: null,
    profiles: [{ id: "new", name: "New shared", opening_time: 25, closing_time: 45, uses: 2,
      assigned_to: [{ name: "Room A" }, { name: "Room B" }] }] };
  for (const view of [a, b]) { view.setData(updated); view.subscriptions[0].callback({ entry_id: "one", revision: 1, kind: "changed" }); }
  await tick();
  for (const view of [a, b]) {
    assert.equal(form(view).elements.profile.options.length, 2);
    assert.equal(form(view).elements.profile.options[1].value, "new");
    form(view).elements.profile.value = "new"; form(view).elements.profile.onchange();
    assert.match(view.host.querySelector("#profile-usage").textContent, /Room A, Room B/);
    assert.equal(view.host.querySelector("#profile-delete").hidden, true);
  }
  a.editor.close(); assert.equal(a.subscriptions[0].stopped, true);
});

test("remote changes preserve a focused draft and require explicit reload before writes", async () => {
  const view = setup(); await view.open();
  const input = form(view).elements.profile_name; input.value = "My draft"; input.focus(); input.setSelectionRange(2, 5);
  view.push(1); await tick();
  assert.equal(form(view).elements.profile_name, input);
  assert.equal(input.value, "My draft"); assert.equal(document.activeElement, input); assert.equal(input.selectionStart, 2);
  assert.match(view.host.querySelector("#profile-error").textContent, /bozza è conservata/);
  assert.equal(view.host.querySelector('[data-profile-action="update"]').disabled, true);
  await view.editor._save("update"); assert.equal(view.calls.some((request) => request.type.endsWith("/write")), false);
  view.host.querySelector("#profile-reload").click(); await tick();
  assert.equal(form(view).elements.profile_name.value, "Saved 1"); assert.equal(view.subscriptions[0].stopped, true);
});

test("changes during initial read and repeated, reordered or other-gateway events reconcile once", async () => {
  const pending = deferred(); let reads = 0;
  const view = setup({ read: (data) => ++reads === 1 ? pending.promise : structuredClone(data) });
  const opened = view.open(); await tick(); view.push(1); pending.resolve(snapshot(0)); await opened; await tick();
  assert.equal(form(view).elements.profile_name.value, "Saved 1"); assert.equal(reads, 2);
  view.push(1); view.push(0); view.push(200, { entry_id: "two" }); await tick(); assert.equal(reads, 2);
  // A new ready event on the same HA connection reconciles edits made offline.
  view.push(3, { kind: "ready" }); await tick(); assert.equal(form(view).elements.profile_name.value, "Saved 3");
});

test("typing during a refresh preserves the new draft; pending deletion confirmation also counts", async () => {
  const pending = deferred(); let reads = 0;
  const view = setup({ read: (data) => ++reads === 1 ? data : pending.promise }); await view.open();
  view.push(1); form(view).elements.closing_time.value = "55.5"; pending.resolve(snapshot(1)); await tick();
  assert.equal(form(view).elements.closing_time.value, "55.5"); assert.equal(view.host.querySelector("#profile-reload").hidden, false);
  const other = setup(); await other.open();
  other.host.querySelector("#profile-delete-confirmation").hidden = false;
  other.push(1); await tick(); assert.equal(other.host.querySelector("#profile-delete-confirmation").hidden, false);
  assert.equal(other.host.querySelector('[data-profile-action="delete"]').disabled, true);
});

test("own save event before its response does not flag a conflict; a later remote revision is fetched", async () => {
  const pending = deferred(); const view = setup({ write: () => pending.promise }); await view.open();
  form(view).elements.profile_name.value = "Edited";
  const saving = view.editor._save("update"); view.push(1); view.push(2);
  assert.equal(view.calls.filter((request) => request.type.endsWith("/read")).length, 1);
  pending.resolve(snapshot(1)); await saving; await tick();
  assert.equal(form(view).elements.profile_name.value, "Saved 2");
  assert.equal(view.host.querySelector("#profile-error").hidden, true);
});

test("late subscriptions and reads cannot revive a closed or replaced dialog", async () => {
  const pending = deferred(); const view = setup({ subscribe: () => pending.promise });
  const opened = view.open(); await tick(); view.editor.close(); let stopped = false;
  pending.resolve(() => { stopped = true; }); await opened;
  assert.equal(stopped, true); assert.equal(view.calls.length, 0);
  const reading = deferred(); let reads = 0;
  const other = setup({ read: (data) => ++reads === 2 ? reading.promise : data }); await other.open();
  other.push(1); const oldCallback = other.subscriptions[0].callback;
  await other.open(); const currentForm = form(other); reading.resolve(snapshot(9)); oldCallback({ entry_id: "one", revision: 20, kind: "removed" }); await tick();
  assert.equal(form(other), currentForm); assert.equal(other.subscriptions[0].stopped, true);
  other.push(2, { kind: "removed" }); assert.equal(other.host.querySelector("dialog"), null);
});

test("failed subscriptions use visible-only fallback and reconcile on tab resume", async (context) => {
  context.mock.timers.enable({ apis: ["setInterval"] });
  const view = setup({ subscribe: () => { throw new Error("unsupported"); } }); await view.open();
  assert.match(view.host.textContent, /ogni 15 secondi/);
  Object.defineProperty(document, "hidden", { configurable: true, value: true });
  view.setData(snapshot(1)); context.mock.timers.tick(15000); await tick(); assert.equal(form(view).elements.profile_name.value, "Saved 0");
  delete document.hidden; document.dispatchEvent(new dom.window.Event("visibilitychange")); await tick(); assert.equal(form(view).elements.profile_name.value, "Saved 1");
  view.setData(snapshot(2)); context.mock.timers.tick(15000); await tick(); assert.equal(form(view).elements.profile_name.value, "Saved 2");
  view.editor.close(); const count = view.calls.length; context.mock.timers.tick(15000); await tick(); assert.equal(view.calls.length, count);
});

test("profile events leave guided calibration in place and refresh failures allow explicit retry", async () => {
  const view = setup(); await view.open();
  view.editor._calibration.open = ({ host }) => { host.innerHTML = '<input id="cal-draft" value="Measurement">'; };
  view.host.querySelector("#profile-calibrate").click(); view.push(1); await tick();
  assert.equal(view.host.querySelector("#cal-draft").value, "Measurement");
  let fail = false; const other = setup({ read: (data) => { if (fail) throw { code: "target_not_found" }; return data; } }); await other.open();
  fail = true; other.push(1); await tick(); assert.equal(other.host.querySelector("#profile-reload").hidden, false);
  fail = false; other.host.querySelector("#profile-reload").click(); await tick(); assert.equal(form(other).elements.profile_name.value, "Saved 1");
});

test("unscaled timing profiles explain assignment limits in the existing section", async () => {
  const view = setup({ read: () => ({ ...snapshot(), model: "linear_time", scaling: "unscaled" }) });
  await view.open();
  const note = view.host.querySelector("#profile-unscaled");
  assert.match(note.textContent, /senza adattamento all’altezza/);
  assert.equal(note.closest("fieldset"), form(view).querySelector("fieldset"));
  assert.equal(view.host.querySelectorAll(".profile-details").length, 3);
});

test("individual timings save without a profile name, retain assignment and clear each direction explicitly", async () => {
  const view = setup(); await view.open();
  const f = form(view);
  f.elements.profile_name.value = "";
  assert.equal(f.elements.override_opening.disabled, true);
  f.elements.use_opening.checked = true; f.elements.use_opening.onchange();
  f.elements.override_opening.value = "18.25";
  await view.editor._save("overrides");
  const request = view.calls.find((item) => item.action === "overrides");
  assert.deepEqual(request.overrides, { opening: 18.25, closing: null });
  assert.equal("profile_id" in request, false);
  assert.equal("profile" in request, false);
  assert.equal(form(view).elements.override_closing.disabled, true);
});

const impact = (request) => ({ revision: request.revision, confirmation: "proposal-token",
  before: { name: "Saved 0", opening_time: 20, closing_time: 40 }, after: request.profile,
  followers: [{ name: "Bedroom <one>", available: true,
    changes: { opening: { before: 20, after: request.profile.opening_time, overridden: false }, closing: { before: 18, after: 18, overridden: true } } },
  { name: null, entity_id: null, available: false,
    changes: { opening: { before: 20, after: request.profile.opening_time, overridden: false }, closing: { before: 40, after: request.profile.closing_time, overridden: false } } }] });
const sharedSnapshot = () => { const data = snapshot(); data.profiles[0].uses = 2; return data; };

test("shared edits show affected covers and retained overrides before an explicit exact-proposal confirmation", async () => {
  const view = setup({ read: sharedSnapshot, write: (request) => request.action === "preview" ? impact(request) : snapshot(1) });
  await view.open();
  assert.equal(view.host.querySelector('[data-profile-action="update"]').hidden, true);
  assert.equal(view.host.querySelector('[data-profile-action="shared"]').hidden, false);
  form(view).elements.opening_time.value = "25";
  await view.editor._save("shared");
  assert.equal(view.calls.filter((item) => item.type.endsWith("/write")).length, 1);
  const box = view.host.querySelector("#profile-impact");
  assert.equal(box.hidden, false);
  assert.match(box.textContent, /Bedroom <one>/);
  assert.equal(box.querySelector("one"), null);
  assert.match(box.textContent, /Valore personale mantenuto/);
  assert.match(box.textContent, /Non disponibile o rimossa/);
  assert.match(box.textContent, /20 → 25 s/);
  await view.editor._save("confirm_shared");
  const request = view.calls.at(-1);
  assert.equal(request.action, "update_shared"); assert.equal(request.confirmation, "proposal-token");
  assert.equal(request.profile.opening_time, 25);
  assert.equal(view.host.querySelector("#profile-impact").hidden, true);
});

test("editing or cancelling a preview invalidates confirmation and remote revisions preserve the draft", async () => {
  const view = setup({ read: sharedSnapshot, write: impact }); await view.open();
  await view.editor._save("shared");
  form(view).elements.opening_time.value = "26";
  form(view).elements.opening_time.dispatchEvent(new dom.window.Event("input", { bubbles: true }));
  assert.equal(view.host.querySelector("#profile-impact").hidden, true);
  const count = view.calls.length; await view.editor._save("confirm_shared"); assert.equal(view.calls.length, count);
  await view.editor._save("shared"); view.host.querySelector("#profile-impact-cancel").click();
  assert.equal(view.editor._preview, null);
  // An open preview counts as a draft even if none of its input values changed.
  const other = setup({ write: impact }); await other.open(); await other.editor._save("shared");
  other.push(1); await tick();
  assert.equal(other.host.querySelector('[data-profile-action="confirm_shared"]').disabled, true);
  assert.equal(other.host.querySelector('#profile-impact').hidden, true);
  assert.equal(other.editor._preview, null);
  const writes = other.calls.length; await other.editor._save("confirm_shared"); assert.equal(other.calls.length, writes);
});

test("personal timing drafts survive remote edits and storage failure leaves checkbox availability intact", async () => {
  const view = setup(); await view.open();
  form(view).elements.use_closing.checked = true; form(view).elements.use_closing.onchange();
  form(view).elements.override_closing.value = "44";
  view.push(1); await tick();
  assert.equal(form(view).elements.override_closing.value, "44");
  assert.equal(view.host.querySelector('[data-profile-action="overrides"]').disabled, true);
  const other = setup({ write: () => { throw { code: "storage_error" }; } }); await other.open();
  form(other).elements.use_opening.checked = true; form(other).elements.use_opening.onchange();
  await other.editor._save("overrides");
  assert.equal(form(other).elements.override_opening.disabled, false);
  assert.equal(form(other).elements.override_closing.disabled, true);
  assert.equal(other.host.querySelector("#profile-error").hidden, false);
});

test("late preview responses cannot replace a newly opened cover editor", async () => {
  const pending = deferred(); const view = setup({ write: () => pending.promise }); await view.open();
  const saving = view.editor._save("shared");
  const request = view.calls.at(-1);
  await view.open(); const current = form(view);
  pending.resolve(impact(request)); await saving;
  assert.equal(form(view), current);
  assert.equal(view.host.querySelector("#profile-impact").hidden, true);
});

test("remote updates retain the selected calibration mode and direction until explicit reload", async () => {
  const view = setup(); await view.open();
  const direction = view.host.querySelector('#cal-direction');
  direction.value = 'opening'; direction.dispatchEvent(new dom.window.Event('change', { bubbles: true }));
  view.push(1); await tick();
  assert.equal(view.host.querySelector('#cal-direction'), direction);
  assert.equal(direction.value, 'opening');
  assert.equal(view.host.querySelector('#profile-calibrate').disabled, true);
  assert.equal(view.host.querySelector('#profile-reload').hidden, false);
});

test("same-revision availability changes update pristine editors in both directions", async () => {
  const view = setup(); await view.open();
  view.setData({ ...snapshot(), writable: false, reason: 'cover_unavailable' });
  await view.editor._refresh(true);
  assert.equal(form(view).elements.profile.disabled, true);
  assert.equal(view.host.querySelector('#profile-calibrate').disabled, true);
  view.setData(snapshot()); await view.editor._refresh(true);
  assert.equal(form(view).elements.profile.disabled, false);
  assert.equal(view.host.querySelector('#profile-calibrate').disabled, false);
});

test("same-revision unavailability preserves drafts but blocks writes until explicit reload", async () => {
  const view = setup(); await view.open();
  const input = form(view).elements.profile_name; input.value = 'Do not lose'; input.focus();
  view.setData({ ...snapshot(), writable: false, reason: 'cover_unavailable' });
  await view.editor._refresh(true);
  assert.equal(form(view).elements.profile_name, input);
  assert.equal(input.value, 'Do not lose');
  assert.equal(view.host.querySelector('[data-profile-action="update"]').disabled, true);
  assert.equal(view.host.querySelector('#profile-reload').hidden, false);
  await view.editor._save('update');
  assert.equal(view.calls.some(item => item.type.endsWith('/write')), false);
});

for (const [id, value] of [['cal-direction', 'closing'], ['cal-mode', 'automatic']]) {
  test(`remote changes preserve ${id}=${value}`, async () => {
    const view = setup(); await view.open();
    const selector = view.host.querySelector(`#${id}`);
    selector.value = value; selector.dispatchEvent(new dom.window.Event('change', { bubbles: true }));
    view.push(1); await tick();
    assert.equal(view.host.querySelector(`#${id}`), selector);
    assert.equal(selector.value, value);
    assert.equal(view.host.querySelector('#profile-calibrate').disabled, true);
  });
}

test('HA availability transitions trigger an authoritative read without polling every position update', async () => {
  const view = setup();
  view.context.hass.states['cover.test'] = { state: 'open', attributes: {} };
  await view.open();
  const reads = () => view.calls.filter(item => item.type.endsWith('/read')).length;
  assert.equal(reads(), 1);
  view.setData({ ...snapshot(), writable: false, reason: 'cover_unavailable' });
  view.editor.updateState({ states: { 'cover.test': { state: 'unavailable', attributes: {} } } });
  await tick(); assert.equal(form(view).elements.profile.disabled, true);
  assert.equal(reads(), 2);
  view.editor.updateState({ states: { 'cover.test': { state: 'unavailable', attributes: {} } } });
  await tick(); assert.equal(reads(), 2);
  view.setData(snapshot());
  view.editor.updateState({ states: { 'cover.test': { state: 'closed', attributes: {} } } });
  await tick(); assert.equal(form(view).elements.profile.disabled, false);
  view.editor.updateState({ states: { 'cover.test': { state: 'opening', attributes: { current_position: 35 } } } });
  await tick(); assert.equal(reads(), 3);
});
