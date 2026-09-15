"""Tests for OWNd discovery mechanism."""
import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from aiohttp import client_exceptions
from OWNd.discovery import (
    SimpleServiceDiscoveryProtocol,
    SSDPMessage,
    SSDPRequest,
    SSDPResponse,
    _get_scpd_details,
    _get_soap_body,
    find_gateways,
    get_gateway,
    get_port,
)


def test_ssdp_message_base():
    msg = SSDPMessage()
    with pytest.raises(NotImplementedError):
        SSDPMessage.parse("test")
    with pytest.raises(NotImplementedError):
        str(msg)

def test_ssdp_response():
    raw_response = (
        "HTTP/1.1 200 OK\r\n"
        "CACHE-CONTROL: max-age=1800\r\n"
        "EXT:\r\n"
        "LOCATION: http://192.168.1.135:49153/description.xml\r\n"
        "SERVER: Linux/2.6.14.0 UPnP/1.0 DLNADOC/1.00\r\n"
        "ST: upnp:rootdevice\r\n"
        "USN: uuid:upnp-Basic gateway-1_0-1234567890001::upnp:rootdevice\r\n"
        "\r\n"
    )

    resp = SSDPResponse.parse(raw_response)
    assert resp.version == "HTTP/1.1"
    assert resp.status_code == 200
    assert resp.reason == "OK"
    assert resp.headers_dictionary["LOCATION"] == "http://192.168.1.135:49153/description.xml"
    assert resp.headers_dictionary["ST"] == "upnp:rootdevice"

    output = str(resp)
    assert "HTTP/1.1 200 OK" in output
    assert "LOCATION: http://192.168.1.135:49153/description.xml" in output

    encoded = bytes(resp)
    assert b"HTTP/1.1 200 OK\r\n" in encoded

def test_ssdp_request():
    raw_req = (
        "M-SEARCH * HTTP/1.1\r\n"
        "HOST: 239.255.255.250:1900\r\n"
        "MAN: \"ssdp:discover\"\r\n"
        "MX: 2\r\n"
        "ST: upnp:rootdevice\r\n"
        "\r\n"
    )
    req = SSDPRequest.parse(raw_req)
    assert req.method == "M-SEARCH"
    assert req.uri == "*"
    assert req.headers_dictionary["ST"] == "upnp:rootdevice"

    output = str(req)
    assert "M-SEARCH * HTTP/1.1" in output
    assert "ST: upnp:rootdevice" in output


@pytest.mark.asyncio
async def test_simple_service_discovery_protocol():
    recvq = asyncio.Queue()
    excq = asyncio.Queue()

    protocol = SimpleServiceDiscoveryProtocol(recvq, excq)

    mock_transport = MagicMock()
    protocol.connection_made(mock_transport)
    assert protocol._transport == mock_transport

    valid_data = (
        "HTTP/1.1 200 OK\r\n"
        "LOCATION: http://192.168.1.135:49153/description.xml\r\n"
        "ST: upnp:rootdevice\r\n"
        "USN: uuid:upnp-Basic gateway-1_0-1234567890001::upnp:rootdevice\r\n"
        "\r\n"
    ).encode()

    protocol.datagram_received(valid_data, ("192.168.1.135", 1900))
    result = await recvq.get()
    assert result["address"] == "192.168.1.135"
    assert result["ssdp_location"] == "http://192.168.1.135:49153/description.xml"
    assert result["ssdp_st"] == "upnp:rootdevice"

    invalid_data = (
        "HTTP/1.1 200 OK\r\n"
        "LOCATION: http://something/else.xml\r\n"
        "ST: upnp:rootdevice\r\n"
        "USN: uuid:some-other-manufacturer::upnp:rootdevice\r\n"
        "\r\n"
    ).encode()
    protocol.datagram_received(invalid_data, ("192.168.1.136", 1900))
    assert recvq.empty()

    exc = Exception("Test exception")
    protocol.error_received(exc)
    assert await excq.get() is exc

    protocol.connection_lost(exc)
    assert await excq.get() is exc
    mock_transport.close.assert_called_once()

    protocol._transport = None
    protocol.connection_lost(None) # Safe fallback test


def test_get_soap_body():
    body = _get_soap_body("urn:schemas-bticino-it:service:openserver:1", "getopenserverPort")
    assert "urn:schemas-bticino-it:service:openserver:1" in body
    assert "getopenserverPort" in body


class MockAioHttpResponse:
    def __init__(self, text_data):
        self._text = text_data

    async def text(self):
        return self._text

    def raise_for_status(self):
        return None

class MockSessionProvider:
    def __init__(self, text_data=None, post_side_effect=None):
        self._text_data = text_data
        self.post_side_effect = post_side_effect

    async def __aenter__(self):
        session = AsyncMock()
        if self.post_side_effect:
            session.post.side_effect = self.post_side_effect
        elif self._text_data:
            session.post.return_value = MockAioHttpResponse(self._text_data)
            session.get.return_value = MockAioHttpResponse(self._text_data)
        return session

    async def __aexit__(self, exc_type, exc, tb):
        pass

