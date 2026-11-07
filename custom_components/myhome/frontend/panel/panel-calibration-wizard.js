/**
 * The calibration wizard of the Calibration tab: stepper · instruction · actions on desktop, a
 * full screen on a phone. It draws what `panel-calibration-screens.js` decides and talks to a
 * `CalibrationGateway` (live session or local preview); it never builds a WebSocket message itself.
 */
const asset = (name) => { const url = new URL(name, import.meta.url); url.search = new URL(import.meta.url).search; return url.href; };
const [{ escapeHtml: esc }, text, screens, gateways, illustration, { morph }, { layout, fitBars, fitRows, dockBar, focusOnOpen, revealHead }] = await Promise.all([
  import(asset("panel-dom.js")), import(asset("panel-calibration-text.js")), import(asset("panel-calibration-screens.js")),
  import(asset("panel-calibration-gateway.js")), import(asset("panel-calibration-illustration.js")), import(asset("panel-calibration-morph.js")), import(asset("panel-calibration-layout.js")),
]);
const { rich, inline, names } = text;
const { buildScreen, slug, stopVerdict } = screens;
const { LiveCalibrationGateway, PreviewCalibrationGateway } = gateways;
const { illustrationMarkup, ILLUSTRATIONS } = illustration;

/** A cover's room, gateway and bus address: one string, and its units for the header (responsive map §3.3). */
const subline = (lead, bus) => {
  const units = lead.filter(Boolean);
  return { address: [...units, bus].filter(Boolean).join(" · "), units: { lead: units, bus } };
};

function factMarkup(line) {
  const index = line.indexOf(": ");
  return index < 0 ? esc(line) : `${esc(line.slice(0, index))}: <strong>${esc(line.slice(index + 2))}</strong>`;
}

/** A tape reading with a comma or a point; anything else, empty included, is not a number. */
export function parseReading(value) {
  const textValue = String(value ?? "").trim().replace(",", ".");
  return /^-?(\d+\.?\d*|\.\d+)$/.test(textValue) ? Number(textValue) : NaN;
}

const STAGE_ICON = { done: "mdi:check-circle", current: "mdi:circle-half-full", future: "mdi:circle-outline" };
const STAGES_BEFORE_SESSION = ["choose", "path", "profile", "current", "case", "before"];
/**
 * After every change of screen (or of its buttons) the confirming buttons stay disabled this long, so that a slow
 * double tap never confirms an end stop the cover has not reached, nor skips the second tap of "Take control".
 * Stop is never held back.
 */
export const CONFIRM_GUARD_MS = 700;
/** L-108: what counts as the user touching the page, and how long this tab's own press may wait for its answer. */
const TOUCHES = ["pointerdown", "touchstart", "mousedown", "click", "keydown"];
const OWN_PRESS_MS = 10000;
/** Review N1: a new session on the cover within this time of ours ending, this tab still on that screen, is a restart. */
const RESTART_WINDOW_MS = 600000;
/** L-25: the choices made on the owner's own screens that its readers need to show the same step. */
const SHARED_FLAGS = ["ackLift", "ackAscent", "ackDescent", "ackReadings", "invalidRun", "discarded", "misbehaved", "travelTyped",
  // L-103: every other choice a session screen depends on (the check skipped, the gap's origin, the travel kept).
  "checkSkipped", "lastGap", "liftOrigin", "keptTravel"];
/** How long a sheet takes to slide in or out (the CSS animations use the same). */
const SHEET_MS = 220;
/** Home Assistant keeps a session open this long for its owner at rest (IDLE_LEASE_SECONDS) when the view does not say. */
const IDLE_LEASE_SECONDS = 1800;
/** N-3: the positioning of a cover with no known closing time waits this long at most (seconds). */
const HOME_FALLBACK_S = 120;
/** A stop frame between these times is an actuator's timer (about 60 s), never the real end of the run. */
export const TIMER_STOP_SECONDS = screens.TIMER_STOP_SECONDS;
// Phases a press goes through before the cover stops again: the press that started them still counts.
const TRANSIT = ["starting_open", "starting_close", "opening", "closing", "geometry_wait_stop"];

export class CalibrationWizard {
  /** `previews` (review P1): the preview paths, off unless switched on for development (`previewPathsEnabled`). */
  constructor({ stageMs, guardMs = CONFIRM_GUARD_MS, previews = screens.previewPathsEnabled() } = {}) {
    this._stageMs = stageMs; this._guardMs = guardMs; this._previews = !!previews; this._generation = 0;
  }

  /** L-4: its own sessions, geometry and guided («solo tempi», marked `origin: "wizard"`); the rest is the maintainer's. */
  handles(session) { return !!session && !session.batch && (session.mode === "geometry" || (session.mode === "guided" && session.origin === "wizard" && !session.direction)); }

  updateHass(hass) { if (this._context) { this._context.hass = hass; this._theme(); if (this._local?.homing) this._homeTick(); } }

  /** The drawings follow Home Assistant's dark mode; without theme data, the system's preference. */
  _theme() {
    if (!this._root) return;
    const mode = this._context?.hass?.themes?.darkMode;
    const dark = typeof mode === "boolean" ? mode : !!globalThis.matchMedia?.("(prefers-color-scheme: dark)")?.matches;
    this._root.dataset.theme = dark ? "dark" : "light";
  }

  close() {
    clearInterval(this._leaseTimer); this._leaseTimer = null;
    clearInterval(this._homeTimer); this._homeTimer = null;
    clearInterval(this._tickTimer); this._tickTimer = null;
    if (this._onVisible) document.removeEventListener("visibilitychange", this._onVisible);
    this._onVisible = null;
    if (this._onTouch) for (const type of TOUCHES) document.removeEventListener(type, this._onTouch, true);
    this._onTouch = null;
    this._moreObserver?.disconnect();
    this._moreObserver = null;
    this._moreUpdate = null;
    this._navResolve?.(true);
    this._navResolve = null;
    this._unloadGuard(false);
    this._generation++;
    this._staying = null;
    clearTimeout(this._guardTimer);
    this._gateway?.close();
    this._gateway = null;
    this._undock?.();
    this._undock = null;
    this._root?.remove();
    this._root = null;
    this._frame = null;
    this._engine = null;
    this._context = null;
  }

  open({ host, hass, t, covers, data, picker = null, start = "choose", item = null, session = null, onOpenCover, onAssignProfile, onDiscarded, onClose }) {
    this.close();
    this._context = { host, hass, t, covers, data, picker, onOpenCover, onAssignProfile, onDiscarded, onClose };
    // Opened on a given cover, the choice is made; from the tab's generic button it starts empty.
    this._local = { stage: "choose", path: "A", scope: null, coverKey: item ? key(item) : "", coverPicked: !!item };
    host.innerHTML = "";
    this._root = document.createElement("section");
    this._root.className = "wiz";
    this._root.setAttribute("aria-labelledby", "wiz-cover");
    // The drawn screen is replaced at every step; the session engine keeps its own element.
    this._frame = document.createElement("div");
    this._frame.className = "wiz-frame";
    this._engine = document.createElement("div");
    this._engine.className = "wiz-engine";
    this._engine.hidden = true;
    this._root.append(this._frame, this._engine);
    host.append(this._root);
    this._revealOnOpen = true;
    // U-1: the one-column layout docks its action bar to the window's bottom.
    this._undock = dockBar(this._root);
    this._theme();
    this._root.addEventListener("click", (event) => this._click(event));
    // L-108: when the user last touched the page (the wizard opens from a tap too): the hold after a change of the bar
    // only follows a recent touch, or this tab's own press.
    this._touchedAt = performance.now();
    this._ownPress = null;
    this._onTouch = () => { this._touchedAt = performance.now(); };
    for (const type of TOUCHES) document.addEventListener(type, this._onTouch, true);
    this._root.addEventListener("keydown", (event) => this._keydown(event));
    this._root.addEventListener("input", (event) => this._input(event));
    this._root.addEventListener("change", (event) => this._change(event));
    this._root.addEventListener("submit", (event) => { event.preventDefault(); this._reading(); });
    // Back on the page (Safari suspends a tab in the background): reconnect at once, without asking.
    this._onVisible = () => {
      const gateway = this._gateway, status = gateway?.status || {};
      if (document.hidden || !gateway || gateway.preview || status.expired || !gateway.view?.recoverable) return;
      // Lost: resume the same session. Otherwise ask at once: a subscription that died in the background shows now.
      if (status.lost) gateway.reconnect(); else gateway.ping?.();
    };
    document.addEventListener("visibilitychange", this._onVisible);
    this._signature = null;
    if (start === "path" && item) this._local.stage = "path";
    // Where the way back ends: A-01 from the tab's generic button, A-02 from a cover's own Measure.
    this._local.entry = this._local.stage;
    this._render();
    // The tab's Fine-tune asks first what is wrong (C-01, C-02); A-34's "Continue with fine-tuning" goes straight to A-22.
    if (start === "refine" && item) { Object.assign(this._local, { stage: "current", path: "C", entry: "current" }); this._render(); }
    else if (start === "resume" && session) this._resume(session, item);
    this._render();
  }

  // ---------- context for the screens ----------

  /** The tab's covers as they are now (a function from the tab, read again after a save), or a fixed list. */
  _covers() { const { covers } = this._context; return (typeof covers === "function" ? covers() : covers) || []; }

  _item() { return this._covers().find((item) => key(item) === this._local.coverKey) || null; }

  _gatewayData(item = this._item()) { return item ? this._context.data?.(item.entry.entry_id) : null; }

  _cover() {
    const item = this._item();
    if (!item) return null;
    const data = this._gatewayData(item), { hass } = this._context;
    const followers = item.profile ? item.profile.assigned_to.filter((id) => id && id !== item.cover.entity_id)
      .map((id) => this._covers().find((entry) => entry.cover.entity_id === id)?.name || hass.states?.[id]?.attributes?.friendly_name || id) : [];
    const followed = this._local.path === "B" ? data?.profiles.find((profile) => profile.id === this._local.profileId) : null;
    const address = item.entity?.address;
    return { name: item.name, entity_id: item.cover.entity_id, effective: item.cover.effective || {}, travel_cm: item.cover.travel_cm ?? null,
      status: item.status, detail: item.detail, last: item.last,
      profile: item.profile, followers, followProfile: followed?.name ?? null,
      // G4-1: as the list rows: room first (and the gateway when there are several), then the bus address; no WHO.
      // The units apart too (responsive map §3.3): the header wraps between them, never inside the address.
      ...subline([item.area, new Set(this._covers().map((entry) => entry.entry.entry_id)).size > 1 ? item.entry.title : ""],
        address ? [`${this._context.t("address")}: ${address.raw ?? this._context.t("addressUnknown")}`,
          ...(address.a != null && address.pl != null ? [`A: ${address.a}`, `PL: ${address.pl}`] : [])].join(" · ") : "") };
  }

  /** A drop-down entry: "Name · Room · Address: 86 · A: 8 · PL: 6", and the gateway when there are several. */
  _coverLabel(item) {
    const { t } = this._context, covers = this._covers(), address = item.entity?.address;
    const bus = address?.raw ? [`${t("address")}: ${address.raw}`, ...(address.a != null && address.pl != null ? [`A: ${address.a}`, `PL: ${address.pl}`] : [])].join(" · ") : "";
    const gateway = new Set(covers.map((entry) => entry.entry.entry_id)).size > 1 ? item.entry.title : "";
    return [item.name, item.area, bus, gateway].filter(Boolean).join(" · ");
  }

  _profiles() {
    const data = this._gatewayData();
    return (data?.profiles || []).map((profile) => ({ id: profile.id, name: profile.name, followers: profile.assigned_to.length,
      measuredOn: this._originName(profile.provenance?.opening), slat: profile.geometry ? profile.geometry.slat_time_s ?? null : null,
      followerNames: profile.assigned_to.filter(Boolean).map((id) => this._covers().find((item) => item.cover.entity_id === id)?.name
        || this._context.hass.states?.[id]?.attributes?.friendly_name || id),
      referenceTravel: profile.reference_travel_cm ?? null }));
  }

  /** Where a profile was measured, by the cover's name in the list (the provenance's name may be its entity id). */
  _originName(origin) {
    const id = origin?.origin_entity_id, { hass } = this._context;
    return (id && (this._covers().find((item) => item.cover.entity_id === id)?.name || hass.states?.[id]?.attributes?.friendly_name))
      || origin?.origin_name || id || null;
  }

  _screen() {
    const { t, hass } = this._context, gateway = this._gateway;
    // L-25: a reader shows the step the owner shows: the owner's path and the choices made on its screens (published
    // with `show`) over this tab's own.
    const shown = gateway?.view?.read_only && !gateway.view.preview ? gateway.view.screen : null;
    const local = shown ? Object.assign(Object.create(this._local), shown.local || {}, shown.path ? { path: shown.path, scope: shown.scope ?? null } : {}) : this._local;
    const cover = this._cover(), language = hass.language || "en";
    const followed = local.path === "B" ? this._gatewayData()?.profiles.find((profile) => profile.id === local.profileId) : null;
    const screen = buildScreen({ t, language, path: local.path, scope: local.scope, stage: local.stage, local, cover,
      covers: this._covers().map((item) => ({ key: key(item), label: this._coverLabel(item), status: item.status, detail: item.detail, last: item.last })),
      profiles: this._profiles(), followed, followerNames: cover ? names(cover.followers, language) : "",
      view: gateway?.view || null, status: gateway?.status || {}, elapsed: gateway?.elapsed() ?? null, runPosition: local.run?.position ?? null, now: Date.now(),
      preview: !!gateway?.preview, previews: this._previews, supports: (target) => gateway ? gateway.supports(target) : false });
    // M3-3: the step reached by a live session, so that its end shows where it stopped.
    if (local.stage === "session" && gateway?.view && !screen.interrupted && !/^(S-|loading|paused|unknown)/.test(screen.code)) local.reachedPhase = screen.phase;
    return screen;
  }

