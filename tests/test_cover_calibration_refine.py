"""The fine-tuning with the tape on the times already saved: readings and a check, no timed full run.

The lift-off is not timed again (the cover rises by itself for the saved slat time, after a close to the rest that
ends by itself), and the runs start from where the cover stands: up (the travel), half the time down, down, half the
time up. Only the order changes: the same fits on the same samples.
"""
import asyncio
import copy
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
from OWNd.message import OWNMessage

from custom_components.myhome.cover_calibration import begin
from custom_components.myhome.cover_profiles import ProfileError, get_store, read_profile
from tests.test_cover_calibration_geometry import GAP, LIFT, endpoint, half, start, stopped
from tests.test_cover_profiles import plant as plant_fixture
from tests.test_panel_cover_calibration import act, bus

plant = plant_fixture
GUIDED = {"source": "guided", "recorded_at": "2026-11-02T09:00:00+00:00"}


def seed(plant, *, profile=None, record=None):
    """What storage already holds for the first cover: its profile (measured on it unless said) and its record."""
    store = get_store(plant.covers[0].hass, plant.entries[0].entry_id)
    unique = plant.covers[0].unique_id
    evidence = {**GUIDED, "origin_unique_id": unique, "ended_by": "press"}
    store.data["profiles"]["base"] = {
        "name": "Kitchen window", "opening_time": 22.0, "closing_time": 20.0,
        "geometry": {"slat_time_s": 2.0, "opening_roll": 1.0, "closing_roll": 1.0},
        "geometry_provenance": {"slat_time_s": dict(evidence), **{key: {
            "source": "unknown", "recorded_at": None, "origin_unique_id": None} for key in ("opening_roll", "closing_roll")}},
        "provenance": {"opening": dict(evidence), "closing": dict(evidence)}, **(profile or {})}
    store.data["assignments"][unique] = "base"
    if record:
        store.data["covers"][unique] = {"overrides": {}, **record}
    plant.covers[0].async_apply_cover_profile(store.profile(unique))
    return store, evidence


async def started(hass, plant, **extra):
    queue = []

    def enqueue(message, guard, lock):
        future = hass.loop.create_future()
        queue.append((message, guard, lock, future))
        return future

    plant.gateways[0].async_queue_calibration = enqueue
    message = {"id": 12, "entry_id": plant.entries[0].entry_id, "entity_id": plant.records[0].entity_id,
               "revision": 0, "mode": "geometry", "client_id": "owner", "keep_times": True, **extra}
    session = await begin(hass, MagicMock(subscriptions={}), message)
    return SimpleNamespace(session=session, cover=plant.covers[0], queue=queue, clock=[100.0], plant=plant)


async def rest_then_lift(cal, elapsed=LIFT, *, by_bus=True):
    """One `next`, then two movements by themselves: the close to the rest (ended by its stop status, or by its
    time), a pause, the rise for the saved slat time (ended by the session after `elapsed`); then the gap's reading."""
    commands = len(cal.queue)
    await act(cal, "next")
    command, guard, _, written = cal.queue[-1]
    assert "*2*2*" in str(command) and guard()
    written.set_result(cal.clock[0])
    cal.clock[0] += .5
    bus(cal, "*2*2*11##")
    view = cal.session.view()
    assert (view["phase"], view["lift_pair"], view["planned_seconds"]) == ("closing", 1, 25.0)  # 20 s + max(4, 5) s.
    with pytest.raises(ProfileError, match="calibration_step"):
        await act(cal, "endpoint")  # Nothing to confirm: it ends by itself.
    cal.clock[0] += 10
    if by_bus:
        bus(cal, "*2*0*11##")
    else:
        callback = cal.session.deadline._callback
        cal.session.deadline.cancel()
        callback()
    view = cal.session.view()
    assert (view["phase"], view["step"], view["lift_pair"]) == ("settling", "lift", 2)
    assert "*2*0*" in str(cal.queue[-1][0]) and cal.queue[-1][1]()
    cal.queue[-1][3].set_result(cal.clock[0])
    await asyncio.sleep(0)
    bus(cal, "*2*2*11##")  # The close's own late status changes nothing at the rest.
    callback = cal.session.settle._callback
    cal.session.settle.cancel()
    callback()
    command, guard, _, written = cal.queue[-1]
    assert "*2*1*" in str(command) and guard() and len(cal.queue) == commands + 3
    written.set_result(cal.clock[0])
    cal.clock[0] += .5
    bus(cal, "*2*1*11##")
    view = cal.session.view()
    assert (view["phase"], view["planned_seconds"]) == ("opening", view["saved_slat_s"])
    with pytest.raises(ProfileError, match="calibration_step"):
        await act(cal, "lift")  # Not marked by a press.
    cal.clock[0] += elapsed - .2
    callback = cal.session.deadline._callback
    cal.session.deadline.cancel()
    callback()
    await stopped(cal, elapsed=.2)
    assert (cal.session.phase, cal.session.step, cal.session.samples["lift"]) == ("reading", "lift", pytest.approx(elapsed))


