# privacy-check: allow-samples - the redaction test feeds the diagnostics a household identity
"""Tests for MyHOME config entry diagnostics."""
from unittest.mock import MagicMock

import pytest
from homeassistant.const import CONF_MAC, CONF_PASSWORD
from homeassistant.core import HomeAssistant
from OWNd.message import OWNMessage

from custom_components.myhome.bus_monitor import DEFAULT_RING_BUFFER_SIZE, BusMonitor
from custom_components.myhome.const import CONF_ENTITY, CONF_PLATFORMS, DOMAIN, INTEGRATION_VERSION
from custom_components.myhome.diagnostics import async_get_config_entry_diagnostics
from tests.conftest import attach_runtime


@pytest.mark.asyncio
async def test_diagnostics_without_gateway_handler(hass: HomeAssistant):
    """Test diagnostics output when gateway handler is not yet registered."""
    mock_entry = MagicMock()
    mock_entry.entry_id = "test_entry_123"
    mock_entry.version = 1
    mock_entry.domain = DOMAIN
    mock_entry.title = "My Gateway"
    mock_entry.data = {
        CONF_MAC: "00:03:50:11:22:33",
        CONF_PASSWORD: "super_secret_password",
        "pin": "1234",
        "normal_field": "visible_value",
    }
    mock_entry.options = {
        "secret": "top_secret",
        "scan_interval": 30,
    }

    hass.data[DOMAIN] = {}

    diag = await async_get_config_entry_diagnostics(hass, mock_entry)

    # Verify versions
    assert diag["integration_version"] == INTEGRATION_VERSION
    assert "ownd_version" in diag

    # Verify redactions
    assert diag["config_entry"]["entry_id"] == "**REDACTED**"
    assert diag["config_entry"]["title"] == "MyHOME Gateway"  # not the user's title
    assert diag["config_entry"]["data"][CONF_PASSWORD] == "**REDACTED**"
    assert diag["config_entry"]["data"]["pin"] == "**REDACTED**"
    assert diag["config_entry"]["data"]["normal_field"] == "visible_value"
    assert diag["config_entry"]["options"]["secret"] == "**REDACTED**"
    assert diag["config_entry"]["options"]["scan_interval"] == 30

    # Verify empty gateway info
    assert diag["gateway"] == {}
    assert diag["profile"] == {}
    assert diag["queue"] == {}
    assert diag["bus_monitor"] == {}
    assert diag["platforms"] == {}
    assert diag["audio"] == {}


