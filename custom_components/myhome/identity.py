"""Gateway identity: evidence in, one verdict out.

The protocol handlers only record what they observed - the model picked in the
config flow, the model the gateway announced over SSDP or that the serial
transport fixes, the WHO=13 dimension-15 device type, the WHO=1013 dimension-1
OBJECT_MODEL. ``resolve_gateway_identity`` turns that evidence into the model the
integration should believe, plus the corroboration or conflict state that goes
with it. It is a pure function of its input, so applying it twice changes nothing,
and the periodic re-broadcast of a WHO=13 reply cannot flap a repair issue.

Precedence, unchanged from the handler-side rules it replaces:

- an SSDP or serial identity is never overruled (the device said so itself); a
  certain contradiction raises a *mismatch* that asks the owner to confirm;
- a manual choice is kept unless a certain code contradicts it, and is then
  *corrected*;
- with no trustworthy model (none configured, or a label an earlier resolution
  wrote), the in-band evidence labels the gateway;
- WHO=1013 outranks WHO=13 when both are present: its catalogue is one code per
  model, while a WHO=13 code may be shared by several modern gateways, and the
  two do not necessarily agree for the same product;
- a shared WHO=13 code is not evidence of any model; it is the cue to ask WHO=1013.

"Certain" means the 2006 specification (official WHO=13 codes) or the WHO=1013
catalogue. A WHO=13 code known from field evidence only can corroborate a model,
never contradict it.
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from homeassistant.const import CONF_NAME
from homeassistant.core import HomeAssistant
from OWNd.connection import OWNGateway

from .const import (
    CONF_FIRMWARE,
    CONF_SSDP_LOCATION,
    CONF_UDN,
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

# Labels that name no model: an entry carrying one is labelled from the in-band evidence.
UNTRUSTED_MODEL_LABELS: frozenset[str] = frozenset({"", "generic", "gateway", "unknown"})


def _normalized(model: str | None) -> str:
    """Compare model names without case, spaces, dashes, slashes or underscores."""
    return str(model or "").strip().upper().replace(" ", "").replace("-", "").replace("_", "").replace("/", "")


def _same_product_names() -> dict[str, frozenset[str]]:
    """Every model name a table lists -> the names of that one product (itself and its brand variants).

    A shared code lists distinct models, not one product under several names, so
    it contributes nothing.
    """
    entries: list[tuple[str, ...]] = [(model,) for model in WHO13_OFFICIAL_DEVICE_TYPES.values()]
    entries += [
        models
        for code, models in WHO13_OBSERVED_DEVICE_TYPES.items()
        if code not in WHO13_SHARED_DEVICE_TYPES
    ]
    entries += list(WHO13_THIRD_PARTY_DEVICE_TYPES.values())
    entries += list(WHO1013_OBJECT_MODELS.values())
    names: dict[str, frozenset[str]] = {}
    for models in entries:
        product = frozenset(_normalized(m) for m in models)
        for name in product:
            names[name] = names.get(name, frozenset()) | product
    return names


_SAME_PRODUCT = _same_product_names()


@dataclass(frozen=True)
class CodeReading:
    """What one in-band code means, according to the tables."""

    label: str  # "WHO=13 device type" / "WHO=1013 OBJECT_MODEL"
    code: str  # the value on the wire
    raw: str  # the form repair issues carry: "4", "1013-1-67"
    models: tuple[str, ...]  # every name the code stands for (brand variants); () = unknown
    basis: str  # what the models rest on, for issue text
    certain: bool  # a contradiction is proof, not a hint
    shared: bool  # answered by several distinct models: identifies none

    @property
    def known(self) -> bool:
        return bool(self.models)

    @property
    def canonical(self) -> str:
        """The model name to label a gateway with (the first in the table)."""
        return self.models[0]

    @property
    def alternative_names(self) -> tuple[str, ...]:
        """The same product under another brand, e.g. Legrand's 003598 for a BTicino F454.

        Not order codes or model numbers: one piece of hardware, two houses selling
        it (#420). They are recorded so a gateway announcing the Legrand name over
        SSDP is corroborated, and so diagnostics can show the owner the name on
        their box even though the BTicino one is displayed.
        """
        return self.models[1:]

    def compatible_with(self, model: str | None) -> bool | None:
        """Does ``model`` name the product this code stands for?

        True when it does, False when it does not and the code is certain, None
        when it does not but the code is field evidence only (unverified).

        A model some table lists by name has a code of its own, so only that name
        or a brand variant of it agrees: an MH200N (44) is contradicted by code 4
        (MH200), although both reduce to the MH200 family. A name no table lists
        (a variant suffix, a spelling the tables do not carry) is judged by family.
        """
        names = _SAME_PRODUCT.get(_normalized(model))
        if names is not None:
            if names & {_normalized(m) for m in self.models}:
                return True
        else:
            family = gateway_model_family(model)
            if family and family in {gateway_model_family(m) for m in self.models}:
                return True
        return False if self.certain else None

    def describe(self) -> str:
        return f"{self.label} {self.code}"


def read_who13(code: str) -> CodeReading:
    """Interpret a WHO=13 dimension-15 device type.

    Three sources, in descending order of authority: the 2006 specification, what
    this project has observed on real hardware, and a third-party implementation
    (Nmap). Only the specification is certain - the other two can label a gateway
    that has no model and corroborate one that has, but never contradict it.
    """
    official = WHO13_OFFICIAL_DEVICE_TYPES.get(code)
    if official:
        models: tuple[str, ...] = (official,)
        basis = "the OpenWebNet specification"
    elif code in WHO13_OBSERVED_DEVICE_TYPES:
        models = WHO13_OBSERVED_DEVICE_TYPES[code]
        basis = "field evidence"
    else:
        models = WHO13_THIRD_PARTY_DEVICE_TYPES.get(code, ())
        basis = "an independent implementation"
    return CodeReading(
        label="WHO=13 device type",
        code=code,
        raw=code,
        models=models,
        basis=basis,
        certain=bool(official),
        shared=code in WHO13_SHARED_DEVICE_TYPES,
    )


def read_who1013(code: str) -> CodeReading:
    """Interpret a WHO=1013 dimension-1 OBJECT_MODEL."""
    return CodeReading(
        label="WHO=1013 OBJECT_MODEL",
        code=code,
        raw=f"1013-1-{code}",
        models=tuple(WHO1013_OBJECT_MODELS.get(code, ())),
        basis="diagnostic catalogue",
        certain=True,
        shared=False,
    )


@dataclass(frozen=True)
class GatewayIdentityEvidence:
    """Everything observed about the gateway's model, each source kept apart."""

    manual: str | None = None  # picked in the config or options flow
    technical: str | None = None  # announced over SSDP, or fixed by the serial transport
    technical_source: str | None = None  # IDENTIFICATION_SSDP / IDENTIFICATION_SERIAL
    prior_label: str | None = None  # written to the entry by an earlier in-band resolution
    who13_code: str | None = None  # WHO=13 dimension 15
    who1013_code: str | None = None  # WHO=1013 dimension 1


@dataclass(frozen=True)
class GatewayIdentityResolution:
    """What the integration should believe, and what follows from it."""

    model: str | None  # effective model; None when nothing is known
    source: str  # the evidence `model` rests on (IDENTIFICATION_*)
    conflict: str | None = None  # a certain contradiction of an SSDP / serial identity
    conflict_reading: CodeReading | None = None  # the code that contradicts
    corrected_from: str | None = None  # the manual model `model` replaces
    corrected_reading: CodeReading | None = None  # the code that corrects it
    unknown_readings: tuple[CodeReading, ...] = ()  # codes in no table: ask for a trace
    request_who1013: bool = False  # WHO=13 was shared and WHO=1013 has not answered
    who13_shared: bool = False  # for the log line only

    @property
    def unknown_code(self) -> str | None:
        """The code an unknown-model repair issue should name (the latest question asked)."""
        return self.unknown_readings[-1].raw if self.unknown_readings else None


def resolve_gateway_identity(evidence: GatewayIdentityEvidence) -> GatewayIdentityResolution:
    """Decide the effective gateway model from the evidence collected so far."""
    who13 = read_who13(evidence.who13_code) if evidence.who13_code is not None else None
    who1013 = read_who1013(evidence.who1013_code) if evidence.who1013_code is not None else None
    unknown = tuple(r for r in (who13, who1013) if r is not None and not r.known)
    shared = who13 is not None and who13.shared
    request = shared and evidence.who1013_code is None

    # The configured identity and how far it can be trusted.
    if evidence.technical:
        configured: str | None = evidence.technical
        source = evidence.technical_source or IDENTIFICATION_SSDP
        authoritative, trusted = True, True
    elif evidence.manual:
        configured, source = evidence.manual, IDENTIFICATION_MANUAL
        authoritative, trusted = False, True
    else:
        configured = evidence.prior_label or None
        source = IDENTIFICATION_WHO13 if configured else IDENTIFICATION_UNKNOWN
        authoritative, trusted = False, False

    # The in-band evidence that can discriminate: WHO=1013 first, else a WHO=13
    # code that is known and not shared. A shared code alone says nothing.
    if who1013 is not None and who1013.known:
        inband: CodeReading | None = who1013
    elif who13 is not None and who13.known and not who13.shared:
        inband = who13
    else:
        inband = None

    def verdict(
        model: str | None,
        source: str,
        *,
        conflict: str | None = None,
        conflict_reading: CodeReading | None = None,
        corrected_from: str | None = None,
        corrected_reading: CodeReading | None = None,
    ) -> GatewayIdentityResolution:
        return GatewayIdentityResolution(
            model=model,
            source=source,
            conflict=conflict,
            conflict_reading=conflict_reading,
            corrected_from=corrected_from,
            corrected_reading=corrected_reading,
            unknown_readings=unknown,
            request_who1013=request,
            who13_shared=shared,
        )

    if inband is None:
        return verdict(configured, source)

    if not trusted:
        return verdict(inband.canonical, IDENTIFICATION_WHO13)

    if inband.compatible_with(configured) is False:
        if authoritative:
            conflict = (
                f"configured as {configured} ({source}) but {inband.describe()} "
                f"identifies {inband.canonical} per {inband.basis}"
            )
            return verdict(configured, source, conflict=conflict, conflict_reading=inband)
        return verdict(
            inband.canonical, IDENTIFICATION_WHO13, corrected_from=configured, corrected_reading=inband
        )

    # compatible (True) or unverified (None): the configured model stands
    return verdict(configured, source)


class GatewayIdentityMixin:
    """Mixin providing gateway identity resolution, WHO=13/1013 telemetry, and repair syncing."""

    hass: HomeAssistant
    config_entry: Any
    gateway: OWNGateway
    device_registry_id: str | None
    _who13: dict[str, Any]
    _who1013: dict[str, Any]
    _identity_resolution: GatewayIdentityResolution | None
    _identity_conflict: str | None
    send_buffer: Any
    sending_workers: list[asyncio.Task[None]]

    if TYPE_CHECKING:

        @property
        def log_id(self) -> str: ...

        @property
        def model(self) -> str: ...

        @property
        def profile(self) -> Any: ...

    @property
    def identification_source(self) -> str:
        """How the configured model was established (SSDP > manual > serial > WHO=13)."""
        data = getattr(self.config_entry, "data", None) or {}
        if data.get("transport_type") == "serial":
            return IDENTIFICATION_SERIAL
        if data.get(CONF_SSDP_LOCATION) or data.get(CONF_UDN):
            return IDENTIFICATION_SSDP
        model_source = data.get("model_source")
        if model_source == IDENTIFICATION_WHO13:
            return IDENTIFICATION_WHO13
        if model_source == IDENTIFICATION_MANUAL:
            return IDENTIFICATION_MANUAL
        model = data.get(CONF_NAME)
        if model and str(model).strip().lower() not in UNTRUSTED_MODEL_LABELS:
            return IDENTIFICATION_MANUAL
        return IDENTIFICATION_UNKNOWN

    def identification(self) -> dict[str, Any]:
        """Evidence behind the model label, for diagnostics and trace exports."""
        data = getattr(self.config_entry, "data", None) or {}
        return {
            "model": self.model,
            "source": self.identification_source,
            "configured_model": data.get(CONF_NAME),
            "ssdp_model": data.get(CONF_NAME) if self.identification_source == IDENTIFICATION_SSDP else None,
            "ssdp_location": data.get(CONF_SSDP_LOCATION) or None,
            "who13_code": self._who13["code"],
            "who13_model": self._who13["model"],
            "who13_model_official": self._who13["model_official"],
            "who13_model_observed": self._who13["model_observed"],
            "who13_firmware": self._who13["firmware"],
            "who13_kernel": self._who13["kernel"],
            "who13_distribution": self._who13["distribution"],
            "who1013_code": self._who1013["code"],
            "who1013_model": self._who1013["model"],
            "who1013_other_names": list(self._who1013["names"]),
            "who1013_n_conf": self._who1013["n_conf"],
            "who1013_brand": self._describe_who1013("brand", WHO1013_BRANDS),
            "who1013_line": self._describe_who1013("line", WHO1013_LINES),
            "profile": type(self.profile).__name__ if self.profile is not None else None,
            "conflict": self._identity_conflict,
        }

    def _describe_who1013(self, field: str, table: dict[str, str]) -> str | None:
        """Render a WHO=1013 metadata value as ``code (meaning)``, or the bare code."""
        value = self._who1013[field]
        if value is None:
            return None
        meaning = table.get(str(value))
        return f"{value} ({meaning})" if meaning else str(value)

    def _handle_gateway_diagnostics(self, message: Any) -> None:
        """Handle WHO=13 Gateway Management diagnostic telemetry."""
        from . import gateway as _gw

        dim = getattr(message, "dimension", None)
        dim_val = getattr(message, "dimension_value", [])

        if dim in (0, 22) and dim_val:
            if len(dim_val) > 3 and str(dim_val[3]) == "999":
                if self.config_entry:
                    if self.model.strip().upper() == "F461" or self._who1013.get("code") == "134":
                        _gw.async_delete_unconfigured_timezone_issue(self.hass, self.config_entry.entry_id)
                    else:
                        _gw.async_create_unconfigured_timezone_issue(
                            self.hass, self.config_entry.entry_id, self.config_entry.title
                        )
            elif len(dim_val) > 3 and str(dim_val[3]) != "":
                if self.config_entry:
                    _gw.async_delete_unconfigured_timezone_issue(self.hass, self.config_entry.entry_id)

        if dim == 15 and dim_val:
            self._handle_device_type(str(dim_val[0]))
        elif dim in (23, 24) and dim_val:
            self._who13["kernel" if dim == 23 else "distribution"] = ".".join(str(v) for v in dim_val)
        elif dim == 16:
            fw = getattr(message, "firmware_version", None)
            if fw:
                self._who13["firmware"] = fw
            if fw and fw != self.gateway.firmware:
                getattr(_gw, "LOGGER").info(
                    "%s Auto-detected gateway firmware `%s` via WHO=13 Dimension 16.",
                    self.log_id,
                    fw,
                )
                self.gateway.firmware = fw
                self.gateway.profile = _gw._profile_for_model(self.gateway.model_name, fw)
                if self.config_entry is not None:
                    new_data = dict(self.config_entry.data)
                    if new_data.get(CONF_FIRMWARE) != fw:
                        new_data[CONF_FIRMWARE] = fw
                        self.hass.config_entries.async_update_entry(self.config_entry, data=new_data)
                if self.device_registry_id:
                    dev_reg = _gw.dr.async_get(self.hass)
                    dev_reg.async_update_device(self.device_registry_id, sw_version=fw)

    def _handle_device_type(self, raw_code: str) -> None:
        """Record a WHO=13 dimension-15 reply and let the resolver decide what it means."""
        reading = read_who13(raw_code)
        self._who13["code"] = raw_code
        self._who13["model"] = reading.canonical if reading.known else None
        self._who13["model_official"] = reading.canonical if reading.certain else None
        self._who13["model_observed"] = (
            " / ".join(reading.models) if reading.known and not reading.certain else None
        )
        self._resolve_identity()

    def _handle_gateway_identity_diagnostics(self, message: Any) -> None:
        """Record a WHO=1013 dimension-1 (OBJECT_MODEL) reply and let the resolver decide."""
        dim_val = getattr(message, "dimension_value", [])
        if not dim_val or not isinstance(dim_val, list):
            return
        reading = read_who1013(str(dim_val[0]))
        self._who1013["code"] = reading.code
        self._who1013["model"] = reading.canonical if reading.known else None
        self._who1013["names"] = reading.alternative_names if reading.known else ()
        for index, field in enumerate(("n_conf", "brand", "line"), start=1):
            self._who1013[field] = str(dim_val[index]) if len(dim_val) > index else None
        self._who1013["pending"] = False
        self._resolve_identity()

    def _identity_evidence(self) -> GatewayIdentityEvidence:
        """Everything observed so far, each source kept apart."""
        source = self.identification_source
        configured = str(self.gateway.model_name or "") or None
        return GatewayIdentityEvidence(
            manual=configured if source == IDENTIFICATION_MANUAL else None,
            technical=configured if source in (IDENTIFICATION_SSDP, IDENTIFICATION_SERIAL) else None,
            technical_source=source if source in (IDENTIFICATION_SSDP, IDENTIFICATION_SERIAL) else None,
            prior_label=configured if source == IDENTIFICATION_WHO13 else None,
            who13_code=self._who13["code"],
            who1013_code=self._who1013["code"],
        )

    def _resolve_identity(self) -> None:
        """Decide the effective model from the evidence and bring everything in step with it."""
        from . import gateway as _gw

        log = getattr(_gw, "LOGGER")
        configured = str(self.gateway.model_name or "")
        resolution = resolve_gateway_identity(self._identity_evidence())
        entry_id = getattr(self.config_entry, "entry_id", None)
        entry_id = entry_id if isinstance(entry_id, str) else None
        changed = resolution != self._identity_resolution
        self._identity_resolution = resolution

        if resolution.request_who1013:
            if changed:
                log.info(
                    "%s WHO=13 reports device type %s (seen on multiple modern gateways); "
                    "keeping model `%s` until WHO=1013 answers.",
                    self.log_id,
                    self._who13["code"],
                    configured,
                )
            self._request_object_model()

        unknown = resolution.unknown_code
        if unknown:
            if changed:
                log.info(
                    "%s The gateway reports model code %s, unknown to the OpenWebNet tables and to field "
                    "evidence; keeping model `%s`. Please attach a trace to an issue so it can be documented.",
                    self.log_id,
                    unknown,
                    configured,
                )
            if entry_id:
                _gw.async_create_unknown_model_issue(self.hass, entry_id, unknown)
        elif entry_id:
            _gw.async_delete_unknown_model_issue(self.hass, entry_id)

        model = resolution.model or configured
        if ((model and model.strip().upper() == "F461") or self._who1013.get("code") == "134") and entry_id:
            _gw.async_delete_unconfigured_timezone_issue(self.hass, entry_id)
        if model and model.lower() != configured.lower():
            reading = resolution.corrected_reading
            log.warning(
                "%s Gateway model `%s` set from %s (was `%s`, source %s).",
                self.log_id,
                model,
                reading.describe() if reading else "in-band evidence",
                configured,
                self.identification_source,
            )
            self._apply_model(model)
            if resolution.corrected_from and reading is not None and entry_id:
                _gw.async_create_identity_corrected_issue(
                    self.hass, entry_id, resolution.corrected_from, model, reading.raw
                )

        reading = resolution.conflict_reading
        if resolution.conflict and reading is not None:
            if changed:
                log.warning("%s Gateway identity mismatch: %s.", self.log_id, resolution.conflict)
            self._set_conflict(
                resolution.conflict,
                entry_id,
                who13_model=reading.canonical,
                raw_code=reading.raw,
                source=resolution.source,
                official=reading.certain,
            )
        else:
            self._set_conflict(None, entry_id)
        self._sync_device_registry_model(model)

    def _apply_model(self, model: str) -> None:
        """Make ``model`` the entry's model: handler, profile, log id, config entry and title."""
        from . import gateway as _gw

        self.gateway.model_name = model
        self.gateway.model = model
        fw = getattr(self.gateway, "firmware", None)
        if not fw and self.config_entry:
            from .topology import entry_firmware

            fw = entry_firmware(self.config_entry)
        self.gateway.profile = _gw._profile_for_model(model, fw)
        self.gateway._log_id = f"[{model} gateway - {self.gateway.host}]"
        self._trim_sending_workers(model)
        new_data = dict(self.config_entry.data)
        if new_data.get(CONF_NAME) == model:
            return
        new_data[CONF_NAME] = model
        new_data["model_source"] = IDENTIFICATION_WHO13
        update_kwargs: dict[str, Any] = {"data": new_data}
        if str(getattr(self.config_entry, "title", "")).endswith("Gateway"):
            update_kwargs["title"] = f"{model} Gateway"
        self.hass.config_entries.async_update_entry(self.config_entry, **update_kwargs)

    def _trim_sending_workers(self, model: str) -> None:
        """Stop the command workers a corrected model has no sessions for."""
        from . import gateway as _gw

        limit = _gw.command_session_limit(model)
        if limit is None or len(self.sending_workers) <= limit:
            return
        getattr(_gw, "LOGGER").warning(
            "%s The %s accepts at most %d command session(s); stopping %d of %d command workers.",
            self.log_id,
            model,
            limit,
            len(self.sending_workers) - limit,
            len(self.sending_workers),
        )
        for worker in self.sending_workers[limit:]:
            worker.cancel()
        del self.sending_workers[limit:]

    def _request_object_model(self) -> None:
        """Queue ``*#1013**1##`` (Gateway Diagnostic, dimension 1 OBJECT_MODEL) once."""
        from . import gateway as _gw

        if self._who1013["pending"]:
            return
        cmd = getattr(_gw, "OWNCommand").parse("*#1013**1##")
        if cmd is None:
            return
        log = getattr(_gw, "LOGGER")
        log.debug(
            "%s Requesting WHO=1013 dimension 1 (OBJECT_MODEL) to settle the model.",
            self.log_id,
        )
        try:
            self.send_buffer.put_nowait(
                {"message": cmd, "written": self.hass.loop.create_future(), "is_status_request": True}
            )
        except asyncio.QueueFull:
            log.warning("%s Cannot queue the WHO=1013 request: send buffer full.", self.log_id)
            return
        self._who1013["pending"] = True

    def _set_conflict(self, conflict: str | None, entry_id: str | None, **issue: Any) -> None:
        """Track the identity conflict and keep the repair issue in step with it."""
        from . import gateway as _gw

        changed = conflict != self._identity_conflict
        self._identity_conflict = conflict
        if not entry_id:
            return
        if conflict:
            if changed:
                _gw.async_create_identity_issue(
                    self.hass,
                    entry_id,
                    str(self.gateway.model_name or ""),
                    issue["who13_model"],
                    issue["raw_code"],
                    issue["source"],
                    issue["official"],
                )
            return
        _gw.async_delete_identity_issue(self.hass, entry_id)

    def _sync_device_registry_model(self, model: str) -> None:
        """Keep the device registry in step with the resolved identity."""
        from . import gateway as _gw

        if not model or not self.device_registry_id:
            return
        dev_reg = _gw.dr.async_get(self.hass)
        device = dev_reg.async_get(self.device_registry_id)
        if device is None:
            return
        updates: dict[str, Any] = {}
        if getattr(device, "model", None) != model:
            updates["model"] = model
        model_id = self._who1013["code"]
        if model_id is not None and getattr(device, "model_id", None) != model_id:
            updates["model_id"] = model_id
        if updates:
            dev_reg.async_update_device(self.device_registry_id, **updates)

