/**
 * The Calibration tab's texts, in Italian and English: names with their type, one form for one or more covers, the glossary.
 */
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { after, afterEach, describe, test } from "node:test";

import { sampleOverview } from "./fake-calibration.mjs";
import { dom, mount, pageTab, reset, tick } from "./wizard-harness.mjs";

const original = globalThis.matchMedia;
afterEach(() => { globalThis.matchMedia = original; reset(); });
after(() => dom.window.close());

describe("the glossary", async () => {
  const row = (tab, id) => [...tab().querySelectorAll(".cal-row")].find((item) => item.dataset.entity === id);
  const { translations } = await import("../../custom_components/myhome/frontend/panel/panel-translations.js");
  const { buildScreen, valueRows, travelText } = await import("../../custom_components/myhome/frontend/panel/panel-calibration-screens.js");
  const tIt = (key) => translations.it[key] ?? key, tEn = (key) => translations.en[key] ?? key;

  test("one glossary, IT and EN, and one order of the values wherever they are listed", () => {
    const glossary = { wiz_T093: ["Tempo di salita", "Opening time"], wiz_T094: ["Tempo di discesa", "Closing time"], wiz_T095: ["Tempo lamelle", "Slat time"],
      calTabSlatsNone: ["Nessuna lamella", "No slats"], wiz_T041: ["Corsa del telo", "Curtain travel"], wiz_T105: ["Coefficiente in salita", "Opening coefficient"],
      wiz_T106: ["Coefficiente in discesa", "Closing coefficient"], wiz_T602: ["Tempo di stacco", "Lift-off time"], wiz_T603: ["Sollevamento", "Lift"],
      wiz_T570: ["Precisione", "Precision"], calTabHow: ["Come", "How"], calTabColProfile: ["Profilo", "Profile"],
      wiz_T018: ["Come una tapparella già misurata", "Like a cover already measured"], calTabFromProfile: ["dal profilo", "from the profile"] };
    for (const [key, [it, en]] of Object.entries(glossary)) assert.deepEqual([translations.it[key], translations.en[key]], [it, en], key);
    // The rolls are coefficients, never «Rullo» as a label; path B has one name, never «misurazione con profilo».
    for (const language of ["it", "en"]) {
      const all = Object.values(translations[language]).join("\n");
      assert.doesNotMatch(all, /misurazione con profilo|measurement with a profile/, language);
    }
    for (const key of ["calTabSlats", "calTabSlatsYes", "calTabSlatsNo", "wiz_T188", "wiz_T557"]) assert.equal(translations.it[key], undefined, `${key} retired`);
    // One order: times, slats, travel, coefficients, profile (C-01); the details the same after its two times.
    const n = (value, digits = 1) => String(value).replace(".", ","); n.fixed = (value) => Number(value).toFixed(1).replace(".", ",");
    assert.deepEqual(valueRows(tIt, n, { opening: 22, closing: 21, slat: 0, travel: 198, opening_roll: 1.8, closing_roll: 1.9, profile: "alte" }).map(([label]) => label),
      ["Tempo di salita", "Tempo di discesa", "Tempo lamelle", "Corsa del telo", "Coefficiente in salita", "Coefficiente in discesa", "Profilo"]);
    assert.deepEqual(valueRows(tIt, n, { slat: 0 }, { times: false, withProfile: false }).map(([label, value]) => [label, value]),
      [["Tempo lamelle", "Nessuna lamella"], ["Corsa del telo", "—"], ["Coefficiente in salita", "—"], ["Coefficiente in discesa", "—"]]);
    // A-21: the table's labels and the technical rows, the same words.
    const view = { mode: "geometry", phase: "review", step: "half_close", values: { opening_time: 22, closing_time: 21 }, samples: { lift: 4 }, readings: { half_open: 99, half_close: 98, gap: 3 },
      geometry: { slat_time_s: 4, opening_roll: 1.8, closing_roll: 1.9 }, slats: true, travel_cm: 198, owner: true, sequence: 9, check: { deviation_cm: 1, passed: true, measured_cm: 99, expected_cm: 98, check_seconds: 10 } };
    const summary = buildScreen({ t: tIt, language: "it", stage: "session", path: "A", view, local: {}, cover: { name: "K", effective: {} } });
    assert.deepEqual(summary.table.rows.map((row) => row.label), ["Tempo di salita", "Tempo di discesa", "Tempo lamelle", "Corsa del telo"]);
    assert.deepEqual(summary.technical.rows.map((row) => row.label), ["Coefficiente in salita", "Coefficiente in discesa"]);
    // The steps: «Tempo di stacco», «Sollevamento», «Tempo di salita», «Corsa del telo», «Tempo di discesa», «Precisione».
    const labels = summary.stepper.phases.flatMap((phase) => phase.substeps.map((item) => item.label)).filter(Boolean);
    for (const label of ["Tempo di stacco", "Sollevamento", "Tempo di salita", "Corsa del telo", "Tempo di discesa", "Precisione"]) assert.ok(labels.includes(label), label);
    assert.ok(!labels.some((label) => /^(Stacco|Salita completa|Discesa completa|Scarto)$/.test(label)), "no second name for the same value");
  });
});

