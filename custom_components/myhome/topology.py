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
    for source in (getattr(entry, "data", None), getattr(entry, "options", None)):
        if isinstance(source, Mapping) and source.get(CONF_NAME):
            return str(source[CONF_NAME])
    title = getattr(entry, "title", "") or ""
    return title.split(" Gateway")[0].strip() if " Gateway" in title else (title or None)


def entry_firmware(entry: Any) -> str | None:
    """The configured firmware version of a gateway entry."""
    for source in (getattr(entry, "data", None), getattr(entry, "options", None)):
        if isinstance(source, Mapping) and source.get(CONF_FIRMWARE):
            return str(source[CONF_FIRMWARE])
    return None


def gateway_tier(model: str | None) -> int:
    """Return performance tier for a gateway model (1 = Linux fast, 2 = Modern scenario/Touch, 3 = Legacy)."""
    norm = (model or "").strip().upper()
    if any(k in norm for k in ("F454", "MYHOMESERVER1", "F455", "F461")):
        return 1
    if any(
        k in norm
        for k in (
            "MH201",
            "MH202",
            "H4890",
            "AM4890",
            "LN4890",
            "067292",
            "67292",
            "573958",
            "078479",
            "78479",
        )
    ):
        return 2
    return 3


#: Door entry (WHO 6) and video door entry / intercom (WHO 8): the subsystems whose support
#: depends on the gateway's firmware routing them to ``bt_vct``.
DOOR_ENTRY_WHOS: tuple[int, ...] = (6, 8)
#: A/V gateways assumed to route both WHO 6 and WHO 8. Unverified: F453AV ships ``bt_vct``
#: (oracle target ``030014``) but its ``stack_open.xml`` routing has not been inspected, and
#: F452 / F452V are listed by product line.
DOOR_ENTRY_AV_MODELS: frozenset[str] = frozenset({"F452", "F452V", "F453AV"})
#: A/V gateways that route WHO 8 but not WHO 6: the F454 ``020051`` ``stack_open.xml`` gives
#: ``bt_vct`` ``<chi>8</chi>`` and no program ``6``; the F459 ships the same image
#: (``art.xml.F459``). They hear doorbells but NACK ``*6*10*WHERE##`` unlocks.
DOOR_ENTRY_WHO8_ONLY_MODELS: frozenset[str] = frozenset({"F454", "F459"})
#: ``MH`` scenario programmers that route WHO 6 to ``bt_vct`` (``<chi>6.7</chi>`` on MH202
#: ``010024``, WHO 6/7 on MH200 ``020102``) and pass WHO 6 / WHO 8 bus frames to the event
#: session when the door-entry riser reaches their bus, e.g. through an ``F422`` (inferred:
#: neither capture documents one). Captures: ``mh202_sound_intercom_ducking`` (MH202 1.0: WHO 8
#: call, WHO 6 lock release) and ``issue_667`` (MH200 2.1.0: WHO 8 call, ``*6*9##``). MH200N is
#: listed because its OWNd profile advertises WHO 6/8; there is no capture or firmware data.
DOOR_ENTRY_MH_BRIDGE_AV_MODELS: frozenset[str] = frozenset({"MH200", "MH200N", "MH202"})
#: Gateways that may route WHO 6 without a captured WHO 8 relay: F453 (``bt_vct`` WHO 6/7),
#: H4684 (ships ``bt_vct``, routing not inspected). H4890 and MH201 are unmeasured and kept
#: permissive so their locks raise no false warning.
DOOR_ENTRY_WHO6_ONLY_MODELS: frozenset[str] = frozenset({"F453", "H4684", "H4890", "MH201"})
#: Gateways that route neither WHO 6 nor WHO 8: no ``bt_vct`` in the MyHomeServer1 ``028206``
#: or F450 ``020010`` ``stack_open.xml`` nor in the F455 / F460 oracle targets, and the F461
#: NACKs both in ~55 ms (``issue_667``).
DOOR_ENTRY_UNSUPPORTED_MODELS: frozenset[str] = frozenset(
    {"F450", "F455", "F460", "F461", "MYHOMESERVER1"}
)
#: Subsystems eligible for shared-bus delegation.
DELEGATABLE_WHOS: tuple[int, ...] = (1, 2, 4, 5, 6, 8, 9, 15, 16, 18, 22, 25)


