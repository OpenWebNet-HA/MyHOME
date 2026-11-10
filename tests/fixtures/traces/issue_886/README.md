# Authentic Bus Log Trace: MyHomeServer1 with 41 Climate Zones – Startup Storm & FIFO Desync (Issue #886)

Authentic on-wire OpenWebNet trace extracted from the Home Assistant debug log contributed in GitHub issue [#886](https://github.com/OpenWebNet-HA/MyHOME/issues/886) on a physical BTicino `MyHomeServer1` serving a 41-zone thermoregulation plant (sanitized to RFC 5737 documentation IP `192.0.2.86` and synthetic MAC `00:03:50:00:08:86`).

## Provenance

- **Origin Issue**: https://github.com/OpenWebNet-HA/MyHOME/issues/886
- **Gateway**: BTicino `MyHomeServer1` (FW 3.87.13, `MyHomeServer1Profile`, 2 command workers, MAC prefix `00:03:50`)
- **Environment**: Home Assistant 2026.11.0, MyHOME 2.0.0b17, OWNd 2.0.0b13
- **Thermoregulation Plant**: 41 climate zones (`6`–`9`, `14`–`17`, `21`, `26`–`29`, `34`–`37`, `42`–`51`, `57`–`70`), of which 24 zones carry restored or live `_humidity_capable = True` (`42`–`51`, `57`–`70` except `46`..`70` subset)

## What the Trace Establishes

1. **Startup queue burst (`plant.unpatched_startup_who4_queue_burst`)**:
    - At `15:07:16.323`–`15:07:16.338` (15 ms), discovery completion enqueued 66 WHO 4 status queries back-to-back: 41 per-zone `*#4*Z##` polls + 24 per-zone `*#4*Z*60##` humidity polls, followed at the very tail by `*#4*#0*20##` (`ALL_ZONE_REQUESTS` actuator sweep), which starved for 60 s (`PACED_WRITE_TIMEOUT`).
2. **Physical SCS bus round-trip vs gateway cache myth**:
    - Each `*#4*Z##` status query forwards onto the physical 2-wire SCS bus (`$0499013000` in the `MyHomeServer1` `028206` firmware oracle) and elicits a 5-frame burst (`*#4*Z*0*...##`, `*#4*Z*12*...##`, `*4*1*Z##`, `*#4*Z*14*...##`, `*#4*Z*13*...##`) taking ~1.3–1.9 s per zone while thermostats simultaneously emit autonomous `*#4*Z*0*...##`, `*#4*Z*7*1*1*...##`, `*#4*Z*19*...##`, and `*#4*Z*60*...##` frames.
3. **Autonomous Dimension 7 broadcasts (`*#4*Z*7*1*1*TTTT##`)**:
    - Zones continuously report live mode + setpoint via Dimension 7 (`*#4*6*7*1*1*0100##`, `*#4*7*7*1*1*0100##`, etc.) paired with Dimension 0 (`*#4*7*0*0141##`) before post-discovery polling begins.
4. **Dual-worker `_TIER_STATUS` saturation & setpoint starvation**:
    - With both `MyHomeServer1` command workers simultaneously executing slow `_TIER_STATUS` reads, user setpoint writes (`*#4*59*#14*0105*3##`, `*#4*59*#14*0100*3##`) waited 6.2–7.7 s behind in-flight status timeouts and then entered an already-backed-up gateway SCS transmit FIFO, timing out at 10.0 s.
5. **Cross-session WHO 4 FIFO reply bleed (`fifo_bleed_batches`, 11 batches)**:
    - After timed-out command sessions closed and re-authenticated, delayed 5-frame replies from earlier queries flushed onto the newly opened TCP command socket: e.g. `*#4*60##` collected Zone 34's 5 frames (`*#4*34*0*0146##` .. `*#4*34*13*00##`), `*#4*60*60##` collected Zone 28's 5 frames, and `*#4*51##` collected Zone 21's 5 frames split `3 + 2` across two consecutive transactions.
6. **Stray `*#*1##` ACK frames on the event session**:
    - Under SCS FIFO saturation (`15:11:09`–`15:12:12`), `MyHomeServer1` leaked 10 bare `*#*1##` command-session ACKs onto the `*99*1##` event socket.
