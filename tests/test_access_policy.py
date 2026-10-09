"""Tests for the access policy of impulse gates and garage doors (access_policy.py)."""

import asyncio
import time
from datetime import date, timedelta

import pytest
from homeassistant.const import STATE_HOME, STATE_NOT_HOME, STATE_OFF, STATE_ON, STATE_UNAVAILABLE
from homeassistant.core import Context, HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import async_fire_time_changed, async_mock_service
from voluptuous import Invalid

from custom_components.myhome.access_policy import (
    ACTION_PREFIX,
    EVENT_NOTIFICATION_ACTION,
    AccessConfig,
    AccessController,
    AccessDenied,
    Effect,
    Intent,
    Phase,
    RemoteClose,
    classify,
)
from custom_components.myhome.const import EVENT_ACCESS_PREWARNING
from custom_components.myhome.validate import cover_schema

SENSOR = "binary_sensor.garage_contact"
NOTIFY = "notify.mobile_app_parent"


async def _user(hass: HomeAssistant, name: str, home: bool | None = True):
    user = await hass.auth.async_create_user(name)
    if home is not None:
        hass.states.async_set(f"person.{name.lower()}", STATE_HOME if home else STATE_NOT_HOME, {"user_id": user.id})
    return user


def _config(user_id: str, **overrides) -> AccessConfig:
    base = dict(
        allowed_users=(user_id,),
        approvers={user_id: NOTIFY},
        remote_close=RemoteClose.AT_HOME,
        safety_devices_verified=dt_util.now().date(),
        prewarn_seconds=0.05,
        watchdog_margin=5.0,
        approval_timeout=60.0,
    )
    base.update(overrides)
    return AccessConfig(**base)


class Harness:
    """A controller plus the pulses it sent."""

    def __init__(self, hass: HomeAssistant, config: AccessConfig, state_sensor: str | None = SENSOR, pulse_ok: bool = True):
        self.pulses: list[tuple[Intent, Effect]] = []
        self.published = 0
        self.pulse_ok = pulse_ok

        async def send_pulse(intent: Intent, effect: Effect) -> bool:
            self.pulses.append((intent, effect))
            return self.pulse_ok

        def publish() -> None:
            self.published += 1

        self.ctrl = AccessController(
            hass,
            name="Garage 1",
            config=config,
            state_sensor=state_sensor,
            travel_time=15.0,
            send_pulse=send_pulse,
            publish=publish,
            entity_id=lambda: "cover.garage_1",
        )


def _approve_action(calls) -> dict:
    return calls[-1].data["data"]["actions"][0]


async def _press(hass: HomeAssistant, action: str, user_id: str | None, wait: bool = True, **extra) -> None:
    hass.bus.async_fire(EVENT_NOTIFICATION_ACTION, {"action": action, **extra}, context=Context(user_id=user_id))
    if wait:
        await hass.async_block_till_done()
    else:
        await asyncio.sleep(0)


async def _wait_phase(ctrl: AccessController, phase: Phase) -> None:
    for _ in range(200):
        if ctrl.phase is phase:
            return
        await asyncio.sleep(0.005)
    raise AssertionError(f"phase stayed {ctrl.phase}, expected {phase}")


def _fire(hass: HomeAssistant, seconds: float) -> None:
    async_fire_time_changed(hass, dt_util.utcnow() + timedelta(seconds=seconds))


# ── classify ────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("intent", "sensor", "state", "expected"),
    [
        (Intent.OPEN, SENSOR, STATE_OFF, Effect.OPEN),
        (Intent.CLOSE, SENSOR, STATE_ON, Effect.MAY_CLOSE),
        (Intent.STOP, SENSOR, STATE_ON, Effect.MAY_CLOSE),
        (Intent.OPEN, None, None, Effect.MAY_CLOSE),
        (Intent.CLOSE, None, None, Effect.MAY_CLOSE),
    ],
)
def test_classify_allowed(intent, sensor, state, expected):
    """Only a door proven closed is opened by a pulse; without a sensor every pulse can close."""
    assert classify(intent, sensor, state) is expected


