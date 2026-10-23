"""myhome.send_message sends status and dimension requests as requests (#777).

The #777 reporter sent ``*#1*52*5##`` / ``*#1*52*7##`` / ``*#1*52*6##`` with
``myhome.send_message`` to an F454 where a Legrand 067226 detector shares address 52
with an F411 channel. The replies came back with WHERE 0 and the gateway NACKed the
request. Sent as a command, OWNd logged ``Could not send message ... Retrying (1)``,
sent the request a second time and then logged ``No more retries`` at ERROR. Sent as a
status request, OWNd logs the NACK at DEBUG ("device may not be present") and does not
repeat it, which is what the integration's own status requests already do.
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from custom_components.myhome.const import ATTR_GATEWAY, ATTR_MESSAGE, DOMAIN
from custom_components.myhome.services import SERVICE_SEND_MESSAGE, async_setup_services

MAC = "00:03:50:00:77:77"


@pytest.fixture
async def gateway(hass):
    await async_setup_services(hass)
    gw = MagicMock()
    gw.log_id = "[F454]"
    gw.send = AsyncMock()
    gw.send_status_request = AsyncMock()
    with patch("custom_components.myhome.services._get_gateway_handler", return_value=gw):
        yield gw


async def _send(hass, frame: str) -> None:
    await hass.services.async_call(DOMAIN, SERVICE_SEND_MESSAGE, {ATTR_GATEWAY: MAC, ATTR_MESSAGE: frame}, blocking=True)


@pytest.mark.parametrize(
    "frame",
    [
        "*#1*52##",  # status request
        "*#1*52*5##",  # dimension requests from the #777 capture
        "*#1*52*6##",
        "*#1*52*7##",
        "*#4*2*20##",
    ],
)
async def test_requests_go_out_as_status_requests(hass, gateway, frame: str) -> None:
    await _send(hass, frame)
    gateway.send_status_request.assert_awaited_once()
    assert str(gateway.send_status_request.await_args.args[0]) == frame
    gateway.send.assert_not_awaited()


@pytest.mark.parametrize(
    "frame",
    [
        "*1*1*52##",  # command
        "*#1*52*#1*150*5##",  # dimension writing changes state: a command, not a query
    ],
)
async def test_commands_and_dimension_writes_stay_commands(hass, gateway, frame: str) -> None:
    await _send(hass, frame)
    gateway.send.assert_awaited_once()
    assert str(gateway.send.await_args.args[0]) == frame
    gateway.send_status_request.assert_not_awaited()
