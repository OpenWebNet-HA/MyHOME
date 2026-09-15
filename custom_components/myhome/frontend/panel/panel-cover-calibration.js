/** Socket-owned guided or automatic measurement; the browser never computes or saves timings. */
const url = new URL("panel-dom.js", import.meta.url);
url.search = new URL(import.meta.url).search;
const { escapeHtml: esc } = await import(url.href);

export class CoverCalibration {
  constructor() { this._generation = 0; }

  close() {
    this._generation++;
    clearInterval(this._heartbeat);
    this._heartbeat = null;
    const state = this._state;
    if (state && !["saved", "cancelled"].includes(state.phase)) {
      this._context.hass.callWS({ type: "myhome/cover_calibration/action", entry_id: state.entry_id,
        session_id: state.session_id, action: "cancel" }).catch(() => {});
    }
    Promise.resolve(this._unsubscribe?.()).catch(() => {});
    this._unsubscribe = null;
    this._state = null;
  }

  async open(context) {
    this.close();
    this._context = context;
    this._lost = false;
    this._busy = false;
    const generation = this._generation;
    const { host, hass, entity, revision, t } = context;
    const automatic = context.mode === "automatic";
    host.innerHTML = `<div class="cal-panel" data-phase="loading">
      <h3 class="cal-title"><ha-icon icon="mdi:timer-outline" aria-hidden="true"></ha-icon>${esc(t(automatic ? "calAutomatic" : "calGuided"))}</h3>
      <ol class="cal-steps" aria-hidden="true" ${automatic ? "hidden" : ""}>
        <li data-step="opening"><span class="cal-step-index">1</span><span>${esc(t("calStepOpening"))}</span></li>
        <li data-step="closing"><span class="cal-step-index">2</span><span>${esc(t("calStepClosing"))}</span></li>
        <li data-step="review"><span class="cal-step-index">3</span><span>${esc(t("calStepReview"))}</span></li>
      </ol>
      <p class="muted cal-help">${esc(t(automatic ? "calAutomaticHelp" : "calHelp"))}</p>
      ${context.entity_ids ? `<p class="notice">${esc(t("calBatchHelp"))}</p><ol id="cal-targets"></ol>` : ""}
      <div class="cal-status">
        <p id="cal-phase" role="status">${esc(t("loading"))}</p>
        <p id="cal-elapsed" class="cal-elapsed"></p>
      </div>
      <p id="cal-stop-status" class="notice" hidden>${esc(t("calStopRequested"))}</p>
      <p id="cal-reason" class="error" role="alert" hidden></p>
      <div class="actions cal-actions">
        <button type="button" class="primary" data-cal-action="run" hidden>${esc(t("calAutomaticStart"))}</button>
        <button type="button" class="primary" data-cal-action="open" hidden><ha-icon icon="mdi:arrow-up-bold" aria-hidden="true"></ha-icon><span>${esc(t("calOpen"))}</span></button>
        <button type="button" class="primary" data-cal-action="close" hidden><ha-icon icon="mdi:arrow-down-bold" aria-hidden="true"></ha-icon><span>${esc(t("calClose"))}</span></button>
        <button type="button" class="primary" data-cal-action="endpoint" hidden></button>
      </div>
      <form id="cal-save" class="profile-section cal-save" hidden><p id="cal-values" class="cal-values"></p>
        <label ${context.entity_ids ? "hidden" : ""}>${esc(t("profileName"))}<input name="profile_name" required maxlength="64" ${context.entity_ids ? "disabled" : ""}></label>
        <div id="cal-batch-review"></div>
        <button type="submit" class="primary">${esc(t(context.entity_ids ? "calBatchSave" : "calSave"))}</button>
      </form>
      <div class="actions calibration-safety-actions"><button type="button" id="cal-stop" disabled><ha-icon icon="mdi:stop-circle-outline" aria-hidden="true"></ha-icon><span>${esc(t("calStop"))}</span></button>
        <button type="button" id="cal-cancel">${esc(t("calCancel"))}</button></div>
    </div>`;
    for (const button of host.querySelectorAll("[data-cal-action]")) {
      button.onclick = () => this._perform(button.dataset.calAction);
    }
    host.querySelector("#cal-stop").onclick = () => this._perform("stop");
    host.querySelector("#cal-cancel").onclick = () => { this.close(); context.onCancel(); };
    host.querySelector("#cal-save").onsubmit = (event) => {
      event.preventDefault();
      const form = event.currentTarget;
      if (form.reportValidity()) this._perform("save", context.entity_ids
        ? { names: [...form.querySelectorAll("[data-batch-name]")].map((input) => input.value.trim()) }
        : { name: form.elements.profile_name.value.trim() });
    };
    try {
      const unsubscribe = await hass.connection.subscribeMessage((state) => {
        if (!this._current(generation)) return;
        this._accept(state);
      }, context.entity_ids
        ? { type: "myhome/cover_calibration/batch_start", entry_id: entity.entry_id, entity_ids: context.entity_ids, revision }
        : { type: "myhome/cover_calibration/start", entry_id: entity.entry_id, entity_id: entity.entity_id, revision, ...(automatic ? { mode: "automatic" } : {}) });
      if (!this._current(generation)) { Promise.resolve(unsubscribe()).catch(() => {}); return; }
      this._unsubscribe = unsubscribe;
      this._heartbeat = setInterval(() => this._perform("heartbeat"), 5000);
    } catch (error) {
      if (this._current(generation)) this._error(error);
    }
  }

