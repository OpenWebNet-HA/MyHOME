/**
 * The calibration files explain themselves: no names of people, of work batches or test rounds, no codes of design
 * boards or review notes, and no rooms of a real house in their comments, test titles or data. The wizard's own screen
 * codes (A-01, B-02, C-35, S-06: the `code` of each screen) are names the code uses, so they stay. The list is written
 * here on purpose: a new file joins it by hand.
 */
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { test } from "node:test";

const ROOT = new URL("../../", import.meta.url);
const INTERNAL = /\bAlberto\b|\bx?timmy\b|\bfett[ae]\b|\bslice ?\d|\bgiro\b|\blott[oi]\b|collaudo|esplorator|Taverna/i;
/** A code of a board or a note («T-09», «L-110», «R5-14», «B1-7»), not one of the wizard's screens. */
const CODE = /\b(?![ABCS]-\d{2}[a-z]?\b)[A-Z][A-Z0-9]?-\d+[a-z]?\b/;

const FILES = [
  "custom_components/myhome/cover_calibration_check.py",
  "custom_components/myhome/cover_calibration_save.py",
  "custom_components/myhome/cover_geometry.py",
  "custom_components/myhome/cover_profile_export.py",
  "custom_components/myhome/cover_profile_mutations.py",
  "custom_components/myhome/cover_profiles.py",
  "custom_components/myhome/cover_settings.py",
  "custom_components/myhome/cover_settings_api.py",
  "custom_components/myhome/frontend/panel/panel-calibration-gateway.js",
  "custom_components/myhome/frontend/panel/panel-calibration-illustration.js",
  "custom_components/myhome/frontend/panel/panel-calibration-layout.js",
  "custom_components/myhome/frontend/panel/panel-calibration-morph.js",
  "custom_components/myhome/frontend/panel/panel-calibration-screens.js",
  "custom_components/myhome/frontend/panel/panel-calibration-tab.js",
  "custom_components/myhome/frontend/panel/panel-calibration-text.js",
  "custom_components/myhome/frontend/panel/panel-calibration-wizard.js",
  "custom_components/myhome/frontend/panel/panel-calibration.css",
  "custom_components/myhome/frontend/panel/panel-profile-assign.js",
  "custom_components/myhome/frontend/panel/panel-translations.js",
  "docs/cover-travel-scaling.md",
  "docs/panel-calibration-open-cover.md",
  "tests/frontend/calibration-bars.test.mjs",
  "tests/frontend/calibration-check-procedure.test.mjs",
  "tests/frontend/calibration-cover-details.test.mjs",
  "tests/frontend/calibration-cover-overview.test.mjs",
  "tests/frontend/calibration-dialog.test.mjs",
  "tests/frontend/calibration-list.test.mjs",
  "tests/frontend/calibration-overlays.test.mjs",
  "tests/frontend/calibration-path-b.test.mjs",
  "tests/frontend/calibration-path-c.test.mjs",
  "tests/frontend/calibration-profile-sheet.test.mjs",
  "tests/frontend/calibration-shell.test.mjs",
  "tests/frontend/calibration-tab.test.mjs",
  "tests/frontend/calibration-texts.test.mjs",
  "tests/frontend/calibration-theme.test.mjs",
  "tests/frontend/calibration-wizard-base.test.mjs",
  "tests/frontend/calibration-wizard-path.test.mjs",
  "tests/frontend/calibration-wizard-refine.test.mjs",
  "tests/frontend/calibration-wizard-screens.test.mjs",
  "tests/frontend/calibration-wizard.test.mjs",
  "tests/frontend/css-cascade.mjs",
  "tests/frontend/fake-calibration.mjs",
  "tests/frontend/fixtures-realistic.mjs",
  "tests/frontend/fixtures-realistic.test.mjs",
  "tests/frontend/panel-css.mjs",
  "tests/frontend/profiles-world.mjs",
  "tests/frontend/wizard-harness.mjs",
  "tests/test_cover_calibration_base.py",
  "tests/test_cover_calibration_batch.py",
  "tests/test_cover_calibration_bus_stop.py",
  "tests/test_cover_calibration_check_store.py",
  "tests/test_cover_calibration_live_refresh.py",
  "tests/test_cover_calibration_direction.py",
  "tests/test_cover_calibration_refine.py",
  "tests/test_cover_calibration_save.py",
  "tests/test_cover_calibration_travel.py",
  "tests/test_cover_manual_travel.py",
  "tests/test_cover_precision_class.py",
  "tests/test_cover_slats_mark.py",
  "tests/test_gateway_send_compat.py",
];

for (const file of FILES) {
  test(`${file} has no internal references`, () => {
    const found = readFileSync(new URL(file, ROOT), "utf8").split("\n").map((line, index) => [index + 1, line]).filter(([, line]) => INTERNAL.test(line) || CODE.test(line));
    assert.deepEqual(found.map(([number, line]) => `${number}: ${line.trim().slice(0, 120)}`), []);
  });
}
