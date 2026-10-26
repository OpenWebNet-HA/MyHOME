"""Tests for scripts/update_gateway_profiles.py."""
from __future__ import annotations

import copy
import importlib.util
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPT = REPO_ROOT / "scripts" / "update_gateway_profiles.py"

spec = importlib.util.spec_from_file_location("update_gateway_profiles", SCRIPT)
ugp = importlib.util.module_from_spec(spec)
sys.modules["update_gateway_profiles"] = ugp
spec.loader.exec_module(ugp)

END_MARKER = ugp.END_MARKER
GATEWAY_METADATA = ugp.GATEWAY_METADATA
START_MARKER = ugp.START_MARKER
check_readme_in_sync = ugp.check_readme_in_sync
extract_gateway_models_from_const = ugp.extract_gateway_models_from_const
extract_ssdp_models_from_manifest = ugp.extract_ssdp_models_from_manifest
generate_gateway_profiles_table = ugp.generate_gateway_profiles_table
main = ugp.main
update_readme = ugp.update_readme


def test_extract_gateway_models_from_const():
    """Verify gateway models extracted from const.py match expected list."""
    models = extract_gateway_models_from_const()
    assert "F454" in models
    assert "F455" in models
    assert "F461" in models
    assert "MH202" in models
    assert "MH201" in models
    assert "MyHomeServer1" in models
    assert "MH200N" in models
    assert "MH200" in models
    assert "AM4890" in models
    assert "F452" in models
    assert "F452V" in models
    assert "F453" in models
    assert "F453AV" in models
    assert "H4684" in models
    assert "Generic" in models


def test_extract_ssdp_models_from_manifest():
    """Verify SSDP discovery models extracted from manifest.json."""
    ssdp_models = extract_ssdp_models_from_manifest()
    assert "F454" in ssdp_models
    assert "MyHomeServer1" in ssdp_models
    assert "AM4890" in ssdp_models
    assert "HL4684" in ssdp_models
    assert "H/L4684" in ssdp_models


def test_generate_gateway_profiles_table():
    """Verify markdown table format and content."""
    table = generate_gateway_profiles_table()
    assert "| Gateway Model | Protocol Support | Max Command Workers | Inter-Frame Delay | UPnP Discovery | Notes |" in table
    assert "| **F454** | OpenWebNet / HMAC | 4 workers | 50 ms | ✅ Port 49153 |" in table
    assert "| **MH202** | OpenWebNet / HMAC | 2 workers | 100 ms | ✅ Port 49153 |" in table
    assert "| **MH201** | OpenWebNet | 1 worker | 100 ms | ✅ Port 49153 |" in table
    assert "| **MH200N** | OpenWebNet | 1 worker | 150 ms | ✅ SSDP |" in table
    assert "| **MH200** *(Legacy)* | OpenWebNet | 1 worker | 150 ms | ✅ SSDP |" in table
    assert "| **H4890 / AM4890** | OpenWebNet | 1 worker | 50 ms | ✅ SSDP |" in table
    assert "| **Legrand 3578** | OpenWebNet (Serial) | 1 worker | 50 ms | ❌ Manual (Serial) |" in table

    for gw in GATEWAY_METADATA:
        assert gw["model"] in table


def test_real_readme_in_sync():
    """Verify repository README.md has valid markers and matches generated output."""
    in_sync, msg = check_readme_in_sync()
    assert in_sync, f"README.md is out of sync: {msg}"


def test_temp_readme_detection_and_update(tmp_path: Path):
    """Verify check and update work correctly on a temporary README copy."""
    temp_readme = tmp_path / "README.md"

    temp_readme.write_text(
        "### Gateway Profiles\n\n"
        "| Gateway Model | Protocol Support | Max Command Workers | Inter-Frame Delay | UPnP Discovery | Notes |\n"
        "|---|---|---|---|---|---|\n"
        "| **F454** | OpenWebNet / HMAC | 4 workers | 20 ms | ✅ Port 49153 | Old notes |\n\n"
        "---\n",
        encoding="utf-8",
    )

    in_sync, msg = check_readme_in_sync(temp_readme)
    assert not in_sync
    assert "Missing markers" in msg

    changed = update_readme(temp_readme)
    assert changed is True

    in_sync, msg = check_readme_in_sync(temp_readme)
    assert in_sync is True

    content = temp_readme.read_text(encoding="utf-8")
    assert START_MARKER in content
    assert END_MARKER in content
    assert "| **H4890 / AM4890** |" in content
    assert "*This table is automatically updated from gateway profile definitions" in content

    changed_again = update_readme(temp_readme)
    assert changed_again is False