  // ---------- drawing ----------

  _render() {
    if (!this._root) return;
    // While "Close without saving?" is open the screen behind it is not rebuilt: a tap meant for the
    // dialog never lands on a button that appeared under it. It is redrawn when the dialog closes.
    if (this._frame.querySelector(".wiz-dialog:not([hidden])")) { this._patch(this._screen()); return; }
    const screen = this._screen();
    this._current = screen;
    this._publish(screen);
    const { t } = this._context;
    const drawing = (position) => screen.illustration?.position === position ? illustrationMarkup(screen.illustration.id, t("wizIllustration"), esc) : "";
    // G2-11: a notice that comments a result sits right under the result box (or the stopwatch), before text and drawing.
    const resultNotices = '<div class="wiz-result-notices" aria-live="polite"></div>';
    // The header (cover name and address) is patched, not part of the frame: choosing a cover on A-01 keeps the list
    // and its scroll (M3-4).
    const signature = JSON.stringify([screen.code, screen.title, screen.body, screen.choice && { ...screen.choice, value: undefined },
      screen.field && { ...screen.field, disabled: undefined, error: undefined }, screen.box, screen.table, screen.technical, screen.precision,
      screen.profileBlock && { ...screen.profileBlock, name: screen.profileBlock.name && { label: screen.profileBlock.name.label, hint: screen.profileBlock.name.hint } },
      screen.saveInfo, screen.status, screen.preview, screen.illustration, screen.wait && screen.wait.text, screen.discard, !!screen.readOnly, screen.back, !!screen.picker, screen.results, screen.note, screen.help]);
    // L-119: the step the bar belongs to (one bar per step, whatever its screens).
    const barView = this._gateway?.view;
    const barStep = barView ? JSON.stringify([barView.session_id ?? null, barView.mode ?? null, barView.step ?? null, !!barView.read_only]) : null;
    if (signature !== this._signature) {
      // L-117b: a screen is new when its step changes, not when only its variant does: the summary is A-21, A-21c or
      // A-21d by the profile switch under it (`family`), and toggling the switch is the same screen.
      const family = screen.family || screen.code;
      const codeChanged = this._renderedFamily !== family;
      // "What happens when I save?" opens with the summary; a reader's choice is kept while it stays on screen.
      const info = this._frame.querySelector(".wiz-save-info");
      this._local.saveInfoOpen = info ? info.open : true;
      // The slats explanation (G2-3) starts closed on every screen and keeps the reader's choice while it stays.
      const help = this._frame.querySelector(".wiz-help");
      this._local.helpOpen = !codeChanged && !!help?.open;
      // A choice that redraws the screen (a note under the chosen option) keeps the keyboard on that choice.
      const active = this._root.getRootNode().activeElement;
      const radio = !codeChanged && active?.type === "radio" && this._frame.contains(active) ? active.name : null;
      // L-117: the same screen drawn again (a switch, a choice, a view of Home Assistant that changes a part of it) keeps
      // where it was scrolled, in the wizard's own content (phone) and in the page around it, and the field in use;
      // only a new screen starts from the top.
      const kept = codeChanged ? null : {
        main: this._frame.querySelector(".wiz-main")?.scrollTop ?? 0,
        around: scrollParents(this._root).map((element) => [element, element.scrollTop]),
        focus: active && this._frame.contains(active) && active.type !== "radio"
          ? (active.name ? `[name="${active.name}"]` : active.dataset?.wiz ? `[data-wiz="${active.dataset.wiz}"]` : null) : null,
      };
      this._signature = signature;
      this._renderedCode = screen.code;
      this._renderedFamily = family;
      this._root.dataset.code = screen.code;
      this._root.toggleAttribute("data-preview", screen.preview);
      // L-61 (review nit): a reader's ✕ only stops following, and says so.
      const xLabel = !screen.discard ? t("wiz_T158") : screen.readOnly ? t("wiz_T573") : t("wizDiscard");
      const oldBar = this._frame.querySelector(".wiz-actions");
      const keptBar = oldBar && barStep === this._barStep && this._actionsSignature === barParts(screen).signature ? oldBar : null;
      this._frame.innerHTML = `
        <header class="wiz-head">
          ${screen.back ? `<button type="button" class="icon-button wiz-back" data-wiz="back" aria-label="${esc(t("wiz_T403"))}" title="${esc(t("wiz_T403"))}"><ha-icon icon="mdi:arrow-left" aria-hidden="true"></ha-icon></button>` : ""}
          <div class="wiz-head-text"><h3 id="wiz-cover" class="wiz-cover">${esc(screen.header.title)}</h3><p class="muted wiz-head-sub" hidden></p></div>
          ${screen.discard ? (screen.readOnly
            // L-61: a reader only stops following (the calibration goes on elsewhere): secondary, no dialog.
            ? `<button type="button" class="wiz-unfollow" data-wiz="exit">${esc(t("wiz_T573"))}</button>`
            : `<button type="button" class="wiz-discard" data-wiz="exit">${esc(t("wizDiscard"))}</button>`) : ""}
          <button type="button" class="icon-button wiz-x" data-wiz="${screen.discard ? "exit" : "close"}" aria-label="${esc(xLabel)}" title="${esc(xLabel)}"><ha-icon icon="mdi:close" aria-hidden="true"></ha-icon></button>
        </header>
        <div class="wiz-compact"><span class="wiz-dot" aria-hidden="true"></span><span class="wiz-compact-line"></span>
          <button type="button" class="wiz-steps-button" data-wiz="steps" aria-haspopup="dialog">${esc(t("wiz_T274"))}</button></div>
        <div class="wiz-columns">
          <nav class="wiz-steps" aria-label="${esc(t("wiz_T274"))}"></nav>
          <div class="wiz-main">
            ${screen.preview ? `<p class="wiz-preview" role="note"><ha-icon icon="mdi:eye-outline" aria-hidden="true"></ha-icon><span>${esc(t("wizPreview"))}</span></p>` : ""}
            <h4 class="wiz-title" tabindex="-1">${esc(screen.title)}</h4>${screen.subline ? `<p class="cal-note wiz-subline">${esc(screen.subline)}</p>` : ""}
            <div class="wiz-stopwatch" hidden${ILLUSTRATIONS[screen.illustration?.id] ? ` style="--wiz-stopwatch-width: ${ILLUSTRATIONS[screen.illustration.id].width}px"` : ""}><span class="wiz-stopwatch-label"></span><span class="wiz-stopwatch-value"></span></div>
            ${screen.results?.length ? "" : resultNotices}
            ${drawing("before-body")}
            ${screen.wait ? this._waitMarkup(screen.wait) : ""}
            ${screen.results?.length ? `<div class="wiz-results">${screen.results.map((tile) => {
              // L-69: a tile with a note (the check's precision) takes the note's colours: green within the threshold, yellow beyond.
              const box = `<div class="wiz-result${tile.invalid ? " is-invalid" : ""}${tile.note ? ` is-${tile.note.tone}` : ""}"><span class="wiz-result-label">${esc(tile.label)}</span>
              <span class="wiz-result-value"><span class="wiz-result-number">${esc(tile.value)}</span><small> ${esc(tile.unit)}</small>${tile.extra ? `<span class="wiz-result-extra"> · ${esc(tile.extra)}</span>` : ""}</span>${tile.caption ? `<span class="wiz-result-caption">${esc(tile.caption)}</span>` : ""}</div>`;
              // L-55: a tile with its own note (the check's precision): the note beside it, under it where narrow.
              return tile.note ? `<div class="wiz-result-row">${box}<p class="cal-note is-${tile.note.tone} wiz-result-note"><span>${text.units(esc(tile.note.text))}</span></p></div>` : box;
            }).join("")}</div>${resultNotices}` : ""}
            ${screen.body ? `<div class="wiz-body">${rich(screen.body)}</div>` : ""}
            ${screen.help && !screen.choice ? this._helpMarkup(screen.help) : ""}
            ${screen.note ? `<p class="cal-note${screen.note.tone === "warning" ? " is-warning" : ""} wiz-note">${esc(screen.note.text)}</p>` : ""}
            ${screen.status.length ? `<div class="wiz-status">${screen.status.map((line) => `<p>${esc(line)}</p>`).join("")}</div>` : ""}
            ${screen.box ? `<div class="wiz-box"><strong>${esc(screen.box.label)}</strong>${screen.box.lines.map((line) => `<p>${factMarkup(line)}</p>`).join("")}</div>` : ""}
            ${screen.choice ? this._choiceMarkup(screen.choice) : ""}
            ${screen.picker ? '<div class="wiz-picker"></div>' : ""}
            ${screen.field ? this._fieldMarkup(screen.field) : ""}
            ${screen.precision ? `<p class="wiz-precision">${inline(screen.precision)}</p>` : ""}
            ${screen.table ? this._tableMarkup(screen.table) : ""}
            ${screen.technical ? this._technicalMarkup(screen.technical) : ""}
            ${screen.profileBlock ? this._profileMarkup(screen.profileBlock) : ""}
            ${screen.saveInfo ? `<details class="wiz-accordion wiz-save-info"${this._local.saveInfoOpen ? " open" : ""}><summary>${esc(screen.saveInfo.title)}<ha-icon icon="mdi:chevron-down" aria-hidden="true"></ha-icon></summary>${rich(screen.saveInfo.body)}</details>` : ""}
            <div class="wiz-notices" aria-live="polite"></div>
          </div>
          <div class="wiz-actions"></div>
        </div>
        <div class="wiz-sheet" role="dialog" aria-modal="true" aria-label="${esc(t("wiz_T274"))}" hidden><div class="wiz-sheet-card" tabindex="-1">
          <div class="wiz-sheet-head"><h4 class="wiz-sheet-title">${esc(t("wiz_T274"))}</h4><button type="button" class="icon-button wiz-sheet-close" data-wiz="steps-close" aria-label="${esc(t("close"))}"><ha-icon icon="mdi:close" aria-hidden="true"></ha-icon></button></div>
          <div class="wiz-sheet-steps"></div></div></div>
        <div class="wiz-dialog" role="alertdialog" aria-modal="true" aria-labelledby="wiz-dialog-title" aria-describedby="wiz-dialog-body" hidden><div class="wiz-dialog-card" tabindex="-1">
          <div class="wiz-dialog-head"><h4 id="wiz-dialog-title">${esc(t("wiz_T267"))}</h4><button type="button" class="icon-button wiz-dialog-x" data-wiz="dialog-x" hidden aria-label="${esc(t("close"))}" title="${esc(t("close"))}"><ha-icon icon="mdi:close" aria-hidden="true"></ha-icon></button></div><p id="wiz-dialog-body"></p>
          <div class="actions"><button type="button" class="wiz-leave" data-wiz="leave">${esc(t("wiz_T270"))}</button>
            <button type="button" class="primary" data-wiz="stay">${esc(t("wiz_T269"))}</button></div></div></div>
        <div class="wiz-dialog wiz-nav-dialog" role="alertdialog" aria-modal="true" aria-labelledby="wiz-nav-title" aria-describedby="wiz-nav-body" hidden><div class="wiz-dialog-card" tabindex="-1">
          <div class="wiz-dialog-head"><h4 id="wiz-nav-title">${esc(t("wiz_T408"))}</h4><button type="button" class="icon-button wiz-dialog-x" data-wiz="dialog-x" hidden aria-label="${esc(t("close"))}" title="${esc(t("close"))}"><ha-icon icon="mdi:close" aria-hidden="true"></ha-icon></button></div><p id="wiz-nav-body"></p>
          <div class="actions"><button type="button" class="wiz-leave" data-wiz="nav-discard">${esc(t("wiz_T411"))}</button>
            <button type="button" data-wiz="nav-later">${esc(t("wiz_T410"))}</button>
            <button type="button" class="primary" data-wiz="nav-stay">${esc(t("wiz_T269"))}</button></div></div></div>`;
      // L-119b: within one step the bar is the same nodes from screen to screen (A-04 → A-05, A-09 → A-10, …): the
      // frame is new, the bar is moved into it as it was.
      if (keptBar) this._frame.querySelector(".wiz-actions").replaceWith(keptBar);
      else { this._actionsSignature = null; this._actionIds = null; this._enabledIds = null; }
      this._pickerHtml = null;
      this._watchMore();
      if (radio) this._frame.querySelector(`.wiz-radios input[name="${radio}"]:checked`)?.focus({ preventScroll: true });
      if (kept) {
        if (kept.focus) this._frame.querySelector(kept.focus)?.focus({ preventScroll: true });
        const restore = () => {
          const main = this._frame?.querySelector(".wiz-main");
          if (main && main.scrollTop !== kept.main) main.scrollTop = kept.main;
          for (const [element, top] of kept.around) if (element.scrollTop !== top) element.scrollTop = top;
        };
        // At once (before the frame is painted), and once more on the next frame in case a layout pass moved it.
        restore();
        const frame = this._frame;
        (globalThis.requestAnimationFrame || ((callback) => setTimeout(callback, 16)))(() => { if (this._frame === frame && this._renderedFamily === family) restore(); });
      }
      // L-8: in the page's inline layouts the page is scrolled by `revealHead` (the head at the top on opening, kept in
      // view at every step), never by the focus; full screen, the focus scrolls the wizard's own content.
      if (codeChanged) queueMicrotask(() => {
        const inline = !layout.fullScreen();
        this._frame?.querySelector(".wiz-title")?.focus({ preventScroll: inline });
        if (inline) this._reveal();
      });
    }
    this._barStep = barStep;
    this._patch(screen);
  }

