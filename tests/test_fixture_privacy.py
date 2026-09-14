"""Plant fixtures carry no personal data, and the anonymizer keeps it that way."""
# privacy-check: allow-samples - the inputs below are what the check must catch
import json
import re
import shutil
from pathlib import Path

import pytest

from scripts.anonymize_plant_fixture import Anonymizer, anonymize, check, findings, main

PLANTS = Path(__file__).resolve().parent / "fixtures" / "plants"
PRIVATE_IP = re.compile(r"\b(10\.\d+|172\.(1[6-9]|2\d|3[01])|192\.168)\.\d+\.\d+\b")
IPV4 = re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}\b")
MAC = re.compile(r"\b(?:[0-9A-Fa-f]{2}:){5}[0-9A-Fa-f]{2}\b")


@pytest.mark.parametrize("plant", sorted(p.name for p in PLANTS.iterdir() if p.is_dir()))
def test_committed_fixture_is_synthetic(plant):
    """Every fixture: documentation-range IPs, synthetic MACs, generic names, no secrets."""
    plant_dir = PLANTS / plant
    text = (plant_dir / "myhome.yaml").read_text(encoding="utf-8")
    diag = json.loads((plant_dir / "diagnostic_summary.json").read_text(encoding="utf-8"))
    text += json.dumps(diag)

    assert not PRIVATE_IP.search(text), "a LAN address leaked into the fixture"
    assert all(ip.startswith("192.0.2.") for ip in IPV4.findall(text))
    assert all(mac.lower().startswith("00:03:50:00:") for mac in MAC.findall(text))
    assert diag["home_assistant"].get("timezone", "UTC") == "UTC"
    entry_data = diag["data"]["config_entry"]["data"]
    assert entry_data.get("password") is None
    assert diag["data"]["config_entry"]["entry_id"].startswith("01PLANT")
    assert all(u.startswith("01PLANT") for u in re.findall(r"\b01[A-Z0-9]{24}\b", text))  # also as setup_times keys
    # no local paths (a Windows path carries the user name), no list of what else the home runs
    assert not re.search(r"[A-Za-z]:\\\\|/home/|/Users/", text), "a local path leaked into the fixture"
    assert set(diag.get("custom_components", {})) <= {"myhome"}
    # every configured device is named after its address, never after a room or a person
    raw_names = re.findall(r"^\s+name: (.+)$", (plant_dir / "myhome.yaml").read_text(encoding="utf-8"), re.M)
    names = [n.strip().strip("\"'") for n in raw_names]
    assert names and all(re.fullmatch(r"[A-Z][a-z]+( [A-Z][a-z]+)? [0-9]+([I][0-9]+)?( [0-9]+)*", n) for n in names), names[:5]


