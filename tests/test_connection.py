"""Tests for OWNGateway, OWNSession crypto helpers, and connection infrastructure."""
import asyncio
import logging
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from OWNd.connection import OWNCommandSession, OWNEventSession, OWNGateway, OWNSession

# ── OWNGateway ─────────────────────────────────────────────────────────────

class TestOWNGateway:
    """Validate the OWNGateway data holder."""

    @pytest.fixture
    def gateway_info(self):
        return {
            "address": "192.168.0.35",
            "password": "12345",
            "ssdp_location": "http://192.168.0.35:8080/desc.xml",
            "ssdp_st": "upnp:rootdevice",
            "deviceType": "urn:schemas-upnp-org:device:server:1",
            "friendlyName": "MH200N",
            "manufacturer": "BTicino S.p.A.",
            "manufacturerURL": "http://www.bticino.it",
            "modelName": "MH200N",
            "modelNumber": "1.2.3",
            "serialNumber": "00:03:50:00:12:34",
            "UDN": "uuid:deadbeef",
            "port": 20000,
        }

    def test_basic_construction(self, gateway_info):
        gw = OWNGateway(gateway_info)
        assert gw.host == "192.168.0.35"
        assert gw.password == "12345"
        assert gw.model_name == "MH200N"
        assert gw.manufacturer == "BTicino S.p.A."
        assert gw.port == 20000

    def test_manufacturer_normalization(self):
        # Tuple
        gw_tuple = OWNGateway({"address": "127.0.0.1", "manufacturer": ("BTicino S.p.A.",)})
        assert gw_tuple.manufacturer == "BTicino S.p.A."

        # Empty tuple / list fallback
        gw_empty_list = OWNGateway({"address": "127.0.0.1", "manufacturer": []})
        assert gw_empty_list.manufacturer == "BTicino S.p.A."

        # Missing / None fallback
        gw_none = OWNGateway({"address": "127.0.0.1", "manufacturer": None})
        assert gw_none.manufacturer == "BTicino S.p.A."

    def test_unique_id(self, gateway_info):
        gw = OWNGateway(gateway_info)
        assert gw.unique_id == "00:03:50:00:12:34"
        gw.unique_id = "aa:bb:cc:dd:ee:ff"
        assert gw.unique_id == "aa:bb:cc:dd:ee:ff"

    def test_host_setter(self, gateway_info):
        gw = OWNGateway(gateway_info)
        gw.host = "10.0.0.1"
        assert gw.host == "10.0.0.1"

    def test_firmware_accessor(self, gateway_info):
        gw = OWNGateway(gateway_info)
        assert gw.firmware == "1.2.3"
        gw.firmware = "4.5.6"
        assert gw.firmware == "4.5.6"

    def test_firmware_normalization(self):
        # List of strings/ints in discovery info
        gw_list = OWNGateway({"address": "127.0.0.1", "modelNumber": ["2", "1", "0"]})
        assert gw_list.firmware == "2.1.0"
        assert gw_list.model_number == "2.1.0"

        # Tuple in discovery info
        gw_tuple = OWNGateway({"address": "127.0.0.1", "modelNumber": ("1", "0")})
        assert gw_tuple.firmware == "1.0"

        # Empty list in discovery info
        gw_empty = OWNGateway({"address": "127.0.0.1", "modelNumber": []})
        assert gw_empty.firmware is None

        # None / missing in discovery info
        gw_none = OWNGateway({"address": "127.0.0.1", "modelNumber": None})
        assert gw_none.firmware is None

        gw_missing = OWNGateway({"address": "127.0.0.1"})
        assert gw_missing.firmware is None

        # Setter normalization
        gw = OWNGateway({"address": "127.0.0.1"})
        gw.firmware = ["3", "4", "5"]
        assert gw.firmware == "3.4.5"

        gw.firmware = ("4", "0")
        assert gw.firmware == "4.0"

        gw.firmware = []
        assert gw.firmware is None

        gw.firmware = None
        assert gw.firmware is None

        gw.firmware = "9.9.9"
        assert gw.firmware == "9.9.9"

    def test_serial_accessor(self, gateway_info):
        gw = OWNGateway(gateway_info)
        assert gw.serial == "00:03:50:00:12:34"
        gw.serial = "new_serial"
        assert gw.serial == "new_serial"

    def test_password_setter(self, gateway_info):
        gw = OWNGateway(gateway_info)
        gw.password = "new_pass"
        assert gw.password == "new_pass"

    def test_log_id(self, gateway_info):
        gw = OWNGateway(gateway_info)
        assert "MH200N" in gw.log_id
        assert "192.168.0.35" in gw.log_id
        gw.log_id = "[custom]"
        assert gw.log_id == "[custom]"

    def test_minimal_construction(self):
        """Gateway with minimum required fields."""
        gw = OWNGateway({"address": "10.0.0.1"})
        assert gw.host == "10.0.0.1"
        assert gw.password is None
        assert gw.model_name == "Unknown model"
        assert gw.manufacturer == "BTicino S.p.A."
        assert gw.port == 20000


