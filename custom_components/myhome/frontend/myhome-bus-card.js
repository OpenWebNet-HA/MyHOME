/**
 * MyHOME Bus Monitor — Custom Lovelace Card
 * 
 * Provides real-time streaming, filtering, and diagnostic frame transmission
 * for BTicino / Legrand MyHOME SCS bus systems via OpenWebNet.
 */

// Fallback only: the live value comes from the backend (bus_monitor/info -> integration_version).
const CARD_VERSION = "2.0.0b12";

const WHO_CATALOG = {
  "0": { name: "Scenarios (Basic)", short: "Scenario", class: "who-cen" },
  "1": { name: "Lighting / Switches", short: "Light/Switch", class: "who-light" },
  "2": { name: "Automation / Shutters", short: "Automation", class: "who-cover" },
  "3": { name: "Load Control", short: "Load Ctrl", class: "who-energy" },
  "4": { name: "Heating / Thermoregulation", short: "Heating", class: "who-thermo" },
  "5": { name: "Burglar Alarm", short: "Burglar Alarm", class: "who-alarm" },
  "6": { name: "Door Entry / Access Control", short: "Door Entry", class: "who-access" },
  "7": { name: "Video Door Entry / Multimedia", short: "Video Entry", class: "who-video" },
  "9": { name: "Auxiliary", short: "Auxiliary", class: "who-default" },
  "13": { name: "Gateway Management", short: "Gateway", class: "who-diag" },
  "14": { name: "Actuator Diagnostics & Lock", short: "Actuator Lock", class: "who-diag" },
  "15": { name: "CEN Pushbuttons", short: "CEN", class: "who-cen" },
  "16": { name: "Sound System", short: "Sound", class: "who-sound" },
  "17": { name: "Scenario Management / MH200N", short: "MH200N", class: "who-cen" },
  "18": { name: "Energy Management", short: "Energy", class: "who-energy" },
  "22": { name: "Sound Diffusion Extended", short: "Sound Ext", class: "who-sound" },
  "24": { name: "Lighting Management / DALI", short: "DALI Light", class: "who-light" },
  "25": { name: "CEN+ / Security", short: "CEN+/Sec", class: "who-cen" },
  "1001": { name: "Lighting Diagnostics", short: "Diag Light", class: "who-diag" },
  "1004": { name: "Heating Diagnostics", short: "Diag Heat", class: "who-diag" },
  "1013": { name: "Gateway Diagnostics", short: "Diag Gateway", class: "who-diag" },
};

class MyHomeBusCard extends HTMLElement {
  constructor() {
    super();
    this.attachShadow({ mode: "open" });
    this._frames = [];
    this._maxDisplayFrames = 200;
    this._isPaused = false;
    this._filterWho = "all";
    this._filterWhere = "";
    this._filterDir = "all";
    // Capture mode chosen by the user: "trace" (default, passive recording started
    // with Start Trace or simply the live buffer) or "sweep" (buffer populated by
    // Sweep Bus). Export / Copy follow it; Clear and Start Trace reset it.
    this._captureMode = "trace";
    this._lastSweepAt = null;
    this._traceStartedAt = null;
    // True between Start Trace and Stop Trace (or Pause / Clear / Sweep Bus).
    this._tracing = false;
    // Raw-frame transmission must be armed explicitly (see _toggleArmed).
    this._sendArmed = false;
    this._helpOpen = false;
    this._unsub = null;
    this._stats = { captured: 0, total_rx: 0, total_tx: 0 };
    this._gatewayInfo = {};
    this._connectionStatus = "connecting"; // "connecting" | "connected" | "disconnected" | "paused"
    this._retryTimeout = null;
    this._retryDelay = 1000;
    this._maxRetryDelay = 30000;
    this._isSubscribing = false;
  }

  static getStubConfig() {
    return {
      title: "MyHOME OpenWebNet Bus Monitor",
      max_frames: 200,
    };
  }

  static getConfigForm() {
    return {
      schema: [
        { name: "title", label: "Title", selector: { text: {} } },
        { name: "max_frames", label: "Max Frames in Buffer", selector: { number: { min: 50, max: 1000, step: 50, mode: "box" } } },
      ],
    };
  }

  setConfig(config) {
    this._config = Object.assign(
      {
        title: "MyHOME OpenWebNet Bus Monitor",
        max_frames: 200,
        mac: null,
      },
      config
    );
    this._maxDisplayFrames = this._config.max_frames || 200;
    this._render();
  }

  get hass() {
    return this._hass;
  }

  set hass(hass) {
    const oldHass = this._hass;
    this._hass = hass;

    // Connect stream once hass is available
    if (!oldHass && hass) {
      this._subscribeStream();
    } else if (oldHass && hass && oldHass.connection !== hass.connection) {
      if (this._unsub) {
        try { this._unsub(); } catch (e) {}
        this._unsub = null;
      }
      this._subscribeStream();
    }
  }

  connectedCallback() {
    if (this._hass && !this._unsub && !this._isSubscribing) {
      this._subscribeStream();
    }
  }

  disconnectedCallback() {
    if (this._retryTimeout) {
      clearTimeout(this._retryTimeout);
      this._retryTimeout = null;
    }
    if (this._unsub) {
      try { this._unsub(); } catch (e) {}
      this._unsub = null;
    }
    this._isSubscribing = false;
  }

  _wsPayload(type, extra = {}) {
    const payload = Object.assign({ type }, extra);
    if (this._config && this._config.mac != null && String(this._config.mac).trim() !== "") {
      payload.mac = String(this._config.mac).trim();
    }
    return payload;
  }

  async _loadHistory() {
    if (!this._hass) return;
    try {
      const res = await this._hass.callWS(
        this._wsPayload("myhome/bus_monitor/history", { limit: 50 })
      );
      if (res && res.frames) {
        const existingKeys = new Set(
          this._frames.map((f) => `${f.timestamp}_${f.raw}_${f.direction}`)
        );
        const newHistory = res.frames.filter(
          (f) => !existingKeys.has(`${f.timestamp}_${f.raw}_${f.direction}`)
        );
        for (const f of newHistory) {
          if (f.who != null) this._ensureWhoRegistered(f.who);
        }
        this._frames = newHistory.concat(this._frames);
        if (this._frames.length > this._maxDisplayFrames) {
          this._frames = this._frames.slice(-this._maxDisplayFrames);
        }
        if (res.stats) this._stats = res.stats;
        if (res.gateway) this._gatewayInfo = res.gateway;
        this._updateFrameList();
        this._updateStats();
      }
    } catch (err) {
      console.warn("MyHOME Bus Monitor: Failed to load initial history", err);
    }
  }

  async _subscribeStream() {
    if (!this._hass || this._unsub || this._isSubscribing) return;
    this._isSubscribing = true;
    this._updateConnectionStatus("connecting");

    try {
      this._unsub = await this._hass.connection.subscribeMessage(
        (frame) => this._onNewFrame(frame),
        this._wsPayload("myhome/bus_monitor/stream")
      );
      this._isSubscribing = false;
      this._retryDelay = 1000;
      this._updateConnectionStatus("connected");
      this._loadHistory();
    } catch (err) {
      this._isSubscribing = false;
      console.warn(`MyHOME Bus Monitor: Failed to subscribe to stream, retrying in ${this._retryDelay / 1000}s`, err);
      this._updateConnectionStatus("disconnected");
      this._scheduleRetry();
    }
  }

