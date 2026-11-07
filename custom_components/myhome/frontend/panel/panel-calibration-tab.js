/**
 * Hold the page still: `overflow: hidden` on `html`, `body` and every scrolling ancestor of `host` (through shadow roots);
 * the returned function puts their own values and scroll back (iOS may move the scroll when overflow changes).
 */
function holdScroll(host) {
  const held = [];
  const hold = (element) => { if (element?.style) { held.push([element, element.style.overflow, element.scrollTop]); element.style.overflow = "hidden"; } };
  hold(document.documentElement); hold(document.body);
  for (let node = host; node; node = node.parentElement || node.getRootNode?.().host) {
    if (node === document.documentElement || node === document.body) continue;
    if (/(auto|scroll)/.test(node.ownerDocument.defaultView.getComputedStyle(node).overflowY)) hold(node);
  }
  return () => { for (const [element, value, top] of held.reverse()) { element.style.overflow = value; if (element.scrollTop !== top) element.scrollTop = top; } };
}


/** WHO 2 "Calibration" view: what each timed cover uses, its measurement state and the ways to measure it. */
const asset = (name) => { const url = new URL(name, import.meta.url); url.search = new URL(import.meta.url).search; return url.href; };
const [{ escapeHtml: esc }, model, text, { CoverCalibration, checkedCalibrationClient }, { CalibrationWizard }, { ProfileCatalogueEditor }, { morph }, { layout, SHEETS, PHONE, SHORT, listBand, contentWidth, fitBars, fitRows, fitActions, dockBar, keepScrollOnPick, focusOnOpen, revealHead }, { cancelSession, CHECK_THRESHOLD_CM }] = await Promise.all([
  import(asset("panel-dom.js")), import(asset("panel-model.js")), import(asset("panel-calibration-text.js")),
  import(asset("panel-cover-calibration.js")), import(asset("panel-calibration-wizard.js")), import(asset("panel-profile-catalogue-editor.js")),
  import(asset("panel-calibration-morph.js")), import(asset("panel-calibration-layout.js")), import(asset("panel-calibration-gateway.js")),
]);
const { fill, rich, number, dateText, names, followedBy } = text;

const MEASURED = ["guided", "automatic"];
/** L-100: a list after a colon, commas only (no «and»: one name or many read the same); a closing number stays with its name (D-41). */
const commaList = (list) => list.map((name) => String(name).replace(/ (\d+)$/, "\u00a0$1")).join(", ");
/**
 * L-93: the action of an open session says what it will do. Home Assistant gives it back to the tab that owns it
 * (after a reload, or once the wizard was closed with the session left running): «Riprendi sessione»; any other tab
 * or device joins as a reader, with Take control: «Segui sessione».
 */
const sessionLabel = (session) => (session?.client_owner ? "wiz_T580" : "wiz_T579");
export const FILTERS = ["all", "to_measure", "calibrated", "profile"];
// Covers read in turn to find the gateway's session when a read fails.
const SESSION_PROBES = 3;
/** L-107: a burst of session transitions within this time makes one read of the list. */
export const SESSION_READ_MS = 250;
export const SORT_KEYS = ["cover", "status", "profile"];
/** The Assign profile dialog's "No profile" entry (a profile id is never this). */
const NO_PROFILE = "__no_profile__";
export const DEFAULT_SORT = Object.freeze({ key: "cover", dir: "asc" });
export const SORT_STORAGE_KEY = "myhome-panel-calibration-sort-v1";
// Status from what needs attention first; a running cover is always on top, whatever the order.
// L-73: to calibrate, calibrated (measured times, no check kept), verified (a check kept at the last save).
const STATUS_RANK = { to_measure: 0, calibrated: 1, verified: 2 };

function savedSort() {
  try {
    const saved = JSON.parse(window.localStorage.getItem(SORT_STORAGE_KEY));
    if (SORT_KEYS.includes(saved?.key) && ["asc", "desc"].includes(saved?.dir)) return { key: saved.key, dir: saved.dir };
  } catch { /* The default order applies when browser storage is blocked. */ }
  return { ...DEFAULT_SORT };
}

/** What a cover uses, read from the backend view; nothing is estimated in the browser. */
export function coverStatus(cover) {
  const effective = cover.effective || {};
  const evidence = ["opening", "closing"].map((direction) => effective[direction]?.provenance).filter(Boolean);
  const measured = evidence.filter((item) => MEASURED.includes(item.source));
  const rolls = effective.opening_roll?.value != null && effective.closing_roll?.value != null;
  const dates = [...measured, ...["slat_time_s", "opening_roll", "closing_roll"].map((key) => effective[key]?.provenance)]
    .map((item) => item?.recorded_at).filter((value) => value && !Number.isNaN(Date.parse(value))).sort();
  const latest = [...measured].sort((a, b) => String(b.recorded_at).localeCompare(String(a.recorded_at)))[0] || null;
  const check = Number.isInteger(cover.check_cm) ? cover.check_cm : null;
  return {
    status: !measured.length ? "to_measure" : check != null ? "verified" : "calibrated",
    // What was measured, for the «Metodo» line and the wizard's «already calibrated (…)»: times, or times and roll.
    detail: rolls ? "rolls" : "times", check,
    rolls, last: dates.at(-1) || null, method: latest ? (latest.inherited ? "inherited" : latest.source) : null,
    origin: latest?.inherited ? latest.origin_name || latest.origin_entity_id : null,
  };
}

/**
 * L-73: the status chip of a cover: «Da calibrare» (grey), «Calibrata» (accent) or «Verificata · N cm», green within the
 * check's threshold and yellow beyond.
 */
export function statusBadge(item, t) {
  const tone = item.status === "verified" ? ` data-tone="${item.check > CHECK_THRESHOLD_CM ? "warning" : "success"}"` : "";
  const label = item.status === "verified" ? fill(t("calTabStatus_verified"), { cm: item.check }) : t(`calTabStatus_${item.status}`);
  return `<span class="badge cal-badge" data-status="${item.status}"${tone}>${esc(label)}</span>`;
}

/** Covers this view can measure: timed covers, never those with a position sensor (no resolved timings). */
export function timedCovers(data) {
  return (data?.covers || []).filter((cover) => cover.effective && !cover.advanced);
}

export class CalibrationTab {
  constructor() {
    this._generation = 0; this._rows = new Map(); this._unsubs = []; this._expanded = new Set(); this._page = null;
    this._filter = "all"; this._sort = savedSort(); this._menu = null; this._mode = { kind: "list" }; this._hosts = new WeakSet();
    this._calibration = new CoverCalibration();
    this._wizard = new CalibrationWizard();
  }

  /**
   * While the wizard or the sequence view is shown, the page does not bounce or pull to refresh at its top (Safari's
   * pull-to-refresh reloads Home Assistant and drops the wizard without asking: iOS never shows `beforeunload`). The
   * previous values come back as soon as the list is shown again.
   */
  get _mode() { return this.__mode; }
  set _mode(mode) {
    this.__mode = mode;
    // The phone's details page belongs to the list: a wizard, a sequence or a session closes it.
    // The details page it closed comes back when the list does (`_backToList`).
    if (mode?.kind !== "list") { if (this._page) this._pageReturn = this._page; this._page = null; }
    // L-8: the room the wizard or the sequence added under itself to reach the window's top belongs to them.
    else this._context?.host.style.removeProperty("--wiz-room");
    this._syncHold();
  }

  /**
   * The views over the page hold it: the wizard and the sequence (always no pull-to-refresh; held still when full
   * screen, M4-4) and the phone's details page (P-5: held still too, only its body scrolls).
   */
  _syncHold() {
    const mode = this.__mode;
    const full = mode?.kind === "wizard" || mode?.kind === "batch", page = mode?.kind === "list" && !!this._page;
    const hold = full || page, roots = [document.documentElement, document.body].filter(Boolean);
    if (hold && !this._overscroll) {
      this._overscroll = roots.map((element) => [element, element.style.overscrollBehaviorY]);
      for (const element of roots) element.style.overscrollBehaviorY = "none";
    } else if (!hold && this._overscroll) {
      for (const [element, value] of this._overscroll) element.style.overscrollBehaviorY = value;
      this._overscroll = null;
    }
    // M4-4: the full-screen wizard (a phone, or a landscape phone: the stylesheet's rule) holds the page under it still;
    // only its content area scrolls. Elsewhere the wizard sits in the page. The details page exists only on the phone.
    const still = (full && layout.fullScreen()) || page;
    if (still && this._context?.host && !this._fullHold) this._fullHold = holdScroll(this._context.host);
    else if (!still && this._fullHold) { this._fullHold(); this._fullHold = null; }
  }

  /** L-6/L-7, D-39: what the window tells the list (rotation, phone edge, fonts); returns its teardown (R-8). */
  _watchWindow(context) {
    const off = [];
    const listen = (target, type, handler, options) => {
      if (!target?.addEventListener) return;
      target.addEventListener(type, handler, options);
      off.push(() => target.removeEventListener(type, handler, options));
    };
    // L-6/L-7: a rotation or a window that crosses the phone's edge changes the layout without always changing the
    // tab's width first (iOS settles the media queries after the resize): the list is drawn again for the new mode
    // (no card open in place on the phone, no page outside it), and every card is measured again, also once the
    // rotation has settled.
    const view = context.host.ownerDocument?.defaultView;
    const relayout = () => {
      if (this._mode.kind !== "list") return;
      if (layout.phone() !== this._wasPhone) this._render(); else { this._band(); this._alignCards(); }
    };
    const frame = globalThis.requestAnimationFrame || ((callback) => setTimeout(callback, 16));
    for (const query of [PHONE, SHORT]) {
      const list = typeof view?.matchMedia === "function" ? view.matchMedia(query) : null;
      listen(list, "change", () => frame(relayout));
    }
    // L-7b: after a rotation WebKit may keep the cards' previous geometry (container queries and measured rules not
    // evaluated again) until a touch changes a state on them. Once the rotation has settled (two frames and 150 ms,
    // again at 400 ms), the list is laid out afresh: see `_settleLayout`.
    let settle = 0;
    const settled = () => {
      clearTimeout(settle);
      frame(() => frame(() => { settle = setTimeout(() => { this._settleLayout(); setTimeout(() => this._settleLayout(), 250); }, 150); }));
    };
    listen(view, "orientationchange", () => { frame(relayout); settled(); });
    listen(view?.visualViewport, "resize", settled);
    // D-39: the measured card layouts (chips, Q-14 edge) are measured again once the fonts have loaded.
    const fonts = context.host.ownerDocument?.fonts;
    listen(fonts, "loadingdone", () => this._alignCards());
    fonts?.ready?.then(() => this._alignCards());
    return () => { clearTimeout(settle); for (const remove of off.splice(0)) remove(); };
  }

  /** Replace the wizard (tests use a faster preview); `null` leaves only sessions and groups. */
  useWizard(wizard) { this._wizard?.close(); this._wizard = wizard; }

  clear() {
    this._context?.setBeforeLeave?.(null);
    // R-8: one teardown for what the tab hung on the window and on a docked view.
    this._windowOff?.(); this._windowOff = null;
    // Review nit: a wizard asked for before the tab was cleared never opens later by surprise.
    this._pendingWizard = null;
    this._undockView?.(); this._undockView = null; this._dockedView = null;
    this._catalogue?.close();
    this._generation++;
    this._closeDialog();
    for (const unsubscribe of this._unsubs.splice(0)) Promise.resolve(unsubscribe()).catch(() => {});
    this._calibration.close();
    this._wizard?.close();
    this._rows.clear();
    this._page = null;
    this._pageReturn = null;
    this._mode = { kind: "list" };
    this._batch = null;
    this._menu = null;
    this._context = null;
    this._key = null;
  }

