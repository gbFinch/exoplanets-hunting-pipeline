# Exohunt Data Analysis Milestone Plan

Important note: "When implementing the step explain the theory behind the milestone"

## Milestones

1. [Pending] Define analysis goals and success metrics
- Set explicit detection targets (e.g., SNR threshold, max false-positive rate, minimum transit depth sensitivity).

2. [Pending] Build a robust light-curve ingestion layer
- Standardize loading from cache/download, metadata tracking, and per-sector provenance.

3. [Pending] Add quality filtering and outlier handling
- Apply quality flags, remove bad cadences, and clip non-astrophysical outliers safely.

4. [Pending] Implement detrending and normalization pipeline
- Add flattening/systematics correction options and compare methods on known targets.

5. [Pending] Create diagnostic visualizations
- Generate raw vs cleaned plots, trend model overlays, and residual diagnostics per target.

6. [Pending] Implement transit search module (BLS-first)
- Run Box Least Squares period search and store top candidate periods, durations, and depths.

7. [Pending] Add candidate scoring and ranking
- Score detections by SNR, odd-even consistency, transit count, and period plausibility.

8. [Pending] Build false-positive vetting checks
- Add checks for eclipsing binaries, harmonic aliases, centroid shifts, and data-artifact signatures.

9. [Pending] Add phase-folded validation products
- Produce phase-folded plots at candidate periods with binned overlays and transit windows.

10. [Pending] Estimate preliminary planet parameters
- Compute first-pass radius ratio, period, duration, and equilibrium-temperature proxies (with assumptions logged).

11. [Pending] Benchmark on known TESS planets
- Validate recovery rate against confirmed systems before searching for new candidates.

12. [Pending] Expand automated tests for analysis pipeline
- Add unit/integration tests for preprocessing, period search, and vetting logic with reproducible fixtures.

13. [Pending] Add experiment tracking and reproducibility
- Log config, software versions, random seeds, and outputs for every analysis run.

14. [Pending] Create a candidate report/export workflow
- Produce machine-readable candidate summaries (CSV/JSON) plus a human review report.

15. [Pending] Prepare for scale and batch processing
- Add batch target runs, resumability, and parallel-safe execution for many TIC IDs.
