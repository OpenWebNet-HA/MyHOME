"""Repair issues and diagnostics for the MyHOME integration."""
from __future__ import annotations

import asyncio
import logging
import math
import re
import time
from collections.abc import Iterable, Mapping
from typing import Any

import voluptuous as vol
from homeassistant.components.repairs import RepairsFlow, RepairsFlowResult
from homeassistant.const import CONF_MAC
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers import issue_registry as ir
from homeassistant.helpers import selector
from homeassistant.helpers.entity_component import DATA_INSTANCES
from homeassistant.helpers.issue_registry import (
    IssueSeverity,
    async_create_issue,
    async_delete_issue,
)

from .const import (
    CALIBRATION_MIN_RUN,
    CONF_BUS_TOPOLOGY,
    CONF_DELEGATED_WHOS,
    CONF_GATEWAY_ROLE,
    CONF_PRIMARY_GATEWAY,
    DEFAULT_TRAVEL_TIME,
    DOMAIN,
    ISSUE_GATEWAY_FAILOVER,
    ISSUE_PRIMARY_GATEWAY_MISSING,
    ISSUE_SHARED_BUS_DETECTED,
    ROLE_PRIMARY,
    ROLE_SECONDARY,
    SERVICE_RESET_COVER_TRAVEL_TIME,
    SERVICE_SET_COVER_TRAVEL_TIME,
    TOPOLOGY_SHARED,
)
from .data import get_runtime_data
from .discovery import parse_unique_id
from .topology import (
    entry_for_mac,
    entry_mac,
    entry_model,
    infer_shared_bus_topology,
    validate_shared_bus_topology,
)

_LOGGER = logging.getLogger(__name__)

ISSUE_GATEWAY_AUTH = "gateway_authentication_failed"
ISSUE_BUS_COLLISION = "bus_collision_storm"
ISSUE_GATEWAY_IDENTITY = "gateway_identity_mismatch"
ISSUE_UNKNOWN_GATEWAY_MODEL = "unknown_gateway_model"
ISSUE_UNCONFIGURED_TIMEZONE = "unconfigured_timezone"

ISSUE_GATEWAY_IDENTITY_CORRECTED = "gateway_identity_corrected"
ISSUE_INCOMPATIBLE_DECODER = "incompatible_decoder_platform"
ISSUE_INVALID_DECODER = "invalid_decoder"
ISSUE_AMBIGUOUS_COMPANION = "ambiguous_companion"
ISSUE_MULTIPLE_AUDIO_GATEWAYS = "multiple_audio_gateways"
ISSUE_LEGACY_YAML_IMPORTED = "legacy_yaml_imported"
ISSUE_COVER_CALIBRATION_CUTOFF = "cover_calibration_cutoff"
ISSUE_COVER_LEGACY_CUTOFF = "cover_legacy_calibration_cutoff"

_MANUAL_TRAVEL_MAX_SECONDS = 600.0
_STOP_ECHO_WAIT_SECONDS = 0.35


def async_create_legacy_yaml_imported_issue(
    hass: HomeAssistant, entry_id: str, gateway_name: str
) -> None:
    """Create an informational repair issue when myhome.yaml is migrated to ConfigEntry.options."""
    async_create_issue(
        hass,
        DOMAIN,
        f"{ISSUE_LEGACY_YAML_IMPORTED}_{entry_id}",
        is_fixable=False,
        severity=IssueSeverity.WARNING,
        translation_key=ISSUE_LEGACY_YAML_IMPORTED,
        translation_placeholders={"gateway": gateway_name},
    )


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
        learn_more_url="https://openwebnet-ha.github.io/MyHOME/latest/diagnostics/repair-issues/#unconfigured-timezone",
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


def _canonical_shared_bus_pair(mac_a: str, mac_b: str) -> tuple[str, str, str]:
    """Return sorted clean MACs and canonical issue ID."""
    clean_a = mac_a.replace(":", "").lower()
    clean_b = mac_b.replace(":", "").lower()
    first, second = sorted([clean_a, clean_b])
    return first, second, f"{ISSUE_SHARED_BUS_DETECTED}_{first}_{second}"


def async_create_shared_bus_issue(hass: HomeAssistant, mac_a: str, mac_b: str) -> None:
    """Create a repair issue when two gateways observe the same SCS bus traffic."""
    _, _, issue_id = _canonical_shared_bus_pair(mac_a, mac_b)
    disp_a, disp_b = sorted([mac_a, mac_b])

    _LOGGER.info("Detected shared SCS bus between %s and %s; repair issue created (%s)", disp_a, disp_b, issue_id)
    async_create_issue(
        hass,
        DOMAIN,
        issue_id,
        is_fixable=True,
        severity=IssueSeverity.WARNING,
        translation_key=ISSUE_SHARED_BUS_DETECTED,
        translation_placeholders={
            "gateway_a": disp_a,
            "gateway_b": disp_b,
        },
        learn_more_url="https://openwebnet-ha.github.io/MyHOME/latest/diagnostics/repair-issues/#unconfigured-shared-bus-detected",
        data={"mac_a": mac_a, "mac_b": mac_b},
    )


def async_delete_shared_bus_issue(hass: HomeAssistant, mac_a: str, mac_b: str) -> None:
    """Delete the shared bus repair issue once the gateways are configured."""
    clean_a, clean_b, issue_id = _canonical_shared_bus_pair(mac_a, mac_b)
    _LOGGER.debug("Dismissed shared SCS bus repair issue %s for %s and %s", issue_id, mac_a, mac_b)
    async_delete_issue(hass, DOMAIN, issue_id)
    domain_data = hass.data.get(DOMAIN)
    if isinstance(domain_data, dict):
        evidence_map = domain_data.get("_shared_bus_evidence")
        if isinstance(evidence_map, dict):
            evidence_map.pop((clean_a, clean_b), None)
            evidence_map.pop((mac_a, mac_b), None)
            evidence_map.pop((mac_b, mac_a), None)
            evidence_map.pop(tuple(sorted([mac_a, mac_b])), None)


