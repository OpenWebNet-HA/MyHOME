"""Tuner decoding, checked against frames recorded from a live F500N on an MH200N.

The frames below are copied from ``tests/fixtures/traces/f500_tuner``; the
WHO=16 forms are the same tuner's mirrored reports, so both families must
agree on every fact they share.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from custom_components.myhome.tuner_decode import (
    FM_MAX_KHZ,
    FM_MIN_KHZ,
    TunerReport,
    decode_rds_frame,
    decode_who22_tuner,
    frequency_khz_from_who16,
    frequency_khz_from_who22,
    parse_tuner_pictures,
    preset_for_frequency,
    rds_text,
)


def _values(frame: str) -> tuple[int, list[str]]:
    """Split ``*#22*5#2#1*<DIM>*<VALUES...>##`` into (dimension, values)."""
    parts = frame.strip("#").split("*")
    return int(parts[3]), parts[4:]


@pytest.mark.parametrize(
    ("frame", "khz"),
    [
        ("*#22*5#2#1*5*1*8880##", 88800),
        ("*#22*5#2#1*5*1*9620##", 96200),
        ("*#22*5#2#1*5*1*9030##", 90300),
        ("*#22*5#2#1*5*1*10350##", 103500),
    ],
)
def test_who22_frequency_in_ten_khz_units(frame: str, khz: int) -> None:
    dimension, values = _values(frame)
    assert decode_who22_tuner(dimension, values) == TunerReport(frequency_khz=khz)


def test_who22_and_who16_frequencies_agree() -> None:
    """``*#16*101*6*0*96200##`` and ``*#22*5#2#1*5*1*9620##`` are one fact."""
    assert frequency_khz_from_who22("9620") == frequency_khz_from_who16("96200") == 96200


@pytest.mark.parametrize(
    ("frame", "khz", "preset"),
    [
        ("*#22*5#2#1*11*1*8880*1##", 88800, 1),
        ("*#22*5#2#1*11*1*9620*2##", 96200, 2),
        ("*#22*5#2#1*11*1*9030*3##", 90300, 3),
        ("*#22*5#2#1*11*1*10350*4##", 103500, 4),
    ],
)
def test_who22_frequency_and_preset(frame: str, khz: int, preset: int) -> None:
    dimension, values = _values(frame)
    assert decode_who22_tuner(dimension, values) == TunerReport(
        frequency_khz=khz, preset=preset
    )


def test_preset_zero_is_a_frequency_without_a_stored_station() -> None:
    assert decode_who22_tuner(11, ["1", "8950", "0"]) == TunerReport(frequency_khz=89500)


@pytest.mark.parametrize("preset", [1, 2, 3, 4])
def test_who22_station_report(preset: int) -> None:
    dimension, values = _values(f"*#22*2#1*6*{preset}##")
    assert decode_who22_tuner(dimension, values) == TunerReport(preset=preset)


def test_rds_decodes_station_names() -> None:
    dimension, values = _values("*#22*5#2#1*10*75*82*79*78*69*72*73*84##")
    assert decode_who22_tuner(dimension, values) == TunerReport(rds="KRONEHIT")
    dimension, values = _values("*#22*5#2#1*10*107*114*111*110*101*104*105*116##")
    assert decode_who22_tuner(dimension, values) == TunerReport(rds="kronehit")


def test_rds_text_of_a_retune_is_blank_not_missing() -> None:
    dimension, values = _values("*#22*5#2#1*10*32*32*32*32*32*32*32*32##")
    report = decode_who22_tuner(dimension, values)
    assert report == TunerReport(rds=None, rds_blank=True)


def test_rds_rotating_text_keeps_its_padding_characters() -> None:
    """Some stations rotate ``*RADIO* ``; the asterisks are real RDS characters."""
    dimension, values = _values("*#22*5#2#1*10*42*82*65*68*73*79*42*32##")
    assert decode_who22_tuner(dimension, values) == TunerReport(rds="*RADIO*")


def test_a_twelve_code_rds_frame_is_dropped_as_a_bus_glitch() -> None:
    values = "42*42*79*79*69*42*79*79*69*42*42*32".split("*")
    assert len(values) == 12
    assert decode_rds_frame(values) is None
    assert decode_who22_tuner(10, values) is None


@pytest.mark.parametrize(
    ("dimension", "values"),
    [
        (5, ["1"]),  # short
        (5, ["1", "96200"]),  # kHz where 10 kHz units belong: 962 MHz
        (5, ["1", "abc"]),
        (11, ["1", "9620"]),  # preset missing
        (11, ["1", "50", "2"]),  # below the FM band
        (6, ["0"]),  # preset numbers start at 1
        (99, ["1"]),  # a dimension this module does not know
        (None, []),
    ],
)
def test_malformed_or_unknown_frames_yield_nothing(dimension, values) -> None:
    assert decode_who22_tuner(dimension, values) is None


