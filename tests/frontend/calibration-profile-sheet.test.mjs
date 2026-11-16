/**
 * Giving a profile: «Cambia profilo» / «Assegna», «Scollega il profilo», and the profile to other covers (A-35).
 */
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { after, afterEach, describe, test } from "node:test";

import { cascadeCss, rule } from "./css-cascade.mjs";
import { mountPanel, NAMES, OFFICE } from "./profiles-world.mjs";
import { coverInventory, sampleOverview } from "./fake-calibration.mjs";
import { panelCss as css } from "./panel-css.mjs";
import { dom, mount, move, pageTab, reset, settle, tick } from "./wizard-harness.mjs";

const original = globalThis.matchMedia;
afterEach(() => { globalThis.matchMedia = original; reset(); for (const element of [document.documentElement, document.body]) { element.style.overflow = ""; element.style.overscrollBehaviorY = ""; } });
after(() => dom.window.close());

describe("the sheet", async () => {
  test("«Cambia profilo» is a sheet in the flows' frame: ✕ on the left at every size, the sheet's handle, Assegna primary, the rows of one card everywhere", async () => {
    const { tab, root } = await mount({ language: "it" });
    const row = (id) => [...tab().querySelectorAll(".cal-row")].find((item) => item.dataset.entity === id);
    row("cover.kitchen_two").querySelector('[data-action="cal-tab-menu"]').click(); await tick();
    row("cover.kitchen_two").querySelector('.cal-menu [data-action="cal-tab-assign"]').click(); await tick();
    // The tab's one frame (the flows' and the details'), never a dialog of its own.
    assert.equal(root.querySelector("#dialog-host dialog"), null);
    const frame = tab().querySelector("dialog.cal-flow"), sheet = frame?.querySelector(".cal-flow-body > .cal-assign-page");
    assert.ok(frame?.open && sheet, "a sheet of ours");
    assert.ok(sheet.hasAttribute("data-closable"), "closing it costs nothing");
    assert.ok(frame.querySelector(".cal-flow-box > .cal-flow-handle[aria-hidden='true']"), "the tendina's handle (shown up to 511 px)");
    const x = sheet.querySelector(".cal-page-head > .cal-page-close:first-child");
    assert.equal(x.hidden, false, "its ✕ on the left, on the desktop too");
    assert.deepEqual([...sheet.querySelectorAll(".cal-page-actions button")].map((button) => [button.textContent.trim(), button.classList.contains("primary")]), [["Assegna", true], ["Annulla", false]]);
    assert.ok(sheet.querySelector(".cal-assign-list > legend + .cal-assign-rows"));
    // The frame and the rows' card hold on every window (not only the phone's).
    for (const part of ["dialog.cal-dialog.cal-sheet { box-sizing: border-box; width: min(calc(100% - 48px), 480px);", "dialog.cal-dialog.cal-sheet .cal-assign-rows { display: flex;",
      "dialog.cal-dialog.cal-sheet .cal-assign-rows > .wiz-radio:has(input:checked) { background: var(--cal-tint-selected); }",
      "dialog.cal-dialog.cal-sheet .dialog-head .cal-dialog-x { display: inline-flex; order: -1; margin: 0; }"]) {
      const at = cascadeCss.indexOf(part);
      assert.ok(at > 0, part);
      assert.ok(!/@media[^{]*\{[^}]*$/.test(cascadeCss.slice(Math.max(0, at - 200), at)), `${part}: outside any media`);
    }
    const tendina = cascadeCss.slice(cascadeCss.lastIndexOf("@media (max-width: 511px) {\n  dialog.cal-dialog.cal-sheet"));
    assert.match(tendina, /dialog\.cal-dialog\.cal-sheet \{ width: 100%;[^}]*border-radius: var\(--cal-r-14\) var\(--cal-r-14\) 0 0; --cal-head-band: var\(--cal-handle-band\); \}/);
    assert.match(tendina, /dialog\.cal-dialog\.cal-sheet \.dialog-foot \{ flex-direction: column-reverse; align-items: stretch; \}/, "the phone: a column, the primary first");
    // As the flows', the handle only while closing costs nothing (`data-closable`: no new choice made).
    assert.match(tendina, /dialog\.cal-dialog\.cal-sheet > \.cal-flow-handle \{ display: block;[^}]*visibility: hidden;/, "hidden by default");
    assert.match(tendina, /dialog\.cal-dialog\.cal-sheet\[data-closable\] > \.cal-flow-handle \{ visibility: visible; \}/, "shown while closing costs nothing");
  });
});

describe("the verbs", async () => {
  const row = (tab, id) => [...tab().querySelectorAll(".cal-row")].find((item) => item.dataset.entity === id);
  const { translations } = await import("../../custom_components/myhome/frontend/panel/panel-translations.js");
  const { createFakeBackend, sampleOverview } = await import("./fake-calibration.mjs");

  test("«Cambia profilo» says its verbs: «Scollega il profilo»; the source's limit in impersonal form with its name; the intro of the assignment", async () => {
    const overview = sampleOverview();
    overview.profiles[0].provenance.opening.origin_entity_id = "cover.kitchen_two";
    const { tab, root } = await mount({ language: "it", overview });
    row(tab, "cover.kitchen_two").querySelector('[data-action="cal-tab-menu"]').click(); await tick();
    row(tab, "cover.kitchen_two").querySelector('.cal-menu [data-action="cal-tab-assign"]').click(); await tick();
    void root;
    const dialog = tab().querySelector("dialog.cal-flow .cal-assign-page");
    const none = dialog.querySelector('input[value="__no_profile__"]').closest(".wiz-radio");
    assert.equal(none.querySelector("strong").textContent, "Scollega il profilo", "an action with its verb, never «Nessun profilo»");
    assert.equal(none.querySelector("small").textContent, "Questa tapparella è la sorgente del profilo «tall_two_leaf»: per ora il collegamento non si può togliere. Sarà possibile con una prossima versione; i valori misurati resteranno a lei.");
    assert.equal(dialog.querySelector(".cal-assign-intro").textContent, "Questa tapparella userà i tempi del profilo che scegli. Se il profilo indica una corsa di riferimento e il telo di questa tapparella è più alto o più basso, dopo potrai inserirne la misura nella sua scheda: i tempi si adatteranno.");
    assert.equal(translations.it.wiz_T404, "Non usare un profilo");
    assert.equal(translations.it.calTabProfileNone, "Nessun profilo", "«Nessun profilo» stays a state, in the list and the chips");
  });
});

