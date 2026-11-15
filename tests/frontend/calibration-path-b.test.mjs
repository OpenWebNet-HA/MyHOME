import assert from "node:assert/strict";
import { after, afterEach, test } from "node:test";

import { translations } from "../../custom_components/myhome/frontend/panel/panel-translations.js";
import { sampleOverview } from "./fake-calibration.mjs";
import { dom, mount, reset, settle, tick, waitFor } from "./wizard-harness.mjs";

afterEach(reset);
after(() => dom.window.close());

/** Review P3: a cover with no travel has no closest profile: B-01 preselects none, the profile is ticked by hand. */
const tickProfile = async (wiz, value = "tall") => {
  const radio = wiz().querySelector(`input[name="profile"][value="${value}"]`);
  radio.checked = true; radio.dispatchEvent(new Event("change", { bubbles: true })); await tick();
};

const calibrationTraffic = (backend) => [...backend.calls, ...backend.subscriptions].filter((message) => message.type.startsWith("myhome/cover_calibration/"));

test("B-01 offers the gateway's profiles with where they were measured and who follows them", async () => {
  const { wiz, code, press, startFor, title } = await mount();
  await startFor("cover.living_one", "B");
  assert.equal(code(), "B-01");
  assert.ok(wiz().querySelector(".wiz-action-note")?.textContent.startsWith("Preview"), "Y-c: B-01 says it is a preview, under its button");
  assert.match(wiz().querySelector(".wiz-body").textContent, /then, live, the cover would move by itself/);
  assert.equal(title(), "Which profile should this cover follow?");
  assert.match(wiz().querySelector(".wiz-body").textContent, /No run will be timed on this cover: you will measure its curtain travel/);
  // A radio list, one row per profile, as in the mockup (no drop-down).
  assert.equal(wiz().querySelector('select[name="profile"]'), null);
  assert.equal(wiz().querySelector(".wiz-radios legend").textContent, "Profile to follow");
  const rows = [...wiz().querySelectorAll('input[name="profile"]')];
  assert.deepEqual(rows.map((input) => [input.value, input.checked, input.closest(".wiz-radio").textContent.trim()]),
    [["tall", true, "tall_two_leaf · measured on Living room shutter 1Followed by Kitchen shutter\u00a02 and Living room shutter\u00a03"]]);
  assert.equal(wiz().querySelector('input[name="profile"]').closest(".wiz-radio").querySelector("small").textContent, "Followed by Kitchen shutter\u00a02 and Living room shutter\u00a03", "D-41: a name keeps its number");
  assert.equal(wiz().querySelector(".wiz-steps-line").textContent, "Introduction · 1 of 3");
  assert.equal(wiz().querySelector(".wiz-steps li[data-step-state] > ul > li").textContent, 'Like "tall_two_leaf"');
  await press("profile");
  assert.equal(code(), "B-02");
  assert.equal(wiz().querySelector(".wiz-steps-line").textContent, "Tape measure readings · 2 of 3");
  assert.match(wiz().querySelector(".wiz-body").innerHTML, /<strong>the curtain travel<\/strong>/);
});

test("review P3: B-01 preselects the profile A-02 named as the closest by travel, not the first; none when none is close", async () => {
  // Two profiles: the first in the list far from the cover's travel, the second within 5 %: A-02 names the second.
  const overview = sampleOverview();
  overview.profiles.unshift({ ...structuredClone(overview.profiles[0]), id: "first", name: "aaa_first", reference_travel_cm: 120, assigned_to: [] });
  overview.covers.find((cover) => cover.entity_id === "cover.living_one").travel_cm = 195;
  const { wiz, code, press, startFor } = await mount({ overview });
  await startFor("cover.living_one", "B");
  assert.equal(code(), "B-01");
  const checked = () => [...wiz().querySelectorAll('input[name="profile"]')].filter((input) => input.checked).map((input) => input.value);
  assert.equal(wiz().querySelector('input[name="profile"]').value, "first", "the first in the list is another one");
  assert.deepEqual(checked(), ["tall"], "the closest by travel (198 cm for 195 cm) is the one ticked");
  // No profile close to the cover's travel: nothing ticked, Continue waits for a choice.
  reset();
  const far = sampleOverview();
  far.covers.find((cover) => cover.entity_id === "cover.living_one").travel_cm = null;
  const none = await mount({ overview: far });
  await none.startFor("cover.living_one", "B");
  assert.deepEqual([...none.wiz().querySelectorAll('input[name="profile"]')].filter((input) => input.checked).length, 0);
  assert.equal(none.wiz().querySelector('[data-wiz="profile"]').disabled, true);
  await tickProfile(none.wiz);
  assert.equal(none.wiz().querySelector('[data-wiz="profile"]').disabled, false);
  void press;
});

