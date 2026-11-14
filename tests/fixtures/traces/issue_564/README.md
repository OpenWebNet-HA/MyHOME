# Issue #564: MH202 Secondary Gateway Burglar Alarm (WHO 5) Trace

## Provenance
- **Source**: Contributed by @nicolacavallo84 in [issue #564 comment 5917195080](https://github.com/OpenWebNet-HA/MyHOME/issues/564#issuecomment-5917195080)
- **Gateway Model**: BTicino MH202 (Scenario programmer / IP gateway)
- **Firmware**: 1.0
- **Home Assistant Version**: 2026.9.4
- **Integration Version**: 2.0.0b14
- **OWNd Protocol Engine**: 2.0.0b9
- **Topology Context**: Secondary gateway alongside primary MyHomeServer1 (MHS1). Arming and disarming performed on-bus via MyHomeTouch10 screen.
- **Subsystem**: WHO = 5 (Burglar Alarm / Antifurto)

## Key Wire Frames
- `*5*1*0##`: System arm away (total)
- `*5*9*0##`: System disarm
- `*5*11*#n##`: Zone n active / armed
- `*5*18*#n##`: Zone n inactive / idle
