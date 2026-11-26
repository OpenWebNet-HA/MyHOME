"""Zone commanded setpoint (`nominal_temperature`) and zone operation across protection, restore and a
refused write.
"""

import pytest
from homeassistant.components.climate.const import HVACMode
from homeassistant.core import State
from homeassistant.exceptions import HomeAssistantError

from tests.test_climate_setpoint_in_force import (
    F461_POLL_REPLY,
)
from tests.test_climate_setpoint_in_force import (
    _feed as _in_force_feed,
)
from tests.test_climate_setpoint_in_force import (
    _zone as _in_force_zone,
)
from tests.test_climate_standalone_zone_2026_11_18 import (
    _entity_base_init as _entity_base_init,
)
from tests.test_climate_standalone_zone_2026_11_18 import (
    _Plant,
    _replay,
)
from tests.test_climate_standalone_zone_2026_11_18 import (
    _zone as _standalone_zone,
)
from tests.test_climate_zone_operation import _feed, _operation, _zone


async def test_missing_setpoint_success(hass):
    zone = _zone(hass, "3")
    _feed(zone, "*4*102*3##", "*#4*3*12*0070*3##")  # tests/test_climate_zone_operation.py:189
    assert "nominal_temperature" not in zone.extra_state_attributes
    with pytest.raises(HomeAssistantError):
        await zone.async_set_hvac_mode(HVACMode.HEAT)


async def test_no_read_of_missing_nominal(hass):
    zone = _zone(hass, "3")
    _feed(zone, "*4*102*3##", "*#4*3*12*0070*3##")  # tests/test_climate_zone_operation.py:189
    assert "nominal_temperature" not in zone.extra_state_attributes
    assert (
        "*#4*3*14##" in zone._silent_zone_reads()
    )  # tests/test_climate_setpoint_in_force.py:369-371


async def test_off_rolls_back_new_nominal(hass):
    z = _zone(hass, "2")
    _feed(z, "*4*1*2##", "*#4*2*14*0210*3##")  # tests/test_climate_setpoint_in_force.py:20-25
    _feed(
        z, "*#4*2*12*0070*3##", "*#4*2*14*0170*3##", "*4*102*2##"
    )  # tests/test_climate_zone_operation.py:106
    assert z.extra_state_attributes["nominal_temperature"] == 17.0


async def test_preadd_zone_operation(hass):
    zone = _zone(hass, "3")
    # Frame used to seed the entity before HA retrieves the saved state.
    _feed(zone, "*4*110*#3##", "*#4*#3*14*0250*1##")  # tests/test_climate_zone_operation.py:82
    assert (zone.hvac_mode, _operation(zone)) == (HVACMode.HEAT, "manual")
    await zone.async_restore_last_state(
        State(zone.entity_id, "off", {"zone_operation": "antifreeze", "temperature": 7.0})
    )
    assert (zone.hvac_mode, _operation(zone)) == (HVACMode.HEAT, "manual")


async def test_running_nominal_lost(hass):
    live = _in_force_zone(hass)
    _in_force_feed(live, *F461_POLL_REPLY)  # tests/test_climate_setpoint_in_force.py:20-25
    saved = State(
        live.entity_id,
        live.hvac_mode,
        {"temperature": live.target_temperature, **live.extra_state_attributes},
    )
    assert saved.attributes["nominal_temperature"] == 21.0
    restarted = _in_force_zone(hass)
    await restarted.async_restore_last_state(saved)
    await restarted.async_set_hvac_mode(HVACMode.HEAT)
    sent = restarted._gateway_handler.send.await_args.args[0]
    assert str(sent).split("*")[-2] == "0210"


async def test_zone_mode_refusal_not_raised():
    p = _Plant()
    z = _standalone_zone(p, "3", standalone=False)
    _replay(z, ["*4*110*#3##", "*#4*#3*14*0250*1##"])  # tests/test_climate_zone_operation.py:82
    p.nack.add("*#4*#3*#14*0250*1##")  # tests/test_climate_setpoint_in_force.py:309
    with pytest.raises(HomeAssistantError):
        await z.async_set_hvac_mode(HVACMode.HEAT)