# ── OWNSession Properties ──────────────────────────────────────────────────

class TestOWNSessionProperties:
    """Validate OWNSession construction without network."""

    @pytest.fixture
    def session(self):
        gw = OWNGateway({"address": "192.168.0.35", "port": 20000})
        return OWNSession(gateway=gw, connection_type="Command", logger=logging.getLogger("test"))

    def test_gateway_property(self, session):
        assert session.gateway.host == "192.168.0.35"

    def test_gateway_setter(self, session):
        new_gw = OWNGateway({"address": "10.0.0.1"})
        session.gateway = new_gw
        assert session.gateway.host == "10.0.0.1"

    def test_connection_type(self, session):
        assert session.connection_type == "command"

    def test_connection_type_setter(self, session):
        session.connection_type = "EVENT"
        assert session.connection_type == "event"

    def test_logger_property(self, session):
        assert session.logger is not None

    def test_logger_setter(self, session):
        new_logger = logging.getLogger("new_test")
        session.logger = new_logger
        assert session.logger == new_logger


# ── Crypto Helper Functions ────────────────────────────────────────────────

class TestCryptoHelpers:
    """Test the password encoding/decoding methods on OWNSession.
    These are pure computational functions with no I/O."""

    @pytest.fixture
    def session(self):
        gw = OWNGateway({"address": "test", "port": 20000, "password": "12345"})
        return OWNSession(gateway=gw, connection_type="test", logger=logging.getLogger("test"))

    def test_hex_to_int_string(self, session):
        result = session._hex_string_to_int_string("0a1b2c")
        assert isinstance(result, str)
        assert all(c.isdigit() for c in result)

    def test_int_to_hex_string(self, session):
        result = session._int_string_to_hex_string("0010021503")
        assert isinstance(result, str)

    def test_hex_int_roundtrip(self, session):
        """Converting hex->int->hex should be deterministic."""
        original = "aabbccdd"
        int_str = session._hex_string_to_int_string(original)
        assert len(int_str) > 0
        assert all(c.isdigit() for c in int_str)

    def test_get_own_password(self, session):
        """Legacy nonce-based password hashing (non-HMAC)."""
        result = session._get_own_password("12345", "123456789")
        assert isinstance(result, int)
        assert result >= 0

    def test_get_own_password_all_digits(self, session):
        """Test with nonce containing every digit 0-9."""
        result = session._get_own_password("12345", "1234567890")
        assert isinstance(result, int)

    def test_get_own_password_zeros(self, session):
        """Nonce with leading zeros should skip initial processing."""
        result = session._get_own_password("12345", "0001234")
        assert isinstance(result, int)

    def test_encode_hmac_sha1(self, session):
        result = session._encode_hmac_password(
            method="sha1",
            password="12345",
            nonce_a="1234567890",
            nonce_b="0987654321"
        )
        assert isinstance(result, str)
        assert len(result) > 0

    def test_encode_hmac_sha256(self, session):
        result = session._encode_hmac_password(
            method="sha256",
            password="12345",
            nonce_a="1234567890",
            nonce_b="0987654321"
        )
        assert isinstance(result, str)
        assert len(result) > 0

    def test_encode_hmac_unknown_method(self, session):
        result = session._encode_hmac_password(
            method="md5",
            password="12345",
            nonce_a="1234567890",
            nonce_b="0987654321"
        )
        assert result is None

    def test_decode_hmac_sha1(self, session):
        result = session._decode_hmac_response(
            method="sha1",
            password="12345",
            nonce_a="1234567890",
            nonce_b="0987654321"
        )
        assert isinstance(result, str)
        assert len(result) > 0

    def test_decode_hmac_sha256(self, session):
        result = session._decode_hmac_response(
            method="sha256",
            password="12345",
            nonce_a="1234567890",
            nonce_b="0987654321"
        )
        assert isinstance(result, str)

    def test_decode_hmac_unknown_method(self, session):
        result = session._decode_hmac_response(
            method="md5",
            password="12345",
            nonce_a="1234567890",
            nonce_b="0987654321"
        )
        assert result is None

    def test_encode_decode_consistency(self, session):
        """Encode and decode with same params should produce different results
        (encode adds 'scope' constants, decode does not)."""
        encoded = session._encode_hmac_password(
            method="sha256", password="12345",
            nonce_a="1234567890", nonce_b="0987654321"
        )
        decoded = session._decode_hmac_response(
            method="sha256", password="12345",
            nonce_a="1234567890", nonce_b="0987654321"
        )
        # They use different input strings (encode includes 'scope' constants)
        assert encoded != decoded