def _profile_and_model(model: str | None, firmware: str | None = None) -> tuple[Any, str]:
    """Return ``(profile, canonical_model_name)`` for ``model``."""
    import re

    import OWNd.profiles as _ownd_profiles

    from .gateway import _ZONE_REQUEST_MODELS, _alnum, _profile_for_model, _resolved_model

    raw = model or ""
    profile = _profile_for_model(raw, firmware, lookup=_ownd_profiles.get_gateway_profile)
    if isinstance(profile, _ownd_profiles.GenericGatewayProfile) and raw:
        for word in re.findall(r"[A-Za-z0-9]+", raw):
            resolved = _resolved_model(word)
            if resolved is not None:
                profile = _profile_for_model(
                    resolved, firmware, lookup=_ownd_profiles.get_gateway_profile
                )
                break
    model_name = str(getattr(profile, "model_name", "") or "").upper()
    if isinstance(profile, _ownd_profiles.GenericGatewayProfile):
        normalized = _alnum(raw)
        for candidate in _ZONE_REQUEST_MODELS:
            if candidate in normalized:
                model_name = candidate
                break
    return profile, model_name


def gateway_supported_whos(model: str | None, firmware: str | None = None) -> set[int]:
    """Retrieve intrinsic supported WHO set from OWNd profile for topology inference."""
    whos: set[int] = set()
    norm = (model or "").strip().upper()
    model_name = ""
    try:
        profile, model_name = _profile_and_model(model, firmware)
        supports = getattr(profile, "supports_who", None)
        for w in getattr(profile, "supported_who", None) or ():
            if not callable(supports) or supports(int(w)):
                whos.add(int(w))
        if model_name in DOOR_ENTRY_AV_MODELS:
            whos |= {6, 8}
        elif model_name in DOOR_ENTRY_WHO8_ONLY_MODELS:
            whos = (whos - {6}) | {8}
        elif model_name in DOOR_ENTRY_MH_BRIDGE_AV_MODELS or model_name in DOOR_ENTRY_WHO6_ONLY_MODELS:
            whos -= {6, 8}
    except Exception:
        model_name = ""
    # Model-specific hardware capability constraints:
    # MyHomeServer1 firmware omits audio (WHO 16/22), burglar alarm (WHO 5), and door entry (WHO 6/8).
    if model_name == "MYHOMESERVER1" or "MYHOMESERVER1" in norm:
        whos -= {5, 6, 8, 16, 22}
    if model_name in DOOR_ENTRY_UNSUPPORTED_MODELS or any(k in norm for k in DOOR_ENTRY_UNSUPPORTED_MODELS):
        whos -= {6, 8}
    return {w for w in whos if w in DELEGATABLE_WHOS}


def gateway_delegatable_whos(model: str | None, firmware: str | None = None) -> set[int]:
    """Subsystems ``model`` can accept when explicitly delegated on a shared bus.

    Includes :func:`gateway_supported_whos` plus the door-entry WHOs that ``MH``
    gateways route to ``bt_vct`` when the door-entry riser reaches their bus, and
    WHO 6 / 8 for un-identified ``Generic`` gateways (their other subsystems still
    come from the conservative profile).
    """
    whos = set(gateway_supported_whos(model, firmware))
    norm = (model or "").strip().upper()
    try:
        import OWNd.profiles as _ownd_profiles

        profile, model_name = _profile_and_model(model, firmware)
        is_generic = isinstance(profile, _ownd_profiles.GenericGatewayProfile)
    except Exception:
        model_name, is_generic = "", False
    if model_name in DOOR_ENTRY_MH_BRIDGE_AV_MODELS:
        whos |= {6, 8}
    elif model_name in DOOR_ENTRY_WHO6_ONLY_MODELS:
        whos.add(6)
    elif (
        is_generic
        and "3578" not in norm
        and model_name not in DOOR_ENTRY_UNSUPPORTED_MODELS | DOOR_ENTRY_WHO8_ONLY_MODELS
        and not any(k in norm for k in DOOR_ENTRY_UNSUPPORTED_MODELS)
    ):
        whos |= set(DOOR_ENTRY_WHOS)
    return whos


def peer_handles_who(peer: Any, who: int) -> bool:
    """Whether a delegated or standby peer can take ``who``.

    Door-entry WHOs need the peer's firmware to route them (an F454 takes WHO 8
    but NACKs WHO 6); every other subsystem goes where the topology says.
    """
    if who not in DOOR_ENTRY_WHOS:
        return True
    fn = getattr(peer, "_profile_supports_who", None) or getattr(peer, "profile_supports_who", None)
    return bool(fn(who)) if callable(fn) else True


