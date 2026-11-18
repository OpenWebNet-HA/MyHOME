"""Tests for ZigbeeInventoryManager, ZigBee resync, services, and __init__ serial lifecycle."""
from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from homeassistant.config_entries import ConfigEntryState
from homeassistant.const import CONF_MAC, CONF_NAME
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from OWNd.message import (
    OWNAutomationCommand,
    OWNGatewayCommand,
    OWNGatewayEvent,
    OWNLightingCommand,
    OWNMessage,
)
from OWNd.profiles import Legrand3578Profile
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.myhome import (
    async_remove_config_entry_device,
    async_remove_entry,
)
from custom_components.myhome.const import (
    CONF_BAUDRATE,
    CONF_DEVICE_MODEL,
    CONF_DIMMABLE,
    CONF_EXPLICIT_KINEMATICS,
    CONF_SERIAL_PORT,
    CONF_WHERE,
    DOMAIN,
)
from custom_components.myhome.gateway import MyHOMEGatewayHandler
from custom_components.myhome.gateway_sessions import _TIER_OTHERS, FrameScheduler
from custom_components.myhome.services import (
    async_register_turn_on_timed_service,
    async_setup_services,
)
from custom_components.myhome.zigbee_inventory import (
    DIAGNOSTIC_PROBE_FRAMES,
    STORAGE_VERSION,
    ZigbeeInventoryManager,
    dev_id_to_who,
    zigbee_inventory_store,
    zigbee_inventory_store_key,
)

TEST_EUI64 = "00:03:50:00:01:02:03:04"
TEST_PORT = "/dev/serial/by-id/usb-BTicino_3578-if00"


class _InMemoryStore:
    """Simple async Store fake for ZigbeeInventoryManager tests."""

    def __init__(self, initial=None) -> None:
        self.payload = initial
        self.delay_saved: list = []

    async def async_load(self):
        return self.payload

    def async_delay_save(self, data_func, delay: float) -> None:
        self.delay_saved.append((data_func(), delay))


def _make_gateway_event(
    *,
    dimension: int,
    product_count: int | None = None,
    device_id: str | None = None,
    unit: str | None = None,
    zb_where: str | None = None,
    dev_id: int | None = None,
    dev_type: str | None = None,
    unreachable: bool = False,
) -> OWNGatewayEvent:
    ev = MagicMock(spec=OWNGatewayEvent)
    ev.dimension = dimension
    ev.network_product_count = product_count
    ev.zigbee_device_id = device_id
    ev.zigbee_unit = unit
    ev.zigbee_where = zb_where
    ev.zigbee_device_type_code = dev_id
    ev.zigbee_device_type = dev_type
    ev.is_zigbee_product_unreachable = unreachable
    return ev


async def test_store_key_and_dev_id_to_who(hass) -> None:
    """Store key strips colons/hyphens for NTFS safety and dev_id maps actuators only."""
    assert zigbee_inventory_store_key("00:03:50-00:01:02:03:04") == (
        "myhome.zigbee_inventory.0003500001020304"
    )
    store = zigbee_inventory_store(hass, TEST_EUI64)
    assert store.version == STORAGE_VERSION
    assert store.key == "myhome.zigbee_inventory.0003500001020304"

    assert dev_id_to_who(256) == 1
    assert dev_id_to_who(258) == 1
    assert dev_id_to_who(513) == 2
    assert dev_id_to_who(1025) is None
    assert dev_id_to_who(None) is None


