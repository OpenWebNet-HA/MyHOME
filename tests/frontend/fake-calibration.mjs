/**
 * A stand-in for Home Assistant in the panel tests: inventory, profile overview and read, and a
 * calibration session whose `geometry` and `guided` transitions follow the backend modules
 * (`cover_calibration_geometry.py`, `cover_calibration.py`) closely enough to drive every screen.
 * Bus events are explicit calls (`started()`, `stopped()`, `autoStop()`), so tests decide when the
 * cover "moves". Every message the panel sends is recorded in `calls` / `subscriptions`.
 */
export function coverInventory(covers) {
  return {
    version: "2.0.0b13", panel_version: "0.41.0",
    gateways: [{ entry_id: "one", title: "Home", mac: "00:03:50:00:00:01", model: "F454", host: "192.0.2.1", state: "loaded", connected: true, monitor_available: true }],
    devices: [],
    entities: covers.map(([entity_id, name, area_id]) => ({ entity_id, entry_id: "one", domain: "cover", who: "2", name: null, original_name: name,
      device_id: null, area_id, unique_id: `uid-${entity_id}`, address: { raw: "91", a: "9", pl: "1", interface: null } })),
    areas: [{ id: "living", name: "Living room" }, { id: "kitchen", name: "Kitchen" }],
  };
}

const evidence = (source, recorded_at = null, extra = {}) => ({ source, recorded_at, origin_entity_id: null, origin_name: null, inherited: false, ...extra });
const value = (number, provenance, origin = "override") => ({ value: number, origin, provenance, scaled: false });

/** Every cover carries Home Assistant's derived `precision` (none, basic, refined, verified). */
export function sampleOverview() {
  const measured = evidence("guided", "2026-09-29T10:00:00+00:00");
  return {
    entry_id: "one", revision: 7, capabilities: { profile_management: true, profile_assignment: true },
    profiles: [{ id: "tall", name: "tall_two_leaf", opening_time: 22.4, closing_time: 21.9, reference_travel_cm: 198,
      geometry: { slat_time_s: 4.4, opening_roll: 1.83, closing_roll: 1.91 }, assigned_to: ["cover.kitchen_two", "cover.living_three"],
      provenance: { opening: evidence("guided", "2026-09-29T10:00:00+00:00", { origin_entity_id: "cover.living_one", origin_name: "cover.living_one" }), closing: measured } }],
    covers: [
      { entity_id: "cover.living_one", name: "Living room shutter 1", profile_id: null, travel_cm: 195, available: true, advanced: false, overrides: {},
        effective: { opening: value(22.0, evidence("unknown"), "native_fallback"), closing: value(21.7, evidence("unknown"), "native_fallback") }, precision: "none" },
      { entity_id: "cover.kitchen_two", name: "Kitchen shutter 2", profile_id: "tall", travel_cm: 110, available: true, advanced: false, overrides: {},
        effective: { opening: value(12.3, measured, "profile"), closing: value(12.1, measured, "profile"), slat_time_s: value(2.4, measured, "profile"),
          opening_roll: value(1.83, measured, "profile"), closing_roll: value(1.91, measured, "profile") }, precision: "refined" },
      { entity_id: "cover.living_three", name: "Living room shutter 3", profile_id: "tall", travel_cm: 196, available: true, advanced: false, overrides: {},
        effective: { opening: value(22.2, measured, "profile"), closing: value(21.8, measured, "profile") }, precision: "basic" },
    ],
  };
}

