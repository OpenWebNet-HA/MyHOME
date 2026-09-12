"""Comprehensive unit tests covering Phase 1 architecture and hardening:
- GatewayProfile classes, capability introspection, and profile resolver
- Mock gateway harness integration, NACK handling, response latency, and disconnect simulation
- Queue mechanics, max_queue_size enforcement, pacing delay, multi-worker processing, and clean shutdown
- Connection hardening, error recovery, and EOF handling
- Config flow reauth (success & failure modes), options flow validation (media_player, mass rejection, IP check)
- Entity registry migration safety, collision avoidance, and entity_id canonicalization
"""
import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

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
from homeassistant.helpers import entity_registry as er
from OWNd.connection import (
    OWNCommandSession,
    OWNEventSession,
    OWNGateway,
    OWNSession,
)
from OWNd.message import OWNCommand
from OWNd.profiles import (
    WHO_AUTOMATION,
    WHO_ENERGY,
    WHO_LIGHTING,
    WHO_SOUND,
    F454Profile,
    F455Profile,
    GenericGatewayProfile,
    MH200NProfile,
    MH202Profile,
    MyHomeServer1Profile,
    get_gateway_profile,
)
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.myhome.config_flow import MyhomeFlowHandler, MyhomeOptionsFlowHandler
from custom_components.myhome.const import (
    CONF_DECODER_ENTITY,
    CONF_DECODER_PRE_GAIN,
    CONF_DECODER_SOURCE,
    CONF_DEVICE_TYPE,
    CONF_ENTITY,
    CONF_FILE_PATH,
    CONF_FIRMWARE,
    CONF_GENERATE_EVENTS,
    CONF_MANUFACTURER,
    CONF_MANUFACTURER_URL,
    CONF_SSDP_LOCATION,
    CONF_SSDP_ST,
    CONF_TRANSITION_MODE,
    CONF_UDN,
    CONF_WORKER_COUNT,
    DOMAIN,
)
from custom_components.myhome.gateway import MyHOMEGatewayHandler
from tests.mock_gateway_harness import MockGatewayHarness

# ── 1. GatewayProfile Tests ──────────────────────────────────────────────────

class TestGatewayProfiles:
    """Test GatewayProfile classes, capability introspection, and resolution logic."""

    def test_f454_profile(self):
        profile = F454Profile()
        assert profile.model_name == "F454"
        assert profile.max_workers == 4
        assert profile.max_command_workers == 4
        assert profile.default_workers == 1
        assert profile.supports_hmac is True
        assert profile.supports_native_transitions is True
        assert profile.supports_extended_frames is True
        assert profile.command_queue_delay == 0.05
        assert profile.max_queue_size == 250
        assert profile.display_name == "F454 Gateway"
        assert profile.supports_who(WHO_LIGHTING) is True
        assert profile.supports_who(WHO_SOUND) is True
        assert profile.supports_who(999) is False
        assert profile.can_support_workers(1) is True
        assert profile.can_support_workers(4) is True
        assert profile.can_support_workers(5) is False
        assert profile.can_support_workers(0) is False

    def test_f455_profile(self):
        profile = F455Profile()
        assert profile.model_name == "F455"
        assert profile.max_workers == 4
        assert profile.max_command_workers == 4
        assert profile.supports_audio is True
        assert profile.supports_extended_frames is True
        assert profile.command_queue_delay == 0.05
        assert profile.supports_who(WHO_SOUND) is True

    def test_mh200n_profile(self):
        profile = MH200NProfile()
        assert profile.model_name == "MH200N"
        assert profile.max_workers == 1
        assert profile.max_command_workers == 1
        assert profile.supports_hmac is False
        assert profile.supports_native_transitions is False
        assert profile.supports_extended_frames is False
        assert profile.max_queue_size == 100
        assert profile.command_queue_delay == 0.15
        assert profile.supports_audio is False
        assert profile.supports_energy_instant_power is False
        # MH200N does not support audio or energy
        assert profile.supports_who(WHO_LIGHTING) is True
        assert profile.supports_who(WHO_AUTOMATION) is True
        assert profile.supports_who(WHO_SOUND) is False
        assert profile.supports_who(WHO_ENERGY) is False
        assert profile.can_support_workers(1) is True
        assert profile.can_support_workers(2) is False

    def test_mh202_profile(self):
        profile = MH202Profile()
        assert profile.model_name == "MH202"
        assert profile.max_workers == 2
        assert profile.max_command_workers == 2
        assert profile.supports_hmac is True
        assert profile.supports_extended_frames is True
        assert profile.command_queue_delay == 0.10
        assert profile.can_support_workers(2) is True
        assert profile.can_support_workers(3) is False

    def test_myhomeserver1_profile(self):
        profile = MyHomeServer1Profile()
        assert profile.model_name == "MyHomeServer1"
        assert profile.max_workers == 4
        assert profile.max_command_workers == 4
        assert profile.default_workers == 2
        assert profile.supports_native_transitions is True
        assert profile.supports_extended_frames is True
        assert profile.max_queue_size == 300
        assert profile.command_queue_delay == 0.02
        assert profile.can_support_workers(4) is True
        assert profile.can_support_workers(5) is False

    def test_generic_profile(self):
        profile = GenericGatewayProfile("CustomBox")
        assert profile.model_name == "CustomBox"
        assert profile.max_workers == 1
        assert profile.max_command_workers == 1
        assert profile.command_queue_delay == 0.05
        assert profile.supports_extended_frames is False
        assert profile.display_name == "CustomBox Gateway"
        assert profile.can_support_workers(1) is True
        assert profile.can_support_workers(2) is False

    @pytest.mark.parametrize(
        "name,expected_cls",
        [
            ("F454", F454Profile),
            ("f454", F454Profile),
            ("F 454", F454Profile),
            ("F-454", F454Profile),
            ("F455", F455Profile),
            ("MH200N", MH200NProfile),
            ("mh200", MH200NProfile),
            ("MH-200-N", MH200NProfile),
            ("MH202", MH202Profile),
            ("MyHomeServer1", MyHomeServer1Profile),
            ("mhs1", MyHomeServer1Profile),
            ("Unknown_Model", GenericGatewayProfile),
            ("", GenericGatewayProfile),
            (None, GenericGatewayProfile),
        ],
    )
    def test_get_gateway_profile_resolution(self, name, expected_cls):
        profile = get_gateway_profile(name)
        assert isinstance(profile, expected_cls)

    def test_owngateway_profile_integration(self):
        gw = OWNGateway({
            "address": "192.168.1.100",
            "modelName": "F454",
            "serialNumber": "00:11:22:33:44:55",
        })
        assert isinstance(gw.profile, F454Profile)
        assert gw.model == "F454"
        assert gw.profile.max_command_workers == 4

    def test_owngateway_profile_fallback_when_model_missing(self):
        gw = OWNGateway({
            "address": "192.168.1.101",
            "serialNumber": "00:11:22:33:44:66",
        })
        assert isinstance(gw.profile, GenericGatewayProfile)
        assert gw.profile.model_name == "Unknown model"


