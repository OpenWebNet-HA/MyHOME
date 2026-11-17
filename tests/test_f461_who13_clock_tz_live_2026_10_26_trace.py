"""Replay and verification tests for the 2026-10-26 live BTicino F461 WHO 13 clock time-zone capture.

Fixture:
  tests/fixtures/traces/f461_who13_clock_tz_live_2026-10-26/myhome_trace_F461_who13_clock_tz_live_2026-10-26.json

The capture is the one asked for in OpenWebNet-HA/OWNd#144 (comment 6426869553): an event-only
listener ran for the whole capture on the F461 2.0.11 plant (gateway clock UTC + 2 h) while a
probe sent, on its own command session, clock writes whose time-zone field is non-standard
(``0530``, ``124``), each followed by the ``*#13**0##`` / ``*#13**22##`` read-backs, then stayed
quiet through the gateway's spontaneous 10-minute clock broadcast, then restored ``999``.
Listener and probe ran the #144 head (``da77a53``); every frame was decoded again offline with
OWNd master, and every row that carries a frame stores both decodes (``ownd_*`` = the #144
head, ``ownd_master_*`` = master, ``ownd_master_error`` where master raised).

What the plant said:

- Every write (``*#13**#0*HH*MM*SS*0530##``, ``...*124##``, ``*#13**#22*HH*MM*SS*0530*W*DD*MM*YYYY##``,
  ``...*124*...##`` and the restore ``*#13**#0*HH*MM*SS*999##``) was ACKed in 9 - 18 ms with no
  reply, and nothing was echoed on the event session: not within the 3 s the probe waited, not
  anywhere in the 13-minute listener window.
- Every read-back, on the command session and echoed on the event session, kept reporting ``999``.
- The 10-minute broadcast at 20:50:00 UTC (``*#13**#1*01*26*10*2026##`` then ``*#13**#0*22*50*00*##``)
  carried an empty time-zone field while ``124`` had been the last value written.
- The restore was not echoed either (the oracle's emulated F461 echoes it; the physical one does not).
- No written time field (the shell's wall clock 1 - 2 s before the send) was in effect at the
  read-back 3 s later: every read-back followed the wall clock (deduction at 1 s resolution).

So the physical F461 never emits a ``0530`` / ``124`` time zone: OWNd master's ``ValueError`` on
such a value cannot be triggered by a frame this gateway emits. It is triggered by the WRITTEN
command strings: master raises on ``*#13**#0*22*46*45*0530##`` (``Invalid gateway timezone``) and
on the ``124`` forms (a ``timedelta`` range error), the #144 head parses them as
``OWNGatewayCommand`` with ``timezone None`` and ``raw_timezone`` kept. Both are pinned below.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from unittest.mock import patch

import pytest
from homeassistant.const import (
    CONF_FRIENDLY_NAME,
    CONF_HOST,
    CONF_MAC,
    CONF_NAME,
    CONF_PASSWORD,
    CONF_PORT,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers import issue_registry as ir
from OWNd.message import OWNCommand, OWNGatewayCommand, OWNGatewayEvent, OWNMessage
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.myhome.const import (
    CONF_DEVICE_TYPE,
    CONF_ENTITY,
    CONF_FIRMWARE,
    CONF_MANUFACTURER,
    DOMAIN,
)
from custom_components.myhome.repairs import (
    ISSUE_UNCONFIGURED_TIMEZONE,
    async_create_unconfigured_timezone_issue,
)

MAC = "00:03:50:00:10:26"
FIXTURE = (
    Path(__file__).resolve().parent
    / "fixtures"
    / "traces"
    / "f461_who13_clock_tz_live_2026-10-26"
    / "myhome_trace_F461_who13_clock_tz_live_2026-10-26.json"
)

RUN1 = "run1_baseline_reads"
RUN2 = "run2_write_dim0_tz_0530"
RUN3 = "run3_write_dim0_tz_124"
RUN4 = "run4_write_dim22_tz_0530"
RUN5 = "run5_write_dim22_tz_124"
RUN6 = "run6_clock_broadcast_listen"
RUN7 = "run7_restore_tz_999"
ALL_RUNS = (RUN1, RUN2, RUN3, RUN4, RUN5, RUN6, RUN7)

TIME_READ = "*#13**0##"
DATETIME_READ = "*#13**22##"
WRITE_0_0530 = "*#13**#0*22*46*45*0530##"
WRITE_0_124 = "*#13**#0*22*46*56*124##"
WRITE_22_0530 = "*#13**#22*22*47*06*0530*1*26*10*2026##"
WRITE_22_124 = "*#13**#22*22*47*17*124*1*26*10*2026##"
RESTORE_999 = "*#13**#0*22*59*08*999##"
NON_STANDARD_WRITES = {
    WRITE_0_0530: "0530",
    WRITE_0_124: "124",
    WRITE_22_0530: "0530",
    WRITE_22_124: "124",
}
WRITES = (WRITE_0_0530, WRITE_0_124, WRITE_22_0530, WRITE_22_124, RESTORE_999)
BROADCAST_DATE = "*#13**#1*01*26*10*2026##"
BROADCAST_TIME = "*#13**#0*22*50*00*##"
ZONE2_TEMPERATURE = "*#4*2*0*0240##"
ZONE2_HUMIDITY = "*#4*2*60*39##"
ACK = "*#*1##"
MASTER_ERROR_0530 = "ValueError: Invalid gateway timezone: '0530'"
MASTER_ERROR_124_PREFIX = "ValueError: offset must be a timedelta strictly between"


@pytest.fixture(scope="module")
def trace_payload() -> dict:
    """Load the 2026-10-26 F461 WHO 13 clock time-zone trace archive."""
    assert FIXTURE.is_file(), FIXTURE
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def rows(trace_payload: dict) -> list[dict]:
    """Return the chronological session log rows."""
    return trace_payload["rows"]


@pytest.fixture(scope="module")
def frames(trace_payload: dict) -> list[dict]:
    """Return the event-session frames."""
    return trace_payload["frames"]


def _results(rows: list[dict], run: str | None = None, frame: str | None = None) -> list[dict]:
    return [
        r
        for r in rows
        if r["dir"] == "result"
        and (run is None or r["run"] == run)
        and (frame is None or r["frame"] == frame)
    ]


def _result(rows: list[dict], run: str, frame: str) -> dict:
    found = _results(rows, run, frame)
    assert len(found) == 1, (run, frame, len(found))
    return found[0]


def _tx(rows: list[dict], run: str, frame: str) -> dict:
    found = [r for r in rows if r["dir"] == "tx" and r["run"] == run and r["frame"] == frame]
    assert len(found) == 1, (run, frame, len(found))
    return found[0]


def _evt(rows: list[dict], run: str | None = None) -> list[dict]:
    return [
        r
        for r in rows
        if (run is None or r["run"] == run) and r["dir"] == "rx" and r["session"] == "evt"
    ]


def _evt_between(rows: list[dict], t_from: float, t_to: float) -> list[dict]:
    return [r for r in _evt(rows) if t_from <= r["t"] <= t_to]


def _fields(frame: str) -> list[str]:
    """Fields of a WHO 13 frame: ['', '#13', '', '<dim>', HH, MM, SS, <tz>, ...] (the trailing ## stripped)."""
    return frame.rstrip("#").split("*")


def _tz(frame: str) -> str:
    """The time-zone field of a WHO 13 dimension 0 / 22 frame."""
    return _fields(frame)[7]


def _hms(frame: str) -> int:
    """Seconds since midnight of the HH*MM*SS field of a WHO 13 dimension 0 / 22 frame."""
    h, m, s = _fields(frame)[4:7]
    return int(h) * 3600 + int(m) * 60 + int(s)


def _utc_seconds(utc: str) -> int:
    h, m, s = utc.split(":")
    return int(h) * 3600 + int(m) * 60 + int(s)


# --------------------------------------------------------------- schema, privacy, decodes


def test_schema_runs_and_synthetic_identity(
    trace_payload: dict, rows: list[dict], frames: list[dict]
) -> None:
    """Fixture metadata, synthetic identity, run names and counts, probe segments, one monotonic time axis."""
    gw = trace_payload["gateway"]
    assert gw["model"] == "F461" and gw["firmware"] == "2.0.11"
    assert gw["ip"] == "192.0.2.92" and gw["mac"] == MAC
    assert gw["identification"] == {
        "model": "F461",
        "who13_code": "200",
        "who13_model_name": "F461",
        "who1013_code": "134",
    }
    capture = trace_payload["capture"]
    assert capture["kind"] == "derived_trace_archive"
    assert capture["window"] == {
        "first": "2026-10-26T20:46:39+00:00",
        "last": "2026-10-26T20:59:16+00:00",
        "frames": 16,
        "rows": 79,
    }
    assert len(frames) == 16 and len(rows) == 79
    assert (
        capture["listener"]["file"] == "00_listener.jsonl"
        and capture["listener"]["duration_s"] == 1500
    )
    assert "da77a53" in capture["listener"]["ownd"] and "da77a53" in capture["listener"]["probe"]
    assert [r["run"] for r in capture["runs"]] == list(ALL_RUNS)
    assert {r["run"] for r in rows} == set(ALL_RUNS)
    assert [
        (r["run"], r["utc"], r["rows"], r["frames"], r["probe_requests"]) for r in capture["runs"]
    ] == [
        (RUN1, "20:46:39-20:46:42", 10, 2, [TIME_READ, DATETIME_READ]),
        (RUN2, "20:46:47-20:46:53", 13, 2, [WRITE_0_0530, TIME_READ, DATETIME_READ]),
        (RUN3, "20:46:57-20:47:03", 13, 2, [WRITE_0_124, TIME_READ, DATETIME_READ]),
        (RUN4, "20:47:07-20:47:14", 13, 2, [WRITE_22_0530, TIME_READ, DATETIME_READ]),
        (RUN5, "20:47:18-20:47:24", 13, 2, [WRITE_22_124, TIME_READ, DATETIME_READ]),
        (RUN6, "20:50:00-20:55:44", 4, 4, []),
        (RUN7, "20:59:10-20:59:16", 13, 2, [RESTORE_999, TIME_READ, DATETIME_READ]),
    ]
    assert [r["t_offset"] for r in capture["runs"]] == [
        0.0,
        7.365,
        17.74,
        28.174,
        38.628,
        200.853,
        750.316,
    ]
    # one probe invocation per step, placed on the listener's clock through the frames both sessions received
    for run_block in capture["runs"]:
        if run_block["run"] == RUN6:
            assert "probe_segments" not in run_block, "the broadcast run is the listener alone"
            continue
        (seg,) = run_block["probe_segments"]
        assert (
            seg["probe_wait_s"] == 3.0
            and seg["alignment_frames"] == 2
            and seg["alignment_spread_s"] <= 0.001
        )
        assert seg["t_first_request"] == run_block["t_offset"]
    assert [s["probe_segments"][0]["file"] for s in capture["runs"] if s["run"] != RUN6] == [
        "01_baseline.jsonl", "02_write_0530.jsonl", "03_write_124.jsonl", "04_write_22_0530.jsonl",
        "04b_write_22_124.jsonl", "05_restore_999.jsonl",
    ]  # fmt: skip
    # which decoder is which
    assert set(capture["decoders"]) == {
        "ownd_class / ownd_log / ownd_timezone / ownd_raw_timezone",
        "ownd_master_class / ownd_master_log, or ownd_master_error",
    }
    ts = [f["timestamp"] for f in frames]
    assert ts == sorted(ts), "one monotonic time axis across the runs"
    assert all(a["t"] <= b["t"] for a, b in zip(rows, rows[1:], strict=False))
    assert all(f["direction"] == "rx" and f["session"] == "evt" for f in frames)
    assert [r["frame"] for r in rows if r["dir"] == "rx" and r["session"] == "evt"] == [
        f["raw"] for f in frames
    ]
    assert [(r["t"], r["run"]) for r in _evt(rows)] == [(f["timestamp"], f["run"]) for f in frames]
    for r in rows:
        if r["dir"] == "rx" and r["session"] == "cmd":
            assert r["t_estimated"] is True, r
        else:
            assert "t_estimated" not in r, r
    # privacy: the only MAC-like string is the synthetic one, no LAN address, no local path
    text = json.dumps(trace_payload)
    assert set(re.findall(r"(?:[0-9A-Fa-f]{2}:){5}[0-9A-Fa-f]{2}", text)) == {MAC}
    assert not re.search(r"192[.*]168", text) and "/Users/" not in text and "\\\\" not in text, (
        "no LAN address, no local path"
    )
    assert "*#13**12*" not in text and "*#13**10*" not in text, (
        "no WHO 13 MAC / IP dimension was exchanged"
    )
    # WHO 13 is the capture; the thermostat's two spontaneous reports during the broadcast wait are kept as captured
    assert {re.match(r"^\*#?(\d+)\*", f["raw"]).group(1) for f in frames} == {"13", "4"}
    assert [f["raw"] for f in frames if not f["raw"].startswith("*#13*")] == [
        ZONE2_TEMPERATURE,
        ZONE2_HUMIDITY,
    ]


def test_every_row_carries_both_decodes_and_they_agree_on_everything_the_f461_emitted(
    rows: list[dict],
) -> None:
    """Both OWNd decodes are stored on every row with a frame; they differ only on the written non-standard strings."""
    for r in rows:
        if r["frame"] == ACK or r["dir"] == "result":
            # the ACK has nothing to decode; a result row describes the command session's outcome,
            # its tx row carries the decodes of the request
            assert not any(k.startswith("ownd") for k in r), r
            continue
        assert r["ownd_class"] and r["ownd_log"], r
        if r["dir"] == "tx" and r["frame"] in NON_STANDARD_WRITES:
            assert "ownd_master_class" not in r and "ownd_master_log" not in r
            if NON_STANDARD_WRITES[r["frame"]] == "0530":
                assert r["ownd_master_error"] == MASTER_ERROR_0530
            else:
                assert r["ownd_master_error"].startswith(MASTER_ERROR_124_PREFIX)
            assert r["ownd_class"] == "OWNGatewayCommand"
            assert (
                r["ownd_timezone"] is None
                and r["ownd_raw_timezone"] == NON_STANDARD_WRITES[r["frame"]]
            )
        else:
            assert "ownd_master_error" not in r, r
            assert (r["ownd_master_class"], r["ownd_master_log"]) == (
                r["ownd_class"],
                r["ownd_log"],
            ), r
    # everything the F461 emitted decodes identically: an OWNGatewayEvent with time zone 999 for each reply,
    # an OWNGatewayCommand with an empty time-zone field for the broadcast
    for r in _evt(rows):
        if r["frame"].startswith(("*#13**0*", "*#13**22*")):
            assert (
                r["ownd_class"] == "OWNGatewayEvent"
                and r["ownd_raw_timezone"] == "999"
                and r["ownd_timezone"] == ""
            )
        elif r["frame"] == BROADCAST_TIME:
            assert (
                r["ownd_class"] == "OWNGatewayCommand"
                and r["ownd_raw_timezone"] is None
                and r["ownd_timezone"] == ""
            )
            assert r["ownd_log"] == "Gateway broadcasting internal time: 22:50:00."
        elif r["frame"] == BROADCAST_DATE:
            assert (
                r["ownd_class"] == "OWNGatewayCommand"
                and r["ownd_log"] == "Gateway broadcasting internal date: 2026-10-26."
            )
        else:
            assert r["ownd_class"] == "OWNHeatingEvent", r
    # the restore string parses on both: 999 is a standard value
    restore = _tx(rows, RUN7, RESTORE_999)
    assert restore["ownd_class"] == restore["ownd_master_class"] == "OWNGatewayCommand"
    assert restore["ownd_raw_timezone"] == "999" and restore["ownd_timezone"] == ""
    assert restore["ownd_log"] == "Gateway broadcasting internal time: 22:59:08."


def test_written_strings_fail_before_pass_after(rows: list[dict]) -> None:
    """The non-standard writes: OWNd master raises (fail before), the #144 head keeps raw_timezone (pass after).

    Whichever OWNd is on the path, the outcome must be one of the two the archive recorded.
    """
    for frame, raw_tz in NON_STANDARD_WRITES.items():
        recorded = next(r for r in rows if r["dir"] == "tx" and r["frame"] == frame)
        try:
            cmd = OWNCommand.parse(frame)
        except ValueError as exc:
            # OWNd master / the 2.0.0b11 pin: the ValueError the archive recorded, raised at parse time
            assert f"ValueError: {exc}" == recorded["ownd_master_error"], (frame, exc)
            with pytest.raises(ValueError):
                OWNMessage.parse(frame)
            continue
        assert isinstance(cmd, OWNGatewayCommand), (frame, cmd)
        assert cmd.timezone is None and cmd.raw_timezone == raw_tz, (
            frame,
            cmd.timezone,
            cmd.raw_timezone,
        )
        assert cmd.human_readable_log == recorded["ownd_log"]
    # the standard value parses the same way everywhere
    restore = OWNCommand.parse(RESTORE_999)
    assert isinstance(restore, OWNGatewayCommand) and restore.raw_timezone == "999"
    # the broadcast with an empty time-zone field is parsed by both as a command (the integration only logs it)
    broadcast = OWNMessage.parse(BROADCAST_TIME)
    assert isinstance(broadcast, OWNGatewayCommand) and broadcast.raw_timezone is None
    assert broadcast.human_readable_log == "Gateway broadcasting internal time: 22:50:00."


def test_every_frame_the_f461_emitted_parses_in_ownd(rows: list[dict], frames: list[dict]) -> None:
    """Every rx frame parses in the OWNd on the path; every read request parses as a status request."""
    for item in frames:
        assert OWNMessage.parse(item["raw"]) is not None, (
            f"Failed to parse evt frame: {item['raw']}"
        )
    for r in rows:
        if r["dir"] == "tx" and r["frame"] in (TIME_READ, DATETIME_READ):
            cmd = OWNCommand.parse(r["frame"])
            assert isinstance(cmd, OWNGatewayCommand) and cmd.is_dimension_request is True
            assert r["is_status_request"] is True, "the probe sent the reads as status requests"
        elif r["dir"] == "tx":
            assert r["is_status_request"] is False, r
        elif r["dir"] == "rx" and r["frame"] != ACK:
            msg = OWNMessage.parse(r["frame"])
            assert msg is not None, r
            if r["frame"].startswith(("*#13**0*", "*#13**22*")):
                assert isinstance(msg, OWNGatewayEvent) and msg.dimension_value[3] == "999"


# --------------------------------------------------------------- row consistency


def test_result_rows_are_consistent_with_their_tx_and_cmd_rx_rows(rows: list[dict]) -> None:
    """Each result row follows its tx row; replies sit at the request; ACK is logged; events_after is the pending window."""
    assert all(r["ack"] is True for r in _results(rows)), "every request of this capture was ACKed"
    assert [r["frame"] for r in _results(rows)] == [
        TIME_READ, DATETIME_READ,
        WRITE_0_0530, TIME_READ, DATETIME_READ,
        WRITE_0_124, TIME_READ, DATETIME_READ,
        WRITE_22_0530, TIME_READ, DATETIME_READ,
        WRITE_22_124, TIME_READ, DATETIME_READ,
        RESTORE_999, TIME_READ, DATETIME_READ,
    ]  # fmt: skip
    for run in ALL_RUNS:
        run_rows = [r for r in rows if r["run"] == run]
        for i, r in enumerate(run_rows):
            if r["dir"] != "result":
                continue
            tx_i = next(j for j in range(i - 1, -1, -1) if run_rows[j]["dir"] == "tx")
            tx = run_rows[tx_i]
            assert tx["frame"] == r["frame"] and tx["step"] == r["step"] and tx["t"] <= r["t"]
            assert abs((r["t"] - tx["t"]) - r["reply_time_s"]) < 0.002
            cmd_rx = [
                x for x in run_rows[tx_i + 1 : i] if x["dir"] == "rx" and x["session"] == "cmd"
            ]
            assert [x["frame"] for x in cmd_rx] == [*r["replies"], ACK]
            assert all(x["t"] == tx["t"] for x in cmd_rx[:-1]) and cmd_rx[-1]["t"] == r["t"]
            pending = [
                x["frame"]
                for x in run_rows[tx_i + 1 : i]
                if x["dir"] == "rx" and x["session"] == "evt" and x["t"] <= r["t"]
            ]
            assert r["events_after"] == pending
            assert r["wait_s"] == 3.0
            # a read is delivered on both sessions; a write has no reply and no echo
            if r["frame"] in WRITES:
                assert r["replies"] == [] and pending == [] and r["events_within_wait"] == [], r
            else:
                assert r["replies"] == pending == r["events_within_wait"], r
                # the reply reached the listener before the ACK closed the command-session batch
                (evt_row,) = [
                    y for y in run_rows[tx_i + 1 : i] if y["dir"] == "rx" and y["session"] == "evt"
                ]
                assert tx["t"] <= evt_row["t"] <= r["t"]
    # the probe paused 3 s after each request: the next request of a run follows the ACK by 3.0 s
    for run in ALL_RUNS:
        reqs = [r for r in rows if r["run"] == run and r["dir"] == "tx"]
        res = _results(rows, run)
        for prev_res, nxt in zip(res, reqs[1:], strict=False):
            assert 3.0 <= nxt["t"] - prev_res["t"] <= 3.01, (run, prev_res["frame"], nxt["frame"])


# --------------------------------------------------------------- what the F461 did


@pytest.mark.parametrize(
    ("run", "frame", "ack_max_s"),
    [
        (RUN2, WRITE_0_0530, 0.010),
        (RUN3, WRITE_0_124, 0.009),
        (RUN4, WRITE_22_0530, 0.018),
        (RUN5, WRITE_22_124, 0.012),
        (RUN7, RESTORE_999, 0.012),
    ],
)
def test_each_clock_write_is_acked_with_no_reply_and_no_echo(
    rows: list[dict], run: str, frame: str, ack_max_s: float
) -> None:
    """A clock write with a non-standard (or the standard 999) time zone: ACK within 18 ms, no reply, no echo."""
    res = _result(rows, run, frame)
    assert res["ack"] is True and res["replies"] == []
    assert 0.009 <= res["reply_time_s"] <= ack_max_s
    assert res["events_after"] == [] and res["events_within_wait"] == []
    tx = _tx(rows, run, frame)
    # nothing reached the event session between the write and its first read-back (3 s later)
    nxt = _tx(rows, run, TIME_READ)
    assert _evt_between(rows, tx["t"], nxt["t"] - 0.001) == []
    # and nothing that looks like an echo of the write anywhere in the listener window
    echoes = [
        r["frame"]
        for r in _evt(rows)
        if r["frame"].startswith(("*#13**#0*", "*#13**#22*", "*#13**#1*"))
    ]
    assert echoes == [BROADCAST_DATE, BROADCAST_TIME], (
        "the only write-form WHO 13 frames the F461 emitted are its 10-minute broadcast"
    )
    assert not any(fld in ("0530", "124") for r in _evt(rows) for fld in _fields(r["frame"])), (
        "no event-session frame carries 0530 or 124 in any field"
    )


@pytest.mark.parametrize("run", [RUN1, RUN2, RUN3, RUN4, RUN5, RUN7])
def test_read_backs_keep_reporting_999_on_both_sessions(rows: list[dict], run: str) -> None:
    """*#13**0## and *#13**22## answer the time zone 999 before, after every write and after the restore."""
    for req, prefix, n_fields in ((TIME_READ, "*#13**0*", 8), (DATETIME_READ, "*#13**22*", 12)):
        res = _result(rows, run, req)
        assert res["ack"] is True and 0.009 <= res["reply_time_s"] <= 0.059
        (reply,) = res["replies"]
        fields = _fields(reply)
        assert reply.startswith(prefix) and len(fields) == n_fields and fields[7] == "999", reply
        # the reply is delivered on the event session too (as in f461_live_2026-10-23)
        assert res["events_after"] == [reply]
        echo = [r for r in _evt(rows, run) if r["frame"] == reply]
        assert len(echo) == 1 and echo[0]["ownd_class"] == "OWNGatewayEvent"
        if req == DATETIME_READ:
            assert fields[8:12] == ["01", "26", "10", "2026"], "Monday 2026-10-26"
    assert _results(rows, run, TIME_READ)[0]["note"].endswith(
        ("999", "also_on_evt", "after_restore")
    )


def test_dim22_writes_were_not_stored_either(rows: list[dict]) -> None:
    """The dimension 22 forms (date-time + time zone) are ACKed like dimension 0 and change nothing."""
    for run, frame in ((RUN4, WRITE_22_0530), (RUN5, WRITE_22_124)):
        assert _result(rows, run, frame)["note"].endswith("acked_no_reply_no_echo_on_evt")
        for req in (TIME_READ, DATETIME_READ):
            assert _tz(_result(rows, run, req)["replies"][0]) == "999"


def test_no_written_time_was_in_effect_at_the_read_back(rows: list[dict]) -> None:
    """Deduction at 1 s resolution: the gateway clock kept following the wall clock after every write.

    Each write carried the shell's wall clock as sampled 1 - 2 s before the send (HH*MM*SS, local =
    UTC + 2 h). Had the F461 applied it, the read-back 3.0 s later would lag the wall clock by that
    1 - 2 s; instead every read-back equals the wall clock of its own request (UTC + 2 h).
    """
    for r in _results(rows):
        if r["frame"] in WRITES:
            continue
        (reply,) = r["replies"]
        wall_local = _utc_seconds(r["utc"]) + 2 * 3600
        assert abs(_hms(reply) - wall_local) <= 1, (r["frame"], reply, r["utc"])
    for run, frame in (
        (RUN2, WRITE_0_0530),
        (RUN3, WRITE_0_124),
        (RUN4, WRITE_22_0530),
        (RUN5, WRITE_22_124),
        (RUN7, RESTORE_999),
    ):
        tx = _tx(rows, run, frame)
        written = _hms(frame)
        readback = _result(rows, run, TIME_READ)
        elapsed = readback["t"] - tx["t"]
        assert 3.0 <= elapsed <= 3.1
        # the read-back is 4 - 5 s past the written time, not the 3 s it would be had the time been applied
        assert _hms(readback["replies"][0]) - written >= elapsed + 1 - 0.1, (
            frame,
            readback["replies"][0],
        )


def test_ten_minute_broadcast_carried_no_stored_time_zone(
    rows: list[dict], frames: list[dict]
) -> None:
    """20:50:00 UTC, 2.6 min after the last 124 write: date then time 2 ms later, empty time-zone field."""
    run6 = _evt(rows, RUN6)
    assert [r["frame"] for r in run6] == [
        BROADCAST_DATE,
        BROADCAST_TIME,
        ZONE2_TEMPERATURE,
        ZONE2_HUMIDITY,
    ]
    assert all(r["step"] == "listen" for r in run6)
    date_row, time_row = run6[0], run6[1]
    assert date_row["utc"] == time_row["utc"] == "20:50:00"
    assert 0.0 <= time_row["t"] - date_row["t"] <= 0.003
    assert _tz(time_row["frame"]) == "", (
        "the time-zone field of the broadcast is empty, not 0530 / 124"
    )
    last_write = _tx(rows, RUN5, WRITE_22_124)
    assert 150 < date_row["t"] - last_write["t"] < 170
    assert _fields(time_row["frame"])[4:7] == ["22", "50", "00"], "local time, UTC + 2 h"
    # the same form as the broadcasts of f461_live_2026-10-23 (*#13**#0*HH*MM*00*##)
    assert re.fullmatch(r"\*#13\*\*#0\*\d\d\*\d0\*00\*##", time_row["frame"])
    assert all(
        r["ownd_class"] == r["ownd_master_class"] == "OWNGatewayCommand"
        for r in (date_row, time_row)
    )
    assert [f["run"] for f in frames if f["raw"] in (BROADCAST_DATE, BROADCAST_TIME)] == [
        RUN6,
        RUN6,
    ]


def test_restore_999_is_acked_not_echoed_and_read_back_as_999(rows: list[dict]) -> None:
    """The restore: ACK 12 ms, no reply, no echo (the oracle's emulated F461 echoes it, the physical one did not)."""
    res = _result(rows, RUN7, RESTORE_999)
    assert res["ack"] is True and res["reply_time_s"] == 0.012 and res["replies"] == []
    assert (
        res["events_after"] == []
        and res["events_within_wait"] == []
        and res["note"] == "restore_set_time_tz_999"
    )
    assert res["utc"] == "20:59:10"
    assert not any(
        r["frame"].endswith("*999##") and r["frame"].startswith("*#13**#0*") for r in _evt(rows)
    )
    assert _result(rows, RUN7, TIME_READ)["replies"] == ["*#13**0*22*59*13*999##"]
    assert _result(rows, RUN7, DATETIME_READ)["replies"] == [
        "*#13**22*22*59*16*999*01*26*10*2026##"
    ]
    assert _result(rows, RUN7, TIME_READ)["note"] == "readback_after_restore"
    # the restore came 11.7 min after the last read-back: the listener saw only the broadcast pair and the
    # thermostat's two reports in between
    before = _result(rows, RUN5, DATETIME_READ)
    between = _evt_between(rows, before["t"] + 0.001, _tx(rows, RUN7, RESTORE_999)["t"] - 0.001)
    assert [r["frame"] for r in between] == [
        BROADCAST_DATE,
        BROADCAST_TIME,
        ZONE2_TEMPERATURE,
        ZONE2_HUMIDITY,
    ]


# --------------------------------------------------------------- replay through the integration


async def _setup_f461(hass: HomeAssistant) -> tuple[MockConfigEntry, object]:
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={
            CONF_HOST: "192.0.2.92",
            CONF_PORT: 20000,
            CONF_PASSWORD: "pass",
            CONF_MAC: MAC,
            CONF_NAME: "F461",
            CONF_DEVICE_TYPE: "urn:schemas-bticino-it:device:lightingcontrolunit:1",
            CONF_FRIENDLY_NAME: "F461 clock tz live 2026-10-26",
            CONF_MANUFACTURER: "BTicino S.p.A.",
            CONF_FIRMWARE: "2.0.11",
        },
        unique_id=MAC,
    )
    entry.add_to_hass(hass)
    with (
        patch(
            "custom_components.myhome.gateway.OWNSession.test_connection",
            return_value={"Success": True, "Message": None},
        ),
        patch("custom_components.myhome.gateway.MyHOMEGatewayHandler.listening_loop"),
        patch("custom_components.myhome.gateway.MyHOMEGatewayHandler.sending_loop"),
    ):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
    handler = hass.data[DOMAIN][MAC][CONF_ENTITY]
    handler._on_event_connection_state_change(True)
    return entry, handler