  /**
   * L-8b: after the step has been painted (two frames), the head is placed: at the top of the window on opening (once a
   * screen with its head is there), kept in view at every other step, never sent to the top of the page.
   */
  _reveal() {
    const root = this._root, frame = globalThis.requestAnimationFrame || ((callback) => setTimeout(callback, 16));
    frame(() => frame(() => {
      if (root !== this._root || !root?.isConnected) return;
      const always = this._revealOnOpen;
      this._revealOnOpen = false;
      revealHead(root, { always });
    }));
  }

  /** L-24: the countdowns on screen, each its text only. */
  _tick() {
    for (const notice of this._frame?.querySelectorAll(".wiz-notice[data-expires]") || []) {
      const value = text.fill(notice.dataset.template, { time: screens.clock(Number(notice.dataset.expires) - Date.now()) });
      if (notice.textContent !== value) notice.textContent = value;
    }
  }

  _engineHost() { return this._engine; }

  /** A confirmation hides at once while centred; as a sheet (window under 720 × 500 px) it slides down first. */
  _hideDialog(dialog, done) {
    if (layout.sheets()) { this._slideAway(dialog, done); return; }
    dialog.hidden = true;
    done?.();
  }

  _slideAway(element, done) {
    const finish = () => { element.classList.remove("is-leaving"); element.hidden = true; done?.(); };
    const motion = typeof matchMedia === "function" && !matchMedia("(prefers-reduced-motion: reduce)").matches;
    if (!motion || element.hidden) { finish(); return; }
    element.classList.add("is-leaving");
    setTimeout(finish, SHEET_MS);
  }

  /**
   * On the phone the content scrolls above a fixed action bar: a fade over the bar says there is more below, only
   * while there really is (it goes at the end of the scroll; on the wide layout nothing scrolls, so it never shows).
   */
  _watchMore() {
    const main = this._frame?.querySelector(".wiz-main"), actions = this._frame?.querySelector(".wiz-actions");
    if (!main || !actions) return;
    const update = () => actions.classList.toggle("has-more", main.scrollHeight - main.scrollTop - main.clientHeight > 1);
    main.addEventListener("scroll", update, { passive: true });
    this._moreObserver?.disconnect();
    if (typeof ResizeObserver === "function") {
      this._moreObserver = new ResizeObserver(update);
      this._moreObserver.observe(main);
      for (const child of main.children) this._moreObserver.observe(child);
    }
    this._moreUpdate = update;
    queueMicrotask(update);
  }

  /** Whatever changes within one screen: stepper, stopwatch, progress, notices, buttons, read-only, A-01's list. */
  _patch(screen) {
    const root = this._frame, { t } = this._context;
    queueMicrotask(() => this._moreUpdate?.());
    const picker = root.querySelector(".wiz-picker");
    if (picker && screen.picker) {
      const html = this._context.picker?.markup(screen.picker) ?? "";
      if (html !== this._pickerHtml) {
        // The list is redrawn in place: the control that had the focus (a radio, a chip, a header) gets it back.
        const active = root.getRootNode().activeElement;
        const focus = active && picker.contains(active) ? (active.dataset.wiz ? ["wiz", active.dataset.wiz] : active.name === "cover" ? ["cover", active.value]
          : active.dataset.pickSort ? ["pickSort", active.dataset.pickSort] : active.matches("[data-pick-filter]") ? ["pickFilter", ""] : null) : null;
        this._pickerHtml = html;
        // Patched in place: the rows, the scroll and the focus stay (M3-4).
        morph(picker, html);
        fitBars(picker);
        fitRows(picker);
        // X-3: a new choice only shows as selected (its notes open under it): nothing scrolls, the row stays where the
        // finger left it.
        if (focus) {
          const [kind, value] = focus;
          [...picker.querySelectorAll("button, input, select")].find((element) => (kind === "cover" ? element.name === "cover" && element.value : element.dataset[kind]) === value)
            ?.focus({ preventScroll: true });
        }
      }
    } else this._pickerHtml = null;
    const head = root.querySelector(".wiz-cover"), sub = root.querySelector(".wiz-head-sub");
    if (head && head.textContent !== screen.header.title) head.textContent = screen.header.title;
    if (sub) {
      // A cover's line as units (room, gateway, the address that never breaks); any other subtitle as plain text.
      const units = screen.header.units;
      const html = units ? [...units.lead.map((unit) => ["cal-row-unit", esc(unit)]), ...(units.bus ? [["cal-row-address", esc(units.bus)]] : [])]
        .map(([kind, part], index, all) => `<span class="${kind}">${part}${index < all.length - 1 ? " ·" : ""}</span>`).join(" ") : esc(screen.header.subtitle || "");
      if (sub._html !== html) { sub.innerHTML = html; sub._html = html; }
      sub.hidden = !screen.header.subtitle;
      sub.classList.toggle("wiz-head-placeholder", !!screen.header.placeholder);
    }
    const stepper = this._stepperMarkup(screen);
    for (const target of [root.querySelector(".wiz-steps"), root.querySelector(".wiz-sheet-steps")]) if (target.innerHTML !== stepper) target.innerHTML = stepper;
    root.querySelector(".wiz-compact-line").textContent = screen.stepper.line;
    // An ended session keeps the step it reached, greyed (M3-3).
    for (const target of [root.querySelector(".wiz-steps"), root.querySelector(".wiz-sheet-steps"), root.querySelector(".wiz-compact")]) target.toggleAttribute("data-interrupted", !!screen.stepper.interrupted);
    const watch = root.querySelector(".wiz-stopwatch");
    watch.hidden = !screen.stopwatch;
    if (screen.stopwatch) {
      root.querySelector(".wiz-stopwatch-label").textContent = screen.stopwatch.label;
      root.querySelector(".wiz-stopwatch-value").textContent = screen.stopwatch.value;
    }
    const wait = root.querySelector(".wiz-wait");
    if (wait && screen.wait) {
      // Stages done carry ✓, the current one ◐, the next ones an empty circle.
      // The bar and the time left sit under the stage in progress (after the text when there are no stages).
      const current = screen.wait.stages.findIndex((stage) => stage.state === "current");
      // L-53: a leg's label stays on one line beside its circle: its «— circa N s» is a part of its own, dropped in a
      // narrow box (the time left is under the active bar anyway); the label ellipsizes only as a last resort.
      const label = (value) => { const at = value.indexOf(" — "); return at < 0 ? `<span class="wiz-stage-label">${esc(value)}</span>`
        : `<span class="wiz-stage-label">${esc(value.slice(0, at))}<span class="wiz-stage-time"> — ${esc(value.slice(at + 3))}</span></span>`; };
      const stages = screen.wait.stages.map((stage, index) => `<li data-step-state="${stage.state}"><ha-icon icon="${STAGE_ICON[stage.state]}" aria-hidden="true"></ha-icon>${label(stage.text)}${index === current ? '<div class="wiz-wait-slot"></div>' : ""}</li>`).join("");
      const list = wait.querySelector(".wiz-wait-stages"), meter = wait.querySelector(".wiz-wait-meter");
      if (list.dataset.stages !== stages) {
        wait.append(meter);
        list.innerHTML = stages;
        list.dataset.stages = stages;
        list.querySelector(".wiz-wait-slot")?.replaceWith(meter);
      }
      list.hidden = !screen.wait.stages.length;
      // The bar is there for every run: the share already run, or moving back and forth while the duration is unknown;
      // not where nothing runs (`progress: false`, path B's preview, D-46).
      meter.hidden = screen.wait.progress === false;
      const bar = wait.querySelector(".wiz-progress"), share = screen.wait.progress?.value ?? null;
      bar.classList.toggle("is-indeterminate", share == null);
      if (share == null) bar.removeAttribute("aria-valuenow");
      else bar.setAttribute("aria-valuenow", String(Math.round(share * 100)));
      bar.querySelector("span").style.width = share == null ? "" : `${Math.round(share * 100)}%`;
      const remaining = wait.querySelector(".wiz-wait-remaining");
      remaining.textContent = screen.wait.remaining || "";
      remaining.hidden = !screen.wait.remaining;
    }
    // L-24: a countdown keeps its own text node, updated every second by `_tick` (the rest is not redrawn).
    const markup = (list) => list.map((notice) => notice.countdown
      ? `<p class="notice wiz-notice" data-tone="${notice.tone}" data-expires="${notice.countdown.expires}" data-template="${esc(notice.countdown.template)}">${esc(notice.text)}</p>`
      : `<p class="notice wiz-notice" data-tone="${notice.tone}" ${notice.tone === "error" ? 'role="alert"' : ""}>${text.units(esc(notice.text))}${notice.action
        ? ` <button type="button" class="wiz-notice-action" data-wiz="${esc(notice.action.id)}">${esc(notice.action.label)}</button>` : ""}</p>`).join("");
    const top = root.querySelector(".wiz-result-notices");
    const notices = markup(screen.notices.filter((notice) => !(top && notice.result)));
    const box = root.querySelector(".wiz-notices");
    if (box.innerHTML !== notices) box.innerHTML = notices;
    if (top) { const first = markup(screen.notices.filter((notice) => notice.result)); if (top.innerHTML !== first) top.innerHTML = first; }
    const field = root.querySelector(".wiz-field input");
    if (field) field.disabled = !!screen.field?.disabled;
    // G3-4: a refused value says why, under the field; never a silent red.
    const fieldError = root.querySelector("#wiz-field-error");
    if (fieldError) {
      const message = screen.field?.error || "";
      if (fieldError.textContent !== message) fieldError.textContent = message;
      fieldError.hidden = !message;
      // Review nit: a field whose refusal is gone (a new step, a new value) is no longer marked invalid.
      if (message) field?.setAttribute("aria-invalid", "true"); else field?.removeAttribute("aria-invalid");
    }
    // L-9: a reading the field accepts but far from the model: a yellow warning under it, never a refusal.
    const warning = root.querySelector("#wiz-field-warning");
    if (warning) {
      const band = screen.field?.band, input = root.querySelector('[name="reading_cm"]'), value = input ? parseReading(input.value) : NaN;
      const far = !!band && Number.isFinite(value) && accepted(input) && (value < band.min || value > band.max) && !fieldError?.textContent;
      const message = far ? band.warning : "";
      if (warning.textContent !== message) warning.textContent = message;
      warning.hidden = !far;
    }
    const block = screen.profileBlock?.name;
    const line = root.querySelector(".wiz-name-line");
    if (line && block && line.textContent !== block.line) line.textContent = block.line;
    root.querySelector(".wiz-name-input")?.setAttribute("aria-invalid", String(block ? !block.valid : false));
    // Why the name cannot be saved, under the field (Save stays disabled meanwhile).
    const nameError = root.querySelector(".wiz-name-error");
    if (nameError) { nameError.textContent = block?.error || ""; nameError.hidden = !block?.error; }
    // L-58b: the countdown of an armed «Conferma presa di controllo» only rewrites the button's label below: redrawing
    // the bar every second would hold the button back (the guard) and could swallow a tap.
    const { readOnlyKey, slotAction, barActions, signature: actions } = barParts(screen);
    const column = root.querySelector(".wiz-actions");
    const one = (action) => action.kind === "status" || action.kind === "state"
      ? `<p class="wiz-action status${action.kind === "state" ? " is-still" : ""}" data-wiz="${esc(action.id)}" role="status">${action.kind === "status" ? '<span class="wiz-spinner" aria-hidden="true"></span>' : ""}${esc(action.label)}</p>`
      : `<button type="button" class="wiz-action ${action.kind}" data-wiz="${esc(action.id)}"${action.note ? ` aria-describedby="wiz-note-${esc(action.id.replace(/\W/g, "-"))}"` : ""}>${esc(action.label)}</button>${action.note
      ? `<p class="wiz-action-note muted" id="wiz-note-${esc(action.id.replace(/\W/g, "-"))}">${esc(action.note)}</p>` : ""}`;
    if (actions !== this._actionsSignature) {
      // The hold protects a tap from landing on a button that just appeared or changed. A button that only goes away
      // (Stop when the motor stops by itself) holds nothing back: the end stop never flickers disabled.
      // Review P5: Save's label follows the name being typed: the same button, nothing to hold back.
      const ids = screen.actions.map((action) => `${action.id}|${action.id === "save" ? "" : action.label}|${action.kind}`);
      const readOnly = JSON.stringify(readOnlyKey);
      if (!this._actionIds || readOnly !== this._readOnlySignature || ids.some((id) => !this._actionIds.includes(id))) this._guardIfTouched();
      this._actionIds = ids;
      this._readOnlySignature = readOnly;
      const focused = column.contains(root.getRootNode().activeElement) ? root.getRootNode().activeElement.dataset.wiz : null;
      this._actionsSignature = actions;
      // L-58: a reader's bar: one warning note saying who guides the calibration, then «Prendi il controllo».
      // L-67: the only action a reader has is the primary of its bar, armed or not.
      column.innerHTML = `${screen.readOnly ? `<div class="wiz-readonly"><p class="cal-note is-warning wiz-readonly-note">${esc(screen.readOnly.text)}</p>
          ${screen.readOnly.take ? `<button type="button" class="wiz-take primary" data-wiz="take">${esc(takeLabel(screen.readOnly))}</button>` : ""}
          ${screen.readOnly.consequence ? `<p class="muted">${esc(screen.readOnly.consequence)}</p>` : ""}</div>` : ""}
        ${(() => {
          // L-119b: the slot stays out of the count of «Altre opzioni»: Stop or its finished line, the same bar.
          const { shown, more, red } = overflow(barActions);
          // L-116: the emergency Stop is the last of the actions, after a gap and a hairline (CSS), never next to ②;
          // leaving a process (outlined) follows it.
          return `${shown.map(one).join("")}${more.length ? `<div class="wiz-more"><button type="button" class="wiz-more-toggle" data-wiz="more" aria-expanded="false" aria-controls="wiz-more-list">${esc(t("wiz_T529"))}<ha-icon icon="mdi:chevron-down" aria-hidden="true"></ha-icon></button>
            <div class="wiz-more-list" id="wiz-more-list">${more.map(one).join("")}</div></div>` : ""}${slotAction ? '<div class="wiz-stop-slot"></div>' : ""}${red.map(one).join("")}`;
        })()}`;
      if (focused) column.querySelector(`[data-wiz="${focused}"]`)?.focus({ preventScroll: true });
    }
    // L-119b: the slot's content follows the step (Stop ↔ «La corsa è finita»); the slot itself is never redrawn.
    const slot = column.querySelector(".wiz-stop-slot");
    if (slot && slotAction) {
      const shownSlot = slot.firstElementChild;
      if (shownSlot?.dataset.wiz !== slotAction.id || shownSlot.textContent !== slotAction.label) {
        const focusedStop = shownSlot && root.getRootNode().activeElement === shownSlot;
        slot.classList.toggle("is-finished", slotAction.id === "stopped");
        slot.innerHTML = one(slotAction);
        if (focusedStop) slot.firstElementChild.focus?.({ preventScroll: true });
      }
    }
    // L-1: the other ways on stack under the actions; in the phone's bar they wait behind «Altre opzioni».
    const more = column.querySelector(".wiz-more");
    if (more) { more.classList.toggle("is-open", !!this._local.moreOpen); more.querySelector(".wiz-more-toggle").setAttribute("aria-expanded", String(!!this._local.moreOpen)); }
    // L-119b: within a step the bar keeps its buttons, so one that turns on (② once the motor runs, the end stop once
    // the cover arrives) is what may now sit under a finger: it is held as a new one was (L-108).
    const enabledIds = screen.actions.filter((action) => !action.disabled && action.kind !== "state" && action.id !== "stop").map((action) => action.id);
    if (this._enabledIds && enabledIds.some((id) => !this._enabledIds.includes(id))) this._guardIfTouched();
    this._enabledIds = enabledIds;
    const guarded = this._guarded();
    // L-78: from the click until Home Assistant answers the buttons are held too (one press, one message), with the
    // passing look; Stop never waits.
    const inFlight = !!this._gateway?.status.busy;
    for (const action of screen.actions) {
      const button = column.querySelector(`[data-wiz="${action.id}"]`);
      if (!button || action.kind === "status" || action.kind === "state") continue;
      // N-06/Z-13: a reading's «Fatto, avanti» waits for a value in the field: off until the user types one. Z-2: after a
      // refused value it stays off until the value typed is one the field accepts.
      const input = action.id === "reading" ? root.querySelector('[name="reading_cm"]') : null;
      const empty = !!input && (!input.value.trim() || (this._local.fieldError != null && !accepted(input)));
      button.disabled = !!action.disabled || empty || ((guarded || inFlight) && action.id !== "stop");
      // D-13 (decided 8 Oct): off only for a moment, enabling by itself (the hold after a change, a request or the motor
      // settling) keeps the light primary look; off until the user acts is the neutral grey.
      button.classList.toggle("is-settling", button.disabled && !empty && (!action.disabled || !!action.settling));
      // Disabled by the step itself (not by the short hold after a change): said to assistive technology too.
      if (action.disabled) button.setAttribute("aria-disabled", "true"); else button.removeAttribute("aria-disabled");
    }
    const take = column.querySelector('[data-wiz="take"]');
    if (take) {
      take.disabled = !!this._gateway?.status.busy || guarded; take.classList.toggle("is-settling", take.disabled);
      const label = takeLabel(screen.readOnly);
      if (take.textContent !== label) take.textContent = label;
    }
  }

