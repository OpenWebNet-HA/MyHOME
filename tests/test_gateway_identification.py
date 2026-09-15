"""Gateway identification: SSDP / manual are authoritative, WHO=13 only corroborates.

The only official device-type table (BTicino OpenWebNet_Community_2_device v1.0.0,
2006-06-13, WHO=13 section 1.2.6) is: 2 MHServer, 4 MH200, 6 F452, 7 F452V,
11 MHServer2, 13 H4684. Every gateway sold after 2006 is absent from it.
"""
from unittest.mock import MagicMock, patch

import pytest
from OWNd.message import OWNEvent

from custom_components.myhome.const import (
    GATEWAY_DEVICE_TYPE_MAP,
    IDENTIFICATION_MANUAL,
    IDENTIFICATION_SERIAL,
    IDENTIFICATION_SSDP,
    IDENTIFICATION_UNKNOWN,
    IDENTIFICATION_WHO13,
    WHO13_OBSERVED_DEVICE_TYPES,
    WHO13_OFFICIAL_DEVICE_TYPES,
    gateway_model_family,
)
from custom_components.myhome.gateway import MyHOMEGatewayHandler

OFFICIAL_2006_TABLE = {"2": "MHServer", "4": "MH200", "6": "F452", "7": "F452V", "11": "MHServer2", "13": "H4684"}


def _handler(data_overrides=None, *, title="MH200 Gateway"):
    entry = MagicMock()
    entry.entry_id = "entry_ident"
    entry.title = title
    entry.data = {
        "host": "192.0.2.40",
        "port": 20000,
        "password": "x",
        "mac": "00:03:50:00:48:71",
        "name": "MH200",
        "manufacturer": "BTicino S.p.A.",
        "firmware": None,
        "ssdp_location": None,
        "UDN": None,
    }
    entry.data.update(data_overrides or {})
    hass = MagicMock()
    hass.data = {}
    h = MyHOMEGatewayHandler(hass, entry)
    h.device_registry_id = "dev_gw"
    return h


def _who13(h, code):
    h._handle_gateway_diagnostics(OWNEvent.parse(f"*#13**15*{code}##"))


@pytest.fixture
def dev_reg():
    reg = MagicMock()
    reg.async_get.return_value = MagicMock(model="MH200")
    with patch("homeassistant.helpers.device_registry.async_get", return_value=reg):
        yield reg


@pytest.fixture
def issues():
    with patch("custom_components.myhome.gateway.async_create_identity_issue") as create, \
         patch("custom_components.myhome.gateway.async_delete_identity_issue") as delete, \
         patch("custom_components.myhome.gateway.async_create_identity_corrected_issue") as corrected:
        yield create, delete, corrected


# ── the table itself ─────────────────────────────────────────────────────


def test_official_table_is_the_2006_document_verbatim():
    assert WHO13_OFFICIAL_DEVICE_TYPES == OFFICIAL_2006_TABLE
    # observed codes never shadow official ones
    assert not set(WHO13_OBSERVED_DEVICE_TYPES) & set(WHO13_OFFICIAL_DEVICE_TYPES)
    assert GATEWAY_DEVICE_TYPE_MAP["4"] == "MH200"
    assert "MH200N" not in GATEWAY_DEVICE_TYPE_MAP.values()
    # code 200: the only field evidence is a self-identified MyHOMEServer1 (#292 / #297)
    assert WHO13_OBSERVED_DEVICE_TYPES == {"200": "MyHomeServer1"}
    assert "F454" not in GATEWAY_DEVICE_TYPE_MAP.values()


def test_official_table_matches_ownd_decoder():
    for code, model in OFFICIAL_2006_TABLE.items():
        decoded = OWNEvent.parse(f"*#13**15*{code}##")
        assert getattr(decoded, "device_type", getattr(decoded, "_device_type", None)) == model


@pytest.mark.parametrize(
    ("model", "family"),
    [("MH200", "MH200"), ("MH200N", "MH200"), ("F452V", "F452"), ("F454", "F454"), ("MyHomeServer1", "MYHOMESERVER1"), ("", ""), (None, "")],
)
def test_model_family(model, family):
    assert gateway_model_family(model) == family


