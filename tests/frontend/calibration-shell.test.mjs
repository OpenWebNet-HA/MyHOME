/**
 * The Calibration tab inside the panel: opening a cover's card from the rest of the panel.
 */
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { after, afterEach, describe, test } from "node:test";

import { sampleOverview } from "./fake-calibration.mjs";
import { dom, mount, reset, tick } from "./wizard-harness.mjs";

afterEach(reset);
after(() => dom.window.close());

describe("opening a cover's card", async () => {
  const MEASURED_AT = "2026-11-01T16:09:00+00:00";
  const measured = { source: "guided", recorded_at: MEASURED_AT, origin_entity_id: null, origin_name: null, inherited: false };
  const timing = (value, origin = "profile", scaled = false) => ({ value, origin, provenance: measured, scaled });
  /** «tall» measured on Living room shutter 1 (its source), used by the three covers (listed out of order). */
  function overview() {
    const data = sampleOverview();
    const profile = data.profiles[0];
    profile.assigned_to = ["cover.living_three", "cover.kitchen_two", "cover.living_one"];
    profile.provenance.opening.recorded_at = MEASURED_AT;
    const [one] = data.covers;
    Object.assign(one, { profile_id: "tall", travel_cm: 198, precision: "basic", effective: { opening: timing(22.4), closing: timing(21.9) } });
    return data;
  }
  async function close(context) { context.tab().querySelector(".cal-page-close").click(); await tick(6); }
  const cardOf = (context) => context.tab().querySelector(".cal-flow .cal-page:not(.cal-assign-page)");

  test("requestCover opens the cover's card as a row's tap does; returnTo is called once when it closes back to the list", async () => {
    const context = await mount({ overview: overview(), language: "it" });
    const tab = context.panel._calibrationTab;
    let back = 0;
    assert.equal(tab.requestCover("one", "cover.kitchen_two", { returnTo: () => { back += 1; } }), true);
    await tick(6);
    assert.equal(cardOf(context)?.dataset.entity, "cover.kitchen_two");
    assert.equal(cardOf(context).querySelector("#cal-page-name").textContent, "Kitchen shutter 2");
    // Refused while a card is open: never queued.
    assert.equal(tab.requestCover("one", "cover.living_one"), false);
    // «Cambia profilo» opened from it and closed back to the card: not yet back.
    cardOf(context).querySelector('.cal-page-link [data-action="cal-tab-assign"]').click(); await tick(6);
    context.tab().querySelector('[data-action="cal-tab-assign-cancel"]').click(); await tick(6);
    assert.equal(back, 0);
    await close(context); await tick(2);
    assert.equal(cardOf(context), null);
    assert.equal(back, 1);
    context.tab().querySelector(`.cal-row[data-entity="cover.living_one"] [data-action="cal-tab-toggle"]`).click(); await tick(6);
    await close(context); await tick(2);
    assert.equal(back, 1, "once: a card opened afterwards from the list does not call it again");
  });

  test("a flow opened from the card calls returnTo when it closes to the list; an unknown cover opens nothing; a wizard open refuses the request", async () => {
    const context = await mount({ overview: overview(), language: "it" });
    const tab = context.panel._calibrationTab;
    let back = 0;
    tab.requestCover("one", "cover.living_one", { returnTo: () => { back += 1; } }); await tick(6);
    cardOf(context).querySelector('[data-action="cal-tab-refine"]').click(); await tick(6);
    assert.ok(context.wiz(), "Perfeziona replaces the card");
    assert.equal(tab.requestCover("one", "cover.kitchen_two"), false, "refused while the wizard is open");
    tab._wizard._act("close"); await tick(8);
    assert.equal(context.wiz(), null);
    assert.equal(back, 1);
    let never = 0;
    assert.equal(tab.requestCover("one", "cover.unknown", { returnTo: () => { never += 1; } }), true);
    await tick(6);
    assert.equal(cardOf(context), null);
    assert.equal(never, 0);
    assert.equal(tab._pendingCover, null, "forgotten");
  });

  test("the shell's twin of _openCalibrationWizard opens the Calibration tab on the cover's card; no control of Profiles or Devices is wired to it", async () => {
    const context = await mount({ overview: overview(), language: "it" });
    const { panel, root } = context;
    root.querySelector('[data-action="cover-view"][data-id="devices"]').click(); await tick(8);
    assert.equal(context.tab(), null, "on Devices");
    const entity = panel._data.entities.find((item) => item.entity_id === "cover.living_three");
    let back = 0;
    assert.equal(panel._openCalibrationCover(entity, { returnTo: () => { back += 1; } }), true);
    await tick(8);
    assert.equal(panel._coverView, "calibration");
    assert.equal(cardOf(context)?.dataset.entity, "cover.living_three");
    await close(context); await tick(2);
    assert.equal(back, 1);
    const source = (name) => readFileSync(new URL(`../../custom_components/myhome/frontend/panel/${name}`, import.meta.url), "utf8");
    assert.equal(source("myhome-panel.js").match(/_openCalibrationCover\(/g).length, 1, "defined, called by nothing");
    for (const name of ["panel-cover-profile-list.js", "panel-cover-profiles.js", "panel-profile-catalogue-editor.js"]) assert.doesNotMatch(source(name), /requestCover|_openCalibrationCover/);
    assert.ok(readFileSync(new URL("../../docs/panel-calibration-open-cover.md", import.meta.url), "utf8").includes("requestCover(entryId, entityId, { returnTo } = {})"));
  });
});
