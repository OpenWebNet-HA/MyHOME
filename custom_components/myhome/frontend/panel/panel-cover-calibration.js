/** Backend-owned measurement with one attached controller and explicit recovery. */
const url = new URL("panel-dom.js", import.meta.url);
url.search = new URL(import.meta.url).search;
const { escapeHtml: esc } = await import(url.href);
const visualUrl = new URL("panel-calibration-visual.js", import.meta.url);
visualUrl.search = new URL(import.meta.url).search;
const { visualMarkup, renderCalibrationVisual } = await import(visualUrl.href);

export class CoverCalibration {
  constructor() { this._generation = 0; }

  close({ cancel = false } = {}) {
    this._generation++;
    clearInterval(this._heartbeat);
    this._heartbeat = null;
    const state = this._state;
    const operation = state && !["saved", "cancelled"].includes(state.phase)
      ? this._context.hass.callWS({ type: "myhome/cover_calibration/action", entry_id: state.entry_id,
        session_id: state.session_id, ...(state.attachment ? { attachment: state.attachment } : {}),
        action: cancel || !state.recoverable ? "cancel" : "detach" }).catch(() => {}) : Promise.resolve();
    const unsubscribe = this._unsubscribe;
    const done = operation.then(() => unsubscribe?.()).catch(() => {});
    this._unsubscribe = null;
    this._state = null;
    return done;
  }

