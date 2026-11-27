"""Test repair issues management for MyHOME integration."""
from unittest.mock import patch

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.helpers import issue_registry as ir

from custom_components.myhome.const import DOMAIN
from custom_components.myhome.repairs import (
    ISSUE_BUS_COLLISION,
    ISSUE_GATEWAY_AUTH,
    async_create_auth_issue,
    async_create_collision_issue,
    async_delete_auth_issue,
    async_delete_collision_issue,
)


async def test_auth_repair_issue_lifecycle(hass: HomeAssistant) -> None:
    """Test creating and deleting an authentication repair issue."""
    issue_registry = ir.async_get(hass)
    entry_id = "test_entry_123"
    issue_id = f"{ISSUE_GATEWAY_AUTH}_{entry_id}"

    # Initially no issue
    assert issue_registry.async_get_issue(DOMAIN, issue_id) is None

    # Create auth issue
    async_create_auth_issue(hass, entry_id, "TestGateway")
    issue = issue_registry.async_get_issue(DOMAIN, issue_id)
    assert issue is not None
    assert issue.domain == DOMAIN
    assert issue.issue_id == issue_id
    assert issue.severity == ir.IssueSeverity.ERROR

    # Delete auth issue
    async_delete_auth_issue(hass, entry_id)
    assert issue_registry.async_get_issue(DOMAIN, issue_id) is None


async def test_collision_repair_issue_lifecycle(hass: HomeAssistant) -> None:
    """Test creating and deleting a bus collision repair issue."""
    issue_registry = ir.async_get(hass)
    entry_id = "test_entry_456"
    issue_id = f"{ISSUE_BUS_COLLISION}_{entry_id}"

    # Initially no issue
    assert issue_registry.async_get_issue(DOMAIN, issue_id) is None

    # Create collision issue
    async_create_collision_issue(hass, entry_id, 42)
    issue = issue_registry.async_get_issue(DOMAIN, issue_id)
    assert issue is not None
    assert issue.domain == DOMAIN
    assert issue.issue_id == issue_id
    assert issue.severity == ir.IssueSeverity.WARNING

    # Delete collision issue
    async_delete_collision_issue(hass, entry_id)
    assert issue_registry.async_get_issue(DOMAIN, issue_id) is None


async def test_identity_repair_issues_lifecycle(hass: HomeAssistant) -> None:
    """Identity mismatch (ask) and identity corrected (inform) issues are created with their placeholders."""
    from custom_components.myhome.repairs import (
        ISSUE_GATEWAY_IDENTITY,
        ISSUE_GATEWAY_IDENTITY_CORRECTED,
        async_create_identity_corrected_issue,
        async_create_identity_issue,
        async_delete_identity_issue,
    )

    issue_registry = ir.async_get(hass)
    entry_id = "entry_identity"

    async_create_identity_issue(hass, entry_id, "F454", "MyHomeServer1", "200", "manual", False)
    issue = issue_registry.async_get_issue(DOMAIN, f"{ISSUE_GATEWAY_IDENTITY}_{entry_id}")
    assert issue is not None and issue.severity == ir.IssueSeverity.WARNING
    assert issue.translation_placeholders["reported"] == "MyHomeServer1"
    assert issue.translation_placeholders["basis"].startswith("field evidence")

    async_create_identity_issue(hass, entry_id, "F454", "MH200", "4", "ssdp", True)
    issue = issue_registry.async_get_issue(DOMAIN, f"{ISSUE_GATEWAY_IDENTITY}_{entry_id}")
    assert issue.translation_placeholders["basis"] == "the OpenWebNet specification"

    async_delete_identity_issue(hass, entry_id)
    assert issue_registry.async_get_issue(DOMAIN, f"{ISSUE_GATEWAY_IDENTITY}_{entry_id}") is None

    async_create_identity_corrected_issue(hass, entry_id, "F454", "F452", "6")
    issue = issue_registry.async_get_issue(DOMAIN, f"{ISSUE_GATEWAY_IDENTITY_CORRECTED}_{entry_id}")
    assert issue is not None
    assert issue.translation_placeholders == {"previous": "F454", "corrected": "F452", "code": "6"}


