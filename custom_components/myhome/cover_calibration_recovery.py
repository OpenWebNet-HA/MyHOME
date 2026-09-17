"""Explicit attachment to a retained session; recovery never sends a command."""
from typing import Any

import voluptuous as vol
from homeassistant.components.websocket_api.decorators import (
    async_response,
    require_admin,
    websocket_command,
)

from .cover_calibration import send_error
from .cover_profiles import DATA_KEY, ProfileError, target

WS_RESUME = "myhome/cover_calibration/resume"


@websocket_command({
    vol.Required("type"): WS_RESUME, vol.Required("entry_id"): str,
    vol.Required("session_id"): str,
    vol.Required("client_id"): vol.All(str, vol.Length(min=1, max=64)),
})
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
            session.attach(connection, msg["id"], msg["client_id"])
        connection.send_result(msg["id"])
        session.emit()
    except ProfileError as error:
        send_error(connection, msg, error)
