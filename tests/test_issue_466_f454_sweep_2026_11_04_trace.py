"""#466: Physical BTicino F454 (FW 2.0.51) sweep + MyHOME scenario-controller traces.

Contributed by @gimprota76 after upgrading to 2.0.0b17, in:
- https://github.com/OpenWebNet-HA/MyHOME/issues/466#issuecomment-6626357212 (bus sweep)
- https://github.com/OpenWebNet-HA/MyHOME/issues/466#issuecomment-6626397744 (scenario controller)
(BTicino F454 firmware 2.0.51, Home Assistant 2026.10.4, MyHOME 2.0.0b17, OWNd 2.0.0b13.)

Two verbatim exports are pinned:

* ``myhome_sweep_F454_all_2026-11-04T08-31-02.json`` - an 89-frame bus sweep whose WHO 5 run
  captures a full 3486 arm/disarm cycle, and whose 36 WHO 18 frames read the nine energy meters ``5N``
  (WHERE 51..59) with dimension 51 (energy totalizer) and 1200 (automatic-update control), and the
  ``7N#0`` Energy Management Actuator address (71#0..79#0).
* ``myhome_sweep_F454_all_2026-11-04T08-33-48.json`` - an 82-frame export taken while the MyHOME
  scenario controller fired, adding four WHO 0 scenario-module frames and WHO 1 / WHO 2 point
  activations with their ``1000#M`` / advanced-position echoes. The companion T08-33-52 export the
  contributor attached is frame-identical (timestamps aside) and is not duplicated here.

The traces are read-only evidence: no gateway network identity (WHO 13 dimensions 10/11/12) is
present in either export, so they are committed verbatim.
"""

from __future__ import annotations

import collections
import hashlib
import json
from pathlib import Path

from OWNd.message import (
    OWNAlarmEvent,
    OWNEnergyCommand,
    OWNMessage,
    OWNScenarioEvent,
)

FIXTURES = Path(__file__).resolve().parent / "fixtures/traces/issue_466"
SWEEP = FIXTURES / "myhome_sweep_F454_all_2026-11-04T08-31-02.json"
SCENARIO = FIXTURES / "myhome_sweep_F454_all_2026-11-04T08-33-48.json"

# Verbatim provenance: the exact bytes contributed on the issue.
SWEEP_SHA256 = "423cbe44f2f150edab978ec15897908eda1d9b9bc1efda2e0fec8ec3abe11850"
SCENARIO_SHA256 = "aedb53f148c27d668a78f3af38e132855dae90edb486d369bd07d2f4155dfbfc"