def test_cli_flags(capsys):
    """Verify CLI --dry-run and --check behavior."""
    ret_dry = main(["--dry-run"])
    assert ret_dry == 0
    captured = capsys.readouterr()
    assert START_MARKER in captured.out
    assert END_MARKER in captured.out
    assert "| **F454** |" in captured.out

    ret_check = main(["--check"])
    assert ret_check == 0


def test_unmapped_model_raises_error(tmp_path: Path):
    """Verify that an unmapped model in const.py raises ValueError during table generation."""
    dummy_const = tmp_path / "const.py"
    dummy_const.write_text('SUPPORTED_GATEWAY_MODELS = ["F454", "UnmappedGateway9999"]\n', encoding="utf-8")

    with pytest.raises(ValueError, match="Gateway models defined in const.py missing from GATEWAY_METADATA"):
        generate_gateway_profiles_table(dummy_const)


def test_calibrate_metadata_with_ownd():
    """Verify dynamic calibration against OWNd profiles and manifest.json without mutating global state."""
    original_metadata = copy.deepcopy(ugp.GATEWAY_METADATA)

    calibrated = ugp.calibrate_metadata_with_ownd()
    assert ugp.GATEWAY_METADATA == original_metadata
    assert calibrated is not ugp.GATEWAY_METADATA

    f454 = next(gw for gw in calibrated if "F454" in str(gw["model"]))
    assert f454["max_workers"] == "4 workers"
    assert f454["delay"] == "50 ms"
    assert f454["upnp"] == "✅ Port 49153"

    mh202 = next(gw for gw in calibrated if "MH202" in str(gw["model"]))
    assert mh202["max_workers"] == "2 workers"
    assert mh202["delay"] == "100 ms"

    mh200n = next(gw for gw in calibrated if "MH200N" in str(gw["model"]))
    assert mh200n["max_workers"] == "1 worker"
    assert mh200n["delay"] == "150 ms"
    assert mh200n["upnp"] == "✅ SSDP"

    f453av = next(gw for gw in calibrated if "F453AV" in str(gw["model"]))
    assert f453av["max_workers"] == "1 worker"
    assert f453av["delay"] == "150 ms"
    assert f453av["upnp"] == "✅ Port 49153"


def _sound_row_cells(profile_name: str) -> str:
    """The advertised and sent cells of the F461 / MyHomeServer1 row for the installed OWNd profile.

    Both firmwares drop ``*#4*0##`` and refuse every WHO 16 frame; whether WHO 16 is still
    *advertised* (and ``*#16*0*5##`` therefore an omission of the sweep) depends on the
    installed OWNd profile.
    """
    from OWNd.profiles import get_gateway_profile

    if get_gateway_profile(profile_name).supports_who(16):
        return "WHO 1, 2, 4, 16, 18 | WHO 1, 2, 18 (firmware drops `*#4*0##`, `*#16*0*5##`)"
    return "WHO 1, 2, 4, 18 | WHO 1, 2, 18 (firmware drops `*#4*0##`)"


