"""Gateway identification: SSDP / manual are authoritative, WHO=13 only corroborates.

The only official device-type table (BTicino OpenWebNet_Community_2_device v1.0.0,
2006-06-13, WHO=13 section 1.2.6) is: 2 MHServer, 4 MH200, 6 F452, 7 F452V,
11 MHServer2, 13 H4684. Every gateway sold after 2006 is absent from it.
"""
import json
from pathlib import Path
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
    WHO13_SHARED_DEVICE_TYPES,
    WHO13_THIRD_PARTY_DEVICE_TYPES,
    WHO1013_BRANDS,
    WHO1013_LINES,
    WHO1013_OBJECT_MODELS,
    gateway_model_family,
)
from custom_components.myhome.gateway import MyHOMEGatewayHandler, get_gateway_profile
from custom_components.myhome.identity import GatewayIdentityEvidence as Evidence
from custom_components.myhome.identity import read_who13, read_who1013
from custom_components.myhome.identity import resolve_gateway_identity as resolve

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
    # code 4 is the 2006 MH200, never its successor: an MH200N answering 4 is
    # consistent, not a contradiction. MH200N has a code of its own (44, Nmap).
    assert "MH200N" not in {GATEWAY_DEVICE_TYPE_MAP[c] for c in WHO13_OFFICIAL_DEVICE_TYPES}
    assert GATEWAY_DEVICE_TYPE_MAP["44"] == "MH200N"
    # code 200: observed on F454 (#370, #420), MyHOMEServer1 (#292 / #297, #420), MH202 (#420);
    # reported for F461 (#370), observed on H4890 (#466)
    assert WHO13_OBSERVED_DEVICE_TYPES == {"200": ("F454", "MyHomeServer1", "MH202", "F461", "H4890")}
    assert WHO13_SHARED_DEVICE_TYPES == {"200"}
    assert WHO13_SHARED_DEVICE_TYPES <= set(WHO13_OBSERVED_DEVICE_TYPES)
    # every model a shared WHO=13 code may stand for has a WHO=1013 code that settles it
    who1013_families = {gateway_model_family(m) for models in WHO1013_OBJECT_MODELS.values() for m in models}
    for code in WHO13_SHARED_DEVICE_TYPES:
        for model in WHO13_OBSERVED_DEVICE_TYPES[code]:
            assert gateway_model_family(model) in who1013_families, (code, model)
    # WHO=1013 codes are unique per model: no model appears under two codes
    seen: dict[str, str] = {}
    for code, models in WHO1013_OBJECT_MODELS.items():
        for model in models:
            assert model not in seen, (model, seen.get(model), code)
            seen[model] = code


# ── reading a code ───────────────────────────────────────────────────────


def test_third_party_table_is_the_nmap_device_table():
    """Nmap's openwebnet-discovery.nse `device` table, whose dimension is this same WHO=13 15.

    Carried since the script was first committed (2017-07-18), so it predates this
    project and is independent of the OpenWebNet device database: two unrelated
    sources agree that an F454 can answer 51. The six 2006 codes it repeats are not
    duplicated here, and 200 is left to the field evidence that outranks Nmap's
    tentative "F454 (new?)".
    """
    assert WHO13_THIRD_PARTY_DEVICE_TYPES == {
        "12": ("F453AV",),
        "15": ("F427",),
        "16": ("F453",),
        "23": ("H4684",),
        "27": ("L4686SDK",),
        "44": ("MH200N",),
        "51": ("F454",),
    }
    # the three tables never fight over a code
    assert not set(WHO13_THIRD_PARTY_DEVICE_TYPES) & set(WHO13_OFFICIAL_DEVICE_TYPES)
    assert not set(WHO13_THIRD_PARTY_DEVICE_TYPES) & set(WHO13_OBSERVED_DEVICE_TYPES)
    # a code from it labels an unconfigured gateway but never contradicts a configured one
    r = read_who13("16")
    assert (r.canonical, r.certain, r.basis) == ("F453", False, "an independent implementation")
    assert r.compatible_with("F453") is True
    assert r.compatible_with("MH200") is None
    assert resolve(Evidence(who13_code="16")).model == "F453"
    assert resolve(Evidence(manual="MH200", who13_code="16")).corrected_from is None
    assert resolve(Evidence(technical="MH200", technical_source=IDENTIFICATION_SSDP, who13_code="16")).conflict is None
    # ...and it is no longer reported as an unknown code asking for a trace
    assert resolve(Evidence(who13_code="16")).unknown_code is None
    # a model OWNd does not know still resolves to a profile
    assert get_gateway_profile("L4686SDK") is not None


def test_who13_and_who1013_are_separate_identifier_spaces():
    """The same model has different numbers in the two families, so the tables stay apart (#420).

    F453 is 42 for WHO=1013 but 16 for Nmap's WHO=13 table; H4684 is 29 for
    WHO=1013 but 13 (2006) or 23 (Nmap) for WHO=13. Some values do coincide - 4,
    12, 44, 51 - which is exactly why this is pinned: agreeing on a few codes is
    not a reason to treat one table as the other.
    """
    who13_all = {**WHO13_OFFICIAL_DEVICE_TYPES, **{c: m[0] for c, m in WHO13_THIRD_PARTY_DEVICE_TYPES.items()}}
    by_model_who13 = {model: code for code, model in who13_all.items()}
    by_model_who1013 = {models[0]: code for code, models in WHO1013_OBJECT_MODELS.items()}

    differ = {m: (by_model_who13[m], by_model_who1013[m]) for m in by_model_who13.keys() & by_model_who1013.keys()
              if by_model_who13[m] != by_model_who1013[m]}
    agree = {m for m in by_model_who13.keys() & by_model_who1013.keys() if by_model_who13[m] == by_model_who1013[m]}
    assert differ == {"F453": ("16", "42"), "H4684": ("23", "29")}
    assert agree == {"MH200", "F453AV", "MH200N", "F454"}
    # and the shared WHO=13 code has no counterpart at all in the WHO=1013 space
    assert "200" not in WHO1013_OBJECT_MODELS