  show(context) {
    const key = JSON.stringify(context.gateways.map((entry) => entry.entry_id).sort());
    const changed = key !== this._key || context.hass.connection !== this._context?.hass.connection;
    // A wizard asked for just before this showing (the profile dialog's «New guided measurement») survives the
    // showing's own reset when its gateway is among the ones shown; any other clear forgets it.
    const pending = this._pendingWizard;
    if (changed) this.clear();
    if (pending && context.gateways.some((entry) => entry.entry_id === pending.entryId)) this._pendingWizard = pending;
    if (!changed && this._context && this._context.host !== context.host) {
      // A rebuilt shell gives a new element: whatever the old one showed is closed, as on any exit.
      this._calibration.close();
      this._wizard?.close();
      this._mode = { kind: "list" };
    }
    this._context = context;
    this._key = key;
    this._theme();
    // Leaving the tab with a live calibration open asks first (the wizard's "Calibration in progress").
    context.setBeforeLeave?.(() => (this._mode.kind === "wizard" ? this._wizard?.beforeLeave?.() : null) || null);
    if (!this._hosts.has(context.host)) {
      this._hosts.add(context.host);
      context.host.addEventListener("click", (event) => this._click(event));
      context.host.addEventListener("keydown", (event) => this._keydown(event));
      context.host.addEventListener("change", (event) => this._change(event));
      // X-2: a filter's or an order's native drop-down never leaves the page scrolled (iOS's picker).
      keepScrollOnPick(context.host);
      // Q-3: a new width may change the list's band.
      if (typeof ResizeObserver === "function") {
        const host = context.host;
        let width = 0;
        // D-37: the band and the cards are set in the next frame, outside the observer's delivery.
        new ResizeObserver(([entry]) => {
          const now = Math.round(entry.contentRect.width);
          if (now === width) return;
          width = now;
          (globalThis.requestAnimationFrame || ((callback) => setTimeout(callback, 16)))(() => {
            if (this._mode.kind === "list" && layout.phone() !== this._wasPhone) { this._render(); return; }
            this._band(); this._alignCards();
          });
        }).observe(host);
      }
    }
    // R-8: the window's listeners once per showing, removed by `clear()` (not once per host: a rebuilt shell gave a new
    // host, and every one added them again).
    if (!this._windowOff) this._windowOff = this._watchWindow(context);
    if (changed) {
      const generation = this._generation;
      for (const entry of context.gateways) this._rows.set(entry.entry_id, { loading: false, data: null, session: null, error: false, again: false });
      for (const entry of context.gateways) {
        this._subscribe(entry.entry_id, generation);
        this._read(entry.entry_id, generation);
      }
    }
    this._render();
  }

  updateHass(hass) {
    if (!this._context) return;
    this._context.hass = hass;
    this._theme();
    this._wizard?.updateHass?.(hass);
  }

  /**
   * The tab's notes and chips follow Home Assistant's dark mode like the wizard does, on every update (L-62: set only
   * when the tab was shown, a list could keep the light rules under a dark Home Assistant: white warning text, a faint
   * «In corso» chip); without theme data, the system's preference. D-13: the tab's dialogs follow it too.
   */
  _theme() {
    const context = this._context;
    if (!context?.host) return;
    const darkMode = context.hass?.themes?.darkMode;
    const theme = (typeof darkMode === "boolean" ? darkMode : !!globalThis.matchMedia?.("(prefers-color-scheme: dark)")?.matches) ? "dark" : "light";
    if (context.host.dataset.theme !== theme) context.host.dataset.theme = theme;
    if (context.dialogHost && context.dialogHost.dataset.theme !== theme) context.dialogHost.dataset.theme = theme;
  }

  /** Session state is transient (no revision): the panel's periodic refresh reads it again. */
  refresh() {
    if (!this._context || document.hidden) return;
    for (const entryId of this._rows.keys()) this._read(entryId, this._generation);
  }

  _current(generation) { return generation === this._generation && this._context?.host.isConnected; }

  async _subscribe(entryId, generation) {
    try {
      const unsubscribe = await this._context.hass.connection.subscribeMessage((event) => {
        if (!this._current(generation) || event.entry_id !== entryId) return;
        const row = this._rows.get(entryId);
        if (event.kind === "removed") { row.removed = true; row.data = null; this._render(); return; }
        // L-107: a session started, moved on or ended on this gateway (any tab or device): the list reads it again at
        // once (a burst of transitions makes one read), instead of at the next 15 s refresh. Only while the list shows:
        // the way back to it reads everything again anyway.
        if (event.kind === "session") {
          clearTimeout(row.sessionTimer);
          // L-113: an open wizard left behind by another device (its session cancelled there) learns of the new one.
          if (this._mode.kind === "wizard") {
            row.sessionTimer = setTimeout(() => { if (this._current(generation)) this._wizard?.gatewaySession?.(entryId); }, SESSION_READ_MS);
            return;
          }
          if (this._mode.kind !== "list") return;
          row.sessionTimer = setTimeout(() => this._read(entryId, generation), SESSION_READ_MS);
          return;
        }
        if (Number.isInteger(event.revision) && event.revision > (row.data?.revision ?? -1)) this._read(entryId, generation);
      }, { type: "myhome/cover_profiles/subscribe", entry_id: entryId });
      if (!this._current(generation)) { await unsubscribe(); return; }
      this._unsubs.push(unsubscribe);
    } catch { /* The panel's periodic refresh reconciles missed notifications. */ }
  }

  async _read(entryId, generation) {
    if (!this._current(generation) || document.hidden) return;
    const row = this._rows.get(entryId);
    if (!row || row.removed) return;
    if (row.loading) { row.again = true; return; }
    row.loading = true;
    const { hass } = this._context;
    // U-10: a read begun before a discard (`_discarded`) may still carry the cancelled session: it is read again.
    const epoch = row.epoch || 0, stale = () => (row.epoch || 0) !== epoch && (row.again = true);
    try {
      const data = await hass.callWS({ type: "myhome/cover_profiles/overview", entry_id: entryId });
      if (!this._current(generation) || row.removed || stale()) return;
      if (data.entry_id !== entryId) throw new Error("Wrong gateway");
      if (!row.data || data.revision >= row.data.revision) row.data = data;
      row.error = false;
      // The overview carries no session: a cover read gives the gateway's session (one at a time). If a read
      // fails the next cover is tried; the overview still says when the gateway is reserved (calibration_busy).
      row.reserved = row.data.covers.some((cover) => cover.assignment_reason === "calibration_busy");
      // The row keeps what it showed until the reads are over: a redraw meanwhile shows no transient «busy».
      let session = null, known = !timedCovers(row.data).length;
      for (const probe of timedCovers(row.data).slice(0, SESSION_PROBES)) {
        try {
          // L-93: with this tab's identity the session says whether this tab owns it (its action then resumes it).
          const client = await checkedCalibrationClient().catch(() => null);
          const snapshot = await hass.callWS({ type: "myhome/cover_profiles/read", entry_id: entryId, entity_id: probe.entity_id,
            ...(client ? { client_id: client } : {}) });
          if (!this._current(generation) || stale()) return;
          session = snapshot.calibration && !["saved", "cancelled"].includes(snapshot.calibration.phase) ? snapshot.calibration : null;
          known = true;
          break;
        } catch { /* Try the next cover; the session is read again at the next refresh. */ }
      }
      row.session = session;
      row.sessionKnown = known;
    } catch {
      if (this._current(generation)) row.error = true;
    } finally {
      if (this._current(generation)) {
        row.loading = false;
        this._render();
        if (row.again) { row.again = false; this._read(entryId, generation); }
      }
    }
  }

  /** U-10: «Esci senza salvare» confirmed by Home Assistant: the gateway's session is gone at once, then read again. */
  _discarded(entryId) {
    const row = this._rows.get(entryId);
    if (!row || !this._context) return;
    row.epoch = (row.epoch || 0) + 1;
    row.session = null;
    row.sessionKnown = true;
    this._render();
    this._read(entryId, this._generation);
  }

  /** Every timed cover of the scope, with its gateway and inventory entity. */
  _covers() {
    const { gateways, inventory, hass } = this._context;
    return gateways.flatMap((entry) => {
      const row = this._rows.get(entry.entry_id);
      if (!row?.data) return [];
      return timedCovers(row.data).map((cover) => {
        const entity = inventory.entities.find((item) => item.entity_id === cover.entity_id && item.entry_id === entry.entry_id);
        const profile = row.data.profiles.find((item) => item.id === cover.profile_id) || null;
        const areaId = entity ? model.effectiveArea(entity, inventory.devices) : "";
        return { entry, cover, entity, profile, revision: row.data.revision, session: row.session,
          name: entity ? model.entityName(entity, hass) : cover.name, area: inventory.areas.find((area) => area.id === areaId)?.name || "",
          ...coverStatus(cover) };
      });
    }).sort((a, b) => a.name.localeCompare(b.name));
  }

  /** The rows in the chosen order: running covers first, then by the column; ties by name, then room. */
  _sorted(items, language) {
    const collator = new Intl.Collator(language, { sensitivity: "base", numeric: true });
    const { key, dir } = this._sort, sign = dir === "desc" ? -1 : 1;
    const byName = (a, b) => collator.compare(a.name, b.name) || collator.compare(a.area, b.area);
    const compare = (a, b) => {
      const running = Number(this._running(b)) - Number(this._running(a));
      if (running) return running;
      if (key === "status") return sign * ((STATUS_RANK[a.status] ?? 0) - (STATUS_RANK[b.status] ?? 0)) || byName(a, b);
      if (key === "profile") {
        // Covers without a profile stay at the bottom in both orders.
        if (!a.profile !== !b.profile) return a.profile ? -1 : 1;
        return (a.profile ? sign * collator.compare(a.profile.name, b.profile.name) : 0) || byName(a, b);
      }
      return sign * byName(a, b);
    };
    return [...items].sort(compare);
  }

  /** Same column: ascending, then descending, then back to the default order; another column starts ascending. */
  _setSort(key, dir) {
    if (!SORT_KEYS.includes(key)) return;
    if (dir) this._sort = { key, dir };
    else if (this._sort.key !== key) this._sort = { key, dir: "asc" };
    else if (this._sort.dir === "asc") this._sort = { key, dir: "desc" };
    else this._sort = { ...DEFAULT_SORT };
    try { window.localStorage.setItem(SORT_STORAGE_KEY, JSON.stringify(this._sort)); } catch { /* Kept for this visit only. */ }
    this._render();
  }

  _change(event) {
    if (this._mode.kind === "batch") {
      const input = event.target.closest?.(".cal-batch-row input[type=checkbox]");
      if (input) { this._batchToggle(input.value, input.checked); return; }
      if (event.target.matches?.('select[data-batch="gateway"]')) { this._loadBatch(event.target.value); return; }
      // M4-2: «Select ⌄» is a native menu of actions: the choice is applied, then the placeholder comes back.
      if (event.target.matches?.("select[data-batch-pick]")) {
        const pick = event.target.value;
        event.target.value = "";
        event.target.blur();
        if (pick) this._batchPick(pick);
        return;
      }
    }
    // M4-3: on a touch screen a native drop-down keeps the focus (and its ring) after the choice: let it go.
    const touch = typeof matchMedia === "function" && matchMedia("(hover: none)").matches;
    if (touch && event.target.matches?.("select[data-filter], select[data-sort]")) event.target.blur();
    if (event.target.matches?.("select[data-filter]") && this._mode.kind === "list") { this._filter = event.target.value; this._render(); return; }
    const select = event.target.closest?.("select[data-sort]");
    if (!select || !["list", "batch"].includes(this._mode.kind)) return;
    const [key, dir] = select.value.split(":");
    this._setSort(key, dir === "desc" ? "desc" : "asc");
  }

  /**
   * P-3: the one filter and order bar of the Calibration list, A-01 and the sequence. Two states, chosen by the bar's own
   * width (`@container calbar`, 640 px: a 680 px column less its padding): wide, the chips on the left (filters, or the
   * sequence's selection shortcuts) and «Ordina per» as a compact drop-down on the right; narrow, two native drop-downs at 50 % (the chips' drop-down and
   * the order). The order is the same `<select>` in both states.
   */
  _filterBar({ chips, chipsClass = "", label = "", select, compactClass = "", sortAttribute = "data-sort", extra = "", count = "" }) {
    const { t } = this._context;
    // Q-10: the counts («12 tapparelle · 7 calibrate · 5 da calibrare») only above the two drop-downs; the chips carry them.
    return `<div class="cal-filterbar${extra}">${count ? `<p class="muted cal-tab-summary" role="status">${esc(count)}</p>` : ""}<div class="cal-filterbar-row">
          <div class="cal-tab-chips${chipsClass}" role="group" aria-label="${esc(t("calTabFilterLabel"))}">${label ? `<span class="cal-filterbar-label">${esc(label)}</span>` : ""}${chips}</div>
          <div class="cal-tab-compact${compactClass}">${select}${this._compactSort(sortAttribute)}</div></div></div>`;
  }

  /** «12 tapparelle · 7 calibrate · 5 da calibrare»: the counts above the bar's drop-downs (Q-10, R-6). */
  _countLine(counts) {
    const { t } = this._context;
    return [["calTabCountCovers", counts.all], ["calTabCountCalibrated", counts.calibrated], ["calTabCountToMeasure", counts.to_measure]]
      .map(([key, count]) => fill(t(count === 1 ? `${key}_one` : key), { n: count })).join(" · ");
  }

  /** The filter drop-down of the narrow bar (T-01c M): the four filters with their counts. */
  _filterSelect(counts, current, attribute = "data-filter") {
    const { t } = this._context;
    return `<label class="cal-compact-sort cal-compact-filter"><span>${esc(t("wiz_T474"))}</span><select ${attribute} aria-label="${esc(t("calTabFilterLabel"))}">${FILTERS.map((filter) =>
      `<option value="${filter}" ${current === filter ? "selected" : ""}>${esc(t(`calTabFilter_${filter}`))} (${counts[filter]})</option>`).join("")}</select></label>`;
  }