def _follower_delegation(pri_whos: set[int], sec_whos: set[int]) -> tuple[str, set[int], bool]:
    """Role, delegated WHOs and audio coupling of a follower next to a primary.

    The follower takes the subsystems the primary lacks; when that includes
    sound (WHO 16 or 22) it takes both, so audio stays on one gateway. With
    nothing to delegate it is a warm standby.
    """
    delta = sec_whos - pri_whos
    audio_coupled = False
    if (22 in delta or 16 in delta) and (16 in sec_whos or 22 in sec_whos):
        for audio_who in (16, 22):
            if audio_who in sec_whos and audio_who not in delta:
                delta.add(audio_who)
                audio_coupled = True
    return (ROLE_STANDBY, set(), False) if not delta else (ROLE_SECONDARY, delta, audio_coupled)


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
    mac_a, mac_b = entry_mac(entry_a) or "", entry_mac(entry_b) or ""
    model_a, model_b = entry_model(entry_a), entry_model(entry_b)
    tier_a, tier_b = gateway_tier(model_a), gateway_tier(model_b)
    whos_a = gateway_supported_whos(model_a, entry_firmware(entry_a))
    whos_b = gateway_supported_whos(model_b, entry_firmware(entry_b))

    # Determine Primary vs Follower:
    # 1. Higher tier wins (lower tier number)
    # 2. More supported WHOs wins
    # 3. Deterministic fallback by MAC sort
    if tier_a < tier_b:
        pri_mac, sec_mac, pri_whos, sec_whos = mac_a, mac_b, whos_a, whos_b
        pri_model, sec_model = model_a or "Gateway A", model_b or "Gateway B"
        selection_reason = f"Tier {tier_a} < Tier {tier_b}"
    elif tier_b < tier_a:
        pri_mac, sec_mac, pri_whos, sec_whos = mac_b, mac_a, whos_b, whos_a
        pri_model, sec_model = model_b or "Gateway B", model_a or "Gateway A"
        selection_reason = f"Tier {tier_b} < Tier {tier_a}"
    elif len(whos_a) > len(whos_b):
        pri_mac, sec_mac, pri_whos, sec_whos = mac_a, mac_b, whos_a, whos_b
        pri_model, sec_model = model_a or "Gateway A", model_b or "Gateway B"
        selection_reason = f"WHO count {len(whos_a)} > {len(whos_b)}"
    elif len(whos_b) > len(whos_a):
        pri_mac, sec_mac, pri_whos, sec_whos = mac_b, mac_a, whos_b, whos_a
        pri_model, sec_model = model_b or "Gateway B", model_a or "Gateway A"
        selection_reason = f"WHO count {len(whos_b)} > {len(whos_a)}"
    elif mac_a <= mac_b:
        pri_mac, sec_mac, pri_whos, sec_whos = mac_a, mac_b, whos_a, whos_b
        pri_model, sec_model = model_a or "Gateway A", model_b or "Gateway B"
        selection_reason = "Equal tier and WHO count; deterministic MAC sort"
    else:
        pri_mac, sec_mac, pri_whos, sec_whos = mac_b, mac_a, whos_b, whos_a
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
        pri_model, gateway_tier(pri_model), sorted(pri_whos),
        sec_model, gateway_tier(sec_model), sorted(sec_whos),
        pri_model, selection_reason, sorted(delegated), audio_coupled,
    )
    _LOGGER.info(
        "Inferred shared bus topology: Primary=%s (%s, Tier %d), Follower=%s (%s, Tier %d, role=%s, delegated=%s). %s",
        pri_model, pri_mac, gateway_tier(pri_model),
        sec_model, sec_mac, gateway_tier(sec_model), role, sorted(delegated), rationale,
    )
    return RecommendedTopology(
        primary_mac=pri_mac, secondary_mac=sec_mac, role=role, delegated_whos=delegated,
        rationale=rationale, capability_delta=raw_delta, audio_coupled=audio_coupled,
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
        entry_topology(entry), entry_role(entry),
        entry_primary_mac(entry), tuple(sorted(entry_delegated_whos(entry))),
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
            if target_topo == TOPOLOGY_SHARED and target_role in (ROLE_SECONDARY, ROLE_STANDBY) and target_pri_mac == my_mac:
                errors[CONF_PRIMARY_GATEWAY] = "circular_gateway_reference"
            elif target_topo != TOPOLOGY_SHARED or target_role != ROLE_PRIMARY:
                errors[CONF_PRIMARY_GATEWAY] = "primary_gateway_not_shared_primary"

        if norm_pri and in_role == ROLE_STANDBY and CONF_PRIMARY_GATEWAY not in errors:
            for other in dependents(hass, norm_pri):
                if getattr(entry, "entry_id", None) != other.entry_id and entry_role(other) == ROLE_STANDBY:
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
                supported = gateway_delegatable_whos(model, entry_firmware(entry))
                for w in delegated:
                    if w not in supported:
                        errors[CONF_DELEGATED_WHOS] = "who_not_supported_by_gateway"
                        break
            if norm_pri and CONF_DELEGATED_WHOS not in errors:
                for other in dependents(hass, norm_pri):
                    if (
                        getattr(entry, "entry_id", None) != other.entry_id
                        and entry_role(other) == ROLE_SECONDARY
                        and (set(delegated) & entry_delegated_whos(other))
                    ):
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

    if TYPE_CHECKING:
        @property
        def mac(self) -> str: ...
        @property
        def name(self) -> str: ...
        @property
        def log_id(self) -> str: ...
        def _profile_supports_who(self, who: int) -> bool: ...
        def _natively_hears_who(self, who: int) -> bool: ...

    @property
    def available(self) -> bool:
        """Return the grace-filtered gateway availability."""
        if self._available:
            return True
        standby = self._get_standby_gateway()
        return bool(standby is not None and standby.available)

    def is_who_available(self, who: str | int) -> bool:
        """Return True if this gateway (or its delegated/standby peer) can currently handle the given WHO."""
        who_int = int(who)
        if who_int in DOOR_ENTRY_WHOS and who_int in self.delegated_away_whos:
            sec = self._get_secondary_for_who(who_int)
            if sec is not None and sec.available and peer_handles_who(sec, who_int):
                return True
            if self._available:
                return self._profile_supports_who(who_int)
        elif self._available:
            return True
        standby = self._get_standby_gateway()
        if standby is not None and standby.available:
            return standby._profile_supports_who(who_int)
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
            gw: MyHOMEGatewayHandler | None = getattr(getattr(entry, "runtime_data", None), "gateway", None)
            if gw is not None and gw.bus_topology == TOPOLOGY_SHARED and gw.gateway_role == ROLE_STANDBY and gw.primary_gateway_mac == self.mac:
                return gw
        return None

    def _get_secondary_for_who(self, who: int) -> MyHOMEGatewayHandler | None:
        """Find the connected secondary gateway handling a delegated WHO subsystem."""
        if not getattr(self, "hass", None):
            return None
        for entry in self.hass.config_entries.async_entries(DOMAIN):
            gw: MyHOMEGatewayHandler | None = getattr(getattr(entry, "runtime_data", None), "gateway", None)
            if gw is not None and gw.bus_topology == TOPOLOGY_SHARED and gw.gateway_role == ROLE_SECONDARY and gw.primary_gateway_mac == self.mac and who in gw.delegated_whos:
                return gw
        return None

    def _get_primary_gateway(self) -> MyHOMEGatewayHandler | None:
        """Find the configured primary gateway for this secondary/standby gateway."""
        if not getattr(self, "hass", None) or not self.primary_gateway_mac:
            return None
        for entry in self.hass.config_entries.async_entries(DOMAIN):
            gw: MyHOMEGatewayHandler | None = getattr(getattr(entry, "runtime_data", None), "gateway", None)
            if gw is not None and gw.mac == self.primary_gateway_mac:
                return gw
        return None

    def _record_failover_active(self, standby: MyHOMEGatewayHandler) -> None:
        """Record that failover to standby is currently active and raise repair issue."""
        if self._failover_active:
            return
        self._failover_active = True
        LOGGER.warning("%s Primary gateway offline; warm standby %s now carries its traffic.", self.log_id, standby.log_id)
        from .repairs import async_create_failover_issue

        async_create_failover_issue(self.hass, self.mac, standby.mac, self.name, standby.name)

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
            if primary_gw is None or primary_gw.is_connected or primary_gw._get_standby_gateway() is not self:
                return None
            return primary_gw.device_health
        return self.device_health

    @property
    def ignored_addresses(self) -> Any:
        """Addresses configured to be ignored on this gateway."""
        from .ignored import IgnoredAddresses

        return IgnoredAddresses.from_config_entry(self.config_entry)

    def _bridge_to_primary(self, message: Any) -> None:
        """Hand a bus frame to the primary's entities on warm-standby failover, or door-entry frames the primary does not hear."""
        from . import gateway as _gw

        raw_who = getattr(message, "who", getattr(message, "_who", None))
        who_int = int(raw_who) if raw_who is not None and str(raw_who).isdigit() else None
        if who_int in (13, 1013):
            return
        if self.is_standby:
            primary_gw = self._get_primary_gateway()
            if primary_gw is None or primary_gw.is_connected or primary_gw._get_standby_gateway() is not self:
                return
            primary_gw._evaluate_failover()
            if who_int in DOOR_ENTRY_WHOS and who_int in primary_gw.delegated_away_whos:
                # A secondary that routes it bridges this door-entry frame itself.
                sec = primary_gw._get_secondary_for_who(who_int)
                if sec is not None and sec.is_connected and peer_handles_who(sec, who_int):
                    return
            _gw.async_dispatcher_send(self.hass, f"myhome_message_{primary_gw.mac}", message)
            return
        if (
            self.is_secondary
            and who_int in DOOR_ENTRY_WHOS
            and who_int in self.delegated_whos
            and peer_handles_who(self, who_int)
        ):
            primary_gw = self._get_primary_gateway()
            if primary_gw is not None and not (
                primary_gw.is_connected and primary_gw._natively_hears_who(who_int)
            ):
                _gw.async_dispatcher_send(self.hass, f"myhome_message_{primary_gw.mac}", message)

    def _outage_confirmed(self) -> bool:
        """Down past the reconnect grace, or not up yet a grace period after setup."""
        from . import gateway as _gw

        return not self.is_connected and not self._available and _gw.time.monotonic() - self._setup_at >= _gw.AVAILABILITY_GRACE

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
        """Notify all entities bound to this gateway, and any primary backed by this standby or secondary."""
        from . import gateway as _gw

        _gw.async_dispatcher_send(self.hass, self.availability_signal)
        if self.is_standby:
            primary = self._get_primary_gateway()
            if primary is not None and not primary._available:
                primary._evaluate_failover()
                _gw.async_dispatcher_send(self.hass, primary.availability_signal)
        elif self.is_secondary and self.delegated_whos:
            primary = self._get_primary_gateway()
            if primary is not None:
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
        if getattr(message, "is_general", False) or getattr(message, "is_area", False) or getattr(message, "is_group", False):
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
        if any(tx_mac == self.mac and tx_frame == raw_msg and now - tx_time <= SHARED_BUS_TX_ECHO_S for tx_time, tx_mac, _tx_group, tx_frame in recent_tx):
            return
        for tx_time, tx_mac, tx_group, tx_frame in recent_tx:
            if tx_group != group and tx_frame == raw_msg and now - tx_time <= SHARED_BUS_TX_ECHO_S:
                self._record_shared_bus_evidence(tx_mac, now)
                return

    def _record_shared_bus_evidence(self, other_mac: str, now: float) -> None:
        """Count one correlated TX->RX echo; raise the repair issue on three echoes in the window."""
        my_mac, other_mac = dr.format_mac(str(self.mac)), dr.format_mac(str(other_mac))
        if not other_mac or other_mac == my_mac:
            return
        domain_data = self.hass.data.setdefault(DOMAIN, {})
        evidence_map = domain_data.setdefault("_shared_bus_evidence", {})
        pair_key = tuple(sorted([my_mac, other_mac]))
        seen = evidence_map.setdefault(pair_key, collections.deque(maxlen=SHARED_BUS_EVIDENCE_COUNT))
        seen.append(now)
        if len(seen) == SHARED_BUS_EVIDENCE_COUNT and seen[-1] - seen[0] <= SHARED_BUS_EVIDENCE_WINDOW_S:
            seen.clear()
            from .repairs import async_create_shared_bus_issue

            async_create_shared_bus_issue(self.hass, pair_key[0], pair_key[1])

    def _delegated_target(self, message: OWNCommand) -> MyHOMEGatewayHandler | None:
        """The connected secondary gateway that owns the delegated WHO subsystem."""
        msg_who = getattr(message, "who", getattr(message, "_who", None))
        if msg_who in (13, 1013) or msg_who is None or msg_who not in self.delegated_away_whos:
            return None
        sec = self._get_secondary_for_who(msg_who)
        if sec is None or not sec.is_connected or not peer_handles_who(sec, int(msg_who)):
            return None
        LOGGER.debug("%s Subsystem WHO=%s is delegated; sending `%s` through secondary gateway %s.", self.log_id, msg_who, message, sec.log_id)
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
        LOGGER.debug("%s Primary gateway is disconnected; sending `%s` through standby gateway %s.", self.log_id, message, standby.log_id)
        self._evaluate_failover()
        return standby