test("path B runs as a preview from the travel to the check and the summary, without any calibration message", async () => {
  // A cover without a saved travel: B measures it first.
  const overview = sampleOverview();
  overview.covers.find((cover) => cover.entity_id === "cover.living_one").travel_cm = null;
  const { backend, wiz, code, press, type, startFor, title } = await mount({ stageMs: 40, overview });
  await startFor("cover.living_one", "B");
  await tickProfile(wiz); await press("profile");
  await press("begin");
  assert.equal(wiz().querySelector(".wiz-preview").textContent, translations.en.wizPreview);
  assert.equal(code(), "B-03");
  assert.equal(wiz().querySelector(".wiz-wait p").textContent, "Live, Living room shutter 1 would open fully and the curtain travel would be measured from there.");
  // D-46: nothing moves in a preview: no «positioning», no countdown, no Stop.
  assert.equal(title(), "Here the cover would position itself");
  assert.equal(wiz().querySelector(".wiz-wait-meter").hidden, true);
  assert.equal(wiz().querySelector('[data-wiz="stop"]'), null);
  // Y-b: the preview box says nothing moves (once); the step offers «Continue» to the reading at once.
  assert.equal(wiz().querySelector(".wiz-actions .status.is-still"), null);
  assert.equal(wiz().querySelector('.wiz-actions [data-wiz="skip"]').textContent, "Continue");
  await waitFor(() => code() === "B-04", "B-04");
  await type("196");
  assert.equal(code(), "B-05");
  const stages = () => [...wiz().querySelectorAll(".wiz-wait-stages li")].map((item) => [item.querySelector(":scope > span").textContent, item.dataset.stepState]);
  const meterIn = () => wiz().querySelector(".wiz-wait-meter").closest("li")?.dataset.stepState ?? null;
  assert.deepEqual(stages(), [["1/2 Descends to the bottom — about 22 s", "current"], ["2/2 Ascends halfway through its travel — about 11 s", "future"]]);
  assert.equal(title(), "Here the cover would position itself", "D-46");
  assert.equal(wiz().querySelector(".wiz-wait-meter").hidden, true, "D-46: no bar nor countdown where nothing runs");
  assert.equal(wiz().querySelector('[data-wiz="stop"]'), null, "D-46: no Stop");
  assert.deepEqual([...wiz().querySelectorAll(".wiz-wait-stages ha-icon")].map((icon) => icon.getAttribute("icon")), ["mdi:circle-half-full", "mdi:circle-outline"]);
  assert.equal(meterIn(), "current", "the (hidden) meter still follows the stage in progress");
  await waitFor(() => stages()[1]?.[1] === "current", "second stage");
  assert.equal(stages()[0][1], "done");
  assert.equal(wiz().querySelector(".wiz-wait-stages ha-icon").getAttribute("icon"), "mdi:check-circle");
  await waitFor(() => code() === "B-06", "B-06");
  assert.equal(title(), "Check the cover");
  assert.match(wiz().querySelector(".wiz-body").textContent, /The expected height is about 98 cm, half of its 196 cm travel/);
  assert.match(wiz().querySelector(".wiz-body").textContent, /^Live, the cover would stop at half its curtain travel/, "D-46: not «is at half»");
  await type("99");
  assert.equal(code(), "B-07");
  assert.match(wiz().querySelector(".wiz-body").textContent, /^Gap of about 1 cm at half travel: profile "tall_two_leaf" describes this cover well\./);
  assert.match(wiz().querySelector(".wiz-body").innerHTML, /The edge should have stopped at about <span class="cal-nb">98 cm<\/span> and stopped at <span class="cal-nb">99 cm<\/span>\./,
    "D-36: each value held with its unit");
  await press("accept");
  assert.equal(code(), "B-08");
  assert.equal(wiz().querySelector(".wiz-steps-line").textContent, "Summary · 3 of 3");
  const rows = [...wiz().querySelectorAll(".wiz-main > .wiz-table tbody tr")].map((row) => [...row.children].map((cell) => cell.textContent));
  assert.deepEqual(rows, [["Opening time", "22.0 s", "22.2 s"], ["Closing time", "21.7 s", "="], ["Slat time", "—", "4.4 s"], ["Curtain travel", "—", "196 cm"]], "no travel saved before: measured now");
  assert.equal(wiz().querySelector(".wiz-technical .muted").textContent, "The two roll coefficients, 1.83 and 1.91, come from the profile: they were not measured on this cover.");
  assert.equal(wiz().querySelector(".wiz-profile-block"), null, "B creates and updates no profile");
  assert.equal(wiz().querySelector('[data-wiz="refine"]'), null, "B cannot continue to fine-tuning");
  // L-46: a preview saves nothing: no Save, no «what happens when I save?», one notice, Close or another cover.
  assert.equal(wiz().querySelector(".wiz-save-info"), null);
  assert.deepEqual([...wiz().querySelectorAll(".wiz-actions .wiz-action")].map((button) => button.dataset.wiz), ["close", "another"]);
  assert.equal(wiz().querySelector(".wiz-result-notices").textContent, "Preview: in this version this path saves nothing yet; Living room shutter 1 keeps using its current values.");
  assert.deepEqual([...wiz().querySelectorAll(".wiz-steps ul li")].map((item) => item.textContent), ['Like "tall_two_leaf"', "Curtain travel: 196 cm", "Check: 1 cm"], "the steps keep what was read");
  assert.ok(wiz().querySelector(".wiz-preview"));
  assert.deepEqual(calibrationTraffic(backend), [], "the preview sends nothing to Home Assistant");
});