def async_create_failover_issue(
    hass: HomeAssistant,
    primary_mac: str,
    standby_mac: str,
    primary_name: str,
    standby_name: str,
) -> None:
    """Create a repair issue when primary gateway fails over to standby."""
    clean_pri = primary_mac.replace(":", "").lower()
    issue_id = f"{ISSUE_GATEWAY_FAILOVER}_{clean_pri}"
    _LOGGER.warning(
        "Primary gateway %s (%s) offline; failover activated on warm standby %s (%s)",
        primary_name,
        primary_mac,
        standby_name,
        standby_mac,
    )
    async_create_issue(
        hass,
        DOMAIN,
        issue_id,
        is_fixable=False,
        severity=IssueSeverity.WARNING,
        translation_key=ISSUE_GATEWAY_FAILOVER,
        translation_placeholders={
            "primary": f"{primary_name} ({primary_mac})",
            "standby": f"{standby_name} ({standby_mac})",
        },
        learn_more_url="https://openwebnet-ha.github.io/MyHOME/latest/diagnostics/repair-issues/#gateway-failover-active-warm-standby-high-availability",
    )


def async_delete_failover_issue(hass: HomeAssistant, primary_mac: str) -> None:
    """Delete the failover repair issue once primary gateway reconnects."""
    clean_pri = primary_mac.replace(":", "").lower()
    issue_id = f"{ISSUE_GATEWAY_FAILOVER}_{clean_pri}"
    _LOGGER.info("Primary gateway %s reconnected; failover resolved", primary_mac)
    async_delete_issue(hass, DOMAIN, issue_id)


def async_create_primary_missing_issue(hass: HomeAssistant, entry_id: str, gateway_name: str, primary: str) -> None:
    """A secondary/standby whose primary is gone or no longer a shared primary."""
    async_create_issue(
        hass,
        DOMAIN,
        f"{ISSUE_PRIMARY_GATEWAY_MISSING}_{entry_id}",
        is_fixable=False,
        severity=IssueSeverity.WARNING,
        translation_key=ISSUE_PRIMARY_GATEWAY_MISSING,
        translation_placeholders={"gateway": gateway_name, "primary": primary},
        learn_more_url="https://openwebnet-ha.github.io/MyHOME/latest/diagnostics/repair-issues/#primary-gateway-missing",
    )


def async_delete_primary_missing_issue(hass: HomeAssistant, entry_id: str) -> None:
    """Delete the missing-primary issue once the secondary points at a valid primary."""
    async_delete_issue(hass, DOMAIN, f"{ISSUE_PRIMARY_GATEWAY_MISSING}_{entry_id}")


def async_create_incompatible_decoder_issue(
    hass: HomeAssistant, entry_id: str, decoder_id: str, platform: str
) -> None:
    """Create a repair issue when a configured decoder platform does not support streaming URLs."""
    slug_id = decoder_id.replace(".", "_")
    async_create_issue(
        hass,
        DOMAIN,
        f"{ISSUE_INCOMPATIBLE_DECODER}_{entry_id}_{slug_id}",
        is_fixable=True,
        severity=IssueSeverity.WARNING,
        translation_key=ISSUE_INCOMPATIBLE_DECODER,
        translation_placeholders={"decoder": decoder_id, "platform": platform},
        learn_more_url="https://openwebnet-ha.github.io/MyHOME/latest/configuration/use_cases/#music-assistant",
        data={"entry_id": entry_id, "decoder_id": decoder_id, "platform": platform},
    )


def async_create_decoder_config_issue(
    hass: HomeAssistant,
    entry_id: str,
    decoder_id: str,
    issue_key: str,
    placeholders: dict[str, str],
) -> None:
    """Flag a decoder slot the user has to fix in the options (not fixable from the repair)."""
    async_create_issue(
        hass,
        DOMAIN,
        f"{issue_key}_{entry_id}_{decoder_id.replace('.', '_')}",
        is_fixable=False,
        severity=IssueSeverity.WARNING,
        translation_key=issue_key,
        translation_placeholders={"decoder": decoder_id, **placeholders},
        learn_more_url="https://openwebnet-ha.github.io/MyHOME/latest/configuration/use_cases/#music-assistant",
    )


def async_sync_decoder_config_issues(
    hass: HomeAssistant, entry_id: str, issue_key: str, flagged: Iterable[str]
) -> None:
    """Drop the ``issue_key`` issues of decoders that are no longer flagged."""
    prefix = f"{issue_key}_{entry_id}_"
    keep = {f"{prefix}{decoder_id.replace('.', '_')}" for decoder_id in flagged}
    for domain, issue_id in list(ir.async_get(hass).issues):
        if domain == DOMAIN and issue_id.startswith(prefix) and issue_id not in keep:
            async_delete_issue(hass, DOMAIN, issue_id)


def async_sync_multiple_audio_gateways_issue(hass: HomeAssistant, gateway_titles: list[str]) -> None:
    """Warn while more than one gateway serves sound zones (see issue #426).

    Sound addressing drops the bus interface, so the zones of two audio matrices
    on different gateways can collide on one entity id and one environment.
    """
    if len(gateway_titles) < 2:
        async_delete_issue(hass, DOMAIN, ISSUE_MULTIPLE_AUDIO_GATEWAYS)
        return
    async_create_issue(
        hass,
        DOMAIN,
        ISSUE_MULTIPLE_AUDIO_GATEWAYS,
        is_fixable=False,
        severity=IssueSeverity.WARNING,
        translation_key=ISSUE_MULTIPLE_AUDIO_GATEWAYS,
        translation_placeholders={"gateways": ", ".join(sorted(gateway_titles))},
        learn_more_url="https://github.com/OpenWebNet-HA/MyHOME/issues/426",
    )