@pytest.fixture
async def clocked():
    clock = [100.0]
    with patch("custom_components.myhome.cover_calibration.monotonic", side_effect=lambda: clock[0]):
        yield clock


async def test_fine_tuning_with_slats_keeps_the_saved_times_and_times_no_full_run(hass, plant, clocked):
    store, evidence = seed(plant)
    cal = await started(hass, plant)
    cal.clock = clocked
    view = cal.session.view()
    assert (view["mode"], view["keep_times"], view["step"], view["values"]) == (
        "geometry", True, "home", {"opening_time": 22.0, "closing_time": 20.0})
    assert (view["auto_lift"], view["saved_slat_s"], view["lift_pair"]) == (True, 2.0, 1)
    await rest_then_lift(cal)
    await act(cal, "reading", reading_cm=GAP)
    # No return to the rest: nothing is timed from it, the cover goes straight up to read the travel.
    assert (cal.session.phase, cal.session.step) == ("briefing", "opening")
    await endpoint(cal, 31)  # Pressed late: nothing is timed.
    assert (cal.session.phase, cal.session.view()["reading_kind"]) == ("reading", "travel")
    await act(cal, "reading", reading_cm=200)
    # From the top, half the time down first, planned on the slat time of the lift-off run.
    assert (cal.session.phase, cal.session.step) == ("briefing", "half_close")
    await start(cal)
    assert cal.session.view()["planned_seconds"] == pytest.approx((20 - LIFT) / 2)
    callback = cal.session.deadline._callback
    cal.session.deadline.cancel()
    cal.clock[0] += 9 - .2
    callback()
    await stopped(cal, elapsed=.2)
    await act(cal, "reading", reading_cm=200 * (2 * .5 + 2 * .5 ** 2) / 4)
    assert (cal.session.phase, cal.session.step, cal.session.geometry) == ("briefing", "closing", {})  # Fitted later.
    await endpoint(cal, 7)  # Down, untimed.
    assert (cal.session.step, cal.session.view()["can_repeat"]) == ("half_open", False)  # No full run to repeat.
    await half(cal, 12)
    await act(cal, "reading", reading_cm=200 * (2 * .5 + .5 ** 2) / 3)
    assert cal.session.phase == "review"
    assert cal.session.values == {"opening_time": 22.0, "closing_time": 20.0}
    assert cal.session.geometry == pytest.approx({"slat_time_s": 2, "opening_roll": 2, "closing_roll": 3})
    await act(cal, "save", name="Kitchen window, refined")
    profile = store.data["profiles"][store.data["assignments"][cal.cover.unique_id]]
    # The times and their evidence as they were; travel, slat time and rolls from the tape.
    assert (profile["opening_time"], profile["closing_time"], profile["reference_travel_cm"]) == (22.0, 20.0, 200)
    assert "slats" not in profile and profile["geometry"]["slat_time_s"] > 0, "the slat time says it"
    assert profile["provenance"] == {"opening": evidence, "closing": evidence}
    assert {item["source"] for item in profile["geometry_provenance"].values()} == {"guided"}
    assert store.data["covers"][cal.cover.unique_id]["travel_cm"] == 200
    view = await read_profile(hass, plant.entries[0].entry_id, cal.cover.entity_id)
    assert view["precision"] == "refined"
    cal.session.close()