def test_read_who13():
    official = read_who13("4")
    assert (official.models, official.certain, official.shared, official.raw) == (("MH200",), True, False, "4")
    assert official.compatible_with("MH200") is True
    assert official.compatible_with(" mh 200 ") is True  # case and spacing do not matter
    # same family, but the MH200N has a code of its own (44): a certain contradiction
    assert official.compatible_with("MH200N") is False
    assert official.compatible_with("003565") is False  # the MH200N's Legrand name
    assert official.compatible_with("MH200X") is True  # a name no table lists: judged by family
    assert official.compatible_with("F454") is False  # a certain contradiction
    assert official.compatible_with(None) is False and official.compatible_with("") is False

    third_party = read_who13("51")
    assert (third_party.models, third_party.certain, third_party.shared) == (("F454",), False, False)
    assert third_party.compatible_with("F454") is True
    assert third_party.compatible_with("003598") is True  # the F454's Legrand name
    assert third_party.compatible_with("MH200") is None  # a third-party table cannot contradict
    # nor can Nmap's 44 contradict an MH200, though the two are different products
    assert read_who13("44").compatible_with("MH200") is None

    shared = read_who13("200")
    assert shared.shared and shared.known and shared.canonical == "F454"
    assert shared.compatible_with("F461") is True and shared.compatible_with("MH200") is None

    unknown = read_who13("999")
    assert not unknown.known and unknown.models == ()
    assert unknown.compatible_with("F454") is None
    assert read_who13("4").describe() == "WHO=13 device type 4"


def test_read_who1013():
    r = read_who1013("51")
    assert (r.models, r.certain, r.shared, r.raw, r.canonical) == (("F454", "003598"), True, False, "1013-1-51", "F454")
    # the same product under its Legrand name is corroborated, not contradicted
    assert r.compatible_with("003598") is True
    assert r.compatible_with("F454") is True
    assert r.compatible_with("MyHomeServer1") is False
    assert read_who1013("999").known is False
    assert read_who1013("67").describe() == "WHO=1013 OBJECT_MODEL 67"


# ── the resolver: evidence in, verdict out ───────────────────────────────


def test_resolver_nothing_known():
    r = resolve(Evidence())
    assert (r.model, r.source, r.conflict, r.request_who1013, r.unknown_code) == (None, IDENTIFICATION_UNKNOWN, None, False, None)
    r = resolve(Evidence(manual="MH200"))
    assert (r.model, r.source) == ("MH200", IDENTIFICATION_MANUAL)
    r = resolve(Evidence(technical="F454", technical_source=IDENTIFICATION_SSDP))
    assert (r.model, r.source) == ("F454", IDENTIFICATION_SSDP)
    r = resolve(Evidence(technical="MH200"))  # source defaults to ssdp
    assert r.source == IDENTIFICATION_SSDP
    r = resolve(Evidence(prior_label="F452"))
    assert (r.model, r.source) == ("F452", IDENTIFICATION_WHO13)


def test_resolver_shared_who13_code_is_a_question_not_an_answer():
    for ev in (
        Evidence(who13_code="200"),
        Evidence(manual="MH200", who13_code="200"),
        Evidence(technical="F454", technical_source=IDENTIFICATION_SSDP, who13_code="200"),
        Evidence(technical="MH200", technical_source=IDENTIFICATION_SERIAL, who13_code="200"),
        Evidence(prior_label="F452", who13_code="200"),
    ):
        r = resolve(ev)
        assert r.request_who1013 and r.who13_shared and r.conflict is None and r.corrected_from is None, ev
        assert r.model == (ev.manual or ev.technical or ev.prior_label), ev
    # answered: no further request, whatever the answer
    assert resolve(Evidence(who13_code="200", who1013_code="51")).request_who1013 is False
    assert resolve(Evidence(who13_code="200", who1013_code="999")).request_who1013 is False


def test_resolver_labels_an_untrusted_model_from_in_band_evidence():
    assert resolve(Evidence(who13_code="6")).model == "F452"
    assert resolve(Evidence(prior_label="F452", who13_code="4")).model == "MH200"  # our own label: relabel freely
    assert resolve(Evidence(who13_code="51")).model == "F454"  # a third-party code still labels
    r = resolve(Evidence(who13_code="200", who1013_code="67"))
    assert (r.model, r.source, r.corrected_from) == ("MyHomeServer1", IDENTIFICATION_WHO13, None)


def test_resolver_manual_choice():
    # compatible, or questioned by field evidence only: kept
    assert resolve(Evidence(manual="MH200", who13_code="4")).model == "MH200"
    assert resolve(Evidence(manual="MH200", who13_code="44")).corrected_from is None  # Nmap only
    r = resolve(Evidence(manual="MH200", who13_code="51"))
    assert (r.model, r.conflict, r.corrected_from) == ("MH200", None, None)
    # a certain contradiction corrects it and says from what
    r = resolve(Evidence(manual="F454", who13_code="6"))
    assert (r.model, r.source, r.corrected_from, r.corrected_reading.raw) == ("F452", IDENTIFICATION_WHO13, "F454", "6")
    r = resolve(Evidence(manual="F454", who13_code="200", who1013_code="67"))
    assert (r.model, r.corrected_from, r.corrected_reading.raw) == ("MyHomeServer1", "F454", "1013-1-67")
    # confirmed by WHO=1013 with no table change needed for the model itself
    r = resolve(Evidence(manual="F461", who13_code="200", who1013_code="134"))
    assert (r.model, r.conflict, r.corrected_from) == ("F461", None, None)
    # a manual entry naming the Legrand variant is corroborated
    assert resolve(Evidence(manual="003598", who13_code="200", who1013_code="51")).corrected_from is None


