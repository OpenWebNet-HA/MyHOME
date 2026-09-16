# Integration Quality Scale plan: MyHOME → 🏆 Platinum

_Status on 2026-09-14 (`scripts/quality_scale_report.py`): **no tier reached yet** — 52/54 rules satisfied (Bronze 19/20, Silver 10/10, Gold 21/21, Platinum 2/3). Phases 0–4 are done; 🥉 Bronze is blocked only by `brands` (an external PR), Platinum by `strict-typing`. There is no "Diamond" tier in the official scale; the top is 🏆 Platinum. Tiers are formally awarded only by Home Assistant core review — this plan gets the self-audit there, which is the precondition for the upstream submission in ROADMAP Phase 5._

Reference: <https://developers.home-assistant.io/docs/core/integration-quality-scale/rules/>  
Manifest: [`custom_components/myhome/quality_scale.yaml`](../custom_components/myhome/quality_scale.yaml)

## Phase 0 — Land what's in flight (prerequisite) — ✅ done

- Finish, test and merge `feat/cover-calibration` (`cover.py`, the bus-monitor card, `services.py`). It adds new services and ~10 new `HomeAssistantError` raises that Phases 2–4 must cover (`docs-actions`, `exception-translations`); doing quality work on an unmerged branch means doing it twice.
- Fix the local crash in `scripts/quality_scale_report.py` (cp1252 `UnicodeEncodeError` on Windows — `sys.stdout.reconfigure(encoding="utf-8")`) so the report is runnable locally, not only in CI.

## Phase 1 — 🥉 Bronze (4 rules → the tier appears on the badge) — 3 of 4 done (`brands` pending)

| Rule | Work | Size |
|---|---|---|
| `docs-removal-instructions` | Add a "Removing the integration" section to README: delete the config entry → what is cleaned up, leftovers in `/config/www`, `myhome.yaml`. | S |
| `runtime-data` | 12 files still read `hass.data[DOMAIN]` (all 9 platforms + `services.py`, `decoder_pool.py`, `const.py`). Refactor to `entry.runtime_data` (typed `MyHOMEConfigEntry`); keep the `hass.data` mapping as a compatibility alias for one release, then delete. Extend `scripts/verify_ha_standards.py` to fail on `hass.data[DOMAIN]` in platform files. | M |
| `has-entity-name` | **Decision first, then code.** `myhome_device.py` sets `_attr_has_entity_name = False`; buttons already set `True`. Existing installs keep their `entity_id` (the registry keys on `unique_id`, it does not rename), so the breakage is limited to new installs / `myhome.yaml` users relying on generated ids. Proposal: close the community RFC with "migrate in 2.0.0 final, document the naming change"; set `_attr_name = None` on primary entities (one device = one entity), drop manual `self.entity_id` overrides, ship a migration note. | L |
| `brands` | Open the PR to `home-assistant/brands` (icon + logo, 256/512 px). External latency — open it in week 1 so it is not the last blocker. | S (+ wait) |

## Phase 2 — 🥇 Gold: documentation (4 rules, no code risk) — ✅ done

