/**
 * The drawings of the calibration wizard (A-05, A-06, A-07…), one per illustration id.
 *
 * The screens pick an id per mockup code (`screen.illustration`); this module is the only place that
 * knows what an id looks like: an inline SVG from `panel-calibration-illustrations-svg.js` (generated from
 * the Figma exports), drawn at the Figma size (1×) and shrinking with the column on a phone. Its colours are
 * the `--wiz-ill-*` properties of the wizard, light or dark with the Home Assistant theme.
 * Never a picture of the live position: the same drawing for every cover and every moment of the step.
 */
const asset = (name) => { const url = new URL(name, import.meta.url); url.search = new URL(import.meta.url).search; return url.href; };
const { SVG } = await import(asset("panel-calibration-illustrations-svg.js"));

/** Width and height in px of each drawing in the Figma frames, rounded. */
export const ILLUSTRATIONS = {
  "window-closed": { width: 259, height: 250 },
  "two-runs": { width: 560, height: 346 },
  "lift-sequence": { width: 560, height: 160 },
  "descent-sequence": { width: 560, height: 142 },
  "ascent-sequence": { width: 560, height: 142 },
  "lift-zoom": { width: 289, height: 222 },
  "curtain-open-tape": { width: 207, height: 200 },
  "curtain-partial-tape": { width: 207, height: 200 },
  check: { width: 208, height: 200 },
  "lift-sequence-mobile": { width: 358, height: 228 },
  // Z-14 (Figma, 9 Oct): the edge at a quarter, half and three quarters with the tape (the ascent's quarter apart), and
  // the ascent and descent of a cover without slats.
  "curtain-tape-quarter": { width: 207, height: 200 },
  "curtain-tape-half": { width: 207, height: 200 },
  "curtain-tape-three-quarters": { width: 207, height: 200 },
  "curtain-tape-quarter-ascent": { width: 207, height: 200 },
  "ascent-sequence-no-slats": { width: 560, height: 142 },
  "descent-sequence-no-slats": { width: 560, height: 142 },
};

/** Drawings with a version of their own for phones (under 720 px, A-07 M): only the lift-off sequence. */
export const PHONE_VERSION = { "lift-sequence": "lift-sequence-mobile" };

export { SVG };

/** The drawing of one illustration id, described as a whole for assistive technology. */
export function illustrationMarkup(id, label, escape) {
  const one = (name, extra = "") => {
    const size = ILLUSTRATIONS[name], svg = SVG[name];
    if (!size || !svg) return "";
    // The ratio is set too: with only width and height attributes some browsers (Safari) keep the full height when the
    // column is narrower than the drawing, and it overlaps the text under it.
    return svg.replace(/^<svg /, `<svg class="wiz-illustration${extra}" data-illustration="${escape(name)}" role="img" aria-label="${escape(label)}" width="${size.width}" height="${size.height}" style="width: min(100%, ${size.width}px); aspect-ratio: ${size.width} / ${size.height}" focusable="false" `);
  };
  // A drawing with a phone version: both are in the page and the stylesheet shows one per width (a resize needs no redraw).
  const phone = PHONE_VERSION[id];
  return phone && SVG[phone] ? one(id, " wiz-ill-wide") + one(phone, " wiz-ill-phone") : one(id);
}