def _doc(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _frames(path: Path) -> list[dict]:
    return _doc(path)["frames"]


def _raws(path: Path, who: int) -> list[str]:
    return [f["raw"] for f in _frames(path) if str(f.get("who")) == str(who)]


def test_fixture_provenance_is_pinned() -> None:
    assert hashlib.sha256(SWEEP.read_bytes()).hexdigest() == SWEEP_SHA256
    assert hashlib.sha256(SCENARIO.read_bytes()).hexdigest() == SCENARIO_SHA256


def test_capture_environment_is_the_f454_2_0_51_on_b17() -> None:
    for path in (SWEEP, SCENARIO):
        doc = _doc(path)
        assert doc["gateway"]["model"] == "F454"
        assert doc["gateway"]["firmware"] == "2.0.51"
        env = doc["environment"]
        assert env["home_assistant_version"] == "2026.10.4"
        assert env["integration_version"] == "2.0.0b17"
        assert env["ownd_version"] == "2.0.0b13"


def test_no_gateway_network_identity_is_committed() -> None:
    # WHO 13 dimensions 10 / 11 / 12 (IP, netmask, MAC) must not be published in a fixture.
    for path in (SWEEP, SCENARIO):
        for raw in _raws(path, 13):
            assert not raw.startswith(("*#13**10*", "*#13**11*", "*#13**12*"))


def test_every_sweep_frame_parses() -> None:
    frames = _frames(SWEEP)
    assert len(frames) == 89
    messages = [OWNMessage.parse(f["raw"]) for f in frames]
    assert all(m is not None for m in messages)
    kinds = collections.Counter(type(m).__name__ for m in messages)
    # 36 WHO 18 energy, 32 WHO 5 alarm, 15 WHO 1 (10 events + 5 status requests),
    # 5 WHO 2 covers, 1 WHO 13 beacon.
    assert kinds["OWNEnergyCommand"] == 36
    assert kinds["OWNAlarmEvent"] == 32
    assert kinds["OWNLightingEvent"] + kinds["OWNLightingCommand"] == 15
    assert kinds["OWNAutomationEvent"] == 5
    assert kinds["OWNGatewayEvent"] == 1


def test_every_scenario_frame_parses() -> None:
    frames = _frames(SCENARIO)
    assert len(frames) == 82
    messages = [OWNMessage.parse(f["raw"]) for f in frames]
    assert all(m is not None for m in messages)
    kinds = collections.Counter(type(m).__name__ for m in messages)
    # 36 WHO 18 energy, 20 WHO 2 covers, 20 WHO 1 (16 events + 4 status requests),
    # 4 WHO 0 scenarios, 2 WHO 13 beacons.
    assert kinds["OWNEnergyCommand"] == 36
    assert kinds["OWNScenarioEvent"] == 4
    assert kinds["OWNAutomationEvent"] == 20
    assert kinds["OWNLightingEvent"] + kinds["OWNLightingCommand"] == 20
    assert kinds["OWNGatewayEvent"] == 2


def test_sweep_who18_reads_the_meters_and_the_actuator_address() -> None:
    # The third field is the WHERE: the nine 5N energy meters and the 7N#0 actuators.
    wheres = {r.split("*")[2] for r in _raws(SWEEP, 18)}
    assert {"51", "52", "53", "54", "55", "56", "57", "58", "59"} <= wheres
    # Energy Management Actuator 7N#0 (71#0..79#0): the published WHO 18 WHERE form.
    assert {f"{7}{n}#0" for n in range(1, 10)} <= wheres
    for r in _raws(SWEEP, 18):
        assert isinstance(OWNMessage.parse(r), OWNEnergyCommand)
    # The dimensions read are 51 (energy totalizer) and 1200 (automatic-update control).
    assert {r.split("*")[3].rstrip("#") for r in _raws(SWEEP, 18)} <= {"51", "1200"}



def test_sweep_alarm_deactivation_decodes_as_deactivation_not_silent() -> None:
    # The real F454 emits *5*2*0## (WHAT 2); OWNd decodes it as deactivation, not a silent alarm.
    deact = OWNMessage.parse("*5*2*0##")
    assert isinstance(deact, OWNAlarmEvent)
    assert getattr(deact, "state_name", None) == "deactivation"
    assert deact.is_silent is False
    assert "*5*2*0##" in _raws(SWEEP, 5)
    # *5*1*0## is activation and is present (emitted during both arm and disarm, per #466/#841).
    activ = OWNMessage.parse("*5*1*0##")
    assert isinstance(activ, OWNAlarmEvent)
    assert activ.state_name == "activation"
    assert "*5*1*0##" in _raws(SWEEP, 5)


def test_sweep_alarm_zone_states_decode_across_the_zones() -> None:
    # Zone states captured during the 3486 cycle. Asserted as OWNd-version-stable facts
    # (class + state_name + presence); the numeric ``zone_number`` property is exercised in
    # OWNd's own tests, since it is not in every pinned OWNd release.
    for zone in (1, 2, 3, 4):
        active = OWNMessage.parse(f"*5*11*#{zone}##")
        assert isinstance(active, OWNAlarmEvent)
        assert active.state_name == "active zone"
        assert f"*5*11*#{zone}##" in _raws(SWEEP, 5)
    for zone in (5, 6, 7, 8):
        inactive = OWNMessage.parse(f"*5*18*#{zone}##")
        assert isinstance(inactive, OWNAlarmEvent)
        assert inactive.state_name == "non-active zone"
        assert f"*5*18*#{zone}##" in _raws(SWEEP, 5)
    intrusion = OWNMessage.parse("*5*15*#2##")
    assert isinstance(intrusion, OWNAlarmEvent)
    assert intrusion.state_name == "intrusion alarm"
    assert "*5*15*#2##" in _raws(SWEEP, 5)


def test_scenario_controller_fires_four_scenarios_on_module_43() -> None:
    who0 = _raws(SCENARIO, 0)
    assert who0 == ["*0*1*43##", "*0*3*43##", "*0*2*43##", "*0*4*43##"]
    for raw in who0:
        assert isinstance(OWNMessage.parse(raw), OWNScenarioEvent)
