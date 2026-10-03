# Gateway Identification

> [!NOTE]
> How MyHOME decides **which gateway model you have**, why that matters, what evidence it uses, and how to check the result in your traces.

**Summary**: The model label comes from the gateway's own UPnP/SSDP announcement or from your choice during configuration. The in-band `WHO=13` "model request" reply can only *confirm* or *question* that label, because its official code table dates from 2006 and does not know any gateway sold since. Every diagnostics download carries an `identification` block that shows exactly how the label was established.

---

## Why the Label Matters

The model name selects the **gateway profile** in the protocol engine, which drives:

| Profile setting | Example: MH200 / MH200N | Example: MyHOMEServer1 |
| :--- | :---: | :---: |
| Concurrent command sessions | 1 | 4 (2 by default) |
| Pacing between commands | 150 ms | 20 ms |
| Command queue size | 100 | 300 |
| Subsystems queried at startup | Lighting, automation, heating, CEN | All, incl. audio and energy |
| HMAC (SHA) authentication | No | Yes |

A gateway labelled as a faster model than it is gets flooded; one labelled as a slower model is throttled unnecessarily.

---

## Sources of a Model Label

| `source` | Where it comes from | Trust |
| :--- | :--- | :--- |
| `ssdp` | The gateway announced its own `modelName` over UPnP/SSDP when discovered (F454, F455, MH200N, MH202, MyHomeServer1 …). | **Authoritative** — the device reported it directly. |
| `serial` | Serial / USB interface (Legrand 3578): the model is fixed by transport. | **Authoritative** |
| `manual` | Entered manually in configuration. | Trusted, but *correctable* by certain evidence. |
| `who13` | No model was configured; labelled from WHO=13 reply. | Best effort. |

---

## What the Bus Can Tell Us: WHO=13 Dimension 15

Gateways answer the *model request* `*#13**15##` with `*#13**15*<code>##`, and most broadcast it periodically on the monitor session. The **only official meaning of `<code>`** is BTicino's *OpenWebNet_Community_2_device* v1.0.0 (13 June 2006), section 1.2.6:

| Code | Model | Era |
| :---: | :--- | :--- |
| `2` | MHServer | 2005 |
| `4` | MH200 | 2006 |
| `6` | F452 | 2006 |
| `7` | F452V | 2006 |
| `11` | MHServer2 | 2006 |
| `13` | H4684 | 2006 |

**F454, F455, MH200N, MH201, MH202, MyHOMEServer1, F461 … are not in it.** Newer gateways reuse an old code or invent one, so the reply can *corroborate* a label but can never *establish* one for a modern gateway. For example an **MH200N reports `4`** — the code of its 2006 predecessor — which is consistent.

### Codes Seen in the Field

| Code | Observed on | Evidence |
| :---: | :--- | :--- |
| `200` | MyHOMEServer1 | Diagnostics in issue [#297](https://github.com/OpenWebNet-HA/MyHOME/issues/297). |

---

## The Rule Applied by the Integration

When a dimension-15 reply arrives, the handler compares the reported model with the configured one **by family** (`MH200N` → `MH200`, `F452V` → `F452`):

| Configured `source` | Code agrees | Official 2006 code contradicts | Observed-only code contradicts | Unknown code |
| :--- | :--- | :--- | :--- | :--- |
| `ssdp` / `serial` | Nothing | Model kept; repair issue asks to confirm | Model kept; repair issue asks | Recorded only |
| `manual` | Nothing | Model and profile corrected | Model kept; repair issue asks | Recorded only |
| None / `who13` | — | Labelled from code | Labelled from code | Recorded only |

---

## Diagnostics Block

Every diagnostics download carries an `identification` dictionary:

```json
"identification": {
  "model": "MH200",
  "source": "manual",
  "configured_model": "MH200",
  "ssdp_model": null,
  "who13_code": "4",
  "who13_model": "MH200",
  "who13_model_official": "MH200",
  "who13_model_observed": null,
  "who13_firmware": null,
  "who13_kernel": null,
  "who13_distribution": null,
  "profile": "MH200NProfile",
  "conflict": null
}
```

- **See Also**: [OpenWebNet Protocol & WHO Specifications](../protocol/who-specifications.md)
