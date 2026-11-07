import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { after, afterEach, test } from "node:test";

import { translations } from "../../custom_components/myhome/frontend/panel/panel-translations.js";
import { sampleOverview } from "./fake-calibration.mjs";
import { dom, mount, move, reset, settle, tick } from "./wizard-harness.mjs";

const { parseReading } = await import("../../custom_components/myhome/frontend/panel/panel-calibration-wizard.js");
const { buildScreen, phasesFor, slug } = await import("../../custom_components/myhome/frontend/panel/panel-calibration-screens.js");
const { inline, rich } = await import("../../custom_components/myhome/frontend/panel/panel-calibration-text.js");

afterEach(reset);
after(() => dom.window.close());

test("bold spans, paragraphs and line breaks are rendered from escaped text only", () => {
  assert.equal(inline("**You will need a tape measure. **Stand here"), "<strong>You will need a tape measure.</strong> Stand here");
  assert.equal(rich("**Done.\n**Gap <b>1</b> cm\n\nNext"), "<p><strong>Done.</strong><br>Gap &lt;b&gt;1&lt;/b&gt; cm</p><p>Next</p>");
  assert.equal(parseReading("2,8"), 2.8);
  assert.ok(Number.isNaN(parseReading("")));
  assert.equal(slug("Hall Shutter 1"), "hall_shutter_1");
  assert.deepEqual(phasesFor("A", null, false), ["intro", "prep", "ascent", "descent", "readings", "check", "summary", "finetune"], "L-43: the check is a step");
  assert.deepEqual(phasesFor("A", null, true), ["intro", "prep", "ascent", "descent", "readings", "summary", "finetune"], "fine-tuning has its own check");
  assert.deepEqual(phasesFor("A", null, true).length, 7, "N-10: fine-tuning's summary is part of its step");
  assert.deepEqual(phasesFor("B"), ["intro", "readings", "summary"]);
});

test("every generated screen text is filled in English and Italian", () => {
  for (const language of ["en", "it"]) {
    for (const [key, value] of Object.entries(translations[language])) {
      if (!key.startsWith("wiz")) continue;
      assert.ok(value.trim(), `${language} ${key} is empty`);
      assert.equal((value.match(/\*\*/g) || []).length % 2, 0, `${language} ${key} has unbalanced bold markers`);
      assert.doesNotMatch(value, /<[a-z]/i, `${language} ${key} carries HTML`);
    }
  }
  const placeholders = (value) => [...value.matchAll(/\{(\w+)\}/g)].map((match) => match[1]).sort();
  for (const key of Object.keys(translations.en).filter((item) => item.startsWith("wiz"))) {
    assert.deepEqual(placeholders(translations.it[key]), placeholders(translations.en[key]), key);
  }
});

test("the summaries speak of leaving the calibration, never of a window", () => {
  for (const [language, phrase, stop] of [["it", "Se abbandoni la calibrazione senza salvare, i valori attualmente in uso resteranno invariati.", "Se preferisci fermarti qui, abbandona la calibrazione: **niente è ancora stato salvato**."],
    ["en", "If you leave the calibration without saving, the values currently in use will stay unchanged.", "If you prefer to stop here, discard the calibration: **nothing has been saved yet**."]]) {
    const texts = Object.entries(translations[language]).filter(([key]) => key.startsWith("wiz"));
    // The window of the house (the base the closed cover rests on, G2-8) is not a window of the interface.
    assert.deepEqual(texts.filter(([, value]) => /finestra|the window/.test(value.replace(/base della finestra|the window base/g, ""))).map(([key]) => key), [], `${language}: no window`);
    for (const key of ["wiz_T314", "wiz_T317", "wizSaveCoverOnly"]) assert.ok(translations[language][key].endsWith(phrase), `${language} ${key}`);
    assert.ok(translations[language].wiz_T176.endsWith(stop), `${language} T176`);
  }
});

test("A-01 to A-03 stay in the browser: choosing a cover and a path sends nothing", async () => {
  const { tab, wiz, code, press, title, backend, button, pickCover, choosePath } = await mount();
  tab().querySelector('[data-action="cal-tab-measure"]').click(); await tick();
  assert.equal(code(), "A-01");
  assert.equal(title(), "Which cover do you want to calibrate?");
  assert.equal(wiz().querySelector(".wiz-cover").textContent, "Calibrate a cover");
  assert.match(wiz().querySelector(".wiz-body").innerHTML, /<strong>from travel times<\/strong>/);
  // The covers as the sequence view's rows, one radio each: name, room and address, status, profile.
  assert.equal(wiz().querySelector('select[name="cover"]'), null, "no drop-down");
  const rows = () => [...wiz().querySelectorAll(".wiz-picker .wiz-pick-row")];
  assert.deepEqual(rows().map((row) => [row.querySelector(".cal-row-name strong").textContent.replace(/\u00a0/g, " "), row.querySelector(".cal-row-name .muted").textContent.replace(/\u00a0/g, " "),
    row.querySelector(".cal-badge").textContent.replace(/\u00a0/g, " "), row.querySelector(".cal-batch-profile").textContent.trim()]), [
    ["Kitchen shutter 2", "Kitchen · Address: 91 · A: 9 · PL: 1", "Calibrated", "tall_two_leaf"],
    ["Living room shutter 1", "Living room · Address: 91 · A: 9 · PL: 1", "To calibrate", "—"],
    ["Living room shutter 3", "Living room · Address: 91 · A: 9 · PL: 1", "Calibrated", "tall_two_leaf"]]);
  assert.equal(wiz().querySelector(".wiz-picker [role=radiogroup]").getAttribute("aria-label"), "Cover to calibrate");
  assert.deepEqual([...wiz().querySelectorAll(".wiz-pick-chips .cal-chip")].map((chip) => chip.textContent.replace(/\s+/g, " ").trim()),
    ["All 3", "To calibrate 1", "Calibrated 2", "With profile 2"], "the list's filter chips");
  assert.equal(wiz().querySelectorAll('.wiz-picker input[name="cover"]:checked').length, 0, "opened from the tab's button, nothing is chosen");
  assert.equal(wiz().querySelector(".wiz-pick-notes"), null, "no note before a choice");
  const subtitle = () => wiz().querySelector(".wiz-head .muted");
  assert.equal(subtitle().textContent, "No cover selected yet", "a placeholder until a cover is picked");
  assert.ok(subtitle().classList.contains("wiz-head-placeholder"));
  assert.equal(button("choose").disabled, true, "Continue waits for a choice");
  await pickCover("cover.living_one");
  assert.equal(subtitle().textContent, "Living room · Address: 91 · A: 9 · PL: 1", "the picked cover's room and address, its name only in its row");
  assert.deepEqual([...subtitle().children].map((unit) => [unit.className, unit.textContent]), [["cal-row-unit", "Living room ·"], ["cal-row-address", "Address: 91 · A: 9 · PL: 1"]],
    "units that wrap only between them (responsive map §3.3)");
  assert.equal(subtitle().classList.contains("wiz-head-placeholder"), false);
  assert.equal(wiz().querySelector(".wiz-pick-row.is-selected").dataset.entity, "cover.living_one");
  assert.equal(wiz().querySelector(".wiz-pick-notes"), null, "a never measured cover without a profile has no note");
  assert.equal(wiz().querySelector('.wiz-picker input[name="cover"]:checked').value, JSON.stringify(["one", "cover.living_one"]), "the choice survives the redraw");
  assert.equal(button("choose").disabled, false);
  // Filters and order act on the list only; the choice stays.
  wiz().querySelector('[data-wiz="pick-filter:calibrated"]').click(); await tick();
  assert.deepEqual(rows().map((row) => row.dataset.entity), ["cover.kitchen_two", "cover.living_three"]);
  assert.equal(button("choose").disabled, false, "a chosen cover hidden by a filter stays chosen");
  wiz().querySelector('[data-wiz="pick-filter:all"]').click(); await tick();
  // Cards at every width (responsive map): no column headers; one Sort by control gives the list's orders.
  assert.equal(wiz().querySelector(".wiz-picker .cal-list-head"), null, "no table headers in the wizard");
  const sortBy = (value) => { const select = wiz().querySelector('.wiz-picker select[data-pick-sort="order"]'); select.value = value; select.dispatchEvent(new Event("change", { bubbles: true })); };
  sortBy("status:asc"); await tick();
  assert.deepEqual(rows().map((row) => row.dataset.entity), ["cover.living_one", "cover.kitchen_two", "cover.living_three"], "sorted by status, as the list (L-73: both calibrated, by name)");
  // P-3: the list's own bar; its narrow filter drop-down filters as the chips do.
  assert.deepEqual([...wiz().querySelectorAll(".wiz-picker .cal-filterbar-row > *")].map((part) => part.classList[0]), ["cal-tab-chips", "cal-tab-compact"]);
  const pickFilter = wiz().querySelector(".wiz-picker select[data-pick-filter]");
  pickFilter.value = "calibrated"; pickFilter.dispatchEvent(new Event("change", { bubbles: true })); await tick();
  assert.deepEqual(rows().map((row) => row.dataset.entity), ["cover.kitchen_two", "cover.living_three"]);
  assert.equal(wiz().querySelector('.wiz-pick-chips [data-wiz="pick-filter:calibrated"]').getAttribute("aria-pressed"), "true", "the chips follow");
  const back = wiz().querySelector(".wiz-picker select[data-pick-filter]");
  back.value = "all"; back.dispatchEvent(new Event("change", { bubbles: true })); await tick();
  const phoneSort = wiz().querySelector('.wiz-picker select[data-pick-sort="order"]');
  phoneSort.value = "status:desc"; phoneSort.dispatchEvent(new Event("change", { bubbles: true })); await tick();
  assert.deepEqual(rows().map((row) => row.dataset.entity), ["cover.kitchen_two", "cover.living_three", "cover.living_one"], "status, descending");
  assert.equal(wiz().querySelector('.wiz-picker select[data-pick-sort="order"]').value, "status:desc");
  const again = wiz().querySelector('.wiz-picker select[data-pick-sort="order"]');
  again.value = "status:asc"; again.dispatchEvent(new Event("change", { bubbles: true })); await tick();
  await pickCover("cover.kitchen_two");
  // The chosen row opens with what choosing it means, in the row itself.
  const notes = [...wiz().querySelectorAll(".wiz-pick-row.is-selected .wiz-pick-notes .cal-note")];
  assert.deepEqual(notes.map((note) => [note.textContent, note.classList.contains("is-warning")]), [[
    'This cover is already calibrated (times and roll, Sep 29, 2026) and follows the profile "tall_two_leaf". A new calibration, if you choose it in the next step, will replace the values in use; to adjust them, choose fine-tuning.', true]],
    "one yellow note with the profile");
  sortBy("status:desc"); await tick(); sortBy("status:asc"); await tick();
  await press("choose");
  assert.equal(code(), "A-02");
  assert.equal(title(), 'How to calibrate "Kitchen shutter 2"?');
  assert.equal(wiz().querySelector(".wiz-cover").textContent, "Kitchen shutter 2");
  assert.equal(wiz().querySelector(".wiz-head .muted").textContent, "Kitchen · Address: 91 · A: 9 · PL: 1", "room first, no WHO (G4-1)");
  assert.equal(wiz().querySelector(".wiz-subline").textContent, 'Follows the profile "tall_two_leaf"');
  const radios = [...wiz().querySelectorAll('.wiz-radios input[name="path"]')];
  assert.deepEqual(radios.map((input) => input.closest(".wiz-radio").querySelector(".wiz-radio-label").firstChild.textContent.trim()),
    ["First cover of its type", "Like a cover already measured", "Fine-tuning"], "a radio list, not three buttons");
  assert.equal(wiz().querySelectorAll('.wiz-actions [data-wiz^="path:"]').length, 0);
  assert.deepEqual([...wiz().querySelectorAll(".wiz-recommended")].map((badge) => [badge.closest(".wiz-radio").querySelector("input").value, badge.textContent]),
    [["C", "Recommended"]], "a calibrated cover: C is advised");
  assert.deepEqual(radios.filter((input) => input.checked).map((input) => input.value), ["C"], "the advised path is preselected");
  assert.equal(button("path").disabled, false, "Continue is ready on the advised path");
  assert.match(wiz().querySelector(".wiz-body").textContent, /"Fine-tuning" is the recommended option because this cover is already calibrated[^.]*\. If in doubt, choose this option\.$/, "the reason names the advised option");
  assert.ok(wiz().querySelector(".wiz-subline").classList.contains("cal-note"), "the cover's context is an information box");
  const sent = () => backend.calls.length + backend.subscriptions.length;
  const before = sent();
  const a = radios.find((input) => input.value === "A");
  a.checked = true; a.dispatchEvent(new Event("change", { bubbles: true })); await tick();
  assert.equal(code(), "A-02", "ticking a radio only changes the choice");
  assert.equal(wiz().querySelector('input[name="path"][value="A"]').checked, true, "the choice survives the redraw");
  assert.match(wiz().querySelector(".wiz-body").textContent, /"Fine-tuning" is the recommended option.*If in doubt, choose this option\.$/, "the advice does not follow the choice");
  assert.equal(wiz().querySelector('input[name="path"][value="A"]').closest(".wiz-radio").querySelector(".wiz-radio-warning.is-warning").textContent,
    "A full new calibration will replace this cover's values in use. To adjust them without starting over, choose \"Fine-tuning\".", "a warning under the chosen path");
  assert.equal(sent(), before, "choosing a path sends nothing");
  await choosePath("A");
  assert.equal(code(), "A-03");
  assert.equal(wiz().querySelector(".wiz-steps-line").textContent, "Preparation · 2 of 8");
  assert.equal(wiz().querySelector('.wiz-steps li[data-step-state="current"] .wiz-step-label').textContent, "Preparation");
  assert.equal(wiz().querySelector('input[name="slats"][value="yes"]').checked, true);
  assert.equal(backend.calls.filter((message) => message.type.startsWith("myhome/cover_calibration/")).length, 0);
  assert.equal(backend.subscriptions.filter((message) => message.type.startsWith("myhome/cover_calibration/")).length, 0);
  // Cancel before any session simply closes the wizard.
  button("exit").click(); await tick();
  assert.equal(wiz(), null);
  assert.ok(tab().querySelector(".cal-list"));
});

test("the header's back arrow goes one choice back before any session, keeping what was chosen", async () => {
  const { tab, wiz, code, press, pickCover, choosePath, backend } = await mount();
  const back = () => wiz().querySelector('.wiz-head [data-wiz="back"]');
  tab().querySelector('[data-action="cal-tab-measure"]').click(); await tick();
  assert.equal(code(), "A-01");
  assert.equal(back().getAttribute("aria-label"), "Back");
  assert.equal(back().nextElementSibling.className, "wiz-head-text", "left of the cover's name");
  await pickCover("cover.kitchen_two"); await press("choose");
  await choosePath("C");
  assert.equal(code(), "C-01");
  await press("current");
  const radio = wiz().querySelector('input[name="case"][value="times_only"]'); radio.checked = true; radio.dispatchEvent(new Event("change", { bubbles: true }));
  await press("case");
  assert.equal(code(), "C-35");
  back().click(); await tick();
  assert.deepEqual([code(), wiz().querySelector('input[name="case"]:checked').value], ["C-02", "times_only"], "the case stays chosen");
  back().click(); await tick();
  assert.equal(code(), "C-01");
  back().click(); await tick();
  assert.deepEqual([code(), wiz().querySelector('input[name="path"]:checked').value], ["A-02", "C"], "the path stays chosen");
  await choosePath("A");
  const no = wiz().querySelector('input[name="slats"][value="no"]'); no.checked = true; no.dispatchEvent(new Event("change", { bubbles: true }));
  back().click(); await tick();
  assert.equal(code(), "A-02");
  await press("path");
  assert.deepEqual([code(), wiz().querySelector('input[name="slats"]:checked').value], ["A-03", "no"], "the slat choice too");
  back().click(); await tick(); back().click(); await tick();
  assert.equal(code(), "A-01");
  assert.equal(wiz().querySelector('.wiz-picker input[name="cover"]:checked').value, JSON.stringify(["one", "cover.kitchen_two"]), "the cover stays chosen");
  back().click(); await tick();
  assert.equal(wiz(), null, "from the first screen, back to the list");
  assert.ok(tab().querySelector(".cal-list"));
  assert.equal(backend.subscriptions.filter((message) => message.type === "myhome/cover_calibration/start").length, 0, "nothing was started");
  // During a session there is no way back, only Abandon.
  const second = await mount();
  await second.startFor("cover.living_one");
  await second.press("begin");
  assert.equal(second.code(), "A-04");
  assert.equal(second.wiz().querySelector('[data-wiz="back"]'), null);
});

test("after Calibrate another cover, the way back from A-02 is A-01 again", async () => {
  const context = await mount();
  const { wiz, code, press, pickCover } = context;
  await runToSaved(context, { fromRow: true });
  await press("another");
  assert.equal(code(), "A-01");
  await pickCover("cover.kitchen_two"); await press("choose");
  wiz().querySelector('[data-wiz="back"]').click(); await tick();
  assert.equal(code(), "A-01", "the restart begins at A-01, so back goes there");
});

test("from a cover's own Measure the way back from A-02 is the list", async () => {
  const { tab, wiz, code } = await mount();
  const row = [...tab().querySelectorAll(".cal-row")].find((item) => item.dataset.entity === "cover.living_one");
  row.querySelector('[data-action="cal-tab-measure-cover"]').click(); await tick();
  assert.equal(code(), "A-02");
  wiz().querySelector('[data-wiz="back"]').click(); await tick();
  assert.equal(wiz(), null);
});

test("path A runs the live geometry session from the first close to Save, one message per press", async () => {
  const { backend, wiz, code, press, type, actions, title, startFor } = await mount();
  await startFor("cover.living_one");
  await press("begin");
  const start = backend.subscriptions.find((message) => message.type === "myhome/cover_calibration/start");
  assert.equal(start.mode, "geometry");
  assert.equal(start.entity_id, "cover.living_one");
  assert.equal(start.revision, 7);
  assert.ok(start.client_id);
  assert.equal("slats" in start, false, "a cover with slats keeps the default");
  await tick();
  assert.deepEqual(actions(), ["next"], "Understood, begin closes the cover once");
  assert.equal(code(), "A-04");
  await move(backend);
  assert.equal(code(), "A-05");
  assert.equal(title(), "Starting closure");
  await press("endpoint");
  backend.stopped(); await tick();
  assert.equal(code(), "A-06");
  assert.equal(wiz().querySelector(".wiz-steps-line").textContent, "Ascent · 3 of 8");
  // "Ⓐ first of its type" is the introduction's sub-line, not a caption under the phase line.
  const intro = wiz().querySelector(".wiz-steps ol > li");
  assert.deepEqual([intro.querySelector(".wiz-step-label").textContent, intro.querySelector("ul > li").textContent], ["Introduction", "First of its type"]);
  assert.equal(wiz().querySelector(".wiz-steps-caption"), null);
  await press("ack:lift");
  assert.equal(code(), "A-07");
  assert.equal(wiz().querySelector(".wiz-stopwatch").hidden, true, "no stopwatch before the run");
  assert.equal(wiz().querySelector('[data-wiz="lift"]').disabled, true);
  await press("next");
  await move(backend, { seconds: 4 });
  assert.equal(wiz().querySelector(".wiz-stopwatch").hidden, false);
  assert.equal(wiz().querySelector(".wiz-stopwatch-label").textContent, "Motor");
  assert.equal(wiz().querySelector(".wiz-stopwatch").getAttribute("style"), "--wiz-stopwatch-width: 560px", "as wide as the drawing under it");
  assert.match(wiz().querySelector(".wiz-stopwatch-value").textContent, /^moving for \d+(\.\d)? s$/);
  assert.equal(wiz().querySelector('[data-wiz="next"]').disabled, true, "L-116: ① stays, off once it started the run");
  await press("lift");
  backend.stopped(); await tick();
  assert.equal(code(), "A-08");
  await type("3");
  assert.deepEqual(actions().slice(-2), ["reading", "next"], "the return to the rest follows the reading by itself");
  assert.equal(code(), "A-09");
  await move(backend);
  assert.equal(code(), "A-10");
  await press("endpoint");
  backend.stopped(); await tick();
  assert.equal(code(), "A-11");
  await press("next");
  await move(backend, { seconds: 22.2 });
  await press("endpoint");
  backend.stopped(); await tick();
  // L-13: the travel first, then the ascent's result with it.
  assert.equal(code(), "A-13");
  assert.equal(wiz().querySelector('[name="reading_cm"]').value, "195", "the saved travel is offered, not sent");
  await type("198");
  assert.equal(code(), "A-12");
  assert.equal(backend.calls.filter((message) => message.action === "reading").length, 1, "the travel is not sent before «OK, continue»");
  // What was measured sits in tiles above the text: a small label, a big value; the text no longer repeats it.
  const tiles = () => [...wiz().querySelectorAll(".wiz-results .wiz-result")].map((tile) => [tile.querySelector(".wiz-result-label").textContent,
    tile.querySelector(".wiz-result-value").textContent.replace(/\s+/g, " ").trim()]);
  assert.equal(tiles().length, 3);
  assert.equal(tiles()[0][0], "Lift-off");
  assert.match(tiles()[0][1], /^\d+(\.\d)? s · 3 cm$/, "lift-off time and gap in one tile");
  assert.deepEqual(tiles().slice(1), [["Full ascent", "22.2 s"], ["Curtain travel", "198 cm"]]);
  assert.ok([...wiz().querySelectorAll(".wiz-steps li li")].some((item) => item.textContent.replace(/\s+/g, " ").includes("Curtain travel: 198 cm")),
    "L-41: the stepper shows the travel typed, as the tile does");
  assert.deepEqual([wiz().querySelector(".wiz-results").nextElementSibling.className, wiz().querySelector(".wiz-results").nextElementSibling.nextElementSibling.className],
    ["wiz-result-notices", "wiz-body"], "above the text, with the notices about the result between them (G2-11)");
  assert.match(wiz().querySelector(".wiz-body").textContent, /^If both presses came at the right moment, continue\./);
  await press("ack:ascent");
  assert.equal(code(), "A-14");
  assert.equal(wiz().querySelector(".wiz-steps-line").textContent, "Descent · 4 of 8");
  await press("next");
  await move(backend, { seconds: 21.7 });
  await press("endpoint");
  backend.stopped(); await tick();
  assert.equal(code(), "A-15");
  assert.deepEqual(tiles(), [["Full descent", "21.7 s"]], "L-13: the duration only");
  assert.doesNotMatch(wiz().querySelector(".wiz-body").textContent, /21\.7/);
  await press("repeat");
  assert.equal(actions().at(-1), "next", "the return to the top starts by itself");
  await move(backend);
  assert.equal(wiz().querySelector('[data-wiz="endpoint"]').textContent, "It reached the top, continue");
  await press("endpoint"); backend.stopped(); await tick();
  assert.equal(code(), "A-14");
  await press("next"); await move(backend, { seconds: 21.7 }); await press("endpoint"); backend.stopped(); await tick();
  assert.equal(code(), "A-15", "a repeated descent is confirmed again");
  await press("ack:descent");
  assert.equal(code(), "A-16");
  await press("ack:readings");
  assert.equal(actions().at(-1), "next");
  assert.equal(code(), "A-17");
  await move(backend, { auto: true, stop: true });
  assert.equal(code(), "A-18");
  // L-30: the hint names the heights the model explains; L-9: only a reading above the travel (+3 cm) is refused.
  assert.equal(wiz().querySelector("#wiz-field-hint").textContent, "Usually between 73 and 110 cm: a few centimetres of difference is normal. Enter what the tape says; a comma or a point is fine.");
  const sentBefore = actions().length;
  await type("205");
  assert.equal(actions().length, sentBefore, "a reading above the travel is not sent");
  assert.equal(wiz().querySelector('[name="reading_cm"]').getAttribute("aria-invalid"), "true");
  assert.equal(wiz().querySelector("#wiz-field-error").textContent,
    "The reading (205 cm) is more than the curtain travel (198 cm): check the tape and the point you measure from.");
  // Far from the model (outside the band) but physical: a yellow warning, «Done, next» stays on.
  const field = wiz().querySelector('[name="reading_cm"]');
  field.value = "40"; field.dispatchEvent(new Event("input", { bubbles: true })); await tick();
  assert.equal(wiz().querySelector("#wiz-field-warning").hidden, false);
  assert.equal(wiz().querySelector("#wiz-field-warning").textContent, "The reading is far from the expected one: check the reference. If it is right, continue.");
  assert.equal(wiz().querySelector('[data-wiz="reading"]').disabled, false, "never blocked");
  field.value = "100"; field.dispatchEvent(new Event("input", { bubbles: true })); await tick();
  assert.equal(wiz().querySelector("#wiz-field-warning").hidden, true, "half the travel plus 1 cm: no warning");
  await type("97");
  assert.equal(wiz().querySelector(".wiz-notices").textContent, "");
  assert.equal(actions().at(-1), "next", "the return to the top starts by itself");
  assert.equal(code(), "A-19");
  // Before the automatic confirmation (measured time + margin), the button is the shortcut.
  await move(backend, { seconds: 24 });
  assert.equal(wiz().querySelector('[data-wiz="endpoint"]').textContent, "It reached the top, continue");
  await press("endpoint");
  backend.stopped(); await tick();
  assert.equal(actions().at(-1), "next", "the half descent starts by itself after the top");
  await move(backend, { auto: true, stop: true });
  assert.equal(code(), "A-20");
  await type("101"); await press("check-start");
  assert.equal(code(), "A-21");
  assert.equal(wiz().querySelector(".wiz-steps-line").textContent, "Summary · 7 of 8");
  const rows = [...wiz().querySelectorAll(".wiz-table:not(.wiz-technical .wiz-table) tbody tr")].map((row) => [...row.children].map((cell) => cell.textContent));
  assert.deepEqual(rows[3], ["Curtain travel", "195 cm", "198 cm"]);
  assert.equal(wiz().querySelector('[name="save_profile"]').checked, true);
  assert.equal(wiz().querySelector('[name="profile_name"]').value, "living_room_shutter_1");
  assert.equal(wiz().querySelector(".wiz-name-line").textContent, 'You will create the profile "living_room_shutter_1".');
  await press("save");
  const save = backend.calls.filter((message) => message.action === "save").at(-1);
  assert.equal(save.save_mode, "new");
  assert.equal(save.name, "living_room_shutter_1");
  assert.equal(code(), "A-34");
  assert.match(wiz().querySelector(".wiz-body").textContent, /The profile "living_room_shutter_1" was created/);
  assert.deepEqual(actions(), ["next", "endpoint", "next", "lift", "reading", "next", "endpoint", "next", "endpoint", "reading", "next", "endpoint",
    "repeat", "next", "endpoint", "next", "endpoint", "next", "reading", "next", "endpoint", "next", "reading", "check", "save"]);
});

test("without slats the lift-off is skipped and the slat choice is sent", async () => {
  const { backend, code, press, type, startFor, wiz } = await mount();
  await startFor("cover.living_one");
  const no = wiz().querySelector('input[name="slats"][value="no"]');
  no.checked = true; no.dispatchEvent(new Event("change", { bubbles: true }));
  await press("begin");
  assert.equal(backend.subscriptions.find((message) => message.type === "myhome/cover_calibration/start").slats, false);
  await move(backend);
  const body = () => wiz().querySelector(".wiz-body").textContent;
  assert.equal(code(), "A-05");
  assert.doesNotMatch(body(), /slat/i, "D-10: no slats in the starting closure");
  await press("endpoint");
  backend.stopped(); await tick();
  assert.equal(code(), "A-11");
  assert.equal(wiz().querySelector(".wiz-title").textContent, "Full ascent");
  assert.equal([...wiz().querySelectorAll(".wiz-steps li li")].some((item) => item.textContent.startsWith("Lift-off")), false);
  assert.doesNotMatch(body(), /second run/i, "D-10: the ascent is the first run here");
  assert.match(body(), /To start the ascent, press button ①/);
  await press("next"); await move(backend); await press("endpoint"); backend.stopped(); await tick();
  assert.equal(code(), "A-13");
  await type("198"); await press("ack:ascent");
  assert.equal(code(), "A-14");
  assert.doesNotMatch(body(), /slat|NOT press when the edge/i, "D-10: no slats in the descent, nor the late press they need");
  assert.match(body(), /To start the descent, press button ①/);
});

test("a wide gap asks before moving on and a reading out of range stays in the field", async () => {
  const { backend, code, press, type, actions, startFor, wiz } = await mount();
  await startFor("cover.living_one");
  await press("begin"); await move(backend); await press("endpoint"); backend.stopped(); await tick();
  await press("ack:lift"); await press("next"); await move(backend); await press("lift"); backend.stopped(); await tick();
  await type("25");
  assert.equal(wiz().querySelector('[name="reading_cm"]').getAttribute("aria-invalid"), "true");
  assert.equal(actions().includes("reading"), false, "a gap above the limit is not sent");
  await type("12");
  assert.equal(code(), "A-08");
  assert.match(wiz().querySelector(".wiz-result-notices").textContent, /10 cm or more/, "under the result, before the text (G2-11)");
  assert.equal(wiz().querySelector('[name="reading_cm"]')?.hasAttribute("aria-invalid") ?? false, false, "the refusal gone, the field is no longer marked invalid");
  assert.equal(actions().at(-1), "reading", "no movement starts before the choice");
  await press("repeat");
  assert.equal(actions().at(-1), "next", "Repeat returns to the rest by itself");
  assert.equal(backend.session.lift_attempts, 2);
});

test("a reader sees the step read-only and takes control in two taps while the owner is present", async () => {
  const session = { entry_id: "one", entity_id: "cover.living_one", session_id: "session-1", sequence: 9, revision: 7, mode: "geometry",
    phase: "reading", step: "half_open", reason: null, values: { opening_time: 22, closing_time: 21 }, samples: { lift: 4 }, readings: { gap: 3 },
    geometry: {}, travel_cm: 198, stop_requested: false, slats: true, lift_attempts: 1, still_resting: false, gap_warning: false,
    touching_cm: 1, max_gap_cm: 20, gap_warn_cm: 10, saved_travel_cm: 195, expected_cm: 99, can_repeat: true, save_modes: ["new"], reading_kind: "half_open" };
  const { backend, tab, wiz, code, actions } = await mount({ session, owner: false });
  const open = tab().querySelector('[data-action="cal-tab-session"]');
  open.click(); await tick(6);
  const resume = backend.subscriptions.find((message) => message.type === "myhome/cover_calibration/resume");
  assert.equal("claim" in resume, false);
  assert.equal(code(), "A-18");
  // L-58: the reader's bar: one warning note, then Take control; none of the owner's buttons; no Stop at rest.
  assert.deepEqual([...wiz().querySelectorAll(".wiz-actions .wiz-readonly > p")].map((line) => [line.className, line.textContent]),
    [["cal-note is-warning wiz-readonly-note", "Another device is guiding the calibration."]], "L-79: one short sentence");
  assert.equal(wiz().querySelector('[data-wiz="take"]').className, "wiz-take primary", "L-67: the reader's only action is its primary");
  assert.deepEqual([...wiz().querySelectorAll(".wiz-action")].map((button) => button.dataset.wiz), [], "no owner's buttons, not even greyed");
  assert.doesNotMatch(wiz().querySelector(".wiz-body").textContent, /other device/, "L-75: the step's text stays the step's");
  assert.equal(wiz().querySelector('.wiz-frame [name="reading_cm"]').disabled, true, "the field is there, read only");
  assert.ok(wiz().querySelector(".wiz-body").textContent.length > 0, "and the step's text");
  assert.equal(wiz().querySelector(".wiz-stopwatch").hidden, true, "who guides it is no longer in the stopwatch box");
  wiz().querySelector('[data-wiz="take"]').click(); await tick();
  assert.equal(wiz().querySelector('[data-wiz="take"]').textContent, "Confirm take control · 10 s");
  assert.equal(wiz().querySelector('[data-wiz="take"]').className, "wiz-take primary", "L-67: armed, still the primary");
  assert.match(wiz().querySelector(".wiz-readonly").textContent, /this device will guide the calibration/);
  assert.equal(backend.subscriptions.filter((message) => message.claim).length, 0, "the first tap sends nothing");
  wiz().querySelector('[data-wiz="take"]').click(); await tick(6);
  const claim = backend.subscriptions.filter((message) => message.claim);
  assert.equal(claim.length, 1);
  assert.equal(claim[0].sequence, 9);
  assert.equal(wiz().querySelector(".wiz-readonly"), null);
  assert.equal(wiz().querySelector('[name="reading_cm"]').disabled, false);
  assert.deepEqual([...wiz().querySelectorAll(".wiz-actions .wiz-action")].map((button) => button.dataset.wiz), ["reading", "repeat"], "L-58: the owner's bar, as before");
  assert.deepEqual(actions(), [], "taking control moves nothing");
});

test("an interruption shows its reason and Start over returns to Before you begin", async () => {
  const { backend, code, press, startFor, wiz, title } = await mount({ language: "it" });
  await startFor("cover.living_one");
  await press("begin"); await move(backend);
  backend.interrupt("unexpected_movement"); await tick();
  assert.equal(code(), "S-01");
  assert.equal(title(), translations.it.wiz_T240);
  assert.equal(wiz().querySelector(".wiz-body").textContent, translations.it.calReason_unexpected_movement);
  assert.equal(wiz().querySelector('[data-wiz="exit"]'), null, "an ended session has nothing to discard");
  await press("start-over");
  assert.equal(code(), "A-03");
  backend.interrupt("expired"); await tick();
  assert.equal(code(), "A-03", "a previous session no longer reaches the wizard");
});

test("Discard asks first, closes with Escape, and leaves only once Cancel reaches Home Assistant", async () => {
  const { backend, root, wiz, press, startFor, tab, actions } = await mount();
  await startFor("cover.living_one");
  await press("begin"); await move(backend);
  const discard = wiz().querySelector('.wiz-head [data-wiz="exit"]');
  window.dispatchEvent(new Event("pointerdown")); // a tap
  discard.click(); await tick();
  const dialog = wiz().querySelector(".wiz-dialog");
  assert.equal(dialog.hidden, false);
  assert.equal(dialog.querySelector("h4").textContent, "Close without saving?");
  assert.match(dialog.textContent, /A stop command is sent/);
  assert.equal(dialog.querySelector('[data-wiz="stop"]'), null, "no Stop of its own: Exit without saving sends it, and Stop stays on the page");
  assert.equal(root.activeElement, dialog.querySelector(".wiz-dialog-card"), "X-6: after a tap the focus rests on the dialog's box, no ring");
  dialog.dispatchEvent(new KeyboardEvent("keydown", { key: "Escape", bubbles: true })); await tick();
  assert.equal(dialog.hidden, true);
  assert.equal(root.activeElement, wiz().querySelector('.wiz-actions [data-wiz="endpoint"]'), "focus returns to the screen's confirming button, visible at every width");
  wiz().querySelector('.wiz-head [data-wiz="exit"]').click(); await tick();
  wiz().querySelector('[data-wiz="leave"]').click(); await tick(6);
  assert.equal(actions().at(-1), "cancel");
  assert.equal(wiz(), null);
  assert.ok(tab().querySelector(".cal-list"));
});

test("U-10: after Exit without saving the list is free at once, even with a read begun before the cancel", async () => {
  const { backend, panel, wiz, startFor, tab } = await mount();
  await startFor("cover.living_one");
  wiz().querySelector('[data-wiz="begin"]').click(); await tick(); await move(backend);
  // The list has read the live session; the next periodic read answers only after the cancel.
  panel._calibrationTab.refresh(); await tick(8);
  const callWS = backend.hass.callWS, held = [];
  backend.hass.callWS = async (message) => {
    const result = await callWS(message);
    if (message.type !== "myhome/cover_profiles/overview") return result;
    return new Promise((resolve) => held.push(() => resolve(result)));
  };
  panel._calibrationTab.refresh(); await tick();
  assert.ok(held.length, "a read is in flight");
  backend.hass.callWS = callWS;
  wiz().querySelector('.wiz-head [data-wiz="exit"]').click(); await tick();
  wiz().querySelector('[data-wiz="leave"]').click(); await tick(6);
  assert.equal(wiz(), null);
  const free = () => !tab().querySelector(".cal-tab-busy") && !tab().querySelector('[data-action="cal-tab-measure"]').disabled;
  assert.ok(free(), "no «In corso» row, notice or disabled header right after the cancel");
  held.splice(0).forEach((release) => release()); await tick(8);
  assert.ok(free(), "the read begun before the cancel does not bring the session back");
});

test("a cover that follows a profile saves only on itself by default, which waits for the backend", async () => {
  const overview = sampleOverview();
  const { backend, wiz, code, press, type, startFor } = await mount({ overview });
  await startFor("cover.kitchen_two");
  await press("begin"); await move(backend); await press("endpoint"); backend.stopped(); await tick();
  await press("ack:lift"); await press("next"); await move(backend); await press("lift"); backend.stopped(); await tick();
  await type("3"); await move(backend); await press("endpoint"); backend.stopped(); await tick();
  await press("next"); await move(backend); await press("endpoint"); backend.stopped(); await tick();
  await type("110"); await press("ack:ascent");
  await press("next"); await move(backend); await press("endpoint"); backend.stopped(); await tick();
  await press("ack:descent"); await press("ack:readings"); await move(backend, { auto: true, stop: true });
  await type("54"); await move(backend); await press("endpoint"); backend.stopped(); await tick();
  await move(backend, { auto: true, stop: true }); await type("56"); await press("check-start");
  assert.equal(code(), "A-21c");
  assert.equal(wiz().querySelector('[name="save_profile"]').checked, false);
  assert.match(wiz().querySelector(".wiz-profile-block").textContent, /follows the profile "tall_two_leaf"/);
  assert.equal(wiz().querySelector('[data-wiz="save"]').disabled, true);
  // D-16: a Save not available yet is said at the top, and no help says what it would do.
  assert.match(wiz().querySelector(".wiz-result-notices").textContent, /not available yet/);
  assert.equal(wiz().querySelector(".wiz-save-info"), null);
  const toggle = wiz().querySelector('[name="save_profile"]');
  toggle.checked = true; toggle.dispatchEvent(new Event("change", { bubbles: true })); await tick();
  assert.equal(code(), "A-21d");
  assert.equal(wiz().querySelector(".wiz-save-info").open, true, "What happens when I save? starts open");
  wiz().querySelector(".wiz-save-info").open = false;
  assert.deepEqual([...wiz().querySelectorAll('[name="save_choice"]')].map((input) => input.value), ["update", "new"], "listed as in A-21d");
  assert.equal(wiz().querySelector('[name="save_choice"][value="new"]').checked, true);
  assert.match(wiz().querySelector(".wiz-profile-block").textContent, /Will also change 1 cover: Living room shutter\u00a03\./);
  assert.equal(wiz().querySelector('[data-wiz="save"]').disabled, false);
  const name = wiz().querySelector('[name="profile_name"]');
  const error = () => wiz().querySelector(".wiz-name-error");
  assert.equal(error().hidden, true);
  assert.equal(wiz().querySelector("#wiz-name-hint").textContent, "Up to 64 characters, different from the saved profiles.");
  // Home Assistant accepts any name of 1–64 characters, spaces included: only an empty or an existing name is refused.
  name.value = "test K1"; name.dispatchEvent(new Event("input", { bubbles: true })); await tick();
  assert.equal(wiz().querySelector('[data-wiz="save"]').disabled, false, "a space is fine");
  name.value = "  Tall_Two_Leaf "; name.dispatchEvent(new Event("input", { bubbles: true })); await tick();
  assert.equal(wiz().querySelector('[data-wiz="save"]').disabled, true, "an existing name");
  assert.deepEqual([error().hidden, error().textContent, name.getAttribute("aria-invalid")], [false, "A profile with this name already exists.", "true"]);
  assert.match(name.getAttribute("aria-describedby"), /wiz-name-error/);
  name.value = "   "; name.dispatchEvent(new Event("input", { bubbles: true })); await tick();
  assert.equal(error().textContent, "Enter a name for the profile.");
  assert.equal(wiz().querySelector('[data-wiz="save"]').disabled, true);
  assert.equal(wiz().querySelector('[name="profile_name"]'), name, "typing never redraws the field");
  assert.equal(wiz().querySelector(".wiz-save-info").open, false, "closed by the user, it stays closed while the summary changes");
  name.value = "kitchen_two"; name.dispatchEvent(new Event("input", { bubbles: true })); await tick();
  assert.equal(error().hidden, true, "the error goes with the problem");
  assert.equal(wiz().querySelector(".wiz-name-line").textContent, 'This cover will follow the new profile. "tall_two_leaf" stays with the other cover.');
  // L-118: a base calibration (Home Assistant offers `save_modes: ["new"]`) cannot update the profile followed: that
  // choice is shown disabled, saying so, and never chosen; Save stays usable on a new profile.
  const update = wiz().querySelector('[name="save_choice"][value="update"]');
  assert.equal(update.disabled, true);
  assert.equal(update.closest(".wiz-radio").querySelector(".wiz-option-note.cal-note.is-warning").textContent, "Available in a future version");
  update.checked = true; update.dispatchEvent(new Event("change", { bubbles: true })); await tick();
  assert.equal(wiz().querySelector('[name="save_choice"]:checked').value, "new", "the screen never lands on it");
  assert.equal(wiz().querySelector('[data-wiz="save"]').disabled, false, "Save is never left disabled by a choice it cannot carry out");
  assert.equal(wiz().querySelector('[data-wiz="save"]').textContent, 'Save as "kitchen_two"');
  assert.equal(wiz().querySelector(".wiz-notices").textContent.includes("not available yet"), false, "no notice about a choice not made");
  await press("save"); await tick(4);
  const save = backend.calls.filter((message) => message.action === "save").at(-1);
  assert.deepEqual([save.save_mode, save.name], ["new", "kitchen_two"]);
});

/** Path A on Living room shutter 1 from the first close to its saved screen (A-34). */
async function runToSaved({ backend, press, type, startFor, code, tab, choosePath }, { fromRow = false } = {}) {
  if (!fromRow) await startFor("cover.living_one");
  else {
    // From the cover's own Measure: A-01 is skipped.
    [...tab().querySelectorAll(".cal-row")].find((item) => item.dataset.entity === "cover.living_one").querySelector('[data-action="cal-tab-measure-cover"]').click(); await tick();
    await choosePath("A");
  }
  await press("begin"); await move(backend); await press("endpoint"); backend.stopped(); await tick();
  await press("ack:lift"); await press("next"); await move(backend, { seconds: 4.4 }); await press("lift"); backend.stopped(); await tick();
  await type("3"); await move(backend); await press("endpoint"); backend.stopped(); await tick();
  await press("next"); await move(backend, { seconds: 22.2 }); await press("endpoint"); backend.stopped(); await tick();
  await type("198"); await press("ack:ascent");
  await press("next"); await move(backend, { seconds: 21.7 }); await press("endpoint"); backend.stopped(); await tick();
  await press("ack:descent"); await press("ack:readings"); await move(backend, { auto: true, stop: true });
  await type("97"); await move(backend, { seconds: 24 }); await press("endpoint"); backend.stopped(); await tick();
  await move(backend, { auto: true, stop: true }); await type("101"); await press("check-start");
  assert.equal(code(), "A-21");
  await press("save");
  assert.equal(code(), "A-34");
}

test("L-37/L-43: the base calibration ends with its check, a step of its own, from the top to half the travel; its gap is the summary's precision", async () => {
  const { backend, press, type, startFor, code, title, actions, wiz } = await mount();
  await startFor("cover.living_one");
  await press("begin"); await move(backend); await press("endpoint"); backend.stopped(); await tick();
  await press("ack:lift"); await press("next"); await move(backend, { seconds: 4.4 }); await press("lift"); backend.stopped(); await tick();
  await type("3"); await move(backend); await press("endpoint"); backend.stopped(); await tick();
  await press("next"); await move(backend, { seconds: 22.2 }); await press("endpoint"); backend.stopped(); await tick();
  await type("198"); await press("ack:ascent");
  await press("next"); await move(backend, { seconds: 21.7 }); await press("endpoint"); backend.stopped(); await tick();
  await press("ack:descent"); await press("ack:readings"); await move(backend, { auto: true, stop: true });
  await type("97"); await move(backend, { seconds: 24 }); await press("endpoint"); backend.stopped(); await tick();
  await move(backend, { auto: true, stop: true }); await type("101");
  assert.deepEqual([code(), title()], ["A-21v", "Check the calibration"]);
  // L-97: the drawing of the reading with the tape, between the title and the text, as on the other steps.
  const drawing = wiz().querySelector(".wiz-main .wiz-illustration");
  assert.equal(drawing?.dataset.illustration, "curtain-tape-half", "the check reads at half the travel: the half-height drawing");
  assert.deepEqual([...wiz().querySelector(".wiz-main").querySelectorAll(".wiz-title, .wiz-illustration, .wiz-body")].map((item) => item.getAttribute("class").split(" ")[0]),
    ["wiz-title", "wiz-illustration", "wiz-body"], "title → drawing → text");
  assert.deepEqual([...wiz().querySelectorAll(".wiz-actions .wiz-action")].map((item) => item.dataset.wiz), ["check-start"], "L-43: not skippable");
  assert.equal(wiz().querySelector(".wiz-compact-line").textContent, "Check · 6 of 8");
  const sent = actions().length;
  await press("check-start");
  const check = backend.calls.filter((message) => message.action === "check").at(-1);
  assert.deepEqual([check.direction, check.target_cm], ["closing", 99], "descending from the top to half the travel");
  assert.equal(actions().length, sent + 1);
  assert.equal(code(), "A-21", "once checked, the summary");
  const step = [...wiz().querySelectorAll(".wiz-steps ol > li")].find((item) => item.querySelector(".wiz-step-label").textContent === "Check");
  assert.equal(step.dataset.stepState, "done");
  assert.match(step.textContent.replace(/\s+/g, " "), /Gap: 1 cm/, "its result under it");
});

test("L-44: the check runs as the half readings do: to the top (confirmed by the user), then its timed run starts by itself, then the reading", async () => {
  const { backend, press, type, startFor, code, wiz, actions } = await mount();
  backend.realisticCheck = true;
  await startFor("cover.living_one");
  await press("begin"); await move(backend); await press("endpoint"); backend.stopped(); await tick();
  await press("ack:lift"); await press("next"); await move(backend, { seconds: 4.4 }); await press("lift"); backend.stopped(); await tick();
  await type("3"); await move(backend); await press("endpoint"); backend.stopped(); await tick();
  await press("next"); await move(backend, { seconds: 22.2 }); await press("endpoint"); backend.stopped(); await tick();
  await type("198"); await press("ack:ascent");
  await press("next"); await move(backend, { seconds: 21.7 }); await press("endpoint"); backend.stopped(); await tick();
  await press("ack:descent"); await press("ack:readings"); await move(backend, { auto: true, stop: true });
  await type("97"); await move(backend, { seconds: 24 }); await press("endpoint"); backend.stopped(); await tick();
  await move(backend, { auto: true, stop: true }); await type("101");
  await press("check-start"); await tick(6);
  assert.deepEqual(actions().slice(-2), ["check", "next"], "the run to the top starts from the press");
  backend.started(); backend.tick(5); backend.emit(); await tick();
  assert.equal(code(), "A-30");
  assert.equal(wiz().querySelector('[data-wiz="endpoint"]').disabled, true, "phase 1: the end stop comes on once the motor stopped");
  assert.ok(wiz().querySelector('[data-wiz="stop"]'), "Stop while it moves");
  backend.session.motor_stopped = true; backend.emit(); await tick(6);
  await press("endpoint"); backend.stopped(); await tick(6);
  assert.equal(actions().at(-1), "next", "phase 2: the timed run starts on that confirmation");
  backend.started(); backend.tick(3); backend.emit(); await tick();
  assert.equal(code(), "A-30");
  assert.ok(wiz().querySelector(".wiz-progress:not(.is-indeterminate)"), "its own determinate bar");
  assert.ok(wiz().querySelector('[data-wiz="stop"]'));
  backend.autoStop(); await tick(); backend.stopped(); await tick(6);
  assert.equal(code(), "A-31");
  assert.equal(wiz().querySelector("#wiz-field-hint").textContent, "About 99 cm expected: a few centimetres of difference is normal. Enter what the tape says; a comma or a point is fine.");
  await type("97");
  assert.equal(code(), "A-21", "then the summary");
  // L-45: the result is a tile with the other measured values, and a note with the heights and the threshold.
  // L-55: «Precision», with its caption, and its note beside it in green (within the threshold).
  const tile = [...wiz().querySelectorAll(".wiz-result")].find((item) => item.querySelector(".wiz-result-label").textContent === "Precision");
  assert.equal(tile.querySelector(".wiz-result-value").textContent.replace(/\s+/g, " ").trim(), "2 cm");
  assert.equal(tile.querySelector(".wiz-result-caption").textContent, "gap at half travel");
  const note = tile.parentElement.querySelector(".wiz-result-note");
  assert.ok(note.classList.contains("is-success"));
  assert.equal(tile.className, "wiz-result is-success", "L-69: the tile in the note's colours");
  assert.equal(note.firstElementChild.tagName, "SPAN", "L-70: one box for the text, centred in the note's height");
  assert.match(note.textContent, /Check at half travel: the cover stopped at 97 cm, about 99 cm was expected\. Gap 2 cm: up to 4 cm the calibration counts as precise\./);
  assert.doesNotMatch(wiz().textContent, /\{\w+\}/, "no placeholder left on screen");
  await press("save");
  assert.equal(code(), "A-34");
  assert.ok([...wiz().querySelectorAll(".wiz-result-label")].some((item) => item.textContent === "Precision"), "the tile on A-34 too");
  assert.ok([...wiz().querySelectorAll(".wiz-steps li li")].some((item) => item.textContent.replace(/\s+/g, " ").includes("Gap: 2 cm")), "and its gap in the stepper");
  assert.doesNotMatch(wiz().textContent, /\{\w+\}/);
});

test("L-45: no placeholder reaches a screen of the check, the summary or A-34, in Italian or English", () => {
  const check = { direction: "closing", target_cm: 99, check_seconds: 7.5, planned_seconds: 7.4, expected_cm: 99, measured_cm: 97, deviation_cm: -2, passed: true };
  const base = { mode: "geometry", values: { opening_time: 22, closing_time: 21 }, samples: { lift: 4 }, readings: { gap: 3, half_open: 97, half_close: 101 },
    geometry: { slat_time_s: 4, opening_roll: 1.8, closing_roll: 1.9 }, travel_cm: 198, slats: true, owner: true, sequence: 9, check_threshold_cm: 4 };
  const views = [{ phase: "review", step: "half_close" }, { phase: "review", step: "half_close", check }, { phase: "review", step: "half_close", check: { ...check, measured_cm: 93, deviation_cm: -6, passed: false } },
    { phase: "opening", step: "top", check }, { phase: "closing", step: "check", check }, { phase: "reading", step: "check", check }];
  for (const language of ["it", "en"]) {
    const t = (key) => translations[language][key] || key;
    const screens = [...views.map((view) => buildScreen({ t, language, stage: "session", path: "A", view: { ...base, ...view }, local: { ackReadings: true }, cover: { name: "K", effective: {} } })),
      buildScreen({ t, language, stage: "saved", path: "A", local: { saved: { target: "profile_new", name: "k", check, threshold: 4 } }, cover: { name: "K" } })];
    for (const screen of screens) {
      const text = JSON.stringify([screen.title, screen.body, screen.notices, screen.results, screen.actions, screen.wait, screen.field, screen.precision, screen.stepper]);
      assert.doesNotMatch(text, /\{\w+\}/, `${language} ${screen.code}`);
    }
  }
});

test("L-46: the base summary's «What happens when I save?» says exactly what Save does with the choice made", () => {
  const t = (key) => translations.it[key] || key;
  const view = { mode: "geometry", phase: "review", values: { opening_time: 22, closing_time: 21 }, geometry: {}, travel_cm: 110, owner: true, sequence: 2,
    check: { gap_cm: 1, passed: true, check_seconds: 10.5 } };
  const at = (cover, local) => buildScreen({ t, language: "it", stage: "session", path: "A", cover, view, local: { saveProfile: true, ...local }, supports: () => true });
  const fresh = at({ name: "Kitchen 2", entity_id: "cover.kitchen_two", followers: [], effective: {} }, { profileName: "kitchen_2" });
  assert.match(fresh.saveInfo.body, /^\*\*Salva:\*\* i valori misurati diventano un profilo nuovo, «kitchen_2», che Kitchen 2 segue da subito/);
  const follows = { name: "Kitchen 2", entity_id: "cover.kitchen_two", profile: { name: "alte", provenance: { opening: { origin_entity_id: "cover.kitchen_two" } } }, followers: [], effective: {} };
  assert.match(at(follows, {}).saveInfo.body, /^\*\*Salva:\*\* il profilo «alte» viene aggiornato con i valori misurati/);
});

test("L-44b: a check stopped is done again: A-21v with its notice, Stop in both of its legs", () => {
  const t = (key) => translations.en[key] || key;
  const base = { mode: "geometry", values: { opening_time: 22, closing_time: 21 }, samples: { lift: 4 }, readings: { gap: 3, half_open: 97, half_close: 101 },
    geometry: { slat_time_s: 4, opening_roll: 1.8, closing_roll: 1.9 }, travel_cm: 198, slats: true, owner: true, sequence: 9 };
  const at = (view) => buildScreen({ t, stage: "session", path: "A", view: { ...base, ...view }, local: { ackReadings: true }, elapsed: 3, cover: { name: "K", effective: {} } });
  const again = at({ phase: "review", step: "half_close", check: null, check_interrupted: "stopped" });
  assert.equal(again.code, "A-21v");
  assert.equal(again.notices[0].text, "The check was stopped: do it again to complete the calibration.");
  const check = { direction: "closing", target_cm: 99, planned_seconds: 7.4 };
  for (const view of [{ phase: "opening", step: "top", check }, { phase: "starting_open", step: "top", check }, { phase: "closing", step: "check", check }, { phase: "starting_close", step: "check", check }]) {
    const stop = at(view).actions.find((action) => action.id === "stop");
    assert.deepEqual([stop?.kind, !!stop?.disabled], ["stop", false], `${view.step} ${view.phase}: «Ferma la tapparella», outlined red`);
  }
});

test("L-51: a state line under the actions is a caption the whole width of the bar", async () => {
  const css = (await import("node:fs")).readFileSync(new URL("../../custom_components/myhome/frontend/panel/myhome-panel.css", import.meta.url), "utf8");
  assert.match(css, /\.wiz-actions \.wiz-action\.status \{ width: 100%; max-width: none; min-height: 0; padding: 6px 0; font-size: 13px; line-height: 18px; text-align: center; \}/);
});

test("L-53: a leg's label stays on one line; its «— about N s» goes in a narrow box", async () => {
  const css = (await import("node:fs")).readFileSync(new URL("../../custom_components/myhome/frontend/panel/myhome-panel.css", import.meta.url), "utf8");
  assert.match(css, /\.wiz-stage-label \{ flex: 1 1 0; min-width: 0; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; \}/);
  assert.match(css, /@container wizwait \(max-width: 479px\) \{ \.wiz-stage-time \{ display: none; \} \}/);
  const source = (await import("node:fs")).readFileSync(new URL("../../custom_components/myhome/frontend/panel/panel-calibration-wizard.js", import.meta.url), "utf8");
  assert.match(source, /<span class="wiz-stage-time"> — \$\{esc\(value\.slice\(at \+ 3\)\)\}<\/span>/);
});

test("L-54: the return to the top before the half-descent reading is the readings' two-leg wait, whatever this tab acknowledged", () => {
  const t = (key) => translations.it[key] || key;
  const view = { mode: "geometry", step: "top", phase: "opening", values: { opening_time: 22, closing_time: 21 }, samples: { lift: 4, half_open: 11 },
    readings: { gap: 3, half_open: 97 }, geometry: { slat_time_s: 4.4 }, travel_cm: 198, slats: true, owner: true, sequence: 9 };
  for (const local of [{}, { ackReadings: true }]) {
    const screen = buildScreen({ t, language: "it", stage: "session", path: "A", view, local, elapsed: 4, cover: { name: "K", effective: {} } });
    assert.equal(screen.code, "A-19", JSON.stringify(local));
    assert.deepEqual(screen.wait.stages.map((stage) => stage.state), ["current", "future"], "two legs: to the top, then the half descent");
    assert.equal(screen.stepper.line, "Letture col metro · 5 di 8", "a step of the readings, not of the descent");
    assert.deepEqual(screen.actions.map((action) => action.id), ["endpoint", "stop"]);
  }
  // Before the readings (a repeated descent: no half-ascent reading yet) the return to the top stays A-09.
  const repeat = buildScreen({ t, language: "it", stage: "session", path: "A", view: { ...view, readings: { gap: 3 } }, local: {}, elapsed: 4, cover: { name: "K", effective: {} } });
  assert.equal(repeat.code, "A-09");
});

test("L-55: the precision tile's note is green within the threshold, yellow beyond, and «Repeat the check» is offered beyond", () => {
  const t = (key) => translations.it[key] || key;
  const base = { mode: "geometry", phase: "review", step: "half_close", values: { opening_time: 22, closing_time: 21 }, samples: {}, readings: { half_open: 97, half_close: 101 },
    geometry: { slat_time_s: 0, opening_roll: 1.8, closing_roll: 1.9 }, slats: false, travel_cm: 198, owner: true, sequence: 9, check_threshold_cm: 4 };
  const at = (check) => buildScreen({ t, language: "it", stage: "session", path: "A", view: { ...base, check }, local: {}, cover: { name: "K", effective: {} } });
  const good = at({ expected_cm: 99, measured_cm: 97, deviation_cm: -2, passed: true });
  assert.deepEqual([good.results.at(-1).label, good.results.at(-1).value, good.results.at(-1).caption, good.results.at(-1).note.tone], ["Precisione", "2", "scarto a metà corsa", "success"]);
  const bad = at({ expected_cm: 99, measured_cm: 93, deviation_cm: -6, passed: false });
  assert.equal(bad.results.at(-1).note.tone, "warning");
  assert.match(bad.results.at(-1).note.text, /Scarto 6 cm: sopra i 4 cm considerati accettabili/);
  assert.ok(bad.actions.some((action) => action.id === "check-start"), "the check can be repeated");
});

test("L-56: the header's «Abandon the calibration» is 44 px high, its label centred, centred in the header", async () => {
  const css = (await import("node:fs")).readFileSync(new URL("../../custom_components/myhome/frontend/panel/myhome-panel.css", import.meta.url), "utf8");
  assert.match(css, /\.wiz-discard \{ display: inline-flex; align-items: center; justify-content: center; align-self: center; box-sizing: border-box; min-height: 44px; padding: 0 16px;/);
});

test("L-57: a red button keeps its red border under the pointer, pressed and focused; no accent", async () => {
  const css = (await import("node:fs")).readFileSync(new URL("../../custom_components/myhome/frontend/panel/myhome-panel.css", import.meta.url), "utf8");
  assert.match(css, /:is\(\.wiz-discard, \.wiz-leave, \.wiz-action\.stop, \.wiz-action\.danger-outline, \.wiz-action\.danger, \.cal-abort-confirm\):is\(:hover, :active, :focus, :focus-visible\) \{ border-color: var\(--panel-error\); \}/);
  assert.match(css, /:focus-visible \{ outline-color: var\(--panel-error\); \}/);
});

test("L-59/L-81: the check note beside the «Precision» tile, about 280 px wide, centred on the tile at its own height", async () => {
  const css = (await import("node:fs")).readFileSync(new URL("../../custom_components/myhome/frontend/panel/myhome-panel.css", import.meta.url), "utf8");
  assert.match(css, /\.wiz-result-row \{ display: flex; flex-wrap: wrap; gap: 12px; align-items: stretch;/);
  assert.match(css, /\.wiz-result-note \{ flex: 0 1 280px; min-width: 200px; max-width: 280px; align-self: center; box-sizing: border-box; \}/, "L-81: its own height, centred on the tile");
  assert.match(css, /@container wiz-result-row \(max-width: 479px\) \{ \.wiz-result-note \{ flex: 1 1 100%; max-width: none; \} \}/, "stacked: full width");
  assert.doesNotMatch(css, /\.wiz-result-note \{[^}]*align-self: stretch/);
});

const cascadeCss = readFileSync(new URL("../../custom_components/myhome/frontend/panel/myhome-panel.css", import.meta.url), "utf8")
.replace(/\/\*[\s\S]*?\*\//g, "");
/** L-60/L-62: the CSS cascade, resolved by hand (jsdom computes no styles): selector weight, then order. */
const weight = (selector) => {
  // :is() and :not() weigh as their heaviest argument, :where() nothing (innermost first, so nesting works too).
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
// A selector list split at its own commas only (not those inside :is(), :not() and attribute values).
const topLevel = (list) => {
  const parts = []; let depth = 0, start = 0;
  for (let i = 0; i < list.length; i++) {
    if ("([".includes(list[i])) depth += 1; else if (")]".includes(list[i])) depth -= 1;
    else if (list[i] === "," && depth === 0) { parts.push(list.slice(start, i)); start = i + 1; }
  }
  return [...parts, list.slice(start)].map((part) => part.trim());
};
// The declaration of `property` that wins for `element`: highest weight, then the later rule.
const winner = (element, property) => {
  let best = null, order = 0;
  for (const [, selectors, body] of cascadeCss.matchAll(/(?<=^|[{}])\s*([^{}@]+?)\s*\{([^{}]*)\}/g)) {
    order += 1;
    const value = body.match(new RegExp(`(?:^|;)\\s*${property}\\s*:\\s*([^;]+)`))?.[1]?.trim();
    if (!value) continue;
    for (const selector of topLevel(selectors)) {
      let hit = false; try { hit = element.matches(selector); } catch { /* selectors jsdom does not know */ }
      const w = weight(selector);
      if (hit && (!best || w > best.w || (w === best.w && order >= best.order))) best = { w, order, value, selector };
    }
  }
  return best?.value ?? "";
};

test("L-92: on the phone the back arrow is the ✕'s twin, centred on the title's first line; the desktop header is unchanged", () => {
  // The first @media block matching `head`, whole (balanced braces).
  const block = (head) => {
    const start = cascadeCss.search(head); assert.ok(start >= 0, String(head));
    let depth = 0;
    for (let i = cascadeCss.indexOf("{", start); i < cascadeCss.length; i++) {
      if (cascadeCss[i] === "{") depth += 1; else if (cascadeCss[i] === "}" && (depth -= 1) === 0) return cascadeCss.slice(start, i + 1);
    }
    return "";
  };
  const rule = (css, selector) => [...css.matchAll(/(?<=^|[{}])\s*([^{}@]+?)\s*\{([^{}]*)\}/g)]
    .filter(([, selectors]) => topLevel(selectors).includes(selector)).map(([, , body]) => body).join(";");
  const target = /width: 44px; min-width: 44px; height: 44px; min-height: 44px; padding: 0; justify-content: center;/;
  const portrait = block(/@media \(max-width: 719px\) \{\s*\.wiz \{ position: fixed/);
  assert.match(rule(portrait, ".wiz-back"), target, "44 px target");
  assert.match(rule(portrait, ".wiz-x"), target);
  assert.match(rule(portrait, ".wiz-back ha-icon"), /--mdc-icon-size: 24px/, "24 px glyph");
  assert.match(rule(portrait, ".wiz-x ha-icon"), /--mdc-icon-size: 24px/);
  // -10 px above and below keep the header's height; L-102: arrow, ✕ and title block centred on the band's height.
  assert.match(rule(portrait, ".wiz-head"), /align-items: center/);
  assert.match(rule(portrait, ".wiz-cover"), /line-height: 24px/);
  assert.match(rule(portrait, ".wiz-back"), /margin: -10px 0 -10px -10px/);
  assert.match(rule(portrait, ".wiz-x"), /margin: -10px -10px -10px 0/);
  const landscape = block(/@media \(max-height: 499px\) and \(orientation: landscape\) \{\s*\.wiz \{/);
  assert.match(rule(landscape, ".wiz-back"), target);
  assert.match(rule(landscape, ".wiz-back ha-icon"), /--mdc-icon-size: 24px/);
  assert.match(rule(landscape, ".wiz-x"), /margin: 0/, "the landscape header centres them itself");
  // Desktop: «Abandon» in place of the ✕, the back arrow as before.
  const desktop = cascadeCss.slice(0, cascadeCss.search(/@media \(max-width: 719px\) \{\s*\.wiz \{ position: fixed/));
  assert.match(rule(desktop, ".wiz-back"), /^\s*flex: none; margin: -2px 0 0 -8px;\s*$/);
  assert.match(rule(desktop, ".wiz-x"), /display: none/);
});

test("L-94: every dialog of the tab has its title band as much above as below, and its buttons their own bottom padding", () => {
  const rules = (selector) => [...cascadeCss.matchAll(/(?<=^|[{}])\s*([^{}@]+?)\s*\{([^{}]*)\}/g)]
    .filter(([, selectors]) => topLevel(selectors).includes(selector)).map(([, , body]) => body.trim());
  // One padding for the band, the same above and below, at every size (no sheet-only top padding left over).
  assert.deepEqual(rules("dialog.cal-dialog .dialog-head").filter((body) => /padding/.test(body)),
    ["justify-content: space-between; padding-block: 18px;"]);
  assert.match(rules("dialog.cal-dialog:has(.calibration-safety-actions) .dialog-head").join(";"), /padding-block: 8px/, "the landscape sheet: tighter, still even");
  // The buttons' row keeps its own bottom padding: the footer on desktop and on the sheets.
  assert.match(rules(".dialog-foot").join(";"), /padding: 14px 26px 20px/);
  assert.match(rules("dialog.cal-dialog .dialog-foot").join(";"), /padding-block: 12px/);
  // The body ends tight only for the hosted maintainer's view, whose Stop/Cancel bar brings its own padding.
  assert.match(rules(".cal-dialog-body").join(";"), /padding-block: 18px 4px/);
  assert.match(rules(".calibration-safety-actions").join(";"), /padding: 14px 26px/);
  assert.equal(rules(".cal-abort-actions").length, 0, "no buttons' row in a body any more");
});

test("L-95: the wizard's bands and buttons in Figma 937:56699's colours, every one a theme token, light and dark", () => {
  const block = cascadeCss.slice(cascadeCss.indexOf(".wiz { --wiz-strip:"));
  assert.ok(block.length > 0 && !/#[0-9a-f]{3,8}\b(?![^(]*\))/i.test(block.replace(/var\([^()]*\)/g, "")), "no literal colour outside a token's fallback");
  const rule = (selector) => [...block.matchAll(/(?<=^|[{}])\s*([^{}@]+?)\s*\{([^{}]*)\}/g)]
    .filter(([, selectors]) => topLevel(selectors).includes(selector)).map(([, , body]) => body.replace(/\s+/g, " ").trim()).join(" | ");
  // Bands: the accent on the page's background, 15 % (dark 20 %) for the steps strip, 20 % for the action bar.
  assert.match(rule(".wiz"), /--wiz-strip: color-mix\(in srgb, var\(--panel-accent\) 15%, var\(--primary-background-color, #fafafa\)\)/);
  assert.match(rule(".wiz"), /--wiz-bar: color-mix\(in srgb, var\(--panel-accent\) 20%, var\(--primary-background-color, #fafafa\)\)/);
  assert.match(rule('.wiz[data-theme="dark"]'), /--wiz-strip: color-mix\(in srgb, var\(--panel-accent\) 20%, var\(--primary-background-color, #111\)\)/);
  assert.match(rule('.wiz[data-theme="dark"]'), /--wiz-bar: color-mix\(in srgb, var\(--panel-accent\) 20%, var\(--primary-background-color, #111\)\)/);
  assert.match(rule(".wiz-compact"), /background: var\(--wiz-strip\); border-bottom-color: transparent;/, "light: no hairline under the strip");
  assert.match(rule('.wiz[data-theme="dark"] .wiz-compact'), /border-bottom-color: var\(--panel-border\)/, "dark: its hairline");
  assert.match(rule(".wiz-steps-button"), /background: var\(--panel-surface\)/);
  assert.match(block, /@media \(max-width: 719px\), \(max-height: 499px\) and \(orientation: landscape\) \{ \.wiz-actions \{ background: var\(--wiz-bar\); \} \}/);
  assert.match(block, /@container caltab \(max-width: 699px\) \{ \.wiz-actions \{ background: var\(--wiz-bar\); \} \}/);
  // Disabled: the text colour (dark: the on-accent white) at 15 % / 30 % (dark 40 %), the outline at 30 %.
  assert.match(rule(".wiz"), /--wiz-off: var\(--primary-text-color, #141414\); --wiz-off-fill: 15%; --wiz-off-text: 30%; --wiz-off-line: 30%;/);
  assert.match(rule('.wiz[data-theme="dark"]'), /--wiz-off: var\(--text-primary-color, #fff\); --wiz-off-fill: 15%; --wiz-off-text: 40%; --wiz-off-line: 30%;/);
  assert.match(rule(".wiz .wiz-action.secondary:disabled"), /border-color: color-mix\(in srgb, var\(--wiz-off\) var\(--wiz-off-line\), transparent\)/);
  // Red: «Ferma la tapparella» filled.
  assert.match(rule(".wiz-action.stop"), /background: var\(--panel-error\); color: var\(--text-primary-color, #fff\)/);
  // The phone's title band: the strong accent; dark at 70 % on the background.
  assert.match(rule(".wiz-head"), /background: var\(--panel-accent-strong\)/);
  assert.match(rule('.wiz[data-theme="dark"] .wiz-head'), /color-mix\(in srgb, var\(--panel-accent-strong\) 70%, var\(--primary-background-color, #111\)\)/);
  // Applied in the jsdom cascade: the block wins over the earlier rules.
  const doc = dom.window.document, host = doc.createElement("div");
  host.innerHTML = `<div class="wiz"><div class="wiz-compact"></div><div class="wiz-actions"><button class="wiz-action stop"></button><button class="wiz-action secondary" disabled></button></div></div>`;
  doc.body.append(host);
  assert.equal(winner(host.querySelector(".wiz-compact"), "background"), "var(--wiz-strip)");
  assert.equal(winner(host.querySelector(".wiz-action.stop"), "background"), "var(--panel-error)");
  assert.equal(winner(host.querySelector(".wiz-action.secondary"), "opacity"), "1");
  host.remove();
});

test("L-62: the list follows Home Assistant's dark mode on every update, its dialogs too", async () => {
  const { backend, panel, root } = await mount();
  const host = root.querySelector("#cover-calibration-tab");
  assert.equal(host.dataset.theme, "light");
  backend.hass = { ...backend.hass, themes: { ...(backend.hass.themes || {}), darkMode: true } };
  panel.hass = backend.hass; await tick();
  assert.equal(host.dataset.theme, "dark", "switched without leaving the tab");
  const dialogHost = root.getElementById("dialog-host");
  if (dialogHost) assert.equal(dialogHost.dataset.theme, "dark");
  backend.hass = { ...backend.hass, themes: { ...backend.hass.themes, darkMode: false } };
  panel.hass = backend.hass; await tick();
  assert.equal(host.dataset.theme, "light");
});

test("L-63/L-64: dark «In corso» chip in the full warning colour; a disabled button of the list is outlined in both themes", async () => {
  const doc = dom.window.document;
  for (const theme of ["light", "dark"]) {
    const host = doc.createElement("div"); host.dataset.theme = theme; host.className = "cal-tab"; doc.body.append(host);
    host.innerHTML = `<span class="badge cal-badge" data-status="running"></span>
      <div class="cal-tab-top"><button class="primary" disabled></button></div>
      <div class="cal-list"><div class="cal-row"><button disabled></button><button class="primary is-settling" disabled></button></div>
        <div class="cal-block-action"><button class="primary" disabled></button></div><div class="cal-block-action"><button disabled></button></div></div>
      <div class="wiz"><div class="wiz-actions"><button class="wiz-action primary" disabled></button></div></div>`;
    const chip = host.querySelector(".cal-badge");
    if (theme === "dark") {
      assert.equal(winner(chip, "color"), "var(--panel-warning)", "dark: chip text");
      assert.equal(winner(chip, "border-color"), "var(--panel-warning)", "dark: chip border");
    }
    assert.match(winner(chip, "background"), /--cal-chip\) 12%/, `${theme}: the chip's 12 % fill`);
    const [head, rowButton, settling, cta, secondary] = host.querySelectorAll(".cal-tab-top button, .cal-list button");
    for (const button of [head, rowButton, cta, secondary]) {
      assert.equal(winner(button, "background"), "transparent", `${theme}: ${button.outerHTML} not filled`);
      assert.match(winner(button, "border-color"), /--primary-text-color[^)]*\) 24%/, `${theme}: outlined`);
      assert.equal(winner(button, "opacity"), "1");
    }
    assert.match(winner(settling, "background"), /--panel-accent/, `${theme}: the passing state keeps its look`);
    // L-95: the wizard's own disabled primary follows Figma 937:56699 (its colours per theme are pinned by the L-95 test).
    assert.equal(winner(host.querySelector(".wiz-action"), "background"), "color-mix(in srgb, var(--wiz-off) var(--wiz-off-fill), transparent)", `${theme}: the wizard's disabled primary`);
    host.remove();
  }
});

test("L-65: every button that cannot be pressed shows the same «not allowed» cursor, the passing state included", async () => {
  const doc = dom.window.document;
  const host = doc.createElement("div"); host.className = "cal-tab"; doc.body.append(host);
  host.innerHTML = `<div class="cal-list"><button class="is-settling" disabled></button><button disabled></button></div>
    <div class="cal-tab-top"><button class="primary is-settling" disabled></button></div>
    <div class="wiz"><div class="wiz-actions"><button class="wiz-action primary" disabled></button><button class="wiz-action primary is-settling" disabled></button><button class="wiz-action secondary" disabled></button></div></div>
    <button class="icon-button" disabled></button>`;
  for (const button of host.querySelectorAll("button")) assert.equal(winner(button, "cursor"), "not-allowed", button.outerHTML);
  assert.doesNotMatch(cascadeCss, /cursor: (?:wait|progress)/, "no other cursor for a button that waits");
  host.remove();
});

test("dark pass: no colour darkened towards black applies on a dark theme without a lighter override", () => {
  // Every rule that mixes a colour with #000 must have a [data-theme="dark"] counterpart mixing it the other way.
  const darkened = [...cascadeCss.matchAll(/(?<=^|[{}])\s*([^{}@]+?)\s*\{[^{}]*color-mix\([^;{}]*#000\)/g)].map((match) => match[1]);
  assert.ok(darkened.length >= 3);
  for (const selectors of darkened) {
    for (const selector of selectors.split(/,(?![^(]*\))/).map((part) => part.trim())) {
      const base = selector.replace(/:hover|:not\(:disabled\)/g, "");
      const covered = [...cascadeCss.matchAll(/(?<=^|[{}])\s*(\[data-theme="dark"\][^{}@]+?)\s*\{([^{}]*)\}/g)]
        .some(([, dark, body]) => /(?:^|[\s;])(?:color|--note-text): (?:var\(--panel-warning\)|color-mix\([^;]*(?:#fff|--primary-text-color))/.test(body)
          && dark.replace(/\[data-theme="dark"\] ?/g, "").replace(/:hover|:not\(:disabled\)/g, "").includes(base.replace(/^:is\(([^)]*)\)$/, "$1").split(",")[0].trim()));
      assert.ok(covered, `no dark counterpart for ${selector}`);
    }
  }
});

test("L-66: a card with a session in progress has the table row's warning fill, first card included", () => {
  const row = /\.cal-row\.is-running \{ background: (color-mix\(in srgb, var\(--panel-warning\) 7%, var\(--panel-surface\)\)); \}/.exec(cascadeCss)?.[1];
  assert.ok(row, "the table row's fill");
  const cards = /@container caltab \(max-width: 1015px\) \{([\s\S]*?)\n\}/.exec(cascadeCss)?.[1] ?? "";
  // Inside the card band, every selector that paints a card's surface is matched by one at least as heavy for a running card.
  const card = /\.cal-row\.is-running, \.cal-row\.is-running:first-of-type, \.cal-list-head \+ \.cal-row\.is-running \{[^}]*background: ([^;]+);/.exec(cards)?.[1];
  assert.equal(card, row, "same token and alpha as the row");
  assert.ok(cards.indexOf(".cal-list-head + .cal-row.is-running") > cards.indexOf(".cal-list-head + .cal-row {"), "after the card's own surface");
});

test("L-68: every step with a drawing reads title → drawing → text → field; the lift-off reading A-08 included", async () => {
  const { tab, wiz, code } = await mount({ session: resumed({ phase: "reading", step: "lift", reading_kind: "lift" }) });
  tab().querySelector('[data-action="cal-tab-session"]').click(); await tick(6);
  assert.equal(code(), "A-08");
  assert.equal(wiz().querySelector(".wiz-title").textContent, "How far has the edge risen?");
  const main = wiz().querySelector(".wiz-main");
  const parts = [".wiz-title", ".wiz-illustration", ".wiz-body", ".wiz-field, .wiz-frame input"].map((selector) => main.querySelector(selector));
  assert.ok(parts.every(Boolean), "title, drawing, text and field are all there");
  const before = (a, b) => Boolean(a.compareDocumentPosition(b) & dom.window.Node.DOCUMENT_POSITION_FOLLOWING);
  for (let i = 1; i < parts.length; i++) assert.ok(before(parts[i - 1], parts[i]), `${i - 1} before ${i}`);
  // The table of drawings has a single place for them now: under the title.
  const table = readFileSync(new URL("../../custom_components/myhome/frontend/panel/panel-calibration-screens.js", import.meta.url), "utf8");
  assert.doesNotMatch(table, /AFTER|after-body/);
});

test("L-69: the «Precision» tile takes its note's colours, green within the threshold and yellow beyond, in both themes", () => {
  const doc = dom.window.document;
  for (const theme of ["light", "dark"]) {
    const host = doc.createElement("div"); host.dataset.theme = theme; doc.body.append(host);
    host.innerHTML = `<div class="wiz-result is-success"><span class="wiz-result-label"></span></div><p class="cal-note is-success"></p>
      <div class="wiz-result is-warning"><span class="wiz-result-caption"></span></div><p class="cal-note is-warning"></p><div class="wiz-result"></div>`;
    const [good, goodNote, bad, badNote, plain] = host.children;
    for (const [tile, note, tone] of [[good, goodNote, "success"], [bad, badNote, "warning"]]) {
      assert.match(winner(tile, "background"), new RegExp(`--panel-${tone}\\) 8%`), `${theme}: ${tone} fill (the note itself has none, L-74)`);
      // L-127: a note's text is its `--note-text`.
      assert.equal(winner(note, "color"), "var(--note-text)");
      assert.equal(winner(tile, "color"), winner(note, "--note-text"), `${theme}: ${tone} text as its note`);
      assert.match(winner(tile, "box-shadow"), new RegExp(`--panel-${tone}`), `${theme}: ${tone} border`);
    }
    assert.equal(winner(good.firstElementChild, "color"), "inherit");
    assert.equal(winner(bad.firstElementChild, "color"), "inherit");
    assert.equal(winner(plain, "background"), "var(--panel-accent-soft)", "other tiles unchanged");
    host.remove();
  }
});

test("L-72: one corner radius (the shell's 8 px) for notes, tiles, cards, boxes, menus, inputs and buttons; chips stay pills", () => {
  const doc = dom.window.document;
  const host = doc.createElement("div"); host.className = "cal-tab"; doc.body.append(host);
  host.innerHTML = `<p class="cal-note"></p><p class="cal-note is-success"></p><p class="cal-note is-warning"></p><div class="cal-compact-sort"></div>
    <div class="cal-list"><div class="cal-row"><div class="cal-row-main"><span class="cal-row-icon"></span><button></button><button class="icon-button"></button></div>
      <div class="cal-menu"></div><div class="cal-block-action"><button class="primary"></button></div></div></div>
    <div class="cal-empty"><span class="cal-empty-icon"></span></div><div class="cal-batch-row"></div><input type="text"><select></select>
    <div class="wiz"><p class="wiz-preview"></p><div class="wiz-stopwatch"></div><div class="wiz-wait"></div><div class="wiz-result"></div><div class="wiz-box"></div>
      <details class="wiz-accordion"></details><pre class="wiz-code"></pre><div class="wiz-profile-block"></div>
      <div class="wiz-actions"><button class="wiz-action primary"></button><button class="wiz-action secondary"></button><button class="wiz-take primary"></button></div></div>
    <div class="cal-filterbar is-chips"><span class="cal-filterbar-sort"><select></select></span></div>
    <button class="cal-chip"></button><span class="badge cal-badge"></span><span class="cal-profile-chip"></span><input type="checkbox">`;
  const all = [...host.querySelectorAll("*")];
  const pills = all.filter((element) => element.matches(".cal-chip, .cal-badge, .cal-profile-chip"));
  // The list's and the wizard's own frames keep Home Assistant's card radius (none where they fill the screen).
  const frames = all.filter((element) => element.matches(".cal-list, .wiz, .cal-filterbar, .cal-filterbar-sort, .cal-filterbar-sort select"));
  const rounded = ".cal-compact-sort, .cal-row, .cal-row-main, .cal-row-icon, .cal-menu, .cal-empty, .cal-empty-icon, .cal-batch-row, "
    + ".wiz-preview, .wiz-stopwatch, .wiz-wait, .wiz-result, .wiz-box, .wiz-accordion, .wiz-code, .wiz-profile-block, button, select, input";
  for (const element of all.filter((item) => item.matches(rounded) && !pills.includes(item) && !frames.includes(item) && !item.matches('input[type="checkbox"]'))) {
    // A card's inner band sits 1 px inside its border.
    assert.match(winner(element, "border-radius"), /^(?:var\(--cal-btn-radius\)|calc\(var\(--cal-btn-radius\) - 1px\))$/, element.outerHTML.slice(0, 60));
  }
  for (const element of pills) assert.equal(winner(element, "border-radius"), "999px", `${element.className}: a pill`);
  assert.equal(winner(host.querySelector(".cal-filterbar-sort select"), "border-radius"), "inherit", "the sort chip's select follows its pill");
  for (const note of host.querySelectorAll(".cal-note")) assert.equal(winner(note, "border-radius"), "0", "L-74: a note is a quote, not a box");
  host.remove();
  // No other fixed radius left in the calibration rules but pills, circles, none, the card radius and the dialogs' frames.
  const loose = [...cascadeCss.matchAll(/(?<=^|[{}])\s*([^{}@]*\.(?:cal|wiz)-[^{}@]*?)\s*\{[^{}]*border-radius: ([^;}]+)/g)]
    .filter(([, , value]) => !/^(?:var\(--cal-btn-radius\)|calc\(var\(--cal-btn-radius\) - 1px\)|999px|50%|0|inherit|var\(--panel-radius\)|18px(?: 18px 0 0)?|0 0 var\(--panel-radius\) var\(--panel-radius\)|(?:calc\(var\(--cal-btn-radius\) - 1px\) ?){2}0 0|0 0 (?:calc\(var\(--cal-btn-radius\) - 1px\) ?){2})$/.test(value.trim()))
    .map(([, selector, value]) => `${selector.trim().slice(0, 50)}: ${value}`)
    .filter((line) => !/^\.(?:cal-status|cal-visual|cal-actions|cal-values)/.test(line));
  assert.deepEqual(loose, [], "every other radius is the shell's");
});

test("L-73: the three status chips: grey, accent, and «Verified · N cm» green up to 4 cm, yellow beyond, in both themes", async () => {
  const { statusBadge } = await import("../../custom_components/myhome/frontend/panel/panel-calibration-tab.js");
  const t = (key) => translations.en[key];
  assert.equal(statusBadge({ status: "to_measure" }, t), '<span class="badge cal-badge" data-status="to_measure">To calibrate</span>');
  assert.equal(statusBadge({ status: "calibrated" }, t), '<span class="badge cal-badge" data-status="calibrated">Calibrated</span>');
  assert.equal(statusBadge({ status: "verified", check: 4 }, t), '<span class="badge cal-badge" data-status="verified" data-tone="success">Verified · 4 cm</span>', "the threshold itself passes");
  assert.equal(statusBadge({ status: "verified", check: 5 }, t), '<span class="badge cal-badge" data-status="verified" data-tone="warning">Verified · 5 cm</span>');
  const doc = dom.window.document;
  for (const theme of ["light", "dark"]) {
    const host = doc.createElement("div"); host.dataset.theme = theme; doc.body.append(host);
    host.innerHTML = ["to_measure", "calibrated"].map((status) => `<span class="badge cal-badge" data-status="${status}"></span>`).join("")
      + '<span class="badge cal-badge" data-status="verified" data-tone="success"></span><span class="badge cal-badge" data-status="verified" data-tone="warning"></span>';
    const [grey, accent, green, yellow] = host.children;
    assert.equal(winner(grey, "--cal-chip"), "var(--panel-text-2)");
    assert.equal(winner(accent, "--cal-chip"), "var(--panel-accent)");
    assert.equal(winner(green, "--cal-chip"), "var(--panel-success)");
    assert.equal(winner(yellow, "--cal-chip"), "var(--panel-warning)");
    for (const chip of host.children) {
      assert.match(winner(chip, "background"), /--cal-chip\) 12%/, `${theme}: 12 % fill`);
      assert.match(winner(chip, "border"), /--cal-chip\) 20%/, `${theme}: 20 % border`);
      assert.equal(winner(chip, "border-radius"), "999px", `${theme}: a pill`);
    }
    assert.equal(winner(yellow, "color"), theme === "dark" ? "var(--panel-warning)" : "color-mix(in srgb, var(--panel-warning) 55%, #000)");
    host.remove();
  }
});

test("L-77: the red Stop only while a movement is in progress, for the owner and the reader alike", () => {
  const t = (key) => translations.en[key] || key;
  const base = { mode: "geometry", values: {}, samples: {}, readings: {}, slats: true, sequence: 5, travel_cm: null };
  const reader = { read_only: true, owner: false, owner_present: true };
  const screen = (view, { local = { ackLift: true }, status = {}, elapsed = null } = {}) =>
    buildScreen({ t, stage: "session", path: "A", view: { ...base, ...view }, local, status, elapsed, cover: { name: "Kitchen shutter 2" } });
  const stops = (built) => built.actions.filter((action) => action.id === "stop").length;
  for (const role of [{ owner: true }, reader]) {
    const who = role.owner ? "owner" : "reader";
    // A-07 «First run: slats» at rest, before ①: no Stop.
    const before = screen({ phase: "briefing", step: "lift", ...role });
    assert.equal(before.code, "A-07");
    // L-116: a bar with the ①/② pair always draws its Stop, last, off while nothing moves.
    assert.equal(stops(before), 1, `${who}: at rest before ①, drawn`);
    assert.equal(before.actions.find((action) => action.id === "stop").disabled, true, `${who}: off at rest`);
    // ① sent, the session's answer not back yet: Stop at once.
    assert.equal(stops(screen({ phase: "briefing", step: "lift", ...role }, { status: { starting: true } })), 1, `${who}: ① sent`);
    // The run under way.
    assert.equal(stops(screen({ phase: "opening", step: "lift", ...role }, { elapsed: 2 })), 1, `${who}: running`);
    // «Back to the base» (A-10): Stop while it closes; gone the moment the session reports the motor stopped.
    const back = { phase: "closing", step: "reset", samples: { lift: 4 }, readings: { gap: 3 }, values: { opening_time: 22, closing_time: 21 }, ...role };
    const running = screen(back, { elapsed: 3 });
    assert.equal(running.code, "A-10");
    assert.equal(stops(running), 1, `${who}: back to the base, still running`);
    assert.equal(stops(screen({ ...back, motor_stopped: true, stopped_elapsed: 19 }, { elapsed: 19 })), 0, `${who}: back to the base, stop frame received`);
    // Readings with the tape, review: at rest.
    assert.equal(stops(screen({ phase: "reading", step: "lift", reading_kind: "lift", ...role })), 0, `${who}: a tape reading`);
    assert.equal(stops(screen({ phase: "review", step: "half_close", travel_cm: 198, values: { opening_time: 22, closing_time: 21 }, ...role }, { local: { ackLift: true, ackAscent: true, ackDescent: true, ackReadings: true } })), 0, `${who}: review`);
  }
  // The reader shows exactly the owner's decision within a movement, the run-out state included (U-9).
  const ranOut = { ...base, phase: "closing", step: "reset", samples: { lift: 4 }, readings: { gap: 3 }, values: { opening_time: 22, closing_time: 21 } };
  const owner = buildScreen({ t, stage: "session", path: "A", view: { ...ranOut, owner: true }, local: { ackLift: true }, elapsed: 60, cover: { name: "K" } });
  const follower = buildScreen({ t, stage: "session", path: "A", view: { ...ranOut, ...reader }, local: { ackLift: true }, elapsed: 60, cover: { name: "K" } });
  assert.equal(stops(owner), stops(follower), "one rule for both roles");
});

test("L-77: Stop shows the moment ① is sent, before the session answers, and the 700 ms hold keeps ② safe", async () => {
  const view = { phase: "briefing", step: "lift", values: {}, screen: { code: "A-07", path: "A", scope: null, local: { ackLift: true } } };
  const { backend, tab, wiz, code } = await mount({ session: resumed(view), guardMs: 700 });
  tab().querySelector('[data-action="cal-tab-session"]').click(); await tick(6);
  assert.equal(code(), "A-07");
  await new Promise((resolve) => setTimeout(resolve, 720));
  assert.equal(wiz().querySelector('.wiz-actions [data-wiz="stop"]').disabled, true, "L-116: at rest Stop is drawn, off");
  const original = backend.hass.callWS;
  let release;
  backend.hass.callWS = (message) => message.action === "next" ? new Promise((resolve) => { release = () => resolve(original(message)); }) : original(message);
  wiz().querySelector('.wiz-actions [data-wiz="next"]').click(); await tick(4);
  assert.equal(wiz().querySelector('.wiz-actions [data-wiz="stop"]').disabled, false, "① sent: Stop on at once");
  assert.equal(wiz().querySelector('.wiz-actions [data-wiz="lift"]').disabled, true, "② held back meanwhile");
  release(); await tick(8);
  backend.hass.callWS = original;
});

/** L-78: a base calibration up to its check's intro (A-21v), as in the L-37 test. */
async function toCheckIntro(context) {
  const { backend, press, type, startFor, code } = context;
  await startFor("cover.living_one");
  await press("begin"); await move(backend); await press("endpoint"); backend.stopped(); await tick();
  await press("ack:lift"); await press("next"); await move(backend, { seconds: 4.4 }); await press("lift"); backend.stopped(); await tick();
  await type("3"); await move(backend); await press("endpoint"); backend.stopped(); await tick();
  await press("next"); await move(backend, { seconds: 22.2 }); await press("endpoint"); backend.stopped(); await tick();
  await type("198"); await press("ack:ascent");
  await press("next"); await move(backend, { seconds: 21.7 }); await press("endpoint"); backend.stopped(); await tick();
  await press("ack:descent"); await press("ack:readings"); await move(backend, { auto: true, stop: true });
  await type("97"); await move(backend, { seconds: 24 }); await press("endpoint"); backend.stopped(); await tick();
  await move(backend, { auto: true, stop: true }); await type("101");
  assert.equal(code(), "A-21v");
}

test("L-78: one press sends one check, held from the click until the answer; its target is never a NaN", async () => {
  const context = await mount({ guardMs: 0 });
  const { backend, wiz, panel } = context;
  await toCheckIntro(context);
  const original = backend.hass.callWS;
  let release;
  backend.hass.callWS = (message) => message.action === "check" ? new Promise((resolve) => { release = () => resolve(original(message)); }) : original(message);
  const button = () => wiz().querySelector('.wiz-actions [data-wiz="check-start"]');
  button().click(); button().click(); await tick();
  assert.equal(button().disabled, true, "held from the click until the answer");
  assert.ok(button().classList.contains("is-settling"), "with the passing look");
  button().click(); await tick();
  release(); await tick(8);
  backend.hass.callWS = original;
  const checks = backend.calls.filter((message) => message.action === "check");
  assert.equal(checks.length, 1, "a double click sends once");
  assert.deepEqual([checks[0].direction, checks[0].target_cm], ["closing", 99], "half the view's travel, descending");
  // The view without its travel when the check is sent: no target at all (the backend plans its own), nothing null.
  const wizard = panel._calibrationTab._wizard, gateway = wizard._gateway;
  const sent = [];
  const act = gateway.act.bind(gateway);
  gateway.act = (action, extra) => { sent.push([action, extra]); return Promise.resolve(); };
  const travel = gateway.view.travel_cm; gateway.view.travel_cm = undefined;
  await wizard._act("check-start");
  gateway.view.travel_cm = travel; gateway.act = act;
  assert.deepEqual(sent, [["check", {}]]);
});

test("L-78: refusals say what happened: our words for the check, the code for an unknown one, the connection only when nothing came back", () => {
  const t = (key) => translations.en[key] || key;
  const view = { mode: "geometry", phase: "review", step: "half_close", travel_cm: 198, values: { opening_time: 22, closing_time: 21 }, samples: { lift: 4 },
    readings: { gap: 3, half_open: 97, half_close: 101 }, geometry: { slat_time_s: 4 }, slats: true, owner: true, sequence: 9, check: null };
  const local = { ackLift: true, ackAscent: true, ackDescent: true, ackReadings: true };
  const errorOf = (error) => buildScreen({ t, stage: "session", path: "A", view, local, status: { error }, cover: { name: "K" } });
  const texts = (built) => built.notices.filter((notice) => notice.tone === "error").map((notice) => notice.text);
  const impossible = errorOf({ code: "invalid_check" });
  assert.deepEqual(texts(impossible), [translations.en.wiz_T575]);
  assert.deepEqual(impossible.actions.map((action) => action.id), ["check-start", "skip-check"], "never a dead end");
  assert.deepEqual(texts(errorOf({ code: "invalid_format" })), ["Home Assistant did not accept the command (code: invalid_format). Check the current step and try again."]);
  assert.deepEqual(texts(errorOf({ code: 3, message: "Connection lost" })), [translations.en.calConnectionError], "the socket's own number");
  assert.deepEqual(texts(errorOf({})), [translations.en.calConnectionError], "nothing came back");
  // Skipped: the summary, without a check.
  const skipped = buildScreen({ t, stage: "session", path: "A", view, local: { ...local, checkSkipped: true }, cover: { name: "K" } });
  assert.equal(skipped.code, "A-21");
});

test("L-78: «the measurement state changed» is not shown once the session has moved past the command", async () => {
  const context = await mount({ guardMs: 0 });
  const { backend, wiz, panel } = context;
  await toCheckIntro(context);
  const original = backend.hass.callWS;
  backend.hass.callWS = async (message) => {
    if (message.action !== "check") return original(message);
    backend.calls.push(message);
    throw { code: "calibration_step", message: "stale" };
  };
  wiz().querySelector('.wiz-actions [data-wiz="check-start"]').click(); await tick(4);
  assert.match(wiz().querySelector(".wiz-notices").textContent, /measurement state changed/i, "shown while nothing newer is known");
  backend.session.sequence += 1; backend.emit(); await tick(6);
  assert.equal(wiz().querySelector(".wiz-notices").textContent.trim(), "", "a newer view: the refusal is gone");
  backend.hass.callWS = original;
  void panel;
});

test("L-127 parity: every note and notice of the list, the wizard and their dialogs follows one recipe; light boxed, dark bar only; tones only change colours", () => {
  const doc = dom.window.document;
  const accentDark = "color-mix(in srgb, var(--panel-accent) 70%, var(--primary-text-color, #fff))";
  const warningLight = "color-mix(in srgb, var(--panel-warning) 60%, #000)", warningDark = "color-mix(in srgb, var(--panel-warning) 70%, var(--primary-text-color, #fff))";
  const successDark = "color-mix(in srgb, var(--panel-success) 70%, var(--primary-text-color, #fff))", text = "var(--primary-text-color, #182b37)";
  // [classes|data-tone, host, tone token, light text, dark text]: Info, neutral info, Avviso, Successo, Errore.
  const notes = [
    ["cal-note", "cal-tab", "--panel-accent", "var(--panel-accent)", accentDark],
    ["cal-note cal-tab-help", "cal-tab", "--panel-accent", "var(--panel-accent)", accentDark],
    ["cal-note wiz-subline", "cal-tab", "--panel-accent", "var(--panel-accent)", accentDark],
    ["cal-note wiz-radio-notice", "cal-tab", "--panel-accent", "var(--panel-accent)", accentDark],
    ["notice wiz-notice|info", "cal-tab", "--panel-accent", text, text],
    ["notice", "cal-tab", "--panel-accent", text, text],
    ["cal-note is-warning", "cal-tab", "--panel-warning", warningLight, warningDark],
    ["cal-note is-warning wiz-readonly-note", "cal-tab", "--panel-warning", warningLight, warningDark],
    ["cal-note is-warning wiz-field-warning", "cal-tab", "--panel-warning", warningLight, warningDark],
    ["cal-note is-warning cal-batch-note", "cal-tab", "--panel-warning", warningLight, warningDark],
    ["notice cal-tab-busy", "cal-tab", "--panel-warning", warningLight, warningDark],
    ["notice wiz-notice|warning", "cal-tab", "--panel-warning", warningLight, warningDark],
    ["cal-note is-success", "cal-tab", "--panel-success", "var(--panel-success)", successDark],
    ["cal-note is-success wiz-result-note", "cal-tab", "--panel-success", "var(--panel-success)", successDark],
    ["notice cal-tab-done", "cal-tab", "--panel-success", "var(--panel-success)", successDark],
    ["notice error", "cal-tab", "--panel-error", "var(--panel-error)", "var(--panel-error)"],
    ["notice wiz-notice|error", "cal-tab", "--panel-error", "var(--panel-error)", "var(--panel-error)"],
    ["notice error cal-abort-error", "dialog", "--panel-error", "var(--panel-error)", "var(--panel-error)"],
  ];
  for (const theme of ["light", "dark"]) {
    const list = doc.createElement("div"); list.className = "cal-tab"; list.dataset.theme = theme; doc.body.append(list);
    const dialog = doc.createElement("dialog"); dialog.className = "cal-dialog"; list.append(dialog);
    for (const [spec, host, tone, lightText, darkText] of notes) {
      const [className, dataTone] = spec.split("|");
      const note = doc.createElement("p"); note.className = className; if (dataTone) note.dataset.tone = dataTone;
      (host === "dialog" ? dialog : list).append(note);
      const what = `${theme}: ${spec}`;
      assert.equal(winner(note, "--note-tone"), `var(${tone})`, `${what}: its tone`);
      assert.equal(winner(note, "--note-text"), theme === "light" ? lightText : darkText, `${what}: its text`);
      assert.equal(winner(note, "color"), "var(--note-text)", `${what}: one colour rule`);
      assert.equal(winner(note, "border-inline-start"), "2px solid var(--note-tone)", `${what}: a 2 px bar in the tone`);
      assert.equal(winner(note, "border-radius"), "0", `${what}: square`);
      assert.equal(winner(note, "font-size"), "13px", `${what}: 13 px`);
      if (theme === "light") {
        assert.equal(winner(note, "background"), "color-mix(in srgb, var(--note-tone) 12%, transparent)", `${what}: the tone at 12 %`);
        assert.equal(winner(note, "border"), "1px solid color-mix(in srgb, var(--note-tone) 45%, transparent)", `${what}: 1 px at 45 % on the other sides`);
        assert.equal(winner(note, "padding"), "8px 12px", what);
      } else {
        assert.equal(winner(note, "background"), "none", `${what}: no fill`);
        assert.equal(winner(note, "border"), "0 solid transparent", `${what}: no other side`);
        assert.equal(winner(note, "padding"), "4px 0 4px 12px", what);
      }
    }
    list.remove();
  }
  // No icon in a note: the tab draws none any more, and one left over is not shown.
  const host = doc.createElement("div"); host.className = "cal-tab"; host.innerHTML = '<p class="notice cal-tab-busy"><ha-icon></ha-icon><span></span></p>'; doc.body.append(host);
  assert.equal(winner(host.querySelector("ha-icon"), "display"), "none");
  host.remove();
});

test("L-98/L-99: an open row in the secondary background (band 65 %, details 30 %), the pointer's accent 12 % on every row, both themes; a row in progress keeps its yellow", () => {
  const doc = dom.window.document;
  for (const theme of ["light", "dark"]) {
    const host = doc.createElement("div"); host.className = "cal-tab"; host.dataset.theme = theme; doc.body.append(host);
    host.innerHTML = `<div class="cal-row is-expanded"><div class="cal-row-main"></div><div class="cal-row-details"></div></div>
      <div class="cal-row is-expanded is-running"><div class="cal-row-main"></div><div class="cal-row-details"></div></div>`;
    const [open, running] = host.querySelectorAll(".cal-row");
    assert.equal(winner(open.querySelector(".cal-row-main"), "background"), "color-mix(in srgb, var(--panel-surface-2) 65%, var(--panel-surface))", theme);
    assert.equal(winner(open.querySelector(".cal-row-details"), "background"), "color-mix(in srgb, var(--panel-surface-2) 30%, var(--panel-surface))", theme);
    assert.match(winner(running.querySelector(".cal-row-main"), "background"), /--panel-warning/, `${theme}: in progress stays yellow`);
    assert.match(winner(running.querySelector(".cal-row-details"), "background"), /--panel-warning/);
    host.remove();
  }
  // The pointer (jsdom has no :hover): a closed row takes the accent; L-101: the open one's grey intensifies instead.
  assert.match(cascadeCss, /@media \(hover: hover\) \{ \.cal-row-main:hover \{ background: color-mix\(in srgb, var\(--panel-accent\) 12%, var\(--panel-surface\)\); \} \}/);
  assert.match(cascadeCss, /\.cal-row\.is-expanded \.cal-row-main:hover \{\s*background: color-mix\(in srgb, var\(--primary-text-color, #182b37\) 6%, color-mix\(in srgb, var\(--panel-surface-2\) 65%, var\(--panel-surface\)\)\); \}/);
  assert.doesNotMatch(cascadeCss, /\.cal-row\.is-expanded \.cal-row-main:hover \{[^}]*--panel-accent/, "no accent on the open row's hover");
  assert.match(cascadeCss, /\.cal-row\.is-running \.cal-row-main:hover \{ background: color-mix\(in srgb, var\(--panel-warning\) 14%, transparent\); \}/, "in progress: its yellow under the pointer");
  assert.doesNotMatch(cascadeCss, /\.cal-row\.is-expanded \.cal-row-(main|details) \{ background: color-mix\(in srgb, var\(--panel-accent\)/, "L-83's accent tints are gone");
});

test("L-96: the fields of the wizard, the tab and its dialogs read as fields; focus, error and the sort selects keep theirs", () => {
  const doc = dom.window.document, host = doc.createElement("div");
  host.innerHTML = `<div class="wiz"><form class="wiz-field"><input name="reading_cm"><input name="bad" aria-invalid="true"></form>
      <label class="wiz-switch"><input type="checkbox"></label></div>
    <div class="cal-tab"><label class="cal-compact-sort"><select></select></label><label class="cal-batch-gateway"><select></select></label></div>
    <dialog class="cal-dialog"><input name="profile_name"></dialog><div class="elsewhere"><input></div>`;
  doc.body.append(host);
  const outline = "1px solid color-mix(in srgb, var(--primary-text-color, #182b37) 35%, transparent)";
  for (const field of host.querySelectorAll('.wiz-field [name="reading_cm"], .cal-batch-gateway select, dialog input')) {
    assert.equal(winner(field, "border"), outline, field.outerHTML);
    assert.equal(winner(field, "background-color"), "var(--panel-surface)");
  }
  assert.equal(winner(host.querySelector('[aria-invalid="true"]'), "border-color"), "var(--panel-error)", "the error keeps its red");
  assert.equal(winner(host.querySelector(".cal-compact-sort select"), "border"), "0", "the sort select stays borderless…");
  assert.equal(winner(host.querySelector(".cal-compact-sort"), "border-color"), "color-mix(in srgb, var(--primary-text-color, #182b37) 35%, transparent)",
    "…its label is the box, with the same outline");
  assert.notEqual(winner(host.querySelector(".wiz-switch input"), "border"), outline, "a switch is no field");
  assert.notEqual(winner(host.querySelector(".elsewhere input"), "border"), outline, "only the calibration's fields");
  // The keyboard's accent focus is the shell's, unchanged.
  assert.match(cascadeCss, /input:focus-visible, select:focus-visible \{ outline: none; border-color: var\(--panel-accent\); box-shadow: 0 0 0 3px var\(--panel-accent-soft\); \}/);
  host.remove();
});

test("L-95b: red has two classes: the emergency («Ferma la tapparella») filled in every layout, abandoning outlined", () => {
  const doc = dom.window.document, host = doc.createElement("div");
  // The Stop of the owner's bar, of a reader's bar and of the landscape column is the same button (`kind: "stop"`).
  host.innerHTML = `<div class="wiz"><header class="wiz-head"><button class="wiz-discard"></button></header>
      <div class="wiz-actions"><button class="wiz-action stop"></button><button class="wiz-action danger-outline"></button><button class="wiz-action danger"></button></div>
      <div class="wiz-readonly"><div class="wiz-actions"><button class="wiz-action stop"></button></div></div>
      <div class="wiz-dialog"><div class="wiz-dialog-card"><div class="actions"><button class="wiz-leave"></button></div></div></div></div>
    <dialog class="cal-dialog"><footer class="dialog-foot"><button class="danger cal-abort-confirm"></button></footer></dialog>`;
  doc.body.append(host);
  // The filled emergency comes with the L-95 recolouring (one block, revertable on its own): without it, the Stop is
  // the earlier outlined one, and this test still holds.
  const recoloured = cascadeCss.includes(".wiz { --wiz-strip:");
  for (const stop of host.querySelectorAll(".wiz-action.stop")) {
    if (recoloured) {
      assert.equal(winner(stop, "background"), "var(--panel-error)", "emergency: filled");
      assert.equal(winner(stop, "color"), "var(--text-primary-color, #fff)");
    } else assert.equal(winner(stop, "background"), "transparent", "before L-95: outlined");
  }
  // Rules for the phone bar and the landscape column come earlier in the sheet: the filled Stop wins over them.
  if (recoloured) {
    assert.match(cascadeCss, /\.wiz-action\.stop, \.wiz-action\.link \{ min-height: 44px; background: transparent; \}[\s\S]*\.wiz-action\.stop \{ border-color: var\(--panel-error\); background: var\(--panel-error\);/);
  }
  for (const abandon of host.querySelectorAll(".wiz-discard, .wiz-action.danger-outline, .wiz-action.danger, .wiz-leave, .cal-abort-confirm")) {
    assert.notEqual(winner(abandon, "background"), "var(--panel-error)", `${abandon.className}: never filled`);
    assert.match(winner(abandon, "color"), /--panel-error/, `${abandon.className}: red text`);
    assert.match(winner(abandon, "border-color") || winner(abandon, "border"), /--panel-error/, `${abandon.className}: red outline`);
  }
  host.remove();
});

test("L-104: a ⋮ menu item reads from the start at every width, its icon on the first line, centred in its height", () => {
  const doc = dom.window.document, host = doc.createElement("div");
  host.innerHTML = `<div class="cal-tab"><span class="cal-menu" role="menu"><button role="menuitem" class="cal-menu-danger"><ha-icon></ha-icon>Annulla la calibrazione in corso</button></span></div>`;
  doc.body.append(host);
  const item = host.querySelector("button");
  assert.equal(winner(item, "text-align"), "start");
  assert.equal(winner(item, "display"), "grid");
  assert.equal(winner(item, "align-items"), "start", "the icon on the first line");
  assert.equal(winner(item, "align-content"), "center", "the whole centred in the item's height");
  assert.equal(winner(item, "line-height"), "18px", "a line as tall as the icon");
  host.remove();
});

test("L-106: our texts never speak in the first person plural (Italian and English)", () => {
  // Our keys (wiz…, cal tab texts added by this panel); calBrief_* belongs to the maintainer's view and is not ours.
  const ours = (key) => key.startsWith("wiz") || key.startsWith("calTab");
  const plural = { it: /\b\w+(iamo|eremo|iremo|aremo)\b|\bnoi\b|\bnostr\w*/i, en: /\b(we|we'll|us|our|ours|let's)\b/i };
  for (const language of ["it", "en"]) {
    const found = Object.entries(translations[language]).filter(([key, value]) => ours(key) && plural[language].test(String(value))).map(([key]) => key);
    assert.deepEqual(found, [], language);
  }
  assert.equal(translations.it.wiz_T196.split("\n**")[1], "Nel dubbio conviene ripartire da zero, con il percorso completo: tempi e letture col metro e alla fine potrai aggiungere il perfezionamento.");
});

test("L-112: the calibration's buttons ease their paint (200 ms, ease-out) between states, never their size; instant with reduced motion", async () => {
  const css = (await import("node:fs")).readFileSync(new URL("../../custom_components/myhome/frontend/panel/myhome-panel.css", import.meta.url), "utf8");
  const scope = ":is(.wiz-actions, .wiz-head, .cal-tab-top, .cal-row-actions, .cal-block-action, .cal-page-session) button";
  const eased = css.match(/@media \(prefers-reduced-motion: no-preference\) \{\s*([^{]+)\{\s*transition: ([^;]+);\s*\}\s*\}/);
  assert.ok(eased, "behind the no-preference guard");
  assert.equal(eased[1].trim(), scope, "the wizard's bar and header, the list's head, rows and blocks, a cover's page");
  const properties = eased[2].split(",").map((part) => part.trim());
  assert.deepEqual(properties, ["background-color 200ms ease-out", "border-color 200ms ease-out", "color 200ms ease-out", "opacity 200ms ease-out", "box-shadow 200ms ease-out"]);
  assert.ok(!/width|height|transform|outline|padding|margin|inset|top|left/.test(eased[2]), "no size, layout, transform or focus ring");
  assert.ok(css.includes(`@media (prefers-reduced-motion: reduce) {\n  ${scope} { transition: none; }\n}`), "reduced motion: instant");
  // The behaviour stays instant: the disabled attribute and the hold are untouched (L-108's rules), only the paint eases.
  const recolouring = css.indexOf("/* L-95 (Figma");
  assert.ok(recolouring < 0 || css.indexOf(eased[0]) < recolouring, "with the generic button rules, not in L-95's block (revertable alone)");
});

test("L-116: an emergency Stop is the last action, always drawn with the ①/② pair, 85 % of an action's height and a hairline after the action before it", async () => {
  const css = (await import("node:fs")).readFileSync(new URL("../../custom_components/myhome/frontend/panel/myhome-panel.css", import.meta.url), "utf8");
  // L-116b/c: the space before Stop is 85 % of an action's height (the bar's 12 px gap plus the rest), the hairline in its middle.
  assert.match(css, /\.wiz-actions \{ --wiz-action-height: 44px; --cal-stop-gap: calc\(0\.85 \* var\(--wiz-action-height\)\); \}/, "0.85 × the action's height");
  assert.match(css, /\.wiz-action \{ width: 100%; min-height: 44px;/, "the action's height it is computed from");
  // L-119b: the gap and the hairline belong to Stop's slot, whatever it holds (Stop or the finished line).
  assert.match(css, /\.wiz-actions > \.wiz-stop-slot \{ position: relative; box-sizing: border-box; display: grid; height: var\(--wiz-action-height\); min-width: 0; \}/,
    "the slot is the action's height in both states");
  assert.match(css, /\.wiz-actions > \.wiz-stop-slot:not\(:first-child\) \{ margin-top: calc\(var\(--cal-stop-gap\) - 12px\); \}/);
  assert.match(css, /\.wiz-actions > \.wiz-stop-slot:not\(:first-child\)::before \{ content: ""; position: absolute; inset-inline: 0; top: calc\(var\(--cal-stop-gap\) \/ -2 - 0\.5px\); height: 1px;\s*background: var\(--panel-border\); pointer-events: none; \}/,
    "a hairline in the middle of the space");
  assert.match(css, /\.wiz-actions > \.wiz-stop-slot > \.wiz-action \{ box-sizing: border-box; width: 100%; max-width: none; height: 100%; min-height: 0; margin: 0; \}/,
    "Stop and the finished line fill the slot");
  assert.doesNotMatch(css, /wiz-stop-slot[^{]*:has|wiz-stop-slot\.is-finished[^{]*\{[^}]*(height|margin)|wiz-action\.stop:not\(:first-child\)/,
    "the slot's height, gap and hairline never depend on what it holds");
  assert.match(css, /\.wiz-actions \{ display: grid; grid-template-columns: minmax\(0, 1fr\); gap: 12px;/, "the bar's own gap");
  assert.doesNotMatch(css, /\.wiz-action\.stop:not\(:last-child\)/, "never ahead of the actions (P4's layout is gone)");
  const { backend, wiz, press, startFor } = await mount();
  await startFor("cover.living_one");
  await press("begin"); await move(backend); await press("endpoint"); backend.stopped(); await tick();
  await press("ack:lift");
  const bar = () => [...wiz().querySelector(".wiz-actions").children].map((item) => item.firstElementChild?.dataset.wiz ? item.firstElementChild : item)
    .map((item) => [item.dataset.wiz, item.disabled]);
  assert.deepEqual(bar(), [["next", false], ["lift", true], ["stop", true]], "A-07 at the start: ① on, ② and Stop off");
  await press("next"); backend.started(); backend.emit(); await tick(6);
  assert.deepEqual(bar(), [["next", true], ["lift", false], ["stop", false]], "after ①: ① off, ② and Stop on");
  // Every bar with a Stop: Stop is the last of the actions (an outlined way out may follow, never a timing confirm).
  for (const screen of [{ step: "opening", phase: "opening" }, { step: "closing", phase: "closing" }, { step: "reset", phase: "closing" }]) {
    backend.session.step = screen.step; backend.session.phase = screen.phase; backend.emit(); await tick(4);
    const ids = [...wiz().querySelectorAll(".wiz-actions > .wiz-action, .wiz-actions > .wiz-stop-slot > .wiz-action")].map((item) => item.dataset.wiz).filter((id) => id !== "abort");
    if (ids.includes("stop")) assert.equal(ids.at(-1), "stop", `${screen.step}/${screen.phase}: ${ids}`);
  }
});
test("review P5: Save says what it saves and follows the choices; a reading above the travel is refused while typing", async () => {
  const t = (key) => translations.it[key] || key;
  const review = { mode: "geometry", phase: "review", values: { opening_time: 22, closing_time: 21 }, geometry: {}, travel_cm: 198, owner: true, sequence: 2,
    check: { deviation_cm: 1, passed: true, check_seconds: 10.5, measured_cm: 99, expected_cm: 98 } };
  const label = (cover, local = {}) => buildScreen({ t, language: "it", stage: "session", path: "A", cover, view: review, local })
    .actions.find((action) => action.id === "save").label;
  const first = { name: "Bagno", entity_id: "cover.bath", effective: {} };
  assert.equal(label(first), "Salva come «bagno»", "a new profile, by the name proposed");
  assert.equal(label(first, { profileName: "bagno_grande" }), "Salva come «bagno_grande»", "the name typed");
  assert.equal(label(first, { saveProfile: false }), "Salva solo per questa tapparella");
  const follower = { name: "Bagno", entity_id: "cover.bath", effective: {}, followers: [], profile: { name: "alte", provenance: { opening: { origin_entity_id: "cover.bath" } } } };
  assert.equal(label(follower, { saveProfile: true, saveChoice: "update" }), "Salva nel profilo «alte»");
  assert.equal(label(follower, { saveProfile: true, saveChoice: "new", profileName: "alte_2" }), "Salva come «alte_2»");
  assert.equal(buildScreen({ t, language: "it", stage: "session", path: "C", scope: "times_only", cover: first,
    view: { ...review, mode: "guided" }, local: { ackAscent: true, ackDescent: true } }).actions.find((action) => action.id === "save").label, "Salva solo per questa tapparella", "times only");
  // Live, as the far warning: a reading above the travel is refused while typing, the button off, nothing sent.
  const { backend, wiz, press, type, actions, startFor, code } = await mount();
  await startFor("cover.living_one");
  await press("begin"); await move(backend); await press("endpoint"); backend.stopped(); await tick();
  await press("ack:lift"); await press("next"); await move(backend, { seconds: 4.4 }); await press("lift"); backend.stopped(); await tick();
  await type("3"); await move(backend); await press("endpoint"); backend.stopped(); await tick();
  await press("next"); await move(backend, { seconds: 22.2 }); await press("endpoint"); backend.stopped(); await tick();
  await type("198"); await press("ack:ascent");
  await press("next"); await move(backend, { seconds: 21.7 }); await press("endpoint"); backend.stopped(); await tick();
  await press("ack:descent"); await press("ack:readings"); await move(backend, { auto: true, stop: true });
  assert.equal(code(), "A-18");
  const input = wiz().querySelector('[name="reading_cm"]'), button = () => wiz().querySelector('.wiz-actions [data-wiz="reading"]');
  const sent = actions().length;
  input.value = "205"; input.dispatchEvent(new Event("input", { bubbles: true })); await tick();
  assert.equal(wiz().querySelector("#wiz-field-error").hidden, false, "said while typing");
  assert.match(wiz().querySelector("#wiz-field-error").textContent, /more than the curtain travel/);
  assert.equal(button().disabled, true, "«Done, continue» waits");
  input.value = "97"; input.dispatchEvent(new Event("input", { bubbles: true })); await tick();
  assert.deepEqual([wiz().querySelector("#wiz-field-error").hidden, button().disabled], [true, false]);
  assert.equal(actions().length, sent, "nothing sent while typing");
  // On A-21 the label follows the name as it is typed, and the switch.
  await type("97"); await move(backend, { seconds: 24 }); await press("endpoint"); backend.stopped(); await tick();
  await move(backend, { auto: true, stop: true }); await type("101"); await press("check-start");
  assert.equal(code(), "A-21");
  const save = () => wiz().querySelector('.wiz-actions [data-wiz="save"]').textContent;
  assert.equal(save(), 'Save as "living_room_shutter_1"');
  const name = wiz().querySelector('[name="profile_name"]');
  name.value = "living_big"; name.dispatchEvent(new Event("input", { bubbles: true })); await tick();
  assert.equal(save(), 'Save as "living_big"');
  assert.equal(wiz().querySelector('.wiz-actions [data-wiz="save"]').disabled, false, "typing holds nothing back");
  const toggle = wiz().querySelector('[name="save_profile"]');
  toggle.checked = false; toggle.dispatchEvent(new Event("change", { bubbles: true })); await tick();
  assert.equal(save(), "Save for this cover only");
});

for (const [entity, variant] of [["cover.living_one", "A-21 throughout"], ["cover.kitchen_two", "A-21c ↔ A-21d: the switch changes the code"]]) {
  test(`L-117/L-117b: the same screen drawn again keeps its scroll and the field in use; a new screen starts from the top (${variant})`, async () => {
    // jsdom has no layout: each element keeps the scrollTop it is given, as a browser does; the wizard finds the scrolling
    // elements itself (its content, and every composed parent up to the page, through the panel's shadow root).
    const tops = new WeakMap(), proto = dom.window.HTMLElement.prototype, original = Object.getOwnPropertyDescriptor(dom.window.Element.prototype, "scrollTop");
    Object.defineProperty(proto, "scrollTop", { configurable: true, get() { return tops.get(this) ?? 0; }, set(value) { tops.set(this, value); } });
    try {
      const { backend, wiz, press, type, startFor, code, panel } = await mount();
      await startFor(entity);
      await press("begin"); await move(backend); await press("endpoint"); backend.stopped(); await tick();
      await press("ack:lift"); await press("next"); await move(backend, { seconds: 4.4 }); await press("lift"); backend.stopped(); await tick();
      await type("3"); await move(backend); await press("endpoint"); backend.stopped(); await tick();
      await press("next"); await move(backend, { seconds: 22.2 }); await press("endpoint"); backend.stopped(); await tick();
      await type("198"); await press("ack:ascent");
      await press("next"); await move(backend, { seconds: 21.7 }); await press("endpoint"); backend.stopped(); await tick();
      await press("ack:descent"); await press("ack:readings"); await move(backend, { auto: true, stop: true });
      await type("97"); await move(backend, { seconds: 24 }); await press("endpoint"); backend.stopped(); await tick();
      await move(backend, { auto: true, stop: true }); await type("101"); await press("check-start");
      assert.match(code(), /^A-21[cd]?$/);
      const first = code();
      // The scrolling elements: the wizard's content and a composed parent (the panel, host of the shadow root).
      wiz().querySelector(".wiz-main").scrollTop = 420; panel.scrollTop = 150;
      const toggle = wiz().querySelector('[name="save_profile"]');
      toggle.focus(); toggle.checked = !toggle.checked; toggle.dispatchEvent(new Event("change", { bubbles: true })); await tick();
      if (entity === "cover.kitchen_two") assert.notEqual(code(), first, "the switch changed the screen's code…");
      assert.deepEqual([wiz().querySelector(".wiz-main").scrollTop, panel.scrollTop], [420, 150], "…and the place stays");
      assert.equal(wiz().getRootNode().activeElement?.name, "save_profile", "the switch keeps the focus, the title does not take it");
      await new Promise((resolve) => setTimeout(resolve, 30));
      assert.deepEqual([wiz().querySelector(".wiz-main").scrollTop, panel.scrollTop], [420, 150], "still there a frame later");
      // A view of Home Assistant that changes a part of the same screen (a value of its table): the scroll stays too.
      backend.session.values.opening_time += 0.1; backend.emit(); await tick(6);
      assert.match(code(), /^A-21[cd]?$/);
      assert.deepEqual([wiz().querySelector(".wiz-main").scrollTop, panel.scrollTop], [420, 150], "same screen, same scroll");
      // A new screen starts from the top of its content, its title focused.
      const sw = wiz().querySelector('[name="save_profile"]');
      if (!sw.checked) { sw.checked = true; sw.dispatchEvent(new Event("change", { bubbles: true })); await tick(); }
      if (wiz().querySelector('[name="save_choice"][value="new"]')) { const nw = wiz().querySelector('[name="save_choice"][value="new"]'); nw.checked = true; nw.dispatchEvent(new Event("change", { bubbles: true })); await tick(); }
      const name = wiz().querySelector('.wiz-frame [name="profile_name"]');
      if (name) { name.value = `l117_${entity.slice(6)}`; name.dispatchEvent(new Event("input", { bubbles: true })); await tick(); }
      await press("save"); await tick(6);
      assert.equal(code(), "A-34");
      assert.equal(wiz().querySelector(".wiz-main").scrollTop, 0, "a new screen: from the top");
      await tick(2);
      assert.match(wiz().getRootNode().activeElement?.className || "", /wiz-title/);
    } finally {
      if (original) Object.defineProperty(proto, "scrollTop", original); else delete proto.scrollTop;
    }
  });
}
test("R-8: what the tab hangs on the window is removed when it is cleared, and added once when it shows again", async () => {
  const view = dom.window;
  const active = new Map();
  const add = view.addEventListener.bind(view), remove = view.removeEventListener.bind(view);
  view.addEventListener = (type, handler, options) => { if (type === "orientationchange") active.set(handler, true); return add(type, handler, options); };
  view.removeEventListener = (type, handler, options) => { if (type === "orientationchange") active.delete(handler); return remove(type, handler, options); };
  try {
    const { panel } = await mount();
    const tab = panel._calibrationTab;
    assert.equal(active.size, 1, "one listener while the tab shows");
    const context = tab._context;
    tab.clear();
    assert.equal(active.size, 0, "none once cleared");
    // A rebuilt shell gives the tab a new host: still one listener, not one more per host.
    const host = context.host.ownerDocument.createElement("div"); context.host.after(host);
    tab.show({ ...context, host }); await tick(4);
    tab.show({ ...context, host }); await tick(4);
    assert.equal(active.size, 1);
    tab.requestWizard("one", "cover.living_one");
    tab.clear();
    assert.equal(active.size, 0);
    assert.equal(tab._pendingWizard, null, "a wizard asked for before the clear never opens later by surprise");
  } finally {
    view.addEventListener = add; view.removeEventListener = remove;
  }
});

test("R-10: closing the wizard gives the focus back to what opened it", async () => {
  const { tab, wiz, panel } = await mount();
  const opener = tab().querySelector('[data-action="cal-tab-measure"]');
  opener.focus(); opener.click(); await tick();
  assert.ok(wiz());
  panel._calibrationTab._wizard._act("close"); await tick(6);
  assert.equal(wiz(), null);
  const active = tab().getRootNode().activeElement;
  assert.equal(active?.dataset.action, "cal-tab-measure", "the header's «Calibrate a cover» has the focus again");
});

test("L-84/L-85/L-88: the phone bar on the steps strip's surface, no bar on the motor box, a note's bar in its text's colour", () => {
  assert.match(cascadeCss, /\.wiz-actions \{ padding: 12px calc\(16px \+ var\(--cal-safe-right\)\) calc\(12px \+ var\(--cal-safe-bottom\)\) calc\(16px \+ var\(--cal-safe-left\)\); border-top: 1px solid var\(--panel-border\); background: var\(--panel-surface-2\); \}/, "L-84");
  const doc = dom.window.document;
  const host = doc.createElement("div"); host.className = "cal-tab"; doc.body.append(host);
  host.innerHTML = '<div class="wiz-stopwatch"></div>';
  assert.equal(winner(host.firstElementChild, "border-inline-start"), "", "L-85: the motor box is not a note");
  host.remove();
  for (const theme of ["light", "dark"]) {
    const box = doc.createElement("div"); box.className = "cal-tab"; box.dataset.theme = theme; doc.body.append(box);
    box.innerHTML = '<p class="cal-note"></p><p class="cal-note is-success"></p><p class="notice error"></p><p class="cal-note is-warning"></p>';
    const [info, success, error, warning] = box.children;
    const bar = (note) => winner(note, "border-inline-start-color") || "var(--note-tone)";
    if (theme === "light") {
      // L-127: the tone and the text are the same token for info, success and error.
      assert.equal(winner(info, "--note-tone"), winner(info, "--note-text"), "light info: bar as text");
      assert.equal(winner(success, "--note-tone"), winner(success, "--note-text"), "light success: bar as text");
      assert.equal(winner(error, "--note-text"), "var(--panel-error)");
      assert.equal(winner(error, "border-inline-start"), "2px solid var(--note-tone)");
    } else {
      assert.equal(bar(info), "var(--note-tone)", "dark: the tone's colour, as before");
    }
    assert.equal(winner(warning, "border-inline-start"), "2px solid var(--note-tone)", `${theme}: a warning's bar yellow`);
    box.remove();
  }
});

test("L-18: a tap on the cover already chosen in A-01 un-chooses it", async () => {
  const { wiz, tab, pickCover, code } = await mount();
  tab().querySelector('[data-action="cal-tab-measure"]').click(); await tick();
  await pickCover("cover.living_one");
  assert.equal(wiz().querySelector('[data-wiz="choose"]').disabled, false);
  const radio = [...wiz().querySelectorAll('.wiz-picker input[name="cover"]')].find((input) => JSON.parse(input.value)[1] === "cover.living_one");
  radio.click(); await tick();
  assert.equal(code(), "A-01");
  assert.equal([...wiz().querySelectorAll('.wiz-picker input[name="cover"]')].some((input) => input.checked), false, "nothing chosen");
  assert.equal(wiz().querySelector('[data-wiz="choose"]').disabled, true, "Continue off again");
});

test("L-16: «Open the cover's details» closes the wizard and opens the cover's row in the list, never the maintainer's dialog", async () => {
  const context = await mount();
  await runToSaved(context);
  context.wiz().querySelector('[data-wiz="open-cover"]').click(); await tick(8);
  assert.equal(context.wiz(), null, "the wizard is closed");
  const row = [...context.tab().querySelectorAll(".cal-row")].find((item) => item.dataset.entity === "cover.living_one");
  assert.equal(row.classList.contains("is-expanded"), true, "its row opened in place");
  assert.equal(context.panel.shadowRoot.querySelector("dialog.cover-profile-dialog[open]"), null, "no maintainer's dialog");
});

test("L-47 (revised): every bar keeps the primary and up to two secondaries in view; with more, the primary and «Other options»; red always in view, last", async () => {
  const { overflow } = await import("../../custom_components/myhome/frontend/panel/panel-calibration-wizard.js");
  const a = (id, kind = "secondary") => ({ id, kind });
  const ids = ({ shown, more, red }) => [shown.map((x) => x.id), more.map((x) => x.id), red.map((x) => x.id)];
  assert.deepEqual(ids(overflow([a("p", "primary"), a("b"), a("c"), a("d"), a("e")])), [["p"], ["b", "c", "d", "e"], []], "A-34: the primary and the disclosure");
  assert.deepEqual(ids(overflow([a("p", "primary"), a("b"), a("c")])), [["p", "b", "c"], [], []], "three: all in view");
  assert.deepEqual(ids(overflow([a("p", "primary"), a("b"), a("s", "stop")])), [["p", "b"], [], ["s"]], "Stop in view, last");
  assert.deepEqual(ids(overflow([a("p", "primary"), a("s", "danger-outline"), a("c"), a("d"), a("e")])), [["p"], ["c", "d", "e"], ["s"]], "red never in the disclosure");
  // L-125: an action marked visible stays beside the primary; three in view at most.
  const v = (id) => ({ id, kind: "secondary", visible: true });
  assert.deepEqual(ids(overflow([a("p", "primary"), v("b"), a("c"), a("d")])), [["p", "b"], ["c", "d"], []]);
  assert.deepEqual(ids(overflow([a("p", "primary"), a("b"), v("c"), v("d"), v("e")])), [["p", "c", "d"], ["b", "e"], []], "never more than three");
});

test("A-34 offers fine-tuning once the calibration is saved: path C on the same cover, from the values just written", async () => {
  const { backend, wiz, code, press, type, actions, startFor } = await mount();
  await startFor("cover.living_one");
  await press("begin"); await move(backend); await press("endpoint"); backend.stopped(); await tick();
  await press("ack:lift"); await press("next"); await move(backend, { seconds: 4.4 }); await press("lift"); backend.stopped(); await tick();
  await type("3"); await move(backend); await press("endpoint"); backend.stopped(); await tick();
  await press("next"); await move(backend, { seconds: 22.2 }); await press("endpoint"); backend.stopped(); await tick();
  await type("198"); await press("ack:ascent");
  await press("next"); await move(backend, { seconds: 21.7 }); await press("endpoint"); backend.stopped(); await tick();
  await press("ack:descent"); await press("ack:readings"); await move(backend, { auto: true, stop: true });
  await type("97"); await move(backend, { seconds: 24 }); await press("endpoint"); backend.stopped(); await tick();
  await move(backend, { auto: true, stop: true }); await type("101"); await press("check-start");
  assert.equal(code(), "A-21");
  assert.equal(wiz().querySelector('[data-wiz="refine"]'), null, "the summary only saves");
  await press("save");
  assert.equal(code(), "A-34");
  // Review P2: «Back to the list» the primary, then the new profile to other covers, another cover, this cover's details,
  // and fine-tuning last, a secondary (the previews on here).
  const kinds = [...wiz().querySelectorAll(".wiz-actions .wiz-action")].map((item) => [item.dataset.wiz, item.className.replace("wiz-action ", "")]);
  assert.deepEqual(kinds, [["close", "primary"], ["assign-profile", "secondary"], ["another", "secondary"], ["open-cover", "secondary"], ["refine", "secondary"]]);
  assert.equal(wiz().querySelector('.wiz-actions [data-wiz="close"]').textContent, "Back to the list");
  // In a one-column bar the secondaries are under «Other options ▾» (CSS hides them there); L-125: a profile created
  // in this session keeps «Assign this profile…» beside the primary. In the columns the order is the same as before.
  assert.equal(wiz().querySelector(".wiz-main .wiz-action"), null, "nothing in the text column");
  assert.deepEqual([...wiz().querySelectorAll(".wiz-actions .wiz-more .wiz-more-list .wiz-action")].map((item) => item.dataset.wiz), ["another", "open-cover", "refine"]);
  const order = [...wiz().querySelector(".wiz-actions").children].map((item) => item.dataset.wiz || item.className);
  assert.deepEqual(order, ["close", "assign-profile", "wiz-more"], "the primary, the new profile to other covers, then the disclosure");
  const toggle = wiz().querySelector(".wiz-actions .wiz-more-toggle");
  assert.deepEqual([toggle.textContent.trim(), toggle.getAttribute("aria-expanded")], ["Other options", "false"]);
  toggle.click(); await tick();
  assert.deepEqual([wiz().querySelector(".wiz-more").classList.contains("is-open"), wiz().querySelector(".wiz-more-toggle").getAttribute("aria-expanded")], [true, "true"]);
  const css = (await import("node:fs")).readFileSync(new URL("../../custom_components/myhome/frontend/panel/myhome-panel.css", import.meta.url), "utf8");
  assert.equal((css.match(/\.wiz-more:not\(\.is-open\) > \.wiz-more-list \{ display: none; \}/g) || []).length, 2, "hidden behind the toggle in both bars");
  assert.match(css, /\.wiz-more-toggle \{ display: none; \}\n\.wiz-more-list \{ display: grid;/, "all shown, as buttons, in the columns");
  assert.equal(wiz().querySelector('[data-wiz="refine"]').textContent, "Continue with fine-tuning");
  assert.equal(wiz().querySelector("#wiz-note-refine"), null, "no note under the button");
  assert.equal(wiz().querySelector('[data-wiz="refine"]').hasAttribute("aria-describedby"), false);
  const paragraphs = [...wiz().querySelectorAll(".wiz-body p")].map((item) => item.textContent);
  assert.equal(paragraphs.length, 2, "the saved text, then what fine-tuning does");
  assert.equal(wiz().querySelectorAll(".wiz-body p")[1].querySelector("strong").textContent, "Want to fine-tune the calibration?", "a bold lead-in");
  assert.equal(paragraphs[1],
    "Want to fine-tune the calibration? Four tape readings at a quarter and three quarters of the travel, plus a final check, will make the position estimate more precise along the whole travel. A few minutes; the cover will move.");
  const finetune = wiz().querySelector('.wiz-steps li[data-step-state]:last-of-type');
  assert.deepEqual([finetune.querySelector(".wiz-step-label").textContent, finetune.dataset.stepState], ["Fine-tuning (optional)", "future"], "still ahead, not ticked");
  assert.deepEqual([...wiz().querySelectorAll(".wiz-steps ol > li")].slice(0, -1).map((item) => item.dataset.stepState), Array(7).fill("done"));
  const sent = actions().length, saves = backend.calls.filter((message) => message.action === "save").length;
  await press("refine");
  // Straight to the quarter readings (A-22…), in the same wizard: no C-01/C-02 in between.
  assert.match(code(), /^A-2[23]$/, "fine-tuning starts at once (its first positioning may already be over)");
  assert.equal(wiz().querySelector(".wiz-cover").textContent, "Living room shutter 1", "on the same cover");
  assert.equal(wiz().querySelector(".wiz-steps-line").textContent, "Fine-tuning · 7 of 7", "the stepper goes on at Fine-tuning, the count unchanged (N-10)");
  await settle();
  assert.equal(code(), "A-23");
  for (const reading of ["52", "141", "146", "48"]) { await type(reading); await settle(); }
  assert.equal(code(), "A-31");
  // L-30: the hint gives a range; the expected height is the preview's own.
  const expected = Math.round(document.querySelector("myhome-panel")._calibrationTab._wizard._gateway.view.expected_cm);
  await type(String(expected + 6));
  assert.equal(code(), "A-32b");
  assert.equal(wiz().querySelector('[data-wiz="continue_anyway"]').classList.contains("secondary"), true, "L-47: a secondary (it loses nothing; no text links outside «Other options»)");
  await press("repeat_check"); await settle();
  await type(String(expected + 1));
  assert.equal(code(), "A-32");
  await press("accept");
  assert.equal(code(), "A-33");
  // L-46: fine-tuning is a preview: its summary has no Save, only the notice and Close (or another cover).
  assert.deepEqual([...wiz().querySelectorAll(".wiz-actions .wiz-action")].map((button) => button.dataset.wiz), ["close", "another"]);
  assert.match(wiz().querySelector(".wiz-result-notices").textContent, /^Preview: in this version this path saves nothing yet/);
  assert.equal(wiz().querySelector(".wiz-profile-block"), null);
  assert.equal(actions().length, sent, "the fine-tuning preview sent nothing");
  assert.equal(backend.calls.filter((message) => message.action === "save").length, saves, "and saved nothing more");
});

test("A-34's Assign this profile to other covers opens the maintainer's Assign dialog on the profile just created", async () => {
  const context = await mount();
  const { backend, root, wiz, code } = context;
  await runToSaved(context);
  const button = wiz().querySelector('[data-wiz="assign-profile"]');
  assert.equal(button.textContent, "Assign this profile to other covers");
  // Home Assistant has written the new profile: the tab finds it by name in the overview.
  backend.overview.profiles.push({ id: "fresh", name: "living_room_shutter_1", opening_time: 22.2, closing_time: 21.7, reference_travel_cm: 198,
    geometry: { slat_time_s: 4.4, opening_roll: 1.83, closing_roll: 1.91 }, assigned_to: ["cover.living_one"], provenance: {} });
  button.click(); await settle();
  const dialog = root.querySelector("#dialog-host dialog");
  assert.ok(dialog?.open, "the dialog is open");
  assert.equal(dialog.querySelector("#catalogue-title").textContent, "Assign profile to covers");
  assert.ok(backend.calls.some((message) => message.type === "myhome/cover_profiles/overview"));
  assert.equal(code(), "A-34", "the wizard stays under it");
  dialog.querySelector("#catalogue-close").click(); await tick();
  assert.equal(root.querySelector("#dialog-host dialog"), null);
});

test("the tab's Fine-tune asks first what is wrong (C-01, C-02), and the way back from C-01 is the list", async () => {
  const { tab, wiz, code, press } = await mount();
  const refine = () => [...tab().querySelectorAll('[data-action="cal-tab-refine"]')].find((button) => button.dataset.id.includes("cover.kitchen_two"));
  refine().click(); await tick();
  assert.equal(code(), "C-01");
  assert.equal(wiz().querySelector(".wiz-cover").textContent, "Kitchen shutter 2");
  await press("current");
  assert.equal(code(), "C-02");
  wiz().querySelector('[data-wiz="back"]').click(); await tick();
  assert.equal(code(), "C-01");
  wiz().querySelector('[data-wiz="back"]').click(); await tick();
  assert.equal(wiz(), null);
});

test("the profile dialog offers the new wizard next to the existing measurement", async () => {
  const { root, backend, wiz, code } = await mount();
  backend.hass.callWS = ((original) => async (message) => message.type === "myhome/cover_profiles/read"
    ? { revision: 7, profiles: [], assigned_profile_id: null, writable: true, effective_opening_time: 22, effective_closing_time: 21.7, calibration: null }
    : original(message))(backend.hass.callWS);
  root.querySelector('[data-action="cover-view"][data-id="devices"]').click(); await tick();
  root.querySelector('[data-action="cover-profile"][data-id="cover.living_one"]').click(); await tick(6);
  assert.ok(root.querySelector("#profile-calibrate"), "the existing measurement stays");
  const entry = root.querySelector("#profile-wizard");
  assert.equal(entry.textContent, "New guided measurement");
  entry.click(); await tick(10);
  assert.equal(root.querySelector(".cover-profile-dialog"), null);
  assert.equal(root.querySelector('[data-action="cover-view"][data-id="calibration"]').getAttribute("aria-pressed"), "true");
  assert.equal(code(), "A-02");
  assert.equal(wiz().querySelector(".wiz-cover").textContent, "Living room shutter 1");
});

test("L-58b: an armed «Confirm take control» counts down and goes back after 10 s, or when the owner shows another step", async (t) => {
  const view = { phase: "briefing", step: "half_open", reading_kind: "half_open", values: { opening_time: 22, closing_time: 21 },
    screen: { code: "A-15", path: "A", scope: null, local: { ackLift: true, ackAscent: true } } };
  // Fake clocks from the start, so every interval the wizard keeps is a fake one.
  t.mock.timers.enable({ apis: ["setInterval", "Date"], now: Date.now() });
  const { backend, tab, wiz } = await mount({ session: resumed(view), owner: false });
  tab().querySelector('[data-action="cal-tab-session"]').click(); await tick(6);
  const take = () => wiz().querySelector('[data-wiz="take"]');
  // (b) 10 s without confirming.
  take().click(); await tick();
  assert.equal(take().textContent, "Confirm take control · 10 s");
  const armed = take();
  t.mock.timers.tick(4000); await tick();
  assert.equal(take().textContent, "Confirm take control · 6 s", "it counts down");
  assert.equal(take(), armed, "only the label changes: the button is not drawn again (no hold, no lost tap)");
  t.mock.timers.tick(6000); await tick();
  assert.equal(take().textContent, "Take control", "back after 10 s");
  // (a) the owner shows another step.
  take().click(); await tick();
  assert.match(take().textContent, /^Confirm take control/);
  backend.session.screen = { ...view.screen, code: "A-16", local: { ...view.screen.local, ackDescent: true } };
  backend.emit(); await tick(6);
  assert.equal(take().textContent, "Take control", "back when the step changes");
  // Confirmed: the timer is gone with the confirmation.
  take().click(); await tick();
  take().click(); await tick(8);
  assert.equal(wiz().querySelector(".wiz-readonly"), null, "now the owner");
  t.mock.timers.tick(20000); await tick();
  assert.equal(wiz().querySelector(".wiz-readonly"), null, "nothing comes back");
  t.mock.timers.reset();
});

test("L-61: a reader's header says «Stop following» (secondary) and leaves without a dialog; the owner's stays red; it follows a claim", async () => {
  const view = { phase: "briefing", step: "half_open", reading_kind: "half_open", values: { opening_time: 22, closing_time: 21 },
    screen: { code: "A-15", path: "A", scope: null, local: { ackLift: true, ackAscent: true } } };
  const { backend, panel, tab, wiz } = await mount({ session: resumed(view), owner: false });
  tab().querySelector('[data-action="cal-tab-session"]').click(); await tick(6);
  const head = () => [...wiz().querySelectorAll(".wiz-head button:not(.icon-button)")].map((button) => [button.className, button.textContent]);
  assert.deepEqual(head(), [["wiz-unfollow", "Stop following"]], "reader: secondary, no red");
  assert.equal(wiz().querySelector(".wiz-x").getAttribute("aria-label"), "Stop following", "the phone's ✕ says the same");
  assert.equal(panel._beforeLeave?.(), null, "no exit guard armed for a reader");
  // Taken: the owner's red button; taken back by the other device: the reader's again.
  wiz().querySelector('[data-wiz="take"]').click(); await tick();
  wiz().querySelector('[data-wiz="take"]').click(); await tick(8);
  assert.deepEqual(head(), [["wiz-discard", "Discard calibration"]], "owner: unchanged");
  const owned = backend.view;
  backend.view = (extra = {}) => owned({ owner: false, read_only: true, ...extra });
  backend.emit(); await tick(6);
  assert.deepEqual(head(), [["wiz-unfollow", "Stop following"]], "control taken back elsewhere");
  // Stop following: straight back to the list, no dialog, nothing cancelled; the row can follow again.
  const sent = backend.calls.length;
  wiz().querySelector(".wiz-unfollow").click(); await tick(8);
  assert.equal(wiz(), null, "back to the list");
  assert.equal(backend.calls.slice(sent).some((call) => call.action === "cancel"), false, "nothing cancelled");
  assert.notEqual(backend.session.phase, "cancelled");
  assert.ok(tab().querySelector('[data-action="cal-tab-session"]'), "the session's action still there");
});

test("L-61: the header follows the role even when nothing else on the screen changes (a run under way)", async () => {
  const { backend, tab, wiz } = await mount({ session: resumed({ phase: "opening", step: "opening", reading_kind: "travel", values: {}, _started: 0 }) });
  tab().querySelector('[data-action="cal-tab-session"]').click(); await tick(6);
  const head = () => [...wiz().querySelectorAll(".wiz-head button:not(.icon-button)")].map((button) => button.className);
  assert.deepEqual(head(), ["wiz-discard"], "owner");
  const owned = backend.view;
  backend.view = (extra = {}) => owned({ owner: false, read_only: true, ...extra });
  backend.emit(); await tick(6);
  assert.deepEqual(head(), ["wiz-unfollow"], "taken by another device mid-run");
});

test("L-58: a reader stops a moving cover from its bar", async () => {
  const { backend, tab, wiz } = await mount({ session: resumed({ phase: "opening", step: "opening", reading_kind: "travel", values: {}, _started: 0 }), owner: false });
  tab().querySelector('[data-action="cal-tab-session"]').click(); await tick(6);
  assert.deepEqual([...wiz().querySelectorAll(".wiz-actions .wiz-action")].map((button) => [button.dataset.wiz, button.className]), [["stop", "wiz-action stop"]]);
  wiz().querySelector('.wiz-actions [data-wiz="stop"]').click(); await tick(6);
  assert.ok(backend.calls.some((message) => message.action === "stop"), "Stop reaches Home Assistant from a reader");
  assert.equal(backend.session.phase, "interrupted");
});

test("L-58: a reader's bar while the cover moves: the note, Take control and Stop only", () => {
  const t = (key) => translations.en[key] || key;
  const view = { mode: "geometry", phase: "opening", step: "opening", values: {}, samples: {}, readings: {}, slats: true, read_only: true, owner: false, owner_present: true };
  const screen = buildScreen({ t, stage: "session", path: "A", view, cover: { name: "Kitchen shutter 2" } });
  assert.equal(screen.code, "A-11");
  assert.deepEqual(screen.actions.map((action) => [action.id, action.kind, !!action.disabled]), [["stop", "stop", false]]);
  assert.equal(screen.readOnly.text, "Another device is guiding the calibration.");
  assert.equal(screen.readOnly.take, "Take control");
});

test("a check run is shown like an intermediate run, two stages and its own bar, Stop only while it moves, then asks for its tape reading (L-44)", () => {
  const t = (key) => translations.en[key] || key;
  const view = { mode: "geometry", phase: "opening", step: "check", values: { opening_time: 22, closing_time: 21 }, samples: { lift: 4 },
    readings: {}, travel_cm: 110, slats: true, owner: true, sequence: 9, check: { direction: "opening", target_cm: 82.5, check_seconds: null, planned_seconds: 12.4 } };
  const screen = (extra) => buildScreen({ t, stage: "session", path: "A", view: { ...view, ...extra }, elapsed: 6.4, local: { ackReadings: true }, cover: { name: "Kitchen shutter 2" } });
  const running = screen({});
  assert.equal(running.code, "A-30");
  assert.equal(running.stopwatch, null, "Home Assistant stops the check run: no elapsed time");
  assert.equal(running.illustration, null, "a wait has no drawing");
  // L-119: A-30 is one step: the first leg's bar, its end stop off (already confirmed), Stop last.
  assert.deepEqual(running.actions.map((action) => [action.id, !!action.disabled]), [["endpoint", true], ["stop", false]]);
  assert.deepEqual(running.wait.stages.map((stage) => [stage.text, stage.state]), [["1/2 Down to the bottom — about 22 s", "done"], ["2/2 Rises to half the travel — about 13 s", "current"]]);
  assert.equal(running.wait.progress.value != null, true, "a determinate bar for the planned run");
  assert.deepEqual(screen({ phase: "briefing" }).actions.map((action) => [action.id, !!action.disabled]), [["next", false], ["endpoint", true], ["stop", true]],
    "not started by itself: its start first; no Stop to press while nothing moves");
  assert.equal(screen({ phase: "geometry_wait_stop" }).stopwatch, null);
  const reading = screen({ phase: "reading", reading_kind: "check" });
  assert.equal(reading.code, "A-31");
  assert.equal(reading.title, "Check the cover");
  assert.deepEqual([reading.field.min, reading.field.max], [0.1, 110]);
  assert.equal(reading.field.hint, "About 83 cm expected: a few centimetres of difference is normal. Enter what the tape says; a comma or a point is fine.", "L-44: the expected height, not a range");
  assert.deepEqual(reading.actions.map((action) => action.id), ["reading", "repeat"]);
});

test("L-9: an intermediate reading names the expected height in Italian, refuses only more than the travel, and reports the far ones", () => {
  const t = (key) => translations.it[key] || key;
  const view = { mode: "geometry", phase: "reading", step: "half_close", values: { opening_time: 22, closing_time: 21 }, samples: { lift: 4 },
    readings: { gap: 3 }, geometry: { slat_time_s: 4 }, travel_cm: 198, slats: true, owner: true, sequence: 3,
    reading_range: { min_cm: 0.1, max_cm: 201 }, reading_band: { min_cm: 46.3, max_cm: 118.8, expected_cm: 82.55, model_min_cm: 66.1, model_max_cm: 99.1 } };
  const screen = buildScreen({ t, language: "it", stage: "session", path: "A", view, cover: { name: "Kitchen 2" },
    status: { error: { code: "reading_over_travel", reading: 205.5 } } });
  assert.equal(screen.code, "A-20");
  assert.equal(screen.field.hint, "Di solito fra 66 e 100 cm: qualche centimetro di differenza è normale. Scrivi quello che dice il metro; virgola o punto vanno bene.", "L-30");
  assert.deepEqual([screen.field.min > 0, screen.field.max > 201, screen.field.max < 201.001], [true, true, true], "the physical limits");
  assert.equal(screen.field.band.warning, "La lettura è lontana da quella attesa: ricontrolla il riferimento. Se è giusta, continua.");
  assert.equal(screen.notices[0].text, "La lettura (205,5 cm) è più della corsa del telo (198 cm): ricontrolla il metro e il punto da cui misuri.");
  assert.equal(screen.stopwatch, null, "no time is shown for a reading");
  const typed = buildScreen({ t, language: "it", stage: "session", path: "A", view, local: { fieldError: 230 }, cover: { name: "Kitchen 2" } });
  assert.equal(typed.field.error, "La lettura (230 cm) è più della corsa del telo (198 cm): ricontrolla il metro e il punto da cui misuri.");
  // Kept although far: L-87, the summary says so only when the check did not answer for it.
  const summary = (check) => buildScreen({ t, language: "it", stage: "session", path: "A", cover: { name: "Kitchen 2" }, local: { checkSkipped: !check }, view: { ...view, phase: "review", reading_range: null, reading_band: null,
    geometry: { slat_time_s: 4, opening_roll: 5, closing_roll: 1.9 }, readings: { gap: 3, half_open: 40, half_close: 97 },
    reading_discrepancy: { half_open: { reading_cm: 40, expected_cm: 82.55, min_cm: 46.3, max_cm: 118.8 } }, check } })
    .notices.filter((notice) => notice.tone === "warning").map((notice) => notice.text);
  assert.deepEqual(summary({ gap_cm: 1, passed: true, check_seconds: 10.5 }), [], "a check that passed has answered");
  assert.deepEqual(summary({ gap_cm: 6, passed: false, check_seconds: 10.5 }),
    ["Metà tempo salita: la lettura (40 cm) era lontana da quella attesa (circa 83 cm) e questo potrebbe spiegare lo scarto della verifica."]);
  assert.deepEqual(summary(null), // saved without the check (L-78)
    ["Metà tempo salita: la lettura (40 cm) era lontana da quella attesa (circa 83 cm): senza verifica non è possibile dire se le posizioni sono giuste."]);
  const running = buildScreen({ t, language: "it", stage: "session", path: "A", view: { ...view, phase: "closing", reading_range: null, reading_band: null }, elapsed: 3.2,
    local: { ackReadings: true }, cover: { name: "Kitchen 2" } });
  assert.equal(running.code, "A-19");
  assert.equal(running.stopwatch, null, "an intermediate run shows no elapsed time: Home Assistant stops it");
});

test("each mockup gets its drawing by code, always under the title and before the text (L-68), none elsewhere", () => {
  const t = (key) => translations.en[key] || key;
  const base = { mode: "geometry", values: { opening_time: 22, closing_time: 21 }, samples: { lift: 4 }, readings: { gap: 3 },
    geometry: { slat_time_s: 4 }, travel_cm: 110, slats: true, owner: true, sequence: 4 };
  const drawn = (view, local = {}, path = "A") => {
    const screen = buildScreen({ t, stage: "session", path, scope: path === "C" ? "times_and_rolls" : null, view: { ...base, ...view }, local,
      elapsed: 2, cover: { name: "Kitchen shutter 2" } });
    return [screen.code, screen.illustration && `${screen.illustration.id} ${screen.illustration.position}`];
  };
  assert.deepEqual(drawn({ step: "home", phase: "closing" }), ["A-05", "window-closed before-body"]);
  assert.deepEqual(drawn({ step: "lift", phase: "briefing" }), ["A-06", "two-runs before-body"]);
  assert.deepEqual(drawn({ step: "lift", phase: "briefing" }, { ackLift: true }), ["A-07", "lift-sequence before-body"]);
  assert.deepEqual(drawn({ step: "lift", phase: "reading", reading_kind: "lift" }), ["A-08", "lift-zoom before-body"]);
  assert.deepEqual(drawn({ step: "reset", phase: "starting_close" }), ["A-09", null], "A-09's frame shows only the wait box");
  assert.deepEqual(drawn({ step: "reset", phase: "closing" }), ["A-10", "window-closed before-body"], "the closed window, as on A-05");
  assert.deepEqual(drawn({ step: "reset", phase: "closing" }, {}, "C"), ["C-10", "window-closed before-body"]);
  assert.deepEqual(drawn({ step: "opening", phase: "opening" }), ["A-11", "ascent-sequence before-body"]);
  assert.deepEqual(drawn({ step: "opening", phase: "reading" }, { travelTyped: 198 }), ["A-12", null]);
  assert.deepEqual(drawn({ step: "opening", phase: "reading" }), ["A-13", "curtain-open-tape before-body"]);
  assert.deepEqual(drawn({ step: "closing", phase: "closing" }), ["A-14", "descent-sequence before-body"]);
  assert.deepEqual(drawn({ step: "half_open", phase: "briefing" }), ["A-15", null]);
  assert.deepEqual(drawn({ step: "half_open", phase: "briefing" }, { ackDescent: true }), ["A-16", "curtain-partial-tape before-body"]);
  assert.deepEqual(drawn({ step: "half_open", phase: "opening" }, { ackDescent: true, ackReadings: true }), ["A-17", null]);
  assert.deepEqual(drawn({ step: "half_open", phase: "reading" }, { ackDescent: true, ackReadings: true }), ["A-18", "curtain-partial-tape before-body"]);
  assert.deepEqual(drawn({ step: "top", phase: "opening" }, { ackReadings: true }), ["A-19", null]);
  assert.deepEqual(drawn({ step: "top", phase: "opening" }), ["A-09", null], "the return to the top reuses A-09: no drawing either");
  assert.deepEqual(drawn({ step: "half_close", phase: "reading" }, { ackReadings: true }), ["A-20", "curtain-partial-tape before-body"]);
  assert.deepEqual(drawn({ step: "closing", phase: "closing" }, {}, "C"), ["C-14", "descent-sequence before-body"], "clones follow their A screen");
  assert.deepEqual(drawn({ phase: "review" }), ["A-21v", "curtain-partial-tape before-body"], "L-43: the check first; L-97: with the tape drawing");
  assert.deepEqual(drawn({ phase: "review", check: { gap_cm: 1, passed: true, check_seconds: 10.5 } }), ["A-21", null]);
});

test("every drawing a screen can pick is a themed inline SVG, and A-09 has none", async () => {
  const { readFileSync, statSync } = await import("node:fs");
  const { ILLUSTRATIONS, SVG, illustrationMarkup } = await import("../../custom_components/myhome/frontend/panel/panel-calibration-illustration.js");
  const panel = new URL("../../custom_components/myhome/frontend/panel/", import.meta.url);
  const source = readFileSync(new URL("panel-calibration-screens.js", panel), "utf8");
  const table = source.slice(source.indexOf("const ILLUSTRATION_OF = {"), source.indexOf("ILLUSTRATION_OF.C ="));
  const used = new Set([...table.matchAll(/\["([a-z-]+)", BEFORE\]/g)].map((match) => match[1]));
  assert.ok(used.size >= 9, `${used.size} ids in the table`);
  assert.deepEqual(Object.keys(SVG).sort(), Object.keys(ILLUSTRATIONS).sort(), "one drawing per id, no drawing without an id");
  const ids = new Set();
  for (const id of used) {
    assert.ok(ILLUSTRATIONS[id] && SVG[id], `${id} has a size and a drawing`);
    const host = document.createElement("div");
    host.innerHTML = illustrationMarkup(id, "Step diagram", (value) => value);
    const svg = host.firstElementChild;
    assert.equal(svg.tagName.toLowerCase(), "svg", id);
    assert.match(svg.getAttribute("viewBox"), /^0 0 \d+ \d+$/, `${id} keeps its viewBox`);
    assert.deepEqual([svg.getAttribute("width"), svg.getAttribute("height")], [String(ILLUSTRATIONS[id].width), String(ILLUSTRATIONS[id].height)]);
    assert.deepEqual([svg.getAttribute("role"), svg.getAttribute("aria-label"), svg.dataset.illustration], ["img", "Step diagram", id]);
    // Outside masks and clip paths every colour is a palette property (its light colour only as the fallback).
    const painted = SVG[id].replace(/<(mask|clipPath)\b[\s\S]*?<\/\1>/g, "").replace(/var\(--wiz-ill-[a-z0-9-]+,#[0-9A-Fa-f]{3,6}\)/g, "");
    assert.doesNotMatch(painted, /#[0-9A-Fa-f]{3,6}\b|"white"|"black"/, `${id} has no raw colour outside its masks`);
    for (const match of SVG[id].matchAll(/\bid="([^"]+)"/g)) {
      assert.ok(match[1].startsWith(`wiz-${id}-`), `${match[1]} carries its drawing's prefix`);
      assert.equal(ids.has(match[1]), false, `${match[1]} is unique on the page`);
      ids.add(match[1]);
    }
  }
  // L-91: every drawing of the module, used by a screen or not, themes every paint (no literal colour outside masks
  // and clip paths) and draws its masks and clip paths only in black and white or their grey.
  for (const [id, markup] of Object.entries(SVG)) {
    const outside = markup.replace(/<(mask|clipPath)\b[\s\S]*?<\/\1>/g, "");
    assert.doesNotMatch(outside.replace(/var\(--wiz-ill-[a-z0-9-]+,#[0-9A-Fa-f]{3,6}\)/g, "THEMED"), /(?:fill|stroke|stop-color)="(?!none"|url\(|THEMED")/,
      `${id}: a paint outside its masks that is not a --wiz-ill-* property`);
    for (const inside of markup.matchAll(/<(mask|clipPath)\b[\s\S]*?<\/\1>/g)) {
      for (const paint of inside[0].matchAll(/(?:fill|stroke)="([^"]+)"/g)) assert.match(paint[1], /^(?:#000|#fff|#D9D9D9|none)$/i, `${id}: mask paint ${paint[1]}`);
    }
  }
  const bytes = statSync(new URL("panel-calibration-illustrations-svg.js", panel)).size;
  assert.ok(bytes <= 110 * 1024, `${bytes} bytes of drawings (16 since Z-14)`);
  assert.match(table, /"05": \["window-closed"/);
  assert.doesNotMatch(table, /"09":/, "A-09 has no drawing");
  const t = (key) => translations.en[key] || key;
  const a09 = buildScreen({ t, stage: "session", path: "A", cover: { name: "Kitchen shutter 2" },
    view: { mode: "geometry", step: "reset", phase: "starting_close", values: {}, samples: { lift: 4 }, readings: { gap: 3 }, slats: true, owner: true, sequence: 4 } });
  assert.deepEqual([a09.code, a09.illustration], ["A-09", null]);
});

test("the profile switch is a 36×20 track with a 16 px thumb 2 px from every edge, in both states", async () => {
  const css = (await import("node:fs")).readFileSync(new URL("../../custom_components/myhome/frontend/panel/myhome-panel.css", import.meta.url), "utf8");
  const track = css.match(/\.wiz-switch input \{([^}]*)\}/)[1], thumb = css.match(/\.wiz-switch input::after \{([^}]*)\}/)[1];
  for (const rule of ["box-sizing: border-box", "width: 36px", "height: 20px", "border: 0", "margin: 0", "padding: 0"]) assert.ok(track.includes(rule), rule);
  for (const rule of ["top: 2px", "left: 2px", "width: 16px", "height: 16px"]) assert.ok(thumb.includes(rule), rule);
  assert.match(css, /\.wiz-switch input:checked::after \{ transform: translateX\(16px\); \}/);
});

test("on the phone the confirmations are sheets that slide down before they hide", async () => {
  const { wiz, press, startFor } = await mount();
  await startFor("cover.living_one");
  await press("begin");
  const original = globalThis.matchMedia;
  try {
    globalThis.matchMedia = (query) => ({ matches: query.includes("max-width") });
    wiz().querySelector(".wiz-head .wiz-x").click(); await tick();
    const exit = wiz().querySelector(".wiz-dialog:not(.wiz-nav-dialog)");
    exit.querySelector('[data-wiz="stay"]').click(); await tick();
    assert.deepEqual([exit.hidden, exit.classList.contains("is-leaving")], [false, true], "sliding down");
    await new Promise((resolve) => setTimeout(resolve, 260));
    assert.equal(exit.hidden, true);
  } finally { globalThis.matchMedia = original; }
  const css = (await import("node:fs")).readFileSync(new URL("../../custom_components/myhome/frontend/panel/myhome-panel.css", import.meta.url), "utf8");
  assert.match(css, /\.wiz-dialog, \.wiz-sheet \{ background: rgb\(0 0 0 \/ 55%\); -webkit-backdrop-filter: blur\(8px\); backdrop-filter: blur\(8px\); \}/);
  assert.match(css, /\.wiz-dialog \.actions \{ flex-direction: column-reverse; align-items: stretch;/, "stacked, primary first");
  assert.match(css, /dialog\.cal-dialog \{ width: 100%; max-width: none; max-height: 90vh; margin: auto 0 0; border-radius: 18px 18px 0 0;/, "the tab's dialogs too");
});

test("the Steps sheet slides down before it hides, at once with reduced motion", async () => {
  const { wiz, tab } = await mount();
  tab().querySelector('[data-action="cal-tab-measure"]').click(); await tick();
  const sheet = wiz().querySelector(".wiz-sheet");
  const original = globalThis.matchMedia;
  try {
    globalThis.matchMedia = () => ({ matches: false });
    wiz().querySelector('[data-wiz="steps"]').click(); await tick();
    assert.equal(sheet.hidden, false);
    wiz().querySelector('[data-wiz="steps-close"]').click(); await tick();
    assert.deepEqual([sheet.hidden, sheet.classList.contains("is-leaving")], [false, true], "sliding down");
    await new Promise((resolve) => setTimeout(resolve, 260));
    assert.deepEqual([sheet.hidden, sheet.classList.contains("is-leaving")], [true, false], "then hidden");
    globalThis.matchMedia = (query) => ({ matches: query.includes("reduce") });
    wiz().querySelector('[data-wiz="steps"]').click(); await tick();
    wiz().querySelector('[data-wiz="steps-close"]').click(); await tick();
    assert.equal(sheet.hidden, true, "reduced motion: at once");
  } finally { globalThis.matchMedia = original; }
  const css = (await import("node:fs")).readFileSync(new URL("../../custom_components/myhome/frontend/panel/myhome-panel.css", import.meta.url), "utf8");
  assert.match(css, /\.wiz-sheet:not\(\[hidden\]\) \.wiz-sheet-card \{ animation: wiz-sheet-in 220ms/);
  assert.match(css, /@media \(prefers-reduced-motion: reduce\) \{ \.wiz-sheet, \.wiz-sheet \.wiz-sheet-card \{ animation: none !important; \} \}/);
});

test("the fade over the phone's action bar shows only while content is hidden below", async () => {
  const { wiz, tab } = await mount();
  tab().querySelector('[data-action="cal-tab-measure"]').click(); await tick();
  const main = wiz().querySelector(".wiz-main"), actions = wiz().querySelector(".wiz-actions");
  // jsdom has no layout: give the content a scroll box by hand.
  Object.defineProperties(main, { scrollHeight: { value: 900, configurable: true }, clientHeight: { value: 400, configurable: true } });
  main.scrollTop = 0; main.dispatchEvent(new Event("scroll"));
  assert.equal(actions.classList.contains("has-more"), true, "more below");
  main.scrollTop = 500; main.dispatchEvent(new Event("scroll"));
  assert.equal(actions.classList.contains("has-more"), false, "at the end of the scroll it goes");
  const css = (await import("node:fs")).readFileSync(new URL("../../custom_components/myhome/frontend/panel/myhome-panel.css", import.meta.url), "utf8");
  assert.match(css, /\.wiz-actions\.has-more::before \{ opacity: 1; \}/);
});

test("on the phone the run's ① and ② stack, never side by side", async () => {
  const css = (await import("node:fs")).readFileSync(new URL("../../custom_components/myhome/frontend/panel/myhome-panel.css", import.meta.url), "utf8");
  assert.doesNotMatch(css, /\.wiz-actions:has\(\.wiz-action:nth-of-type\(2\)\.primary\)/, "no two-column rule for two primaries");
  assert.match(css, /\.wiz-actions \{ display: grid; grid-template-columns: minmax\(0, 1fr\);/, "one column");
  assert.match(css, /@media \(hover: hover\) \{ :is\(\.wiz-action\.stop, \.wiz-action\.danger-outline, \.wiz-action\.danger\):hover:not\(:disabled\) \{/, "a tap leaves no tint on Stop");
  assert.match(css, /\.wiz-main \{ flex: 1; min-height: 0; overflow-y: auto;[^}]*display: flex; flex-direction: column; \}/, "the phone's content scrolls between the strip and the bar");
  assert.match(css, /\.wiz-main > \* \{ flex-shrink: 0; min-width: 0; \}/, "blocks keep their height: a drawing never covers the text");
  assert.match(css, /\.wiz-action\.stop, \.wiz-action\.link \{ min-height: 44px; background: transparent; \}/, "plain red text, the bar's height");
});

test("1 px dividers separate the side columns from the centre on the wide layout only", async () => {
  const css = (await import("node:fs")).readFileSync(new URL("../../custom_components/myhome/frontend/panel/myhome-panel.css", import.meta.url), "utf8");
  const wide = css.slice(css.indexOf("@container caltab (min-width: 1000px) {\n  .wiz-columns"));
  assert.match(wide.slice(0, wide.indexOf("\n}")), /\.wiz-steps \{ border-inline-end: 1px solid var\(--panel-border\); \}[\s\S]*\.wiz-actions \{ border-inline-start: 1px solid var\(--panel-border\); \}/);
});

test("the wizard's side columns take the page colour, slightly shaded", async () => {
  const css = (await import("node:fs")).readFileSync(new URL("../../custom_components/myhome/frontend/panel/myhome-panel.css", import.meta.url), "utf8");
  assert.match(css, /\.wiz \{ --wiz-side: color-mix\(in srgb, var\(--primary-text-color, #182b37\) 4%, var\(--primary-background-color, #fafafa\)\);/);
});

test("the drawings' palette follows Home Assistant's dark mode", async () => {
  const { backend, panel, wiz, startFor } = await mount();
  await startFor("cover.living_one");
  assert.equal(wiz().dataset.theme, "light", "no theme data and no dark preference: light");
  panel.hass = { ...backend.hass, themes: { darkMode: true } }; await tick();
  assert.equal(wiz().dataset.theme, "dark");
  panel.hass = { ...backend.hass, themes: { darkMode: false } }; await tick();
  assert.equal(wiz().dataset.theme, "light");
  const css = (await import("node:fs")).readFileSync(new URL("../../custom_components/myhome/frontend/panel/myhome-panel.css", import.meta.url), "utf8");
  assert.match(css, /\.wiz\[data-theme="dark"\] \{[^}]*--wiz-ill-cover-border: #606060/);
});

test("the drawing is one inline SVG at its Figma size, placed by its position, and gone where the mockup has none", async () => {
  const { ILLUSTRATIONS } = await import("../../custom_components/myhome/frontend/panel/panel-calibration-illustration.js");
  assert.deepEqual(Object.keys(ILLUSTRATIONS).sort(), ["ascent-sequence", "ascent-sequence-no-slats", "check", "curtain-open-tape", "curtain-partial-tape",
    "curtain-tape-half", "curtain-tape-quarter", "curtain-tape-quarter-ascent", "curtain-tape-three-quarters", "descent-sequence", "descent-sequence-no-slats",
    "lift-sequence", "lift-sequence-mobile", "lift-zoom", "two-runs", "window-closed"]);
  assert.ok(Object.values(ILLUSTRATIONS).every((size) => size.width <= 564), "never wider than the instruction column");
  const { backend, wiz, code, press, startFor } = await mount();
  await startFor("cover.living_one");
  await press("begin");
  await move(backend);
  assert.equal(code(), "A-05");
  const figure = wiz().querySelector(".wiz-main .wiz-illustration");
  assert.equal(figure.tagName.toLowerCase(), "svg");
  assert.equal(figure.dataset.illustration, "window-closed");
  assert.equal(figure.getAttribute("aria-label"), "Step diagram");
  assert.deepEqual([figure.getAttribute("width"), figure.getAttribute("height"), figure.getAttribute("viewBox")], ["259", "250", "0 0 540 521"], "L-91: the master's own size, drawn at 259 × 250");
  assert.equal(figure.getAttribute("style"), "width: min(100%, 259px); aspect-ratio: 259 / 250", "the ratio is kept when the column is narrower");
  assert.equal(figure.nextElementSibling.className, "wiz-body", "a step shows its drawing above the text");
  assert.equal(wiz().querySelector(".wiz-frame .cal-visual"), null, "the maintainer's small scene is not used by the wizard");
});

test("every wait has its bar: the time left when the duration is known, moving back and forth when it is not", async () => {
  const t = (key) => translations.it[key] || key;
  const view = { mode: "geometry", step: "home", phase: "starting_close", values: {}, samples: {}, readings: {}, slats: true, owner: true, sequence: 1 };
  const wait = (cover, extra = {}, elapsed = null, local = {}) => buildScreen({ t, language: "it", stage: "session", path: "A", view: { ...view, ...extra },
    elapsed, local, cover: { name: "Kitchen 2", ...cover } }).wait;
  // A positioning lasts the full time in use, the integration's default too (R3-11); without any time it is indeterminate.
  const fallback = wait({ effective: { closing: { value: 30, origin: "default" } } });
  assert.deepEqual(fallback.progress, { value: 0, remaining: 30 }, "the default time counts for a positioning");
  const never = wait({ effective: {} });
  assert.deepEqual(never.progress, { value: null, remaining: null }, "no time at all: indeterminate");
  assert.equal(never.remaining, "");
  const known = wait({ effective: { closing: { value: 21.7, origin: "native_fallback" } } });
  assert.deepEqual(known.progress, { value: 0, remaining: 22 });
  assert.equal(known.remaining, "Tempo residuo circa 22 s");
  // A-19, second leg: 3 s into a half descent of (21 - 4) / 2 s.
  const half = wait({}, { step: "half_close", phase: "closing", values: { opening_time: 22, closing_time: 21 }, geometry: { slat_time_s: 4 } }, 3, { ackReadings: true });
  assert.equal(half.stages[0].state, "done");
  assert.equal(Math.round(half.progress.value * 100), 35);
  assert.equal(half.remaining, "Tempo residuo circa 6 s");
  // A-19, first leg: the return to the top lasts the measured opening time.
  assert.equal(wait({}, { step: "top", phase: "briefing", values: { opening_time: 22, closing_time: 21 } }, null, { ackReadings: true }).remaining, "Tempo residuo circa 22 s");

  const { backend, wiz, code, press, startFor } = await mount();
  await startFor("cover.living_one");
  await press("begin");
  assert.equal(code(), "A-04");
  assert.equal(wiz().querySelector(".wiz-progress").classList.contains("is-indeterminate"), false);
  assert.equal(wiz().querySelector(".wiz-wait-remaining").textContent, "About 22 s remaining");
  assert.equal(backend.calls.length > 0, true);
});

test("Stop alone in a wait is the red filled button; beside a step's buttons it stays a red link", async () => {
  const t = (key) => translations.en[key] || key;
  const view = { mode: "geometry", values: { opening_time: 22, closing_time: 21 }, samples: { lift: 4 }, readings: {}, slats: true, owner: true, sequence: 2 };
  const kinds = (extra, local = {}) => buildScreen({ t, stage: "session", path: "A", view: { ...view, ...extra }, local, elapsed: 1, cover: { name: "Kitchen shutter 2" } })
    .actions.map((action) => `${action.id}:${action.kind}`);
  // Positionings to an end stop offer the end stop at once: Stop is then the red link beside it (R6-6).
  // L-119: A-04 draws A-05's bar from the start (the end stop off until the cover arrives).
  assert.deepEqual(kinds({ step: "home", phase: "starting_close" }), ["endpoint:primary", "misbehaved:secondary", "stop:stop"], "A-04");
  assert.deepEqual(kinds({ step: "reset", phase: "closing" }), ["endpoint:primary", "stop:stop"], "A-09 (no time known: A-10 at once)");
  assert.deepEqual(kinds({ step: "half_open", phase: "opening" }, { ackDescent: true, ackReadings: true }), ["stop:stop"], "A-17: Stop alone, outlined red as everywhere (L-47)");
  assert.deepEqual(kinds({ step: "lift", phase: "opening" }, { ackLift: true }), ["next:primary", "lift:primary", "stop:stop"], "A-07: ① · ② · Stop (L-116)");
  assert.deepEqual(kinds({ step: "top", phase: "opening" }, { ackReadings: true }), ["endpoint:primary", "stop:stop"], "A-19 while it positions");
  const starting = buildScreen({ t, stage: "session", path: "A", view: { ...view, step: "home", phase: "starting_close" }, elapsed: null, cover: { name: "K" } });
  assert.equal(starting.actions[0].disabled, true, "until the motor runs Home Assistant would refuse the end stop");
  const { backend, wiz, code, press, startFor } = await mount();
  await startFor("cover.living_one");
  await press("begin");
  assert.equal(code(), "A-04");
  assert.equal(wiz().querySelector('.wiz-actions [data-wiz="stop"]').className, "wiz-action stop");
  backend.started(); await tick();
  assert.equal(wiz().querySelector('.wiz-actions [data-wiz="endpoint"]').textContent, "It is fully closed, continue");
  // L-119: the end stop is pressable once the cover has arrived (here: its closing time is over).
  assert.equal(wiz().querySelector('.wiz-actions [data-wiz="endpoint"]').disabled, true);
  backend.tick(22); backend.emit(); await tick();
  await press("endpoint");
  assert.equal(backend.calls.filter((message) => message.action === "endpoint").length, 1, "the same end stop the automatic advance sends");
  await press("stop");
  assert.equal(backend.calls.filter((message) => message.action === "stop").length, 1, "the red button stops as before");
});

test("radios are plain rows, the advice badge is accent blue; L-47: red text closes, red outlined stops or discards", async () => {
  const css = (await import("node:fs")).readFileSync(new URL("../../custom_components/myhome/frontend/panel/myhome-panel.css", import.meta.url), "utf8");
  const rule = (selector) => css.match(new RegExp(`^${selector.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")} \\{([^}]*)\\}`, "m"))?.[1] ?? "";
  assert.doesNotMatch(rule(".wiz-radio"), /border|background/, "no card around a radio");
  assert.doesNotMatch(css, /\.wiz-radio:has\(input:checked\)/, "no tint on the chosen radio");
  assert.match(rule(".wiz-recommended"), /color: var\(--panel-accent\)/);
  assert.match(rule(".wiz-action.link"), /color: var\(--panel-accent\)/, "L-47: no red text");
  assert.match(rule(".wiz-action.danger"), /border: 1px solid var\(--panel-error\); background: transparent;/, "no red is filled");
  assert.match(rule(".wiz-leave"), /color: var\(--panel-error\); border: 1px solid var\(--panel-error\)/, "Exit without saving discards: outlined");
  assert.match(css, /\.wiz-action\.stop, \.wiz-action\.danger-outline \{ min-height: 44px; border: 1px solid var\(--panel-error\)/);
  assert.match(css, /@media \(hover: hover\) \{ \.wiz-action\.secondary:hover:not\(:disabled\)/, "outlined buttons answer the pointer");
});

test("a step's problem says the motor's state as plain lines under the text, S-05 with the stopwatch, and keeps Stop", () => {
  const t = (key) => translations.it[key] || key;
  const view = { mode: "geometry", phase: "opening", step: "opening", values: {}, samples: {}, readings: {}, slats: true, owner: true, sequence: 5 };
  const problem = (reason, extra = {}) => buildScreen({ t, language: "it", stage: "session", path: "A", view: { ...view, problem: { reason, ...extra } },
    cover: { name: "Kitchen 2" } });
  const s05 = problem("travel_timeout", { elapsed_s: 92 });
  assert.equal(s05.code, "S-05");
  assert.equal(s05.stopwatch, null, "no light-blue box");
  assert.deepEqual(s05.status, [translations.it.wiz_T256, "Cronometro: fermo a 92 s"]);
  assert.deepEqual(problem("travel_timeout").status, [translations.it.wiz_T256], "without the time the second line is left out");
  const s01 = problem("unexpected_stop");
  assert.deepEqual([s01.code, s01.status], ["S-01", [translations.it.wiz_T242]]);
  assert.deepEqual(s01.actions.map((action) => action.id), ["repeat_step", "stop"], "Stop stays until the owner decides otherwise");
});

test("screens carry no placeholder or key in either language", () => {
  const view = { mode: "geometry", phase: "reading", step: "half_open", values: { opening_time: 22, closing_time: 21 }, samples: { lift: 4 },
    readings: { gap: 3 }, geometry: {}, travel_cm: 198, expected_cm: 99, slats: true, owner: true, sequence: 1 };
  for (const language of ["en", "it"]) {
    const t = (key) => translations[language][key] || translations.en[key] || key;
    const cover = { name: "Kitchen shutter 2", address: "Address: 91", effective: { opening: { value: 12 }, closing: { value: 12 } }, travel_cm: 110,
      profile: { name: "tall_two_leaf", assigned_to: [] }, followers: ["Living room shutter 3"] };
    const contexts = [
      { stage: "choose", covers: [{ key: "k", label: "Kitchen shutter 2" }] }, { stage: "path", profiles: [{ id: "p" }] }, { stage: "before" },
      { stage: "profile", path: "B", profiles: [{ id: "tall", name: "tall_two_leaf", followers: 2, measuredOn: "Living room shutter 1" }] },
      { stage: "current", path: "C" }, { stage: "case", path: "C" }, { stage: "before", path: "C", scope: "times_only" },
      { stage: "before", path: "C", scope: "points_only" }, { stage: "session", view },
      { stage: "session", view: { ...view, phase: "review" }, followerNames: "Living room shutter 3", local: { saveProfile: true } },
      { stage: "saved", local: { saved: { target: "profile_new", name: "kitchen" } } },
    ];
    for (const extra of contexts) {
      const screen = buildScreen({ t, language, path: "A", cover, ...extra });
      const textOf = JSON.stringify(screen);
      assert.doesNotMatch(textOf, /\{(cover|profile|name|seconds|cm|gap|count|names|travel|run|slat|phase|n|total)\}/, `${language} ${screen.code}`);
      assert.doesNotMatch(textOf, /"wiz_?T?\w*"|calTab\w+/, `${language} ${screen.code} shows a key`);
    }
  }
});

test("the keyboard reaches every step: title focus, Steps sheet, and a dialog that keeps Tab inside", async () => {
  const { root, wiz, press, startFor, backend } = await mount();
  await startFor("cover.living_one");
  await new Promise((resolve) => setTimeout(resolve, 0));
  assert.equal(root.activeElement, wiz().querySelector(".wiz-title"), "a new step moves focus to its title");
  window.dispatchEvent(new KeyboardEvent("keydown", { key: "Enter" })); // from the keyboard: the focus goes to the sheet's button
  wiz().querySelector('[data-wiz="steps"]').click(); await tick();
  const sheet = wiz().querySelector(".wiz-sheet");
  assert.equal(sheet.hidden, false);
  assert.equal(root.activeElement, sheet.querySelector('[data-wiz="steps-close"]'));
  assert.equal(sheet.querySelector('[aria-current="step"] .wiz-step-label').textContent, "Preparation");
  sheet.dispatchEvent(new KeyboardEvent("keydown", { key: "Escape", bubbles: true }));
  assert.equal(sheet.hidden, true);
  assert.equal(root.activeElement, wiz().querySelector('[data-wiz="steps"]'));
  await press("begin"); await move(backend);
  wiz().querySelector('.wiz-head [data-wiz="exit"]').click(); await tick();
  const dialog = wiz().querySelector(".wiz-dialog");
  const buttons = [...dialog.querySelectorAll("button:not([hidden])")];
  // S-08: "Exit without saving" as red text on the left, the primary on the right; focus starts on the primary.
  assert.deepEqual(buttons.map((button) => button.dataset.wiz), ["leave", "stay"], "two buttons only");
  assert.equal(root.activeElement, dialog.querySelector('[data-wiz="stay"]'));
  buttons.at(-1).focus();
  dialog.dispatchEvent(new KeyboardEvent("keydown", { key: "Tab", bubbles: true }));
  assert.equal(root.activeElement, buttons[0], "Tab wraps to the first button");
  dialog.dispatchEvent(new KeyboardEvent("keydown", { key: "Tab", shiftKey: true, bubbles: true }));
  assert.equal(root.activeElement, buttons.at(-1), "Shift+Tab wraps to the last one");
  dialog.querySelector('[data-wiz="stay"]').click(); await tick();
  assert.equal(dialog.hidden, true, "Continue the calibration closes it and keeps the wizard");
});

test("a half run that did not start by itself can be started from its screen, and fine-tuning runs as a preview without Stop", async () => {
  const t = (key) => translations.en[key] || key;
  const view = { mode: "geometry", phase: "briefing", step: "half_open", values: { opening_time: 22, closing_time: 21 }, samples: { lift: 4 },
    readings: { gap: 3 }, geometry: {}, travel_cm: 198, slats: true, owner: true, sequence: 4 };
  const screen = buildScreen({ t, stage: "session", path: "A", view, local: { ackDescent: true, ackReadings: true }, cover: { name: "Living room shutter 1" } });
  assert.equal(screen.code, "A-17");
  assert.deepEqual(screen.actions.map((action) => [action.id, !!action.disabled]), [["next", false], ["stop", true]], "L-77: at rest before the run, no Stop to press (L-119: its slot is kept)");
  const context = await mount({ stageMs: 30 });
  const { wiz, code, press, backend } = context;
  await runToSaved(context);
  await press("refine");
  assert.equal(code(), "A-22");
  // D-46: a preview moves nothing, so it has no Stop: the state says so, under «Here the cover would position itself».
  assert.equal(wiz().querySelector('[data-wiz="stop"]'), null);
  assert.equal(wiz().querySelector(".wiz-title").textContent, "Here the cover would position itself");
  assert.ok(wiz().querySelector('.wiz-actions [data-wiz="skip"].primary'), "Y-b: «Continue» to the reading, nothing to wait for");
  assert.equal(wiz().querySelector(".wiz-wait-meter").hidden, true, "no countdown");
  assert.ok(wiz().querySelector(".wiz-preview"));
  assert.equal(backend.calls.filter((message) => message.action === "save").length, 1, "only the base calibration was saved");
});

const resumed = (extra) => ({ entry_id: "one", entity_id: "cover.living_one", session_id: "session-1", sequence: 9, revision: 7, mode: "geometry",
  reason: null, values: { opening_time: 22, closing_time: 21 }, samples: { lift: 4 }, readings: { gap: 3 },
  geometry: {}, travel_cm: 198, stop_requested: false, slats: true, lift_attempts: 1, still_resting: false, gap_warning: false,
  touching_cm: 1, max_gap_cm: 20, gap_warn_cm: 10, saved_travel_cm: 195, expected_cm: 99, can_repeat: true, save_modes: ["new"], ...extra });

test("a refused press is not sent again by itself and its error stays on screen", async () => {
  const { backend, tab, wiz, code, press, actions } = await mount({ session: resumed({ phase: "briefing", step: "half_open", reading_kind: "half_open" }) });
  tab().querySelector('[data-action="cal-tab-session"]').click(); await tick(6);
  assert.equal(code(), "A-15");
  await press("ack:descent");
  const original = backend.hass.callWS;
  let refused = false;
  backend.hass.callWS = async (message) => {
    if (message.action === "next" && !refused) { refused = true; backend.calls.push(message); throw Object.assign(new Error("busy"), { code: "cover_unavailable" }); }
    return original(message);
  };
  await press("ack:readings"); await tick(6);
  assert.deepEqual(actions(), ["next"], "one press, one message");
  assert.equal(wiz().querySelector(".wiz-notices").textContent, translations.en.profileError_cover_unavailable);
  assert.equal(code(), "A-17");
  assert.equal(wiz().querySelector('[data-wiz="next"]').disabled, false, "the run can be started again by hand");
});

test("taking control moves nothing, even on a step the wizard would otherwise continue by itself", async () => {
  const { backend, tab, wiz, code, type, actions } = await mount({ session: resumed({ phase: "reading", step: "half_open", reading_kind: "half_open" }) });
  tab().querySelector('[data-action="cal-tab-session"]').click(); await tick(6);
  await type("99"); await tick(6);
  assert.deepEqual(actions(), ["reading", "next"]);
  // Another device takes the session and leaves it before the return to the top.
  backend.session.phase = "briefing"; backend.session.step = "top";
  const view = backend.view;
  backend.view = (extra = {}) => view({ owner: false, read_only: true, ...extra });
  backend.emit(); await tick(6);
  assert.ok(wiz().querySelector(".wiz-readonly"));
  backend.view = view;
  wiz().querySelector('[data-wiz="take"]').click(); await tick();
  wiz().querySelector('[data-wiz="take"]').click(); await tick(8);
  assert.equal(wiz().querySelector(".wiz-readonly"), null);
  assert.deepEqual(actions(), ["reading", "next"], "nothing after the claim");
  assert.equal(code(), "A-19", "the readings' two-leg return (L-54), to start by hand after a claim");
  assert.equal(wiz().querySelector('[data-wiz="next"]').disabled, false);
});

test("the screen behind an open Discard dialog is not rebuilt until the dialog closes", async () => {
  const { backend, wiz, code, press, startFor } = await mount();
  await startFor("cover.living_one");
  await press("begin"); await move(backend);
  assert.equal(code(), "A-05");
  wiz().querySelector('.wiz-head [data-wiz="exit"]').click(); await tick();
  const stay = wiz().querySelector('[data-wiz="stay"]');
  backend.interrupt("unexpected_movement"); await tick();
  assert.equal(wiz().querySelector('[data-wiz="stay"]'), stay, "the dialog stays where the user is about to tap");
  stay.click(); await tick();
  assert.equal(code(), "S-01");
});

test("review P2: after a real save A-34's primary is «Back to the list»; fine-tuning and T319 only while it can be run", async () => {
  const t = (key) => translations.it[key] || key;
  const saved = (previews, target = "profile_new") => buildScreen({ t, language: "it", stage: "saved", path: "A", cover: { name: "Kitchen 2" },
    local: { saved: { target, name: "kitchen_2" } }, previews });
  const off = saved(false), on = saved(true);
  assert.deepEqual(off.actions.map((action) => [action.id, action.kind, action.label]), [["close", "primary", "Torna all'elenco"],
    ["assign-profile", "secondary", "Assegna questo profilo ad altre tapparelle"], ["another", "secondary", "Calibra un'altra tapparella"],
    ["open-cover", "secondary", "Apri la scheda della tapparella"]]);
  assert.equal(off.body, translations.it.wiz_T155.replace("{name}", "kitchen_2"), "no «la tapparella si muoverà» without fine-tuning");
  assert.equal(off.stepper.phases.some((phase) => phase.key === "finetune"), false, "no fine-tuning step either");
  assert.deepEqual(on.actions.map((action) => [action.id, action.kind]).at(-1), ["refine", "secondary"], "on: a secondary, never the primary");
  assert.ok(on.body.endsWith(translations.it.wiz_T319));
  // Phone (one column): the primary, and the rest under «Altre opzioni ▾» once they are more than three.
  const { overflow } = await import("../../custom_components/myhome/frontend/panel/panel-calibration-wizard.js");
  // L-125: a profile just created: «Assegna questo profilo…» beside the primary; one updated: under «Altre opzioni».
  assert.deepEqual([overflow(off.actions).shown.map((action) => action.id), overflow(off.actions).more.map((action) => action.id)],
    [["close", "assign-profile"], ["another", "open-cover"]]);
  assert.deepEqual([overflow(on.actions).shown.map((action) => action.id), overflow(on.actions).more.map((action) => action.id)],
    [["close", "assign-profile"], ["another", "open-cover", "refine"]]);
  const updated = saved(false, "profile_update");
  assert.deepEqual([overflow(updated.actions).shown.map((action) => action.id), overflow(updated.actions).more.map((action) => action.id)],
    [["close"], ["assign-profile", "another", "open-cover"]]);
  const coverOnly = saved(false, "cover_only");
  assert.deepEqual([overflow(coverOnly.actions).shown.map((action) => action.id), overflow(coverOnly.actions).more.length], [["close", "another", "open-cover"], 0],
    "three at most: all in view");
});

test("L-47: a completed flow ends on «Back to the list», the primary (a secondary on S-06 beside Start over); never red", () => {
  const t = (key) => translations.it[key] || key;
  const close = (screen) => [screen.code, screen.actions.find((action) => action.id === "close")?.kind ?? (screen.links.some((link) => link.id === "close") ? "link" : null)];
  const saved = (path, preview = false) => buildScreen({ t, language: "it", stage: "saved", path, scope: path === "C" ? "times_only" : null,
    cover: { name: "Kitchen 2", followProfile: "alta" }, local: { saved: { target: path === "B" ? "follow_profile" : "cover_only", preview } } });
  assert.deepEqual(close(saved("B")), ["B-09", "primary"]);
  assert.deepEqual(close(saved("A")), ["A-34", "primary"], "review P2: the primary, fine-tuning a secondary");
  assert.deepEqual(close(saved("C")), ["C-34", "primary"]);
  assert.deepEqual([close(saved("B", true)), close(saved("A", true)), close(saved("C", true))],
    [["B-09", "primary"], ["A-34", "primary"], ["C-34", "primary"]], "U-8: at the end of a preview it is the primary");
  const expired = buildScreen({ t, language: "it", stage: "session", path: "A", cover: { name: "Kitchen 2" }, status: { expired: true },
    view: { mode: "geometry", phase: "briefing", step: "lift", values: {}, samples: {}, readings: {}, owner: true, sequence: 3 } });
  assert.deepEqual(close(expired), ["S-06", "secondary"]);
});

test("full runs and the return to the top: once the motor stopped the stopwatch freezes, Stop goes, the bar is full; ② only while it runs (L-3)", async () => {
  const t = (key) => translations.en[key] || key;
  const base = { mode: "geometry", values: { opening_time: 22, closing_time: 21 }, samples: { lift: 4 }, readings: { gap: 3 },
    geometry: { slat_time_s: 4 }, travel_cm: 110, slats: true, owner: true, sequence: 7 };
  const screen = (view, local = {}, elapsed = 12.3, mode) => buildScreen({ t, stage: "session", path: mode === "guided" ? "C" : "A",
    scope: mode === "guided" ? "times_only" : null, view: { ...base, ...(mode ? { mode } : {}), ...view }, local, elapsed, cover: { name: "Kitchen shutter 2" } });
  const ids = (item) => item.actions.map((action) => action.id);
  for (const [view, code, local] of [[{ step: "opening", phase: "opening" }, "A-11"], [{ step: "closing", phase: "closing" }, "A-14"]]) {
    const running = screen({ ...view, motor_stopped: false, stopped_elapsed: null }, local);
    assert.equal(running.code, code);
    assert.deepEqual(ids(running), ["next", "endpoint", "stop"], `${code} running: Stop`);
    assert.deepEqual(running.stopwatch, { label: "Motor", value: "moving for 12.3 s" });
    assert.equal(screen({ ...view }, local, 12).stopwatch.value, "moving for 12.0 s", "one decimal always: the value keeps its length");
    // ② pressed while it ran, the stop crossed it: the press decides; nothing more to press, no Stop.
    const pressed = { fullRun: { step: view.step, pressed: true } };
    const stopped = screen({ ...view, motor_stopped: true, stopped_elapsed: 21.37 }, pressed, 25.8);
    assert.deepEqual(ids(stopped), ["next", "endpoint", "stop"], `${code} stopped: Stop stays in its place (L-116)…`);
    assert.equal(stopped.actions.find((action) => action.id === "stop").disabled, true, `${code}: …off, nothing moves`);
    assert.equal(stopped.actions.find((action) => action.id === "endpoint").disabled, true, "L-3: ② only while the motor runs");
    assert.deepEqual(stopped.stopwatch, { label: "Stopwatch", value: "stopped at 21.4 s" }, `${code} freezes at the stop frame`);
    assert.equal(screen({ ...view, motor_stopped: true, stopped_elapsed: 21 }, pressed).stopwatch.value, "stopped at 21.0 s");
    // L-3: stopped before ② (any tab, a resumed one too): not valid at once, its time struck through, only Repeat.
    const unpressed = screen({ ...view, motor_stopped: true, stopped_elapsed: 21.37 }, {}, 25.8);
    assert.deepEqual([unpressed.code, unpressed.title, ids(unpressed)], [code === "A-11" ? "A-12" : "A-15",
      code === "A-11" ? "Ascent not valid" : "Descent not valid", ["repeat", "invalid-next"]]);
    assert.deepEqual([unpressed.results.at(-1).value, unpressed.results.at(-1).invalid, unpressed.notices[0].tone], ["21.4", true, "error"]);
    assert.match(unpressed.notices[0].text, /stopped before you pressed ②/);
    assert.match(screen({ ...view, motor_stopped: true, stopped_elapsed: 60.1 }).notices[0].text, /actuator's timer/, "59-65 s: the timer's notice");
  }
  // The return to the top (A-19 first leg): the bar is complete once the motor stopped, before the expected time.
  const top = screen({ step: "top", phase: "opening", motor_stopped: false, stopped_elapsed: null }, { ackReadings: true }, 5);
  assert.deepEqual([top.code, ids(top), Math.round(top.wait.progress.value * 100)], ["A-19", ["endpoint", "stop"], 23], "still positioning: the end stop at once");
  assert.deepEqual(ids(screen({ step: "top", phase: "opening", motor_stopped: false }, { ackReadings: true }, 23)), ["endpoint", "stop"],
    "past its measured time the end stop is asked, Stop stays while the motor runs");
  const topStopped = screen({ step: "top", phase: "opening", motor_stopped: true, stopped_elapsed: 9.1 }, { ackReadings: true }, 9.5);
  assert.deepEqual([ids(topStopped), topStopped.wait.progress, topStopped.wait.remaining], [["endpoint", "stopped"], { value: 1, remaining: null }, ""],
    "L-119: the finished line in Stop's slot");
  assert.deepEqual(ids(screen({ step: "top", phase: "opening", motor_stopped: true, stopped_elapsed: 9.1 })), ["endpoint", "misbehaved", "stopped"],
    "the return before a repeated descent too, with the stopped state in place of its wait (D-12)");
  // The lift-off run is unchanged: a stop there still ends the session, so it keeps Stop.
  assert.deepEqual(ids(screen({ step: "lift", phase: "opening" }, { ackLift: true })), ["next", "lift", "stop"]);
  // The guided runs of path C (times only).
  const guided = screen({ phase: "closing", values: { opening_time: 12.6 }, motor_stopped: true, stopped_elapsed: 12.24 }, { fullRun: { step: "closing", pressed: true } }, 14, "guided");
  assert.deepEqual([guided.code, ids(guided), guided.stopwatch.value], ["C-14", ["run:close", "endpoint", "stop"], "stopped at 12.2 s"]);
  assert.equal(guided.actions.at(-1).disabled, true, "L-116: Stop stays, off once the motor stopped");
  const guidedStopped = screen({ phase: "opening", motor_stopped: true, stopped_elapsed: 60.1 }, {}, 61, "guided");
  assert.deepEqual([guidedStopped.code, guidedStopped.title, ids(guidedStopped), guidedStopped.results[0].invalid],
    ["C-12", "Ascent not valid", ["restart", "invalid-next"], true], "L-3: the guided runs too");
  assert.deepEqual(ids(screen({ phase: "opening", motor_stopped: false }, {}, 3, "guided")), ["run:open", "endpoint", "stop"]);

  // On screen: the stopwatch stops counting and the Stop link leaves.
  const { backend, wiz, code, press, type, startFor } = await mount();
  await startFor("cover.living_one");
  await press("begin"); await move(backend); await press("endpoint"); backend.stopped(); await tick();
  await press("ack:lift"); await press("next"); await move(backend, { seconds: 4.4 }); await press("lift"); backend.stopped(); await tick();
  await type("3"); await move(backend); await press("endpoint"); backend.stopped(); await tick();
  await press("next"); await move(backend, { seconds: 22.2 });
  assert.equal(code(), "A-11");
  assert.ok(wiz().querySelector('.wiz-actions [data-wiz="stop"]'));
  Object.assign(backend.session, { motor_stopped: true, stopped_elapsed: 22.24 }); backend.session.sequence += 1; backend.emit(); await settle();
  // L-3: stopped before ②: not valid at once, ② and Stop gone, the time it stopped at struck through.
  assert.equal(wiz().querySelector(".wiz-title").textContent, "Ascent not valid");
  assert.equal(wiz().querySelector('.wiz-actions [data-wiz="stop"]'), null, "no Stop once the motor stopped");
  assert.equal(wiz().querySelector('[data-wiz="endpoint"]'), null, "no ② once the motor stopped");
  assert.match(wiz().querySelector(".wiz-result.is-invalid").textContent, /22\.2/);
});

test("a full run ended by the bus: valid with a press while the motor ran, otherwise not valid at once and only Repeat (R8-7, L-3)", async () => {
  const toAscent = async (context) => {
    const { backend, press, type, startFor, code } = context;
    await startFor("cover.living_one");
    await press("begin"); await move(backend); await press("endpoint"); backend.stopped(); await tick();
    await press("ack:lift"); await press("next"); await move(backend, { seconds: 4.4 }); await press("lift"); backend.stopped(); await tick();
    await type("3"); await move(backend); await press("endpoint"); backend.stopped(); await tick();
    assert.equal(code(), "A-11");
    await press("next"); backend.started(); await tick();
  };
  const frame = (backend, seconds) => {
    backend.tick(seconds);
    Object.assign(backend.session, { motor_stopped: true, stopped_elapsed: backend.view().elapsed });
    backend.emit();
  };
  const endpoints = (backend) => backend.calls.filter((message) => message.action === "endpoint").length;
  const notice = (wiz) => wiz().querySelector(".wiz-result-notices").textContent;
  // (a) ② before the frame: the press time, valid.
  const a = await mount();
  await toAscent(a);
  a.backend.tick(22); a.backend.emit(); await tick();
  await a.press("endpoint"); a.backend.stopped(); await tick();
  assert.deepEqual([a.code(), notice(a.wiz)], ["A-13", ""], "valid: on to the travel (L-13)");
  // (b) L-3: ② exists only while the motor runs: a click racing the stop's view (the button still drawn) confirms
  // nothing valid; the run is not valid and its end is confirmed once.
  const b = await mount();
  await toAscent(b);
  const racing = b.button("endpoint");
  frame(b.backend, 22);
  racing.click(); await tick(6);
  b.backend.stopped(); await tick();
  assert.deepEqual([b.code(), b.title(), b.wiz().querySelector('[data-wiz="ack:ascent"]')], ["A-12", "Ascent not valid", null]);
  assert.equal(endpoints(b.backend), 3, "home, reset and this run's end: one endpoint each");
  // (c) a stop before ②: not valid at once; the panel confirms the end stop itself, the result offers only Repeat.
  const c = await mount();
  await toAscent(c);
  const before = endpoints(c.backend);
  frame(c.backend, 22); await tick(6);
  assert.equal(endpoints(c.backend), before + 1, "no 2 s wait: the end stop is confirmed at once");
  c.backend.tick(3); c.backend.emit(); await tick(6);
  assert.equal(endpoints(c.backend), before + 1, "once");
  c.backend.stopped(); await tick();
  assert.equal(c.code(), "A-12");
  assert.equal(notice(c.wiz), "The cover stopped before you pressed ②: the time may not be the real one. Repeat the run and press ② as soon as the motor really stops.");
  assert.deepEqual([...c.wiz().querySelectorAll(".wiz-actions .wiz-action")].map((item) => [item.dataset.wiz, item.className.replace("wiz-action ", ""), item.disabled]),
    [["repeat", "primary", false], ["invalid-next", "secondary", true]], "no OK, continue: Continue shown, disabled (D-11)");
  assert.equal(c.title(), "Ascent not valid", "D-11: the title says so");
  assert.equal(c.wiz().querySelector(".wiz-result-notices .wiz-notice").dataset.tone, "error");
  const tiles = [...c.wiz().querySelectorAll(".wiz-result")];
  assert.deepEqual(tiles.map((tile) => tile.classList.contains("is-invalid")), [false, true], "the lift-off stays valid, the ascent is struck through");
  const ascent = () => [...c.wiz().querySelectorAll(".wiz-steps li li")].find((item) => item.textContent.startsWith("Full ascent"))?.textContent;
  assert.match(ascent(), /—/, "D-11: the stepper does not show the time judged not valid");
  await c.press("repeat");
  assert.ok(c.actions().slice(-2).includes("repeat"), "Repeat sends repeat (the return to the bottom then starts by itself)");
  assert.match(ascent(), /—/, "D-11: nor while the repeat returns to the bottom (A-09)");
  // (d) a frame at the actuator's timer (59-65 s): not valid, its own notice.
  const d = await mount();
  await toAscent(d);
  frame(d.backend, 60.2); await tick(6);
  assert.equal(d.button("endpoint"), null, "L-3: no ② after the timer stopped the motor");
  d.backend.stopped(); await tick();
  assert.equal(d.code(), "A-12");
  assert.equal(notice(d.wiz), "The cover stopped when the actuator's timer ran out (about 60 s): the time is not valid. Repeat the run and press ② as soon as the motor really stops.");
  assert.equal(d.wiz().querySelector('[data-wiz="ack:ascent"]'), null);
});

test("the full descent follows the same rule, and the lift-off run is not affected", async () => {
  const { backend, press, type, startFor, code, wiz } = await mount();
  await startFor("cover.living_one");
  await press("begin"); await move(backend); await press("endpoint"); backend.stopped(); await tick();
  await press("ack:lift"); await press("next"); backend.started(); await tick();
  assert.equal(code(), "A-07");
  // The lift-off run: a stop frame is not a full run's end (the backend interrupts it), the panel sends nothing.
  const before = backend.calls.filter((message) => message.action === "endpoint").length;
  backend.tick(5); Object.assign(backend.session, { motor_stopped: true, stopped_elapsed: 2 }); backend.emit(); await tick(6);
  assert.equal(backend.calls.filter((message) => message.action === "endpoint").length, before, "nothing automatic on A-07");
  delete backend.session.motor_stopped; delete backend.session.stopped_elapsed;
  await press("lift"); backend.stopped(); await tick();
  await type("3"); await move(backend); await press("endpoint"); backend.stopped(); await tick();
  await press("next"); await move(backend, { seconds: 22.2 }); await press("endpoint"); backend.stopped(); await tick();
  await type("198"); await press("ack:ascent");
  assert.equal(code(), "A-14");
  await press("next"); backend.started(); backend.tick(21); Object.assign(backend.session, { motor_stopped: true, stopped_elapsed: backend.view().elapsed }); backend.emit(); await tick(6);
  assert.equal(wiz().querySelector(".wiz-title").textContent, "Descent not valid", "L-3: at once, before its end is confirmed");
  backend.stopped(); await tick();
  assert.equal(code(), "A-15");
  assert.match(wiz().querySelector(".wiz-result-notices").textContent, /stopped before you pressed ②/);
  assert.deepEqual([...wiz().querySelectorAll(".wiz-actions .wiz-action")].map((item) => item.dataset.wiz), ["repeat", "invalid-next"]);
  assert.equal(wiz().querySelector(".wiz-title").textContent, "Descent not valid");
});

const T445 = "The cover stopped when the actuator's timer ran out (about 60 s): the time is not valid. Repeat the run and press ② as soon as the motor really stops.";

for (const slats of [false, true]) {
  test(`L-3: a full ascent the actuator's timer stopped while no tab was attached is not valid on resume, as a reader and after Take control (${slats ? "slats" : "no slats"})`, async () => {
    const session = resumed({ phase: "opening", step: "opening", slats, values: {}, samples: slats ? { lift: 4 } : {}, readings: slats ? { gap: 3 } : {},
      travel_cm: null, can_repeat: false, reading_kind: "travel", expected_cm: null, motor_stopped: true, stopped_elapsed: 60.1, _started: 0, _after_stop: "reading" });
    const { backend, tab, wiz, code, title, press, actions } = await mount({ session, owner: false });
    backend.tick(75);
    tab().querySelector('[data-action="cal-tab-session"]').click(); await tick(6);
    const shown = () => [code(), title(), wiz().querySelector(".wiz-result.is-invalid")?.textContent.includes("60.1"),
      wiz().querySelector(".wiz-result-notices").textContent, wiz().querySelector('[data-wiz="endpoint"]')];
    assert.deepEqual(shown(), ["A-12", "Ascent not valid", true, T445, null], "the reader sees it at once, struck through, with no ②");
    assert.deepEqual(actions(), [], "a reader sends nothing");
    wiz().querySelector('[data-wiz="take"]').click(); await tick();
    wiz().querySelector('[data-wiz="take"]').click(); await tick(8);
    assert.equal(wiz().querySelector(".wiz-readonly"), null);
    assert.deepEqual(shown(), ["A-12", "Ascent not valid", true, T445, null], "after Take control: still not valid, ② never offered");
    assert.deepEqual(actions(), ["endpoint"], "its end stop is confirmed once (nothing moves), so the result can be repeated");
    backend.stopped(); await tick();
    assert.deepEqual(shown(), ["A-12", "Ascent not valid", true, T445, null]);
    assert.equal(wiz().querySelector('[data-wiz="ack:ascent"]'), null);
    assert.notEqual(title(), translations.en.wiz_T062, "never the travel reading");
    const sent = actions().length;
    await press("repeat");
    assert.equal(actions()[sent], "repeat");
  });

  test(`L-3: ② cannot turn a run the timer stopped into a valid time in the live tab (${slats ? "slats" : "no slats"})`, async () => {
    const { backend, wiz, code, title, press, type, startFor, actions, panel } = await mount();
    await startFor("cover.living_one");
    if (!slats) { const no = wiz().querySelector('input[name="slats"][value="no"]'); no.checked = true; no.dispatchEvent(new Event("change", { bubbles: true })); }
    await press("begin"); await move(backend); await press("endpoint"); backend.stopped(); await tick();
    if (slats) {
      await press("ack:lift"); await press("next"); await move(backend, { seconds: 4.4 }); await press("lift"); backend.stopped(); await tick();
      await type("3"); await move(backend); await press("endpoint"); backend.stopped(); await tick();
    }
    assert.equal(code(), "A-11");
    await press("next"); backend.started(); await tick();
    // The timer's stop frame arrives while a click on ② is on its way (the button was still drawn).
    const racing = wiz().querySelector('[data-wiz="endpoint"]');
    backend.tick(60.1);
    Object.assign(backend.session, { motor_stopped: true, stopped_elapsed: 60.1 });
    backend.emit();
    racing.click();
    panel._calibrationTab._wizard._act("endpoint");
    await tick(6);
    assert.equal(actions().filter((action) => action === "endpoint").length, slats ? 3 : 2, "one end stop for this run, sent as not valid");
    backend.stopped(); await tick();
    assert.deepEqual([code(), title(), wiz().querySelector(".wiz-result-notices").textContent], ["A-12", "Ascent not valid", T445]);
    assert.equal(backend.session.phase, "reading", "the backend waits for a reading the panel does not ask for");
  });
}

test("L-3: Repeat pressed before the end of a stopped run is confirmed is sent on the result it reaches", async () => {
  const { backend, wiz, code, press, type, startFor, actions } = await mount();
  await startFor("cover.living_one");
  await press("begin"); await move(backend); await press("endpoint"); backend.stopped(); await tick();
  await press("ack:lift"); await press("next"); await move(backend, { seconds: 4.4 }); await press("lift"); backend.stopped(); await tick();
  await type("3"); await move(backend); await press("endpoint"); backend.stopped(); await tick();
  await press("next"); backend.started(); backend.tick(22);
  Object.assign(backend.session, { motor_stopped: true, stopped_elapsed: 22 });
  backend.emit();
  wiz().querySelector('[data-wiz="repeat"]').click(); await tick(6);
  assert.equal(actions().at(-1), "endpoint", "the end stop first");
  const sent = actions().length;
  backend.stopped(); await tick(6);
  assert.equal(actions()[sent], "repeat", "then the repeat, by itself (the return to the bottom follows as after any repeat)");
  assert.equal(code(), "A-09");
});

test("L-3: a guided run (times only) stopped before ② is not valid on resume", () => {
  const t = (key) => translations.en[key] || key;
  const screen = buildScreen({ t, stage: "session", path: "C", scope: "times_only", cover: { name: "Kitchen shutter 2" }, local: {}, elapsed: 61,
    view: { mode: "guided", phase: "opening", values: {}, motor_stopped: true, stopped_elapsed: 60.1, owner: false, read_only: true, sequence: 4 } });
  assert.deepEqual([screen.code, screen.title, screen.results[0].invalid, screen.notices[0].text], ["C-12", "Ascent not valid", true, T445]);
});

// L-10: a session interrupted by a Stop from another tab stays open in Home Assistant (it holds the cover) until cancelled.
const interrupted = (extra = {}) => resumed({ phase: "interrupted", reason: "stopped", step: "opening", values: {}, ...extra });
const cancels = (backend) => backend.calls.filter((message) => message.action === "cancel").length;
const claims = (backend) => backend.subscriptions.filter((message) => message.type === "myhome/cover_calibration/resume" && message.claim).length;

test("L-113: a reader of an interrupted session never restarts or cancels it: the reader's bar, Take control only while it can be claimed", async () => {
  const { backend, tab, wiz, code, panel } = await mount({ session: interrupted(), owner: false });
  tab().querySelector('[data-action="cal-tab-session"]').click(); await tick(6);
  assert.equal(code(), "S-01");
  const bar = () => [...wiz().querySelectorAll(".wiz-actions .wiz-action, .wiz-actions .wiz-take")].map((button) => button.dataset.wiz);
  assert.deepEqual(bar(), ["take", "close"], "the note, Take control and «Back to the list»: no Start over, no Cancel the measurement");
  assert.ok(wiz().querySelector(".wiz-readonly-note"));
  assert.ok(wiz().querySelector(".wiz-head .wiz-unfollow"), "the header keeps «Stop following»");
  // Even sent by other means, Start over and Cancel do nothing for a reader: no claim, no cancel.
  const wizard = panel._calibrationTab._wizard;
  await wizard._act("start-over"); await wizard._act("abort"); await tick(8);
  assert.deepEqual([claims(backend), cancels(backend)], [0, 0]);
  // Take control: now the owner, its own bar (Start over, Back to the list, Cancel the measurement); cancelling works.
  wiz().querySelector('[data-wiz="take"]').click(); await tick();
  wiz().querySelector('[data-wiz="take"]').click(); await tick(8);
  assert.equal(wiz().querySelector(".wiz-readonly"), null);
  assert.deepEqual(bar(), ["start-over", "close", "abort"]);
  wiz().querySelector('[data-wiz="abort"]').click(); await tick(12);
  assert.equal(cancels(backend), 1);
  assert.equal(backend.session.phase, "cancelled");
  // A session Home Assistant no longer keeps (lease run out): no Take control, only the way back.
  reset();
  const gone = await mount({ session: interrupted(), owner: false });
  const view = gone.backend.view;
  gone.backend.view = (extra = {}) => view({ recoverable: false, ...extra });
  gone.tab().querySelector('[data-action="cal-tab-session"]').click(); await tick(6);
  const goneBar = [...gone.wiz().querySelectorAll(".wiz-actions .wiz-action, .wiz-actions .wiz-take")].map((button) => button.dataset.wiz);
  assert.deepEqual(goneBar, ["close"], "not claimable: no Take control");
});
test("R-2: a refused cancel on «Start over» leaves the wizard alive: the next views still reach it", async () => {
  const { backend, tab, wiz, code, panel } = await mount({ session: interrupted() });
  tab().querySelector('[data-action="cal-tab-session"]').click(); await tick(6);
  assert.equal(code(), "S-01");
  const original = backend.hass.callWS;
  backend.hass.callWS = async (message) => {
    if (message.action === "cancel") { backend.calls.push(message); throw { code: "cover_unavailable", message: "busy" }; }
    return original(message);
  };
  wiz().querySelector('[data-wiz="start-over"]').click(); await tick(12);
  assert.equal(backend.session.phase, "interrupted", "not cancelled");
  assert.ok(wiz(), "still open");
  backend.hass.callWS = original;
  // The session moves on elsewhere: the wizard still follows it (before R-2 every callback was ignored).
  backend.session.phase = "briefing"; backend.session.reason = null; backend.session.step = "top"; backend.emit(); await tick(8);
  assert.notEqual(code(), "S-01", "the new view reached the wizard");
  void panel;
});

test("S-2: «Start over» while «Cancel the measurement» is still on its way: the next session's exit still closes the wizard", async () => {
  const { backend, tab, wiz, code, press } = await mount({ session: interrupted() });
  tab().querySelector('[data-action="cal-tab-session"]').click(); await tick(6);
  assert.equal(code(), "S-01");
  const original = backend.hass.callWS;
  let release;
  backend.hass.callWS = (message) => message.action === "cancel" && !release
    ? new Promise((resolve) => { release = () => resolve(original(message)); }) : original(message);
  wiz().querySelector('[data-wiz="abort"]').click(); await tick(6);
  assert.ok(release, "the first cancel is on its way");
  wiz().querySelector('[data-wiz="start-over"]').click(); await tick(6);
  release(); await tick(12);
  backend.hass.callWS = original;
  assert.equal(backend.session.phase, "cancelled");
  assert.ok(wiz(), "the wizard stays open to start again");
  // A new session, then «Exit without saving»: the wizard closes (before S-2 the stale flag swallowed this cancel).
  backend.hass.states["cover.living_one"] = { state: "closed", attributes: { current_position: 0 } };
  await press("begin");
  assert.ok(backend.session && backend.session.phase !== "cancelled", "a new session is open");
  wiz().querySelector('[data-wiz="exit"]').click(); await tick();
  wiz().querySelector('[data-wiz="leave"]').click(); await tick(12);
  assert.equal(backend.session.phase, "cancelled");
  assert.equal(wiz(), null, "the wizard closed");
});

for (const [label, owner] of [["the owner starts again, its reader", false], ["a reader that took control starts again, the former owner", true]]) {
  test(`L-113: ${label} is not left on «cancelled from another device»: it can follow the new session`, async () => {
    const { backend, tab, wiz, code, title, panel } = await mount({ session: resumed({ phase: "briefing", step: "half_open", reading_kind: "half_open" }), owner });
    tab().querySelector('[data-action="cal-tab-session"]').click(); await tick(6);
    // The other device guides (or takes control: this tab reads), then cancels and starts a new session on the same cover.
    const view = backend.view;
    backend.view = (extra = {}) => view({ owner: false, read_only: true, ...extra });
    backend.emit(); await tick(6);
    backend.session.phase = "cancelled"; backend.emit(); await tick(6);
    assert.equal(title(), translations.en.wiz_T542, "first: cancelled from another device");
    // A plain cancel: the event comes, no new session: the screen stays.
    backend.sessionEvent(); await new Promise((resolve) => setTimeout(resolve, 300)); await tick(6);
    assert.equal(title(), translations.en.wiz_T542);
    backend.replaceSession(resumed({ session_id: "session-2", phase: "briefing", step: "home", sequence: 1 }));
    backend.sessionEvent(); await new Promise((resolve) => setTimeout(resolve, 300)); await tick(6);
    assert.equal(code(), "S-01r");
    assert.equal(title(), "The calibration was restarted from another device.");
    // L-114: its body, under the title and above the actions.
    assert.equal(wiz().querySelector(".wiz-main .wiz-body").textContent.trim(), translations.en.wiz_T583);
    assert.ok(wiz().querySelector(".wiz-title").compareDocumentPosition(wiz().querySelector(".wiz-body")) & 4, "after the title");
    assert.match(translations.it.wiz_T583, /^La misurazione che stavi seguendo è stata annullata/);
    assert.deepEqual([...wiz().querySelectorAll(".wiz-actions .wiz-action")].map((button) => [button.dataset.wiz, button.textContent]),
      [["follow-new", "Follow session"], ["close", "Back to the list"]]);
    // «Follow session»: this tab joins the new session as a reader (no claim).
    wiz().querySelector('[data-wiz="follow-new"]').click(); await tick(8);
    const resume = backend.subscriptions.filter((message) => message.type === "myhome/cover_calibration/resume").at(-1);
    assert.deepEqual([resume.session_id, !!resume.claim], ["session-2", false]);
    assert.ok(wiz().querySelector(".wiz-readonly"), "a reader of the new session");
    assert.notEqual(code(), "S-01r");
    void panel;
  });
}

test("review S1: «Esci senza salvare?» open, another device takes control, the confirm only stops following: no claim, no cancel", async () => {
  const { backend, tab, wiz, panel } = await mount({ session: resumed({ phase: "briefing", step: "half_open", reading_kind: "half_open" }) });
  tab().querySelector('[data-action="cal-tab-session"]').click(); await tick(6);
  const dialog = () => wiz()?.querySelector(".wiz-dialog:not(.wiz-nav-dialog)");
  // 1. The owner asks to leave: the question opens.
  wiz().querySelector('[data-wiz="exit"]').click(); await tick();
  assert.equal(dialog().hidden, false);
  // 2. Another device takes control: this tab reads; the owner's question closes at once.
  const view = backend.view;
  backend.view = (extra = {}) => view({ owner: false, read_only: true, ...extra });
  backend.emit(); await tick(6);
  assert.equal(dialog().hidden, true, "the question asked as owner is gone");
  assert.ok(wiz().querySelector(".wiz-readonly"));
  // 3. A confirm that still lands (a tap already on its way): it only stops following.
  await panel._calibrationTab._wizard._act("leave"); await tick(8);
  assert.deepEqual([claims(backend), cancels(backend)], [0, 0], "never takes control back to cancel");
  assert.equal(wiz(), null, "back to the list");
  assert.notEqual(backend.session.phase, "cancelled");
});

test("review N1: a new session long after ours ended is not a restart; a restarted session gone before it is followed shows its end", async () => {
  const later = (ms) => new Promise((resolve) => setTimeout(resolve, ms));
  const leftBehind = async () => {
    const context = await mount({ session: resumed({ phase: "briefing", step: "half_open", reading_kind: "half_open" }), owner: false });
    context.tab().querySelector('[data-action="cal-tab-session"]').click(); await tick(6);
    context.backend.session.phase = "cancelled"; context.backend.emit(); await tick(6);
    assert.equal(context.title(), translations.en.wiz_T542);
    return context;
  };
  // 11 minutes after ours ended, a calibration started on the same cover is someone else's: the screen stays.
  const late = await leftBehind();
  late.panel._calibrationTab._wizard._local.endedAt -= 11 * 60 * 1000;
  late.backend.replaceSession(resumed({ session_id: "session-2", phase: "briefing", step: "home", sequence: 1 }));
  late.backend.sessionEvent(); await later(300); await tick(6);
  assert.equal(late.title(), translations.en.wiz_T542, "not «restarted»");
  // Restarted, then gone before «Follow session»: its end, with only the way back.
  for (const [phase, code, title] of [["expired", "S-06", translations.en.wiz_T259], ["cancelled", "S-01", translations.en.wiz_T542]]) {
    reset();
    const gone = await leftBehind();
    gone.backend.replaceSession(resumed({ session_id: "session-2", phase: "briefing", step: "home", sequence: 1 }));
    gone.backend.sessionEvent(); await later(300); await tick(6);
    assert.equal(gone.code(), "S-01r");
    gone.backend.session.phase = phase;
    gone.wiz().querySelector('[data-wiz="follow-new"]').click(); await tick(8);
    assert.deepEqual([gone.code(), gone.title()], [code, title], `${phase}: its end`);
    assert.deepEqual([...gone.wiz().querySelectorAll(".wiz-actions .wiz-action")].map((button) => button.dataset.wiz), ["close"]);
    assert.equal(gone.backend.subscriptions.filter((message) => message.type === "myhome/cover_calibration/resume" && message.session_id === "session-2").length, 0, "nothing joined");
  }
});

test("L-10: Start over and Abandon from S-01 cancel the interrupted session first; the exit of a live one cancels it", async () => {
  // Start over: the session is cancelled, then the wizard starts again from «Before you begin».
  const a = await mount({ session: interrupted() });
  a.tab().querySelector('[data-action="cal-tab-session"]').click(); await tick(6);
  a.wiz().querySelector('[data-wiz="start-over"]').click(); await tick(12);
  assert.deepEqual([cancels(a.backend), a.backend.session.phase], [1, "cancelled"], "the owner's Start over (L-113: a reader has none)");
  assert.ok(a.wiz(), "the wizard stays open to start again");
  // Abandon (the header's exit) on S-01: cancelled too, not only the view closed.
  reset();
  const b = await mount({ session: interrupted() });
  b.tab().querySelector('[data-action="cal-tab-session"]').click(); await tick(6);
  // (S-01's header has «Close»; «Abandon the calibration» is the same exit, as Escape reaches it.)
  await b.panel._calibrationTab._wizard._act("exit"); await tick(12);
  assert.deepEqual([cancels(b.backend), b.backend.session.phase, b.wiz()], [1, "cancelled", null]);
  // A live session this tab guides: «Exit without saving» cancels it (guided «solo tempi» here; geometry as before).
  reset();
  const c = await mount();
  await c.startFor("cover.kitchen_two", "C");
  c.backend.hass.states["cover.kitchen_two"] = { state: "closed", attributes: { current_position: 0 } };
  await c.press("current");
  const radio = c.wiz().querySelector('input[name="case"][value="times_only"]'); radio.checked = true; radio.dispatchEvent(new Event("change", { bubbles: true })); await tick();
  await c.press("case"); await c.press("begin");
  assert.equal(c.backend.session.mode, "guided");
  c.wiz().querySelector('[data-wiz="exit"]').click(); await tick();
  c.wiz().querySelector('[data-wiz="leave"]').click(); await tick(12);
  assert.deepEqual([cancels(c.backend), c.backend.session.phase], [1, "cancelled"]);
});

test("L-10: the row of a session in progress cancels it from its ⋮ menu, after a confirmation, taking control first", async () => {
  const session = { entry_id: "one", entity_id: "cover.living_one", session_id: "session-1", sequence: 9, revision: 7, mode: "guided", origin: "wizard",
    phase: "opening", reason: null, values: {}, stop_requested: false, save_modes: ["new"], _started: 0 };
  const { tab: host, backend, panel } = await mount({ session, owner: false });
  const tab = host();
  const row = (root, entity) => root.querySelector(`.cal-row[data-entity="${entity}"]`);
  const menu = () => row(tab, "cover.living_one").querySelector(".cal-menu");
  row(tab, "cover.living_one").querySelector('[data-action="cal-tab-menu"]').click(); await tick();
  const item = menu().querySelector('[data-action="cal-tab-abort"]');
  assert.ok(item, "«Cancel the calibration in progress» in the menu of the row in progress");
  assert.equal(item.textContent.trim(), "Cancel the calibration in progress");
  assert.equal(row(tab, "cover.kitchen_two").querySelector('[data-action="cal-tab-abort"]'), null, "only there");
  item.click(); await tick();
  const dialog = panel.shadowRoot.querySelector("dialog.cal-dialog");
  assert.equal(dialog.querySelector("#cal-dialog-title").textContent, "Cancel the calibration in progress?");
  assert.match(dialog.textContent, /whichever tab or device is guiding it: nothing measured is saved/);
  // L-94: a confirmation on the dialog's own footer (Back, then the red action), not in the body of a hosted dialog.
  const foot = dialog.querySelector(".dialog-foot");
  assert.equal(foot.hidden, false);
  assert.deepEqual([...foot.querySelectorAll("button")].map((button) => [button.textContent, button.className]),
    [["Back", "cal-dialog-close cal-dialog-cancel"], ["Cancel the calibration", "danger cal-abort-confirm"]]);
  assert.equal(dialog.querySelector(".cal-dialog-body button"), null);
  assert.equal(backend.calls.filter((message) => message.action === "cancel").length, 0, "nothing before the confirmation");
  dialog.querySelector("[data-abort-confirm]").click(); await new Promise((resolve) => setTimeout(resolve, 200)); await tick(8);
  assert.ok(backend.subscriptions.some((message) => message.type === "myhome/cover_calibration/resume" && message.claim && message.sequence === 9), "control taken");
  assert.equal(backend.session.phase, "cancelled");
  assert.equal(panel.shadowRoot.querySelector("dialog.cal-dialog[open]"), null, "the dialog closes");
  assert.equal(tab.querySelector(".cal-tab-busy"), null, "the cover is free again");
});

test("L-25: the owner publishes its screen and a reader shows the same step, not one behind", async () => {
  // The owner: every screen it draws is published (code, path, the acknowledgements made on it).
  const { backend, press, startFor, code } = await mount();
  await startFor("cover.living_one");
  await press("begin"); await move(backend); await press("endpoint"); backend.stopped(); await tick();
  const shown = () => backend.calls.filter((message) => message.action === "show").at(-1)?.screen;
  assert.deepEqual([shown().code, shown().path, shown().local.ackLift], [code(), "A", false]);
  await press("ack:lift");
  assert.deepEqual([shown().code, shown().local.ackLift], ["A-07", true], "a local acknowledgement is published at once");
  // A reader of a session whose owner acknowledged the ascent shows the travel question, as the owner does.
  reset();
  const view = { phase: "reading", step: "opening", reading_kind: "travel", values: { opening_time: 22 }, travel_cm: null, expected_cm: null, can_repeat: true,
    screen: { code: "A-13", path: "A", scope: null, local: { ackLift: true, ackAscent: true } } };
  const reader = await mount({ session: resumed(view), owner: false });
  reader.tab().querySelector('[data-action="cal-tab-session"]').click(); await tick(6);
  assert.equal(reader.code(), "A-13", "not A-12, the screen the owner already left");
});

test("an owner that loses control and takes it back publishes its screen again, even the same one", async () => {
  const { backend, press, startFor, wiz } = await mount();
  await startFor("cover.living_one");
  await press("begin"); await move(backend); await press("endpoint"); backend.stopped(); await tick();
  const shows = () => backend.calls.filter((message) => message.action === "show").length;
  const before = shows();
  // Another device takes control: this tab reads; then it takes control back on the same step.
  const owned = backend.view;
  backend.view = (extra = {}) => owned({ owner: false, read_only: true, ...extra });
  backend.emit(); await tick(6);
  backend.view = owned;
  wiz().querySelector('[data-wiz="take"]').click(); await tick();
  wiz().querySelector('[data-wiz="take"]').click(); await tick(8);
  assert.ok(shows() > before, "the same screen is published again for the new ownership");
});

test("L-52: a reader that takes control continues on the step its former owner showed", async () => {
  const view = { phase: "briefing", step: "half_open", reading_kind: "half_open", values: { opening_time: 22, closing_time: 21 },
    screen: { code: "A-15", path: "A", scope: null, local: { ackLift: true, ackAscent: true } } };
  const { backend, tab, wiz, code } = await mount({ session: resumed(view), owner: false });
  tab().querySelector('[data-action="cal-tab-session"]').click(); await tick(6);
  assert.equal(code(), "A-15");
  // The owner acknowledges the descent: it now shows «Before the automatic readings» (A-16), a step of its own.
  backend.session.screen = { code: "A-16", path: "A", scope: null, local: { ackLift: true, ackAscent: true, ackDescent: true } };
  backend.emit(); await tick(6);
  assert.equal(code(), "A-16", "the reader follows");
  wiz().querySelector('[data-wiz="take"]').click(); await tick();
  wiz().querySelector('[data-wiz="take"]').click(); await tick(8);
  assert.equal(wiz().querySelector(".wiz-readonly"), null, "now the owner");
  assert.equal(code(), "A-16", "on the same step, not one back on A-15");
});

test("L-103: whichever step the owner shows, a reader that takes control lands on that same step, the first time too", async () => {
  // Owner A walks path A to the check, every screen published; at each step the session (with A's screen) is copied.
  const { backend, press, type, startFor, code } = await mount();
  await startFor("cover.living_one");
  const steps = [];
  const at = () => steps.push({ code: code(), session: structuredClone(backend.session) });
  const walk = [
    () => press("begin"), async () => { await move(backend); await press("endpoint"); backend.stopped(); await tick(); },
    () => press("ack:lift"), async () => { await press("next"); await move(backend, { seconds: 4.4 }); await press("lift"); backend.stopped(); await tick(); },
    () => type("3"), async () => { await move(backend); await press("endpoint"); backend.stopped(); await tick(); },
    async () => { await press("next"); await move(backend, { seconds: 22.2 }); await press("endpoint"); backend.stopped(); await tick(); },
    () => type("198"), () => press("ack:ascent"),
    async () => { await press("next"); await move(backend, { seconds: 21.7 }); await press("endpoint"); backend.stopped(); await tick(); },
    () => press("ack:descent"), () => press("ack:readings"), () => move(backend, { auto: true, stop: true }),
    () => type("97"), async () => { await move(backend, { seconds: 24 }); await press("endpoint"); backend.stopped(); await tick(); },
    () => move(backend, { auto: true, stop: true }), () => type("101"),
  ];
  for (const step of walk) { await step(); at(); }
  assert.ok(steps.some((step) => step.code === "A-12"), "the ascent's summary is among them");
  for (const step of steps) assert.equal(step.session.screen?.code, step.code, `${step.code}: published by its owner`);
  assert.equal(steps.at(-1).code, "A-21v");
  // B: another device, reading the session since its first step and following it up to that step (A's screens reach
  // it while it reads), then taking control: Home Assistant drops A's screen on the claim.
  for (const step of steps) {
    reset();
    const b = await mount({ session: structuredClone(steps[0].session), owner: false });
    b.tab().querySelector('[data-action="cal-tab-session"]').click(); await tick(6);
    assert.equal(b.code(), steps[0].code);
    const live = b.backend.session;
    for (const name of Object.keys(live)) if (!(name in step.session)) delete live[name];
    Object.assign(live, structuredClone(step.session));
    b.backend.emit(); await tick(6);
    assert.equal(b.code(), step.code, `reader follows to ${step.code}`);
    b.wiz().querySelector('[data-wiz="take"]').click(); await tick();
    b.wiz().querySelector('[data-wiz="take"]').click(); await tick(8);
    assert.equal(b.wiz().querySelector(".wiz-readonly"), null, `${step.code}: B owns it`);
    assert.equal(b.code(), step.code, `${step.code}: B continues on A's step, not one back`);
    const published = b.backend.calls.filter((message) => message.action === "show").at(-1)?.screen;
    assert.equal(published?.code, step.code, `${step.code}: and publishes it as its own`);
  }
});

test("review N2: on taking control a reader adopts the owner's screen whole: a choice the owner has cleared is not kept", async () => {
  const view = { phase: "briefing", step: "half_open", reading_kind: "half_open", values: { opening_time: 22, closing_time: 21 },
    screen: { code: "A-15", path: "A", scope: null, local: { ackLift: true, ackAscent: true } } };
  const { backend, tab, wiz, code, press } = await mount({ session: resumed(view) });
  tab().querySelector('[data-action="cal-tab-session"]').click(); await tick(6);
  assert.equal(code(), "A-15");
  await press("ack:descent");
  assert.equal(code(), "A-16", "this tab, owner, acknowledged the descent");
  // Another device takes control and shows A-15 (the descent not acknowledged there): this tab reads it.
  const owned = backend.view;
  backend.session.screen = { code: "A-15", path: "A", scope: null, local: { ackLift: true, ackAscent: true, ackDescent: null } };
  backend.view = (extra = {}) => owned({ owner: false, read_only: true, ...extra });
  backend.emit(); await tick(6);
  assert.equal(code(), "A-15", "the reader shows the owner's step");
  // This tab takes control back: the owner's screen is adopted whole, its own stale acknowledgement is dropped.
  backend.view = owned;
  wiz().querySelector('[data-wiz="take"]').click(); await tick();
  wiz().querySelector('[data-wiz="take"]').click(); await tick(8);
  assert.equal(wiz().querySelector(".wiz-readonly"), null);
  assert.equal(code(), "A-15", "not A-16 from its own earlier acknowledgement");
});

test("L-28: a reopened session keeps the path it was started on, so a path A on a cover with a profile ends on A-34 with fine-tuning", async () => {
  const session = resumed({ phase: "review", step: "half_close", reading_kind: "half_close", readings: { gap: 3, half_open: 97, half_close: 101 },
    geometry: { slat_time_s: 4.4, opening_roll: 1.83, closing_roll: 1.91 }, screen: { code: "A-21", path: "A", scope: null, local: { ackAscent: true, ackDescent: true, ackReadings: true } } });
  const { tab, code, panel } = await mount({ session });
  tab().querySelector('[data-action="cal-tab-session"]').click(); await tick(6);
  assert.match(code(), /^A-21/, "path A's summary, not C-21");
  assert.equal(panel._calibrationTab._wizard._local.path, "A");
});

test("L-26: the A-34 of a reader is the owner's (what was saved, the old profile left alone) with «Close» only", () => {
  const t = (key) => translations.en[key] || key;
  const screen = buildScreen({ t, stage: "saved", path: "A", cover: { name: "Kitchen shutter 2" },
    local: { saved: { target: "profile_new", name: "kitchen_big", orphan: "old_one", reader: true } } });
  assert.equal(screen.code, "A-34");
  assert.deepEqual(screen.actions.map((action) => action.id), ["close"]);
  assert.deepEqual(screen.links, []);
  assert.match(screen.body, /kitchen_big/);
  assert.match(screen.note.text, /old_one/, "the note on the old profile, as the owner sees it");
  assert.match(screen.body, /If the other device goes on with fine-tuning, there is nothing more to follow here\.$/, "L-49: nothing more to follow");
  assert.deepEqual(screen.actions.map((action) => [action.id, action.label]), [["close", "Back to the list"]]);
});

test("L-40: a reader whose session is cancelled where it is guided ends on its own screen, Close only, and stops talking to the session", async () => {
  const { backend, tab, wiz, title, panel } = await mount({ session: resumed({ phase: "briefing", step: "half_open", reading_kind: "half_open" }), owner: false });
  tab().querySelector('[data-action="cal-tab-session"]').click(); await tick(6);
  Object.assign(backend.session, { phase: "cancelled", reason: "cancelled" }); backend.emit(); await tick(6);
  assert.equal(title(), "The measurement was cancelled from another device");
  assert.deepEqual([...wiz().querySelectorAll(".wiz-actions .wiz-action")].map((button) => [button.dataset.wiz, button.disabled]), [["close", false]], "no Start over for a reader");
  assert.equal(panel._calibrationTab._wizard._gateway, null, "the session is let go: no heartbeat, no «expired», no flicker");
});

test("A-03 and A-16 have no Cancel of their own: the header's Abandon the calibration does the same", () => {
  const t = (key) => translations.it[key] || key;
  const a03 = buildScreen({ t, language: "it", stage: "before", path: "A", cover: { name: "Kitchen 2" } });
  const a16 = buildScreen({ t, language: "it", stage: "session", path: "A", cover: { name: "Kitchen 2" },
    view: { mode: "geometry", step: "half_open", phase: "briefing", values: { closing_time: 21 }, samples: {}, readings: {}, slats: true, owner: true, sequence: 9 },
    local: { ackDescent: true } });
  for (const screen of [a03, a16]) {
    assert.deepEqual(screen.actions.map((action) => action.id), [screen.code === "A-03" ? "begin" : "ack:readings"], screen.code);
    assert.equal(screen.discard, true, `${screen.code} keeps the header's Abandon`);
  }
  assert.deepEqual([a03.code, a16.code], ["A-03", "A-16"]);
});

test("A-04 lasts the whole positioning; A-05 then offers the end stop and T033 at once, Stop while the motor runs; A-10 never has T033", async () => {
  const { backend, wiz, code, press, actions, startFor } = await mount();
  await startFor("cover.living_one");
  await press("begin");
  backend.started(); backend.tick(5); backend.emit(); await tick();
  assert.equal(code(), "A-04", "5 s into a 21.7 s closing: still positioning");
  assert.deepEqual([...wiz().querySelectorAll(".wiz-actions .wiz-action")].map((item) => item.dataset.wiz), ["endpoint", "misbehaved", "stop"], "L-116: Stop last; L-119: A-05's bar from the start");
  assert.equal(wiz().querySelector('[data-wiz="endpoint"]').disabled, true, "L-119: the end stop waits for the cover to arrive");
  assert.match(wiz().querySelector(".wiz-wait-remaining").textContent, /17 s/, "the time left counts down over the closing time");
  backend.tick(16); backend.emit(); await tick();
  assert.equal(code(), "A-04", "21 s in: still positioning");
  // No new view: the screen's own clock ends the positioning (the time runs on between two views).
  const now = performance.now.bind(performance);
  performance.now = () => now() + 2000;
  try { await new Promise((resolve) => setTimeout(resolve, 250)); } finally { performance.now = now; }
  assert.equal(code(), "A-05", "past the closing time, drawn whole by the clock's tick");
  const kinds = () => [...wiz().querySelectorAll(".wiz-actions .wiz-action")].map((item) => [item.dataset.wiz, item.className.replace("wiz-action ", "")]);
  const misbehaved = () => wiz().querySelector('[data-wiz="misbehaved"]');
  assert.deepEqual(kinds(), [["endpoint", "primary"], ["misbehaved", "secondary"], ["stop", "stop"]], "Stop while moving, last (L-116)");
  assert.equal(misbehaved().textContent, "It did not do what it should");
  assert.equal(misbehaved().disabled, false, "active at once");
  assert.equal(misbehaved().hasAttribute("aria-disabled"), false);
  assert.equal(wiz().querySelector('[data-wiz="endpoint"]').disabled, false);
  // Home Assistant says the motor stopped (a stop frame, real or the actuator's timer): Stop leaves.
  backend.session.motor_stopped = true; backend.emit(); await tick();
  assert.equal(code(), "A-05");
  assert.deepEqual(kinds(), [["endpoint", "primary"], ["misbehaved", "secondary"], ["stopped", "status is-still"]], "no Stop once the motor stopped: its state (U-9)");
  await press("misbehaved");
  assert.equal(actions().at(-1), "stop", "today it ends the session: nothing measured yet is lost");
  await tick();
  assert.equal(code(), "S-01");

  const t = (key) => translations.it[key] || key;
  const closing = { effective: { closing: { value: 21.7, origin: "native_fallback" } } };
  const run = (step, path, extra = {}, elapsed = 25) => buildScreen({ t, language: "it", stage: "session", path, scope: path === "C" ? "times_and_rolls" : null,
    cover: { name: "Kitchen 2", ...closing }, elapsed,
    view: { mode: "geometry", step, phase: "closing", values: {}, samples: { lift: 4 }, readings: { gap: 3 }, slats: true, owner: true, sequence: 5, ...extra } });
  // The motor stopping ends the positioning before its time.
  assert.equal(run("home", "A", { motor_stopped: true }, 3).code, "A-05");
  assert.deepEqual([run("reset", "A", {}, 3).code, run("reset", "A", {}, 3).actions.map((action) => action.id)], ["A-09", ["endpoint", "stop"]], "A-09 lasts the positioning too, with the end stop at once");
  // U-9: once the estimated run and a margin are over (21.7 s + 15 %), the cover has stopped: its state in place of Stop.
  assert.deepEqual(run("reset", "A", {}, 30).actions.map((action) => action.id), ["endpoint", "stopped"], "the run is over");
  assert.deepEqual(run("home", "A", {}, 30).actions.map((action) => action.id), ["endpoint", "misbehaved", "stopped"]);
  assert.deepEqual([run("reset", "A", {}, 22.5).code, run("reset", "A", {}, 22.5).actions.map((action) => action.id)], ["A-10", ["endpoint", "stop"]],
    "a stop on A-10 would discard the lift-off: no T033 until repeat_step");
  assert.deepEqual(run("reset", "A", { motor_stopped: true }, 3).actions.map((action) => action.id), ["endpoint", "stopped"], "Stop only while moving");
  assert.deepEqual([run("home", "C", {}, 22.5).code, run("home", "C", {}, 22.5).actions.map((action) => [action.id, !!action.disabled])],
    ["C-05", [["endpoint", false], ["misbehaved", false], ["stop", false]]], "the C clone too");
  // L-58: a reader at rest has no button of the step (L-75: the note says the other device confirms; the text is the step's).
  // L-119: the finished line that took Stop's place stays in the reader's bar too.
  const reader = run("home", "C", { read_only: true, owner: false, owner_present: true, motor_stopped: true });
  assert.deepEqual(reader.actions.map((action) => action.id), ["stopped"]);
  assert.doesNotMatch(reader.body, /altro dispositivo/);
  assert.equal(reader.readOnly.text, "Un altro dispositivo guida la calibrazione.");
});

test("a positioning lasts the share of the travel still ahead, from the cover's estimated position", async () => {
  const t = (key) => translations.en[key] || key;
  const cover = { name: "Kitchen shutter 2", effective: { closing: { value: 20, origin: "native_fallback" } } };
  const wait = (view, runPosition, elapsed = 0, local = {}) => buildScreen({ t, stage: "session", path: "A", cover, runPosition, elapsed, local,
    view: { mode: "geometry", values: { opening_time: 22, closing_time: 21 }, samples: { lift: 4 }, readings: { gap: 3 }, slats: true, owner: true, sequence: 3, ...view } });
  const home = { step: "home", phase: "closing" };
  assert.deepEqual([wait(home, 50, 1).code, wait(home, 50, 1).wait.progress.remaining], ["A-04", 9], "half open: half the closing time");
  assert.equal(wait(home, 50, 10.5).code, "A-05", "past that half");
  assert.deepEqual([wait(home, null, 1).wait.progress.remaining, wait(home, null, 15).code], [19, "A-04"], "position unknown: the full time");
  assert.deepEqual([wait(home, 0, 0.5).code, wait(home, 0, 0.5).wait.progress.remaining, wait(home, 0, 2.5).code], ["A-04", 2, "A-05"],
    "already closed: a short run until the bus confirms");
  assert.equal(wait({ ...home, motor_stopped: true }, 100, 1).code, "A-05", "the motor's own stop still ends it early");
  assert.deepEqual([wait({ step: "reset", phase: "closing" }, 20, 3).code, wait({ step: "reset", phase: "closing" }, 20, 5).code], ["A-09", "A-10"]);
  const top = { step: "top", phase: "opening" };
  assert.equal(wait(top, 30, 1, { ackReadings: true }).wait.progress.remaining, Math.ceil(22 * 0.7 - 1), "the return to the top runs 70 % of the opening time");
  // On screen: the position is read once, when the run starts.
  const { backend, code, press, startFor, wiz } = await mount();
  backend.hass.states = { "cover.living_one": { state: "open", attributes: { current_position: 50 } } };
  await startFor("cover.living_one");
  await press("begin");
  backend.started(); backend.tick(5); backend.emit(); await tick();
  assert.equal(code(), "A-04");
  assert.match(wiz().querySelector(".wiz-wait-remaining").textContent, /\b6 s/, "21.7 s × 50 % − 5 s");
  backend.hass.states["cover.living_one"].attributes.current_position = 100;
  backend.tick(6); backend.emit(); await tick();
  assert.equal(code(), "A-05", "a later position does not stretch the run");
});

test("L-14: a return before a tape reading waits for the user: its end stop is enabled once the motor stopped or its time ran, and the next run starts from that press", async () => {
  const toTop = async (context) => {
    const { backend, press, type, startFor, code } = context;
    await startFor("cover.living_one");
    await press("begin"); await move(backend); await press("endpoint"); backend.stopped(); await tick();
    await press("ack:lift"); await press("next"); await move(backend, { seconds: 4.4 }); await press("lift"); backend.stopped(); await tick();
    await type("3"); await move(backend); await press("endpoint"); backend.stopped(); await tick();
    await press("next"); await move(backend, { seconds: 22.2 }); await press("endpoint"); backend.stopped(); await tick();
    await type("198"); await press("ack:ascent");
    await press("next"); await move(backend, { seconds: 21.7 }); await press("endpoint"); backend.stopped(); await tick();
    await press("ack:descent"); await press("ack:readings"); await move(backend, { auto: true, stop: true });
    await type("97");
    assert.equal(code(), "A-19");
    backend.started(); await tick();
  };
  const endpoints = (backend) => backend.calls.filter((message) => message.action === "endpoint").length;
  const button = (context) => context.wiz().querySelector('[data-wiz="endpoint"]');
  // 1. The motor stops by itself: nothing is sent, the end stop comes on; the user's press, then the half descent.
  const first = await mount();
  await toTop(first);
  const before = endpoints(first.backend);
  assert.equal(button(first).disabled, true, "while it runs towards the top: off");
  first.backend.tick(10); first.backend.session.motor_stopped = true; first.backend.emit(); await tick(8);
  assert.equal(endpoints(first.backend), before, "nothing confirmed by itself");
  assert.equal(button(first).disabled, false, "on, once the motor stopped");
  await first.press("endpoint"); first.backend.stopped(); await tick(6);
  assert.equal(endpoints(first.backend), before + 1);
  assert.equal(first.actions().at(-1), "next", "the half descent starts from the press");
  // 2. No stop frame: the end stop comes on once the rest of the measured ascent has run (L-120: from where the
  // half-ascent run stopped, 22.2 − (4.4 + 22.2) / 2 = 8.9 s; no margin), and still waits.
  const second = await mount();
  await toTop(second);
  const base = endpoints(second.backend);
  second.backend.tick(8.8); second.backend.emit(); await tick(6);
  assert.equal(button(second).disabled, true, "8.8 s: not yet");
  assert.match(second.wiz().querySelector(".wiz-wait-remaining").textContent, /1 s/, "the bar still has its last second");
  second.backend.tick(0.2); second.backend.emit(); await tick(6);
  assert.equal(button(second).disabled, false, "9.0 s: on");
  assert.equal(second.wiz().querySelector(".wiz-wait-remaining").hidden, true, "and the bar has ended at that instant");
  second.backend.tick(30); second.backend.emit(); await tick(6);
  assert.equal(endpoints(second.backend), base, "and nothing goes on by itself");
});

test("L-14: the return to the bottom before a repeated half-ascent reading waits for the user too", async () => {
  const { backend, press, type, startFor, code, wiz } = await mount();
  await startFor("cover.living_one");
  await press("begin"); await move(backend); await press("endpoint"); backend.stopped(); await tick();
  await press("ack:lift"); await press("next"); await move(backend, { seconds: 4.4 }); await press("lift"); backend.stopped(); await tick();
  await type("3"); await move(backend); await press("endpoint"); backend.stopped(); await tick();
  await press("next"); await move(backend, { seconds: 22.2 }); await press("endpoint"); backend.stopped(); await tick();
  await type("198"); await press("ack:ascent");
  await press("next"); await move(backend, { seconds: 21.7 }); await press("endpoint"); backend.stopped(); await tick();
  await press("ack:descent"); await press("ack:readings"); await move(backend, { auto: true, stop: true });
  assert.equal(code(), "A-18");
  await press("repeat"); await tick();
  const step = backend.session.step;
  assert.ok(["reset", "home"].includes(step), `back to the bottom (${step})`);
  const before = backend.calls.filter((message) => message.action === "endpoint").length;
  backend.started(); backend.tick(8); backend.session.motor_stopped = true; backend.emit(); await tick(6);
  assert.equal(backend.calls.filter((message) => message.action === "endpoint").length, before, "not confirmed by itself");
  assert.equal(wiz().querySelector('[data-wiz="endpoint"]').disabled, false, "the user confirms it");
});

test("Abandon and the X always ask first while a session is open, on every step, preview included", async () => {
  const { backend, wiz, code, press, type, startFor } = await mount();
  const asks = async (label) => {
    for (const selector of ['.wiz-head [data-wiz="exit"].wiz-discard', '.wiz-head .wiz-x']) {
      const button = wiz().querySelector(selector);
      assert.ok(button, `${selector} on ${label}`);
      button.click(); await tick();
      const dialog = wiz().querySelector(".wiz-dialog:not(.wiz-nav-dialog)");
      assert.equal(dialog.hidden, false, `${selector} asks on ${label}`);
      dialog.querySelector('[data-wiz="stay"]').click(); await tick();
      assert.ok(wiz(), `still open on ${label}`);
    }
  };
  await startFor("cover.living_one");
  await press("begin");
  await asks("A-04 before the first view arrives");
  await tick();
  await asks(code());
  backend.started(); backend.tick(30); backend.emit(); await tick();
  await asks(code());
  await press("endpoint"); await asks("the stop after A-05"); backend.stopped(); await tick();
  await asks(code());
  await press("ack:lift"); await asks(code());
  await press("next"); await move(backend, { seconds: 4 }); await asks(code());
  await press("lift"); backend.stopped(); await tick(); await asks(code());
  await type("3"); await asks(code());
  // A problem Home Assistant reports as a step (L2) keeps the session open: Abandon still asks.
  backend.session.problem = { reason: "unexpected_stop" }; backend.emit(); await tick();
  assert.equal(code(), "S-01");
  await asks("S-01 as a step");
  delete backend.session.problem; backend.emit(); await tick();
  // A preview (path B) asks too.
  const preview = await mount({ stageMs: 30 });
  await preview.startFor("cover.living_one", "B");
  await preview.press("profile"); await preview.press("begin"); await settle();
  for (const selector of ['.wiz-head [data-wiz="exit"].wiz-discard', '.wiz-head .wiz-x']) {
    preview.wiz().querySelector(selector).click(); await tick();
    assert.equal(preview.wiz().querySelector(".wiz-dialog:not(.wiz-nav-dialog)").hidden, false, `${selector} asks in the preview (${preview.code()})`);
    preview.wiz().querySelector('[data-wiz="stay"]').click(); await tick();
  }
});

test("a reopened session asks before leaving once this tab owns it again", async () => {
  const session = { entry_id: "one", entity_id: "cover.living_one", session_id: "session-1", sequence: 9, revision: 7, mode: "geometry",
    phase: "briefing", step: "lift", reason: null, values: {}, samples: {}, readings: {}, geometry: {}, travel_cm: null, stop_requested: false,
    slats: true, lift_attempts: 1, still_resting: false, gap_warning: false, touching_cm: 1, max_gap_cm: 20, gap_warn_cm: 10,
    saved_travel_cm: 195, expected_cm: null, can_repeat: false, save_modes: ["new"], reading_kind: "lift" };
  const { tab, wiz, code } = await mount({ session, owner: false });
  tab().querySelector('[data-action="cal-tab-session"]').click(); await tick(6);
  assert.ok(wiz().querySelector(".wiz-readonly"), "a reader first");
  wiz().querySelector(".wiz-head .wiz-x").click(); await tick();
  assert.equal(wiz(), null, "a reader leaves nothing behind: no question");
  tab().querySelector('[data-action="cal-tab-session"]').click(); await tick(6);
  wiz().querySelector('[data-wiz="take"]').click(); await tick(6);
  if (wiz().querySelector('[data-wiz="take"]')) { wiz().querySelector('[data-wiz="take"]').click(); await tick(6); }
  assert.equal(wiz().querySelector(".wiz-readonly"), null, `the owner again on ${code()}`);
  wiz().querySelector(".wiz-head .wiz-x").click(); await tick();
  assert.equal(wiz().querySelector(".wiz-dialog:not(.wiz-nav-dialog)").hidden, false, "the owner is asked");
});

test("leaving the tab with a live session open asks: continue, leave and resume later, or leave without saving", async () => {
  const { panel, root, backend, tab, wiz, code, press, startFor } = await mount();
  const view = (id) => root.querySelector(`[data-action="cover-view"][data-id="${id}"]`);
  const nav = () => wiz()?.querySelector(".wiz-nav-dialog");
  // Before any session nothing is asked.
  tab().querySelector('[data-action="cal-tab-measure"]').click(); await tick();
  assert.equal(panel._beforeLeave?.(), null, "A-01: nothing to ask");
  view("devices").click(); await tick();
  assert.equal(panel._coverView, "devices");
  view("calibration").click(); await tick(6);
  await startFor("cover.living_one");
  const unloads = [];
  const add = window.addEventListener.bind(window), remove = window.removeEventListener.bind(window);
  window.addEventListener = (type, handler, ...rest) => { if (type === "beforeunload") unloads.push(handler); return add(type, handler, ...rest); };
  window.removeEventListener = (type, handler, ...rest) => { if (type === "beforeunload") unloads.splice(unloads.indexOf(handler), 1); return remove(type, handler, ...rest); };
  try {
    await press("begin"); await tick();
    assert.equal(code(), "A-04");
    assert.equal(unloads.length, 1, "the browser warns before the page closes while the session is open");
    const event = new window.Event("beforeunload", { cancelable: true });
    unloads[0](event);
    assert.equal(event.defaultPrevented, true);
    // Another cover view: asked first, and nothing moves until the answer.
    window.dispatchEvent(new Event("pointerdown")); // a tap
    view("devices").click(); await tick();
    assert.equal(panel._coverView, "calibration");
    assert.equal(nav().hidden, false);
    assert.equal(nav().querySelector("h4").textContent, "Calibration in progress");
    assert.equal(nav().querySelector("#wiz-nav-body").textContent,
      "If you leave now, the calibration will stay open for 30 minutes and you will be able to resume it from the Calibration tab. If you cancel it, the measurements taken so far will be lost.");
    assert.equal(nav().querySelector(".wiz-dialog-x").hidden, true, "centred: no ✕ (R-5)");
    assert.deepEqual([...nav().querySelectorAll(".actions button")].map((button) => [button.dataset.wiz, button.textContent, button.className]),
      [["nav-discard", "Leave without saving", "wiz-leave"], ["nav-later", "Leave and resume later", ""], ["nav-stay", "Continue calibration", "primary"]]);
    assert.equal(root.activeElement, nav().querySelector(".wiz-dialog-card"), "X-6: after a tap, the dialog's box");
    nav().querySelector('[data-wiz="nav-stay"]').click(); await tick();
    assert.equal(panel._coverView, "calibration", "Continue keeps the wizard");
    assert.equal(nav().hidden, true);
    assert.equal(code(), "A-04");
    // The Bus monitor tab, then Escape: stays too.
    root.querySelector('[data-view="bus"]').click(); await tick();
    nav().dispatchEvent(new KeyboardEvent("keydown", { key: "Escape", bubbles: true })); await tick();
    assert.equal(panel._view, "entities");
    // Leave and resume later: the panel moves on, the session stays open in Home Assistant.
    view("devices").click(); await tick();
    nav().querySelector('[data-wiz="nav-later"]').click(); await tick(4);
    assert.equal(panel._coverView, "devices");
    assert.equal(backend.calls.some((message) => message.action === "cancel"), false, "nothing cancelled");
    assert.equal(unloads.length, 0, "the browser's warning leaves with the wizard");
    assert.notEqual(backend.session.phase, "cancelled");
    // Back in the tab: the cover in progress is first; this tab still owns its session (L-93): «Resume session».
    view("calibration").click(); await tick(8);
    const first = tab().querySelector(".cal-row");
    assert.equal(first.dataset.entity, "cover.living_one", "the cover in progress sits on top");
    assert.equal(first.querySelector(".cal-badge").dataset.status, "running");
    assert.equal(first.querySelector('[data-action="cal-tab-session"]').textContent, "Resume session");
    assert.match(tab().querySelector(".cal-tab-busy").textContent, /resume it with «Resume session»/);
    // Reopened, taken back, and left without saving: Cancel reaches Home Assistant first.
    first.querySelector('[data-action="cal-tab-session"]').click(); await tick(6);
    for (let i = 0; i < 2 && wiz().querySelector('[data-wiz="take"]'); i++) { wiz().querySelector('[data-wiz="take"]').click(); await tick(6); }
    view("profiles").click(); await tick();
    assert.equal(nav().hidden, false);
    nav().querySelector('[data-wiz="nav-discard"]').click(); await tick(8);
    assert.equal(backend.calls.filter((message) => message.action === "cancel").length, 1);
    assert.equal(panel._coverView, "profiles");
  } finally {
    window.addEventListener = add; window.removeEventListener = remove;
  }
});

test("a landscape phone has the wizard full screen in two columns, Stop at the top of the actions (responsive map §3.2, D11, D12)", async () => {
  const css = (await import("node:fs")).readFileSync(new URL("../../custom_components/myhome/frontend/panel/myhome-panel.css", import.meta.url), "utf8");
  const short = css.slice(css.lastIndexOf("@media (max-height: 499px) and (orientation: landscape) {"));
  assert.match(short, /\.wiz \{ position: fixed; inset: 0;/, "full screen at any width");
  assert.match(short, /\.wiz-head \{ box-sizing: border-box; min-height: 44px;/, "a 44 px header");
  assert.match(short, /\.wiz-steps \{ display: none; \}/, "no stepper, the strip instead");
  assert.match(short, /\.wiz-columns \{[^}]*grid-template-columns: minmax\(0, 1fr\) calc\(clamp\(240px, 35%, 280px\) \+ var\(--cal-safe-right\)\);/);
  assert.match(short, /\.wiz-main \{ min-height: 0; overflow-y: auto;/, "the content scrolls");
  assert.match(short, /\.wiz-actions \{ position: static; min-height: 0; overflow-y: auto;/, "the actions scroll by themselves");
  assert.doesNotMatch(short, /\[data-wiz="stop"\] \{ order: -1; \}/, "Z-3: Stop below the primary, as in every other layout");
});

test("the Calibration in progress dialog is 592 px wide when centred, its three buttons on one row", async () => {
  const css = (await import("node:fs")).readFileSync(new URL("../../custom_components/myhome/frontend/panel/myhome-panel.css", import.meta.url), "utf8");
  assert.match(css, /\.wiz-nav-dialog \.wiz-dialog-card \{ width: min\(100%, 592px\);/);
  assert.match(css, /@media \(min-width: 720px\) and \(min-height: 500px\) \{ \.wiz-nav-dialog \.wiz-dialog-card \.actions \{ flex-wrap: nowrap; \} \}/, "wherever it is centred");
});

test("a click on a confirmation's backdrop is Continue the calibration", async () => {
  const { backend, wiz, press, startFor, panel } = await mount();
  await startFor("cover.living_one");
  await press("begin");
  wiz().querySelector(".wiz-head .wiz-discard").click(); await tick();
  const exit = wiz().querySelector(".wiz-dialog:not(.wiz-nav-dialog)");
  assert.equal(exit.hidden, false);
  exit.querySelector(".wiz-dialog-card").click(); await tick();
  assert.equal(exit.hidden, false, "a click on the card keeps it");
  exit.click(); await tick();
  assert.equal(wiz().querySelector(".wiz-dialog:not(.wiz-nav-dialog)").hidden, true, "the backdrop continues");
  assert.equal(backend.calls.some((message) => message.action === "cancel"), false);
  const answer = panel._beforeLeave();
  wiz().querySelector(".wiz-nav-dialog").click();
  assert.equal(await answer, false, "Calibration in progress: the backdrop stays");
});

test("the time it stays open comes from Home Assistant's lease: 10 minutes after a movement", async () => {
  const { backend, wiz, press, startFor, panel } = await mount();
  await startFor("cover.living_one");
  await press("begin"); await tick();
  Object.assign(backend.session, { recovery_seconds: 600 }); backend.emit(); await tick();
  const answer = panel._beforeLeave();
  assert.match(wiz().querySelector("#wiz-nav-body").textContent, /will stay open for 10 minutes/);
  wiz().querySelector('[data-wiz="nav-stay"]').click();
  assert.equal(await answer, false);
});

test("a subscription replayed after a reconnection never starts a movement, even on a step that would continue by itself", async () => {
  const { backend, wiz, code, press, type, actions, startFor } = await mount();
  const callbacks = [];
  const subscribe = backend.hass.connection.subscribeMessage;
  backend.hass.connection.subscribeMessage = async (callback, message) => { callbacks.push(callback); return subscribe(callback, message); };
  await startFor("cover.living_one");
  await press("begin"); await move(backend); await press("endpoint"); backend.stopped(); await tick();
  await press("ack:lift"); await press("next"); await move(backend, { seconds: 4 }); await press("lift"); backend.stopped(); await tick();
  await type("3"); await move(backend); await press("endpoint"); backend.stopped(); await tick();
  await press("next"); await move(backend, { seconds: 22 }); await press("endpoint"); backend.stopped(); await tick();
  await type("198"); await press("ack:ascent");
  await press("next"); await move(backend, { seconds: 21 }); await press("endpoint"); backend.stopped(); await tick();
  await press("ack:descent"); await press("ack:readings"); await move(backend, { auto: true, stop: true });
  await type("97");
  assert.equal(code(), "A-19");
  await move(backend, { seconds: 24 });
  await press("endpoint");
  // The socket drops: the cover stops and the session moves on with nobody listening.
  const emit = backend.emit;
  backend.emit = () => { backend.session.sequence += 1; };
  backend.stopped();
  backend.emit = emit;
  assert.equal(backend.session.step, "half_close");
  const before = actions().length;
  // Home Assistant reconnects and replays the subscription: same sequence, a new attachment.
  callbacks.at(-1)(backend.view({ attachment: "attachment-2" }));
  await tick(8);
  assert.deepEqual(actions().slice(before), [], "no movement after a replay");
  assert.equal(code(), "A-19");
  assert.equal(wiz().querySelector('[data-wiz="next"]').disabled, false, "the run waits for a press");
});

test("after every change of screen the confirming buttons wait 700 ms, Stop never does", async () => {
  const { backend, tab, wiz, code } = await mount({ guardMs: 700 });
  const { CONFIRM_GUARD_MS } = await import("../../custom_components/myhome/frontend/panel/panel-calibration-wizard.js");
  assert.equal(CONFIRM_GUARD_MS, 700);
  const wait = (ms) => new Promise((resolve) => setTimeout(resolve, ms));
  const button = (id) => wiz().querySelector(`.wiz-actions [data-wiz="${id}"]`);
  const tap = async (id) => { await wait(720); assert.equal(button(id).disabled, false, id); button(id).click(); await tick(); };
  tab().querySelector('[data-action="cal-tab-measure"]').click(); await tick();
  assert.equal(button("choose").disabled, true, "a tap landing right after the change does nothing");
  button("choose").click(); await tick();
  assert.equal(code(), "A-01");
  const radio = wiz().querySelector(`.wiz-picker input[value='${JSON.stringify(["one", "cover.living_one"])}']`);
  radio.checked = true; radio.dispatchEvent(new Event("change", { bubbles: true }));
  await tap("choose");
  const a = wiz().querySelector('input[name="path"][value="A"]'); a.checked = true; a.dispatchEvent(new Event("change", { bubbles: true })); await tick();
  await tap("path"); await tap("begin");
  await move(backend, { seconds: 22.5 }); // past the opening, within the closing time + margin: Stop is still offered
  assert.equal(code(), "A-05");
  assert.equal(button("endpoint").disabled, true, "an end stop is never confirmed by the tap that started the run");
  assert.equal(button("stop").disabled, false, "Stop is always available");
  await tap("endpoint");
  assert.equal(backend.calls.filter((message) => message.action === "endpoint").length, 1);
});

test("a stop frame well before the estimated run asks to check the end stop; the confirmation stays active", () => {
  const t = (key) => translations.en[key] || key;
  const cover = { name: "Kitchen 2", effective: { closing: { value: 20, origin: "native_fallback" } } };
  const at = (view, local = {}) => buildScreen({ t, stage: "session", path: "A", cover, elapsed: 8, local,
    view: { mode: "geometry", values: { opening_time: 22, closing_time: 21 }, samples: { lift: 4 }, readings: { gap: 3 }, slats: true, owner: true, sequence: 4, ...view } });
  const early = at({ step: "home", phase: "closing", motor_stopped: true, stopped_elapsed: 8 });
  assert.equal(early.code, "A-05");
  assert.deepEqual(early.notices, [{ tone: "warning", result: true, text: "The cover stopped earlier than expected (8.0 s of about 20 s): check that it is really fully closed before continuing." }]);
  assert.equal(!!early.actions[0].disabled, false, "the end stop stays pressable");
  assert.deepEqual(at({ step: "home", phase: "closing", motor_stopped: true, stopped_elapsed: 13 }).notices, [], "past 60 %: no warning");
  assert.equal(at({ step: "reset", phase: "closing", motor_stopped: true, stopped_elapsed: 5 }).notices.length, 1, "A-10 too");
  assert.equal(at({ step: "reset", phase: "closing", motor_stopped: true, stopped_elapsed: 5 }, { ackReadings: true }).notices.length, 0,
    "not on a return that confirms itself on the stop frame (§9.53)");
  const top = at({ step: "top", phase: "opening", motor_stopped: true, stopped_elapsed: 5 });
  assert.match(top.notices[0].text, /really fully open/, "the manual return to the top");
  // D-12: stopped is not «positioning»: no wait block telling to wait for the stop, the state instead of Stop.
  assert.deepEqual([top.code, top.title, top.wait ?? null], ["A-09", "The cover has stopped", null]);
  assert.deepEqual(top.actions.map((action) => [action.id, action.kind]), [["endpoint", "primary"], ["misbehaved", "secondary"], ["stopped", "state"]],
    "Z-5: «It did not do what it should» as on A-05");
  assert.equal(at({ step: "top", phase: "opening" }).title, "Positioning cover", "while it runs it is still a positioning");
});

test("S-01 says what happened when the panel knows more: It did not do what it should", async () => {
  const t = (key) => translations.en[key] || key;
  const view = { mode: "geometry", phase: "interrupted", reason: "stopped", step: "home", values: {}, samples: {}, readings: {}, owner: true, sequence: 9 };
  assert.equal(buildScreen({ t, stage: "session", path: "A", view, local: { misbehaved: true }, cover: { name: "K" } }).body, "The cover did not do what it should. Restart the calibration.");
  assert.equal(buildScreen({ t, stage: "session", path: "A", view, local: { stopSent: true }, cover: { name: "K" } }).body, translations.en.calReason_stopped, "a plain Stop from here keeps its text");
  assert.equal(buildScreen({ t, stage: "session", path: "A", view, local: {}, cover: { name: "K" } }).body, translations.en.wiz_T524, "N-15: a Stop not sent from here came from elsewhere");
  const reader = buildScreen({ t, stage: "session", path: "A", cover: { name: "K" }, local: {},
    view: { mode: "geometry", phase: "briefing", step: "half_open", values: { closing_time: 21 }, samples: {}, readings: {}, read_only: true, owner: false, sequence: 4 } });
  assert.ok(reader.body.length > 0, "L-23 (decided 10 Oct): a reader sees the whole step, its text too");
  const { backend, wiz, code, press, startFor } = await mount();
  await startFor("cover.living_one");
  await press("begin"); await move(backend);
  await press("misbehaved"); await tick();
  assert.equal(code(), "S-01");
  assert.equal(wiz().querySelector(".wiz-body").textContent, "The cover did not do what it should. Restart the calibration.");
});

test("S-01 after the lift-off run stopped before ② says how to redo it; other steps keep the generic text", () => {
  const t = (key) => translations.en[key] || key;
  const body = (step) => buildScreen({ t, stage: "session", path: "A", cover: { name: "K" }, local: {},
    view: { mode: "geometry", phase: "interrupted", reason: "unexpected_stop", step, values: {}, samples: {}, readings: {}, owner: true, sequence: 9 } }).body;
  assert.equal(body("lift"), "The cover stopped before you pressed ②. Restart the calibration and press ② as soon as the bottom edge lifts off the rest.");
  assert.equal(body("half_open"), translations.en.wiz_T577, "R-7: our words, the maintainer's text left as it was");
});

test("L-108: a bar changed by data alone, with no recent touch, is enabled at once; right after a touch it waits", async () => {
  const wait = (ms) => new Promise((resolve) => setTimeout(resolve, ms));
  const view = { phase: "briefing", step: "lift", values: {}, screen: { code: "A-06", path: "A", scope: null, local: {} } };
  for (const touched of [false, true]) {
    reset();
    const { backend, tab, wiz, code } = await mount({ session: resumed(view), guardMs: 700 });
    tab().querySelector('[data-action="cal-tab-session"]').click(); await tick(6);
    assert.equal(code(), "A-06");
    await wait(720);
    // Home Assistant moves the session on (a timer, another device): this page touched just now, or not at all.
    if (touched) document.body.dispatchEvent(new Event("pointerdown", { bubbles: true }));
    Object.assign(backend.session, { phase: "briefing", step: "half_open", reading_kind: "half_open" });
    backend.emit(); await tick(6);
    assert.equal(code(), "A-15");
    const primary = wiz().querySelector(".wiz-actions .wiz-action.primary");
    if (!touched) {
      assert.equal(primary.disabled, false, "no recent touch: enabled at once");
      assert.equal(primary.classList.contains("is-settling"), false);
    } else {
      assert.equal(primary.disabled, true, "a touch 0 ms ago: the tap was meant for the old bar, held back");
      await wait(720); await tick();
      assert.equal(primary.isConnected ? primary.disabled : wiz().querySelector(".wiz-actions .wiz-action.primary").disabled, false);
    }
  }
});

test("review N3: the bar of a running motor that comes more than 700 ms after this tab's start is still held (②)", async () => {
  const wait = (ms) => new Promise((resolve) => setTimeout(resolve, ms));
  const view = { phase: "briefing", step: "lift", values: {}, screen: { code: "A-07", path: "A", scope: null, local: { ackLift: true } } };
  const { backend, tab, wiz, code, panel } = await mount({ session: resumed(view), guardMs: 700 });
  tab().querySelector('[data-action="cal-tab-session"]').click(); await tick(6);
  assert.equal(code(), "A-07");
  await wait(720);
  wiz().querySelector('.wiz-actions [data-wiz="next"]').click(); await tick(6);
  assert.match(backend.session.phase, /^starting_/, "answered: the motor is starting, not yet running");
  // The answer may change the bar by itself (Stop held until the session answers, L-77): that change does not use up
  // the press, the motor is not running yet.
  const wizard = panel._calibrationTab._wizard;
  wizard._guardIfTouched();
  assert.notEqual(wizard._ownPress, null, "the press still counts while the motor starts");
  await wait(800);
  // The motor runs (Home Assistant's next view), long after the press: ② appears under the finger that pressed ①.
  backend.started(); backend.emit(); await tick(6);
  const lift = wiz().querySelector('.wiz-actions [data-wiz="lift"]');
  assert.ok(lift, "② is there");
  assert.equal(lift.disabled, true, "held: the tap may have been meant for ①");
  await wait(720); await tick();
  assert.equal(wiz().querySelector('.wiz-actions [data-wiz="lift"]').disabled, false);
});

test("when the motor stops by itself on A-05 the end stop does not flicker disabled", async () => {
  const { backend, tab, wiz, code, press } = await mount({ guardMs: 700 });
  const wait = (ms) => new Promise((resolve) => setTimeout(resolve, ms));
  tab().querySelector('[data-action="cal-tab-measure"]').click(); await tick();
  const radio = wiz().querySelector(`.wiz-picker input[value='${JSON.stringify(["one", "cover.living_one"])}']`);
  radio.checked = true; radio.dispatchEvent(new Event("change", { bubbles: true })); await wait(720);
  wiz().querySelector('[data-wiz="choose"]').click(); await tick();
  const a = wiz().querySelector('input[name="path"][value="A"]'); a.checked = true; a.dispatchEvent(new Event("change", { bubbles: true })); await wait(720);
  wiz().querySelector('[data-wiz="path"]').click(); await tick(); await wait(720);
  wiz().querySelector('[data-wiz="begin"]').click(); await tick();
  backend.started(); backend.tick(30); backend.emit(); await tick();
  assert.equal(code(), "A-05");
  await wait(720);
  const endpoint = () => wiz().querySelector('[data-wiz="endpoint"]');
  assert.equal(endpoint().disabled, false);
  // The timer's stop frame: Stop goes away, nothing new appears under the finger.
  backend.session.motor_stopped = true; backend.emit(); await tick();
  assert.equal(wiz().querySelector('.wiz-actions [data-wiz="stop"]'), null);
  assert.equal(endpoint().disabled, false, "no hold for a button that only went away");
  void press;
});

test("a reopened slat and roll session of a cover that follows a profile is shown as path C", async () => {
  const session = { entry_id: "one", entity_id: "cover.kitchen_two", session_id: "session-1", sequence: 9, revision: 7, mode: "geometry",
    phase: "briefing", step: "lift", reason: null, values: {}, samples: {}, readings: {}, geometry: {}, travel_cm: null, stop_requested: false,
    slats: true, lift_attempts: 1, still_resting: false, gap_warning: false, touching_cm: 1, max_gap_cm: 20, gap_warn_cm: 10,
    saved_travel_cm: 110, expected_cm: null, can_repeat: false, save_modes: ["new"], reading_kind: "lift" };
  const { backend, tab, wiz, code, actions } = await mount({ session });
  tab().querySelector('[data-action="cal-tab-session"]').click(); await tick(6);
  assert.equal(code(), "C-06");
  assert.equal(wiz().querySelector(".wiz-steps li[data-step-state] > ul > li").textContent, "Remeasure all");
  assert.deepEqual(actions(), [], "reopening moves nothing");
  void backend;
});

test("the preview is announced before it starts and its end never says that something was saved", async () => {
  const t = (key) => translations.it[key] || key;
  // Under Ⓑ on A-02 the note now counts the profiles (R4-8); the preview is said where Ⓑ starts (B-02).
  assert.equal(buildScreen({ t, stage: "before", path: "B", cover: { name: "Kitchen 2" } }).actions[0].note, translations.it.wiz_T318);
  assert.equal(buildScreen({ t, stage: "before", path: "C", scope: "points_only", cover: { name: "Kitchen 2" } }).actions[0].note, translations.it.wiz_T318);
  assert.equal(buildScreen({ t, stage: "before", path: "C", scope: "times_only", cover: { name: "Kitchen 2" } }).actions[0].note, undefined, "times only is live");
  const review = buildScreen({ t, language: "it", stage: "session", path: "A", cover: { name: "Kitchen 2" },
    view: { mode: "geometry", phase: "review", values: { opening_time: 22, closing_time: 21 }, geometry: {}, travel_cm: 198, owner: true, sequence: 2, check: { gap_cm: 1, passed: true, check_seconds: 10.5 } } });
  assert.deepEqual(review.actions.map((action) => action.id), ["save"], "the summary only saves");
  const real = buildScreen({ t, language: "it", stage: "saved", path: "A", cover: { name: "Kitchen 2" }, local: { saved: { target: "profile_new", name: "kitchen_2" } } });
  assert.equal(real.actions.find((action) => action.id === "refine").note, undefined, "no note under the button");
  assert.equal(real.body, `${translations.it.wiz_T155.replace("{name}", "kitchen_2")}\n\n**Vuoi perfezionare la calibrazione?** Quattro letture col metro a un quarto e a tre quarti della corsa, più una verifica finale, renderanno la stima della posizione più precisa lungo tutta la corsa. Qualche minuto; la tapparella si muoverà.`,
    "T319 is the body's second paragraph");
  assert.deepEqual(real.actions.map((action) => [action.id, action.kind]), [["close", "primary"], ["assign-profile", "secondary"], ["another", "secondary"],
    ["open-cover", "secondary"], ["refine", "secondary"]], "review P2");
  const coverOnly = buildScreen({ t, language: "it", stage: "saved", path: "A", cover: { name: "Kitchen 2" }, local: { saved: { target: "cover_only" } } });
  assert.equal(coverOnly.actions.some((action) => action.id === "assign-profile"), false, "no profile written, nothing to assign");
  assert.deepEqual(real.stepper.phases.map((phase) => [phase.key, phase.state]).slice(-2), [["summary", "done"], ["finetune", "future"]]);
  assert.equal(real.stepper.phases.at(-1).label, "Perfezionamento (facoltativo)");
  assert.equal(buildScreen({ t, language: "it", stage: "saved", path: "C", cover: { name: "Kitchen 2" }, local: { saved: { target: "cover_only" } } })
    .actions.some((action) => action.id === "refine"), false, "not on C-34");
  const saved = buildScreen({ t, language: "it", stage: "saved", path: "C", cover: { name: "Kitchen 2" }, local: { saved: { preview: true }, baseSaved: true } });
  assert.equal(saved.title, "Anteprima del riepilogo");
  assert.equal(saved.body, translations.it.wiz_T322);
  assert.equal(saved.preview, true);
  // On screen the note sits under its path, inside the radio's label.
  const { tab, wiz, press, pickCover } = await mount({ language: "it" });
  tab().querySelector('[data-action="cal-tab-measure"]').click(); await tick();
  await pickCover();
  await press("choose");
  const b = wiz().querySelector('input[name="path"][value="B"]').closest(".wiz-radio");
  assert.deepEqual([...b.querySelectorAll("small")].map((line) => line.textContent), ["Basta misurare la corsa del telo.", "1 profilo disponibile"],
    "under B, its explanation then the profiles available");
});

test("A-02 advises a path from the cover and the gateway's profiles, and preselects it", () => {
  const t = (key) => translations.en[key] || key;
  const advice = (screen) => screen.choice.options.filter((option) => option.badge).map((option) => [option.value, option.badge]);
  const note = (screen) => screen.choice.options.find((option) => option.value === "B").note;
  const tall = { id: "tall", name: "tall_two_leaf", slat: 4.4, referenceTravel: 198 }, low = { id: "low", name: "low_one_leaf", slat: 0, referenceTravel: 112 };
  // Never calibrated, no profile on the gateway: Ⓐ advised and preselected, Ⓑ cannot be chosen and says why.
  const none = buildScreen({ t, stage: "path", profiles: [], cover: { name: "Kitchen 2", status: "to_measure" } });
  assert.deepEqual(advice(none), [["A", "Recommended"]]);
  assert.equal(none.choice.options.find((option) => option.value === "B").disabled, true);
  assert.deepEqual(note(none), [translations.en.wiz_T453, translations.en.wiz_T401]);
  assert.equal(none.choice.value, "A");
  assert.equal(none.actions[0].disabled, false);
  assert.equal(none.subline, "Never calibrated", "the context says only that");
  assert.match(none.body, /If in doubt, choose this option\.$/);
  const c = none.choice.options.find((option) => option.value === "C");
  assert.deepEqual([c.disabled, c.note, c.caption, c.notice], [true, ["Only what is off gets corrected: tape readings, times, or everything."], "Calibrate the cover first.", undefined],
    "no fine-tuning on a cover never measured: said as its grey caption (L-124)");
  // G2-2: the introduction is one line and the advice; each option explains itself in its note.
  assert.equal(none.body, 'There are **three measurement paths**.\n\n"First cover of its type" is the recommended option because the cover has never been measured and there are no saved profiles. If in doubt, choose this option.');
  assert.deepEqual(none.choice.options.map((option) => option.note[0]), [translations.en.wiz_T452, translations.en.wiz_T453, translations.en.wiz_T454]);
  // Never calibrated, profiles available, no saved travel: no badge, nothing preselected, Ⓑ lists the profiles to compare.
  const some = buildScreen({ t, stage: "path", profiles: [tall, low], cover: { name: "Kitchen 2", status: "to_measure" } });
  assert.deepEqual(advice(some), [["A", "Recommended"]], "always one advised option (G4-2)");
  assert.equal(some.choice.value, "A");
  assert.equal(some.actions[0].disabled, false);
  assert.deepEqual(note(some), [translations.en.wiz_T453, "2 profiles available", "tall_two_leaf · With slats · Reference travel 198 cm", "low_one_leaf · Without slats · Reference travel 112 cm"]);
  assert.match(some.body, /^There are \*\*three measurement paths\*\*\.\n\n"First cover of its type" is the recommended option because the cover has never been measured and, without the length of its curtain, there is no telling whether a saved profile fits it\. If in doubt, choose this option\.$/);
  assert.deepEqual(note(buildScreen({ t, stage: "path", profiles: [tall], cover: { name: "Kitchen 2", status: "to_measure" } }))[1], "1 profile available");
  // A saved travel within 5 % of a profile's reference: Ⓑ advised, preselected and explained (closest first).
  const close = { id: "close", name: "near", slat: 0, referenceTravel: 109 };
  const similar = buildScreen({ t, stage: "path", profiles: [tall, low, close], cover: { name: "Kitchen 2", status: "to_measure", travel_cm: 110 } });
  assert.deepEqual([advice(similar), similar.choice.value], [[["B", "Recommended"]], "B"]);
  assert.deepEqual(note(similar), [translations.en.wiz_T453, "3 profiles available", 'Travel similar to the profile "near" (110 cm vs 109 cm)', 'Travel similar to the profile "low_one_leaf" (110 cm vs 112 cm)']);
  assert.match(similar.body, /"Like a cover already measured" is the recommended option because .* If in doubt, choose this option\.$/);
  assert.deepEqual(advice(buildScreen({ t, stage: "path", profiles: [tall], cover: { name: "Kitchen 2", status: "to_measure", travel_cm: 110 } })), [["A", "Recommended"]], "198 cm is not similar: the first of its type");
  // Already calibrated: Ⓒ; another choice gets its warning under it (Ⓑ: "a different profile" only when one is followed).
  const calibrated = (local, profile) => buildScreen({ t, stage: "path", local, profiles: [tall], cover: { name: "Kitchen 2", status: "calibrated", detail: "rolls", last: "2026-10-01T09:00:00+00:00", profile } });
  assert.deepEqual([advice(calibrated({})), calibrated({}).choice.value, calibrated({}).subline], [[["C", "Recommended"]], "C", "Already calibrated (times and roll, Oct 1, 2026)"]);
  assert.deepEqual(note(calibrated({})), [translations.en.wiz_T453, "1 profile available"]);
  assert.equal(calibrated({}).choice.options.some((option) => option.warning), false);
  assert.equal(calibrated({ pathChoice: "B" }, { name: "tall_two_leaf" }).choice.options.find((option) => option.value === "B").warning, translations.en.wiz_T416);
  assert.equal(calibrated({ pathChoice: "B" }).choice.options.find((option) => option.value === "B").warning, translations.en.wiz_T417);
  assert.equal(calibrated({ pathChoice: "B" }, { name: "tall_two_leaf" }).subline,
    'Follows the profile "tall_two_leaf": the values are the profile\'s, without travel scaling, since the travel has not been measured.', "no measured travel (G2-1)");
});

test("review P1: with the previews off, A-02 never advises nor preselects a preview path; C-02 the same; on, as before", async () => {
  const t = (key) => translations.en[key] || key;
  const tall = { id: "tall", name: "tall_two_leaf", slat: 4.4, referenceTravel: 198 }, close = { id: "close", name: "near", slat: 0, referenceTravel: 109 };
  const cover = { name: "Kitchen 2", status: "to_measure", travel_cm: 110 };
  const option = (screen, value) => screen.choice.options.find((item) => item.value === value);
  // Off: Ⓑ visible, disabled, «Available in a future version» under it, the close profile still said; Ⓐ advised and chosen.
  const off = buildScreen({ t, stage: "path", profiles: [tall, close], cover, previews: false });
  assert.deepEqual([option(off, "B").disabled, option(off, "B").badge, option(off, "A").badge, off.choice.value], [true, "", "Recommended", "A"]);
  assert.equal(option(off, "B").note.at(-1), 'Travel similar to the profile "near" (110 cm vs 109 cm)');
  assert.equal(option(off, "B").notice, "Available in a future version", "L-115: a yellow note under its title");
  assert.match(off.body, /"First cover of its type" is the recommended option because the cover has never been measured: checking a similar profile will be available in a future version\. If in doubt, choose this option\.$/);
  // On: as before, Ⓑ advised and preselected.
  const on = buildScreen({ t, stage: "path", profiles: [tall, close], cover, previews: true });
  assert.deepEqual([option(on, "B").disabled, option(on, "B").badge, on.choice.value], [false, "Recommended", "B"]);
  // A-01 and A-02 show the step without a total, which the path decides.
  assert.equal(off.stepper.line, "Introduction · step 1");
  assert.equal(buildScreen({ t, stage: "choose", covers: [], cover: null, previews: false }).stepper.line, "Introduction · step 1");
  assert.match(buildScreen({ t, stage: "before", path: "A", cover }).stepper.line, / of \d+$/, "with the path fixed, the total");
  // C-02 off: the tape readings only disabled with the same caption, no badge, nothing chosen, Continue waits.
  const caseOff = buildScreen({ t, stage: "case", path: "C", cover: { ...cover, status: "calibrated" }, previews: false });
  assert.deepEqual([option(caseOff, "points_only").disabled, option(caseOff, "points_only").badge ?? "", caseOff.choice.value, caseOff.actions[0].disabled],
    [true, "", "", true]);
  assert.deepEqual([option(caseOff, "points_only").notice, option(caseOff, "points_only").noticeTone, option(caseOff, "points_only").warning],
    ["Available in a future version", "info", undefined], "L-124: under its title, as a blue information note");
  assert.equal(caseOff.body, translations.en.wiz_T190, "no sentence advising the tape readings");
  const caseOn = buildScreen({ t, stage: "case", path: "C", cover: { ...cover, status: "calibrated" }, previews: true });
  assert.deepEqual([option(caseOn, "points_only").disabled ?? false, caseOn.choice.value], [false, "points_only"]);
  // Mounted with the previews off (the panel's default): Ⓑ cannot be chosen, even pressed by other means.
  const { tab, wiz, press, pickCover, code, panel } = await mount({ previews: false });
  tab().querySelector('[data-action="cal-tab-measure"]').click(); await tick();
  await pickCover();
  await press("choose");
  const b = wiz().querySelector('input[name="path"][value="B"]');
  assert.equal(b.disabled, true);
  assert.match(b.closest(".wiz-radio").textContent, /Available in a future version/);
  await panel._calibrationTab._wizard._act("path:B"); await tick();
  assert.equal(code(), "A-02", "path B does not start");
});

test("L-115/L-124: why an option cannot be chosen is right under its title: a blue note for a feature not shipped, the grey caption for a condition to meet", async () => {
  const { tab, wiz, press, pickCover } = await mount({ previews: false });
  tab().querySelector('[data-action="cal-tab-measure"]').click(); await tick();
  await pickCover("cover.living_one");
  await press("choose");
  for (const [value, text, kind] of [["B", "Available in a future version", "cal-note wiz-radio-notice"], ["C", "Calibrate the cover first.", "muted wiz-radio-caption"]]) {
    const label = wiz().querySelector(`input[name="path"][value="${value}"]`).closest(".wiz-radio");
    const note = label.querySelector(".wiz-radio-notice, .wiz-radio-caption");
    assert.deepEqual([note?.className, note?.textContent], [kind, text], value);
    assert.equal(label.querySelector(".is-warning"), null, `${value}: no yellow (it is not a measurement's warning)`);
    const parts = [...label.querySelector(".wiz-radio-text").children].map((child) => child.className.split(" ").pop() || child.tagName.toLowerCase());
    assert.deepEqual(parts.slice(0, 2), ["wiz-radio-label", kind.split(" ").pop()], `${value}: under the title, before what it does (${parts})`);
    assert.equal(label.classList.contains("is-disabled"), true);
  }
  const css = (await import("node:fs")).readFileSync(new URL("../../custom_components/myhome/frontend/panel/myhome-panel.css", import.meta.url), "utf8");
  assert.match(css, /\.wiz-radio\.is-disabled \{ cursor: not-allowed; \}/, "the option itself is not faded as a whole…");
  assert.match(css, /\.wiz-radio\.is-disabled > input, \.wiz-radio\.is-disabled \.wiz-radio-text > :not\(\.wiz-radio-notice, \.wiz-radio-warning\) \{ opacity: \.6; \}/,
    "…only its parts, never the yellow note");
});

test("review P1: the flag is off by default and switched on for development by a query parameter or a local storage key", async () => {
  const { PREVIEW_PATHS, previewPathsEnabled } = await import("../../custom_components/myhome/frontend/panel/panel-calibration-screens.js");
  assert.equal(PREVIEW_PATHS, false);
  const location = globalThis.location, storage = globalThis.localStorage;
  try {
    globalThis.location = { search: "" }; globalThis.localStorage = { getItem: () => null };
    assert.equal(previewPathsEnabled(), false);
    globalThis.location = { search: "?entry_id=one&calibration_preview=1" };
    assert.equal(previewPathsEnabled(), true);
    globalThis.location = { search: "" }; globalThis.localStorage = { getItem: (key) => (key === "myhome-calibration-preview" ? "1" : null) };
    assert.equal(previewPathsEnabled(), true);
  } finally { globalThis.location = location; globalThis.localStorage = storage; }
});

test("A-21d defaults to updating the profile on the cover it was measured on, and A-34 says when a profile is left alone", () => {
  const t = (key) => translations.en[key] || key;
  const view = { mode: "geometry", phase: "review", values: { opening_time: 22, closing_time: 21 }, geometry: {}, travel_cm: 110, owner: true, sequence: 2, check: { gap_cm: 1, passed: true, check_seconds: 10.5 } };
  const cover = (origin) => ({ name: "Kitchen 2", entity_id: "cover.kitchen_two", profile: { name: "tall", provenance: { opening: { origin_entity_id: origin } } }, followers: [], effective: {} });
  const choice = (origin, supports) => buildScreen({ t, stage: "session", path: "A", cover: cover(origin), view, local: { saveProfile: true }, supports })
    .profileBlock.choice.value;
  assert.equal(choice("cover.kitchen_two", () => true), "update", "the source cover updates its own profile by default, once that can be saved");
  assert.equal(choice("cover.kitchen_two", (target) => target !== "profile_update"), "new", "until then (no L5a) the default stays a new profile");
  assert.equal(choice("cover.hall", () => true), "new", "a follower creates a new one by default");
  const saved = (orphan) => buildScreen({ t, stage: "saved", path: "A", cover: { name: "Kitchen 2" }, local: { saved: { target: "profile_new", name: "kitchen_2", orphan } } });
  assert.deepEqual(saved("tall").note, { tone: "info", text: 'The profile "tall" is no longer followed by any cover: you can delete it from the Profiles tab.' });
  assert.equal(saved(null).note, null);
});

test("counts of zero and one use their own forms", () => {
  const t = (key) => translations.it[key] || key;
  const view = { mode: "geometry", phase: "review", values: { opening_time: 22, closing_time: 21 }, geometry: {}, travel_cm: 110, owner: true, sequence: 2, check: { gap_cm: 1, passed: true, check_seconds: 10.5 } };
  const cover = (followers) => ({ name: "Kitchen 2", profile: { name: "alte" }, followers, effective: {} });
  const review = (followers, local = {}) => buildScreen({ t, language: "it", stage: "session", path: "C", scope: "times_only", cover: cover(followers),
    view: { ...view, mode: "guided" }, local: { ackDescent: true, ...local } });
  assert.match(review([]).saveInfo.body, /Il profilo «alte» non cambierà\.\n/);
  assert.match(review(["Sala 3"]).saveInfo.body, /e l’altra tapparella che lo segue resterà com’è\./);
  assert.match(review(["Sala 2", "Sala 3"]).saveInfo.body, /e le altre 2 tapparelle che lo seguono resteranno come sono\./);
  const a21d = (followers) => buildScreen({ t, language: "it", stage: "session", path: "A", cover: cover(followers), view, local: { saveProfile: true },
    followerNames: followers.join(" e ") }).profileBlock;
  assert.equal(a21d([]).choice.options.find((option) => option.value === "update").description, "Nessun’altra tapparella lo segue.");
  assert.equal(a21d([]).name.line, "Questa tapparella seguirà il nuovo profilo. «alte» resterà senza tapparelle: potrai eliminarlo dalla scheda Profili.");
  assert.equal(a21d(["Sala 3"]).name.line, "Questa tapparella seguirà il nuovo profilo. «alte» resterà all'altra tapparella.");
  assert.equal(a21d(["Sala 2", "Sala 3"]).name.line, "Questa tapparella seguirà il nuovo profilo. «alte» resterà alle altre 2 tapparelle.");
  const update = buildScreen({ t, language: "it", stage: "session", path: "A", cover: cover(["Sala 3"]), view, local: { saveProfile: true, saveChoice: "update" } }).profileBlock;
  assert.equal(update.name, null, "updating the profile: no name and no sentence");
  assert.equal(update.switchLabel, "Salva anche come profilo riusabile, o aggiorna quello esistente", "the switch says it can also update");
  assert.equal(buildScreen({ t, language: "it", stage: "session", path: "A", cover: { name: "Kitchen 2", followers: [], effective: {} }, view, local: {} }).profileBlock.switchLabel,
    "Salva anche come profilo riusabile", "without a profile the short label");
  assert.equal(a21d(["Sala 3"]).choice.options.find((option) => option.value === "update").description, "Cambierà anche l’altra tapparella che lo segue: Sala 3.");
  const b01 = (followerNames) => buildScreen({ t, language: "it", stage: "profile", path: "B", cover: cover([]), local: {},
    profiles: [{ id: "p", name: "alte", followers: followerNames.length, followerNames, measuredOn: "Sala 1" }] }).choice.options[0];
  assert.equal(b01([]).label, "alte · misurato su Sala 1");
  assert.deepEqual(b01([]).note, ["Nessuna tapparella lo segue"]);
  assert.deepEqual(b01(["Kitchen Shutter"]).note, ["Seguito da Kitchen Shutter"]);
  assert.deepEqual(b01(["Kitchen Shutter", "Patio Door"]).note, ["Seguito da Kitchen Shutter e Patio Door"]);
  assert.deepEqual(b01(["Bathroom Shutter", "Study Shutter", "Hallway Shutter"]).note, ["Seguito da Bathroom Shutter, Study Shutter e Hallway Shutter"]);
  assert.deepEqual(b01(["Bathroom Shutter", "Study Shutter", "Hallway Shutter", "Kitchen Shutter", "Patio Door"]).note,
    ["Seguito da Bathroom Shutter, Study Shutter, Hallway Shutter e altre 2"]);
  const en = (key) => translations.en[key] || key;
  assert.deepEqual(buildScreen({ t: en, language: "en", stage: "profile", path: "B", cover: cover([]), local: {},
    profiles: [{ id: "p", name: "tall", followerNames: ["X", "Y", "Z", "W"], measuredOn: "Hall" }] }).choice.options[0].note, ["Followed by X, Y, Z and 1 more"]);
});

test("a refusal is forgotten once the session moves to another phase", async () => {
  const { backend, wiz, code, press, startFor } = await mount();
  await startFor("cover.living_one");
  await press("begin"); await move(backend);
  const original = backend.hass.callWS;
  backend.hass.callWS = async (message) => message.action === "endpoint"
    ? (backend.calls.push(message), Promise.reject(Object.assign(new Error("range"), { code: "reading_out_of_range" }))) : original(message);
  await press("endpoint"); await tick(4);
  backend.interrupt("unexpected_movement"); await tick(4);
  assert.equal(code(), "S-01");
  assert.doesNotMatch(wiz().textContent, /\{\w+\}/, "no empty placeholder after the phase changed");
  assert.equal(wiz().querySelector(".wiz-notices").textContent, "");
});

test("the second tap of Take control is held back too, so a double click cannot skip the confirmation", async () => {
  const session = resumed({ phase: "reading", step: "half_open", reading_kind: "half_open" });
  const { backend, tab, wiz } = await mount({ session, owner: false, guardMs: 700 });
  tab().querySelector('[data-action="cal-tab-session"]').click(); await tick(6);
  await new Promise((resolve) => setTimeout(resolve, 720));
  const take = () => wiz().querySelector('[data-wiz="take"]');
  take().click(); await tick();
  assert.equal(take().textContent, "Confirm take control · 10 s");
  assert.equal(take().disabled, true);
  take().click(); await tick();
  assert.equal(backend.subscriptions.filter((message) => message.claim).length, 0, "a double click does not claim");
  await new Promise((resolve) => setTimeout(resolve, 720));
  assert.equal(take().disabled, false);
});

test("the panel's entity states never overwrite the wizard's stepper", async () => {
  const { panel, wiz, startFor } = await mount();
  await startFor("cover.living_one");
  const labels = () => [...wiz().querySelectorAll(".wiz-steps .wiz-step-label")].map((item) => item.textContent);
  const before = labels();
  assert.deepEqual(before, ["Introduction", "Preparation", "Ascent", "Descent", "Tape measure readings", "Check", "Summary", "Fine-tuning (optional)"]);
  // Home Assistant pushes a state update: the panel refreshes every [data-state] element it owns.
  panel.hass = { ...panel.hass, states: { ...panel.hass.states } };
  assert.deepEqual(labels(), before);
  assert.equal(wiz().querySelector("[data-state]"), null, "no element of the wizard uses the panel's state attribute");
});

test("A-01's list names the bus address and its notes in Italian too", async () => {
  const { tab, wiz, pickCover } = await mount({ language: "it" });
  tab().querySelector('[data-action="cal-tab-measure"]').click(); await tick();
  assert.equal(wiz().querySelector(".wiz-pick-row .cal-row-name .muted").textContent.replace(/\u00a0/g, " "), "Kitchen · Indirizzo: 91 · A: 9 · PL: 1");
  await pickCover("cover.kitchen_two");
  assert.equal(wiz().querySelector(".wiz-pick-notes .cal-note").textContent,
    "Questa tapparella è già calibrata (tempi e rullo, 29 set 2026) e segue il profilo «tall_two_leaf». Una nuova calibrazione, se la scegli al passo successivo, sostituirà i valori in uso; per correggerli, scegli il perfezionamento.");
  // Without a profile the clause about it falls.
  const t = (key) => translations.it[key] || key;
  const alone = buildScreen({ t, language: "it", stage: "choose", local: { coverKey: "k", coverPicked: true },
    covers: [{ key: "k", label: "Kitchen 2", status: "calibrated", detail: "times", last: "2026-10-01T09:00:00+00:00" }], cover: { name: "Kitchen 2", profile: null } });
  assert.deepEqual(alone.picker.notes, [{ tone: "warning",
    text: "Questa tapparella è già calibrata (tempi misurati, 1 ott 2026). Una nuova calibrazione, se la scegli al passo successivo, sostituirà i valori in uso; per correggerli, scegli il perfezionamento." }]);
});

test("opened on a given cover, the wizard already has it chosen; from the tab's button it starts empty", async () => {
  const { panel, tab, wiz, code, button } = await mount();
  // From a row: Measure goes straight to the path choice on that cover.
  tab().querySelector('.cal-row[data-entity="cover.living_one"] [data-action="cal-tab-measure-cover"]').click(); await tick();
  assert.equal(code(), "A-02");
  assert.equal(wiz().querySelector(".wiz-cover").textContent, "Living room shutter 1");
  button("exit").click(); await tick();
  // Back on A-01 with a given cover (as after a cover-specific entry), its radio is ticked and the subtitle names it.
  const tabView = panel._calibrationTab;
  tabView._openWizard({ start: "choose", item: tabView._covers().find((item) => item.cover.entity_id === "cover.living_three") }); await tick();
  assert.equal(code(), "A-01");
  assert.equal(wiz().querySelector('.wiz-picker input[name="cover"]:checked').value, JSON.stringify(["one", "cover.living_three"]));
  assert.equal(wiz().querySelector(".wiz-head .muted").textContent, "Living room · Address: 91 · A: 9 · PL: 1");
  assert.equal(button("choose").disabled, false);
});

test("a missed heartbeat is a calm status, and the panel reconnects when the page is visible again", async () => {
  const { backend, panel, wiz, code, press, startFor } = await mount();
  await startFor("cover.living_one");
  await press("begin"); await move(backend); await settle();
  const screenCode = code();
  const gateway = panel._calibrationTab._wizard._gateway;
  const notices = () => wiz().querySelector(".wiz-notices");
  // Safari puts the page aside: the heartbeat fails, as a closed connection would.
  const callWS = backend.hass.callWS;
  backend.hass.callWS = async (message) => {
    if (message.action === "heartbeat") throw Object.assign(new Error("Connection lost"), { code: 3 });
    return callWS(message);
  };
  const bar = () => [...wiz().querySelectorAll(".wiz-actions [data-wiz]")].map((item) => (item.dataset.wiz === "stopped" ? "stop" : item.dataset.wiz));
  const before = bar();
  await gateway._engine._perform("heartbeat"); await tick();
  assert.equal(notices().querySelector(".wiz-notice").firstChild.textContent.trim(), translations.en.wiz_T446);
  assert.ok(notices().querySelector(".notice.info, [data-tone='info'], .info"), "the interruption is told as information");
  // L-119b: «Resume session» is in its notice, in the content; the step's bar is untouched.
  assert.equal(notices().querySelector('.wiz-notice > button.wiz-notice-action[data-wiz="reconnect"]').textContent, translations.en.calResume);
  assert.deepEqual(bar(), before);
  assert.doesNotMatch(notices().textContent, /Connection or command failed/);
  // The page comes back: the panel resumes the session on its own and the status goes away.
  backend.hass.callWS = callWS;
  const resumes = () => backend.subscriptions.filter((message) => message.type === "myhome/cover_calibration/resume").length;
  const resumed = resumes();
  document.dispatchEvent(new window.Event("visibilitychange")); await settle();
  assert.equal(resumes(), resumed + 1);
  assert.equal(notices().textContent, "");
  assert.equal(code(), screenCode);
  // A command that really fails still says so.
  backend.hass.callWS = async (message) => { if (message.type === "myhome/cover_calibration/action" && message.action !== "heartbeat") throw new Error("boom"); return callWS(message); };
  await gateway._engine._perform("stop", {}); await tick();
  assert.match(notices().textContent, /Connection or command failed/);
});

test("A-07 carries its phone drawing beside the wide one; the stylesheet shows one per width", async () => {
  const { illustrationMarkup, PHONE_VERSION, SVG } = await import("../../custom_components/myhome/frontend/panel/panel-calibration-illustration.js");
  assert.deepEqual(PHONE_VERSION, { "lift-sequence": "lift-sequence-mobile" });
  const host = document.createElement("div");
  host.innerHTML = illustrationMarkup("lift-sequence", "Step diagram", (value) => value);
  const drawings = [...host.querySelectorAll("svg")];
  assert.deepEqual(drawings.map((svg) => [svg.dataset.illustration, svg.getAttribute("class")]),
    [["lift-sequence", "wiz-illustration wiz-ill-wide"], ["lift-sequence-mobile", "wiz-illustration wiz-ill-phone"]]);
  assert.equal(drawings[1].getAttribute("viewBox"), "0 0 358 228");
  // The "②" stays live text in the panel's font, its colour themed like the rest.
  const label = drawings[1].querySelector("text");
  assert.equal(label.textContent, "②");
  assert.deepEqual([label.getAttribute("font-family"), label.getAttribute("font-weight"), label.getAttribute("fill")], ["inherit", "600", "var(--wiz-ill-surface,#fff)"]);
  assert.doesNotMatch(SVG["lift-sequence-mobile"], /#(?:009AC7|BDBDBD|2E2E2E)"/i, "no raw paint colour left");
  host.innerHTML = illustrationMarkup("lift-zoom", "Step diagram", (value) => value);
  assert.equal(host.querySelectorAll("svg").length, 1, "other drawings are one SVG");
  const css = (await import("node:fs")).readFileSync(new URL("../../custom_components/myhome/frontend/panel/myhome-panel.css", import.meta.url), "utf8");
  assert.match(css, /@media \(max-width: 719px\) and \(min-height: 500px\), \(max-width: 719px\) and \(orientation: portrait\) \{\s*\.wiz-illustration\.wiz-ill-wide \{ display: none; \}/,
    "the phone's drawing in portrait only; a landscape phone keeps the wide one");
});

test("our texts name the shell's tabs with the shell's own labels, and never call a window a dialog in Italian", async () => {
  const fillIn = (text, values) => text.replace(/\{(\w+)\}/g, (_, key) => values[key]);
  assert.match(fillIn(translations.it.wiz_T409, { minutes: 30, tab: translations.it.calTabTab }), /dalla scheda Calibrazione\./);
  assert.match(fillIn(translations.en.wiz_T409, { minutes: 30, tab: translations.en.calTabTab }), /from the Calibration tab\./);
  assert.match(fillIn(translations.it.wiz_T431, { old: "x", tab: translations.it.profileListTitle }), /dalla scheda Profili\.$/);
  const italian = Object.entries(translations.it).filter(([key]) => key.startsWith("wiz_") || key.startsWith("calTab"));
  assert.deepEqual(italian.filter(([, text]) => /\bdialog|scheda (Calibration|Devices|Profiles)\b/.test(text)).map(([key]) => key), []);
});

test("a cover that follows a profile without a measured travel says the values are not scaled (A-02, C-01)", () => {
  const t = (key) => translations.it[key] || key;
  const cover = (travel_cm) => ({ name: "Kitchen 2", status: "calibrated", detail: "rolls", last: "2026-10-01T09:00:00+00:00", travel_cm, profile: { name: "alte" }, effective: {} });
  assert.equal(buildScreen({ t, language: "it", stage: "path", profiles: [], cover: cover(null) }).subline,
    "Segue il profilo «alte»: i valori sono quelli del profilo, senza adattamento alla corsa, che non è stata misurata.");
  assert.equal(buildScreen({ t, language: "it", stage: "path", profiles: [], cover: cover(110) }).subline, "Segue il profilo «alte»");
  const unscaled = buildScreen({ t, language: "it", stage: "current", path: "C", cover: cover(null) }).body.split("\n\n");
  assert.equal(unscaled[0], "Segue il profilo «alte»: i valori sono quelli del profilo, senza adattamento alla corsa, che non è stata misurata.");
  assert.match(unscaled[1], /^Nel passo successivo/);
  assert.doesNotMatch(unscaled.join(" "), /cm/);
  assert.match(buildScreen({ t, language: "it", stage: "current", path: "C", cover: cover(110) }).body, /^Kitchen 2 segue il profilo «alte», adattato alla sua corsa del telo di 110 cm/);
});

test("how to tell the slats is a closed toggle right after the question (A-03); times only (C-35) says nothing about slats", async () => {
  const { wiz, code, startFor } = await mount();
  await startFor("cover.living_one");
  assert.equal(code(), "A-03");
  const fieldset = wiz().querySelector("fieldset.wiz-radios");
  const help = fieldset.querySelector("legend + details.wiz-help");
  assert.ok(help, "the toggle follows the question");
  assert.equal(help.open, false, "closed by default");
  assert.equal(help.querySelector("summary").textContent, "How to tell if it has slats");
  assert.match(help.textContent, /Slats are the horizontal strips/);
  assert.doesNotMatch(wiz().querySelector(".wiz-body").textContent, /horizontal strips/, "no longer in the body");
  help.open = true;
  const t = (key) => translations.en[key] || key;
  const timesOnly = buildScreen({ t, stage: "before", path: "C", scope: "times_only", cover: { name: "Kitchen 2", travel_cm: 110 } });
  assert.equal(timesOnly.choice, null);
  assert.equal(timesOnly.help, null, "times only: no slats help (M5-5)");
  assert.doesNotMatch(timesOnly.body, /slats|horizontal strips/, "nor any word about slats");
});

test("a lift-off gap under 1 cm repeats the run and the repeat says which value and why (G2-9)", async () => {
  const { backend, code, press, type, startFor, wiz } = await mount({ language: "it" });
  await startFor("cover.living_one");
  await press("begin"); await move(backend); await press("endpoint"); backend.stopped(); await tick();
  await press("ack:lift"); await press("next"); await move(backend); await press("lift"); backend.stopped(); await tick();
  assert.equal(code(), "A-08");
  assert.equal(wiz().querySelector("#wiz-field-hint").textContent,
    "Di solito 1–4 cm. Non è un errore da correggere: con questo numero il ritardo della pressione si annulla e il risultato diventa più preciso.", "L-76: the reassurance in the hint");
  assert.equal(wiz().querySelector(".wiz-body").textContent.trim(), "Fra la pressione e l'arresto passa sempre un istante, e il bordo continua a salire per quell'istante. Misura con il metro quanti centimetri ci sono ora dalla base della finestra, cioè l'appoggio su cui si ferma la tapparella chiusa, al bordo inferiore del telo.", "L-76: the shorter text");
  assert.doesNotMatch(wiz().querySelector(".wiz-body").textContent + wiz().querySelector("#wiz-field-hint").textContent, /—/, "no em dash");
  // 0,5 is sent as it is (no rounding): under the backend's 1 cm the edge still rests.
  await type("0,5");
  assert.equal(backend.calls.filter((message) => message.action === "reading").at(-1).reading_cm, 0.5);
  assert.equal(backend.session.lift_attempts, 2, "the backend repeats the lift-off");
  assert.equal(wiz().querySelector(".wiz-result-notices").textContent,
    "Hai indicato 0,5 cm: sotto 1 cm il bordo conta come ancora appoggiato, quindi la corsa viene ripetuta.");
  // Back at the bottom, the lift-off screen repeats it.
  await move(backend); await press("endpoint"); backend.stopped(); await tick();
  assert.equal(code(), "A-07");
  assert.match(wiz().querySelector(".wiz-result-notices").textContent, /^Hai indicato 0,5 cm/);
});

test("while the backend waits for the actuator's stop the screen says «Stopping…» with the stopwatch at the press (G2-10, L-119)", async () => {
  const { backend, code, press, startFor, wiz } = await mount({ language: "it" });
  await startFor("cover.living_one");
  await press("begin"); await move(backend); await press("endpoint"); backend.stopped(); await tick();
  await press("ack:lift"); await press("next"); await move(backend);
  await press("lift");
  assert.equal(backend.session.phase, "geometry_wait_stop");
  assert.equal(code(), "A-07");
  // L-119: the bar stays ① · ② · Stop, ①/② off; the stop in progress is said above it.
  const bar = wiz().querySelector(".wiz-actions");
  assert.deepEqual([...bar.querySelectorAll("[data-wiz]")].map((item) => [item.dataset.wiz, item.disabled]), [["next", true], ["lift", true], ["stop", false]]);
  assert.ok([...wiz().querySelectorAll(".wiz-notice")].some((item) => item.dataset.tone === "info" && item.textContent === "Arresto in corso…"));
  const watch = wiz().querySelector(".wiz-stopwatch");
  assert.equal(watch.hidden, false);
  assert.match(watch.textContent, /^Cronometro\s*fermo a \d+,\d s$/);
  const shown = watch.textContent;
  await new Promise((resolve) => setTimeout(resolve, 120)); backend.tick?.(1); await tick();
  assert.equal(wiz().querySelector(".wiz-stopwatch").textContent, shown, "frozen");
  backend.stopped(); await tick();
  assert.equal(code(), "A-08");
  // The actuator never confirmed: S-01, said plainly.
  const t = (key) => translations.it[key] || key;
  const screen = buildScreen({ t, language: "it", stage: "session", path: "A", cover: { name: "Kitchen 2" }, local: {},
    view: { mode: "geometry", phase: "interrupted", reason: "not_stopped", step: "lift", values: {}, samples: {}, readings: {}, owner: true, sequence: 9 } });
  assert.deepEqual([screen.code, screen.body], ["S-01", "La tapparella non ha confermato l'arresto: ricomincia la calibrazione."]);
});

test("a heartbeat refused as expired after a reconnection resumes the same session; only a session Home Assistant ended shows S-06 (M3-1)", async () => {
  const { backend, panel, wiz, code, press, startFor } = await mount();
  await startFor("cover.living_one");
  await press("begin"); await move(backend); await settle();
  const before = code();
  const gateway = panel._calibrationTab._wizard._gateway;
  const callWS = backend.hass.callWS;
  // The page came back on a new socket: the old token is refused as "expired", but the session is still there.
  backend.hass.callWS = async (message) => {
    if (message.action === "heartbeat") throw Object.assign(new Error("not owned by this connection"), { code: "calibration_expired" });
    return callWS(message);
  };
  const resumes = () => backend.subscriptions.filter((message) => message.type === "myhome/cover_calibration/resume").length;
  const count = resumes();
  await gateway._engine._perform("heartbeat"); await settle();
  backend.hass.callWS = callWS;
  assert.equal(gateway.status.expired, false);
  assert.notEqual(code(), "S-06", "no terminal screen while Home Assistant still has the session");
  assert.equal(resumes(), count + 2, "one read to check, one to resume the same session");
  assert.equal(backend.session.session_id, gateway.view.session_id);
  await settle();
  assert.equal(code(), before);
  // Home Assistant ended it for real: the check is refused, and only then the wizard says so.
  backend.hass.callWS = async (message) => {
    if (message.action === "heartbeat") throw Object.assign(new Error("expired"), { code: "calibration_expired" });
    return callWS(message);
  };
  backend.session.session_id = "gone";
  await gateway._engine._perform("heartbeat"); await settle();
  assert.equal(gateway.status.expired, true);
  assert.equal(code(), "S-06");
  backend.hass.callWS = callWS;
});

test("coming back to the page asks Home Assistant at once (M3-1)", async () => {
  const { backend, startFor, press } = await mount();
  await startFor("cover.living_one");
  await press("begin"); await move(backend); await settle();
  const beats = () => backend.calls.filter((message) => message.action === "heartbeat").length;
  const count = beats();
  document.dispatchEvent(new window.Event("visibilitychange")); await settle();
  assert.equal(beats(), count + 1);
});

test("«Start over» after an ended session carries nothing of the old measurement (M3-2)", async () => {
  const { backend, panel, wiz, code, press, type, startFor } = await mount({ language: "it" });
  const toLiftReading = async () => {
    await press("begin"); await move(backend); await press("endpoint"); backend.stopped(); await tick();
    await press("ack:lift"); await press("next"); await move(backend); await press("lift"); backend.stopped(); await tick();
  };
  await startFor("cover.living_one");
  await toLiftReading();
  await type("2");
  assert.equal(panel._calibrationTab._wizard._local.lastGap, 2);
  // Home Assistant ends the session.
  const gateway = panel._calibrationTab._wizard._gateway, callWS = backend.hass.callWS;
  backend.hass.callWS = async (message) => { if (message.action === "heartbeat") throw Object.assign(new Error("expired"), { code: "calibration_expired" }); return callWS(message); };
  backend.session.session_id = "gone";
  await gateway._engine._perform("heartbeat"); await settle();
  backend.hass.callWS = callWS;
  assert.equal(code(), "S-06");
  await press("start-over");
  assert.equal(code(), "A-03");
  const local = panel._calibrationTab._wizard._local;
  assert.deepEqual([local.lastGap, local.baseReadings, local.fineReadings, local.review, local.reachedPhase], [null, null, null, null, null]);
  await toLiftReading();
  assert.equal(code(), "A-08");
  assert.equal(wiz().querySelector('[name="reading_cm"]').value, "", "the gap field starts empty");
  assert.equal(wiz().querySelector(".wiz-field").getAttribute("autocomplete"), "off");
  const lift = [...wiz().querySelectorAll(".wiz-steps li li")].find((item) => item.textContent.startsWith("Stacco"));
  assert.match(lift.textContent, /s · —$/, "no gap in the stepper before this session's reading");
});

test("two minutes before the idle expiry a waiting screen warns, counting minutes and seconds down (M3, L-24)", async () => {
  const t = (key) => translations.it[key] || key;
  const now = Date.parse("2026-10-05T13:00:00Z");
  const at = (seconds, extra = {}) => buildScreen({ t, language: "it", stage: "session", path: "A", local: { ackLift: true, ackAscent: true }, now, cover: { name: "Kitchen 2" },
    view: { mode: "geometry", step: "opening", phase: "briefing", values: {}, samples: {}, readings: {}, slats: true, owner: true, sequence: 9, recoverable: true, attached: true,
      idle_expires_at: new Date(now + seconds * 1000).toISOString(), ...extra } });
  const warning = (screen) => screen.notices.find((notice) => /scade fra/.test(notice.text));
  assert.equal(warning(at(300)), undefined, "five minutes left: nothing yet");
  const shown = warning(at(103));
  assert.deepEqual([shown.tone, shown.text], ["warning", "La sessione scade fra 1:43 se non prosegui: la tapparella verrà liberata e le misure andranno perse."]);
  assert.deepEqual(shown.countdown, { expires: now + 103000, template: translations.it.wiz_T541 }, "the wizard counts it down by itself");
  assert.match(warning(at(9)).text, /fra 0:09 /);
  assert.equal(warning(at(-5)), undefined, "past the deadline the backend's own end decides");
  assert.equal(warning(at(60, { phase: "opening" })), undefined, "not while the cover moves");
  // On screen: only the notice's text changes, every second.
  const { backend, wiz, startFor, press } = await mount({ language: "it" });
  await startFor("cover.living_one");
  await press("begin"); await move(backend); await press("endpoint"); backend.stopped(); await tick();
  backend.session.idle_expires_at = new Date(Date.now() + 65000).toISOString(); backend.emit(); await tick();
  const notice = () => wiz().querySelector(".wiz-notice[data-expires]");
  assert.match(notice().textContent, /fra 1:0[45] /);
  const node = notice(), frame = wiz().querySelector(".wiz-title"), before = node.textContent;
  await new Promise((resolve) => setTimeout(resolve, 2100));
  assert.notEqual(notice().textContent, before, "seconds later it has counted down");
  assert.match(notice().textContent, /fra 1:0\d /);
  assert.deepEqual([notice(), wiz().querySelector(".wiz-title")], [node, frame], "the same nodes: nothing redrawn");
});

test("an ended session keeps the step it reached, greyed, in the stepper and the phone's strip (M3-3)", async () => {
  const { backend, panel, wiz, code, press, type, startFor } = await mount({ language: "it" });
  await startFor("cover.living_one");
  await press("begin"); await move(backend); await press("endpoint"); backend.stopped(); await tick();
  await press("ack:lift"); await press("next"); await move(backend); await press("lift"); backend.stopped(); await tick();
  await type("3"); await move(backend); await press("endpoint"); backend.stopped(); await tick();
  assert.equal(wiz().querySelector(".wiz-compact-line").textContent, "Salita · 3 di 8");
  const gateway = panel._calibrationTab._wizard._gateway, callWS = backend.hass.callWS;
  backend.hass.callWS = async (message) => { if (message.action === "heartbeat") throw Object.assign(new Error("expired"), { code: "calibration_expired" }); return callWS(message); };
  backend.session.session_id = "gone";
  await gateway._engine._perform("heartbeat"); await settle();
  backend.hass.callWS = callWS;
  assert.equal(code(), "S-06");
  assert.equal(wiz().querySelector(".wiz-compact-line").textContent, "Interrotta · Salita 3 di 8");
  assert.ok(wiz().querySelector(".wiz-compact").hasAttribute("data-interrupted"));
  const steps = wiz().querySelector(".wiz-steps");
  assert.ok(steps.hasAttribute("data-interrupted"));
  assert.equal(steps.querySelector('li[data-step-state="current"] > .wiz-step-label').textContent, "Salita");
  assert.match(steps.querySelector(".wiz-steps-line").textContent, /^Interrotta · Salita 3 di 8$/);
  // Starting over clears it.
  await press("start-over");
  assert.equal(wiz().querySelector(".wiz-steps").hasAttribute("data-interrupted"), false);
  assert.equal(wiz().querySelector(".wiz-compact-line").textContent, "Preparazione · 2 di 8");
});

test("choosing a cover on A-01 patches the list in place: same content area, same rows (M3-4)", async () => {
  const { tab, wiz, pickCover } = await mount();
  tab().querySelector('[data-action="cal-tab-measure"]').click(); await tick();
  const main = wiz().querySelector(".wiz-main"), rowOf = (id) => wiz().querySelector(`.wiz-pick-row[data-entity="${id}"]`);
  const kept = rowOf("cover.living_three");
  await pickCover("cover.kitchen_two");
  assert.equal(wiz().querySelector(".wiz-main"), main, "the frame is not rebuilt");
  assert.equal(rowOf("cover.living_three"), kept);
  assert.match(wiz().querySelector(".wiz-head .muted").textContent, /Kitchen/, "the header is patched");
  await pickCover("cover.living_three");
  assert.equal(rowOf("cover.living_three"), kept);
  assert.ok(kept.classList.contains("is-selected"));
});

test("A-02 says why the advised option is advised, impersonally, and says nothing when nothing is advised (G3-1)", () => {
  const t = (key) => translations.it[key] || key;
  const tall = { id: "tall", name: "alte", slat: 4.4, referenceTravel: 109 };
  const reason = (cover, profiles) => buildScreen({ t, language: "it", stage: "path", profiles, cover: { name: "Kitchen 2", ...cover } }).body.split("\n\n")[1].replace(/ Nel dubbio, scegli questa opzione\.$/, "");
  assert.equal(reason({ status: "calibrated", detail: "rolls", last: "2026-10-01T09:00:00+00:00" }, [tall]), "«Perfezionamento» è l'opzione consigliata perché questa tapparella è già calibrata.");
  assert.equal(reason({ status: "calibrated", detail: "rolls", last: "2026-10-01T09:00:00+00:00", profile: { name: "alte" } }, [tall]),
    "«Perfezionamento» è l'opzione consigliata perché questa tapparella è già calibrata e segue il profilo «alte».");
  assert.equal(reason({ status: "to_measure" }, [tall]),
    "«Prima tapparella del suo tipo» è l'opzione consigliata perché la tapparella non è mai stata misurata e, senza la lunghezza del telo, non si può dire se un profilo salvato le si adatti.");
  assert.equal(reason({ status: "to_measure" }, []), "«Prima tapparella del suo tipo» è l'opzione consigliata perché la tapparella non è mai stata misurata e non ci sono profili salvati.");
  assert.equal(reason({ status: "to_measure", travel_cm: 110 }, [tall]),
    "«Come una tapparella già misurata» è l'opzione consigliata perché la corsa del telo (110 cm) è vicina a quella del profilo «alte» (109 cm).");
  const none = buildScreen({ t, language: "it", stage: "path", profiles: [{ ...tall, referenceTravel: 198 }], cover: { name: "Kitchen 2", status: "to_measure", travel_cm: 110 } });
  assert.equal(none.body, "Ci sono **tre percorsi di misurazione**.\n\n«Prima tapparella del suo tipo» è l'opzione consigliata perché la corsa del telo (110 cm) non è vicina a quella di nessun profilo salvato. Nel dubbio, scegli questa opzione.",
    "travel known, no similar profile: still one advice (G4-2)");
  assert.deepEqual(none.choice.options.filter((option) => option.badge).map((option) => option.value), ["A"]);
});

test("«The edge is still resting» repeats the lift-off without a value, and the repeat warns by origin (G3-2, G3-3)", async () => {
  const { backend, code, press, startFor, wiz, button } = await mount({ language: "it" });
  await startFor("cover.living_one");
  await press("begin"); await move(backend); await press("endpoint"); backend.stopped(); await tick();
  await press("ack:lift"); await press("next"); await move(backend); await press("lift"); backend.stopped(); await tick();
  assert.equal(code(), "A-08");
  assert.deepEqual([...wiz().querySelectorAll(".wiz-actions .wiz-action")].map((item) => [item.dataset.wiz, item.textContent, item.className.replace("wiz-action ", "")]),
    [["reading", "Fatto, avanti", "primary"], ["resting", "Il bordo è ancora appoggiato", "secondary"]]);
  await press("resting");
  assert.equal(backend.calls.filter((message) => message.action === "reading").at(-1).reading_cm, 0, "the backend's path of a value under 1 cm");
  assert.equal(backend.session.lift_attempts, 2);
  const notice = wiz().querySelector(".wiz-result-notices .wiz-notice");
  assert.equal(notice.dataset.tone, "warning");
  assert.equal(notice.textContent, "Il bordo era ancora appoggiato: la corsa di stacco viene ripetuta.");
});

test("a value the field refuses says why under it, never a silent red (G3-4)", async () => {
  const { backend, code, press, type, startFor, wiz, actions } = await mount({ language: "it" });
  await startFor("cover.living_one");
  await press("begin"); await move(backend); await press("endpoint"); backend.stopped(); await tick();
  await press("ack:lift"); await press("next"); await move(backend); await press("lift"); backend.stopped(); await tick();
  assert.equal(code(), "A-08");
  const error = () => wiz().querySelector("#wiz-field-error");
  assert.equal(error().hidden, true);
  await type("25");
  assert.equal(error().hidden, false);
  assert.equal(error().textContent, "Valore oltre il massimo (20 cm): controlla la lettura.");
  assert.equal(wiz().querySelector('[name="reading_cm"]').getAttribute("aria-invalid"), "true");
  assert.match(wiz().querySelector('[name="reading_cm"]').getAttribute("aria-describedby"), /wiz-field-error/);
  assert.equal(actions().includes("reading"), false, "nothing sent");
  const input = wiz().querySelector('[name="reading_cm"]'), button = () => wiz().querySelector('.wiz-actions [data-wiz="reading"]');
  // Z-2: refused, «Fatto, avanti» stays off (the user must act) until the value is one the field accepts.
  input.value = "due"; input.dispatchEvent(new Event("input", { bubbles: true })); await tick();
  assert.deepEqual([button().disabled, button().classList.contains("is-settling"), error().hidden], [true, false, false]);
  input.value = "2"; input.dispatchEvent(new Event("input", { bubbles: true })); await tick();
  assert.equal(error().hidden, true, "an accepted value clears it");
  assert.equal(button().disabled, false);
  // A word typed from scratch says what is expected.
  await type("due");
  assert.equal(error().textContent, "Scrivi un numero di centimetri, con la virgola o il punto per i decimali.");
  const css = (await import("node:fs")).readFileSync(new URL("../../custom_components/myhome/frontend/panel/myhome-panel.css", import.meta.url), "utf8");
  assert.doesNotMatch(css, /\.wiz-field:has\(input\[aria-invalid="true"\]\) \.muted/, "Z-2: the help line stays grey");
});

test("A-08b advises repeating: «Repeat the run» is the primary, «Ignore and continue» the outlined secondary; its warning is amber on dark themes (G3-5)", async () => {
  const { backend, code, press, type, startFor, wiz } = await mount();
  await startFor("cover.living_one");
  await press("begin"); await move(backend); await press("endpoint"); backend.stopped(); await tick();
  await press("ack:lift"); await press("next"); await move(backend); await press("lift"); backend.stopped(); await tick();
  await type("12");
  assert.equal(code(), "A-08");
  assert.deepEqual([...wiz().querySelectorAll(".wiz-actions .wiz-action")].map((item) => [item.dataset.wiz, item.textContent, item.className.replace("wiz-action ", "")]),
    [["repeat", "Repeat the run", "primary"], ["next", "Ignore and continue", "secondary"]]);
  // Z-4: the reading is in: the screen is about the choice, without the reading's drawing.
  assert.equal(wiz().querySelector(".wiz-title").textContent, "Repeat the lift-off run?");
  assert.match(wiz().querySelector(".wiz-body").textContent, /^The reading is recorded/);
  assert.equal(wiz().querySelector(".wiz-illustration"), null, "no drawing of the reading");
  const css = (await import("node:fs")).readFileSync(new URL("../../custom_components/myhome/frontend/panel/myhome-panel.css", import.meta.url), "utf8");
  assert.match(css, /\[data-theme="dark"\] :is\(\.cal-note\.is-warning, :is\(\.cal-tab, dialog\.cal-dialog\) \.notice:is\(\.cal-tab-busy, \[data-tone="warning"\]\)\) \{ --note-text: color-mix\(in srgb, var\(--panel-warning\) 70%/);
});

test("C-03 asks about the slats with the answer of the last calibration preselected (G4-5)", () => {
  const t = (key) => translations.it[key] || key;
  const before = (slat, local = {}) => buildScreen({ t, language: "it", stage: "before", path: "C", scope: "times_and_rolls", local,
    cover: { name: "Kitchen 2", effective: slat === undefined ? {} : { slat_time_s: { value: slat } } } });
  const c03 = before(0);
  assert.equal(c03.code, "C-03");
  assert.equal(c03.choice.label, "Ha le lamelle?");
  assert.ok(c03.choice.help, "with its help");
  assert.equal(c03.choice.value, "no", "known: without slats");
  assert.equal(before(2.4).choice.value, "yes");
  assert.equal(before(undefined).choice.value, "yes", "unknown: with slats");
  assert.equal(before(0, { slats: true }).choice.value, "yes", "the user's own answer wins");
});

test("no «—» inside a sentence: without the datum the variant that does not name it (M5-6)", () => {
  const t = (key) => translations.it[key] || key;
  const before = (scope, travel_cm) => buildScreen({ t, language: "it", stage: "before", path: "C", scope, cover: { name: "Kitchen 2", travel_cm } }).body;
  assert.match(before("times_only", null), /I due coefficienti di rullo e la corsa del telo resteranno quelli in uso/);
  assert.match(before("times_only", 110), /la corsa del telo, 110 cm, resteranno/);
  assert.match(before("points_only", null), /La corsa del telo non verrà rimisurata\./);
  for (const scope of ["times_only", "points_only"]) assert.doesNotMatch(before(scope, null), /—/);
  const all = Object.entries(translations.it).filter(([key]) => /^wiz_T(230|235|489|490|491)$/.test(key));
  assert.equal(all.length, 5);
});

test("a tap on the Steps sheet's backdrop closes it, as every sheet and dialog (M6-1, R7-7)", async () => {
  const { wiz, startFor } = await mount();
  await startFor("cover.living_one");
  wiz().querySelector('[data-wiz="steps"]').click(); await tick();
  const sheet = wiz().querySelector(".wiz-sheet");
  assert.equal(sheet.hidden, false);
  sheet.querySelector(".wiz-sheet-card").click(); await tick();
  assert.equal(sheet.hidden, false, "a tap inside the card keeps it");
  sheet.click(); await tick(); await new Promise((resolve) => setTimeout(resolve, 260));
  assert.equal(sheet.hidden, true, "a tap on the backdrop closes it");
});

test("the wizard's actions, Take control and PASSI take the shell's 8 px radius (Q-18); the compact order is round (Q-26)", async () => {
  const css = (await import("node:fs")).readFileSync(new URL("../../custom_components/myhome/frontend/panel/myhome-panel.css", import.meta.url), "utf8");
  assert.match(css, /--cal-btn-radius: 8px; \}/);
  for (const rule of [/\.wiz-action \{[^}]*border-radius: var\(--cal-btn-radius\);/, /\.wiz-take \{[^}]*border-radius: var\(--cal-btn-radius\);/]) assert.match(css, rule);
  assert.match(css, /\.cal-filterbar\.is-chips \.cal-filterbar-sort \{[^}]*border-radius: 999px;/, "Q-26: the compact order is drawn as a chip, round");
  assert.equal(css.match(/\.wiz-steps-button \{[^}]*border-radius: var\(--cal-btn-radius\);/g)?.length, 2, "PASSI in both layouts");
});

test("the wizard's layouts follow the content width as the layout module says, buttons 44 px (R-1)", async () => {
  const css = (await import("node:fs")).readFileSync(new URL("../../custom_components/myhome/frontend/panel/myhome-panel.css", import.meta.url), "utf8");
  const { WIZARD_BANDS, wizardBand, SHORT } = await import("../../custom_components/myhome/frontend/panel/panel-calibration-layout.js");
  assert.match(css, new RegExp(`@container caltab \\(min-width: ${WIZARD_BANDS.threeColumns}px\\) \\{\\s*\\.wiz-columns \\{ grid-template-columns: 240px minmax\\(0, 1fr\\) clamp\\(240px, 30%, 320px\\);`));
  assert.match(css, new RegExp(`@container caltab \\(min-width: ${WIZARD_BANDS.intermediate}px\\) and \\(max-width: ${WIZARD_BANDS.threeColumns - 1}px\\) \\{\\s*\\.wiz-columns \\{ grid-template-columns: minmax\\(0, 1fr\\) clamp\\(240px, 30%, 320px\\);`));
  assert.match(css, new RegExp(`@container caltab \\(max-width: ${WIZARD_BANDS.intermediate - 1}px\\) \\{\\s*/\\*[^*]*\\*/\\s*\\.wiz, \\.wiz\\.cal-batch-wiz \\{ overflow: clip; --wiz-single: 1; \\}`));
  assert.match(css, new RegExp(`@container wizbar \\(min-width: ${WIZARD_BANDS.wideBar}px\\)`));
  assert.ok(css.includes(`@media ${SHORT} {`));
  assert.deepEqual([1200, 1000, 999, 700, 699].map((width) => wizardBand(width)), ["three-columns", "three-columns", "intermediate", "intermediate", "single-column"]);
  assert.equal(wizardBand(390, { phone: true }), "phone");
  assert.equal(wizardBand(844, { short: true }), "landscape");
  assert.match(css, /\.wiz-action \{ width: 100%; min-height: 44px;/);
  assert.match(css, /\.wiz-actions \{ display: grid; grid-template-columns: minmax\(0, 1fr\); gap: 12px; align-content: start; padding: 24px;/);
});

test("a 40 px fade of the surface under every fixed or sticky action bar and at the landscape centre's edge (R-2)", async () => {
  const css = (await import("node:fs")).readFileSync(new URL("../../custom_components/myhome/frontend/panel/myhome-panel.css", import.meta.url), "utf8");
  const fades = css.match(/\.wiz-actions::before \{ content: ""; position: absolute; inset-inline: 0; bottom: calc\(100% \+ 1px\); height: 40px;/g) || [];
  assert.equal(fades.length, 2, "the phone's fixed bar and the single column's sticky bar");
  assert.match(css, /\.wiz-main::after \{ content: ""; flex: none; position: sticky;[^}]*height: 40px;/);
  assert.match(css, /\.wiz-columns:has\(> \.wiz-actions\.has-more\) > \.wiz-main::after \{ opacity: 1; \}/);
});

test("«Passi» has its title and ✕: a centred card, a sheet from the bottom, a square side panel in landscape (R-3)", async () => {
  const { wiz, tab } = await mount();
  tab().querySelector('[data-action="cal-tab-measure"]').click(); await tick();
  const head = wiz().querySelector(".wiz-sheet .wiz-sheet-head");
  assert.equal(head.querySelector(".wiz-sheet-title").textContent, "Steps");
  assert.ok(head.querySelector('.wiz-sheet-close[data-wiz="steps-close"]'));
  const css = (await import("node:fs")).readFileSync(new URL("../../custom_components/myhome/frontend/panel/myhome-panel.css", import.meta.url), "utf8");
  assert.match(css, /\.wiz-sheet-card \{[^}]*width: min\(100%, 360px\);/);
  const short = css.slice(css.lastIndexOf("@media (max-height: 499px) and (orientation: landscape) {"));
  assert.match(short, /\.wiz-sheet \.wiz-sheet-card \{ width: calc\(320px \+ var\(--cal-safe-right\)\); height: 100%; max-height: none; border-radius: 0;/);
  assert.match(short, /animation: wiz-panel-in/);
});

test("every wizard, block and dialog button centres its label and takes the shell's radius (R-4)", async () => {
  const css = (await import("node:fs")).readFileSync(new URL("../../custom_components/myhome/frontend/panel/myhome-panel.css", import.meta.url), "utf8");
  assert.match(css, /\.wiz-action, \.wiz-take, \.wiz-dialog-card \.actions button, dialog\.cal-dialog \.dialog-foot button, \.cal-block-action > button \{\s*justify-content: center; text-align: center; border-radius: var\(--cal-btn-radius\); \}/);
});

test("dialogs are 560 px centred cards, and sheets with a ✕ that keeps the calibration and stacked buttons (R-5)", async () => {
  const { wiz, press, startFor } = await mount();
  await startFor("cover.living_one");
  await press("begin");
  const original = globalThis.matchMedia;
  try {
    globalThis.matchMedia = (query) => ({ matches: query.includes("max-width") });
    wiz().querySelector(".wiz-head .wiz-x").click(); await tick();
    const exit = wiz().querySelector(".wiz-dialog:not(.wiz-nav-dialog)");
    assert.equal(exit.querySelector(".wiz-dialog-x").hidden, false, "a sheet has its ✕");
    exit.querySelector(".wiz-dialog-x").click(); await tick();
    await new Promise((resolve) => setTimeout(resolve, 260));
    assert.equal(exit.hidden, true, "✕ keeps the calibration");
  } finally { globalThis.matchMedia = original; }
  const css = (await import("node:fs")).readFileSync(new URL("../../custom_components/myhome/frontend/panel/myhome-panel.css", import.meta.url), "utf8");
  assert.match(css, /\.wiz-dialog-card \{ box-sizing: border-box; width: min\(100%, 560px\); padding: 24px;/);
  assert.doesNotMatch(css, /width: 40px; height: 4px; border-radius: 2px;/, "X-5: no drag handle (there is no drag-to-close)");
  assert.match(css, /dialog\.cal-dialog \{ width: min\(100%, 560px\); margin: auto auto 0; \}/, "landscape: a 560 px sheet, centred");
});

test("a gated primary stays a primary, disabled: A-01's Continue before a choice, the sequence's Start with nothing ticked (Q-29)", async () => {
  const css = (await import("node:fs")).readFileSync(new URL("../../custom_components/myhome/frontend/panel/myhome-panel.css", import.meta.url), "utf8");
  // D-13 (decided 8 Oct): waiting for the user it is neutral; Q-29's light primary is the passing state (`is-settling`).
  const rule = css.match(/:is\(\.wiz-action\.primary, button\.primary\):disabled, :is\(\.wiz-action\.primary, button\.primary\):disabled:hover \{([^}]*)\}/)?.[1] || "";
  assert.match(rule, /background: color-mix\(in srgb, var\(--primary-text-color, #182b37\) 12%, var\(--panel-surface\)\)/);
  assert.match(rule, /color: color-mix\(in srgb, var\(--primary-text-color, #182b37\) 45%, transparent\)/);
  assert.match(rule, /box-shadow: none/);
  const settling = css.match(/:is\(\.wiz-action\.primary, button\.primary\)\.is-settling:disabled, [^{]*\{([^}]*)\}/)?.[1] || "";
  assert.match(settling, /background: color-mix\(in srgb, var\(--panel-accent\) 38%, var\(--panel-surface\)\)/, "Q-29's look, for a moment only");
  assert.match(settling, /color: color-mix\(in srgb, var\(--panel-accent\) 40%, var\(--primary-text-color, #182b37\)\)/, "a label readable on it (not white on light blue)");
  assert.doesNotMatch(css, /\[data-theme="dark"\] :is\(\.wiz-action\.primary, button\.primary\):disabled/, "one rule for both themes");
  const { wiz, tab, button } = await mount({ guardMs: 700 });
  tab().querySelector('[data-action="cal-tab-measure"]').click(); await tick();
  assert.ok(button("choose").classList.contains("primary"));
  assert.equal(button("choose").disabled, true);
  assert.equal(button("choose").classList.contains("is-settling"), false, "nothing chosen yet: off until the user acts");
  const radio = wiz().querySelector('.wiz-picker input[name="cover"]'); radio.checked = true; radio.dispatchEvent(new Event("change", { bubbles: true })); await tick();
  await new Promise((resolve) => setTimeout(resolve, 720)); await tick();
  assert.equal(button("choose").disabled, false);
  button("choose").click(); await tick();
  assert.deepEqual([button("path").disabled, button("path").classList.contains("is-settling")], [true, true], "right after the change: the hold, a passing state");
});

test("in the one-column layout the action bar is docked to the window's bottom (Home Assistant scrolls <html>) (U-1)", async () => {
  const css = (await import("node:fs")).readFileSync(new URL("../../custom_components/myhome/frontend/panel/myhome-panel.css", import.meta.url), "utf8");
  assert.match(css, /\.wiz\.is-docked > \.wiz-frame > \.wiz-columns > \.wiz-actions \{ position: fixed; z-index: 7; inset-inline-start: var\(--dock-left\); bottom: 0;/);
  assert.match(css, /\.wiz\.is-docked > \.wiz-frame > \.wiz-columns \{ padding-bottom: var\(--dock-height\); \}/, "the wizard keeps the bar's room");
  const { dockBar } = await import("../../custom_components/myhome/frontend/panel/panel-calibration-layout.js");
  const { wiz, tab } = await mount();
  tab().querySelector('[data-action="cal-tab-measure"]').click(); await tick();
  const stop = dockBar(wiz());
  assert.equal(wiz().classList.contains("is-docked"), false, "without a layout nothing is docked");
  stop();
});


test("D-02: with columns the sequence's status and Start are held 16 px under the top while an outer box scrolls", async () => {
  const { dockBar } = await import("../../custom_components/myhome/frontend/panel/panel-calibration-layout.js");
  const wiz = document.createElement("div"), column = document.createElement("div"), block = document.createElement("div");
  block.className = "cal-batch-sticky"; column.append(block); wiz.append(column); document.body.append(wiz);
  const rect = (top, height) => ({ top, bottom: top + height, height, left: 0, right: 600, width: 600 });
  let scrolled = 0;
  wiz.getBoundingClientRect = () => rect(100 - scrolled, 2000);
  column.getBoundingClientRect = () => rect(100 - scrolled, 2000);
  // Where it sits in the flow (the transform is cleared before each measure): the column's top + its 24 px padding.
  block.getBoundingClientRect = () => rect(124 - scrolled, 90);
  const original = window.getComputedStyle, frame = window.requestAnimationFrame;
  window.getComputedStyle = (element) => ({ position: element === block ? "sticky" : "static", paddingBottom: "24px", getPropertyValue: () => "" });
  window.requestAnimationFrame = (callback) => setTimeout(callback, 0);
  try {
    const stop = dockBar(wiz);
    assert.equal(block.style.transform, "", "in place while the column's top is in view");
    scrolled = 600; window.dispatchEvent(new window.Event("scroll")); await new Promise((resolve) => setTimeout(resolve, 5));
    assert.equal(block.style.transform, "translateY(492px)", "16 px under the window's top: 16 − (124 − 600)");
    scrolled = 1990; window.dispatchEvent(new window.Event("scroll")); await new Promise((resolve) => setTimeout(resolve, 5));
    assert.equal(block.style.transform, "translateY(1862px)", "never past the column's end: 2100 − 24 − 214 = 1862");
    stop();
    assert.equal(block.style.transform, "");
  } finally { window.getComputedStyle = original; window.requestAnimationFrame = frame; wiz.remove(); }
});

test("D-21: on A-02 the stepper shows the steps of the path ticked so far", async () => {
  const { wiz, code, press, pickCover, tab } = await mount();
  tab().querySelector('[data-action="cal-tab-measure"]').click(); await tick();
  await pickCover("cover.living_one"); await press("choose");
  assert.equal(code(), "A-02");
  const phases = () => wiz().querySelectorAll(".wiz-steps > ol > li").length;
  const tick_ = async (value) => { const radio = wiz().querySelector(`input[name="path"][value="${value}"]`); radio.checked = true; radio.dispatchEvent(new Event("change", { bubbles: true })); await tick(); };
  await tick_("B");
  assert.equal(phases(), 3, "path B: introduction, readings, summary");
  await tick_("A");
  assert.equal(phases(), 8);
  // (Fine-tuning needs a calibrated cover: here it is offered only when enabled.)
  if (!wiz().querySelector('input[name="path"][value="C"]').disabled) {
    await tick_("C");
    assert.equal(phases(), 1, "fine-tuning: its case is chosen later, only the introduction for now");
  }
});

test("D-22: from a 560 px bar every action is centred at 480, a red Stop alone included", async () => {
  const css = (await import("node:fs")).readFileSync(new URL("../../custom_components/myhome/frontend/panel/myhome-panel.css", import.meta.url), "utf8");
  assert.match(css, /@container wizbar \(min-width: 560px\) \{ \.wiz-actions > \*, \.wiz-actions > button\.danger \{ box-sizing: border-box; width: 100%; max-width: 480px; justify-self: center; \} \}/);
  assert.match(css, /button\.danger \{ color: var\(--panel-error\); justify-self: start; \}/, "the shell's rule it outweighs");
});

test("D-23/D-36: a number and its unit are held on one line in bodies, notices, waits and hints, the text unchanged", async () => {
  const { units, inline } = await import("../../custom_components/myhome/frontend/panel/panel-calibration-text.js");
  assert.equal(units("fra 73,6 e 109,1 cm."), 'fra 73,6 e <span class="cal-nb">109,1 cm</span>.');
  assert.equal(units("circa 30 s) · 600 secondi · 12 %"), 'circa <span class="cal-nb">30 s</span>) · <span class="cal-nb">600 secondi</span> · <span class="cal-nb">12 %</span>');
  assert.equal(units("3 scatti, 2 sere"), "3 scatti, 2 sere", "only units");
  assert.match(inline("**21 s** o 20 s"), /<strong><span class="cal-nb">21 s<\/span><\/strong> o <span class="cal-nb">20 s<\/span>/);
  const css = (await import("node:fs")).readFileSync(new URL("../../custom_components/myhome/frontend/panel/myhome-panel.css", import.meta.url), "utf8");
  assert.match(css, /\.cal-nb \{ white-space: nowrap; \}/);
});

test("D-24: the progress track is seen at 0 % on light and dark boxes", async () => {
  const css = (await import("node:fs")).readFileSync(new URL("../../custom_components/myhome/frontend/panel/myhome-panel.css", import.meta.url), "utf8");
  assert.match(css, /\.wiz-progress \{ height: 6px; border-radius: 999px; background: color-mix\(in srgb, var\(--primary-text-color, #182b37\) 14%, transparent\); overflow: hidden; \}/);
});

test("D-26/X-5: the side Steps panel reaches the right edge; no sheet draws a drag handle", async () => {
  const css = (await import("node:fs")).readFileSync(new URL("../../custom_components/myhome/frontend/panel/myhome-panel.css", import.meta.url), "utf8");
  assert.doesNotMatch(css, /dialog\.cal-dialog::before|\.wiz-dialog-card::before|\.wiz-sheet-head::before/);
  assert.match(css, /\.wiz-sheet \.wiz-sheet-card \{ width: calc\(320px \+ var\(--cal-safe-right\)\); height: 100%; max-height: none; border-radius: 0; padding: 0; \}/);
  assert.match(css, /\.wiz-sheet \.wiz-sheet-head \{ padding-top: 4px; padding-inline-end: calc\(8px \+ var\(--cal-safe-right\)\); \}/);
});

test("D-30: in a landscape sheet the buttons sit side by side only while their labels fit on one line", async () => {
  const css = (await import("node:fs")).readFileSync(new URL("../../custom_components/myhome/frontend/panel/myhome-panel.css", import.meta.url), "utf8");
  assert.match(css, /dialog\.cal-dialog \.dialog-foot, \.wiz-dialog \.actions \{ flex-direction: row; flex-wrap: wrap; justify-content: end; \}/);
  assert.match(css, /dialog\.cal-dialog \.dialog-foot button, \.wiz-dialog \.actions button \{ flex: 1 1 auto; width: auto; min-width: 0; white-space: nowrap; \}/);
});

test("D-31: an ended session with a measurement out of range is titled as such, not as two presses", async () => {
  const t = (key) => translations.it[key] || key;
  const view = { mode: "geometry", phase: "interrupted", reason: "invalid_measurement", step: "opening", values: {}, samples: {}, readings: {}, owner: true, sequence: 9 };
  const screen = buildScreen({ t, language: "it", stage: "session", path: "A", view, local: {}, cover: { name: "K" } });
  assert.deepEqual([screen.code, screen.title, screen.body], ["S-04", "La corsa non è valida", "Il tempo misurato deve essere compreso tra 1 e 600 secondi."]);
});

test("D-32: in the summary a cover without slats reads «No» for the slat time, not «0 s»", async () => {
  const t = (key) => translations.it[key] || key;
  const view = { mode: "geometry", phase: "review", step: "check", values: { opening_time: 22, closing_time: 21 }, samples: {}, readings: { half_open: 99, half_close: 98 },
    geometry: { slat_time_s: 0, opening_roll: 1.8, closing_roll: 1.9 }, slats: false, travel_cm: 198, owner: true, sequence: 9, check: { gap_cm: 1, passed: true, check_seconds: 10.5 } };
  const screen = buildScreen({ t, language: "it", stage: "session", path: "A", view, local: {}, cover: { name: "K", effective: {} } });
  const slat = screen.table.rows.find((row) => row.label === t("wiz_T095"));
  assert.deepEqual([slat.before, slat.after], ["—", "No"]);
});

test("D-33: the stepper's times always carry one decimal", async () => {
  const t = (key) => translations.en[key] || key;
  const view = { mode: "geometry", phase: "briefing", step: "opening", values: { opening_time: 22 }, samples: { lift: 5 }, readings: { gap: 3 }, slats: true, owner: true, sequence: 4 };
  const screen = buildScreen({ t, stage: "session", path: "A", view, local: {}, cover: { name: "K" } });
  const ascent = screen.stepper.phases.find((phase) => phase.key === "ascent").substeps.map((item) => item.value);
  assert.deepEqual(ascent.slice(0, 2), ["5.0 s · 3 cm", "22.0 s"]);
});

test("D-37: the filter bars are fitted in the next frame, never inside the resize observer's delivery", async () => {
  const original = globalThis.ResizeObserver, frame = globalThis.requestAnimationFrame;
  let deliver = null; const frames = [];
  globalThis.ResizeObserver = class { constructor(callback) { deliver = callback; } observe() {} disconnect() {} };
  globalThis.requestAnimationFrame = (callback) => { frames.push(callback); return frames.length; };
  try {
    const { fitBars } = await import("../../custom_components/myhome/frontend/panel/panel-calibration-layout.js?d37");
    const root = document.createElement("div"); root.innerHTML = '<div class="cal-filterbar"><div class="cal-filterbar-row"></div></div>';
    document.body.append(root);
    fitBars(root);
    assert.equal(typeof deliver, "function");
    const before = frames.length;
    deliver([{ target: root.firstElementChild }]);
    assert.equal(frames.length, before + 1, "the fit waits for the next frame");
    root.remove();
  } finally { globalThis.ResizeObserver = original; globalThis.requestAnimationFrame = frame; }
});

test("D-43: in one column the fade over the bar shows only while the bar is docked over the content", async () => {
  const css = (await import("node:fs")).readFileSync(new URL("../../custom_components/myhome/frontend/panel/myhome-panel.css", import.meta.url), "utf8");
  assert.match(css, /background: linear-gradient\(to top, var\(--panel-surface\), transparent\); opacity: 0; \}\s*\.wiz\.is-docked > \.wiz-frame > \.wiz-columns > \.wiz-actions::before \{ opacity: 1; \}/);
});

test("D-44 (decided): during a session the ← is not drawn and leaves no space: the title starts at the head's edge", async () => {
  const { backend, wiz, press, startFor } = await mount();
  await startFor("cover.living_one");
  const head = () => wiz().querySelector(".wiz-head");
  assert.ok(head().querySelector('[data-wiz="back"]'), "before the session the way back is there");
  await press("begin"); await move(backend, { seconds: 2 });
  assert.equal(head().querySelector('[data-wiz="back"]'), null);
  assert.equal(head().querySelector(".wiz-back"), null, "no placeholder keeps its room");
});

test("D-13: a button off while the motor starts or a request is pending is a passing state; one waiting for a press is not", () => {
  const t = (key) => translations.en[key] || key;
  const base = { mode: "geometry", values: { opening_time: 22, closing_time: 21 }, samples: { lift: 4 }, readings: { gap: 3 }, slats: true, owner: true, sequence: 4 };
  const at = (view, status = {}) => buildScreen({ t, stage: "session", path: "A", view: { ...base, ...view }, status, local: { ackAscent: true }, cover: { name: "K" } });
  const flags = (screen) => screen.actions.filter((action) => action.disabled).map((action) => [action.id, !!action.settling]);
  assert.deepEqual(flags(at({ step: "home", phase: "starting_close" })), [["endpoint", true]], "the end stop waits for the motor to start");
  assert.deepEqual(flags(at({ step: "opening", phase: "opening" })), [["next", false]], "① waits for ② first: off until the user acts");
  assert.deepEqual(flags(at({ step: "opening", phase: "briefing" }, { locked: true })), [["next", true], ["endpoint", false], ["stop", false]],
    "a request pending: ① passes, ② still waits for ①");
});

test("X-4: notes and notices are plain flow text, never flex or grid: an inline «10 cm» or bold run stays in the sentence", async () => {
  const css = (await import("node:fs")).readFileSync(new URL("../../custom_components/myhome/frontend/panel/myhome-panel.css", import.meta.url), "utf8");
  const offenders = [...css.matchAll(/([^{}]+)\{([^}]*)\}/g)].filter(([, selector, body]) => /display:\s*(inline-)?(flex|grid)/.test(body))
    .flatMap(([, selector]) => selector.split(",").map((part) => part.trim()))
    .filter((part) => /\.(cal-note|wiz-notice|notice)(?![\w-])/.test(part) && !/::before|:has\(> ha-icon\)|^\.notice\.muted$/.test(part));
  assert.deepEqual(offenders, [], "only an [icon][text] notice is laid out side by side");
  // The lift-off warning (a gap of 10 cm or more) as drawn: one paragraph whose «10 cm» is held, not a column.
  const { backend, code, press, type, startFor, wiz } = await mount();
  await startFor("cover.living_one");
  await press("begin"); await move(backend); await press("endpoint"); backend.stopped(); await tick();
  await press("ack:lift"); await press("next"); await move(backend); await press("lift"); backend.stopped(); await tick();
  await type("12");
  assert.equal(code(), "A-08");
  const notice = wiz().querySelector(".wiz-result-notices .wiz-notice");
  assert.ok(notice.classList.contains("notice") && notice.querySelector(".cal-nb"), "the held «10 cm» inside the sentence");
  assert.equal(notice.querySelector("ha-icon"), null, "no icon: so no side-by-side layout applies");
});

test("N-05: A-21's switch label takes the block's width; only the name field keeps 420 px", async () => {
  const css = (await import("node:fs")).readFileSync(new URL("../../custom_components/myhome/frontend/panel/myhome-panel.css", import.meta.url), "utf8");
  assert.match(css, /\.wiz-profile-block label:not\(\.wiz-switch\) \{ max-width: 420px; \}/);
  assert.doesNotMatch(css, /\.wiz-profile-block label \{ max-width/);
});

test("N-06: a reading's «Fatto, avanti» waits for a value: neutral off with the field empty, on once typed", async () => {
  const { backend, code, press, startFor, wiz } = await mount();
  await startFor("cover.living_one");
  await press("begin"); await move(backend); await press("endpoint"); backend.stopped(); await tick();
  await press("ack:lift"); await press("next"); await move(backend); await press("lift"); backend.stopped(); await tick();
  assert.equal(code(), "A-08");
  const button = () => wiz().querySelector('.wiz-actions [data-wiz="reading"]'), field = wiz().querySelector('[name="reading_cm"]');
  assert.deepEqual([button().disabled, button().classList.contains("is-settling")], [true, false], "off until the user types: neutral");
  field.value = "3"; field.dispatchEvent(new Event("input", { bubbles: true }));
  assert.equal(button().disabled, false);
  assert.equal(wiz().querySelector('[name="reading_cm"]'), field, "typing never redraws the field");
});

test("V-1: A-19 asks to confirm the top only on its first leg, and each leg says the time its bar counts", () => {
  const t = (key) => translations.en[key] || key;
  const base = { mode: "geometry", values: { opening_time: 23.3, closing_time: 22.2 }, samples: { lift: 4 }, readings: { gap: 3, half_open: 97 },
    geometry: { slat_time_s: 4.4 }, travel_cm: 198, slats: true, owner: true, sequence: 7 };
  const at = (view, elapsed = 1) => buildScreen({ t, stage: "session", path: "A", view: { ...base, ...view }, elapsed, local: { ackReadings: true }, cover: { name: "K" } });
  const first = at({ step: "top", phase: "opening" });
  assert.equal(first.code, "A-19");
  assert.ok(first.actions.some((action) => action.id === "endpoint"), "the top is confirmed on the first leg");
  // L-120: from where the half-ascent run stopped: 23.3 − (4 + 23.3) / 2 = 9.65 s, rounded up as the time left.
  assert.match(first.wait.stages[0].text, /about 10 s/, "9.65 s rounded up, as the time left");
  assert.equal(first.wait.remaining, "About 9 s remaining", "1 s run of 9.65");
  const second = at({ step: "half_close", phase: "closing" });
  assert.equal(second.code, "A-19");
  assert.equal(second.actions.find((action) => action.id === "endpoint").disabled, true, "never on the way down to half (L-119: off, in its place)");
});

test("D-33: the result tiles show times with one decimal too", () => {
  const t = (key) => translations.en[key] || key;
  const view = { mode: "geometry", phase: "reading", step: "opening", values: { opening_time: 23 }, samples: { lift: 5 }, readings: { gap: 3 }, slats: true, owner: true, sequence: 4 };
  const screen = buildScreen({ t, stage: "session", path: "A", view, local: { travelTyped: 198 }, cover: { name: "K" } });
  assert.deepEqual(screen.results.map((tile) => tile.value), ["5.0", "23.0", "198"]);
});

test("D-36: A-02's notes and warnings hold each number to its unit", async () => {
  const source = (await import("node:fs")).readFileSync(new URL("../../custom_components/myhome/frontend/panel/panel-calibration-wizard.js", import.meta.url), "utf8");
  assert.match(source, /<small class="muted">\$\{text\.units\(esc\(line\)\)\}<\/small>/);
  assert.match(source, /wiz-radio-warning">\$\{text\.units\(esc\(option\.warning\)\)\}/);
});

test("N-12: while the motor runs the step's text says what to watch for, not how to start", () => {
  const t = (key) => translations.en[key] || key;
  const base = { mode: "geometry", values: { opening_time: 22, closing_time: 21 }, samples: { lift: 4 }, readings: { gap: 3 }, travel_cm: 198, owner: true, sequence: 4 };
  const at = (view, local = {}) => buildScreen({ t, stage: "session", path: "A", view: { ...base, slats: true, ...view }, local: { ackLift: true, ackAscent: true, ...local }, elapsed: 2, cover: { name: "K" } });
  assert.match(at({ step: "lift", phase: "briefing" }).body, /^To start the first run/);
  assert.equal(at({ step: "lift", phase: "opening" }).body, t("wiz_T520"));
  assert.equal(at({ step: "opening", phase: "opening" }).body, t("wiz_T521"));
  assert.equal(at({ step: "closing", phase: "closing" }).body, t("wiz_T522"));
  assert.equal(at({ step: "closing", phase: "closing", slats: false }).body, t("wiz_T523"));
});

test("Z-9: in landscape a two-line action grows to fit (54 px) instead of squeezing into 44 px", async () => {
  const css = (await import("node:fs")).readFileSync(new URL("../../custom_components/myhome/frontend/panel/myhome-panel.css", import.meta.url), "utf8");
  assert.match(css, /\.wiz-actions \{ grid-auto-rows: max-content; \}\s*\.wiz-actions > \.wiz-action \{ height: auto; flex: none; \}/);
});

test("Z-7: on a dark theme the passing disabled primary is a faint tint with the neutral disabled label", async () => {
  const css = (await import("node:fs")).readFileSync(new URL("../../custom_components/myhome/frontend/panel/myhome-panel.css", import.meta.url), "utf8");
  const rule = css.match(/\[data-theme="dark"\] :is\(\.wiz-action\.primary, button\.primary\)\.is-settling:disabled, [^{]*\{([^}]*)\}/)?.[1] || "";
  assert.match(rule, /background: color-mix\(in srgb, var\(--panel-accent\) 14%, var\(--panel-surface\)\)/);
  assert.match(rule, /color: color-mix\(in srgb, var\(--primary-text-color, #fff\) 45%, transparent\)/);
});

test("Z-14: each reading shows the drawing nearest its expected height; A-23 its ascent; no slats, the curtains without", () => {
  const t = (key) => translations.en[key] || key;
  const id = (screen) => screen.illustration?.id;
  const live = (view, local = {}) => buildScreen({ t, stage: "session", path: "A", elapsed: 1, local: { ackLift: true, ackAscent: true, ackDescent: true, ackReadings: true, ...local }, cover: { name: "K" },
    view: { mode: "geometry", values: { opening_time: 22, closing_time: 21 }, samples: { lift: 4 }, readings: { gap: 3 }, travel_cm: 200, owner: true, sequence: 4, slats: true, ...view } });
  assert.equal(id(live({ step: "opening", phase: "briefing", slats: false })), "ascent-sequence-no-slats", "A-11 without slats");
  assert.equal(id(live({ step: "closing", phase: "briefing", slats: false })), "descent-sequence-no-slats", "A-14 without slats");
  assert.equal(id(live({ step: "opening", phase: "briefing" })), "ascent-sequence", "with slats as before");
  assert.equal(id(live({ step: "half_open", phase: "reading", reading_range: { min_cm: 90, max_cm: 110 } })), "curtain-tape-half", "A-18: half");
  const preview = (view) => buildScreen({ t, stage: "session", path: "A", elapsed: 1, local: {}, cover: { name: "K" },
    view: { preview: true, path: "refine", phase: "reading", travel_cm: 200, values: {}, readings: {}, stages: [], ...view } });
  assert.equal(id(preview({ step: "quarter_open", expected_cm: 50 })), "curtain-tape-quarter", "A-23: the quarter, no arrow (L-12)");
  assert.equal(id(preview({ step: "quarter_close", expected_cm: 52 })), "curtain-tape-quarter", "a quarter");
  assert.equal(id(preview({ step: "three_quarter_close", expected_cm: 150 })), "curtain-tape-three-quarters", "three quarters");
});

test("L-122: A-02 says how many of the three paths can be used for now; with the previews on, the three-paths wording", () => {
  const t = (key) => translations.it[key] || key;
  const first = (screen) => screen.body.split("\n\n")[0];
  const at = (status, previews) => buildScreen({ t, language: "it", stage: "path", profiles: [{ id: "p", name: "alte", referenceTravel: 198 }],
    cover: { name: "Cucina 2", status, travel_cm: 150 }, previews });
  assert.equal(first(at("to_measure", false)), "Ci sono **tre percorsi di misurazione**: per ora se ne può usare uno.", "never measured: only Ⓐ");
  assert.equal(first(at("measured", false)), "Ci sono **tre percorsi di misurazione**: per ora se ne possono usare due.", "measured: Ⓐ and fine-tuning");
  assert.equal(first(at("to_measure", true)), "Ci sono **tre percorsi di misurazione**.", "PREVIEW_PATHS on: as before");
  assert.equal(first(at("measured", true)), "Ci sono **tre percorsi di misurazione**.");
});

test("L-123: after a real save the stepper line names the last step completed, as every other line names its step", () => {
  const t = (key) => translations.it[key] || key;
  const saved = (path, scope = null) => buildScreen({ t, language: "it", stage: "saved", path, scope, local: { saved: { target: "cover_only" } }, cover: { name: "Cucina 2" } });
  const a = saved("A");
  assert.equal(a.code, "A-34");
  assert.equal(a.stepper.line, "Riepilogo · 7 di 8", "path A: fine-tuning (optional) stays ahead");
  assert.deepEqual(a.stepper.phases.map((phase) => phase.state).slice(-2), ["done", "future"]);
  const c = saved("C", "times_only");
  assert.match(c.stepper.line, /^Riepilogo · (\d+) di \1$/, "a path without fine-tuning: the last of its steps");
  assert.doesNotMatch(`${a.stepper.line} ${c.stepper.line}`, /fuori dai passi/);
});

test("L-126: in the steps only the current label is in the text colour, semibold; done and future ones grey, regular", async () => {
  const css = (await import("node:fs")).readFileSync(new URL("../../custom_components/myhome/frontend/panel/myhome-panel.css", import.meta.url), "utf8");
  // The list's own rule (0,1,2) used to outweigh the state's (0,1,1): the current label was grey as well.
  assert.match(css, /\.wiz-steps ol > li, \.wiz-sheet-steps ol > li \{[^}]*color: var\(--panel-text-2\); \}/, "the list's grey");
  assert.match(css, /:is\(\.wiz-steps, \.wiz-sheet-steps\) ol > li\[data-step-state="current"\] \{ color: var\(--primary-text-color, #182b37\); \}/, "the current label, at the list's weight");
  assert.match(css, /:is\(\.wiz-steps, \.wiz-sheet-steps\) ol > li:is\(\[data-step-state="done"\], \[data-step-state="future"\]\) \{ color: var\(--panel-text-2\); \}/);
  assert.match(css, /li\[data-step-state="current"\] > \.wiz-step-label \{ font-weight: 600; \}/, "semibold");
  assert.match(css, /:is\(\.wiz-steps, \.wiz-sheet-steps\)\[data-interrupted\] ol > li\[data-step-state\] \{ color: var\(--panel-text-2\); \}/, "an interrupted session stays grey");
  assert.ok(css.indexOf(':is(.wiz-steps, .wiz-sheet-steps) ol > li[data-step-state="current"]') > css.indexOf(".wiz-steps ol > li, .wiz-sheet-steps ol > li {"), "after the list's rule");
});

test("L-125: the bar is drawn again when an action moves in or out of view, its id and label unchanged", async () => {
  const { panel, wiz, startFor } = await mount({ previews: false });
  await startFor("cover.living_one");
  const wizard = panel._calibrationTab._wizard;
  const bar = () => [[...wiz().querySelectorAll(".wiz-actions > .wiz-action")].map((item) => item.dataset.wiz),
    [...wiz().querySelectorAll(".wiz-actions .wiz-more-list .wiz-action")].map((item) => item.dataset.wiz)];
  Object.assign(wizard._local, { stage: "saved", saved: { target: "profile_new", name: "living_room_shutter_1" } });
  wizard._render(); await tick();
  assert.deepEqual(bar(), [["close", "assign-profile"], ["another", "open-cover"]]);
  wizard._local.saved.target = "profile_update";
  wizard._render(); await tick();
  assert.deepEqual(bar(), [["close"], ["assign-profile", "another", "open-cover"]]);
});