  /**
   * L-108: the hold protects only a tap meant for the old bar: it follows a touch of the page in the last
   * CONFIRM_GUARD_MS, or this tab's own press (until the change that answers it, within OWN_PRESS_MS). A bar changed by
   * data alone (the owner's bar moved on by Home Assistant, a reader following, a step entered by a timer) is enabled
   * at once. The hold while a request is in flight (click → answer) is separate and unchanged.
   */
  _guardIfTouched() {
    const now = performance.now(), own = this._ownPress != null && now - this._ownPress < OWN_PRESS_MS;
    // Review N3: a start is answered before the motor runs (`starting_*`): the press still holds the bar that comes
    // with the running motor (②), still within OWN_PRESS_MS.
    const starting = /^starting_/.test(this._gateway?.view?.phase || "");
    if (own && !this._gateway?.status.busy && !starting) this._ownPress = null;
    if (own || now - (this._touchedAt ?? -Infinity) < this._guardMs) this._guard();
  }

  /** Hold the confirming buttons for CONFIRM_GUARD_MS, then draw them enabled again. */
  _guard() {
    if (!this._guardMs) return;
    this._guardUntil = performance.now() + this._guardMs;
    clearTimeout(this._guardTimer);
    const generation = this._generation;
    this._guardTimer = setTimeout(() => { if (generation === this._generation && this._frame) this._patch(this._screen()); }, this._guardMs + 10);
  }

  _guarded() { return !!this._guardMs && performance.now() < (this._guardUntil || 0); }

  _stepperMarkup(screen) {
    return `<p class="wiz-steps-line">${esc(screen.stepper.line)}</p>
      <ol>${screen.stepper.phases.map((phase) => `<li data-step-state="${phase.state}"${phase.state === "current" ? ' aria-current="step"' : ""}><span class="wiz-step-mark" aria-hidden="true"></span><span class="wiz-step-label">${esc(phase.label)}</span>
        ${phase.substeps.length ? `<ul>${phase.substeps.map((item) => `<li>${item.label ? `${esc(item.label)}: <strong>${esc(item.value)}</strong>` : esc(item.value)}</li>`).join("")}</ul>` : ""}</li>`).join("")}</ol>`;
  }

  _waitMarkup(wait) {
    return `<div class="wiz-wait" role="status">${wait.text ? `<p>${text.units(esc(wait.text))}</p>` : ""}<ol class="wiz-wait-stages"></ol>
      <div class="wiz-wait-meter"><div class="wiz-progress" role="progressbar" aria-valuemin="0" aria-valuemax="100" aria-label="${esc(this._context.t("wiz_T027"))}"><span></span></div>
      <p class="wiz-wait-remaining muted" aria-live="off" hidden></p></div></div>`;
  }

  /** A closed-by-default explanation (G2-3): a native <details>, so it opens from the keyboard and reads as a toggle. */
  _helpMarkup(help) {
    // Q-32: the panel's one disclosure (`cal-disclosure`): a blue text link with a chevron, over a short paragraph.
    return `<details class="cal-disclosure wiz-help"${this._local.helpOpen ? " open" : ""}><summary><span class="cal-disclosure-label">${esc(help.summary)}</span><ha-icon icon="mdi:chevron-down" aria-hidden="true"></ha-icon></summary>${rich(help.text)}</details>`;
  }

  _choiceMarkup(choice) {
    // G4-3: a label written as «**title**\nexplanation» (C-02) is a title and its explanation, not one flex line with a
    // break in it (the break took a whole row: 24 px between them).
    const parts = (option) => {
      if (option.plain) return [esc(option.label), ""];
      const [first, ...rest] = option.label.split("\n");
      // The bold run closes on the next line in the sheet («**title\n**explanation»): close it on the title.
      const head = /^\*\*/.test(first) && !/^\*\*.*\*\*/.test(first) ? `${first.trim()}**` : first;
      const tail = rest.join(" ").trim().replace(/^\*\*\s*/, "");
      return tail ? [inline(head), `<span class="wiz-radio-explain">${inline(tail)}</span>`] : [inline(option.label), ""];
    };
    return `<fieldset class="wiz-choice wiz-radios"><legend${choice.legendHidden ? ' class="visually-hidden"' : ""}>${esc(choice.label)}</legend>${choice.help ? this._helpMarkup(choice.help) : ""}${choice.options.map((option) =>
      `<label class="wiz-radio${option.disabled ? " is-disabled" : ""}"><input type="radio" name="${esc(choice.name)}" value="${esc(option.value)}" ${option.value === choice.value ? "checked" : ""} ${option.disabled ? "disabled" : ""}><span class="wiz-radio-text"><span class="wiz-radio-label">${parts(option)[0]}${option.badge
        ? ` <span class="badge wiz-recommended">${esc(option.badge)}</span>` : ""}</span>${option.notice
        // L-115: why an option cannot be chosen yet, a note right under its title, before what it does. L-124: blue for a
        // feature not shipped yet (`noticeTone: "info"`), yellow only for a measurement's warning; a condition the user
        // can meet is the option's plain grey caption.
        ? `<span class="cal-note${option.noticeTone === "info" ? "" : " is-warning"} wiz-radio-notice">${text.units(esc(option.notice))}</span>` : ""}${option.caption
        ? `<small class="muted wiz-radio-caption">${text.units(esc(option.caption))}</small>` : ""}${parts(option)[1]}${[].concat(option.note || []).map((line) => `<small class="muted">${text.units(esc(line))}</small>`).join("")}${option.warning
        ? `<span class="cal-note is-warning wiz-radio-warning">${text.units(esc(option.warning))}</span>` : ""}</span></label>`).join("")}</fieldset>`;
  }

  _fieldMarkup(field) {
    const value = field.value == null ? "" : `${field.value}`;
    return `<form class="wiz-field" novalidate autocomplete="off"><label>${esc(field.label)}<span class="input-suffix"><input name="reading_cm" type="text" inputmode="decimal" autocomplete="off" value="${esc(value)}" aria-describedby="wiz-field-hint wiz-field-error" data-kind="${esc(field.kind)}" data-min="${field.min}" data-max="${field.max}"><span>cm</span></span></label>
      <p id="wiz-field-hint" class="muted">${text.units(esc(field.hint))}</p><p id="wiz-field-error" class="wiz-field-error" role="alert" hidden></p>
      <p id="wiz-field-warning" class="cal-note is-warning wiz-field-warning" role="status" hidden></p></form>`;
  }

  _tableMarkup(table) {
    return `<table class="wiz-table"><thead><tr>${table.headers.map((header) => `<th scope="col">${esc(header)}</th>`).join("")}</tr></thead>
      <tbody>${table.rows.map((row) => `<tr><th scope="row">${esc(row.label)}</th><td${table.mutedBefore ? ' class="muted"' : ""}>${row.changed ? `<s>${esc(row.before)}</s>` : esc(row.before)}</td><td>${esc(row.after)}</td></tr>`).join("")}</tbody></table>`;
  }

  _technicalMarkup(technical) {
    return `<details class="wiz-accordion wiz-technical"><summary>${esc(technical.title)}<ha-icon icon="mdi:chevron-down" aria-hidden="true"></ha-icon></summary>
      <p class="muted">${esc(technical.caption)}</p>${this._tableMarkup({ headers: [this._context.t("wizTableValue"), this._context.t("wizTableBefore"), this._context.t("wizTableAfter")], rows: technical.rows })}
      ${technical.code ? `<pre class="wiz-code">${esc(technical.code)}</pre>` : ""}</details>`;
  }

  _profileMarkup(block) {
    return `<div class="wiz-profile-block">
      <label class="wiz-switch"><input type="checkbox" role="switch" name="save_profile" ${block.on ? "checked" : ""}><span>${esc(block.switchLabel)}</span></label>
      <p class="muted">${esc(block.explanation)}</p>
      ${block.choice ? `<fieldset class="wiz-radios">${block.choice.options.map((option) => `<label class="wiz-radio${option.disabled ? " is-disabled" : ""}"><input type="radio" name="save_choice" value="${esc(option.value)}" ${option.value === block.choice.value ? "checked" : ""} ${option.disabled ? "disabled" : ""}>
        <span class="wiz-radio-text"><span class="wiz-radio-label">${esc(option.label)}</span>${option.warning ? `<span class="cal-note is-warning wiz-radio-notice wiz-option-note">${esc(option.warning)}</span>` : ""}${option.description ? `<small class="muted">${esc(option.description)}</small>` : ""}</span></label>`).join("")}</fieldset>` : ""}
      ${block.name ? `<label>${esc(block.name.label)}<input class="wiz-name-input" name="profile_name" maxlength="64" autocomplete="off" value="${esc(block.name.value)}" aria-describedby="wiz-name-error wiz-name-hint"></label>
        <p id="wiz-name-error" class="wiz-name-error" role="alert" hidden></p>
        <p id="wiz-name-hint" class="muted">${esc(block.name.hint)}</p><p class="wiz-name-line">${esc(block.name.line)}</p>` : ""}</div>`;
  }

  // ---------- events ----------

