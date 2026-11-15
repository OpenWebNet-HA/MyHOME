# Calibration tab and wizard (panel 0.43.0)

> Texts no screen draws any more were removed from the panel in the final review (0.43.0): calTabBusy, calTabEmptyTitle, calTabIntro, wizTopReached, T038, T083, T086, T100, T112, T123, T170, T173, T177, T201, T262, T268, T281, T282, T283, T285, T286, T289, T323, T324, T325, T326, T327, T328, T329, T330, T331, T332, T333, T334, T335, T336, T337, T338, T339, T340, T341, T342, T343, T344, T345, T346, T347, T348, T349, T350, T351, T352, T353, T354, T355, T356, T357, T358, T359, T360, T361, T362, T363, T367, T375, T376, T380, T389, T393, T394, T396, T397, T398, T421, T449, T451, T462, T508, T527, T528, T547. Where the tables below still name one of them, it is the mockup's text, not the panel's.

Panel 0.43.0 adds a **Calibration** view to WHO 2 covers, next to **Devices** and **Profiles**, and a calibration
wizard that opens inside it. The backend is unchanged: every step that exists today runs on the current session
API ([WebSocket API](panel-websocket-api.md)); the steps whose verbs do not exist yet run as a local **preview**
that sends nothing to Home Assistant and says so on screen. The profile dialog keeps its calibration section and
adds **New guided measurement**, which opens the wizard on the same cover.

This page maps each screen of the design mockups (boards T-xx, A-xx, B-xx, C-xx, S-xx) to the code that draws it,
the texts it uses and the message it sends.

## Files

| File | Role |
| --- | --- |
| `panel-calibration-tab.js` | The Calibration view: list, filters, expanded row, row menu, Assign profile, open session, several covers in sequence |
| `panel-calibration-wizard.js` | The wizard: layout, events, sessions, automatic continuation, leaving |
| `panel-calibration-screens.js` | `buildScreen()`: which board a moment is, as plain data (no DOM, no messages) |
| `panel-calibration-gateway.js` | `LiveCalibrationGateway` (the existing session) and `PreviewCalibrationGateway` (local) |
| `panel-calibration-illustration.js` | The wizard's drawings, one per illustration id (`window-closed`, `two-runs`, `lift-sequence`, `descent-sequence`, `ascent-sequence`, `lift-zoom`, `curtain-open-tape`, `curtain-partial-tape`, `check`); `buildScreen()` picks the id and its place (`before-body`: under the title, steps and briefings; `after-body`: between the text and the field, tape readings) from the board code (none on A-09, which shows only its wait box; A-16 uses `curtain-partial-tape`). Each id is an inline SVG from `panel-calibration-illustrations-svg.js` (generated from the Figma exports, data only), drawn at the Figma size (shrinks with the column); its colours are `--wiz-ill-*` properties of the wizard, light by default and dark when the wizard has `data-theme="dark"` (from `hass.themes.darkMode`, else the system preference) |
| `panel-calibration-text.js` | `**bold**` and paragraphs from escaped text, localized numbers, dates and lists |
| `panel-translations.js` | `calTab…` (view), `wiz_T001…` (generated from the translation sheet), `wiz…` (screen-only) |

The wizard never builds a WebSocket message itself. `LiveCalibrationGateway` extends the `CoverCalibration` engine of
the profile dialog and only replaces its drawing, so subscriptions, heartbeats, reconnection, take control (two taps
while the owner is present), Cancel and the per-tab identity are the ones already documented and tested.

## Calibration view (T-01…T-05)

| Board | What is shown | Data | Texts |
| --- | --- | --- | --- |
| T-01 list | Timed covers (no position sensor): name, room and bus address (`Address: 86 · A: 8 · PL: 6`), status, profile (the date of the last measurement is in the details, and on phones under the name); the Roller shutter, Status and Profile headers sort the list (ascending, descending, back to the default: name ascending; a running cover always first; Status from to measure to times and roll; covers without a profile last in both orders; the order is kept per browser in `localStorage` `myhome-panel-calibration-sort-v1` and applies with the active filter; on phones a *Sort by* bar, T379–T382); Measure / Fine-tune; ⋮ with every secondary action (Fine-tune, Measure or Remeasure, Assign profile, Details); the whole row opens its details | `cover_profiles/overview` per gateway | `calTabTitle`, `calTabIntro`, `calTabCol*`, `calTabStatus_*`, `calTabMeasuredOn`, T363 |
| T-02 filter | All / To measure / Calibrated / With profile, with counts as grey badges; help note on To measure | same | `calTabFilter_*`, `calTabCount*`, `calTabToMeasureHelp` |
| T-03 session | The covers of the open session marked In progress (a tab guides it / no tab guides it / paused / waiting for the stop) with Open session; new measurements disabled | `cover_profiles/read` on one cover: its `calibration` is the gateway's single session | `calTabRunning_*`, `calTabBusy`, `calOpenSession` |
| T-04 empty | No timed cover | overview | `calTabEmpty*`, `calTabHowItWorks*` |
| T-05 details | Row expanded in place: three blocks of at most 360 px (values in use, last measurement and method, profile and who else follows it), in columns while they fit; notes in one shared style (`.cal-note`), including where the times of a never measured cover come from (defaults or YAML); each block ends with its own action, left-aligned 20 px under its content: values → Fine-tune (only with measured rolls), last measurement → Remeasure or Measure, profile → Assign profile | overview `effective` values, origin and provenance | `calTabValues`, `calTabSlats*`, `calTabMethod_*`, `calTabFollowers*`, `wiz_T093`, `wiz_T094`, `wiz_T041`, `wiz_T105`, `wiz_T106`, T377, T378 |

