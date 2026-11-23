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


async def test_fw123_offline_dim66_nack_preserves_units_and_counts_all_offline(
    hass,
) -> None:
    """Offline fw 1.2.3 *#13*<ADDR>00#9*66*<IDX>## + NACK records offline nodes & preserves cached units."""
    from datetime import timedelta

    from pytest_homeassistant_custom_component.common import async_fire_time_changed

    from custom_components.myhome.zigbee_inventory import _TierSendOutcome

    profile = Legrand3578Profile("1.2.3")
    entry = MockConfigEntry(domain=DOMAIN, data={CONF_MAC: TEST_EUI64}, unique_id=TEST_EUI64)
    entry.add_to_hass(hass)

    # Warm cached node 7000102 at index 2 already has unit 01; 5 other offline nodes are cold.
    initial = {
        "eui64": TEST_EUI64,
        "firmware": "1.2.3",
        "last_scan_at": None,
        "product_count": 9,
        "products": {
            "7000102": {
                "addr": "7000102",
                "index": 2,
                "reachable": True,
                "last_seen": "2026-11-20T00:00:00+00:00",
                "units": {
                    "01": {
                        "unit": "01",
                        "where": "700010201#9",
                        "who": 1,
                        "dev_id": 256,
                        "model": "On/Off switch",
                    }
                },
            }
        },
    }
    handler = SimpleNamespace(
        hass=hass,
        mac=TEST_EUI64,
        firmware="1.2.3",
        log_id="[3578]",
        availability_signal=f"myhome_{TEST_EUI64}_availability",
        config_entry=entry,
        send_buffer=FrameScheduler(),
        profile=profile,
        is_connected=True,
    )
    store = _InMemoryStore(initial)
    mgr = ZigbeeInventoryManager(handler, store=store)  # type: ignore[arg-type]
    await mgr.async_load()

    avail_notifications: list[bool] = []
    from homeassistant.helpers.dispatcher import (
        async_dispatcher_connect,
        async_dispatcher_send,
    )

    unsub_avail = async_dispatcher_connect(
        hass, handler.availability_signal, lambda: avail_notifications.append(True)
    )

    offline_indices = {0, 1, 2, 3, 4, 8}
    online_defs = {
        5: ("7000105", "01", 256),
        6: ("7000106", "01", 258),
        7: ("7000107", "01", 513),
    }
    scan_frame = str(OWNGatewayCommand.scan_network())
    info_frames = {i: str(OWNGatewayCommand.get_product_info(i)) for i in range(9)}

    async def _fake_send_lowest(cmd, *, timeout=35.0):
        frame = str(cmd)
        if frame == scan_frame:
            dim67 = OWNMessage.parse("*#13**67*9##", gateway=profile)
            async_dispatcher_send(hass, f"myhome_message_{TEST_EUI64}", dim67)
            return _TierSendOutcome(True, False, False, False, [])
        for idx, req_frame in info_frames.items():
            if frame == req_frame:
                if idx in offline_indices:
                    ev = OWNMessage.parse(
                        f"*#13*700010{idx}00#9*66*{idx}##", gateway=profile
                    )
                    async_dispatcher_send(hass, f"myhome_message_{TEST_EUI64}", ev)
                    return _TierSendOutcome(False, False, True, False, [ev])
                addr, unit, dev_id = online_defs[idx]
                ev = OWNMessage.parse(
                    f"*#13*{addr}{unit}#9*66*{idx}*{dev_id}##", gateway=profile
                )
                async_dispatcher_send(hass, f"myhome_message_{TEST_EUI64}", ev)
                return _TierSendOutcome(True, False, False, False, [ev])
        return _TierSendOutcome(True, False, False, False, [])

    with patch.object(mgr, "_send_lowest_tier", side_effect=_fake_send_lowest):
        await mgr.async_run_scan()

    summary = mgr.get_summary()
    assert summary["product_count"] == 9
    assert summary["reachable_count"] == 3
    assert summary["unreachable_count"] == 6
    # Warm cached unit 01 on unplugged node 7000102 is preserved!
    assert mgr.products["7000102"]["reachable"] is False
    assert "01" in mgr.products["7000102"]["units"]
    assert mgr.products["7000102"]["last_seen"] == "2026-11-20T00:00:00+00:00"
    assert len(avail_notifications) >= 1

    unsub_avail()
    mgr.cancel()
    async_fire_time_changed(hass, hass.loop.time() and ( __import__("homeassistant.util.dt").util.dt.utcnow() + timedelta(seconds=10)))


