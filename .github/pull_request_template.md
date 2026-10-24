## Summary

<!-- Briefly describe what this pull request changes and why. -->

Fixes #<!-- issue number, if applicable -->

## Changes

- 

## Protocol & Bus Trace Evidence (if applicable)

<!-- If this PR touches OpenWebNet frame parsing, device discovery, or entity state handling, list the gateway model/firmware, WHO/WHAT/Dimension frames, or anonymous bus trace / fixture used to verify the behavior. -->

- **Gateway / Firmware:** 
- **Frames / Fixtures verified:** 

## Checklist

- [ ] **PR Description:** This pull request has a clear title and summary explaining the change.
- [ ] **Trace-Backed (if protocol/device change):** Behavior is verified against real OpenWebNet bus traces or gateway firmware fixtures rather than guessed.
- [ ] **V2 Architecture:** New or modified entities implement `handle_event()` and do not manually subscribe via `async_dispatcher_connect`.
- [ ] **Tests & Linters:** `pytest`, `ruff`, and `scripts/verify_ha_standards.py` pass.
