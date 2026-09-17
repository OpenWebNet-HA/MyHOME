import assert from "node:assert/strict";
import { after, afterEach, test } from "node:test";
import { JSDOM } from "jsdom";
import { ProfileCatalogueEditor } from "../../custom_components/myhome/frontend/panel/panel-profile-catalogue-editor.js";
import { translations } from "../../custom_components/myhome/frontend/panel/panel-translations.js";
const dom = new JSDOM('<!doctype html><body></body>', { pretendToBeVisual: true });
const { document } = dom.window; globalThis.document = document;
dom.window.HTMLDialogElement.prototype.showModal = function () { this.open = true; };
dom.window.HTMLDialogElement.prototype.close = function () { this.open = false; };
const tick = () => new Promise(resolve => setImmediate(resolve));
const deferred = () => { let resolve; const promise = new Promise(done => { resolve = done; }); return { promise, resolve }; };
const t = key => translations.it[key] || translations.en[key] || key;
const instances = [];
const profile = { id: 'shared', name: 'Alluminio', opening_time: 20, closing_time: 30, assigned_to: ['cover.one'] };
const overview = (extra = {}) => ({ entry_id: 'one', revision: 4, capabilities: { profile_management: true }, profiles: [profile], ...extra });
const impact = { before: profile, after: { ...profile, opening_time: 22 }, confirmation: 'proposal', followers: [
  { name: '<b>Kitchen</b>', entity_id: 'cover.one', available: true, changes: { opening: { before: 12, after: 12, overridden: true }, closing: { before: 30, after: 30 } } },
  { name: null, entity_id: null, available: false, changes: { opening: { before: 20, after: 22 }, closing: { before: 30, after: 30 } } },
] };
async function mount({ action = 'edit', read, call, subscribe } = {}) {
  const host = document.createElement('div'); document.body.append(host);
  const editor = new ProfileCatalogueEditor(); instances.push(editor);
  const calls = []; let event, saved = 0, stopped = 0;
  const context = { host, entryId: 'one', profileId: 'shared', action, t, onSaved: () => saved++, hass: {
    connection: { subscribeMessage: async (cb) => { event = cb; return subscribe ? subscribe(() => stopped++) : () => stopped++; } },
    callWS: async message => { calls.push(message); if (message.type.endsWith('/overview')) return read ? read(message) : overview();
      return call ? call(message) : message.action === 'preview' ? impact : { revision: 5 }; },
  } };
  await editor.open(context);
  return { host, editor, calls, context, event: (...args) => event(...args), counts: () => ({ saved, stopped }) };
}
const submit = host => host.querySelector('form').dispatchEvent(new dom.window.Event('submit', { cancelable: true }));
const input = (host, name, value) => { const field = host.querySelector(`[name="${name}"]`); field.value = value; field.dispatchEvent(new dom.window.Event('input', { bubbles: true })); };
afterEach(() => { instances.splice(0).forEach(editor => editor.close()); document.body.replaceChildren(); delete document.hidden; });
after(() => dom.window.close());

test('edit previews every follower and requires separate confirmation bound to exact profile and revision', async () => {
  const { host, calls, counts } = await mount(); input(host, 'opening_time', '22');
  submit(host); await tick();
  assert.equal(calls.at(-1).action, 'preview');
  assert.equal(host.querySelector('#catalogue-impact').hidden, false);
  assert.equal(host.querySelector('#catalogue-impact b'), null);
  assert.match(host.querySelector('#catalogue-impact').textContent, /valore personale conservato/);
  assert.match(host.querySelector('#catalogue-submit').textContent, /Conferma/);
  assert.equal(counts().saved, 0);
  submit(host); await tick();
  assert.deepEqual(calls.at(-1), { type: 'myhome/cover_profiles/manage', entry_id: 'one', profile_id: 'shared', revision: 4,
    action: 'update', profile: { name: 'Alluminio', opening_time: 22, closing_time: 30 }, confirmation: 'proposal' });
  assert.equal(counts().saved, 1); assert.equal(host.querySelector('dialog'), null);
});

