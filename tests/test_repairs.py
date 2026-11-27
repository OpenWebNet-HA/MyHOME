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


def _make_real_cover_repair_env(
    hass: HomeAssistant,
    *,
    mac: str = "00:03:50:00:00:01",
    device_id: str = "22#4#02",
    where: str = "22",
    interface: str | None = "02",
    name: str = "Gordijn woonkamer",
    object_id: str = "gordijn_woonkamer",
    options: dict | None = None,
    calibration: dict | None = None,
):
    """Build a real MyHOMECover + MockConfigEntry + fake bus gateway for RepairFlow tests."""
    import asyncio
    import time
    from unittest.mock import AsyncMock, MagicMock

    from homeassistant.helpers import entity_registry as er
    from pytest_homeassistant_custom_component.common import MockConfigEntry

    from custom_components.myhome.cover import MyHOMECover
    from custom_components.myhome.data import MyHOMERuntimeData

    entry = MockConfigEntry(
        domain=DOMAIN, data={"mac": mac}, unique_id=mac, options=options or {}
    )
    entry.add_to_hass(hass)

    gw = MagicMock()
    gw.mac = mac
    gw.log_id = "[Test GW]"
    gw.available = True
    gw.is_who_available = lambda _who: bool(gw.available)
    gw.availability_signal = f"myhome_avail_{mac}"
    gw.device_registry_id = None
    gw.config_entry = entry
    gw.deliveries = []

    async def _send(message):
        fut = asyncio.get_running_loop().create_future()
        fut.set_result(time.monotonic())
        gw.deliveries.append((str(message), fut))
        return fut

    gw.send = AsyncMock(side_effect=_send)
    gw.send_status_request = AsyncMock()

    cover = MyHOMECover(
        hass=hass,
        name=name,
        entity_name=None,
        device_id=device_id,
        who="2",
        where=where,
        interface=interface,
        advanced=False,
        manufacturer="BTicino",
        model="Shutter / Cover",
        gateway=gw,
        travel_time=25,
        calibration=calibration,
    )
    cover.hass = hass
    ent_reg = er.async_get(hass)
    reg_entry = ent_reg.async_get_or_create(
        "cover",
        DOMAIN,
        f"{mac}-2-{device_id}",
        suggested_object_id=object_id,
        config_entry=entry,
    )
    cover.entity_id = reg_entry.entity_id
    cover.registry_entry = reg_entry
    cover.async_write_ha_state = MagicMock()
    cover.async_schedule_update_ha_state = MagicMock()

    runtime = MyHOMERuntimeData(gateway=gw)
    runtime.entities["cover"] = {device_id: cover}
    entry.runtime_data = runtime
    return entry, gw, cover