  _keydown(event) {
    const dialog = this._frame.querySelector(".wiz-dialog:not([hidden])"), sheet = this._frame.querySelector(".wiz-sheet");
    const open = dialog || (!sheet.hidden ? sheet : null);
    if (!open) return;
    if (event.key === "Escape") {
      event.preventDefault();
      if (open === dialog && dialog.classList.contains("wiz-nav-dialog")) this._navAnswer(false);
      else if (open === dialog) this._closeDialog();
      else { this._slideAway(sheet); this._frame.querySelector('[data-wiz="steps"]').focus(); }
    } else if (event.key === "Tab") {
      // A modal keeps the keyboard inside it, as the native dialog would.
      const focusable = [...open.querySelectorAll("button:not([hidden]):not(:disabled)")];
      const active = this._root.getRootNode().activeElement, first = focusable[0], last = focusable.at(-1);
      if (!focusable.includes(active)) { event.preventDefault(); first?.focus(); }
      else if (event.shiftKey && active === first) { event.preventDefault(); last.focus(); }
      else if (!event.shiftKey && active === last) { event.preventDefault(); first.focus(); }
    }
  }

  _input(event) {
    if (event.target.name === "profile_name") { this._local.profileName = event.target.value.trim(); this._patch(this._screen()); }
    if (event.target.name === "reading_cm") {
      // Review P5: a value above what the field accepts (more than the curtain travel) is said while typing, as the far
      // warning is, and the button waits; Z-2: once what is typed is accepted the message goes and the button comes on.
      const value = parseReading(event.target.value);
      if (Number.isFinite(value) && value > Number(event.target.dataset.max)) this._local.fieldError = value;
      else if (this._local.fieldError != null && accepted(event.target)) { this._local.fieldError = null; event.target.removeAttribute("aria-invalid"); }
      // N-06: the button follows the field (typing never redraws the field itself).
      this._patch(this._screen());
    }
  }

  _change(event) {
    const { name, value, checked } = event.target;
    if (name === "save_profile") { this._local.saveProfile = checked; this._render(); }
    else if (name === "save_choice") { this._local.saveChoice = value; this._render(); }
    else if (name === "slats") this._local.slats = value !== "no";
    else if (name === "case") { this._local.scope = value; this._render(); }
    else if (name === "cover") { this._local.coverKey = value; this._local.coverPicked = true; this._render(); }
    else if (event.target.matches?.("select[data-pick-filter]")) { this._local.pickFilter = value; this._render(); }
    else if (event.target.dataset?.pickSort) {
      // The phone's "Sort by" bar of A-01: the list's shared order, as in the tab.
      const [key, dir] = value.split(":");
      this._context.picker?.sort(key, dir === "desc" ? "desc" : "asc");
      this._render();
    }
    else if (name === "profile") { this._local.profileId = value; this._render(); }
    else if (name === "path") { this._local.pathChoice = value; this._render(); }
  }

  _click(event) {
    // A click on a confirmation's backdrop (outside its card) is "Continue the calibration".
    if (event.target.classList?.contains("wiz-dialog")) {
      if (event.target.classList.contains("wiz-nav-dialog")) this._navAnswer(false); else this._closeDialog();
      return;
    }
    // M6-1 (R7-7 for every sheet): a tap on the Steps sheet's backdrop closes it, as its ✕ does.
    if (event.target.classList?.contains("wiz-sheet")) { this._act("steps-close"); return; }
    // L-18: a tap on the cover already chosen in A-01 un-chooses it (a radio cannot do that by itself).
    const radio = event.target.matches?.('.wiz-picker input[name="cover"]') ? event.target : null;
    if (radio && this._local.coverPicked && this._local.coverKey === radio.value) {
      radio.checked = false;
      Object.assign(this._local, { coverKey: "", coverPicked: false });
      this._render();
      return;
    }
    const button = event.target.closest("button[data-wiz]");
    if (!button || button.disabled) return;
    event.preventDefault();
    this._ownPress = performance.now();
    this._act(button.dataset.wiz);
  }

  _value(name) { return this._frame.querySelector(`[name="${name}"]:checked, select[name="${name}"]`)?.value; }

  async _act(id) {
    const local = this._local, gateway = this._gateway;
    if (id === "steps") { const sheet = this._frame.querySelector(".wiz-sheet"); sheet.hidden = false; focusOnOpen(sheet.querySelector(".wiz-sheet-card"), sheet.querySelector("button")); return; }
    if (id === "more") { local.moreOpen = !local.moreOpen; return this._render(); }
    // L-37: the optional check at the end of the base calibration: descending from the top to half the travel.
    if (id === "skip-check") { local.checkSkipped = true; gateway?.clearError?.(); this._render(); return; }
    if (id === "check-start") {
      // L-78: the target comes from the session's own view at the moment of sending: half its travel, descending from
      // the top (L-37). Without a travel there (never a NaN on the wire) the backend plans its own default check.
      const travel = gateway?.view?.travel_cm;
      return this._send("check", Number.isFinite(travel) && travel > 0 ? { direction: "closing", target_cm: travel / 2 } : {});
    }
    if (id === "steps-close") { this._slideAway(this._frame.querySelector(".wiz-sheet")); this._frame.querySelector('[data-wiz="steps"]').focus(); return; }
    if (id === "exit") { this._exit(); return; }
    if (id === "back") { this._back(); return; }
    if (id.startsWith("pick-filter:")) { local.pickFilter = id.slice(12); return this._render(); }
    // R-5: the ✕ of a confirmation shown as a sheet keeps the calibration, as «Continua la calibrazione».
    if (id === "dialog-x") { this._act(this._frame.querySelector(".wiz-nav-dialog")?.hidden === false ? "nav-stay" : "stay"); return; }
    if (id === "stay") { this._closeDialog(); return; }
    if (id === "nav-stay") { this._navAnswer(false); return; }
    if (id === "nav-later") { this._navAnswer(true); return; }
    if (id === "nav-discard") { this._navDiscard(); return; }
    if (id === "leave") { this._leave(); return; }
    if (id === "close") { this._finish(); return; }
    if (id === "choose") { if (!local.coverPicked) return; local.pathChoice = null; local.stage = "path"; return this._render(); }
    if (id === "path" || id.startsWith("path:")) {
      const path = id === "path" ? this._value("path") || local.pathChoice : id.slice(5);
      if (!["A", "B", "C"].includes(path) || (path === "B" && !this._previews)) return;
      local.path = path;
      local.stage = local.path === "A" ? "before" : local.path === "B" ? "profile" : "current";
      // Review P3: B-01 preselects the profile A-02 named as the closest by travel; none when no profile is close.
      if (local.path === "B") local.profileId = screens.pathAdvice(this._cover(), this._profiles()).similar[0]?.id ?? null;
      return this._render();
    }
    if (id === "profile") {
      local.profileId = this._value("profile") || local.profileId;
      if (!local.profileId) return;
      local.stage = "before"; return this._render();
    }
    if (id === "current") { local.stage = "case"; return this._render(); }
    if (id === "case") {
      const scope = this._value("case") || local.scope || (this._previews ? "points_only" : null);
      if (!scope || (scope === "points_only" && !this._previews)) return;
      local.scope = scope; local.stage = "before"; return this._render();
    }
    if (id === "begin") { if (local.stage === "before") local.slats = (this._value("slats") ?? (local.slats === false ? "no" : "yes")) !== "no"; return this._begin(); }
    if (id === "ack:ascent" && local.travelTyped != null) {
      const travel = local.travelTyped;
      Object.assign(local, { ackAscent: true, travelTyped: null });
      return this._send("reading", { reading_cm: travel });
    }
    if (id.startsWith("ack:")) {
      local[{ lift: "ackLift", ascent: "ackAscent", descent: "ackDescent", readings: "ackReadings" }[id.slice(4)]] = true;
      if (id === "ack:readings") return this._send("next");
      return this._render();
    }
    if (id === "reading") return this._reading();
    // G3-2: «The edge is still resting» repeats the lift-off run without a value: the backend's path of a reading under
    // 1 cm (0 cm), remembered as coming from the button for the notice of the repeat (G3-3).
    if (id === "resting") { this._local.lastGap = 0; this._local.liftOrigin = "button"; this._local.fieldError = null; return this._send("reading", { reading_cm: 0 }); }
    // The guided protocol's own movements ("open", "close") are named apart from closing the wizard.
    if (id.startsWith("run:")) return this._send(id.slice(4));
    if (id === "endpoint" && local.fullRun) {
      // L-3: ② exists only while the motor runs. A click that races the stop's view is not a confirmation: the run is
      // not valid, as if ② had never been there; otherwise the press decides the run, even across a stop in flight.
      if (gateway?.view?.motor_stopped === true && !local.fullRun.pressed) { this._closeFullRun(gateway.view); return this._render(); }
      const run = local.fullRun;
      run.pressed = true;
      // A refused ② confirmed nothing: the run is judged again as if it had not been pressed.
      return Promise.resolve(this._send("endpoint")).then(() => {
        if (local.fullRun !== run || !this._gateway?.status.error) return;
        Object.assign(run, { pressed: false, decided: false });
        this._closeFullRun(this._gateway.view);
        this._render();
      });
    }
    const open = gateway?.view;
    if (id === "repeat" && local.invalidRun && open && !open.preview && open.mode === "geometry" && ["opening", "closing"].includes(open.step)
      && ["opening", "closing", "geometry_wait_stop"].includes(open.phase)) {
      // L-3: the backend is still in the stopped run or confirming its end: the end is confirmed first (nothing moves),
      // the repeat follows on the result it reaches.
      local.repeatQueued = true;
      if (local.fullRun) this._closeFullRun(open);
      return this._render();
    }
    // D-11: the time of a run judged not valid stays out of the stepper until a new run replaces it.
    if (id === "repeat" && local.invalidRun) {
      const key = local.invalidRun.step === "opening" ? "opening_time" : "closing_time";
      local.discarded = { step: local.invalidRun.step, value: gateway?.view?.values?.[key] ?? null };
    }
    if (id === "repeat") { local.invalidRun = null; local.travelTyped = null; }
    // L-119: a run started from its own button keeps that button, off, in its bar until the step ends.
    if (id === "next" && gateway?.view?.phase === "briefing" && gateway.view.step) local.manualStart = gateway.view.step;
    if (["next", "lift", "endpoint", "repeat", "repeat_step", "accept", "repeat_check", "redo_readings", "continue_anyway", "continue", "skip"].includes(id)) return this._send(id);
    // "It did not do what it should" (A-05): today the run is stopped and S-01 offers to start over (L2 will repeat the step).
    // L-38: Stop during the positioning before a times-only measurement: Home Assistant stops the cover, nothing started.
    if ((id === "stop" || id === "misbehaved") && local.homing) {
      const entity = local.homing.entity;
      clearInterval(this._homeTimer); this._homeTimer = null; local.homing = null; local.stage = "before";
      Promise.resolve(this._context.hass.callService?.("cover", "stop_cover", { entity_id: entity })).catch(() => {});
      return this._render();
    }
    if (id === "stop" || id === "misbehaved") { local.misbehaved = id === "misbehaved"; local.stopSent = true; this._closeDialog(false); return gateway?.stop(); }
    // Ownership gained by a claim or a reconnection moves nothing until this tab presses something.
    if (id === "take") { local.arm = null; return gateway?.claim(); }
    if (id === "reconnect") { local.arm = null; return gateway?.reconnect(); }
    if (id === "save") return this._save();
    if (id === "refine") return this._previews ? this._fineTune() : undefined;
    if (id === "assign-profile") {
      // The profile just written: by its id when it was updated, by its name when it was created (the id comes with the
      // overview Home Assistant publishes after the save).
      const item = this._item(), saved = local.saved || {};
      if (!item) return;
      this._context.onAssignProfile?.({ entryId: item.entry.entry_id, profileId: saved.target === "profile_update" ? item.profile?.id ?? null : null,
        name: saved.name ?? item.profile?.name ?? null });
      return;
    }
    // L-113: the new session another device started on this cover: followed here as a reader.
    if (id === "follow-new") {
      const item = this._item(), restarted = local.restarted;
      if (!restarted || !item) return;
      // Review N1: read again right before following: a session already gone shows its end (cancelled or expired).
      let session = null;
      try {
        session = (await this._context.hass.callWS({ type: "myhome/cover_profiles/read", entry_id: item.entry.entry_id, entity_id: item.cover.entity_id }))?.calibration;
      } catch { /* As gone. */ }
      if (local.stage !== "restarted" || local.restarted !== restarted) return;
      local.restarted = null;
      const live = session && session.session_id === restarted.session_id && !["saved", "cancelled", "expired"].includes(session.phase);
      if (!live) {
        local.stage = session?.reason === "expired" || session?.phase === "expired" ? "restart-expired" : "cancelled";
        return this._render();
      }
      this._resume(session, item);
      return this._render();
    }
    if (id === "restart") {
      // G4-6: the guided session has no repeat verb: this attempt is cancelled (nothing was saved) and the times start
      // again from the briefing, with the cover to be closed.
      // R-6: only a cancel Home Assistant confirmed lets the gateway go; refused (its error shows) or not sent, the
      // session stays here, still reserving the cover for this tab, instead of being detached and left to its lease.
      const gateway = this._gateway;
      Promise.resolve(gateway?.act("cancel")).catch(() => {}).finally(() => {
        if (this._gateway !== gateway) return;
        if (gateway && (gateway.lastView || gateway.view)?.phase !== "cancelled") { this._render(); return; }
        this._dropGateway(); Object.assign(local, freshFlags(), { stage: "before" }); this._render();
      });
      return;
    }
    // L-113: restarting or cancelling is the owner's; a reader claims first (its bar has neither).
    if ((id === "abort" || id === "start-over") && gateway?.view?.read_only && !gateway.preview) return;
    if (id === "abort") { this._abortDead(); return; }
    if (id === "start-over" && !(await this._abortDead({ stay: true }))) return;
    if (id === "start-over") {
      // Fine-tuning starts again from the values just saved; everything else from "Before you begin".
      if (local.refining && local.base) { this._startRefine(local.base); return this._render(); }
      this._dropGateway(); Object.assign(local, freshFlags(), { stage: "before" }); return this._render();
    }
    if (id === "another") { this._dropGateway(); Object.assign(local, freshFlags(), { stage: "choose", entry: "choose", path: "A", scope: null, saved: null, refining: false, base: null, baseSaved: false, coverKey: "", coverPicked: false }); return this._render(); }
    if (id === "open-cover") { const entityId = this._item()?.cover.entity_id; const { onOpenCover } = this._context; this._finish(); onOpenCover?.(entityId); return; }
    // L-21: after a failed check of path B: this cover from scratch (path A), keeping the travel typed; or another profile.
    if (id === "restart-a") {
      const travel = gateway?.view?.travel_cm ?? null;
      this._dropGateway();
      Object.assign(local, freshFlags(), { path: "A", scope: null, stage: "before", keptTravel: travel });
      return this._render();
    }
    if (id === "other-profile") {
      this._dropGateway();
      Object.assign(local, freshFlags(), { path: "B", stage: "profile" });
      return this._render();
    }
    if (id === "correct") {
      // B → C: the cover is measured on its own; nothing of the preview was written.
      this._dropGateway();
      Object.assign(local, freshFlags(), { path: "C", scope: "times_and_rolls", stage: "current" });
      return this._render();
    }
  }