async def test_fine_tuning_follows_the_travel_read_where_the_profile_scales_the_times(hass, plant, clocked):
    other = {**GUIDED, "origin_unique_id": "elsewhere"}
    store, _ = seed(plant, profile={"opening_time": 20.0, "closing_time": 20.0, "reference_travel_cm": 100.0,
                                    "provenance": {"opening": other, "closing": other}},
                    record={"travel_cm": 150.0, "overrides": {"closing": {"value": 33.0, "provenance": {
                        **GUIDED, "origin_unique_id": plant.covers[0].unique_id}}}})
    store.data["profiles"]["base"].pop("geometry")
    store.data["profiles"]["base"].pop("geometry_provenance")
    cal = await started(hass, plant, slats=False)
    cal.clock = clocked
    # Scaled to the 150 cm saved; the closing time is this cover's own.
    assert cal.session.values == {"opening_time": 30.0, "closing_time": 33.0}
    # Without slats, straight up from wherever it stands (no close first).
    assert (cal.session.step, cal.session.view()["auto_lift"]) == ("opening", False)
    await endpoint(cal, 12)  # Up, untimed.
    await act(cal, "reading", reading_cm=200)
    # The travel read is 200 cm: the profile's times follow it, the cover's own stays.
    assert cal.session.values == {"opening_time": 40.0, "closing_time": 33.0}
    assert cal.session.provenance["opening"] == other
    await half(cal, 16.5)  # Half the time down, from the top.
    await act(cal, "reading", reading_cm=100)
    await endpoint(cal, 4)  # Down.
    await half(cal, 20)  # Half the time up, from the rest.
    await act(cal, "reading", reading_cm=100)
    assert cal.session.phase == "review"
    await act(cal, "save", name="Tall window")
    profile = store.data["profiles"][store.data["assignments"][cal.cover.unique_id]]
    assert (profile["opening_time"], profile["closing_time"], profile["reference_travel_cm"]) == (40.0, 33.0, 200)
    assert profile["geometry"]["slat_time_s"] == 0.0 and "slats" not in profile
    assert store.data["covers"][cal.cover.unique_id] == {"overrides": {}, "travel_cm": 200}
    cal.session.close()


async def test_fine_tuning_on_saved_times_needs_times_and_the_geometry_session(hass, plant, clocked):
    with pytest.raises(ProfileError, match="invalid_profile"):
        await started(hass, plant)  # Never calibrated: no times to keep.
    seed(plant)
    with pytest.raises(ProfileError, match="invalid_profile"):
        await started(hass, plant, mode="guided")
    cal = await started(hass, plant)
    assert cal.session.same_request({"entity_id": cal.cover.entity_id, "mode": "geometry", "keep_times": True})
    assert not cal.session.same_request({"entity_id": cal.cover.entity_id, "mode": "geometry"})
    cal.session.close()


async def test_a_wide_gap_still_repeats_the_lift_off_run(hass, plant, clocked):
    seed(plant)
    cal = await started(hass, plant)
    cal.clock = clocked
    await rest_then_lift(cal, by_bus=False)  # A timer actuator: the close to the rest ends at its time.
    await act(cal, "reading", reading_cm=12)
    view = cal.session.view()
    assert (view["step"], view["gap_warning"], view["can_repeat"]) == ("opening", True, True)
    await act(cal, "repeat")
    assert (cal.session.step, cal.session.after_position) == ("reset", "lift")
    await endpoint(cal, 1)  # Back to the rest (confirmed), then the rise by itself again on `next`.
    assert (cal.session.phase, cal.session.step) == ("briefing", "lift")
    await start(cal)
    assert cal.session.view()["planned_seconds"] == 2.0
    cal.session.close()


async def test_a_reading_on_the_sill_is_kept_when_the_lift_off_was_not_timed(hass, plant, clocked):
    """The rise lasted the saved slat time: a gap of 0 is a reading (the same run would give it again)."""
    seed(plant)
    cal = await started(hass, plant)
    cal.clock = clocked
    await rest_then_lift(cal, elapsed=2.0)
    await act(cal, "reading", reading_cm=0)
    assert (cal.session.step, cal.session.readings["gap"], cal.session.still_resting) == ("opening", 0, False)
    cal.session.close()


async def test_without_a_saved_slat_time_the_lift_off_is_still_marked(hass, plant, clocked):
    """A profile without geometry (said 'with slats' only now): the operator marks the lift-off, as before; a reading
    on the sill returns to the rest."""
    store, _ = seed(plant)
    store.data["profiles"]["base"].pop("geometry")
    store.data["profiles"]["base"].pop("geometry_provenance")
    cal = await started(hass, plant)
    cal.clock = clocked
    assert (cal.session.view()["auto_lift"], cal.session.step) == (False, "home")
    await endpoint(cal, 9)
    await start(cal)
    cal.clock[0] += LIFT - .5
    await act(cal, "lift")
    await stopped(cal, elapsed=.5)
    await act(cal, "reading", reading_cm=0)
    assert (cal.session.step, cal.session.after_position) == ("reset", "lift")
    cal.session.close()