# ── 2. Mock Gateway Harness Tests ────────────────────────────────────────────

class TestMockGatewayHarness:
    """Test MockGatewayHarness server and protocol interaction."""

    @pytest.mark.asyncio
    async def test_harness_lifecycle_and_command_session(self):
        harness = MockGatewayHarness()
        port = await harness.start()
        assert port > 0

        gw = OWNGateway({
            "address": "127.0.0.1",
            "port": port,
            "password": None,
            "modelName": "F454",
            "serialNumber": "00:03:50:00:12:34",
        })

        session = OWNCommandSession(gateway=gw, logger=MagicMock())
        connected = await session.connect()
        assert connected["Success"] is True

        # Send command through harness
        cmd = OWNCommand.parse("*1*1*21##")
        await session.send(cmd)
        assert "*1*1*21##" in harness.received_messages

        await session.close()
        await harness.stop()

    @pytest.mark.asyncio
    async def test_harness_event_broadcast_multiple_clients(self):
        harness = MockGatewayHarness()
        port = await harness.start()

        gw = OWNGateway({
            "address": "127.0.0.1",
            "port": port,
            "password": None,
            "modelName": "F454",
            "serialNumber": "00:03:50:00:12:34",
        })

        session1 = OWNEventSession(gateway=gw, logger=MagicMock())
        session2 = OWNEventSession(gateway=gw, logger=MagicMock())
        assert (await session1.connect())["Success"] is True
        assert (await session2.connect())["Success"] is True

        await harness.broadcast_event("*1*1*22##")
        msg1 = await asyncio.wait_for(session1.get_next(), timeout=2.0)
        msg2 = await asyncio.wait_for(session2.get_next(), timeout=2.0)
        assert str(msg1) == "*1*1*22##"
        assert str(msg2) == "*1*1*22##"

        await session1.close()
        await session2.close()
        await harness.stop()

    @pytest.mark.asyncio
    async def test_harness_disconnect_on_connect(self):
        harness = MockGatewayHarness()
        harness.set_disconnect_on_connect(True)
        port = await harness.start()

        gw = OWNGateway({
            "address": "127.0.0.1",
            "port": port,
            "password": None,
            "modelName": "F454",
            "serialNumber": "00:03:50:00:12:34",
        })

        session = OWNSession(gateway=gw, logger=MagicMock())
        res = await session.test_connection()
        assert res["Success"] is False

        await harness.stop()

    @pytest.mark.asyncio
    async def test_harness_nack_simulation(self):
        harness = MockGatewayHarness()
        harness.set_nack_commands(True)
        port = await harness.start()

        gw = OWNGateway({
            "address": "127.0.0.1",
            "port": port,
            "password": None,
            "modelName": "F454",
            "serialNumber": "00:03:50:00:12:34",
        })

        mock_logger = MagicMock()
        session = OWNCommandSession(gateway=gw, logger=mock_logger)
        assert (await session.connect())["Success"] is True

        try:
            # An explicit NACK retries once, then reports rejection accurately.
            cmd = OWNCommand.parse("*1*1*21##")
            assert await session.send(cmd) is None
            assert harness.received_messages.count("*1*1*21##") == 2
            mock_logger.error.assert_called_with(
                "%s Could not send message `%s`. No more retries.",
                gw.log_id, cmd,
            )

            # An explicit NACK for status request does NOT retry or warn (logged at DEBUG)
            status_cmd = OWNCommand.parse("*#16*0##")
            assert await session.send(status_cmd, is_status_request=True) is None
            assert harness.received_messages.count("*#16*0##") == 2
            mock_logger.debug.assert_any_call(
                "%s Gateway rejected status request %s (NACK, %s response(s)). Subsystem or device may not be present.",
                gw.log_id, status_cmd, 0,
            )
            status_warnings = [
                call for call in mock_logger.warning.call_args_list
                if str(status_cmd) in str(call)
            ]
            assert len(status_warnings) == 0
        finally:
            await session.close()
            await harness.stop()

    @pytest.mark.asyncio
    async def test_harness_disconnect_simulation(self):
        harness = MockGatewayHarness()
        port = await harness.start()

        gw = OWNGateway({
            "address": "127.0.0.1",
            "port": port,
            "password": None,
            "modelName": "F454",
            "serialNumber": "00:03:50:00:12:34",
        })

        session = OWNCommandSession(gateway=gw, logger=MagicMock())
        assert (await session.connect())["Success"] is True
        assert len(harness.connected_clients) == 1

        cmd1 = OWNCommand.parse("*1*1*21##")
        await session.send(cmd1)

        # Server severs connected clients
        await harness.disconnect_all_clients()
        assert len(harness.connected_clients) == 0

        await session.close()
        await harness.stop()

    @pytest.mark.asyncio
    async def test_harness_reset(self):
        harness = MockGatewayHarness()
        harness.set_disconnect_on_connect(True)
        harness.set_nack_commands(True)
        harness.received_messages.append("dummy")
        harness.reset()

        assert harness._disconnect_on_connect is False
        assert harness._nack_commands is False
        assert len(harness.received_messages) == 0


# ── 3. Queue Mechanics & Multi-Worker Processing ─────────────────────────────

