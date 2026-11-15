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
 * D-23/D-36: in escaped text a number and its unit («109,1 cm», «30 s», «600 secondi») are held on one line by a
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

/** A date in the browser's time zone; unknown or invalid dates are left to the caller. */
export function dateText(value, language = "en") {
  if (!value || Number.isNaN(Date.parse(value))) return "";
  try { return new Intl.DateTimeFormat(language, { dateStyle: "medium" }).format(new Date(value)); }
  catch { return new Date(value).toISOString().slice(0, 10); }
}

/** L-33: the date with its time («9 ott 2026 16:23»), for the details; the date alone when no time is known. */
export function dateTimeText(value, language = "en") {
  if (!value || Number.isNaN(Date.parse(value))) return "";
  if (!/T\d{2}:\d{2}/.test(String(value))) return dateText(value, language);
  try {
    const date = new Date(value);
    return `${new Intl.DateTimeFormat(language, { dateStyle: "medium" }).format(date)} ${new Intl.DateTimeFormat(language, { hour: "2-digit", minute: "2-digit", hourCycle: "h23" }).format(date)}`;
  } catch { return new Date(value).toISOString().slice(0, 16).replace("T", " "); }
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
export function followedBy(t, list, language = "en") {
  if (!list.length) return t("wiz_T374");
  const shown = list.length > 3 ? fill(t("wiz_T429"), { names: list.slice(0, 3).join(", "), count: list.length - 3 }) : names(list, language);
  return fill(t("wiz_T428"), { names: shown });
}

export function names(list, language = "en") {
  // D-41: a name's closing number stays with it («Lounge Shutter 1» never ends a line on «Lounge»).
  const kept = list.map((name) => String(name).replace(/ (\d+)$/, "\u00a0$1"));
  try { return new Intl.ListFormat(language, { style: "long", type: "conjunction" }).format(kept); }
  catch { return kept.join(", "); }
}
