"""Unit tests for software stepped dimming transition engine."""

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from custom_components.myhome.const import (
    DEFAULT_TRANSITION_MODE,
    TRANSITION_MODE_NATIVE,
    TRANSITION_MODE_SOFTWARE,
)
from custom_components.myhome.light import MyHOMELight
from custom_components.myhome.light_fade import SoftwareFadeEngine


@pytest.fixture
def create_engine():
    """Fixture to easily create a SoftwareFadeEngine with mocks."""

    def _create(
        transition_mode=TRANSITION_MODE_SOFTWARE,
        worker_count=1,
        is_on=True,
        auto_echo=True,
        first_echo_timeout=1.5,
    ):
        send_instant_cb = AsyncMock()
        apply_state_cb = MagicMock()
        update_ha_state_cb = MagicMock()

        def create_task_cb(coro):
            return asyncio.create_task(coro)

        engine = SoftwareFadeEngine(
            where="12",
            create_task_cb=create_task_cb,
            send_instant_cb=send_instant_cb,
            apply_state_cb=apply_state_cb,
            update_ha_state_cb=update_ha_state_cb,
            get_worker_count_cb=lambda: worker_count,
            get_transition_mode_cb=lambda: transition_mode,
            is_on_cb=lambda: is_on,
            first_echo_timeout=first_echo_timeout,
        )
        if auto_echo:
            send_instant_cb.side_effect = lambda _pct: engine.notify_bus_echo()
        return engine, send_instant_cb, apply_state_cb, update_ha_state_cb

    return _create


async def test_software_fade_basics(create_engine):
    """Test standard software fade steps over time."""
    engine, send_instant, apply_state, update_ha = create_engine()

    with patch("asyncio.sleep", new_callable=AsyncMock) as mock_sleep:
        task = engine.start_fade(start_pct=10, target_pct=20, duration=1.0)
        assert engine.is_fading is True
        await task

        assert engine.is_fading is False
        assert send_instant.call_count > 1
        assert apply_state.call_count > 1
        assert update_ha.call_count > 1
        mock_sleep.assert_called()


async def test_software_fade_instant_when_small_delta(create_engine):
    """Test fade skips steps if delta is <= 1%."""
    engine, send_instant, apply_state, update_ha = create_engine()

    task = engine.start_fade(start_pct=10, target_pct=11, duration=1.0)
    await task

    assert send_instant.call_count == 1
    send_instant.assert_called_with(11)


async def test_software_fade_instant_when_already_at_target(create_engine):
    """Test fade skips bus send and pre-echo mutation if already at target and on."""
    engine, send_instant, apply_state, update_ha = create_engine(is_on=True)

    task = engine.start_fade(start_pct=50, target_pct=50, duration=1.0)
    await task

    send_instant.assert_not_called()
    apply_state.assert_not_called()
    update_ha.assert_not_called()


async def test_software_fade_cancellation(create_engine):
    """Test robust cancellation of fade."""
    engine, send_instant, apply_state, update_ha = create_engine()

    # Block sleep so it gets stuck
    async def infinite_sleep(*args, **kwargs):
        await asyncio.sleep(86400)

    with patch("asyncio.sleep", new=infinite_sleep):
        task = engine.start_fade(start_pct=10, target_pct=90, duration=10.0)
        assert engine.is_fading is True

        await engine.cancel_fade_robustly()

        assert engine.is_fading is False
        assert task.cancelled()


async def test_software_fade_multi_worker_warning(create_engine, caplog):
    """Test warning is logged when workers > 1."""
    engine, _, _, _ = create_engine(worker_count=2)

    with patch("asyncio.sleep", new_callable=AsyncMock):
        await engine.start_fade(start_pct=0, target_pct=100, duration=0.5)

    warnings = [r for r in caplog.records if "Step reordering is possible" in r.getMessage()]
    assert len(warnings) == 1

    # Second fade shouldn't warn again
    with patch("asyncio.sleep", new_callable=AsyncMock):
        await engine.start_fade(start_pct=0, target_pct=100, duration=0.5)

    warnings2 = [r for r in caplog.records if "Step reordering is possible" in r.getMessage()]
    assert len(warnings2) == 1


