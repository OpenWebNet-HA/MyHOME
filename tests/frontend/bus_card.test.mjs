/**
 * Behavioural tests for the bus-monitor Lovelace card (run with `node --test tests/frontend`).
 *
 * Covers the capture workflow (Start/Stop Trace, Sweep Bus, Clear), the labels that
 * follow it, the self-describing export, and the armed transmit bar.
 */
import { test } from "node:test";
import assert from "node:assert/strict";
import { loadCard, frame } from "./harness.mjs";

const { create, el, downloads } = loadCard();

const labels = () => [el("btn-trace").innerHTML, el("btn-export").innerHTML, el("btn-report").innerHTML];

test("idle card is a live trace: default labels and kind", () => {
  const card = create();
  card._refreshExportLabel();
  card._setTracing(false);
  card._updateBadge();
  assert.equal(card._captureKind(), "trace");
  assert.deepEqual(labels(), ["🔴 Start Trace", "💾 Export Trace", "📋 Copy Trace"]);
  assert.equal(el("badge").textContent, "LIVE");
});

test("Start Trace clears, records (REC badge) and toggles to Stop Trace", async () => {
  const card = create();
  card._frames = [frame("*1*1*12##", { who: "1", where: "12" })];
  await card._handleStartTrace();
  assert.equal(card._frames.length, 0);
  assert.equal(card._tracing, true);
  assert.equal(card._isPaused, false);
  assert.equal(el("btn-trace").innerHTML, "⏹ Stop Trace");
  assert.equal(el("badge").textContent, "● REC");
  assert.equal(card._captureKind(), "trace");
});

test("Stop Trace freezes the buffer (paused) and keeps the frames for export", async () => {
  const card = create();
  await card._handleStartTrace();
  card._frames = [frame("*2*2*21##", { timestamp: 5 })];
  await card._handleStartTrace(); // stop
  assert.equal(card._tracing, false);
  assert.equal(card._isPaused, true);
  assert.equal(card._frames.length, 1);
  assert.equal(el("btn-trace").innerHTML, "🔴 Start Trace");
  assert.equal(el("badge").textContent, "PAUSED");
  assert.equal(el("btn-pause").textContent, "Resume");
});

test("Pause while tracing ends the trace; Resume returns to the live view without clearing", async () => {
  const card = create();
  await card._handleStartTrace();
  card._frames = [frame("*2*2*21##")];
  card._togglePause();
  assert.equal(card._tracing, false);
  assert.equal(card._isPaused, true);
  card._togglePause();
  assert.equal(card._isPaused, false);
  assert.equal(card._frames.length, 1);
  assert.equal(el("badge").textContent, "LIVE");
});

test("Sweep Bus clears, calls the service and switches labels to Sweep", async () => {
  const card = create();
  card._frames = [frame("*1*1*12##", { who: "1", where: "12" })];
  await card._handleSweepBus();
  assert.deepEqual(card.services, ["myhome.sweep_bus"]);
  assert.equal(card._frames.length, 0);
  assert.equal(card._captureKind(), "sweep");
  assert.deepEqual(labels(), ["🔴 Start Trace", "💾 Export Sweep", "📋 Copy Sweep"]);
});

test("regression: Sweep Bus after Stop Trace resumes the stream so replies are captured", async () => {
  // Seen live on an MH200N: Stop Trace left the card paused, Sweep Bus dropped all 90 replies.
  const card = create();
  await card._handleStartTrace();
  await card._handleStartTrace(); // stop -> paused
  assert.equal(card._isPaused, true);
  await card._handleSweepBus();
  assert.equal(card._isPaused, false);
  assert.equal(el("badge").textContent, "LIVE");
  // a streamed reply is now appended instead of discarded
  card._onNewFrame(frame("*2*0*21##"));
  assert.equal(card._frames.length, 1);
});

test("paused stream discards incoming frames (why Sweep/Start must resume)", async () => {
  const card = create();
  card._togglePause();
  card._onNewFrame(frame("*2*0*21##"));
  assert.equal(card._frames.length, 0);
});

test("Clear returns to trace mode and ends a running trace", async () => {
  const card = create();
  await card._handleSweepBus();
  await card._handleStartTrace();
  await card._clearBuffer();
  assert.equal(card._captureKind(), "trace");
  assert.equal(card._tracing, false);
  assert.deepEqual(labels(), ["🔴 Start Trace", "💾 Export Trace", "📋 Copy Trace"]);
});

test("export is named after the chosen kind, gateway and filter, and describes itself", async () => {
  const card = create({ model: "MH200N" });
  card._frames = [
    frame("*1*1*57##", { timestamp: 100, who: "1", where: "57" }),
    frame("*#2*21##", { timestamp: 101, direction: "tx" }),
    frame("*2*0*21##", { timestamp: 101.2 }),
    frame("*2*2*21##", { timestamp: 105 }),
  ];
  card._filterWho = "2";
  card._filterDir = "rx";
  await card._handleExportTrace();
  const { fileName, payload } = downloads.at(-1);
  assert.match(fileName, /^myhome_trace_MH200N_who2-rx_\d{4}-\d{2}-\d{2}T\d{2}-\d{2}-\d{2}\.json$/);
  assert.equal(payload.capture.kind, "trace");
  assert.deepEqual(payload.capture.filters, { who: "2", where: null, direction: "rx" });
  assert.equal(payload.capture.window.frames, 2);
  assert.equal(payload.capture.window.buffer_frames, 4);
  assert.equal(payload.capture.window.truncated, false);
  assert.deepEqual(payload.frames.map((f) => f.raw), ["*2*0*21##", "*2*2*21##"]);
  assert.deepEqual(payload.frames.map((f) => f.direction), ["rx", "rx"]);
  assert.equal(payload.environment.ownd_version, "2.0.0b6");
});