async def test_evidence_a_profile_cannot_keep_is_unknown(hass, plant, clocked):
    seed(plant, profile={"provenance": {"opening": {"source": "guided", "recorded_at": "yesterday",
                                                    "origin_unique_id": "x"}, "closing": GUIDED | {"origin_unique_id": "x"}}})
    cal = await started(hass, plant)
    assert cal.session.provenance["opening"] == {"source": "unknown", "recorded_at": None, "origin_unique_id": None}
    assert cal.session.provenance["closing"]["source"] == "guided"
    cal.session.close()


async def to_review(cal):
    """The fine-tuning with slats, from home to review, with the readings of a 2 s slat phase and rolls 2 and 3."""
    await rest_then_lift(cal)
    await act(cal, "reading", reading_cm=GAP)
    await endpoint(cal, 22)
    await act(cal, "reading", reading_cm=200)
    await half(cal, 9)
    await act(cal, "reading", reading_cm=200 * (2 * .5 + 2 * .5 ** 2) / 4)
    await endpoint(cal, 20)
    await half(cal, 12)
    await act(cal, "reading", reading_cm=200 * (2 * .5 + .5 ** 2) / 3)
    assert cal.session.phase == "review"


async def test_fine_tuning_saved_into_the_profile_followed(hass, plant, clocked):
    """`shared` updates the profile the cover follows, after a preview that lists who follows it."""
    store, evidence = seed(plant, record={"overrides": {"opening": {"value": 23.0, "provenance": {
        **GUIDED, "origin_unique_id": plant.covers[0].unique_id}}}})
    follower = plant.covers[1].unique_id
    store.data["assignments"][follower] = "base"
    store.data["covers"][follower] = {"overrides": {}, "travel_cm": 100.0, "check_cm": 1, "check_measured_cm": 40.0,
                                      "check_expected_cm": 41.0, "check_at": "2026-11-01T09:00:00+00:00"}
    cal = await started(hass, plant)
    cal.clock = clocked
    await to_review(cal)
    assert cal.session.view()["save_modes"] == ["new", "shared"]  # Never `cover`: it holds no geometry.
    for mode in ("cover", "new"):
        with pytest.raises(ProfileError, match="calibration_step"):
            await act(cal, "preview_save", save_mode=mode)
    preview = (await act(cal, "preview_save", save_mode="shared"))["save_preview"]
    assert (preview["before"]["reference_travel_cm"], preview["after"]["reference_travel_cm"]) == (None, 200)
    assert preview["after"]["opening_time"] == 23.0  # The time this cover used, its own included.
    changes = {item["entity_id"]: item["changes"] for item in preview["followers"]}
    scaled = changes[plant.covers[1].entity_id]["opening"]
    # Half the reference travel: the slat phase halves, the curtain by the rolls' geometric scale.
    assert (scaled["before"], scaled["scaled"], scaled["overridden"]) == (22.0, True, False)
    assert 11.5 < scaled["after"] < 23.0 / 2 + 3
    assert changes[plant.covers[0].entity_id]["opening"]["override_removed"] is True
    # The follower with a check loses it, and the preview says so.
    assert {item["entity_id"]: item["loses_check"] for item in preview["followers"]} == {
        plant.covers[0].entity_id: False, plant.covers[1].entity_id: True}
    with pytest.raises(ProfileError, match="preview_required"):
        await act(cal, "save", save_mode="shared", confirmation="other")
    assert cal.session.phase == "review"
    await act(cal, "save", save_mode="shared", confirmation=preview["confirmation"])
    data = store.data
    profile = data["profiles"]["base"]  # The same profile, its name kept.
    assert data["assignments"][cal.cover.unique_id] == "base" and len(data["profiles"]) == 1
    assert (profile["name"], profile["opening_time"], profile["closing_time"], profile["reference_travel_cm"]) == (
        "Kitchen window", 23.0, 20.0, 200)
    assert profile["geometry"] == cal.session.geometry  # Fitted on the opening time it uses (23 s).
    assert "slats" not in profile and profile["geometry"]["slat_time_s"] > 0
    assert {item["source"] for item in profile["geometry_provenance"].values()} == {"guided"}
    assert profile["provenance"]["closing"] == evidence
    assert data["covers"][cal.cover.unique_id] == {"overrides": {}, "travel_cm": 200}
    assert "check_cm" not in data["covers"][follower]  # Other times: its check goes.
    assert plant.covers[1]._travel_time_up == pytest.approx(scaled["after"])  # As previewed, for its 100 cm.
    view = await read_profile(hass, plant.entries[0].entry_id, cal.cover.entity_id)
    assert view["precision"] == "refined"
    # The same profile again (the fit to the last digit): the follower's times stay, and its check.
    data["covers"][follower].update(check_cm=1, check_measured_cm=40.0, check_expected_cm=41.0,
                                    check_at="2026-11-01T09:00:00+00:00")
    cal.session.close()
    cal = await started(hass, plant, revision=data["revision"])
    cal.clock = clocked
    await to_review(cal)
    cal.session.geometry = dict(data["profiles"]["base"]["geometry"])
    preview = (await act(cal, "preview_save", save_mode="shared"))["save_preview"]
    assert not any(item["loses_check"] for item in preview["followers"]), "the same times: no check goes"
    await act(cal, "save", save_mode="shared", confirmation=preview["confirmation"])
    assert data is not store.data and store.data["covers"][follower]["check_cm"] == 1
    cal.session.close()