@pytest.mark.parametrize(
    ("intent", "state", "match"),
    [
        (Intent.CLOSE, STATE_OFF, "would open"),
        (Intent.STOP, STATE_OFF, "would open"),
        (Intent.OPEN, STATE_ON, "could close"),
        (Intent.OPEN, STATE_UNAVAILABLE, "not reporting"),
        (Intent.OPEN, None, "not reporting"),
    ],
)
def test_classify_refused(intent, state, match):
    """A pulse in the wrong direction or on an unknown state is refused."""
    with pytest.raises(AccessDenied, match=match):
        classify(intent, SENSOR, state)


def test_config_from_config_defaults_and_due_date():
    """No access block means nobody is allowed and closing is disabled."""
    cfg = AccessConfig.from_config(None)
    assert cfg.allowed_users == ()
    assert cfg.remote_close is RemoteClose.NEVER
    assert cfg.safety_check_due() is None
    cfg = AccessConfig.from_config(
        {"allowed_users": ["u1"], "approvers": {"u1": NOTIFY}, "remote_close": "with_camera",
         "camera": "camera.drive", "safety_devices_verified": date(2026, 10, 1), "safety_check_days": 10},
        pin_code="4321",
    )
    assert cfg.approvers == {"u1": NOTIFY}
    assert cfg.remote_close is RemoteClose.WITH_CAMERA
    assert cfg.safety_check_due() == date(2026, 10, 11)
    assert cfg.pin_code == "4321"


# ── Who may ask ─────────────────────────────────────────────────────────


async def test_request_refuses_automations_and_strangers(hass: HomeAssistant):
    """Requests without a user (automations) and from users not on the list are refused."""
    parent = await _user(hass, "Parent")
    child = await _user(hass, "Child")
    h = Harness(hass, _config(parent.id))
    hass.states.async_set(SENSOR, STATE_OFF)
    with pytest.raises(AccessDenied, match="a person must request it"):
        await h.ctrl.async_request(Intent.OPEN, None)
    with pytest.raises(AccessDenied, match="a person must request it"):
        await h.ctrl.async_request(Intent.OPEN, Context())
    with pytest.raises(AccessDenied, match="not allowed"):
        await h.ctrl.async_request(Intent.OPEN, Context(user_id=child.id))
    assert h.ctrl.phase is Phase.IDLE


async def test_request_without_hass_raises():
    """A controller that is not attached refuses with a clear error."""
    h = Harness(None, _config("u1"))  # type: ignore[arg-type]
    with pytest.raises(HomeAssistantError, match="not attached"):
        await h.ctrl.async_request(Intent.OPEN, Context(user_id="u1"))


# ── Open flow ───────────────────────────────────────────────────────────


async def test_open_flow_requires_on_phone_approval(hass: HomeAssistant):
    """Open: a request sends an authenticated approval; only the approval pulses the relay."""
    parent = await _user(hass, "Parent")
    calls = async_mock_service(hass, "notify", "mobile_app_parent")
    h = Harness(hass, _config(parent.id))
    hass.states.async_set(SENSOR, STATE_OFF)

    await h.ctrl.async_request(Intent.OPEN, Context(user_id=parent.id))
    assert h.ctrl.phase is Phase.PENDING
    assert h.pulses == []
    action = _approve_action(calls)
    assert action["authenticationRequired"] is True
    assert action["destructive"] is False
    assert action["action"].startswith(f"{ACTION_PREFIX}APPROVE_")
    assert len(action["action"]) > len(ACTION_PREFIX) + 20  # carries a random nonce
    assert "image" not in calls[-1].data["data"]
    assert "Parent asks to open Garage 1" in calls[-1].data["message"]

    await _press(hass, action["action"], parent.id)
    await hass.async_block_till_done()
    assert h.pulses == [(Intent.OPEN, Effect.OPEN)]
    assert h.ctrl.phase is Phase.MONITORING
    assert any("open by Parent" in c.data["message"] for c in calls)

    hass.states.async_set(SENSOR, STATE_ON)
    _fire(hass, 6)
    await hass.async_block_till_done()
    assert h.ctrl.phase is Phase.IDLE
    await h.ctrl.async_shutdown()


