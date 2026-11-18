"""Tests for USB/serial OpenZigBee config, reconfigure, options, and topology exclusions."""
from __future__ import annotations

import errno
import sys
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from homeassistant import config_entries
from homeassistant.config_entries import ConfigEntryState
from homeassistant.const import CONF_HOST, CONF_MAC, CONF_NAME, CONF_PORT
from homeassistant.data_entry_flow import FlowResultType
from OWNd.message import OWNMessage
from OWNd.profiles import Legrand3578Profile
from pytest_homeassistant_custom_component.common import MockConfigEntry
from serialx import SerialException

from custom_components.myhome.config_flow import (
    MyhomeConfigFlow,
    _async_list_serial_options,
    _async_ports_match,
    _async_probe_zigbee_serial,
    _async_resolve_serial_by_id,
    _fallback_get_serial_by_id,
    _resolve_serial_by_id_sync,
    _validated_eui64,
)
from custom_components.myhome.config_flow_options import MyhomeOptionsFlowHandler
from custom_components.myhome.config_flow_topology import (
    ConfigFlowTopologyMixin,
    OptionsFlowTopologyMixin,
)
from custom_components.myhome.const import (
    CONF_BAUDRATE,
    CONF_BROADCAST_RESYNC,
    CONF_FIRMWARE,
    CONF_GENERATE_EVENTS,
    CONF_IGNORED_ADDRESSES,
    CONF_SERIAL_PORT,
    CONF_TRANSITION_MODE,
    CONF_WORKER_COUNT,
    DEFAULT_BAUDRATE,
    DOMAIN,
    ROLE_PRIMARY,
    TOPOLOGY_STANDALONE,
)
from custom_components.myhome.topology import (
    GatewayTopologyMixin,
    entry_is_follower,
    entry_role,
    entry_topology,
)

TEST_EUI64 = "00:03:50:00:01:02:03:04"
OTHER_EUI64 = "00:03:50:00:05:06:07:08"
TEST_PORT = "/dev/serial/by-id/usb-BTicino_3578-if00"
ALT_PORT = "/dev/ttyUSB1"


class _FakeProbeTransport:
    """Fake AsyncSerialTransport used during _async_probe_zigbee_serial tests."""

    def __init__(
        self,
        *,
        connect_ok: bool = True,
        ieee_address: str | None = TEST_EUI64,
        firmware_version: str | None = "1.2.3",
        last_error: object = None,
        connect_exc: BaseException | None = None,
    ) -> None:
        self._connect_ok = connect_ok
        self.ieee_address = ieee_address
        self.firmware_version = firmware_version
        self.last_error = last_error
        self._connect_exc = connect_exc
        self.disconnected = False

    async def connect(self) -> bool:
        if self._connect_exc is not None:
            raise self._connect_exc
        return self._connect_ok

    async def disconnect(self) -> None:
        self.disconnected = True