test('changing the draft invalidates its preview and cannot submit the old confirmation', async () => {
  const { host, calls } = await mount(); submit(host); await tick();
  input(host, 'profile_name', 'New name');
  assert.equal(host.querySelector('#catalogue-impact').hidden, true);
  submit(host); await tick(); assert.equal(calls.at(-1).action, 'preview');
  assert.equal(calls.at(-1).profile.name, 'New name');
});

test('duplicate submits only a new name and never sends assignments, times or evidence', async () => {
  const { host, calls, counts } = await mount({ action: 'duplicate' });
  input(host, 'profile_name', 'Independent'); submit(host); await tick();
  assert.deepEqual(calls.at(-1), { type: 'myhome/cover_profiles/manage', entry_id: 'one', profile_id: 'shared', revision: 4, action: 'duplicate', name: 'Independent' });
  assert.equal(counts().saved, 1);
});

test('delete requires confirmation and in-use profiles cannot be submitted', async () => {
  const used = await mount({ action: 'delete' });
  assert.equal(used.host.querySelector('#catalogue-submit').disabled, true);
  assert.match(used.host.querySelector('#catalogue-error').textContent, /utilizzato|uso|assegnato/);
  const unused = await mount({ action: 'delete', read: () => overview({ profiles: [{ ...profile, assigned_to: [] }] }) });
  assert.equal(unused.calls.length, 1);
  submit(unused.host); await tick(); assert.equal(unused.calls.at(-1).action, 'delete');
});

test('remote revisions preserve the draft and prevent confirmation of a late preview', async () => {
  const pending = deferred();
  const { host, calls, event } = await mount({ call: () => pending.promise });
  input(host, 'profile_name', 'My draft'); submit(host);
  event({ entry_id: 'other', revision: 9 });
  event({ entry_id: 'one', revision: 5 });
  pending.resolve(impact); await tick();
  assert.equal(host.querySelector('[name="profile_name"]').value, 'My draft');
  assert.equal(host.querySelector('#catalogue-impact').hidden, true);
  assert.equal(host.querySelector('#catalogue-submit').disabled, true);
  assert.equal(host.querySelector('#catalogue-reload').hidden, false);
  assert.equal(calls.filter(x => x.action === 'update').length, 0);
});

test('storage failures retain editable draft but require a new preview', async () => {
  const { host, calls } = await mount({ call: message => { if (message.action === 'preview') return impact; throw { code: 'storage_error' }; } });
  input(host, 'opening_time', '25'); submit(host); await tick(); submit(host); await tick();
  assert.equal(host.querySelector('[name="opening_time"]').value, '25');
  assert.equal(host.querySelector('#catalogue-impact').hidden, true);
  assert.equal(host.querySelector('#catalogue-submit').disabled, false);
  assert.match(host.querySelector('#catalogue-error').textContent, /Salvataggio fallito/);
  submit(host); await tick(); assert.equal(calls.at(-1).action, 'preview');
});

test('stale revision or new association requires explicit reload after backend rejection', async () => {
  const { host } = await mount({ action: 'delete', read: () => overview({ profiles: [{ ...profile, assigned_to: [] }] }),
    call: () => { throw { code: 'profile_in_use' }; } });
  submit(host); await tick();
  assert.equal(host.querySelector('#catalogue-submit').disabled, true);
  assert.equal(host.querySelector('#catalogue-reload').hidden, false);
});

test('late accepted save cannot close or notify a replacement dialog', async () => {
  const pending = deferred(); const original = await mount({ action: 'duplicate', call: () => pending.promise });
  submit(original.host); original.editor.close();
  const next = await mount(); pending.resolve({ revision: 5 }); await tick();
  assert.equal(original.counts().saved, 0); assert.equal(next.host.querySelector('dialog').open, true);
});