def test_resolver_same_family_with_a_code_of_its_own_is_a_contradiction():
    """Live 2026-09-23: an MH200 (firmware 2.1.0, `*#13**15*4##`) set up by hand as MH200N.

    Both names reduce to the MH200 family, so the family check let the manual MH200N
    stand; OWNd's MH200N profile then skipped the startup WHO=16 sweep. The MH200N
    has a code of its own (WHO=1013 44, Nmap's WHO=13 44), so an official 4 proves
    the gateway is an MH200.
    """
    r = resolve(Evidence(manual="MH200N", who13_code="4"))
    assert (r.model, r.source, r.corrected_from, r.corrected_reading.raw) == ("MH200", IDENTIFICATION_WHO13, "MH200N", "4")
    assert r.conflict is None
    # the reverse, settled by the certain catalogue
    r = resolve(Evidence(manual="MH200", who1013_code="44"))
    assert (r.model, r.corrected_from, r.corrected_reading.raw) == ("MH200N", "MH200", "1013-1-44")
    # the MH200N under its Legrand name is contradicted too, and corroborated by its own code
    assert resolve(Evidence(manual="003565", who13_code="4")).corrected_from == "003565"
    assert resolve(Evidence(manual="003565", who1013_code="44")).corrected_from is None
    # F452V is in the 2006 table itself (7), so the F452 code (6) contradicts it too
    assert resolve(Evidence(manual="F452V", who13_code="7")).corrected_from is None
    r = resolve(Evidence(manual="F452V", who13_code="6"))
    assert (r.model, r.corrected_from) == ("F452", "F452V")
    # a variant name no table lists is still judged by family, so it stays
    r = resolve(Evidence(manual="F452X", who13_code="6"))
    assert (r.model, r.source, r.corrected_from) == ("F452X", IDENTIFICATION_MANUAL, None)
    r = resolve(Evidence(manual="F454X", who13_code="200", who1013_code="51"))
    assert (r.model, r.corrected_from) == ("F454X", None)


def test_resolver_same_family_contradiction_of_a_technical_identity_is_a_conflict():
    """An SSDP or serial MH200N answering 4 is kept and flagged, never relabelled."""
    for source in (IDENTIFICATION_SSDP, IDENTIFICATION_SERIAL):
        r = resolve(Evidence(technical="MH200N", technical_source=source, who13_code="4"))
        assert (r.model, r.source, r.corrected_from) == ("MH200N", source, None)
        assert r.conflict == f"configured as MH200N ({source}) but WHO=13 device type 4 identifies MH200 per the OpenWebNet specification"
        assert r.conflict_reading.raw == "4"


def test_resolver_technical_identity_is_never_overruled():
    for source in (IDENTIFICATION_SSDP, IDENTIFICATION_SERIAL):
        r = resolve(Evidence(technical="F454", technical_source=source, who13_code="4"))
        assert r.model == "F454" and r.source == source
        assert r.conflict == f"configured as F454 ({source}) but WHO=13 device type 4 identifies MH200 per the OpenWebNet specification"
        assert (r.conflict_reading.raw, r.conflict_reading.certain) == ("4", True)
        r = resolve(Evidence(technical="MH202", technical_source=source, who13_code="200", who1013_code="67"))
        assert r.model == "MH202"
        assert r.conflict == f"configured as MH202 ({source}) but WHO=1013 OBJECT_MODEL 67 identifies MyHomeServer1 per diagnostic catalogue"
        # field evidence only: unverified, no conflict
        assert resolve(Evidence(technical="MH200", technical_source=source, who13_code="51")).conflict is None
        # corroborated
        assert resolve(Evidence(technical="MH202", technical_source=source, who13_code="200", who1013_code="5")).conflict is None
        assert resolve(Evidence(technical="003598", technical_source=source, who13_code="200", who1013_code="51")).conflict is None


def test_resolver_who1013_outranks_who13():
    """The catalogue is one code per model and the two families disagree for the same product."""
    r = resolve(Evidence(technical="F454", technical_source=IDENTIFICATION_SSDP, who13_code="4", who1013_code="51"))
    assert r.conflict is None
    r = resolve(Evidence(manual="MH200", who13_code="4", who1013_code="51"))
    assert (r.model, r.corrected_from) == ("F454", "MH200")
    # an unknown WHO=1013 code does not outrank a known WHO=13 one
    r = resolve(Evidence(who13_code="4", who1013_code="999"))
    assert (r.model, r.unknown_code) == ("MH200", "1013-1-999")


def test_resolver_unknown_codes():
    r = resolve(Evidence(manual="MH200", who13_code="999"))
    assert (r.model, r.conflict, r.unknown_code) == ("MH200", None, "999")
    r = resolve(Evidence(who13_code="200", who1013_code="999"))
    assert (r.model, r.unknown_code, r.request_who1013) == (None, "1013-1-999", False)
    r = resolve(Evidence(who13_code="998", who1013_code="999"))
    assert [u.raw for u in r.unknown_readings] == ["998", "1013-1-999"]
    assert r.unknown_code == "1013-1-999"


def test_resolver_is_idempotent():
    ev = Evidence(technical="MH202", technical_source=IDENTIFICATION_SSDP, who13_code="200", who1013_code="67")
    assert resolve(ev) == resolve(ev)


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


def test_manual_mh200n_on_a_live_mh200_is_corrected(dev_reg, issues, caplog):
    """Live 2026-09-23: a manual-flow entry named MH200N on an MH200 answering `*#13**15*4##`.

    The entry (source "user", no model_source, no SSDP fields) kept MH200N because
    both names share the MH200 family, so the gateway got OWNd's MH200N profile and
    startup discovery skipped `*#16*0*5##`. The MH200N has a code of its own, so the
    official 4 now corrects it to MH200.
    """
    create, _, corrected = issues
    h = _handler({"name": "MH200N"}, title="MH200N Gateway")
    h.gateway.model_name = "MH200N"
    dev_reg.async_get.return_value = MagicMock(model="MH200N")
    assert h.identification_source == IDENTIFICATION_MANUAL

    _who13(h, "4")
    assert h.gateway.model_name == "MH200"
    # whatever profile the installed OWNd gives an MH200 (its own with WHO 16 from OWNd#53)
    assert type(h.gateway.profile) is type(get_gateway_profile("MH200"))
    kwargs = h.hass.config_entries.async_update_entry.call_args.kwargs
    assert kwargs["data"]["name"] == "MH200" and kwargs["data"]["model_source"] == IDENTIFICATION_WHO13
    assert kwargs["title"] == "MH200 Gateway"
    corrected.assert_called_once_with(h.hass, "entry_ident", "MH200N", "MH200", "4")
    create.assert_not_called()
    assert h._identity_conflict is None
    dev_reg.async_update_device.assert_called_once_with("dev_gw", model="MH200")
    assert "Gateway model `MH200` set from WHO=13 device type 4 (was `MH200N`, source manual)" in caplog.text


