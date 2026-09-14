#!/usr/bin/env python3
"""SDomotica to MyHOME Frictionless Migration Tool.

This utility automates the migration from SDomotica to the native MyHOME
(OpenWebNet) Home Assistant integration, supporting all device types,
addressing modes, and configuration formats documented in the official
SDomotica Gateway Manual (Hassio_Sdomotica_manual.pdf).

Supported Ingestion Sources:
  1. Home Assistant Entity Registry (.storage/core.entity_registry)
  2. Sdomotica Gateway Config JSON (config.json, Homebridge format)
  3. Sdomotica Package YAML (packages/sdomoticabticino.yaml)

Migration Capabilities:
  1. YAML Generation (--generate-yaml):
     Generates a complete, schema-compliant myhome.yaml pre-keyed to preserve
     exact entity IDs, friendly names, F422 interface addresses, dimming,
     advanced cover controls, climate zones, and audio zones.

  2. In-Place Registry Migration (--migrate-registry):
     Safely creates a timestamped backup of core.entity_registry and updates
     orphaned SDomotica entries in-place to platform 'myhome' with native
     unique_ids. Eliminates entity_id renaming (_2 suffix), preserves
     historical statistics, and retains all room/area assignments.

Legal Notice:
  This script is an independent data conversion and interoperability utility
  developed under AGPL-3.0. It parses user configuration files and Home
  Assistant registry databases under EU Directive 2009/24/EC Art. 6, GDPR
  Art. 20 (data portability), and US Copyright Act Fair Use / 17 U.S.C. 1201(f).
  It contains no proprietary software, code, or binaries from SDomotica or Legrand.
"""
from __future__ import annotations

import argparse
import json
import logging
import re
import shutil
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

try:
    import yaml
except ImportError:
    yaml = None

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
_LOGGER = logging.getLogger("migrate_sdomotica")

SDOMOTICA_ENTITY_RE = re.compile(
    r"^(?P<domain>light|cover|switch|sensor|binary_sensor|climate|media_player|alarm_control_panel|lock)\.sdomotica(?:bticino2_|bticino20212_|bticino_?|btalarm_?|alarm_?)?(?P<address>[0-9a-zA-Z_#]*)$",
    re.IGNORECASE,
)
AUDIO_ZONE_ENTITY_RE = re.compile(
    r"^(?P<domain>media_player)\.(?:audio_zone_|bticino_sound_ampli_|bticino_sound_?)(?P<zone>[0-9]+)?$",
    re.IGNORECASE,
)
CLIMATE_ZONE_ENTITY_RE = re.compile(
    r"^(?P<domain>climate)\.(?:sdomotica(?:bticino2?)?_?4_|sdomotica_climate_|thermostat_)(?P<zone>[0-9]+)$",
    re.IGNORECASE,
)