test("L-21: a failed check of path B offers this cover from scratch, another profile or the check again, never fine-tuning", async () => {
  const { wiz, code, press, type, startFor, title } = await mount({ language: "it" });
  await startFor("cover.living_one", "B");
  // The saved travel (195 cm) is used: the check comes first, expected at 97.5 cm.
  await press("profile"); await press("begin"); await settle();
  await type("104.5");
  assert.equal(code(), "B-07b");
  assert.equal(title(), translations.it.wiz_T144);
  assert.match(wiz().querySelector(".wiz-body").textContent, /^Scarto di circa 7 cm a metà della corsa: più di quanto atteso\./);
  assert.match(wiz().querySelector(".wiz-body").innerHTML, /<strong>niente è ancora stato salvato<\/strong>/);
  assert.equal(wiz().querySelector('[data-wiz="continue_anyway"]'), null, "B has no Continue anyway");
  // L-21: three ways on, none into fine-tuning.
  assert.deepEqual([...wiz().querySelectorAll(".wiz-actions .wiz-action")].map((item) => item.dataset.wiz), ["restart-a", "other-profile", "repeat_check"]);
  assert.doesNotMatch(wiz().querySelector(".wiz-body").textContent, /Correggendo/);
  await press("repeat_check"); await settle();
  assert.equal(code(), "B-06");
  await type("105");
  await press("restart-a");
  assert.equal(code(), "A-03", "path A from the start, outside the preview");
  assert.equal(wiz().querySelector(".wiz-preview"), null);
  assert.equal(document.querySelector("myhome-panel")._calibrationTab._wizard._local.keptTravel, 195, "the travel typed is kept for A-13");
});

test("L-19: B-01 marks the profile the cover follows now as «current», still selectable", async () => {
  const { buildScreen } = await import("../../custom_components/myhome/frontend/panel/panel-calibration-screens.js");
  const t = (key) => translations.en[key] || key;
  const profiles = [{ id: "p1", name: "tall", measuredOn: "Kitchen", followerNames: [] }, { id: "p2", name: "short", measuredOn: "Hall", followerNames: [] }];
  const screen = buildScreen({ t, stage: "profile", path: "B", profiles, local: {}, cover: { name: "K", profile: { id: "p2", name: "short" } } });
  assert.deepEqual(screen.choice.options.map((option) => [option.value, option.badge, !!option.disabled]), [["p1", "", false], ["p2", "current", false]]);
});