describe("A-35", async () => {
  const { buildScreen } = await import("../../custom_components/myhome/frontend/panel/panel-calibration-screens.js");
  const { translations } = await import("../../custom_components/myhome/frontend/panel/panel-translations.js");
  const tEn = (key) => translations.en[key] ?? key;
  /** A press on an overlay's scrim: the press starts and ends on the dialog element itself. */
  const scrim = (dialog) => { dialog.dispatchEvent(new dom.window.MouseEvent("pointerdown", { bubbles: true })); dialog.dispatchEvent(new dom.window.MouseEvent("click", { bubbles: true })); };
  /** Six covers on one gateway: the one calibrated, two following «tall_two_leaf» (one its source), three more. */
  function fixture({ source = "cover.kitchen_two", unavailable = "cover.hall_six" } = {}) {
    const inventory = coverInventory([["cover.living_one", "Living room shutter 1", "living"], ["cover.kitchen_two", "Kitchen shutter 2", "kitchen"],
      ["cover.living_three", "Living room shutter 3", "living"], ["cover.kitchen_four", "Kitchen shutter 4", "kitchen"],
      ["cover.living_five", "Living room shutter 5", "living"], ["cover.hall_six", "Hall shutter 6", "living"]]);
    inventory.entities.find((entity) => entity.entity_id === "cover.kitchen_four").address = { raw: "42", a: "4", pl: "2", interface: null };
    const overview = sampleOverview();
    overview.profiles[0].provenance.opening.origin_entity_id = source;
    const template = overview.covers.find((cover) => cover.entity_id === "cover.living_three");
    for (const id of ["cover.kitchen_four", "cover.living_five", "cover.hall_six"]) {
      overview.covers.push({ ...structuredClone(template), entity_id: id, name: id, profile_id: null, ...(id === unavailable ? { assignment_reason: "cover_unavailable" } : {}) });
    }
    return { inventory, overview };
  }
  async function runToSaved({ backend, press, type, startFor, code }) {
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
    await press("save");
    assert.equal(code(), "A-34");
    // `persist`: Home Assistant wrote the new profile (followed by this cover) and announced it; the tab reads it again.
    await settle();
  }
  /** The wizard on A-35 after a real save of a new profile («Living room shutter 1»). */
  async function atA35(options = {}) {
    const context = await mount({ ...fixture(options), persist: true, ...options });
    await runToSaved(context);
    await context.press("assign-profile");
    await settle();
    assert.equal(context.code(), "A-35");
    const live = () => context.wiz().querySelector(".cal-assign-live");
    const rows = () => [...live().querySelectorAll(".cal-assign-row")];
    const row = (id) => rows().find((item) => item.dataset.entity === id);
    const tickRow = async (id, checked = true) => {
      const box = row(id).querySelector('input[name="assign_cover"]');
      box.checked = checked; box.dispatchEvent(new dom.window.Event("change", { bubbles: true })); await tick();
    };
    const search = async (value) => {
      const field = context.wiz().querySelector('[name="assign_filter"]');
      field.value = value; field.dispatchEvent(new dom.window.Event("input", { bubbles: true })); await tick();
    };
    const bar = () => [...context.wiz().querySelectorAll(".wiz-actions .wiz-action")].map((button) => [button.dataset.wiz, button.textContent, button.disabled]);
    const profileId = () => context.backend.overview.profiles.find((profile) => profile.name === "Living room shutter 1")?.id;
    return { ...context, live, rows, row, tickRow, search, bar, profileId };
  }
  test("«Assegna il profilo ad altre tapparelle» advances to A-35 in the same frame: the question with the profile, its line, the steps' last item", async () => {
    // As shipped: the preview paths off, so no fine-tuning step after the summary.
    const { wiz, root, title, tab, live } = await atA35({ previews: false });
    assert.equal(title(), 'Which covers should follow the profile "Living room shutter 1"?');
    assert.equal(root.querySelector("#dialog-host dialog"), null, "never a dialog over the flow");
    assert.equal(tab().querySelectorAll("dialog.cal-flow[open]").length, 1);
    assert.equal(wiz().querySelector(".wiz-body").textContent, tEn("wiz_T628"), "the profile has a reference travel: the times adapt to each travel");
    // The order of the content: the search, the count line, the list, the refusal's slot right under the list.
    const main = wiz().querySelector(".wiz-main .cal-assign");
    assert.deepEqual([...main.children].map((item) => item.className), ["cal-assign-search", "cal-assign-live"]);
    assert.deepEqual([...live().children].map((item) => item.className.split(" ").at(-1)), ["cal-assign-count", "cal-assign-list", "cal-assign-error"]);
    // A-35 is not a step: the steps stay as on A-34 (the summary the last, done); the header names the process
    // and «Other covers», with no count.
    const steps = [...wiz().querySelectorAll(".wiz-steps ol > li")].map((item) => [item.querySelector(".wiz-step-label").textContent, item.dataset.stepState]);
    assert.deepEqual(steps.at(-1), ["Summary", "done"]);
    assert.equal(steps.some(([label]) => label === "Other covers"), false, "no step of its own");
    assert.equal(wiz().querySelector(".wiz-head-phase").textContent, "Calibrate · Other covers");
    assert.equal(wiz().querySelector(".wiz-sheet-steps").textContent.includes("Other covers"), false, "nor in the phone's «Passi» sheet");
    assert.deepEqual([...wiz().querySelectorAll(".wiz-segments > span")].map((item) => item.dataset.stepState).at(-1), "done");
    assert.equal(wiz().dataset.code, "A-35");
  });

  test("the steps end with the summary before, on and after A-35; «Salta» leads back to A-34 and nothing was written", async () => {
    const context = await mount({ ...fixture(), persist: true });
    await runToSaved(context);
    const labels = () => [...context.wiz().querySelectorAll(".wiz-steps ol > li")].map((item) => [item.querySelector(".wiz-step-label").textContent, item.dataset.stepState]);
    const atSummary = labels();
    assert.equal(atSummary.some(([label]) => label === "Other covers"), false, "never a step");
    await context.press("assign-profile"); await settle();
    assert.deepEqual(labels(), atSummary, "A-35: the steps as on A-34");
    const before = context.backend.calls.length;
    await context.press("assign-skip");
    assert.equal(context.code(), "A-34");
    assert.deepEqual(labels(), atSummary, "back on A-34: the same steps");
    assert.equal(context.backend.calls.slice(before).some((message) => message.type === "myhome/cover_profiles/manage"), false);
    assert.equal(context.wiz().querySelector(".wiz-result-notices").textContent, "", "no notice: nothing was assigned");
  });

  test("A-35's rows: friendly name, room · address, the profile it has now; the followers left out; a refused cover listed off, with the reason; a source on", async () => {
    const { rows, row } = await atA35();
    // The cover just calibrated follows the new profile: not listed (nothing to do there).
    assert.deepEqual(rows().map((item) => item.dataset.entity),
      ["cover.hall_six", "cover.kitchen_two", "cover.kitchen_four", "cover.living_three", "cover.living_five"]);
    const text = (id, part) => row(id).querySelector(part).textContent.replace(/ /g, " ");
    assert.equal(text("cover.kitchen_four", "strong"), "Kitchen shutter 4", "the friendly name, never the entity id");
    assert.equal(text("cover.kitchen_four", ".cal-assign-where"), "Kitchen · Address: 42 · A: 4 · PL: 2");
    assert.equal(text("cover.kitchen_four", ".cal-assign-state"), "No profile");
    assert.equal(text("cover.living_three", ".cal-assign-state"), 'Follows the profile "tall_two_leaf"');
    // The source of another profile: listed with its line, and on, as «Cambia profilo» lets it change.
    assert.equal(text("cover.kitchen_two", ".cal-assign-state"), 'Source of the profile "tall_two_leaf"');
    assert.equal(row("cover.kitchen_two").querySelector("input").disabled, false);
    assert.equal(row("cover.kitchen_two").classList.contains("is-disabled"), false);
    // Refused by Home Assistant (unavailable): off, with its reason under its profile line.
    assert.equal(row("cover.hall_six").querySelector("input").disabled, true);
    assert.equal(text("cover.hall_six", ".cal-assign-reason"), tEn("profileError_cover_unavailable"));
    assert.equal(row("cover.living_five").querySelector("input").disabled, false);
    for (const item of rows()) assert.doesNotMatch(item.textContent, /cover\./, "no entity id as a label");
  });

  test("A-35's count line sits between the search and the list; the primary «Assegna a N tapparelle» is off at none, singular and plural right", async () => {
    const { live, tickRow, bar, wiz } = await atA35();
    const count = () => live().querySelector(".cal-assign-count").textContent;
    assert.equal(count(), "0 selected");
    assert.deepEqual(bar(), [["assign-submit", "Assign", true], ["assign-skip", "Skip", false]]);
    await tickRow("cover.living_five");
    assert.equal(count(), "1 selected");
    assert.deepEqual(bar()[0], ["assign-submit", "Assign to 1 cover", false]);
    await tickRow("cover.kitchen_four");
    assert.equal(count(), "2 selected");
    assert.deepEqual(bar()[0], ["assign-submit", "Assign to 2 covers", false]);
    assert.ok(live().querySelector('.cal-assign-row[data-entity="cover.kitchen_four"]').classList.contains("is-selected"), "a chosen row is tinted");
    await tickRow("cover.kitchen_four", false);
    assert.equal(count(), "1 selected");
    // Italian too.
    const it = (key) => translations.it[key];
    assert.deepEqual([it("wiz_T615"), it("wiz_T616"), it("wiz_T617"), it("wiz_T618"), it("wiz_T619")],
      ["{count} selezionate", "1 selezionata", "Assegna a {count} tapparelle", "Assegna a 1 tapparella", "Salta"]);
    assert.equal(it("wiz_T613"), "A quali tapparelle dare il profilo «{profile}»?");
    void wiz;
  });

  test("A-35's search filters by name or address, keeps the field and its focus, and a chosen cover hidden by it stays chosen and counted", async () => {
    const { wiz, root, rows, search, tickRow, live } = await atA35();
    await tickRow("cover.living_five");
    const field = wiz().querySelector('[name="assign_filter"]');
    field.focus();
    await search("kitchen");
    assert.deepEqual(rows().map((item) => item.dataset.entity), ["cover.kitchen_two", "cover.kitchen_four"]);
    assert.equal(wiz().querySelector('[name="assign_filter"]'), field, "the field is never redrawn");
    assert.equal(root.activeElement, field, "typing keeps the focus");
    assert.equal(live().querySelector(".cal-assign-count").textContent, "1 selected", "the hidden choice still counts");
    await search("A: 4 PL: 2");
    assert.deepEqual(rows().map((item) => item.dataset.entity), ["cover.kitchen_four"], "the address, spaced as the list writes it");
    await search("42");
    assert.deepEqual(rows().map((item) => item.dataset.entity), ["cover.kitchen_four"]);
    await search("nothing like this");
    assert.equal(live().querySelector(".wiz-pick-empty").textContent, tEn("noResults"));
    await search("");
    assert.equal(rows().find((item) => item.dataset.entity === "cover.living_five").querySelector("input").checked, true);
  });

  test("A-35's «Assegna a N» sends Home Assistant's multi-cover write (the proposal, then the assignment with its confirmation) and A-34 says to how many", async () => {
    const { backend, press, tickRow, code, wiz, profileId } = await atA35();
    await tickRow("cover.living_five"); await tickRow("cover.kitchen_four");
    const before = backend.calls.length, revision = backend.overview.revision;
    await press("assign-submit"); await settle();
    const sent = backend.calls.slice(before).filter((message) => message.type === "myhome/cover_profiles/manage");
    assert.deepEqual(sent, [
      { type: "myhome/cover_profiles/manage", entry_id: "one", profile_id: profileId(), revision, action: "preview_assign", entity_ids: ["cover.living_five", "cover.kitchen_four"] },
      { type: "myhome/cover_profiles/manage", entry_id: "one", profile_id: profileId(), revision, action: "assign", entity_ids: ["cover.living_five", "cover.kitchen_four"],
        confirmation: `confirm:${profileId()}:${revision}:cover.kitchen_four,cover.living_five` },
    ]);
    assert.equal(code(), "A-34");
    assert.equal(wiz().querySelector(".wiz-result-notices").textContent, "Profile assigned to 2 covers");
    const ids = [...wiz().querySelectorAll(".wiz-actions [data-wiz]")].map((button) => button.dataset.wiz);
    for (const id of ["close", "another", "open-cover"]) assert.ok(ids.includes(id), `${id} as before`);
    // No step of its own, before or after the assignment.
    const steps = [...wiz().querySelectorAll(".wiz-steps ol > li")];
    assert.equal(steps.some((item) => item.querySelector(".wiz-step-label").textContent === "Other covers"), false);
    assert.deepEqual(backend.overview.covers.filter((cover) => cover.profile_id === profileId()).map((cover) => cover.entity_id).sort(),
      ["cover.kitchen_four", "cover.living_five", "cover.living_one"]);
    assert.equal(translations.it.wiz_T622, "Profilo assegnato a {count} tapparelle");
    assert.equal(translations.it.wiz_T623, "Profilo assegnato a 1 tapparella");
  });

  test("on A-35 a refusal is the first thing under the list (the field errors' slot), the selection kept; another change saved meanwhile says the list is up to date", async () => {
    const { backend, press, tickRow, code, live, wiz } = await atA35();
    const original = backend.hass.callWS;
    let refusal = "cover_unavailable";
    backend.hass.callWS = async (message) => {
      if (message.type === "myhome/cover_profiles/manage" && refusal) { backend.calls.push(message); throw Object.assign(new Error("no"), { code: refusal }); }
      return original(message);
    };
    await tickRow("cover.living_five");
    await press("assign-submit"); await settle();
    assert.equal(code(), "A-35");
    const error = live().querySelector(".cal-assign-error");
    assert.equal(error.hidden, false);
    assert.equal(error.textContent, tEn("profileError_cover_unavailable"));
    assert.equal(error.previousElementSibling.classList.contains("cal-assign-list"), true, "right under the list");
    assert.ok(error.classList.contains("cal-note") && error.classList.contains("is-error"));
    assert.equal(error.getAttribute("role"), "alert");
    assert.equal(live().querySelector('input[value="cover.living_five"]').checked, true, "the selection is kept");
    refusal = "revision_conflict";
    await press("assign-submit"); await settle();
    assert.equal(live().querySelector(".cal-assign-error").textContent, "Another change was saved meanwhile: the list is up to date. Check the selection and assign again.");
    // A new choice clears it.
    const box = live().querySelector('input[value="cover.kitchen_four"]');
    box.checked = true; box.dispatchEvent(new dom.window.Event("change", { bubbles: true })); await tick();
    assert.equal(live().querySelector(".cal-assign-error").hidden, true);
    refusal = null;
    await press("assign-submit"); await settle();
    assert.equal(code(), "A-34");
    void wiz;
  });

  test("on A-35 with covers chosen closing costs something: the scrim nudges, the handle hides; the ✕ and Escape close as on A-34", async () => {
    const { tab, wiz, tickRow, root } = await atA35();
    const flow = () => tab().querySelector("dialog.cal-flow");
    assert.equal(wiz().hasAttribute("data-closable"), true, "nothing chosen: closing costs nothing");
    await tickRow("cover.living_five");
    assert.equal(wiz().hasAttribute("data-closable"), false);
    scrim(flow()); await tick();
    assert.ok(flow()?.open, "a press on the scrim does not close it");
    assert.equal(wiz().querySelector(".wiz-head .wiz-x").dataset.wiz, "close", "the calibration is saved: the ✕ asks nothing");
    wiz().querySelector(".wiz-head .wiz-x").click(); await settle();
    assert.equal(tab().querySelector("dialog.cal-flow[open]"), null);
    void root;
  });

  test("following another device A-35 is inert as every step: no actions of the owner, the search and the rows off and out of Tab", async () => {
    const { wiz, panel, tickRow } = await atA35();
    await tickRow("cover.living_five");
    const wizard = panel._calibrationTab._wizard;
    // A reader's view (another device guides it): the same screen, read only.
    wizard._gateway = { view: { read_only: true, phase: "review", preview: false }, status: {}, preview: false, elapsed: () => null, supports: () => false,
      close() {}, lastView: null };
    wizard._render();
    const controls = [...wiz().querySelectorAll(".wiz-main .cal-assign input")];
    assert.ok(controls.length > 2);
    for (const control of controls) { assert.equal(control.disabled, true); assert.equal(control.tabIndex, -1); }
    assert.ok(wiz().hasAttribute("data-following"));
    const ids = [...wiz().querySelectorAll(".wiz-actions [data-wiz]")].map((button) => button.dataset.wiz);
    assert.equal(ids.includes("assign-submit") || ids.includes("assign-skip"), false);
    wizard._gateway = null;
    // Built alone: a reader's A-35 has no owner's action.
    const screen = buildScreen({ t: tEn, stage: "assign", path: "A", local: { assign: { selected: ["x"], name: "p" }, assignEntered: true }, cover: { name: "K" },
      assign: { profile: { name: "p" }, candidates: [], scaling: "" }, view: { read_only: true, phase: "review" } });
    assert.equal(screen.code, "A-35");
    assert.ok(screen.readOnly);
    assert.equal(screen.actions.some((action) => action.id.startsWith("assign")), false);
  });

  test("a reader's A-34 offers no way to A-35; a cover-only save neither; the shared list's styles are one card of rows", () => {
    const saved = (extra) => buildScreen({ t: tEn, stage: "saved", path: "A", local: { saved: { target: "profile_new", name: "p", ...extra } }, cover: { name: "K" } });
    assert.equal(saved({ reader: true }).actions.some((action) => action.id === "assign-profile"), false);
    assert.equal(saved({ target: "cover_only" }).actions.some((action) => action.id === "assign-profile"), false);
    assert.equal(buildScreen({ t: tEn, stage: "saved", path: "A", local: { saved: { target: "profile_new", name: "p" } }, cover: { name: "K" }, assignable: false })
      .actions.some((action) => action.id === "assign-profile"), false, "only where Home Assistant assigns several covers");
    assert.match(rule(cascadeCss, ".cal-assign .cal-batch-rows"), /gap: 0; overflow: hidden; border: 1px solid var\(--panel-border\); border-radius: var\(--cal-r-14\);/);
    assert.match(rule(cascadeCss, ".cal-assign .cal-batch-row + .cal-batch-row"), /border-top: 1px solid var\(--panel-border\)/);
    assert.match(rule(cascadeCss, ".cal-assign .cal-batch-row.is-selected"), /background: var\(--cal-tint-selected\)/);
    assert.match(rule(cascadeCss, ".cal-assign .cal-assign-list"), /display: block;/, "never the list's table grid (container caltab)");
  });

  test("wiz_T405 says the non-source unlinking impersonally, as wiz_T430; the maintainer's assign dialog keeps its texts", () => {
    // What stays and what goes, impersonal.
    assert.match(translations.it.wiz_T405, /^Questa tapparella verrà scollegata dal profilo «\{profile\}»\. Restano gli eventuali tempi/);
    assert.match(translations.en.wiz_T405, /^This cover will be unlinked from profile “\{profile\}”\. Any movement times set on this cover/);
    // The maintainer's assign dialog is his again, with its texts.
    for (const key of ["catalogueAssignHelp", "catalogueSelected", "catalogueAlreadyAssigned", "catalogueNoCovers", "catalogueNoProfile"]) {
      assert.equal(typeof translations.it[key], "string", key);
    }
  });

  test("while the slats question is open on A-35, its Annulla / Assegna are the only pair: the footer's hide", async () => {
    // A-35 in the wizard: the profile just measured has slats; «Living room shutter 5» says it has none.
    const { inventory, overview } = fixture();
    overview.covers.find((cover) => cover.entity_id === "cover.living_five").slats_state = "no";
    const context = await mount({ inventory, overview, persist: true });
    await runToSaved(context);
    // The store as Home Assistant writes it: the profile measured with slats.
    context.backend.overview.profiles.at(-1).geometry = { slat_time_s: 4.4, opening_roll: 1.83, closing_roll: 1.91 };
    context.backend.publishOverview(); await settle();
    await context.press("assign-profile"); await settle();
    assert.equal(context.code(), "A-35");
    const box = context.wiz().querySelector('.cal-assign-row[data-entity="cover.living_five"] input');
    box.checked = true; box.dispatchEvent(new dom.window.Event("change", { bubbles: true })); await tick();
    const bar = () => [...context.wiz().querySelectorAll(".wiz-actions .wiz-action")].map((button) => button.dataset.wiz);
    assert.deepEqual(bar(), ["assign-submit", "assign-skip"]);
    await context.press("assign-submit"); await settle();
    const ask = () => context.wiz().querySelector(".cal-assign-confirm");
    assert.ok(ask(), "the question under the list");
    assert.deepEqual(bar(), [], "no second pair in the footer");
    assert.deepEqual([...ask().querySelectorAll("button")].map((button) => button.dataset.wiz), ["assign-confirm-cancel", "assign-confirm"]);
    ask().querySelector('[data-wiz="assign-confirm-cancel"]').click(); await settle();
    assert.deepEqual([ask(), bar()], [null, ["assign-submit", "assign-skip"]], "the footer back once it is answered");
  });
});