async def test_unknown_model_issues_lifecycle(hass: HomeAssistant) -> None:
    """Test the unknown model repair issue lifecycle."""
    from custom_components.myhome.repairs import (
        ISSUE_UNKNOWN_GATEWAY_MODEL,
        async_create_unknown_model_issue,
        async_delete_unknown_model_issue,
    )

    issue_registry = ir.async_get(hass)
    entry_id = "entry_unknown"

    async_create_unknown_model_issue(hass, entry_id, "999")
    issue = issue_registry.async_get_issue(DOMAIN, f"{ISSUE_UNKNOWN_GATEWAY_MODEL}_{entry_id}")
    assert issue is not None
    assert issue.severity == ir.IssueSeverity.WARNING
    assert issue.translation_key == ISSUE_UNKNOWN_GATEWAY_MODEL
    assert issue.translation_placeholders["code"] == "999"
    assert not issue.is_fixable
    assert issue.learn_more_url == "https://github.com/OpenWebNet-HA/MyHOME/issues/new?template=device_request.yml"

    async_delete_unknown_model_issue(hass, entry_id)
    assert issue_registry.async_get_issue(DOMAIN, f"{ISSUE_UNKNOWN_GATEWAY_MODEL}_{entry_id}") is None


async def test_unconfigured_timezone_issues_lifecycle(hass: HomeAssistant) -> None:
    """Test the unconfigured timezone repair issue lifecycle."""
    from custom_components.myhome.repairs import (
        ISSUE_UNCONFIGURED_TIMEZONE,
        async_create_unconfigured_timezone_issue,
        async_delete_unconfigured_timezone_issue,
    )

    issue_registry = ir.async_get(hass)
    entry_id = "entry_tz"

    async_create_unconfigured_timezone_issue(hass, entry_id, "Mock Gateway")
    issue = issue_registry.async_get_issue(DOMAIN, f"{ISSUE_UNCONFIGURED_TIMEZONE}_{entry_id}")
    assert issue is not None
    assert issue.severity == ir.IssueSeverity.WARNING
    assert issue.translation_key == ISSUE_UNCONFIGURED_TIMEZONE
    assert not issue.is_fixable
    assert issue.translation_placeholders == {"gateway": "Mock Gateway"}
    assert (
        issue.learn_more_url
        == "https://openwebnet-ha.github.io/MyHOME/latest/diagnostics/repair-issues/#unconfigured-timezone"
    )

    async_delete_unconfigured_timezone_issue(hass, entry_id)
    assert issue_registry.async_get_issue(DOMAIN, f"{ISSUE_UNCONFIGURED_TIMEZONE}_{entry_id}") is None


async def test_incompatible_decoder_issue_lifecycle(hass: HomeAssistant) -> None:
    """Test the incompatible decoder repair issue lifecycle."""
    from custom_components.myhome.repairs import (
        ISSUE_INCOMPATIBLE_DECODER,
        async_create_incompatible_decoder_issue,
        async_delete_incompatible_decoder_issue,
    )

    issue_registry = ir.async_get(hass)
    entry_id = "entry_dec"
    decoder_id = "media_player.cambridge_cxn"

    async_create_incompatible_decoder_issue(hass, entry_id, decoder_id, "cambridge_audio")
    slug_id = decoder_id.replace(".", "_")
    issue_id = f"{ISSUE_INCOMPATIBLE_DECODER}_{entry_id}_{slug_id}"
    issue = issue_registry.async_get_issue(DOMAIN, issue_id)
    assert issue is not None
    assert issue.severity == ir.IssueSeverity.WARNING
    assert issue.translation_key == ISSUE_INCOMPATIBLE_DECODER
    assert issue.is_fixable
    assert issue.translation_placeholders == {"decoder": decoder_id, "platform": "cambridge_audio"}
    assert (
        issue.learn_more_url
        == "https://openwebnet-ha.github.io/MyHOME/latest/configuration/use_cases/#music-assistant"
    )

    async_delete_incompatible_decoder_issue(hass, entry_id, decoder_id)
    assert issue_registry.async_get_issue(DOMAIN, issue_id) is None


