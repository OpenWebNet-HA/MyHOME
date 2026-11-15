/**
 * The calibration views' layout rules (responsive map), one source for the scripts; the stylesheet's media queries
 * spell the same conditions. The widths of the tab itself are container queries and need no script.
 */
/** A landscape phone: too short for the phone's stacked bars, whatever its width. */
export const SHORT = "(max-height: 499px) and (orientation: landscape)";
/** The phone layout: a window under 720 px that is not a landscape phone (details as a page, bottom action bar). */
export const PHONE = "(max-width: 719px) and (min-height: 500px), (max-width: 719px) and (orientation: portrait)";
/** The wizard and the sequence full screen: the phone, and the landscape phone with its own two-column layout. */
export const FULL_SCREEN = `(max-width: 719px), ${SHORT}`;
/** Dialogs as sheets from the bottom: a window under 720 px wide or under 500 px high; centred otherwise. */
export const SHEETS = "(max-width: 719px), (max-height: 499px)";

/**
 * Q-3: the Calibration list by the tab's content width (the stylesheet's `@container caltab` rules use the same
 * numbers): the table with its actions column, the table over three full-width columns, cards with the three detail
 * blocks in one row, cards with the blocks one under the other.
 */
export const LIST_BANDS = { table: 1200, tableFull: 1016, cardsRow: 700 };

/**
 * Q-22: the content width from which a card's header could lie on the blocks' three columns: its third column must
 * hold the widest profile chip (180 px, cut beyond), the widest action («Apri sessione», ~120 px at 14 px semibold with
 * its 14 px padding), ⋮ (40 px) and two 8 px gaps = 356 px; three such columns, two 32 px gaps and the card's 2 × 14 px
 * padding = 1160 px. That is past the cards' band (they end at 1015 px, `LIST_BANDS.tableFull`): the cards keep the
 * header [icon + texts][chips][action ⋮] at every width, and the table takes over from 1016 px.
 */
export const CARD_HEADER = { chip: 180, action: 120, menu: 40, gap: 8, columnGap: 32, padding: 14 };
export const CARD_HEADER_COLUMNS = 3 * (CARD_HEADER.chip + CARD_HEADER.action + CARD_HEADER.menu + 2 * CARD_HEADER.gap)
  + 2 * CARD_HEADER.columnGap + 2 * CARD_HEADER.padding;

/**
 * R-1: the wizard and the sequence by the tab's content width (`@container caltab`): three columns (stepper 240 · centre ·
 * actions clamp(240px, 30 %, 320px)) from 1000 px, the steps strip with centre and actions column from 700 px, one
 * column with the actions in a sticky bottom bar below. Window rules stay media queries: the phone (`PHONE`, under
 * 720 px, full screen, fixed bar whose actions keep 480 px centred from a 560 px bar) and the landscape phone (`SHORT`).
 */
export const WIZARD_BANDS = { threeColumns: 1000, intermediate: 700, wideBar: 560 };

/** The wizard's layout for a content width, given the window rules. */
export function wizardBand(width, { phone = false, short = false } = {}) {
  return short ? "landscape" : phone ? "phone" : width >= WIZARD_BANDS.threeColumns ? "three-columns" : width >= WIZARD_BANDS.intermediate ? "intermediate" : "single-column";
}

/** The list's band for a content width (px). */
export function listBand(width) {
  return width >= LIST_BANDS.table ? "table" : width >= LIST_BANDS.tableFull ? "table-full" : width >= LIST_BANDS.cardsRow ? "cards-row" : "cards-stacked";
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
  phone: () => matches(PHONE),
  fullScreen: () => matches(FULL_SCREEN),
  sheets: () => matches(SHEETS),
  /** The full-screen views and the sheets slide in and out, unless motion is reduced. */
  slides: (query = FULL_SCREEN) => matches(query) && !still(),
};