def test_should_use_software_stepped(create_engine):
    """Test should_use_software_stepped for various transitions and modes."""
    engine, _, _, _ = create_engine(transition_mode=TRANSITION_MODE_SOFTWARE)
    assert engine.should_use_software_stepped(None) is False
    assert engine.should_use_software_stepped(0) is False
    assert engine.should_use_software_stepped(-1.0) is False
    assert engine.should_use_software_stepped(1.5) is True

    native_engine, _, _, _ = create_engine(transition_mode=TRANSITION_MODE_NATIVE)
    assert native_engine.should_use_software_stepped(1.5) is False


def test_get_transition_mode_exception_fallback():
    """Test fallback when get_transition_mode_cb raises an exception."""
    engine = SoftwareFadeEngine(
        where="12",
        create_task_cb=lambda coro: asyncio.create_task(coro),
        send_instant_cb=AsyncMock(),
        apply_state_cb=MagicMock(),
        update_ha_state_cb=MagicMock(),
        get_worker_count_cb=lambda: 1,
        get_transition_mode_cb=MagicMock(side_effect=RuntimeError("boom")),
        is_on_cb=lambda: True,
    )
    assert engine.get_transition_mode() == DEFAULT_TRANSITION_MODE


async def test_software_fade_duration_instant_path(create_engine):
    """Test instant brightness path when duration < 0.05s sends command without pre-echo mutation."""
    engine, send_instant, apply_state, update_ha = create_engine()

    task = engine.start_fade(start_pct=10, target_pct=80, duration=0.02)
    await task

    send_instant.assert_called_once_with(80)
    apply_state.assert_not_called()
    update_ha.assert_not_called()
    assert engine.fade_task is None


async def test_software_fade_stale_fade_id_early_return(create_engine):
    """Test async_fade_to returns immediately when fade_id doesn't match."""
    engine, send_instant, apply_state, update_ha = create_engine()
    engine.fade_id = 5

    await engine.async_fade_to(0, 100, 1.0, fade_id=1)
    send_instant.assert_not_called()
    apply_state.assert_not_called()
    update_ha.assert_not_called()


async def test_software_fade_mid_loop_abort(create_engine, caplog):
    """Test fade aborts mid-loop if fade_id is mutated during execution."""
    engine, send_instant, apply_state, update_ha = create_engine()

    async def sleep_and_change_id(step_time):
        engine.fade_id = 999

    with patch("asyncio.sleep", side_effect=sleep_and_change_id):
        with caplog.at_level("DEBUG"):
            task = engine.start_fade(start_pct=0, target_pct=100, duration=1.0)
            await task

    assert "Aborting stale fade step" in caplog.text
    assert engine.fade_id == 999
    # Only step 1 was executed before sleep mutated the ID
    assert send_instant.call_count == 1


async def test_cancel_fade_if_active(create_engine):
    """Test cancel_fade_if_active cancels task and clears reference."""
    engine, _, _, _ = create_engine()

    # When no task is active, no-op
    engine.cancel_fade_if_active()
    assert engine.fade_task is None

    # Start a long task
    async def infinite_sleep(*args, **kwargs):
        await asyncio.sleep(86400)

    with patch("asyncio.sleep", new=infinite_sleep):
        task = engine.start_fade(start_pct=0, target_pct=100, duration=10.0)
        assert engine.is_fading is True

        engine.cancel_fade_if_active()
        assert engine.fade_task is None
        assert engine.is_fading is False
        assert task.cancelling() or task.cancelled()


async def test_software_fade_cancelled_error_reraise(create_engine, caplog):
    """Test CancelledError is re-raised and logged cleanly."""
    engine, send_instant, _, _ = create_engine()
    send_instant.side_effect = asyncio.CancelledError()

    with caplog.at_level("DEBUG"):
        with pytest.raises(asyncio.CancelledError):
            fid = engine.next_fade_id()
            await engine.async_fade_to(0, 100, 0.5, fade_id=fid)

    assert "Software fade cancelled" in caplog.text


async def test_software_fade_generic_exception_caught(create_engine, caplog):
    """Test unhandled exceptions inside async_fade_to are logged to avoid unretrieved task errors."""
    engine, send_instant, _, _ = create_engine()
    send_instant.side_effect = RuntimeError("Bus timeout")

    with caplog.at_level("WARNING"):
        task = engine.start_fade(start_pct=0, target_pct=100, duration=0.5)
        await task

    assert "Fade task error" in caplog.text
    assert "Bus timeout" in caplog.text
    assert engine.fade_task is None


