/**
 * Which screen of the calibration mockups (A-xx, B-xx, C-xx, S-xx) a moment of the wizard is.
 *
 * Pure: from the wizard's local choices and the session view it returns plain data (texts already
 * filled, fields, buttons, stepper), and the wizard only draws it. Text keys are those of the
 * shared translation sheet (`wiz_T…`); a few screen-only keys are `wiz…`.
 */
const asset = (name) => { const url = new URL(name, import.meta.url); url.search = new URL(import.meta.url).search; return url.href; };
const { fill, number, fixed, readingRange, RANGE_SLACK_CM, dateText, shortDate, followedBy } = await import(asset("panel-calibration-text.js"));

const PHASES = {
  intro: "wiz_T007", prep: "wiz_T008", ascent: "wiz_T009", descent: "wiz_T010", readings: "wiz_T011", check: "wiz_T556", summary: "wiz_T012", finetune: "wiz_T013",
};

/** The phases each path shows, in order (C depends on what is remeasured). */
export function phasesFor(path, scope, refining, previews = true) {
  if (path === "B") return ["intro", "readings", "summary"];
  if (path === "C" && scope === "times_only") return ["intro", "prep", "ascent", "descent", "summary"];
  if (path === "C" && scope === "points_only") return ["intro", "prep", "readings", "summary"];
  // N-10: fine-tuning's own summary is part of «Perfezionamento» (the last step), «Riepilogo» once. L-43: the base
  // calibration's check is a step of its own, between the readings and the summary (fine-tuning has its own check).
  if (refining) return ["intro", "prep", "ascent", "descent", "readings", "summary", "finetune"];
  // Review P2: fine-tuning is a step only while it can be run (the preview paths on).
  return ["intro", "prep", "ascent", "descent", "readings", "check", "summary", ...(previews ? ["finetune"] : [])];
}

const MEASURED_RUN = (view) => view && ((view.mode === "geometry" && ["opening", "closing"].includes(view.phase) && ["lift", "opening", "closing"].includes(view.step))
  || (view.mode === "guided" && ["opening", "closing"].includes(view.phase)));

/**
 * `motor_stopped` (from the backend lot v2-endpoint): in a run the user confirms (the closings to the base `home` and
 * `reset`, the full ascent and descent, the return to the top, the guided runs) the motor has stopped (a stop frame
 * from the bus, real or the actuator's own timer) and the session waits for the confirmation; `stopped_elapsed` is
 * the run's time at that frame. Without the field the run counts as moving.
 */
const MOTOR_STOPPED = (view) => view?.motor_stopped === true;
/** A stop frame between these times is an actuator's timer (about 60 s), never the real end of the run. */
export const TIMER_STOP_SECONDS = [59, 65];
/** Why a full run is not valid: stopped at the actuator's timer, or by anything else before ② (L-3). */
export const stopVerdict = (seconds) => seconds >= TIMER_STOP_SECONDS[0] && seconds <= TIMER_STOP_SECONDS[1] ? "timer" : "late";
/**
 * L-3: ② exists only while the motor runs. A full run whose motor stopped before ② was pressed in this tab (the
 * actuator's timer, a wall switch, any stop) is not valid at once, in the live view and on a resumed one: its result
 * is struck through and only «Ripeti la corsa» goes on. A press of ② made before the stop reached the tab is in flight
 * and decides by itself (`local.fullRun.pressed`).
 */
const STOPPED_BEFORE_PRESS = (view, local) => MOTOR_STOPPED(view) && view.stopped_elapsed != null && !local?.fullRun?.pressed;
const invalidNotice = (t, reason) => ({ tone: "error", result: true, text: t(reason === "timer" ? "wiz_T445" : "wiz_T444") });

/** L-24: a time left as minutes and seconds («1:43»), rounded up to the second. */
export const clock = (ms) => { const total = Math.max(0, Math.ceil(ms / 1000)); return `${Math.floor(total / 60)}:${String(total % 60).padStart(2, "0")}`; };

/** Movements in progress or about to start: Stop is the action that matters. */
export const MOVING = (view) => !!view && !MOTOR_STOPPED(view)
  && (["opening", "closing", "geometry_wait_stop", "positioning"].includes(view.phase) || view.phase?.startsWith("starting_"));
/**
 * L-77: THE rule for the red Stop, for the owner and the reader alike: a movement is in progress (the session's run
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
/** How long before the session's idle expiry the wizard warns (M3). */
export const LEASE_WARNING_MS = 120000;

/** Texts with a count: the zero and one forms of the sheet's plural texts (PROVVISORIO rows T305–T317; T307 and T310–T313 retired with T112, T285 and T286). */
const PLURALS = { wiz_T110: ["wiz_T305", "wiz_T306"], wiz_T165: ["wiz_T308", "wiz_T309"],
  wiz_T287: ["wiz_T314", "wiz_T315"], wiz_T288: ["wiz_T316", "wiz_T317"] };
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
  // G2-1: without a measured travel the profile's values are not scaled, and the line says so.
  if (cover.profile?.name) return fill(t(cover.travel_cm > 0 ? "wiz_T395" : "wiz_T455"), { profile: cover.profile.name });
  if (cover.status && cover.status !== "to_measure") {
    // Inside «already calibrated (…)» the detail of the status, not the badge (G3-7: the badge says «Calibrated (…)»).
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
 * A-02's advice. Already calibrated: Ⓒ. Never measured: Ⓑ when a profile's reference travel is within ±5 % of the
 * cover's saved travel (the closest first), otherwise Ⓐ, with a badge only when no profile exists (with profiles the
 * user judges the similarity from the list under Ⓑ). The "if unsure" line always names the advised path.
 */
export function pathAdvice(cover, profiles = [], previews = true) {
  const measured = !!cover?.status && cover.status !== "to_measure";
  if (measured) return { badge: "C", hint: "C", similar: [] };
  const travel = cover?.travel_cm;
  const similar = travel > 0 ? profiles.filter((profile) => profile.referenceTravel > 0 && Math.abs(profile.referenceTravel - travel) <= travel * SIMILAR_TRAVEL)
    .sort((a, b) => Math.abs(a.referenceTravel - travel) - Math.abs(b.referenceTravel - travel)) : [];
  // Review P1: without the previews Ⓑ is never advised; the similar profiles stay as information under it.
  if (similar.length) return previews ? { badge: "B", hint: "B", similar } : { badge: "A", hint: "A", similar };
  // G4-2 (replaces R4-9b): there is always exactly one advised option, badge and preselection included.
  return { badge: "A", hint: "A", similar: [] };
}

