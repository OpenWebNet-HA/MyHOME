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
      if (action === "assign" && !data.capabilities?.profile_assignment) throw { code: "invalid_selection" };
      this._revision = data.revision;
      this._renderForm(profile, data);
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

  _address(entityId) {
    const entity = this._context.entities?.find((item) => item.entity_id === entityId && item.entry_id === this._context.entryId);
    return entity && this._context.addressDetails ? this._context.addressDetails(entity) : `<p class="muted">${esc(entityId)}</p>`;
  }

  _selection() {
    return [...this.dialog.querySelectorAll('[name="assignment"]')].filter((input) => input.checked && !input.disabled).map((input) => input.value);
  }

  _candidates(profile, data) {
    const { t } = this._context;
    return `<label>${esc(t("catalogueSearch"))}<input name="assignment_filter" type="search"></label>
      <p id="catalogue-selection-count" class="muted" aria-live="polite"></p>
      <div class="catalogue-candidates">${data.covers.map((cover) => {
        const entity = this._context.entities?.find((item) => item.entity_id === cover.entity_id && item.entry_id === this._context.entryId);
        const address = entity?.address;
        const search = [cover.name, cover.entity_id, address?.raw,
          address?.a == null ? "" : `A:${address.a} A: ${address.a}`,
          address?.pl == null ? "" : `PL:${address.pl} PL: ${address.pl}`].filter(Boolean).join(" ");
        const assigned = cover.profile_id === profile.id;
        const reason = assigned ? t("catalogueAlreadyAssigned") : cover.assignment_reason ? t(`profileError_${cover.assignment_reason}`) : "";
        const current = data.profiles.find((item) => item.id === cover.profile_id)?.name || t("catalogueNoProfile");
        return `<label class="catalogue-candidate" data-search="${esc(search)}"><input type="checkbox" name="assignment" value="${esc(cover.entity_id)}" ${assigned ? "checked" : ""} ${assigned || cover.assignment_reason !== null ? "disabled" : ""}>
          <span><strong>${esc(cover.name)}</strong>${this._address(cover.entity_id)}<span class="muted">${esc(current)}${reason ? ` · ${esc(reason)}` : ""}</span></span></label>`;
      }).join("") || `<p>${esc(t("catalogueNoCovers"))}</p>`}</div>`;
  }

  _renderForm(profile, data) {
    const { action, t } = this._context;
    const edit = action === "edit", duplicate = action === "duplicate", assign = action === "assign";
    this._inUse = action === "delete" && profile.assigned_to.length > 0;
    this.dialog.querySelector("#catalogue-body").innerHTML = `<h3>${esc(profile.name)}</h3>
      <p class="muted">${profile.assigned_to.length} ${esc(t("profileAssociatedCovers"))} · ${esc(profile.opening_time)} / ${esc(profile.closing_time)} s</p>
      <form id="catalogue-form"><fieldset class="profile-section">
        ${assign ? `<p class="notice muted" id="catalogue-unscaled">${esc(t(profile.reference_travel_cm != null ? "profileScalingHelp" : "profileUnscaled"))}</p>` : ""}
        ${assign ? this._candidates(profile, data) : ""}
        ${edit || duplicate ? `<label>${esc(t("profileName"))}<input name="profile_name" required maxlength="64" value="${esc(duplicate ? `${profile.name.slice(0, 50)} ${t("catalogueCopySuffix")}` : profile.name)}"></label>` : ""}
        ${edit ? `<div class="profile-times">${["opening", "closing"].map((direction) => `<label>${esc(t(direction === "opening" ? "profileOpeningTime" : "profileClosingTime"))}<input name="${direction}_time" type="number" required min="1" max="600" step="any" value="${esc(profile[`${direction}_time`])}"></label>`).join("")}</div>` : ""}
        ${edit && data.capabilities?.height_scaling ? `<label>${esc(t("profileReferenceTravel"))}<input name="reference_travel_cm" type="number" min="0.1" max="10000" step="any" inputmode="decimal" value="${esc(profile.reference_travel_cm ?? "")}" aria-describedby="catalogue-reference-help"></label><p class="muted" id="catalogue-reference-help">${esc(t("profileReferenceHelp"))}</p>` : ""}
      </fieldset><p class="muted">${esc(t(assign ? "catalogueAssignHelp" : edit ? "catalogueEditHelp" : duplicate ? "catalogueDuplicateHelp" : "catalogueDeleteHelp"))}</p>
      <div id="catalogue-impact" class="notice" hidden></div>
      <button type="submit" class="primary" id="catalogue-submit">${esc(t(edit ? "cataloguePreview" : duplicate ? "catalogueDuplicate" : "catalogueDeleteConfirm"))}</button></form>`;
    const form = this.dialog.querySelector("form");
    const invalidate = (event) => {
      if (event.target.name === "assignment_filter") {
        const terms = event.target.value.trim().toLocaleLowerCase().split(/\s+/).filter(Boolean);
        form.querySelectorAll(".catalogue-candidate").forEach((row) => { row.hidden = !terms.every((term) => row.dataset.search.toLocaleLowerCase().includes(term)); });
        return;
      }
      this._preview = null; this.dialog.querySelector("#catalogue-impact").hidden = true; this._controls();
    };
    form.addEventListener("input", invalidate); form.addEventListener("change", invalidate);
    form.querySelector('[name="assignment_filter"]')?.addEventListener("keydown", (event) => {
      if (event.key === "Enter") event.preventDefault();
    });
    form.onsubmit = (event) => { event.preventDefault(); if (form.reportValidity()) this._submit(); };
    if (this._inUse) this._error({ code: "profile_in_use" });
    this._controls();
  }

  _controls() {
    const form = this.dialog.querySelector("form"); if (!form) return;
    form.querySelector("fieldset").disabled = this._busy;
    form.querySelector('[type="submit"]').disabled = this._busy || this._stale || this._inUse;
    if (["edit", "assign"].includes(this._context.action)) form.querySelector('[type="submit"]').textContent = this._context.t(this._preview ? "catalogueConfirm" : "cataloguePreview");
    if (this._context.action === "assign") {
      const count = this._selection().length;
      form.querySelector("#catalogue-selection-count").textContent = `${count} ${this._context.t("catalogueSelected")}`;
      form.querySelector('[type="submit"]').disabled ||= count === 0 || count > 200;
    }
  }

  _error(error) {
    const { t } = this._context, key = `profileError_${error.code}`;
    const box = this.dialog.querySelector("#catalogue-error"); box.hidden = false;
    box.textContent = t(key) === key ? t("profileError") : t(key);
  }

  _impact(preview) {
    const { t } = this._context, box = this.dialog.querySelector("#catalogue-impact");
    const assign = this._context.action === "assign", followers = assign ? preview.targets : preview.followers;
    box.innerHTML = `${assign ? `<p><strong>${esc(preview.profile_name)}</strong> · ${followers.length} ${esc(t("catalogueSelected"))}</p>` : `<p><strong>${esc(preview.before.name)} → ${esc(preview.after.name)}</strong></p>
      <p>${esc(preview.before.opening_time)} / ${esc(preview.before.closing_time)} s → ${esc(preview.after.opening_time)} / ${esc(preview.after.closing_time)} s</p>
      ${"reference_travel_cm" in preview.before || "reference_travel_cm" in preview.after ? `<p>${esc(t("profileReferenceTravel"))}: ${esc(preview.before.reference_travel_cm ?? "—")} → ${esc(preview.after.reference_travel_cm ?? "—")} cm</p>` : ""}`}
      ${followers.length ? `<ul>${followers.map((item) => `<li><strong>${esc(item.name || item.entity_id || t("profileMissingCover"))}</strong>
        ${assign ? `${this._address(item.entity_id)}${esc(item.previous_profile_name || t("catalogueNoProfile"))} → ${esc(preview.profile_name)}` : item.available ? "" : ` · ${esc(t("profileUnavailableFollower"))}`}<br>${["opening", "closing"].map((direction) => {
          const change = item.changes[direction];
          return `${esc(t(direction === "opening" ? "profileOpeningTime" : "profileClosingTime"))}: ${esc(change.before)} → ${esc(change.after)} s${change.overridden ? ` · ${esc(t("calPersonalRetained"))}` : ` · ${esc(t(change.scaled ? "profileScaled" : "profileNotScaled"))}`}`;
        }).join("<br>")}</li>`).join("")}</ul>` : `<p>${esc(t("profileListUnused"))}</p>`}`;
    box.hidden = false;
  }

  async _submit() {
    if (this._busy || this._stale || this._inUse) return;
    const generation = this._generation, { hass, entryId, profileId, action, onSaved } = this._context;
    const form = this.dialog.querySelector("form");
    if (action === "assign" && (this._selection().length === 0 || this._selection().length > 200)) return;
    let message = { type: "myhome/cover_profiles/manage", entry_id: entryId, profile_id: profileId, revision: this._revision };
    if (action === "edit") message = this._preview
      ? { ...this._preview.message, action: "update", confirmation: this._preview.confirmation }
      : { ...message, action: "preview", profile: { name: form.elements.profile_name.value.trim(),
        opening_time: Number(form.elements.opening_time.value), closing_time: Number(form.elements.closing_time.value),
        ...(form.elements.reference_travel_cm ? { reference_travel_cm: form.elements.reference_travel_cm.value === "" ? null : Number(form.elements.reference_travel_cm.value) } : {}) } };
    else if (action === "assign") message = this._preview
      ? { ...this._preview.message, action: "assign", confirmation: this._preview.confirmation }
      : { ...message, action: "preview_assign", entity_ids: this._selection() };
    else message = { ...message, action, ...(action === "duplicate" ? { name: form.elements.profile_name.value.trim() } : {}) };
    this._busy = true; this.dialog.querySelector("#catalogue-error").hidden = true; this._controls();
    try {
      const result = await hass.callWS(message);
      if (!this._current(generation)) return;
      if (["preview", "preview_assign"].includes(message.action)) {
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
