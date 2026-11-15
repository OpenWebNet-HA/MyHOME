/**
 * What the calibration wizard talks to. Two implementations of one interface:
 *
 * - `LiveCalibrationGateway`: the existing session of Home Assistant (`geometry` and `guided` modes),
 *   driven through the same `CoverCalibration` engine as the profile dialog, so every message,
 *   heartbeat, reconnection, claim and Cancel is the one already tested there.
 * - `PreviewCalibrationGateway`: steps whose backend verbs do not exist yet (fine-tuning, path B,
 *   tape readings only). Local state only: it sends nothing to Home Assistant or the bus, and the
 *   wizard says so on screen.
 *
 * Interface: `view` (the session view or null), `status` ({busy, lost, expired, error, notice,
 * cancelPending, confirmTake}), `elapsed()` (seconds or null), `start(options)`, `resume(session)`,
 * `act(action, extra)`, `stop()`, `claim()`, `reconnect()`, `cancel()`, `save({target, name})`,
 * `supports(target)` and `close()`. `onChange(kind)` is called with "view", "tick" or "error".
 */
const asset = (name) => { const url = new URL(name, import.meta.url); url.search = new URL(import.meta.url).search; return url.href; };
const { CoverCalibration, calibrationClient, checkedCalibrationClient } = await import(asset("panel-cover-calibration.js"));

const RESUME = "myhome/cover_calibration/resume", ACTION = "myhome/cover_calibration/action";
/** L-58b: how long «Conferma presa di controllo» waits before it goes back to «Prendi il controllo». */
export const CONFIRM_TAKE_MS = 10000;
/** L-77: the verbs that start a movement (Stop is shown from the moment one is sent). */
const STARTS = ["next", "lift", "run", "open", "close", "repeat", "check", "restart"];
/** The step a reader is looking at: what the owner shows (its screen) and Home Assistant's step and phase. */
const armKey = (state) => JSON.stringify([state?.step ?? null, state?.phase ?? null, state?.screen?.code ?? null]);
const pause = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

/**
 * L-10: cancel the gateway's session from the list (the row's ⋮ menu), whoever guides it: this tab takes control
 * (a claim against the sequence it read) and cancels; Home Assistant then frees the cover once nothing moves. Rejects
 * with Home Assistant's error, or `calibration_owned` when the claim did not take (the session moved on meanwhile).
 */
export async function cancelSession(hass, session) {
  const client_id = calibrationClient() || await checkedCalibrationClient();
  let view = null;
  const unsubscribe = await hass.connection.subscribeMessage((state) => { view = state; },
    { type: RESUME, entry_id: session.entry_id, session_id: session.session_id, client_id, claim: true, sequence: session.sequence });
  try {
    for (let i = 0; i < 40 && !(view?.owner && !view.read_only); i++) await pause(50);
    if (!view?.owner || view.read_only) throw { code: "calibration_owned" };
    return await hass.callWS({ type: ACTION, entry_id: session.entry_id, session_id: session.session_id,
      ...(view.attachment ? { attachment: view.attachment } : {}), action: "cancel" });
  } finally {
    Promise.resolve(unsubscribe?.()).catch(() => {});
  }
}

/** The existing engine, with its DOM replaced by notifications to the wizard. */
class WizardSession extends CoverCalibration {
  constructor(listener) {
    super();
    this._listener = listener; this.error = null; this.notice = null;
    // A failed heartbeat is the connection, not a command: it is told apart by the extra object its call carries.
    this._beats = new WeakSet(); this.connection = null;
  }
  _render() { this._listener("view"); }
  _renderElapsed() { this._listener("tick"); }
  // The reading that was refused travels with the error, to name it beside the accepted range.
  _error(error, extra = {}) {
    if (this._beats.has(extra)) {
      // M3-1: a heartbeat refused as `calibration_expired` may only mean that its token died with the socket (Safari
      // suspends the page, Home Assistant reconnects on a new one). It is not an end until Home Assistant says so.
      if (this._expired) { this._expired = false; this.onBeatExpired?.(); }
      this.connection = "interrupted"; this._listener("view"); return;
    }
    this.error = { ...(error || {}), code: error?.code, reading: extra.reading_cm ?? null, sequence: this._sentSequence ?? null }; this._listener("error");
  }
  _accept(state) {
    const claiming = this._claiming;
    // L-78: «the measurement state changed» for a command the session has moved past: the new view says it all.
    if (this.error?.code === "calibration_step" && this.error.sequence != null && state?.sequence !== this.error.sequence) this.error = null;
    // L-26: the last view stays readable after the engine closes on «saved».
    this.lastView = state;
    super._accept(state);
    this.onAccept?.(state);
    // Attached again (Home Assistant reconnected, or the page came back): the interruption is over.
    if (state.attached && !this._lost) this.connection = null;
    // A claim against a sequence that moved on only reads: the wizard says so.
    if (claiming && state.read_only) { this.notice = "calClaimStale"; this._listener("view"); }
  }
  _perform(action, extra) {
    // L-78: the sequence a command was sent against, to recognise a refusal the session has since moved past.
    if (action !== "heartbeat") { this.error = null; this.notice = null; this._sentSequence = this._state?.sequence ?? null; return super._perform(action, extra); }
    const beat = {};
    this._beats.add(beat);
    return super._perform(action, beat);
  }
}

