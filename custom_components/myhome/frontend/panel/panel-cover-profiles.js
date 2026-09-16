/** Single-cover profile editor. Navigation/connection changes invalidate all UI work. */
const url = new URL("panel-dom.js", import.meta.url);
url.search = new URL(import.meta.url).search;
const { escapeHtml: esc } = await import(url.href);

const calibrationUrl = new URL("panel-cover-calibration.js", import.meta.url);
calibrationUrl.search = url.search;
const { CoverCalibration } = await import(calibrationUrl.href);

export class CoverProfileEditor {
  constructor() { this._generation = 0; this._calibration = new CoverCalibration(); }

  close() {
    this._calibration.close();
    this._generation++;
    Promise.resolve(this._unsubscribe?.()).catch(() => {});
    this._unsubscribe = null;
    clearInterval(this._fallback);
    document.removeEventListener("visibilitychange", this._visibility);
    this.dialog?.close();
    this.dialog?.remove();
    this.dialog = null;
  }

  async open({ host, hass, entity, t, onSaved }) {
    this.close();
    const generation = this._generation;
    this._context = { host, hass, entity, t, onSaved, generation };
    this._data = null;
    this._preview = null;
    this._noticedRevision = -1;
    this._stale = this._calibrating = this._syncFailed = false;
    host.innerHTML = `<dialog class="cover-profile-dialog" aria-labelledby="profile-title">
      <header class="dialog-head"><div class="dialog-icon" aria-hidden="true"><ha-icon icon="mdi:window-shutter-settings"></ha-icon></div>
        <div class="dialog-head-text"><h2 id="profile-title">${esc(t("coverProfiles"))}</h2>
        <p class="muted">${esc(entity.name || entity.original_name || entity.entity_id)}</p></div>
        <button type="button" id="profile-export" class="icon-button" title="${esc(t("profileExport"))}" aria-label="${esc(t("profileExport"))}" aria-describedby="profile-export-help"><ha-icon icon="mdi:download-outline" aria-hidden="true"></ha-icon></button>
        <button type="button" class="help" data-help="profile-scope-help" aria-expanded="false" aria-controls="profile-scope-help" title="${esc(t("help"))}" aria-label="${esc(t("help"))}">?</button></header>
      <p id="profile-scope-help" class="help-text muted" hidden>${esc(t("profileScope"))}</p>
      <p id="profile-export-help" hidden>${esc(t("profileExportHelp"))}</p>
      <div id="profile-body"><p class="muted" role="status">${esc(t("loading"))}</p></div>
      <footer class="dialog-foot">
        <p id="profile-export-status" role="status" class="muted"></p>
        <button type="button" id="profile-close">${esc(t("close"))}</button>
      </footer>
    </dialog>`;
    this.dialog = host.querySelector("dialog");
    this.dialog.addEventListener("click", (event) => {
      const trigger = event.target.closest("[data-help]");
      if (!trigger) return;
      event.preventDefault();
      const text = this.dialog.querySelector(`#${trigger.dataset.help}`);
      if (!text) return;
      text.hidden = !text.hidden;
      trigger.setAttribute("aria-expanded", String(!text.hidden));
      const details = trigger.closest("details");
      if (details && !text.hidden) details.open = true;
    });
    host.querySelector("#profile-close").onclick = () => this.close();
    host.querySelector("#profile-export").onclick = () => this._export();
    this.dialog.oncancel = (event) => { event.preventDefault(); this.close(); };
    this.dialog.showModal();
    this._visibility = () => { if (!document.hidden) this._refresh(true); };
    document.addEventListener("visibilitychange", this._visibility);
    try {
      // Subscribe first: an edit between subscription and read cannot be missed.
      try {
        const unsubscribe = await hass.connection.subscribeMessage((event) => {
          if (!this._current(generation) || event.entry_id !== entity.entry_id) return;
          if (event.kind === "removed") { this.close(); return; }
          if (!Number.isInteger(event.revision) || event.revision < 0) return;
          this._noticedRevision = Math.max(this._noticedRevision, event.revision);
          this._refresh();
        }, { type: "myhome/cover_profiles/subscribe", entry_id: entity.entry_id });
        if (!this._current(generation)) { await unsubscribe(); return; }
        this._unsubscribe = unsubscribe;
      } catch {
        if (!this._current(generation)) return;
        this._syncFailed = true;
        this._fallback = setInterval(() => { if (!document.hidden) this._refresh(true); }, 15000);
      }
      const data = await hass.callWS({ type: "myhome/cover_profiles/read", entry_id: entity.entry_id, entity_id: entity.entity_id });
      if (!this._current(generation)) return;
      this._data = data;
      this._render();
      this._refresh();
    } catch (error) {
      if (this._current(generation)) host.querySelector("#profile-body").textContent = this._error(error);
    }
  }

