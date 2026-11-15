/**
 * The panel's CSS cascade resolved by hand (jsdom computes no styles), for the layout tests: the stylesheets without
 * comments, the weight of a selector, a selector list split at its own commas, the declaration that wins for an element
 * (highest weight, then the later rule; @media blocks are not evaluated) and an @media block whole. The same rules as
 * the helpers of calibration-wizard.test.mjs.
 */
import { panelCss } from "./panel-css.mjs";

export const cascadeCss = panelCss.replace(/\/\*[\s\S]*?\*\//g, "");

export const topLevel = (list) => {
  const parts = []; let depth = 0, start = 0;
  for (let i = 0; i < list.length; i++) {
    if ("([".includes(list[i])) depth += 1; else if (")]".includes(list[i])) depth -= 1;
    else if (list[i] === "," && depth === 0) { parts.push(list.slice(start, i)); start = i + 1; }
  }
  return [...parts, list.slice(start)].map((part) => part.trim());
};

export const weight = (selector) => {
  let flat = selector, inner;
  const pattern = /:(is|not|where)\(([^()]*)\)/;
  while ((inner = pattern.exec(flat))) {
    const heaviest = inner[1] === "where" ? "" : topLevel(inner[2]).reduce((best, part) => (weight(part) > weight(best) ? part : best), "");
    flat = flat.slice(0, inner.index) + (heaviest ? ` ${heaviest.replace(/^[\s>+~]+/, "")}` : "") + flat.slice(inner.index + inner[0].length);
  }
  const ids = (flat.match(/#[\w-]+/g) || []).length;
  const classes = (flat.match(/\.[\w-]+|\[[^\]]+\]|:(?!:)[\w-]+/g) || []).length;
  const types = (flat.replace(/\[[^\]]+\]|[.#:][\w-]+/g, " ").match(/[a-z][\w-]*/gi) || []).length;
  return ids * 10000 + classes * 100 + types;
};

export const winner = (element, property, css = cascadeCss) => {
  let best = null, order = 0;
  for (const [, selectors, body] of css.matchAll(/(?<=^|[{}])\s*([^{}@]+?)\s*\{([^{}]*)\}/g)) {
    order += 1;
    const value = body.match(new RegExp(`(?:^|;)\\s*${property.replace(/[-]/g, "\\-")}\\s*:\\s*([^;]+)`))?.[1]?.trim();
    if (!value) continue;
    for (const selector of topLevel(selectors)) {
      let hit = false; try { hit = element.matches(selector); } catch { /* selectors jsdom does not know */ }
      const w = weight(selector);
      if (hit && (!best || w > best.w || (w === best.w && order >= best.order))) best = { w, order, value, selector };
    }
  }
  return best?.value ?? "";
};

/** The first @media (or other at-rule) block whose head matches, whole (balanced braces). */
export function block(head, css = cascadeCss) {
  const start = typeof head === "string" ? css.indexOf(head) : css.search(head);
  if (start < 0) return "";
  let depth = 0;
  for (let i = css.indexOf("{", start); i < css.length; i++) {
    if (css[i] === "{") depth += 1; else if (css[i] === "}" && (depth -= 1) === 0) return css.slice(start, i + 1);
  }
  return "";
}

/** The bodies of the rules of `css` whose selector list holds `selector`, joined. */
export const rule = (css, selector) => [...css.matchAll(/(?<=^|[{}])\s*([^{}@]+?)\s*\{([^{}]*)\}/g)]
  .filter(([, selectors]) => topLevel(selectors).includes(selector)).map(([, , body]) => body.replace(/\s+/g, " ").trim()).join(" | ");
