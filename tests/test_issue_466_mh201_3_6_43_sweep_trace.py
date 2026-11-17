"""Tests for #466: Physical BTicino MH201 (FW 3.6.43) Bus Sweep, WHO 1013 Identity & Timeout Trace.

Contributed by @emil-d in https://github.com/OpenWebNet-HA/MyHOME/issues/466#issuecomment-6436427163
(BTicino MH201 firmware 3.6.43, Home Assistant 2026.10.4, MyHOME 2.0.0b16, OWNd 2.0.0b11).

Pins five hardware facts from the verbatim `<myhome-bus-card>` sweep export:
1. Verbatim SHA-256 provenance, privacy hygiene, and frame-level `OWNd` parser coverage across all
   22 frames (13 `tx`, 9 `rx`).
2. First committed physical fixture carrying `WHO 1013` `OBJECT_MODEL = 10` (`MH201`) with trailing
   metadata `N_CONF = 15`, `BRAND = 0`, `LINE = 5` (distinct from Linux gateways' `15*5*0`), alongside
   `WHO 13` device type `200` and firmware `3.6.43`.
3. Sub-300 ms `WHO 13` replies (`*#13**0##`, `*#13**15##`, `*#13**16##`) paired with fast command-session
   `NACK` (`~10 ms`, zero `rx` event frames) on Linux-only kernel/distribution dimensions (`23`, `24`).
4. Exact on-wire timing of silent command-session timeouts (`COMMAND_TIMEOUT = 10.0 s` on unanswered
   `*#2*0##` and `*#4*0##`) and single-slot TCP reconnect lockouts (`MAX_CONNECT_ATTEMPTS = 5`,
   `1 + 2 + 4 + 6 = 13.0 s` backoff + 5 TCP `*99*0##` handshakes) on the MH201's 1-session stack.
5. Full integration replay discovering the 5 `OFF` lighting endpoints (`31`, `32`, `71`, `72`, `73`)
   with zero phantom cover, climate, audio, alarm, or energy sensor entities.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, cast
from unittest.mock import MagicMock, patch

import pytest
from homeassistant.const import (
    CONF_HOST,
    CONF_MAC,
    CONF_NAME,
    CONF_PASSWORD,
    CONF_PORT,
    STATE_OFF,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from OWNd.connection.session import COMMAND_TIMEOUT, MAX_CONNECT_ATTEMPTS
from OWNd.message import (
    OWNEvent,
    OWNGatewayEvent,
    OWNLightingEvent,
    OWNMessage,
)
from OWNd.profiles import MH201Profile
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.myhome.const import (
    CONF_DEVICE_TYPE,
    CONF_ENTITY,
    CONF_FIRMWARE,
    CONF_MANUFACTURER,
    CONF_SSDP_LOCATION,
    DOMAIN,
    IDENTIFICATION_MANUAL,
    IDENTIFICATION_SSDP,
)
from custom_components.myhome.gateway import (
    BUS_QUIET_PERIOD,
    MyHOMEGatewayHandler,
    command_session_default,
    command_session_limit,
)
from custom_components.myhome.services import _handler_refuses_frame
from scripts.anonymize_plant_fixture import findings

TRACE_PATH = (
    Path(__file__).resolve().parent
    / "fixtures"
    / "traces"
    / "issue_466"
    / "myhome_sweep_MH201_all_2026-10-27T07-28-09.json"
)
# `<myhome-bus-card>` exports `JSON.stringify(tracePayload, null, 2)` (no trailing newline);
# keep the fixture byte-for-byte identical to @emil-d's attachment on #466.
VERBATIM_FIXTURE_SHA256 = "54717d7ef7b207d04a475d9c379d1b043f8e50a533cbfec400e248c315f5e2bb"


def _load_trace() -> dict[str, Any]:
    assert TRACE_PATH.is_file(), f"Missing trace fixture: {TRACE_PATH}"
    return cast(dict[str, Any], json.loads(TRACE_PATH.read_text(encoding="utf-8")))


def test_mh201_3_6_43_sweep_schema_privacy_and_frame_counts() -> None:
    """Verify verbatim SHA-256 provenance, privacy, metadata, telemetry, and `OWNd` parse coverage."""
    # Normalize CRLF when checked out with `core.autocrlf=true` on Windows hosts
    raw_text = TRACE_PATH.read_text(encoding="utf-8")
    assert not raw_text.endswith("\n")
    assert hashlib.sha256(raw_text.encode("utf-8")).hexdigest() == VERBATIM_FIXTURE_SHA256
    assert findings(raw_text) == []

    doc = json.loads(raw_text)
    assert doc["capture"]["kind"] == "sweep"
    assert doc["capture"]["window"]["truncated"] is False
    assert doc["capture"]["window"]["frames"] == 22
    assert doc["capture"]["window"]["buffer_frames"] == 22

    env = doc["environment"]
    assert env["home_assistant_version"] == "2026.10.4"
    assert env["integration_version"] == "2.0.0b16"
    assert env["ownd_version"] == "2.0.0b11"

    gw = doc["gateway"]
    assert gw["model"] == "MH201"
    assert gw["manufacturer"] == "BTicino S.p.A."
    assert gw["firmware"] == "3.6.43"
    assert gw["mac_prefix"] == "00:03:50"
    assert gw["connection_type"] == "tcp"
    assert gw["queue_pacing"] == MH201Profile().command_queue_delay == 0.1

    telemetry = doc["telemetry"]
    assert telemetry["total_tx"] == 13
    assert telemetry["total_rx"] == 9
    assert telemetry["captured_in_buffer"] == 22

    frames = doc["frames"]
    assert len(frames) == 22
    tx_frames = [f for f in frames if f["direction"] == "tx"]
    rx_frames = [f for f in frames if f["direction"] == "rx"]
    assert len(tx_frames) == 13
    assert len(rx_frames) == 9

    for entry in frames:
        parsed = OWNMessage.parse(entry["raw"])
        assert parsed is not None, f"Failed to parse captured frame: {entry['raw']}"


async def test_mh201_3_6_43_first_physical_who1013_object_model_10_and_replay(
    hass: HomeAssistant,
) -> None:
    """Pin WHO 1013 OBJECT_MODEL 10 (`15*0*5`) and FW 3.6.43 from the physical MH201 export."""
    doc = _load_trace()
    ident = doc["gateway"]["identification"]

    assert ident == {
        "model": "MH201",
        "source": IDENTIFICATION_SSDP,
        "configured_model": "MH201",
        "ssdp_model": "MH201",
        "who13_code": "200",
        "who13_model": "F454",
        "who13_model_official": None,
        "who13_model_observed": "F454 / MyHomeServer1 / MH202 / F461 / H4890 / MH201",
        "who13_firmware": "3.6.43",
        "who13_kernel": None,
        "who13_distribution": None,
        "who1013_code": "10",
        "who1013_model": "MH201",
        "who1013_other_names": [],
        "who1013_n_conf": "15",
        "who1013_brand": "0",
        "who1013_line": "5",
        "profile": "MH201Profile",
        "conflict": None,
    }

    rx_raw = [f["raw"] for f in doc["frames"] if f["direction"] == "rx"]
    assert "*#13**15*200##" in rx_raw
    assert "*#13**16*3*6*43##" in rx_raw

    dev_reg = MagicMock()
    dev_reg.async_get.return_value = MagicMock(model="MH201", model_id=None)

    ssdp_entry = MockConfigEntry(
        domain=DOMAIN,
        title="MH201 Gateway",
        data={
            CONF_HOST: "192.0.2.46",
            CONF_PORT: 20000,
            CONF_PASSWORD: None,
            CONF_MAC: "00:03:50:00:04:66",
            CONF_NAME: "MH201",
            CONF_MANUFACTURER: "BTicino S.p.A.",
            CONF_SSDP_LOCATION: "http://192.0.2.46:49152/description.xml",
        },
        unique_id="00:03:50:00:04:66",
    )
    ssdp_entry.add_to_hass(hass)

    with (
        patch("homeassistant.helpers.device_registry.async_get", return_value=dev_reg),
        patch("custom_components.myhome.gateway.async_create_identity_issue"),
        patch("custom_components.myhome.gateway.async_delete_identity_issue"),
        patch("custom_components.myhome.gateway.async_create_identity_corrected_issue"),
    ):
        handler = MyHOMEGatewayHandler(hass, ssdp_entry)
        handler.device_registry_id = "dev_mh201"
        for raw in ("*#13**15*200##", "*#13**16*3*6*43##", "*#1013**1*10*15*0*5##"):
            msg = OWNEvent.parse(raw)
            assert msg is not None
            await handler._event_dispatcher.process_message(msg)

        replayed_ident = handler.identification()
        # `<myhome-bus-card>` strips `ssdp_location` (contains the LAN IP) on export
        assert {k: v for k, v in replayed_ident.items() if k != "ssdp_location"} == ident
        assert isinstance(handler.gateway.profile, MH201Profile)
        assert ssdp_entry.data[CONF_FIRMWARE] == "3.6.43"

        # Also verify an unconfigured Generic manual entry settles to MH201 from *#1013**1*10*15*0*5##
        generic_entry = MockConfigEntry(
            domain=DOMAIN,
            title="Generic Gateway",
            data={
                CONF_HOST: "192.0.2.47",
                CONF_PORT: 20000,
                CONF_PASSWORD: None,
                CONF_MAC: "00:03:50:00:04:67",
                CONF_NAME: "Generic",
                "model_source": IDENTIFICATION_MANUAL,
            },
            unique_id="00:03:50:00:04:67",
        )
        generic_entry.add_to_hass(hass)
        dev_reg.async_get.return_value = MagicMock(model="Generic", model_id=None)
        generic_handler = MyHOMEGatewayHandler(hass, generic_entry)
        generic_handler.device_registry_id = "dev_generic"
        for raw in ("*#13**15*200##", "*#1013**1*10*15*0*5##"):
            msg = OWNEvent.parse(raw)
            assert msg is not None
            await generic_handler._event_dispatcher.process_message(msg)

        assert generic_handler.gateway.model_name == "MH201"
        assert isinstance(generic_handler.gateway.profile, MH201Profile)
        assert generic_entry.data[CONF_NAME] == "MH201"


def test_mh201_3_6_43_who13_queries_and_non_linux_mcu_silence() -> None:
    """Verify fast WHO 13 replies (`0`, `15`, `16`) and fast NACK (no `rx`) on Linux dims (`23`, `24`)."""
    frames = _load_trace()["frames"]
    queue_pacing = MH201Profile().command_queue_delay
    assert queue_pacing == 0.1

    # Frame 0..1: clock read (*#13**0## -> *#13**0*09*26*26*## with empty TZ field)
    assert frames[0]["direction"] == "tx" and frames[0]["raw"] == "*#13**0##"
    assert frames[1]["direction"] == "rx" and frames[1]["raw"] == "*#13**0*09*26*26*##"
    assert frames[1]["timestamp"] - frames[0]["timestamp"] == pytest.approx(0.2539, abs=0.01)
    clock_msg = OWNEvent.parse(frames[1]["raw"])
    assert isinstance(clock_msg, OWNGatewayEvent)

    # Frame 2..3: device model (*#13**15## -> *#13**15*200## in ~18.6 ms after 100 ms queue pacing)
    assert frames[2]["timestamp"] - frames[1]["timestamp"] == pytest.approx(queue_pacing, abs=0.005)
    assert frames[2]["direction"] == "tx" and frames[2]["raw"] == "*#13**15##"
    assert frames[3]["direction"] == "rx" and frames[3]["raw"] == "*#13**15*200##"
    assert frames[3]["timestamp"] - frames[2]["timestamp"] == pytest.approx(0.0186, abs=0.005)

    # Frame 4..5: firmware version (*#13**16## -> *#13**16*3*6*43## in ~18.8 ms after 100 ms queue pacing)
    assert frames[4]["timestamp"] - frames[3]["timestamp"] == pytest.approx(queue_pacing, abs=0.005)
    assert frames[4]["direction"] == "tx" and frames[4]["raw"] == "*#13**16##"
    assert frames[5]["direction"] == "rx" and frames[5]["raw"] == "*#13**16*3*6*43##"
    assert frames[5]["timestamp"] - frames[4]["timestamp"] == pytest.approx(0.0188, abs=0.005)

    # Frame 6..7: Linux kernel (23) and distro (24) queries emit zero rx data frames, and advance in
    # ~109.8-109.9 ms (~9.9 ms command-session *#*0## NACK + 100 ms queue_pacing) rather than 10 s timeout
    assert frames[6]["timestamp"] - frames[5]["timestamp"] == pytest.approx(queue_pacing, abs=0.005)
    assert frames[6]["direction"] == "tx" and frames[6]["raw"] == "*#13**23##"
    assert frames[7]["direction"] == "tx" and frames[7]["raw"] == "*#13**24##"
    assert frames[7]["timestamp"] - frames[6]["timestamp"] == pytest.approx(
        queue_pacing + 0.0099, abs=0.005
    )
    assert frames[8]["timestamp"] - frames[7]["timestamp"] == pytest.approx(
        queue_pacing + 0.0098, abs=0.005
    )
    rx_dims_who13 = {
        f["dimension"]
        for f in frames
        if f["direction"] == "rx" and f["who"] == "13"
    }
    assert rx_dims_who13 == {"0", "15", "16"}


def test_mh201_3_6_43_command_timeout_and_single_slot_lockout_timings(
    hass: HomeAssistant,
) -> None:
    """Verify the exact 10 s command timeout and ~13.7 s single-slot TCP lockout intervals."""
    frames = _load_trace()["frames"]

    assert COMMAND_TIMEOUT == 10.0
    assert MAX_CONNECT_ATTEMPTS == 5
    assert BUS_QUIET_PERIOD == 0.75
    assert command_session_limit("MH201") == 1
    assert command_session_default("MH201") == 1

    # Frames 8..14: *#1*0## echoed in 66.9 ms and answered by 5 light points within 283.8 ms
    assert frames[8]["direction"] == "tx" and frames[8]["raw"] == "*#1*0##"
    assert frames[9]["direction"] == "rx" and frames[9]["raw"] == "*#1*0##"
    assert frames[9]["timestamp"] - frames[8]["timestamp"] == pytest.approx(0.0669, abs=0.005)

    light_replies = [f["raw"] for f in frames[10:15]]
    assert light_replies == ["*1*0*31##", "*1*0*71##", "*1*0*72##", "*1*0*73##", "*1*0*32##"]
    assert all(f["direction"] == "rx" for f in frames[10:15])
    assert frames[14]["timestamp"] - frames[8]["timestamp"] == pytest.approx(0.2838, abs=0.005)

    # BUS_QUIET_PERIOD (0.75 s) after last lighting reply before *#2*0## is sent
    assert frames[15]["direction"] == "tx" and frames[15]["raw"] == "*#2*0##"
    assert frames[15]["timestamp"] - frames[14]["timestamp"] == pytest.approx(
        BUS_QUIET_PERIOD, abs=0.02
    )

    # Zero rx frames arrive anywhere after frames[14]
    assert [f for f in frames[15:] if f["direction"] == "rx"] == []

    # Unanswered *#2*0## hits COMMAND_TIMEOUT (10.0 s) + BUS_QUIET_PERIOD (0.75 s) = 10.753 s
    assert frames[16]["direction"] == "tx" and frames[16]["raw"] == "*#4*0##"
    assert frames[16]["timestamp"] - frames[15]["timestamp"] == pytest.approx(
        COMMAND_TIMEOUT + BUS_QUIET_PERIOD, abs=0.05
    )

    # Every subsequent frame (frames[16..21]) enters OWNSession.connect() for MAX_CONNECT_ATTEMPTS = 5
    # against the MH201's locked single command slot:
    # - 4 inter-attempt backoff sleeps: sum(max(1, i * 2) for i in range(4)) = 1 + 2 + 4 + 6 = 13.0 s
    # - 5 TCP *99*0## handshake round-trips against the MH201 MCU (~136-149 ms each = 0.682-0.744 s)
    backoff_sleep_total = float(
        sum(max(1, i * 2) for i in range(MAX_CONNECT_ATTEMPTS - 1))
    )
    assert backoff_sleep_total == 13.0

    # frames[16..17] (*#4*0## -> *#16*0*5##, 24.494 s): 5th connect() attempt succeeds once *#2*0##'s
    # server socket expires, then *#4*0## hits COMMAND_TIMEOUT (10.0 s) + BUS_QUIET_PERIOD (0.75 s)
    assert frames[17]["direction"] == "tx" and frames[17]["raw"] == "*#16*0*5##"
    dt_climate = frames[17]["timestamp"] - frames[16]["timestamp"]
    assert dt_climate == pytest.approx(24.494, abs=0.01)

    # Current MH201 handler omits *#4*0## from both startup discovery and unforced sweeps (#794)
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={
            CONF_HOST: "192.0.2.46",
            CONF_PORT: 20000,
            CONF_PASSWORD: None,
            CONF_MAC: "00:03:50:00:04:66",
            CONF_NAME: "MH201",
            CONF_FIRMWARE: "3.6.43",
        },
    )
    entry.add_to_hass(hass)
    handler = MyHOMEGatewayHandler(hass, entry)
    assert handler.general_zone_request_refused is True
    assert "*#4*0##" not in handler._discovery_frames()
    assert _handler_refuses_frame(handler, "*#4*0##") is True

    # frames[17..18] (*#16*0*5## -> *#18*51*51##, 14.477 s): connect() exhausts all 5 attempts,
    # cancels `written`, and `send_paced` waits BUS_QUIET_PERIOD (0.75 s)
    assert frames[18]["direction"] == "tx" and frames[18]["raw"] == "*#18*51*51##"
    dt_sound = frames[18]["timestamp"] - frames[17]["timestamp"]
    assert dt_sound == pytest.approx(14.477, abs=0.01)

    # frames[18..21] (*#18*51..52*51/1200##, 13.682 / 13.701 / 13.685 s): all 36 WHO=18 queries are
    # already queued in FrameScheduler by `handle_sweep_bus`, and failed `connect()` hits `continue`
    # in `sending_loop` (skipping `command_queue_delay`), leaving purely backoff_sleep_total + 5 TCP RTTs
    energy_queries = [f["raw"] for f in frames[18:22]]
    assert energy_queries == [
        "*#18*51*51##",
        "*#18*51*1200##",
        "*#18*52*51##",
        "*#18*52*1200##",
    ]
    dt_energy = [
        next_f["timestamp"] - prev_f["timestamp"]
        for prev_f, next_f in zip(frames[18:21], frames[19:22], strict=True)
    ]
    assert dt_energy == pytest.approx([13.682, 13.701, 13.685], abs=0.01)

    # Across all five 5-attempt cycles, residual TCP handshake overhead is uniformly 0.68-0.75 s
    five_attempt_handshake_overheads = [
        dt_climate - (backoff_sleep_total + COMMAND_TIMEOUT + BUS_QUIET_PERIOD),
        dt_sound - (backoff_sleep_total + BUS_QUIET_PERIOD),
        *(dt - backoff_sleep_total for dt in dt_energy),
    ]
    for overhead in five_attempt_handshake_overheads:
        assert 0.68 <= overhead <= 0.75


async def test_mh201_3_6_43_sweep_replay_discovers_five_lights_and_no_phantom_entities(
    hass: HomeAssistant,
) -> None:
    """Replay all captured `rx` frames through a live config entry and verify entity discovery."""
    doc = _load_trace()
    mac = "00:03:50:00:04:66"
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="MH201 Gateway",
        data={
            CONF_HOST: "192.0.2.46",
            CONF_PORT: 20000,
            CONF_PASSWORD: None,
            CONF_MAC: mac,
            CONF_NAME: "MH201",
            CONF_DEVICE_TYPE: "urn:schemas-bticino-it:device:webserver:1",
            CONF_MANUFACTURER: "BTicino S.p.A.",
            CONF_FIRMWARE: "3.6.43",
            CONF_SSDP_LOCATION: "http://192.0.2.46:49152/description.xml",
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

    handler: MyHOMEGatewayHandler = hass.data[DOMAIN][mac][CONF_ENTITY]
    handler._on_event_connection_state_change(True)

    rx_frames = [f["raw"] for f in doc["frames"] if f["direction"] == "rx"]
    assert len(rx_frames) == 9

    lighting_wheres: list[str] = []
    for raw in rx_frames:
        msg = OWNEvent.parse(raw) or OWNMessage.parse(raw)
        assert msg is not None
        if isinstance(msg, OWNLightingEvent) and msg.where != "0":
            lighting_wheres.append(str(msg.where))
        await handler._event_dispatcher.process_message(msg)

    await hass.async_block_till_done()
    assert lighting_wheres == ["31", "71", "72", "73", "32"]

    ent_reg = er.async_get(hass)
    entries = er.async_entries_for_config_entry(ent_reg, entry.entry_id)
    by_domain: dict[str, list[er.RegistryEntry]] = {}
    for reg_entry in entries:
        by_domain.setdefault(reg_entry.domain, []).append(reg_entry)

    light_entries = by_domain.get("light", [])
    assert len(light_entries) == 5
    discovered_light_wheres = {
        reg_entry.unique_id.rsplit("-", 1)[-1] for reg_entry in light_entries
    }
    assert discovered_light_wheres == {"31", "32", "71", "72", "73"}
    for light_entry in light_entries:
        state = hass.states.get(light_entry.entity_id)
        assert state is not None
        assert state.state == STATE_OFF

    for forbidden_domain in (
        "cover",
        "climate",
        "media_player",
        "alarm_control_panel",
        "sensor",
    ):
        assert by_domain.get(forbidden_domain, []) == []

    assert await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()
