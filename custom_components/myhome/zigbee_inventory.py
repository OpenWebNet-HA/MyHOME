"""Cached OpenZigBee product inventory and yield-aware scan scheduler."""
from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, TypedDict

from homeassistant.const import CONF_MAC
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.dispatcher import async_dispatcher_connect
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
    "UNIT_POLL_TIMEOUT",
    "ZIGBEE_DIMMER_DEV_IDS",
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
UNIT_POLL_TIMEOUT = 35.0
CONTROL_TIMEOUT = 10.0
DIAGNOSTIC_PROBE_TIMEOUT = 5.0
_TIER_OTHERS = 3
_SAVE_DELAY = 1.0

DIAGNOSTIC_PROBE_FRAMES: tuple[str, ...] = (
    "*#13**16##",
    "*#13**12##",
    "*#13**15##",
    "*#13**17##",
    "*#13**26##",
    "*#13**71#0##",
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
    """Manages cached OpenZigBee device inventory and yield-aware background scans."""

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
        product = self._data["products"].get(str(addr))
        if product is None:
            return
        was_reachable = bool(product.get("reachable"))
        product["reachable"] = True
        product["last_seen"] = dt_util.utcnow().isoformat()
        if not was_reachable:
            self._schedule_save()

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
        """Seed cached WHO 1/2 actuator units into ``configured_platforms`` before setup."""
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
                if where in plat_dict:
                    continue
                cfg: dict[str, Any] = {
                    CONF_WHERE: where,
                    CONF_DEVICE_MODEL: unit_rec.get("model") or None,
                    CONF_MANUFACTURER: "Legrand / BTicino",
                }
                if platform == "light" and unit_rec.get("dev_id") in ZIGBEE_DIMMER_DEV_IDS:
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
        """Cancel any active background ZigBee refresh task."""
        if self._refresh_task is not None and not self._refresh_task.done():
            self._refresh_task.cancel()
        self._refresh_task = None

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
        for product in self._data["products"].values():
            if product.get("index") == index:
                product["reachable"] = False

    def _update_device_registry_model(self, addr: str, model: str) -> None:
        entry = getattr(self.handler, "config_entry", None)
        entry_id = getattr(entry, "entry_id", None)
        if not isinstance(entry_id, str) or not entry_id:
            return
        dev_reg = dr.async_get(self.handler.hass)
        target_ident = (DOMAIN, f"{self.handler.mac}-zigbee-{addr}")
        for dev in dr.async_entries_for_config_entry(dev_reg, entry_id):
            if target_ident in dev.identifiers and dev.model != model:
                dev_reg.async_update_device(dev.id, model=model)

    async def _restore_supervisor(self) -> None:
        """Restore OpenZigBee supervisor mode (``*13*66*##``, KB ``c001825``)."""
        if not getattr(self.handler, "is_connected", False):
            return
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

    def _ingest_dim66_frames(
        self, index: int, responses: list[Any], now_iso: str
    ) -> bool:
        """Process collected Dimension 66 frames for product ``index``."""
        updated = False
        for ev in responses:
            if not isinstance(ev, OWNGatewayEvent) or getattr(ev, "dimension", None) != 66:
                continue
            if getattr(ev, "is_zigbee_product_unreachable", False):
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
                    continue
                addr, unit = parsed_zb
                zb_where = f"{addr}{unit}#9"
            dev_id = getattr(ev, "zigbee_device_type_code", None)
            if not isinstance(dev_id, int):
                continue
            model = getattr(ev, "zigbee_device_type", None) or f"Unknown ({dev_id})"
            who = dev_id_to_who(dev_id)
            product = self._data["products"].setdefault(
                addr,
                {
                    "addr": addr,
                    "index": index,
                    "reachable": True,
                    "last_seen": now_iso,
                    "units": {},
                },
            )
            product["index"] = index
            product["reachable"] = True
            product["last_seen"] = now_iso
            product["units"][unit] = {
                "unit": unit,
                "where": zb_where,
                "who": who,
                "dev_id": dev_id,
                "model": model,
            }
            self._update_device_registry_model(addr, model)
            updated = True
        return updated

    async def async_run_scan(
        self, *, include_diagnostic_probes: bool = False
    ) -> None:
        """Run a yield-aware ZigBee network scan and poll reachable actuator units."""
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
                        if info_outcome.timed_out or info_outcome.nacked or not info_outcome.written_ok:
                            self._mark_index_unreachable(p)
                            continue
                        if not self._ingest_dim66_frames(p, info_outcome.responses, now_iso):
                            self._mark_index_unreachable(p)
                    self._schedule_save()
            finally:
                unsub()
                await self._restore_supervisor()

            if product_count is not None:
                dirty = False
                for product in self._data["products"].values():
                    if not product.get("reachable"):
                        continue
                    for unit_rec in product["units"].values():
                        who = unit_rec.get("who")
                        where = unit_rec["where"]
                        if who == 1:
                            unit_cmd: OWNCommand = OWNLightingCommand.status(where)
                        elif who == 2:
                            unit_cmd = OWNAutomationCommand.status(where)
                        else:
                            continue
                        poll_outcome = await self._send_lowest_tier(
                            unit_cmd, timeout=UNIT_POLL_TIMEOUT
                        )
                        if poll_outcome.timed_out:
                            product["reachable"] = False
                            dirty = True
                            break
                if dirty:
                    self._schedule_save()

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
