"""#429: WHO 4 dimension 7 drives the climate zones of MyHomeServer1 + Home+Control plants.

Every frame here comes from the traces in ``tests/fixtures/traces/issue_429``
(see its README for who recorded what); the expected states are what the
reporters said they did at those times.
"""
import json
import re
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest
from homeassistant.components.climate.const import HVACAction, HVACMode
from OWNd.message import OWNEvent, OWNHeatingEvent

from custom_components.myhome.climate import MESSAGE_TYPE_ZONE_STATE, MyHOMEClimate

TRACES = Path(__file__).parent / "fixtures" / "traces" / "issue_429"

# OWNd up to 2.0.0b8 parses dimension 7 with no message type (OWNd#58, fixed
# in OWNd#60).  Detect it instead of pinning a version; strict=True turns an
# unexpected pass into a failure so the marker cannot outlive the old OWNd.
_OWND_DROPS_DIMENSION_7 = OWNHeatingEvent("*#4*2*7*1*1*0170##").message_type != MESSAGE_TYPE_ZONE_STATE
needs_dimension_7 = pytest.mark.xfail(
    _OWND_DROPS_DIMENSION_7,
    reason="installed OWNd parses WHO 4 dimension 7 with no message type (OWNd#58)",
    strict=True,
)


def _card_trace(name: str) -> list[tuple[str, str]]:
    """(HH:MM:SS.fff, raw) of the received frames of a bus-card export."""
    frames = json.loads((TRACES / name).read_text(encoding="utf-8"))["frames"]
    return [(f["iso_time"][11:23], f["raw"]) for f in frames if f["direction"] == "rx"]


def _text_trace(name: str) -> list[tuple[str, str]]:
    """(HH:MM:SS.fff, raw) of the received frames of a pasted text trace."""
    pattern = re.compile(r"^\[?(\d\d:\d\d:\d\d\.\d{3})\]? \[?RX\]? (?:Heating )?(\*\S+##)$")
    lines = (TRACES / name).read_text(encoding="utf-8").splitlines()
    return [m.groups() for m in map(pattern.match, lines) if m]


class Plant:
    """One climate entity per zone, fed the zone's frames in bus order."""

    def __init__(self, hass, zones: dict[str, tuple[bool, bool]]) -> None:
        gateway = MagicMock()
        gateway.mac = "00:03:50:00:04:29"
        gateway.log_id = "[429]"
        gateway.send = AsyncMock()
        self.zones: dict[str, MyHOMEClimate] = {}
        for zone, (heating, cooling) in zones.items():
            climate = MyHOMEClimate(
                hass=hass, name=f"Zone {zone}", device_id=f"4-{zone}", who="4", where=zone,
                heating=heating, cooling=cooling, fan=False, standalone=True, central=False,
                manufacturer="BTicino", model="Heating Zone", gateway=gateway,
            )
            climate.hass = hass
            climate.entity_id = f"climate.zone_{zone}"
            climate.async_write_ha_state = MagicMock()
            self.zones[zone] = climate
        self._cursor: dict[int, int] = {}

    def replay(self, trace: list[tuple[str, str]], until: str = "99") -> None:
        """Deliver the received events up to and including time ``until``, continuing where the last call stopped."""
        position = self._cursor.get(id(trace), 0)
        while position < len(trace) and trace[position][0] <= until:
            event = OWNEvent.parse(trace[position][1])
            if isinstance(event, OWNHeatingEvent) and str(event.zone) in self.zones:
                self.zones[str(event.zone)].handle_event(event)
            position += 1
        self._cursor[id(trace)] = position

    def state(self, zone: str) -> tuple[HVACMode | None, float | None]:
        """The zone's mode and its nominal setpoint (what HEAT/COOL restores)."""
        climate = self.zones[zone]
        return climate.hvac_mode, climate._target_temperature  # noqa: SLF001


def _dark_wizard(hass) -> Plant:
    # Zones 1-3 heat and cool; zone 4 is a heating-only bathroom (comment 5800919197).
    return Plant(hass, {"1": (True, True), "2": (True, True), "3": (True, True), "4": (True, False)})