async def test_without_a_profile_followed_the_fine_tuning_saves_only_a_new_profile(hass, plant):
    plant.gateways[0].async_queue_calibration = MagicMock()
    message = {"id": 12, "entry_id": plant.entries[0].entry_id, "entity_id": plant.records[0].entity_id,
               "revision": 0, "mode": "geometry"}
    session = await begin(hass, MagicMock(subscriptions={}), message)
    assert session.view()["save_modes"] == ["new"]
    session.phase = "review"
    for action in ("preview_save", "save"):
        with pytest.raises(ProfileError, match="profile_not_source"):  # The code of a `shared` not offered.
            await session.action({"action": action, "sequence": session.sequence, "save_mode": "shared"})
    session.close()


async def test_a_follower_fine_tunes_into_a_profile_of_its_own(hass, plant, clocked):
    """Fine-tuning a cover that follows a profile measured on another one offers only `new`;
    `shared` is refused with `profile_not_source`, by the action and by the save itself, and nothing is written."""
    from custom_components.myhome.cover_calibration_save import save_geometry

    store, _ = seed(plant)
    follower = plant.covers[1]
    store.data["assignments"][follower.unique_id] = "base"
    follower.async_apply_cover_profile(store.profile(follower.unique_id))
    cal = await started(hass, plant, entity_id=plant.records[1].entity_id)
    session = cal.session
    assert session.view()["save_modes"] == ["new"]
    session.phase = "review"
    before = copy.deepcopy(store.data)
    for verb in ("preview_save", "save"):
        with pytest.raises(ProfileError, match="profile_not_source"):
            await session.action({"action": verb, "sequence": session.sequence, "save_mode": "shared"})
    with pytest.raises(ProfileError, match="profile_not_source"):
        await save_geometry(session, {"save_mode": "shared"}, preview=True)
    assert store.data == before
    session.close()


@pytest.mark.parametrize("broken", ["expired", "revision", "unavailable", "unassigned", "busy"])
async def test_a_shared_fine_tuning_save_checks_the_session_and_the_profile(hass, plant, clocked, broken):
    from custom_components.myhome.cover_calibration_save import save_geometry

    store, _ = seed(plant)
    store.data["assignments"][plant.covers[1].unique_id] = "base"
    cal = await started(hass, plant)
    cal.clock = clocked
    await to_review(cal)
    preview = await save_geometry(cal.session, {"save_mode": "shared"}, preview=True)
    msg = {"save_mode": "shared", "confirmation": preview["confirmation"]}
    expected = {"expired": "calibration_expired", "revision": "revision_conflict", "unavailable": "cover_unavailable",
                "unassigned": "profile_not_found", "busy": "calibration_busy"}[broken]
    if broken == "expired":
        store.calibration = None
    elif broken == "revision":
        cal.session.revision += 1
    elif broken == "unassigned":
        del store.data["assignments"][cal.cover.unique_id]
    with patch("custom_components.myhome.cover_calibration_save.ready_cover",
               return_value=None if broken == "unavailable" else cal.cover), \
            patch.object(type(plant.covers[1]), "native_calibration_busy",
                         lambda cover: broken == "busy" and cover is plant.covers[1]):
        with pytest.raises(ProfileError, match=expected):
            await save_geometry(cal.session, msg)
    assert store.data["profiles"]["base"]["opening_time"] == 22.0
    store.calibration = cal.session
    cal.session.close()