class SDomoticaEntity:
    """Represents a discovered SDomotica entity to be migrated."""

    def __init__(
        self,
        domain: str,
        entity_id: str,
        address: str,
        original_unique_id: str = "",
        name: str | None = None,
        area_id: str | None = None,
        icon: str | None = None,
        dimmable: bool = False,
        advanced_shutter: bool = False,
        travel_time: int = 25,
        zone: str | None = None,
        heat: bool = True,
        cool: bool = False,
        inverted: bool = False,
        device_class: str | None = None,
        raw_entry: dict[str, Any] | None = None,
    ) -> None:
        self.domain = domain.lower()
        self.entity_id = entity_id
        self.address = str(address).strip()
        self.original_unique_id = original_unique_id
        self.name = name or entity_id.split(".")[-1]
        self.area_id = area_id
        self.icon = icon
        self.dimmable = dimmable
        self.advanced_shutter = advanced_shutter
        self.travel_time = travel_time
        self.zone = str(zone).strip() if zone is not None else None
        self.heat = heat
        self.cool = cool
        self.inverted = inverted
        self.device_class = device_class
        self.raw_entry = raw_entry or {}

        self.where: str = self.address
        self.interface: str | None = None

        raw_addr = self.address
        if "#4#" in raw_addr:
            parts = raw_addr.split("#4#", 1)
            self.where = parts[0]
            self.interface = parts[1]
        elif "_4_" in raw_addr:
            parts = raw_addr.split("_4_", 1)
            self.where = parts[0]
            self.interface = parts[1]
        elif "#" in raw_addr:
            parts = raw_addr.split("#", 1)
            self.where = parts[0]
            self.interface = parts[1] if len(parts) > 1 and parts[1] else None
        elif raw_addr.count("_") == 2:
            parts = raw_addr.split("_")
            if parts[1] == "4":
                self.where = parts[0]
                self.interface = parts[2]

        if self.interface is not None and len(self.interface) == 1 and self.interface.isdigit():
            self.interface = f"0{self.interface}"

        if self.domain in ["light", "switch"]:
            self.who = "1"
        elif self.domain == "cover":
            self.who = "2"
        elif self.domain == "climate":
            self.who = "4"
            if self.address.startswith("4_") or self.address.startswith("4#"):
                self.zone = self.address[2:]
            elif not self.zone:
                self.zone = self.where
        elif self.domain == "media_player":
            self.who = "16"
            if not self.zone:
                self.zone = self.where[0] if len(self.where) >= 1 else "1"
        elif self.domain == "binary_sensor":
            self.who = "25"
        elif self.domain == "sensor":
            self.who = "18" if self.device_class in ["power", "energy"] else "4"
        elif self.domain == "alarm_control_panel":
            self.who = "5"
        else:
            self.who = "1"

    @property
    def object_id(self) -> str:
        return self.entity_id.split(".", 1)[-1]

    def compute_myhome_unique_id(self, gateway_mac: str) -> str:
        mac = gateway_mac.lower().replace("-", ":")
        if self.domain == "media_player":
            clean_dev_id = self.zone or self.address
        elif self.domain == "climate":
            clean_dev_id = self.zone or self.where or self.address
        elif self.interface:
            clean_dev_id = f"{self.where}#4#{self.interface}"
        else:
            clean_dev_id = self.where
        return f"{mac}-{self.who}-{clean_dev_id}"

    def update_capabilities(self, other: SDomoticaEntity) -> None:
        if other.dimmable:
            self.dimmable = True
        if other.advanced_shutter:
            self.advanced_shutter = True
        if other.travel_time != 25:
            self.travel_time = other.travel_time
        if other.zone:
            self.zone = other.zone
        if other.cool:
            self.cool = True
        if other.inverted:
            self.inverted = True
        if other.device_class:
            self.device_class = other.device_class
        if other.interface and not self.interface:
            self.interface = other.interface
        if other.name and self.name == self.object_id:
            self.name = other.name


def find_entity_registry(config_dir: str | Path) -> Path | None:
    candidates = [
        Path(config_dir) / ".storage" / "core.entity_registry",
        Path(config_dir) / "core.entity_registry",
        Path(config_dir) / "storage" / "core.entity_registry",
    ]
    for c in candidates:
        if c.is_file():
            return c
    return None


def find_sdomotica_files(config_dir: str | Path) -> dict[str, Any]:
    found: dict[str, Any] = {"yamls": [], "yaml": None, "json": None}
    base = Path(config_dir)

    yaml_candidates = [
        base / "packages" / "sdomoticabticino.yaml",
        base / "packages" / "sdomoticabticino2.yaml",
        base / "packages" / "sdomoticabticino20212.yaml",
        base / "packages" / "sdomoticabtalarm.yaml",
        base / "packages" / "sdomoticabticinoalarm.yaml",
        base / "packages" / "sdomotica.yaml",
        base / "packages" / "myhome.yaml",
        base / "sdomoticabticino.yaml",
        base / "sdomoticabticino2.yaml",
        base / "sdomoticabtalarm.yaml",
        base / "sdomotica.yaml",
    ]
    seen_yamls: set[Path] = set()
    for y in yaml_candidates:
        if y.is_file() and y.resolve() not in seen_yamls:
            found["yamls"].append(y)
            seen_yamls.add(y.resolve())
            if found["yaml"] is None:
                found["yaml"] = y

    pkg_dir = base / "packages"
    if pkg_dir.is_dir():
        for p in sorted(pkg_dir.glob("*sdomotica*.yaml")):
            if p.resolve() not in seen_yamls:
                found["yamls"].append(p)
                seen_yamls.add(p.resolve())
                if found["yaml"] is None:
                    found["yaml"] = p

    json_candidates = [
        base / "config.json",
        base / "sdomotica_config.json",
        base / "sdomotica.json",
        base / "sdomotica" / "config.json",
        base / "share" / "sdomotica" / "config.json",
        base / "share" / "sdomoticabticino" / "config.json",
        base.parent / "share" / "sdomotica" / "config.json",
        base.parent / "share" / "sdomoticabticino" / "config.json",
        base.parent / "share" / "sdomoticabticino2021" / "config.json",
        base.parent / "share" / "sdomoticabticino20212" / "config.json",
    ]
    for j in json_candidates:
        if j.is_file():
            found["json"] = j
            break

    return found