@pytest.mark.asyncio
async def test_incompatible_decoder_repair_flow_confirm(hass: HomeAssistant) -> None:
    """Test the repair flow adopts DLNA DMR companion when present."""
    from homeassistant.helpers import device_registry as dr
    from homeassistant.helpers import entity_registry as er
    from pytest_homeassistant_custom_component.common import MockConfigEntry

    from custom_components.myhome.const import CONF_DECODER_ENTITY
    from custom_components.myhome.repairs import async_create_fix_flow

    cam_entry = MockConfigEntry(domain="cambridge_audio")
    cam_entry.add_to_hass(hass)

    ent_reg = er.async_get(hass)
    dev_reg = dr.async_get(hass)

    device = dev_reg.async_get_or_create(
        config_entry_id=cam_entry.entry_id,
        identifiers={("cambridge_audio", "cxn_id")},
    )
    ent_reg.async_get_or_create(
        "media_player", "cambridge_audio", "cxn_id", device_id=device.id, suggested_object_id="streamer"
    )
    ent_reg.async_get_or_create(
        "media_player", "dlna_dmr", "cxn_dlna_id", device_id=device.id, suggested_object_id="streamer_dlna"
    )

    entry = MockConfigEntry(
        domain=DOMAIN,
        title="MyHome Gateway",
        options={CONF_DECODER_ENTITY.format(1): "media_player.streamer"},
    )
    entry.add_to_hass(hass)

    flow = await async_create_fix_flow(
        hass,
        "incompatible_decoder_platform_test_gw",
        {"entry_id": entry.entry_id, "decoder_id": "media_player.streamer", "platform": "cambridge_audio"},
    )
    result = await flow.async_step_init()
    assert result["type"] == "form"
    assert result["step_id"] == "confirm_companion"
    assert result["description_placeholders"]["companion"] == "media_player.streamer_dlna"

    # User confirms the fix
    fix_result = await flow.async_step_confirm_companion(user_input={})
    assert fix_result["type"] == "create_entry"

    # Verify options entry was NOT modified
    assert entry.options[CONF_DECODER_ENTITY.format(1)] == "media_player.streamer"


@pytest.mark.asyncio
async def test_incompatible_decoder_repair_flow_missing_companion(hass: HomeAssistant) -> None:
    """Test the repair flow shows missing instructions when no DLNA companion is found."""
    from custom_components.myhome.repairs import async_create_fix_flow

    flow = await async_create_fix_flow(
        hass,
        "incompatible_decoder_platform_test_gw",
        {"entry_id": "mock_entry", "decoder_id": "media_player.unknown", "platform": "cambridge_audio"},
    )
    result = await flow.async_step_init()
    assert result["type"] == "form"
    assert result["step_id"] == "missing_companion"

    # Submitting without DLNA configured aborts with companion_still_missing
    abort_result = await flow.async_step_missing_companion(user_input={})
    assert abort_result["type"] == "abort"
    assert abort_result["reason"] == "companion_still_missing"

    # Retrying when companion is now discovered transitions to confirm_companion
    with patch(
        "custom_components.myhome.decoder_companion.async_find_streaming_companion",
        return_value="media_player.found_dlna",
    ):
        retry_result = await flow.async_step_missing_companion(user_input={})
        assert retry_result["type"] == "form"
        assert retry_result["step_id"] == "confirm_companion"


@pytest.mark.asyncio
async def test_create_fix_flow_fallback_confirm(hass: HomeAssistant) -> None:
    """Test creating fix flow for non-decoder issue falls back to ConfirmRepairFlow."""
    from homeassistant.components.repairs import ConfirmRepairFlow

    from custom_components.myhome.repairs import async_create_fix_flow

    flow = await async_create_fix_flow(hass, "other_generic_issue", None)
    assert isinstance(flow, ConfirmRepairFlow)
    assert flow.hass is hass


