/** Gateway profile mutations. Preview is bound to the saved revision and exact draft. */
const url = new URL("panel-dom.js", import.meta.url); url.search = new URL(import.meta.url).search;
const { escapeHtml: esc } = await import(url.href);

export class ProfileCatalogueEditor {
  constructor() { this._generation = 0; }

  close() {
    this._generation++;
    Promise.resolve(this._unsubscribe?.()).catch(() => {});
    this._unsubscribe = null;
    clearInterval(this._poll);
    document.removeEventListener("visibilitychange", this._visibility);
    this.dialog?.close(); this.dialog?.remove(); this.dialog = null;
  }

  _current(generation) { return this._generation === generation && this.dialog?.isConnected; }

  async open(context) {
    this.close();
    this._context = context;
    this._preview = null; this._stale = false; this._busy = false; this._revision = null; this._noticed = -1;
    const generation = this._generation, { host, hass, entryId, profileId, action, t } = context;
    host.innerHTML = `<dialog class="cover-profile-dialog" aria-labelledby="catalogue-title">
      <header class="dialog-head"><h2 id="catalogue-title">${esc(t(`catalogueTitle_${action}`))}</h2></header>
      <div id="catalogue-body"><p>${esc(t("loading"))}</p></div>
      <p id="catalogue-error" class="error" role="alert" hidden></p>
      <button type="button" id="catalogue-reload" hidden>${esc(t("catalogueReload"))}</button>
      <footer class="dialog-foot"><button type="button" id="catalogue-close">${esc(t("cancel"))}</button></footer></dialog>`;
    this.dialog = host.querySelector("dialog"); this.dialog.showModal();
    this.dialog.oncancel = (event) => { event.preventDefault(); this.close(); };
    this.dialog.querySelector("#catalogue-close").onclick = () => this.close();
    this.dialog.querySelector("#catalogue-reload").onclick = () => this.open(context);
    this._visibility = () => { if (!document.hidden) this._check(generation); };
    document.addEventListener("visibilitychange", this._visibility);
    try {
      try {
        const unsubscribe = await hass.connection.subscribeMessage((event) => {
          if (!this._current(generation) || event.entry_id !== entryId) return;
          if (event.kind === "removed") { this.close(); return; }
          if (!Number.isInteger(event.revision)) return;
          this._noticed = Math.max(this._noticed, event.revision);
          if (this._revision !== null && event.revision > this._revision) this._markStale();
        }, { type: "myhome/cover_profiles/subscribe", entry_id: entryId });
        if (!this._current(generation)) { await unsubscribe(); return; }
        this._unsubscribe = unsubscribe;
      } catch {
        if (!this._current(generation)) return;
        this._poll = setInterval(() => { if (!document.hidden) this._check(generation); }, 15000);
      }
      const data = await hass.callWS({ type: "myhome/cover_profiles/overview", entry_id: entryId });
      if (!this._current(generation)) return;
      const profile = data.profiles.find((item) => item.id === profileId);
      if (data.entry_id !== entryId || !profile || !data.capabilities?.profile_management) throw { code: "profile_not_found" };
      this._revision = data.revision;
      this._renderForm(profile);
      if (this._noticed > data.revision) this._markStale();
    } catch (error) {
      if (this._current(generation)) {
        this.dialog.querySelector("#catalogue-body").textContent = "";
        this._error(error); this.dialog.querySelector("#catalogue-reload").hidden = false;
      }
    }
  }

  async _check(generation) {
    if (!this._current(generation) || this._revision === null || this._stale || this._checking === generation) return;
    this._checking = generation;
    try {
      const data = await this._context.hass.callWS({ type: "myhome/cover_profiles/overview", entry_id: this._context.entryId });
      if (this._current(generation) && data.revision !== this._revision) this._markStale();
    } catch {
      if (this._current(generation)) this._markStale();
    } finally { if (this._checking === generation) this._checking = null; }
  }

  _markStale() {
    this._stale = true; this._preview = null;
    this._error({ code: "revision_conflict" });
    this.dialog.querySelector("#catalogue-reload").hidden = false;
    const impact = this.dialog.querySelector("#catalogue-impact");
    if (impact) impact.hidden = true;
    this._controls();
  }