def find_gateway_mac_from_config(config_dir: str | Path) -> str | None:
    entries_file = Path(config_dir) / ".storage" / "core.config_entries"
    if not entries_file.is_file():
        return None
    try:
        with open(entries_file, "r", encoding="utf-8") as f:
            data = json.load(f)
        for entry in data.get("data", {}).get("entries", []):
            if entry.get("domain") == "myhome":
                mac = entry.get("data", {}).get("mac") or entry.get("unique_id")
                if mac:
                    return str(mac)
    except Exception as err:
        _LOGGER.debug("Could not read config entries for gateway MAC: %s", err)
    return None


def extract_sdomotica_entities(registry_data: dict[str, Any]) -> list[SDomoticaEntity]:
    entities: list[SDomoticaEntity] = []
    data = registry_data.get("data", {})
    raw_entities = data.get("entities", [])

    for item in raw_entities:
        entity_id = item.get("entity_id", "")
        unique_id = str(item.get("unique_id", ""))
        platform = str(item.get("platform", "")).lower()

        climate_match = CLIMATE_ZONE_ENTITY_RE.match(entity_id)
        audio_match = AUDIO_ZONE_ENTITY_RE.match(entity_id)
        sdomotica_match = SDOMOTICA_ENTITY_RE.match(entity_id)

        zone: str | None = None
        dev_class: str | None = None
        if climate_match:
            domain = climate_match.group("domain")
            address = climate_match.group("zone")
            zone = address
        elif audio_match:
            domain = audio_match.group("domain")
            addr_str = audio_match.group("zone") or "1"
            address = addr_str
            zone = addr_str[0] if len(addr_str) >= 1 else "1"
        elif sdomotica_match:
            domain = sdomotica_match.group("domain")
            address = sdomotica_match.group("address") or ""
            if domain == "climate" and (address.startswith("4_") or address.startswith("4#")):
                zone = address[2:]
                address = zone
            elif domain == "alarm_control_panel" and (not address or address in ["sdomoticabtalarm", "sdomoticaalarm"]):
                address = "0"
            elif domain == "binary_sensor" and "zone_" in address:
                address = address.split("zone_", 1)[-1]
                dev_class = "safety"
            elif not address:
                address = "0" if domain == "alarm_control_panel" else "1"
        elif "sdomotica" in unique_id.lower() or platform in ["sdomotica", "myhomeaudio", "sdomoticabticino", "sdomoticabtalarm"]:
            domain = entity_id.split(".", 1)[0]
            digits = "".join(filter(str.isdigit, entity_id.split(".")[-1]))
            address = digits or ("0" if domain == "alarm_control_panel" else "1")
            if domain == "media_player":
                zone = address[0] if len(address) >= 1 else "1"
            elif domain == "climate":
                zone = address
        else:
            continue

        name = item.get("name") or item.get("original_name") or entity_id.split(".")[-1]
        area_id = item.get("area_id")
        icon = item.get("icon") or item.get("original_icon")

        entities.append(
            SDomoticaEntity(
                domain=domain,
                entity_id=entity_id,
                address=address,
                original_unique_id=unique_id,
                name=name,
                area_id=area_id,
                icon=icon,
                zone=zone,
                device_class=dev_class,
                raw_entry=item,
            )
        )

    return entities


