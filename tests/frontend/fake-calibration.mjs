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

export function sampleOverview() {
  const measured = evidence("guided", "2026-09-29T10:00:00+00:00");
  return {
    entry_id: "one", revision: 7, capabilities: { profile_management: true, profile_assignment: true },
    profiles: [{ id: "tall", name: "tall_two_leaf", opening_time: 22.4, closing_time: 21.9, reference_travel_cm: 198,
      geometry: { slat_time_s: 4.4, opening_roll: 1.83, closing_roll: 1.91 }, assigned_to: ["cover.kitchen_two", "cover.living_three"],
      provenance: { opening: evidence("guided", "2026-09-29T10:00:00+00:00", { origin_entity_id: "cover.living_one", origin_name: "cover.living_one" }), closing: measured } }],
    covers: [
      { entity_id: "cover.living_one", name: "Living room shutter 1", profile_id: null, travel_cm: 195, available: true, advanced: false, overrides: {},
        effective: { opening: value(22.0, evidence("unknown"), "native_fallback"), closing: value(21.7, evidence("unknown"), "native_fallback") } },
      { entity_id: "cover.kitchen_two", name: "Kitchen shutter 2", profile_id: "tall", travel_cm: 110, available: true, advanced: false, overrides: {},
        effective: { opening: value(12.3, measured, "profile"), closing: value(12.1, measured, "profile"), slat_time_s: value(2.4, measured, "profile"),
          opening_roll: value(1.83, measured, "profile"), closing_roll: value(1.91, measured, "profile") } },
      { entity_id: "cover.living_three", name: "Living room shutter 3", profile_id: "tall", travel_cm: 196, available: true, advanced: false, overrides: {},
        effective: { opening: value(22.2, measured, "profile"), closing: value(21.8, measured, "profile") } },
    ],
  };
}