@pytest.mark.asyncio
async def test_replay_event_session_through_the_gateway_handler(
    hass: HomeAssistant, rows: list[dict], frames: list[dict]
) -> None:
    """Every event-session frame goes through the real dispatcher; the 999 read-backs clear the time-zone repair.

    The integration's only WHO 13 clock handling is ``_handle_gateway_diagnostics``: a dimension 0 / 22
    event whose fourth value is ``999`` deletes the "unconfigured time zone" repair issue on an F461
    (the F461 never has one configured), the broadcast (an ``OWNGatewayCommand``) is only logged.
    The frames go through ``GatewayEventDispatcher.process_message``, what the listening loop calls.
    """
    entry, handler = await _setup_f461(hass)
    issue_id = f"{ISSUE_UNCONFIGURED_TIMEZONE}_{entry.entry_id}"
    registry = ir.async_get(hass)
    try:
        for item in frames:
            msg = OWNMessage.parse(item["raw"])
            assert msg is not None, item
            # an issue raised for this gateway is withdrawn by each 999 read-back and left alone by anything else
            async_create_unconfigured_timezone_issue(hass, entry.entry_id, "F461")
            assert registry.async_get_issue(DOMAIN, issue_id) is not None
            await handler._event_dispatcher.process_message(msg)
            await hass.async_block_till_done()
            if isinstance(msg, OWNGatewayEvent):
                assert msg.dimension in (0, 22) and msg.dimension_value[3] == "999"
                assert registry.async_get_issue(DOMAIN, issue_id) is None, item
            else:
                assert registry.async_get_issue(DOMAIN, issue_id) is not None, item
                ir.async_delete_issue(hass, DOMAIN, issue_id)
        # the recorded decodes are the classes the replay produced
        assert [r["ownd_class"] for r in _evt(rows)] == [
            type(OWNMessage.parse(f["raw"])).__name__ for f in frames
        ]
    finally:
        await hass.config_entries.async_unload(entry.entry_id)