def extract_sdomotica_config_json(
    config_data: dict[str, Any],
) -> tuple[list[SDomoticaEntity], dict[str, Any]]:
    entities: list[SDomoticaEntity] = []
    gw_info: dict[str, Any] = {
        "host": "192.168.1.35",
        "port": 20000,
        "password": "12345",
        "sources": {},
    }

    platforms = config_data.get("platforms", [])
    accessories: list[dict[str, Any]] = []

    for plat in platforms:
        if plat.get("platform") == "MyHome2":
            if "host" in plat:
                gw_info["host"] = plat["host"]
            if "password" in plat:
                gw_info["password"] = plat["password"]
            for s_idx in range(1, 5):
                s_key = f"source{s_idx}"
                if s_key in plat:
                    gw_info["sources"][s_idx] = plat[s_key]
            accessories.extend(plat.get("accessories", []))

    accessories.extend(config_data.get("accessories", []))

    for acc in accessories:
        acc_type = str(acc.get("type", "")).strip()
        name = str(acc.get("name", "")).strip() or "Device"
        addr = str(acc.get("address", "")).strip()

        safe_addr = addr.replace("#", "_")
        slug_id = re.sub(r"[^a-zA-Z0-9_]", "_", f"sdomoticabticino_{safe_addr}").lower()

        if acc_type == "Lightbulb":
            can_dim = bool(acc.get("can_dim", False))
            entities.append(
                SDomoticaEntity(
                    domain="light",
                    entity_id=f"light.{slug_id}",
                    address=addr,
                    name=name,
                    dimmable=can_dim,
                )
            )
        elif acc_type in ["Outlets", "Switch", "Button"]:
            dev_class = "outlet" if acc_type == "Outlets" else "switch"
            entities.append(
                SDomoticaEntity(
                    domain="switch",
                    entity_id=f"switch.{slug_id}",
                    address=addr,
                    name=name,
                    device_class=dev_class,
                )
            )
        elif acc_type in ["Windows", "WindowsAdvance"]:
            advanced = (acc_type == "WindowsAdvance")
            t_time = int(acc.get("time", 25))
            entities.append(
                SDomoticaEntity(
                    domain="cover",
                    entity_id=f"cover.{slug_id}",
                    address=addr,
                    name=name,
                    advanced_shutter=advanced,
                    travel_time=t_time,
                )
            )
        elif acc_type in ["Sensor", "Sensor3477", "Sensor3477inv"]:
            inv = (acc_type == "Sensor3477inv")
            entities.append(
                SDomoticaEntity(
                    domain="binary_sensor",
                    entity_id=f"binary_sensor.{slug_id}",
                    address=addr,
                    name=name,
                    inverted=inv,
                )
            )
        elif acc_type in ["Energy", "F522", "F523"]:
            d_class = "energy" if acc_type == "Energy" else "power"
            entities.append(
                SDomoticaEntity(
                    domain="sensor",
                    entity_id=f"sensor.{slug_id}",
                    address=addr,
                    name=name,
                    device_class=d_class,
                )
            )
        elif acc_type in ["Thermostat", "4ZThermo", "SAThermoHC", "SAThermoC", "SAThermoH"]:
            heat = acc_type in ["Thermostat", "4ZThermo", "SAThermoHC", "SAThermoH"]
            cool = acc_type in ["4ZThermo", "SAThermoHC", "SAThermoC"]
            entities.append(
                SDomoticaEntity(
                    domain="climate",
                    entity_id=f"climate.sdomoticabticino_4_{addr}",
                    address=addr,
                    zone=addr,
                    name=name,
                    heat=heat,
                    cool=cool,
                )
            )
        elif acc_type in ["TemperatureSensors", "TemperatureSensorsInternal"]:
            entities.append(
                SDomoticaEntity(
                    domain="sensor",
                    entity_id=f"sensor.{slug_id}",
                    address=addr,
                    name=name,
                    device_class="temperature",
                )
            )
        elif acc_type == "Audio":
            zone_id = addr[0] if len(addr) >= 1 else "1"
            entities.append(
                SDomoticaEntity(
                    domain="media_player",
                    entity_id=f"media_player.audio_zone_{zone_id}",
                    address=addr,
                    zone=zone_id,
                    name=name,
                )
            )
        elif acc_type == "SecuritySystem":
            sec_zone = str(acc.get("zone", "8"))
            entities.append(
                SDomoticaEntity(
                    domain="alarm_control_panel",
                    entity_id="alarm_control_panel.sdomoticabticino_alarm",
                    address=sec_zone,
                    name=name,
                )
            )
        elif acc_type == "Door":
            entities.append(
                SDomoticaEntity(
                    domain="switch",
                    entity_id=f"switch.{slug_id}",
                    address=addr,
                    name=name,
                )
            )

    return entities, gw_info