async def test_eui64_validation_and_serial_helpers(hass, tmp_path) -> None:
    """Validate EUI-64 normalization, realpath matching, and serial listing paths."""
    assert _validated_eui64("00:03:50:00:01:02:03:04") == TEST_EUI64
    assert _validated_eui64("00:03:50:00:01:02:03:04".upper()) == TEST_EUI64
    assert _validated_eui64("0003500001020304") is None
    assert _validated_eui64("00:00:00:00:00:00:00:00") is None
    assert _validated_eui64("ff:ff:ff:ff:ff:ff:ff:ff") is None
    assert _validated_eui64("11:22:33:44:55:66") is None
    assert _validated_eui64(None) is None

    # _fallback_get_serial_by_id resolves matching symlink, else returns input unchanged
    assert _fallback_get_serial_by_id("/dev/ttyUSB9") == "/dev/ttyUSB9"
    assert _resolve_serial_by_id_sync("socket://10.0.0.1:2000") == "socket://10.0.0.1:2000"
    target = tmp_path / "ttyUSB0"
    target.write_text("", encoding="utf-8")
    by_id_dir = tmp_path / "by-id"
    by_id_dir.mkdir()
    link = by_id_dir / "usb-BTicino_3578-if00"
    link.symlink_to(target)
    fake_entry = SimpleNamespace(is_symlink=lambda: True, path=str(link))
    with (
        patch("custom_components.myhome.config_flow.os.path.isdir", return_value=True),
        patch("custom_components.myhome.config_flow.os.scandir", return_value=[fake_entry]),
    ):
        assert _fallback_get_serial_by_id(str(target)) == str(link)
        assert _fallback_get_serial_by_id(str(tmp_path / "unmatched")) == str(
            tmp_path / "unmatched"
        )

    # _async_ports_match exact, socket mismatch, and realpath check
    assert await _async_ports_match(hass, TEST_PORT, TEST_PORT) is True
    assert await _async_ports_match(hass, "socket://10.0.0.1:2000", TEST_PORT) is False
    assert await _async_ports_match(hass, str(link), str(target)) is True

    # When usb component is absent, falls back to serialx.list_serial_ports
    if "usb" in hass.config.components:
        hass.config.components.remove("usb")
    mock_port_a = SimpleNamespace(device="/dev/ttyUSB1", description="USB UART")
    mock_port_b = SimpleNamespace(device="/dev/ttyUSB0", description="")
    with patch(
        "serialx.list_serial_ports",
        return_value=[mock_port_a, mock_port_b],
    ):
        opts = await _async_list_serial_options(hass)
    values = [opt["value"] for opt in opts]
    assert "/dev/ttyUSB0" in values and "/dev/ttyUSB1" in values

    # When usb component IS loaded, uses usb.async_scan_serial_ports & usb.get_serial_by_id
    fake_usb = SimpleNamespace(
        async_scan_serial_ports=AsyncMock(
            return_value=[SimpleNamespace(device="/dev/ttyACM0", description="BTicino 3578")]
        ),
        get_serial_by_id=lambda p: TEST_PORT if p == "/dev/ttyACM0" else p,
        human_readable_device_name=lambda resolved, *args: f"BTicino 3578 ({resolved})",
    )
    hass.config.components.add("usb")
    with patch.dict(sys.modules, {"homeassistant.components.usb": fake_usb}):
        usb_opts = await _async_list_serial_options(hass)
        resolved = await _async_resolve_serial_by_id(hass, "/dev/ttyACM0")
        socket_res = await _async_resolve_serial_by_id(hass, "socket://192.168.1.5:2000")
    assert usb_opts[0]["value"] == TEST_PORT
    assert resolved == TEST_PORT
    assert socket_res == "socket://192.168.1.5:2000"

    # When usb component raises ImportError despite "usb" in components, falls back cleanly
    with (
        patch.dict(sys.modules, {"homeassistant.components.usb": None}),
        patch(
            "serialx.list_serial_ports",
            return_value=[mock_port_a],
        ),
    ):
        fallback_opts = await _async_list_serial_options(hass)
    assert fallback_opts[0]["value"] == "/dev/ttyUSB1"
    hass.config.components.remove("usb")


