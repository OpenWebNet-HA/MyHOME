/**
 * MyHOME Bus Monitor — Custom Lovelace Card
 * 
 * Provides real-time streaming, filtering, and diagnostic frame transmission
 * for BTicino / Legrand MyHOME SCS bus systems via OpenWebNet.
 */

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
          justify-content: space-between;
          align-items: center;
          margin-bottom: 12px;
        }
        .title {
          font-size: 1.15rem;
          font-weight: 600;
          color: var(--primary-text-color);
          display: flex;
          align-items: center;
          gap: 8px;
        }
        .badge {
          font-size: 0.75rem;
          padding: 2px 8px;
          border-radius: 12px;
          font-weight: 500;
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
        .actions {
          display: flex;
          gap: 6px;
          align-items: center;
          flex-wrap: wrap;
        }
        .btn-sweep {
          background: #1976d2;
          color: #fff;
          font-weight: 600;
          font-size: 0.8rem;
          display: inline-flex;
          align-items: center;
          gap: 4px;
        }
        .btn-sweep:hover {
          background: #1565c0;
        }
        .btn-export {
          background: #2e7d32;
          color: #fff;
          font-weight: 600;
          font-size: 0.8rem;
          display: inline-flex;
          align-items: center;
          gap: 4px;
        }
        .btn-export:hover {
          background: #1b5e20;
        }
        .btn-report {
          background: #ff9800;
          color: #fff;
          font-weight: 600;
          font-size: 0.8rem;
          display: inline-flex;
          align-items: center;
          gap: 4px;
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
          <div class="title">
            <span>📡 ${this._config.title}</span>
            <span id="badge" class="badge badge-connecting">CONNECTING...</span>
          </div>
          <div class="actions">
            <button id="btn-sweep" class="btn-sweep" title="Safely query all bus subsystems to discover all devices and populate trace buffer">
              🧹 Sweep Bus
            </button>
            <button id="btn-export" class="btn-export" title="Download sanitized gateway trace JSON file">
              💾 Export Trace
            </button>
            <button id="btn-report" class="btn-report" title="Copy diagnostic markdown to clipboard and open GitHub issue form">
              📋 Copy Trace
            </button>
            <button id="btn-pause" class="btn-secondary">Pause</button>
            <button id="btn-clear" class="btn-secondary">Clear</button>
          </div>
        </div>

        <div id="feedback-banner" class="feedback-banner"></div>

        <div class="stats-bar">
          <div>Buffered: <span id="stat-buffer" class="stat-val">0</span>/<span id="stat-max">${this._maxDisplayFrames}</span></div>
          <div>RX: <span id="stat-rx" class="stat-val">0</span></div>
          <div>TX: <span id="stat-tx" class="stat-val">0</span></div>
          <div>Queue: <span id="stat-queue" class="stat-val">0</span></div>
          <div style="margin-left: auto; font-size: 0.75rem; opacity: 0.85;">MyHOME <span id="stat-version" class="stat-val">v2.0.0b12</span></div>
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

        <div class="sender-bar">
          <input type="text" id="send-frame" placeholder="Transmit frame (e.g. *1*1*12##)..." />
          <button id="btn-send">Send</button>
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
    root.getElementById("btn-sweep")?.addEventListener("click", () => this._handleSweepBus());
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

  _togglePause() {
    this._isPaused = !this._isPaused;
    const btn = this.shadowRoot.getElementById("btn-pause");
    if (btn) {
      btn.textContent = this._isPaused ? "Resume" : "Pause";
    }
    this._updateBadge();
  }

  async _clearBuffer() {
    this._frames = [];
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
      const intVer = this._gatewayInfo.integration_version || "2.0.0b8";
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
    const integrationVersion = gw.integration_version || "2.0.0b8";
    const owndVersion = gw.ownd_version || "Unknown";
    const userAgent = (typeof navigator !== "undefined" && navigator.userAgent) ? navigator.userAgent : "Unknown";
    const timestamp = new Date().toISOString();

    const model = gw.model || "Unknown";
    const manufacturer = gw.manufacturer || "BTicino";
    const firmware = gw.firmware || "Unknown";
    const macPrefix =
      gw.mac_prefix ||
      (this._config && this._config.mac ? this._config.mac.substring(0, 8) : "Unknown");

    let conn = "Unknown";
    if (gw.serial_port) {
      conn = `USB / Serial (${gw.serial_port})`;
    } else if (gw.host) {
      conn = `Ethernet TCP (${gw.host}:${gw.port || 20000})`;
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

    const frameLines = this._frames.map((f) => {
      let timeStr = "";
      if (f.iso_time && f.iso_time.includes("T")) {
        timeStr = f.iso_time.split("T")[1].substring(0, 12);
      } else if (f.timestamp) {
        timeStr = new Date(f.timestamp * 1000).toISOString().split("T")[1].substring(0, 12);
      }
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
- **Browser / User Agent:** ${userAgent}
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
        await this._hass.callService("myhome", "sweep_bus", {});
        if (banner) {
          banner.className = "feedback-banner banner-success";
          banner.innerHTML = `
            <span><strong>🧹 Bus sweep initiated!</strong> Querying all lighting, automation, heating, and diagnostic states across the bus.</span>
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
    const integrationVersion = (this._gatewayInfo && this._gatewayInfo.integration_version) || "2.0.0b12";
    const owndVersion = (this._gatewayInfo && this._gatewayInfo.ownd_version) || "Unknown";

    const timestampIso = new Date().toISOString();
    const timestampFile = timestampIso.replace(/[:.]/g, "-").slice(0, 19);

    const tracePayload = {
      environment: {
        home_assistant_version: haVersion,
        integration_version: integrationVersion,
        ownd_version: owndVersion,
        exported_at: timestampIso,
        user_agent: navigator.userAgent,
      },
      gateway: {
        model: (this._gatewayInfo && this._gatewayInfo.model) || "Unknown",
        manufacturer: (this._gatewayInfo && this._gatewayInfo.manufacturer) || "BTicino",
        firmware: (this._gatewayInfo && this._gatewayInfo.firmware) || "Unknown",
        mac_prefix: (this._gatewayInfo && this._gatewayInfo.mac_prefix) || "Unknown",
        connection_type: (this._gatewayInfo && this._gatewayInfo.connection_type) || "tcp",
        queue_pacing: (this._gatewayInfo && this._gatewayInfo.queue_pacing) || "standard",
        is_connected: (this._gatewayInfo && this._gatewayInfo.is_connected) !== false,
      },
      telemetry: {
        total_rx: this._stats.total_rx,
        total_tx: this._stats.total_tx,
        captured_in_buffer: this._frames.length,
        buffer_depth: this._maxDisplayFrames,
        queue_depth: this._stats.queue_depth || 0,
      },
      frames: this._frames.map((f) => ({
        timestamp: f.timestamp,
        direction: f.dir,
        raw: f.raw,
        who: f.who,
        what: f.what,
        where: f.where,
        description: f.desc || "",
      })),
    };

    const blob = new Blob([JSON.stringify(tracePayload, null, 2)], { type: "application/json" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    const fileName = `myhome_gateway_trace_${timestampFile}.json`;
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
          <span><strong>✅ Exported trace:</strong> <code>${fileName}</code></span>
          <span style="font-size: 0.75rem; opacity: 0.9;">Attach this file directly to GitHub Discussion #291 or a bug report.</span>
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
    const integrationVersion = (this._gatewayInfo && this._gatewayInfo.integration_version) || "2.0.0b12";
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
      setTimeout(() => {
        if (btn) btn.innerHTML = origText;
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

    const timeStr = frame.iso_time && frame.iso_time.includes("T")
      ? frame.iso_time.split("T")[1].substring(0, 12)
      : (frame.timestamp ? new Date(frame.timestamp * 1000).toISOString().split("T")[1].substring(0, 12) : "");
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
  "%c MYHOME-BUS-CARD %c v2.0.0b8 ",
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

