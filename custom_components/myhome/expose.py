"""Expose Home Assistant entities and system clock to the OpenWebNet SCS bus.

Hardware Verification & Caveats (BTicino F461 `2.0.11` live captures `2026-10-18` & `2026-10-20`):
- **Report Form vs. `#DIM` Write Form on `WHO=1`, `WHO=2`, `WHO=4`, `WHO=18`**:
  - On real BTicino gateways (`bt_luci` and `bt_termo`), incoming un-hashed dimension response
    frames (`*#1*WHERE*1*level*speed##`, `*#2*WHERE*10*state*pos*001*0##`, `*#4*WHERE*0*TTTT##`)
    on the TCP command session are NACKed or parsed as dimension reads, whereas `#DIM` write
    frames (`*#1*WHERE*#1*level*speed##`, `*#2*WHERE*#11#001*pos##`, `*#4*WHERE*#0*TTTT##`)
    are ACKed in ~83–126 ms and forwarded to the SCS bus.
  - Conversely, `WHO=18` (`bt_energy` / `bt_supervisione`) **does** accept un-hashed dimension
    response frames (`*#18*WHERE*113*W##`, `51..54`, `1200#1`), which are ACKed on F461.
- **WHO=2 virtual covers**: Physical F461 gateways immediately broadcast command-translation
  bursts (`*2*1000#0*WHERE##` followed by `*2*1000#1/2*WHERE##`, and for `#11#001` level
  writes `*#2*WHERE*#11#001#1*pos##`) on the event monitor session before returning `ACK` or
  `NACK` on the TCP command session. `_pre_move_stop_guard` and `_tx_echo_guard` absorb all
  translation frames without self-oscillation.
- **WHO=13 clock**: Setting `periodic_send: 0` suppresses proactive `*#13**#22*...##` writes
  during `async_sync_all()` so NTP-synced gateways can use `WHO=13` in read-only query-reply mode.

Security Model for Custom Actions:
- Bidirectional `turn_on_action`, `turn_off_action`, `step_up_action`, and `step_down_action`
  entries invoke Home Assistant services in response to OpenWebNet bus frames, evaluating
  payload templates through Home Assistant's sandboxed `Template` engine. Because any device
  or client on the SCS bus / OpenWebNet TCP port can emit trigger frames, custom actions
  should only be configured on trusted physical and network installations.
"""

from __future__ import annotations

import asyncio
import datetime as dt
import math
import time
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import (
    CONF_ATTRIBUTE,
    CONF_ENTITY_ID,
    STATE_UNAVAILABLE,
    STATE_UNKNOWN,
)
from homeassistant.core import CALLBACK_TYPE, Event, HomeAssistant, State, callback
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.event import (
    async_call_later,
    async_track_state_change_event,
    async_track_time_interval,
)
from homeassistant.helpers.template import Template
from homeassistant.util import dt as dt_util
from OWNd.message import (
    OWNAutomationCommand,
    OWNEnergyCommand,
    OWNGatewayCommand,
    OWNHeatingCommand,
    OWNLightingCommand,
    OWNMessage,
)

from .const import (
    CONF_BIDIRECTIONAL,
    CONF_BRIGHTNESS_STEP_PCT,
    CONF_COOLDOWN,
    CONF_DEFAULT,
    CONF_DIMENSION,
    CONF_EXPOSE,
    CONF_MIN_DELTA,
    CONF_ON_STATES,
    CONF_PERIODIC_SEND,
    CONF_RESPOND_TO_READ,
    CONF_STEP_DOWN_ACTION,
    CONF_STEP_UP_ACTION,
    CONF_TURN_OFF_ACTION,
    CONF_TURN_ON_ACTION,
    CONF_VALUE_TEMPLATE,
    CONF_WHERE,
    CONF_WHO,
    LOGGER,
    SUBENTRY_TYPE_EXPOSE,
    area_of_where,
)
from .where_grammar import expose_message_where, normalize_expose_where

if TYPE_CHECKING:
    from .gateway import MyHOMEGatewayHandler

EXPOSE_SUPPORTED_WHOS: tuple[int, ...] = (1, 2, 4, 13, 18)
EXPOSE_ENERGY_DIMENSIONS: tuple[int, ...] = (51, 52, 53, 54, 113)
EXPOSE_CLOCK_DIMENSIONS: tuple[int, ...] = (0, 1, 22)
DEFAULT_ON_STATES: tuple[str, ...] = ("on", "playing", "open", "unlocked")
DEFAULT_MEDIA_PLAYER_ON_STATES: tuple[str, ...] = (
    "on",
    "playing",
    "paused",
    "idle",
    "buffering",
)
TX_ECHO_GUARD_WINDOW_S: float = 1.5


@dataclass
class _EchoGuardEntry:
    """Active loopback/translation echo guard entry for an outgoing frame."""

    seq: int
    target: tuple[int, str] | None
    expiry: float
    future: asyncio.Future[Any] | None = None
    seen_sources: set[str] = field(default_factory=set)

    def is_expired(self, now: float) -> bool:
        """Return True only when any attached write future is done and TTL has elapsed."""
        if self.future is not None and not self.future.done():
            return False
        return self.expiry <= now


def _where_matches_binding(
    who: int,
    binding_where: str,
    msg_where: str,
    *,
    allow_scope: bool,
) -> bool:
    """Return True if ``msg_where`` targets ``binding_where`` (exact or general/area scope)."""
    if binding_where == msg_where:
        return True
    if not allow_scope or who not in (1, 2) or not msg_where:
        return False
    b_base, b_iface = (
        binding_where.split("#4#", 1) if "#4#" in binding_where else (binding_where, "")
    )
    m_base, m_iface = (
        msg_where.split("#4#", 1) if "#4#" in msg_where else (msg_where, "")
    )
    if b_iface != m_iface:
        return False
    if m_base == "0":
        return True
    return bool(area_of_where(b_base) == m_base)


def _coerce_action_list(raw: Any) -> list[dict[str, Any]] | None:
    """Normalize an optional action or list of actions into a list of dicts."""
    if not raw:
        return None
    if isinstance(raw, dict):
        return [dict(raw)]
    if isinstance(raw, (list, tuple)):
        out = [dict(item) for item in raw if isinstance(item, dict)]
        return out or None
    return None


def _coerce_on_states(raw: Any, *, entity_id: str | None = None) -> tuple[str, ...]:
    """Normalize ``on_states`` into a lowercase tuple of state strings."""
    fallback = (
        DEFAULT_MEDIA_PLAYER_ON_STATES
        if entity_id and entity_id.startswith("media_player.")
        else DEFAULT_ON_STATES
    )
    if not raw:
        return fallback
    if isinstance(raw, str):
        parts = [s.strip().lower() for s in raw.split(",") if s.strip()]
        return tuple(parts) if parts else fallback
    if isinstance(raw, (list, tuple, set, frozenset)):
        parts = [str(s).strip().lower() for s in raw if str(s).strip()]
        return tuple(parts) if parts else fallback
    return fallback


def _require_finite_float(val: Any, name: str) -> float:
    """Convert ``val`` to a finite float, rejecting booleans and NaN/inf."""
    if isinstance(val, bool):
        raise ValueError(f"{name} must be a finite number")
    try:
        fval = float(val)
    except (TypeError, ValueError, OverflowError) as exc:
        raise ValueError(f"{name} must be a finite number") from exc
    if not math.isfinite(fval):
        raise ValueError(f"{name} must be a finite number")
    return fval


def _require_int(val: Any, name: str) -> int:
    """Convert ``val`` to an integer, rejecting booleans, non-finite values, and fractional floats."""
    if isinstance(val, bool):
        raise ValueError(f"{name} must be an integer")
    if isinstance(val, float):
        if not math.isfinite(val) or not val.is_integer():
            raise ValueError(f"{name} must be an integer")
        return int(val)
    try:
        return int(val)
    except (TypeError, ValueError, OverflowError) as exc:
        raise ValueError(f"{name} must be an integer") from exc


def _parse_position_pct(val: Any) -> int | None:
    """Parse a finite shutter position percentage (clamped to 0..100), or return None."""
    if val is None or isinstance(val, bool):
        return None
    try:
        fval = float(val)
    except (TypeError, ValueError, OverflowError):
        return None
    if not math.isfinite(fval):
        return None
    return min(max(round(fval), 0), 100)


