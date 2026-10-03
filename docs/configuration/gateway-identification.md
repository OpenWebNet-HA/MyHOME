# Gateway Identification

How MyHOME decides **which gateway model you have**, why that matters, what evidence it uses, and how to check the result in your own traces.

> **TL;DR** — The model label comes from the gateway's own UPnP/SSDP announcement or from your choice in the config flow. The in-band `WHO=13` "model request" reply can only *confirm* or *question* that label, because its official code table dates from 2006 and does not know any gateway sold since. When that reply is a code shared by several modern gateways (`200`), the integration asks a second question, `WHO=1013` dimension 1 (OBJECT_MODEL), whose catalogue is one code per model, and lets that answer settle it. Every diagnostics download and bus-monitor export carries an `identification` block that shows exactly how the label was established.

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

That is the whole list. **F454, F455, MH200N, MH201, MH202, MyHOMEServer1, F461 … are not in it.** Newer gateways reuse an old code or invent one, so the reply can *corroborate* a label but can never *establish* one for a modern gateway. The **MH200N is the exception that has a code of its own**: `44` in the `WHO=1013` catalogue and in Nmap's `WHO=13` table (below). A gateway answering the 2006 code `4` is therefore an MH200, not an MH200N: a physical MH200 (firmware 2.1.0) answers `*#13**15*4##`, and until 2026-09-23 a manual MH200N label on one was wrongly accepted as the same family.

### Codes seen in the field (evidence, not specification)