test("sweep export carries the sweep kind and start time; truncated when the buffer wrapped", async () => {
  const card = create({ model: "F454" });
  await card._handleSweepBus();
  card._maxDisplayFrames = 2;
  card._frames = [frame("*2*0*21##", { timestamp: 10 }), frame("*2*0*22##", { timestamp: 11, where: "22" })];
  await card._handleExportTrace();
  const { fileName, payload } = downloads.at(-1);
  assert.match(fileName, /^myhome_sweep_F454_all_/);
  assert.equal(payload.capture.kind, "sweep");
  assert.ok(payload.capture.started_at);
  assert.equal(payload.capture.window.truncated, true);
});

test("transmit is refused until armed, allowed while armed, refused again after disarming", async () => {
  const card = create();
  const input = el("send-frame");
  input.value = "*5*2*0##";
  await card._sendCustomFrame();
  assert.deepEqual(card.sent, []);
  assert.equal(el("btn-send").disabled, false, "stub default; the markup renders it disabled");

  card._toggleArmed(true);
  assert.equal(el("send-frame").disabled, false);
  assert.equal(el("btn-send").disabled, false);
  assert.equal(el("arm-bar").classList.contains("armed"), true);
  input.value = "*5*2*0##";
  await card._sendCustomFrame();
  assert.deepEqual(card.sent, ["*5*2*0##"]);

  card._toggleArmed(false);
  assert.equal(el("btn-send").disabled, true);
  assert.equal(el("send-frame").disabled, true);
  input.value = "*5*1*0##";
  await card._sendCustomFrame();
  assert.deepEqual(card.sent, ["*5*2*0##"]);
});

test("markup: transmit bar disabled, arm checkbox and help panel present; help toggles", () => {
  const card = create();
  card._render();
  const html = card.shadowRoot.innerHTML;
  assert.match(html, /id="send-frame"[^>]*\bdisabled\b/);
  assert.match(html, /id="btn-send"[^>]*\bdisabled\b/);
  assert.match(html, /id="arm-send"/);
  assert.match(html, /id="help-panel"/);
  assert.match(html, /id="btn-trace"[^>]*>\s*🔴 Start Trace/);
  assert.match(html, /Start Trace<\/em> and <em>Sweep Bus<\/em> above are read-only and safe/);

  // the help button lives in the title row, next to the status badge, before the actions
  const badgeAt = html.indexOf('id="badge"');
  const helpAt = html.indexOf('id="btn-help"');
  const actionsAt = html.indexOf('class="toolbar actions"');
  assert.ok(badgeAt > 0 && helpAt > badgeAt && actionsAt > helpAt, "help button must sit between the badge and the action buttons");
  // toolbar is grouped by intent: capture, output, stream
  const groups = [...html.matchAll(/class="group (capture|output|stream)"/g)].map((m) => m[1]);
  assert.deepEqual(groups, ["capture", "output", "stream"]);

  card._toggleHelp();
  assert.equal(el("help-panel").style.display, "block");
  assert.equal(el("btn-help").classList.contains("open"), true);
  card._toggleHelp();
  assert.equal(el("help-panel").style.display, "none");
  assert.equal(el("btn-help").classList.contains("open"), false);
});

// ── cover calibration panel ──────────────────────────────────────────────

function coverState(entityId, attrs) {
  return { entity_id: entityId, state: "open", attributes: { calibration_source: "default", travel_time_down: 25, travel_time_up: 25, ...attrs } };
}

function withCovers(card, states) {
  card._hass.states = states;
  card._hass.connection = { subscribeEvents: async (cb, type) => { card._hass._eventCb = cb; card._hass._eventType = type; return () => { card._hass._unsubbed = true; }; } };
  return card;
}

test("covers panel lists only timed MyHOME covers, sorted by name, with an honest time estimate", async () => {
  const card = withCovers(create(), {
    "cover.b": coverState("cover.b", { friendly_name: "Kitchen", travel_time_down: 20, calibration_source: "measured", calibrated_at: "2026-09-13T12:00:00Z" }),
    "cover.a": coverState("cover.a", { friendly_name: "Bedroom", travel_time_down: 30, calibration_source: "yaml" }),
    "cover.other": { entity_id: "cover.other", state: "open", attributes: { friendly_name: "Not ours" } },
    "light.x": { entity_id: "light.x", state: "on", attributes: { calibration_source: "default" } },
  });
  const covers = card._timedCovers();
  assert.deepEqual(covers.map((s) => s.entity_id), ["cover.a", "cover.b"]);
  assert.equal(card._estimateMinutes(covers), Math.max(1, Math.round((3 * 30 + 3 + 3 * 20 + 3) / 60)));

  global.confirm = undefined;
  await card._toggleCovers();
  assert.equal(el("covers-panel").style.display, "block");
  assert.equal(card._hass._eventType, "myhome_cover_calibration");
  const html = el("covers-list").innerHTML;
  assert.match(html, /Bedroom[\s\S]*Kitchen/);
  assert.match(html, /src-yaml/);
  assert.match(html, /src-measured/);
  assert.match(html, /data-entity="cover\.a"/);
  assert.match(el("covers-estimate").textContent, /2 cover\(s\), about \d+ min/);
  await card._toggleCovers();
  assert.equal(el("covers-panel").style.display, "none");
});

test("covers panel: empty state when no timed cover exists", async () => {
  const card = withCovers(create(), {});
  await card._toggleCovers();
  assert.match(el("covers-list").innerHTML, /No timed MyHOME covers/);
  assert.equal(el("covers-estimate").textContent, "");
});