def test_fm_band_bounds() -> None:
    assert frequency_khz_from_who22(str(FM_MIN_KHZ // 10)) == FM_MIN_KHZ
    assert frequency_khz_from_who22(str(FM_MAX_KHZ // 10)) == FM_MAX_KHZ
    assert frequency_khz_from_who22(str(FM_MIN_KHZ // 10 - 1)) is None
    assert frequency_khz_from_who22(str(FM_MAX_KHZ // 10 + 1)) is None
    assert frequency_khz_from_who16(str(FM_MAX_KHZ + 1)) is None


def test_rds_text_drops_control_codes() -> None:
    assert rds_text(["72", "5", "73"]) == "HI"
    assert rds_text(["32"] * 8) is None


def test_preset_follows_the_live_frequency_not_the_other_way_round() -> None:
    presets = {1: 88800, 2: 96200, 3: 90300}
    assert preset_for_frequency(96200, presets) == 2
    # An unsaved station gets no preset, so it cannot inherit one's identity.
    assert preset_for_frequency(89500, presets) is None

def test_tuner_pictures_parse_one_entry_per_line() -> None:
    text = "96.2=/local/radio/one.png\n 103.5 = https://example.org/two.png \n"
    assert parse_tuner_pictures(text) == {
        96200: "/local/radio/one.png",
        103500: "https://example.org/two.png",
    }


@pytest.mark.parametrize(
    "line",
    [
        "no separator",
        "96.2=",  # no picture
        "96.2=relative/path.png",  # neither a local path nor an http(s) URL
        "96.2=javascript:alert(1)",
        "abc=/local/x.png",
        "50.0=/local/x.png",  # not FM
        "962=/local/x.png",  # 962 MHz: kHz typed where MHz belongs
    ],
)
def test_tuner_pictures_skip_a_bad_line_not_the_list(line: str) -> None:
    assert parse_tuner_pictures(f"{line}\n88.8=/local/ok.png") == {88800: "/local/ok.png"}


def test_tuner_pictures_empty() -> None:
    assert parse_tuner_pictures(None) == {}
    assert parse_tuner_pictures("") == {}

@pytest.mark.parametrize(("raw", "expected"), [("96200", 96200), ("abc", None), ("962000", None)])
def test_who16_frequency_is_validated(raw: str, expected: int | None) -> None:
    assert frequency_khz_from_who16(raw) == expected


# ---------------------------------------------------------------------------
# MyHomeServer1 + F500, contributed by @bboykaos in OpenWebNet-HA/OWNd#103
# (tests/fixtures/traces/who22_myhomeserver1_f500_h4562). Hand-transcribed.
# ---------------------------------------------------------------------------

_MHS1_TRACES = (
    Path(__file__).parent / "fixtures" / "traces" / "who22_myhomeserver1_f500_h4562"
)


def _mhs1_tuner_frames() -> list[str]:
    raws: list[str] = []
    for trace in sorted(_MHS1_TRACES.glob("trace*.json")):
        for frame in json.loads(trace.read_text(encoding="utf-8"))["frames"]:
            if frame["raw"].startswith("*#22*5#2#"):
                raws.append(frame["raw"])
    return raws


@pytest.mark.parametrize(
    ("frame", "khz", "preset"),
    [
        ("*#22*5#2#1*11*1*1015*5##", 101500, 5),
        ("*#22*5#2#1*11*1*933*1##", 93300, 1),
        ("*#22*5#2#1*11*1*911*2##", 91100, 2),
        ("*#22*5#2#1*11*1*995*3##", 99500, 3),
    ],
)
def test_who22_frequency_in_hundred_khz_units(frame: str, khz: int, preset: int) -> None:
    """The hand-transcribed MyHomeServer1 excerpt records ``1015`` for 101.5 MHz."""
    dimension, values = _values(frame)
    assert decode_who22_tuner(dimension, values) == TunerReport(
        frequency_khz=khz, preset=preset
    )


def test_the_two_who22_units_cannot_be_confused() -> None:
    """875..1080 and 8750..10800 do not overlap, so the value picks the unit."""
    assert frequency_khz_from_who22("1015") == frequency_khz_from_who22("10150") == 101500
    assert frequency_khz_from_who22("875") == FM_MIN_KHZ
    assert frequency_khz_from_who22("1080") == FM_MAX_KHZ
    assert frequency_khz_from_who22("874") is None
    assert frequency_khz_from_who22("1081") is None
    assert frequency_khz_from_who22("8749") is None
    assert frequency_khz_from_who22("10801") is None


def test_a_short_rds_chunk_is_text_not_a_glitch() -> None:
    """4-code frames (lost middle SCS frame, k=0) and 5-code excerpts decode as text."""
    dimension, values = _values("*#22*5#2#1*10*82*65*68*73##")
    assert decode_who22_tuner(dimension, values) == TunerReport(rds="RADI")
    dimension, values = _values("*#22*5#2#1*10*82*65*68*73*79##")
    assert decode_who22_tuner(dimension, values) == TunerReport(rds="RADIO")
    assert decode_rds_frame([]) is None


def test_every_tuner_frame_of_the_myhomeserver1_trace_decodes() -> None:
    frames = _mhs1_tuner_frames()
    assert len(frames) == 6
    for raw in frames:
        dimension, values = _values(raw)
        assert decode_who22_tuner(dimension, values) is not None, raw
