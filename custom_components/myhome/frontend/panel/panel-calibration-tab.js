/** WHO 2 "Calibration" view: what each timed cover uses, its measurement state and the ways to measure it. */
const asset = (name) => { const url = new URL(name, import.meta.url); url.search = new URL(import.meta.url).search; return url.href; };
const [{ escapeHtml: esc }, model, text, { CoverCalibration, checkedCalibrationClient }, { CalibrationWizard }, { morph }, { layout, SHEETS, NARROW, PHONE, SHORT, dragOverlay, listBand, contentWidth, fitBars, fitRows, keepScrollOnPick, focusOnOpen, watchEdges, slideBlock, slideDetails, slideHeight, untickOnSecondTap, hiddenByStyle, closeOnOutsidePress, releaseFocus, closeOnScrim, holdScrimScroll, lockScroll, returnFocus, revealOpened, followKeyboard, markCutChips, followCutChips, fitStatus }, { cancelSession, CHECK_THRESHOLD_CM }, { travelText }, { sublineMarkup, errorText: assignError }] = await Promise.all([
  import(asset("panel-dom.js")), import(asset("panel-model.js")), import(asset("panel-calibration-text.js")),
  import(asset("panel-cover-calibration.js")), import(asset("panel-calibration-wizard.js")),
  import(asset("panel-calibration-morph.js")), import(asset("panel-calibration-layout.js")), import(asset("panel-calibration-gateway.js")),
  import(asset("panel-calibration-screens.js")), import(asset("panel-profile-assign.js")),
]);
const { fill, rich, number, dateText, names, followedBy } = text;

const MEASURED = ["guided", "automatic"];
/** One icon per action button everywhere: the stopwatch measures times, the ruler refines. */
// «Perfeziona» carries `mdi:arrow-expand-vertical` (`text.REFINE_ICON`).
export const ICON = Object.freeze({ calibrate: "mdi:timer-outline", refine: text.REFINE_ICON });
/** How long a flow's dialog takes to fade away (its CSS animation uses the same). */
const FLOW_OUT_MS = 160;
/** How long the tendina takes to slide down (its CSS animation uses the same). */
const SHEET_OUT_MS = 220;
/** The ids of the history entries of what opens over the list (one at a time). */
let layerCount = 0;
/**
 * The action of an open session says what it will do. Home Assistant gives it back to the tab that owns it
 * (after a reload, or once the wizard was closed with the session left running): «Riprendi sessione»; any other tab
 * or device joins as a reader, with Take control: «Segui sessione».
 */
const sessionLabel = (session) => (session?.client_owner ? "wiz_T580" : "wiz_T579");
/** In a row its one word, «Riprendi» / «Segui» (the «In corso» chip beside it says what); the whole label in its title. */
const sessionShort = (session) => (session?.client_owner ? "calTabRowResume" : "calTabRowFollow");
export const FILTERS = ["all", "to_measure", "calibrated", "profile"];
// Covers read in turn to find the gateway's session when a read fails.
const SESSION_PROBES = 3;
/** A burst of session transitions within this time makes one read of the list. */
export const SESSION_READ_MS = 250;
export const SORT_KEYS = ["cover", "status", "profile"];
/** The Assign profile dialog's "No profile" entry (a profile id is never this). */
const NO_PROFILE = "__no_profile__";
export const DEFAULT_SORT = Object.freeze({ key: "cover", dir: "asc" });
export const SORT_STORAGE_KEY = "myhome-panel-calibration-sort-v1";
// Status from what needs attention first; a running cover is always on top, whatever the order.
// To calibrate, calibrated (measured times, no check kept), verified (a check kept at the last save).
// A basic estimate between the two (calibrated, its rolls not measured).
const STATUS_RANK = { to_measure: 0, basic: 1, profile: 1.5, calibrated: 2, verified: 3 };

function savedSort() {
  try {
    const saved = JSON.parse(window.localStorage.getItem(SORT_STORAGE_KEY));
    if (SORT_KEYS.includes(saved?.key) && ["asc", "desc"].includes(saved?.dir)) return { key: saved.key, dir: saved.dir };
  } catch { /* The default order applies when browser storage is blocked. */ }
  return { ...DEFAULT_SORT };
}

/** The dates of what was measured on a cover (its times, slat time and rolls), oldest first: the last is its last calibration. */
function evidenceDates(cover) {
  const effective = cover.effective || {};
  const measured = ["opening", "closing"].map((direction) => effective[direction]?.provenance).filter((item) => item && MEASURED.includes(item.source));
  return [...measured, ...["slat_time_s", "opening_roll", "closing_roll"].map((key) => effective[key]?.provenance)]
    .map((item) => item?.recorded_at).filter((value) => value && !Number.isNaN(Date.parse(value))).sort();
}

/**
 * One time for one calibration. A calibration dates each run as it ends (the opening
 * a minute before the closing or the lift-off), so the source's «Tempi misurati su questa tapparella · …» (the profile's
 * opening) and «Ultima calibrazione» (the last thing measured here) could differ by a minute for the same calibration.
 * Home Assistant keeps no session with its dates: the dates measured on this cover after the opening belong to the
 * same calibration while none follows the one before it by more than a session's idle lease (`SAME_CALIBRATION_MS`,
 * IDLE_LEASE_SECONDS in cover_calibration.py: past it the session has ended); then both say the last one. Otherwise a
 * later calibration (a Perfeziona on another day) was made here: the profile's own date, as before.
 */
export const SAME_CALIBRATION_MS = 30 * 60 * 1000;
/**
 * The one date of a profile's measured times, wherever it is said: the source's
 * «Tempi misurati su questa tapparella · …», a follower's «Tempi misurati sulla tapparella X · …», «Misurato…» under each
 * profile in «Assegna» / «Cambia profilo». `source` is the cover the times were measured on (its overview's record), when
 * it is still there and still follows this profile; without it, the profile's own date.
 */
export function profileTimesDate(profile, source) {
  const opening = profile?.provenance?.opening?.recorded_at;
  if (!opening || Number.isNaN(Date.parse(opening))) return opening || null;
  if (!source || source.entity_id !== profile.provenance.opening.origin_entity_id || (source.profile_id != null && source.profile_id !== profile.id)) return opening;
  const dates = evidenceDates(source);
  let previous = Date.parse(opening);
  for (const date of dates.filter((value) => Date.parse(value) > previous)) {
    if (Date.parse(date) - previous > SAME_CALIBRATION_MS) return opening;
    previous = Date.parse(date);
  }
  const last = dates.at(-1);
  return last && Date.parse(last) >= Date.parse(opening) ? last : opening;
}
/** The source's own page: `profileTimesDate` on the cover itself. */
export function sourceTimesDate(item) {
  return profileTimesDate(item.profile, item.cover);
}

/** What a cover uses, read from the backend view; nothing is estimated in the browser. */
export function coverStatus(cover, profile = null) {
  const effective = cover.effective || {};
  const evidence = ["opening", "closing"].map((direction) => effective[direction]?.provenance).filter(Boolean);
  const measured = evidence.filter((item) => MEASURED.includes(item.source));
  const dates = evidenceDates(cover);
  const latest = [...measured].sort((a, b) => String(b.recorded_at).localeCompare(String(a.recorded_at)))[0] || null;
  // A check is kept when the record has one (`check_cm`, whole or with a decimal); its gap is the
  // measured and expected heights' difference where both are saved, as shown (one decimal below 1 cm).
  const check = cover.check_cm != null && Number.isFinite(Number(cover.check_cm)) ? text.gapTenth(text.savedGap(cover)) : null;
  // What the calibration is, as Home Assistant derives it (`precision`); for a view without
  // the field, the same rule here: none without a measured time, verified with a check, refined with both rolls
  // measured or entered, basic otherwise.
  const rollMeasured = ["opening_roll", "closing_roll"].every((key) => ["guided", "manual"].includes(effective[key]?.provenance?.source));
  const precision = cover.precision ?? (!measured.length ? "none" : check != null ? "verified" : rollMeasured ? "refined" : "basic");
  // The status follows the class: «Da calibrare», «Stima di base» (the times, with or without the slat time
  // of a tap, rolls unknown or absent: today's times-only profiles too), «Calibrata» (rolls measured with the tape),
  // «Precisione N cm» (a check kept). A number only from a saved check.
  const own = precision === "none" ? "to_measure" : precision === "verified" && check != null ? "verified" : precision === "basic" ? "basic" : "calibrated";
  // A cover that follows a profile measured on another, without a check of its own,
  // says where its values come from: «Dal profilo»; with its own check «Precisione N cm»; the source as it is.
  const origin = profile?.provenance?.opening?.origin_entity_id;
  const follows = !!profile && !!origin && origin !== cover.entity_id;
  // A cover that follows a profile whose times were entered by hand (no time
  // measured, so Home Assistant's class stays `none`) works with those times: it follows a profile as any other, «Dal
  // profilo», never «Da calibrare». Only here, on the card: the class of a source or of a cover without a profile is
  // Home Assistant's as it is.
  const entered = own === "to_measure" && !!profile && origin !== cover.entity_id && profile.opening_time > 0 && profile.closing_time > 0
    && ["opening", "closing"].some((direction) => effective[direction]?.origin === "profile");
  const status = entered || (follows && ["basic", "calibrated"].includes(own)) ? "profile" : own;
  // The rolls count as measured only in a fine-tuning (a basic profile with slats carries neutral rolls, unknown).
  const rolls = precision === "refined" || (precision === "verified" && rollMeasured);
  return {
    precision, status,
    // What was measured, for the «Metodo» line and the wizard's «already calibrated (…)»: times, times and slats (the
    // basic calibration's tap), or times and roll (fine-tuned).
    detail: rolls ? "rolls" : effective.slat_time_s?.value > 0 ? "slat" : "times", check,
    // Times entered on a profile come «Dal profilo», never «misurati» (`entered`, only on such a cover).
    rolls, ...(entered ? { entered } : {}), last: dates.at(-1) || null, method: entered ? "inherited" : latest ? (latest.inherited ? "inherited" : latest.source) : null,
    origin: latest?.inherited ? latest.origin_name || latest.origin_entity_id : null,
  };
}

/** The status chips' icons: ✓ on «Calibrata…», the chain on «Dal profilo». */
const STATUS_ICON = Object.freeze({ calibrated: "mdi:check", verified: "mdi:check", profile: "mdi:link-variant" });

/**
 * A follower that uses the profile's times adapted to its own travel (its travel and the
 * profile's reference both known and different, times scaled, none set on this cover): «Adattata dal profilo».
 */
export function adaptedFromProfile(item, language = "en") {
  if (!item?.profile || item.status === "to_measure") return false;
  const ctx = checkContext(item, []);
  if (ctx.role !== "follower" || ctx.personal.opening || ctx.personal.closing || !ctx.scaled) return false;
  const cm = ctx.ownTravel != null ? number(ctx.ownTravel, language) : "", ref = ctx.reference != null ? number(ctx.reference, language) : "";
  return !!cm && !!ref && cm !== ref;
}

/**
 * The status chip of a cover: «Da calibrare» (grey), «Calibrata» (accent) or «Verificata · N cm», green within the
 * check's threshold and yellow beyond. «Stima di base» in the tone of «Calibrata» (it is calibrated).
 * A follower with adapted times says «Adattata dal profilo»; in the lists
 * (`chain: false`) «Dal profilo» / «Adattata dal profilo» go without the chain, which stays on the profile's chip beside it.
 */
export function statusBadge(item, t, language = "en", { chain = true } = {}) {
  // The tone from the gap's true value (to the tenth), not from the number shown: 4,3 is beyond the 4 cm.
  const tone = item.status === "verified" ? ` data-tone="${text.gapPasses(item.check, CHECK_THRESHOLD_CM) ? "success" : "warning"}"` : "";
  // The gap with one decimal below a centimetre («0,3 cm»), whole above (text.gapText). «Calibrata · N cm»: one chip
  // that says both (a check presumes the calibration).
  const cm = text.gapText(item.check, language);
  const adapted = item.status === "profile" && adaptedFromProfile(item, language);
  const label = item.status === "verified" ? fill(t("calTabStatus_verified"), { cm }) : adapted ? t("calTabStatus_profile_adapted") : t(`calTabStatus_${item.status}`);
  // The chip of a check says in its title where it stopped, and what to do beyond the threshold.
  // Every other chip says in its title what it means (the profile by its name).
  const hint = adapted ? "calTabStatusHint_profile_adapted" : `calTabStatusHint_${item.status}`;
  const said = item.status === "verified" ? checkSentence(item, t, language)
    : t(hint) === hint || (item.status === "profile" && !item.profile?.name) ? "" : fill(t(hint), { profile: item.profile?.name ?? "" });
  // No icon on «Da calibrare» nor on «Stima di base».
  const icon = item.status === "profile" && !chain ? null : STATUS_ICON[item.status];
  return `<span class="badge cal-badge" data-status="${item.status}"${tone}${said ? ` title="${esc(said)}"` : ""}>${icon ? `<ha-icon icon="${icon}" aria-hidden="true"></ha-icon>` : ""}${esc(label)}</span>`;
}

/**
 * The sentence of a check kept, in the details' «Stato» and in the chip's title:
 * where it stopped from the expected point («a metà corsa» only when it was half the travel), and beyond the 4 cm that
 * refining the positioning is worth it. None without a check (a check kept on a cover the backend says verified).
 * A follower has no Perfeziona: beyond the threshold, measuring the times again (`checkContext`'s role).
 */
export function checkSentence(item, t, language = "en") {
  if (item?.check == null || item.status === "to_measure") return "";
  const cm = text.gapText(item.check, language);
  const where = fill(t(checkAtHalf(item.cover, item.profile) ? "cal_POS_check_half" : "cal_POS_check_any"), { cm });
  const follower = !!item.profile && item.profile.provenance?.opening?.origin_entity_id !== item.cover.entity_id;
  return text.gapPasses(item.check, CHECK_THRESHOLD_CM) ? where
    : `${where} ${fill(t(follower ? "cal_POS_check_beyond_follower" : "cal_POS_check_beyond"), { limit: number(CHECK_THRESHOLD_CM, language) })}`;
}

/**
 * A value of the details' tables in the format of «Caratteristiche»: the times with one decimal («3,0 s»),
 * the coefficients with one or two («1,0», «1,05»), the travel as everywhere («110 cm», «109,5 cm»); null when missing.
 */
const valueText = (language) => (value, digits, unit) => value == null || !Number.isFinite(Number(value)) ? null
  : `${unit === "cm" ? number(value, language, digits) : text.decimals(value, language, 1, digits)}${unit ? ` ${unit}` : ""}`;

/** A value never leaves its unit, nor a date its year. */
const keepUnits = (shown) => String(shown).replace(/(\d) (s|cm)(?=$|[\s)])/g, "$1\u00a0$2");

/**
 * Label and value rows (`.cal-pairs`): `[label, shown, note, html, kind, below]`. A fourth item, when there is one, is its
 * own markup in place of the value (the travel's pencil); a sixth is what an edit in place says under
 * its row (a refusal, a question), on the row's full width.
 */
function pairsMarkup(items) {
  return `<dl class="cal-pairs">${items.map(([label, shown, note, html, kind, below]) => `<div${html ? ` class="${kind || "cal-pairs-travel"}"` : ""}><dt>${esc(label)}</dt><dd>${html ?? `${esc(keepUnits(shown))}${note ? ` <span class="cal-pairs-note">· ${esc(note)}</span>` : ""}`}</dd>${below || ""}</div>`).join("")}</dl>`;
}

/** Covers this view can measure: timed covers, never those with a position sensor (no resolved timings). */
export function timedCovers(data) {
  return (data?.covers || []).filter((cover) => cover.effective && !cover.advanced);
}

/**
 * What the details'
 * «Posizionamento» and the procedure «Controlla il posizionamento» show for a cover, read from the overview only. One
 * question on what the reader sees, then an outcome that leads to one of the two flows: Ricalibra (the times, no tape)
 * or Perfeziona (with the tape). Nothing here is written or estimated: the roles, the users of the profile and their
 * checks are Home Assistant's.
 */

/** The share of the travel a saved check must sit at to be «a metà corsa» (the base check plans 50 %, the tape's 75 %). */
const HALF_BAND = 0.1;

/**
 * The cover's role and what the procedure needs to know of it. `items` are the tab's covers (`_covers()`), `nameOf` the
 * panel's name of an entity id. Roles: `none` (never calibrated: no procedure), `alone` (calibrated, no profile),
 * `source` (the profile was measured on it) and `follower` (it follows a profile measured on another cover).
 */
export function checkContext(item, items, nameOf = (id) => id) {
  const cover = item.cover, profile = item.profile || null, effective = cover.effective || {};
  const origin = profile?.provenance?.opening || {};
  const role = text.coverRole(item.status, profile, cover.entity_id);
  const same = (other) => other.entry.entry_id === item.entry.entry_id;
  const sourceItem = role === "follower" && origin.origin_entity_id
    ? items.find((other) => same(other) && other.cover.entity_id === origin.origin_entity_id) || null : null;
  const sourceName = sourceItem ? sourceItem.name : origin.origin_entity_id ? origin.origin_name && origin.origin_name !== origin.origin_entity_id
    ? origin.origin_name : nameOf(origin.origin_entity_id) : null;
  const personal = Object.fromEntries(["opening", "closing"].map((direction) => [direction,
    effective[direction]?.origin === "override" || cover.overrides?.[direction] != null]));
  const reference = profile?.reference_travel_cm > 0 ? profile.reference_travel_cm : null;
  const ownTravel = cover.travel_cm != null && Number(cover.travel_cm) > 0 ? Number(cover.travel_cm) : null;
  // The other covers on the profile, as Home Assistant lists them, with whether each keeps a check (`loses_check`
  // is a cover other than the saved one with a check stored, when the profile's values change).
  const users = profile ? profile.assigned_to.filter((id) => id && id !== cover.entity_id).map((id) => {
    const other = items.find((entry) => same(entry) && entry.cover.entity_id === id);
    return { id, name: other?.name || nameOf(id), check: other ? other.cover.check_cm != null : false };
  }) : [];
  const slats = effective.slat_time_s?.value > 0 || ["yes_measured", "yes_profile", "yes"].includes(cover.slats_state);
  // Known to have none, as «Profilo e valori» says «senza lamelle» (its slats' state, or a slat time of 0).
  const noSlats = !slats && (text.coverSlats(cover) === false || (text.coverSlats(cover) == null && effective.slat_time_s?.value === 0));
  const scaled = ["opening", "closing"].some((direction) => effective[direction]?.scaled === true);
  return { role, profile, sourceName, personal, reference, ownTravel, users, slats, noSlats, scaled };
}

/**
 * The question's answers: «Apertura e chiusura complete» (Ricalibra), «Si ferma nel punto sbagliato» (Perfeziona) and,
 * for a reader who cannot tell, «Non so, vorrei ricontrollare tutto» (Ricalibra: at its end A-34 offers Perfeziona).
 * None on a follower (it has no Perfeziona: «Controlla» leads straight to Ricalibra) nor on a cover never calibrated
 * (no procedure).
 */
export function answersOf(ctx) {
  return ctx.role === "alone" || ctx.role === "source" ? ["full", "middle", "unsure"] : [];
}

/** The outcome: `D1` (Perfeziona) for «Si ferma nel punto sbagliato», `D2` (Ricalibra) otherwise (on a follower always). */
export function outcomeOf(ctx, answer) {
  return ctx.role !== "follower" && answer === "middle" ? "D1" : "D2";
}

/** Whether a saved check sat at half the travel (its expected height against the travel the cover uses now). */
export function checkAtHalf(cover, profile) {
  const expected = Number(cover?.check_expected_cm), travel = Number(cover?.travel_cm ?? profile?.reference_travel_cm);
  if (cover?.check_expected_cm == null || !Number.isFinite(expected) || !(travel > 0)) return false;
  return Math.abs(expected / travel - 0.5) <= HALF_BAND;
}

export class CalibrationTab {
  constructor() {
    this._generation = 0; this._rows = new Map(); this._unsubs = []; this._page = null;
    this._filter = "all"; this._sort = savedSort(); this._menu = null; this._mode = { kind: "list" }; this._hosts = new WeakSet();
    this._calibration = new CoverCalibration();
    this._wizard = new CalibrationWizard();
  }

