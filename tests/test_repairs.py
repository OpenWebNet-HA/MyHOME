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


async def test_actuator_hardware_fault_repair_issue_lifecycle(hass: HomeAssistant) -> None:
    """Test creating and deleting an actuator hardware fault repair issue."""
    from custom_components.myhome.repairs import (
        ISSUE_ACTUATOR_HARDWARE_FAULT,
        async_create_actuator_hardware_fault_issue,
        async_delete_actuator_hardware_fault_issue,
    )

    issue_registry = ir.async_get(hass)
    entry_id = "test_entry_789"
    where = "74"
    issue_id = f"{ISSUE_ACTUATOR_HARDWARE_FAULT}_{entry_id}_{where}"

    assert issue_registry.async_get_issue(DOMAIN, issue_id) is None

    async_create_actuator_hardware_fault_issue(hass, entry_id, "Light 74", where, 19)
    issue = issue_registry.async_get_issue(DOMAIN, issue_id)
    assert issue is not None
    assert issue.domain == DOMAIN
    assert issue.issue_id == issue_id
    assert issue.severity == ir.IssueSeverity.WARNING
    assert issue.translation_placeholders == {
        "device": "Light 74",
        "where": "74",
        "what": "19",
    }

    async_delete_actuator_hardware_fault_issue(hass, entry_id, where)
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
    assert issue.learn_more_url == "https://github.com/OpenWebNet-HA/MyHOME/wiki/Configuration#timezone"

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
    assert issue.learn_more_url == "https://openwebnet-ha.github.io/MyHOME/beta/configuration/use_cases/#music-assistant"

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

