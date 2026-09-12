"""Unit tests for the SDomotica to MyHOME migration tool."""
import json

import pytest
import yaml

from custom_components.myhome.validate import config_schema
from scripts.migrate_from_sdomotica import (
    extract_sdomotica_config_json,
    extract_sdomotica_entities,
    extract_sdomotica_package_yaml,
    generate_myhome_yaml,
    main,
    merge_entity_sources,
    migrate_registry_in_place,
)


@pytest.fixture
def mock_registry_data():
    """Return a mock core.entity_registry dictionary."""
    return {
        "version": 1,
        "minor_version": 1,
        "key": "core.entity_registry",
        "data": {
            "entities": [
                {
                    "area_id": "keuken",
                    "config_entry_id": "sdomotica_entry",
                    "device_id": "dev_1",
                    "entity_id": "light.sdomoticabticino2",
                    "name": "Keuken Spots",
                    "original_name": "sdomoticabticino2",
                    "platform": "mqtt",
                    "unique_id": "sdomotica_light_2",
                },
                {
                    "area_id": "keuken",
                    "config_entry_id": "sdomotica_entry",
                    "device_id": "dev_2",
                    "entity_id": "cover.sdomoticabticino18_4_02",
                    "name": "Gordijn Keuken Terras",
                    "original_name": "18#4#02",
                    "platform": "mqtt",
                    "unique_id": "sdomotica_cover_18_4_02",
                },
                {
                    "area_id": "cv_ruimte",
                    "config_entry_id": "sdomotica_entry",
                    "device_id": "dev_3",
                    "entity_id": "switch.sdomoticabticino62",
                    "name": "Pomp Schakelaar",
                    "original_name": "sdomoticabticino62",
                    "platform": "mqtt",
                    "unique_id": "sdomotica_switch_62",
                },
                {
                    "area_id": "keuken",
                    "config_entry_id": "sdomotica_entry",
                    "device_id": "dev_4",
                    "entity_id": "media_player.audio_zone_2",
                    "name": "Keuken Audio",
                    "original_name": "audio_zone_2",
                    "platform": "mqtt",
                    "unique_id": "sdomotica_zone_2",
                },
                {
                    "area_id": "woonkamer",
                    "config_entry_id": "sdomotica_entry",
                    "device_id": "dev_5",
                    "entity_id": "climate.sdomoticabticino_4_1",
                    "name": "Woonkamer Thermostaat",
                    "original_name": "sdomoticabticino_4_1",
                    "platform": "mqtt",
                    "unique_id": "sdomotica_climate_1",
                },
                {
                    "area_id": "boven",
                    "config_entry_id": "sdomotica_entry",
                    "device_id": "dev_7",
                    "entity_id": "light.sdomoticabticino2_12",
                    "name": "Slaapkamer Spots",
                    "original_name": "sdomoticabticino2_12",
                    "platform": "mqtt",
                    "unique_id": "sdomotica_light_2_12",
                },
                {
                    "area_id": "hal",
                    "config_entry_id": "sdomotica_entry",
                    "device_id": "dev_8",
                    "entity_id": "alarm_control_panel.sdomoticabtalarm",
                    "name": "Centrale Antifurto",
                    "original_name": "sdomoticabtalarm",
                    "platform": "mqtt",
                    "unique_id": "sdomotica_alarm_main",
                },
                {
                    "area_id": "hal",
                    "config_entry_id": "sdomotica_entry",
                    "device_id": "dev_9",
                    "entity_id": "binary_sensor.sdomoticabtalarm_zone_1",
                    "name": "Zone 1 Hal Contact",
                    "original_name": "zone_1",
                    "platform": "mqtt",
                    "unique_id": "sdomotica_alarm_zone_1",
                },
                {
                    "area_id": "badkamer",
                    "config_entry_id": "sdomotica_entry",
                    "device_id": "dev_10",
                    "entity_id": "media_player.bticino_sound_ampli_11",
                    "name": "Badkamer Audio",
                    "original_name": "bticino_sound_ampli_11",
                    "platform": "MyHomeAudio",
                    "unique_id": "myhomeaudio_zone_1",
                },
                {
                    "area_id": "woonkamer",
                    "config_entry_id": "hue_entry",
                    "device_id": "dev_6",
                    "entity_id": "light.hue_bloom",
                    "name": "Hue Bloom",
                    "original_name": "Hue Bloom",
                    "platform": "hue",
                    "unique_id": "hue_unique_123",
                },
            ]
        },
    }