  _scheduleRetry() {
    if (this._retryTimeout) {
      clearTimeout(this._retryTimeout);
      this._retryTimeout = null;
    }
    const delay = this._retryDelay;
    this._retryTimeout = setTimeout(() => {
      this._retryTimeout = null;
      if (this._hass && !this._unsub && !this._isSubscribing) {
        this._subscribeStream();
      }
    }, delay);

    this._retryDelay = Math.min(this._retryDelay * 2, this._maxRetryDelay);
  }

  _updateConnectionStatus(status) {
    if (this._isPaused) {
      this._connectionStatus = "paused";
    } else {
      this._connectionStatus = status;
    }
    this._updateBadge();
    this._updatePlaceholder();
  }

  _updateBadge() {
    const badge = this.shadowRoot && this.shadowRoot.getElementById("badge");
    if (!badge) return;

    if (this._isPaused) {
      badge.textContent = "PAUSED";
      badge.className = "badge badge-paused";
    } else if (this._tracing && this._connectionStatus === "connected") {
      badge.textContent = "● REC";
      badge.className = "badge badge-recording";
    } else if (this._connectionStatus === "connected") {
      badge.textContent = "LIVE";
      badge.className = "badge badge-live";
    } else if (this._connectionStatus === "connecting") {
      badge.textContent = "CONNECTING...";
      badge.className = "badge badge-connecting";
    } else if (this._connectionStatus === "disconnected") {
      badge.textContent = "DISCONNECTED";
      badge.className = "badge badge-disconnected";
    }
  }

  _updatePlaceholder(isFiltered = false) {
    const container = this.shadowRoot && this.shadowRoot.getElementById("stream");
    if (!container) return;
    if (isFiltered) {
      container.innerHTML = `<div class="placeholder-msg">No bus frames match the active filter.</div>`;
      return;
    }
    if (this._frames.length === 0) {
      let msg = "Waiting for OpenWebNet bus frames...";
      if (this._connectionStatus === "connecting") {
        msg = "Connecting to MyHOME gateway stream...";
      } else if (this._connectionStatus === "disconnected") {
        msg = `Disconnected from MyHOME gateway. Reconnecting in ${Math.round(this._retryDelay / 1000)}s...`;
      }
      container.innerHTML = `<div class="placeholder-msg">${msg}</div>`;
    }
  }

  _ensureWhoRegistered(who) {
    if (who == null || String(who).trim() === "") return;
    const whoStr = String(who).trim();
    const select = this.shadowRoot && this.shadowRoot.getElementById("filter-who");
    if (!select) return;

    for (let i = 0; i < select.options.length; i++) {
      if (select.options[i].value === whoStr) return;
    }

    const catalogEntry = WHO_CATALOG[whoStr];
    const label = catalogEntry
      ? `${catalogEntry.name} (WHO=${whoStr})`
      : `Subsystem (WHO=${whoStr})`;

    const opt = document.createElement("option");
    opt.value = whoStr;
    opt.textContent = label;

    const whoNum = parseInt(whoStr, 10);
    let inserted = false;
    for (let i = 1; i < select.options.length; i++) {
      const curNum = parseInt(select.options[i].value, 10);
      if (!isNaN(whoNum) && !isNaN(curNum) && whoNum < curNum) {
        select.insertBefore(opt, select.options[i]);
        inserted = true;
        break;
      }
    }
    if (!inserted) {
      select.appendChild(opt);
    }
  }

  _onNewFrame(frame) {
    if (this._connectionStatus !== "connected" && !this._isPaused) {
      this._updateConnectionStatus("connected");
    }
    if (this._isPaused) return;

    // Suppress immediate duplicate frames within 1.0s window (e.g. concurrent session echoes)
    const last = this._frames[this._frames.length - 1];
    if (
      last &&
      last.direction === frame.direction &&
      last.raw === frame.raw &&
      Math.abs((frame.timestamp || 0) - (last.timestamp || 0)) < 1.0
    ) {
      return;
    }

    if (frame.who != null) {
      this._ensureWhoRegistered(frame.who);
    }

    if (frame.direction === "rx") this._stats.total_rx++;
    else this._stats.total_tx++;
    this._stats.captured++;

    this._frames.push(frame);
    if (this._frames.length > this._maxDisplayFrames) {
      this._frames.shift();
    }

    this._appendFrameElement(frame);
    this._updateStats();
  }

  _matchesFilter(frame) {
    if (this._filterDir !== "all") {
      const dir = (frame.direction || "").toLowerCase();
      if (this._filterDir === "rx" && dir !== "rx") return false;
      if (this._filterDir === "tx" && dir !== "tx") return false;
      if (this._filterDir === "ack" && !frame.is_ack) return false;
      if (this._filterDir === "nack" && !frame.is_nack) return false;
    }
    if (this._filterWho !== "all" && String(frame.who) !== String(this._filterWho)) {
      return false;
    }
    if (this._filterWhere) {
      const q = this._filterWhere.trim().toLowerCase();
      if (q) {
        if (q.startsWith("where:") || q.startsWith("where=")) {
          const val = q.substring(6).trim();
          if (!String(frame.where || "").toLowerCase().includes(val)) return false;
        } else if (q.startsWith("what:") || q.startsWith("what=")) {
          const val = q.substring(5).trim();
          if (!String(frame.what || "").toLowerCase().includes(val)) return false;
        } else if (q.startsWith("dim:") || q.startsWith("dim=")) {
          const val = q.substring(4).trim();
          if (!String(frame.dimension || "").toLowerCase().includes(val)) return false;
        } else if (q.startsWith("raw:") || q.startsWith("raw=")) {
          const val = q.substring(4).trim();
          if (!String(frame.raw || "").toLowerCase().includes(val)) return false;
        } else {
          const inWhere = String(frame.where || "").toLowerCase().includes(q);
          const inRaw = String(frame.raw || "").toLowerCase().includes(q);
          const inWhat = String(frame.what || "").toLowerCase().includes(q);
          const inDim = String(frame.dimension || "").toLowerCase().includes(q);
          if (!inWhere && !inRaw && !inWhat && !inDim) return false;
        }
      }
    }
    return true;
  }

  _formatWho(who) {
    if (who == null || String(who).trim() === "") return "Sys";
    const strWho = String(who).trim();
    const entry = WHO_CATALOG[strWho];
    if (entry && entry.short) return entry.short;
    if (entry && entry.name) return entry.name;
    return `WHO=${strWho}`;
  }

  _formatFrameTime(frame) {
    // Frames are stamped in UTC by the backend; render them in the browser's
    // local time zone (HH:MM:SS.mmm) so they line up with the HA logbook.
    let date = null;
    if (typeof frame.timestamp === "number" && frame.timestamp > 0) {
      date = new Date(frame.timestamp * 1000);
    } else if (frame.iso_time) {
      date = new Date(frame.iso_time);
    }
    if (!date || Number.isNaN(date.getTime())) return "";
    const pad = (n, w = 2) => String(n).padStart(w, "0");
    return `${pad(date.getHours())}:${pad(date.getMinutes())}:${pad(date.getSeconds())}.${pad(date.getMilliseconds(), 3)}`;
  }

  _getWhoClass(who) {
    if (who == null) return "who-default";
    const entry = WHO_CATALOG[String(who).trim()];
    if (entry && entry.class) return entry.class;
    return "who-default";
  }

  _getWhoBadgeStyle(who) {
    if (who == null || String(who).trim() === "") return "";
    const str = String(who).trim();
    if (WHO_CATALOG[str] && WHO_CATALOG[str].class !== "who-default") {
      return "";
    }
    const num = parseInt(str, 10);
    const hue = !isNaN(num) ? (num * 137.5) % 360 : 200;
    return `style="background: hsl(${hue}, 45%, 18%); color: hsl(${hue}, 85%, 75%);"`;
  }