/**
 * L-4: a session the wizard starts says so (`origin: "wizard"` in its view), so a second tab opens it in the wizard's
 * reader view, guided «solo tempi» included; sessions started from Devices keep the maintainer's dialog. Only the
 * start message is marked: the maintainer's engine builds it, so the wizard hands it a Home Assistant that adds the field.
 */
const START = "myhome/cover_calibration/start";
export function wizardHass(hass) {
  const bound = (target, key) => { const value = Reflect.get(target, key); return typeof value === "function" ? value.bind(target) : value; };
  const connection = new Proxy(hass.connection, { get: (target, key) => key === "subscribeMessage"
    ? (callback, message, ...rest) => target.subscribeMessage(callback, message?.type === START ? { ...message, origin: "wizard" } : message, ...rest)
    : bound(target, key) });
  return new Proxy(hass, { get: (target, key) => key === "connection" ? connection : bound(target, key) });
}

export const SAVE_TARGETS = ["profile_new", "profile_update", "cover_only"];
// Path B only: the cover follows a profile with its own travel, nothing measured is written.
const PREVIEW_TARGETS = [...SAVE_TARGETS, "follow_profile"];

export class LiveCalibrationGateway {
  constructor({ onChange }) {
    this.preview = false;
    this._onChange = onChange;
    this._engine = new WizardSession((kind) => this._onChange?.(kind));
    this._engine.onBeatExpired = () => this._verify();
    // L-58b: an armed «Conferma presa di controllo» goes back when the owner shows another step.
    this._engine.onAccept = (state) => {
      if (this._armed && armKey(state) !== this._armed.key) this._disarm();
      // A change of owner (this tab taking control, or losing it): what this tab published before is no longer the
      // session's screen, so the next `show` is sent even if it is the same.
      const owns = !!state?.owner && !state.read_only;
      if (owns !== this._owned) { this._owned = owns; this._shown = null; }
    };
  }

  /** L-58b: «Conferma presa di controllo» armed: back to «Prendi il controllo» after CONFIRM_TAKE_MS, counting down. */
  _arm() {
    const engine = this._engine;
    this._disarm(false);
    engine._confirmTake = true;
    this._armed = { key: armKey(engine._state), until: Date.now() + CONFIRM_TAKE_MS };
    this._armTimer = setInterval(() => {
      if (!this._armed) return;
      if (Date.now() >= this._armed.until || engine._lost) this._disarm(); else this._onChange?.("view");
    }, 1000);
  }

  _disarm(notify = true) {
    clearInterval(this._armTimer); this._armTimer = null;
    const was = !!this._armed;
    this._armed = null;
    if (was && this._engine._confirmTake) { this._engine._confirmTake = false; if (notify) this._onChange?.("view"); }
  }

  /**
   * Is the session still there? A `resume` without claim reads it and sends nothing to the bus. Refused as expired: the
   * session really ended and the wizard says so; read: the same session is resumed under a new subscription; any other
   * failure (no connection yet): it stays "interrupted" until the next heartbeat or the page's return.
   */
  async _verify() {
    const engine = this._engine, state = engine._state;
    if (!state || this._verifying || !this._base) return;
    this._verifying = true;
    let alive = null;
    try {
      const unsubscribe = await this._base.hass.connection.subscribeMessage(() => {}, { type: "myhome/cover_calibration/resume",
        entry_id: state.entry_id, session_id: state.session_id, client_id: calibrationClient() });
      Promise.resolve(unsubscribe()).catch(() => {});
      alive = true;
    } catch (error) {
      alive = error?.code === "calibration_expired" ? false : null;
    } finally {
      this._verifying = false;
    }
    if (engine._state !== state) return;
    if (alive === false) { engine._expired = true; this._onChange?.("view"); }
    else if (alive) this.reconnect();
  }

