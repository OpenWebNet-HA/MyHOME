/**
 * The overlays of the Calibration tab: one frame at a time, focus, scrim and handle, menus ⋮, the wheel, what opens comes into view.
 */
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { after, afterEach, describe, test } from "node:test";

import { cascadeCss, rule, winner } from "./css-cascade.mjs";
import { sampleOverview } from "./fake-calibration.mjs";
import { panelCss as css } from "./panel-css.mjs";
import { dom, mount, move, pageTab, recalibrateFromCard, reset, settle, tick } from "./wizard-harness.mjs";

const original = globalThis.matchMedia;
afterEach(() => { globalThis.matchMedia = original; reset(); for (const element of [document.documentElement, document.body]) { element.style.overflow = ""; element.style.overscrollBehaviorY = ""; } });
after(() => dom.window.close());

describe("bands, sheets and menus", async () => {
  test("three bands by width; a flow's dialog has a fixed height (86 %, at most 860 px; the sheet the whole height less its strip)", () => {
    assert.match(cascadeCss, /\.cal-flow-box \{[^}]*width: min\(100%, 480px\); height: min\(86%, 860px\);/, "512–1023: 480 px, a fixed height");
    assert.doesNotMatch(cascadeCss, /\.cal-flow-box \{[^}]*max-height: 86%/, "never «as high as the content» any more");
    assert.match(cascadeCss, /@media \(min-width: 1024px\) \{\s*\.cal-flow-box \{ width: min\(100%, 880px\); \}/, "from 1024 px: 880 px");
    const sheet = cascadeCss.slice(cascadeCss.indexOf("@media (max-width: 511px) {\n  dialog.cal-flow {"));
    assert.match(sheet, /\.cal-flow-box \{ width: 100%; height: calc\(100% - var\(--cal-flow-safe-top\) - var\(--cal-sheet-strip\)\); border-radius: var\(--cal-r-14\) var\(--cal-r-14\) 0 0;/, "up to 511 px: the tendina");
    // A landscape phone (under 500 px high) gets the whole height but 8 px above and below.
    assert.match(cascadeCss, /@media \(min-width: 512px\) and \(max-height: 499px\) \{[^@]*\.cal-flow-box \{ height: calc\(100% - 16px\); \}/);
    // The shell fills the fixed height: the content scrolls between the header and the footer, which never move.
    assert.match(cascadeCss, /\.cal-flow \.wiz \{ flex: 1 1 auto; display: flex; flex-direction: column; min-height: 0;/);
    assert.match(cascadeCss, /\.cal-flow \.wiz-frame, \.cal-flow \.wiz-columns \{ flex: 1 1 auto; min-height: 0; \}/);
  });

  test("the sheet's handle shows only while closing costs nothing; its band above the header is kept either way", () => {
    const sheet = cascadeCss.slice(cascadeCss.indexOf("@media (max-width: 511px) {\n  dialog.cal-flow {"));
    assert.match(sheet, /\.cal-flow-handle \{ display: block; position: absolute; top: var\(--cal-sp-12\);[^}]*visibility: hidden;/, "12 px above it, hidden by default");
    assert.match(sheet, /\.cal-flow-box:has\(> \.cal-flow-body > \[data-closable\]\) > \.cal-flow-handle \{ visibility: visible; \}/);
    assert.match(sheet, /--cal-head-band: var\(--cal-handle-band\);/, "the band, with or without the handle: nothing moves");
    assert.match(cascadeCss, /--cal-sheet-strip: 24px; --cal-handle-band: 20px;/);
    // The header keeps one height (the band is in its text block's height), its content lifted only by the
    // handle's own room while the handle shows.
    assert.match(cascadeCss, /\.wiz \.wiz-head \{[^}]*padding: calc\(var\(--cal-safe-top\) \+ var\(--cal-head-lift, 0px\)\)/, "lifted by the handle only");
    assert.match(cascadeCss, /\.wiz \.wiz-head > \.wiz-head-text \{[^}]*min-height: calc\(var\(--cal-bar-h\) \+ var\(--cal-head-band, 0px\) - var\(--cal-head-lift, 0px\)\);/, "one height either way");
    assert.match(cascadeCss, /\}\s*\.cal-flow-handle \{ display: none; \}/, "no handle outside the tendina");
  });

  test("a row's ⋮ has no border, and the first press outside its open menu only closes it (nothing opens under the finger)", async () => {
    assert.match(cascadeCss, /\.cal-row-actions \.icon-button \{ width: var\(--cal-target\); min-width: var\(--cal-target\); height: var\(--cal-target\); min-height: var\(--cal-target\); padding: 0;\s*border: 0; border-radius: 50%;/);
    const { tab, root } = await mount({ language: "it" });
    const row = (entity) => [...tab().querySelectorAll(".cal-row")].find((item) => item.dataset.entity === entity);
    row("cover.living_one").querySelector('[data-action="cal-tab-menu"]').click(); await tick();
    assert.equal(row("cover.living_one").querySelector(".cal-menu").hidden, false);
    // A press on another row's name: the menu closes, the click that follows is swallowed.
    const other = row("cover.kitchen_two").querySelector(".cal-row-name");
    other.dispatchEvent(new dom.window.Event("pointerdown", { bubbles: true, composed: true }));
    other.click(); await tick();
    assert.equal(row("cover.living_one").querySelector(".cal-menu").hidden, true, "closed");
    assert.equal(tab().querySelector(".cal-page, dialog.cal-flow"), null, "the other cover's details did not open");
    assert.equal(root.querySelector("#dialog-host dialog"), null);
    // The next press is a press again.
    row("cover.kitchen_two").querySelector(".cal-row-name").click(); await tick();
    assert.ok(tab().querySelector(".cal-page, .cal-row.is-expanded, dialog.cal-flow"), "now it opens");
  });

  test("the footer's ⋮ menu takes the first press outside it for itself too", async () => {
    const { panel, wiz, startFor, code } = await mount({ previews: false });
    await startFor("cover.living_one");
    const wizard = panel._calibrationTab._wizard;
    Object.assign(wizard._local, { stage: "saved", saved: { target: "profile_new", name: "living_room_shutter_1" } });
    wizard._render(); await tick();
    wiz().querySelector(".wiz-overflow-toggle").click(); await tick();
    const close = wiz().querySelector('.wiz-footer-main [data-wiz="close"]');
    close.dispatchEvent(new dom.window.Event("pointerdown", { bubbles: true, composed: true }));
    close.click(); await tick();
    assert.equal(wiz()?.querySelector(".wiz-overflow-menu").hidden, true, "the menu closed");
    assert.equal(code(), "A-34", "and «Torna all'elenco» was not pressed");
  });

});

describe("headers, focus and the scrim", async () => {
  const html = (markup) => { const host = dom.window.document.createElement("div"); host.innerHTML = markup; dom.window.document.body.append(host); return host; };
  const row = (tab, id) => [...tab().querySelectorAll(".cal-row")].find((item) => item.dataset.entity === id);
  /** A press on an overlay's scrim: the press starts and ends on the dialog element itself. */
  const scrim = (dialog) => { dialog.dispatchEvent(new dom.window.MouseEvent("pointerdown", { bubbles: true })); dialog.dispatchEvent(new dom.window.MouseEvent("click", { bubbles: true })); };

  test("every header keeps one height with or without the handle; its content is centred under the handle, or in the whole header", () => {
    assert.match(cascadeCss, /--cal-handle-band: 20px; --cal-handle-room: 16px;/, "the handle's own room: 12 px above it and its 4 px");
    // The page, the flows' bar and the sheets: lifted only by the handle's room, the band kept in their height.
    assert.match(rule(cascadeCss, ".cal-page-head"), /min-height: calc\(var\(--cal-bar-h\) \+ var\(--cal-safe-top\) \+ var\(--cal-head-band, 0px\)\);\s*padding: calc\(var\(--cal-safe-top\) \+ var\(--cal-head-lift, 0px\)\)/);
    assert.match(rule(cascadeCss, ".wiz .wiz-head"), /padding: calc\(var\(--cal-safe-top\) \+ var\(--cal-head-lift, 0px\)\)/);
    assert.match(rule(cascadeCss, ".wiz .wiz-head > .wiz-head-text"), /min-height: calc\(var\(--cal-bar-h\) \+ var\(--cal-head-band, 0px\) - var\(--cal-head-lift, 0px\)\); display: flex; flex-direction: column;\s*justify-content: center;/);
    assert.match(rule(cascadeCss, "dialog.cal-dialog.cal-sheet .dialog-head"), /min-height: calc\(var\(--cal-bar-h\) \+ var\(--cal-head-band, 0px\)\); padding: var\(--cal-head-lift, 0px\) /);
    // The lift belongs to the tendina, and only while its handle shows.
    const tendina = cascadeCss.slice(cascadeCss.indexOf("@media (max-width: 511px) {\n  dialog.cal-flow {"));
    assert.match(tendina, /\.cal-flow-box:has\(> \.cal-flow-body > \[data-closable\]\) \{ --cal-head-lift: var\(--cal-handle-room\); \}/);
    assert.match(cascadeCss, /dialog\.cal-dialog\.cal-sheet\[data-closable\] \{ --cal-head-lift: var\(--cal-handle-room\); \}/);
    assert.doesNotMatch(cascadeCss.replace(/@media \(max-width: 511px\) \{[\s\S]*?\n\}/g, ""), /--cal-head-lift: var/, "never set outside the tendina");
  });

  test("an overlay opens with nothing looking focused: the box takes the focus (tabindex −1, autofocus, no ring); the wizard rests on its title", async () => {
    const { tab, root, startFor, wiz } = await mount({ language: "it" });
    row(tab, "cover.kitchen_two").querySelector(".cal-row-toggle").click(); await tick();
    const box = tab().querySelector("dialog.cal-page-dialog .cal-flow-box");
    assert.deepEqual([box.getAttribute("tabindex"), box.hasAttribute("autofocus")], ["-1", true]);
    assert.equal(root.activeElement, box, "not the ✕");
    assert.match(cascadeCss, /\.cal-flow-box:focus \{ outline: none; \}/, "no ring on the box");
    tab().querySelector(".cal-page-close").click(); await tick();
    await startFor("cover.living_one");
    const flowBox = tab().querySelector("dialog.cal-flow:not(.cal-page-dialog) .cal-flow-box");
    assert.deepEqual([flowBox.getAttribute("tabindex"), flowBox.hasAttribute("autofocus")], ["-1", true]);
    await tick();
    assert.equal(root.activeElement, wiz().querySelector(".wiz-title"), "a step's title (a heading, no ring), never a button");
    assert.equal(winner(wiz().querySelector(".wiz-title"), "outline"), "none");
  });

  test("an overlay lets the focus go before it opens (nothing to give back, so nothing scrolls) and the row menu never scrolls the page", async () => {
    const { tab, root, panel } = await mount({ language: "it" });
    const options = [];
    const focus = dom.window.HTMLElement.prototype.focus;
    dom.window.HTMLElement.prototype.focus = function (option) { options.push([this, option]); return focus.call(this, option); };
    try {
      // A render left the focus on a cover tab (Safari scrolled the page back to it when a dialog closed).
      const coverTab = root.querySelector('[data-action="cover-view"][data-id="calibration"]');
      focus.call(coverTab);
      assert.equal(root.activeElement, coverTab);
      row(tab, "cover.kitchen_two").querySelector(".cal-row-toggle").click(); await tick();
      assert.notEqual(root.activeElement, coverTab, "let go before the dialog opened");
      tab().querySelector(".cal-page-close").click(); await tick();
      options.length = 0;
      row(tab, "cover.kitchen_two").querySelector('[data-action="cal-tab-menu"]').click(); await tick();
      row(tab, "cover.kitchen_two").dispatchEvent(new dom.window.KeyboardEvent("keydown", { key: "ArrowDown", bubbles: true }));
      row(tab, "cover.kitchen_two").dispatchEvent(new dom.window.KeyboardEvent("keydown", { key: "Escape", bubbles: true }));
      assert.ok(options.length >= 3, "the menu's focus moves");
      assert.ok(options.every(([, option]) => option?.preventScroll === true), "never scrolling the page");
    } finally { dom.window.HTMLElement.prototype.focus = focus; }
    void panel;
  });

  test("the footer's ⋮ has the light fill of the row's ⋮ under the pointer, pressed and from the keyboard", () => {
    const css = cascadeCss;
    assert.match(css, /@media \(hover: hover\) \{ \.wiz-overflow > \.wiz-overflow-toggle:hover \{ background: color-mix\(in srgb, var\(--primary-text-color, #182b37\) 6%, transparent\); color: var\(--panel-text-2\); \} \}/);
    const host = html('<div class="wiz"><div class="wiz-actions"><div class="wiz-overflow"><button class="icon-button wiz-overflow-toggle" aria-expanded="false"></button></div></div></div>');
    const toggle = host.querySelector("button");
    assert.equal(winner(toggle, "background"), "transparent", "at rest");
    assert.match(css, /\.wiz-overflow > \.wiz-overflow-toggle:is\(:active, :focus-visible\):not\(\[aria-expanded="true"\]\) \{ background: color-mix\(in srgb, var\(--primary-text-color, #182b37\) 8%, transparent\); \}/);
    host.remove();
  });

  test("one predicate for the handle, the drag and the scrim: the flow closes on its scrim before a session, never during one (a nudge instead)", async () => {
    const { tab, wiz, startFor, press, code, backend } = await mount({ language: "it" });
    // The details: closing costs nothing.
    row(tab, "cover.kitchen_two").querySelector(".cal-row-toggle").click(); await tick();
    const page = tab().querySelector("dialog.cal-page-dialog");
    page.querySelector(".cal-flow-box").dispatchEvent(new dom.window.MouseEvent("pointerdown", { bubbles: true }));
    page.dispatchEvent(new dom.window.MouseEvent("click", { bubbles: true }));
    assert.ok(page.open && page.isConnected, "a press begun inside the box is not a press on the scrim");
    scrim(page); await tick();
    assert.equal(tab().querySelector("dialog.cal-page-dialog"), null, "the details close on their scrim");
    // The wizard before any session (A-03): closable, the scrim closes it.
    await startFor("cover.living_one");
    assert.equal(code(), "A-03");
    assert.ok(wiz().hasAttribute("data-closable"));
    scrim(wiz().closest("dialog")); await tick(6);
    assert.equal(wiz(), null, "closed, back to the list");
    // During a session (A-04): the same predicate says no; the scrim only nudges the box.
    await startFor("cover.living_one");
    await press("begin"); await tick();
    assert.equal(code(), "A-04");
    assert.equal(wiz().hasAttribute("data-closable"), false);
    const dialog = wiz().closest("dialog");
    scrim(dialog); await tick();
    assert.ok(wiz(), "still open: a step is never lost by a press outside");
    assert.ok(dialog.querySelector(".cal-flow-box").classList.contains("is-nudged"), "a short nudge says so");
    assert.equal(wiz().querySelector(".wiz-dialog:not([hidden])"), null, "no confirmation either (Escape keeps the ✕'s)");
    assert.ok(backend.calls.every((message) => message.action !== "cancel"), "nothing cancelled");
    assert.match(cascadeCss, /@media \(prefers-reduced-motion: reduce\) \{ :is\(\.cal-flow-box, dialog\.cal-dialog\)\.is-nudged \{ animation: none; \} \}/);
  });

});

describe("one frame at a time", async () => {
  const layout = await import("../../custom_components/myhome/frontend/panel/panel-calibration-layout.js");
  const row = (tab, id) => [...tab().querySelectorAll(".cal-row")].find((item) => item.dataset.entity === id);
  /** Every <dialog> open in the panel (its shadow root: the tab's frame, the dialog host). */
  const openDialogs = (panel) => [...panel.shadowRoot.querySelectorAll("dialog")].filter((dialog) => dialog.open);
  const frame = (tab) => tab().querySelector("dialog.cal-flow");
  const details = (tab) => frame(tab)?.querySelector(".cal-flow-body > .cal-page:not(.cal-assign-page)") || null;
  const sheet = (tab) => frame(tab)?.querySelector(".cal-flow-body > .cal-assign-page") || null;
  const tap = () => dom.window.dispatchEvent(new dom.window.Event("pointerdown"));
  /** A key pressed where the focus is (the event reaches the window from there). */
  const key = (target, name = "Tab") => target.dispatchEvent(new dom.window.KeyboardEvent("keydown", { key: name, bubbles: true, composed: true }));
  /** Home Assistant accepts one cover's assignment. */
  const accepting = (backend) => { backend.hass.callWS = ((original) => async (message) => message.type === "myhome/cover_profiles/write" ? { revision: 8 } : original(message))(backend.hass.callWS); };
  /**
   * Walk path A from the first close to A-34 (and an interrupted run for «Annulla la misurazione»), checking every screen:
   * the order of an illustrated step's parts and where the red actions sit in the bar.
   */
  async function walk(context, check) {
    const { backend, press, type, startFor } = context;
    await startFor("cover.living_one"); check();
    await press("begin"); check(); await move(backend); check(); await press("endpoint"); check(); backend.stopped(); await tick(); check();
    await press("ack:lift"); check(); await press("next"); check(); await move(backend, { seconds: 4.4 }); check(); await press("lift"); check(); backend.stopped(); await tick(); check();
    await type("3"); check(); await move(backend); check(); await press("endpoint"); backend.stopped(); await tick(); check();
    await press("next"); check(); await move(backend, { seconds: 22.2 }); check(); await press("endpoint"); backend.stopped(); await tick(); check();
    await type("198"); check(); await press("ack:ascent"); check();
    await press("next"); check(); await move(backend, { seconds: 21.7 }); check(); await press("endpoint"); backend.stopped(); await tick(); check();
    await press("ack:descent"); check(); await press("ack:readings"); check(); await move(backend, { auto: true, stop: true }); check();
    await type("97"); check(); await move(backend, { seconds: 24 }); check(); await press("endpoint"); backend.stopped(); await tick(); check();
    await move(backend, { auto: true, stop: true }); check(); await type("101"); check(); await press("check-start"); check();
    await press("save"); check();
  }

  test("«Ricalibra» and «Perfeziona» replace the details with the flow in the same frame, one history entry; A-34's «Apri la scheda» the other way round", async () => {
    const context = await mount({ language: "it" });
    const { tab, panel, wiz } = context;
    row(tab, "cover.kitchen_two").querySelector(".cal-row-toggle").click(); await tick();
    const box = frame(tab), entry = window.history.state?.myhomeCalibration;
    // Ricalibra from the card through the procedure «Controlla il posizionamento».
    await recalibrateFromCard(tab);
    assert.ok(wiz(), "the wizard");
    assert.equal(frame(tab), box, "in the details' frame");
    assert.equal(box.querySelector(".cal-flow-box").dataset.kind, "flow", "a flow's fixed height");
    assert.equal(box.classList.contains("cal-page-dialog"), false);
    assert.deepEqual(openDialogs(panel), [box]);
    assert.equal(window.history.state?.myhomeCalibration, entry, "the same entry");
    wiz().querySelector(".wiz-head .wiz-x").click(); await tick();
    assert.equal(frame(tab), null, "its ✕: the list");
    // Perfeziona on a cover without a profile (a follower has none yet).
    context.backend.overview.covers.find((cover) => cover.entity_id === "cover.living_three").profile_id = null;
    context.backend.overview.profiles[0].assigned_to = ["cover.kitchen_two"];
    context.backend.publishOverview(); await tick(8);
    row(tab, "cover.living_three").querySelector(".cal-row-toggle").click(); await tick();
    const again = frame(tab);
    details(tab).querySelector('[data-action="cal-tab-refine"]').click(); await tick();
    assert.equal(frame(tab), again, "«Perfeziona» too");
    assert.ok(wiz());
  });

  test("nothing of ours opens a dialog while another is open (the details, «Cambia profilo», a flow, the maintainer's view of a session)", async () => {
    // A session the wizard does not lead (the maintainer's view): from the details, «Segui sessione» replaces them.
    const session = { entry_id: "one", entity_id: "cover.kitchen_two", session_id: "m", mode: "automatic", phase: "paused", reason: "owner_absent", sequence: 2,
      values: {}, recoverable: true, attached: false, owner_present: false, direction: "opening" };
    const { tab, panel } = await mount({ language: "it", session, owner: false });
    await settle();
    row(tab, "cover.kitchen_two").querySelector(".cal-row-toggle").click(); await tick();
    assert.deepEqual(openDialogs(panel), [frame(tab)]);
    details(tab).querySelector('[data-action="cal-tab-session"]').click(); await tick(6);
    const open = openDialogs(panel);
    assert.equal(open.length, 1, "one at a time");
    assert.equal(frame(tab), null, "the details gave way");
    assert.ok(open[0].matches("#dialog-host dialog.cal-dialog"), "the maintainer's view in its own dialog");
    // The source: every `showModal` of the tab comes from the one frame or the host's dialog, which closes the other first.
    const { readFileSync } = await import("node:fs");
    const source = readFileSync(new URL("../../custom_components/myhome/frontend/panel/panel-calibration-tab.js", import.meta.url), "utf8");
    assert.equal((source.match(/showModal\(\)/g) || []).length, 2, "two places open a dialog: the frame (`_overlayDialog`) and the host's (`_openDialog`)");
    assert.doesNotMatch(source, /_openFlowDialog\(\)[^}]*_closePageDialog/, "the flow never closes the details to open its own dialog");
  });

  test("a closed overlay gives the focus back to what opened it, without scrolling; its ring only when the last input was the keyboard", async () => {
    const { tab, root } = await mount({ language: "it" });
    const options = [];
    const focus = dom.window.HTMLElement.prototype.focus;
    dom.window.HTMLElement.prototype.focus = function (option) { options.push(option); return focus.call(this, option); };
    try {
      const toggle = () => row(tab, "cover.kitchen_two").querySelector(".cal-row-toggle");
      tap();
      focus.call(toggle()); toggle().click(); await tick();
      details(tab).querySelector(".cal-page-close").click(); await tick();
      assert.equal(root.activeElement, toggle(), "after a tap: back on the row");
      assert.ok(toggle().hasAttribute("data-quiet-focus"), "no ring");
      assert.ok(options.every((option) => option?.preventScroll === true), "never scrolling the page");
      key(toggle());
      assert.equal(toggle().hasAttribute("data-quiet-focus"), false, "a key brings the ring back where the focus is");
      toggle().click(); await tick();
      tab().dispatchEvent(new dom.window.KeyboardEvent("keydown", { key: "Escape", bubbles: true, composed: true })); await tick();
      assert.equal(root.activeElement, toggle(), "after a key: back on the row");
      assert.equal(toggle().hasAttribute("data-quiet-focus"), false, "with its ring");
      // Losing the focus clears the mark.
      tap(); toggle().click(); await tick(); details(tab).querySelector(".cal-page-close").click(); await tick();
      assert.ok(toggle().hasAttribute("data-quiet-focus"));
      toggle().blur();
      assert.equal(toggle().hasAttribute("data-quiet-focus"), false);
    } finally { dom.window.HTMLElement.prototype.focus = focus; }
    assert.match(cascadeCss, /\[data-quiet-focus\]:is\(:focus, :focus-visible\) \{ outline: none; box-shadow: none; \}/);
    assert.equal(layout.QUIET_FOCUS, "data-quiet-focus");
  });

  test("A-34's «Apri la scheda» puts the cover's details in the flow's place, in the same frame (no fade, no second dialog)", async () => {
    const context = await mount({ language: "it" });
    await walk(context, () => {});
    assert.equal(context.code(), "A-34");
    const box = frame(context.tab);
    context.wiz().querySelector('[data-wiz="open-cover"]').click(); await tick(8);
    assert.equal(context.wiz(), null, "the wizard is closed");
    assert.equal(frame(context.tab), box, "the same frame");
    assert.equal(details(context.tab)?.dataset.entity, "cover.living_one", "its details in it");
    assert.equal(box.querySelector(".cal-flow-box").dataset.kind, "page", "as high as their content");
    assert.deepEqual(openDialogs(context.panel), [box]);
    details(context.tab).querySelector(".cal-page-close").click(); await tick();
    assert.equal(frame(context.tab), null, "their ✕: the list");
  });

});

describe("closing the wizard", async () => {
  /** Living room shutter 1 (never calibrated): A-02 with the basic calibration, closed at a known 0 (no positioning first). */
  async function toBasic(context, { slats = "yes" } = {}) {
    const { backend, startFor, code, wiz } = context;
    backend.hass.states["cover.living_one"] = { state: "closed", attributes: { current_position: 0 } };
    await startFor("cover.living_one", "base");
    assert.equal(code(), "C-35");
    const radio = wiz().querySelector(`input[name="slats"][value="${slats}"]`);
    radio.checked = true; radio.dispatchEvent(new Event("change", { bubbles: true })); await tick();
  }

  test("closing costs nothing on A-02 and C-35 (✕, scrim, handle); during the runs ✕ asks first", async () => {
    const context = await mount();
    const { backend, wiz, press } = context;
    await toBasic(context);
    assert.equal(wiz().hasAttribute("data-closable"), true, "C-35: before any session");
    await press("begin");
    assert.equal(wiz().hasAttribute("data-closable"), false, "C-11: a session is open");
    await press("run:open");
    await move(backend, { seconds: 3 });
    assert.equal(wiz().hasAttribute("data-closable"), false);
    wiz().querySelector('.wiz-head [data-wiz="exit"]').click(); await tick();
    assert.equal(wiz().querySelector(".wiz-dialog:not([hidden])").getAttribute("role"), "alertdialog", "«Esci senza salvare?»");
  });
});

describe("what opens comes into view", async () => {
  const tick_ = async (wiz, name, value) => {
    const radio = wiz().querySelector(`input[name="${name}"][value="${value}"]`);
    radio.checked = true; radio.dispatchEvent(new Event("change", { bubbles: true })); await tick();
  };
  const { revealOpened } = await import("../../custom_components/myhome/frontend/panel/panel-calibration-layout.js");
  /** A details' `toggle` is a task of its own (a timer in jsdom). */
  const toggled = async () => { await new Promise((resolve) => setTimeout(resolve, 5)); await tick(); };
  /**
   * jsdom lays nothing out: the boxes are given here. `below` names what sits under the visible part of its scroller (a
   * box from 900 to 1000 px against 0-800); everything else is in view. Every `scrollIntoView` is recorded.
   */
  function layoutStub(below) {
    const proto = dom.window.Element.prototype, saved = [proto.getBoundingClientRect, proto.scrollIntoView, globalThis.matchMedia];
    const calls = [];
    proto.getBoundingClientRect = function () { const out = typeof below === "function" ? below(this) : this.matches?.(below); return out ? { top: 900, bottom: 1000, height: 100 } : { top: 10, bottom: 300, height: 290 }; };
    proto.scrollIntoView = function (options) { calls.push([this, options]); };
    return { calls, motion(reduced) { globalThis.matchMedia = (query) => ({ matches: reduced && query.includes("reduced-motion") }); },
      restore() { [proto.getBoundingClientRect, proto.scrollIntoView, globalThis.matchMedia] = saved; } };
  }

  test("`revealOpened` scrolls only what is out of view, to its nearest edge, smoothly unless motion is reduced", () => {
    const host = document.createElement("div");
    host.innerHTML = '<div class="box" style="overflow-y: auto"><p class="seen">a</p><p class="hidden-below">b</p></div>';
    document.body.append(host);
    const stub = layoutStub((element) => element.classList?.contains("hidden-below"));
    try {
      assert.equal(revealOpened(host.querySelector(".seen")), false, "already in view: nothing moves");
      assert.deepEqual(stub.calls, []);
      assert.equal(revealOpened(host.querySelector(".hidden-below")), true);
      assert.deepEqual(stub.calls.map(([element, options]) => [element.className, options]), [["hidden-below", { block: "nearest", behavior: "smooth" }]]);
      stub.motion(true);
      revealOpened(host.querySelector(".hidden-below"));
      assert.deepEqual(stub.calls.at(-1)[1], { block: "nearest", behavior: "auto" }, "prefers-reduced-motion: no animation");
      assert.equal(revealOpened(null), false);
    } finally { stub.restore(); host.remove(); }
  });

  test("C-35's «Come capirlo» opened at the bottom of the scroll area is brought into view; opened again while in view, nothing moves; a redraw that keeps it open scrolls nothing", async () => {
    const context = await mount({ language: "it" });
    const { wiz, code, startFor } = context;
    await startFor("cover.living_one", "base");
    assert.equal(code(), "C-35");
    const more = () => wiz().querySelector("details.wiz-help");
    assert.ok(more(), "the slats question's disclosure");
    const stub = layoutStub("details.wiz-help");
    try {
      more().querySelector("summary").click(); await toggled();
      assert.equal(more().open, true);
      assert.deepEqual(stub.calls.map(([element, options]) => [element.matches("details.wiz-help"), options.block]), [[true, "nearest"]]);
      more().querySelector("summary").click(); await toggled();
      assert.equal(more().open, false, "closing scrolls nothing");
      assert.equal(stub.calls.length, 1);
      stub.restore();
      const seen = layoutStub(() => false);
      try {
        more().querySelector("summary").click(); await toggled();
        assert.deepEqual(seen.calls, [], "already in view: nothing moves");
        const radio = wiz().querySelector('input[name="slats"][value="no"]');
        radio.checked = true; radio.dispatchEvent(new Event("change", { bubbles: true })); await toggled();
        assert.deepEqual(seen.calls, [], "a redraw is not an opening");
      } finally { seen.restore(); }
    } finally { stub.restore(); }
  });

  test("«Altre opzioni» in the phone's bar and the steps sheet bring what they reveal into view", async () => {
    const original = globalThis.matchMedia;
    globalThis.matchMedia = (query) => ({ matches: /max-width/.test(query) && !/max-height/.test(query) });
    const context = await mount();
    const { wiz, code, press } = context;
    context.backend.hass.states["cover.living_one"] = { state: "closed", attributes: { current_position: 0 } };
    await context.startFor("cover.living_one", "base");
    await tick_(wiz, "slats", "no");
    await press("begin");
    await press("run:open"); await move(context.backend, { seconds: 14.2 }); await press("endpoint");
    await press("ack:ascent");
    await press("run:close"); await move(context.backend, { seconds: 14 }); await press("endpoint");
    await press("ack:descent");
    assert.equal(code(), "A-21b");
    const stub = layoutStub((element) => element.classList?.contains("wiz-more-list") || element.matches?.('.wiz-sheet-steps [aria-current="step"]'));
    try {
      // The steps sheet: the step in progress («Riepilogo», the last) in view inside it.
      wiz().querySelector('.wiz-head [data-wiz="steps"]').click(); await tick();
      assert.equal(wiz().querySelector(".wiz-sheet").hidden, false);
      assert.deepEqual(stub.calls.map(([element]) => element.querySelector(".wiz-step-label").textContent), ["Summary"]);
      assert.ok(stub.calls[0][0].closest(".wiz-sheet-steps"));
      await press("steps-close");
      stub.calls.length = 0;
      await press("save");
      assert.equal(code(), "A-34");
      const toggle = wiz().querySelector('.wiz-actions [data-wiz="more"]');
      assert.ok(toggle, "the phone's «Altre opzioni»");
      toggle.click(); await tick();
      assert.deepEqual(stub.calls.map(([element]) => element.className), ["wiz-more-list"], "the other ways in view");
    } finally { stub.restore(); globalThis.matchMedia = original; }
  });

  test("the tab's «Come funziona» opened is brought into view", async () => {
    const context = await mount();
    const stub = layoutStub("#cal-tab-how-text");
    try {
      context.tab().querySelector('[data-action="cal-tab-how"]').click(); await tick();
      assert.ok(context.tab().querySelector("#cal-tab-how-text"));
      assert.deepEqual(stub.calls.map(([element, options]) => [element.id, options]), [["cal-tab-how-text", { block: "nearest", behavior: "smooth" }]]);
    } finally { stub.restore(); }
  });
});

describe("the phone's keyboard", async () => {
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
  async function details(context, entity, view = "overview") {
    row(context.tab, entity).querySelector('[data-action="cal-tab-toggle"]').click();
    await tick(6);
    // The details open on «Panoramica» (the travel, the slats, the profile); the table and its tail are on «Dettagli».
    if (view !== "overview") await pageTab(context.tab, view);
    return context.tab().querySelector(".cal-flow .cal-page");
  }
  const travelRow = (page) => page.querySelector(".cal-pairs-travel");

  test("with the phone's keyboard up the overlays fit the visible viewport and the field being typed in is brought into view; nothing changes without it", async () => {
    const viewport = new dom.window.EventTarget();
    Object.assign(viewport, { height: dom.window.innerHeight, offsetTop: 0 });
    Object.defineProperty(dom.window, "visualViewport", { configurable: true, value: viewport });
    const proto = dom.window.HTMLElement.prototype, saved = proto.scrollIntoView, calls = [];
    proto.scrollIntoView = function (options) { calls.push([this, options]); };
    try {
      const context = await mount({ overview: sourceOverview(), language: "it" });
      const { tab } = context;
      const page = await details(context, "cover.living_three");
      travelRow(page).querySelector('[data-action="cal-tab-travel-edit"]').click(); await tick();
      const field = tab().querySelector('.cal-inline-field input[name="travel_cm"]');
      field.focus();
      viewport.height = 380; viewport.offsetTop = 12;
      viewport.dispatchEvent(new dom.window.Event("resize")); await new Promise((resolve) => setTimeout(resolve, 30));
      assert.equal(tab().style.getPropertyValue("--cal-vv-h"), "380px");
      assert.equal(tab().style.getPropertyValue("--cal-vv-top"), "12px");
      assert.ok(tab().hasAttribute("data-keyboard"));
      // The field's whole row (its ✓ and ✕ too).
      for (let wait = 0; wait < 20 && !calls.some(([element]) => element === field.closest(".cal-pairs > div")); wait++) await new Promise((resolve) => setTimeout(resolve, 20));
      const options = calls.filter(([element]) => element === field.closest(".cal-pairs > div")).map(([, value]) => value);
      assert.ok(options.length >= 1 && options.every((value) => value.block === "nearest"), "the field in view, in its own container");
      viewport.height = dom.window.innerHeight; viewport.offsetTop = 0;
      viewport.dispatchEvent(new dom.window.Event("resize")); await tick();
      assert.deepEqual([tab().style.getPropertyValue("--cal-vv-h"), tab().hasAttribute("data-keyboard")], ["", false], "the keyboard gone: as before");
      const css = readFileSync(new URL("../../custom_components/myhome/frontend/panel/panel-calibration.css", import.meta.url), "utf8");
      assert.match(css, /\[data-keyboard\] dialog:is\(\.cal-flow, \.cal-dialog\)\[open\] \{\s*top: var\(--cal-vv-top, 0px\); bottom: auto; height: var\(--cal-vv-h, 100%\); max-height: var\(--cal-vv-h, 100%\); \}/);
    } finally {
      proto.scrollIntoView = saved;
      delete dom.window.visualViewport;
    }
  });
});

describe("the flow's dialog clips", async () => {
  const PANEL = new URL("../../custom_components/myhome/frontend/panel/", import.meta.url);
  const read = (name) => readFileSync(new URL(name, PANEL), "utf8");

  test("the flow's dialog clips (it cannot be scrolled): the sheet rises from the bottom edge, whatever the panel's scroll", () => {
    const css = read("panel-calibration.css");
    assert.match(css, /dialog\.cal-flow \{[^}]*overflow: hidden; overflow: clip;/);
  });
});

describe("the overlay's height slides", async () => {
  const { SLIDE_MS, SLIDE_EASE } = await import("../../custom_components/myhome/frontend/panel/panel-calibration-layout.js");
  /** A window `width` px wide and 800 high for `matchMedia`; motion reduced unless `motion`. */
  const windowOf = (width, { motion = false } = {}) => (query) => ({ matches: query.split(",").some((part) => [...part.matchAll(/\(\s*([a-z-]+)\s*:\s*([a-z0-9-]+)\s*\)/g)].every(([, name, value]) =>
    name === "max-width" ? width <= parseFloat(value) : name === "min-width" ? width >= parseFloat(value) : name === "max-height" ? 800 <= parseFloat(value)
      : name === "min-height" ? 800 >= parseFloat(value) : name === "orientation" ? value === "landscape" : name === "prefers-reduced-motion" ? !motion : false)), addEventListener() {}, removeEventListener() {} });
  const desktop = (options) => { globalThis.matchMedia = windowOf(1280, options); };
  const MEASURED_AT = "2026-11-01T16:09:00+00:00";
  const measured = { source: "guided", recorded_at: MEASURED_AT, origin_entity_id: null, origin_name: null, inherited: false };
  const timing = (value, origin = "profile", scaled = false) => ({ value, origin, provenance: measured, scaled });
  /** «tall» measured on Living room shutter 1 (the source), followed by Kitchen shutter 2 (130 cm, slats) and Living room shutter 3. */
  function family() {
    const data = sampleOverview();
    const profile = data.profiles[0];
    profile.reference_travel_cm = 110;
    profile.assigned_to = ["cover.living_one", "cover.kitchen_two", "cover.living_three"];
    profile.provenance.opening.recorded_at = MEASURED_AT;
    const [one, two, three] = data.covers;
    Object.assign(one, { profile_id: "tall", travel_cm: 110, precision: "basic", slats_state: "yes_measured", effective: { opening: timing(21.8), closing: timing(20.4), slat_time_s: timing(1.4) } });
    Object.assign(two, { profile_id: "tall", travel_cm: 130, precision: "basic", slats_state: "yes_profile", effective: { opening: timing(24.6, "profile", true), closing: timing(23.1, "profile", true), slat_time_s: timing(1.4) } });
    Object.assign(three, { profile_id: "tall", precision: "basic", slats_state: "no_profile", effective: { opening: timing(21.8), closing: timing(20.4) } });
    delete three.travel_cm;
    return data;
  }
  const row = (context, entity) => context.tab().querySelector(`.cal-row[data-entity="${entity}"]`);
  const card = (context) => context.tab().querySelector(".cal-flow .cal-page:not(.cal-assign-page)");
  const body = (context) => card(context).querySelector(".cal-page-body");
  async function open(context, entity) {
    row(context, entity).querySelector('[data-action="cal-tab-toggle"]').click();
    await tick(6);
    return card(context);
  }
  const click = async (context, selector) => {
    const button = card(context).querySelector(selector);
    assert.ok(button, selector);
    button.click();
    await tick(6);
  };
  /** jsdom lays nothing out: the overlay's box is as high as `heights` says for its view; its slides are recorded. */
  function heightStub(heights) {
    const proto = dom.window.Element.prototype, saved = [proto.getBoundingClientRect, proto.animate];
    const slides = [];
    proto.getBoundingClientRect = function () {
      if (!this.classList?.contains("cal-flow-box")) return { top: 0, bottom: 0, height: 0, width: 0 };
      const view = this.querySelector(".cal-page-body"), name = view?.dataset.tab === "check" ? `check-${view.querySelector('[aria-selected="true"]')?.dataset.id ?? view.id}` : view?.dataset.tab;
      return { top: 0, bottom: heights[name] ?? 400, height: heights[name] ?? 400, width: 480 };
    };
    proto.animate = function (keyframes, options) {
      const animation = { keyframes, options, cancelled: false, cancel() { this.cancelled = true; this.oncancel?.(); }, onfinish: null, oncancel: null };
      if (this.classList?.contains("cal-flow-box")) slides.push(animation);
      return animation;
    };
    return { slides, restore() { [proto.getBoundingClientRect, proto.animate] = saved; } };
  }

  test("a new tab, the question and its outcome slide the overlay from its height to the new one (400 ms, ease-in-out); nothing when the height stays, nor on the 15 s reads", async () => {
    globalThis.matchMedia = windowOf(390, { motion: true });
    const stub = heightStub({ overview: 420, details: 760, "check-cal-page-check-1": 520, "check-cal-page-check-2": 520 });
    try {
      const context = await mount({ language: "it", overview: family() });
      await open(context, "cover.living_one");
      assert.equal(stub.slides.length, 0, "opening the card is its own entrance");
      await pageTab(context.tab, "details");
      assert.deepEqual(stub.slides.map((slide) => [slide.keyframes.height, slide.options.duration, slide.options.easing]), [[["420px", "760px"], SLIDE_MS, SLIDE_EASE]]);
      assert.deepEqual([SLIDE_MS, SLIDE_EASE], [400, "ease-in-out"]);
      for (let i = 0; i < 2; i++) { context.panel._calibrationTab.refresh(); await tick(8); }
      assert.equal(stub.slides.length, 1, "the reads redraw the same view: nothing moves");
      await pageTab(context.tab, "overview");
      assert.deepEqual(stub.slides.at(-1).keyframes.height, ["760px", "420px"]);
      await click(context, '[data-action="cal-tab-check-start"]');
      assert.deepEqual(stub.slides.at(-1).keyframes.height, ["420px", "520px"], "to the question");
      await click(context, '[data-action="cal-tab-check-answer"][data-id="full"]');
      assert.equal(stub.slides.length, 3, "question → outcome of the same height: no slide");
      await click(context, ".cal-page-back");
      assert.equal(stub.slides.length, 3);
    } finally { stub.restore(); }
  });

  test("on a desktop «Panoramica» → outcome slides (another answer beside it does not); a slide still running is taken up where it is; with reduced motion nothing slides", async () => {
    desktop({ motion: true });
    const stub = heightStub({ overview: 480, "check-full": 560, "check-middle": 500, "check-unsure": 560 });
    try {
      const context = await mount({ language: "it", overview: family() });
      await open(context, "cover.living_one");
      await click(context, '.cal-pos-help [data-id="full"]');
      assert.deepEqual(stub.slides.map((slide) => slide.keyframes.height), [["480px", "560px"]]);
      await click(context, '.cal-check-answers [data-id="middle"]');
      await click(context, '.cal-check-answers [data-id="unsure"]');
      assert.equal(stub.slides.length, 1, "the same page: no slide");
      await click(context, ".cal-check-back");
      assert.deepEqual(stub.slides.at(-1).keyframes.height, ["560px", "480px"]);
      assert.equal(stub.slides[0].cancelled, true, "the previous one, still running, gives way");
      assert.equal(stub.slides.length, 2);
      reset();
      desktop();
      const still = await mount({ language: "it", overview: family() });
      await open(still, "cover.living_one");
      await click(still, '.cal-pos-help [data-id="full"]');
      await click(still, ".cal-check-back");
      await pageTab(still.tab, "details");
      assert.equal(body(still).dataset.tab, "details");
      assert.equal(stub.slides.length, 2, "prefers-reduced-motion: no slide");
    } finally { stub.restore(); }
  });
});

describe("the first tap after a menu", async () => {
  /** A tap as a finger or a mouse gives it: a press, then its click. */
  async function tap(element) {
    element.dispatchEvent(new dom.window.Event("pointerdown", { bubbles: true, composed: true }));
    element.click();
    await settle();
  }
  /** Whether a tap reaches this element (a click swallowed by the document never gets to it). */
  async function reaches(element) {
    let reached = false;
    element.addEventListener("click", () => { reached = true; }, { once: true });
    await tap(element);
    return reached;
  }
  /**
   * Kitchen shutter 2 is the source of «tall_two_leaf»: its ⋮ holds Ricalibra, Cambia profilo, Dettagli.
   * Perfeziona is in ⋮ only after a check within the threshold (`checked`), in view otherwise.
   */
  async function world({ checked = false } = {}) {
    const overview = sampleOverview();
    overview.profiles[0].provenance.opening.origin_entity_id = "cover.kitchen_two";
    const source = overview.covers.find((cover) => cover.entity_id === "cover.kitchen_two");
    if (checked) Object.assign(source, { precision: "verified", check_cm: 1, check_measured_cm: 56, check_expected_cm: 55, check_at: "2026-11-04T09:30:00+00:00" });
    const context = await mount({ language: "it", overview });
    const row = (entity = "cover.kitchen_two") => [...context.tab().querySelectorAll(".cal-row")].find((item) => item.dataset.entity === entity);
    return { ...context, row };
  }
  /** ⋮ of the row tapped, then one of its items. */
  async function choose(row, action) {
    await tap(row().querySelector('[data-action="cal-tab-menu"]'));
    assert.equal(row().querySelector(".cal-menu").hidden, false, "the ⋮ menu is open");
    const item = row().querySelector(`.cal-menu [data-action="${action}"]`);
    assert.ok(item && !item.disabled, `${action} in the ⋮ menu`);
    await tap(item);
  }

  for (const [action, label] of [["cal-tab-measure-cover", "Ricalibra"], ["cal-tab-refine", "Perfeziona"]]) {
    test(`⋮ › «${label}», the first tap on «Ho capito, inizia» reaches the wizard`, async () => {
      const { row, wiz, code, panel } = await world({ checked: action === "cal-tab-refine" });
      await choose(row, action);
      const start = code();
      assert.ok(start, "the wizard is open");
      assert.equal(panel._calibrationTab._menuOff, null, "the ⋮'s watch stopped with the choice");
      const begin = wiz().querySelector('.wiz-frame [data-wiz="begin"]');
      assert.ok(begin && !begin.disabled, `«Ho capito, inizia» on ${start}`);
      assert.equal(await reaches(begin), true, "the first tap is not swallowed");
      assert.notEqual(code(), start, "and the wizard moved on");
    });
  }

  for (const [action, close] of [["cal-tab-toggle", "cal-tab-page-close"]]) {
    test(`⋮ › ${action}, the first tap in the window opened reaches it`, async () => {
      const { row, tab, panel } = await world();
      await choose(row, action);
      const button = tab().querySelector(`dialog.cal-flow[open] [data-action="${close}"]`);
      assert.ok(button, `${close} in the window opened`);
      assert.equal(panel._calibrationTab._menuOff, null);
      assert.equal(await reaches(button), true, "the first tap is not swallowed");
    });
  }

  test("a press outside an open ⋮ still only closes it, and the next tap is a tap again", async () => {
    const { row, tab, root } = await world();
    await tap(row().querySelector('[data-action="cal-tab-menu"]'));
    assert.equal(row().querySelector(".cal-menu").hidden, false);
    const other = row("cover.living_one").querySelector(".cal-row-name");
    assert.equal(await reaches(other), false, "the press outside is taken by the menu");
    assert.equal(row().querySelector(".cal-menu").hidden, true, "closed");
    assert.equal(tab().querySelector("dialog.cal-flow[open]"), null, "nothing opened under the finger");
    assert.equal(root.querySelector("#dialog-host dialog"), null);
    assert.equal(await reaches(row("cover.living_one").querySelector(".cal-row-name")), true, "the next tap reaches");
    assert.ok(tab().querySelector("dialog.cal-flow[open] .cal-page"), "and opens the cover's details");
  });

  test("the wizard's footer ⋮, an item chosen, the next tap reaches the wizard", async () => {
    const { panel, wiz, startFor, code } = await mount({ previews: false, language: "it" });
    await startFor("cover.living_one");
    const wizard = panel._calibrationTab._wizard;
    Object.assign(wizard._local, { stage: "saved", saved: { target: "profile_new", name: "living_room_shutter_1" } });
    wizard._render(); await tick();
    assert.equal(code(), "A-34");
    await tap(wiz().querySelector(".wiz-overflow-toggle"));
    await tap(wiz().querySelector('.wiz-overflow-menu [data-wiz="another"]'));
    assert.equal(wizard._offOutside, null, "the footer ⋮'s watch stopped with the choice");
    const next = wiz().querySelector(".wiz-frame button[data-wiz]:not(:disabled)");
    assert.ok(next, `a button on ${code()}`);
    assert.equal(await reaches(next), true, "the first tap is not swallowed");
  });
});

describe("the wheel on the scrim", async () => {
  const PANEL = "../../custom_components/myhome/frontend/panel/";
  const { holdScrimScroll } = await import(`${PANEL}panel-calibration-layout.js`);
  const tabSource = readFileSync(new URL(`${PANEL}panel-calibration-tab.js`, import.meta.url), "utf8");
  /** A window `width` px wide and 800 high for `matchMedia`, motion reduced. */
  const windowOf = (width) => (query) => ({ matches: query.split(",").some((part) => [...part.matchAll(/\(\s*([a-z-]+)\s*:\s*([a-z0-9-]+)\s*\)/g)].every(([, name, value]) =>
    name === "max-width" ? width <= parseFloat(value) : name === "min-width" ? width >= parseFloat(value) : name === "max-height" ? 800 <= parseFloat(value)
      : name === "min-height" ? 800 >= parseFloat(value) : name === "orientation" ? value === "landscape" : name === "prefers-reduced-motion")), addEventListener() {}, removeEventListener() {} });
  const desktop = () => { globalThis.matchMedia = windowOf(1280); };
  const row = (context, entity) => context.tab().querySelector(`.cal-row[data-entity="${entity}"]`);
  const card = (context) => context.tab().querySelector(".cal-flow .cal-page:not(.cal-assign-page)");
  async function open(context, entity) {
    row(context, entity).querySelector('[data-action="cal-tab-toggle"]').click();
    await tick(6);
    return card(context);
  }
  const check = (cover, gap) => Object.assign(cover, { precision: "verified", check_cm: gap, check_expected_cm: 55, check_measured_cm: 55 + gap, check_at: "2026-11-04T09:30:00+00:00" });
  const own = (number) => ({ value: number, origin: "override", provenance: { source: "guided", recorded_at: "2026-09-29T10:00:00+00:00", origin_entity_id: null, origin_name: null, inherited: false }, scaled: false });
  /**
   * Three covers: Kitchen shutter 2 measures «tall_two_leaf» (its source), Living room shutter 3 follows it, Living
   * room shutter 1 is never calibrated (or, `alone`, calibrated with times of its own and no profile).
   */
  function world({ source = "refined", sourceGap = null, alone = null, aloneGap = null, followerGap = null } = {}) {
    const data = sampleOverview();
    data.profiles[0].provenance.opening.origin_entity_id = "cover.kitchen_two";
    const [one, two, three] = data.covers;
    two.precision = source;
    if (sourceGap != null) check(two, sourceGap);
    if (alone) {
      Object.assign(one, { overrides: { opening: 18, closing: 17.2 }, precision: alone, effective: { opening: own(18), closing: own(17.2) } });
      if (aloneGap != null) check(one, aloneGap);
    }
    if (followerGap != null) check(three, followerGap);
    return data;
  }
  const wheel = (target) => { const event = new dom.window.WheelEvent("wheel", { bubbles: true, cancelable: true, deltaY: 120 }); target.dispatchEvent(event); return event.defaultPrevented; };
  const touch = (target) => { const event = new dom.window.Event("touchmove", { bubbles: true, cancelable: true }); target.dispatchEvent(event); return event.defaultPrevented; };
  /**
   * A body lets the wheel through only while it really scrolls (jsdom measures nothing: 0 and 0, and reads no
   * shadow root's stylesheet). jsdom keeps a computed style cached for a node in a shadow tree: an attribute changed on
   * the document's own element drops the cache.
   */
  const overflowing = (element) => {
    element.style.overflowY = "auto";
    Object.defineProperty(element, "scrollHeight", { configurable: true, value: 900 });
    Object.defineProperty(element, "clientHeight", { configurable: true, value: 400 });
    const root = element.ownerDocument.documentElement;
    root.toggleAttribute("data-style-cache"); root.toggleAttribute("data-style-cache");
    return element;
  };

  test("a wheel on the details' scrim (the dialog itself) is held; inside the body it is not; the wizard's frame the same", async () => {
    desktop();
    const context = await mount({ language: "it", overview: world() });
    await open(context, "cover.kitchen_two");
    const dialog = context.tab().querySelector("dialog.cal-flow");
    assert.equal(wheel(dialog), true, "the scrim: the page does not scroll");
    assert.equal(touch(dialog), true, "a touch move too");
    assert.equal(wheel(overflowing(card(context).querySelector(".cal-page-body"))), false, "the body scrolls itself");
    card(context).querySelector(".cal-page-close").click(); await tick(8);
    reset();
    const wizard = await mount({ language: "it", overview: world() });
    wizard.tab().querySelector('[data-action="cal-tab-measure"]').click(); await tick();
    const frame = wizard.tab().querySelector("dialog.cal-flow");
    assert.ok(frame && wizard.wiz(), "the wizard in the frame");
    assert.equal(wheel(frame), true, "the wizard's scrim");
    assert.equal(wheel(overflowing(wizard.wiz().querySelector(".wiz-main") || wizard.wiz())), false, "the wizard's body");
  });

  test("the tab's own sheet (cal-dialog) holds its scrim too; the listeners go when it closes; every scroller contains its scroll", async () => {
    const context = await mount({ language: "it", overview: world() });
    const tab = context.panel._calibrationTab;
    const body = tab._openDialog({ title: "Sheet", icon: "mdi:information-outline", sheet: true });
    const dialog = body.closest("dialog");
    assert.equal(wheel(dialog), true, "the scrim");
    assert.equal(wheel(overflowing(body)), false, "the body");
    tab._closeDialog();
    assert.equal(tab._unwheelDialog, null, "let go");
    // The helper itself: on until stopped, the dialog only (never a child).
    const bare = dom.window.document.createElement("dialog");
    const inner = bare.appendChild(dom.window.document.createElement("div"));
    dom.window.document.body.append(bare);
    const stop = holdScrimScroll(bare);
    assert.deepEqual([wheel(bare), wheel(overflowing(inner))], [true, false]);
    stop();
    assert.equal(wheel(bare), false, "removed");
    for (const selector of [".cal-page-body", "dialog.cal-dialog .cal-dialog-body", ".wiz-main"]) {
      assert.ok(new RegExp(`${selector.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")} \\{[^}]*overscroll-behavior: contain`).test(css), selector);
    }
    assert.match(css, /:is\(\.wiz-sheet-card, \.cal-flow \.wiz \.wiz-steps\) \{ overscroll-behavior: contain; \}/);
    assert.match(tabSource, /holdScrimScroll\(dialog\)/);
  });
});

describe("the wheel inside an overlay", async () => {
  const PANEL = "../../custom_components/myhome/frontend/panel/";
  const { holdScrimScroll, scrollsItself } = await import(`${PANEL}panel-calibration-layout.js`);
  const layoutSource = readFileSync(new URL(`${PANEL}panel-calibration-layout.js`, import.meta.url), "utf8");
  const wheel = (target) => { const event = new dom.window.WheelEvent("wheel", { bubbles: true, cancelable: true, composed: true, deltaY: 120 }); target.dispatchEvent(event); return event.defaultPrevented; };
  const touch = (target) => { const event = new dom.window.Event("touchmove", { bubbles: true, cancelable: true, composed: true }); target.dispatchEvent(event); return event.defaultPrevented; };
  /**
   * A box measured as the browser would (jsdom measures nothing: 0 and 0), `overflow-y: auto`. jsdom keeps a computed
   * style cached for a node in a shadow tree: an attribute changed on the document's own element drops the cache.
   */
  function box(element, { overflow = "auto", content = 900, height = 400 } = {}) {
    element.style.overflowY = overflow;
    Object.defineProperty(element, "scrollHeight", { configurable: true, value: content });
    Object.defineProperty(element, "clientHeight", { configurable: true, value: height });
    const root = element.ownerDocument.documentElement;
    root.toggleAttribute("data-style-cache"); root.toggleAttribute("data-style-cache");
    return element;
  }
  const world = () => {
    const data = sampleOverview();
    data.profiles[0].provenance.opening.origin_entity_id = "cover.kitchen_two";
    return data;
  };

  test("the wheel rule: the scrim and a child that scrolls nothing are held; a child of a body that overflows is not; a body overflow:auto too short to scroll is held", () => {
    const document = dom.window.document;
    const dialog = document.body.appendChild(document.createElement("dialog"));
    const head = dialog.appendChild(document.createElement("header"));
    const title = head.appendChild(document.createElement("h2"));
    const body = dialog.appendChild(document.createElement("div"));
    const line = body.appendChild(document.createElement("p"));
    const short = dialog.appendChild(document.createElement("div"));
    const shortLine = short.appendChild(document.createElement("p"));
    const foot = dialog.appendChild(document.createElement("footer"));
    box(body);
    box(short, { content: 120, height: 400 });
    const stop = holdScrimScroll(dialog);
    assert.equal(wheel(dialog), true, "the scrim, as before");
    assert.equal(wheel(title), true, "the header: nothing scrolls there");
    assert.equal(wheel(foot), true, "the foot");
    assert.equal(wheel(line), false, "a child of a body that overflows: the body scrolls");
    assert.equal(wheel(body), false, "the body itself");
    assert.equal(wheel(shortLine), true, "a body overflow:auto that does not overflow: held, never the page");
    assert.deepEqual([touch(dialog), touch(title), touch(line), touch(shortLine)], [true, true, false, true], "a touch move by the same rule");
    // overflow hidden or visible never scrolls, however long its content.
    box(body, { overflow: "hidden" });
    assert.equal(wheel(line), true, "overflow hidden");
    box(body, { overflow: "scroll" });
    assert.equal(wheel(line), false, "overflow scroll");
    stop();
    assert.equal(wheel(title), false, "removed");
    dialog.remove();
    assert.equal(scrollsItself(null), false);
    assert.match(layoutSource, /composedPath\(\)/, "through shadow roots");
  });

  test("the wheel through a shadow root (composedPath): a scroller inside the shadow tree lets it through; its host's other nodes are held", () => {
    const document = dom.window.document;
    const dialog = document.body.appendChild(document.createElement("dialog"));
    const host = dialog.appendChild(document.createElement("div"));
    const shadow = host.attachShadow({ mode: "open" });
    const head = shadow.appendChild(document.createElement("div"));
    const body = shadow.appendChild(document.createElement("div"));
    const line = body.appendChild(document.createElement("span"));
    box(body);
    const stop = holdScrimScroll(dialog);
    assert.deepEqual([wheel(head), wheel(line)], [true, false]);
    stop();
    dialog.remove();
  });

  test("in the tab the details' header and the wizard's bar hold the wheel; their bodies let it through only while they overflow", async () => {
    const context = await mount({ language: "it", overview: world() });
    context.tab().querySelector('.cal-row[data-entity="cover.kitchen_two"] [data-action="cal-tab-toggle"]').click(); await tick(6);
    const card = context.tab().querySelector(".cal-flow .cal-page:not(.cal-assign-page)");
    const head = card.querySelector(".cal-page-head");
    assert.ok(head, "the details' header");
    assert.equal(wheel(head), true, "the header: the page does not scroll (197 px at 1280, 500 at 360 before)");
    const body = card.querySelector(".cal-page-body");
    box(body, { content: 300, height: 400 });
    assert.equal(wheel(body.firstElementChild || body), true, "a body too short to scroll: held");
    box(body);
    assert.equal(wheel(body.firstElementChild || body), false, "a body that overflows scrolls itself");
    card.querySelector(".cal-page-close").click(); await tick(8);
    reset();
    const wizard = await mount({ language: "it", overview: world() });
    wizard.tab().querySelector('[data-action="cal-tab-measure"]').click(); await tick();
    const bar = wizard.wiz().querySelector(".wiz-head");
    assert.ok(bar, "the wizard's bar");
    assert.equal(wheel(bar), true, "the wizard's bar holds it");
    assert.equal(wheel(wizard.wiz().querySelector(".wiz-actions") || bar), true, "its foot too");
    const main = box(wizard.wiz().querySelector(".wiz-main"));
    assert.equal(wheel(main.firstElementChild || main), false, "its body scrolls");
  });
});