  /** «Ordina: Tapparella ↑ ⌄» (narrow) or «Ordina per: Tapparella ↑ ⌄» (wide): the order control of every list. */
  _compactSort(attribute = "data-sort") {
    const { t } = this._context;
    const column = (key) => t({ cover: "calTabColCover", status: "calTabColStatus", profile: "calTabColProfile" }[key]);
    const options = SORT_KEYS.flatMap((key) => ["asc", "desc"].map((dir) =>
      `<option value="${key}:${dir}" ${this._sort.key === key && this._sort.dir === dir ? "selected" : ""}>${esc(column(key))} ${dir === "asc" ? "↑" : "↓"}</option>`)).join("");
    // U-11: as a chip the drop-down covers the whole chip, invisible; the label and the choice are drawn under it.
    const current = `${column(this._sort.key)} ${this._sort.dir === "asc" ? "↑" : "↓"}`;
    return `<label class="cal-compact-sort cal-filterbar-sort"><span class="cal-sort-short">${esc(t("wiz_T450"))}</span><span class="cal-sort-long">${esc(t("wiz_T379"))}:</span><span class="cal-sort-value" aria-hidden="true">${esc(current)}</span><select ${attribute}="order" aria-label="${esc(t("wiz_T379"))}">${options}</select></label>`;
  }

  /** The header row of the Calibration table: Cover, Status and Profile sort it (the wizard's lists are cards). */
  _sortHeads(lead = "") {
    const { t } = this._context, trigger = (key) => `data-action="cal-tab-sort" data-id="${key}"`;
    return `<div class="cal-list-head" role="group" aria-label="${esc(t("wiz_T379"))}"><span class="cal-head-lead">${lead}</span>${this._sortHead("cover", t("calTabColCover"), trigger)}${this._sortHead("status", t("calTabColStatus"), trigger)}${this._sortHead("profile", t("calTabColProfile"), trigger)}<span></span></div>`;
  }

  /** A column header: a button that sorts by it, with the arrow of the current order. */
  _sortHead(key, label, trigger) {
    const { t } = this._context;
    const active = this._sort.key === key, dir = this._sort.dir;
    return `<span class="cal-sort-cell"><button type="button" class="cal-sort" ${trigger(key)} aria-pressed="${active}">${esc(label)}${active
      ? `<ha-icon icon="mdi:arrow-${dir === "asc" ? "up" : "down"}" aria-hidden="true"></ha-icon><span class="visually-hidden">, ${esc(t(dir === "asc" ? "wiz_T381" : "wiz_T382"))}</span>` : ""}</button></span>`;
  }

  /**
   * A-01's list: the sequence view's rows (name, room, bus address, status, profile) with one radio each, the list's
   * filter chips and its shared order. The wizard draws it and owns the choice; its controls carry `data-wiz`.
   * The chosen row opens and shows `notes` (the "already calibrated" note, the profile it follows).
   */
  _pickerMarkup({ selected = "", filter = "all", notes = [] } = {}) {
    const { t, hass, gateways } = this._context, language = hass.language || "en";
    const all = this._covers();
    const counts = { all: all.length, to_measure: all.filter((item) => item.status === "to_measure").length,
      calibrated: all.filter((item) => item.status !== "to_measure").length, profile: all.filter((item) => item.profile).length };
    const visible = this._sorted(all, language).filter((item) => filter === "all" || (filter === "to_measure" ? item.status === "to_measure"
      : filter === "calibrated" ? item.status !== "to_measure" : !!item.profile));
    const profile = (item) => item.profile ? `${this._profileChip(item.profile.name)}`
      : `<span class="muted" aria-label="${esc(t("calTabProfileNone"))}">—</span>`;
    const rows = visible.map((item) => {
      const key = JSON.stringify([item.entry.entry_id, item.cover.entity_id]), chosen = key === selected;
      return `<label class="cal-batch-row wiz-pick-row${chosen ? " is-selected" : ""}" data-entity="${esc(item.cover.entity_id)}">
        <span class="cal-batch-lead"><input type="radio" name="cover" value="${esc(key)}" ${chosen ? "checked" : ""}>
          <span class="cal-row-icon" aria-hidden="true"><ha-icon icon="mdi:window-shutter"></ha-icon></span></span>
        <span class="cal-row-name"><strong>${esc(item.name)}</strong><span class="muted">${this._subline([item.area, gateways.length > 1 ? item.entry.title : ""], item.entity)}</span></span>
        <span class="cal-batch-chips"><span class="cal-batch-status">${statusBadge(item, t)}</span>
        <span class="cal-batch-profile">${profile(item)}</span></span>
        ${chosen && notes.length ? `<span class="wiz-pick-notes">${notes.map((note) => `<span class="cal-note${note.tone === "warning" ? " is-warning" : ""}">${esc(note.text)}</span>`).join("")}</span>` : ""}</label>`;
    }).join("");
    return `${this._filterBar({ chipsClass: " wiz-pick-chips", sortAttribute: "data-pick-sort", extra: " wiz-pick-bar", count: this._countLine(counts),
        chips: FILTERS.map((id) => `<button type="button" class="cal-chip" data-wiz="pick-filter:${id}" aria-pressed="${filter === id}">${esc(t(`calTabFilter_${id}`))} <span class="count">${counts[id]}</span></button>`).join(""),
        select: this._filterSelect(counts, filter, "data-pick-filter") })}
      <div class="cal-list cal-batch-list wiz-pick-list">
        <div class="cal-batch-rows" role="radiogroup" aria-label="${esc(t("wiz_T005"))}">${rows || `<p class="muted wiz-pick-empty">${esc(t("noResults"))}</p>`}</div></div>`;
  }

  /** In the chevron column: close every visible row when any is open, otherwise open them all (the filter applies). */
  _expandAll(visible) {
    const { t } = this._context;
    const keys = visible.map((item) => JSON.stringify([item.entry.entry_id, item.cover.entity_id]));
    const all = keys.some((key) => this._expanded.has(key));
    const label = t(all ? "wiz_T425" : "wiz_T424");
    return `<button type="button" class="icon-button cal-expand-all" data-action="cal-tab-expand-all" data-id="${all ? "collapse" : "expand"}" aria-label="${esc(label)}" title="${esc(label)}"><ha-icon icon="mdi:unfold-${all ? "less" : "more"}-horizontal" aria-hidden="true"></ha-icon></button>`;
  }

  _running(item) {
    const session = item.session;
    return !!session && (session.entity_id === item.cover.entity_id || (session.targets || []).some((target) => target.entity_id === item.cover.entity_id));
  }

  _matches(item) {
    const { filters, inventory } = this._context;
    if (filters.category && filters.category !== "cover") return false;
    if (filters.area) {
      const area = item.entity ? model.effectiveArea(item.entity, inventory.devices) : "";
      if (filters.area === "__none__" ? !!area : area !== filters.area) return false;
    }
    const terms = filters.query.trim().toLocaleLowerCase().split(/\s+/).filter(Boolean);
    const words = [item.name, item.cover.entity_id, item.profile?.name, item.entity?.address?.raw].filter(Boolean).join(" ").toLocaleLowerCase();
    return terms.every((term) => words.includes(term));
  }

  _render() {
    if (this._context?.host.isConnected && this._mode.kind === "batch") { this._renderBatch(); return; }
    if (!this._context?.host.isConnected || this._mode.kind !== "list") return;
    if (this._openPendingWizard()) return;
    this._syncLayout();
    const { host, t, hass, gateways } = this._context;
    const language = hass.language || "en";
    const loading = gateways.some((entry) => !this._rows.get(entry.entry_id)?.data && !this._rows.get(entry.entry_id)?.error && !this._rows.get(entry.entry_id)?.removed);
    const failed = gateways.filter((entry) => this._rows.get(entry.entry_id)?.error);
    const all = this._covers().filter((item) => this._matches(item));
    const counts = { all: all.length, to_measure: all.filter((item) => item.status === "to_measure").length,
      calibrated: all.filter((item) => item.status !== "to_measure").length, profile: all.filter((item) => item.profile).length };
    const visible = this._sorted(all, language).filter((item) => this._filter === "all" || (this._filter === "to_measure" ? item.status === "to_measure"
      : this._filter === "calibrated" ? item.status !== "to_measure" : !!item.profile));
    // A live session, or a gateway the overview reports reserved while its session could not be read: no new measurement.
    const rows = gateways.map((entry) => this._rows.get(entry.entry_id)).filter(Boolean);
    const busy = rows.map((row) => row.session).find(Boolean) || (rows.some((row) => row.data && row.reserved && !row.sessionKnown) ? { unknown: true } : null);
    // D-08: no overview could be read: no filter bar with zero counts, no «nothing matches the filter»; the error says it.
    const unread = !!failed.length && !this._covers().length;
    // D-09: the two head actions go off only when there is no cover at all, never because of a filter or the search.
    const none = !this._covers().length;
    const searched = !!this._context.filters.query.trim(), filtered = this._filter !== "all";
    const narrowed = filtered || searched;
    // D-09: the title names what hides the covers: the filter, the search, or both. N-07: «Come funziona» is already in
    // the head above: not a second time in the card.
    const noMatchTitle = searched && !filtered ? "wiz_T514" : searched && filtered ? "wiz_T515" : "wiz_T493";
    const clear = narrowed ? `<button type="button" class="cal-tab-clear" data-action="cal-tab-clear">${esc(t("wiz_T510"))}</button>` : "";
    let html;
    if (!loading && !failed.length && !this._covers().length) {
      // T-04: no cover estimated from its travel times on the gateway(s); the tab's title stays above.
      html = `<h3 class="cal-tab-title">${esc(t("calTabTitle"))}</h3>${this._emptyCard(t(gateways.length > 1 ? "wiz_T495" : "wiz_T492"), t("calTabEmptyBody"),
        this._howItWorks(), "cal-tab-empty")}`;
    } else {
      // The buttons line up with the introduction, not with the title above it.
      // Q-25: the title with «Come funziona ›» under it, and the two actions on the title's row (the primary first).
      // U-2: «Come funziona ›» opens its text under the whole title row, which keeps its place and its buttons.
      html = `<div class="cal-tab-top"><div class="cal-tab-head"><div class="cal-tab-heading"><h3 class="cal-tab-title">${esc(t("calTabTitle"))}</h3>
          <button type="button" class="cal-tab-how cal-disclosure-toggle" data-action="cal-tab-how" aria-expanded="${!!this._howOpen}" aria-controls="cal-tab-how-text"><span class="cal-disclosure-label">${esc(t("calTabHowItWorks"))}</span><ha-icon icon="mdi:chevron-down" aria-hidden="true"></ha-icon></button></div>
        <div class="actions cal-tab-actions">
          ${this._wizard ? `<button type="button" class="primary${loading ? " is-settling" : ""}" data-action="cal-tab-measure" ${busy || loading || none ? "disabled" : ""}><ha-icon icon="mdi:ruler" aria-hidden="true"></ha-icon><span>${esc(t("calTabMeasureOne"))}</span></button>` : ""}
          <button type="button" data-action="cal-tab-batch" ${busy || loading || none ? "disabled" : ""}><ha-icon icon="mdi:format-list-numbered" aria-hidden="true"></ha-icon><span>${esc(t("wiz_T496"))}</span></button>
        </div></div>
        <p class="muted cal-tab-how-text" id="cal-tab-how-text" ${this._howOpen ? "" : "hidden"}>${esc(t("calTabHowItWorksBody"))}</p></div>
        ${unread ? "" : this._filterBar({ extra: " cal-tab-filters", select: this._filterSelect(counts, this._filter),
          count: this._countLine(counts),
          chips: FILTERS.map((filter) => `<button type="button" class="cal-chip" data-action="cal-tab-filter" data-id="${filter}" aria-pressed="${this._filter === filter}">${esc(t(`calTabFilter_${filter}`))} <span class="count">${counts[filter]}</span></button>`).join("") })}
        ${this._done ? `<p class="notice cal-tab-done" role="status"><span>${esc(this._done)}</span></p>` : ""}
        ${busy ? `<p class="notice cal-tab-busy" role="status"><span>${esc(t(this._busyText(busy)))}</span></p>` : ""}
        ${this._filter === "to_measure" ? `<p class="cal-note cal-tab-help">${esc(t("calTabToMeasureHelp"))}</p>` : ""}
        ${failed.map((entry) => `<p class="notice error" role="alert">${esc(gateways.length > 1 ? `${entry.title}: ` : "")}${esc(t("calTabError"))}</p>`).join("")}
        ${failed.length ? `<button type="button" data-action="cal-tab-refresh">${esc(t("refresh"))}</button>` : ""}
        ${loading ? `<p class="muted" role="status">${esc(t("loading"))}</p>` : ""}
        ${visible.length || loading || unread ? "" : this._emptyCard(t(noMatchTitle), t("wiz_T494"), clear, "cal-tab-no-match")}
        ${visible.length ? `<div class="cal-list">
          ${this._sortHeads(this._expandAll(visible))}
          <div class="cal-list-rows" role="list">${visible.map((item) => this._row(item, busy, language)).join("")}</div></div>` : ""}
        ${this._pageMarkup(busy, language)}`;
    }
    // Patched in place (M3): the list, the details page and their scroll stay where they are.
    morph(host, html);
    fitBars(host);
    fitRows(host);
    fitActions(host);
    this._band();
    this._alignCards();
    this._syncHold();
  }