  _render() {
    const sortedWhoKeys = Object.keys(WHO_CATALOG).sort((a, b) => {
      const na = parseInt(a, 10);
      const nb = parseInt(b, 10);
      if (!isNaN(na) && !isNaN(nb)) return na - nb;
      return a.localeCompare(b);
    });

    const optionsList = [
      `<option value="all"${this._filterWho === "all" ? " selected" : ""}>All Subsystems</option>`
    ];
    for (const key of sortedWhoKeys) {
      const item = WHO_CATALOG[key];
      const sel = String(this._filterWho) === key ? " selected" : "";
      optionsList.push(`<option value="${key}"${sel}>${item.name} (WHO=${key})</option>`);
    }

    const seenWhos = new Set(sortedWhoKeys);
    for (const f of this._frames) {
      if (f.who != null) {
        const wStr = String(f.who).trim();
        if (wStr && !seenWhos.has(wStr)) {
          seenWhos.add(wStr);
          const sel = String(this._filterWho) === wStr ? " selected" : "";
          optionsList.push(`<option value="${wStr}"${sel}>Subsystem (WHO=${wStr})</option>`);
        }
      }
    }
    const whoOptionsHtml = optionsList.join("\n            ");

    this.shadowRoot.innerHTML = `
      <style>
        :host {
          display: block;
          font-family: var(--ha-card-font-family, inherit);
        }
        ha-card {
          padding: 16px;
          background: var(--ha-card-background, var(--card-background-color, #fff));
          border-radius: var(--ha-card-border-radius, 12px);
          box-shadow: var(--ha-card-box-shadow, none);
          border: var(--ha-card-border-width, 1px) solid var(--ha-card-border-color, var(--divider-color, #e0e0e0));
        }
        .header {
          display: flex;
          flex-direction: column;
          gap: 10px;
          margin-bottom: 12px;
        }
        .title-row {
          display: flex;
          align-items: center;
          gap: 10px;
          min-height: 28px;
        }
        .title {
          flex: 1 1 auto;
          min-width: 0;
          font-size: 1.15rem;
          font-weight: 600;
          color: var(--primary-text-color);
          white-space: nowrap;
          overflow: hidden;
          text-overflow: ellipsis;
        }
        .badge {
          flex-shrink: 0;
          white-space: nowrap;
          font-size: 0.72rem;
          letter-spacing: 0.02em;
          padding: 3px 9px;
          border-radius: 12px;
          font-weight: 600;
          line-height: 1.2;
        }
        .toolbar {
          display: flex;
          flex-wrap: wrap;
          align-items: center;
          gap: 8px 14px;
        }
        .toolbar .group {
          display: inline-flex;
          gap: 6px;
        }
        .toolbar .group.stream { margin-left: auto; }
        .toolbar button {
          height: 32px;
          padding: 0 12px;
          font-size: 0.8rem;
          font-weight: 600;
          display: inline-flex;
          align-items: center;
          gap: 5px;
          white-space: nowrap;
        }
        .badge-live {
          background: rgba(76, 175, 80, 0.15);
          color: #2e7d32;
        }
        .badge-connecting {
          background: rgba(255, 152, 0, 0.15);
          color: #f57c00;
        }
        .badge-disconnected {
          background: rgba(244, 67, 54, 0.15);
          color: #d32f2f;
        }
        .badge-paused {
          background: rgba(244, 67, 54, 0.15);
          color: #d32f2f;
        }
        .badge-recording {
          background: rgba(198, 40, 40, 0.2);
          color: #ef5350;
          animation: rec-blink 1.2s ease-in-out infinite;
        }
        @keyframes rec-blink { 50% { opacity: 0.45; } }
        .placeholder-msg {
          color: #888;
          font-style: italic;
          padding: 24px 16px;
          text-align: center;
        }
        .stats-bar {
          display: flex;
          gap: 16px;
          font-size: 0.8rem;
          color: var(--secondary-text-color);
          margin-bottom: 12px;
          padding-bottom: 8px;
          border-bottom: 1px solid var(--divider-color, #eee);
        }
        .stat-val {
          font-weight: 600;
          color: var(--primary-text-color);
        }
        .controls {
          display: flex;
          flex-wrap: wrap;
          gap: 8px;
          margin-bottom: 12px;
        }
        select, input[type="text"] {
          background: var(--card-background-color, #fafafa);
          color: var(--primary-text-color);
          border: 1px solid var(--divider-color, #ccc);
          border-radius: 6px;
          padding: 6px 10px;
          font-size: 0.85rem;
        }
        button {
          background: var(--primary-color, #03a9f4);
          color: #fff;
          border: none;
          border-radius: 6px;
          padding: 6px 12px;
          font-size: 0.85rem;
          cursor: pointer;
          font-weight: 500;
          transition: opacity 0.2s;
        }
        button:hover { opacity: 0.85; }
        button.btn-secondary {
          background: var(--secondary-background-color, #eceff1);
          color: var(--primary-text-color);
        }
        .stream-container {
          background: #1e1e1e;
          color: #d4d4d4;
          font-family: monospace, monospace;
          font-size: 0.82rem;
          height: 280px;
          overflow-y: auto;
          border-radius: 8px;
          padding: 8px 12px;
          box-shadow: inset 0 2px 4px rgba(0,0,0,0.3);
        }
        .frame-line {
          display: flex;
          gap: 8px;
          padding: 2px 0;
          border-bottom: 1px solid rgba(255, 255, 255, 0.05);
          align-items: center;
        }
        .col-time { color: #888; flex-shrink: 0; }
        .col-dir {
          font-weight: 600;
          padding: 1px 4px;
          border-radius: 4px;
          font-size: 0.72rem;
          flex-shrink: 0;
        }
        .dir-rx { background: #1b5e20; color: #a5d6a7; }
        .dir-tx { background: #e65100; color: #ffcc80; }
        .col-who {
          font-size: 0.75rem;
          padding: 1px 6px;
          border-radius: 4px;
          flex-shrink: 0;
        }
        .who-light { background: #4a3b00; color: #ffe082; }
        .who-cover { background: #0d47a1; color: #90caf9; }
        .who-thermo { background: #b71c1c; color: #ef9a9a; }
        .who-alarm { background: #880e4f; color: #f48fb1; }
        .who-cen { background: #4a148c; color: #ce93d8; }
        .who-sound { background: #004d40; color: #80cbc4; }
        .who-energy { background: #006064; color: #80deea; }
        .who-access { background: #bf360c; color: #ffcc80; }
        .who-video { background: #1a237e; color: #c5cae9; }
        .who-diag { background: #263238; color: #cfd8dc; }
        .who-default { background: #37474f; color: #b0bec5; }
        .col-raw { color: #fff; word-break: break-all; }
        .raw-ack { color: #69f0ae; font-weight: 600; }
        .raw-nack { color: #ff5252; font-weight: 600; }
        .sender-bar {
          display: flex;
          gap: 8px;
          margin-top: 12px;
        }
        .sender-bar input { flex-grow: 1; }
        .btn-trace {
          background: #c62828;
          color: #fff;
        }
        .btn-trace:hover { opacity: 0.85; }
        .btn-help {
          flex-shrink: 0;
          background: #0288d1;
          color: #fff;
          border: none;
          border-radius: 50%;
          width: 26px;
          height: 26px;
          padding: 0;
          font-family: Georgia, "Times New Roman", serif;
          font-style: italic;
          font-weight: 700;
          font-size: 0.9rem;
          line-height: 26px;
          text-align: center;
          box-shadow: 0 0 0 2px rgba(2, 136, 209, 0.25);
        }
        .btn-help:hover { background: #039be5; opacity: 1; }
        .btn-help.open { background: #ffb300; color: #212121; box-shadow: 0 0 0 2px rgba(255, 179, 0, 0.35); }
        .help-panel {
          display: none;
          margin: 0 0 12px 0;
          padding: 10px 14px;
          border-radius: 6px;
          border: 1px solid var(--divider-color, #555);
          background: var(--secondary-background-color, #263238);
          font-size: 0.82rem;
          line-height: 1.5;
        }
        .help-panel h4 { margin: 8px 0 4px 0; font-size: 0.85rem; }
        .help-panel p, .help-panel ul { margin: 4px 0; }
        .help-panel ul { padding-left: 18px; }
        .help-panel code { font-size: 0.78rem; }
        .arm-bar {
          display: flex;
          align-items: center;
          gap: 8px;
          margin-top: 14px;
          padding: 8px 12px;
          border-radius: 6px;
          border: 1px solid #ef6c00;
          background: rgba(239, 108, 0, 0.12);
          font-size: 0.8rem;
          line-height: 1.4;
        }
        .arm-bar.armed {
          border-color: #c62828;
          background: rgba(198, 40, 40, 0.15);
        }
        .arm-bar label { display: inline-flex; align-items: center; gap: 6px; cursor: pointer; font-weight: 600; white-space: nowrap; }
        .arm-bar .arm-text { flex-grow: 1; opacity: 0.9; }
        .sender-bar button:disabled, .sender-bar input:disabled { opacity: 0.45; cursor: not-allowed; }
        .btn-sweep {
          background: #1976d2;
          color: #fff;
        }
        .btn-sweep:hover {
          background: #1565c0;
        }
        .btn-export {
          background: #2e7d32;
          color: #fff;
        }
        .btn-export:hover {
          background: #1b5e20;
        }
        .btn-report {
          background: #ff9800;
          color: #fff;
        }
        .btn-report:hover {
          background: #f57c00;
        }
        .feedback-banner {
          display: none;
          justify-content: space-between;
          align-items: center;
          padding: 8px 12px;
          margin-bottom: 12px;
          border-radius: 6px;
          font-size: 0.82rem;
          line-height: 1.4;
          gap: 8px;
        }
        .banner-success {
          background: rgba(76, 175, 80, 0.15);
          color: #2e7d32;
          border: 1px solid rgba(76, 175, 80, 0.35);
        }
        .banner-warning {
          background: rgba(255, 152, 0, 0.15);
          color: #e65100;
          border: 1px solid rgba(255, 152, 0, 0.35);
        }
        .banner-link {
          color: inherit;
          font-weight: 600;
          text-decoration: underline;
          white-space: nowrap;
        }
      </style>

      <ha-card>
        <div class="header">
          <div class="title-row">
            <span class="title" title="${this._escapeHtml(String(this._config.title))}">📡 ${this._config.title}</span>
            <span id="badge" class="badge badge-connecting">CONNECTING...</span>
            <button id="btn-help" class="btn-help" title="How this card works: Start Trace vs Sweep Bus, exports, transmit bar" aria-label="How this card works">i</button>
          </div>
          <div class="toolbar actions" role="toolbar" aria-label="Bus monitor actions">
            <div class="group capture" aria-label="Capture">
              <button id="btn-trace" class="btn-trace" title="Start a new passive trace: clears the buffer and records everything the bus says. Harmless - nothing is sent.">
                🔴 Start Trace
              </button>
              <button id="btn-sweep" class="btn-sweep" title="Start a new bus sweep: clears the buffer and asks every subsystem for its status. Harmless - only status requests are sent.">
                🧹 Sweep Bus
              </button>
            </div>
            <div class="group output" aria-label="Output">
              <button id="btn-export" class="btn-export" title="Download the frames currently shown (active filters applied) as a JSON file named after the capture kind">
                💾 Export Trace
              </button>
              <button id="btn-report" class="btn-report" title="Copy the shown frames as a diagnostic markdown bundle to the clipboard and open the GitHub issue form">
                📋 Copy Trace
              </button>
            </div>
            <div class="group stream" aria-label="Stream">
              <button id="btn-pause" class="btn-secondary">Pause</button>
              <button id="btn-clear" class="btn-secondary">Clear</button>
            </div>
          </div>
        </div>

        <div id="help-panel" class="help-panel">
          <p><strong>Two ways to capture, both harmless:</strong></p>
          <h4>🔴 Start Trace / ⏹ Stop Trace</h4>
          <p>Clears the buffer and records what the bus says while you reproduce a problem (press a wall switch, run an automation, move a cover). Nothing is sent to the bus. <em>Stop Trace</em> freezes the buffer; then <em>Export Trace</em> or <em>Copy Trace</em>. <em>Resume</em> returns to the live view.</p>
          <h4>🧹 Sweep Bus</h4>
          <p>Clears the buffer and sends one status request per subsystem (<code>*#1*0##</code>-style queries). Every device answers with its current state, so the buffer becomes a device inventory. Only read-only status requests are sent. Then <em>Export Sweep</em> / <em>Copy Sweep</em>.</p>
          <h4>💾 Export / 📋 Copy</h4>
          <p>Both use the frames <strong>currently shown</strong> (WHO / WHERE / direction filters applied). The file is named <code>myhome_&lt;trace|sweep&gt;_&lt;gateway&gt;_&lt;filter&gt;_&lt;time&gt;.json</code> and starts with a <code>capture</code> block describing what it is. Clear the filters to export the whole buffer.</p>
          <h4>⚠️ Transmit frame</h4>
          <p>The bar at the bottom writes a raw OpenWebNet frame to the bus - this <strong>can</strong> switch loads, move shutters, arm or disarm the alarm. It stays disabled until you tick <em>I understand the risk</em>. Trace and Sweep never use it.</p>
          <p style="opacity:0.8">Time stamps are shown in your browser's local time; exports keep UTC.</p>
        </div>

        <div id="feedback-banner" class="feedback-banner"></div>

        <div class="stats-bar">
          <div>Buffered: <span id="stat-buffer" class="stat-val">0</span>/<span id="stat-max">${this._maxDisplayFrames}</span></div>
          <div>RX: <span id="stat-rx" class="stat-val">0</span></div>
          <div>TX: <span id="stat-tx" class="stat-val">0</span></div>
          <div>Queue: <span id="stat-queue" class="stat-val">0</span></div>
          <div style="margin-left: auto; font-size: 0.75rem; opacity: 0.85;">MyHOME <span id="stat-version" class="stat-val">v${CARD_VERSION}</span></div>
        </div>

        <div class="controls">
          <select id="filter-who">
            ${whoOptionsHtml}
          </select>

          <input type="text" id="filter-where" placeholder="Filter WHERE / WHAT / Raw..." value="${this._escapeHtml(this._filterWhere)}" style="width: 170px;" />

          <select id="filter-dir">
            <option value="all"${this._filterDir === "all" ? " selected" : ""}>All Directions</option>
            <option value="rx"${this._filterDir === "rx" ? " selected" : ""}>RX (Bus Traffic)</option>
            <option value="tx"${this._filterDir === "tx" ? " selected" : ""}>TX (Commands)</option>
            <option value="ack"${this._filterDir === "ack" ? " selected" : ""}>ACK (*#*1##)</option>
            <option value="nack"${this._filterDir === "nack" ? " selected" : ""}>NACK (*#*0##)</option>
          </select>
        </div>

        <div id="stream" class="stream-container"></div>

        <div id="arm-bar" class="arm-bar">
          <span class="arm-text"><strong>⚠️ Direct bus command.</strong> The frame below is written to the SCS bus as-is and can switch loads, move shutters or arm/disarm the alarm. <em>Start Trace</em> and <em>Sweep Bus</em> above are read-only and safe.</span>
          <label><input type="checkbox" id="arm-send" /> I understand the risk</label>
        </div>
        <div class="sender-bar">
          <input type="text" id="send-frame" placeholder="Transmit frame (e.g. *1*1*12##)..." disabled />
          <button id="btn-send" disabled>Send</button>
        </div>
      </ha-card>
    `;

    this._bindEvents();
    this._updateBadge();
    this._updateFrameList();
  }