/** The wizard's whole state as one screen. */
export function buildScreen(ctx) {
  // `previews` (review P1): the wizard passes `previewPathsEnabled()`; a screen built alone shows them.
  const { t, language = "en", path = "A", scope = null, stage, local = {}, cover, view, status = {}, preview = false, previews = true } = ctx;
  const n = (value, digits = 1) => number(value, language, digits);
  n.fixed = (value, digits = 1) => fixed(value, language, digits);
  const tx = (key, values) => fill(t(key), values);
  const name = cover?.name || "";
  const values = { cover: name, profile: cover?.profile?.name ?? "" };
  // D-21: before the case is confirmed (C-01, C-02) the stepper and its caption follow the case chosen so far, the
  // preselected «tape readings only» at first.
  // D-21 (Y): on C-01 the case is not chosen yet: the stepper shows only the introduction under «Perfezionamento».
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
    // The covers as a list with one radio each (the sequence view's rows, the list's filters and order); Continue
    // waits for a choice. The chosen row opens and says what choosing it means.
    const picked = local.coverPicked ? (ctx.covers || []).find((item) => item.key === local.coverKey) : null;
    const notes = [];
    // A calibrated cover: one warning that a new measurement replaces its values (and the profile it follows);
    // fine-tuning is the way to adjust them. A cover that only follows a profile says which.
    if (picked?.status && picked.status !== "to_measure") {
      notes.push({ tone: "warning", text: fill(t(cover?.profile?.name ? "wiz_T413" : "wiz_T414"), { profile: cover?.profile?.name ?? "",
        status: t(`calTabStatusDetail_${picked.detail}`), date: dateText(picked.last, language) || "—" }) });
    } else if (picked && cover?.profile?.name) notes.push({ tone: "info", text: fill(t("wiz_T395"), { profile: cover.profile.name }) });
    Object.assign(screen, { code: "A-01", phase: "intro", title: t("wiz_T003"), body: t("wiz_T004"),
      picker: { selected: picked ? local.coverKey : "", filter: local.pickFilter || "all", notes },
      actions: [{ id: "choose", label: t("wiz_T006"), kind: "primary", disabled: !ctx.covers?.length || !picked }] });
    // Until a cover is picked the subtitle says so; then its room and address (its name is in the chosen row, not
    // repeated here: responsive map §3.3).
    screen.header = picked ? { title: t("wiz_T001"), subtitle: cover?.address || "", units: cover?.units || null }
      : { title: t("wiz_T001"), subtitle: t("wiz_T392"), placeholder: true };
    return finish();
  }
  if (stage === "path") {
    // A radio list (A-02 Section A) with Continue in the actions; the advised path carries a badge.
    const list = ctx.profiles || [], profiles = list.length, measured = !!cover?.status && cover.status !== "to_measure";
    const advice = pathAdvice(cover, list, previews);
    // G2-4: a path is named by its option's own label, never by a letter.
    const label = { A: t("wiz_T017"), B: t("wiz_T018"), C: t("wiz_T019") };
    // Under Ⓑ: how many profiles exist, then why it is advised (similar travel) or, on a never measured cover, each
    // profile with its slats and reference travel so the user can judge the similarity.
    const describe = (profile) => [profile.name, t(profile.slat == null ? "wiz_T370" : profile.slat > 0 ? "wiz_T368" : "wiz_T369"),
      ...(profile.referenceTravel > 0 ? [fill(t("wiz_T372"), { cm: n(profile.referenceTravel, 0) })] : [])].join(" · ");
    // G2-2: each option carries its explanation as the first line of its note, then what applies to this cover.
    const bNote = !profiles ? [t("wiz_T453"), t("wiz_T401")] : [t("wiz_T453"), profiles === 1 ? t("wiz_T419") : fill(t("wiz_T420"), { count: profiles }),
      ...(advice.similar.length ? advice.similar.map((profile) => fill(t("wiz_T422"), { profile: profile.name, travel: n(cover.travel_cm, 0), reference: n(profile.referenceTravel, 0) }))
        : measured ? [] : list.map(describe))];
    const options = [
      { value: "A", label: label.A, note: [t("wiz_T452")] },
      // Review P1: a preview stays visible, disabled, saying when it comes. L-124: a feature not shipped yet is a blue
      // information note under its title (yellow is for the measurements' warnings only).
      { value: "B", label: label.B, disabled: !profiles || !previews, note: bNote, ...(previews ? {} : { notice: t("wiz_T584"), noticeTone: "info" }) },
      // Fine-tuning corrects values a cover already has: never on a cover never measured.
      // L-124: a condition the user can meet is the option's plain grey caption, under its title.
      { value: "C", label: label.C, note: [t("wiz_T454")], ...(measured ? {} : { disabled: true, caption: t("wiz_T423") }) },
    ].map((option) => ({ ...option, badge: option.value === advice.badge && !option.disabled ? t("wiz_T400") : "" }));
    // Preselected on the badge, Continue is ready; without a badge nothing is chosen and Continue waits.
    const enabled = (value) => options.some((option) => option.value === value && !option.disabled);
    const chosen = enabled(local.pathChoice) ? local.pathChoice : advice.badge && enabled(advice.badge) ? advice.badge : "";
    // On a calibrated cover another path than the advised one says what it replaces, under the chosen option.
    if (measured && chosen && chosen !== advice.badge) {
      const warning = chosen === "A" ? fill(t("wiz_T415"), { option: label.C }) : chosen === "B" ? t(cover?.profile ? "wiz_T416" : "wiz_T417") : "";
      const option = options.find((item) => item.value === chosen);
      if (warning) option.warning = warning;
    }
    // "There are three paths", then always "If unsure, choose «…»" with the advised option's label; the options follow.
    // G3-1: why the advised option is advised, in one impersonal sentence (none when nothing is advised).
    const closest = advice.similar[0];
    const reason = measured ? fill(t(cover?.profile?.name ? "wiz_T464" : "wiz_T463"), { option: label.C, profile: cover?.profile?.name ?? "" })
      : advice.badge === "B" && closest ? fill(t("wiz_T467"), { option: label.B, travel: n(cover.travel_cm, 0), reference: n(closest.referenceTravel, 0), profile: closest.name })
        // Review P1: Ⓑ off, a close profile still said (under Ⓑ, as information); the advice is Ⓐ.
        : closest ? fill(t("wiz_T586"), { option: label.A })
        : !profiles ? fill(t("wiz_T466"), { option: label.A })
          : !(cover?.travel_cm > 0) ? fill(t("wiz_T465"), { option: label.A }) : fill(t("wiz_T476"), { option: label.A, travel: n(cover.travel_cm, 0) });
    // G4-2: the reason ends with «If in doubt, choose this option.»; no separate «If unsure» line.
    // L-122: without the previews (PREVIEW_PATHS) the first sentence says how many of the three can be used for now
    // (Ⓑ is a preview; Perfezionamento only on a cover already measured).
    const usable = options.filter((option) => !option.disabled).length;
    const intro = previews ? t("wiz_T016") : t(usable === 1 ? "wiz_T592" : usable === 2 ? "wiz_T593" : "wiz_T016");
    const body = [intro, `${reason} ${t("wiz_T475")}`].join("\n\n");
    Object.assign(screen, { code: "A-02", phase: "intro", title: tx("wiz_T015", { name }), subline: pathContext(t, cover, language), body,
      choice: { name: "path", kind: "radio", label: tx("wiz_T015", { name }), legendHidden: true, value: chosen, options },
      actions: [{ id: "path", label: t("wiz_T006"), kind: "primary", disabled: !chosen }],
      // D-21: the stepper shows the steps of the path chosen so far (the advised one at first).
      stepPath: chosen || path });
    return finish();
  }
  if (stage === "profile") {
    // Review P3: the profile chosen (preselected: the closest by travel, A-02's); without one, nothing until a choice.
    const chosen = ctx.profiles?.find((item) => item.id === local.profileId) || null;
    // One radio row per profile: "tall_two_leaf · measured on Living room shutter 1", then who follows it by name.
    const about = (item) => fill(t("wiz_T165").split(" · ")[0], { cover: item.measuredOn || "—" });
    // Y-c: path B runs only as a preview: B-01 says so under its button, as B-02 does.
    Object.assign(screen, { code: "B-01", phase: "intro", title: t("wiz_T161"), body: t("wiz_T162"),
      choice: { name: "profile", kind: "radio", label: t("wiz_T163"), value: chosen?.id ?? "",
        // L-19: the profile the cover follows now is marked «attuale» and stays selectable (to verify it again).
        options: (ctx.profiles || []).map((item) => ({ value: item.id, label: `${item.name} · ${lowerFirst(about(item), language)}`, plain: true,
          badge: cover?.profile?.id && item.id === cover.profile.id ? t("wiz_T555") : "",
          note: [followedBy(t, item.followerNames || [], language)] })) },
      actions: [{ id: "profile", label: t("wiz_T006"), kind: "primary", disabled: !chosen, note: t("wiz_T318") }] });
    return finish();
  }
  if (stage === "current") {
    const effective = cover?.effective || {};
    Object.assign(screen, { code: "C-01", phase: "intro", title: t("wiz_T184"),
      // G2-1: with no measured travel the first paragraph says the values are the profile's, not scaled.
      body: !cover?.profile ? tx("wizNoProfileBody", values) : cover.travel_cm > 0 ? tx("wiz_T185", { ...values, travel: n(cover.travel_cm) })
        : [tx("wiz_T455", values), ...tx("wiz_T185", { ...values, travel: "" }).split(/\n\s*\n/).slice(1)].join("\n\n"),
      // A bold title and one line per value: the sheet's single line (T188) split at its separators.
      box: { label: t("wiz_T186"), lines: tx("wiz_T188", { profile: cover?.profile?.name ?? "—", travel: n(cover?.travel_cm),
        opening: n.fixed(effective.opening?.value), closing: n.fixed(effective.closing?.value), slat: n.fixed(effective.slat_time_s?.value) }).split(" · ")
        // D-20: a value not known is «—», without the unit.
        .map((line) => line.replace(/—\s*(s|cm)$/, "—")) },
      actions: [{ id: "current", label: t("wiz_T006"), kind: "primary" }] });
    return finish();
  }
  if (stage === "case") {
    // L-34: the tape readings only (the fine-tuning offered at the end of a base calibration) are recommended and
    // preselected, with a line saying why. No cover is fine-tuned yet (fine-tuning is a preview until its backend lot),
    // so the line always applies.
    // Review P1: without the previews the tape readings only stay visible, disabled, and nothing is advised or chosen.
    const value = previews ? local.scope || "points_only" : local.scope && local.scope !== "points_only" ? local.scope : "";
    Object.assign(screen, { code: "C-02", phase: "intro", title: t("wiz_T189"), body: previews ? `${t("wiz_T190")}\n\n${t("wiz_T548")}` : t("wiz_T190"),
      choice: { name: "case", kind: "radio", label: t("wiz_T191"), value,
        options: [previews ? { value: "points_only", label: t("wiz_T193"), badge: t("wiz_T400") } : { value: "points_only", label: t("wiz_T193"), disabled: true, notice: t("wiz_T584"), noticeTone: "info" },
          { value: "times_only", label: t("wiz_T194") }, { value: "times_and_rolls", label: t("wiz_T196") }] },
      actions: [{ id: "case", label: t("wiz_T006"), kind: "primary", disabled: !value }] });
    return finish();
  }
  if (stage === "before") {
    const pointsOnly = path === "C" && scope === "points_only", timesOnly = path === "C" && scope === "times_only";
    const bModel = path === "B";
    if (bModel) {
      Object.assign(screen, { code: "B-02", phase: "readings", title: t("wiz_T167"), body: t("wiz_T168"),
        actions: [{ id: "begin", label: t("wiz_T076"), kind: "primary", note: t("wiz_T318") }] });
      return finish();
    }
    Object.assign(screen, {
      code: pointsOnly ? "C-37" : timesOnly ? "C-35" : path === "C" ? "C-03" : "A-03", phase: "prep", title: t("wiz_T021"),
      // M5-6: never «— cm» inside a sentence: without a known travel the variant that does not name it.
      body: pointsOnly ? tx(cover?.travel_cm > 0 ? "wiz_T235" : "wiz_T490", { ...values, travel: n(cover?.travel_cm) })
        : timesOnly ? tx(cover?.travel_cm > 0 ? "wiz_T230" : "wiz_T489", { ...values, travel: n(cover?.travel_cm) })
        : path === "C" ? tx("wiz_T197", values) : t("wiz_T022"),
      // Guided times only has no lift-off: the backend does not use the slat choice there.
      // G4-5: the answer known from the last calibration is preselected (0 s of slats: without), «with slats» otherwise.
      choice: pointsOnly || timesOnly ? null : { name: "slats", kind: "radio", label: t("wiz_T023"),
        value: (local.slats ?? cover?.effective?.slat_time_s?.value !== 0) === false ? "no" : "yes",
        // G2-3: how to tell, closed by default, right after the question.
        help: { summary: t("wiz_T456"), text: t("wiz_T457") },
        options: [{ value: "yes", label: t("wiz_T198") }, { value: "no", label: t("wiz_T199") }] },
      // M5-5: times only has no slats question and says nothing about slats.
      help: null,
      // Readings only run as a preview: said before it starts.
      // No "Cancel" (T025) here or on A-16: it did what the header's "Abandon the calibration" does.
      actions: [{ id: "begin", label: t("wiz_T024"), kind: "primary", ...(pointsOnly ? { note: t("wiz_T318") } : {}) }] });
    return finish();
  }
  if (stage === "saved") return finish(savedScreen(screen, ctx, tx, values));
  // L-40: a reader whose session was cancelled where it was guided: its own end, with «Chiudi» only.
  if (stage === "cancelled") return finish(Object.assign(screen, { code: "S-01", phase: screen.phase, title: t("wiz_T542"), body: t("wiz_T543"),
    discard: false, actions: [{ id: "close", label: t("wiz_T390"), kind: "primary" }] }));
  // Review N1: the restarted session was gone by the time it was followed (its lease ran out): said as such.
  if (stage === "restart-expired") return finish(Object.assign(screen, { code: "S-06", phase: screen.phase, title: t("wiz_T259"), body: t("wiz_T260"),
    discard: false, actions: [{ id: "close", label: t("wiz_T390"), kind: "primary" }] }));
  // L-113: the device that guided it started again on this cover: no dead end, the new session can be followed.
  if (stage === "restarted") return finish(Object.assign(screen, { code: "S-01r", phase: screen.phase, title: t("wiz_T582"), body: t("wiz_T583"),
    discard: false, actions: [{ id: "follow-new", label: t("wiz_T579"), kind: "primary" }, { id: "close", label: t("wiz_T390"), kind: "secondary" }] }));

  // L-38: before a times-only measurement the cover is closed by Home Assistant, as path A's first positioning (C-04).
  if (local.homing && !view) {
    // L-120b: the bar runs to the instant the panel writes the Stop (the estimate and its margin).
    const seconds = homeStopAt(local.homing.seconds), done = Math.min(seconds ?? 0, Math.max(0, ((ctx.now ?? Date.now()) - local.homing.started) / 1000));
    Object.assign(screen, { code: "C-04", phase: "prep", title: t("wiz_T027"),
      // L-120: the bar ends when the close counts as done (L-120b: at the Stop).
      wait: { text: tx("wiz_T029", values), stages: [], progress: seconds > 0 ? { value: done / seconds, remaining: done >= seconds ? null : Math.max(1, Math.ceil(seconds - done - 0.001)) } : { value: null, remaining: null } },
      actions: [{ id: "stop", label: t("wiz_T028"), kind: "stop" }] });
    return finish();
  }
  // ---- A session (live or preview). ----
  if (!view) {
    Object.assign(screen, { code: "loading", title: t("loading"), actions: [] });
    return finish();
  }
  // M3-3: an ended session keeps the step it reached in the stepper, marked as interrupted.
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

