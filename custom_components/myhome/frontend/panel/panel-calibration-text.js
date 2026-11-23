/** Text helpers shared by the Calibration tab and the calibration wizard. Strings never carry HTML. */
const url = new URL("panel-dom.js", import.meta.url);
url.search = new URL(import.meta.url).search;
const { escapeHtml: esc } = await import(url.href);

/** `{name}` placeholders from `values`; a missing value keeps the placeholder visible to tests. */
export function fill(text, values = {}) {
  return String(text).replace(/\{(\w+)\}/g, (match, key) => values[key] == null ? match : String(values[key]));
}

/** Escaped text with `**bold**` spans and blank-line paragraphs: the only markup the texts use. */
export function rich(text, values) {
  const paragraphs = fill(text, values).split(/\n\s*\n/).map((part) => part.trim()).filter(Boolean);
  return paragraphs.map((part) => `<p>${inline(part)}</p>`).join("");
}

/** One paragraph, without the wrapping `<p>`: line breaks become `<br>`. */
export function inline(text, values) {
  const escaped = esc(fill(text, values));
  // Spaces or a line break just inside the markers (`**text. **Next`, `**text.\n**`) move outside the bold run.
  return escaped.replace(/\*\*([\s\S]+?)\*\*/g, (match, inner) => {
    const lead = /^\s/.test(inner) ? " " : "", trail = /\n\s*$/.test(inner) ? "\n" : /\s$/.test(inner) ? " " : "";
    return inner.trim() ? `${lead}<strong>${inner.trim()}</strong>${trail}` : `${lead}${trail}`;
  }).replace(/\*\*/g, "").replace(/\n/g, "<br>").replace(UNIT, UNIT_HTML);
}

/**
 * In escaped text a number and its unit («109,1 cm», «30 s», «600 secondi») are held on one line by a
 * `nowrap` span (the text itself, and so what is read, is unchanged).
 */
const UNIT = /(\d+(?:[.,]\d+)?) (s|cm|secondi|seconds|%)(?![\p{L}\d])/gu, UNIT_HTML = '<span class="cal-nb">$1 $2</span>';
export function units(html) { return String(html).replace(UNIT, UNIT_HTML); }

/** A live value with a fixed number of decimals ("12.0 s", never "12 s"), so it never changes length mid-run. */
export function fixed(value, language = "en", digits = 1) {
  if (value == null || !Number.isFinite(Number(value))) return "—";
  try {
    return new Intl.NumberFormat(language, { maximumFractionDigits: digits, minimumFractionDigits: digits, useGrouping: false }).format(Number(value));
  } catch {
    return Number(value).toFixed(digits);
  }
}

export function number(value, language = "en", digits = 1) {
  if (value == null || !Number.isFinite(Number(value))) return "—";
  try {
    return new Intl.NumberFormat(language, { maximumFractionDigits: digits, minimumFractionDigits: 0, useGrouping: false }).format(Number(value));
  } catch {
    return `${Number(Number(value).toFixed(digits))}`;
  }
}

/**
 * A value with at least `min` decimals and at most `max` («3,0», «1,0», «1,05»): the details' tables in
 * the format of «Caratteristiche», a whole number never shorter than its neighbours.
 */
export function decimals(value, language = "en", min = 1, max = min) {
  if (value == null || !Number.isFinite(Number(value))) return "—";
  try {
    return new Intl.NumberFormat(language, { minimumFractionDigits: min, maximumFractionDigits: max, useGrouping: false }).format(Number(value));
  } catch {
    const shown = String(Number(Number(value).toFixed(max)));
    const [whole, part = ""] = shown.split(".");
    return `${whole}.${part.padEnd(min, "0")}`.replace(/\.$/, "");
  }
}

/**
 * Perfeziona's icon, in every place that means Perfeziona (its button, the row's ⋮, the details, the wizard's A-34):
 * `mdi:arrow-expand-vertical`, «as far as it goes», upright, thicker than the straight ruler (`mdi:ruler`) it replaced (a
 * tape measure read as a magnifying glass at 18 px).
 * `iconMarkup` draws any icon name.
 */
export const REFINE_ICON = "mdi:arrow-expand-vertical";
export function iconMarkup(name) {
  return `<ha-icon${name === REFINE_ICON ? ' class="cal-refine-icon"' : ""} icon="${String(name).replace(/[&<>"']/g, "")}" aria-hidden="true"></ha-icon>`;
}

/**
 * The one place for a check's gap. Its true value is the gap to the tenth, as Home Assistant keeps it
 * (the backend now rounds the deviation to 0.1 cm and passes it up to 4.0 cm included): the outcome and the tone come
 * from that value, never from the rounded number shown.
 */
export const CHECK_PASS_CM = 4;
export function gapTenth(gap) {
  const value = Math.abs(Number(gap));
  return gap == null || !Number.isFinite(value) ? null : Math.round(value * 10) / 10;
}