@pytest.mark.asyncio
async def test_prune_incompatible_decoder_issues(hass: HomeAssistant) -> None:
    """Test pruning incompatible decoder issues for decoders no longer configured."""
    from custom_components.myhome.repairs import (
        async_create_incompatible_decoder_issue,
        async_prune_incompatible_decoder_issues,
    )
    issue_registry = ir.async_get(hass)
    entry_id = "test_entry_prune"

    # Create issues for decoder_1 and decoder_2
    async_create_incompatible_decoder_issue(
        hass, entry_id, "media_player.dec1", "cambridge_audio"
    )
    async_create_incompatible_decoder_issue(
        hass, entry_id, "media_player.dec2", "cambridge_audio"
    )

    issue1_id = f"incompatible_decoder_platform_{entry_id}_media_player_dec1"
    issue2_id = f"incompatible_decoder_platform_{entry_id}_media_player_dec2"
    assert issue_registry.async_get_issue(DOMAIN, issue1_id) is not None
    assert issue_registry.async_get_issue(DOMAIN, issue2_id) is not None

    # Prune keeping only dec1
    async_prune_incompatible_decoder_issues(hass, entry_id, ["media_player.dec1"])
    assert issue_registry.async_get_issue(DOMAIN, issue1_id) is not None
    assert issue_registry.async_get_issue(DOMAIN, issue2_id) is None


@pytest.mark.asyncio
async def test_shared_bus_repair_flow(hass: HomeAssistant) -> None:
    """Test automated resolution of shared bus repair issue applies recommended topology."""
    from unittest.mock import AsyncMock, patch

    from pytest_homeassistant_custom_component.common import MockConfigEntry

    from custom_components.myhome.const import (
        CONF_BUS_TOPOLOGY,
        CONF_DELEGATED_WHOS,
        CONF_GATEWAY_ROLE,
        CONF_PRIMARY_GATEWAY,
        ROLE_PRIMARY,
        ROLE_SECONDARY,
        TOPOLOGY_SHARED,
    )
    from custom_components.myhome.repairs import (
        async_create_fix_flow,
        async_create_shared_bus_issue,
    )
    from custom_components.myhome.topology import gateway_supported_whos

    pri_entry = MockConfigEntry(
        domain=DOMAIN,
        title="MyHomeServer1 Gateway",
        data={"name": "MyHomeServer1", "mac": "00:03:50:aa:bb:01"},
        options={},
    )
    sec_entry = MockConfigEntry(
        domain=DOMAIN,
        title="H4890 Gateway",
        data={"name": "H4890", "mac": "00:03:50:aa:bb:02"},
        options={},
    )
    pri_entry.add_to_hass(hass)
    sec_entry.add_to_hass(hass)

    async_create_shared_bus_issue(hass, "00:03:50:aa:bb:01", "00:03:50:aa:bb:02")

    flow = await async_create_fix_flow(
        hass,
        "shared_bus_detected_000350aabb01_000350aabb02",
        {"mac_a": "00:03:50:aa:bb:01", "mac_b": "00:03:50:aa:bb:02"},
    )
    result = await flow.async_step_init()
    assert result["type"] == "form"
    assert "MyHomeServer1" in result["description_placeholders"]["primary"]
    assert "H4890" in result["description_placeholders"]["secondary"]
    assert result["description_placeholders"]["role"] == "Secondary"

    with patch.object(hass.config_entries, "async_reload", AsyncMock()) as mock_reload:
        fix_result = await flow.async_step_init(user_input={})
        assert fix_result["type"] == "create_entry"
        assert mock_reload.call_count == 2

    assert pri_entry.options[CONF_BUS_TOPOLOGY] == TOPOLOGY_SHARED
    assert pri_entry.options[CONF_GATEWAY_ROLE] == ROLE_PRIMARY
    assert sec_entry.options[CONF_BUS_TOPOLOGY] == TOPOLOGY_SHARED
    assert sec_entry.options[CONF_GATEWAY_ROLE] == ROLE_SECONDARY
    assert sec_entry.options[CONF_PRIMARY_GATEWAY] == "00:03:50:aa:bb:01"
    expected_whos = {5, 16, 22} if 5 in gateway_supported_whos("H4890") else {16, 22}
    assert set(sec_entry.options[CONF_DELEGATED_WHOS]) == expected_whos

    # Verify repair issue was dismissed
    assert ir.async_get(hass).async_get_issue(DOMAIN, "shared_bus_detected_000350aabb01_000350aabb02") is None