function caption(t, path, scope, cover) {
  if (path === "B") return fill(t("wiz_T169"), { profile: cover?.followProfile ?? cover?.profile?.name ?? "" });
  if (path === "C") return t(scope === "times_only" ? "wiz_T231" : scope === "points_only" ? "wiz_T236" : scope === "times_and_rolls" ? "wiz_T200" : "wiz_T114");
  return t("wiz_T026");
}

/** Live `geometry` session: path A and path C (times and rolls). */
function geometryScreen(screen, ctx, tx, n, values, stop) {
  const { t, view, local = {}, path } = ctx;
  const p = (number) => `${path === "C" ? "C" : "A"}-${number}`;
  const slats = view.slats !== false;
  const elapsed = ctx.elapsed;
  const step = view.step, phase = view.phase;
  // A positioning run lasts about the full time of its direction (the time in use, the integration's default too).
  const positioning = (number, text, seconds = null) => Object.assign(screen, { code: p(number), title: t("wiz_T027"),
    wait: { text, stages: [], progress: waitBar(view, elapsed, seconds) }, actions: [stop] });
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
  // What was just measured, as tiles above the text: a small label, a big value, its unit smaller.
  // D-33: a time always carries one decimal, in the tiles too («23,0 s», «1,0 s»).
  const liftTile = () => view.samples?.lift == null ? null : { label: t("wiz_T039"), value: n.fixed(view.samples.lift), unit: "s",
    extra: view.readings?.gap == null ? "" : `${n(view.readings.gap)} cm` };
  const timeTile = (key, seconds) => seconds == null ? null : { label: t(key), value: n.fixed(seconds), unit: "s" };
  // L-120: the return to the top after the half-ascent reading starts where that run stopped, ((lift) + ascent) / 2 s up
  // from the bottom (A-17): what is left is the rest of the measured ascent, known to the session. Home Assistant's
  // position is not needed there (it is estimated with the times in use, and a session never confirms it before a
  // reading); elsewhere it gives the share of the travel still ahead.
  const fromHalf = step === "top" && !view.check && view.readings?.half_open != null && view.values?.opening_time > 0;
  const topTime = fromHalf ? view.values.opening_time - ((slats ? view.samples?.lift || 0 : 0) + view.values.opening_time) / 2
    : toRun(view.values?.opening_time, false);
  // A run to an end stop stays a positioning until the motor stops by itself or its full time has passed; only then
  // does the screen ask to confirm the end stop.
  const positioningRun = (seconds) => !MOTOR_STOPPED(view) && seconds > 0 && (elapsed ?? 0) < seconds;
  // L-120: every «arrived» confirm (A-05, A-10, the return to the top, A-19 and A-30's first leg) is on at the earliest
  // of the motor's stop status and the estimated run, with no margin; the wait's bar ends at that same instant. Before
  // it the user cannot claim an end stop the cover could not have reached yet.
  // The motor stopped by itself well before the estimated run (under 60 %): a wall switch or an obstacle gives the same
  // frame as the end stop, so the user is asked to look before confirming. The confirmation stays active.
  const stoppedEarly = (estimate, key) => {
    if (!MOTOR_STOPPED(view) || view.stopped_elapsed == null || !(estimate > 0) || view.stopped_elapsed >= estimate * EARLY_STOP_SHARE) return;
    screen.notices.push({ tone: "warning", result: true, text: fill(t(key), { seconds: n.fixed(view.stopped_elapsed), estimate: n(estimate, 0) }) });
  };
  // G2-9: a lift-off reading under 1 cm repeats the run. The backend clears `still_resting` as soon as the return to
  // the bottom starts, so the panel keeps the value it sent: the return (A-09) and the new lift-off briefing (A-07)
  // name it and say why; after a reload, without the value, the backend's notice stands while it lasts.
  const resting = local.lastGap != null && local.lastGap < (view.touching_cm ?? 1) && (view.lift_attempts ?? 1) > 1;
  // G3-3: a warning, worded by where the repeat came from (the value typed, or the button).
  const liftRepeated = () => ({ tone: "warning", result: true, text: !resting ? fill(t("calLiftRepeated"), view)
    : local.liftOrigin === "button" ? t("wiz_T469") : fill(t("wiz_T458"), { n: n(local.lastGap, 2) }) });
  // G2-10: after ② (or at the end of a half run) the backend waits for the actuator's stop: the screen says so and the
  // stopwatch stays at the press. L-119: the bar stays as it is, its buttons off (Stop follows the motor); the stop in
  // progress is said above it.
  const stopping = (seconds) => {
    if (seconds !== undefined) {
      if (seconds != null) { const [label, value] = splitLabel(fill(t("wiz_T257"), { seconds: n.fixed(seconds) })); screen.stopwatch = { label, value }; }
      else screen.stopwatch = null;
    }
    screen.actions = screen.actions.map((action) => (action.id === "stop" || action.id === "stopped" ? action : { ...action, disabled: true }));
    screen.notices.push({ tone: "info", text: t("wiz_T459") });
  };
  // U-9: once the run to an end stop is over (the stop frame, or its full time and a margin gone by: a timed actuator
  // stops by itself without a frame) Stop gives way to the state, «La corsa è finita: la tapparella è ferma».
  const ranOut = (seconds) => MOTOR_STOPPED(view) || (seconds > 0 && (elapsed ?? 0) >= seconds + Math.max(2, seconds * 0.15));
  // L-116: in a bar with the ①/② pair Stop is always drawn, last, off while nothing moves (finalize sets it).
  // L-119: so in every bar of a step that moves the cover: its slot never empties, the state only takes its place.
  const pinnedStop = { ...stop, pinned: true };
  const stopOrState = (seconds) => ranOut(seconds) ? [{ id: "stopped", label: t("wiz_T498"), kind: "state" }] : [pinnedStop];
  // L-14: a return before a tape reading (the user is there to read the tape) never goes on by itself: its end stop is
  // confirmed by the user, enabled once the motor stopped by itself or its estimated time has run; the run to the next
  // reading then starts from that press.
  const arrivedBy = (seconds) => MOTOR_STOPPED(view) || !(seconds > 0) || (elapsed ?? 0) >= seconds;
  // L-119 «same step, same bar»: a run Home Assistant starts by itself after a press (a return to an end stop, the
  // runs of the tape readings, the check's legs) draws its whole bar, off, from the briefing that precedes it. When
  // nothing starts it (a session reopened or claimed there) its own start goes first and stays, off, once pressed.
  const withStart = (items) => (phase === "briefing" && local.autoStart !== step) || local.manualStart === step
    ? [{ id: "next", label: t("wiz_T060"), kind: "primary", disabled: phase !== "briefing" }, ...items] : items;
  // L-37: the base calibration ends with an optional check of the model just measured (the backend's check: the end
  // stop, a timed run to half the travel descending from the top, a tape reading); its gap is the precision shown in
  // the summary, and the reading anchors Home Assistant's estimate.
  // L-43: it is part of the path (not skippable): its own step «Verifica», in a live view as in a reopened or read one.
  // L-44b: a check stopped (Stop, or a movement it did not expect) is done again: it is part of the path.
  if (phase === "review" && !view.check && !local.refining && !local.checkSkipped && view.travel_cm > 0) {
    screen.phase = "check";
    if (view.check_interrupted) screen.notices.push({ tone: "warning", result: true, text: t("wiz_T568") });
    // L-78: never a dead end: a model Home Assistant can plan no check for may be saved without one (no «Verificata»).
    const impossible = ctx.status?.error?.code === "invalid_check";
    // L-97: the drawing of the reading with the tape at part height (what the check asks for), between title and text.
    return Object.assign(screen, { code: p("21v"), title: t("wiz_T544"), body: `${t("wiz_T545")}\n\n${tx("wiz_T564", { threshold: n(view.check_threshold_cm ?? 4, 0) })}`,
      illustration: { id: "curtain-partial-tape", position: BEFORE },
      actions: [{ id: "check-start", label: t("wiz_T546"), kind: "primary" },
        ...(impossible ? [{ id: "skip-check", label: t("wiz_T576"), kind: "secondary" }] : [])] });
  }
  if (phase === "review" || phase === "saving") return summaryScreen(screen, ctx, tx, n, values);
  if (step === "home") {
    screen.phase = "prep";
    // A session's first closing starts by itself (L-119: its briefing already draws the positioning and its bar); a
    // session reopened before it started keeps A-03 and its button.
    if (phase === "briefing" && local.autoStart !== "home") return Object.assign(screen, { code: p("03"), title: t("wiz_T021"), body: path === "C" ? tx("wiz_T197", values) : t("wiz_T022"),
      actions: [{ id: "next", label: t("wiz_T024"), kind: "primary" }] });
    // L-119: one bar from the start of the closing to its end stop: the end stop, off until the cover has arrived (the
    // motor stopped by itself, or its estimated time is over); «Non ha fatto quello che doveva» from the start (it
    // stops the cover and opens S-01, nothing measured yet is lost); Stop last, replaced in place by the finished line
    // once the run is over. Not on A-10: there a stop discards the lift-off measurement (§8.9).
    const arrived = phase === "closing" && !positioningRun(closingTime);
    const running = ["starting_close", "closing"].includes(phase);
    const bar = [{ id: "endpoint", label: t("wiz_T032"), kind: "primary", disabled: !arrived, settling: !arrived && running },
      { id: "misbehaved", label: t("wiz_T033"), kind: "secondary", disabled: !running }, ...stopOrState(closingTime)];
    // A-05 once the positioning is over; while the motor still runs its text says the cover is closing, not closed.
    if (arrived) {
      Object.assign(screen, { code: p("05"), title: t("wiz_T030"), body: t(ranOut(closingTime) ? (slats ? "wiz_T031" : "wiz_T501") : (slats ? "wiz_T590" : "wiz_T591")), actions: bar });
      stoppedEarly(closingTime, "wiz_T440");
      return screen;
    }
    positioning("04", tx("wiz_T029", values), closingTime);
    screen.actions = bar;
    return screen;
  }
  if (["lift", "reset", "opening"].includes(step)) screen.phase = "ascent";
  if (step === "closing") screen.phase = "descent";
  if (["half_open", "half_close", "top"].includes(step)) screen.phase = "readings";
  if (step === "lift") {
    if (phase === "briefing" && !local.ackLift && slats) return Object.assign(screen, { code: p("06"), title: t("wiz_T035"), body: t("wiz_T036"),
      actions: [{ id: "ack:lift", label: t("wiz_T037"), kind: "primary" }] });
    if (phase === "reading") {
      Object.assign(screen, { code: p("08"), title: t("wiz_T047"), body: t("wiz_T048"),
        field: { label: t("wiz_T049"), hint: t("wiz_T050"), min: 0, max: view.max_gap_cm ?? 20, kind: "gap" },
        actions: [{ id: "reading", label: t("wiz_T051"), kind: "primary" }, { id: "resting", label: t("wiz_T468"), kind: "secondary" }] });
      return screen;
    }
    const running = ["starting_open", "opening"].includes(phase);
    // N-12: while the motor runs the body says what to watch for, no longer how to start it.
    const moving = /^(starting_)?(open|clos)ing$/.test(phase);
    Object.assign(screen, { code: p("07"), title: t("wiz_T042"), body: moving ? t("wiz_T520") : path === "C" ? t("wiz_T202") : t("wiz_T043"),
      stopwatch: runWatch(t, n, view, elapsed),
      // L-116: ① · ② · Stop, always all three: ① off once it started the run, ② and Stop on while the cover moves.
      actions: [{ id: "next", label: t("wiz_T045"), kind: "primary", disabled: phase !== "briefing" },
        { id: "lift", label: t("wiz_T046"), kind: "primary", disabled: phase !== "opening" }, pinnedStop] });
    if ((resting || view.still_resting) && phase === "briefing") screen.notices.push(liftRepeated());
    if (phase === "geometry_wait_stop") stopping(view.samples?.lift);
    return screen;
  }
  if (step === "reset") {
    // Z-4: the reading is already in: the screen is about the choice (repeat the lift-off run or go on), not the reading.
    if (phase === "briefing" && view.gap_warning) return Object.assign(screen, { code: p("08"), title: t("wiz_T525"), body: t("wiz_T526"), illustration: false,
      results: [liftTile()].filter(Boolean),
      notices: [{ tone: "warning", result: true, text: fill(t("calGapWarning"), view) }],
      // G3-5: repeating is the advised way; going on with a wide gap is the outlined secondary.
      actions: [{ id: "repeat", label: t("wiz_T061"), kind: "primary" }, { id: "next", label: t("wiz_T473"), kind: "secondary" }] });
    // The same Stop rule as A-05; no T033 here, a stop would discard the lift-off.
    // L-119: one bar from the briefing to the end stop: the end stop (L-120: on once the cover has arrived), Stop last
    // or the finished line.
    const ready = phase === "closing" && arrivedBy(closingTime);
    const bar = withStart([{ id: "endpoint", label: t("wiz_T032"), kind: "primary", disabled: !ready,
      settling: ["starting_close", "closing"].includes(phase) && !ready }, ...stopOrState(closingTime)]);
    if (phase === "closing" && !positioningRun(closingTime)) {
      Object.assign(screen, { code: p("10"), title: t("wiz_T053"), body: t("wiz_T054"), actions: bar });
      // Not on a return before a repeated reading: that one confirms itself on the stop frame (§9.53).
      if (!local.ackReadings) stoppedEarly(closingTime, "wiz_T440");
      return screen;
    }
    positioning("09", tx(path === "C" ? "wiz_T203" : "wiz_T052", values), closingTime);
    screen.actions = bar;
    if (resting || view.still_resting) screen.notices.push(liftRepeated());
    return screen;
  }
  // A full run that stopped before ② (L-3): its result is shown but not valid; only a repeat goes on.
  // D-11: not valid is drawn as such: its own title, the tiles struck through, the notice as an error, and Continue
  // shown but disabled beside Repeat.
  const invalid = (key) => local.invalidRun?.step === key ? invalidNotice(t, local.invalidRun.reason) : null;
  // L-3: still in the run, stopped before ②: the run's result is shown not valid at once, ② is gone; so it stays
  // while its end is being confirmed.
  const stoppedRun = (key) => phase === key && STOPPED_BEFORE_PRESS(view, local)
    ? invalidNotice(t, local.invalidRun?.step === key ? local.invalidRun.reason : stopVerdict(view.stopped_elapsed))
    : phase === "geometry_wait_stop" && local.invalidRun?.step === key ? invalidNotice(t, local.invalidRun.reason) : null;
  const stoppedTime = (key) => view.stopped_elapsed ?? view.values?.[`${key}_time`];
  const repeatOnly = (code, title, results, note) => Object.assign(screen, { code, title, results: results.filter(Boolean),
    notices: [note], actions: [{ id: "repeat", label: t("wiz_T061"), kind: "primary" }, { id: "invalid-next", label: t("wiz_T060"), kind: "secondary", disabled: true }] });
  if (step === "opening") {
    const stopped = stoppedRun("opening");
    if (stopped) return repeatOnly(p("12"), t("wiz_T502"), [slats ? liftTile() : null, { ...timeTile("wiz_T040", stoppedTime("opening")), invalid: true }], stopped);
    if (phase === "reading") {
      const bad = invalid("opening");
      if (bad) return repeatOnly(p("12"), t("wiz_T502"), [slats ? liftTile() : null, { ...timeTile("wiz_T040", view.values?.opening_time), invalid: true }], bad);
      // L-13 (decided): the travel first, then the ascent's result with it («Corsa del telo»), confirmed or repeated.
      if (local.travelTyped != null) return Object.assign(screen, { code: p("12"), title: t("wiz_T058"), body: t("wiz_T059"),
        results: [slats ? liftTile() : null, timeTile("wiz_T040", view.values?.opening_time), { label: t("wiz_T041"), value: n(local.travelTyped), unit: "cm" }].filter(Boolean),
        actions: [{ id: "ack:ascent", label: t("wiz_T060"), kind: "primary" }, { id: "repeat", label: t("wiz_T061"), kind: "secondary" }] });
      return Object.assign(screen, { code: p("13"), title: t("wiz_T062"), body: t("wiz_T063"),
        // Z-11: one pattern for every reading's hint: the expected value when known, the field's limits otherwise.
        // L-31: a travel offered from before says where it comes from; otherwise only the accepted range.
        field: { label: t("wiz_T064"), hint: (local.keptTravel ?? view.saved_travel_cm) != null ? tx("wiz_T549", { cm: n(local.keptTravel ?? view.saved_travel_cm, 0) }) : t("wiz_T066"),
          min: 20, max: 500, kind: "travel", value: local.keptTravel ?? view.saved_travel_cm ?? null },
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
      repeatOnly(p("15"), t("wiz_T503"), [{ ...timeTile("wiz_T068", stoppedTime("closing")), invalid: true }], stopped);
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
      repeatOnly(p("15"), t("wiz_T503"), [{ ...timeTile("wiz_T068", view.values?.closing_time), invalid: true }], bad);
      screen.phase = "descent";
      return screen;
    }
    if (phase === "briefing" && !local.ackDescent) return Object.assign(screen, { code: p("15"), phase: "descent", title: t("wiz_T071"), body: t("wiz_T072"),
      // L-13: the descent's result is its duration only (the travel belongs to the ascent's).
      results: [timeTile("wiz_T068", view.values?.closing_time)].filter(Boolean),
      actions: [{ id: "ack:descent", label: t("wiz_T060"), kind: "primary" }, { id: "repeat", label: t("wiz_T061"), kind: "secondary" }] });
    if (phase === "briefing" && !local.ackReadings) return Object.assign(screen, { code: p("16"), title: t("wiz_T074"), body: t("wiz_T075"),
      actions: [{ id: "ack:readings", label: t("wiz_T076"), kind: "primary" }] });
    if (phase === "reading") return Object.assign(screen, { code: p("18"), title: t("wiz_T081"), body: t("wiz_T082"),
      field: halfField(ctx, tx, n), actions: [{ id: "reading", label: t("wiz_T051"), kind: "primary" }, { id: "repeat", label: t("wiz_T033"), kind: "secondary" }] });
    const seconds = ((slats ? view.samples?.lift || 0 : 0) + (view.values?.opening_time || 0)) / 2;
    return Object.assign(screen, { code: p("17"), title: t("wiz_T027"),
      wait: { text: tx(path === "C" ? "wiz_T205" : "wiz_T080", values), stages: [{ text: tx("wiz_T079", { seconds: n(seconds, 0) }), state: "current" }],
        progress: waitBar(view, elapsed, seconds) },
      // Normally started by the wizard itself; if that did not happen the run can be started here (L-119: its start
      // then stays, off). Stop keeps its slot to the end of the run.
      actions: withStart([pinnedStop]) });
    if (phase === "geometry_wait_stop") stopping();
    return screen;
  }
  // L-54: the return to the top before the half-descent reading is the readings' two-leg wait (A-19), known from the
  // session itself (the half-ascent reading is in): never A-09's one leg, whatever this tab's own acknowledgements.
  const readingsReturn = local.ackReadings || view.readings?.half_open != null || !!view.check;
  if (step === "top" && !readingsReturn) {
    // D-12: once the motor has stopped the run is no longer «positioning»: the stop is said in the title, with its
    // warning when it came early, and the end stop to confirm (the state instead of Stop, U-9).
    // Z-5: as A-05, «Non ha fatto quello che doveva» beside the end stop. L-119: from the start of the run, with the end
    // stop (L-120: on once the cover has arrived) and Stop last, replaced in place by the finished line.
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
    // L-29: the end stop it goes to, by direction («va», not «torna»: it did not come from there).
    positioning("09", t("wiz_T553"), topTime);
    screen.phase = "descent";
    screen.actions = bar;
    if (phase === "opening") stoppedEarly(topTime, "wiz_T441");
    return screen;
  }
  if (step === "top" || step === "half_close") {
    if (phase === "reading") return Object.assign(screen, { code: p("20"), title: t("wiz_T087"), body: t("wiz_T088"),
      field: halfField(ctx, tx, n), actions: [{ id: "reading", label: t("wiz_T051"), kind: "primary" }, { id: "repeat", label: t("wiz_T033"), kind: "secondary" }] });
    const down = Math.max(((view.values?.closing_time || 0) - (view.geometry?.slat_time_s || 0)) / 2, 0);
    const first = step === "top";
    Object.assign(screen, { code: p("19"), title: t("wiz_T027"),
      wait: { text: t("wiz_T553"), stages: [
        // V-1: each leg says the time its bar counts down from (the run to the top from where the cover is), whole
        // seconds rounded up as the time left is, so «circa 23 s» never shows «circa 24 s» left.
        { text: tx("wiz_T084", { seconds: n(Math.ceil((topTime ?? view.values?.opening_time ?? 0) - 0.001), 0) }), state: first ? "current" : "done" },
        { text: tx("wiz_T085", { seconds: n(Math.ceil(down - 0.001), 0) }), state: first ? "future" : "current" }],
      progress: waitBar(view, elapsed, first ? topTime : down) } });
    // The return to the top is a positioning run: Home Assistant needs its end stop confirmed (L-14: once it has
    // arrived). L-119: one bar for both legs: that end stop (off in the second leg, already confirmed), Stop last,
    // replaced in place by the finished line once the run to the top is over.
    const ready = arrivedBy(topTime);
    screen.actions = withStart([{ id: "endpoint", label: t("wiz_T432"), kind: "primary", disabled: !first || phase !== "opening" || !ready,
      settling: first && (phase === "starting_open" || (phase === "opening" && !ready)) }, ...(first ? stopOrState(topTime) : [pinnedStop])]);
    if (phase === "geometry_wait_stop") stopping();
    // L-37: the return to the top before the check run says so (not the half descent's stages).
    // L-44: the check's first leg, as the half readings': to the top (its bar), then the run to its target (second
    // stage), which starts on the confirmation of the top.
    if (first && view.check) {
      screen.code = p("30"); screen.phase = "check";
      screen.wait = { text: t("wiz_T553"), stages: checkStages(tx, n, view, topTime ?? view.values?.opening_time, true), progress: waitBar(view, elapsed, topTime) };
    }
    // Started by the wizard itself; if that did not happen (a reopened session, a claim) it starts here (withStart).
    return screen;
  }
  if (step === "check") {
    // The check of the measured model (L-37/L-43): its run is stopped by Home Assistant like the intermediate runs, so
    // no elapsed time; then the tape reading, from 0.1 cm to the measured travel.
    screen.phase = "check";
    // L-9: the final check says which reading was kept far from the model. L-44: its hint gives the height expected
    // (the check is the comparison with it), not a range.
    const expectedCheck = view.check?.expected_cm ?? view.check?.target_cm;
    // L-87: the check is about to answer: the far readings wait for its result, in the summary.
    if (phase === "reading") return Object.assign(screen, { code: p("31"), title: t("wiz_T138"), body: t("wiz_T139"), notices: [...screen.notices],
      field: { label: t("wiz_T064"), hint: expectedCheck != null ? tx("wiz_T558", { cm: n(expectedCheck, 0) }) : t("wiz_T491"), min: 0.1, max: view.travel_cm ?? 10000, kind: "height" },
      actions: [{ id: "reading", label: t("wiz_T051"), kind: "primary" }, { id: "repeat", label: t("wiz_T033"), kind: "secondary" }] });
    // L-44: the second leg, the timed run to the target: its own bar. L-119: the first leg's bar (A-30 is one step):
    // the end stop of the top, off (already confirmed), and Stop in its slot to the end of the run.
    const planned = view.check?.planned_seconds;
    Object.assign(screen, { code: p("30"), title: t("wiz_T027"),
      wait: { text: t(view.check?.direction === "opening" ? "wiz_T554" : "wiz_T553"), stages: checkStages(tx, n, view, view.values?.opening_time, false), progress: waitBar(view, elapsed, planned) },
      actions: withStart([{ id: "endpoint", label: t("wiz_T432"), kind: "primary", disabled: true }, pinnedStop]) });
    if (phase === "geometry_wait_stop") stopping();
    return screen;
  }
  if (phase === "geometry_wait_stop") return positioning("04", "");
  return Object.assign(screen, { code: "unknown", title: t(`calPhase_${phase}`), actions: [stop] });
}

/**
 * Live `guided` session for path C, times only (G4-6): the backend's two-run protocol on our own screens and texts,
 * adapted from A-11/A-12/A-14/A-15 (never the maintainer's phase texts). The backend goes from the measured ascent
 * straight to «ready for the descent» and from the measured descent straight to the review: the result screens are
 * the panel's own (acknowledged locally). A repeat starts the times again from a closed cover (the guided session has
 * no repeat verb). The validity rule of the full runs (R8-7) applies as on A-11/A-14.
 */
function guidedScreen(screen, ctx, tx, n, values, stop) {
  const { t, view, elapsed, local = {} } = ctx;
  const phase = view.phase;
  const tile = (key, seconds) => seconds == null ? null : { label: t(key), value: n.fixed(seconds), unit: "s" };
  const bad = (key) => local.invalidRun?.step === key ? invalidNotice(t, local.invalidRun.reason) : null;
  const result = (code, title, body, time, key, ack, stopped = null) => {
    const notice = stopped || bad(key);
    // D-11: as on A-12/A-15, a result that is not valid looks it.
    return Object.assign(screen, { code, title: notice ? t(key === "opening" ? "wiz_T502" : "wiz_T503") : title, body: notice ? "" : t(body),
      results: [time].filter(Boolean).map((tile) => notice ? { ...tile, invalid: true } : tile), notices: notice ? [notice] : [],
      actions: notice ? [{ id: "restart", label: t("wiz_T061"), kind: "primary" }, { id: "invalid-next", label: t("wiz_T060"), kind: "secondary", disabled: true }]
        : [{ id: ack, label: t("wiz_T060"), kind: "primary" }, { id: "restart", label: t("wiz_T061"), kind: "secondary" }] });
  };
  if ((phase === "review" || phase === "saving") && !local.ackDescent) {
    screen.phase = "descent";
    return result("C-15", t("wiz_T071"), "wiz_T480", tile("wiz_T068", view.values?.closing_time), "closing", "ack:descent");
  }
  if (phase === "review" || phase === "saving") return summaryScreen(screen, ctx, tx, n, values);
  // L-3: still in the run, stopped before ②: its result, not valid, at once; ② is gone.
  if (["opening", "closing"].includes(phase) && STOPPED_BEFORE_PRESS(view, local)) {
    const key = phase, notice = invalidNotice(t, local.invalidRun?.step === key ? local.invalidRun.reason : stopVerdict(view.stopped_elapsed));
    screen.phase = key === "opening" ? "ascent" : "descent";
    return key === "opening" ? result("C-12", t("wiz_T058"), "wiz_T478", tile("wiz_T040", view.stopped_elapsed), "opening", "ack:ascent", notice)
      : result("C-15", t("wiz_T071"), "wiz_T480", tile("wiz_T068", view.stopped_elapsed), "closing", "ack:descent", notice);
  }
  if (["confirm_closed", "starting_open", "opening"].includes(phase)) {
    return Object.assign(screen, { code: "C-11", phase: "ascent", title: t("wiz_T040"), body: t(phase === "confirm_closed" ? "wiz_T477" : "wiz_T521"),
      stopwatch: runWatch(t, n, view, elapsed),
      actions: [{ id: "run:open", label: t("wiz_T045"), kind: "primary", disabled: phase !== "confirm_closed" },
        { id: "endpoint", label: t("wiz_T057"), kind: "primary", disabled: phase !== "opening" || MOTOR_STOPPED(view) }, { ...stop, pinned: true }] });
  }
  if (phase === "confirm_open" && !local.ackAscent) {
    screen.phase = "ascent";
    return result("C-12", t("wiz_T058"), "wiz_T478", tile("wiz_T040", view.values?.opening_time), "opening", "ack:ascent");
  }
  if (["confirm_open", "starting_close", "closing"].includes(phase)) {
    return Object.assign(screen, { code: "C-14", phase: "descent", title: t("wiz_T068"), body: t(phase === "confirm_open" ? "wiz_T479" : "wiz_T522"),
      stopwatch: runWatch(t, n, view, elapsed),
      actions: [{ id: "run:close", label: t("wiz_T045"), kind: "primary", disabled: phase !== "confirm_open" },
        { id: "endpoint", label: t("wiz_T070"), kind: "primary", disabled: phase !== "closing" || MOTOR_STOPPED(view) }, { ...stop, pinned: true }] });
  }
  return Object.assign(screen, { code: "unknown", title: t("wiz_T027"), actions: [stop] });
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
  // D-46: a preview's box says nothing moves (path B, fine-tuning, tape readings only), so its positionings are not
  // drawn as such: what would happen live, the stages it would run, no countdown, and the state in place of Stop.
  // Y-b: the preview box already says nothing moves: no state line, but «Continua» to the reading at once.
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
      field: { label: t("wiz_T064"), hint: t("wiz_T066"), min: 20, max: 500, kind: "travel" },
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
      body: tx(passed ? "wiz_T175" : "wiz_T176", { ...values, profile: ctx.cover?.followProfile ?? values.profile, gap: check.gap_cm,
        expected: n(check.predicted_cm, 0), measured: n(check.measured_cm, 0) }),
      actions: passed ? [{ id: "accept", label: t("wiz_T060"), kind: "primary" }, { id: "repeat_check", label: t("wiz_T143"), kind: "secondary" }]
        // L-21: no fine-tuning from here: start this cover from scratch (path A, the travel typed kept), another profile,
        // or the check again.
        : [{ id: "restart-a", label: t("wiz_T551"), kind: "primary" }, { id: "other-profile", label: t("wiz_T552"), kind: "secondary" },
          { id: "repeat_check", label: t("wiz_T143"), kind: "secondary" }] });
    const verdict = { gap: check.gap_cm, seconds: n(check.check_seconds) };
    return Object.assign(screen, { code: p(passed ? "32" : "32b"), title: t(passed ? "wiz_T141" : "wiz_T144"),
      body: tx(passed ? "wiz_T142" : "wiz_T145", verdict),
      actions: passed ? [{ id: "accept", label: t("wiz_T060"), kind: "primary" }, { id: "repeat_check", label: t("wiz_T143"), kind: "secondary" }]
        : [{ id: "repeat_check", label: t("wiz_T143"), kind: "primary" }, { id: "redo_readings", label: t("wiz_T146"), kind: "secondary" },
          // L-47: not red (it loses nothing); text links exist only inside «Altre opzioni».
          { id: "continue_anyway", label: t("wiz_T147"), kind: "secondary" }] });
  }
  return Object.assign(screen, { code: "unknown", title: t("loading"), actions: [stop] });
}

