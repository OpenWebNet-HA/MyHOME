/**
 * A realistic house for the browser checks of the Calibration tab and the wizard: generic Italian rooms and covers,
 * names of every length, and a mix of states (calibrated with and without slats, times only, never measured with
 * the integration's default or YAML times, following a profile, a cover with a position sensor) plus optional
 * sessions (in progress, owner absent, paused). Names, addresses and identifiers are made up; the gateway's MAC and
 * host are documentation values. Use: `createFakeBackend(realisticFixture({ session: "running" }))`.
 */
const ENTRY = "home";

const AREAS = [
  ["lounge", "Lounge"], ["kitchen", "Kitchen"], ["bedroom", "Main bedroom"], ["study", "Upstairs study"],
  ["guests", "Guest room"], ["bathroom", "Bathroom"], ["hallway", "Hallway"],
];

// [entity id, name, area, A, PL]
const COVERS = [
  ["cover.lounge_1", "Lounge Shutter 1", "lounge", "1", "1"],
  ["cover.lounge_2", "Lounge Shutter 2", "lounge", "1", "2"],
  ["cover.patio_door", "Patio Door", "lounge", "1", "3"],
  ["cover.kitchen", "Kitchen Shutter", "kitchen", "2", "1"],
  ["cover.bedroom_garden", "Main Bedroom Shutter Garden Side", "bedroom", "10", "15"],
  ["cover.bedroom_street", "Main Bedroom Shutter", "bedroom", "3", "2"],
  ["cover.study", "Study Shutter", "study", "4", "1"],
  ["cover.guest_room", "Guest Room Shutter", "guests", "5", "1"],
  ["cover.bathroom", "Bathroom Shutter", "bathroom", "7", "1"],
  ["cover.hallway", "Hallway Shutter", "hallway", "6", "1"],
  // A motorised awning with a position sensor: never in the Calibration tab.
  ["cover.patio_awning", "Patio Awning", "lounge", "8", "1"],
];

export function realisticInventory() {
  return {
    version: "2.0.0b13", panel_version: "0.43.0",
    gateways: [{ entry_id: ENTRY, title: "Home", mac: "00:03:50:00:00:01", model: "F454", host: "192.0.2.10", state: "loaded", connected: true, monitor_available: true }],
    devices: [],
    entities: COVERS.map(([entity_id, name, area_id, a, pl]) => ({ entity_id, entry_id: ENTRY, domain: "cover", who: "2", name: null, original_name: name,
      device_id: null, area_id, unique_id: `uid-${entity_id}`, address: { raw: `${a}${pl}`, a, pl, interface: null } })),
    areas: AREAS.map(([id, name]) => ({ id, name })),
  };
}

const evidence = (source, recorded_at = null, extra = {}) => ({ source, recorded_at, origin_entity_id: null, origin_name: null, inherited: false, ...extra });
const value = (number, provenance, origin = "override") => ({ value: number, origin, provenance, scaled: false });
const inherited = (source, recorded_at, origin) => evidence(source, recorded_at, { inherited: true, origin_entity_id: origin, origin_name: origin });