async def test_inventory_load_seed_reachability_and_summary(hass) -> None:
    """async_load hydrates zero-RF state cleanly, handles bad entries, and seeds platforms."""
    raw_stored = {
        "eui64": TEST_EUI64,
        "firmware": "1.2.3",
        "last_scan_at": "2026-01-01T00:00:00+00:00",
        "product_count": 3,
        "products": {
            "bad-product": "not-a-dict",
            "7001111": {
                "addr": "7001111",
                "index": 0,
                "reachable": True,
                "last_seen": "2026-01-01T00:00:00+00:00",
                "units": {
                    "bad-unit": 123,
                    "01": {
                        "unit": "01",
                        "where": "700111101#9",
                        "who": 1,
                        "dev_id": 258,
                        "model": "Dimmer switch",
                    },
                    "02": {
                        "unit": "02",
                        "where": "700111102#9",
                        "who": 2,
                        "dev_id": 513,
                        "model": "Shutter actuator",
                    },
                },
            },
            "7002222": {
                "addr": "7002222",
                "index": 1,
                "reachable": False,
                "last_seen": None,
                "units": {
                    "01": {
                        "unit": "01",
                        "where": "700222201#9",
                        "who": None,
                        "dev_id": 100,
                        "model": "",
                    }
                },
            },
            "7003333": {
                "addr": "7003333",
                "index": 2,
                "reachable": False,
                "last_seen": None,
                "units": {},
            },
        },
    }
    handler = SimpleNamespace(
        hass=hass,
        mac=TEST_EUI64,
        firmware="1.2.4",
        log_id="[3578]",
        config_entry=SimpleNamespace(data={CONF_MAC: TEST_EUI64}, entry_id="entry-1"),
        send_buffer=FrameScheduler(),
        profile=Legrand3578Profile(),
        is_connected=True,
    )
    store = _InMemoryStore(raw_stored)
    mgr = ZigbeeInventoryManager(handler, store=store)  # type: ignore[arg-type]

    loaded = await mgr.async_load()
    assert loaded["firmware"] == "1.2.3"
    assert set(mgr.products.keys()) == {"7001111", "7002222", "7003333"}
    assert mgr.get_node_model("7001111") == "Dimmer switch"
    assert mgr.get_node_model("7002222") is None
    assert mgr.get_node_model("9999999") is None

    assert mgr.is_reachable("7001111") is True
    assert mgr.is_reachable("7002222") is False
    assert mgr.is_reachable("9999999") is False
    assert mgr.reachable_light_wheres() == ["700111101#9"]

    # mark_reachable on unknown/already-reachable/newly-reachable nodes
    mgr.mark_reachable("9999999")
    mgr.mark_reachable("7001111")
    assert len(store.delay_saved) == 0
    mgr.mark_reachable("7002222")
    assert mgr.is_reachable("7002222") is True
    assert len(store.delay_saved) == 1

    configured: dict[str, dict[str, dict[str, object]]] = {
        "light": {"700999901#9": {CONF_WHERE: "700999901#9"}}
    }
    # Pre-add 700111101#9 duplicate to verify skip path as well
    configured_with_dup = {
        "light": {"700111101#9": {CONF_WHERE: "700111101#9", CONF_DEVICE_MODEL: "Custom"}}
    }
    mgr.seed_configured_platforms(configured_with_dup)
    assert configured_with_dup["light"]["700111101#9"][CONF_DEVICE_MODEL] == "Custom"

    mgr.seed_configured_platforms(configured)
    assert configured["light"]["700111101#9"][CONF_DIMMABLE] is True
    assert configured["cover"]["700111102#9"][CONF_EXPLICIT_KINEMATICS] is False
    assert configured["cover"]["700111102#9"][CONF_DEVICE_MODEL] == "Shutter actuator"

    summary = mgr.get_summary()
    assert summary["product_count"] == 3
    assert summary["reachable_count"] == 2
    assert summary["unreachable_count"] == 1
    assert summary["products"]["7003333"]["unit_count"] == 0
    assert summary["products"]["7003333"]["dev_id"] is None

    # Empty store leaves default firmware fallback.
    mgr_empty = ZigbeeInventoryManager(handler, store=_InMemoryStore(None))  # type: ignore[arg-type]
    empty_data = await mgr_empty.async_load()
    assert empty_data["products"] == {}
    assert empty_data["firmware"] == "1.2.4"