async def test_incremental_dim66_2unit_switch_and_battery_control_pruning(hass) -> None:
    """myhome_message_{mac} ingests 2-unit DIM 66 (+32.5 ms unit 02) and prunes non-actuator controls."""
    from homeassistant.helpers.dispatcher import async_dispatcher_send

    from custom_components.myhome.zigbee_inventory import _TierSendOutcome
    from tests.conftest import attach_runtime

    profile = Legrand3578Profile("1.2.3")
    entry = MockConfigEntry(domain=DOMAIN, data={CONF_MAC: TEST_EUI64}, unique_id=TEST_EUI64)
    entry.add_to_hass(hass)

    dev_reg = dr.async_get(hass)
    dev_2gang = dev_reg.async_get_or_create(
        config_entry_id=entry.entry_id,
        identifiers={(DOMAIN, f"{TEST_EUI64}-zigbee-7005596")},
        name="ZigBee 7005596",
    )
    dev_ctrl = dev_reg.async_get_or_create(
        config_entry_id=entry.entry_id,
        identifiers={(DOMAIN, f"{TEST_EUI64}-zigbee-7008888")},
        name="ZigBee 7008888",
    )
    ent_reg = er.async_get(hass)
    # Stale auto-discovered light, cover, and companion calibrate button on wireless control (700888801#9)
    stale_light = ent_reg.async_get_or_create(
        "light",
        DOMAIN,
        f"{TEST_EUI64}-1-700888801#9",
        config_entry=entry,
        device_id=dev_ctrl.id,
    )
    stale_cover = ent_reg.async_get_or_create(
        "cover",
        DOMAIN,
        f"{TEST_EUI64}-2-700888801#9",
        config_entry=entry,
        device_id=dev_ctrl.id,
    )
    stale_cal_btn = ent_reg.async_get_or_create(
        "button",
        DOMAIN,
        f"{TEST_EUI64}-2-700888801#9-calibrate",
        config_entry=entry,
        device_id=dev_ctrl.id,
    )
    # YAML-configured light on 700888901#9 must NOT be pruned even if dev_id is control
    yaml_light = ent_reg.async_get_or_create(
        "light", DOMAIN, f"{TEST_EUI64}-1-700888901#9", config_entry=entry
    )

    platforms: dict[str, dict[str, dict[str, object]]] = {
        "light": {
            "700888901#9": {CONF_WHERE: "700888901#9"},
            # Pre-existing unlabelled dimmer entry gets enriched on seed_configured_platforms
            "700559701#9": {CONF_WHERE: "700559701#9"},
        }
    }
    handler = SimpleNamespace(
        hass=hass,
        mac=TEST_EUI64,
        firmware="1.2.3",
        log_id="[3578]",
        availability_signal=f"myhome_{TEST_EUI64}_availability",
        config_entry=entry,
        send_buffer=FrameScheduler(),
        profile=profile,
        is_connected=True,
    )
    hass.data[DOMAIN] = {TEST_EUI64: {"entity": handler, "platforms": platforms}}
    attach_runtime(hass, entry, TEST_EUI64, handler)

    mgr = ZigbeeInventoryManager(handler, store=_InMemoryStore())  # type: ignore[arg-type]
    polled: list[str] = []

    async def _fake_poll(cmd, *, timeout=6.0):
        polled.append(str(cmd))
        return _TierSendOutcome(True, False, False, False, [])

    with patch.object(mgr, "_send_lowest_tier", side_effect=_fake_poll):
        # Unit 01 arrives first, then +32.5 ms later Unit 02 arrives on the event stream
        ev_u1 = OWNMessage.parse("*#13*700559601#9*66*5*256##", gateway=profile)
        ev_u2 = OWNMessage.parse("*#13*700559602#9*66*5*256##", gateway=profile)
        ev_dim = OWNMessage.parse("*#13*700559701#9*66*6*258##", gateway=profile)
        ev_ctrl = OWNMessage.parse("*#13*700888801#9*66*7*266##", gateway=profile)
        ev_yaml_ctrl = OWNMessage.parse("*#13*700888901#9*66*8*266##", gateway=profile)

        async_dispatcher_send(hass, f"myhome_message_{TEST_EUI64}", ev_u1)
        async_dispatcher_send(hass, f"myhome_message_{TEST_EUI64}", ev_u2)
        async_dispatcher_send(hass, f"myhome_message_{TEST_EUI64}", ev_dim)
        async_dispatcher_send(hass, f"myhome_message_{TEST_EUI64}", ev_ctrl)
        async_dispatcher_send(hass, f"myhome_message_{TEST_EUI64}", ev_yaml_ctrl)
        await hass.async_block_till_done()

    assert set(mgr.products["7005596"]["units"].keys()) == {"01", "02"}
    assert "700559601#9" in platforms["light"]
    assert "700559602#9" in platforms["light"]
    assert platforms["light"]["700559701#9"][CONF_DIMMABLE] is True
    assert "*#1*700559601#9##" in polled
    assert "*#1*700559602#9##" in polled

    updated_dev = dev_reg.async_get(dev_2gang.id)
    assert updated_dev is not None
    assert updated_dev.manufacturer == "Legrand / BTicino"
    assert updated_dev.model is not None

    assert ent_reg.async_get(stale_light.entity_id) is None
    assert ent_reg.async_get(stale_cover.entity_id) is None
    assert ent_reg.async_get(stale_cal_btn.entity_id) is None
    assert dev_reg.async_get(dev_ctrl.id) is None
    assert ent_reg.async_get(yaml_light.entity_id) is not None

    assert mgr.is_dimmer_where("700559701#9") is True
    assert mgr.is_dimmer_where("700559601#9") is False
    assert mgr.is_dimmer_where("not-zigbee") is False
    assert mgr.is_dimmer_where("999999901#9") is False
    assert mgr.is_non_actuator_where("700888801#9") is True
    assert mgr.is_non_actuator_where("700559601#9") is False
    assert mgr.is_non_actuator_where("not-zigbee") is False

    mgr.cancel()