@pytest.mark.asyncio
async def test_shared_bus_repair_flow_missing_gateway(hass: HomeAssistant) -> None:
    """Test repair flow aborts if a gateway is missing."""
    from custom_components.myhome.repairs import async_create_fix_flow

    flow = await async_create_fix_flow(
        hass,
        "shared_bus_detected_000350aabb01_000350aabb02",
        {"mac_a": "00:03:50:ff:ff:01", "mac_b": "00:03:50:ff:ff:02"},
    )
    result = await flow.async_step_init()
    assert result["type"] == "abort"
    assert result["reason"] == "gateway_missing"


@pytest.mark.asyncio
async def test_repair_issue_canonical_manual_urls(hass: HomeAssistant) -> None:
    """Verify topology and decoder config repair issues point to canonical /MyHOME/latest/ manual URLs."""
    from custom_components.myhome.const import (
        ISSUE_GATEWAY_FAILOVER,
        ISSUE_PRIMARY_GATEWAY_MISSING,
        ISSUE_SHARED_BUS_DETECTED,
    )
    from custom_components.myhome.repairs import (
        ISSUE_INVALID_DECODER,
        async_create_decoder_config_issue,
        async_create_failover_issue,
        async_create_primary_missing_issue,
        async_create_shared_bus_issue,
    )

    reg = ir.async_get(hass)

    async_create_shared_bus_issue(hass, "00:03:50:aa:bb:01", "00:03:50:aa:bb:02")
    shared_issue = reg.async_get_issue(
        DOMAIN, f"{ISSUE_SHARED_BUS_DETECTED}_000350aabb01_000350aabb02"
    )
    assert shared_issue is not None
    assert (
        shared_issue.learn_more_url
        == "https://openwebnet-ha.github.io/MyHOME/latest/diagnostics/repair-issues/#unconfigured-shared-bus-detected"
    )

    async_create_failover_issue(
        hass, "00:03:50:aa:bb:01", "00:03:50:aa:bb:02", "Primary GW", "Standby GW"
    )
    failover_issue = reg.async_get_issue(DOMAIN, f"{ISSUE_GATEWAY_FAILOVER}_000350aabb01")
    assert failover_issue is not None
    assert (
        failover_issue.learn_more_url
        == "https://openwebnet-ha.github.io/MyHOME/latest/diagnostics/repair-issues/#gateway-failover-active-warm-standby-high-availability"
    )

    async_create_primary_missing_issue(hass, "sec_entry", "Secondary GW", "00:03:50:aa:bb:01")
    missing_pri = reg.async_get_issue(DOMAIN, f"{ISSUE_PRIMARY_GATEWAY_MISSING}_sec_entry")
    assert missing_pri is not None
    assert (
        missing_pri.learn_more_url
        == "https://openwebnet-ha.github.io/MyHOME/latest/diagnostics/repair-issues/#primary-gateway-missing"
    )

    async_create_decoder_config_issue(
        hass, "dec_entry", "media_player.loop_zone", ISSUE_INVALID_DECODER, {"platform": "myhome"}
    )
    dec_issue = reg.async_get_issue(
        DOMAIN, f"{ISSUE_INVALID_DECODER}_dec_entry_media_player_loop_zone"
    )
    assert dec_issue is not None
    assert (
        dec_issue.learn_more_url
        == "https://openwebnet-ha.github.io/MyHOME/latest/configuration/use_cases/#music-assistant"
    )