class TestQueueMechanics:
    """Test command queueing, max_queue_size enforcement, pacing delay, and clean shutdown."""

    def test_queue_max_size_from_profile(self):
        # F454 profile -> 250
        entry_f454 = MagicMock()
        entry_f454.data = {
            CONF_HOST: "192.168.1.5",
            CONF_PORT: 20000,
            CONF_PASSWORD: "pass",
            CONF_SSDP_LOCATION: "",
            CONF_SSDP_ST: "",
            CONF_DEVICE_TYPE: "",
            CONF_FRIENDLY_NAME: "",
            CONF_MANUFACTURER: "",
            CONF_MANUFACTURER_URL: "",
            CONF_NAME: "F454",
            CONF_FIRMWARE: "1.0",
            CONF_MAC: "00:11:22:33:44:55",
            CONF_UDN: "123",
        }
        h_f454 = MyHOMEGatewayHandler(MagicMock(), entry_f454)
        assert h_f454.send_buffer.maxsize == 250

        # MH200N profile -> 100
        entry_mh200n = MagicMock()
        entry_mh200n.data = dict(entry_f454.data)
        entry_mh200n.data[CONF_NAME] = "MH200N"
        h_mh200n = MyHOMEGatewayHandler(MagicMock(), entry_mh200n)
        assert h_mh200n.send_buffer.maxsize == 100

        # MyHomeServer1 profile -> 300
        entry_mhs1 = MagicMock()
        entry_mhs1.data = dict(entry_f454.data)
        entry_mhs1.data[CONF_NAME] = "MyHomeServer1"
        h_mhs1 = MyHOMEGatewayHandler(MagicMock(), entry_mhs1)
        assert h_mhs1.send_buffer.maxsize == 300

    @pytest.mark.asyncio
    async def test_send_and_send_status_request_queueing(self):
        mock_entry = MagicMock()
        mock_entry.data = {
            CONF_HOST: "192.168.1.5",
            CONF_PORT: 20000,
            CONF_PASSWORD: "pass",
            CONF_SSDP_LOCATION: "",
            CONF_SSDP_ST: "",
            CONF_DEVICE_TYPE: "",
            CONF_FRIENDLY_NAME: "",
            CONF_MANUFACTURER: "",
            CONF_MANUFACTURER_URL: "",
            CONF_NAME: "F454",
            CONF_FIRMWARE: "1.0",
            CONF_MAC: "00:11:22:33:44:55",
            CONF_UDN: "123",
        }
        mock_hass = MagicMock()
        handler = MyHOMEGatewayHandler(mock_hass, mock_entry)

        cmd1 = OWNCommand.parse("*1*1*11##")
        cmd2 = OWNCommand.parse("*#1*11##")

        await handler.send(cmd1)
        assert handler.send_buffer.qsize() == 1
        item1 = await handler.send_buffer.get()
        assert item1["message"] == cmd1
        assert item1["is_status_request"] is False

        await handler.send_status_request(cmd2)
        assert handler.send_buffer.qsize() == 1
        item2 = await handler.send_buffer.get()
        assert item2["message"] == cmd2
        assert item2["is_status_request"] is True

    @pytest.mark.asyncio
    async def test_sending_loop_worker_execution_with_pacing(self):
        mock_entry = MagicMock()
        mock_entry.data = {
            CONF_HOST: "192.168.1.5",
            CONF_PORT: 20000,
            CONF_PASSWORD: "pass",
            CONF_SSDP_LOCATION: "",
            CONF_SSDP_ST: "",
            CONF_DEVICE_TYPE: "",
            CONF_FRIENDLY_NAME: "",
            CONF_MANUFACTURER: "",
            CONF_MANUFACTURER_URL: "",
            CONF_NAME: "F454",
            CONF_FIRMWARE: "1.0",
            CONF_MAC: "00:11:22:33:44:55",
            CONF_UDN: "123",
        }
        mock_hass = MagicMock()
        handler = MyHOMEGatewayHandler(mock_hass, mock_entry)

        mock_task = MagicMock()
        mock_task.cancel = MagicMock()
        mock_task.done = MagicMock(return_value=False)
        handler.sending_workers = [mock_task]

        with patch("custom_components.myhome.gateway.OWNCommandSession") as mock_session_cls:
            mock_session = MagicMock()
            mock_session.connect = AsyncMock(return_value=True)
            mock_session.send = AsyncMock(return_value=True)
            mock_session.close = AsyncMock()
            mock_session_cls.return_value = mock_session

            # Queue two commands
            cmd1 = OWNCommand.parse("*1*1*21##")
            cmd2 = OWNCommand.parse("*1*0*21##")
            await handler.send(cmd1)
            await handler.send(cmd2)
            handler._event_session_ready.set()

            worker_task = asyncio.create_task(handler.sending_loop(0))
            await asyncio.sleep(0.08)

            # Signal shutdown via sentinel None
            await handler.close_listener()
            await asyncio.wait_for(worker_task, timeout=1.0)

            assert mock_session.send.call_count >= 2
            mock_session.close.assert_called_once()
            assert worker_task.done() is True

    @pytest.mark.asyncio
    async def test_multi_worker_concurrent_queue_dispatch(self):
        mock_entry = MagicMock()
        mock_entry.data = {
            CONF_HOST: "192.168.1.5",
            CONF_PORT: 20000,
            CONF_PASSWORD: "pass",
            CONF_SSDP_LOCATION: "",
            CONF_SSDP_ST: "",
            CONF_DEVICE_TYPE: "",
            CONF_FRIENDLY_NAME: "",
            CONF_MANUFACTURER: "",
            CONF_MANUFACTURER_URL: "",
            CONF_NAME: "F454",
            CONF_FIRMWARE: "1.0",
            CONF_MAC: "00:11:22:33:44:55",
            CONF_UDN: "123",
        }
        mock_hass = MagicMock()
        handler = MyHOMEGatewayHandler(mock_hass, mock_entry)

        sent_messages = []
        with patch("custom_components.myhome.gateway.OWNCommandSession") as mock_session_cls:
            def create_mock_session(*args, **kwargs):
                s = MagicMock()
                s.connect = AsyncMock(return_value=True)
                async def mock_send(message, is_status_request=False):
                    sent_messages.append(str(message))
                    await asyncio.sleep(0.01)
                    return True
                s.send = mock_send
                s.close = AsyncMock()
                return s

            mock_session_cls.side_effect = create_mock_session

            # Queue 6 commands
            for i in range(6):
                await handler.send(OWNCommand.parse(f"*1*1*{10 + i}##"))
            handler._event_session_ready.set()

            # Spin up 3 workers
            t0 = asyncio.create_task(handler.sending_loop(0))
            t1 = asyncio.create_task(handler.sending_loop(1))
            t2 = asyncio.create_task(handler.sending_loop(2))
            handler.sending_workers = [t0, t1, t2]

            # Wait until queue drains
            await asyncio.sleep(0.15)
            assert handler.send_buffer.qsize() == 0
            assert len(sent_messages) == 6

            # Terminate workers gracefully
            await handler.close_listener()
            await asyncio.gather(t0, t1, t2)

    @pytest.mark.asyncio
    async def test_close_listener_unblocks_idle_workers(self):
        mock_entry = MagicMock()
        mock_entry.data = {
            CONF_HOST: "192.168.1.5",
            CONF_PORT: 20000,
            CONF_PASSWORD: "pass",
            CONF_SSDP_LOCATION: "",
            CONF_SSDP_ST: "",
            CONF_DEVICE_TYPE: "",
            CONF_FRIENDLY_NAME: "",
            CONF_MANUFACTURER: "",
            CONF_MANUFACTURER_URL: "",
            CONF_NAME: "F454",
            CONF_FIRMWARE: "1.0",
            CONF_MAC: "00:11:22:33:44:55",
            CONF_UDN: "123",
        }
        handler = MyHOMEGatewayHandler(MagicMock(), mock_entry)

        with patch("custom_components.myhome.gateway.OWNCommandSession") as mock_session_cls:
            mock_session = MagicMock()
            mock_session.connect = AsyncMock(return_value=True)
            mock_session.close = AsyncMock()
            mock_session_cls.return_value = mock_session
            handler._event_session_ready.set()

            worker = asyncio.create_task(handler.sending_loop(0))
            handler.sending_workers = [worker]
            await asyncio.sleep(0.02)

            # Queue is empty, worker is blocked waiting for an item
            assert not worker.done()

            # close_listener unblocks it via None sentinel
            await handler.close_listener()
            await asyncio.wait_for(worker, timeout=1.0)
            assert worker.done()


