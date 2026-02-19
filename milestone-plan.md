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

16. [Done] [Engineering] Add batch processing workflow
- Run analysis for many TIC IDs from an input list.
- Support resumable execution and per-target failure isolation.
- Exit criteria: batch command completes with resumable state and per-target status report.
- Implemented in `src/exohunt/pipeline.py` (`run_batch_analysis`) and CLI flags in `src/exohunt/cli.py`.

## Next Milestones (Refactoring-First)

17. [Done] [Refactor] Remove ingest sector filtering
- Remove `--sectors` from run/batch UX.
- Remove ingest-sector filtering from pipeline inputs and config schema.
- Ingest always uses all available sectors.
- Exit criteria: sector filter is not accepted in new UX; pipeline behavior is full-ingest by default.
- Theory note: explain completeness vs user-control tradeoff and why full-ingest is safer as default.
- Implemented in `src/exohunt/cli.py`, `src/exohunt/pipeline.py`, and `src/exohunt/ingest.py`.

18. [Done] [Refactor] Simplify plotting controls to mode-based behavior
- Remove `--plot-time-start`, `--plot-time-end`, `--plot-sectors` from new UX.
- Introduce `plot.mode = stitched | per-sector`.
- Ensure stitched mode writes one file and per-sector mode writes one file per sector.
- Exit criteria: plotting behavior is selected by mode only, with deterministic output naming.
- Theory note: explain why mode-based plotting reduces cognitive load while preserving review utility.
- Implemented in `src/exohunt/cli.py`, `src/exohunt/pipeline.py`, `src/exohunt/plotting.py`, and `tests/test_smoke.py`.

19. [Done] [Refactor] Normalize mode vocabulary + preprocessing toggle
- Normalize preprocess mode naming to `stitched | per-sector` (map legacy `global -> stitched`).
- Add `preprocess.enabled` toggle in resolved config.
- Define behavior when preprocessing is disabled.
- Exit criteria: mode names are consistent across preprocess/plot/bls and validated centrally.
- Theory note: document consistency principle and reduced ambiguity in CLI semantics.
- Implemented in `src/exohunt/cli.py`, `src/exohunt/pipeline.py`, `README.md`, and test fixtures in `tests/`.

20. [Done] [Refactor] Remove fixed operational knobs from user config
- Remove configurable `cache_dir` (fixed internal path).
- Remove configurable `max_download_files` (always unlimited in standard workflow).
- Keep internal implementation support only if needed for tests/debug.
- Exit criteria: these parameters are absent from user-facing presets, schema, and help.
- Theory note: explain why non-decision knobs should not be user-facing defaults.
- Implemented in `src/exohunt/cli.py` and `src/exohunt/pipeline.py` with fixed internal defaults for cache location and download limits.

21. [Done] [Engineering] Implement config schema + resolver
- Add versioned config schema and strict validation.
- Implement merge order: defaults -> built-in preset -> user file -> CLI explicit overrides.
- Add clear validation errors for invalid/unknown keys.
- Exit criteria: resolver produces a single canonical runtime config object for all commands.
- Theory note: describe deterministic configuration layering for reproducible science runs.
- Implemented in `src/exohunt/config.py` and integrated via `src/exohunt/cli.py` with coverage in `tests/test_config.py`.

22. [Done] [Engineering] Add built-in preset pack and init-config generator
- Implement built-ins: `quicklook`, `science-default`, `deep-search`.
- Add `init-config --from <preset> --out <file>`.
- Persist preset id/version/hash into run manifest.
- Exit criteria: users can scaffold valid config files from built-ins and run without low-level flags.
- Theory note: document progressive disclosure via presets and controlled override paths.
- Implemented in `src/exohunt/config.py` + `src/exohunt/cli.py`, with manifest persistence in `src/exohunt/pipeline.py` and tests in `tests/test_config.py` / `tests/test_smoke.py`.

23. [Done] [Engineering] Restructure CLI into command-oriented UX
- Introduce explicit commands:
  - `exohunt run --target ... --config ...`
  - `exohunt batch --targets-file ... --config ...`
  - `exohunt init-config --from ... --out ...`
- Keep temporary compatibility path for `python -m exohunt.cli` with deprecation messaging.
- Exit criteria: command-specific help is concise and mode-coupled validation runs before execution.
- Theory note: explain command partitioning and reduction of parameter overload.
- Implemented in `src/exohunt/cli.py` with updated usage docs in `README.md`.

24. [Done] [Validation] Migration, testing, and deprecation hardening
- Add tests for config parsing, preset resolution, and command behavior.
- Add migration tests for removed parameters and legacy value mapping (`global -> stitched`).
- Add deprecation/error messages with actionable replacements.
- Exit criteria: legacy users get clear migration paths; new config workflow is test-covered and stable.
- Theory note: describe backward-compatibility strategy and scientific reproducibility safeguards.
- Implemented via deprecation-key handling in `src/exohunt/config.py` with migration/command tests in `tests/test_config.py` and `tests/test_cli.py`.