describe("runs and travel", async () => {
  const { translations } = await import("../../custom_components/myhome/frontend/panel/panel-translations.js");
  const en = translations.en, it = translations.it;

  test("the full runs' texts say to wait until the motor's sound stops completely, IT and EN", () => {
    assert.match(it.wiz_T477, /\*\*Preparati a premere il pulsante ② quando il motore si ferma da solo in alto \(il rumore del motore cessa completamente\)\.\*\*$/);
    assert.match(it.wiz_T479, /\*\*Preparati a premere il pulsante ② quando il motore si ferma da solo in basso \(il rumore del motore cessa completamente\)\.\*\*/);
    assert.match(en.wiz_T477, /at the top \(its sound stops completely\)\.\*\*$/);
    assert.match(en.wiz_T479, /at the bottom \(its sound stops completely\)\.\*\*/);
  });

  test("«Assegna un profilo» no longer takes the travel for granted (wiz_T609), nor «the same height» for a profile without a reference travel (wiz_T629)", () => {
    assert.equal(it.wiz_T609, "Questa tapparella userà i tempi del profilo che scegli. Se il profilo indica una corsa di riferimento e il telo di questa tapparella è più alto o più basso, dopo potrai inserirne la misura nella sua scheda: i tempi si adatteranno.");
    assert.equal(en.wiz_T609, "This cover will use the times of the profile you choose. If the profile has a reference travel and this cover's curtain is taller or shorter, you can enter its measurement on its card afterwards, and the times will adapt.");
    assert.doesNotMatch(it.wiz_T629 + en.wiz_T629, /stessa altezza|same height/);
  });
});

describe("the check", async () => {
  const { translations } = await import("../../custom_components/myhome/frontend/panel/panel-translations.js");
  const it = translations.it, en = translations.en;

  test("the check's texts in Italian and English, the same keys and placeholders; English says «cover», never «shutter»", () => {
    const keys = Object.keys(it).filter((key) => key.startsWith("cal_") || key.startsWith("calTabTab") || key === "calTabActionCheck" || key === "calTabProfileSection");
    assert.ok(keys.length > 50);
    for (const key of keys) {
      assert.equal(typeof en[key], "string", key);
      const names = (text) => new Set(text.match(/\{\w+\}/g) || []);
      for (const name of names(en[key])) assert.ok(names(it[key]).has(name), `${key}: ${name} in both`);
      assert.doesNotMatch(en[key], /shutter/i, `${key}: «cover»`);
    }
    for (const key of ["calTabAlsoUsedBy", "calTabUserSource", "calTabUserFollower", "calTabUsersNone", "calTabSetHereF", "calTabCompare", "calTabRelSame", "calTabRelOwn", "calTabRelNone", "calTabReplaced", "wiz_T405", "calTabAssignKeepsPersonal"]) {
      assert.doesNotMatch(en[key], /shutter/i, key);
    }
    assert.equal(it.calTabAssignKeepsPersonal, "Gli eventuali tempi di apertura e chiusura impostati su questa tapparella verranno mantenuti e usati al posto dei corrispondenti tempi del profilo scelto.");
    assert.equal(it.calTabAlsoUsedBy, "Profilo usato anche da");
    assert.equal(it.calTabReplaced, "Non usato qui");
    // Not written yet: the curve's warning. Gone: the two steps, «Non ne sono sicuro», the travel's
    // and the profile's outcomes (D3, D4), the explanations (D5–D7), POS-noref.
    for (const key of ["cal_D0_warn_follower", "cal_P1_header", "cal_P1_hint", "cal_P1_v1_title", "cal_P2a_a4", "cal_P2b_a4", "cal_P2c_a3", "cal_D0_answered", "cal_D2_button_first",
      "cal_D3_todo", "cal_D3_noref_title", "cal_D4_todo", "cal_D5_text", "cal_D6_text", "cal_D7_text", "cal_POS_noref_title"]) assert.equal(it[key], undefined, key);
    assert.ok(!Object.entries(it).some(([key, text]) => key.startsWith("cal_") && /Non ne sono sicuro/.test(text)), "no «Non ne sono sicuro»");
    // Positions between open and closed are never «a metà» / «50%» (the check is: it is taken at half the travel).
    for (const key of ["cal_P1_full_desc", "cal_P1_middle_title", "cal_P1_middle_desc", "cal_P1_unsure_title", "cal_P1_unsure_desc", "cal_P1_note", "cal_POS_basic_text"]) {
      assert.doesNotMatch(it[key], /metà|50/, key); assert.doesNotMatch(en[key], /half|50/i, key);
    }
  });
});

describe("the procedure", async () => {
  const { translations } = await import("../../custom_components/myhome/frontend/panel/panel-translations.js");
  const it = translations.it, en = translations.en;

  test("the check procedure's texts, Italian and English", () => {
    assert.equal(it.cal_P1_full_desc, "Home Assistant segna aperta o chiusa troppo presto o troppo tardi.");
    assert.equal(it.cal_P1_note, "Se con Apri o Chiudi si ferma prima di arrivare in fondo, dipende dalle impostazioni dell’attuatore, non dalla calibrazione: falle controllare all’installatore.", "the note stays");
    assert.equal(it.cal_D0_heading, "Cosa possiamo fare");
    assert.equal(it.cal_D1_todo, "Misuriamo con un metro l’altezza del bordo inferiore della tapparella in alcuni punti della corsa. Queste misure servono a rendere più precise le posizioni intermedie. Alla fine controlleremo dove si ferma a metà corsa.");
    for (const key of ["cal_D2_todo", "cal_D2_todo_generic", "cal_D2_todo_noslats"]) {
      assert.match(it[key], /^Misuriamo quanto impiega /, key);
      assert.match(it[key], /La procedura ti guida con i pulsanti di avvio e arresto\.$/, `${key}: the rest unchanged`);
      assert.match(en[key], /^We record how long /, `${key} (en)`);
    }
    assert.equal(en.cal_D0_heading, "What we can do");
    assert.equal(en.cal_P1_full_desc, "Home Assistant shows it open or closed too early or too late.");
    // The captions of the guided procedures in «Posizionamento», with «noi».
    assert.deepEqual([it.cal_POS_none_measure_caption, it.cal_POS_refine_caption, it.cal_POS_refine_caption_again], [
      "Misuriamo quanto impiega la tapparella ad aprirsi e a chiudersi del tutto.",
      "Misuriamo alcune altezze con un metro per affinare le posizioni intermedie.",
      "Ripetiamo le misure con il metro per affinare le posizioni intermedie."]);
    // The user's gestures and the advice outside the procedures keep «tu».
    assert.equal(it.cal_POS_none_how_measure, "Misura i tempi");
    assert.equal(it.cal_D2_button, "Misura di nuovo i tempi");
    assert.match(it.cal_P1_note, /falle controllare all’installatore/);
  });
});

