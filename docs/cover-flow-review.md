# Cover flow review — panel 0.27.1

Reviewed on 2026-09-17, starting from panel 0.27.0 at
`4c8f8138904e923584d758ef3ad32df85e33f8c2` on `feat/myhome-sidepanel`.

## Scope and retained design

The review traced the single-cover editor, guided/automatic/single-direction
calibration, measurement review, recovery, personal values, shared-profile
management and multi-cover assignment. Backend review focused on how these
screens use the existing revision checks, previews, persistence and pending
runtime application.

The current layout remains appropriate: direct calibration from a device card,
collapsible sections for detailed operations, and an optional profile view for
shared relationships. There is no reason from this review to insert the profile
catalogue into the single-cover calibration path or add more mandatory steps.
Existing measurement review and shared-change confirmation remain useful because
they expose what will be saved and which covers will be affected.

## Confirmed findings and corrections

| Finding | Effect | Correction |
| --- | --- | --- |
| Calibration mode/direction were absent from draft comparison | A remote revision could replace the form and reset a chosen single direction or automatic mode | Preserve the selectors as draft state; request explicit reload after a conflict |
| Same-revision reads ignored changed writability | The device editor could retain writable controls while unavailable, or remain read-only after recovery | Refresh on HA availability transitions and reconcile backend writability even without a configuration revision; preserve dirty forms and invalidate their previews |
| Assignment search depended on concatenated rendered text | Spaced A-PL queries could miss a matching cover | Search escaped, gateway-scoped name/entity/address data using multiple terms |
| Search input belonged to a form with a confirmation submit button | Enter during search could implicitly submit the current assignment preview | Prevent implicit Enter submission specifically in the search input; leave the explicit confirmation action available |

The first regression tests reproduced failures against 0.27.0. The fixes are
limited to frontend behavior and the panel asset version. Backend contracts,
storage v6, export v3 and physical calibration/timing behavior are unchanged.

## Validation

- 152 frontend tests passed, including eight additional regression scenarios.
- 298 targeted Python tests passed for panel registration, profile persistence,
  overrides, shared previews, catalogue management, atomic assignment, calibration
  save modes, single-direction measurement, recovery and automatic batches.
- Existing tests cover draft retention, late responses, unavailable/foreign
  targets, storage failure, exact confirmation and pending timing application.
- Validation uses jsdom and the Home Assistant test environment. This review does
  not substitute for visual/mobile or physical gateway testing. The operator
  previously confirmed 0.27.0 on the test installation; the new corrections still
  need the corresponding checks on that installation.

## Focused installation checks

1. Select a single calibration direction, then save a profile from another tab.
   The selection should remain visible, with writes blocked until explicit reload.
2. With the cover editor open, disconnect/reconnect the gateway. A pristine form
   should reflect availability; an edited form should retain its draft and offer
   reload instead of silently replacing it.
3. Search the assignment list with a spaced A-PL query. With a preview open, press
   Enter in search: it must not save. Confirm using the action button.