export function realisticOverview() {
  const sep12 = "2026-09-12T08:30:00+00:00", sep20 = "2026-09-20T17:05:00+00:00", oct2 = "2026-10-02T09:40:00+00:00", aug30 = "2026-08-30T15:20:00+00:00";
  const lounge = evidence("guided", sep12), gardenSide = evidence("guided", sep20), hallway = evidence("guided", aug30);
  return {
    entry_id: ENTRY, revision: 12, capabilities: { profile_management: true, profile_assignment: true },
    profiles: [
      { id: "tall4", name: "tall_four_leaf", opening_time: 24.6, closing_time: 23.9, reference_travel_cm: 210,
        geometry: { slat_time_s: 4.4, opening_roll: 1.83, closing_roll: 1.91 }, assigned_to: ["cover.lounge_1", "cover.lounge_2"],
        provenance: { opening: evidence("guided", sep12, { origin_entity_id: "cover.lounge_1", origin_name: "cover.lounge_1" }), closing: lounge } },
      { id: "standard", name: "standard_three_leaf", opening_time: 18.2, closing_time: 17.6, reference_travel_cm: 150, geometry: null,
        assigned_to: ["cover.hallway", "cover.guest_room"],
        provenance: { opening: evidence("guided", aug30, { origin_entity_id: "cover.hallway", origin_name: "cover.hallway" }), closing: hallway } },
      { id: "garden", name: "north_garden_side_shutters_profile", opening_time: 27.1, closing_time: 26.4, reference_travel_cm: 240,
        geometry: { slat_time_s: 0, opening_roll: 2.07, closing_roll: 2.12 }, assigned_to: ["cover.bedroom_garden", "cover.bedroom_street"],
        provenance: { opening: evidence("guided", sep20, { origin_entity_id: "cover.bedroom_garden", origin_name: "cover.bedroom_garden" }), closing: gardenSide } },
    ],
    covers: [
      // Calibrated with slats (times and rolls), source of «tall_four_leaf».
      { entity_id: "cover.lounge_1", name: "Lounge Shutter 1", profile_id: "tall4", travel_cm: 208, available: true, advanced: false, overrides: {},
        // L-73: its check kept at the save, 2 cm: «Verificata · 2 cm» in green.
        check_cm: 2, check_measured_cm: 101, check_expected_cm: 103.4, check_at: "2026-09-12T10:20:00+00:00",
        effective: { opening: value(24.6, lounge, "profile"), closing: value(23.9, lounge, "profile"), slat_time_s: value(4.4, lounge, "profile"),
          opening_roll: value(1.83, lounge, "profile"), closing_roll: value(1.91, lounge, "profile") } },
      // Follows «tall_four_leaf», scaled to its own travel.
      { entity_id: "cover.lounge_2", name: "Lounge Shutter 2", profile_id: "tall4", travel_cm: 204, available: true, advanced: false, overrides: {},
        effective: { opening: value(24.1, inherited("guided", sep12, "cover.lounge_1"), "profile"), closing: value(23.4, inherited("guided", sep12, "cover.lounge_1"), "profile"),
          slat_time_s: value(4.4, inherited("guided", sep12, "cover.lounge_1"), "profile"),
          opening_roll: value(1.83, inherited("guided", sep12, "cover.lounge_1"), "profile"), closing_roll: value(1.91, inherited("guided", sep12, "cover.lounge_1"), "profile") } },
      // Never measured, the integration's default; travel close to «tall_four_leaf» (path B advised).
      { entity_id: "cover.patio_door", name: "Patio Door", profile_id: null, travel_cm: 212, available: true, advanced: false, overrides: {},
        effective: { opening: value(30, evidence("unknown"), "default"), closing: value(30, evidence("unknown"), "default") } },
      // Times only, measured, no profile.
      { entity_id: "cover.kitchen", name: "Kitchen Shutter", profile_id: null, travel_cm: 132, available: true, advanced: false, overrides: {},
        effective: { opening: value(16.8, evidence("guided", oct2)), closing: value(16.1, evidence("guided", oct2)) } },
      // Calibrated without slats, source of the long profile.
      { entity_id: "cover.bedroom_garden", name: "Main Bedroom Shutter Garden Side", profile_id: "garden", travel_cm: 240, available: true, advanced: false, overrides: {},
        // L-73: a check beyond the 4 cm threshold: «Verificata · 6 cm» in yellow.
        check_cm: 6, check_measured_cm: 126, check_expected_cm: 120.2, check_at: "2026-09-20T18:05:00+00:00",
        effective: { opening: value(27.1, gardenSide, "profile"), closing: value(26.4, gardenSide, "profile"), slat_time_s: value(0, gardenSide, "profile"),
          opening_roll: value(2.07, gardenSide, "profile"), closing_roll: value(2.12, gardenSide, "profile") } },
      // Follows the long profile, times only on its side (scaled).
      { entity_id: "cover.bedroom_street", name: "Main Bedroom Shutter", profile_id: "garden", travel_cm: 236, available: true, advanced: false, overrides: {},
        effective: { opening: value(26.7, inherited("guided", sep20, "cover.bedroom_garden"), "profile"), closing: value(26.0, inherited("guided", sep20, "cover.bedroom_garden"), "profile") } },
      // Never measured, times from YAML.
      { entity_id: "cover.study", name: "Study Shutter", profile_id: null, travel_cm: null, available: true, advanced: false, overrides: {},
        effective: { opening: value(21, evidence("unknown"), "yaml"), closing: value(20, evidence("unknown"), "yaml") } },
      // Follows «standard_three_leaf» (times only).
      { entity_id: "cover.guest_room", name: "Guest Room Shutter", profile_id: "standard", travel_cm: 148, available: true, advanced: false, overrides: {},
        effective: { opening: value(18.2, inherited("guided", aug30, "cover.hallway"), "profile"), closing: value(17.6, inherited("guided", aug30, "cover.hallway"), "profile") } },
      // Never measured, default times; travel close to «standard_three_leaf».
      { entity_id: "cover.bathroom", name: "Bathroom Shutter", profile_id: null, travel_cm: 146, available: true, advanced: false, overrides: {},
        effective: { opening: value(30, evidence("unknown"), "default"), closing: value(30, evidence("unknown"), "default") } },
      // Times only, source of «standard_three_leaf».
      { entity_id: "cover.hallway", name: "Hallway Shutter", profile_id: "standard", travel_cm: 150, available: true, advanced: false, overrides: {},
        effective: { opening: value(18.2, hallway, "profile"), closing: value(17.6, hallway, "profile") } },
      // Position sensor: no resolved timings for this view.
      { entity_id: "cover.patio_awning", name: "Patio Awning", profile_id: null, travel_cm: null, available: true, advanced: true, overrides: {}, effective: null },
    ],
  };
}

