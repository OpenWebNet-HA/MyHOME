"""Shared-bus topology of the configured gateways (#453).

Read straight from the config entries, not from loaded handlers, so the answers
do not depend on the order in which the gateways were set up.
"""
from __future__ import annotations

import collections
import logging
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from homeassistant.const import CONF_MAC, CONF_NAME
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import device_registry as dr

from .const import (
    CONF_BUS_TOPOLOGY,
    CONF_DELEGATED_WHOS,
    CONF_FIRMWARE,
    CONF_GATEWAY_ROLE,
    CONF_PRIMARY_GATEWAY,
    DOMAIN,
    LOGGER,
    ROLE_PRIMARY,
    ROLE_SECONDARY,
    ROLE_STANDBY,
    SHARED_BUS_EVIDENCE_COUNT,
    SHARED_BUS_EVIDENCE_WINDOW_S,
    SHARED_BUS_TX_ECHO_S,
    TOPOLOGY_SHARED,
    TOPOLOGY_STANDALONE,
)

if TYPE_CHECKING:
    from OWNd.message import OWNCommand

    from .device_health import DeviceHealth
    from .gateway import MyHOMEGatewayHandler

_LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True)
class RecommendedTopology:
    """Optimal shared-bus configuration inferred from hardware capabilities."""

    primary_mac: str
    secondary_mac: str
    role: str  # ROLE_SECONDARY or ROLE_STANDBY
    delegated_whos: set[int]
    rationale: str
    capability_delta: set[int] = field(default_factory=set)
    audio_coupled: bool = False


def entry_model(entry: Any) -> str | None:
    """The configured model name of a gateway entry."""
    data = getattr(entry, "data", None)
    if isinstance(data, Mapping):
        model = data.get(CONF_NAME)
        if model:
            return str(model)
    options = getattr(entry, "options", None)
    if isinstance(options, Mapping):
        model = options.get(CONF_NAME)
        if model:
            return str(model)
    title = getattr(entry, "title", "") or ""
    if " Gateway" in title:
        return title.split(" Gateway")[0].strip()
    return title or None


def entry_firmware(entry: Any) -> str | None:
    """The configured firmware version of a gateway entry."""
    data = getattr(entry, "data", None)
    if isinstance(data, Mapping):
        fw = data.get(CONF_FIRMWARE)
        if fw:
            return str(fw)
    options = getattr(entry, "options", None)
    if isinstance(options, Mapping):
        fw = options.get(CONF_FIRMWARE)
        if fw:
            return str(fw)
    return None


def gateway_tier(model: str | None) -> int:
    """Return performance tier for a gateway model (1 = Linux fast, 2 = Modern scenario/Touch, 3 = Legacy)."""
    norm = (model or "").strip().upper()
    if any(k in norm for k in ("F454", "MYHOMESERVER1", "F455", "F461")):
        return 1
    if any(k in norm for k in ("MH201", "MH202", "H4890", "AM4890", "LN4890")):
        return 2
    return 3


def gateway_supported_whos(
    model: str | None, firmware: str | None = None
) -> set[int]:
    """Retrieve supported WHO set directly from OWNd profile."""
    whos: set[int] = set()
    norm = (model or "").strip().upper()
    try:
        from OWNd.profiles import get_gateway_profile

        try:
            profile = get_gateway_profile(model or "", firmware)
        except TypeError:  # pragma: no cover - fallback for OWNd without firmware argument
            profile = get_gateway_profile(model or "")
        supports = getattr(profile, "supports_who", None)
        supported = getattr(profile, "supported_who", None)
        if supported:
            for w in supported:
                if not callable(supports) or supports(int(w)):
                    whos.add(int(w))
    except Exception:
        pass

    # Model-specific hardware capability constraints:
    # MyHomeServer1 firmware does not route audio (WHO 16 / WHO 22) or burglar alarm (WHO 5).
    if "MYHOMESERVER1" in norm:
        whos.discard(5)
        whos.discard(16)
        whos.discard(22)

    return {w for w in whos if w in {1, 2, 4, 5, 9, 15, 16, 18, 22, 25}}


