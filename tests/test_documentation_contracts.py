"""Documentation & CI Regression Gate Contract Tests (Gates 1–7).

Validates that hand-authored documentation snippets, service schemas, migration
outputs, discovery tables, WebSocket docs, blueprints, and packaging metadata
remain strictly synchronized with the live MyHOME runtime codebase.
"""
from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest
import voluptuous as vol
import yaml
from homeassistant.components.light import LIGHT_TURN_ON_SCHEMA
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers import config_validation as cv
from pytest_homeassistant_custom_component.common import MockConfigEntry

import scripts.sync_documentation as sync_doc
import scripts.verify_ha_standards as verify_ha
from custom_components.myhome import device_trigger, validate
from custom_components.myhome.const import (
    CALIBRATION_MAX_MOTOR_PULSE,
    CALIBRATION_MAX_RUN,
    CALIBRATION_MIN_MOTOR_PULSE,
    CALIBRATION_MIN_RUN,
    CONF_ENTITY,
    DOMAIN,
    PLATFORMS,
    TUNER_MAX_STATION_COUNT,
)
from custom_components.myhome.cover import MAX_SLAT_TIME, MIN_SLAT_TIME
from custom_components.myhome.gateway import DISCOVERY_REQUESTS
from custom_components.myhome.legacy_yaml import _load_live_yaml
from custom_components.myhome.tuner_decode import FM_MAX_KHZ, FM_MIN_KHZ
from scripts.migrate_from_sdomotica import (
    extract_sdomotica_config_json,
    generate_myhome_yaml,
)

REPO_ROOT = Path(__file__).resolve().parents[1]
DOCS_DIR = REPO_ROOT / "docs"
SERVICES_YAML_PATH = REPO_ROOT / "custom_components" / "myhome" / "services.yaml"

FENCE_YAML_RE = re.compile(
    r"^[ \t]*(?:>[ \t]*)?```yaml[^\n]*\n(.*?)^[ \t]*(?:>[ \t]*)?```",
    re.MULTILINE | re.DOTALL,
)
MAC_KEY_RE = re.compile(r"^[0-9A-Fa-f]{2}(?:[:-][0-9A-Fa-f]{2}){5}$")

MYHOME_PLATFORM_KEYS = {
    "light",
    "switch",
    "cover",
    "climate",
    "binary_sensor",
    "sensor",
    "media_player",
    "button",
    "alarm_control_panel",
    "lock",
    "expose",
}

NON_MYHOME_ROOT_KEYS = {
    "alias",
    "description",
    "trigger",
    "triggers",
    "condition",
    "conditions",
    "action",
    "actions",
    "mode",
    "type",
    "entities",
    "title",
    "cards",
    "views",
    "use_blueprint",
    "blueprint",
    "template",
    "input_number",
    "input_boolean",
    "input_select",
    "script",
    "automation",
    "homeassistant",
    "logger",
    "recorder",
    "mqtt",
    "rest_command",
    "shell_command",
    "service",
    "target",
    "data",
    "metadata",
    "sequence",
    "variables",
    "id",
}

HA_TARGET_KEYS = {
    "entity_id",
    "device_id",
    "area_id",
    "floor_id",
    "label_id",
    "exclude",
    *(
        str(k.schema if hasattr(k, "schema") else k)
        for k in getattr(cv, "ENTITY_SERVICE_FIELDS", {})
    ),
}


class _DocYamlLoader(yaml.SafeLoader):
    """SafeLoader supporting Home Assistant !input and !secret tags in docs/blueprints."""


_DocYamlLoader.add_constructor(
    "!input", lambda loader, node: f"__ha_input__:{loader.construct_scalar(node)}"
)
_DocYamlLoader.add_constructor(
    "!secret", lambda loader, node: "secret_placeholder"
)


def _iter_active_markdown_files() -> list[Path]:
    """Return all active v2 Markdown files (excluding archive pre-v0.9 legacy-yaml.md)."""
    files = sorted(DOCS_DIR.rglob("*.md")) + [
        REPO_ROOT / "README.md",
        REPO_ROOT / "ROADMAP.md",
    ]
    return [
        p
        for p in files
        if p.relative_to(REPO_ROOT).as_posix() != "docs/migration/legacy-yaml.md"
    ]


def _extract_yaml_documents() -> list[tuple[str, str, Any]]:
    """Extract and parse all fenced YAML documents across active Markdown files."""
    extracted: list[tuple[str, str, Any]] = []
    for md_path in _iter_active_markdown_files():
        rel = md_path.relative_to(REPO_ROOT).as_posix()
        content = md_path.read_text(encoding="utf-8")
        for idx, match in enumerate(FENCE_YAML_RE.finditer(content), start=1):
            lines = [
                re.sub(r"^[ \t]*>[ \t]?", "", ln)
                for ln in match.group(1).splitlines()
            ]
            raw_block = "\n".join(lines)
            norm_block = re.sub(
                r"00:03:50:XX:XX:XX",
                "00:03:50:aa:bb:cc",
                raw_block,
                flags=re.IGNORECASE,
            )
            for doc in yaml.load_all(norm_block, Loader=_DocYamlLoader):
                if doc is not None:
                    extracted.append((f"{rel}#{idx}", norm_block, doc))
    return extracted


async def _setup_entry_with_services(
    hass: HomeAssistant,
    tmp_path: Path,
    mac: str = "00:03:50:01:02:03",
    yaml_content: str | None = None,
) -> MockConfigEntry:
    """Set up a full MockConfigEntry so all 17 domain + entity services register on hass."""
    effective_yaml = yaml_content
    if effective_yaml is None:
        effective_yaml = (
            "sensor:\n"
            "  general_power:\n"
            "    where: '51'\n"
            "    name: 'General Power'\n"
            "    class: 'power'\n"
        )
    yaml_file = tmp_path / "myhome.yaml"
    yaml_file.write_text(effective_yaml, encoding="utf-8")
    options: dict[str, Any] = {"file_path": str(yaml_file)}

    entry = MockConfigEntry(
        domain=DOMAIN,
        data={
            "host": "192.168.1.35",
            "port": 20000,
            "password": "12345",
            "mac": mac,
            "name": "F454",
            "firmware": "2.0.0",
        },
        options=options,
        unique_id=mac,
    )
    entry.add_to_hass(hass)

    with (
        patch(
            "custom_components.myhome.gateway.OWNSession.test_connection",
            return_value={"Success": True, "Message": None},
        ),
        patch("custom_components.myhome.gateway.MyHOMEGatewayHandler.listening_loop"),
        patch("custom_components.myhome.gateway.MyHOMEGatewayHandler.sending_loop"),
        patch(
            "custom_components.myhome.gateway.MyHOMEGatewayHandler.send",
            return_value=True,
        ),
    ):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
        gw = hass.data[DOMAIN][mac][CONF_ENTITY]
        gw._on_event_connection_state_change(True)
        await hass.async_block_till_done()
    return entry


# ---------------------------------------------------------------------------
# Gate 2a: Executable /config/myhome.yaml Fenced Snippets & Archive Warning
# ---------------------------------------------------------------------------