  _bindEvents() {
    const root = this.shadowRoot;
    if (!root) return;
    root.getElementById("btn-trace")?.addEventListener("click", () => this._handleStartTrace());
    root.getElementById("btn-sweep")?.addEventListener("click", () => this._handleSweepBus());
    root.getElementById("btn-help")?.addEventListener("click", () => this._toggleHelp());
    root.getElementById("arm-send")?.addEventListener("change", (e) => this._toggleArmed(!!e.target.checked));
    root.getElementById("btn-export")?.addEventListener("click", () => this._handleExportTrace());
    root.getElementById("btn-report")?.addEventListener("click", () => this._handleReportIssue());
    root.getElementById("btn-pause")?.addEventListener("click", () => this._togglePause());
    root.getElementById("btn-clear")?.addEventListener("click", () => this._clearBuffer());
    root.getElementById("filter-who")?.addEventListener("change", (e) => {
      this._filterWho = e.target.value;
      this._updateFrameList();
    });
    root.getElementById("filter-where")?.addEventListener("input", (e) => {
      this._filterWhere = e.target.value;
      this._updateFrameList();
    });
    root.getElementById("filter-dir")?.addEventListener("change", (e) => {
      this._filterDir = e.target.value;
      this._updateFrameList();
    });
    root.getElementById("btn-send")?.addEventListener("click", () => this._sendCustomFrame());
    root.getElementById("send-frame")?.addEventListener("keydown", (e) => {
      if (e.key === "Enter") this._sendCustomFrame();
    });
  }