@pytest.fixture
def mock_sdomotica_config_json():
    """Return a mock SDomotica config.json adhering to official manual pages 18-23."""
    return {
        "bridge": {
            "name": "Sdomotica",
            "username": "CC:13:3D:E3:CE:39",
            "port": 51828,
            "pin": "031-45-154",
        },
        "platforms": [
            {
                "platform": "MyHome2",
                "name": "MyHome2",
                "host": "192.168.1.35",
                "port": "3002",
                "password": "12345",
                "source1": "Radio",
                "source2": "Spotify",
                "accessories": [
                    {"type": "Lightbulb", "name": "Cucina", "address": "12", "can_dim": False},
                    {"type": "Lightbulb", "name": "Dimmer TV", "address": "19", "can_dim": True},
                    {"type": "Lightbulb", "name": "Palla Balcone", "address": "41#4#01"},
                    {"type": "Outlets", "name": "Presa Rack", "address": "12"},
                    {"type": "Switch", "name": "Switch 11", "address": "11"},
                    {"type": "Windows", "name": "Veranda", "address": "31", "time": 20},
                    {"type": "WindowsAdvance", "name": "Veranda Avanzata", "address": "55"},
                    {"type": "Sensor3477", "name": "Sensore Finestra", "address": "19"},
                    {"type": "Sensor3477inv", "name": "Sensore Invertito", "address": "19"},
                    {"type": "Energy", "name": "Generale", "address": "1"},
                    {"type": "Thermostat", "name": "Soggiorno", "address": "1"},
                    {"type": "SAThermoHC", "name": "Camera Singola", "address": "2"},
                    {"type": "Audio", "name": "Ampli Cucina", "address": "11"},
                    {"type": "SecuritySystem", "name": "Antifurto", "zone": "8"},
                ],
            }
        ],
    }