async def test_software_fade_worker_count_exception_handled(create_engine):
    """Test get_worker_count_cb raising does not crash the fade."""
    engine, _, _, _ = create_engine()
    engine.get_worker_count_cb = MagicMock(side_effect=ValueError("bad count"))

    with patch("asyncio.sleep", new_callable=AsyncMock):
        task = engine.start_fade(start_pct=0, target_pct=100, duration=0.5)
        await task

    assert engine.fade_task is None


async def test_maybe_instant_brightness_when_not_is_on_at_same_pct(create_engine):
    """Test maybe_instant_brightness sends command without pre-echo mutation when light is off."""
    engine, send_instant, apply_state, update_ha = create_engine(is_on=False)

    result = await engine.maybe_instant_brightness(50, 50, is_on=True)
    assert result is True
    send_instant.assert_called_once_with(50)
    apply_state.assert_not_called()
    update_ha.assert_not_called()

    # Delta > 1 returns False
    send_instant.reset_mock()
    result_large = await engine.maybe_instant_brightness(10, 50)
    assert result_large is False
    send_instant.assert_not_called()


async def test_software_fade_aborts_without_bounce_when_step1_has_no_echo(create_engine, caplog):
    """Test multi-step fade aborts cleanly with zero state mutation when step 1 does not echo."""
    engine, send_instant, apply_state, update_ha = create_engine(
        auto_echo=False, first_echo_timeout=0.02
    )
    # Idle notify_bus_echo is a safe no-op
    engine.notify_bus_echo()

    with caplog.at_level("DEBUG"):
        task = engine.start_fade(start_pct=20, target_pct=80, duration=1.0)
        await task

    assert send_instant.call_count == 1
    apply_state.assert_not_called()
    update_ha.assert_not_called()
    assert engine.fade_task is None
    assert engine.pending_first_step_pct is None
    assert "no bus echo" in caplog.text


async def test_myhome_light_fade_shims(hass):
    """Test MyHOMELight backward-compatibility shims delegating to SoftwareFadeEngine."""
    gateway = MagicMock()
    gateway.log_id = "GW1"
    gateway.config_entry = None
    light = MyHOMELight(
        hass=hass,
        name="Test",
        entity_name="Test",
        icon=None,
        icon_on=None,
        device_id="12",
        who="1",
        where="12",
        interface=None,
        dimmable=True,
        manufacturer="BTicino",
        model="Dimmer",
        gateway=gateway,
    )
    # Test _cmd_lock shim
    assert isinstance(light._cmd_lock, asyncio.Lock)

    # Test _warned_multi_worker getter & setter shims
    assert light._warned_multi_worker is False
    light._warned_multi_worker = True
    assert light._warned_multi_worker is True
    assert light._fade_engine.warned_multi_worker is True

    # Test _get_worker_count_config when config_entry is None (line 607)
    assert light._get_worker_count_config() == 1

    # Test _next_fade_id shim (line 640)
    fid = light._next_fade_id()
    assert fid == 1
    assert light._fade_id == 1

    # Test _get_transition_mode shim
    assert light._get_transition_mode() == DEFAULT_TRANSITION_MODE

    # Test _should_use_software_stepped shim
    assert light._should_use_software_stepped(None) is False

    # Test _cancel_fade_if_active and _cancel_fade_robustly shims
    light._cancel_fade_if_active()
    await light._cancel_fade_robustly()


async def test_myhome_light_set_brightness_instant_zero_switches_off(hass):
    """Test _set_brightness_instant sends switch_off when pct <= 0 and set_brightness otherwise."""
    gateway = MagicMock()
    gateway.log_id = "GW1"
    gateway.config_entry = None
    gateway.send = AsyncMock()
    light = MyHOMELight(
        hass=hass,
        name="Test",
        entity_name="Test",
        icon=None,
        icon_on=None,
        device_id="12",
        who="1",
        where="12",
        interface=None,
        dimmable=True,
        manufacturer="BTicino",
        model="Dimmer",
        gateway=gateway,
    )
    # pct == 0 sends switch_off (*1*0*12##)
    await light._set_brightness_instant(0)
    assert gateway.send.call_count == 1
    cmd = gateway.send.call_args[0][0]
    assert str(cmd) == "*1*0*12##"

    # pct == 50 sends set_brightness (*#1*12*#1*150*0##)
    gateway.send.reset_mock()
    await light._set_brightness_instant(50)
    assert gateway.send.call_count == 1
    cmd = gateway.send.call_args[0][0]
    assert str(cmd) == "*#1*12*#1*150*0##"