async def test_background_refresh_scheduling_and_cancel(hass) -> None:
    """schedule_background_refresh avoids duplicate tasks and cancel cleans up."""
    handler = SimpleNamespace(
        hass=None,
        mac=TEST_EUI64,
        firmware="1.0.0",
        log_id="[3578]",
        config_entry=SimpleNamespace(data={CONF_MAC: TEST_EUI64}),
        send_buffer=FrameScheduler(),
        profile=Legrand3578Profile(),
    )
    mgr = ZigbeeInventoryManager(handler, store=_InMemoryStore())  # type: ignore[arg-type]
    assert mgr.schedule_background_refresh() is None

    handler.hass = hass
    gate = asyncio.Event()

    async def _slow_scan(*, include_diagnostic_probes: bool = False) -> None:
        await gate.wait()

    with patch.object(mgr, "async_run_scan", side_effect=_slow_scan):
        t1 = mgr.schedule_background_refresh()
        t2 = mgr.schedule_background_refresh()
        assert t1 is not None and t1 is t2
        mgr.cancel()
        assert mgr._refresh_task is None
        with pytest.raises(asyncio.CancelledError):
            await t1


async def test_send_lowest_tier_yields_and_reads_outcomes(hass, monkeypatch) -> None:
    """_send_lowest_tier yields while Tier 0 work exists and reads written flags."""
    real_sleep = asyncio.sleep
    monkeypatch.setattr(
        "custom_components.myhome.zigbee_inventory.asyncio.sleep",
        lambda _delay: real_sleep(0),
    )
    scheduler = FrameScheduler()
    handler = SimpleNamespace(
        hass=hass,
        mac=TEST_EUI64,
        firmware="1.0.0",
        log_id="[3578]",
        config_entry=SimpleNamespace(data={CONF_MAC: TEST_EUI64}),
        send_buffer=scheduler,
        profile=Legrand3578Profile(),
    )
    mgr = ZigbeeInventoryManager(handler, store=_InMemoryStore())  # type: ignore[arg-type]

    # Put a Tier-0 command, wait until _send_lowest_tier checks has_tier0_commands twice then clear it.
    tier0_fut = asyncio.get_running_loop().create_future()
    await scheduler.put({"message": "*1*1*700123401#9##", "is_status_request": False, "written": tier0_fut})

    async def _drain_and_answer() -> None:
        # Pop Tier 0 command so has_tier0_commands becomes False
        await scheduler.acquire_next()
        # Next wait for lowest-tier command queued by mgr
        env = await scheduler.acquire_next()
        assert env["tier_override"] == _TIER_OTHERS
        assert env["is_status_request"] is True
        fut = env["written"]
        setattr(fut, "_collected_responses", ["resp1"])
        fut.set_result(1.0)

    driver = asyncio.create_task(_drain_and_answer())
    outcome = await mgr._send_lowest_tier("*#13**67##", timeout=5.0)
    await driver
    assert outcome.written_ok is True
    assert outcome.timed_out is False
    assert outcome.responses == ["resp1"]

    # Also cover timeout branch in _send_lowest_tier when asyncio.wait returns empty done set.
    async def _empty_wait(futures, timeout=None):
        return set(), futures

    monkeypatch.setattr("custom_components.myhome.zigbee_inventory.asyncio.wait", _empty_wait)
    timeout_outcome = await mgr._send_lowest_tier("*#13**66*0##", timeout=1.0)
    assert timeout_outcome.written_ok is False
    assert timeout_outcome.timed_out is True