test("Calibrate buttons call myhome.calibrate_cover after confirmation and reflect live events", async () => {
  const card = withCovers(create(), { "cover.a": coverState("cover.a", { friendly_name: "Bedroom" }), "cover.b": coverState("cover.b", { friendly_name: "Kitchen" }) });
  await card._toggleCovers();

  // declined confirmation -> nothing sent
  global.confirm = () => false;
  await card._calibrateOne("cover.a");
  assert.deepEqual(card.services, []);

  global.confirm = () => true;
  await card._calibrateOne("cover.a");
  assert.deepEqual(card.services, ["myhome.calibrate_cover"]);
  assert.match(el("covers-list").innerHTML, /scheduled/);

  card._onCalibrationEvent({ data: { entity_id: "cover.a", phase: "queued" } });
  assert.match(el("covers-list").innerHTML, /waiting for another cover/);
  card._onCalibrationEvent({ data: { entity_id: "cover.a", phase: "start" } });
  assert.match(el("covers-list").innerHTML, /starting/);

  // events from the backend drive the status column
  card._onCalibrationEvent({ data: { entity_id: "cover.a", phase: "run", direction: "close" } });
  assert.match(el("covers-list").innerHTML, /running close/);
  card._onCalibrationEvent({ data: { entity_id: "cover.a", phase: "done", down: 18.42, up: 20.1 } });
  assert.match(el("covers-list").innerHTML, /down 18\.4 s · up 20\.1 s/);
  card._onCalibrationEvent({ data: { entity_id: "cover.b", phase: "failed", error: "no stop status" } });
  assert.match(el("covers-list").innerHTML, /✗ no stop status/);
  card._onCalibrationEvent({ data: {} }); // ignored

  // calibrate all targets every timed cover in one service call
  await card._calibrateAll();
  assert.equal(card.services.length, 2);
});

test("service failure is shown in the status column", async () => {
  const card = withCovers(create(), { "cover.a": coverState("cover.a", { friendly_name: "Bedroom" }) });
  card._hass.callService = async () => { throw new Error("gateway offline"); };
  global.confirm = () => true;
  await card._toggleCovers();
  await card._calibrateOne("cover.a");
  assert.match(el("covers-list").innerHTML, /gateway offline/);
  await card._calibrateAll();
  assert.match(el("covers-list").innerHTML, /gateway offline/);
});

test("markup: toolbar has the tools group and the covers panel is hidden by default", () => {
  const card = create();
  card._render();
  const html = card.shadowRoot.innerHTML;
  const groups = [...html.matchAll(/class="group (capture|output|tools|stream)"/g)].map((m) => m[1]);
  assert.deepEqual(groups, ["capture", "output", "tools", "stream"]);
  assert.match(html, /id="covers-panel"/);
  assert.match(html, /id="btn-calibrate-all"/);
  assert.match(html, /id="btn-stop-calibration"/);
  assert.match(html, /id="btn-export-calibration-trace"/);
});

test("stop button toggles with calibration lifecycle and calls stop_cover_calibration", async () => {
  const card = withCovers(create(), { "cover.a": coverState("cover.a", { friendly_name: "Bedroom" }) });
  await card._toggleCovers();
  assert.equal(el("btn-stop-calibration").style.display, "none");

  // Calibrating starts
  card._onCalibrationEvent({ data: { entity_id: "cover.a", phase: "run", direction: "open" } });
  assert.equal(card._isCalibrating, true);
  assert.equal(el("btn-stop-calibration").style.display, "inline-block");
  assert.equal(el("btn-calibrate-all").disabled, true);

  // Click stop
  await card._stopCalibration();
  assert.ok(card.services.includes("myhome.stop_cover_calibration"));
  assert.equal(card._isCalibrating, false);
  assert.equal(el("btn-stop-calibration").style.display, "none");
  assert.equal(el("btn-calibrate-all").disabled, false);
  assert.match(el("covers-list").innerHTML, /✗ cancelled/);
});

test("export calibration trace exports recorded frames and falls back to buffer if empty", async () => {
  const card = withCovers(create({ model: "MH200N" }), { "cover.a": coverState("cover.a", { friendly_name: "Bedroom" }) });
  await card._toggleCovers();

  // 1. Export with recorded calibration frames
  card._calibrationTrace = [
    frame("*2*1*21##", { timestamp: 200, direction: "tx" }),
    frame("*2*0*21##", { timestamp: 220, direction: "rx" }),
  ];
  card._calibrationEvents = [
    { timestamp: 200, phase: "start", entity_id: "cover.a" },
    { timestamp: 220, phase: "done", entity_id: "cover.a" },
  ];
  await card._handleExportCalibrationTrace();
  const calDl = downloads.at(-1);
  assert.match(calDl.fileName, /^myhome_calibration_MH200N_/);
  assert.equal(calDl.payload.capture.kind, "calibration");
  assert.equal(calDl.payload.frames.length, 2);
  assert.equal(calDl.payload.calibration_events.length, 2);

  // 2. Export with empty calibration trace falls back to buffer frames so trace is not empty
  card._calibrationTrace = [];
  card._frames = [frame("*2*1*21##", { timestamp: 300 })];
  await card._handleExportCalibrationTrace();
  const fbDl = downloads.at(-1);
  assert.equal(fbDl.payload.frames.length, 1);
  assert.equal(fbDl.payload.frames[0].raw, "*2*1*21##");
});