  /**
   * While the wizard is shown, the page does not bounce or pull to refresh at its top (Safari's
   * pull-to-refresh reloads Home Assistant and drops the wizard without asking: iOS never shows `beforeunload`). The
   * previous values come back as soon as the list is shown again.
   */
  get _mode() { return this.__mode; }
  set _mode(mode) {
    this.__mode = mode;
    // A cover's details belong to the list: a wizard or a session replaces them (the ✕ of the
    // flow always leads back to the list, never to them; «Perfeziona» replaces the dialog).
    if (mode?.kind !== "list") this._page = null;
    this._syncHold();
  }

  /**
   * The views over the page hold it: the wizard (always no pull-to-refresh, held still) and a
   * cover's details (its dialog on every window; only its body scrolls).
   */
  _syncHold() {
    const mode = this.__mode;
    // «Cambia profilo» opened from the list holds the same frame as the details.
    const full = mode?.kind === "wizard", page = mode?.kind === "list" && (!!this._page || !!this._assign);
    const hold = full || page, roots = [document.documentElement, document.body].filter(Boolean);
    if (hold && !this._overscroll) {
      this._overscroll = roots.map((element) => [element, element.style.overscrollBehaviorY]);
      for (const element of roots) element.style.overscrollBehaviorY = "none";
    } else if (!hold && this._overscroll) {
      for (const [element, value] of this._overscroll) element.style.overscrollBehaviorY = value;
      this._overscroll = null;
    }
    // The wizard and a cover's details hold the page under them still, in their dialog; only their
    // content scrolls. Held without moving it (`lockScroll`: overflow hidden on the box that scrolls).
    const still = full || page;
    if (still && this._context?.host && !this._fullHold) this._fullHold = lockScroll(this._context.host);
    else if (!still && this._fullHold) { this._fullHold(); this._fullHold = null; }
  }

  /** What the window tells the list (rotation, phone edge, fonts); returns its teardown. */
  _watchWindow(context) {
    const off = [];
    const listen = (target, type, handler, options) => {
      if (!target?.addEventListener) return;
      target.addEventListener(type, handler, options);
      off.push(() => target.removeEventListener(type, handler, options));
    };
    // A rotation or a window that crosses the phone's edge changes the layout without always changing the
    // tab's width first (iOS settles the media queries after the resize): every card is measured again, also once the
    // rotation has settled. A cover's details are the same dialog on every window (the stylesheet lays it
    // out), the list has no card open in place any more.
    const view = context.host.ownerDocument?.defaultView;
    const relayout = () => {
      if (this._mode.kind === "wizard") { this._relayoutFlow(); return; }
      if (this._mode.kind !== "list") return;
      this._band();
      // A card open while the window crosses 1024 px takes the other layout (one column, or two).
      if (this._page && !this._assign) this._render();
    };
    const frame = globalThis.requestAnimationFrame || ((callback) => setTimeout(callback, 16));
    // The bands of the overlays too (the tendina, the medium dialog's footer).
    for (const query of [PHONE, SHORT, NARROW, SHEETS]) {
      const list = typeof view?.matchMedia === "function" ? view.matchMedia(query) : null;
      listen(list, "change", () => frame(relayout));
    }
    // After a rotation WebKit may keep the cards' previous geometry (container queries and measured rules not
    // evaluated again) until a touch changes a state on them. Once the rotation has settled (two frames and 150 ms,
    // again at 400 ms), the list is laid out afresh: see `_settleLayout`.
    let settle = 0;
    const settled = () => {
      clearTimeout(settle);
      frame(() => frame(() => { settle = setTimeout(() => { this._settleLayout(); setTimeout(() => this._settleLayout(), 250); }, 150); }));
    };
    listen(view, "orientationchange", () => { frame(relayout); settled(); });
    // The browser's Back (Android's and iOS's too) is the ✕ of what is open over the list.
    listen(view, "popstate", (event) => this._popped(event));
    listen(view?.visualViewport, "resize", settled);
    return () => { clearTimeout(settle); for (const remove of off.splice(0)) remove(); };
  }

  /** Replace the wizard (tests use a faster preview); `null` leaves only sessions and groups. */
  useWizard(wizard) { this._wizard?.close(); this._wizard = wizard; }

  clear() {
    this._context?.setBeforeLeave?.(null);
    // One teardown for what the tab hung on the window and on a docked view.
    this._windowOff?.(); this._windowOff = null;
    // A wizard asked for before the tab was cleared never opens later by surprise.
    this._pendingWizard = null;
    // Nor a card asked for by another tab; its way back is dropped, never called.
    this._pendingCover = null;
    this._coverReturn = null;
    this._leaveFlow();
    this._generation++;
    this._closeDialog();
    for (const unsubscribe of this._unsubs.splice(0)) Promise.resolve(unsubscribe()).catch(() => {});
    this._calibration.close();
    this._wizard?.close();
    this._rows.clear();
    this._page = null;
    this._assign = null;
    this._closePageDialog();
    this._mode = { kind: "list" };
    this._dropMenu();
    this._context = null;
    this._key = null;
  }

