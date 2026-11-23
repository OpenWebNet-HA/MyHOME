/**
 * Giving a profile to several covers of a gateway at once, with ONE list: the wizard's step A-35 (after A-34). The
 * Calibration tab uses the same rows' line (room and bus address) and the same refusal texts. The rows are the wizard's pick-list rows (`cal-batch-row wiz-pick-row`) with a
 * checkbox: the cover's name, its room and bus address, the profile it has now on a line of its own. A search over name
 * and address, the count of the selection between the search and the list, the refusal right under the list.
 *
 * Home Assistant writes several covers in one go (`myhome/cover_profiles/manage`): `preview_assign` returns the exact
 * proposal and its `confirmation`, which `assign` must carry (`preview_required` otherwise); both are bound to the
 * revision the list was drawn from (`revision_conflict` when another change was saved meanwhile).
 */
const asset = (name) => { const url = new URL(name, import.meta.url); url.search = new URL(import.meta.url).search; return url.href; };
const [{ escapeHtml: esc }, { fill, plural, profileSlats, coverSlats, ownSlats, bySlats, slatsConflict }] = await Promise.all([
  import(asset("panel-dom.js")), import(asset("panel-calibration-text.js")),
]);

/** Home Assistant takes at most 200 covers in one assignment. */
export const ASSIGN_MAX = 200;

/** «Indirizzo: 93 · A: 9 · PL: 3»: each label held to its value (as the list's rows). */
export function addressParts(t, address) {
  if (!address?.raw) return [];
  const fields = [[t("address"), address.raw], ...(address.a != null && address.pl != null ? [["A", address.a], ["PL", address.pl]] : [])];
  return fields.map(([label, value]) => `<span class="cal-row-address-label">${esc(label)}:</span> <span class="cal-row-address-value">${esc(value)}</span>`);
}

/**
 * The line under a cover's name: room (and gateway), then the bus address always whole. It
 * wraps only between units, each pair of the address being one, and the «·» stays with the unit before it.
 */
export function sublineMarkup(t, before, address, after = []) {
  const parts = addressParts(t, address), units = before.filter(Boolean).map(esc), tail = after.filter(Boolean).map(esc);
  // The «·» is held to the word before it, even when a unit wraps between its words.
  const dot = (last) => (last ? "" : " ·");
  const bus = parts.length ? [`<span class="cal-row-address">${parts.map((part, index) => `<span class="cal-row-address-part">${part}${dot(index === parts.length - 1 && !tail.length)}</span>`).join(" ")}</span>`] : [];
  const lead = units.map((unit, index) => `<span class="cal-row-unit">${unit}${dot(index === units.length - 1 && !bus.length && !tail.length)}</span>`);
  return [...lead, ...bus, ...tail.map((unit, index) => `<span class="cal-row-unit">${unit}${dot(index === tail.length - 1)}</span>`)].join(" ");
}

/**
 * Who can be given `profile`: the gateway's timed covers (a cover with hardware position feedback takes no timed profile:
 * not listed), but not those already following it (nothing to do there: left out, not shown ticked). A cover that is the
 * source of another profile is listed, off, its line saying so (unlinking a source is not possible yet, wiz_T430); one
 * Home Assistant refuses (`assignment_reason`: unavailable, busy) is listed off with its reason. `describe(cover)` gives
 * the name, the room and the address the list shows.
 * `sources` lists the source of another profile on, as «Cambia profilo» lets it take another
 * profile (A-35); the other profile keeps its values and the cover it was measured on.
 */
export function assignCandidates({ profile, data, describe, sources = false }) {
  if (!profile || !data) return [];
  const profiles = data.profiles || [];
  return (data.covers || []).filter((cover) => cover.effective && !cover.advanced && cover.profile_id !== profile.id).map((cover) => {
    const info = describe(cover) || {}, address = info.address || null;
    const followed = profiles.find((item) => item.id === cover.profile_id) || null;
    const source = !!followed && followed.provenance?.opening?.origin_entity_id === cover.entity_id, off = source && !sources;
    const reason = off ? null : cover.assignment_reason ?? null;
    const search = [info.name, info.area, cover.entity_id, address?.raw, address?.a == null ? "" : `A:${address.a} A: ${address.a}`,
      address?.pl == null ? "" : `PL:${address.pl} PL: ${address.pl}`].filter(Boolean).join(" ").toLocaleLowerCase();
    return { entity_id: cover.entity_id, name: info.name || cover.name, area: info.area || "", address, slats: coverSlats(cover),
      slats_state: cover.slats_state ?? null, own: ownSlats(cover),
      state: !followed ? { key: "none" } : { key: source ? "source" : "follows", profile: followed.name }, disabled: off || !!reason, reason, search };
  }).sort((a, b) => a.name.localeCompare(b.name));
}

/** The search: every word typed is in the name, the room or the address («A: 9 PL: 1» as well as «A:9»). */
export function matchesQuery(candidate, query = "") {
  const terms = query.trim().toLocaleLowerCase().split(/\s+/).filter(Boolean);
  return terms.every((term) => candidate.search.includes(term));
}

/** The profile a cover has now, as the row's second line: «Nessun profilo», «Segue «X»», «Sorgente di «X»». */
export function stateText(t, state) {
  return state.key === "none" ? t("calTabProfileNone") : fill(t(state.key === "source" ? "wiz_T621" : "wiz_T620"), { profile: state.profile });
}

/** «N selezionate». */
export const selectedText = (t, count) => plural(t, count, "wiz_T616", "wiz_T615");

/** What giving the profile does to the times, in one line (adapted to each travel, or as they are). */
export function scalingText(t, profile) {
  return t(profile?.reference_travel_cm != null ? "wiz_T628" : "wiz_T629");
}

