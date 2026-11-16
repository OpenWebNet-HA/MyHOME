/**
 * The calibration views' layout rules (responsive map), one source for the scripts; the stylesheet's media queries
 * spell the same conditions. The widths of the tab itself are container queries and need no script.
 */
/** A landscape phone: too short for the phone's stacked bars, whatever its width. */
export const SHORT = "(max-height: 499px) and (orientation: landscape)";
/** The phone layout: a window under 720 px that is not a landscape phone (details as a page, bottom action bar). */
export const PHONE = "(max-width: 719px) and (min-height: 500px), (max-width: 719px) and (orientation: portrait)";
/**
 * What opens over the list (the wizard, a cover's details, the tab's sheets) is laid
 * out in three bands chosen by the window's width alone, a landscape phone included: up to 511 px a tendina from the
 * bottom across the whole width (SHEETS: its handle when closing costs nothing); 512–1023 px the phone's layout in a
 * dialog centred 480 px wide (NARROW, with the tendina); from 1024 px the medium dialog, 880 px (MEDIUM: the steps column
 * beside the content, the footer in one row). The stylesheet spells the same conditions.
 */
export const SHEETS = "(max-width: 511px)";
export const NARROW = "(max-width: 1023px)";
export const MEDIUM = "(min-width: 1024px)";

/**
 * The Calibration list by the tab's content width, in exactly two band breakpoints (the stylesheet's `@container caltab`
 * rules use the same numbers): under 640 px the phone's cards;
 * 640–1015 px rows on one card, the chips stacked in one column; from 1016 px the table, the chips in their columns. 640 is
 * where the rows hold the name at 200 px, a realistic (34-character) profile whole and the action. Nothing in the list is
 * measured by a script, no row adapts on its own; a cover's name wraps (never «…»), the action is never cut, a profile
 * chip's text only past a realistic length.
 */
export const LIST_BANDS = { table: 1016, rows: 640 };

/** The list's band for a content width (px). */
export function listBand(width) {
  return width >= LIST_BANDS.table ? "table" : width >= LIST_BANDS.rows ? "rows" : "cards";
}

/** The content width of the tab (`caltab`'s inline size: the box without its padding), 0 when it is not laid out. */
export function contentWidth(host) {
  const width = host?.clientWidth || 0;
  if (!width) return 0;
  const style = host.ownerDocument.defaultView.getComputedStyle(host);
  return width - (parseFloat(style.paddingLeft) || 0) - (parseFloat(style.paddingRight) || 0);
}

const matches = (query) => typeof matchMedia === "function" && matchMedia(query).matches;
const still = () => typeof matchMedia === "function" && matchMedia("(prefers-reduced-motion: reduce)").matches;

export const layout = {
  /** The list's phone layout (cards, the head's buttons stacked). */
  phone: () => matches(PHONE),
  /** The overlays as the tendina from the bottom. */
  sheets: () => matches(SHEETS),
  /** The medium dialog (the footer in one row, the steps column); without window rules (jsdom) as a desktop. */
  medium: () => !matches(NARROW),
  /**
   * A cover's card in two columns from 1024 px («Posizionamento» with its question, the outcome beside the
   * answers). Only where the window says so: without window rules (jsdom) the card keeps the phone's single column.
   */
  wide: () => matches(MEDIUM),
  /** The sheets slide in and out, unless motion is reduced. */
  slides: (query = SHEETS) => matches(query) && !still(),
  /** Motion reduced: nothing slides or fades. */
  still,
  /** In a browser whose motion is not reduced (never in jsdom, which has no window rules): the overlays move. */
  animates: () => matches("(min-width: 0px)") && !still(),
};

/**
 * The filter and order bar shows its chips (and «Ordina per» on the right) only when the chips on one line, the
 * order control and at least `BAR_GAP` px between them fit the bar's width; otherwise its two drop-downs. Measured, not
 * a container query: the chips' width depends on their labels and counts. Each bar is measured again when its width
 * changes (a ResizeObserver per bar).
 */
export const BAR_GAP = 16;
const watched = new WeakSet();
let observer = null;

function fitBar(bar) {
  // The wizard's list (A-01) always has the two
  // drop-downs (the filter beside «Ordina»), on the phone and in the dialog alike: one layout.
  if (bar.matches(".wiz-pick-bar")) { bar.classList.remove("is-chips"); return; }
  const chips = bar.querySelector(".cal-tab-chips"), compact = bar.querySelector(".cal-tab-compact");
  const room = bar.clientWidth;
  if (!chips || !compact || !room) return;
  bar.classList.add("is-chips", "is-measuring");
  // In the table layouts the bar has no order control: only the chips count.
  const order = compact.getBoundingClientRect().width;
  const need = chips.getBoundingClientRect().width + (order > 0 ? BAR_GAP + order : 0);
  bar.classList.remove("is-measuring");
  bar.classList.toggle("is-chips", need <= room);
  bar.dataset.width = String(room);
}

export function fitBars(root) {
  for (const bar of root?.querySelectorAll?.(".cal-filterbar") || []) {
    fitBar(bar);
    if (typeof ResizeObserver !== "function" || watched.has(bar)) continue;
    // Measured in the next frame, so a bar changing its own state never re-enters the observer's delivery
    // («ResizeObserver loop completed with undelivered notifications»).
    observer ||= new ResizeObserver((entries) => {
      const targets = entries.map((entry) => entry.target);
      (globalThis.requestAnimationFrame || ((callback) => setTimeout(callback, 16)))(() => {
        for (const target of targets) if (target.isConnected && String(target.clientWidth) !== target.dataset.width) fitBar(target);
      });
    });
    observer.observe(bar);
    watched.add(bar);
  }
}