def test_anonymizer_rewrites_a_contributed_plant(tmp_path):
    plant = tmp_path / "issue_999_f454"
    plant.mkdir()
    (plant / "myhome.yaml").write_text(
        "f454:\n"
        '  mac: "00:03:50:AB:CD:EF"\n'
        "  light:\n"
        "    # ── Kitchen ──\n"
        "    light_kitchen_ceiling:\n"
        '      where: "12"\n'
        '      name: "Kitchen Ceiling"\n'
        "    light_emma_room:\n"
        '      where: "0311"\n'
        '      interface: "01"\n'
        "      name: Emma's Room\n"
        "      entity_name: Emma's Room\n"
        "  sensor:\n"
        "    meter:\n"
        '      where: "51"\n'
        "      name: House Meter\n"
        "      class: power\n"
        "    meter_energy:\n"
        '      where: "51"\n'
        "      name: House Meter\n"
        "      class: energy\n"
        "  cover:\n"
        "    not_a_device: 3\n",
        encoding="utf-8",
    )
    (plant / "diagnostic_summary.json").write_text(json.dumps({
        "home_assistant": {"timezone": "Europe/Rome", "version": "2026.9.1"},
        "custom_components": {"energy_supplier": {"version": "1.0"}, "myhome": {"version": "2.0.0b12"}},
        "setup_times": {"01REALULIDFROMTHEUSERSHOME": {"setup": 0.1}},
        "data": {
            "config_entry": {
                "entry_id": "01REALULIDFROMTHEUSERSHOME",
                "data": {"host": "192.168.1.35", "mac": "00:03:50:AB:CD:EF", "id": "00:03:50:AB:CD:EF",
                         "password": "12345", "ssdp_location": "http://192.168.1.35:49153/description.xml"},
                "options": {"file_path": "C:" + "\\Users\\someone\\myhome.yaml"},
            },
            "bus_monitor": {"recent_frames": [{"raw": "*1*1*12##"}]},
        },
    }, indent=2), encoding="utf-8")

    a = anonymize(plant)

    yaml_text = (plant / "myhome.yaml").read_text(encoding="utf-8")
    assert "Kitchen" not in yaml_text and "Emma" not in yaml_text and "House" not in yaml_text
    assert "light_12:" in yaml_text and 'name: "Light 12"' in yaml_text  # quoting kept
    assert "# ── group 1 ──" in yaml_text and "Kitchen" not in yaml_text  # room comments neutralized
    assert "light_0311i01:" in yaml_text  # the bus interface stays part of the identity
    assert "entity_name: Light 0311I01" in yaml_text
    assert "sensor_51_power:" in yaml_text and "sensor_51_energy:" in yaml_text  # same meter, two classes
    assert yaml_text.count("name: Sensor 51" + chr(10)) == 2  # ... one device name; HA adds the class to the entity id
    assert "not_a_device: 3" in yaml_text  # non-device entries pass through
    assert 'mac: "00:03:50:00:09:99"' in yaml_text  # from the issue number
    assert a.entity_ids["light.kitchen_ceiling"] == "light.light_12"
    assert a.entity_ids["light.emma_s_room"] == "light.light_0311i01"
    assert a.entity_ids["sensor.house_meter_power"] == "sensor.sensor_51_power"

    diag = json.loads((plant / "diagnostic_summary.json").read_text(encoding="utf-8"))
    entry = diag["data"]["config_entry"]
    assert entry["entry_id"] == "01PLANTISSUE999F4540000000" and len(entry["entry_id"]) == 26
    assert entry["data"]["password"] is None
    assert entry["data"]["host"] == "192.0.2.1" and entry["data"]["ssdp_location"] == "http://192.0.2.1:49153/description.xml"
    assert entry["data"]["mac"] == "00:03:50:00:09:99"
    assert diag["home_assistant"]["timezone"] == "UTC"
    assert diag["data"]["bus_monitor"]["recent_frames"] == [{"raw": "*1*1*12##"}]  # frames are untouched
    assert diag["custom_components"] == {"myhome": {"version": "2.0.0b12"}}  # what else the home runs is dropped
    assert list(diag["setup_times"]) == ["01PLANTISSUE999F4540000000"]  # the old id is gone everywhere
    assert entry["options"]["file_path"] == "/config/myhome.yaml"

    # a second MAC in the same plant counts up; the same input maps to the same output
    b = Anonymizer("issue_999_f454")
    assert b.scrub_text("00:03:50:aa:bb:cc then 00:03:50:11:22:33 then 00:03:50:AA:BB:CC") == (
        "00:03:50:00:09:99 then 00:03:50:00:09:9a then 00:03:50:00:09:99"
    )


def test_cli_writes_the_mapping(tmp_path, capsys):
    src = PLANTS / "issue_297_f454"
    plant = tmp_path / "issue_297_f454"
    shutil.copytree(src, plant)
    mapping = tmp_path / "mapping.json"
    assert main([str(plant), "--mapping", str(mapping)]) == 0
    assert "issue_297_f454" in capsys.readouterr().out
    written = json.loads(mapping.read_text(encoding="utf-8"))["issue_297_f454"]
    assert set(written) == {"entity_ids", "ips", "macs"}
    # already synthetic: running it again changes nothing
    assert (plant / "myhome.yaml").read_text(encoding="utf-8") == (src / "myhome.yaml").read_text(encoding="utf-8")


def test_nothing_in_the_test_tree_looks_personal():
    """The same rules, over every data and source file under tests/ - wherever a dump lands."""
    assert check([PLANTS.parent]) == 0