def test_extract_sdomotica_entities(mock_registry_data):
    """Test extracting only SDomotica, climate, and audio zone entities."""
    entities = extract_sdomotica_entities(mock_registry_data)
    assert len(entities) == 9

    by_id = {e.entity_id: e for e in entities}
    assert "light.sdomoticabticino2" in by_id
    assert "cover.sdomoticabticino18_4_02" in by_id
    assert "switch.sdomoticabticino62" in by_id
    assert "media_player.audio_zone_2" in by_id
    assert "climate.sdomoticabticino_4_1" in by_id
    assert "light.sdomoticabticino2_12" in by_id
    assert "alarm_control_panel.sdomoticabtalarm" in by_id
    assert "binary_sensor.sdomoticabtalarm_zone_1" in by_id
    assert "media_player.bticino_sound_ampli_11" in by_id
    assert "light.hue_bloom" not in by_id

    # Verify light properties
    light = by_id["light.sdomoticabticino2"]
    assert light.domain == "light"
    assert light.where == "2"
    assert light.who == "1"
    assert light.name == "Keuken Spots"
    assert light.area_id == "keuken"
    assert light.compute_myhome_unique_id("00:03:50:AA:BB:CC") == "00:03:50:aa:bb:cc-1-2"

    # Verify second gateway light (bticino20212)
    light2 = by_id["light.sdomoticabticino2_12"]
    assert light2.domain == "light"
    assert light2.where == "12"
    assert light2.who == "1"

    # Verify burglar alarm (bticinoalarm)
    alarm = by_id["alarm_control_panel.sdomoticabtalarm"]
    assert alarm.domain == "alarm_control_panel"
    assert alarm.who == "5"
    assert alarm.where == "0"
    assert alarm.compute_myhome_unique_id("00:03:50:AA:BB:CC") == "00:03:50:aa:bb:cc-5-0"

    alarm_zone = by_id["binary_sensor.sdomoticabtalarm_zone_1"]
    assert alarm_zone.domain == "binary_sensor"
    assert alarm_zone.where == "1"

    # Verify MyHomeAudio custom component
    audio_custom = by_id["media_player.bticino_sound_ampli_11"]
    assert audio_custom.domain == "media_player"
    assert audio_custom.who == "16"
    assert audio_custom.zone == "1"
    assert audio_custom.compute_myhome_unique_id("00:03:50:AA:BB:CC") == "00:03:50:aa:bb:cc-16-1"

    # Verify cover with sanitized interface (_4_)
    cover = by_id["cover.sdomoticabticino18_4_02"]
    assert cover.domain == "cover"
    assert cover.who == "2"
    assert cover.where == "18"
    assert cover.interface == "02"
    assert cover.compute_myhome_unique_id("00:03:50:AA:BB:CC") == "00:03:50:aa:bb:cc-2-18#4#02"

    # Verify climate
    climate = by_id["climate.sdomoticabticino_4_1"]
    assert climate.domain == "climate"
    assert climate.who == "4"
    assert climate.zone == "1"
    assert climate.compute_myhome_unique_id("00:03:50:AA:BB:CC") == "00:03:50:aa:bb:cc-4-1"

    # Verify audio zone
    audio = by_id["media_player.audio_zone_2"]
    assert audio.domain == "media_player"
    assert audio.who == "16"
    assert audio.zone == "2"
    assert audio.compute_myhome_unique_id("00:03:50:AA:BB:CC") == "00:03:50:aa:bb:cc-16-2"


def test_extract_sdomotica_config_json(mock_sdomotica_config_json):
    """Test extracting all 14 accessory types from SDomotica config.json."""
    entities, gw_info = extract_sdomotica_config_json(mock_sdomotica_config_json)
    assert len(entities) == 14
    assert gw_info["host"] == "192.168.1.35"
    assert gw_info["password"] == "12345"
    assert gw_info["sources"][1] == "Radio"

    by_name = {e.name: e for e in entities}

    # Dimmer
    dimmer = by_name["Dimmer TV"]
    assert dimmer.domain == "light"
    assert dimmer.where == "19"
    assert dimmer.dimmable is True

    # F422 Interface
    f422 = by_name["Palla Balcone"]
    assert f422.domain == "light"
    assert f422.where == "41"
    assert f422.interface == "01"

    # Outlets / Switch
    outlet = by_name["Presa Rack"]
    assert outlet.domain == "switch"
    assert outlet.device_class == "outlet"

    # Windows vs WindowsAdvance
    win = by_name["Veranda"]
    assert win.domain == "cover"
    assert win.advanced_shutter is False
    assert win.travel_time == 20

    win_adv = by_name["Veranda Avanzata"]
    assert win_adv.domain == "cover"
    assert win_adv.advanced_shutter is True

    # Inverted 3477
    inv_sensor = by_name["Sensore Invertito"]
    assert inv_sensor.domain == "binary_sensor"
    assert inv_sensor.inverted is True

    # SAThermoHC
    thermo = by_name["Camera Singola"]
    assert thermo.domain == "climate"
    assert thermo.heat is True
    assert thermo.cool is True