def test_generate_gateway_discovery_table_and_verify_oracle():
    """The discovery table follows the installed OWNd profiles and the oracle check passes."""
    block = ugp.build_discovery_block()
    assert ugp.DISCOVERY_START_MARKER in block
    assert ugp.DISCOVERY_END_MARKER in block
    # Rows whose profile has not changed between OWNd releases: the literal cells.
    assert "| **F454** | WHO 1, 2, 4, 16, 18 | WHO 1, 2, 16, 18 (firmware drops `*#4*0##`) |" in block
    assert "| **F452 / F453 / F453AV** | WHO 1, 2, 4, 16, 18 | WHO 1, 2, 4, 16, 18 |" in block
    assert "| **F455** | WHO 1, 2, 4, 18 | WHO 1, 2, 4, 18 |" in block
    assert "| **MH200** *(Legacy)* | WHO 1, 2, 4, 16 | WHO 1, 2, 16 (firmware drops `*#4*0##`) |" in block
    assert "| **MH200N** | WHO 1, 2, 4, 16 | WHO 1, 2, 4, 16 |" in block
    assert "| **MH201** | WHO 1, 2, 4, 16, 18 | WHO 1, 2, 16, 18 (firmware drops `*#4*0##`) |" in block
    assert "| **H4684 / H/L4684** | WHO 1, 2, 4, 16, 18 | WHO 1, 2, 16, 18 (firmware drops `*#4*0##`) |" in block
    # Rows that follow the OWNd profile (WHO 16 on the F461 / MyHomeServer1 is being dropped
    # in OWNd): derived from the installed profile, so the test is right before and after.
    assert f"| **F461** | {_sound_row_cells('F461')} |" in block
    assert f"| **MyHomeServer1** | {_sound_row_cells('MyHomeServer1')} |" in block
    # No row hard-codes its WHOs; the generator gets them from the profile (``pinned_whos``
    # is the no-OWNd fallback only, and every row carries one).
    assert all("queried_whos" not in row for row in ugp.GATEWAY_DISCOVERY_METADATA)
    assert all(isinstance(row.get("pinned_whos"), list) for row in ugp.GATEWAY_DISCOVERY_METADATA)

    assert ugp.verify_profiles_against_oracle() == []


def test_oracle_check_rejects_a_sweep_omission_the_profile_does_not_advertise(monkeypatch: pytest.MonkeyPatch):
    """``sweep_omits`` may only name *#4*0## / *#16*0*5## for a WHO the profile advertises."""
    rows = copy.deepcopy(ugp.GATEWAY_DISCOVERY_METADATA)
    f455 = next(row for row in rows if row["profile_name"] == "F455")
    f455["sweep_omits"] = ["*#16*0*5##"]  # the F455 profile has no WHO 16
    mh202 = next(row for row in rows if row["profile_name"] == "MH202")
    mh202["sweep_omits"] = ["*#5*0##"]  # not a request the firmware tables know
    monkeypatch.setattr(ugp, "GATEWAY_DISCOVERY_METADATA", rows)
    violations = "\n".join(ugp.verify_profiles_against_oracle())
    assert "**F455** sweep_omits='*#16*0*5##'" in violations
    assert "**MH202** sweep_omits='*#5*0##'" in violations


def test_discovery_whos_follow_the_runtime_gate():
    """``discovery_whos_for`` follows ``_profile_supports_who`` for real profiles; without ``supports_who`` it yields no rows (the runtime gate assumes everything)."""
    from OWNd.profiles import get_gateway_profile

    for name in ("F454", "F455", "F461", "MH200N", "MH202", "MyHomeServer1"):
        prof = get_gateway_profile(name)
        assert ugp.discovery_whos_for(prof) == [w for w in ugp.STARTUP_DISCOVERY_WHOS if prof.supports_who(w)]
    assert ugp.discovery_whos_for(None) == []
    assert ugp.discovery_whos_for(object()) == []


class _ProfileWithoutWho16:
    """An OWNd profile double for the F461 / MyHomeServer1 once OWNd drops WHO 16 from them."""

    def supports_who(self, who: int) -> bool:
        return who in (1, 2, 4, 18, 22)