def test_ssdp_mh200n_answering_type_4_is_flagged_not_corrected(dev_reg, issues):
    """An MH200N the gateway announced itself is never relabelled; code 4 raises a mismatch."""
    create, _, corrected = issues
    h = _handler({"name": "MH200N", "ssdp_location": "http://192.0.2.40:49153/desc.xml"}, title="MH200N Gateway")
    h.gateway.model_name = "MH200N"
    dev_reg.async_get.return_value = MagicMock(model="MH200N")
    _who13(h, "4")
    assert h.gateway.model_name == "MH200N"
    assert "configured as MH200N (ssdp)" in h._identity_conflict
    create.assert_called_once()
    corrected.assert_not_called()
    h.hass.config_entries.async_update_entry.assert_not_called()


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

    # code 200 is compatible with F454 per field evidence (#370): conflict cleared, issue deleted
    create.reset_mock()
    _who13(h, "200")
    assert h._identity_conflict is None
    delete.assert_called_once_with(h.hass, "entry_ident")
    assert h.gateway.model_name == "F454"
    create.assert_not_called()
    ident = h.identification()
    assert ident["who13_code"] == "200"
    assert ident["who13_model_official"] is None
    assert ident["who13_model_observed"] == "F454 / MyHomeServer1 / MH202 / F461 / H4890"
    assert ident["conflict"] is None
    # ...and, the code being shared, WHO=1013 is asked to cross-check the announcement
    assert str(h._command_pool.send_buffer.get_nowait()["message"]) == "*#1013*0*1##"

    # a code outside both tables: conflict cleared, issue deleted, model kept
    delete.reset_mock()
    _who13(h, "999")
    assert h._identity_conflict is None
    delete.assert_called_once_with(h.hass, "entry_ident")
    assert h.gateway.model_name == "F454"


def test_f454_and_mhs1_with_code_200_have_no_conflict(dev_reg, issues):
    """Both F454 (#370) and MyHomeServer1 (#292/#297) report code 200: no conflict for either."""
    create, delete, corrected = issues
    for model in ("F454", "MyHomeServer1"):
        create.reset_mock()
        delete.reset_mock()
        h = _handler({"name": model}, title=f"{model} Gateway")
        h.gateway.model_name = model
        dev_reg.async_get.return_value = MagicMock(model=model)
        _who13(h, "200")
        assert h.gateway.model_name == model
        assert h._identity_conflict is None
        create.assert_not_called()
        corrected.assert_not_called()
        ident = h.identification()
        assert ident["who13_code"] == "200"
        assert ident["who13_model_observed"] == "F454 / MyHomeServer1 / MH202 / F461 / H4890"
        assert ident["conflict"] is None


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


def test_manual_model_with_unverified_observed_code_keeps_model_without_conflict(dev_reg, issues):
    """An observed code whose compatibility is unverified (e.g. manual MH200, reports 200) keeps the model without conflict."""
    create, delete, corrected = issues
    h = _handler({"name": "MH200"}, title="MH200 Gateway")
    h.gateway.model_name = "MH200"
    dev_reg.async_get.return_value = MagicMock(model="MH200")

    _who13(h, "200")
    assert h.gateway.model_name == "MH200"  # not overruled by field evidence
    h.hass.config_entries.async_update_entry.assert_not_called()
    corrected.assert_not_called()
    create.assert_not_called()
    assert h._identity_conflict is None
    ident = h.identification()
    assert ident["who13_model_official"] is None and ident["who13_model_observed"] == "F454 / MyHomeServer1 / MH202 / F461 / H4890"
    assert ident["conflict"] is None
    assert ident["who1013_code"] is None and ident["who1013_model"] is None
    # the shared code is the cue to ask WHO=1013, whatever the source
    queued = h._command_pool.send_buffer.get_nowait()
    assert str(queued["message"]) == "*#1013*0*1##" and queued["is_status_request"] is True


def test_manual_model_confirmed_by_who1013_is_kept(dev_reg, issues):
    """Manual F461, WHO=13 says 200, WHO=1013 says 134 (F461): nothing to fix, nothing to add to any table."""
    create, delete, corrected = issues
    h = _handler({"name": "F461"}, title="F461 Gateway")
    h.gateway.model_name = "F461"
    dev_reg.async_get.return_value = MagicMock(model="F461")
    _who13(h, "200")
    _who1013(h, "134")
    assert h.gateway.model_name == "F461"
    h.hass.config_entries.async_update_entry.assert_not_called()
    corrected.assert_not_called()
    create.assert_not_called()
    ident = h.identification()
    assert ident["who1013_code"] == "134" and ident["who1013_model"] == "F461"
    assert ident["conflict"] is None
    # answered: a repeat of the shared code does not ask again
    _who13(h, "200")
    assert h._command_pool.send_buffer.qsize() == 1


def test_manual_model_contradicted_by_who1013_is_corrected(dev_reg, issues):
    """Manual F454 (the old flow default), WHO=13 says 200, WHO=1013 says 67: it is a MyHomeServer1 (#292/#297)."""
    create, delete, corrected = issues
    h = _handler({"name": "F454"}, title="F454 Gateway")
    h.gateway.model_name = "F454"
    dev_reg.async_get.return_value = MagicMock(model="F454")
    _who13(h, "200")
    assert h.gateway.model_name == "F454"
    _who1013(h, "67")
    assert h.gateway.model_name == "MyHomeServer1"
    kwargs = h.hass.config_entries.async_update_entry.call_args.kwargs
    assert kwargs["data"]["name"] == "MyHomeServer1" and kwargs["data"]["model_source"] == IDENTIFICATION_WHO13
    assert kwargs["title"] == "MyHomeServer1 Gateway"
    corrected.assert_called_once_with(h.hass, "entry_ident", "F454", "MyHomeServer1", "1013-1-67")
    create.assert_not_called()
    dev_reg.async_update_device.assert_called_once_with("dev_gw", model="MyHomeServer1", model_id="67")
    assert h.identification()["who1013_model"] == "MyHomeServer1"