async def test_async_probe_zigbee_serial_paths(hass) -> None:
    """Exercise probe success (EUI-64 + firmware) and every failure mapping."""
    transport_ok = _FakeProbeTransport(ieee_address=TEST_EUI64, firmware_version="1.2.3")
    with patch(
        "custom_components.myhome.config_flow.AsyncSerialTransport",
        return_value=transport_ok,
    ):
        eui64, fw, err = await _async_probe_zigbee_serial(hass, TEST_PORT, DEFAULT_BAUDRATE)
    assert err is None
    assert eui64 == TEST_EUI64
    assert fw == "1.2.3"
    assert transport_ok.disconnected is True

    # Invalid / zeroed / missing EUI-64 -> no_zigbee_interface
    t_bad_eui = _FakeProbeTransport(ieee_address="00:00:00:00:00:00:00:00")
    with patch(
        "custom_components.myhome.config_flow.AsyncSerialTransport",
        return_value=t_bad_eui,
    ):
        eui64, fw, err = await _async_probe_zigbee_serial(hass, TEST_PORT)
    assert (eui64, fw, err) == (None, None, "no_zigbee_interface")

    # port_busy & no_zigbee_interface via last_error.reason
    for reason, expected in [
        ("port_busy", "port_busy"),
        ("no_zigbee_interface", "no_zigbee_interface"),
        ("unexpected_reason", "cannot_connect"),
    ]:
        t_fail = _FakeProbeTransport(
            connect_ok=False, last_error=SimpleNamespace(reason=reason)
        )
        with patch(
            "custom_components.myhome.config_flow.AsyncSerialTransport", return_value=t_fail
        ):
            _, _, err = await _async_probe_zigbee_serial(hass, TEST_PORT)
        assert err == expected

    # OSError EBUSY -> port_busy; other OSError, SerialException, TimeoutError -> cannot_connect
    for exc, expected in [
        (OSError(errno.EBUSY, "busy"), "port_busy"),
        (OSError(errno.ENOENT, "missing"), "cannot_connect"),
        (SerialException("serial fail"), "cannot_connect"),
        (TimeoutError(), "cannot_connect"),
    ]:
        t_exc = _FakeProbeTransport(connect_exc=exc)
        with patch(
            "custom_components.myhome.config_flow.AsyncSerialTransport", return_value=t_exc
        ):
            _, _, err = await _async_probe_zigbee_serial(hass, TEST_PORT)
        assert err == expected


async def test_serial_user_flow_and_subentry_exclusion(hass) -> None:
    """User can choose USB/serial, hit probe errors, succeed without topology step, and deduplicate."""
    with patch(
        "custom_components.myhome.config_flow._async_list_serial_options",
        return_value=[{"value": TEST_PORT, "label": "BTicino 3578"}],
    ):
        result = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": config_entries.SOURCE_USER}
        )
        assert result["type"] is FlowResultType.FORM
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {"serial": "serial"}
        )
        assert result["type"] is FlowResultType.FORM
        assert result["step_id"] == "serial"

        # Whitespace-only port fails without probing
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            {CONF_SERIAL_PORT: "   ", CONF_NAME: "BTicino 3578"},
        )
        assert result["type"] is FlowResultType.FORM
        assert result["errors"] == {CONF_SERIAL_PORT: "cannot_connect"}

        # Failed probe shows field error
        with patch(
            "custom_components.myhome.config_flow._async_probe_zigbee_serial",
            return_value=(None, None, "port_busy"),
        ):
            result = await hass.config_entries.flow.async_configure(
                result["flow_id"],
                {CONF_SERIAL_PORT: TEST_PORT, CONF_NAME: "BTicino 3578"},
            )
            assert result["type"] is FlowResultType.FORM
            assert result["errors"] == {CONF_SERIAL_PORT: "port_busy"}

        # Successful probe creates entry directly (no bus_topology step)
        with (
            patch(
                "custom_components.myhome.config_flow._async_probe_zigbee_serial",
                return_value=(TEST_EUI64, "1.2.3", None),
            ),
            patch("custom_components.myhome.async_setup_entry", return_value=True),
        ):
            result = await hass.config_entries.flow.async_configure(
                result["flow_id"],
                {CONF_SERIAL_PORT: TEST_PORT, CONF_NAME: "BTicino 3578"},
            )
        assert result["type"] is FlowResultType.CREATE_ENTRY
        entry = result["result"]
        assert entry.unique_id == TEST_EUI64
        assert entry.data[CONF_SERIAL_PORT] == TEST_PORT
        assert entry.data[CONF_FIRMWARE] == "1.2.3"
        assert MyhomeConfigFlow.async_get_supported_subentry_types(entry) == {}

        # Adding same EUI-64 over another symlink/port aborts already_configured and updates port
        result2 = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": config_entries.SOURCE_USER}
        )
        result2 = await hass.config_entries.flow.async_configure(
            result2["flow_id"], {"serial": "serial"}
        )
        with patch(
            "custom_components.myhome.config_flow._async_probe_zigbee_serial",
            return_value=(TEST_EUI64, "1.2.3", None),
        ):
            result2 = await hass.config_entries.flow.async_configure(
                result2["flow_id"],
                {CONF_SERIAL_PORT: ALT_PORT, CONF_NAME: "Legrand 088328"},
            )
        assert result2["type"] is FlowResultType.ABORT
        assert result2["reason"] == "already_configured"
        assert entry.data[CONF_SERIAL_PORT] == ALT_PORT