describe("A-35 is not a step", async () => {
  const { translations } = await import("../../custom_components/myhome/frontend/panel/panel-translations.js");

  test("A-35 is not a step: the steps end with the summary, the header says «Calibra · Altre tapparelle» with no count (IT and EN)", async () => {
    assert.equal(translations.it.wiz_T614, "Altre tapparelle");
    assert.equal(translations.en.wiz_T614, "Other covers");
    const { buildScreen } = await import("../../custom_components/myhome/frontend/panel/panel-calibration-screens.js");
    const t = (name) => translations.it[name] ?? name;
    const ctx = { t, language: "it", stage: "assign", path: "A", previews: false, local: { assign: { selected: [], name: "p" }, saved: { target: "profile_new", name: "p" } },
      assign: { profile: { name: "p" }, candidates: [], scaling: "", max: 200 }, cover: { name: "K", status: "to_measure" } };
    const screen = buildScreen(ctx);
    assert.equal(screen.code, "A-35");
    assert.equal(screen.stepper.barLine, "Altre tapparelle", "no «8 di 8»");
    assert.equal(screen.stepper.process, "Calibra");
    assert.equal(screen.stepper.phases.at(-1).key, "summary", "the summary is the last step");
    assert.ok(screen.stepper.phases.every((phase) => phase.key !== "assign"));
    assert.ok(screen.stepper.phases.every((phase) => phase.state === "done"), "as on A-34");
    assert.deepEqual(screen.stepper.segments, screen.stepper.phases.map((phase) => phase.state));
    const saved = buildScreen({ ...ctx, stage: "saved", local: { ...ctx.local, assigned: 2 } });
    assert.equal(saved.code, "A-34");
    assert.deepEqual(saved.stepper.phases.map((phase) => [phase.key, phase.state]), screen.stepper.phases.map((phase) => [phase.key, phase.state]), "A-34 and A-35: the same steps");
  });
});

