/** Shared setup of the wizard tests: jsdom, the panel on the Calibration tab, the fake backend. */
import assert from "node:assert/strict";
import { JSDOM } from "jsdom";

import { createFakeBackend } from "./fake-calibration.mjs";

export const dom = new JSDOM("<!doctype html><html><body></body></html>", { url: "http://localhost/", pretendToBeVisual: true });
for (const key of ["window", "document", "HTMLElement", "customElements", "CustomEvent", "Event", "KeyboardEvent", "history"]) {
  globalThis[key] = key === "window" ? dom.window : dom.window[key];
}
dom.window.HTMLDialogElement.prototype.showModal = function () { this.open = true; };
dom.window.HTMLDialogElement.prototype.close = function () { this.open = false; };
await import("../../custom_components/myhome/frontend/panel/myhome-panel.js");
const { CalibrationWizard } = await import("../../custom_components/myhome/frontend/panel/panel-calibration-wizard.js");

export const tick = async (count = 4) => { for (let i = 0; i < count; i++) await new Promise((resolve) => setImmediate(resolve)); };
// Preview stages advance on zero-delay timers.
export const settle = async () => { for (let i = 0; i < 6; i++) { await new Promise((resolve) => setTimeout(resolve, 1)); await tick(); } };
/** Wait until `predicate()` holds, for timers the tests do not control. */
export async function waitFor(predicate, label = "condition") {
  for (let i = 0; i < 200; i++) { if (predicate()) return; await new Promise((resolve) => setTimeout(resolve, 5)); }
  assert.fail(`timed out waiting for ${label}`);
}
/** The details on one of their two tabs: «overview» («Panoramica») or «details» («Dettagli»). */
export async function pageTab(tab, id = "details") {
  const button = tab().querySelector(`.cal-flow #cal-page-tab-${id}`);
  assert.ok(button, `the details' tab ${id}`);
  button.click();
  await tick(6);
}
/**
 * Ricalibra from a cover's card goes through «Controlla il posizionamento»: «Apertura e chiusura complete», then its
 * «Misura di nuovo i tempi». On a follower there is no question: the outcome opens at once. `host` is the tab's element
 * (or a function giving it).
 */
export async function recalibrateFromCard(host) {
  const root = () => (typeof host === "function" ? host() : host);
  for (const [action, id] of [["cal-tab-check-start"], ["cal-tab-check-answer", "full"], ["cal-tab-measure-cover"]]) {
    if (action === "cal-tab-check-answer" && !root().querySelector('.cal-page [data-action="cal-tab-check-answer"]')) continue;
    const button = root().querySelector(`.cal-page [data-action="${action}"]${id ? `[data-id="${id}"]` : ""}`);
    assert.ok(button, `${action} ${id ?? ""} on the card`);
    button.click();
    await tick(6);
  }
}
/**
 * The list's action in view is «Controlla» on a calibrated cover and a follower's card has no Perfeziona yet: the flow
 * itself is opened as the card's Perfeziona opens it (`_openWizard({ start: "refine" })`).
 */
export async function refineCover(context, entity) {
  const tab = context.panel._calibrationTab, item = tab._covers().find((entry) => entry.cover.entity_id === entity);
  assert.ok(item, `${entity} in the list`);
  tab._openWizard({ start: "refine", item });
  await tick();
}
export function reset() { document.body.replaceChildren(); window.localStorage.clear(); window.history.replaceState(null, "", "/"); }

/** The panel on the Calibration tab, with the fake backend; preview stages advance with no delay. */
export async function mount(options = {}) {
  const backend = createFakeBackend(options);
  const panel = document.createElement("myhome-panel");
  panel.hass = backend.hass;
  document.body.append(panel);
  await tick();
  const root = panel.shadowRoot;
  root.querySelector('[data-action="cover-view"][data-id="calibration"]').click();
  await tick(8);
  panel._calibrationTab.useWizard(new CalibrationWizard({ stageMs: options.stageMs ?? 0, guardMs: options.guardMs ?? 0, previews: options.previews ?? true }));
  const tab = () => root.getElementById("cover-calibration-tab");
  const wiz = () => tab().querySelector(".wiz");
  const code = () => wiz()?.dataset.code;
  const button = (id) => wiz().querySelector(`.wiz-frame [data-wiz="${id}"]`);
  const press = async (id) => {
    const target = button(id);
    assert.ok(target, `button ${id} on ${code()}`);
    assert.equal(target.disabled, false, `button ${id} enabled on ${code()}`);
    target.click();
    await tick();
  };
  const type = async (value) => {
    const input = wiz().querySelector('.wiz-frame [name="reading_cm"]');
    input.value = value;
    input.dispatchEvent(new Event("input", { bubbles: true }));
    // A value above what the field accepts is refused while typing: the button is off, there is nothing to press.
    if (button("reading")?.disabled && wiz().querySelector("#wiz-field-error:not([hidden])")) { await tick(); return; }
    await press("reading");
  };
  const actions = () => backend.calls.filter((message) => message.type === "myhome/cover_calibration/action" && !["heartbeat", "show"].includes(message.action)).map((message) => message.action);
  const title = () => wiz().querySelector(".wiz-title").textContent;
  /** A-01 starts empty: pick a cover by its position in the drop-down (0 = the first cover). */
  /** A-01: tick the radio of the cover at this place in the list (or of this entity id). */
  const pickCover = async (which = 0) => {
    const radios = [...wiz().querySelectorAll('.wiz-picker input[name="cover"]')];
    const radio = typeof which === "number" ? radios[which] : radios.find((input) => JSON.parse(input.value)[1] === which);
    assert.ok(radio, `cover ${which} in the A-01 list`);
    radio.checked = true;
    radio.dispatchEvent(new Event("change", { bubbles: true }));
    await tick();
  };
  /** A-02: tick a path in the radio list, then Continue. */
  const choosePath = async (path) => {
    const radio = wiz().querySelector(`input[name="path"][value="${path}"]`);
    // A-02 offers «Ricalibra» (base) and the fine-tuning on the saved times (R) only; path A whole and path B are
    // entered by their ids, as their code stays.
    if (!radio) { panel._calibrationTab._wizard._act(`path:${path}`); await tick(); return; }
    assert.ok(!radio.disabled, `path ${path} can be chosen on ${code()}`);
    radio.checked = true;
    radio.dispatchEvent(new Event("change", { bubbles: true }));
    await tick();
    await press("path");
  };
  /**
   * From the tab's «Calibra una tapparella»: A-01, the cover, then A-02 on a calibrated cover. A cover never
   * calibrated goes straight to the basic calibration's «Preparazione»; another path is entered there by its id as if
   * A-02 had been shown (the tests of path A whole and path B).
   */
  const startFor = async (entity, path = "A") => {
    tab().querySelector('[data-action="cal-tab-measure"]').click(); await tick();
    await pickCover(entity);
    await press("choose");
    if (code() === "A-02") { await choosePath(path); return; }
    if (path === "base") return;
    Object.assign(panel._calibrationTab._wizard._local, { stage: "path", noChoice: false });
    await choosePath(path);
  };
  return { backend, panel, root, tab, wiz, code, button, press, type, actions, title, startFor, pickCover, choosePath };
}

/** Run the backend through one movement: start echo, then the actuator stop when there is one. */
export async function move(backend, { stop = false, auto = false, seconds = 30 } = {}) {
  backend.started();
  backend.tick(seconds);
  // Home Assistant's next view carries the time run so far (a positioning ends once its full time has passed).
  backend.emit();
  await tick();
  if (auto) { backend.autoStop(); await tick(); }
  if (stop) { backend.stopped(); await tick(); }
}

