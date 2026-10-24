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


def _password_then_ok() -> AsyncMock:
    """A password-protected gateway: the first probe asks for the password, the second accepts it."""
    return AsyncMock(side_effect=[{"Success": False, "Message": "password_required"}, {"Success": True}])


async def _manual_flow(
    hass: HomeAssistant,
    test_connection: AsyncMock,
    identify: AsyncMock,
    mac: str = "00:03:50:00:00:00",
    model: str = "MyHomeServer1",
):
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
        # By default the values the form offers when the user does not know better
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {"serialNumber": mac, "modelName": model}
        )
        assert result["type"] == FlowResultType.FORM
        assert result["step_id"] == "password"
        result = await hass.config_entries.flow.async_configure(result["flow_id"], {"password": "12345"})
        if result["type"] == FlowResultType.FORM and result["step_id"] == "bus_topology":
            # Another IP gateway exists: the topology question comes before the entry (#524)
            result = await hass.config_entries.flow.async_configure(
                result["flow_id"], {"bus_topology": "standalone"}
            )
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
    assert result["data"]["model_source"] == "who13"
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
    assert result["data"]["model_source"] == "manual"


@pytest.mark.parametrize("model", ["H4890", "LN4890"])
async def test_manual_flow_keeps_a_compatible_brand_variant(hass: HomeAssistant, model: str) -> None:
    """WHO=1013 code 30 names the AM4890 and its brand variants: the chosen variant stays (#466)."""
    identify = AsyncMock(return_value=(None, "200", "30"))

    result = await _manual_flow(hass, _password_then_ok(), identify, model=model)

    assert result["type"] == FlowResultType.CREATE_ENTRY
    assert result["data"]["name"] == model
    assert result["title"] == f"{model} Gateway"
    assert result["data"]["model_source"] == "manual"


async def test_manual_flow_keeps_a_legrand_name_of_the_reported_model(hass: HomeAssistant) -> None:
    """003598 is the Legrand name of the F454 (WHO=1013 code 51): not a correction."""
    identify = AsyncMock(return_value=(None, "200", "51"))

    result = await _manual_flow(hass, _password_then_ok(), identify, model="003598")

    assert result["type"] == FlowResultType.CREATE_ENTRY
    assert result["data"]["name"] == "003598"
    assert result["data"]["model_source"] == "manual"


@pytest.mark.parametrize(
    ("model", "codes", "expected_name", "expected_source"),
    [
        ("Generic", ("44", None), "MH200N", "who13"),
        ("MH202", ("44", None), "MH202", "manual"),
        ("Generic", ("200", "134"), "F461", "who13"),
    ],
)
async def test_manual_flow_uncertain_who13_code_labels_generic_but_never_overrides_a_choice(
    hass: HomeAssistant, model: str, codes: tuple[str | None, str | None], expected_name: str, expected_source: str
) -> None:
    """Code 44 is known from Nmap only (certain=False): it may label a Generic entry, not contradict a chosen model;
    a Generic entry is labelled from WHO=1013 as well."""
    identify = AsyncMock(return_value=(None, *codes))

    result = await _manual_flow(hass, _password_then_ok(), identify, model=model)

    assert result["type"] == FlowResultType.CREATE_ENTRY
    assert result["data"]["name"] == expected_name
    assert result["title"] == f"{expected_name} Gateway"
    assert result["data"]["model_source"] == expected_source


async def test_apply_gateway_identity_refreshes_the_profile_and_log_id(hass: HomeAssistant) -> None:
    """The corrected model drives the OWNd profile and the log prefix, as the runtime's _apply_model does."""
    from OWNd.connection import OWNGateway

    from custom_components.myhome.config_flow import MyhomeFlowHandler

    flow = MyhomeFlowHandler()
    flow.hass = hass
    gateway = OWNGateway(
        {"address": "192.0.2.92", "port": 20000, "serialNumber": "00:03:50:00:00:00", "modelName": "MyHomeServer1"}
    )
    with patch(
        "custom_components.myhome.config_flow.identify_gateway", AsyncMock(return_value=(None, "200", "134"))
    ):
        source = await flow._async_apply_gateway_identity(gateway)

    assert source == "who13"
    assert gateway.model_name == "F461"
    assert type(gateway.profile).__name__ == "F461Profile"
    assert gateway.log_id == "[F461 gateway - 192.0.2.92]"