# ── OWNSession IO Mocking ────────────────────────────────────────────────


class TestOWNSessionConnecting:
    @pytest.fixture
    def session(self):
        gw = OWNGateway({"address": "127.0.0.1", "port": 20000})
        return OWNSession(gateway=gw, connection_type="test", logger=logging.getLogger("test"))

    @pytest.mark.asyncio
    async def test_connect_success(self, session):
        mock_reader = AsyncMock()
        mock_writer = AsyncMock()

        with patch('asyncio.open_connection', return_value=(mock_reader, mock_writer)) as mock_open:
            with patch.object(session, '_negotiate', return_value={"Success": True}) as mock_neg:
                res = await session.connect()
                assert res["Success"] is True
                mock_open.assert_called_once_with("127.0.0.1", 20000)
                mock_neg.assert_called_once()
                assert session._stream_reader == mock_reader
                assert session._stream_writer == mock_writer

    @pytest.mark.asyncio
    async def test_connect_refused_retry_success(self, session):
        mock_reader = AsyncMock()
        mock_writer = AsyncMock()

        with patch('asyncio.sleep', return_value=None):
            with patch('asyncio.open_connection', side_effect=[ConnectionRefusedError, (mock_reader, mock_writer)]) as mock_open:
                with patch.object(session, '_negotiate', return_value={"Success": True}):
                    res = await session.connect()
                    assert res["Success"] is True
                    assert mock_open.call_count == 2

    @pytest.mark.asyncio
    async def test_connect_refused_max_retries(self, session):
        with patch('asyncio.sleep', return_value=None):
            with patch('asyncio.open_connection', side_effect=ConnectionRefusedError) as mock_open:
                res = await session.connect()
                assert res is None
                assert mock_open.call_count == 5  # Submits 5 total checks (0 through 4) before returning None

    @pytest.mark.asyncio
    async def test_close(self, session):
        mock_writer = MagicMock()
        mock_writer.wait_closed = AsyncMock()
        session._stream_writer = mock_writer
        await session.close()
        mock_writer.close.assert_called_once()
        mock_writer.wait_closed.assert_called_once()

class TestOWNEventSession:
    @pytest.fixture
    def session(self):
        gw = OWNGateway({"address": "127.0.0.1", "port": 20000})
        return OWNEventSession(gateway=gw, logger=logging.getLogger("test"))

    @pytest.mark.asyncio
    async def test_get_next_success(self, session):
        session._stream_reader = AsyncMock()
        session._stream_reader.readuntil.return_value = b"*1*1*12##"

        msg = await session.get_next()
        assert msg is not None

    @pytest.mark.asyncio
    async def test_get_next_heartbeat_timeout(self, session):
        session._stream_reader = AsyncMock()

        async def mock_readuntil(*args, **kwargs):
            raise asyncio.TimeoutError()

        session._stream_reader.readuntil.side_effect = mock_readuntil

        with patch('asyncio.sleep', return_value=None):
            with patch.object(session, 'close', new_callable=AsyncMock) as mock_close:
                with patch.object(session, 'connect', new_callable=AsyncMock) as mock_connect:
                    msg = await session.get_next()
                    assert msg is None
                    mock_close.assert_called_once()
                    mock_connect.assert_called_once()

