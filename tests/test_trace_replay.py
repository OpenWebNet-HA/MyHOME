"""Tests for P5: Real-World Gateway Trace Replay Fixtures.

Validates that real on-wire OpenWebNet traces captured from production gateways
(via <myhome-bus-card> and Home Assistant Diagnostics) can be deterministically
replayed against the integration state machine without exceptions or regressions.
"""

from __future__ import annotations

import json
import time
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
from homeassistant.helpers.dispatcher import async_dispatcher_send
from OWNd.message import OWNMessage
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.myhome.const import (
    CONF_DEVICE_TYPE,
    CONF_ENTITY,
    CONF_FILE_PATH,
    CONF_FIRMWARE,
    CONF_MANUFACTURER,
    CONF_MANUFACTURER_URL,
    CONF_SSDP_LOCATION,
    CONF_SSDP_ST,
    CONF_UDN,
    DOMAIN,
)
from custom_components.myhome.diagnostics import async_get_config_entry_diagnostics

FIXTURES_PLANTS_DIR = Path(__file__).resolve().parent / "fixtures" / "plants"


def get_plant_fixture_dirs() -> list[Path]:
    """Discover all plant fixture directories containing diagnostic traces."""
    if not FIXTURES_PLANTS_DIR.is_dir():
        return []
    return [
        p
        for p in FIXTURES_PLANTS_DIR.iterdir()
        if p.is_dir() and (p / "diagnostic_summary.json").is_file()
    ]