@pytest.mark.asyncio
async def test_replay_written_strings_never_reach_the_dispatcher_as_events(
    hass: HomeAssistant, rows: list[dict]
) -> None:
    """The written command strings: on master they raise at parse (before any dispatch); on the #144 head they
    are commands, which the dispatcher logs and never treats as a clock report."""
    entry, handler = await _setup_f461(hass)
    issue_id = f"{ISSUE_UNCONFIGURED_TIMEZONE}_{entry.entry_id}"
    registry = ir.async_get(hass)
    try:
        for frame in NON_STANDARD_WRITES:
            try:
                msg = OWNMessage.parse(frame)
            except ValueError:
                # master: the ValueError belongs to the sender's parse of its own string; an event session
                # of this F461 never sees such a frame (test_each_clock_write_is_acked_with_no_reply_and_no_echo)
                continue
            assert isinstance(msg, OWNGatewayCommand)
            async_create_unconfigured_timezone_issue(hass, entry.entry_id, "F461")
            await handler._event_dispatcher.process_message(msg)
            await hass.async_block_till_done()
            assert registry.async_get_issue(DOMAIN, issue_id) is not None, (
                "a command is only logged"
            )
            ir.async_delete_issue(hass, DOMAIN, issue_id)
        assert all(
            r["dir"] in ("tx", "result") for r in rows if r["frame"] in NON_STANDARD_WRITES
        ), "the non-standard strings exist only as requests"
    finally:
        await hass.config_entries.async_unload(entry.entry_id)