# ── 4. Connection Hardening Tests ────────────────────────────────────────────

class TestConnectionHardening:
    """Test connection error handling, timeouts, EOF, and teardown safety."""

    @pytest.mark.asyncio
    async def test_connect_timeout_handling(self):
        gw = OWNGateway({
            "address": "192.0.2.1",
            "port": 20000,
            "password": None,
            "modelName": "F454",
            "serialNumber": "00:03:50:00:12:34",
        })

        session = OWNSession(gateway=gw, logger=MagicMock())
        with patch("asyncio.open_connection", side_effect=asyncio.TimeoutError()), \
             patch("asyncio.sleep", new_callable=AsyncMock):
            result = await session.test_connection()
        assert result == {"Success": False, "Message": "connection_error"}

    @pytest.mark.asyncio
    async def test_connect_connection_refused(self):
        gw = OWNGateway({
            "address": "127.0.0.1",
            "port": 20000,
            "password": None,
            "modelName": "F454",
            "serialNumber": "00:03:50:00:12:34",
        })

        session = OWNSession(gateway=gw, logger=MagicMock())
        with patch("asyncio.open_connection", side_effect=ConnectionRefusedError("Connection refused")), \
             patch("asyncio.sleep", new_callable=AsyncMock):
            res = await session.test_connection()
            assert res == {"Success": False, "Message": "connection_error"}

    @pytest.mark.asyncio
    async def test_own_command_session_send_when_disconnected(self):
        gw = OWNGateway({
            "address": "127.0.0.1",
            "port": 20000,
            "password": None,
            "modelName": "F454",
            "serialNumber": "00:03:50:00:12:34",
        })
        session = OWNCommandSession(gateway=gw, logger=MagicMock())
        # An unavailable gateway must not attempt a write on a missing stream.
        with patch.object(session, "connect", return_value={"Success": False}):
            res = await session.send(OWNCommand.parse("*1*1*21##"))
        assert res is None

    @pytest.mark.asyncio
    async def test_session_close_safe_when_unconnected(self):
        gw = OWNGateway({
            "address": "127.0.0.1",
            "port": 20000,
            "modelName": "F454",
            "serialNumber": "00:03:50:00:12:34",
        })
        session = OWNSession(gateway=gw, logger=MagicMock())
        assert session._stream_writer is None
        # Must not raise AttributeError
        await session.close()


# ── 5. Config Flow Reauth & Options Flow Tests ───────────────────────────────

class TestConfigFlowHardening:
    """Test config flow reauth preserving metadata and options flow options handling."""

    @pytest.mark.asyncio
    async def test_async_step_reauth_preserves_discovery_data(self, hass: HomeAssistant):
        mac = "00:03:50:00:12:99"
        old_data = {
            CONF_HOST: "192.168.1.50",
            CONF_PORT: 20000,
            CONF_PASSWORD: "old_password",
            CONF_MAC: mac,
            CONF_SSDP_LOCATION: "http://192.168.1.50:49153/description.xml",
            CONF_SSDP_ST: "urn:schemas-upnp-org:device:Basic:1",
            CONF_DEVICE_TYPE: "urn:schemas-upnp-org:device:Basic:1",
            CONF_FRIENDLY_NAME: "My Gateway",
            CONF_MANUFACTURER: "BTicino S.p.A.",
            CONF_MANUFACTURER_URL: "http://www.bticino.com",
            CONF_NAME: "F454",
            CONF_FIRMWARE: "2.0.0",
            CONF_UDN: "uuid:1234",
        }
        entry = MockConfigEntry(domain=DOMAIN, data=old_data, unique_id=mac)
        entry.add_to_hass(hass)

        flow = MyhomeFlowHandler()
        flow.hass = hass
        flow.context = {"source": "reauth", "entry_id": entry.entry_id}

        # Trigger reauth
        res = await flow.async_step_reauth(config=old_data)
        assert res["type"] == "form"
        assert res["step_id"] == "password"
        assert flow._existing_entry == entry

        # Submit new password with successful connection
        with patch(
            "custom_components.myhome.config_flow.OWNSession.test_connection",
            return_value={"Success": True, "Message": None},
        ), patch("custom_components.myhome.config_flow.OWNGateway.find_from_address") as mock_find:
            mock_gw = MagicMock()
            mock_gw.password = "new_password"
            mock_gw.address = "192.168.1.50"
            mock_gw.serial = mac
            mock_gw.model_name = "F454"
            mock_gw.port = 20000
            mock_gw.udn = "uuid:1234"
            mock_find.return_value = mock_gw

            res2 = await flow.async_step_password(user_input={CONF_PASSWORD: "new_password"})
            assert res2["type"] == "abort"
            assert res2["reason"] == "reauth_successful"

            # Verify entry has updated password while retaining existing metadata
            updated = hass.config_entries.async_get_entry(entry.entry_id)
            assert updated.data[CONF_PASSWORD] == "new_password"
            assert updated.data[CONF_HOST] == "192.168.1.50"
            assert updated.data[CONF_SSDP_LOCATION] == "http://192.168.1.50:49153/description.xml"

    @pytest.mark.asyncio
    async def test_async_step_reauth_wrong_password_shows_error(self, hass: HomeAssistant):
        mac = "00:03:50:00:12:98"
        entry = MockConfigEntry(domain=DOMAIN, data={CONF_MAC: mac, CONF_HOST: "192.168.1.50", CONF_PASSWORD: "p"}, unique_id=mac)
        entry.add_to_hass(hass)

        flow = MyhomeFlowHandler()
        flow.hass = hass
        flow.context = {"source": "reauth", "entry_id": entry.entry_id}

        await flow.async_step_reauth(config=entry.data)

        with patch(
            "custom_components.myhome.config_flow.OWNSession.test_connection",
            return_value={"Success": False, "Message": "password_error"},
        ):
            res = await flow.async_step_password(user_input={CONF_PASSWORD: "bad_password"})
            assert res["type"] == "form"
            assert res["errors"]["password"] == "password_error"

    @pytest.mark.asyncio
    async def test_options_flow_transition_mode_and_decoders(self, hass: HomeAssistant):
        mac = "00:03:50:00:12:88"
        entry = MockConfigEntry(
            domain=DOMAIN,
            data={CONF_MAC: mac, CONF_HOST: "192.168.1.50", CONF_PORT: 20000, CONF_PASSWORD: "p"},
            options={CONF_WORKER_COUNT: 1, CONF_GENERATE_EVENTS: False},
            unique_id=mac,
        )
        entry.add_to_hass(hass)

        options_flow = MyhomeOptionsFlowHandler(entry)
        options_flow.hass = hass

        init_res = await options_flow.async_step_init()
        assert init_res["type"] == "form"
        assert init_res["step_id"] == "user"

        user_input = {
            "address": "192.168.1.50",
            "own_password": "p",
            CONF_WORKER_COUNT: 3,
            CONF_GENERATE_EVENTS: True,
            CONF_TRANSITION_MODE: "software_stepped",
            CONF_DECODER_ENTITY.format(1): "media_player.living_room_decoder",
            CONF_DECODER_SOURCE.format(1): 1,
            CONF_DECODER_PRE_GAIN.format(1): 0,
            CONF_DECODER_ENTITY.format(2): "",
            CONF_DECODER_SOURCE.format(2): 2,
            CONF_DECODER_PRE_GAIN.format(2): 0,
            CONF_DECODER_ENTITY.format(3): "",
            CONF_DECODER_SOURCE.format(3): 3,
            CONF_DECODER_PRE_GAIN.format(3): 0,
            CONF_DECODER_ENTITY.format(4): "",
            CONF_DECODER_SOURCE.format(4): 4,
            CONF_DECODER_PRE_GAIN.format(4): 0,
        }

        save_res = await options_flow.async_step_user(user_input=user_input)
        assert save_res["type"] == "create_entry"
        assert save_res["data"][CONF_WORKER_COUNT] == 3
        assert save_res["data"][CONF_GENERATE_EVENTS] is True
        assert save_res["data"][CONF_TRANSITION_MODE] == "software_stepped"
        assert save_res["data"][CONF_DECODER_ENTITY.format(1)] == "media_player.living_room_decoder"

    @pytest.mark.asyncio
    async def test_options_flow_rejects_invalid_decoder_and_mass(self, hass: HomeAssistant):
        mac = "00:03:50:00:12:87"
        entry = MockConfigEntry(domain=DOMAIN, data={CONF_MAC: mac, CONF_HOST: "192.168.1.50"}, options={}, unique_id=mac)
        entry.add_to_hass(hass)

        entity_registry = er.async_get(hass)
        entity_registry.async_get_or_create("media_player", "mass", "player_123", suggested_object_id="mass_speaker")

        options_flow = MyhomeOptionsFlowHandler(entry)
        options_flow.hass = hass

        # 1. Non-media_player entity rejection
        bad_input = {
            "address": "192.168.1.50",
            "own_password": "p",
            CONF_WORKER_COUNT: 1,
            CONF_GENERATE_EVENTS: False,
            CONF_DECODER_ENTITY.format(1): "light.keuken",
        }
        res = await options_flow.async_step_user(user_input=bad_input)
        assert res["type"] == "form"
        assert res["errors"][CONF_DECODER_ENTITY.format(1)] == "not_a_media_player"

        # 2. Music Assistant entity rejection
        mass_input = {
            "address": "192.168.1.50",
            "own_password": "p",
            CONF_WORKER_COUNT: 1,
            CONF_GENERATE_EVENTS: False,
            CONF_DECODER_ENTITY.format(1): "media_player.mass_speaker",
        }
        res2 = await options_flow.async_step_user(user_input=mass_input)
        assert res2["type"] == "form"
        assert res2["errors"][CONF_DECODER_ENTITY.format(1)] == "mass_entity_not_allowed"

    @pytest.mark.asyncio
    async def test_options_flow_rejects_invalid_ip(self, hass: HomeAssistant):
        mac = "00:03:50:00:12:86"
        entry = MockConfigEntry(domain=DOMAIN, data={CONF_MAC: mac, CONF_HOST: "192.168.1.50"}, options={}, unique_id=mac)
        entry.add_to_hass(hass)

        options_flow = MyhomeOptionsFlowHandler(entry)
        options_flow.hass = hass

        bad_ip_input = {
            "address": "not_an_ip",
            "own_password": "p",
            CONF_WORKER_COUNT: 1,
            CONF_GENERATE_EVENTS: False,
        }
        res = await options_flow.async_step_user(user_input=bad_ip_input)
        assert res["type"] == "form"
        assert res["errors"]["address"] == "invalid_ip"

    def test_options_flow_config_entry_property_fallback(self, hass: HomeAssistant):
        entry = MockConfigEntry(domain=DOMAIN, data={CONF_MAC: "00:11:22:33:44:55"}, options={})
        entry.add_to_hass(hass)

        flow = MyhomeOptionsFlowHandler()
        flow.hass = hass
        flow.handler = entry.entry_id
        assert flow.config_entry == entry