@pytest.mark.asyncio
async def test_get_port_success():
    xml_data = '''<?xml version="1.0"?>
    <SOAP-ENV:Envelope xmlns:SOAP-ENV="http://schemas.xmlsoap.org/soap/envelope/">
    <SOAP-ENV:Body>
    <u:getopenserverPortResponse xmlns:u="urn:schemas-bticino-it:service:openserver:1">
    <Port>20000</Port>
    </u:getopenserverPortResponse></SOAP-ENV:Body></SOAP-ENV:Envelope>'''

    provider = MockSessionProvider(xml_data)

    with patch('aiohttp.ClientSession', return_value=provider):
        port = await get_port("http://192.168.1.135:80/description.xml")
        assert port == 20000

@pytest.mark.asyncio
async def test_get_port_exceptions():
    provider_1 = MockSessionProvider(post_side_effect=client_exceptions.ServerDisconnectedError(message="Disconnected"))
    with patch('aiohttp.ClientSession', return_value=provider_1):
        port = await get_port("http://192.168.1.135:80/description.xml")
        assert port == 20000  # Fallback

    provider_2 = MockSessionProvider(post_side_effect=client_exceptions.ClientOSError())
    with patch('aiohttp.ClientSession', return_value=provider_2):
        port = await get_port("http://192.168.1.135:80/description.xml")
        assert port == 20000  # Fallback

@pytest.mark.asyncio
async def test_get_scpd_details():
    xml_data = '''<?xml version="1.0"?>
    <root>
        <deviceType>urn:schemas-upnp-org:device:Basic:1</deviceType>
        <friendlyName>F454</friendlyName>
        <manufacturer>BTicino S.p.A.</manufacturer>
        <manufacturerURL>http://www.bticino.it</manufacturerURL>
        <modelName>F454</modelName>
        <modelNumber>2.0.0</modelNumber>
        <serialNumber>00:03:50:00:12:34</serialNumber>
        <UDN>uuid:upnp-Basic gateway-1_0-000350001234::upnp:rootdevice</UDN>
    </root>'''

    provider = MockSessionProvider(xml_data)

    with patch('aiohttp.ClientSession', return_value=provider), \
         patch('OWNd.discovery.get_port', return_value=20000):

        details = await _get_scpd_details("http://192.168.1.135:80/description.xml")

        assert details["deviceType"] == "urn:schemas-upnp-org:device:Basic:1"
        assert details["friendlyName"] == "F454"
        assert details["manufacturer"] == "BTicino S.p.A."
        assert details["manufacturerURL"] == "http://www.bticino.it"
        assert details["modelName"] == "F454"
        assert details["modelNumber"] == "2.0.0"
        assert details["serialNumber"] == "00:03:50:00:12:34"
        assert details["UDN"] == "uuid:upnp-Basic gateway-1_0-000350001234::upnp:rootdevice"
        assert details["port"] == 20000

@pytest.mark.asyncio
async def test_find_gateways():
    mock_transport = MagicMock()

    async def mock_create_datagram_endpoint(protocol_factory, family):
        protocol = protocol_factory()
        # Simulate an incoming packet!
        protocol._recvq.put_nowait({
            "address": "192.168.1.135",
            "ssdp_location": "http://192.168.1.135:80/description.xml",
            "ssdp_st": "upnp:rootdevice"
        })
        return mock_transport, protocol

    with patch('asyncio.get_running_loop') as mock_loop, \
         patch('OWNd.discovery._get_scpd_details', return_value={"modelName": "F454", "port": 20000}), \
         patch('asyncio.sleep', return_value=None):

        mock_loop.return_value.create_datagram_endpoint = mock_create_datagram_endpoint

        gateways = await find_gateways()

        assert len(gateways) == 1
        assert gateways[0]["address"] == "192.168.1.135"
        assert gateways[0]["modelName"] == "F454"
        assert gateways[0]["port"] == 20000
        mock_transport.sendto.assert_called_once()
        mock_transport.close.assert_called_once()

@pytest.mark.asyncio
async def test_get_gateway():
    mock_gateways = [
        {"address": "192.168.1.135", "modelName": "F454"},
        {"address": "192.168.1.136", "modelName": "MH200N"}
    ]
    with patch('OWNd.discovery._get_scpd_details', side_effect=Exception("no network")), \
         patch('OWNd.discovery.find_gateways', return_value=mock_gateways):
        gw = await get_gateway("192.168.1.136")
        assert gw is not None
        assert gw["modelName"] == "MH200N"

        gw_none = await get_gateway("192.168.1.100")
        assert gw_none is None

    # Test direct HTTP fetch success (Attempt 1, lines 334-339)
    direct_details = {
        "modelName": "F454",
        "serialNumber": "00:03:50:00:12:34",
        "port": 20000,
    }
    with patch('OWNd.discovery._get_scpd_details', return_value=direct_details):
        gw_direct = await get_gateway("192.168.1.50")
        assert gw_direct["address"] == "192.168.1.50"
        assert gw_direct["serialNumber"] == "00:03:50:00:12:34"
        assert gw_direct["ssdp_location"] == "http://192.168.1.50:49153/description.xml"
        assert gw_direct["ssdp_st"] is None
