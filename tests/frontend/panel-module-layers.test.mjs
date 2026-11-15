/**
 * The panel's modules load layer by layer (`MODULE_LAYERS` in myhome-panel.js), so Safari never hands a module still in
 * its top-level `await` to a second importer: the layers must stay true to the imports. The bus monitor's view and its
 * texts stay out of them, loaded only when the Bus section opens.
 */
import assert from "node:assert/strict";
import { readdirSync, readFileSync } from "node:fs";
import { test } from "node:test";

const PANEL = new URL("../../custom_components/myhome/frontend/panel/", import.meta.url);
const read = (name) => readFileSync(new URL(name, PANEL), "utf8");
const importsOf = (name) => new Set([...read(name).matchAll(/["'](panel-[a-z-]+\.js)["']/g)].map(([, used]) => used));
/** Modules loaded on demand, never at the panel's start. */
const LAZY = ["panel-bus-monitor-view.js", "panel-bus-translations.js"];

function layers() {
  const shell = read("myhome-panel.js");
  const block = shell.slice(shell.indexOf("export const MODULE_LAYERS = ["), shell.indexOf("];", shell.indexOf("export const MODULE_LAYERS")));
  return block.split(/\],\s*\[/).map((layer) => [...layer.matchAll(/"([a-z-]+\.js)"/g)].map(([, name]) => name));
}

test("the panel's modules load layer by layer, each importing only the layers before it", () => {
  const shell = read("myhome-panel.js");
  const modules = readdirSync(PANEL).filter((name) => name.endsWith(".js") && name !== "myhome-panel.js").sort();
  assert.deepEqual([...layers().flat(), ...LAZY].sort(), modules, "every module of the panel, once: in a layer or on demand");
  const layerOf = Object.fromEntries(layers().flatMap((layer, index) => layer.map((name) => [name, index])));
  for (const name of layers().flat()) {
    for (const used of importsOf(name)) {
      if (LAZY.includes(used)) continue;
      assert.ok(layerOf[used] < layerOf[name], `${name} (layer ${layerOf[name]}) imports ${used} (layer ${layerOf[used]})`);
    }
  }
  // The shell itself imports nothing else at the top: no module is asked for by two importers at the same moment.
  assert.equal((shell.match(/import\(/g) || []).length, 1, "one import, in the layer loop");
  assert.match(shell, /for \(const layer of MODULE_LAYERS\) \{\s*const modules = await Promise\.all\(layer\.map\(\(name\) => import\(assetUrl\(name\)\)\)\);/);
});

test("the bus monitor's view and its texts stay loaded on demand, never at the panel's start", () => {
  const shell = read("myhome-panel.js");
  for (const name of LAZY) {
    assert.ok(!layers().flat().includes(name), `${name} is not in a layer`);
    assert.ok(!shell.includes(name), `the shell never names ${name}`);
  }
  // A lazy module imports only modules of the layers or other lazy modules.
  const known = new Set([...layers().flat(), ...LAZY]);
  for (const name of LAZY) for (const used of importsOf(name)) assert.ok(known.has(used), `${name} imports ${used}`);
  // The one layered module that names the view loads it when the Bus section asks for it, not at import time.
  const naming = layers().flat().filter((name) => LAZY.some((lazy) => importsOf(name).has(lazy)));
  assert.deepEqual(naming, ["panel-bus-monitor.js"]);
  const monitor = read("panel-bus-monitor.js");
  assert.match(monitor, /constructor\(loadView = \(\) => import\(viewUrl\.href\)\)/);
  assert.equal((monitor.match(/import\(/g) || []).length, 1, "no other import of the view");
});