  async open(context) {
    this.close();
    if (context.resume) context = { ...context, mode: context.resume.mode, direction: context.resume.direction,
      entity_ids: context.resume.batch ? context.resume.targets.map((item) => item.entity_id) : undefined };
    this._context = context;
    this._lost = false;
    this._busy = false;
    this._savePreview = null;
    this._renderedPreview = null;
    const generation = this._generation;
    const { host, hass, entity, revision, t } = context;
    const client_id = globalThis.crypto.getRandomValues(new Uint32Array(4)).join("-");
    const automatic = context.mode === "automatic";
    const quick = context.direction;
    const geometry = context.mode === "geometry";
    host.innerHTML = `<div class="cal-panel" data-phase="loading">
      <h3 class="cal-title"><ha-icon icon="mdi:timer-outline" aria-hidden="true"></ha-icon>${esc(t(geometry ? "calGeometry" : automatic ? "calAutomatic" : "calGuided"))}</h3>
      <ol class="cal-steps" aria-hidden="true" ${automatic || quick || geometry ? "hidden" : ""}>
        <li data-step="opening"><span class="cal-step-index">1</span><span>${esc(t("calStepOpening"))}</span></li>
        <li data-step="closing"><span class="cal-step-index">2</span><span>${esc(t("calStepClosing"))}</span></li>
        <li data-step="review"><span class="cal-step-index">3</span><span>${esc(t("calStepReview"))}</span></li>
      </ol>
      <details class="profile-meta cal-guide"><summary>${esc(t("calVisualGuide"))}</summary>
        <p class="muted cal-help">${esc(t(geometry ? "calGeometryHelp" : quick ? (quick === "opening" ? "calQuickOpeningHelp" : "calQuickClosingHelp") : automatic ? "calAutomaticHelp" : "calHelp"))}</p>
        <p class="muted">${esc(t("calRecoveryHelp"))}</p>
      </details>
      ${context.resume ? `<p>${esc(context.resume.entity_id)}</p>` : ""}
      ${context.entity_ids ? `<p class="notice">${esc(t("calBatchHelp"))}</p><ol id="cal-targets"></ol>` : ""}
      <div class="cal-status">
        ${visualMarkup()}
        <p id="cal-phase" role="status">${esc(t("loading"))}</p>
        <p id="cal-elapsed" class="cal-elapsed"></p>
      </div>
      <p id="cal-stop-status" class="notice" hidden>${esc(t("calStopRequested"))}</p>
      <p id="cal-reason" class="error" role="alert" hidden></p>
      <button type="button" id="cal-reconnect" hidden>${esc(t("calResume"))}</button>
      <div class="actions cal-actions">
        <button type="button" class="primary" data-cal-action="run" hidden>${esc(t("calAutomaticStart"))}</button>
        <button type="button" class="primary" data-cal-action="open" hidden><ha-icon icon="mdi:arrow-up-bold" aria-hidden="true"></ha-icon><span>${esc(t("calOpen"))}</span></button>
        <button type="button" class="primary" data-cal-action="close" hidden><ha-icon icon="mdi:arrow-down-bold" aria-hidden="true"></ha-icon><span>${esc(t("calClose"))}</span></button>
        <button type="button" class="primary" data-cal-action="endpoint" hidden></button>
        <button type="button" class="primary" data-cal-action="next" hidden>${esc(t("calGeometryStart"))}</button>
        <button type="button" class="primary" data-cal-action="lift" hidden>${esc(t("calLift"))}</button>
      </div>
      <form id="cal-reading" hidden>
        <label><span id="cal-reading-label"></span><input name="reading_cm" type="number" step="any" min="0" max="10000" required inputmode="decimal"></label>
        <p id="cal-expected" class="muted"></p>
        <button type="submit" class="primary">${esc(t("calReadingAccept"))}</button>
      </form>
      <button type="button" id="cal-repeat" hidden>${esc(t("calRepeat"))}</button>
      <form id="cal-save" class="profile-section cal-save" hidden><p id="cal-values" class="cal-values"></p>
        <label ${context.entity_ids ? "hidden" : ""}>${esc(t("calSaveDestination"))}<select id="cal-save-mode">
          <option value="new">${esc(t("calSaveNew"))}</option><option value="cover">${esc(t("calSaveCover"))}</option><option value="shared">${esc(t("calSaveShared"))}</option>
        </select></label>
        <label id="cal-name-label" ${context.entity_ids ? "hidden" : ""}>${esc(t("profileName"))}<input name="profile_name" required maxlength="64" ${context.entity_ids ? "disabled" : ""}></label>
        <div id="cal-batch-review"></div>
        <p id="cal-save-help" class="muted">${esc(t("calSaveOverrides"))}</p>
        <div id="cal-save-impact" class="notice" hidden></div>
        <button type="submit" class="primary">${esc(t(context.entity_ids ? "calBatchSave" : "calSave"))}</button>
      </form>
      <div class="actions calibration-safety-actions"><button type="button" id="cal-stop" disabled><ha-icon icon="mdi:stop-circle-outline" aria-hidden="true"></ha-icon><span>${esc(t("calStop"))}</span></button>
        <button type="button" id="cal-cancel" disabled>${esc(t("calCancel"))}</button></div>
    </div>`;
    for (const button of host.querySelectorAll("[data-cal-action]")) {
      button.onclick = () => this._perform(button.dataset.calAction);
    }
    host.querySelector("#cal-repeat").onclick = () => this._perform("repeat");
    host.querySelector("#cal-reading").onsubmit = (event) => {
      event.preventDefault();
      if (event.currentTarget.reportValidity()) this._perform("reading", { reading_cm: Number(event.currentTarget.elements.reading_cm.value) });
    };
    host.querySelector("#cal-stop").onclick = () => this._perform("stop");
    host.querySelector("#cal-cancel").onclick = async () => {
      await this.close({ cancel: true });
      if (this._generation === generation + 1 && host.isConnected) context.onCancel();
    };
    host.querySelector("#cal-reconnect").onclick = () => this.open({ ...context, resume: this._state });
    host.querySelector("#cal-save-mode").onchange = () => {
      this._savePreview = null;
      if (this._state) this._render();
    };
    host.querySelector("#cal-save").onsubmit = (event) => {
      event.preventDefault();
      const form = event.currentTarget;
      if (!form.reportValidity()) return;
      if (context.entity_ids) {
        this._perform("save", { names: [...form.querySelectorAll("[data-batch-name]")].map((input) => input.value.trim()) });
        return;
      }
      const save_mode = host.querySelector("#cal-save-mode").value;
      if (save_mode === "shared" && !this._savePreview) this._perform("preview_save", { save_mode });
      else this._perform("save", { save_mode,
        ...(save_mode === "new" ? { name: form.elements.profile_name.value.trim() } : {}),
        ...(save_mode === "shared" ? { confirmation: this._savePreview.confirmation } : {}) });
    };
    try {
      const unsubscribe = await hass.connection.subscribeMessage((state) => {
        if (!this._current(generation)) return;
        this._accept(state);
      }, context.resume
        ? { type: "myhome/cover_calibration/resume", entry_id: entity.entry_id, session_id: context.resume.session_id, client_id }
        : context.entity_ids
          ? { type: "myhome/cover_calibration/batch_start", entry_id: entity.entry_id, entity_ids: context.entity_ids, revision, client_id }
          : { type: "myhome/cover_calibration/start", entry_id: entity.entry_id, entity_id: entity.entity_id, revision, client_id, ...(automatic || geometry ? { mode: context.mode } : {}), ...(quick ? { direction: quick } : {}) });
      if (!this._current(generation)) { Promise.resolve(unsubscribe()).catch(() => {}); return; }
      this._unsubscribe = unsubscribe;
      this._heartbeat = setInterval(() => this._perform("heartbeat"), 5000);
    } catch (error) {
      if (this._current(generation)) {
        this._error(error);
        host.querySelector("#cal-cancel").disabled = false;
      }
    }
  }