  _renderForm(profile) {
    const { action, t } = this._context;
    const edit = action === "edit", duplicate = action === "duplicate";
    this._inUse = action === "delete" && profile.assigned_to.length > 0;
    this.dialog.querySelector("#catalogue-body").innerHTML = `<h3>${esc(profile.name)}</h3>
      <p class="muted">${profile.assigned_to.length} ${esc(t("profileAssociatedCovers"))} · ${esc(profile.opening_time)} / ${esc(profile.closing_time)} s</p>
      <form id="catalogue-form"><fieldset class="profile-section">
        ${edit || duplicate ? `<label>${esc(t("profileName"))}<input name="profile_name" required maxlength="64" value="${esc(duplicate ? `${profile.name.slice(0, 50)} ${t("catalogueCopySuffix")}` : profile.name)}"></label>` : ""}
        ${edit ? `<div class="profile-times">${["opening", "closing"].map((direction) => `<label>${esc(t(direction === "opening" ? "profileOpeningTime" : "profileClosingTime"))}<input name="${direction}_time" type="number" required min="1" max="600" step="any" value="${esc(profile[`${direction}_time`])}"></label>`).join("")}</div>` : ""}
      </fieldset><p class="muted">${esc(t(edit ? "catalogueEditHelp" : duplicate ? "catalogueDuplicateHelp" : "catalogueDeleteHelp"))}</p>
      <div id="catalogue-impact" class="notice" hidden></div>
      <button type="submit" class="primary" id="catalogue-submit">${esc(t(edit ? "cataloguePreview" : duplicate ? "catalogueDuplicate" : "catalogueDeleteConfirm"))}</button></form>`;
    const form = this.dialog.querySelector("form");
    const invalidate = () => { this._preview = null; this.dialog.querySelector("#catalogue-impact").hidden = true; this._controls(); };
    form.addEventListener("input", invalidate); form.addEventListener("change", invalidate);
    form.onsubmit = (event) => { event.preventDefault(); if (form.reportValidity()) this._submit(); };
    if (this._inUse) this._error({ code: "profile_in_use" });
    this._controls();
  }

  _controls() {
    const form = this.dialog.querySelector("form"); if (!form) return;
    form.querySelector("fieldset").disabled = this._busy;
    form.querySelector('[type="submit"]').disabled = this._busy || this._stale || this._inUse;
    if (this._context.action === "edit") form.querySelector('[type="submit"]').textContent = this._context.t(this._preview ? "catalogueConfirm" : "cataloguePreview");
  }

  _error(error) {
    const { t } = this._context, key = `profileError_${error.code}`;
    const box = this.dialog.querySelector("#catalogue-error"); box.hidden = false;
    box.textContent = t(key) === key ? t("profileError") : t(key);
  }

  _impact(preview) {
    const { t } = this._context, box = this.dialog.querySelector("#catalogue-impact");
    box.innerHTML = `<p><strong>${esc(preview.before.name)} → ${esc(preview.after.name)}</strong></p>
      <p>${esc(preview.before.opening_time)} / ${esc(preview.before.closing_time)} s → ${esc(preview.after.opening_time)} / ${esc(preview.after.closing_time)} s</p>
      ${preview.followers.length ? `<ul>${preview.followers.map((item) => `<li><strong>${esc(item.name || item.entity_id || t("profileMissingCover"))}</strong>
        ${item.available ? "" : ` · ${esc(t("profileUnavailableFollower"))}`}<br>${["opening", "closing"].map((direction) => {
          const change = item.changes[direction];
          return `${esc(t(direction === "opening" ? "profileOpeningTime" : "profileClosingTime"))}: ${esc(change.before)} → ${esc(change.after)} s${change.overridden ? ` · ${esc(t("calPersonalRetained"))}` : ""}`;
        }).join("<br>")}</li>`).join("")}</ul>` : `<p>${esc(t("profileListUnused"))}</p>`}`;
    box.hidden = false;
  }

  async _submit() {
    if (this._busy || this._stale || this._inUse) return;
    const generation = this._generation, { hass, entryId, profileId, action, onSaved } = this._context;
    const form = this.dialog.querySelector("form");
    let message = { type: "myhome/cover_profiles/manage", entry_id: entryId, profile_id: profileId, revision: this._revision };
    if (action === "edit") message = this._preview
      ? { ...this._preview.message, action: "update", confirmation: this._preview.confirmation }
      : { ...message, action: "preview", profile: { name: form.elements.profile_name.value.trim(),
        opening_time: Number(form.elements.opening_time.value), closing_time: Number(form.elements.closing_time.value) } };
    else message = { ...message, action, ...(action === "duplicate" ? { name: form.elements.profile_name.value.trim() } : {}) };
    this._busy = true; this.dialog.querySelector("#catalogue-error").hidden = true; this._controls();
    try {
      const result = await hass.callWS(message);
      if (!this._current(generation)) return;
      if (message.action === "preview") {
        if (this._stale) return;
        this._preview = { message, confirmation: result.confirmation }; this._impact(result);
      } else { this.close(); onSaved(); }
    } catch (error) {
      if (!this._current(generation)) return;
      this._preview = null; this.dialog.querySelector("#catalogue-impact").hidden = true;
      if (["revision_conflict", "profile_not_found", "profile_in_use"].includes(error.code)) this._markStale();
      this._error(error);
    } finally {
      if (this._current(generation)) { this._busy = false; this._controls(); }
    }
  }
}
