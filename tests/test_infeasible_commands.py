"""Unit tests ensuring no infeasible OpenWebNet commands or addresses are generated."""
from unittest.mock import AsyncMock, MagicMock

import pytest
from OWNd.message import (
    OWNAutomationCommand,
    OWNCommand,
    OWNLightingCommand,
)

from custom_components.myhome.button import (
    DisableCommandButtonEntity,
    EnableCommandButtonEntity,
)
from custom_components.myhome.const import is_apl_address, normalize_where


class TestPointToPointFeasibility:
    """Test suite ensuring strict OpenWebNet Point-to-Point (APL) feasibility rules."""

    def test_all_feasible_2_digit_combinations(self):
        """A in [1-9] and PL in [1-9] must all be valid 2-digit APL addresses."""
        for a in range(1, 10):
            for pl in range(1, 10):
                addr = f"{a}{pl}"
                assert is_apl_address(addr) is True, f"Address {addr} (A={a}, PL={pl}) must be feasible"

    def test_all_feasible_4_digit_area_00_combinations(self):
        """A = 00 and PL in [01-15] must all be valid 4-digit APL addresses."""
        for pl in range(1, 16):
            addr = f"00{pl:02d}"
            assert is_apl_address(addr) is True, f"Address {addr} (A=00, PL={pl}) must be feasible"

    def test_all_feasible_4_digit_area_1_to_9_high_pl_combinations(self):
        """A in [01-09] and PL in [10-15] must all be valid 4-digit APL addresses."""
        for a in range(1, 10):
            for pl in range(10, 16):
                addr = f"{a:02d}{pl:02d}"
                assert is_apl_address(addr) is True, f"Address {addr} (A={a}, PL={pl}) must be feasible"

    def test_all_feasible_4_digit_area_10_combinations(self):
        """A = 10 and PL in [01-15] must all be valid 4-digit APL addresses."""
        for pl in range(1, 16):
            addr = f"10{pl:02d}"
            assert is_apl_address(addr) is True, f"Address {addr} (A=10, PL={pl}) must be feasible"

    def test_infeasible_3_digit_addresses_never_valid(self):
        """3-digit addresses are impossible in OpenWebNet point-to-point addressing."""
        infeasible_3_digit = [
            "111",  # ambiguous: A=1, PL=11 or A=11, PL=1?
            "115",  # ambiguous: A=1, PL=15? (must be 0115)
            "210",  # ambiguous: A=2, PL=10? (must be 0210)
            "015",  # invalid: A=0, PL=15? (must be 0015)
            "001",  # invalid: A=0, PL=1? (must be 0001)
            "999",  # invalid
            "101",  # invalid
        ]
        for addr in infeasible_3_digit:
            assert is_apl_address(addr) is False, f"3-digit address {addr} must never be a feasible APL address"

    def test_infeasible_light_point_above_15(self):
        """Light point PL can never exceed 15 in OpenWebNet."""
        infeasible_high_pl = [
            "0016",   # A=00, PL=16 (>15)
            "0116",   # A=01, PL=16 (>15)
            "0220",   # A=02, PL=20 (>15)
            "1016",   # A=10, PL=16 (>15)
            "0021",   # A=00, PL=21 (>15, e.g. dry contact object ID)
            "0099",   # A=00, PL=99 (>15)
        ]
        for addr in infeasible_high_pl:
            assert is_apl_address(addr) is False, f"Address {addr} with PL > 15 must never be a feasible APL address"

    def test_infeasible_light_point_zero(self):
        """Light point PL can never be 0 in OpenWebNet (PL=0 does not exist; 0 is general broadcast)."""
        infeasible_zero_pl = [
            "10",     # A=1, PL=0 -> invalid APL
            "20",     # A=2, PL=0 -> invalid APL
            "0000",   # A=00, PL=0 -> invalid APL
            "0100",   # A=01, PL=0 -> invalid APL
            "1000",   # A=10, PL=0 -> invalid APL
        ]
        for addr in infeasible_zero_pl:
            assert is_apl_address(addr) is False, f"Address {addr} with PL=0 must never be a feasible APL address"

    def test_infeasible_4_digit_for_single_digit_pl_and_area(self):
        """A [1-9] and PL [1-9] must be 2 digits (e.g. '15'), never 4 digits (e.g. '0105')."""
        infeasible_padded = ["0101", "0105", "0208", "0909"]
        for addr in infeasible_padded:
            assert is_apl_address(addr) is False, f"4-digit address {addr} for A,PL in [1-9] must be 2 digits"

    def test_infeasible_area_above_10(self):
        """Area A can never exceed 10 in OpenWebNet."""
        infeasible_high_area = ["1101", "1215", "9901", "9915"]
        for addr in infeasible_high_area:
            assert is_apl_address(addr) is False, f"Address {addr} with Area > 10 must never be a feasible APL address"