async def test_open_watchdog_reports_but_does_not_latch(hass: HomeAssistant):
    """A door that did not start opening is reported; it is not a safety fault."""
    parent = await _user(hass, "Parent")
    calls = async_mock_service(hass, "notify", "mobile_app_parent")
    h = Harness(hass, _config(parent.id))
    hass.states.async_set(SENSOR, STATE_OFF)
    await h.ctrl.async_request(Intent.OPEN, Context(user_id=parent.id))
    await _press(hass, _approve_action(calls)["action"], parent.id)
    _fire(hass, 6)
    await hass.async_block_till_done()
    assert h.ctrl.phase is Phase.IDLE
    assert h.ctrl.fault_reason is None
    assert any("did not start opening" in c.data["message"] for c in calls)


async def test_approval_is_single_use_and_bound_to_the_user(hass: HomeAssistant):
    """A replayed approval or one from another user's phone does nothing."""
    parent = await _user(hass, "Parent")
    other = await _user(hass, "Other")
    calls = async_mock_service(hass, "notify", "mobile_app_parent")
    h = Harness(hass, _config(parent.id))
    hass.states.async_set(SENSOR, STATE_OFF)
    await h.ctrl.async_request(Intent.OPEN, Context(user_id=parent.id))
    action = _approve_action(calls)["action"]

    await _press(hass, action, other.id)  # wrong user: ignored, still pending
    await _press(hass, f"{ACTION_PREFIX}APPROVE_forged", parent.id)  # wrong nonce
    await _press(hass, "SOMETHING_ELSE", parent.id)  # not ours
    hass.bus.async_fire(EVENT_NOTIFICATION_ACTION, {}, context=Context(user_id=parent.id))
    await hass.async_block_till_done()
    assert h.ctrl.phase is Phase.PENDING
    assert h.pulses == []

    await _press(hass, action, parent.id)
    await _press(hass, action, parent.id)  # replay
    assert h.pulses == [(Intent.OPEN, Effect.OPEN)]
    await h.ctrl.async_shutdown()


async def test_deny_and_expiry(hass: HomeAssistant):
    """Cancel on the phone ends the request; an unanswered request expires and clears the notification."""
    parent = await _user(hass, "Parent")
    calls = async_mock_service(hass, "notify", "mobile_app_parent")
    h = Harness(hass, _config(parent.id))
    hass.states.async_set(SENSOR, STATE_OFF)

    await h.ctrl.async_request(Intent.OPEN, Context(user_id=parent.id))
    deny = calls[-1].data["data"]["actions"][1]["action"]
    await _press(hass, deny, parent.id)
    assert h.ctrl.phase is Phase.IDLE
    assert h.pulses == []

    await h.ctrl.async_request(Intent.OPEN, Context(user_id=parent.id))
    _fire(hass, 61)
    await hass.async_block_till_done()
    assert h.ctrl.phase is Phase.IDLE
    assert calls[-1].data["message"] == "clear_notification"
    assert calls[-1].data["data"]["tag"] == "myhome_access_cover.garage_1"


async def test_busy_and_late_or_changed_approvals(hass: HomeAssistant):
    """One operation at a time; an approval that arrives late or after the door moved does nothing."""
    parent = await _user(hass, "Parent")
    calls = async_mock_service(hass, "notify", "mobile_app_parent")
    h = Harness(hass, _config(parent.id))
    hass.states.async_set(SENSOR, STATE_OFF)

    await h.ctrl.async_request(Intent.OPEN, Context(user_id=parent.id))
    with pytest.raises(AccessDenied, match="in progress"):
        await h.ctrl.async_request(Intent.OPEN, Context(user_id=parent.id))

    hass.states.async_set(SENSOR, STATE_ON)  # someone opened it with the remote
    await _press(hass, _approve_action(calls)["action"], parent.id)
    await hass.async_block_till_done()
    assert h.pulses == []
    assert "could close" in calls[-1].data["message"]

    hass.states.async_set(SENSOR, STATE_OFF)
    await h.ctrl.async_request(Intent.OPEN, Context(user_id=parent.id))
    h.ctrl._pending.created = time.monotonic() - 120  # type: ignore[union-attr]
    await _press(hass, _approve_action(calls)["action"], parent.id)
    await hass.async_block_till_done()
    assert h.pulses == []
    assert "too late" in calls[-1].data["message"]


