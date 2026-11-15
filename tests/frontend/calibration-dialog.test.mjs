/**
 * From 720 px (not a landscape phone) the wizard opens in a modal dialog over the list;
 * the ✕ everywhere, Escape and the browser's Back as the ✕, the footer's ⋮ menu, the window crossing the phone's edge.
 * jsdom has no window rules (`matchMedia` absent): the panel lays itself out as on a desktop, so the dialog is the default.
 */
import assert from "node:assert/strict";
import { after, afterEach, test } from "node:test";

import { dom, mount, move, reset, tick, waitFor } from "./wizard-harness.mjs";

afterEach(reset);
after(() => dom.window.close());

const key = (target, name, extra = {}) => target.dispatchEvent(new dom.window.KeyboardEvent("keydown", { key: name, bubbles: true, cancelable: true, composed: true, ...extra }));
const resumed = (extra) => ({ entry_id: "one", entity_id: "cover.living_one", session_id: "session-1", sequence: 9, revision: 7, mode: "geometry",
  reason: null, values: { opening_time: 22, closing_time: 21 }, samples: { lift: 4 }, readings: { gap: 3 },
  geometry: {}, travel_cm: 198, stop_requested: false, slats: true, lift_attempts: 1, still_resting: false, gap_warning: false,
  touching_cm: 1, max_gap_cm: 20, gap_warn_cm: 10, saved_travel_cm: 195, expected_cm: 99, can_repeat: true, save_modes: ["new"], ...extra });

test("the wizard opens in a modal dialog over the list, named by its title, the focus on the step's title", async () => {
  const { tab, wiz, root, code } = await mount();
  tab().querySelector('[data-action="cal-tab-measure"]').click(); await tick();
  const flow = tab().querySelector("dialog.cal-flow");
  assert.ok(flow?.open, "a modal <dialog> in the tab");
  assert.deepEqual([flow.getAttribute("aria-modal"), flow.getAttribute("aria-labelledby")], ["true", "wiz-cover"]);
  // The box holds the tendina's handle and the body the content goes in.
  assert.equal(wiz().parentElement, flow.querySelector(":scope > .cal-flow-box > .cal-flow-body"), "the wizard in the dialog's box");
  assert.ok(flow.querySelector(":scope > .cal-flow-box > .cal-flow-handle[aria-hidden='true']"), "the handle, beside the body");
  assert.equal(flow.querySelector(".cal-flow-box").dataset.kind, "flow", "a flow: the fixed height");
  assert.equal(code(), "A-01");
  assert.ok(tab().querySelector(".cal-list-rows"), "the list stays under it");
  assert.equal(root.activeElement, wiz().querySelector(".wiz-title"), "the focus on the title");
  // The page under it is held (no scroll behind the scrim) and let go when it closes.
  assert.equal(document.documentElement.style.overflow, "hidden");
  wiz().querySelector(".wiz-head .wiz-x").click(); await tick();
  assert.equal(tab().querySelector("dialog.cal-flow"), null);
  assert.equal(document.documentElement.style.overflow, "");
});

test("the medium dialog's steps column has its title; the header keeps «Passi» and the segments for the narrow one (the stylesheet chooses)", async () => {
  const { wiz, startFor } = await mount();
  await startFor("cover.living_one");
  const column = wiz().querySelector(".wiz-columns > nav.wiz-steps");
  // Headed by the process (here «Calibrate»), in place of the generic «Steps» (still its name).
  assert.equal(column.querySelector(":scope > .wiz-steps-head > .wiz-steps-title").textContent, "Calibrate");
  assert.equal(column.getAttribute("aria-label"), "Steps");
  assert.equal(column.querySelector(":scope > ol > li .wiz-step-label").textContent, "Choosing the path");
  // The phone's «Passi» sheet is headed the same way.
  assert.equal(wiz().querySelector(".wiz-sheet .wiz-steps-head > .wiz-steps-title").textContent, "Calibrate");
  assert.ok(wiz().querySelector(".wiz-head > .wiz-head-steps:not([hidden])"));
  assert.ok(wiz().querySelector(".wiz-head > .wiz-segments > span"));
});

