/** Illustrations of backend-owned steps, never an estimate of the live position. */
export function calibrationScene(state, lost = false) {
  const scene = (shape, label, icon = "", measure = false) => ({ shape, label: `calVisual_${label}`, icon, measure });
  if (lost) return scene("neutral", "disconnected", "mdi:connection");
  const { phase, step, mode } = state;
  if (["interrupted", "cancelled"].includes(phase)) return scene("neutral", "interrupted", "mdi:stop-circle-outline");
  if (["review", "saving", "saved"].includes(phase)) return scene("neutral", "review", "mdi:clipboard-check-outline");
  if (phase === "geometry_wait_stop") return scene("neutral", "waitStop", "mdi:timer-sand");
  if (phase.startsWith("starting_")) return scene("neutral", "starting", "mdi:timer-sand");
  if (["settling", "between_covers"].includes(phase)) return scene("neutral", "wait", "mdi:pause");
  if (phase === "confirm_automatic") return scene("neutral", "automatic", "mdi:swap-vertical");
  if (mode === "geometry") {
    if (phase === "reading") {
      if (state.reading_kind === "lift") return scene("gap", "measureGap", "", true);
      if (state.reading_kind === "travel") return scene("open", "measureTravel", "", true);
      if (["half_open", "half_close"].includes(state.reading_kind)) return scene("middle", "measureHeight", "", true);
    }
    if (phase === "briefing") {
      if (["home", "reset", "closing"].includes(step)) return scene("closed", "targetClosed");
      if (["opening", "top"].includes(step)) return scene("open", "targetOpen");
      if (step === "lift") return scene("slats", "lift");
      if (["half_open", "half_close"].includes(step)) return scene("middle", "intermediate");
    }
    if (phase === "opening" && step === "lift") return scene("slats", "lift", "mdi:arrow-up-bold");
    if (["opening", "closing"].includes(phase) && ["half_open", "half_close"].includes(step)) {
      return scene("middle", "autoStop", phase === "opening" ? "mdi:arrow-up-bold" : "mdi:arrow-down-bold");
    }
  }
  if (phase === "confirm_closed") return scene("closed", "confirmClosed");
  if (phase === "confirm_open") return scene("open", "confirmOpen");
  if (["opening", "closing"].includes(phase)) return scene("middle", mode === "automatic" ? "autoMoving" : phase,
    phase === "opening" ? "mdi:arrow-up-bold" : "mdi:arrow-down-bold");
  return null;
}

export function visualMarkup() {
  return `<figure class="cal-visual" hidden>
    <div class="cal-visual-drawing" aria-hidden="true">
      <div class="cal-visual-frame"><div class="cal-visual-curtain"></div></div>
      <div class="cal-visual-box"></div><div class="cal-visual-base"></div>
      <div class="cal-visual-focus"></div>
      <div class="cal-visual-measure" hidden><span>cm</span></div>
      <ha-icon class="cal-visual-icon" hidden></ha-icon>
    </div>
    <figcaption><strong class="cal-visual-label" role="status"></strong><span class="cal-visual-caption muted"></span></figcaption>
  </figure>`;
}

export function renderCalibrationVisual(host, state, t, lost) {
  const figure = host.querySelector(".cal-visual"), scene = calibrationScene(state, lost);
  figure.hidden = !scene;
  if (!scene) return;
  figure.dataset.scene = scene.shape;
  // No timers or per-frame DOM writes: heartbeats must not restart motion or announcements.
  const label = figure.querySelector(".cal-visual-label"), text = t(scene.label);
  if (label.textContent !== text) label.textContent = text;
  const caption = figure.querySelector(".cal-visual-caption"), hint = t("calVisualIllustration");
  if (caption.textContent !== hint) caption.textContent = hint;
  const icon = figure.querySelector(".cal-visual-icon");
  icon.hidden = !scene.icon;
  if (scene.icon) icon.setAttribute("icon", scene.icon);
  else icon.removeAttribute("icon");
  figure.querySelector(".cal-visual-measure").hidden = !scene.measure;
}