def _is_myhome_yaml_snippet(norm_block: str, doc: Any) -> bool:
    """Return True if a parsed fenced YAML document represents a /config/myhome.yaml snippet."""
    if not isinstance(doc, dict) or not doc:
        return False
    keys = {str(k) for k in doc}
    if keys & NON_MYHOME_ROOT_KEYS:
        return False
    return bool(
        "myhome" in keys
        or any(MAC_KEY_RE.match(k) for k in keys)
        or (keys & (MYHOME_PLATFORM_KEYS | {"groups", "platforms"}))
        or re.search(r"(?im)^\s*#[^\n]*myhome\.yaml\b", norm_block)
    )


async def test_all_fenced_myhome_yaml_snippets_validate_and_load(
    hass: HomeAssistant,
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Every /config/myhome.yaml fenced snippet in docs/ passes config_schema and _load_live_yaml."""
    legacy_doc = (DOCS_DIR / "migration" / "legacy-yaml.md").read_text(encoding="utf-8")
    assert "> [!WARNING]" in legacy_doc
    assert "pre-v0.9" in legacy_doc

    myhome_snippets: list[tuple[str, str, dict[str, Any]]] = []
    for loc, norm_block, doc in _extract_yaml_documents():
        if _is_myhome_yaml_snippet(norm_block, doc):
            myhome_snippets.append((loc, norm_block, doc))

    assert len(myhome_snippets) >= 18, (
        f"Expected at least 18 myhome.yaml snippets, found {len(myhome_snippets)}"
    )

    for idx, (loc, norm_block, doc) in enumerate(myhome_snippets):
        keys = [str(k) for k in doc]
        macs_to_test = [k.lower().replace("-", ":") for k in keys if MAC_KEY_RE.match(k)]
        if not macs_to_test:
            if "myhome" in doc and isinstance(doc["myhome"], dict) and "mac" in doc["myhome"]:
                macs_to_test = [str(doc["myhome"]["mac"]).lower().replace("-", ":")]
            else:
                macs_to_test = ["00:03:50:01:02:03"]

        yaml_file = tmp_path / f"snippet_{idx}.yaml"
        serialized = (
            yaml.safe_dump(doc, sort_keys=False)
            if re.search(r"(?m)^---\s*$", norm_block)
            else norm_block
        )
        yaml_file.write_text(serialized, encoding="utf-8")

        for mac_idx, mac in enumerate(macs_to_test):
            entry = MockConfigEntry(
                domain=DOMAIN,
                data={"mac": mac},
                options={"file_path": str(yaml_file)},
                unique_id=f"snippet_{idx}_{mac_idx}_{mac}",
            )
            entry.add_to_hass(hass)
            try:
                caplog.clear()
                with caplog.at_level(logging.ERROR):
                    configured_platforms: dict[str, dict[str, dict[str, Any]]] = {}
                    configured_expose: list[dict[str, Any]] = []
                    loaded = await _load_live_yaml(
                        hass,
                        entry,
                        configured_platforms,
                        PLATFORMS,
                        configured_expose,
                    )
                errors = [r.message for r in caplog.records if r.levelno >= logging.ERROR]
                assert loaded is True, f"Snippet {loc} (MAC {mac}) failed _load_live_yaml: {errors}"
                assert not errors, f"Snippet {loc} (MAC {mac}) logged errors: {errors}"
            finally:
                await hass.config_entries.async_remove(entry.entry_id)


async def test_gate2_negative_invalid_myhome_yaml_traps_rejected(
    hass: HomeAssistant,
    tmp_path: Path,
) -> None:
    """Known historic documentation schema traps are caught by classifier and fail schema + _load_live_yaml."""
    invalid_traps = {
        "top_level_groups": "groups:\n  living:\n    where: '#1'\n    name: Living\n",
        "mixed_light_and_top_level_groups": (
            "light:\n  spot:\n    where: '11'\n    name: Spot\n"
            "groups:\n  living:\n    where: '#1'\n    name: Living\n"
        ),
        "legacy_platforms_root": (
            "platforms:\n  - platform: light\n    devices:\n      spot:\n        where: '11'\n        name: Spot\n"
        ),
        "comment_tagged_unknown_root": "# /config/myhome.yaml\nbogus_domain:\n  spot:\n    where: '11'\n",
        "nested_light_groups": "light:\n  groups:\n    living:\n      where: '#1'\n      name: Living\n",
        "double_nested_mac": "myhome:\n  00:03:50:01:02:03:\n    expose:\n      - who: '4'\n        where: '1'\n        entity_id: sensor.temp\n",
        "cover_missing_name": "cover:\n  shutter_1:\n    where: '21'\n",
        "cover_bus_interface": "cover:\n  shutter_1:\n    where: '21'\n    bus_interface: '02'\n    name: Shutter\n",
        "multi_mac_second_invalid": (
            "00:03:50:01:02:03:\n  light:\n    spot:\n      where: '11'\n      name: Spot\n"
            "00:03:50:04:05:06:\n  cover:\n    shutter_1:\n      where: '21'\n"
        ),
    }

    entry = MockConfigEntry(
        domain=DOMAIN,
        data={"mac": "00:03:50:01:02:03"},
        unique_id="00:03:50:01:02:03",
    )
    entry.add_to_hass(hass)
    try:
        for trap_name, raw_text in invalid_traps.items():
            parsed_doc = yaml.safe_load(raw_text)
            assert _is_myhome_yaml_snippet(raw_text, parsed_doc) is True, (
                f"Classifier failed to detect invalid myhome.yaml trap '{trap_name}'!"
            )
            with pytest.raises(vol.Invalid):
                validate.config_schema(parsed_doc)
            trap_file = tmp_path / f"{trap_name}.yaml"
            trap_file.write_text(raw_text, encoding="utf-8")
            hass.config_entries.async_update_entry(
                entry, options={"file_path": str(trap_file)}
            )
            loaded = await _load_live_yaml(hass, entry, {}, PLATFORMS, [])
            assert loaded is False, f"Invalid trap '{trap_name}' unexpectedly loaded!"
    finally:
        await hass.config_entries.async_remove(entry.entry_id)


# ---------------------------------------------------------------------------
# Gate 2b & 2c: Executable Service Call Snippets & End-to-End Service Bus Call
# ---------------------------------------------------------------------------

ALLOWED_SERVICE_ACTION_KEYS = {
    "action",
    "service",
    "target",
    "data",
    "data_template",
    "service_template",
    "metadata",
    "alias",
    "enabled",
    "continue_on_error",
    "response_variable",
    "id",
    *HA_TARGET_KEYS,
}


def _resolve_templates_for_schema(
    value: Any,
    field_key: str = "",
    default_entity_domain: str = "cover",
) -> Any:
    """Substitute concrete typed values only for Jinja templates or !input placeholders."""
    if isinstance(value, str):
        if value.startswith("__ha_input__:"):
            input_name = value.split(":", 1)[1]
            if "entity" in input_name or field_key == "entity_id":
                return f"{default_entity_domain}.sample_device"
            if "duration" in input_name or "seconds" in input_name:
                return 30
            return "sample"
        if "{{" in value and "}}" in value:
            if field_key in {
                "travel_time",
                "travel_time_down",
                "travel_time_up",
                "slat_time",
                "duration",
                "seconds",
                "transition",
            }:
                return 20.0
            if field_key in {"min_motor_pulse"}:
                return 0.5
            if field_key == "frequency":
                return 98.5
            if field_key in {"hours"}:
                return 1
            if field_key in {"minutes", "station"}:
                return 5
            if field_key in {"brightness"}:
                return 200
            if field_key in {"brightness_pct"}:
                return 75
            if field_key in {"kelvin", "color_temp_kelvin"}:
                return 3000
            if field_key == "entity_id":
                return f"{default_entity_domain}.sample_device"
            return "sample_value"
        return value
    if isinstance(value, dict):
        return {
            k: _resolve_templates_for_schema(v, str(k), default_entity_domain)
            for k, v in value.items()
        }
    if isinstance(value, list):
        return [
            _resolve_templates_for_schema(v, field_key, default_entity_domain)
            for v in value
        ]
    return value


def _collect_service_calls(obj: Any, loc: str, out: list[tuple[str, str, dict[str, Any]]]) -> None:
    """Recursively collect dicts containing action:/service: targeting myhome.* or light/switch.turn_on."""
    if isinstance(obj, dict):
        svc = obj.get("action") or obj.get("service")
        if isinstance(svc, str) and (
            svc.startswith("myhome.") or svc in {"light.turn_on", "switch.turn_on"}
        ):
            out.append((loc, svc, obj))
        for val in obj.values():
            _collect_service_calls(val, loc, out)
    elif isinstance(obj, list):
        for item in obj:
            _collect_service_calls(item, loc, out)


def _validate_extracted_service_call(
    loc: str,
    full_service: str,
    raw_call: dict[str, Any],
    myhome_services: dict[str, Any],
    light_turn_on_schema: Any,
    switch_turn_on_schema: Any,
) -> None:
    """Validate wrapper structure (no stray root/target keys) and Voluptuous schema for one action dict."""
    domain, svc_name = full_service.split(".", 1)
    default_entity_domain = domain if domain in {"light", "switch"} else "cover"

    extra_root = sorted(
        {str(k) for k in raw_call} - ALLOWED_SERVICE_ACTION_KEYS
    )
    if extra_root:
        raise vol.Invalid(
            f"{loc} ({full_service}) has invalid/misplaced top-level action key(s) {extra_root}; "
            "service parameters belong under 'data:'"
        )

    merged_payload: dict[str, Any] = {}
    target_part = raw_call.get("target")
    if isinstance(target_part, dict):
        extra_target = sorted({str(k) for k in target_part} - HA_TARGET_KEYS)
        if extra_target:
            raise vol.Invalid(
                f"{loc} ({full_service}) has invalid target key(s) {extra_target}"
            )
        merged_payload.update(target_part)
    elif isinstance(target_part, str) and (
        target_part.startswith("__ha_input__:")
        or ("{{" in target_part and "}}" in target_part)
    ):
        merged_payload["entity_id"] = f"{default_entity_domain}.sample_device"
    elif target_part is not None:
        raise vol.Invalid(
            f"{loc} ({full_service}) has invalid target structure: {target_part!r}"
        )

    for tkey in HA_TARGET_KEYS:
        if tkey in raw_call and tkey not in merged_payload:
            merged_payload[tkey] = raw_call[tkey]

    for data_key in ("data", "data_template"):
        data_part = raw_call.get(data_key)
        if isinstance(data_part, dict):
            merged_payload.update(data_part)
        elif data_part is not None and not (
            isinstance(data_part, str)
            and (
                data_part.startswith("__ha_input__:")
                or ("{{" in data_part and "}}" in data_part)
            )
        ):
            raise vol.Invalid(
                f"{loc} ({full_service}) has non-mapping {data_key}: {data_part!r}"
            )

    resolved_payload = _resolve_templates_for_schema(
        merged_payload, default_entity_domain=default_entity_domain
    )

    if domain == DOMAIN:
        assert svc_name in myhome_services, (
            f"{loc} references unregistered service '{full_service}'"
        )
        schema = myhome_services[svc_name].schema
        assert schema is not None
        schema(resolved_payload)
    elif full_service == "light.turn_on":
        light_turn_on_schema(resolved_payload)
    elif full_service == "switch.turn_on":
        switch_turn_on_schema(resolved_payload)


async def test_all_fenced_service_call_snippets_match_runtime_schemas(
    hass: HomeAssistant,
    tmp_path: Path,
) -> None:
    """Every fenced YAML and blueprint service call validates against live Voluptuous schemas."""
    entry = await _setup_entry_with_services(hass, tmp_path)
    try:
        calls: list[tuple[str, str, dict[str, Any]]] = []
        for loc, _, doc in _extract_yaml_documents():
            _collect_service_calls(doc, loc, calls)

        for bp_path in sorted((REPO_ROOT / "blueprints").rglob("*.yaml")):
            bp_rel = bp_path.relative_to(REPO_ROOT).as_posix()
            bp_doc = yaml.load(bp_path.read_text(encoding="utf-8"), Loader=_DocYamlLoader)
            _collect_service_calls(bp_doc, bp_rel, calls)

        assert len(calls) >= 25, f"Expected >= 25 service calls, found {len(calls)}"

        light_turn_on_schema = cv.make_entity_service_schema(LIGHT_TURN_ON_SCHEMA)
        switch_turn_on_schema = cv.make_entity_service_schema({})
        myhome_services = hass.services._services[DOMAIN]

        for loc, full_service, raw_call in calls:
            _validate_extracted_service_call(
                loc,
                full_service,
                raw_call,
                myhome_services,
                light_turn_on_schema,
                switch_turn_on_schema,
            )

        # Negative assertions: both inside data: AND misplaced on root/target must fail
        misplaced_and_invalid_snippets = [
            "service: myhome.start_sending_instant_power\nmeter_id: 1\ninterval: 10\n",
            "action: light.turn_on\nentity_id: light.kitchen\ntimer: 60\n",
            "action: switch.turn_on\ntarget:\n  entity_id: switch.boiler\nduration: 60\n",
            "action: myhome.turn_on_timed\ntarget:\n  entity_id: light.kitchen\ntime: 120\n",
            "action: myhome.turn_on_timed\ntarget:\n  entity_id: light.kitchen\ndata:\n  time: 120\n",
            "action: myhome.start_sending_instant_power\ntarget:\n  meter_id: 1\n",
        ]
        for idx, bad_yaml in enumerate(misplaced_and_invalid_snippets):
            bad_calls: list[tuple[str, str, dict[str, Any]]] = []
            _collect_service_calls(
                yaml.load(bad_yaml, Loader=_DocYamlLoader), f"bad#{idx}", bad_calls
            )
            assert len(bad_calls) == 1
            with pytest.raises(vol.Invalid):
                _validate_extracted_service_call(
                    bad_calls[0][0],
                    bad_calls[0][1],
                    bad_calls[0][2],
                    myhome_services,
                    light_turn_on_schema,
                    switch_turn_on_schema,
                )

        # Edge-case stress test: multi-document (---) YAML fence + deeply nested
        # choose / repeat / if-then-else / parallel blocks with Jinja templates in target & data
        complex_multidoc_yaml = """
action: myhome.sync_time
---
alias: Complex Nested Automation
triggers:
  - trigger: state
    entity_id: binary_sensor.motion
actions:
  - choose:
      - conditions:
          - condition: template
            value_template: "{{ true }}"
        sequence:
          - repeat:
              count: 2
              sequence:
                - if:
                    - condition: template
                      value_template: "{{ repeat.index == 1 }}"
                  then:
                    - action: myhome.turn_on_timed
                      target:
                        entity_id: "{{ target_light }}"
                      data:
                        duration: "{{ dur_seconds }}"
                        brightness_pct: "{{ level_pct }}"
                  else:
                    - parallel:
                        - action: light.turn_on
                          target: "{{ fallback_target }}"
                          data:
                            transition: "{{ fade_secs }}"
                            color_temp_kelvin: "{{ kelvin_val }}"
                        - action: myhome.tuner_store_station
                          target:
                            entity_id: "{{ tuner_entity }}"
                          data:
                            station: "{{ slot_num }}"
"""
        nested_calls: list[tuple[str, str, dict[str, Any]]] = []
        for doc_idx, sub_doc in enumerate(
            yaml.load_all(complex_multidoc_yaml, Loader=_DocYamlLoader)
        ):
            _collect_service_calls(sub_doc, f"multidoc#{doc_idx}", nested_calls)
        assert len(nested_calls) == 4
        for n_loc, n_svc, n_raw in nested_calls:
            _validate_extracted_service_call(
                n_loc,
                n_svc,
                n_raw,
                myhome_services,
                light_turn_on_schema,
                switch_turn_on_schema,
            )
    finally:
        await hass.config_entries.async_unload(entry.entry_id)


async def test_turn_on_timed_end_to_end_via_hass_service_bus(
    hass: HomeAssistant,
    tmp_path: Path,
) -> None:
    """Calling myhome.turn_on_timed with brightness_pct and brightness succeeds via hass.services."""
    yaml_cfg = (
        "light:\n"
        "  hall_dimmer:\n"
        "    where: '11'\n"
        "    name: 'Hall Dimmer'\n"
        "    dimmable: true\n"
        "switch:\n"
        "  garden_pump:\n"
        "    where: '12'\n"
        "    name: 'Garden Pump'\n"
    )
    entry = await _setup_entry_with_services(
        hass, tmp_path, mac="00:03:50:11:22:33", yaml_content=yaml_cfg
    )
    try:
        assert hass.states.get("light.hall_dimmer") is not None
        assert hass.states.get("switch.garden_pump") is not None

        with patch(
            "custom_components.myhome.gateway.MyHOMEGatewayHandler.send",
            return_value=True,
        ) as mock_send:
            await hass.services.async_call(
                DOMAIN,
                "turn_on_timed",
                {
                    "entity_id": "light.hall_dimmer",
                    "duration": 120,
                    "brightness_pct": 75,
                },
                blocking=True,
            )
            await hass.services.async_call(
                DOMAIN,
                "turn_on_timed",
                {
                    "entity_id": "switch.garden_pump",
                    "duration": 60,
                    "brightness": 128,
                },
                blocking=True,
            )
            assert [str(c.args[0]) for c in mock_send.call_args_list] == [
                "*#1*11*#1*175*0##",
                "*1*12*11##",
                "*1*11*12##",
            ]
    finally:
        await hass.config_entries.async_unload(entry.entry_id)


# ---------------------------------------------------------------------------
# Gate 2d: SDomotica Migration Round-Trip Across All Accessory Types + Icons
# ---------------------------------------------------------------------------


async def test_sdomotica_migration_roundtrip_all_accessory_types_with_icons(
    hass: HomeAssistant,
    tmp_path: Path,
) -> None:
    """Generated myhome.yaml across all SDomotica accessory types with icons passes live schema."""
    sdomotica_config = {
        "platforms": [
            {
                "platform": "MyHome2",
                "host": "192.168.1.35",
                "password": "12345",
                "accessories": [
                    {"type": "Lightbulb", "name": "Relay Light", "address": "11", "can_dim": False},
                    {"type": "Lightbulb", "name": "Dimmer Light", "address": "12", "can_dim": True},
                    {"type": "Lightbulb", "name": "Local Bus Light", "address": "13#4#01", "can_dim": False},
                    {"type": "Outlets", "name": "Desk Outlet", "address": "21"},
                    {"type": "Switch", "name": "Boiler Relay", "address": "22"},
                    {"type": "Button", "name": "Bell Relay", "address": "23"},
                    {"type": "Door", "name": "Front Door Strike", "address": "24"},
                    {"type": "Windows", "name": "Bedroom Shutter", "address": "31", "time": 25},
                    {"type": "WindowsAdvance", "name": "Living Shutter", "address": "32", "time": 32},
                    {"type": "Sensor", "name": "Hall Contact", "address": "3011"},
                    {"type": "Sensor3477", "name": "Window Contact", "address": "3012"},
                    {"type": "Sensor3477inv", "name": "Garage Contact", "address": "3013"},
                    {"type": "Energy", "name": "Main Energy", "address": "51"},
                    {"type": "F522", "name": "Oven Meter", "address": "52"},
                    {"type": "F523", "name": "Pump Meter", "address": "53"},
                    {"type": "Thermostat", "name": "Living Thermo", "address": "1"},
                    {"type": "4ZThermo", "name": "Central 4Z", "address": "2"},
                    {"type": "SAThermoHC", "name": "Office HC", "address": "3"},
                    {"type": "SAThermoC", "name": "Attic Cool", "address": "4"},
                    {"type": "SAThermoH", "name": "Bath Heat", "address": "5"},
                    {"type": "TemperatureSensors", "name": "Outdoor Probe", "address": "501"},
                    {"type": "TemperatureSensorsInternal", "name": "Indoor Probe", "address": "101"},
                    {"type": "Audio", "name": "Kitchen Audio", "address": "11"},
                    {"type": "SecuritySystem", "name": "Home Alarm", "zone": "8"},
                ],
            }
        ]
    }

    entities, _ = extract_sdomotica_config_json(sdomotica_config)
    assert len(entities) == 24

    door_entities = [e for e in entities if e.name == "Front Door Strike"]
    assert len(door_entities) == 1
    assert door_entities[0].domain == "switch"
    assert door_entities[0].who == "1"

    for ent in entities:
        ent.icon = "mdi:home-automation"
        ent.area_id = "living_room"

    mac = "00:03:50:aa:bb:cc"
    generated_yaml = generate_myhome_yaml(entities, gateway_mac=mac)
    parsed = yaml.safe_load(generated_yaml)
    assert isinstance(parsed, dict) and "myhome" in parsed

    # Verify icon: is emitted ONLY under light and switch
    for domain_key, domain_entries in parsed["myhome"].items():
        if not isinstance(domain_entries, dict):
            continue
        for slug, item_cfg in domain_entries.items():
            if not isinstance(item_cfg, dict):
                continue
            if domain_key in {"light", "switch"}:
                assert item_cfg.get("icon") == "mdi:home-automation", (
                    f"Expected icon on {domain_key}.{slug}"
                )
            else:
                assert "icon" not in item_cfg, (
                    f"Unexpected icon emitted on unsupported domain {domain_key}.{slug}"
                )

    yaml_file = tmp_path / "migrated_myhome.yaml"
    yaml_file.write_text(generated_yaml, encoding="utf-8")
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={"mac": mac},
        options={"file_path": str(yaml_file)},
        unique_id=mac,
    )
    entry.add_to_hass(hass)
    try:
        configured_platforms: dict[str, dict[str, dict[str, Any]]] = {
            str(p): {} for p in PLATFORMS
        }
        assert await _load_live_yaml(hass, entry, configured_platforms, PLATFORMS, [])
        assert any(
            cfg.get("name") == "Front Door Strike" and cfg.get("where") == "24"
            for cfg in configured_platforms["switch"].values()
        )
    finally:
        await hass.config_entries.async_remove(entry.entry_id)


# ---------------------------------------------------------------------------
# Gate 3: Runtime Service Schema Introspection & verify_ha_standards Negative Tests
# ---------------------------------------------------------------------------


def _iter_child_validators(obj: Any) -> tuple[Any, ...]:
    """Yield child validators across both voluptuous (.validators) and probatio (._validators / ._pair)."""
    for attr in ("validators", "_validators", "_pair"):
        children = getattr(obj, attr, None)
        if children:
            return tuple(children)
    return ()


def _extract_vol_range(validator: Any) -> tuple[float | None, float | None]:
    """Recursively find vol.Range(min, max) inside a Voluptuous/probatio validator tree."""
    if isinstance(validator, vol.Range) or type(validator).__name__ == "Range":
        return getattr(validator, "min", None), getattr(validator, "max", None)
    for child in _iter_child_validators(validator):
        lo, hi = _extract_vol_range(child)
        if lo is not None or hi is not None:
            return lo, hi
    return None, None


def _unwrap_schema_dict(schema_obj: Any) -> dict[Any, Any]:
    """Extract the underlying mapping dict from a Voluptuous/probatio Schema or All wrapper."""
    inner = getattr(schema_obj, "schema", None)
    if isinstance(inner, dict):
        return inner
    if inner is not None and inner is not schema_obj:
        found = _unwrap_schema_dict(inner)
        if found:
            return found
    for child in _iter_child_validators(schema_obj):
        found = _unwrap_schema_dict(child)
        if found:
            return found
    return {}


async def test_runtime_service_schemas_match_services_yaml(
    hass: HomeAssistant,
    tmp_path: Path,
) -> None:
    """All 17 registered Voluptuous service schemas match services.yaml fields and bounds."""
    entry = await _setup_entry_with_services(hass, tmp_path)
    try:
        services_yaml = yaml.safe_load(SERVICES_YAML_PATH.read_text(encoding="utf-8"))
        registered = hass.services._services[DOMAIN]

        assert set(registered) == set(services_yaml)

        handler_enforced_ranges: dict[tuple[str, str], tuple[float, float]] = {
            ("set_cover_travel_time", "travel_time"): (CALIBRATION_MIN_RUN, CALIBRATION_MAX_RUN),
            ("set_cover_travel_time", "travel_time_down"): (CALIBRATION_MIN_RUN, CALIBRATION_MAX_RUN),
            ("set_cover_travel_time", "travel_time_up"): (CALIBRATION_MIN_RUN, CALIBRATION_MAX_RUN),
            ("set_cover_travel_time", "min_motor_pulse"): (
                CALIBRATION_MIN_MOTOR_PULSE,
                CALIBRATION_MAX_MOTOR_PULSE,
            ),
            ("set_cover_travel_time", "slat_time"): (MIN_SLAT_TIME, MAX_SLAT_TIME),
            ("tuner_set_frequency", "frequency"): (FM_MIN_KHZ / 1000.0, FM_MAX_KHZ / 1000.0),
            ("tuner_store_station", "station"): (1, TUNER_MAX_STATION_COUNT),
        }

        for svc_name, svc_doc in services_yaml.items():
            service_obj = registered[svc_name]
            assert service_obj.schema is not None, f"{svc_name} registered schema=None"

            yaml_fields = svc_doc.get("fields") or {}
            schema_dict = _unwrap_schema_dict(service_obj.schema)
            assert isinstance(schema_dict, dict)

            schema_markers: dict[str, Any] = {}
            schema_validators: dict[str, Any] = {}
            is_entity_service = "target" in svc_doc or "entity_id" in yaml_fields
            for key_marker, val_validator in schema_dict.items():
                if type(key_marker).__name__ == "Remove":
                    continue
                key_name = str(
                    key_marker.schema
                    if hasattr(key_marker, "schema")
                    else key_marker
                )
                if key_name in HA_TARGET_KEYS and is_entity_service and key_name not in yaml_fields:
                    continue
                schema_markers[key_name] = key_marker
                schema_validators[key_name] = val_validator

            assert set(schema_validators) == set(yaml_fields), (
                f"Field key mismatch on myhome.{svc_name}: "
                f"runtime={sorted(schema_validators)} vs services.yaml={sorted(yaml_fields)}"
            )

            for field_name, field_doc in yaml_fields.items():
                is_yaml_required = bool(field_doc.get("required", False))
                marker = schema_markers.get(field_name)
                marker_cls = type(marker).__name__
                if svc_name == "send_scenario_plus_command" and field_name in {"where", "action"}:
                    # Validated inside handle_send_scenario_plus_command to raise translated ServiceValidationError
                    assert marker_cls == "Optional"
                elif field_name == "entity_id":
                    # HA entity-service target key added by make_entity_service_schema
                    assert marker_cls in {"Optional", "str"}
                elif is_yaml_required:
                    assert marker_cls == "Required", (
                        f"myhome.{svc_name}.{field_name} is required:true in services.yaml "
                        f"but {marker_cls} in runtime schema"
                    )
                else:
                    assert marker_cls in {"Optional", "str"}, (
                        f"myhome.{svc_name}.{field_name} is optional in services.yaml "
                        f"but {marker_cls} in runtime schema"
                    )

                num_sel = (field_doc.get("selector") or {}).get("number")
                if isinstance(num_sel, dict):
                    yaml_min = num_sel.get("min")
                    yaml_max = num_sel.get("max")
                    vol_min, vol_max = _extract_vol_range(schema_validators[field_name])
                    if vol_min is not None or vol_max is not None:
                        assert float(vol_min) == float(yaml_min), (
                            f"Min mismatch on {svc_name}.{field_name}: {vol_min} != {yaml_min}"
                        )
                        assert float(vol_max) == float(yaml_max), (
                            f"Max mismatch on {svc_name}.{field_name}: {vol_max} != {yaml_max}"
                        )
                    else:
                        expected_lo, expected_hi = handler_enforced_ranges[(svc_name, field_name)]
                        assert float(expected_lo) == float(yaml_min)
                        assert float(expected_hi) == float(yaml_max)

        # Confirm send_scenario_plus_command raises ServiceValidationError when required where is omitted
        with pytest.raises(ServiceValidationError):
            await hass.services.async_call(
                DOMAIN, "send_scenario_plus_command", {"action": "on"}, blocking=True
            )
    finally:
        await hass.config_entries.async_unload(entry.entry_id)


def _create_gate3_sandbox(tmp_path: Path) -> Path:
    """Create a minimal sandbox tree mirroring service/icon files for Gate 3 mutation tests."""
    cc_dir = tmp_path / "custom_components" / "myhome"
    cc_dir.mkdir(parents=True)
    docs_cfg = tmp_path / "docs" / "configuration"
    docs_cfg.mkdir(parents=True)

    src_cc = REPO_ROOT / "custom_components" / "myhome"
    for fname in ("services.yaml", "strings.json", "icons.json"):
        (cc_dir / fname).write_text(
            (src_cc / fname).read_text(encoding="utf-8"),
            encoding="utf-8",
        )
    for py_file in src_cc.glob("*.py"):
        (cc_dir / py_file.name).write_text(
            py_file.read_text(encoding="utf-8"),
            encoding="utf-8",
        )
    (docs_cfg / "services.md").write_text(
        (DOCS_DIR / "configuration" / "services.md").read_text(encoding="utf-8"),
        encoding="utf-8",
    )
    return tmp_path


def test_gate3_verify_ha_standards_catches_regressions(tmp_path: Path) -> None:
    """verify_ha_standards.check_service_and_icon_parity catches schema=None and missing icons/docs."""
    sandbox = _create_gate3_sandbox(tmp_path)

    # Baseline passes cleanly
    checker = verify_ha.StandardsChecker()
    verify_ha.check_service_and_icon_parity(checker, root_dir=sandbox)
    assert checker.errors == []

    # 1. Inject schema=None into services.py
    svc_py = sandbox / "custom_components" / "myhome" / "services.py"
    orig_svc_py = svc_py.read_text(encoding="utf-8")
    svc_py.write_text(
        orig_svc_py.replace("schema=as_any(SYNC_TIME_SCHEMA)", "schema=None", 1),
        encoding="utf-8",
    )
    checker = verify_ha.StandardsChecker()
    verify_ha.check_service_and_icon_parity(checker, root_dir=sandbox)
    assert any("non-None schema" in e for e in checker.errors)
    svc_py.write_text(orig_svc_py, encoding="utf-8")

    # 2. Remove an entity translation_key from icons.json
    icons_path = sandbox / "custom_components" / "myhome" / "icons.json"
    orig_icons = icons_path.read_text(encoding="utf-8")
    icons_path.write_text(orig_icons.replace('"sync_time"', '"sync_time_broken"'), encoding="utf-8")
    checker = verify_ha.StandardsChecker()
    verify_ha.check_service_and_icon_parity(checker, root_dir=sandbox)
    assert any("sync_time" in e for e in checker.errors)
    icons_path.write_text(orig_icons, encoding="utf-8")

    # 3. Remove a service field from docs/configuration/services.md
    svc_md = sandbox / "docs" / "configuration" / "services.md"
    orig_md = svc_md.read_text(encoding="utf-8")
    svc_md.write_text(orig_md.replace("`brightness_pct`", "`removed_field`"), encoding="utf-8")
    checker = verify_ha.StandardsChecker()
    verify_ha.check_service_and_icon_parity(checker, root_dir=sandbox)
    assert any("brightness_pct" in e for e in checker.errors)
    svc_md.write_text(orig_md, encoding="utf-8")

    # 4. Inject an untranslated _attr_translation_key into a helper module (thermo_switch.py)
    helper_py = sandbox / "custom_components" / "myhome" / "thermo_switch.py"
    orig_helper = helper_py.read_text(encoding="utf-8")
    helper_py.write_text(
        orig_helper + '\n_attr_translation_key = "untranslated_helper_key"\n',
        encoding="utf-8",
    )
    checker = verify_ha.StandardsChecker()
    verify_ha.check_service_and_icon_parity(checker, root_dir=sandbox)
    assert any("untranslated_helper_key" in e for e in checker.errors)
    helper_py.write_text(orig_helper, encoding="utf-8")


# ---------------------------------------------------------------------------
# Gate 4: Auto-Discovery, WHO Subsystem Matrix, CEN/CEN+ & Events Contracts
# ---------------------------------------------------------------------------


def test_runtime_discovery_triggers_and_events_contract() -> None:
    """Verify runtime DISCOVERY_REQUESTS, PlatformDiscovery AST, device_trigger, and Gate 4 check."""
    assert {w for w, _ in DISCOVERY_REQUESTS} == {1, 2, 4, 5, 16, 18}
    assert 15 not in {w for w, _ in DISCOVERY_REQUESTS}
    assert 25 not in {w for w, _ in DISCOVERY_REQUESTS}

    pd_contracts = sync_doc._extract_platform_discovery_contracts(
        REPO_ROOT / "custom_components" / "myhome"
    )
    assert pd_contracts == {
        ("light", "1"): True,
        ("switch", "1"): False,
        ("switch", "4"): False,
        ("cover", "2"): True,
        ("cover", "1"): False,
        ("climate", "4"): True,
        ("binary_sensor", "25"): True,
        ("binary_sensor", "9"): False,
        ("binary_sensor", "1"): True,
        ("sensor", "18"): True,
        ("sensor", "1"): True,
        ("sensor", "4"): True,
        ("media_player", "16"): True,
        ("alarm_control_panel", "5"): True,
        ("lock", "6"): False,
    }

    assert "pushbutton_short_release" in device_trigger._UNSUPPORTED_TRIGGER_TYPES["25"]
    assert len(device_trigger.TRIGGER_SUBTYPES) == 32
    assert device_trigger.TRIGGER_SUBTYPES[0] == "button_0"
    assert device_trigger.TRIGGER_SUBTYPES[-1] == "button_31"

    assert sync_doc.check_discovery_and_subsystem_docs()[0] is True


def test_gate4_discovery_and_subsystem_negative_mutations(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Gate 4 catches false auto-discovery claims, startup sweep drift, unimplemented WHOs, and CEN+ drift."""
    fake_docs = tmp_path / "docs"
    for src in DOCS_DIR.rglob("*.md"):
        dst = fake_docs / src.relative_to(DOCS_DIR)
        dst.parent.mkdir(parents=True, exist_ok=True)
        dst.write_text(src.read_text(encoding="utf-8"), encoding="utf-8")

    monkeypatch.setattr(sync_doc, "DOCS_DIR", fake_docs)
    assert sync_doc.check_discovery_and_subsystem_docs()[0] is True

    # Mutation A: false WHO=1 switch auto-discovery claim
    sw_md = fake_docs / "configuration" / "switches.md"
    orig_sw = sw_md.read_text(encoding="utf-8")
    sw_md.write_text(
        orig_sw + "\nWHO = 1 switches are automatically discovered from bus traffic.\n",
        encoding="utf-8",
    )
    assert sync_doc.check_discovery_and_subsystem_docs()[0] is False
    sw_md.write_text(orig_sw, encoding="utf-8")

    # Mutation B: startup sweep WHO list drift in docs/configuration/README.md (adding WHO 15)
    cfg_readme = fake_docs / "configuration" / "README.md"
    orig_cfg_readme = cfg_readme.read_text(encoding="utf-8")
    cfg_readme.write_text(
        orig_cfg_readme.replace(
            "`WHO = 1, 2, 4, 5, 16, 18`",
            "`WHO = 1, 2, 4, 5, 15, 16, 18`",
            1,
        ),
        encoding="utf-8",
    )
    assert sync_doc.check_discovery_and_subsystem_docs()[0] is False
    cfg_readme.write_text(orig_cfg_readme, encoding="utf-8")

    # Mutation C: assign HA entity platform to unimplemented WHO 24
    who_md = fake_docs / "openwebnet-who-specifications.md"
    orig_who = who_md.read_text(encoding="utf-8")
    who_md.write_text(
        re.sub(
            r"(\|\s*\*\*24\*\*.*?\|\s*)— \(not implemented\)",
            r"\1`light`",
            orig_who,
            count=1,
        ),
        encoding="utf-8",
    )
    assert sync_doc.check_discovery_and_subsystem_docs()[0] is False
    who_md.write_text(orig_who, encoding="utf-8")

    # Mutation D: mark pushbutton_short_release as CEN+ ✅ in supported_functions.md
    sf_md = fake_docs / "configuration" / "supported_functions.md"
    orig_sf = sf_md.read_text(encoding="utf-8")
    sf_md.write_text(
        orig_sf.replace(
            "| Short release (`pushbutton_short_release`) | ✅ | ❌ |",
            "| Short release (`pushbutton_short_release`) | ✅ | ✅ |",
        ),
        encoding="utf-8",
    )
    assert sync_doc.check_discovery_and_subsystem_docs()[0] is False
    sf_md.write_text(orig_sf, encoding="utf-8")

    # Mutation E: 1..31 subtype range in cen_cenplus.md
    cen_md = fake_docs / "configuration" / "cen_cenplus.md"
    orig_cen = cen_md.read_text(encoding="utf-8")
    cen_md.write_text(orig_cen + "\nButtons 1..31 are exposed.\n", encoding="utf-8")
    assert sync_doc.check_discovery_and_subsystem_docs()[0] is False
    cen_md.write_text(orig_cen, encoding="utf-8")


# ---------------------------------------------------------------------------
# Gates 1, 5, 6, 7: WHO Labels, Bus Monitor/WS, Blueprints/Branch, Packaging
# ---------------------------------------------------------------------------


def test_gate1_who_labels_parity_catches_drift(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Gate 1 fails if WHO 9 is mislabeled 'Power' or WHO 25 is mislabeled 'Diag'."""
    assert sync_doc.check_who_labels_parity()[0] is True

    fake_trace = tmp_path / "update_trace_matrix.py"
    orig_trace = (REPO_ROOT / "scripts" / "update_trace_matrix.py").read_text(encoding="utf-8")
    fake_trace.write_text(
        orig_trace.replace('9: "Aux",', '9: "Power",').replace('25: "CEN+ / Dry Contact",', '25: "Diag",'),
        encoding="utf-8",
    )
    monkeypatch.setattr(sync_doc, "UPDATE_TRACE_MATRIX_PY", fake_trace)
    assert sync_doc.check_who_labels_parity()[0] is False


def test_gate5_bus_monitor_and_ws_docs_catches_drift(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Gate 5 fails on nonexistent WS endpoints, missing/removed toolbar buttons, or max_frames > WS limit."""
    orig_js = (
        REPO_ROOT / "custom_components" / "myhome" / "frontend" / "myhome-bus-card.js"
    ).read_text(encoding="utf-8")
    extracted_btns = sync_doc._extract_bus_card_toolbar_buttons(
        sync_doc.BUS_VIEW_JS.read_text(encoding="utf-8"),
        sync_doc.BUS_TRANSLATIONS_JS.read_text(encoding="utf-8"),
    )
    assert extracted_btns == [
        "🔴 Start Trace",
        "🧹 Sweep Bus",
        "💾 Export Trace",
        "📋 Copy Trace",
        "Pause",
        "Clear",
        "⏹ Stop Trace",
    ]
    assert sync_doc.check_bus_monitor_and_ws_docs()[0] is True

    fake_docs = tmp_path / "docs"
    (fake_docs / "configuration").mkdir(parents=True)
    bus_doc = fake_docs / "configuration" / "bus_monitor.md"
    orig_bus_doc = (DOCS_DIR / "configuration" / "bus_monitor.md").read_text(encoding="utf-8")
    bus_doc.write_text(
        orig_bus_doc + "\nSubscribe via `myhome/bus_monitor/sub` and click Report Issue / Copy Trace.\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(sync_doc, "DOCS_DIR", fake_docs)
    assert sync_doc.check_bus_monitor_and_ws_docs()[0] is False

    # Missing real toolbar button label ("🧹 Sweep Bus") in bus_monitor.md must fail
    bus_doc.write_text(orig_bus_doc.replace("🧹 Sweep Bus", "Removed Sweep"), encoding="utf-8")
    assert sync_doc.check_bus_monitor_and_ws_docs()[0] is False

    # Restore clean docs and test max_frames > SCHEMA_WS_HISTORY max
    bus_doc.write_text(orig_bus_doc, encoding="utf-8")
    assert sync_doc.check_bus_monitor_and_ws_docs()[0] is True

    fake_js = tmp_path / "myhome-bus-card.js"
    fake_js.write_text(
        orig_js.replace("max: 1000", "max: 5000", 1),
        encoding="utf-8",
    )
    monkeypatch.setattr(sync_doc, "BUS_CARD_JS", fake_js)
    assert sync_doc.check_bus_monitor_and_ws_docs()[0] is False


def test_monitor_docs_follow_shared_view_and_translated_labels(tmp_path, monkeypatch):
    """A drift in the panel's actual labels/catalog cannot hide behind the adapter."""
    translations = tmp_path / "panel-bus-translations.js"
    translations.write_text(sync_doc.BUS_TRANSLATIONS_JS.read_text(encoding="utf-8").replace(
        '"stopTrace": "⏹ Stop Trace"', '"stopTrace": "Finish capture"'), encoding="utf-8")
    with monkeypatch.context() as patch:
        patch.setattr(sync_doc, "BUS_TRANSLATIONS_JS", translations)
        ok, messages = sync_doc.check_bus_monitor_and_ws_docs()
        assert not ok
        assert any("Finish capture" in item for item in messages)

    view = tmp_path / "panel-bus-monitor-view.js"
    view.write_text(sync_doc.BUS_VIEW_JS.read_text(encoding="utf-8").replace(
        'name: "Auxiliary", short: "Auxiliary"', 'name: "Power", short: "Power"'), encoding="utf-8")
    monkeypatch.setattr(sync_doc, "BUS_VIEW_JS", view)
    assert sync_doc.check_who_labels_parity()[0] is False


def test_gate6_blueprints_and_stable_version_branch_transition(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Gate 6 rejects lock-only 'Open Gate' claims and auto-rewrites branch URLs on 2.0.0 stable."""
    assert sync_doc.sync_blueprints_and_branch_urls(update=False)[0] is True

    # Setup sandbox copies of blueprints, mkdocs, hardware-coupled-sensors.md, and manifest.json
    bp_dir = tmp_path / "blueprints" / "automation" / "myhome"
    bp_dir.mkdir(parents=True)
    for src_bp in (REPO_ROOT / "blueprints" / "automation" / "myhome").glob("*.yaml"):
        (bp_dir / src_bp.name).write_text(src_bp.read_text(encoding="utf-8"), encoding="utf-8")

    fake_docs = tmp_path / "docs"
    (fake_docs / "guides").mkdir(parents=True)
    hw_guide = fake_docs / "guides" / "hardware-coupled-sensors.md"
    hw_guide.write_text(
        (DOCS_DIR / "guides" / "hardware-coupled-sensors.md").read_text(encoding="utf-8"),
        encoding="utf-8",
    )
    mkdocs_main = tmp_path / "mkdocs.yml"
    mkdocs_main.write_text((REPO_ROOT / "mkdocs.yml").read_text(encoding="utf-8"), encoding="utf-8")
    mkdocs_v09 = tmp_path / "mkdocs-v0.9.yml"
    mkdocs_v09.write_text(
        (REPO_ROOT / "mkdocs-v0.9.yml").read_text(encoding="utf-8"), encoding="utf-8"
    )
    fake_manifest = tmp_path / "manifest.json"
    orig_manifest = (REPO_ROOT / "custom_components" / "myhome" / "manifest.json").read_text(
        encoding="utf-8"
    )
    fake_manifest.write_text(orig_manifest, encoding="utf-8")

    monkeypatch.setattr(sync_doc, "BLUEPRINTS_DIR", bp_dir)
    monkeypatch.setattr(sync_doc, "DOCS_DIR", fake_docs)
    monkeypatch.setattr(sync_doc, "MKDOCS_YML", mkdocs_main)
    monkeypatch.setattr(sync_doc, "MKDOCS_V09_YML", mkdocs_v09)
    monkeypatch.setattr(sync_doc, "MANIFEST_JSON", fake_manifest)

    assert sync_doc.sync_blueprints_and_branch_urls(update=False)[0] is True

    # 1. Inject false "Open Gate" button label into the domain: lock-only blueprint
    door_bp = bp_dir / "doorbell_intercom_notification.yaml"
    orig_door_bp = door_bp.read_text(encoding="utf-8")
    door_bp.write_text(
        orig_door_bp.replace("Release Lock", "Open Gate", 1),
        encoding="utf-8",
    )
    assert sync_doc.sync_blueprints_and_branch_urls(update=False)[0] is False
    door_bp.write_text(orig_door_bp, encoding="utf-8")

    # 2. Verify bidirectional branch transitions (beta <-> stable) regardless of
    #    whether the live repo is currently on a pre-release or stable version:
    #    a) Put sandbox into beta ("2.0.0b18" -> v2-phase1-architecture)
    fake_manifest.write_text(
        re.sub(r'"version":\s*"[^"]+"', '"version": "2.0.0b18"', orig_manifest),
        encoding="utf-8",
    )
    if not re.search(r'"version":\s*"[^"]*(?:a|b|rc)', orig_manifest):
        assert sync_doc.sync_blueprints_and_branch_urls(update=False)[0] is False
    assert sync_doc.sync_blueprints_and_branch_urls(update=True)[0] is True
    assert sync_doc.sync_blueprints_and_branch_urls(update=False)[0] is True
    assert "/blob/v2-phase1-architecture/blueprints/" in door_bp.read_text(encoding="utf-8")

    #    b) Transition sandbox from beta to stable ("2.0.0" -> master):
    #       - check mode must FAIL while URLs point to v2-phase1-architecture
    #       - update mode must rewrite all 4 target groups to master
    #       - subsequent check mode must PASS
    fake_manifest.write_text(
        re.sub(r'"version":\s*"[^"]+"', '"version": "2.0.0"', orig_manifest),
        encoding="utf-8",
    )
    assert sync_doc.sync_blueprints_and_branch_urls(update=False)[0] is False
    assert sync_doc.sync_blueprints_and_branch_urls(update=True)[0] is True
    assert sync_doc.sync_blueprints_and_branch_urls(update=False)[0] is True

    assert "v2-phase1-architecture" not in door_bp.read_text(encoding="utf-8")
    assert "/blob/master/blueprints/" in door_bp.read_text(encoding="utf-8")
    assert "edit_uri: edit/master/docs/" in mkdocs_main.read_text(encoding="utf-8")
    assert "edit_uri: edit/master/docs-v0.9/" in mkdocs_v09.read_text(encoding="utf-8")
    assert "v2-phase1-architecture" not in hw_guide.read_text(encoding="utf-8")


def test_gate7_packaging_metadata_drift_and_autofix(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Gate 7 catches pyproject.toml / CONTRIBUTING.md drift and auto-updates pyproject.toml."""
    assert sync_doc.sync_packaging_metadata(update=False)[0] is True

    cc_dir = tmp_path / "custom_components" / "myhome"
    cc_dir.mkdir(parents=True)
    (cc_dir / "manifest.json").write_text(
        (REPO_ROOT / "custom_components" / "myhome" / "manifest.json").read_text(encoding="utf-8"),
        encoding="utf-8",
    )
    drifted_pyproject = tmp_path / "pyproject.toml"
    drifted_pyproject.write_text(
        '[project]\nname = "myhome"\nversion = "2.0.0b1"\nrequires-python = ">=3.12"\n'
        'classifiers = [\n    "Programming Language :: Python :: 3.13",\n'
        '    "Programming Language :: Python :: 3.14",\n]\n'
        '[project.optional-dependencies]\ntest = [\n    "OWNd==2.0.0b1",\n]\n',
        encoding="utf-8",
    )
    drifted_contrib = tmp_path / "CONTRIBUTING.md"
    drifted_contrib.write_text("Requires Python 3.12+ in WSL.\n", encoding="utf-8")

    # Both verify_ha_standards and sync_documentation must fail on drifted metadata
    checker = verify_ha.StandardsChecker()
    verify_ha.check_packaging_metadata_rule(checker, root_dir=tmp_path)
    assert len(checker.errors) >= 4

    monkeypatch.setattr(sync_doc, "PYPROJECT_TOML", drifted_pyproject)
    monkeypatch.setattr(sync_doc, "CONTRIBUTING_MD", drifted_contrib)
    monkeypatch.setattr(sync_doc, "MANIFEST_JSON", cc_dir / "manifest.json")
    assert sync_doc.sync_packaging_metadata(update=False)[0] is False

    # Fix CONTRIBUTING.md to Python 3.14 and run sync_packaging_metadata(update=True)
    drifted_contrib.write_text("Requires Python 3.14 in WSL.\n", encoding="utf-8")
    assert sync_doc.sync_packaging_metadata(update=True)[0] is True
    assert sync_doc.sync_packaging_metadata(update=False)[0] is True

    checker2 = verify_ha.StandardsChecker()
    verify_ha.check_packaging_metadata_rule(checker2, root_dir=tmp_path)
    assert checker2.errors == []
