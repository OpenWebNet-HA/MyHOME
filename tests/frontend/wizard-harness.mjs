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
    // Review P5: a value above what the field accepts is refused while typing: the button is off, there is nothing to press.
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
    assert.ok(radio && !radio.disabled, `path ${path} can be chosen on ${code()}`);
    radio.checked = true;
    radio.dispatchEvent(new Event("change", { bubbles: true }));
    await tick();
    await press("path");
  };
  const startFor = async (entity, path = "A") => {
    tab().querySelector('[data-action="cal-tab-measure"]').click(); await tick();
    await pickCover(entity);
    await press("choose");
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