describe("the screens of «Cambia profilo»", async () => {
  const { translations } = await import("../../custom_components/myhome/frontend/panel/panel-translations.js");
  const en = translations.en, it = translations.it;
  const measured = { source: "guided", recorded_at: "2026-10-30T10:00:00+00:00", origin_entity_id: null, origin_name: null, inherited: false };
  const timing = (value, origin = "profile") => ({ value, origin, provenance: measured, scaled: false });
  /** Three covers: «tall» measured on Living room shutter 1 (its source), followed by the other two. */
  function sourceOverview({ reference = 198, followerTravel = [110, null] } = {}) {
    const data = sampleOverview();
    const profile = data.profiles[0];
    profile.reference_travel_cm = reference;
    profile.assigned_to = ["cover.living_one", "cover.kitchen_two", "cover.living_three"];
    const [one, two, three] = data.covers;
    Object.assign(one, { profile_id: "tall", travel_cm: reference, precision: "basic", effective: { opening: timing(22.4), closing: timing(21.9) } });
    if (reference == null) delete one.travel_cm;
    for (const [cover, travel] of [[two, followerTravel[0]], [three, followerTravel[1]]]) {
      if (travel == null) delete cover.travel_cm; else cover.travel_cm = travel;
    }
    return data;
  }
  const row = (tab, entity) => tab().querySelector(`.cal-row[data-entity="${entity}"]`);
  async function details(context, entity, view = "overview") {
    row(context.tab, entity).querySelector('[data-action="cal-tab-toggle"]').click();
    await tick(6);
    // The details open on «Panoramica» (the travel, the slats, the profile); the table and its tail are on «Dettagli».
    if (view !== "overview") await pageTab(context.tab, view);
    return context.tab().querySelector(".cal-flow .cal-page");
  }

  test("«Cambia profilo» marks «Profilo attuale» and keeps its notes as one block; «Assegna un profilo» is the same screen without «Scollega il profilo»", async () => {
    const context = await mount({ language: "it" });
    const { tab } = context;
    const page = await details(context, "cover.kitchen_two");
    page.querySelector('.cal-page-link [data-action="cal-tab-assign"]').click(); await tick(6);
    const sheet = () => tab().querySelector(".cal-flow .cal-assign-page");
    const shape = () => ({ rows: sheet().querySelectorAll(".cal-assign-rows > .wiz-radio").length, none: !!sheet().querySelector(".cal-assign-none"),
      notes: [...sheet().querySelectorAll(".cal-assign-rows > .wiz-radio:not(.cal-assign-none) .cal-assign-notes")].map((block) => block.children.length),
      actions: [...sheet().querySelectorAll(".cal-assign-actions button")].map((button) => button.textContent.trim()) });
    const current = [...sheet().querySelectorAll(".cal-assign-rows > .wiz-radio")].find((label) => label.querySelector(".cal-current-chip"));
    assert.equal(current.querySelector(".cal-assign-name strong").textContent, "tall_two_leaf");
    assert.equal(current.querySelector(".cal-current-chip").textContent, "Profilo attuale");
    assert.deepEqual(shape(), { rows: 2, none: true, notes: [3], actions: ["Assegna", "Annulla"] });
    sheet().querySelector('[data-action="cal-tab-assign-close"]').click(); await tick(8);
    // From the list, on a cover without a profile: the same frame and rows, no «Scollega», no current one.
    row(tab, "cover.living_one").querySelector('.cal-menu [data-action="cal-tab-assign"]').click(); await tick(6);
    assert.equal(sheet().closest(".cal-flow-box").dataset.kind, "page", "the details' frame (880 px from 1024 px)");
    assert.equal(sheet().querySelector("h2").textContent, "Assegna un profilo alla tapparella Living room shutter 1");
    assert.deepEqual(shape(), { rows: 1, none: false, notes: [3], actions: ["Assegna", "Annulla"] });
    assert.equal(sheet().querySelector(".cal-current-chip"), null);
    assert.equal(en.calTabCurrentProfile, "Current profile");
    const css = (await import("node:fs")).readFileSync(new URL("../../custom_components/myhome/frontend/panel/panel-calibration.css", import.meta.url), "utf8");
    assert.match(css, /\.cal-assign-notes \{ display: block; margin-top: 7px; \}/);
    assert.match(css, /\.cal-assign-page \.cal-assign-list \{ box-sizing: border-box; width: 100%; min-inline-size: 0; align-self: stretch; \}/, "25: the list across the frame");
    assert.match(css, /\.cal-assign-notes small \{ display: block; margin: 0; font-size: var\(--cal-fs-12\); line-height: var\(--cal-lh-12\); \}/);
  });

  test("with the cover's slats known, «Cambia profilo» lists the fitting profiles first and the others under «Altri profili (N)»; A-35 the same for covers; nothing left out; unknown: one list", async () => {
    const data = sourceOverview();
    data.profiles.push({ id: "flat", name: "flat_door", opening_time: 20, closing_time: 20, slats: false, assigned_to: [],
      provenance: { opening: measured, closing: measured } }, { id: "old", name: "old_times", opening_time: 18, closing_time: 18, assigned_to: [],
      provenance: { opening: measured, closing: measured } });
    data.covers[2].slats_state = "no";
    const context = await mount({ overview: data, language: "it" });
    const { tab } = context;
    row(tab, "cover.living_three").querySelector('.cal-menu [data-action="cal-tab-assign"]').click(); await tick(6);
    const sheet = tab().querySelector(".cal-flow .cal-assign-page");
    const names = (root) => [...root.querySelectorAll(":scope > .wiz-radio:not(.cal-assign-none) .cal-assign-name strong")].map((item) => item.textContent);
    assert.deepEqual(names(sheet.querySelector(".cal-assign-list > .cal-assign-rows")), ["flat_door", "old_times"], "without slats: the profile without, then the one that says nothing");
    const more = sheet.querySelector("details.cal-assign-more");
    assert.equal(more.querySelector("summary").textContent.trim(), "Altri profili (1)");
    assert.equal(more.open, true, "the profile followed now is there: open");
    assert.deepEqual(names(more.querySelector(".cal-assign-rows")), ["tall_two_leaf"]);
    const notes = (name) => [...sheet.querySelectorAll(".wiz-radio")].find((label) => label.querySelector("strong").textContent === name).querySelector(".cal-assign-notes small").textContent;
    assert.deepEqual([notes("flat_door"), notes("tall_two_leaf").split(" · ")[0]], ["Senza lamelle", "Con lamelle"]);
    assert.match(notes("old_times"), /^Nessuna tapparella|^Seguito/, "not said: no slats note");
    sheet.querySelector('[data-action="cal-tab-assign-close"]').click(); await tick(8);
    // Unknown: one list.
    row(tab, "cover.kitchen_two").querySelector('.cal-menu [data-action="cal-tab-assign"]').click(); await tick(6);
    assert.equal(tab().querySelector(".cal-flow .cal-assign-page details.cal-assign-more"), null);
    const { liveMarkup } = await import("../../custom_components/myhome/frontend/panel/panel-profile-assign.js");
    const host = document.createElement("div");
    host.innerHTML = liveMarkup({ t: (key) => it[key] ?? key, profile: { slats: true }, candidates: [
      { entity_id: "a", name: "A", search: "a", slats: false, state: { key: "none" } }, { entity_id: "b", name: "B", search: "b", slats: null, state: { key: "none" } },
      { entity_id: "c", name: "C", search: "c", slats: true, state: { key: "none" } }] });
    assert.deepEqual([...host.querySelectorAll(":scope > .cal-assign-list .cal-assign-row")].map((item) => item.dataset.entity), ["b", "c"]);
    assert.equal(host.querySelector("details.cal-assign-more summary").textContent.trim(), "Altre tapparelle (1)");
    assert.deepEqual([...host.querySelectorAll(".cal-assign-slats")].map((item) => item.textContent), ["Con lamelle", "Senza lamelle"]);
  });
});