class IncompatibleDecoderRepairFlow(RepairsFlow):
    """Handler for fixing an incompatible streaming decoder."""

    def __init__(self, data: dict[str, Any]) -> None:
        """Initialize the flow."""
        self._entry_id: str = str(data.get("entry_id") or "")
        self._decoder_id: str = str(data.get("decoder_id") or "")
        self._platform: str = str(data.get("platform") or "")
        self._companion_id: str | None = None

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> RepairsFlowResult:
        """Handle the first step of the repair flow."""
        from .decoder_companion import async_find_streaming_companion

        self._companion_id = async_find_streaming_companion(self.hass, self._decoder_id)

        if self._companion_id:
            return await self.async_step_confirm_companion()
        return await self.async_step_missing_companion()

    async def async_step_confirm_companion(
        self, user_input: dict[str, Any] | None = None
    ) -> RepairsFlowResult:
        """Confirm adopting the DLNA companion for the incompatible decoder."""
        if user_input is not None:
            entry = self.hass.config_entries.async_get_entry(self._entry_id)
            if entry and self._companion_id:
                # We do not rewrite the slot; we just reload the entry so the dynamic
                # bridge discovers the companion during setup.
                self.hass.async_create_task(
                    self.hass.config_entries.async_reload(self._entry_id)
                )
            return self.async_create_entry(data={})

        return self.async_show_form(
            step_id="confirm_companion",
            description_placeholders={
                "decoder": self._decoder_id,
                "platform": self._platform,
                "companion": self._companion_id or "",
            },
        )

    async def async_step_missing_companion(
        self, user_input: dict[str, Any] | None = None
    ) -> RepairsFlowResult:
        """Inform the user how to configure DLNA DMR for this device."""
        if user_input is not None:
            from .decoder_companion import async_find_streaming_companion

            companion = async_find_streaming_companion(self.hass, self._decoder_id)
            if companion:
                self._companion_id = companion
                return await self.async_step_confirm_companion()
            return self.async_abort(reason="companion_still_missing")

        return self.async_show_form(
            step_id="missing_companion",
            description_placeholders={
                "decoder": self._decoder_id,
                "platform": self._platform,
            },
        )


class SharedBusRepairFlow(RepairsFlow):
    """Handler for automatically configuring an unconfigured shared bus."""

    def __init__(self, data: dict[str, Any]) -> None:
        """Initialize the repair flow."""
        self._mac_a: str = str(data.get("mac_a") or "")
        self._mac_b: str = str(data.get("mac_b") or "")

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> RepairsFlowResult:
        """Confirm applying the recommended shared-bus topology."""
        entry_a = entry_for_mac(self.hass, self._mac_a)
        entry_b = entry_for_mac(self.hass, self._mac_b)

        if not entry_a or not entry_b:
            return self.async_abort(reason="gateway_missing")

        rec = infer_shared_bus_topology(entry_a, entry_b)
        pri_entry = entry_a if entry_mac(entry_a) == rec.primary_mac else entry_b
        sec_entry = entry_b if pri_entry is entry_a else entry_a

        if user_input is not None:
            # Prepare Primary settings
            pri_opts = dict(pri_entry.options)
            pri_opts[CONF_BUS_TOPOLOGY] = TOPOLOGY_SHARED
            pri_opts[CONF_GATEWAY_ROLE] = ROLE_PRIMARY
            pri_opts.pop(CONF_PRIMARY_GATEWAY, None)
            pri_opts.pop(CONF_DELEGATED_WHOS, None)

            # Prepare Secondary/Standby settings
            sec_opts = dict(sec_entry.options)
            sec_opts[CONF_BUS_TOPOLOGY] = TOPOLOGY_SHARED
            sec_opts[CONF_GATEWAY_ROLE] = rec.role
            sec_opts[CONF_PRIMARY_GATEWAY] = rec.primary_mac
            if rec.role == ROLE_SECONDARY:
                sec_opts[CONF_DELEGATED_WHOS] = sorted(rec.delegated_whos)
            else:
                sec_opts.pop(CONF_DELEGATED_WHOS, None)

            # Atomic validation: validate BOTH primary and secondary proposed topologies
            # BEFORE applying changes to either config entry. If either validation fails,
            # abort without modifying either entry.
            pri_errs = validate_shared_bus_topology(self.hass, pri_entry, pri_opts)
            sec_errs = validate_shared_bus_topology(
                self.hass, sec_entry, sec_opts, target_primary_options=pri_opts
            )
            if pri_errs or sec_errs:
                return self.async_abort(reason="invalid_topology")

            orig_pri_opts = dict(pri_entry.options)
            orig_sec_opts = dict(sec_entry.options)
            try:
                self.hass.config_entries.async_update_entry(pri_entry, options=pri_opts)
                self.hass.config_entries.async_update_entry(sec_entry, options=sec_opts)
            except Exception:
                self.hass.config_entries.async_update_entry(pri_entry, options=orig_pri_opts)
                self.hass.config_entries.async_update_entry(sec_entry, options=orig_sec_opts)
                raise

            # Reload both entries
            self.hass.async_create_task(
                self.hass.config_entries.async_reload(pri_entry.entry_id)
            )
            self.hass.async_create_task(
                self.hass.config_entries.async_reload(sec_entry.entry_id)
            )

            async_delete_shared_bus_issue(self.hass, self._mac_a, self._mac_b)
            _LOGGER.info(
                "Applied recommended shared bus topology via 1-click repair: Primary=%s (%s), Follower=%s (%s, role=%s, delegated WHOs=%s)",
                entry_model(pri_entry),
                rec.primary_mac,
                entry_model(sec_entry),
                rec.secondary_mac,
                rec.role,
                sorted(rec.delegated_whos),
            )
            return self.async_create_entry(data={})

        subsystems = (
            ", ".join(f"WHO {w}" for w in sorted(rec.delegated_whos))
            if rec.delegated_whos
            else "None (Warm Standby failover)"
        )
        return self.async_show_form(
            step_id="init",
            description_placeholders={
                "primary": f"{entry_model(pri_entry)} ({rec.primary_mac})",
                "secondary": f"{entry_model(sec_entry)} ({rec.secondary_mac})",
                "role": rec.role.title(),
                "subsystems": subsystems,
                "rationale": rec.rationale,
            },
        )