def test_discovery_row_drops_who16_when_the_profile_does(monkeypatch: pytest.MonkeyPatch):
    """With a profile that no longer lists WHO 16 the F461 row, its sweep omissions and its sound wording follow.

    ``*#16*0*5##`` is no longer an omission of the sweep (nothing is sent for WHO 16 at all),
    so the generated row stops naming it; ``*#4*0##`` is still left out by the firmware.
    """
    import OWNd.profiles as ownd_profiles

    real = ownd_profiles.get_gateway_profile

    def _patched(name: str, *args, **kwargs):
        if name in ("F461", "MyHomeServer1"):
            return _ProfileWithoutWho16()
        return real(name, *args, **kwargs)

    monkeypatch.setattr(ownd_profiles, "get_gateway_profile", _patched)
    rows = {row["model"]: row for row in ugp.calibrate_discovery_with_ownd()}
    for model in ("**F461**", "**MyHomeServer1**"):
        assert rows[model]["queried_whos"] == [1, 2, 4, 18]
        assert rows[model]["sweep_omits"] == ["*#4*0##"]
        assert ugp.sweep_whos(rows[model]) == [1, 2, 18]
        assert "WHO 22 only" in str(rows[model]["notes"])
        assert "notes_without_who16" not in rows[model]
    assert rows["**F454**"]["queried_whos"] == [1, 2, 4, 16, 18]
    assert rows["**F454**"]["sweep_omits"] == ["*#4*0##"]
    assert "bt_difson" in str(rows["**F454**"]["notes"])
    assert rows["**MH202**"]["sweep_omits"] == ["*#4*0##", "*#16*0*5##"]
    block = ugp.build_discovery_block()
    assert (
        "| **F461** | WHO 1, 2, 4, 18 | WHO 1, 2, 18 (firmware drops `*#4*0##`) | "
        "Lighting, cover, and energy meter 51 queries. Sound is WHO 22 only"
    ) in block
    assert "| **MyHomeServer1** | WHO 1, 2, 4, 18 | WHO 1, 2, 18 (firmware drops `*#4*0##`) | " in block


def test_discovery_table_without_ownd_uses_the_pinned_rows(monkeypatch: pytest.MonkeyPatch):
    """Without an importable OWNd every row carries ``pinned_whos`` (the pinned OWNd rows), nothing else."""
    monkeypatch.setitem(sys.modules, "OWNd.profiles", None)
    rows = {row["model"]: row for row in ugp.calibrate_discovery_with_ownd()}
    pinned = {row["model"]: row["pinned_whos"] for row in ugp.GATEWAY_DISCOVERY_METADATA}
    assert rows.keys() == pinned.keys()
    for model, row in rows.items():
        assert row["queried_whos"] == pinned[model]
        assert "pinned_whos" not in row
        assert "notes_without_who16" not in row
    # Whether the F461 / MyHomeServer1 rows carry WHO 16 is the pin's business (the committed
    # docs pin it, see the next test), not this test's: it stays right across the pin bump.
    assert all(row["queried_whos"] for row in rows.values())
    assert "none" not in ugp.generate_gateway_discovery_table()


def test_discovery_table_without_ownd_matches_the_committed_docs(monkeypatch: pytest.MonkeyPatch):
    """The no-OWNd fallback regenerates the committed table byte for byte.

    The quality-scale, test-coverage and docs CI jobs run ``sync_documentation.py --check``
    without OWNd installed, so the fallback and the committed table must agree; at a pin bump
    that changes a profile, ``pinned_whos`` is updated together with ``--update``.
    """
    monkeypatch.setitem(sys.modules, "OWNd.profiles", None)
    doc = (ugp.REPO_ROOT / "docs" / "getting-started" / "hardware-compatibility.md").read_text(
        encoding="utf-8"
    )
    start = doc.index(ugp.DISCOVERY_START_MARKER)
    end = doc.index(ugp.DISCOVERY_END_MARKER) + len(ugp.DISCOVERY_END_MARKER)
    assert ugp.build_discovery_block() == doc[start:end]


def test_discovery_table_with_ownd_matches_the_committed_docs():
    """With the installed OWNd the derived table equals the committed one, row for row.

    Together with the no-OWNd test above this pins ``installed profile == committed table ==
    pinned_whos`` for the pinned OWNd. Against an OWNd whose profiles differ from the pin
    (OWNd #142 drops WHO 16 from the F461 / MyHomeServer1; until the pin bump) only the rows
    whose profile disagrees with ``pinned_whos`` may differ (they are what
    ``sync_documentation.py --update`` regenerates at the pin bump, which ``--check`` reports
    until then); every other row stays byte-identical, so the derivation cannot move a row
    whose profile did not change.
    """
    from OWNd.profiles import get_gateway_profile

    doc = (ugp.REPO_ROOT / "docs" / "getting-started" / "hardware-compatibility.md").read_text(
        encoding="utf-8"
    )
    start = doc.index(ugp.DISCOVERY_START_MARKER)
    end = doc.index(ugp.DISCOVERY_END_MARKER) + len(ugp.DISCOVERY_END_MARKER)
    committed = doc[start:end].splitlines()
    generated = ugp.build_discovery_block().splitlines()
    rows_off_the_pin = {
        str(row["model"])
        for row in ugp.GATEWAY_DISCOVERY_METADATA
        if ugp.discovery_whos_for(get_gateway_profile(str(row["profile_name"]))) != row["pinned_whos"]
    }
    assert len(generated) == len(committed)
    for got, want in zip(generated, committed, strict=True):
        model = got.split(" | ")[0].lstrip("| ")
        if model in rows_off_the_pin:
            assert got != want, model
        else:
            assert got == want
    if not rows_off_the_pin:
        assert ugp.build_discovery_block() == doc[start:end]


