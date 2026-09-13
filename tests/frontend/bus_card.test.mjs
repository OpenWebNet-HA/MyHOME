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
  const actionsAt = html.indexOf('<div class="actions">');
  assert.ok(badgeAt > 0 && helpAt > badgeAt && actionsAt > helpAt, "help button must sit between the badge and the action buttons");

  card._toggleHelp();
  assert.equal(el("help-panel").style.display, "block");
  assert.equal(el("btn-help").classList.contains("open"), true);
  card._toggleHelp();
  assert.equal(el("help-panel").style.display, "none");
  assert.equal(el("btn-help").classList.contains("open"), false);
});
