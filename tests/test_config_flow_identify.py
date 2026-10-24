"""The manual config flow asks the authenticated gateway for its MAC and model (live F461 replies)."""
import logging
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from homeassistant import config_entries
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from OWNd.message import OWNMessage

from custom_components.myhome.const import DOMAIN

F461_MAC_REPLY = "*#13**12*0*3*80*0*7*119##"
F461_MODEL_REPLY = "*#1013**1*134*15*5*0##"


async def _manual_flow(hass: HomeAssistant, test_connection: AsyncMock, identify: AsyncMock):
    with (
        patch("custom_components.myhome.config_flow.find_gateways", return_value=[]),
        patch("custom_components.myhome.config_flow.get_gateway", return_value=None),
        patch("custom_components.myhome.config_flow.OWNSession.test_connection", test_connection),
        patch("custom_components.myhome.config_flow.identify_gateway", identify),
        patch("custom_components.myhome.async_setup_entry", return_value=True),
    ):
        result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": config_entries.SOURCE_USER})
        result = await hass.config_entries.flow.async_configure(result["flow_id"], {"serial": "00:00:00:00:00:00"})
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {"address": "192.0.2.92", "port": 20000}
        )
        assert result["step_id"] == "custom_manual"
        # The defaults the form offers when the user does not know better
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {"serialNumber": "00:03:50:00:00:00", "modelName": "MyHomeServer1"}
        )
        assert result["type"] == FlowResultType.FORM
        assert result["step_id"] == "password"
        result = await hass.config_entries.flow.async_configure(result["flow_id"], {"password": "12345"})
        await hass.async_block_till_done()
    return result


async def test_manual_flow_corrects_mac_and_model_from_the_gateway(hass: HomeAssistant, caplog) -> None:
    test_connection = AsyncMock(
        side_effect=[{"Success": False, "Message": "password_required"}, {"Success": True}]
    )
    identify = AsyncMock(return_value=("00:03:50:00:07:77", "200", "134"))

    with caplog.at_level(logging.DEBUG, logger="custom_components.myhome"):
        result = await _manual_flow(hass, test_connection, identify)

    assert result["type"] == FlowResultType.CREATE_ENTRY
    assert result["data"]["mac"] == "00:03:50:00:07:77"
    assert result["data"]["name"] == "F461"
    assert result["title"] == "F461 Gateway"
    assert result["result"].unique_id == "00:03:50:00:07:77"
    identify.assert_awaited_once()
    # The password probe is the normal path, not a failure worth a WARNING
    assert not [
        r for r in caplog.records if r.levelno >= logging.WARNING and "password_required" in r.getMessage()
    ]


async def test_manual_flow_keeps_the_answers_when_the_gateway_says_nothing(hass: HomeAssistant) -> None:
    test_connection = AsyncMock(
        side_effect=[{"Success": False, "Message": "password_required"}, {"Success": True}]
    )
    identify = AsyncMock(return_value=(None, None, None))

    result = await _manual_flow(hass, test_connection, identify)

    assert result["type"] == FlowResultType.CREATE_ENTRY
    assert result["data"]["mac"] == "00:03:50:00:00:00"
    assert result["data"]["name"] == "MyHomeServer1"


async def test_manual_flow_aborts_when_the_real_mac_is_already_configured(hass: HomeAssistant) -> None:
    from pytest_homeassistant_custom_component.common import MockConfigEntry

    MockConfigEntry(
        domain=DOMAIN, data={"host": "192.0.2.92", "mac": "00:03:50:00:07:77"}, unique_id="00:03:50:00:07:77"
    ).add_to_hass(hass)
    test_connection = AsyncMock(
        side_effect=[{"Success": False, "Message": "password_required"}, {"Success": True}]
    )
    identify = AsyncMock(return_value=("00:03:50:00:07:77", "200", "134"))

    result = await _manual_flow(hass, test_connection, identify)

    assert result["type"] == FlowResultType.ABORT
    assert result["reason"] == "already_configured"