class TestNormalizationSafety:
    """Test suite ensuring normalize_where never destroys or corrupts feasible OpenWebNet addresses."""

    def test_0015_and_15_never_collide(self):
        """0015 (Area 00, PL 15) must NEVER be collapsed to 15 (Area 1, PL 5)."""
        norm_0015 = normalize_where("0015")
        norm_15 = normalize_where("15")
        assert norm_0015 == "0015"
        assert norm_15 == "15"
        assert norm_0015 != norm_15

    def test_4_digit_apl_addresses_preserve_exact_string(self):
        """Feasible 4-digit APL addresses must retain all digits."""
        assert normalize_where("0001") == "0001"
        assert normalize_where("0015") == "0015"
        assert normalize_where("0115") == "0115"
        assert normalize_where("0212") == "0212"
        assert normalize_where("1001") == "1001"
        assert normalize_where("1015") == "1015"

    def test_broadcast_addresses_preserve_exact_string(self):
        """General '0', Area 0 '00', and Area 10 '100' must never be stripped or altered."""
        assert normalize_where("0") == "0"
        assert normalize_where("00") == "00"
        assert normalize_where("100") == "100"

    def test_sub_bus_preserved_on_feasible_addresses(self):
        """Sub-bus interface #4#... must be preserved without corrupting base address."""
        assert normalize_where("0015#4#1") == "0015#4#1"
        assert normalize_where("15#4#2") == "15#4#2"
        assert normalize_where("0115#4#1") == "0115#4#1"

    def test_non_apl_zero_padded_numeric_ids_stripped(self):
        """Zero-padded integer IDs (e.g. CEN+ object 21 '0021') are normalized to match integer IDs."""
        assert normalize_where("0021") == "21"
        assert normalize_where("0021#4#1") == "21#4#1"
        assert normalize_where(None) == ""
        assert normalize_where("") == ""
        assert normalize_where("   ") == ""
        assert normalize_where("abc") == "abc"
        assert normalize_where("abc#4#1") == "abc#4#1"
        assert is_apl_address("abc") is False


class TestCommandGenerationFeasibility:
    """Test suite ensuring generated OpenWebNet command strings are strictly feasible."""

    def test_lighting_commands_maintain_exact_where(self):
        """Lighting commands must never truncate or alter feasible WHERE addresses."""
        # 0015 (Area 00, PL 15)
        cmd_on_0015 = OWNLightingCommand.switch_on("0015")
        assert str(cmd_on_0015) == "*1*1*0015##"
        assert cmd_on_0015.where == "0015"

        cmd_off_0015 = OWNLightingCommand.switch_off("0015")
        assert str(cmd_off_0015) == "*1*0*0015##"

        cmd_status_0015 = OWNLightingCommand.status("0015")
        assert str(cmd_status_0015) == "*#1*0015##"

        # 15 (Area 1, PL 5)
        cmd_on_15 = OWNLightingCommand.switch_on("15")
        assert str(cmd_on_15) == "*1*1*15##"
        assert cmd_on_15.where == "15"

        # Commands for 0015 and 15 must never match
        assert str(cmd_on_0015) != str(cmd_on_15)
        assert str(cmd_status_0015) != str(OWNLightingCommand.status("15"))

    def test_automation_commands_maintain_exact_where(self):
        """Automation (shutter) commands must preserve exact feasible WHERE."""
        cmd_status = OWNAutomationCommand.status("1015")
        assert str(cmd_status) == "*#2*1015##"
        assert cmd_status.where == "1015"

    @pytest.mark.asyncio
    async def test_who14_button_command_generation_and_entity_id_rules(self, hass):
        """WHO 14 buttons must produce feasible commands and strictly end with _lock and _unlock."""
        mock_gateway = MagicMock()
        mock_gateway.mac = "00:03:50:00:14:99"
        mock_gateway.send = AsyncMock()

        lock_btn = DisableCommandButtonEntity(
            hass=hass,
            platform="button",
            name="Office Light",
            device_id="15",
            who="1",
            where="15",
            interface=None,
            manufacturer="BTicino",
            model="F411",
            gateway=mock_gateway,
        )
        unlock_btn = EnableCommandButtonEntity(
            hass=hass,
            platform="button",
            name="Office Light",
            device_id="15",
            who="1",
            where="15",
            interface=None,
            manufacturer="BTicino",
            model="F411",
            gateway=mock_gateway,
        )

        # 1. Rule: Entity IDs must strictly end with _lock and _unlock
        assert lock_btn.entity_id == "button.office_light_lock"
        assert unlock_btn.entity_id == "button.office_light_unlock"
        assert lock_btn.entity_id != "button.office_light"
        assert unlock_btn.entity_id != "button.office_light_2"

        # 2. Rule: Press commands must generate exact feasible WHO 14 frames
        await lock_btn.async_press()
        mock_gateway.send.assert_called_once_with("*14*0*15##")
        mock_gateway.send.reset_mock()

        await unlock_btn.async_press()
        mock_gateway.send.assert_called_once_with("*14*1*15##")
        mock_gateway.send.reset_mock()

        # Test with 4-digit address 0015
        lock_btn_0015 = DisableCommandButtonEntity(
            hass=hass,
            platform="button",
            name="Motion 0015",
            device_id="0015",
            who="1",
            where="0015",
            interface=None,
            manufacturer="BTicino",
            model="Legrand 048834",
            gateway=mock_gateway,
        )
        await lock_btn_0015.async_press()
        mock_gateway.send.assert_called_once_with("*14*0*0015##")
        assert "*14*0*15##" != "*14*0*0015##"