describe("the slats asked before giving a profile", async () => {
  const { translations } = await import("../../custom_components/myhome/frontend/panel/panel-translations.js");
  const i18n = translations;
  /** «tall» measured on Living room shutter 1 (198 cm, its source), followed by the other two (110 cm / none of their own). */
  function sourceData({ followerTravel = [110, null], reference = 198 } = {}) {
    const data = sampleOverview();
    const profile = data.profiles[0];
    profile.reference_travel_cm = reference;
    profile.assigned_to = ["cover.living_one", "cover.kitchen_two", "cover.living_three"];
    const [one, two, three] = data.covers;
    Object.assign(one, { profile_id: "tall", travel_cm: reference, travel_edit: "reference" });
    for (const [cover, travel] of [[two, followerTravel[0]], [three, followerTravel[1]]]) {
      Object.assign(cover, { profile_id: "tall", travel_edit: reference ? "scaled" : "display" });
      if (travel == null) delete cover.travel_cm; else cover.travel_cm = travel;
    }
    profile.provenance.opening.origin_entity_id = "cover.living_one";
    return data;
  }
  async function page(context, id) {
    const target = [...context.tab().querySelectorAll("[data-action=cal-tab-toggle]")].find((button) => button.dataset.id.includes(id));
    target.click(); await tick(6);
    // The travel, the slats and the profile are on «Panoramica», the tab the card opens on.
    return context.tab().querySelector(".cal-flow .cal-page");
  }

  test("«No» (the source) and «No · dal profilo» read only; giving a profile whose slats contradict the cover's own is asked first, Annulla / Assegna", async () => {
    const data = sourceData();
    data.covers[0].slats_state = "no_measured";
    data.covers[1].slats_state = "no_profile";
    // Living room shutter 3: no profile, declared «no slats»; the profile «tall» has slats (4.4 s).
    Object.assign(data.covers[2], { profile_id: null, slats_state: "no" });
    data.profiles[0].assigned_to = ["cover.living_one", "cover.kitchen_two"];
    const context = await mount({ overview: data, language: "it" });
    const slatsRow = (root) => root.querySelector(".cal-pairs-slats");
    let shown = await page(context, "cover.living_one");
    assert.deepEqual([slatsRow(shown).querySelector("dd").textContent.trim(), slatsRow(shown).querySelector("button")], ["No", null], "no label on the source's own values");
    context.tab().querySelector(".cal-page-close").click(); await tick(6);
    shown = await page(context, "cover.kitchen_two");
    assert.deepEqual([slatsRow(shown).querySelector("dd").textContent.trim(), slatsRow(shown).querySelector("button")], ["No · dal profilo", null]);
    context.tab().querySelector(".cal-page-close").click(); await tick(6);
    shown = await page(context, "cover.living_three");
    assert.equal(slatsRow(shown).querySelector("dd").textContent.trim(), "No");
    assert.ok(slatsRow(shown).querySelector('[data-action="cal-tab-slats-edit"]'), "no profile: its card says it");
    shown.querySelector('.cal-page-link [data-action="cal-tab-assign"]').click(); await tick(6);
    const sheet = context.tab().querySelector(".cal-assign-page");
    // Every profile disagrees with the cover's own «no»: all under «Altri profili», and no empty box above them.
    assert.deepEqual([sheet.querySelectorAll(".cal-assign-list > .cal-assign-rows").length, !!sheet.querySelector(".cal-assign-more")], [0, true]);
    const pick = sheet.querySelector('input[name="assign_profile"][value="tall"]');
    pick.checked = true; pick.dispatchEvent(new Event("change", { bubbles: true })); await tick();
    sheet.querySelector("[data-assign]").click(); await tick(4);
    const ask = sheet.querySelector(".cal-assign-confirm");
    assert.equal(ask.hidden, false);
    assert.equal(ask.querySelector("p").textContent, "Questa tapparella non ha le lamelle, il profilo sì: vicino alla chiusura le posizioni possono essere sbagliate. Assegna comunque?");
    assert.deepEqual([...ask.querySelectorAll("button")].map((button) => button.textContent), ["Annulla", "Assegna"]);
    const writes_ = () => context.backend.calls.filter((message) => message.type === "myhome/cover_profiles/write" && message.action === "assign");
    assert.equal(writes_().length, 0, "nothing written before the answer");
    assert.equal(sheet.querySelector("[data-assign]").disabled, true);
    ask.querySelector('[data-assign-ask="cancel"]').click(); await tick();
    assert.deepEqual([ask.hidden, writes_().length, sheet.querySelector("[data-assign]").disabled], [true, 0, false]);
    sheet.querySelector("[data-assign]").click(); await tick(4);
    sheet.querySelector('.cal-assign-confirm [data-assign-ask="assign"]').click(); await tick(8);
    assert.deepEqual(writes_().map(({ profile_id, entity_id }) => [profile_id, entity_id]), [["tall", "cover.living_three"]]);
  });

  test("A-35 asks the same before giving the profile to covers that say otherwise: one named, several counted", async () => {
    const { selectionConflict } = await import("../../custom_components/myhome/frontend/panel/panel-profile-assign.js");
    const t = (key) => i18n.it[key];
    const profile = { geometry: { slat_time_s: 0, opening_roll: 1, closing_roll: 1 } };
    const candidates = [{ entity_id: "a", name: "Door", slats_state: "yes" }, { entity_id: "b", name: "Hall", slats_state: "yes_measured" },
      { entity_id: "c", name: "Bay", slats_state: "yes_profile" }, { entity_id: "d", name: "Attic", slats_state: "unknown" }];
    assert.equal(selectionConflict(t, candidates, ["a", "c", "d"], profile), "La tapparella Door ha le lamelle, il profilo no: vicino alla chiusura le posizioni possono essere meno precise. Assegna comunque?");
    assert.equal(selectionConflict(t, candidates, ["a", "b"], profile), "2 tapparelle hanno le lamelle, il profilo no: vicino alla chiusura le posizioni possono essere meno precise. Assegna comunque?");
    assert.equal(selectionConflict(t, candidates, ["c", "d"], profile), "", "inherited or not said: nothing to ask");
    assert.equal(selectionConflict(t, candidates, ["a"], { name: "old" }), "", "a profile that says nothing");
  });

  test("«Cambia profilo»'s question about slats is the only Annulla / Assegna pair while open: the footer hides, then comes back", async () => {
    const data = sourceData();
    Object.assign(data.covers[2], { profile_id: null, slats_state: "no" });
    data.profiles[0].assigned_to = ["cover.living_one", "cover.kitchen_two"];
    const context = await mount({ overview: data, language: "it" });
    const shown = await page(context, "cover.living_three");
    shown.querySelector('.cal-page-link [data-action="cal-tab-assign"]').click(); await tick(6);
    const sheet = context.tab().querySelector(".cal-assign-page");
    const pick = sheet.querySelector('input[name="assign_profile"][value="tall"]');
    pick.checked = true; pick.dispatchEvent(new Event("change", { bubbles: true })); await tick();
    const foot = sheet.querySelector(".cal-assign-actions");
    assert.equal(foot.hidden, false);
    sheet.querySelector("[data-assign]").click(); await tick(4);
    const ask = sheet.querySelector(".cal-assign-confirm");
    assert.deepEqual([ask.hidden, foot.hidden], [false, true], "one pair: the question's");
    assert.equal(sheet.getRootNode().activeElement, ask.querySelector('[data-assign-ask="cancel"]'));
    ask.querySelector('[data-assign-ask="cancel"]').click(); await tick();
    assert.deepEqual([ask.hidden, foot.hidden], [true, false]);
    assert.equal(sheet.getRootNode().activeElement, sheet.querySelector("[data-assign]"));
    // A new choice drops the question and brings the footer back too.
    sheet.querySelector("[data-assign]").click(); await tick(4);
    assert.equal(foot.hidden, true);
    const none = sheet.querySelector('input[name="assign_profile"]:not([value="tall"])');
    if (none) { none.checked = true; none.dispatchEvent(new Event("change", { bubbles: true })); await tick(); assert.deepEqual([ask.hidden, foot.hidden], [true, false]); }
    else { ask.querySelector('[data-assign-ask="assign"]').click(); await tick(8); }
  });
});

describe("what the screens say", async () => {
  const { translations } = await import("../../custom_components/myhome/frontend/panel/panel-translations.js");
  const { dateTimeText } = await import("../../custom_components/myhome/frontend/panel/panel-calibration-text.js");
  const it = translations.it, en = translations.en;
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
  const row = (tab, entity) => tab().querySelector(`.cal-row[data-entity="${entity}"]`);
  /** A cover's card on «Panoramica» (the times, the profile) or «Dettagli» (the table, who else uses the profile). */
  async function details(context, entity, view = "overview") {
    row(context.tab, entity).querySelector('[data-action="cal-tab-toggle"]').click();
    await tick(6);
    if (view !== "overview") await pageTab(context.tab, view);
    return context.tab().querySelector(".cal-flow .cal-page");
  }

  test("«Cambia profilo»'s profiles say the same line, the cover in evidence, «questa tapparella» on the profile measured on the cover", async () => {
    const context = await mount({ overview: overview(), language: "it" });
    const when = dateTimeText(MEASURED_AT, "it");
    const page = await details(context, "cover.living_one");
    page.querySelector('.cal-page-link [data-action="cal-tab-assign"]').click(); await tick(6);
    const line = context.tab().querySelector(".cal-assign-page .cal-assign-measured");
    assert.equal(line.textContent, `Misurato su questa tapparella · ${when}`);
    assert.equal(line.querySelector("strong").textContent, "questa tapparella");
    context.tab().querySelector('[data-action="cal-tab-assign-close"]').click(); await tick(6);
    await details(context, "cover.kitchen_two");
    context.tab().querySelector('.cal-page-link [data-action="cal-tab-assign"]').click(); await tick(6);
    const other = context.tab().querySelector(".cal-assign-page .cal-assign-measured");
    assert.equal(other.textContent, `Misurato sulla tapparella Living room shutter 1 · ${when}`);
    assert.equal(other.querySelector("strong").textContent, "Living room shutter 1");
  });

  test("«Scollega il profilo» on a follower says what stays (personal times, travel, slats said) and what goes (the check, the profile's values), no numbers; the source's line is unchanged", async () => {
    const context = await mount({ overview: overview(), language: "it" });
    await details(context, "cover.kitchen_two");
    context.tab().querySelector('.cal-page-link [data-action="cal-tab-assign"]').click(); await tick(6);
    const none = context.tab().querySelector(".cal-assign-none small").textContent;
    // wiz_T405 as worded; in English it says «cover».
    assert.equal(none, "Questa tapparella verrà scollegata dal profilo «tall_two_leaf». Restano gli eventuali tempi impostati su questa tapparella, la corsa del telo e l’indicazione della presenza delle lamelle. "
      + "Non saranno più usati i tempi, il tempo delle lamelle e i coefficienti forniti dal profilo. La verifica del posizionamento verrà rimossa. Se non ci sono tempi impostati su questa tapparella, lo stato tornerà a «Da calibrare». "
      + "Il profilo rimane disponibile e le altre tapparelle che lo usano non cambiano.");
    for (const language of ["it", "en"]) assert.doesNotMatch(translations[language].wiz_T405, /\d/, "no numbers");
    assert.equal(en.wiz_T405, "This cover will be unlinked from profile “{profile}”. Any movement times set on this cover, its travel and its slat-type setting will be kept. "
      + "Times, slat-phase time and coefficients supplied by the profile will no longer be used. The positioning check will be removed. If no movement times are set on this cover, "
      + "its status will return to “To calibrate”. The profile remains available, and other covers using it remain unchanged.");
    context.tab().querySelector('[data-action="cal-tab-assign-close"]').click(); await tick(6);
    await details(context, "cover.living_one");
    context.tab().querySelector('.cal-page-link [data-action="cal-tab-assign"]').click(); await tick(6);
    const source = context.tab().querySelector(".cal-assign-none");
    assert.ok(source.querySelector("input").disabled);
    assert.match(source.querySelector("small").textContent, /è la sorgente del profilo «tall_two_leaf»: per ora il collegamento non si può togliere/);
  });

  test("«Cambia profilo» says that the cover's personal times stay when a profile is given, only when it has some", async () => {
    const data = overview();
    const three = data.covers.find((cover) => cover.entity_id === "cover.living_three");
    three.effective.closing = timing(19.5, "override");
    three.overrides = { closing: three.effective.closing };
    const context = await mount({ overview: data, language: "it" });
    await details(context, "cover.living_three");
    context.tab().querySelector('.cal-page-link [data-action="cal-tab-assign"]').click(); await tick(6);
    const note = context.tab().querySelector(".cal-assign-body > .cal-assign-personal");
    assert.equal(note?.textContent, "Gli eventuali tempi di apertura e chiusura impostati su questa tapparella verranno mantenuti e usati al posto dei corrispondenti tempi del profilo scelto.");
    assert.equal(note.previousElementSibling.className, "cal-assign-intro", "under what assigning does");
    context.tab().querySelector('[data-action="cal-tab-assign-close"]').click(); await tick(6);
    await details(context, "cover.kitchen_two");
    context.tab().querySelector('.cal-page-link [data-action="cal-tab-assign"]').click(); await tick(6);
    assert.equal(context.tab().querySelector(".cal-assign-personal"), null, "none without personal times");
    assert.equal(en.calTabAssignKeepsPersonal, "Any opening or closing times set on this cover will be kept and used instead of the corresponding times in the selected profile.");
  });
});

