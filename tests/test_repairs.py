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