  updateState(hass) {
    const attrs = hass?.states?.[this._context?.entity.entity_id]?.attributes;
    if (!this.dialog || !attrs) return;
    for (const direction of ["opening", "closing"]) {
      const effective = this.dialog.querySelector(`#profile-effective-${direction}`);
      const value = attrs[`${direction}_time`] ?? attrs.travel_time;
      if (effective && value != null) effective.textContent = value;
    }
    const pending = this.dialog.querySelector("#profile-pending");
    if (pending && typeof attrs.cover_profile_pending === "boolean") pending.hidden = !attrs.cover_profile_pending;
  }

  _current(generation) { return generation === this._generation && this.dialog?.isConnected; }

  _draft() {
    const form = this.dialog?.querySelector("#profile-form");
    return form ? JSON.stringify([...["profile", "profile_name", "opening_time", "closing_time", "override_opening", "override_closing"]
      .map((name) => form.elements[name].value), !form.querySelector("#profile-delete-confirmation").hidden,
      ...["opening", "closing"].map((direction) => form.elements[`use_${direction}`].checked), Boolean(this._preview)]) : null;
  }

  _markStale() {
    this._stale = true;
    const error = this.dialog.querySelector("#profile-error");
    error.textContent = this._context.t("profileChanged");
    error.hidden = false;
    this.dialog.querySelector("#profile-reload").hidden = false;
    for (const button of this.dialog.querySelectorAll("[data-profile-action], #profile-delete, #profile-calibrate, #profile-calibrate-batch")) button.disabled = true;
  }

  async _refresh(force = false) {
    const { generation, hass, entity } = this._context;
    if (!this._current(generation) || !this._data || this._calibrating || this._stale
        || this._saving === generation || this._refreshing === generation
        || (!force && this._noticedRevision <= this._data.revision)) return;
    let failed = false;
    this._refreshing = generation;
    try {
      const data = await hass.callWS({ type: "myhome/cover_profiles/read", entry_id: entity.entry_id, entity_id: entity.entity_id });
      if (!this._current(generation) || this._calibrating || this._saving === generation || data.revision <= this._data.revision) return;
      if (this._draft() !== this._baseline) { this._markStale(); return; }
      this._data = data;
      this._render();
    } catch (error) {
      failed = true;
      if (!this._current(generation) || this._calibrating) return;
      const box = this.dialog.querySelector("#profile-error");
      box.textContent = this._error(error);
      box.hidden = false;
      this.dialog.querySelector("#profile-reload").hidden = false;
    } finally {
      if (this._current(generation)) {
        this._refreshing = null;
        if (!failed && this._noticedRevision > this._data.revision) this._refresh();
      }
    }
  }
  _error(error) {
    const { t } = this._context;
    const key = `profileError_${error.code}`;
    return t(key) === key ? t("profileError") : t(key);
  }