/** Before/after of every summary: A-21, A-33, B-08, C-21, C-33, C-36, C-38. */
function summaryScreen(screen, ctx, tx, n, values) {
  const { t, view, path, scope, cover, local = {} } = ctx;
  const effective = cover?.effective || {};
  const refine = view.preview && view.path === "refine", points = view.preview && view.path === "rolls", profileB = view.preview && view.path === "profile";
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
  // D-32: a slat time of 0 is a cover without slats: «No», as in the list's details, not «0 s».
  // Z-1: times always with one decimal, as the stepper and the tiles.
  const format = (key, value, unit, digits) => value == null ? "—" : key === "slat" && value === 0 ? t("calTabSlatsNo")
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
  // D-35: a gap under 1 cm is said so, not «about 0 cm».
  // L-45: a live check (the base calibration's) is a tile with the measured values, as the times are; the preview's
  // check of fine-tuning keeps its sentence. The live check names its gap `deviation_cm`, the preview `gap_cm`.
  const liveCheck = view.check && !view.preview ? checkResult(ctx, tx, n, view.check, view.check_threshold_cm) : null;
  const precision = view.check && !liveCheck ? tx(Number(checkGap(view.check)) < 1 ? "wiz_T513" : "wiz_T151", { gap: checkGap(view.check), seconds: n(view.check.check_seconds) }) : null;
  const saveInfo = (key) => ({ title: t("wiz_T280"), body: counted(t, key, cover?.followers?.length ?? 0, values) });
  const save = { id: "save", label: t("wiz_T102"), kind: "primary" };
  // Review P5: Save says what it saves, and follows the choices made under it (a new profile and its name, the
  // profile followed, or this cover only).
  const saveAs = (target, name) => ({ ...save, target, label: target === "profile_new" ? tx("wiz_T587", { name })
    : target === "profile_update" ? tx("wiz_T588", { name: cover?.profile?.name ?? "" }) : t("wiz_T589") });
  // L-46: a preview's summary (path B, tape readings only, fine-tuning) saves nothing in this version: no Save, no
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
    Object.assign(screen, { code: "C-36", title: t("wiz_T179"), body: follows ? tx("wiz_T233", values) : t("wiz_T092"),
      saveInfo: follows ? saveInfo("wiz_T287") : saveInfo("wizSaveCoverOnly"), actions: [saveAs("cover_only")] });
    return withSupport(screen, ctx, "cover_only");
  }
  if (points) {
    // C-38 shows the values in use in grey: only the rolls change, so (D-35) they are in the table, not only under
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
  // L-117b: one screen whatever the profile switch makes its code (A-21, A-21c, A-21d): toggling it keeps the place.
  Object.assign(screen, { family: `summary:${path}:${refine ? "refine" : "base"}`,
    code: c ? (refine ? "C-33" : "C-21") : refine ? (follows ? "A-33c" : "A-33") : follows ? (block.choice ? "A-21d" : "A-21c") : "A-21",
    title: t(refine ? "wiz_T149" : "wiz_T091"),
    body: c ? (follows ? tx(refine ? "wiz_T226" : "wiz_T210", values) : t(refine ? "wiz_T150" : "wiz_T092"))
      : t(refine ? (follows ? "wiz_T152" : "wiz_T150") : (follows ? "wiz_T107" : "wiz_T092")),
    precision, profileBlock: block,
    // L-46: the box says exactly what Save does with the choice made: a new profile the cover follows, the profile it
    // follows updated, or (once available) this cover only.
    saveInfo: target === "profile_new" ? { title: t("wiz_T280"), body: tx("wiz_T566", { ...values, name: block.name?.value ?? "" }) }
      : target === "profile_update" ? { title: t("wiz_T280"), body: tx("wiz_T567", { ...values, profile: cover?.profile?.name ?? "" }) }
      : saveInfo("wizSaveCoverOnly"),
    // Fine-tuning is offered once this is saved (A-34), not here. L-45: above the threshold the check can be repeated.
    actions: [saveAs(target, block.name?.value ?? ""), ...(liveCheck && !liveCheck.passed ? [{ id: "check-start", label: t("wiz_T143"), kind: "secondary" }] : [])] });
  withSupport(screen, ctx, target);
  // After the notices that matter more (D-16: what cannot be saved comes first).
  if (liveCheck) screen.results = [...(screen.results || []), liveCheck.tile];
  return screen;
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
  const on = local.saveProfile ?? (!follows && !pathC);
  // On the cover the followed profile was measured on, updating that profile is the default, but only once the session
  // can save into an existing profile (L5a; the same condition as the "not available yet" notice): the default never
  // lands on a choice that cannot be saved. Elsewhere, and until then, a new profile.
  const source = follows && !!cover.entity_id && cover.profile.provenance?.opening?.origin_entity_id === cover.entity_id;
  const canUpdate = ctx.supports ? ctx.supports("profile_update") : true;
  // L-118: a session that cannot save into the profile followed (a base calibration: Home Assistant offers only a new
  // profile, `save_modes: ["new"]`) shows «update» disabled, saying so, and never lands on it: Save stays usable.
  const choice = on && follows ? { name: "save_choice", value: canUpdate ? local.saveChoice || (source ? "update" : "new") : "new",
    // Listed as in A-21d: update first, then a new profile (still the default choice, §9.10).
    options: [{ value: "update", label: tx("wiz_T109", { profile: cover.profile.name }),
      description: counted(t, "wiz_T110", cover.followers?.length ?? 0, { names: ctx.followerNames || "—" }),
      ...(canUpdate ? {} : { disabled: true, warning: t("wiz_T584") }) }, { value: "new", label: t("wiz_T111") }] } : null;
  const creating = on && (!follows || choice.value === "new");
  const nameValue = local.profileName ?? slug(cover?.name || "");
  // Home Assistant accepts any name of 1–64 characters; two profiles with the same name would be told apart by nothing.
  const wanted = nameValue.trim().toLocaleLowerCase();
  const nameError = !wanted ? t("wiz_T435") : (ctx.profiles || []).some((profile) => profile.name?.trim().toLocaleLowerCase() === wanted) ? t("wiz_T434") : "";
  const block = { switchLabel: t(follows ? "wiz_T439" : "wiz_T096"), on, explanation: follows ? tx("wiz_T108", { profile: cover.profile.name }) : t("wiz_T097"), choice,
    name: creating ? { label: t("wiz_T098"), value: nameValue, hint: t("wiz_T433"), error: nameError,
      line: follows ? followLine(t, cover) : tx("wiz_T101", { name: nameValue }),
      valid: !nameError } : null,
    target: !on ? "cover_only" : creating ? "profile_new" : "profile_update" };
  return block;
}