@pytest.mark.asyncio
async def test_diagnostics_with_full_gateway_and_bus_monitor(hass: HomeAssistant):
    """Test diagnostics output when gateway, profile, queue, and bus monitor are active."""
    mac = "00:03:50:aa:bb:cc"
    mock_entry = MagicMock()
    mock_entry.entry_id = "test_full_456"
    mock_entry.version = 1
    mock_entry.domain = DOMAIN
    mock_entry.title = "Living Room Gateway"
    mock_entry.data = {
        CONF_MAC: mac,
        CONF_PASSWORD: "pw",
    }
    mock_entry.options = {}

    # Mock gateway and profile
    mock_profile = MagicMock()
    mock_profile.name = "MH200N"
    mock_profile.command_queue_delay = 0.05
    mock_profile.max_queue_size = 250
    mock_profile.keepalive_interval = 90.0

    mock_gw = MagicMock()
    mock_gw.model_name = "MH200N"
    mock_gw.manufacturer = "BTicino S.p.A."
    mock_gw.firmware = "2.0.1"
    mock_gw.profile = mock_profile

    # Mock send buffer
    mock_send_buffer = MagicMock()
    mock_send_buffer.qsize.return_value = 3
    mock_send_buffer.maxsize = 250

    # Bus monitor
    bus_mon = BusMonitor(maxlen=10)
    bus_mon.record_frame(direction="rx", raw="*1*1*12##")

    # Gateway handler
    mock_handler = MagicMock()
    mock_handler.gateway = mock_gw
    mock_handler.is_connected = True
    mock_handler.sending_workers = [MagicMock(), MagicMock()]
    mock_handler.send_buffer = mock_send_buffer
    mock_handler.bus_monitor = bus_mon

    hass.data[DOMAIN] = {
        mac: {
            CONF_ENTITY: mock_handler,
            CONF_PLATFORMS: {
                "light": {"a": {}, "b": {}},
                "switch": {"c": {}},
            },
        }
    }
    attach_runtime(hass, mock_entry, mac, mock_handler)

    diag = await async_get_config_entry_diagnostics(hass, mock_entry)

    # Verify versions
    assert diag["integration_version"] == INTEGRATION_VERSION
    assert "ownd_version" in diag

    # Verify gateway details
    assert diag["gateway"]["model_name"] == "MH200N"
    assert diag["gateway"]["manufacturer"] == "BTicino S.p.A."
    assert diag["gateway"]["firmware"] == "2.0.1"
    assert diag["gateway"]["is_connected"] is True
    assert diag["gateway"]["send_workers"] == 2

    # Verify profile details
    assert diag["profile"]["name"] == "MH200N"
    assert diag["profile"]["command_queue_delay"] == 0.05
    assert diag["profile"]["max_queue_size"] == 250
    assert diag["profile"]["keepalive_interval"] == 90.0

    # Verify queue details
    assert diag["queue"]["queue_depth"] == 3
    assert diag["queue"]["max_size"] == 250

    # Verify bus monitor details
    assert diag["bus_monitor"]["stats"]["captured"] == 1
    assert len(diag["bus_monitor"]["recent_frames"]) == 1
    assert diag["bus_monitor"]["recent_frames"][0]["raw"] == "*1*1*12##"

    # Verify platform counts
    assert diag["platforms"] == {"light": 2, "switch": 1}


@pytest.mark.asyncio
async def test_diagnostics_export_the_whole_ring_buffer(hass: HomeAssistant):
    """The startup status sweep overflows 100 frames: diagnostics carry the full ring (#429)."""
    mac = "00:03:50:aa:bb:cc"
    mock_entry = MagicMock()
    mock_entry.entry_id = "test_ring_789"
    mock_entry.version = 1
    mock_entry.domain = DOMAIN
    mock_entry.data = {CONF_MAC: mac}
    mock_entry.options = {}

    bus_mon = BusMonitor()
    assert bus_mon.maxlen == DEFAULT_RING_BUFFER_SIZE
    for n in range(DEFAULT_RING_BUFFER_SIZE + 100):
        bus_mon.record_frame(direction="rx", raw=f"*#4*{n}*0*0215*1##")
    mock_handler = MagicMock()
    mock_handler.gateway, mock_handler.send_buffer, mock_handler.bus_monitor = None, None, bus_mon
    attach_runtime(hass, mock_entry, mac, mock_handler)

    diag = await async_get_config_entry_diagnostics(hass, mock_entry)

    frames = diag["bus_monitor"]["recent_frames"]
    assert len(frames) == DEFAULT_RING_BUFFER_SIZE
    # the newest frames, oldest first: the evicted ones are the first 100
    assert frames[0]["raw"] == "*#4*100*0*0215*1##"
    assert frames[-1]["raw"] == f"*#4*{DEFAULT_RING_BUFFER_SIZE + 99}*0*0215*1##"


