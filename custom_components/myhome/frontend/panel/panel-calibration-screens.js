/**
 * Which screen of the calibration mockups (A-xx, B-xx, C-xx, S-xx) a moment of the wizard is.
 *
 * Pure: from the wizard's local choices and the session view it returns plain data (texts already
 * filled, fields, buttons, stepper), and the wizard only draws it. Text keys are those of the
 * shared translation sheet (`wiz_T…`); a few screen-only keys are `wiz…`.
 */
const asset = (name) => { const url = new URL(name, import.meta.url); url.search = new URL(import.meta.url).search; return url.href; };
const { fill, number, fixed, readingRange, RANGE_SLACK_CM, dateText, shortDate, followedBy, gapTenth, gapPasses, gapText, plural, REFINE_ICON, advisedStep, coverRole } = await import(asset("panel-calibration-text.js"));

/** The stages before the path is known (the preamble): no stepper there. */
const PREAMBLE = ["choose", "path", "profile", "current", "case"];

const PHASES = {
  intro: "wiz_T007", prep: "wiz_T008", ascent: "wiz_T009", descent: "wiz_T010", readings: "wiz_T011", check: "wiz_T556", summary: "wiz_T012", finetune: "wiz_T013",
  // The basic calibration's lift-off run («Lamelle»).
  slats: "wiz_T656",
  // A-35: giving the profile to other covers. Not a step (the steps end with the summary): the
  // name of the epilogue in the header only («Calibra · Altre tapparelle»).
  assign: "wiz_T614",
};

/** The phases each path shows, in order (C depends on what is remeasured). */
export function phasesFor(path, scope, refining, previews = true, slats = false) {
  if (path === "B") return ["intro", "readings", "summary"];
  // Fine-tuning on the times already saved: the tape readings (the runs to the end stops
  // time nothing), the check and the summary; no ascent or descent of its own.
  if (path === "R") return ["intro", "prep", "readings", "check", "summary"];
  // The basic calibration, Home Assistant's guided session: five steps; with slats a sixth, «Lamelle», the lift-off
  // run of its own after the descent.
  if (path === "base") return ["intro", "prep", "ascent", "descent", ...(slats ? ["slats"] : []), "summary"];
  if (path === "C" && scope === "times_only") return ["intro", "prep", "ascent", "descent", "summary"];
  if (path === "C" && scope === "points_only") return ["intro", "prep", "readings", "summary"];
  // Fine-tuning's own summary is part of «Perfezionamento» (the last step), «Riepilogo» once. The base
  // calibration's check is a step of its own, between the readings and the summary (fine-tuning has its own check).
  if (refining) return ["intro", "prep", "ascent", "descent", "readings", "summary", "finetune"];
  // Fine-tuning is a step only while it can be run (the preview paths on).
  return ["intro", "prep", "ascent", "descent", "readings", "check", "summary", ...(previews ? ["finetune"] : [])];
}

const MEASURED_RUN = (view) => view && ((view.mode === "geometry" && ["opening", "closing"].includes(view.phase) && ["lift", "opening", "closing"].includes(view.step))
  || (view.mode === "guided" && ["opening", "closing", "lifting"].includes(view.phase)));

/**
 * `motor_stopped` (from the backend): in a run the user confirms (the closings to the base `home` and
 * `reset`, the full ascent and descent, the return to the top, the guided runs) the motor has stopped (a stop frame
 * from the bus, real or the actuator's own timer) and the session waits for the confirmation; `stopped_elapsed` is
 * the run's time at that frame. Without the field the run counts as moving.
 */
const MOTOR_STOPPED = (view) => view?.motor_stopped === true;
/** A stop frame between these times is an actuator's timer (about 60 s), never the real end of the run. */
export const TIMER_STOP_SECONDS = [59, 65];
/** Why a full run is not valid: stopped at the actuator's timer, or by anything else before ②. */
export const stopVerdict = (seconds) => seconds >= TIMER_STOP_SECONDS[0] && seconds <= TIMER_STOP_SECONDS[1] ? "timer" : "late";
/**
 * ② exists only while the motor runs. A full run whose motor stopped before ② was pressed in this tab (the
 * actuator's timer, a wall switch, any stop) is not valid at once, in the live view and on a resumed one: its result
 * is struck through and only «Ripeti la corsa» goes on. A press of ② made before the stop reached the tab is in flight
 * and decides by itself (`local.fullRun.pressed`).
 */
const STOPPED_BEFORE_PRESS = (view, local) => MOTOR_STOPPED(view) && view.stopped_elapsed != null && !local?.fullRun?.pressed;
const invalidNotice = (t, reason) => ({ tone: "error", result: true, text: t(reason === "timer" ? "wiz_T445" : "wiz_T444") });

/**
 * A cover whose rolls were measured with the tape (Home Assistant's derived `precision`: `refined`, or
 * `verified`, which only a fine-tuning gives). On it the basic calibration measures the times again and nothing else:
 * no slats question, no lift-off run, saved on the cover (never as a new profile, which would drop the rolls).
 */
export const refinedCover = (cover) => ["refined", "verified"].includes(cover?.precision);
/**
 * The slats answer known from what is saved, never from a session's memory: a slat time saved
 * (a profile's geometry with a slat phase) is «with slats», none (no geometry, or 0 s) «without»; a cover never
 * calibrated has no answer (null: the question keeps its default, «with slats»).
 */
export const savedSlats = (cover) => {
  // What Home Assistant knows of the cover's slats (measured, from its profile, declared);
  // then what is saved.
  const state = cover?.slats_state;
  if (["yes_measured", "yes_profile", "yes"].includes(state)) return true;
  if (["no_measured", "no_profile", "no"].includes(state)) return false;
  // «unknown» from Home Assistant is unknown: no slat time in use (times saved on the cover alone, a
  // profile without geometry) says nothing about slats, so no «No» is deduced from it (the question keeps «Sì»).
  if (state) return null;
  return !cover || cover.status === "to_measure" ? null : cover.effective?.slat_time_s?.value > 0;
};
/**
 * The slats question only when what is known does not answer it. A slat time measured on this cover
 * (> 0 or 0) or the card's declaration of a cover without a profile answers it: nothing is asked and no step is added;
 * a cover that follows a profile has its profile's answer, asked only on «Ricalibra» (the base calibration),
 * preset with it (one tap to confirm or correct). Nobody said: asked, preselected from what is saved («with slats»
 * otherwise). `local.slatsKnown` is the answer of the base calibration just saved in this frame, before the list has
 * read it again (why the question came back right after a base calibration).
 */
export function slatsQuestion(cover, path, local = {}) {
  if (local.slatsKnown != null) return { ask: false, value: local.slatsKnown };
  const state = cover?.slats_state;
  if (["yes_measured", "yes"].includes(state)) return { ask: false, value: true };
  if (["no_measured", "no"].includes(state)) return { ask: false, value: false };
  if (["yes_profile", "no_profile"].includes(state)) return { ask: path === "base", value: state === "yes_profile" };
  return { ask: true, value: savedSlats(cover) };
}

/**
 * The fine-tuning's briefing says the readings of this cover (four with slats, three without; both while
 * it is not known) and that the saved times stay, the slat time aside: the lift-off's reading can refine it.
 */
export const fineTuneBrief = (slats) => slats === true ? "wiz_T700" : slats === false ? "wiz_T701" : "wiz_T684";

/** Path B («Come una tapparella già misurata») hidden everywhere; its code stays for a later version. */
export const PATH_B_SHOWN = false;

/** A time left as minutes and seconds («1:43»), rounded up to the second. */
export const clock = (ms) => { const total = Math.max(0, Math.ceil(ms / 1000)); return `${Math.floor(total / 60)}:${String(total % 60).padStart(2, "0")}`; };

/**
 * Movements in progress or about to start: Stop is the action that matters. The runs back the session
 * makes by itself are all here: `reclosing`, `reopening` and `rehoming` (the close before the first
 * ascent and before a repeated one), from the Stop written before them to their end stop.
 */
export const MOVING = (view) => !!view && !MOTOR_STOPPED(view)
  && (["opening", "closing", "geometry_wait_stop", "positioning", "lifting", "reclosing", "reopening", "rehoming"].includes(view.phase)
    || view.phase?.startsWith("starting_"));
/**
 * THE rule for the red Stop, for the owner and the reader alike: a movement is in progress (the session's run
 * phase, until the bus reports the motor stopped), one has just been asked for (`status.starting`: ① or another start
 * sent, the session's answer not back yet), or an automatic cycle goes on by itself. At rest (briefings before ①,
 * readings with the tape, confirmations after the stop, choices, summaries) there is none: pressed there it would only
 * discard the run.
 */
export const IN_MOTION = (view, status = {}) => MOVING(view) || !!status.starting
  || (!!view && ["settling", "between_covers"].includes(view.phase));

const ENDED = ["interrupted", "cancelled", "expired"];
/** Refusals told in our own words rather than with the maintainer's `profileError_*` texts. */
const OUR_REFUSALS = { reading_over_travel: "wiz_T531", invalid_check: "wiz_T575" };
/** How long before the session's idle expiry the wizard warns. */
export const LEASE_WARNING_MS = 120000;

/** Texts with a count: the zero and one forms of the sheet's plural texts (rows T305–T317; T307 and T310–T313 retired with T112, T285 and T286). */
const PLURALS = { wiz_T110: ["wiz_T305", "wiz_T306"], wiz_T165: ["wiz_T308", "wiz_T309"],
  wiz_T287: ["wiz_T314", "wiz_T315"], wiz_T288: ["wiz_T316", "wiz_T317"] };
/** The covers that follow the profile, without the one it was measured on (it follows nothing). */
const trueFollowers = (cover) => Math.max(0, (cover?.followers?.length ?? 0) - (cover?.sourceAmong ? 1 : 0));

export function counted(t, key, count, values = {}) {
  const [zero, one] = PLURALS[key] || [];
  return fill(t(count === 0 && zero ? zero : count === 1 && one ? one : key), { ...values, count });
}

/** "Measured on…" in the middle of a line: "measured on…" (the language's own lower case). */
function lowerFirst(text, language) {
  return text ? text[0].toLocaleLowerCase(language) + text.slice(1) : text;
}

/** A-02's box on the chosen cover: the profile it follows, its calibration, or that it was never measured. */
function pathContext(t, cover, language) {
  if (!cover) return "";
  // On the cover the profile was measured on (the panel's rule: its `provenance.opening.origin_entity_id`
  // is this cover) the profile is its own, not one it follows. Its name is in A-02's bar: «questa tapparella».
  if (cover.profile?.name && !!cover.entity_id && cover.profile.provenance?.opening?.origin_entity_id === cover.entity_id) {
    return fill(t("wiz_A02_source"), { profile: cover.profile.name });
  }
  // Without a measured travel the profile's values are not scaled, and the line says so.
  if (cover.profile?.name) return fill(t(cover.travel_cm > 0 ? "wiz_T395" : "wiz_T455"), { profile: cover.profile.name });
  if (cover.status && cover.status !== "to_measure") {
    // Inside «already calibrated (…)» the detail of the status, not the badge (the badge says «Calibrated (…)»).
    return fill(t("wiz_T399"), { status: t(`calTabStatusDetail_${cover.detail}`), date: dateText(cover.last, language) || "—" });
  }
  return t("wiz_T418");
}

/**
 * The paths that only run as previews until their backend lands: path B (a profile verified with the tape), path C's
 * «tape readings only» and the fine-tuning offered after a base calibration. Off: they stay visible on A-02 and C-02,
 * disabled with «Disponibile in una prossima versione», never advised nor preselected, and A-34 offers no fine-tuning.
 * For development they can be switched on in one browser, until it is reloaded without it, with the query parameter
 * `calibration_preview=1` on the panel's address or the local storage key `myhome-calibration-preview` set to "1".
 */
export const PREVIEW_PATHS = false;
export function previewPathsEnabled() {
  try { if (new URLSearchParams(globalThis.location?.search || "").get("calibration_preview") === "1") return true; } catch { /* no address */ }
  try { if (globalThis.localStorage?.getItem("myhome-calibration-preview") === "1") return true; } catch { /* storage off */ }
  return PREVIEW_PATHS;
}

/** Profiles whose reference travel is within this share of the cover's saved travel count as "the same size". */
export const SIMILAR_TRAVEL = 0.05;

/**
 * A-02's advice (the advised option follows the list, below). The maintainer asked on #10 for «the recommended option from the first wizard screen»: the basic
 * calibration, on every cover, never measured, calibrated or fine-tuned (where it measures the times only). The
 * profiles with a similar travel (within ±5 % of the cover's saved travel, the closest first) are still worked out
 * whenever the travel is known: they head the list under «Come una tapparella già misurata», marked, and B-01
 * preselects the closest; they no longer change the advice.
 */
export function pathAdvice(cover, profiles = []) {
  const travel = cover?.travel_cm;
  const similar = travel > 0 ? profiles.filter((profile) => profile.referenceTravel > 0 && Math.abs(profile.referenceTravel - travel) <= travel * SIMILAR_TRAVEL)
    .sort((a, b) => Math.abs(a.referenceTravel - travel) - Math.abs(b.referenceTravel - travel)) : [];
  // There is always exactly one advised option, badge and preselection included: the one the list
  // advises (`advisedStep`, the rule of `_rowActions`): fine-tuning (`R`, Perfeziona's procedure) on the profile's source
  // and on a cover without a profile, the basic calibration where the list advises Ricalibra and on a follower.
  const advised = cover ? advisedStep(cover).path : "base";
  return { badge: advised, hint: advised, similar };
}