  _setCaptureMode(mode) {
    this._captureMode = mode === "sweep" ? "sweep" : "trace";
    this._refreshExportLabel();
  }

  _setTracing(on) {
    this._tracing = !!on;
    const btn = this.shadowRoot && this.shadowRoot.getElementById("btn-trace");
    if (btn) btn.innerHTML = this._tracing ? "⏹ Stop Trace" : "🔴 Start Trace";
    this._updateBadge();
  }

  async _handleStartTrace() {
    if (this._tracing) {
      // Stop: freeze the buffer so the export is exactly what was reproduced.
      this._setTracing(false);
      if (!this._isPaused) this._togglePause();
      this._showBanner(
        "banner-success",
        `<span><strong>⏹ Trace stopped.</strong> ${this._frames.length} frame(s) captured and frozen. Click <em>Export Trace</em> or <em>Copy Trace</em>; <em>Resume</em> goes back to the live view.</span>`,
        8000
      );
      return;
    }
    await this._clearBuffer();
    this._traceStartedAt = Date.now() / 1000;
    this._lastSweepAt = null;
    this._setCaptureMode("trace");
    if (this._isPaused) this._togglePause();
    this._setTracing(true);
    this._showBanner(
      "banner-success",
      `<span><strong>🔴 Trace running.</strong> Reproduce the problem now (wall switch, automation, cover...), then click <em>Stop Trace</em> and export. Nothing is sent to the bus.</span>`,
      8000
    );
  }

  _toggleHelp() {
    this._helpOpen = !this._helpOpen;
    const panel = this.shadowRoot.getElementById("help-panel");
    if (panel) panel.style.display = this._helpOpen ? "block" : "none";
    const btn = this.shadowRoot.getElementById("btn-help");
    if (btn) btn.classList.toggle("open", this._helpOpen);
  }

  _toggleArmed(armed) {
    this._sendArmed = !!armed;
    const root = this.shadowRoot;
    const input = root.getElementById("send-frame");
    const btn = root.getElementById("btn-send");
    const bar = root.getElementById("arm-bar");
    if (input) input.disabled = !this._sendArmed;
    if (btn) btn.disabled = !this._sendArmed;
    if (bar) bar.classList.toggle("armed", this._sendArmed);
    if (this._sendArmed && input) input.focus();
  }

  _showBanner(className, html, timeoutMs) {
    const banner = this.shadowRoot.getElementById("feedback-banner");
    if (!banner) return;
    if (this._bannerTimeout) {
      clearTimeout(this._bannerTimeout);
      this._bannerTimeout = null;
    }
    banner.className = `feedback-banner ${className}`;
    banner.innerHTML = html;
    banner.style.display = "flex";
    this._bannerTimeout = setTimeout(() => {
      banner.style.display = "none";
    }, timeoutMs);
  }

  _togglePause() {
    this._isPaused = !this._isPaused;
    const btn = this.shadowRoot.getElementById("btn-pause");
    if (btn) {
      btn.textContent = this._isPaused ? "Resume" : "Pause";
    }
    if (this._isPaused && this._tracing) this._setTracing(false);
    this._updateBadge();
  }

  async _clearBuffer() {
    this._frames = [];
    this._traceStartedAt = null;
    this._lastSweepAt = null;
    this._setTracing(false);
    this._setCaptureMode("trace");
    this._updateFrameList();
    this._updateStats();
    if (this._hass) {
      try {
        await this._hass.callWS(
          this._wsPayload("myhome/bus_monitor/clear")
        );
      } catch (err) {
        console.warn("Could not clear backend bus monitor", err);
      }
    }
  }

  async _sendCustomFrame() {
    if (!this._sendArmed) return;
    const input = this.shadowRoot.getElementById("send-frame");
    const frame = input ? input.value.trim() : "";
    if (!frame || !this._hass) return;

    try {
      await this._hass.callWS(
        this._wsPayload("myhome/bus_monitor/send", { frame: frame })
      );
      if (input) input.value = "";
    } catch (err) {
      alert(`Error sending frame: ${err.message || err}`);
    }
  }

  _updateStats() {
    const root = this.shadowRoot;
    if (!root) return;
    const buf = root.getElementById("stat-buffer");
    const max = root.getElementById("stat-max");
    const rx = root.getElementById("stat-rx");
    const tx = root.getElementById("stat-tx");
    const queue = root.getElementById("stat-queue");
    const ver = root.getElementById("stat-version");
    if (buf) buf.textContent = this._frames.length;
    if (max) max.textContent = this._maxDisplayFrames;
    if (rx) rx.textContent = this._stats.total_rx;
    if (tx) tx.textContent = this._stats.total_tx;
    if (queue) queue.textContent = (this._gatewayInfo && this._gatewayInfo.queue_depth != null) ? this._gatewayInfo.queue_depth : 0;
    if (ver && this._gatewayInfo) {
      const intVer = this._gatewayInfo.integration_version || CARD_VERSION;
      const owndVer = this._gatewayInfo.ownd_version;
      ver.textContent = owndVer && owndVer !== "unknown" ? `v${intVer} (OWNd ${owndVer})` : `v${intVer}`;
    }
  }

