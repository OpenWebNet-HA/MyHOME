# #624 Classic CEN (WHO 15) 4-digit address on a shared MH200N + MyHomeServer1 bus

Authentic on-wire bus traces contributed by **@wave68runner** on
[#624 (comment 6002817949)](https://github.com/OpenWebNet-HA/MyHOME/issues/624#issuecomment-6002817949).
Both gateways sit on the same SCS bus, each capturing physical button interactions with unit `0512` ($A=5, PL=12$).

## Contributed Files

| File | Gateway | Frames | Description |
|---|---|---|---|
| `myhome_trace_MH200N_all_2026-10-05T20-50-47.json` | MH200N (fw 1.0, SSDP) | 9 | CEN press, short release, long press, hold repetitions, and long release on unit `0512`, plus lighting and clock frames. |
| `myhome_trace_MyHomeServer1_all_2026-10-05T20-50-46.json` | MyHomeServer1 | 40 | The exact same CEN sequences on unit `0512`, surrounded by the `*#13**#0` / `*#13**#1` clock frames this gateway emits every second. |

## Empirical Protocol Discoveries

- On physical SCS plants with $PL \ge 10$, OpenWebNet CEN (`WHO = 15`) uses 4-digit point-to-point addressing with a leading zero for Area (`0512` represents $A=5, PL=12$).
- Both MH200N and MyHomeServer1 emit identical wire WHERE addresses (`0512`).
- A short press sequence on button 3 is `*15*03*0512##` (pressed) followed by `*15*03#1*0512##` (release after short press).
- A long press sequence on button 3 is `*15*03*0512##` (pressed), `*15*03#3*0512##` (extended pressure), repeated `*15*03#3*0512##`, followed by `*15*03#2*0512##` (release after long press).
- Settles the golden sample trace testing for 4-digit CEN addressing and ensures seamless tolerance across both integer `512` and wire string `"0512"` in Home Assistant event payloads, device triggers, and device registry identifiers.
