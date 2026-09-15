/** Single-cover profile editor. Navigation/connection changes invalidate all UI work. */
const url = new URL("panel-dom.js", import.meta.url);
url.search = new URL(import.meta.url).search;
const { escapeHtml: esc } = await import(url.href);

export class CoverProfileEditor {
  constructor() { this._generation = 0; }

  close() {
    this._generation++;
    this.dialog?.close();
    this.dialog?.remove();
    this.dialog = null;
  }

  async open({ host, hass, entity, t, onSaved }) {
    this.close();
    const generation = this._generation;
    this._context = { host, hass, entity, t, onSaved, generation };
    host.innerHTML = `<dialog class="cover-profile-dialog" aria-labelledby="profile-title">
      <h2 id="profile-title">${esc(t("coverProfiles"))}</h2>
      <p>${esc(entity.name || entity.original_name || entity.entity_id)}</p>
      <div id="profile-body"><p role="status">${esc(t("loading"))}</p></div>
      <div class="actions"><button type="button" id="profile-close">${esc(t("cancel"))}</button></div>
    </dialog>`;
    this.dialog = host.querySelector("dialog");
    host.querySelector("#profile-close").onclick = () => this.close();
    this.dialog.oncancel = (event) => { event.preventDefault(); this.close(); };
    this.dialog.showModal();
    try {
      const data = await hass.callWS({ type: "myhome/cover_profiles/read", entry_id: entity.entry_id, entity_id: entity.entity_id });
      if (!this._current(generation)) return;
      this._data = data;
      this._render();
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
  _error(error) {
    const { t } = this._context;
    const key = `profileError_${error.code}`;
    return t(key) === key ? t("profileError") : t(key);
  }

  _render() {
    const { host, t } = this._context;
    const data = this._data;
    const assigned = data.profiles.find((profile) => profile.id === data.assigned_profile_id);
    host.querySelector("#profile-body").innerHTML = `
      <p class="muted">${esc(t("profileScope"))}</p>
      <p>${esc(t("profileAssigned"))}: <strong>${esc(assigned?.name || t("profileDefault"))}</strong></p>
      <p>${esc(t("profileEffectiveOpening"))}: <span id="profile-effective-opening">${esc(data.effective_opening_time ?? data.effective_travel_time ?? "—")}</span> s</p>
      <p>${esc(t("profileEffectiveClosing"))}: <span id="profile-effective-closing">${esc(data.effective_closing_time ?? data.effective_travel_time ?? "—")}</span> s</p>
      <p id="profile-pending" role="status" ${data.pending ? "" : "hidden"}>${esc(t("profilePending"))}</p>
      ${!data.writable ? `<p class="notice">${esc(t(`profileError_${data.reason}`))}</p>` : ""}
      <form id="profile-form">
        <label>${esc(t("profileChoose"))}<select name="profile" ${data.writable ? "" : "disabled"}>
          <option value="">${esc(t("profileDefault"))}</option>
          ${data.profiles.map((profile) => `<option value="${esc(profile.id)}">${esc(profile.name)} · ${esc(profile.opening_time ?? profile.travel_time)} / ${esc(profile.closing_time ?? profile.travel_time)} s</option>`).join("")}
        </select></label>
        <button type="button" data-profile-action="assign" ${data.writable ? "" : "disabled"}>${esc(t("profileAssign"))}</button>
        <label>${esc(t("profileName"))}<input name="profile_name" maxlength="64" required ${data.writable ? "" : "disabled"}></label>
        <label>${esc(t("profileOpeningTime"))}<input name="opening_time" type="number" min="1" max="600" step="any" required ${data.writable ? "" : "disabled"}></label>
        <label>${esc(t("profileClosingTime"))}<input name="closing_time" type="number" min="1" max="600" step="any" required ${data.writable ? "" : "disabled"}></label>
        <p class="muted">${esc(t("profileSharedHelp"))}</p>
        <div class="actions">
          <button type="button" data-profile-action="update">${esc(t("profileUpdate"))}</button>
          <button type="button" data-profile-action="new" ${data.writable ? "" : "disabled"}>${esc(t("profileCreate"))}</button>
        </div>
        <p id="profile-usage" class="muted"></p>
        <button type="button" id="profile-delete">${esc(t("profileDelete"))}</button>
        <div id="profile-delete-confirmation" hidden>
          <p id="profile-delete-prompt"></p>
          <div class="actions">
            <button type="button" data-profile-action="delete">${esc(t("profileDeleteConfirm"))}</button>
            <button type="button" id="profile-delete-cancel">${esc(t("cancel"))}</button>
          </div>
        </div>
        <p id="profile-error" class="error" role="alert" hidden></p>
        <button type="button" id="profile-reload" hidden>${esc(t("profileReload"))}</button>
      </form>`;
    const form = host.querySelector("#profile-form");
    form.elements.profile.value = data.assigned_profile_id || "";
    const select = () => {
      const profile = data.profiles.find((item) => item.id === form.elements.profile.value);
      form.elements.profile_name.value = profile?.name || "";
      for (const direction of ["opening", "closing"]) {
        form.elements[`${direction}_time`].value = profile?.[`${direction}_time`] ?? profile?.travel_time ?? data.default_travel_time ?? "";
      }
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
    form.elements.profile.onchange = select;
    form.onsubmit = (event) => event.preventDefault();
    for (const button of form.querySelectorAll("[data-profile-action]")) button.onclick = () => this._save(button.dataset.profileAction);
    host.querySelector("#profile-reload").onclick = () => this.open(this._context);
  }

  async _save(action) {
    const { hass, entity, host, onSaved, generation, t } = this._context;
    if (this._saving === generation || !this._data.writable) return;
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
      }
    }
  }
}