| code | seen on | evidence |
| :---: | :--- | :--- |
| `200` | F454 / MyHOMEServer1 / MH202 / F461 | Confirmed on physical hardware in [#420](https://github.com/OpenWebNet-HA/MyHOME/pull/420) for the F454 (firmware 2.0.51), the MH202 (1.0.21) and the MyHomeServer1 (2.87.13), each with its `WHO=1013` reply; earlier for the MyHomeServer1 in [#297](https://github.com/OpenWebNet-HA/MyHOME/issues/297) / [#292](https://github.com/OpenWebNet-HA/MyHOME/issues/292) and the F454 in [#370](https://github.com/OpenWebNet-HA/MyHOME/issues/370). F461 reported in #370 without diagnostics. **Shared** by every modern Linux-based gateway, so it identifies none of them — it is the cue for the WHO=1013 question below. |

### Codes from an independent implementation

Nmap's [`openwebnet-discovery.nse`](https://github.com/nmap/nmap/blob/master/scripts/openwebnet-discovery.nse) has carried a `device` table for this very dimension (`device_dimension["Device Type"] = "15"`) since the script was first committed on **2017-07-18** — years before this project. It repeats the six official codes and adds seven more, which the integration now recognises:

| code | model | note |
| :---: | :--- | :--- |
| `12` | F453AV | |
| `15` | F427 | Nmap writes "F427 (Gateway Open-KNX)" |
| `16` | F453 | WHO=1013 calls the same model `42` |
| `23` | H4684 | a second code for the model the 2006 table gives as `13` |
| `27` | L4686SDK | |
| `44` | MH200N | |
| `51` | F454 | see below |

Nobody here has seen these on a bus, so they rank below field evidence: they can **label** a gateway that has no model and **corroborate** one that has, but they never contradict your configuration.

`51` is the interesting one. Nmap listed it as `F454` in 2017 and, separately, the OpenWebNet device database quoted in [#420](https://github.com/OpenWebNet-HA/MyHOME/pull/420) says the same — two unrelated sources, neither of them a capture. Nmap also labels `200` as *"F454 (new?)"*, which suggests `200` was the newcomer at the time.

> **A theory, not a finding.** The F454 may straddle two schemes: early/1.x firmware answering the concrete `51`, later/2.x firmware answering the generic `200` and leaving the specific identity to `WHO=1013` OBJECT_MODEL `51`. The traced F454 in #420 runs firmware 2.0.51 and does answer `200` then `51`. But no 1.x unit has ever been captured — a 2.x F454 cannot be downgraded to check — so nothing in the code depends on this being true.

If your gateway reports a code that is in none of these tables, the integration logs it, keeps your configured model, and surfaces an `unknown_gateway_model` repair issue asking for a diagnostic trace.

### Step 2 — `WHO=1013` dimension 1 (OBJECT_MODEL) settles a shared code

A shared code such as `200` cannot label an unconfigured gateway, and it cannot check an SSDP or manual label either. So whenever `WHO=13` answers a shared code — **whatever the source of the configured model** — the integration sends one status request on the command session:

```text
*#1013*0*1##            → *#1013**1*<OBJECT_MODEL>##
```

`WHO=1013` is the *Gateway Diagnostic* family (Nmap's table of WHO values calls it *Device Diagnostic*); dimension 1 is the model code, and its catalogue has one code per model (`4` MH200, `5` MH202, `44` MH200N, `51` F454, `67` MyHOMEServer1, `134` F461, … — the full list is `WHO1013_OBJECT_MODELS` in `const.py`, taken from the OpenWebNet device database as listed in #370). A real reply is four values, not one — `OBJECT_MODEL`, `N_CONF`, `BRAND`, `LINE` (per the OpenWebNet Encyclopedia's work on `MHCatalogue.db`, via @anotherjulien in [#420](https://github.com/OpenWebNet-HA/MyHOME/pull/420)):

```text
*#1013**1*67*15*5*0##   ← MyHomeServer1 (firmware 2.87.13)
*#1013**1*51*15*5*0##   ← F454 (firmware 2.0.51)
*#1013**1*5*15*5*0##    ← MH202 (firmware 1.0.21)
         │   │  │ └── LINE  = 0, "Undefined"
         │   │  └──── BRAND = 5, "Legrand BTicino"
         │   └─────── N_CONF = 15
         └─────────── OBJECT_MODEL
```

Every gateway traced so far answers the same `*15*5*0`. `N_CONF` 15 lies outside the ordinary `0..12` physical-configurator range and looks like the `0xF` sentinel, so its gateway-specific meaning stays unresolved. Only `OBJECT_MODEL` decides the identity; `N_CONF`, `BRAND` and `LINE` are recorded and travel in diagnostics. Note that the same product does not necessarily answer matching codes in the two families: an F454 is `51` here but `200` (or `51` on 1.x firmware) on `WHO=13`. The two tables are therefore kept apart, and the `WHO=1013` one is consulted only after a shared `WHO=13` code.

#### One product, two brand names

Some catalogue entries carry more than one name — `51` is `F454` *and* `003598`. These are **not** order codes or sub-models: BTicino sold the device as F454, Legrand sold the same hardware as 003598, and nowadays Legrand mostly uses the BTicino names ([@anotherjulien](https://github.com/OpenWebNet-HA/MyHOME/pull/420)). The integration therefore:

- always **displays** the BTicino name, and never treats the other as a separate model;
- still **accepts** the other name, so a gateway announcing `003598` over SSDP is corroborated by OBJECT_MODEL `51` rather than flagged as a mismatch;
- lists the alternatives in diagnostics as `who1013_other_names`, so an owner whose box says `003598` can see why their device page says `F454`.

In principle the `BRAND` field could choose which name to show. It cannot today: the only value ever traced is `5`, *"Legrand BTicino"*, which covers both houses.

Two safeguards keep this off legacy hardware and out of your logs:

- **A legacy gateway never gets the question.** An MH200 answers `4` on `WHO=13`, which is unambiguous, so the `WHO=1013` request is never queued (verified on a physical MH200; there is a regression test for it). Only gateways that answer a shared code are asked, and those are all modern Linux gateways that implement `WHO=1013`.
- **A gateway that does not answer costs nothing visible.** The request goes out as a *status request*: if the gateway NACKs it, OWNd retries once and logs both attempts at DEBUG. One request is sent per answer: while it is unanswered the periodic re-broadcast of the shared code does not repeat it, and a reconnect of the event session is what gives it another try.

What the answer does depends on the configured source, exactly mirroring the `WHO=13` rule below: it **labels** an unconfigured gateway, **corrects** a manual choice (with a *Gateway model corrected* issue), and **cross-checks** an SSDP or serial identity — which is never overruled, but a *Gateway model mismatch* issue asks you to confirm when the two disagree. Because `WHO=1013` outranks `WHO=13`, the periodic re-broadcast of the shared code that started the check cannot undo its verdict; only a later `WHO=1013` reply changes it. An OBJECT_MODEL outside the catalogue raises the same `unknown_gateway_model` issue as an unknown `WHO=13` code, with the code written as `1013-1-<value>`.

### How the verdict is reached

Each source is recorded separately — what you picked, what the gateway announced, the `WHO=13` code, the `WHO=1013` code — and one pure function (`resolve_gateway_identity` in `identity.py`) turns all of it into the effective model plus its corroboration or conflict state. The handlers only answer *"what did I observe?"*; a single resolver answers *"what should the integration believe?"*. Because the same evidence always yields the same verdict, a repeated broadcast can neither repeat a correction nor flap a repair issue.

> **Hardware version is deliberately not requested.** Nmap's table calls `WHO=13` dimension 17 "Hardware Version", and it is documented as such for the **Zigbee** part of OpenWebNet, with the same three-digit `V.r.b` shape as the firmware version. It has never been canonically described for non-Zigbee OpenWebNet ([@anotherjulien](https://github.com/OpenWebNet-HA/MyHOME/pull/420)) — it is probably the same thing, but "probably" is not evidence, so the integration neither asks for it nor fills the device page's `hw_version` from it.

> **The two families are separate number spaces.** They are not two spellings of the same identifier, and the same model carries different numbers in each: an F453 is `42` for `WHO=1013` but `16` for Nmap's `WHO=13` table, and an H4684 is `29` against `13` (2006) or `23` (Nmap). Several values *do* coincide — `4`, `12`, `44`, `51` — which is precisely why the tables are kept apart instead of merged on the ones that agree; a test pins both the overlaps and the clashes.

> **Status of the evidence.** Four gateways are verified on real hardware, each with a fixture the test suite replays: the **MH200** answers `4` and is never asked (`mh200_physical_plant`), and the **F454**, **MH202** and **MyHomeServer1** answer the shared `200` and then `51`, `5` and `67` (`pr_420_f454`, `pr_420_mh202`, `pr_420_myhomeserver1`, contributed in #420). Still untraced: the **F461** (reported to answer `200`), the claim that a 1.x **F454** answers `51` on `WHO=13`, and every other code in the catalogue. If you own one of those, the `who1013_code` field in your diagnostics download (below) is the evidence we are missing — please attach it to an issue.

---

## The rule the integration applies

When a dimension-15 reply arrives, the handler compares the reported model with the configured one. A configured model that one of the tables lists by name has a code of its own, so only that name or a brand variant of it agrees: `MH200N` (`44`) is contradicted by `4` (MH200), and `F452V` (`7`) by `6` (F452), even though each pair shares a family. A name no table lists is compared **by family** (`F452X` → `F452`), so an unlisted variant suffix is never downgraded. Then:

| configured `source` | code agrees | code contradicts — **official** 2006 code | code contradicts — **uncertain** code (field evidence or third-party) | code **shared** (`200`) | code unknown |
| :--- | :--- | :--- | :--- | :--- | :--- |
| `ssdp` / `serial` | nothing | model kept; repair issue **asks** you to confirm | model kept, no repair issue | model kept; `WHO=1013` asked and its answer cross-checks (mismatch → repair issue asks) | recorded; repair issue created |
| `manual` | nothing | model, profile and device registry **corrected**; repair issue tells you | model kept, no repair issue | model kept; `WHO=1013` asked and its answer corrects if it differs | recorded; repair issue created |
| none / `who13` | — | labelled from the code | labelled from the code | `WHO=1013` asked and its answer labels | recorded; repair issue created |

"Contradicts — uncertain code" covers both field evidence and the third-party table: neither can prove your gateway is something else, so the configured model stands untouched.

Two repair issues exist for this:

- **Gateway model mismatch for …** (`gateway_identity_mismatch`) — the reported and configured models disagree and the integration is *not* sure enough to act. It names the code, what the specification or field evidence says it means, and how to fix it (reconfigure flow) if the reported model is what you own.
- **Gateway model corrected to …** (`gateway_identity_corrected`) — a manually chosen model was contradicted by an official 2006 code or by a `WHO=1013` OBJECT_MODEL and was corrected; reconfigure if that is wrong.

Neither issue is raised twice for the same finding, and a mismatch issue is withdrawn automatically if the gateway later reports a code that no longer contradicts.

---

## What the device page shows

*Settings → Devices & services → MyHOME → the gateway device:*

| field | where it comes from |
| :--- | :--- |
| **Model** | the resolved identity — SSDP or your choice, corrected or labelled by `WHO=13` / `WHO=1013` as described above. Always the BTicino name. |
| **Model ID** | the `OBJECT_MODEL` number from `WHO=1013` (`51` for an F454). The only model identifier the gateway ever states about itself; absent until the gateway answers, which only happens when `WHO=13` returned a shared code. |
| **Firmware** | `WHO=13` dimension 16. |
| **Serial number** | the gateway's serial as reported by the connection (the MAC for TCP gateways). |
| **Manufacturer** | the entry's manufacturer, normally `BTicino S.p.A.`. Not taken from the `BRAND` field, which does not distinguish the two houses. |

`N_CONF`, `BRAND`, `LINE` and the alternative brand name are **not** on the device page — none of them identifies the hardware, and brand only repeats the manufacturer. They are in the diagnostics download instead.

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
  "who1013_code": null,
  "who1013_model": null,
  "who1013_other_names": [],
  "who1013_n_conf": null,
  "who1013_brand": null,
  "who1013_line": null,
  "profile": "MH200NProfile",
  "conflict": null
}
```

Reading it:

- `source` — which of the three sources produced the label.
- `who13_code` — the raw code your gateway reported; `who13_model_official` / `who13_model_observed` — what the 2006 specification and the field evidence say it means (either may be `null`).
- `who13_firmware`, `who13_kernel`, `who13_distribution` — dimensions 16 / 23 / 24, corroborating evidence when the gateway sends them.
- `who1013_code` / `who1013_model` — the `WHO=1013` OBJECT_MODEL reply and what the catalogue says it means; both `null` unless `who13_code` was a shared code (the question is not asked otherwise) and the gateway answered. On an MH200 they are always `null`.
- `who1013_other_names` — the same product's other brand name(s), e.g. `["003598"]` for an F454.
- `who1013_n_conf`, `who1013_brand`, `who1013_line` — the rest of the reply, rendered as `value (meaning)` when the value has been seen before and as the bare value when it has not, so a new one reaches the trace instead of being dropped. A traced gateway shows `"5 (Legrand BTicino)"` and `"0 (Undefined)"`.
- `profile` — the OWNd profile actually in use (MH200 and MH200N share `MH200NProfile`; that is expected).
- `conflict` — non-null exactly when a *Gateway model mismatch* repair issue is open, with the reason.

If `source` is `manual` and `who13_code` is not in either table, you are the first to see that code: please open an issue with the export attached.

---

## What to do if your gateway is mislabelled

1. Open the entry's **⋮ → Reconfigure** and set the model you own. The reconfigure flow keeps your entities.
2. If you know your model but the integration keeps questioning it, the WHO=13 code your gateway sends is new evidence — attach the export to an issue so the table above can grow.
3. Never edit `custom_components/myhome/const.py` on your installation to "fix" a code: the precedence rule above is what protects you from the next mislabel.