def extract_sdomotica_package_yaml(yaml_content: str | dict[str, Any]) -> list[SDomoticaEntity]:
    if yaml is None:
        _LOGGER.warning("PyYAML not available. Skipping YAML package parsing.")
        return []

    if isinstance(yaml_content, str):
        parsed = yaml.safe_load(yaml_content) or {}
    else:
        parsed = yaml_content

    entities: list[SDomoticaEntity] = []

    for item in parsed.get("light", []):
        if not isinstance(item, dict):
            continue
        name = item.get("name", "Light")
        cmd_topic = item.get("command_topic", "")
        dimmable = "brightness_command_topic" in item or "brightness_scale" in item
        addr_match = re.search(r"sdomotica/light/([^/]+)/set", cmd_topic)
        addr = addr_match.group(1) if addr_match else "".join(filter(str.isdigit, name))
        slug_id = re.sub(r"[^a-zA-Z0-9_]", "_", f"sdomoticabticino_{addr}").lower()
        entities.append(
            SDomoticaEntity(
                domain="light",
                entity_id=f"light.{slug_id}",
                address=addr,
                name=name,
                dimmable=dimmable,
            )
        )

    for item in parsed.get("cover", []):
        if not isinstance(item, dict):
            continue
        name = item.get("name", "Cover")
        cmd_topic = item.get("command_topic", "")
        advanced = "position_topic" in item or "set_position_topic" in item
        addr_match = re.search(r"sdomotica/cover/([^/]+)/set", cmd_topic)
        addr = addr_match.group(1) if addr_match else "".join(filter(str.isdigit, name))
        slug_id = re.sub(r"[^a-zA-Z0-9_]", "_", f"sdomoticabticino_{addr}").lower()
        entities.append(
            SDomoticaEntity(
                domain="cover",
                entity_id=f"cover.{slug_id}",
                address=addr,
                name=name,
                advanced_shutter=advanced,
            )
        )

    for item in parsed.get("switch", []):
        if not isinstance(item, dict):
            continue
        name = item.get("name", "Switch")
        cmd_topic = item.get("command_topic", "")
        addr_match = re.search(r"sdomotica/switch/([^/]+)/set", cmd_topic)
        addr = addr_match.group(1) if addr_match else "".join(filter(str.isdigit, name))
        slug_id = re.sub(r"[^a-zA-Z0-9_]", "_", f"sdomoticabticino_{addr}").lower()
        entities.append(
            SDomoticaEntity(
                domain="switch",
                entity_id=f"switch.{slug_id}",
                address=addr,
                name=name,
            )
        )

    for item in parsed.get("climate", []):
        if not isinstance(item, dict):
            continue
        name = item.get("name", "Thermostat")
        state_topic = item.get("current_temperature_topic", "")
        zone_match = re.search(r"sdomotica/4/([^/]+)/", state_topic)
        zone = zone_match.group(1) if zone_match else "1"
        entities.append(
            SDomoticaEntity(
                domain="climate",
                entity_id=f"climate.sdomoticabticino_4_{zone}",
                address=zone,
                zone=zone,
                name=name,
            )
        )

    for item in parsed.get("media_player", []):
        if not isinstance(item, dict):
            continue
        platform = item.get("platform", "")
        if "MyHomeAudio" in platform or "myhome" in platform.lower():
            name = item.get("name", "Audio Zone")
            addr = str(item.get("address", "11"))
            zone_id = addr[0] if len(addr) >= 1 else "1"
            entities.append(
                SDomoticaEntity(
                    domain="media_player",
                    entity_id=f"media_player.audio_zone_{zone_id}",
                    address=addr,
                    zone=zone_id,
                    name=name,
                )
            )

    for item in parsed.get("sensor", []):
        if not isinstance(item, dict):
            continue
        name = item.get("name", "Sensor")
        state_topic = item.get("state_topic", "")
        uom = item.get("unit_of_measurement", "")
        dev_class = item.get("device_class")
        if not dev_class:
            if uom in ["W", "kW"] or "power" in state_topic:
                dev_class = "power"
            elif uom in ["Wh", "kWh"] or "energy" in state_topic:
                dev_class = "energy"
            elif uom in ["°C", "C", "°F", "F"] or "temp" in state_topic:
                dev_class = "temperature"
        addr_match = re.search(r"sdomotica/(?:energy|power|sensor|temperature)/([^/]+)", state_topic)
        addr = addr_match.group(1) if addr_match else "".join(filter(str.isdigit, name)) or "1"
        slug_id = re.sub(r"[^a-zA-Z0-9_]", "_", f"sdomoticabticino_{addr}").lower()
        entities.append(
            SDomoticaEntity(
                domain="sensor",
                entity_id=f"sensor.{slug_id}",
                address=addr,
                name=name,
                device_class=dev_class,
            )
        )

    for item in parsed.get("binary_sensor", []):
        if not isinstance(item, dict):
            continue
        name = item.get("name", "Contact")
        state_topic = item.get("state_topic", "")
        dev_class = item.get("device_class", "opening")
        inverted = item.get("payload_on") == "0" or "inv" in name.lower()
        addr_match = re.search(r"sdomotica/(?:sensor|contact|binary_sensor|alarm)/([^/]+)", state_topic)
        addr = addr_match.group(1) if addr_match else "".join(filter(str.isdigit, name)) or "1"
        slug_id = re.sub(r"[^a-zA-Z0-9_]", "_", f"sdomoticabticino_{addr}").lower()
        entities.append(
            SDomoticaEntity(
                domain="binary_sensor",
                entity_id=f"binary_sensor.{slug_id}",
                address=addr,
                name=name,
                device_class=dev_class,
                inverted=inverted,
            )
        )

    for item in parsed.get("alarm_control_panel", []):
        if not isinstance(item, dict):
            continue
        name = item.get("name", "Burglar Alarm")
        cmd_topic = item.get("command_topic", "")
        addr_match = re.search(r"sdomotica/(?:alarm|5)/([^/]+)", cmd_topic)
        addr = addr_match.group(1) if addr_match else "0"
        entities.append(
            SDomoticaEntity(
                domain="alarm_control_panel",
                entity_id="alarm_control_panel.sdomoticabtalarm",
                address=addr,
                name=name,
            )
        )

    return entities