async def test_the_new_order_changes_no_fit(hass, plant, clocked):
    """The half close read before the half open is fitted after it, on the fitted slat time: the same
    numbers as `opening_fit` then `closing_fit` on the same samples, in the old order."""
    from custom_components.myhome.cover_calibration_fit import closing_fit, opening_fit

    seed(plant)
    cal = await started(hass, plant)
    cal.clock = clocked
    await rest_then_lift(cal, elapsed=2.37)
    await act(cal, "reading", reading_cm=1.6)
    await endpoint(cal, 22)
    await act(cal, "reading", reading_cm=187.0)
    await half(cal, 8.71)
    await act(cal, "reading", reading_cm=113.0)
    await endpoint(cal, 20)
    await half(cal, 12.19)
    await act(cal, "reading", reading_cm=71.0)
    session = cal.session
    slat, opening_roll = opening_fit(22.0, session.samples["lift"], 1.6, 187.0, session.samples["half_open"], 71.0,
                                     clamp=True, closing=20.0)
    closing_roll = closing_fit(20.0, slat, session.samples["half_close"], 113.0, 187.0, clamp=True)
    assert session.geometry == {"slat_time_s": slat, "opening_roll": opening_roll, "closing_roll": closing_roll}
    assert session.samples == pytest.approx({"lift": 2.37, "half_close": 8.71, "half_open": 12.19})
    cal.session.close()


async def test_the_automatic_rise_waits_for_its_owner(hass, plant, clocked):
    """The rise after the rest is an automatic step: without its owner the cycle pauses, and `continue` starts it."""
    seed(plant)
    cal = await started(hass, plant)
    cal.clock = clocked
    await act(cal, "next")
    cal.queue[-1][1]()
    cal.queue[-1][3].set_result(cal.clock[0])
    cal.clock[0] += .5
    bus(cal, "*2*2*11##")
    cal.clock[0] += 10
    bus(cal, "*2*0*11##")
    cal.queue[-1][1]()
    cal.queue[-1][3].set_result(cal.clock[0])
    await asyncio.sleep(0)
    cal.session.owner_seen = float("-inf")
    commands = len(cal.queue)
    callback = cal.session.settle._callback
    cal.session.settle.cancel()
    callback()
    view = cal.session.view()
    assert (view["phase"], view["next_step"]["step"], len(cal.queue)) == ("paused", "lift", commands)
    await act(cal, "continue")
    assert cal.session.phase == "starting_open" and "*2*1*" in str(cal.queue[-1][0])
    cal.session.close()


# The two automatic movements of the lift-off run: what may go wrong between them.

async def at_rest(cal, *, stop=True):
    """One `next` and the close to the rest, under way; with `stop`, ended by its stop status (the session's Stop
    queued, not written yet)."""
    await act(cal, "next")
    cal.queue[-1][1]()
    cal.queue[-1][3].set_result(cal.clock[0])
    cal.clock[0] += .5
    bus(cal, "*2*2*11##")
    cal.clock[0] += 10
    if stop:
        bus(cal, "*2*0*11##")
        assert cal.session.phase == "settling" and "*2*0*" in str(cal.queue[-1][0])


async def test_a_rise_at_the_rest_before_the_automatic_one_interrupts(hass, plant, clocked):
    """Between the two movements nothing rises: a rise is foreign. The Stop of the rest written afterwards starts
    nothing."""
    seed(plant)
    cal = await started(hass, plant)
    cal.clock = clocked
    await at_rest(cal)
    rest_stop = cal.queue[-1][3]
    bus(cal, "*2*1*11##")
    assert (cal.session.phase, cal.session.reason) == ("interrupted", "unexpected_movement")
    rest_stop.set_result(cal.clock[0])
    await asyncio.sleep(0)
    assert cal.session.settle is None and cal.session.phase == "interrupted"
    cal.session.close()


async def test_the_rest_ends_the_session_when_its_stop_cannot_be_queued(hass, plant, clocked):
    seed(plant)
    cal = await started(hass, plant)
    cal.clock = clocked
    await at_rest(cal, stop=False)
    with patch.object(cal.session, "queue_stop", return_value=None):
        bus(cal, "*2*0*11##")
    assert (cal.session.phase, cal.session.reason) == ("interrupted", "stop_queue_full")
    cal.session.close()