def test_pinned_rows_are_ignored_when_ownd_is_importable(monkeypatch: pytest.MonkeyPatch):
    """With OWNd importable the installed profile wins over ``pinned_whos``."""
    metadata = [
        {
            "model": "**F461**",
            "profile_name": "F461",
            "pinned_whos": [1],
            "notes": "sound",
            "notes_without_who16": "no sound",
        }
    ]
    from OWNd.profiles import get_gateway_profile

    (row,) = ugp.calibrate_discovery_with_ownd(metadata)
    assert row["queried_whos"] == ugp.discovery_whos_for(get_gateway_profile("F461"))
    assert row["queried_whos"] != [1]
    assert row["notes"] == ("sound" if 16 in row["queried_whos"] else "no sound")


def test_oracle_check_rejects_a_stale_sweep_omission_at_the_pin_bump(monkeypatch: pytest.MonkeyPatch):
    """Once ``pinned_whos`` drops WHO 16 (the OWNd #142 pin bump) a leftover ``*#16*0*5##`` is flagged.

    ``sweep_omits`` is checked against ``pinned_whos`` (what the row advertises on the pinned
    OWNd), not against whatever OWNd is on ``PYTHONPATH``; a ``pinned_whos`` entry outside the
    startup sweep is flagged too.
    """
    rows = copy.deepcopy(ugp.GATEWAY_DISCOVERY_METADATA)
    f461 = next(row for row in rows if row["profile_name"] == "F461")
    f461["sweep_omits"] = ["*#4*0##", "*#16*0*5##"]  # the pinned-release omissions
    f461["pinned_whos"] = [1, 2, 4, 18]  # the pin bump edit, without the sweep_omits edit
    mhs1 = next(row for row in rows if row["profile_name"] == "MyHomeServer1")
    mhs1["sweep_omits"] = ["*#4*0##", "*#16*0*5##"]  # the pinned-release omissions
    mhs1["pinned_whos"] = [1, 2, 4, 18, 22]  # WHO 22 is never a startup sweep request
    monkeypatch.setattr(ugp, "GATEWAY_DISCOVERY_METADATA", rows)
    violations = ugp.verify_profiles_against_oracle()
    assert [v for v in violations if "**F461** sweep_omits='*#16*0*5##'" in v]
    assert [v for v in violations if "**F461** sweep_omits='*#4*0##'" in v] == []
    assert [v for v in violations if "**MyHomeServer1** pinned_whos=[1, 2, 4, 18, 22]" in v]
    assert [v for v in violations if "**MyHomeServer1** sweep_omits='*#16*0*5##'" in v]
    assert [v for v in violations if "**MyHomeServer1** pinned_whos=[1, 2, 4, 18, 22] disagrees with the installed" in v]
    assert len(violations) == 4