test("Escape is the ✕: no session, back to the list; a session asks first, and Escape in the question only closes it", async () => {
  const first = await mount();
  first.tab().querySelector('[data-action="cal-tab-measure"]').click(); await tick();
  key(first.wiz(), "Escape"); await tick();
  assert.equal(first.wiz(), null, "A-01: straight back to the list");
  assert.equal(first.tab().querySelector("dialog.cal-flow"), null);
  const { backend, wiz, press, startFor, tab, actions } = await mount();
  await startFor("cover.living_one");
  await press("begin"); await move(backend);
  key(wiz().querySelector(".wiz-main"), "Escape"); await tick();
  const question = wiz().querySelector(".wiz-dialog:not(.wiz-nav-dialog)");
  assert.equal(question.hidden, false, "«Close without saving?»");
  key(question, "Escape"); await tick();
  assert.equal(question.hidden, true, "Escape closes the question");
  assert.ok(wiz(), "and only the question");
  // Any other close request of the dialog (Android's back) is the ✕ too, the dialog never closes by itself.
  const cancel = new dom.window.Event("cancel", { cancelable: true });
  tab().querySelector("dialog.cal-flow").dispatchEvent(cancel); await tick();
  assert.equal(cancel.defaultPrevented, true);
  assert.equal(wiz().querySelector(".wiz-dialog:not(.wiz-nav-dialog)").hidden, false, "it asks");
  wiz().querySelector('[data-wiz="leave"]').click(); await tick(6);
  assert.equal(actions().at(-1), "cancel");
  assert.equal(wiz(), null);
});

test("the keyboard stays in the dialog: Tab from the last control goes to the first, Shift+Tab from the first to the last", async () => {
  const { tab, wiz, root, startFor } = await mount();
  await startFor("cover.living_one");
  const flow = tab().querySelector("dialog.cal-flow");
  const focusable = () => [...flow.querySelectorAll("button, [href], input, select, textarea, summary, [tabindex]:not([tabindex='-1'])")]
    .filter((element) => !element.disabled && !element.closest("[hidden], [inert]"));
  const [first, last] = [focusable()[0], focusable().at(-1)];
  assert.ok(first && last && first !== last);
  last.focus(); key(last, "Tab"); await tick();
  assert.equal(root.activeElement, first, "Tab wraps to the first");
  key(first, "Tab", { shiftKey: true }); await tick();
  assert.equal(root.activeElement, focusable().at(-1), "Shift+Tab wraps to the last");
  // The focus outside the dialog (it should never be) is brought back in.
  tab().querySelector(".cal-list-rows button")?.focus();
  key(tab(), "Tab"); await tick();
  assert.ok(flow.contains(root.activeElement));
  assert.ok(wiz());
});

test("the browser's Back is the ✕: the dialog leaves its history entry; with a session it asks and keeps an entry for the next Back", async () => {
  const { tab, wiz, backend, press, startFor } = await mount();
  tab().querySelector('[data-action="cal-tab-measure"]').click(); await tick();
  assert.ok(Number.isInteger(window.history.state?.myhomeCalibration), "one entry while the flow is open");
  window.history.back();
  await waitFor(() => !wiz(), "Back closes the wizard");
  assert.equal(tab().querySelector("dialog.cal-flow"), null);
  await startFor("cover.living_one");
  await press("begin"); await move(backend);
  const entry = window.history.state?.myhomeCalibration;
  window.history.back();
  await waitFor(() => wiz()?.querySelector(".wiz-dialog:not(.wiz-nav-dialog)")?.hidden === false, "Back asks first");
  await waitFor(() => Number.isInteger(window.history.state?.myhomeCalibration) && window.history.state.myhomeCalibration !== entry, "a new entry for the next Back");
  wiz().querySelector('[data-wiz="stay"]').click(); await tick();
  assert.ok(wiz(), "the calibration goes on");
  // Closed by its own ✕, the flow takes its entry away.
  wiz().querySelector(".wiz-head .wiz-x").click(); await tick();
  wiz().querySelector('[data-wiz="leave"]').click(); await tick(6);
  await waitFor(() => !Number.isInteger(window.history.state?.myhomeCalibration), "the entry goes with the flow");
});