function withSupport(screen, ctx, target) {
  const supported = ctx.supports ? ctx.supports(target) : true;
  if (!supported) {
    // D-16: said at the top of the screen (seen on a phone too), and no help explaining what Save would do.
    // N-11: the notice names the way of saving that is not available and what to do instead.
    const text = target === "cover_only" ? fill(ctx.t("wiz_T518"), { switch: ctx.t(ctx.cover?.profile ? "wiz_T439" : "wiz_T096") })
      : target === "profile_update" ? fill(ctx.t("wiz_T519"), { profile: ctx.cover?.profile?.name ?? "", choice: ctx.t("wiz_T111") }) : ctx.t("wizSaveUnavailable");
    screen.notices.push({ tone: "warning", result: true, text });
    screen.saveInfo = null;
    for (const action of screen.actions) if (action.id === "save") action.disabled = true;
  }
  if (screen.profileBlock?.name && !screen.profileBlock.name.valid) {
    for (const action of screen.actions) if (action.id === "save") action.disabled = true;
  }
  return screen;
}

/** Profile names: letters, digits and underscores, from the cover name. */
export function slug(text) {
  return text.normalize("NFD").replace(/[̀-ͯ]/g, "").toLocaleLowerCase().replace(/[^a-z0-9]+/g, "_").replace(/^_+|_+$/g, "").slice(0, 64) || "profile";
}