  async _selectBatch() {
    if (this._calibrating || this._saving === this._generation || this._stale) return;
    this._calibrating = true;
    const context = this._context;
    const { hass, entity, t, generation } = context;
    const host = this.dialog.querySelector("#profile-body");
    host.innerHTML = `<h3>${esc(t("calBatchTitle"))}</h3><p>${esc(t("calBatchSelectHelp"))}</p>
      <p id="batch-error" role="alert"></p><div id="batch-selection">${esc(t("loading"))}</div>
      <div class="actions"><button type="button" id="batch-continue" disabled>${esc(t("calBatchContinue"))}</button>
      <button type="button" id="batch-back">${esc(t("cancel"))}</button></div>`;
    host.querySelector("#batch-back").onclick = () => this.open(context);
    try {
      const data = await hass.callWS({ type: "myhome/cover_calibration/targets", entry_id: entity.entry_id });
      if (!this._current(generation)) return;
      const list = host.querySelector("#batch-selection");
      list.innerHTML = data.targets.map((item) => `<label class="batch-target"><input type="checkbox" value="${esc(item.entity_id)}" ${item.reason ? "disabled" : ""}>
        <span>${esc(item.name)}<small class="muted">${esc(item.entity_id)}${item.reason ? ` · ${esc(t(`profileError_${item.reason}`))}` : ""}</small></span></label>`).join("") || esc(t("calBatchEmpty"));
      const selected = () => [...list.querySelectorAll("input:checked:not(:disabled)")].map((input) => input.value);
      const next = host.querySelector("#batch-continue");
      list.onchange = () => {
        const count = selected().length;
        next.disabled = count === 0 || count > data.max_batch;
        host.querySelector("#batch-error").textContent = count > data.max_batch ? `${t("calBatchLimit")}: ${data.max_batch}` : "";
      };
      next.onclick = () => {
        const ids = selected();
        if (next.disabled || !ids.length || ids.length > data.max_batch) return;
        next.disabled = true;
        this._calibration.open({ ...context, host, mode: "automatic", entity_ids: ids, revision: data.revision,
          onCancel: () => this.open(context), onSaved: () => { context.onSaved(t("saved")); this.open(context); } });
      };
    } catch (error) {
      if (this._current(generation)) {
        host.querySelector("#batch-selection").textContent = "";
        host.querySelector("#batch-error").textContent = this._error(error);
      }
    }
  }

  async _export() {
    const { hass, entity, generation, t } = this._context;
    if (this._exporting === generation) return;
    this._exporting = generation;
    const button = this.dialog.querySelector("#profile-export");
    const status = this.dialog.querySelector("#profile-export-status");
    button.disabled = true;
    status.textContent = t("profileExporting");
    try {
      const data = await hass.callWS({ type: "myhome/cover_profiles/export", entry_id: entity.entry_id });
      if (!this._current(generation)) return;
      const blob = new Blob([JSON.stringify(data, null, 2) + "\n"], { type: "application/json" });
      const url = URL.createObjectURL(blob);
      const link = document.createElement("a");
      try {
        link.href = url;
        link.download = `myhome-calibration-${entity.entry_id.replace(/[^a-zA-Z0-9_-]/g, "_")}-r${data.revision}.json`;
        document.body.append(link);
        link.click();
      } finally {
        link.remove();
        // Allow browsers to start the download before releasing its object URL.
        setTimeout(() => URL.revokeObjectURL(url), 1000);
      }
      status.textContent = t("profileExported");
    } catch {
      if (this._current(generation)) status.textContent = t("profileExportError");
    } finally {
      if (this._exporting === generation) this._exporting = null;
      if (this._current(generation)) button.disabled = false;
    }
  }

