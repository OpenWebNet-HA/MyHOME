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
    this._noticedRevision = -1;
    this._stale = this._calibrating = this._syncFailed = false;
    host.innerHTML = `<dialog class="cover-profile-dialog" aria-labelledby="profile-title">
      <header class="dialog-head"><div class="dialog-icon" aria-hidden="true"><ha-icon icon="mdi:window-shutter-settings"></ha-icon></div>
        <div class="dialog-head-text"><h2 id="profile-title">${esc(t("coverProfiles"))}</h2>
        <p class="muted">${esc(entity.name || entity.original_name || entity.entity_id)}</p></div></header>
      <div id="profile-body"><p class="muted" role="status">${esc(t("loading"))}</p></div>
      <footer class="dialog-foot">
        <p class="muted">${esc(t("profileExportHelp"))}</p>
        <p id="profile-export-status" role="status" class="muted"></p>
        <div class="actions"><button type="button" id="profile-export"><ha-icon icon="mdi:download" aria-hidden="true"></ha-icon><span>${esc(t("profileExport"))}</span></button>
          <button type="button" id="profile-close">${esc(t("cancel"))}</button></div>
      </footer>
    </dialog>`;
    this.dialog = host.querySelector("dialog");
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
    return form ? JSON.stringify([...["profile", "profile_name", "opening_time", "closing_time"]
      .map((name) => form.elements[name].value), !form.querySelector("#profile-delete-confirmation").hidden]) : null;
  }

  _markStale() {
    this._stale = true;
    const error = this.dialog.querySelector("#profile-error");
    error.textContent = this._context.t("profileChanged");
    error.hidden = false;
    this.dialog.querySelector("#profile-reload").hidden = false;
    for (const button of this.dialog.querySelectorAll("[data-profile-action], #profile-delete, #profile-calibrate")) button.disabled = true;
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
    host.innerHTML = `<h3>${esc(t("profileProvenance"))}</h3><div class="profile-origin-grid">${["opening", "closing"].map((direction) => {
      const meta = profile?.provenance?.[direction];
      const source = profile ? (["manual", "guided"].includes(meta?.source) ? meta.source : "unknown") : "configured";
      const value = profile?.[`${direction}_time`] ?? profile?.travel_time ?? this._data.default_travel_time ?? "—";
      return `<section data-origin-direction="${direction}"><h4>${esc(t(direction === "opening" ? "profileOpeningTime" : "profileClosingTime"))}: ${esc(value)} s</h4>
        ${meta?.inherited ? `<p class="profile-origin-inherited">${esc(t("profileInherited"))}: ${esc(profile.name)}</p>` : ""}
        <p>${esc(t(`profileSource_${source}`))}</p>
        ${meta?.origin_entity_id || meta?.inherited ? `<p class="muted">${esc(t("profileOriginCover"))}: ${esc(meta.origin_name || meta.origin_entity_id || t("profileMissingCover"))}</p>` : ""}
        ${["manual", "guided"].includes(source) ? `<p class="muted">${esc(t(source === "guided" ? "profileMeasuredAt" : "profileModifiedAt"))}: ${esc(dateText(meta.recorded_at))}</p>` : ""}
      </section>`;
    }).join("")}</div><p class="muted">${esc(t("profileProvenanceHelp"))}</p>`;
  }

  _render() {
    const { host, t } = this._context;
    const data = this._data;
    const assigned = data.profiles.find((profile) => profile.id === data.assigned_profile_id);
    const disabled = data.writable ? "" : "disabled";
    host.querySelector("#profile-body").innerHTML = `
      <p class="muted">${esc(t("profileScope"))}</p>
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
      <button type="button" id="profile-calibrate" class="profile-calibrate" ${disabled}><ha-icon icon="mdi:timer-outline" aria-hidden="true"></ha-icon><span>${esc(t("calTitle"))}</span></button>
      <form id="profile-form">
        <fieldset class="profile-section"><legend>${esc(t("profileSectionAssign"))}</legend>
          <div class="profile-assign-row">
            <label>${esc(t("profileChoose"))}<select name="profile" ${disabled}>
              <option value="">${esc(t("profileDefault"))}</option>
              ${data.profiles.map((profile) => `<option value="${esc(profile.id)}">${esc(profile.name)} · ${esc(profile.opening_time ?? profile.travel_time)} / ${esc(profile.closing_time ?? profile.travel_time)} s</option>`).join("")}
            </select></label>
            <button type="button" class="primary" data-profile-action="assign" ${disabled}>${esc(t("profileAssign"))}</button>
          </div>
        </fieldset>
        <fieldset class="profile-section"><legend>${esc(t("profileSectionEdit"))}</legend>
          <label>${esc(t("profileName"))}<input name="profile_name" maxlength="64" required ${disabled}></label>
          <div class="profile-times">
            <label>${esc(t("profileOpeningTime"))}<span class="input-suffix"><input name="opening_time" type="number" min="1" max="600" step="any" inputmode="decimal" required ${disabled}><span>s</span></span></label>
            <label>${esc(t("profileClosingTime"))}<span class="input-suffix"><input name="closing_time" type="number" min="1" max="600" step="any" inputmode="decimal" required ${disabled}><span>s</span></span></label>
          </div>
          <div id="profile-provenance" class="profile-provenance"></div>
          <p class="muted">${esc(t("profileSharedHelp"))}</p>
          <div class="actions">
            <button type="button" data-profile-action="update">${esc(t("profileUpdate"))}</button>
            <button type="button" class="primary" data-profile-action="new" ${disabled}>${esc(t("profileCreate"))}</button>
          </div>
        </fieldset>
        <p id="profile-usage" class="muted"></p>
        <button type="button" id="profile-delete" class="danger"><ha-icon icon="mdi:delete-outline" aria-hidden="true"></ha-icon><span>${esc(t("profileDelete"))}</span></button>
        <div id="profile-delete-confirmation" class="profile-confirm" hidden>
          <p id="profile-delete-prompt"></p>
          <div class="actions">
            <button type="button" class="danger-solid" data-profile-action="delete">${esc(t("profileDeleteConfirm"))}</button>
            <button type="button" id="profile-delete-cancel">${esc(t("cancel"))}</button>
          </div>
        </div>
        <p id="profile-error" class="error" role="alert" hidden></p>
        <button type="button" id="profile-reload" hidden><ha-icon icon="mdi:reload" aria-hidden="true"></ha-icon><span>${esc(t("profileReload"))}</span></button>
      </form>`;
    host.querySelector("#profile-calibrate").onclick = () => {
      if (this._saving === this._generation || !data.writable || this._stale) return;
      this._calibrating = true;
      const context = this._context;
      this._calibration.open({ ...context, host: host.querySelector("#profile-body"), revision: data.revision,
        onCancel: () => this.open(context), onSaved: () => { context.onSaved(t("saved")); this.open(context); } });
    };
    const form = host.querySelector("#profile-form");
    form.elements.profile.value = data.assigned_profile_id || "";
    const select = () => {
      const profile = data.profiles.find((item) => item.id === form.elements.profile.value);
      form.elements.profile_name.value = profile?.name || "";
      for (const direction of ["opening", "closing"]) {
        form.elements[`${direction}_time`].value = profile?.[`${direction}_time`] ?? profile?.travel_time ?? data.default_travel_time ?? "";
      }
      this._renderProvenance(profile);
      form.querySelector("#profile-delete-confirmation").hidden = true;
      form.querySelector("#profile-delete").hidden = !data.writable || !profile || profile.uses !== 0;
      form.querySelector("#profile-usage").textContent = profile?.uses > 0
        ? `${t("profileInUse")}: ${(profile.assigned_to || []).map((item) => item.name || item.entity_id || t("profileMissingCover")).join(", ") || profile.uses}. ${t("profileDeleteHelp")}`
        : profile ? t("profileUnused") : "";
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

  async _save(action) {
    const { hass, entity, host, onSaved, generation, t } = this._context;
    if (this._saving === generation || !this._data.writable || this._stale) return;
    const form = host.querySelector("#profile-form");
    const savingProfile = action === "new" || action === "update";
    if (savingProfile && !form.reportValidity()) return;
    if (action === "delete") {
      const selected = this._data.profiles.find((item) => item.id === form.elements.profile.value);
      if (!selected || selected.uses !== 0 || form.querySelector("#profile-delete-confirmation").hidden) return;
    }
    const message = {
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
    this._saving = generation;
    const controls = [...form.querySelectorAll("input, select, button")];
    for (const control of controls) control.disabled = true;
    const errorBox = form.querySelector("#profile-error");
    errorBox.hidden = true;
    try {
      const data = await hass.callWS(message);
      if (!this._current(generation)) return;
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
        for (const control of controls) control.disabled = !this._data.writable && control.id !== "profile-reload";
        this._refresh();
      }
    }
  }
}