The session is read from one cover (up to three are tried in turn if a read fails); when none answers but the
overview reports the gateway reserved (`assignment_reason: calibration_busy`), new measurements stay disabled.

Status comes from the saved evidence: **To measure** without a guided or automatic measurement, **Times measured**
with one, **Times and roll** with roll coefficients. *Measure several covers in sequence* replaces the list with the
selection view T-06 (no dialog): *Back to the list*, the note on the automatic cycle, *Select: All · To measure · None*,
the gateway's `cover_calibration/targets` as the list's rows (checkbox, place in the sequence, icon, name, room and
address, status, profile), sorted by the headers as the list (same stored choice, *Sort by* bar on phones), with the backend's `max_batch`;
covers run in the order they are ticked, whatever the order of the rows.
An action bar is fixed to the bottom of the window over the width of the view (summary, Cancel, *Start the sequence*;
`position: fixed`, placed from the view's box, since inside Home Assistant the panel's own box is not the one that
scrolls), with a fade above it while the list continues. The note is the warning variant of `.cal-note`. Start sends `batch_start` once, with that order and the targets' revision; the run itself is, for
now, the existing view in a dialog (T-07/T-08 next) (T363–T366, T383–T391). *Assign profile* opens a dialog listing the gateway's profiles as on B-01
(type with or without slats, where and when measured, reference travel, how many covers follow it) and assigns the
chosen one with `cover_profiles/write` `assign` (T368–T376); it never opens the Travel profile dialog. *Open session*
opens the wizard for a `geometry` session and the existing view, in the same dialog, for guided, automatic and
sequence sessions. Above 1024 px the rows are columns (cover, status, profile, actions; the three blocks of the details
sit under the first three), below they are cards. Rendering the view sends no calibration message.

## Wizard layout

