# Shared MyHOME panel contract — implementation and proposal

**Updated: 2026-09-17, panel 0.29.0.** The shared backend now includes:
storage v7 (optional reference/per-cover travel; manual edits without a fictitious origin), migration of native v2 fallbacks, one directional resolver, native
service persistence through the shared transaction, gateway-wide read API,
per-direction personal overrides and confirmed shared-profile impact previews.
Single-cover measurement review now supports new profiles, personal values and
confirmed assigned-profile updates; see [save semantics](sidepanel.md#measurement-save-destinations-0230).
Panel 0.25.0 adds a [shared profile overview](sidepanel.md#shared-profile-view-0250)
inside WHO 2, reusing the same shell and cover editor. It shows the one-to-many
relationship without adding a mandatory step to single-cover calibration. Direct
profile-level editing, duplication and deletion are implemented in 0.26.0 through
[the gateway management endpoint](panel-websocket-api.md#gateway-profile-management-0260),
with exact-proposal confirmation and unchanged personal overrides. Version 0.27.0
adds atomic multi-cover assignment from the same profile view, with complete
selection validation and a single confirmed transaction.
Version 0.28.0 separates closed-client lifetime from gateway reservation until
Stop/guard completion. Version 0.29.0 adds the contract's optional linear height
scaling, preserving overrides and using the same resolver for runtime and
previews. See [dimensions, normalization, migration and export v4](cover-travel-scaling.md).
Slat/roll fitting and physical accuracy checks remain future work.
See [implemented schema, migration, API and limits](cover-settings-backend.md).
The agreed proposal is pinned to [51ffaf7](https://github.com/Interstellar0verdrive/MyHOME-stability/blob/51ffaf73a3da3ac09246ee199751e613217e0a78/docs/calibration-contract-proposal.md).
This is a review of that document; the historical fork-code comparison below stays
pinned to `229b1eb` and is not a fresh audit of its runtime.

Shared-backend baseline: [`166b923`](https://github.com/xtimmy86x/MyHOME/commit/166b92324b714ad588f280ed1bd816d8e73c6017).
The preceding implementation was panel 0.21.0 at [`d40f02f`](https://github.com/xtimmy86x/MyHOME/commit/d40f02ff11d9bdbd34adcd870afa12cef0d71f0d), following `e472ff4`.
The integrated v2 baseline is now `1dc4b3e` (2026-09-17), including FrameRouter,
OWNd 2.0.0b7, delivery-failure handling and broadcast resync. The previous
command-session signature and WHO=13 ambiguous F454 identification fixes remain included. Storage v5 and export v3 supersede
the v4/v2 descriptions in the historical comparison below. Panel 0.24.0 adds
[bounded checkpoint recovery](sidepanel.md#session-recovery-0240) with one attached
controller per gateway, rotating attachment tokens and interruption of unattended
movement. Backend-supervised movement without a live panel and nonlinear
calibration remain future work. Measurement saves now clear old overrides, retaining the
configured opposite direction during single-direction measurement. WHO layout and the standalone
bus card remain supported.

The original comparison used our panel 0.9.0 at `02ce199` and the calibration fork
at `229b1eb`; `9b839be` pins the preceding single-direction guided measurement.
The backend from
upstream #349 is compared separately. The calibration-fork column still describes
its pinned baseline, not a fresh audit of its moving `master`.

The [one-panel discussion](https://github.com/orgs/OpenWebNet-HA/discussions/270#discussioncomment-18447590)
and [Interstellar0verdrive's #349 review](https://github.com/OpenWebNet-HA/MyHOME/pull/349#pullrequestreview-5212428784)
support a common backend and calibration UI in the panel. Endpoint names, timing
semantics, profile migration and the first nonlinear feature scope still need
agreement in #270. Approval of #349 is not approval of this draft contract.

The aim is one MyHOME panel containing inventory, bus diagnostics and calibration
modules. **The existing standalone bus card remains supported. Its removal is
deferred to a separate future PR**, even after native-monitor parity testing.
Gateway connection settings remain in HA's Config Entry flows; entity/device names
and areas remain in native registries. Automatic entity discovery remains
independent of opening the panel.

## Reviewed baselines

| Implementation | Exact baseline | Relevant evidence |
| --- | --- | --- |
| Our `feat/myhome-sidepanel`, panel 0.20.0 | [`9b839be`](https://github.com/xtimmy86x/MyHOME/tree/9b839bedbbba05231557b7b0ba75a6ed7ea36e35) | [Implemented API](panel-websocket-api.md), `panel.py`, `cover_profiles.py`, `cover_profile_provenance.py`, `cover_profile_export.py`, `cover_calibration.py`, `cover_calibration_automatic.py`, `cover_calibration_batch.py`, `frontend/panel/` |
| Integrated `v2-phase1-architecture` base | [`3f5e791`](https://github.com/OpenWebNet-HA/MyHOME/tree/3f5e79128d5f7261b8e62060d093ec9c0d0c603a) | Runtime-data and discovery refactoring; cover motor-start/echo timing and native calibration; monitor capture workflow; gateway identification, climate fixes and light feature locking |
| Interstellar0verdrive's `MyHOME-stability`, `master` | [`229b1eb`](https://github.com/Interstellar0verdrive/MyHOME-stability/tree/229b1eb30558012674e1e7f5c2059a58300f09df) | [API reference](https://github.com/Interstellar0verdrive/MyHOME-stability/blob/229b1eb30558012674e1e7f5c2059a58300f09df/docs/panel-websocket-api.md), `websocket_api.py`, `panel_data.py`, `panel_write.py`, `panel_schemas.py`, `panel_src/` |
| Upstream #349, backend only | [`e807e99`](https://github.com/OpenWebNet-HA/MyHOME/tree/e807e9986ca9e09e8f3c516bd9c5d8b55e076485) | [PR #349](https://github.com/OpenWebNet-HA/MyHOME/pull/349), `cover.py`, services, [runtime/service documentation](https://github.com/OpenWebNet-HA/MyHOME/blob/e807e9986ca9e09e8f3c516bd9c5d8b55e076485/docs/configuration/services.md) |

The original 0.9.0 baseline remains [`02ce199`](https://github.com/xtimmy86x/MyHOME/tree/02ce19908787297c1a6e2a65d56a289e766c0695)
for historical comparison. Relative implementation links follow this panel branch;
the commit links above pin the reviewed snapshots.

This is a contract comparison checked against source, not a new full audit or a
claim that the fork's UI can be imported unchanged. In particular, its
`tests/test_panel_parity.py` checks panel values/origins against the real cover
runtime. It is not simply a JSON-schema or documentation parity test.

## Implemented since the original comparison

| Panel version | Implemented behavior relevant to this proposal |
| --- | --- |
| 0.10.0 | Backend-owned, socket-bound guided measurement for one standard cover, one session per gateway; manual endpoint confirmation; review and explicit save to a new assigned profile |
| 0.11.0 | Admin-only `myhome/cover_profiles/subscribe`; initial revision and post-persistence invalidations; clean editors refresh, dirty drafts require explicit reload; visible-only 15-second fallback reads |
| 0.11.1 | Idempotent session cleanup and one-shot HA shutdown-listener handling, avoiding a second unsubscribe |
| 0.12.0 | Native `BusMonitorView` loaded by the panel; existing Lovelace card is an adapter over the shared view; explicit selected gateway and stale callback cleanup |
| 0.13.0 | Shared EN/IT monitor catalog with regional-language and per-key English fallback; language changes preserve monitor input/state; no texts endpoint |
| 0.14.0 | Compact secondary entities (buttons and registry `config`/`diagnostic` categories); button timestamps omitted, diagnostic sensor states retained; native details/editing, filters and flags preserved |
| 0.15.0 | Live primary-entity state in collapsed device headers; multiple primary states labeled by entity, secondary buttons remain compact |
| 0.16.0 | Backend-owned opening/closing provenance and UTC dates; manual/guided/unknown sources; copies and assignments preserve unchanged evidence; version-1/2 storage migrates to version 3 |
| 0.17.0 | Admin-only gateway-wide `myhome/cover_profiles/export`; versioned JSON of committed profiles, provenance and assignments, including unused profiles and removed covers; panel download preserves drafts |
| 0.18.0 | Optional single-cover automatic open/close/open cycle in the existing socket session; bus feedback, one-second pauses, 59–65 s cutoff rejection, 180 s timeout; explicit review/Save; automatic provenance; storage v4 and export v2 |
| 0.19.0 | Explicit selection of 1–20 covers in one gateway; sequential automatic cycles in one session; whole-group interruption; final review and one atomic new-profile/assignment save; storage v4/export v2 unchanged |
| 0.20.0 | Optional guided opening-only or closing-only measurement from the assigned saved profile; retained opposite time/provenance; one leg then review; explicit new-copy save with shared session protections |

The current profile model uses version-4 storage with opaque profile IDs,
native unique-ID assignments and two linear directional times. Writes are
revision-checked and persisted before in-memory publication; HA storage write and
serialization failures are surfaced through the existing error path. Saved profile
changes apply after the current movement stops.

The current guided session already implements ownership, step sequences, Stop,
Cancel, heartbeat/lease expiry, start/travel timeouts, guarded queued movement,
external-command interruption, disconnect/unload/shutdown cleanup and explicit
save. These mechanisms should be retained and tested through a shared adapter,
rather than listed as missing first implementations. An accepted persistence
operation may still finish after the UI closes.

## Concrete differences

| Area | Our panel 0.20.0 | Calibration fork at `229b1eb` | Proposed shared direction |
| --- | --- | --- | --- |
| Scope | Whole-installation inventory, primary header states, compact secondary entities, native monitor, profiles, guided/automatic measurement and export | Gateway overview with profile groups and detailed calibration | Keep the common shell; mount calibration as a section |
| Gateway | Profile reads/writes require exact `entry_id`; inventory lists all | `overview` and `subscribe` allow omission and choose first loaded entry | Shell lists gateways; all module reads/writes/subscriptions use explicit entry |
| Cover identity | Request uses native `entity_id`; assignments store native unique ID | Requests use `cover_unique_id`; row may lack an entity ID | Module identity is `(entry_id, cover_unique_id)`; native entity ID is nullable navigation metadata |
| Profile identity | Opaque UUID ID; freely editable display name | Name is the key and appears in assignments/YAML | Opaque stable ID in shared payload; backend adapter handles legacy name mapping |
| Runtime model | Two linear full-travel times | Height scaling, slats, directional roll, per-cover overrides/provenance | Advertise supported model/operations; never interpret nonlinear values as linear |
| Defaults and sources | Assignment overrides YAML/default time; saved direction values have manual/guided/automatic/unknown provenance and inherited origin; YAML versus default remains indistinguishable | Resolver combines file, profile, height and own measurements | Backend exposes effective configuration and provenance; browser never resolves precedence |
| Calibration export | Gateway-wide saved JSON, format version 2; origin references and assignments; no import | Not reassessed for export in this pinned comparison | Agree a portable format and identity/conflict rules before implementing import; do not confuse export with store migration |
| Texts | Bundled shell and shared-monitor EN/IT catalogs; regional/per-key fallback; no texts endpoint | `myhome/calibration/texts` reads translation files | User-language texts service, module namespace, per-key fallback |
| Changes | Profile revision invalidations, draft-safe refresh and visible fallback reads; native HA updates for runtime state | Initial/after-write overview pushes, separate measuring event | Shared subscription semantics, initial synchronization and explicit lifecycle |
| Concurrency | Gateway write lock, expected persisted revision and one socket-owned calibration session per gateway | Refuses overlapping writes / active calibration; no expected revision in documented write payloads | Retain revision conflict protection and calibration busy state |
| Shared edits | Only exclusive profiles can be updated | Profile edits affect all followers | Copy/exclusive edit first; shared changes need impact preview and explicit confirmation |
| Delete | Rejects all assigned profiles | Removes stored assignments; reports file followers and supports undo | Preserve unused-only deletion initially; broader delete requires fallback preview |
| Batch/order | Not implemented | Assignment accepts an order; dedicated reorder also exists | One validated persisted mutation for assignment+order; test failure atomicity before port |
| Undo/preview | Not implemented | Preview via runtime resolver; one undo slot per gateway, five-minute expiry | Capability-gated; undo is revision-checked configuration change, never a movement reversal |
| Advanced covers | Read-only profile response | Excluded from overview; detail refuses | Present unsupported status explicitly; calibration controls disabled |

Source review also found a documentation detail worth reconciling: the fork's
texts reference describes three verbatim blocks, while `async_texts` merges
English fallback per key and exports four blocks via `TEXT_BLOCKS`. The shared
spec should describe the actual exported namespaces and fallback behavior.

## Alignment with upstream #349

As checked on 2026-09-15, #349 was merged into `split/09-cover-302`; this does
not establish that it has shipped in a release or been integrated into our pinned
panel branch. Its new card UI is separated into
[draft #355](https://github.com/OpenWebNet-HA/MyHOME/pull/355), intended for panel
integration rather than the next beta.

| Concern | Our panel 0.20.0 | #349 at `e807e99` | Remaining shared-contract decision |
| --- | --- | --- | --- |
| Entry point | Experimental `myhome/cover_calibration/start` and `action` WebSockets, owned by one socket | `myhome.calibrate_cover`, native buttons, set/reset/stop operations | One backend session/controller with explicit ownership and cancellation semantics for both socket and service clients |
| Completion | Guided endpoints remain operator-confirmed; optional automatic open/close/open completes each run on actuator stop, then requires review/Save | Automatic sequence: open to establish position, close and measure, open and measure; actuator stop ends each run | Define supported automatic/manual completion modes and their interruption rules |
| Measurement boundary | Matching bus feedback is required; guided duration ends at operator confirmation, automatic duration at standard actuator stop (`state == 0`); no write-clock fallback | Motion anchor to actuator stop status; ordinary manually stopped runs expose duration to the Stop write through `last_run_seconds` | Agree one definition of the reported duration; retain manual endpoint evidence separately from physical Stop acknowledgement |
| Persistence | `myhome.cover_profiles.<entry_id>` HA storage v4, opaque profiles, native unique-ID assignments, per-direction evidence and revision checks | Config-entry options `cover_travel_times`, keyed by the cover's device ID; directional times and source metadata | Define identity mapping, conflict policy and one authoritative persistence path |
| Saving | Review then explicitly save a new assigned profile | Automatic calibration stores its result; manual set service also persists values | Decide when automatic modes may save and how all writers update the same revision/notifications |
| Device support | Standard timed covers; advanced hardware-position covers read-only | Automatic calibration rejects advanced covers | Use capability checks; actuator stop feedback does not itself imply an advanced cover |
| Guard | Automatic 59–65 s rejection on every run, 180 s stop timeout, 10 s feedback deadline; shared lease and cancellation | Rejects an automatic run in the 59–65 s window | Keep the automatic-mode guard and document its limits without implying automatic physical endstop detection |

**Timing is not yet identical.** Our endpoint-confirmation duration includes
operator reaction and the request's trip to HA, but excludes the later wait for
the Stop write. #349's ordinary-run duration ends at that write or the actuator's
stop status. Our automatic mode shares the bus-start/actuator-stop boundary when that feedback
is available, but does not use #349’s write-time fallback. This does not make all
values from the two implementations interchangeable. Queue delay, bus feedback latency, endpoint evidence and Stop
confirmation need explicit acceptance cases.

The #349 guard detects the approximately 60-second factory cutoff only. A
different installer-set run time can still exceed physical travel and be stored
as travel. The common API must preserve that limitation and provenance; a bus
stop alone is not proof of a physical endpoint. Advanced-cover support mentioned
in the discussion is a design question, not an implemented capability of either
current calibration path.

### Persistence convergence proposal

Interstellar0verdrive proposes profiles with reference travel, per-cover
overrides and value provenance, eventually including slat time and directional
roll coefficients. Our current runtime implements two linear travel times and
per-direction measurement provenance. Reference-height scaling, slats, roll and
per-cover overrides are not implemented by this panel. Agree whether they belong
in the first common slice or later capability-gated extensions.

Before connecting both clients, define and verify:

- Map `travel_time_up/down` to `opening_time/closing_time` with explicit units
  and gateway/cover identity; preserve source/timestamp metadata where present.
- Resolve existing profile assignments versus imported overrides explicitly.
  Do not silently replace a profile, infer provenance that was never stored,
  or collapse unrelated covers with identical bus addresses.
- Choose the authoritative store and an idempotent, versioned migration that
  preserves the original data until successful completion. Define recovery and
  downgrade behavior; profile storage v1/v2/v3-to-v4 migration already exists, but
  no #349-to-profile migration exists at the pinned panel revision.
- Route panel, service and button writes through the same validation, revision,
  persistence and notification path. Retain pending-versus-effective behavior
  during movement; avoid two independently writable timing stores.
- Preserve service compatibility through an adapter while the panel adopts the
  agreed API. Endpoint renames and migration are implementation work requiring
  review, not effects of this document.

## Implemented provenance and export: boundary for convergence

Panel 0.16.0 records `source`, UTC `recorded_at` and stable origin unique ID for
opening and closing independently. Guided dates are captured when HA processes
the endpoint confirmation, not at later profile save. Manual creation or a changed
numeric time records a manual value at save. Renaming, assigning and explicit
copying preserve unchanged evidence. The write API accepts `copy_from_profile_id`
for a new copy in the same gateway; clients cannot supply provenance metadata.

The read API resolves current origin entity IDs/names and a target-dependent
`inherited` flag. Assigning a kitchen profile to a bedroom does not claim that
the bedroom was measured. Existing profiles migrate with `unknown` source and
null origin/date. Storage version 4 adds the automatic source and preserves existing version-3
evidence; it requires the updated integration; downgrading
requires a compatible backup. Evidence describes saved values separately from
pending/effective runtime values and unsaved editor drafts.

Panel 0.17.0 exports all saved profiles and assignments for one explicit gateway,
using the existing lock to capture a single committed revision. The document has
`format: myhome.cover_calibration` and, since panel 0.18.0, `format_version: 2`, independent of panel
and storage versions. It retains separate directional evidence, unused profiles
and references to removed covers. Local cover references preserve relationships;
current HA registry IDs, entity IDs and names identify surviving covers. Export
references are not stable IDs across documents and do not expose internal
MAC-bearing unique IDs. The export excludes live/session values and drafts,
does not increment revision or send bus commands, and is available for offline
or read-only targets. Closing/changing the UI before a response discards the
pending download. See the [exact export contract](panel-websocket-api.md#calibration-export-0170).

Panel 0.18.0 adapts #349’s three-run sequence into `AutomaticCalibrationSession`,
a mode of the existing panel session. Starting reserves ownership but sends no
movement until explicit `run`. Both modes share socket/gateway ownership, lease,
queue guard, profile revision, Stop/Cancel and atomic Save. Automatic intermediate
states include one-second `settling` pauses; every next run revalidates the target
and HA state. A matching bus start is mandatory, standard stop feedback ends the
run, and suspected 59–65-second cutoff or missing stop within 180 seconds discards
all provisional evidence. The initial open is positioning only; close and final
open enter review. Nothing is saved automatically. Automatic provenance dates
come from the stop feedback and do not establish physical endpoints.

Panel 0.19.0 adds `myhome/cover_calibration/targets` and `batch_start`. Selection
starts empty, is limited to 1–20 eligible covers in one gateway, and executes in
displayed entity-ID order. After explicit confirmation, each cover completes the
same open/close/open cycle, with a one-second pause between covers. One session,
socket, lease and gateway owner span the whole group; only the current cover is
attached to the controller. Each queued move has its own token. Targets are
validated before start, before their turn, and before Save. A measurement error
or interruption discards all provisional group results and prevents later moves.
Final review offers one new-profile name per cover; one atomic write creates and
assigns every result, retains older profiles, increments revision once and emits
one change notification. Validation or disk failure applies none of the group.
An already accepted save may finish after the browser closes. Storage v4 and
export v2 are unchanged. The user confirmed successful selected-cover physical
operation on their actuators.

Panel 0.20.0 adds optional `direction: opening|closing` to guided `start`, using
the same controller. An assigned saved profile is required; the backend copies
only the opposite time and its evidence under the revision lock. Confirmation
starts one selected leg from the appropriate physical endpoint, and endpoint
confirmation leads directly to review. The opposite source/date/origin remains
unchanged, including inherited or unknown evidence. Save creates and assigns a
new profile copy without changing shared originals. Automatic and batch modes
keep their full cycles. No new storage/export format or persistence path is added.
Single-direction physical testing is pending.

This is a local adaptation of the cycle, not a completed adapter for #349’s native
services/buttons or its options store. Import/restore and #349 persistence migration remain unimplemented. The local APIs are not jointly agreed common endpoints. A JSON
export is a record of calibration data, not an HA backup or a migration adapter.
Guided and automatic panel modes now share one session lifecycle and persistence
path. Convergence with the upstream services/options store remains separate work
and must retain these safety and revision guarantees.

## Shell/module boundary

The shell owns navigation, gateway selection, current HA connection, user language
and the optional sidebar entry. A module receives these through a small host
interface; it never independently chooses another gateway or accesses Python store
internals. Lit is an implementation choice inside the module, not an API dependency
for the shell.

On entry/connection change or unmount, a module invalidates its pending reads,
previews and subscription callbacks. A late subscription success must immediately
unsubscribe if its owner has gone. Reconnect creates a new subscription and fresh
snapshot even when the gateway ID is unchanged. A module may preserve a draft
visually, but must label it stale and never silently resubmit it against new data.

This does not cancel an accepted backend operation. Physical calibration needs a
separate backend lifecycle; closing a browser is not evidence that a shutter stopped.

## Proposed first contract slice

The following names and shapes are discussion material, **not callable endpoints**.
Do not change existing clients to use them until both sides agree and handlers,
adapters and acceptance tests exist. Keep the currently implemented APIs available
through the migration; documentation alone does not rename them.

### 1. Module capabilities and explicit gateway scope

Retain the installation-wide inventory as the shell's gateway source. Add a
small versioned descriptor for available modules; the exact discovery endpoint can
be agreed with the existing inventory owner. For example:

```json
{"contract_version":1,"modules":{"covers":{"model":"linear_timed","operations":["read","save_exclusive","copy","assign","delete_unused"]}}}
```

A nonlinear backend advertises a different model and its actual operations.
Unsupported operations are absent, not buttons that fail after a user fills a form.
Contract version is independent of the panel asset and store versions. Unknown
major versions require a clear incompatibility message, not guessed payload parsing.

All proposed module/texts commands require administrator authorization at the
backend, independently of the shell. All cover-module requests include `entry_id`;
cover-specific requests also include
`cover_unique_id`. Validate ownership server-side on every operation. The adapter
must retain assignments through native entity renames and represent unregistered,
unloaded and advanced covers explicitly. Removing a gateway terminates its module
subscription with an unavailable/removed state; it never transfers to another entry.

### 2. Texts service

Proposed request:

```json
{"id":10,"type":"myhome/panel/texts","module":"covers","language":"it-CH"}
```

Proposed result:

```json
{"module":"covers","requested":"it-CH","language":"it","fallback":true,"texts":{"profile.delete":"Elimina profilo","error.revision_conflict":"Ricarica i dati salvati prima di riprovare."}}
```

Use the user's language, not silently the server language. Normalize tags and try
exact tag, primary language, then English; fill missing keys from English and show
the key if no text exists. Validate module/language identifiers before file access.
Cache per language/module for the integration lifetime and invalidate on reload.
Use text interpolation, not trusted HTML from translations or entity names.

The existing bundled strings can remain until this service is ready; migrate one
section at a time. The fork's `options`, `selector`, `exceptions` and `panel` blocks need
an explicit mapping to agreed namespaced keys, not wholesale copying into the shell.

### 3. Cover read model and configuration changes

Proposed `myhome/covers/read` returns a gateway-wide full snapshot; an optional
cover selector can request detail. Initial implementation may adapt our per-cover
snapshot, but it must not expose unsupported calibrated properties as zero values.
Suggested core fields:

| Field | Meaning |
| --- | --- |
| `entry_id`, `revision` | Exact gateway and current persisted cover-configuration revision |
| `model`, `operations` | Supported motion model and actions for this gateway/cover |
| `profiles` | Stable ID, display name, stored values, source/editability and explicit followers |
| `covers` | Unique ID, nullable native entity ID, name/area, availability and support reason |
| `covers[].effective` | Backend-resolved runtime values; never calculated by the browser |
| `covers[].configured` | Latest saved/resolved target values, which may still be pending |
| `covers[].pending` | Saved changes not yet applied to the current movement |
| `busy` | Null or active calibration identity/state; distinct from ordinary cover motion |

Both backends must distinguish saved values from the model currently used in a
movement. A saved profile is not evidence of a physical measurement. Measurement
provenance, height and roll are optional model-specific data, not invented defaults.

Proposed mutations use `myhome/covers/write` with explicit action, target and
`expected_revision`. Agree action schemas individually; begin with our existing
save/copy/assign/delete-unused semantics. Validate all data and effects before
persisting; publish a new revision only after successful persistence. Revalidate
a preview against the same revision at apply time. A conflict preserves the draft
and requires another read/preview; the client must not merely replace its revision
and retry. Accepted no-ops initially retain our current revision-increment behavior.

A legacy name-to-ID mapping must be stable and unambiguous across reloads; our
profiles can have duplicate display names. Do not derive identity from the current
display label or rename a YAML reference implicitly. Importing either store into
the other is a separate migration requiring explicit conflict handling.

The fork's more powerful shared edit, rename, delete and assignment/order operations
need adapters plus persistence-failure tests. A command containing several awaited
store writes is not automatically an atomic disk transaction just because it is
one WebSocket call. Verify the entire proposed mutation on the destination store.

### 4. Refresh subscriptions

Proposed request:

```json
{"id":11,"type":"myhome/covers/subscribe","entry_id":"ENTRY"}
```

Proposed event in HA's ordinary subscription envelope:

```json
{"id":11,"type":"event","event":{"type":"changed","entry_id":"ENTRY","revision":9,"reason":"configuration"}}
```

**Recommended first implementation:** invalidation events followed by a fresh read.
This adds one read after a change, but avoids introducing a second snapshot-ordering
model while adapting the two stores. The fork's existing full `overview` pushes
could be adapted to invalidations. Whether to retain full snapshots instead is an
explicit review decision, not an assumption of compatibility.

Required behavior for the invalidation option:

1. Register the listener, acknowledge the subscription, then send an initial
   invalidation so the client reads after registration without a lost-update gap.
2. Emit after persisted changes from every writer, including guided-calibration saves,
   and on runtime pending-to-applied, busy, registry and availability changes.
   Use reasons `configuration`, `runtime`, `busy`, `registry`, `availability`.
   Runtime/registry events do not increment the configuration revision.
3. Coalesce events while reading but remember a refresh is pending. Re-read after
   the in-flight read completes; never drop the final invalidation. Replace the
   authoritative snapshot, retaining an independent user draft and stale indicator.
4. Do not rely on the relative arrival order of a write response and an event.
   Serialize refresh reads per module/generation and reject older configuration
   revisions. Treat successful write responses as another reason to refresh;
   they must not overwrite a newer runtime view with an older snapshot.
5. Unsubscribe on unmount/gateway change and server-side socket closure. After
   reconnect or visible-tab resume, fetch fresh data; events are not durable replay.
   If subscription fails, use a visible-only fallback poll for the module snapshot.

Bus frames remain a separate high-volume stream and do not increment profile
revisions. The shell's existing inventory poll is not a substitute for cover refresh.

### 5. Errors

Use HA's normal `success:false` envelope with a stable machine `code`, a useful
English `message`, and `translation_domain`, `translation_key` and safe
`translation_placeholders` where supported by the target HA version. The frontend
uses keys to choose behavior and translated text for presentation.

Preserve our refusal semantics through adapters. Distinguish missing entry,
unloaded/disconnected gateway, unknown cover, advanced cover, revision conflict,
profile in use, calibration busy, invalid input and persistence failure. The
exact mapping of existing `profile_*` codes to the fork's `not_allowed` /
`service_validation_error` vocabulary remains a review item. Invalid transport
schemas and unauthorized callers remain HA errors.

Do not report a storage success when only a command was queued. Do not describe
an unavailable device as having disappeared from the registry. Never include
credentials, unfiltered store objects or raw configuration in error placeholders.

## Migration sequence and review decisions

| Step | Concrete result | Decision needed before implementation |
| --- | --- | --- |
| 1. Compare | Existing comparison, current panel and pinned #349 delta | Explicit gateway scope, stable cover/profile identity, supported models |
| 2. Agree refresh/texts | Small request/result/event schemas and error map | Namespaces, invalidation vs snapshot push, language blocks, revision rules |
| 3. Implement adapters | Existing profiles, guided session and subscriptions through the agreed interface | One session/controller, duration semantics, persistence ownership and explicit #349 migration |
| 4. Extend calibration | Adapt implemented guided/automatic panel modes and provenance/export to agreed service/backend boundaries | Capability-gated height/slat/roll scope; runtime parity and mutation atomicity |
| 5. Retire standalone card (separate future PR) | Existing native monitor validated for full parity; compatibility retained until then | Explicit removal decision, keyboard/mobile checks and migration notice |

The first two steps can proceed while Interstellar0verdrive is unavailable. This
proposal does not assign him a deadline or treat his implementation as approved
for import. Resolve identity/model and API differences together before extending
the calibration UI against a second competing contract.

## Acceptance cases for the agreed contract

Use small shared request/result/error fixtures to exercise the actual backend
handlers and frontend consumer. Include malformed payloads and denied users, not
only happy-path JSON. Reuse runtime-parity tests in the spirit of the fork's suite:
assert the API describes what the cover actually runs, including pending changes.

The required cases are:

- Two gateways with identical bus addresses: reads, writes, previews and subscriptions
  never cross entries; a removed/disabled gateway does not trigger fallback.
- Native rename, missing registry row, unload/rebind and restart preserve stable
  identities and make unsupported/offline state visible.
- Legacy profile migration, asymmetric timing and an in-flight scheduled stop agree
  between persistence, API and cover runtime; do not fabricate calibration provenance.
- Two tabs: stale edit/delete/undo fails, an assignment/delete race is consistent,
  notifications refresh the other tab and a dirty editor retains its draft.
- Initial subscription, event-during-read, late read/subscribe, reconnect with the
  same gateway and hidden-tab resume leave exactly one current subscription/view.
- Language change/fallback and missing keys work without mixing data or carrying
  executable HTML from names/translations.
- Failed persistence leaves the entire configuration/revision unchanged, including
  batch assignment+order and profile rename if those operations are introduced.
- Per-direction provenance survives rename, assignment, copy and restart; changing
  one time affects only its evidence. Legacy data never gains fabricated dates,
  and assignment to a different cover never implies a fresh measurement.
- Export contains one committed gateway revision, retains orphan references and
  unused profiles, excludes drafts/session values, and neither mutates storage
  nor sends bus commands. Unauthorized requests fail before storage access;
  frontend retries and late responses preserve the current editor/navigation.
- Automatic mode performs exactly open/close/open without a save until review,
  rejects cutoff runs immediately, discards all provisional values on failure,
  and never starts a later run after Stop, cancellation, lease expiry or unload.
- A guided measurement makes the appropriate configuration scope busy; ordinary
  motion instead defers application. Stop/cancel outcomes remain explicit.

Several cases already have regressions in our panel (profile migration/failure,
revision races, subscription lifecycle, guided-session cleanup, gateway scoping,
per-direction provenance, saved calibration export and the automatic cycle).
The future gate is to run them through the **agreed common adapters**, plus add the
new migration, automatic/manual timing and optional nonlinear cases. Existing tests
do not yet establish interoperability with #349 or the calibration fork.

## Next review step and verification scope

Use this existing document as the starting point in #270. The immediate work is
agreement on the #349 differences above and adapting the already implemented
session/profile backend, not recreating the sidepanel or repeating the entire
original fork audit. Keep unrelated inventory and monitor improvements moving
while the common calibration boundary is discussed. No reviewer deadline is
assumed.

Panel 0.20.0 extends the existing profile/calibration tests with both one-leg
paths, retained manual/automatic/unknown evidence, shared-profile isolation,
restart persistence, cancellation, storage errors and stale revisions.
All **79 frontend tests passed**, including direction selection, endpoint
instructions and distinct measured/retained labels. Existing coverage includes explicit gateway selection,
sequential targets, whole-group interruption, queue tokens, atomic failure cases,
restart persistence and preservation of review names. It covers automatic run ordering, cutoff boundaries,
missing start/stop feedback, cancellation during pauses/queued dispatch, shared
ownership, persistence failure, source migration, UI confirmation and explicit Save.
The full local suite passed **1,551 Python tests (1 skipped), with 100% line
coverage** on the base aligned to `db95d1f`. Ruff and HA architectural checks
passed. CI results are recorded with the implementation in the panel PR.
These checks are not proof of interoperability with upstream services or of
physical actuator feedback accuracy. The user confirmed the preceding 0.16.0
provenance, 0.18.0 single-cover automatic and 0.19.0 selected-cover automatic
behavior in their installation. Single-direction physical testing is pending.
Real-browser visual validation and the guided-calibration reference's remaining
physical-gateway/feedback cases retain their separate scope.

This document accompanies the panel 0.20.0 single-direction implementation; storage
v4 and export v2 remain unchanged from 0.18.0.
The calibration-fork assessment remains pinned to `229b1eb` and the #349 backend
comparison to `e807e99`; neither external implementation was freshly audited for
this documentation update. Newer changes require a focused delta review.

## Keeping this document current

Update this document in the same PR as each relevant panel/backend change. Keep
the current implementation version and pinned SHA, implemented-feature history,
API/storage/export versions, remaining convergence decisions and verification
scope aligned with the actual code. Separate implemented local behavior from
proposed shared behavior. Retain external baseline pins unless their code was
reviewed again, and keep standalone-card removal in its separate future PR.
