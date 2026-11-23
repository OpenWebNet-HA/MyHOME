"""Read a live session from another tab or socket; recovery never sends a command."""
from typing import Any

import voluptuous as vol
from homeassistant.components.websocket_api.decorators import (
    async_response,
    require_admin,
    websocket_command,
)

from .cover_calibration import send_error
from .cover_profiles import DATA_KEY, ProfileError, target
from .typing_compat import as_any

WS_RESUME = "myhome/cover_calibration/resume"


@websocket_command(as_any({
    vol.Required("type"): WS_RESUME, vol.Required("entry_id"): str,
    vol.Required("session_id"): str,
    vol.Required("client_id"): vol.All(str, vol.Length(min=1, max=64)),
    vol.Optional("claim"): bool,
    vol.Optional("sequence"): vol.All(int, vol.Range(min=0)),
}))
@require_admin
@async_response
async def ws_resume(hass: Any, connection: Any, msg: dict[str, Any]) -> None:
    try:
        store = hass.data.get(DATA_KEY, {}).get(msg["entry_id"])
        if store is None:
            raise ProfileError("calibration_expired")
        async with store.lock:
            session = store.calibration
            if session is None or session.id != msg["session_id"]:
                raise ProfileError("calibration_expired")
            target(hass, session.entry_id, session.cover.entity_id)
            # Without a claim, or with a sequence the client did not just read, the
            # new subscription only reads: an automatic replay cannot take control.
            subscriber, claimed = session.attach(connection, msg["id"], msg["client_id"],
                                                 claim=msg.get("claim", False), sequence=msg.get("sequence"))
        connection.send_result(msg["id"])
        if claimed:
            session.emit()
        else:
            session.send_to(subscriber)
    except ProfileError as error:
        send_error(connection, msg, error)