  _current(generation) { return generation === this._generation && this._context.host.isConnected; }

  _accept(state) {
    if (this._state && state.sequence < this._state.sequence) return;
    if (state.recoverable && state.attachment !== this._state?.attachment) {
      this._busy = false;
      this._savePreview = null;
      this._context.host.querySelector("#cal-reason").hidden = true;
    }
    this._state = state;
    if (state.recoverable) this._lost = !state.attached;
    if (state.phase !== "review") this._savePreview = null;
    else if (state.save_preview && this._context.host.querySelector("#cal-save-mode").value === "shared") this._savePreview = state.save_preview;
    this._render();
    if (state.phase === "saved") {
      this.close();
      this._context.onSaved();
    }
  }

  _render() {
    const { host, t } = this._context;
    const state = this._state;
    host.querySelector("#cal-reconnect").hidden = !this._lost || !state.recoverable;
    host.querySelector("#cal-cancel").disabled = false;
    host.querySelector(".cal-panel").dataset.phase = state.phase;
    const automatic = state.mode === "automatic";
    const geometry = state.mode === "geometry";
    host.querySelector("#cal-phase").textContent = automatic && ["starting_open", "starting_close", "opening", "closing", "settling"].includes(state.phase)
      ? `${t("calAutomaticRun")} ${state.run_index + 1}/3 · ${t(`calAutoPhase_${state.phase}`)}`
      : t(state.direction && state.phase === "review" ? "calQuickReview" : `calPhase_${state.phase}`);
    host.querySelector("#cal-elapsed").textContent = state.elapsed == null ? "" : `${t("calElapsed")}: ${state.elapsed} s`;
    host.querySelector("#cal-stop-status").hidden = !state.stop_requested;
    const reason = host.querySelector("#cal-reason");
    if (state.reason) {
      reason.hidden = false;
      reason.textContent = t(`calReason_${state.reason}`);
    }
    for (const action of ["run", "open", "close", "endpoint"]) {
      const button = host.querySelector(`[data-cal-action="${action}"]`);
      button.hidden = geometry ? action !== "endpoint" || !["opening", "closing"].includes(state.phase) || !["home", "reset", "opening", "closing", "top"].includes(state.step) : action === "run" ? !automatic || state.phase !== "confirm_automatic" : automatic || (action === "open" ? state.phase !== "confirm_closed" : action === "close"
        ? state.phase !== "confirm_open" : !["opening", "closing"].includes(state.phase));
      button.disabled = this._busy || this._lost;
    }
    host.querySelector('[data-cal-action="endpoint"]').textContent = t(state.phase === "opening" ? "calEndpointOpen" : "calEndpointClose");
    host.querySelector("#cal-stop").disabled = ["saved", "cancelled"].includes(state.phase);
    host.querySelector("#cal-save").hidden = state.phase !== "review";
    this._renderSave();
    host.querySelector("#cal-values").textContent = `${t("profileOpeningTime")}: ${state.values.opening_time ?? "—"} · ${t("profileClosingTime")}: ${state.values.closing_time ?? "—"}`;
    if (state.direction) {
      host.querySelector("#cal-values").textContent = ["opening", "closing"].map((direction) =>
        `${t(direction === "opening" ? "profileOpeningTime" : "profileClosingTime")}: ${state.values[`${direction}_time`] ?? "—"} s · ${t(direction === state.direction ? "calQuickMeasured" : "calQuickRetained")}`).join(" · ");
    }
    this._renderGeometry(geometry);
    renderCalibrationVisual(host, state, t, this._lost);
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
          ${state.targets[result.index].travel_cm != null ? `<span class="muted">${esc(t("profileReferenceTravel"))}: ${esc(state.targets[result.index].travel_cm)} cm</span>` : ""}
          <span class="muted">${esc(t("profileName"))}</span><input data-batch-name="${result.index}" required maxlength="64" value="${esc(state.targets[result.index].name.slice(0, 64))}"></label>`).join("");
      }
    }
  }

  _renderGeometry(enabled) {
    const { host, t } = this._context, state = this._state;
    const disabled = this._busy || this._lost;
    for (const [action, visible] of [["next", state.phase === "briefing"], ["lift", state.step === "lift" && state.phase === "opening"]]) {
      const button = host.querySelector(`[data-cal-action="${action}"]`);
      button.hidden = !enabled || !visible;
      button.disabled = disabled;
    }
    const form = host.querySelector("#cal-reading");
    form.hidden = !enabled || state.phase !== "reading";
    // HA's scoped registry exposes named form controls but its iterator throws.
    for (const input of form.querySelectorAll("input, button")) input.disabled = disabled;
    const repeat = host.querySelector("#cal-repeat");
    repeat.hidden = !enabled || !state.can_repeat;
    repeat.disabled = disabled;
    if (!enabled) return;
    if (["briefing", "opening", "closing", "reading", "geometry_wait_stop"].includes(state.phase)) {
      const key = state.phase === "briefing" ? `calBrief_${state.step}` : state.phase === "geometry_wait_stop" ? "calGeometryWaitStop"
        : state.phase === "reading" ? `calReading_${state.reading_kind}` : state.step === "lift" ? "calLiftRunning"
          : state.step.startsWith("half_") ? "calHalfRunning" : "calEndpointRunning";
      host.querySelector("#cal-phase").textContent = t(key);
    }
    if (form.dataset.step !== state.step) { form.elements.reading_cm.value = ""; form.dataset.step = state.step; }
    form.elements.reading_cm.min = state.step === "lift" ? "0" : "0.1";
    host.querySelector("#cal-reading-label").textContent = t(state.step === "opening" ? "profileCoverTravel" : "calHeightCm");
    host.querySelector("#cal-expected").textContent = state.expected_cm == null ? "" : `${t("calExpectedRough")}: ${Number(state.expected_cm.toFixed(1))} cm. ${t("calExpectedHelp")}`;
    if (state.phase === "review") {
      host.querySelector("#cal-values").textContent += ` · ${t("profileCoverTravel")}: ${state.travel_cm} cm · ` +
        ["slat_time_s", "opening_roll", "closing_roll"].map((key) => `${t(`calGeometry_${key}`)}: ${Number(state.geometry[key].toFixed(4))}`).join(" · ");
      host.querySelector("#cal-save-help").textContent = t("calGeometryReview");
    }
  }

  _renderSave() {
    const { host, t } = this._context;
    const state = this._state, form = host.querySelector("#cal-save");
    const selector = host.querySelector("#cal-save-mode");
    const modes = state.save_modes || ["new"];
    for (const option of selector.options) option.disabled = !modes.includes(option.value);
    if (!modes.includes(selector.value)) { selector.value = "new"; this._savePreview = null; }
    selector.disabled = this._busy || this._lost;
    const mode = selector.value;
    host.querySelector("#cal-name-label").hidden = !!state.batch || mode !== "new";
    form.elements.profile_name.disabled = !!state.batch || mode !== "new";
    form.elements.profile_name.required = !state.batch && mode === "new";
    host.querySelector("#cal-save-help").textContent = t(mode === "new" ? "calSaveOverrides" : mode === "cover" ? "calSaveCoverHelp" : "calSaveSharedHelp");
    if (!state.batch && mode === "new" && state.travel_cm != null) host.querySelector("#cal-save-help").textContent += ` ${t("profileReferenceTravel")}: ${state.travel_cm} cm.`;
    if (mode === "shared" && state.reference_travel_cm != null) host.querySelector("#cal-save-help").textContent += ` ${t("profileReferenceTravel")}: ${state.reference_travel_cm} cm. ${t("calReferenceNormalization")}`;
    const button = form.querySelector('button[type="submit"]');
    button.disabled = this._busy || this._lost;
    button.textContent = t(state.batch ? "calBatchSave" : mode === "new" ? "calSave" : mode === "cover" ? "calSaveCover" : this._savePreview ? "calConfirmShared" : "calPreviewShared");
    const box = host.querySelector("#cal-save-impact");
    box.hidden = !this._savePreview;
    if (this._savePreview && this._renderedPreview !== this._savePreview) {
      const preview = this._savePreview;
      box.innerHTML = `<p><strong>${esc(preview.after.name)}</strong> · ${esc(t("calSharedImpact"))}</p><ul>${preview.followers.map((item) =>
        `<li><strong>${esc(item.name || item.entity_id || t("profileMissingCover"))}</strong>${item.available ? "" : ` · ${esc(t("profileUnavailableFollower"))}`}<br>${["opening", "closing"].map((direction) => {
          const change = item.changes[direction];
          return `${esc(t(direction === "opening" ? "profileOpeningTime" : "profileClosingTime"))}: ${esc(change.before)} → ${esc(change.after)} s${change.overridden ? ` · ${esc(t("calPersonalRetained"))}` : change.override_removed ? ` · ${esc(t("calPersonalRemoved"))}` : ""}`;
        }).join("<br>")}</li>`).join("")}</ul>`;
    }
    this._renderedPreview = this._savePreview;
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
    const current = () => this._current(generation) && this._state?.attachment === state.attachment;
    if (ownsBusy) {
      this._busy = true;
      this._context.host.querySelector("#cal-reason").hidden = true;
    }
    this._render();
    try {
      const result = await hass.callWS({ type: "myhome/cover_calibration/action", entry_id: state.entry_id,
        session_id: state.session_id, sequence: state.sequence,
        ...(state.attachment ? { attachment: state.attachment } : {}), action, ...extra });
      if (current()) this._accept(result);
    } catch (error) {
      if (!current()) return;
      if (action === "heartbeat") this._lost = true;
      if (action === "save" || action === "preview_save") this._savePreview = null;
      this._error(error);
    } finally {
      if (current()) { if (ownsBusy) this._busy = false; this._render(); }
    }
  }
}
