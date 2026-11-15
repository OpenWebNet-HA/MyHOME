# Opening a cover's Calibration card from another tab

The Calibration tab (WHO 2 covers) shows each timed cover's card (its details: times and precision, profile and values, last
calibration) in its own overlay. Another tab of the panel, such as Profiles or Devices, can open that card for a given
cover. This page describes the contract. No existing control uses it yet: wiring a button to it is the owning tab's choice.

## Calibration tab

```js
calibrationTab.requestCover(entryId, entityId, { returnTo } = {}) // → boolean
```

- `entryId`: the gateway's config entry id; `entityId`: the cover's entity id.
- The card opens as soon as the tab has read that gateway (immediately if it already has). It is the same card a tap on
  the cover's row opens, in the same overlay, with the same actions.
- `returnTo` (optional function) is called once, with no arguments, when the card and anything opened from it
  (*Change profile*, a calibration flow) have closed and the Calibration list is shown again. It is not called if the
  cover is not in the tab (unknown, removed, or a cover with a position sensor) or if the tab is cleared first.
- Returns `false`, and does nothing, while the tab already shows something over its list: a wizard, an open
  session, a card or *Change profile*. A request is never queued behind them, so a card never opens by surprise later.
- A request survives the tab's own reset when the panel shows the tab right after it, as long as the gateway is shown.
- Known limit: the tab shows one gateway's covers at a time. For a cover of a gateway other than the one shown,
  `requestCover` returns `true` but nothing opens yet: the request waits until that gateway is shown in the tab.

## Panel shell

```js
panel._openCalibrationCover(entity, options) // → boolean
```

The twin of `_openCalibrationWizard`: it switches to the Calibration tab of WHO 2 (the single-category view included),
calls `requestCover(entity.entry_id, entity.entity_id, options)` and scrolls the tab into view. `entity` is an entity of
the panel's inventory; `options` is passed through (`{ returnTo }`). It returns what `requestCover` returned.

## What it does not do

It reads and writes nothing: the card loads the same overview as the list. It does not change the Profiles or Devices
tabs, their dialogs or their controls.

Tests: `tests/frontend/calibration-shell.test.mjs`.