test("regression: the backend calibration trace is requested for the card's gateway only (two gateways)", async () => {
  // Two gateways calibrate; the backend keeps one buffer. The card must ask for its own
  // gateway's frames, never receive the other one's (xtimmy86x, #319 review).
  const traces = {
    "00:03:50:00:00:01": [{ timestamp: 1, iso_time: "2026-09-15T08:00:00+00:00", gateway_mac: "00:03:50:00:00:01", direction: "tx", raw: "*2*2*21##", entity_id: "cover.a" }],
    "00:03:50:00:00:02": [{ timestamp: 2, iso_time: "2026-09-15T08:00:01+00:00", gateway_mac: "00:03:50:00:00:02", direction: "tx", raw: "*2*2*31##", entity_id: "cover.b" }],
  };
  const requests = [];
  const card = withCovers(create(), { "cover.a": coverState("cover.a", { friendly_name: "Bedroom" }) });
  card._config = { title: "test", mac: "00:03:50:00:00:02" };
  card._hass.callWS = async (payload) => {
    requests.push(payload);
    if (payload.type !== "myhome/cover/calibration_trace") return {};
    return { mac: payload.mac, frames: traces[payload.mac] };
  };
  await card._toggleCovers();
  card._calibrationTrace = [];
  card._frames = [];
  await card._handleExportCalibrationTrace();

  const req = requests.find((r) => r.type === "myhome/cover/calibration_trace");
  assert.equal(req.mac, "00:03:50:00:00:02");
  const dl = downloads.at(-1);
  assert.deepEqual(dl.payload.frames.map((f) => f.entity_id), ["cover.b"]);
  assert.ok(!dl.payload.frames.some((f) => f.entity_id === "cover.a"));
  // the file stays free of full MACs (privacy, #335): the scope is the request's
  assert.doesNotMatch(JSON.stringify(dl.payload), /00:03:50:00:00:02/);

  // without a configured mac the request carries none: the backend answers for the primary gateway
  card._config = { title: "test" };
  requests.length = 0;
  card._hass.callWS = async (payload) => { requests.push(payload); return { mac: "00:03:50:00:00:01", frames: traces["00:03:50:00:00:01"] }; };
  await card._handleExportCalibrationTrace();
  assert.equal("mac" in requests.find((r) => r.type === "myhome/cover/calibration_trace"), false);
  assert.deepEqual(downloads.at(-1).payload.frames.map((f) => f.entity_id), ["cover.a"]);
});

test("covers panel automatically updates when hass state changes while panel is open", async () => {
  const card = withCovers(create(), {
    "cover.a": coverState("cover.a", { friendly_name: "Bedroom", travel_time_down: 25, calibration_source: "default" }),
  });
  await card._toggleCovers();
  assert.match(el("covers-list").innerHTML, /25\.0 s/);
  assert.match(el("covers-list").innerHTML, /src-default/);

  // Hass updates entity attributes dynamically (e.g. calibration completes in backend)
  const updatedHass = Object.assign({}, card._hass, {
    states: {
      "cover.a": coverState("cover.a", {
        friendly_name: "Bedroom",
        travel_time_down: 18.5,
        travel_time_up: 19.2,
        calibration_source: "measured",
      }),
    },
  });
  card.hass = updatedHass;
  assert.match(el("covers-list").innerHTML, /18\.5 s/);
  assert.match(el("covers-list").innerHTML, /19\.2 s/);
  assert.match(el("covers-list").innerHTML, /src-measured/);
});

