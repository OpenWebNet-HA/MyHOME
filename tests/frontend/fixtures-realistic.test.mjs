/** The realistic house used by the browser checks: the Calibration tab reads it as Home Assistant would send it. */
import assert from "node:assert/strict";
import { after, afterEach, test } from "node:test";

import { mount, reset, tick, dom } from "./wizard-harness.mjs";
import { realisticFixture } from "./fixtures-realistic.mjs";

afterEach(reset);
after(() => dom.window.close());

const rows = (tab) => [...tab().querySelectorAll(".cal-row")];
const badge = (tab, entity) => rows(tab).find((row) => row.dataset.entity === entity).querySelector(".cal-badge").dataset.status;

test("the realistic house lists every timed cover with its state, never the awning with a position sensor", async () => {
  const { tab } = await mount(realisticFixture());
  await tick(8);
  assert.equal(rows(tab).length, 10);
  assert.equal(rows(tab).some((row) => row.dataset.entity === "cover.patio_awning"), false);
  assert.deepEqual(["cover.lounge_1", "cover.bedroom_garden", "cover.kitchen", "cover.guest_room", "cover.study", "cover.bathroom"].map((entity) => badge(tab, entity)),
    ["verified", "verified", "calibrated", "calibrated", "to_measure", "to_measure"]);
  // L-73: the gap in the label, green within the 4 cm threshold and yellow beyond; a follower of a checked cover has none.
  const chip = (entity) => rows(tab).find((row) => row.dataset.entity === entity).querySelector(".cal-badge");
  assert.deepEqual(["cover.lounge_1", "cover.bedroom_garden"].map((entity) => [chip(entity).textContent, chip(entity).dataset.tone]),
    [["Verificata · 2 cm", "success"], ["Verificata · 6 cm", "warning"]]);
  assert.equal(badge(tab, "cover.lounge_2"), "calibrated", "it follows a verified cover's profile: no check of its own");
  assert.equal(chip("cover.kitchen").dataset.tone, undefined);
  const counts = Object.fromEntries([...tab().querySelectorAll('[data-action="cal-tab-filter"]')].map((chip) => [chip.dataset.id, chip.querySelector(".count")?.textContent]));
  assert.deepEqual([counts.to_measure, counts.calibrated], ["3", "7"], "«Calibrate» counts calibrated and verified, «Da calibrare» as before");
  assert.match(rows(tab).find((row) => row.dataset.entity === "cover.bedroom_garden").textContent, /Main Bedroom Shutter Garden Side/);
});

test("a session open in another tab puts its cover on top, in progress, and holds every new measurement", async () => {
  const { tab } = await mount(realisticFixture({ session: "running" }));
  await tick(8);
  const busy = tab().querySelector(".cal-tab-busy");
  assert.ok(busy && !busy.querySelector("ha-icon"), "L-80: the note «a calibration is in progress» has no icon");
  const first = rows(tab)[0];
  assert.equal(first.dataset.entity, "cover.bathroom");
  assert.equal(first.querySelector(".cal-badge").dataset.status, "running");
  assert.ok(first.querySelector('[data-action="cal-tab-session"]'));
  assert.equal(tab().querySelector('[data-action="cal-tab-measure"]').disabled, true);
});

test("a paused automatic cycle shows as paused on its cover", async () => {
  const { tab } = await mount(realisticFixture({ session: "paused" }));
  await tick(8);
  const first = rows(tab)[0];
  assert.equal(first.dataset.entity, "cover.hallway");
  assert.match(first.querySelector(".cal-badge").textContent, /pausa|paused/i);
});