async def test_dropped_pulse_is_reported(hass: HomeAssistant):
    """A pulse refused by the deadband is reported to the requester."""
    parent = await _user(hass, "Parent")
    calls = async_mock_service(hass, "notify", "mobile_app_parent")
    h = Harness(hass, _config(parent.id), pulse_ok=False)
    hass.states.async_set(SENSOR, STATE_OFF)
    await h.ctrl.async_request(Intent.OPEN, Context(user_id=parent.id))
    await _press(hass, _approve_action(calls)["action"], parent.id)
    await hass.async_block_till_done()
    assert h.ctrl.phase is Phase.IDLE
    assert "dropped" in calls[-1].data["message"]


async def test_failed_notification_aborts_request(hass: HomeAssistant):
    """Without a working notifier no request is left pending."""
    parent = await _user(hass, "Parent")
    h = Harness(hass, _config(parent.id))
    hass.states.async_set(SENSOR, STATE_OFF)
    with pytest.raises(HomeAssistantError, match="could not send the approval request"):
        await h.ctrl.async_request(Intent.OPEN, Context(user_id=parent.id))
    assert h.ctrl.phase is Phase.IDLE
    assert h.ctrl._unsub_action is None


# ── PIN ─────────────────────────────────────────────────────────────────


async def test_pin_reply_and_lockout(hass: HomeAssistant):
    """With a PIN the approval asks for it; three wrong PINs lock the user out."""
    parent = await _user(hass, "Parent")
    calls = async_mock_service(hass, "notify", "mobile_app_parent")
    h = Harness(hass, _config(parent.id, pin_code="2468"))
    hass.states.async_set(SENSOR, STATE_OFF)

    for _ in range(3):
        await h.ctrl.async_request(Intent.OPEN, Context(user_id=parent.id))
        action = _approve_action(calls)
        assert action["behavior"] == "textInput"
        await _press(hass, action["action"], parent.id, reply_text="0000")
        await hass.async_block_till_done()
        assert "wrong PIN" in calls[-1].data["message"]
    assert h.pulses == []
    with pytest.raises(AccessDenied, match="too many wrong PINs"):
        await h.ctrl.async_request(Intent.OPEN, Context(user_id=parent.id))

    h.ctrl._failed_pins.clear()
    await h.ctrl.async_request(Intent.OPEN, Context(user_id=parent.id))
    await _press(hass, _approve_action(calls)["action"], parent.id, reply_text="2468")
    await hass.async_block_till_done()
    assert h.pulses == [(Intent.OPEN, Effect.OPEN)]
    assert h.ctrl.attributes()["pin_protected"] is True
    await h.ctrl.async_shutdown()


# ── Close policy ────────────────────────────────────────────────────────


