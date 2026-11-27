/**
 * HTML-escaping and XSS regression tests for custom_components/myhome/frontend/myhome-bus-card.js.
 *
 * Covers Frenck's hacs/default#15020 review findings (unescaped config title and dynamic
 * frame WHO options in _render()) as well as every other DOM/innerHTML sink across the card.
 */
import { test } from "node:test";
import assert from "node:assert/strict";
import { loadCard, frame } from "./harness.mjs";

const XSS_PAYLOADS = [
  '<img src=x onerror=alert(1)>',
  '" onmouseover="alert(1)',
  "</option><script>alert(1)</script>",
  "' onclick='alert(1)&<>",
];

function freshCard(opts = {}) {
  const ctx = loadCard();
  return { ...ctx, card: ctx.create(opts) };
}

test("_escapeHtml escapes all five HTML special characters and handles non-strings cleanly", () => {
  const { card } = freshCard();
  assert.equal(
    card._escapeHtml(`<img src="x" alt='y' &>`),
    "&lt;img src=&quot;x&quot; alt=&#039;y&#039; &amp;&gt;"
  );
  assert.equal(card._escapeHtml(null), "");
  assert.equal(card._escapeHtml(undefined), "");
  assert.equal(card._escapeHtml(""), "");
  assert.equal(card._escapeHtml(0), "0");
  assert.equal(card._escapeHtml(false), "false");
  assert.equal(card._escapeHtml(42), "42");
});

test("Frenck #1: configured title is escaped in both title attribute and visible text", () => {
  for (const payload of XSS_PAYLOADS) {
    const { card } = freshCard();
    card.setConfig({ title: payload });
    const html = card.shadowRoot.innerHTML;
    const escaped = card._escapeHtml(payload);

    assert.ok(
      html.includes(`<span class="title" title="${escaped}">📡 ${escaped}</span>`),
      `expected escaped title span for payload: ${payload}`
    );
    assert.ok(!html.includes(payload), `raw unescaped payload leaked into shadowRoot.innerHTML: ${payload}`);
  }
});

test("title handles nullish, empty, and numeric config values safely", () => {
  const { card } = freshCard();

  card.setConfig({ title: null });
  assert.ok(
    card.shadowRoot.innerHTML.includes(
      '<span class="title" title="MyHOME OpenWebNet Bus Monitor">📡 MyHOME OpenWebNet Bus Monitor</span>'
    )
  );

  card.setConfig({ title: undefined });
  assert.ok(
    card.shadowRoot.innerHTML.includes(
      '<span class="title" title="MyHOME OpenWebNet Bus Monitor">📡 MyHOME OpenWebNet Bus Monitor</span>'
    )
  );

  card._config = {};
  card._render();
  assert.ok(
    card.shadowRoot.innerHTML.includes(
      '<span class="title" title="MyHOME OpenWebNet Bus Monitor">📡 MyHOME OpenWebNet Bus Monitor</span>'
    )
  );

  card.setConfig({ title: "" });
  assert.ok(card.shadowRoot.innerHTML.includes('<span class="title" title="">📡 </span>'));

  card.setConfig({ title: 0 });
  assert.ok(card.shadowRoot.innerHTML.includes('<span class="title" title="0">📡 0</span>'));
});

test("Frenck #2: dynamic frame WHO values and catalog labels are escaped in _render() <option> tags", () => {
  for (const payload of XSS_PAYLOADS) {
    const { card } = freshCard();
    card._frames = [frame("*99*1*1##", { who: payload })];
    card._filterWho = payload;
    card._render();
    const html = card.shadowRoot.innerHTML;
    const escaped = card._escapeHtml(payload);

    assert.ok(
      html.includes(`<option value="${escaped}" selected>Subsystem (WHO=${escaped})</option>`),
      `expected escaped <option> for WHO payload: ${payload}`
    );
    assert.ok(!html.includes(payload), `raw unescaped WHO payload leaked into shadowRoot.innerHTML: ${payload}`);
  }

  // Catalog entry containing '&' (WHO=14 "Actuator Diagnostics & Lock") must be HTML-escaped too.
  const { card } = freshCard();
  card._render();
  assert.ok(
    card.shadowRoot.innerHTML.includes(
      '<option value="14">Actuator Diagnostics &amp; Lock (WHO=14)</option>'
    )
  );
});

