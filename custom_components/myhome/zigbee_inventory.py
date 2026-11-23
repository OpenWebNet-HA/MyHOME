"""Cached OpenZigBee product inventory, event-stream enrichment, and missing-echo watchdog."""
from __future__ import annotations

import asyncio
import time
from collections.abc import Callable, Coroutine
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, TypedDict

from homeassistant.const import CONF_MAC
from homeassistant.core import CALLBACK_TYPE, HomeAssistant, callback
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.dispatcher import async_dispatcher_connect, async_dispatcher_send
from homeassistant.helpers.event import async_call_later
from homeassistant.helpers.storage import Store
from homeassistant.util import dt as dt_util
from OWNd.message import (
    OWNAutomationCommand,
    OWNCommand,
    OWNGatewayCommand,
    OWNGatewayEvent,
    OWNLightingCommand,
)

from .const import (
    CONF_DEVICE_MODEL,
    CONF_DIMMABLE,
    CONF_EXPLICIT_KINEMATICS,
    CONF_LOCK_FEATURES,
    CONF_MANUFACTURER,
    CONF_WHERE,
    DOMAIN,
    LOGGER,
)
from .where_grammar import effective_message_where, parse_zigbee_where

if TYPE_CHECKING:
    from .gateway import MyHOMEGatewayHandler

__all__ = [
    "CONTROL_TIMEOUT",
    "DIAGNOSTIC_PROBE_FRAMES",
    "DIAGNOSTIC_PROBE_TIMEOUT",
    "PRODUCT_INFO_TIMEOUT",
    "SCAN_PRODUCT_COUNT_TIMEOUT",
    "STORAGE_KEY_PREFIX",
    "STORAGE_VERSION",
    "SUPERVISOR_MIN_INTERVAL_S",
    "UNIT_POLL_TIMEOUT",
    "ZIGBEE_DIMMER_DEV_IDS",
    "ZIGBEE_ECHO_WATCHDOG_S",
    "ZIGBEE_WHO1_ACTUATOR_DEV_IDS",
    "ZIGBEE_WHO2_ACTUATOR_DEV_IDS",
    "ZigbeeInventoryData",
    "ZigbeeInventoryManager",
    "ZigbeeProductRecord",
    "ZigbeeUnitRecord",
    "dev_id_to_who",
    "zigbee_inventory_store",
    "zigbee_inventory_store_key",
]

STORAGE_VERSION = 1
STORAGE_KEY_PREFIX = f"{DOMAIN}.zigbee_inventory"

SCAN_PRODUCT_COUNT_TIMEOUT = 15.0
PRODUCT_INFO_TIMEOUT = 35.0
UNIT_POLL_TIMEOUT = 6.0
CONTROL_TIMEOUT = 10.0
DIAGNOSTIC_PROBE_TIMEOUT = 5.0
ZIGBEE_ECHO_WATCHDOG_S = 4.5
SUPERVISOR_MIN_INTERVAL_S = 1.0
_SUPERVISOR_DEBOUNCE_S = 0.2
_TIER_OTHERS = 3
_SAVE_DELAY = 1.0

DIAGNOSTIC_PROBE_FRAMES: tuple[str, ...] = (
    "*#13**16##",
    "*#13**15##",
    "*#13**17##",
    "*#13**26##",
    "*#13**71##",
    "*#13**73#0##",
)

# OpenZigBee DEV_ID actuator mapping per openwebnet4j OpenDeviceType (EPL-2.0) and
# Encyclopedia Machine KB ownkb:claim:c001851, c001954. Controls/sensors stay who=None.
ZIGBEE_WHO1_ACTUATOR_DEV_IDS: frozenset[int] = frozenset(
    {256, 258, 259, 261, 263, 264, 265, 269, 275}
)
ZIGBEE_DIMMER_DEV_IDS: frozenset[int] = frozenset({258, 263, 265})
ZIGBEE_WHO2_ACTUATOR_DEV_IDS: frozenset[int] = frozenset({513, 515})


def dev_id_to_who(dev_id: int | None) -> int | None:
    """Map an OpenZigBee ``DEV_ID`` to its actuator WHO (1 or 2), or ``None`` for controls."""
    if dev_id in ZIGBEE_WHO1_ACTUATOR_DEV_IDS:
        return 1
    if dev_id in ZIGBEE_WHO2_ACTUATOR_DEV_IDS:
        return 2
    return None


class ZigbeeUnitRecord(TypedDict):
    """Persisted OpenZigBee endpoint unit record."""

    unit: str
    where: str
    who: int | None
    dev_id: int
    model: str


class ZigbeeProductRecord(TypedDict):
    """Persisted OpenZigBee physical product node record."""

    addr: str
    index: int
    reachable: bool
    last_seen: str | None
    units: dict[str, ZigbeeUnitRecord]


class ZigbeeInventoryData(TypedDict):
    """Persisted OpenZigBee inventory file schema."""

    eui64: str
    firmware: str | None
    last_scan_at: str | None
    product_count: int
    products: dict[str, ZigbeeProductRecord]