  _renderProvenance(profile) {
    const { t, hass } = this._context;
    const host = this.dialog.querySelector("#profile-provenance");
    const dateText = (value) => {
      if (!value || Number.isNaN(new Date(value).getTime())) return t("profileDateUnknown");
      try { return new Intl.DateTimeFormat(hass.language || "en", { dateStyle: "medium", timeStyle: "short" }).format(new Date(value)); }
      catch { return new Intl.DateTimeFormat("en", { dateStyle: "medium", timeStyle: "short" }).format(new Date(value)); }
    };
    const icons = { manual: "mdi:pencil-outline", guided: "mdi:timer-outline", automatic: "mdi:robot-outline", unknown: "mdi:help-circle-outline", configured: "mdi:file-code-outline" };
    const wasOpen = !host.querySelector("#profile-provenance-help")?.hidden;
    host.innerHTML = `<div class="profile-origin-head"><span class="muted">${esc(t("profileProvenance"))}</span>
      <button type="button" class="help" data-help="profile-provenance-help" aria-expanded="${wasOpen}" aria-controls="profile-provenance-help" title="${esc(t("help"))}" aria-label="${esc(t("help"))}">?</button></div>
      <p id="profile-provenance-help" class="help-text muted" ${wasOpen ? "" : "hidden"}>${esc(t("profileProvenanceHelp"))}</p>
      <div class="profile-origin-grid">${["opening", "closing"].map((direction) => {
      const meta = profile?.provenance?.[direction];
      const source = profile ? (["manual", "guided", "automatic"].includes(meta?.source) ? meta.source : "unknown") : "configured";
      const parts = [
        `<span class="profile-origin-source">${esc(t(`profileSource_${source}`))}</span>`,
        meta?.inherited ? `<span>${esc(t("profileInherited"))}: ${esc(profile.name)}</span>` : "",
        meta?.origin_entity_id || meta?.inherited ? `<span>${esc(t("profileOriginCover"))}: ${esc(meta.origin_name || meta.origin_entity_id || t("profileMissingCover"))}</span>` : "",
        ["manual", "guided", "automatic"].includes(source) ? `<span>${esc(t(source !== "manual" ? "profileMeasuredAt" : "profileModifiedAt"))}: ${esc(dateText(meta.recorded_at))}</span>` : "",
      ].filter(Boolean);
      return `<p class="profile-origin" data-origin-direction="${direction}"><ha-icon icon="${icons[source]}" aria-hidden="true"></ha-icon><span class="profile-origin-text">${parts.join("")}</span></p>`;
    }).join("")}</div>`;
  }