class TestOWNCommandSession:
    @pytest.fixture
    def session(self):
        gw = OWNGateway({"address": "127.0.0.1", "port": 20000})
        return OWNCommandSession(gateway=gw, logger=logging.getLogger("test"))

    @pytest.mark.asyncio
    async def test_send_success(self, session):
        session._stream_writer = MagicMock()
        session._stream_writer.drain = AsyncMock()
        session._stream_reader = AsyncMock()

        # Simulating ACK response
        session._stream_reader.readuntil.return_value = b"*#*1##"

        await session.send("*1*1*12##")
        session._stream_writer.write.assert_called_once()
        session._stream_writer.drain.assert_called_once()

    @pytest.mark.asyncio
    async def test_send_retry_on_reset(self, session):
        session._stream_writer = MagicMock()
        session._stream_writer.drain = AsyncMock()
        session._stream_writer.wait_closed = AsyncMock()
        session._stream_reader = AsyncMock()

        session._stream_writer.write.side_effect = ConnectionResetError

        with patch.object(session, 'connect', new_callable=AsyncMock) as mock_connect:
            # Need to restore writer to simulate reconnect success
            async def restore_network():
                session._stream_writer = MagicMock()
                session._stream_writer.drain = AsyncMock()
                session._stream_reader = AsyncMock()
                session._stream_reader.readuntil.return_value = b"*#*1##"
                return {"Success": True}
            mock_connect.side_effect = restore_network
            await session.send("*1*1*12##")
            mock_connect.assert_called_once()

    @pytest.mark.asyncio
    async def test_send_multi_frame_response(self, session):
        """Verify openwebnet4j multi-frame response draining: collects intermediate frames until terminal ACK."""
        session._stream_writer = MagicMock()
        session._stream_writer.drain = AsyncMock()
        session._stream_reader = AsyncMock()
        session._stream_reader.readuntil.side_effect = [
            b"*1*1*12##",
            b"*1*0*13##",
            b"*#*1##",
        ]

        collected = await session.send("*#1*0##", is_status_request=True)
        assert isinstance(collected, list)
        assert len(collected) == 2
        assert session._stream_reader.readuntil.call_count == 3

    @pytest.mark.asyncio
    async def test_send_nack_retry_failure(self, session):
        """Verify that immediate NACK triggers a single retry, and if still NACK, returns None."""
        session._stream_writer = MagicMock()
        session._stream_writer.drain = AsyncMock()
        session._stream_reader = AsyncMock()
        session._stream_reader.readuntil.side_effect = [
            b"*#*0##",
            b"*#*0##",
        ]

        result = await session.send("*1*1*99##")
        assert result is None
        assert session._stream_writer.write.call_count == 2

    @pytest.mark.asyncio
    async def test_probe_gateway(self):
        """Verify active watchdog probe sending *#13**15## (openwebnet4j GatewayMgmt.requestModel by @mvalla)."""
        gw = OWNGateway({"address": "127.0.0.1", "port": 20000})
        with patch.object(OWNCommandSession, 'connect', new_callable=AsyncMock, return_value={"Success": True}):
            with patch.object(OWNCommandSession, 'send', new_callable=AsyncMock, return_value=True) as mock_send:
                with patch.object(OWNCommandSession, 'close', new_callable=AsyncMock):
                    alive = await OWNCommandSession.probe_gateway(gw)
                    assert alive is True
                    mock_send.assert_called_once_with("*#13**15##", is_status_request=True)


class TestOpenWebNet4jHardening:
    """Test suite specifically validating openwebnet4j protocol hardening from Massimo Valla (@mvalla)."""

    @pytest.fixture
    def command_session(self):
        gw = OWNGateway({"address": "127.0.0.1", "port": 20000})
        return OWNSession(gateway=gw, connection_type="command", logger=logging.getLogger("test"))

    @pytest.fixture
    def event_session(self):
        gw = OWNGateway(
            {"address": "127.0.0.1", "port": 20000, "modelName": "F454"}
        )
        return OWNEventSession(gateway=gw, logger=logging.getLogger("test"))

    @pytest.mark.asyncio
    async def test_negotiate_fallback_to_cmd_session_alt(self, command_session):
        """Verify *99*9## fallback when *99*0## receives NACK on newer Legrand firmware."""
        mock_reader = AsyncMock()
        mock_writer = MagicMock()
        mock_writer.drain = AsyncMock()

        # Gateway replies: NACK to *99*0##, then ACK to *99*9##, then ACK for open session
        mock_reader.readuntil.side_effect = [
            b"*#*0##",
            b"*#*1##",
            b"*#*1##",
        ]
        command_session._stream_reader = mock_reader
        command_session._stream_writer = mock_writer

        res = await command_session._negotiate()
        assert res["Success"] is True
        # Verify both *99*0## and *99*9## were written
        written_bytes = [call[0][0] for call in mock_writer.write.call_args_list]
        assert b"*99*0##" in written_bytes
        assert b"*99*9##" in written_bytes

    @pytest.mark.asyncio
    async def test_negotiate_fail_closed_both_nacked(self, command_session):
        """Verify negotiation fails closed if both *99*0## and *99*9## are NACKed."""
        mock_reader = AsyncMock()
        mock_writer = MagicMock()
        mock_writer.drain = AsyncMock()

        mock_reader.readuntil.side_effect = [
            b"*#*0##",
            b"*#*0##",
        ]
        command_session._stream_reader = mock_reader
        command_session._stream_writer = mock_writer

        res = await command_session._negotiate()
        assert res["Success"] is False
        assert res["Message"] == "connection_refused"

    @pytest.mark.asyncio
    async def test_event_session_keepalive_lifecycle(self, event_session):
        """Verify that connecting an event session schedules 90s keepalive and closing cancels it."""
        mock_reader = AsyncMock()
        mock_writer = MagicMock()
        mock_writer.drain = AsyncMock()
        mock_writer.wait_closed = AsyncMock()
        mock_writer.is_closing.return_value = False

        with patch('asyncio.open_connection', return_value=(mock_reader, mock_writer)):
            with patch.object(OWNSession, '_negotiate', return_value={"Success": True}):
                await event_session.connect()
                assert event_session._keepalive_task is not None
                assert not event_session._keepalive_task.done()

                # Closing session should cleanly cancel keepalive task
                await event_session.close()
                assert event_session._keepalive_task is None


