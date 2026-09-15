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