def is_exposed_on_bus(
    hass: HomeAssistant | None,
    config_entry: ConfigEntry | None,
    who: int | str,
    where: str | int | None,
) -> bool:
    """Return True if ``(who, where)`` is exposed on ``config_entry`` or any gateway on the same shared bus."""
    if config_entry is None:
        return False
    runtime = getattr(config_entry, "runtime_data", None)
    expose_mgr = getattr(runtime, "expose_manager", None)
    if isinstance(expose_mgr, ExposeManager) and expose_mgr.is_exposed(who, where):
        return True
    if hass is None:
        return False
    gw = getattr(runtime, "gateway", None)
    my_bus_group = getattr(gw, "bus_group", None)
    if not isinstance(my_bus_group, str) or not my_bus_group:
        return False
    from .const import DOMAIN

    for other_entry in hass.config_entries.async_entries(DOMAIN):
        if other_entry.entry_id == config_entry.entry_id:
            continue
        other_rt = getattr(other_entry, "runtime_data", None)
        other_gw = getattr(other_rt, "gateway", None)
        other_mgr = getattr(other_rt, "expose_manager", None)
        if (
            isinstance(other_mgr, ExposeManager)
            and getattr(other_gw, "bus_group", None) == my_bus_group
            and other_mgr.is_exposed(who, where)
        ):
            return True
    return False


@dataclass
class ExposeBinding:
    """Single entity or system attribute exposed onto the OpenWebNet bus."""

    who: int
    where: str
    entity_id: str | None = None
    attribute: str | None = None
    dimension: int | None = None
    value_template: str | None = None
    cooldown: float = 1.0
    min_delta: float | None = None
    periodic_send: float = 0.0
    respond_to_read: bool = True
    bidirectional: bool = True
    default: Any = None
    on_states: tuple[str, ...] = DEFAULT_ON_STATES
    turn_on_action: list[dict[str, Any]] | None = None
    turn_off_action: list[dict[str, Any]] | None = None
    step_up_action: list[dict[str, Any]] | None = None
    step_down_action: list[dict[str, Any]] | None = None
    brightness_step_pct: int = 10
    source: str = "yaml"
    subentry_id: str | None = None

    # Runtime telemetry & state
    last_sent_value: Any = field(default=None, init=False, repr=False)
    last_sent_at: float = field(default=0.0, init=False, repr=False)
    sent_count: int = field(default=0, init=False, repr=False)
    query_reply_count: int = field(default=0, init=False, repr=False)
    rx_action_count: int = field(default=0, init=False, repr=False)
    stream_expires_at: float = field(default=0.0, init=False, repr=False)

    @property
    def key(self) -> tuple[int, str, int | None]:
        """Return the unique address key for this binding."""
        return (self.who, self.where, self.dimension)

    @classmethod
    def from_dict(
        cls,
        cfg: dict[str, Any],
        *,
        source: str = "yaml",
        subentry_id: str | None = None,
    ) -> ExposeBinding:
        """Construct an :class:`ExposeBinding` from a validated config dictionary."""
        who = _require_int(cfg.get(CONF_WHO), "who")
        if who not in EXPOSE_SUPPORTED_WHOS:
            raise ValueError(f"Unsupported expose WHO={who}")
        where = normalize_expose_where(who, cfg.get(CONF_WHERE))
        if who != 13 and (not where or "*" in where):
            raise ValueError(f"Invalid expose WHERE={where!r}")
        raw_dim = cfg.get(CONF_DIMENSION)
        if raw_dim is not None and str(raw_dim).strip() != "":
            dimension: int | None = _require_int(raw_dim, "dimension")
        elif who == 4:
            dimension = 0
        elif who == 18:
            dimension = 113
        elif who == 13:
            dimension = 22
        else:
            dimension = None

        raw_periodic = cfg.get(CONF_PERIODIC_SEND)
        if raw_periodic is not None and str(raw_periodic).strip() != "":
            periodic_send = max(
                0.0, _require_finite_float(raw_periodic, "periodic_send")
            )
        elif who == 4:
            periodic_send = 900.0
        elif who == 13:
            periodic_send = 86400.0
        else:
            periodic_send = 0.0

        raw_bidir = cfg.get(CONF_BIDIRECTIONAL)
        if raw_bidir is not None:
            bidirectional = bool(raw_bidir)
        else:
            bidirectional = who in (1, 2)

        raw_cooldown = cfg.get(CONF_COOLDOWN)
        cooldown = (
            max(0.0, _require_finite_float(raw_cooldown, "cooldown"))
            if raw_cooldown is not None
            else 1.0
        )

        raw_min_delta = cfg.get(CONF_MIN_DELTA)
        min_delta = (
            max(0.0, _require_finite_float(raw_min_delta, "min_delta"))
            if raw_min_delta is not None and str(raw_min_delta).strip() != ""
            else None
        )

        raw_step = cfg.get(CONF_BRIGHTNESS_STEP_PCT)
        brightness_step_pct = (
            min(max(_require_int(raw_step, "brightness_step_pct"), 1), 100)
            if raw_step is not None
            else 10
        )

        default_val = cfg.get(CONF_DEFAULT)
        if isinstance(default_val, float) and not math.isfinite(default_val):
            raise ValueError("default must be finite")

        entity_id = str(cfg.get(CONF_ENTITY_ID) or "").strip() or None
        attribute = str(cfg.get(CONF_ATTRIBUTE) or "").strip() or None
        value_template = str(cfg.get(CONF_VALUE_TEMPLATE) or "").strip() or None

        return cls(
            who=who,
            where=where,
            entity_id=entity_id,
            attribute=attribute,
            dimension=dimension,
            value_template=value_template,
            cooldown=cooldown,
            min_delta=min_delta,
            periodic_send=periodic_send,
            respond_to_read=bool(cfg.get(CONF_RESPOND_TO_READ, True)),
            bidirectional=bidirectional,
            default=default_val,
            on_states=_coerce_on_states(cfg.get(CONF_ON_STATES), entity_id=entity_id),
            turn_on_action=_coerce_action_list(cfg.get(CONF_TURN_ON_ACTION)),
            turn_off_action=_coerce_action_list(cfg.get(CONF_TURN_OFF_ACTION)),
            step_up_action=_coerce_action_list(cfg.get(CONF_STEP_UP_ACTION)),
            step_down_action=_coerce_action_list(cfg.get(CONF_STEP_DOWN_ACTION)),
            brightness_step_pct=brightness_step_pct,
            source=source,
            subentry_id=subentry_id,
        )

    def as_diagnostics_dict(self, hass: HomeAssistant | None = None) -> dict[str, Any]:
        """Serialize binding configuration and runtime telemetry for diagnostics."""
        cur_state: str | None = None
        if hass is not None and self.entity_id:
            st = hass.states.get(self.entity_id)
            if st is not None:
                cur_state = st.state
        return {
            "who": self.who,
            "where": self.where,
            "dimension": self.dimension,
            "entity_id": self.entity_id,
            "attribute": self.attribute,
            "current_ha_state": cur_state,
            "cooldown": self.cooldown,
            "min_delta": self.min_delta,
            "periodic_send": self.periodic_send,
            "respond_to_read": self.respond_to_read,
            "bidirectional": self.bidirectional,
            "on_states": list(self.on_states),
            "brightness_step_pct": self.brightness_step_pct,
            "has_custom_actions": any(
                (
                    self.turn_on_action,
                    self.turn_off_action,
                    self.step_up_action,
                    self.step_down_action,
                )
            ),
            "source": self.source,
            "subentry_id": self.subentry_id,
            "last_sent_value": self.last_sent_value,
            "last_sent_at": self.last_sent_at,
            "sent_count": self.sent_count,
            "query_reply_count": self.query_reply_count,
            "rx_action_count": self.rx_action_count,
        }