def _follower_delegation(pri_whos: set[int], sec_whos: set[int]) -> tuple[str, set[int], bool]:
    """Role, delegated WHOs and audio coupling of a follower next to a primary.

    The follower takes the subsystems the primary lacks; when that includes
    sound (WHO 16 or 22) it takes both, so audio stays on one gateway. With
    nothing to delegate it is a warm standby.
    """
    delta = sec_whos - pri_whos
    audio_coupled = False
    if (22 in delta or 16 in delta) and (16 in sec_whos or 22 in sec_whos):
        if 16 in sec_whos and 16 not in delta:
            delta.add(16)
            audio_coupled = True
        if 22 in sec_whos and 22 not in delta:
            delta.add(22)
            audio_coupled = True
    if not delta:
        return ROLE_STANDBY, set(), False
    return ROLE_SECONDARY, delta, audio_coupled


def recommend_follower(primary: Any, follower: Any) -> tuple[str, set[int]]:
    """Role and delegated WHOs for ``follower`` joining the bus of an existing ``primary``.

    Unlike :func:`infer_shared_bus_topology` the primary is fixed: a gateway
    added next to one that already owns the bus's devices joins as its follower.
    """
    role, delegated, _ = _follower_delegation(
        gateway_supported_whos(entry_model(primary), entry_firmware(primary)),
        gateway_supported_whos(entry_model(follower), entry_firmware(follower)),
    )
    return role, delegated


def infer_shared_bus_topology(entry_a: Any, entry_b: Any) -> RecommendedTopology:
    """Infer optimal primary/secondary role and delegated WHOs for a gateway pair on a shared bus."""
    mac_a = entry_mac(entry_a) or ""
    mac_b = entry_mac(entry_b) or ""
    model_a = entry_model(entry_a)
    model_b = entry_model(entry_b)

    tier_a = gateway_tier(model_a)
    tier_b = gateway_tier(model_b)
    whos_a = gateway_supported_whos(model_a, entry_firmware(entry_a))
    whos_b = gateway_supported_whos(model_b, entry_firmware(entry_b))

    # Determine Primary vs Follower:
    # 1. Higher tier wins (lower tier number)
    # 2. More supported WHOs wins
    # 3. Deterministic fallback by MAC sort
    if tier_a < tier_b:
        pri_mac, sec_mac = mac_a, mac_b
        pri_whos, sec_whos = whos_a, whos_b
        pri_model, sec_model = model_a or "Gateway A", model_b or "Gateway B"
        selection_reason = f"Tier {tier_a} < Tier {tier_b}"
    elif tier_b < tier_a:
        pri_mac, sec_mac = mac_b, mac_a
        pri_whos, sec_whos = whos_b, whos_a
        pri_model, sec_model = model_b or "Gateway B", model_a or "Gateway A"
        selection_reason = f"Tier {tier_b} < Tier {tier_a}"
    elif len(whos_a) > len(whos_b):
        pri_mac, sec_mac = mac_a, mac_b
        pri_whos, sec_whos = whos_a, whos_b
        pri_model, sec_model = model_a or "Gateway A", model_b or "Gateway B"
        selection_reason = f"WHO count {len(whos_a)} > {len(whos_b)}"
    elif len(whos_b) > len(whos_a):
        pri_mac, sec_mac = mac_b, mac_a
        pri_whos, sec_whos = whos_b, whos_a
        pri_model, sec_model = model_b or "Gateway B", model_a or "Gateway A"
        selection_reason = f"WHO count {len(whos_b)} > {len(whos_a)}"
    elif mac_a <= mac_b:
        pri_mac, sec_mac = mac_a, mac_b
        pri_whos, sec_whos = whos_a, whos_b
        pri_model, sec_model = model_a or "Gateway A", model_b or "Gateway B"
        selection_reason = "Equal tier and WHO count; deterministic MAC sort"
    else:
        pri_mac, sec_mac = mac_b, mac_a
        pri_whos, sec_whos = whos_b, whos_a
        pri_model, sec_model = model_b or "Gateway B", model_a or "Gateway A"
        selection_reason = "Equal tier and WHO count; deterministic MAC sort"

    # Capability delta: subsystems supported by the follower that the primary lacks
    raw_delta = sec_whos - pri_whos
    role, delegated, audio_coupled = _follower_delegation(pri_whos, sec_whos)

    if not delegated:
        rationale = (
            f"{pri_model} (Tier {gateway_tier(pri_model)}) selected as Primary ({selection_reason}). "
            f"{sec_model} (Tier {gateway_tier(sec_model)}) capabilities are fully covered by Primary; configured as Warm Standby for failover."
        )
    else:
        subsystems_str = ", ".join(f"WHO {w}" for w in sorted(delegated))
        coupling_note = " (Audio coupled)" if audio_coupled else ""
        rationale = (
            f"{pri_model} (Tier {gateway_tier(pri_model)}) selected as Primary ({selection_reason}). "
            f"{sec_model} (Tier {gateway_tier(sec_model)}) delegated unique subsystems: {subsystems_str}{coupling_note}."
        )

    _LOGGER.debug(
        "Evaluating shared bus topology between %s (Tier %d, WHOs %s) and %s (Tier %d, WHOs %s). "
        "Primary selection: %s (%s). Secondary capability delta: %s (audio coupled: %s)",
        pri_model,
        gateway_tier(pri_model),
        sorted(pri_whos),
        sec_model,
        gateway_tier(sec_model),
        sorted(sec_whos),
        pri_model,
        selection_reason,
        sorted(delegated),
        audio_coupled,
    )
    _LOGGER.info(
        "Inferred shared bus topology: Primary=%s (%s, Tier %d), Follower=%s (%s, Tier %d, role=%s, delegated=%s). %s",
        pri_model,
        pri_mac,
        gateway_tier(pri_model),
        sec_model,
        sec_mac,
        gateway_tier(sec_model),
        role,
        sorted(delegated),
        rationale,
    )

    return RecommendedTopology(
        primary_mac=pri_mac,
        secondary_mac=sec_mac,
        role=role,
        delegated_whos=delegated,
        rationale=rationale,
        capability_delta=raw_delta,
        audio_coupled=audio_coupled,
    )