describe("the card", async () => {
  const { translations } = await import("../../custom_components/myhome/frontend/panel/panel-translations.js");
  const it = translations.it, en = translations.en;

  test("the card's texts, Italian and English", () => {
    assert.deepEqual([it.calTabTabOverview, it.calTabTabDetails, en.calTabTabOverview, en.calTabTabDetails], ["Panoramica", "Dettagli", "Overview", "Details"]);
    assert.deepEqual([it.calTabStateSection, it.calTabTraits, it.cal_S1_fold, it.cal_S1_fold_follower], ["Stato", "Caratteristiche", "Qualcosa non va?", "Ricalibriamo questa tapparella."], "the card's section titles and its fold");
    assert.equal(it.cal_POS_follow_title, "Usa i tempi di un profilo");
    assert.equal(it.cal_POS_follow_adapted, "I tempi del profilo sono adattati alla corsa di questa tapparella: {cm} cm invece di {ref}.");
    assert.equal(it.cal_POS_curve_text, "Le fermate intermedie tengono conto anche delle altezze misurate con il metro su questa tapparella.");
    assert.equal(it.cal_POS_check_half, "Alla verifica a metà corsa si è fermata a {cm} cm dal punto atteso.");
    assert.equal(it.cal_POS_check_beyond, "Oltre {limit} cm conviene perfezionare il posizionamento.");
    assert.deepEqual([it.calTabLastCheck, it.calTabCheckWhere, it.calTabCheckWhereHalf, it.calTabCheckGap, it.calTabCheckNone], ["Ultima verifica", "Dove", "a metà corsa", "Scarto", "Nessuna verifica"]);
    assert.deepEqual([it.calTabHow, it.calTabHow_guided_tape, it.calTabHow_linked], ["Come", "Procedura guidata, con il metro", "Collegata al profilo"]);
    assert.deepEqual([it.calTabUserSource, it.calTabUserFollower, it.calTabUsersNone], ["ha misurato il profilo", "segue il profilo", "Nessun’altra tapparella lo segue"]);
    for (const key of ["calTabTabOverview", "calTabStateSection", "calTabTraits", "calTabSetHereF", "calTabHow_guided", "calTabHow_automatic", "calTabLastCheck", "calTabUsersNone",
      "cal_S1_fold_follower", "cal_POS_follow_adapted", "cal_POS_check_any", "cal_POS_check_beyond"]) {
      assert.equal(typeof en[key], "string", key);
      assert.doesNotMatch(en[key], /shutter/i, `${key}: «cover»`);
    }
    for (const key of ["calTabTabPositioning", "calTabTabValues", "cal_POS_label", "cal_POS_check_label", "calTabMethod", "calTabFollowedBy", "calTabSourceMark"]) assert.equal(it[key] ?? en[key], undefined, `${key} retired`);
  });
});

describe("no repetitions", async () => {
  const { translations } = await import("../../custom_components/myhome/frontend/panel/panel-translations.js");
  const it = translations.it, en = translations.en;

  test("wiz_T575 no longer names a chip «Verificata»", () => {
    assert.equal(it.wiz_T575, "Home Assistant non riesce a pianificare una verifica con i valori appena misurati. Ripeti la verifica, oppure salva senza: la tapparella non avrà una verifica.");
    assert.equal(en.wiz_T575, "Home Assistant cannot plan a check for the values just measured. Repeat the check, or save without it: the cover will have no check.");
    for (const language of ["it", "en"]) assert.doesNotMatch(translations[language].wiz_T575, /Verificata|Verified/);
  });

  test("the check's texts without repetitions, Italian and English", () => {
    assert.equal(it.cal_S1_fold_follower, "Ricalibriamo questa tapparella.");
    assert.equal(it.cal_D2_todo, "Misuriamo quanto impiega la tapparella ad aprirsi e a chiudersi del tutto, poi il tempo necessario ad aprire gli spazi tra le lamelle. La procedura ti guida con i pulsanti di avvio e arresto.");
    assert.equal(it.cal_D0_warn_follower_today, "Le nuove misure verranno salvate in un profilo nuovo, solo per questa tapparella. Non riceverà più gli aggiornamenti del profilo attuale.");
    assert.equal(it.cal_D2_lead_unsure, "Ripartiamo dai tempi di apertura e chiusura. Alla fine potrai continuare con il metro, per rendere più precise le posizioni intermedie.");
    assert.equal(it.cal_POS_none_text, "Home Assistant stima la posizione con i tempi attualmente impostati.");
    assert.equal(en.cal_S1_fold_follower, "We recalibrate this cover.");
    assert.equal(en.cal_D2_todo, "We record how long the cover takes to open and close fully, then how long the gaps between its slats take to open. The procedure guides you with start and stop controls.");
    assert.equal(en.cal_D0_warn_follower_today, "The new measurements will be saved in a new profile, for this cover only. It will no longer receive updates from the current profile.");
    assert.equal(en.cal_D2_lead_unsure, "We start again from the opening and closing times. At the end you can continue with the tape measure, to make intermediate positions more precise.");
    assert.equal(en.cal_POS_none_text, "Home Assistant estimates the position using the current movement times.");
    for (const language of ["it", "en"]) for (const key of ["cal_D2_todo", "cal_D0_warn_follower_today"]) assert.doesNotMatch(translations[language][key], /\{cover\}/, `${language} ${key}: no name`);
  });
});