# ── identification source ────────────────────────────────────────────────


def test_identification_source_precedence():
    assert _handler({"ssdp_location": "http://192.0.2.40:49153/desc.xml"}).identification_source == IDENTIFICATION_SSDP
    assert _handler({"UDN": "uuid:1234"}).identification_source == IDENTIFICATION_SSDP
    assert _handler({"transport_type": "serial", "name": "Legrand 3578 USB Gateway"}).identification_source == IDENTIFICATION_SERIAL
    assert _handler().identification_source == IDENTIFICATION_MANUAL
    assert _handler({"name": "Generic"}).identification_source == IDENTIFICATION_UNKNOWN
    assert _handler({"name": "MH200", "model_source": "who13"}).identification_source == IDENTIFICATION_WHO13
    assert _handler({"name": "MH200N", "model_source": "manual"}).identification_source == IDENTIFICATION_MANUAL


# ── a manually configured MH200 that reports type 4 (the live case) ─────


def test_manual_mh200_reporting_type_4_is_left_alone(dev_reg, issues):
    create, delete, corrected = issues
    h = _handler()
    _who13(h, "4")
    assert h.gateway.model_name == "MH200"
    assert h._who13["code"] == "4" and h._who13["model"] == "MH200"
    assert h._identity_conflict is None
    create.assert_not_called()
    corrected.assert_not_called()
    h.hass.config_entries.async_update_entry.assert_not_called()
    dev_reg.async_update_device.assert_not_called()
    ident = h.identification()
    assert ident["model"] == "MH200" and ident["source"] == IDENTIFICATION_MANUAL and ident["who13_code"] == "4"
    assert ident["who13_model_official"] == "MH200" and ident["who13_model_observed"] is None


def test_variant_suffix_is_not_a_conflict(dev_reg, issues):
    """An MH200N owner whose unit reports the 2006 code 4 (MH200) is consistent, not mislabelled."""
    create, _, corrected = issues
    h = _handler({"name": "MH200N"})
    h.gateway.model_name = "MH200N"
    dev_reg.async_get.return_value = MagicMock(model="MH200N")
    _who13(h, "4")
    assert h.gateway.model_name == "MH200N"
    assert h._identity_conflict is None
    create.assert_not_called()
    corrected.assert_not_called()


# ── SSDP-announced model is never overruled, but a contradiction is flagged ──


def test_ssdp_model_never_relabelled_and_conflict_raises_repair(dev_reg, issues):
    create, delete, corrected = issues
    h = _handler({"name": "F454", "ssdp_location": "http://192.0.2.40:49153/desc.xml"})
    h.gateway.model_name = "F454"
    dev_reg.async_get.return_value = MagicMock(model="F454")

    _who13(h, "4")  # device says MH200 - contradiction with the announcement
    assert h.gateway.model_name == "F454"
    assert "configured as F454 (ssdp)" in h._identity_conflict
    create.assert_called_once()
    args = create.call_args.args
    assert args[1:] == ("entry_ident", "F454", "MH200", "4", "ssdp", True)
    corrected.assert_not_called()
    h.hass.config_entries.async_update_entry.assert_not_called()

    # same reply again: no duplicate issue
    _who13(h, "4")
    create.assert_called_once()

    # an observed-only code that contradicts is also only asked about
    create.reset_mock()
    _who13(h, "200")
    assert "per field evidence" in h._identity_conflict
    assert create.call_args.args[1:] == ("entry_ident", "F454", "MyHomeServer1", "200", "ssdp", False)
    assert h.gateway.model_name == "F454"

    # a code outside both tables: conflict cleared, issue deleted, model kept
    _who13(h, "999")
    assert h._identity_conflict is None
    delete.assert_called_once_with(h.hass, "entry_ident")
    assert h.gateway.model_name == "F454"