test('Escape releases subscriptions; removed gateway closes the editor', async () => {
  const first = await mount(); first.host.querySelector('dialog').dispatchEvent(new dom.window.Event('cancel', { cancelable: true }));
  await tick(); assert.equal(first.counts().stopped, 1); assert.equal(first.host.querySelector('dialog'), null);
  const next = await mount(); next.event({ entry_id: 'one', kind: 'removed' }); await tick();
  assert.equal(next.host.querySelector('dialog'), null);
});

test('fallback and visibility checks detect missed changes without replacing drafts', async () => {
  let data = overview();
  const { host, editor } = await mount({ read: () => data, subscribe: () => { throw Error('offline'); } });
  input(host, 'profile_name', 'Keep this'); data = overview({ revision: 6 });
  await editor._check(editor._generation);
  assert.equal(host.querySelector('[name="profile_name"]').value, 'Keep this');
  assert.equal(host.querySelector('#catalogue-submit').disabled, true);
});

const assignOverview = () => overview({ capabilities: { profile_management: true, profile_assignment: true }, profiles: [profile, { ...profile, id: 'old', name: 'Legno' }], covers: [
  { entity_id: 'cover.one', name: 'Già presente', profile_id: 'shared', assignment_reason: null },
  { entity_id: 'cover.two', name: '<img src=x> Cucina', profile_id: 'old', assignment_reason: null },
  { entity_id: 'cover.three', name: 'Camera', profile_id: null, assignment_reason: null },
  { entity_id: 'cover.offline', name: 'Offline', profile_id: null, assignment_reason: 'cover_unavailable' },
  { entity_id: 'cover.advanced', name: 'Advanced', profile_id: null, assignment_reason: 'advanced_cover' },
] });
const assignedPreview = { profile_name: 'Alluminio', confirmation: 'assign-token', targets: [
  { entity_id: 'cover.two', name: '<img src=x> Cucina', previous_profile_name: 'Legno', changes: { opening: { before: 12, after: 12, overridden: true }, closing: { before: 45, after: 30 } } },
  { entity_id: 'cover.three', name: 'Camera', previous_profile_name: null, changes: { opening: { before: 30, after: 20 }, closing: { before: 30, after: 30 } } },
] };
const select = (host, id, checked = true) => {
  const field = host.querySelector(`[name="assignment"][value="${id}"]`); field.checked = checked;
  field.dispatchEvent(new dom.window.Event('change', { bubbles: true }));
};

test('multi-cover assignment displays eligibility and preview, then confirms the exact selection', async () => {
  const { host, calls, counts } = await mount({ action: 'assign', read: assignOverview,
    call: message => message.action === 'preview_assign' ? assignedPreview : { revision: 5 } });
  assert.match(host.querySelector('#catalogue-unscaled').textContent, /senza adattamento all’altezza/);
  assert.equal(host.querySelector('#catalogue-submit').disabled, true);
  for (const id of ['cover.one', 'cover.offline', 'cover.advanced']) assert.equal(host.querySelector(`[value="${id}"]`).disabled, true);
  assert.equal(host.querySelector('img'), null);
  select(host, 'cover.two'); select(host, 'cover.three');
  assert.match(host.querySelector('#catalogue-selection-count').textContent, /^2 /);
  submit(host); await tick();
  assert.deepEqual(calls.at(-1).entity_ids, ['cover.two', 'cover.three']);
  assert.equal(calls.at(-1).action, 'preview_assign');
  assert.equal(counts().saved, 0);
  assert.match(host.querySelector('#catalogue-impact').textContent, /Legno → Alluminio/);
  assert.equal(host.querySelector('#catalogue-unscaled').hidden, false);
  assert.match(host.querySelector('#catalogue-impact').textContent, /valore personale conservato/);
  assert.equal(host.querySelector('#catalogue-impact img'), null);
  submit(host); await tick();
  assert.equal(calls.at(-1).action, 'assign'); assert.equal(calls.at(-1).confirmation, 'assign-token');
  assert.deepEqual(calls.at(-1).entity_ids, ['cover.two', 'cover.three']);
  assert.equal(counts().saved, 1);
});