/**
 * On iOS a native drop-down opens its picker over the lower half of the screen and scrolls the page to keep the
 * <select> above it; when the picker closes the page stays there (only the first time: afterwards the select is already
 * high enough). Around a filter's or an order's drop-down the panel notes where every scrolling box (and the window)
 * stood when it got the focus, and puts them back once the choice is made or the picker closes, after the list has
 * been redrawn. Returns the function that stops it.
 */
export function keepScrollOnPick(host) {
  if (!host?.addEventListener) return () => {};
  const view = host.ownerDocument?.defaultView || globalThis;
  let saved = null, timer = 0;
  const boxes = (start) => {
    const out = [];
    for (let node = start; node; node = node.parentNode || node.host) {
      if (node.nodeType === 11) { node = node.host; if (!node) break; }
      if (node.nodeType === 1 && node.scrollHeight > node.clientHeight + 1) out.push([node, node.scrollTop]);
    }
    return out;
  };
  const picker = (target) => target?.matches?.("select") && target.closest?.(".cal-filterbar, .wiz-pick-bar");
  const restore = () => {
    if (!saved || view.performance.now() - saved.at > 10000) { saved = null; return; }
    const { list, x, y } = saved;
    const apply = () => { for (const [node, top] of list) if (node.isConnected && Math.abs(node.scrollTop - top) > 1) node.scrollTop = top;
      if (Math.abs((view.scrollY || 0) - y) > 1) view.scrollTo?.(x, y); };
    (view.requestAnimationFrame || ((callback) => setTimeout(callback, 16)))(apply);
    clearTimeout(timer);
    // iOS animates the picker away: once more when it has gone.
    timer = setTimeout(() => { apply(); saved = null; }, 350);
  };
  const focusin = (event) => { if (picker(event.target)) saved = { list: boxes(event.target.parentNode), x: view.scrollX || 0, y: view.scrollY || 0, at: view.performance.now() }; };
  const settle = (event) => { if (picker(event.target)) restore(); };
  host.addEventListener("focusin", focusin);
  host.addEventListener("change", settle);
  host.addEventListener("focusout", settle);
  return () => { host.removeEventListener("focusin", focusin); host.removeEventListener("change", settle); host.removeEventListener("focusout", settle); clearTimeout(timer); };
}

/**
 * A dialog or sheet that opens puts the focus inside itself, for assistive technology and the keyboard; after a
 * tap, on its own box (no ring anywhere: WebKit draws one on a button focused by script), after a key, on its safe
 * button as before. The last input modality is noted once, for every panel view: a key (not a modifier
 * alone) is the keyboard, a press of a pointer or a finger (`pointerdown`, `touchstart`) is not.
 */
let keyboardInput = false;
if (typeof window !== "undefined" && window.addEventListener && !window.__myhomeInputWatch) {
  window.__myhomeInputWatch = true;
  window.addEventListener("keydown", (event) => {
    if (["Shift", "Control", "Alt", "Meta"].includes(event.key)) return;
    keyboardInput = true;
    // A focus given back quietly after a tap shows its ring as soon as the keyboard is used (the element with the focus,
    // through the shadow roots).
    let active = document.activeElement;
    while (active?.shadowRoot?.activeElement) active = active.shadowRoot.activeElement;
    for (const target of [...(event.composedPath?.() || []), active]) if (target?.hasAttribute?.(QUIET_FOCUS)) target.removeAttribute(QUIET_FOCUS);
  }, true);
  const pointer = () => { keyboardInput = false; };
  window.addEventListener("pointerdown", pointer, true);
  window.addEventListener("touchstart", pointer, { capture: true, passive: true });
}
/** Whether the last input was the keyboard. */
export const keyboardModality = () => keyboardInput;
export function focusOnOpen(box, target) {
  if (keyboardInput && target) target.focus({ preventScroll: true });
  else box?.focus({ preventScroll: true });
}

/**
 * An overlay or a sheet that closes gives the focus back to what opened it (the keyboard and a screen
 * reader go on from there), never scrolling the page for it (`preventScroll`); its ring shows only when the last input
 * was the keyboard. After a tap or a click the element is marked `data-quiet-focus` (the stylesheet draws no ring on it)
 * until it loses the focus or a key is pressed; `focusVisible: false` says the same to the browsers that know it.
 */
export const QUIET_FOCUS = "data-quiet-focus";
export function returnFocus(target) {
  if (!target?.focus || !target.isConnected) return false;
  const quiet = !keyboardInput;
  if (quiet) {
    target.setAttribute(QUIET_FOCUS, "");
    const clear = () => { target.removeAttribute(QUIET_FOCUS); target.removeEventListener("blur", clear); };
    target.addEventListener("blur", clear);
  } else target.removeAttribute(QUIET_FOCUS);
  target.focus({ preventScroll: true, focusVisible: !quiet });
  return true;
}

/**
 * Before a modal dialog of ours opens, the focus is let go (it rests on the page's body). A dialog that
 * closes gives the focus back to what had it when it opened, and Safari scrolls the page to show it (the cover tabs a
 * render had focused, so the page jumped up to them); with nothing to give back nothing moves, and the panel puts the
 * focus where it belongs itself, without scrolling (`focus({ preventScroll: true })`).
 */
export function releaseFocus(node) {
  const root = node?.getRootNode?.(), body = node?.ownerDocument?.body;
  for (const active of [root?.activeElement, node?.ownerDocument?.activeElement]) if (active && active !== body) active.blur?.();
}

/**
 * A press on an overlay's scrim (the dialog element itself, outside its box) closes it only while closing
 * costs nothing: the same predicate as the tendina's handle and its drag (`closable`); otherwise it does nothing but a
 * short nudge of the box (`is-nudged`, none with reduced motion). The press must start and end on the scrim: a text
 * selection dragged out of the box ends on the dialog too, and is not a press. Escape keeps its own rule (the ✕). Returns
 * the function that stops it.
 */