/** The wizard's whole state as one screen. */
export function buildScreen(ctx) {
  // `previews`: the wizard passes `previewPathsEnabled()`; a screen built alone shows them.
  const { t, language = "en", path = "A", scope = null, stage, local = {}, cover, view, status = {}, preview = false, previews = true } = ctx;
  const n = (value, digits = 1) => number(value, language, digits);
  n.fixed = (value, digits = 1) => fixed(value, language, digits);
  const tx = (key, values) => fill(t(key), values);
  const name = cover?.name || "";
  const values = { cover: name, profile: cover?.profile?.name ?? "" };
  // Before the case is confirmed (C-02) the caption follows the case chosen so far, the preselected «tape readings
  // only» at first (the stepper itself waits for the path).
  const stepScope = scope ?? (path === "C" && stage === "case" && previews ? "points_only" : null);
  const screen = {
    code: "", header: { title: name || t("wiz_T001"), subtitle: cover?.address || "", units: cover?.units || null }, discard: true, preview,
    title: "", subline: "", body: "", help: null, note: null, results: null, stopwatch: null, wait: null, illustration: null, choice: null, picker: null, field: null, box: null, table: null,
    technical: null, profileBlock: null, saveInfo: null, precision: null, status: [], notices: [], actions: [], links: [], readOnly: null, phase: "intro",
    caption: caption(t, path, stepScope, cover), stepScope,
    // Before any session the header offers a way back to the previous choice (kept as it was).
    back: ["choose", "path", "profile", "current", "case", "before"].includes(stage),
  };
  const stop = { id: "stop", label: t("wiz_T028"), kind: "stop" };
  const finish = () => finalize(screen, ctx);

  // ---- Before any session: choices that stay in the browser. ----
  if (stage === "choose") {
    // The covers as a list with one radio each (the list's filters and order); Continue
    // waits for a choice. The chosen row opens and says what choosing it means.
    const picked = local.coverPicked ? (ctx.covers || []).find((item) => item.key === local.coverKey) : null;
    // The chosen row carries no note: A-02 says that a cover is already calibrated (its yellow
    // box) and which profile it follows (its caption); the row's chips already show both.
    const notes = [];
    Object.assign(screen, { code: "A-01", phase: "intro", title: t("wiz_T003"), body: t("wiz_T004"),
      picker: { selected: picked ? local.coverKey : "", filter: local.pickFilter || "all", notes },
      actions: [{ id: "choose", label: t("wiz_T006"), kind: "primary", disabled: !ctx.covers?.length || !picked }] });
    // Until a cover is picked the subtitle says so; then its room and address (its name is in the chosen row, not
    // repeated here).
    screen.header = picked ? { title: t("wiz_T001"), subtitle: cover?.address || "", units: cover?.units || null }
      : { title: t("wiz_T001"), subtitle: t("wiz_T392"), placeholder: true };
    return finish();
  }
  if (stage === "path") {
    // A radio list (A-02 Section A) with Continue in the actions; the advised path carries a badge.
    const list = ctx.profiles || [], profiles = list.length, measured = !!cover?.status && cover.status !== "to_measure";
    const advice = pathAdvice(cover, list);
    const refined = refinedCover(cover);
    // A path is named by its option's own label, never by a letter. The basic calibration (advised and
    // preselected as the list advises), fine-tuning with the tape (today's path A, whole) and path B; path C's choice is folded
    // in here (C-01 and C-02 are no longer entered).
    const label = { base: t("wiz_T632"), A: t("wiz_T634"), B: t("wiz_T018") };
    // Under Ⓑ: how many profiles exist; each profile (its slats, its reference travel) is listed on demand.
    // «Con lamelle · Corsa 197 cm» (wiz_T611; «Cambia profilo»'s rows keep their «corsa di riferimento»).
    const describe = (profile) => ({ name: profile.name, similar: advice.similar.includes(profile),
      details: [t(profile.slat == null ? "wiz_T370" : profile.slat > 0 ? "wiz_T368" : "wiz_T369"),
        ...(profile.referenceTravel > 0 ? [fill(t("wiz_T611"), { cm: n(profile.referenceTravel, 0) })] : [])].join(" · ") });
    // Each option carries its explanation as the first line of its note, then what applies to this cover. With
    // profiles, their count is the disclosure's own label, no line of its own under the note.
    const bNote = !profiles ? [t("wiz_T453"), t("wiz_T401")] : [t("wiz_T453")];
    // The whole list of profiles (slats, reference travel) only on demand, under its disclosure
    // closed by default: information only, no profile is chosen from there. On every cover,
    // measured or not; the profiles with a similar travel first (the closest first), marked «corsa simile».
    // The disclosure says «N profili disponibili ⌄».
    const bMore = profiles ? { label: profiles === 1 ? t("wiz_T419") : fill(t("wiz_T420"), { count: profiles }), similarLabel: t("wiz_T610"),
      items: [...advice.similar, ...list.filter((profile) => !advice.similar.includes(profile))].map(describe) } : null;
    // Each option says what is done, how long in relative terms and what comes of it; no percentage and
    // no centimetre promised. On a cover already fine-tuned the basic calibration measures the times only (T636).
    // The choice is offered only on a cover already calibrated, from «Calibra una
    // tapparella»: «Ricalibra» (everything from scratch, as Calibra) and «Perfezionamento con il metro» (tape readings and
    // a check on the times already saved, no run timed again). Path B is hidden (PATH_B_SHOWN), never «non disponibile».
    void refined; void label;
    const options = [
      // No icons in the choice; they stay on the action buttons.
      { value: "base", label: t("calTabActionRemeasure"), note: [t("wiz_T665")] },
      { value: "R", label: t("wiz_T634"), note: [t("wiz_T666")] },
      ...(PATH_B_SHOWN ? [{ value: "B", label: t("wiz_T018"), disabled: !profiles || !previews, note: bNote, ...(bMore ? { more: bMore } : {}) }] : []),
    ].map((option) => ({ ...option, badge: option.value === advice.badge && !option.disabled ? t("wiz_T400") : "" }));
    // Preselected on the badge, Continue is ready.
    const enabled = (value) => options.some((option) => option.value === value && !option.disabled);
    const chosen = enabled(local.pathChoice) ? local.pathChoice : enabled(advice.badge) ? advice.badge : "";
    // On a calibrated cover path B says what it replaces, under the option (a preview: only with the previews on).
    if (measured && chosen === "B") options.find((item) => item.value === "B").warning = t(cover?.profile ? "wiz_T416" : "wiz_T417");
    // Why the advised option is advised, in one impersonal sentence: T630, or T631 on a cover fine-tuned.
    // What the two ways do, in one paragraph; no count of the paths that cannot be used (B is hidden).
    // On a cover that follows a profile (the panel's rule, `coverRole`) the times are the profile's, not
    // its own. The box above already names the profile, so the paragraph only says what the two ways start from.
    // The source and a cover without a profile keep T667.
    const role = cover?.role ?? coverRole(cover?.status, cover?.profile, cover?.entity_id);
    const body = role === "follower" && cover?.profile ? t("wiz_A02_follower") : t("wiz_T667");
    Object.assign(screen, { code: "A-02", phase: "intro", title: tx("wiz_T015", { name }), subline: pathContext(t, cover, language), body,
      choice: { name: "path", kind: "radio", label: tx("wiz_T015", { name }), legendHidden: true, value: chosen, options },
      actions: [{ id: "path", label: t("wiz_T006"), kind: "primary", disabled: !chosen }],
      // A choice made (or preselected) shows its path in the stepper, «Scelta del percorso» its current step.
      stepPath: chosen || path, stepChoice: !!chosen });
    return finish();
  }
  if (stage === "profile") {
    // The profile chosen (preselected: the closest by travel, A-02's); without one, nothing until a choice.
    const chosen = ctx.profiles?.find((item) => item.id === local.profileId) || null;
    // One radio row per profile: "tall_two_leaf · measured on Living room shutter 1", then who follows it by name.
    const about = (item) => fill(t("wiz_T165").split(" · ")[0], { cover: item.measuredOn || "—" });
    // Path B runs only as a preview: B-01 says so under its button, as B-02 does.
    Object.assign(screen, { code: "B-01", phase: "intro", title: t("wiz_T161"), body: t("wiz_T162"),
      choice: { name: "profile", kind: "radio", label: t("wiz_T163"), value: chosen?.id ?? "",
        // The profile the cover follows now is marked «attuale» and stays selectable (to verify it again).
        options: (ctx.profiles || []).map((item) => ({ value: item.id, label: `${item.name} · ${lowerFirst(about(item), language)}`, plain: true,
          badge: cover?.profile?.id && item.id === cover.profile.id ? t("wiz_T555") : "",
          note: [followedBy(t, item.followerNames || [], language)] })) },
      actions: [{ id: "profile", label: t("wiz_T006"), kind: "primary", disabled: !chosen, note: t("wiz_T318") }], stepChoice: !!chosen });
    return finish();
  }
  if (stage === "current") {
    const effective = cover?.effective || {};
    Object.assign(screen, { code: "C-01", phase: "intro", title: t("wiz_T184"),
      // With no measured travel the first paragraph says the values are the profile's, not scaled.
      body: !cover?.profile ? tx("wizNoProfileBody", values) : cover.travel_cm > 0 ? tx("wiz_T185", { ...values, travel: n(cover.travel_cm) })
        : [tx("wiz_T455", values), ...tx("wiz_T185", { ...values, travel: "" }).split(/\n\s*\n/).slice(1)].join("\n\n"),
      // «In uso attualmente» over a card of label/value rows, as the details' «Valori in uso».
      // The glossary's labels in the one order of these values (times, slats, travel, coefficients, profile);
      // A value not known is «—»; a travel taken from the profile says so.
      box: { label: t("wiz_T186"), rows: valueRows(t, n, { opening: effective.opening?.value, closing: effective.closing?.value, slat: effective.slat_time_s?.value,
        travel: cover?.travel_cm, reference: cover?.profile?.reference_travel_cm, opening_roll: effective.opening_roll?.value, closing_roll: effective.closing_roll?.value,
        profile: cover?.profile?.name }) },
      actions: [{ id: "current", label: t("wiz_T006"), kind: "primary" }] });
    return finish();
  }
  if (stage === "case") {
    // The tape readings only (the fine-tuning offered at the end of a base calibration) are recommended and
    // preselected, with a line saying why. No cover is fine-tuned yet (fine-tuning is a preview until its backend lands),
    // so the line always applies.
    // Without the previews the tape readings only stay visible, disabled, and nothing is advised or chosen.
    const value = previews ? local.scope || "points_only" : local.scope && local.scope !== "points_only" ? local.scope : "";
    Object.assign(screen, { code: "C-02", phase: "intro", title: t("wiz_T189"), body: previews ? `${t("wiz_T190")}\n\n${t("wiz_T548")}` : t("wiz_T190"),
      choice: { name: "case", kind: "radio", label: t("wiz_T191"), value,
        options: [previews ? { value: "points_only", label: t("wiz_T193"), badge: t("wiz_T400") } : { value: "points_only", label: t("wiz_T193"), disabled: true, notice: t("wiz_T584"), noticeTone: "info" },
          { value: "times_only", label: t("wiz_T194") }, { value: "times_and_rolls", label: t("wiz_T196") }] },
      actions: [{ id: "case", label: t("wiz_T006"), kind: "primary", disabled: !value }], stepChoice: !!value });
    return finish();
  }
  if (stage === "before") {
    // A start refused because the cover moved: its refusal, above the buttons; «Ho capito, inizia» tries again.
    if (local.startError) screen.notices.push({ tone: "error", text: t(`profileError_${local.startError.code}`) });
    const pointsOnly = path === "C" && scope === "points_only", timesOnly = path === "C" && scope === "times_only";
    const bModel = path === "B";
    if (bModel) {
      Object.assign(screen, { code: "B-02", phase: "readings", title: t("wiz_T167"), body: t("wiz_T168"),
        actions: [{ id: "begin", label: t("wiz_T076"), kind: "primary", note: t("wiz_T318") }] });
      return finish();
    }
    // The answer known from the last calibration is preselected (0 s of slats: without), «with slats» otherwise.
    // Asked only when what is known does not answer it (`slatsQuestion`).
    const question = slatsQuestion(cover, path, local);
    const slatsChoice = !question.ask ? null : { name: "slats", kind: "radio", label: t("wiz_T023"),
      value: (local.slats ?? question.value ?? true) === false ? "no" : "yes",
      // How to tell, closed by default, right after the question.
      help: { summary: t("wiz_T456"), text: t("wiz_T457") },
      options: [{ value: "yes", label: t("wiz_T198") }, { value: "no", label: t("wiz_T199") }] };
    if (path === "base") {
      // C-35 of the basic calibration, with the slats question (the tap at the lift-off only with
      // slats). On a cover fine-tuned with the tape it measures the times only: today's «solo i tempi» briefing, no
      // question (the slat time and the rolls stay).
      // Ricalibra is the basic calibration from scratch on every cover, fine-tuned ones too
      // (their rolls stay): the slats question always, its answer from what is saved.
      const keep = false;
      Object.assign(screen, { code: "C-35", phase: "prep", title: t("wiz_T021"),
        body: keep ? tx(cover?.travel_cm > 0 ? "wiz_T230" : "wiz_T489", { ...values, travel: n(cover?.travel_cm) }) : t("wiz_T637"),
        choice: keep ? null : slatsChoice, actions: [{ id: "begin", label: t("wiz_T024"), kind: "primary" }] });
      return finish();
    }
    Object.assign(screen, {
      code: pointsOnly ? "C-37" : timesOnly ? "C-35" : path === "C" ? "C-03" : "A-03", phase: "prep", title: t("wiz_T021"),
      // Never «— cm» inside a sentence: without a known travel the variant that does not name it.
      body: pointsOnly ? tx(cover?.travel_cm > 0 ? "wiz_T235" : "wiz_T490", { ...values, travel: n(cover?.travel_cm) })
        : timesOnly ? tx(cover?.travel_cm > 0 ? "wiz_T230" : "wiz_T489", { ...values, travel: n(cover?.travel_cm) })
        : path === "C" ? tx("wiz_T197", values) : local.keepTimes ? t(fineTuneBrief(local.slats ?? (question.ask ? null : question.value))) : t("wiz_T022"),
      // Guided times only has no lift-off: the backend does not use the slat choice there.
      choice: pointsOnly || timesOnly ? null : slatsChoice,
      // Times only has no slats question and says nothing about slats.
      help: null,
      // Readings only run as a preview: said before it starts.
      // No "Cancel" (T025) here or on A-16: it did what the header's "Abandon the calibration" does.
      actions: [{ id: "begin", label: t("wiz_T024"), kind: "primary", ...(pointsOnly ? { note: t("wiz_T318") } : {}) }] });
    return finish();
  }
  if (stage === "saved") return finish(savedScreen(screen, ctx, tx, values));
  if (stage === "assign") return finish(assignScreen(screen, ctx));
  // A reader whose session was cancelled where it was guided: its own end, with «Chiudi» only.
  if (stage === "cancelled") return finish(Object.assign(screen, { code: "S-01", phase: screen.phase, title: t("wiz_T542"), body: t("wiz_T543"),
    discard: false, actions: [{ id: "close", label: t("wiz_T390"), kind: "primary" }] }));
  // The restarted session was gone by the time it was followed (its lease ran out): said as such.
  if (stage === "restart-expired") return finish(Object.assign(screen, { code: "S-06", phase: screen.phase, title: t("wiz_T259"), body: t("wiz_T260"),
    discard: false, actions: [{ id: "close", label: t("wiz_T390"), kind: "primary" }] }));
  // The device that guided it started again on this cover: no dead end, the new session can be followed.
  if (stage === "restarted") return finish(Object.assign(screen, { code: "S-01r", phase: screen.phase, title: t("wiz_T582"), body: t("wiz_T583"),
    discard: false, actions: [{ id: "follow-new", label: t("wiz_T579"), kind: "primary" }, { id: "close", label: t("wiz_T390"), kind: "secondary" }] }));

  // Before a times-only measurement the cover is closed by Home Assistant, as path A's first positioning (C-04).
  if (local.homing && !view) {
    // The bar runs to the instant the panel writes the Stop (the estimate and its margin).
    const seconds = homeStopAt(local.homing.seconds), done = Math.min(seconds ?? 0, Math.max(0, ((ctx.now ?? Date.now()) - local.homing.started) / 1000));
    Object.assign(screen, { code: "C-04", phase: "prep", title: t("wiz_T027"),
      // The bar ends when the close counts as done (at the Stop).
      wait: { text: tx("wiz_T029", values), stages: [], progress: seconds > 0 ? { value: done / seconds, remaining: done >= seconds ? null : Math.max(1, Math.ceil(seconds - done - 0.001)) } : { value: null, remaining: null } },
      actions: [{ id: "stop", label: t("wiz_T028"), kind: "stop" }] });
    return finish();
  }
  // ---- A session (live or preview). ----
  if (!view) {
    Object.assign(screen, { code: "loading", title: t("loading"), actions: [] });
    return finish();
  }
  // An ended session keeps the step it reached in the stepper, marked as interrupted.
  const ended = (reason) => { problemScreen(screen, ctx, reason); screen.interrupted = true; screen.phase = local.reachedPhase || screen.phase; return finish(); };
  if (status.expired || view.reason === "expired") return ended("expired");
  if (ENDED.includes(view.phase)) return ended(view.reason || "stopped");
  if (view.problem) return finish(problemScreen(screen, ctx, view.problem.reason || view.problem, true));
  if (view.phase === "paused") {
    Object.assign(screen, { code: "paused", phase: screen.phase, title: t("calPhase_paused"), body: t("calRecoveryHelp"),
      actions: [{ id: "continue", label: t("calContinue"), kind: "primary" }, stop] });
    return finish();
  }

  if (view.preview) return finish(previewScreen(screen, ctx, tx, n, values, stop));
  if (view.mode === "guided") return finish(guidedScreen(screen, ctx, tx, n, values, stop));
  return finish(geometryScreen(screen, ctx, tx, n, values, stop));
}

/**
 * The values a cover uses, in ONE order and with the glossary's labels wherever they are listed
 * (C-01's «In uso attualmente»; the details' «Valori in uso» takes the rows after the two times): «Tempo di salita», «Tempo di
 * discesa», «Tempo lamelle» («Nessuna lamella» without), «Corsa del telo» («· dal profilo» when it is the profile's
 * reference, never measured here), «Coefficiente in salita», «Coefficiente in discesa» (no unit), «Profilo».
 */
export function valueRows(t, n, { opening, closing, slat, travel, reference, opening_roll, closing_roll, profile } = {}, { times = true, withProfile = true, unmeasured = false } = {}) {
  const seconds = (value) => value == null ? "—" : `${n.fixed ? n.fixed(value) : n(value)} s`;
  // T646: the coefficients of a cover calibrated without the tape are «Non misurato», not «—».
  const roll = (value) => value != null ? n(value, 2) : unmeasured ? t("wiz_T646") : "—";
  return [
    ...(times ? [[t("wiz_T093"), seconds(opening)], [t("wiz_T094"), seconds(closing)]] : []),
    [t("wiz_T095"), slat == null ? "—" : slat === 0 ? t("calTabSlatsNone") : seconds(slat)],
    [t("wiz_T041"), travelText(t, n, travel, reference)],
    [t("wiz_T105"), roll(opening_roll)],
    [t("wiz_T106"), roll(closing_roll)],
    ...(withProfile ? [[t("calTabColProfile"), profile ?? "—"]] : []),
  ];
}

/** A cover's travel, measured (its own), or presumed from its profile's reference («110 cm · dal profilo»). */
export function travelText(t, n, travel, reference) {
  if (travel != null) return `${n(travel)} cm`;
  return reference > 0 ? `${n(reference)} cm · ${t("calTabFromProfile")}` : "—";
}

function caption(t, path, scope, cover) {
  // The processes «Base» and «Col metro» (fine-tuning with the tape, today's path A).
  if (path === "base") return t("wiz_T649");
  if (path === "B") return fill(t("wiz_T169"), { profile: cover?.followProfile ?? cover?.profile?.name ?? "" });
  if (path === "C") return t(scope === "times_only" ? "wiz_T231" : scope === "points_only" ? "wiz_T236" : scope === "times_and_rolls" ? "wiz_T200" : "wiz_T114");
  return t("wiz_T650");
}

