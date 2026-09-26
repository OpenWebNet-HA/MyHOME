"""Test repair issues management for MyHOME integration."""
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
    assert issue.learn_more_url == "https://openwebnet-ha.github.io/MyHOME/beta/diagnostics/repair-issues/#unconfigured-timezone"

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
    assert not issue.is_fixable
    assert issue.translation_placeholders == {"decoder": decoder_id, "platform": "cambridge_audio"}
    assert issue.learn_more_url == "https://openwebnet-ha.github.io/MyHOME/beta/configuration/use_cases/#music-assistant"

    async_delete_incompatible_decoder_issue(hass, entry_id, decoder_id)
    assert issue_registry.async_get_issue(DOMAIN, issue_id) is None