  /**
   * Q-22: a profile chip: at most 180 px in rows, cards and headers, its name cut with «…» and whole in the tooltip;
   * the details' «Profilo» block shows it whole.
   */
  _profileChip(name, whole = false) {
    // D-06: shown whole (the details' «Profilo»), a long name may wrap only after its «_», «-» or «.», not mid-word.
    const text = whole ? esc(name).replace(/([_.-])/g, "$1<wbr>") : esc(name);
    return `<span class="chip cal-profile-chip" title="${esc(name)}"><ha-icon icon="mdi:circle" aria-hidden="true"></ha-icon><span class="cal-chip-text">${text}</span></span>`;
  }

  /** Q-3: the list's band for the tab's content width, as the stylesheet's container rules choose it (`data-band`). */
  _band() {
    const host = this._context?.host, width = contentWidth(host);
    if (!host || !width) return;
    const band = listBand(width);
    if (host.dataset.band === band) return;
    host.dataset.band = band;
    // L-7b: nothing measured in the previous band survives into the new one.
    this._clearMeasured(host);
  }

  /** L-7b: the sizes the measured rules (Q-14, D-39) left inline on the cards, removed. */
  _clearMeasured(host) {
    for (const chips of host.querySelectorAll(".cal-row-main > .cal-row-chips")) {
      chips.style.removeProperty("flex");
      chips.style.removeProperty("min-width");
      if (!chips.getAttribute("style")) chips.removeAttribute("style");
    }
    for (const card of host.querySelectorAll(".cal-row.is-aligned, .cal-row[style]")) {
      card.classList.remove("is-aligned");
      card.style.removeProperty("--cal-col2");
      if (!card.getAttribute("style")) card.removeAttribute("style");
    }
  }

  /**
   * L-7b: after a rotation, the list laid out afresh. WebKit (iOS) evaluates the container queries of the tab
   * (`caltab`, `calblock`) again only for elements whose style is invalidated; a rotation that changes the tab's width
   * without touching those elements can leave the cards with the previous band's rules (the empty spaces) until a touch
   * (:hover/:active) invalidates the touched card. Here, in list mode: the inline sizes measured before are removed, the
   * containers are switched off and on (each change read back, so the style of every card is computed again against
   * the new width), the list is painted once on its own layer, and the band, the cards (Q-14/D-39), the filter bar
   * (Q-24) and the wizard's lists (D-40) are measured again.
   */
  _settleLayout() {
    const host = this._context?.host;
    if (!host?.isConnected || this._mode.kind !== "list") return;
    if (layout.phone() !== this._wasPhone) { this._render(); return; }
    this._clearMeasured(host);
    const containers = [host, ...host.querySelectorAll(".cal-details-grid > section")];
    for (const box of containers) box.style.setProperty("container-type", "normal");
    void host.offsetHeight;
    for (const box of containers) { box.style.removeProperty("container-type"); if (!box.getAttribute("style")) box.removeAttribute("style"); }
    void host.offsetHeight;
    const list = host.querySelector(".cal-list");
    if (list) {
      list.style.transform = "translateZ(0)";
      void list.offsetHeight;
      (globalThis.requestAnimationFrame || ((callback) => setTimeout(callback, 16)))(() => { list.style.removeProperty("transform"); if (!list.getAttribute("style")) list.removeAttribute("style"); });
    }
    this._band();
    this._alignCards();
    fitBars(host);
    fitRows(host);
    fitActions(host);
  }

  /**
   * Q-14: an open card with its blocks one under the other lays its header and its pairs on one two-column grid: the
   * chips start where the pairs' second half starts. That edge is the wider of what the header's texts and the first
   * half of the pairs need (whichever pushes more decides), but never so far right that the chips and the action no
   * longer fit. Measured, and kept in `--cal-col2` on the card (`is-aligned`).
   */
  _alignCards() {
    const host = this._context?.host;
    if (!host) return;
    this._fitChips(host);
    const active = host.dataset.band === "cards-stacked" && !layout.phone();
    for (const card of host.querySelectorAll(".cal-row.is-expanded")) {
      card.classList.remove("is-aligned");
      card.style.removeProperty("--cal-col2");
      if (!card.getAttribute("style")) card.removeAttribute("style");
      const main = card.querySelector(".cal-row-main"), name = card.querySelector(".cal-row-main .cal-row-name");
      const chips = card.querySelector(".cal-row-chips"), actions = card.querySelector(".cal-row-actions");
      if (!active || !main || !name || !chips || !actions || !main.clientWidth) continue;
      const box = main.getBoundingClientRect(), style = main.ownerDocument.defaultView.getComputedStyle(main);
      const left = box.left + (parseFloat(style.paddingLeft) || 0), right = box.right - (parseFloat(style.paddingRight) || 0);
      const width = (element) => { const range = element.ownerDocument.createRange(); range.selectNodeContents(element); return range.getBoundingClientRect().width; };
      // The texts on one line: the name block's own width without wrapping.
      card.classList.add("is-measuring");
      const text = name.getBoundingClientRect().left - left + name.scrollWidth;
      card.classList.remove("is-measuring");
      let pairs = 0;
      for (const half of card.querySelectorAll(".cal-row-details .cal-facts > .cal-facts-col:first-child")) {
        const labels = [...half.querySelectorAll("dt")].map(width), values = [...half.querySelectorAll("dd")].map(width);
        pairs = Math.max(pairs, Math.max(0, ...labels) + 26 + Math.max(0, ...values));
      }
      const widest = Math.max(0, ...[...chips.children].filter((chip) => chip.getClientRects().length).map((chip) => chip.getBoundingClientRect().width));
      const cap = right - left - actions.getBoundingClientRect().width - 16 - widest;
      // D-04: where the chips leave too little room for the pairs' first half, the card keeps its own layout rather
      // than squeeze the values against the second half.
      if (cap < pairs + 32) continue;
      const edge = Math.round(Math.min(Math.max(text, pairs) + 32, cap));
      if (edge <= 0) continue;
      card.style.setProperty("--cal-col2", `${edge}px`);
      card.classList.add("is-aligned");
    }
  }

  /**
   * D-39: in the cards (not on a phone) the status and profile chips, once stacked, leave their box as wide as the
   * widest chip, not as wide as the two side by side: the name's column takes the room and the chips end 16 px before
   * the action, as in a row whose chips sit side by side.
   */
  _fitChips(host) {
    const cards = (host.dataset.band || "").startsWith("cards") && !layout.phone();
    for (const chips of host.querySelectorAll(".cal-row-main > .cal-row-chips")) {
      chips.style.removeProperty("flex");
      chips.style.removeProperty("min-width");
      if (!chips.getAttribute("style")) chips.removeAttribute("style");
      if (!cards || !chips.clientWidth) continue;
      // The chips themselves, not their cells (a cell stretches to the box).
      const shown = [...chips.children].map((cell) => cell.firstElementChild || cell).filter((chip) => chip.getClientRects().length);
      if (!shown.length) continue;
      const box = chips.getBoundingClientRect();
      const used = Math.max(...shown.map((chip) => chip.getBoundingClientRect().right)) - box.left;
      // (A cut profile chip counts its whole name as its smallest width: the box is let go below it.)
      if (box.width - used > 1) { chips.style.flex = `0 0 ${Math.ceil(used)}px`; chips.style.minWidth = "0"; }
    }
  }

  /** «Come funziona» under an empty state (T-04, T-04b; R-7): it opens in place. */
  _howItWorks(extra = "") {
    const { t } = this._context;
    return `<details class="cal-disclosure cal-empty-how${extra}"><summary><span class="cal-disclosure-label">${esc(t("calTabHowItWorks"))}</span><ha-icon icon="mdi:chevron-down" aria-hidden="true"></ha-icon></summary><p class="muted">${esc(t("calTabHowItWorksBody"))}</p></details>`;
  }

  /** T-04 / T-04b: an empty state as a card: the shutter icon, a title, its explanation and an optional extra. */
  _emptyCard(title, body, extra = "", kind = "") {
    return `<div class="cal-empty ${kind}" role="status"><span class="cal-empty-icon" aria-hidden="true"><ha-icon icon="mdi:window-shutter"></ha-icon></span>
        <h4>${esc(title)}</h4><p class="muted">${esc(body)}</p>${extra}</div>`;
  }

  _row(item, busy, language) {
    const { t, gateways, addressDetails } = this._context;
    const key = JSON.stringify([item.entry.entry_id, item.cover.entity_id]);
    const id = `cal-row-${encodeURIComponent(key).replace(/%/g, "")}`;
    // L-6: on the phone a card never opens in place (its details are the page).
    const expanded = this._expanded.has(key) && !this._phone();
    const running = this._running(item);
    const session = running ? item.session : null;
    const badge = session ? `<span class="badge cal-badge" data-status="running">${esc(t(session.waiting_for_stop ? "calTabRunning_waiting"
      : session.phase === "paused" ? "calTabRunning_paused" : session.owner_present ?? session.attached ? "calTabRunning_present" : "calTabRunning_absent"))}</span>`
      : statusBadge(item, t);
    const profile = item.profile ? `${this._profileChip(item.profile.name)}` : `<span class="muted" aria-label="${esc(t("calTabProfileNone"))}">—</span>`;
    const last = item.last ? fill(t("calTabMeasuredOn"), { date: dateText(item.last, language) }) : "";
    const action = session
      ? (session.waiting_for_stop || session.recoverable === false ? "" : `<button type="button" class="cal-row-action" data-action="cal-tab-session" data-id="${esc(item.entry.entry_id)}">${esc(t(sessionLabel(session)))}</button>`)
      : this._wizard ? `<button type="button" class="cal-row-action" data-action="cal-tab-${item.rolls ? "refine" : "measure-cover"}" data-id="${esc(key)}" ${busy ? "disabled" : ""}>${esc(t(item.rolls ? "calTabActionRefine" : "calTabActionMeasure"))}</button>` : "";
    const menuOpen = this._menu === key;
    return `<div class="cal-row${session ? " is-running" : ""}${expanded ? " is-expanded" : ""}" role="listitem" data-entity="${esc(item.cover.entity_id)}" data-entry="${esc(item.entry.entry_id)}">
      <div class="cal-row-main">
        <button type="button" class="cal-row-toggle" data-action="cal-tab-toggle" data-id="${esc(key)}" aria-expanded="${expanded}" aria-controls="${esc(id)}">
          <span class="cal-row-lead" aria-hidden="true"><ha-icon class="cal-row-chevron" icon="mdi:chevron-right"></ha-icon><span class="cal-row-icon"><ha-icon icon="mdi:window-shutter"></ha-icon></span></span>
          <span class="cal-row-name"><strong>${esc(item.name)}</strong><span class="muted">${this._subline([this._area(item), gateways.length > 1 ? item.entry.title : ""], item.entity)}</span><span class="cal-row-last-inline">${esc(last || t("wiz_T418"))}</span></span></button>
        <span class="cal-row-chips"><span class="cal-row-status">${badge}</span>
        <span class="cal-row-profile">${profile}</span></span>
        <span class="cal-row-actions">${action}
          <span class="cal-menu-wrap"><button type="button" class="icon-button" data-action="cal-tab-menu" data-id="${esc(key)}" aria-haspopup="menu" aria-expanded="${menuOpen}" aria-label="${esc(t("calTabMore"))}: ${esc(item.name)}" title="${esc(t("calTabMore"))}"><ha-icon icon="mdi:dots-vertical" aria-hidden="true"></ha-icon></button>
          <span class="cal-menu" role="menu" ${menuOpen ? "" : "hidden"}>
            ${!session && this._wizard && item.rolls ? `<button type="button" role="menuitem" data-action="cal-tab-refine" data-id="${esc(key)}" ${busy ? "disabled" : ""}><ha-icon icon="mdi:target" aria-hidden="true"></ha-icon>${esc(t("calTabActionRefine"))}</button>` : ""}
            ${!session && this._wizard ? `<button type="button" role="menuitem" data-action="cal-tab-measure-cover" data-id="${esc(key)}" ${busy ? "disabled" : ""}><ha-icon icon="${item.status === "to_measure" ? "mdi:ruler" : "mdi:reload"}" aria-hidden="true"></ha-icon>${esc(t(item.status === "to_measure" ? "calTabActionMeasure" : "calTabActionRemeasure"))}</button>` : ""}
            <button type="button" role="menuitem" data-action="cal-tab-assign" data-id="${esc(key)}" ${session ? "disabled" : ""}><ha-icon icon="mdi:link-variant" aria-hidden="true"></ha-icon>${esc(t(item.profile ? "wiz_T427" : "calTabAssign"))}</button>
            <button type="button" role="menuitem" data-action="cal-tab-toggle" data-id="${esc(key)}"><ha-icon icon="mdi:information-outline" aria-hidden="true"></ha-icon>${esc(t("calTabDetails"))}</button>
            ${session && !session.waiting_for_stop && session.recoverable !== false ? `<button type="button" role="menuitem" class="cal-menu-danger" data-action="cal-tab-abort" data-id="${esc(item.entry.entry_id)}"><ha-icon icon="mdi:close-octagon-outline" aria-hidden="true"></ha-icon>${esc(t("wiz_T534"))}</button>` : ""}
          </span></span></span>
      </div>
      <div class="cal-row-details" id="${esc(id)}" ${expanded ? "" : "hidden"}>${expanded ? this._details(item, busy, language, addressDetails) : ""}</div>
    </div>`;
  }

