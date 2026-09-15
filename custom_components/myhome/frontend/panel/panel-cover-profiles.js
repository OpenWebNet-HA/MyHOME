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
    const effective = this.dialog.querySelector("#profile-effective");
    if (effective && attrs.travel_time != null) effective.textContent = attrs.travel_time;
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
      <p>${esc(t("profileEffective"))}: <span id="profile-effective">${esc(data.effective_travel_time ?? "—")}</span> s</p>
      <p id="profile-pending" role="status" ${data.pending ? "" : "hidden"}>${esc(t("profilePending"))}</p>
      ${!data.writable ? `<p class="notice">${esc(t(`profileError_${data.reason}`))}</p>` : ""}
      <form id="profile-form">
        <label>${esc(t("profileChoose"))}<select name="profile" ${data.writable ? "" : "disabled"}>
          <option value="">${esc(t("profileDefault"))}</option>
          ${data.profiles.map((profile) => `<option value="${esc(profile.id)}">${esc(profile.name)} · ${esc(profile.travel_time)} s</option>`).join("")}
        </select></label>
        <button type="button" data-profile-action="assign" ${data.writable ? "" : "disabled"}>${esc(t("profileAssign"))}</button>
        <label>${esc(t("profileName"))}<input name="profile_name" maxlength="64" required ${data.writable ? "" : "disabled"}></label>
        <label>${esc(t("profileTravelTime"))}<input name="travel_time" type="number" min="1" max="600" step="any" required ${data.writable ? "" : "disabled"}></label>
        <p class="muted">${esc(t("profileSharedHelp"))}</p>
        <div class="actions">
          <button type="button" data-profile-action="update">${esc(t("profileUpdate"))}</button>
          <button type="button" data-profile-action="new" ${data.writable ? "" : "disabled"}>${esc(t("profileCreate"))}</button>
        </div>
        <p id="profile-error" class="error" role="alert" hidden></p>
        <button type="button" id="profile-reload" hidden>${esc(t("profileReload"))}</button>
      </form>`;
    const form = host.querySelector("#profile-form");
    form.elements.profile.value = data.assigned_profile_id || "";
    const select = () => {
      const profile = data.profiles.find((item) => item.id === form.elements.profile.value);
      form.elements.profile_name.value = profile?.name || "";
      form.elements.travel_time.value = profile?.travel_time ?? data.default_travel_time ?? "";
      form.querySelector('[data-profile-action="update"]').hidden = !data.writable || !profile
        || profile.id !== data.assigned_profile_id || profile.uses > 1;
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
    if (action !== "assign" && !form.reportValidity()) return;
    const message = {
      type: "myhome/cover_profiles/write", entry_id: entity.entry_id, entity_id: entity.entity_id,
      revision: this._data.revision, action: action === "assign" ? "assign" : "save",
      profile_id: action === "new" ? null : form.elements.profile.value || null,
    };
    if (action !== "assign") message.profile = { name: form.elements.profile_name.value.trim(), travel_time: Number(form.elements.travel_time.value) };
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
      onSaved(data.pending ? t("profilePending") : t("saved"));
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
