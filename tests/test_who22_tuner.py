"""WHO=22 reports reaching a WHO=16 tuner entity.

The F500 mirrors its WHO=16 reports onto WHO=22 (source address ``5#2#S``). The
frames are the ones recorded from a live F500N on an MH200N, in
``tests/fixtures/traces/f500_tuner``.
"""
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from homeassistant.components.media_player import MediaPlayerState
from homeassistant.const import CONF_MAC
from OWNd.message import OWNMessage, OWNSoundEvent

from custom_components.myhome.data import MyHOMERuntimeData
from custom_components.myhome.sound_source import (
    MyHOMESoundSource,
    who22_source_id,
    who22_tuner_listener,
)
from tests.conftest import attach_platform


def _frame(where, where_param, dimension, *values, who="22"):
    """A WHO=22 frame as the gateway handler hands it on."""
    return SimpleNamespace(
        who=who,
        where=where,
        where_param=list(where_param),
        dimension=dimension,
        dimension_value=list(values),
    )


def _tuner(hass, gateway, source=1):
    entity = MyHOMESoundSource(
        hass=hass,
        name=f"Radio {source}",
        device_id=f"10{source}#16",
        who="16",
        where=f"10{source}",
        manufacturer="BTicino",
        model="Audio Source",
        gateway=gateway,
    )
    entity.hass = hass
    entity.entity_id = f"media_player.radio_{source}"
    entity.async_schedule_update_ha_state = MagicMock()
    entry = MagicMock()
    entry.data = {CONF_MAC: gateway.mac}
    entry.runtime_data = MyHOMERuntimeData(gateway=gateway)
    attach_platform(entity, entry)
    return entity


@pytest.fixture
def gateway():
    gw = MagicMock()
    gw.mac = "00:11:22:33:44:55"
    gw.log_id = "[MYHOME gateway - 192.168.1.5]"
    gw.send = AsyncMock()
    gw.send_status_request = AsyncMock()
    return gw


@pytest.fixture
def tuner(hass, gateway):
    return _tuner(hass, gateway)


def test_frequency_and_preset_report(tuner):
    tuner.handle_who22_event(_frame("5", ["2", "1"], 11, "1", "9620", "2"))
    assert tuner.extra_state_attributes == {"frequency": 96.2, "station": 2}
    assert tuner.source == "Station 2"


def test_frequency_without_a_preset_clears_the_previous_station(tuner):
    tuner.handle_who22_event(_frame("5", ["2", "1"], 11, "1", "9620", "2"))
    tuner.handle_who22_event(_frame("5", ["2", "1"], 5, "1", "8950"))
    assert tuner.extra_state_attributes == {"frequency": 89.5}
    assert tuner.source is None


def test_a_new_frequency_drops_the_old_stations_text(tuner):
    tuner.handle_who22_event(_frame("5", ["2", "1"], 10, *"75*82*79*78*69*72*73*84".split("*")))
    assert tuner.media_title == "KRONEHIT"
    tuner.handle_who22_event(_frame("5", ["2", "1"], 5, "1", "9030"))
    assert tuner.media_title is None


def test_rds_text_and_its_blanking_while_retuning(tuner):
    tuner.handle_who22_event(_frame("5", ["2", "1"], 10, *"75*82*79*78*69*72*73*84".split("*")))
    assert tuner.media_title == "KRONEHIT"
    tuner.handle_who22_event(_frame("5", ["2", "1"], 10, *["32"] * 8))
    assert tuner.media_title is None


def test_a_twelve_code_rds_glitch_leaves_the_title_alone(tuner):
    tuner.handle_who22_event(_frame("5", ["2", "1"], 10, *"75*82*79*78*69*72*73*84".split("*")))
    tuner.handle_who22_event(
        _frame("5", ["2", "1"], 10, *"42*42*79*79*69*42*79*79*69*42*42*32".split("*"))
    )
    assert tuner.media_title == "KRONEHIT"


def test_station_report_from_a_query_reply(tuner):
    tuner.handle_who22_event(_frame("2", ["1"], 6, "3"))
    assert tuner.extra_state_attributes == {"station": 3}