/** Live `geometry` session: path A and path C (times and rolls). */
function geometryScreen(screen, ctx, tx, n, values, stop) {
  const { t, view, local = {}, path } = ctx;
  const p = (number) => `${path === "C" ? "C" : "A"}-${number}`;
  const slats = view.slats !== false;
  const elapsed = ctx.elapsed;
  const step = view.step, phase = view.phase;
  // A positioning run lasts about the full time of its direction (the time in use, the integration's default too).
  // A wait may lead with its own bold line (`lead`), the text then a grey note under it.
  const positioning = (number, text, seconds = null, lead = "") => Object.assign(screen, { code: p(number), title: t("wiz_T027"),
    wait: { lead, text, stages: [], progress: waitBar(view, elapsed, seconds) }, actions: [stop] });
  // A positioning only runs the part of the travel still ahead: the full time of its direction × that share, from the
  // cover's estimated position when the run started (100 = open). Already there: a short run until the bus confirms.
  // Unknown position: the full time. The estimate is only as good as the times in use, not yet measured here.
  const toRun = (full, closing) => {
    if (!(full > 0)) return null;
    const position = ctx.runPosition;
    if (position == null) return full;
    const share = Math.min(100, Math.max(0, closing ? position : 100 - position)) / 100;
    return share === 0 ? ALREADY_THERE_SECONDS : full * share;
  };
  const closingTime = toRun(timeInUse(ctx.cover?.effective?.closing), true);
  // A return inside the session lasts what the session itself measured, with a
  // margin (`sessionReturn`), never the whole run: the return to the bottom after the lift-off run lasts that run (the
  // closing and opening times' ratio applied), unless the ascent was measured (a repeated ascent: from the top, as before).
  const ratio = timeInUse(ctx.cover?.effective?.closing) && timeInUse(ctx.cover?.effective?.opening)
    ? timeInUse(ctx.cover.effective.closing) / timeInUse(ctx.cover.effective.opening) : 1;
  const liftRun = view.samples?.lift ?? local.liftRun;
  const resetTime = !(view.values?.opening_time > 0) && liftRun > 0 ? sessionReturn(liftRun * ratio, closingTime) : closingTime;
  // What was just measured, as tiles above the text: a small label, a big value, its unit smaller.
  // A time always carries one decimal, in the tiles too («23,0 s», «1,0 s»).
  // The lift-off as two «Misure», its time and how far the edge rose (when read).
  const liftTiles = () => view.samples?.lift == null ? [] : [{ label: t("wiz_T602"), value: n.fixed(view.samples.lift), unit: "s" },
    ...(view.readings?.gap == null ? [] : [{ label: t("wiz_T603"), value: n(view.readings.gap), unit: "cm" }])];
  // A measured time is labelled by the glossary («Tempo di salita», «Tempo di discesa»), as in the details and A-21.
  const timeTile = (key, seconds) => seconds == null ? null : { label: t(key), value: n.fixed(seconds), unit: "s" };
  // The return to the top after the half-ascent reading starts where that run stopped, ((lift) + ascent) / 2 s up
  // from the bottom (A-17): what is left is the rest of the measured ascent, known to the session. Home Assistant's
  // position is not needed there (it is estimated with the times in use, and a session never confirms it before a
  // reading); elsewhere it gives the share of the travel still ahead.
  // From the half-ascent reading the rest of the measured ascent, before a repeated descent the whole
  // measured ascent (the cover is at the bottom), each with the margin of a return; elsewhere the share still ahead.
  const halfRest = !view.check && view.readings?.half_open != null && view.values?.opening_time > 0
    ? sessionReturn(view.values.opening_time - ((slats ? view.samples?.lift || 0 : 0) + view.values.opening_time) / 2, view.values.opening_time) : null;
  const readingsReturn = local.ackReadings || view.readings?.half_open != null || !!view.check;
  const topTime = step === "top" && halfRest != null ? halfRest
    : step === "top" && !readingsReturn && view.values?.opening_time > 0 ? sessionReturn(view.values.opening_time, toRun(view.values.opening_time, false))
      : toRun(view.values?.opening_time, false);
  // A run to an end stop stays a positioning until the motor stops by itself or its full time has passed; only then
  // does the screen ask to confirm the end stop.
  const positioningRun = (seconds) => !MOTOR_STOPPED(view) && seconds > 0 && (elapsed ?? 0) < seconds;
  // Every «arrived» confirm (A-05, A-10, the return to the top, A-19 and A-30's first leg) is on at the earliest
  // of the motor's stop status and the estimated run, with no margin; the wait's bar ends at that same instant. Before
  // it the user cannot claim an end stop the cover could not have reached yet.
  // The motor stopped by itself well before the estimated run (under 60 %): a wall switch or an obstacle gives the same
  // frame as the end stop, so the user is asked to look before confirming. The confirmation stays active.
  const stoppedEarly = (estimate, key) => {
    if (!MOTOR_STOPPED(view) || view.stopped_elapsed == null || !(estimate > 0) || view.stopped_elapsed >= estimate * EARLY_STOP_SHARE) return;
    screen.notices.push({ tone: "warning", result: true, text: fill(t(key), { seconds: n.fixed(view.stopped_elapsed), estimate: n(estimate, 0) }) });
  };
  // A lift-off reading under 1 cm repeats the run. The backend clears `still_resting` as soon as the return to
  // the bottom starts, so the panel keeps the value it sent: the return (A-09) and the new lift-off briefing (A-07)
  // name it and say why; after a reload, without the value, the backend's notice stands while it lasts.
  const resting = local.lastGap != null && local.lastGap < (view.touching_cm ?? 1) && (view.lift_attempts ?? 1) > 1;
  // A warning, worded by where the repeat came from (the value typed, or the button).
  const liftRepeated = () => ({ tone: "warning", result: true, text: !resting ? fill(t("calLiftRepeated"), view) : fill(t("wiz_T458"), { n: n(local.lastGap, 2) }) });
  // After ② (or at the end of a half run) the backend waits for the actuator's stop: the stopwatch stays at the
  // press. The bar stays as it is, its buttons off (Stop follows the motor). The screen is the one the
  // user was on; «Arresto in corso…» only if the wait lasts (finalize, STOP_NOTE_MS).
  const stopping = (seconds) => {
    if (seconds !== undefined) {
      if (seconds != null) { const [label, value] = splitLabel(fill(t("wiz_T257"), { seconds: n.fixed(seconds) })); screen.stopwatch = { label, value }; }
      else screen.stopwatch = null;
    }
    screen.actions = screen.actions.map((action) => (action.id === "stop" || action.id === "stopped" ? action : { ...action, disabled: true }));
  };
  // Once the run to an end stop is over (the stop frame, or its full time and a margin gone by: a timed actuator
  // stops by itself without a frame) Stop gives way to the state, «La corsa è finita: la tapparella è ferma».
  const ranOut = (seconds) => MOTOR_STOPPED(view) || (seconds > 0 && (elapsed ?? 0) >= seconds + Math.max(2, seconds * 0.15));
  // In a bar with the ①/② pair Stop is always drawn, last, off while nothing moves (finalize sets it).
  // So in every bar of a step that moves the cover: its slot never empties, the state only takes its place.
  const pinnedStop = { ...stop, pinned: true };
  // The state keeps Stop's own label; the slot draws it as the red button, off (no line saying the
  // run is over, which read as a message popping up).
  const stopOrState = (seconds) => ranOut(seconds) ? [{ id: "stopped", label: t("wiz_T028"), kind: "state" }] : [pinnedStop];
  // A return before a tape reading (the user is there to read the tape) never goes on by itself: its end stop is
  // confirmed by the user, enabled once the motor stopped by itself or its estimated time has run; the run to the next
  // reading then starts from that press.
  const arrivedBy = (seconds) => MOTOR_STOPPED(view) || !(seconds > 0) || (elapsed ?? 0) >= seconds;
  // Same step, same bar: a run Home Assistant starts by itself after a press (a return to an end stop, the
  // runs of the tape readings, the check's legs) draws its whole bar, off, from the briefing that precedes it. When
  // nothing starts it (a session reopened or claimed there) its own start goes first.
  // That start is «Avvia il movimento», and the run it waits for is never said as moving: its title
  // «Posizionamento della tapparella», its wait says to press it and what the cover will then do (`key`), with no bar, no
  // time left and no «Aspetta che si fermi»; its stages stay as a preview, none of them in progress. Once the run starts
  // (from that press or by itself) the screen is the run's own again, and the start is gone, as in a run started by itself.
  const toStart = phase === "briefing" && local.autoStart !== step;
  const withStart = (items) => toStart ? [{ id: "next", label: t("wiz_T659"), kind: "primary" }, ...items] : items;
  const startWait = (key) => { if (toStart && screen.wait) Object.assign(screen, { title: t("wiz_T720"), wait: { ...screen.wait, text: tx(key, values), progress: false,
    stages: screen.wait.stages.map((stage) => stage.state === "current" ? { ...stage, state: "future" } : stage) } }); };
  // The base calibration ends with an optional check of the model just measured (the backend's check: the end
  // stop, a timed run to half the travel descending from the top, a tape reading); its gap is the precision shown in
  // the summary, and the reading anchors Home Assistant's estimate.
  // It is part of the path (not skippable): its own step «Verifica», in a live view as in a reopened or read one.
  // A check stopped (Stop, or a movement it did not expect) is done again: it is part of the path.
  if (phase === "review" && !view.check && !local.refining && !local.checkSkipped && view.travel_cm > 0) {
    screen.phase = "check";
    if (view.check_interrupted) screen.notices.push({ tone: "warning", result: true, text: t("wiz_T568") });
    // Never a dead end: a model Home Assistant can plan no check for may be saved without one (no «Verificata»).
    const impossible = ctx.status?.error?.code === "invalid_check";
    // The drawing of the reading with the tape at part height (what the check asks for), between title and text.
    return Object.assign(screen, { code: p("21v"), title: t("wiz_T544"), body: `${t("wiz_T545")}\n\n${tx("wiz_T564", { threshold: n(view.check_threshold_cm ?? 4, 0) })}`,
      illustration: { id: "curtain-partial-tape", position: BEFORE },
      actions: [{ id: "check-start", label: t("wiz_T546"), kind: "primary" },
        ...(impossible ? [{ id: "skip-check", label: t("wiz_T576"), kind: "secondary" }] : [])] });
  }
  if (phase === "review" || phase === "saving") return summaryScreen(screen, ctx, tx, n, values);
  // On the saved times the lift-off is a reading as the others: the cover closes (1/2)
  // and rises by itself for the slat time saved (2/2), the waiting screen of every automatic movement, then the gap.
  if (view.auto_lift && ((step === "home" && view.lift_pair === 1 && phase !== "briefing") || (step === "lift" && !["briefing", "reading"].includes(phase)))) {
    const first = step === "home", closeTime = first ? view.planned_seconds : null;
    // A code of its own (no drawing: nothing to press here, unlike A-07's lift-off with ②).
    Object.assign(screen, { code: p("41"), phase: "readings", title: t("wiz_T027"),
      wait: { text: t("wiz_T687"), stages: [
        { text: tx("wiz_T685", { seconds: n(Math.ceil((closeTime ?? view.values?.closing_time ?? 0) - 0.001), 0) }), state: first ? "current" : "done" },
        { text: tx("wiz_T686", { seconds: n.fixed(view.saved_slat_s ?? 0, 1) }), state: first ? "future" : "current" }],
        progress: phase === "settling" ? { value: 0, remaining: null } : waitBar(view, elapsed, view.planned_seconds) },
      actions: [pinnedStop] });
    if (phase === "geometry_wait_stop") stopping();
    return screen;
  }
  if (step === "home") {
    screen.phase = view.auto_lift && view.lift_pair === 1 && phase !== "briefing" ? "readings" : "prep";
    // A session's first closing starts by itself (its briefing already draws the positioning and its bar); a
    // session reopened before it started keeps A-03 and its button.
    if (phase === "briefing" && local.autoStart !== "home") return Object.assign(screen, { code: p("03"), title: t("wiz_T021"), body: path === "C" ? tx("wiz_T197", values) : view.keep_times ? t(fineTuneBrief(typeof view.slats === "boolean" ? view.slats : null)) : t("wiz_T022"),
      actions: [{ id: "next", label: t("wiz_T024"), kind: "primary" }] });
    // One bar from the start of the closing to its end stop: the end stop, off until the cover has arrived (the
    // motor stopped by itself, or its estimated time is over); «Non ha fatto quello che doveva» from the start (it
    // stops the cover and opens S-01, nothing measured yet is lost); Stop last, replaced in place by the finished line
    // once the run is over. Not on A-10: there a stop discards the lift-off measurement.
    // Once the end stop is confirmed the session waits for the bus to confirm its Stop: A-05 stays, off.
    const waiting = phase === "geometry_wait_stop";
    const arrived = (phase === "closing" && !positioningRun(closingTime)) || waiting;
    const running = ["starting_close", "closing"].includes(phase);
    const bar = [{ id: "endpoint", label: t("wiz_T032"), kind: "primary", disabled: !arrived, settling: !arrived && running },
      { id: "misbehaved", label: t("wiz_T033"), kind: "secondary", disabled: !running }, ...stopOrState(closingTime)];
    // A-05 once the positioning is over; while the motor still runs its text says the cover is closing, not closed.
    if (arrived) {
      Object.assign(screen, { code: p("05"), title: t("wiz_T030"), body: t(ranOut(closingTime) ? (slats ? "wiz_T031" : "wiz_T501") : (slats ? "wiz_T590" : "wiz_T591")), actions: bar });
      if (waiting) stopping(); else stoppedEarly(closingTime, "wiz_T440");
      return screen;
    }
    positioning("04", t("wiz_T605"), closingTime, tx("wiz_T604", values));
    screen.actions = bar;
    return screen;
  }
  if (["lift", "reset", "opening"].includes(step)) screen.phase = "ascent";
  if (step === "closing") screen.phase = "descent";
  if (["half_open", "half_close", "top"].includes(step)) screen.phase = "readings";
  if (step === "lift") {
    // «La salita si misura in due corse» introduces the timed runs: nothing is timed on the saved times.
    if (phase === "briefing" && !local.ackLift && slats && !view.keep_times) return Object.assign(screen, { code: p("06"), title: t("wiz_T035"), body: t("wiz_T036"),
      actions: [{ id: "ack:lift", label: t("wiz_T037"), kind: "primary" }] });
    if (phase === "reading") {
      // No dead end beyond the 20 cm: the field takes 1–20 cm (Fatto off outside), its note and
      // its refusal say the same thing, and the way out is «Ripeti il movimento» (the backend's `repeat`), the one label of
      // every repetition; no «Il bordo è ancora appoggiato» button any more.
      // The «20 cm» sentence is only the refusal of a value out of range (0, or more than 20), in the slot under
      // the field; the drawing on top, a little smaller, so that the field and its message are seen without scrolling.
      const max = view.max_gap_cm ?? 20, again = tx("wiz_T612", { max: n(max, 0) });
      // After the rise by itself for the slat time saved the edge may not have lifted off: 0 is a reading.
      Object.assign(screen, { code: p("08"), title: t("wiz_T047"), body: t(view.auto_lift ? "wiz_T690" : "wiz_T048"), illustrationSize: "small",
        field: { label: t("wiz_T049"), hint: t("wiz_T050"), rangeError: again, min: view.auto_lift ? 0 : view.touching_cm ?? 1, max, kind: "gap" },
        actions: [{ id: "reading", label: t("wiz_T051"), kind: "primary" }, { id: "repeat", label: t("wiz_T061"), kind: "secondary" }] });
      return screen;
    }
    // Repeated, the rise by itself starts from the rest on ①; nothing to press while it runs.
    if (view.auto_lift) {
      Object.assign(screen, { code: p("07"), phase: "readings", title: t("wiz_T042"), body: t("wiz_T687"),
        actions: [{ id: "next", label: t("wiz_T045"), kind: "primary" }, pinnedStop] });
      return screen;
    }
    const running = ["starting_open", "opening"].includes(phase);
    // While the motor runs the body says what to watch for, no longer how to start it.
    const moving = /^(starting_)?(open|clos)ing$/.test(phase);
    Object.assign(screen, { code: p("07"), title: t("wiz_T042"), body: moving ? t("wiz_T520") : path === "C" ? t("wiz_T202") : t("wiz_T043"),
      stopwatch: runWatch(t, n, view, elapsed),
      // ① · ② · Stop, always all three: ① off once it started the run, ② and Stop on while the cover moves.
      actions: [{ id: "next", label: t("wiz_T045"), kind: "primary", disabled: phase !== "briefing" },
        { id: "lift", label: t("wiz_T046"), kind: "primary", disabled: phase !== "opening" }, pinnedStop] });
    if ((resting || view.still_resting) && phase === "briefing") screen.notices.push(liftRepeated());
    if (phase === "geometry_wait_stop") stopping(view.samples?.lift);
    return screen;
  }
  if (step === "reset") {
    // The reading is already in: the screen is about the choice (repeat the lift-off run or go on), not the reading.
    if (phase === "briefing" && view.gap_warning) return Object.assign(screen, { code: p("08"), title: t("wiz_T525"), body: t("wiz_T526"), illustration: false,
      results: liftTiles(), resultsTitle: t("wiz_T599"), provisional: true,
      notices: [{ tone: "warning", result: true, text: fill(t("calGapWarning"), view) }],
      // Repeating is the advised way; going on with a wide gap is the outlined secondary.
      actions: [{ id: "repeat", label: t("wiz_T061"), kind: "primary" }, { id: "next", label: t("wiz_T473"), kind: "secondary" }] });
    // The same Stop rule as A-05; no T033 here, a stop would discard the lift-off.
    // One bar from the briefing to the end stop: the end stop (on once the cover has arrived), Stop last
    // or the finished line.
    const waiting = phase === "geometry_wait_stop";
    const ready = phase === "closing" && arrivedBy(resetTime);
    const bar = withStart([{ id: "endpoint", label: t("wiz_T032"), kind: "primary", disabled: !ready,
      settling: ["starting_close", "closing"].includes(phase) && !ready }, ...stopOrState(resetTime)]);
    if ((phase === "closing" && !positioningRun(resetTime)) || waiting) {
      Object.assign(screen, { code: p("10"), title: t("wiz_T053"), body: t("wiz_T054"), actions: bar });
      // A-10 stays, off, while the bus confirms the Stop.
      if (waiting) stopping();
      // Not on a return before a repeated reading: that one confirms itself on the stop frame.
      else if (!local.ackReadings) stoppedEarly(resetTime, "wiz_T440");
      return screen;
    }
    positioning("09", tx(path === "C" ? "wiz_T203" : "wiz_T052", values), resetTime);
    startWait(path === "C" ? "wiz_T712" : "wiz_T711");
    screen.actions = bar;
    if (resting || view.still_resting) screen.notices.push(liftRepeated());
    return screen;
  }
  // A full run that stopped before ②: its result is shown but not valid; only a repeat goes on.
  // Not valid is drawn as such: its own title, the tiles struck through, the notice as an error, and Continue
  // shown but disabled beside Repeat.
  const invalid = (key) => local.invalidRun?.step === key ? invalidNotice(t, local.invalidRun.reason) : null;
  // Still in the run, stopped before ②: the run's result is shown not valid at once, ② is gone; so it stays
  // while its end is being confirmed.
  const stoppedRun = (key) => phase === key && STOPPED_BEFORE_PRESS(view, local)
    ? invalidNotice(t, local.invalidRun?.step === key ? local.invalidRun.reason : stopVerdict(view.stopped_elapsed))
    : phase === "geometry_wait_stop" && local.invalidRun?.step === key ? invalidNotice(t, local.invalidRun.reason) : null;
  const stoppedTime = (key) => view.stopped_elapsed ?? view.values?.[`${key}_time`];
  const repeatOnly = (code, title, results, note) => Object.assign(screen, { code, title, results: results.filter(Boolean),
    notices: [note], actions: [{ id: "repeat", label: t("wiz_T061"), kind: "primary" }, { id: "invalid-next", label: t("wiz_T060"), kind: "secondary", disabled: true }] });
  // On the times already saved the runs to the top (then the travel is read) and back to
  // the bottom time nothing: each is a positioning confirmed at its end stop, as the returns are; after a wide lift-off
  // gap the choice comes first (repeat the lift-off run, or go on).
  if (view.keep_times && ["opening", "closing"].includes(step) && phase !== "reading") {
    if (step === "opening" && phase === "briefing" && view.gap_warning) return Object.assign(screen, { code: p("08"), title: t("wiz_T525"), body: t("wiz_T526"), illustration: false,
      results: liftTiles(), resultsTitle: t("wiz_T599"), provisional: true,
      notices: [{ tone: "warning", result: true, text: fill(t("calGapWarning"), view) }],
      actions: [{ id: "repeat", label: t("wiz_T061"), kind: "primary" }, { id: "next", label: t("wiz_T473"), kind: "secondary" }] });
    const up = step === "opening", seconds = toRun(view.values?.[up ? "opening_time" : "closing_time"], !up);
    const running = (up ? ["starting_open", "opening"] : ["starting_close", "closing"]).includes(phase);
    const arrived = phase === (up ? "opening" : "closing") && arrivedBy(seconds);
    const bar = withStart([{ id: "endpoint", label: t(up ? "wiz_T432" : "wiz_T032"), kind: "primary", disabled: !arrived, settling: running && !arrived },
      { id: "misbehaved", label: t("wiz_T033"), kind: "secondary", disabled: !running }, ...stopOrState(seconds)]);
    positioning(up ? "09" : "04", t(up ? "wiz_T668" : "wiz_T669"), seconds);
    startWait(up ? "wiz_T716" : "wiz_T717");
    screen.phase = "readings";
    screen.actions = bar;
    if (phase === (up ? "opening" : "closing")) stoppedEarly(seconds, up ? "wiz_T441" : "wiz_T440");
    if (phase === "geometry_wait_stop") stopping();
    return screen;
  }
  if (step === "opening") {
    const stopped = stoppedRun("opening");
    if (stopped) return repeatOnly(p("12"), t("wiz_T502"), [...(slats ? liftTiles() : []), { ...timeTile("wiz_T093", stoppedTime("opening")), invalid: true }], stopped);
    if (phase === "reading") {
      const bad = invalid("opening");
      if (bad) return repeatOnly(p("12"), t("wiz_T502"), [...(slats ? liftTiles() : []), { ...timeTile("wiz_T093", view.values?.opening_time), invalid: true }], bad);
      // The travel first, then the ascent's result with it («Corsa del telo»), confirmed or repeated.
      if (local.travelTyped != null) return Object.assign(screen, { code: p("12"), title: t("wiz_T058"), body: t("wiz_T059"),
        results: [...(slats ? liftTiles() : []), timeTile("wiz_T093", view.values?.opening_time), { label: t("wiz_T041"), value: n(local.travelTyped), unit: "cm" }].filter(Boolean),
        resultsTitle: t("wiz_T600"), provisional: true,
        actions: [{ id: "ack:ascent", label: t("wiz_T060"), kind: "primary" }, { id: "repeat", label: t("wiz_T061"), kind: "secondary" }] });
      return Object.assign(screen, { code: p("13"), title: t("wiz_T062"), body: t("wiz_T063"),
        // One pattern for every reading's hint: the expected value when known, the field's limits otherwise.
        // A travel offered from before says where it comes from; otherwise only the accepted range.
        field: { label: t("wiz_T064"), hint: (local.keptTravel ?? view.saved_travel_cm) != null ? tx("wiz_T549", { cm: n(local.keptTravel ?? view.saved_travel_cm, 0) }) : t("wiz_T066"),
          // The card's range (10–1000 cm) here too; Home Assistant takes 0.1–10000.
          min: 10, max: 1000, kind: "travel", value: local.keptTravel ?? view.saved_travel_cm ?? null },
        actions: [{ id: "reading", label: t("wiz_T051"), kind: "primary" }] });
    }
    Object.assign(screen, { code: p("11"), title: slats ? t("wiz_T055") : t("wiz_T040"),
      body: t(/^(starting_)?opening$/.test(phase) ? "wiz_T521" : slats ? "wiz_T056" : "wiz_T499"),
      stopwatch: runWatch(t, n, view, elapsed),
      actions: [{ id: "next", label: t("wiz_T045"), kind: "primary", disabled: phase !== "briefing" },
        { id: "endpoint", label: t("wiz_T057"), kind: "primary", disabled: phase !== "opening" || MOTOR_STOPPED(view) }, pinnedStop] });
    if (phase === "geometry_wait_stop") stopping(view.values?.opening_time);
    return screen;
  }
  if (step === "closing") {
    const stopped = stoppedRun("closing");
    if (stopped) {
      repeatOnly(p("15"), t("wiz_T503"), [{ ...timeTile("wiz_T094", stoppedTime("closing")), invalid: true }], stopped);
      screen.phase = "descent";
      return screen;
    }
    Object.assign(screen, { code: p("14"), title: t("wiz_T068"),
      body: t(/^(starting_)?closing$/.test(phase) ? (slats ? "wiz_T522" : "wiz_T523") : slats ? "wiz_T069" : "wiz_T500"), stopwatch: runWatch(t, n, view, elapsed),
      actions: [{ id: "next", label: t("wiz_T045"), kind: "primary", disabled: phase !== "briefing" },
        { id: "endpoint", label: t("wiz_T070"), kind: "primary", disabled: phase !== "closing" || MOTOR_STOPPED(view) }, pinnedStop] });
    if (phase === "geometry_wait_stop") stopping(view.values?.closing_time);
    return screen;
  }
  if (step === "half_open") {
    const bad = phase === "briefing" ? invalid("closing") : null;
    if (bad) {
      repeatOnly(p("15"), t("wiz_T503"), [{ ...timeTile("wiz_T094", view.values?.closing_time), invalid: true }], bad);
      screen.phase = "descent";
      return screen;
    }
    // On the saved times nothing was measured on the way down: no result to confirm.
    if (phase === "briefing" && !local.ackDescent && !view.keep_times) return Object.assign(screen, { code: p("15"), phase: "descent", title: t("wiz_T071"), body: t("wiz_T072"),
      // The descent's result is its duration only (the travel belongs to the ascent's).
      results: [timeTile("wiz_T094", view.values?.closing_time)].filter(Boolean), resultsTitle: t("wiz_T601"), provisional: true,
      actions: [{ id: "ack:descent", label: t("wiz_T060"), kind: "primary" }, { id: "repeat", label: t("wiz_T061"), kind: "secondary" }] });
    // A-16 says the runs in their order: the full measure reads the half ascent first, then the half descent; on
    // the saved times the half descent came first (below), so a briefing shown here then says that order.
    if (phase === "briefing" && !local.ackReadings) return Object.assign(screen, { code: p("16"), title: t("wiz_T074"), body: t(view.keep_times ? "wiz_T075_keep" : "wiz_T075"),
      actions: [{ id: "ack:readings", label: t("wiz_T076"), kind: "primary" }] });
    if (phase === "reading") return Object.assign(screen, { code: p("18"), title: t("wiz_T081"), body: t("wiz_T082"),
      field: halfField(ctx, tx, n), actions: [{ id: "reading", label: t("wiz_T051"), kind: "primary" }, { id: "repeat", label: t("wiz_T033"), kind: "secondary" }] });
    const seconds = ((slats ? view.samples?.lift || 0 : 0) + (view.values?.opening_time || 0)) / 2;
    Object.assign(screen, { code: p("17"), title: t("wiz_T027"),
      wait: { text: tx(path === "C" ? "wiz_T205" : "wiz_T080", values), stages: [{ text: tx("wiz_T079", { seconds: n(seconds, 0) }), state: "current" }],
        progress: waitBar(view, elapsed, seconds) },
      // Normally started by the wizard itself; if that did not happen the run can be started here (its start is
      // gone once the run starts). Stop keeps its slot to the end of the run.
      actions: withStart([pinnedStop]) });
    startWait(path === "C" ? "wiz_T714" : "wiz_T713");
    return screen;
    if (phase === "geometry_wait_stop") stopping();
    return screen;
  }
  // The return to the top before the half-descent reading is the readings' two-leg wait (A-19), known from the
  // session itself (the half-ascent reading is in): never A-09's one leg, whatever this tab's own acknowledgements
  // (`readingsReturn`, above).
  if (step === "top" && !readingsReturn) {
    // Once the motor has stopped the run is no longer «positioning»: the stop is said in the title, with its
    // warning when it came early, and the end stop to confirm (the state instead of Stop).
    // As A-05, «Non ha fatto quello che doveva» beside the end stop. From the start of the run, with the end
    // stop (on once the cover has arrived) and Stop last, replaced in place by the finished line.
    const running = ["starting_open", "opening"].includes(phase);
    const arrived = phase === "opening" && arrivedBy(topTime);
    const bar = withStart([{ id: "endpoint", label: t("wiz_T432"), kind: "primary", disabled: !arrived, settling: running && !arrived },
      { id: "misbehaved", label: t("wiz_T033"), kind: "secondary", disabled: !running }, ...stopOrState(topTime)]);
    if (phase === "opening" && MOTOR_STOPPED(view)) {
      Object.assign(screen, { code: p("09"), phase: "descent", title: t("wiz_T504"), actions: bar });
      stoppedEarly(topTime, "wiz_T441");
      return screen;
    }
    // The return to the top before a repeated descent: a positioning run to confirm at the end stop.
    // The end stop it goes to, by direction («va», not «torna»: it did not come from there).
    positioning("09", t("wiz_T553"), topTime);
    startWait("wiz_T718");
    screen.phase = "descent";
    screen.actions = bar;
    if (phase === "opening") stoppedEarly(topTime, "wiz_T441");
    if (phase === "geometry_wait_stop") stopping();
    return screen;
  }
  // On the saved times the half descent starts from the top where the travel was read: one leg (no return
  // to the top first), after the readings' briefing (A-16), as the half ascent did before.
  if (view.keep_times && step === "half_close" && phase !== "reading") {
    screen.phase = "readings";
    // A-16 on the saved times: the half descent first, then the half ascent.
    if (phase === "briefing" && !local.ackReadings) return Object.assign(screen, { code: p("16"), title: t("wiz_T074"), body: t("wiz_T075_keep"),
      actions: [{ id: "ack:readings", label: t("wiz_T076"), kind: "primary" }] });
    const seconds = view.planned_seconds ?? Math.max(((view.values?.closing_time || 0) - (view.samples?.lift || 0)) / 2, 0);
    Object.assign(screen, { code: p("19"), title: t("wiz_T027"),
      wait: { text: tx("wiz_T689", values), stages: [{ text: tx("wiz_T688", { seconds: n(Math.ceil(seconds - 0.001), 0) }), state: "current" }],
        progress: waitBar(view, elapsed, seconds) },
      actions: withStart([pinnedStop]) });
    startWait("wiz_T715");
    if (phase === "geometry_wait_stop") stopping();
    return screen;
  }
  if (step === "top" || step === "half_close") {
    if (phase === "reading") return Object.assign(screen, { code: p("20"), title: t("wiz_T087"), body: t("wiz_T088"),
      field: halfField(ctx, tx, n), actions: [{ id: "reading", label: t("wiz_T051"), kind: "primary" }, { id: "repeat", label: t("wiz_T033"), kind: "secondary" }] });
    const down = Math.max(((view.values?.closing_time || 0) - (view.geometry?.slat_time_s || 0)) / 2, 0);
    const first = step === "top";
    // The first leg keeps, once over, the time it was planned with while it ran (frozen by the wizard:
    // `local.legOne`; else the session's own half-ascent rest), never the whole ascent of the second leg's fallback.
    const legOne = first ? topTime : local.legOne?.seconds ?? halfRest ?? topTime;
    if (first) screen.legOne = topTime;
    Object.assign(screen, { code: p("19"), title: t("wiz_T027"),
      wait: { text: t("wiz_T553"), stages: [
        // Each leg says the time its bar counts down from (the run to the top from where the cover is), whole
        // seconds rounded up as the time left is, so «circa 23 s» never shows «circa 24 s» left.
        { text: tx("wiz_T084", { seconds: n(Math.ceil((legOne ?? view.values?.opening_time ?? 0) - 0.001), 0) }), state: first ? "current" : "done" },
        { text: tx("wiz_T085", { seconds: n(Math.ceil(down - 0.001), 0) }), state: first ? "future" : "current" }],
      progress: waitBar(view, elapsed, first ? topTime : down) } });
    // The return to the top is a positioning run: Home Assistant needs its end stop confirmed (once it has
    // arrived). One bar for both legs: that end stop (off in the second leg, already confirmed), Stop last,
    // replaced in place by the finished line once the run to the top is over.
    const ready = arrivedBy(topTime);
    screen.actions = withStart([{ id: "endpoint", label: t("wiz_T432"), kind: "primary", disabled: !first || phase !== "opening" || !ready,
      settling: first && (phase === "starting_open" || (phase === "opening" && !ready)) }, ...(first ? stopOrState(topTime) : [pinnedStop])]);
    if (phase === "geometry_wait_stop") stopping();
    // The return to the top before the check run says so (not the half descent's stages).
    // The check's first leg, as the half readings': to the top (its bar), then the run to its target (second
    // stage), which starts on the confirmation of the top.
    if (first && view.check) {
      screen.code = p("30"); screen.phase = "check"; screen.legOne = topTime;
      screen.wait = { text: t("wiz_T553"), stages: checkStages(tx, n, view, topTime ?? view.values?.opening_time, true), progress: waitBar(view, elapsed, topTime) };
    }
    startWait("wiz_T718");
    // Started by the wizard itself; if that did not happen (a reopened session, a claim) it starts here (withStart).
    return screen;
  }
  if (step === "check") {
    // The check of the measured model: its run is stopped by Home Assistant like the intermediate runs, so
    // no elapsed time; then the tape reading, from 0.1 cm to the measured travel.
    screen.phase = "check";
    // The final check says which reading was kept far from the model. Its hint gives the height expected
    // (the check is the comparison with it), not a range.
    const expectedCheck = view.check?.expected_cm ?? view.check?.target_cm;
    // The check is about to answer: the far readings wait for its result, in the summary.
    if (phase === "reading") return Object.assign(screen, { code: p("31"), title: t("wiz_T138"), body: t("wiz_T139"), notices: [...screen.notices],
      field: { label: t("wiz_T064"), hint: expectedCheck != null ? tx("wiz_T558", { cm: n(expectedCheck, 0) }) : t("wiz_T491"), min: 0.1, max: view.travel_cm ?? 10000, kind: "height" },
      actions: [{ id: "reading", label: t("wiz_T051"), kind: "primary" }, { id: "repeat", label: t("wiz_T033"), kind: "secondary" }] });
    // The second leg, the timed run to the target: its own bar. The first leg's bar (A-30 is one step):
    // the end stop of the top, off (already confirmed), and Stop in its slot to the end of the run.
    const planned = view.check?.planned_seconds;
    // The first leg (to the end stop) keeps the time it was planned with.
    Object.assign(screen, { code: p("30"), title: t("wiz_T027"),
      wait: { text: t(view.check?.direction === "opening" ? "wiz_T554" : "wiz_T553"), stages: checkStages(tx, n, view, local.legOne?.seconds ?? view.values?.opening_time, false), progress: waitBar(view, elapsed, planned) },
      actions: withStart([{ id: "endpoint", label: t("wiz_T432"), kind: "primary", disabled: true }, pinnedStop]) });
    startWait(view.check?.direction === "opening" ? "wiz_T719" : "wiz_T718");
    if (phase === "geometry_wait_stop") stopping();
    return screen;
  }
  if (phase === "geometry_wait_stop") return positioning("04", "");
  return Object.assign(screen, { code: "unknown", title: t(`calPhase_${phase}`), actions: [stop] });
}