function savedScreen(screen, ctx, tx, values) {
  const { t, path, local = {} } = ctx;
  const saved = local.saved || {};
  // L-45: the check's tile and note, as in the summary.
  const n = (value, digits = 1) => number(value, ctx.language || "en", digits);
  const checked = saved.check ? checkResult(ctx, tx, n, saved.check, saved.threshold ?? 4) : null;
  if (checked) screen.results = [checked.tile];
  // "Close" keeps each mockup's own style: B-09 red filled, A-34 red text, C-34 red outlined.
  // L-47 (revised): the end of a completed flow is not «Chiudi»: «Torna all'elenco», the primary unless fine-tuning is
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
      // U-8: at the end of a preview nothing is lost: on B-09 and C-34 «Chiudi» is the primary, not red (A-34 keeps its
      // primary «another» and Close as red text).
      actions: path === "B" ? [close()]
        : [close(), { id: "another", label: t("wiz_T156"), kind: "secondary" }, { id: "open-cover", label: t("wiz_T157"), kind: "secondary" }] });
  }
  if (path === "B") return Object.assign(screen, { code: "B-09", title: t("wiz_T154"),
    body: tx("wiz_T182", { ...values, profile: ctx.cover?.followProfile ?? "", travel: number(saved.travel_cm, ctx.language, 0) }),
    actions: [close()] });
  const body = saved.target === "profile_new" ? tx("wiz_T155", { name: saved.name ?? "" })
    : path === "C" && ctx.cover?.profile ? tx("wiz_T228", values) : tx("wizSavedCover", values);
  // Review P2: A-34's primary is «Torna all'elenco»; fine-tuning (path C on the same cover) is a secondary offered only
  // while it can be run, and only then the body's second paragraph (T319) says the cover will move.
  // A profile was created or updated: it can be given to similar covers at once (the maintainer's Assign dialog).
  // L-125: a profile just created has no cover but this one: giving it to others is then the likely next step, kept
  // beside the primary where the bar folds the rest under «Altre opzioni» (phone); an updated profile's stays there.
  const assign = ["profile_new", "profile_update"].includes(saved.target)
    ? [{ id: "assign-profile", label: t("wiz_T426"), kind: "secondary", ...(saved.target === "profile_new" ? { visible: true } : {}) }] : [];
  // A new profile left the previous one without covers: it can be deleted from the Profiles tab (never by itself).
  if (saved.target === "profile_new" && saved.orphan) screen.note = { tone: "info", text: fill(t("wiz_T431"), { old: saved.orphan, tab: t("profileListTitle") }) };
  // L-26: another device saved it: the same A-34, read only: «Chiudi» alone.
  // L-49: whatever the other device does next (fine-tuning is a preview with no session), nothing more to follow here.
  if (saved.reader) return Object.assign(screen, { code: path === "C" ? "C-34" : "A-34", title: t("wiz_T154"), body: `${body}\n\n${t("wiz_T569")}`,
    actions: [close()], links: [] });
  // Review P2: «Torna all'elenco» first (the primary), then giving the profile to other covers, another cover, this
  // cover's details, and fine-tuning last when it can be run. Where the bar is one column (phone, single column) the
  // wizard keeps the primary visible and puts the others under «Altre opzioni ▾» when they are more than three.
  const fineTune = ctx.previews !== false;
  if (path !== "C") return Object.assign(screen, { code: "A-34", title: t("wiz_T154"), body: fineTune ? `${body}\n\n${t("wiz_T319")}` : body,
    actions: [close(), ...assign, { id: "another", label: t("wiz_T156"), kind: "secondary" }, { id: "open-cover", label: t("wiz_T157"), kind: "secondary" },
      ...(fineTune ? [{ id: "refine", label: t("wiz_T103"), kind: "secondary" }] : [])] });
  return Object.assign(screen, { code: "C-34", title: t("wiz_T154"), body,
    actions: [close(), { id: "another", label: t("wiz_T156"), kind: "secondary" }, { id: "open-cover", label: t("wiz_T157"), kind: "secondary" }] });
}

/** S-01…S-06: a step that ended. Today's sessions end with their reason (repeating starts over);
 * a published `problem` (L2) keeps the earlier measurements and repeats only the step. */