/** The gateway's targets for a sequence: every timed cover, one of them refused by Home Assistant. */
export function realisticTargets() {
  return COVERS.filter(([entity_id]) => entity_id !== "cover.patio_awning").map(([entity_id, name]) => ({ entity_id, name, reason: null }));
}

/** A session already open on the gateway when the page loads (another tab's, or this browser's before a reload). */
export function realisticSession(kind) {
  if (kind === "running" || kind === "absent") {
    return { entry_id: ENTRY, entity_id: "cover.bathroom", session_id: "session-1", sequence: 14, revision: 12, mode: "geometry", phase: "briefing", step: "half_open",
      reason: null, values: { opening_time: 21.4, closing_time: 20.6 }, samples: { lift: 2.1 }, readings: { gap: 3 }, geometry: {}, travel_cm: 146,
      stop_requested: false, slats: true, lift_attempts: 1, still_resting: false, gap_warning: false, touching_cm: 1, max_gap_cm: 20, gap_warn_cm: 10,
      lift_repeat: true, saved_travel_cm: 146, expected_cm: null, can_repeat: true, save_modes: ["new"], reading_kind: "half_open",
      _after_stop: "briefing", _after_position: "half_open", _started: null };
  }
  if (kind === "paused") {
    return { entry_id: ENTRY, entity_id: "cover.hallway", session_id: "session-1", sequence: 9, revision: 12, mode: "automatic", phase: "paused",
      reason: "owner_absent", values: { closing_time: 17.5 }, run_index: 1, stop_requested: false, save_modes: ["new", "cover", "shared"],
      next_step: { step: "opening", run_index: 1, entity_id: "cover.hallway" }, paused_at: "2026-10-07T07:58:00+00:00", idle_expires_at: new Date(Date.now() + 9 * 60000).toISOString(), recovery_seconds: 540, _started: null };
  }
  return null;
}

/** Options for `createFakeBackend`: the realistic house, optionally with a session already open. */
export function realisticFixture({ session = null, language = "it" } = {}) {
  return {
    language, inventory: realisticInventory(), overview: realisticOverview(), targets: realisticTargets(), maxBatch: 6, persist: true,
    session: realisticSession(session), owner: session ? false : true, ownerPresent: session !== "absent" && session !== "paused",
  };
}