/**
 * Live `guided` session: the basic calibration and path C's times only: the backend's two-run protocol
 * on our own screens and texts, adapted from A-11/A-12/A-14/A-15 (never the maintainer's phase texts). The backend goes
 * from the measured ascent straight to «ready for the descent» and from the measured descent straight to the review: the
 * result screens are the panel's own (acknowledged locally). A repeat starts the times again from a closed cover (the
 * guided session has no repeat verb for the full runs). The validity rule of the full runs applies as on A-11/A-14.
 * One step does one thing. The ascent and the descent are the two
 * buttons of «Solo i tempi»; with `slats` a step of its own follows, «Lamelle» (C-07): from the closed cover a short run
 * whose one press («Si è staccato») is the Stop, then Home Assistant closes the cover again by itself (`reclosing`); its
 * result (C-08b) can be repeated (`repeat`) before the summary.
 */
function guidedScreen(screen, ctx, tx, n, values, stop) {
  const { t, view, elapsed, local = {}, cover } = ctx;
  const phase = view.phase;
  const slats = view.slats === true;
  // The drawings show slats when the session runs them, or (the times only of a cover fine-tuned) when the
  // cover has them.
  screen.drawSlats = slats || (refinedCover(cover) && cover?.effective?.slat_time_s?.value > 0);
  const tile = (key, seconds) => seconds == null ? null : { label: t(key), value: n.fixed(seconds), unit: "s" };
  const bad = (key) => local.invalidRun?.step === key ? invalidNotice(t, local.invalidRun.reason) : null;
  // A run the actuator's own stop ended (Home Assistant's `ended_by`) was not ended
  // by ②: its result says the cover stopped by itself and the time is the actuator's.
  const byItself = (key) => view.ended_by?.[key] === "bus_stop";
  // The descent's own «Ripeti» opens the cover back by itself and repeats only the descent
  // (Home Assistant's `repeat` with `direction: "closing"`); the ascent's still starts the times again. A run not
  // valid (stopped before ②) is still repeated from the start.
  const result = (code, title, body, tiles, key, ack, stopped = null) => {
    const notice = stopped || bad(key);
    // The ascent's «Ripeti» too brings the cover back by itself (down) and repeats only the ascent.
    const again = !notice && !view.direction ? `repeat:${key}` : "restart";
    // As on A-12/A-15, a result that is not valid looks it.
    return Object.assign(screen, { code, title: notice ? t(key === "opening" ? "wiz_T502" : "wiz_T503") : title, body: notice ? "" : t(body),
      results: tiles.filter(Boolean).map((tile) => notice ? { ...tile, invalid: true } : tile), notices: notice ? [notice] : [],
      ...(notice ? {} : { resultsTitle: t(key === "opening" ? "wiz_T600" : "wiz_T601"), provisional: true }),
      actions: notice ? [{ id: "restart", label: t("wiz_T061"), kind: "primary" }, { id: "invalid-next", label: t("wiz_T060"), kind: "secondary", disabled: true }]
        : [{ id: ack, label: t("wiz_T060"), kind: "primary" }, { id: again, label: t("wiz_T061"), kind: "secondary" }] });
  };
  // The cover opens back by itself (nothing measured), then the descent's step again, from the top.
  if (phase === "reopening") return backScreen(screen, ctx, { phase: "descent", title: "wiz_T681", seconds: view.reopen_seconds }, stop);
  // The session closes the cover itself, before the first ascent (from wherever it stands, never
  // trusting a «closed» kept by Home Assistant) or before a repeated one; then the ascent from the rest.
  // The first close is a positioning as the others (C-04 of «Solo i tempi», A-04): «La tapparella si
  // sta posizionando», its bar on Home Assistant's `rehome_seconds`, the red Stop; the stepper still on «Preparazione».
  if (phase === "rehoming") {
    const first = !local.ackAscent && view.values?.opening_time == null && !local.rehomeRepeat;
    return backScreen(screen, ctx, first ? { code: "C-04", phase: "prep", title: "wiz_T027", lead: tx("wiz_T604", values), seconds: view.rehome_seconds, text: "wiz_T605" }
      : { phase: "ascent", title: "wiz_T682", seconds: view.rehome_seconds }, stop);
  }
  // The run stopped as it started: the cover was already at the end stop it was sent to; nothing was
  // measured. «Ripeti» takes it to where the run starts, by itself, then the run as the first time.
  if (view.reason === "already_at_end" && ["confirm_closed", "confirm_open"].includes(phase)) {
    const up = phase === "confirm_closed";
    return Object.assign(screen, { code: up ? "C-12" : "C-15", phase: up ? "ascent" : "descent", title: t(up ? "wiz_T502" : "wiz_T503"), body: "",
      notices: [{ tone: "error", result: true, text: t(up ? "wiz_T693" : "wiz_T694") }],
      actions: [{ id: up ? "repeat:opening" : "repeat:closing", label: t("wiz_T061"), kind: "primary" }] });
  }
  const summary = phase === "review" || phase === "saving";
  // With slats the measured descent leads to the lift-off run (`confirm_lift`), without them to the review.
  if ((summary || (phase === "confirm_lift" && slats)) && !local.ackDescent) {
    screen.phase = "descent";
    return result("C-15", t("wiz_T071"), byItself("closing") ? "wiz_T707" : "wiz_T480", [tile("wiz_T094", view.values?.closing_time)], "closing", "ack:descent");
  }
  if (slats && (["confirm_lift", "starting_lift", "lifting", "reclosing"].includes(phase) || (summary && !local.ackLift))) return liftStep(screen, ctx, n, stop);
  if (summary) return summaryScreen(screen, ctx, tx, n, values);
  // Still in the run, stopped before ②: its result, not valid, at once; the button is gone.
  if (["opening", "closing"].includes(phase) && STOPPED_BEFORE_PRESS(view, local)) {
    const key = phase, notice = invalidNotice(t, local.invalidRun?.step === key ? local.invalidRun.reason : stopVerdict(view.stopped_elapsed));
    screen.phase = key === "opening" ? "ascent" : "descent";
    return key === "opening" ? result("C-12", t("wiz_T058"), "wiz_T478", [tile("wiz_T093", view.stopped_elapsed)], "opening", "ack:ascent", notice)
      : result("C-15", t("wiz_T071"), "wiz_T480", [tile("wiz_T094", view.stopped_elapsed)], "closing", "ack:descent", notice);
  }
  if (["confirm_closed", "starting_open", "opening"].includes(phase)) {
    // At rest the body says how to start; once it runs, what to watch for (the motor stopping at the top).
    return Object.assign(screen, { code: "C-11", phase: "ascent", title: t("wiz_T040"), body: t(phase === "confirm_closed" ? "wiz_T477" : "wiz_T521"),
      stopwatch: runWatch(t, n, view, elapsed),
      // Every button of the run always drawn, off when it cannot act.
      actions: [{ id: "run:open", label: t("wiz_T045"), kind: "primary", disabled: phase !== "confirm_closed" },
        { id: "endpoint", label: t("wiz_T057"), kind: "primary", disabled: phase !== "opening" || MOTOR_STOPPED(view) }, { ...stop, pinned: true }] });
  }
  if (phase === "confirm_open" && !local.ackAscent) {
    screen.phase = "ascent";
    return result("C-12", t("wiz_T058"), byItself("opening") ? "wiz_T706" : "wiz_T478", [tile("wiz_T093", view.values?.opening_time)], "opening", "ack:ascent");
  }
  if (["confirm_open", "starting_close", "closing"].includes(phase)) {
    // A cover without slats (said so, or the times only of one drawn without) is told nothing about slats
    // (their own texts, as path A's).
    const noSlats = view.slats === false || (view.slats == null && !screen.drawSlats);
    return Object.assign(screen, { code: "C-14", phase: "descent", title: t("wiz_T068"),
      body: t(phase === "confirm_open" ? (noSlats ? "wiz_T699" : "wiz_T479") : noSlats ? "wiz_T523" : "wiz_T522"),
      stopwatch: runWatch(t, n, view, elapsed),
      actions: [{ id: "run:close", label: t("wiz_T045"), kind: "primary", disabled: phase !== "confirm_open" },
        { id: "endpoint", label: t("wiz_T070"), kind: "primary", disabled: phase !== "closing" || MOTOR_STOPPED(view) }, { ...stop, pinned: true }] });
  }
  return Object.assign(screen, { code: "unknown", title: t("wiz_T027"), actions: [stop] });
}

