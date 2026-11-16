/**
 * The world of the profile tests (jsdom): a small Home Assistant whose profile store
 * is kept as `cover_profiles.py` keeps it (`profiles`, `assignments`, `covers`) and from which every answer is derived
 * (`overview`, `read`), so a mutation the panel asks for leaves the same derived fields Home Assistant would
 * (`travel_edit`, `slats_state`, `precision`, the check cleared). The rules of the backend are followed where the panel
 * can meet them: the revision (`revision_conflict`), a profile in use (`profile_in_use`), a profile shared
 * (`profile_shared`), the confirmation of a preview (`preview_required`), and an effective slat phase not shorter than a
 * run (`cover_motion.py` 67-68, `validate_scaling`: Home Assistant's `invalid_profile`).
 *
 * The four roles, fictitious names: Living room is the source of «Living room» and Office follows it (a travel of its
 * own and a check saved); Bedroom has no profile; Attic follows «Hand-made», a profile made by hand (no source). «Spare»
 * is followed by nobody (slats: yes), the other profile to change to, assign or delete.
 */
import { coverInventory } from "./fake-calibration.mjs";
import { dom, reset, tick } from "./wizard-harness.mjs";

export { dom, reset, tick };

export const LIVING = "cover.living_room", OFFICE = "cover.office", BEDROOM = "cover.bedroom", ATTIC = "cover.attic";
export const NAMES = { [LIVING]: "Living room", [OFFICE]: "Office", [BEDROOM]: "Bedroom", [ATTIC]: "Attic" };
const ENTITIES = [LIVING, OFFICE, BEDROOM, ATTIC];
const NATIVE = { opening: 22.0, closing: 21.7 };
const MEASURED_AT = "2026-11-01T16:09:00+00:00";
const MEASURED = ["guided", "automatic"], ROLLS = ["guided", "manual"];
const DIRECTIONS = ["opening", "closing"];

const evidence = (source, origin = null) => ({ source, recorded_at: source === "manual" || source === "unknown" ? null : MEASURED_AT,
  origin_entity_id: origin, origin_name: origin ? NAMES[origin] ?? origin : null, inherited: false });
const round = (number) => Math.round(number * 100) / 100;
const clone = (value) => structuredClone(value);
const refuse = (code) => Object.assign(new Error(code), { code });

/** The store before any operation (Home Assistant's `.storage`, as the tests need it). */
export function initialStore() {
  return {
    revision: 7,
    profiles: {
      living: { name: "Living room", opening_time: 21.8, closing_time: 20.4, reference_travel_cm: 110,
        geometry: { slat_time_s: 0, opening_roll: 1, closing_roll: 1 }, geometry_provenance: { slat_time_s: { source: "guided", origin_entity_id: LIVING } },
        provenance: { opening: evidence("guided", LIVING), closing: evidence("guided", LIVING) } },
      manual: { name: "Hand-made", opening_time: 18.0, closing_time: 17.0, geometry: { slat_time_s: 0, opening_roll: 1, closing_roll: 1 },
        provenance: { opening: evidence("manual"), closing: evidence("manual") } },
      spare: { name: "Spare", opening_time: 25.0, closing_time: 24.0, geometry: { slat_time_s: 1.4, opening_roll: 1, closing_roll: 1 },
        provenance: { opening: evidence("automatic"), closing: evidence("automatic") } },
    },
    assignments: { [LIVING]: "living", [OFFICE]: "living", [ATTIC]: "manual" },
    covers: {
      [LIVING]: { overrides: {}, travel_cm: 110 },
      [OFFICE]: { overrides: {}, travel_cm: 130, check_cm: 1, check_measured_cm: 65.5, check_expected_cm: 65, check_at: "2026-11-04T09:30:00+00:00" },
    },
  };
}

const isSource = (profile, entity) => !!profile && profile.provenance?.opening?.origin_entity_id === entity;
const followersOf = (store, id) => ENTITIES.filter((entity) => store.assignments[entity] === id);

