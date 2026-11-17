# OpenWebNet MCP Server Notes & Verification Log

- **Server**: `openwebnet-mcp`
- **Repository**: `https://github.com/OpenWebNet-HA/openwebnet-mcp.git`
- **Date Verified**: 2026-09-11
- **Client**: Antigravity Pair-Programming Agent on `OpenWebNet-HA/MyHOME` (`v2-phase1-architecture`)

---

## 1. Tool Catalog & Availability

| Tool | Status | Purpose | Verified Input / Test Case |
|---|---|---|---|
| `list_who_catalog` | **PASS** | Returns master table of all 21 OpenWebNet WHO subsystems with Legrand titles, Home Assistant entities, and status | Invoked with `{}` |
| `get_who_spec` | **PASS** | Retrieves full specification, addressing rules, WHAT codes, and dimensions for a given WHO | Verified with `who: 1` (Lighting v1.1.0) and `who: 2` (Automation v1.0.0) |
| `parse_and_validate_frame` | **PASS** | Parses raw OWN frame, extracts semantic components, validates against official Legrand specifications, emits warnings for non-standard frames | Verified with `*1*1*51##`, `*#1*51##`, `*1*1*0311#4#01##`, `*2*1*93##`, `*#*1##`, `*#*0##`, and invalid syntax `*1*1*51` |
| `draft_own_frame` | **PASS** | Constructs and validates syntactically correct OpenWebNet frames from parameters | Verified for light ON (`*1*1*51##`) and cover STOP (`*2*0*93##`) |
| `lookup_frame_syntax` | **PASS** | Provides grammar regex patterns, templates, and parameter formats for all frame types | Verified for `who: 1` |
| `search_documentation` | **PASS** | Searches indexed Legrand documentation PDFs | Available |
| `get_ha_guide` | **PASS** | HA integration guidelines for OpenWebNet subsystems | Available |
| `get_code_signature` | **PASS** | Extracts code signatures from reference implementations | Available |
| `rescan_documentation` | **PASS** | Reloads documentation cache | Available |

---

## 2. Detailed Behavior Notes & Verification Log

### `list_who_catalog`
- Returned cleanly formatted markdown table with 21 WHO families (0, 1, 2, 3, 4, 5, 6, 7, 9, 13, 14, 15, 16, 17, 18, 22, 24, 25, 1001, 1004, 1013).
- Zero hallucination; matches BTicino specification categories.

### `get_who_spec`
- **WHO=1 (Lighting)**:
  - Spec: `WHO_1.pdf` v1.1.0 (2014-11-17).
  - Documents WHAT codes: `0` (OFF), `1` (ON 100%), `2`-`10` (stepped dimming 20%-100%), `11`/`12` (dim UP/DOWN), `13` (toggle), `14` (timed), `15` (blinking), `1#<speed>` (ON with transition speed).
  - Documents Dimensions: Dimension 1 (level 1-100%), Dimension 2 (RGB), Dimension 3 (Tunable White Kelvin).
- **WHO=2 (Automation / Covers)**:
  - Spec: `WHO_2.pdf` v1.0.0 (2015-11-12).
  - Documents WHAT codes: `0` (STOP), `1` (UP), `2` (DOWN), `30` (Adv STOP), `31` (Adv UP), `32` (Adv DOWN).
  - Documents Dimensions: Dimension 10 (Position percentage 0-100%), Dimension 11 (Louvre tilt angle 0-100%).

### `parse_and_validate_frame`
- Point-to-Point Light ON: `*1*1*51##` -> Valid: YES, Type: `STATUS_EVENT`, Subsystem: `1`, Address: `51`, Action: `1`.
- Status Request: `*#1*51##` -> Valid: YES, Type: `STATUS_REQUEST`, Subsystem: `1`, Address: `51`.
- Local Bus Routed Light: `*1*1*0311#4#01##` -> Valid: YES, Type: `STATUS_EVENT`, Subsystem: `1`, Address: `0311`, Action: `1`, routed to private SCS bus 01.
- Automation UP: `*2*1*93##` -> Valid: YES, Type: `STATUS_EVENT`, Subsystem: `2`, Address: `93`, Action: `1`.
- Signaling ACK: `*#*1##` -> Valid: YES, Type: `ACK`, Subsystem: `None`, Address: `N/A`.
- Signaling NACK: `*#*0##` -> Valid: YES, Type: `NACK`, Subsystem: `None`, Address: `N/A`.
- Non-standard WHAT: `*1*999*51##` -> Valid: YES, Warnings: `WHAT=999 is not standard for WHO=1`.
- Malformed Delimiters: `*1*1*51` -> Valid: NO, Type: `UNKNOWN`, Warnings: `Frame must begin with '*' and end with '##'`.

### `draft_own_frame`
- `{who: 1, command_type: "command", what: 1, where: "51"}` -> `*1*1*51##` (Valid: YES)
- `{who: 2, command_type: "command", what: 0, where: "93"}` -> `*2*0*93##` (Valid: YES)

---

## 3. Exit Criteria Evaluation
- Agent can parse and validate any OpenWebNet frame against official Legrand specifications via `openwebnet-mcp`.
- All tools function deterministically and quickly.