def merge_entity_sources(
    registry_entities: list[SDomoticaEntity],
    extra_entities: list[SDomoticaEntity],
) -> list[SDomoticaEntity]:
    extra_map: dict[str, SDomoticaEntity] = {}
    for ext in extra_entities:
        key = f"{ext.domain}:{ext.zone or ext.where}"
        extra_map[key] = ext
        extra_map[f"{ext.domain}:{ext.address}"] = ext
        extra_map[ext.entity_id] = ext

    merged: list[SDomoticaEntity] = []
    seen_keys: set[str] = set()

    for reg in registry_entities:
        match_keys = [
            f"{reg.domain}:{reg.zone or reg.where}",
            f"{reg.domain}:{reg.address}",
            reg.entity_id,
        ]
        for mk in match_keys:
            if mk in extra_map:
                reg.update_capabilities(extra_map[mk])
                break
        merged.append(reg)
        seen_keys.add(f"{reg.domain}:{reg.zone or reg.where}")

    for ext in extra_entities:
        key = f"{ext.domain}:{ext.zone or ext.where}"
        if key not in seen_keys:
            merged.append(ext)
            seen_keys.add(key)

    return merged


def generate_myhome_yaml(
    entities: list[SDomoticaEntity],
    gateway_mac: str,
    gateway_host: str = "192.168.1.50",
    gateway_port: int = 20000,
) -> str:
    lines: list[str] = [
        "# ═════════════════════════════════════════════════════════════════",
        "# MyHOME Configuration Generated from SDomotica Migration Tool",
        f"# Generated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
        "# Preserves exact entity IDs for zero-touch Lovelace & automation compatibility",
        f"# Gateway Host: {gateway_host}:{gateway_port}",
        "# ═════════════════════════════════════════════════════════════════",
        "",
        "myhome:",
        f"  mac: \"{gateway_mac}\"",
        "",
    ]

    by_domain: dict[str, list[SDomoticaEntity]] = {}
    for ent in entities:
        by_domain.setdefault(ent.domain, []).append(ent)

    domain_order = [
        "light",
        "cover",
        "switch",
        "climate",
        "binary_sensor",
        "sensor",
        "media_player",
        "alarm_control_panel",
    ]

    for domain in domain_order:
        domain_entities = by_domain.get(domain, [])
        if not domain_entities:
            continue

        if domain == "media_player":
            lines.append(f"  # ── {domain.upper()} ({len(domain_entities)} devices - auto-discovered dynamically by MyHOME) ──────")
            lines.append("  # media_player:")
            for ent in domain_entities:
                lines.append(f"  #   {ent.object_id}:")
                lines.append(f"  #     zone: \"{ent.zone or ent.address}\"")
                lines.append(f"  #     name: \"{ent.name}\"")
            lines.append("")
            continue

        lines.append(f"  # ── {domain.upper()} ({len(domain_entities)} devices) ──────────────────────")
        lines.append(f"  {domain}:")
        for ent in domain_entities:
            lines.append(f"    {ent.object_id}:")
            if domain == "climate":
                lines.append(f"      zone: \"{ent.zone or ent.where}\"")
                lines.append(f"      heat: {str(ent.heat).lower()}")
                lines.append(f"      cool: {str(ent.cool).lower()}")
            elif domain == "alarm_control_panel":
                lines.append(f"      where: \"{ent.where or '0'}\"")
            else:
                lines.append(f"      where: \"{ent.where}\"")
                if ent.interface:
                    lines.append(f"      interface: \"{ent.interface}\"")

            lines.append(f"      name: \"{ent.name}\"")

            if ent.dimmable and domain == "light":
                lines.append("      dimmable: true")
            if domain == "cover":
                lines.append(f"      advanced_shutter: {str(ent.advanced_shutter).lower()}")
                if ent.travel_time != 25:
                    lines.append(f"      travel_time: {ent.travel_time}")
            if domain == "switch" and ent.device_class:
                lines.append(f"      device_class: \"{ent.device_class}\"")
            if domain == "binary_sensor" and ent.inverted:
                lines.append("      inverted: true")
            if domain == "sensor" and ent.device_class:
                lines.append(f"      class: \"{ent.device_class}\"")
            if ent.area_id:
                lines.append(f"      # area: {ent.area_id}")
            if ent.icon:
                lines.append(f"      icon: \"{ent.icon}\"")

        lines.append("")

    return "\n".join(lines)