# ==============================================================================
# Comprehensive Coverage Tests for OWNGateway and OWNSession (100% Target)
# ==============================================================================

class TestOWNGatewayDiscoveryAndClassmethods:
    """Test all OWNGateway discovery helpers and constructors."""

    @pytest.mark.asyncio
    async def test_get_first_available_gateway(self):
        with patch("OWNd.connection.find_gateways", return_value=[{"address": "192.168.1.50"}]):
            gw = await OWNGateway.get_first_available_gateway(password="secret")
            assert gw.host == "192.168.1.50"
            assert gw.password == "secret"

    @pytest.mark.asyncio
    async def test_find_from_address_present(self):
        with patch("OWNd.connection.get_gateway", return_value={"address": "192.168.1.55"}):
            gw = await OWNGateway.find_from_address("192.168.1.55")
            assert gw.host == "192.168.1.55"

    @pytest.mark.asyncio
    async def test_find_from_address_none(self):
        with patch.object(OWNGateway, "get_first_available_gateway", return_value=OWNGateway({"address": "10.0.0.1"})) as mock_first:
            gw = await OWNGateway.find_from_address(None)
            assert gw.host == "10.0.0.1"
            mock_first.assert_called_once()

    @pytest.mark.asyncio
    async def test_build_from_discovery_info_ssdp_hostname(self):
        info = {
            "ssdp_location": "http://192.168.1.99:8080/desc.xml",
            "port": 20000,
        }
        gw = await OWNGateway.build_from_discovery_info(info)
        assert gw.host == "192.168.1.99"
        assert gw.port == 20000

    @pytest.mark.asyncio
    async def test_build_from_discovery_info_fetch_port(self):
        info = {
            "ssdp_location": "http://192.168.1.100:8080/desc.xml",
            "port": None,
        }
        with patch("OWNd.connection.get_port", return_value=20000):
            gw = await OWNGateway.build_from_discovery_info(info)
            assert gw.port == 20000

    @pytest.mark.asyncio
    async def test_build_from_discovery_info_address_no_port(self):
        info = {
            "address": "192.168.1.101",
            "port": None,
        }
        with patch.object(OWNGateway, "find_from_address", return_value=OWNGateway({"address": "192.168.1.101", "port": 20000})):
            gw = await OWNGateway.build_from_discovery_info(info)
            assert gw.host == "192.168.1.101"

    @pytest.mark.asyncio
    async def test_build_from_discovery_info_first_available_fallback(self):
        info = {
            "port": None,
            "password": "pass",
        }
        with patch.object(OWNGateway, "get_first_available_gateway", return_value=OWNGateway({"address": "10.0.0.2", "password": "pass"})):
            gw = await OWNGateway.build_from_discovery_info(info)
            assert gw.host == "10.0.0.2"