@pytest.mark.asyncio
async def test_diagnostics_carry_no_household_identity(hass: HomeAssistant):
    """A download is attached to public issues: no LAN address, MAC, SSDP identity, path or title."""
    mock_entry = MagicMock()
    mock_entry.entry_id = "01M284WWKZG4XTEG62NVW1DPVG"
    mock_entry.version = 1
    mock_entry.domain = DOMAIN
    mock_entry.title = "Casa Rossi"
    mock_entry.data = {
        "host": "192.168.1.50",
        "port": 20000,
        CONF_MAC: "00:03:50:24:70:01",
        "id": "00:03:50:a4:11:2e",
        "UDN": "uuid:12345678",
        "ssdp_location": "http://192.168.1.50:49153/description.xml",
        "friendly_name": "Rossi MyHomeServer1",
        CONF_PASSWORD: "12345",
        "name": "MyHomeServer1",
    }
    mock_entry.options = {
        "file_path": "C:/Users/rossi/myhome.yaml", "command_worker_count": 1,
        # decoder slots: the media_player is named after a room, its slot -> source mapping is diagnostics
        "decoder_1_entity": "media_player.rossi_living_room", "decoder_1_source": 2, "decoder_1_pre_gain": 10,
        "decoder_2_entity": "", "decoder_2_source": 2, "decoder_2_pre_gain": 0,
    }
    hass.data[DOMAIN] = {}

    diag = await async_get_config_entry_diagnostics(hass, mock_entry)

    data, options = diag["config_entry"]["data"], diag["config_entry"]["options"]
    for key in ("host", CONF_MAC, "id", "UDN", "ssdp_location", "friendly_name", CONF_PASSWORD):
        assert data[key] == "**REDACTED**", key
    assert options["file_path"] == "**REDACTED**"
    assert data["port"] == 20000 and data["name"] == "MyHomeServer1" and options["command_worker_count"] == 1
    assert options["decoder_1_entity"] == "media_player.decoder_1"  # the slot, not the room
    assert options["decoder_1_source"] == 2 and options["decoder_1_pre_gain"] == 10
    assert options["decoder_2_entity"] == ""  # an unused slot is still visibly unused
    assert mock_entry.options["decoder_1_entity"] == "media_player.rossi_living_room"  # the entry is untouched
    assert diag["config_entry"]["entry_id"] == "**REDACTED**"
    assert diag["config_entry"]["title"] == "MyHOME Gateway"
    text = str(diag)
    assert "192.168" not in text and "Rossi" not in text and "rossi" not in text and "01M284" not in text


@pytest.mark.asyncio
async def test_redaction_is_scoped_to_the_config_entry(hass: HomeAssistant):
    """Review of #335: redaction covers entry data / options only, and breaks no relationship.

    The entry is tied to its gateway through ``entry.runtime_data`` (the raw MAC is
    the legacy key), so the gateway, queue and bus-monitor blocks are still found and arrive whole - a
    frame's ``where`` / ``who`` / ``what`` are what a bug report is about.
    """
    mac = "00:03:50:24:70:01"
    mock_entry = MagicMock()
    mock_entry.entry_id = "01M284WWKZG4XTEG62NVW1DPVG"
    mock_entry.version = 1
    mock_entry.domain = DOMAIN
    mock_entry.title = "Casa Rossi"
    mock_entry.data = {"host": "192.168.1.50", CONF_MAC: mac, "id": mac, "friendly_name": "Rossi F454", "name": "F454"}
    mock_entry.options = {"command_worker_count": 1}

    mock_gw = MagicMock()
    mock_gw.model_name, mock_gw.manufacturer, mock_gw.firmware, mock_gw.profile = "F454", "BTicino S.p.A.", "1.0", None
    bus_mon = BusMonitor(maxlen=10)
    bus_mon.record_frame(direction="rx", raw="*1*1*12##", parsed=OWNMessage.parse("*1*1*12##"))
    mock_handler = MagicMock()
    mock_handler.gateway, mock_handler.is_connected, mock_handler.sending_workers = mock_gw, True, []
    mock_handler.send_buffer, mock_handler.bus_monitor = None, bus_mon
    mock_handler.identification.return_value = {"model": "F454", "source": "manual", "conflict": None}
    hass.data[DOMAIN] = {mac: {CONF_ENTITY: mock_handler, CONF_PLATFORMS: {"light": {"a": {}}}}}
    attach_runtime(hass, mock_entry, mac, mock_handler)

    diag = await async_get_config_entry_diagnostics(hass, mock_entry)

    # the entry side: identity gone, the rest kept
    data = diag["config_entry"]["data"]
    assert data["host"] == data[CONF_MAC] == data["id"] == data["friendly_name"] == "**REDACTED**"
    assert data["name"] == "F454" and diag["config_entry"]["options"] == {"command_worker_count": 1}
    assert mock_entry.data[CONF_MAC] == mac  # redacted on a copy, the entry itself is untouched
    # the gateway side: found through the raw MAC, delivered as built
    assert diag["gateway"] == {"model_name": "F454", "manufacturer": "BTicino S.p.A.", "firmware": "1.0",
                               "is_connected": True, "send_workers": 0,
                               "identification": {"model": "F454", "source": "manual", "conflict": None}}
    assert diag["platforms"] == {"light": 1}
    frame = diag["bus_monitor"]["recent_frames"][0]
    assert (frame["raw"], frame["who"], frame["where"], frame["what"]) == ("*1*1*12##", "1", "12", "1")
    assert "**REDACTED**" not in str(diag["bus_monitor"]) + str(diag["gateway"])