async def test_the_rest_ends_the_session_when_its_stop_is_never_written(hass, plant, clocked):
    seed(plant)
    cal = await started(hass, plant)
    cal.clock = clocked
    await at_rest(cal)
    cal.queue[-1][3].cancel()
    await asyncio.sleep(0)
    assert (cal.session.phase, cal.session.reason, cal.session.settle) == ("interrupted", "not_delivered", None)
    cal.session.close()


async def test_the_automatic_rise_does_not_start_on_a_cover_no_longer_writable(hass, plant, clocked):
    seed(plant)
    cal = await started(hass, plant)
    cal.clock = clocked
    await at_rest(cal)
    cal.queue[-1][3].set_result(cal.clock[0])
    await asyncio.sleep(0)
    commands = len(cal.queue)
    callback = cal.session.settle._callback
    cal.session.settle.cancel()
    with patch("custom_components.myhome.cover_calibration_geometry.snapshot", return_value={"writable": False}):
        callback()
    assert (cal.session.phase, cal.session.reason) == ("interrupted", "cover_unavailable")
    assert all("*2*1*" not in str(item[0]) for item in cal.queue[commands:])
    cal.session.close()


async def test_a_late_marked_reply_does_not_end_the_close_to_the_rest_before_the_automatic_rise(hass, plant, clocked):
    seed(plant)
    cal = await started(hass, plant)
    cal.clock = clocked
    await at_rest(cal, stop=False)
    marked = OWNMessage.parse("*2*0*11##")
    marked.myhome_status_reply = True
    cal.cover.handle_event(marked)
    assert (cal.session.phase, cal.session.step) == ("closing", "home")
    bus(cal, "*2*0*11##")
    assert cal.session.phase == "settling"
    cal.session.close()


# A cover with slats and an end-stop actuator whose profile, measured on it, kept a reference travel read wrong
# (50 cm): 18.3 / 17.8 s are the times its motor runs, for its real 150 cm.
END_STOP = {"opening_time": 18.3, "closing_time": 17.8, "reference_travel_cm": 50.0,
            "geometry": {"slat_time_s": 3.01, "opening_roll": 1.0, "closing_roll": 1.0}}


def write(cal):
    """The last command queued goes on the bus now."""
    _, guard, _, written = cal.queue[-1]
    assert guard()
    written.set_result(cal.clock[0])


async def to_half_close_at_end_stops(cal):
    """Perfeziona with every run to an end stop ended by the actuator's own stop, up to the half close's start."""
    await act(cal, "next")  # The close to the rest: it ends at its stop status.
    write(cal)
    cal.clock[0] += .5
    bus(cal, "*2*2*11##")
    cal.clock[0] += 17.8
    bus(cal, "*2*0*11##")
    assert (cal.session.phase, cal.session.step) == ("settling", "lift")
    write(cal)
    await asyncio.sleep(0)
    callback = cal.session.settle._callback
    cal.session.settle.cancel()
    callback()  # The rise for the saved slat time, by itself.
    write(cal)
    cal.clock[0] += .5
    bus(cal, "*2*1*11##")
    cal.clock[0] += 3.01
    callback = cal.session.deadline._callback
    cal.session.deadline.cancel()
    callback()
    await stopped(cal)
    await act(cal, "reading", reading_cm=GAP)
    await start(cal)  # Up: its end stop ends it.
    cal.clock[0] += 18.3
    bus(cal, "*2*0*11##")
    write(cal)  # The Stop after the arrival.
    await asyncio.sleep(0)
    bus(cal, "*2*1000#0*11##")
    bus(cal, "*2*0*11##")
    assert (cal.session.phase, cal.session.view()["reading_kind"]) == ("reading", "travel")
    await act(cal, "reading", reading_cm=150)
    assert (cal.session.phase, cal.session.step) == ("briefing", "half_close")
    # The command as the gateway relays it: its interlock Stop and that Stop's status first, then the translation
    # (held, superseded by the status) and the actuator's movement status.
    await act(cal, "next")
    assert "*2*2*" in str(cal.queue[-1][0])
    write(cal)
    for seconds, frame in ((.06, "*2*1000#0*11##"), (.05, "*2*0*11##"), (.01, "*2*1000#2*11##"), (.48, "*2*2*11##")):
        cal.clock[0] += seconds
        bus(cal, frame)
    assert cal.session.phase == "closing"