def test_mirrored_who16_and_who22_reports_agree(hass, gateway):
    """``bt_difson`` (WHO=16) and ``bt_multi`` (WHO=22) race; either order lands on one state."""
    who16_first = _tuner(hass, gateway, 1)
    who16_first.handle_event(OWNMessage.parse("*#16*101*6*0*96200##"))
    who16_first.handle_event(OWNMessage.parse("*#16*101*7*0*2##"))
    who16_first.handle_who22_event(_frame("5", ["2", "1"], 5, "1", "9620"))
    who16_first.handle_who22_event(_frame("5", ["2", "1"], 11, "1", "9620", "2"))

    who22_first = _tuner(hass, gateway, 2)
    who22_first.handle_who22_event(_frame("5", ["2", "2"], 5, "1", "9620"))
    who22_first.handle_who22_event(_frame("5", ["2", "2"], 11, "1", "9620", "2"))
    who22_first.handle_event(OWNMessage.parse("*#16*102*6*0*96200##"))
    who22_first.handle_event(OWNMessage.parse("*#16*102*7*0*2##"))

    assert isinstance(OWNMessage.parse("*#16*101*6*0*96200##"), OWNSoundEvent)
    assert who16_first.extra_state_attributes == {"frequency": 96.2, "station": 2}
    assert who22_first.extra_state_attributes == {"frequency": 96.2, "station": 2}
    assert who16_first.source == who22_first.source == "Station 2"


@pytest.mark.parametrize(
    ("where", "param", "expected"),
    [
        ("5", ["2", "1"], 1),
        ("5", ["2", "4"], 4),
        ("2", ["3"], 3),
        ("3", ["4", "1"], None),  # an amplifier, not a source
        ("6", [], None),
        ("5", ["3", "1"], None),
    ],
)
def test_who22_source_id_reads_the_address(where, param, expected):
    assert who22_source_id(_frame(where, param, 5, "1", "9620")) == expected


def test_only_who22_frames_are_placed():
    assert who22_source_id(_frame("5", ["2", "1"], 5, "1", "9620", who="16")) is None


def test_the_listener_feeds_the_declared_tuner_and_nothing_else(hass, gateway):
    first, second = _tuner(hass, gateway, 1), _tuner(hass, gateway, 2)
    listener = who22_tuner_listener([first, second])
    listener(_frame("5", ["2", "2"], 5, "1", "10350"))
    listener(_frame("5", ["2", "9"], 5, "1", "8880"))  # no such tuner declared
    assert first.extra_state_attributes == {}
    assert second.extra_state_attributes == {"frequency": 103.5}


def test_a_who22_frame_of_unknown_meaning_changes_nothing(tuner):
    tuner.handle_who22_event(_frame("5", ["2", "1"], 99, "1"))
    tuner.handle_who22_event(_frame("5", ["2", "1"], None))
    assert tuner.extra_state_attributes == {}


def test_real_owndiffusion_event_is_placed_when_owNd_parses_who22():
    """With an OWNd that parses WHO=22, its own reading of the address agrees."""
    event = OWNMessage.parse("*#22*5#2#1*11*1*9620*2##")
    if str(getattr(event, "who", "")) != "22" or not hasattr(event, "source_id"):
        pytest.skip("this OWNd does not parse WHO=22")
    assert who22_source_id(event) == 1


def test_who22_source_id_prefers_known_source_id():
    """Prefers the source_id worked out by OWNd when present."""
    event = SimpleNamespace(who="22", source_id=2, where="5", where_param=["2", "1"])
    assert who22_source_id(event) == 2



def test_entity_picture_follows_the_live_frequency(hass, gateway):
    tuner = _tuner(hass, gateway)
    tuner._pictures = {96200: "/local/radio/one.png"}
    assert tuner.entity_picture is None  # nothing heard yet
    tuner.handle_who22_event(_frame("5", ["2", "1"], 5, "1", "9620"))
    assert tuner.entity_picture == "/local/radio/one.png"
    tuner.handle_who22_event(_frame("5", ["2", "1"], 5, "1", "8950"))
    assert tuner.entity_picture is None  # an unlisted station shows no picture

def test_a_non_numeric_dimension_is_ignored(tuner):
    tuner.handle_who22_event(_frame("5", ["2", "1"], "abc", "1", "9620"))
    assert tuner.extra_state_attributes == {}


def test_a_frame_without_an_address_is_placed_nowhere():
    assert who22_source_id(SimpleNamespace(who="22", where=None)) is None


def test_source_id_reads_the_private_where_param_of_older_owndiffusion():
    frame = SimpleNamespace(who="22", where="5", _where_param=["2", "3"])
    assert who22_source_id(frame) == 3


def test_the_listener_ignores_frames_of_other_whos(hass, gateway):
    tuner = _tuner(hass, gateway)
    who22_tuner_listener([tuner])(_frame("5", ["2", "1"], 5, "1", "9620", who="16"))
    assert tuner.extra_state_attributes == {}