def test_oracle_check_rejects_a_pin_that_disagrees_with_the_installed_profile(monkeypatch: pytest.MonkeyPatch):
    """``pinned_whos`` may differ from the installed profile only by WHO 16, and only where the row declares it.

    A pin that gains a WHO the profile does not advertise (with or without a matching
    ``sweep_omits``) or loses one it does is a stale or mistyped pin and is flagged; the F461 /
    MyHomeServer1 rows, which carry ``notes_without_who16``, may list WHO 16 or not whatever the
    installed OWNd says about it (before and after OWNd #142). A pin that is not a list of WHO
    numbers is flagged instead of raising.
    """
    rows = copy.deepcopy(ugp.GATEWAY_DISCOVERY_METADATA)
    f455 = next(row for row in rows if row["profile_name"] == "F455")
    f455["pinned_whos"] = [1, 2, 4, 16, 18]  # the F455 profile has no WHO 16, and no notes_without_who16
    f455["sweep_omits"] = ["*#16*0*5##"]
    f454 = next(row for row in rows if row["profile_name"] == "F454")
    f454["pinned_whos"] = [1, 2, 16, 18]  # lost WHO 4, which the profile advertises
    mh202 = next(row for row in rows if row["profile_name"] == "MH202")
    mh202["pinned_whos"] = "1, 2, 4"
    h4890 = next(row for row in rows if row["profile_name"] == "H4890")
    h4890["pinned_whos"] = [1, 1, 2, 4, 5, 16, 18]  # a repeated WHO
    mh201 = next(row for row in rows if row["profile_name"] == "MH201")
    mh201["pinned_whos"] = [2, 1, 4, 16, 18]  # reordered
    for name in ("F461", "MyHomeServer1"):
        next(row for row in rows if row["profile_name"] == name)["pinned_whos"] = [1, 2, 4, 18]
    monkeypatch.setattr(ugp, "GATEWAY_DISCOVERY_METADATA", rows)
    violations = ugp.verify_profiles_against_oracle()
    assert [v for v in violations if "**F455** pinned_whos=[1, 2, 4, 16, 18] disagrees with the installed F455 profile [1, 2, 4, 18]." in v]
    assert [v for v in violations if "**F455** sweep_omits=" in v] == []  # 16 is in the (wrong) pin; the pin itself is flagged
    assert [v for v in violations if "**F454** pinned_whos=[1, 2, 16, 18] disagrees with the installed F454 profile [1, 2, 4, 16, 18]." in v]
    assert [v for v in violations if "**MH202** pinned_whos='1, 2, 4' is not a list of WHO numbers." in v]
    assert [v for v in violations if "**MH202** sweep_omits=" in v]  # an unreadable pin advertises nothing
    assert [v for v in violations if "**H4890 / AM4890** pinned_whos=[1, 1, 2, 4, 5, 16, 18] is not in startup sweep order [1, 2, 4, 5, 16, 18]" in v]
    assert [v for v in violations if "**MH201** pinned_whos=[2, 1, 4, 16, 18] is not in startup sweep order [1, 2, 4, 16, 18]" in v]
    assert [v for v in violations if ("**H4890 / AM4890**" in v or "**MH201**" in v) and "disagrees" in v] == []
    assert [v for v in violations if "**F461**" in v or "**MyHomeServer1**" in v] == [
        v for v in violations if "sweep_omits='*#16*0*5##'" in v and ("**F461**" in v or "**MyHomeServer1**" in v)
    ]


@pytest.mark.parametrize("profile_name", ["Generic", "BTicino Nonesuch 9000"])
def test_oracle_check_rejects_a_row_that_resolves_to_the_generic_profile(monkeypatch: pytest.MonkeyPatch, profile_name):
    """A row whose ``profile_name`` is ``Generic`` or names no OWNd profile is flagged, not silently accepted.

    ``get_gateway_profile`` answers ``GenericGatewayProfile`` for both; the check must say which row
    points at a profile that does not exist instead of deriving an empty advertised column from it.
    """
    rows = copy.deepcopy(ugp.GATEWAY_DISCOVERY_METADATA)
    f455 = next(row for row in rows if row["profile_name"] == "F455")
    f455["profile_name"] = profile_name
    monkeypatch.setattr(ugp, "GATEWAY_DISCOVERY_METADATA", rows)
    violations = ugp.verify_profiles_against_oracle()
    assert [
        v
        for v in violations
        if "**F455**" in v and f"{profile_name} (resolves to the generic profile)." in v
    ]
    # the other rows still resolve: no generic-profile violation is reported for them
    assert [v for v in violations if "resolves to the generic profile" in v and "**F455**" not in v] == []