def test_check_reports_and_rejects(tmp_path, capsys):
    dump = tmp_path / "diagnostics.json"
    dump.write_text(json.dumps({"host": "192.168.1.35", "mac": "00:03:50:AB:CD:EF", "password": "12345",
                                "entry_id": "01M284WWKZG4XTEG62NVW1DPVG", "file_path": "C:/Users/someone/x"}), encoding="utf-8")
    assert findings(dump.read_text(encoding="utf-8")) == ["LAN address", "non-synthetic MAC", "secret value", "local path", "config-entry id"]
    assert findings("mac = '00:03:50:aa:bb:cc'; password = '123'", data_file=False) == []  # placeholders in source are fine
    assert findings("x = '/home/alice/.homeassistant'", data_file=False) == ["local path"]  # a user name is not
    assert findings("192.168.1.1  # privacy-check: allow-samples") == []
    assert check([dump]) == 1
    assert "diagnostics.json: LAN address" in capsys.readouterr().out
    assert main(["--check", str(dump)]) == 1


def test_card_exports_carry_no_fingerprint_or_address():
    """The issue bundle and the JSON export name the transport and the model, never the browser or the address."""
    card = (Path(__file__).resolve().parents[1] / "custom_components" / "myhome" / "frontend" / "myhome-bus-card.js").read_text(encoding="utf-8")
    assert "userAgent" not in card and "user_agent" not in card
    assert "${gw.host}" not in card and "${gw.port" not in card and "${gw.serial_port}" not in card


def _sanitize(text: str) -> tuple[str, dict]:
    out = Anonymizer("issue_500_f454").json_text(text)
    return out, json.loads(out)  # valid JSON, whatever came in


def test_diagnostics_are_sanitized_structurally_not_textually():
    """Review of #316: single-line JSON, numeric passwords and escaped quotes are user input."""
    # compact, single-line JSON still drops the other integrations
    out, diag = _sanitize('{"custom_components":{"alarmo":{"version":"1"},"myhome":{"version":"2"}},"data":{}}')
    assert diag["custom_components"] == {"myhome": {"version": "2"}}
    # a numeric password is cleared like a string one
    out, diag = _sanitize('{"data":{"config_entry":{"data":{"password":12345,"pin":1234,"token":"x"}}}}')
    assert diag["data"]["config_entry"]["data"] == {"password": None, "pin": None, "token": None}
    # a password with an escaped quote and a backslash neither survives nor breaks the file
    secret = 'say "hi"' + "\\"
    raw = json.dumps({"data": {"config_entry": {"entry_id": "01M284WWKZG4XTEG62NVW1DPVG", "data": {"password": secret, "host": "10.0.0.7"}}}})
    out, diag = _sanitize(raw)
    assert diag["data"]["config_entry"]["data"] == {"password": None, "host": "192.0.2.1"}
    assert "hi" not in out and "01M284" not in out
    # nested and list-valued places are walked too: the original id under setup_times and inside strings
    out, diag = _sanitize(json.dumps({
        "setup_times": {"01M284WWKZG4XTEG62NVW1DPVG": {"setup": 0.1}},
        "data": {"config_entry": {"entry_id": "01M284WWKZG4XTEG62NVW1DPVG", "options": {"file_path": "/home/rossi/myhome.yaml"}},
                 "issues": [{"note": "entry 01M284WWKZG4XTEG62NVW1DPVG at 192.168.1.9 mac 00:03:50:AB:CD:EF"}]},
        "home_assistant": {"timezone": "Europe/Rome"},
    }))
    assert list(diag["setup_times"]) == ["01PLANTISSUE500F4540000000"]
    assert diag["data"]["config_entry"]["options"]["file_path"] == "/config/myhome.yaml"
    assert diag["data"]["issues"] == [{"note": "entry 01PLANTISSUE500F4540000000 at 192.0.2.1 mac 00:03:50:00:05:00"}]
    assert diag["home_assistant"]["timezone"] == "UTC"
    assert findings(out) == []


def test_serializer_keeps_frames_one_per_line_and_round_trips():
    from scripts.anonymize_plant_fixture import dump_json

    value = {"a": {"b": [], "c": {}}, "frames": [{"raw": "*1*1*12##", "n": 1}, {"raw": "*1*0*12##", "n": 2}],
             "nested": [{"x": [1, 2]}, 3], "s": 'é "q"'}
    out = dump_json(value)
    assert json.loads(out) == value
    assert '  "frames": [\n    {"raw": "*1*1*12##", "n": 1},\n    {"raw": "*1*0*12##", "n": 2}\n  ]' in out
    assert '"b": []' in out and '"c": {}' in out