describe("refusals and A-35's sources", async () => {
  const { translations } = await import("../../custom_components/myhome/frontend/panel/panel-translations.js");
  /** Calibrazione › Office › «Panoramica» › «Cambia profilo», «Spare» chosen. */
  async function changeProfile(context) {
    await context.view("calibration");
    context.tab().querySelector(`.cal-row[data-entity="${OFFICE}"] .cal-row-toggle`).click(); await settle();
    context.tab().querySelector('.cal-flow .cal-page [data-action="cal-tab-assign"]').click(); await settle();
    const sheet = () => context.tab().querySelector(".cal-flow .cal-assign-page");
    const spare = sheet().querySelector('input[name="assign_profile"][value="spare"]');
    spare.checked = true; spare.dispatchEvent(new dom.window.Event("change", { bubbles: true })); await tick();
    return sheet;
  }
  /** Six covers, «tall_two_leaf» measured on Kitchen shutter 2 and followed by Living room shutter 3. */
  function fixture() {
    const inventory = coverInventory([["cover.living_one", "Living room shutter 1", "living"], ["cover.kitchen_two", "Kitchen shutter 2", "kitchen"],
      ["cover.living_three", "Living room shutter 3", "living"], ["cover.kitchen_four", "Kitchen shutter 4", "kitchen"]]);
    const overview = sampleOverview();
    overview.profiles[0].provenance.opening.origin_entity_id = "cover.kitchen_two";
    const template = overview.covers.find((cover) => cover.entity_id === "cover.living_three");
    overview.covers.push({ ...structuredClone(template), entity_id: "cover.kitchen_four", name: "cover.kitchen_four", profile_id: null });
    return { inventory, overview };
  }
  /** Living room shutter 1 measured and saved as a new profile, then A-35. */
  async function atA35() {
    const context = await mount({ ...fixture(), persist: true });
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
    await move(backend, { auto: true, stop: true }); await type("101"); await press("check-start");
    await press("save"); await settle();
    await press("assign-profile"); await settle();
    assert.equal(code(), "A-35");
    const row = (id) => context.wiz().querySelector(`.cal-assign-live .cal-assign-row[data-entity="${id}"]`);
    return { ...context, row };
  }

  for (const announced of [false, true]) {
    test(`«Cambia profilo» refused for another change saved meanwhile (${announced ? "announced" : "not announced"}) reads the data again, says wiz_T627 and assigns again, the choice kept`, async () => {
      const context = await mountPanel();
      const sheet = await changeProfile(context);
      context.backend.silentBump();
      if (announced) { context.backend.notify(); await settle(); }
      const before = structuredClone(context.backend.store);
      sheet().querySelector("[data-assign]").click(); await settle();
      assert.deepEqual(context.backend.store, before, "nothing written");
      const box = sheet().querySelector(".cal-assign-error");
      assert.equal(box.hidden, false);
      assert.equal(box.textContent, translations.it.wiz_T627, "A-35's message, no new text");
      assert.equal(sheet().querySelector('input[name="assign_profile"][value="spare"]').checked, true, "the choice kept");
      const button = sheet().querySelector("[data-assign]");
      assert.equal(button.disabled, false, "Assign on again");
      assert.equal(context.tab().querySelector("#catalogue-reload, #profile-reload"), null, "no «Ricarica»: it reloaded by itself");
      button.click(); await settle();
      assert.equal(context.backend.store.assignments[OFFICE], "spare", `${NAMES[OFFICE]} follows «Spare»`);
      assert.equal(sheet(), null, "the window closed on the confirmation");
    });
  }

  test("a choice no longer there after the new reading is not kept; another refusal keeps the usual words", async () => {
    const context = await mountPanel();
    const sheet = await changeProfile(context);
    // «Spare» deleted elsewhere: the new reading has no «Spare» to keep.
    delete context.backend.store.profiles.spare;
    context.backend.silentBump();
    sheet().querySelector("[data-assign]").click(); await settle();
    assert.equal(sheet().querySelector('input[name="assign_profile"][value="spare"]'), null);
    assert.equal(sheet().querySelector(".cal-assign-error").textContent, translations.it.wiz_T627);
    assert.equal(sheet().querySelector("[data-assign]").disabled, true, "nothing new chosen: Assign off");
    // Another refusal (not a conflict): the same message as before, and Assign on again to retry.
    const other = sheet().querySelector('input[name="assign_profile"][value="manual"]');
    other.checked = true; other.dispatchEvent(new dom.window.Event("change", { bubbles: true })); await tick();
    context.backend.refuseNext("cover_unavailable");
    sheet().querySelector("[data-assign]").click(); await settle();
    assert.equal(sheet().querySelector(".cal-assign-error").textContent, translations.it.profileError_cover_unavailable);
    assert.equal(sheet().querySelector("[data-assign]").disabled, false);
  });

  /** The next profile write fails with `error` (a refusal, or the connection lost), then the backend answers again. */
  function failNextWrite(context, error) {
    const { hass } = context.backend, answer = hass.callWS;
    hass.callWS = async (message) => {
      if (message.type !== "myhome/cover_profiles/write") return answer(message);
      hass.callWS = answer;
      context.backend.calls.push(structuredClone(message));
      throw error;
    };
  }

  test("«Cambia profilo» refused for a passing reason says the profile was not changed, keeps the choice and Assign on: a new tap tries again and closes as usual", async () => {
    const context = await mountPanel();
    const sheet = await changeProfile(context);
    const writes = () => context.backend.calls.filter((message) => message.type === "myhome/cover_profiles/write").length;
    failNextWrite(context, Object.assign(new Error("no"), { code: "unknown_error" }));
    sheet().querySelector("[data-assign]").click(); await settle();
    assert.equal(context.backend.store.assignments[OFFICE], "living", "nothing written");
    const box = sheet().querySelector(".cal-assign-error");
    assert.equal(box.hidden, false);
    assert.equal(box.textContent, translations.it.calTabAssignRetry, "no «Chiudi e riprova»: Assign tries again here");
    assert.equal(sheet().querySelector('input[name="assign_profile"][value="spare"]').checked, true, "the choice kept");
    const button = sheet().querySelector("[data-assign]");
    assert.equal(button.disabled, false, "Assign on again");
    // Refused again, with a text of its own: that text, and Assign on again.
    context.backend.refuseNext("cover_unavailable");
    button.click(); await settle();
    assert.equal(writes(), 2, "the same write sent again");
    assert.equal(box.textContent, translations.it.profileError_cover_unavailable);
    assert.equal(box.hidden, false);
    assert.equal(button.disabled, false, "Assign on again");
    // A request on its way keeps Assign off (no double sending).
    button.click();
    assert.equal(button.disabled, true, "off while the request runs");
    assert.equal(box.hidden, true, "the message hidden at the new attempt");
    await settle();
    assert.equal(writes(), 3);
    assert.equal(context.backend.store.assignments[OFFICE], "spare", `${NAMES[OFFICE]} follows «Spare»`);
    assert.equal(sheet(), null, "the window closed on the confirmation");
  });

  test("«Scollega il profilo» after the connection was lost: Assign on again, a new tap unlinks", async () => {
    const context = await mountPanel();
    const sheet = await changeProfile(context);
    const none = sheet().querySelector('input[name="assign_profile"][value="__no_profile__"]');
    none.checked = true; none.dispatchEvent(new dom.window.Event("change", { bubbles: true })); await tick();
    const button = sheet().querySelector("[data-assign]");
    assert.equal(button.textContent.trim(), translations.it.wiz_T406);
    // Home Assistant's websocket rejects a lost connection with its own numeric code: no text of its own.
    failNextWrite(context, { code: 3, message: "Connection lost" });
    button.click(); await settle();
    assert.equal(context.backend.store.assignments[OFFICE], "living", "nothing written");
    assert.equal(sheet().querySelector(".cal-assign-error").textContent, translations.it.calTabAssignRetry);
    assert.equal(none.checked, true, "the choice kept");
    assert.equal(button.textContent.trim(), translations.it.wiz_T406, "still «Scollega il profilo»");
    assert.equal(button.disabled, false, "on again");
    button.click(); await settle();
    assert.equal(context.backend.store.assignments[OFFICE], undefined, `${NAMES[OFFICE]} follows no profile`);
    assert.equal(sheet(), null, "the window closed on the confirmation");
  });

  test("«Cambia profilo» refused for another change saved meanwhile keeps Assign off until the data are read again", async () => {
    const context = await mountPanel();
    const sheet = await changeProfile(context);
    const { hass } = context.backend, answer = hass.callWS;
    let release = null;
    hass.callWS = (message) => (message.type === "myhome/cover_profiles/overview" && !release
      ? new Promise((resolve) => { release = () => resolve(answer(message)); }) : answer(message));
    context.backend.silentBump();
    sheet().querySelector("[data-assign]").click(); await settle();
    assert.ok(release, "the data are being read again");
    assert.equal(sheet().querySelector(".cal-assign-error").textContent, translations.it.wiz_T627);
    const button = sheet().querySelector("[data-assign]");
    assert.equal(button.disabled, true, "off until the data are back");
    const writes = context.backend.writes().length;
    button.click(); await settle();
    assert.equal(context.backend.writes().length, writes, "a tap meanwhile sends nothing");
    hass.callWS = answer;
    release(); await settle();
    assert.equal(sheet().querySelector('input[name="assign_profile"][value="spare"]').checked, true, "the choice kept");
    assert.equal(sheet().querySelector("[data-assign]").disabled, false, "on again once the data are back");
  });

  test("A-35 lists the source of another profile on; assigned, it follows the new profile and the other profile keeps its values, its source and its followers", async () => {
    const { backend, press, code, row, wiz } = await atA35();
    const source = row("cover.kitchen_two");
    assert.equal(source.querySelector(".cal-assign-state").textContent, 'Source of the profile "tall_two_leaf"', "its line as before");
    const box = source.querySelector('input[name="assign_cover"]');
    assert.equal(box.disabled, false, "on");
    assert.equal(source.classList.contains("is-disabled"), false);
    assert.equal(source.querySelector(".cal-assign-reason"), null, "no reason under it");
    assert.doesNotMatch(wiz().textContent, /cannot be removed for now/, "wiz_T430 is not shown in A-35");
    const tall = structuredClone(backend.overview.profiles.find((profile) => profile.id === "tall"));
    box.checked = true; box.dispatchEvent(new dom.window.Event("change", { bubbles: true })); await tick();
    // The question about slats, when there is one, is the same as for any cover (as «Cambia profilo»).
    if (wiz().querySelector('.cal-assign-confirm [data-wiz="assign-confirm"]')) await press("assign-confirm");
    else await press("assign-submit");
    await settle();
    const fresh = backend.overview.profiles.find((profile) => profile.name === "Living room shutter 1");
    const sent = backend.calls.filter((message) => message.type === "myhome/cover_profiles/manage").map((message) => [message.action, message.entity_ids]);
    assert.deepEqual(sent, [["preview_assign", ["cover.kitchen_two"]], ["assign", ["cover.kitchen_two"]]], "Home Assistant's own multi-cover write, as for any cover");
    assert.equal(code(), "A-34");
    assert.equal(backend.overview.covers.find((cover) => cover.entity_id === "cover.kitchen_two").profile_id, fresh.id);
    const after = backend.overview.profiles.find((profile) => profile.id === "tall");
    assert.deepEqual([after.opening_time, after.closing_time, after.provenance], [tall.opening_time, tall.closing_time, tall.provenance], "the other profile keeps its values and its source");
    assert.deepEqual(after.assigned_to, ["cover.living_three"], "its follower stays with it");
  });
});