test("the footer's ⋮ menu (A-34): two buttons in view, the others in a menu that opens, moves with the arrows and closes by Escape, a press outside or a choice", async () => {
  const { panel, wiz, root, code, startFor } = await mount({ previews: false });
  await startFor("cover.living_one");
  const wizard = panel._calibrationTab._wizard;
  Object.assign(wizard._local, { stage: "saved", saved: { target: "profile_new", name: "living_room_shutter_1" } });
  wizard._render(); await tick();
  assert.equal(code(), "A-34");
  const bar = wiz().querySelector(".wiz-actions");
  assert.ok(bar.classList.contains("is-footer"));
  // The primary and its secondary grouped on the right.
  assert.deepEqual([...bar.querySelectorAll(":scope > .wiz-footer-main > .wiz-action")].map((item) => item.dataset.wiz), ["close", "assign-profile"]);
  assert.equal(bar.querySelector(".wiz-more"), null, "no «Other options» disclosure in the dialog");
  const toggle = () => wiz().querySelector(".wiz-overflow-toggle"), menu = () => wiz().querySelector(".wiz-overflow-menu");

  assert.deepEqual([toggle().getAttribute("aria-haspopup"), toggle().getAttribute("aria-controls"), menu().id, menu().getAttribute("role"), menu().hidden],
    ["menu", "wiz-overflow-menu", "wiz-overflow-menu", "menu", true]);
  assert.deepEqual([...menu().querySelectorAll('[role="menuitem"]')].map((item) => [item.dataset.wiz, item.textContent]),
    [["another", "Calibrate another cover"], ["open-cover", "Open the cover page"]]);
  // From the keyboard: open, the first item focused, the arrows wrap, Escape closes it (the flow stays) back on ⋮.
  key(toggle(), "Enter"); toggle().click(); await tick();
  assert.deepEqual([menu().hidden, toggle().getAttribute("aria-expanded")], [false, "true"]);
  assert.equal(root.activeElement, menu().querySelector('[data-wiz="another"]'));
  key(root.activeElement, "ArrowDown"); assert.equal(root.activeElement.dataset.wiz, "open-cover");
  key(root.activeElement, "ArrowDown"); assert.equal(root.activeElement.dataset.wiz, "another", "the arrows wrap");
  key(root.activeElement, "Escape"); await tick();
  assert.deepEqual([menu().hidden, toggle().getAttribute("aria-expanded"), root.activeElement === toggle()], [true, "false", true]);
  assert.ok(wiz(), "Escape closed the menu only");
  // A press outside it closes it.
  toggle().click(); await tick();
  document.body.dispatchEvent(new dom.window.Event("pointerdown", { bubbles: true, composed: true }));
  assert.equal(menu().hidden, true, "closed by a press outside");
  // A choice closes it and acts.
  toggle().click(); await tick();
  menu().querySelector('[data-wiz="another"]').click(); await tick();
  assert.equal(code(), "A-01", "«Calibrate another cover»");
});