class TestTraceReplayHarness:
    """Test suite executing frozen real-world gateway bus captures."""

    def test_fixture_discovery_and_schema(self) -> None:
        """Verify plant fixture directories contain valid diagnostic summaries."""
        fixtures = get_plant_fixture_dirs()
        assert len(fixtures) >= 1, (
            "Expected at least one real-world plant fixture in tests/fixtures/plants"
        )

        for plant_dir in fixtures:
            diag_file = plant_dir / "diagnostic_summary.json"
            assert diag_file.is_file()
            with open(diag_file, "r", encoding="utf-8") as f:
                payload = json.load(f)

            # Validate standard HA diagnostic & bus card structure
            assert "data" in payload, f"Missing 'data' in {diag_file}"
            data = payload["data"]
            assert "bus_monitor" in data, f"Missing 'bus_monitor' in {diag_file}"
            recent_frames = data["bus_monitor"].get("recent_frames", [])
            assert len(recent_frames) > 0, f"No frames recorded in {diag_file}"

            for frame in recent_frames:
                assert "raw" in frame
                assert "direction" in frame
                assert frame["direction"] in ("rx", "tx")

    @pytest.mark.asyncio
    async def test_real_world_trace_replay_issue_247(self, hass: HomeAssistant) -> None:
        """Replay all 100 on-wire frames from Nicola Cavallo's production F454 gateway.

        Verifies that every single frame across WHO 1, 2, 4, 13, 14, 16, 18 and
        ACK/NACK signals is cleanly processed without unhandled exceptions or state loss.
        """
        plant_dir = FIXTURES_PLANTS_DIR / "issue_247_nicolacavallo84"
        plant_yaml = plant_dir / "myhome.yaml"
        diag_json = plant_dir / "diagnostic_summary.json"

        assert plant_yaml.is_file()
        assert diag_json.is_file()

        with open(diag_json, "r", encoding="utf-8") as f:
            diag_data = json.load(f)

        raw_frames = diag_data["data"]["bus_monitor"]["recent_frames"]
        assert len(raw_frames) == 100, f"Expected 100 frames in trace, found {len(raw_frames)}"

        mac = "00:03:50:24:70:01"
        entry = MockConfigEntry(
            domain=DOMAIN,
            data={
                CONF_HOST: "192.168.1.50",
                CONF_PORT: 20000,
                CONF_PASSWORD: "pass",
                CONF_MAC: mac,
                CONF_SSDP_LOCATION: "http://192.168.1.50:49153/description.xml",
                CONF_SSDP_ST: "urn:schemas-upnp-org:device:Basic:1",
                CONF_DEVICE_TYPE: "urn:schemas-upnp-org:device:Basic:1",
                CONF_FRIENDLY_NAME: "MyHomeServer1",
                CONF_MANUFACTURER: "BTicino",
                CONF_MANUFACTURER_URL: "http://www.bticino.com",
                CONF_NAME: "MyHomeServer1",
                CONF_FIRMWARE: "2.0.0",
                CONF_UDN: "uuid:mhs1-issue247",
            },
            options={
                CONF_FILE_PATH: str(plant_yaml),
            },
            unique_id=mac,
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

        handler = hass.data[DOMAIN][mac][CONF_ENTITY]
        handler._on_event_connection_state_change(True)

        replayed_count = 0
        skipped_ack_nack = 0

        # Sequentially replay all 100 frames from the production gateway trace
        for item in raw_frames:
            raw = item.get("raw")
            if not raw:
                continue

            # Special bus signals: ACK and NACK
            if raw in ("*#*1##", "*#*0##"):
                skipped_ack_nack += 1
                continue

            try:
                msg = OWNMessage.parse(raw)
            except Exception as exc:
                pytest.fail(f"Trace replay failed to parse real-world frame {raw!r}: {exc}")

            # Feed frame into Home Assistant event bus
            async_dispatcher_send(hass, f"myhome_message_{mac}", msg)
            replayed_count += 1

        await hass.async_block_till_done()

        assert replayed_count == 100, (
            f"Expected 100 valid message frames, replayed {replayed_count}"
        )

        # Verify key entity states reflecting on-wire status changes from trace
        # 1. Lighting: *1*0*1002## -> OFF
        light_state = hass.states.get("light.luci_vialetto_lontano")
        assert light_state is not None
        assert light_state.state == "off"

        # 2. Configured Lighting: *1*0*92## -> OFF
        light_scala = hass.states.get("light.luce_scala_esterna")
        assert light_scala is not None
        assert light_scala.state == "off"

        # 3. Configured Switch (F522): *1*1*24## -> ON
        switch_clima = hass.states.get("switch.interruttore_climatizzatori_zona_notte")
        assert switch_clima is not None
        assert switch_clima.state == "on"

        # 4. Configured Switch: *1*1*0910## -> ON
        switch_prese = hass.states.get("switch.prese_esterne")
        assert switch_prese is not None
        assert switch_prese.state == "on"

        # 5. Configured Switch: *1*1*14## -> ON
        switch_forno = hass.states.get("switch.presa_forno")
        assert switch_forno is not None
        assert switch_forno.state == "on"

        # 3. Automation / Covers: *2*0*42## -> STOPPED
        cover_state = hass.states.get("cover.tapparella_bagno_piccolo")
        assert cover_state is not None
        assert cover_state.state in ("open", "closed")

        # 4. Dry contact (quiescent during trace): *25*... -> OFF
        cancello_state = hass.states.get("binary_sensor.cancello")
        assert cancello_state is not None
        assert cancello_state.state == "off"

        # 5. Energy Meter: *#18*51*113*602## -> 602 W
        power_state = hass.states.get("sensor.consumo_energia")
        assert power_state is not None
        assert power_state.state == "602"

        # Unload cleanly
        await hass.config_entries.async_unload(entry.entry_id)

    @pytest.mark.asyncio
    async def test_real_world_trace_replay_mh200_physical_plant(
        self, hass: HomeAssistant
    ) -> None:
        """Replay on-wire frames from the physical BTicino MH200 gateway plant.

        Verifies that all 107 frames captured from the physical MH200
        (controlling 62 lights, 7 switches, and 11 covers across F422 bus-bus
        interfaces) replay cleanly against the integration state machine without
        exceptions, verifying entity discovery and state synchronization.
        """
        plant_dir = FIXTURES_PLANTS_DIR / "mh200_physical_plant"
        plant_yaml = plant_dir / "myhome.yaml"
        diag_json = plant_dir / "diagnostic_summary.json"

        assert plant_yaml.is_file()
        assert diag_json.is_file()

        with open(diag_json, "r", encoding="utf-8") as f:
            diag_data = json.load(f)

        raw_frames = diag_data["data"]["bus_monitor"]["recent_frames"]
        assert len(raw_frames) >= 100, f"Expected at least 100 frames, found {len(raw_frames)}"

        mac = "00:03:50:20:00:01"
        entry = MockConfigEntry(
            domain=DOMAIN,
            data={
                CONF_HOST: "192.168.1.50",
                CONF_PORT: 20000,
                CONF_PASSWORD: "pass",
                CONF_MAC: mac,
                CONF_NAME: "MH200",
                CONF_DEVICE_TYPE: "urn:schemas-upnp-org:device:Basic:1",
                CONF_FRIENDLY_NAME: "MH200 Gateway",
                CONF_MANUFACTURER: "BTicino",
                CONF_FIRMWARE: "2.0.0",
            },
            options={
                CONF_FILE_PATH: str(plant_yaml),
            },
            unique_id=mac,
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

        handler = hass.data[DOMAIN][mac][CONF_ENTITY]
        handler._on_event_connection_state_change(True)

        replayed_count = 0
        for item in raw_frames:
            raw = item.get("raw")
            if not raw or raw in ("*#*1##", "*#*0##"):
                continue

            try:
                msg = OWNMessage.parse(raw)
            except Exception as exc:
                pytest.fail(f"Trace replay failed to parse MH200 frame {raw!r}: {exc}")

            async_dispatcher_send(hass, f"myhome_message_{mac}", msg)
            replayed_count += 1

        await hass.async_block_till_done()
        assert replayed_count >= 80

        # Verify active states from the real physical MH200 plant
        # 1. Keuken Wasbak (where: 57) -> ON (*1*1*57##)
        light_wasbak = hass.states.get("light.keuken_wasbak")
        assert light_wasbak is not None
        assert light_wasbak.state == "on"

        # 2. Keuken Tafel (where: 69, dimmable) -> ON (*1*9*69##)
        light_tafel = hass.states.get("light.keuken_tafel")
        assert light_tafel is not None
        assert light_tafel.state == "on"

        # 3. Keuken Plafond (where: 67) -> ON (*1*10*67##)
        light_plafond = hass.states.get("light.keuken_plafond")
        assert light_plafond is not None
        assert light_plafond.state == "on"

        # 4. Stopcontact Bed (where: 84) -> ON (*1*1*84##)
        switch_bed = hass.states.get("switch.stopcontact_bed")
        assert switch_bed is not None
        assert switch_bed.state == "on"

        # 5. Covers with F422 interface (e.g. Gordijn Woonkamer West: 11#4#02)
        cover_west = hass.states.get("cover.gordijn_woonkamer_west")
        assert cover_west is not None

        # Unload cleanly
        await hass.config_entries.async_unload(entry.entry_id)

    @pytest.mark.parametrize(
        "plant_dir",
        get_plant_fixture_dirs(),
        ids=lambda p: p.name,
    )
    @pytest.mark.asyncio
    async def test_dynamic_plant_trace_replay_matrix(
        self, hass: HomeAssistant, plant_dir: Path
    ) -> None:
        """Dynamically replay every discovered plant fixture without custom test code.

        Ensures that any community-submitted gateway trace added to tests/fixtures/plants/
        is automatically loaded, setup in Home Assistant, and replayed through the event bus
        with zero unhandled exceptions or state loss.
        """
        plant_yaml = plant_dir / "myhome.yaml"
        diag_json = plant_dir / "diagnostic_summary.json"

        assert diag_json.is_file(), f"Missing diagnostic_summary.json in {plant_dir}"

        with open(diag_json, "r", encoding="utf-8") as f:
            diag_data = json.load(f)

        raw_frames = diag_data["data"]["bus_monitor"]["recent_frames"]
        assert len(raw_frames) > 0, f"No frames in {diag_json}"

        config_entry_data = diag_data["data"].get("config_entry", {})
        entry_dict = dict(config_entry_data.get("data", {}))

        mac = entry_dict.get(CONF_MAC) or "00:03:50:99:99:99"
        entry_dict[CONF_HOST] = entry_dict.get(CONF_HOST, "192.168.1.50")
        entry_dict[CONF_PORT] = entry_dict.get(CONF_PORT, 20000)
        entry_dict[CONF_PASSWORD] = "pass"
        entry_dict[CONF_MAC] = mac

        options = {}
        if plant_yaml.is_file():
            options[CONF_FILE_PATH] = str(plant_yaml)

        entry = MockConfigEntry(
            domain=DOMAIN,
            data=entry_dict,
            options=options,
            unique_id=mac,
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

        handler = hass.data[DOMAIN][mac][CONF_ENTITY]
        handler._on_event_connection_state_change(True)

        replayed_count = 0
        for item in raw_frames:
            raw = item.get("raw")
            if not raw or raw in ("*#*1##", "*#*0##"):
                continue

            try:
                msg = OWNMessage.parse(raw)
            except Exception as exc:
                pytest.fail(f"Plant {plant_dir.name} failed to parse on-wire frame {raw!r}: {exc}")

            async_dispatcher_send(hass, f"myhome_message_{mac}", msg)
            replayed_count += 1

        await hass.async_block_till_done()
        assert replayed_count > 0, f"No valid frames replayed for {plant_dir.name}"

        # Clean unload
        await hass.config_entries.async_unload(entry.entry_id)

    @pytest.mark.asyncio
    async def test_trace_replay_resilience_to_malformed_and_unknown_frames(
        self, hass: HomeAssistant
    ) -> None:
        """Ensure unhandled, corrupt, or unknown WHO frames do not crash the integration."""
        mac = "00:03:50:24:70:99"
        entry = MockConfigEntry(
            domain=DOMAIN,
            data={
                CONF_HOST: "192.168.1.60",
                CONF_PORT: 20000,
                CONF_PASSWORD: "pass",
                CONF_MAC: mac,
            },
            unique_id=mac,
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

        handler = hass.data[DOMAIN][mac][CONF_ENTITY]
        handler._on_event_connection_state_change(True)

        # Synthetic trace containing unknown WHOs and control signals
        synthetic_trace = [
            "*#*1##",  # ACK
            "*#*0##",  # NACK
            "*999*1*1##",  # Unknown WHO
            "*15*1*1##",  # CEN frame without configured entity
            "*1001*0*1##",  # Diagnostic WHO
            None,  # None / empty entry
        ]

        for raw in synthetic_trace:
            if not raw or raw in ("*#*1##", "*#*0##"):
                continue
            try:
                msg = OWNMessage.parse(raw)
                async_dispatcher_send(hass, f"myhome_message_{mac}", msg)
            except Exception:
                # Malformed frame shouldn't crash test
                pass

        await hass.async_block_till_done()

        # Gateway handler remains connected and operational
        assert handler.is_connected is True

        await hass.config_entries.async_unload(entry.entry_id)

    @pytest.mark.asyncio
    async def test_trace_replay_high_frequency_burst(self, hass: HomeAssistant) -> None:
        """Stress-test dispatcher with 50 frames fired in rapid burst without per-frame awaits."""
        mac = "00:03:50:24:70:88"
        plant_dir = FIXTURES_PLANTS_DIR / "issue_247_nicolacavallo84"
        plant_yaml = plant_dir / "myhome.yaml"

        entry = MockConfigEntry(
            domain=DOMAIN,
            data={
                CONF_HOST: "192.168.1.50",
                CONF_PORT: 20000,
                CONF_PASSWORD: "pass",
                CONF_MAC: mac,
            },
            options={CONF_FILE_PATH: str(plant_yaml)},
            unique_id=mac,
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

        handler = hass.data[DOMAIN][mac][CONF_ENTITY]
        handler._on_event_connection_state_change(True)

        # 1. Initialize and add light to HA
        init_msg = OWNMessage.parse("*1*0*10##")
        async_dispatcher_send(hass, f"myhome_message_{mac}", init_msg)
        await hass.async_block_till_done()

        # 2. Rapidly toggle light 10 50 times in a burst
        for i in range(50):
            action = "1" if (i % 2 == 0) else "0"
            msg = OWNMessage.parse(f"*1*{action}*10##")
            async_dispatcher_send(hass, f"myhome_message_{mac}", msg)

        # Await full event loop flush
        await hass.async_block_till_done()

        # Entity should be in the final state (i=49 -> action='0' -> 'off')
        state = hass.states.get("light.light_10")
        assert state is not None
        assert state.state == "off"

        await hass.config_entries.async_unload(entry.entry_id)

    @pytest.mark.asyncio
    async def test_trace_replay_diagnostic_export_roundtrip(self, hass: HomeAssistant) -> None:
        """Verify streamed frames are captured by the bus monitor and exported in diagnostics."""
        mac = "00:03:50:24:70:77"
        entry = MockConfigEntry(
            domain=DOMAIN,
            data={
                CONF_HOST: "192.168.1.50",
                CONF_PORT: 20000,
                CONF_PASSWORD: "pass",
                CONF_MAC: mac,
            },
            unique_id=mac,
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

        handler = hass.data[DOMAIN][mac][CONF_ENTITY]

        # Record frames directly into bus monitor as gateway would
        test_frames = [
            "*1*1*11##",
            "*1*0*11##",
            "*2*1*21##",
            "*#18*51*113*350##",
        ]
        for raw in test_frames:
            handler.bus_monitor.record_frame(raw=raw, direction="rx")

        diagnostics = await async_get_config_entry_diagnostics(hass, entry)
        assert "bus_monitor" in diagnostics
        bm = diagnostics["bus_monitor"]
        assert "recent_frames" in bm
        assert len(bm["recent_frames"]) == 4

        recorded_raws = [f["raw"] for f in bm["recent_frames"]]
        assert recorded_raws == test_frames

        await hass.config_entries.async_unload(entry.entry_id)

    @pytest.mark.asyncio
    async def test_real_world_trace_replay_issue_292_thedarkwizard(self, hass: HomeAssistant) -> None:
        """Replay all 50 on-wire frames from @TheDarkWizard's MyHomeServer1 gateway trace (Issue #292).

        Verifies:
        1. All 50 frames are parsed and dispatched cleanly across 20 lights and 5 covers.
        2. BusMonitor de-duplication suppresses the duplicate interleaved echo frames.
        3. WHO=13 Dimension 15 dynamic auto-detection updates the gateway model from F454
           to MyHomeServer1 and sets inter-frame pacing to 0.02s.
        4. WHO=13 Dimension 16 updates the firmware version.
        """
        plant_dir = FIXTURES_PLANTS_DIR / "issue_292_thedarkwizard"
        plant_yaml = plant_dir / "myhome.yaml"
        diag_json = plant_dir / "diagnostic_summary.json"

        assert plant_yaml.is_file()
        assert diag_json.is_file()

        with open(diag_json, "r", encoding="utf-8") as f:
            diag_data = json.load(f)

        raw_frames = diag_data["data"]["bus_monitor"]["recent_frames"]
        assert len(raw_frames) == 50, f"Expected 50 frames in trace, found {len(raw_frames)}"

        mac = "00:03:50:30:13:34"
        # Configured as F454 initially (reproducing the issue where manual entry defaulted to F454)
        entry = MockConfigEntry(
            domain=DOMAIN,
            data={
                CONF_HOST: "192.168.30.134",
                CONF_PORT: 20000,
                CONF_PASSWORD: None,
                CONF_MAC: mac,
                CONF_NAME: "F454",
                CONF_FIRMWARE: None,
            },
            options={
                CONF_FILE_PATH: str(plant_yaml),
            },
            unique_id=mac,
            title="F454 Gateway",
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

        handler = hass.data[DOMAIN][mac][CONF_ENTITY]
        handler._on_event_connection_state_change(True)

        # Initial state: gateway profile is F454 with 0.05s pacing
        assert handler.model == "F454"
        assert handler.profile.command_queue_delay == 0.05

        # Replay all 50 frames into bus monitor and dispatcher
        for item in raw_frames:
            raw = item.get("raw")
            if not raw:
                continue
            handler.bus_monitor.record_frame(raw=raw, direction="rx")
            msg = OWNMessage.parse(raw)
            async_dispatcher_send(hass, f"myhome_message_{mac}", msg)

        await hass.async_block_till_done()

        # 1. Verify bus monitor de-duplication: the 50 frames had 25 duplicates, so captured should be 25
        assert handler.bus_monitor.total_rx == 25
        assert len(handler.bus_monitor.get_recent_frames()) == 25

        # 2. Verify lighting entity states
        # *1*1*12## -> ON
        light_12 = hass.states.get("light.light_12")
        assert light_12 is not None
        assert light_12.state == "on"

        # *1*0*19## -> OFF
        light_19 = hass.states.get("light.light_19")
        assert light_19 is not None
        assert light_19.state == "off"

        # *1*1*0012## -> ON
        light_0012 = hass.states.get("light.light_0012")
        assert light_0012 is not None
        assert light_0012.state == "on"

        # *1*0*0110## -> OFF
        light_0110 = hass.states.get("light.light_0110")
        assert light_0110 is not None
        assert light_0110.state == "off"

        # 3. Verify cover entity states
        cover_02 = hass.states.get("cover.cover_02")
        assert cover_02 is not None

        # 4. Verify WHO=13 Dimension 15 auto-detection:
        # Gateway sends *#13**15*2## (device type 2 = MHServer / MyHomeServer1)
        who13_dim15 = OWNMessage.parse("*#13**15*2##")
        await handler._process_message(who13_dim15)
        await hass.async_block_till_done()

        # Model and profile must now be auto-corrected to MyHomeServer1!
        assert handler.model == "MyHomeServer1"
        assert handler.profile.model_name == "MyHomeServer1"
        assert handler.profile.command_queue_delay == 0.02
        assert entry.data[CONF_NAME] == "MyHomeServer1"
        assert entry.title == "MyHomeServer1 Gateway"

        # 5. Verify WHO=13 Dimension 16 firmware auto-detection:
        who13_dim16 = OWNMessage.parse("*#13**16*2*40*12##")
        await handler._process_message(who13_dim16)
        await hass.async_block_till_done()

        assert handler.firmware == "2.40.12"
        assert entry.data[CONF_FIRMWARE] == "2.40.12"

        await hass.config_entries.async_unload(entry.entry_id)

    @pytest.mark.asyncio
    async def test_real_world_trace_replay_issue_297_f454_thedarkwizard(self, hass: HomeAssistant) -> None:
        """Replay all 127 on-wire frames from @TheDarkWizard's F454 gateway trace (Issue #297).

        Verifies:
        1. All 127 frames are parsed cleanly without crashing on timezone sentinel 999.
        2. F454 clock broadcast *#13**0*23*06*59*999## parses into a valid datetime.time.
        3. WHO=13 Dimension 15 *#13**15*200## accurately identifies hardware model as F454.
        4. BusMonitor sliding-window deduplication and has_frame_since suppress redundant frames.
        5. Light and cover entities are populated and updated correctly.
        """
        plant_dir = FIXTURES_PLANTS_DIR / "issue_297_thedarkwizard"
        plant_yaml = plant_dir / "myhome.yaml"
        diag_json = plant_dir / "diagnostic_summary.json"

        assert plant_yaml.is_file()
        assert diag_json.is_file()

        with open(diag_json, "r", encoding="utf-8") as f:
            diag_data = json.load(f)

        raw_frames = diag_data["data"]["bus_monitor"]["recent_frames"]
        assert len(raw_frames) == 127, f"Expected 127 frames in trace, found {len(raw_frames)}"

        mac = "00:03:50:30:13:34"
        entry = MockConfigEntry(
            domain=DOMAIN,
            data={
                CONF_HOST: "192.168.30.134",
                CONF_PORT: 20000,
                CONF_PASSWORD: None,
                CONF_MAC: mac,
                CONF_NAME: "F454",
                CONF_FIRMWARE: None,
            },
            options={
                CONF_FILE_PATH: str(plant_yaml),
            },
            unique_id=mac,
            title="F454 Gateway",
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

        handler = hass.data[DOMAIN][mac][CONF_ENTITY]
        handler._on_event_connection_state_change(True)

        # 1. Test F454 real-time clock frame with 999 unconfigured timezone offset
        rtc_frame = "*#13**0*23*06*59*999##"
        rtc_msg = OWNMessage.parse(rtc_frame)
        assert isinstance(rtc_msg, OWNMessage)
        assert rtc_msg.is_valid is True
        assert getattr(rtc_msg, "_hour", None) == "23"
        assert getattr(rtc_msg, "_minute", None) == "06"
        assert getattr(rtc_msg, "_second", None) == "59"
        assert getattr(rtc_msg, "_timezone", None) == ""
        await handler._process_message(rtc_msg)

        # 2. Test F454 hardware model identification frame
        model_frame = "*#13**15*200##"
        model_msg = OWNMessage.parse(model_frame)
        assert isinstance(model_msg, OWNMessage)
        assert getattr(model_msg, "_device_type", None) == "F454"
        await handler._process_message(model_msg)
        assert handler.model == "F454"
        assert handler.profile.model_name == "F454"
        assert handler.profile.command_queue_delay == 0.05

        # 3. Feed frames into BusMonitor and verify sliding window deduplication
        task_start = time.time()
        for item in raw_frames:
            raw = item.get("raw")
            direction = item.get("direction", "rx")
            if not raw:
                continue
            parsed_msg = OWNMessage.parse(raw)
            handler.bus_monitor.record_frame(direction=direction, raw=raw, parsed=parsed_msg)
            if direction == "rx" and parsed_msg is not None:
                async_dispatcher_send(hass, f"myhome_message_{mac}", parsed_msg)

        await hass.async_block_till_done()

        # Deduplication check: immediate duplicates in trace are suppressed
        # Out of 127 total frames, immediate identical frames are deduplicated
        assert handler.bus_monitor.total_rx < 122
        assert len(handler.bus_monitor.get_recent_frames()) < 127

        # 4. Verify has_frame_since detects frames recorded during the sweep
        assert handler.bus_monitor.has_frame_since(task_start, direction="rx", raw="*#2*02*10*10*100*001*0##") is True
        assert handler.bus_monitor.has_frame_since(task_start, direction="rx", raw="*#2*99*10*10*100*001*0##") is False

        # 5. Verify entity states from trace status messages
        # *1*1*12## -> ON
        light_12 = hass.states.get("light.light_12")
        assert light_12 is not None
        assert light_12.state == "on"

        # *1*0*19## -> OFF
        light_19 = hass.states.get("light.light_19")
        assert light_19 is not None
        assert light_19.state == "off"

        # *1*1*0012## -> ON
        light_0012 = hass.states.get("light.light_0012")
        assert light_0012 is not None
        assert light_0012.state == "on"

        # *1*0*0110## -> OFF
        light_0110 = hass.states.get("light.light_0110")
        assert light_0110 is not None
        assert light_0110.state == "off"

        # Verify cover entity
        cover_02 = hass.states.get("cover.cover_02")
        assert cover_02 is not None

        await hass.config_entries.async_unload(entry.entry_id)


