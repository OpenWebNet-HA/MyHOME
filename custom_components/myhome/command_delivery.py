"""Tell the caller of an entity action when its command did not reach the bus.

``MyHOMEGatewayHandler.send()`` only queues a frame and returns a delivery future. The
command worker resolves it with the write time once the gateway acknowledged the frame, and
cancels it when the gateway answered ``*#*0##`` (NACK), when the command session was lost
or could not be opened, or when the queue was closed. An action that does not look at that
future returns success whatever happened, which is how the WHO 14 lock / unlock buttons
failed silently on an F455 (firmware 1.0.76 answers ``*14*0*05##`` and ``*14*1*05##`` with
NACK; an F461 2.0.11 acknowledges both).

This is the pattern ``lock.py`` applies to the WHO 6 door-entry unlock (#835), with the
gateway's NACK told apart from a frame that was never delivered.
"""
from __future__ import annotations

import asyncio
from typing import Any

from homeassistant.exceptions import HomeAssistantError

from .const import DOMAIN

# As LOCK_WRITE_TIMEOUT (lock.py): reopening an idle command session may take OWNd's whole
# reconnect loop (MAX_CONNECT_ATTEMPTS = 5, about 13.3 s) before the frame is written.
COMMAND_DELIVERY_TIMEOUT = 15.0


def gateway_label(gateway: Any) -> str:
    """Human-readable gateway model / name for error placeholders."""
    for attr in ("model", "name", "mac"):
        val = getattr(gateway, attr, None)
        if isinstance(val, str) and val.strip():
            return val.strip()
    return "MyHOME gateway"


async def async_confirm_delivery(
    written: Any,
    *,
    name: str,
    frame: str,
    gateway: Any,
    timeout: float = COMMAND_DELIVERY_TIMEOUT,
) -> None:
    """Wait for a queued command's delivery future; raise a translated error if it failed.

    ``written`` is what ``MyHOMEGatewayHandler.send()`` returned. Anything that is not a
    future (a gateway double without delivery information) is taken as delivered, as the
    cover and lock platforms do. A cancellation of the calling task is passed on untouched.
    """
    if not isinstance(written, asyncio.Future):
        return
    placeholders = {"name": name, "gateway": gateway_label(gateway), "frame": frame}
    done, _pending = await asyncio.wait((written,), timeout=timeout)
    if not done:
        # Withdraw the queued frame: the worker skips a cancelled future, so a command
        # the user was told had failed is not written later.
        written.cancel()
        raise HomeAssistantError(
            f"{name}: gateway {placeholders['gateway']} did not confirm the command ({frame}) "
            f"within {timeout:.0f} s; it was withdrawn if it had not been sent yet",
            translation_domain=DOMAIN,
            translation_key="command_not_confirmed",
            translation_placeholders={**placeholders, "timeout": f"{timeout:.0f}"},
        )
    cause: BaseException | None = None
    if not written.cancelled():
        cause = written.exception()
        if cause is None:
            return
    if getattr(written, "_gateway_nack", False) is True:
        # The worker marks the future of a frame the gateway answered with *#*0##.
        raise HomeAssistantError(
            f"{name}: gateway {placeholders['gateway']} rejected the command ({frame}) with NACK",
            translation_domain=DOMAIN,
            translation_key="command_rejected",
            translation_placeholders=placeholders,
        )
    # Cancelled without that mark: the session was lost or could not be opened, or the
    # queue was closed (unload, shutdown).
    raise HomeAssistantError(
        f"{name}: the command ({frame}) could not be delivered to gateway {placeholders['gateway']}",
        translation_domain=DOMAIN,
        translation_key="command_not_delivered",
        translation_placeholders=placeholders,
    ) from cause


async def async_send_confirmed(
    gateway: Any,
    command: Any,
    *,
    name: str,
    timeout: float = COMMAND_DELIVERY_TIMEOUT,
) -> None:
    """Queue ``command`` on ``gateway`` and return once it was acknowledged, else raise."""
    written = await gateway.send(command)
    await async_confirm_delivery(written, name=name, frame=str(command), gateway=gateway, timeout=timeout)