# ---------------------------------------------------------------------------
# Synthetic traces synthesized from own-firmware-oracle (bt_multi / bt_difson)
# (tests/fixtures/traces/oracle_who22_tuner). Explicitly marked synthetic.
# ---------------------------------------------------------------------------

_SYNTHETIC_ORACLE_DIR = (
    Path(__file__).parent / "fixtures" / "traces" / "oracle_who22_tuner"
)
_SYNTHETIC_MHS1_WHO22_ONLY = (
    _SYNTHETIC_ORACLE_DIR / "myhome_trace_oracle_myhomeserver1_who22_only_tuner.json"
)
_SYNTHETIC_MH200N_WHO22_FIRST = (
    _SYNTHETIC_ORACLE_DIR / "myhome_trace_oracle_mh200n_who22_first_dual_stack.json"
)


def _wire_to_who22_event(raw: str):
    """Parse a raw ``*#22*<WHERE>*<DIM>*<VALUES...>##`` frame into a handler event."""
    parts = raw.strip("#").split("*")
    addr = parts[2].split("#")
    return _frame(addr[0], addr[1:], int(parts[3]), *parts[4:], who=parts[1].lstrip("#"))


@pytest.mark.parametrize(
    "trace_path",
    [_SYNTHETIC_MHS1_WHO22_ONLY, _SYNTHETIC_MH200N_WHO22_FIRST],
    ids=lambda p: p.name,
)
def test_synthetic_oracle_traces_explicit_provenance(trace_path: Path):
    """Every synthetic trace in oracle_who22_tuner is explicitly marked synthetic."""
    data = json.loads(trace_path.read_text(encoding="utf-8"))
    capture = data["capture"]
    assert capture["synthetic"] is True
    assert capture["kind"] == "synthetic-firmware-oracle-trace"
    assert capture["source"] == "firmware-oracle-contract"
    assert "SYNTHETIC TRACE" in capture["provenance"]


def test_synthetic_myhomeserver1_who22_only_trace_replay(hass, gateway):
    """Replay the synthetic WHO 22-only MyHomeServer1 lifecycle step-by-step."""
    tuner = _tuner(hass, gateway, 1)
    listener = who22_tuner_listener([tuner])
    data = json.loads(_SYNTHETIC_MHS1_WHO22_ONLY.read_text(encoding="utf-8"))
    assert data["gateway"]["model"] == "MyHomeServer1"

    rx_frames = [f["raw"] for f in data["frames"] if f["direction"] == "rx"]
    assert rx_frames == [
        "*#*0##",
        "*#22*5#2#1*5*1*10150##",
        "*#22*5#2#1*11*1*10150*5##",
        "*#22*5#2#1*10*82*65*68*73*79*32*32*32##",
        "*#22*5#2#1*5*1*9950##",
        "*#22*5#2#1*10*32*32*32*32*32*32*32*32##",
        "*#22*5#2#1*10*82*65*68*73##",
        "*#22*5#2#1*10*68*69*69*74*65*89*32*32##",
        "*#22*5#2#1*10*42*42*79*79*69*42*82*65*68*42*42*32##",
        "*#22*5#2#1*11*1*8950*0##",
        "*#22*2#1*6*3##",
    ]

    # 1. Preset 5 tune at 101.50 MHz: bt_multi emits dim 5 then dim 11, then 8-code RDS
    listener(_wire_to_who22_event(rx_frames[1]))
    assert tuner.extra_state_attributes == {"frequency": 101.5}
    listener(_wire_to_who22_event(rx_frames[2]))
    assert tuner.extra_state_attributes == {"frequency": 101.5, "station": 5}
    assert tuner.source == "Station 5"
    listener(_wire_to_who22_event(rx_frames[3]))
    assert tuner.media_title == "RADIO"

    # 2. Manual tune (station='F') to 99.50 MHz: dim 5 only clears preset 5, 8 spaces blank RDS
    listener(_wire_to_who22_event(rx_frames[4]))
    assert tuner.extra_state_attributes == {"frequency": 99.5}
    assert tuner.source is None
    listener(_wire_to_who22_event(rx_frames[5]))
    assert tuner.media_title is None

    # 3. 4-code (k=0 lost middle SCS packet) RDS followed by full 8-code (k=1) 'DEEJAY  '
    listener(_wire_to_who22_event(rx_frames[6]))
    assert tuner.media_title == "RADI"
    listener(_wire_to_who22_event(rx_frames[7]))
    assert tuner.media_title == "DEEJAY"

    # 4. 12-code (k=2 missed EOT) buffer-collision glitch is ignored
    listener(_wire_to_who22_event(rx_frames[8]))
    assert tuner.media_title == "DEEJAY"

    # 5. Dimension 11 with preset=0 (unsaved station at 89.50 MHz) and dim 6 reply on 2#1
    listener(_wire_to_who22_event(rx_frames[9]))
    assert tuner.extra_state_attributes == {"frequency": 89.5}
    assert tuner.source is None
    listener(_wire_to_who22_event(rx_frames[10]))
    assert tuner.extra_state_attributes == {"frequency": 89.5, "station": 3}
    assert tuner.source == "Station 3"