test("without a profile on the gateway, B is disabled and the screen says why", async () => {
  const overview = sampleOverview();
  overview.profiles = [];
  for (const cover of overview.covers) cover.profile_id = null;
  const { tab, wiz, code, press, pickCover } = await mount({ overview });
  tab().querySelector('[data-action="cal-tab-measure"]').click(); await tick();
  await pickCover();
  await press("choose");
  assert.equal(code(), "A-02");
  const b = wiz().querySelector('input[name="path"][value="B"]');
  assert.equal(b.disabled, true);
  assert.deepEqual([...b.closest(".wiz-radio").querySelectorAll("small")].map((line) => line.textContent),
    ["Only the curtain travel is measured.", "No saved profile: calibrate a cover of this type first"]);
  assert.equal(wiz().querySelector(".wiz-subline").textContent, "Already calibrated (times and roll, Sep 29, 2026)");
  assert.equal(wiz().querySelector(".wiz-radio:has(.wiz-recommended) input").value, "C", "a calibrated cover is advised to C");
});

test("path B with the cover's travel already saved skips its measurement and keeps the check at half travel", async () => {
  const { wiz, code, press, type, startFor } = await mount({ stageMs: 30 });
  await startFor("cover.living_one", "B");
  await press("profile");
  await press("begin");
  assert.equal(code(), "B-05", "straight to the run to half travel");
  await waitFor(() => code() === "B-06", "B-06");
  assert.match(wiz().querySelector(".wiz-body").textContent, /half of its 195 cm travel/, "the saved travel");
  await type("98");
  assert.equal(code(), "B-07");
  await press("accept");
  assert.equal(code(), "B-08");
  assert.ok([...wiz().querySelectorAll(".wiz-steps ul li")].some((item) => item.textContent === "Curtain travel: 195 cm"), "D-47: the steps keep the travel");
});

test("leaving a preview says that nothing was saved and nothing moved (U-5)", async () => {
  const { wiz, press, startFor } = await mount();
  await startFor("cover.living_one", "B");
  await press("profile");
  await press("begin");
  wiz().querySelector('.wiz-head [data-wiz="exit"]').click(); await tick();
  assert.equal(wiz().querySelector("#wiz-dialog-body").textContent, translations.en.wiz_T497);
  assert.equal(translations.it.wiz_T497, "L'anteprima si chiude: non è stato salvato niente e nessuna tapparella si è mossa.");
});

test("a preview reading beyond the travel + 50 cm shows why it is refused, in the reading's screen (U-7)", async () => {
  const overview = sampleOverview();
  overview.covers.find((cover) => cover.entity_id === "cover.living_one").travel_cm = null;
  const { wiz, code, press, type, startFor } = await mount({ stageMs: 0, overview });
  await startFor("cover.living_one", "B");
  await tickProfile(wiz); await press("profile");
  await press("begin");
  await waitFor(() => code() === "B-04", "B-04");
  await type("196");
  await waitFor(() => wiz().querySelector('.wiz-frame [name="reading_cm"]'), "the check's reading");
  const before = code();
  await type("400");
  assert.equal(code(), before, "the same screen");
  assert.ok(wiz().textContent.includes(translations.en.profileError_invalid_reading), "the refusal is shown");
});


test("Y-b: a preview's positioning is passed with «Continue», nothing to wait for", async () => {
  const overview = sampleOverview();
  overview.covers.find((cover) => cover.entity_id === "cover.living_one").travel_cm = null;
  const { code, press, startFor, wiz } = await mount({ stageMs: 60000, overview });
  await startFor("cover.living_one", "B");
  await tickProfile(wiz); await press("profile"); await press("begin");
  assert.equal(code(), "B-03");
  await press("skip");
  assert.equal(code(), "B-04", "straight to the reading");
});