async def test_run_scan_dim67_dim66_polling_and_supervisor_finally(hass) -> None:
    """Full scan ingests DIM 66 products, updates dev reg, latches unreachables, and restores supervisor."""
    entry = MockConfigEntry(domain=DOMAIN, data={CONF_MAC: TEST_EUI64}, unique_id=TEST_EUI64)
    entry.add_to_hass(hass)
    dev_reg = dr.async_get(hass)
    dev_entry = dev_reg.async_get_or_create(
        config_entry_id=entry.entry_id,
        identifiers={(DOMAIN, f"{TEST_EUI64}-zigbee-7001111")},
        name="ZigBee 7001111",
    )
    assert dev_entry.model is None

    handler = SimpleNamespace(
        hass=hass,
        mac=TEST_EUI64,
        firmware="1.0.0",
        log_id="[3578]",
        config_entry=entry,
        send_buffer=FrameScheduler(),
        profile=Legrand3578Profile(),
        is_connected=True,
    )
    store = _InMemoryStore(
        {
            "eui64": TEST_EUI64,
            "firmware": "1.0.0",
            "last_scan_at": None,
            "product_count": 3,
            "products": {
                "7003333": {
                    "addr": "7003333",
                    "index": 2,
                    "reachable": True,
                    "last_seen": None,
                    "units": {},
                }
            },
        }
    )
    mgr = ZigbeeInventoryManager(handler, store=store)  # type: ignore[arg-type]
    await mgr.async_load()

    # Fallback-parsed DIM 66 frame (no zigbee_device_id attribute, parsed via effective_message_where)
    fallback_ev = _make_gateway_event(
        dimension=66,
        device_id=None,
        unit=None,
        zb_where=None,
        dev_id=513,
        dev_type=None,
    )
    # Invalid DIM66 cases covered too: non-66 item, non-int dev_id, unparseable where
    non66 = _make_gateway_event(dimension=67, product_count=3)
    bad_where = _make_gateway_event(dimension=66, dev_id=258)
    bad_dev_id = _make_gateway_event(
        dimension=66, device_id="7004444", unit="01", zb_where="700444401#9", dev_id=None
    )

    from custom_components.myhome.zigbee_inventory import _TierSendOutcome

    scan_frame = str(OWNGatewayCommand.scan_network())
    count_frame = str(OWNGatewayCommand.request_product_count())
    sup_frame = str(OWNGatewayCommand.enable_supervisor())
    info_frames = {i: str(OWNGatewayCommand.get_product_info(i)) for i in range(4)}
    light_poll_frame = str(OWNLightingCommand.status("700111101#9"))
    cover_poll_frame = str(OWNAutomationCommand.status("700555501#9"))

    async def _fake_send_lowest(cmd, *, timeout=35.0):
        frame = str(cmd)
        if frame == scan_frame:
            # Emit real DIM 67 frame over HA dispatcher during scan_network!
            from homeassistant.helpers.dispatcher import async_dispatcher_send

            dim67 = _make_gateway_event(dimension=67, product_count=4)
            async_dispatcher_send(hass, f"myhome_message_{TEST_EUI64}", dim67)
            return _TierSendOutcome(True, False, False, False, [])
        if frame == info_frames[0]:
            ev0_light = _make_gateway_event(
                dimension=66,
                device_id="7001111",
                unit="01",
                zb_where="700111101#9",
                dev_id=258,
                dev_type="Dimmer actuator",
            )
            ev0_ctrl = _make_gateway_event(
                dimension=66,
                device_id="7001111",
                unit="02",
                zb_where="700111102#9",
                dev_id=999,
                dev_type="Wall switch",
            )
            return _TierSendOutcome(
                True, False, False, False, [non66, bad_where, bad_dev_id, ev0_light, ev0_ctrl]
            )
        if frame == info_frames[1]:
            return _TierSendOutcome(True, False, False, False, [fallback_ev])
        if frame == info_frames[2]:
            # Explicitly flagged unreachable (DEV_ID=0)
            unreach = _make_gateway_event(dimension=66, unreachable=True)
            return _TierSendOutcome(True, False, False, False, [unreach])
        if frame == info_frames[3]:
            # Timeout on index 3
            return _TierSendOutcome(False, True, False, False, [])
        if frame == light_poll_frame:
            return _TierSendOutcome(True, False, False, False, [])
        if frame == cover_poll_frame:
            # Unit poll timeout latches 7005555 unreachable!
            return _TierSendOutcome(False, True, False, False, [])
        if frame in DIAGNOSTIC_PROBE_FRAMES or frame in (count_frame, sup_frame):
            return _TierSendOutcome(False, True, False, False, [])
        return _TierSendOutcome(True, False, False, False, [])

    with (
        patch.object(mgr, "_send_lowest_tier", side_effect=_fake_send_lowest) as spy_send,
        patch(
            "custom_components.myhome.zigbee_inventory.effective_message_where",
            side_effect=lambda e: "700555501#9" if e is fallback_ev else None,
        ),
    ):
        await mgr.async_run_scan(include_diagnostic_probes=True)

    sent_frames = [str(call.args[0]) for call in spy_send.call_args_list]
    assert sup_frame in sent_frames
    for probe in DIAGNOSTIC_PROBE_FRAMES:
        assert probe in sent_frames

    assert mgr.is_reachable("7001111") is True
    assert mgr.is_reachable("7005555") is False  # polled WHO=2 timed out -> latched False
    assert mgr.is_reachable("7003333") is False  # DIM66 index 2 unreachable -> latched False
    assert mgr.products["7005555"]["units"]["01"]["model"] == "Unknown (513)"

    updated_dev = dev_reg.async_get(dev_entry.id)
    assert updated_dev is not None and updated_dev.model == "Wall switch"


