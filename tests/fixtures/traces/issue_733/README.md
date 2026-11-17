# Live Capture: Dimension 1 capability probe on a BTicino MH200 (Firmware 2.1.0), 2026-10-20

Evidence for the post-sweep dimmer probe of [#733](https://github.com/OpenWebNet-HA/MyHOME/issues/733) / [PR #742](https://github.com/OpenWebNet-HA/MyHOME/pull/742): what an on/off relay and a dimmer answer to `*#1*WHERE*1##`.

## File: `myhome_trace_MH200_dim1_probe_2026-10-20T19-22-47.json`

Event-session capture (recorded with the `myhome-gateway` MCP, which does not log command-session ACK/NACK):

1. A general light status request `*#1*0##`.
2. Dimension 1 requests to three relays that were ON (`81`, `84`, `57`), one relay that was OFF (`11`) and two dimmers that were ON (`29`, `67`, BTicino F418U2 channels).

Anonymised: the file records no IP address, MAC address or device names, and the gateway clock frames (`*#13**22*…##`, which carry the plant's local time and UTC offset) were removed.

## Findings

| Request | Device | Bus answer |
|---|---|---|
| `*#1*81*1##` | relay, ON | `*1*1*81##` |
| `*#1*84*1##` | relay, ON | `*1*1*84##` |
| `*#1*57*1##` | relay, ON | `*1*1*57##` |
| `*#1*11*1##` | relay, OFF | `*1*0*11##` |
| `*#1*29*1##` | dimmer, ON | `*1*1*29##`, `*#1*29*1*200*2##`, `*1*1*29##` |
| `*#1*67*1##` | dimmer, ON | `*1*1*67##`, `*#1*67*1*174*2##`, `*1*1*67##` |

- A relay answers a Dimension 1 request with its plain `WHAT` status and no level, so the probe never promotes a relay to a dimmer. It does not NACK on the bus, and it does not stay silent.
- A dimmer answers with `*#1*WHERE*1*LEVEL*SPEED##`, which promotes the light to `ColorMode.BRIGHTNESS`.
- In the general sweep, the ON dimmers `29` and `67` first answered with a plain `*1*1*W##` and only later in the same sweep with a level frame (`*1*10*29##`, `*1*9*67##`). The first frame alone cannot tell a dimmer from a relay.