  /** Ask Home Assistant at once (the page came back): a dead subscription shows up now, not at the next heartbeat. */
  ping() { if (this._engine._state && !this._engine._busy) this._engine._perform("heartbeat"); }

  get view() { return this._engine._state || null; }
  /** The last view received, kept after the session ended (the engine closes on «saved»). */
  get lastView() { return this._engine.lastView || null; }
  get status() {
    const engine = this._engine;
    return { busy: !!engine._busy, lost: !!engine._lost, expired: !!engine._expired, error: engine.error, notice: engine.notice, connection: engine.connection,
      confirmLeft: this._armed && engine._confirmTake ? Math.max(1, Math.ceil((this._armed.until - Date.now()) / 1000)) : null,
      cancelPending: !!engine._cancelPending, confirmTake: !!engine._confirmTake, locked: !!engine._state && engine._locked(),
      starting: !!this._starting && !!engine._busy };
  }

  elapsed() {
    const elapsed = this._engine._elapsed;
    return elapsed ? elapsed.value + (performance.now() - elapsed.since) / 1000 : null;
  }

  /** `mode` is "geometry" (paths A and C, times and rolls) or "guided" (path C, times only). */
  start({ host, hass, t, entity, revision, mode, slats, onSaved, onCancel }) {
    this._base = { host, hass: wizardHass(hass), t, entity, revision, onSaved, onCancel };
    return this._engine.open({ ...this._base, mode, ...(mode === "geometry" ? { slats } : {}) });
  }

  /** Reads the session: never a claim, never a command to the bus. */
  resume({ host, hass, t, entity, revision, session, onSaved, onCancel }) {
    this._base = { host, hass, t, entity, revision, onSaved, onCancel };
    return this._engine.open({ ...this._base, resume: session });
  }

  reconnect() { return this._engine.open({ ...this._base, claim: false, resume: this._engine._state }); }

  /** While the owner is present the first call only asks for confirmation, as in the profile dialog. */
  claim() {
    const engine = this._engine;
    if (engine._state?.owner_present === true && !engine._confirmTake) {
      this._arm();
      this._onChange?.("view");
      return Promise.resolve();
    }
    this._disarm(false);
    return engine.open({ ...this._base, claim: true, resume: engine._state });
  }

  act(action, extra = {}) {
    // L-77: a start just sent shows Stop at once, before the session's answer (cleared with the answer or the refusal).
    const starting = STARTS.includes(action);
    if (starting) this._starting = true;
    const sent = this._engine._perform(action, extra);
    if (starting) this._onChange?.("view");
    return Promise.resolve(sent).finally(() => { if (starting && this._starting) { this._starting = false; this._onChange?.("view"); } });
  }

  /**
   * L-25: publish this tab's screen to the session's readers (Home Assistant's `show`): outside the engine's queue of
   * verbs (no busy state, nothing held back), only while this tab owns the session, and never twice the same.
   */
  show(screen) {
    const engine = this._engine, state = engine._state, text = JSON.stringify(screen);
    if (!state || state.read_only || !state.owner || !state.recoverable || text === this._shown) return Promise.resolve();
    this._shown = text;
    return engine._context.hass.callWS({ type: "myhome/cover_calibration/action", entry_id: state.entry_id, session_id: state.session_id,
      ...(state.attachment ? { attachment: state.attachment } : {}), action: "show", screen }).catch(() => { this._shown = null; });
  }
  /** Forget a refusal once the session has moved to another phase: its values belonged to that phase. */
  clearError() { this._engine.error = null; }
  stop() { return this._engine._perform("stop"); }
  cancel() { return this._engine._cancel(this._engine._generation); }

  /**
   * L-10: cancel the session for good, also from a tab that only reads it (a Stop sent from another tab or device
   * leaves the session interrupted, owned by a tab that may be gone): control is taken first, without the question of
   * Take control, then Cancel is sent. A claim that does not take leaves the error `calibration_owned`.
   */
  async abort() {
    const engine = this._engine, state = engine._state;
    if (state?.read_only && state.recoverable) {
      engine._confirmTake = false;
      await engine.open({ ...this._base, claim: true, resume: state });
      for (let i = 0; i < 40 && engine._state?.read_only; i++) await pause(50);
      if (!engine._state || engine._state.read_only) {
        engine.error = { code: "calibration_owned" };
        this._onChange?.("error");
        return;
      }
    }
    return this.cancel();
  }