def test_synthetic_mh200n_who22_first_dual_stack_trace_replay(hass, gateway):
    """Replay the synthetic MH200N trace where bt_multi (WHO 22) arrives before bt_difson (WHO 16)."""
    tuner = _tuner(hass, gateway, 1)
    listener = who22_tuner_listener([tuner])
    data = json.loads(_SYNTHETIC_MH200N_WHO22_FIRST.read_text(encoding="utf-8"))
    assert data["gateway"]["model"] == "MH200N"

    for frame in data["frames"]:
        raw = frame["raw"]
        if frame["who"] == "22":
            listener(_wire_to_who22_event(raw))
        elif frame["who"] == "16":
            tuner.handle_event(OWNMessage.parse(raw))

        if raw in ("*#22*5#2#1*11*1*10150*5##", "*#16*101*6*0*101500##", "*#16*101*7*0*5##"):
            assert tuner.extra_state_attributes == {"frequency": 101.5, "station": 5}
            assert tuner.source == "Station 5"
        elif "75*82*79*78*69*72*73*84" in raw or "42*42*79*79*69*42*82*65*68*42*42*32" in raw:
            assert tuner.media_title == "KRONEHIT"
        elif raw in ("*#22*5#2#1*5*1*9950##", "*#16*101*6*0*99500##", "*#16*101*7*0*0##"):
            assert tuner.extra_state_attributes == {"frequency": 99.5}
            assert tuner.source is None
            assert tuner.media_title is None


def test_who22_only_power_transitions_and_same_freq_preset_clear(hass, gateway):
    """WHO 22 power-on/power-off commands and dim 12 status update state; dim 11 preset=0 clears same-freq preset."""
    tuner = _tuner(hass, gateway, 1)
    listener = who22_tuner_listener([tuner])

    # 1. Power ON via *22*1*2#1## sets ON
    listener(OWNMessage.parse("*22*1*2#1##"))
    assert tuner.state == MediaPlayerState.ON

    # 2. Tune Preset 5 at 105.30 MHz + RDS 'DIM*SOFT'
    listener(_wire_to_who22_event("*#22*5#2#1*11*1*10530*5##"))
    listener(_wire_to_who22_event("*#22*5#2#1*10*68*73*77*42*83*79*70*84##"))
    assert tuner.extra_state_attributes == {"frequency": 105.3, "station": 5}
    assert tuner.source == "Station 5"
    assert tuner.media_title == "DIM*SOFT"

    # 3. Same frequency 105.30 MHz reported with preset=0 (dim 11) MUST still clear preset/source!
    listener(_wire_to_who22_event("*#22*5#2#1*11*1*10530*0##"))
    assert tuner.extra_state_attributes == {"frequency": 105.3}
    assert tuner.source is None

    # 4. Dimension 12 status report: 0 = OFF (clears media_title), 1 = ON
    listener(OWNMessage.parse("*#22*2#1*12*0*0##"))
    assert tuner.state == MediaPlayerState.OFF
    assert tuner.media_title is None

    listener(OWNMessage.parse("*#22*2#1*12*1*0##"))
    assert tuner.state == MediaPlayerState.ON

    # 5. Power OFF via *22*0*2#1## sets OFF and clears media_title
    listener(_wire_to_who22_event("*#22*5#2#1*10*82*65*68*73*79*32*32*32##"))
    assert tuner.media_title == "RADIO"
    listener(OWNMessage.parse("*22*0*2#1##"))
    assert tuner.state == MediaPlayerState.OFF
    assert tuner.media_title is None