@pytest.mark.asyncio
async def test_cover_manual_calibration_repair_flow_guided_start_stop(hass: HomeAssistant) -> None:
    """Guided Start -> Stop drives a real MyHOMECover, skips idle stop, clamps >300s, saves manual, and dismisses notification."""
    from homeassistant.components import persistent_notification
    from OWNd.message import OWNEvent

    from custom_components.myhome.const import CONF_COVER_TRAVEL_TIMES
    from custom_components.myhome.repairs import (
        ISSUE_COVER_CALIBRATION_CUTOFF,
        CoverManualCalibrationRepairFlow,
        async_create_cover_cutoff_issue,
        async_create_fix_flow,
        cover_calibration_notification_id,
    )

    entry, gw, cover = _make_real_cover_repair_env(hass)
    notif_id = cover_calibration_notification_id(entry.entry_id, "22#4#02")

    async_create_cover_cutoff_issue(
        hass, entry.entry_id, "22#4#02", cover.entity_id, "Gordijn woonkamer", 60.0, 60.9
    )
    issue_id = f"{ISSUE_COVER_CALIBRATION_CUTOFF}_{entry.entry_id}_22_4_02"
    issue = ir.async_get(hass).async_get_issue(DOMAIN, issue_id)
    assert issue is not None and issue.is_fixable

    flow = await async_create_fix_flow(hass, issue_id, issue.data)
    assert isinstance(flow, CoverManualCalibrationRepairFlow)

    # 1. Menu step uses localized placeholders {name, seconds, limit} (no hardcoded English {detail})
    step = await flow.async_step_init()
    assert step["type"] == "menu"
    assert step["step_id"] == "init"
    assert step["menu_options"] == ["timed_close_ready", "manual_entry", "reset_default"]
    assert step["description_placeholders"] == {
        "name": "Gordijn woonkamer",
        "seconds": "60.9",
        "limit": "60",
    }

    # 2. Close ready -> real async_close_cover queues *2*2*22#4#02## with _settles_on_timer=False
    step = await flow.async_step_timed_close_ready()
    assert step["type"] == "form" and step["step_id"] == "timed_close_ready"
    step = await flow.async_step_timed_close_ready(user_input={})
    assert step["type"] == "form" and step["step_id"] == "timed_close_stop"
    assert cover._settles_on_timer is False
    assert cover._manual_timing_owner is flow
    assert cover.is_closing is True
    assert [f for f, _ in gw.deliveries] == ["*2*2*22#4#02##"]

    # 3. Bus motor-start echo anchors run; Submit while moving sends *2*0*22#4#02## and captures bus run
    with patch("custom_components.myhome.cover.time.monotonic", return_value=100.0):
        cover.handle_event(OWNEvent.parse("*2*2*22#4#02##"))
    with patch("custom_components.myhome.cover.time.monotonic", return_value=138.4):
        # Stop echo arrives right as user stops
        async def _send_stop_and_echo(message):
            import asyncio

            fut = asyncio.get_running_loop().create_future()
            fut.set_result(138.4)
            gw.deliveries.append((str(message), fut))
            if str(message) == "*2*0*22#4#02##":
                cover.handle_event(OWNEvent.parse("*2*0*22#4#02##"))
            return fut

        gw.send.side_effect = _send_stop_and_echo
        step = await flow.async_step_timed_close_stop(user_input={})

    assert step["type"] == "form" and step["step_id"] == "timed_open_ready"
    assert step["description_placeholders"]["down"] == "38.4"
    assert cover._settles_on_timer is True
    assert cover._manual_timing_owner is None
    assert [f for f, _ in gw.deliveries] == ["*2*2*22#4#02##", "*2*0*22#4#02##"]

    # 4. Open run where actuator already stopped on the bus BEFORE user clicks Submit -> skips redundant *2*0*
    step = await flow.async_step_timed_open_ready(user_input={"reuse_down_for_up": False})
    assert step["type"] == "form" and step["step_id"] == "timed_open_stop"
    assert cover._settles_on_timer is False
    assert cover.is_opening is True
    assert gw.deliveries[-1][0] == "*2*1*22#4#02##"

    with patch("custom_components.myhome.cover.time.monotonic", return_value=200.0):
        cover.handle_event(OWNEvent.parse("*2*1*22#4#02##"))
    with patch("custom_components.myhome.cover.time.monotonic", return_value=240.2):
        cover.handle_event(OWNEvent.parse("*2*0*22#4#02##"))
    assert cover.is_opening is False
    tx_count_before_idle_stop = len(gw.deliveries)

    step = await flow.async_step_timed_open_stop(user_input={})
    assert len(gw.deliveries) == tx_count_before_idle_stop  # zero redundant *2*0* sent!
    assert step["type"] == "form" and step["step_id"] == "confirm_save"
    assert step["description_placeholders"] == {
        "name": "Gordijn woonkamer",
        "down": "38.4",
        "up": "40.2",
    }
    assert cover._settles_on_timer is True

    # 5. Confirm save calls real cover.async_set_travel_time, clears issue, and dismisses notification
    with patch.object(persistent_notification, "async_dismiss") as mock_dismiss:
        done = await flow.async_step_confirm_save(
            user_input={"travel_time_down": 38.4, "travel_time_up": 40.2}
        )
        assert done["type"] == "create_entry"
        assert any(call.args[1] == notif_id for call in mock_dismiss.call_args_list)

    saved = entry.options[CONF_COVER_TRAVEL_TIMES]["22#4#02"]
    assert saved["source"] == "manual"
    assert saved["down"] == pytest.approx(38.4)
    assert saved["up"] == pytest.approx(40.2)
    assert ir.async_get(hass).async_get_issue(DOMAIN, issue_id) is None

    # 6. Verify >300s clamping on _fresh_last_run_seconds and reuse_down_for_up=True shortcut
    flow_clamp = await async_create_fix_flow(hass, issue_id, issue.data)
    cover._last_run = {"seconds": 450.0, "direction": "close", "ended_at": "new_ts"}
    assert flow_clamp._fresh_last_run_seconds(cover, cover.entity_id, "close") == 300.0
    flow_clamp._measured_down = 33.5
    step_reuse = await flow_clamp.async_step_timed_open_ready(user_input={"reuse_down_for_up": True})
    assert step_reuse["type"] == "form" and step_reuse["step_id"] == "confirm_save"
    assert step_reuse["description_placeholders"]["up"] == "33.5"

    await cover.async_will_remove_from_hass()