  supports(target) {
    const modes = this.view?.save_modes || [];
    return target === "profile_new" ? modes.includes("new") : target === "cover_only" ? modes.includes("cover") : false;
  }

  /** The unified Save of the screens, mapped on the existing `save_mode`s (L5a will accept `target`). */
  save({ target, name }) {
    if (!this.supports(target)) return Promise.resolve();
    return this._engine._perform("save", target === "profile_new" ? { save_mode: "new", name } : { save_mode: "cover" });
  }

  close() { this._disarm(false); return this._engine.dispose(); }
}

/** Half-up to the whole centimetre: 4.5 cm is 5 cm (the threshold of the check is on the integer). */
export const roundHalfUp = (value) => Math.floor(Math.abs(value) + 0.5);
export const CHECK_THRESHOLD_CM = 4;

/**
 * A rough height model for the preview only: the slat phase lifts nothing, then the edge moves
 * in proportion to time. The real fit belongs to the backend (L5c); these numbers only fill the
 * screens so they can be tried.
 */
export function previewHeight(values, direction, fraction) {
  const travel = values.travel_cm || 0;
  if (direction === "up") {
    const total = values.opening_time || 1, slat = values.slat_time_s || 0;
    return travel * Math.min(1, Math.max(0, (fraction * total - slat) / Math.max(total - slat, 0.1)));
  }
  return travel * Math.min(1, Math.max(0, 1 - fraction));
}

const READINGS = [
  { step: "quarter_open", direction: "up", fraction: 0.25, back: "down" },
  { step: "three_quarter_open", direction: "up", fraction: 0.75, back: "down" },
  { step: "quarter_close", direction: "down", fraction: 0.25, back: "up" },
  { step: "three_quarter_close", direction: "down", fraction: 0.75, back: "up" },
];
const CHECK = { step: "check", direction: "down", fraction: 0.4, back: "up" };

/**
 * Simulated sessions with the shape the backend will publish (L5b/L5c): `phase`, `step`,
 * `stages`, `expected_cm`, `readings`, `check`, `values`. Plans: "refine" (fine-tuning after a
 * calibration, path A and C), "rolls" (path C, tape readings only) and "profile" (path B).
 */
export class PreviewCalibrationGateway {
  constructor({ onChange, plan, values, stageMs = 1500 }) {
    this.preview = true;
    this._onChange = onChange;
    this._plan = plan;
    this._stageMs = stageMs;
    this._timers = new Set();
    this._view = null;
    this._seed = { ...values };
    this._error = null;
  }

  get view() { return this._view; }
  get status() { return { busy: false, lost: false, expired: false, error: this._error, notice: null, cancelPending: false, confirmTake: false, locked: false }; }
  elapsed() { return null; }
  supports(target) { return PREVIEW_TARGETS.includes(target); }
  reconnect() { return Promise.resolve(); }
  claim() { return Promise.resolve(); }
  clearError() { this._error = null; }

  start() {
    const base = { preview: true, path: this._plan, owner: true, read_only: false, recoverable: false, sequence: 0,
      values: { ...this._seed }, travel_cm: this._seed.travel_cm ?? null, readings: {}, check: null, reason: null };
    this._readings = this._plan === "profile" ? [] : READINGS;
    this._view = base;
    // Path B with the cover's travel already saved: no travel measurement, only the check at half travel.
    if (this._plan === "profile" && this._seed.skip_travel && this._seed.travel_cm > 0) this._position({ step: "check", stages: this._stages(CHECK) }, "check");
    else if (this._plan === "profile") this._position({ step: "open_full", stages: [{ key: "open_full", seconds: this._seed.opening_time }] }, "travel");
    else this._next(0);
    return Promise.resolve();
  }

  resume() { return this.start(); }

  _emit(patch) {
    this._view = { ...this._view, ...patch, sequence: this._view.sequence + 1 };
    this._onChange?.("view");
  }

  _clear() { for (const timer of this._timers) clearTimeout(timer); this._timers.clear(); }

  _later(callback) {
    const timer = setTimeout(() => { this._timers.delete(timer); callback(); }, this._stageMs);
    this._timers.add(timer);
  }

  /** A positioning in one or two stages, then the reading of `step`. */
  _position({ step, stages }, reading) {
    this._clear();
    this._pendingReading = reading;
    this._emit({ phase: "positioning", step, stages, stage_index: 0, expected_cm: null, check_result: null });
    const advance = (index) => {
      if (index < stages.length - 1) { this._emit({ stage_index: index + 1 }); this._later(() => advance(index + 1)); return; }
      this._emit({ phase: "reading", step: reading, stages: [], stage_index: null, expected_cm: this._expected(reading) });
    };
    this._later(() => advance(0));
  }