@pytest.mark.asyncio
async def test_cover_manual_calibration_repair_flow_guided_start_stop(hass: HomeAssistant) -> None:
    """Guided Start -> Stop repair flow disables early timer settling, saves manual travel times, and clears issue."""
    from unittest.mock import AsyncMock, MagicMock

    from homeassistant.helpers import entity_registry as er
    from pytest_homeassistant_custom_component.common import MockConfigEntry

    from custom_components.myhome.const import CONF_COVER_TRAVEL_TIMES
    from custom_components.myhome.cover import MyHOMECover
    from custom_components.myhome.data import MyHOMERuntimeData
    from custom_components.myhome.repairs import (
        ISSUE_COVER_CALIBRATION_CUTOFF,
        CoverManualCalibrationRepairFlow,
        async_create_cover_cutoff_issue,
        async_create_fix_flow,
    )

    mac = "00:03:50:00:00:01"
    entry = MockConfigEntry(domain=DOMAIN, data={"mac": mac}, unique_id=mac, options={})
    entry.add_to_hass(hass)

    gw = MagicMock()
    gw.mac = mac
    gw.log_id = "[Test GW]"
    gw.available = True
    gw.config_entry = entry

    cover = MyHOMECover(
        hass=hass,
        name="Gordijn woonkamer",
        entity_name=None,
        device_id="22#4#02",
        who="2",
        where="22",
        interface="02",
        advanced=False,
        manufacturer="BTicino",
        model="Shutter / Cover",
        gateway=gw,
        travel_time=25,
    )
    cover.hass = hass
    cover.entity_id = "cover.gordijn_woonkamer"
    cover.async_write_ha_state = MagicMock()
    cover.async_close_cover = AsyncMock()
    cover.async_open_cover = AsyncMock()
    cover.async_stop_cover = AsyncMock()

    runtime = MyHOMERuntimeData(gateway=gw)
    runtime.entities["cover"] = {"22#4#02": cover}
    entry.runtime_data = runtime

    ent_reg = er.async_get(hass)
    ent_reg.async_get_or_create(
        "cover",
        DOMAIN,
        f"{mac}-2-22#4#02",
        suggested_object_id="gordijn_woonkamer",
        config_entry=entry,
    )

    async def _svc_set_travel_time(call) -> None:
        await cover.async_set_travel_time(
            travel_time_down=call.data.get("travel_time_down"),
            travel_time_up=call.data.get("travel_time_up"),
        )

    hass.services.async_register(DOMAIN, "set_cover_travel_time", _svc_set_travel_time)

    async_create_cover_cutoff_issue(
        hass, entry.entry_id, "22#4#02", cover.entity_id, "Gordijn woonkamer", 60.0, 60.9
    )
    issue_id = f"{ISSUE_COVER_CALIBRATION_CUTOFF}_{entry.entry_id}_22_4_02"
    issue = ir.async_get(hass).async_get_issue(DOMAIN, issue_id)
    assert issue is not None and issue.is_fixable

    flow = await async_create_fix_flow(hass, issue_id, issue.data)
    assert isinstance(flow, CoverManualCalibrationRepairFlow)

    # 1. Menu step
    step = await flow.async_step_init()
    assert step["type"] == "menu"
    assert step["step_id"] == "init"
    assert step["menu_options"] == ["timed_close_ready", "manual_entry", "reset_default"]
    assert step["description_placeholders"]["name"] == "Gordijn woonkamer"

    # 2. Close ready -> start closing (disables timer settle)
    step = await flow.async_step_timed_close_ready()
    assert step["type"] == "form" and step["step_id"] == "timed_close_ready"
    step = await flow.async_step_timed_close_ready(user_input={})
    assert step["type"] == "form" and step["step_id"] == "timed_close_stop"
    assert cover._settles_on_timer is False
    cover.async_close_cover.assert_awaited_once()

    # 3. Close stop -> bus-anchored last_run_seconds captured & timer settle restored
    cover._last_run = {"seconds": 38.4, "direction": "close", "ended_at": "2026-11-27T12:00:38.400+00:00"}
    step = await flow.async_step_timed_close_stop(user_input={})
    assert step["type"] == "form" and step["step_id"] == "timed_open_ready"
    assert step["description_placeholders"]["down"] == "38.4"
    assert cover._settles_on_timer is True
    cover.async_stop_cover.assert_awaited_once()

    # 4. Open ready -> start opening -> open stop -> confirm save
    step = await flow.async_step_timed_open_ready(user_input={"reuse_down_for_up": False})
    assert step["type"] == "form" and step["step_id"] == "timed_open_stop"
    assert cover._settles_on_timer is False
    cover.async_open_cover.assert_awaited_once()

    cover._last_run = {"seconds": 40.2, "direction": "open", "ended_at": "2026-11-27T12:01:20.200+00:00"}
    step = await flow.async_step_timed_open_stop(user_input={})
    assert step["type"] == "form" and step["step_id"] == "confirm_save"
    assert step["description_placeholders"]["down"] == "38.4"
    assert step["description_placeholders"]["up"] == "40.2"
    assert cover._settles_on_timer is True

    # 5. Confirm save persists source='manual' and clears repair issue
    done = await flow.async_step_confirm_save(
        user_input={"travel_time_down": 38.4, "travel_time_up": 40.2}
    )
    assert done["type"] == "create_entry"
    saved = entry.options[CONF_COVER_TRAVEL_TIMES]["22#4#02"]
    assert saved["source"] == "manual"
    assert saved["down"] == pytest.approx(38.4)
    assert saved["up"] == pytest.approx(40.2)
    assert ir.async_get(hass).async_get_issue(DOMAIN, issue_id) is None

    # 6. Also verify reuse_down_for_up=True jumps directly to confirm_save
    flow_reuse = await async_create_fix_flow(hass, issue_id, issue.data)
    flow_reuse._measured_down = 33.5
    step_reuse = await flow_reuse.async_step_timed_open_ready(user_input={"reuse_down_for_up": True})
    assert step_reuse["type"] == "form" and step_reuse["step_id"] == "confirm_save"
    assert step_reuse["description_placeholders"]["down"] == "33.5"
    assert step_reuse["description_placeholders"]["up"] == "33.5"


