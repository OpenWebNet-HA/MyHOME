# Integration Quality Scale plan: MyHOME → 🏆 Platinum

_Status on 2026-09-14 (`scripts/quality_scale_report.py`): **no tier reached yet** — 50/54 rules satisfied (Bronze 18/20, Silver 10/10, Gold 20/21, Platinum 2/3). Phases 0–4 are done except the two decisions below; 🥉 Bronze is blocked by `brands` and `has-entity-name`, so nothing is awarded yet. There is no "Diamond" tier in the official scale; the top is 🏆 Platinum. Tiers are formally awarded only by Home Assistant core review — this plan gets the self-audit there, which is the precondition for the upstream submission in ROADMAP Phase 5._

Reference: <https://developers.home-assistant.io/docs/core/integration-quality-scale/rules/>  
Manifest: [`custom_components/myhome/quality_scale.yaml`](../custom_components/myhome/quality_scale.yaml)

## Phase 0 — Land what's in flight (prerequisite) — ✅ done

- Finish, test and merge `feat/cover-calibration` (`cover.py`, the bus-monitor card, `services.py`). It adds new services and ~10 new `HomeAssistantError` raises that Phases 2–4 must cover (`docs-actions`, `exception-translations`); doing quality work on an unmerged branch means doing it twice.
- Fix the local crash in `scripts/quality_scale_report.py` (cp1252 `UnicodeEncodeError` on Windows — `sys.stdout.reconfigure(encoding="utf-8")`) so the report is runnable locally, not only in CI.

## Phase 1 — 🥉 Bronze (4 rules → the tier appears on the badge) — 2 of 4 done

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

## Phase 4 — 🥇 Gold: code rules (4 rules) — 3 of 4 done (`entity-translations` waits for Phase 1)

| Rule | Work |
|---|---|
| `entity-translations` | Follows Phase 1 `has-entity-name`: add `translation_key` per entity type and an `entity` block in `strings.json` / `translations/en.json` (the validator already checks the top-level keys stay in sync). |
| `exception-translations` | User-facing raises in `cover.py`, `media_player.py`, `decoder_pool.py` are f-string `HomeAssistantError`s. Convert to `HomeAssistantError(translation_domain=DOMAIN, translation_key=..., translation_placeholders=...)` with an `exceptions` block in `strings.json`; use `ServiceValidationError` for bad service input (e.g. the `travel_time` range checks). |
| `repair-issues` | `repairs.py` has 4 issue helpers, none called from production code. Wire at least gateway-identity-corrected (from the gateway precedence logic) and bus-collision into `gateway.py` / `__init__.py`, with tests. |
| `stale-devices` | Implement `async_remove_config_entry_device` in `__init__.py` (allow removal when the device has no live entities / is no longer seen on the bus); pair with the existing setup-time prune. |

## Phase 5 — 🏆 Platinum: `strict-typing`

- `mypy --strict custom_components/myhome` reports **522 errors in 20 files** today. Ratchet: add mypy to CI with a per-module allowlist, clear modules in order (`const` → `myhome_device` → `gateway` → platforms → `__init__`), shrink the allowlist with every PR.
- The rule also requires the dependency to be PEP 561 typed: confirm OWNd ships `py.typed` and is fully annotated; if not, that is an OWNd release first.
- `inject-websession` is correctly `exempt` (raw TCP, no HTTP); `async-dependency` is done.

## Throughout

- Update `quality_scale.yaml` in the same PR as each rule — the report is only as honest as the manifest.
- Once Bronze is reached, add `--require bronze` to `.github/workflows/quality-scale.yml` and ratchet the tier as phases land so it cannot silently regress.

## Order and risk

Phase 0 → 1 (`docs-removal` + `runtime-data` + brands PR in week 1, `has-entity-name` decision in parallel) → 2 → 3 → 4 → 5.  
Phases 1 (`has-entity-name`) and 4 (`stale-devices`, `repair-issues`) are the only ones with breaking-change or behavioural risk; everything else is documentation, audit or typing.