test("an open session resumes in the dialog, the owner's and a reader's alike; the reader's ✕ only stops following", async () => {
  const view = { phase: "briefing", step: "half_open", reading_kind: "half_open" };
  const owner = await mount({ session: resumed(view) });
  owner.tab().querySelector('[data-action="cal-tab-session"]').click(); await tick(6);
  assert.equal(owner.code(), "A-15");
  assert.ok(owner.tab().querySelector("dialog.cal-flow[open] .wiz"), "the owner's wizard in the dialog");
  key(owner.wiz(), "Escape"); await tick();
  assert.equal(owner.wiz().querySelector(".wiz-dialog:not(.wiz-nav-dialog)").hidden, false, "Escape asks while the session is open");
  const { backend, tab, wiz } = await mount({ session: resumed({ ...view, screen: { code: "A-15", path: "A", scope: null, local: { ackLift: true, ackAscent: true } } }), owner: false });
  tab().querySelector('[data-action="cal-tab-session"]').click(); await tick(6);
  assert.ok(tab().querySelector("dialog.cal-flow[open] .wiz"), "the reader's wizard in the dialog");
  const bar = wiz().querySelector(".wiz-actions");
  // The reader's note at the top of the content; the footer: «Prendi il controllo» on the right.
  assert.ok(wiz().querySelector(".wiz-main > .wiz-readonly .wiz-readonly-note"), "the reader's note in the content");
  assert.equal(bar.querySelector(".wiz-readonly"), null, "not in the footer");
  assert.ok(bar.querySelector(":scope > .wiz-footer-main > .wiz-take.primary"), "Take control, the footer's primary");
  assert.equal(wiz().querySelector(".wiz-head .wiz-x").getAttribute("aria-label"), "Stop following");
  const sent = backend.calls.length;
  wiz().querySelector(".wiz-head .wiz-x").click(); await tick(8);
  assert.equal(wiz(), null, "back to the list, no question");
  assert.equal(backend.calls.slice(sent).some((call) => call.action === "cancel"), false, "nothing cancelled");
});

test("the wizard is in the dialog on every window; crossing a band changes only its bar's shape, keeping where it is", async () => {
  const original = globalThis.matchMedia;
  globalThis.matchMedia = (query) => ({ matches: query.includes("max-width") });
  try {
    const { panel, tab, wiz, code, startFor } = await mount();
    await startFor("cover.living_one");
    assert.equal(code(), "A-03");
    assert.ok(tab().querySelector("dialog.cal-flow > .cal-flow-box > .cal-flow-body > .wiz"), "the phone: the tendina, over the list");
    assert.ok(tab().querySelector(".cal-list-rows"), "the list stays under it");
    assert.equal(wiz().querySelector(".wiz-actions").classList.contains("is-footer"), false, "the phone's column");
    const node = wiz();
    globalThis.matchMedia = () => ({ matches: false });
    panel._calibrationTab._relayoutFlow(); await tick();
    assert.equal(wiz(), node, "the same wizard, not moved");
    assert.equal(code(), "A-03");
    assert.equal(wiz().querySelector(".wiz-actions").classList.contains("is-footer"), true, "the medium dialog's footer row");
    globalThis.matchMedia = (query) => ({ matches: query.includes("max-width") });
    panel._calibrationTab._relayoutFlow(); await tick();
    assert.ok(tab().querySelector("dialog.cal-flow .wiz"));
    assert.equal(wiz().querySelector(".wiz-actions").classList.contains("is-footer"), false);
  } finally { globalThis.matchMedia = original; }
});

test("on the phone a cover's page has a ✕ and the browser's Back closes it, as the wizard's", async () => {
  const original = globalThis.matchMedia;
  globalThis.matchMedia = (query) => ({ matches: query.includes("max-width") || query.includes("reduced-motion") });
  try {
    const { tab } = await mount({ language: "it" });
    [...tab().querySelectorAll(".cal-row")].find((row) => row.dataset.entity === "cover.living_one").querySelector(".cal-row-toggle").click(); await tick();
    assert.ok(tab().querySelector(".cal-page"));
    assert.equal(tab().querySelector(".cal-page-close ha-icon").getAttribute("icon"), "mdi:close");
    window.history.back();
    await waitFor(() => !tab().querySelector(".cal-page"), "Back closes the page");
  } finally { globalThis.matchMedia = original; }
});