/** `cover_settings.resolve`: the cover's own time, else the profile's (adapted to its travel), else the native one. */
function resolveCover(store, entity) {
  const profile = store.profiles[store.assignments[entity]] ?? null, record = store.covers[entity] ?? {};
  const travel = record.travel_cm ?? null, reference = profile?.reference_travel_cm ?? null;
  const scaled = !!profile && travel != null && reference != null, k = scaled ? travel / reference : 1, source = isSource(profile, entity);
  const out = {};
  for (const direction of DIRECTIONS) {
    const own = record.overrides?.[direction];
    out[direction] = own ? { value: own.value, origin: "override", provenance: own.provenance, scaled: false }
      : profile ? { value: round(profile[`${direction}_time`] * k), origin: "profile", provenance: { ...profile.provenance[direction], inherited: !source }, scaled }
        : { value: NATIVE[direction], origin: "native_fallback", provenance: evidence("unknown"), scaled: false };
  }
  if (profile?.geometry?.slat_time_s > 0) out.slat_time_s = { value: profile.geometry.slat_time_s, origin: "profile", provenance: { ...evidence("guided"), inherited: !source }, scaled: false };
  return out;
}

/** `validate_scaling` (`cover_motion.py` 67-68): the slat phase must be shorter than both effective runs. */
function validate(store) {
  for (const entity of ENTITIES) {
    const profile = store.profiles[store.assignments[entity]];
    if (store.assignments[entity] && !profile) throw refuse("invalid_profile");
    const effective = resolveCover(store, entity), slat = effective.slat_time_s?.value ?? 0;
    if (slat > 0 && slat >= Math.min(effective.opening.value, effective.closing.value)) throw refuse("invalid_profile");
  }
}

function precision(effective, record) {
  const source = (key) => effective?.[key]?.provenance?.source;
  if (!DIRECTIONS.some((direction) => MEASURED.includes(source(direction)))) return "none";
  if (typeof record?.check_cm === "number") return "verified";
  if (["opening_roll", "closing_roll"].every((key) => ROLLS.includes(source(key)))) return "refined";
  return "basic";
}

function travelEdit(store, entity) {
  const profile = store.profiles[store.assignments[entity]];
  if (!profile) return "display";
  return isSource(profile, entity) ? "reference" : profile.reference_travel_cm != null ? "scaled" : "display";
}

function slatsState(store, entity) {
  const profile = store.profiles[store.assignments[entity]];
  if (!profile) { const said = store.covers[entity]?.slats; return said == null ? "unknown" : said ? "yes" : "no"; }
  if (!profile.geometry) return "unknown";
  const has = profile.geometry.slat_time_s > 0 ? "yes" : "no", slat = profile.geometry_provenance?.slat_time_s;
  return `${has}_${isSource(profile, entity) && MEASURED.includes(slat?.source) && slat?.origin_entity_id === entity ? "measured" : "profile"}`;
}

/** `clear_check`: new times without a check of their own. */
function clearCheck(store, entity) {
  const record = store.covers[entity];
  if (record) for (const key of ["check_cm", "check_measured_cm", "check_expected_cm", "check_at"]) delete record[key];
}

const publicProfile = (store, id) => {
  const profile = store.profiles[id];
  return { id, name: profile.name, opening_time: profile.opening_time, closing_time: profile.closing_time, reference_travel_cm: profile.reference_travel_cm ?? null,
    slats: profile.geometry ? profile.geometry.slat_time_s > 0 : null, geometry: profile.geometry ? clone(profile.geometry) : null,
    provenance: clone(profile.provenance), assigned_to: followersOf(store, id) };
};

/** `myhome/cover_profiles/overview`, derived from the store. */
export function overviewOf(store) {
  return {
    entry_id: "one", revision: store.revision,
    capabilities: { height_scaling: true, nonlinear: true, nonlinear_calibration: true, profile_assignment: true, profile_management: true, override_write: true, shared_profile_write: true },
    profiles: Object.keys(store.profiles).map((id) => publicProfile(store, id)),
    covers: ENTITIES.map((entity) => {
      const record = store.covers[entity] ?? {}, effective = resolveCover(store, entity);
      return { entity_id: entity, name: NAMES[entity], assignment_reason: null, profile_id: store.assignments[entity] ?? null,
        ...(record.travel_cm != null ? { travel_cm: record.travel_cm } : {}), available: true, advanced: false, pending: false,
        overrides: Object.fromEntries(Object.entries(record.overrides ?? {}).map(([key, item]) => [key, item.value])),
        effective, precision: precision(effective, record), travel_edit: travelEdit(store, entity), slats_state: slatsState(store, entity),
        ...Object.fromEntries(["check_cm", "check_measured_cm", "check_expected_cm", "check_at"].filter((key) => key in record).map((key) => [key, record[key]])) };
    }),
  };
}