def test_ssdp_model_with_unverified_observed_code_keeps_model_without_conflict(dev_reg, issues):
    """An observed code whose compatibility is unverified on an SSDP gateway keeps the model without conflict."""
    create, delete, corrected = issues
    h = _handler({"name": "MH202", "ssdp_location": "http://192.168.1.40:49153/desc.xml"})
    h.gateway.model_name = "MH202"
    dev_reg.async_get.return_value = MagicMock(model="MH202")

    _who13(h, "200")
    assert h.gateway.model_name == "MH202"
    assert h._identity_conflict is None
    create.assert_not_called()
    corrected.assert_not_called()
    ident = h.identification()
    assert ident["who13_model_observed"] == "F454 / MyHomeServer1 / MH202 / F461 / H4890"
    assert ident["conflict"] is None
    assert str(h._command_pool.send_buffer.get_nowait()["message"]) == "*#1013*0*1##"


def test_ssdp_model_cross_checked_by_who1013(dev_reg, issues):
    """SSDP MH202 answering 200: WHO=1013 confirms (5) or contradicts (67); the announcement is never overruled.

    The contradiction survives the periodic re-broadcast of the shared code that
    triggered the check, and is cleared only by a WHO=1013 reply that agrees.
    """
    create, delete, corrected = issues
    h = _handler({"name": "MH202", "ssdp_location": "http://192.0.2.40:49153/desc.xml"})
    h.gateway.model_name = "MH202"
    dev_reg.async_get.return_value = MagicMock(model="MH202")

    _who13(h, "200")
    _who1013(h, "5")  # MH202: agrees
    assert h._identity_conflict is None
    create.assert_not_called()
    assert h.gateway.model_name == "MH202"

    _who1013(h, "67")  # MyHomeServer1: the device announced MH202 - keep it, ask the owner
    assert h.gateway.model_name == "MH202"
    assert h._identity_conflict == (
        "configured as MH202 (ssdp) but WHO=1013 OBJECT_MODEL 67 identifies MyHomeServer1 per diagnostic catalogue"
    )
    create.assert_called_once()
    assert create.call_args.args[1:] == ("entry_ident", "MH202", "MyHomeServer1", "1013-1-67", "ssdp", True)
    corrected.assert_not_called()
    h.hass.config_entries.async_update_entry.assert_not_called()

    # the gateway broadcasts 200 again: no new request (answered), conflict untouched, no flapping issue
    delete.reset_mock()
    while not h._command_pool.send_buffer.empty():
        h._command_pool.send_buffer.get_nowait()
    _who13(h, "200")
    assert h._command_pool.send_buffer.empty()
    assert h._identity_conflict is not None
    delete.assert_not_called()
    create.assert_called_once()

    # an agreeing WHO=1013 reply clears it
    _who1013(h, "5")
    assert h._identity_conflict is None
    delete.assert_called_once_with(h.hass, "entry_ident")

    # WHO=1013 outranks WHO=13: an official code arriving later does not reopen the question
    _who13(h, "4")
    assert h._identity_conflict is None


def test_serial_model_cross_checked_by_who1013(dev_reg, issues):
    """A serial-fixed identity is treated like SSDP: cross-checked, never relabelled."""
    create, delete, corrected = issues
    h = _handler({"transport_type": "serial", "name": "MH200"})
    h.gateway.model_name = "MH200"
    dev_reg.async_get.return_value = MagicMock(model="MH200")
    _who13(h, "200")  # no serial gateway is known to answer this; the path must still be sound
    assert str(h._command_pool.send_buffer.get_nowait()["message"]) == "*#1013*0*1##"
    _who1013(h, "4")  # MH200: corroborated
    assert h._identity_conflict is None
    _who1013(h, "44")  # MH200N: same family, but a product with a code of its own
    assert h.gateway.model_name == "MH200"
    assert create.call_args.args[1:] == ("entry_ident", "MH200", "MH200N", "1013-1-44", "serial", True)
    _who1013(h, "51")  # F454
    assert h.gateway.model_name == "MH200"
    assert "configured as MH200 (serial)" in h._identity_conflict
    assert create.call_args.args[1:] == ("entry_ident", "MH200", "F454", "1013-1-51", "serial", True)
    corrected.assert_not_called()


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


def test_unknown_entry_with_ambiguous_code_stays_generic(dev_reg, issues):
    """Code 200 is ambiguous between F454 and MyHomeServer1: cannot auto-label an unknown gateway."""
    _, _, corrected = issues
    h = _handler({"name": "Generic"}, title="Generic Gateway")
    h.gateway.model_name = "Generic"
    dev_reg.async_get.return_value = MagicMock(model="Generic")
    _who13(h, "200")
    assert h.gateway.model_name == "Generic"
    h.hass.config_entries.async_update_entry.assert_not_called()
    dev_reg.async_update_device.assert_not_called()
    corrected.assert_not_called()
    assert h._identity_conflict is None
    # Verify the diagnostic request is queued to disambiguate the gateway.
    queued = h._command_pool.send_buffer.get_nowait()
    assert str(queued["message"]) == "*#1013*0*1##"
    assert queued["is_status_request"] is True
    # unanswered: the request is pending, a re-broadcast does not repeat it
    _who13(h, "200")
    assert h._command_pool.send_buffer.empty()
    # ...until the event session reconnects
    h._on_event_connection_state_change(True)
    _who13(h, "200")
    assert h._command_pool.send_buffer.qsize() == 1
    h._command_pool.send_buffer.get_nowait()
    # answered: labelled from the catalogue, and no further request
    _who1013(h, "51")
    assert h.gateway.model_name == "F454"
    kwargs = h.hass.config_entries.async_update_entry.call_args.kwargs
    assert kwargs["data"]["name"] == "F454" and kwargs["data"]["model_source"] == IDENTIFICATION_WHO13
    assert kwargs["title"] == "F454 Gateway"
    corrected.assert_not_called()  # nothing was corrected: there was no model
    _who13(h, "200")
    assert h._command_pool.send_buffer.empty()