def test_manual_model_contradicted_by_official_code_is_corrected(dev_reg, issues):
    """The manual flow used to default to F454; an official code proves otherwise and is applied."""
    create, delete, corrected = issues
    h = _handler({"name": "F454"}, title="F454 Gateway")
    h.gateway.model_name = "F454"
    dev_reg.async_get.return_value = MagicMock(model="F454")

    _who13(h, "6")  # F452 per the 2006 table: certain
    assert h.gateway.model_name == "F452"
    kwargs = h.hass.config_entries.async_update_entry.call_args.kwargs
    assert kwargs["data"]["name"] == "F452" and kwargs["data"]["model_source"] == IDENTIFICATION_WHO13
    assert kwargs["title"] == "F452 Gateway"
    corrected.assert_called_once_with(h.hass, "entry_ident", "F454", "F452", "6")
    create.assert_not_called()
    dev_reg.async_update_device.assert_called_once_with("dev_gw", model="F452")


def test_manual_model_contradicted_by_observed_code_is_only_questioned(dev_reg, issues):
    """The #292/#297 reporter's case: manual F454, device reports 200 (seen on MyHOMEServer1)."""
    create, delete, corrected = issues
    h = _handler({"name": "F454"}, title="F454 Gateway")
    h.gateway.model_name = "F454"
    dev_reg.async_get.return_value = MagicMock(model="F454")

    _who13(h, "200")
    assert h.gateway.model_name == "F454"  # not overruled by field evidence alone
    h.hass.config_entries.async_update_entry.assert_not_called()
    corrected.assert_not_called()
    create.assert_called_once()
    assert create.call_args.args[1:] == ("entry_ident", "F454", "MyHomeServer1", "200", "manual", False)
    ident = h.identification()
    assert ident["who13_model_official"] is None and ident["who13_model_observed"] == "MyHomeServer1"


def test_unknown_code_on_configured_gateway_is_recorded_not_applied(dev_reg, issues):
    create, _, corrected = issues
    h = _handler()
    _who13(h, "999")
    assert h.gateway.model_name == "MH200"
    assert h._who13["code"] == "999" and h._who13["model"] is None
    create.assert_not_called()
    corrected.assert_not_called()


def test_mislabelled_registry_is_repaired_from_the_configured_model(dev_reg, issues):
    """A device registry entry written as MH200N by an earlier release is corrected to the configured model."""
    dev_reg.async_get.return_value = MagicMock(model="MH200N")
    h = _handler()
    _who13(h, "4")
    dev_reg.async_update_device.assert_called_once_with("dev_gw", model="MH200")


# ── no model configured: WHO=13 may label, official codes first ──────────


def test_unknown_entry_is_labelled_from_official_code(dev_reg, issues):
    h = _handler({"name": "Generic"}, title="Generic Gateway")
    h.gateway.model_name = "Generic"
    dev_reg.async_get.return_value = MagicMock(model="Generic")
    _who13(h, "6")
    assert h.gateway.model_name == "F452"
    kwargs = h.hass.config_entries.async_update_entry.call_args.kwargs
    assert kwargs["data"]["name"] == "F452"
    assert kwargs["data"]["model_source"] == IDENTIFICATION_WHO13
    assert kwargs["title"] == "F452 Gateway"
    dev_reg.async_update_device.assert_called_once_with("dev_gw", model="F452")


def test_unknown_entry_is_labelled_from_observed_code(dev_reg, issues):
    _, _, corrected = issues
    h = _handler({"name": "Generic"}, title="Generic Gateway")
    h.gateway.model_name = "Generic"
    dev_reg.async_get.return_value = MagicMock(model="Generic")
    _who13(h, "200")
    assert h.gateway.model_name == "MyHomeServer1"
    corrected.assert_not_called()  # nothing was "corrected": there was no model to begin with


def test_unknown_entry_with_unknown_code_stays_generic(dev_reg, issues):
    h = _handler({"name": "Generic"})
    h.gateway.model_name = "Generic"
    _who13(h, "999")
    assert h.gateway.model_name == "Generic"
    h.hass.config_entries.async_update_entry.assert_not_called()


def test_who13_label_is_not_repeated_when_already_applied(dev_reg, issues):
    h = _handler({"name": "F452", "model_source": "who13"})
    h.gateway.model_name = "F452"
    dev_reg.async_get.return_value = MagicMock(model="F452")
    _who13(h, "6")
    h.hass.config_entries.async_update_entry.assert_not_called()
    dev_reg.async_update_device.assert_not_called()