async def test_watchdog_45s_replug_t0_passive_enrich_and_what33_what34(hass) -> None:
    """4.5s echo watchdog handles 2.4s online echoes, marks unplugged nodes Unavailable, and handles re-plug/join/leave."""
    import time
    from datetime import timedelta

    from homeassistant.helpers.dispatcher import async_dispatcher_send
    from homeassistant.util import dt as dt_util
    from pytest_homeassistant_custom_component.common import async_fire_time_changed

    from custom_components.myhome.zigbee_inventory import _TierSendOutcome

    profile = Legrand3578Profile("1.2.3")
    entry = MockConfigEntry(domain=DOMAIN, data={CONF_MAC: TEST_EUI64}, unique_id=TEST_EUI64)
    entry.add_to_hass(hass)

    initial = {
        "eui64": TEST_EUI64,
        "firmware": "1.2.3",
        "last_scan_at": None,
        "product_count": 3,
        "products": {
            # Warm floating 503-box
            "7003098": {
                "addr": "7003098",
                "index": 1,
                "reachable": True,
                "last_seen": None,
                "units": {
                    "01": {
                        "unit": "01",
                        "where": "700309801#9",
                        "who": 1,
                        "dev_id": 256,
                        "model": "On/Off switch",
                    }
                },
            },
            # Cold 503-box (seen offline at boot, units={})
            "7004099": {
                "addr": "7004099",
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
        firmware="1.2.3",
        log_id="[3578]",
        availability_signal=f"myhome_{TEST_EUI64}_availability",
        config_entry=entry,
        send_buffer=FrameScheduler(),
        profile=profile,
        is_connected=True,
    )
    mgr = ZigbeeInventoryManager(handler, store=_InMemoryStore(initial))  # type: ignore[arg-type]
    await mgr.async_load()

    sent_low: list[str] = []
    box_unplugged = False

    async def _fake_low(cmd, *, timeout=6.0):
        frame = str(cmd)
        sent_low.append(frame)
        if frame == "*#13**66*1##":
            if box_unplugged:
                ev = OWNMessage.parse("*#13*700309800#9*66*1##", gateway=profile)
                return _TierSendOutcome(False, False, True, False, [ev])
            ev = OWNMessage.parse("*#13*700309801#9*66*1*256##", gateway=profile)
            return _TierSendOutcome(True, False, False, False, [ev])
        if frame == "*#13**66*2##":
            ev = OWNMessage.parse("*#13*700409901#9*66*2*258##", gateway=profile)
            return _TierSendOutcome(True, False, False, False, [ev])
        if frame == "*#13**67##":
            ev67 = OWNMessage.parse("*#13**67*4##", gateway=profile)
            return _TierSendOutcome(True, False, False, False, [ev67])
        if frame == "*#13**66*3##":
            ev_new = OWNMessage.parse("*#13*700900101#9*66*3*256##", gateway=profile)
            return _TierSendOutcome(True, False, False, False, [ev_new])
        return _TierSendOutcome(True, False, False, False, [])

    with patch.object(mgr, "_send_lowest_tier", side_effect=_fake_low):
        # Guard checks on non-WHO1/2, status request, broadcast, and post-RX race
        mgr.note_unicast_command_sent(OWNGatewayCommand.enable_supervisor(), time.monotonic())
        mgr.note_unicast_command_sent(
            OWNLightingCommand.status("700309801#9"), time.monotonic()
        )
        bcast_cmd = OWNMessage.parse("*1*0*0#00#9##", gateway=profile)
        mgr.note_unicast_command_sent(bcast_cmd, time.monotonic())
        assert mgr._echo_timers == {}

        # 1. Healthy online echo arriving at +2.4 s MUST cancel the 4.5 s watchdog!
        tx_on = OWNMessage.parse("*1*1*700309801#9##", gateway=profile)
        mgr.note_unicast_command_sent(tx_on, time.monotonic())
        assert "7003098" in mgr._echo_timers
        async_fire_time_changed(hass, dt_util.utcnow() + timedelta(seconds=2.4))
        rx_echo = OWNMessage.parse("*1*1*700309801#9##", gateway=profile)
        async_dispatcher_send(hass, f"myhome_message_{TEST_EUI64}", rx_echo)
        assert "7003098" not in mgr._echo_timers
        async_fire_time_changed(hass, dt_util.utcnow() + timedelta(seconds=6.0))
        await hass.async_block_till_done()
        assert "*#13**66*1##" not in sent_low

        # Post-RX race: written_at older than _last_rx_monotonic OR rx_seq changed skips watchdog;
        # same-tick written_at == _last_rx_monotonic (and unchanged rx_seq) MUST arm watchdog!
        mgr.note_unicast_command_sent(tx_on, time.monotonic() - 1.0)
        assert "7003098" not in mgr._echo_timers
        seq_before = mgr.snapshot_rx_seq(tx_on)
        mgr.mark_reachable("7003098")
        mgr.note_unicast_command_sent(tx_on, time.monotonic(), rx_seq_before=seq_before)
        assert "7003098" not in mgr._echo_timers

        same_tick = mgr._last_rx_monotonic["7003098"]
        seq_same = mgr.snapshot_rx_seq(tx_on)
        mgr.note_unicast_command_sent(tx_on, same_tick, rx_seq_before=seq_same)
        assert "7003098" in mgr._echo_timers
        mgr._cancel_echo_watchdog("7003098")

        # 2. Unplugged 503-box: 4.5 s expires -> probes *#13**66*1## -> NACK -> Unavailable!
        box_unplugged = True
        tx_off = OWNMessage.parse("*1*0*700309801#9##", gateway=profile)
        mgr.note_unicast_command_sent(tx_off, mgr._last_rx_monotonic["7003098"])
        async_fire_time_changed(hass, dt_util.utcnow() + timedelta(seconds=12.0))
        await hass.async_block_till_done()
        assert "*#13**66*1##" in sent_low
        assert mgr.is_reachable("7003098") is False

        # 3. Online node whose echo was merely dropped: DIM 66 succeeds -> polls unit *#1*...##
        box_unplugged = False
        sent_low.clear()
        mgr.note_unicast_command_sent(tx_off, time.monotonic() + 0.01)
        async_fire_time_changed(hass, dt_util.utcnow() + timedelta(seconds=18.0))
        await hass.async_block_till_done()
        assert "*#13**66*1##" in sent_low
        assert "*#1*700309801#9##" in sent_low
        assert mgr.is_reachable("7003098") is True

        # 4. Re-plug at t = 0 s: warm node flips reachable immediately; cold node 7004099 passively enriches!
        mgr.mark_unreachable("7003098")
        assert mgr.is_reachable("7003098") is False
        sent_low.clear()
        async_dispatcher_send(
            hass,
            f"myhome_message_{TEST_EUI64}",
            OWNMessage.parse("*1*1*700309801#9##", gateway=profile),
        )
        assert mgr.is_reachable("7003098") is True  # immediate at t = 0 s!

        async_dispatcher_send(
            hass,
            f"myhome_message_{TEST_EUI64}",
            OWNMessage.parse("*1*1*700409901#9##", gateway=profile),
        )
        await hass.async_block_till_done()
        assert "*#13**66*2##" in sent_low
        assert mgr.is_dimmer_where("700409901#9") is True

        # 5. WHAT 33 join on warm node & brand-new node 7009001 -> rate-limited *13*66*## + tail DIM 66
        sent_low.clear()
        join_warm = OWNMessage.parse("*13*33*700309800#9##", gateway=profile)
        join_new = OWNMessage.parse("*13*33*700900100#9##", gateway=profile)
        async_dispatcher_send(hass, f"myhome_message_{TEST_EUI64}", join_warm)
        async_dispatcher_send(hass, f"myhome_message_{TEST_EUI64}", join_new)
        async_fire_time_changed(hass, dt_util.utcnow() + timedelta(seconds=25.0))
        await hass.async_block_till_done()

        assert sent_low.count("*13*66*##") == 1  # debounced & rate-limited <= 1/s!
        assert "*13*65*##" not in sent_low  # no broadcast scan!
        assert "*#13**67##" in sent_low
        assert "*#13**66*3##" in sent_low
        assert "01" in mgr.products["7009001"]["units"]

        # 6. WHAT 34 leave marks node unreachable immediately
        leave_ev = OWNMessage.parse("*13*34*700309800#9##", gateway=profile)
        async_dispatcher_send(hass, f"myhome_message_{TEST_EUI64}", leave_ev)
        assert mgr.is_reachable("7003098") is False

    mgr.cancel()


async def test_inventory_remaining_edge_branches(hass) -> None:
    """Exercise remaining defensive branches in ZigbeeInventoryManager for 100% coverage."""
    import time
    from datetime import timedelta

    from homeassistant.helpers.dispatcher import async_dispatcher_send
    from homeassistant.util import dt as dt_util
    from pytest_homeassistant_custom_component.common import async_fire_time_changed

    from custom_components.myhome.zigbee_inventory import _TierSendOutcome

    profile = Legrand3578Profile("1.2.3")

    # 1. Non-HomeAssistant handler: _spawn_bg_task closes coroutine, _notify_availability / _schedule_supervisor_restore / note_unicast_command_sent no-op
    bare_handler = SimpleNamespace(
        hass=SimpleNamespace(),  # not isinstance(hass, HomeAssistant)
        mac=TEST_EUI64,
        firmware="1.2.3",
        log_id="[3578]",
        config_entry=SimpleNamespace(data={CONF_MAC: TEST_EUI64}, entry_id=""),
        send_buffer=FrameScheduler(),
        profile=profile,
        is_connected=True,
    )
    bare_mgr = ZigbeeInventoryManager(bare_handler, store=_InMemoryStore())  # type: ignore[arg-type]

    async def _noop() -> None:
        return None

    assert bare_mgr._spawn_bg_task(_noop(), name="noop") is None
    bare_mgr._notify_availability()
    bare_mgr._schedule_supervisor_restore()
    bare_mgr._prune_non_actuator_entities("700111101#9")
    bare_mgr.mark_unreachable("unknown")
    bare_mgr.note_unicast_command_sent(
        OWNMessage.parse("*1*1*700111101#9##", gateway=profile), time.monotonic()
    )
    # _ingest_dim66_event with non-66 event
    assert bare_mgr._ingest_dim66_event(
        OWNMessage.parse("*#13**67*2##", gateway=profile)  # type: ignore[arg-type]
    ) is False

    # 2. Real hass edge branches:
    # - _drain_pending_unit_polls re-entrant guard, unreachable skip, and WHO 2 poll timeout
    # - WHAT 33 join with empty addr and with cold product (units={})
    # - _async_discover_joined_node when request_product_count returns 0 / None
    # - _verify_missing_echo on unindexed WHO 2 cover when status poll NACKs
    # - cancel() while timers and _bg_tasks are active
    # - async_run_scan draining _pending_unit_polls queued mid-scan
    entry = MockConfigEntry(domain=DOMAIN, data={CONF_MAC: TEST_EUI64}, unique_id=TEST_EUI64)
    entry.add_to_hass(hass)
    handler = SimpleNamespace(
        hass=hass,
        mac=TEST_EUI64,
        firmware="1.2.3",
        log_id="[3578]",
        availability_signal=f"myhome_{TEST_EUI64}_availability",
        config_entry=entry,
        send_buffer=FrameScheduler(),
        profile=profile,
        is_connected=True,
    )
    mgr = ZigbeeInventoryManager(
        handler,
        store=_InMemoryStore(
            {
                "eui64": TEST_EUI64,
                "firmware": "1.2.3",
                "last_scan_at": None,
                "product_count": 5,
                "products": {
                    "7007777": {
                        "addr": "7007777",
                        "index": 0,
                        "reachable": False,
                        "last_seen": None,
                        "units": {},
                    },
                    "7007778": {
                        "addr": "7007778",
                        "index": 1,
                        "reachable": True,
                        "last_seen": None,
                        "units": {
                            "01": {
                                "unit": "01",
                                "where": "700777801#9",
                                "who": 2,
                                "dev_id": 513,
                                "model": "Shutter",
                            }
                        },
                    },
                },
            }
        ),
    )  # type: ignore[arg-type]
    await mgr.async_load()

    # Re-entrant guard on _drain_pending_unit_polls
    mgr._drain_polls_active = True
    await mgr._drain_pending_unit_polls()
    mgr._drain_polls_active = False

    async def _edge_low(cmd, *, timeout=6.0):
        frame = str(cmd)
        if frame == "*#2*700777801#9##":
            return _TierSendOutcome(False, True, False, False, [])
        if frame == "*#2*700999901#9##":
            return _TierSendOutcome(False, False, True, False, [])
        if frame == "*#13**67##":
            return _TierSendOutcome(True, False, False, False, [])
        if frame == str(OWNGatewayCommand.scan_network()):
            # Queue a pending unit poll during scan so post-scan drain runs
            mgr._pending_unit_polls.append((1, "700777701#9"))
            mgr._pending_unit_polls.append((2, "700777801#9"))
            async_dispatcher_send(
                hass,
                f"myhome_message_{TEST_EUI64}",
                OWNMessage.parse("*#13**67*0##", gateway=profile),
            )
            return _TierSendOutcome(True, False, False, False, [])
        return _TierSendOutcome(True, False, False, False, [])

    with patch.object(mgr, "_send_lowest_tier", side_effect=_edge_low):
        # Empty addr join & cold product join
        mgr._handle_zigbee_join(None)
        mgr._handle_zigbee_join("7007777")
        # Brand-new join when count returns empty -> exits cleanly; also test old_count >= new_count fallback
        await mgr._async_discover_joined_node("7008000")
        await hass.async_block_till_done()

        # Unindexed WHO 2 cover missing echo -> status poll NACK
        mgr.products["7009999"] = {
            "addr": "7009999",
            "index": 9,
            "reachable": True,
            "last_seen": None,
            "units": {},
        }
        del mgr.products["7009999"]  # unindexed in products during _verify_missing_echo
        await mgr._verify_missing_echo("7009999", 2, "700999901#9")

        # Non-zigbee & unit-00 note_unicast_command_sent
        mgr.note_unicast_command_sent(OWNLightingCommand.switch_on("12"), time.monotonic())
        mgr.note_unicast_command_sent(
            OWNMessage.parse("*1*0*700777800#9##", gateway=profile),
            time.monotonic() + 10.0,
        )

        # Duplicate _schedule_passive_enrich skips while already enriching
        mgr._enriching_addrs.add("7007777")
        mgr._schedule_passive_enrich("7007777", 0)
        mgr._enriching_addrs.discard("7007777")

        # Direct _drain_pending_unit_polls with unreachable skip + WHO 2 poll timeout
        mgr.products["7007777"]["reachable"] = False
        mgr.products["7007778"]["reachable"] = True
        mgr._pending_unit_polls = [(1, "700777701#9"), (2, "700777801#9")]
        await mgr._drain_pending_unit_polls()
        assert mgr.is_reachable("7007778") is False

        # Scan with queued _pending_unit_polls
        mgr.products["7007778"]["reachable"] = True
        await mgr.async_run_scan()
        assert mgr.is_reachable("7007778") is False

    # Test shifted-index WHAT 33 join fallback (old_count=2, new_count=3, new node at index 0)
    # and diagnostic-probe-stage _pending_unit_polls drain
    mgr._data["product_count"] = 2

    async def _shifted_join_and_diag(cmd, *, timeout=6.0):
        frame = str(cmd)
        if frame == "*#13**67##":
            return _TierSendOutcome(
                True,
                False,
                False,
                False,
                [OWNMessage.parse("*#13**67*3##", gateway=profile)],
            )
        if frame == "*#13**66*2##":
            return _TierSendOutcome(
                True,
                False,
                False,
                False,
                [OWNMessage.parse("*#13*700309801#9*66*2*256##", gateway=profile)],
            )
        if frame == "*#13**66*0##":
            return _TierSendOutcome(
                True,
                False,
                False,
                False,
                [OWNMessage.parse("*#13*700800201#9*66*0*256##", gateway=profile)],
            )
        if frame == "*#13**73#0##":
            mgr.products["7007778"]["reachable"] = True
            mgr._pending_unit_polls.append((2, "700777801#9"))
            return _TierSendOutcome(True, False, False, False, [])
        if frame == "*#2*700777801#9##":
            return _TierSendOutcome(False, True, False, False, [])
        if frame == str(OWNGatewayCommand.scan_network()):
            async_dispatcher_send(
                hass,
                f"myhome_message_{TEST_EUI64}",
                OWNMessage.parse("*#13**67*0##", gateway=profile),
            )
            return _TierSendOutcome(True, False, False, False, [])
        return _TierSendOutcome(True, False, False, False, [])

    with patch.object(mgr, "_send_lowest_tier", side_effect=_shifted_join_and_diag):
        await mgr._async_discover_joined_node("7008002")
        await hass.async_block_till_done()
        assert "01" in mgr.products["7008002"]["units"]

        await mgr.async_run_scan(include_diagnostic_probes=True)
        assert mgr._pending_unit_polls == []
        assert mgr.is_reachable("7007778") is False

    # Test old_count >= new_count range fallback in _async_discover_joined_node
    mgr._data["product_count"] = 5

    async def _smaller_count(cmd, *, timeout=6.0):
        frame = str(cmd)
        if frame == "*#13**67##":
            return _TierSendOutcome(
                True,
                False,
                False,
                False,
                [OWNMessage.parse("*#13**67*1##", gateway=profile)],
            )
        return _TierSendOutcome(True, False, False, False, [])

    with patch.object(mgr, "_send_lowest_tier", side_effect=_smaller_count):
        await mgr._async_discover_joined_node("7008001")
        await hass.async_block_till_done()

    # Active timer + active bg_task cancellation in cancel()
    gate = asyncio.Event()

    async def _wait_forever() -> None:
        await gate.wait()

    mgr._spawn_bg_task(_wait_forever(), name="test_bg_cancel")
    mgr._schedule_supervisor_restore()
    mgr.note_unicast_command_sent(
        OWNMessage.parse("*1*1*700123401#9##", gateway=profile), time.monotonic() + 10.0
    )
    assert mgr._echo_timers and mgr._supervisor_timer is not None and mgr._bg_tasks
    mgr.cancel()
    async_fire_time_changed(hass, dt_util.utcnow() + timedelta(seconds=10.0))
    await hass.async_block_till_done()