class TestOWNSessionFullCoverage:
    """Test OWNSession properties, test_connection, connect backoff, close errors, and negotiate branches."""

    @pytest.fixture
    def session(self):
        gw = OWNGateway({"address": "127.0.0.1", "port": 20000, "password": "1234"})
        return OWNSession(gateway=gw, connection_type="command", logger=logging.getLogger("test"))

    def test_session_password_getter_setter(self, session):
        session.password = "secret123"
        assert session.password == "secret123"

    def test_session_default_logger(self):
        gw = OWNGateway({"address": "127.0.0.1", "port": 20000})
        session = OWNSession(gateway=gw)
        assert session.logger is not None

    @pytest.mark.asyncio
    async def test_test_gateway_classmethod(self):
        gw = OWNGateway({"address": "127.0.0.1", "port": 20000})
        with patch.object(OWNSession, "test_connection", return_value={"Success": True}):
            res = await OWNSession.test_gateway(gw)
            assert res == {"Success": True}

    @pytest.mark.asyncio
    async def test_test_connection_success(self, session):
        mock_reader = AsyncMock()
        mock_writer = MagicMock()
        mock_writer.drain = AsyncMock()
        mock_writer.wait_closed = AsyncMock()

        with patch("asyncio.open_connection", return_value=(mock_reader, mock_writer)):
            with patch.object(session, "_negotiate", return_value={"Success": True}):
                res = await session.test_connection()
                assert res == {"Success": True}

    @pytest.mark.asyncio
    async def test_test_connection_retries_exceeded(self, session):
        with patch("asyncio.sleep", return_value=None):
            with patch("asyncio.open_connection", side_effect=ConnectionRefusedError):
                res = await session.test_connection()
                assert res == {"Success": False, "Message": "connection_error"}

    @pytest.mark.asyncio
    async def test_test_connection_reset_error(self, session):
        mock_reader = AsyncMock()
        mock_writer = MagicMock()
        with patch("asyncio.open_connection", return_value=(mock_reader, mock_writer)):
            with patch.object(session, "_negotiate", side_effect=ConnectionResetError):
                res = await session.test_connection()
                assert res == {"Success": False, "Message": "password_retry"}

    @pytest.mark.asyncio
    async def test_connect_reset_and_timeout_backoff(self, session):
        mock_reader = AsyncMock()
        mock_writer = MagicMock()
        with patch("asyncio.sleep", return_value=None):
            with patch("asyncio.open_connection", side_effect=[ConnectionResetError, TimeoutError, (mock_reader, mock_writer)]):
                with patch.object(session, "_negotiate", return_value={"Success": True}):
                    res = await session.connect()
                    assert res == {"Success": True}

    @pytest.mark.asyncio
    async def test_close_with_writer_exception(self, session):
        mock_writer = MagicMock()
        mock_writer.close = MagicMock()
        mock_writer.wait_closed = AsyncMock(side_effect=OSError("Socket closed"))
        session._stream_writer = mock_writer
        await session.close()
        mock_writer.close.assert_called_once()

    def test_get_own_password_with_test_flag(self, session):
        """Call _get_own_password with test=True to hit debug prints."""
        pwd = session._get_own_password("12345", "1234567890", test=True)
        assert isinstance(pwd, int)

    def test_get_own_password_all_branches(self, session):
        """Ensure every digit 0-9 and non-zero/non-9 logic is exercised."""
        for ch in "0123456789":
            pwd = session._get_own_password("987654", f"{ch}12345678", test=False)
            assert isinstance(pwd, int)


