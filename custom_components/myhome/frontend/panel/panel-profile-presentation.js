/** Display saved profile evidence without deriving it from editable drafts. */
const url = new URL("panel-dom.js", import.meta.url); url.search = new URL(import.meta.url).search;
const { escapeHtml: esc } = await import(url.href);

export function duration(value, language = "en") {
  if (!Number.isFinite(value)) return "—";
  try { return new Intl.NumberFormat(language, { maximumFractionDigits: 20, useGrouping: false }).format(value); }
  catch { return String(value); }
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