/**
 * The step «Lamelle» of the basic calibration with slats, C-07 with the lift-off drawing of A-07.
 * The same two buttons as «Salita» and «Discesa» (both always drawn, off when they cannot
 * act): ① «Avvia il movimento» starts the run from the closed cover (`confirm_lift`), ② «Si è staccato» is its Stop
 * (on once the bus reports the start); nothing moves before ①, the first time or after «Ripeti»;
 * the red Stop pinned in its slot. «Torna in appoggio…» off in the settling style while Home Assistant closes it
 * again by itself (`reclosing`). A lift-off refused (`lift_not_shorter`, Home Assistant's `reason`) is said in the
 * notice slot until the run starts again. Its result (C-08b, in `review`): «Tempo lamelle» as a provisional «Misura»,
 * «Va bene, avanti» or «Ripeti il movimento».
 */
function liftStep(screen, ctx, n, stop) {
  const { t, view, elapsed } = ctx;
  const phase = view.phase;
  screen.phase = "slats";
  if (phase === "review" || phase === "saving") {
    // The cover stays where the lift-off stopped it; its result says a few centimetres are fine.
    return Object.assign(screen, { code: "C-08b", title: t("wiz_T663"), body: `${t("wiz_T664")}\n\n${t("wiz_T670")}`,
      results: view.samples?.lift == null ? [] : [{ label: t("wiz_T095"), value: n.fixed(view.samples.lift), unit: "s" }], resultsTitle: t("wiz_T599"), provisional: true,
      actions: [{ id: "ack:lift", label: t("wiz_T060"), kind: "primary" }, { id: "repeat", label: t("wiz_T061"), kind: "secondary" }] });
  }
  if (view.reason === "lift_not_shorter") screen.notices.push({ tone: "error", result: true, text: t("calReason_lift_not_shorter") });
  if (phase === "reclosing") {
    return backScreen(screen, ctx, { phase: "slats", title: "wiz_T682", seconds: view.reclose_seconds,
      text: view.reason === "lift_not_shorter" ? "wiz_T662" : "wiz_T683" }, stop);
  }
  // After a stop the bus reported in the run (the lift-off can no longer be marked) ② gives way to the repeat.
  const second = phase === "lifting" && MOTOR_STOPPED(view) ? { id: "repeat", label: t("wiz_T061"), kind: "primary" }
    : { id: "lift", label: t("wiz_T680"), kind: "primary", disabled: phase !== "lifting" };
  return Object.assign(screen, { code: "C-07", title: t("wiz_T657"), body: t("wiz_T658"), stopwatch: runWatch(t, n, view, elapsed),
    actions: [{ id: "run:open", label: t("wiz_T045"), kind: "primary", disabled: phase !== "confirm_lift" }, second, { ...stop, pinned: true }] });
}

/**
 * A movement the session makes by itself to bring the cover back into position (`reopening` before
 * a repeated descent, `reclosing` before a repeated lift-off) is the waiting screen of every such movement: the
 * movement as its title, its bar on the time Home Assistant gives it (`back_stage`, `elapsed`
 * once it runs), the red Stop in its slot. No switched-off primary; the stepper stays on the step being repeated, and
 * once the cover is back that step is ready to start as the first time. No drawing, as every positioning (A-04, A-09):
 * C-40 is a code of its own, outside the drawings' table.
 */
function backScreen(screen, ctx, { code = "C-40", phase, title, seconds, text = "wiz_T683", lead = "" }, stop) {
  const { t, view, elapsed } = ctx;
  return Object.assign(screen, { code, phase, title: t(title), body: "", stopwatch: null,
    wait: { lead, text: t(text), stages: [], progress: waitBar(view, view.back_stage === "running" ? elapsed : null, seconds) },
    actions: [{ ...stop, pinned: true }] });
}

/** Simulated steps: fine-tuning (A, C), tape readings only (C) and path B. */
function previewScreen(screen, ctx, tx, n, values, stop) {
  const { t, view, path } = ctx;
  const points = view.path === "rolls", profile = view.path === "profile";
  const p = (number) => `${path === "B" ? "B" : path === "C" ? "C" : "A"}-${number}`;
  screen.phase = profile ? "readings" : points ? "readings" : "finetune";
  if (view.phase === "review") return summaryScreen(screen, ctx, tx, n, values);
  const readingCodes = { quarter_open: ["22", "23", "wiz_T122"], three_quarter_open: ["24", "25", "wiz_T126"],
    quarter_close: ["26", "27", "wiz_T130"], three_quarter_close: ["28", "29", "wiz_T134"], check: ["30", "31", "wiz_T138"] };
  const stageText = { back_down: "wiz_T120", back_up: "wiz_T084", quarter_open: "wiz_T121", three_quarter_open: "wiz_T125",
    quarter_close: "wiz_T129", three_quarter_close: "wiz_T133", check: "wiz_T137", bottom: "wiz_T171", half_travel: "wiz_T172" };
  // A preview's box says nothing moves (path B, fine-tuning, tape readings only), so its positionings are not
  // drawn as such: what would happen live, the stages it would run, no countdown, and the state in place of Stop.
  // The preview box already says nothing moves: no state line, but «Continua» to the reading at once.
  const still = [{ id: "skip", label: t("wiz_T006"), kind: "primary" }];
  if (view.phase === "positioning") {
    if (profile && view.step === "open_full") return Object.assign(screen, { code: "B-03", title: t("wiz_T505"),
      wait: { text: tx("wiz_T506", values), stages: [], progress: false }, actions: still });
    const stages = view.stages.map((stage, index) => ({ text: tx(stageText[stage.key], { seconds: n(stage.seconds, 0) }),
      state: index < view.stage_index ? "done" : index === view.stage_index ? "current" : "future" }));
    return Object.assign(screen, { code: profile ? "B-05" : p(readingCodes[view.step][0]), title: t("wiz_T505"),
      wait: { text: t("wiz_T507"), stages, progress: false }, actions: still });
  }
  if (view.phase === "reading") {
    if (view.step === "travel") return Object.assign(screen, { code: "B-04", title: t("wiz_T062"), body: t("wiz_T063"),
      field: { label: t("wiz_T064"), hint: t("wiz_T066"), min: 10, max: 1000, kind: "travel" },
      actions: [{ id: "reading", label: t("wiz_T051"), kind: "primary" }] });
    if (profile) return Object.assign(screen, { code: "B-06", title: t("wiz_T138"),
      body: tx("wiz_T509", { expected: n(view.expected_cm, 0), travel: n(view.travel_cm, 0) }),
      field: { label: t("wiz_T064"), hint: expectedHint(t, tx, n, view.expected_cm, view.travel_cm), min: 0, max: 10000, kind: "height" },
      actions: [{ id: "reading", label: t("wiz_T051"), kind: "primary" }, { id: "repeat", label: t("wiz_T033"), kind: "secondary" }] });
    const [, number, title] = readingCodes[view.step];
    return Object.assign(screen, { code: p(number), title: t(title), body: view.step === "check" ? t("wiz_T139") : t("wiz_T082"),
      field: { label: t("wiz_T064"), hint: expectedHint(t, tx, n, view.expected_cm, view.travel_cm), min: 0, max: 10000, kind: "height" },
      actions: [{ id: "reading", label: t("wiz_T051"), kind: "primary" }, { id: "repeat", label: t("wiz_T033"), kind: "secondary" }] });
  }
  if (view.phase === "check_result") {
    const check = view.check, passed = check.passed;
    if (profile) return Object.assign(screen, { code: passed ? "B-07" : "B-07b", title: t(passed ? "wiz_T141" : "wiz_T144"),
      body: tx(passed ? "wiz_T175" : "wiz_T176", { ...values, profile: ctx.cover?.followProfile ?? values.profile, gap: gapText(checkGap(check), ctx.language),
        expected: n(check.predicted_cm, 0), measured: n(check.measured_cm, 0) }),
      actions: passed ? [{ id: "accept", label: t("wiz_T060"), kind: "primary" }, { id: "repeat_check", label: t("wiz_T143"), kind: "secondary" }]
        // No fine-tuning from here: start this cover from scratch (path A, the travel typed kept), another profile,
        // or the check again.
        : [{ id: "restart-a", label: t("wiz_T551"), kind: "primary" }, { id: "other-profile", label: t("wiz_T552"), kind: "secondary" },
          { id: "repeat_check", label: t("wiz_T143"), kind: "secondary" }] });
    const verdict = { gap: gapText(checkGap(check), ctx.language), seconds: n(check.check_seconds) };
    return Object.assign(screen, { code: p(passed ? "32" : "32b"), title: t(passed ? "wiz_T141" : "wiz_T144"),
      body: tx(passed ? "wiz_T142" : "wiz_T145", verdict),
      actions: passed ? [{ id: "accept", label: t("wiz_T060"), kind: "primary" }, { id: "repeat_check", label: t("wiz_T143"), kind: "secondary" }]
        : [{ id: "repeat_check", label: t("wiz_T143"), kind: "primary" }, { id: "redo_readings", label: t("wiz_T146"), kind: "secondary" },
          // Not red (it loses nothing); text links exist only inside «Altre opzioni».
          { id: "continue_anyway", label: t("wiz_T147"), kind: "secondary" }] });
  }
  return Object.assign(screen, { code: "unknown", title: t("loading"), actions: [stop] });
}

/** Before/after of every summary: A-21, A-33, B-08, C-21, C-33, C-36, C-38. */
function summaryScreen(screen, ctx, tx, n, values) {
  const { t, view, path, scope, cover, local = {} } = ctx;
  const effective = cover?.effective || {};
  const refine = view.preview && view.path === "refine", points = view.preview && view.path === "rolls", profileB = view.preview && view.path === "profile";
  // The basic calibration of a cover fine-tuned with the tape is today's «solo i tempi» (C-36, this cover).
  // Ricalibra on a fine-tuned cover is the basic calibration with its profile choice too.
  const timesOnly = path === "C" && scope === "times_only";
  const follows = !!cover?.profile;
  screen.phase = "summary";
  if (!view.preview) screen.notices.push(...discrepancyNotes(ctx, tx, n));
  // What would be applied: the session's values, a profile adapted to this travel (B), or the values in use (readings only).
  let after;
  if (profileB) {
    const chosen = ctx.followed || {};
    const scale = chosen.reference_travel_cm && view.travel_cm ? view.travel_cm / chosen.reference_travel_cm : 1;
    after = { opening: chosen.opening_time * scale, closing: chosen.closing_time * scale, slat: chosen.geometry?.slat_time_s ?? null,
      travel: view.travel_cm, opening_roll: chosen.geometry?.opening_roll ?? null, closing_roll: chosen.geometry?.closing_roll ?? null };
  } else if (view.preview) {
    after = { opening: view.values.opening_time, closing: view.values.closing_time, slat: view.values.slat_time_s ?? null, travel: view.travel_cm,
      opening_roll: view.values.opening_roll ?? null, closing_roll: view.values.closing_roll ?? null };
  } else {
    after = { opening: view.values?.opening_time ?? effective.opening?.value, closing: view.values?.closing_time ?? effective.closing?.value,
      slat: view.mode === "geometry" ? view.geometry?.slat_time_s ?? null : effective.slat_time_s?.value ?? null,
      travel: view.mode === "geometry" ? view.travel_cm : cover?.travel_cm ?? null,
      opening_roll: view.mode === "geometry" ? view.geometry?.opening_roll ?? null : effective.opening_roll?.value ?? null,
      closing_roll: view.mode === "geometry" ? view.geometry?.closing_roll ?? null : effective.closing_roll?.value ?? null };
  }
  // Fine-tuning compares with what the calibration just saved; everything else with the values in use.
  const base = refine && local.base ? local.base : { opening: effective.opening?.value, closing: effective.closing?.value,
    slat: effective.slat_time_s?.value ?? null, travel: cover?.travel_cm ?? null,
    opening_roll: effective.opening_roll?.value ?? null, closing_roll: effective.closing_roll?.value ?? null };
  // A slat time of 0 is a cover without slats: «No», as in the list's details, not «0 s».
  // Times always with one decimal, as the stepper and the tiles.
  const format = (key, value, unit, digits) => value == null ? "—" : key === "slat" && value === 0 ? t("calTabSlatsNone")
    : `${unit === " s" ? n.fixed(value, 1) : n(value, digits)}${unit}`;
  const row = (label, key, unit, digits = 1) => {
    const before = format(key, base[key], unit, digits);
    const shown = format(key, after[key], unit, digits);
    return { label, before, after: shown === before ? "=" : shown, changed: shown !== before && base[key] != null };
  };
  screen.table = { headers: [t("wizTableValue"), t("wizTableBefore"), t("wizTableAfter")],
    rows: [row(t("wiz_T093"), "opening", " s"), row(t("wiz_T094"), "closing", " s"), row(t("wiz_T095"), "slat", " s"), row(t("wiz_T041"), "travel", " cm")] };
  const rolls = [row(t("wiz_T105"), "opening_roll", "", 2), row(t("wiz_T106"), "closing_roll", "", 2)];
  const rollText = { opening_roll: n(after.opening_roll, 2), closing_roll: n(after.closing_roll, 2) };
  screen.technical = { title: t("wizTechnical"), caption: profileB || (timesOnly && after.opening_roll != null) ? tx("wiz_T181", rollText)
    : points ? t("wiz_T239") : t("wiz_T104"), rows: rolls,
    code: profileB || timesOnly ? "" : ["slat_time_s", "opening_roll", "closing_roll"].map((key) => `${key}: ${after[key === "slat_time_s" ? "slat" : key] == null ? "—" : n(after[key === "slat_time_s" ? "slat" : key], key === "slat_time_s" ? 1 : 2)}`).join("\n") };
  if (timesOnly && after.opening_roll == null) screen.technical = null;
  // A gap under 1 cm is said so, not «about 0 cm».
  // A live check (the base calibration's) is a tile with the measured values, as the times are; the preview's
  // check of fine-tuning keeps its sentence. The live check names its gap `deviation_cm`, the preview `gap_cm`.
  const liveCheck = view.check && !view.preview ? checkResult(ctx, tx, n, view.check, view.check_threshold_cm) : null;
  const precision = view.check && !liveCheck ? tx(Number(checkGap(view.check)) < 1 ? "wiz_T513" : "wiz_T151", { gap: gapText(checkGap(view.check), ctx.language), seconds: n(view.check.check_seconds) }) : null;
  const saveInfo = (key) => ({ title: t("wiz_T280"), body: counted(t, key, trueFollowers(cover), values) });
  const save = { id: "save", label: t("wiz_T102"), kind: "primary" };
  // Save says what it saves, and follows the choices made under it (a new profile and its name, the
  // profile followed, or this cover only).
  const saveAs = (target, name) => ({ ...save, target, label: target === "profile_new" ? tx("wiz_T587", { name })
    : target === "profile_update" ? tx("wiz_T588", { name: cover?.profile?.name ?? "" }) : t("wiz_T589") });
  // A preview's summary (path B, tape readings only, fine-tuning) saves nothing in this version: no Save, no
  // profile switch, no «what happens when I save?»: one notice, then «Chiudi» (or another cover).
  if (view.preview) {
    const code = profileB ? "B-08" : points ? "C-38" : path === "C" ? "C-33" : (follows ? "A-33c" : "A-33");
    if (points || refine) { screen.table.mutedBefore = points; if (points) screen.table.rows = [...screen.table.rows, ...rolls]; }
    Object.assign(screen, { code, title: t(profileB ? "wiz_T179" : "wiz_T149"), body: "", precision, profileBlock: null, saveInfo: null,
      actions: [{ id: "close", label: t("wiz_T390"), kind: "primary" }, { id: "another", label: t("wiz_T156"), kind: "secondary" }] });
    screen.notices.push({ tone: "info", result: true, text: tx("wiz_T565", values) });
    return screen;
  }
  if (profileB) {
    const followed = ctx.cover?.followProfile ?? "";
    Object.assign(screen, { code: "B-08", title: t("wiz_T179"), body: tx("wiz_T180", { ...values, profile: followed, travel: n(view.travel_cm, 0) }),
      saveInfo: { title: t("wiz_T280"), body: tx("wiz_T284", { profile: followed }) }, actions: [save] });
    return screen;
  }
  if (timesOnly) {
    // T653 (T233 sent to C-02's «Cosa non va?», no longer entered: fine-tuning with the tape instead).
    Object.assign(screen, { code: "C-36", title: t("wiz_T179"), body: follows ? tx("wiz_T653", values) : t("wiz_T092"),
      saveInfo: follows ? saveInfo("wiz_T287") : saveInfo("wizSaveCoverOnly"), actions: [saveAs("cover_only")] });
    return withSupport(screen, ctx, "cover_only");
  }
  if (path === "base") return basicSummary(screen, ctx, tx, n, values, { base, saveAs });
  if (points) {
    // C-38 shows the values in use in grey: only the rolls change, so they are in the table, not only under
    // «Dettagli tecnici».
    screen.table.mutedBefore = true;
    screen.table.rows = [...screen.table.rows, ...rolls];
    Object.assign(screen, { code: "C-38", title: t("wiz_T149"), body: t("wiz_T238"), precision,
      saveInfo: follows ? saveInfo("wiz_T288") : saveInfo("wizSaveCoverOnly"), actions: [saveAs("cover_only")] });
    return screen;
  }
  // A and C (times and rolls): one Save, with the reusable-profile switch.
  const c = path === "C";
  const block = profileBlock(ctx, tx, c);
  const target = block.target;
  // One screen whatever the profile switch makes its code (A-21, A-21c, A-21d): toggling it keeps the place.
  Object.assign(screen, { family: `summary:${path}:${refine ? "refine" : "base"}`,
    code: c ? (refine ? "C-33" : "C-21") : refine ? (follows ? "A-33c" : "A-33") : follows ? (block.choice ? "A-21d" : "A-21c") : "A-21",
    title: t(refine ? "wiz_T149" : "wiz_T091"),
    body: c ? (follows ? tx(refine ? "wiz_T226" : "wiz_T210", values) : t(refine ? "wiz_T150" : "wiz_T092"))
      : t(refine ? (follows ? "wiz_T152" : "wiz_T150") : (follows ? "wiz_T107" : "wiz_T092")),
    precision, profileBlock: block,
    // The box says exactly what Save does with the choice made: a new profile the cover follows, the profile it
    // follows updated, or (once available) this cover only.
    saveInfo: target === "profile_new" ? { title: t("wiz_T280"), body: tx("wiz_T566", { ...values, name: block.name?.value ?? "" }) }
      : target === "profile_update" ? { title: t("wiz_T280"), body: tx("wiz_T567", { ...values, profile: cover?.profile?.name ?? "" }) }
      : saveInfo("wizSaveCoverOnly"),
    // Fine-tuning is offered once this is saved (A-34), not here. Above the threshold the check can be repeated.
    actions: [saveAs(target, block.name?.value ?? ""), ...(liveCheck && !liveCheck.passed ? [{ id: "check-start", label: t("wiz_T143"), kind: "secondary" }] : [])] });
  withSupport(screen, ctx, target);
  sharedConfirm(screen, ctx, target);
  // After the notices that matter more (what cannot be saved comes first).
  if (liveCheck) screen.results = [...(screen.results || []), liveCheck.tile];
  return screen;
}