@pytest.mark.asyncio
async def test_cover_manual_calibration_repair_flow_manual_and_reset(hass: HomeAssistant) -> None:
    """Repair flow supports numeric manual entry (with validation), reset to default, and missing-cover abort."""
    from homeassistant.helpers import entity_registry as er
    from pytest_homeassistant_custom_component.common import MockConfigEntry

    from custom_components.myhome.repairs import (
        ISSUE_COVER_LEGACY_CUTOFF,
        async_create_fix_flow,
        async_sync_legacy_cover_cutoff_issues,
    )

    mac = "00:03:50:00:00:02"
    entry = MockConfigEntry(domain=DOMAIN, data={"mac": mac}, unique_id=mac)
    entry.add_to_hass(hass)

    ent_reg = er.async_get(hass)
    cov_ent = ent_reg.async_get_or_create(
        "cover", DOMAIN, f"{mac}-2-21", suggested_object_id="gordijn_badkamer", config_entry=entry
    )
    hass.states.async_set(
        cov_ent.entity_id,
        "open",
        {"friendly_name": "Gordijn badkamer", "travel_time_down": 61.0, "travel_time_up": 61.0},
    )

    set_calls: list[dict] = []
    reset_calls: list[dict] = []

    async def _svc_set(call) -> None:
        set_calls.append(dict(call.data))

    async def _svc_reset(call) -> None:
        reset_calls.append(dict(call.data))

    hass.services.async_register(DOMAIN, "set_cover_travel_time", _svc_set)
    hass.services.async_register(DOMAIN, "reset_cover_travel_time", _svc_reset)

    async_sync_legacy_cover_cutoff_issues(
        hass,
        entry.entry_id,
        [{"device_id": "21", "entity_id": cov_ent.entity_id, "name": "Gordijn badkamer", "down": 61.0, "up": 61.0}],
    )
    issue_id = f"{ISSUE_COVER_LEGACY_CUTOFF}_{entry.entry_id}_21"
    issue = ir.async_get(hass).async_get_issue(DOMAIN, issue_id)
    assert issue is not None and issue.is_fixable

    # Manual entry: invalid input -> form error, valid input -> service called & issue deleted
    flow = await async_create_fix_flow(hass, issue_id, issue.data)
    form = await flow.async_step_manual_entry()
    assert form["type"] == "form" and form["step_id"] == "manual_entry"

    invalid = await flow.async_step_manual_entry(
        user_input={"travel_time_down": 0.2, "travel_time_up": 20.0}
    )
    assert invalid["type"] == "form"
    assert invalid["errors"] == {"base": "invalid_travel_time"}
    assert set_calls == []

    too_high = await flow.async_step_manual_entry(
        user_input={"travel_time_down": 350.0, "travel_time_up": 20.0}
    )
    assert too_high["type"] == "form"
    assert too_high["errors"] == {"base": "invalid_travel_time"}
    assert set_calls == []

    done = await flow.async_step_manual_entry(
        user_input={"travel_time_down": 35.0, "travel_time_up": 37.5}
    )
    assert done["type"] == "create_entry"
    assert set_calls == [
        {"entity_id": cov_ent.entity_id, "travel_time_down": 35.0, "travel_time_up": 37.5}
    ]
    assert ir.async_get(hass).async_get_issue(DOMAIN, issue_id) is None

    # Reset to default path
    async_sync_legacy_cover_cutoff_issues(
        hass,
        entry.entry_id,
        [{"device_id": "21", "entity_id": cov_ent.entity_id, "name": "Gordijn badkamer", "down": 61.0, "up": 61.0}],
    )
    issue = ir.async_get(hass).async_get_issue(DOMAIN, issue_id)
    assert issue is not None
    flow_reset = await async_create_fix_flow(hass, issue_id, issue.data)
    confirm = await flow_reset.async_step_reset_default()
    assert confirm["type"] == "form" and confirm["step_id"] == "reset_default"
    done_reset = await flow_reset.async_step_reset_default(user_input={})
    assert done_reset["type"] == "create_entry"
    assert reset_calls == [{"entity_id": cov_ent.entity_id}]
    assert ir.async_get(hass).async_get_issue(DOMAIN, issue_id) is None

    # Missing cover aborts cleanly
    missing_flow = await async_create_fix_flow(
        hass,
        f"{ISSUE_COVER_LEGACY_CUTOFF}_{entry.entry_id}_99",
        {"entry_id": entry.entry_id, "device_id": "99", "entity_id": "cover.missing", "cover_name": "Missing"},
    )
    aborted = await missing_flow.async_step_init()
    assert aborted["type"] == "abort" and aborted["reason"] == "cover_missing"