def _xtimmy(hass) -> Plant:
    return Plant(hass, {str(z): (True, False) for z in range(1, 8)})


@needs_dimension_7
def test_zone_off_to_manual_and_back_to_off(hass):
    """18:11 zone 2 OFF -> manual at 26 °C in cooling; 18:13:48 OFF again."""
    plant = _dark_wizard(hass)
    trace = _card_trace("myhome_trace_MyHomeServer1_who4_2026-09-23T18-13-49.json")

    # The 35.0 °C protection value comes first, the real setpoint 230 ms later.
    plant.replay(trace, until="18:11:35.100")
    assert plant.state("2") == (HVACMode.COOL, 35.0)
    plant.replay(trace, until="18:11:36")
    assert plant.state("2") == (HVACMode.COOL, 26.0)
    assert plant.zones["2"].target_temperature == 26.0

    plant.replay(trace)
    # *#4*2*7*2*2## + *4*202*2##: OFF, the nominal setpoint is kept (#383)
    assert plant.state("2") == (HVACMode.OFF, 26.0)
    assert plant.zones["2"].hvac_action == HVACAction.OFF
    # the heating-only bathroom sits in heating protection all along
    assert plant.state("4") == (HVACMode.OFF, None)


@needs_dimension_7
def test_manual_setpoint_changes_follow_dimension_7(hass):
    """19:18 on at 29 °C, 19:20 23 °C, 19:21 33 °C, 19:22 OFF."""
    plant = _dark_wizard(hass)
    trace = _card_trace("myhome_trace_MyHomeServer1_who4_2026-09-23T19-22-25.json")

    for until, setpoint in (("19:18:19", 29.0), ("19:20:21", 23.0), ("19:21:34", 33.0)):
        plant.replay(trace, until=until)
        assert plant.state("2") == (HVACMode.COOL, setpoint), until

    plant.replay(trace)
    assert plant.state("2") == (HVACMode.OFF, 33.0)


@needs_dimension_7
def test_summer_scenario_on_and_off(hass):
    plant = _dark_wizard(hass)
    trace = _card_trace("myhome_trace_MyHomeServer1_who4_2026-09-23T19-28-14.json")

    plant.replay(trace, until="19:27:00")
    for zone in ("1", "2", "3"):
        assert plant.state(zone) == (HVACMode.COOL, 22.5), zone
    assert plant.state("4") == (HVACMode.OFF, None)

    plant.replay(trace)
    for zone in ("1", "2", "3", "4"):
        assert plant.state(zone)[0] == HVACMode.OFF, zone


@needs_dimension_7
def test_winter_command_and_all_off(hass):
    plant = _dark_wizard(hass)
    trace = _card_trace("myhome_trace_MyHomeServer1_who4_2026-09-23T19-32-29.json")

    plant.replay(trace, until="19:31:00")
    for zone in ("1", "2", "3", "4"):
        assert plant.state(zone) == (HVACMode.HEAT, 22.5), zone

    plant.replay(trace)
    for zone in ("1", "2", "3", "4"):
        # heating protection (antifreeze): OFF, 7.0 °C shown, 22.5 °C kept
        assert plant.state(zone) == (HVACMode.OFF, 22.5), zone
        assert plant.zones[zone].target_temperature == 7.0


@needs_dimension_7
def test_heating_cooling_switches(hass):
    plant = _dark_wizard(hass)
    trace = _card_trace("myhome_trace_MyHomeServer1_who4_2026-09-23T19-39-59.json")

    expected = (("19:36:36", HVACMode.COOL), ("19:38:05", HVACMode.HEAT), ("19:39:28", HVACMode.COOL))
    for until, mode in expected:
        plant.replay(trace, until=until)
        for zone in ("1", "2", "3"):
            assert plant.state(zone) == (mode, 22.5), (until, zone)
    # the bathroom follows heating, and is protected (OFF) while cooling
    assert plant.state("4")[0] == HVACMode.OFF