@dataclass(frozen=True)
class _TierSendOutcome:
    """Result of a single lowest-tier command dispatch."""

    written_ok: bool
    timed_out: bool
    nacked: bool
    busy: bool
    responses: list[Any]


def zigbee_inventory_store_key(eui64: str) -> str:
    """Return the NTFS-safe ``.storage`` key for a ZigBee gateway EUI-64."""
    cleaned = str(eui64).lower().replace(":", "").replace("-", "").strip()
    return f"{STORAGE_KEY_PREFIX}.{cleaned}"


def zigbee_inventory_store(
    hass: HomeAssistant, eui64: str
) -> Store[ZigbeeInventoryData]:
    """Return the Home Assistant ``Store`` backing a ZigBee gateway's inventory."""
    return Store(hass, STORAGE_VERSION, zigbee_inventory_store_key(eui64))


class ZigbeeInventoryManager:
    """Manages cached OpenZigBee device inventory, live DIM 66 enrichment, and echo watchdogs."""

    def __init__(
        self,
        handler: MyHOMEGatewayHandler,
        *,
        store: Store[ZigbeeInventoryData] | None = None,
    ) -> None:
        """Initialize the ZigBee inventory manager for ``handler``."""
        self.handler = handler
        raw_eui = (
            handler.mac
            or str(getattr(handler.config_entry, "data", {}).get(CONF_MAC, ""))
        )
        self._eui64 = str(raw_eui).lower()
        self._store: Store[ZigbeeInventoryData] = (
            store
            if store is not None
            else zigbee_inventory_store(handler.hass, self._eui64)
        )
        self._data: ZigbeeInventoryData = {
            "eui64": self._eui64,
            "firmware": handler.firmware,
            "last_scan_at": None,
            "product_count": 0,
            "products": {},
        }
        self._scan_lock = asyncio.Lock()
        self._refresh_task: asyncio.Task[None] | None = None
        self._unsub_bus: Callable[[], None] | None = None
        self._echo_timers: dict[str, CALLBACK_TYPE] = {}
        self._supervisor_timer: CALLBACK_TYPE | None = None
        self._bg_tasks: set[asyncio.Task[None]] = set()
        self._enriching_addrs: set[str] = set()
        self._pending_unit_polls: list[tuple[int, str]] = []
        self._drain_polls_active = False
        self._last_rx_monotonic: dict[str, float] = {}
        self._node_rx_seq: dict[str, int] = {}
        self._last_supervisor_at: float = -SUPERVISOR_MIN_INTERVAL_S
        self._ensure_subscribed()

    def _ensure_subscribed(self) -> None:
        """Subscribe once to ``myhome_message_{mac}`` when a Home Assistant instance is attached."""
        if self._unsub_bus is not None:
            return
        hass = getattr(self.handler, "hass", None)
        if not isinstance(hass, HomeAssistant):
            return
        self._unsub_bus = async_dispatcher_connect(
            hass,
            f"myhome_message_{self.handler.mac}",
            self._on_bus_message,
        )

    def _spawn_bg_task(
        self, coro: Coroutine[Any, Any, None], *, name: str
    ) -> asyncio.Task[None] | None:
        """Spawn and track a background task on ``handler.hass``."""
        hass = getattr(self.handler, "hass", None)
        if not isinstance(hass, HomeAssistant):
            coro.close()
            return None
        task: asyncio.Task[None] = hass.async_create_background_task(coro, name=name)
        self._bg_tasks.add(task)
        task.add_done_callback(self._bg_tasks.discard)
        return task

    @property
    def data(self) -> ZigbeeInventoryData:
        """Return the current in-memory inventory data."""
        return self._data

    @property
    def products(self) -> dict[str, ZigbeeProductRecord]:
        """Return cached product records keyed by node address."""
        return self._data["products"]

    async def async_load(self) -> ZigbeeInventoryData:
        """Hydrate cached ZigBee inventory from ``.storage`` with zero RF traffic."""
        self._ensure_subscribed()
        loaded: Any = await self._store.async_load()
        if isinstance(loaded, dict):
            raw_products = loaded.get("products")
            products: dict[str, ZigbeeProductRecord] = {}
            if isinstance(raw_products, dict):
                for addr, rec in raw_products.items():
                    if not isinstance(rec, dict):
                        continue
                    raw_units = rec.get("units")
                    units: dict[str, ZigbeeUnitRecord] = {}
                    if isinstance(raw_units, dict):
                        for unit_key, unit_val in raw_units.items():
                            if isinstance(unit_val, dict) and isinstance(
                                unit_val.get("where"), str
                            ):
                                units[str(unit_key)] = {
                                    "unit": str(unit_val.get("unit") or unit_key),
                                    "where": unit_val["where"],
                                    "who": unit_val.get("who"),
                                    "dev_id": int(unit_val.get("dev_id") or 0),
                                    "model": str(unit_val.get("model") or ""),
                                }
                    products[str(addr)] = {
                        "addr": str(rec.get("addr") or addr),
                        "index": int(rec.get("index") or 0),
                        "reachable": bool(rec.get("reachable", True)),
                        "last_seen": (
                            str(rec["last_seen"])
                            if rec.get("last_seen") is not None
                            else None
                        ),
                        "units": units,
                    }
            self._data = {
                "eui64": str(loaded.get("eui64") or self._eui64),
                "firmware": (
                    str(loaded["firmware"])
                    if loaded.get("firmware") is not None
                    else self.handler.firmware
                ),
                "last_scan_at": (
                    str(loaded["last_scan_at"])
                    if loaded.get("last_scan_at") is not None
                    else None
                ),
                "product_count": int(loaded.get("product_count") or 0),
                "products": products,
            }
        return self._data

    def _schedule_save(self) -> None:
        self._store.async_delay_save(lambda: self._data, _SAVE_DELAY)

    def _notify_availability(self) -> None:
        hass = getattr(self.handler, "hass", None)
        if not isinstance(hass, HomeAssistant):
            return
        sig = getattr(
            self.handler,
            "availability_signal",
            f"myhome_{self.handler.mac}_availability",
        )
        if isinstance(sig, str) and sig:
            async_dispatcher_send(hass, sig)

    def _cancel_echo_watchdog(self, addr: str) -> None:
        timer = self._echo_timers.pop(addr, None)
        if timer is not None:
            timer()

    def get_unit_record(self, where: str) -> ZigbeeUnitRecord | None:
        """Return the cached unit record for ZigBee ``where``, or ``None``."""
        parsed = parse_zigbee_where(where, address_family="zigbee")
        if parsed is None:
            return None
        product = self._data["products"].get(parsed[0])
        if product is None:
            return None
        return product["units"].get(parsed[1])

    def is_dimmer_where(self, where: str) -> bool:
        """Return True when ``where`` is known in inventory as a ZigBee dimmer actuator."""
        rec = self.get_unit_record(where)
        return rec is not None and rec.get("dev_id") in ZIGBEE_DIMMER_DEV_IDS

    def is_non_actuator_where(self, where: str) -> bool:
        """Return True when ``where`` is known in inventory as a non-actuator control/sensor."""
        rec = self.get_unit_record(where)
        return rec is not None and rec.get("who") is None and rec.get("dev_id", 0) > 0

    def get_node_model(self, addr: str) -> str | None:
        """Return the cached human-readable model label for ZigBee node ``addr``."""
        product = self._data["products"].get(str(addr))
        if not product:
            return None
        for unit_rec in product["units"].values():
            model = unit_rec.get("model")
            if model:
                return model
        return None

    def is_reachable(self, addr: str) -> bool:
        """Return whether node ``addr`` is currently marked reachable."""
        product = self._data["products"].get(str(addr))
        return bool(product.get("reachable", False)) if product else False

    def mark_reachable(self, addr: str) -> None:
        """Mark node ``addr`` reachable upon receiving a live RF frame."""
        addr_str = str(addr)
        self._last_rx_monotonic[addr_str] = time.monotonic()
        self._node_rx_seq[addr_str] = self._node_rx_seq.get(addr_str, 0) + 1
        self._cancel_echo_watchdog(addr_str)
        product = self._data["products"].get(addr_str)
        if product is None:
            return
        was_reachable = bool(product.get("reachable"))
        product["reachable"] = True
        product["last_seen"] = dt_util.utcnow().isoformat()
        if not was_reachable:
            self._schedule_save()
            self._notify_availability()

    def mark_unreachable(self, addr: str) -> None:
        """Mark node ``addr`` unreachable and notify subscribed entities."""
        addr_str = str(addr)
        self._cancel_echo_watchdog(addr_str)
        product = self._data["products"].get(addr_str)
        if product is None:
            return
        was_reachable = bool(product.get("reachable"))
        product["reachable"] = False
        if was_reachable:
            self._schedule_save()
            self._notify_availability()

    def reachable_light_wheres(self) -> list[str]:
        """Return sorted unicast WHO=1 WHERE addresses for reachable ZigBee nodes."""
        wheres: list[str] = []
        for product in self._data["products"].values():
            if not product.get("reachable"):
                continue
            for unit_rec in product["units"].values():
                if unit_rec.get("who") == 1:
                    wheres.append(unit_rec["where"])
        return sorted(wheres)

    def seed_configured_platforms(
        self, configured_platforms: dict[str, dict[str, dict[str, Any]]]
    ) -> None:
        """Seed or enrich WHO 1/2 actuator units into ``configured_platforms``."""
        for product in self._data["products"].values():
            for unit_rec in product["units"].values():
                who = unit_rec.get("who")
                where = unit_rec["where"]
                if who == 1:
                    platform = "light"
                elif who == 2:
                    platform = "cover"
                else:
                    continue
                plat_dict = configured_platforms.setdefault(platform, {})
                is_dimmer = (
                    platform == "light"
                    and unit_rec.get("dev_id") in ZIGBEE_DIMMER_DEV_IDS
                )
                if where in plat_dict:
                    existing = plat_dict[where]
                    if not existing.get(CONF_DEVICE_MODEL) and unit_rec.get("model"):
                        existing[CONF_DEVICE_MODEL] = unit_rec["model"]
                    if not existing.get(CONF_MANUFACTURER):
                        existing[CONF_MANUFACTURER] = "Legrand / BTicino"
                    if is_dimmer and not existing.get(CONF_LOCK_FEATURES):
                        existing[CONF_DIMMABLE] = True
                    continue
                cfg: dict[str, Any] = {
                    CONF_WHERE: where,
                    CONF_DEVICE_MODEL: unit_rec.get("model") or None,
                    CONF_MANUFACTURER: "Legrand / BTicino",
                }
                if is_dimmer:
                    cfg[CONF_DIMMABLE] = True
                if platform == "cover":
                    cfg[CONF_EXPLICIT_KINEMATICS] = False
                plat_dict[where] = cfg

    def get_summary(self) -> dict[str, Any]:
        """Return a redacted diagnostic summary of the ZigBee inventory."""
        products_summary: dict[str, dict[str, Any]] = {}
        reachable_count = 0
        unreachable_count = 0
        for addr, product in sorted(self._data["products"].items()):
            reachable = bool(product.get("reachable"))
            if reachable:
                reachable_count += 1
            else:
                unreachable_count += 1
            first_unit = next(iter(product["units"].values()), None)
            products_summary[addr] = {
                "index": product["index"],
                "reachable": reachable,
                "last_seen": product["last_seen"],
                "unit_count": len(product["units"]),
                "dev_id": first_unit["dev_id"] if first_unit else None,
                "model": first_unit["model"] if first_unit else None,
            }
        return {
            "product_count": self._data["product_count"],
            "reachable_count": reachable_count,
            "unreachable_count": unreachable_count,
            "last_scan_at": self._data["last_scan_at"],
            "products": products_summary,
        }

    def schedule_background_refresh(self) -> asyncio.Task[None] | None:
        """Spawn a lowest-tier background scan if one is not already active."""
        self._ensure_subscribed()
        if self._refresh_task is not None and not self._refresh_task.done():
            return self._refresh_task
        hass = getattr(self.handler, "hass", None)
        if hass is None or not hasattr(hass, "async_create_background_task"):
            return None
        self._refresh_task = hass.async_create_background_task(
            self.async_run_scan(include_diagnostic_probes=False),
            name=f"myhome_{self._eui64}_zigbee_refresh",
        )
        return self._refresh_task

    def cancel(self) -> None:
        """Cancel any active ZigBee scan, timers, tasks, and bus subscription."""
        if self._refresh_task is not None and not self._refresh_task.done():
            self._refresh_task.cancel()
        self._refresh_task = None
        if self._unsub_bus is not None:
            self._unsub_bus()
            self._unsub_bus = None
        for unsub_timer in list(self._echo_timers.values()):
            unsub_timer()
        self._echo_timers.clear()
        if self._supervisor_timer is not None:
            self._supervisor_timer()
            self._supervisor_timer = None
        for task in tuple(self._bg_tasks):
            if not task.done():
                task.cancel()
        self._bg_tasks.clear()
        self._enriching_addrs.clear()
        self._pending_unit_polls.clear()

    async def _send_lowest_tier(
        self, cmd: OWNCommand, *, timeout: float = PRODUCT_INFO_TIMEOUT
    ) -> _TierSendOutcome:
        """Yield while Tier-0 commands are queued, then enqueue ``cmd`` at ``_TIER_OTHERS``."""
        send_buffer = self.handler.send_buffer
        has_tier0 = getattr(send_buffer, "has_tier0_commands", None)
        while callable(has_tier0) and has_tier0():
            await asyncio.sleep(0.1)

        loop = asyncio.get_running_loop()
        written: asyncio.Future[float] = loop.create_future()
        envelope: dict[str, Any] = {
            "message": cmd,
            "is_status_request": True,
            "tier_override": _TIER_OTHERS,
            "timeout": timeout,
            "written": written,
        }
        await send_buffer.put(envelope)

        done, _ = await asyncio.wait({written}, timeout=timeout + 15.0)
        if not done and not written.done():
            written.cancel()

        timed_out = getattr(written, "_command_timeout", False) is True or not done
        nacked = getattr(written, "_gateway_nack", False) is True
        busy = getattr(written, "_gateway_busy", False) is True
        collected = getattr(written, "_collected_responses", None)
        responses = list(collected) if isinstance(collected, list) else []
        written_ok = written in done and not written.cancelled()
        return _TierSendOutcome(
            written_ok=written_ok,
            timed_out=timed_out,
            nacked=nacked,
            busy=busy,
            responses=responses,
        )

    def _mark_index_unreachable(self, index: int) -> None:
        for addr, product in list(self._data["products"].items()):
            if product.get("index") == index:
                self.mark_unreachable(addr)

    def _update_device_registry_model(self, addr: str, model: str) -> None:
        entry = getattr(self.handler, "config_entry", None)
        entry_id = getattr(entry, "entry_id", None)
        if not isinstance(entry_id, str) or not entry_id:
            return
        dev_reg = dr.async_get(self.handler.hass)
        target_ident = (DOMAIN, f"{self.handler.mac}-zigbee-{addr}")
        for dev in dr.async_entries_for_config_entry(dev_reg, entry_id):
            if target_ident in dev.identifiers and (
                dev.model != model or dev.manufacturer != "Legrand / BTicino"
            ):
                dev_reg.async_update_device(
                    dev.id,
                    model=model,
                    manufacturer="Legrand / BTicino",
                )

    def _prune_non_actuator_entities(self, zb_where: str) -> None:
        """Remove stale auto-discovered light/cover entities once DIM 66 identifies a control."""
        entry = getattr(self.handler, "config_entry", None)
        entry_id = getattr(entry, "entry_id", None)
        hass = getattr(self.handler, "hass", None)
        if not isinstance(hass, HomeAssistant) or not isinstance(entry_id, str) or not entry_id:
            return
        from .discovery import prune_entity

        runtime = getattr(entry, "runtime_data", None)
        platforms = getattr(runtime, "platforms", None)
        ent_reg = er.async_get(hass)
        for domain, who_str in (("light", "1"), ("cover", "2")):
            if isinstance(platforms, dict) and zb_where in platforms.get(domain, {}):
                continue
            uid = f"{self.handler.mac}-{who_str}-{zb_where}"
            if (
                (entity_id := ent_reg.async_get_entity_id(domain, DOMAIN, uid)) is not None
                and (reg_entry := ent_reg.async_get(entity_id)) is not None
            ):
                prune_entity(hass, ent_reg, reg_entry, entry_id)

    async def _restore_supervisor(self) -> None:
        """Restore OpenZigBee supervisor mode (``*13*66*##``, KB ``c001825``)."""
        if not getattr(self.handler, "is_connected", False):
            return
        self._last_supervisor_at = time.monotonic()
        try:
            await self._send_lowest_tier(
                OWNGatewayCommand.enable_supervisor(), timeout=CONTROL_TIMEOUT
            )
        except Exception as err:  # noqa: BLE001 - best-effort supervisor restore in finally
            LOGGER.debug(
                "%s Failed to restore ZigBee supervisor mode: %s",
                self.handler.log_id,
                err,
            )

    def _schedule_supervisor_restore(self) -> None:
        """Schedule a debounced, rate-limited (<= 1/s) ``*13*66*##`` Supervisor restore."""
        if self._supervisor_timer is not None:
            return
        hass = getattr(self.handler, "hass", None)
        if not isinstance(hass, HomeAssistant):
            return
        elapsed = time.monotonic() - self._last_supervisor_at
        delay = max(_SUPERVISOR_DEBOUNCE_S, SUPERVISOR_MIN_INTERVAL_S - elapsed)

        @callback
        def _fire(_now: Any) -> None:
            self._supervisor_timer = None
            self._spawn_bg_task(
                self._restore_supervisor(),
                name=f"myhome_{self._eui64}_zigbee_supervisor",
            )

        self._supervisor_timer = async_call_later(hass, delay, _fire)

    @staticmethod
    def _extract_zigbee_addr(message: Any) -> str | None:
        addr = getattr(message, "zigbee_device_id", None)
        if isinstance(addr, str) and addr:
            return addr
        parsed = parse_zigbee_where(effective_message_where(message), address_family="zigbee")
        return parsed[0] if parsed is not None else None

    def _enqueue_unit_poll(self, who: int, zb_where: str) -> None:
        pair = (who, zb_where)
        if pair not in self._pending_unit_polls:
            self._pending_unit_polls.append(pair)
        if not self._scan_lock.locked() and not self._drain_polls_active:
            self._spawn_bg_task(
                self._drain_pending_unit_polls(),
                name=f"myhome_{self._eui64}_zigbee_unit_polls",
            )

    async def _drain_pending_unit_polls(self) -> None:
        """Poll queued WHO 1/2 actuator endpoints to trigger discovery and sync state."""
        if self._drain_polls_active:
            return
        self._drain_polls_active = True
        try:
            while self._pending_unit_polls:
                who, where = self._pending_unit_polls.pop(0)
                parsed = parse_zigbee_where(where, address_family="zigbee")
                if parsed is not None and not self.is_reachable(parsed[0]):
                    continue
                unit_cmd: OWNCommand = (
                    OWNLightingCommand.status(where)
                    if who == 1
                    else OWNAutomationCommand.status(where)
                )
                outcome = await self._send_lowest_tier(
                    unit_cmd, timeout=UNIT_POLL_TIMEOUT
                )
                if outcome.timed_out and parsed is not None:
                    self.mark_unreachable(parsed[0])
        finally:
            self._drain_polls_active = False

    def _ingest_dim66_event(
        self,
        ev: OWNGatewayEvent,
        *,
        fallback_index: int | None = None,
        now_iso: str | None = None,
    ) -> bool:
        """Ingest one online or offline Dimension 66 ``OWNGatewayEvent``."""
        if getattr(ev, "dimension", None) != 66:
            return False
        ev_idx = getattr(ev, "zigbee_product_index", None)
        index = (
            ev_idx
            if isinstance(ev_idx, int) and not isinstance(ev_idx, bool)
            else fallback_index
        )
        if getattr(ev, "is_zigbee_product_unreachable", False):
            addr = self._extract_zigbee_addr(ev)
            if isinstance(addr, str) and addr:
                existing = self._data["products"].get(addr)
                if existing is None:
                    self._data["products"][addr] = {
                        "addr": addr,
                        "index": index if index is not None else 0,
                        "reachable": False,
                        "last_seen": None,
                        "units": {},
                    }
                    self._schedule_save()
                else:
                    if index is not None:
                        existing["index"] = index
                    self.mark_unreachable(addr)
            elif index is not None:
                self._mark_index_unreachable(index)
            return False

        addr = getattr(ev, "zigbee_device_id", None)
        unit = getattr(ev, "zigbee_unit", None)
        zb_where = getattr(ev, "zigbee_where", None)
        if not (
            isinstance(addr, str)
            and addr
            and isinstance(unit, str)
            and unit
            and isinstance(zb_where, str)
            and zb_where
        ):
            eff = effective_message_where(ev)
            parsed_zb = parse_zigbee_where(eff, address_family="zigbee")
            if parsed_zb is None:
                return False
            addr, unit = parsed_zb
            zb_where = f"{addr}{unit}#9"
        dev_id = getattr(ev, "zigbee_device_type_code", None)
        if not isinstance(dev_id, int) or isinstance(dev_id, bool):
            return False

        idx_val = index if index is not None else 0
        ts = now_iso or dt_util.utcnow().isoformat()
        model = getattr(ev, "zigbee_device_type", None) or f"Unknown ({dev_id})"
        who = dev_id_to_who(dev_id)
        was_new_product = addr not in self._data["products"]
        product = self._data["products"].setdefault(
            addr,
            {
                "addr": addr,
                "index": idx_val,
                "reachable": True,
                "last_seen": ts,
                "units": {},
            },
        )
        was_reachable = bool(product.get("reachable", False)) and not was_new_product
        prev_unit = product["units"].get(unit)
        new_unit: ZigbeeUnitRecord = {
            "unit": unit,
            "where": zb_where,
            "who": who,
            "dev_id": dev_id,
            "model": model,
        }
        product["index"] = idx_val
        product["reachable"] = True
        product["last_seen"] = ts
        product["units"][unit] = new_unit

        runtime = getattr(getattr(self.handler, "config_entry", None), "runtime_data", None)
        platforms = getattr(runtime, "platforms", None)
        if isinstance(platforms, dict):
            self.seed_configured_platforms(platforms)

        self._update_device_registry_model(addr, model)
        if who is None:
            self._prune_non_actuator_entities(zb_where)

        if prev_unit != new_unit or not was_reachable:
            self._schedule_save()
            self._notify_availability()
        if prev_unit is None and who in (1, 2):
            self._enqueue_unit_poll(who, zb_where)
        return True

    def _ingest_dim66_frames(
        self, index: int, responses: list[Any], now_iso: str
    ) -> bool:
        """Process collected Dimension 66 frames for product ``index``."""
        updated = False
        for ev in responses:
            if not isinstance(ev, OWNGatewayEvent) or getattr(ev, "dimension", None) != 66:
                continue
            if self._ingest_dim66_event(ev, fallback_index=index, now_iso=now_iso):
                updated = True
        return updated

    def _schedule_passive_enrich(self, addr: str, index: int) -> None:
        """Passively query ``*#13**66*<index>##`` for an online node with empty cached units."""
        if addr in self._enriching_addrs or self._scan_lock.locked():
            return
        self._enriching_addrs.add(addr)
        self._spawn_bg_task(
            self._async_passive_enrich(addr, index),
            name=f"myhome_{self._eui64}_zigbee_enrich_{addr}",
        )

    async def _async_passive_enrich(self, addr: str, index: int) -> None:
        try:
            now_iso = dt_util.utcnow().isoformat()
            outcome = await self._send_lowest_tier(
                OWNGatewayCommand.get_product_info(index),
                timeout=PRODUCT_INFO_TIMEOUT,
            )
            if outcome.responses:
                self._ingest_dim66_frames(index, outcome.responses, now_iso)
        finally:
            self._enriching_addrs.discard(addr)

    async def _async_discover_joined_node(self, addr: str) -> None:
        """Query ``*#13**67##`` and tail ``DIM 66`` indices for a brand-new ``WHAT 33`` node."""
        try:
            old_count = int(self._data.get("product_count") or 0)
            count_outcome = await self._send_lowest_tier(
                OWNGatewayCommand.request_product_count(),
                timeout=CONTROL_TIMEOUT,
            )
            new_count: int | None = None
            for item in count_outcome.responses:
                cnt = getattr(item, "network_product_count", None)
                if isinstance(cnt, int) and not isinstance(cnt, bool):
                    new_count = cnt
                    break
            if new_count is None or new_count <= 0:
                return
            self._data["product_count"] = new_count
            now_iso = dt_util.utcnow().isoformat()
            tail_indices = (
                list(range(old_count, new_count))
                if 0 <= old_count < new_count
                else list(range(new_count))
            )
            fallback_indices = [
                idx for idx in range(new_count) if idx not in tail_indices
            ]
            for idx in [*tail_indices, *fallback_indices]:
                info_outcome = await self._send_lowest_tier(
                    OWNGatewayCommand.get_product_info(idx),
                    timeout=PRODUCT_INFO_TIMEOUT,
                )
                if info_outcome.responses:
                    self._ingest_dim66_frames(idx, info_outcome.responses, now_iso)
                if addr in self._data["products"] and self._data["products"][addr]["units"]:
                    break
        finally:
            self._enriching_addrs.discard(addr)

    def _handle_zigbee_join(self, addr: str | None) -> None:
        """Handle ``WHO 13 WHAT 33`` join announcement (KB ``c001825``)."""
        self._schedule_supervisor_restore()
        if not addr:
            return
        product = self._data["products"].get(addr)
        if product is not None:
            self.mark_reachable(addr)
            if not product["units"]:
                self._schedule_passive_enrich(addr, product["index"])
            else:
                for unit_rec in product["units"].values():
                    who = unit_rec.get("who")
                    if unit_rec["unit"] != "00" and who in (1, 2):
                        self._enqueue_unit_poll(who, unit_rec["where"])
            return
        if addr not in self._enriching_addrs and not self._scan_lock.locked():
            self._enriching_addrs.add(addr)
            self._spawn_bg_task(
                self._async_discover_joined_node(addr),
                name=f"myhome_{self._eui64}_zigbee_join_{addr}",
            )

    @callback
    def _on_bus_message(self, message: Any) -> None:
        """Handle collected, NACKed, and event-stream ZigBee frames on ``myhome_message_{mac}``."""
        if isinstance(message, OWNGatewayEvent):
            dim = getattr(message, "dimension", None)
            what = getattr(message, "what", None)
            if dim == 66:
                self._ingest_dim66_event(message)
                return
            if dim == 67:
                count = getattr(message, "network_product_count", None)
                if isinstance(count, int) and not isinstance(count, bool):
                    self._data["product_count"] = count
                return
            if getattr(message, "is_zigbee_join", False) is True or what == 33:
                self._handle_zigbee_join(self._extract_zigbee_addr(message))
                return
            if getattr(message, "is_zigbee_leave", False) is True or what == 34:
                addr = self._extract_zigbee_addr(message)
                if addr:
                    self.mark_unreachable(addr)
                return

        if getattr(message, "is_zigbee", False) is True and not getattr(
            message, "is_zigbee_product_unreachable", False
        ):
            addr = self._extract_zigbee_addr(message)
            if addr:
                self.mark_reachable(addr)
                product = self._data["products"].get(addr)
                if product is not None and not product["units"]:
                    self._schedule_passive_enrich(addr, product["index"])

    def snapshot_rx_seq(self, message: Any) -> int | None:
        """Return the current inbound frame sequence for ``message``'s ZigBee node."""
        addr = self._extract_zigbee_addr(message)
        return self._node_rx_seq.get(addr, 0) if addr else None

    def note_unicast_command_sent(
        self,
        message: Any,
        written_at: float,
        *,
        rx_seq_before: int | None = None,
    ) -> None:
        """Arm the ``4.5 s`` missing-echo watchdog for unicast WHO 1/2 ZigBee commands."""
        self._ensure_subscribed()
        hass = getattr(self.handler, "hass", None)
        if not isinstance(hass, HomeAssistant):
            return
        who = getattr(message, "who", None)
        if who not in (1, 2):
            return
        if getattr(message, "is_status_request", False) or getattr(
            message, "is_dimension_request", False
        ):
            return
        parsed = parse_zigbee_where(
            effective_message_where(message), address_family="zigbee"
        )
        if parsed is None:
            return
        addr, unit = parsed
        if addr == "0" or unit == "00":
            return
        if rx_seq_before is not None:
            if self._node_rx_seq.get(addr, 0) != rx_seq_before:
                return
        elif self._last_rx_monotonic.get(addr, 0.0) > written_at:
            return

        self._cancel_echo_watchdog(addr)
        zb_where = f"{addr}{unit}#9"

        @callback
        def _on_watchdog_expired(_now: Any) -> None:
            self._echo_timers.pop(addr, None)
            self._spawn_bg_task(
                self._verify_missing_echo(addr, int(who), zb_where),
                name=f"myhome_{self._eui64}_zigbee_watchdog_{addr}",
            )

        self._echo_timers[addr] = async_call_later(
            hass, ZIGBEE_ECHO_WATCHDOG_S, _on_watchdog_expired
        )

    async def _verify_missing_echo(self, addr: str, who: int, zb_where: str) -> None:
        """Probe ``DIM 66`` after a missing command echo; mark unreachable or resync unit."""
        product = self._data["products"].get(addr)
        if product is not None:
            now_iso = dt_util.utcnow().isoformat()
            info_outcome = await self._send_lowest_tier(
                OWNGatewayCommand.get_product_info(product["index"]),
                timeout=UNIT_POLL_TIMEOUT,
            )
            if info_outcome.responses:
                self._ingest_dim66_frames(product["index"], info_outcome.responses, now_iso)
            if (
                info_outcome.nacked
                or info_outcome.timed_out
                or not info_outcome.written_ok
                or any(
                    getattr(r, "is_zigbee_product_unreachable", False)
                    for r in info_outcome.responses
                )
            ):
                self.mark_unreachable(addr)
                return

        poll_cmd: OWNCommand = (
            OWNLightingCommand.status(zb_where)
            if who == 1
            else OWNAutomationCommand.status(zb_where)
        )
        poll_outcome = await self._send_lowest_tier(poll_cmd, timeout=UNIT_POLL_TIMEOUT)
        if poll_outcome.timed_out or poll_outcome.nacked:
            self.mark_unreachable(addr)

    async def async_run_scan(
        self, *, include_diagnostic_probes: bool = False
    ) -> None:
        """Run a yield-aware ZigBee network scan and poll reachable actuator units."""
        self._ensure_subscribed()
        async with self._scan_lock:
            count_event = asyncio.Event()
            product_count_box: list[int] = []

            @callback
            def _on_gateway_message(message: Any) -> None:
                count = getattr(message, "network_product_count", None)
                if (
                    isinstance(message, OWNGatewayEvent)
                    and getattr(message, "dimension", None) == 67
                    and isinstance(count, int)
                    and not isinstance(count, bool)
                ):
                    product_count_box.append(count)
                    count_event.set()

            unsub = async_dispatcher_connect(
                self.handler.hass,
                f"myhome_message_{self.handler.mac}",
                _on_gateway_message,
            )
            product_count: int | None = None
            try:
                scan_outcome = await self._send_lowest_tier(
                    OWNGatewayCommand.scan_network(), timeout=CONTROL_TIMEOUT
                )
                if scan_outcome.written_ok:
                    try:
                        await asyncio.wait_for(
                            count_event.wait(), timeout=SCAN_PRODUCT_COUNT_TIMEOUT
                        )
                    except TimeoutError:
                        pass
                if product_count_box:
                    product_count = product_count_box[-1]
                elif scan_outcome.written_ok or scan_outcome.nacked:
                    count_outcome = await self._send_lowest_tier(
                        OWNGatewayCommand.request_product_count(),
                        timeout=CONTROL_TIMEOUT,
                    )
                    for item in count_outcome.responses:
                        item_count = getattr(item, "network_product_count", None)
                        if (
                            isinstance(item, OWNGatewayEvent)
                            and isinstance(item_count, int)
                            and not isinstance(item_count, bool)
                        ):
                            product_count = item_count
                            break
                    if product_count is None and product_count_box:
                        product_count = product_count_box[-1]

                if product_count is not None:
                    now_iso = dt_util.utcnow().isoformat()
                    self._data["product_count"] = product_count
                    self._data["last_scan_at"] = now_iso
                    self._data["firmware"] = self.handler.firmware
                    for p in range(product_count):
                        info_outcome = await self._send_lowest_tier(
                            OWNGatewayCommand.get_product_info(p),
                            timeout=PRODUCT_INFO_TIMEOUT,
                        )
                        has_live = (
                            self._ingest_dim66_frames(p, info_outcome.responses, now_iso)
                            if info_outcome.responses
                            else False
                        )
                        if (
                            info_outcome.timed_out
                            or info_outcome.nacked
                            or not info_outcome.written_ok
                        ):
                            self._mark_index_unreachable(p)
                            continue
                        if not has_live and not any(
                            prod.get("index") == p and prod.get("reachable")
                            for prod in self._data["products"].values()
                        ):
                            self._mark_index_unreachable(p)
                    self._schedule_save()
            finally:
                unsub()
                await self._restore_supervisor()

            if product_count is not None:
                polled_wheres: set[str] = set()
                for product in list(self._data["products"].values()):
                    if not product.get("reachable"):
                        continue
                    for unit_rec in list(product["units"].values()):
                        who = unit_rec.get("who")
                        where = unit_rec["where"]
                        if who == 1:
                            unit_cmd: OWNCommand = OWNLightingCommand.status(where)
                        elif who == 2:
                            unit_cmd = OWNAutomationCommand.status(where)
                        else:
                            continue
                        polled_wheres.add(where)
                        poll_outcome = await self._send_lowest_tier(
                            unit_cmd, timeout=UNIT_POLL_TIMEOUT
                        )
                        if poll_outcome.timed_out:
                            self.mark_unreachable(product["addr"])
                            break
                if self._pending_unit_polls:
                    self._pending_unit_polls = [
                        item
                        for item in self._pending_unit_polls
                        if item[1] not in polled_wheres
                    ]

            if include_diagnostic_probes:
                for raw_probe in DIAGNOSTIC_PROBE_FRAMES:
                    probe_cmd = OWNCommand.parse(
                        raw_probe, gateway=self.handler.profile
                    )
                    if probe_cmd is None:  # pragma: no cover - static valid frames
                        continue
                    await self._send_lowest_tier(
                        probe_cmd, timeout=DIAGNOSTIC_PROBE_TIMEOUT
                    )

        if self._pending_unit_polls:
            await self._drain_pending_unit_polls()
