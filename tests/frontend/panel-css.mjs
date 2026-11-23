import { readFileSync } from "node:fs";

/**
 * The panel's styles as the browser applies them: the shell's stylesheet, then the calibration's (two <link>s in that
 * order), one text so the tests read the rules, their order and their cascade as in a single stylesheet.
 */
const read = (name) => readFileSync(new URL(`../../custom_components/myhome/frontend/panel/${name}`, import.meta.url), "utf8");
export const PANEL_CSS_FILES = ["myhome-panel.css", "panel-calibration.css"];
export const panelCss = PANEL_CSS_FILES.map(read).join("");