export function createFakeBackend({ language = "en", inventory = coverInventory([["cover.living_one", "Living room shutter 1", "living"],
  ["cover.kitchen_two", "Kitchen shutter 2", "kitchen"], ["cover.living_three", "Living room shutter 3", "living"]]), overview = sampleOverview(),
  session: initial = null, owner = true, ownerPresent = true, targets = [], maxBatch = 4, persist = false } = {}) {
  const calls = [], subscriptions = [];
  let session = initial, listener = null, clock = 0, profilesListener = null;
  const backend = {
    calls, subscriptions, overview,
    get session() { return session; },
    tick(seconds) { clock += seconds; },
    view(extra = {}) {
      const { _after_stop, _after_position, _started, ...public_ } = session;
      void _after_stop; void _after_position;
      return { ...public_, elapsed: _started == null ? null : Math.round((clock - _started) * 100) / 100,
        owner, read_only: !owner, attachment: "attachment-1", attached: true, owner_present: ownerPresent, recoverable: !["saved", "cancelled"].includes(session.phase), ...extra };
    },
    emit() { if (session.mode === "geometry") refresh(); session.sequence += 1; listener?.(backend.view()); },
    /** The bus confirms the movement asked by `next`, `open` or `close`. */
    started() {
      session.phase = session.phase === "starting_open" ? "opening" : "closing";
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
    /** The scheduled Stop of an intermediate run. */
    autoStop() {
      session.samples[session.step] = clock - session._started;
      session.phase = "geometry_wait_stop"; session._after_stop = "reading";
      backend.emit();
    },
    /** Presence of the owning tab as Home Assistant reports it (`owner_present`). */
    setPresence(present) { ownerPresent = present; },
    /** Another tab presses Stop: the session ends and every subscriber gets the view. */
    stopFromElsewhere() { session.phase = "interrupted"; session.reason = "stopped"; session._started = null; backend.emit(); },
    interrupt(reason) { session.phase = "interrupted"; session.reason = reason; session._started = null; backend.emit(); },
  };

  const startGeometry = (message) => ({
    entry_id: message.entry_id, entity_id: message.entity_id, session_id: "session-1", sequence: 1, revision: message.revision,
    mode: "geometry", phase: "briefing", step: "home", reason: null, values: {}, samples: {}, readings: {}, geometry: {}, travel_cm: null,
    stop_requested: false, slats: message.slats !== false, lift_attempts: 1, still_resting: false, gap_warning: false, touching_cm: 1, max_gap_cm: 20,
    gap_warn_cm: 10, lift_repeat: true, saved_travel_cm: 195, expected_cm: null, can_repeat: false, save_modes: ["new"], reading_kind: "home",
    _after_stop: "briefing", _after_position: message.slats === false ? "opening" : "lift", _started: null });
  const startGuided = (message) => ({
    entry_id: message.entry_id, entity_id: message.entity_id, session_id: "session-1", sequence: 1, revision: message.revision, mode: "guided",
    phase: "confirm_closed", reason: null, values: {}, stop_requested: false, save_modes: ["new", "cover", "shared"], _started: null });

  const repeatable = () => session.phase === "reading" || session.phase === "review" || (session.phase === "briefing" && session.step === "half_open") || session.gap_warning;
  const refresh = () => {
    session.can_repeat = repeatable();
    session.reading_kind = session.step === "opening" ? "travel" : session.step;
    session.expected_cm = session.travel_cm && session.step?.startsWith("half_") ? session.travel_cm / 2 : null;
    // As cover_calibration_geometry.py publishes them (L-9): the physical heights accepted at this stop (up to the
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
    } else if (action === "lift" && session.step === "lift" && session.phase === "opening") {
      session.samples.lift = clock - session._started;
      session.phase = "geometry_wait_stop"; session._after_stop = "reading";
    } else if (action === "endpoint" && ["opening", "closing"].includes(session.phase) && ["home", "reset", "opening", "closing", "top"].includes(session.step)) {
      // As v2-endpoint: confirmed after the bus reported the stop, the stop frame's time is the run's.
      if (["opening", "closing"].includes(session.step)) session.values[`${session.step}_time`] = session.motor_stopped ? session.stopped_elapsed : clock - session._started;
      delete session.motor_stopped; delete session.stopped_elapsed;
      session._after_stop = session.step === "opening" ? "reading" : "briefing";
      session.phase = "geometry_wait_stop";
    } else if (action === "reading" && session.phase === "reading") {
      const number = message.reading_cm;
      if (session.step === "lift") {
        if (number > session.max_gap_cm) throw Object.assign(new Error("gap"), { code: "invalid_gap" });
        session.still_resting = number < session.touching_cm;
        if (session.still_resting) { delete session.samples.lift; session.lift_attempts += 1; } else session.readings.gap = number;
        session.gap_warning = number >= session.gap_warn_cm;
        session.step = "reset"; session._after_position = session.still_resting ? "lift" : "opening";
      } else if (session.step === "check") {
        const expected = session.check.target_cm, deviation = Math.round(number - expected);
        Object.assign(session.check, { check_seconds: session.check.planned_seconds, expected_cm: expected, measured_cm: number, deviation_cm: deviation, passed: Math.abs(deviation) <= 4 });
        session.phase = "review"; session.step = "half_close"; refresh(); return;
      } else if (session.step === "opening") {
        session.travel_cm = number; session.step = "closing";
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
      } else {
        session.geometry.closing_roll = 1.91; session.readings.half_close = number; session.phase = "review"; refresh(); return;
      }
      session.phase = "briefing";
    } else if (action === "check" && session.phase === "review" && backend.realisticCheck) {
      // L-44: as the backend: to the end stop first, then (on `next`) the timed run, then the reading.
      session.check = { direction: message.direction, target_cm: message.target_cm, check_seconds: null, planned_seconds: 12.4,
        expected_cm: null, measured_cm: null, deviation_cm: null, passed: null };
      session.step = message.direction === "opening" ? "home" : "top"; session._after_position = "check"; session.phase = "briefing";
    } else if (action === "check" && session.phase === "review") {
      // L-37/L-43: the whole check (end stop, timed run, reading) at once: a passed check, 1 cm off.
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
    if (action === "open" && session.phase === "confirm_closed") session.phase = "starting_open";
    else if (action === "close" && session.phase === "confirm_open") session.phase = "starting_close";
    else if (action === "endpoint" && ["opening", "closing"].includes(session.phase)) {
      session.values[`${session.phase}_time`] = session.motor_stopped ? session.stopped_elapsed : clock - session._started;
      delete session.motor_stopped; delete session.stopped_elapsed;
      session._started = null;
      session.phase = session.phase === "opening" ? "confirm_open" : "review";
    } else throw Object.assign(new Error("step"), { code: "calibration_step" });
  }

  // The covers at rest, closed (L-38: a times-only measurement starts at once from a closed cover).
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
          // As Home Assistant since the review nit: a claim drops the previous owner's screen (readers wait for the new
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
        // L-93: as Home Assistant, the reader learns only whether it owns the session itself (with its client_id).
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
        if (action === "stop") { if (session.phase !== "review") { session.phase = "interrupted"; session.reason = "stopped"; } session.sequence += 1; return backend.view(); }
        if (action === "cancel") { session.phase = "cancelled"; session.reason = "cancelled"; session.sequence += 1; return backend.view(); }
        if (action === "detach") return backend.view();
        // L-25: the owner's screen, published to readers without a transition.
        if (action === "show") { session.screen = message.screen; return backend.view(); }
        if (action === "save") {
          if (session.phase !== "review") throw Object.assign(new Error("step"), { code: "calibration_step" });
          if (!session.save_modes.includes(message.save_mode || "new")) throw Object.assign(new Error("mode"), { code: "invalid_profile" });
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
      if (message.type === "myhome/cover_calibration/targets") return { revision: overview.revision, max_batch: maxBatch, targets: structuredClone(targets) };
      throw new Error(`Unexpected command ${message.type}`);
    },
  };
  backend.hass = hass;
  /** Home Assistant wrote the profiles store: a new revision is announced and the panel reads the overview again. */
  // L-107/L-113: a session of the gateway changed (Home Assistant's empty `session` event); a new session in its place.
  backend.sessionEvent = () => profilesListener?.({ entry_id: overview.entry_id, revision: overview.revision, kind: "session" });
  backend.replaceSession = (next) => { session = next; };
  backend.publishOverview = () => { overview.revision += 1; profilesListener?.({ entry_id: overview.entry_id, revision: overview.revision }); };
  return backend;
}