@pytest.mark.asyncio
async def test_cover_manual_calibration_repair_flow_manual_and_reset(hass: HomeAssistant) -> None:
    """Manual entry & reset drive a real MyHOMECover, ignore 61s cutoff prefill, surface custom error keys, and reject unloaded entities."""
    from homeassistant.exceptions import ServiceValidationError

    from custom_components.myhome.const import CONF_COVER_TRAVEL_TIMES, DEFAULT_TRAVEL_TIME
    from custom_components.myhome.repairs import (
        ISSUE_COVER_LEGACY_CUTOFF,
        async_create_fix_flow,
        async_sync_legacy_cover_cutoff_issues,
    )

    legacy_cal = {"down": 60.9, "up": 61.0}
    entry, _gw, cover = _make_real_cover_repair_env(
        hass,
        mac="00:03:50:00:00:02",
        device_id="21",
        where="21",
        interface=None,
        name="Gordijn badkamer",
        object_id="gordijn_badkamer",
        options={CONF_COVER_TRAVEL_TIMES: {"21": dict(legacy_cal)}},
        calibration=dict(legacy_cal),
    )

    async_sync_legacy_cover_cutoff_issues(
        hass,
        entry.entry_id,
        [{"device_id": "21", "entity_id": cover.entity_id, "name": "Gordijn badkamer", "down": 60.9, "up": 61.0}],
    )
    issue_id = f"{ISSUE_COVER_LEGACY_CUTOFF}_{entry.entry_id}_21"
    issue = ir.async_get(hass).async_get_issue(DOMAIN, issue_id)
    assert issue is not None and issue.is_fixable

    flow = await async_create_fix_flow(hass, issue_id, issue.data)
    init_step = await flow.async_step_init()
    assert init_step["description_placeholders"] == {
        "name": "Gordijn badkamer",
        "down": "60.9",
        "up": "61.0",
    }

    # Minor #2: cutoff values (60.9 / 61.0) are NOT pre-filled; falls back to 25.0 s default
    assert flow._default_travel_times(cover, cover.entity_id) == (
        float(DEFAULT_TRAVEL_TIME),
        float(DEFAULT_TRAVEL_TIME),
    )
    form = await flow.async_step_manual_entry()
    assert form["type"] == "form" and form["step_id"] == "manual_entry"

    invalid = await flow.async_step_manual_entry(
        user_input={"travel_time_down": 0.2, "travel_time_up": 20.0}
    )
    assert invalid["type"] == "form"
    assert invalid["errors"] == {"base": "invalid_travel_time"}

    too_high = await flow.async_step_manual_entry(
        user_input={"travel_time_down": 350.0, "travel_time_up": 20.0}
    )
    assert too_high["type"] == "form"
    assert too_high["errors"] == {"base": "invalid_travel_time"}

    # #374 bridge: custom ServiceValidationError translation_key surfaces directly
    with patch.object(
        cover,
        "async_set_travel_time",
        side_effect=ServiceValidationError(
            "Managed by panel",
            translation_domain=DOMAIN,
            translation_key="panel_timing_managed",
        ),
    ):
        custom_err = await flow.async_step_manual_entry(
            user_input={"travel_time_down": 30.0, "travel_time_up": 30.0}
        )
        assert custom_err["type"] == "form"
        assert custom_err["errors"] == {"base": "panel_timing_managed"}

    done = await flow.async_step_manual_entry(
        user_input={"travel_time_down": 35.0, "travel_time_up": 37.5}
    )
    assert done["type"] == "create_entry"
    assert entry.options[CONF_COVER_TRAVEL_TIMES]["21"]["source"] == "manual"
    assert entry.options[CONF_COVER_TRAVEL_TIMES]["21"]["down"] == pytest.approx(35.0)
    assert entry.options[CONF_COVER_TRAVEL_TIMES]["21"]["up"] == pytest.approx(37.5)
    assert ir.async_get(hass).async_get_issue(DOMAIN, issue_id) is None

    # Reset to default path on real MyHOMECover (works even if gateway is temporarily offline)
    cover._gateway_handler.available = False
    async_sync_legacy_cover_cutoff_issues(
        hass,
        entry.entry_id,
        [{"device_id": "21", "entity_id": cover.entity_id, "name": "Gordijn badkamer", "down": 61.0, "up": 61.0}],
    )
    issue = ir.async_get(hass).async_get_issue(DOMAIN, issue_id)
    assert issue is not None
    flow_reset = await async_create_fix_flow(hass, issue_id, issue.data)
    confirm = await flow_reset.async_step_reset_default()
    assert confirm["type"] == "form" and confirm["step_id"] == "reset_default"
    done_reset = await flow_reset.async_step_reset_default(user_input={})
    assert done_reset["type"] == "create_entry"
    assert "21" not in entry.options.get(CONF_COVER_TRAVEL_TIMES, {})
    assert cover._travel_time_down == float(DEFAULT_TRAVEL_TIME)
    assert ir.async_get(hass).async_get_issue(DOMAIN, issue_id) is None

    # M5: If cover entity is unloaded from runtime (even while present in registry/states), flow aborts cover_missing
    hass.states.async_set(cover.entity_id, "open", {"friendly_name": "Gordijn badkamer"})
    entry.runtime_data.entities["cover"].clear()
    unloaded_flow = await async_create_fix_flow(hass, issue_id, issue.data)
    aborted = await unloaded_flow.async_step_init()
    assert aborted["type"] == "abort" and aborted["reason"] == "cover_missing"

    await cover.async_will_remove_from_hass()