test("callout box explaining 60s actuator safety cutoff and Issue #302 is present in covers panel", async () => {
  const card = create();
  card._render();
  const html = card.shadowRoot.innerHTML;
  assert.match(html, /Actuator Relay Cutoff vs Physical Travel/);
  assert.match(html, /60-second safety cutoff/);
  assert.match(html, /Issue #302/);
  assert.match(html, /https:\/\/github\.com\/OpenWebNet-HA\/MyHOME\/issues\/302/);
});

test("stopwatch drawer toggles open with direct inputs and live stopwatch controls", async () => {
  const card = withCovers(create(), {
    "cover.a": coverState("cover.a", { friendly_name: "Bedroom", travel_time_down: 25, travel_time_up: 25 }),
  });
  await card._toggleCovers();
  assert.equal(card._activeStopwatchCover, null);
  assert.doesNotMatch(el("covers-list").innerHTML, /stopwatch-row/);

  // Open drawer
  card._toggleStopwatch("cover.a");
  assert.equal(card._activeStopwatchCover, "cover.a");
  assert.match(el("covers-list").innerHTML, /stopwatch-row/);
  assert.match(el("covers-list").innerHTML, /input-down-cover\.a/);
  assert.match(el("covers-list").innerHTML, /input-up-cover\.a/);
  assert.match(el("covers-list").innerHTML, /Start Down/);
  assert.match(el("covers-list").innerHTML, /Start Up/);

  // Close drawer
  card._toggleStopwatch("cover.a");
  assert.equal(card._activeStopwatchCover, null);
  assert.doesNotMatch(el("covers-list").innerHTML, /stopwatch-row/);
});

test("live stopwatch: start down sends close_cover and stop & save calls set_cover_travel_time", async () => {
  const card = withCovers(create(), {
    "cover.a": coverState("cover.a", { friendly_name: "Bedroom", travel_time_down: 25, travel_time_up: 25 }),
  });
  await card._toggleCovers();
  card._toggleStopwatch("cover.a");

  const serviceCalls = [];
  card._hass.callService = async (domain, service, data) => {
    serviceCalls.push({ service: `${domain}.${service}`, data });
    return data;
  };

  // Start down
  await card._startStopwatch("cover.a", "down");
  assert.ok(card._stopwatch.timerId != null);
  assert.equal(card._stopwatch.direction, "down");
  assert.equal(card._stopwatch.anchored, false); // the click is not the motor start
  assert.equal(serviceCalls[0].service, "cover.close_cover");
  assert.equal(serviceCalls[0].data.entity_id, "cover.a");

  // The click-anchored clock ran 21 s (queue wait + travel); the backend measured
  // the run from the motor start to the stop's write: 18.42 s. That is what is saved.
  card._stopwatch.elapsed = 21.0;
  card._hass.states["cover.a"].attributes.last_run_seconds = 18.42;
  card._hass.states["cover.a"].attributes.last_run_direction = "close";
  card._hass.states["cover.a"].attributes.last_run_ended_at = "2026-09-15T08:00:20.000+00:00";

  // Stop & save
  await card._stopAndSaveStopwatch("cover.a");
  assert.equal(card._stopwatch.timerId, null);
  assert.equal(serviceCalls[1].service, "cover.stop_cover");
  assert.equal(serviceCalls[2].service, "myhome.set_cover_travel_time");
  assert.equal(serviceCalls[2].data.entity_id, "cover.a");
  assert.equal(serviceCalls[2].data.travel_time_down, 18.4);
  assert.equal(serviceCalls[2].data.travel_time_up, 25);

  // "Save" is visible: the measured time lands in the Down field (flashed) and the
  // clock keeps the final reading instead of resetting to 0.0
  assert.equal(el("input-down-cover.a").value, "18.4");
  assert.equal(card._manualDrafts["cover.a"].down, "18.4");
  assert.ok(el("input-down-cover.a").classList.contains("flash"));
  assert.equal(card._stopwatch.elapsed, 18.4);
  assert.equal(card._stopwatch.savedDirection, "down");
  assert.match(el("covers-list").innerHTML, /stopwatch-clock done">18\.4s/);
});

test("stopwatch: a run the bus already reported is not mistaken for this one; without a report the clock is the fallback", async () => {
  const card = withCovers(create(), {
    "cover.a": coverState("cover.a", { friendly_name: "Bedroom", last_run_seconds: 40.0, last_run_direction: "open", last_run_ended_at: "2026-09-15T07:00:00.000+00:00" }),
  });
  await card._toggleCovers();
  card._toggleStopwatch("cover.a");
  const serviceCalls = [];
  card._hass.callService = async (domain, service, data) => { serviceCalls.push({ service: `${domain}.${service}`, data }); return data; };

  await card._startStopwatch("cover.a", "up");
  assert.equal(card._stopwatch.runBefore, "2026-09-15T07:00:00.000+00:00");
  card._stopwatch.elapsed = 12.3;
  // the backend never reports a new run (no stop status, no write): the previous
  // 40 s run must not be saved, the clock is
  await card._stopAndSaveStopwatch("cover.a", { timeoutMs: 0 });
  const save = serviceCalls.find((c) => c.service === "myhome.set_cover_travel_time");
  assert.equal(save.data.travel_time_up, 12.3);
  assert.equal(save.data.travel_time_down, 25);
});

test("stopwatch: the running clock re-anchors on the backend's motion_started_at", async () => {
  const card = withCovers(create(), { "cover.a": coverState("cover.a", { friendly_name: "Bedroom" }) });
  await card._toggleCovers();
  card._toggleStopwatch("cover.a");
  await card._startStopwatch("cover.a", "down");
  const clickedAt = card._stopwatch.startTime;
  assert.equal(card._motionAnchor("cover.a"), null);

  // the entity reports the motor start 2.5 s after the click (queue wait + MOTOR_START_DELAY)
  const anchorIso = new Date(clickedAt + 2500).toISOString();
  card._hass.states["cover.a"].attributes.motion_started_at = anchorIso;
  assert.equal(card._motionAnchor("cover.a"), clickedAt + 2500);
  await new Promise((resolve) => setTimeout(resolve, 150)); // one timer tick
  assert.equal(card._stopwatch.anchored, true);
  assert.equal(card._stopwatch.startTime, clickedAt + 2500);
  card._toggleStopwatch("cover.a"); // stops the interval

  card._hass.states["cover.a"].attributes.motion_started_at = "not a date";
  assert.equal(card._motionAnchor("cover.a"), null);
});

test("stopwatch buttons: Start turns into Stop & Save in place; ✕ cancels without saving", async () => {
  const card = withCovers(create({ model: "MH200" }), {
    "cover.a": coverState("cover.a", { friendly_name: "Bedroom", travel_time_down: 25, travel_time_up: 25 }),
  });
  await card._toggleCovers();
  card._toggleStopwatch("cover.a");
  const serviceCalls = [];
  card._hass.callService = async (domain, service, data) => {
    serviceCalls.push({ service: `${domain}.${service}`, data });
    return data;
  };

  let html = el("covers-list").innerHTML;
  assert.match(html, /data-action="sw-start-down"[^>]*>⬇️ Start Down/);
  assert.match(html, /data-action="sw-start-up"[^>]*>⬆️ Start Up/);
  assert.doesNotMatch(html, /Stop &amp; Save/);

  await card._startStopwatch("cover.a", "up");
  html = el("covers-list").innerHTML;
  // the Up button is now the stop button, Down is disabled, ✕ became Cancel
  assert.match(html, /data-action="sw-stop-save"[^>]*>⏹️ Stop &amp; Save Up/);
  assert.match(html, /data-action="sw-start-down"[^>]*disabled/);
  assert.match(html, /✕ Cancel/);

  // ✕ while running: motor stopped, nothing saved, drawer still open
  card._toggleStopwatch("cover.a");
  await new Promise((r) => setTimeout(r, 0));
  assert.equal(serviceCalls.at(-1).service, "cover.stop_cover");
  assert.ok(!serviceCalls.some((c) => c.service === "myhome.set_cover_travel_time"));
  assert.equal(card._activeStopwatchCover, "cover.a");
  assert.equal(card._stopwatch.timerId, null);
  assert.match(el("covers-list").innerHTML, /⬆️ Start Up/);
});

test("manual inputs save: validates range and calls set_cover_travel_time", async () => {
  const card = withCovers(create(), {
    "cover.a": coverState("cover.a", { friendly_name: "Bedroom", travel_time_down: 25, travel_time_up: 25 }),
  });
  await card._toggleCovers();
  card._toggleStopwatch("cover.a");

  const serviceCalls = [];
  card._hass.callService = async (domain, service, data) => {
    serviceCalls.push({ service: `${domain}.${service}`, data });
    return data;
  };

  // Set input values in DOM
  el("input-down-cover.a").value = "17.5";
  el("input-up-cover.a").value = "19.0";

  await card._saveManualInputs("cover.a");
  assert.equal(serviceCalls.length, 1);
  assert.equal(serviceCalls[0].service, "myhome.set_cover_travel_time");
  assert.equal(serviceCalls[0].data.entity_id, "cover.a");
  assert.equal(serviceCalls[0].data.travel_time_down, 17.5);
  assert.equal(serviceCalls[0].data.travel_time_up, 19.0);
  assert.equal(card._activeStopwatchCover, null); // closes on success
});

test("reset travel times calls reset_cover_travel_time after confirmation", async () => {
  const card = withCovers(create(), {
    "cover.a": coverState("cover.a", { friendly_name: "Bedroom", travel_time_down: 18.5, calibration_source: "manual" }),
  });
  await card._toggleCovers();

  const serviceCalls = [];
  card._hass.callService = async (domain, service, data) => {
    serviceCalls.push({ service: `${domain}.${service}`, data });
    return data;
  };

  global.confirm = () => false;
  await card._resetTravelTime("cover.a");
  assert.equal(serviceCalls.length, 0);

  global.confirm = () => true;
  await card._resetTravelTime("cover.a");
  assert.equal(serviceCalls.length, 1);
  assert.equal(serviceCalls[0].service, "myhome.reset_cover_travel_time");
  assert.equal(serviceCalls[0].data.entity_id, "cover.a");
});

test("mode detection: the gateway model does not gate auto-calibration (the actuator does, in the backend)", async () => {
  // An MH200 / MyHOMEServer1 with ordinary travel times: auto mode is offered. Whether a run
  // can be measured is decided per actuator by the backend guard, whoever calls the service
  // (Interstellar0verdrive, #319 review).
  for (const model of ["MH200", "MH200N", "F454", "MyHOME Server 1"]) {
    const card = withCovers(create({ model }), {
      "cover.a": coverState("cover.a", { friendly_name: "Bedroom", travel_time_down: 25, travel_time_up: 25 }),
    });
    await card._toggleCovers();
    assert.equal(card._isAutoCalibrationSupported(), true, model);
    assert.equal(card._detectCoversMode(), "auto", model);
    assert.doesNotMatch(card._coversModeReason(), /MH200|F454|gateway/);
    assert.equal(el("btn-calibrate-all").style.display, "inline-block");
    await card._toggleCovers();
  }
});

test("mode detection: a cover carrying the 60 s actuator run-time signature pre-selects manual mode", async () => {
  // Whatever the gateway, a stored ~61.5 s time is the actuator's limit, not a travel
  const card = withCovers(create({ model: "MyHOME Server 1" }), {
    "cover.a": coverState("cover.a", { friendly_name: "Living Room", travel_time_down: 61.5, travel_time_up: 61.0, calibration_source: "measured" }),
  });
  await card._toggleCovers();
  assert.equal(card._detectCoversMode(), "manual");
  assert.match(card._coversModeReason(), /60 s actuator run-time limit/);
  assert.match(el("covers-mode-hint").textContent, /60 s actuator run-time limit/);
  assert.equal(el("btn-calibrate-all").style.display, "none");

  // Single button per row in manual mode: [⏱️ Time], NO [Calibrate] or [↺] in the action cell
  const html = el("covers-list").innerHTML;
  assert.match(html, /⏱️ Time/);
  assert.doesNotMatch(html, /title="Bus Automatic Calibration">Calibrate<\/button>/);
  assert.doesNotMatch(html, /title="Reset to YAML or default/);
});

test("a backend refusal at the 60 s limit is shown in the status column", async () => {
  const card = withCovers(create(), { "cover.a": coverState("cover.a", { friendly_name: "Bedroom" }) });
  await card._toggleCovers();
  card._onCalibrationEvent({ data: { entity_id: "cover.a", phase: "failed", error: "Bedroom: the open run ended after 61.5 s, at the actuator's 60 s run-time limit, not at the end stop; the actuator cannot measure this shutter - use the stopwatch (Stop & Save) or set travel_time manually" } });
  assert.match(el("covers-list").innerHTML, /60 s run-time limit/);
});

test("dynamic mode detection: modern gateway with normal travel times defaults to auto mode", async () => {
  const card = withCovers(create({ model: "MyHOME Server 1" }), {
    "cover.a": coverState("cover.a", { friendly_name: "Living Room", travel_time_down: 18.0, travel_time_up: 19.5, calibration_source: "measured" }),
  });
  await card._toggleCovers();
  assert.equal(card._detectCoversMode(), "auto");
  assert.match(card._coversModeReason(), /Bus auto-calibration available/);

  // In auto mode: single [Calibrate] button per row
  const html = el("covers-list").innerHTML;
  assert.match(html, /Calibrate<\/button>/);
  assert.doesNotMatch(html, /⏱️ Time/);
  assert.equal(el("btn-calibrate-all").style.display, "inline-block");
});

test("with the 60 s signature: auto mode button is disabled and cannot be selected", async () => {
  const card = withCovers(create({ model: "MH200" }), {
    "cover.a": coverState("cover.a", { friendly_name: "Bedroom", travel_time_down: 61.5, calibration_source: "measured" }),
  });
  await card._toggleCovers();
  assert.equal(card._detectCoversMode(), "manual");
  assert.equal(card._isAutoCalibrationSupported(), false);
  assert.equal(el("mode-btn-auto").disabled, true);
  assert.match(el("mode-btn-auto").textContent, /Unsupported/);

  // Attempt to switch to auto mode is refused
  card._setCoversMode("auto");
  assert.equal(card._detectCoversMode(), "manual");
  assert.match(el("covers-list").innerHTML, /⏱️ Time/);
  assert.doesNotMatch(el("covers-list").innerHTML, /Calibrate<\/button>/);
  assert.equal(el("btn-calibrate-all").style.display, "none");
});

test("on supported gateway: user can toggle between manual and auto modes via segmented buttons", async () => {
  const card = withCovers(create({ model: "MyHOME Server 1" }), {
    "cover.a": coverState("cover.a", { friendly_name: "Bedroom", travel_time_down: 18.0, travel_time_up: 19.0 }),
  });
  await card._toggleCovers();
  assert.equal(card._isAutoCalibrationSupported(), true);
  assert.equal(el("mode-btn-auto").disabled, false);
  assert.equal(card._detectCoversMode(), "auto");
  assert.match(el("covers-list").innerHTML, /Calibrate<\/button>/);

  // Switch to manual mode
  card._setCoversMode("manual");
  assert.equal(card._detectCoversMode(), "manual");
  assert.match(el("covers-list").innerHTML, /⏱️ Time/);
  assert.equal(el("btn-calibrate-all").style.display, "none");

  // Switch back to auto mode
  card._setCoversMode("auto");
  assert.equal(card._detectCoversMode(), "auto");
  assert.match(el("covers-list").innerHTML, /Calibrate<\/button>/);
  assert.equal(el("btn-calibrate-all").style.display, "inline-block");
});

test("stopwatch drawer contains reset button alongside save", async () => {
  const card = withCovers(create({ model: "MH200" }), {
    "cover.a": coverState("cover.a", { friendly_name: "Bedroom", travel_time_down: 18.5, calibration_source: "manual" }),
  });
  await card._toggleCovers();
  card._toggleStopwatch("cover.a");

  // Drawer is open and contains ↺ Reset button
  const listHtml = el("covers-list").innerHTML;
  assert.match(listHtml, /stopwatch-row/);
  assert.match(listHtml, /💾 Save/);
  assert.match(listHtml, /↺ Reset/);
});

test("manual edits persistence: editing down and up values is preserved across background hass state updates", async () => {
  const card = withCovers(create({ model: "MH200" }), {
    "cover.persist": coverState("cover.persist", { friendly_name: "Living Room", travel_time_down: 25.0, travel_time_up: 25.0 }),
  });
  await card._toggleCovers();
  card._toggleStopwatch("cover.persist");

  // User edits down value
  el("input-down-cover.persist").value = "14.5";
  card._manualDrafts["cover.persist"] = { down: "14.5" };

  // User edits up value
  el("input-up-cover.persist").value = "16.0";
  card._manualDrafts["cover.persist"].up = "16.0";

  // A background update arrives from HA (e.g. sensor update or bus state)
  card._renderCoversList();

  // The rendered HTML must still display 14.5 and 16.0, NOT 25.0!
  assert.equal(card._manualDrafts["cover.persist"].down, "14.5");
  assert.equal(card._manualDrafts["cover.persist"].up, "16.0");

  const serviceCalls = [];
  card._hass.callService = async (domain, service, data) => {
    serviceCalls.push({ service: `${domain}.${service}`, data });
    return data;
  };

  await card._saveManualInputs("cover.persist");
  assert.equal(serviceCalls.length, 1);
  assert.equal(serviceCalls[0].data.travel_time_down, 14.5);
  assert.equal(serviceCalls[0].data.travel_time_up, 16.0);
  assert.equal(card._manualDrafts["cover.persist"], undefined); // Draft cleared on save
});

test("stepper controls: -1s, -½, +½, +1s correctly adjust manual inputs and update drafts", async () => {
  const card = withCovers(create({ model: "MH200" }), {
    "cover.step": coverState("cover.step", { friendly_name: "Kitchen", travel_time_down: 20.0, travel_time_up: 22.0 }),
  });
  await card._toggleCovers();
  card._toggleStopwatch("cover.step");

  // Initialize mock input values from attributes
  el("input-down-cover.step").value = "20.0";
  el("input-up-cover.step").value = "22.0";

  // Step down value +1s
  card._stepInputValue("input-down-cover.step", 1.0);
  assert.equal(el("input-down-cover.step").value, "21.0");
  assert.equal(card._manualDrafts["cover.step"].down, "21.0");

  // Step down value -0.5s
  card._stepInputValue("input-down-cover.step", -0.5);
  assert.equal(el("input-down-cover.step").value, "20.5");
  assert.equal(card._manualDrafts["cover.step"].down, "20.5");

  // Step up value -1s
  card._stepInputValue("input-up-cover.step", -1.0);
  assert.equal(el("input-up-cover.step").value, "21.0");
  assert.equal(card._manualDrafts["cover.step"].up, "21.0");

  // Step up value +0.5s
  card._stepInputValue("input-up-cover.step", 0.5);
  assert.equal(el("input-up-cover.step").value, "21.5");
  assert.equal(card._manualDrafts["cover.step"].up, "21.5");
});

test("batch apply: applies travel times to selected covers in one click", async () => {
  const card = withCovers(create({ model: "MH200" }), {
    "cover.batch1": coverState("cover.batch1", { friendly_name: "Window 1", travel_time_down: 25.0, travel_time_up: 25.0 }),
    "cover.batch2": coverState("cover.batch2", { friendly_name: "Window 2", travel_time_down: 25.0, travel_time_up: 25.0 }),
    "cover.batch3": coverState("cover.batch3", { friendly_name: "Window 3", travel_time_down: 25.0, travel_time_up: 25.0 }),
  });
  await card._toggleCovers();
  card._toggleStopwatch("cover.batch1");

  el("input-down-cover.batch1").value = "19.5";
  el("input-up-cover.batch1").value = "20.5";
  card._manualDrafts["cover.batch1"] = { down: "19.5", up: "20.5" };

  card._toggleApplyOthers("cover.batch1");
  assert.equal(card._applyOthersCover, "cover.batch1");

  // Check Window 2 and Window 3
  const chkB = el("batch-chk-cover.batch1-cover.batch2");
  chkB.checked = true;
  chkB.value = "cover.batch2";
  // The class carries a CSS-safe key: an entity id's dot would split the selector in a browser
  chkB.className = `batch-cover-chk-${card._batchKey("cover.batch1")}`;
  assert.equal(chkB.className, "batch-cover-chk-cover_batch1");

  const chkC = el("batch-chk-cover.batch1-cover.batch3");
  chkC.checked = true;
  chkC.value = "cover.batch3";
  chkC.className = `batch-cover-chk-${card._batchKey("cover.batch1")}`;

  const serviceCalls = [];
  card._hass.callService = async (domain, service, data) => {
    serviceCalls.push({ service: `${domain}.${service}`, data });
    return data;
  };

  await card._applyToSelectedCovers("cover.batch1");

  // The edited source is saved as its own (manual) time; the others are "copied" from it
  assert.equal(serviceCalls.length, 2);
  assert.equal(serviceCalls[0].service, "myhome.set_cover_travel_time");
  assert.equal(serviceCalls[0].data.entity_id, "cover.batch1");
  assert.equal(serviceCalls[0].data.copied_from, undefined);
  assert.equal(serviceCalls[1].service, "myhome.set_cover_travel_time");
  assert.deepEqual(serviceCalls[1].data.entity_id, ["cover.batch2", "cover.batch3"]);
  assert.equal(serviceCalls[1].data.travel_time_down, 19.5);
  assert.equal(serviceCalls[1].data.travel_time_up, 20.5);
  assert.equal(serviceCalls[1].data.copied_from, "cover.batch1");
  // The panel stays open and confirms the result where the click happened
  assert.equal(card._applyOthersCover, "cover.batch1");
  const status = el("batch-status-cover.batch1");
  assert.match(status.textContent, /Saved to 3 covers/);
  assert.match(status.className, /ok/);

  // Pushing a cover's *stored* times (fields untouched) does not re-save the source
  card._hass.states["cover.batch1"] = coverState("cover.batch1", { friendly_name: "Window 1", travel_time_down: 19.5, travel_time_up: 20.5, calibration_source: "measured" });
  serviceCalls.length = 0;
  chkB.checked = true;
  chkC.checked = false;
  await card._applyToSelectedCovers("cover.batch1");
  assert.equal(serviceCalls.length, 1);
  assert.deepEqual(serviceCalls[0].data.entity_id, ["cover.batch2"]);
  assert.equal(serviceCalls[0].data.copied_from, "cover.batch1");

  // Nothing selected: the warning is shown inline too (a browser re-render clears the
  // boxes; the harness keeps element objects, so clear them here)
  serviceCalls.length = 0;
  chkB.checked = false;
  chkC.checked = false;
  await card._applyToSelectedCovers("cover.batch1");
  assert.equal(serviceCalls.length, 0);
  assert.match(el("batch-status-cover.batch1").textContent, /Select at least one cover/);
  // The drawer stays open after a batch apply so the confirmation is visible
  assert.equal(card._activeStopwatchCover, "cover.batch1");
});

test("markup: stopwatch drawer has stepper buttons and pushes its times to other covers", async () => {
  const card = withCovers(create({ model: "MH200" }), {
    "cover.mark1": coverState("cover.mark1", { friendly_name: "Living 1", travel_time_down: 25.0 }),
    "cover.mark2": coverState("cover.mark2", { friendly_name: "Living 2", travel_time_down: 25.0 }),
  });
  await card._toggleCovers();
  card._toggleStopwatch("cover.mark1");

  const html = el("covers-list").innerHTML;
  assert.match(html, /stepper-control/);
  assert.match(html, /data-delta="-1"/);
  assert.match(html, /data-delta="-0.5"/);
  assert.match(html, /data-delta="0.5"/);
  assert.match(html, /data-delta="1"/);
  // The open cover is the source: no "copy from" selector, only the push to other covers
  assert.doesNotMatch(html, /copy-cover-select|Copy from/);
  assert.match(html, /📋 Apply these times to other covers…/);
  card._toggleApplyOthers("cover.mark1");
  const panel = el("covers-list").innerHTML;
  assert.match(panel, /Apply this cover's times/);
  assert.match(panel, /Living 2/);
});


