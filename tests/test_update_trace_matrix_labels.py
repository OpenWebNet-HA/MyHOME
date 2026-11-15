"""The trace matrix attributes Home Assistant debug-log frames to the gateway named in the log label."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture
def trace_matrix():
    spec = importlib.util.spec_from_file_location(
        "update_trace_matrix", REPO_ROOT / "scripts" / "update_trace_matrix.py"
    )
    module = importlib.util.module_from_spec(spec)
    sys.modules["update_trace_matrix"] = module
    spec.loader.exec_module(module)
    return module


def test_canonical_gateway_maps_slashed_and_alias_labels(trace_matrix) -> None:
    assert trace_matrix.canonical_gateway("H/L4684") == "H4684"
    assert trace_matrix.canonical_gateway("HL4684") == "H4684"
    assert trace_matrix.canonical_gateway("L4684") == "H4684"
    assert trace_matrix.canonical_gateway("AM4890") == "H4890"
    assert trace_matrix.canonical_gateway("F459") == "F459"
    assert trace_matrix.canonical_gateway("MyHomeServer1") == "MyHomeServer1"


def test_debug_log_frames_are_attributed_to_the_labelled_gateway(
    trace_matrix, tmp_path, monkeypatch
) -> None:
    log = tmp_path / "traces" / "gw_full_debug_log.txt"
    log.parent.mkdir(parents=True)
    log.write_text(
        "\n".join(
            [
                "2026-11-05 18:21:24.924+02:00 DEBUG (MainThread) [custom_components.myhome] "
                "[H/L4684 gateway - 192.0.2.1] Message received: `*1*1*12##`",
                "2026-11-05 18:21:25.000+02:00 DEBUG (MainThread) [custom_components.myhome] "
                "[H/L4684 gateway - 192.0.2.1] Collected command response `*#4*2*0*0215##`.",
                "2026-11-05 18:21:26.000+02:00 DEBUG (MainThread) [custom_components.myhome] "
                "[F459 gateway - 192.0.2.59] Message received: `*2*0*0711##`",
                "2026-11-05 18:21:27.000+02:00 DEBUG (MainThread) [custom_components.myhome] "
                "[Generic gateway - 192.0.2.9] Message received: `*#13**15*200##`",
                "2026-11-05 18:21:28.000+02:00 DEBUG (MainThread) [custom_components.myhome] "
                "[F459 gateway - 192.0.2.59] Message `*#1*0##` was successfully queued.",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(trace_matrix, "FIXTURES_DIR", tmp_path)
    matrix = trace_matrix.build_matrix()
    rows = {line.split("|")[1].strip(): line for line in matrix.splitlines()[2:]}
    assert set(rows) == {"**H4684 / H/L4684**", "**F459**"}
    header = matrix.splitlines()[0]
    columns = [cell.strip() for cell in header.split("|")[2:-1]]
    assert columns == ["WHO 1<br>Lights", "WHO 2<br>Autom.", "WHO 4<br>Climate"]
    h4684 = [cell.strip() for cell in rows["**H4684 / H/L4684**"].split("|")[2:-1]]
    f459 = [cell.strip() for cell in rows["**F459**"].split("|")[2:-1]]
    assert h4684 == ["✅", "", "✅"]
    assert f459 == ["", "✅", ""]


def test_trace_matrix_excludes_tx_probes_and_oracle_fixtures(
    trace_matrix, tmp_path, monkeypatch
) -> None:
    """Outbound tx sweep probes and synthetic oracle fixtures must never appear as physical traces."""
    import json

    traces = tmp_path / "traces"
    traces.mkdir(parents=True)

    # F461 diagnostic export with rx on WHO 1/4 and NACKed tx sweep probes on WHO 5/16/18
    (traces / "f461_diag.json").write_text(
        json.dumps(
            {
                "data": {
                    "gateway": {"model_name": "F461"},
                    "bus_monitor": {
                        "recent_frames": [
                            {"direction": "rx", "raw": "*1*1*11##"},
                            {"direction": "rx", "raw": "*#4*1*0*0215##"},
                            {"direction": "tx", "raw": "*#5*0##"},
                            {"direction": "tx", "raw": "*#16*0##"},
                            {"direction": "tx", "raw": "*#18*0##"},
                        ]
                    },
                }
            }
        ),
        encoding="utf-8",
    )

    # Synthetic firmware-oracle fixture on F453AV must also be excluded
    (traces / "myhome_trace_oracle_F453AV_synth.json").write_text(
        json.dumps(
            {
                "synthetic": True,
                "gateway": {"model": "F453AV"},
                "frames": [{"direction": "rx", "raw": "*16*3*01##"}],
            }
        ),
        encoding="utf-8",
    )

    monkeypatch.setattr(trace_matrix, "FIXTURES_DIR", tmp_path)
    matrix = trace_matrix.build_matrix()
    rows = {line.split("|")[1].strip(): line for line in matrix.splitlines()[2:]}
    assert set(rows) == {"**F461**"}
    header = matrix.splitlines()[0]
    columns = [cell.strip() for cell in header.split("|")[2:-1]]
    assert columns == ["WHO 1<br>Lights", "WHO 4<br>Climate"]
    f461_cells = [cell.strip() for cell in rows["**F461**"].split("|")[2:-1]]
    assert f461_cells == ["✅", "✅"]


def test_who_labels_canonical_aux_and_cen_plus(trace_matrix, tmp_path, monkeypatch) -> None:
    """WHO 9 and WHO 25 short labels must be canonical 'Aux' and 'CEN+', never 'Power' or 'Diag'."""
    import json

    assert trace_matrix.WHO_LABELS[9] == "Aux"
    assert trace_matrix.WHO_LABELS[25] == "CEN+"
    trace_matrix.validate_who_labels()

    with pytest.raises(ValueError, match="WHO 9"):
        trace_matrix.validate_who_labels({**trace_matrix.WHO_LABELS, 9: "Power"})
    with pytest.raises(ValueError, match="WHO 25"):
        trace_matrix.validate_who_labels({**trace_matrix.WHO_LABELS, 25: "Diag"})

    traces = tmp_path / "traces"
    traces.mkdir(parents=True)
    (traces / "mh200n_diag.json").write_text(
        json.dumps(
            {
                "data": {
                    "gateway": {"model_name": "MH200N"},
                    "bus_monitor": {
                        "recent_frames": [
                            {"direction": "rx", "raw": "*9*1*1##"},
                            {"direction": "rx", "raw": "*25*21#1*21##"},
                        ]
                    },
                }
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(trace_matrix, "FIXTURES_DIR", tmp_path)
    matrix = trace_matrix.build_matrix()
    header_cols = [cell.strip() for cell in matrix.splitlines()[0].split("|")[2:-1]]
    assert header_cols == ["WHO 9<br>Aux", "WHO 25<br>CEN+"]