def test_manual_selection_outranks_an_earlier_who13_label(dev_reg, issues):
    """PR #345 review: an entry labelled MH200 by WHO=13, then switched to MH200N in the
    options flow, must not be flipped back by the next device-type 4 reply."""
    create, delete, corrected = issues
    # Before the options flow: the entry was labelled from WHO=13 and would be relabelled.
    h = _handler({"name": "MH200N", "model_source": "who13"})
    h.gateway.model_name = "MH200N"
    _who13(h, "4")
    assert h.gateway.model_name == "MH200"  # the bug the review reproduced
    # After the options flow: the selection is recorded as manual and stays intact.
    h = _handler({"name": "MH200N", "model_source": "manual"})
    h.gateway.model_name = "MH200N"
    dev_reg.async_get.return_value = MagicMock(model="MH200N")
    _who13(h, "4")
    assert h.gateway.model_name == "MH200N"
    assert h.config_entry.data["name"] == "MH200N"
    assert h._identity_conflict is None
    h.hass.config_entries.async_update_entry.assert_not_called()
    create.assert_not_called()
    corrected.assert_not_called()


def test_stale_identity_issue_is_cleared_by_a_fresh_handler(dev_reg, issues):
    """PR #345 review: a reload creates a handler with no conflict in memory, but the
    previous instance's warning is still in the issue registry; a matching reply must remove it."""
    create, delete, corrected = issues
    first = _handler()  # manual MH200
    _who13(first, "200")  # observed-only MyHomeServer1 code: questioned, issue created
    assert first._identity_conflict is not None
    create.assert_called_once()
    delete.assert_not_called()

    second = _handler()  # the integration reloaded: same entry, new handler, no conflict in memory
    assert second._identity_conflict is None
    _who13(second, "4")  # MH200 per the 2006 table: matches the configured model
    assert second._identity_conflict is None
    delete.assert_called_once_with(second.hass, "entry_ident")


# ── corroborating dimensions ─────────────────────────────────────────────


def test_firmware_kernel_distribution_are_recorded(dev_reg):
    h = _handler()
    h._handle_gateway_diagnostics(OWNEvent.parse("*#13**16*2*60*46##"))
    h._handle_gateway_diagnostics(OWNEvent.parse("*#13**23*2*6*32##"))
    h._handle_gateway_diagnostics(OWNEvent.parse("*#13**24*1*0*5##"))
    ident = h.identification()
    assert ident["who13_firmware"] == "2.60.46"
    assert ident["who13_kernel"] == "2.6.32"
    assert ident["who13_distribution"] == "1.0.5"
    assert ident["profile"] == "MH200NProfile"


# ── the evidence travels with diagnostics and the WebSocket / trace payload ──


def test_websocket_gateway_info_carries_identification_without_location():
    from custom_components.myhome.websocket import _extract_gateway_info

    h = _handler({"ssdp_location": "http://192.0.2.40:49153/desc.xml"})
    h._who13["code"] = "4"
    info = _extract_gateway_info(h, "2.0.0b6")
    assert info["identification"]["source"] == IDENTIFICATION_SSDP
    assert info["identification"]["who13_code"] == "4"
    assert "ssdp_location" not in info["identification"]


def test_websocket_gateway_info_tolerates_gateway_without_identification():
    from custom_components.myhome.websocket import _extract_gateway_info

    gw = MagicMock(spec=[])  # no identification attribute at all
    assert _extract_gateway_info(gw)["identification"] == {}


def test_conflict_tracking_without_entry_id_and_registry_sync_without_device(dev_reg):
    """Defensive paths: no config entry id (no issue registry access) and no device registry id."""
    h = _handler()
    h.config_entry.entry_id = None
    with patch("custom_components.myhome.gateway.async_create_identity_issue") as create:
        h._set_conflict("some conflict", None)
        assert h._identity_conflict == "some conflict"
        create.assert_not_called()
    h.device_registry_id = None
    h._sync_device_registry_model("MH200")
    dev_reg.async_update_device.assert_not_called()