def test_mh200_unambiguous_who13_skips_who1013(dev_reg, issues):
    """Anonymous golden sample: a physical MH200 correctly returning WHO=13 DIM=15 value 4
    is correctly labelled as an MH200, and does not incorrectly trigger the WHO=1013 diagnostic frame
    (validating @anotherjulien's approach).
    """
    _, _, corrected = issues
    h = _handler({"name": "Generic"}, title="Generic Gateway")
    h.gateway.model_name = "Generic"
    dev_reg.async_get.return_value = MagicMock(model="Generic")
    _who13(h, "4")
    assert h.gateway.model_name == "MH200"

    # Verify we did NOT query WHO=1013 DIM=1.
    assert h._command_pool.send_buffer.empty()



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
    """PR #345 review: an entry labelled MH200N by WHO=13, then switched to MH200 in the
    options flow, must not be flipped back by the next device-type 44 reply (Nmap's
    MH200N code: field evidence, which labels but never contradicts)."""
    create, delete, corrected = issues
    # Before the options flow: the entry was labelled from WHO=13 and would be relabelled.
    h = _handler({"name": "MH200", "model_source": "who13"})
    h.gateway.model_name = "MH200"
    _who13(h, "44")
    assert h.gateway.model_name == "MH200N"  # the bug the review reproduced
    # After the options flow: the selection is recorded as manual and stays intact.
    h = _handler({"name": "MH200", "model_source": "manual"})
    h.gateway.model_name = "MH200"
    dev_reg.async_get.return_value = MagicMock(model="MH200")
    _who13(h, "44")
    assert h.gateway.model_name == "MH200"
    assert h.config_entry.data["name"] == "MH200"
    assert h._identity_conflict is None
    h.hass.config_entries.async_update_entry.assert_not_called()
    create.assert_not_called()
    corrected.assert_not_called()


def test_stale_identity_issue_is_cleared_by_a_fresh_handler(dev_reg, issues):
    """PR #345 review: a reload creates a handler with no conflict in memory, but the
    previous instance's warning is still in the issue registry; a matching reply must remove it."""
    create, delete, corrected = issues
    first = _handler({"name": "MH200", "ssdp_location": "http://192.168.1.40:49153/desc.xml"})
    first.gateway.model_name = "MH200"
    dev_reg.async_get.return_value = MagicMock(model="MH200")
    _who13(first, "6")  # official F452 code contradicts announced MH200: issue created
    assert first._identity_conflict is not None
    create.assert_called_once()
    delete.assert_not_called()

    second = _handler({"name": "MH200", "ssdp_location": "http://192.168.1.40:49153/desc.xml"})
    second.gateway.model_name = "MH200"
    dev_reg.async_get.return_value = MagicMock(model="MH200")
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
    # MH200NProfile up to OWNd 2.0.0b8, MH200Profile from OWNd#53 on
    assert ident["profile"] == type(get_gateway_profile("MH200")).__name__


# ── the evidence travels with diagnostics and the WebSocket / trace payload ──


def test_websocket_gateway_info_carries_identification_without_location():
    from custom_components.myhome.websocket import _extract_gateway_info

    h = _handler({"ssdp_location": "http://192.0.2.40:49153/desc.xml"})
    h._who13["code"] = "4"
    h._who1013["code"] = "44"
    h._who1013["model"] = "MH200N"
    info = _extract_gateway_info(h, "2.0.0b6")
    assert info["identification"]["source"] == IDENTIFICATION_SSDP
    assert info["identification"]["who13_code"] == "4"
    assert info["identification"]["who1013_code"] == "44"
    assert info["identification"]["who1013_model"] == "MH200N"
    assert "ssdp_location" not in info["identification"]


def test_websocket_gateway_info_tolerates_gateway_without_identification():
    from custom_components.myhome.websocket import _extract_gateway_info

    gw = MagicMock(spec=[])  # no identification attribute at all
    assert _extract_gateway_info(gw)["identification"] == {}


def test_apply_model_does_not_rewrite_an_entry_that_already_carries_it(dev_reg):
    h = _handler()
    h.config_entry.data["name"] = "F452"
    h._apply_model("F452")
    assert h.gateway.model_name == "F452" and h.profile is not None
    h.hass.config_entries.async_update_entry.assert_not_called()


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
    # the id is set but the registry no longer holds that device (removed under us)
    h.device_registry_id = "dev_gw"
    dev_reg.async_get.return_value = None
    h._sync_device_registry_model("MH200")
    dev_reg.async_update_device.assert_not_called()


def _who1013(h, code):
    h._handle_gateway_identity_diagnostics(OWNEvent.parse(f"*#1013**1*{code}##"))


# ── golden samples: real WHO=13 / WHO=1013 exchanges captured on hardware (PR #420) ──

FIXTURES_PLANTS_DIR = Path(__file__).resolve().parent / "fixtures" / "plants"


@pytest.mark.parametrize(
    ("plant", "model", "object_model", "firmware"),
    [
        ("pr_420_f454", "F454", "51", "2.0.51"),
        ("pr_420_mh202", "MH202", "5", "1.0.21"),
        ("pr_420_myhomeserver1", "MyHomeServer1", "67", "2.87.13"),
    ],
)
def test_golden_who1013_exchange(dev_reg, issues, plant, model, object_model, firmware):
    """Replay the identification frames of a physical gateway, in the order the bus produced them.

    All three announced themselves over SSDP, answer the shared WHO=13 device type 200,
    and answer ``*#1013*0*1##`` with their catalogue OBJECT_MODEL followed by N_CONF,
    BRAND and LINE (``*15*5*0`` on all three). The handler must queue exactly the
    request the reporter's installation sent, and end corroborated, not in conflict.
    """
    create, delete, corrected = issues
    diag = json.loads((FIXTURES_PLANTS_DIR / plant / "diagnostic_summary.json").read_text(encoding="utf-8"))
    frames = diag["data"]["bus_monitor"]["recent_frames"]
    assert diag["data"]["gateway"]["firmware"] == firmware
    h = _handler({"name": model, "ssdp_location": "http://192.0.2.1:49153/description.xml", "firmware": firmware})
    h.gateway.model_name = model
    dev_reg.async_get.return_value = MagicMock(model=model)

    sent: list[str] = []  # what the reporter's installation (2.0.0b13, by hand) put on the bus
    queued: list[str] = []  # what this handler puts on the bus
    for frame in frames:
        raw = frame["raw"]
        if not (raw.startswith("*#13*") or raw.startswith("*#1013*")):
            continue
        if frame["direction"] == "tx":
            sent.append(raw)
            continue
        msg = OWNEvent.parse(raw)
        if raw.startswith("*#13*"):
            h._handle_gateway_diagnostics(msg)
        else:
            h._handle_gateway_identity_diagnostics(msg)
        while not h._command_pool.send_buffer.empty():
            item = h._command_pool.send_buffer.get_nowait()
            assert item["is_status_request"] is True
            queued.append(str(item["message"]))
    # one request, the same frame the reporter sent by hand to produce the reply
    assert queued == ["*#1013*0*1##"]
    assert "*#1013*0*1##" in sent

    ident = h.identification()
    assert ident["source"] == IDENTIFICATION_SSDP
    assert ident["who13_code"] == "200"
    assert ident["who1013_code"] == object_model
    assert ident["who1013_model"] == model
    assert ident["model"] == model and h.gateway.model_name == model
    assert ident["conflict"] is None
    assert h._who1013["pending"] is False
    create.assert_not_called()
    corrected.assert_not_called()
    h.hass.config_entries.async_update_entry.assert_not_called()
    # the same evidence, resolved on its own, says the same
    verdict = resolve(Evidence(technical=model, technical_source=IDENTIFICATION_SSDP, who13_code="200", who1013_code=object_model))
    assert (verdict.model, verdict.conflict, verdict.request_who1013) == (model, None, False)


