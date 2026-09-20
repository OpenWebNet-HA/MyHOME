/** Geometry fields and readouts; all physical calculations stay in the backend. */
const url = new URL("panel-dom.js", import.meta.url); url.search = new URL(import.meta.url).search;
const { escapeHtml: esc } = await import(url.href);
export const geometryKeys = ["slat_time_s", "opening_roll", "closing_roll"];
const number = (value) => typeof value === "number" ? Number(value.toFixed(4)) : "—";
export function scalingText(profile, t) {
  const key = profile?.reference_travel_cm != null ? "profileScalingHelp" : "profileUnscaled";
  return t(profile?.geometry ? `${key}Nonlinear` : key);
}

export function geometryFields(t) {
  return `<div class="profile-geometry"><label>${esc(t("profileMotionModel"))}<select name="motion_model"><option value="linear_time">${esc(t("profileModel_linear_time"))}</option><option value="slat_roll">${esc(t("profileModel_slat_roll"))}</option></select></label>
    <details class="profile-meta"><summary>${esc(t("profileMeasurementOptions"))}</summary><p class="muted">${esc(t("profileGeometryHelp"))}</p></details><div data-geometry-fields class="profile-times" hidden>${geometryKeys.map((key) => `<label>${esc(t(`profileGeometry_${key}`))}<input name="${key}" type="number" required min="${key === "slat_time_s" ? 0 : 1}" max="${key === "slat_time_s" ? 600 : 5}" step="any" inputmode="decimal" disabled></label>`).join("")}</div></div>`;
}

export function geometryControls(form, writable = true) {
  const select = form.elements.motion_model; if (!select) return;
  const nonlinear = select.value === "slat_roll";
  select.disabled = !writable;
  form.querySelector("[data-geometry-fields]").hidden = !nonlinear;
  geometryKeys.forEach((key) => { form.elements[key].disabled = !writable || !nonlinear; });
}

export function fillGeometry(form, profile, writable = true) {
  if (!form.elements.motion_model) return;
  form.elements.motion_model.value = profile?.geometry ? "slat_roll" : "linear_time";
  geometryKeys.forEach((key) => { form.elements[key].value = profile?.geometry?.[key] ?? ""; });
  geometryControls(form, writable);
}

export function readGeometry(form) {
  if (!form.elements.motion_model) return {};
  return { geometry: form.elements.motion_model.value === "slat_roll"
    ? Object.fromEntries(geometryKeys.map((key) => [key, Number(form.elements[key].value)])) : null };
}

export function validGeometry(form) {
  return !form.elements.motion_model || form.elements.motion_model.value !== "slat_roll"
    || geometryKeys.every((key) => form.elements[key].value !== "" && form.elements[key].reportValidity());
}

export function geometrySummary(settings, t, known = true) {
  if (!settings?.slat_time_s) return "";
  return `${esc(t("profileModel_slat_roll"))} · ${geometryKeys.map((key) => `${esc(t(`profileGeometry_${key}`))}: ${esc(number(settings[key]?.value))}`).join(" · ")}. ${esc(t("profileAccuracyUnknown"))}${known ? "" : ` ${esc(t("profileMotionUnknown"))}`}`;
}

export function geometryImpact(item, t) {
  if (!item.geometry_changes) return "";
  return `<p class="muted">${esc(t(`profileModel_${item.model_before}`))} → ${esc(t(`profileModel_${item.model_after}`))}<br>${geometryKeys.map((key) => `${esc(t(`profileGeometry_${key}`))}: ${esc(number(item.geometry_changes[key].before))} → ${esc(number(item.geometry_changes[key].after))}`).join(" · ")}</p>`;
}