/**
 * «Aggiorna il profilo «X»» asks first. Its preview (Home Assistant's `preview_save`)
 * names the other covers whose times change, or says there are none; Save becomes «Conferma e salva nel profilo «X»»,
 * with «Annulla» beside it. Any other choice drops it (the wizard).
 */
/**
 * Of the other covers on the profile (Home Assistant's preview), those whose times
 * updating it changes (`times`); those whose times stay but whose intermediate positions change, the model or a value
 * of its geometry being different (`positions`, never one already in `times`); and apart those that lose their check
 * (`loses_check`). Only the source updates its profile: they are all covers that follow it.
 */
export function sharedImpact(preview, ...self) {
  const others = (preview?.followers || []).filter((item) => !self.includes(item.entity_id));
  const changed = (item) => ["opening", "closing"].some((direction) => item.changes?.[direction]
    && item.changes[direction].before !== item.changes[direction].after);
  // Home Assistant lists the geometry whenever there are slats before or after, changed or not.
  const reshaped = (item) => item.model_before !== item.model_after
    || Object.values(item.geometry_changes || {}).some((value) => value?.before !== value?.after);
  const times = others.filter(changed);
  return { times, positions: others.filter((item) => !times.includes(item) && reshaped(item)),
    checks: others.filter((item) => item.loses_check === true) };
}

export function sharedConfirm(screen, ctx, target) {
  const { t, local = {}, cover, view } = ctx, preview = local.sharedConfirm;
  if (target !== "profile_update" || !preview) return screen;
  const { times, positions, checks } = sharedImpact(preview, cover?.entity_id, view?.entity_id);
  // The names the panel shows; Home Assistant's own only when it is not the entity id.
  const names = (items) => items.map((item) => ctx.coverName?.(item.entity_id) || (item.name && item.name !== item.entity_id ? item.name : null) || t("wiz_T679")).join(", ");
  // For example: «Cambiano i tempi delle tapparelle A, B. Cambiano le posizioni intermedie della tapparella C.
  // Perderà la verifica la tapparella B.» The names said as covers, one («della tapparella X») or more («delle tapparelle X, Y»).
  const text = [times.length ? fill(t(times.length === 1 ? "wiz_T709_one" : "wiz_T709"), { names: names(times) }) : "",
    positions.length ? fill(t(positions.length === 1 ? "wiz_T721_one" : "wiz_T721"), { names: names(positions) }) : "",
    checks.length ? fill(t(checks.length === 1 ? "wiz_T710_one" : "wiz_T710"), { names: names(checks) }) : ""].filter(Boolean).join(" ");
  screen.notices.push({ tone: "info", result: true, text: text || t("wiz_T678") });
  screen.actions = screen.actions.map((action) => action.id === "save" ? { ...action, label: fill(t("wiz_T675"), { name: cover?.profile?.name ?? "" }) } : action);
  screen.actions.push({ id: "shared-cancel", label: t("wiz_T025"), kind: "secondary" });
  return screen;
}

/**
 * A-21b, the basic calibration's summary. «Precisione · Stima di base» first, as a «Misura»
 * whose tail «Nessuna verifica col metro» opens why (no number: a precision is said only after a check); the table of
 * the times and the slat time (no travel: the basic calibration neither measures nor changes it); «Dettagli tecnici» with
 * the neutral coefficients a new profile with slats gets; the profile card of A-21. With slats, a new profile needs the
 * lift-off and one shorter than both runs (with the run of its own a guard the flow no longer reaches):
 * said before Save, which stays off, with «Ricomincia».
 */
function basicSummary(screen, ctx, tx, n, values, { base, saveAs }) {
  const { t, view, cover } = ctx;
  const effective = cover?.effective || {};
  const slats = view.slats === true, lift = view.samples?.lift ?? null;
  const block = profileBlock(ctx, tx, false);
  const target = block.target, creating = target === "profile_new";
  // What Save would apply: the times and the slat time. Into the profile followed too,
  // the lift-off just measured is its slat time (a refinement keeps its own only when no lift-off was measured); without
  // slats 0, but a refinement keeps its geometry.
  const updating = target === "profile_update";
  const after = { opening: view.values?.opening_time ?? effective.opening?.value, closing: view.values?.closing_time ?? effective.closing?.value,
    slat: creating ? (slats ? lift : 0) : updating ? (slats ? lift : refinedCover(cover) ? base.slat : 0) : base.slat };
  const format = (key, value) => value == null ? "—" : key === "slat" && value === 0 ? t("calTabSlatsNone") : `${n.fixed(value, 1)} s`;
  const row = (label, key) => {
    const before = format(key, base[key]), shown = format(key, after[key]);
    return { label, before, after: shown === before ? "=" : shown, changed: shown !== before && base[key] != null };
  };
  screen.table = { headers: [t("wizTableValue"), t("wizTableBefore"), t("wizTableAfter")],
    rows: [row(t("wiz_T093"), "opening"), row(t("wiz_T094"), "closing"), row(t("wiz_T095"), "slat")] };
  screen.technical = creating && slats ? { title: t("wizTechnical"), caption: t("wiz_T645"), rows: [],
    code: [`slat_time_s: ${lift == null ? "—" : n(lift, 1)}`, `opening_roll: ${n(1, 2)}`, `closing_roll: ${n(1, 2)}`].join("\n") } : null;
  // The class this calibration gives, never a number (the maintainer on #10).
  // The class this save gives: on a cover fine-tuned with the tape the rolls stay, so
  // «Calibrata», not «Stima di base».
  const keepsRolls = refinedCover(cover) && (creating || updating);
  screen.results = [{ label: t("wiz_T642"), value: t(keepsRolls ? "calTabStatus_calibrated" : "calTabStatus_basic"), unit: "", tone: "neutral",
    tail: { summary: t("wiz_T643"), text: t(keepsRolls ? "wiz_T698" : "wiz_T644") } }];
  const opening = view.values?.opening_time, closing = view.values?.closing_time;
  const refusal = creating && slats ? (lift == null ? "profileError_lift_missing"
    : opening > 0 && closing > 0 && lift >= Math.min(opening, closing) ? "profileError_lift_not_shorter" : null) : null;
  if (refusal) screen.notices.push({ tone: "error", result: true, text: t(refusal) });
  Object.assign(screen, { family: "summary:base", code: "A-21b", title: t("wiz_T091"), body: t(cover?.profile ? "wiz_T107" : "wiz_T092"), precision: null, profileBlock: block,
    saveInfo: creating ? { title: t("wiz_T280"), body: tx("wiz_T566", { ...values, name: block.name?.value ?? "" }) }
      : target === "profile_update" ? { title: t("wiz_T280"), body: tx("wiz_T567", { ...values, profile: cover?.profile?.name ?? "" }) }
      : { title: t("wiz_T280"), body: counted(t, cover?.profile ? "wiz_T287" : "wizSaveCoverOnly", trueFollowers(cover), values) },
    actions: [{ ...saveAs(target, block.name?.value ?? ""), ...(refusal ? { disabled: true } : {}) },
      ...(refusal ? [{ id: "restart", label: t("wiz_T261"), kind: "secondary" }] : [])] });
  withSupport(screen, ctx, target);
  return sharedConfirm(screen, ctx, target);
}

/**
 * A cover that follows a profile measured on another one does not update it: it saves a profile of
 * its own, and the others keep theirs (or, with none left, the profile is left alone).
 */
function ownProfileLine(t, cover) {
  const others = cover.followers?.length ?? 0;
  return fill(t(others === 0 ? "wiz_T436" : "wiz_T708"), { old: cover.profile.name, tab: t("profileListTitle") });
}

/** Under a new profile's name, for a cover that followed another: what happens to that one (left alone, or kept by others). */
function followLine(t, cover) {
  const others = cover.followers?.length ?? 0, old = cover.profile.name;
  return fill(t(others === 0 ? "wiz_T436" : others === 1 ? "wiz_T438" : "wiz_T437"), { old, count: others, tab: t("profileListTitle") });
}

/** "Also save as reusable profile": on by default for a first cover, off when it already follows a profile. */
function profileBlock(ctx, tx, pathC) {
  const { t, cover, local = {} } = ctx;
  const follows = !!cover?.profile;
  // A session that cannot save on the cover alone (the tape's: a cover's own values hold only the two
  // times) has no switch: the profile followed updated, or a new one.
  const fixed = ctx.supports ? !ctx.supports("cover_only") && ctx.supports("profile_new") : false;
  const on = fixed || (local.saveProfile ?? (!follows && !pathC));
  // The profile is updated only from its source, the cover it was measured on, and only where
  // Home Assistant offers it (`shared`, which it offers to the source alone): there «Aggiorna il profilo «X»» is the
  // default and «Nuovo profilo» the other choice. A cover that follows a profile measured elsewhere has no choice: it
  // saves a profile of its own.
  const source = follows && !!cover.entity_id && cover.profile.provenance?.opening?.origin_entity_id === cover.entity_id;
  const updatable = source && (ctx.supports ? ctx.supports("profile_update") : true);
  const choice = on && updatable ? { name: "save_choice", value: local.saveChoice || "update",
    // Listed as in A-21d: update first, then a new profile.
    options: [{ value: "update", label: tx("wiz_T109", { profile: cover.profile.name }),
      description: counted(t, "wiz_T110", cover.followers?.length ?? 0, { names: ctx.followerNames || "—" }) },
    { value: "new", label: t("wiz_T111") }] } : null;
  const creating = on && (!choice || choice.value === "new");
  // The new profile is named as the cover is shown («Living room shutter 1»), a readable variant
  // when that name is taken («… (2)»).
  const nameValue = local.profileName ?? freeName(cover?.name || "", ctx.profiles || []);
  // Home Assistant accepts any name of 1–64 characters; two profiles with the same name would be told apart by nothing.
  const wanted = nameValue.trim().toLocaleLowerCase();
  const nameError = !wanted ? t("wiz_T435") : (ctx.profiles || []).some((profile) => profile.name?.trim().toLocaleLowerCase() === wanted) ? t("wiz_T434") : "";
  const block = { switchLabel: t(follows ? "wiz_T439" : "wiz_T096"), on, fixed,
    // On the cover the profile was measured on, that, never «segue il profilo».
    explanation: fixed ? (follows ? (updatable ? tx("wiz_T702", { profile: cover.profile.name }) : ownProfileLine(t, cover)) : t("wiz_T097"))
      : follows ? tx(source ? "wiz_T703" : "wiz_T108", { profile: cover.profile.name }) : t("wiz_T097"), choice,
    name: creating ? { label: t("wiz_T098"), value: nameValue, hint: t("wiz_T433"), error: nameError,
      // «Crei il profilo «…».» is not repeated: the primary already says «Salva come «…»» (wiz_T587).
      // Without a choice the line is the cover's own profile, said in the explanation when there is no switch.
      line: !follows ? "" : choice ? followLine(t, cover) : fixed ? "" : ownProfileLine(t, cover),
      valid: !nameError } : null,
    target: !on ? "cover_only" : creating ? "profile_new" : "profile_update" };
  return block;
}

function withSupport(screen, ctx, target) {
  const supported = ctx.supports ? ctx.supports(target) : true;
  if (!supported) {
    // Said at the top of the screen (seen on a phone too), and no help explaining what Save would do.
    // The notice names the way of saving that is not available and what to do instead.
    const text = target === "cover_only" ? fill(ctx.t("wiz_T518"), { switch: ctx.t(ctx.cover?.profile ? "wiz_T439" : "wiz_T096") })
      : ctx.t("wizSaveUnavailable");
    screen.notices.push({ tone: "warning", result: true, text });
    screen.saveInfo = null;
    for (const action of screen.actions) if (action.id === "save") action.disabled = true;
  }
  if (screen.profileBlock?.name && !screen.profileBlock.name.valid) {
    for (const action of screen.actions) if (action.id === "save") action.disabled = true;
  }
  return screen;
}

/**
 * A cover's visible name as a new profile's name: at most 64 characters, and when a saved profile has it
 * (case and spaces aside) the first free «Name (2)», «Name (3)»…, shortened so the number always fits.
 */
export function freeName(name, profiles = []) {
  const base = String(name).trim().slice(0, 64) || "profile";
  const taken = new Set(profiles.map((profile) => profile.name?.trim().toLocaleLowerCase()));
  if (!taken.has(base.toLocaleLowerCase())) return base;
  for (let index = 2; ; index++) {
    const tail = ` (${index})`, candidate = `${base.slice(0, 64 - tail.length).trimEnd()}${tail}`;
    if (!taken.has(candidate.toLocaleLowerCase())) return candidate;
  }
}

/** Profile names: letters, digits and underscores, from the cover name. */
export function slug(text) {
  return text.normalize("NFD").replace(/[̀-ͯ]/g, "").toLocaleLowerCase().replace(/[^a-z0-9]+/g, "_").replace(/^_+|_+$/g, "").slice(0, 64) || "profile";
}

function savedScreen(screen, ctx, tx, values) {
  const { t, path, local = {} } = ctx;
  const saved = local.saved || {};
  // The check's tile and note, as in the summary.
  const n = (value, digits = 1) => number(value, ctx.language || "en", digits);
  const checked = saved.check ? checkResult(ctx, tx, n, saved.check, saved.threshold ?? 4) : null;
  if (checked) screen.results = [checked.tile];
  // "Close" keeps each mockup's own style: B-09 red filled, A-34 red text, C-34 red outlined.
  // The end of a completed flow is not «Chiudi»: «Torna all'elenco», the primary unless fine-tuning is
  // offered (then a secondary); never red.
  const close = (kind = "primary") => ({ id: "close", label: t("wiz_T390"), kind });
  screen.discard = false;
  screen.phase = "done";
  screen.preview = !!saved.preview;
  // The end of a preview never says that something was saved: what was saved for real is said as such.
  if (saved.preview) {
    // The check mark of A-34 says "saved": not drawn at the end of a preview.
    screen.illustration = false;
    return Object.assign(screen, { code: path === "B" ? "B-09" : path === "C" ? "C-34" : "A-34", title: t("wiz_T320"),
      body: local.baseSaved ? t("wiz_T322") : tx("wiz_T321", values),
      // At the end of a preview nothing is lost: on B-09 and C-34 «Chiudi» is the primary, not red (A-34 keeps its
      // primary «another» and Close as red text).
      actions: path === "B" ? [close()]
        : [close(), { id: "another", label: t("wiz_T156"), kind: "secondary" }, { id: "open-cover", label: t("wiz_T157"), kind: "secondary" }] });
  }
  if (path === "B") return Object.assign(screen, { code: "B-09", title: t("wiz_T154"),
    body: tx("wiz_T182", { ...values, profile: ctx.cover?.followProfile ?? "", travel: number(saved.travel_cm, ctx.language, 0) }),
    actions: [close()] });
  // An update of the profile followed says so, and which other covers use it now (by
  // their names, as the confirmation before it names them).
  const others = ctx.cover?.followers?.length ?? 0;
  const body = saved.target === "profile_new" ? tx("wiz_T155", { name: saved.name ?? "" })
    : saved.target === "profile_update" ? tx(others === 0 ? "wiz_T695" : others === 1 ? "wiz_T696" : "wiz_T697", { ...values, names: ctx.followerNames || "" })
    : ["C", "base"].includes(path) && ctx.cover?.profile ? tx("wiz_T228", values) : tx("wizSavedCover", values);
  // A-34's primary is «Torna all'elenco»; fine-tuning (path C on the same cover) is a secondary offered only
  // while it can be run, and only then the body's second paragraph (T319) says the cover will move.
  // A profile was created or updated: it can be given to similar covers at once (the maintainer's Assign dialog).
  // A profile just created has no cover but this one: giving it to others is then the likely next step, kept
  // beside the primary where the bar folds the rest under «Altre opzioni» (phone); an updated profile's stays there.
  // A-35: it opens the step in this frame (never a dialog over the flow); offered only where Home Assistant
  // assigns several covers at once (`assignable`). Once done, A-34 says to how many, and another cover is the next step.
  const assigned = local.assigned || 0, assignFirst = saved.target === "profile_new" && !assigned;
  if (assigned) screen.notices.push({ tone: "success", result: true, text: plural(t, assigned, "wiz_T623", "wiz_T622") });
  const assign = ["profile_new", "profile_update"].includes(saved.target) && ctx.assignable !== false
    ? [{ id: "assign-profile", label: t("wiz_T426"), kind: "secondary", ...(assignFirst ? { visible: true } : {}) }] : [];
  // The secondary in view is the likely next step: giving a new profile to other covers; otherwise (a
  // profile updated, this cover only, path C, the profile already given) another cover.
  const another = { id: "another", label: t("wiz_T156"), kind: "secondary", ...(assignFirst ? {} : { visible: true }) };
  // A new profile left the previous one without covers: it can be deleted from the Profiles tab (never by itself).
  if (saved.target === "profile_new" && saved.orphan) screen.note = { tone: "info", text: fill(t("wiz_T431"), { old: saved.orphan, tab: t("profileListTitle") }) };
  // Another device saved it: the same A-34, read only: «Chiudi» alone.
  // Whatever the other device does next (fine-tuning is a preview with no session), nothing more to follow here.
  if (saved.reader) return Object.assign(screen, { code: path === "C" ? "C-34" : "A-34", title: t("wiz_T154"), body: `${body}\n\n${t("wiz_T569")}`,
    actions: [close()], links: [] });
  // «Torna all'elenco» first (the primary), then giving the profile to other covers, another cover, this
  // cover's details, and fine-tuning last when it can be run. Where the bar is one column (phone, single column) the
  // wizard keeps the primary visible and puts the others under «Altre opzioni ▾» when they are more than three.
  const fineTune = ctx.previews !== false;
  // After a basic calibration, fine-tuning with the tape is offered as an action of the epilogue (not a
  // step: the steps stay on «Riepilogo · 5 di 5»), the secondary in view (T647 names it); giving the profile to other
  // covers goes under «Altre opzioni» / ⋮ with another cover and this cover's details.
  if (path === "base") return Object.assign(screen, { code: "A-34", title: t("wiz_T154"), body: `${body}\n\n${t("wiz_T647")}`,
    // «Perfeziona» with the tape measure, as in the list, its menu and the details.
    actions: [close(), { id: "refine-tape", label: t("calTabActionRefine"), icon: REFINE_ICON, kind: "secondary", visible: true },
      ...assign.map(({ visible, ...action }) => (void visible, action)), { ...another, visible: undefined }, { id: "open-cover", label: t("wiz_T157"), kind: "secondary" }] });
  if (path !== "C") return Object.assign(screen, { code: "A-34", title: t("wiz_T154"), body: fineTune ? `${body}\n\n${t("wiz_T319")}` : body,
    actions: [close(), ...assign, another, { id: "open-cover", label: t("wiz_T157"), kind: "secondary" },
      ...(fineTune ? [{ id: "refine", label: t("wiz_T103"), kind: "secondary" }] : [])] });
  return Object.assign(screen, { code: "C-34", title: t("wiz_T154"), body,
    actions: [close(), another, { id: "open-cover", label: t("wiz_T157"), kind: "secondary" }] });
}