async def test_run_scan_fallback_product_count_and_unusable_responses(hass, monkeypatch) -> None:
    """Scan falls back to *#13**67## on event timeout and ignores invalid DIM66 frames."""
    monkeypatch.setattr("custom_components.myhome.zigbee_inventory.SCAN_PRODUCT_COUNT_TIMEOUT", 0.001)
    handler = SimpleNamespace(
        hass=hass,
        mac=TEST_EUI64,
        firmware="1.0.0",
        log_id="[3578]",
        config_entry=None,
        send_buffer=FrameScheduler(),
        profile=Legrand3578Profile(),
        is_connected=False,
    )
    mgr = ZigbeeInventoryManager(handler, store=_InMemoryStore())  # type: ignore[arg-type]

    from custom_components.myhome.zigbee_inventory import _TierSendOutcome

    scan_frame = str(OWNGatewayCommand.scan_network())
    count_frame = str(OWNGatewayCommand.request_product_count())
    info0_frame = str(OWNGatewayCommand.get_product_info(0))

    # Case 1: scan_network succeeds cleanly, event times out, *#13**67## answers with DIM 67 frame,
    # index 0 returns written_ok=True with empty responses (no valid DIM66).
    async def _send_fallback_count(cmd, *, timeout=35.0):
        frame = str(cmd)
        if frame == scan_frame:
            return _TierSendOutcome(True, False, False, False, [])
        if frame == count_frame:
            return _TierSendOutcome(
                True, False, False, False, [_make_gateway_event(dimension=67, product_count=1)]
            )
        if frame == info0_frame:
            return _TierSendOutcome(True, False, False, False, [])
        return _TierSendOutcome(True, False, False, False, [])

    with patch.object(mgr, "_send_lowest_tier", side_effect=_send_fallback_count):
        await mgr.async_run_scan()
    assert mgr.data["product_count"] == 1

    # Case 2: _restore_supervisor error handling when connected
    handler.is_connected = True
    with patch.object(mgr, "_send_lowest_tier", side_effect=RuntimeError("boom")):
        await mgr._restore_supervisor()

    # Case 3: late DIM 67 arriving via dispatcher during request_product_count retry (line 539)
    async def _send_late_dim67(cmd, *, timeout=35.0):
        frame = str(cmd)
        if frame == scan_frame:
            return _TierSendOutcome(True, False, False, False, [])
        if frame == count_frame:
            from homeassistant.helpers.dispatcher import async_dispatcher_send

            async_dispatcher_send(
                hass,
                f"myhome_message_{TEST_EUI64}",
                _make_gateway_event(dimension=67, product_count=0),
            )
            return _TierSendOutcome(True, False, False, False, [])
        return _TierSendOutcome(True, False, False, False, [])

    handler.is_connected = False
    with patch.object(mgr, "_send_lowest_tier", side_effect=_send_late_dim67):
        await mgr.async_run_scan()
    assert mgr.data["product_count"] == 0

    # Case 4: _update_device_registry_model with None entry_id and with valid entry_id
    handler.config_entry = SimpleNamespace(entry_id=None)
    mgr._update_device_registry_model("7001111", "Ignored")
    entry = MockConfigEntry(domain=DOMAIN, data={CONF_MAC: TEST_EUI64}, unique_id=TEST_EUI64)
    entry.add_to_hass(hass)
    handler.config_entry = entry
    mgr._update_device_registry_model("7001111", "Ignored")