@needs_dimension_7
def test_away_mode_is_a_plain_setpoint(hass):
    """Away and its setpoint change show up as heating setpoints, no preset."""
    plant = _dark_wizard(hass)
    trace = _card_trace("myhome_trace_MyHomeServer1_who4_2026-09-23T20-20-42.json")

    for until, setpoint in (("20:18:59", 22.5), ("20:19:20", 26.5), ("20:20:24", 21.0)):
        plant.replay(trace, until=until)
        for zone in ("1", "2", "3", "4"):
            assert plant.state(zone) == (HVACMode.HEAT, setpoint), (until, zone)


def test_restart_status_replies_carry_no_dimension_7(hass):
    """After a restart the zones answer *#4*Z## without dimension 7; WHAT 202/102 still turn them OFF."""
    trace = _card_trace("myhome_trace_MyHomeServer1_who4_2026-09-23T22-25-37.json")
    assert not [raw for _, raw in trace if re.match(r"^\*#4\*\d+\*7\*", raw)]

    plant = _dark_wizard(hass)
    plant.replay(trace)
    for zone in ("1", "2", "3", "4"):
        assert plant.state(zone)[0] == HVACMode.OFF, zone


@needs_dimension_7
def test_manual_override_and_schedule_step(hass):
    """22:37 zone 2 manual 20 °C, 22:38 back to the cooling schedule, 22:45 Summer ECO."""
    plant = _dark_wizard(hass)
    trace = _card_trace("myhome_trace_MyHomeServer1_who4_2026-09-23T22-45-26.json")

    plant.replay(trace, until="22:37:14")
    assert plant.state("2") == (HVACMode.COOL, 20.0)
    assert plant.state("1") == (HVACMode.COOL, 22.5)
    plant.replay(trace, until="22:38:12")
    assert plant.state("2") == (HVACMode.COOL, 22.5)

    plant.replay(trace)
    for zone in ("1", "2", "3"):
        assert plant.state(zone) == (HVACMode.COOL, 24.5), zone
    assert plant.state("4")[0] == HVACMode.OFF


@needs_dimension_7
def test_myhomeserver1_program_night_comfort_eco(hass):
    """13 h passive trace: 06:00 Night -> Comfort, 09:00 Comfort -> Eco, all driven by dimension 7 writes."""
    plant = _xtimmy(hass)
    trace = _text_trace("xtimmy86x_capture.24092026.00-13.txt")
    assert len(trace) > 2000

    plant.replay(trace, until="05:59:59")
    assert {z: plant.state(z) for z in plant.zones} == {z: (HVACMode.HEAT, 17.0) for z in plant.zones}

    plant.replay(trace, until="06:05:00")
    comfort = {"1": 20.0, "2": 20.0, "3": 20.0, "4": 20.0, "5": 18.5, "6": 18.5, "7": 19.0}
    assert {z: plant.state(z) for z in plant.zones} == {z: (HVACMode.HEAT, t) for z, t in comfort.items()}

    plant.replay(trace)
    assert {z: plant.state(z) for z in plant.zones} == {z: (HVACMode.HEAT, 18.0) for z in plant.zones}


@needs_dimension_7
def test_setpoint_from_home_assistant_leaves_the_program(hass):
    """13:56 HA sets zone 1 to 20 °C; MyHomeServer1's next program writes skip zone 1."""
    trace = _text_trace("xtimmy86x_ha_setpoint_2026-09-24.txt")
    written = {raw.split("*")[2] for _, raw in trace if raw.startswith("*#4*") and "*#7*" in raw}
    assert written == {"2", "3", "4", "5", "6", "7"}

    plant = _xtimmy(hass)
    plant.replay(trace)

    assert plant.zones["1"].hvac_mode == HVACMode.HEAT
    assert plant.zones["1"].target_temperature == 20.0
    for zone in ("2", "3", "4", "5", "6", "7"):
        assert plant.state(zone) == (HVACMode.HEAT, 16.0), zone


def test_every_trace_is_used():
    used = {p.name for p in TRACES.iterdir() if p.suffix in (".json", ".txt")}
    source = Path(__file__).read_text(encoding="utf-8")
    assert {name for name in used if name not in source} == set()