def _setting(entry: Any, key: str) -> Any:
    """An entry setting, options first, then data."""
    for source in (getattr(entry, "options", None), getattr(entry, "data", None)):
        if isinstance(source, Mapping) and key in source:
            return source[key]
    return None


def entry_mac(entry: Any) -> str | None:
    """The normalised MAC of a gateway entry."""
    data = getattr(entry, "data", None)
    raw = (data.get(CONF_MAC) if isinstance(data, Mapping) else None) or getattr(entry, "unique_id", None)
    return dr.format_mac(str(raw)) if raw else None


def entry_topology(entry: Any) -> str:
    return str(_setting(entry, CONF_BUS_TOPOLOGY) or TOPOLOGY_STANDALONE)


def entry_role(entry: Any) -> str:
    return str(_setting(entry, CONF_GATEWAY_ROLE) or ROLE_PRIMARY)


def entry_is_follower(entry: Any) -> bool:
    """Secondary or standby on a shared bus."""
    return entry_topology(entry) == TOPOLOGY_SHARED and entry_role(entry) in (ROLE_SECONDARY, ROLE_STANDBY)



def entry_primary_mac(entry: Any) -> str | None:
    """The primary a secondary/standby entry points at."""
    if not entry_is_follower(entry):
        return None
    raw = _setting(entry, CONF_PRIMARY_GATEWAY)
    return dr.format_mac(str(raw)) if raw else None


def entry_delegated_whos(entry: Any) -> set[int]:
    """WHOs delegated to a secondary entry (never to a standby)."""
    if entry_topology(entry) != TOPOLOGY_SHARED or entry_role(entry) != ROLE_SECONDARY:
        return set()
    whos: set[int] = set()
    for item in _setting(entry, CONF_DELEGATED_WHOS) or []:
        try:
            whos.add(int(item))
        except (ValueError, TypeError):
            pass
    return whos