Desktop (≥ 1024 px): header with the cover name, its address and **Discard calibration**; stepper (left), instruction
(centre), actions (right). Phone: a full screen with the header and ✕, a one-line stepper with **Steps** (a sheet with
the whole stepper), the instruction scrolling, the actions anchored at the bottom. The stepper shows the phases of the
path, the current one half filled, under the introduction the path chosen (T026 *Ⓐ first of its type*, T169, T200,
T231, T236) and under each other reached phase its measured values. The stopwatch appears only in
the timed runs. Every wait has a progress bar: its duration comes from the times in use (a positioning from the
closing or opening time in use, the integration's default included, times the share of the travel still ahead from the
cover's `current_position` when the run starts (already there: 2 s until the bus confirms; unknown position: the full
time; the estimate is only as good as times not yet measured), a half run from the measured times, a simulated step from its stages), with the time left
under it (T402, provisional); when the duration is unknown (no time at all, or a check run Home Assistant stops) the bar
moves back and forth and the time left is omitted. Stages carry ✓ when done and ◐ while running. In the runs the user confirms (A-05, A-10, the full ascent A-11 and descent A-14, the
return to the top, the guided runs of C) Stop is offered only while the motor runs: once the view says `motor_stopped`
the stopwatch stays at `stopped_elapsed` (T257), a wait's bar is shown complete and only the confirmation is left; the
lift-off run A-07 is unchanged (a stop there still ends the session). The positionings to an end stop (A-04, A-09, the returns to the top) offer the end stop as the primary from the
start (T032, or T432 towards the top), since the estimate comes from times not yet measured. Stop is a red filled
button when it is a wait's only action and a red text link beside a step's buttons (the timed runs); text actions
(*Continue anyway*, A-34's *Close*) are red text; *Close* keeps each mockup's style elsewhere (B-09 red filled, C-34 and
S-06 red outlined); radios are plain rows and the advised path's badge is accent blue. Every new step moves the focus
to its title; the Steps sheet and the Discard dialog keep Tab inside and close with Escape, and closing the dialog
returns the focus to the screen's confirming button (or its title). After every change of screen or of its buttons the
confirming buttons stay disabled for `CONFIRM_GUARD_MS` (700 ms), so a slow double tap never confirms an end stop the
cover has not reached nor skips the second tap of *Take control*; Stop is never held back.

## Path A — first cover of its type

Live: `myhome/cover_calibration/start` with `mode: "geometry"` (and `slats: false` for a cover without slats), then
`myhome/cover_calibration/action`. "Auto" marks the `next` the wizard sends after the user's previous press, because
the mockups show that movement without a press. **The rule** (`CalibrationWizard._autoNext`): an automatic verb is
sent only on the view that immediately follows a press of the user in this tab (through the movement and the stop
that press started, never past a stop that waits for the user), once per `sequence`, and never after a replayed
subscription (a new `attachment`: Home Assistant reconnected), a reconnection, a claim, a lost connection, a refused
verb or a view this tab does not own. When it does not happen, the screen offers the same `next` as a button.

| Board | Session (`phase` / `step`) | Texts | Sent |
| --- | --- | --- | --- |
| A-01 | before the session: the covers as the sequence view's rows (name, room, bus address, status, profile) with one radio each, the list's filter chips and shared order (drawn by `CalibrationTab._pickerMarkup`); the chosen row opens with one warning note, T413 (already calibrated, with the profile it follows) or T414 (no profile); a cover that only follows a profile shows T395 | T001, T003–T006, T392, T395, T413, T414 | nothing |
| A-02 | before the session: the cover's context as an information box (T395 profile followed, T399 calibrated, T418 never measured); advice (`pathAdvice`): calibrated → badge Ⓒ; never measured with a saved travel within ±5 % of a profile's reference → badge Ⓑ with the matches under it (T422, closest first); never measured with profiles → no badge, nothing preselected, the profiles listed under Ⓑ (name · slats · reference travel); no profile → badge Ⓐ. Under Ⓑ the count (T419/T420, T401 when none); Ⓒ disabled on a never measured cover (T423). The intro always ends with T421 naming the advised path. On a calibrated cover another choice shows its warning under it (T415 Ⓐ, T416/T417 Ⓑ) | T015–T019, T395, T399–T401, T415–T423 | nothing |
| A-03 | before the session | T021–T024, T198, T199 (no Cancel T025: the header's *Abandon the calibration* does the same) | `start` on «Understood, begin» |
| A-04 | `briefing`/`starting_close` · `home` | T027, T029, T032 (primary from the start, pressable once the motor runs), T028 (red link) | `next` (auto), `endpoint` |
| A-05 | `closing` · `home` | T030–T033, T028 | shown once the positioning is over (A-04 stays until `motor_stopped` or the closing time in use, default included, has passed); `endpoint` and T033 (outlined) are active at once, the red Stop link stays until `motor_stopped`; T033 sends `stop` and opens S-01, nothing measured yet is lost. A-09/A-10 and the return to the top follow the same rule; A-10 never has T033 (a stop there discards the lift-off) |
| A-06 | `briefing` · `lift` (local briefing) | T035–T037 | nothing |
| A-07 | `briefing`/`opening` · `lift` | T042–T046, T028 | `next`, then `lift` |
| A-08 | `reading` · `lift`; `briefing` · `reset` with `gap_warning` | T047–T051, `calGapWarning` | `reading`; `next` or `repeat` after a wide gap |
| A-09 / A-10 | `reset` | T027, T052; T053, T054, T032 | `next` (auto), `endpoint` |
| A-11 | `briefing`/`opening` · `opening` | T055 (T040 without slats), T056, T045, T057 | `next`, `endpoint` |
| A-11 / A-14 ended by the bus | `motor_stopped` in the full ascent or descent | T444, T445 | ② within 2 s of the stop frame (time since the frame = `elapsed` − `stopped_elapsed`) gives the frame's time, valid; no ② within 2 s: the panel sends `endpoint` itself and A-12/A-15 show the result not valid (T444) with only *Repeat the measurement*; a frame at 59–65 s (actuator timer) without ② before it is not valid either (T445). A press before the frame is valid as before; the lift-off and half runs are not affected |
| A-12 | `reading` · `opening` (local): result tiles «Lift-off s · cm» and «Full ascent s» above the text (T039, T040) | T058–T061 | `repeat` |
| A-13 | `reading` · `opening` | T062–T066 | `reading` (travel, 20–500 cm, saved travel offered) |
| A-14 | `briefing`/`closing` · `closing` | T068–T070 | `next`, `endpoint` |
| A-15 / A-16 | `briefing` · `half_open` (local); A-15 with result tiles «Full descent s» and «Curtain travel cm» (T068, T041) | T071, T072, T060, T061; T074–T076 | `repeat`; `next` |
| A-17 / A-18 | `opening` · `half_open`; `reading` | T027, T079, T080; T081, T082, `calReadingRange`, T051, T033 | `reading`, `repeat` |
| A-19 | `top`, then `closing` · `half_close` | T086, T084, T085, T432 (primary from the start) | `next` (auto), `endpoint` (auto on `motor_stopped` or after the measured opening time + 3 s; the button is a shortcut), `next` (auto) |
| A-20 | `reading` · `half_close` | T087, T088, `calReadingRange` | `reading`, `repeat` |
| A-30 / A-31 | `check`: briefing, run, `reading` (the step «Verifica», started from A-21v with `direction: "closing"` and half the travel) | T027, T086; T138, T139, T123, T051, T033 | `next`, Stop; `reading` (0.1 cm to the travel), `repeat` |
| A-21 / A-21c / A-21d | `review` | T091, T092/T107, T093–T106, T108–T112, T280, T281/T289, `wizTable*`, `wizTechnical` | `save` (`save_mode: "new"`, `name`) |
| A-22…A-33 | preview `refine`, opened by A-34's *Continue with fine-tuning* on the values just saved (the tab's *Fine-tune* opens C-01, C-02 instead) | T113–T152 | nothing (preview) |
| A-34 | after `saved` | T153–T158; after a real save the body adds T319 as its second paragraph and *Continue with fine-tuning* (T103) is the primary action, then *Assign this profile to other covers* (T426, outlined, when a profile was created or updated: the maintainer's Assign dialog, `ProfileCatalogueEditor` action `assign`, on that profile), then *Calibrate another*, *Open the roller shutter page*, *Close* (red text); the stepper leaves *Fine-tuning (optional)* unticked | nothing; *Continue with fine-tuning* opens A-22 at once (same wizard, stepper at Fine-tuning) |

The intermediate readings (A-18, A-20) use the session's `reading_band` and `reading_range` (0.43.0, L-9/L-30): the
hint gives the heights the model explains («di solito fra … e … cm», `model_min_cm`–`model_max_cm`); a value above
`reading_range` (the travel plus 3 cm) is refused in the field (T531, Home Assistant's `reading_over_travel`) before
anything is sent; a value outside `reading_band` is accepted with a warning under the field (T530) and reported in the
summary (`reading_discrepancy`). As in the profile dialog, the intermediate runs show
no elapsed time (Home Assistant stops them); their wait card keeps the stages and a progress bar. A check run, which
Home Assistant stops in the same way, is shown alike.

The summary has one **Save** and the switch *Also save as reusable profile*: on for a first cover, off when the cover
already follows a profile; on with a followed profile offers *Create a new profile* (default) or *Update «…»*. Today a
`geometry` session saves only as a new profile, so saving on the cover only or updating a shared profile shows
`wizSaveUnavailable` and keeps Save disabled. The summary only saves: *Continue with fine-tuning* (T103) is
on A-34 once the base calibration is saved, and opens the fine-tuning A-22…A-34 at once on the values just saved.

## Path B — like a cover already measured (preview)

| Board | Preview state | Texts |
| --- | --- | --- |
| B-01 | before | T161–T165 (one radio row per profile: name · where it was measured · how many covers follow it) |
| B-02 | before | T167, T168, T076 |
| B-03 / B-04 | `positioning` · `open_full`; `reading` · `travel` (skipped when the cover's travel is already saved: B starts at B-05 with that travel) | T027, T170; T062–T066 |
| B-05 / B-06 | `positioning` · `check`; `reading` · `check` | T171, T172, T086; T138, T173, T123 |
| B-07 / B-07b | `check_result` | T141, T175, T060, T143; T144, T176, T177 (opens path C) |
| B-08 / B-09 | `review`; `saved` | T179–T181, T280, T284; T154, T182, T158 |

## Path C — has a profile but stops at the wrong point

C-01 (T184–T188) shows what the cover uses (a box with a bold title and one line per value); C-02 (T189–T196) chooses the case:

| Case | Runs on | Boards | Save |
| --- | --- | --- | --- |
| Everything | live `geometry`, as path A | C-03…C-21 (T197, T201–T203, T205, T210, T285), fine-tuning preview C-22…C-33 (T226, T286), C-34 (T228) | new profile live; on the cover only waits for the backend |
| Times only | live `guided` (both directions) | C-35 (T230, T231), C-05 · C-11 · C-12 · C-14 with `calPhase_*`, C-36 (T179, T233, T287) | `save_mode: "cover"` |
| Tape readings only (preselected, as in the mockup) | preview `rolls` | C-37 (T235, T236), C-22…C-32, C-38 (T149, T238, T239, T288), C-34 | preview |

## States

| Board | When | Texts | Actions |
| --- | --- | --- | --- |
| S-01…S-05 | the session ended with a reason (`interrupted`) | titles T240, T244, T248, T252, T254 by family; body `calReason_<reason>` | Start over (T261), Close |
| S-01…S-05 | a published `problem` (future): the motor state as plain lines under the text, S-05 also the stopped stopwatch (T257, from `problem.elapsed_s` when L2 sends it) | T240–T258 | `repeat_step`, Stop |
| S-06 | `expired` | T259, T260 | Start over, Close |
| S-07 | read-only view: in the actions column T262 and T263 as plain text, then Take control (outlined while the owner is present) | T262–T265, `calConfirmTakeControl`; no reading field | Take control (two taps while the owner is present); Stop while it may move |
| — | Leaving the tab (another tab, gateway, category, cover view, link, type or area filter) while this tab owns a live session: *Calibration in progress* asks through the shell's `setBeforeLeave` guard; the browser's `beforeunload` warning while it is open | T408, T409 ({minutes} from `recovery_seconds`, 30 by default), T410, T411, T269 | *Continue calibration* stays; *Leave and resume later* leaves it open (the list shows it first with *Open session*); *Leave without saving* sends `cancel` first |
| S-08 | Discard during a session: *Exit without saving* as red text on the left, *Continue the calibration* (primary) on the right | T267, T269, T270, `wizExitBodyLive` (live) / T268 (preview) | `cancel` (two buttons only: Stop stays on the page) |

A session reopened from the list is drawn as path C (remeasure everything) when its cover already follows a profile,
otherwise as path A; its slat choice comes from the view.

## Preview steps and the backend they wait for

| Preview | Future backend | Where to connect |
| --- | --- | --- |
| Fine-tuning `refine` (A, C) | `refine` on the record just saved | `CalibrationWizard._startRefine` |
| Path B `profile` | mode `profile_check`; save = follow the profile with the measured travel | `CalibrationWizard._begin`, `_save` |
| Path C `rolls` | mode `rolls` | `CalibrationWizard._begin` |
| Save targets `cover_only` / `profile_update` in `geometry` | unified `save {target}` | `LiveCalibrationGateway.supports`, `save` |
| Session in the overview | `calibration` in `cover_profiles/overview` | `CalibrationTab._read` |
| Verified status, start time | check result stored with its evidence; `started_at` | `coverStatus`, `CalibrationTab._row` |
| Problems as steps | `problem` + `repeat_step` | already drawn by `buildScreen` |

The preview is announced before it starts: a line (T318) under path B on A-02, under the start of B-02 and C-37. The end of a
preview is titled *Preview of the summary* (T320) and says that nothing was saved (T321) or that only the calibration before the
fine-tuning was (T322).

The preview check rounds the gap half up to the whole centimetre and passes up to 4 cm (`roundHalfUp`,
`CHECK_THRESHOLD_CM`); its heights come from a rough linear model only meant to fill the screens.

## Texts

`wiz_T…` keys are generated from the translation sheet (`ui-texts-en.csv`, columns `italian` and `english`): the
example names and numbers of the mockups become placeholders (`{cover}`, `{profile}`, `{seconds}`, `{cm}`, `{gap}`,
`{travel}`, …), `**…**` marks bold text, blank lines separate paragraphs. Texts with a count have zero and one forms
(rows T305–T317, chosen by `counted()` in `panel-calibration-screens.js`); the tab summary uses `calTabCount*` and
`calTabCount*_one`. Rows that only describe the mockups (example
names, scrims, phone annotations), the phase lines (built from `wizPhaseLine`) and copies of identical texts are
left out. `calTab…` and the few `wiz…` keys without a `_T` number are screen texts not in the sheet yet.

## Tests

`tests/frontend/calibration-tab.test.mjs`, `calibration-wizard.test.mjs`, `calibration-path-b.test.mjs` and
`calibration-path-c.test.mjs` drive the panel with `fake-calibration.mjs`, a stand-in for Home Assistant whose
`geometry` and `guided` transitions follow the backend modules; bus events are explicit calls, so each test decides
when the cover moves and checks the exact messages sent.