/** Within the threshold (4.0 cm included): green; beyond (4.1 and up): yellow. */
export function gapPasses(gap, threshold = CHECK_PASS_CM) {
  const tenth = gapTenth(gap);
  return tenth != null && tenth <= threshold;
}

/**
 * A cover's role towards its profile, the rule of the whole panel: `none` (never calibrated), `alone` (no profile),
 * `source` (the profile's `provenance.opening.origin_entity_id` is this cover) or `follower` (any other profile).
 * `status` is the tab's (`to_measure` for never calibrated), `entityId` the cover's own.
 */
export function coverRole(status, profile, entityId) {
  if (status === "to_measure") return "none";
  if (!profile) return "alone";
  return profile.provenance?.opening?.origin_entity_id === entityId ? "source" : "follower";
}

/**
 * THE advised step of a cover, one rule for the list's row (`_rowActions`) and A-02 (`pathAdvice`), so
 * they never part. `shown` is the list's action in view: `calibrate` (never calibrated), `refine` (Perfeziona),
 * `remeasure` (Ricalibra) or null (nothing to advise: the row has no button). `path` is A-02's advised and
 * preselected option: `R` (fine-tuning with the tape, the same procedure as Perfeziona) on the profile's source and on a
 * cover without a profile, also where the list advises nothing (a check within the threshold: A-02 always advises one
 * option); `base` (Ricalibra) where the list advises Ricalibra and on a cover that follows a profile.
 */
export function advisedStep({ status, role, check }, threshold = CHECK_PASS_CM) {
  if (status === "to_measure" || role === "none") return { shown: "calibrate", path: "base" };
  const beyond = status === "verified" && !gapPasses(check, threshold);
  if (role !== "source" && role !== "alone") return { shown: beyond ? "remeasure" : null, path: "base" };
  return { shown: status === "verified" && !beyond ? null : "refine", path: "R" };
}

/**
 * The gap as it is shown everywhere (the chips, A-21's «Misura», the stepper, the precision sentences): one decimal below
 * a centimetre, and between 4 and 5 cm (4,3 is beyond the threshold, «4» would contradict it); whole otherwise.
 */
export function shownGap(gap) {
  const tenth = gapTenth(gap);
  if (tenth == null) return null;
  return tenth < 1 || (tenth > CHECK_PASS_CM && tenth < CHECK_PASS_CM + 1) ? tenth : Math.round(tenth);
}

/** The shown gap formatted in the language (a decimal comma in Italian): «0,3», «2». */
export function gapText(gap, language = "en") {
  const value = shownGap(gap);
  return value == null ? "—" : Number.isInteger(value) ? number(value, language, 0) : fixed(value, language, 1);
}

/**
 * A saved check's gap: |measured − expected| when the record has both (the stored `check_cm` was a whole number: a
 * 0.3 cm gap was kept as 0), otherwise `check_cm` (whole today, with a decimal once the backend keeps one).
 */
export function savedGap(record) {
  const measured = Number(record?.check_measured_cm), expected = Number(record?.check_expected_cm);
  if (record?.check_measured_cm != null && record?.check_expected_cm != null && Number.isFinite(measured) && Number.isFinite(expected)) return Math.abs(measured - expected);
  const stored = Number(record?.check_cm);
  return record?.check_cm != null && Number.isFinite(stored) ? Math.abs(stored) : null;
}

/** A date in the browser's time zone; unknown or invalid dates are left to the caller. */
export function dateText(value, language = "en") {
  if (!value || Number.isNaN(Date.parse(value))) return "";
  try { return new Intl.DateTimeFormat(language, { dateStyle: "medium" }).format(new Date(value)); }
  catch { return new Date(value).toISOString().slice(0, 10); }
}

/** The date with its time («9 ott 2026 16:23»), for the details; the date alone when no time is known. */
export function dateTimeText(value, language = "en") {
  if (!value || Number.isNaN(Date.parse(value))) return "";
  if (!/T\d{2}:\d{2}/.test(String(value))) return dateText(value, language);
  try {
    const date = new Date(value);
    return `${new Intl.DateTimeFormat(language, { dateStyle: "medium" }).format(date)} ${timeText(value, language)}`;
  } catch { return new Date(value).toISOString().slice(0, 16).replace("T", " "); }
}

/** The time alone («12:18»), "" when the value has none (the list's phone line drops it when it does not fit). */
export function timeText(value, language = "en") {
  if (!value || Number.isNaN(Date.parse(value)) || !/T\d{2}:\d{2}/.test(String(value))) return "";
  try { return new Intl.DateTimeFormat(language, { hour: "2-digit", minute: "2-digit", hourCycle: "h23" }).format(new Date(value)); }
  catch { return new Date(value).toISOString().slice(11, 16); }
}

