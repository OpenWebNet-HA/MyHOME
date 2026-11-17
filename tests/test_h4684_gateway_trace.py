"""Tests for H4684 and LGRH4684 Gateway Identification Firmware Oracle Trace Replay.

Verifies deterministic replay of gateway model identification frames captured
from live QEMU execution of Legrand / BTicino bt_device daemon (firmware 2.0.54,
TiDisplayColorIP build 0x02003194, own-firmware-oracle PR #34) and binary
disassembly (own-firmware-oracle PR #26, OWNd PR #96):
1. WHO 13 Dimension 15, 16, 23, 24 resolution:
   - Code 13 (BTicino 2006 table & bt_device) -> H4684
   - Code 20 (bt_device Legrand brand) -> WHO 13 unmapped, settled by WHO 1013
   - Firmware 2.0.54 (*#13**16*2*0*54##), Kernel 2.0.5 (*#13**23*2*0*5##),
     Distribution 3.0.0 (*#13**24*3*0*0##)
2. WHO 1013 Dimension 1 diagnostic object model resolution:
   - Code 19 (*#1013**1*19## in QEMU & *#1013**1*19*15*5*0##) -> H4684
   - Code 26 (*#1013**1*26*15*5*0##) -> LGRH4684
3. Profile binding:
   - Both H4684 and LGRH4684 bind to H4684Profile.
4. Zero identity conflicts and zero device health faults across replay cycles.
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

from OWNd.message import OWNEvent
from OWNd.profiles import get_gateway_profile

try:
    from OWNd.profiles import H4684Profile
except ImportError:
    H4684Profile = None  # type: ignore[assignment, misc]

from custom_components.myhome.gateway import MyHOMEGatewayHandler

TRACES_DIR = Path(__file__).resolve().parent / "fixtures" / "traces" / "oracle_h4684_gateway"
TRACE_FILE = TRACES_DIR / "myhome_trace_oracle_h4684_gateway_identification.json"


def _create_mock_handler(data_overrides: dict | None = None) -> MyHOMEGatewayHandler:
    """Create a mock MyHOMEGatewayHandler for identification trace replay."""
    entry = MagicMock()
    entry.entry_id = "test_h4684_entry"
    entry.title = "H4684 Gateway"
    entry.data = {
        "host": "192.168.1.50",
        "port": 20000,
        "name": "H4684",
        "model_source": "auto",
        "ssdp_location": None,
        "UDN": None,
    }
    entry.data.update(data_overrides or {})
    hass = MagicMock()
    hass.data = {}
    h = MyHOMEGatewayHandler(hass, entry)
    h.device_registry_id = "dev_h4684"
    return h


def test_h4684_trace_fixture_integrity():
    """Verify schema integrity of the H4684 firmware oracle trace fixture."""
    assert TRACE_FILE.is_file(), f"Trace fixture missing: {TRACE_FILE}"
    with open(TRACE_FILE, "r", encoding="utf-8") as f:
        trace_data = json.load(f)

    assert trace_data["gateway"]["model"] == "H4684"
    assert trace_data["gateway"]["firmware"] == "2.0.54"
    frames = trace_data["frames"]
    assert len(frames) == 13

    raw_frames = [f["raw"] for f in frames]
    assert "*#13**15*13##" in raw_frames
    assert "*#13**16*2*0*54##" in raw_frames
    assert "*#13**23*2*0*5##" in raw_frames
    assert "*#13**24*3*0*0##" in raw_frames
    assert "*#1013**1*19##" in raw_frames
    assert "*#1013**1*19*15*5*0##" in raw_frames
    assert "*#13**15*20##" in raw_frames
    assert "*#1013**1*26*15*5*0##" in raw_frames


def test_h4684_qemu_empirical_identification_trace_replay():
    """Replay empirical QEMU gateway identification sequence for BTicino H4684."""
    dev_reg = MagicMock()
    dev_reg.async_get.return_value = MagicMock(model="H4684")

    with patch("homeassistant.helpers.device_registry.async_get", return_value=dev_reg), \
         patch("custom_components.myhome.gateway.async_create_identity_issue") as create_issue, \
         patch("custom_components.myhome.gateway.async_create_identity_corrected_issue") as corrected_issue:
        h = _create_mock_handler({"name": "H4684"})

        # Step 1: Gateway reports device type code 13 on WHO 13
        h._handle_gateway_diagnostics(OWNEvent.parse("*#13**15*13##"))
        assert h._who13["code"] == "13"
        assert h._who13["model"] == "H4684"

        # Step 2: Gateway reports firmware 2.0.54, kernel 2.0.5, distribution 3.0.0
        h._handle_gateway_diagnostics(OWNEvent.parse("*#13**16*2*0*54##"))
        h._handle_gateway_diagnostics(OWNEvent.parse("*#13**23*2*0*5##"))
        h._handle_gateway_diagnostics(OWNEvent.parse("*#13**24*3*0*0##"))
        assert h._who13["firmware"] == "2.0.54"
        assert h._who13["kernel"] == "2.0.5"
        assert h._who13["distribution"] == "3.0.0"

        # Step 3: Gateway reports single-value WHO 1013 diagnostic object model 19 (live QEMU verdict)
        h._handle_gateway_identity_diagnostics(OWNEvent.parse("*#1013**1*19##"))
        assert h._who1013["code"] == "19"
        assert h._who1013["model"] == "H4684"
        assert h._who1013["n_conf"] is None
        assert h._who1013["brand"] is None
        assert h._who1013["line"] is None

        # Step 4: Verify 4-value WHO 1013 response populates optional metadata fields
        h._handle_gateway_identity_diagnostics(OWNEvent.parse("*#1013**1*19*15*5*0##"))
        assert h._who1013["code"] == "19"
        assert h._who1013["model"] == "H4684"
        assert h._who1013["n_conf"] == "15"
        assert h._who1013["brand"] == "5"
        assert h._who1013["line"] == "0"

        # Step 5: Verify identification dictionary
        ident = h.identification()
        assert ident["model"] == "H4684"
        assert ident["who13_code"] == "13"
        assert ident["who13_firmware"] == "2.0.54"
        assert ident["who13_kernel"] == "2.0.5"
        assert ident["who13_distribution"] == "3.0.0"
        assert ident["who1013_code"] == "19"
        assert ident["who1013_n_conf"] == "15"
        assert ident["who1013_brand"] == "5 (Legrand BTicino)"
        assert ident["who1013_line"] == "0 (Undefined)"
        assert ident["conflict"] is None
        if H4684Profile is not None:
            assert ident["profile"] == "H4684Profile"
            profile = get_gateway_profile(ident["model"])
            assert isinstance(profile, H4684Profile)
            assert profile.model_name == "H4684"
        else:
            assert ident["profile"] in ("H4684Profile", "GenericGatewayProfile")

        # Step 6: Zero faults and zero repair issues
        assert h.device_health.faults == []
        create_issue.assert_not_called()
        corrected_issue.assert_not_called()


def test_lgrh4684_synthetic_identification_trace_replay():
    """Replay complete gateway identification sequence for Legrand LGRH4684."""
    dev_reg = MagicMock()
    dev_reg.async_get.return_value = MagicMock(model="LGRH4684")

    with patch("homeassistant.helpers.device_registry.async_get", return_value=dev_reg), \
         patch("custom_components.myhome.gateway.async_create_identity_issue") as create_issue, \
         patch("custom_components.myhome.gateway.async_create_identity_corrected_issue") as corrected_issue:
        h = _create_mock_handler({"name": "LGRH4684"})

        # Step 1: Gateway reports device type code 20 on WHO 13 (unmapped in WHO 13 tables)
        h._handle_gateway_diagnostics(OWNEvent.parse("*#13**15*20##"))
        assert h._who13["code"] == "20"
        assert h._who13["model"] is None

        # Step 2: Gateway reports WHO 1013 diagnostic object model 26 (settles model)
        h._handle_gateway_identity_diagnostics(OWNEvent.parse("*#1013**1*26*15*5*0##"))
        assert h._who1013["code"] == "26"
        assert h._who1013["model"] == "LGRH4684"

        # Step 3: Verify identification dictionary
        ident = h.identification()
        assert ident["model"] == "LGRH4684"
        assert ident["who13_code"] == "20"
        assert ident["who1013_code"] == "26"
        assert ident["who1013_brand"] == "5 (Legrand BTicino)"
        assert ident["who1013_line"] == "0 (Undefined)"
        assert ident["conflict"] is None
        if H4684Profile is not None:
            assert ident["profile"] == "H4684Profile"
            profile = get_gateway_profile(ident["model"])
            assert isinstance(profile, H4684Profile)
            assert profile.model_name == "H4684"
        else:
            assert ident["profile"] in ("H4684Profile", "GenericGatewayProfile")

        # Step 4: Zero faults and zero repair issues
        assert h.device_health.faults == []
        create_issue.assert_not_called()
        corrected_issue.assert_not_called()


def test_shared_code_200_resolved_to_h4684_by_who1013():
    """Verify shared code 200 is settled cleanly by WHO 1013 object model 19 (1-value and 4-value)."""
    dev_reg = MagicMock()
    dev_reg.async_get.return_value = MagicMock(model="F454")

    with patch("homeassistant.helpers.device_registry.async_get", return_value=dev_reg):
        h = _create_mock_handler({"name": "MyHome Gateway"})

        # Gateway reports shared code 200
        h._handle_gateway_diagnostics(OWNEvent.parse("*#13**15*200##"))
        assert h._who13["code"] == "200"

        # Empirical single-value diagnostic response arrives settling model to H4684
        h._handle_gateway_identity_diagnostics(OWNEvent.parse("*#1013**1*19##"))
        ident = h.identification()
        assert ident["model"] == "H4684"
        assert ident["who13_code"] == "200"
        assert ident["who1013_code"] == "19"
        assert ident["conflict"] is None
        if H4684Profile is not None:
            assert ident["profile"] == "H4684Profile"
        else:
            assert ident["profile"] in ("H4684Profile", "GenericGatewayProfile")


def test_shared_code_200_resolved_to_lgrh4684_by_who1013():
    """Verify shared code 200 is settled cleanly by WHO 1013 object model 26."""
    dev_reg = MagicMock()
    dev_reg.async_get.return_value = MagicMock(model="F454")

    with patch("homeassistant.helpers.device_registry.async_get", return_value=dev_reg):
        h = _create_mock_handler({"name": "MyHome Gateway"})

        # Gateway reports shared code 200
        h._handle_gateway_diagnostics(OWNEvent.parse("*#13**15*200##"))
        assert h._who13["code"] == "200"

        # Diagnostic response arrives settling model to LGRH4684
        h._handle_gateway_identity_diagnostics(OWNEvent.parse("*#1013**1*26*15*5*0##"))
        ident = h.identification()
        assert ident["model"] == "LGRH4684"
        assert ident["who13_code"] == "200"
        assert ident["who1013_code"] == "26"
        assert ident["conflict"] is None
        if H4684Profile is not None:
            assert ident["profile"] == "H4684Profile"
        else:
            assert ident["profile"] in ("H4684Profile", "GenericGatewayProfile")


def test_h4684_qemu_oracle_subsystem_boundaries():
    """Verify H4684 020054 QEMU oracle verdicts and daemon subsystem boundaries."""
    oracle_path = Path(__file__).parent / "golden" / "firmware_oracle.json"
    verdicts = json.loads(oracle_path.read_text(encoding="utf-8"))["verdicts"]

    def _h4684_entry(frame: str) -> dict:
        matches = [e for e in verdicts[frame] if e["product"] == "H4684"]
        assert matches, f"Missing H4684 verdict for {frame}"
        return matches[0]

    # 1. Gateway identity & diagnostic frames in trace-corpus
    assert _h4684_entry("*#13**15##")["emitted_own"] == ["*#13**15*13##"]
    assert _h4684_entry("*#13**16##")["emitted_own"] == ["*#13**16*2*0*54##"]
    assert _h4684_entry("*#13**23##")["emitted_own"] == ["*#13**23*2*0*5##"]
    assert _h4684_entry("*#13**24##")["emitted_own"] == ["*#13**24*3*0*0##"]
    assert _h4684_entry("*#1013*0*1##")["emitted_own"] == ["*#1013**1*19##"]

    # 2. Subsystems supported by H4684 daemons (bt_autom, bt_termoreg, bt_alarm, bt_difson)
    for supported_frame in (
        "*0*1*31#4#01##",
        "*1*1*31##",
        "*2*1*31##",
        "*3*2*#1##",
        "*#4*1##",
        "*#5*#1##",
        "*#9*1##",
        "*14*0*32##",
        "*16*3*101##",
    ):
        entry = _h4684_entry(supported_frame)
        assert entry["verdict"] == "out", f"Expected H4684 to forward {supported_frame}"
        assert len(entry["bus_frames"]) > 0 or len(entry["emitted_own"]) > 0

    # 3. Subsystems absent on H4684 020054 (lacks bt_energia, bt_multi, bt_supervisione -> refuses WHO 17, 18, 22, 25)
    for unsupported_frame in (
        "*17*1*1##",
        "*18*21*11##",
        "*22*3#1*3#1#1##",
        "*25*21#1*21##",
    ):
        entry = _h4684_entry(unsupported_frame)
        assert entry["reply"] == "nack"
        assert entry["verdict"] == "silent"
        assert entry["bus_frames"] == []