  /** "Address: 86 · A: 8 · PL: 6" with the device cards' style: grey labels, dark values. */
  _address(item) {
    const address = item.entity?.address, { t } = this._context;
    if (!address?.raw) return [];
    const fields = [[t("address"), address.raw], ...(address.a != null && address.pl != null ? [["A", address.a], ["PL", address.pl]] : [])];
    return fields.map(([label, value]) => `<span class="cal-row-address-label">${esc(label)}:</span> <span class="cal-row-address-value">${esc(value)}</span>`);
  }

  /**
   * The line under a cover's name (responsive map §3.3, Q-15): room, gateway and the bus address always whole
   * («Indirizzo: 86 · A: 8 · PL: 6», never a short form, never «…»). It wraps only between units, each pair of the
   * address being one, and the «·» stays with the unit before it.
   */
  _subline(before, entity, after = []) {
    const parts = this._address({ entity }), units = before.filter(Boolean).map(esc), tail = after.filter(Boolean).map(esc);
    // D-07: the «·» is held to the word before it, even when a unit wraps between its words.
    const dot = (last) => (last ? "" : "\u00a0·");
    const address = parts.length ? [`<span class="cal-row-address">${parts.map((part, index) => `<span class="cal-row-address-part">${part}${dot(index === parts.length - 1 && !tail.length)}</span>`).join(" ")}</span>`] : [];
    const lead = units.map((unit, index) => `<span class="cal-row-unit">${unit}${dot(index === units.length - 1 && !address.length && !tail.length)}</span>`);
    return [...lead, ...address, ...tail.map((unit, index) => `<span class="cal-row-unit">${unit}${dot(index === tail.length - 1)}</span>`)].join(" ");
  }

  _area(item) { return item.area; }

  /**
   * T-05 M: on phones a cover's details are a page over the list (the list stays below it, so closing it lands at the
   * same place): name, address and ✕ at the top, then the title, the status and the three blocks one under the other.
   */
  _pageMarkup(busy, language) {
    const item = this._page ? this._item(this._page) : null;
    if (!item) { this._page = null; return ""; }
    const { t, gateways, addressDetails } = this._context;
    const session = this._running(item) ? item.session : null;
    const badge = session ? `<span class="badge cal-badge" data-status="running">${esc(t(session.waiting_for_stop ? "calTabRunning_waiting"
      : session.phase === "paused" ? "calTabRunning_paused" : session.owner_present ?? session.attached ? "calTabRunning_present" : "calTabRunning_absent"))}</span>`
      : statusBadge(item, t);
    // N-09: a cover with its session open offers «Segui sessione» / «Riprendi sessione» on its page too, as its row does.
    const open = session && !(session.waiting_for_stop || session.recoverable === false)
      ? `<div class="cal-page-session"><button type="button" class="primary" data-action="cal-tab-session" data-id="${esc(item.entry.entry_id)}">${esc(t(sessionLabel(session)))}</button></div>` : "";
    return `<div class="cal-page${session ? " is-running" : ""}" role="dialog" aria-modal="true" aria-labelledby="cal-page-name" data-entity="${esc(item.cover.entity_id)}">
        <div class="cal-page-head"><div class="cal-page-head-text"><h2 id="cal-page-name">${esc(item.name)}</h2>
          <p class="muted">${this._subline([this._area(item), gateways.length > 1 ? item.entry.title : ""], item.entity)}</p></div>
          <button type="button" class="icon-button cal-page-close" data-action="cal-tab-page-close" aria-label="${esc(t("wiz_T448"))}" title="${esc(t("wiz_T448"))}"><ha-icon icon="mdi:close" aria-hidden="true"></ha-icon></button></div>
        <div class="cal-page-body"><div class="cal-page-title"><h3>${esc(t("wiz_T447"))}</h3>${badge}</div>
          ${open}${this._details(item, busy, language, addressDetails)}</div></div>`;
  }

  /**
   * Only the phone layout (window under 720 px, not a landscape phone) opens a cover's details as a page; elsewhere the
   * row or the card opens in place, so the page never covers Home Assistant's sidebar (responsive map, D3).
   */
  _phone() { return layout.phone(); }

  /**
   * L-6: the open state follows the layout. Into the phone layout the cards open in place close (a card's details are
   * a page there, opened by a tap); out of it an open page becomes that card open in place (the page is never shown
   * outside the phone). Called before each drawing of the list.
   */
  _syncLayout() {
    const phone = this._phone();
    if (phone === this._wasPhone) return;
    const first = this._wasPhone === undefined;
    this._wasPhone = phone;
    if (first) return;
    if (phone) this._expanded.clear();
    else if (this._page) { this._expanded.add(this._page); this._page = null; }
  }

  _closePage() {
    const page = this._context.host.querySelector(".cal-page");
    if (page && layout.slides() && !page.classList.contains("is-leaving")) {
      page.classList.add("is-leaving");
      setTimeout(() => { if (this._page) this._closePage(); }, 220);
      return;
    }
    const key = this._page;
    this._page = null;
    this._render();
    [...this._context.host.querySelectorAll('.cal-row [data-action="cal-tab-toggle"].cal-row-toggle')].find((button) => button.dataset.id === key)?.focus({ preventScroll: true });
  }