async def test_serial_reconfigure_self_probe_skip_and_mismatch(hass) -> None:
    """Reconfigure skips probing an open loaded port on self, validates other ports & EUI-64."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={
            CONF_MAC: TEST_EUI64,
            CONF_SERIAL_PORT: TEST_PORT,
            CONF_BAUDRATE: DEFAULT_BAUDRATE,
            CONF_NAME: "3578",
        },
        unique_id=TEST_EUI64,
    )
    entry.add_to_hass(hass)
    object.__setattr__(entry, "state", ConfigEntryState.LOADED)

    with (
        patch(
            "custom_components.myhome.config_flow._async_list_serial_options",
            return_value=[{"value": "/dev/ttyACM9", "label": "Other"}],
        ),
        patch(
            "custom_components.myhome.config_flow._async_probe_zigbee_serial"
        ) as spy_probe,
        patch(
            "custom_components.myhome.config_flow._async_ports_match",
            side_effect=lambda _h, a, b: a == b,
        ),
        patch("custom_components.myhome.async_setup_entry", return_value=True),
    ):
        result = await entry.start_reconfigure_flow(hass)
        assert result["type"] is FlowResultType.FORM
        assert result["step_id"] == "reconfigure_serial"

        # Same port while LOADED skips probing and updates/reloads cleanly
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            {CONF_SERIAL_PORT: TEST_PORT},
        )
        assert result["type"] is FlowResultType.ABORT
        assert result["reason"] == "reconfigure_successful"
        spy_probe.assert_not_called()

        # New port with empty string / probe error / mismatched EUI-64 sets field errors
        result2 = await entry.start_reconfigure_flow(hass)
        result2 = await hass.config_entries.flow.async_configure(
            result2["flow_id"],
            {CONF_SERIAL_PORT: "  "},
        )
        assert result2["type"] is FlowResultType.FORM
        assert result2["errors"] == {CONF_SERIAL_PORT: "cannot_connect"}

        spy_probe.return_value = (None, None, "cannot_connect")
        result2 = await hass.config_entries.flow.async_configure(
            result2["flow_id"],
            {CONF_SERIAL_PORT: ALT_PORT},
        )
        assert result2["type"] is FlowResultType.FORM
        assert result2["errors"] == {CONF_SERIAL_PORT: "cannot_connect"}

        spy_probe.return_value = (OTHER_EUI64, "2.0.0", None)
        result2 = await hass.config_entries.flow.async_configure(
            result2["flow_id"],
            {CONF_SERIAL_PORT: ALT_PORT},
        )
        assert result2["type"] is FlowResultType.FORM
        assert result2["errors"] == {CONF_SERIAL_PORT: "different_device"}

        # New port matching entry EUI-64 succeeds and updates port & firmware
        spy_probe.return_value = (TEST_EUI64, "2.0.0", None)
        result2 = await hass.config_entries.flow.async_configure(
            result2["flow_id"],
            {CONF_SERIAL_PORT: ALT_PORT},
        )
        assert result2["type"] is FlowResultType.ABORT
        assert result2["reason"] == "reconfigure_successful"
        assert entry.data[CONF_SERIAL_PORT] == ALT_PORT
        assert entry.data[CONF_FIRMWARE] == "2.0.0"

    # async_step_reconfigure_serial aborts with "unknown" when entry cannot be resolved
    flow = MyhomeConfigFlow()
    flow.hass = hass
    flow.context = {}
    abort_res = await flow.async_step_reconfigure_serial()
    assert abort_res["type"] is FlowResultType.ABORT
    assert abort_res["reason"] == "unknown"


async def test_serial_options_flow_and_topology_exclusions(hass) -> None:
    """Options flow hides IP/password/audio/topology for serial, and topology excludes serial."""
    serial_entry = MockConfigEntry(
        domain=DOMAIN,
        data={
            CONF_MAC: TEST_EUI64,
            CONF_SERIAL_PORT: TEST_PORT,
            CONF_BAUDRATE: DEFAULT_BAUDRATE,
            CONF_NAME: "3578",
        },
        unique_id=TEST_EUI64,
    )
    serial_entry.add_to_hass(hass)

    tcp_entry = MockConfigEntry(
        domain=DOMAIN,
        data={CONF_HOST: "192.168.1.50", CONF_PORT: 20000, CONF_MAC: OTHER_EUI64},
        unique_id=OTHER_EUI64,
    )
    tcp_entry.add_to_hass(hass)

    # Topology helpers exclude serial entry from primaries/followers
    assert entry_topology(serial_entry) == TOPOLOGY_STANDALONE
    assert entry_role(serial_entry) == ROLE_PRIMARY
    assert entry_is_follower(serial_entry) is False
    flow_topo = ConfigFlowTopologyMixin()
    flow_topo.hass = hass
    assert all(e.entry_id != serial_entry.entry_id for e in flow_topo._bus_primaries())
    opts_topo = OptionsFlowTopologyMixin()
    opts_topo.hass = hass
    opts_topo.config_entry = tcp_entry
    assert all(e.entry_id != serial_entry.entry_id for e in opts_topo._other_gateways())
    assert isinstance(MyhomeOptionsFlowHandler(serial_entry), OptionsFlowTopologyMixin)
    assert MyhomeConfigFlow.async_get_supported_subentry_types(tcp_entry) != {}

    # GatewayTopologyMixin methods are safe no-ops for serial
    topo = GatewayTopologyMixin()
    topo.hass = hass
    topo.config_entry = serial_entry
    topo.is_serial_transport = True  # type: ignore[attr-defined]
    topo.is_zigbee_address_family = True  # type: ignore[attr-defined]
    topo._record_tx(1.0, "*1*1*700123401#9##")
    topo._correlate_shared_bus_traffic(
        OWNMessage.parse("*1*1*700123401#9##", gateway=Legrand3578Profile())
    )

    # Options flow opens without host/port/password/audio/topology fields and saves worker_count=1
    result = await hass.config_entries.options.async_init(serial_entry.entry_id)
    assert result["type"] is FlowResultType.FORM
    schema_keys = {str(k) for k in result["data_schema"].schema}
    assert CONF_HOST not in schema_keys
    assert CONF_WORKER_COUNT not in schema_keys
    assert CONF_GENERATE_EVENTS in schema_keys
    assert CONF_BROADCAST_RESYNC in schema_keys
    assert CONF_TRANSITION_MODE in schema_keys
    assert CONF_IGNORED_ADDRESSES in schema_keys

    with patch("custom_components.myhome.async_setup_entry", return_value=True):
        result = await hass.config_entries.options.async_configure(
            result["flow_id"],
            {
                CONF_GENERATE_EVENTS: True,
                CONF_BROADCAST_RESYNC: False,
                CONF_IGNORED_ADDRESSES: "",
            },
        )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"][CONF_WORKER_COUNT] == 1
    assert result["data"][CONF_GENERATE_EVENTS] is True
    assert result["data"][CONF_BROADCAST_RESYNC] is False
