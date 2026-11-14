"""The frame router (custom_components/myhome/router.py)."""
from unittest.mock import MagicMock

from custom_components.myhome.router import FrameRouter


def test_frames_reach_every_subscriber_of_a_key_once():
    router = FrameRouter()
    seen = []
    unsub_a = router.subscribe("1", ["12", "0012"], lambda m: seen.append(("a", m)))
    router.subscribe("1", ["12"], lambda m: seen.append(("b", m)))
    router.subscribe("2", ["12"], lambda m: seen.append(("cover", m)))  # another WHO, same key
    router.subscribe("1", [], lambda m: seen.append(("nobody", m)))  # no keys: never reached

    # one frame published under both spellings reaches each handler once
    assert router.publish("1", ["12", "0012", "12"], "frame") == 2
    assert seen == [("a", "frame"), ("b", "frame")]
    assert router.publish(1, ["0012"], "frame2") == 1  # who may be an int
    assert seen[-1] == ("a", "frame2")
    assert router.publish("1", ["99"], "lost") == 0 and router.publish("1", [], "lost") == 0

    unsub_a()
    unsub_a()  # idempotent
    assert router.subscribers("1", "12") == 1 and router.subscribers("1", "0012") == 0
    assert router.publish("1", ["12"], "after") == 1 and seen[-1] == ("b", "after")
    assert len(router) == 2  # b and the cover


def test_a_failing_handler_does_not_starve_the_others(caplog):
    router = FrameRouter()
    good = MagicMock()
    router.subscribe("2", ["21"], MagicMock(side_effect=RuntimeError("boom")))
    router.subscribe("2", ["21"], good)
    assert router.publish("2", ["21"], "frame") == 2
    good.assert_called_once_with("frame")
    assert "Error handling frame" in caplog.text