export const NUDGE_MS = 240;
export function closeOnScrim(dialog, { closable = () => true, onClose = () => {}, box = null } = {}) {
  if (!dialog?.addEventListener) return () => {};
  let pressed = false, timer = 0;
  const down = (event) => { pressed = event.target === dialog; };
  const click = (event) => {
    const hit = pressed && event.target === dialog;
    pressed = false;
    if (!hit) return;
    if (closable()) { onClose(); return; }
    const target = box || dialog;
    target.classList.remove("is-nudged");
    clearTimeout(timer);
    if (still()) return;
    void target.offsetWidth;
    target.classList.add("is-nudged");
    timer = setTimeout(() => target.classList.remove("is-nudged"), NUDGE_MS);
  };
  dialog.addEventListener("pointerdown", down);
  dialog.addEventListener("click", click);
  return () => { clearTimeout(timer); dialog.removeEventListener("pointerdown", down); dialog.removeEventListener("click", click); };
}

/** An element that really scrolls: its `overflow-y` is auto or scroll and its content overflows it. */
export function scrollsItself(element) {
  if (element?.nodeType !== 1) return false;
  const view = element.ownerDocument?.defaultView;
  return !!view && /(auto|scroll)/.test(view.getComputedStyle(element).overflowY) && element.scrollHeight > element.clientHeight;
}

/**
 * A wheel or a touch move on an overlay's scrim (the dialog element itself, outside its box: the
 * blurred area) never scrolls the page under it. Nor on the box's header or foot. Inside the dialog a wheel
 * or a touch move scrolls only a box that really scrolls: from the event's target up to the dialog (excluded, through
 * shadow roots: `composedPath`), the first element whose `overflow-y` is auto or scroll and whose content overflows
 * lets it through, and its ends keep the scroll to themselves (`overscroll-behavior: contain`, the stylesheet); with
 * none (the header, the foot, a body too short to scroll) it is held. No direction is worked out, a touch move as a
 * wheel. The listeners are not passive, so `preventDefault` holds. Returns the function that stops it.
 */
export function holdScrimScroll(dialog) {
  if (!dialog?.addEventListener) return () => {};
  const hold = (event) => {
    const path = event.composedPath?.() || [];
    const end = path.indexOf(dialog);
    if (path.slice(0, end < 0 ? 0 : end).some(scrollsItself)) return;
    event.preventDefault();
  };
  for (const type of ["wheel", "touchmove"]) dialog.addEventListener(type, hold, { passive: false });
  return () => { for (const type of ["wheel", "touchmove"]) dialog.removeEventListener(type, hold, { passive: false }); };
}

/**
 * The box the page scrolls in. In Home Assistant a custom panel scrolls itself: its element
 * (`myhome-panel`, `:host { height: 100%; overflow: auto }`) is the scrolling box, not the document, whose body never
 * moves; found by walking up from `node` through the shadow roots to the first box whose overflow scrolls (one we hold
 * counts as found), the document's own scrolling element when there is none (a page that scrolls the window).
 */
export function scrollContainer(node) {
  const doc = node?.ownerDocument || document;
  for (let element = node?.parentElement || node?.getRootNode?.().host; element; element = element.parentElement || element.getRootNode?.().host) {
    if (element === doc.documentElement || element === doc.body) break;
    if (held.has(element) || /(auto|scroll)/.test(doc.defaultView.getComputedStyle(element).overflowY)) return element;
  }
  return doc.scrollingElement || doc.documentElement;
}

/**
 * Hold the page still under an overlay without moving it: `overflow: hidden` on the box that really
 * scrolls (`scrollContainer`), nothing else. Its scroll offset is kept by the browser (an element whose overflow turns
 * hidden keeps its scrollTop), so nothing is saved nor put back, nothing is fixed in place: the page under the overlay
 * does not move by a pixel when it opens or closes. Its width does not change either: the box keeps its scrollbar's
 * room (`scrollbar-gutter: stable`, the stylesheet's `:host`). The overlays keep their own scroll to themselves
 * (`overscroll-behavior: contain`). Several holders count: the box is let go when the last one lets go. Returns the
 * function that lets go (once).
 */
const held = new Map();
export function lockScroll(host) {
  if (!host) return () => {};
  const box = scrollContainer(host);
  const entry = held.get(box) || { count: 0, overflow: box.style.overflow };
  if (!entry.count) box.style.overflow = "hidden";
  entry.count++;
  held.set(box, entry);
  let done = false;
  return () => {
    if (done) return;
    done = true;
    if (--entry.count > 0) return;
    held.delete(box);
    box.style.overflow = entry.overflow;
    if (!box.getAttribute("style")) box.removeAttribute("style");
  };
}

/** The room the name of a wizard list's row keeps before its chips leave the side for under it. */
export const ROW_NAME_MIN = 200;

/**
 * The rows of the wizard's lists (A-01, not the Calibration list) follow the page
 * cards' measured rule instead of a fixed list width: the chips beside the name, side by side while the name keeps
 * ROW_NAME_MIN px (`is-beside`), one under the other while it still does (`is-beside is-stacked`), under the name
 * otherwise. Measured again when a list's width changes.
 */
