"""The setup steps warn that the gateway's connection slots are shared (OWNd#76 review)."""

import json
from pathlib import Path

import pytest

COMPONENT = Path(__file__).parent.parent / "custom_components" / "myhome"
FILES = ["strings.json", *(f"translations/{lang}.json" for lang in ("en", "fr", "it", "nl"))]
STEPS = ("user", "custom", "discovery_confirm")


@pytest.mark.parametrize("file", FILES)
@pytest.mark.parametrize("step", STEPS)
def test_setup_step_warns_about_shared_gateway(file: str, step: str) -> None:
    steps = json.loads((COMPONENT / file).read_text(encoding="utf8"))["config"]["step"]
    description = steps[step]["description"]
    assert "Home+Project" in description
    assert "F455" in description