async def test_software_fade_step1_timeout_anchors_on_written_future_and_handles_nack(
    create_engine,
) -> None:
    """Audit #977 item 4a: step-1 echo window starts after socket write completion, not enqueue."""
    loop = asyncio.get_running_loop()

    # 1. Queue wait (0.08s) exceeds first_echo_timeout (0.04s), then echo arrives 0.01s after write -> succeeds!
    engine, send_instant, apply_state, _ = create_engine(
        auto_echo=False, first_echo_timeout=0.04
    )
    step1_written: asyncio.Future[float] = loop.create_future()
    call_idx = 0

    async def _queued_send(_pct: int) -> asyncio.Future[float] | None:
        nonlocal call_idx
        call_idx += 1
        return step1_written if call_idx == 1 else None

    send_instant.side_effect = _queued_send
    task = engine.start_fade(start_pct=10, target_pct=30, duration=0.12)
    await asyncio.sleep(0.08)  # longer than first_echo_timeout while still queued!
    assert engine.is_fading is True
    step1_written.set_result(asyncio.get_running_loop().time())
    await asyncio.sleep(0.01)
    engine.notify_bus_echo()
    await task
    assert send_instant.call_count >= 2
    assert apply_state.call_count >= 2

    # 2. Fast-event echo arriving BEFORE written future resolves confirms immediately
    engine2, send2, apply2, _ = create_engine(auto_echo=False, first_echo_timeout=0.04)
    fast_written: asyncio.Future[float] = loop.create_future()
    idx2 = 0

    async def _fast_send(_pct: int) -> asyncio.Future[float] | None:
        nonlocal idx2
        idx2 += 1
        return fast_written if idx2 == 1 else None

    send2.side_effect = _fast_send
    real_sleep = asyncio.sleep
    with patch("custom_components.myhome.light_fade.asyncio.sleep", new_callable=AsyncMock):
        t2 = engine2.start_fade(start_pct=10, target_pct=30, duration=0.5)
        await real_sleep(0)
        await real_sleep(0)
        engine2.notify_bus_echo()
        await t2
    assert apply2.call_count >= 2

    # 3. Cancelled / NACKed / timed-out written future aborts step 1 without state mutation
    for fail_mode in ("cancel", "exc", "stale", "write_timeout"):
        eng_f, send_f, apply_f, _ = create_engine(auto_echo=False, first_echo_timeout=5.0)
        fut_f: asyncio.Future[float] = loop.create_future()

        async def _ret_fut(_pct: int, f: asyncio.Future[float] = fut_f) -> asyncio.Future[float]:
            return f

        send_f.side_effect = _ret_fut
        if fail_mode == "write_timeout":
            with patch("custom_components.myhome.light_fade._STEP1_WRITE_TIMEOUT_S", 0.01):
                tf = eng_f.start_fade(start_pct=10, target_pct=40, duration=1.0)
                await tf
        else:
            tf = eng_f.start_fade(start_pct=10, target_pct=40, duration=1.0)
            await asyncio.sleep(0)
            if fail_mode == "cancel":
                fut_f.cancel()
            elif fail_mode == "exc":
                fut_f.set_exception(RuntimeError("gateway nack"))
            else:
                eng_f.fade_id += 1
                fut_f.set_result(loop.time())
            await tf
        apply_f.assert_not_called()

    # 4. Direct start_fade clears stale step-1 echo state before incrementing fade_id
    eng_clean, _, _, _ = create_engine(auto_echo=True, first_echo_timeout=1.0)
    eng_clean._step1_echo_event = asyncio.Event()
    eng_clean.pending_first_step_pct = 42
    eng_clean.fade_start_pct = 20
    t_clean = eng_clean.start_fade(start_pct=50, target_pct=50, duration=0.5)
    assert eng_clean._step1_echo_event is None
    assert eng_clean.pending_first_step_pct is None
    assert eng_clean.fade_start_pct is None
    await t_clean