async def test_close_policy_refusals(hass: HomeAssistant):
    """Every close precondition refuses on its own."""
    parent = await _user(hass, "Parent")
    away = await _user(hass, "Away", home=False)
    nobody = await hass.auth.async_create_user("Nobody")  # no person entity: not home
    hass.states.async_set(SENSOR, STATE_ON)
    ctx = Context(user_id=parent.id)

    h = Harness(hass, _config(parent.id, remote_close=RemoteClose.NEVER))
    with pytest.raises(AccessDenied, match="closing from Home Assistant is disabled"):
        await h.ctrl.async_request(Intent.CLOSE, ctx)

    h = Harness(hass, _config(parent.id, safety_devices_verified=None))
    with pytest.raises(AccessDenied, match="safety devices"):
        await h.ctrl.async_request(Intent.CLOSE, ctx)

    h = Harness(hass, _config(parent.id, safety_devices_verified=dt_util.now().date() - timedelta(days=40)))
    with pytest.raises(AccessDenied, match="periodic safety test was due"):
        await h.ctrl.async_request(Intent.CLOSE, ctx)

    h = Harness(hass, _config(parent.id, close_block_entity="input_boolean.kids_playing"))
    with pytest.raises(AccessDenied, match="missing"):
        await h.ctrl.async_request(Intent.CLOSE, ctx)
    hass.states.async_set("input_boolean.kids_playing", STATE_ON)
    with pytest.raises(AccessDenied, match="blocked by input_boolean.kids_playing"):
        await h.ctrl.async_request(Intent.CLOSE, ctx)

    for user in (away, nobody):
        h = Harness(hass, _config(user.id))
        with pytest.raises(AccessDenied, match="needs you at home"):
            await h.ctrl.async_request(Intent.CLOSE, Context(user_id=user.id))
        h = Harness(hass, _config(user.id, remote_close=RemoteClose.WITH_CAMERA))
        with pytest.raises(AccessDenied, match="needs a camera image"):
            await h.ctrl.async_request(Intent.CLOSE, Context(user_id=user.id))


async def test_close_flow_prewarning_watchdog_fault_and_acknowledge(hass: HomeAssistant):
    """Close: pre-warning, pulse, and a latched fault when the door does not close."""
    parent = await _user(hass, "Parent")
    calls = async_mock_service(hass, "notify", "mobile_app_parent")
    light_calls = async_mock_service(hass, "light", "toggle")
    restore_calls = async_mock_service(hass, "light", "turn_off")
    prewarn_events = []
    hass.bus.async_listen(EVENT_ACCESS_PREWARNING, prewarn_events.append)
    hass.states.async_set("light.garage", STATE_OFF)
    h = Harness(hass, _config(parent.id, prewarn_light="light.garage"))
    hass.states.async_set(SENSOR, STATE_ON)
    ctx = Context(user_id=parent.id)

    await h.ctrl.async_request(Intent.CLOSE, ctx)
    action = _approve_action(calls)
    assert action["destructive"] is True
    assert action["title"] == "Area clear - close"
    assert "door area is clear" in calls[-1].data["message"]

    await _press(hass, action["action"], parent.id)
    await _wait_phase(h.ctrl, Phase.MONITORING)
    assert h.pulses == [(Intent.CLOSE, Effect.MAY_CLOSE)]
    assert len(prewarn_events) == 1
    assert light_calls and restore_calls

    _fire(hass, 21)  # travel 15 + margin 5: still open
    await hass.async_block_till_done()
    assert h.ctrl.phase is Phase.FAULT
    assert "did not close" in (h.ctrl.fault_reason or "")
    assert any(c.data["message"].startswith("ALARM Garage 1") for c in calls)

    with pytest.raises(AccessDenied, match="ended in a fault"):
        await h.ctrl.async_request(Intent.CLOSE, ctx)

    hass.states.async_set("person.parent", STATE_NOT_HOME, {"user_id": parent.id})
    with pytest.raises(AccessDenied, match="on site"):
        await h.ctrl.async_acknowledge_fault(ctx)
    hass.states.async_set("person.parent", STATE_HOME, {"user_id": parent.id})
    await h.ctrl.async_acknowledge_fault(ctx)
    assert h.ctrl.phase is Phase.IDLE
    with pytest.raises(AccessDenied, match="no fault"):
        await h.ctrl.async_acknowledge_fault(ctx)


async def test_open_during_fault_keeps_the_fault_latched(hass: HomeAssistant):
    """Opening stays possible during a fault, and does not clear it."""
    parent = await _user(hass, "Parent")
    calls = async_mock_service(hass, "notify", "mobile_app_parent")
    h = Harness(hass, _config(parent.id))
    h.ctrl.fault_reason = "did not close: possible obstacle or reversal"
    h.ctrl.phase = Phase.FAULT
    hass.states.async_set(SENSOR, STATE_OFF)
    await h.ctrl.async_request(Intent.OPEN, Context(user_id=parent.id))
    with pytest.raises(AccessDenied, match="wait until"):
        await h.ctrl.async_acknowledge_fault(Context(user_id=parent.id))
    await _press(hass, _approve_action(calls)["action"], parent.id)
    hass.states.async_set(SENSOR, STATE_ON)
    _fire(hass, 6)
    await hass.async_block_till_done()
    assert h.ctrl.phase is Phase.FAULT
    await h.ctrl.async_shutdown()