function problemScreen(screen, ctx, reason, step = false) {
  const { t } = ctx;
  const family = { unexpected_movement: ["S-01", "wiz_T240", "wiz_T241", "wiz_T243"], external_command: ["S-01", "wiz_T240", "wiz_T241", "wiz_T243"],
    unexpected_stop: ["S-01", "wiz_T240", "wiz_T241", "wiz_T243"], stopped: ["S-01", "wiz_T240", "wiz_T241", "wiz_T243"],
    not_delivered: ["S-02", "wiz_T244", "wiz_T245", "wiz_T247"], start_timeout: ["S-02", "wiz_T244", "wiz_T245", "wiz_T247"], no_echo: ["S-02", "wiz_T244", "wiz_T245", "wiz_T247"],
    not_stopped: ["S-03", "wiz_T248", "wiz_T249", "wiz_T251"], stop_queue_full: ["S-03", "wiz_T248", "wiz_T249", "wiz_T251"],
    invalid_measurement: ["S-04", "wiz_T252", "wiz_T253", "wiz_T243"], bad_point: ["S-04", "wiz_T252", "wiz_T253", "wiz_T243"],
    travel_timeout: ["S-05", "wiz_T254", "wiz_T255", "wiz_T258"], timeout: ["S-05", "wiz_T254", "wiz_T255", "wiz_T258"],
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
    // `elapsed_s` is the name expected from L2 for the time the run had when it was cut off; without it the line is left out.
    const seconds = ctx.view?.problem?.elapsed_s;
    screen.status = [t(code === "S-02" ? "wiz_T246" : code === "S-03" ? "wiz_T250" : code === "S-05" ? "wiz_T256" : "wiz_T242"),
      ...(code === "S-05" && seconds != null ? [fill(t("wiz_T257"), { seconds: number(seconds, ctx.language || "en", 1) })] : [])];
    screen.actions = [{ id: "repeat_step", label: t(retry), kind: "primary" }, { id: "stop", label: t("wiz_T028"), kind: "stop" }];
  } else {
    // Today's backend discards the session: say what it says, and offer to start over. Two cases say more, from what
    // the panel knows: the stop came from "It did not do what it should", or the lift-off run stopped before ②.
    const key = `calReason_${reason}`;
    // G2-10: the actuator never confirmed the stop: S-01 says it plainly and offers to start over.
    if (reason === "not_stopped") { screen.code = "S-01"; screen.title = t("wiz_T240"); }
    // D-31: the ended session's reason is about the measured value (an interval out of range…), not two presses: a
    // title that says only that it is not valid.
    if (code === "S-04") screen.title = t("wiz_T512");
    // N-15: a Stop this tab did not send came from another tab or device: said so (the maintainer's «Stop richiesto.»
    // does not say where it came from).
    screen.body = reason === "not_stopped" ? t("wiz_T460") : reason === "stopped" && ctx.local?.misbehaved ? t("wiz_T442")
      : reason === "stopped" && ctx.local && !ctx.local.stopSent ? t("wiz_T524")
      : reason === "unexpected_stop" && ctx.view?.step === "lift" ? t("wiz_T443")
      // R-7: our words for an unexpected stop (the maintainer's text speaks of an endpoint not yet confirmed).
      : reason === "unexpected_stop" ? t("wiz_T577")
        : t(key) === key ? t("calPhase_interrupted") : t(key);
    screen.actions = [{ id: "start-over", label: t("wiz_T261"), kind: "primary" }, { id: "close", label: t("wiz_T390"), kind: "secondary" }];
    // L-10: a session interrupted (a Stop from another tab, say) stays open in Home Assistant, holding the cover, until
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
  // L-9: the field refuses only what cannot be physical (more than the travel plus the tape's error); a reading far
  // from the model is accepted with a warning under the field (`band`, drawn by the wizard while typing).
  // L-30: a range, never a single number that sounds exact: the heights the model explains (or the expected height
  // with 7 % of the travel either side), whole centimetres.
  const hint = band?.model_min_cm != null ? tx("wiz_T550", { min: n(Math.floor(band.model_min_cm), 0), max: n(Math.ceil(band.model_max_cm), 0) })
    : expectedHint(t, tx, n, view.expected_cm, view.travel_cm);
  if (!span) return { label: t("wiz_T064"), hint, min: 0.1, max: 10000, kind: "height" };
  return { label: t("wiz_T064"), hint, min: span.min_cm - RANGE_SLACK_CM, max: span.max_cm + RANGE_SLACK_CM, kind: "height",
    travel: view.travel_cm, band: band ? { min: band.min_cm, max: band.max_cm, warning: t("wiz_T530") } : null };
}

/** L-45: a check's gap, whichever way the session names it. */
export const checkGap = (check) => check?.gap_cm ?? (check?.deviation_cm != null ? Math.abs(check.deviation_cm) : null);

/**
 * L-45: the base calibration's check as a result: a tile «Verifica» with its gap, and a note with the heights (the
 * expected one «circa») and what the gap means against the threshold (the session's own, said once in A-21v).
 */
export function checkResult(ctx, tx, n, check, threshold = 4) {
  const gap = checkGap(check);
  if (gap == null) return null;
  const passed = check.passed ?? gap <= threshold, values = { measured: n(check.measured_cm), expected: n(check.expected_cm ?? check.target_cm, 0), gap: n(gap, 0), threshold: n(threshold, 0) };
  // L-55: the tile is «Precisione» (the gap at half travel); its note beside it, green within the threshold (Nota
  // «Successo»), yellow beyond (Avviso).
  return { passed, tile: { label: ctx.t("wiz_T570"), value: n(gap, 0), unit: "cm", caption: ctx.t("wiz_T571"),
    note: { tone: passed ? "success" : "warning", text: `${tx("wiz_T561", values)} ${tx(passed ? "wiz_T562" : "wiz_T563", values)}` } } };
}

/** L-44: the two stages of the check's wait: to the end stop (its time), then the timed run to its target (planned). */
function checkStages(tx, n, view, endTime, first) {
  const up = view.check?.direction !== "opening", whole = (seconds) => n(Math.ceil((seconds ?? 0) - 0.001), 0);
  return [{ text: tx(up ? "wiz_T084" : "wiz_T120", { seconds: whole(endTime) }), state: first ? "current" : "done" },
    { text: tx(up ? "wiz_T559" : "wiz_T560", { seconds: whole(view.check?.planned_seconds) }), state: first ? "future" : "current" }];
}

/** L-30: the hint of a reading with one expected height: «usually between» it ± 7 % of the travel (whole cm). */
function expectedHint(t, tx, n, expected, travel) {
  if (expected == null) return t("wiz_T491");
  const spread = (travel > 0 ? travel : expected * 2) * 0.07;
  return tx("wiz_T550", { min: n(Math.max(0, Math.floor(expected - spread)), 0), max: n(Math.ceil(travel > 0 ? Math.min(travel, expected + spread) : expected + spread), 0) });
}

/**
 * L-9: the intermediate readings kept although far from the model, as notes in the summary. L-87: a check that passed
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
  if (MOTOR_STOPPED(view)) return { value: 1, remaining: null };
  if (!(seconds > 0)) return { value: null, remaining: null };
  const done = ["opening", "closing"].includes(view.phase) && elapsed != null ? Math.min(seconds, Math.max(0, elapsed)) : 0;
  // L-120: the estimate run, the bar is complete and says no time left: the instant its confirm comes on.
  if (done >= seconds) return { value: 1, remaining: null };
  return { value: done / seconds, remaining: secondsLeft(seconds - done) };
}


/** Whole seconds left, never 0 while the bar is shown (sums of tenths are not exact: 11.000000001 is 11). */
function secondsLeft(seconds) { return Math.max(1, Math.ceil(seconds - 0.001)); }

/** A stop frame before this share of the estimated run is "earlier than expected" (R7-9). */
const EARLY_STOP_SHARE = 0.6;

/**
 * L-120b: when the panel writes the Stop of the close before a times-only measurement (C-04): its estimated closing
 * time and a margin, as an action on the motor (not a gate of the screen) deserves. L-120c: max(4 s, 25 %): a few
 * seconds more cost nothing, an early Stop would leave the cover short of the bottom and block the measurement.
 */
export const homeStopAt = (seconds) => (seconds > 0 ? seconds + Math.max(4, seconds * 0.25) : null);

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
 * where it sits. L-68: on every step the order is title → drawing → text (→ inputs → actions): the readings with the tape
 * too, which used to show the text first. Every other screen has none (A-09 too: its frame shows only the wait box).
 */
const BEFORE = "before-body";
const ILLUSTRATION_OF = {
  A: { "05": ["window-closed", BEFORE], "10": ["window-closed", BEFORE], "06": ["two-runs", BEFORE], "07": ["lift-sequence", BEFORE], "08": ["lift-zoom", BEFORE],
    "11": ["ascent-sequence", BEFORE], "13": ["curtain-open-tape", BEFORE], "14": ["descent-sequence", BEFORE],
    "16": ["curtain-partial-tape", BEFORE], "18": ["curtain-partial-tape", BEFORE], "20": ["curtain-partial-tape", BEFORE],
    "23": ["curtain-partial-tape", BEFORE], "25": ["curtain-partial-tape", BEFORE], "27": ["curtain-partial-tape", BEFORE],
    "29": ["curtain-partial-tape", BEFORE], "31": ["curtain-partial-tape", BEFORE], "34": ["check", BEFORE] },
  B: { "02": ["curtain-open-tape", BEFORE], "06": ["curtain-partial-tape", BEFORE] },
};
ILLUSTRATION_OF.C = ILLUSTRATION_OF.A;

/**
 * Z-14 (Figma, 9 Oct): the drawing that fits the moment. A reading whose expected height is known shows the edge at the
 * closest of a quarter, half and three quarters of the travel (Y-a: B-06, the readings of fine-tuning and of «solo
 * letture», A-18/A-20); A-23, the quarter on the way up, its own ascent drawing (Z-12); without slats the full ascent
 * and descent show a curtain with no slats (N-13).
 */
function drawingFor(code, id, view, local) {
  const slats = view && !view.preview ? view.slats !== false : local?.slats !== false;
  if (!slats && /^[AC]-11$/.test(code)) return "ascent-sequence-no-slats";
  if (!slats && /^[AC]-14$/.test(code)) return "descent-sequence-no-slats";
  if (id !== "curtain-partial-tape") return id;
  // L-12: every reading uses the drawing of its height without an arrow, A-23 too (the arrow added nothing).
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
  if (screen.illustration) screen.illustration = { ...screen.illustration, id: drawingFor(screen.code, screen.illustration.id, view, local) };
  const n = (value, digits = 1) => number(value, language, digits);
  // Under a wait's bar, the time left when its duration is known.
  if (screen.wait) screen.wait.remaining = screen.wait.progress?.remaining != null ? fill(t("wiz_T402"), { seconds: n(screen.wait.progress.remaining, 0) }) : "";
  const refining = (view?.preview && view.path === "refine") || local.refining;
  if (["choose", "path"].includes(ctx.stage)) screen.caption = "";
  // D-21: fine-tuning before its case is chosen (A-02 with it ticked, C-01): only the introduction, no count.
  // L-11: on C-01 and C-02 the steps of the case preselected (the tape readings only) are shown at once, and follow the
  // choice; only A-02 with fine-tuning ticked, before any case, shows the introduction alone (D-21).
  const presetCase = (screen.stepPath || path) === "C" && ["current", "case"].includes(ctx.stage) ? (screen.stepScope ?? scope ?? (ctx.previews === false ? null : "points_only")) : null;
  const unknownCase = (screen.stepPath || path) === "C" && !(screen.stepScope ?? scope) && !presetCase && !refining && ["path", "current"].includes(ctx.stage);
  const phases = unknownCase ? ["intro"] : phasesFor(screen.stepPath || path, presetCase ?? screen.stepScope ?? scope, refining, ctx.previews !== false);
  let current = phases.indexOf(screen.phase);
  if (refining && screen.phase === "summary") current = phases.length - 1;
  // Y-d: the end of a preview saved nothing: its summary step stays the current one, not ticked as done.
  const previewEnd = screen.phase === "done" && screen.preview;
  if (screen.phase === "done") current = previewEnd ? phases.indexOf("summary") : phases.length;
  const source = view && !view.preview ? view : null;
  const base = local.base || {};
  const values = source?.values || {}, samples = source?.samples || {}, readings = { ...(local.baseReadings || {}), ...(source?.readings || {}) };
  // D-33: times always with one decimal («5,0 s» beside «4,9 s», R6-7).
  const seconds = (value) => value == null ? "—" : `${fixed(value, language, 1)} s`, cm = (value) => value == null ? "—" : `${n(value)} cm`;
  // D-11: a time judged not valid, or discarded by its repeat and not yet measured again, is not shown as a result.
  const kept = (step, value) => local.invalidRun?.step === step || (local.discarded?.step === step && local.discarded.value === value) ? null : value;
  const slats = source ? source.slats !== false : local.slats !== false;
  const sub = {
    // The path chosen ("Ⓐ first of its type") is the introduction's sub-line, as the mockups' stepper shows it.
    intro: screen.caption ? [["", screen.caption]] : [],
    ascent: [...(slats && !(path === "C" && scope === "times_only") ? [[t("wiz_T039"), (samples.lift ?? base.lift) != null
      ? `${seconds(samples.lift ?? base.lift)} · ${(readings.gap ?? base.gap) != null ? cm(readings.gap ?? base.gap) : "—"}` : "—"]] : []),
      [t("wiz_T040"), seconds(kept("opening", values.opening_time ?? base.opening))],
      // L-41: the travel typed and not yet sent (L-13) is shown as soon as it is known, as its tile does.
      ...(path === "C" && scope === "times_only" ? [] : [[t("wiz_T041"), cm(local.travelTyped ?? source?.travel_cm ?? base.travel)]])],
    descent: [[t("wiz_T068"), seconds(kept("closing", values.closing_time ?? base.closing))]],
    readings: path === "B" ? [[t("wiz_T041"), cm(view?.travel_cm ?? local.fineReadings?.travel)],
      [t("wiz_T119"), (view?.check ?? local.fineReadings?.check) ? `${(view?.check ?? local.fineReadings.check).gap_cm} cm` : "—"]]
      : path === "C" && scope === "points_only" ? fineSub(t, view, cm, local.fineReadings)
        : [[t("wiz_T077"), cm(readings.half_open)], [t("wiz_T078"), cm(readings.half_close)]],
    // L-43: the check's result once done, as the summary's precision says it.
    // (On A-34 the session is closed: the check kept with what was saved.)
    check: checkGap(source?.check ?? local.saved?.check) != null ? [[t("wiz_T557"), `${n(checkGap(source?.check ?? local.saved.check), 0)} cm`]] : [],
    finetune: refining ? fineSub(t, view, cm, local.fineReadings) : [],
  };
  const finetuneLabel = refining ? t("wiz_T114") : t("wiz_T013");
  screen.stepper = {
    interrupted: !!screen.interrupted,
    // Review: before the path is fixed (A-01, A-02) the step without a total, which the path will decide.
    // L-123: after a real save the line names the last step completed, as every other line names its step
    // («Riepilogo · 7 di 8» on path A, whose optional fine-tuning stays ahead; «Riepilogo · N di N» elsewhere).
    line: previewEnd ? t("wiz_T517") : screen.phase === "done" ? fill(t("wizPhaseLine"), { phase: t(PHASES.summary), n: phases.indexOf("summary") + 1, total: phases.length })
      : unknownCase ? t(PHASES.intro)
      : ["choose", "path"].includes(ctx.stage) ? fill(t("wiz_T585"), { phase: t(PHASES[screen.phase] || "wiz_T007"), n: current + 1 })
      : fill(t(screen.interrupted ? "wiz_T461" : "wizPhaseLine"), { phase: screen.phase === "finetune" ? t("wiz_T114") : t(PHASES[screen.phase] || "wiz_T007"),
      n: current + 1, total: phases.length }),
    phases: phases.map((key, index) => ({ key, label: key === "finetune" ? finetuneLabel : t(PHASES[key]),
      // A-34 after a real save: fine-tuning (optional) is still ahead, so it is not ticked.
      state: key === "finetune" && screen.code === "A-34" && !screen.preview && !refining ? "future"
        : index < current ? "done" : index === current ? "current" : "future",
      substeps: index <= current ? (sub[key] || []).map(([label, value]) => ({ label, value })) : [] })),
  };
  // L-23/L-23b: a reader (another tab or device guides it, or this one handed control over) sees the whole step: its
  // texts, results, notes and field (read only). L-58: its bar holds only what it can do: the note saying who guides
  // it, «Prendi il controllo» and, while the cover moves, «Ferma la tapparella» (a safety control for every device).
  // The owner's buttons are not drawn; the reader's note says who guides and confirms the steps (L-75: once, there).
  // L-77: no Stop at rest, whoever looks. Within a movement each step still decides (U-9: once a run to an end stop has
  // run out, its state replaces Stop), and the reader keeps exactly the owner's decision.
  // The close Home Assistant makes before a session (L-38, no session yet) is a movement too.
  const moving = IN_MOTION(view, status) || !!(local.homing && !view);
  // L-116: a pinned Stop (bars with the ①/② pair) stays in its place, off while nothing moves; any other Stop only
  // while something moves.
  screen.actions = screen.actions.filter((action) => action.id !== "stop" || moving || action.pinned)
    .map((action) => (action.id === "stop" && action.pinned ? { ...action, disabled: !moving } : action));
  // L-113: a reader never restarts or cancels: on an interrupted session it keeps the reader's bar too, with «Torna
  // all'elenco»; «Prendi il controllo» only while Home Assistant keeps the session (`recoverable`, within its lease).
  // «Ricomincia» and «Annulla la misurazione» are the owner's, after a claim.
  const readerEnded = !!view?.read_only && !view.preview && view.phase === "interrupted";
  if (view?.read_only && (!ENDED.includes(view.phase) || readerEnded)) {
    const claimable = !readerEnded || !!view.recoverable;
    // L-58b: the armed confirmation counts its seconds down (`left`) before it goes back.
    screen.readOnly = { text: t("wiz_T263"), take: !claimable ? null : status.confirmTake ? t("calConfirmTakeControl") : t("wiz_T264"),
      left: status.confirmTake && status.confirmLeft ? status.confirmLeft : null, consequence: claimable && status.confirmTake ? t("wiz_T265") : null };
    // L-119: the run's state that replaces Stop in place («La corsa è finita») is the reader's too: its bar keeps its slot.
    screen.actions = screen.actions.filter((action) => ["stop", "stopped"].includes(action.id) || (readerEnded && action.id === "close"));
    // The header keeps the reader's «Smetti di seguire».
    if (readerEnded) screen.discard = true;
    screen.links = [];
    if (screen.field) screen.field = { ...screen.field, disabled: true };
  }
  // Alone in a wait, Stop is the red filled button of the mockups; beside a step's buttons it stays a red link.
  // L-47: Stop is outlined red everywhere, alone in a wait too (one rule; no red is filled).
  // The backend hands the cover back after a stretch without any step (600 s after a movement; heartbeats do not
  // extend it). Two minutes before, a screen that waits for the user says so, counting the minutes down.
  if (view && !view.preview && view.idle_expires_at && !status.expired && !ENDED.includes(view.phase) && !MOVING(view)) {
    const left = Date.parse(view.idle_expires_at) - (ctx.now ?? Date.now());
    // L-24: under two minutes the time left counts down in minutes and seconds (the wizard updates only that text).
    if (left > 0 && left <= LEASE_WARNING_MS) screen.notices.unshift({ tone: "warning", result: true, text: fill(t("wiz_T541"), { time: clock(left) }),
      countdown: { expires: Date.parse(view.idle_expires_at), template: t("wiz_T541") } });
  }
  if (status.lost && !status.expired && view && !ENDED.includes(view.phase)) {
    // A missed heartbeat (the phone put the page aside, the connection dropped for a moment) is a calm status while the
    // panel reconnects; a lost presence for any other reason stays an error.
    // L-119b: «Riprendi» belongs to that notice, in the content: the bar of the step stays as it is.
    screen.notices.push({ ...(status.connection === "interrupted" ? { tone: "info", text: t("wiz_T446") } : { tone: "error", text: t("calVisual_disconnected") }),
      ...(view.recoverable ? { action: { id: "reconnect", label: t("calResume") } } : {}) });
  }
  if (status.notice) screen.notices.push({ tone: "warning", text: t(status.notice) });
  if (status.cancelPending) screen.notices.push({ tone: "error", text: t("calCancelFailed") });
  if (status.error) {
    // L-9: our own refusal of a reading above the travel, in our words.
    const code = status.error.code;
    // Our own words for some refusals (L-9 a reading above the travel, L-78 a check the cover cannot make).
    const key = OUR_REFUSALS[code] || `profileError_${code}`;
    const values = { ...(view || {}), ...(readingRange(view?.reading_range, language, status.error.reading) || {}),
      ...(code === "reading_over_travel" ? { reading: n(status.error.reading), travel: n(view?.travel_cm) } : {}), code };
    // L-78: the connection's text only when nothing came back (no code, or the socket's own number); a refusal Home
    // Assistant sent with a code of its own, even one without a text here, says so and names it.
    const transport = code == null || typeof code === "number";
    const text = t(key) !== key ? fill(t(key), values) : view?.preview ? t("calReadingNumber") : transport ? t("calConnectionError") : fill(t("wiz_T574"), values);
    // A refusal whose values are gone (the session moved on) is not shown with empty placeholders.
    if (!/\{\w+\}/.test(text)) screen.notices.push({ tone: "error", text });
  }
  // A reading refused in the field, before anything is sent.
  // G3-4: outside the field's own limits (or not a number) the field says so under itself.
  // (A reading outside the fit's range keeps its notice with the accepted range.)
  if (local.fieldError != null && screen.field && !(screen.field.range && typeof local.fieldError === "number")) {
    const value = local.fieldError, { min, max } = screen.field;
    screen.field.error = value === "nan" ? t("wiz_T472") : value > max && screen.field.travel != null ? fill(t("wiz_T531"), { reading: n(value), travel: n(screen.field.travel) })
      : value > max ? fill(t("wiz_T470"), { max: n(max) })
      : value < min ? fill(t("wiz_T471"), { min: n(min) }) : null;
  }
  if (typeof local.fieldError === "number" && screen.field?.range) {
    screen.notices.push({ tone: "error", text: fill(t("profileError_reading_out_of_range"), readingRange(view.reading_range, language, local.fieldError)) });
  }
  // D-13 (decided 8 Oct): a button off while the motor starts or the backend settles after a run enables by itself.
  if (view && (/^starting_/.test(view.phase) || view.phase === "geometry_wait_stop")) for (const action of screen.actions) if (action.disabled) action.settling = true;
  // Movement, readings and Save wait for an answer, a connection and ownership; Stop never does (also a passing state).
  if (status.locked) for (const action of screen.actions) if (!["stop", "reconnect", "close", "start-over", "abort"].includes(action.id)) { if (!action.disabled) action.settling = true; action.disabled = true; }
  if (status.locked && screen.field) screen.field = { ...screen.field, disabled: true };
  return screen;
}

function fineSub(t, view, cm, kept = {}) {
  const readings = view?.preview ? view.readings || {} : kept || {};
  const check = view?.preview ? view.check : kept?.check;
  return [[t("wiz_T115"), cm(readings.quarter_open)], [t("wiz_T116"), cm(readings.three_quarter_open)],
    [t("wiz_T117"), cm(readings.quarter_close)], [t("wiz_T118"), cm(readings.three_quarter_close)],
    [t("wiz_T119"), check ? `${check.gap_cm} cm` : "—"]];
}