@pytest.mark.asyncio
async def test_topology_inference_diagnostics_standalone(hass: HomeAssistant):
    """Test topology_inference diagnostics section for a single standalone gateway."""
    from pytest_homeassistant_custom_component.common import MockConfigEntry

    entry = MockConfigEntry(
        domain=DOMAIN,
        title="F454 Gateway",
        data={CONF_MAC: "00:03:50:11:22:33", "name": "F454"},
        options={},
    )
    entry.add_to_hass(hass)

    diag = await async_get_config_entry_diagnostics(hass, entry)
    assert "topology_inference" in diag
    top = diag["topology_inference"]
    assert top["target_gateway"]["model"] == "F454"
    assert top["target_gateway"]["hardware_tier"] == 1
    assert top["target_gateway"]["mac"] == "**REDACTED**"
    assert top["target_gateway"]["configured_topology"] == "standalone"
    assert top["target_gateway"]["configured_role"] == "primary"
    assert top["target_gateway"]["configured_primary"] is None
    assert top["peer_count"] == 0
    assert top["evaluations"] == []


@pytest.mark.asyncio
async def test_topology_inference_diagnostics_paired_and_redacted(hass: HomeAssistant):
    """Test topology_inference diagnostics evaluates peers, audio coupling, and alignment while redacting all MACs."""
    from pytest_homeassistant_custom_component.common import MockConfigEntry

    from custom_components.myhome.const import (
        CONF_BUS_TOPOLOGY,
        CONF_DELEGATED_WHOS,
        CONF_GATEWAY_ROLE,
        CONF_PRIMARY_GATEWAY,
        ROLE_PRIMARY,
        ROLE_SECONDARY,
        TOPOLOGY_SHARED,
    )
    from custom_components.myhome.topology import gateway_supported_whos

    pri_mac = "00:03:50:aa:bb:01"
    sec_mac = "00:03:50:aa:bb:02"

    entry_pri = MockConfigEntry(
        domain=DOMAIN,
        title="MyHomeServer1 Gateway",
        data={CONF_MAC: pri_mac, "name": "MyHomeServer1"},
        options={CONF_BUS_TOPOLOGY: TOPOLOGY_SHARED, CONF_GATEWAY_ROLE: ROLE_PRIMARY},
    )
    entry_sec = MockConfigEntry(
        domain=DOMAIN,
        title="H4890 Gateway",
        data={CONF_MAC: sec_mac, "name": "H4890"},
        options={
            CONF_BUS_TOPOLOGY: TOPOLOGY_SHARED,
            CONF_GATEWAY_ROLE: ROLE_SECONDARY,
            CONF_PRIMARY_GATEWAY: pri_mac,
            CONF_DELEGATED_WHOS: [5, 16, 22],
        },
    )
    entry_pri.add_to_hass(hass)
    entry_sec.add_to_hass(hass)

    diag = await async_get_config_entry_diagnostics(hass, entry_sec)
    top = diag["topology_inference"]

    # Target gateway verification
    assert top["target_gateway"]["model"] == "H4890"
    assert top["target_gateway"]["hardware_tier"] == 2
    assert top["target_gateway"]["mac"] == "**REDACTED**"
    assert top["target_gateway"]["configured_topology"] == TOPOLOGY_SHARED
    assert top["target_gateway"]["configured_role"] == ROLE_SECONDARY
    assert top["target_gateway"]["configured_primary"] == "**REDACTED**"
    assert top["target_gateway"]["configured_delegated_whos"] == [5, 16, 22]

    # Peer evaluation
    assert top["peer_count"] == 1
    eval_peer = top["evaluations"][0]
    assert eval_peer["peer_model"] == "MyHomeServer1"
    assert eval_peer["peer_tier"] == 1
    assert eval_peer["peer_mac"] == "**REDACTED**"
    assert eval_peer["primary_mac"] == "**REDACTED**"
    assert eval_peer["secondary_mac"] == "**REDACTED**"
    assert eval_peer["recommended_primary_model"] == "MyHomeServer1"
    assert eval_peer["recommended_secondary_model"] == "H4890"
    assert eval_peer["recommended_role"] == ROLE_SECONDARY
    has_alarm = 5 in gateway_supported_whos("H4890")
    expected_diag = [5, 16, 22] if has_alarm else [16, 22]
    assert eval_peer["delegated_whos"] == expected_diag
    assert eval_peer["audio_coupled"] is False
    assert "unique subsystems" in eval_peer["rationale"]

    # Alignment verification
    align = eval_peer["alignment"]
    assert align["configured_shared_bus"] is True
    assert align["is_recommended_primary"] is False
    assert align["role_aligned"] is True
    assert align["delegated_whos_aligned"] is (True if has_alarm else False)
    assert align["primary_aligned"] is True

    # Strict redaction check: raw MACs must not exist anywhere in topology_inference
    top_str = str(top)
    assert pri_mac not in top_str
    assert sec_mac not in top_str
    assert pri_mac.replace(":", "") not in top_str
    assert sec_mac.replace(":", "") not in top_str