/**
 * Q-24: the filter and order bar shows its chips (and «Ordina per» on the right) only when the chips on one line, the
 * order control and at least `BAR_GAP` px between them fit the bar's width; otherwise its two drop-downs. Measured, not
 * a container query: the chips' width depends on their labels and counts. Each bar is measured again when its width
 * changes (a ResizeObserver per bar).
 */
export const BAR_GAP = 16;
const watched = new WeakSet();
let observer = null;

function fitBar(bar) {
  const chips = bar.querySelector(".cal-tab-chips"), compact = bar.querySelector(".cal-tab-compact");
  const room = bar.clientWidth;
  if (!chips || !compact || !room) return;
  bar.classList.add("is-chips", "is-measuring");
  // Q-27: in the table layouts the bar has no order control: only the chips count.
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
    // D-37: measured in the next frame, so a bar changing its own state never re-enters the observer's delivery
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
 * X-2: on iOS a native drop-down opens its picker over the lower half of the screen and scrolls the page to keep the
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
  const picker = (target) => target?.matches?.("select") && target.closest?.(".cal-filterbar, .cal-batch-tools, .wiz-pick-bar");
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
 * X-6: a dialog or sheet that opens puts the focus inside itself, for assistive technology and the keyboard; after a
 * tap, on its own box (no ring anywhere: WebKit draws one on a button focused by script), after a key, on its safe
 * button as before. The last input is noted once, for every panel view.
 */
let keyboardInput = false;
if (typeof window !== "undefined" && window.addEventListener && !window.__myhomeInputWatch) {
  window.__myhomeInputWatch = true;
  window.addEventListener("keydown", (event) => { if (!["Shift", "Control", "Alt", "Meta"].includes(event.key)) keyboardInput = true; }, true);
  window.addEventListener("pointerdown", () => { keyboardInput = false; }, true);
}
export function focusOnOpen(box, target) {
  if (keyboardInput && target) target.focus({ preventScroll: true });
  else box?.focus({ preventScroll: true });
}

/** D-40: the room the name of a wizard list's row keeps before its chips leave the side for under it. */
export const ROW_NAME_MIN = 200;

/**
 * D-40 (decided 8 Oct): A-01's and the sequence's rows follow the page cards' measured rule (Q-15) instead of a fixed
 * list width: the chips beside the name, side by side while the name keeps ROW_NAME_MIN px (`is-beside`), one under
 * the other while it still does (`is-beside is-stacked`), under the name otherwise. Measured again when a list's
 * width changes.
 */
/**
 * L-109: the table's actions column (CTA and ⋮) as wide as its widest row needs, 158 px at least: a label never wraps
 * («Segui sessione», «Riprendi sessione»). One width for every row, so the columns stay aligned.
 */
export function fitActions(root) {
  for (const list of root?.querySelectorAll?.(".cal-list") || []) {
    let widest = 0;
    for (const cell of list.querySelectorAll(".cal-row-actions")) {
      const parts = [...cell.children].filter((child) => child.getClientRects().length);
      if (!parts.length) continue;
      const gap = parseFloat(cell.ownerDocument.defaultView.getComputedStyle(cell).columnGap) || 0;
      widest = Math.max(widest, parts.reduce((sum, child) => sum + child.getBoundingClientRect().width, 0) + gap * (parts.length - 1));
    }
    const value = widest ? `${Math.ceil(widest)}px` : "";
    if (list.style.getPropertyValue("--cal-actions-w") !== value) {
      if (value) list.style.setProperty("--cal-actions-w", value); else list.style.removeProperty("--cal-actions-w");
    }
  }
}

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
  // D-40 (Y): the name's column also keeps its line (room, address) on one line beside the chips, never less than
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
 * L-8: the band at the top of the window that fixed or sticky bars cover (Home Assistant's toolbar, the panel's own
 * header), measured over the element's middle: the elements at that point, in the element's own tree, that are or sit
 * in a fixed or sticky box not holding the element.
 */
export function coveredTop(element) {
  const root = element?.getRootNode?.(), view = element?.ownerDocument?.defaultView;
  if (!root?.elementsFromPoint || !view) return 0;
  const box = element.getBoundingClientRect(), x = Math.max(1, Math.min(view.innerWidth - 1, box.left + box.width / 2));
  const pinned = (node) => {
    for (let current = node; current && current !== element; current = current.parentElement || current.getRootNode?.().host) {
      if (current.contains?.(element)) return null;
      if (["fixed", "sticky"].includes(view.getComputedStyle(current).position)) return current;
    }
    return null;
  };
  let covered = 0;
  for (let guard = 0; guard < 4; guard++) {
    const bar = root.elementsFromPoint(x, covered + 1).map(pinned).find(Boolean);
    const bottom = bar ? Math.round(bar.getBoundingClientRect().bottom) : 0;
    if (bottom <= covered) break;
    covered = bottom;
  }
  return covered;
}

/**
 * L-8: the wizard (or the sequence) in the page's inline layouts opens with its head at the top of what can be seen,
 * under the bars that cover the window's top; Home Assistant scrolls the page on <html>. `always`: on opening; otherwise
 * (a new step) only when the head went above the visible area. In a window shorter than the wizard the top of its
 * actions column stays in view. Nothing in the full-screen layouts (the wizard is the window there).
 */
export function revealHead(wiz, { always = false } = {}) {
  const view = wiz?.ownerDocument?.defaultView, head = wiz?.querySelector(".wiz-head");
  // Without a layout (no size: hidden, or not drawn yet) there is nothing to place.
  if (!view || !head || !wiz.isConnected || layout.fullScreen() || !head.getBoundingClientRect().height) return;
  // The box that scrolls the wizard: Home Assistant's page (<html>), or a scrolling ancestor where there is one.
  const scrollerOf = () => {
    for (let node = wiz.parentElement || wiz.getRootNode?.().host; node && node !== wiz.ownerDocument.documentElement && node !== wiz.ownerDocument.body;
      node = node.parentElement || node.getRootNode?.().host) {
      const overflow = view.getComputedStyle(node).overflowY;
      if (["auto", "scroll"].includes(overflow) && node.scrollHeight > node.clientHeight + 1) return node;
    }
    return null;
  };
  const measure = () => {
    const scroller = scrollerOf(), frame = scroller ? scroller.getBoundingClientRect() : { top: 0, bottom: view.innerHeight };
    const covered = Math.max(coveredTop(head), frame.top), top = head.getBoundingClientRect().top;
    let delta = top - covered - 8;
    const actions = wiz.querySelector(".wiz-columns > .wiz-actions");
    const room = Math.min(view.innerHeight, frame.bottom) - 96;
    if (actions && actions.getBoundingClientRect().top - delta > room) delta = actions.getBoundingClientRect().top - room;
    const box = scroller || wiz.ownerDocument.scrollingElement || wiz.ownerDocument.documentElement;
    return { scroller, covered, top, delta, left: box.scrollHeight - box.clientHeight - box.scrollTop };
  };
  // L-8b: on opening, room under the wizard for a whole window: a shorter step can never pull the page back up (the
  // page's end would otherwise clamp its scroll), whatever the steps' heights.
  const host = wiz.parentElement;
  if (always && host?.style) host.style.setProperty("--wiz-room", `${Math.ceil(view.innerHeight)}px`);
  let { scroller, covered, top, delta, left } = measure();
  if (!always && top >= covered) return;
  if (Math.abs(delta) < 1) return;
  // A page too short to bring the head up gets the missing room under the wizard (its host's `--wiz-room`); the box
  // that scrolls is looked for again (a container may only start scrolling with that room).
  if (delta > left && host?.style) {
    const room = (parseFloat(host.style.getPropertyValue("--wiz-room")) || 0) + Math.ceil(delta - left);
    host.style.setProperty("--wiz-room", `${room}px`);
    ({ scroller, delta } = measure());
  }
  (scroller || view).scrollBy?.({ top: delta, behavior: still() ? "auto" : "smooth" });
}

/**
 * U-1: in the one-column wizard (content under 700 px, a window of 720 px or more) the actions are a bar held at the
 * bottom of the window. Home Assistant scrolls the page on <html> while the panel grows with its content, so a sticky
 * bar never sticks: the bar is docked instead, `position: fixed` over the wizard's own width, whenever the wizard runs
 * past the window's bottom, and back in its place at the end of the wizard when that end is in view. The wizard keeps
 * the bar's height as padding meanwhile, so nothing moves. The stylesheet marks the layout with `--wiz-single: 1`.
 * D-02: with columns, the sequence's status and Start (`.cal-batch-sticky`, sticky at 76 px) are held the same way: moved
 * down their column by a transform so their top stays 16 px under the panel's bar (or the window's top), never past
 * the column's end. Where the sticky rule works the block is already there and nothing is added.
 * Returns the function that stops it.
 */
export function dockBar(wiz) {
  if (!wiz || typeof window === "undefined" || typeof window.addEventListener !== "function") return () => {};
  const view = wiz.ownerDocument.defaultView;
  let frame = 0;
  const update = () => {
    frame = 0;
    if (!wiz.isConnected) return;
    const bar = wiz.querySelector(":scope > .wiz-frame > .wiz-columns > .wiz-actions, :scope .wiz-columns > .wiz-actions");
    const single = view.getComputedStyle(wiz).getPropertyValue("--wiz-single").trim() === "1";
    const box = wiz.getBoundingClientRect(), height = view.innerHeight;
    const docked = wiz.classList.contains("is-docked");
    const barHeight = bar ? (docked ? bar.getBoundingClientRect().height : bar.offsetHeight) : 0;
    const dock = !!bar && single && box.height > 0 && box.bottom > height + 1 && box.top + barHeight + 80 < height;
    wiz.classList.toggle("is-docked", dock);
    if (dock) {
      wiz.style.setProperty("--dock-left", `${Math.round(box.left)}px`);
      wiz.style.setProperty("--dock-width", `${Math.round(box.width)}px`);
      wiz.style.setProperty("--dock-height", `${Math.round(barHeight)}px`);
    }
    hold(single);
  };
  const hold = (single) => {
    const block = wiz.querySelector(".cal-batch-sticky");
    if (!block) return;
    block.style.transform = "";
    if (single || view.getComputedStyle(block).position !== "sticky") return;
    const column = block.parentElement.getBoundingClientRect(), own = block.getBoundingClientRect();
    const topbar = wiz.getRootNode().querySelector?.(".topbar")?.getBoundingClientRect();
    const top = (topbar && topbar.bottom > 0 ? topbar.bottom : 0) + 16;
    const room = column.bottom - parseFloat(view.getComputedStyle(block.parentElement).paddingBottom || "0") - own.bottom;
    const shift = Math.min(top - own.top, room);
    if (shift > 0.5) block.style.transform = `translateY(${Math.round(shift)}px)`;
  };
  const schedule = () => { if (!frame) frame = (view.requestAnimationFrame || ((callback) => setTimeout(callback, 16)))(update); };
  view.addEventListener("scroll", schedule, { capture: true, passive: true });
  view.addEventListener("resize", schedule, { passive: true });
  const observer = typeof ResizeObserver === "function" ? new ResizeObserver(schedule) : null;
  observer?.observe(wiz);
  update();
  return () => {
    view.removeEventListener("scroll", schedule, { capture: true });
    view.removeEventListener("resize", schedule);
    observer?.disconnect();
    wiz.classList.remove("is-docked");
    const block = wiz.querySelector(".cal-batch-sticky");
    if (block) block.style.transform = "";
  };
}

