# Project: Exohunt Pipeline — Path to 10/10

## Type
research

## Description
Research what specific improvements would make the Exohunt exoplanet transit detection pipeline world-class (10/10) for discovering new exoplanets from TESS photometry. The pipeline currently scores ~6/10 — it has TLS transit search, iterative masking with vetting, and batch processing, but lacks several capabilities that professional pipelines use.

The deliverable is a prioritized list of specific, actionable improvements with:
- What the improvement is
- Why it matters (what false positives/negatives it prevents)
- How professional pipelines implement it (specific algorithms, papers, tools)
- Estimated implementation effort
- Expected impact on detection rate and false positive rate

## Background
The Exohunt pipeline processes TESS light curves to search for transiting exoplanet candidates. Current capabilities:
- Ingests TESS SPOC light curves via lightkurve
- Per-sector SavGol flattening for detrending
- TLS (Transit Least Squares) as primary search, BLS as fallback
- Iterative masking with vetting gate for multi-planet detection
- Vetting: odd/even depth, secondary eclipse, alias/harmonic, depth consistency
- Batch processing with resume and network retry
- Crossmatching against NASA Exoplanet Archive TOI catalog

Validated on TOI-1260 (3/3 planets recovered) and TOI-178 (1/6, limited by data).

### Known gaps to investigate (starting points, not exhaustive):

1. **Centroid / pixel-level analysis** — Currently no way to distinguish a planet transit from a nearby eclipsing binary bleeding into the TESS aperture. High-contamination targets (>20% TIC contamination ratio) produce false positives. Research how TESS pipelines do difference imaging or centroid shift analysis.

2. **Stellar parameter integration** — TLS runs with default solar parameters. Passing actual R_star, M_star, Teff from the TIC catalog would improve the transit model and duration grid. Research how to query TIC parameters and feed them to TLS.

3. **Gaussian Process detrending** — SavGol flattening is basic and can eat transit signals or leave correlated noise. GP detrending (e.g., celerite2, george, tinygp) models stellar variability while protecting transit windows. Research the ARIMA+TCF approach from Caceres et al. as an alternative.

4. **Statistical validation framework** — Currently no way to compute a false positive probability for candidates. Tools like VESPA (Morton 2012, 2015) and TRICERATOPS (Giacalone & Dressing 2020) compute the probability that a signal is a planet vs. eclipsing binary, background EB, etc. Research what's needed to integrate one of these.

5. **Transit model fitting with batman / PyTransit** — Currently we only use BLS box fits for depth/duration. Research whether fitting a proper physical transit model using libraries like batman (Kreidberg 2015), PyTransit, or exoplanet (Foreman-Mackey et al.) would improve parameter estimation and help distinguish real transits from systematics. Specifically investigate:
   - Does fitting a limb-darkened transit model improve depth/duration accuracy?
   - Can model fitting provide better transit time precision for TTV analysis?
   - Would MCMC-based fitting (e.g., emcee, PyMC) give useful uncertainty estimates?
   - Is this needed for credible candidate publication, or is BLS/TLS sufficient for detection?

6. **Known planet catalog and pre-masking strategy** — Research whether the pipeline should:
   - Maintain a local catalog of known exoplanets (periods, epochs, durations) from the NASA Exoplanet Archive and TOI list for faster analysis without network queries
   - Pre-mask or subtract known planet transits BEFORE running the search, so the pipeline only searches for NEW signals. This could dramatically improve sensitivity for additional planets in known systems.
   - Mask all confirmed planets, or also mask unconfirmed TOI candidates? What's the risk of masking a real signal that was incorrectly cataloged?
   - How do professional pipelines (SPOC, QLP, DTARPS) handle known signals — do they mask, subtract a model, or ignore them?
   - What's the right approach: mask (set to NaN), subtract a fitted transit model (using batman), or use a simultaneous multi-planet fit?

7. **Secondary eclipse and phase curve analysis** — For short-period candidates, checking for thermal emission at phase 0.5 or ellipsoidal variations can distinguish planets from EBs. Research what checks are standard.

8. **Multi-sector systematics modeling** — Different TESS sectors have different systematic noise patterns. Currently we flatten each sector independently and stitch. Research whether joint modeling across sectors (e.g., with cotrending basis vectors) improves sensitivity.

9. **Runtime optimization** — TLS takes ~35 min per search pass. Research whether GPU-accelerated BLS/TLS, smarter period grid sampling, or the TCF algorithm could reduce this without sacrificing sensitivity.

10. **Injection-recovery tests** — No systematic measurement of pipeline completeness (what fraction of planets at a given depth/period are recovered). Research how to run injection-recovery and compute detection efficiency maps.

11. **Machine learning vetting** — Current vetting uses hand-crafted rules. Research whether a trained classifier (like Astronet, Exoplanet, or DAVE) on phase-fold images could improve vetting accuracy.

12. **Nearby star contamination check** — Beyond the TIC contamination ratio, research whether querying Gaia DR3 for nearby sources and computing flux contamination would help flag false positives.

## Constraints
- Pipeline is Python 3.10+, runs on a MacBook Pro
- Must work with publicly available TESS data (SPOC light curves from MAST)
- No access to proprietary RV data or ground-based follow-up
- Runtime budget: up to 1 hour per target is acceptable
- Dependencies should be pip-installable, no custom C/Fortran builds
- Changes should be incremental — each improvement should be independently testable

## Existing Code/System
- Repository: `/Users/gbasin/Development/exoplanets-hunting-pipeline`
- Key modules:
  - `src/exohunt/tls.py` — TLS search wrapper
  - `src/exohunt/bls.py` — BLS search + iterative masking loop
  - `src/exohunt/vetting.py` — candidate vetting checks
  - `src/exohunt/preprocess.py` — light curve flattening
  - `src/exohunt/pipeline.py` — main pipeline orchestration
  - `src/exohunt/config.py` — TOML config and presets
  - `src/exohunt/crossmatch.py` — NASA archive crossmatching
  - `src/exohunt/collect.py` — candidate aggregation
- Config presets: `src/exohunt/presets/{science-default,deep-search,quicklook}.toml`
- TLS integration plan: `.docs/tls_integration_plan.md`
- Test suite: 138 tests in `tests/`

## Success Criteria
- Produce a prioritized list of 8-12 specific improvements
- Each improvement has: description, rationale, reference implementation/paper, effort estimate (S/M/L), expected impact (high/medium/low)
- Improvements are ordered by impact-to-effort ratio (quick wins first)
- The list is realistic — implementing all items would genuinely make the pipeline competitive with professional survey pipelines (TESS QLP, SPOC, DTARPS)
- Include a "minimum viable" subset (3-4 items) that would get the pipeline from 6/10 to 8/10

## Human Gates
none

## Additional Notes
- The research paper "A study of two periodogram algorithms for improving the detection of small transiting planets" (Gondhalekar & Feigelson 2023, arXiv:2308.04282) is highly relevant — it compares BLS vs TCF and recommends ARIMA+TCF for small planet detection
- The TLS paper (Hippke & Heller 2019) is already implemented
- Focus on improvements that help find NEW planets, not just recover known ones
- Consider what would make a candidate credible enough to publish or submit as a community TOI (CTOI) on ExoFOP
- The known planet pre-masking question (item 6) is particularly important — the pipeline currently wastes its strongest BLS/TLS detection on re-finding known planets, then relies on iterative masking to find new ones. Pre-masking known signals could make the first search pass immediately sensitive to new planets.