class TestProbeAndStatusFeasibility:
    """Test suite ensuring probe and status requests respect OpenWebNet feasibility."""

    @pytest.mark.asyncio
    async def test_general_lighting_event_does_not_send_invalid_status_request(self, hass):
        """A general light event (*1*0*0##) must not trigger an invalid *#1*0## status request."""
        import asyncio
        from unittest.mock import AsyncMock, MagicMock, patch

        from OWNd.message import OWNLightingEvent

        from custom_components.myhome.gateway import MyHOMEGatewayHandler

        config_entry = MagicMock()
        config_entry.data = {
            "address": "192.168.1.50",
            "port": 20000,
            "serialNumber": "00:03:50:AA:BB:CC",
        }

        handler = MyHOMEGatewayHandler(hass=hass, config_entry=config_entry)
        handler.send_status_request = AsyncMock()

        msg = OWNLightingEvent("*1*0*0##")
        assert msg.is_general is True

        with patch("custom_components.myhome.gateway.OWNEventSession") as mock_session_class:
            mock_session = MagicMock()
            mock_session.connect = AsyncMock(return_value={"Success": True})
            mock_session.get_next = AsyncMock(side_effect=[msg, asyncio.CancelledError()])
            mock_session_class.return_value = mock_session

            try:
                await handler.listening_loop()
            except asyncio.CancelledError:
                pass

        # send_status_request is called 3 times on initial active discovery (*#2*0##, *#4*0##, *#16*0##)
        # but MUST NEVER be called for *#1*0##!
        for call_arg in handler.send_status_request.call_args_list:
            cmd = call_arg[0][0]
            assert str(cmd) != "*#1*0##", "General status request *#1*0## is invalid in OpenWebNet and must never be sent"

    @pytest.mark.asyncio
    async def test_status_request_nack_logged_at_debug_without_warning(self):
        """Status request NACK (e.g. *#16*0## without audio matrix) must log DEBUG and not WARN."""
        from unittest.mock import MagicMock

        from OWNd.connection import OWNCommandSession, OWNGateway

        from tests.mock_gateway_harness import MockGatewayHarness

        harness = MockGatewayHarness()
        harness.set_nack_commands(True)
        port = await harness.start()

        gw = OWNGateway({
            "address": "127.0.0.1",
            "port": port,
            "password": None,
            "modelName": "MH201",
            "serialNumber": "00:03:50:00:12:34",
        })

        mock_logger = MagicMock()
        session = OWNCommandSession(gateway=gw, logger=mock_logger)
        assert (await session.connect())["Success"] is True

        try:
            # When probing audio on MH201 without audio matrix:
            status_cmd = OWNCommand.parse("*#16*0##")
            result = await session.send(status_cmd, is_status_request=True)
            assert result is None

            # Attempt 0 retries once upon immediate NACK, then on attempt 1 reports NACK at DEBUG
            assert harness.received_messages.count("*#16*0##") == 2

            # Must log at DEBUG, never at WARNING
            mock_logger.debug.assert_any_call(
                "%s Gateway rejected status request %s (NACK, %s response(s)). Subsystem or device may not be present.",
                gw.log_id, status_cmd, 0,
            )
            warning_calls = [
                call for call in mock_logger.warning.call_args_list
                if str(status_cmd) in str(call)
            ]
            assert len(warning_calls) == 0
        finally:
            await session.close()
            await harness.stop()