test("max_frames config injection is rejected in setConfig() and escaped in _render()", () => {
  const { card } = freshCard();
  const payload = '<img src=x onerror=alert(1)>';

  card.setConfig({ max_frames: payload });
  assert.equal(card._maxDisplayFrames, 500);
  assert.ok(card.shadowRoot.innerHTML.includes('<span id="stat-max">500</span>'));
  assert.ok(!card.shadowRoot.innerHTML.includes(payload));

  // Defense in depth: even if _maxDisplayFrames is mutated directly, _render() escapes it.
  card._maxDisplayFrames = payload;
  card._render();
  assert.ok(
    card.shadowRoot.innerHTML.includes(`<span id="stat-max">${card._escapeHtml(payload)}</span>`)
  );
  assert.ok(!card.shadowRoot.innerHTML.includes(payload));
});

test("_render() escapes filter-where input attribute", () => {
  const { card } = freshCard();
  const payload = '" onfocus="alert(1)" autofocus="';
  card._filterWhere = payload;
  card._render();
  const html = card.shadowRoot.innerHTML;
  assert.ok(html.includes(`value="${card._escapeHtml(payload)}"`));
  assert.ok(!html.includes(payload));
});

test("_createFrameNode() escapes direction, who, raw, and time fields", () => {
  const dirPayload = '<img src=x onerror="&#97;&#108;&#101;&#114;&#116;(1)">';
  const whoPayload = '</option><script>alert(1)</script>';
  const rawPayload = '<img src=x onerror=alert(1)>&"\'';

  const { card } = freshCard();
  const node = card._createFrameNode(
    frame(rawPayload, { direction: dirPayload, who: whoPayload, timestamp: 1700000000 })
  );

  assert.ok(node.innerHTML.includes(card._escapeHtml(dirPayload.toUpperCase())));
  assert.ok(node.innerHTML.includes(card._escapeHtml(`WHO=${whoPayload}`)));
  assert.ok(node.innerHTML.includes(card._escapeHtml(rawPayload)));
  assert.ok(!node.innerHTML.includes("<img"));
  assert.ok(!node.innerHTML.includes("<IMG"));
  assert.ok(!node.innerHTML.includes("<script"));
  assert.ok(!node.innerHTML.includes("</option>"));

  // Non-string direction should not throw TypeError.
  const numericDirNode = card._createFrameNode(frame("*1*1*11##", { direction: 123, who: "1" }));
  assert.ok(numericDirNode.innerHTML.includes('<span class="col-dir dir-tx">123</span>'));
});

test("prototype property names in frame.who do not resolve Object.prototype entries", () => {
  const { card, el } = freshCard();
  for (const protoName of ["toString", "constructor", "__proto__", "valueOf"]) {
    assert.equal(card._formatWho(protoName), `WHO=${protoName}`);
    assert.equal(card._getWhoClass(protoName), "who-default");
    assert.match(card._getWhoBadgeStyle(protoName), /^style="background: hsl\(\d+(?:\.\d+)?, 45%, 18%\); color: hsl\(\d+(?:\.\d+)?, 85%, 75%\);"$/);

    card._ensureWhoRegistered(protoName);
    const added = el("filter-who").options.find((opt) => opt.value === protoName);
    assert.ok(added, `expected option registered for ${protoName}`);
    assert.equal(added.textContent, `Subsystem (WHO=${protoName})`);
  }
});

test("_ensureWhoRegistered() uses DOM value/textContent rather than HTML parsing", () => {
  const { card, el } = freshCard();
  const payload = '</option><script>alert(1)</script>';
  card._ensureWhoRegistered(payload);
  const opt = el("filter-who").options.find((o) => o.value === payload);
  assert.ok(opt);
  assert.equal(opt.value, payload);
  assert.equal(opt.textContent, `Subsystem (WHO=${payload})`);
  assert.equal(opt.innerHTML, "");
});