def test_real_who1013_reply_is_object_model_n_conf_brand_line(dev_reg, issues):
    """``*#1013**1*67*15*5*0##`` as a MyHomeServer1 really answers it.

    The reply is ``OBJECT_MODEL * N_CONF * BRAND * LINE`` (#420, from the OpenWebNet
    Encyclopedia's work on MHCatalogue.db). Only OBJECT_MODEL identifies the model;
    the rest is metadata this integration does not read yet, so the point of this
    test is that it is parsed and then ignored, not silently taken for model data.
    """
    def dimension_values(message):
        """OWNd exposes this as a private attribute on some message classes."""
        return getattr(message, "dimension_value", getattr(message, "_dimension_value", None))

    msg = OWNEvent.parse("*#1013**1*67*15*5*0##")
    values = dimension_values(msg)
    assert values is not None
    object_model, n_conf, brand, line = values
    assert (object_model, n_conf, brand, line) == ("67", "15", "5", "0")

    h = _handler({"name": "Generic"}, title="Generic Gateway")
    h.gateway.model_name = "Generic"
    h._handle_gateway_identity_diagnostics(msg)
    assert h.gateway.model_name == "MyHomeServer1"  # from OBJECT_MODEL alone
    ident = h.identification()
    assert ident["who1013_code"] == "67"
    # N_CONF / BRAND / LINE are not mistaken for the model or recorded as evidence
    assert ident["who1013_model"] == "MyHomeServer1"
    assert n_conf not in (ident["who1013_code"], ident["who13_code"])

    # every traced gateway answers the same trailing metadata
    for plant in ("pr_420_f454", "pr_420_mh202", "pr_420_myhomeserver1"):
        diag = json.loads((FIXTURES_PLANTS_DIR / plant / "diagnostic_summary.json").read_text(encoding="utf-8"))
        replies = [f["raw"] for f in diag["data"]["bus_monitor"]["recent_frames"] if f["raw"].startswith("*#1013**1*")]
        assert replies, plant
        for raw in replies:
            assert dimension_values(OWNEvent.parse(raw))[1:] == ["15", "5", "0"], (plant, raw)

def test_alternative_names_are_brand_variants_not_order_codes():
    """003598 is what Legrand calls a BTicino F454 - one product, two houses (#420).

    They are therefore kept as names the gateway may legitimately announce, never
    treated as a separate model or used as an identifier.
    """
    reading = read_who1013("51")
    assert reading.canonical == "F454"
    assert reading.alternative_names == ("003598",)
    assert reading.compatible_with("003598") is True
    # a code with a single name has no variants
    assert read_who1013("67").alternative_names == ()
    assert read_who1013("999").alternative_names == ()


def test_who1013_metadata_is_recorded_and_described(dev_reg, issues):
    """N_CONF / BRAND / LINE are kept and rendered, without ever touching the identity."""
    h = _handler({"name": "Generic"}, title="Generic Gateway")
    h.gateway.model_name = "Generic"
    h._handle_gateway_identity_diagnostics(OWNEvent.parse("*#1013**1*51*15*5*0##"))

    ident = h.identification()
    assert ident["who1013_code"] == "51"
    assert ident["who1013_model"] == "F454"
    assert ident["who1013_other_names"] == ["003598"]
    assert ident["who1013_n_conf"] == "15"
    assert ident["who1013_brand"] == "5 (Legrand BTicino)"
    assert ident["who1013_line"] == "0 (Undefined)"
    # the model came from OBJECT_MODEL alone
    assert h.gateway.model_name == "F454"


def test_who1013_metadata_tolerates_unknown_and_missing_values(dev_reg, issues):
    """An unseen brand or line still reaches diagnostics; a short reply leaves fields unset."""
    h = _handler({"name": "Generic"}, title="Generic Gateway")
    h.gateway.model_name = "Generic"
    h._handle_gateway_identity_diagnostics(OWNEvent.parse("*#1013**1*67*3*9*7##"))
    ident = h.identification()
    assert ident["who1013_n_conf"] == "3"
    assert ident["who1013_brand"] == "9"  # not in WHO1013_BRANDS: reported raw, not dropped
    assert ident["who1013_line"] == "7"

    short = _handler({"name": "Generic"}, title="Generic Gateway")
    short.gateway.model_name = "Generic"
    short._handle_gateway_identity_diagnostics(OWNEvent.parse("*#1013**1*67##"))
    ident = short.identification()
    assert ident["who1013_code"] == "67" and ident["who1013_model"] == "MyHomeServer1"
    assert ident["who1013_n_conf"] is None
    assert ident["who1013_brand"] is None and ident["who1013_line"] is None


def test_brand_and_line_tables_hold_only_observed_values():
    assert WHO1013_BRANDS == {"5": "Legrand BTicino"}
    assert WHO1013_LINES == {"0": "Undefined"}