  _details(item, busy, language) {
    const { t, inventory, hass } = this._context;
    const effective = item.cover.effective;
    const value = (key, digits = 1) => number(effective[key]?.value, language, digits);
    const slat = effective.slat_time_s?.value;
    const rows = [
      // Z-1: times always with one decimal.
      [t("wiz_T093"), `${text.fixed(effective.opening?.value, language, 1)} s`], [t("wiz_T094"), `${text.fixed(effective.closing?.value, language, 1)} s`],
      [t("calTabSlats"), slat == null ? "—" : slat === 0 ? t("calTabSlatsNo") : fill(t("calTabSlatsYes"), { seconds: text.fixed(slat, language, 1) })],
      [t("wiz_T041"), item.cover.travel_cm == null ? "—" : `${number(item.cover.travel_cm, language)} cm`],
      [t("wiz_T105"), item.rolls ? value("opening_roll", 2) : "—"], [t("wiz_T106"), item.rolls ? value("closing_roll", 2) : "—"],
    ];
    const followers = item.profile ? item.profile.assigned_to.filter((id) => id && id !== item.cover.entity_id).map((id) => {
      const entity = inventory.entities.find((entry) => entry.entity_id === id);
      return entity ? model.entityName(entity, hass) : id;
    }) : [];
    const scaled = ["opening", "closing"].some((direction) => effective[direction]?.origin === "profile");
    // Q-2/Q-4: label and value as compact pairs; the pairs in two halves, side by side when the block is wide.
    // D-04: a value never leaves its unit («27,1 s», «240 cm»), nor a date its year: no-break spaces.
    const keep = (text) => String(text).replace(/(\d) (s|cm)(?=$|[\s)])/g, "$1\u00a0$2");
    const pairs = (items) => `<dl class="cal-facts-col">${items.map(([label, shown]) => `<div><dt>${esc(label)}</dt><dd>${esc(keep(shown))}</dd></div>`).join("")}</dl>`;
    const list = (items) => { const half = Math.ceil(items.length / 2);
      return `<div class="cal-facts">${pairs(items.slice(0, half))}${items.length > half ? pairs(items.slice(half)) : ""}</div>`; };
    const key = JSON.stringify([item.entry.entry_id, item.cover.entity_id]);
    // Each block ends with its own action, left-aligned under its content.
    const action = (button) => button ? `<div class="cal-block-action">${button}</div>` : "";
    const refine = this._wizard && item.rolls ? `<button type="button" class="primary" data-action="cal-tab-refine" data-id="${esc(key)}" ${busy ? "disabled" : ""}><ha-icon icon="mdi:target" aria-hidden="true"></ha-icon>${esc(t("calTabActionRefine"))}</button>` : "";
    const measure = this._wizard ? `<button type="button" class="${item.rolls ? "" : "primary"}" data-action="cal-tab-measure-cover" data-id="${esc(key)}" ${busy ? "disabled" : ""}><ha-icon icon="${item.status === "to_measure" ? "mdi:ruler" : "mdi:reload"}" aria-hidden="true"></ha-icon>${esc(t(item.status === "to_measure" ? "calTabActionMeasure" : "calTabActionRemeasure"))}</button>` : "";
    // A cover that follows a profile changes it (or unlinks it); one that follows none is assigned one. L-105/L-110: off
    // while the cover has a session open (Home Assistant would refuse it: calibration_busy), shown as Refine is.
    const assign = `<button type="button" data-action="cal-tab-assign" data-id="${esc(key)}" ${this._running(item) ? "disabled" : ""}><ha-icon icon="mdi:link-variant" aria-hidden="true"></ha-icon>${esc(t(item.profile ? "wiz_T427" : "calTabAssign"))}</button>`;
    // A cover in progress is yellow throughout: its notes take the warning variant (icon and primary stay accent).
    const note = this._running(item) ? "cal-note is-warning" : "cal-note";
    return `<div class="cal-details-grid">
        <section><h4>${esc(t("calTabValues"))}</h4>${list(rows)}${this._originNote(item, language)}${action(refine)}</section>
        <section><h4>${esc(t("calTabColLast"))}</h4>${list([[t("calTabDate"), item.last ? text.dateTimeText(item.last, language).replace(/ /g, "\u00a0") : "—"],
          // L-73: the chip no longer says what was measured: the «Metodo» line does («Guidata · tempi e rullo»).
          [t("calTabMethod"), [t(`calTabMethod_${item.method || "none"}`), item.status !== "to_measure" ? t(`calTabStatusDetail_${item.detail}`) : ""].filter(Boolean).join(" · ")]])}
          ${item.profile && scaled ? `<p class="${note}">${esc(fill(t("calTabInheritedNote"), { profile: item.profile.name }))}</p>` : ""}${action(measure)}</section>
        <section><h4>${esc(t("calTabColProfile"))}</h4>${item.profile ? `<p>${this._profileChip(item.profile.name, true)}</p>
          <p>${esc(followers.length ? fill(t("calTabFollowers"), { names: commaList(followers) }) : t("calTabFollowersNone"))}</p>
          <p class="${note}">${esc(t("calTabProfileNote"))}</p>` : `<p class="muted">${esc(t("calTabProfileNone"))}</p>`}${action(assign)}</section>
      </div>`;
  }

  /** Where the times of a never measured cover come from: the integration's default or the YAML configuration. */
  _originNote(item, language) {
    const effective = item.cover.effective, origin = effective.opening?.origin;
    if (item.status !== "to_measure" || !["default", "yaml"].includes(origin) || effective.closing?.origin !== origin) return "";
    const [opening, closing] = [effective.opening.value, effective.closing.value].map((value) => number(value, language));
    // D-36: «21 / 20 s» never breaks inside.
    return `<p class="cal-note${this._running(item) ? " is-warning" : ""} cal-origin-note">${text.units(esc(fill(this._context.t(origin === "yaml" ? "wiz_T378" : "wiz_T377"),
      { seconds: opening === closing ? opening : `${opening}\u00a0/\u00a0${closing}` })))}</p>`;
  }

  _item(key) {
    const [entryId, entityId] = JSON.parse(key);
    return this._covers().find((item) => item.entry.entry_id === entryId && item.cover.entity_id === entityId);
  }

  _keydown(event) {
    if (this._page && this._menu === null && this._mode.kind === "list" && event.key === "Escape") { event.preventDefault(); this._closePage(); return; }
    if (this._menu === null || this._mode.kind !== "list") return;
    if (["ArrowDown", "ArrowUp"].includes(event.key)) {
      // Menu items move with the arrows and wrap, as in Home Assistant's own menus; a disabled one is skipped (review S2:
      // a cover in progress, or a gateway busy with another, has some).
      const items = [...this._context.host.querySelectorAll('.cal-menu:not([hidden]) [role="menuitem"]:not(:disabled)')];
      const index = items.indexOf(this._context.host.getRootNode().activeElement);
      if (!items.length) return;
      event.preventDefault();
      items[(index + (event.key === "ArrowDown" ? 1 : -1) + items.length) % items.length].focus();
      return;
    }
    if (event.key !== "Escape") return;
    const key = this._menu;
    this._menu = null;
    this._render();
    [...this._context.host.querySelectorAll('[data-action="cal-tab-menu"]')].find((button) => button.dataset.id === key)?.focus();
  }

  _click(event) {
    if (this._mode.kind === "batch") {
      // M6-1 (R7-7): a tap on the Steps sheet's backdrop closes it.
      if (event.target.classList?.contains("wiz-sheet")) {
        this._batchStepsOpen = false;
        event.target.hidden = true;
        return;
      }
      const button = event.target.closest("button[data-action]");
      if (!button) return;
      const { action, id } = button.dataset;
      if (action === "cal-tab-batch-back") this._batchBack();
      else if (action === "cal-tab-batch-steps" || action === "cal-tab-batch-steps-close") {
        this._batchStepsOpen = action === "cal-tab-batch-steps";
        this._context.host.querySelector(".cal-batch .wiz-sheet")?.toggleAttribute("hidden", !this._batchStepsOpen);
      }
      else if (action === "cal-tab-batch-pick") this._batchPick(id);
      else if (action === "cal-tab-batch-start") this._batchStart();
      else if (action === "cal-tab-sort") this._setSort(id);
      return;
    }
    if (this._mode.kind !== "list") return;
    const target = event.target.closest("button");
    // The whole row opens and closes its details, as the maintainer's device cards do; its buttons keep their own action.
    const row = !target && !event.target.closest(".cal-menu, a, input, select") && event.target.closest(".cal-row-main");
    if (row) { row.querySelector('[data-action="cal-tab-toggle"]')?.click(); return; }
    if (!target) return;
    const { action, id } = target.dataset;
    if (action !== "cal-tab-menu" && this._menu !== null) { this._menu = null; if (!action?.startsWith("cal-tab-")) this._render(); }
    if (action === "cal-tab-how") { this._howOpen = !this._howOpen; this._render(); return; }
    if (action === "cal-tab-filter") { this._filter = id; this._render(); }
    // D-09: the empty list's way out: the tab's filter back to «Tutte» and the panel's search emptied (as typed away).
    if (action === "cal-tab-clear") {
      this._filter = "all";
      const search = this._context.host.getRootNode().getElementById?.("search");
      if (search && search.value) { search.value = ""; search.dispatchEvent(new Event("input", { bubbles: true })); }
      this._render();
    }
    else if (action === "cal-tab-sort") this._setSort(id);
    else if (action === "cal-tab-expand-all") {
      const language = this._context.hass.language || "en";
      const visible = this._sorted(this._covers().filter((item) => this._matches(item)), language).filter((item) => this._filter === "all"
        || (this._filter === "to_measure" ? item.status === "to_measure" : this._filter === "calibrated" ? item.status !== "to_measure" : !!item.profile));
      for (const item of visible) {
        const key = JSON.stringify([item.entry.entry_id, item.cover.entity_id]);
        if (id === "collapse") this._expanded.delete(key); else this._expanded.add(key);
      }
      this._render();
    } else if (action === "cal-tab-toggle") {
      if (this._phone()) {
        this._page = id;
        this._render();
        this._context.host.querySelector(".cal-page-close")?.focus();
        return;
      }
      if (this._expanded.has(id)) this._expanded.delete(id); else this._expanded.add(id);
      this._render();
    } else if (action === "cal-tab-page-close") {
      this._closePage();
    } else if (action === "cal-tab-menu") {
      this._menu = this._menu === id ? null : id;
      this._render();
      if (this._menu) this._context.host.querySelector('.cal-menu:not([hidden]) [role="menuitem"]:not(:disabled)')?.focus();
    } else if (action === "cal-tab-refresh") this.refresh();
    else if (action === "cal-tab-batch") this._selectBatch();
    else if (action === "cal-tab-session") this._openSession(id);
    else if (action === "cal-tab-abort") this._abortSession(id);
    else if (action === "cal-tab-measure") this._openWizard({ start: "choose" });
    else if (action === "cal-tab-measure-cover") { const item = this._item(id); if (item) this._openWizard({ start: "path", item }); }
    else if (action === "cal-tab-refine") { const item = this._item(id); if (item) this._openWizard({ start: "refine", item }); }
    else if (action === "cal-tab-assign") { const item = this._item(id); if (item && !this._running(item)) this._assignProfile(item); }
    else if (action?.startsWith("cal-tab-")) this._render();
  }

  /** Open the wizard on a cover as soon as the list knows it (the tab may still be loading). */
  requestWizard(entryId, entityId) {
    // Never queued behind an open wizard, group or session: it would open by surprise when that closes.
    if (this._mode.kind !== "list") return;
    this._pendingWizard = { entryId, entityId };
    if (this._context) this._render();
  }

  _openPendingWizard() {
    const pending = this._pendingWizard;
    if (!pending || !this._wizard || this._mode.kind !== "list") return false;
    const row = this._rows.get(pending.entryId);
    if (!row || (!row.data && !row.error)) return false;
    this._pendingWizard = null;
    const item = this._covers().find((entry) => entry.entry.entry_id === pending.entryId && entry.cover.entity_id === pending.entityId);
    if (!item || row.session || (row.reserved && !row.sessionKnown)) return false;
    this._openWizard({ start: "path", item });
    return true;
  }

  /** Back to the list after a wizard, a group or an opened session; the backend is read again. */
  _backToList() {
    // M5-4: on a phone the full-screen view slides down before the list comes back (at once with reduced motion).
    const view = this._context?.host.querySelector(":scope > .wiz");
    if (view && layout.slides() && !view.classList.contains("is-leaving")) {
      view.classList.add("is-leaving");
      setTimeout(() => { if (view.isConnected) this._backToList(); }, 220);
      return;
    }
    this._mode = { kind: "list" };
    if (!this._context) return;
    // Back from the wizard opened on a cover's details page: the same page again (a row opened in place stays open).
    if (this._pageReturn && layout.phone()) this._page = this._pageReturn;
    this._pageReturn = null;
    // L-16: a cover's details asked from the wizard open now, in the list's own way.
    const show = this._showAfter;
    this._showAfter = null;
    if (show) { if (this._phone()) this._page = show; else this._expanded.add(show); }
    this._context.host.innerHTML = "";
    this._render();
    if (show && !this._phone()) [...this._context.host.querySelectorAll(".cal-row")].find((row) => row.dataset.entity === JSON.parse(show)[1])?.scrollIntoView?.({ block: "start" });
    this._focusOpener();
    this.refresh();
  }

  /** R-10: back on the list, the focus returns to the control that opened the wizard (a menu item: its row's ⋮). */
  _focusOpener() {
    const opener = this._opener, host = this._context?.host;
    this._opener = null;
    if (!opener || !host) return;
    const match = (action) => [...host.querySelectorAll(`[data-action="${action}"]`)].find((element) => (element.dataset.id ?? null) === opener.id
      && !element.closest("[hidden]"));
    (match(opener.action) || (opener.id != null ? match("cal-tab-menu") : null))?.focus?.({ preventScroll: true });
  }

  /** L-16: the cover's details from the wizard: its row opened in place, or its page on a phone (never the maintainer's dialog). */
  _showCover(entityId) {
    const item = this._covers().find((entry) => entry.cover.entity_id === entityId);
    if (!item) return;
    const key = JSON.stringify([item.entry.entry_id, item.cover.entity_id]);
    if (this._mode.kind !== "list") { this._showAfter = key; return; }
    if (this._phone()) this._page = key; else this._expanded.add(key);
    this._render();
  }

  _openWizard(options) {
    if (!this._wizard || !this._context) return;
    // R-10: what opened the wizard, to give it the focus back when the list returns.
    const opener = this._context.host.getRootNode?.()?.activeElement;
    this._opener = opener?.dataset?.action ? { action: opener.dataset.action, id: opener.dataset.id ?? null } : null;
    this._done = "";
    this._mode = { kind: "wizard" };
    const { host, hass, t } = this._context;
    // The covers as they are at each step: after a save, fine-tuning (C-01) shows the values just written.
    this._wizard.open({ host, hass, t, covers: () => this._covers(), data: (entryId) => this._rows.get(entryId)?.data, ...options,
      // A-01's list, drawn as the tab's lists are, in the list's shared order.
      picker: { markup: (state) => this._pickerMarkup(state), sort: (key, dir) => this._setSort(key, dir), order: () => ({ ...this._sort }) },
      // L-16: «Open the cover's details» is ours: the row opened in place (the page on a phone), once the list is back.
      onOpenCover: (entityId) => this._showCover(entityId),
      onAssignProfile: (profile) => this._assignToCovers(profile),
      onDiscarded: (entryId) => this._discarded(entryId),
      onClose: () => this._backToList() });
  }

  /** The gateway's open session: the wizard for its own modes, the existing view, in its own dialog, for the others. */
  _openSession(entryId) {
    const row = this._rows.get(entryId), session = row?.session;
    if (!session || !this._context) return;
    const item = this._covers().find((entry) => entry.entry.entry_id === entryId && entry.cover.entity_id === session.entity_id);
    // The wizard needs the cover in the list (name, gateway); otherwise the existing view opens it.
    if (item && this._wizard?.handles?.(session)) { this._openWizard({ start: "resume", session, item }); return; }
    const { hass, t } = this._context;
    const body = this._openDialog({ title: t(session.batch ? "wiz_T364" : "calTitle"), subtitle: session.batch ? "" : item?.name || session.entity_id,
      icon: session.batch ? "mdi:format-list-numbered" : "mdi:timer-outline", hosted: true });
    this._calibration.open({ host: body, hass, t, entity: { entry_id: entryId, entity_id: session.entity_id }, resume: session,
      revision: row.data?.revision, onCancel: () => this._dialogDone(), onSaved: () => this._dialogDone() });
  }

  /**
   * A dialog with the look of the maintainer's profile dialog, in the panel's dialog host. Closing it leaves an open
   * session as closing the profile dialog does: the session keeps running in Home Assistant.
   * `hosted`: the dialog hosts the maintainer's view, which has its own Stop and Cancel/Close bar: the dialog's own
   * «Chiudi» is left out and its ✕ shown at every size, so there is one way to close and one bar (D-17, D-18).
   */
  _openDialog({ title, subtitle = "", icon, hosted = false }) {
    this._closeDialog();
    // U-4: a row's ⋮ menu that opened the dialog closes with it, not at the next redraw.
    this._menu = null;
    if (this._mode.kind === "list" && this._context?.host.querySelector(".cal-menu:not([hidden])")) this._render();
    this._done = "";
    const { dialogHost, t } = this._context;
    dialogHost.innerHTML = `<dialog class="cover-profile-dialog cal-dialog" tabindex="-1" aria-labelledby="cal-dialog-title">
      <header class="dialog-head"><div class="dialog-icon" aria-hidden="true"><ha-icon icon="${esc(icon)}"></ha-icon></div>
        <div class="dialog-head-text"><h2 id="cal-dialog-title">${esc(title)}</h2>${subtitle ? `<p class="muted">${esc(subtitle)}</p>` : ""}</div>
        <button type="button" class="icon-button cal-dialog-x" ${hosted || layout.sheets() ? "" : "hidden"} aria-label="${esc(t("close"))}" title="${esc(t("close"))}"><ha-icon icon="mdi:close" aria-hidden="true"></ha-icon></button></header>
      <div class="cal-dialog-body"></div>
      <footer class="dialog-foot" ${hosted ? "hidden" : ""}><button type="button" class="cal-dialog-close">${esc(t("close"))}</button></footer></dialog>`;
    const dialog = dialogHost.querySelector("dialog");
    dialog.querySelector(".cal-dialog-close").onclick = () => this._dialogDone();
    dialog.querySelector(".cal-dialog-x").onclick = () => this._dialogDone();
    dialog.oncancel = (event) => { event.preventDefault(); this._dialogDone(); };
    // A click on the backdrop (outside the dialog's box) closes it as Escape does, applying nothing.
    dialog.addEventListener("click", (event) => {
      if (event.target !== dialog) return;
      const box = dialog.getBoundingClientRect();
      const inside = event.clientX >= box.left && event.clientX <= box.right && event.clientY >= box.top && event.clientY <= box.bottom;
      if (!inside || (!box.width && !box.height)) this._dialogDone();
    });
    dialog.showModal();
    // X-6: after a tap the focus rests on the sheet itself, not on its ✕ (no ring); after a key it stays where the
    // browser put it.
    focusOnOpen(dialog, null);
    this._dialog = dialog;
    this._holdPage(dialog);
    return dialog.querySelector(".cal-dialog-body");
  }

  _closeDialog() {
    this._calibration.close();
    this._dialog?.close();
    this._dialog?.remove();
    this._dialog = null;
    this._releasePage();
  }

  /**
   * M2-6: while a sheet is open on a phone the page under it does not scroll (iOS scrolls the page behind a modal
   * dialog, and the sheet moves with it): every scrolling ancestor of the panel, `html` and `body` get
   * `overflow: hidden` until the sheet closes. Only the sheet's body scrolls; its footer stays at the bottom, with the
   * fade above it while there is more to read (as M-5).
   */
  _holdPage(dialog) {
    this._releasePage();
    const body = dialog.querySelector(".cal-dialog-body"), foot = dialog.querySelector(".dialog-foot");
    if (body && foot) {
      const more = () => foot.classList.toggle("has-more", body.scrollHeight - body.scrollTop - body.clientHeight > 1);
      body.addEventListener("scroll", more, { passive: true });
      if (typeof ResizeObserver === "function") { this._sheetObserver = new ResizeObserver(more); this._sheetObserver.observe(body); }
      more();
    }
    if (!layout.sheets()) return;
    this._held = holdScroll(this._context.host);
  }

  _releasePage() {
    this._sheetObserver?.disconnect();
    this._sheetObserver = null;
    this._held?.();
    this._held = null;
  }

  /**
   * The cover a measurement was taken on, as the list names it: Home Assistant's provenance carries the registry name,
   * which for a cover named by its device is only the entity id.
   */
  _originName(origin) {
    const id = origin?.origin_entity_id, { hass } = this._context;
    return (id && (this._covers().find((item) => item.cover.entity_id === id)?.name || hass.states?.[id]?.attributes?.friendly_name))
      || origin?.origin_name || id || "";
  }

  /** A cover by the name the list gives it (then its friendly name, then its entity id). */
  _coverName(id) {
    return this._covers().find((item) => item.cover.entity_id === id)?.name || this._context.hass.states?.[id]?.attributes?.friendly_name || id;
  }

  _dialogDone() {
    // As a sheet (window under 720 × 500 px) the dialog slides down before it closes (at once with reduced motion or centred).
    const dialog = this._dialog;
    if (dialog && layout.slides(SHEETS) && !dialog.classList.contains("is-leaving")) {
      dialog.classList.add("is-leaving");
      setTimeout(() => { if (this._dialog === dialog) { this._closeDialog(); this.refresh(); } }, 220);
      return;
    }
    if (dialog?.classList.contains("is-leaving")) return;
    this._closeDialog();
    this.refresh();
  }

  /**
   * L-10 (4): what the busy notice says is only what the list offers: an open session can be followed («Segui
   * sessione») or, by the tab that owns it, resumed («Riprendi sessione»), or cancelled (the row's ⋮); one closed and
   * waiting for the stop, or one that cannot be read, frees the cover by itself.
   */
  _busyText(busy) {
    if (busy.unknown) return "wiz_T540";
    return busy.waiting_for_stop || busy.recoverable === false ? "wiz_T539" : busy.client_owner ? "wiz_T581" : "wiz_T538";
  }

  /**
   * L-10 (3): the row's «Cancel the calibration in progress», after a confirmation: this tab takes control of the
   * session and cancels it (it may be guided elsewhere, or interrupted with its tab gone); a refusal says why.
   */
  _abortSession(entryId) {
    const row = this._rows.get(entryId), session = row?.session;
    if (!session || !this._context) return;
    const { hass, t } = this._context, item = this._covers().find((entry) => entry.entry.entry_id === entryId && entry.cover.entity_id === session.entity_id);
    const name = item?.name || session.entity_id;
    // L-94: a confirmation, not the maintainer's view: its buttons on the dialog's own footer (Back in place of Close),
    // as the other confirmations of the tab, not in the body of a hosted dialog (whose bottom is the hosted view's bar).
    const body = this._openDialog({ title: t("wiz_T535"), subtitle: name, icon: "mdi:close-octagon-outline" });
    const dialog = this._dialog, generation = this._generation;
    body.innerHTML = `<p class="notice error cal-abort-error" role="alert" hidden></p><p>${esc(fill(t("wiz_T536"), { cover: name }))}</p>`;
    const back = dialog.querySelector(".cal-dialog-close");
    back.textContent = t("wiz_T403");
    back.classList.add("cal-dialog-cancel");
    back.setAttribute("data-abort-back", "");
    dialog.querySelector(".dialog-foot").insertAdjacentHTML("beforeend",
      `<button type="button" class="danger cal-abort-confirm" data-abort-confirm>${esc(t("wiz_T537"))}</button>`);
    const confirm = dialog.querySelector("[data-abort-confirm]"), box = body.querySelector(".cal-abort-error");
    confirm.onclick = async () => {
      if (confirm.disabled) return;
      confirm.disabled = true;
      box.hidden = true;
      try {
        await cancelSession(hass, session);
        if (generation === this._generation && this._dialog === dialog) this._dialogDone();
      } catch (error) {
        if (generation !== this._generation || this._dialog !== dialog) return;
        const key = `profileError_${error?.code}`;
        box.textContent = t(key) === key ? t("calCancelFailed") : t(key);
        box.hidden = false;
        confirm.disabled = false;
      }
    };
  }

  _assignProfile(item) {
    const { hass, t } = this._context, language = hass.language || "en";
    const data = this._rows.get(item.entry.entry_id)?.data;
    if (!data) return;
    const body = this._openDialog({ title: t(item.profile ? "wiz_T427" : "calTabAssign"), subtitle: item.name, icon: "mdi:link-variant" });
    const dialog = this._dialog, generation = this._generation;
    const profiles = [...data.profiles].sort((a, b) => a.name.localeCompare(b.name));
    // Who follows each profile, by name, to recognise a similar cover.
    const followers = (profile) => followedBy(t, profile.assigned_to.filter(Boolean).map((id) => this._coverName(id)), language);
    const describe = (profile) => {
      const slat = profile.geometry?.slat_time_s;
      const origin = profile.provenance?.opening;
      return [
        [t(slat == null ? "wiz_T370" : slat > 0 ? "wiz_T368" : "wiz_T369"),
          profile.reference_travel_cm != null ? fill(t("wiz_T372"), { cm: number(profile.reference_travel_cm, language) }) : ""].filter(Boolean).join(" · "),
        origin?.recorded_at && (origin.origin_entity_id || origin.origin_name)
          ? fill(t("wiz_T371"), { cover: this._originName(origin), date: dateText(origin.recorded_at, language) }) : "",
        followers(profile),
      ];
    };
    // "No profile" unlinks the profile the cover follows. On the cover the profile was measured on it is shown disabled:
    // its values live only in the profile until Home Assistant copies them on unlinking (backend lot to plan).
    const followed = data.profiles.find((profile) => profile.id === item.cover.profile_id);
    const source = followed?.provenance?.opening?.origin_entity_id === item.cover.entity_id;
    const none = followed ? `<label class="wiz-radio cal-assign-none${source ? " is-disabled" : ""}"><input type="radio" name="assign_profile" value="${NO_PROFILE}"${source ? " disabled" : ""}>
        <span><strong><ha-icon icon="mdi:link-variant-off" aria-hidden="true"></ha-icon>${esc(t("wiz_T404"))}</strong><small class="muted">${esc(fill(t(source ? "wiz_T430" : "wiz_T405"), { profile: followed.name }))}</small></span></label>` : "";
    // D-15/D-29: a refusal is an error notice above the choices, where it is seen even in a landscape sheet.
    body.innerHTML = profiles.length ? `<p class="notice error cal-assign-error" role="alert" hidden></p><fieldset class="wiz-radios cal-assign-list"><legend>${esc(t("wiz_T163"))}</legend>${none}${profiles.map((profile) => {
      const [type, measured, followers] = describe(profile);
      return `<label class="wiz-radio"><input type="radio" name="assign_profile" value="${esc(profile.id)}" ${profile.id === item.cover.profile_id ? "checked" : ""}>
        <span><strong>${esc(profile.name)}</strong><small class="muted">${esc(type)}</small>${measured ? `<small class="muted">${esc(measured)}</small>` : ""}<small class="muted cal-assign-followers">${esc(followers)}</small></span></label>`;
    }).join("")}</fieldset>`
      : `<p class="muted">${esc(t("profileListEmpty"))}</p>`;
    // A calibration open on the gateway reserves it: Home Assistant would refuse, so the dialog says so and waits.
    const row = this._rows.get(item.entry.entry_id), reserved = !!(row?.session || row?.reserved);
    if (reserved) body.insertAdjacentHTML("afterbegin", `<p class="cal-note cal-assign-busy" role="status">${esc(t("wiz_T412"))}</p>`);
    if (!profiles.length) return;
    // As in the design (T-09): Cancel, outlined, then Assign on the footer's row.
    const cancel = dialog.querySelector(".cal-dialog-close");
    cancel.textContent = t("wiz_T025");
    cancel.classList.add("cal-dialog-cancel");
    dialog.querySelector(".dialog-foot").insertAdjacentHTML("beforeend", `<button type="button" class="primary" data-assign disabled>${esc(t("wiz_T373"))}</button>`);
    const button = dialog.querySelector("[data-assign]");
    const chosen = () => body.querySelector('input[name="assign_profile"]:checked')?.value;
    // Assign stays off while the gateway is reserved, and after Home Assistant has refused once.
    let refused = false;
    body.onchange = () => {
      button.disabled = reserved || refused || !chosen() || chosen() === item.cover.profile_id;
      // Unlinking is said as such on the button, with the broken link.
      button.innerHTML = chosen() === NO_PROFILE ? `<ha-icon icon="mdi:link-variant-off" aria-hidden="true"></ha-icon><span>${esc(t("wiz_T406"))}</span>` : esc(t("wiz_T373"));
    };
    button.onclick = async () => {
      const choice = chosen();
      if (!choice || button.disabled || reserved || refused) return;
      const profileId = choice === NO_PROFILE ? null : choice;
      button.disabled = true;
      const box = body.querySelector(".cal-assign-error");
      box.hidden = true;
      try {
        await hass.callWS({ type: "myhome/cover_profiles/write", entry_id: item.entry.entry_id, entity_id: item.cover.entity_id,
          revision: data.revision, action: "assign", profile_id: profileId });
        if (generation === this._generation && this._dialog === dialog) {
          // D-28: an assignment is confirmed as an unlinking is.
          this._done = profileId === null ? fill(t("wiz_T407"), { cover: item.name })
            : fill(t("wiz_T511"), { cover: item.name, profile: profiles.find((profile) => profile.id === profileId)?.name ?? "" });
          this._dialogDone();
        }
      } catch (error) {
        if (generation !== this._generation || this._dialog !== dialog) return;
        const key = `profileError_${error?.code}`;
        box.textContent = t(key) === key ? t("profileError") : t(key);
        box.hidden = false;
        box.scrollIntoView?.({ block: "nearest" });
        refused = true;
      }
    };
  }

  /**
   * A-34's "Assign this profile to other covers": the maintainer's own Assign dialog (the one the Profiles tab opens,
   * ProfileCatalogueEditor with action "assign"), on the profile just written. A profile just created is found by
   * name once the overview carries it.
   */
  async _assignToCovers({ entryId, profileId, name }) {
    if (!this._context) return;
    const find = () => this._rows.get(entryId)?.data?.profiles.find((profile) => (profileId && profile.id === profileId) || (!profileId && profile.name === name));
    if (!find()) await this._read(entryId, this._generation);
    const profile = find();
    if (!profile || !this._context) return;
    const { hass, t, dialogHost, inventory, addressDetails } = this._context;
    this._catalogue ??= new ProfileCatalogueEditor();
    this._catalogue.open({ host: dialogHost, hass, entryId, profileId: profile.id, action: "assign", entities: inventory.entities,
      addressDetails, t, onSaved: () => this.refresh() });
  }

  /** Sequence selection (T-06): a view in the tab, in place of the list, with our own rows. */
  async _selectBatch() {
    const { gateways } = this._context;
    this._menu = null;
    this._mode = { kind: "batch" };
    this._batch = { entryId: gateways[0].entry_id, data: null, order: [], error: "", loading: true };
    this._context.host.innerHTML = "";
    // L-8: the sequence opens with its head at the top of what can be seen, as the wizard.
    const opened = this._loadBatch(this._batch.entryId);
    // L-8b: placed after its first paint (two frames), as the wizard.
    const host = this._context.host, frame = globalThis.requestAnimationFrame || ((callback) => setTimeout(callback, 16));
    frame(() => frame(() => { const wiz = this._mode.kind === "batch" ? host.querySelector(".cal-batch-wiz") : null; if (wiz) revealHead(wiz, { always: true }); }));
    await opened;
  }

  async _loadBatch(entryId) {
    const { hass } = this._context, generation = this._generation;
    const batch = this._batch = { entryId, data: null, order: [], error: "", loading: true };
    this._render();
    try {
      batch.data = await hass.callWS({ type: "myhome/cover_calibration/targets", entry_id: entryId });
    } catch (error) {
      const key = `profileError_${error?.code}`, { t } = this._context;
      batch.error = t(key) === key ? t("profileError") : t(key);
    }
    if (!this._current(generation) || this._batch !== batch) return;
    batch.loading = false;
    this._render();
  }

  /** The gateway's targets with the names, rooms, addresses, status and profile the list shows, in the list's order. */
  _batchTargets() {
    const batch = this._batch, { inventory, hass } = this._context;
    const covers = new Map(this._covers().filter((item) => item.entry.entry_id === batch.entryId).map((item) => [item.cover.entity_id, item]));
    return this._sorted((batch.data?.targets || []).map((target) => {
      const item = covers.get(target.entity_id);
      const entity = item?.entity || inventory.entities.find((entry) => entry.entity_id === target.entity_id && entry.entry_id === batch.entryId);
      const area = item?.area ?? (entity ? inventory.areas.find((entry) => entry.id === model.effectiveArea(entity, inventory.devices))?.name || "" : "");
      return { ...target, item, entity, area, status: item?.status, profile: item?.profile || null,
        name: item?.name || (entity ? model.entityName(entity, hass) : target.name) };
    }), hass.language || "en");
  }

  _renderBatch() {
    const { host, t, gateways } = this._context;
    const batch = this._batch, max = batch.data?.max_batch ?? 0, count = batch.order.length;
    // The same order as the list (shared choice); the place in the sequence stays the order of the ticks.
    const targets = this._batchTargets();
    const badge = (item) => item ? statusBadge(item, t) : "";
    const profile = (item) => item?.profile ? `${this._profileChip(item.profile.name)}`
      : `<span class="muted" aria-label="${esc(t("calTabProfileNone"))}">—</span>`;
    const rows = targets.map((target) => {
      const place = batch.order.indexOf(target.entity_id);
      return `<label class="cal-batch-row${place >= 0 ? " is-selected" : ""}" data-entity="${esc(target.entity_id)}">
        <span class="cal-batch-lead"><input type="checkbox" value="${esc(target.entity_id)}" ${place >= 0 ? "checked" : ""} ${target.reason ? "disabled" : ""}>
          <span class="cal-batch-order" aria-hidden="true">${place >= 0 ? place + 1 : ""}</span>
          <span class="cal-row-icon" aria-hidden="true"><ha-icon icon="mdi:window-shutter"></ha-icon></span></span>
        <span class="cal-row-name"><strong>${esc(target.name)}</strong><span class="muted">${this._subline([target.area], target.entity, target.reason ? [t(`profileError_${target.reason}`)] : [])}</span></span>
        <span class="cal-batch-chips"><span class="cal-batch-status">${badge(target.item)}</span>
        <span class="cal-batch-profile">${profile(target.item)}</span></span></label>`;
    }).join("");
    const summary = count === 0 ? t("wiz_T386") : count === 1 ? t("wiz_T385") : fill(t("wiz_T384"), { count });
    const select = t("wiz_T383").replace(/\s*:\s*$/, "");
    const picks = [["all", t("calTabFilter_all")], ["to_measure", t("calTabFilter_to_measure")], ["none", t("wiz_T391")]];
    // M4-5 (T-06): the selection is a view with the wizard's chrome: header (back, title, capacity, «Cancel the
    // sequence» / ✕), the steps Selection · Run · Results (the last two still run in the maintainer's dialog), the
    // content in the middle and the status with Start in the actions column (the bottom bar on phones).
    const line = fill(t("wizPhaseLine"), { phase: t("wiz_T482"), n: 1, total: 3 });
    const html = `<div class="wiz cal-batch cal-batch-wiz">
      <div class="wiz-frame">
        <header class="wiz-head">
          <button type="button" class="icon-button wiz-back" data-action="cal-tab-batch-back" aria-label="${esc(t("wiz_T403"))}" title="${esc(t("wiz_T403"))}"><ha-icon icon="mdi:arrow-left" aria-hidden="true"></ha-icon></button>
          <div class="wiz-head-text"><h3 class="wiz-cover">${esc(t("wiz_T364"))}</h3><p class="muted wiz-head-sub">${esc(fill(t("wiz_T488"), { max }))}</p></div>
          <button type="button" class="wiz-discard" data-action="cal-tab-batch-back">${esc(t("wiz_T366"))}</button>
          <button type="button" class="icon-button wiz-x" data-action="cal-tab-batch-back" aria-label="${esc(t("wiz_T366"))}" title="${esc(t("wiz_T366"))}"><ha-icon icon="mdi:close" aria-hidden="true"></ha-icon></button>
        </header>
        <div class="wiz-compact"><span class="wiz-dot" aria-hidden="true"></span><span class="wiz-compact-line">${esc(line)}</span>
          <button type="button" class="wiz-steps-button" data-action="cal-tab-batch-steps" aria-haspopup="dialog">${esc(t("wiz_T274"))}</button></div>
        <div class="wiz-columns">
          <nav class="wiz-steps" aria-label="${esc(t("wiz_T274"))}">${this._batchSteps(count)}</nav>
          <div class="wiz-main">
            <h4 class="wiz-title">${esc(t("wiz_T481"))}</h4>
            <div class="wiz-body"><p>${esc(t("wiz_T387"))}</p></div>
            <div class="cal-note is-warning cal-batch-note">${rich(t("wiz_T388"))}</div>
            ${gateways.length > 1 ? `<label class="cal-batch-gateway">${esc(t("gateway"))}<select data-batch="gateway">${gateways.map((entry) => `<option value="${esc(entry.entry_id)}" ${entry.entry_id === batch.entryId ? "selected" : ""}>${esc(entry.title)}</option>`).join("")}</select></label>` : ""}
            ${batch.error ? `<p class="notice error" role="alert">${esc(batch.error)}</p>` : ""}
            ${batch.loading ? `<p class="muted" role="status">${esc(t("loading"))}</p>` : ""}
            ${!batch.loading && !batch.error && !targets.length ? `<p class="muted">${esc(t("calBatchEmpty"))}</p>` : ""}
            ${targets.length ? `${this._filterBar({ extra: " cal-batch-filterbar", compactClass: " cal-batch-compact", label: t("wiz_T383"),
              count: this._countLine({ all: targets.length, calibrated: targets.filter((target) => target.status && target.status !== "to_measure").length,
                to_measure: targets.filter((target) => target.status === "to_measure").length }),
              chips: picks.map(([id, label]) => `<button type="button" class="cal-chip" data-action="cal-tab-batch-pick" data-id="${id}">${esc(label)}</button>`).join(""),
              select: `<label class="cal-compact-sort cal-compact-filter cal-compact-select"><select data-batch-pick aria-label="${esc(select)}">
                <option value="" selected>${esc(select)}</option>${picks.map(([id, label]) => `<option value="${id}">${esc(label)}</option>`).join("")}</select></label>` })}
            <div class="cal-list cal-batch-list">
              <div class="cal-batch-rows" role="group" aria-label="${esc(t("wiz_T364"))}">${rows}</div></div>
            <p class="error cal-batch-limit" role="alert">${count > max ? esc(`${t("calBatchLimit")}: ${max}`) : ""}</p>` : ""}
          </div>
          <div class="wiz-actions cal-batch-actions"><div class="cal-batch-sticky">
            <button type="button" class="wiz-action primary" data-action="cal-tab-batch-start" ${count === 0 || count > max || batch.loading ? "disabled" : ""}>${esc(t("wiz_T365"))}</button>
            <p class="cal-batch-summary" role="status">${esc(summary)}</p>
          </div></div>
        </div>
        <div class="wiz-sheet" role="dialog" aria-modal="true" aria-label="${esc(t("wiz_T274"))}" ${this._batchStepsOpen ? "" : "hidden"}><div class="wiz-sheet-card">
          <div class="wiz-sheet-head"><h4 class="wiz-sheet-title">${esc(t("wiz_T274"))}</h4><button type="button" class="icon-button wiz-sheet-close" data-action="cal-tab-batch-steps-close" aria-label="${esc(t("close"))}"><ha-icon icon="mdi:close" aria-hidden="true"></ha-icon></button></div>
          <div class="wiz-sheet-steps">${this._batchSteps(count)}</div></div></div>
      </div>
    </div>`;
    // A new selection only patches the rows, the summary and Start: the view is not rebuilt, so the list never moves
    // under the pointer (rebuilding recreated the spacer and the browser shifted the scroll by a pixel or two).
    const structure = JSON.stringify([targets.map((target) => [target.entity_id, target.name, target.area, target.status, target.profile?.name, target.reason]),
      batch.loading, batch.error, batch.entryId, max, gateways.length, this._sort, t("wiz_T364")]);
    if (host.querySelector(".cal-batch") && this._batchStructure === structure) this._patchBatch(summary, count, max);
    else { this._batchStructure = structure; morph(host, html); fitBars(host.querySelector(".cal-batch")); }
    fitRows(host.querySelector(".cal-batch"));
    // U-1: the sequence's one-column layout docks its action bar too (once per view).
    const docked = host.querySelector(".cal-batch");
    if (docked && this._dockedView !== docked) { this._undockView?.(); this._dockedView = docked; this._undockView = dockBar(docked); }
    // The fade above the actions while the list goes on under them (phones, as M-5).
    const main = host.querySelector(".cal-batch .wiz-main"), actions = host.querySelector(".cal-batch .wiz-actions");
    if (main && actions && !main.dataset.watched) {
      main.dataset.watched = "1";
      const more = () => actions.classList.toggle("has-more", main.scrollHeight - main.scrollTop - main.clientHeight > 1);
      main.addEventListener("scroll", more, { passive: true });
      if (typeof ResizeObserver === "function") new ResizeObserver(more).observe(main);
      more();
    }
  }

  /** The sequence's steps, as the wizard's stepper: Selection (with how many are chosen), Run, Results. */
  _batchSteps(count) {
    const { t } = this._context;
    const chosen = count === 0 ? t("wiz_T487") : count === 1 ? t("wiz_T486") : fill(t("wiz_T485"), { count });
    const step = (label, state, sub = "") => `<li data-step-state="${state}"${state === "current" ? ' aria-current="step"' : ""}><span class="wiz-step-mark" aria-hidden="true"></span><span class="wiz-step-label">${esc(label)}</span>${sub ? `<ul><li>${esc(sub)}</li></ul>` : ""}</li>`;
    return `<p class="wiz-steps-line">${esc(fill(t("wizPhaseLine"), { phase: t("wiz_T482"), n: 1, total: 3 }))}</p>
      <ol>${step(t("wiz_T482"), "current", chosen)}${step(t("wiz_T483"), "future")}${step(t("wiz_T484"), "future")}</ol>`;
  }

  _patchBatch(summary, count, max) {
    const { host, t } = this._context, batch = this._batch;
    for (const row of host.querySelectorAll(".cal-batch-row")) {
      const place = batch.order.indexOf(row.dataset.entity), input = row.querySelector("input");
      row.classList.toggle("is-selected", place >= 0);
      if (input.checked !== place >= 0) input.checked = place >= 0;
      row.querySelector(".cal-batch-order").textContent = place >= 0 ? String(place + 1) : "";
    }
    host.querySelector(".cal-batch-summary").textContent = summary;
    const steps = this._batchSteps(count);
    for (const target of host.querySelectorAll(".cal-batch .wiz-steps, .cal-batch .wiz-sheet-steps")) if (target.innerHTML !== steps) morph(target, steps);
    const limit = host.querySelector(".cal-batch-limit");
    if (limit) limit.textContent = count > max ? `${t("calBatchLimit")}: ${max}` : "";
    host.querySelector('[data-action="cal-tab-batch-start"]').disabled = count === 0 || count > max || batch.loading;
  }

  _batchToggle(entityId, checked) {
    const batch = this._batch;
    batch.order = batch.order.filter((id) => id !== entityId);
    if (checked) batch.order.push(entityId);
    this._render();
  }

  /** Shortcuts: all available covers or those to measure, in the list's order; None clears the sequence. */
  _batchPick(kind) {
    this._batch.order = kind === "none" ? [] : this._batchTargets().filter((target) => !target.reason && (kind === "all" || target.item?.status === "to_measure"))
      .map((target) => target.entity_id);
    this._render();
  }

  /** Start: `batch_start` with the chosen order, once; the existing view runs it in a dialog for now. */
  _batchStart() {
    const batch = this._batch, ids = [...batch.order];
    if (batch.starting || !ids.length || ids.length > (batch.data?.max_batch ?? 0)) return;
    batch.starting = true;
    const { hass, t } = this._context;
    this._mode = { kind: "list" };
    this._batch = null;
    this._context.host.innerHTML = "";
    this._render();
    const body = this._openDialog({ title: t("wiz_T364"), icon: "mdi:format-list-numbered", hosted: true });
    this._calibration.open({ host: body, hass, t, entity: { entry_id: batch.entryId }, mode: "automatic", entity_ids: ids, revision: batch.data.revision,
      onCancel: () => this._dialogDone(), onSaved: () => this._dialogDone() });
  }

  _batchBack() {
    this._batch = null;
    this._backToList();
  }
}
