import assert from "node:assert/strict";
import { after, test } from "node:test";
import { JSDOM } from "jsdom";

// Separate file/process: the element must not be defined before these instances
// exist, as can happen while HA's module script awaits its imported assets.
const dom = new JSDOM("<!doctype html><html><body></body></html>", { url: "http://localhost/" });
for (const key of ["window", "document", "HTMLElement", "customElements", "CustomEvent", "Event", "history"]) {
  globalThis[key] = key === "window" ? dom.window : dom.window[key];
}
after(() => { document.body.replaceChildren(); dom.window.close(); });
const tick = () => new Promise((resolve) => setImmediate(resolve));

test("first mount recovers HA properties assigned before definition, including detached and delayed-hass mounts", async () => {
  const fixtures = ["connected", "detached", "delayed-hass"].map((id) => {
    const calls = [];
    const subscriptions = [];
    const hass = {
      language: "it",
      states: { "light.test": { state: "on", attributes: {} } },
      connection: {
        subscribeEvents: async (_callback, type) => {
          const subscription = { type, stopped: false };
          subscriptions.push(subscription);
          return () => { subscription.stopped = true; };
        },
      },
      callWS: async (message) => {
        calls.push(message);
        return {
          version: "2.0.0b9", panel_version: "0.4.3",
          gateways: [{ entry_id: id, title: id, state: "loaded", connected: true }],
          devices: [], areas: [],
          entities: [{ entity_id: "light.test", entry_id: id, domain: "light", who: "1", unique_id: "test" }],
        };
      },
    };
    const element = document.createElement("myhome-panel");
    // Match HA's assignment to a still-undefined custom element.
    element.panel = { config: { panel_version: "0.4.3", bus_card_url: `/card-${id}.js` } };
    if (id !== "delayed-hass") element.hass = hass;
    element.narrow = true;
    if (id !== "detached") document.body.append(element);
    return { id, element, hass, calls, subscriptions };
  });
  assert.equal(customElements.get("myhome-panel"), undefined);
  await import("../../custom_components/myhome/frontend/panel/myhome-panel.js");
  const detached = fixtures[1].element;
  customElements.upgrade(detached);
  document.body.append(detached);
  fixtures[2].element.hass = fixtures[2].hass;
  await tick();

  for (const { element, hass, calls, subscriptions } of fixtures) {
    assert.equal(calls.length, 1, "first mount must load without navigating away");
    assert.equal(calls[0].type, "myhome/panel/inventory");
    assert.equal(element.shadowRoot.querySelectorAll(".item-card").length, 1);
    assert.equal(element.shadowRoot.getElementById("panel-version").textContent, "Pannello v0.4.3");
    for (const property of ["hass", "panel", "narrow"]) assert.equal(Object.hasOwn(element, property), false);
    assert.equal(element.shadowRoot.querySelector("ha-menu-button").narrow, true);
    assert.equal(element._panel.config.bus_card_url, `/card-${element._entryId}.js`);
    element.narrow = false;
    element.hass = { ...hass, states: { "light.test": { state: "off", attributes: {} } } };
    assert.equal(element.shadowRoot.querySelector("ha-menu-button").narrow, false);
    assert.equal(element.shadowRoot.querySelector(".state").textContent, "off");
    assert.equal(calls.length, 1, "ordinary HA updates must not restart the panel");
    assert.equal(subscriptions.length, 3);
    element.remove();
    assert.ok(subscriptions.every((subscription) => subscription.stopped));
  }
  const first = fixtures[0];
  document.body.append(first.element);
  await tick();
  assert.equal(first.calls.length, 2);
  assert.equal(first.element.shadowRoot.querySelectorAll(".item-card").length, 1);
  assert.equal(first.subscriptions.filter((subscription) => !subscription.stopped).length, 3);
});
