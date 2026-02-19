# Milestone 03 Theory: Preliminary Planet Parameter Estimation

## Goal

For each BLS candidate, provide first-pass physical context that can be used for triage before full modeling.

## Radius ratio from transit depth

At first order for small planets and a uniform stellar disk:

- depth ~= (Rp / Rs)^2
- Rp / Rs ~= sqrt(depth)

This gives a quick radius-ratio estimate from the BLS box depth.

## Duration plausibility from period

Using a circular, central-transit reference, expected duration depends on period and stellar density:

1. Kepler scaling gives `a / R*` from period and assumed stellar density.
2. A central transit then gives a reference duration `T_ref`.
3. Compare fitted BLS duration to `T_ref` via `duration_ratio = T_obs / T_ref`.

A broad ratio gate is used as a plausibility check, not a hard classifier.

## Required assumptions in outputs

Each candidate output records assumptions and caveats explicitly, including:

- depth-to-radius mapping assumes no dilution and no strong limb-darkening effects
- duration baseline assumes circular orbit and central geometry
- host-star density is set to a solar-like default for first-pass checks

## Caveats

These are preliminary values only. Major uncertainty sources:

- detrending and systematics can bias BLS depth/duration
- unknown stellar properties (radius/density) propagate directly to inferred size
- impact parameter and eccentricity can strongly change transit duration

Use these fields for ranking and vetting context, then follow with stellar characterization and transit-model fitting.