def _cover_issue_slug(device_id: str) -> str:
    """Return a stable, registry-safe slug for a cover device_id."""
    return re.sub(r"[^a-zA-Z0-9_-]", "_", str(device_id))


def async_create_cover_cutoff_issue(
    hass: HomeAssistant,
    entry_id: str,
    device_id: str,
    entity_id: str | None,
    cover_name: str,
    limit_seconds: float,
    elapsed_seconds: float,
) -> None:
    """Create a fixable repair issue when auto-calibration hits the actuator's run-time limit."""
    slug = _cover_issue_slug(device_id)
    limit_str = f"{limit_seconds:.0f}"
    seconds_str = f"{elapsed_seconds:.1f}"
    async_create_issue(
        hass,
        DOMAIN,
        f"{ISSUE_COVER_CALIBRATION_CUTOFF}_{entry_id}_{slug}",
        is_fixable=True,
        severity=IssueSeverity.WARNING,
        translation_key=ISSUE_COVER_CALIBRATION_CUTOFF,
        translation_placeholders={
            "name": cover_name,
            "limit": limit_str,
            "seconds": seconds_str,
        },
        learn_more_url="https://openwebnet-ha.github.io/MyHOME/latest/diagnostics/repair-issues/#cover-calibration-cutoff",
        data={
            "entry_id": entry_id,
            "device_id": str(device_id),
            "entity_id": entity_id or "",
            "cover_name": cover_name,
            "reason": "cutoff",
            "limit": limit_str,
            "seconds": seconds_str,
        },
    )


def async_delete_cover_cutoff_issue(
    hass: HomeAssistant, entry_id: str, device_id: str
) -> None:
    """Delete any active cutoff or legacy cutoff repair issue for a cover."""
    slug = _cover_issue_slug(device_id)
    async_delete_issue(hass, DOMAIN, f"{ISSUE_COVER_CALIBRATION_CUTOFF}_{entry_id}_{slug}")
    async_delete_issue(hass, DOMAIN, f"{ISSUE_COVER_LEGACY_CUTOFF}_{entry_id}_{slug}")


def async_sync_legacy_cover_cutoff_issues(
    hass: HomeAssistant,
    entry_id: str,
    flagged_covers: list[dict[str, Any]],
) -> None:
    """Create/update per-cover legacy cutoff repair issues and prune resolved ones."""
    prefix = f"{ISSUE_COVER_LEGACY_CUTOFF}_{entry_id}_"
    keep: set[str] = set()

    for item in flagged_covers:
        device_id = str(item.get("device_id") or "")
        if not device_id:
            continue
        slug = _cover_issue_slug(device_id)
        issue_id = f"{prefix}{slug}"
        keep.add(issue_id)

        cover_name = str(item.get("name") or item.get("cover_name") or device_id)
        entity_id = str(item.get("entity_id") or "")
        raw_down = item.get("down")
        raw_up = item.get("up")
        down_str = (
            f"{float(raw_down):.1f}"
            if isinstance(raw_down, (int, float)) and not isinstance(raw_down, bool)
            else "?"
        )
        up_str = (
            f"{float(raw_up):.1f}"
            if isinstance(raw_up, (int, float)) and not isinstance(raw_up, bool)
            else "?"
        )

        async_create_issue(
            hass,
            DOMAIN,
            issue_id,
            is_fixable=True,
            severity=IssueSeverity.WARNING,
            translation_key=ISSUE_COVER_LEGACY_CUTOFF,
            translation_placeholders={
                "name": cover_name,
                "down": down_str,
                "up": up_str,
            },
            learn_more_url="https://openwebnet-ha.github.io/MyHOME/latest/diagnostics/repair-issues/#cover-legacy-calibration-cutoff",
            data={
                "entry_id": entry_id,
                "device_id": device_id,
                "entity_id": entity_id,
                "cover_name": cover_name,
                "reason": "legacy",
                "down": down_str,
                "up": up_str,
            },
        )

    for domain, issue_id in list(ir.async_get(hass).issues):
        if domain == DOMAIN and issue_id.startswith(prefix) and issue_id not in keep:
            async_delete_issue(hass, DOMAIN, issue_id)


def _travel_time_schema(default_down: float, default_up: float) -> vol.Schema:
    """Return the voluptuous schema for entering down/up travel times."""
    num_selector = selector.NumberSelector(
        selector.NumberSelectorConfig(
            min=CALIBRATION_MIN_RUN,
            max=_MANUAL_TRAVEL_MAX_SECONDS,
            step=0.1,
            unit_of_measurement="s",
            mode=selector.NumberSelectorMode.BOX,
        )
    )
    return vol.Schema(
        {
            vol.Required("travel_time_down", default=round(default_down, 1)): num_selector,
            vol.Required("travel_time_up", default=round(default_up, 1)): num_selector,
        }
    )