/**
 * A-35: after A-34, the profile just written given to other covers in the same
 * frame: the question, what it does to the times in one line, then the list the wizard draws (search, «N selezionate»,
 * the rows, the refusal under them); «Assegna a N tapparelle» (off while none is chosen) and «Salta» back to A-34. The
 * calibration is saved: the ✕ closes without asking (the selection is all it loses); the scrim, the handle and a drag
 * only while nothing is chosen (the wizard's `_closable`).
 */
function assignScreen(screen, ctx) {
  const { t, local = {} } = ctx, state = local.assign || {}, info = ctx.assign || {};
  const count = (state.selected || []).length;
  screen.discard = false;
  screen.illustration = false;
  // A-35 is not a step: an action of the epilogue in the same frame. The steps stay as on A-34
  // (the summary the last, done), the header says the process and «Altre tapparelle» with no count.
  return Object.assign(screen, { code: "A-35", phase: "done", epilogue: t(PHASES.assign), title: fill(t("wiz_T613"), { profile: info.profile?.name ?? state.name ?? "" }),
    body: info.scaling || "", assign: info,
    // While the question under the list is open its Annulla / Assegna are the only pair: no footer.
    actions: state.confirm ? [] : [{ id: "assign-submit", label: count ? plural(t, count, "wiz_T618", "wiz_T617") : t("wiz_T373"), kind: "primary",
      disabled: !count || count > (info.max ?? 200) || !!state.busy || !!state.confirm || !info.profile },
    { id: "assign-skip", label: t("wiz_T619"), kind: "secondary" }] });
}

/** S-01…S-06: a step that ended. Today's sessions end with their reason (repeating starts over);
 * a published `problem` keeps the earlier measurements and repeats only the step. */
function problemScreen(screen, ctx, reason, step = false) {
  const { t } = ctx;
  const family = { unexpected_movement: ["S-01", "wiz_T240", "wiz_T241", "wiz_T243"], external_command: ["S-01", "wiz_T240", "wiz_T241", "wiz_T243"],
    unexpected_stop: ["S-01", "wiz_T240", "wiz_T241", "wiz_T243"], stopped: ["S-01", "wiz_T240", "wiz_T241", "wiz_T243"],
    not_delivered: ["S-02", "wiz_T244", "wiz_T245", "wiz_T247"], start_timeout: ["S-02", "wiz_T244", "wiz_T245", "wiz_T247"], no_echo: ["S-02", "wiz_T244", "wiz_T245", "wiz_T247"],
    not_stopped: ["S-03", "wiz_T248", "wiz_T249", "wiz_T251"], stop_queue_full: ["S-03", "wiz_T248", "wiz_T249", "wiz_T251"],
    invalid_measurement: ["S-04", "wiz_T252", "wiz_T253", "wiz_T243"], bad_point: ["S-04", "wiz_T252", "wiz_T253", "wiz_T243"],
    // «Ripeti il movimento», the one label of every repetition.
    travel_timeout: ["S-05", "wiz_T254", "wiz_T255", "wiz_T061"], timeout: ["S-05", "wiz_T254", "wiz_T255", "wiz_T061"],
    expired: ["S-06", "wiz_T259", "wiz_T260", "wiz_T261"] }[reason] || ["S-01", "wiz_T240", null, "wiz_T243"];
  const [code, title, body, retry] = family;
  screen.code = code;
  screen.title = t(title);
  if (code === "S-06") {
    screen.body = t(body);
    // As in its mockup, "Close" is outlined in red.
    screen.actions = [{ id: "start-over", label: t("wiz_T261"), kind: "primary" }, { id: "close", label: t("wiz_T390"), kind: "secondary" }];
  } else if (step) {
    // The step alone is repeated: the texts of the mockups describe exactly this.
    screen.body = t(body);
    // The state of the motor (and, for S-05, of the stopwatch) as plain lines under the text, as in the mockups.
    // `elapsed_s` is the name expected from the backend for the time the run had when it was cut off; without it the line is left out.
    const seconds = ctx.view?.problem?.elapsed_s;
    screen.status = [t(code === "S-02" ? "wiz_T246" : code === "S-03" ? "wiz_T250" : code === "S-05" ? "wiz_T256" : "wiz_T242"),
      ...(code === "S-05" && seconds != null ? [fill(t("wiz_T257"), { seconds: number(seconds, ctx.language || "en", 1) })] : [])];
    screen.actions = [{ id: "repeat_step", label: t(retry), kind: "primary" }, { id: "stop", label: t("wiz_T028"), kind: "stop" }];
  } else {
    // Today's backend discards the session: say what it says, and offer to start over. Two cases say more, from what
    // the panel knows: the stop came from "It did not do what it should", or the lift-off run stopped before ②.
    const key = `calReason_${reason}`;
    // The actuator never confirmed the stop: S-01 says it plainly and offers to start over.
    if (reason === "not_stopped") { screen.code = "S-01"; screen.title = t("wiz_T240"); }
    // The ended session's reason is about the measured value (an interval out of range…), not two presses: a
    // title that says only that it is not valid.
    if (code === "S-04") screen.title = t("wiz_T512");
    // A Stop this tab did not send came from another tab or device: said so (the maintainer's «Stop richiesto.»
    // does not say where it came from).
    screen.body = reason === "not_stopped" ? t("wiz_T460") : reason === "stopped" && ctx.local?.misbehaved ? t("wiz_T442")
      : reason === "stopped" && ctx.local && !ctx.local.stopSent ? t("wiz_T524")
      : reason === "unexpected_stop" && ctx.view?.step === "lift" ? t("wiz_T443")
      // Our words for an unexpected stop (the maintainer's text speaks of an endpoint not yet confirmed).
      : reason === "unexpected_stop" ? t("wiz_T577")
        : t(key) === key ? t("calPhase_interrupted") : t(key);
    screen.actions = [{ id: "start-over", label: t("wiz_T261"), kind: "primary" }, { id: "close", label: t("wiz_T390"), kind: "secondary" }];
    // A session interrupted (a Stop from another tab, say) stays open in Home Assistant, holding the cover, until
    // it is cancelled: S-01 says how, in red; «Start over» cancels it too before starting again.
    if (ctx.view && !ctx.view.preview && ctx.view.phase === "interrupted" && ctx.view.recoverable) screen.actions.push({ id: "abort", label: t("wiz_T533"), kind: "danger-outline" });
  }
  // A step's problem keeps the session open: Abandon stays (and asks); an ended session has nothing to abandon.
  screen.discard = step && code !== "S-06";
  return screen;
}

/**
 * A reading at half the opening or closing time: with the session's `reading_range` the hint gives the heights
 * the fit accepts (never a target) and the field refuses the others before sending; otherwise the mockup's rough
 * expectation (T083).
 */
function halfField(ctx, tx, n) {
  const { t, view } = ctx, span = view.reading_range, band = view.reading_band;
  // The field refuses only what cannot be physical (more than the travel plus the tape's error); a reading far
  // from the model is accepted with a warning under the field (`band`, drawn by the wizard while typing).
  // A range, never a single number that sounds exact: the heights the model explains (or the expected height
  // with 7 % of the travel either side), whole centimetres.
  const hint = band?.model_min_cm != null ? tx("wiz_T550", { min: n(Math.floor(band.model_min_cm), 0), max: n(Math.ceil(band.model_max_cm), 0) })
    : expectedHint(t, tx, n, view.expected_cm, view.travel_cm);
  if (!span) return { label: t("wiz_T064"), hint, min: 0.1, max: 10000, kind: "height" };
  return { label: t("wiz_T064"), hint, min: span.min_cm - RANGE_SLACK_CM, max: span.max_cm + RANGE_SLACK_CM, kind: "height",
    travel: view.travel_cm, band: band ? { min: band.min_cm, max: band.max_cm, warning: t("wiz_T530") } : null };
}

/**
 * A check's gap, whichever way the session names it: the preview's `gap_cm`, else the measured
 * and expected heights' difference, else `deviation_cm`; its true value to the tenth (the outcome comes from it, the
 * text from gapText).
 */
export const checkGap = (check) => {
  if (!check) return null;
  if (check.gap_cm != null) return gapTenth(check.gap_cm);
  const expected = check.expected_cm ?? check.target_cm;
  if (check.measured_cm != null && expected != null && Number.isFinite(Number(check.measured_cm)) && Number.isFinite(Number(expected))) return gapTenth(Number(check.measured_cm) - Number(expected));
  return check.deviation_cm != null ? gapTenth(check.deviation_cm) : null;
};

/**
 * The base calibration's check as a result: a tile «Verifica» with its gap, and a note with the heights (the
 * expected one «circa») and what the gap means against the threshold (the session's own, said once in A-21v).
 */
export function checkResult(ctx, tx, n, check, threshold = 4) {
  const gap = checkGap(check);
  if (gap == null) return null;
  const passed = check.passed ?? gapPasses(gap, threshold), values = { measured: n(check.measured_cm), expected: n(check.expected_cm ?? check.target_cm, 0), gap: gapText(gap, ctx.language), threshold: n(threshold, 0) };
  // «Precisione misurata» as a «Misura» in the result's tone (success within the threshold,
  // warning beyond), its tail «A metà corsa, entro/oltre i N cm ammessi» opening the explanation.
  // One decimal below a centimetre.
  return { passed, tile: { label: ctx.t("wiz_T595"), value: gapText(gap, ctx.language), unit: "cm", tone: passed ? "success" : "warning",
    tail: { summary: tx(passed ? "wiz_T596" : "wiz_T597", values), text: `${tx("wiz_T561", values)} ${tx(passed ? "wiz_T562" : "wiz_T563", values)}` } } };
}

/** The two stages of the check's wait: to the end stop (its time), then the timed run to its target (planned). */
function checkStages(tx, n, view, endTime, first) {
  const up = view.check?.direction !== "opening", whole = (seconds) => n(Math.ceil((seconds ?? 0) - 0.001), 0);
  return [{ text: tx(up ? "wiz_T084" : "wiz_T120", { seconds: whole(endTime) }), state: first ? "current" : "done" },
    { text: tx(up ? "wiz_T559" : "wiz_T560", { seconds: whole(view.check?.planned_seconds) }), state: first ? "future" : "current" }];
}

/** The hint of a reading with one expected height: «usually between» it ± 7 % of the travel (whole cm). */
function expectedHint(t, tx, n, expected, travel) {
  if (expected == null) return t("wiz_T491");
  const spread = (travel > 0 ? travel : expected * 2) * 0.07;
  return tx("wiz_T550", { min: n(Math.max(0, Math.floor(expected - spread)), 0), max: n(Math.ceil(travel > 0 ? Math.min(travel, expected + spread) : expected + spread), 0) });
}

/**
 * The intermediate readings kept although far from the model, as notes in the summary. A check that passed
 * has already answered (no note); one beyond its threshold may be explained by them (T532); without a check result
 * nothing can say whether the positions are right (T578).
 */
function discrepancyNotes(ctx, tx, n) {
  const { t, view } = ctx, items = view?.reading_discrepancy || {};
  const check = view?.check, gap = check && (check.measured_cm != null || check.gap_cm != null) ? checkGap(check) : null;
  if (gap != null && Number(gap) <= (view.check_threshold_cm ?? 4)) return [];
  const key = gap != null ? "wiz_T532" : "wiz_T578";
  return ["half_open", "half_close"].filter((step) => items[step]).map((step) => ({ tone: "warning", result: true,
    text: tx(key, { which: t(step === "half_open" ? "wiz_T077" : "wiz_T078"), reading: n(items[step].reading_cm), expected: n(items[step].expected_cm, 0) }) }));
}

function runWatch(t, n, view, elapsed) {
  if (!MEASURED_RUN(view)) return null;
  // Stopped by itself: the stopwatch stays at the time of the stop frame ("Stopwatch: stopped at 22.2 s").
  const frozen = MOTOR_STOPPED(view) && view.stopped_elapsed != null;
  if (!frozen && elapsed == null) return null;
  // One decimal always: the value keeps its length while it counts (and when it freezes).
  const [label, value] = splitLabel(frozen ? fill(t("wiz_T257"), { seconds: n.fixed(view.stopped_elapsed) }) : fill(t("wiz_T044"), { seconds: n.fixed(elapsed) }));
  return { label, value };
}

/** "Motor: moving for 4.0 s" → ["Motor", "moving for 4.0 s"], as the box shows them. */
export function splitLabel(text) {
  const index = text.indexOf(": ");
  return index < 0 ? ["", text] : [text.slice(0, index), text.slice(index + 2)];
}

/**
 * The bar of a wait: the share of `seconds` already run (0 until the movement is under way) and the seconds left.
 * Without a known duration (a never measured cover) `value` and `remaining` are null: the bar is indeterminate.
 */
function waitBar(view, elapsed, seconds) {
  // The motor stopped before the expected time: the run is over, the bar is complete and no time is left.
  // So too while the bus confirms a Stop (the bar never jumps back to empty).
  if (MOTOR_STOPPED(view) || view.phase === "geometry_wait_stop") return { value: 1, remaining: null };
  if (!(seconds > 0)) return { value: null, remaining: null };
  // The session's runs back fill their bar as they run, `rehoming` too.
  const done = ["opening", "closing", "reclosing", "reopening", "rehoming"].includes(view.phase) && elapsed != null ? Math.min(seconds, Math.max(0, elapsed)) : 0;
  // The estimate run, the bar is complete and says no time left: the instant its confirm comes on.
  if (done >= seconds) return { value: 1, remaining: null };
  return { value: done / seconds, remaining: secondsLeft(seconds - done) };
}


/** Whole seconds left, never 0 while the bar is shown (sums of tenths are not exact: 11.000000001 is 11). */
function secondsLeft(seconds) { return Math.max(1, Math.ceil(seconds - 0.001)); }

/** A stop frame before this share of the estimated run is "earlier than expected". */
const EARLY_STOP_SHARE = 0.6;

/**
 * When the panel writes the Stop of the close before a times-only measurement (C-04): its estimated closing
 * time and a margin, as an action on the motor (not a gate of the screen) deserves: max(4 s, 25 %): a few
 * seconds more cost nothing, an early Stop would leave the cover short of the bottom and block the measurement.
 */
export const homeStopAt = (seconds) => (seconds > 0 ? seconds + Math.max(4, seconds * 0.25) : null);

/**
 * A return inside a session lasts what the session measured, with a margin: max(2 s, 25 %) (a few
 * seconds cost nothing; «avanti» comes on at the earliest of the bus's stop and this estimate), never longer than the
 * estimate of the whole run it replaces (`full`): a return is never planned longer than before.
 */
export const sessionReturn = (seconds, full = null) => {
  if (!(seconds > 0)) return full;
  const planned = seconds + Math.max(2, seconds * 0.25);
  return full > 0 ? Math.min(planned, full) : planned;
};
/** «Arresto in corso…» shows only once the wait for the actuator's stop lasts this long (ms). */
export const STOP_NOTE_MS = 500;

/** A positioning to an end stop the cover already sits at: the bar runs this long, until the bus confirms the stop. */
const ALREADY_THERE_SECONDS = 2;

/** The full time of a positioning run: the time in use, the integration's default included. */
function timeInUse(entry) {
  return entry && entry.value > 0 ? entry.value : null;
}

/** A time in use counts as known once measured or configured; the integration's default is only a guess. */
function knownTime(entry) {
  return entry && entry.value > 0 && entry.origin !== "default" ? entry.value : null;
}

/**
 * The drawing of each mockup, by the number of its code (C-05…C-34 are the clones of A): its illustration id and
 * where it sits.
 * On every step with a drawing the order is title → drawing → text → field → its message → its help (a
 * running step's stopwatch and a result's notice stay with the title, above the drawing). Every other screen has none
 * (A-09 too: its frame shows only the wait box). `ILLUSTRATED` is the inventory.
 */
const BEFORE = "after-title";
const ILLUSTRATION_OF = {
  A: { "05": ["window-closed", BEFORE], "10": ["window-closed", BEFORE], "06": ["two-runs", BEFORE], "07": ["lift-sequence", BEFORE], "08": ["lift-zoom", BEFORE],
    "11": ["ascent-sequence", BEFORE], "13": ["curtain-open-tape", BEFORE], "14": ["descent-sequence", BEFORE],
    "16": ["curtain-partial-tape", BEFORE], "18": ["curtain-partial-tape", BEFORE], "20": ["curtain-partial-tape", BEFORE],
    "23": ["curtain-partial-tape", BEFORE], "25": ["curtain-partial-tape", BEFORE], "27": ["curtain-partial-tape", BEFORE],
    "29": ["curtain-partial-tape", BEFORE], "31": ["curtain-partial-tape", BEFORE], "34": ["check", BEFORE] },
  B: { "02": ["curtain-open-tape", BEFORE], "06": ["curtain-partial-tape", BEFORE] },
};
ILLUSTRATION_OF.C = ILLUSTRATION_OF.A;
/** Every code that draws an illustration, and where (A-21v, the check, sets its own: after its title too). */
export const ILLUSTRATED = Object.freeze(Object.fromEntries(Object.entries(ILLUSTRATION_OF).flatMap(([letter, entries]) =>
  Object.entries(entries).map(([number, [id, position]]) => [`${letter}-${number}`, { id, position }]))));

/**
 * The drawing that fits the moment. A reading whose expected height is known shows the edge at the
 * closest of a quarter, half and three quarters of the travel (B-06, the readings of fine-tuning and of «solo
 * letture», A-18/A-20); A-23, the quarter on the way up, its own ascent drawing; without slats the full ascent
 * and descent show a curtain with no slats.
 */
function drawingFor(code, id, view, local, drawSlats) {
  const slats = drawSlats ?? (view && !view.preview ? view.slats !== false : local?.slats !== false);
  if (!slats && /^[AC]-11$/.test(code)) return "ascent-sequence-no-slats";
  if (!slats && /^[AC]-14$/.test(code)) return "descent-sequence-no-slats";
  if (id !== "curtain-partial-tape") return id;
  // Every reading uses the drawing of its height without an arrow, A-23 too (the arrow added nothing).
  if (/^[AC]-23$/.test(code)) return "curtain-tape-quarter";
  const range = view?.reading_range, travel = view?.travel_cm;
  const expected = view?.expected_cm ?? view?.check?.target_cm ?? (range ? (range.min_cm + range.max_cm) / 2 : null);
  if (!(expected > 0) || !(travel > 0)) return id;
  const share = expected / travel;
  return share < 0.375 ? "curtain-tape-quarter" : share > 0.625 ? "curtain-tape-three-quarters" : "curtain-tape-half";
}