@pytest.mark.asyncio
async def test_cover_manual_calibration_repair_flow_cancel_stops_motor(hass: HomeAssistant) -> None:
    """Dismissing the modal mid-run stops moving/pre-move-settling covers without double-stopping or stopping idle covers."""
    import asyncio

    from OWNd.message import OWNEvent

    from custom_components.myhome.repairs import (
        ISSUE_COVER_CALIBRATION_CUTOFF,
        async_create_fix_flow,
    )

    entry, gw, cover = _make_real_cover_repair_env(
        hass,
        mac="00:03:50:00:00:03",
        device_id="21",
        where="21",
        interface=None,
        name="Living Cover",
        object_id="living_cover",
    )
    issue_data = {
        "entry_id": entry.entry_id,
        "device_id": "21",
        "entity_id": cover.entity_id,
        "cover_name": "Living Cover",
    }

    # 1. Dismiss while actively closing -> restores _settles_on_timer and sends *2*0*21##
    flow = await async_create_fix_flow(
        hass, f"{ISSUE_COVER_CALIBRATION_CUTOFF}_{entry.entry_id}_21", issue_data
    )
    step = await flow.async_step_timed_close_ready(user_input={})
    assert step["step_id"] == "timed_close_stop"
    assert cover._settles_on_timer is False
    assert cover.is_closing is True
    assert flow._moving is True

    flow.async_remove()
    assert cover._settles_on_timer is True
    assert flow._moving is False
    await hass.async_block_till_done()
    assert [f for f, _ in gw.deliveries] == ["*2*2*21##", "*2*0*21##"]
    cover.handle_event(OWNEvent.parse("*2*0*21##"))

    # 2. Dismiss during MH201 0.6s pre-move stop settle pause -> *2*2*21## is NEVER sent after close!
    gw.deliveries.clear()
    cover._stop_before_move = True
    allow_settle_finish = asyncio.Event()
    entered_settle = asyncio.Event()
    real_sleep = asyncio.sleep

    async def _controlled_sleep(delay: float) -> None:
        if delay >= 0.5:
            entered_settle.set()
            await allow_settle_finish.wait()
        else:
            await real_sleep(delay)

    flow_pre = await async_create_fix_flow(
        hass, f"{ISSUE_COVER_CALIBRATION_CUTOFF}_{entry.entry_id}_21", issue_data
    )
    with (
        patch.object(cover, "_omits_bus_pre_move_stop", return_value=True),
        patch("custom_components.myhome.cover.asyncio.sleep", side_effect=_controlled_sleep),
    ):
        start_task = asyncio.create_task(flow_pre.async_step_timed_close_ready(user_input={}))
        await asyncio.wait_for(entered_settle.wait(), timeout=2.0)
        assert flow_pre._moving is True
        assert cover._pre_move_stop_gen is not None
        # User closes modal during the 0.6s pre-move settle pause
        flow_pre.async_remove()
        await real_sleep(0)
        await real_sleep(0)
        allow_settle_finish.set()
        await asyncio.wait_for(start_task, timeout=2.0)
        await hass.async_block_till_done()

    assert "*2*2*21##" not in [f for f, _ in gw.deliveries]
    assert cover._settles_on_timer is True
    cover._stop_before_move = None

    # 3. Dismiss while cover already stopped on bus -> zero redundant *2*0*21## sent
    gw.deliveries.clear()
    flow_idle = await async_create_fix_flow(
        hass, f"{ISSUE_COVER_CALIBRATION_CUTOFF}_{entry.entry_id}_21", issue_data
    )
    await flow_idle.async_step_timed_close_ready(user_input={})
    cover.handle_event(OWNEvent.parse("*2*2*21##"))
    cover.handle_event(OWNEvent.parse("*2*0*21##"))
    assert cover.is_closing is False
    gw.deliveries.clear()
    flow_idle.async_remove()
    await hass.async_block_till_done()
    assert gw.deliveries == []

    await cover.async_will_remove_from_hass()