def test_device_registry_carries_model_and_model_id(dev_reg, issues):
    """The card shows the model name; model_id is the number the gateway gave for itself."""
    h = _handler({"name": "Generic"}, title="Generic Gateway")
    h.gateway.model_name = "Generic"
    dev_reg.async_get.return_value = MagicMock(model="Generic", model_id=None)

    _who13(h, "200")
    _who1013(h, "51")
    dev_reg.async_update_device.assert_called_once_with("dev_gw", model="F454", model_id="51")

    # already in step: nothing is rewritten
    dev_reg.async_update_device.reset_mock()
    dev_reg.async_get.return_value = MagicMock(model="F454", model_id="51")
    _who1013(h, "51")
    dev_reg.async_update_device.assert_not_called()

    # a WHO=13-only identification sets the model and leaves model_id alone
    other = _handler({"name": "Generic"}, title="Generic Gateway")
    other.gateway.model_name = "Generic"
    dev_reg.async_update_device.reset_mock()
    dev_reg.async_get.return_value = MagicMock(model="Generic", model_id=None)
    _who13(other, "6")
    dev_reg.async_update_device.assert_called_once_with("dev_gw", model="F452")


def test_who1013_unknown_code_is_reported_like_an_unknown_who13_code(dev_reg, issues):
    """A code outside the catalogue keeps the model and raises the same unknown-model repair as WHO=13 does."""
    h = _handler({"name": "Generic"}, title="Generic Gateway")
    h.gateway.model_name = "Generic"
    with patch("custom_components.myhome.gateway.async_create_unknown_model_issue") as unknown, \
         patch("custom_components.myhome.gateway.async_delete_unknown_model_issue") as known:
        _who1013(h, "999")
        assert h.gateway.model_name == "Generic"
        unknown.assert_called_once_with(h.hass, "entry_ident", "1013-1-999")
        known.assert_not_called()
        ident = h.identification()
        assert ident["who1013_code"] == "999" and ident["who1013_model"] is None
        # the shared WHO=13 code that keeps being broadcast neither re-asks nor withdraws the request
        _who13(h, "200")
        assert h._command_pool.send_buffer.empty()
        known.assert_not_called()
        unknown.assert_called_with(h.hass, "entry_ident", "1013-1-999")
        # a recognised reply afterwards clears the request for a trace
        _who1013(h, "5")
        known.assert_called_once_with(h.hass, "entry_ident")
        assert h.gateway.model_name == "MH202"

def test_who1013_updates_manual_model(dev_reg, issues):
    h = _handler({"name": "Generic", "model_source": "manual"}, title="Generic Gateway")
    h.gateway.model_name = "Generic"
    _who1013(h, "5") # 5 is MH202
    assert h.gateway.model_name == "MH202"
    h.hass.config_entries.async_update_entry.assert_called_once()
    kwargs = h.hass.config_entries.async_update_entry.call_args.kwargs
    assert kwargs["data"]["name"] == "MH202"
    assert kwargs["title"] == "MH202 Gateway"
    dev_reg.async_update_device.assert_called_once_with("dev_gw", model="MH202", model_id="5")

def test_who1013_ssdp_conflict(dev_reg, issues):
    h = _handler({"name": "F454", "ssdp_location": "http://192.0.2.40:49153/desc.xml"}, title="F454 Gateway")
    h.gateway.model_name = "F454"
    _who1013(h, "4") # 4 is MH200
    assert h.gateway.model_name == "F454" # Does not update
    assert "identifies MH200 per diagnostic catalogue" in h._identity_conflict

def test_who1013_ssdp_compatible(dev_reg, issues):
    h = _handler({"name": "MH200", "ssdp_location": "http://192.0.2.40:49153/desc.xml"}, title="MH200 Gateway")
    h.gateway.model_name = "MH200"
    _who1013(h, "4") # 4 is MH200 (same family)
    assert h.gateway.model_name == "MH200"
    assert h._identity_conflict is None

async def test_who1013_is_dispatched(dev_reg, issues):
    """Cover the async _process_message dispatch for WHO=1013 DIM=1."""
    h = _handler({"name": "Generic"}, title="Generic Gateway")
    h.gateway.model_name = "Generic"
    # Valid dimension 1
    await h._event_dispatcher.process_message(OWNEvent.parse("*#1013**1*5##"))
    assert h.gateway.model_name == "MH202"
    # Unhandled dimension
    await h._event_dispatcher.process_message(OWNEvent.parse("*#1013**2*5##"))
    # Unsupported who fallback (covered elsewhere typically, but good to ensure no crash)
    await h._event_dispatcher.process_message(OWNEvent.parse("*#9999**1*5##"))

def test_who13_ambiguous_handles_queue_full(dev_reg, issues):
    """Cover the QueueFull exception when dispatching the WHO=1013 diagnostic."""
    import asyncio
    h = _handler({"name": "Generic"}, title="Generic Gateway")
    h.gateway.model_name = "Generic"

    # Fill the queue (maxsize is 0 by default, let us replace it with maxsize 1 and fill it)
    h._command_pool.send_buffer = asyncio.Queue(maxsize=1)
    h._command_pool.send_buffer.put_nowait({"message": "filler"})

    # This will attempt to queue *#1013*0*1## but fail with QueueFull
    _who13(h, "200")
    assert h._who1013["pending"] is False  # nothing left the handler: the next broadcast retries
    # and the parse guard: a request that cannot be built is skipped, not queued
    h._command_pool.send_buffer = asyncio.Queue()
    with patch("custom_components.myhome.gateway.OWNCommand.parse", return_value=None):
        h._request_object_model()
    assert h._command_pool.send_buffer.empty() and h._who1013["pending"] is False
    h._command_pool.send_buffer = asyncio.Queue(maxsize=1)
    h._command_pool.send_buffer.put_nowait({"message": "filler"})

    # It should still remain Generic and not crash
    assert h.gateway.model_name == "Generic"
    assert h._command_pool.send_buffer.qsize() == 1

def test_who1013_invalid_dimension_value(dev_reg, issues):
    """Cover the early return when dimension value is missing."""
    h = _handler({"name": "Generic"}, title="Generic Gateway")
    h.gateway.model_name = "Generic"

    # Create an OWNEvent with no dimension values
    msg = OWNEvent.parse("*#1013**1##")
    h._handle_gateway_identity_diagnostics(msg)

    # Should safely return without changes
    assert h.gateway.model_name == "Generic"