describe("its frame", async () => {
  const css = readFileSync(new URL("../../custom_components/myhome/frontend/panel/panel-calibration.css", import.meta.url), "utf8");
  /** A window `width` px wide and 800 high for `matchMedia`, motion reduced. */
  const windowOf = (width) => (query) => ({ matches: query.split(",").some((part) => [...part.matchAll(/\(\s*([a-z-]+)\s*:\s*([a-z0-9-]+)\s*\)/g)].every(([, name, value]) =>
    name === "max-width" ? width <= parseFloat(value) : name === "min-width" ? width >= parseFloat(value) : name === "max-height" ? 800 <= parseFloat(value)
      : name === "min-height" ? 800 >= parseFloat(value) : name === "orientation" ? value === "landscape" : name === "prefers-reduced-motion")), addEventListener() {}, removeEventListener() {} });
  const desktop = () => { globalThis.matchMedia = windowOf(1280); };
  const MEASURED_AT = "2026-11-01T16:09:00+00:00";
  const measured = { source: "guided", recorded_at: MEASURED_AT, origin_entity_id: null, origin_name: null, inherited: false };
  const timing = (value, origin = "profile", scaled = false) => ({ value, origin, provenance: measured, scaled });
  /** «tall» measured on Living room shutter 1 (the source, slats), followed by Kitchen shutter 2 and Living room shutter 3. */
  function family() {
    const data = sampleOverview();
    const profile = data.profiles[0];
    profile.reference_travel_cm = 110;
    profile.assigned_to = ["cover.living_one", "cover.kitchen_two", "cover.living_three"];
    profile.provenance.opening.recorded_at = MEASURED_AT;
    const [one, two, three] = data.covers;
    Object.assign(one, { profile_id: "tall", travel_cm: 110, precision: "basic", slats_state: "yes_measured", effective: { opening: timing(21.8), closing: timing(20.4), slat_time_s: timing(1.4) } });
    Object.assign(two, { profile_id: "tall", travel_cm: 130, precision: "basic", slats_state: "yes_profile", effective: { opening: timing(24.6, "profile", true), closing: timing(23.1, "profile", true), slat_time_s: timing(1.4) } });
    Object.assign(three, { profile_id: "tall", precision: "basic", slats_state: "no_profile", effective: { opening: timing(21.8), closing: timing(20.4) } });
    delete three.travel_cm;
    return data;
  }
  const row = (context, entity) => context.tab().querySelector(`.cal-row[data-entity="${entity}"]`);
  const card = (context) => context.tab().querySelector(".cal-flow .cal-page:not(.cal-assign-page)");
  async function open(context, entity) {
    row(context, entity).querySelector('[data-action="cal-tab-toggle"]').click();
    await tick(6);
    return card(context);
  }
  async function click(context, selector) {
    const button = (card(context) || context.tab().querySelector(".cal-flow .cal-page")).querySelector(selector);
    assert.ok(button, selector);
    button.click();
    await tick(6);
  }

  test("«Cambia profilo» holds a frame marked as its own (as wide as its column from 1024 px); back to the details, the details' width", async () => {
    desktop();
    const context = await mount({ language: "it", overview: family() });
    // «Cambia profilo» is in the card «Profilo» of «Panoramica», the tab the details open on.
    await open(context, "cover.living_one");
    const dialog = context.tab().querySelector("dialog.cal-flow");
    assert.equal(dialog.classList.contains("cal-assign-dialog"), false, "the details: their own width");
    await click(context, '[data-action="cal-tab-assign"]');
    assert.ok(context.tab().querySelector(".cal-flow .cal-assign-page"), "«Cambia profilo»");
    assert.equal(dialog.classList.contains("cal-assign-dialog"), true);
    // «Annulla»: back to the details, in the same frame, at their width.
    context.tab().querySelector('.cal-flow .cal-assign-page [data-action="cal-tab-assign-cancel"]').click();
    await tick(8);
    assert.ok(card(context), "the details again");
    assert.equal(context.tab().querySelector("dialog.cal-flow"), dialog, "the same frame");
    assert.equal(dialog.classList.contains("cal-assign-dialog"), false, "the class gone");
    // The stylesheet: 608 px (560 px of column, 24 px each side) only from 1024 px; the phone and the 480 px dialog unchanged.
    const wide = css.slice(css.lastIndexOf("@media (min-width: 1024px)", css.indexOf("dialog.cal-assign-dialog .cal-flow-box")));
    assert.match(wide, /^@media \(min-width: 1024px\) \{[^}]*\}[^}]*dialog\.cal-assign-dialog \.cal-flow-box \{ width: min\(100%, 608px\); \}/);
    assert.equal(css.match(/cal-assign-dialog/g).length, 1, "no other rule for it");
  });

  test("from the row's ⋮ «Cambia profilo» takes the same narrow frame", async () => {
    desktop();
    const context = await mount({ language: "it", overview: family() });
    const line = row(context, "cover.living_one");
    line.querySelector('[data-action="cal-tab-menu"]').click(); await tick(4);
    const item = line.querySelector('.cal-menu [data-action="cal-tab-assign"]');
    assert.ok(item, "«Cambia profilo» in the ⋮");
    item.click(); await tick(8);
    assert.ok(context.tab().querySelector("dialog.cal-flow.cal-assign-dialog .cal-assign-page"));
  });
});