def dependents(hass: HomeAssistant, mac: str) -> list[Any]:
    """The secondary/standby entries that point at ``mac`` as their primary."""
    return [e for e in hass.config_entries.async_entries(DOMAIN) if entry_primary_mac(e) == mac]


def delegated_away_whos(hass: HomeAssistant, mac: str) -> set[int]:
    """WHOs a primary leaves to its secondaries."""
    whos: set[int] = set()
    for entry in dependents(hass, mac):
        whos |= entry_delegated_whos(entry)
    return whos


def topology_signature(entry: Any) -> tuple[Any, ...]:
    """What a reload has to pick up when it changes."""
    return (
        entry_topology(entry),
        entry_role(entry),
        entry_primary_mac(entry),
        tuple(sorted(entry_delegated_whos(entry))),
    )


def validate_shared_bus_topology(
    hass: HomeAssistant,
    entry: Any,
    user_input: Mapping[str, Any],
    *,
    model_override: str | None = None,
    target_primary_options: Mapping[str, Any] | None = None,
) -> dict[str, str]:
    """Validate shared-bus topology options before saving or applying repairs.

    Ensures that:
    - Follower roles (secondary/standby) are only configured on shared topology.
    - Follower gateways specify an existing, non-self, non-circular shared primary.
    - At most one warm standby gateway is assigned to a primary.
    - Delegated WHOs are supported by the gateway hardware profile (or model_override).
    - Delegated WHOs do not overlap with other secondaries on the same bus.
    - Gateways with configured dependents cannot be demoted away from shared primary.
    - target_primary_options allows evaluating follower validity against a proposed primary.
    """
    errors: dict[str, str] = {}
    in_topo = user_input.get(CONF_BUS_TOPOLOGY, _setting(entry, CONF_BUS_TOPOLOGY))
    in_role = user_input.get(CONF_GATEWAY_ROLE, _setting(entry, CONF_GATEWAY_ROLE))
    in_pri = user_input.get(CONF_PRIMARY_GATEWAY, _setting(entry, CONF_PRIMARY_GATEWAY))
    my_mac = entry_mac(entry)
    shared = in_topo == TOPOLOGY_SHARED
    follower = shared and in_role in (ROLE_SECONDARY, ROLE_STANDBY)

    if not shared and user_input.get(CONF_GATEWAY_ROLE) in (ROLE_SECONDARY, ROLE_STANDBY):
        errors[CONF_GATEWAY_ROLE] = "secondary_requires_shared_topology"
        return errors

    norm_pri = dr.format_mac(str(in_pri)) if in_pri else None

    if follower:
        target = entry_for_mac(hass, norm_pri) if norm_pri and norm_pri != my_mac else None
        if not norm_pri:
            errors[CONF_PRIMARY_GATEWAY] = "primary_gateway_required"
        elif norm_pri == my_mac:
            errors[CONF_PRIMARY_GATEWAY] = "invalid_primary_gateway"
        elif target is None:
            errors[CONF_PRIMARY_GATEWAY] = "primary_gateway_not_found"
        else:
            is_target_override = bool(target_primary_options and norm_pri == entry_mac(target))
            target_topo = (
                target_primary_options.get(CONF_BUS_TOPOLOGY)
                if is_target_override and target_primary_options
                else entry_topology(target)
            )
            target_role = (
                target_primary_options.get(CONF_GATEWAY_ROLE)
                if is_target_override and target_primary_options
                else entry_role(target)
            )
            target_pri_mac = (
                dr.format_mac(str(target_primary_options.get(CONF_PRIMARY_GATEWAY)))
                if is_target_override and target_primary_options and target_primary_options.get(CONF_PRIMARY_GATEWAY)
                else entry_primary_mac(target)
            )

            if (
                target_topo == TOPOLOGY_SHARED
                and target_role in (ROLE_SECONDARY, ROLE_STANDBY)
                and target_pri_mac == my_mac
            ):
                errors[CONF_PRIMARY_GATEWAY] = "circular_gateway_reference"
            elif target_topo != TOPOLOGY_SHARED or target_role != ROLE_PRIMARY:
                errors[CONF_PRIMARY_GATEWAY] = "primary_gateway_not_shared_primary"

        if norm_pri and in_role == ROLE_STANDBY and CONF_PRIMARY_GATEWAY not in errors:
            for other in dependents(hass, norm_pri):
                if getattr(entry, "entry_id", None) == other.entry_id:
                    continue
                if entry_role(other) == ROLE_STANDBY:
                    errors[CONF_GATEWAY_ROLE] = "multiple_standbys"
                    break

        if in_role == ROLE_SECONDARY and CONF_PRIMARY_GATEWAY not in errors:
            delegated: list[int] = []
            for w in user_input.get(CONF_DELEGATED_WHOS, []):
                try:
                    delegated.append(int(w))
                except (ValueError, TypeError):
                    pass

            model = model_override or entry_model(entry)
            if model:
                supported = gateway_supported_whos(model, entry_firmware(entry))
                for w in delegated:
                    if w not in supported:
                        errors[CONF_DELEGATED_WHOS] = "who_not_supported_by_gateway"
                        break

            if norm_pri and CONF_DELEGATED_WHOS not in errors:
                for other in dependents(hass, norm_pri):
                    if getattr(entry, "entry_id", None) == other.entry_id:
                        continue
                    if entry_role(other) == ROLE_SECONDARY:
                        if set(delegated) & entry_delegated_whos(other):
                            errors[CONF_DELEGATED_WHOS] = "overlapping_delegated_whos"
                            break

    if not (shared and in_role == ROLE_PRIMARY) and my_mac and dependents(hass, my_mac):
        errors[CONF_GATEWAY_ROLE] = "gateway_has_dependents"

    return errors