@pytest.mark.asyncio
async def test_diagnostics_audio_block_names_no_room(hass: HomeAssistant):
    """The audio block reports the sound system by bus address and decoder slot, never by room name."""
    from types import SimpleNamespace

    from custom_components.myhome.decoder_pool import DecoderPool

    mock_entry = MagicMock()
    mock_entry.data = {"mac": "00:03:50:11:22:33"}
    mock_entry.options = {"decoder_1_entity": "media_player.cambridge_living_room", "decoder_1_source": 1}
    handler = MagicMock()
    handler.gateway.profile = None
    handler.send_buffer = handler.bus_monitor = None
    runtime = attach_runtime(hass, mock_entry, "00:03:50:11:22:33", handler)

    pool = DecoderPool(
        hass,
        {"media_player.cambridge_living_room": 1},
        {"media_player.cambridge_living_room": 20},
        companion_map={"media_player.cambridge_living_room": "media_player.mxn10_dlna"},
    )
    await pool.claim("media_player.bureau", environment="2")
    await pool.set_group("media_player.bureau", {"media_player.eetkamer": "2"})
    runtime.decoder_pool = pool
    hass.states.async_set("media_player.cambridge_living_room", "playing")

    def zone(where: str, **state: object) -> SimpleNamespace:
        return SimpleNamespace(where=where, diagnostics_state=lambda: state)

    runtime.media_players = {
        "media_player.bureau": zone("21", state="on", source=1, volume_level=0.0, is_volume_muted=False, parked=False),
        "media_player.eetkamer": zone("23", state="off", source=None, volume_level=None, is_volume_muted=False, parked=True),
    }

    audio = (await async_get_config_entry_diagnostics(hass, mock_entry))["audio"]

    assert audio["zones"]["zone_21"]["volume_level"] == 0.0
    assert audio["zones"]["zone_21"]["is_volume_muted"] is False
    assert audio["zones"]["zone_23"]["parked"] is True
    pool_info = audio["decoder_pool"]
    assert pool_info["decoders"]["decoder_1"] == {
        "source": 1,
        "pre_gain_pct": 20,
        "stream_incompatible": False,
        "companion": "decoder_1_companion",
        "companion_chosen_by_user": False,
        "state": "playing",
        "held_by": "zone_21",
    }
    assert pool_info["groups"] == {"zone_21": ["zone_23"]}
    assert pool_info["environments"] == {"zone_21": "2", "zone_23": "2"}
    dump = str(audio)
    for identifying in ("bureau", "eetkamer", "living_room", "mxn10"):
        assert identifying not in dump
