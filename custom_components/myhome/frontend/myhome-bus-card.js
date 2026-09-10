/**
 * MyHOME Bus Monitor — Custom Lovelace Card
 * 
 * Provides real-time streaming, filtering, and diagnostic frame transmission
 * for BTicino / Legrand MyHOME SCS bus systems via OpenWebNet.
 */

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

  _onNewFrame(frame) {
    if (this._connectionStatus !== "connected" && !this._isPaused) {
      this._updateConnectionStatus("connected");
    }
    if (this._isPaused) return;

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
    if (this._filterDir !== "all" && frame.direction !== this._filterDir) {
      return false;
    }
    if (this._filterWho !== "all" && String(frame.who) !== String(this._filterWho)) {
      return false;
    }
    if (this._filterWhere && !String(frame.where || "").toLowerCase().includes(this._filterWhere.toLowerCase())) {
      return false;
    }
    return true;
  }

  _formatWho(who) {
    const map = {
      "1": "Light/Switch",
      "2": "Automation",
      "4": "Heating",
      "15": "CEN",
      "16": "Sound",
      "18": "Energy",
      "25": "CEN+/Sec",
    };
    return map[String(who)] || (who ? `WHO=${who}` : "Sys");
  }

  _getWhoClass(who) {
    switch (String(who)) {
      case "1": return "who-light";
      case "2": return "who-cover";
      case "4": return "who-thermo";
      case "15":
      case "25": return "who-cen";
      case "16": return "who-sound";
      case "18": return "who-energy";
      default: return "who-default";
    }
  }

  _render() {
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
        .who-cen { background: #4a148c; color: #ce93d8; }
        .who-sound { background: #004d40; color: #80cbc4; }
        .who-energy { background: #006064; color: #80deea; }
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
            <button id="btn-report" class="btn-report" title="Bundle system diagnostics & bus trace to clipboard, then open GitHub issue form">
              📋 Report Issue / Copy Trace
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
        </div>

        <div class="controls">
          <select id="filter-who">
            <option value="all">All Subsystems</option>
            <option value="1">Lighting / Switches (WHO=1)</option>
            <option value="2">Automation / Shutters (WHO=2)</option>
            <option value="4">Heating / Thermoregulation (WHO=4)</option>
            <option value="15">CEN Pushbuttons (WHO=15)</option>
            <option value="16">Sound System (WHO=16)</option>
            <option value="18">Energy Management (WHO=18)</option>
            <option value="25">CEN+ / Security (WHO=25)</option>
          </select>

          <input type="text" id="filter-where" placeholder="Filter WHERE (e.g. 12)..." style="width: 140px;" />

          <select id="filter-dir">
            <option value="all">All Directions</option>
            <option value="rx">RX (Bus Traffic)</option>
            <option value="tx">TX (Commands)</option>
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
    root.getElementById("btn-report").addEventListener("click", () => this._handleReportIssue());
    root.getElementById("btn-pause").addEventListener("click", () => this._togglePause());
    root.getElementById("btn-clear").addEventListener("click", () => this._clearBuffer());
    root.getElementById("filter-who").addEventListener("change", (e) => {
      this._filterWho = e.target.value;
      this._updateFrameList();
    });
    root.getElementById("filter-where").addEventListener("input", (e) => {
      this._filterWhere = e.target.value;
      this._updateFrameList();
    });
    root.getElementById("filter-dir").addEventListener("change", (e) => {
      this._filterDir = e.target.value;
      this._updateFrameList();
    });
    root.getElementById("btn-send").addEventListener("click", () => this._sendCustomFrame());
    root.getElementById("send-frame").addEventListener("keydown", (e) => {
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
    if (buf) buf.textContent = this._frames.length;
    if (max) max.textContent = this._maxDisplayFrames;
    if (rx) rx.textContent = this._stats.total_rx;
    if (tx) tx.textContent = this._stats.total_tx;
    if (queue) queue.textContent = (this._gatewayInfo && this._gatewayInfo.queue_depth != null) ? this._gatewayInfo.queue_depth : 0;
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
    const integrationVersion = gw.integration_version || "2.0.0b2";
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

  async _handleReportIssue() {
    const btn = this.shadowRoot.getElementById("btn-report");
    const origText = btn ? btn.innerHTML : "📋 Report Issue / Copy Trace";
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
    const integrationVersion = (this._gatewayInfo && this._gatewayInfo.integration_version) || "2.0.0b2";

    const issueUrl = `https://github.com/OpenWebNet-HA/MyHOME/issues/new?template=bug_report.yml&ha_version=${encodeURIComponent(haVersion)}&integration_version=${encodeURIComponent(integrationVersion)}`;

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

    let rawClass = "col-raw";
    if (frame.is_ack) rawClass += " raw-ack";
    if (frame.is_nack) rawClass += " raw-nack";

    div.innerHTML = `
      <span class="col-time">${timeStr}</span>
      <span class="col-dir ${dirClass}">${dirLabel}</span>
      <span class="col-who ${whoClass}">${whoLabel}</span>
      <span class="${rawClass}">${this._escapeHtml(frame.raw)}</span>
    `;
    return div;
  }

  getCardSize() {
    return 6;
  }
}

if (!customElements.get("myhome-openwebnet-bus-monitor")) {
  customElements.define("myhome-openwebnet-bus-monitor", MyHomeBusCard);
}

// Backward-compatible alias for existing dashboards
if (!customElements.get("myhome-bus-card")) {
  customElements.define("myhome-bus-card", class extends MyHomeBusCard {});
}

window.customCards = window.customCards || [];
if (!window.customCards.some((c) => c.type === "myhome-openwebnet-bus-monitor")) {
  window.customCards.push({
    type: "myhome-openwebnet-bus-monitor",
    name: "MyHOME OpenWebNet Bus Monitor",
    description: "Real-time BTicino / Legrand SCS OpenWebNet bus traffic stream, packet inspector, and diagnostic frame sender.",
    preview: true,
  });
}
if (!window.customCards.some((c) => c.type === "myhome-bus-card")) {
  window.customCards.push({
    type: "myhome-bus-card",
    name: "MyHOME Bus Card (Alias)",
    description: "Real-time BTicino / Legrand SCS OpenWebNet bus monitor (alias for myhome-openwebnet-bus-monitor).",
    preview: true,
  });
}