  _render() {
    const { host, t } = this._context;
    const data = this._data;
    this._preview = null;
    const assigned = data.profiles.find((profile) => profile.id === data.assigned_profile_id);
    const disabled = data.writable ? "" : "disabled";
    this._sections ??= new Set(["calibration"]);
    const open = (name) => this._sections.has(name) ? "open" : "";
    const chevron = `<ha-icon class="section-chevron" icon="mdi:chevron-down" aria-hidden="true"></ha-icon>`;
    const help = (id) => `<button type="button" class="help" data-help="${id}" aria-expanded="false" aria-controls="${id}" title="${esc(t("help"))}" aria-label="${esc(t("help"))}">?</button>`;
    const helpText = (id, key) => `<p id="${id}" class="help-text muted" hidden>${esc(t(key))}</p>`;
    host.querySelector("#profile-body").innerHTML = `
      ${this._syncFailed ? `<p class="notice">${esc(t("profileSyncFallback"))}</p>` : ""}
      <section class="profile-summary" aria-label="${esc(t("profileSectionStatus"))}">
        <div class="profile-assigned"><span class="muted">${esc(t("profileAssigned"))}</span><strong>${esc(assigned?.name || t("profileDefault"))}</strong></div>
        <div class="profile-stats">
          <div class="profile-stat"><ha-icon icon="mdi:arrow-up-bold-outline" aria-hidden="true"></ha-icon><span class="muted">${esc(t("profileEffectiveOpening"))}</span>
            <span class="profile-stat-value"><span id="profile-effective-opening">${esc(data.effective_opening_time ?? data.effective_travel_time ?? "—")}</span><small>s</small></span></div>
          <div class="profile-stat"><ha-icon icon="mdi:arrow-down-bold-outline" aria-hidden="true"></ha-icon><span class="muted">${esc(t("profileEffectiveClosing"))}</span>
            <span class="profile-stat-value"><span id="profile-effective-closing">${esc(data.effective_closing_time ?? data.effective_travel_time ?? "—")}</span><small>s</small></span></div>
        </div>
        <p id="profile-pending" class="profile-pending" role="status" ${data.pending ? "" : "hidden"}>${esc(t("profilePending"))}</p>
      </section>
      ${!data.writable ? `<p class="notice">${esc(t(`profileError_${data.reason}`))}</p>` : ""}
      <p class="muted" id="profile-override-status" ${Object.values(data.configured || {}).some((item) => item.origin === "override") ? "" : "hidden"}>${esc(t("profileOverridesActive"))}</p>
      <form id="profile-form">
        <fieldset class="profile-section"><legend>${esc(t("profileSectionAssign"))}</legend>
          <div class="profile-assign-row">
            <label>${esc(t("profileChoose"))}<select name="profile" ${disabled}>
              <option value="">${esc(t("profileDefault"))}</option>
              ${data.profiles.map((profile) => `<option value="${esc(profile.id)}">${esc(profile.name)} · ${esc(profile.opening_time ?? profile.travel_time)} / ${esc(profile.closing_time ?? profile.travel_time)} s</option>`).join("")}
            </select></label>
            <button type="button" class="primary" data-profile-action="assign" ${disabled}>${esc(t("profileAssign"))}</button>
          </div>
          ${data.scaling === "unscaled" ? `<p class="muted" id="profile-unscaled">${esc(t("profileUnscaled"))}</p>` : ""}
        </fieldset>
        <details class="profile-details" data-section="calibration" ${open("calibration")}>
          <summary><span class="profile-details-title">${esc(t("profileSectionCalibrate"))} ${help("cal-help-text")}</span><span class="profile-details-hint" id="cal-hint"></span>${chevron}</summary>
          <div class="profile-details-body">
            ${helpText("cal-help-text", "calQuickRequirement")}
            <div class="profile-times">
              <label>${esc(t("calMode"))}<select id="cal-mode" ${disabled}><option value="guided">${esc(t("calGuided"))}</option><option value="automatic">${esc(t("calAutomatic"))}</option></select></label>
              <label>${esc(t("calScope"))}<select id="cal-direction" aria-describedby="cal-scope-help" ${disabled}><option value="">${esc(t("calBothDirections"))}</option><option value="opening" ${assigned ? "" : "disabled"}>${esc(t("calOnlyOpening"))}</option><option value="closing" ${assigned ? "" : "disabled"}>${esc(t("calOnlyClosing"))}</option></select></label>
            </div>
            <p id="cal-scope-help" class="muted" role="status">${esc(t(assigned ? "calSingleDirectionGuided" : "profileError_calibration_profile_required"))}</p>
            <button type="button" id="profile-calibrate" class="primary profile-calibrate" ${disabled}><ha-icon icon="mdi:timer-outline" aria-hidden="true"></ha-icon><span id="cal-label">${esc(t("calTitle"))}</span></button>
            <button type="button" id="profile-calibrate-batch" class="ghost profile-calibrate-batch" ${disabled}><ha-icon icon="mdi:select-group" aria-hidden="true"></ha-icon><span>${esc(t("calBatchTitle"))}</span></button>
          </div>
        </details>
        <details class="profile-details" data-section="edit" ${open("edit")}>
          <summary><span class="profile-details-title">${esc(t("profileSectionEdit"))} ${help("edit-help-text")}</span><span class="profile-details-hint" id="edit-hint"></span>${chevron}</summary>
          <div class="profile-details-body">
            ${helpText("edit-help-text", "profileSharedHelp")}
            <label>${esc(t("profileName"))}<input name="profile_name" maxlength="64" required ${disabled}></label>
            <div class="profile-times">
              <label>${esc(t("profileOpeningTime"))}<span class="input-suffix"><input name="opening_time" type="number" min="1" max="600" step="any" inputmode="decimal" required ${disabled}><span>s</span></span></label>
              <label>${esc(t("profileClosingTime"))}<span class="input-suffix"><input name="closing_time" type="number" min="1" max="600" step="any" inputmode="decimal" required ${disabled}><span>s</span></span></label>
            </div>
            <div id="profile-provenance" class="profile-provenance"></div>
            <div class="actions">
              <button type="button" data-profile-action="update">${esc(t("profileUpdate"))}</button>
              <button type="button" data-profile-action="shared">${esc(t("profileSharedPreview"))}</button>
              <button type="button" class="primary" data-profile-action="new" ${disabled}>${esc(t("profileCreate"))}</button>
            </div>
            <div id="profile-impact" class="profile-confirm" hidden aria-live="polite"></div>
          </div>
        </details>
        <details class="profile-details" data-section="advanced" ${open("advanced")}>
          <summary><span class="profile-details-title">${esc(t("profileSectionAdvanced"))}</span><span class="profile-details-hint">${esc(t("profilePersonalValues"))}</span>${chevron}</summary>
          <div class="profile-details-body">
            <fieldset class="profile-section"><legend>${esc(t("profilePersonalValues"))}</legend>
              <p class="muted">${esc(t("profileOverrideHelp"))}</p>
              ${["opening", "closing"].map((direction) => {
                const item = data.configured?.[direction];
                const own = item?.origin === "override";
                return `<label class="profile-override"><span class="profile-override-toggle"><input type="checkbox" name="use_${direction}" ${own ? "checked" : ""} ${disabled}> ${esc(t(direction === "opening" ? "profileEffectiveOpening" : "profileEffectiveClosing"))}</span>
                  <span class="input-suffix"><input name="override_${direction}" aria-label="${esc(t("profilePersonalValues"))}: ${esc(t(direction === "opening" ? "profileEffectiveOpening" : "profileEffectiveClosing"))}" type="number" min="1" max="600" step="any" inputmode="decimal" value="${esc(item?.value ?? data.default_travel_time ?? "")}" ${data.writable && own ? "" : "disabled"}><span>s</span></span></label>`;
              }).join("")}
              <button type="button" data-profile-action="overrides" ${disabled}>${esc(t("profileSavePersonal"))}</button>
            </fieldset>
            <p id="profile-usage" class="muted profile-usage"></p>
            <button type="button" id="profile-delete" class="danger"><ha-icon icon="mdi:delete-outline" aria-hidden="true"></ha-icon><span>${esc(t("profileDelete"))}</span></button>
            <div id="profile-delete-confirmation" class="profile-confirm" hidden>
              <p id="profile-delete-prompt"></p>
              <div class="actions">
                <button type="button" class="danger-solid" data-profile-action="delete">${esc(t("profileDeleteConfirm"))}</button>
                <button type="button" id="profile-delete-cancel">${esc(t("cancel"))}</button>
              </div>
            </div>
          </div>
        </details>
        <p id="profile-error" class="error" role="alert" hidden></p>
        <button type="button" id="profile-reload" hidden><ha-icon icon="mdi:reload" aria-hidden="true"></ha-icon><span>${esc(t("profileReload"))}</span></button>
      </form>`;
    for (const section of host.querySelectorAll(".profile-details")) {
      section.addEventListener("toggle", () => {
        if (section.open) this._sections.add(section.dataset.section);
        else this._sections.delete(section.dataset.section);
      });
    }
    const updateCalibrationLabels = () => {
      const mode = host.querySelector("#cal-mode").value;
      const scope = host.querySelector("#cal-direction");
      const scopeLabel = scope.selectedOptions[0]?.textContent || t("calBothDirections");
      host.querySelector("#cal-label").textContent = mode === "automatic" ? t("calAutomatic") : `${t("calMeasure")}: ${scopeLabel.toLocaleLowerCase()}`;
      host.querySelector("#cal-hint").textContent = `${t(mode === "automatic" ? "calAutomatic" : "calGuided")} · ${scopeLabel.toLocaleLowerCase()}`;
    };
    host.querySelector("#profile-calibrate-batch").onclick = () => this._selectBatch();
    host.querySelector("#cal-mode").onchange = () => {
      const scope = host.querySelector("#cal-direction");
      if (host.querySelector("#cal-mode").value === "automatic") scope.value = "";
      updateCalibrationLabels();
    };
    host.querySelector("#cal-direction").onchange = () => {
      // A partial measurement uses the guided protocol; keep the choice accessible
      // even when the operator selected automatic measurement first.
      if (host.querySelector("#cal-direction").value) host.querySelector("#cal-mode").value = "guided";
      updateCalibrationLabels();
    };
    updateCalibrationLabels();
    host.querySelector("#profile-calibrate").onclick = () => {
      if (this._saving === this._generation || !data.writable || this._stale) return;
      this._calibrating = true;
      const context = this._context;
      const mode = host.querySelector("#cal-mode").value;
      const direction = mode === "guided" && assigned ? host.querySelector("#cal-direction").value : "";
      this._calibration.open({ ...context, mode, direction, host: host.querySelector("#profile-body"), revision: data.revision,
        onCancel: () => this.open(context), onSaved: () => { context.onSaved(t("saved")); this.open(context); } });
    };
    const form = host.querySelector("#profile-form");
    form.elements.profile.value = data.assigned_profile_id || "";
    const clearPreview = () => { this._preview = null; form.querySelector("#profile-impact").hidden = true; };
    form.addEventListener("input", clearPreview);
    for (const direction of ["opening", "closing"]) {
      form.elements[`use_${direction}`].onchange = () => {
        form.elements[`override_${direction}`].disabled = !data.writable || !form.elements[`use_${direction}`].checked;
        clearPreview();
      };
    }
    const select = () => {
      clearPreview();
      const profile = data.profiles.find((item) => item.id === form.elements.profile.value);
      form.elements.profile_name.value = profile?.name || "";
      for (const direction of ["opening", "closing"]) {
        form.elements[`${direction}_time`].value = profile?.[`${direction}_time`] ?? profile?.travel_time ?? data.default_travel_time ?? "";
      }
      this._renderProvenance(profile);
      form.querySelector("#edit-hint").textContent = profile
        ? `${profile.name} · ${profile.opening_time ?? profile.travel_time} / ${profile.closing_time ?? profile.travel_time} s`
        : t("profileDefault");
      form.querySelector("#profile-delete-confirmation").hidden = true;
      form.querySelector("#profile-delete").hidden = !data.writable || !profile || profile.uses !== 0;
      form.querySelector("#profile-usage").classList.toggle("is-warning", profile?.uses > 0);
      form.querySelector("#profile-usage").textContent = profile?.uses > 0
        ? `${t("profileInUse")}: ${(profile.assigned_to || []).map((item) => item.name || item.entity_id || t("profileMissingCover")).join(", ") || profile.uses}. ${t("profileDeleteHelp")}`
        : profile ? t("profileUnused") : "";
      form.querySelector('[data-profile-action="shared"]').hidden = !data.writable || !profile
        || profile.id !== data.assigned_profile_id || profile.uses <= 1;
      form.querySelector('[data-profile-action="update"]').hidden = !data.writable || !profile
        || profile.id !== data.assigned_profile_id || profile.uses > 1;
    };
    form.querySelector("#profile-delete").onclick = () => {
      const profile = data.profiles.find((item) => item.id === form.elements.profile.value);
      if (!data.writable || !profile || profile.uses !== 0) return;
      form.querySelector("#profile-delete-prompt").textContent = `${t("profileDeletePrompt")} “${profile.name}”?`;
      form.querySelector("#profile-delete-confirmation").hidden = false;
      form.querySelector('[data-profile-action="delete"]').focus();
    };
    form.querySelector("#profile-delete-cancel").onclick = () => {
      form.querySelector("#profile-delete-confirmation").hidden = true;
      form.querySelector("#profile-delete").focus();
    };
    select();
    this._baseline = this._draft();
    form.elements.profile.onchange = select;
    form.onsubmit = (event) => event.preventDefault();
    for (const button of form.querySelectorAll("[data-profile-action]")) button.onclick = () => this._save(button.dataset.profileAction);
    host.querySelector("#profile-reload").onclick = () => this.open(this._context);
  }