def migrate_registry_in_place(
    registry_path: Path,
    entities: list[SDomoticaEntity],
    gateway_mac: str,
    dry_run: bool = False,
) -> tuple[int, Path | None]:
    with open(registry_path, "r", encoding="utf-8") as f:
        data = json.load(f)

    raw_entities = data.get("data", {}).get("entities", [])
    entity_map = {e.entity_id: e for e in entities}
    updated_count = 0

    for item in raw_entities:
        eid = item.get("entity_id")
        if eid in entity_map:
            ent = entity_map[eid]
            new_unique_id = ent.compute_myhome_unique_id(gateway_mac)
            item["platform"] = "myhome"
            item["unique_id"] = new_unique_id
            updated_count += 1

    if dry_run or updated_count == 0:
        return updated_count, None

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_path = registry_path.parent / f"{registry_path.name}.backup_sdomotica_{timestamp}"
    shutil.copy2(registry_path, backup_path)
    _LOGGER.info("Created safety backup of registry at: %s", backup_path)

    temp_path = registry_path.parent / f"{registry_path.name}.tmp_{timestamp}"
    with open(temp_path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
    shutil.move(str(temp_path), str(registry_path))

    return updated_count, backup_path


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="SDomotica to MyHOME Frictionless Migration Tool",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--config-dir",
        default=".",
        help="Home Assistant configuration directory containing .storage (default: current directory)",
    )
    parser.add_argument(
        "--sdomotica-json",
        default=None,
        help="Path to Sdomotica Gateway config.json (Homebridge format, manual p. 18-23)",
    )
    parser.add_argument(
        "--sdomotica-yaml",
        default=None,
        help="Path to downloaded Sdomotica package YAML (packages/sdomoticabticino.yaml, manual p. 24)",
    )
    parser.add_argument(
        "--gateway-mac",
        default=None,
        help="MAC address of the MyHOME gateway (e.g. 00:03:50:20:00:01)",
    )
    parser.add_argument(
        "--gateway-host",
        default=None,
        help="IP address of the MyHOME gateway (default: 192.168.1.50 or auto-detected from config.json)",
    )
    parser.add_argument(
        "--generate-yaml",
        metavar="OUTPUT_FILE",
        default=None,
        help="Generate a myhome.yaml configuration file and save to the specified path",
    )
    parser.add_argument(
        "--migrate-registry",
        action="store_true",
        help="Perform safe in-place migration of core.entity_registry (HA should be stopped)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Preview changes without modifying any files",
    )

    args = parser.parse_args(argv)

    config_path = Path(args.config_dir).resolve()
    registry_path = find_entity_registry(config_path)

    extra_entities: list[SDomoticaEntity] = []
    gw_host = args.gateway_host or "192.168.1.50"

    json_path = (
        Path(args.sdomotica_json).resolve()
        if args.sdomotica_json
        else find_sdomotica_files(config_path).get("json")
    )
    if json_path and json_path.is_file():
        _LOGGER.info("Loading Sdomotica config.json from: %s", json_path)
        try:
            with open(json_path, "r", encoding="utf-8") as f:
                json_data = json.load(f)
            json_ents, gw_info = extract_sdomotica_config_json(json_data)
            _LOGGER.info("Extracted %d devices from config.json", len(json_ents))
            extra_entities.extend(json_ents)
            if not args.gateway_host and gw_info.get("host"):
                gw_host = gw_info["host"]
        except Exception as err:
            _LOGGER.error("Failed to parse config.json: %s", err)

    yaml_paths: list[Path] = []
    if args.sdomotica_yaml:
        yaml_paths.append(Path(args.sdomotica_yaml).resolve())
    else:
        yaml_paths.extend(find_sdomotica_files(config_path).get("yamls", []))

    for y_path in yaml_paths:
        if y_path.is_file() and yaml is not None:
            _LOGGER.info("Loading Sdomotica package YAML from: %s", y_path)
            try:
                with open(y_path, "r", encoding="utf-8") as f:
                    yaml_text = f.read()
                yaml_ents = extract_sdomotica_package_yaml(yaml_text)
                _LOGGER.info("Extracted %d devices from %s", len(yaml_ents), y_path.name)
                extra_entities.extend(yaml_ents)
            except Exception as err:
                _LOGGER.error("Failed to parse package YAML %s: %s", y_path, err)

    registry_entities: list[SDomoticaEntity] = []
    if registry_path and registry_path.is_file():
        _LOGGER.info("Found Home Assistant entity registry at: %s", registry_path)
        try:
            with open(registry_path, "r", encoding="utf-8") as f:
                registry_data = json.load(f)
            registry_entities = extract_sdomotica_entities(registry_data)
            _LOGGER.info("Identified %d SDomotica entities in entity registry.", len(registry_entities))
        except Exception as err:
            _LOGGER.error("Failed to parse entity registry: %s", err)

    if registry_entities and extra_entities:
        entities = merge_entity_sources(registry_entities, extra_entities)
    elif registry_entities:
        entities = registry_entities
    elif extra_entities:
        entities = extra_entities
    else:
        _LOGGER.error(
            "No SDomotica configuration or entity registry found in '%s'. "
            "Please specify --config-dir, --sdomotica-json, or --sdomotica-yaml.",
            config_path,
        )
        return 1

    if not entities:
        _LOGGER.warning("No SDomotica entities identified. Nothing to migrate.")
        return 0

    by_domain: dict[str, int] = {}
    for e in entities:
        by_domain[e.domain] = by_domain.get(e.domain, 0) + 1

    print("\n--- Discovered SDomotica Entities ---")
    for domain, count in sorted(by_domain.items()):
        print(f"  • {domain:<20}: {count:>3} devices")
    print("-------------------------------------\n")

    gateway_mac = (
        args.gateway_mac
        or find_gateway_mac_from_config(config_path)
        or "00:03:50:20:00:01"
    )
    _LOGGER.info("Target MyHOME Gateway MAC: %s", gateway_mac)
    _LOGGER.info("Target MyHOME Gateway Host: %s", gw_host)

    if args.generate_yaml:
        yaml_content = generate_myhome_yaml(
            entities,
            gateway_mac=gateway_mac,
            gateway_host=gw_host,
        )
        out_file = Path(args.generate_yaml)
        if args.dry_run:
            _LOGGER.info(
                "[DRY RUN] Generated YAML content would be written to %s (%d bytes)",
                out_file,
                len(yaml_content),
            )
            print("\n" + yaml_content[:1000] + "\n... [truncated] ...\n")
        else:
            out_file.write_text(yaml_content, encoding="utf-8")
            _LOGGER.info("Successfully generated MyHOME configuration at: %s", out_file.resolve())

    if args.migrate_registry:
        if not registry_path:
            _LOGGER.error("Cannot perform --migrate-registry: core.entity_registry not found!")
            return 1

        if args.dry_run:
            _LOGGER.info(
                "[DRY RUN] Would migrate %d entities in %s to platform 'myhome'.",
                len(entities),
                registry_path,
            )
        else:
            updated, backup = migrate_registry_in_place(registry_path, entities, gateway_mac)
            _LOGGER.info(
                "Successfully migrated %d entities in %s to platform 'myhome'!",
                updated,
                registry_path,
            )
            if backup:
                _LOGGER.info("Registry backup saved to: %s", backup)

    if not args.generate_yaml and not args.migrate_registry and not args.dry_run:
        print("To proceed with migration, use one of:")
        print("  --generate-yaml myhome.yaml      (Outputs pre-mapped configuration)")
        print("  --migrate-registry               (In-place seamless registry adoption)")
        print("  --dry-run                        (Simulate migration actions)")

    return 0


if __name__ == "__main__":
    sys.exit(main())
