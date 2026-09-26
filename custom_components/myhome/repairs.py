"""Repair issues and diagnostics for the MyHOME integration."""
from __future__ import annotations

import logging
from collections.abc import Iterable

from homeassistant.core import HomeAssistant
from homeassistant.helpers import issue_registry as ir
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
ISSUE_UNKNOWN_GATEWAY_MODEL = "unknown_gateway_model"
ISSUE_UNCONFIGURED_TIMEZONE = "unconfigured_timezone"

ISSUE_GATEWAY_IDENTITY_CORRECTED = "gateway_identity_corrected"
ISSUE_INCOMPATIBLE_DECODER = "incompatible_decoder_platform"


def async_create_unknown_model_issue(hass: HomeAssistant, entry_id: str, code: str) -> None:
    """Create a repair issue asking the user to report an unknown WHO=13 code."""
    async_create_issue(
        hass,
        DOMAIN,
        f"{ISSUE_UNKNOWN_GATEWAY_MODEL}_{entry_id}",
        is_fixable=False,
        severity=IssueSeverity.WARNING,
        translation_key=ISSUE_UNKNOWN_GATEWAY_MODEL,
        translation_placeholders={"code": code},
        learn_more_url="https://github.com/OpenWebNet-HA/MyHOME/issues/new?template=device_request.yml",
    )


def async_delete_unknown_model_issue(hass: HomeAssistant, entry_id: str) -> None:
    """Delete the unknown model issue."""
    async_delete_issue(hass, DOMAIN, f"{ISSUE_UNKNOWN_GATEWAY_MODEL}_{entry_id}")


def async_create_unconfigured_timezone_issue(hass: HomeAssistant, entry_id: str, gateway_name: str) -> None:
    """Create a repair issue when the gateway reports an unconfigured timezone (999)."""
    async_create_issue(
        hass,
        DOMAIN,
        f"{ISSUE_UNCONFIGURED_TIMEZONE}_{entry_id}",
        is_fixable=False,
        severity=IssueSeverity.WARNING,
        translation_key=ISSUE_UNCONFIGURED_TIMEZONE,
        translation_placeholders={"gateway": gateway_name},
        learn_more_url="https://openwebnet-ha.github.io/MyHOME/beta/diagnostics/repair-issues/#unconfigured-timezone",
    )


def async_delete_unconfigured_timezone_issue(hass: HomeAssistant, entry_id: str) -> None:
    """Delete the unconfigured timezone issue once the gateway returns a valid timezone."""
    async_delete_issue(hass, DOMAIN, f"{ISSUE_UNCONFIGURED_TIMEZONE}_{entry_id}")


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


def async_create_incompatible_decoder_issue(
    hass: HomeAssistant, entry_id: str, decoder_id: str, platform: str
) -> None:
    """Create a repair issue when a configured decoder platform does not support streaming URLs."""
    slug_id = decoder_id.replace(".", "_")
    async_create_issue(
        hass,
        DOMAIN,
        f"{ISSUE_INCOMPATIBLE_DECODER}_{entry_id}_{slug_id}",
        is_fixable=False,
        severity=IssueSeverity.WARNING,
        translation_key=ISSUE_INCOMPATIBLE_DECODER,
        translation_placeholders={"decoder": decoder_id, "platform": platform},
        learn_more_url="https://openwebnet-ha.github.io/MyHOME/beta/configuration/use_cases/#music-assistant",
    )


def async_delete_incompatible_decoder_issue(
    hass: HomeAssistant, entry_id: str, decoder_id: str
) -> None:
    """Delete the incompatible decoder repair issue."""
    slug_id = decoder_id.replace(".", "_")
    async_delete_issue(hass, DOMAIN, f"{ISSUE_INCOMPATIBLE_DECODER}_{entry_id}_{slug_id}")


def async_prune_incompatible_decoder_issues(
    hass: HomeAssistant, entry_id: str, configured_decoders: Iterable[str]
) -> None:
    """Delete the incompatible-decoder issues of decoders that are no longer configured.

    The issue id carries the decoder's entity_id with dots replaced, which
    cannot be turned back into an entity_id; the comparison is therefore made
    on issue ids, built here by the same rule the create helper uses.
    """
    prefix = f"{ISSUE_INCOMPATIBLE_DECODER}_{entry_id}_"
    keep = {
        f"{prefix}{decoder_id.replace('.', '_')}" for decoder_id in configured_decoders
    }
    for domain, issue_id in list(ir.async_get(hass).issues):
        if domain == DOMAIN and issue_id.startswith(prefix) and issue_id not in keep:
            async_delete_issue(hass, DOMAIN, issue_id)