async def test_close_success_returns_to_idle(hass: HomeAssistant):
    """A door that reports closed before the watchdog expires ends the operation."""
    parent = await _user(hass, "Parent")
    calls = async_mock_service(hass, "notify", "mobile_app_parent")
    h = Harness(hass, _config(parent.id))
    hass.states.async_set(SENSOR, STATE_ON)
    await h.ctrl.async_request(Intent.CLOSE, Context(user_id=parent.id))
    await _press(hass, _approve_action(calls)["action"], parent.id)
    await _wait_phase(h.ctrl, Phase.MONITORING)
    hass.states.async_set(SENSOR, STATE_OFF)
    _fire(hass, 21)
    await hass.async_block_till_done()
    assert h.ctrl.phase is Phase.IDLE


async def test_prewarning_cancel_sends_nothing(hass: HomeAssistant):
    """A cancel during the warning (wall button, service) aborts the pulse."""
    parent = await _user(hass, "Parent")
    calls = async_mock_service(hass, "notify", "mobile_app_parent")
    h = Harness(hass, _config(parent.id, prewarn_seconds=5.0, prewarn_light="light.missing"))
    hass.states.async_set("light.missing", STATE_ON)
    hass.states.async_set(SENSOR, STATE_ON)
    try:
        await h.ctrl.async_request(Intent.CLOSE, Context(user_id=parent.id))
        await _press(hass, _approve_action(calls)["action"], parent.id, wait=False)
        await _wait_phase(h.ctrl, Phase.PREWARNING)
        await h.ctrl.async_cancel(None)
        await _wait_phase(h.ctrl, Phase.IDLE)
        await hass.async_block_till_done()
        assert h.pulses == []
        assert "cancelled during the warning" in calls[-1].data["message"]
    finally:
        await h.ctrl.async_shutdown()


async def test_cancel_pending_and_nothing_to_cancel(hass: HomeAssistant):
    """Cancelling a pending request informs the requester; with nothing pending it is refused."""
    parent = await _user(hass, "Parent")
    calls = async_mock_service(hass, "notify", "mobile_app_parent")
    h = Harness(hass, _config(parent.id))
    with pytest.raises(AccessDenied, match="nothing to cancel"):
        await h.ctrl.async_cancel(Context(user_id=parent.id))
    hass.states.async_set(SENSOR, STATE_OFF)
    await h.ctrl.async_request(Intent.OPEN, Context(user_id=parent.id))
    assert h.ctrl.cancel("wall button pressed") is True
    await hass.async_block_till_done()
    assert h.ctrl.phase is Phase.IDLE
    assert "request cancelled (wall button pressed)" in calls[-1].data["message"]


async def test_camera_close_from_away(hass: HomeAssistant):
    """With a camera, closing from away is offered with the image in the approval."""
    away = await _user(hass, "Away", home=False)
    calls = async_mock_service(hass, "notify", "mobile_app_parent")
    h = Harness(hass, _config(away.id, remote_close=RemoteClose.WITH_CAMERA, camera="camera.garage"))
    hass.states.async_set(SENSOR, STATE_ON)
    await h.ctrl.async_request(Intent.CLOSE, Context(user_id=away.id))
    assert calls[-1].data["data"]["image"] == "/api/camera_proxy/camera.garage"
    await h.ctrl.async_shutdown()
    assert h.ctrl._unsub_action is None