def test_discovery_row_with_an_unreadable_pin_and_no_ownd_is_empty(monkeypatch: pytest.MonkeyPatch):
    """Without OWNd an unreadable ``pinned_whos`` yields an empty row: ``none`` cells, no omissions, the plain notes."""
    monkeypatch.setitem(sys.modules, "OWNd.profiles", None)
    metadata = [
        {
            "model": "**F461**",
            "profile_name": "F461",
            "pinned_whos": "1, 2, 4, 18",
            "sweep_omits": ["*#4*0##", "*#16*0*5##"],
            "notes": "sound",
            "notes_without_who16": "no sound",
        }
    ]
    (row,) = ugp.calibrate_discovery_with_ownd(metadata)
    assert row["queried_whos"] == []
    assert row["sweep_omits"] == []
    assert row["notes"] == "sound"
    monkeypatch.setattr(ugp, "GATEWAY_DISCOVERY_METADATA", metadata)
    assert "| **F461** | none | none | sound |" in ugp.generate_gateway_discovery_table()
    # A repeated or reordered pin (which the oracle check flags) is still rendered in sweep order.
    metadata[0]["pinned_whos"] = [18, 1, 1, 4, 2]
    (row,) = ugp.calibrate_discovery_with_ownd(metadata)
    assert row["queried_whos"] == [1, 2, 4, 18]
    assert row["sweep_omits"] == ["*#4*0##"]
    assert row["notes"] == "no sound"


def test_discovery_table_without_ownd_follows_the_pinned_rows_at_the_pin_bump(monkeypatch: pytest.MonkeyPatch):
    """After the pin bump the CI jobs without OWNd generate the WHO 22 wording from ``pinned_whos`` alone."""
    monkeypatch.setitem(sys.modules, "OWNd.profiles", None)
    rows = copy.deepcopy(ugp.GATEWAY_DISCOVERY_METADATA)
    for row in rows:
        if row["profile_name"] in ("F461", "MyHomeServer1"):
            row["pinned_whos"] = [1, 2, 4, 18]
    calibrated = {row["model"]: row for row in ugp.calibrate_discovery_with_ownd(rows)}
    for model in ("**F461**", "**MyHomeServer1**"):
        assert calibrated[model]["queried_whos"] == [1, 2, 4, 18]
        assert calibrated[model]["sweep_omits"] == ["*#4*0##"]
        assert "WHO 22 only" in str(calibrated[model]["notes"])
    assert calibrated["**MH202**"]["queried_whos"] == [1, 2, 4, 5, 16, 18]
    assert calibrated["**MH202**"]["sweep_omits"] == ["*#4*0##", "*#16*0*5##"]
    monkeypatch.setattr(ugp, "GATEWAY_DISCOVERY_METADATA", rows)
    block = ugp.build_discovery_block()
    assert "| **F461** | WHO 1, 2, 4, 18 | WHO 1, 2, 18 (firmware drops `*#4*0##`) | Lighting, cover, and energy meter 51 queries. Sound is WHO 22 only" in block
    assert "| **MH202** | WHO 1, 2, 4, 5, 16, 18 | WHO 1, 2, 5, 18 (firmware drops `*#4*0##`, `*#16*0*5##`) |" in block


def test_oracle_check_rejects_patched_profile_instance():
    """A profile instance rewritten after import fails the check instead of verifying the rewrite."""
    from OWNd.profiles import get_gateway_profile

    mh200n = get_gateway_profile("MH200N")
    original = mh200n.supported_who
    object.__setattr__(mh200n, "supported_who", (*original, 3, 18))
    try:
        violations = ugp.verify_profiles_against_oracle()
    finally:
        object.__setattr__(mh200n, "supported_who", original)

    assert "OWNd MH200N profile was patched after import" in "\n".join(violations)
    assert ugp.verify_profiles_against_oracle() == []


def test_oracle_check_rejects_patched_profile_class(monkeypatch: pytest.MonkeyPatch):
    """A profile class whose __init__ was wrapped after import fails the check."""
    from OWNd.profiles import F461Profile

    original_init = F461Profile.__init__

    def _wrapped_init(self: F461Profile) -> None:
        original_init(self)

    monkeypatch.setattr(F461Profile, "__init__", _wrapped_init)

    assert ugp._patched_ownd_profiles() == ["F461"]


def test_missing_files_and_markers_graceful_degradation(tmp_path: Path):
    """Verify graceful degradation when files or markers are missing."""
    nonexistent = tmp_path / "nonexistent.md"
    assert update_readme(nonexistent) is False

    no_markers = tmp_path / "no_markers.md"
    no_markers.write_text("# Hello World\nNo markers here.", encoding="utf-8")
    assert update_readme(no_markers) is False

    nonexistent_const = tmp_path / "const.py"
    assert extract_gateway_models_from_const(nonexistent_const) == []