export function createFakeBackend({ language = "en", inventory = coverInventory([["cover.living_one", "Living room shutter 1", "living"],
  ["cover.kitchen_two", "Kitchen shutter 2", "kitchen"], ["cover.living_three", "Living room shutter 3", "living"]]), overview = sampleOverview(),
  session: initial = null, owner = true, ownerPresent = true, targets = [], maxBatch = 4, persist = false, slowHome = false } = {}) {
  const calls = [], subscriptions = [];
  let session = initial, listener = null, clock = 0, profilesListener = null;
  const backend = {
    calls, subscriptions, overview,
    get session() { return session; },
    tick(seconds) { clock += seconds; },
    view(extra = {}) {
      const { _after_stop, _after_position, _after_reclose, _started, ...public_ } = session;
      void _after_stop; void _after_position; void _after_reclose;
      return { ...public_, elapsed: _started == null ? null : Math.round((clock - _started) * 100) / 100,
        owner, read_only: !owner, attachment: "attachment-1", attached: true, owner_present: ownerPresent, recoverable: !["saved", "cancelled"].includes(session.phase), ...extra };
    },
    emit() { if (session.mode === "geometry") refresh(); session.sequence += 1; listener?.(backend.view()); },
    /** The bus confirms the movement asked by `next`, `open` or `close` (`open` in `confirm_lift`: the lift-off run). */
    started() {
      session.phase = { starting_open: "opening", starting_lift: "lifting" }[session.phase] ?? "closing";
      session._started = clock;
      backend.emit();
    },
    /** The actuator confirms Stop after a press, an endpoint or a timed half run. */
    stopped() {
      if (session.phase !== "geometry_wait_stop") throw new Error(`stopped() in ${session.phase}`);
      session._started = null;
      session.phase = session._after_stop;
      if (session.phase === "briefing") session.step = ["home", "reset", "top"].includes(session.step) ? session._after_position : "half_open";
      backend.emit();
    },
    /** The close to the rest ended by itself: the pause, then the rise for the saved slat time. */
    restReached() { session._started = null; session.phase = "settling"; session.step = "lift"; backend.emit(); },
    /** …and the rise by itself starts (the bus reports it). */
    riseStarted() { session.phase = "opening"; session._started = clock; backend.emit(); },
    /** The scheduled Stop of an intermediate run. */
    autoStop() {
      session.samples[session.step] = clock - session._started;
      session.phase = "geometry_wait_stop"; session._after_stop = "reading";
      backend.emit();
    },
    /** Presence of the owning tab as Home Assistant reports it (`owner_present`). */
    setPresence(present) { ownerPresent = present; },
    /** Another device claims the session: this client only reads it from now on (a claim is a transition). */
    takenElsewhere() { owner = false; session.sequence += 1; listener?.(backend.view()); },
    /** Another tab presses Stop: the session ends and every subscriber gets the view. */
    stopFromElsewhere() { session.phase = "interrupted"; session.reason = "stopped"; session._started = null; forgetTap(); backend.emit(); },
    interrupt(reason) { session.phase = "interrupted"; session.reason = reason; session._started = null; forgetTap(); backend.emit(); },
    /**
     * The session's own close after the lift-off run ends (the bus's stop or the closing time
     * plus its margin): the review after a lift-off taken, `confirm_lift` after one refused or a repeat in the run.
     */
    reclosed() {
      if (session.phase !== "reclosing") throw new Error(`reclosed() in ${session.phase}`);
      Object.assign(session, { phase: session._after_reclose, reclose_seconds: null, back_stage: null, _started: null });
      backend.emit();
    },
    /** The bus reports the start of a run back (`reopening`, `reclosing`): `back_stage` running. */
    backRunning() { session.back_stage = "running"; session._started = clock; backend.emit(); },
    /** The close back before the ascent is over: `confirm_closed`. */
    rehomed() {
      if (session.phase !== "rehoming") throw new Error(`rehomed() in ${session.phase}`);
      Object.assign(session, { phase: "confirm_closed", rehome_seconds: null, back_stage: null, _started: null });
      backend.emit();
    },
    /** The open back before a repeated descent is over: `confirm_open`. */
    reopened() {
      if (session.phase !== "reopening") throw new Error(`reopened() in ${session.phase}`);
      Object.assign(session, { phase: "confirm_open", reopen_seconds: null, back_stage: null, _started: null });
      backend.emit();
    },
    /** The bus reports the motor stopped by itself during a run (`motor_stopped`, `stopped_elapsed`). */
    busStop() {
      session.motor_stopped = true; session.stopped_elapsed = Math.round((clock - session._started) * 100) / 100;
      // In the basic calibration the actuator's own stop confirms the full run, timed to it (not at a relay's timer,
      // 59-65 / 119-125 s); one within 2 s of the start means it was already there.
      const seconds = session.stopped_elapsed, timer = (seconds >= 59 && seconds <= 65) || (seconds >= 119 && seconds <= 125);
      if (session.mode === "guided" && ["opening", "closing"].includes(session.phase) && seconds < 2) {
        Object.assign(session, { phase: session.phase === "opening" ? "confirm_closed" : "confirm_open", reason: "already_at_end", _started: null });
        delete session.motor_stopped; delete session.stopped_elapsed;
      } else if (session.mode === "guided" && ["opening", "closing"].includes(session.phase) && !timer) {
        session.values[`${session.phase}_time`] = seconds;
        session.ended_by = { ...session.ended_by, [session.phase]: "bus_stop" };
        delete session.motor_stopped; delete session.stopped_elapsed;
        session._started = null;
        session.phase = session.phase === "opening" ? "confirm_open" : session.slats ? "confirm_lift" : "review";
      } else if (session.mode === "geometry" && ["opening", "closing"].includes(session.phase) && seconds >= 1.5
        && ["home", "reset", "opening", "closing", "top"].includes(session.step) && !(session.auto_lift && session.step === "home" && session._after_position === "lift")
        && (!timer || session.keep_times || !["opening", "closing"].includes(session.step))) {
        // The same rule in the runs to an end stop of the tape's session: the stop is
        // the arrival (a run that times nothing at any stop; a timed run of path A unless at a relay timer).
        if (["opening", "closing"].includes(session.step) && !session.keep_times) session.values[`${session.step}_time`] = seconds;
        delete session.motor_stopped; delete session.stopped_elapsed;
        session._started = null;
        session.phase = session.step === "opening" ? "reading" : "briefing";
        if (session.phase === "briefing") session.step = ["home", "reset", "top"].includes(session.step) ? session._after_position : "half_open";
      }
      backend.emit();
    },
  };

  // `keep_times` starts from the cover's times in use; its runs to the end stops time nothing.
  const keptTimes = (message) => {
    const cover = overview.covers.find((item) => item.entity_id === message.entity_id);
    return message.keep_times ? { opening_time: cover?.effective?.opening?.value, closing_time: cover?.effective?.closing?.value } : {};
  };
  // On the saved times, from where the cover stands: with a slat time saved the lift-off runs by itself
  // (`auto_lift`: close, rise for that time, `lift_pair` 1 then 2); without slats straight up first.
  const savedSlat = (message) => overview.covers.find((item) => item.entity_id === message.entity_id)?.effective?.slat_time_s?.value;
  // `shared` only on the source of the profile followed (the cover its times were measured on).
  const saveModes = (message) => {
    const cover = overview.covers.find((item) => item.entity_id === message.entity_id);
    const profile = overview.profiles.find((item) => item.id === cover?.profile_id);
    return profile && profile.provenance?.opening?.origin_entity_id === message.entity_id ? ["new", "shared"] : ["new"];
  };
  const startGeometry = (message) => ({
    entry_id: message.entry_id, entity_id: message.entity_id, session_id: "session-1", sequence: 1, revision: message.revision,
    mode: "geometry", phase: "briefing", step: message.keep_times && message.slats === false ? "opening" : "home", reason: null, values: keptTimes(message), samples: {}, readings: {}, geometry: {}, travel_cm: null,
    keep_times: !!message.keep_times,
    auto_lift: !!message.keep_times && message.slats !== false && savedSlat(message) > 0,
    saved_slat_s: !!message.keep_times && message.slats !== false && savedSlat(message) > 0 ? savedSlat(message) : null, planned_seconds: null,
    stop_requested: false, slats: message.slats !== false, lift_attempts: 1, still_resting: false, gap_warning: false, touching_cm: 1, max_gap_cm: 20,
    gap_warn_cm: 10, lift_repeat: true, saved_travel_cm: 195, expected_cm: null, can_repeat: false, reading_kind: "home",
    // The tape's session saves a new profile, or into the profile followed (`shared`); never `cover`.
    save_modes: saveModes(message),
    _after_stop: "briefing", _after_position: message.slats === false ? "opening" : "lift", _started: null });
  // The basic calibration is the guided session with `slats`; every view carries `slats`, `samples` ({} or {lift}),
  // `lift_taken` and `reclose_seconds`.
  const startGuided = (message) => {
    if (message.slats !== undefined && (message.direction || message.mode === "automatic")) throw Object.assign(new Error("slats"), { code: "invalid_profile" });
    // `home` closes the cover first (`rehoming`); here at once, unless the test holds it (`slowHome`).
    return { entry_id: message.entry_id, entity_id: message.entity_id, session_id: "session-1", sequence: 1, revision: message.revision, mode: "guided",
      // Always a profile: a new one, or the one the cover follows (`shared`); never `cover`.
      phase: message.home && slowHome ? "rehoming" : "confirm_closed", back_stage: message.home && slowHome ? "stopping" : null,
      rehome_seconds: message.home && slowHome ? 27.5 : null, home: !!message.home, reason: null, values: {}, ended_by: {}, stop_requested: false, slats: message.slats === true,
      save_modes: saveModes(message),
      samples: {}, lift_taken: false, reclose_seconds: null, reopen_seconds: null, back_stage: null, _started: null };
  };
  /** An interruption of a guided session drops its values and its tap. */
  const forgetTap = () => { if (session?.mode === "guided") { session.samples = {}; session.lift_taken = false; } };

  const repeatable = () => session.phase === "reading" || session.phase === "review" || (session.phase === "briefing" && session.step === "half_open" && !session.keep_times) || session.gap_warning;
  const refresh = () => {
    session.lift_pair = session.auto_lift ? (session.step === "home" && session._after_position === "lift" ? 1 : session.step === "lift" ? 2 : null) : null;
    session.planned_seconds = !["starting_open", "starting_close", "opening", "closing", "settling"].includes(session.phase) ? null
      : session.auto_lift && session.step === "lift" ? session.saved_slat_s
        : session.lift_pair === 1 ? session.values.closing_time + Math.max(4, session.values.closing_time * 0.25)
          : session.step === "half_close" && session.keep_times ? (session.values.closing_time - (session.samples.lift || 0)) / 2 : null;
    session.can_repeat = repeatable();
    session.reading_kind = session.step === "opening" ? "travel" : session.step;
    session.expected_cm = session.travel_cm && session.step?.startsWith("half_") ? session.travel_cm / 2 : null;
    // As cover_calibration_geometry.py publishes them: the physical heights accepted at this stop (up to the
    // travel plus 3 cm), and the band near the model (the heights of rolls 1-5 widened by 10 % of the travel).
    const half = session.phase === "reading" && session.step?.startsWith("half_") && session.travel_cm;
    session.reading_range = half ? { min_cm: 0.1, max_cm: session.travel_cm + 3 } : null;
    session.reading_band = half ? { min_cm: session.travel_cm * 0.2712345, max_cm: session.travel_cm * 0.6512345, expected_cm: session.travel_cm * 0.4612345,
      model_min_cm: session.travel_cm * 0.3712345, model_max_cm: session.travel_cm * 0.5512345 } : null;
    session.reading_discrepancy ??= {};
  };

  function geometryAction(message) {
    const { action } = message;
    if (action === "next" && session.phase === "briefing") {
      session.gap_warning = session.still_resting = false;
      session.phase = session.step === "check" ? (session.check?.direction === "opening" ? "starting_open" : "starting_close")
        : ["home", "reset", "closing", "half_close"].includes(session.step) ? "starting_close" : "starting_open";
    } else if (action === "lift" && session.step === "lift" && session.phase === "opening" && !session.auto_lift) {
      session.samples.lift = clock - session._started;
      session.phase = "geometry_wait_stop"; session._after_stop = "reading";
    } else if (action === "endpoint" && ["opening", "closing"].includes(session.phase) && ["home", "reset", "opening", "closing", "top"].includes(session.step)
      && !(session.auto_lift && session.step === "home" && session._after_position === "lift")) {
      // As v2-endpoint: confirmed after the bus reported the stop, the stop frame's time is the run's.
      if (["opening", "closing"].includes(session.step) && !session.keep_times) session.values[`${session.step}_time`] = session.motor_stopped ? session.stopped_elapsed : clock - session._started;
      delete session.motor_stopped; delete session.stopped_elapsed;
      session._after_stop = session.step === "opening" ? "reading" : "briefing";
      session.phase = "geometry_wait_stop";
    } else if (action === "reading" && session.phase === "reading") {
      const number = message.reading_cm;
      if (session.step === "lift") {
        if (number > session.max_gap_cm) throw Object.assign(new Error("gap"), { code: "invalid_gap" });
        session.still_resting = number < session.touching_cm && !session.auto_lift;
        if (session.still_resting) { delete session.samples.lift; session.lift_attempts += 1; } else session.readings.gap = number;
        session.gap_warning = number >= session.gap_warn_cm;
        session.step = "reset"; session._after_position = session.still_resting ? "lift" : "opening";
        // Nothing to time from the rest: straight up after the lift-off reading.
        if (session.keep_times && !session.still_resting) session.step = "opening";
      } else if (session.step === "check") {
        const expected = session.check.target_cm, deviation = Math.round(number - expected);
        Object.assign(session.check, { check_seconds: session.check.planned_seconds, expected_cm: expected, measured_cm: number, deviation_cm: deviation, passed: Math.abs(deviation) <= 4 });
        session.phase = "review"; session.step = "half_close"; refresh(); return;
      } else if (session.step === "opening") {
        // On the saved times, from the top, half the time down first.
        session.travel_cm = number; session.step = session.keep_times ? "half_close" : "closing";
      } else if (session.reading_range && number > session.reading_range.max_cm) {
        throw Object.assign(new Error("over"), { code: "reading_over_travel" });
      } else if ((() => {
        const band = session.reading_band;
        if (band && (number < band.min_cm || number > band.max_cm)) session.reading_discrepancy[session.step] = { reading_cm: number, expected_cm: band.expected_cm, min_cm: band.min_cm, max_cm: band.max_cm };
        return false;
      })()) {
        // (never: the band only reports)
      } else if (session.step === "half_open") {
        session.geometry = { slat_time_s: session.slats ? 4.4 : 0, opening_roll: 1.83 };
        session.readings.half_open = number; session.step = "top"; session._after_position = "half_close";
        if (session.keep_times && session.readings.half_close != null) { session.geometry.closing_roll = 1.91; session.step = "half_open"; session.phase = "review"; refresh(); return; }
      } else if (session.keep_times && session.geometry.opening_roll == null) {
        session.readings.half_close = number; session.step = "closing";
      } else {
        session.geometry.closing_roll = 1.91; session.readings.half_close = number; session.phase = "review"; refresh(); return;
      }
      session.phase = "briefing";
    } else if (action === "check" && session.phase === "review" && backend.realisticCheck) {
      // As the backend: to the end stop first, then (on `next`) the timed run, then the reading.
      session.check = { direction: message.direction, target_cm: message.target_cm, check_seconds: null, planned_seconds: 12.4,
        expected_cm: null, measured_cm: null, deviation_cm: null, passed: null };
      session.step = message.direction === "opening" ? "home" : "top"; session._after_position = "check"; session.phase = "briefing";
    } else if (action === "check" && session.phase === "review") {
      // The whole check (end stop, timed run, reading) at once: a passed check, 1 cm off.
      session.check = { direction: message.direction, target_cm: message.target_cm, predicted_cm: message.target_cm, measured_cm: message.target_cm + 1,
        expected_cm: message.target_cm, deviation_cm: 1, gap_cm: 1, passed: true, check_seconds: 10.5 };
    } else if (action === "repeat" && repeatable()) {
      const target = session.gap_warning ? "lift" : session.phase === "briefing" && session.step === "half_open" ? "closing" : session.step;
      session.step = ["half_close", "closing"].includes(target) ? "top" : session.slats ? "reset" : "home";
      session._after_position = target;
      if (target === "lift") { session.lift_attempts += 1; delete session.samples.lift; delete session.readings.gap; }
      session.still_resting = session.gap_warning = false;
      session.phase = "briefing";
    } else throw Object.assign(new Error("step"), { code: "calibration_step" });
  }

  function guidedAction(message) {
    const { action } = message;
    const step = () => Object.assign(new Error("step"), { code: "calibration_step" });
    // With `slats` the measured descent leads to `confirm_lift`; `open` there starts
    // the lift-off run, `lift` in it (before any stop of the bus) is its Stop: the time since the motor started, then
    // Home Assistant closes the cover again by itself (`reclosing`, ended here by `reclosed()`). A lift-off not shorter
    // than both full runs is refused: nothing kept, the cover closes again all the same, then `confirm_lift` with the
    // reason. `repeat` in `review` drops the lift-off (nothing moves); in `lifting` it stops and closes again first.
    // An accepted lift-off is the Stop and review follows at once, the cover where
    // it stopped; a refused one (not shorter than both runs) closes the cover back (`reclosing`, ended by `reclosed()`)
    // to `confirm_lift`. `repeat` (in `lifting` or review) closes it back, then waits for ① (`reclosed()` leads to
    // `confirm_lift`); the close back lasts the rise × 1.5 + 2 s, at most a full close.
    // `repeat` with `direction: "closing"` opens the cover back (`reopening`, ended by `reopened()`)
    // to `confirm_open`, the opening time kept.
    const back = (risen) => { const closing = session.values.closing_time;
      return Math.round(Math.min(closing + Math.max(4, closing * 0.25), risen * 1.5 + 2) * 100) / 100; };
    if (action === "lift") {
      if (!session.slats || session.phase !== "lifting" || session.motor_stopped) throw step();
      const lift = Math.round((clock - session._started) * 100) / 100;
      const closing = session.values.closing_time;
      if (!(lift < session.values.opening_time && lift < closing)) {
        Object.assign(session, { phase: "reclosing", back_stage: "stopping", _started: null, reclose_seconds: back(lift), reason: "lift_not_shorter", _after_reclose: "confirm_lift" });
        session.sequence += 1;
        listener?.(backend.view());
        throw Object.assign(new Error("lift"), { code: "lift_not_shorter" });
      }
      Object.assign(session, { phase: "review", _started: null, samples: { lift }, lift_taken: true, reclose_seconds: null });
      return;
    }
    if (action === "repeat" && message.direction === "opening") {
      const refused = session.phase === "confirm_closed" && session.reason === "already_at_end";
      if (session.direction === "closing" || (!refused && (session.phase !== "confirm_open" || session.values.opening_time == null))) throw step();
      delete session.values.opening_time;
      Object.assign(session, { phase: "rehoming", back_stage: "stopping", reason: null, _started: null, rehome_seconds: 27.5 });
      return;
    }
    if (action === "repeat" && message.direction === "closing") {
      const refused = session.phase === "confirm_open" && session.reason === "already_at_end";
      if (session.direction === "opening" || (!refused && !["confirm_lift", "review"].includes(session.phase))) throw step();
      delete session.values.closing_time;
      const opening = session.values.opening_time;
      Object.assign(session, { phase: "reopening", back_stage: "stopping", reason: null, _started: null, reopen_seconds: Math.round((opening + Math.max(4, opening * 0.25)) * 100) / 100 });
      return;
    }
    if (action === "repeat") {
      if (message.direction || !session.slats || !["lifting", "review"].includes(session.phase)) throw step();
      const risen = session.phase === "review" ? session.samples.lift : session.motor_stopped ? session.stopped_elapsed : clock - session._started;
      delete session.motor_stopped; delete session.stopped_elapsed;
      // Then `confirm_lift`: the run starts again only from ①.
      Object.assign(session, { samples: {}, lift_taken: false, phase: "reclosing", back_stage: "stopping", _started: null, _after_reclose: "confirm_lift", reclose_seconds: back(risen) });
      return;
    }
    if (action === "open" && session.phase === "confirm_closed") Object.assign(session, { phase: "starting_open", reason: null });
    else if (action === "open" && session.phase === "confirm_lift") Object.assign(session, { phase: "starting_lift", reason: null });
    else if (action === "close" && session.phase === "confirm_open") Object.assign(session, { phase: "starting_close", reason: null });
    else if (action === "endpoint" && ["opening", "closing"].includes(session.phase)) {
      session.values[`${session.phase}_time`] = session.motor_stopped ? session.stopped_elapsed : clock - session._started;
      // What ended the run, as Home Assistant's `ended_by`.
      session.ended_by = { ...session.ended_by, [session.phase]: session.motor_stopped ? "bus_stop" : "press" };
      delete session.motor_stopped; delete session.stopped_elapsed;
      session._started = null;
      session.phase = session.phase === "opening" ? "confirm_open" : session.slats ? "confirm_lift" : "review";
    } else throw step();
  }

  // The covers at rest, closed (a times-only measurement starts at once from a closed cover).
  const states = Object.fromEntries((inventory.entities || []).filter((entity) => entity.domain === "cover" || String(entity.entity_id).startsWith("cover."))
    .map((entity) => [entity.entity_id, { state: "closed", attributes: {} }]));
  const services = [];
  const hass = {
    language, states, services,
    callService: async (domain, service, data) => { services.push({ domain, service, data }); },
    connection: {
      subscribeEvents: async () => () => {},
      subscribeMessage: async (callback, message) => {
        subscriptions.push(message);
        if (message.type === "myhome/cover_profiles/subscribe") { profilesListener = callback; return () => {}; }
        if (message.type === "myhome/cover_calibration/start") {
          // As in Home Assistant, a start without `mode` is the guided measurement.
          session = message.mode === "geometry" ? startGeometry(message) : startGuided(message);
          if (message.origin) session.origin = message.origin;
          refresh();
        } else if (message.type === "myhome/cover_calibration/batch_start") {
          // A sequence: the automatic cycle of each cover in turn (`cover_index`), confirmed with `run`.
          session = { entry_id: message.entry_id, entity_id: message.entity_ids[0], session_id: "session-1", sequence: 1, revision: message.revision,
            mode: "automatic", phase: "confirm_automatic", reason: null, values: {}, stop_requested: false, save_modes: ["new"], run_index: 0,
            cover_index: 0, results: [], targets: message.entity_ids.map((entity_id) => ({ entity_id })), _started: null };
        } else if (message.type === "myhome/cover_calibration/resume") {
          if (!session || session.session_id !== message.session_id) throw Object.assign(new Error("expired"), { code: "calibration_expired" });
          // As Home Assistant: a claim drops the previous owner's screen (readers wait for the new
          // owner's first `show`), so the new owner never finds it in its own first view.
          if (message.claim && !owner) { owner = true; delete session.screen; }
        } else throw new Error(`Unexpected subscription ${message.type}`);
        listener = callback;
        callback(backend.view());
        return () => { if (listener === callback) listener = null; };
      },
    },
    callWS: async (message) => {
      calls.push(message);
      if (message.type === "myhome/panel/inventory") return structuredClone(inventory);
      if (message.type === "myhome/cover_profiles/overview") return structuredClone(overview);
      if (message.type === "myhome/cover_profiles/read") {
        // As Home Assistant, the reader learns only whether it owns the session itself (with its client_id).
        const live = session && !["saved", "cancelled"].includes(session.phase)
          ? { ...backend.view(), owner: false, read_only: true, ...(message.client_id ? { client_owner: owner } : {}) } : null;
        if (live) delete live.attachment;
        return { revision: overview.revision, calibration: live };
      }
      if (message.type === "myhome/cover_calibration/action") {
        const { action } = message;
        if (action === "heartbeat") return backend.view();
        // As Home Assistant: every verb but Stop (and the reading ones) belongs to the owner.
        if (!owner && !["stop", "detach"].includes(action)) throw Object.assign(new Error("owned"), { code: "calibration_owned" });
        if (action === "stop") { if (session.phase !== "review") { session.phase = "interrupted"; session.reason = "stopped"; forgetTap(); } session.sequence += 1; return backend.view(); }
        if (action === "cancel") { session.phase = "cancelled"; session.reason = "cancelled"; session.sequence += 1; return backend.view(); }
        if (action === "detach") return backend.view();
        // The owner's screen, published to readers without a transition.
        if (action === "show") { session.screen = message.screen; return backend.view(); }
        // `shared` is previewed first (the covers that follow the profile), then saved with its confirmation.
        if (action === "preview_save") {
          if (session.phase !== "review" || message.save_mode !== "shared") throw Object.assign(new Error("step"), { code: "calibration_step" });
          if (!session.save_modes.includes("shared")) throw Object.assign(new Error("source"), { code: "profile_not_source" });
          const cover = overview.covers.find((item) => item.entity_id === session.entity_id);
          const profile = overview.profiles.find((item) => item.id === cover?.profile_id);
          // The other covers lose their check (`loses_check`) when they had one, the times changing.
          const followers = (profile?.assigned_to || []).map((id) => ({ entity_id: id, name: inventory.entities.find((entity) => entity.entity_id === id)?.original_name ?? id, available: true,
            loses_check: id !== session.entity_id && overview.covers.find((item) => item.entity_id === id)?.precision === "verified",
            changes: { opening: { before: 1, after: 2, scaled: false, overridden: false }, closing: { before: 1, after: 2, scaled: false, overridden: false } } }));
          backend.savePreview = { revision: overview.revision, profile_id: profile?.id, followers, confirmation: `shared:${session.session_id}` };
          return backend.view({ save_preview: backend.savePreview });
        }
        if (action === "save") {
          if (session.phase !== "review") throw Object.assign(new Error("step"), { code: "calibration_step" });
          if (message.save_mode === "shared" && !session.save_modes.includes("shared")) throw Object.assign(new Error("source"), { code: "profile_not_source" });
          if (!session.save_modes.includes(message.save_mode || "new")) throw Object.assign(new Error("mode"), { code: "invalid_profile" });
          if (message.save_mode === "shared" && message.confirmation !== `shared:${session.session_id}`) throw Object.assign(new Error("preview"), { code: "preview_required" });
          // A new profile from a basic calibration with slats needs the tap, shorter than both runs.
          if (session.mode === "guided" && session.slats && (message.save_mode || "new") === "new") {
            if (!session.lift_taken) throw Object.assign(new Error("lift"), { code: "lift_missing" });
            if (session.samples.lift >= Math.min(session.values.opening_time, session.values.closing_time)) throw Object.assign(new Error("lift"), { code: "lift_not_shorter" });
          }
          session.phase = "saved"; session.saved = { save_mode: message.save_mode, name: message.name }; session.sequence += 1;
          // `persist`: the store is written as Home Assistant would (a new profile followed by this cover), then announced.
          if (persist && message.name) {
            const id = `profile_${overview.profiles.length + 1}`, cover = overview.covers.find((item) => item.entity_id === session.entity_id);
            for (const profile of overview.profiles) profile.assigned_to = profile.assigned_to.filter((entity) => entity !== session.entity_id);
            overview.profiles.push({ id, name: message.name, opening_time: 22.7, closing_time: 22.2, reference_travel_cm: session.travel_cm, geometry: null,
              assigned_to: [session.entity_id], provenance: { opening: { source: "guided", recorded_at: "2026-10-07T09:00:00+00:00", origin_entity_id: session.entity_id,
                origin_name: session.entity_id, inherited: false }, closing: { source: "guided", recorded_at: "2026-10-07T09:00:00+00:00", origin_entity_id: null, origin_name: null, inherited: false } } });
            if (cover) cover.profile_id = id;
            setTimeout(() => backend.publishOverview(), 0);
          }
          return backend.view();
        }
        if (message.sequence !== session.sequence) throw Object.assign(new Error("sequence"), { code: "calibration_sequence" });
        if (action === "continue" || (action === "run" && session.mode === "automatic")) {
          // `continue` only in `paused` (0.40.0); `run` confirms an automatic cycle.
          if (action === "continue" ? session.phase !== "paused" : session.phase !== "confirm_automatic") throw Object.assign(new Error("step"), { code: "calibration_step" });
          const step = session.next_step?.step;
          Object.assign(session, { phase: step === "opening" ? "starting_open" : "starting_close", reason: null, next_step: null, paused_at: null });
          session.sequence += 1;
          return backend.view();
        }
        if (session.mode === "guided") guidedAction(message); else geometryAction(message);
        refresh();
        session.sequence += 1;
        const result = backend.view();
        // The answer reaches the caller first; other subscribers get the same view.
        return result;
      }
      // The travel entered on a cover's card; on the profile's source it is the reference travel too.
      // What the card says of the cover's slats; refused where a slat time was measured on it.
      if (message.type === "myhome/cover_profiles/write" && message.action === "manual_slats") {
        if (message.revision !== overview.revision) throw Object.assign(new Error("revision"), { code: "revision_conflict" });
        const cover = overview.covers.find((item) => item.entity_id === message.entity_id);
        if (["yes_measured", "no_measured"].includes(cover.slats_state)) throw Object.assign(new Error("slats"), { code: "slats_measured" });
        // Only a cover without a profile declares its slats.
        if (cover.profile_id) throw Object.assign(new Error("slats"), { code: "slats_profile" });
        cover.slats_state = message.slats === true ? "yes" : message.slats === false ? "no" : "unknown";
        setTimeout(() => backend.publishOverview(), 0);
        return { revision: overview.revision + 1 };
      }
      if (message.type === "myhome/cover_profiles/write" && message.action === "manual_travel") {
        if (message.revision !== overview.revision) throw Object.assign(new Error("revision"), { code: "revision_conflict" });
        const cover = overview.covers.find((item) => item.entity_id === message.entity_id);
        const profile = overview.profiles.find((item) => item.id === cover?.profile_id);
        if (message.travel_cm === null) delete cover.travel_cm; else cover.travel_cm = message.travel_cm;
        if (profile?.provenance?.opening?.origin_entity_id === cover.entity_id) profile.reference_travel_cm = message.travel_cm;
        setTimeout(() => backend.publishOverview(), 0);
        return { revision: overview.revision + 1 };
      }
      if (message.type === "myhome/cover_profiles/write" && message.action === "assign") {
        if (message.revision !== overview.revision) throw Object.assign(new Error("revision"), { code: "stale_revision" });
        const cover = overview.covers.find((item) => item.entity_id === message.entity_id);
        if (!cover) throw Object.assign(new Error("cover"), { code: "cover_not_found" });
        for (const profile of overview.profiles) profile.assigned_to = profile.assigned_to.filter((entity) => entity !== message.entity_id);
        cover.profile_id = message.profile_id;
        overview.profiles.find((profile) => profile.id === message.profile_id)?.assigned_to.push(message.entity_id);
        setTimeout(() => backend.publishOverview(), 0);
        return { revision: overview.revision + 1 };
      }
      // Several covers given a profile at once, as Home Assistant's catalogue does (cover_profile_assignment.py): the
      // exact proposal and its confirmation first, then the assignment carrying it, both on the current revision.
      if (message.type === "myhome/cover_profiles/manage" && ["preview_assign", "assign"].includes(message.action)) {
        if (message.revision !== overview.revision) throw Object.assign(new Error("revision"), { code: "revision_conflict" });
        const profile = overview.profiles.find((item) => item.id === message.profile_id);
        if (!profile) throw Object.assign(new Error("profile"), { code: "profile_not_found" });
        const ids = [...message.entity_ids].sort();
        for (const id of ids) {
          const cover = overview.covers.find((item) => item.entity_id === id);
          if (!cover) throw Object.assign(new Error("cover"), { code: "target_not_found" });
          if (cover.assignment_reason) throw Object.assign(new Error("cover"), { code: cover.assignment_reason });
        }
        const confirmation = `confirm:${profile.id}:${overview.revision}:${ids.join(",")}`;
        if (message.action === "preview_assign") return { entry_id: overview.entry_id, revision: overview.revision, profile_id: profile.id, profile_name: profile.name,
          targets: ids.map((id) => ({ entity_id: id })), confirmation };
        if (message.confirmation !== confirmation) throw Object.assign(new Error("preview"), { code: "preview_required" });
        for (const id of ids) {
          for (const item of overview.profiles) item.assigned_to = item.assigned_to.filter((entity) => entity !== id);
          overview.covers.find((item) => item.entity_id === id).profile_id = profile.id;
          profile.assigned_to.push(id);
        }
        setTimeout(() => backend.publishOverview(), 0);
        return { entry_id: overview.entry_id, revision: overview.revision + 1, profile_id: profile.id, entity_ids: ids };
      }
      if (message.type === "myhome/cover_calibration/targets") return { revision: overview.revision, max_batch: maxBatch, targets: structuredClone(targets) };
      throw new Error(`Unexpected command ${message.type}`);
    },
  };
  backend.hass = hass;
  /** Home Assistant wrote the profiles store: a new revision is announced and the panel reads the overview again. */
  // A session of the gateway changed (Home Assistant's empty `session` event); a new session in its place.
  backend.sessionEvent = () => profilesListener?.({ entry_id: overview.entry_id, revision: overview.revision, kind: "session" });
  backend.replaceSession = (next) => { session = next; };
  backend.publishOverview = () => { overview.revision += 1; profilesListener?.({ entry_id: overview.entry_id, revision: overview.revision }); };
  return backend;
}