/** `myhome/cover_profiles/read` (`snapshot`) for one cover, derived from the store. */
function readOf(store, entity) {
  const record = store.covers[entity] ?? {}, effective = resolveCover(store, entity), assigned = store.assignments[entity] ?? null;
  return {
    entry_id: "one", entity_id: entity, calibration: null, revision: store.revision, assigned_profile_id: assigned,
    model: "linear_time", nonlinear: true, position_known: false, scaling: "unscaled", travel_cm: record.travel_cm ?? null, height_scaling: true,
    profiles: Object.keys(store.profiles).map((id) => ({ ...publicProfile(store, id), uses: followersOf(store, id).length,
      assigned_to: followersOf(store, id).map((item) => ({ entity_id: item, name: NAMES[item] })) })),
    writable: true, reason: null, default_travel_time: 30,
    effective_travel_time: effective.opening.value, effective_opening_time: effective.opening.value, effective_closing_time: effective.closing.value,
    pending: false, configured: effective, effective, precision: precision(effective, record), travel_edit: travelEdit(store, entity), slats_state: slatsState(store, entity),
  };
}

/** `reference_profile`: an omitted reference travel or geometry is kept, an explicit null removes it. */
function referenceProfile(profile, previous) {
  const result = clone(profile);
  if (!("reference_travel_cm" in result) && previous && "reference_travel_cm" in previous) result.reference_travel_cm = previous.reference_travel_cm;
  if (result.reference_travel_cm === null) delete result.reference_travel_cm;
  if (!("geometry" in result) && previous?.geometry) result.geometry = clone(previous.geometry);
  if (result.geometry === null) delete result.geometry;
  return result;
}

const timesChanged = (before, after) => DIRECTIONS.some((direction) => before[`${direction}_time`] !== after[`${direction}_time`]);
const token = (...parts) => `confirm:${JSON.stringify(parts)}`;

function sharedPreview(store, id, profile) {
  const previous = store.profiles[id];
  const keys = new Set([...Object.keys(profile), ...("reference_travel_cm" in previous ? ["reference_travel_cm"] : []), ...("geometry" in previous ? ["geometry"] : [])]);
  const after = { ...store, profiles: { ...store.profiles, [id]: { ...profile, provenance: previous.provenance } } };
  return {
    revision: store.revision, profile_id: id,
    before: Object.fromEntries([...keys].map((key) => [key, previous[key] ?? null])), after: Object.fromEntries([...keys].map((key) => [key, profile[key] ?? null])),
    followers: followersOf(store, id).map((entity) => {
      const was = resolveCover(store, entity), will = resolveCover(after, entity);
      return { entity_id: entity, name: NAMES[entity], available: true, changes: Object.fromEntries(DIRECTIONS.map((direction) => [direction,
        { before: was[direction].value, after: will[direction].value, scaled: will[direction].scaled, overridden: will[direction].origin === "override" }])) };
    }),
    confirmation: token(store.revision, id, profile),
  };
}

/**
 * The backend: `hass` for the panel, `store` (Home Assistant's data), `calls` (every command the panel sent) and
 * `writes()` (those that change the store). `refuseNext(code)` makes the next mutating command fail with `code`
 * (a refusal Home Assistant gives for a reason the tests cannot stage); `silentBump()` saves a change made elsewhere
 * whose notification has not reached the panel yet.
 */