test("_handleExportTrace() sanitizes filter slugs and escapes fileName in feedback banner", async () => {
  const { card, el, downloads } = freshCard({ model: '<img src=x onerror=alert(1)>' });
  card._filterWho = '<img src=x onerror=alert(1)>';
  card._filterDir = '" onmouseover="alert(1)';
  card._filterWhere = '<script>alert(1)</script>';
  card._frames = [frame("*1*1*11##", { who: card._filterWho })];

  await card._handleExportTrace();

  const { fileName } = downloads.at(-1);
  assert.match(fileName, /^[A-Za-z0-9_.-]+\.json$/);
  const bannerHtml = el("feedback-banner").innerHTML;
  assert.ok(bannerHtml.includes(`<code>${card._escapeHtml(fileName)}</code>`));
  assert.ok(!bannerHtml.includes("<img"));
  assert.ok(!bannerHtml.includes("<script"));
  assert.ok(!bannerHtml.includes("onmouseover="));
});

test("_handleSweepBus() escapes error messages and survives nullish errors", async () => {
  const { card, el } = freshCard();
  const payload = '<img src=x onerror=alert(1)>';
  card._hass.callService = async () => {
    throw new Error(payload);
  };

  await card._handleSweepBus();
  const bannerHtml = el("feedback-banner").innerHTML;
  assert.ok(bannerHtml.includes(card._escapeHtml(payload)));
  assert.ok(!bannerHtml.includes(payload));

  card._hass.callService = async () => {
    throw null;
  };
  await card._handleSweepBus();
  assert.ok(el("feedback-banner").innerHTML.includes("null"));
});

test("_handleReportIssue() URL-encodes and HTML-escapes gateway/HA versions in banner links", async () => {
  const { card, el } = freshCard();
  const versionPayload = '" onmouseover="alert(1)\'<script>';
  card._hass.config.version = versionPayload;
  card._gatewayInfo = {
    model: '<img src=x onerror=alert(1)>',
    integration_version: versionPayload,
    ownd_version: versionPayload,
  };
  const opened = [];
  globalThis.window.open = (url) => opened.push(url);

  // Branch 1: clipboard failure (banner-warning)
  await card._handleReportIssue();
  const warnHtml = el("feedback-banner").innerHTML;
  assert.equal(opened.length, 1);
  assert.ok(warnHtml.includes(`href="${card._escapeHtml(opened[0])}"`));
  assert.ok(!warnHtml.includes(versionPayload));
  assert.ok(!warnHtml.includes("onmouseover="));
  assert.ok(!warnHtml.includes("<script>"));

  // Branch 2: clipboard success (banner-success)
  globalThis.window.isSecureContext = true;
  Object.defineProperty(globalThis, "navigator", {
    value: { userAgent: "node-test", clipboard: { writeText: async () => {} } },
    configurable: true,
  });
  await card._handleReportIssue();
  const okHtml = el("feedback-banner").innerHTML;
  assert.equal(opened.length, 2);
  assert.ok(okHtml.includes(`href="${card._escapeHtml(opened[1])}"`));
  assert.ok(!okHtml.includes(versionPayload));
  assert.ok(!okHtml.includes("onmouseover="));
  assert.ok(!okHtml.includes("<script>"));
});

test("_updateStats() writes gateway metadata via textContent only", () => {
  const { card, el } = freshCard();
  const payload = '<img src=x onerror=alert(1)>';
  card._gatewayInfo = {
    integration_version: payload,
    ownd_version: payload,
    queue_depth: payload,
  };
  card._updateStats();
  assert.equal(el("stat-version").innerHTML, "");
  assert.equal(el("stat-version").textContent, `v${payload} (OWNd ${payload})`);
  assert.equal(el("stat-queue").innerHTML, "");
  assert.equal(el("stat-queue").textContent, payload);
});