  _current(generation) { return generation === this._generation && this._context.host.isConnected; }

  _accept(state) {
    if (this._state && state.sequence < this._state.sequence) return;
    this._state = state;
    this._render();
    if (state.phase === "saved") {
      this.close();
      this._context.onSaved();
    }
  }

  _render() {
    const { host, t } = this._context;
    const state = this._state;
    host.querySelector(".cal-panel").dataset.phase = state.phase;
    const automatic = state.mode === "automatic";
    host.querySelector("#cal-phase").textContent = automatic && ["starting_open", "starting_close", "opening", "closing", "settling"].includes(state.phase)
      ? `${t("calAutomaticRun")} ${state.run_index + 1}/3 · ${t(`calAutoPhase_${state.phase}`)}`
      : t(`calPhase_${state.phase}`);
    host.querySelector("#cal-elapsed").textContent = state.elapsed == null ? "" : `${t("calElapsed")}: ${state.elapsed} s`;
    host.querySelector("#cal-stop-status").hidden = !state.stop_requested;
    const reason = host.querySelector("#cal-reason");
    if (state.reason) {
      reason.hidden = false;
      reason.textContent = t(`calReason_${state.reason}`);
    }
    for (const action of ["run", "open", "close", "endpoint"]) {
      const button = host.querySelector(`[data-cal-action="${action}"]`);
      button.hidden = action === "run" ? !automatic || state.phase !== "confirm_automatic" : automatic || (action === "open" ? state.phase !== "confirm_closed" : action === "close"
        ? state.phase !== "confirm_open" : !["opening", "closing"].includes(state.phase));
      button.disabled = this._busy || this._lost;
    }
    host.querySelector('[data-cal-action="endpoint"]').textContent = t(state.phase === "opening" ? "calEndpointOpen" : "calEndpointClose");
    host.querySelector("#cal-stop").disabled = ["saved", "cancelled"].includes(state.phase);
    host.querySelector("#cal-save").hidden = state.phase !== "review";
    host.querySelector('#cal-save button').disabled = this._busy || this._lost;
    host.querySelector("#cal-values").textContent = `${t("profileOpeningTime")}: ${state.values.opening_time ?? "—"} · ${t("profileClosingTime")}: ${state.values.closing_time ?? "—"}`;
    host.querySelector("#cal-values").hidden = !!state.batch;
    if (state.batch) {
      host.querySelector("#cal-targets").innerHTML = state.targets.map((item, index) => {
        const result = state.results.find((row) => row.index === index);
        const status = ["interrupted", "cancelled"].includes(state.phase) ? t("calBatchDiscarded") : result ? `${result.values.opening_time} / ${result.values.closing_time} s` : t(index === state.cover_index ? "calBatchCurrent" : "calBatchWaiting");
        return `<li>${esc(item.name)} · ${esc(status)}</li>`;
      }).join("");
      const review = host.querySelector("#cal-batch-review");
      if (state.phase === "review" && !review.children.length) {
        review.innerHTML = state.results.map((result) => `<label>${esc(state.targets[result.index].name)} · ${esc(result.values.opening_time)} / ${esc(result.values.closing_time)} s
          <span class="muted">${esc(t("profileName"))}</span><input data-batch-name="${result.index}" required maxlength="64" value="${esc(state.targets[result.index].name.slice(0, 64))}"></label>`).join("");
      }
    }
  }

  _error(error) {
    const { host, t } = this._context;
    const key = `profileError_${error.code}`;
    const box = host.querySelector("#cal-reason");
    box.textContent = t(key) === key ? t("calConnectionError") : t(key);
    box.hidden = false;
  }

  async _perform(action, extra = {}) {
    if (!this._state || (this._busy && !["stop", "heartbeat"].includes(action))) return;
    const generation = this._generation;
    const { hass } = this._context;
    const state = this._state;
    const ownsBusy = !["heartbeat", "stop"].includes(action);
    if (ownsBusy) this._busy = true;
    this._render();
    try {
      const result = await hass.callWS({ type: "myhome/cover_calibration/action", entry_id: state.entry_id,
        session_id: state.session_id, sequence: state.sequence, action, ...extra });
      if (this._current(generation)) this._accept(result);
    } catch (error) {
      if (!this._current(generation)) return;
      if (action === "heartbeat") this._lost = true;
      this._error(error);
    } finally {
      if (this._current(generation)) { if (ownsBusy) this._busy = false; this._render(); }
    }
  }
}