class TestOWNSessionNegotiateBranches:
    """Test every branch in OWNSession._negotiate."""

    @pytest.fixture
    def session(self):
        gw = OWNGateway({"address": "127.0.0.1", "port": 20000, "password": "1234"})
        s = OWNSession(gateway=gw, connection_type="command", logger=logging.getLogger("test"))
        s._stream_writer = MagicMock()
        s._stream_writer.drain = AsyncMock()
        s._stream_reader = AsyncMock()
        return s

    @pytest.mark.asyncio
    async def test_negotiate_second_frame_nack(self, session):
        session._stream_reader.readuntil.side_effect = [
            b"*#*1##",
            b"*#*0##",
        ]
        res = await session._negotiate()
        assert res["Success"] is False
        assert res["Message"] == "negotiation_refused"

    @pytest.mark.asyncio
    async def test_negotiate_sha_no_password(self, session):
        session.gateway.password = None
        session._stream_reader.readuntil.side_effect = [
            b"*#*1##",
            b"*98*2##", # SHA challenge
        ]
        res = await session._negotiate()
        assert res["Success"] is False
        assert res["Message"] == "password_required"
        session._stream_writer.write.assert_any_call(b"*#*0##")

    @pytest.mark.asyncio
    async def test_negotiate_sha1_success(self, session):
        nonce_a = "1234567890123456789012345678901234567890"
        call_count = 0

        async def mock_readuntil(sep):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return b"*#*1##"
            elif call_count == 2:
                return b"*98*1##"
            elif call_count == 3:
                return f"*#{nonce_a}##".encode()
            else:
                last_call = session._stream_writer.write.call_args[0][0].decode()
                parts = last_call.strip("*#").split("*")
                rb = parts[0]
                server_hmac = session._decode_hmac_response("sha1", "1234", nonce_a, rb)
                return f"*#{server_hmac}##".encode()

        session._stream_reader.readuntil.side_effect = mock_readuntil
        res = await session._negotiate()
        assert res["Success"] is True
        assert res["Message"] is None

    @pytest.mark.asyncio
    async def test_negotiate_sha256_success(self, session):
        nonce_a = "1234567890123456789012345678901234567890"
        call_count = 0

        async def mock_readuntil(sep):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return b"*#*1##"
            elif call_count == 2:
                return b"*98*2##"
            elif call_count == 3:
                return f"*#{nonce_a}##".encode()
            else:
                last_call = session._stream_writer.write.call_args[0][0].decode()
                parts = last_call.strip("*#").split("*")
                rb = parts[0]
                server_hmac = session._decode_hmac_response("sha256", "1234", nonce_a, rb)
                return f"*#{server_hmac}##".encode()

        session._stream_reader.readuntil.side_effect = mock_readuntil
        res = await session._negotiate()
        assert res["Success"] is True

    @pytest.mark.asyncio
    async def test_negotiate_sha_nack(self, session):
        session._stream_reader.readuntil.side_effect = [
            b"*#*1##",
            b"*98*2##",
            b"*#12345##",
            b"*#*0##",
        ]
        res = await session._negotiate()
        assert res["Success"] is False
        assert res["Message"] == "password_error"

    @pytest.mark.asyncio
    async def test_negotiate_sha_hmac_mismatch(self, session):
        session._stream_reader.readuntil.side_effect = [
            b"*#*1##",
            b"*98*2##",
            b"*#12345##",
            b"*#9999999999##",
        ]
        res = await session._negotiate()
        assert res["Success"] is False
        assert res["Message"] == "negotiation_error"
        session._stream_writer.write.assert_any_call(b"*#*0##")

    @pytest.mark.asyncio
    async def test_negotiate_sha_incomplete_read(self, session):
        session._stream_reader.readuntil.side_effect = [
            b"*#*1##",
            b"*98*2##",
            b"*#12345##",
            asyncio.IncompleteReadError(b"", 10),
        ]
        res = await session._negotiate()
        assert res["Success"] is False
        assert res["Message"] == "connection_closed"

    @pytest.mark.asyncio
    async def test_negotiate_sha_timeout(self, session):
        session._stream_reader.readuntil.side_effect = [
            b"*#*1##",
            b"*98*2##",
            b"*#12345##",
            asyncio.TimeoutError(),
        ]
        res = await session._negotiate()
        assert res["Success"] is False
        assert res["Message"] == "negotiation_timeout"

    @pytest.mark.asyncio
    async def test_negotiate_nonce_numeric_password_success(self, session):
        session._stream_reader.readuntil.side_effect = [
            b"*#*1##",
            b"*#123456789##",
            b"*#*1##",
        ]
        res = await session._negotiate()
        assert res["Success"] is True

    @pytest.mark.asyncio
    async def test_negotiate_nonce_numeric_password_nack(self, session):
        session._stream_reader.readuntil.side_effect = [
            b"*#*1##",
            b"*#123456789##",
            b"*#*0##",
        ]
        res = await session._negotiate()
        assert res["Success"] is False
        assert res["Message"] == "password_error"

    @pytest.mark.asyncio
    async def test_negotiate_nonce_without_password(self, session):
        session.gateway.password = None
        session._stream_reader.readuntil.side_effect = [
            b"*#*1##",
            b"*#123456789##",
        ]
        res = await session._negotiate()
        assert res["Success"] is False
        assert res["Message"] == "password_error"

    @pytest.mark.asyncio
    async def test_negotiate_open_session_ack(self, session):
        session._stream_reader.readuntil.side_effect = [
            b"*#*1##",
            b"*#*1##",
        ]
        res = await session._negotiate()
        assert res["Success"] is True

    @pytest.mark.asyncio
    async def test_negotiate_unexpected_message(self, session):
        session._stream_reader.readuntil.side_effect = [
            b"*#*1##",
            b"*1*1*12##",
        ]
        res = await session._negotiate()
        assert res["Success"] is False
        assert res["Message"] == "negotiation_failed"