def test_extract_sdomotica_package_yaml():
    """Test parsing sdomoticabticino.yaml package format."""
    sample_yaml = """
light:
  - platform: mqtt
    name: "Cucina"
    command_topic: "sdomotica/light/12/set"
  - platform: mqtt
    name: "Dimmer TV"
    command_topic: "sdomotica/light/19/set"
    brightness_command_topic: "sdomotica/light/19/brightness"
cover:
  - platform: mqtt
    name: "Veranda Avanzata"
    command_topic: "sdomotica/cover/55/set"
    position_topic: "sdomotica/cover/55/statusposition"
climate:
  - platform: mqtt
    name: "Soggiorno"
    current_temperature_topic: "sdomotica/4/1/status"
media_player:
  - platform: MyHomeAudio
    name: "Ampli Cucina"
    address: "11"
sensor:
  - platform: mqtt
    name: "Consumo Generale"
    state_topic: "sdomotica/energy/1"
    unit_of_measurement: "W"
binary_sensor:
  - platform: mqtt
    name: "Finestra Studio"
    state_topic: "sdomotica/sensor/21"
    payload_on: "1"
    payload_off: "0"
alarm_control_panel:
  - platform: mqtt
    name: "Centrale Allarme"
    state_topic: "sdomotica/alarm/status"
    command_topic: "sdomotica/alarm/0/set"
"""
    entities = extract_sdomotica_package_yaml(sample_yaml)
    assert len(entities) == 8

    by_domain = {e.domain: e for e in entities}
    assert by_domain["light"].where in ["12", "19"]
    assert by_domain["cover"].advanced_shutter is True
    assert by_domain["climate"].zone == "1"
    assert by_domain["media_player"].zone == "1"
    assert by_domain["sensor"].where == "1"
    assert by_domain["sensor"].device_class == "power"
    assert by_domain["binary_sensor"].where == "21"
    assert by_domain["alarm_control_panel"].who == "5"


def test_merge_entity_sources(mock_registry_data, mock_sdomotica_config_json):
    """Test merging registry names and areas with config.json capabilities."""
    reg_entities = extract_sdomotica_entities(mock_registry_data)
    json_entities, _ = extract_sdomotica_config_json(mock_sdomotica_config_json)

    merged = merge_entity_sources(reg_entities, json_entities)
    assert len(merged) >= len(reg_entities)

    # Keuken Spots (where: 2) from registry should be preserved with area
    by_where = {e.where: e for e in merged if e.domain == "light"}
    assert "2" in by_where
    assert by_where["2"].area_id == "keuken"


def test_generate_myhome_yaml_and_schema_validation(mock_sdomotica_config_json):
    """Test generating myhome.yaml and validating it against MyHOME schema."""
    entities, gw_info = extract_sdomotica_config_json(mock_sdomotica_config_json)
    yaml_text = generate_myhome_yaml(
        entities,
        gateway_mac="00:03:50:11:22:33",
        gateway_host=gw_info["host"],
    )

    assert 'mac: "00:03:50:11:22:33"' in yaml_text
    assert "192.168.1.35" in yaml_text
    assert "dimmable: true" in yaml_text
    assert "advanced_shutter: true" in yaml_text
    assert "inverted: true" in yaml_text

    # Validate against MyHOME schema
    parsed_config = yaml.safe_load(yaml_text)
    validated = config_schema(parsed_config)
    assert "00:03:50:11:22:33" in validated
    platforms = validated["00:03:50:11:22:33"]["platforms"]
    assert "light" in platforms
    assert "cover" in platforms
    assert "switch" in platforms
    assert "climate" in platforms
    assert "binary_sensor" in platforms


