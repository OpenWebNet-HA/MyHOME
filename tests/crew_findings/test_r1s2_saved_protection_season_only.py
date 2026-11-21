# ruff: noqa
from homeassistant.components.climate.const import HVACMode
from homeassistant.core import State
from OWNd.message import OWNMessage

from tests.test_crew_central_season_restore import HEAT, _climate, _gateway, _sent
from tests.test_crew_central_season_restore import _entity_base_init as _entity_base_init


async def test_saved_protection_season_only():
    gw = _gateway()
    cu = _climate(gw, "#0", standalone=None, central=True)
    await cu.async_restore_last_state(
        State(cu.entity_id, "heat", {"central_operation": "antifreeze"})
    )
    # A season selector, e.g. from another client; not an operating-mode report.
    cu.handle_event(OWNMessage.parse(HEAT))
    await cu.async_set_hvac_mode(HVACMode.HEAT)
    assert _sent(gw) == [HEAT], (
        "season confirmed, but the only protection information came from storage"
    )