def test_real_hl4684_f500_who22_only_replay(hass, gateway):
    """Replay ONLY the WHO 22 frames from physical H/L4684 trace 11-48-15 (simulating MyHomeServer1 WHO 22-only)."""
    trace_path = (
        Path(__file__).resolve().parent
        / "fixtures"
        / "traces"
        / "issue_466"
        / "myhome_trace_HL4684_all_2026-11-01T11-48-15.json"
    )
    doc = json.loads(trace_path.read_text(encoding="utf-8"))
    tuner = _tuner(hass, gateway, 1)
    listener = who22_tuner_listener([tuner])

    titles: list[str] = []
    states: list[MediaPlayerState | None] = []
    for f in doc["frames"]:
        if f["direction"] == "rx" and f["who"] == "22":
            msg = OWNMessage.parse(f["raw"])
            listener(msg)
            states.append(tuner.state)
            if tuner.media_title and (not titles or titles[-1] != tuner.media_title):
                titles.append(tuner.media_title)

    assert MediaPlayerState.ON in states
    assert "MY HOME" in titles
    assert "DIM*SOFT" in titles
    assert tuner.state == MediaPlayerState.OFF
    assert tuner.media_title is None
    assert tuner.extra_state_attributes == {"frequency": 105.3, "station": 5}
    assert tuner.source == "Station 5"


async def test_who22_only_gateway_outbound_commands(hass):
    """On a WHO 22-only gateway (MyHomeServer1), MyHOMESoundSource dispatches WHO 22 commands instead of WHO 16."""
    gw = MagicMock()
    gw.mac = "00:03:50:aa:bb:cc"
    gw.send = AsyncMock()
    gw.send_status_request = AsyncMock()
    gw.profile_supports_who = MagicMock(side_effect=lambda who: who == 22)

    tuner = _tuner(hass, gw, 1)
    assert tuner._uses_who22_commands() is True

    await tuner.async_added_to_hass()
    assert [str(call.args[0]) for call in gw.send.call_args_list] == ["*22*31*2#1##"]

    await tuner.async_update()
    sent_status = [str(call.args[0]) for call in gw.send_status_request.call_args_list]
    assert sent_status == ["*#22*2#1*5##", "*#22*2#1*6##", "*#22*2#1*12##"]

    gw.send.reset_mock()
    await tuner.async_turn_on()
    await tuner.async_turn_off()
    await tuner.async_media_next_track()
    await tuner.async_media_previous_track()
    await tuner.async_seek_up()
    await tuner.async_seek_down()
    await tuner.async_select_source("Station 5")
    await tuner.async_set_frequency(105.3)
    await tuner.async_store_station(5)
    await tuner.async_store_station(15)

    sent_cmds = [str(call.args[0]) for call in gw.send.call_args_list]
    assert sent_cmds == [
        "*22*1*2#1##",
        "*22*0*2#1##",
        "*22*9*2#1##",
        "*22*10*2#1##",
        "*22*5*2#1##",
        "*22*6*2#1##",
        "*#22*2#1*#6*5##",
        "*#22*2#1*#5*1*10530##",
        "*22*33#5*2#1##",
        "*22*33#15*2#1##",
    ]


def test_hl4684_empty_where_who22_frequency_frame_routes_to_sole_or_active_tuner(hass, gateway):
    """#466 (08-54-06 frame 124): H/L4684 emits *#22**5*1*9650## with empty WHERE below 100 MHz."""
    # 1. Sole declared tuner receives the empty-WHERE frequency frame
    tuner1 = _tuner(hass, gateway, 1)
    listener_single = who22_tuner_listener([tuner1])
    listener_single(OWNMessage.parse("*#22**5*1*9650##"))
    assert tuner1.extra_state_attributes == {"frequency": 96.5}

    # 2. Multiple declared tuners: routes to the single ON tuner
    tuner_a = _tuner(hass, gateway, 1)
    tuner_b = _tuner(hass, gateway, 2)
    tuner_a._attr_state = MediaPlayerState.OFF
    tuner_b._attr_state = MediaPlayerState.ON
    listener_multi = who22_tuner_listener([tuner_a, tuner_b])
    listener_multi(OWNMessage.parse("*#22**5*1*9650##"))
    assert tuner_a.extra_state_attributes == {}
    assert tuner_b.extra_state_attributes == {"frequency": 96.5}

    # 3. Multiple declared tuners with neither ON (ambiguous): ignored
    tuner_b._attr_state = MediaPlayerState.OFF
    listener_multi(OWNMessage.parse("*#22**5*1*9030##"))
    assert tuner_a.extra_state_attributes == {}
    assert tuner_b.extra_state_attributes == {"frequency": 96.5}