class CoverManualCalibrationRepairFlow(RepairsFlow):
    """Guided Start -> Stop manual timing, numeric entry, or reset flow for timed covers."""

    def __init__(self, data: dict[str, Any]) -> None:
        """Initialize the manual cover calibration repair flow."""
        self._entry_id: str = str(data.get("entry_id") or "")
        self._device_id: str = str(data.get("device_id") or "")
        self._entity_id: str = str(data.get("entity_id") or "")
        self._cover_name: str = str(
            data.get("cover_name") or data.get("name") or self._device_id or "Cover"
        )
        self._reason: str = str(data.get("reason") or "cutoff")
        self._limit: str = str(data.get("limit") or "")
        self._seconds: str = str(data.get("seconds") or "")
        self._stored_down: str = str(data.get("down") or "")
        self._stored_up: str = str(data.get("up") or "")
        self._moving: bool = False
        self._run_started_at: float | None = None
        self._measured_down: float | None = None
        self._measured_up: float | None = None
        self._manual_timing_cover: Any | None = None
        self._pre_run_snapshot: dict[str, Any] | None = None

    def _resolve_cover_and_entity(self) -> tuple[Any | None, str | None]:
        """Resolve the live cover entity (if loaded) and its entity_id."""
        cover: Any | None = None
        entry = (
            self.hass.config_entries.async_get_entry(self._entry_id)
            if self._entry_id
            else None
        )
        runtime = get_runtime_data(entry) if entry is not None else None
        if runtime is not None and self._device_id:
            cover = (runtime.entities.get("cover") or {}).get(self._device_id)

        entity_id: str | None = getattr(cover, "entity_id", None) or self._entity_id or None
        ent_reg = er.async_get(self.hass)

        if cover is None and entity_id:
            entity_comp = self.hass.data.get(DATA_INSTANCES, {}).get("cover")
            if entity_comp is not None and hasattr(entity_comp, "get_entity"):
                cover = entity_comp.get_entity(entity_id)

        if (
            cover is None
            and (not entity_id or ent_reg.async_get(entity_id) is None)
            and self._entry_id
            and self._device_id
        ):
            gw_mac = runtime.mac if runtime is not None else ""
            entry_mac_val = (
                str(entry.data.get(CONF_MAC, gw_mac))
                if entry is not None and isinstance(getattr(entry, "data", None), Mapping)
                else gw_mac
            )
            for reg_entry in er.async_entries_for_config_entry(ent_reg, self._entry_id):
                if reg_entry.domain != "cover" or not reg_entry.unique_id:
                    continue
                who, dev_id = parse_unique_id(reg_entry.unique_id, gw_mac, entry_mac_val)
                if (who is None or who == "2") and dev_id == self._device_id:
                    entity_id = reg_entry.entity_id
                    entity_comp = self.hass.data.get(DATA_INSTANCES, {}).get("cover")
                    if entity_comp is not None and hasattr(entity_comp, "get_entity"):
                        cover = entity_comp.get_entity(entity_id)
                    break

        if cover is not None:
            resolved_id = getattr(cover, "entity_id", None) or entity_id
            if resolved_id:
                self._entity_id = resolved_id
            return cover, resolved_id

        if entity_id and (
            ent_reg.async_get(entity_id) is not None
            or self.hass.states.get(entity_id) is not None
        ):
            self._entity_id = entity_id
            return None, entity_id

        return None, None

    def _resolve_cover_name(self, cover: Any | None, entity_id: str | None) -> str:
        """Resolve the user-visible cover name from registry, entity, or state."""
        if entity_id:
            ent_reg = er.async_get(self.hass)
            reg_entry = ent_reg.async_get(entity_id)
            if reg_entry is not None:
                if reg_entry.name:
                    return reg_entry.name
                compat_name = getattr(reg_entry, "compat_name", None)
                if isinstance(compat_name, str) and compat_name:
                    return compat_name
                if reg_entry.device_id:
                    dev = dr.async_get(self.hass).async_get(reg_entry.device_id)
                    if dev is not None and (dev.name_by_user or dev.name):
                        return str(dev.name_by_user or dev.name)
                if reg_entry.original_name:
                    return reg_entry.original_name
        if cover is not None:
            display = getattr(cover, "_display_name", None)
            if isinstance(display, str) and display:
                return display
        if entity_id:
            state = self.hass.states.get(entity_id)
            if state is not None and state.attributes.get("friendly_name"):
                return str(state.attributes["friendly_name"])
        return self._cover_name

    def _build_detail(self) -> str:
        """Build a concise explanation shown in the init menu description."""
        if self._reason == "legacy":
            return (
                f"Stored travel times are {self._stored_down or '?'} s (down) and "
                f"{self._stored_up or '?'} s (up), which matches an actuator run-time limit."
            )
        if self._seconds and self._limit:
            return (
                f"Auto-calibration ended after {self._seconds} s at the actuator's "
                f"{self._limit} s run-time limit rather than the physical end stop."
            )
        if self._limit:
            return (
                f"Auto-calibration hit the actuator's {self._limit} s run-time limit "
                "rather than the physical end stop."
            )
        return "Auto-calibration hit the actuator's run-time limit rather than the physical end stop."

    def _read_last_run(self, cover: Any | None, entity_id: str | None) -> dict[str, Any] | None:
        """Read the current last_run metadata from the cover entity or HA state."""
        if cover is not None:
            last_run = getattr(cover, "_last_run", None)
            if isinstance(last_run, Mapping) and last_run:
                return dict(last_run)
        if entity_id:
            state = self.hass.states.get(entity_id)
            if state is not None:
                sec = state.attributes.get("last_run_seconds")
                direction = state.attributes.get("last_run_direction")
                ended_at = state.attributes.get("last_run_ended_at")
                if sec is not None or direction is not None or ended_at is not None:
                    return {
                        "seconds": sec,
                        "direction": direction,
                        "ended_at": ended_at,
                    }
        return None

    def _default_travel_times(
        self, cover: Any | None, entity_id: str | None
    ) -> tuple[float, float]:
        """Determine sensible pre-fill values for manual travel-time entry."""
        down = float(DEFAULT_TRAVEL_TIME)
        up = float(DEFAULT_TRAVEL_TIME)
        state = self.hass.states.get(entity_id) if entity_id else None

        raw_down = (
            getattr(cover, "_travel_time_down", None)
            if cover is not None
            else (state.attributes.get("travel_time_down") if state is not None else None)
        )
        raw_up = (
            getattr(cover, "_travel_time_up", None)
            if cover is not None
            else (state.attributes.get("travel_time_up") if state is not None else None)
        )
        if (
            isinstance(raw_down, (int, float))
            and not isinstance(raw_down, bool)
            and CALIBRATION_MIN_RUN <= float(raw_down) <= _MANUAL_TRAVEL_MAX_SECONDS
        ):
            down = float(raw_down)
        if (
            isinstance(raw_up, (int, float))
            and not isinstance(raw_up, bool)
            and CALIBRATION_MIN_RUN <= float(raw_up) <= _MANUAL_TRAVEL_MAX_SECONDS
        ):
            up = float(raw_up)

        last_run = self._read_last_run(cover, entity_id)
        if isinstance(last_run, Mapping):
            lr_sec = last_run.get("seconds")
            lr_dir = last_run.get("direction")
            if (
                isinstance(lr_sec, (int, float))
                and not isinstance(lr_sec, bool)
                and CALIBRATION_MIN_RUN <= float(lr_sec) <= _MANUAL_TRAVEL_MAX_SECONDS
            ):
                if lr_dir == "close":
                    down = float(lr_sec)
                elif lr_dir == "open":
                    up = float(lr_sec)

        return round(down, 1), round(up, 1)

    def _fresh_last_run_seconds(
        self, cover: Any | None, entity_id: str | None, direction: str
    ) -> float | None:
        """Return bus-anchored last_run_seconds if it matches this run and direction."""
        last_run = self._read_last_run(cover, entity_id)
        if not isinstance(last_run, Mapping):
            return None
        if last_run.get("direction") != direction:
            return None
        sec = last_run.get("seconds")
        if not isinstance(sec, (int, float)) or isinstance(sec, bool) or float(sec) <= 0:
            return None
        if self._pre_run_snapshot is not None:
            same_ended = last_run.get("ended_at") == self._pre_run_snapshot.get("ended_at")
            if dict(last_run) == self._pre_run_snapshot or (
                last_run.get("ended_at") is not None and same_ended
            ):
                return None
        return round(max(CALIBRATION_MIN_RUN, float(sec)), 1)

    async def _start_motion(
        self, cover: Any | None, entity_id: str | None, direction: str
    ) -> None:
        """Disable early timer settling and start closing or opening the cover."""
        self._pre_run_snapshot = self._read_last_run(cover, entity_id)
        if cover is not None:
            if hasattr(cover, "begin_manual_timing"):
                cover.begin_manual_timing()
                self._manual_timing_cover = cover
            if direction == "close":
                await cover.async_close_cover()
            else:
                await cover.async_open_cover()
        else:
            service = "close_cover" if direction == "close" else "open_cover"
            await self.hass.services.async_call(
                "cover", service, {"entity_id": entity_id}, blocking=True
            )
        self._run_started_at = time.monotonic()
        self._moving = True

    async def _stop_motion_and_measure(
        self, cover: Any | None, entity_id: str | None, direction: str
    ) -> float:
        """Stop the cover, capture bus-anchored or monotonic duration, and restore timer settle."""
        elapsed_fallback = (
            round(max(CALIBRATION_MIN_RUN, time.monotonic() - self._run_started_at), 1)
            if self._run_started_at is not None
            else float(DEFAULT_TRAVEL_TIME)
        )
        try:
            if cover is not None:
                await cover.async_stop_cover()
            else:
                await self.hass.services.async_call(
                    "cover", "stop_cover", {"entity_id": entity_id}, blocking=True
                )

            measured = self._fresh_last_run_seconds(cover, entity_id, direction)
            if (
                measured is None
                and cover is not None
                and getattr(cover, "_run_started_at", None) is not None
            ):
                deadline = time.monotonic() + _STOP_ECHO_WAIT_SECONDS
                while time.monotonic() < deadline:
                    await asyncio.sleep(0.05)
                    measured = self._fresh_last_run_seconds(cover, entity_id, direction)
                    if measured is not None or getattr(cover, "_run_started_at", None) is None:
                        break
            return measured if measured is not None else elapsed_fallback
        finally:
            self._moving = False
            self._run_started_at = None
            target = self._manual_timing_cover or cover
            self._manual_timing_cover = None
            if target is not None and hasattr(target, "end_manual_timing"):
                target.end_manual_timing()

    def _restore_manual_timing(self) -> None:
        """Restore timer settling without stopping the motor."""
        target = self._manual_timing_cover
        self._manual_timing_cover = None
        if target is not None and hasattr(target, "end_manual_timing"):
            target.end_manual_timing()

    async def _async_validate_and_save(
        self,
        step_id: str,
        cover: Any | None,
        entity_id: str | None,
        user_input: dict[str, Any],
        placeholders: dict[str, str],
    ) -> RepairsFlowResult:
        """Validate submitted down/up travel times, persist via service, and clear issues."""
        errors: dict[str, str] = {}
        try:
            down = float(user_input["travel_time_down"])
            up = float(user_input["travel_time_up"])
        except (KeyError, TypeError, ValueError):
            down, up = self._default_travel_times(cover, entity_id)
            errors["base"] = "invalid_travel_time"
        else:
            if (
                not math.isfinite(down)
                or not math.isfinite(up)
                or not (CALIBRATION_MIN_RUN <= down <= _MANUAL_TRAVEL_MAX_SECONDS)
                or not (CALIBRATION_MIN_RUN <= up <= _MANUAL_TRAVEL_MAX_SECONDS)
            ):
                errors["base"] = "invalid_travel_time"

        if not errors:
            try:
                if entity_id:
                    await self.hass.services.async_call(
                        DOMAIN,
                        SERVICE_SET_COVER_TRAVEL_TIME,
                        {
                            "entity_id": entity_id,
                            "travel_time_down": round(down, 1),
                            "travel_time_up": round(up, 1),
                        },
                        blocking=True,
                    )
                elif cover is not None and hasattr(cover, "async_set_travel_time"):
                    await cover.async_set_travel_time(
                        travel_time_down=round(down, 1),
                        travel_time_up=round(up, 1),
                    )
                else:
                    return self.async_abort(reason="cover_missing")
            except ServiceValidationError:
                errors["base"] = "invalid_travel_time"
            except HomeAssistantError as err:
                _LOGGER.warning("Failed saving manual travel times for %s: %s", entity_id, err)
                return self.async_abort(reason="save_failed")
            else:
                if self._entry_id and self._device_id:
                    async_delete_cover_cutoff_issue(self.hass, self._entry_id, self._device_id)
                return self.async_create_entry(data={})

        return self.async_show_form(
            step_id=step_id,
            data_schema=_travel_time_schema(down, up),
            errors=errors,
            description_placeholders=placeholders,
        )

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> RepairsFlowResult:
        """Show the repair menu: guided timing, manual entry, or reset to default."""
        cover, entity_id = self._resolve_cover_and_entity()
        if cover is None and entity_id is None:
            return self.async_abort(reason="cover_missing")
        self._cover_name = self._resolve_cover_name(cover, entity_id)
        return self.async_show_menu(
            step_id="init",
            menu_options=["timed_close_ready", "manual_entry", "reset_default"],
            description_placeholders={
                "name": self._cover_name,
                "detail": self._build_detail(),
            },
        )

    async def async_step_timed_close_ready(
        self, user_input: dict[str, Any] | None = None
    ) -> RepairsFlowResult:
        """Prepare to start a timed closing run."""
        cover, entity_id = self._resolve_cover_and_entity()
        if cover is None and entity_id is None:
            return self.async_abort(reason="cover_missing")
        self._cover_name = self._resolve_cover_name(cover, entity_id)
        if user_input is not None:
            try:
                await self._start_motion(cover, entity_id, "close")
            except HomeAssistantError as err:
                self._restore_manual_timing()
                _LOGGER.warning("Failed starting timed close for %s: %s", entity_id, err)
                return self.async_abort(reason="save_failed")
            return await self.async_step_timed_close_stop()
        return self.async_show_form(
            step_id="timed_close_ready",
            data_schema=vol.Schema({}),
            description_placeholders={"name": self._cover_name},
        )

    async def async_step_timed_close_stop(
        self, user_input: dict[str, Any] | None = None
    ) -> RepairsFlowResult:
        """Stop the cover when it reaches the closed end stop and record down time."""
        cover, entity_id = self._resolve_cover_and_entity()
        if cover is None and entity_id is None:
            self._restore_manual_timing()
            self._moving = False
            return self.async_abort(reason="cover_missing")
        self._cover_name = self._resolve_cover_name(cover, entity_id)
        if user_input is not None:
            try:
                self._measured_down = await self._stop_motion_and_measure(cover, entity_id, "close")
            except HomeAssistantError as err:
                _LOGGER.warning("Failed stopping timed close for %s: %s", entity_id, err)
                return self.async_abort(reason="save_failed")
            return await self.async_step_timed_open_ready()
        return self.async_show_form(
            step_id="timed_close_stop",
            data_schema=vol.Schema({}),
            description_placeholders={"name": self._cover_name},
        )

    async def async_step_timed_open_ready(
        self, user_input: dict[str, Any] | None = None
    ) -> RepairsFlowResult:
        """Optionally reuse closing time or start a timed opening run."""
        cover, entity_id = self._resolve_cover_and_entity()
        if cover is None and entity_id is None:
            return self.async_abort(reason="cover_missing")
        self._cover_name = self._resolve_cover_name(cover, entity_id)
        down_val = (
            self._measured_down if self._measured_down is not None else float(DEFAULT_TRAVEL_TIME)
        )
        if user_input is not None:
            if user_input.get("reuse_down_for_up"):
                self._measured_up = down_val
                return await self.async_step_confirm_save()
            try:
                await self._start_motion(cover, entity_id, "open")
            except HomeAssistantError as err:
                self._restore_manual_timing()
                _LOGGER.warning("Failed starting timed open for %s: %s", entity_id, err)
                return self.async_abort(reason="save_failed")
            return await self.async_step_timed_open_stop()
        return self.async_show_form(
            step_id="timed_open_ready",
            data_schema=vol.Schema(
                {vol.Optional("reuse_down_for_up", default=False): selector.BooleanSelector()}
            ),
            description_placeholders={
                "name": self._cover_name,
                "down": f"{down_val:.1f}",
            },
        )

    async def async_step_timed_open_stop(
        self, user_input: dict[str, Any] | None = None
    ) -> RepairsFlowResult:
        """Stop the cover when it reaches the open end stop and record up time."""
        cover, entity_id = self._resolve_cover_and_entity()
        if cover is None and entity_id is None:
            self._restore_manual_timing()
            self._moving = False
            return self.async_abort(reason="cover_missing")
        self._cover_name = self._resolve_cover_name(cover, entity_id)
        if user_input is not None:
            try:
                self._measured_up = await self._stop_motion_and_measure(cover, entity_id, "open")
            except HomeAssistantError as err:
                _LOGGER.warning("Failed stopping timed open for %s: %s", entity_id, err)
                return self.async_abort(reason="save_failed")
            return await self.async_step_confirm_save()
        return self.async_show_form(
            step_id="timed_open_stop",
            data_schema=vol.Schema({}),
            description_placeholders={"name": self._cover_name},
        )

    async def async_step_manual_entry(
        self, user_input: dict[str, Any] | None = None
    ) -> RepairsFlowResult:
        """Manually enter closing and opening travel times."""
        cover, entity_id = self._resolve_cover_and_entity()
        if cover is None and entity_id is None:
            return self.async_abort(reason="cover_missing")
        self._cover_name = self._resolve_cover_name(cover, entity_id)
        placeholders = {"name": self._cover_name}
        if user_input is not None:
            return await self._async_validate_and_save(
                "manual_entry", cover, entity_id, user_input, placeholders
            )
        default_down, default_up = self._default_travel_times(cover, entity_id)
        return self.async_show_form(
            step_id="manual_entry",
            data_schema=_travel_time_schema(default_down, default_up),
            description_placeholders=placeholders,
        )

    async def async_step_confirm_save(
        self, user_input: dict[str, Any] | None = None
    ) -> RepairsFlowResult:
        """Review and save measured travel times."""
        cover, entity_id = self._resolve_cover_and_entity()
        if cover is None and entity_id is None:
            return self.async_abort(reason="cover_missing")
        self._cover_name = self._resolve_cover_name(cover, entity_id)
        fallback_down, fallback_up = self._default_travel_times(cover, entity_id)
        down_val = self._measured_down if self._measured_down is not None else fallback_down
        up_val = self._measured_up if self._measured_up is not None else fallback_up
        placeholders = {
            "name": self._cover_name,
            "down": f"{down_val:.1f}",
            "up": f"{up_val:.1f}",
        }
        if user_input is not None:
            return await self._async_validate_and_save(
                "confirm_save", cover, entity_id, user_input, placeholders
            )
        return self.async_show_form(
            step_id="confirm_save",
            data_schema=_travel_time_schema(down_val, up_val),
            description_placeholders=placeholders,
        )

    async def async_step_reset_default(
        self, user_input: dict[str, Any] | None = None
    ) -> RepairsFlowResult:
        """Confirm resetting travel times back to default/YAML and clearing repair issues."""
        cover, entity_id = self._resolve_cover_and_entity()
        if cover is None and entity_id is None:
            return self.async_abort(reason="cover_missing")
        self._cover_name = self._resolve_cover_name(cover, entity_id)
        if user_input is not None:
            try:
                if entity_id:
                    await self.hass.services.async_call(
                        DOMAIN,
                        SERVICE_RESET_COVER_TRAVEL_TIME,
                        {"entity_id": entity_id},
                        blocking=True,
                    )
                elif cover is not None and hasattr(cover, "async_reset_travel_time"):
                    await cover.async_reset_travel_time()
                else:
                    return self.async_abort(reason="cover_missing")
            except HomeAssistantError as err:
                _LOGGER.warning("Failed resetting travel times for %s: %s", entity_id, err)
                return self.async_abort(reason="save_failed")
            if self._entry_id and self._device_id:
                async_delete_cover_cutoff_issue(self.hass, self._entry_id, self._device_id)
            return self.async_create_entry(data={})
        return self.async_show_form(
            step_id="reset_default",
            data_schema=vol.Schema({}),
            description_placeholders={"name": self._cover_name},
        )

    @callback
    def async_remove(self) -> None:
        """Stop the motor and restore timer settling if the modal is dismissed mid-run."""
        if not self._moving:
            self._restore_manual_timing()
            return
        self._moving = False
        self._run_started_at = None
        cover = self._manual_timing_cover
        if cover is None and self.hass is not None:
            cover, _ = self._resolve_cover_and_entity()
        self._manual_timing_cover = None
        if cover is not None and hasattr(cover, "end_manual_timing"):
            cover.end_manual_timing()
        if self.hass is None:
            return
        if cover is not None and hasattr(cover, "async_stop_cover"):
            self.hass.async_create_task(cover.async_stop_cover())
        elif self._entity_id:
            self.hass.async_create_task(
                self.hass.services.async_call(
                    "cover",
                    "stop_cover",
                    {"entity_id": self._entity_id},
                    blocking=False,
                )
            )


async def async_create_fix_flow(
    hass: HomeAssistant,
    issue_id: str,
    data: dict[str, Any] | None,
) -> RepairsFlow:
    """Create a repair fix flow."""
    if issue_id.startswith(f"{ISSUE_INCOMPATIBLE_DECODER}_"):
        flow: RepairsFlow = IncompatibleDecoderRepairFlow(data or {})
        flow.hass = hass
        return flow
    if issue_id.startswith(f"{ISSUE_SHARED_BUS_DETECTED}_"):
        shared_flow: RepairsFlow = SharedBusRepairFlow(data or {})
        shared_flow.hass = hass
        return shared_flow
    if issue_id.startswith(f"{ISSUE_COVER_CALIBRATION_CUTOFF}_") or issue_id.startswith(
        f"{ISSUE_COVER_LEGACY_CUTOFF}_"
    ):
        cover_flow: RepairsFlow = CoverManualCalibrationRepairFlow(data or {})
        cover_flow.hass = hass
        return cover_flow
    from homeassistant.components.repairs import ConfirmRepairFlow

    confirm_flow: RepairsFlow = ConfirmRepairFlow()
    confirm_flow.hass = hass
    return confirm_flow


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

