/** Shared rendering helpers for panel sections. All interpolated data is escaped. */
export const escapeHtml = (value) => String(value ?? "").replace(/[&<>"']/g, (char) => ({
  "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
})[char]);

const focusKey = (element) => JSON.stringify([
  element.tagName, element.dataset.action, element.dataset.id,
  element.dataset.group, element.getAttribute("href"),
  element.closest(".who-group")?.dataset.who,
  element.closest(".device-group")?.dataset.entry,
  element.closest(".device-group")?.dataset.device,
]);

/** Restore the same inventory action, never a different device or gateway. */
export function replacePreservingFocus(container, html) {
  const active = container.getRootNode().activeElement;
  const key = active && container.contains(active) ? focusKey(active) : null;
  container.innerHTML = html;
  if (key === null) return;
  const replacement = [...container.querySelectorAll("button, a")].find((element) =>
    focusKey(element) === key && !element.closest("[hidden]"));
  replacement?.focus({ preventScroll: true });
}