function illustrationFor(code) {
  const [, letter, number] = /^([ABC])-(\d+)$/.exec(code || "") || [];
  const entry = letter && ILLUSTRATION_OF[letter][number];
  return entry ? { id: entry[0], position: entry[1] } : null;
}

/** Header line, stepper, read-only and connection state: the same on every screen. */
function finalize(screen, ctx) {
  const { t, language = "en", path = "A", scope, view, status = {}, local = {} } = ctx;
  // A screen may opt out (false) where its code is shared with a run the drawing does not show.
  screen.illustration = screen.illustration === false ? null : screen.illustration || illustrationFor(screen.code);
  if (screen.illustration) screen.illustration = { ...screen.illustration, id: drawingFor(screen.code, screen.illustration.id, view, local, screen.drawSlats), ...(screen.illustrationSize ? { size: screen.illustrationSize } : {}) };
  const n = (value, digits = 1) => number(value, language, digits);
  // Under a wait's bar, the time left when its duration is known.
  if (screen.wait) screen.wait.remaining = screen.wait.progress?.remaining != null ? fill(t("wiz_T402"), { seconds: n(screen.wait.progress.remaining, 0) }) : "";
  const refining = (view?.preview && view.path === "refine") || local.refining;
  if (["choose", "path"].includes(ctx.stage)) screen.caption = "";
  // Before the path is known (the preamble: A-01, A-02, B-01, C-01, C-02) there is no stepper at
  // all; from the first real step on, the whole path with «Scelta del percorso» as its first step, done.
  // On a choice (A-02, B-01, C-02) as soon as an option is selected, preselected included, the stepper shows its whole
  // path with «Scelta del percorso» the current step, and follows the option chosen.
  const preamble = PREAMBLE.includes(ctx.stage) && !screen.stepChoice;
  // «Altre tapparelle» (A-35) is never a step: the steps end with the summary (and fine-tuning when offered).
  // The basic calibration has the step «Lamelle» when the session runs with slats, or, before it starts, when the
  // slats question is answered «Sì» (preselected from the last calibration; never on a cover fine-tuned).
  const live = view && !view.preview ? view : null;
  const liftStepShown = (screen.stepPath || path) === "base" && (live ? live.slats === true
    : ["choose", "path", "before"].includes(ctx.stage) ? (local.slats ?? slatsQuestion(ctx.cover, "base", local).value ?? true) !== false
      : !!local.sessionSlats);
  // Fine-tuning on the saved times has its own steps («R»); the runs to the end stops between its
  // readings are part of «Letture». When nothing was chosen (a cover's own action, or a cover never
  // calibrated), there is no «Scelta del percorso»: the steps start at «Preparazione».
  const keepTimes = !!(local.keepTimes || view?.keep_times);
  if (keepTimes && ["ascent", "descent"].includes(screen.phase)) screen.phase = "readings";
  const phases = phasesFor(keepTimes && !screen.stepChoice ? "R" : screen.stepPath || path, screen.stepScope ?? scope, refining, ctx.previews !== false, liftStepShown)
    .filter((key) => key !== "intro" || !local.noChoice);
  let current = phases.indexOf(screen.phase);
  if (refining && screen.phase === "summary") current = phases.length - 1;
  // The end of a preview saved nothing: its summary step stays the current one, not ticked as done.
  const previewEnd = screen.phase === "done" && screen.preview;
  // Perfeziona's end keeps the steps as its summary left them, the summary the current step.
  if (screen.phase === "done") current = previewEnd || keepTimes ? phases.indexOf("summary") : phases.length;
  const source = view && !view.preview ? view : null;
  const base = local.base || {};
  const values = source?.values || {}, samples = source?.samples || {}, readings = { ...(local.baseReadings || {}), ...(source?.readings || {}) };
  // Times always with one decimal («5,0 s» beside «4,9 s»).
  const seconds = (value) => value == null ? "—" : `${fixed(value, language, 1)} s`, cm = (value) => value == null ? "—" : `${n(value)} cm`;
  // A time judged not valid, or discarded by its repeat and not yet measured again, is not shown as a result.
  const kept = (step, value) => local.invalidRun?.step === step || (local.discarded?.step === step && local.discarded.value === value) ? null : value;
  const slats = source ? source.slats !== false : local.slats !== false;
  // The guided runs (the basic calibration, path C's times only): the slats as the session runs them (its `slats`, the
  // tap at the lift-off), once it is closed as it was started.
  const timed = path === "base" || (path === "C" && scope === "times_only");
  const sub = {
    // The path chosen ("Ⓐ first of its type") is the introduction's sub-line, as the mockups' stepper shows it.
    intro: screen.caption ? [["", screen.caption]] : [],
    // The glossary's labels, as the tiles of the steps: «Tempo di stacco», «Sollevamento», «Tempo di
    // salita», «Corsa del telo», «Tempo di discesa», «Precisione».
    // The basic calibration's ascent is its time alone (the lift-off has its own step); no
    // lift, no travel (none is measured).
    ascent: [...(timed ? []
      : slats ? [[t("wiz_T602"), seconds(samples.lift ?? base.lift)],
        [t("wiz_T603"), (samples.lift ?? base.lift) != null && (readings.gap ?? base.gap) != null ? cm(readings.gap ?? base.gap) : "—"]] : []),
      [t("wiz_T093"), seconds(kept("opening", values.opening_time ?? base.opening))],
      // The travel typed and not yet sent is shown as soon as it is known, as its tile does.
      ...(timed ? [] : [[t("wiz_T041"), cm(local.travelTyped ?? source?.travel_cm ?? base.travel)]])],
    descent: [[t("wiz_T094"), seconds(kept("closing", values.closing_time ?? base.closing))]],
    // The lift-off run's time once taken, as A-21b's «Tempo lamelle» (A-34: the one saved). «—»
    // before it is taken, as the ascent's and the descent's times.
    slats: [[t("wiz_T095"), seconds(source ? source.samples?.lift : local.review?.lift)]],
    readings: path === "B" ? [[t("wiz_T041"), cm(view?.travel_cm ?? local.fineReadings?.travel)],
      [t("wiz_T119"), (view?.check ?? local.fineReadings?.check) ? `${gapText(checkGap(view?.check ?? local.fineReadings.check), language)} cm` : "—"]]
      : path === "C" && scope === "points_only" ? fineSub(t, view, cm, local.fineReadings, language)
        // On the saved times the readings in the order they are taken: the lift-off, the travel, half the
        // descent from the top, half the ascent from the bottom.
        : keepTimes ? [...(slats ? [[t("wiz_T603"), readings.gap != null ? cm(readings.gap) : "—"]] : []), [t("wiz_T041"), cm(view?.travel_cm ?? local.travelTyped ?? local.saved?.travel_cm)],
          [t("wiz_T078"), cm(readings.half_close)], [t("wiz_T077"), cm(readings.half_open)]]
        : [[t("wiz_T077"), cm(readings.half_open)], [t("wiz_T078"), cm(readings.half_close)]],
    // The check's result once done, as the summary's precision says it.
    // (On A-34 the session is closed: the check kept with what was saved.)
    check: checkGap(source?.check ?? local.saved?.check) != null ? [[t("wiz_T570"), `${gapText(checkGap(source?.check ?? local.saved.check), language)} cm`]] : [],
    finetune: refining ? fineSub(t, view, cm, local.fineReadings, language) : [],
  };
  const finetuneLabel = refining ? t("wiz_T114") : t("wiz_T013");
  // On A-01, while the cover is being chosen, the first step says so; «Scelta del percorso» after.
  const introLabel = ctx.stage === "choose" ? t("wiz_T007_cover") : t(PHASES.intro);
  screen.stepper = {
    interrupted: !!screen.interrupted,
    // None in the preamble (the path decides the steps). After a real save the line names the last step completed, as every other line names its step
    // («Riepilogo · 7 di 8» on path A, whose optional fine-tuning stays ahead; «Riepilogo · N di N» elsewhere).
    line: previewEnd ? t("wiz_T517") : screen.phase === "done" ? fill(t("wizPhaseLine"), { phase: t(PHASES.summary), n: phases.indexOf("summary") + 1, total: phases.length })
      : preamble ? ""
      : fill(t(screen.interrupted ? "wiz_T461" : "wizPhaseLine"), { phase: screen.phase === "finetune" ? t("wiz_T114") : t(PHASES[screen.phase] || "wiz_T007"),
      n: current + 1, total: phases.length }),
    phases: phases.map((key, index) => ({ key, label: key === "finetune" ? finetuneLabel : key === "intro" ? introLabel : t(PHASES[key]),
      // A-34 after a real save: fine-tuning (optional) is still ahead, so it is not ticked.
      state: key === "finetune" && ["A-34", "A-35"].includes(screen.code) && !screen.preview && !refining ? "future"
        : index < current ? "done" : index === current ? "current" : "future",
      substeps: index <= current ? (sub[key] || []).map(([label, value]) => ({ label, value })) : [] })),
  };
  // The phone bar: the segmented line under the header, one segment per step in the steps' own states.
  screen.stepper.segments = screen.stepper.phases.map((phase) => phase.state);
  // On A-35 the header names the epilogue alone, no count («Calibra · Altre tapparelle»).
  screen.stepper.barLine = screen.epilogue || screen.stepper.line;
  // Which process this is, before the step in the header («Perfeziona · Preparazione · 2 di 5»), and as the
  // medium dialog's column title with its variant under it («Perfeziona», «Solo i tempi»): Calibra or Ricalibra (by the
  // cover's state when it was chosen), Perfeziona (path C, fine-tuning), Segui (another device guides it).
  const following = !!view?.read_only && !view.preview;
  // On A-01, once a cover is chosen, the process is the one the list advises for it (the rule that preselects
  // A-02's option): «Perfeziona» where fine-tuning is advised, else Calibra or Ricalibra by its state. A choice made
  // further on and left with «Indietro» no longer counts; picking another cover follows the new one.
  const chosenHere = ctx.stage === "choose" && local.coverPicked && ctx.cover ? ctx.cover : null;
  const startStatus = chosenHere ? chosenHere.status : local.startStatus ?? ctx.cover?.status;
  // Fine-tuning with the tape entered from a cover's «Perfeziona» or from A-34 after a basic calibration
  // (`refineEntry`) is «Perfeziona»; from A-02 it is the cover's Calibra or Ricalibra, as the basic calibration.
  // On A-02 the option chosen or preselected decides: «Perfezionamento con il metro» (`R`) is «Perfeziona»,
  // as the list's button; any other keeps the cover's Calibra or Ricalibra.
  const refineShown = chosenHere ? advisedStep(chosenHere).path === "R" : screen.code === "A-02" ? screen.stepPath === "R" : refining || local.refineEntry || (screen.stepPath || path) === "C";
  screen.stepper.process = t(following ? "wizProcessFollow" : refineShown ? "calTabActionRefine"
    : startStatus && startStatus !== "to_measure" ? "calTabActionRemeasure" : "calTabActionMeasure");
  screen.stepper.variant = refining ? "" : screen.caption || "";
  // The preamble has no stepper (no segments, no «N di M», no «Passi»); the medium dialog's column shows its one
  // step, «Scelta del percorso», alone.
  if (preamble) Object.assign(screen.stepper, { hidden: true, segments: [], barLine: "",
    phases: [{ key: "intro", label: introLabel, state: "current", substeps: [] }] });
  // A reader (another tab or device guides it, or this one handed control over) sees the whole step: its
  // texts, results, notes and field (read only). Its bar holds only what it can do: the note saying who guides
  // it, «Prendi il controllo» and, while the cover moves, «Ferma la tapparella» (a safety control for every device).
  // The owner's buttons are not drawn; the reader's note says who guides and confirms the steps (once, there).
  // No Stop at rest, whoever looks. Within a movement each step still decides (once a run to an end stop has
  // run out, its state replaces Stop), and the reader keeps exactly the owner's decision.
  // The close Home Assistant makes before a session (no session yet) is a movement too.
  const moving = IN_MOTION(view, status) || !!(local.homing && !view);
  // A pinned Stop (bars with the ①/② pair) stays in its place, off while nothing moves; any other Stop only
  // while something moves.
  screen.actions = screen.actions.filter((action) => action.id !== "stop" || moving || action.pinned)
    .map((action) => (action.id === "stop" && action.pinned ? { ...action, disabled: !moving } : action));
  // A reader never restarts or cancels: on an interrupted session it keeps the reader's bar too, with «Torna
  // all'elenco»; «Prendi il controllo» only while Home Assistant keeps the session (`recoverable`, within its lease).
  // «Ricomincia» and «Annulla la misurazione» are the owner's, after a claim.
  const readerEnded = !!view?.read_only && !view.preview && view.phase === "interrupted";
  if (view?.read_only && (!ENDED.includes(view.phase) || readerEnded)) {
    const claimable = !readerEnded || !!view.recoverable;
    // The armed confirmation counts its seconds down (`left`) before it goes back.
    screen.readOnly = { text: t("wiz_T263"), take: !claimable ? null : status.confirmTake ? t("calConfirmTakeControl") : t("wiz_T264"),
      left: status.confirmTake && status.confirmLeft ? status.confirmLeft : null, consequence: claimable && status.confirmTake ? t("wiz_T265") : null };
    // The run's state that replaces Stop in place («La corsa è finita») is the reader's too: its bar keeps its slot.
    screen.actions = screen.actions.filter((action) => ["stop", "stopped"].includes(action.id) || (readerEnded && action.id === "close"));
    // The header keeps the reader's «Smetti di seguire».
    if (readerEnded) screen.discard = true;
    screen.links = [];
    if (screen.field) screen.field = { ...screen.field, disabled: true };
  }
  // Alone in a wait, Stop is the red filled button of the mockups; beside a step's buttons it stays a red link.
  // Stop is outlined red everywhere, alone in a wait too (one rule; no red is filled).
  // The backend hands the cover back after a stretch without any step (600 s after a movement; heartbeats do not
  // extend it). Two minutes before, a screen that waits for the user says so, counting the minutes down.
  if (view && !view.preview && view.idle_expires_at && !status.expired && !ENDED.includes(view.phase) && !MOVING(view)) {
    const left = Date.parse(view.idle_expires_at) - (ctx.now ?? Date.now());
    // Under two minutes the time left counts down in minutes and seconds (the wizard updates only that text).
    if (left > 0 && left <= LEASE_WARNING_MS) screen.notices.unshift({ tone: "warning", result: true, text: fill(t("wiz_T541"), { time: clock(left) }),
      countdown: { expires: Date.parse(view.idle_expires_at), template: t("wiz_T541") } });
  }
  if (status.lost && !status.expired && view && !ENDED.includes(view.phase)) {
    // A missed heartbeat (the phone put the page aside, the connection dropped for a moment) is a calm status while the
    // panel reconnects; a lost presence for any other reason stays an error.
    // «Riprendi» belongs to that notice, in the content: the bar of the step stays as it is.
    screen.notices.push({ ...(status.connection === "interrupted" ? { tone: "info", text: t("wiz_T446") } : { tone: "error", text: t("calVisual_disconnected") }),
      ...(view.recoverable ? { action: { id: "reconnect", label: t("calResume") } } : {}) });
  }
  if (status.notice) screen.notices.push({ tone: "warning", text: t(status.notice) });
  if (status.cancelPending) screen.notices.push({ tone: "error", text: t("calCancelFailed") });
  if (status.error) {
    // Our own refusal of a reading above the travel, in our words.
    const code = status.error.code;
    // Our own words for some refusals (a reading above the travel, a check the cover cannot make).
    const key = OUR_REFUSALS[code] || `profileError_${code}`;
    const values = { ...(view || {}), ...(readingRange(view?.reading_range, language, status.error.reading) || {}),
      ...(code === "reading_over_travel" ? { reading: n(status.error.reading), travel: n(view?.travel_cm) } : {}), code };
    // The connection's text only when nothing came back (no code, or the socket's own number); a refusal Home
    // Assistant sent with a code of its own, even one without a text here, says so and names it.
    const transport = code == null || typeof code === "number";
    const text = t(key) !== key ? fill(t(key), values) : view?.preview ? t("calReadingNumber") : transport ? t("calConnectionError") : fill(t("wiz_T574"), values);
    // A refusal whose values are gone (the session moved on) is not shown with empty placeholders.
    // A refusal the screen already says (A-21b's lift refusals, the lift-off run's `lift_not_shorter`; an ended
    // run's own reason) is not said twice.
    const said = screen.notices.some((notice) => notice.text === text) || (screen.interrupted && code === view?.reason);
    if (!/\{\w+\}/.test(text) && !said) screen.notices.push({ tone: "error", text });
  }
  // A reading refused in the field, before anything is sent.
  // Outside the field's own limits (or not a number) the field says so under itself.
  // (A reading outside the fit's range keeps its notice with the accepted range.)
  if (local.fieldError != null && screen.field && !(screen.field.range && typeof local.fieldError === "number")) {
    const value = local.fieldError, { min, max } = screen.field;
    // A field with its own words for a value out of its range (the lift-off: «ripeti il movimento») says them.
    screen.field.error = value === "nan" ? t("wiz_T472") : (value > max || value < min) && screen.field.rangeError ? screen.field.rangeError
      : value > max && screen.field.travel != null ? fill(t("wiz_T531"), { reading: n(value), travel: n(screen.field.travel) })
      : value > max ? fill(t("wiz_T470"), { max: n(max) })
      : value < min ? fill(t("wiz_T471"), { min: n(min) }) : null;
  }
  if (typeof local.fieldError === "number" && screen.field?.range) {
    screen.notices.push({ tone: "error", text: fill(t("profileError_reading_out_of_range"), readingRange(view.reading_range, language, local.fieldError)) });
  }
  // A button off while the motor starts or the backend settles after a run enables by itself.
  if (view && (/^starting_/.test(view.phase) || view.phase === "geometry_wait_stop")) for (const action of screen.actions) if (action.disabled) action.settling = true;
  // «Arresto in corso…» only once the wait for the actuator's stop lasts (STOP_NOTE_MS), never as a flash.
  if (view && !view.preview && view.phase === "geometry_wait_stop" && local.waitStopAt != null && (ctx.now ?? Date.now()) - local.waitStopAt >= STOP_NOTE_MS) {
    screen.notices.push({ tone: "info", text: t("wiz_T459") });
  }
  // Movement, readings and Save wait for an answer, a connection and ownership; Stop never does (also a passing state),
  // nor «Non ha fatto quello che doveva», a Stop too.
  if (status.locked) for (const action of screen.actions) if (!["stop", "misbehaved", "reconnect", "close", "start-over", "abort"].includes(action.id)) { if (!action.disabled) action.settling = true; action.disabled = true; }
  if (status.locked && screen.field) screen.field = { ...screen.field, disabled: true };
  return screen;
}

function fineSub(t, view, cm, kept = {}, language = "en") {
  const readings = view?.preview ? view.readings || {} : kept || {};
  const check = view?.preview ? view.check : kept?.check;
  return [[t("wiz_T115"), cm(readings.quarter_open)], [t("wiz_T116"), cm(readings.three_quarter_open)],
    [t("wiz_T117"), cm(readings.quarter_close)], [t("wiz_T118"), cm(readings.three_quarter_close)],
    [t("wiz_T119"), check ? `${gapText(checkGap(check), language)} cm` : "—"]];
}
