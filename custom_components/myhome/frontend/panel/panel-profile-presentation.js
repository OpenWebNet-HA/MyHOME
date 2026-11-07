/** Display saved profile evidence without deriving it from editable drafts. */
const url = new URL("panel-dom.js", import.meta.url); url.search = new URL(import.meta.url).search;
const { escapeHtml: esc, decimal } = await import(url.href);

/** Display only, to a tenth (a decimal comma in Italian); stored values keep full precision. */
export function duration(value, language = "en") {
  return Number.isFinite(value) ? decimal(value, 1, language) : "—";
}

export function savedEvidence(profile, t, language = "en") {
  return `<div class="profile-evidence-compact">${["opening", "closing"].map((direction) => {
    const evidence = profile?.provenance?.[direction];
    const source = !profile ? "configured" : ["manual", "guided", "automatic"].includes(evidence?.source) ? evidence.source : "unknown";
    let date = "";
    if (["manual", "guided", "automatic"].includes(source)) {
      date = t("profileDateUnknown");
      if (evidence?.recorded_at && !Number.isNaN(Date.parse(evidence.recorded_at))) {
        try { date = new Intl.DateTimeFormat(language, { dateStyle: "medium", timeStyle: "short" }).format(new Date(evidence.recorded_at)); }
        catch { date = new Date(evidence.recorded_at).toISOString(); }
      }
    }
    return `<p><span class="profile-evidence-direction">${esc(t(direction === "opening" ? "calStepOpening" : "calStepClosing"))}:</span>
      ${esc(t(`profileSource_${source}`))}${evidence?.inherited ? ` · ${esc(t("profileEvidenceInherited"))}` : ""}${date ? ` · ${esc(date)}` : ""}</p>`;
  }).join("")}</div>`;
}

export function usageLabel(count, t) {
  return `${count} ${t(count === 1 ? "profileCoverSingular" : "profileAssociatedCovers")}`;
}