@pytest.mark.parametrize(
    "frame",
    [
        "*#13**12*0*0*0*0*0*0##",  # six zeros: the factory value, and the UI sentinel of the first form
        "*#13**12*255*255*255*255*255*255##",  # broadcast
        "*#13**12*256*3*80*0*7*119##",  # an octet outside 0..255 (OWNd formats it as "100")
    ],
)
async def test_manual_flow_keeps_the_typed_mac_when_the_reported_one_names_no_interface(
    hass: HomeAssistant, frame: str
) -> None:
    reported = OWNMessage.parse(frame).mac_address
    assert reported  # OWNd decodes all three; the flow is what must refuse them
    identify = AsyncMock(return_value=(reported, "200", "134"))

    result = await _manual_flow(hass, _password_then_ok(), identify, mac="00:03:50:00:12:34")

    assert result["type"] == FlowResultType.CREATE_ENTRY
    assert result["data"]["mac"] == "00:03:50:00:12:34"
    assert result["result"].unique_id == "00:03:50:00:12:34"
    assert result["data"]["name"] == "F461"


def test_validated_reported_mac() -> None:
    from custom_components.myhome.config_flow_discovery import validated_reported_mac

    assert validated_reported_mac("00:03:50:00:07:77") == "00:03:50:00:07:77"
    assert validated_reported_mac("00:03:50:00:07:77".upper()) == "00:03:50:00:07:77"
    assert validated_reported_mac("00:00:00:00:00:00") is None
    assert validated_reported_mac("ff:ff:ff:ff:ff:ff") is None
    assert validated_reported_mac("FF:FF:FF:FF:FF:FF") is None
    assert validated_reported_mac("100:00:00:00:00:00") is None
    assert validated_reported_mac("00:03:50:00:07") is None
    assert validated_reported_mac("not a mac") is None
    assert validated_reported_mac(None) is None
    assert validated_reported_mac("") is None


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


async def test_manual_flow_adds_a_second_gateway_typed_with_the_placeholder_mac(hass: HomeAssistant) -> None:
    """An F454 kept the placeholder as unique_id (its MAC is never asked); an F461 typed with the same
    placeholder is not refused before it can report its real MAC."""
    from pytest_homeassistant_custom_component.common import MockConfigEntry

    MockConfigEntry(
        domain=DOMAIN,
        data={"host": "192.0.2.54", "mac": "00:03:50:00:00:00", "name": "F454"},
        unique_id="00:03:50:00:00:00",
    ).add_to_hass(hass)
    identify = AsyncMock(return_value=("00:03:50:00:07:77", "200", "134"))

    result = await _manual_flow(hass, _password_then_ok(), identify, model="F461")

    assert result["type"] == FlowResultType.CREATE_ENTRY
    assert result["data"]["mac"] == "00:03:50:00:07:77"
    assert result["result"].unique_id == "00:03:50:00:07:77"
    assert result["data"]["name"] == "F461"
    assert len(hass.config_entries.async_entries(DOMAIN)) == 2


async def test_manual_flow_still_refuses_a_duplicate_placeholder_the_gateway_cannot_resolve(
    hass: HomeAssistant,
) -> None:
    """Two gateways whose MAC is never learned cannot share the placeholder: the second aborts after auth."""
    from pytest_homeassistant_custom_component.common import MockConfigEntry

    MockConfigEntry(
        domain=DOMAIN,
        data={"host": "192.0.2.54", "mac": "00:03:50:00:00:00", "name": "F454"},
        unique_id="00:03:50:00:00:00",
    ).add_to_hass(hass)
    identify = AsyncMock(return_value=(None, "200", "5"))  # an MH202: not in MAC_REQUEST_SAFE_MODELS

    result = await _manual_flow(hass, _password_then_ok(), identify, model="MH202")

    assert result["type"] == FlowResultType.ABORT
    assert result["reason"] == "already_configured"
    identify.assert_awaited_once()


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