  _renderImpact(data, message) {
    const { t } = this._context;
    this._preview = { message, confirmation: data.confirmation };
    const box = this.dialog.querySelector("#profile-impact");
    box.hidden = false;
    box.innerHTML = `<h4>${esc(t("profileSharedPreview"))}</h4>
      <p>${esc(data.before.name)} → ${esc(data.after.name)} · ${esc(data.before.opening_time)} / ${esc(data.before.closing_time)} s → ${esc(data.after.opening_time)} / ${esc(data.after.closing_time)} s</p>
      <p>${esc(t("profileImpactHelp"))}</p>
      <ul>${data.followers.map((item) => `<li><strong>${esc(item.name || item.entity_id || t("profileMissingCover"))}</strong>${item.available ? "" : ` · ${esc(t("profileUnavailableFollower"))}`}
        <p>${["opening", "closing"].map((direction) => {
          const change = item.changes[direction];
          return `${esc(t(direction === "opening" ? "profileEffectiveOpening" : "profileEffectiveClosing"))}: ${esc(change.before)} → ${esc(change.after)} s${change.overridden ? ` (${esc(t("profilePersonalValue"))})` : ""}`;
        }).join(" · ")}</p></li>`).join("")}</ul>
      <div class="actions"><button type="button" class="primary" data-profile-action="confirm_shared">${esc(t("profileConfirmShared"))}</button>
        <button type="button" id="profile-impact-cancel">${esc(t("cancel"))}</button></div>`;
    box.querySelector('[data-profile-action="confirm_shared"]').onclick = () => this._save("confirm_shared");
    box.querySelector("#profile-impact-cancel").onclick = () => { this._preview = null; box.hidden = true; };
  }