# ── 6. Entity Registry Migration Safety Tests ────────────────────────────────

class TestEntityRegistryMigrationSafety:
    """Test safety, collision handling, and ID alignment during async_setup_entry migration."""

    @pytest.mark.asyncio
    async def test_migration_old_unique_id_mac_where_to_mac_who_where(self, hass: HomeAssistant):
        mac = "00:03:50:00:12:34"
        entry = MockConfigEntry(
            domain=DOMAIN,
            data={
                CONF_HOST: "192.168.0.35",
                CONF_PORT: 20000,
                CONF_PASSWORD: "pass",
                CONF_MAC: mac,
                CONF_SSDP_LOCATION: "http://192.168.0.35:49153/description.xml",
                CONF_SSDP_ST: "urn:schemas-upnp-org:device:Basic:1",
                CONF_DEVICE_TYPE: "urn:schemas-upnp-org:device:Basic:1",
                CONF_FRIENDLY_NAME: "MyHOME Gateway",
                CONF_MANUFACTURER: "BTicino",
                CONF_MANUFACTURER_URL: "http://www.bticino.com",
                CONF_NAME: "F454",
                CONF_FIRMWARE: "2.0.0",
                CONF_UDN: "uuid:12345678",
            },
            unique_id=mac,
        )
        entry.add_to_hass(hass)

        entity_registry = er.async_get(hass)
        # Pre-populate old format unique_id: MAC-WHERE for light, cover, switch, media_player with custom entity_ids and names
        entity_registry.async_get_or_create("light", DOMAIN, f"{mac}-21", config_entry=entry, suggested_object_id="keuken_lamp")
        entity_registry.async_update_entity("light.keuken_lamp", name="Keuken Plafond")
        # Pre-populate an entity with custom entity_id but WITHOUT a custom friendly name (name=None)
        entity_registry.async_get_or_create("light", DOMAIN, f"{mac}-22", config_entry=entry, suggested_object_id="gang_lamp")
        entity_registry.async_get_or_create("cover", DOMAIN, f"{mac}-31", config_entry=entry, suggested_object_id="rolluik_salon")
        entity_registry.async_update_entity("cover.rolluik_salon", name="Zijraam Rolluik")
        entity_registry.async_get_or_create("switch", DOMAIN, f"{mac}-41", config_entry=entry, suggested_object_id="socket_tuin")
        entity_registry.async_update_entity("switch.socket_tuin", name="Tuin Stopcontact")
        entity_registry.async_get_or_create("media_player", DOMAIN, f"{mac}-1", config_entry=entry, suggested_object_id="zone_living")
        entity_registry.async_update_entity("media_player.zone_living", name="Woonkamer Audio")

        with patch("custom_components.myhome.gateway.OWNSession.test_connection", return_value={"Success": True, "Message": None}), \
             patch("custom_components.myhome.gateway.MyHOMEGatewayHandler.listening_loop"), \
             patch("custom_components.myhome.gateway.MyHOMEGatewayHandler.sending_loop"):
            assert await hass.config_entries.async_setup(entry.entry_id)
            await hass.async_block_till_done()

        # Check migrated unique IDs: MAC-WHO-WHERE, verifying custom entity IDs and names are preserved in registry
        migrated_light = entity_registry.async_get("light.keuken_lamp")
        assert migrated_light is not None
        assert migrated_light.unique_id == f"{mac}-1-21"
        assert migrated_light.entity_id == "light.keuken_lamp"
        assert migrated_light.name == "Keuken Plafond"
        assert entity_registry.async_get("light.light_21") is None

        migrated_light_no_name = entity_registry.async_get("light.gang_lamp")
        assert migrated_light_no_name is not None
        assert migrated_light_no_name.unique_id == f"{mac}-1-22"
        assert migrated_light_no_name.entity_id == "light.gang_lamp"
        assert migrated_light_no_name.name is None
        assert entity_registry.async_get("light.light_22") is None

        migrated_cover = entity_registry.async_get("cover.rolluik_salon")
        assert migrated_cover is not None
        assert migrated_cover.unique_id == f"{mac}-2-31"
        assert migrated_cover.entity_id == "cover.rolluik_salon"
        assert migrated_cover.name == "Zijraam Rolluik"
        assert entity_registry.async_get("cover.cover_31") is None

        migrated_switch = entity_registry.async_get("switch.socket_tuin")
        assert migrated_switch is not None
        assert migrated_switch.unique_id == f"{mac}-1-41"
        assert migrated_switch.entity_id == "switch.socket_tuin"
        assert migrated_switch.name == "Tuin Stopcontact"
        assert entity_registry.async_get("switch.switch_41") is None

        migrated_audio = entity_registry.async_get("media_player.zone_living")
        assert migrated_audio is not None
        assert migrated_audio.unique_id == f"{mac}-16-1"
        assert migrated_audio.entity_id == "media_player.zone_living"
        assert migrated_audio.name == "Woonkamer Audio"
        assert entity_registry.async_get("media_player.media_player_1") is None

        # Verify active runtime state machine retains custom entity IDs and names
        state_light = hass.states.get("light.keuken_lamp")
        assert state_light is not None
        assert state_light.name == "Keuken Plafond"
        assert hass.states.get("light.light_21") is None

        state_light_no_name = hass.states.get("light.gang_lamp")
        assert state_light_no_name is not None
        assert hass.states.get("light.light_22") is None

        state_cover = hass.states.get("cover.rolluik_salon")
        assert state_cover is not None
        assert state_cover.name == "Zijraam Rolluik"
        assert hass.states.get("cover.cover_31") is None

        state_audio = hass.states.get("media_player.zone_living")
        assert state_audio is not None
        assert state_audio.name == "Woonkamer Audio"
        assert hass.states.get("media_player.media_player_1") is None

        await hass.config_entries.async_unload(entry.entry_id)

    @pytest.mark.asyncio
    async def test_migration_collision_skips_safely(self, hass: HomeAssistant):
        mac = "00:03:50:00:12:34"
        entry = MockConfigEntry(
            domain=DOMAIN,
            data={
                CONF_HOST: "192.168.0.35",
                CONF_PORT: 20000,
                CONF_PASSWORD: "pass",
                CONF_MAC: mac,
                CONF_SSDP_LOCATION: "http://192.168.0.35:49153/description.xml",
                CONF_SSDP_ST: "urn:schemas-upnp-org:device:Basic:1",
                CONF_DEVICE_TYPE: "urn:schemas-upnp-org:device:Basic:1",
                CONF_FRIENDLY_NAME: "MyHOME Gateway",
                CONF_MANUFACTURER: "BTicino",
                CONF_MANUFACTURER_URL: "http://www.bticino.com",
                CONF_NAME: "F454",
                CONF_FIRMWARE: "2.0.0",
                CONF_UDN: "uuid:12345678",
            },
            unique_id=mac,
        )
        entry.add_to_hass(hass)

        entity_registry = er.async_get(hass)
        # Target unique_id already exists
        target_entity = entity_registry.async_get_or_create(
            "light", DOMAIN, f"{mac}-1-25", config_entry=entry, suggested_object_id="light_25"
        )
        # Orphan entity with old unique_id targeting the same
        orphan_entity = entity_registry.async_get_or_create(
            "light", DOMAIN, f"{mac}-25", config_entry=entry, suggested_object_id="keuken"
        )

        with patch("custom_components.myhome.gateway.OWNSession.test_connection", return_value={"Success": True, "Message": None}), \
             patch("custom_components.myhome.gateway.MyHOMEGatewayHandler.listening_loop"), \
             patch("custom_components.myhome.gateway.MyHOMEGatewayHandler.sending_loop"):
            assert await hass.config_entries.async_setup(entry.entry_id)
            await hass.async_block_till_done()

        # Target entity remains untouched
        assert entity_registry.async_get(target_entity.entity_id).unique_id == f"{mac}-1-25"
        # Orphan entity is safely skipped without crash
        assert entity_registry.async_get(orphan_entity.entity_id) is not None

        await hass.config_entries.async_unload(entry.entry_id)

    @pytest.mark.asyncio
    async def test_migration_where_with_bus_interface_preserves_custom_id(self, hass: HomeAssistant):
        mac = "00:03:50:00:12:34"
        entry = MockConfigEntry(
            domain=DOMAIN,
            data={
                CONF_HOST: "192.168.0.35",
                CONF_PORT: 20000,
                CONF_PASSWORD: "pass",
                CONF_MAC: mac,
                CONF_SSDP_LOCATION: "http://192.168.0.35:49153/description.xml",
                CONF_SSDP_ST: "urn:schemas-upnp-org:device:Basic:1",
                CONF_DEVICE_TYPE: "urn:schemas-upnp-org:device:Basic:1",
                CONF_FRIENDLY_NAME: "MyHOME Gateway",
                CONF_MANUFACTURER: "BTicino",
                CONF_MANUFACTURER_URL: "http://www.bticino.com",
                CONF_NAME: "F454",
                CONF_FIRMWARE: "2.0.0",
                CONF_UDN: "uuid:12345678",
            },
            unique_id=mac,
        )
        entry.add_to_hass(hass)

        entity_registry = er.async_get(hass)
        # Entity with where including interface #4#s and custom entity_id
        routed_light = entity_registry.async_get_or_create(
            "light", DOMAIN, f"{mac}-45#4#s", config_entry=entry, suggested_object_id="custom_light_spot"
        )
        entity_registry.async_update_entity(routed_light.entity_id, name="Custom Spot")

        with patch("custom_components.myhome.gateway.OWNSession.test_connection", return_value={"Success": True, "Message": None}), \
             patch("custom_components.myhome.gateway.MyHOMEGatewayHandler.listening_loop"), \
             patch("custom_components.myhome.gateway.MyHOMEGatewayHandler.sending_loop"):
            assert await hass.config_entries.async_setup(entry.entry_id)
            await hass.async_block_till_done()

        # Custom entity_id and name are preserved, unique_id migrated to MAC-1-45#4#s
        migrated = entity_registry.async_get("light.custom_light_spot")
        assert migrated is not None
        assert migrated.unique_id == f"{mac}-1-45#4#s"
        assert migrated.name == "Custom Spot"
        # Confirm it was NOT forcibly renamed to default light.light_45
        assert entity_registry.async_get("light.light_45") is None

        # Verify runtime state
        state_routed = hass.states.get("light.custom_light_spot")
        assert state_routed is not None
        assert state_routed.name == "Custom Spot"
        assert hass.states.get("light.light_45") is None

        await hass.config_entries.async_unload(entry.entry_id)

    @pytest.mark.asyncio
    async def test_migration_entry_mac_format(self, hass: HomeAssistant):
        raw_mac = "000350001234"
        entry = MockConfigEntry(
            domain=DOMAIN,
            data={
                CONF_HOST: "192.168.0.35",
                CONF_PORT: 20000,
                CONF_PASSWORD: "pass",
                CONF_MAC: raw_mac,
                CONF_SSDP_LOCATION: "",
                CONF_SSDP_ST: "",
                CONF_DEVICE_TYPE: "",
                CONF_FRIENDLY_NAME: "Gateway",
                CONF_MANUFACTURER: "BTicino",
                CONF_MANUFACTURER_URL: "",
                CONF_NAME: "F454",
                CONF_FIRMWARE: "2.0",
                CONF_UDN: "1",
            },
            unique_id=raw_mac,
        )
        entry.add_to_hass(hass)

        entity_registry = er.async_get(hass)
        # Pre-populate entity with raw_mac in old unique_id format
        entity_registry.async_get_or_create("light", DOMAIN, f"{raw_mac}-21", config_entry=entry, suggested_object_id="eetkamer_lamp")
        entity_registry.async_update_entity("light.eetkamer_lamp", name="Eetkamer Lamp")

        with patch("custom_components.myhome.gateway.OWNSession.test_connection", return_value={"Success": True, "Message": None}), \
             patch("custom_components.myhome.gateway.MyHOMEGatewayHandler.listening_loop"), \
             patch("custom_components.myhome.gateway.MyHOMEGatewayHandler.sending_loop"):
            assert await hass.config_entries.async_setup(entry.entry_id)
            await hass.async_block_till_done()

        # Config entry unique_id formatted with colons
        assert entry.unique_id == "00:03:50:00:12:34"

        # Entity migrated to standard colon-formatted MAC unique_id and preserved custom name/id
        migrated = entity_registry.async_get("light.eetkamer_lamp")
        assert migrated is not None
        assert migrated.unique_id == "00:03:50:00:12:34-1-21"
        assert migrated.entity_id == "light.eetkamer_lamp"
        assert migrated.name == "Eetkamer Lamp"
        assert entity_registry.async_get("light.light_21") is None

        state_eetkamer = hass.states.get("light.eetkamer_lamp")
        assert state_eetkamer is not None
        assert state_eetkamer.name == "Eetkamer Lamp"

        await hass.config_entries.async_unload(entry.entry_id)

    @pytest.mark.asyncio
    async def test_customize_yaml_dimmable_with_custom_entity_id(self, hass: HomeAssistant):
        mac = "00:03:50:00:12:34"
        entry = MockConfigEntry(
            domain=DOMAIN,
            data={
                CONF_HOST: "192.168.0.35",
                CONF_PORT: 20000,
                CONF_PASSWORD: "pass",
                CONF_MAC: mac,
                CONF_SSDP_LOCATION: "http://192.168.0.35:49153/description.xml",
                CONF_SSDP_ST: "urn:schemas-upnp-org:device:Basic:1",
                CONF_DEVICE_TYPE: "urn:schemas-upnp-org:device:Basic:1",
                CONF_FRIENDLY_NAME: "MyHOME Gateway",
                CONF_MANUFACTURER: "BTicino",
                CONF_MANUFACTURER_URL: "http://www.bticino.com",
                CONF_NAME: "F454",
                CONF_FIRMWARE: "2.0.0",
                CONF_UDN: "uuid:12345678",
            },
            unique_id=mac,
        )
        entry.add_to_hass(hass)

        entity_registry = er.async_get(hass)
        entity_registry.async_get_or_create("light", DOMAIN, f"{mac}-1-21", config_entry=entry, suggested_object_id="keuken_dimmer")

        # Mock customize.yaml containing dimmable: true under custom entity_id
        fake_customs = {"light.keuken_dimmer": {"dimmable": True}}
        import os as _os
        orig_isfile = _os.path.isfile
        with patch("custom_components.myhome.gateway.OWNSession.test_connection", return_value={"Success": True, "Message": None}), \
             patch("custom_components.myhome.gateway.MyHOMEGatewayHandler.listening_loop"), \
             patch("custom_components.myhome.gateway.MyHOMEGatewayHandler.sending_loop"), \
             patch("os.path.isfile", side_effect=lambda p: True if "customize.yaml" in str(p) else orig_isfile(p)), \
             patch("homeassistant.util.yaml.loader.load_yaml", return_value=fake_customs):
            assert await hass.config_entries.async_setup(entry.entry_id)
            await hass.async_block_till_done()

        state = hass.states.get("light.keuken_dimmer")
        assert state is not None
        # Brightness color mode should be supported because dimmable is True from custom entity_id in customize.yaml
        assert "brightness" in state.attributes.get("supported_color_modes", [])

        await hass.config_entries.async_unload(entry.entry_id)