  /** One choice back, before any session: what was chosen stays chosen; from the first screen, back to the list. */
  _back() {
    const local = this._local;
    const to = { choose: null, path: local.entry === "path" ? null : "choose", profile: "path", current: local.entry === "current" ? null : "path", case: "current",
      before: local.path === "A" ? "path" : local.path === "B" ? "profile" : "case" }[local.stage];
    if (to === undefined) return;
    if (to === null) { this._finish(); return; }
    if (to === "path") local.pathChoice = local.path;
    local.stage = to;
    this._render();
  }

  _send(action, extra = {}) {
    if (!this._gateway) return;
    this._arm(this._gateway.view);
    // A repeated run is confirmed again on its own screen (A-12, A-15) once measured.
    if (action === "repeat" && this._current?.code.endsWith("-12")) this._local.ackAscent = false;
    if (action === "repeat" && this._current?.code.endsWith("-15")) this._local.ackDescent = false;
    return this._gateway.act(action, extra);
  }

  _reading() {
    const input = this._frame.querySelector('[name="reading_cm"]');
    if (!input) return;
    const value = parseReading(input.value), min = Number(input.dataset.min), max = Number(input.dataset.max);
    if (!Number.isFinite(value) || value < min || value > max) {
      // Outside the accepted range of an intermediate reading the screen names the range; nothing is sent.
      this._local.fieldError = Number.isFinite(value) ? value : "nan";
      this._patch(this._screen());
      const field = this._frame.querySelector('[name="reading_cm"]');
      field.setAttribute("aria-invalid", "true");
      field.focus();
      // The reason under the field stays in view above the phone's action bar.
      this._frame.querySelector("#wiz-field-error:not([hidden])")?.scrollIntoView?.({ block: "nearest" });
      return;
    }
    this._local.fieldError = null;
    // The lift-off gap as typed (G2-9): a value under the backend's 1 cm counts as still resting, and the screen of the
    // repeated run names it. The backend uses the number as sent, without rounding (0.5 is under 1).
    if (input.dataset.kind === "gap") { this._local.lastGap = value; this._local.liftOrigin = "typed"; }
    // L-13: the travel is typed first and kept here: the ascent's result (with it) comes next, and «OK, continue»
    // sends it; «Repeat the run» forgets it and nothing was sent.
    const view = this._gateway?.view;
    if (input.dataset.kind === "travel" && view && !view.preview && view.mode === "geometry" && view.step === "opening") { this._local.travelTyped = value; return this._render(); }
    return this._send("reading", { reading_cm: value });
  }

  /**
   * L-25/L-26/L-28: what this tab shows, published to the readers of the session it owns: the screen's code, the path
   * and scope (a reopened session continues on them instead of guessing from the cover), the choices made on screens
   * Home Assistant does not know (the result acknowledgements, a run judged not valid) and, once Save is pressed,
   * what is being saved (the readers' A-34).
   */
  _publish(screen) {
    const gateway = this._gateway, view = gateway?.view, local = this._local;
    if (!gateway?.show || gateway.preview || !view || view.read_only || !view.owner || local.stage !== "session") return;
    const flags = Object.fromEntries(SHARED_FLAGS.map((name) => [name, local[name] ?? null]));
    gateway.show({ code: screen.code, path: local.path, scope: local.scope ?? null, slats: local.slats !== false, local: flags,
      ...(local.pendingSave ? { saved: { target: local.pendingSave.target, name: local.pendingSave.name ?? null, orphan: local.pendingSave.orphan ?? null } } : {}) });
  }

  _save() {
    const screen = this._current, local = this._local;
    const action = screen.actions.find((item) => item.id === "save");
    if (!action || action.disabled || !this._gateway) return;
    const name = screen.profileBlock?.name?.value;
    // Path B saves no measured value: the cover follows the chosen profile (L5b).
    local.pendingSave = local.path === "B" ? { target: "follow_profile", name: null } : { target: action.target || "profile_new", name };
    // A new profile for a cover that followed another, which no other cover follows: that profile will be left alone.
    const previous = this._item()?.profile;
    if (local.pendingSave.target === "profile_new" && previous && !previous.assigned_to.some((id) => id && id !== this._item().cover.entity_id)) {
      local.pendingSave.orphan = previous.name;
    }
    this._arm(this._gateway.view);
    // L-26: the readers learn what is saved before Home Assistant closes the session.
    this._publish(screen);
    return this._gateway.save(local.pendingSave);
  }

  // ---------- sessions ----------

  _newGateway(kind, plan, values) {
    const generation = this._generation;
    const onChange = (change) => {
      if (generation !== this._generation || !this._root) return;
      // A tick may end a positioning (A-04 → A-05): a new screen is drawn whole, the same one only patched.
      if (change === "tick") {
        this._closeFullRun(this._gateway?.view);
        const screen = this._screen(); if (screen.code !== this._renderedCode) this._render(); else this._patch(screen); return;
      }
      this._viewChanged();
    };
    this._gateway = kind === "live" ? new LiveCalibrationGateway({ onChange })
      : new PreviewCalibrationGateway({ onChange, plan, values, ...(this._stageMs != null ? { stageMs: this._stageMs } : {}) });
    if (kind === "live") this._watchLease();
    return this._gateway;
  }

  _dropGateway() { clearInterval(this._leaseTimer); this._leaseTimer = null; clearInterval(this._tickTimer); this._tickTimer = null; this._gateway?.close(); this._gateway = null; this._staying = null; }

  /** The idle expiry warning counts down without any message from Home Assistant: redraw it every 15 s. */
  _watchLease() {
    // L-24: and its minutes and seconds every second, its text node only.
    clearInterval(this._tickTimer);
    this._tickTimer = setInterval(() => this._tick(), 1000);
    clearInterval(this._leaseTimer);
    this._leaseTimer = setInterval(() => {
      if (!this._root || !this._gateway?.view?.idle_expires_at) return;
      const screen = this._screen(); if (screen.code !== this._renderedCode) this._render(); else this._patch(screen);
    }, 15000);
  }

  _liveContext() {
    // Called once the gateway it serves is in place (`this._newGateway("live").start({ ...this._liveContext() })`).
    const item = this._item(), { hass, t } = this._context, generation = this._generation, gateway = this._gateway;
    return { host: this._engineHost(), hass, t, entity: { entry_id: item.entry.entry_id, entity_id: item.cover.entity_id }, revision: item.revision,
      onSaved: () => { if (generation === this._generation) this._saved(); },
      // R-2: a cancel asked to start over closes the session, not the wizard (one-shot, set by `_abortDead`). S-2: the
      // flag names the gateway it was set for, so it never swallows the cancel of a later session.
      onCancel: () => {
        if (generation !== this._generation) return;
        if (this._staying === gateway) { this._staying = null; return; }
        this._finish();
      } };
  }

  _begin() {
    const local = this._local, item = this._item();
    if (!item) return;
    this._dropGateway();
    Object.assign(local, freshFlags(), { stage: "session" });
    this._arm(null, true);
    if (local.path === "B" || (local.path === "C" && local.scope === "points_only")) {
      // Path B skips the travel measurement when the cover's travel is already saved (only the check remains).
      this._newGateway("preview", local.path === "B" ? "profile" : "rolls", { ...this._baseValues(item), skip_travel: local.path === "B" && item.cover.travel_cm > 0 }).start();
    } else {
      const guided = local.path === "C" && local.scope === "times_only";
      // L-38: the times-only session (Home Assistant's guided protocol) starts from a closed cover with no positioning
      // of its own: the panel closes the cover first, as path A does, then starts it.
      if (guided && this._home(item)) return this._render();
      this._newGateway("live").start({ ...this._liveContext(), mode: guided ? "guided" : "geometry", slats: local.slats !== false });
    }
    this._render();
  }

  /**
   * L-38: the positioning before a times-only measurement: Home Assistant closes the cover (its own close), the step
   * waits for it to be closed and still (or for its full closing time with a margin, L-121), then the session starts on its
   * confirmation screen («Check that it is fully closed»). Returns false when the cover is already closed and still.
   */
  _home(item) {
    const { hass } = this._context, entity = item.cover.entity_id, state = hass.states?.[entity];
    // Only a cover Home Assistant reports closed at a known 0 skips it: an unknown position (after a restart Home
    // Assistant discards a modelled cover's position) is positioned first, whatever its state says.
    if (state?.state === "closed" && state.attributes?.current_position === 0) return false;
    // L-121 THE RULE for every Stop the panel writes by itself (today only this one: the returns and the positionings
    // of the sessions are confirmed by the user, the previews move nothing): it is timed on the FULL run of its
    // direction (the time in use) plus its margin, never on Home Assistant's position, unless the stop status comes
    // first. A Stop after the end stop is harmless; an early one leaves the cover short of it and blocks the process.
    // (A position Home Assistant cannot know, after a restart, has been published as 0 on an open cover.)
    const closing = item.cover.effective?.closing?.value;
    const seconds = closing > 0 ? closing : null;
    this._local.homing = { entity, started: Date.now(), seconds, moved: state?.state !== "closed" };
    Promise.resolve(hass.callService?.("cover", "close_cover", { entity_id: entity })).catch(() => {});
    clearInterval(this._homeTimer);
    this._homeTimer = setInterval(() => this._homeTick(), 500);
    return true;
  }

  _homeTick() {
    const local = this._local, homing = local.homing;
    if (!homing || !this._root) { clearInterval(this._homeTimer); this._homeTimer = null; return; }
    const state = this._context.hass.states?.[homing.entity], elapsed = (Date.now() - homing.started) / 1000;
    const still = state && !["closing", "opening"].includes(state.state);
    // Review nit: a cover already shown «closed» (at an unknown position) is still «closed» before its run starts:
    // that «closed» counts only once Home Assistant has reported the close under way; otherwise the run's time decides.
    if (state && ["closing", "opening"].includes(state.state)) homing.moved = true;
    // L-120: the close is over at the earliest of its stop status and its estimated closing time (a MyHOMEServer1
    // reports the stop of a run to the end stop only at the actuator's timer, about 60 s). Home Assistant still shows
    // the cover closing until that status, and a session does not start on a moving cover: the panel then writes the
    // Stop, as path A's confirmed end stop does, and the session starts once the cover is still, on C-11 («check that
    // it is fully closed and still»), where nothing moves before the user's press. L-120b: an action on the motor, so
    // the estimate keeps a margin (`homeStopAt`, L-120c: max(4 s, 25 %)).
    // N-3: no closing time to wait for (none known): two minutes, then the session starts on «check that it is fully
    // closed», where the user confirms it, rather than a positioning that never ends for a cover that never moves.
    const estimated = homing.seconds > 0 && elapsed >= screens.homeStopAt(homing.seconds);
    if (estimated && state && !still && !homing.stopSent) {
      homing.stopSent = true;
      Promise.resolve(this._context.hass.callService?.("cover", "stop_cover", { entity_id: homing.entity })).catch(() => {});
    }
    const done = (still && state.state === "closed" && homing.moved) || (estimated && still) || (homing.seconds == null && elapsed >= HOME_FALLBACK_S && still);
    if (!done) { this._patch(this._screen()); return; }
    clearInterval(this._homeTimer); this._homeTimer = null;
    local.homing = null;
    this._newGateway("live").start({ ...this._liveContext(), mode: "guided" });
    this._render();
  }

  _resume(session, item) {
    const local = this._local;
    if (item) local.coverKey = key(item);
    // The view has no path: a slat and roll measurement of a cover that already follows a profile is the
    // correction "remeasure everything" (C), of any other cover the first of its type (A).
    // L-28: the path and the choices made come from the session (published by the tab that guided it): a path A on a
    // cover that already follows a profile stays a path A, with its A-34 and its offer of fine-tuning. Only a session
    // that never published them falls back on a guess from the cover.
    const shown = session.screen?.path ? session.screen : null;
    const correction = session.mode === "geometry" && !!item?.profile, timesOnly = session.mode === "guided";
    Object.assign(local, freshFlags(), { stage: "session", path: shown ? shown.path : correction || timesOnly ? "C" : "A",
      scope: shown ? shown.scope ?? null : timesOnly ? "times_only" : correction ? "times_and_rolls" : null, slats: session.slats !== false },
      shown ? Object.fromEntries(SHARED_FLAGS.filter((name) => shown.local?.[name] != null).map((name) => [name, shown.local[name]])) : {});
    this._newGateway("live").resume({ ...this._liveContext(), session });
  }

