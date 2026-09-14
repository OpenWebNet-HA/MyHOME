# Synthetic MyHOME test installation

These small, invented YAML and diagnostic fixtures exercise extended addresses,
lights, a dimmer, a switch, a cover, a climate central, dry contacts and power
measurements. They contain no captured installation data or credentials.

The registry, state-dispatch, burst and generic replay tests always run with these
fixtures. Optional real installation captures can be supplied locally under
`tests/fixtures/plants/<capture>/`; their existing replay assertions remain active
when those directories exist. Missing optional captures are explicitly skipped.
The mandatory synthetic fixture is still required: missing or malformed files fail.