test('search retains hidden selections, selection changes invalidate preview and empty selection cannot submit', async () => {
  const { host, calls } = await mount({ action: 'assign', read: assignOverview, call: () => assignedPreview });
  submit(host); await tick(); assert.equal(calls.length, 1);
  select(host, 'cover.two'); submit(host); await tick();
  input(host, 'assignment_filter', 'Camera');
  assert.equal(host.querySelector('[value="cover.two"]').closest('label').hidden, true);
  assert.equal(host.querySelector('#catalogue-impact').hidden, false);
  select(host, 'cover.three'); assert.equal(host.querySelector('#catalogue-impact').hidden, true);
  submit(host); await tick(); assert.equal(calls.at(-1).action, 'preview_assign');
  assert.deepEqual(calls.at(-1).entity_ids, ['cover.two', 'cover.three']);
});

test('failed assignment keeps selection and requires another preview', async () => {
  const { host, calls } = await mount({ action: 'assign', read: assignOverview,
    call: message => { if (message.action === 'preview_assign') return assignedPreview; throw { code: 'cover_unavailable' }; } });
  select(host, 'cover.two'); submit(host); await tick(); submit(host); await tick();
  assert.equal(host.querySelector('[value="cover.two"]').checked, true);
  assert.equal(host.querySelector('#catalogue-impact').hidden, true);
  assert.match(host.querySelector('#catalogue-error').textContent, /gateway/);
  submit(host); await tick(); assert.equal(calls.at(-1).action, 'preview_assign');
});

test('remote changes during assignment preview cannot enable confirmation', async () => {
  const pending = deferred();
  const { host, event } = await mount({ action: 'assign', read: assignOverview, call: () => pending.promise });
  select(host, 'cover.two'); submit(host); event({ entry_id: 'one', revision: 5 });
  pending.resolve(assignedPreview); await tick();
  assert.equal(host.querySelector('#catalogue-submit').disabled, true);
  assert.equal(host.querySelector('[value="cover.two"]').checked, true);
  assert.equal(host.querySelector('#catalogue-impact').hidden, true);
});

test('assignment search accepts spaced A-PL terms and Enter never confirms an assignment', async () => {
  const { host, editor, context, calls } = await mount({ action: 'assign', read: assignOverview, call: () => assignedPreview });
  context.entities = [{ entity_id: 'cover.two', entry_id: 'one', address: { raw: '12', a: '1', pl: '2' } }];
  context.addressDetails = () => '<dl><dt>A:</dt><dd>1</dd><dt>PL:</dt><dd>2</dd></dl>';
  await editor.open(context);
  input(host, 'assignment_filter', 'A: 1 PL: 2');
  assert.equal(host.querySelector('[value="cover.two"]').closest('label').hidden, false);
  assert.equal(host.querySelector('[value="cover.three"]').closest('label').hidden, true);
  select(host, 'cover.two'); submit(host); await tick();
  const before = calls.length;
  const enter = new dom.window.KeyboardEvent('keydown', { key: 'Enter', bubbles: true, cancelable: true });
  host.querySelector('[name="assignment_filter"]').dispatchEvent(enter);
  assert.equal(enter.defaultPrevented, true);
  assert.equal(calls.length, before);
  assert.equal(host.querySelector('#catalogue-impact').hidden, false);
});

test('Enter in the assignment search field cannot confirm an existing preview', async () => {
  const { host, calls, counts } = await mount({ action: 'assign', read: assignOverview,
    call: message => message.action === 'preview_assign' ? assignedPreview : { revision: 5 } });
  select(host, 'cover.two'); submit(host); await tick();
  const enter = new dom.window.KeyboardEvent('keydown', { key: 'Enter', bubbles: true, cancelable: true });
  host.querySelector('[name="assignment_filter"]').dispatchEvent(enter);
  assert.equal(enter.defaultPrevented, true);
  assert.equal(calls.some(item => item.action === 'assign'), false);
  assert.equal(counts().saved, 0);
  host.querySelector('#catalogue-submit').click(); await tick();
  assert.equal(calls.at(-1).action, 'assign');
  assert.equal(counts().saved, 1);
});
