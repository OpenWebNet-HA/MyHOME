# F454 gateway regression checks

This branch addresses the gateway and sensor failures seen during discovery and
normal bus traffic. The tests use synthetic frames and a local mock gateway;
validation on a physical F454 is still required.

## Changes

- Accept time broadcasts and replies when the optional timezone is empty or
  absent. Keep an unspecified timezone unspecified. Reject truncated or invalid
  clock values without losing the following event.
- Read command replies through their terminal ACK or NACK, including discovery
  responses longer than 20 frames. Bound each transaction to 30 seconds. Close
  incomplete sessions before reuse, and avoid repeating an actuation whose ACK
  was lost. An immediate NACK may retry once.
- Deliver energy, power, temperature and illuminance updates to configured
  sensors. Process each YAML device once even when it has a WHERE alias.
- Discover WHO 18 power and energy measurements as they arrive. Restore their
  registry identities after reload, preserving names and disabled entities.
  Discover only reported measurements; daily and monthly energy remain disabled
  by default. Explicit sensor configuration takes precedence at that address.
- Use the gateway's registry ID for device links on Home Assistant versions that
  support `via_device_id`, with compatibility for older versions. Replace the
  removed media-player standby enum with OFF and IDLE behavior. Remove an unused
  `async_timeout` import that prevents loading on a clean newer installation.
- Keep routine bus traffic at debug level; retain warnings for malformed frames,
  explicit rejections and transport failures.

## Automated verification

Run from the repository root in an environment with the project's test and
packaging dependencies installed:

```bash
pytest tests/ --cov=custom_components.myhome --cov-report=xml --cov-report=term-missing
python scripts/verify_ownd_coverage.py
validate-pyproject pyproject.toml
python -m build
twine check --strict dist/*
check-wheel-contents dist/*.whl
```

The strict coverage gate covers every `ownd` module except the interactive CLI.
Run the tests both with the Python 3.12 environment used by CI and with the Python
version required by current Home Assistant. This fix was checked with Home
Assistant 2025.1.4 / Python 3.12 and Home Assistant 2026.9.1 / Python 3.14.

`tests/test_gateway_regressions.py` covers incomplete clock frames, discovery with
19/20/30/100 replies, ACK ownership, timeouts, cancellation and bounded retries.
`tests/test_sensor_dispatcher.py` checks state updates, discovery bursts, registry
restoration, multiple gateways and YAML precedence through real HA platforms.
The existing mock-gateway, media-player, snapshot and packaging checks also apply.

## Physical gateway verification

1. Install the branch's `custom_components/myhome` folder and restart Home
   Assistant. Verify that existing entity IDs and customized names are retained.
2. Check discovery on an installation with more than 20 light responses. Verify
   that subsequent cover, heating and audio requests receive their own replies.
3. Leave the integration running through several clock and energy reports.
   Check that the clock no longer causes `IndexError` and power/energy values
   update. Enable daily or monthly energy entities if needed.
4. Reload the integration and confirm that discovered sensors return with the
   same IDs. Verify normal light, cover and audio operation.
5. In a controlled test, interrupt the gateway connection. Confirm that timed-out
   command sessions are closed, later commands reconnect, and an actuation with
   a lost ACK is not automatically sent again.

These changes do not address unrelated Bluetooth permissions, camera libraries
or other custom integrations reported in the same Home Assistant log.