test("closing costs nothing before a session and after it ends (the handle, the drag); during a session only the ✕ with its question", async () => {
  const { tab, wiz, backend, press, startFor, panel, code } = await mount();
  tab().querySelector('[data-action="cal-tab-measure"]').click(); await tick();
  assert.ok(wiz().hasAttribute("data-closable"), "A-01: nothing to lose");
  wiz().querySelector(".wiz-head .wiz-x").click(); await tick();
  await startFor("cover.living_one");
  assert.ok(wiz().hasAttribute("data-closable"), "A-03, before the session");
  await press("begin"); await move(backend);
  assert.equal(wiz().hasAttribute("data-closable"), false, "a session open: no handle, no drag");
  const wizard = panel._calibrationTab._wizard;
  Object.assign(wizard._local, { stage: "saved", saved: { target: "profile_new", name: "living_room_shutter_1" } });
  wizard._dropGateway(); wizard._render(); await tick();
  assert.equal(code(), "A-34");
  assert.ok(wiz().hasAttribute("data-closable"), "saved: closing costs nothing");
});

test("a drag down closes the tendina as its ✕ does, only in the tendina, only while closable, from the header or the content at its top", async () => {
  const { dragOverlay, DRAG_SHEET } = await import("../../custom_components/myhome/frontend/panel/panel-calibration-layout.js");
  const original = globalThis.matchMedia;
  const doc = dom.window.document;
  const box = doc.createElement("div");
  box.innerHTML = '<header class="wiz-head"><button class="wiz-x">x</button></header><div class="wiz-main"><p>content</p></div>';
  doc.body.append(box);
  const main = box.querySelector(".wiz-main");
  let top = 0;
  Object.defineProperty(main, "scrollTop", { get: () => top, configurable: true });
  const touch = (type, target, y) => {
    const event = new dom.window.Event(type, { bubbles: true, cancelable: true });
    const point = { identifier: 1, clientY: y, target };
    Object.assign(event, { touches: type === "touchend" ? [] : [point], changedTouches: [point] });
    target.dispatchEvent(event);
    return event;
  };
  const drag = (target, distance) => { touch("touchstart", target, 100); const move = touch("touchmove", target, 100 + distance / 2); touch("touchmove", target, 100 + distance); touch("touchend", target, 100 + distance); return move; };
  let closable = true, closed = 0;
  const stop = dragOverlay(box, { closable: () => closable, onClose: () => { closed += 1; } });
  try {
    globalThis.matchMedia = (query) => ({ matches: query.includes("max-width: 511px") });
    const moved = drag(box.querySelector(".wiz-head"), DRAG_SHEET + 10);
    assert.equal(closed, 1, "from the header: closed");
    assert.equal(moved.defaultPrevented, true, "the page under it does not scroll meanwhile");
    drag(box.querySelector(".wiz-head"), 40);
    assert.equal(closed, 1, "a short drag goes back");
    drag(main.firstElementChild, DRAG_SHEET + 10);
    assert.equal(closed, 2, "from the content at its top");
    top = 50;
    drag(main.firstElementChild, DRAG_SHEET + 10);
    assert.equal(closed, 2, "the content scrolled: a scroll, not a drag");
    top = 0;
    touch("touchstart", main.firstElementChild, 100); touch("touchmove", main.firstElementChild, 60); touch("touchmove", main.firstElementChild, 300); touch("touchend", main.firstElementChild, 300);
    assert.equal(closed, 2, "a finger going up first scrolls");
    closable = false;
    drag(box.querySelector(".wiz-head"), DRAG_SHEET + 10);
    assert.equal(closed, 2, "a session open: no drag");
    closable = true;
    globalThis.matchMedia = () => ({ matches: false });
    drag(box.querySelector(".wiz-head"), DRAG_SHEET + 10);
    assert.equal(closed, 2, "a centred dialog does not drag");
  } finally { globalThis.matchMedia = original; stop(); box.remove(); }
});
