# Firmware Oracle Trace: BTicino H4684 and Legrand LGRH4684 Gateway Identification

Trace fixture capturing OpenWebNet WHO 13 (Gateway Management) and WHO 1013 (Gateway Diagnostic) device identification sequences for the BTicino H4684 / Legrand LGRH4684 3.5" Colour Touch Screen family.

## Provenance: Live QEMU Emulation (`own-firmware-oracle` PR #34) & `bt_device` Disassembly (PR #26)

> [!NOTE]
> **Empirical Firmware Provenance**:
> Unofficial third-party documentation (such as legacy Nmap scripts and `MHCatalogue.db` extractions) previously recorded conflicting codes, conflating WHO 13 dimension 15 codes (`13`, `20`, `23`) with WHO 1013 dimension 1 diagnostic object model codes (`19`, `26`, `29`).
>
> This trace is grounded in:
> 1. **Live QEMU Firmware Execution (`own-firmware-oracle` PR #34, `results/H4684/020054/oracle/full/trace-corpus.tsv`)**:
>    Full multi-daemon emulation of H4684 firmware 2.0.54 (`bt_processi`, `openserver`, `scsserver`, and `bt_device` on port 40013) empirically emitted:
>    - `*#13**15##` &rarr; `*#13**15*13##` (`ACK`, `OUT` — matches official BTicino 2006 specification verbatim)
>    - `*#13**16##` &rarr; `*#13**16*2*0*54##` (`ACK`, `OUT` — firmware version `2.0.54`)
>    - `*#13**23##` &rarr; `*#13**23*2*0*5##` (`ACK`, `OUT` — Linux kernel version `2.0.5`)
>    - `*#13**24##` &rarr; `*#13**24*3*0*0##` (`ACK`, `OUT` — distribution version `3.0.0`)
>    - `*#1013*0*1##` &rarr; `*#1013**1*19##` (`ACK`, `OUT` — single-parameter object model `19` for `H4684`)
> 2. **Static Binary Reverse Engineering (`own-firmware-oracle` PR #26)**:
>    Disassembly of the `bt_device` binary from H4684 firmware 2.0.54 (`TiDisplayColorIP` build `0x02003194`):
>    - WHO 1013 Dimension 1 dispatch entries: `19` (`H4684` / `L4684`) and `26` (`LGRH4684`), plus 4-parameter form (`*15*5*0`).
>    - WHO 13 Dimension 15 dispatch entries: `13` (`H4684`) and `20` (`LGRH4684`).
> 3. **`OWNd` Protocol Diagnostic Mapping (PR #96)** & **`MyHOME` Gateway Identity Resolver**:
>    - `WHO1013_OBJECT_MODELS` mapping `"19": ("H4684", "L4684")` and `"26": ("LGRH4684",)`, binding to `H4684Profile`.

## Plant Profile

| Property | Value |
|---|---|
| Gateway Model | BTicino H4684 3.5" Colour Touch Screen (firmware 2.0.54, kernel 2.0.5, distribution 3.0.0, MAC prefix `00:03:50`) |
| Alternate Model | Legrand LGRH4684 3.5" Colour Touch Screen |
| Diagnostic Systems | WHO 13 (Gateway Management) & WHO 1013 (Gateway Diagnostic) |
| Resolved Profile | `H4684Profile` |

## Captured Sequence (13 Frames)

1. **H4684 Device Type Request & Response**:
   - `*#13**15##` &rarr; `*#13**15*13##`: Gateway reports device type code `13` (BTicino 2006 standard table).
2. **H4684 Firmware Version Request & Response**:
   - `*#13**16##` &rarr; `*#13**16*2*0*54##`: Gateway reports firmware version `2.0.54`.
3. **H4684 Kernel Version Request & Response**:
   - `*#13**23##` &rarr; `*#13**23*2*0*5##`: Gateway reports kernel version `2.0.5`.
4. **H4684 Distribution Version Request & Response**:
   - `*#13**24##` &rarr; `*#13**24*3*0*0##`: Gateway reports distribution version `3.0.0`.
5. **H4684 Diagnostic Object Model Request & Responses**:
   - `*#1013*0*1##` &rarr; `*#1013**1*19##` (empirical QEMU 1-value reply) and `*#1013**1*19*15*5*0##` (4-value form with `N_CONF` 15, `BRAND` 5, `LINE` 0).
6. **LGRH4684 Device Type & Diagnostic Object Model Responses**:
   - `*#13**15*20##` and `*#1013**1*26*15*5*0##`: Legrand variant reporting WHO 13 code `20` and WHO 1013 object model `26`.

## Replay Verification

Replayed by `tests/test_h4684_gateway_trace.py` and validated against `tests/golden/firmware_oracle.json` in `tests/test_trace_oracle_validation.py`.
