# Hardware Trace Availability Matrix

This matrix documents the real-world physical gateways for which we have captured bus traces and diagnostic bundles in our `tests/fixtures` corpus. It is an honest representation of the hardware configurations that we actively test against to validate our protocol implementation.

<!-- TRACE_MATRIX_START -->
| Gateway Model | WHO 0<br>Scenario | WHO 1<br>Lights | WHO 2<br>Autom. | WHO 4<br>Climate | WHO 5<br>Alarm | WHO 9<br>Power | WHO 13<br>Gateway | WHO 14<br>Lock | WHO 15<br>CEN | WHO 16<br>Audio | WHO 17<br>Scenario | WHO 18<br>Energy | WHO 22<br>Audio Diff. | WHO 25<br>Diag | WHO 1013<br>Diag | WHO 1022<br>Diag |
| :--- |  :---:  |  :---:  |  :---:  |  :---:  |  :---:  |  :---:  |  :---:  |  :---:  |  :---:  |  :---:  |  :---:  |  :---:  |  :---:  |  :---:  |  :---:  |  :---:  |
| **F454** |  | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ |  |  | ✅ |  |  |  |  | ✅ |  |
| **F461** |  | ✅ |  | ✅ |  |  |  |  |  |  |  |  |  |  |  |  |
| **H4890 / AM4890** |  | ✅ | ✅ |  | ✅ | ✅ |  |  |  | ✅ |  | ✅ | ✅ | ✅ |  |  |
| **MH200** |  | ✅ | ✅ |  | ✅ |  | ✅ |  |  | ✅ |  |  |  |  |  |  |
| **MH200N** | ✅ | ✅ | ✅ | ✅ |  |  | ✅ | ✅ | ✅ |  | ✅ | ✅ |  |  |  | ✅ |
| **MH201** | ✅ | ✅ | ✅ | ✅ |  |  | ✅ | ✅ | ✅ | ✅ |  | ✅ |  | ✅ |  |  |
| **MH202** |  |  | ✅ | ✅ |  |  | ✅ |  |  |  |  |  |  |  | ✅ |  |
| **MyHomeServer1** |  | ✅ | ✅ | ✅ |  | ✅ | ✅ | ✅ |  | ✅ |  | ✅ |  |  | ✅ |  |
<!-- TRACE_MATRIX_END -->

*Checkmarks (✅) indicate that at least one `diagnostic_summary.json` or `.txt` bus capture in our test corpus contains frames for that subsystem from the specified gateway model.*