/** Day and short month ("29 set", "Sep 29"), for compact labels. */
export function shortDate(value, language = "en") {
  if (!value || Number.isNaN(Date.parse(value))) return "";
  try { return new Intl.DateTimeFormat(language, { day: "numeric", month: "short" }).format(new Date(value)); }
  catch { return new Date(value).toISOString().slice(5, 10); }
}

/** The backend accepts readings this close to the ends of their range (floating point only). */
export const RANGE_SLACK_CM = 1e-9;

/**
 * The accepted heights of an intermediate reading (`reading_range` of the session view), shown inside their
 * true ends a tenth of a centimetre at a time so that every value shown is accepted; a narrower range is
 * shown to hundredths, and one narrower than that with its exact ends (as the profile dialog shows it).
 */
export function readingRange(span, language = "en", reading = null) {
  if (!span) return null;
  const inside = (scale) => [Math.ceil(span.min_cm * scale) / scale, Math.floor(span.max_cm * scale) / scale];
  let [low, high] = inside(10), digits = 1;
  if (low >= high) { [low, high] = inside(100); digits = 2; }
  if (low > high) { low = span.min_cm; high = span.max_cm; digits = 20; }
  return { min_cm: number(low, language, digits), max_cm: number(high, language, digits),
    ...(reading == null ? {} : { reading_cm: number(reading, language, 3) }) };
}

/** "Followed by X and Y" / "… X, Y, Z and 2 more" / "No cover follows it": who follows a profile, by name. */
export function followedBy(t, list, language = "en", here = false) {
  if (!list.length) return t(here ? "wiz_T428_self" : "wiz_T374");
  const shown = list.length > 3 ? fill(t("wiz_T429"), { names: list.slice(0, 3).join(", "), count: list.length - 3 }) : names(list, language);
  // The names said as covers, «dalla tapparella X» or «dalle tapparelle X e Y».
  // `here`, the cover whose sheet this is follows it too: «Seguito da questa tapparella» first, never its name.
  const key = list.length === 1 ? "wiz_T428_one" : "wiz_T428";
  return fill(t(here ? `${key}_here` : key), { names: shown });
}

/** A count said in its own form, the singular as such («1 selezionata», «Assegna a 1 tapparella»). */
export function plural(t, count, one, many, values = {}) {
  return fill(t(count === 1 ? one : many), { ...values, count });
}

export function names(list, language = "en") {
  // A name's closing number stays with it («Lounge Shutter 1» never ends a line on «Lounge»).
  const kept = list.map((name) => String(name).replace(/ (\d+)$/, "\u00a0$1"));
  try { return new Intl.ListFormat(language, { style: "long", type: "conjunction" }).format(kept); }
  catch { return kept.join(", "); }
}

/**
 * Whether a profile's cover has slats: its mark (`slats`) when it has one, else what its geometry says
 * (a slat phase measured, or 0 s measured without slats); null when nobody said (an old times-only profile).
 */
export function profileSlats(profile) {
  if (typeof profile?.slats === "boolean") return profile.slats;
  return profile?.geometry ? profile.geometry.slat_time_s > 0 : null;
}

/**
 * Whether a cover has slats, from Home Assistant's `slats_state` (null: not said): measured
 * here, inherited from its profile or declared on its card.
 */
export function coverSlats(cover) {
  const state = cover?.slats_state;
  return ["yes_measured", "yes_profile", "yes"].includes(state) ? true : ["no_measured", "no_profile", "no"].includes(state) ? false : null;
}

/** What a cover says of its own slats: measured on it, or declared on its card (without a profile). */
export function ownSlats(cover) {
  const state = cover?.slats_state;
  return ["yes_measured", "yes"].includes(state) ? true : ["no_measured", "no"].includes(state) ? false : null;
}

/**
 * The question asked before giving a profile whose slats contradict what covers say of their own
 * (`ownSlats`): «Questa tapparella non ha le lamelle, il profilo sì: … Assegna comunque?» and the reverse; for one cover
 * of several named, for more counted. "" when nothing contradicts it (or the profile does not say).
 */
export function slatsConflict(t, covers, profile, { named = false } = {}) {
  const has = profileSlats(profile);
  if (has == null) return "";
  const against = covers.filter((cover) => { const own = ownSlats(cover); return own != null && own !== has; });
  if (!against.length) return "";
  const key = has ? "calAssignSlatsNoYes" : "calAssignSlatsYesNo";
  if (against.length > 1) return fill(t(`${key}Many`), { count: against.length });
  return named ? fill(t(`${key}Named`), { cover: against[0].name ?? "" }) : t(key);
}

/**
 * A list split by slats: what fits (the same answer, or nobody said) first, the rest apart; nothing is left
 * out. Without an answer to compare with, one list.
 */
export function bySlats(items, wanted, slatsOf) {
  if (wanted == null) return { head: items, other: [] };
  const other = items.filter((item) => { const value = slatsOf(item); return value != null && value !== wanted; });
  return { head: items.filter((item) => !other.includes(item)), other };
}
