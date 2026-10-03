# #541 Classic CEN (WHO 15) short press on a shared MH200N + MyHomeServer1 bus

Authentic on-wire bus traces contributed by **@wave68runner** on
[#541 (comment 5894041457)](https://github.com/OpenWebNet-HA/MyHOME/issues/541#issuecomment-5894041457).
Both gateways sit on the same SCS bus, so each captured the same physical button press.

## Contributed Files

| File | Gateway | Frames | Description |
|---|---|---|---|
| `myhome_trace_MH200N_cen_short_press_2026-09-29T16-03-43.json` | MH200N (fw 1.0, SSDP) | 3 | Classic CEN press + release-after-short-press, plus one gateway clock frame. |
| `myhome_trace_MyHomeServer1_cen_short_press_2026-09-29T16-03-55.json` | MyHomeServer1 | 56 | The same press + release, surrounded by the `*#13**#0` / `*#13**#1` clock frames this gateway emits every second. |

## Empirical Protocol Discoveries

- One short press of CEN button 1 on object 73 is `*15*01*73##` (pressed) followed by `*15*01#1*73##`
  (released after short press). The two-digit `01` WHAT is parsed as pushbutton 1.
- Fills the **MyHomeServer1 / WHO 15** cell of the trace matrix.