  async _copyToClipboard(text) {
    if (navigator.clipboard && window.isSecureContext) {
      try {
        await navigator.clipboard.writeText(text);
        return true;
      } catch (err) {
        console.warn("MyHOME Bus Monitor: navigator.clipboard.writeText failed, trying fallback", err);
      }
    }
    try {
      const textArea = document.createElement("textarea");
      textArea.value = text;
      textArea.style.position = "fixed";
      textArea.style.left = "-999999px";
      textArea.style.top = "-999999px";
      document.body.appendChild(textArea);
      textArea.focus();
      textArea.select();
      const success = document.execCommand("copy");
      document.body.removeChild(textArea);
      return success;
    } catch (e) {
      console.error("MyHOME Bus Monitor: clipboard copy failed", e);
      return false;
    }
  }

  _generateDiagnosticPayload() {
    const haVersion =
      (this._hass && this._hass.config && this._hass.config.version) ||
      (this.hass && this.hass.config && this.hass.config.version) ||
      "Unknown";
    const gw = this._gatewayInfo || {};
    const integrationVersion = gw.integration_version || CARD_VERSION;
    const owndVersion = gw.ownd_version || "Unknown";
    const timestamp = new Date().toISOString();

    const model = gw.model || "Unknown";
    const manufacturer = gw.manufacturer || "BTicino";
    const firmware = gw.firmware || "Unknown";
    const macPrefix =
      gw.mac_prefix ||
      (this._config && this._config.mac ? this._config.mac.substring(0, 8) : "Unknown");

    // The bundle is meant to be pasted into a public issue: name the transport,
    // never the address (LAN IP / port, serial device path) or the browser.
    let conn = "Unknown";
    if (gw.serial_port) {
      conn = "USB / Serial";
    } else if (gw.host) {
      conn = "Ethernet TCP";
    }

    const queuePacing = gw.queue_pacing != null ? `${gw.queue_pacing}s` : "0.0s";
    const workerCount = gw.worker_count != null ? gw.worker_count : 1;
    const queueDepth = gw.queue_depth != null ? gw.queue_depth : 0;
    const isConnected =
      gw.is_connected != null ? (gw.is_connected ? "Connected" : "Disconnected") : "Unknown";

    const totalRx = this._stats && this._stats.total_rx != null ? this._stats.total_rx : 0;
    const totalTx = this._stats && this._stats.total_tx != null ? this._stats.total_tx : 0;
    const captured = this._stats && this._stats.captured != null ? this._stats.captured : this._frames.length;
    const bufferDepth = `${this._frames.length} / ${this._maxDisplayFrames}`;

    const activeFilter = [];
    if (this._filterWho !== "all") activeFilter.push(`WHO=${this._filterWho}`);
    if (this._filterWhere) activeFilter.push(`WHERE=${this._filterWhere}`);
    if (this._filterDir !== "all") activeFilter.push(`DIR=${this._filterDir.toUpperCase()}`);
    const filterDesc = activeFilter.length > 0 ? activeFilter.join(", ") : "None (All frames)";

    const visible = this._visibleFrames();
    const captureKind = this._captureKind();
    const frameLines = visible.map((f) => {
      const timeStr = this._formatFrameTime(f);
      const dir = (f.direction || "rx").toUpperCase();
      return `[${timeStr}] [${dir}] ${f.raw || ""}`;
    });

    const framesText =
      frameLines.length > 0
        ? frameLines.join("\n")
        : "(No bus frames recorded in buffer)";

    return `### MyHOME Diagnostic Bundle

**Environment:**
- **Home Assistant Version:** ${haVersion}
- **Integration Version:** ${integrationVersion}
- **OWNd Protocol Engine:** ${owndVersion}
- **Timestamp:** ${timestamp}

**Active Gateway Configuration:**
- **Model:** ${model} (${manufacturer})
- **Firmware:** ${firmware}
- **Connection:** ${conn}
- **MAC Prefix:** ${macPrefix}
- **Queue Pacing:** ${queuePacing}
- **Worker Count:** ${workerCount}
- **Connection Status:** ${isConnected}

**Buffer Telemetry:**
- **Total RX Frames:** ${totalRx}
- **Total TX Frames:** ${totalTx}
- **Total Captured:** ${captured}
- **Buffer Depth:** ${bufferDepth}
- **Capture Kind:** ${captureKind === "sweep" ? "Bus sweep (device inventory)" : "Passive trace"}
- **Gateway Queue Depth:** ${queueDepth}
- **Active Card Filter:** ${filterDesc}

<details><summary>OpenWebNet Bus Trace</summary>

\`\`\`
${framesText}
\`\`\`
</details>`;
  }

  async _handleSweepBus() {
    const btn = this.shadowRoot.getElementById("btn-sweep");
    const origText = btn ? btn.innerHTML : "🧹 Sweep Bus";
    if (btn) {
      btn.innerHTML = "⏳ Sweeping...";
      btn.disabled = true;
    }

    const banner = this.shadowRoot.getElementById("feedback-banner");
    if (this._bannerTimeout) {
      clearTimeout(this._bannerTimeout);
      this._bannerTimeout = null;
    }

    if (this._hass) {
      try {
        await this._clearBuffer();
        // A stopped trace leaves the stream paused; the sweep replies must be captured.
        if (this._isPaused) this._togglePause();
        await this._hass.callService("myhome", "sweep_bus", {});
        this._lastSweepAt = Date.now() / 1000;
        this._setCaptureMode("sweep");
        if (banner) {
          banner.className = "feedback-banner banner-success";
          banner.innerHTML = `
            <span><strong>🧹 Bus sweep started.</strong> Every subsystem is asked for its status (read-only). Wait a few seconds for the replies, then <em>Export Sweep</em> or <em>Copy Sweep</em>.</span>
          `;
          banner.style.display = "flex";
          this._bannerTimeout = setTimeout(() => {
            if (banner) banner.style.display = "none";
          }, 6000);
        }
      } catch (err) {
        console.error("MyHOME Bus Monitor: Error triggering sweep_bus service", err);
        if (banner) {
          banner.className = "feedback-banner banner-warning";
          banner.innerHTML = `
            <span><strong>⚠️ Bus sweep failed:</strong> ${this._escapeHtml(err.message || String(err))}</span>
          `;
          banner.style.display = "flex";
          this._bannerTimeout = setTimeout(() => {
            if (banner) banner.style.display = "none";
          }, 6000);
        }
      }
    }

    setTimeout(() => {
      if (btn) {
        btn.innerHTML = origText;
        btn.disabled = false;
      }
    }, 3000);
  }

  _visibleFrames() {
    // What the user sees: the ring buffer with the active WHO / WHERE / direction filters applied.
    return this._frames.filter((f) => this._matchesFilter(f));
  }

  _captureKind() {
    // The kind is what the user chose: Start Trace / Clear -> "trace", Sweep Bus -> "sweep".
    return this._captureMode === "sweep" ? "sweep" : "trace";
  }

  _captureFilters() {
    return {
      who: this._filterWho === "all" ? null : String(this._filterWho),
      where: this._filterWhere ? this._filterWhere.trim() : null,
      direction: this._filterDir === "all" ? null : this._filterDir,
    };
  }

  _captureFilterSlug() {
    const parts = [];
    if (this._filterWho !== "all") parts.push(`who${this._filterWho}`);
    if (this._filterDir !== "all") parts.push(this._filterDir);
    if (this._filterWhere) parts.push(this._filterWhere.trim().replace(/[^a-z0-9]+/gi, "").slice(0, 12).toLowerCase());
    return parts.length ? parts.join("-") : "all";
  }