describe("names with their type", async () => {
  const PANEL = "../../custom_components/myhome/frontend/panel/";
  const { translations } = await import(`${PANEL}panel-translations.js`);
  const text = await import(`${PANEL}panel-calibration-text.js`);
  const { sharedNotice } = await import(`${PANEL}panel-calibration-screens.js`);
  const it = translations.it, en = translations.en;
  /** A window `width` px wide and 800 high for `matchMedia`, motion reduced. */
  const windowOf = (width) => (query) => ({ matches: query.split(",").some((part) => [...part.matchAll(/\(\s*([a-z-]+)\s*:\s*([a-z0-9-]+)\s*\)/g)].every(([, name, value]) =>
    name === "max-width" ? width <= parseFloat(value) : name === "min-width" ? width >= parseFloat(value) : name === "max-height" ? 800 <= parseFloat(value)
      : name === "min-height" ? 800 >= parseFloat(value) : name === "orientation" ? value === "landscape" : name === "prefers-reduced-motion")), addEventListener() {}, removeEventListener() {} });
  const phone = () => { globalThis.matchMedia = windowOf(390); };
  /**
   * Every key reworded or added for the Calibration tab, all used by it only (its details, wizard, procedures, «Assegna»).
   * (`cal_LINK_unlink_desc_*` left with the card's row «Scollega dal profilo».)
   */
  const CHANGED = ["cal_POS_follow_same", "cal_POS_follow_unscaled", "wiz_T511", "wiz_T407", "wiz_T607", "wiz_T608", "wiz_T609", "wiz_T430", "wiz_T428", "wiz_T428_one", "cal_D1_title", "cal_D2_title",
    "cal_D2_title_unsure", "wiz_T015", "wiz_T029", "wiz_T604", "wiz_T052", "wiz_T203", "wiz_T711", "wiz_T712", "wiz_T080", "wiz_T205", "wiz_T713",
    "wiz_T714", "wiz_T689", "wiz_T715", "wiz_T180", "wiz_T182", "wiz_T185", "wiz_T197", "wiz_T210", "wiz_T226", "wiz_T653", "wiz_T228", "wiz_T230",
    "wiz_T489", "wiz_T235", "wiz_T490", "wiz_T287", "wiz_T314", "wiz_T315", "wiz_T288", "wiz_T316", "wiz_T317", "wiz_T321", "wiz_T506", "wiz_T565",
    "wiz_T566", "wiz_T567", "wiz_T695", "wiz_T696", "wiz_T697", "wiz_T709", "wiz_T709_one", "wiz_T721", "wiz_T721_one", "wiz_T710", "wiz_T710_one", "wiz_T536", "wizNoProfileBody",
    "wizSaveCoverOnly", "wizSavedCover", "wiz_T405", "calAssignSlatsNoYesNamed", "calAssignSlatsYesNoNamed", "cal_D0_warn_source_names",
    "cal_D0_warn_source_names_one", "cal_D0_warn_source_checks", "cal_D0_warn_source_checks_one", "wiz_T169", "wiz_T620", "wiz_T621", "wiz_T436",
    "wiz_T437", "wiz_T438", "wiz_T708", "wiz_T587", "wiz_T675", "calTabStatus_profile_adapted"];
  const AT = (minutes) => new Date(Date.parse("2026-11-03T17:39:00+00:00") + minutes * 60000).toISOString().replace(".000Z", "+00:00");
  const evidence = (recorded_at, extra = {}) => ({ source: "guided", recorded_at, origin_entity_id: null, origin_name: null, inherited: false, ...extra });
  const timing = (value, provenance, origin = "profile", scaled = false) => ({ value, origin, provenance, scaled });
  /**
   * «tall_two_leaf» measured on Living room shutter 1 in one calibration (the opening at 18:39, the closing and the lift-off
   * at 18:40; `refinedAfter` minutes later a Perfeziona, when given), followed by Kitchen shutter 2 (`travel` cm against the
   * 110 of the reference: adapted when they differ); Living room shutter 3 never calibrated.
   */
  function family({ travel = 130, refinedAfter = null } = {}) {
    const data = sampleOverview();
    const profile = data.profiles[0];
    Object.assign(profile, { opening_time: 22, closing_time: 21.6, reference_travel_cm: 110, assigned_to: ["cover.living_one", "cover.kitchen_two"] });
    profile.geometry = { slat_time_s: 3, opening_roll: 1, closing_roll: 1 };
    profile.provenance.opening = evidence(AT(0), { origin_entity_id: "cover.living_one", origin_name: "Living room shutter 1" });
    profile.provenance.closing = evidence(AT(1), { origin_entity_id: "cover.living_one", origin_name: "Living room shutter 1" });
    const rolled = refinedAfter == null ? { source: "unknown", recorded_at: null } : evidence(AT(1 + refinedAfter));
    const [one, two, three] = data.covers;
    Object.assign(one, { profile_id: "tall", travel_cm: 110, precision: refinedAfter == null ? "basic" : "refined", slats_state: "yes_measured",
      effective: { opening: timing(22, profile.provenance.opening), closing: timing(21.6, profile.provenance.closing), slat_time_s: timing(3, evidence(AT(1))),
        opening_roll: timing(1, rolled), closing_roll: timing(1, rolled) } });
    const inherited = evidence(AT(0), { inherited: true, origin_entity_id: "cover.living_one", origin_name: "Living room shutter 1" });
    const scaled = travel !== 110;
    Object.assign(two, { profile_id: "tall", travel_cm: travel, precision: "basic", slats_state: "yes_profile", overrides: {},
      effective: { opening: timing(22 * travel / 110, inherited, "profile", scaled), closing: timing(21.6 * travel / 110, inherited, "profile", scaled),
        slat_time_s: timing(3, inherited, "profile", scaled) } });
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

  test("no name alone: a cover's name after «tapparella»/«tapparelle» («cover»/«covers»), a profile's after «profilo «»» («profile \"\"»), in Italian and English", () => {
    const COVER = /\{(cover|names|source|checks)\}/g, PROFILE = /[«"]\{(profile|old|name)\}[»"]/g;
    for (const key of CHANGED) {
      for (const [language, table, cover, profile] of [["it", it, /(tapparell[ae]) $/, /(profilo|profilo nuovo,) $/i], ["en", en, /\b(cover|covers) $/, /\b(profile|new profile,) $/i]]) {
        const value = table[key];
        assert.equal(typeof value, "string", `${language} ${key}`);
        for (const match of value.matchAll(COVER)) assert.match(value.slice(0, match.index), cover, `${language} ${key}: «${match[0]}» with its type`);
        for (const match of value.matchAll(PROFILE)) assert.match(value.slice(0, match.index), profile, `${language} ${key}: «${match[0]}» with its type`);
        assert.doesNotMatch(value, /^\{cover\}|[«"]\{cover\}[»"]/, `${language} ${key}: never the cover's name alone`);
      }
    }
  });

  test("the reworded texts, Italian and English", () => {
    assert.deepEqual([it.cal_POS_follow_same, it.cal_POS_follow_unscaled], ["Usa senza modifiche i tempi misurati sulla tapparella {source}.",
      "Usa i tempi misurati sulla tapparella {source}, senza adattarli alla corsa di questa tapparella."]);
    assert.deepEqual([it.wiz_T511, it.wiz_T407], ["La tapparella {cover} ora segue il profilo «{profile}».", "Profilo scollegato dalla tapparella {cover}."]);
    // The card's Scollega lines are gone (the window says it with wiz_T405).
    assert.deepEqual([it.cal_LINK_unlink_desc_none, it.cal_LINK_unlink_desc_one, it.cal_LINK_unlink_desc_personal], [undefined, undefined, undefined]);
    assert.deepEqual([it.wiz_T607, it.wiz_T608], ["Assegna un profilo alla tapparella {cover}", "Cambia il profilo della tapparella {cover}"]);
    assert.match(it.wiz_T609, /^Questa tapparella userà i tempi del profilo che scegli\. Se il profilo indica/);
    assert.match(it.wiz_T430, /^Questa tapparella è la sorgente del profilo «\{profile\}»: per ora il collegamento non si può togliere\./);
    assert.deepEqual([it.wiz_T428_one, it.wiz_T428], ["Seguito dalla tapparella {names}", "Seguito dalle tapparelle {names}"]);
    assert.deepEqual([it.cal_D1_title, it.cal_D2_title, it.cal_D2_title_unsure], ["Perfezioniamo le posizioni intermedie di questa tapparella",
      "Controlliamo i tempi di questa tapparella", "Ricontrolliamo questa tapparella dall’inizio"]);
    assert.deepEqual([en.wiz_T511, en.wiz_T407, en.wiz_T607, en.wiz_T608], ["The cover {cover} now follows the profile \"{profile}\".",
      "Profile unlinked from the cover {cover}.", "Assign a profile to the cover {cover}", "Change the profile of the cover {cover}"]);
    assert.deepEqual([en.cal_D1_title, en.cal_D2_title, en.cal_D2_title_unsure], ["Let’s refine this cover’s intermediate positions",
      "Check this cover’s movement times", "Let’s check this cover again from the start"]);
  });

  test("one cover or more, each in its own form: «Seguito da…», «Cambiano anche i tempi…», «Perderà la verifica…», the source's warning", () => {
    const t = (key) => it[key];
    assert.equal(text.followedBy(t, ["Office"], "it").replace(/ /g, " "), "Seguito dalla tapparella Office");
    assert.equal(text.followedBy(t, ["Office", "Kitchen"], "it").replace(/ /g, " "), "Seguito dalle tapparelle Office e Kitchen");
    assert.equal(text.followedBy((key) => en[key], ["Office"], "en").replace(/ /g, " "), "Followed by the cover Office");
    // What updating the profile changes for the others, said in yellow under «Aggiorna il profilo «tall»» before Save.
    const notice = (followers, table = it) => sharedNotice({ t: (key) => table[key], cover: { entity_id: "cover.a", profile: { name: "tall" } },
      view: { entity_id: "cover.a" }, coverName: (id) => ({ "cover.b": "Office", "cover.c": "Kitchen" })[id] }, { followers });
    const changed = { opening: { before: 20, after: 21 } };
    const reshaped = { model_before: "slat_roll", model_after: "slat_roll", geometry_changes: { opening_roll: { before: 1, after: 1.4 } } };
    // The times only, one cover or two; the check lost after them.
    assert.equal(notice([{ entity_id: "cover.b", changes: changed, loses_check: true }]),
      "Cambiano anche i tempi dell’altra tapparella che lo segue: Office. Perderà la verifica la tapparella Office.");
    assert.equal(notice([{ entity_id: "cover.b", changes: changed, loses_check: true }, { entity_id: "cover.c", changes: changed, loses_check: true }]),
      "Cambiano anche i tempi delle 2 tapparelle che lo seguono: Office, Kitchen. Perderanno la verifica le tapparelle Office, Kitchen.");
    // The times and the intermediate positions, every cover whose times change changing those too.
    assert.equal(notice([{ entity_id: "cover.b", changes: changed, ...reshaped }]),
      "Cambiano anche i tempi e le posizioni intermedie dell’altra tapparella che lo segue: Office.");
    assert.equal(notice([{ entity_id: "cover.b", changes: changed, ...reshaped }, { entity_id: "cover.c", changes: changed, ...reshaped }]),
      "Cambiano anche i tempi e le posizioni intermedie delle 2 tapparelle che lo seguono: Office, Kitchen.");
    // The intermediate positions only.
    assert.equal(notice([{ entity_id: "cover.b", ...reshaped }]), "Cambiano anche le posizioni intermedie dell’altra tapparella che lo segue: Office.");
    assert.equal(notice([{ entity_id: "cover.b", ...reshaped }, { entity_id: "cover.c", ...reshaped }]),
      "Cambiano anche le posizioni intermedie delle 2 tapparelle che lo seguono: Office, Kitchen.");
    // Mixed: the times' sentence, then today's for the covers whose intermediate positions alone change, then the check.
    assert.equal(notice([{ entity_id: "cover.b", changes: changed, ...reshaped }, { entity_id: "cover.c", ...reshaped, loses_check: true }]),
      "Cambiano anche i tempi e le posizioni intermedie dell’altra tapparella che lo segue: Office. Cambiano le posizioni intermedie della tapparella Kitchen. Perderà la verifica la tapparella Kitchen.");
    assert.equal(notice([{ entity_id: "cover.b", changes: changed, ...reshaped }, { entity_id: "cover.c", changes: changed }]),
      "Cambiano anche i tempi delle 2 tapparelle che lo seguono: Office, Kitchen.", "not every one changes its positions: the times' sentence");
    // Nothing changes for the others (this cover itself not counted): nothing said.
    assert.equal(notice([{ entity_id: "cover.a", changes: changed }, { entity_id: "cover.b", changes: { opening: { before: 20, after: 20 } } }]), "");
    assert.equal(notice([{ entity_id: "cover.b", changes: changed, ...reshaped }], en), "The times and intermediate positions of the other cover that follows it change too: Office.");
    assert.equal(notice([{ entity_id: "cover.b", ...reshaped }, { entity_id: "cover.c", changes: changed }], en),
      "The times of the other cover that follows it change too: Kitchen. Intermediate positions change for the cover Office.");
    assert.deepEqual([it.wiz_T722, it.wiz_T722_one, it.wiz_T723, it.wiz_T723_one, it.wiz_T724, it.wiz_T724_one], [
      "Cambiano anche i tempi delle {count} tapparelle che lo seguono: {names}.", "Cambiano anche i tempi dell’altra tapparella che lo segue: {names}.",
      "Cambiano anche i tempi e le posizioni intermedie delle {count} tapparelle che lo seguono: {names}.", "Cambiano anche i tempi e le posizioni intermedie dell’altra tapparella che lo segue: {names}.",
      "Cambiano anche le posizioni intermedie delle {count} tapparelle che lo seguono: {names}.", "Cambiano anche le posizioni intermedie dell’altra tapparella che lo segue: {names}."]);
    assert.deepEqual([en.wiz_T722, en.wiz_T722_one, en.wiz_T723, en.wiz_T723_one, en.wiz_T724, en.wiz_T724_one], [
      "The times of the {count} covers that follow it change too: {names}.", "The times of the other cover that follows it change too: {names}.",
      "The times and intermediate positions of the {count} covers that follow it change too: {names}.", "The times and intermediate positions of the other cover that follows it change too: {names}.",
      "The intermediate positions of the {count} covers that follow it change too: {names}.", "The intermediate positions of the other cover that follows it change too: {names}."]);
    assert.equal(text.fill(it.calAssignSlatsYesNoNamed, { cover: "Door" }), "La tapparella Door ha le lamelle, il profilo no: vicino alla chiusura le posizioni possono essere meno precise. Assegna comunque?");
  });

  test("no key reworded here is used by the Profili or Dispositivi tabs; their shared texts are as they were", () => {
    for (const file of ["panel-cover-profiles.js", "panel-cover-profile-list.js", "panel-profile-catalogue-editor.js", "panel-cover-calibration.js", "panel-profile-presentation.js", "myhome-panel.js"]) {
      const source = readFileSync(new URL(`${PANEL}${file}`, import.meta.url), "utf8");
      for (const key of CHANGED) assert.doesNotMatch(source, new RegExp(`["'\`]${key}["'\`]`), `${file} does not use ${key}`);
    }
    // Left as it was: the next cover of a multi-cover session already says «la tapparella successiva», in a module the
    // Dispositivi tab shares.
    assert.equal(it.calNextStep_next_cover, "la misura della tapparella successiva, «{name}»");
    assert.equal(it.calTabStatus_profile, "Dal profilo");
  });

  test("the reworded texts on the page: a follower's sentence, the source's procedure titles, «Cambia profilo»'s title and introduction", async () => {
    phone();
    const context = await mount({ language: "it", overview: family({ travel: 110 }) });
    let page = await open(context, "cover.kitchen_two");
    assert.equal(plain(page.querySelector(".cal-pos-state").textContent).includes("Usa senza modifiche i tempi misurati sulla tapparella Living room shutter 1."), true);
    assert.equal(page.querySelector('.cal-page-link [data-action="cal-tab-unlink"]'), null);
    page.querySelector('.cal-page-link [data-action="cal-tab-assign"]').click(); await tick(6);
    const sheet = context.tab().querySelector(".cal-assign-page");
    assert.match(sheet.querySelector(".cal-assign-none small").textContent, /^Questa tapparella verrà scollegata dal profilo «tall_two_leaf»\./);
    assert.deepEqual([sheet.querySelector("h2").textContent, sheet.querySelector(".cal-assign-intro").textContent.split(".")[0]],
      ["Cambia il profilo della tapparella Kitchen shutter 2", "Questa tapparella userà i tempi del profilo che scegli"]);
    context.tab().querySelector('[data-action="cal-tab-assign-close"]').click(); await tick(6);
    if (card(context)) await close(context);
    page = await open(context, "cover.living_one");
    page.querySelector('.cal-pos-help [data-action="cal-tab-check-start"]').click(); await tick(6);
    card(context).querySelector('[data-action="cal-tab-check-answer"][data-id="middle"]').click(); await tick(6);
    page = card(context);
    assert.deepEqual([page.querySelector("#cal-page-name").textContent, page.querySelector(".cal-step-title").textContent],
      ["Living room shutter 1", "Perfezioniamo le posizioni intermedie di questa tapparella"], "the name in the bar, «questa tapparella» under it");
    assert.equal(plain(page.querySelector(".cal-note.is-warning").textContent).startsWith("Se aggiorni il profilo attuale, anche la tapparella Kitchen shutter 2 userà"), true);
  });
});

describe("«questa tapparella»", async () => {
  const PANEL = "../../custom_components/myhome/frontend/panel/";
  const { translations } = await import(`${PANEL}panel-translations.js`);
  const it = translations.it, en = translations.en;
  /** A window `width` px wide and 800 high for `matchMedia`, motion reduced. */
  const windowOf = (width) => (query) => ({ matches: query.split(",").some((part) => [...part.matchAll(/\(\s*([a-z-]+)\s*:\s*([a-z0-9-]+)\s*\)/g)].every(([, name, value]) =>
    name === "max-width" ? width <= parseFloat(value) : name === "min-width" ? width >= parseFloat(value) : name === "max-height" ? 800 <= parseFloat(value)
      : name === "min-height" ? 800 >= parseFloat(value) : name === "orientation" ? value === "landscape" : name === "prefers-reduced-motion")), addEventListener() {}, removeEventListener() {} });
  const phone = () => { globalThis.matchMedia = windowOf(390); };
  const AT = (minutes) => new Date(Date.parse("2026-11-03T17:39:00+00:00") + minutes * 60000).toISOString().replace(".000Z", "+00:00");
  const evidence = (recorded_at, extra = {}) => ({ source: "guided", recorded_at, origin_entity_id: null, origin_name: null, inherited: false, ...extra });
  const timing = (value, provenance, origin = "profile", scaled = false) => ({ value, origin, provenance, scaled });
  /**
   * «tall_two_leaf» measured on Living room shutter 1 in one calibration (the opening at 18:39, the closing and the lift-off
   * at 18:40; `refinedAfter` minutes later a Perfeziona, when given), followed by Kitchen shutter 2 (`travel` cm against the
   * 110 of the reference); Living room shutter 3 never calibrated, without a profile.
   */
  function family({ travel = 110, refinedAfter = null } = {}) {
    const data = sampleOverview();
    const profile = data.profiles[0];
    Object.assign(profile, { opening_time: 22, closing_time: 21.6, reference_travel_cm: 110, assigned_to: ["cover.living_one", "cover.kitchen_two"] });
    profile.geometry = { slat_time_s: 3, opening_roll: 1, closing_roll: 1 };
    profile.provenance.opening = evidence(AT(0), { origin_entity_id: "cover.living_one", origin_name: "Living room shutter 1" });
    profile.provenance.closing = evidence(AT(1), { origin_entity_id: "cover.living_one", origin_name: "Living room shutter 1" });
    const rolled = refinedAfter == null ? { source: "unknown", recorded_at: null } : evidence(AT(1 + refinedAfter));
    const [one, two, three] = data.covers;
    Object.assign(one, { profile_id: "tall", travel_cm: 110, precision: refinedAfter == null ? "basic" : "refined", slats_state: "yes_measured",
      effective: { opening: timing(22, profile.provenance.opening), closing: timing(21.6, profile.provenance.closing), slat_time_s: timing(3, evidence(AT(1))),
        opening_roll: timing(1, rolled), closing_roll: timing(1, rolled) } });
    const inherited = evidence(AT(0), { inherited: true, origin_entity_id: "cover.living_one", origin_name: "Living room shutter 1" });
    const scaled = travel !== 110;
    Object.assign(two, { profile_id: "tall", travel_cm: travel, precision: "basic", slats_state: "yes_profile", overrides: {},
      effective: { opening: timing(22 * travel / 110, inherited, "profile", scaled), closing: timing(21.6 * travel / 110, inherited, "profile", scaled),
        slat_time_s: timing(3, inherited, "profile", scaled) } });
    Object.assign(three, { profile_id: null, precision: "none", slats_state: "unknown", overrides: {},
      effective: { opening: { value: 20, origin: "default", provenance: { source: "unknown" } }, closing: { value: 20, origin: "default", provenance: { source: "unknown" } } } });
    delete three.travel_cm;
    return data;
  }
  const row = (context, entity) => context.tab().querySelector(`.cal-row[data-entity="${entity}"]`);
  const card = (context) => context.tab().querySelector(".cal-flow .cal-page:not(.cal-assign-page)");
  const sheet = (context) => context.tab().querySelector(".cal-flow .cal-assign-page");
  async function open(context, entity) {
    row(context, entity).querySelector('[data-action="cal-tab-toggle"]').click();
    await tick(6);
    return card(context);
  }
  async function close(context) { card(context).querySelector(".cal-page-close").click(); await tick(8); }
  /** Home Assistant accepts one cover's assignment. */
  const accepting = (backend) => { backend.hass.callWS = ((call) => async (message) => message.type === "myhome/cover_profiles/write" ? { revision: 8 } : call(message))(backend.hass.callWS); };
  /** The keys reworded or added for «questa tapparella», used by the Calibration tab only (the card's Scollega lines are gone). */
  const CHANGED = ["wiz_T511_here", "wiz_T407_here", "wiz_T430"];

  test("«questa tapparella» in Italian and English; the list's keys keep the name; none of them is used by Profili or Dispositivi", () => {
    assert.deepEqual([it.wiz_T511_here, it.wiz_T407_here], ["Questa tapparella ora segue il profilo «{profile}».", "Profilo scollegato da questa tapparella."]);
    assert.deepEqual([en.wiz_T511_here, en.wiz_T407_here], ["This cover now follows the profile \"{profile}\".", "Profile unlinked from this cover."]);
    assert.deepEqual([it.wiz_T511, it.wiz_T407], ["La tapparella {cover} ora segue il profilo «{profile}».", "Profilo scollegato dalla tapparella {cover}."], "the list's notices, named");
    // The card's Scollega row and its lines are gone; the window says it (wiz_T405).
    assert.deepEqual([it.cal_LINK_unlink_desc_none, it.cal_LINK_unlink_desc_one, it.cal_LINK_unlink_desc_personal], [undefined, undefined, undefined]);
    assert.equal(it.wiz_T430, "Questa tapparella è la sorgente del profilo «{profile}»: per ora il collegamento non si può togliere. Sarà possibile con una prossima versione; i valori misurati resteranno a lei.");
    assert.match(en.wiz_T430, /^This cover is the source of the profile "\{profile\}": /);
    for (const key of CHANGED) for (const table of [it, en]) assert.doesNotMatch(table[key], /\{cover\}/, `${key}: no name, it is in the title above`);
    for (const file of ["panel-cover-profiles.js", "panel-cover-profile-list.js", "panel-profile-catalogue-editor.js", "panel-cover-calibration.js", "panel-profile-presentation.js", "panel-profile-assign.js", "myhome-panel.js"]) {
      const source = readFileSync(new URL(`${PANEL}${file}`, import.meta.url), "utf8");
      for (const key of CHANGED) assert.doesNotMatch(source, new RegExp(`["'\`]${key}["'\`]`), `${file} does not use ${key}`);
    }
  });

  test("on the page the Scollega line and «Cambia profilo»'s source line say «questa tapparella»; after Assegna and Scollega on the card «questa tapparella», on the list the name", async () => {
    phone();
    const context = await mount({ language: "it", overview: family() });
    accepting(context.backend);
    let page = await open(context, "cover.kitchen_two");
    assert.equal(page.querySelector("#cal-page-name").textContent, "Kitchen shutter 2", "the name in the title above");
    page.querySelector('.cal-page-link [data-action="cal-tab-assign"]').click(); await tick(6);
    assert.match(sheet(context).querySelector('input[value="__no_profile__"]').closest(".wiz-radio").querySelector("small").textContent,
      /^Questa tapparella verrà scollegata dal profilo «tall_two_leaf»\. Restano gli eventuali tempi impostati su questa tapparella/);
    context.tab().querySelector('[data-action="cal-tab-assign-close"]').click(); await tick(6);
    if (card(context)) await close(context);
    page = await open(context, "cover.living_one");
    page.querySelector('.cal-page-link [data-action="cal-tab-assign"]').click(); await tick(6);
    assert.equal(sheet(context).querySelector("h2").textContent, "Cambia il profilo della tapparella Living room shutter 1");
    const line = sheet(context).querySelector('input[value="__no_profile__"]').closest(".wiz-radio").querySelector("small").textContent;
    assert.match(line, /^Questa tapparella è la sorgente del profilo «tall_two_leaf»: .*resteranno a lei\.$/, "one form in the sentence");
    context.tab().querySelector('[data-action="cal-tab-assign-close"]').click(); await tick(6);
    if (card(context)) await close(context);
    // Assegna from the card: the card says «questa tapparella», the list behind it the name.
    page = await open(context, "cover.living_three");
    page.querySelector('[data-action="cal-tab-assign"]').click(); await tick(6);
    const choice = sheet(context).querySelector('input[value="tall"]');
    choice.checked = true; choice.dispatchEvent(new dom.window.Event("change", { bubbles: true }));
    sheet(context).querySelector("[data-assign]").click(); await tick(8);
    assert.equal(card(context).querySelector(".cal-page-notice").textContent, "Questa tapparella ora segue il profilo «tall_two_leaf».");
    assert.equal(context.tab().querySelector(".cal-tab-done").textContent.trim(), "La tapparella Living room shutter 3 ora segue il profilo «tall_two_leaf».");
    await close(context);
    // Scollega from the card of a follower: the same.
    page = await open(context, "cover.kitchen_two");
    page.querySelector('.cal-page-link [data-action="cal-tab-assign"]').click(); await tick(6);
    sheet(context).querySelector('input[value="__no_profile__"]').click(); await tick(2);
    sheet(context).querySelector("[data-assign]").click(); await tick(8);
    assert.equal(card(context).querySelector(".cal-page-notice").textContent, "Profilo scollegato da questa tapparella.");
    assert.equal(context.tab().querySelector(".cal-tab-done").textContent.trim(), "Profilo scollegato dalla tapparella Kitchen shutter 2.");
    await close(context);
    // From the list's ⋮ (no name in a title on the list): the list's notice, named.
    row(context, "cover.living_three").querySelector('[data-action="cal-tab-menu"]').click(); await tick();
    row(context, "cover.living_three").querySelector('.cal-menu [data-action="cal-tab-assign"]').click(); await tick(6);
    const again = sheet(context).querySelector('input[value="tall"]');
    again.checked = true; again.dispatchEvent(new dom.window.Event("change", { bubbles: true }));
    sheet(context).querySelector("[data-assign]").click(); await tick(8);
    assert.equal(card(context), null, "back on the list");
    assert.equal(context.tab().querySelector(".cal-tab-done").textContent.trim(), "La tapparella Living room shutter 3 ora segue il profilo «tall_two_leaf».");
  });
});

describe("the source in «Profilo usato anche da»", async () => {
  const PANEL = "../../custom_components/myhome/frontend/panel/";
  const { translations } = await import(`${PANEL}panel-translations.js`);
  const it = translations.it, en = translations.en;

  test("«Profilo usato anche da»: the source «ha misurato il profilo» (its title «Ha misurato il profilo»), Italian and English", () => {
    assert.deepEqual([it.calTabUserSource, it.calTabUserSourceTitle], ["ha misurato il profilo", "Ha misurato il profilo"]);
    assert.deepEqual([en.calTabUserSource, en.calTabUserSourceTitle], ["measured the profile", "Measured the profile"]);
  });
});