- `docs-known-limitations` — consolidate the deferred items in ROADMAP (RFC #248 list), gateway-profile gaps and `protocol_conformance_matrix.md` into one page.
- `docs-troubleshooting` — generalise the README "🩹 Troubleshooting" loader-error section: connection/password failures, NACKs, bus-monitor permissions, log lines to look for.
- `docs-use-cases` — a few end-to-end scenarios (scenes/CEN+ automations, timed covers with calibration, multi-room audio).
- `docs-supported-functions` — audit table per WHO / platform of what is supported, read-only, or not supported.

Most material already exists (`runtime_behaviour.md`, `protocol_conformance_matrix.md`, ROADMAP); this is mostly consolidation.

## Phase 3 — 🥇 Gold: entity audit (3 `in_progress` rules) — ✅ done (`tests/test_entity_audit.py`)

One pass over the 9 platforms producing a table `platform × entity → device_class / entity_category / enabled_by_default`:

- `entity-category` — only buttons are categorised today (`EntityCategory.CONFIG`). Diagnostic sensors (gateway, bus telemetry) → `EntityCategory.DIAGNOSTIC`.
- `entity-device-class` — verify cover (shutter/blind/…), switch (outlet/switch), binary_sensor and sensor classes per WHO.
- `entity-disabled-by-default` — only `sensor.py` applies it today; decide for noisy/telemetry entities in the other platforms.

Add a parametrised test that asserts the table so it cannot regress.

## Phase 4 — 🥇 Gold: code rules (4 rules) — ✅ done

| Rule | Work |
|---|---|
| `entity-translations` | Follows Phase 1 `has-entity-name`: add `translation_key` per entity type and an `entity` block in `strings.json` / `translations/en.json` (the validator already checks the top-level keys stay in sync). |
| `exception-translations` | User-facing raises in `cover.py`, `media_player.py`, `decoder_pool.py` are f-string `HomeAssistantError`s. Convert to `HomeAssistantError(translation_domain=DOMAIN, translation_key=..., translation_placeholders=...)` with an `exceptions` block in `strings.json`; use `ServiceValidationError` for bad service input (e.g. the `travel_time` range checks). |
| `repair-issues` | `repairs.py` has 4 issue helpers, none called from production code. Wire at least gateway-identity-corrected (from the gateway precedence logic) and bus-collision into `gateway.py` / `__init__.py`, with tests. |
| `stale-devices` | Implement `async_remove_config_entry_device` in `__init__.py` (allow removal when the device has no live entities / is no longer seen on the bus); pair with the existing setup-time prune. |

## Phase 5 — 🏆 Platinum: `strict-typing`

- ✅ Ratchet in place: `[tool.mypy]` (strict) in `pyproject.toml`, `mypy_baseline.json` with a per-module ceiling, `scripts/typing_ratchet.py` (fails on any regression, `--update` only lowers), workflow `strict-typing.yml`. 15 of 28 modules are clean (`const`, `data`, `gateway`, `myhome_device`, `services`, `device_trigger`, `decoder_pool`, transports, diagnostics, repairs, bus monitor); **507 errors remain in 13 modules** — biggest first: `config_flow` 105, `cover` 64, `validate` 57, `binary_sensor` 43, `light` 39, `sensor` 32, `websocket` 30, `__init__` 27, `button` 27, `climate` 25, `media_player` 25, `alarm_control_panel` 18, `switch` 15 (`discovery` is clean). Each PR that touches a module should leave it lower and run `--update`.
- OWNd does **not** ship `py.typed`; the rule needs an OWNd release that is PEP 561 typed before it can be marked done.
- `inject-websession` is correctly `exempt` (raw TCP, no HTTP); `async-dependency` is done.

## Maintainability (not a scale rule, keeps the rules honest)

- ✅ `custom_components/myhome/discovery.py` holds the restore / configure / discover / route skeleton once; `light`, `switch`, `cover`, `alarm_control_panel` and `media_player` are on it (their setup functions shrank from ~940 to ~260 lines, module coverage stays 100%, the new module is mypy-strict clean).
- ✅ `sensor`, `binary_sensor` and `climate` followed (multi-WHO platforms, one entity per measurement, address spellings, direct feed); `button` uses the shared address helpers. Their setup code is fully typed, which took the ratchet from 537 to 506.

## Throughout

- Update `quality_scale.yaml` in the same PR as each rule — the report is only as honest as the manifest.
- Once Bronze is reached, add `--require bronze` to `.github/workflows/quality-scale.yml` and ratchet the tier as phases land so it cannot silently regress.

## Order and risk

Phase 0 → 1 (`docs-removal` + `runtime-data` + brands PR in week 1, `has-entity-name` decision in parallel) → 2 → 3 → 4 → 5.  
Phases 1 (`has-entity-name`) and 4 (`stale-devices`, `repair-issues`) are the only ones with breaking-change or behavioural risk; everything else is documentation, audit or typing.