describe("the dates in «Cambia profilo»", async () => {
  const { translations } = await import("../../custom_components/myhome/frontend/panel/panel-translations.js");
  const text = await import("../../custom_components/myhome/frontend/panel/panel-calibration-text.js");
  const it = translations.it, en = translations.en;
  /** A window `width` px wide and 800 high for `matchMedia`, motion reduced. */
  const windowOf = (width) => (query) => ({ matches: query.split(",").some((part) => [...part.matchAll(/\(\s*([a-z-]+)\s*:\s*([a-z0-9-]+)\s*\)/g)].every(([, name, value]) =>
    name === "max-width" ? width <= parseFloat(value) : name === "min-width" ? width >= parseFloat(value) : name === "max-height" ? 800 <= parseFloat(value)
      : name === "min-height" ? 800 >= parseFloat(value) : name === "orientation" ? value === "landscape" : name === "prefers-reduced-motion")), addEventListener() {}, removeEventListener() {} });
  const phone = () => { globalThis.matchMedia = windowOf(390); };
  const MEASURED_AT = "2026-11-01T16:09:00+00:00", CHECKED_AT = "2026-11-01T18:01:00+00:00", REFINED_AT = "2026-11-09T01:57:00+00:00";
  const measured = { source: "guided", recorded_at: MEASURED_AT, origin_entity_id: null, origin_name: null, inherited: false };
  const inherited = { ...measured, inherited: true, origin_entity_id: "cover.living_one", origin_name: "Living room shutter 1" };
  const timing = (value, origin = "profile", scaled = false, provenance = measured) => ({ value, origin, provenance, scaled });
  /**
   * «tall» measured on Living room shutter 1 (the source, whole numbers in its times and coefficients, a check at half the
   * travel `gap` cm away), followed by Kitchen shutter 2 (115 cm, adapted); Living room shutter 3 never calibrated.
   * `alone`: Kitchen shutter 2 without a profile, calibrated with the tape (its own values, whole numbers too).
   */
  function family({ gap = 2, alone = false } = {}) {
    const data = sampleOverview();
    const profile = data.profiles[0];
    Object.assign(profile, { opening_time: 22, closing_time: 21.6, reference_travel_cm: 110 });
    profile.geometry = { slat_time_s: 3, opening_roll: 1, closing_roll: 1.05 };
    profile.assigned_to = alone ? ["cover.living_one"] : ["cover.living_one", "cover.kitchen_two"];
    profile.provenance.opening.recorded_at = MEASURED_AT;
    const [one, two, three] = data.covers;
    Object.assign(one, { profile_id: "tall", travel_cm: 110, precision: "verified", slats_state: "yes_measured", check_cm: Math.round(gap),
      check_expected_cm: 55, check_measured_cm: 55 + gap, check_at: CHECKED_AT,
      effective: { opening: timing(22), closing: timing(21.6), slat_time_s: timing(3), opening_roll: timing(1, "profile", false, { ...measured, recorded_at: REFINED_AT }),
        closing_roll: timing(1.05, "profile", false, { ...measured, recorded_at: REFINED_AT }) } });
    if (alone) {
      Object.assign(two, { profile_id: null, travel_cm: 120, precision: "refined", slats_state: "yes_measured",
        effective: { opening: timing(12, "cover"), closing: timing(11.5, "cover"), slat_time_s: timing(2, "cover"), opening_roll: timing(1, "cover"), closing_roll: timing(1.25, "cover") } });
    } else {
      Object.assign(two, { profile_id: "tall", travel_cm: 115, precision: "refined", slats_state: "yes_profile", travel_edit: "scaled",
        effective: { opening: timing(23, "profile", true, inherited), closing: timing(22.6, "profile", true, inherited), slat_time_s: timing(3.1, "profile", true, inherited),
          opening_roll: timing(1, "profile", false, inherited), closing_roll: timing(1.05, "profile", false, inherited) } });
    }
    Object.assign(three, { profile_id: null, precision: "none", slats_state: "unknown", overrides: {},
      effective: { opening: { value: 20, origin: "default", provenance: { source: "unknown" } }, closing: { value: 20, origin: "default", provenance: { source: "unknown" } } } });
    delete three.travel_cm;
    return data;
  }
  const row = (context, entity) => context.tab().querySelector(`.cal-row[data-entity="${entity}"]`);
  const card = (context) => context.tab().querySelector(".cal-flow .cal-page:not(.cal-assign-page)");
  async function open(context, entity, tab = "overview") {
    row(context, entity).querySelector('[data-action="cal-tab-toggle"]').click();
    await tick(6);
    if (tab !== "overview") await pageTab(context.tab, tab);
    return card(context);
  }
  async function close(context) { card(context).querySelector(".cal-page-close").click(); await tick(8); }
  const plain = (value) => String(value ?? "").replace(/[  ]/g, " ").trim();

  test("in «Assegna» / «Cambia profilo» the cover's name in «Misurato sulla tapparella **X** · …» is 12 px, the profile's name keeps its 16 px", () => {
    assert.match(css, /:is\(\.cal-assign-page, dialog\.cal-dialog\) \.cal-assign-rows \.cal-assign-measured strong \{ font-size: var\(--cal-fs-12\); line-height: var\(--cal-lh-12\); \}/);
    assert.match(css, /\.cal-assign-page \.cal-assign-rows strong \{ font-size: var\(--cal-fs-16\);/, "the profile's name unchanged");
    // The new rule comes after every rule it overrides (same or higher specificity: the last wins).
    const at = css.lastIndexOf(".cal-assign-measured strong { font-size");
    for (const rule of [".cal-assign-page .cal-assign-rows strong {", "dialog.cal-dialog .cal-assign-rows strong {", "dialog.cal-dialog.cal-sheet .cal-assign-rows strong {"]) assert.ok(css.lastIndexOf(rule) < at, rule);
  });

  test("«Assegna» / «Cambia profilo» opened from Calibrazione: «Misurato sulla tapparella **X** · …», «Misurato su **questa tapparella** · …» on its source", async () => {
    assert.deepEqual([it.wiz_T165, it.wiz_T308, it.wiz_T309, it.wiz_T371], ["Misurato sulla tapparella {cover} · {count} tapparelle lo seguono",
      "Misurato sulla tapparella {cover} · nessuna tapparella lo segue", "Misurato sulla tapparella {cover} · 1 tapparella lo segue", "Misurato sulla tapparella {cover} · {date}"]);
    assert.deepEqual([en.wiz_T165, en.wiz_T308, en.wiz_T309, en.wiz_T371], ["Measured on the cover {cover} · {count} covers follow it",
      "Measured on the cover {cover} · no cover follows it", "Measured on the cover {cover} · 1 cover follows it", "Measured on the cover {cover} · {date}"]);
    assert.deepEqual([it.calAssignRoleSource, en.calAssignRoleSource], ["Misurato su **questa tapparella** · {date}", "Measured on **this cover** · {date}"]);
    phone();
    const when = plain(text.dateTimeText(MEASURED_AT, "it"));
    const context = await mount({ language: "it", overview: family() });
    await open(context, "cover.kitchen_two");
    card(context).querySelector('.cal-page-link [data-action="cal-tab-assign"]').click(); await tick(6);
    let line = context.tab().querySelector(".cal-assign-page .cal-assign-measured");
    assert.deepEqual([plain(line.textContent), line.querySelector("strong").textContent], [`Misurato sulla tapparella Living room shutter 1 · ${when}`, "Living room shutter 1"]);
    context.tab().querySelector('[data-action="cal-tab-assign-close"]').click(); await tick(6);
    if (card(context)) await close(context);
    await open(context, "cover.living_one");
    card(context).querySelector('.cal-page-link [data-action="cal-tab-assign"]').click(); await tick(6);
    line = context.tab().querySelector(".cal-assign-page .cal-assign-measured");
    assert.deepEqual([plain(line.textContent), line.querySelector("strong").textContent], [`Misurato su questa tapparella · ${when}`, "questa tapparella"]);
  });
});

describe("«Scollega il profilo»", async () => {
  const PANEL = "../../custom_components/myhome/frontend/panel/";
  const { translations } = await import(`${PANEL}panel-translations.js`);
  const it = translations.it, en = translations.en;
  const source = readFileSync(new URL(`${PANEL}panel-calibration-tab.js`, import.meta.url), "utf8");
  /** A window `width` px wide and 800 high for `matchMedia`, motion reduced. */
  const windowOf = (width) => (query) => ({ matches: query.split(",").some((part) => [...part.matchAll(/\(\s*([a-z-]+)\s*:\s*([a-z0-9-]+)\s*\)/g)].every(([, name, value]) =>
    name === "max-width" ? width <= parseFloat(value) : name === "min-width" ? width >= parseFloat(value) : name === "max-height" ? 800 <= parseFloat(value)
      : name === "min-height" ? 800 >= parseFloat(value) : name === "orientation" ? value === "landscape" : name === "prefers-reduced-motion")), addEventListener() {}, removeEventListener() {} });
  const phone = () => { globalThis.matchMedia = windowOf(390); };
  const row = (context, entity) => context.tab().querySelector(`.cal-row[data-entity="${entity}"]`);
  const card = (context) => context.tab().querySelector(".cal-flow .cal-page:not(.cal-assign-page)");
  const sheet = (context) => context.tab().querySelector(".cal-assign-page");
  async function open(context, entity) {
    row(context, entity).querySelector('[data-action="cal-tab-toggle"]').click();
    await tick(6);
    return card(context);
  }
  async function close(context) { card(context).querySelector(".cal-page-close").click(); await tick(8); }
  const CHECKED_AT = "2026-11-04T09:30:00+00:00";
  const check = (cover, gap) => Object.assign(cover, { precision: "verified", check_cm: gap, check_expected_cm: 55, check_measured_cm: 55 + gap, check_at: CHECKED_AT });
  const own = (number) => ({ value: number, origin: "override", provenance: { source: "guided", recorded_at: "2026-09-29T10:00:00+00:00", origin_entity_id: null, origin_name: null, inherited: false }, scaled: false });
  /**
   * The world: Kitchen shutter 2 measures «tall_two_leaf» (its source), Living room shutter 3 follows it, Living room
   * shutter 1 is never calibrated (or, `alone`, calibrated with times of its own and no profile).
   */
  function world({ source = "refined", sourceGap = null, alone = null, aloneGap = null, followerGap = null, profiles = true } = {}) {
    const data = sampleOverview();
    data.profiles[0].provenance.opening.origin_entity_id = "cover.kitchen_two";
    const [one, two, three] = data.covers;
    two.precision = source;
    if (sourceGap != null) check(two, sourceGap);
    if (alone) {
      Object.assign(one, { overrides: { opening: 18, closing: 17.2 }, precision: alone, effective: { opening: own(18), closing: own(17.2) } });
      if (aloneGap != null) check(one, aloneGap);
    }
    if (followerGap != null) check(three, followerGap);
    if (!profiles) { data.profiles = []; for (const cover of data.covers) cover.profile_id = null; }
    return data;
  }

  test("the window opened from a follower offers «Scollega il profilo» and says, before the confirmation, what becomes of the times; on the source it is off (wiz_T430)", async () => {
    phone();
    const context = await mount({ language: "it", overview: world() });
    let page = await open(context, "cover.living_three");
    page.querySelector('.cal-page-link [data-action="cal-tab-assign"]').click(); await tick(6);
    let none = sheet(context).querySelector(".cal-assign-none");
    assert.equal(none.querySelector("strong").textContent, "Scollega il profilo");
    assert.equal(none.querySelector("input").disabled, false);
    assert.equal(none.querySelector("input").checked, false, "offered, not chosen");
    assert.equal(none.querySelector("small").textContent, it.wiz_T405.replace("{profile}", "tall_two_leaf"));
    assert.match(it.wiz_T405, /Restano gli eventuali tempi impostati su questa tapparella.*Se non ci sono tempi impostati su questa tapparella, lo stato tornerà a «Da calibrare»\./, "what becomes of the times");
    none.querySelector("input").click(); await tick(2);
    assert.equal(sheet(context).querySelector("[data-assign]").textContent, "Scollega il profilo");
    assert.equal(context.backend.calls.filter((message) => message.type === "myhome/cover_profiles/write").length, 0, "nothing written until confirmed");
    context.tab().querySelector('[data-action="cal-tab-assign-close"]').click(); await tick(6);
    if (card(context)) await close(context);
    page = await open(context, "cover.kitchen_two");
    page.querySelector('.cal-page-link [data-action="cal-tab-assign"]').click(); await tick(6);
    none = sheet(context).querySelector(".cal-assign-none");
    assert.equal(none.querySelector("input").disabled, true, "off on the source");
    assert.equal(none.querySelector("small").textContent, it.wiz_T430.replace("{profile}", "tall_two_leaf"));
  });
});