def test_migrate_registry_in_place(tmp_path, mock_registry_data):
    """Test in-place migration with safety backup creation."""
    registry_file = tmp_path / "core.entity_registry"
    with open(registry_file, "w", encoding="utf-8") as f:
        json.dump(mock_registry_data, f, indent=2)

    entities = extract_sdomotica_entities(mock_registry_data)
    updated, backup_path = migrate_registry_in_place(
        registry_file,
        entities,
        gateway_mac="00:03:50:20:00:01",
        dry_run=False,
    )

    assert updated == 9
    assert backup_path is not None
    assert backup_path.is_file()

    # Verify modified registry
    with open(registry_file, "r", encoding="utf-8") as f:
        migrated_data = json.load(f)

    migrated_entities = migrated_data["data"]["entities"]
    by_id = {e["entity_id"]: e for e in migrated_entities}

    assert by_id["light.sdomoticabticino2"]["platform"] == "myhome"
    assert by_id["light.sdomoticabticino2"]["unique_id"] == "00:03:50:20:00:01-1-2"

    assert by_id["cover.sdomoticabticino18_4_02"]["platform"] == "myhome"
    assert by_id["cover.sdomoticabticino18_4_02"]["unique_id"] == "00:03:50:20:00:01-2-18#4#02"

    assert by_id["climate.sdomoticabticino_4_1"]["platform"] == "myhome"
    assert by_id["climate.sdomoticabticino_4_1"]["unique_id"] == "00:03:50:20:00:01-4-1"

    assert by_id["light.sdomoticabticino2_12"]["platform"] == "myhome"
    assert by_id["light.sdomoticabticino2_12"]["unique_id"] == "00:03:50:20:00:01-1-12"

    assert by_id["alarm_control_panel.sdomoticabtalarm"]["platform"] == "myhome"
    assert by_id["alarm_control_panel.sdomoticabtalarm"]["unique_id"] == "00:03:50:20:00:01-5-0"

    assert by_id["binary_sensor.sdomoticabtalarm_zone_1"]["platform"] == "myhome"
    assert by_id["binary_sensor.sdomoticabtalarm_zone_1"]["unique_id"] == "00:03:50:20:00:01-25-1"

    assert by_id["media_player.bticino_sound_ampli_11"]["platform"] == "myhome"
    assert by_id["media_player.bticino_sound_ampli_11"]["unique_id"] == "00:03:50:20:00:01-16-1"

    # Non-sdomotica entities untouched
    assert by_id["light.hue_bloom"]["platform"] == "hue"


def test_find_sdomotica_files_multi_package(tmp_path):
    """Test finding multiple packages and shared config.json paths."""
    from scripts.migrate_from_sdomotica import find_sdomotica_files

    pkg_dir = tmp_path / "packages"
    pkg_dir.mkdir()
    (pkg_dir / "sdomoticabticino.yaml").write_text("light: []", encoding="utf-8")
    (pkg_dir / "sdomoticabtalarm.yaml").write_text("alarm_control_panel: []", encoding="utf-8")

    share_dir = tmp_path / "share" / "sdomotica"
    share_dir.mkdir(parents=True)
    (share_dir / "config.json").write_text("{}", encoding="utf-8")

    found = find_sdomotica_files(tmp_path)
    assert len(found["yamls"]) == 2
    assert found["json"] == share_dir / "config.json"


def test_main_cli_modes(tmp_path, mock_registry_data, mock_sdomotica_config_json):
    """Test CLI commands with all ingestion modes."""
    storage_dir = tmp_path / ".storage"
    storage_dir.mkdir()
    reg_file = storage_dir / "core.entity_registry"
    with open(reg_file, "w", encoding="utf-8") as f:
        json.dump(mock_registry_data, f, indent=2)

    json_file = tmp_path / "config.json"
    with open(json_file, "w", encoding="utf-8") as f:
        json.dump(mock_sdomotica_config_json, f, indent=2)

    out_yaml = tmp_path / "myhome.yaml"

    # Test auto-discovery with config-dir and generate-yaml
    code = main([
        "--config-dir", str(tmp_path),
        "--generate-yaml", str(out_yaml),
        "--gateway-mac", "00:03:50:AA:BB:CC",
    ])
    assert code == 0
    assert out_yaml.is_file()

    # Test direct sdomotica-json mode
    out_yaml_json = tmp_path / "myhome_from_json.yaml"
    code = main([
        "--sdomotica-json", str(json_file),
        "--generate-yaml", str(out_yaml_json),
        "--gateway-mac", "00:03:50:AA:BB:CC",
    ])
    assert code == 0
    assert out_yaml_json.is_file()

    # Test in-place migration
    code = main([
        "--config-dir", str(tmp_path),
        "--migrate-registry",
        "--gateway-mac", "00:03:50:AA:BB:CC",
    ])
    assert code == 0