@pytest.mark.asyncio
async def test_cover_manual_calibration_repair_flow_preflight_guards_and_native_cleanup(
    hass: HomeAssistant,
) -> None:
    """RepairFlow aborts cleanly on unavailable, locked, calibrating, concurrent owner, and promoted native covers."""
    from custom_components.myhome.const import CONF_COVER_TRAVEL_TIMES
    from custom_components.myhome.repairs import (
        ISSUE_COVER_LEGACY_CUTOFF,
        async_create_fix_flow,
        async_sync_legacy_cover_cutoff_issues,
    )

    entry, gw, cover = _make_real_cover_repair_env(
        hass,
        mac="00:03:50:00:00:04",
        device_id="04",
        where="04",
        interface=None,
        name="Study Shade",
        object_id="study_shade",
        options={
            CONF_COVER_TRAVEL_TIMES: {
                "04": {"down": 61.0, "up": 61.0},
                "0004": {"down": 61.0, "up": 61.0},
            }
        },
    )
    issue_id = f"{ISSUE_COVER_LEGACY_CUTOFF}_{entry.entry_id}_04"
    issue_data = {
        "entry_id": entry.entry_id,
        "device_id": "04",
        "entity_id": cover.entity_id,
        "cover_name": "Study Shade",
        "reason": "legacy",
        "down": "61.0",
        "up": "61.0",
    }

    # 1. Unavailable cover -> timed run aborts cover_unavailable with 0 frames
    gw.available = False
    f_unavail = await async_create_fix_flow(hass, issue_id, issue_data)
    res = await f_unavail.async_step_timed_close_ready(user_input={})
    assert res["type"] == "abort" and res["reason"] == "cover_unavailable"
    assert gw.deliveries == []
    gw.available = True

    # 2. Actuator locked via WHO=14 -> timed run aborts cover_locked with 0 frames
    cover.handle_actuator_lock(True)
    f_locked = await async_create_fix_flow(hass, issue_id, issue_data)
    res = await f_locked.async_step_timed_close_ready(user_input={})
    assert res["type"] == "abort" and res["reason"] == "cover_locked"
    assert gw.deliveries == []
    cover.handle_actuator_lock(False)

    # 3. Active auto-calibration -> timed run aborts calibration_in_progress
    cover._calibrating = True
    f_cal = await async_create_fix_flow(hass, issue_id, issue_data)
    res = await f_cal.async_step_timed_close_ready(user_input={})
    assert res["type"] == "abort" and res["reason"] == "calibration_in_progress"
    assert gw.deliveries == []
    cover._calibrating = False

    # 4. Concurrent owner token -> second flow refused and its async_remove() never clobbers first flow
    flow_a = await async_create_fix_flow(hass, issue_id, issue_data)
    step_a = await flow_a.async_step_timed_close_ready(user_input={})
    assert step_a["step_id"] == "timed_close_stop"
    assert cover._settles_on_timer is False
    assert cover._manual_timing_owner is flow_a

    flow_b = await async_create_fix_flow(hass, issue_id, issue_data)
    res_b = await flow_b.async_step_timed_close_ready(user_input={})
    assert res_b["type"] == "abort" and res_b["reason"] == "calibration_in_progress"
    flow_b.async_remove()
    assert cover._settles_on_timer is False
    assert cover._manual_timing_owner is flow_a

    flow_a.async_remove()
    await hass.async_block_till_done()
    assert cover._settles_on_timer is True
    assert cover._manual_timing_owner is None

    # 5. M2: Native Dimension 10 cover prunes stale '04' + '0004' travel times, clears issue, and aborts cover_reports_position
    async_sync_legacy_cover_cutoff_issues(
        hass,
        entry.entry_id,
        [{"device_id": "04", "entity_id": cover.entity_id, "name": "Study Shade", "down": 61.0, "up": 61.0}],
    )
    assert ir.async_get(hass).async_get_issue(DOMAIN, issue_id) is not None
    cover._advanced = True
    f_native = await async_create_fix_flow(hass, issue_id, issue_data)
    res_native = await f_native.async_step_init()
    assert res_native["type"] == "abort" and res_native["reason"] == "cover_reports_position"
    assert CONF_COVER_TRAVEL_TIMES not in entry.options
    assert ir.async_get(hass).async_get_issue(DOMAIN, issue_id) is None

    await cover.async_will_remove_from_hass()