  /**
   * L-113: a session of this tab's gateway changed (L-107's event). A tab left on «annullata da un altro dispositivo»
   * reads its cover: a new session on it, not the one that ended, means the other device started again: the tab says
   * so and offers to follow it, instead of a dead end. A plain cancel (no new session) keeps its screen.
   */
  async gatewaySession(entryId) {
    const local = this._local, item = this._item(), context = this._context;
    if (!this._root || local.stage !== "cancelled" || !item || item.entry.entry_id !== entryId) return;
    // Review N1: only a session started soon after ours ended, while this tab stayed on that screen, is a restart; a
    // calibration started later (another device, hours after) is not this one going on.
    if (Date.now() - (local.endedAt ?? 0) > RESTART_WINDOW_MS) return;
    const generation = this._generation;
    let session = null;
    try {
      session = (await context.hass.callWS({ type: "myhome/cover_profiles/read", entry_id: entryId, entity_id: item.cover.entity_id }))?.calibration;
    } catch { return; }
    if (generation !== this._generation || this._local.stage !== "cancelled" || !session) return;
    if (["saved", "cancelled"].includes(session.phase) || session.session_id === local.endedSession || session.entity_id !== item.cover.entity_id) return;
    local.stage = "restarted";
    local.restarted = session;
    this._render();
  }

  _baseValues(item) {
    const effective = item.cover.effective || {};
    return { opening_time: effective.opening?.value, closing_time: effective.closing?.value, slat_time_s: effective.slat_time_s?.value ?? 0,
      opening_roll: effective.opening_roll?.value ?? null, closing_roll: effective.closing_roll?.value ?? null, travel_cm: item.cover.travel_cm ?? null };
  }

  _baseFromCover(item) {
    const values = this._baseValues(item);
    return { opening: values.opening_time, closing: values.closing_time, slat: values.slat_time_s, travel: values.travel_cm,
      opening_roll: values.opening_roll, closing_roll: values.closing_roll };
  }

  /**
   * "Continue with fine-tuning" on A-34: the base calibration is saved, so fine-tuning (A-22…A-34, quarter readings and
   * the check) starts at once on the values just saved, in the same wizard, the stepper at Fine-tuning. Its end says
   * that the calibration before it was saved (T322).
   */
  _fineTune() {
    const local = this._local, base = local.base;
    if (!base || !this._item()) return;
    local.saved = null;
    local.baseSaved = true;
    this._startRefine(base);
    this._render();
  }

  /** Fine-tuning A-22…A-34 on `base`: simulated until the backend offers it (L5c). */
  _startRefine(base) {
    const local = this._local;
    this._dropGateway();
    // The readings of the calibration it refines stay for the stepper.
    Object.assign(local, freshFlags(), { stage: "session", refining: true, base, baseReadings: local.baseReadings });
    this._newGateway("preview", "refine", { opening_time: base.opening, closing_time: base.closing, slat_time_s: base.slat ?? 0,
      opening_roll: base.opening_roll, closing_roll: base.closing_roll, travel_cm: base.travel }).start();
  }

  _viewChanged() {
    const view = this._gateway?.view, local = this._local;
    // L-52/L-103: a reader that takes control continues on the step its former owner showed (published with `show`),
    // not on the last transition of Home Assistant: the owner's path and choices become this tab's own. Home Assistant
    // drops the old owner's screen on a claim (the new owner's view has none), so a reader keeps the last screen it
    // was shown and adopts it whole (a choice the owner had not made is not made here either). A reader never shown one
    // (an owner that published nothing) keeps what it judged from Home Assistant's views while it followed.
    if (view && !view.preview) {
      if (view.read_only) {
        // Review S1: control went to another device: a question asked as owner («Esci senza salvare?», «Calibrazione in
        // corso») no longer applies; it closes, and the panel stays where it was.
        if (this._navResolve) this._navAnswer(false);
        if (this._frame?.querySelector(".wiz-dialog:not(.wiz-nav-dialog):not([hidden])")) this._closeDialog(false);
        local.following = true;
        if (view.screen) this._followed = { gateway: this._gateway, screen: view.screen };
      } else if (local.following && view.owner) {
        local.following = false;
        const shown = view.screen || (this._followed?.gateway === this._gateway ? this._followed.screen : null);
        this._followed = null;
        if (shown) {
          const fresh = freshFlags();
          Object.assign(local, shown.path ? { path: shown.path, scope: shown.scope ?? null } : {},
            Object.fromEntries(SHARED_FLAGS.map((name) => [name, shown.local?.[name] ?? fresh[name]])));
        }
      }
    }
    // L-40: the session was cancelled by the tab or device that guided it: for a reader it is over, said as such, once.
    // The gateway is closed (no more heartbeats against a session that is gone, so no «expired» and no flicker).
    if (view && !view.preview && view.read_only && view.phase === "cancelled") {
      this._dropGateway();
      local.stage = "cancelled";
      // L-113: the session that ended, to tell a restart (a new session on this cover) from a plain cancel.
      local.endedSession = view.session_id ?? null;
      local.endedAt = Date.now();
      this._render();
      return;
    }
    // A refused value belongs to the step it was typed for; a refusal from Home Assistant to the phase it came in.
    if (view?.sequence !== local.fieldSequence) { local.fieldError = null; local.fieldSequence = view?.sequence; }
    if (view && view.phase !== local.errorPhase) { if (local.errorPhase !== undefined) this._gateway.clearError?.(); local.errorPhase = view.phase; }
    if (view?.phase === "review" && !view.preview) {
      local.review = { opening: view.values?.opening_time, closing: view.values?.closing_time, slat: view.geometry?.slat_time_s ?? null,
        travel: view.mode === "geometry" ? view.travel_cm : null, opening_roll: view.geometry?.opening_roll ?? null, closing_roll: view.geometry?.closing_roll ?? null,
        lift: view.samples?.lift ?? null, gap: view.readings?.gap ?? null };
      local.baseReadings = { ...view.readings };
    }
    if (view?.preview && view.phase === "saved") { this._saved(view.saved); return; }
    if (view?.preview && view.phase === "cancelled") { this._finish(); return; }
    this._trackRun(view);
    this._trackFullRun(view);
    // L-14: the returns before the tape readings no longer confirm their end stop by themselves: the user confirms
    // it, and the next run starts from that press.
    this._closeFullRun(view);
    this._queuedRepeat(view);
    // L-119 «same step, same bar»: how the step's run starts (by itself after a press, or from its own button) is
    // decided once per step and keeps its bar for the whole step; a view read from another device decides it anew.
    if ((view?.step ?? null) !== local.barStep || view?.read_only) Object.assign(local, { barStep: view?.step ?? null, autoStart: null, manualStart: null });
    this._autoNext(view);
    this._unloadGuard(this._liveOpen());
    this._render();
  }

  /**
   * A positioning run to an end stop (`home`, `reset`, `top`) lasts the share of the travel still ahead: the cover's
   * estimated position (Home Assistant's `current_position`) is read once, when the run starts.
   */
  _trackRun(view) {
    const local = this._local;
    const run = !!view && !view.preview && ["home", "reset", "top"].includes(view.step)
      && ["starting_close", "starting_open", "closing", "opening"].includes(view.phase);
    if (!run) { local.run = null; return; }
    if (local.run?.step === view.step) return;
    // L-121: the position counts only when Home Assistant knows it and it agrees with the state; otherwise (unknown,
    // or 0 on an open cover, 100 on a closed one) the run lasts its full time.
    const position = knownPosition(this._context.hass.states?.[this._item()?.cover.entity_id]);
    // Who owned the run when it started, and on which subscription: an automatic end stop needs both unchanged.
    local.run = { step: view.step, position, attachment: view.attachment ?? null,
      owned: !!view.owner && !view.read_only, sent: false };
  }

  /** The full ascent and descent (A-11, A-14 and their C clones): who owned the run when it started, on which subscription. */
  _trackFullRun(view) {
    const local = this._local;
    // The guided times-only runs (G4-6) are full runs too: their direction is their step.
    const step = view?.mode === "guided" ? (["starting_open", "opening"].includes(view.phase) ? "opening" : ["starting_close", "closing"].includes(view.phase) ? "closing" : null)
      : view?.step;
    const full = !!view && !view.preview && ["geometry", "guided"].includes(view.mode)
      && ((step === "opening" && ["starting_open", "opening"].includes(view.phase)) || (step === "closing" && ["starting_close", "closing"].includes(view.phase)));
    if (!full) { local.fullRun = null; return; }
    if (local.fullRun?.step === step) return;
    // `pressed`: ② was pressed in this tab while the motor ran; `sent`: the end of a run not valid was confirmed.
    local.fullRun = { step, pressed: false, decided: false, sent: false };
    local.invalidRun = null;
  }

  /**
   * How a full run ended by the bus is judged (R8-7, L-3): ② exists only while the motor runs. A press before the stop
   * reached this tab gives the run's time (the backend takes the frame's time for a press that crossed it); a stop
   * before any press (a wall switch, any stop) or at an actuator's timer (59-65 s) makes the result not valid at once.
   * Only the view decides, so a tab that opened, resumed or claimed the session after the stop judges it the same way.
   */
  _judgeFullRun(view) {
    const run = this._local.fullRun;
    if (!run || run.decided || view?.motor_stopped !== true || view.stopped_elapsed == null) return null;
    const verdict = stopVerdict(view.stopped_elapsed);
    return verdict === "timer" ? "timer" : run.pressed ? "valid" : verdict;
  }

  /**
   * A run judged not valid is shown as such by every tab, a reader's too. Its end stop is then confirmed by whichever
   * tab owns the session now (Home Assistant records the frame's time; nothing moves, the motor has already stopped),
   * so the result screen can offer only «Ripeti la corsa». Never after a lost connection, an error or with a request
   * pending; once per run.
   */
  _closeFullRun(view) {
    const local = this._local, run = local.fullRun, status = this._gateway?.status || {};
    if (!run) return;
    const verdict = this._judgeFullRun(view);
    if (verdict) { run.decided = true; local.invalidRun = verdict === "valid" ? null : { step: run.step, reason: verdict }; }
    if (!local.invalidRun || run.pressed || run.sent || view?.motor_stopped !== true) return;
    if (!view.owner || view.read_only || status.lost || status.error || status.busy) return;
    run.sent = true;
    queueMicrotask(() => {
      if (this._gateway?.view === view && !this._gateway.status.busy) this._send("endpoint"); else run.sent = false;
    });
  }

  /** «Ripeti la corsa» pressed while the stopped run was still open: sent on the result its confirmed end reaches. */
  _queuedRepeat(view) {
    const local = this._local, status = this._gateway?.status || {};
    if (!local.repeatQueued || !view) return;
    const result = view.mode === "geometry" && ((view.step === "opening" && view.phase === "reading") || (view.step === "half_open" && view.phase === "briefing"));
    const guided = view.mode === "guided" && ["confirm_open", "review"].includes(view.phase);
    if (!result && !guided) {
      if (!["opening", "closing", "geometry_wait_stop"].includes(view.phase)) local.repeatQueued = false;
      return;
    }
    local.repeatQueued = false;
    if (!view.owner || view.read_only || status.lost || status.error) return;
    queueMicrotask(() => { if (this._gateway?.view === view && !this._gateway.status.busy) this._act(guided ? "restart" : "repeat"); });
  }

  /** A press of this tab arms one automatic `next`: it remembers the view it was made on and its subscription. */
  _arm(view, start = false) {
    this._local.arm = { sequence: view?.sequence ?? -1, attachment: view?.attachment ?? null, start };
  }

  /**
   * Movements the mockups show as automatic after a press: the return to a known end stop and the runs of the tape
   * readings. THE RULE: an automatic verb is sent only on the view that immediately follows a press of the user in
   * this tab (through the movement and the stop that press started, never past a stop that waits for the user),
   * once per `sequence`, and never after a replayed subscription (a new `attachment`: Home Assistant reconnected),
   * a reconnection, a claim, a lost connection, a refused verb, or a view this tab does not own.
   */
  _autoNext(view) {
    const local = this._local, arm = local.arm, status = this._gateway?.status || {};
    if (!arm || !view || view.preview) return;
    // A start has no view yet: the first one it receives names its subscription.
    if (arm.start && arm.attachment === null && view.attachment) arm.attachment = view.attachment;
    const stale = view.mode !== "geometry" || !view.owner || view.read_only || status.lost || status.error
      || (arm.attachment !== null && view.attachment !== arm.attachment);
    if (stale) { local.arm = null; return; }
    if (view.sequence <= arm.sequence) return;
    if (view.phase !== "briefing") {
      // Still moving or stopping because of that press; anything else is a stop that waits for the user.
      if (!TRANSIT.includes(view.phase)) local.arm = null;
      return;
    }
    // L-44: the check's run to its target starts on the confirmation of its end stop, as the half readings' runs do.
    const due = (view.step === "home" && arm.start) || (view.step === "reset" && !view.gap_warning) || view.step === "top"
      || (["half_open", "half_close"].includes(view.step) && local.ackReadings) || (view.step === "check" && !!view.check);
    // L-119: a run about to start by itself shows its own bar (off) from this briefing on, not the start button.
    if (due) local.autoStart = view.step;
    // The answer to the press arrives while it is still pending: decide once it has settled.
    if (status.busy) return;
    local.arm = null;
    if (!due) return;
    queueMicrotask(() => { if (this._gateway?.view === view) this._gateway.act("next"); });
  }

