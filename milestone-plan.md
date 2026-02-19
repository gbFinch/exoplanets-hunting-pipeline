# Exohunt Data Analysis Milestone Plan

Important note: "When implementing the step explain the theory behind the milestone"

## Milestones

1. [Done] [Research] Define quantitative success criteria
- Set target detection regime (depth, period, duration, minimum transit count).
- Set acceptable false-positive budget and review throughput.
- Choose benchmark target list (known planets + quiet non-planet stars).
- Exit criteria: a documented metrics table and benchmark list committed in `.docs/`.
- Implemented in `.docs/success-criteria.md`.

2. [Done] [Engineering] Add local target cache
- Cache stitched light curves per target.
- Add cache reuse and refresh option in CLI.
- Exit criteria: repeated runs on same target skip network download.

3. [Done] [Engineering] Make cache format robust
- Store cache as `.npz` (`time`, `flux`) to avoid FITS serialization failures.
- Add tests for cache hit and cache miss behavior.
- Exit criteria: cache read/write passes tests and no centroid serialization errors occur.

4. [Done] [Engineering] Add preprocessing v1 pipeline
- Apply TESS quality mask on download (`quality_bitmask="default"`).
- Apply `remove_nans`, `normalize`, `remove_outliers`, and `flatten`.
- Add safe flatten-window handling for short light curves.
- Exit criteria: CLI produces prepared light curve for benchmark targets without runtime failure.

5. [Done] [Research] Add theory notes for preprocessing milestone
- Add in-code theory summary in `prepare_lightcurve()` docstring.
- Add `.docs/milestone-01-preprocessing-theory.md`.
- Exit criteria: theory for each preprocessing action is documented in code/docs.

6. [Done] [Validation] Upgrade diagnostics plot to raw vs prepared
- Save two-panel plot to compare raw and prepared light curves.
- Log preprocessing parameters and point counts.
- Exit criteria: each run emits a plot that visually compares raw and prepared series.

7. [Done] [Validation] Add preprocessing quality metrics
- Compute before/after stats (RMS, MAD, retained cadence fraction, trend proxy).
- Emit per-target preprocessing summary artifact.
- Exit criteria: each run writes a metrics row showing raw-to-prepared improvement values.
- Implemented in `.docs/milestone-02-preprocessing-metrics-theory.md`.

8. [Done] [Research] Add preprocessing method comparisons
- Evaluate multiple detrending configurations on benchmark targets.
- Document recommended defaults by cadence/sector length.
- Exit criteria: a comparison report selects default preprocessing settings with rationale.
- Implemented via `src/exohunt/comparison.py` with report output `outputs/reports/preprocessing-method-comparison.md`.

9. [Done] [Engineering] Implement BLS transit search core
- Run Box Least Squares on prepared light curves.
- Return top candidate periods, durations, depths, and power.
- Exit criteria: BLS module returns ranked candidates for a target without manual intervention.
- Implemented in `src/exohunt/bls.py` and integrated into `src/exohunt/pipeline.py`.

10. [Done] [Engineering] Persist candidate tables
- Write ranked BLS candidates to CSV/JSON under `outputs/candidates/`.
- Include preprocessing parameters and run metadata in outputs.
- Exit criteria: each run writes structured candidate files reproducibly.
- Implemented in `src/exohunt/pipeline.py` via `_write_bls_candidates()`.

11. [Done] [Validation] Add candidate diagnostic products
- Save periodograms for top detections.
- Save phase-folded plots with transit-window overlays.
- Exit criteria: top-N candidates each have periodogram and phase-folded diagnostic assets.
- Implemented in `src/exohunt/plotting.py` + `src/exohunt/pipeline.py`.

12. [Done] [Validation] Add candidate vetting heuristics v1
- Add odd-even depth comparison.
- Add minimum transit-count and alias/harmonic checks.
- Exit criteria: each candidate has pass/fail vetting flags with recorded reasons.
- Implemented in `src/exohunt/vetting.py` and integrated into candidate outputs in `src/exohunt/pipeline.py`.

13. [Done] [Engineering] Add preliminary planet parameter estimation
- Estimate first-pass radius ratio and duration-based plausibility checks.
- Record assumptions and uncertainty caveats in outputs.
- Exit criteria: candidate records include parameter estimates and explicit assumptions.
- Implemented in `src/exohunt/parameters.py` and integrated into candidate outputs in `src/exohunt/pipeline.py`.

14. [Done] [Validation] Expand automated tests for analysis modules
- Add unit tests for preprocessing metrics and BLS ranking logic.
- Add integration tests with fixed fixtures for reproducibility.
- Exit criteria: tests cover core analysis flow and pass in CI/local venv.
- Implemented in `tests/test_analysis_modules.py` and fixture `tests/fixtures/fetch_pipeline_expected.json`.

15. [Done] [Engineering] Add run manifest and reproducibility tracking
- Save run config, package versions, and timestamps per analysis run.
- Ensure reruns can be compared target-by-target.
- Exit criteria: each run has a manifest that can recreate analysis settings.
- Implemented in `src/exohunt/pipeline.py` with coverage in `tests/test_smoke.py`.

16. [Pending] [Engineering] Add batch processing workflow
- Run analysis for many TIC IDs from an input list.
- Support resumable execution and per-target failure isolation.
- Exit criteria: batch command completes with resumable state and per-target status report.