async def test_sensorless_gate_and_stop(hass: HomeAssistant):
    """Without a sensor every pulse is a possible close and the result is reported as unverifiable."""
    parent = await _user(hass, "Parent")
    calls = async_mock_service(hass, "notify", "mobile_app_parent")
    h = Harness(hass, _config(parent.id), state_sensor=None)
    await h.ctrl.async_request(Intent.OPEN, Context(user_id=parent.id))
    assert "direction unknown" in calls[-1].data["title"]
    await _press(hass, _approve_action(calls)["action"], parent.id)
    await _wait_phase(h.ctrl, Phase.IDLE)
    await hass.async_block_till_done()
    assert h.pulses == [(Intent.OPEN, Effect.MAY_CLOSE)]
    assert "cannot be verified" in calls[-1].data["message"]

    h2 = Harness(hass, _config(parent.id))
    hass.states.async_set(SENSOR, STATE_ON)
    await h2.ctrl.async_request(Intent.STOP, Context(user_id=parent.id))
    assert "stop" in calls[-1].data["title"]
    await _press(hass, _approve_action(calls)["action"], parent.id)
    await _wait_phase(h2.ctrl, Phase.IDLE)
    assert h2.pulses == [(Intent.STOP, Effect.MAY_CLOSE)]


async def test_best_effort_notifications_never_raise(hass: HomeAssistant):
    """Status and clear notifications swallow notifier errors."""
    parent = await _user(hass, "Parent")
    async_mock_service(hass, "notify", "mobile_app_parent")
    h = Harness(hass, _config(parent.id))
    hass.states.async_set(SENSOR, STATE_OFF)
    await h.ctrl.async_request(Intent.OPEN, Context(user_id=parent.id))
    hass.services.async_remove("notify", "mobile_app_parent")
    await h.ctrl._notify(NOTIFY, "hello")
    _fire(hass, 61)  # expiry tries to clear the notification
    await hass.async_block_till_done()
    assert h.ctrl.phase is Phase.IDLE


async def test_attributes_never_expose_secrets(hass: HomeAssistant):
    """Attributes show the policy state but no nonce or PIN."""
    h = Harness(hass, _config("u1", pin_code="2468"))
    attrs = h.ctrl.attributes()
    assert attrs["access_phase"] == "idle"
    assert attrs["remote_close"] == "at_home"
    assert attrs["safety_check_due"] == (dt_util.now().date() + timedelta(days=31)).isoformat()
    assert "2468" not in str(attrs)


# ── Validation ──────────────────────────────────────────────────────────


def _cover(**cfg) -> dict:
    return {"garage": {"name": "Garage 1", **cfg}}


def test_schema_accepts_full_access_block():
    """A complete access block validates and keeps its values."""
    data = cover_schema(_cover(
        who="1", where="21", type="impulse_relay", device_class="garage",
        state_sensor="binary_sensor.garage_contact_1", pin_code=1357,
        access={
            "allowed_users": ["u1"], "approvers": {"u1": NOTIFY}, "remote_close": "with_camera",
            "camera": "camera.garage", "safety_devices_verified": "2026-10-08", "prewarn_light": "light.garage",
            "close_block_entity": "input_boolean.kids_playing",
        },
    ))
    cfg = data["1-21"]
    assert cfg["pin_code"] == "1357"
    assert cfg["access"]["safety_devices_verified"] == date(2026, 10, 8)
    assert cfg["access"]["prewarn_seconds"] == 5


@pytest.mark.parametrize(
    ("cfg", "match"),
    [
        (dict(who="1", where="9"), "point-to-point"),
        (dict(who="1", where="0"), "point-to-point"),
        (dict(who="2", where="25", access={}), "only apply to impulse covers"),
        (dict(who="1", where="21", access={"allowed_users": ["u1"]}), "needs an approver"),
        (dict(who="1", where="21", access={"approvers": {"u1": NOTIFY}}), "not in allowed_users"),
        (dict(who="1", where="21", access={"allowed_users": ["u1"], "approvers": {"u1": "light.x"}}), "notify service"),
        (dict(who="1", where="21", access={"remote_close": "with_camera"}), "needs a camera"),
        (dict(who="1", where="21", access={"prewarn_seconds": 1}), "prewarn_seconds"),
    ],
)
def test_schema_rejects_unsafe_config(cfg, match):
    """Group/area addresses and incomplete access blocks are rejected."""
    with pytest.raises(Invalid, match=match):
        cover_schema(_cover(**cfg))
