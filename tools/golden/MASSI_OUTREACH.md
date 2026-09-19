# Massi Valla (mvalla) Outreach Template

> **Context**: Draft message to reach out to Massi Valla (author/maintainer of `openwebnet4j` and openHAB OpenWebNet binding).
> **Status**: On hold as requested while corpus harvesting and internal alignment expand.
> **Objective**: Propose an open, cross-community "OpenWebNet Golden Corpus" to align frame parsing/building across openHAB, Home Assistant, and Legrand specifications.

---

### Subject: Cross-ecosystem OpenWebNet Golden Corpus (openHAB & Home Assistant conformance)

Hi Massi,

First off, huge thank you for your decade of incredible work on `openwebnet4j` and the openHAB OpenWebNet binding. Your library has long been the gold standard for reliable OpenWebNet communication in open-source home automation.

Over in the Home Assistant MyHOME integration (`OpenWebNet-HA/MyHOME`), we've been building a standardized **OpenWebNet Golden Corpus**—a declarative, language-agnostic test suite of frames in YAML/JSON.

The core motivation is simple: OpenWebNet frame edge cases (private bus `#4#bus` routing, leading zeros in A/PL addresses like `0311`, dimension writes, and CEN/CEN+ presses) often lead to subtle divergence across Python, Java, and other implementations.

### How it works
We use a 3-authority conformance model:
1. **Judge**: Official Legrand specification PDFs (verified via the `openwebnet-mcp` protocol engine).
2. **Oracle**: `openwebnet4j` & openHAB reference vectors (`MessageTest.java` and `OwnIdTest.java`).
3. **SUT**: Our Python runtime (`OWNd` / `MyHOME`).

We have harvested and cross-validated **58 fixtures across 11 OpenWebNet subsystems** (Lighting, Automation, Thermoregulation, Alarm, Auxiliary, Gateway Mgmt, CEN, CEN+, Energy, Scenarios, Signaling) with a 100% roundtrip pass rate (137 automated assertions).

Each fixture is validated against a strict JSON Schema:

```yaml
- id: light.cmd.on.bus.0311
  frame: "*1*1*0311#4#01##"
  direction: command
  who: 1
  what: 1
  where: "0311"
  interface: "01"
  source: openwebnet4j
  mcp_valid: true
  mcp_notes: "Lighting: Turn ON at address '0311' routed to private SCS bus 01"
  roundtrip: true
```

### Proposal & Collaboration
We would love your thoughts on making this golden corpus an independent, shared standard that benefits both openHAB and Home Assistant:
1. **Schema Review**: Would you be open to reviewing the schema structure to ensure it accommodates all nuances of `openwebnet4j`?
2. **Shared Conformance**: If you think this is worthwhile, we would be delighted to do the legwork to contribute a test runner or exporter to `openwebnet4j` so both projects can run against the exact same declarative conformance suite.

Zero obligation or pressure, of course. If you're interested, you can explore the schema, harvest report, and fixtures here:
https://github.com/OpenWebNet-HA/MyHOME/tree/v2-phase1-architecture/tests/golden

Thanks again for all your pioneering work for the MyHOME community!

Best regards,  
The OpenWebNet-HA Team