  _saved(previewSaved) {
    const local = this._local, view = this._gateway?.view ?? this._gateway?.lastView;
    // L-26: a reader's A-34 is the owner's: what was saved comes from the session (published before Save), and the
    // owner's path; only «Chiudi» is offered there.
    const reader = !!view?.read_only && !view.preview;
    if (reader && view.screen?.path) Object.assign(local, { path: view.screen.path, scope: view.screen.scope ?? null });
    if (reader) local.pendingSave = view.screen?.saved ? { ...view.screen.saved } : null;
    const target = previewSaved?.target ?? local.pendingSave?.target;
    // The saved screen still shows what the steps measured, and whether the last part was a preview.
    // D-47: path B's stepper keeps the travel typed in B-04 too.
    if (this._gateway?.preview) local.fineReadings = { ...(this._gateway.view?.readings || {}), check: this._gateway.view?.check ?? null, travel: this._gateway.view?.travel_cm ?? null };
    local.saved = { target, name: previewSaved?.name ?? local.pendingSave?.name ?? null, travel_cm: this._gateway?.view?.travel_cm ?? local.review?.travel ?? null,
      preview: !!this._gateway?.preview, orphan: this._gateway?.preview ? null : local.pendingSave?.orphan ?? null, reader,
      // L-45: the check's result stays on A-34 as a tile.
      check: !this._gateway?.preview && view?.check ? { ...view.check } : null, threshold: view?.check_threshold_cm ?? null };
    if (local.path === "B") local.saved.target = "follow_profile";
    local.stage = "saved";
    if (!local.refining && local.review) local.base = { ...local.review };
    this._dropGateway();
    this._render();
  }

  // ---------- leaving ----------

  /** A live session this tab owns and that is still open: leaving the tab leaves it open in Home Assistant. */
  _liveOpen() {
    return !!this._gateway && !this._gateway.preview && this._sessionOpen() && !!this._gateway.view?.owner;
  }

  /**
   * The panel asks before it moves away from the tab (another tab, gateway, category, filter): with a live session
   * open, "Calibration in progress" offers to stay, to leave it open and resume it later, or to cancel it. Returns
   * null when there is nothing to ask, otherwise a promise of whether the panel may leave.
   */
  beforeLeave() {
    if (!this._root || !this._liveOpen()) return null;
    if (this._navResolve) return this._navPromise;
    const dialog = this._frame.querySelector(".wiz-nav-dialog"), { t } = this._context;
    // How long Home Assistant keeps it open for its owner (30 minutes at rest, 10 after a movement).
    const seconds = this._gateway.view?.recovery_seconds;
    const minutes = Math.max(1, Math.round((Number.isFinite(seconds) ? seconds : IDLE_LEASE_SECONDS) / 60));
    dialog.querySelector("#wiz-nav-body").textContent = text.fill(t("wiz_T409"), { minutes, tab: t("calTabTab") });
    this._closeDialog(false);
    dialog.querySelector(".wiz-dialog-x").hidden = !layout.sheets();
    dialog.hidden = false;
    this._root.classList.add("has-dialog");
    focusOnOpen(dialog.querySelector(".wiz-dialog-card"), dialog.querySelector('[data-wiz="nav-stay"]'));
    this._navPromise = new Promise((resolve) => { this._navResolve = resolve; });
    return this._navPromise;
  }

  _navAnswer(leave) {
    const resolve = this._navResolve;
    this._navResolve = null;
    const dialog = this._frame?.querySelector(".wiz-nav-dialog");
    if (dialog && !dialog.hidden) {
      this._root.classList.remove("has-dialog");
      if (leave) dialog.hidden = true;
      else this._hideDialog(dialog, () => { this._render(); this._frame?.querySelector(".wiz-actions .wiz-action.primary:not(:disabled)")?.focus(); });
    }
    resolve?.(leave);
  }

  /** "Leave without saving": Cancel must reach Home Assistant before the panel moves on. */
  async _navDiscard() {
    const gateway = this._gateway;
    if (!gateway) { this._navAnswer(true); return; }
    const discarded = this._discarded(gateway);
    await gateway.cancel();
    if (this._gateway === gateway && gateway.status.cancelPending) { this._navAnswer(false); this._render(); return; }
    discarded();
    this._navAnswer(true);
  }

  /** The browser's own warning before the page closes or reloads while this tab owns an open session. */
  _unloadGuard(on) {
    if (on && !this._unload) {
      this._unload = (event) => { event.preventDefault(); event.returnValue = ""; };
      window.addEventListener("beforeunload", this._unload);
    } else if (!on && this._unload) {
      window.removeEventListener("beforeunload", this._unload);
      this._unload = null;
    }
  }

  _sessionOpen() {
    const view = this._gateway?.view;
    // A live start still waiting for its first view may already have created the session: leaving it asks too.
    if (this._gateway && !this._gateway.preview && !view && this._local.stage === "session" && !this._gateway.status.error) return true;
    return !!view && !["interrupted", "cancelled", "saved"].includes(view.phase) && !(view.read_only && !view.preview);
  }

  _exit() {
    // L-10: an interrupted session still open in Home Assistant is cancelled on the way out, not left holding the cover.
    const view = this._gateway?.view;
    // L-113: a reader's exit only stops following (it never cancels another device's session).
    if (view?.read_only && !this._gateway.preview) { this._finish(); return; }
    if (this._gateway && !this._gateway.preview && view?.phase === "interrupted" && view.recoverable) { this._abortDead(); return; }
    if (STAGES_BEFORE_SESSION.includes(this._local.stage) || !this._sessionOpen()) { this._finish(); return; }
    const dialog = this._frame.querySelector(".wiz-dialog"), { t } = this._context;
    dialog.querySelector("#wiz-dialog-body").textContent = t(this._gateway.preview ? "wiz_T497" : "wizExitBodyLive");
    // R-5: a sheet (window under 720 × 500 px) has its ✕ in the title row.
    dialog.querySelector(".wiz-dialog-x").hidden = !layout.sheets();
    dialog.hidden = false;
    this._root.classList.add("has-dialog");
    focusOnOpen(dialog.querySelector(".wiz-dialog-card"), dialog.querySelector('[data-wiz="stay"]'));
  }

  _closeDialog(focus = true) {
    const dialog = this._frame?.querySelector(".wiz-dialog:not([hidden])");
    if (!dialog || dialog.classList.contains("is-leaving")) return;
    this._root.classList.remove("has-dialog");
    this._hideDialog(dialog, () => {
      if (!this._frame) return;
      this._render();
      // Back to something visible on every width: the screen's first enabled confirming button, or its title.
      if (focus) (this._frame.querySelector(".wiz-actions .wiz-action.primary:not(:disabled)") || this._frame.querySelector(".wiz-title"))?.focus();
    });
  }

  /** "Exit without saving": Cancel must reach Home Assistant; the view closes only once it has. */
  async _leave() {
    this._closeDialog(false);
    const gateway = this._gateway;
    if (!gateway) { this._finish(); return; }
    if (gateway.preview) { this._finish(); return; }
    // Review S1 (L-113): a tab that lost control while «Esci senza salvare?» was open only stops following: it never
    // takes control back to cancel the session another device now guides.
    if (gateway.view?.read_only) { this._finish(); return; }
    const discarded = this._discarded(gateway);
    // L-10: an owner's interrupted session too (`abort` takes control first only where it must).
    await gateway.abort();
    if (gateway.status.cancelPending || gateway.status.error) { if (this._gateway === gateway) this._render(); return; }
    discarded();
  }

  /**
   * L-10: an interrupted session still open in Home Assistant (it holds the cover) is cancelled, taking control when
   * this tab only reads it. `stay`: the wizard stays open (Start over); otherwise it closes once cancelled. Returns
   * whether the session is gone.
   */
  async _abortDead({ stay = false } = {}) {
    const gateway = this._gateway, view = gateway?.view;
    if (!gateway || gateway.preview || view?.phase !== "interrupted" || !view.recoverable) return true;
    const discarded = this._discarded(gateway);
    // Staying: the view's own close after Cancel must not close the wizard. R-2: a one-shot flag, not a new generation,
    // so a refused cancel leaves every callback of this gateway alive.
    if (stay) this._staying = gateway;
    await gateway.abort();
    if (gateway.status.cancelPending || gateway.status.error) { if (this._staying === gateway) this._staying = null; if (this._gateway === gateway) this._render(); return false; }
    discarded();
    return true;
  }

  /** U-10: once the cancel is confirmed the list shows the gateway free at once (taken before the view closes). */
  _discarded(gateway) {
    const entryId = this._item()?.entry.entry_id, { onDiscarded } = this._context || {};
    return () => { if (entryId && !gateway.preview) onDiscarded?.(entryId); };
  }

  _finish() {
    const { onClose } = this._context || {};
    this.close();
    onClose?.();
  }
}

/** L-58b: «Conferma presa di controllo · 8 s» while armed, the plain label otherwise. */
function takeLabel(readOnly) { return readOnly.left ? `${readOnly.take} · ${readOnly.left} s` : readOnly.take; }

function key(item) { return JSON.stringify([item.entry.entry_id, item.cover.entity_id]); }

/** L-117: every element that may scroll the wizard (its ancestors through shadow roots, and the page itself). */
function scrollParents(element) {
  const found = [];
  for (let node = element?.parentElement || element?.getRootNode?.().host; node; node = node.parentElement || node.getRootNode?.().host) found.push(node);
  const page = element?.ownerDocument?.scrollingElement;
  if (page && !found.includes(page)) found.push(page);
  return found;
}

/** The red actions: the emergency (Stop, filled) and abandoning a process (Cancel the measurement, Exit without saving: outlined). */
const RED_KINDS = ["danger", "danger-outline", "stop"];
/**
 * The bar of a screen: its actions, Stop's slot apart, and what decides that it must be drawn again. L-119b: Stop and
 * the finished line that replaces it share one slot (`.wiz-stop-slot`, the action's height, the gap and the hairline
 * above it): the swap changes the slot's content only, never the bar's layout. L-58b: an armed «Conferma presa di
 * controllo» counts down without redrawing the bar.
 */
function barParts(screen) {
  const readOnlyKey = screen.readOnly && { ...screen.readOnly, left: undefined };
  const slotAction = screen.actions.find((action) => STOP_SLOT.includes(action.id));
  const barActions = screen.actions.filter((action) => !STOP_SLOT.includes(action.id));
  return { readOnlyKey, slotAction, barActions,
    signature: JSON.stringify([barActions.map((action) => [action.id, action.label, action.kind, action.note, !!action.visible]), !!slotAction, readOnlyKey]) };
}
/**
 * L-121: the cover's position from Home Assistant, only when it is a number from 0 to 100 that agrees with the state;
 * null otherwise (unknown after a restart, or 0 on an open cover and 100 on a closed one): never taken as 0.
 */
export function knownPosition(state) {
  const position = state?.attributes?.current_position;
  if (typeof position !== "number" || !Number.isFinite(position) || position < 0 || position > 100) return null;
  if ((position === 0 && state.state === "open") || (position === 100 && state.state === "closed")) return null;
  return position;
}
/** L-119b: the ids drawn in the bar's one Stop slot. */
const STOP_SLOT = ["stop", "stopped"];
/**
 * L-47 (revised), the rule of every wizard bar of one column: at most three actions in view, the primary and up to two
 * secondaries; with more, the primary alone and the others under «Altre opzioni ▾» (blue links, the only text-style
 * actions). A red action is always in view, after them, never in the disclosure. The columns show every action (CSS).
 */
export function overflow(actions) {
  const red = actions.filter((action) => RED_KINDS.includes(action.kind)), others = actions.filter((action) => !RED_KINDS.includes(action.kind));
  if (others.length <= 3) return { shown: others, more: [], red };
  // The primary stays visible, with the actions a screen marks `visible` (L-125), three at most; the rest fold.
  const shown = others.filter((action, index) => index === 0 || action.visible).slice(0, 3);
  return { shown, more: others.filter((action) => !shown.includes(action)), red };
}

/** Z-2: a value the reading field accepts (a number within its own limits). */
function accepted(input) {
  const value = parseReading(input.value), min = Number(input.dataset.min), max = Number(input.dataset.max);
  return Number.isFinite(value) && value >= min && value <= max;
}

/**
 * Everything a measurement leaves behind, cleared for a new session or «Start over»: acknowledgements, runs, the
 * lift-off value sent (G2-9), readings kept for the stepper, fine-tuning readings, the review and the step reached.
 * Fine-tuning keeps its base on purpose (`_startRefine` passes it again).
 */
function freshFlags() {
  return { ackLift: false, ackAscent: false, ackDescent: false, ackReadings: false, arm: null, run: null, fullRun: null, invalidRun: null, repeatQueued: false, moreOpen: false, travelTyped: null, discarded: null, misbehaved: false, stopSent: false, barStep: null, autoStart: null, manualStart: null,
    review: null, pendingSave: null, fieldError: null, fieldSequence: undefined, errorPhase: undefined, saveProfile: undefined, saveChoice: undefined,
    profileName: undefined, refining: false, lastGap: null, liftOrigin: null, baseReadings: null, fineReadings: null, pathChoice: null, reachedPhase: null };
}

export { slug };
