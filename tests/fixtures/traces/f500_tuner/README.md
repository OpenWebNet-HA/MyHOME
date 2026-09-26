# F500N Tuner Traces (PR #427)

Authentic on-wire bus frames captured by `@manfredgittmaier-afk` on a live installation:
- **Gateway**: MH200N (firmware 2.0.32)
- **Tuner**: Legrand / BTicino F500N (Source 101) with antenna connected
- **Reported on**: [PR #427 (comment 5847535313)](https://github.com/OpenWebNet-HA/MyHOME/pull/427#issuecomment-5847535313)

### Validated Protocol Behaviors
1. **Next / Previous Station**:
   - `*16*6001*101##` (next station) and `*16*6101*101##` (previous station)
2. **Station Write Asymmetry**:
   - Write: `*#16*101*#7*<N>##` (without leading zero)
   - Status Report: `*#16*101*7*0*<N>##` (with leading zero)
3. **Frequency Format**:
   - `*#16*101*6*0*96200##` -> 96.2 MHz (expressed in kHz, 5 digits)
4. **Autonomous RDS**:
   - `*#16*101*8*75*82*79*78*69*72*73*84##` -> `"KRONEHIT"` (8 ASCII bytes)
   - Follow-up dynamic text: `*#16*101*8*107*114*111*110*101*104*105*116##` -> `"kronehit"`
5. **WHO=22 Mirroring**:
   - Every WHO=16 tuner frame is echoed on WHO=22 with compound source address `5#2#1`.
6. **Direct Frequency Tuning (Dimension 6)**:
   - Write with leading zero: `*#16*101*#6*0*96200##` -> tunes to 96.2 MHz (reported in [comment 5848039616](https://github.com/OpenWebNet-HA/MyHOME/pull/427#issuecomment-5848039616)).
   - Write without zero: `*#16*101*#6*96200##` -> ignored, no reply on bus.
7. **Hardware Seek Up / Down**:
   - Seek up: `*16*5000*101##` -> locks to next station (e.g. 89.5 MHz).
   - Seek down: `*16*5100*101##` -> locks to previous station (e.g. 88.8 MHz).
8. **RDS Blanking and Dynamic Text**:
   - After tuning, tuner sends 8 spaces `*#16*101*8*32*32*32*32*32*32*32*32##` before delivering station title.
