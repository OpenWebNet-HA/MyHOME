"""Fan speed on a central unit is refused with a service error (OWNd#77, A2).

The MyHomeServer1 firmware forwards ``*#4*Z*#11*S##`` only for a plain zone 1..99;
``*#4*#0*#11*1##`` and ``*#4*#0#2*#11*1##`` produce no bus frame. OWNd (PR #82)
therefore raises ValueError for a central unit or the general zone. The entity
must turn that into a ServiceValidationError, send nothing and keep its fan mode.
The OWNd builder is patched so the test does not depend on the OWNd release.
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ServiceValidationError

from custom_components.myhome.climate import MyHOMEClimate
from custom_components.myhome.const import DOMAIN


@pytest.fixture
def mock_gateway():
    gw = MagicMock()
    gw.mac = "00:03:50:11:22:77"
    gw.log_id = "[OWNd#77]"
    gw.send = AsyncMock()
    gw.send_status_request = AsyncMock()
    return gw


def _central(hass: HomeAssistant, gateway, where: str) -> MyHOMEClimate:
    climate = MyHOMEClimate(
        hass=hass,
        name="Central Unit",
        device_id=f"4-{where}",
        who="4",
        where=where,
        heating=True,
        cooling=True,
        fan=True,
        standalone=False,
        central=True,
        manufacturer="BTicino",
        model="Central Unit (3550)",
        gateway=gateway,
    )
    climate.hass = hass
    climate.entity_id = "climate.central_unit"
    return climate


@pytest.mark.parametrize("where", ["#0", "#0#1"])
async def test_fan_speed_on_central_unit_raises_service_error(
    hass: HomeAssistant, mock_gateway, where: str
) -> None:
    climate = _central(hass, mock_gateway, where)
    before = climate.fan_mode

    with (
        patch(
            "custom_components.myhome.climate.OWNHeatingCommand.set_fan_speed",
            side_effect=ValueError(f"Fan speed cannot be set on central unit or general zone: {where}"),
        ),
        pytest.raises(ServiceValidationError) as excinfo,
    ):
        await climate.async_set_fan_mode("high")

    assert excinfo.value.translation_domain == DOMAIN
    assert excinfo.value.translation_key == "fan_speed_zone_only"
    mock_gateway.send.assert_not_awaited()
    assert climate.fan_mode == before