# ── 7. Golden Plant Sample Conformance (Issue #247 Nicola Cavallo Plant) ──────

class TestPhase1GoldenPlantSampleIssue247:
    """End-to-end golden plant conformance tests using real production data from Issue #247.

    Verifies that Nicola Cavallo's full 70+ device plant configuration
    (lights, switches, covers, climate, dry contact sensors, radar sensors, energy meters)
    loads cleanly, eliminates ghost devices, normalizes 4-digit zero-padded WHEREs,
    and updates entity states upon receiving authentic on-wire OpenWebNet bus frames.
    """

    @pytest.mark.asyncio
    async def test_golden_plant_yaml_import_and_device_cleanliness(self, hass: HomeAssistant):
        """Verify Nicola Cavallo's 70+ device plant initializes with zero orphaned ghost devices."""
        from homeassistant.helpers import device_registry as dr
        mac = "00:03:50:24:70:01"
        plant_yaml_path = Path(__file__).resolve().parent / "fixtures" / "plants" / "issue_247_nicolacavallo84" / "myhome.yaml"
        assert plant_yaml_path.is_file(), f"Fixture plant YAML not found at {plant_yaml_path}"

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
                CONF_FILE_PATH: str(plant_yaml_path),
            },
            unique_id=mac,
        )
        entry.add_to_hass(hass)

        with patch("custom_components.myhome.gateway.OWNSession.test_connection", return_value={"Success": True, "Message": None}), \
             patch("custom_components.myhome.gateway.MyHOMEGatewayHandler.listening_loop"), \
             patch("custom_components.myhome.gateway.MyHOMEGatewayHandler.sending_loop"):
            assert await hass.config_entries.async_setup(entry.entry_id)
            await hass.async_block_till_done()

        # 1. Device Registry Verification: Absolutely NO orphaned empty ghost devices
        device_registry = dr.async_get(hass)
        entity_registry = er.async_get(hass)
        entry_devices = [d for d in device_registry.devices.values() if entry.entry_id in d.config_entries]
        assert len(entry_devices) > 0, "Expected devices to be registered for the plant"

        for dev in entry_devices:
            # Skip the gateway hub device itself
            if (DOMAIN, mac) in dev.identifiers:
                continue
            # Each device must have at least one entity associated with it (no empty ghost devices)
            dev_entities = er.async_entries_for_device(entity_registry, dev.id)
            assert len(dev_entities) >= 1, (
                f"Ghost device detected with 0 entities: name={dev.name}, identifiers={dev.identifiers}"
            )
            # Verify no device identifier contains double WHO prefixes (e.g. mac-25-25-31)
            for domain_name, ident in dev.identifiers:
                assert "-25-25-" not in ident, f"Double WHO-25 prefix found in identifier: {ident}"
                # Verify no device identifier contains class suffix leaks (e.g. -moving)
                assert not ident.endswith("-moving"), f"Device class suffix leaked into identifier: {ident}"

        # 2. Entity Registry Verification: Platform entity population
        # 4-digit lighting and switches
        ent_vialetto = entity_registry.async_get("light.luci_vialetto_vicino")
        assert ent_vialetto is not None
        assert ent_vialetto.unique_id == f"{mac}-1-1000"

        ent_presa = entity_registry.async_get("switch.prese_esterne")
        assert ent_presa is not None
        assert ent_presa.unique_id in (f"{mac}-1-0910", f"{mac}-1-910")

        # Dimmable light with model F418
        ent_dimmable = entity_registry.async_get("light.luce_centrale_camera_matrimoniale")
        assert ent_dimmable is not None

        # Covers with advanced model LN4661M2
        ent_cover = entity_registry.async_get("cover.tapparella_camera_matrimoniale")
        assert ent_cover is not None
        assert ent_cover.unique_id == f"{mac}-2-73"

        # Climate central unit 3550 and zone thermostats
        ent_cu = entity_registry.async_get("climate.centrale_termoregolazione")
        assert ent_cu is not None

        # Dry contact binary sensors (WHO=25)
        ent_cancello = entity_registry.async_get("binary_sensor.cancello")
        assert ent_cancello is not None
        assert ent_cancello.unique_id == f"{mac}-25-31-opening"

        ent_moving = entity_registry.async_get("binary_sensor.contatto_tapparella_finestra_salone")
        assert ent_moving is not None
        assert ent_moving.unique_id == f"{mac}-25-331-moving"

        # WHO=18 energy power sensors
        ent_power = entity_registry.async_get("sensor.consumo_energia")
        assert ent_power is not None
        assert ent_power.unique_id.startswith(f"{mac}-18-51")

        await hass.config_entries.async_unload(entry.entry_id)

    @pytest.mark.asyncio
    async def test_golden_plant_live_bus_event_dispatching(self, hass: HomeAssistant):
        """Verify authentic on-wire frames from Nicola's bus monitor update HA entity states."""
        from homeassistant.helpers.dispatcher import async_dispatcher_send
        from OWNd.message import OWNMessage
        mac = "00:03:50:24:70:01"
        plant_yaml_path = Path(__file__).resolve().parent / "fixtures" / "plants" / "issue_247_nicolacavallo84" / "myhome.yaml"

        entry = MockConfigEntry(
            domain=DOMAIN,
            data={
                CONF_HOST: "192.168.1.50",
                CONF_PORT: 20000,
                CONF_PASSWORD: "pass",
                CONF_MAC: mac,
                CONF_SSDP_LOCATION: "",
                CONF_SSDP_ST: "",
                CONF_DEVICE_TYPE: "",
                CONF_FRIENDLY_NAME: "MyHomeServer1",
                CONF_MANUFACTURER: "BTicino",
                CONF_MANUFACTURER_URL: "",
                CONF_NAME: "MyHomeServer1",
                CONF_FIRMWARE: "2.0.0",
                CONF_UDN: "uuid:mhs1-issue247",
            },
            options={
                CONF_FILE_PATH: str(plant_yaml_path),
            },
            unique_id=mac,
        )
        entry.add_to_hass(hass)

        with patch("custom_components.myhome.gateway.OWNSession.test_connection", return_value={"Success": True, "Message": None}), \
             patch("custom_components.myhome.gateway.MyHOMEGatewayHandler.listening_loop"), \
             patch("custom_components.myhome.gateway.MyHOMEGatewayHandler.sending_loop"):
            assert await hass.config_entries.async_setup(entry.entry_id)
            await hass.async_block_till_done()

        handler = hass.data[DOMAIN][mac][CONF_ENTITY]
        handler._on_event_connection_state_change(True)

        # 1. 4-digit lighting frame: *1*0*1002## (Luci vialetto lontano OFF)
        msg_light_off = OWNMessage.parse("*1*0*1002##")
        async_dispatcher_send(hass, f"myhome_message_{mac}", msg_light_off)
        await hass.async_block_till_done()
        state_light = hass.states.get("light.luci_vialetto_lontano")
        assert state_light is not None
        assert state_light.state == "off"


        # 2. 4-digit switch frame: *1*1*0910## (Prese esterne ON)
        msg_switch_on = OWNMessage.parse("*1*1*0910##")
        async_dispatcher_send(hass, f"myhome_message_{mac}", msg_switch_on)
        await hass.async_block_till_done()
        state_switch = hass.states.get("switch.prese_esterne")
        assert state_switch is not None
        assert state_switch.state == "on"

        # 3. Dry contact frame: *25*31#1*31## (Cancello CLOSED / ON)
        msg_dry_closed = OWNMessage.parse("*25*31#1*31##")
        async_dispatcher_send(hass, f"myhome_message_{mac}", msg_dry_closed)
        await hass.async_block_till_done()
        state_dry = hass.states.get("binary_sensor.cancello")
        assert state_dry is not None
        assert state_dry.state == "on"

        # 4. Energy meter frame: *#18*51*113*602## (602 W active power)
        msg_energy = OWNMessage.parse("*#18*51*113*602##")
        async_dispatcher_send(hass, f"myhome_message_{mac}", msg_energy)
        await hass.async_block_till_done()
        state_energy = hass.states.get("sensor.consumo_energia")
        assert state_energy is not None
        assert state_energy.state == "602"

        await hass.config_entries.async_unload(entry.entry_id)