@pytest.mark.asyncio
async def test_cover_manual_calibration_repair_flow_cancel_stops_motor(hass: HomeAssistant) -> None:
    """Dismissing the repair modal mid-run immediately restores _settles_on_timer and stops the motor."""
    from unittest.mock import AsyncMock, MagicMock

    from pytest_homeassistant_custom_component.common import MockConfigEntry

    from custom_components.myhome.cover import MyHOMECover
    from custom_components.myhome.data import MyHOMERuntimeData
    from custom_components.myhome.repairs import (
        ISSUE_COVER_CALIBRATION_CUTOFF,
        async_create_fix_flow,
    )

    mac = "00:03:50:00:00:03"
    entry = MockConfigEntry(domain=DOMAIN, data={"mac": mac}, unique_id=mac)
    entry.add_to_hass(hass)

    gw = MagicMock()
    gw.mac = mac
    gw.log_id = "[Test GW]"
    gw.config_entry = entry

    cover = MyHOMECover(
        hass=hass,
        name="Living Cover",
        entity_name=None,
        device_id="21",
        who="2",
        where="21",
        interface=None,
        advanced=False,
        manufacturer="BTicino",
        model="Shutter / Cover",
        gateway=gw,
    )
    cover.hass = hass
    cover.entity_id = "cover.living_cover"
    cover.async_close_cover = AsyncMock()
    cover.async_stop_cover = AsyncMock()

    runtime = MyHOMERuntimeData(gateway=gw)
    runtime.entities["cover"] = {"21": cover}
    entry.runtime_data = runtime

    flow = await async_create_fix_flow(
        hass,
        f"{ISSUE_COVER_CALIBRATION_CUTOFF}_{entry.entry_id}_21",
        {"entry_id": entry.entry_id, "device_id": "21", "entity_id": cover.entity_id, "cover_name": "Living Cover"},
    )
    step = await flow.async_step_timed_close_ready(user_input={})
    assert step["step_id"] == "timed_close_stop"
    assert cover._settles_on_timer is False
    assert flow._moving is True

    # Simulate dismissing the repair dialog via the close button (X)
    flow.async_remove()
    assert cover._settles_on_timer is True
    assert flow._moving is False
    await hass.async_block_till_done()
    cover.async_stop_cover.assert_awaited_once()