  _reading(step) { return [...READINGS, CHECK].find((item) => item.step === step); }

  _expected(step) {
    const values = this._view.values, travel = this._view.travel_cm;
    if (step === "travel") return null;
    if (this._plan === "profile" && step === "check") return travel / 2;
    const reading = this._reading(step);
    return reading ? previewHeight({ ...values, travel_cm: travel }, reading.direction, reading.fraction) : null;
  }

  _stages(reading) {
    const values = this._view.values;
    if (this._plan === "profile") return [{ key: "bottom", seconds: values.closing_time }, { key: "half_travel", seconds: values.opening_time / 2 }];
    const back = reading.back === "down" ? { key: "back_down", seconds: values.closing_time } : { key: "back_up", seconds: values.opening_time };
    const time = reading.direction === "up" ? values.opening_time : values.closing_time;
    return [back, { key: reading.step, seconds: time * reading.fraction }];
  }

  _next(index) {
    const reading = index < this._readings.length ? this._readings[index] : CHECK;
    this._index = index;
    this._position({ step: reading.step, stages: this._stages(reading) }, reading.step);
  }

  _checkResult(measured) {
    const expected = this._view.expected_cm;
    const gap = roundHalfUp(measured - expected);
    const seconds = this._plan === "profile" ? null : this._view.values.closing_time * CHECK.fraction;
    return { target_cm: expected, predicted_cm: expected, measured_cm: measured, gap_cm: gap, threshold_cm: CHECK_THRESHOLD_CM,
      check_seconds: seconds, passed: gap <= CHECK_THRESHOLD_CM };
  }

  act(action, extra = {}) {
    const view = this._view;
    this._error = null;
    if (!view) return Promise.resolve();
    // Y-b: nothing moves in a preview, so its positioning can be passed at once («Continua»).
    if (action === "skip" && view.phase === "positioning") {
      this._clear();
      this._emit({ phase: "reading", step: this._pendingReading, stages: [], stage_index: null, expected_cm: this._expected(this._pendingReading) });
      return Promise.resolve();
    }
    if (action === "reading" && view.phase === "reading") {
      const value = Number(extra.reading_cm);
      const limit = view.step === "travel" ? 500 : (view.travel_cm || 500) + 50;
      if (!Number.isFinite(value) || value < (view.step === "travel" ? 20 : 0) || value > limit) {
        this._error = { code: "invalid_reading" };
        this._onChange?.("error");
        return Promise.resolve();
      }
      if (view.step === "travel") {
        this._emit({ travel_cm: value, readings: { ...view.readings, travel: value } });
        this._position({ step: "check", stages: this._stages(CHECK) }, "check");
      } else if (view.step === "check") {
        this._emit({ phase: "check_result", readings: { ...view.readings, check: value }, check: this._checkResult(value) });
      } else {
        this._emit({ readings: { ...view.readings, [view.step]: value } });
        this._next(this._index + 1);
      }
    } else if (action === "repeat" && view.phase === "reading") {
      if (view.step === "travel") this._position({ step: "open_full", stages: [{ key: "open_full", seconds: view.values.opening_time }] }, "travel");
      else if (view.step === "check") this._position({ step: "check", stages: this._stages(CHECK) }, "check");
      else this._next(this._index);
    } else if (action === "repeat_check" && view.phase === "check_result") {
      this._position({ step: "check", stages: this._stages(CHECK) }, "check");
    } else if (action === "redo_readings" && view.phase === "check_result" && this._plan !== "profile") {
      this._emit({ readings: {}, check: null });
      this._next(0);
    } else if (["accept", "continue_anyway"].includes(action) && view.phase === "check_result") {
      this._emit({ phase: "review" });
    } else if (action === "stop") {
      this._clear();
      if (view.phase === "positioning") this._emit({ phase: "interrupted", reason: "stopped" });
    } else if (action === "cancel") {
      this._clear();
      this._emit({ phase: "cancelled", reason: "cancelled" });
    }
    return Promise.resolve();
  }

  stop() { return this.act("stop"); }
  cancel() { return this.act("cancel"); }
  abort() { return this.cancel(); }
  show() { return Promise.resolve(); }

  save({ target, name }) {
    if (this._view?.phase !== "review" || !this.supports(target)) return Promise.resolve();
    this._emit({ phase: "saved", saved: { target, name: name ?? null } });
    return Promise.resolve();
  }

  close() { this._clear(); this._view = null; return Promise.resolve(); }
}
