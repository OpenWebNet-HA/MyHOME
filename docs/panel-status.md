# Administration panel: implementation and validation status

Updated **22 November 2026**. Functional baseline: `feat/myhome-sidepanel` at
[`c08484e1`](https://github.com/xtimmy86x/MyHOME/commit/c08484e1e7c412eb9e2a0be88e7b460c48a3e6bc),
after fork PR #23. Panel asset version: **0.43.0**.

The panel is implemented on the development branch. Upstream
[PR #374](https://github.com/OpenWebNet-HA/MyHOME/pull/374) remains a **draft**;
this status does not announce a release or completion of physical validation.
This page is the current status reference; versioned notes and mockup tables in
older guides describe their stated historical baselines.

## Implemented on the branch

| Area | Current behaviour |
| --- | --- |
| Administration | Configure entry point, optional sidebar shortcut, gateway selection, WHO inventory, search, names/areas and integrated bus monitor. |
| WHO 2 navigation | Devices, Profiles and Calibration; the Calibration tab owns profile assignment and unlinking. |
| Calibration view | One suggested action per cover, an Overview/Details dialog, local travel/slat editing and a shared Change profile sheet. |
| Basic calibration | Guided opening/closing runs and, when applicable, a separate lift-off step for slats. A tape measure is not required for this path. |
| Tape refinement | Geometry measurements and a positioning check, including refinement using saved times (`keep_times`). Times measured on the source cover are preserved when its reference travel is corrected. |
| Profile persistence | Revision checks and atomic storage; a shared profile can be updated only from its source cover. Calibration of a follower creates its own profile instead of silently changing the shared one. |
| Shared save | Preview requested on arrival at review. Save is disabled until the current preview is available; Retry refreshes a refused preview without saving. A fresh tap saves, with explicit impact or unchanged-followers wording. |
| Recovery | Session ownership, observers, Stop/cancel, reconnect handling, refused-start recovery and retry after transient profile-assignment errors. |
| Mobile and browser support | Responsive sheets/dialogs, layered module loading for Safari, full truncated cover name in the steps sheet and instructions matching the actual measurement order. |

The Calibration tab measures one cover at a time. Its former **Calibrate in
sequence** entry was removed; the existing profile-dialog batch measurement
remains separate. Basic/tape calibration saves to a new profile or an eligible
shared profile, not to a cover-only calibration destination.

## Merged work

All PR numbers in this table refer to `xtimmy86x/MyHOME`.

| PR | Delivered |
| --- | --- |
| [#15](https://github.com/xtimmy86x/MyHOME/pull/15), [#16](https://github.com/xtimmy86x/MyHOME/pull/16) | Gateway status-reply handling and calibration backend: basic measurement, saved-time refinement, precision/evidence and monitor-copy correlation. |
| [#17](https://github.com/xtimmy86x/MyHOME/pull/17), [#18](https://github.com/xtimmy86x/MyHOME/pull/18) | Panel module loading, Calibration tab, wizard and Change profile. |
| [#19](https://github.com/xtimmy86x/MyHOME/pull/19), [#20](https://github.com/xtimmy86x/MyHOME/pull/20) | Preserve source timings, contain session-event failures, recover from refused starts and retry profile assignment. |
| [#21](https://github.com/xtimmy86x/MyHOME/pull/21) | Upstream `chore/release-2.0.1` integrated through `80657600`, preserving panel profiles, nonlinear timing and Dimension 10 promotion protections. |
| [#22](https://github.com/xtimmy86x/MyHOME/pull/22), [#23](https://github.com/xtimmy86x/MyHOME/pull/23) | Cover-name/measurement-order improvements and preview-before-save handling for shared profiles. |

[Fork #14](https://github.com/xtimmy86x/MyHOME/pull/14) remains the design discussion,
not a PR intended for merge.

## Validation evidence

- At PR #23 head `40ff0e74`, all five workflows passed: panel tests,
  backend tests/coverage, strict typing, hassfest and HACS validation.
  The PR reports **1,026 frontend tests** and **8,205 backend tests**.
  These are the functional baseline's results, not a new test run for this documentation update.
- The maintainer reported a successful installation test of integration PR #21
  before merging it. This does not by itself validate every later wizard path.
- The contributor reports test-Home-Assistant checks of delayed/refused shared
  previews, retries and unchanged followers. Earlier simulated-cover tests cover
  complete wizard paths and the corrected source-travel case.
- Automated and simulated results do not establish positioning accuracy on a
  physical installation. A combined field pass after #23 remains outstanding.

## Remaining work and scope limits

1. **Combined physical validation:** basic calibration with/without slats, tape
   refinement, profile assignment/unlinking, source/follower behaviour, shared
   updates, Stop, reconnect and intermediate-position accuracy. Record the tested
   gateway/actuator, branch commit and observed outcome before judging release readiness.
2. **Independent verification and preview-only flows:** the check performed inside
   geometry calibration is implemented. A separate check-only workflow is still
   follow-up work. The old path B profile-check and path C points-only designs
   remain development previews, disabled by default; assigning a profile through
   Change profile is implemented and must not be confused with those previews.
3. **Upstream cover changes:** [#975](https://github.com/OpenWebNet-HA/MyHOME/pull/975)
   (remaining-travel/drift settle timer) and
   [#977](https://github.com/OpenWebNet-HA/MyHOME/pull/977) (WHO 14 lock tracking)
   were still open on this date and are outside the #21 integration. Review their
   final upstream state before integrating them, preserving nonlinear timing,
   profile protections and calibration interruption. WHO 14 wizard handling is a
   separate follow-up after the backend settles.
4. **Release decision:** keep #374 draft until the combined result and remaining
   scope have been reviewed. No public-preview or release date is committed here.

## References

- [Sidepanel guide and version history](sidepanel.md)
- [Calibration UI: current behaviour and historical mockups](panel-calibration-screens.md)
- [Calibration session guide and historical protocol notes](cover-calibration.md)
- [Open a cover's Calibration view from another tab](panel-calibration-open-cover.md)
- [Coordination discussion #419](https://github.com/orgs/OpenWebNet-HA/discussions/419)
