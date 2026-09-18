# Gateway Identification

How MyHOME decides **which gateway model you have**, why that matters, what evidence it uses, and how to check the result in your own traces.

> **TL;DR** — The model label comes from the gateway's own UPnP/SSDP announcement or from your choice in the config flow. The in-band `WHO=13` "model request" reply can only *confirm* or *question* that label, because its official code table dates from 2006 and does not know any gateway sold since. Every diagnostics download and bus-monitor export carries an `identification` block that shows exactly how the label was established.

---

## Why the label matters

The model name is not cosmetic. It selects the **gateway profile** in the OWNd protocol engine, which drives:

| Profile setting | Example: MH200 / MH200N | Example: MyHOMEServer1 |
| :--- | :---: | :---: |
| concurrent command sessions | 1 | 4 (2 by default) |
| pacing between commands | 150 ms | 20 ms |
| command queue size | 100 | 300 |
| subsystems queried at startup | lighting, automation, heating, CEN, scenarios | all, incl. audio and energy |
| HMAC (SHA) authentication | no | yes |

A gateway labelled as a faster model than it is gets flooded; one labelled as a slower model is throttled for nothing. The label also lands in the entry title, the device registry, diagnostics and every trace attached to a bug report — so a wrong label misleads the people trying to help you.

---

## The three sources of a model label

| `source` | Where it comes from | Trust |
| :--- | :--- | :--- |
| `ssdp` | The gateway announced its own `modelName` over UPnP/SSDP when it was discovered (F454, F455, MH200N, MH202, MyHomeServer1 …). | **Authoritative** — the device said so itself. |
| `serial` | Serial / USB interface (Legrand 3578): the model is fixed by the transport. | **Authoritative** |
| `manual` | You typed the address and picked the model in the config flow. | Trusted, but *correctable* by certain evidence — early versions of the manual flow defaulted to F454, which produced mislabelled entries. |
| `who13` | No model was configured; the entry was labelled from the WHO=13 reply (see below). | Best effort. |

---

## What the bus can tell us: WHO=13 dimension 15

Gateways answer the *model request* `*#13**15##` with `*#13**15*<code>##`, and most broadcast it periodically on the monitor session. The **only official meaning of `<code>`** is BTicino's *OpenWebNet_Community_2_device* v1.0.0 (13 June 2006), section 1.2.6 — and that table is complete at six entries:

| code | model | era |
| :---: | :--- | :--- |
| `2` | MHServer | 2005 |
| `4` | MH200 | 2006 |
| `6` | F452 | 2006 |
| `7` | F452V | 2006 |
| `11` | MHServer2 | 2006 |
| `13` | H4684 | 2006 |

That is the whole list. **F454, F455, MH200N, MH201, MH202, MyHOMEServer1, F461 … are not in it.** Newer gateways reuse an old code or invent one, so the reply can *corroborate* a label but can never *establish* one for a modern gateway. For example an **MH200N reports `4`** — the code of its 2006 predecessor — which is consistent, not a contradiction.

### Codes seen in the field (evidence, not specification)

| code | seen on | evidence |
| :---: | :--- | :--- |
| `200` | MyHOMEServer1 / F454 | Diagnostics in [#297](https://github.com/OpenWebNet-HA/MyHOME/issues/297) from an owner who identified the hardware in [#292](https://github.com/OpenWebNet-HA/MyHOME/issues/292); also reported by F454 installations (#370). Corroborates modern gateways. |

If your gateway reports a code that is in neither table, the integration logs it, keeps your configured model, and surfaces an `unknown_gateway_model` repair issue asking for a diagnostic trace.

---

## The rule the integration applies

When a dimension-15 reply arrives, the handler compares the reported model with the configured one **by family** (`MH200N` → `MH200`, `F452V` → `F452`; a variant suffix is never downgraded) and then:

| configured `source` | code agrees (same family) | code contradicts — **official** 2006 code | code contradicts — **observed-only** code | code unknown |
| :--- | :--- | :--- | :--- | :--- |
| `ssdp` / `serial` | nothing | model kept; repair issue **asks** you to confirm | model kept; repair issue asks | recorded; repair issue created |
| `manual` | nothing | model, profile and device registry **corrected**; repair issue tells you | model kept; repair issue asks | recorded; repair issue created |
| none / `who13` | — | labelled from the code | labelled from the code | recorded; repair issue created |

Two repair issues exist for this:

- **Gateway model mismatch for …** (`gateway_identity_mismatch`) — the reported and configured models disagree and the integration is *not* sure enough to act. It names the code, what the specification or field evidence says it means, and how to fix it (reconfigure flow) if the reported model is what you own.
- **Gateway model corrected to …** (`gateway_identity_corrected`) — a manually chosen model was contradicted by an official 2006 code and was corrected; reconfigure if that is wrong.

Neither issue is raised twice for the same finding, and a mismatch issue is withdrawn automatically if the gateway later reports a code that no longer contradicts.

---

## Check it yourself

Every diagnostics download (*Settings → Devices & services → MyHOME → ⋮ → Download diagnostics*) and every bus-monitor export (Export Trace / Export Sweep) carries:

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

Reading it:

- `source` — which of the three sources produced the label.
- `who13_code` — the raw code your gateway reported; `who13_model_official` / `who13_model_observed` — what the 2006 specification and the field evidence say it means (either may be `null`).
- `who13_firmware`, `who13_kernel`, `who13_distribution` — dimensions 16 / 23 / 24, corroborating evidence when the gateway sends them.
- `profile` — the OWNd profile actually in use (MH200 and MH200N share `MH200NProfile`; that is expected).
- `conflict` — non-null exactly when a *Gateway model mismatch* repair issue is open, with the reason.

If `source` is `manual` and `who13_code` is not in either table, you are the first to see that code: please open an issue with the export attached.

---

## What to do if your gateway is mislabelled

1. Open the entry's **⋮ → Reconfigure** and set the model you own. The reconfigure flow keeps your entities.
2. If you know your model but the integration keeps questioning it, the WHO=13 code your gateway sends is new evidence — attach the export to an issue so the table above can grow.
3. Never edit `custom_components/myhome/const.py` on your installation to "fix" a code: the precedence rule above is what protects you from the next mislabel.
