"""Repair issues and diagnostics for the MyHOME integration."""
from __future__ import annotations

import logging
from collections.abc import Iterable
from typing import Any

from homeassistant.components.repairs import RepairsFlow, RepairsFlowResult
from homeassistant.core import HomeAssistant
from homeassistant.helpers import issue_registry as ir
from homeassistant.helpers.issue_registry import (
    IssueSeverity,
    async_create_issue,
    async_delete_issue,
)

from .const import (
    CONF_BUS_TOPOLOGY,
    CONF_DELEGATED_WHOS,
    CONF_GATEWAY_ROLE,
    CONF_PRIMARY_GATEWAY,
    DOMAIN,
    ISSUE_GATEWAY_FAILOVER,
    ISSUE_PRIMARY_GATEWAY_MISSING,
    ISSUE_SHARED_BUS_DETECTED,
    ROLE_PRIMARY,
    ROLE_SECONDARY,
    TOPOLOGY_SHARED,
)
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
        learn_more_url="https://github.com/OpenWebNet-HA/MyHOME/wiki/Configuration#timezone",
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
        learn_more_url="https://openwebnet-ha.github.io/MyHOME/beta/diagnostics/repair-issues/#unconfigured-shared-bus-detected",
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
        learn_more_url="https://openwebnet-ha.github.io/MyHOME/beta/diagnostics/repair-issues/#gateway-failover-active-warm-standby-high-availability",
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
        learn_more_url="https://openwebnet-ha.github.io/MyHOME/beta/diagnostics/repair-issues/#primary-gateway-missing",
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
        learn_more_url="https://openwebnet-ha.github.io/MyHOME/beta/configuration/use_cases/#music-assistant",
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
        learn_more_url="https://openwebnet-ha.github.io/MyHOME/beta/configuration/use_cases/#music-assistant",
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