  _refreshExportLabel() {
    const root = this.shadowRoot;
    if (!root) return;
    const kind = this._captureKind();
    const btn = root.getElementById("btn-export");
    if (btn) btn.innerHTML = kind === "sweep" ? "💾 Export Sweep" : "💾 Export Trace";
    const reportBtn = root.getElementById("btn-report");
    if (reportBtn) reportBtn.innerHTML = kind === "sweep" ? "📋 Copy Sweep" : "📋 Copy Trace";
  }

  async _handleExportTrace() {
    const btn = this.shadowRoot.getElementById("btn-export");
    const origText = btn ? btn.innerHTML : "💾 Export Trace";
    if (btn) btn.innerHTML = "⏳ Exporting...";

    if (this._hass) {
      try {
        const infoRes = await this._hass.callWS(
          this._wsPayload("myhome/bus_monitor/info")
        );
        if (infoRes) {
          if (infoRes.gateway) this._gatewayInfo = infoRes.gateway;
          if (infoRes.stats) {
            this._stats = Object.assign({}, this._stats, infoRes.stats);
            this._updateStats();
          }
        }
      } catch (err) {
        console.debug("MyHOME Bus Monitor: Falling back to cached gateway telemetry", err);
      }
    }

    const haVersion =
      (this._hass && this._hass.config && this._hass.config.version) ||
      (this.hass && this.hass.config && this.hass.config.version) ||
      "";
    const integrationVersion = (this._gatewayInfo && this._gatewayInfo.integration_version) || CARD_VERSION;
    const owndVersion = (this._gatewayInfo && this._gatewayInfo.ownd_version) || "Unknown";

    const timestampIso = new Date().toISOString();
    const timestampFile = timestampIso.replace(/[:.]/g, "-").slice(0, 19);

    const frames = this._visibleFrames();
    const kind = this._captureKind();
    const firstTs = frames.length ? frames[0].timestamp : null;
    const lastTs = frames.length ? frames[frames.length - 1].timestamp : null;
    const modelSlug = String((this._gatewayInfo && this._gatewayInfo.model) || "gateway").replace(/[^a-z0-9]+/gi, "");

    const tracePayload = {
      capture: {
        kind,
        started_at: kind === "sweep"
          ? (this._lastSweepAt ? new Date(this._lastSweepAt * 1000).toISOString() : null)
          : (this._traceStartedAt ? new Date(this._traceStartedAt * 1000).toISOString() : null),
        filters: this._captureFilters(),
        window: {
          first: firstTs != null ? new Date(firstTs * 1000).toISOString() : null,
          last: lastTs != null ? new Date(lastTs * 1000).toISOString() : null,
          frames: frames.length,
          buffer_frames: this._frames.length,
          buffer_depth: this._maxDisplayFrames,
          // the ring buffer had already wrapped: the true start of a sequence may be missing
          truncated: this._frames.length >= this._maxDisplayFrames,
        },
      },
      environment: {
        home_assistant_version: haVersion,
        integration_version: integrationVersion,
        ownd_version: owndVersion,
        exported_at: timestampIso,
      },
      gateway: {
        model: (this._gatewayInfo && this._gatewayInfo.model) || "Unknown",
        manufacturer: (this._gatewayInfo && this._gatewayInfo.manufacturer) || "BTicino",
        firmware: (this._gatewayInfo && this._gatewayInfo.firmware) || "Unknown",
        mac_prefix: (this._gatewayInfo && this._gatewayInfo.mac_prefix) || "Unknown",
        connection_type: (this._gatewayInfo && this._gatewayInfo.connection_type) || "tcp",
        queue_pacing: (this._gatewayInfo && this._gatewayInfo.queue_pacing) || "standard",
        is_connected: (this._gatewayInfo && this._gatewayInfo.is_connected) !== false,
        // How the model label was established (ssdp / manual / serial / who13) and the
        // WHO=13 evidence behind it - so a trace never hides a mislabelled gateway.
        identification: (this._gatewayInfo && this._gatewayInfo.identification) || null,
      },
      telemetry: {
        total_rx: this._stats.total_rx,
        total_tx: this._stats.total_tx,
        captured_in_buffer: this._frames.length,
        buffer_depth: this._maxDisplayFrames,
        queue_depth: this._stats.queue_depth || 0,
      },
      frames: frames.map((f) => ({
        timestamp: f.timestamp,
        iso_time: f.iso_time || null,
        direction: f.direction || null,
        raw: f.raw,
        who: f.who,
        what: f.what,
        where: f.where,
        dimension: f.dimension != null ? f.dimension : null,
        is_ack: !!f.is_ack,
        is_nack: !!f.is_nack,
      })),
    };

    const blob = new Blob([JSON.stringify(tracePayload, null, 2)], { type: "application/json" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    const fileName = `myhome_${kind}_${modelSlug}_${this._captureFilterSlug()}_${timestampFile}.json`;
    a.href = url;
    a.download = fileName;
    document.body.appendChild(a);
    a.click();
    document.body.removeChild(a);
    URL.revokeObjectURL(url);

    const banner = this.shadowRoot.getElementById("feedback-banner");
    if (this._bannerTimeout) {
      clearTimeout(this._bannerTimeout);
      this._bannerTimeout = null;
    }

    if (banner) {
      banner.className = "feedback-banner banner-success";
      banner.innerHTML = `
        <div style="display: flex; flex-direction: column; gap: 4px;">
          <span><strong>✅ Exported ${kind === "sweep" ? "bus sweep" : "trace"}:</strong> <code>${fileName}</code></span>
          <span style="font-size: 0.75rem; opacity: 0.9;">${frames.length} of ${this._frames.length} buffered frames (active filters applied). Attach this file directly to GitHub Discussion #291 or a bug report.</span>
        </div>
        <a href="https://github.com/orgs/OpenWebNet-HA/discussions/291" target="_blank" rel="noopener noreferrer" class="banner-link">Open Discussion #291 ↗</a>
      `;
      banner.style.display = "flex";
      this._bannerTimeout = setTimeout(() => {
        if (banner) banner.style.display = "none";
      }, 9000);
    }

    if (btn) {
      btn.innerHTML = "✅ Exported!";
      setTimeout(() => {
        if (btn) btn.innerHTML = origText;
        this._refreshExportLabel();
      }, 3000);
    }
  }

  async _handleReportIssue() {
    const btn = this.shadowRoot.getElementById("btn-report");
    const origText = btn ? btn.innerHTML : "📋 Copy Trace";
    if (btn) btn.innerHTML = "⏳ Generating...";

    // Try fetching the freshest gateway & buffer telemetry from backend
    if (this._hass) {
      try {
        const infoRes = await this._hass.callWS(
          this._wsPayload("myhome/bus_monitor/info")
        );
        if (infoRes) {
          if (infoRes.gateway) this._gatewayInfo = infoRes.gateway;
          if (infoRes.stats) {
            this._stats = Object.assign({}, this._stats, infoRes.stats);
            this._updateStats();
          }
        }
      } catch (err) {
        // Continue with available state if backend call fails
        console.debug("MyHOME Bus Monitor: Falling back to cached gateway telemetry", err);
      }
    }

    const payload = this._generateDiagnosticPayload();
    const copied = await this._copyToClipboard(payload);

    const haVersion =
      (this._hass && this._hass.config && this._hass.config.version) ||
      (this.hass && this.hass.config && this.hass.config.version) ||
      "";
    const integrationVersion = (this._gatewayInfo && this._gatewayInfo.integration_version) || CARD_VERSION;
    const owndVersion = (this._gatewayInfo && this._gatewayInfo.ownd_version) || "Unknown";

    const issueUrl = `https://github.com/OpenWebNet-HA/MyHOME/issues/new?template=bug_report.yml&ha_version=${encodeURIComponent(haVersion)}&integration_version=${encodeURIComponent(integrationVersion)}&ownd_version=${encodeURIComponent(owndVersion)}`;

    const banner = this.shadowRoot.getElementById("feedback-banner");
    if (this._bannerTimeout) {
      clearTimeout(this._bannerTimeout);
      this._bannerTimeout = null;
    }

    if (banner) {
      if (copied) {
        banner.className = "feedback-banner banner-success";
        banner.innerHTML = `
          <div style="display: flex; flex-direction: column; gap: 4px;">
            <span><strong>✅ Copied diagnostic payload to clipboard!</strong> Opening GitHub issue form...</span>
            <span style="font-size: 0.75rem; opacity: 0.9;">Paste the clipboard contents directly into the <em>Bus Monitor Diagnostic Payload / Bus Trace</em> field.</span>
            <span style="font-size: 0.72rem; opacity: 0.85;">💡 <em>Tip: Also download and drag &amp; drop your HA log (Settings &rarr; System &rarr; Logs &rarr; Download full log) into the issue!</em></span>
          </div>
          <a href="${issueUrl}" target="_blank" rel="noopener noreferrer" class="banner-link">Open GitHub Form ↗</a>
        `;
      } else {
        banner.className = "feedback-banner banner-warning";
        banner.innerHTML = `
          <div style="display: flex; flex-direction: column; gap: 4px;">
            <span><strong>⚠️ Clipboard write failed.</strong> Diagnostic payload printed to browser console.</span>
            <span style="font-size: 0.75rem; opacity: 0.9;">Copy the payload from your browser console (F12) and open the issue form below.</span>
          </div>
          <a href="${issueUrl}" target="_blank" rel="noopener noreferrer" class="banner-link">Open GitHub Form ↗</a>
        `;
        console.log("MyHOME Diagnostic Payload:\n", payload);
      }
      banner.style.display = "flex";
      this._bannerTimeout = setTimeout(() => {
        if (banner) banner.style.display = "none";
      }, 9000);
    }

    if (btn) {
      btn.innerHTML = copied ? "✅ Copied & Opened!" : "⚠️ Check Console";
      // label follows the capture kind again once the confirmation fades
      setTimeout(() => {
        if (btn) btn.innerHTML = origText;
        this._refreshExportLabel();
      }, 3000);
    }

    // Automatically open GitHub issue form in a new tab
    try {
      window.open(issueUrl, "_blank", "noopener,noreferrer");
    } catch (e) {
      console.warn("MyHOME Bus Monitor: window.open blocked by browser", e);
    }
  }

  _updateFrameList() {
    const container = this.shadowRoot.getElementById("stream");
    if (!container) return;
    const matching = this._frames.filter((f) => this._matchesFilter(f));
    if (matching.length === 0) {
      this._updatePlaceholder(this._frames.length > 0);
      return;
    }
    container.innerHTML = "";
    for (const frame of matching) {
      container.appendChild(this._createFrameNode(frame));
    }
    container.scrollTop = container.scrollHeight;
  }

  _appendFrameElement(frame) {
    if (!this._matchesFilter(frame)) return;
    const container = this.shadowRoot.getElementById("stream");
    if (!container) return;
    const placeholder = container.querySelector(".placeholder-msg");
    if (placeholder) {
      container.innerHTML = "";
    }
    container.appendChild(this._createFrameNode(frame));
    while (container.children.length > this._maxDisplayFrames) {
      container.removeChild(container.firstElementChild);
    }
    container.scrollTop = container.scrollHeight;
  }

  _escapeHtml(text) {
    return String(text || "")
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;")
      .replace(/'/g, "&#039;");
  }

  _createFrameNode(frame) {
    const div = document.createElement("div");
    div.className = "frame-line";

    const timeStr = this._formatFrameTime(frame);
    const dirClass = frame.direction === "rx" ? "dir-rx" : "dir-tx";
    const dirLabel = frame.direction ? frame.direction.toUpperCase() : "RX";
    const whoClass = this._getWhoClass(frame.who);
    const whoLabel = this._formatWho(frame.who);
    const whoCustomStyle = this._getWhoBadgeStyle(frame.who);

    let rawClass = "col-raw";
    if (frame.is_ack) rawClass += " raw-ack";
    if (frame.is_nack) rawClass += " raw-nack";

    div.innerHTML = `
      <span class="col-time">${timeStr}</span>
      <span class="col-dir ${dirClass}">${dirLabel}</span>
      <span class="col-who ${whoClass}" ${whoCustomStyle}>${this._escapeHtml(whoLabel)}</span>
      <span class="${rawClass}">${this._escapeHtml(frame.raw)}</span>
    `;
    return div;
  }

  getCardSize() {
    return 6;
  }
}

window.MyHomeBusCard = MyHomeBusCard;

function registerCardElements() {
  const ce = (typeof window !== "undefined" && window.customElements) || customElements;
  if (!ce) return;
  if (!ce.get("myhome-openwebnet-bus-monitor")) {
    try {
      ce.define("myhome-openwebnet-bus-monitor", MyHomeBusCard);
    } catch (e) {
      // Ignore if already registered in active scope
    }
  }
  if (!ce.get("myhome-bus-card")) {
    try {
      customElements.define("myhome-bus-card", class extends MyHomeBusCard {});
    } catch (e) {
      // Ignore if already registered in active scope
    }
  }
}

// 1. Initial immediate registration
registerCardElements();

// 2. Active self-healing watchdog for scoped-custom-element-registry replacements
if (typeof window !== "undefined") {
  let lastRegistry = window.customElements;
  const watcher = setInterval(() => {
    if (
      window.customElements !== lastRegistry ||
      (window.customElements && !window.customElements.get("myhome-openwebnet-bus-monitor"))
    ) {
      lastRegistry = window.customElements;
      registerCardElements();
    }
  }, 100);
  setTimeout(() => clearInterval(watcher), 45000);
}

console.info(
  `%c MYHOME-BUS-CARD %c v${CARD_VERSION} `,
  "background:#03a9f4;color:#fff;font-weight:bold;padding:2px 4px;border-radius:3px 0 0 3px;",
  "background:#263238;color:#fff;padding:2px 4px;border-radius:0 3px 3px 0;"
);

window.customCards = (window.customCards || []).filter((c) => c.type !== "myhome-bus-card");

const cardDefinition = {
  type: "myhome-openwebnet-bus-monitor",
  name: "MyHOME OpenWebNet Bus Monitor",
  description: "Real-time BTicino / Legrand SCS OpenWebNet bus traffic stream, packet inspector, and diagnostic frame sender.",
  preview: true,
};

// Guarantee registration whenever Lovelace card picker accesses card.type
Object.defineProperty(cardDefinition, "type", {
  get() {
    registerCardElements();
    return "myhome-openwebnet-bus-monitor";
  },
  set(val) {
    // allow assignment if needed
  },
  enumerable: true,
  configurable: true,
});

const existingIndex = window.customCards.findIndex(
  (c) => c && c.type === "myhome-openwebnet-bus-monitor"
);
if (existingIndex >= 0) {
  window.customCards[existingIndex] = cardDefinition;
} else {
  window.customCards.push(cardDefinition);
}