@callback
def async_check_primary_links(hass: HomeAssistant, *, removed: str | None = None) -> None:
    """Raise a repair issue for each secondary/standby left without a shared primary.

    Such a gateway keeps suppressing discovery for a primary that is gone, so the
    user has to reconfigure it. ``removed`` is an entry being deleted right now.
    """
    from .repairs import async_create_primary_missing_issue, async_delete_primary_missing_issue

    for entry in hass.config_entries.async_entries(DOMAIN):
        if entry.entry_id == removed:
            continue
        primary = entry_primary_mac(entry)
        target = entry_for_mac(hass, primary, exclude=removed) if primary else None
        if not entry_is_follower(entry) or (
            target is not None and entry_topology(target) == TOPOLOGY_SHARED and entry_role(target) == ROLE_PRIMARY
        ):
            async_delete_primary_missing_issue(hass, entry.entry_id)
        else:
            async_create_primary_missing_issue(hass, entry.entry_id, entry.title, primary or "-")


def entry_for_mac(hass: HomeAssistant, mac: str, *, exclude: str | None = None) -> Any | None:
    """The config entry of the gateway with ``mac`` (ignoring entry id ``exclude``)."""
    for entry in hass.config_entries.async_entries(DOMAIN):
        if entry.entry_id != exclude and entry_mac(entry) == mac:
            return entry
    return None


def peer_unique_id(unique_id: str, own_mac: str, peer_mac: str) -> str | None:
    """``unique_id`` rewritten onto ``peer_mac`` (entity unique ids start with the gateway MAC)."""
    if unique_id.startswith(own_mac):
        return f"{peer_mac}{unique_id[len(own_mac):]}"
    clean_own = own_mac.replace(":", "").lower()
    if unique_id.lower().startswith(clean_own):
        return f"{peer_mac.replace(':', '').lower()}{unique_id[len(clean_own):]}"
    return None