async def test_the_half_close_of_an_end_stop_cover_stops_on_its_own_times(hass, plant, clocked):
    """The times measured on this cover are its own whatever travel is read: the half close is planned on them and
    the session's Stop goes out at half of it, before the end stop, whatever the order of the statuses."""
    store, evidence = seed(plant, profile=END_STOP, record={"travel_cm": 50.0})
    cal = await started(hass, plant)
    cal.clock = clocked
    assert cal.session.values == {"opening_time": 18.3, "closing_time": 17.8}
    await to_half_close_at_end_stops(cal)
    assert cal.session.values == {"opening_time": 18.3, "closing_time": 17.8}, "never scaled to the travel read"
    planned = (17.8 - 3.01) / 2
    assert cal.session.view()["planned_seconds"] == pytest.approx(planned)
    assert cal.session.deadline._callback == cal.session._sample_stop
    # Due at its planned time from the run's start (the movement status, .6 s after the write).
    assert cal.session.deadline.when() - hass.loop.time() == pytest.approx(planned, abs=.05)
    commands = len(cal.queue)
    cal.clock[0] += planned
    callback = cal.session.deadline._callback
    cal.session.deadline.cancel()
    callback()
    assert len(cal.queue) == commands + 1 and "*2*0*" in str(cal.queue[-1][0])
    await stopped(cal)
    assert (cal.session.phase, cal.session.samples["half_close"]) == ("reading", pytest.approx(planned))
    cal.session.close()


async def test_the_half_close_on_a_timer_cover_is_planned_as_before(hass, plant, clocked):
    """Without slats, on a relay timer, with a profile whose reference travel is the cover's: as before."""
    seed(plant, profile={"opening_time": 20.0, "closing_time": 19.0, "reference_travel_cm": 160.0,
                         "geometry": {"slat_time_s": 0.0, "opening_roll": 1.0, "closing_roll": 1.0}},
         record={"travel_cm": 160.0})
    cal = await started(hass, plant, slats=False)
    cal.clock = clocked
    await endpoint(cal, 20.5)  # Up, confirmed by the press: a relay timer stops it only after 60 s.
    await act(cal, "reading", reading_cm=160)
    assert (cal.session.step, cal.session.values) == ("half_close", {"opening_time": 20.0, "closing_time": 19.0})
    await start(cal)
    assert cal.session.view()["planned_seconds"] == pytest.approx(19.0 / 2)
    assert cal.session.deadline._callback == cal.session._sample_stop
    cal.clock[0] += 19.0 / 2
    callback = cal.session.deadline._callback
    cal.session.deadline.cancel()
    callback()
    await stopped(cal)
    assert (cal.session.phase, cal.session.samples["half_close"]) == ("reading", pytest.approx(19.0 / 2))
    cal.session.close()


async def test_an_end_stop_before_the_sessions_stop_ends_the_session_and_the_cover_stops(hass, plant, clocked):
    """Times inherited from another cover still follow the travel read: here past the whole run, so the end stop comes
    first. The session ends on it; nothing escapes into the cover, which shows the stop as any other."""
    other = {**GUIDED, "origin_unique_id": "elsewhere"}
    seed(plant, profile={**END_STOP, "provenance": {"opening": other, "closing": other}}, record={"travel_cm": 50.0})
    cal = await started(hass, plant)
    cal.clock = clocked
    await to_half_close_at_end_stops(cal)
    assert cal.session.values["closing_time"] == pytest.approx(17.8 * 3)
    assert cal.cover.is_closing
    cal.clock[0] += 17.8
    bus(cal, "*2*0*11##")  # Its end stop, before the planned Stop.
    assert (cal.session.phase, cal.session.reason) == ("interrupted", "unexpected_stop")
    assert not cal.cover.is_closing and cal.cover.state != "closing", "stopped, no longer closing"
    bus(cal, "*2*0*11##")  # Later statuses of the cover reach an ended session: nothing escapes either.
    cal.session.close()


async def test_nothing_the_session_fails_on_escapes_into_the_cover(hass, plant, clocked, caplog):
    """A session that fails on a bus event ends (its Stop written); the cover still handles the event as its own."""
    seed(plant)
    cal = await started(hass, plant)
    cal.clock = clocked
    await act(cal, "next")
    write(cal)
    commands = len(cal.queue)
    with patch.object(cal.session, "_planned", side_effect=KeyError("closing_time")):
        bus(cal, "*2*2*11##")
    assert (cal.session.phase, cal.session.reason) == ("interrupted", "internal_error")
    assert "*2*0*" in str(cal.queue[-1][0]) and len(cal.queue) == commands + 1
    assert cal.cover.is_closing, "the cover follows the status all the same"
    assert "could not handle" in caplog.text
    cal.session.close()
