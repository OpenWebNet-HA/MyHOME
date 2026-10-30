/**
 * Update a container to new markup in place (M3: no data update or user action rebuilds what is on screen).
 *
 * The new markup is parsed once and the existing nodes are kept wherever they match: same tag at the same place, or the
 * same key (`id`, or `data-entity` with `data-entry`) anywhere among the siblings. Kept nodes keep their scroll position,
 * focus and pointer state; only changed attributes, text and form state are written. Nothing changes when the markup
 * is the same, whatever the browser's serialisation of it.
 */
export function morph(container, html) {
  const template = container.ownerDocument.createElement("template");
  template.innerHTML = html;
  children(container, template.content);
}

const key = (node) => node.nodeType !== 1 ? null
  : node.id || (node.dataset?.entity ? `${node.tagName}|${node.dataset.entry ?? ""}|${node.dataset.entity}` : null);

const same = (a, b) => a.nodeType === b.nodeType && (a.nodeType !== 1 || a.tagName === b.tagName);

function children(from, to) {
  const keyed = new Map();
  for (const node of from.childNodes) { const name = key(node); if (name) keyed.set(name, node); }
  let index = 0;
  for (const next of [...to.childNodes]) {
    const name = key(next), current = from.childNodes[index] || null;
    let match = null;
    if (name) { match = keyed.get(name) || null; if (match && !same(match, next)) match = null; if (match) keyed.delete(name); }
    else if (current && !key(current) && same(current, next)) match = current;
    if (match) {
      if (match !== current) from.insertBefore(match, current);
      update(match, next);
    } else from.insertBefore(next, current);
    index += 1;
  }
  while (from.childNodes.length > index) from.lastChild.remove();
}

function update(from, to) {
  if (from.nodeType !== 1) { if (from.data !== to.data) from.data = to.data; return; }
  for (const { name } of [...from.attributes]) if (!to.hasAttribute(name)) from.removeAttribute(name);
  for (const { name, value } of [...to.attributes]) if (from.getAttribute(name) !== value) from.setAttribute(name, value);
  // A custom element (ha-icon…) owns what it draws: only its attributes are ours.
  if (!from.tagName.includes("-")) children(from, to);
  // Form state lives in properties: the markup decides it.
  if (from.tagName === "INPUT" && (from.type === "checkbox" || from.type === "radio")) from.checked = to.hasAttribute("checked");
  else if (from.tagName === "OPTION") from.selected = to.hasAttribute("selected");
  else if (from.tagName === "SELECT") {
    const chosen = [...from.options].find((option) => option.hasAttribute("selected"));
    if (chosen && from.value !== chosen.value) from.value = chosen.value;
  }
}