class GatewayTopologyMixin:
    """Shared-bus topology, failover, and subsystem delegation methods for MyHOMEGatewayHandler (#732)."""

    hass: HomeAssistant
    config_entry: Any
    is_connected: bool
    _available: bool
    _failover_active: bool
    _setup_at: float
    device_health: DeviceHealth
    mac: str
    name: str
    log_id: str

    def _profile_supports_who(self, who: int) -> bool:
        raise NotImplementedError

    @property
    def available(self) -> bool:
        """Return the grace-filtered gateway availability."""
        if self._available:
            return True
        standby = self._get_standby_gateway()
        if standby is not None and standby.available:
            return True
        return False

    def is_who_available(self, who: str | int) -> bool:
        """Return True if this gateway (or its failover) can currently handle the given WHO."""
        if self._available:
            return True
        standby = self._get_standby_gateway()
        if standby is not None and standby.available:
            return standby._profile_supports_who(int(who))
        return False

    @property
    def availability_signal(self) -> str:
        """Return the dispatcher signal for availability changes."""
        return f"{DOMAIN}_{self.mac}_availability"

    @property
    def bus_topology(self) -> str:
        """Bus topology for this gateway: 'standalone' or 'shared'."""
        return entry_topology(self.config_entry)

    @property
    def gateway_role(self) -> str:
        """Role of this gateway: 'primary', 'secondary' or 'standby'."""
        return entry_role(self.config_entry)

    @property
    def is_follower(self) -> bool:
        """Return True if this gateway is a secondary or standby gateway on a shared bus."""
        return entry_is_follower(self.config_entry)

    @property
    def is_standby(self) -> bool:
        """Return True if this gateway is configured as a warm standby failover."""
        return self.bus_topology == TOPOLOGY_SHARED and self.gateway_role == ROLE_STANDBY

    @property
    def is_secondary(self) -> bool:
        """Return True if this gateway is configured as a secondary gateway."""
        return self.bus_topology == TOPOLOGY_SHARED and self.gateway_role == ROLE_SECONDARY

    @property
    def is_primary(self) -> bool:
        """Return True if this gateway acts as primary (or standalone) on its bus."""
        return not self.is_follower

    @property
    def failover_active(self) -> bool:
        """Return True if failover to standby is currently active."""
        return self._failover_active

    @property
    def primary_gateway_mac(self) -> str | None:
        """The primary gateway MAC if this gateway is secondary."""
        return entry_primary_mac(self.config_entry)

    @property
    def delegated_whos(self) -> set[int]:
        """Subsystems (WHOs) this secondary gateway discovers for the bus."""
        return entry_delegated_whos(self.config_entry)

    @property
    def delegated_away_whos(self) -> set[int]:
        """Subsystems this primary leaves to its secondaries: no sweep, no new entities."""
        if self.is_follower or not getattr(self, "hass", None):
            return set()
        return delegated_away_whos(self.hass, self.mac)

    @property
    def bus_group(self) -> str:
        """The configured bus this gateway belongs to (the primary's MAC)."""
        return self.primary_gateway_mac or self.mac

    def _get_standby_gateway(self) -> MyHOMEGatewayHandler | None:
        """Find the standby gateway configured for this primary gateway."""
        if not getattr(self, "hass", None):
            return None
        for entry in self.hass.config_entries.async_entries(DOMAIN):
            runtime_data = getattr(entry, "runtime_data", None)
            gw: MyHOMEGatewayHandler | None = getattr(runtime_data, "gateway", None)
            if (
                gw is not None
                and gw.bus_topology == TOPOLOGY_SHARED
                and gw.gateway_role == ROLE_STANDBY
                and gw.primary_gateway_mac == self.mac
            ):
                return gw
        return None

    def _get_secondary_for_who(self, who: int) -> MyHOMEGatewayHandler | None:
        """Find the connected secondary gateway handling a delegated WHO subsystem."""
        if not getattr(self, "hass", None):
            return None
        for entry in self.hass.config_entries.async_entries(DOMAIN):
            runtime_data = getattr(entry, "runtime_data", None)
            gw: MyHOMEGatewayHandler | None = getattr(runtime_data, "gateway", None)
            if (
                gw is not None
                and gw.bus_topology == TOPOLOGY_SHARED
                and gw.gateway_role == ROLE_SECONDARY
                and gw.primary_gateway_mac == self.mac
                and who in gw.delegated_whos
            ):
                return gw
        return None

    def _get_primary_gateway(self) -> MyHOMEGatewayHandler | None:
        """Find the configured primary gateway for this secondary/standby gateway."""
        if not getattr(self, "hass", None) or not self.primary_gateway_mac:
            return None
        for entry in self.hass.config_entries.async_entries(DOMAIN):
            runtime_data = getattr(entry, "runtime_data", None)
            gw: MyHOMEGatewayHandler | None = getattr(runtime_data, "gateway", None)
            if gw is not None and gw.mac == self.primary_gateway_mac:
                return gw
        return None

    def _record_failover_active(self, standby: MyHOMEGatewayHandler) -> None:
        """Record that failover to standby is currently active and raise repair issue."""
        if self._failover_active:
            return
        self._failover_active = True
        LOGGER.warning(
            "%s Primary gateway offline; warm standby %s now carries its traffic.",
            self.log_id,
            standby.log_id,
        )
        from .repairs import async_create_failover_issue

        async_create_failover_issue(
            self.hass,
            self.mac,
            standby.mac,
            self.name,
            standby.name,
        )

    def _clear_failover(self) -> None:
        """Forget an active failover and resolve its repair issue."""
        if not self._failover_active:
            return
        self._failover_active = False
        from .repairs import async_delete_failover_issue

        async_delete_failover_issue(self.hass, self.mac)

    def health_owner(self) -> DeviceHealth | None:
        """The tracker that files device faults seen by this gateway."""
        if self.is_standby:
            primary_gw = self._get_primary_gateway()
            if (
                primary_gw is None
                or primary_gw.is_connected
                or primary_gw._get_standby_gateway() is not self
            ):
                return None
            return primary_gw.device_health
        return self.device_health

    @property
    def ignored_addresses(self) -> Any:
        """Addresses configured to be ignored on this gateway."""
        from .ignored import IgnoredAddresses

        return IgnoredAddresses.from_config_entry(self.config_entry)

    def _bridge_to_primary(self, message: Any) -> None:
        """Hand a bus frame to the offline primary's entities (warm standby only)."""
        from . import gateway as _gw

        if not self.is_standby or getattr(message, "who", None) in (13, 1013):
            return
        primary_gw = self._get_primary_gateway()
        if (
            primary_gw is None
            or primary_gw.is_connected
            or primary_gw._get_standby_gateway() is not self
        ):
            return
        primary_gw._evaluate_failover()
        _gw.async_dispatcher_send(self.hass, f"myhome_message_{primary_gw.mac}", message)

    def _outage_confirmed(self) -> bool:
        """Down past the reconnect grace, or not up yet a grace period after setup."""
        from . import gateway as _gw

        return (
            not self.is_connected
            and not self._available
            and _gw.time.monotonic() - self._setup_at >= _gw.AVAILABILITY_GRACE
        )

    @callback
    def _evaluate_failover(self) -> None:
        """Raise or clear the failover issue from the primary's and standby's state."""
        standby = self._get_standby_gateway()
        if standby is not None and standby._available and self._outage_confirmed():
            self._record_failover_active(standby)
        else:
            self._clear_failover()

    @callback
    def _notify_availability(self) -> None:
        """Notify all entities bound to this gateway, and any primary backed by this standby."""
        from . import gateway as _gw

        _gw.async_dispatcher_send(self.hass, self.availability_signal)
        if self.is_standby:
            primary = self._get_primary_gateway()
            if primary is not None and not primary._available:
                primary._evaluate_failover()
                _gw.async_dispatcher_send(self.hass, primary.availability_signal)

    def _record_tx(self, written_at: float, message: Any) -> None:
        """Remember a written frame for shared-bus detection."""
        if not getattr(self, "hass", None):
            return
        domain_data = self.hass.data.setdefault(DOMAIN, {})
        recent_tx = domain_data.setdefault("_recent_tx", collections.deque(maxlen=50))
        recent_tx.append((written_at, self.mac, self.bus_group, str(message).strip()))

    def _correlate_shared_bus_traffic(self, message: Any) -> None:
        """Correlate bus traffic with other gateways to detect unconfigured shared buses."""
        from . import gateway as _gw

        if not getattr(self, "hass", None) or getattr(message, "who", None) in (13, 1013):
            return
        if (
            getattr(message, "is_general", False)
            or getattr(message, "is_area", False)
            or getattr(message, "is_group", False)
        ):
            return
        where = getattr(message, "where", None)
        if where is not None:
            where_str = str(where).strip()
            if where_str in ("0", "#0") or where_str.startswith("#"):
                return

        domain_data = self.hass.data.setdefault(DOMAIN, {})
        now = _gw.time.monotonic()
        raw_msg = str(message).strip()
        if raw_msg.endswith("*0##") or raw_msg.endswith("*#0##") or "*0*0##" in raw_msg:
            return

        group = self.bus_group
        recent_tx = domain_data.get("_recent_tx") or ()
        if any(
            tx_mac == self.mac and tx_frame == raw_msg and now - tx_time <= SHARED_BUS_TX_ECHO_S
            for tx_time, tx_mac, _tx_group, tx_frame in recent_tx
        ):
            return

        for tx_time, tx_mac, tx_group, tx_frame in recent_tx:
            if tx_group != group and tx_frame == raw_msg and now - tx_time <= SHARED_BUS_TX_ECHO_S:
                self._record_shared_bus_evidence(tx_mac, now)
                return

    def _record_shared_bus_evidence(self, other_mac: str, now: float) -> None:
        """Count one correlated TX->RX echo; raise the repair issue on three echoes in the window."""
        my_mac = dr.format_mac(str(self.mac))
        other_mac = dr.format_mac(str(other_mac))
        if not other_mac or other_mac == my_mac:
            return
        domain_data = self.hass.data.setdefault(DOMAIN, {})
        evidence_map = domain_data.setdefault("_shared_bus_evidence", {})
        pair_key = tuple(sorted([my_mac, other_mac]))
        seen = evidence_map.setdefault(
            pair_key, collections.deque(maxlen=SHARED_BUS_EVIDENCE_COUNT)
        )
        seen.append(now)
        if (
            len(seen) == SHARED_BUS_EVIDENCE_COUNT
            and seen[-1] - seen[0] <= SHARED_BUS_EVIDENCE_WINDOW_S
        ):
            seen.clear()
            from .repairs import async_create_shared_bus_issue

            async_create_shared_bus_issue(self.hass, pair_key[0], pair_key[1])

    def _delegated_target(self, message: OWNCommand) -> MyHOMEGatewayHandler | None:
        """The connected secondary gateway that owns the delegated WHO subsystem."""
        msg_who = getattr(message, "who", getattr(message, "_who", None))
        if msg_who in (13, 1013) or msg_who is None:
            return None
        if msg_who not in self.delegated_away_whos:
            return None
        sec = self._get_secondary_for_who(msg_who)
        if sec is None or not sec.is_connected:
            return None
        LOGGER.debug(
            "%s Subsystem WHO=%s is delegated; sending `%s` through secondary gateway %s.",
            self.log_id,
            msg_who,
            message,
            sec.log_id,
        )
        return sec

    def _failover_target(self, message: OWNCommand) -> MyHOMEGatewayHandler | None:
        """The connected warm standby to send through while this primary is disconnected."""
        msg_who = getattr(message, "who", getattr(message, "_who", None))
        if msg_who in (13, 1013) or self.is_connected:
            return None
        standby = self._get_standby_gateway()
        if standby is None or not standby.is_connected:
            return None
        if msg_who is not None and not standby._profile_supports_who(int(msg_who)):
            return None
        LOGGER.debug(
            "%s Primary gateway is disconnected; sending `%s` through standby gateway %s.",
            self.log_id,
            message,
            standby.log_id,
        )
        self._evaluate_failover()
        return standby