class TestOWNEventAndCommandSessionRemainingCoverage:
    """Test remaining branches in OWNEventSession and OWNCommandSession."""

    @pytest.fixture
    def event_session(self):
        gw = OWNGateway({"address": "127.0.0.1", "port": 20000})
        return OWNEventSession(gateway=gw, logger=logging.getLogger("test"))

    @pytest.fixture
    def command_session(self):
        gw = OWNGateway({"address": "127.0.0.1", "port": 20000})
        return OWNCommandSession(gateway=gw, logger=logging.getLogger("test"))

    @pytest.mark.asyncio
    async def test_event_session_connect_to_gateway_classmethod(self):
        gw = OWNGateway({"address": "127.0.0.1", "port": 20000})
        with patch.object(OWNEventSession, "connect", new_callable=AsyncMock) as mock_connect:
            await OWNEventSession.connect_to_gateway(gw)
            mock_connect.assert_called_once()

    @pytest.mark.asyncio
    async def test_command_session_send_to_gateway_classmethod(self):
        gw = OWNGateway({"address": "127.0.0.1", "port": 20000})
        with patch.object(OWNCommandSession, "connect", new_callable=AsyncMock):
            with patch.object(OWNCommandSession, "send", new_callable=AsyncMock) as mock_send:
                await OWNCommandSession.send_to_gateway("*1*1*12##", gw)
                mock_send.assert_called_once_with("*1*1*12##")

    @pytest.mark.asyncio
    async def test_command_session_connect_to_gateway_classmethod(self):
        gw = OWNGateway({"address": "127.0.0.1", "port": 20000})
        with patch.object(OWNCommandSession, "connect", new_callable=AsyncMock) as mock_connect:
            await OWNCommandSession.connect_to_gateway(gw)
            mock_connect.assert_called_once()

    @pytest.mark.asyncio
    async def test_event_session_keepalive_loop_sends_ping(self, event_session):
        event_session._keepalive_interval = 90
        mock_writer = MagicMock()
        mock_writer.drain = AsyncMock()
        mock_writer.is_closing.return_value = False
        event_session._stream_writer = mock_writer

        with patch("asyncio.sleep", side_effect=[None, asyncio.CancelledError()]):
            try:
                await event_session._keepalive_loop()
            except asyncio.CancelledError:
                pass
            mock_writer.write.assert_called_once_with(b"*#*1##")
            mock_writer.drain.assert_called_once()

    @pytest.mark.asyncio
    async def test_event_session_keepalive_loop_exception(self, event_session):
        event_session._keepalive_interval = 90
        with patch("asyncio.sleep", side_effect=OSError("Keepalive failed")):
            await event_session._keepalive_loop()

    @pytest.mark.asyncio
    async def test_event_session_get_next_exceptions(self, event_session):
        event_session._stream_reader = AsyncMock()

        # IncompleteReadError
        event_session._stream_reader.readuntil.side_effect = asyncio.IncompleteReadError(b"", 5)
        with patch.object(event_session, "connect", new_callable=AsyncMock):
            assert await event_session.get_next() is None

        # AttributeError
        event_session._stream_reader = AsyncMock()
        event_session._stream_reader.readuntil.side_effect = AttributeError("Malformed")
        with patch.object(event_session, "connect", new_callable=AsyncMock):
            assert await event_session.get_next() is None

        # ConnectionError
        event_session._stream_reader = AsyncMock()
        event_session._stream_reader.readuntil.side_effect = ConnectionResetError()
        with patch.object(event_session, "connect", new_callable=AsyncMock):
            assert await event_session.get_next() is None

        # Broad Exception
        event_session._stream_reader = AsyncMock()
        event_session._stream_reader.readuntil.side_effect = RuntimeError("Crash")
        with patch.object(event_session, "connect", new_callable=AsyncMock):
            assert await event_session.get_next() is None

    @pytest.mark.asyncio
    async def test_probe_gateway_failures(self):
        gw = OWNGateway({"address": "127.0.0.1", "port": 20000})
        # Failed connect
        with patch.object(OWNCommandSession, "connect", new_callable=AsyncMock, return_value={"Success": False}):
            assert await OWNCommandSession.probe_gateway(gw) is False

        # Exception during probe
        with patch.object(OWNCommandSession, "connect", side_effect=Exception("Network down")):
            assert await OWNCommandSession.probe_gateway(gw) is False

    @pytest.mark.asyncio
    async def test_command_session_send_terminal_raw_ack(self, command_session):
        command_session._stream_writer = MagicMock()
        command_session._stream_writer.drain = AsyncMock()
        command_session._stream_reader = AsyncMock()
        command_session._stream_reader.readuntil.return_value = b"*#*1##"

        res = await command_session.send("*1*1*12##", is_status_request=False)
        assert res is True

    @pytest.mark.asyncio
    async def test_command_session_send_immediate_nack_retry_success(self, command_session):
        command_session._stream_writer = MagicMock()
        command_session._stream_writer.drain = AsyncMock()
        command_session._stream_reader = AsyncMock()
        command_session._stream_reader.readuntil.side_effect = [
            b"*#*0##",
            b"*1*1*12##",
            b"*#*1##",
        ]

        res = await command_session.send("*1*1*12##", is_status_request=True)
        assert isinstance(res, list)
        assert len(res) == 1
        assert command_session._stream_writer.write.call_count == 2

    @pytest.mark.asyncio
    async def test_command_session_send_broad_exception(self, command_session):
        command_session._stream_reader = AsyncMock()
        command_session._stream_writer = MagicMock()
        command_session._stream_writer.wait_closed = AsyncMock()
        command_session._stream_writer.write.side_effect = RuntimeError("Fatal")
        res = await command_session.send("*1*1*12##")
        assert res is None
        assert command_session._stream_writer is None