export function createProfilesBackend(store = initialStore()) {
  const calls = [], listeners = new Set();
  let refusal = null;
  const backend = { store, calls };
  const commit = (next, mutate) => {
    const draft = clone(next);
    mutate(draft);
    validate(draft);
    draft.revision += 1;
    Object.assign(store, draft);
    for (const listener of [...listeners]) listener({ entry_id: "one", revision: store.revision, kind: "changed" });
  };
  const checked = (message) => {
    if (refusal) { const code = refusal; refusal = null; throw refuse(code); }
    if (message.entry_id !== "one") throw refuse("target_not_found");
    if (message.revision !== store.revision) throw refuse("revision_conflict");
  };

  function manage(message) {
    const { action, profile_id: id } = message;
    if (!["preview", "preview_assign"].includes(action) && refusal) checked(message);
    if (message.revision !== store.revision) throw refuse("revision_conflict");
    if (!store.profiles[id]) throw refuse("profile_not_found");
    if (action === "preview_assign" || action === "assign") {
      const ids = [...message.entity_ids].sort();
      for (const entity of ids) if (!ENTITIES.includes(entity)) throw refuse("target_not_found");
      const confirmation = token("assign", store.revision, id, ids);
      if (action === "preview_assign") {
        return { entry_id: "one", revision: store.revision, profile_id: id, profile_name: store.profiles[id].name, confirmation,
          targets: ids.map((entity) => {
            const previous = store.profiles[store.assignments[entity]], was = resolveCover(store, entity);
            const will = resolveCover({ ...store, assignments: { ...store.assignments, [entity]: id } }, entity);
            return { entity_id: entity, name: NAMES[entity], previous_profile_id: store.assignments[entity] ?? null, previous_profile_name: previous?.name ?? null,
              changes: Object.fromEntries(DIRECTIONS.map((direction) => [direction, { before: was[direction].value, after: will[direction].value, overridden: will[direction].origin === "override", scaled: will[direction].scaled }])) };
          }) };
      }
      if (message.confirmation !== confirmation) throw refuse("preview_required");
      commit(store, (draft) => { for (const entity of ids) { if (draft.assignments[entity] !== id) clearCheck(draft, entity); draft.assignments[entity] = id; } });
      return { entry_id: "one", revision: store.revision, profile_id: id, entity_ids: ids };
    }
    if (action === "preview" || action === "update") {
      const previous = store.profiles[id], profile = referenceProfile(message.profile, previous), preview = sharedPreview(store, id, profile);
      if (action === "preview") return preview;
      if (message.confirmation !== preview.confirmation) throw refuse("preview_required");
      commit(store, (draft) => {
        profile.provenance = Object.fromEntries(DIRECTIONS.map((direction) => [direction, previous[`${direction}_time`] === profile[`${direction}_time`] ? clone(previous.provenance[direction]) : evidence("manual")]));
        if (previous.geometry_provenance && profile.geometry) profile.geometry_provenance = clone(previous.geometry_provenance);
        draft.profiles[id] = profile;
        if (timesChanged(previous, profile)) for (const entity of followersOf(draft, id)) clearCheck(draft, entity);
      });
      return { entry_id: "one", revision: store.revision, profile_id: id };
    }
    if (action === "duplicate") {
      const name = String(message.name ?? "").trim();
      if (!name || name.length > 64) throw refuse("invalid_profile");
      const copy = `profile_${Object.keys(store.profiles).length + 1}`;
      commit(store, (draft) => { draft.profiles[copy] = { ...clone(store.profiles[id]), name }; });
      return { entry_id: "one", revision: store.revision, profile_id: copy };
    }
    if (action === "delete") {
      if (followersOf(store, id).length) throw refuse("profile_in_use");
      commit(store, (draft) => { delete draft.profiles[id]; });
      return { entry_id: "one", revision: store.revision, profile_id: id };
    }
    throw refuse("invalid_profile");
  }

  function write(message) {
    const { action, entity_id: entity } = message;
    if (!ENTITIES.includes(entity)) throw refuse("target_not_found");
    if (action !== "preview") checked(message);
    else if (message.revision !== store.revision) throw refuse("revision_conflict");
    const id = message.profile_id ?? null;
    if (id !== null && !store.profiles[id]) throw refuse("profile_not_found");
    if (action === "preview" || action === "update_shared") {
      if (store.assignments[entity] !== id) throw refuse("profile_shared");
      const previous = store.profiles[id], profile = referenceProfile(message.profile, previous), preview = sharedPreview(store, id, profile);
      if (action === "preview") return preview;
      if (message.confirmation !== preview.confirmation) throw refuse("preview_required");
      commit(store, (draft) => {
        profile.provenance = Object.fromEntries(DIRECTIONS.map((direction) => [direction, previous[`${direction}_time`] === profile[`${direction}_time`] ? clone(previous.provenance[direction]) : evidence("manual", entity)]));
        draft.profiles[id] = profile;
        if (timesChanged(previous, profile)) for (const item of followersOf(draft, id)) clearCheck(draft, item);
      });
      return readOf(store, entity);
    }
    if (action === "save") {
      const copyFrom = message.copy_from_profile_id ?? null;
      if (copyFrom !== null && id !== null) throw refuse("invalid_profile");
      if (copyFrom !== null && !store.profiles[copyFrom]) throw refuse("profile_not_found");
      const previous = store.profiles[copyFrom ?? id] ?? null, profile = referenceProfile(message.profile, previous);
      if (!profile.name || !(profile.opening_time >= 1 && profile.opening_time <= 600) || !(profile.closing_time >= 1 && profile.closing_time <= 600)) throw refuse("invalid_profile");
      if (id !== null && (store.assignments[entity] !== id || followersOf(store, id).length > 1)) throw refuse("profile_shared");
      const target = id ?? `profile_${Object.keys(store.profiles).length + 1}`;
      commit(store, (draft) => {
        profile.provenance = Object.fromEntries(DIRECTIONS.map((direction) => [direction, previous && previous[`${direction}_time`] === profile[`${direction}_time`] ? clone(previous.provenance[direction]) : evidence("manual", entity)]));
        const changed = !previous || timesChanged(previous, profile), moved = draft.assignments[entity] !== target;
        draft.profiles[target] = profile;
        draft.assignments[entity] = target;
        if (changed || moved) clearCheck(draft, entity);
      });
      return readOf(store, entity);
    }
    if (action === "assign") {
      commit(store, (draft) => {
        const moved = draft.assignments[entity] !== (id ?? undefined);
        if (id === null) delete draft.assignments[entity]; else draft.assignments[entity] = id;
        if (moved) clearCheck(draft, entity);
      });
      return readOf(store, entity);
    }
    if (action === "delete") {
      if (id === null) throw refuse("profile_not_found");
      if (followersOf(store, id).length) throw refuse("profile_in_use");
      commit(store, (draft) => { delete draft.profiles[id]; });
      return readOf(store, entity);
    }
    throw refuse(`unexpected_write_${action}`);
  }

  const states = Object.fromEntries(ENTITIES.map((entity) => [entity, { state: "closed", attributes: { friendly_name: NAMES[entity] } }]));
  backend.hass = {
    language: "it", states, themes: { darkMode: false },
    callService: async () => {},
    connection: {
      subscribeEvents: async () => () => {},
      subscribeMessage: async (callback, message) => {
        if (message.type !== "myhome/cover_profiles/subscribe") throw refuse(`unexpected_subscription_${message.type}`);
        listeners.add(callback);
        return () => { listeners.delete(callback); };
      },
    },
    callWS: async (message) => {
      calls.push(clone(message));
      if (message.type === "myhome/panel/inventory") return coverInventory(ENTITIES.map((entity, index) => [entity, NAMES[entity], index % 2 ? "kitchen" : "living"]));
      if (message.type === "myhome/cover_profiles/overview") return overviewOf(store);
      if (message.type === "myhome/cover_profiles/read") return readOf(store, message.entity_id);
      if (message.type === "myhome/cover_profiles/manage") return manage(message);
      if (message.type === "myhome/cover_profiles/write") return write(message);
      if (message.type === "myhome/cover_calibration/targets") return { revision: store.revision, max_batch: 4, targets: [] };
      throw refuse(`unexpected_command_${message.type}`);
    },
  };
  /** The commands that change the store (a preview changes nothing). */
  backend.writes = () => calls.filter((message) => (message.type === "myhome/cover_profiles/write" && message.action !== "preview")
    || (message.type === "myhome/cover_profiles/manage" && !["preview", "preview_assign"].includes(message.action)));
  backend.refuseNext = (code) => { refusal = code; };
  backend.silentBump = () => { store.revision += 1; };
  /** The notification of a change saved elsewhere reaches the panel (late). */
  backend.notify = () => { for (const listener of [...listeners]) listener({ entry_id: "one", revision: store.revision, kind: "changed" }); };
  return backend;
}

/** The panel on its WHO 2 section, on `view` (devices, profiles, calibration), with this backend. */
export async function mountPanel(store = initialStore()) {
  reset();
  const backend = createProfilesBackend(store);
  const panel = document.createElement("myhome-panel");
  panel.hass = backend.hass;
  document.body.append(panel);
  await tick(8);
  const root = panel.shadowRoot;
  const context = { backend, panel, root };
  context.view = async (id) => {
    const button = root.querySelector(`[data-action="cover-view"][data-id="${id}"]`);
    if (!button) throw new Error(`the view ${id} is not offered`);
    if (button.getAttribute("aria-pressed") !== "true") { button.click(); await tick(8); }
  };
  context.tab = () => root.getElementById("cover-calibration-tab");
  return context;
}