async def test_identify_gateway_reads_the_f461_replies() -> None:
    """identify_gateway asks the model first, then the MAC (an F461 answers both), and decodes the live replies."""
    from custom_components.myhome import config_flow_discovery as cfd

    session = MagicMock()
    session.connect = AsyncMock(return_value={"Success": True})
    replies = {
        "*#13**15##": [OWNMessage.parse("*#13**15*200##")],
        "*#13**12##": [OWNMessage.parse(F461_MAC_REPLY)],
        "*#1013**1##": [OWNMessage.parse(F461_MODEL_REPLY)],
    }
    session.send = AsyncMock(side_effect=lambda message, is_status_request=False: replies[str(message)])
    session.close = AsyncMock()

    with patch.object(cfd, "OWNCommandSession", return_value=session):
        mac, code13, code = await cfd.identify_gateway(MagicMock())

    assert (mac, code13, code) == ("00:03:50:00:07:77", "200", "134")
    assert [str(c.args[0]) for c in session.send.await_args_list] == ["*#13**15##", "*#1013**1##", "*#13**12##"]
    session.close.assert_awaited()


@pytest.mark.parametrize(("model", "who13"), [("H4684", "13"), ("MH200", "4")])
async def test_identify_gateway_never_asks_the_mac_from_a_gateway_that_crashes_on_it(model, who13) -> None:
    """*#13**12## crashes a daemon on the H4684 and MH200 (real-binary oracle run): only *#13**15## goes out.

    Their WHO=13 device type is not the shared 200, so *#1013**1## is not asked either (#420).
    """
    from custom_components.myhome import config_flow_discovery as cfd

    session = MagicMock()
    session.connect = AsyncMock(return_value={"Success": True})
    replies = {"*#13**15##": [OWNMessage.parse(f"*#13**15*{who13}##")]}
    session.send = AsyncMock(side_effect=lambda message, is_status_request=False: replies[str(message)])
    session.close = AsyncMock()

    with patch.object(cfd, "OWNCommandSession", return_value=session):
        mac, code13, code = await cfd.identify_gateway(MagicMock())

    assert (mac, code13, code) == (None, who13, None)
    assert [str(c.args[0]) for c in session.send.await_args_list] == ["*#13**15##"]


def test_mac_request_is_safe_only_for_models_that_answered_it() -> None:
    """Only models a physical unit answered with a real MAC (F461 2.0.11, MyHomeServer1 3.87.13)."""
    from custom_components.myhome import config_flow_discovery as cfd

    assert cfd.mac_request_is_safe("200", "134") is True  # F461
    assert cfd.mac_request_is_safe("200", "67") is True  # MyHomeServer1
    assert cfd.mac_request_is_safe("200", "51") is False  # F454: emulated answer only, no live MAC capture
    assert cfd.mac_request_is_safe("200", None) is False  # shared code, model unknown
    assert cfd.mac_request_is_safe(None, None) is False
    assert cfd.mac_request_is_safe("13", None) is False  # H4684 by its WHO=13 device type
    assert cfd.mac_request_is_safe("999999", None) is False  # unknown code: not asked


async def test_identify_gateway_survives_a_refused_session() -> None:
    from custom_components.myhome import config_flow_discovery as cfd

    session = MagicMock()
    session.connect = AsyncMock(side_effect=OSError("refused"))
    session.close = AsyncMock()
    with patch.object(cfd, "OWNCommandSession", return_value=session):
        assert await cfd.identify_gateway(MagicMock()) == (None, None, None)


async def test_identify_gateway_gives_up_when_the_session_is_not_accepted() -> None:
    """A connect that does not report Success asks nothing and yields no identity."""
    from custom_components.myhome import config_flow_discovery as cfd

    session = MagicMock()
    session.connect = AsyncMock(return_value={"Success": False, "Message": "password_error"})
    session.send = AsyncMock()
    session.close = AsyncMock()
    with patch.object(cfd, "OWNCommandSession", return_value=session):
        assert await cfd.identify_gateway(MagicMock()) == (None, None, None)
    session.send.assert_not_awaited()
    session.close.assert_awaited()
