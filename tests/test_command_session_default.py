"""New entries start at the profile's default command-session count, below its limit."""

import pytest
from OWNd.profiles import get_gateway_profile

from custom_components.myhome.gateway import command_session_default, command_session_limit

MODELS = ["F454", "F455", "F461", "MH200", "MH200N", "MH201", "MH202", "H4890", "MyHomeServer1"]


@pytest.mark.parametrize("model", MODELS)
def test_default_is_within_the_gateway_limit(model: str) -> None:
    default = command_session_default(model)
    limit = command_session_limit(model)
    if limit is None:  # a model the installed OWNd has no profile for
        assert default == 1
        return
    assert 1 <= default <= limit
    assert default == min(get_gateway_profile(model).default_command_sessions, limit)


@pytest.mark.parametrize("model", [None, "", "SomethingUnknown"])
def test_unknown_model_defaults_to_one(model: str | None) -> None:
    assert command_session_default(model) == 1


def test_default_is_clamped_to_the_limit(monkeypatch: pytest.MonkeyPatch) -> None:
    from types import SimpleNamespace

    from custom_components.myhome import gateway

    monkeypatch.setattr(gateway, "get_gateway_profile", lambda _m: SimpleNamespace(default_command_sessions=9, max_command_sessions=2))
    assert command_session_default("X") == 2
