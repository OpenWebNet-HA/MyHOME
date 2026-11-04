# Hardware Trace Availability Matrix

This matrix documents the real-world physical gateways for which we have captured bus traces and diagnostic bundles in our `tests/fixtures` corpus. It is an honest representation of the hardware configurations that we actively test against to validate our protocol implementation.

<!-- TRACE_MATRIX_START -->
| Gateway Model | WHO 0<br>Scenario | WHO 1<br>Lights | WHO 2<br>Autom. | WHO 3<br>Load | WHO 4<br>Climate | WHO 5<br>Alarm | WHO 9<br>Power | WHO 13<br>Gateway | WHO 14<br>Lock | WHO 15<br>CEN | WHO 16<br>Audio | WHO 17<br>Scenario | WHO 18<br>Energy | WHO 22<br>Audio Diff. | WHO 25<br>Diag | WHO 1001<br>Diag | WHO 1004<br>Diag | WHO 1013<br>Diag | WHO 1022<br>Diag |
| :--- |  :---:  |  :---:  |  :---:  |  :---:  |  :---:  |  :---:  |  :---:  |  :---:  |  :---:  |  :---:  |  :---:  |  :---:  |  :---:  |  :---:  |  :---:  |  :---:  |  :---:  |  :---:  |  :---:  |
| **067292** |  | ✅ |  |  | ✅ |  |  | ✅ |  |  |  |  |  |  |  |  |  |  |  |
| **F453AV** |  | ✅ | ✅ |  | ✅ | ✅ |  | ✅ |  |  | ✅ |  | ✅ |  |  | ✅ |  |  |  |
| **F454** | ✅ | ✅ | ✅ |  | ✅ | ✅ | ✅ | ✅ | ✅ |  | ✅ |  | ✅ |  | ✅ |  |  | ✅ |  |
| **F455** |  | ✅ | ✅ |  | ✅ | ✅ |  | ✅ |  |  | ✅ |  | ✅ |  |  |  |  | ✅ |  |
| **F461** |  | ✅ | ✅ |  | ✅ | ✅ |  | ✅ | ✅ |  | ✅ |  | ✅ |  |  |  |  | ✅ |  |
| **H4684 / H/L4684** |  | ✅ | ✅ | ✅ | ✅ |  |  | ✅ |  |  | ✅ |  | ✅ | ✅ |  | ✅ | ✅ | ✅ |  |
| **H4890 / AM4890** |  | ✅ | ✅ |  | ✅ | ✅ | ✅ |  |  |  | ✅ |  | ✅ | ✅ | ✅ |  |  |  |  |
| **MH200** |  | ✅ | ✅ |  | ✅ | ✅ | ✅ | ✅ |  |  | ✅ | ✅ |  |  |  | ✅ |  | ✅ |  |
| **MH200N** | ✅ | ✅ | ✅ |  | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ |  | ✅ | ✅ |
| **MH201** | ✅ | ✅ | ✅ |  | ✅ |  |  | ✅ | ✅ | ✅ | ✅ |  | ✅ |  | ✅ |  |  |  |  |
| **MH202** |  | ✅ | ✅ |  | ✅ | ✅ | ✅ | ✅ | ✅ |  | ✅ |  | ✅ | ✅ | ✅ |  |  | ✅ |  |
| **MyHomeServer1** |  | ✅ | ✅ |  | ✅ |  | ✅ | ✅ | ✅ | ✅ | ✅ |  | ✅ | ✅ | ✅ | ✅ |  | ✅ |  |
<!-- TRACE_MATRIX_END -->

*Checkmarks (✅) indicate that at least one `diagnostic_summary.json` or `.txt` bus capture in our test corpus contains frames for that subsystem from the specified gateway model.*

## Devices captured behind a gateway

The matrix is keyed by the gateway OWNd connects to. Some corpus captures come from a plant whose distinctive device is not the gateway itself; those devices are listed here with the gateway their frames were received through.

| Device | Connected through | Subsystems in the captures | Corpus |
|---|---|---|---|
| **3488 HOMETOUCH** (video door-entry touch screen) | MyHomeServer1 (firmware 3.2.5) | WHO 1 lights, WHO 4 climate, WHO 7 video, WHO 8 door entry | `issue_466/myhome_sweep_MyHomeServer1_all_2026-11-04T09-35-58.json`, `issue_466/MyHomeServer1_full_debug_log_2026-11-04T10-30-17.txt` |