  show(context) {
    const key = JSON.stringify(context.gateways.map((entry) => entry.entry_id).sort());
    const changed = key !== this._key || context.hass.connection !== this._context?.hass.connection;
    // A wizard asked for just before this showing (the profile dialog's «New guided measurement») survives the
    // showing's own reset when its gateway is among the ones shown; any other clear forgets it.
    const pending = this._pendingWizard, cover = this._pendingCover;
    if (changed) this.clear();
    if (pending && context.gateways.some((entry) => entry.entry_id === pending.entryId)) this._pendingWizard = pending;
    // So does a cover's card asked for by another tab (`requestCover`).
    if (cover && context.gateways.some((entry) => entry.entry_id === cover.entryId)) this._pendingCover = cover;
    if (!changed && this._context && this._context.host !== context.host) {
      // A rebuilt shell gives a new element: whatever the old one showed is closed, as on any exit (its dialog too).
      this._calibration.close();
      this._wizard?.close();
      this._leaveFlow();
    }
    this._context = context;
    this._key = key;
    this._theme();
    // Leaving the tab with a live calibration open asks first (the wizard's "Calibration in progress").
    context.setBeforeLeave?.(() => (this._mode.kind === "wizard" ? this._wizard?.beforeLeave?.() : null) || null);
    if (!this._hosts.has(context.host)) {
      this._hosts.add(context.host);
      context.host.addEventListener("click", (event) => this._click(event));
      // The disclosures of the tab, its page and the wizard slide open and closed.
      slideDetails(context.host);
      context.host.addEventListener("keydown", (event) => this._keydown(event));
      context.host.addEventListener("change", (event) => this._change(event));
      // `toggle` does not bubble: «Qualcosa non va?» is heard on its way down.
      context.host.addEventListener("toggle", (event) => this._folded(event), true);
      // A filter's or an order's native drop-down never leaves the page scrolled (iOS's picker).
      keepScrollOnPick(context.host);
      // A field in the tab's overlays (the details, the wizard's readings and names, A-35's search) stays above
      // the phone's keyboard.
      followKeyboard(context.host);
      // A profile chip cut short shows the rest: its text runs on a pointer's passage, scrolls under a finger.
      followCutChips(context.host);
      // A new width may change the list's band.
      if (typeof ResizeObserver === "function") {
        const host = context.host;
        let width = 0;
        // The band and the cards are set in the next frame, outside the observer's delivery.
        new ResizeObserver(([entry]) => {
          const now = Math.round(entry.contentRect.width);
          if (now === width) return;
          width = now;
          (globalThis.requestAnimationFrame || ((callback) => setTimeout(callback, 16)))(() => { this._band(); markCutChips(host); fitStatus(host); });
        }).observe(host);
      }
    }
    // The window's listeners once per showing, removed by `clear()` (not once per host: a rebuilt shell gave a new
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
   * The tab's notes and chips follow Home Assistant's dark mode like the wizard does, on every update (set only
   * when the tab was shown, a list could keep the light rules under a dark Home Assistant: white warning text, a faint
   * «In corso» chip); without theme data, the system's preference. The tab's dialogs follow it too.
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
        // A session started, moved on or ended on this gateway (any tab or device): the list reads it again at
        // once (a burst of transitions makes one read), instead of at the next 15 s refresh. Only while the list shows:
        // the way back to it reads everything again anyway.
        if (event.kind === "session") {
          clearTimeout(row.sessionTimer);
          // An open wizard left behind by another device (its session cancelled there) learns of the new one.
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
    // A read begun before a discard (`_discarded`) may still carry the cancelled session: it is read again.
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
          // With this tab's identity the session says whether this tab owns it (its action then resumes it).
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
        // A wizard giving a profile to other covers (A-35) shows the data just read.
        if (this._mode.kind === "wizard") this._wizard?.dataChanged?.();
        // And «Cambia profilo» refused for another change saved meanwhile.
        this._assignAgain(entryId);
        if (row.again) { row.again = false; this._read(entryId, generation); }
      }
    }
  }

  /** «Esci senza salvare» confirmed by Home Assistant: the gateway's session is gone at once, then read again. */
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
          ...coverStatus(cover, profile) };
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
    // On a touch screen a native drop-down keeps the focus (and its ring) after the choice: let it go.
    const touch = typeof matchMedia === "function" && matchMedia("(hover: none)").matches;
    if (touch && event.target.matches?.("select[data-filter], select[data-sort]")) event.target.blur();
    if (event.target.matches?.("select[data-filter]") && this._mode.kind === "list") { this._filter = event.target.value; this._render(); return; }
    const select = event.target.closest?.("select[data-sort]");
    if (!select || this._mode.kind !== "list") return;
    const [key, dir] = select.value.split(":");
    this._setSort(key, dir === "desc" ? "desc" : "asc");
  }

  /**
   * The one filter and order bar of the Calibration list and A-01. Two states, chosen by the bar's own
   * width (`@container calbar`, 640 px: a 680 px column less its padding): wide, the chips on the left (the filters)
   * and «Ordina per» as a compact drop-down on the right; narrow, two native drop-downs at 50 % (the chips' drop-down and
   * the order). The order is the same `<select>` in both states.
   */
  _filterBar({ chips, chipsClass = "", select, sortAttribute = "data-sort", extra = "", count = "" }) {
    const { t } = this._context;
    // The counts («12 tapparelle · 7 calibrate · 5 da calibrare») only above the two drop-downs; the chips carry them.
    return `<div class="cal-filterbar${extra}">${count ? `<p class="muted cal-tab-summary" role="status">${esc(count)}</p>` : ""}<div class="cal-filterbar-row">
          <div class="cal-tab-chips${chipsClass}" role="group" aria-label="${esc(t("calTabFilterLabel"))}">${chips}</div>
          <div class="cal-tab-compact">${select}${this._compactSort(sortAttribute)}</div></div></div>`;
  }

  /** «12 tapparelle · 7 calibrate · 5 da calibrare»: the counts above the bar's drop-downs. */
  _countLine(counts) {
    const { t } = this._context;
    return [["calTabCountCovers", counts.all], ["calTabCountCalibrated", counts.calibrated], ["calTabCountToMeasure", counts.to_measure]]
      .map(([key, count]) => fill(t(count === 1 ? `${key}_one` : key), { n: count })).join(" · ");
  }

  /** The filter drop-down of the narrow bar: the four filters with their counts. */
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
    // As a chip the drop-down covers the whole chip, invisible; the label and the choice are drawn under it.
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
   * A-01's list: the covers' rows (name, room, bus address, status, profile) with one radio each, the list's
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
    const profile = (item) => item.profile ? `${this._profileChip(item.profile.name, false, this._linked(item))}`
      : `<span class="muted" aria-label="${esc(t("calTabProfileNone"))}">—</span>`;
    const rows = visible.map((item) => {
      const key = JSON.stringify([item.entry.entry_id, item.cover.entity_id]), chosen = key === selected;
      return `<label class="cal-batch-row wiz-pick-row${chosen ? " is-selected" : ""}" data-entity="${esc(item.cover.entity_id)}">
        <span class="cal-batch-lead"><input type="radio" name="cover" value="${esc(key)}" ${chosen ? "checked" : ""}>
          <span class="cal-row-icon" aria-hidden="true"><ha-icon icon="mdi:window-shutter"></ha-icon></span></span>
        <span class="cal-row-name"><strong>${esc(item.name)}</strong><span class="muted">${this._subline([item.area, gateways.length > 1 ? item.entry.title : ""], item.entity)}</span></span>
        <span class="cal-batch-chips"><span class="cal-batch-status">${statusBadge(item, t, language, { chain: false })}</span>
        <span class="cal-batch-profile">${profile(item)}</span></span>
        ${chosen && notes.length ? `<span class="wiz-pick-notes">${notes.map((note) => `<span class="cal-note${note.tone === "warning" ? " is-warning" : ""}">${esc(note.text)}</span>`).join("")}</span>` : ""}</label>`;
    }).join("");
    return `${this._filterBar({ chipsClass: " wiz-pick-chips", sortAttribute: "data-pick-sort", extra: " wiz-pick-bar", count: this._countLine(counts),
        chips: FILTERS.map((id) => `<button type="button" class="cal-chip" data-wiz="pick-filter:${id}" aria-pressed="${filter === id}">${esc(t(`calTabFilter_${id}`))} <span class="count">${counts[id]}</span></button>`).join(""),
        select: this._filterSelect(counts, filter, "data-pick-filter") })}
      <div class="cal-list cal-batch-list wiz-pick-list">
        <div class="cal-batch-rows" role="radiogroup" aria-label="${esc(t("wiz_T005"))}">${rows || `<p class="muted wiz-pick-empty">${esc(t("noResults"))}</p>`}</div></div>`;
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
    if (!this._context?.host.isConnected || this._mode.kind !== "list") return;
    if (this._openPendingWizard()) return;
    if (this._openPendingCover()) return;
    this._drawList();
    // A card opened by another tab, closed (with what opened from it) back to the list: its way back.
    if (this._coverReturn && !this._page && !this._assign) {
      const back = this._coverReturn;
      this._coverReturn = null;
      queueMicrotask(() => { try { back(); } catch (error) { console.error(error); } });
    }
    // Which profile chips are cut short now (a mark only: nothing is laid out from it).
    markCutChips(this._context.host);
  }

  /**
   * The list itself, in its own element of the tab (`.cal-tab-view`, laid out as the tab's own children):
   * what opens over it (a flow's dialog, a cover's details) sits beside it and is never patched away.
   */
  _listView() {
    const { host } = this._context;
    let view = host.querySelector(":scope > .cal-tab-view");
    if (!view) { view = document.createElement("div"); view.className = "cal-tab-view"; host.prepend(view); }
    return view;
  }

  /** The list (and, over it, the details of the cover asked for). */
  _drawList() {
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
    // No overview could be read: no filter bar with zero counts, no «nothing matches the filter»; the error says it.
    const unread = !!failed.length && !this._covers().length;
    // The two head actions go off only when there is no cover at all, never because of a filter or the search.
    const none = !this._covers().length;
    const searched = !!this._context.filters.query.trim(), filtered = this._filter !== "all";
    const narrowed = filtered || searched;
    // The title names what hides the covers: the filter, the search, or both. «Come funziona» is already in
    // the head above: not a second time in the card.
    const noMatchTitle = searched && !filtered ? "wiz_T514" : searched && filtered ? "wiz_T515" : "wiz_T493";
    const clear = narrowed ? `<button type="button" class="cal-tab-clear" data-action="cal-tab-clear">${esc(t("wiz_T510"))}</button>` : "";
    // The head's action: the primary; without it the row is not drawn at all.
    const headActions = this._wizard ? `<button type="button" class="primary${loading ? " is-settling" : ""}" data-action="cal-tab-measure" ${busy || loading || none ? "disabled" : ""}><ha-icon icon="${ICON.calibrate}" aria-hidden="true"></ha-icon><span>${esc(t("calTabMeasureOne"))}</span></button>` : "";
    let html;
    if (!loading && !failed.length && !this._covers().length) {
      // no cover estimated from its travel times on the gateway(s); the tab's title stays above.
      html = `<h3 class="cal-tab-title">${esc(t("calTabTitle"))}</h3>${this._emptyCard(t(gateways.length > 1 ? "wiz_T495" : "wiz_T492"), t("calTabEmptyBody"),
        this._howItWorks(), "cal-tab-empty")}`;
    } else {
      // The buttons line up with the introduction, not with the title above it.
      // The title with «Come funziona ›» under it, and the two actions on the title's row (the primary first).
      // «Come funziona ›» opens its text under the whole title row, which keeps its place and its buttons.
      html = `<div class="cal-tab-top"><div class="cal-tab-head"><div class="cal-tab-heading"><h3 class="cal-tab-title">${esc(t("calTabTitle"))}</h3>
          <button type="button" class="cal-tab-how cal-disclosure-toggle" data-action="cal-tab-how" aria-expanded="${!!this._howOpen}" aria-controls="cal-tab-how-text"><span class="cal-disclosure-label">${esc(t("calTabHowItWorks"))}</span><ha-icon icon="mdi:chevron-down" aria-hidden="true"></ha-icon></button></div>
        ${headActions ? `<div class="actions cal-tab-actions">${headActions}</div>` : ""}</div>
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
        ${visible.length ? `<div class="cal-list" lang="${esc(language)}">
          ${this._sortHeads()}
          <div class="cal-list-rows" role="list">${visible.map((item) => this._row(item, busy, language)).join("")}</div></div>` : ""}`;
    }
    // Patched in place: the list and its scroll stay where they are.
    morph(this._listView(), html);
    fitBars(host);
    fitRows(host);
    fitStatus(host);
    this._band();
    this._syncPage(busy, language);
    this._syncHold();
    this._watchMenu();
    this._revealRow();
  }

  /**
   * Closing an overlay leaves the list exactly where it was (no scroll at all). The one exception: the row
   * just touched (a cover calibrated or given another profile) that its new status or profile sent out of view, by the
   * order or the filter: a gentle scroll to it (`block: "nearest"`), never to the tabs. Looked at on the first drawing of
   * the list after the close and on the one that brings the change (a newer revision), then forgotten.
   */
  _touched(key) {
    if (!key) return;
    const [entryId] = JSON.parse(key);
    this._reveal = { key, revision: this._rows.get(entryId)?.data?.revision ?? -1, drawn: false, until: Date.now() + 15000 };
  }

  _revealRow() {
    const reveal = this._reveal, host = this._context?.host;
    if (!reveal || !host || this._mode.kind !== "list" || this._page || this._assign) return;
    const [entryId, entityId] = JSON.parse(reveal.key);
    const fresh = (this._rows.get(entryId)?.data?.revision ?? -1) > reveal.revision;
    if (Date.now() > reveal.until || (reveal.drawn && !fresh)) { if (Date.now() > reveal.until) this._reveal = null; return; }
    const row = [...host.querySelectorAll(".cal-row")].find((element) => element.dataset.entry === entryId && element.dataset.entity === entityId);
    reveal.drawn = true;
    if (fresh) this._reveal = null;
    const box = row?.getBoundingClientRect();
    if (!box || (!box.width && !box.height)) return;
    // The box the list scrolls in (the panel itself in Home Assistant), or the window.
    let top = 0, bottom = host.ownerDocument.defaultView.innerHeight;
    for (let node = host.parentElement || host.getRootNode().host; node; node = node.parentElement || node.getRootNode?.().host) {
      if (node.scrollHeight > node.clientHeight + 1 && /(auto|scroll)/.test(node.ownerDocument.defaultView.getComputedStyle(node).overflowY)) {
        const frame = node.getBoundingClientRect(); top = Math.max(top, frame.top); bottom = Math.min(bottom, frame.bottom); break;
      }
    }
    if (box.bottom <= top || box.top >= bottom) { row.scrollIntoView?.({ block: "nearest", behavior: "smooth" }); this._reveal = null; }
  }

  /**
   * A row's open ⋮ menu closes on the first press outside it, and that press does nothing else (it never
   * opens another cover's details under the finger).
   */
  _watchMenu() {
    const host = this._context?.host;
    const wrap = this._menu !== null && host ? [...host.querySelectorAll(".cal-menu-wrap")]
      .find((item) => item.querySelector('[data-action="cal-tab-menu"]')?.dataset.id === this._menu) || null : null;
    if (wrap === (this._menuWrap || null)) return;
    this._menuOff?.();
    this._menuOff = null;
    this._menuWrap = wrap;
    if (wrap) this._menuOff = closeOnOutsidePress(wrap, () => { this._menuOff = null; this._menuWrap = null; this._menu = null; this._render(); });
  }

  /**
   * The row's ⋮ menu closed any other way than a press outside it (one of its items chosen, Esc, a window
   * opened over the list): its watch for that press stops at once. Left on, the first press in the window just opened
   * (the wizard's «Ho capito, inizia») would close a menu already closed and its click would be swallowed.
   */
  _dropMenu() {
    this._menu = null;
    this._menuOff?.();
    this._menuOff = null;
    this._menuWrap = null;
  }

  /**
   * A profile chip: in the list never wider than its place (its column, the phone's chips row), its
   * name whole in its title; in the list a name wider than its place wraps inside the chip, never «…»;
   * at most 180 px elsewhere; the details' «Profilo» block shows it whole.
   */
  _profileChip(name, whole = false, linked = false) {
    // Shown whole (the details' «Profilo»), a long name may wrap only after its «_», «-» or «.», not mid-word.
    const text = whole ? esc(name).replace(/([_.-])/g, "$1<wbr>") : esc(name);
    // A mark on the cover the profile was measured on, the chain where it follows a profile
    // measured on another cover (the chain is «from the profile» wherever it is drawn). The mark
    // is the crown, as in the details' «Profilo usato anche da».
    // The name runs inside `.cal-chip-run`, so a chip cut short can show the rest (`followCutChips`).
    return `<span class="chip cal-profile-chip${linked ? " is-linked" : ""}" title="${esc(name)}" aria-label="${esc(name)}"><ha-icon icon="${linked ? "mdi:link-variant" : "mdi:crown-outline"}" aria-hidden="true"></ha-icon><span class="cal-chip-text">${whole ? text : `<span class="cal-chip-run">${text}</span>`}</span></span>`;
  }

  /** Whether the cover follows a profile measured on another cover (its chip's chain), not its own. */
  _linked(item) { return !!item?.profile && item.profile.provenance?.opening?.origin_entity_id !== item.cover.entity_id; }

  /** The list's band for the tab's content width, as the stylesheet's container rules choose it (`data-band`). */
  _band() {
    const host = this._context?.host, width = contentWidth(host);
    if (!host || !width) return;
    const band = listBand(width);
    // A mark only (the stylesheet's container rules lay the list out by themselves); the rows' actions are
    // the same in every band: nothing to draw again, nothing measured to clear.
    if (host.dataset.band !== band) host.dataset.band = band;
  }

  /**
   * After a rotation, the list laid out afresh. WebKit (iOS) evaluates the container queries of the tab
   * (`caltab`) again only for elements whose style is invalidated; a rotation that changes the tab's width without
   * touching those elements can leave the cards with the previous band's rules (the empty spaces) until a touch
   * (:hover/:active) invalidates the touched card. Here, in list mode: the containers are switched off and on (each
   * change read back, so the style of every card is computed again against the new width), the list is painted once on
   * its own layer, and the band, the filter bar and the wizard's lists are looked at again. (The
   * list itself has nothing measured.)
   */
  _settleLayout() {
    const host = this._context?.host;
    if (!host?.isConnected || this._mode.kind !== "list") return;
    const containers = [host];
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
    fitBars(host);
    fitRows(host);
    fitStatus(host);
  }

  /** «Come funziona» under an empty state: it opens in place. */
  _howItWorks(extra = "") {
    const { t } = this._context;
    return `<details class="cal-disclosure cal-empty-how${extra}"><summary><span class="cal-disclosure-label">${esc(t("calTabHowItWorks"))}</span><ha-icon icon="mdi:chevron-down" aria-hidden="true"></ha-icon></summary><p class="muted">${esc(t("calTabHowItWorksBody"))}</p></details>`;
  }

  /** an empty state as a card: the shutter icon, a title, its explanation and an optional extra. */
  _emptyCard(title, body, extra = "", kind = "") {
    return `<div class="cal-empty ${kind}" role="status"><span class="cal-empty-icon" aria-hidden="true"><ha-icon icon="mdi:window-shutter"></ha-icon></span>
        <h4>${esc(title)}</h4><p class="muted">${esc(body)}</p>${extra}</div>`;
  }

  /**
   * A row of the list on every window (a card on the phone, a wide row of the table
   * elsewhere): name, room and address, chips, the likely action and ⋮; no accordion: a tap on the row (or «Dettagli» in
   * ⋮) opens the cover's details over the list.
   */
  _row(item, busy, language) {
    const { t, gateways } = this._context;
    const key = JSON.stringify([item.entry.entry_id, item.cover.entity_id]);
    const running = this._running(item);
    const session = running ? item.session : null;
    const badge = session ? `<span class="badge cal-badge" data-status="running">${esc(t(session.waiting_for_stop ? "calTabRunning_waiting"
      : session.phase === "paused" ? "calTabRunning_paused" : session.owner_present ?? session.attached ? "calTabRunning_present" : "calTabRunning_absent"))}</span>`
      : statusBadge(item, t, language, { chain: false });
    const profile = item.profile ? `${this._profileChip(item.profile.name, false, this._linked(item))}` : `<span class="muted" aria-label="${esc(t("calTabProfileNone"))}">—</span>`;
    // A cover that follows a profile has no date of its own (Home Assistant keeps none):
    // no line at all (neither «Calibrata il…» nor «Mai calibrata»), its chip «Dal profilo» says enough.
    // «Calibrata · 31 ott 2026 12:18», no article; the time wraps away (hidden) when it does not fit.
    const follows = this._linked(item), time = item.last ? text.timeText(item.last, language) : "";
    const last = item.last && !follows ? `<span>${esc(fill(t("calTabMeasuredOn"), { date: dateText(item.last, language) }))}</span>${time ? ` <span class="cal-row-last-time">${esc(time)}</span>` : ""}` : "";
    const { shown, menu } = this._rowActions(item, session, busy);
    const menuOpen = this._menu === key;
    // The row's actions carry the menu's icons, shown from the table's band (≥ 1016 px of content) only.
    const button = (action) => `<button type="button" class="cal-row-action${action.danger ? " cal-menu-danger" : ""}" data-action="${action.action}" data-id="${esc(action.id)}"${action.title ? ` title="${esc(action.title)}" aria-label="${esc(action.title)}"` : ""}${action.disabled ? " disabled" : ""}>${action.icon ? text.iconMarkup(action.icon) : ""}<span>${esc(action.label)}</span></button>`;
    const item_ = (action) => `<button type="button" role="menuitem"${action.danger ? ' class="cal-menu-danger"' : ""} data-action="${action.action}" data-id="${esc(action.id)}"${action.disabled ? " disabled" : ""}>${text.iconMarkup(action.icon)}${esc(action.label)}</button>`;
    return `<div class="cal-row${session ? " is-running" : ""}" role="listitem" data-entity="${esc(item.cover.entity_id)}" data-entry="${esc(item.entry.entry_id)}">
      <div class="cal-row-main">
        <button type="button" class="cal-row-toggle" data-action="cal-tab-toggle" data-id="${esc(key)}" aria-haspopup="dialog">
          <span class="cal-row-lead" aria-hidden="true"><span class="cal-row-icon"><ha-icon icon="mdi:window-shutter"></ha-icon></span></span>
          <span class="cal-row-name"><strong>${esc(item.name)}</strong><span class="muted">${this._subline([this._area(item), gateways.length > 1 ? item.entry.title : ""], item.entity)}</span>${follows ? "" : `<span class="cal-row-last-inline">${last || `<span>${esc(t("wiz_T418"))}</span>`}</span>`}</span></button>
        <span class="cal-row-chips"><span class="cal-row-status">${badge}</span>
        <span class="cal-row-profile">${profile}</span></span>
        <span class="cal-row-actions">${shown.length ? `<span class="cal-row-ctas">${shown.map(button).join("")}</span>` : ""}
          ${menu.length ? `<span class="cal-menu-wrap"><button type="button" class="icon-button" data-action="cal-tab-menu" data-id="${esc(key)}" aria-haspopup="menu" aria-expanded="${menuOpen}" aria-label="${esc(t("calTabMore"))}: ${esc(item.name)}" title="${esc(t("calTabMore"))}"><ha-icon icon="mdi:dots-vertical" aria-hidden="true"></ha-icon></button>
          <span class="cal-menu" role="menu" ${menuOpen ? "" : "hidden"}>
            ${menu.map(item_).join("")}
          </span></span>` : ""}</span>
      </div>
    </div>`;
  }

  /**
   * A row's actions, the likely one first, the profile's action, the details;
   * with a session open, Segui / Riprendi, the profile's action (off), the details and the cancel. In EVERY band ONE
   * action in the row and ⋮ with all the others, never repeating the one in view: the actions' column has one width for
   * every row. (The bars' «up to three in view» is the flows' footer and the final screen's, not the list's.)
   * At most one action in view, and only where a step is advised (`advisedStep`);
   * with nothing to do, none: the row keeps its place (the table's empty column, the phone's ⋮ at the right).
   */
  _rowActions(item, session, busy) {
    const { t } = this._context;
    const key = JSON.stringify([item.entry.entry_id, item.cover.entity_id]);
    const assign = { action: "cal-tab-assign", id: key, icon: "mdi:link-variant", label: t(item.profile ? "wiz_T427" : "calTabAssign"), disabled: !!session };
    const details = { action: "cal-tab-toggle", id: key, icon: "mdi:information-outline", label: t("calTabDetails") };
    if (session) {
      const open = !(session.waiting_for_stop || session.recoverable === false);
      const follow = open ? [{ action: "cal-tab-session", id: item.entry.entry_id, label: t(sessionShort(session)), title: t(sessionLabel(session)) }] : [];
      const abort = open ? [{ action: "cal-tab-abort", id: item.entry.entry_id, icon: "mdi:close-octagon-outline", label: t("wiz_T534"), danger: true }] : [];
      return { shown: follow, menu: [assign, details, ...abort] };
    }
    const measure = { action: "cal-tab-measure-cover", id: key, icon: ICON.calibrate,
      label: t(item.status === "to_measure" ? "calTabActionMeasure" : "calTabActionRemeasure"), disabled: !!busy };
    // No «Controlla» in the list (it would open the same card as
    // «Dettagli» and the row). ONE action in view, only where there is a step to advise; ⋮ holds all the rest. Without the
    // wizard no Calibra, Ricalibra nor Perfeziona: then no action in view.
    const wizard = (action) => (this._wizard ? [action] : []);
    if (item.status === "to_measure") {
      // With a profile in the catalogue, Calibra opens the card on «Come vuoi calibrarla?» (measure or use a profile).
      const profiles = this._rows.get(item.entry.entry_id)?.data?.profiles?.length > 0;
      const calibrate = profiles ? { ...measure, action: "cal-tab-calibrate" } : measure;
      return { shown: wizard(calibrate), menu: [assign, details] };
    }
    // The advised step comes from `advisedStep`, the rule A-02 follows too.
    const { role } = this._checkContext(item), { shown } = text.advisedStep({ status: item.status, role, check: item.check }, CHECK_THRESHOLD_CM);
    // A follower (no Perfeziona): Ricalibra in view only after a check beyond the threshold.
    if (role !== "source" && role !== "alone") return shown === "remeasure" ? { shown: wizard(measure), menu: [assign, details] } : { shown: [], menu: [...wizard(measure), assign, details] };
    // The profile's source, a cover without a profile: Perfeziona in view, unless a check within the threshold says it is fine.
    const refine = { action: "cal-tab-refine", id: key, icon: ICON.refine, label: t("cal_D1_button"), disabled: !!busy };
    if (!shown) return { shown: [], menu: [...wizard(refine), ...wizard(measure), assign, details] };
    return { shown: wizard(refine), menu: [...wizard(measure), assign, details] };
  }

  /**
   * The line under a cover's name: room, gateway and the bus address always whole
   * («Indirizzo: 23 · A: 2 · PL: 3», never a short form, never «…»). It wraps only between units, each pair of the
   * address being one, and the «·» stays with the unit before it.
   */
  _subline(before, entity, after = []) { return sublineMarkup(this._context.t, before, entity?.address, after); }

  _area(item) { return item.area; }

  /**
   * The details (on every window, in the overlay's dialog): a white bar [✕ name / room
   * and address] over the cover's details. Two tabs; the procedure «Controlla il posizionamento» in the card,
   * its own footer on its outcome only. The tabs are «Panoramica» (the status, what
   * to do, the cover's characteristics, its profile) and «Dettagli» (how the profile becomes this cover, the last
   * calibration and check, who else uses the profile); a cover never calibrated has one view and no tabs.
   */
  _pageMarkup(item, busy, language) {
    const { t, gateways } = this._context;
    const session = this._running(item) ? item.session : null;
    const badge = session ? `<span class="badge cal-badge" data-status="running">${esc(t(session.waiting_for_stop ? "calTabRunning_waiting"
      : session.phase === "paused" ? "calTabRunning_paused" : session.owner_present ?? session.attached ? "calTabRunning_present" : "calTabRunning_absent"))}</span>`
      : statusBadge(item, t, language);
    // A cover with its session open offers «Segui sessione» / «Riprendi sessione» on its page too, as its row does.
    const open = session && !(session.waiting_for_stop || session.recoverable === false)
      ? `<div class="cal-page-session"><button type="button" class="primary" data-action="cal-tab-session" data-id="${esc(item.entry.entry_id)}">${esc(t(sessionLabel(session)))}</button></div>` : "";
    // The tabs under the bar; the procedure's views in their place, the tabs hidden and ‹ back where they
    // started. Each view has its own body (its id): a tab or a step drawn anew starts at its top, while a redraw of the
    // same one (the list's reads) keeps its scroll and what was opened in it.
    const view = this._view(), ctx = this._checkContext(item), wide = layout.wide();
    // From 1024 px the question is in «Panoramica» itself (its fold): the procedure's only view
    // is the outcome of an answer (the answers beside it). A view the desktop has not (the question, a follower's outcome:
    // the window grew under it) is «Panoramica», its fold open on the answers.
    if (wide && view.check && !(view.check.step === 2 && view.check.answer)) { view.check = null; view.tab = "overview"; view.fold = true; }
    // A cover never calibrated has no tabs and no procedure.
    const tabbed = ctx.role !== "none";
    if (!tabbed) { view.tab = "overview"; view.check = null; }
    const procedure = view.check ? this._checkMarkup(item, ctx, view.check, busy, language, wide) : null;
    const notice = this._pageNotice ? `<p class="notice cal-tab-done cal-page-notice" role="status"><span>${esc(this._pageNotice)}</span></p>` : "";
    const lead = procedure ? `<button type="button" class="icon-button cal-page-back" data-action="cal-tab-check-back" aria-label="${esc(t("cal_D0_back"))}" title="${esc(t("cal_D0_back"))}"><ha-icon icon="mdi:arrow-left" aria-hidden="true"></ha-icon></button>`
      : `<button type="button" class="icon-button cal-page-close" data-action="cal-tab-page-close" aria-label="${esc(t("close"))}" title="${esc(t("close"))}"><ha-icon icon="mdi:close" aria-hidden="true"></ha-icon></button>`;
    const subline = procedure ? esc(procedure.header) : this._subline([this._area(item), gateways.length > 1 ? item.entry.title : ""], item.entity);
    const tab = (id, key) => `<button type="button" role="tab" id="cal-page-tab-${id}" data-action="cal-tab-page-tab" data-id="${id}" aria-selected="${view.tab === id}" aria-controls="cal-page-panel-${id}" tabindex="${view.tab === id ? 0 : -1}">${esc(t(key))}</button>`;
    const tabs = procedure || !tabbed ? "" : `<div class="cal-page-tabs" role="tablist" aria-labelledby="cal-page-name">${tab("overview", "calTabTabOverview")}${tab("details", "calTabTabDetails")}</div>`;
    // From 1024 px both tabs are two columns (`is-wide`, `.cal-page-column`); the phone reads them as one.
    const panel = tabbed ? ` role="tabpanel" aria-labelledby="cal-page-tab-${view.tab}"` : "";
    const body = procedure ? `<div class="cal-page-body cal-check${wide ? " is-wide" : ""}" id="cal-page-check-${view.check.step}" data-tab="check">${procedure.body}</div>`
      : `<div class="cal-page-body${wide ? " is-wide" : ""}" id="cal-page-panel-${view.tab}"${panel} data-tab="${view.tab}">${notice}${view.tab === "details"
        ? this._detailsMarkup(item, ctx, language, wide) : this._overviewMarkup(item, ctx, badge, busy, language, open, wide)}</div>`;
    // Closing it costs nothing: the tendina's handle and its drag.
    return `<div class="cal-page${session ? " is-running" : ""}" data-entity="${esc(item.cover.entity_id)}" data-closable="">
        <div class="cal-page-head">${lead}
          <div class="cal-page-head-text"><h2 id="cal-page-name">${esc(item.name)}</h2>
          <p class="muted">${subline}</p></div></div>${tabs}${body}${procedure?.actions || ""}</div>`;
  }

  /**
   * The open card's view (its tab, the procedure's step), kept across redraws; a card opened anew starts on
   * «Panoramica». `fold`, whether «Qualcosa non va?» is open (from 1024 px).
   */
  _view() {
    if (this._pageView?.key !== this._page) this._pageView = { key: this._page, tab: "overview", check: null, fold: false };
    return this._pageView;
  }

  /** What the procedure knows of a cover, from the tab's covers. */
  _checkContext(item) {
    const { inventory, hass } = this._context;
    const nameOf = (id) => { const entity = inventory.entities.find((entry) => entry.entity_id === id); return entity ? model.entityName(entity, hass) : id; };
    return checkContext(item, this._covers(), nameOf);
  }

  /**
   * «Panoramica»: «Stato» (the title and the sentence of
   * what the cover uses, the check's sentence, the chip UNDER them; then «Qualcosa non va?»), «Perfeziona il
   * posizionamento» with its caption where it was offered before (the profile's source, a cover without a profile),
   * «Caratteristiche» (`_traitsMarkup`) and «Profilo» (`_profileMarkup`). «Qualcosa non va?»: from 1024 px a fold in the
   * card, the three answers and the actuator's note in it (a follower, which has no question: Ricalibra with its warning),
   * an answer leading to the procedure's page with the answers beside it; on the phone a row that opens the procedure
   * (the question, or a follower's Ricalibra). From 1024 px two columns: the status on the left, the cover and its profile
   * on the right. A cover never calibrated: «Stato» (the chip and its sentence), «Come vuoi calibrarla?» (two rows), its
   * characteristics (from 1024 px on the right).
   */
  _overviewMarkup(item, ctx, badge, busy, language, open = "", wide = false) {
    const { t } = this._context, key = JSON.stringify([item.entry.entry_id, item.cover.entity_id]), effective = item.cover.effective || {};
    const values = { cover: item.name, source: ctx.sourceName ?? "", cm: ctx.ownTravel != null ? number(ctx.ownTravel, language) : "",
      ref: ctx.reference != null ? number(ctx.reference, language) : "" };
    let title = "", desc = "";
    if (ctx.role === "none") desc = t("cal_POS_none_text");
    else if (ctx.role === "follower") {
      title = t("cal_POS_follow_title");
      // A time set on this cover is used as it is, neither adapted nor the profile's: no sentence says it otherwise.
      // The adapted times in one sentence, the two travels side by side.
      const adapted = adaptedFromProfile(item, language);
      desc = ctx.personal.opening || ctx.personal.closing ? "" : adapted ? fill(t("cal_POS_follow_adapted"), values)
        : !ctx.sourceName ? "" : !ctx.reference && ctx.ownTravel != null ? fill(t("cal_POS_follow_unscaled"), values) : fill(t("cal_POS_follow_same"), values);
    } else {
      // «… su questa tapparella» only where the rolls were measured here with the tape (not entered, not taken from
      // elsewhere); otherwise the same sentence without it.
      const measuredHere = ["opening_roll", "closing_roll"].every((roll) => effective[roll]?.provenance?.source === "guided" && !effective[roll]?.provenance?.inherited);
      title = t(item.rolls ? "cal_POS_curve_title" : "cal_POS_basic_title");
      desc = item.rolls ? t(measuredHere ? "cal_POS_curve_text" : "cal_POS_curve_text_other") : t("cal_POS_basic_text");
    }
    // The check's sentence closes the text (the same as the chip's title).
    const said = [desc, checkSentence(item, t, language)].filter(Boolean).join(" ");
    const chip = `<span class="cal-pos-state-chip">${badge}</span>`;
    // The chip under the title and the text; a cover never calibrated, which has no title, opens with it.
    const state = `<div class="cal-card-body cal-pos-state">${ctx.role === "none" ? chip : ""}${title ? `<h3 class="cal-pos-state-title">${esc(title)}</h3>` : ""}${said ? `<p class="cal-pos-state-desc">${esc(said)}</p>` : ""}${ctx.role === "none" ? "" : chip}</div>`;
    const cta = (button, caption) => `<div class="cal-cta">${button}<p class="cal-caption">${esc(caption)}</p></div>`;
    const button = (action, kind, icon, label, off) => `<button type="button" class="${kind}" data-action="${action}" data-id="${esc(key)}"${off ? " disabled" : ""}>${text.iconMarkup(icon)}${esc(label)}</button>`;
    const column = (inner) => `<div class="cal-page-column">${inner}</div>`;
    if (ctx.role === "none") {
      // «Come vuoi calibrarla?», two rows, Calibra and Assegna profilo, each with
      // its caption, at every width.
      const choice = (action, title_, caption, off) => `<button type="button" class="cal-choice" data-action="${action}" data-id="${esc(key)}"${off ? " disabled" : ""}><span class="cal-choice-text"><span class="cal-choice-title">${esc(title_)}</span><span class="cal-choice-desc">${esc(caption)}</span></span><ha-icon icon="mdi:chevron-right" aria-hidden="true"></ha-icon></button>`;
      const rows = `${this._wizard ? choice("cal-tab-measure-cover", t("cal_POS_none_how_measure"), t("cal_POS_none_measure_caption"), !!busy) : ""}${choice("cal-tab-assign", t("cal_POS_none_how_assign"), t("cal_POS_none_assign_caption"), this._running(item))}`;
      // As on the other covers, the card under its title «Stato»; from 1024 px «Stato» and «Come vuoi
      // calibrarla?» on the left, «Caratteristiche» on the right; the phone in the same order, one under the other.
      const status = `<section class="cal-section cal-pos-current"><h4 class="cal-section-title">${esc(t("calTabStateSection"))}</h4><div class="cal-card cal-pos-status">${state}</div></section>`;
      const how = `<section class="cal-section cal-pos-how"><h4 class="cal-section-title">${esc(t("cal_POS_none_how"))}</h4><div class="cal-card cal-rows">${rows}</div></section>`;
      const traits = this._traitsMarkup(item, ctx, language);
      return wide ? `${column(`${open}${status}${how}`)}${column(traits)}` : `${open}${status}${how}${traits}`;
    }
    // «Qualcosa non va?»: its icon, its title and what it leads to (the question, or a follower's Ricalibra).
    const asks = answersOf(ctx).length > 0;
    const head = `<ha-icon class="cal-help-icon" icon="mdi:help-circle-outline" aria-hidden="true"></ha-icon><span class="cal-choice-text"><span class="cal-choice-title">${esc(t("cal_S1_fold"))}</span><span class="cal-choice-desc">${esc(t(asks ? "cal_S1_entry_desc" : "cal_S1_fold_follower"))}</span></span>`;
    const help = wide
      ? `<details class="cal-fold cal-pos-help"${this._view().fold ? " open" : ""}><summary class="cal-help-row">${head}<ha-icon class="cal-help-chevron" icon="mdi:chevron-down" aria-hidden="true"></ha-icon></summary><div class="cal-fold-body">${asks ? this._answersMarkup(ctx) : this._outcomeMarkup(item, ctx, "D2", busy, language).inline}</div></details>`
      : `<div class="cal-pos-help"><button type="button" class="cal-choice cal-help-row" data-action="cal-tab-check-start" data-id="${esc(key)}">${head}<ha-icon icon="mdi:chevron-right" aria-hidden="true"></ha-icon></button></div>`;
    const status = `<section class="cal-section cal-pos-current"><h4 class="cal-section-title">${esc(t("calTabStateSection"))}</h4><div class="cal-card cal-pos-status">${state}${help}</div></section>`;
    // Perfeziona where it was offered (the profile's source, a cover without a profile; never a follower).
    const refine = ctx.role !== "follower" && this._wizard ? cta(button("cal-tab-refine", "primary", ICON.refine, t("cal_D1_button"), !!busy), t(item.rolls ? "cal_POS_refine_caption_again" : "cal_POS_refine_caption")) : "";
    const cover = `${this._traitsMarkup(item, ctx, language)}${this._profileMarkup(item, ctx, language)}`;
    return wide ? `${column(`${open}${status}${refine}`)}${column(cover)}` : `${open}${status}${refine}${cover}`;
  }

  /**
   * «Caratteristiche»: the times (the slats' only where the cover has slats: «—» and «da misurare» while not
   * measured), the curtain travel with its pencil (and the chain «Usa la corsa del profilo» where it is set here) and the
   * slats. No coefficients here (they are in «Dettagli»). Labels only on a follower, where a value is its own: «· impostata
   * qui» (the travel), «impostato qui» (a time set on it); its slats «Sì · dal profilo». A cover never calibrated: where
   * its times come from, as the card's tail.
   */
  _traitsMarkup(item, ctx, language) {
    const { t } = this._context, effective = item.cover.effective || {}, profile = item.profile;
    const slat = effective.slat_time_s?.value, slats = slat > 0 || text.coverSlats(item.cover) === true;
    const follower = ctx.role === "follower";
    const n = (shown, digits = 1) => number(shown, language, digits);
    // The times in one row; on a narrow card the short labels («Salita / Discesa / Lamelle») take
    // the place of the long ones by a container rule, never by measuring here. A caption may be a to-do
    // («da misurare», amber with its dot).
    const measure = (label, short, seconds, caption = "", todo = false) => `<div class="cal-measure"><span class="cal-measure-label"><span class="cal-label-long">${esc(label)}</span><span class="cal-label-short">${esc(short)}</span></span><span class="cal-measure-value"><span class="cal-measure-number">${esc(seconds == null ? "—" : text.fixed(seconds, language, 1))}</span>${seconds == null ? "" : ' <span class="cal-measure-unit">s</span>'}</span>${caption ? `<span class="cal-measure-caption${todo ? " is-todo" : ""}">${todo ? '<span class="cal-todo-dot" aria-hidden="true"></span>' : ""}${esc(caption)}</span>` : ""}</div>`;
    const set = (direction) => follower && effective[direction]?.origin === "override" ? t("calTabSetHereM") : "";
    const times = `${measure(t("wiz_T093"), t("wiz_T009"), effective.opening?.value, set("opening"))}${measure(t("wiz_T094"), t("wiz_T010"), effective.closing?.value, set("closing"))}${slats
      ? measure(t("wiz_T095"), t("wiz_T656"), slat > 0 ? slat : null, slat > 0 ? "" : t("calTabSlatsToMeasure"), !(slat > 0)) : ""}`;
    const travelNote = follower && item.cover.travel_cm != null ? t("calTabSetHereF") : "";
    const travel = this._travelMarkup(item, keepUnits(travelText(t, n, item.cover.travel_cm, profile?.reference_travel_cm)), travelNote, ctx.role === "source");
    const slatsEdit = this._slatsMarkup(item, item.cover.slats_state || "unknown", follower);
    const rows = pairsMarkup([[t("wiz_T041"), "", "", travel.html, travel.kind, travel.below], [t("calTabSlatsRow"), "", "", slatsEdit.html, slatsEdit.kind, slatsEdit.below]]);
    const note = ctx.role === "none" ? this._originNote(item, language).replace(/^<p class="[^"]*">([\s\S]*)<\/p>$/, "$1") : "";
    return `<section class="cal-section cal-page-traits"><h4 class="cal-section-title">${esc(t("calTabTraits"))}</h4><div class="cal-card cal-page-status"><div class="cal-measures cal-page-times">${times}</div>${rows}${note ? `<p class="cal-card-tail"><span>${note}</span></p>` : ""}</div></section>`;
  }

  /**
   * «Profilo»: the profile's own symbol, the same on every cover (a «scheda tecnica»), its name
   * whole, «Misurato su {cover} · {date}» (or «su questa tapparella»), then «Cambia profilo» (`_linkMarkup`:
   * the unlinking in its window). Without a profile: «Nessun profilo» and «Assegna profilo».
   */
  _profileMarkup(item, ctx, language) {
    const { t } = this._context, profile = item.profile;
    let head;
    if (!profile) head = `<div class="cal-card-body cal-page-profile-head"><p class="muted">${esc(t("calTabProfileNone"))}</p></div>`;
    else {
      // The date with its time: «Tempi misurati sulla tapparella
      // **X** · 1 nov 2026 17:09», on the source «Tempi misurati su **questa tapparella** · …» (each date beside what it dates).
      // On the source, the same time as «Ultima calibrazione» when both date the same calibration.
      // On a follower too, the same time as on its source (`profileTimesDate`).
      const origin = profile.provenance?.opening || {}, when = text.dateTimeText(ctx.role === "source" ? sourceTimesDate(item) : this._timesDate(profile), language);
      const measuredOn = ctx.role === "source" ? text.inline(t(when ? "calTabRoleSource" : "calTabRoleSourceNoDate"), { date: when })
        : origin.origin_entity_id ? fill(esc(t(when ? "calTabRoleFollower" : "calTabRoleFollowerNoDate")), { cover: `<strong>${esc(this._originName(origin))}</strong>`, date: esc(when) }) : "";
      // Shown whole, a long name may wrap only after its «_», «-» or «.», not mid-word.
      head = `<div class="cal-card-body cal-page-profile-head"><span class="cal-profile-icon" title="${esc(t("calTabProfileSection"))}"><ha-icon icon="mdi:clipboard-text-outline" aria-hidden="true"></ha-icon></span><span class="cal-profile-text"><span class="cal-profile-name">${esc(profile.name).replace(/([_.-])/g, "$1<wbr>")}</span>${measuredOn ? `<span class="cal-page-role">${measuredOn}</span>` : ""}</span></div>`;
    }
    return `<section class="cal-section cal-page-profile"><h4 class="cal-section-title">${esc(t("calTabProfileSection"))}</h4><div class="cal-card cal-page-profile-card">${head}${this._linkMarkup(item)}</div></section>`;
  }

  /**
   * «Cambia profilo» on every cover with a profile («Assegna profilo» without one). No
   * «Rendi indipendente» yet. It sits in the card «Profilo», under the profile's name and «Misurato su…»
   * (`_profileMarkup`). One secondary button, its caption under it as Perfeziona's; the
   * unlinking is chosen in the window it opens («Scollega il profilo», its consequences under it: wiz_T405, or wiz_T430
   * off on the profile's source), no row of its own any more.
   */
  _linkMarkup(item) {
    const { t } = this._context, key = JSON.stringify([item.entry.entry_id, item.cover.entity_id]), running = this._running(item);
    const [label, caption] = item.profile ? [t("cal_LINK_change"), t("cal_LINK_change_desc")] : [t("calTabAssign"), t("cal_POS_none_assign_caption")];
    return `<div class="cal-card-body cal-page-link"><button type="button" class="cal-outlined" data-action="cal-tab-assign" data-id="${esc(key)}"${running ? " disabled" : ""}>${text.iconMarkup("mdi:link-variant")}${esc(label)}</button><p class="cal-caption">${esc(caption)}</p></div>`;
  }

  /**
   * The procedure «Controlla il posizionamento» inside the card. Step 1 «Cosa
   * vuoi sistemare?» with its three answers and the actuator's note under them, step 2 «Cosa possiamo fare»: Ricalibra (D2) or
   * Perfeziona (D1). A follower has no question (it has no Perfeziona): step 2, Ricalibra, at once. The answers are
   * the rows of one card, the hairline of `.cal-pairs` between them. Returns the bar's line, the body and the footer.
   * From 1024 px (`wide`) only step 2 with an answer: the three answers on the left as vertical tabs
   * (the wizard's steps column), the one chosen selected, its outcome on the right; no «Cambia risposta».
   */
  _checkMarkup(item, ctx, check, busy, language, wide = false) {
    const { t } = this._context;
    if (check.step === 1) {
      return { header: t("cal_S1_fold"), body: `<div class="cal-step"><h3 class="cal-step-title">${esc(t("cal_P1_title"))}</h3>${this._answersMarkup(ctx)}</div>` };
    }
    const outcome = this._outcomeMarkup(item, ctx, outcomeOf(ctx, check.answer), busy, language, wide ? null : check.answer, check.answer);
    const back = `<button type="button" class="cal-check-back" data-action="cal-tab-check-back">${esc(t("cal_D0_back"))}</button>`;
    const actions = `<div class="cal-page-actions cal-check-actions">${outcome.action}${back}</div>`;
    if (!wide) return { header: t("cal_S1_fold"), body: outcome.body, actions };
    // Each answer with its chevron at the right, as the rows of «Posizionamento» it came from.
    const tab = (id) => `<button type="button" role="tab" id="cal-check-tab-${id}" data-action="cal-tab-check-answer" data-id="${esc(id)}" aria-selected="${id === check.answer}" aria-controls="cal-check-detail" tabindex="${id === check.answer ? 0 : -1}"><span class="cal-check-tab-title">${esc(t(`cal_P1_${id}_title`))}</span><ha-icon icon="mdi:chevron-right" aria-hidden="true"></ha-icon></button>`;
    const tabs = `<div class="cal-check-answers" role="tablist" aria-orientation="vertical" aria-label="${esc(t("cal_P1_title"))}">${answersOf(ctx).map(tab).join("")}</div>`;
    // One height for every answer: the other outcomes lie in the same place, unseen (`is-sizer`: hidden,
    // inert, out of the reading order), so the tallest decides it and a change of answer never moves the overlay.
    // One per other answer, «Non so» having its own title and introduction.
    const others = answersOf(ctx).filter((id) => id !== check.answer)
      .map((id) => `<div class="cal-check-outcome is-sizer" aria-hidden="true" inert>${this._outcomeMarkup(item, ctx, outcomeOf(ctx, id), busy, language, null, id).body}</div>`).join("");
    return { header: t("cal_S1_fold"), body: `${tabs}<div class="cal-check-detail" id="cal-check-detail" role="tabpanel" aria-labelledby="cal-check-tab-${esc(check.answer)}"><div class="cal-check-outcome">${outcome.body}</div>${others}</div>`, actions };
  }

  /** The question's answers, rows of one card, and the actuator's note under them (step 1; from 1024 px the fold «Qualcosa non va?»). */
  _answersMarkup(ctx) {
    const { t } = this._context;
    const choice = (id) => `<button type="button" class="cal-choice" data-action="cal-tab-check-answer" data-id="${esc(id)}"><span class="cal-choice-text"><span class="cal-choice-title">${esc(t(`cal_P1_${id}_title`))}</span><span class="cal-choice-desc">${esc(t(`cal_P1_${id}_desc`))}</span></span><ha-icon icon="mdi:chevron-right" aria-hidden="true"></ha-icon></button>`;
    return `<div class="cal-card cal-rows">${answersOf(ctx).map(choice).join("")}</div><p class="cal-caption">${esc(t("cal_P1_note"))}</p>`;
  }

  /**
   * An outcome, D1 (Perfeziona) or D2 (Ricalibra): `body` (the answer given with «Cambia risposta» when `answer` is
   * given, the title, «Cosa possiamo fare», the warning), `action` (the wizard's button) and, for a follower's
   * «Posizionamento» from 1024 px, `inline`: what to do, the warning and the button, under the block's own
   * title. `chosen` is the answer the outcome follows (shown or not, from 1024 px it is the selected
   * tab): «Non so» leads to D2 with a title and an introduction of its own; D1 has its own too.
   */
  _outcomeMarkup(item, ctx, outcome, busy, language, answer = null, chosen = answer) {
    const { t } = this._context, key = JSON.stringify([item.entry.entry_id, item.cover.entity_id]);
    const tx = (id) => fill(t(id), { cover: item.name });
    // «Cosa possiamo fare». The answer given, as it was chosen, with «Cambia risposta» (none on a follower: no question).
    const answered = answer ? `<p class="cal-answer"><span>${esc(t(`cal_P1_${answer}_title`))}</span><button type="button" class="cal-answer-change" data-action="cal-tab-check-change">${esc(t("cal_D0_change"))}</button></p>` : "";
    const head = (title = "", lead = "") => answered || title ? `<div class="cal-step">${answered}${title ? `<h3 class="cal-step-title">${esc(title)}</h3>` : ""}${lead ? `<p class="cal-step-lead">${esc(lead)}</p>` : ""}</div>` : "";
    const card = (paragraph) => `<div class="cal-card"><div class="cal-card-body"><p>${esc(paragraph)}</p></div></div>`;
    const warn = (line) => line ? `<p class="cal-note is-warning">${esc(line)}</p>` : "";
    const section = (inner) => `<section class="cal-section"><h4 class="cal-section-title">${esc(t("cal_D0_heading"))}</h4>${inner}</section>`;
    // `cal_D0_warn_source_*` at the wizard's door: who else uses the profile and who may lose a check, from the overview (the
    // `loses_check`: a cover other than this one with a check stored); the parts without names are left out.
    const users = ctx.users.map((user) => user.name), checks = ctx.users.filter((user) => user.check).map((user) => user.name);
    const warnSource = ctx.role === "source" && users.length ? [fill(t(users.length === 1 ? "cal_D0_warn_source_names_one" : "cal_D0_warn_source_names"), { names: names(users, language) }),
      checks.length ? fill(t(checks.length === 1 ? "cal_D0_warn_source_checks_one" : "cal_D0_warn_source_checks"), { checks: names(checks, language) }) : "", t("cal_D0_warn_source_new")].filter(Boolean).join(" ") : "";
    // On a follower only what is true today (no word on the inherited curve).
    // Without the cover's name (it is in the bar).
    const warnFollower = ctx.role === "follower" ? t("cal_D0_warn_follower_today") : "";
    const primary = (action, label, icon) => `<button type="button" class="primary" data-action="${action}" data-id="${esc(key)}"${busy ? " disabled" : ""}>${text.iconMarkup(icon)}${esc(label)}</button>`;
    let todo, warning, action = "";
    if (outcome === "D1") {
      todo = card(t("cal_D1_todo")); warning = warn(warnSource);
      if (this._wizard) action = primary("cal-tab-refine", t("cal_D1_button"), ICON.refine);
    } else {
      // Without slats, no word on them.
      todo = card(t(ctx.slats ? "cal_D2_todo" : ctx.noSlats ? "cal_D2_todo_noslats" : "cal_D2_todo_generic")); warning = warn(warnSource || warnFollower);
      if (this._wizard) action = primary("cal-tab-measure-cover", t("cal_D2_button"), ICON.calibrate);
    }
    const body = (outcome === "D1" ? head(tx("cal_D1_title"), t("cal_D1_lead"))
      : chosen === "unsure" ? head(tx("cal_D2_title_unsure"), t("cal_D2_lead_unsure")) : head(tx("cal_D2_title"), t("cal_D2_lead"))) + section(todo + warning);
    return { body, action, inline: `<div class="cal-pos-outcome">${todo}${warning}${action ? `<div class="cal-pos-action">${action}</div>` : ""}</div>` };
  }

  /**
   * «Dettagli»: «Valori del profilo e di questa tapparella» (`_compareMarkup`, its
   * legend and ONE tail: the source's or the follower's), «Ultima calibrazione» (the date and «Come», in plain words),
   * «Ultima verifica» (the date, where, the gap; «Nessuna verifica» without one) and «Profilo usato anche da» (on the
   * source too: the crown on the cover that measures the profile, the chain on those that follow it, each name leading to
   * its own details; «Nessun’altra tapparella lo segue»). From 1024 px the table on the left, the rest on the right.
   */
  _detailsMarkup(item, ctx, language, wide = false) {
    const { t } = this._context, profile = item.profile;
    const section = (title, card, kind) => `<section class="cal-section cal-page-${kind}"><h4 class="cal-section-title">${esc(title)}</h4>${card}</section>`;
    // The tail is its text alone (no chain before it).
    const tail = (content) => `<p class="cal-card-tail"><span>${content}</span></p>`;
    // The source's tail says whether the profile is shared yet; a follower's whether its travel is the
    // profile's (a presumed travel, said, never hidden), or its own; with a profile that has no reference
    // travel, that the travel entered only gives positions in cm.
    const presumed = item.cover.travel_cm == null && profile?.reference_travel_cm > 0;
    const ending = ctx.role === "source" ? tail(esc(t(ctx.users.length ? "calTabNoteSource" : "calTabNoteSourceAlone")))
      : ctx.role === "follower" ? tail(!(profile?.reference_travel_cm > 0) ? esc(t("calTabTravelNoReference")) : presumed ? text.units(esc(t("calTabTravelFromProfileHelp"))) : esc(t("calTabNoteFollower"))) : "";
    // A cover calibrated without a profile, its own values in the same place, one column.
    const compare = profile ? section(t("calTabCompare"), `<div class="cal-card cal-page-compare">${this._compareMarkup(item, language)}${ending}</div>`, "values")
      : ctx.role === "alone" ? section(t("calTabOwnValues"), `<div class="cal-card cal-page-compare">${this._ownValuesMarkup(item, language)}</div>`, "values") : "";
    // The last thing done on THIS cover; a follower has no date of its own, only «Come».
    const last = section(t("calTabLastCalibration"), `<div class="cal-card">${pairsMarkup([
      ...(ctx.role === "follower" ? [] : [[t("calTabDate"), item.last ? text.dateTimeText(item.last, language) : "—"]]),
      [t("calTabHow"), this._howText(item)]])}</div>`, "last");
    // A check kept at the last save, its date, where (said only at half the travel) and its gap.
    const checked = item.cover.check_cm != null && item.check != null;
    const checkRows = checked ? [[t("calTabDate"), item.cover.check_at ? text.dateTimeText(item.cover.check_at, language) : "—"],
      ...(checkAtHalf(item.cover, item.profile) ? [[t("calTabCheckWhere"), t("calTabCheckWhereHalf")]] : []),
      [t("calTabCheckGap"), `${text.gapText(item.check, language)} cm`]]
      : [[t("calTabDate"), "", "", `<span class="cal-pairs-note">${esc(t("calTabCheckNone"))}</span>`, "cal-pairs-none"]];
    const check = section(t("calTabLastCheck"), `<div class="cal-card">${pairsMarkup(checkRows)}</div>`, "check");
    const users = profile ? section(t("calTabAlsoUsedBy"), this._usersMarkup(item, ctx, language), "users") : "";
    if (!wide) return `${compare}${last}${check}${users}`;
    const column = (inner) => `<div class="cal-page-column">${inner}</div>`;
    return compare ? `${column(compare)}${column(`${last}${check}${users}`)}` : `${column(`${last}${check}`)}${column(users)}`;
  }

  /**
   * «Come», in plain words, for every method of today: the guided procedure, «con il metro» where the rolls
   * were measured with it (not entered by hand); the automatic one; linked to the profile (its times taken from it,
   * entered by hand there too).
   */
  _howText(item) {
    const { t } = this._context, effective = item.cover.effective || {};
    const tape = item.rolls && ["opening_roll", "closing_roll"].every((roll) => effective[roll]?.provenance?.source === "guided");
    const key = item.entered || item.method === "inherited" ? "calTabHow_linked" : item.method === "guided" ? (tape ? "calTabHow_guided_tape" : "calTabHow_guided")
      : item.method === "automatic" ? "calTabHow_automatic" : null;
    return key ? t(key) : "—";
  }

  /**
   * The other covers on the profile, the one it was measured on first (the crown, «misura
   * il profilo»), then those that follow it (the chain, «segue il profilo»), by name; each name a link to its own details.
   */
  _usersMarkup(item, ctx, language) {
    const { t } = this._context, origin = item.profile?.provenance?.opening?.origin_entity_id;
    const users = ctx.users.map((user) => ({ ...user, source: user.id === origin }))
      .sort((a, b) => Number(b.source) - Number(a.source) || a.name.localeCompare(b.name, language));
    // «Nessun'altra tapparella lo segue» in italics (by its rule in the CSS, not in the text).
    if (!users.length) return `<div class="cal-card"><p class="cal-page-users-none">${esc(t("calTabUsersNone"))}</p></div>`;
    const row = (user) => {
      const other = this._covers().find((entry) => entry.entry.entry_id === item.entry.entry_id && entry.cover.entity_id === user.id);
      const name = other ? `<button type="button" class="cal-user-link" data-action="cal-tab-page-cover" data-id="${esc(JSON.stringify([other.entry.entry_id, other.cover.entity_id]))}">${esc(user.name)}</button>`
        : `<span class="cal-user-name">${esc(user.name)}</span>`;
      const mark = t(user.source ? "calTabUserSourceTitle" : "calTabUserFollowerTitle");
      return `<div class="cal-user${user.source ? " is-source" : ""}"><span class="cal-user-mark" title="${esc(mark)}"><ha-icon icon="${user.source ? "mdi:crown-outline" : "mdi:link-variant"}" aria-hidden="true"></ha-icon></span><span class="cal-user-text">${name}<span class="cal-user-role">${esc(t(user.source ? "calTabUserSource" : "calTabUserFollower"))}</span></span></div>`;
    };
    return `<div class="cal-card cal-rows cal-users">${users.map(row).join("")}</div>`;
  }

  /**
   * A cover calibrated without a profile, its own values: the rows of the profile's table
   * (the times, the slats' where there are slats, the travel, the coefficients when measured), one column, in the same
   * format; no legend, no marks.
   */
  _ownValuesMarkup(item, language) {
    const { t } = this._context, effective = item.cover.effective || {}, shown = valueText(language);
    const rows = [[t("wiz_T093"), shown(effective.opening?.value, 1, "s")], [t("wiz_T094"), shown(effective.closing?.value, 1, "s")],
      ...(effective.slat_time_s?.value > 0 ? [[t("wiz_T095"), shown(effective.slat_time_s.value, 1, "s")]] : []),
      [t("wiz_T041"), shown(item.cover.travel_cm, 1, "cm")],
      ...(item.rolls ? [[t("wiz_T105"), shown(effective.opening_roll?.value, 2, "")], [t("wiz_T106"), shown(effective.closing_roll?.value, 2, "")]] : [])];
    const body = rows.map(([label, value]) => `<tr><th scope="row">${esc(label)}</th><td class="cal-compare-cover">${value == null ? "—" : esc(value)}</td></tr>`).join("");
    return `<div class="cal-compare"><table class="cal-compare-table is-own"><tbody>${body}</tbody></table></div>`;
  }

  /**
   * The profile's values beside this cover's, as Home Assistant resolves them (`effective`: `origin`,
   * `scaled`; the travel against the reference): «=» the same, «→» adapted to this cover's travel, «×» this cover's own
   * (a personal time, a travel of its own), «—» missing on one side. A profile's value a personal time replaces stays in
   * grey with «sostituito qui». Each mark has its label, and a legend of the marks shown. Read only: nothing here is
   * computed in the browser, nothing written. A section of «Dettagli» of its own, always open. No column
   * of its own for the mark: it stands left of this cover's value, in its cell, at a fixed width.
   */
  _compareMarkup(item, language) {
    const { t } = this._context, profile = item.profile, effective = item.cover.effective || {};
    if (!profile) return "";
    const geometry = profile.geometry || {};
    const shown = valueText(language);
    const MARK = { same: "=", scaled: "→", own: "×", none: "—" };
    const LABEL = { same: "calTabRelSame", scaled: "calTabRelScaled", own: "calTabRelOwn", none: "calTabRelNone" };
    const relation = (mine, theirs, entry, digits) => mine == null || theirs == null ? "none"
      : entry.origin !== "profile" ? "own"
      : entry.scaled && number(entry.value, language, digits) !== number(theirs, language, digits) ? "scaled" : "same";
    const timed = (label, theirs, entry, digits, unit) => {
      const mine = entry?.value;
      return { label, theirs: shown(theirs, digits, unit), mine: shown(mine, digits, unit), kind: relation(mine, theirs, entry || {}, digits),
        replaced: entry?.origin === "override" && theirs != null };
    };
    const reference = profile.reference_travel_cm, travel = item.cover.travel_cm;
    const rows = [timed(t("wiz_T093"), profile.opening_time, effective.opening, 1, "s"), timed(t("wiz_T094"), profile.closing_time, effective.closing, 1, "s"),
      ...(geometry.slat_time_s > 0 || effective.slat_time_s?.value > 0 ? [timed(t("wiz_T095"), geometry.slat_time_s > 0 ? geometry.slat_time_s : null, effective.slat_time_s, 1, "s")] : []),
      { label: t("wiz_T041"), theirs: shown(reference, 1, "cm"), mine: shown(travel ?? reference, 1, "cm"),
        kind: reference == null || (travel ?? reference) == null ? "none" : travel != null && number(travel, language) !== number(reference, language) ? "own" : "same" },
      // The coefficients only when measured (a basic profile carries neutral ones, unknown).
      ...(item.rolls ? [timed(t("wiz_T105"), geometry.opening_roll, effective.opening_roll, 2, ""), timed(t("wiz_T106"), geometry.closing_roll, effective.closing_roll, 2, "")] : [])];
    const cell = (value) => value == null ? "—" : esc(value);
    const body = rows.map((row) => `<tr data-relation="${row.kind}"><th scope="row">${esc(row.label)}</th>
        <td class="cal-compare-profile${row.replaced ? " is-replaced" : ""}">${cell(row.theirs)}${row.replaced ? `<span class="cal-compare-note">${esc(t("calTabReplaced"))}</span>` : ""}</td>
        <td class="cal-compare-cover"><span class="cal-compare-mark" title="${esc(t(LABEL[row.kind]))}"><span aria-hidden="true">${MARK[row.kind]}</span><span class="visually-hidden">${esc(t(LABEL[row.kind]))}</span></span><span class="cal-compare-value">${cell(row.mine)}</span></td></tr>`).join("");
    const used = Object.keys(MARK).filter((kind) => rows.some((row) => row.kind === kind));
    const legend = `<p class="cal-compare-legend" aria-hidden="true">${used.map((kind) => `<span><b>${MARK[kind]}</b> ${esc(t(LABEL[kind]))}</span>`).join("")}</p>`;
    return `<div class="cal-compare"><table class="cal-compare-table"><thead><tr><td></td><th scope="col">${esc(t("calTabCompareProfile"))}</th><th scope="col">${esc(t("calTabCompareCover"))}</th></tr></thead><tbody>${body}</tbody></table>${legend}</div>`;
  }

  /**
   * The curtain travel's value with a thin pencil (`mdi:pencil-outline`); only
   * on a follower whose own travel differs from the profile's, the chain «Usa la corsa del profilo» (asked first). The
   * pencil edits IN THE ROW, at its height: a narrow field with «cm», a tick that saves and ✕ that cancels (Enter, Escape);
   * a refusal or a question under the row only when there is one. What a travel entered does is Home Assistant's
   * `travel_edit`: on the profile's source it is the reference too, so followers with a travel of their own
   * change their times: asked first from the tick («Cambia anche i tempi di N tapparelle…», Annulla / Conferma).
   * The field left empty and confirmed clears the travel, by case: without a reference (`display`) at once;
   * on a follower (`scaled`) back to the profile's travel, asked as the chain; on the source (`reference`) the profile's
   * reference goes too, asked only when covers that follow it adapt their times to a travel of their own («Le N
   * tapparelle…»). 0 stays refused. A note under the row says so while it is edited. Never during a session.
   */
  _travelMarkup(item, shown, note, source) {
    const { t, hass } = this._context, key = JSON.stringify([item.entry.entry_id, item.cover.entity_id]);
    const edit = this._travelEdit?.key === key ? this._travelEdit : null;
    const value = `<span class="cal-travel-value">${esc(shown)}${note ? ` <span class="cal-pairs-note">· ${esc(note)}</span>` : ""}</span>`;
    if (this._running(item)) return { html: value };
    const icon = (action, mdi, label, extra = "") => `<button type="button" class="icon-button cal-inline-icon" data-action="${action}" data-id="${esc(key)}" aria-label="${esc(label)}" title="${esc(label)}"${extra}><ha-icon icon="${mdi}" aria-hidden="true"></ha-icon></button>`;
    const button = (action, label, kind) => `<button type="button" class="${kind}" data-action="${action}" data-id="${esc(key)}"${edit?.busy ? " disabled" : ""}>${esc(label)}</button>`;
    const reference = item.profile?.reference_travel_cm;
    const edit_ = item.cover.travel_edit ?? (source ? "reference" : reference > 0 ? "scaled" : "display");
    const unlinkable = edit_ === "scaled" && item.cover.travel_cm != null && item.cover.travel_cm !== reference;
    const below = (inner) => `<div class="cal-inline-below">${inner}</div>`;
    const question = (text, confirm) => below(`<p class="cal-travel-confirm">${esc(text)}</p><span class="cal-travel-buttons">${button("cal-tab-travel-cancel", t("wiz_T025"), "cal-outlined")}${button(confirm, t("calTabTravelConfirmAction"), "primary")}</span>`);
    const error = edit?.error ? below(`<p class="notice error cal-travel-error" role="alert">${esc(edit.error)}</p>`) : "";
    if (!edit || edit.unlink) {
      const icons = `<span class="cal-inline-icons">${icon("cal-tab-travel-edit", "mdi:pencil-outline", t("calTabTravelEdit"))}${unlinkable ? icon("cal-tab-travel-unlink", "mdi:link-variant", t("calTabTravelUseProfile")) : ""}</span>`;
      return { html: `${value}${icons}`, below: edit?.unlink ? question(fill(t("calTabTravelUseProfileConfirm"), { cm: number(reference, hass.language || "en") }), "cal-tab-travel-unlink-confirm") + error : error };
    }
    const current = edit.value ?? String(item.cover.travel_cm ?? reference ?? "").replace(".", (hass.language || "en").startsWith("it") ? "," : ".");
    const field = `<span class="cal-inline-field"><input type="text" name="travel_cm" inputmode="decimal" autocomplete="off" enterkeyhint="done" value="${esc(String(current))}" aria-label="${esc(t("wiz_T041"))}"${edit.error ? ' aria-invalid="true"' : ""}${edit.busy || edit.confirm ? " disabled" : ""}><span class="cal-travel-unit">cm</span></span>`;
    const icons = `<span class="cal-inline-icons">${icon("cal-tab-travel-save", "mdi:check", t("calTabTravelSave"), edit.busy || edit.confirm ? " disabled" : "")}${icon("cal-tab-travel-cancel", "mdi:close", t("wiz_T025"), edit.busy ? " disabled" : "")}</span>`;
    const asked = edit.clear ? (edit.confirm === 1 ? "calTabTravelClearConfirmOne" : "calTabTravelClearConfirm") : edit.confirm === 1 ? "calTabTravelConfirmOne" : "calTabTravelConfirm";
    const ask = edit.confirm ? question(fill(t(asked), { count: edit.confirm }), "cal-tab-travel-confirm") : "";
    const help = ask ? "" : below(`<p class="muted cal-travel-help">${esc(t(edit_ === "scaled" ? "calTabTravelHelpFollower" : "calTabTravelHelp"))}</p>`);
    return { html: `${field}${icons}`, kind: "cal-pairs-travel is-editing", below: help + ask + error };
  }

  /**
   * The row «Lamelle» of «Profilo e valori». A cover that follows a profile takes
   * its slats from it, read only: «Sì · misurato» / «No · misurato» on the cover it was measured on, «Sì · dal profilo» /
   * «No · dal profilo» on the others (to change them: another profile, or a calibration). A cover without a profile says
   * them on its card: «Sì» / «No» / «Non indicato», edited in the row as the travel (a choice, the tick saves at once, no
   * question; ✕ or Escape cancel; Home Assistant's `manual_slats`). No standing notice here: a contradiction is asked
   * when a profile is given (`slatsConflict`).
   */
  _slatsMarkup(item, state, follower = false) {
    const { t } = this._context, key = JSON.stringify([item.entry.entry_id, item.cover.entity_id]);
    const edit = this._slatsEdit?.key === key ? this._slatsEdit : null;
    // The source or a cover alone says only «Sì» / «No» (no label on its own values); a follower «· dal profilo».
    const label = { yes_measured: follower ? "calTabSlatsMarkMeasured" : "calTabSlatsMarkYes", no_measured: follower ? "calTabSlatsMarkMeasuredNo" : "calTabSlatsMarkNo", yes_profile: "calTabSlatsMarkProfileYes",
      no_profile: "calTabSlatsMarkProfileNo", yes: "calTabSlatsMarkYes", no: "calTabSlatsMarkNo" }[state] || "calTabSlatsMarkUnknown";
    const value = `<span class="cal-travel-value">${esc(t(label))}</span>`;
    const icon = (action, mdi, text, extra = "") => `<button type="button" class="icon-button cal-inline-icon" data-action="${action}" data-id="${esc(key)}" aria-label="${esc(text)}" title="${esc(text)}"${extra}><ha-icon icon="${mdi}" aria-hidden="true"></ha-icon></button>`;
    const error = edit?.error ? `<div class="cal-inline-below"><p class="notice error cal-travel-error" role="alert">${esc(edit.error)}</p></div>` : "";
    if (item.profile || this._running(item)) return { html: value, kind: "cal-pairs-travel cal-pairs-slats" };
    if (!edit) return { html: `${value}<span class="cal-inline-icons">${icon("cal-tab-slats-edit", "mdi:pencil-outline", t("calTabSlatsEdit"))}</span>`, kind: "cal-pairs-travel cal-pairs-slats", below: error };
    const chosen = edit.value ?? (state === "yes" ? "yes" : state === "no" ? "no" : "");
    const option = (value_, key_) => `<option value="${value_}"${chosen === value_ ? " selected" : ""}>${esc(t(key_))}</option>`;
    const field = `<span class="cal-inline-field"><select name="slats_mark" aria-label="${esc(t("calTabSlatsRow"))}"${edit.busy ? " disabled" : ""}>${option("yes", "calTabSlatsMarkYes")}${option("no", "calTabSlatsMarkNo")}${option("", "calTabSlatsMarkUnknown")}</select></span>`;
    return { html: `${field}<span class="cal-inline-icons">${icon("cal-tab-slats-save", "mdi:check", t("calTabTravelSave"), edit.busy ? " disabled" : "")}${icon("cal-tab-slats-cancel", "mdi:close", t("wiz_T025"), edit.busy ? " disabled" : "")}</span>`,
      kind: "cal-pairs-travel cal-pairs-slats is-editing", below: error };
  }

  /** The row «Lamelle»'s actions: edit, cancel, save (no question). */
  async _slatsAction(action, key) {
    const item = this._item(key);
    if (!item) return;
    if (action === "cal-tab-slats-edit") { this._slatsEdit = { key }; this._render(); this._pageDialog?.querySelector('select[name="slats_mark"]')?.focus({ preventScroll: true }); return; }
    if (action === "cal-tab-slats-cancel") { this._slatsEdit = null; this._render(); return; }
    const { hass, t } = this._context, data = this._rows.get(item.entry.entry_id)?.data, generation = this._generation;
    const chosen = this._pageDialog?.querySelector('select[name="slats_mark"]')?.value ?? "";
    const slats = chosen === "yes" ? true : chosen === "no" ? false : null;
    this._slatsEdit = { key, value: chosen, busy: true };
    this._render();
    try {
      await hass.callWS({ type: "myhome/cover_profiles/write", entry_id: item.entry.entry_id, entity_id: item.cover.entity_id,
        revision: data?.revision, action: "manual_slats", slats });
      if (generation !== this._generation) return;
      this._slatsEdit = null;
      this._touched(key);
      this.refresh();
    } catch (error) {
      if (generation !== this._generation) return;
      const code = `profileError_${error?.code}`;
      this._slatsEdit = { key, value: chosen, error: t(code) === code ? t("profileError") : t(code) };
      this._render();
    }
  }

  /** How many covers following this one's profile would get other times from a travel written on it (its source). */
  _travelFollowers(item, travel) {
    const profile = item.profile;
    const edit = item.cover.travel_edit ?? (profile?.provenance?.opening?.origin_entity_id === item.cover.entity_id ? "reference" : "other");
    if (!profile || edit !== "reference" || travel === (profile.reference_travel_cm ?? null)) return 0;
    return this._ownTravelFollowers(item);
  }

  /** Only a follower with a travel of its own adapts its times to a reference (Home Assistant's `scaled()`). */
  _ownTravelFollowers(item) {
    const data = this._rows.get(item.entry.entry_id)?.data, profile = item.profile;
    if (!data || !profile) return 0;
    return profile.assigned_to.filter((id) => id && id !== item.cover.entity_id && data.covers.find((cover) => cover.entity_id === id)?.travel_cm != null).length;
  }

  /** The travel's actions on a cover's page: edit, cancel, save (asking first when followers change), confirm, use the profile's. */
  async _travelAction(action, key) {
    const item = this._item(key);
    if (!item) return;
    const { t } = this._context, field = this._pageDialog?.querySelector('.cal-inline-field input[name="travel_cm"]');
    if (action === "cal-tab-travel-edit") {
      this._travelEdit = { key }; this._render();
      const input = this._pageDialog?.querySelector('.cal-inline-field input[name="travel_cm"]');
      input?.focus({ preventScroll: true }); input?.select?.();
      return;
    }
    if (action === "cal-tab-travel-cancel") { this._travelEdit = null; this._render(); return; }
    if (action === "cal-tab-travel-unlink") { this._travelEdit = { key, unlink: true }; this._render(); return; }
    const edit = this._travelEdit?.key === key ? this._travelEdit : { key };
    let travel;
    if (action === "cal-tab-travel-unlink-confirm") travel = null;
    else if (action === "cal-tab-travel-confirm") travel = edit.travel;
    else if (!String(field?.value ?? "").trim()) {
      // Empty and confirmed: the travel is cleared, as its case says (`travel_edit`).
      const kind = item.cover.travel_edit ?? (item.profile?.provenance?.opening?.origin_entity_id === item.cover.entity_id ? "reference"
        : item.profile?.reference_travel_cm > 0 ? "scaled" : "display");
      if (item.cover.travel_cm == null && !(kind === "reference" && item.profile?.reference_travel_cm > 0)) { this._travelEdit = null; this._render(); return; }
      if (kind === "scaled") { this._travelEdit = { key, unlink: true }; this._render(); return; }
      const following = kind === "reference" ? this._ownTravelFollowers(item) : 0;
      if (following) { this._travelEdit = { key, value: "", travel: null, confirm: following, clear: true }; this._render(); return; }
      travel = null;
    } else {
      const typed = String(field?.value ?? "").trim(), value = Number(typed.replace(",", "."));
      if (!/^\d+(?:[.,]\d+)?$/.test(typed) || !Number.isFinite(value) || value < 10 || value > 1000) {
        this._travelEdit = { key, value: field?.value ?? "", error: t("calTabTravelInvalid") };
        this._render();
        this._pageDialog?.querySelector('.cal-inline-field input[name="travel_cm"]')?.focus({ preventScroll: true });
        return;
      }
      travel = Math.round(value * 10) / 10;
    }
    const changing = travel === null ? 0 : this._travelFollowers(item, travel);
    if (changing && action !== "cal-tab-travel-confirm") { this._travelEdit = { key, value: field?.value, travel, confirm: changing }; this._render(); return; }
    await this._writeTravel(item, key, travel);
  }

  async _writeTravel(item, key, travel) {
    const { hass, t } = this._context, data = this._rows.get(item.entry.entry_id)?.data, generation = this._generation;
    this._travelEdit = { ...(this._travelEdit?.key === key ? this._travelEdit : { key }), busy: true, travel };
    this._render();
    try {
      await hass.callWS({ type: "myhome/cover_profiles/write", entry_id: item.entry.entry_id, entity_id: item.cover.entity_id,
        revision: data?.revision, action: "manual_travel", travel_cm: travel });
      if (generation !== this._generation) return;
      this._travelEdit = null;
      this._touched(key);
      this.refresh();
    } catch (error) {
      if (generation !== this._generation) return;
      const code = `profileError_${error?.code}`;
      this._travelEdit = { key, travel, error: t(code) === code ? t("profileError") : t(code) };
      this._render();
    }
  }

  /**
   * A cover's details in the overlay's dialog over the list, on every window: the tendina up to
   * 511 px, 480 px centred up to 1023 px, 880 px from 1024 px. Its markup is patched on every drawing of the list (the
   * data changes under it); Escape, its ✕, a drag of the tendina and the browser's Back close it. What it
   * leads to takes its place in the same frame: «Cambia profilo» (`_assignProfile`), a flow (Refine, Recalibrate).
   */
  _openPage(key, { how = false } = {}) {
    // What opened the details gets the focus back when they close.
    this._pageOpener = this._openerOf();
    this._pageNotice = "";
    this._travelEdit = null;
    this._slatsEdit = null;
    this._page = key;
    // Always on its first tab when opened, from a row, its ⋮, another tab or the wizard.
    this._pageView = { key, tab: "overview", check: null, fold: false };
    this._layerPush();
    this._render();
    // The list's Calibra (profiles in the catalogue) opens it on «Come vuoi calibrarla?», scrolled to it, the
    // focus on its first choice.
    const block = how ? this._pageDialog?.querySelector(".cal-pos-how") : null;
    const entry = block?.querySelector(".cal-choice:not(:disabled)");
    if (entry) { block.scrollIntoView?.({ block: "nearest" }); returnFocus(entry); return; }
    // Nothing looks focused when the details open: the focus rests on the dialog's own box (no ring; the
    // keyboard's Tab goes on from there, Escape and a screen reader work as before).
    this._pageDialog?.querySelector(".cal-flow-box")?.focus({ preventScroll: true });
  }

  /**
   * A cover named in «Profilo usato anche da» opens its own details in the same frame, on «Panoramica» (the
   * history keeps one entry; the ✕ leads back to the list as before).
   */
  _switchPage(key) {
    if (!this._page || key === this._page || !this._item(key)) return;
    this._pageNotice = "";
    this._travelEdit = null;
    this._slatsEdit = null;
    this._page = key;
    this._pageView = { key, tab: "overview", check: null, fold: false };
    this._renderView();
    this._pageDialog?.querySelector(".cal-flow-box")?.focus({ preventScroll: true });
  }

  /** «Qualcosa non va?» opened or closed by the reader stays so across the redraws of the card. */
  _folded(event) {
    const fold = event.target;
    if (!fold?.matches?.("details.cal-fold") || !this._pageView || !fold.closest?.(".cal-page")) return;
    this._pageView.fold = fold.open;
  }

  /** The card on one of its two tabs (the procedure, if open, leaves); the focus on the tab chosen. */
  _pageTabTo(tab) {
    if (!this._page || !["overview", "details"].includes(tab)) return;
    const view = this._view();
    view.tab = tab;
    view.check = null;
    this._renderView();
    returnFocus(this._pageDialog?.querySelector(`#cal-page-tab-${tab}`));
  }

  /**
   * A new view of the card (a tab, the question, an outcome) drawn with the overlay sliding to its new
   * height; the redraws of the same view (the reads every 15 s) go through `_render` and never move it.
   */
  _renderView() {
    const box = this._pageDialog?.isConnected ? this._pageDialog.querySelector(".cal-flow-box") : null;
    if (box) slideHeight(box, () => this._render()); else this._render();
  }

  /**
   * The procedure on a step (null: back to «Panoramica», its entry focused; from 1024 px the
   * answer that was given). `focus`: what takes the focus instead of ‹ (an answer's tab beside the outcome).
   */
  _checkTo(check, focus = null) {
    if (!this._page) return;
    const view = this._view(), was = view.check;
    view.check = check;
    if (!check) view.tab = "overview";
    // Another answer beside the outcome is the same page: drawn in place, the overlay does not slide.
    if (was?.step === 2 && check?.step === 2 && layout.wide()) this._render(); else this._renderView();
    const dialog = this._pageDialog;
    const back = () => dialog?.querySelector(`.cal-pos-help [data-action="cal-tab-check-answer"][data-id="${was?.answer}"]`)
      || dialog?.querySelector('.cal-pos-help [data-action="cal-tab-check-start"]') || dialog?.querySelector(".cal-pos-help button:not(:disabled)");
    returnFocus(check ? (focus && dialog?.querySelector(focus)) || dialog?.querySelector(".cal-page-back") : back());
  }

  /** Whether the cover's procedure has its question (a follower has none: straight to Ricalibra). */
  _checkAsks() {
    const item = this._page ? this._item(this._page) : null;
    return !!item && answersOf(this._checkContext(item)).length > 0;
  }

  /** The procedure's first view: the question, or on a follower its outcome at once. */
  _checkStart() { this._checkTo(this._checkAsks() ? { step: 1 } : { step: 2, answer: null }); }

  /**
   * ‹ and «Indietro»: the outcome to the question (or, without one, to the card), the question to the tab it started from.
   * From 1024 px the question is in «Panoramica» (its fold): the outcome goes back there.
   */
  _checkBack() {
    const check = this._pageView?.check;
    if (!check) return;
    this._checkTo(check.step === 2 && this._checkAsks() && !layout.wide() ? { step: 1 } : null);
  }

  /** An answer chosen from «Panoramica»'s fold or beside the outcome (its tab, focused); on the phone from the question. */
  _checkAnswer(id, target) {
    const item = this._page ? this._item(this._page) : null;
    if (!item || !answersOf(this._checkContext(item)).includes(id)) return;
    const tab = target?.getAttribute?.("role") === "tab";
    if (!this._pageView?.check && !(layout.wide() && target?.closest?.(".cal-pos-help"))) return;
    // Chosen in the fold, the way back finds it open.
    if (layout.wide()) this._pageView.fold = true;
    this._checkTo({ step: 2, answer: id }, tab ? `#cal-check-tab-${id}` : null);
  }

  /**
   * ONE frame for everything that opens over the list: a cover's details, «Cambia profilo», a flow. A modal
   * <dialog> in the tab's element (`cal-flow`); what it holds (`data-holds` on its body: page, assign, flow) is swapped
   * inside it, never a dialog over a dialog. Its Escape, the browser's Back, a press on its scrim and the tendina's drag
   * ask `_overlayExit`, the ✕ of what it holds (the scrim and the drag only while closing costs nothing: the content's
   * `data-closable`).
   */
  _overlayDialog() {
    const open = [this._flow, this._pageDialog].find((dialog) => dialog?.isConnected);
    if (open) return open;
    const dialog = document.createElement("dialog");
    dialog.className = "cal-flow";
    dialog.setAttribute("aria-modal", "true");
    // The tendina's handle, shown only while the content says that closing costs nothing, its room kept.
    dialog.innerHTML = '<div class="cal-flow-box" tabindex="-1" autofocus><span class="cal-flow-handle" aria-hidden="true"></span><div class="cal-flow-body"></div></div>';
    const box = dialog.querySelector(".cal-flow-box"), body = box.querySelector(".cal-flow-body");
    const closable = () => !!body.querySelector(":scope > [data-closable]");
    const undrag = dragOverlay(box, { closable, onClose: () => this._overlayExit() });
    const unscrim = closeOnScrim(dialog, { closable, box, onClose: () => this._overlayExit() });
    // The wheel on the scrim never scrolls the list under it (the details, «Cambia profilo», the wizard).
    const unwheel = holdScrimScroll(dialog);
    this._undragOverlay = () => { undrag(); unscrim(); unwheel(); };
    // Escape reaches the tab's keydown first; any other close request (Android's back) is the ✕ too.
    dialog.addEventListener("cancel", (event) => { event.preventDefault(); if (!this._escaped) this._overlayExit(); });
    this._context.host.append(dialog);
    // Nothing for the dialog to give the focus back to when it closes (Safari scrolled the page to it).
    releaseFocus(this._context.host);
    try { dialog.showModal(); } catch { dialog.setAttribute("open", ""); }
    return dialog;
  }

  /** The frame's body, set to hold `holds` (page, assign, flow): emptied when it held something else. */
  _frameBody(dialog, holds, labelledBy) {
    const box = dialog.querySelector(".cal-flow-box"), body = box.querySelector(".cal-flow-body");
    box.dataset.kind = holds === "flow" ? "flow" : "page";
    if (body.dataset.holds !== holds) { body.replaceChildren(); body.dataset.holds = holds; }
    dialog.classList.toggle("cal-page-dialog", holds !== "flow");
    // «Cambia profilo» is one column: from 1024 px its frame is as wide as its content, not the details'.
    dialog.classList.toggle("cal-assign-dialog", holds === "assign");
    dialog.classList.remove("is-leaving");
    if (labelledBy) dialog.setAttribute("aria-labelledby", labelledBy);
    return body;
  }

  /** The ✕ of what the frame holds: the flow's exit, «Cambia profilo»'s (to the list), the details' close. */
  _overlayExit() {
    if (this._mode.kind === "wizard") { this._flowExit(); return; }
    if (this._page || this._assign) this._closePage();
  }

  _syncPage(busy, language) {
    // «Cambia profilo» holds the frame: the details wait under it, untouched.
    if (this._assign) { if (!this._pageDialog?.isConnected) this._assign = null; else return; }
    const item = this._page ? this._item(this._page) : null;
    if (!item) { this._page = null; this._closePageDialog(); return; }
    if (!this._pageDialog?.isConnected) { this._pageDialog = this._overlayDialog(); this._flow = null; }
    const body = this._frameBody(this._pageDialog, "page", "cal-page-name");
    morph(body, this._pageMarkup(item, busy, language));
    // A cover's page fades its content under its bar and over its actions while it goes on past them.
    const page = body.querySelector(".cal-page");
    if (page) watchEdges(page.querySelector(".cal-page-body"), { top: page.querySelector(".cal-page-head"), bottom: page.querySelector(".cal-page-actions") })();
  }

  /** The frame goes at once, whatever it holds (a flow, the details, «Cambia profilo»). */
  _dropOverlay() {
    const dialog = this._flow || this._pageDialog;
    this._flow = null;
    this._pageDialog = null;
    this._undragOverlay?.();
    this._undragOverlay = null;
    if (!dialog) return;
    try { dialog.close?.(); } catch { /* not open */ }
    dialog.remove();
  }

  /** The details' frame goes at once (the list closes, or nothing is left to show). */
  _closePageDialog() { if (this._pageDialog) this._dropOverlay(); }

  _closePage() {
    const key = this._page || this._assign?.key, dialog = this._pageDialog;
    if (!key) return;
    // The tendina slides down, the dialog fades (none with reduced motion); its entry in the browser's history goes.
    if (dialog && layout.animates() && !dialog.classList.contains("is-leaving")) {
      dialog.classList.add("is-leaving");
      setTimeout(() => { if (this._pageDialog === dialog) this._closePage(); }, layout.sheets() ? SHEET_OUT_MS : FLOW_OUT_MS);
      return;
    }
    // Back to what opened what was shown (the row, its ⋮), quietly after a tap.
    const fromList = !!this._assign && !this._assign.fromPage, opener = fromList ? this._assign.opener : this._pageOpener;
    this._page = null;
    this._pageView = null;
    this._assign = null;
    this._pageNotice = "";
    this._travelEdit = null;
    this._slatsEdit = null;
    this._layerPop();
    this._render();
    this._returnTo(opener, key, fromList ? "cal-tab-menu" : "cal-tab-toggle");
  }

  /** The control that has the focus now, as an action and its id (the list is redrawn under it). */
  _openerOf() {
    const active = this._context?.host.getRootNode?.()?.activeElement;
    return active?.dataset?.action ? { action: active.dataset.action, id: active.dataset.id ?? null } : null;
  }

  /**
   * The focus back on `opener` in the list (the same action on the same cover, shown), or else on the row
   * itself or its ⋮ (`fallback` first: a menu item gone with its menu leads to ⋮); never scrolling, its ring only after a key.
   */
  _returnTo(opener, key = null, fallback = "cal-tab-menu") {
    const host = this._context?.host;
    if (!host) return;
    const visible = (element) => !element.closest("[hidden], dialog");
    const match = (action, id) => [...host.querySelectorAll(`[data-action="${action}"]`)].find((element) => (element.dataset.id ?? null) === id && visible(element));
    const other = fallback === "cal-tab-menu" ? "cal-tab-toggle" : "cal-tab-menu";
    returnFocus((opener && match(opener.action, opener.id)) || (key && (match(fallback, key) || match(other, key))) || null);
  }

  /** Where the times of a never measured cover come from: the integration's default or the YAML configuration. */
  _originNote(item, language) {
    const effective = item.cover.effective, origin = effective.opening?.origin;
    if (item.status !== "to_measure" || !["default", "yaml"].includes(origin) || effective.closing?.origin !== origin) return "";
    const [opening, closing] = [effective.opening.value, effective.closing.value].map((value) => number(value, language));
    // «21 / 20 s» never breaks inside.
    return `<p class="cal-note${this._running(item) ? " is-warning" : ""} cal-origin-note">${text.units(esc(fill(this._context.t(origin === "yaml" ? "wiz_T378" : "wiz_T377"),
      { seconds: opening === closing ? opening : `${opening}\u00a0/\u00a0${closing}` })))}</p>`;
  }

  _item(key) {
    const [entryId, entityId] = JSON.parse(key);
    return this._covers().find((item) => item.entry.entry_id === entryId && item.cover.entity_id === entityId);
  }

  _keydown(event) {
    if (this._mode.kind === "wizard") { this._flowKeydown(event); return; }
    // In the travel's field Enter saves and Escape leaves the field, not the page.
    if (event.target?.name === "slats_mark" && this._slatsEdit && ["Enter", "Escape"].includes(event.key)) {
      event.preventDefault(); event.stopPropagation();
      this._escaped = event.key === "Escape";
      if (this._escaped) setTimeout(() => { this._escaped = false; }, 0);
      this._slatsAction(event.key === "Enter" ? "cal-tab-slats-save" : "cal-tab-slats-cancel", this._slatsEdit.key);
      return;
    }
    if (event.target?.name === "travel_cm" && this._travelEdit && !this._travelEdit.confirm && ["Enter", "Escape"].includes(event.key)) {
      event.preventDefault();
      event.stopPropagation();
      this._escaped = event.key === "Escape";
      if (this._escaped) setTimeout(() => { this._escaped = false; }, 0);
      this._travelAction(event.key === "Enter" ? "cal-tab-travel-save" : "cal-tab-travel-cancel", this._travelEdit.key);
      return;
    }
    // ↑ ↓ (← →, Home, End) move between the answers beside the outcome, its outcome following.
    if (this._page && event.target?.getAttribute?.("role") === "tab" && event.target.closest?.(".cal-check-answers") && ["ArrowUp", "ArrowDown", "ArrowLeft", "ArrowRight", "Home", "End"].includes(event.key)) {
      event.preventDefault();
      const ids = [...event.target.closest(".cal-check-answers").querySelectorAll('[role="tab"]')].map((tab) => tab.dataset.id);
      const at = ids.indexOf(event.target.dataset.id), step = ["ArrowUp", "ArrowLeft"].includes(event.key) ? -1 : 1;
      const next = event.key === "Home" ? ids[0] : event.key === "End" ? ids.at(-1) : ids[(at + step + ids.length) % ids.length];
      if (next && next !== this._pageView?.check?.answer) this._checkTo({ step: 2, answer: next }, `#cal-check-tab-${next}`);
      return;
    }
    // ← → (Home, End) move between the card's two tabs.
    if (this._page && event.target?.getAttribute?.("role") === "tab" && event.target.closest?.(".cal-page-tabs") && ["ArrowLeft", "ArrowRight", "Home", "End"].includes(event.key)) {
      event.preventDefault();
      this._pageTabTo(event.key === "Home" ? "overview" : event.key === "End" ? "details" : this._view().tab === "details" ? "overview" : "details");
      return;
    }
    if (event.key === "Escape") { this._escaped = true; setTimeout(() => { this._escaped = false; }, 0); }
    // Escape is the ✕ of what the frame holds: the details, «Cambia profilo» (to the list).
    if ((this._page || this._assign) && this._menu === null && this._mode.kind === "list" && event.key === "Escape") { event.preventDefault(); this._closePage(); return; }
    if (this._menu === null || this._mode.kind !== "list") return;
    if (["ArrowDown", "ArrowUp"].includes(event.key)) {
      // Menu items move with the arrows and wrap, as in Home Assistant's own menus; a disabled one is skipped (
      // a cover in progress, or a gateway busy with another, has some).
      const items = [...this._context.host.querySelectorAll('.cal-menu:not([hidden]) [role="menuitem"]:not(:disabled)')];
      const index = items.indexOf(this._context.host.getRootNode().activeElement);
      if (!items.length) return;
      event.preventDefault();
      items[(index + (event.key === "ArrowDown" ? 1 : -1) + items.length) % items.length].focus({ preventScroll: true });
      return;
    }
    if (event.key !== "Escape") return;
    const key = this._menu;
    this._dropMenu();
    this._render();
    [...this._context.host.querySelectorAll('[data-action="cal-tab-menu"]')].find((button) => button.dataset.id === key)?.focus({ preventScroll: true });
  }

  _click(event) {
    if (this._mode.kind !== "list") return;
    const target = event.target.closest("button");
    // The whole row opens and closes its details, as the maintainer's device cards do; its buttons keep their own action.
    const row = !target && !event.target.closest(".cal-menu, a, input, select") && event.target.closest(".cal-row-main");
    if (row) { row.querySelector('[data-action="cal-tab-toggle"]')?.click(); return; }
    if (!target) return;
    const { action, id } = target.dataset;
    if (action !== "cal-tab-menu" && this._menu !== null) { this._dropMenu(); if (!action?.startsWith("cal-tab-")) this._render(); }
    if (action === "cal-tab-how") {
      // «Come funziona» slides its text open and closed.
      const text = () => this._context.host.querySelector("#cal-tab-how-text");
      if (this._howSliding) return;
      if (this._howOpen) {
        target.setAttribute("aria-expanded", "false");
        this._howSliding = true;
        slideBlock(text(), false, () => { this._howSliding = false; this._howOpen = false; this._render(); });
      } else { this._howOpen = true; this._render(); slideBlock(text(), true, () => revealOpened(text())); }
      return;
    }
    if (action === "cal-tab-filter") { this._filter = id; this._render(); }
    // The empty list's way out: the tab's filter back to «Tutte» and the panel's search emptied (as typed away).
    if (action === "cal-tab-clear") {
      this._filter = "all";
      const search = this._context.host.getRootNode().getElementById?.("search");
      if (search && search.value) { search.value = ""; search.dispatchEvent(new Event("input", { bubbles: true })); }
      this._render();
    }
    else if (action === "cal-tab-sort") this._setSort(id);
    // A row (or «Dettagli» in its ⋮) opens the cover's details over the list, on every window.
    else if (action === "cal-tab-toggle") this._openPage(id);
    else if (action === "cal-tab-page-close") {
      this._closePage();
    }
    // «Cambia profilo»'s ✕ leads to the list, its «Annulla» back to what it replaced.
    else if (action === "cal-tab-assign-close") this._closePage();
    else if (action === "cal-tab-assign-cancel") this._assignBack(); else if (action === "cal-tab-menu") {
      this._menu = this._menu === id ? null : id;
      this._render();
      // The focus moves into the menu, the page never scrolls for it.
      if (this._menu) this._context.host.querySelector('.cal-menu:not([hidden]) [role="menuitem"]:not(:disabled)')?.focus({ preventScroll: true });
    } else if (action === "cal-tab-refresh") this.refresh();
    else if (action === "cal-tab-session") this._openSession(id);
    else if (action === "cal-tab-abort") this._abortSession(id);
    else if (action === "cal-tab-measure") this._openWizard({ start: "choose" });
    else if (action === "cal-tab-measure-cover") { const item = this._item(id); if (item) this._openWizard({ start: "path", item }); }
    else if (action === "cal-tab-refine") { const item = this._item(id); if (item) this._openWizard({ start: "refine", item }); }
    else if (action === "cal-tab-assign") { const item = this._item(id); if (item && !this._running(item)) this._assignProfile(item); }
    // The list's Calibra, with profiles in the catalogue, opens the card on «Come vuoi calibrarla?».
    else if (action === "cal-tab-calibrate") { if (this._item(id)) this._openPage(id, { how: true }); }
    // The card's tabs, the procedure and where its outcomes lead.
    else if (action === "cal-tab-page-tab") this._pageTabTo(id);
    else if (action === "cal-tab-page-cover") this._switchPage(id);
    else if (action === "cal-tab-check-start") this._checkStart();
    else if (action === "cal-tab-check-answer") this._checkAnswer(id, target);
    else if (action === "cal-tab-check-change") { if (this._pageView?.check) this._checkTo({ step: 1 }); }
    else if (action === "cal-tab-check-back") this._checkBack();
    else if (action?.startsWith("cal-tab-travel-")) this._travelAction(action, id);
    else if (action?.startsWith("cal-tab-slats-")) this._slatsAction(action, id);
    else if (action?.startsWith("cal-tab-")) this._render();
  }

  /** Open the wizard on a cover as soon as the list knows it (the tab may still be loading). */
  requestWizard(entryId, entityId) {
    // Never queued behind an open wizard, group or session: it would open by surprise when that closes.
    if (this._mode.kind !== "list") return;
    this._pendingWizard = { entryId, entityId };
    if (this._context) this._render();
  }

  /**
   * Another tab (Profiles, Devices; docs/panel-calibration-open-cover.md) opens a cover's card
   * here, as soon as the list knows the cover. `returnTo`, when given, is called once when the card, and whatever was
   * opened from it (Cambia profilo, a flow), closes back to the list. Refused (false) while a wizard, a
   * session, a card or Cambia profilo is open: never queued behind it to open by surprise. Nothing else changes.
   */
  requestCover(entryId, entityId, { returnTo = null } = {}) {
    if (this._mode.kind !== "list" || this._page || this._assign) return false;
    this._pendingCover = { entryId, entityId, returnTo: typeof returnTo === "function" ? returnTo : null };
    if (this._context) this._render();
    return true;
  }

  _openPendingCover() {
    const pending = this._pendingCover;
    if (!pending || this._mode.kind !== "list") return false;
    const row = this._rows.get(pending.entryId);
    if (!row || (!row.data && !row.error)) return false;
    this._pendingCover = null;
    const item = this._covers().find((entry) => entry.entry.entry_id === pending.entryId && entry.cover.entity_id === pending.entityId);
    if (!item) return false;
    this._coverReturn = pending.returnTo;
    this._openPage(JSON.stringify([item.entry.entry_id, item.cover.entity_id]));
    return true;
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

  /**
   * Back to the list after a wizard, a group or an opened session; the backend is read again. Always the list
   * (the ✕ of a flow opened from a cover's page included); from the phone's page at once, from the dialog after its
   * short fade (none with reduced motion).
   */
  _backToList() {
    const flow = this._flow;
    // The tendina slides down, the dialogs fade (none with reduced motion). Not when the
    // cover's details take the flow's place in the same frame.
    if (flow && layout.animates() && !flow.classList.contains("is-leaving") && !this._showAfter) {
      flow.classList.add("is-leaving");
      setTimeout(() => { if (this._flow === flow) this._backToList(); }, layout.sheets() ? SHEET_OUT_MS : FLOW_OUT_MS);
      return;
    }
    // A cover's details asked from the wizard open now, over the list (on every window); they keep the
    // flow's entry in the browser's history (a pop and a push at once would race).
    const show = this._showAfter;
    this._showAfter = null;
    // A cover just calibrated may move in the list (its status, the order): brought into view if it left it.
    if (this._wizard?.savedKey?.()) this._touched(this._wizard.savedKey());
    // The details asked from the flow (A-34's «Apri la scheda») take its place in the same frame.
    const frame = show ? this._flow : null;
    this._leaveFlow({ keepLayer: !!show, keepFrame: !!frame });
    if (!this._context) return;
    if (show) { this._page = show; this._pageNotice = ""; this._pageOpener = this._opener; this._opener = null; if (frame) this._pageDialog = frame; }
    // Under the dialog the list stayed in place (patched).
    this._render();
    if (show) this._pageDialog?.querySelector(".cal-flow-box")?.focus({ preventScroll: true });
    else this._focusOpener();
    this.refresh();
  }

  /**
   * A flow's holder, the body of its dialog over the list on every window: the tendina up to 511 px,
   * the phone's layout centred 480 px wide up to 1023 px, the medium dialog from 1024 px.
   */
  _flowHost(labelledBy) {
    // A cover's details belong to the list: the flow replaces them (its ✕ leads to the list), keeping their
    // entry in the browser's history for itself. In the same frame, its content swapped (`_openFlowDialog`).
    const body = this._openFlowDialog();
    // Named by the flow's own title (the cover's name).
    this._flow.setAttribute("aria-labelledby", labelledBy);
    return body;
  }

  /**
   * The dialog that holds the wizard: a modal <dialog> in the tab's element, over the list and
   * its scrim. Escape and the browser's Back are its ✕ (`_flowExit`); a press on the scrim closes it only while closing
   * costs nothing (a step is never lost by accident); the keyboard stays inside it. The one
   * frame of the tab (`_overlayDialog`): opened from a cover's details, the flow takes their place in it.
   */
  _openFlowDialog() {
    if (this._flow?.isConnected) return this._flow.querySelector(".cal-flow-body");
    const dialog = this._overlayDialog();
    this._pageDialog = null;
    this._assign = null;
    this._flow = dialog;
    const body = this._frameBody(dialog, "flow");
    this._syncHold();
    return body;
  }

  _closeFlowDialog() { if (this._flow) this._dropOverlay(); }

  /** The flow is over: its dialog and its history entry go (unless what opens next keeps it), the list is the tab's view again. */
  _leaveFlow({ keepLayer = false, keepFrame = false } = {}) {
    this._mode = { kind: "list" };
    if (!keepLayer) this._layerPop();
    if (keepFrame) this._flow = null; else this._closeFlowDialog();
  }

  /**
   * The window crossed a band with a flow open: the dialog follows by itself (the stylesheet), the bar
   * takes the band's shape (the medium dialog's footer row, the phone's column elsewhere) and the confirmations theirs.
   */
  _relayoutFlow() {
    if (!this._context) return;
    if (this._mode.kind === "wizard") this._wizard?.relayout?.();
  }

  /** The ✕ of the open flow: the wizard's exit (it asks first while a session is open). */
  _flowExit() {
    if (this._mode.kind === "wizard") this._wizard?.requestExit?.();
  }

  /** Inside a flow Escape is its ✕ (after its own sheets, confirmations and menu) and Tab stays in its dialog. */
  _flowKeydown(event) {
    // An Escape already answered (a sheet, a confirmation, the ⋮ menu) or answered here never reaches the dialog's own
    // `cancel` as a second ✕.
    if (event.key === "Escape") { this._escaped = true; setTimeout(() => { this._escaped = false; }, 0); }
    if (event.defaultPrevented) return;
    if (event.key === "Escape") {
      event.preventDefault();
      this._flowExit();
      return;
    }
    if (event.key !== "Tab" || !this._flow) return;
    const focusable = [...this._flow.querySelectorAll("button, [href], input, select, textarea, summary, [tabindex]:not([tabindex='-1'])")]
      .filter((element) => !element.disabled && !element.closest("[hidden], [inert]") && !hiddenByStyle(element));
    const active = this._context.host.getRootNode().activeElement, first = focusable[0], last = focusable.at(-1);
    if (!first) return;
    if (!this._flow.contains(active)) { event.preventDefault(); first.focus(); }
    else if (event.shiftKey && active === first) { event.preventDefault(); last.focus(); }
    else if (!event.shiftKey && active === last) { event.preventDefault(); first.focus(); }
  }

  /**
   * What is open over the list (a flow, the phone's page) has one entry in the browser's history, so that its
   * Back is that view's ✕; the entry goes when the view closes by itself.
   */
  _layerPush() {
    const view = this._context?.host?.ownerDocument?.defaultView;
    if (this._layer || !view?.history) return;
    this._layer = ++layerCount;
    try { view.history.pushState({ ...(view.history.state || {}), myhomeCalibration: this._layer }, "", view.location.href); } catch { this._layer = null; }
  }

  _layerPop() {
    const id = this._layer, view = this._context?.host?.ownerDocument?.defaultView;
    this._layer = null;
    if (!id || !view?.history) return;
    try { if (view.history.state?.myhomeCalibration === id) view.history.back(); } catch { /* no history */ }
  }

  _popped(event) {
    const id = this._layer;
    // Nothing open, or the entry is still ours (a forward move into it): nothing to do.
    if (!id || event?.state?.myhomeCalibration === id) return;
    this._layer = null;
    if (this._mode.kind === "wizard") {
      this._flowExit();
      // Still open (the wizard asks «Esci senza salvare?»): it keeps an entry for the next Back (not while it fades out).
      if (this._mode.kind !== "list" && !this._flow?.classList.contains("is-leaving")) this._layerPush();
    } else if (this._page || this._assign) this._closePage();
  }

  /** Back on the list, the focus returns to the control that opened the wizard (a menu item: its row's ⋮). */
  _focusOpener() {
    const opener = this._opener, host = this._context?.host;
    this._opener = null;
    if (!opener || !host) return;
    const match = (action) => [...host.querySelectorAll(`[data-action="${action}"]`)].find((element) => (element.dataset.id ?? null) === opener.id
      && !element.closest("[hidden]"));
    // Never scrolling, its ring only after a key.
    returnFocus(match(opener.action) || (opener.id != null ? match("cal-tab-menu") : null));
  }

  /** The cover's details from the wizard, over the list (on every window; never the maintainer's dialog). */
  _showCover(entityId) {
    const item = this._covers().find((entry) => entry.cover.entity_id === entityId);
    if (!item) return;
    const key = JSON.stringify([item.entry.entry_id, item.cover.entity_id]);
    if (this._mode.kind !== "list") { this._showAfter = key; return; }
    this._openPage(key);
  }

  _openWizard(options) {
    if (!this._wizard || !this._context) return;
    // What opened the wizard, to give it the focus back when the list returns.
    const opener = this._context.host.getRootNode?.()?.activeElement;
    this._opener = opener?.dataset?.action ? { action: opener.dataset.action, id: opener.dataset.id ?? null } : null;
    this._done = "";
    // Opened from a cover's details, «Annulla» on the flow's first screen leads back to them (on
    // the tab it was on, the procedure closed).
    const fromPage = this._page && this._pageDialog?.isConnected ? this._page : null;
    if (this._pageView) this._pageView.check = null;
    this._mode = { kind: "wizard" };
    const { hass, t } = this._context;
    const host = this._flowHost("wiz-cover");
    this._layerPush();
    // The covers as they are at each step: after a save, fine-tuning (C-01) shows the values just written.
    this._wizard.open({ host, hass, t, covers: () => this._covers(), data: (entryId) => this._rows.get(entryId)?.data, ...options,
      // A-01's list, drawn as the tab's lists are, in the list's shared order.
      picker: { markup: (state) => this._pickerMarkup(state), sort: (key, dir) => this._setSort(key, dir), order: () => ({ ...this._sort }) },
      // «Open the cover's details» is ours: the row opened in place (the page on a phone), once the list is back.
      onOpenCover: (entityId) => this._showCover(entityId),
      // The A-35 step reads the gateways again (the profile just written, after an assignment).
      onRefresh: () => this.refresh(),
      onDiscarded: (entryId) => this._discarded(entryId),
      onCancelFirst: () => { if (fromPage) this._showAfter = fromPage; },
      onClose: () => this._backToList() });
  }

  /** The gateway's open session: the wizard for its own modes, the existing view, in its own dialog, for the others. */
  _openSession(entryId) {
    const row = this._rows.get(entryId), session = row?.session;
    if (!session || !this._context) return;
    const item = this._covers().find((entry) => entry.entry.entry_id === entryId && entry.cover.entity_id === session.entity_id);
    // The wizard needs the cover in the list (name, gateway); otherwise the existing view opens it.
    if (item && this._wizard?.handles?.(session)) { this._openWizard({ start: "resume", session, item }); return; }
    // Never a dialog over the details: the maintainer's view (in its own dialog for now) replaces them.
    if (this._page || this._assign) { this._page = null; this._assign = null; this._closePageDialog(); this._layerPop(); }
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
   * «Chiudi» is left out and its ✕ shown at every size, so there is one way to close and one bar.
   * `sheet`: the tab's own sheets (Assegna / Cambia profilo, the cancel confirmation) take the flows' frame
   * (`cal-sheet`): the ✕ always on the left of the header, the tendina's handle and drag up to 511 px, 480 px centred
   * from 512 px, the footer by the bars' rules. The maintainer's hosted views keep the old dialog for now.
   */
  _openDialog({ title, subtitle = "", subtitleHtml = "", icon, hosted = false, sheet = false }) {
    this._closeDialog();
    // A row's ⋮ menu that opened the dialog closes with it, not at the next redraw.
    this._dropMenu();
    if (this._mode.kind === "list" && this._context?.host.querySelector(".cal-menu:not([hidden])")) this._render();
    this._done = "";
    const { dialogHost, t } = this._context;
    // What opened the sheet gets the focus back when it closes.
    this._dialogOpener = this._openerOf();
    dialogHost.innerHTML = `<dialog class="cover-profile-dialog cal-dialog${sheet ? " cal-sheet" : ""}" tabindex="-1" aria-labelledby="cal-dialog-title"${sheet ? " data-closable" : ""}>
      ${sheet ? '<span class="cal-flow-handle" aria-hidden="true"></span>' : ""}<header class="dialog-head"><div class="dialog-icon" aria-hidden="true"><ha-icon icon="${esc(icon)}"></ha-icon></div>
        <div class="dialog-head-text"><h2 id="cal-dialog-title">${esc(title)}</h2>${subtitleHtml ? `<p class="muted">${subtitleHtml}</p>` : subtitle ? `<p class="muted">${esc(subtitle)}</p>` : ""}</div>
        <button type="button" class="icon-button cal-dialog-x" ${hosted || sheet || layout.sheets() ? "" : "hidden"} aria-label="${esc(t("close"))}" title="${esc(t("close"))}"><ha-icon icon="mdi:close" aria-hidden="true"></ha-icon></button></header>
      <div class="cal-dialog-body"></div>
      <footer class="dialog-foot" ${hosted ? "hidden" : ""}><button type="button" class="cal-dialog-close">${esc(t("close"))}</button></footer></dialog>`;
    const dialog = dialogHost.querySelector("dialog");
    dialog.querySelector(".cal-dialog-close").onclick = () => this._dialogDone();
    dialog.querySelector(".cal-dialog-x").onclick = () => this._dialogDone();
    dialog.oncancel = (event) => { event.preventDefault(); this._dialogDone(); };
    // A sheet of ours closes on its scrim only while closing costs nothing (`data-closable`, the same
    // predicate as its handle and its drag: «Cambia profilo» with no new choice made). The maintainer's hosted views keep
    // their own rule: a click on the backdrop (outside the dialog's box) closes them as Escape does, applying nothing.
    const closable = () => dialog.hasAttribute("data-closable");
    if (sheet) this._unscrimDialog = closeOnScrim(dialog, { closable, onClose: () => this._dialogDone() });
    // The wheel on the scrim never scrolls the list under it.
    this._unwheelDialog = holdScrimScroll(dialog);
    if (!sheet) dialog.addEventListener("click", (event) => {
      if (event.target !== dialog) return;
      const box = dialog.getBoundingClientRect();
      const inside = event.clientX >= box.left && event.clientX <= box.right && event.clientY >= box.top && event.clientY <= box.bottom;
      if (!inside || (!box.width && !box.height)) this._dialogDone();
    });
    releaseFocus(dialogHost);
    dialog.showModal();
    // After a tap the focus rests on the sheet itself, not on its ✕ (no ring); after a key it stays where the
    // browser put it.
    focusOnOpen(dialog, null);
    this._dialog = dialog;
    this._holdPage(dialog);
    // The tendina closes by a drag down too (it costs nothing: nothing is applied).
    if (sheet) this._undragDialog = dragOverlay(dialog, { closable, onClose: () => this._dialogDone() });
    return dialog.querySelector(".cal-dialog-body");
  }

  _closeDialog() {
    this._undragDialog?.();
    this._undragDialog = null;
    this._unscrimDialog?.();
    this._unscrimDialog = null;
    this._unwheelDialog?.();
    this._unwheelDialog = null;
    this._calibration.close();
    this._dialog?.close();
    this._dialog?.remove();
    this._dialog = null;
    this._releasePage();
  }

  /**
   * While a sheet is open on a phone the page under it does not scroll (iOS scrolls the page behind a modal
   * dialog, and the sheet moves with it): every scrolling ancestor of the panel, `html` and `body` get
   * `overflow: hidden` until the sheet closes. Only the sheet's body scrolls; its footer stays at the bottom, with the
   * fade above it while there is more to read.
   */
  _holdPage(dialog) {
    this._releasePage();
    const body = dialog.querySelector(".cal-dialog-body"), foot = dialog.querySelector(".dialog-foot");
    if (body && foot) {
      const head = dialog.querySelector(".dialog-head");
      const more = () => {
        const above = body.scrollTop > 1, below = body.scrollHeight - body.scrollTop - body.clientHeight > 1;
        foot.classList.toggle("has-more", below); head?.classList.toggle("has-above", above);
        body.classList.toggle("has-above", above); body.classList.toggle("has-more", below);
      };
      body.addEventListener("scroll", more, { passive: true });
      if (typeof ResizeObserver === "function") { this._sheetObserver = new ResizeObserver(more); this._sheetObserver.observe(body); }
      more();
    }
    // The page under the sheet held still on every window, without moving it.
    this._held = lockScroll(this._context.host);
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

  /** A profile's times' date (`profileTimesDate`), read on the cover they were measured on. */
  _timesDate(profile) {
    const id = profile?.provenance?.opening?.origin_entity_id;
    return profileTimesDate(profile, id ? this._covers().find((item) => item.cover.entity_id === id)?.cover : null);
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
      setTimeout(() => { if (this._dialog === dialog) { this._closeDialog(); this._dialogReturn(); this.refresh(); } }, 220);
      return;
    }
    if (dialog?.classList.contains("is-leaving")) return;
    this._closeDialog();
    this._dialogReturn();
    this.refresh();
  }

  /** The focus back on what opened the tab's sheet (the row's ⋮ for a menu item), quietly after a tap. */
  _dialogReturn() {
    const opener = this._dialogOpener;
    this._dialogOpener = null;
    if (!opener || this._mode.kind !== "list") return;
    let key = null;
    try { const [entryId, entityId] = JSON.parse(opener.id); key = JSON.stringify([entryId, entityId]); } catch { /* an entry id: the row is the session's */ }
    if (!key) {
      const session = this._rows.get(opener.id)?.session;
      const item = session && this._covers().find((entry) => entry.entry.entry_id === opener.id && entry.cover.entity_id === session.entity_id);
      key = item ? JSON.stringify([item.entry.entry_id, item.cover.entity_id]) : null;
    }
    this._returnTo(opener, key);
  }

  /**
   * What the busy notice says is only what the list offers: an open session can be followed («Segui
   * sessione») or, by the tab that owns it, resumed («Riprendi sessione»), or cancelled (the row's ⋮); one closed and
   * waiting for the stop, or one that cannot be read, frees the cover by itself.
   */
  _busyText(busy) {
    if (busy.unknown) return "wiz_T540";
    return busy.waiting_for_stop || busy.recoverable === false ? "wiz_T539" : busy.client_owner ? "wiz_T581" : "wiz_T538";
  }

  /**
   * The row's «Cancel the calibration in progress», after a confirmation: this tab takes control of the
   * session and cancels it (it may be guided elsewhere, or interrupted with its tab gone); a refusal says why.
   */
  _abortSession(entryId) {
    const row = this._rows.get(entryId), session = row?.session;
    if (!session || !this._context) return;
    const { hass, t } = this._context, item = this._covers().find((entry) => entry.entry.entry_id === entryId && entry.cover.entity_id === session.entity_id);
    const name = item?.name || session.entity_id;
    // A confirmation, not the maintainer's view: its buttons on the dialog's own footer (Back in place of Close),
    // as the other confirmations of the tab, not in the body of a hosted dialog (whose bottom is the hosted view's bar).
    const body = this._openDialog({ title: t("wiz_T535"), subtitle: name, icon: "mdi:close-octagon-outline", sheet: true });
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

  /**
   * «Cambia profilo», «Assegna un profilo» / «Cambia profilo». Never a dialog over a dialog: it takes the
   * place of what the frame shows. From a cover's details it replaces them in the same window or tendina: «Annulla» leads
   * back to the details, «Assegna» back to them with the confirmation on top, ✕ (Escape, Back) to the list. From the
   * list (the row's ⋮) it opens the frame itself, and every way out leads to the list, the confirmation on top of it.
   * The header names the cover («Assegna un profilo a «…»», «Cambia il profilo di «…»»)
   * with its room and address under it.
   * `again` draws it once more in its own frame on the data just read, the choice kept where it still exists
   * and the refusal (`message`) above the choices (after a `revision_conflict`, as A-35).
   */
  _assignProfile(item, { again = null } = {}) {
    const { hass, t, gateways } = this._context, language = hass.language || "en";
    const data = this._rows.get(item.entry.entry_id)?.data;
    if (!data) return;
    // Closed, it leads back to the card's tab, not to the procedure that led here.
    if (this._pageView) this._pageView.check = null;
    const key = JSON.stringify([item.entry.entry_id, item.cover.entity_id]);
    const prior = again ? this._assign : null;
    const fromPage = prior ? prior.fromPage : !!this._page && !!this._pageDialog?.isConnected;
    const opener = prior ? prior.opener : this._openerOf();
    // A row's ⋮ menu that opened it closes with it, not at the next redraw.
    const menuOpen = !prior && (this._menu !== null || !!this._context.host.querySelector(".cal-menu:not([hidden])"));
    if (!prior) {
      this._dropMenu();
      this._done = "";
      this._pageNotice = "";
    }
    if (!fromPage && !prior) { this._layerPush(); this._pageDialog = this._overlayDialog(); this._flow = null; }
    const dialog = this._pageDialog, generation = this._generation;
    const state = this._assign = { key, fromPage, opener };
    const body = this._frameBody(dialog, "assign", "cal-assign-name");
    const box = dialog.querySelector(".cal-flow-box");
    const profiles = [...data.profiles].sort((a, b) => a.name.localeCompare(b.name));
    // Who follows each profile, by name, to recognise a similar cover.
    // «Seguito da» without the cover the profile was measured on (it follows nothing).
    // This cover among them is «questa tapparella» (its name is the sheet's title), never by name.
    const followers = (profile) => {
      const ids = profile.assigned_to.filter((id) => id && id !== profile.provenance?.opening?.origin_entity_id);
      return followedBy(t, ids.filter((id) => id !== item.cover.entity_id).map((id) => this._coverName(id)), language, ids.includes(item.cover.entity_id));
    };
    const describe = (profile) => {
      // «Con lamelle» / «Senza lamelle» from the profile's mark (or its geometry); nothing when not said.
      const slats = text.profileSlats(profile);
      const origin = profile.provenance?.opening;
      return [
        [slats == null ? "" : t(slats ? "wiz_T368" : "wiz_T369"),
          profile.reference_travel_cm != null ? fill(t("wiz_T372"), { cm: number(profile.reference_travel_cm, language) }) : ""].filter(Boolean).join(" · "),
        // The same line as the details' (markup: the cover in evidence), «questa tapparella» on its source.
        !origin?.recorded_at || !(origin.origin_entity_id || origin.origin_name) ? ""
          // The same time as on the source's own page (`profileTimesDate`).
          : origin.origin_entity_id === item.cover.entity_id ? text.inline(t("calAssignRoleSource"), { date: text.dateTimeText(this._timesDate(profile), language) })
            : fill(esc(t("wiz_T371")), { cover: `<strong>${esc(this._originName(origin))}</strong>`, date: esc(text.dateTimeText(this._timesDate(profile), language)) }),
        followers(profile),
      ];
    };
    // «Scollega il profilo» unlinks the profile the cover follows (an action says its verb; «Nessun profilo» is
    // only a state, in the list and the chips; «Non usare un profilo», wiz_T404, would be the verb of a cover without one,
    // and the sheet offers nothing to unlink there). On the cover the profile was measured on it is shown disabled: its
    // values live only in the profile until Home Assistant copies them on unlinking (not done yet).
    const followed = data.profiles.find((profile) => profile.id === item.cover.profile_id);
    const source = followed?.provenance?.opening?.origin_entity_id === item.cover.entity_id;
    const none = followed ? `<label class="wiz-radio cal-assign-none${source ? " is-disabled" : ""}"><input type="radio" name="assign_profile" value="${NO_PROFILE}"${source ? " disabled" : ""}>
        <span><strong><ha-icon icon="mdi:link-variant-off" aria-hidden="true"></ha-icon>${esc(t("wiz_T406"))}</strong><small class="muted">${esc(fill(t(source ? "wiz_T430" : "wiz_T405"), { profile: followed.name, cover: item.name }))}</small></span></label>` : "";
    // A calibration open on the gateway reserves it: Home Assistant would refuse, so the sheet says so and waits.
    const row = this._rows.get(item.entry.entry_id), reserved = !!(row?.session || row?.reserved);
    // A refusal is an error notice above the choices. The choices
    // are the rows of one card (`.cal-assign-rows`), the chosen one tinted; what assigning does under the header.
    // With the cover's slats known, the profiles that fit (or say nothing) first, the others under «Altri
    // profili (N)», open when the profile followed now is among them; nothing is left out.
    const groups = text.bySlats(profiles, text.coverSlats(item.cover), text.profileSlats);
    // Assigning keeps the cover's personal times (Home Assistant's overrides): said here.
    const personal = Object.keys(item.cover.overrides || {}).length > 0
      || ["opening", "closing"].some((direction) => item.cover.effective?.[direction]?.origin === "override");
    const radio = (profile) => {
      const [type, measured, list] = describe(profile);
      // The profile followed now says so («Profilo attuale»); the notes under the name are one block (12/16,
      // no space between them), 7 px under the name.
      const current = profile.id === item.cover.profile_id ? `<span class="cal-chip cal-current-chip">${esc(t("calTabCurrentProfile"))}</span>` : "";
      return `<label class="wiz-radio"><input type="radio" name="assign_profile" value="${esc(profile.id)}" ${profile.id === item.cover.profile_id ? "checked" : ""}>
        <span><span class="cal-assign-name"><strong>${esc(profile.name)}</strong>${current}</span><span class="cal-assign-notes">${type ? `<small class="muted">${esc(type)}</small>` : ""}${measured ? `<small class="muted cal-assign-measured">${measured}</small>` : ""}<small class="muted cal-assign-followers">${esc(list)}</small></span></span></label>`;
    };
    const others = groups.other.length ? `<details class="cal-disclosure cal-assign-more"${groups.other.some((profile) => profile.id === item.cover.profile_id) ? " open" : ""}><summary><span class="cal-disclosure-label">${esc(fill(t("calTabOtherProfiles"), { count: groups.other.length }))}</span><ha-icon icon="mdi:chevron-down" aria-hidden="true"></ha-icon></summary>
      <div class="cal-assign-rows">${groups.other.map(radio).join("")}</div></details>` : "";
    const content = profiles.length ? `${reserved ? `<p class="cal-note cal-assign-busy" role="status">${esc(t("wiz_T412"))}</p>` : ""}<p class="notice error cal-assign-error" role="alert" hidden></p><p class="cal-assign-intro">${esc(fill(t("wiz_T609"), { cover: item.name }))}</p>${personal ? `<p class="cal-note cal-assign-personal">${esc(t("calTabAssignKeepsPersonal"))}</p>` : ""}<fieldset class="wiz-radios cal-assign-list"><legend>${esc(t("calTabColProfile"))}</legend>${none || groups.head.length ? `<div class="cal-assign-rows">${none}${groups.head.map(radio).join("")}</div>` : ""}${others}</fieldset><div class="cal-assign-confirm" role="group" hidden></div>`
      : `<p class="muted">${esc(t("profileListEmpty"))}</p>`;
    // «Assegna» with the link, as «Cambia profilo»; «Annulla» outlined beside it (the primary first in the
    // phone's column, at the right in the medium dialog's row).
    const assignLabel = `<ha-icon icon="mdi:link-variant" aria-hidden="true"></ha-icon><span>${esc(t("wiz_T373"))}</span>`;
    const actions = profiles.length ? `<button type="button" class="primary" data-assign disabled>${assignLabel}</button><button type="button" class="cal-outlined cal-dialog-cancel" data-action="cal-tab-assign-cancel">${esc(t("wiz_T025"))}</button>`
      : `<button type="button" class="cal-outlined cal-dialog-cancel" data-action="cal-tab-assign-cancel">${esc(t("close"))}</button>`;
    body.innerHTML = `<div class="cal-page cal-assign-page" data-entity="${esc(item.cover.entity_id)}" data-closable="">
        <div class="cal-page-head"><button type="button" class="icon-button cal-page-close" data-action="cal-tab-assign-close" aria-label="${esc(t("close"))}" title="${esc(t("close"))}"><ha-icon icon="mdi:close" aria-hidden="true"></ha-icon></button>
          <div class="cal-page-head-text"><h2 id="cal-assign-name">${esc(fill(t(item.profile ? "wiz_T608" : "wiz_T607"), { cover: item.name }))}</h2>
          <p class="muted">${this._subline([this._area(item), gateways.length > 1 ? item.entry.title : ""], item.entity)}</p></div></div>
        <div class="cal-page-body cal-assign-body">${content}</div>
        <div class="cal-page-actions cal-assign-actions">${actions}</div></div>`;
    const page = body.querySelector(".cal-assign-page"), list = body.querySelector(".cal-assign-body");
    watchEdges(list, { top: page.querySelector(".cal-page-head"), bottom: page.querySelector(".cal-page-actions") })();
    if (menuOpen) this._render();
    // The page under it held still, as under the details.
    this._syncHold();
    // Nothing looks focused when it opens: the focus rests on the frame's own box.
    box.focus({ preventScroll: true });
    if (!profiles.length) return;
    const button = page.querySelector("[data-assign]");
    const chosen = () => list.querySelector('input[name="assign_profile"]:checked')?.value;
    // Assign stays off while the gateway is reserved, and after another change saved meanwhile until the data are read again.
    let refused = false;
    // A tap or click on the choice already made takes it back (no choice, Assign off), «Nessun
    // profilo» too: A-01's own behaviour, one shared function; the keyboard keeps the radios' (Space never unticks).
    untickOnSecondTap(list, 'input[name="assign_profile"]', () => list.onchange());
    // A profile whose slats contradict what this cover says of its own is asked first (Annulla / Assegna).
    const ask = list.querySelector(".cal-assign-confirm");
    // While the question is open its own Annulla / Assegna are the only pair: the footer's hide.
    const foot = page.querySelector(".cal-assign-actions");
    let confirmed = null;
    const unask = () => { ask.hidden = true; ask.replaceChildren(); confirmed = null; foot.hidden = false; };
    list.onchange = () => {
      unask();
      button.disabled = reserved || refused || !chosen() || chosen() === item.cover.profile_id;
      // Unlinking is said as such on the button, with the broken link.
      button.innerHTML = chosen() === NO_PROFILE ? `<ha-icon icon="mdi:link-variant-off" aria-hidden="true"></ha-icon><span>${esc(t("wiz_T406"))}</span>` : assignLabel;
      // A new choice made is something closing would lose: no scrim close, no handle, no drag.
      page.toggleAttribute("data-closable", !chosen() || chosen() === item.cover.profile_id);
    };
    if (again) {
      const kept = [...list.querySelectorAll('input[name="assign_profile"]')].find((input) => input.value === again.choice && !input.disabled);
      if (kept) kept.checked = true;
      list.onchange();
      const notice = list.querySelector(".cal-assign-error");
      notice.textContent = again.message;
      notice.hidden = false;
      notice.scrollIntoView?.({ block: "nearest" });
    }
    button.onclick = async () => {
      const choice = chosen();
      if (!choice || button.disabled || reserved || refused) return;
      const profileId = choice === NO_PROFILE ? null : choice;
      const conflict = profileId === null ? "" : text.slatsConflict(t, [item.cover], profiles.find((profile) => profile.id === profileId));
      if (conflict && confirmed !== profileId) {
        ask.innerHTML = `<p class="cal-travel-confirm">${esc(conflict)}</p><span class="cal-travel-buttons"><button type="button" class="cal-outlined" data-assign-ask="cancel">${esc(t("wiz_T025"))}</button><button type="button" class="primary" data-assign-ask="assign">${esc(t("wiz_T373"))}</button></span>`;
        ask.hidden = false;
        foot.hidden = true;
        button.disabled = true;
        ask.querySelector('[data-assign-ask="cancel"]').onclick = () => { unask(); button.disabled = false; button.focus({ preventScroll: true }); };
        ask.querySelector('[data-assign-ask="assign"]').onclick = () => { confirmed = profileId; ask.hidden = true; ask.replaceChildren(); foot.hidden = false; button.disabled = false; button.onclick(); };
        ask.scrollIntoView?.({ block: "nearest" });
        ask.querySelector("button").focus({ preventScroll: true });
        return;
      }
      button.disabled = true;
      const box = list.querySelector(".cal-assign-error");
      box.hidden = true;
      const current = () => generation === this._generation && this._assign === state;
      try {
        await hass.callWS({ type: "myhome/cover_profiles/write", entry_id: item.entry.entry_id, entity_id: item.cover.entity_id,
          revision: data.revision, action: "assign", profile_id: profileId });
        if (!current()) return;
        // An assignment is confirmed as an unlinking is: on top of the details it leads back to, and of the list.
        const done = profileId === null ? fill(t("wiz_T407"), { cover: item.name })
          : fill(t("wiz_T511"), { cover: item.name, profile: profiles.find((profile) => profile.id === profileId)?.name ?? "" });
        this._done = done;
        this._touched(key);
        // On the cover's own card, its name in the title above: «questa tapparella»; on the list, named.
        const here = profileId === null ? t("wiz_T407_here")
          : fill(t("wiz_T511_here"), { profile: profiles.find((profile) => profile.id === profileId)?.name ?? "" });
        if (state.fromPage) { this._assign = null; this._pageNotice = here; this._render(); this._pageFocus(); }
        else this._closePage();
        this.refresh();
      } catch (error) {
        if (!current()) return;
        // A refusal without a text of its own says the profile was not changed and to try again (here Assign does).
        const said = assignError(t, error);
        box.textContent = said === t("profileError") ? t("calTabAssignRetry") : said;
        box.hidden = false;
        box.scrollIntoView?.({ block: "nearest" });
        // Another change saved meanwhile: as A-35, the data are read again and the window drawn on them, the
        // choice kept, Assign on again (wiz_T627); until then Assign stays off.
        if (error?.code === "revision_conflict") {
          refused = true;
          state.retry = { entryId: item.entry.entry_id, revision: data.revision, choice, message: box.textContent };
          this._read(item.entry.entry_id, generation);
        } else {
          // Any other refusal: the choice stays and Assign is on again, a new tap tries the same write.
          button.disabled = reserved || !chosen() || chosen() === item.cover.profile_id;
        }
      }
    };
  }

  /** «Cambia profilo» refused by a `revision_conflict`, drawn again once the tab has read newer data. */
  _assignAgain(entryId) {
    const state = this._assign, retry = state?.retry, data = this._rows.get(entryId)?.data;
    if (!retry || retry.entryId !== entryId || !data || data.revision <= retry.revision || !this._pageDialog?.isConnected) return;
    state.retry = null;
    const item = this._item(state.key);
    if (item) this._assignProfile(item, { again: retry });
  }

  /** «Annulla» in «Cambia profilo»: back to the details it replaced, or to the list when it was opened from there. */
  _assignBack() {
    const state = this._assign;
    if (!state) return;
    if (!state.fromPage) { this._closePage(); return; }
    this._assign = null;
    this._render();
    this._pageFocus();
  }

  /** Back on the details from «Cambia profilo»: the focus on their «Cambia profilo», quietly after a tap. */
  _pageFocus() {
    const button = this._pageDialog?.querySelector('.cal-page [data-action="cal-tab-assign"]');
    if (!returnFocus(button)) this._pageDialog?.querySelector(".cal-flow-box")?.focus({ preventScroll: true });
  }
}
