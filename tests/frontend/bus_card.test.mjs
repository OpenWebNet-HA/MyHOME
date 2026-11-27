/**
 * Behavioural tests for the bus-monitor Lovelace card (run with `node --test tests/frontend`).
 *
 * Covers the capture workflow (Start/Stop Trace, Sweep Bus, Clear), the labels that
 * follow it, the self-describing export, and the armed transmit bar.
 */
import { readFileSync } from "node:fs";
import { test } from "node:test";
import assert from "node:assert/strict";
import { loadCard, frame, CARD_PATH } from "./harness.mjs";

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

test("regression: an export after Clear does not carry the previous trace's start time", async () => {
  const card = create();
  await card._handleStartTrace();
  assert.ok(card._traceStartedAt);
  await card._clearBuffer();
  assert.equal(card._traceStartedAt, null);
  await card._handleExportTrace();
  assert.equal(downloads.at(-1).payload.capture.started_at, null);
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

test("regression #429: history backfill asks for the whole display buffer, de-duplicates and caps it", async () => {
  // After an HA restart the startup status sweep is > 50 frames; a 50-frame backfill lost WHO=4.
  const card = create();
  card._maxDisplayFrames = 300;
  const history = [];
  for (let n = 0; n < 300; n++) {
    history.push(n % 3 === 0
      ? frame(`*#4*${n}*0*0215*1##`, { timestamp: n, who: "4", where: String(n) })
      : frame(`*1*1*${n}##`, { timestamp: n, who: "1", where: String(n) }));
  }
  // Streamed between subscribe and the history reply: one frame the ring also holds, one newer.
  card._frames = [history[299], frame("*1*0*12##", { timestamp: 400, who: "1", where: "12" })];
  const requests = [];
  card._hass.callWS = async (payload) => {
    requests.push(payload);
    return payload.type === "myhome/bus_monitor/history" ? { frames: history, stats: { captured: 300 } } : {};
  };

  await card._loadHistory();

  assert.equal(requests[0].type, "myhome/bus_monitor/history");
  assert.equal(requests[0].limit, 300);
  assert.equal(card._frames.length, 300); // 299 new + 2 streamed, capped to the newest 300
  assert.equal(card._frames[0].raw, "*1*1*1##");
  assert.equal(card._frames.at(-1).raw, "*1*0*12##");
  assert.equal(card._frames.filter((f) => f.raw === history[299].raw).length, 1);

  // Backfilled frames follow the same client-side filter as streamed ones.
  card._filterWho = "4";
  await card._handleExportTrace();
  const { payload } = downloads.at(-1);
  assert.equal(payload.frames.length, 99); // n = 3, 6, ... 297 (n = 0 fell off the cap)
  assert.ok(payload.frames.every((f) => f.who === "4"));
  assert.equal(payload.capture.window.buffer_frames, 300);
});

test("regression #429: a card without max_frames buffers the whole backend ring", () => {
  const card = create();
  card.setConfig({ type: "custom:myhome-bus-card" });
  assert.equal(card._maxDisplayFrames, 500); // bus_monitor.DEFAULT_RING_BUFFER_SIZE
  assert.equal(card.constructor.getStubConfig().max_frames, 500);
  card.setConfig({ max_frames: 1000 });
  assert.equal(card._maxDisplayFrames, 1000);
});

test("regression #429: an HA restart on the same connection backfills the new process's ring", async () => {
  const card = create();
  const listeners = {};
  const connection = {
    subscribeMessage: async () => () => {},
    addEventListener: (type, cb) => { (listeners[type] ||= []).push(cb); },
    removeEventListener: (type, cb) => { listeners[type] = (listeners[type] || []).filter((l) => l !== cb); },
  };
  card._hass.connection = connection;
  let ring = [frame("*1*1*11##", { timestamp: 10, who: "1", where: "11" })];
  card._hass.callWS = async (payload) =>
    payload.type === "myhome/bus_monitor/history" ? { frames: ring } : {};

  await card._subscribeStream();
  await new Promise((r) => setTimeout(r, 0));
  assert.deepEqual(card._frames.map((f) => f.raw), ["*1*1*11##"]);
  assert.equal(listeners.ready.length, 1);

  // HA restarts: the library reconnects the same Connection and re-subscribes on its own;
  // the stream is back only after the startup sweep, which the new ring still holds.
  ring = [
    frame("*#4*1*0*0215*1##", { timestamp: 100, who: "4", where: "1" }),
    frame("*#4*2*0*0200*1##", { timestamp: 101, who: "4", where: "2" }),
  ];
  card._onNewFrame(frame("*1*0*12##", { timestamp: 102, who: "1", where: "12" }));
  listeners.ready.forEach((cb) => cb());
  await new Promise((r) => setTimeout(r, 0));
  assert.deepEqual(card._frames.map((f) => f.raw), ["*1*1*11##", "*#4*1*0*0215*1##", "*#4*2*0*0200*1##", "*1*0*12##"]);

  // A stopped (frozen) trace is not touched by a reconnect.
  card._togglePause();
  ring = [frame("*#4*3*0*0210*1##", { timestamp: 200, who: "4", where: "3" })];
  listeners.ready.forEach((cb) => cb());
  await new Promise((r) => setTimeout(r, 0));
  assert.equal(card._frames.length, 4);

  // Removing the card drops the listener; re-watching the same connection does not stack.
  card._watchReconnect(connection);
  assert.equal(listeners.ready.length, 1);
  card.disconnectedCallback();
  assert.equal(listeners.ready.length, 0);
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

test("gateway indicator renders model_key/model, firmware, active crash guards tooltip, and refreshes on arm", async () => {
  const card = create({ model: "Generic" });
  el("send-frame").value = "";
  card._render();
  const html = card.shadowRoot.innerHTML;
  assert.match(html, /id="stat-gateway"/);
  assert.match(html, /id="send-safety-notice"/);
  assert.match(html, /Firmware Oracle/);

  // Initial render reflects Generic without "(Unknown)" firmware noise
  card._gatewayInfo = {
    model: "Generic",
    model_key: "",
    firmware: "Unknown",
    crash_protections: {
      model_key: "",
      model_known: false,
      blanket_rule_count: 7,
      gateway_rules: [],
      delegated_peers: [],
    },
  };
  card._updateStats();
  assert.equal(el("stat-gateway").textContent, "Generic");
  assert.match(el("stat-gateway-wrap").title, /7 blanket crash guards active/);
  assert.match(el("stat-gateway-wrap").title, /model unknown/i);

  // Arming asynchronously refreshes myhome/bus_monitor/info and updates badge + tooltip
  const wsCalls = [];
  card._hass.callWS = async (payload) => {
    wsCalls.push(payload);
    if (payload.type === "myhome/bus_monitor/info") {
      return {
        gateway: {
          model: "HOMETOUCH",
          model_key: "3488",
          firmware: "2.1.4",
          crash_protections: {
            model_key: "3488",
            model_known: true,
            blanket_rule_count: 7,
            gateway_rules: [
              { rule_id: "who22_bare_what_33_44_45", summary: "Bare WHO 22 WHAT 33/44/45", oracle_ref: "own-firmware-oracle/sound-who22" },
            ],
            delegated_peers: ["MH202"],
          },
        },
      };
    }
    return {};
  };

  card._toggleArmed(true);
  assert.equal(el("btn-send").disabled, false, "arming with empty input leaves Send enabled immediately");
  await new Promise((r) => setTimeout(r, 0));

  assert.ok(wsCalls.some((c) => c.type === "myhome/bus_monitor/info"));
  assert.equal(el("stat-gateway").textContent, "3488 (2.1.4)");
  assert.match(el("stat-gateway-wrap").title, /8 crash guards active/);
  assert.match(el("stat-gateway-wrap").title, /3488-specific/);
  assert.match(el("stat-gateway-wrap").title, /delegated peers: MH202/);

  // Object-shaped delegated_peers entries are also formatted cleanly (no [object Object])
  card._gatewayInfo = {
    model: "F461",
    model_key: "F461",
    crash_protections: {
      model_key: "F461",
      model_known: true,
      blanket_rule_count: 7,
      gateway_rules: [],
      delegated_peers: [{ who: 4, model_key: "MH202", mac: "00:03:50:aa:bb:cc" }],
    },
  };
  card._updateStats();
  assert.match(el("stat-gateway-wrap").title, /delegated peers: MH202/);
  assert.doesNotMatch(el("stat-gateway-wrap").title, /\[object Object\]/);
  card._toggleArmed(false);
});

test("live preflight blocks crashing frames, disables Send, renders escaped notice, and prevents WS send", async () => {
  const card = create({ model: "H4684" });
  const input = el("send-frame");
  input.value = "";
  const wsCalls = [];
  card._hass.callWS = async (payload) => {
    wsCalls.push(payload);
    if (payload.type === "myhome/bus_monitor/check_frame") {
      return {
        valid: true,
        blocked: true,
        severity: "crash",
        rule_id: "who13_network_dims_10_11_12",
        reason: "WHO 13 dims 10/11/12 crash <H4684> & MH200.",
        scope: "gateway",
        target_model: "H4684",
        target_mac: "00:03:50:11:22:33",
        delegated_from: null,
        affected_models: ["H4684", "MH200"],
        oracle_ref: "own-firmware-oracle/who13-identification",
      };
    }
    if (payload.type === "myhome/bus_monitor/send") card.sent.push(payload.frame);
    return {};
  };

  card._toggleArmed(true);
  input.value = "*#13**10##";
  card._scheduleFrameCheck();

  // Wait past the 150ms debounce for preflight to execute
  await new Promise((r) => setTimeout(r, 190));

  assert.ok(wsCalls.some((c) => c.type === "myhome/bus_monitor/check_frame" && c.frame === "*#13**10##"));
  assert.equal(el("btn-send").disabled, true);
  assert.equal(input.classList.contains("frame-blocked"), true);
  const notice = el("send-safety-notice");
  assert.equal(notice.style.display, "block");
  assert.match(notice.className, /safety-notice-blocked/);
  assert.match(notice.innerHTML, /Blocked for H4684/);
  assert.match(notice.innerHTML, /&lt;H4684&gt; &amp; MH200/);
  assert.doesNotMatch(notice.innerHTML, /<H4684>/);
  assert.match(notice.innerHTML, /own-firmware-oracle\/who13-identification/);

  // Attempting to send (e.g. pressing Enter) refuses immediately without calling WS send
  await card._sendCustomFrame();
  assert.deepEqual(card.sent, []);
  assert.equal(wsCalls.filter((c) => c.type === "myhome/bus_monitor/send").length, 0);

  // Delegated gateway verdict surfaces both target_model and delegated_from
  card._applySafetyVerdict("*#4*001##", {
    valid: true,
    blocked: true,
    severity: "crash",
    rule_id: "mh202_who4_all_probes_001_099",
    reason: "WHO 4 all-probes 001..099 crashes MH202.",
    scope: "gateway",
    target_model: "MH202",
    target_mac: "00:03:50:aa:bb:cc",
    delegated_from: "F461",
    affected_models: ["MH202"],
    oracle_ref: "own-firmware-oracle/ownd-audit-frames",
  });
  assert.match(notice.innerHTML, /Blocked for MH202 \(routed via F461\)/);

  card._toggleArmed(false);
});

test("arming with pre-filled dangerous frame leaves Send enabled synchronously then blocks once preflight returns", async () => {
  const card = create({ model: "MH202" });
  const input = el("send-frame");
  input.value = "*#1*1111##";
  let resolveCheck;
  card._hass.callWS = (payload) => {
    if (payload.type === "myhome/bus_monitor/check_frame") {
      return new Promise((resolve) => { resolveCheck = resolve; });
    }
    return Promise.resolve({});
  };

  card._toggleArmed(true);
  // Immediately after arming (before WS replies), Send must stay enabled
  assert.equal(el("btn-send").disabled, false);

  resolveCheck({
    valid: true,
    blocked: true,
    severity: "crash",
    rule_id: "mh202_who1_status_where",
    reason: "WHO 1 status WHERE=1111 crashes MH202.",
    scope: "gateway",
    target_model: "MH202",
    target_mac: "",
    delegated_from: null,
    affected_models: ["MH202"],
    oracle_ref: "own-firmware-oracle/lights-where-extended",
  });
  await new Promise((r) => setTimeout(r, 0));

  assert.equal(el("btn-send").disabled, true);
  assert.equal(input.classList.contains("frame-blocked"), true);
  assert.equal(el("send-safety-notice").style.display, "block");
  card._toggleArmed(false);
});

test("stale out-of-order check_frame replies are suppressed by _checkSeq", async () => {
  const card = create({ model: "H4684" });
  const input = el("send-frame");
  input.value = "";
  const pending = {};
  card._hass.callWS = (payload) => {
    if (payload.type === "myhome/bus_monitor/check_frame") {
      return new Promise((resolve) => { pending[payload.frame] = resolve; });
    }
    return Promise.resolve({});
  };

  card._toggleArmed(true);
  input.value = "*#13**10##";
  const p1 = card._runFrameCheck("*#13**10##");

  // User edits to a safe frame before check #1 replies
  input.value = "*#13**0##";
  const p2 = card._runFrameCheck("*#13**0##");

  // Newer check #2 resolves FIRST as clean
  pending["*#13**0##"]({ valid: true, blocked: false, severity: null });
  await p2;
  assert.equal(el("btn-send").disabled, false);
  assert.equal(el("send-safety-notice").style.display, "none");

  // Older check #1 resolves LATE as blocked — MUST be ignored
  pending["*#13**10##"]({
    valid: true,
    blocked: true,
    severity: "crash",
    rule_id: "who13_network_dims_10_11_12",
    reason: "Stale crash reply",
    target_model: "H4684",
  });
  await p1;

  assert.equal(el("btn-send").disabled, false);
  assert.equal(input.classList.contains("frame-blocked"), false);
  assert.equal(el("send-safety-notice").style.display, "none");
  assert.equal(card._lastCheck.frame, "*#13**0##");

  // Disarming while a check is in flight also suppresses the late reply
  input.value = "*#13**11##";
  const p3 = card._runFrameCheck("*#13**11##");
  card._toggleArmed(false);
  pending["*#13**11##"]({
    valid: true,
    blocked: true,
    severity: "crash",
    reason: "Post-disarm reply",
    target_model: "H4684",
  });
  await p3;
  assert.equal(el("send-safety-notice").style.display, "none");
  assert.equal(el("btn-send").disabled, true);
});

test("amber caution warnings (no-reply and unknown model) keep Send enabled and allow transmission", async () => {
  const card = create({ model: "Generic" });
  const input = el("send-frame");
  input.value = "";
  card._hass.callWS = async (payload) => {
    if (payload.type === "myhome/bus_monitor/check_frame") {
      if (payload.frame === "*1*19*31##") {
        return {
          valid: true,
          blocked: false,
          severity: "warning",
          rule_id: "noreply_who1_what19",
          reason: "WHO 1 WHAT=19 receives no ACK/NACK on H4684/MH200 and stalls the session.",
          scope: "gateway",
          target_model: "H4684",
          target_mac: "",
          delegated_from: null,
          affected_models: ["H4684", "MH200"],
          oracle_ref: "own-firmware-oracle/lights-no-reply",
        };
      }
      return {
        valid: true,
        blocked: false,
        severity: "warning",
        rule_id: "unknown_gateway_model_specific",
        reason: "Gateway model is unidentified; this frame matches a model-specific crash rule.",
        scope: "gateway",
        target_model: "",
        target_mac: "",
        delegated_from: null,
        affected_models: ["H4684", "MH200"],
        oracle_ref: "own-firmware-oracle/who13-identification",
      };
    }
    if (payload.type === "myhome/bus_monitor/send") card.sent.push(payload.frame);
    return {};
  };

  card._toggleArmed(true);

  // Case 1: no-reply stall caution
  input.value = "*1*19*31##";
  await card._runFrameCheck("*1*19*31##");
  assert.equal(el("btn-send").disabled, false);
  assert.equal(input.classList.contains("frame-blocked"), false);
  assert.equal(el("send-safety-notice").style.display, "block");
  assert.match(el("send-safety-notice").className, /safety-notice-warning/);
  assert.match(el("send-safety-notice").innerHTML, /Caution/);
  assert.match(el("send-safety-notice").innerHTML, /stalls the session/);

  // Case 2: unknown gateway model-specific caution lists affected models when absent from reason
  input.value = "*#13**10##";
  await card._runFrameCheck("*#13**10##");
  assert.equal(el("btn-send").disabled, false);
  assert.match(el("send-safety-notice").className, /safety-notice-warning/);
  assert.match(el("send-safety-notice").innerHTML, /Affected models: H4684, MH200/);

  // Sending is still allowed and clears input + notice on success
  await card._sendCustomFrame();
  assert.deepEqual(card.sent, ["*#13**10##"]);
  assert.equal(input.value, "");
  assert.equal(el("send-safety-notice").style.display, "none");
  card._toggleArmed(false);
});

test("editing or clearing a blocked frame immediately re-enables Send and hides the notice", async () => {
  const card = create({ model: "MH202" });
  const input = el("send-frame");
  input.value = "";
  const checkedFrames = [];
  card._hass.callWS = async (payload) => {
    if (payload.type === "myhome/bus_monitor/check_frame") {
      checkedFrames.push(payload.frame);
      if (payload.frame === "*#4*001##") {
        return {
          valid: true,
          blocked: true,
          severity: "crash",
          rule_id: "mh202_who4_all_probes_001_099",
          reason: "Crashes MH202.",
          target_model: "MH202",
        };
      }
      return { valid: true, blocked: false, severity: null };
    }
    return {};
  };

  card._toggleArmed(true);
  input.value = "*#4*001##";
  await card._runFrameCheck("*#4*001##");
  assert.equal(el("btn-send").disabled, true);
  assert.equal(input.classList.contains("frame-blocked"), true);
  assert.equal(el("send-safety-notice").style.display, "block");

  // Editing to a safe frame immediately re-enables Send & hides stale block notice
  input.value = "*#4*1*0##";
  card._scheduleFrameCheck();
  assert.equal(el("btn-send").disabled, false);
  assert.equal(input.classList.contains("frame-blocked"), false);
  assert.equal(el("send-safety-notice").style.display, "none");

  // Wait for debounced preflight of safe frame
  await new Promise((r) => setTimeout(r, 190));
  assert.deepEqual(checkedFrames, ["*#4*001##", "*#4*1*0##"]);
  assert.equal(el("btn-send").disabled, false);

  // Re-block then clear input: clearing re-enables Send with no extra WS call
  input.value = "*#4*001##";
  await card._runFrameCheck("*#4*001##");
  assert.equal(el("btn-send").disabled, true);
  const countBeforeClear = checkedFrames.length;
  input.value = "   ";
  card._scheduleFrameCheck();
  assert.equal(el("btn-send").disabled, false);
  assert.equal(input.classList.contains("frame-blocked"), false);
  assert.equal(el("send-safety-notice").style.display, "none");
  await new Promise((r) => setTimeout(r, 190));
  assert.equal(checkedFrames.length, countBeforeClear);

  card._toggleArmed(false);
});

test("backend send error renders inline safety notice and warning banner without calling alert()", async () => {
  const card = create({ model: "F461" });
  const input = el("send-frame");
  input.value = "";
  let alertCalled = false;
  const origAlert = globalThis.alert;
  globalThis.alert = () => { alertCalled = true; };

  try {
    card._hass.callWS = async (payload) => {
      if (payload.type === "myhome/bus_monitor/send") {
        throw { code: "invalid_format", message: "Refusing crash frame <script>x</script> on F461" };
      }
      return {};
    };

    card._toggleArmed(true);
    input.value = "*#4*1*22##";
    await card._sendCustomFrame();

    assert.equal(alertCalled, false, "must never call blocking window.alert()");
    assert.equal(input.value, "*#4*1*22##", "failed frame stays in input so user can edit it");
    assert.equal(input.classList.contains("frame-blocked"), true);
    const notice = el("send-safety-notice");
    assert.equal(notice.style.display, "block");
    assert.match(notice.className, /safety-notice-blocked/);
    assert.match(notice.innerHTML, /Frame rejected/);
    assert.match(notice.innerHTML, /&lt;script&gt;x&lt;\/script&gt;/);
    assert.doesNotMatch(notice.innerHTML, /<script>/);

    const banner = el("feedback-banner");
    assert.equal(banner.style.display, "flex");
    assert.match(banner.className, /banner-warning/);
    assert.match(banner.innerHTML, /&lt;script&gt;x&lt;\/script&gt;/);

    // Source itself must not contain alert(...) calls
    const source = readFileSync(CARD_PATH, "utf8");
    assert.doesNotMatch(source, /\balert\s*\(/);
    assert.equal(el("btn-send").disabled, true, "invalid_format rejection also disables Send");
  } finally {
    globalThis.alert = origAlert;
    card._toggleArmed(false);
  }
});

test("unknown gateway model_known=false ignores fallback model_key in _updateStats", () => {
  const card = create({ model: "Custom (IP Gateway)" });
  card._render();
  card._gatewayInfo = {
    model: "Custom (IP Gateway)",
    model_key: "CUSTOMIPGATEWAYGATEWAY",
    firmware: "",
    crash_protections: {
      model_key: "CUSTOMIPGATEWAYGATEWAY",
      model_known: false,
      blanket_rule_count: 6,
      gateway_rules: [],
      delegated_peers: [],
    },
  };
  card._updateStats();
  assert.equal(el("stat-gateway").textContent, "Custom (IP Gateway)");
  assert.doesNotMatch(el("stat-gateway").textContent, /CUSTOMIPGATEWAYGATEWAY/);
});

test("fast Enter within <150ms of pasting blocked frame disables Send; transient error keeps retry & whitespace clear", async () => {
  const card = create({ model: "F461" });
  const input = el("send-frame");
  input.value = "";
  let mode = "invalid_format";
  let sendAttempts = 0;

  card._hass.callWS = async (payload) => {
    if (payload.type === "myhome/bus_monitor/check_frame") {
      return { valid: true, blocked: false, severity: null };
    }
    if (payload.type === "myhome/bus_monitor/send") {
      sendAttempts++;
      if (mode === "invalid_format") {
        throw { code: "invalid_format", message: "WHO 4 dimension 22 crashes daemon" };
      }
      if (mode === "transient") {
        throw { code: "unknown_error", message: "Failed to transmit frame: Gateway disconnected" };
      }
      card.sent.push(payload.frame);
    }
    return {};
  };

  card._toggleArmed(true);

  // 1. Paste blocked frame and immediately submit (<150ms, before debounce runs)
  input.value = "*#4*1*22##";
  card._scheduleFrameCheck();
  await card._sendCustomFrame();

  assert.equal(sendAttempts, 1);
  assert.equal(el("btn-send").disabled, true, "invalid_format disables Send");
  assert.equal(input.classList.contains("frame-blocked"), true);

  // Pressing Enter again while still blocked does not fire another WS send
  await card._sendCustomFrame();
  assert.equal(sendAttempts, 1);

  // 2. Edit to a safe frame, run preflight, then hit transient send failure
  mode = "transient";
  input.value = "*1*1*12##";
  card._scheduleFrameCheck();
  assert.equal(el("btn-send").disabled, false);
  await card._runFrameCheck("*1*1*12##");
  assert.equal(card._lastCheck.frame, "*1*1*12##");

  await card._sendCustomFrame();
  assert.equal(sendAttempts, 2);
  assert.equal(card._lastCheck, null, "transient error clears _lastCheck so retry/whitespace work");
  assert.equal(el("btn-send").disabled, false, "Send stays enabled for transient retry");
  assert.equal(input.classList.contains("frame-blocked"), true);

  // Whitespace-only edit clears _hasSendError and frame-blocked styling cleanly
  input.value = "*1*1*12## ";
  card._scheduleFrameCheck();
  assert.equal(card._hasSendError, false);
  assert.equal(input.classList.contains("frame-blocked"), false);
  assert.equal(el("send-safety-notice").style.display, "none");

  // Retry now succeeds cleanly
  mode = "ok";
  await card._sendCustomFrame();
  assert.equal(sendAttempts, 3);
  assert.deepEqual(card.sent, ["*1*1*12##"]);
  assert.equal(input.value, "");
  card._toggleArmed(false);
});

test("setConfig resets armed state, cancels pending preflight debounce, and clears stale check/error state", async () => {
  const card = create({ model: "MH202" });
  const input = el("send-frame");
  const checked = [];
  card._hass.callWS = async (payload) => {
    if (payload.type === "myhome/bus_monitor/check_frame") {
      checked.push(payload.frame);
    }
    return {};
  };

  card._toggleArmed(true);
  input.value = "*#4*001##";
  card._scheduleFrameCheck();
  card._lastCheck = { frame: "*#4*001##", result: { blocked: true, reason: "stale" } };
  card._hasSendError = true;
  assert.ok(card._checkTimeout !== null);

  card.setConfig({ title: "Reconfigured Bus Monitor", mac: "00:03:50:99:88:77" });

  assert.equal(card._sendArmed, false);
  assert.equal(card._checkTimeout, null);
  assert.equal(card._lastCheck, null);
  assert.equal(card._hasSendError, false);
  assert.match(card.shadowRoot.innerHTML, /id="send-frame"[^>]*\bdisabled\b/);
  assert.match(card.shadowRoot.innerHTML, /id="btn-send"[^>]*\bdisabled\b/);

  // Waiting past 150ms must not trigger the pre-setConfig debounce timer
  await new Promise((r) => setTimeout(r, 190));
  assert.deepEqual(checked, []);
});

test("setConfig and disarming while send is mid-flight suppress stale send completion/error state", async () => {
  const card = create({ model: "MH202" });
  const input = el("send-frame");
  let rejectSend;
  let resolveSend;

  // 1. setConfig() mid-flight before send rejects with invalid_format
  card._hass.callWS = (payload) => {
    if (payload.type === "myhome/bus_monitor/send") {
      return new Promise((_res, rej) => { rejectSend = rej; });
    }
    return Promise.resolve({});
  };

  card._toggleArmed(true);
  input.value = "*#4*001##";
  const inFlightReject = card._sendCustomFrame();
  assert.equal(card._isSending, true);

  card.setConfig({ title: "Switched Gateway", mac: "00:03:50:00:04:61" });
  assert.equal(card._isSending, false);
  assert.equal(card._sendArmed, false);

  rejectSend({ code: "invalid_format", message: "Stale MH202 rejection" });
  await inFlightReject;

  assert.equal(card._lastCheck, null, "stale rejection after setConfig must not pollute _lastCheck");
  assert.equal(card._hasSendError, false);
  assert.equal(el("send-safety-notice").style.display, "none");

  // 2. setConfig() mid-flight before send resolves
  card._hass.callWS = (payload) => {
    if (payload.type === "myhome/bus_monitor/send") {
      return new Promise((res) => { resolveSend = res; });
    }
    return Promise.resolve({});
  };
  card._toggleArmed(true);
  el("send-frame").value = "*1*1*12##";
  const inFlightResolve = card._sendCustomFrame();
  card.setConfig({ title: "Switched Again", mac: "00:03:50:00:04:62" });
  card._toggleArmed(true);
  el("send-frame").value = "*1*0*12##";
  resolveSend({ success: true });
  await inFlightResolve;
  assert.equal(el("send-frame").value, "*1*0*12##", "stale send resolution must not wipe new input");

  // 3. Disarming (_toggleArmed(false)) mid-flight before send rejects keeps inline safety notice hidden
  card._hass.callWS = (payload) => {
    if (payload.type === "myhome/bus_monitor/send") {
      return new Promise((_res, rej) => { rejectSend = rej; });
    }
    return Promise.resolve({});
  };
  const inFlightDisarmed = card._sendCustomFrame();
  card._toggleArmed(false);
  rejectSend({ code: "invalid_format", message: "Rejected after disarm" });
  await inFlightDisarmed;
  assert.equal(card._lastCheck, null);
  assert.equal(card._hasSendError, false);
  assert.equal(el("send-safety-notice").style.display, "none");
  assert.equal(el("btn-send").disabled, true);
});