export function fitRows(root) {
  for (const list of root?.querySelectorAll?.(".cal-batch-rows") || []) {
    for (const row of list.querySelectorAll(":scope > .cal-batch-row")) fitRow(row);
    if (typeof ResizeObserver !== "function" || rowsWatched.has(list)) continue;
    rowsObserver ||= new ResizeObserver((entries) => {
      const lists = entries.map((entry) => entry.target);
      (globalThis.requestAnimationFrame || ((callback) => setTimeout(callback, 16)))(() => {
        for (const target of lists) if (target.isConnected) for (const row of target.querySelectorAll(":scope > .cal-batch-row")) fitRow(row);
      });
    });
    rowsObserver.observe(list);
    rowsWatched.add(list);
  }
}
const rowsWatched = new WeakSet();
let rowsObserver = null;

function fitRow(row) {
  row.classList.remove("is-beside", "is-stacked");
  const lead = row.querySelector(".cal-batch-lead"), chips = [...row.querySelectorAll(".cal-batch-chips .badge, .cal-batch-chips .chip, .cal-batch-chips .muted")]
    .filter((chip) => chip.getClientRects().length);
  const view = row.ownerDocument.defaultView, style = view.getComputedStyle(row);
  const inner = row.clientWidth - (parseFloat(style.paddingLeft) || 0) - (parseFloat(style.paddingRight) || 0);
  if (!inner || !lead || !chips.length) return;
  const gap = parseFloat(style.columnGap) || 12, chipGap = 8;
  const widths = chips.map((chip) => chip.getBoundingClientRect().width);
  const room = inner - lead.getBoundingClientRect().width - 2 * gap;
  const together = widths.reduce((sum, width) => sum + width, 0) + chipGap * (widths.length - 1);
  // The name's column also keeps its line (room, address) on one line beside the chips, never less than
  // ROW_NAME_MIN; a line that does not fit even under stacked chips sends the chips under the name.
  const line = row.querySelector(".cal-row-name .muted");
  let need = ROW_NAME_MIN;
  if (line) {
    const before = line.style.whiteSpace;
    line.style.whiteSpace = "nowrap";
    const range = row.ownerDocument.createRange?.();
    if (range) { range.selectNodeContents(line); need = Math.max(need, Math.ceil(range.getBoundingClientRect?.().width || 0)); }
    line.style.whiteSpace = before;
    if (!line.getAttribute("style")) line.removeAttribute("style");
  }
  if (room - together >= need) row.classList.add("is-beside");
  else if (room - Math.max(...widths) >= need) row.classList.add("is-beside", "is-stacked");
}

/**
 * The parts of the wizard's header, a white 64 px
 * bar [✕][name / step line][Passi] over a 4 px line of segments, on the phone and in the narrow dialog (the medium
 * dialog has its steps column instead; the landscape phone keeps its band and «PASSI» strip).
 */
