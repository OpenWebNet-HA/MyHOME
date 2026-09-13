"""Repair issues and diagnostics for the MyHOME integration."""
from __future__ import annotations

import logging

from homeassistant.core import HomeAssistant
from homeassistant.helpers.issue_registry import (
    IssueSeverity,
    async_create_issue,
    async_delete_issue,
)

from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)

ISSUE_GATEWAY_AUTH = "gateway_authentication_failed"
ISSUE_BUS_COLLISION = "bus_collision_storm"
ISSUE_GATEWAY_IDENTITY = "gateway_identity_mismatch"


ISSUE_GATEWAY_IDENTITY_CORRECTED = "gateway_identity_corrected"


def async_create_identity_issue(
    hass: HomeAssistant, entry_id: str, configured: str, reported: str, code: str, source: str, official: bool
) -> None:
    """Ask the owner to confirm a gateway whose WHO=13 device type contradicts the configured model."""
    async_create_issue(
        hass,
        DOMAIN,
        f"{ISSUE_GATEWAY_IDENTITY}_{entry_id}",
        is_fixable=False,
        severity=IssueSeverity.WARNING,
        translation_key=ISSUE_GATEWAY_IDENTITY,
        translation_placeholders={
            "configured": configured,
            "reported": reported,
            "code": code,
            "source": source,
            "basis": "the OpenWebNet specification" if official else "field evidence from other installations",
        },
    )


def async_delete_identity_issue(hass: HomeAssistant, entry_id: str) -> None:
    """Clear the identity issue once the reported and configured models agree."""
    async_delete_issue(hass, DOMAIN, f"{ISSUE_GATEWAY_IDENTITY}_{entry_id}")


def async_create_identity_corrected_issue(
    hass: HomeAssistant, entry_id: str, previous: str, corrected: str, code: str
) -> None:
    """Inform the owner that a manually chosen model was corrected from an official WHO=13 code."""
    async_create_issue(
        hass,
        DOMAIN,
        f"{ISSUE_GATEWAY_IDENTITY_CORRECTED}_{entry_id}",
        is_fixable=False,
        severity=IssueSeverity.WARNING,
        translation_key=ISSUE_GATEWAY_IDENTITY_CORRECTED,
        translation_placeholders={"previous": previous, "corrected": corrected, "code": code},
    )


def async_create_auth_issue(hass: HomeAssistant, entry_id: str, gateway_name: str) -> None:
    """Create a repair issue when gateway authentication fails."""
    async_create_issue(
        hass,
        DOMAIN,
        f"{ISSUE_GATEWAY_AUTH}_{entry_id}",
        is_fixable=False,
        severity=IssueSeverity.ERROR,
        translation_key=ISSUE_GATEWAY_AUTH,
        translation_placeholders={"gateway": gateway_name},
    )


def async_delete_auth_issue(hass: HomeAssistant, entry_id: str) -> None:
    """Delete the authentication repair issue once resolved."""
    async_delete_issue(hass, DOMAIN, f"{ISSUE_GATEWAY_AUTH}_{entry_id}")


def async_create_collision_issue(hass: HomeAssistant, entry_id: str, collision_count: int) -> None:
    """Create a repair issue when excessive SCS bus collisions are detected."""
    async_create_issue(
        hass,
        DOMAIN,
        f"{ISSUE_BUS_COLLISION}_{entry_id}",
        is_fixable=False,
        severity=IssueSeverity.WARNING,
        translation_key=ISSUE_BUS_COLLISION,
        translation_placeholders={"count": str(collision_count)},
    )


def async_delete_collision_issue(hass: HomeAssistant, entry_id: str) -> None:
    """Delete the collision repair issue once bus traffic normalizes."""
    async_delete_issue(hass, DOMAIN, f"{ISSUE_BUS_COLLISION}_{entry_id}")