/** A refusal in our words: another change saved meanwhile says the list is up to date; otherwise Home Assistant's code. */
export function errorText(t, error) {
  if (error?.code === "revision_conflict") return t("wiz_T627");
  const key = `profileError_${error?.code}`;
  return t(key) === key ? t("profileError") : t(key);
}

/** The search field: drawn once (typing never redraws it), above the count and the rows. */
export function searchMarkup(t, query = "") {
  return `<label class="cal-assign-search"><span class="cal-assign-search-label">${esc(t("catalogueSearch"))}</span><input type="search" name="assign_filter" value="${esc(query)}" autocomplete="off" spellcheck="false" enterkeyhint="search"></label>`;
}

/**
 * What changes with the choices and the data: the count line, the rows (only those the search keeps; a cover chosen and
 * hidden by the search stays chosen and counted) and the slot of the refusal, the first thing under the list.
 */
export function liveMarkup({ t, candidates = [], selected = [], query = "", error = "", loading = false, readOnly = false, profile = null, moreOpen = false, confirm = "" }) {
  const chosen = new Set(selected), shown = candidates.filter((candidate) => matchesQuery(candidate, query));
  // The covers whose slats fit the profile's (or are not said) first; the others under «Altre tapparelle (N)»,
  // never left out; each row says what is known of its slats.
  const { head, other } = bySlats(shown, profileSlats(profile), (candidate) => candidate.slats);
  const row = (candidate) => {
    const checked = chosen.has(candidate.entity_id) && !candidate.disabled, off = candidate.disabled || readOnly;
    return `<label class="cal-batch-row wiz-pick-row cal-assign-row${checked ? " is-selected" : ""}${candidate.disabled ? " is-disabled" : ""}" data-entity="${esc(candidate.entity_id)}">
      <span class="cal-batch-lead"><input type="checkbox" name="assign_cover" value="${esc(candidate.entity_id)}"${checked ? " checked" : ""}${off ? " disabled" : ""}></span>
      <span class="cal-row-name"><strong>${esc(candidate.name)}</strong>${candidate.area || candidate.address?.raw ? `<span class="muted cal-assign-where">${sublineMarkup(t, [candidate.area], candidate.address)}</span>` : ""}
        <span class="muted cal-assign-state">${esc(stateText(t, candidate.state))}</span>${candidate.slats == null ? "" : `<span class="muted cal-assign-slats">${esc(t(candidate.slats ? "wiz_T368" : "wiz_T369"))}</span>`}${candidate.reason ? `<span class="muted cal-assign-reason">${esc(errorText(t, { code: candidate.reason }))}</span>` : ""}</span></label>`;
  };
  const empty = loading ? t("loading") : candidates.length ? t("noResults") : t("wiz_T626");
  return `<p class="cal-assign-count" aria-live="polite">${esc(selectedText(t, chosen.size))}</p>
    <div class="cal-list cal-batch-list wiz-pick-list cal-assign-list"><div class="cal-batch-rows" role="group" aria-label="${esc(t("catalogueSearch"))}">${head.map(row).join("") || (other.length ? "" : `<p class="muted wiz-pick-empty">${esc(empty)}</p>`)}</div></div>
    ${other.length ? `<details class="cal-disclosure cal-assign-more"${moreOpen || other.some((candidate) => chosen.has(candidate.entity_id)) ? " open" : ""}><summary><span class="cal-disclosure-label">${esc(fill(t("calTabOtherCovers"), { count: other.length }))}</span><ha-icon icon="mdi:chevron-down" aria-hidden="true"></ha-icon></summary>
      <div class="cal-list cal-batch-list wiz-pick-list cal-assign-list"><div class="cal-batch-rows" role="group">${other.map(row).join("")}</div></div></details>` : ""}
    ${confirm ? `<div class="cal-assign-confirm"><p class="cal-travel-confirm">${esc(confirm)}</p><span class="cal-travel-buttons"><button type="button" class="cal-outlined" data-wiz="assign-confirm-cancel" data-assign-ask="cancel">${esc(t("wiz_T025"))}</button><button type="button" class="primary" data-wiz="assign-confirm" data-assign-ask="assign">${esc(t("wiz_T373"))}</button></span></div>` : ""}
    <p class="cal-note is-error cal-assign-error" role="alert"${error ? "" : " hidden"}>${esc(error)}</p>`;
}

/** The question to ask before giving `profile` to the chosen covers ("" when none contradicts it). */
export function selectionConflict(t, candidates, selected, profile) {
  const chosen = new Set(selected);
  return slatsConflict(t, candidates.filter((candidate) => chosen.has(candidate.entity_id)), profile, { named: true });
}

/**
 * The write: the exact proposal first (`preview_assign`), then the assignment with its confirmation, both on the revision
 * the list was drawn from. The proposal is bound to the covers and the revision only; what the list showed is the review.
 */
export async function assignCovers(hass, { entryId, profileId, revision, entityIds }) {
  const base = { type: "myhome/cover_profiles/manage", entry_id: entryId, profile_id: profileId, revision };
  const proposal = await hass.callWS({ ...base, action: "preview_assign", entity_ids: entityIds });
  return hass.callWS({ ...base, action: "assign", entity_ids: entityIds, confirmation: proposal.confirmation });
}

/** The selection kept through a new reading of the data: only the covers still listed and free. */
export function keptSelection(selected, candidates) {
  const free = new Set(candidates.filter((candidate) => !candidate.disabled).map((candidate) => candidate.entity_id));
  return selected.filter((id) => free.has(id));
}