const escapeText = (value) => String(value ?? "").replace(/[&<>"']/g, (char) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[char]);

/** The bar's «Passi» icon button (lines with dots), with the attribute that wires it (`data-wiz` or `data-action`). */
export function headStepsMarkup(label, wiring, expanded = false) {
  return `<button type="button" class="icon-button wiz-head-steps" ${wiring} aria-haspopup="dialog" aria-expanded="${expanded}" aria-label="${escapeText(label)}" title="${escapeText(label)}"><ha-icon icon="mdi:format-list-bulleted" aria-hidden="true"></ha-icon></button>`;
}

/** The segmented line: one segment per step, each in its step's state (done, current, future). */
export function segmentsMarkup(states = []) {
  return states.map((state) => `<span data-step-state="${escapeText(state)}"></span>`).join("");
}

/** Every «Passi» button of a view (the bar's, the steps strip's) says whether the sheet is open. */
export function stepsExpanded(root, open) {
  for (const button of root?.querySelectorAll?.(".wiz-head-steps") || []) button.setAttribute("aria-expanded", String(!!open));
}

/** Hidden by the layout (display: none on it or an ancestor): it has no box. Without a layout (jsdom) nothing is. */
export function hiddenByStyle(element) {
  if (!element?.isConnected) return true;
  const view = element.ownerDocument.defaultView;
  for (let node = element; node && node.nodeType === 1; node = node.parentElement) if (view.getComputedStyle(node).display === "none") return true;
  return false;
}

/** The first of these elements the layout shows (the first one when none is laid out). */
export function shown(elements) {
  const list = [...(elements || [])];
  return list.find((element) => !hiddenByStyle(element)) || list[0] || null;
}

/**
 * The phone's steps sheet also closes by dragging its bottom row (the handle) upwards. Past
 * DRAG_CLOSE px the row is pressed as a tap would press it (the view's own close, its focus and aria-expanded); a
 * shorter drag puts the sheet back and the tap that ends it is dropped; a tap and the keyboard press the row as
 * before. The sheet follows the finger meanwhile and leaves from where it was let go (`--cal-drag`). Returns the
 * function that stops it.
 */
export const DRAG_CLOSE = 32;
const DRAG_SLOP = 4;
/*
 * On an iPhone, Safari cancels a touch's pointer stream (`pointercancel`) as soon as it decides the
 * finger pans, whatever `touch-action` says on a button inside a scrolling sheet, so the sheet always went back. A
 * finger is followed with touch events (the move cancelled with `preventDefault`, a non-passive listener: nothing
 * scrolls, nothing is cancelled); a mouse or a pen with pointer events. The handle's row has `touch-action: none` and
 * no text selection too (CSS).
 */
export function dragToClose(root, { within = null } = {}) {
  if (!root?.addEventListener) return () => {};
  const rowOf = (event) => {
    const row = event.target?.closest?.(".wiz-sheet-fold");
    return row && (!within || row.closest(within)) ? row : null;
  };
  // One gesture: where it started, how far up it went; the sheet follows and leaves from where it was let go.
  const gesture = (row, start) => {
    const card = row.closest(".wiz-sheet-card");
    let moved = 0;
    card?.style.removeProperty("--cal-drag");
    return {
      move(y) { moved = Math.min(0, y - start); if (card) card.style.transform = moved ? `translateY(${moved}px)` : ""; },
      end(cancelled) {
        const far = !cancelled && moved <= -DRAG_CLOSE;
        if (card) {
          if (far) card.style.setProperty("--cal-drag", `${moved}px`);
          card.style.transform = "";
          if (far) setTimeout(() => card.style.removeProperty("--cal-drag"), 400);
        }
        if (!far && (cancelled || moved >= -DRAG_SLOP)) return;
        // Past the threshold the row is pressed once, now; the native click that may follow the release is not a tap.
        if (far) row.click();
        const swallow = (event) => { event.preventDefault(); event.stopImmediatePropagation(); };
        row.addEventListener("click", swallow, { capture: true, once: true });
        setTimeout(() => row.removeEventListener("click", swallow, { capture: true }), 400);
      },
    };
  };
  const touchStart = (event) => {
    const row = rowOf(event);
    if (!row || event.touches.length !== 1) return;
    const touch = event.touches[0], drag = gesture(row, touch.clientY), id = touch.identifier;
    const find = (list) => [...list].find((item) => item.identifier === id);
    const move = (next) => { const point = find(next.touches); if (!point) return; if (next.cancelable) next.preventDefault(); drag.move(point.clientY); };
    const finish = (cancelled) => (next) => {
      if (!find(next.changedTouches)) return;
      row.removeEventListener("touchmove", move); row.removeEventListener("touchend", ended); row.removeEventListener("touchcancel", stopped);
      drag.end(cancelled);
    };
    const ended = finish(false), stopped = finish(true);
    row.addEventListener("touchmove", move, { passive: false });
    row.addEventListener("touchend", ended);
    row.addEventListener("touchcancel", stopped);
  };
  const pointerDown = (event) => {
    const row = rowOf(event);
    if (!row || event.pointerType === "touch" || (event.button ?? 0) > 0) return;
    const drag = gesture(row, event.clientY);
    try { row.setPointerCapture?.(event.pointerId); } catch { /* a synthetic pointer */ }
    const move = (next) => drag.move(next.clientY);
    const finish = (cancelled) => () => {
      row.removeEventListener("pointermove", move); row.removeEventListener("pointerup", up); row.removeEventListener("pointercancel", cancel);
      drag.end(cancelled);
    };
    const up = finish(false), cancel = finish(true);
    row.addEventListener("pointermove", move);
    row.addEventListener("pointerup", up);
    row.addEventListener("pointercancel", cancel);
  };
  root.addEventListener("touchstart", touchStart, { passive: true });
  root.addEventListener("pointerdown", pointerDown);
  return () => { root.removeEventListener("touchstart", touchStart); root.removeEventListener("pointerdown", pointerDown); };
}

/**
 * After a tap nothing keeps a focus that shows (a ring, or the look of a pressed button). The
 * focus goes back to `target` in every case (the keyboard and a screen reader go on from there), quietly after a tap
 * (`returnFocus`); with nothing to give it to, whatever had it lets it go.
 */
export function focusAfterClose(target) {
  if (returnFocus(target)) return;
  const active = target?.getRootNode?.().activeElement;
  if (active && active !== target?.ownerDocument?.body) active.blur?.();
}

/**
 * The fades at a scrolling box's edges show only while the content goes on past them: `has-above`
 * once it is scrolled, `has-more` while there is more below, on the box itself (its mask) and on `top` and
 * `bottom` (the bar over it, the actions under it). Watched once per box; returns its update.
 */
export function watchEdges(scroller, { top = null, bottom = null } = {}) {
  if (!scroller) return () => {};
  if (scroller._edges) return scroller._edges;
  const update = () => {
    const above = scroller.scrollTop > 1, below = scroller.scrollHeight - scroller.scrollTop - scroller.clientHeight > 1;
    scroller.classList.toggle("has-above", above);
    scroller.classList.toggle("has-more", below);
    top?.classList.toggle("has-above", above);
    bottom?.classList.toggle("has-more", below);
  };
  scroller.addEventListener("scroll", update, { passive: true });
  if (typeof ResizeObserver === "function") {
    const observer = new ResizeObserver(update);
    observer.observe(scroller);
    for (const child of scroller.children) observer.observe(child);
  }
  scroller._edges = update;
  update();
  return update;
}

/**
 * Every block that opens under its link slides its height (400 ms, ease-in-out), it never jumps,
 * in Safari too (no `interpolate-size` there): the Web Animations API on measured heights. Without it, or with reduced
 * motion, the block opens at once.
 */
export const SLIDE_MS = 400;
export const SLIDE_EASE = "ease-in-out";
const SLIDE = { duration: SLIDE_MS, easing: SLIDE_EASE };
const slides = (element) => !!element && typeof element.animate === "function" && !still();

/**
 * The overlay follows its content's height when what it shows changes (a card's tab, the question and its
 * outcome): `change` draws the new view, then the box slides from the height it had to the new one, as a disclosure
 * opens (SLIDE). Nothing moves when the height stays the same, nor with reduced motion; one still running is taken up
 * from where it is. A redraw of the same view (the 15 s reads) never comes here.
 */
export function slideHeight(box, change) {
  if (!slides(box) || !box.isConnected) { change(); return; }
  const from = box.getBoundingClientRect().height;
  box._heightSlide?.cancel();
  change();
  const to = box.getBoundingClientRect().height;
  if (!(from > 0) || !(to > 0) || Math.abs(from - to) < 1) return;
  const animation = box.animate({ height: [`${from}px`, `${to}px`] }, SLIDE);
  box._heightSlide = animation;
  const end = () => { if (box._heightSlide === animation) box._heightSlide = null; };
  animation.onfinish = end;
  animation.oncancel = end;
}

/** Slide a shown block open (from 0 to its height) or closed (to 0, then `done`). */
export function slideBlock(element, open, done) {
  if (!slides(element)) { done?.(); return; }
  const finish = () => { element.style.removeProperty("overflow"); done?.(); };
  element.style.overflow = "hidden";
  const height = open ? element.scrollHeight : element.offsetHeight;
  const animation = element.animate(open ? { height: ["0px", `${height}px`], opacity: [0, 1] } : { height: [`${height}px`, "0px"], opacity: [1, 0] }, SLIDE);
  animation.onfinish = finish;
  animation.oncancel = finish;
}

/**
 * What a disclosure has just revealed is brought into view, only as far as it needs (the nearest
 * edge, as `scrollIntoView({ block: "nearest" })` does), smoothly unless motion is reduced; nothing moves when it is
 * already wholly in view (judged here, against the box that scrolls it). Returns whether it scrolled.
 */
export function revealOpened(element) {
  if (!element?.isConnected || typeof element.scrollIntoView !== "function") return false;
  const doc = element.ownerDocument, scroller = scrollContainer(element);
  const page = !scroller || scroller === doc.scrollingElement || scroller === doc.documentElement;
  const view = page ? { top: 0, bottom: doc.defaultView.innerHeight } : scroller.getBoundingClientRect(), box = element.getBoundingClientRect();
  if (box.top >= view.top && box.bottom <= view.bottom) return false;
  element.scrollIntoView({ block: "nearest", behavior: still() ? "auto" : "smooth" });
  return true;
}

/**
 * The disclosures (`<details>`: a card's tail, the wizard's and the tab's explanations) slide instead of jumping.
 * Once open (its slide finished, or at once without one) what one reveals is brought into view (`revealOpened`);
 * only an opening by its summary does so, never one a redraw restores.
 */
export const SLIDING_DETAILS = "details.cal-card-tail, details.wiz-accordion, details.cal-disclosure, details.cal-fold";
export function slideDetails(root) {
  if (!root?.addEventListener) return () => {};
  const click = (event) => {
    const summary = event.target.closest?.("summary"), details = summary?.parentElement;
    if (!details?.matches?.(SLIDING_DETAILS) || details.dataset.sliding) return;
    // Without a slide the browser opens it: its `toggle` reveals it.
    if (!slides(details)) { if (!details.open) details.dataset.reveal = "1"; return; }
    event.preventDefault();
    const borders = details.offsetHeight - details.clientHeight;
    const end = () => { details.style.removeProperty("overflow"); delete details.dataset.sliding; details.classList.remove("is-closing"); };
    details.dataset.sliding = "1";
    details.style.overflow = "hidden";
    if (!details.open) {
      const from = details.offsetHeight;
      details.open = true;
      const animation = details.animate({ height: [`${from}px`, `${details.offsetHeight}px`] }, SLIDE);
      animation.onfinish = () => { end(); revealOpened(details); }; animation.oncancel = end;
    } else {
      // The chevron turns back as the block starts to close (`is-closing`), not when it has gone.
      details.classList.add("is-closing");
      const animation = details.animate({ height: [`${details.offsetHeight}px`, `${summary.offsetHeight + borders}px`] }, SLIDE);
      animation.onfinish = () => { details.open = false; end(); };
      animation.oncancel = end;
    }
  };
  // `toggle` does not bubble: it is heard on its way down.
  const toggle = (event) => {
    const details = event.target;
    if (!details?.dataset?.reveal) return;
    delete details.dataset.reveal;
    if (details.open) revealOpened(details);
  };
  root.addEventListener("click", click);
  root.addEventListener("toggle", toggle, true);
  return () => { root.removeEventListener("click", click); root.removeEventListener("toggle", toggle, true); };
}

/**
 * Shared: a tap or a click on the choice already made takes it back, in A-01's list and in the Assign and
 * Change profile sheets alike: the input matching `selector` that was ticked when the pointer went down is unticked by
 * the click that follows, then `onUntick(input)`. The keyboard keeps the radios' own behaviour (Space never unticks: no
 * pointer goes down). Returns the function that stops it.
 */
export function untickOnSecondTap(root, selector, onUntick) {
  if (!root?.addEventListener) return () => {};
  let pressed = null;
  const down = (event) => {
    const input = event.target.matches?.(selector) ? event.target : event.target.closest?.("label")?.querySelector(selector);
    pressed = input?.checked ? input : null;
  };
  const click = (event) => {
    if (!event.target.matches?.(selector)) return;
    const again = pressed === event.target;
    pressed = null;
    if (!again) return;
    event.target.checked = false;
    onUntick?.(event.target, event);
  };
  root.addEventListener("pointerdown", down, true);
  root.addEventListener("click", click);
  return () => { root.removeEventListener("pointerdown", down, true); root.removeEventListener("click", click); };
}

/** The phone sheet's bottom row: a grey handle that closes the sheet by a tap or a drag upwards (44 px, named). */
export function sheetFoldMarkup(label, wiring) {
  return `<button type="button" class="wiz-sheet-fold" ${wiring} aria-label="${escapeText(label)}" title="${escapeText(label)}"><span class="wiz-sheet-handle" aria-hidden="true"></span></button>`;
}

/**
 * The tendina from the bottom (up to 511 px) closes by a drag downwards, as Home Assistant's
 * own sheets, but only while closing costs nothing (its handle is shown: `closable()`; during a session there is no
 * handle and only the ✕, with its confirmation). The drag starts on the handle or the header at any time, on the content
 * only while it is scrolled to its top and the finger goes down (a finger going up scrolls). Past DRAG_SHEET px, or a
 * quick flick, the overlay's ✕ is pressed (`onClose`, the same semantics: should it ask and be refused, the tendina comes
 * back up); a shorter drag puts it back. The box follows the finger and leaves from where it was let go (`--cal-drag`).
 * A finger is followed with touch events (the move cancelled once it drags: nothing scrolls under it), a mouse or a pen
 * with pointer events from the handle and the header. Returns the function that stops it.
 */
export const DRAG_SHEET = 96;
export const OVERLAY_HEADS = ".cal-flow-handle, .wiz-head, .cal-page-head, .dialog-head";
export const OVERLAY_BODIES = ".wiz-main, .cal-page-body, .cal-dialog-body";
export function dragOverlay(box, { closable = () => true, onClose = () => {} } = {}) {
  if (!box?.addEventListener) return () => {};
  let drag = null;
  const begin = (target, y) => {
    if (!layout.sheets() || !closable() || box.closest(".is-leaving")) return null;
    // Not from the steps sheet or a confirmation laid over the content.
    if (target.closest?.(".wiz-sheet, .wiz-dialog")) return null;
    const head = target.closest?.(OVERLAY_HEADS), body = head ? null : target.closest?.(OVERLAY_BODIES);
    if (!head && !(body && body.scrollTop <= 0)) return null;
    return { y, moved: 0, active: false, body, at: performance.now() };
  };
  const move = (y, event) => {
    if (!drag) return;
    const dy = y - drag.y;
    if (!drag.active) {
      if (Math.abs(dy) < DRAG_SLOP) return;
      // Upwards, or the content no longer at its top: a scroll, not a drag.
      if (dy < 0 || (drag.body && drag.body.scrollTop > 0)) { drag = null; return; }
      drag.active = true;
      box.classList.add("is-dragging");
    }
    if (event?.cancelable) event.preventDefault();
    drag.moved = Math.max(0, dy);
    box.style.transform = drag.moved ? `translateY(${drag.moved}px)` : "";
  };
  const end = (cancelled) => {
    const current = drag;
    drag = null;
    if (!current?.active) return;
    box.classList.remove("is-dragging");
    // A flick: half the distance, fast (0.6 px/ms).
    const quick = current.moved >= DRAG_SHEET / 2 && current.moved / Math.max(1, performance.now() - current.at) > 0.6;
    const far = !cancelled && (current.moved >= DRAG_SHEET || quick);
    box.style.setProperty("--cal-drag", `${current.moved}px`);
    box.style.transform = "";
    // The tap that may end the gesture is not a press of what lies under the finger.
    const swallow = (event) => { event.preventDefault(); event.stopImmediatePropagation(); };
    box.addEventListener("click", swallow, { capture: true, once: true });
    setTimeout(() => box.removeEventListener("click", swallow, { capture: true }), 400);
    if (far) onClose();
    setTimeout(() => box.style.removeProperty("--cal-drag"), 400);
  };
  const touchStart = (event) => {
    if (event.touches.length !== 1) { drag = null; return; }
    drag = begin(event.target, event.touches[0].clientY);
  };
  const touchMove = (event) => { if (drag) move(event.touches[0]?.clientY ?? drag.y, event); };
  const touchEnd = () => end(false);
  const touchCancel = () => end(true);
  const pointerDown = (event) => {
    if (event.pointerType === "touch" || (event.button ?? 0) > 0 || !event.target.closest?.(OVERLAY_HEADS)) return;
    drag = begin(event.target, event.clientY);
    if (!drag) return;
    const pointerMove = (next) => move(next.clientY, next);
    const finish = (cancelled) => () => {
      box.ownerDocument.removeEventListener("pointermove", pointerMove);
      box.ownerDocument.removeEventListener("pointerup", up);
      box.ownerDocument.removeEventListener("pointercancel", cancel);
      end(cancelled);
    };
    const up = finish(false), cancel = finish(true);
    box.ownerDocument.addEventListener("pointermove", pointerMove);
    box.ownerDocument.addEventListener("pointerup", up);
    box.ownerDocument.addEventListener("pointercancel", cancel);
  };
  box.addEventListener("touchstart", touchStart, { passive: true });
  box.addEventListener("touchmove", touchMove, { passive: false });
  box.addEventListener("touchend", touchEnd);
  box.addEventListener("touchcancel", touchCancel);
  box.addEventListener("pointerdown", pointerDown);
  return () => {
    box.removeEventListener("touchstart", touchStart); box.removeEventListener("touchmove", touchMove);
    box.removeEventListener("touchend", touchEnd); box.removeEventListener("touchcancel", touchCancel);
    box.removeEventListener("pointerdown", pointerDown);
  };
}

/**
 * A menu of ours (a row's ⋮ in the list, the footer's ⋮) closes on the first press outside it, and that
 * press does nothing else, as Home Assistant's menus: the press closes it and the click that follows it, wherever it
 * lands (another row, the dialog's scrim), is swallowed. Returns the function that stops watching (the menu closed
 * another way).
 */
export function closeOnOutsidePress(wrap, close) {
  const doc = wrap?.ownerDocument;
  if (!doc) return () => {};
  const stop = () => doc.removeEventListener("pointerdown", down, true);
  function down(event) {
    if (event.composedPath().includes(wrap)) return;
    stop();
    const swallow = (next) => { next.preventDefault(); next.stopImmediatePropagation(); };
    doc.addEventListener("click", swallow, { capture: true, once: true });
    // A press that becomes a scroll has no click: the swallow does not linger.
    setTimeout(() => doc.removeEventListener("click", swallow, { capture: true }), 600);
    close();
  }
  doc.addEventListener("pointerdown", down, true);
  return stop;
}

/**
 * A field in a sheet or an overlay is never under the phone's keyboard. While the visual
 * viewport is shorter than the window (the keyboard is up, iOS and Android alike), its height and offset go to the
 * host as `--cal-vv-h` / `--cal-vv-top`, which bound the overlay's dialog (the sheet then sits above the keyboard); once
 * resized, the field being typed in is brought into view in its own scrolling container (`block: "nearest"`). Nothing
 * is `position: fixed` beyond the dialog itself, and the page's scroll lock is untouched. Returns its undo.
 */
export function followKeyboard(host) {
  const view = host?.ownerDocument?.defaultView, viewport = view?.visualViewport;
  if (!viewport) return () => {};
  const field = () => {
    const active = host.getRootNode?.()?.activeElement;
    return active && host.contains(active) && active.matches?.("input:not([type=checkbox]):not([type=radio]), textarea, select")
      && active.closest?.("dialog") ? active : null;
  };
  const update = () => {
    // The keyboard only with a field being typed in: a browser's own bars never count as one.
    const input = field();
    const keyboard = !!input && viewport.height < view.innerHeight - 80;
    if (keyboard) {
      host.style.setProperty("--cal-vv-h", `${Math.round(viewport.height)}px`);
      host.style.setProperty("--cal-vv-top", `${Math.round(viewport.offsetTop || 0)}px`);
    } else {
      host.style.removeProperty("--cal-vv-h");
      host.style.removeProperty("--cal-vv-top");
    }
    host.toggleAttribute("data-keyboard", keyboard);
    // The field's whole row in view (its ✓ and ✕ too), with its margin (`scroll-margin`, the stylesheet).
    const target = input?.closest?.(".cal-pairs > div, .wiz-field") || input;
    if (keyboard && target) (view.requestAnimationFrame || ((callback) => setTimeout(callback, 16)))(() => target.scrollIntoView?.({ block: "nearest" }));
  };
  // The field left (✓, ✕, Enter, Escape): as the keyboard goes, at once.
  const left = () => (view.requestAnimationFrame || ((callback) => setTimeout(callback, 16)))(update);
  viewport.addEventListener("resize", update);
  viewport.addEventListener("scroll", update);
  host.addEventListener("focusout", left);
  return () => { viewport.removeEventListener("resize", update); viewport.removeEventListener("scroll", update); host.removeEventListener("focusout", left); };
}

/**
 * A profile chip cut short (`data-cut`, where its name does not fit its place) shows
 * the rest under a fine pointer only (`(hover: hover) and (pointer: fine)`): while the pointer stays on it its text runs
 * to the end and back in a loop, a short pause at each end (the distance and a duration that follows it as
 * `--cal-chip-shift` / `--cal-chip-dur`, `is-marquee`), and it is back at the start as soon as the pointer leaves. On a
 * touch screen nothing moves and nothing scrolls: the «…» (a tap opens the cover's card, where the name is whole). With
 * reduced motion the «…» stays. The whole name is always in its title and its label.
 */
/**
 * A status chip never wraps. Each list's status column is as wide as its widest
 * chip (`--cal-status-w` on the list, read by the table's and the rows' columns). The phone's card gives
 * every chip a line of its own, always: nothing to measure there (`data-status-wide` is gone). Measured: nothing changes
 * where nothing is laid out.
 */
export function fitStatus(root) {
  for (const list of root?.querySelectorAll?.(".cal-list:not(.cal-batch-list)") || []) {
    const chips = [...list.querySelectorAll(".cal-row-status > .cal-badge")];
    const widest = Math.ceil(Math.max(0, ...chips.map((chip) => chip.getBoundingClientRect().width)));
    if (!widest) { list.style.removeProperty("--cal-status-w"); continue; }
    if (list.style.getPropertyValue("--cal-status-w") !== `${widest}px`) list.style.setProperty("--cal-status-w", `${widest}px`);
  }
}

export function markCutChips(root) {
  for (const text of root?.querySelectorAll?.(".cal-profile-chip .cal-chip-text") || []) {
    const chip = text.closest(".cal-profile-chip"), cut = text.scrollWidth > text.clientWidth + 1;
    if (chip.hasAttribute("data-cut") !== cut) chip.toggleAttribute("data-cut", cut);
  }
}

export function followCutChips(host) {
  const view = host?.ownerDocument?.defaultView;
  const pointer = () => !!view?.matchMedia?.("(hover: hover) and (pointer: fine)")?.matches && !view.matchMedia("(prefers-reduced-motion: reduce)").matches;
  const over = (event) => {
    const chip = event.target.closest?.(".cal-profile-chip");
    if (!chip || chip.classList.contains("is-marquee") || chip.contains(event.relatedTarget) || !pointer()) return;
    const text = chip.querySelector(".cal-chip-text");
    const shift = text ? text.scrollWidth - text.clientWidth : 0;
    if (shift <= 1) return;
    chip.style.setProperty("--cal-chip-shift", `${-Math.ceil(shift + 4)}px`);
    // One loop: a pause, to the end, a pause, back (each way 30 % of it, at about 45 px/s, never under 0.8 s).
    chip.style.setProperty("--cal-chip-dur", `${Math.round(Math.max(0.8, shift / 45) / 0.3 * 10) / 10}s`);
    chip.classList.add("is-marquee");
  };
  const out = (event) => {
    const chip = event.target.closest?.(".cal-profile-chip.is-marquee");
    if (!chip || chip.contains(event.relatedTarget)) return;
    chip.classList.remove("is-marquee");
  };
  host.addEventListener("mouseover", over);
  host.addEventListener("mouseout", out);
  return () => { host.removeEventListener("mouseover", over); host.removeEventListener("mouseout", out); };
}