async def test_gateway_resync_zigbee_services_and_init(hass) -> None:
    """Verify gateway initial_discovery/close_listener, #9 resync, services sweep, and __init__ setup."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={
            CONF_MAC: TEST_EUI64,
            CONF_NAME: "3578",
            CONF_SERIAL_PORT: TEST_PORT,
            CONF_BAUDRATE: 19200,
        },
        unique_id=TEST_EUI64,
    )
    entry.add_to_hass(hass)

    # Pre-populate inventory store on disk
    store = zigbee_inventory_store(hass, TEST_EUI64)
    await store.async_save(
        {
            "eui64": TEST_EUI64,
            "firmware": "1.2.3",
            "last_scan_at": "2026-01-01T00:00:00+00:00",
            "product_count": 1,
            "products": {
                "7001234": {
                    "addr": "7001234",
                    "index": 0,
                    "reachable": True,
                    "last_seen": "2026-01-01T00:00:00+00:00",
                    "units": {
                        "01": {
                            "unit": "01",
                            "where": "700123401#9",
                            "who": 1,
                            "dev_id": 258,
                            "model": "Dimmer switch",
                        }
                    },
                }
            },
        }
    )

    fake_transport = MagicMock()
    fake_transport.connect = AsyncMock(return_value=True)
    fake_transport.disconnect = AsyncMock()
    fake_transport.is_connected = True
    fake_transport.last_error = None
    fake_transport.register_listener = MagicMock(return_value=lambda: None)

    with (
        patch(
            "custom_components.myhome.gateway_serial.AsyncSerialTransport",
            return_value=fake_transport,
        ),
        patch.object(
            MyHOMEGatewayHandler,
            "listening_loop",
            new=AsyncMock(),
        ),
        patch.object(
            MyHOMEGatewayHandler,
            "sending_loop",
            new=AsyncMock(),
        ),
        patch.object(
            MyHOMEGatewayHandler,
            "initial_discovery",
            new=AsyncMock(),
        ),
        patch("custom_components.myhome.async_setup_services", new=AsyncMock()),
    ):
        assert await hass.config_entries.async_setup(entry.entry_id) is True
        await hass.async_block_till_done()

    runtime = entry.runtime_data
    gw = runtime.gateway
    assert "700123401#9" in runtime.platforms.get("light", {})

    # Primary gateway device uses CONNECTION_ZIGBEE and cannot be manually removed
    dev_reg = dr.async_get(hass)
    assert gw.device_registry_id is not None
    gw_dev = dev_reg.async_get(gw.device_registry_id)
    assert gw_dev is not None
    assert (dr.CONNECTION_ZIGBEE, entry.data[CONF_MAC]) in gw_dev.connections
    assert await async_remove_config_entry_device(hass, entry, gw_dev) is False

    orphan_dev = dev_reg.async_get_or_create(
        config_entry_id=entry.entry_id,
        identifiers={(DOMAIN, f"{gw.mac}-zigbee-7009999")},
        name="Orphan",
    )
    assert await async_remove_config_entry_device(hass, entry, orphan_dev) is True

    # Services sweep_bus triggers ZigBee async_run_scan(include_diagnostic_probes=True)
    # and send_message uses gateway=handler.profile so ZigBee frames parse cleanly.
    await async_setup_services(hass)
    hass.data.setdefault(DOMAIN, {})[gw.mac] = {"entity": gw}
    with patch.object(
        gw.zigbee_inventory, "async_run_scan", new=AsyncMock()
    ) as spy_sweep:
        await hass.services.async_call(
            DOMAIN, "sweep_bus", {"gateway": gw.mac}, blocking=True
        )
        spy_sweep.assert_awaited_once_with(include_diagnostic_probes=True)

    gw.send = AsyncMock()
    await hass.services.async_call(
        DOMAIN,
        "send_message",
        {"gateway": gw.mac, "message": "*1*1*700123401#9##"},
        blocking=True,
    )
    gw.send.assert_awaited_once()
    gw.send_status_request = AsyncMock()
    await hass.services.async_call(
        DOMAIN,
        "send_message",
        {"gateway": gw.mac, "message": "*#1*700123401#9##"},
        blocking=True,
    )
    gw.send_status_request.assert_awaited_once()

    async_register_turn_on_timed_service(hass)
    await hass.services.async_call(
        DOMAIN,
        "turn_on_timed",
        {"entity_id": "light.light_700123401_9", "minutes": 1},
        blocking=True,
    )

    # Test real initial_discovery & close_listener on ZigBee handler
    with (
        patch.object(gw.zigbee_inventory, "seed_configured_platforms") as spy_seed,
        patch.object(
            gw.zigbee_inventory, "schedule_background_refresh", return_value=None
        ) as spy_refresh,
        patch.object(gw.zigbee_inventory, "cancel") as spy_cancel,
        patch.object(gw.serial_runtime, "shutdown", new=AsyncMock()) as spy_shutdown,
    ):
        await gw.initial_discovery()
        spy_seed.assert_called_once()
        spy_refresh.assert_called_once()
        assert gw._initial_discovery_done.is_set()
        await gw.close_listener()
        spy_cancel.assert_called_once()
        spy_shutdown.assert_awaited_once()

    # Unload cleanly before testing ConfigEntryNotReady on failed serial connection
    assert await hass.config_entries.async_unload(entry.entry_id) is True
    await hass.async_block_till_done()
    fake_transport.is_connected = False
    fake_transport.connect = AsyncMock(return_value=False)
    fake_transport.last_error = SimpleNamespace(reason="port_busy")
    with patch(
        "custom_components.myhome.gateway_serial.AsyncSerialTransport",
        return_value=fake_transport,
    ):
        assert await hass.config_entries.async_setup(entry.entry_id) is False
    assert entry.state is ConfigEntryState.SETUP_RETRY
    assert entry.reason is not None and "port_busy" in entry.reason

    # Cancel scheduled HA setup retry cleanly before proceeding
    assert await hass.config_entries.async_unload(entry.entry_id) is True
    await hass.async_block_till_done()

    # Test ZigBee resync (#9 group & broadcast 0#00#9 polling reachable lights only)
    resync = gw._resync_manager
    assert resync._normalize_group_key("#25#9", address_family="zigbee") == "#25#9"
    assert resync._normalize_group_key("#25", address_family="zigbee") == "#25#9"
    assert resync._normalize_group_key("invalid", address_family="zigbee") == "#invalid#9"
    assert (
        resync._normalize_ptp_where("700123401#9", address_family="zigbee")
        == "700123401#9"
    )
    gw.zigbee_inventory._data["products"]["7002222"] = {
        "addr": "7002222",
        "index": 1,
        "reachable": False,
        "last_seen": None,
        "units": {
            "01": {
                "unit": "01",
                "where": "700222201#9",
                "who": 1,
                "dev_id": 258,
                "model": "Unreachable",
            }
        },
    }
    ent_reg = er.async_get(hass)
    ent_reg.async_get_or_create(
        "light",
        DOMAIN,
        f"{gw.mac}-1-700222201#9",
        config_entry=entry,
    )
    ent_reg.async_get_or_create(
        "light",
        DOMAIN,
        f"{gw.mac}-1-not_zigbee",
        config_entry=entry,
    )
    ent_reg.async_get_or_create(
        "light",
        DOMAIN,
        f"{gw.mac}-1-700888801#9",
        config_entry=entry,
    )
    area_map = resync._known_area_map()
    assert "700123401#9" in area_map
    assert "700888801#9" in area_map
    assert "700222201#9" not in area_map

    gw.send_paced = AsyncMock()
    resync.register_group_members("#25#9", ["700123401#9"])
    grp_msg = OWNMessage.parse("*1*1*#25#9##", gateway=gw.profile)
    bcast_msg = OWNMessage.parse("*1*1*0#00#9##", gateway=gw.profile)
    resync.schedule_resync(grp_msg)
    resync.schedule_resync(bcast_msg)
    ptp_msg = OWNMessage.parse("*1*1*700123401#9##", gateway=gw.profile)
    resync.handle_ptp_echo(ptp_msg)
    resync.cancel_all()

    # Unload / remove_entry cleans up ZigBee inventory Store
    await async_remove_entry(hass, entry)
    assert await zigbee_inventory_store(hass, TEST_EUI64).async_load() is None
