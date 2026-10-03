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

from dataclasses import dataclass

from .const import (
    IDENTIFICATION_MANUAL,
    IDENTIFICATION_SSDP,
    IDENTIFICATION_UNKNOWN,
    IDENTIFICATION_WHO13,
    WHO13_OBSERVED_DEVICE_TYPES,
    WHO13_OFFICIAL_DEVICE_TYPES,
    WHO13_SHARED_DEVICE_TYPES,
    WHO13_THIRD_PARTY_DEVICE_TYPES,
    WHO1013_OBJECT_MODELS,
    gateway_model_family,
)


def _normalized(model: str | None) -> str:
    """Compare model names without case, spaces, dashes or underscores."""
    return str(model or "").strip().upper().replace(" ", "").replace("-", "").replace("_", "")


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