class ExposeManager:
    """Manages state listeners, bus queries, and frame injection for exposed entities."""

    def __init__(
        self,
        hass: HomeAssistant,
        config_entry: ConfigEntry,
        gateway: MyHOMEGatewayHandler | None = None,
        yaml_configs: list[dict[str, Any]] | None = None,
    ) -> None:
        """Initialize the ExposeManager."""
        self.hass = hass
        self.config_entry = config_entry
        runtime = getattr(config_entry, "runtime_data", None)
        if gateway is None and runtime is not None:
            gateway = getattr(runtime, "gateway", None)
        self.gateway: MyHOMEGatewayHandler = gateway  # type: ignore[assignment]
        if yaml_configs is None:
            if runtime is not None and getattr(runtime, "expose", None):
                yaml_configs = list(runtime.expose)
            elif getattr(config_entry, "data", None) and config_entry.data.get(CONF_EXPOSE):
                yaml_configs = list(config_entry.data[CONF_EXPOSE])
            else:
                yaml_configs = []
        self.bindings: list[ExposeBinding] = []
        self._unsub_state: list[CALLBACK_TYPE] = []
        self._unsub_dispatcher: CALLBACK_TYPE | None = None
        self._unsub_secondary_dispatchers: list[CALLBACK_TYPE] = []
        self._subscribed_secondary_macs: set[str] = set()
        self._unsub_periodic: list[CALLBACK_TYPE] = []
        self._cooldown_timers: dict[tuple[int, str, int | None], CALLBACK_TYPE] = {}
        self._stream_timers: dict[tuple[int, str, int | None], CALLBACK_TYPE] = {}
        self._echo_seq: int = 0
        self._tx_echo_guard: dict[str, _EchoGuardEntry] = {}
        self._pre_move_stop_guard: dict[tuple[int, str], _EchoGuardEntry] = {}
        self._last_rx_by_frame: dict[str, tuple[str, float]] = {}
        self._started = False

        self._load_bindings(yaml_configs)

    @property
    def _cooldown_unsubs(self) -> dict[tuple[int, str, int | None], CALLBACK_TYPE]:
        """Return active cooldown timer unsub callbacks."""
        return self._cooldown_timers

    def _load_bindings(self, yaml_configs: list[dict[str, Any]]) -> None:
        """Load bindings from YAML and ConfigSubentries, deduplicating by (who, where, dimension)."""
        seen: dict[tuple[int, str, int | None], ExposeBinding] = {}
        for cfg in yaml_configs:
            try:
                binding = ExposeBinding.from_dict(cfg, source="yaml")
                seen[binding.key] = binding
            except (KeyError, TypeError, ValueError) as err:
                LOGGER.warning("Skipping invalid YAML expose binding %s: %s", cfg, err)

        subentries = getattr(self.config_entry, "subentries", None) or {}
        for sub_id, subentry in subentries.items():
            if getattr(subentry, "subentry_type", None) != SUBENTRY_TYPE_EXPOSE:
                continue
            data = dict(getattr(subentry, "data", None) or {})
            try:
                binding = ExposeBinding.from_dict(
                    data, source="subentry", subentry_id=str(sub_id)
                )
                seen[binding.key] = binding
            except (KeyError, TypeError, ValueError) as err:
                LOGGER.warning(
                    "Skipping invalid subentry expose binding %s: %s", sub_id, err
                )

        self.bindings = list(seen.values())

    @property
    def exposed_addresses(self) -> set[tuple[int, str]]:
        """Return the set of ``(who, where)`` pairs managed as virtual exposed devices."""
        return {
            (b.who, b.where)
            for b in self.bindings
            if b.who in (1, 2, 4, 18) and b.where
        }

    def is_exposed(self, who: int | str, where: str | int | None) -> bool:
        """Return True if ``(who, where)`` is an exposed virtual device on this gateway."""
        try:
            who_int = int(who)
        except (TypeError, ValueError):
            return False
        norm = normalize_expose_where(who_int, where)
        if not norm:
            return False
        return (who_int, norm) in self.exposed_addresses

    def subscribe_secondary_gateways(self) -> None:
        """Subscribe to any shared-bus secondary gateways linked to this primary gateway."""
        if not self._started:
            return
        from .const import DOMAIN

        for other_entry in self.hass.config_entries.async_entries(DOMAIN):
            if other_entry.entry_id == self.config_entry.entry_id:
                continue
            other_rt = getattr(other_entry, "runtime_data", None)
            other_gw = getattr(other_rt, "gateway", None)
            sec_mac = str(getattr(other_gw, "mac", "") or "")
            if (
                other_gw is not None
                and getattr(other_gw, "primary_gateway_mac", None) == self.gateway.mac
                and sec_mac
                and sec_mac not in self._subscribed_secondary_macs
            ):
                self._subscribed_secondary_macs.add(sec_mac)
                self._unsub_secondary_dispatchers.append(
                    async_dispatcher_connect(
                        self.hass,
                        f"myhome_message_{sec_mac}",
                        self._make_secondary_bus_listener(sec_mac),
                    )
                )

    def _make_secondary_bus_listener(self, sec_mac: str) -> Any:
        """Create a secondary-gateway bus listener bound to ``sec_mac``."""

        async def _on_secondary_msg(message: OWNMessage) -> None:
            await self._async_handle_secondary_bus_message(
                message, source_mac=sec_mac
            )

        return _on_secondary_msg

    async def async_start(self) -> None:
        """Subscribe to Home Assistant state changes, bus frames, and periodic timers."""
        pri_mac = getattr(self.gateway, "primary_gateway_mac", None)
        if pri_mac:
            from .const import DOMAIN

            for other_entry in self.hass.config_entries.async_entries(DOMAIN):
                if other_entry.entry_id == self.config_entry.entry_id:
                    continue
                other_rt = getattr(other_entry, "runtime_data", None)
                other_gw = getattr(other_rt, "gateway", None)
                other_mgr = getattr(other_rt, "expose_manager", None)
                if (
                    other_mgr is not None
                    and getattr(other_gw, "mac", None) == pri_mac
                ):
                    other_mgr.subscribe_secondary_gateways()

        if self._started or not self.bindings:
            return
        self._started = True

        # 1. Subscribe to Gateway bus frames (and any shared-bus secondary gateways)
        self._unsub_dispatcher = async_dispatcher_connect(
            self.hass,
            f"myhome_message_{self.gateway.mac}",
            self._async_handle_bus_message,
        )
        self.subscribe_secondary_gateways()

        # 2. Group bindings by entity_id and subscribe to state changes
        by_entity: dict[str, list[ExposeBinding]] = {}
        for binding in self.bindings:
            if binding.entity_id:
                by_entity.setdefault(binding.entity_id, []).append(binding)

        for entity_id, entity_bindings in by_entity.items():
            unsub = async_track_state_change_event(
                self.hass,
                [entity_id],
                self._make_state_listener(entity_bindings),
            )
            self._unsub_state.append(unsub)

        # 3. Start periodic_send timers
        for binding in self.bindings:
            if binding.periodic_send > 0:
                unsub_timer = async_track_time_interval(
                    self.hass,
                    self._make_periodic_callback(binding),
                    dt.timedelta(seconds=binding.periodic_send),
                )
                self._unsub_periodic.append(unsub_timer)

    async def async_stop(self) -> None:
        """Cancel all listeners and pending cooldown/streaming timers."""
        self._started = False
        if self._unsub_dispatcher is not None:
            self._unsub_dispatcher()
            self._unsub_dispatcher = None
        for unsub in self._unsub_secondary_dispatchers:
            unsub()
        self._unsub_secondary_dispatchers.clear()
        self._subscribed_secondary_macs.clear()
        for unsub in self._unsub_state:
            unsub()
        self._unsub_state.clear()
        for unsub in self._unsub_periodic:
            unsub()
        self._unsub_periodic.clear()
        for cancel in self._cooldown_timers.values():
            cancel()
        self._cooldown_timers.clear()
        for cancel in self._stream_timers.values():
            cancel()
        self._stream_timers.clear()
        self._tx_echo_guard.clear()
        self._pre_move_stop_guard.clear()
        self._last_rx_by_frame.clear()

    async def async_sync_all(self) -> None:
        """Send an initial state/clock sync for all configured bindings."""
        for binding in self.bindings:
            if binding.who == 13 and binding.periodic_send <= 0:
                continue
            await self._async_send_binding_state(
                binding, high_priority=False, force=True, is_reply=False
            )

    def _make_state_listener(
        self, entity_bindings: list[ExposeBinding]
    ) -> Any:
        """Create a state-change callback for a list of bindings sharing an entity_id."""

        @callback
        def _on_state_change(event: Event) -> None:
            new_state: State | None = event.data.get("new_state")
            if new_state is None:
                return
            for binding in entity_bindings:
                self._schedule_binding_send(binding)

        return _on_state_change

    def _make_periodic_callback(self, binding: ExposeBinding) -> Any:
        """Create a periodic timer callback for a binding."""

        async def _on_periodic(_now: dt.datetime) -> None:
            await self._async_send_binding_state(
                binding, high_priority=False, force=True, is_reply=False
            )

        return _on_periodic

    def _cancel_cooldown_timer(self, key: tuple[int, str, int | None]) -> None:
        """Cancel any pending trailing-edge cooldown timer for ``key``."""
        cancel = self._cooldown_timers.pop(key, None)
        if cancel is not None:
            cancel()

    @callback
    def _schedule_binding_send(self, binding: ExposeBinding) -> None:
        """Send immediately if outside cooldown window, or schedule trailing-edge send."""
        now = time.monotonic()
        key = binding.key
        elapsed = now - binding.last_sent_at
        effective_cooldown = binding.cooldown
        if (
            binding.who == 18
            and binding.dimension == 113
            and binding.stream_expires_at > now
        ):
            effective_cooldown = min(effective_cooldown, 2.0)

        if effective_cooldown <= 0 or binding.last_sent_at == 0.0 or elapsed >= effective_cooldown:
            self._cancel_cooldown_timer(key)
            self.hass.async_create_task(
                self._async_send_binding_state(
                    binding, high_priority=False, force=False, is_reply=False
                )
            )
            return

        # Inside cooldown window: ensure a single trailing-edge timer is armed
        if key in self._cooldown_timers:
            return
        delay = max(0.01, effective_cooldown - elapsed)

        @callback
        def _fire_trailing(_now: Any) -> None:
            self._cooldown_timers.pop(key, None)
            self.hass.async_create_task(
                self._async_send_binding_state(
                    binding, high_priority=False, force=False, is_reply=False
                )
            )

        self._cooldown_timers[key] = async_call_later(self.hass, delay, _fire_trailing)

    def _record_echo_guard(
        self, frame: OWNMessage | str, *, is_confirmation: bool = False
    ) -> list[_EchoGuardEntry]:
        """Record an outgoing frame (and its Command Translation / pre-move equivalents) in the loopback guard."""
        now = time.monotonic()
        expiry = now + TX_ECHO_GUARD_WINDOW_S
        expired_keys = [
            frame_k
            for frame_k, entry in self._tx_echo_guard.items()
            if entry.is_expired(now)
        ]
        for frame_k in expired_keys:
            self._tx_echo_guard.pop(frame_k, None)
        expired_stop_keys = [
            stop_k
            for stop_k, entry in self._pre_move_stop_guard.items()
            if entry.is_expired(now)
        ]
        for stop_k in expired_stop_keys:
            self._pre_move_stop_guard.pop(stop_k, None)

        self._echo_seq += 1
        seq = self._echo_seq
        entries: list[_EchoGuardEntry] = []
        target: tuple[int, str] | None = None

        if isinstance(frame, OWNMessage):
            who = getattr(frame, "who", None)
            what = getattr(frame, "_what", getattr(frame, "what", None))
            msg_where = expose_message_where(frame)
            if isinstance(who, int) and msg_where:
                target = (who, msg_where)
            if is_confirmation and target is not None:
                # An incoming external bus command superseded any earlier completed TX for this target
                done_keys = [
                    frame_k
                    for frame_k, entry in self._tx_echo_guard.items()
                    if entry.target == target
                    and (entry.future is None or entry.future.done())
                ]
                for frame_k in done_keys:
                    self._tx_echo_guard.pop(frame_k, None)
            if who in (1, 2) and isinstance(what, int) and what != 1000 and msg_where:
                trans_entry = _EchoGuardEntry(seq=seq, target=target, expiry=expiry)
                self._tx_echo_guard[f"*{who}*1000#{what}*{msg_where}##"] = trans_entry
                entries.append(trans_entry)
                if who == 2 and what in (1, 2):
                    # 10 gateways (F453, F453AV, F454, F459, F460, F461, H4684,
                    # MH200, MH200N, MyHomeServer1; MH202 emits 1 frame) emit a
                    # one-shot pre-move STOP (*2*1000#0*W##) before a WHO=2 UP/DOWN move frame.
                    stop_entry = _EchoGuardEntry(seq=seq, target=target, expiry=expiry)
                    self._pre_move_stop_guard[(2, msg_where)] = stop_entry
                    entries.append(stop_entry)
            if (
                who == 2
                and bool(getattr(frame, "is_dimension_writing", False))
                and getattr(frame, "dimension", None) == 11
                and msg_where
            ):
                vals = getattr(frame, "dimension_value", None) or []
                if vals:
                    # 6 gateways (F454, F459, F460, F461, MH202, MyHomeServer1)
                    # translate *#2*W*#11#001*LEVEL## into *#2*W*#11#001#1*LEVEL## on the monitor session.
                    lvl_entry = _EchoGuardEntry(seq=seq, target=target, expiry=expiry)
                    self._tx_echo_guard[f"*#2*{msg_where}*#11#001#1*{vals[0]}##"] = (
                        lvl_entry
                    )
                    entries.append(lvl_entry)

        raw_entry = _EchoGuardEntry(seq=seq, target=target, expiry=expiry)
        self._tx_echo_guard[str(frame)] = raw_entry
        entries.append(raw_entry)
        return entries

    def _attach_echo_guard_future(
        self, entries: list[_EchoGuardEntry], fut: asyncio.Future[Any]
    ) -> None:
        """Bind a send-queue delivery future so echo guards stay alive while queued and expire when cancelled."""
        for entry in entries:
            entry.future = fut

        def _on_written_done(done_fut: asyncio.Future[Any]) -> None:
            if done_fut.cancelled():
                for frame_k, entry in list(self._tx_echo_guard.items()):
                    if entry in entries:
                        self._tx_echo_guard.pop(frame_k, None)
                for stop_k, entry in list(self._pre_move_stop_guard.items()):
                    if entry in entries:
                        self._pre_move_stop_guard.pop(stop_k, None)
                return
            refreshed = time.monotonic() + TX_ECHO_GUARD_WINDOW_S
            for entry in entries:
                entry.expiry = max(entry.expiry, refreshed)

        if fut.done():
            _on_written_done(fut)
        else:
            fut.add_done_callback(_on_written_done)

    def _is_echo_guarded(
        self, message: OWNMessage, *, source_mac: str | None = None
    ) -> bool:
        """Return True if ``message`` is a loopback or translation echo of a frame sent by ExposeManager."""
        now = time.monotonic()
        src = source_mac or str(getattr(self.gateway, "mac", "") or "primary")
        raw_frame = str(message)
        who = getattr(message, "who", None)
        eff_what = getattr(message, "effective_what", None)
        msg_where = expose_message_where(message)

        if who == 2 and msg_where:
            stop_entry = self._pre_move_stop_guard.get((2, msg_where))
            if stop_entry is not None:
                if stop_entry.is_expired(now):
                    self._pre_move_stop_guard.pop((2, msg_where), None)
                elif eff_what == 0 and src not in stop_entry.seen_sources:
                    # Consume the one-shot pre-move STOP echo for this gateway source
                    if stop_entry.future is None or stop_entry.future.done():
                        stop_entry.seen_sources.add(src)
                        if not self._subscribed_secondary_macs:
                            self._pre_move_stop_guard.pop((2, msg_where), None)
                    return True
                elif eff_what in (1, 2) and (
                    stop_entry.future is None or stop_entry.future.done()
                ):
                    # Move echo arrived; pre-move STOP window is over
                    self._pre_move_stop_guard.pop((2, msg_where), None)

        entry = self._tx_echo_guard.get(raw_frame)
        if entry is None:
            return False
        if entry.is_expired(now):
            self._tx_echo_guard.pop(raw_frame, None)
            return False
        if src in entry.seen_sources:
            return False
        if entry.future is not None and not entry.future.done():
            return True

        is_translation = bool(getattr(message, "is_translation", False)) or (
            who == 2
            and bool(getattr(message, "is_dimension_writing", False))
            and getattr(message, "dimension", None) == 11
            and (getattr(message, "_dimension_param", None) or []) == ["001", "1"]
        )
        # Mark this echo (and any older completed entries for the same target) consumed for src.
        # A translation echo (*WHO*1000#WHAT*W##) may precede a raw loopback echo (*WHO*WHAT*W##),
        # so keep the sibling raw entry alive until the raw echo arrives or is superseded.
        for frame_k, candidate in list(self._tx_echo_guard.items()):
            if (
                candidate is entry
                or (not is_translation and candidate.seq == entry.seq)
                or (
                    entry.target is not None
                    and candidate.target == entry.target
                    and candidate.seq < entry.seq
                    and (candidate.future is None or candidate.future.done())
                )
            ):
                candidate.seen_sources.add(src)
                if not self._subscribed_secondary_macs:
                    self._tx_echo_guard.pop(frame_k, None)
        return True

    async def _async_dispatch_frames(
        self,
        frames: list[OWNMessage],
        *,
        high_priority: bool,
        is_confirmation: bool = False,
    ) -> None:
        """Record frames in the echo guard and queue them on the gateway."""
        for frame in frames:
            entries = self._record_echo_guard(frame, is_confirmation=is_confirmation)
            if high_priority:
                fut = await self.gateway.send(frame)
            else:
                fut = await self.gateway.send_status_request(frame)
            if isinstance(fut, asyncio.Future):
                self._attach_echo_guard_future(entries, fut)

    def _effective_profile(self, who: int) -> Any:
        """Return the active GatewayProfile for ``who``, accounting for shared-bus delegation."""
        target_gw: Any = self.gateway
        delegated_away = getattr(target_gw, "delegated_away_whos", None)
        if isinstance(delegated_away, (set, frozenset, list, tuple)) and who in delegated_away:
            get_sec = getattr(target_gw, "_get_secondary_for_who", None)
            sec = get_sec(who) if callable(get_sec) else None
            if sec is not None and getattr(sec, "is_connected", False) is True:
                target_gw = sec

        for candidate in (
            getattr(target_gw, "profile", None),
            getattr(getattr(target_gw, "_gateway", None), "profile", None),
            getattr(getattr(target_gw, "gateway", None), "profile", None),
        ):
            if candidate is not None and (
                isinstance(
                    getattr(candidate, "supports_dimension_response_injection", None),
                    bool,
                )
                or isinstance(
                    getattr(candidate, "supports_who4_command_injection", None),
                    bool,
                )
            ):
                return candidate
        return getattr(target_gw, "profile", None)

    def _resolve_raw_value(
        self, binding: ExposeBinding
    ) -> tuple[Any, State | None]:
        """Resolve the raw value (and HA State object) for a binding."""
        if binding.who == 13:
            return dt_util.now(), None

        state = (
            self.hass.states.get(binding.entity_id)
            if binding.entity_id
            else None
        )
        if state is None or state.state in (STATE_UNAVAILABLE, STATE_UNKNOWN):
            if binding.default is not None:
                raw: Any = binding.default
            else:
                return None, state
        elif binding.attribute:
            raw = state.attributes.get(binding.attribute)
            if raw is None:
                if binding.default is not None:
                    raw = binding.default
                elif (
                    binding.who == 1
                    and binding.attribute == "brightness"
                    and state.state.strip().lower() not in binding.on_states
                ):
                    raw = 0
                elif (
                    binding.who == 2
                    and binding.attribute == "current_position"
                    and state.state.strip().lower() in ("closed", "open")
                ):
                    raw = 0 if state.state.strip().lower() == "closed" else 100
                else:
                    return None, state
        else:
            raw = state.state

        if binding.value_template:
            try:
                tpl = Template(binding.value_template, self.hass)
                raw = tpl.async_render(
                    {
                        "value": raw,
                        "state": state,
                        "entity_id": binding.entity_id,
                    },
                    parse_result=False,
                )
            except Exception as err:
                LOGGER.warning(
                    "Failed rendering expose value_template for %s: %s",
                    binding.entity_id,
                    err,
                )
                return None, state

        return raw, state

    def _build_frames(
        self,
        binding: ExposeBinding,
        *,
        force: bool,
        is_reply: bool,
        query_dimension: int | None = None,
    ) -> tuple[list[OWNMessage], Any]:
        """Build OpenWebNet frame(s) and normalized value for ``binding``."""
        raw, state = self._resolve_raw_value(binding)
        if raw is None:
            return [], None

        if binding.who == 1:
            if isinstance(raw, bool):
                is_on = raw
            else:
                raw_str = str(raw).strip().lower()
                is_on = raw_str in binding.on_states or raw_str in ("true", "1")

            frames: list[OWNMessage] = []
            # Check if brightness reporting is requested or queried
            brightness_pct: int | None = None
            if binding.attribute == "brightness" or binding.dimension == 1 or query_dimension == 1:
                try:
                    from_ha_brightness = (
                        state is not None
                        and state.attributes.get("brightness") is not None
                        and (binding.attribute in (None, "brightness"))
                    )
                    if binding.attribute == "brightness":
                        b_cand: Any = raw
                    elif (
                        state is not None
                        and state.attributes.get("brightness") is not None
                    ):
                        b_cand = state.attributes.get("brightness")
                    else:
                        b_cand = 100 if is_on else 0
                    if isinstance(b_cand, bool):
                        raise ValueError("boolean brightness")
                    b_raw = float(b_cand)
                    if not math.isfinite(b_raw):
                        raise ValueError("non-finite brightness")
                    if from_ha_brightness and not binding.value_template:
                        brightness_pct = round(min(max(b_raw, 0.0), 255.0) * 100.0 / 255.0)
                    else:
                        brightness_pct = round(min(max(b_raw, 0.0), 100.0))
                    is_on = brightness_pct > 0
                except (TypeError, ValueError, OverflowError):
                    brightness_pct = 100 if is_on else 0

            who1_val_key: tuple[bool, int | None] = (is_on, brightness_pct)
            if not force and binding.last_sent_value == who1_val_key:
                return [], None

            profile = self._effective_profile(1)
            supports_dim_resp = bool(
                getattr(profile, "supports_dimension_response_injection", False)
            )
            if query_dimension == 1 and brightness_pct is not None:
                frames.append(
                    OWNLightingCommand.report_brightness(binding.where, brightness_pct)
                    if supports_dim_resp
                    else OWNLightingCommand.set_brightness(
                        binding.where, brightness_pct
                    )
                )
            else:
                frames.append(
                    OWNLightingCommand.switch_on(binding.where)
                    if is_on
                    else OWNLightingCommand.switch_off(binding.where)
                )
                if brightness_pct is not None and (
                    binding.attribute == "brightness" or binding.dimension == 1
                ):
                    if supports_dim_resp:
                        frames.append(
                            OWNLightingCommand.report_brightness(
                                binding.where, brightness_pct
                            )
                        )
                    elif brightness_pct > 0:
                        frames.append(
                            OWNLightingCommand.set_brightness(
                                binding.where, brightness_pct
                            )
                        )
            return frames, who1_val_key

        if binding.who == 2:
            raw_str = str(raw).strip().lower()
            pos_pct: int | None = None
            if state is not None and "current_position" in state.attributes:
                pos_pct = _parse_position_pct(state.attributes["current_position"])
            elif not isinstance(raw, bool):
                pos_pct = _parse_position_pct(raw)

            if raw_str in ("opening", "up"):
                shutter_cmd = OWNAutomationCommand.raise_shutter(binding.where)
                state_code = 11
            elif raw_str in ("closing", "down"):
                shutter_cmd = OWNAutomationCommand.lower_shutter(binding.where)
                state_code = 12
            else:
                shutter_cmd = OWNAutomationCommand.stop_shutter(binding.where)
                state_code = 10

            reports_pos = (
                binding.attribute == "current_position"
                or binding.dimension in (10, 11)
                or query_dimension in (10, 11)
            )
            who2_val_key: tuple[str, int | None] = (
                str(shutter_cmd),
                pos_pct if reports_pos else None,
            )
            if not force and binding.last_sent_value == who2_val_key:
                return [], None

            profile = self._effective_profile(2)
            supports_dim_resp = bool(
                getattr(profile, "supports_dimension_response_injection", False)
            )
            if query_dimension in (10, 11) and pos_pct is not None:
                return [
                    OWNAutomationCommand.report_shutter_level(
                        binding.where, pos_pct, state=state_code
                    )
                    if supports_dim_resp
                    else OWNAutomationCommand.set_shutter_level(binding.where, pos_pct)
                ], who2_val_key

            frames = [shutter_cmd]
            if pos_pct is not None and (
                binding.attribute == "current_position" or binding.dimension in (10, 11)
            ):
                frames.append(
                    OWNAutomationCommand.report_shutter_level(
                        binding.where, pos_pct, state=state_code
                    )
                    if supports_dim_resp
                    else OWNAutomationCommand.set_shutter_level(binding.where, pos_pct)
                )
            return frames, who2_val_key

        if binding.who == 4:
            profile = self._effective_profile(4)
            supports_dim_resp = (
                False
                if profile is None
                else bool(
                    getattr(profile, "supports_dimension_response_injection", False)
                )
            )
            supports_cmd_inj = (
                True
                if profile is None
                else bool(getattr(profile, "supports_who4_command_injection", True))
            )
            if not supports_dim_resp and not supports_cmd_inj:
                return [], None
            if isinstance(raw, bool):
                return [], None
            try:
                fval = float(raw)
            except (TypeError, ValueError, OverflowError):
                return [], None
            if not math.isfinite(fval) or not -99.9 <= fval <= 99.9:
                return [], None
            temp_c = round(fval, 1)

            if not force and binding.last_sent_value is not None:
                if binding.min_delta is not None:
                    if abs(temp_c - float(binding.last_sent_value)) < binding.min_delta:
                        return [], None
                elif temp_c == binding.last_sent_value:
                    return [], None

            if supports_dim_resp:
                return [OWNHeatingCommand.report_temperature(binding.where, temp_c)], temp_c
            return [OWNHeatingCommand.write_temperature(binding.where, temp_c)], temp_c

        if binding.who == 18:
            profile = self._effective_profile(18)
            if profile is not None:
                who18_inj = getattr(
                    profile, "supports_who18_response_injection", None
                )
                supports_who18 = (
                    bool(who18_inj)
                    if isinstance(who18_inj, bool)
                    else bool(
                        getattr(
                            profile,
                            "supports_dimension_response_injection",
                            True,
                        )
                    )
                )
                if not supports_who18:
                    return [], None
            if isinstance(raw, bool):
                return [], None
            try:
                val = float(raw)
            except (TypeError, ValueError, OverflowError):
                return [], None
            if not math.isfinite(val) or val < 0.0:
                return [], None

            dim = binding.dimension if binding.dimension is not None else 113
            if state is not None and not binding.value_template:
                unit = str(state.attributes.get("unit_of_measurement") or "").strip()
                if dim == 113 and unit.lower() == "kw":
                    val *= 1000.0
                elif dim in (51, 52, 53, 54) and unit.lower() == "kwh":
                    val *= 1000.0
            if not math.isfinite(val):
                return [], None

            rounded = round(val)
            if not force and binding.last_sent_value is not None:
                if binding.min_delta is not None:
                    if abs(val - float(binding.last_sent_value)) < binding.min_delta:
                        return [], None
                elif rounded == round(float(binding.last_sent_value)):
                    return [], None

            if dim == 113:
                cmd = OWNEnergyCommand.report_active_power(binding.where, rounded)
            elif dim == 51:
                cmd = OWNEnergyCommand.report_total_energy(binding.where, rounded)
            elif dim == 52:
                cmd = OWNEnergyCommand.report_current_month_energy(binding.where, rounded)
            elif dim == 53:
                cmd = OWNEnergyCommand.report_partial_month_energy(binding.where, rounded)
            elif dim == 54:
                cmd = OWNEnergyCommand.report_partial_day_energy(binding.where, rounded)
            else:
                return [], None
            return [cmd], val

        if binding.who == 13:
            now_dt = raw if isinstance(raw, dt.datetime) else dt_util.now()
            tz_name = str(self.hass.config.time_zone or "UTC")
            dim = query_dimension if query_dimension is not None else (binding.dimension if binding.dimension is not None else 22)
            try:
                if is_reply:
                    if dim == 0:
                        cmd = OWNGatewayCommand.report_time(now_dt, tz_name)
                    elif dim == 1:
                        cmd = OWNGatewayCommand.report_date(now_dt, tz_name)
                    else:
                        cmd = OWNGatewayCommand.report_datetime(now_dt, tz_name)
                else:
                    if dim == 0:
                        cmd = OWNGatewayCommand.set_time(now_dt, tz_name)
                    elif dim == 1:
                        cmd = OWNGatewayCommand.set_date(now_dt, tz_name)
                    else:
                        cmd = OWNGatewayCommand.set_datetime(now_dt, tz_name)
            except ValueError:
                return [], None
            return [cmd], now_dt.isoformat()

        return [], None

    async def _async_send_binding_state(
        self,
        binding: ExposeBinding,
        *,
        high_priority: bool,
        force: bool,
        is_reply: bool,
        query_dimension: int | None = None,
    ) -> None:
        """Build and send OpenWebNet frame(s) for ``binding``."""
        frames, sent_val = self._build_frames(
            binding,
            force=force,
            is_reply=is_reply,
            query_dimension=query_dimension,
        )
        if not frames:
            return
        binding.last_sent_value = sent_val
        binding.last_sent_at = time.monotonic()
        binding.sent_count += len(frames)
        if is_reply:
            binding.query_reply_count += 1
        await self._async_dispatch_frames(
            frames, high_priority=high_priority, is_confirmation=is_reply
        )

    async def _async_run_actions(self, actions: list[dict[str, Any]]) -> None:
        """Execute a list of Home Assistant service action dicts."""
        for item in actions:
            service_str = str(item.get("action") or item.get("service") or "").strip()
            if not service_str or "." not in service_str:
                continue
            domain, service_name = service_str.split(".", 1)
            service_data = dict(item.get("data") or item.get("service_data") or {})
            target = item.get("target")
            if isinstance(target, dict):
                for t_key, t_val in target.items():
                    if t_key not in service_data:
                        service_data[t_key] = t_val
            for top_key in ("entity_id", "device_id", "area_id", "floor_id", "label_id"):
                if top_key in item and top_key not in service_data:
                    service_data[top_key] = item[top_key]
            await self.hass.services.async_call(
                domain, service_name, service_data, blocking=False
            )

    async def _async_execute_who1_on(self, binding: ExposeBinding) -> None:
        """Execute HA turn-on or custom action for a WHO=1 virtual actuator."""
        binding.rx_action_count += 1
        if binding.turn_on_action:
            await self._async_run_actions(binding.turn_on_action)
            return
        if not binding.entity_id:
            return
        domain = binding.entity_id.split(".", 1)[0]
        state = self.hass.states.get(binding.entity_id)
        if domain == "media_player" and state is not None and state.state == "paused":
            await self.hass.services.async_call(
                "media_player",
                "media_play",
                {"entity_id": binding.entity_id},
                blocking=False,
            )
            return
        target_domain = (
            domain
            if self.hass.services.has_service(domain, "turn_on")
            else "homeassistant"
        )
        await self.hass.services.async_call(
            target_domain,
            "turn_on",
            {"entity_id": binding.entity_id},
            blocking=False,
        )

    async def _async_execute_who1_off(self, binding: ExposeBinding) -> None:
        """Execute HA turn-off or custom action for a WHO=1 virtual actuator."""
        binding.rx_action_count += 1
        if binding.turn_off_action:
            await self._async_run_actions(binding.turn_off_action)
            return
        if not binding.entity_id:
            return
        domain = binding.entity_id.split(".", 1)[0]
        target_domain = (
            domain
            if self.hass.services.has_service(domain, "turn_off")
            else "homeassistant"
        )
        await self.hass.services.async_call(
            target_domain,
            "turn_off",
            {"entity_id": binding.entity_id},
            blocking=False,
        )

    async def _async_execute_who1_step(
        self, binding: ExposeBinding, *, up: bool
    ) -> None:
        """Execute HA step-up/step-down or custom action for a WHO=1 virtual dimmer."""
        binding.rx_action_count += 1
        custom = binding.step_up_action if up else binding.step_down_action
        if custom:
            await self._async_run_actions(custom)
            return
        if not binding.entity_id:
            return
        domain = binding.entity_id.split(".", 1)[0]
        if domain == "light":
            delta = (
                binding.brightness_step_pct if up else -binding.brightness_step_pct
            )
            await self.hass.services.async_call(
                "light",
                "turn_on",
                {
                    "entity_id": binding.entity_id,
                    "brightness_step_pct": delta,
                },
                blocking=False,
            )
        elif domain == "media_player":
            await self.hass.services.async_call(
                "media_player",
                "volume_up" if up else "volume_down",
                {"entity_id": binding.entity_id},
                blocking=False,
            )
        elif domain == "cover":
            await self.hass.services.async_call(
                "cover",
                "open_cover" if up else "close_cover",
                {"entity_id": binding.entity_id},
                blocking=False,
            )

    async def _async_handle_secondary_bus_message(
        self, message: OWNMessage, *, source_mac: str | None = None
    ) -> None:
        """Handle bus frames from a shared-bus secondary gateway for delegated WHOs."""
        who = getattr(message, "who", None)
        delegated_away = getattr(self.gateway, "delegated_away_whos", None)
        if isinstance(delegated_away, (set, frozenset, list, tuple)) and who in delegated_away:
            await self._async_handle_bus_message(message, source_mac=source_mac)

    async def _async_handle_bus_message(
        self, message: OWNMessage, *, source_mac: str | None = None
    ) -> None:
        """Handle incoming bus frames for query replies and bidirectional actuator control."""
        src = source_mac or str(getattr(self.gateway, "mac", None) or "primary")
        if self._is_echo_guarded(message, source_mac=src):
            return

        now = time.monotonic()
        raw_frame = str(message)
        prev_rx = self._last_rx_by_frame.get(raw_frame)
        if prev_rx is not None and prev_rx[0] != src and (now - prev_rx[1]) < 0.5:
            return
        self._last_rx_by_frame[raw_frame] = (src, now)
        if len(self._last_rx_by_frame) > 64:
            self._last_rx_by_frame = {
                k: v for k, v in self._last_rx_by_frame.items() if (now - v[1]) < 2.0
            }

        who = getattr(message, "who", None)
        if who not in EXPOSE_SUPPORTED_WHOS:
            return

        msg_where = expose_message_where(message)
        is_status_req = bool(getattr(message, "is_status_request", False))
        is_dim_req = bool(getattr(message, "is_dimension_request", False))
        is_dim_write = bool(getattr(message, "is_dimension_writing", False))
        dim = getattr(message, "dimension", None)
        eff_what = getattr(message, "effective_what", None)
        allow_scope = (
            not is_status_req
            and not is_dim_req
            and not is_dim_write
            and (
                (who == 1 and eff_what in (0, 1))
                or (who == 2 and eff_what in (0, 1, 2))
            )
        )

        for binding in self.bindings:
            if binding.who != who:
                continue

            # ── 1. WHO=13 Clock Queries (*#13**0##, *#13**1##, *#13**22##) ────
            if who == 13:
                if (
                    binding.respond_to_read
                    and is_dim_req
                    and dim in EXPOSE_CLOCK_DIMENSIONS
                    and (binding.dimension == dim or binding.dimension == 22)
                ):
                    await self._async_send_binding_state(
                        binding,
                        high_priority=True,
                        force=True,
                        is_reply=True,
                        query_dimension=dim,
                    )
                continue

            if not _where_matches_binding(
                who, binding.where, msg_where, allow_scope=allow_scope
            ):
                continue

            # ── 2. Status & Dimension Read Queries (*#WHO*WHERE...##) ─────────
            if is_status_req or is_dim_req:
                if not binding.respond_to_read:
                    continue
                if who == 1 and (is_status_req or dim == 1):
                    await self._async_send_binding_state(
                        binding,
                        high_priority=True,
                        force=True,
                        is_reply=True,
                        query_dimension=dim,
                    )
                elif who == 2 and (is_status_req or dim in (10, 11)):
                    await self._async_send_binding_state(
                        binding,
                        high_priority=True,
                        force=True,
                        is_reply=True,
                        query_dimension=dim,
                    )
                elif who == 4 and (is_status_req or dim == 0):
                    await self._async_send_binding_state(
                        binding,
                        high_priority=True,
                        force=True,
                        is_reply=True,
                        query_dimension=0,
                    )
                elif who == 18 and is_dim_req and dim == binding.dimension:
                    await self._async_send_binding_state(
                        binding,
                        high_priority=True,
                        force=True,
                        is_reply=True,
                        query_dimension=dim,
                    )
                elif who == 18 and is_dim_req and dim == 1200 and binding.dimension == 113:
                    now_m = time.monotonic()
                    remaining = (
                        max(1, min(255, round((binding.stream_expires_at - now_m) / 60.0)))
                        if binding.stream_expires_at > now_m
                        else 0
                    )
                    reply = OWNEnergyCommand.report_streaming_state(
                        binding.where, duration=remaining
                    )
                    binding.sent_count += 1
                    binding.query_reply_count += 1
                    await self._async_dispatch_frames(
                        [reply], high_priority=True, is_confirmation=True
                    )
                continue

            # ── 3. WHO=18 Dimension 1200 Streaming Handshake ──────────────────
            if who == 18 and is_dim_write and dim == 1200:
                if not binding.respond_to_read:
                    continue
                if binding.dimension == 113:
                    duration = getattr(message, "stream_duration", None)
                    if duration is None:
                        vals = getattr(message, "dimension_value", [])
                        duration = int(vals[0]) if vals and str(vals[0]).isdigit() else 0
                    duration = min(max(int(duration), 0), 255)
                    dim_params = getattr(message, "_dimension_param", None) or []
                    energy_type = (
                        int(dim_params[0])
                        if dim_params and str(dim_params[0]).isdigit()
                        else 1
                    )
                    if not 1 <= energy_type <= 4:
                        continue
                    confirm = OWNEnergyCommand.report_streaming_state(
                        binding.where, duration=duration, energy_type=energy_type
                    )
                    await self._async_dispatch_frames(
                        [confirm], high_priority=True, is_confirmation=True
                    )
                    old_stream_cancel = self._stream_timers.pop(binding.key, None)
                    if old_stream_cancel is not None:
                        old_stream_cancel()
                    if duration > 0:
                        binding.stream_expires_at = time.monotonic() + duration * 60.0
                        await self._async_send_binding_state(
                            binding,
                            high_priority=True,
                            force=True,
                            is_reply=True,
                        )
                        b_ref = binding
                        et_ref = energy_type

                        @callback
                        def _on_stream_expired(_now: Any) -> None:
                            self._stream_timers.pop(b_ref.key, None)
                            b_ref.stream_expires_at = 0.0
                            end_frame = OWNEnergyCommand.report_streaming_state(
                                b_ref.where, duration=0, energy_type=et_ref
                            )
                            self.hass.async_create_task(
                                self._async_dispatch_frames(
                                    [end_frame],
                                    high_priority=False,
                                    is_confirmation=True,
                                )
                            )

                        self._stream_timers[binding.key] = async_call_later(
                            self.hass, duration * 60.0, _on_stream_expired
                        )
                    else:
                        binding.stream_expires_at = 0.0
                continue

            # ── 4. Bidirectional Virtual Actuator Commands (WHO=1 & WHO=2) ───
            if not binding.bidirectional:
                continue

            if who == 1:
                if is_dim_write and dim in (1, 4):
                    vals = getattr(message, "dimension_value", [])
                    if vals and str(vals[0]).isdigit() and 0 <= int(vals[0]) <= 200:
                        self._cancel_cooldown_timer(binding.key)
                        raw_lvl = int(vals[0])
                        pct = raw_lvl - 100 if raw_lvl >= 100 else raw_lvl
                        profile = self._effective_profile(1)
                        supports_dim_resp = bool(
                            getattr(
                                profile,
                                "supports_dimension_response_injection",
                                False,
                            )
                        )
                        if supports_dim_resp:
                            confirm_frames: list[OWNMessage] = [
                                OWNLightingCommand.switch_on(binding.where)
                                if pct > 0
                                else OWNLightingCommand.switch_off(binding.where),
                                OWNLightingCommand.report_brightness(
                                    binding.where, pct
                                ),
                            ]
                        elif pct > 0:
                            confirm_frames = [
                                OWNLightingCommand.switch_on(binding.where),
                                OWNLightingCommand.set_brightness(binding.where, pct),
                            ]
                        else:
                            confirm_frames = [
                                OWNLightingCommand.switch_off(binding.where),
                            ]
                        binding.last_sent_value = (pct > 0, pct)
                        binding.last_sent_at = time.monotonic()
                        binding.sent_count += len(confirm_frames)
                        await self._async_dispatch_frames(
                            confirm_frames, high_priority=True, is_confirmation=True
                        )
                        if binding.entity_id and binding.entity_id.startswith("light."):
                            binding.rx_action_count += 1
                            await self.hass.services.async_call(
                                "light",
                                "turn_on" if pct > 0 else "turn_off",
                                (
                                    {"entity_id": binding.entity_id, "brightness_pct": pct}
                                    if pct > 0
                                    else {"entity_id": binding.entity_id}
                                ),
                                blocking=False,
                            )
                    continue

                if eff_what == 1:
                    self._cancel_cooldown_timer(binding.key)
                    confirm = OWNLightingCommand.switch_on(binding.where)
                    binding.last_sent_value = (True, None)
                    binding.last_sent_at = time.monotonic()
                    binding.sent_count += 1
                    await self._async_dispatch_frames(
                        [confirm], high_priority=True, is_confirmation=True
                    )
                    await self._async_execute_who1_on(binding)
                elif eff_what == 0:
                    self._cancel_cooldown_timer(binding.key)
                    confirm = OWNLightingCommand.switch_off(binding.where)
                    binding.last_sent_value = (
                        False,
                        0
                        if (binding.attribute == "brightness" or binding.dimension == 1)
                        else None,
                    )
                    binding.last_sent_at = time.monotonic()
                    binding.sent_count += 1
                    await self._async_dispatch_frames(
                        [confirm], high_priority=True, is_confirmation=True
                    )
                    await self._async_execute_who1_off(binding)
                elif eff_what == 30:
                    await self._async_execute_who1_step(binding, up=True)
                elif eff_what == 31:
                    await self._async_execute_who1_step(binding, up=False)
                elif isinstance(eff_what, int) and 2 <= eff_what <= 10:
                    self._cancel_cooldown_timer(binding.key)
                    pct = eff_what * 10
                    confirm = OWNLightingCommand.switch_on(binding.where)
                    binding.last_sent_value = (True, pct)
                    binding.last_sent_at = time.monotonic()
                    binding.sent_count += 1
                    await self._async_dispatch_frames(
                        [confirm], high_priority=True, is_confirmation=True
                    )
                    if binding.entity_id and binding.entity_id.startswith("light."):
                        binding.rx_action_count += 1
                        await self.hass.services.async_call(
                            "light",
                            "turn_on",
                            {"entity_id": binding.entity_id, "brightness_pct": pct},
                            blocking=False,
                        )
                    else:
                        await self._async_execute_who1_on(binding)

            elif who == 2:
                reports_pos = (
                    binding.attribute == "current_position"
                    or binding.dimension in (10, 11)
                )
                if is_dim_write and dim in (10, 11):
                    vals = getattr(message, "dimension_value", [])
                    raw_pct_val = vals[1] if len(vals) >= 2 else (vals[0] if vals else None)
                    if (
                        raw_pct_val is not None
                        and str(raw_pct_val).isdigit()
                        and 0 <= int(raw_pct_val) <= 100
                    ):
                        self._cancel_cooldown_timer(binding.key)
                        pct = int(raw_pct_val)
                        profile = self._effective_profile(2)
                        supports_dim_resp = bool(
                            getattr(
                                profile,
                                "supports_dimension_response_injection",
                                False,
                            )
                        )
                        confirm = (
                            OWNAutomationCommand.report_shutter_level(
                                binding.where, pct, state=10
                            )
                            if supports_dim_resp
                            else OWNAutomationCommand.set_shutter_level(
                                binding.where, pct
                            )
                        )
                        stop_cmd = OWNAutomationCommand.stop_shutter(binding.where)
                        binding.last_sent_value = (
                            str(stop_cmd),
                            pct if reports_pos else None,
                        )
                        binding.last_sent_at = time.monotonic()
                        binding.sent_count += 1
                        await self._async_dispatch_frames(
                            [confirm], high_priority=True, is_confirmation=True
                        )
                        if binding.entity_id and binding.entity_id.startswith("cover."):
                            binding.rx_action_count += 1
                            await self.hass.services.async_call(
                                "cover",
                                "set_cover_position",
                                {"entity_id": binding.entity_id, "position": pct},
                                blocking=False,
                            )
                    continue

                cur_state_obj = (
                    self.hass.states.get(binding.entity_id)
                    if binding.entity_id
                    else None
                )
                cur_pos: int | None = (
                    _parse_position_pct(cur_state_obj.attributes.get("current_position"))
                    if reports_pos and cur_state_obj is not None
                    else None
                )

                if eff_what == 1:
                    self._cancel_cooldown_timer(binding.key)
                    confirm = OWNAutomationCommand.raise_shutter(binding.where)
                    binding.last_sent_value = (str(confirm), cur_pos)
                    binding.last_sent_at = time.monotonic()
                    binding.sent_count += 1
                    await self._async_dispatch_frames(
                        [confirm], high_priority=True, is_confirmation=True
                    )
                    binding.rx_action_count += 1
                    if binding.turn_on_action:
                        await self._async_run_actions(binding.turn_on_action)
                    elif binding.entity_id:
                        domain = binding.entity_id.split(".", 1)[0]
                        await self.hass.services.async_call(
                            "cover" if domain == "cover" else "homeassistant",
                            "open_cover" if domain == "cover" else "turn_on",
                            {"entity_id": binding.entity_id},
                            blocking=False,
                        )
                elif eff_what == 2:
                    self._cancel_cooldown_timer(binding.key)
                    confirm = OWNAutomationCommand.lower_shutter(binding.where)
                    binding.last_sent_value = (str(confirm), cur_pos)
                    binding.last_sent_at = time.monotonic()
                    binding.sent_count += 1
                    await self._async_dispatch_frames(
                        [confirm], high_priority=True, is_confirmation=True
                    )
                    binding.rx_action_count += 1
                    if binding.turn_off_action:
                        await self._async_run_actions(binding.turn_off_action)
                    elif binding.entity_id:
                        domain = binding.entity_id.split(".", 1)[0]
                        await self.hass.services.async_call(
                            "cover" if domain == "cover" else "homeassistant",
                            "close_cover" if domain == "cover" else "turn_off",
                            {"entity_id": binding.entity_id},
                            blocking=False,
                        )
                elif eff_what == 0:
                    self._cancel_cooldown_timer(binding.key)
                    confirm = OWNAutomationCommand.stop_shutter(binding.where)
                    binding.last_sent_value = (str(confirm), cur_pos)
                    binding.last_sent_at = time.monotonic()
                    binding.sent_count += 1
                    await self._async_dispatch_frames(
                        [confirm], high_priority=True, is_confirmation=True
                    )
                    if binding.entity_id and binding.entity_id.startswith("cover."):
                        binding.rx_action_count += 1
                        await self.hass.services.async_call(
                            "cover",
                            "stop_cover",
                            {"entity_id": binding.entity_id},
                            blocking=False,
                        )

    handle_bus_message = _async_handle_bus_message

    def as_diagnostics(self) -> list[dict[str, Any]]:
        """Return diagnostics list for all configured bindings."""
        return [b.as_diagnostics_dict(self.hass) for b in self.bindings]