  async _save(action) {
    const { hass, entity, host, onSaved, generation, t } = this._context;
    if (this._saving === generation || !this._data.writable || this._stale) return;
    const form = host.querySelector("#profile-form");
    const savingProfile = ["new", "update", "shared"].includes(action);
    if (savingProfile && !["profile_name", "opening_time", "closing_time"].every((key) => form.elements[key].reportValidity())) return;
    if (action === "confirm_shared" && !this._preview) return;
    if (action === "delete") {
      const selected = this._data.profiles.find((item) => item.id === form.elements.profile.value);
      if (!selected || selected.uses !== 0 || form.querySelector("#profile-delete-confirmation").hidden) return;
    }
    let message = {
      type: "myhome/cover_profiles/write", entry_id: entity.entry_id, entity_id: entity.entity_id,
      revision: this._data.revision, action: savingProfile ? "save" : action,
      profile_id: action === "new" ? null : form.elements.profile.value || null,
    };
    if (savingProfile) message.profile = {
      name: form.elements.profile_name.value.trim(),
      opening_time: Number(form.elements.opening_time.value),
      closing_time: Number(form.elements.closing_time.value),
    };
    if (action === "new" && form.elements.profile.value) message.copy_from_profile_id = form.elements.profile.value;
    if (action === "shared") message.action = "preview";
    if (action === "confirm_shared") message = { ...this._preview.message, action: "update_shared", confirmation: this._preview.confirmation };
    if (action === "overrides") {
      message.overrides = {};
      delete message.profile_id;
      for (const direction of ["opening", "closing"]) {
        const input = form.elements[`override_${direction}`];
        const own = form.elements[`use_${direction}`].checked;
        if (own && (!input.value || !input.reportValidity())) return;
        message.overrides[direction] = own ? Number(input.value) : null;
      }
    }
    this._saving = generation;
    const controls = [...form.querySelectorAll("input, select, button:not(.help)")];
    const disabledBefore = controls.map((control) => control.disabled);
    for (const control of controls) control.disabled = true;
    const errorBox = form.querySelector("#profile-error");
    errorBox.hidden = true;
    try {
      const data = await hass.callWS(message);
      if (!this._current(generation)) return;
      if (action === "shared") {
        this._renderImpact(data, message);
        return;
      }
      this._data = data;
      this._render();
      onSaved(action === "delete" ? t("profileDeleted") : data.pending ? t("profilePending") : t("saved"));
    } catch (error) {
      if (!this._current(generation)) return;
      errorBox.textContent = this._error(error);
      errorBox.hidden = false;
      form.querySelector("#profile-reload").hidden = false;
      if (error.code === "revision_conflict") this._data.writable = false;
    } finally {
      if (this._current(generation)) {
        this._saving = null;
        for (const [index, control] of controls.entries()) control.disabled = control.id === "profile-reload" ? false : disabledBefore[index] || !this._data.writable;
        this._refresh();
      }
    }
  }
}
