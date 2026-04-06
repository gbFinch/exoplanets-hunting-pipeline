---
agent: research
sequence: 1
references: []
summary: "Researched 12 improvement areas for the Exohunt pipeline to reach professional-grade exoplanet detection. The highest-impact quick wins are stellar parameter integration into TLS, known-planet pre-masking, and centroid/pixel-level vetting. A minimum viable subset of 4 improvements (stellar params, pre-masking, centroid vetting, TRICERATOPS validation) would move the pipeline from 6/10 to 8/10."
---

## 1. Research Objective

**Topic**: Specific, actionable improvements to make the Exohunt exoplanet transit detection pipeline competitive with professional survey pipelines (TESS SPOC, QLP, DTARPS-S) for discovering new transiting exoplanets from TESS photometry.

**Motivation**: The pipeline currently scores ~6/10 — it has TLS transit search, iterative masking with vetting, and batch processing, but lacks capabilities that professional pipelines use to reduce false positives and increase sensitivity to small/long-period planets.

**Scope**: Improvements to the detection, vetting, and validation stages of the pipeline. Excludes ground-based follow-up, RV observations, and hardware changes. All improvements must be pip-installable Python, runnable on a MacBook Pro within ~1 hour per target.

**Key Questions**:
1. Which stellar parameters from the TIC catalog improve TLS sensitivity, and how should they be queried and passed?
2. Does pre-masking known planet transits before the search meaningfully improve sensitivity to additional planets compared to the current iterative approach?
3. What pixel-level or centroid analysis can distinguish on-target transits from nearby eclipsing binary contamination using only TESS data products?
4. Which statistical validation tool (TRICERATOPS, VESPA) is most practical for computing false positive probabilities for TESS candidates?
5. Does GP detrending (celerite2) or ARIMA+TCF provide better sensitivity than SavGol flattening for small planet detection?
6. What injection-recovery framework would quantify pipeline completeness, and how expensive is it computationally?
7. Can a pre-trained ML classifier replace or augment the current hand-crafted vetting rules?

## 2. Methodology

**Sources**: TLS documentation and paper (Hippke & Heller 2019), TRICERATOPS paper and PyPI (Giacalone & Dressing 2020), batman documentation (Kreidberg 2015), celerite2 documentation (Foreman-Mackey et al.), nuance paper and PyPI (Garcia et al. 2024), Gondhalekar & Feigelson 2023 (ARIMA+TCF), lightkurve CBV documentation, TESS SPOC pipeline documentation, tesscentroidvetting PyPI, vetting PyPI, RAVEN pipeline paper (2025), astroquery TIC catalog documentation.

**Evaluation Criteria**: Detection sensitivity improvement, false positive reduction, implementation effort, computational cost, dependency complexity, pip-installability, compatibility with existing pipeline architecture.

**Constraints**: Python 3.10+, pip-installable dependencies, MacBook Pro runtime, TESS SPOC public data only, incremental/independent testability.

## 3. Findings

### 3.1 Stellar Parameter Integration (TIC → TLS)

**Overview**: TLS accepts `R_star`, `M_star`, `R_star_min`, `R_star_max`, `M_star_min`, `M_star_max`, and limb darkening coefficients (`u`) as parameters. When these are set to actual stellar values instead of solar defaults, TLS generates a more accurate transit model template and duration grid, improving detection efficiency by ~10% for non-solar-type hosts.

**Key Characteristics**:
- TLS uses stellar radius and mass to compute the expected transit duration grid via Kepler's third law
- Limb darkening coefficients are interpolated from Claret (2017) tables based on Teff, logg, [Fe/H]
- The TIC v8.2 catalog provides R_star, M_star, Teff, logg, [Fe/H] for most TESS targets
- `astroquery.mast.Catalogs.query_object("TIC NNNNN", catalog="TIC")` returns these parameters

**Maturity**: Production-ready. TLS has supported these parameters since v1.0. astroquery is a mature astropy-affiliated package.

**Ecosystem**: TLS has 200+ GitHub stars, active maintenance. astroquery is maintained by the astropy project with regular releases.

**Compatibility**: The pipeline already imports astroquery in `parameters.py` for TIC density lookup. Extending this to pass R_star, M_star, and limb darkening to TLS requires minimal code changes.

**Known Limitations**: ~5% of TIC entries have missing or unreliable stellar parameters (especially M-dwarfs). Fallback to solar defaults is needed. TIC uncertainties on R_star can be 10-20% for faint stars.

**Evidence**: TLS documentation states that using catalog stellar parameters yields ~10% higher detection efficiency compared to solar defaults. The TLS FAQ confirms that limb darkening estimates from catalogs should be used for optimal performance.

### 3.2 Known Planet Pre-Masking

**Overview**: Instead of relying on iterative masking (which wastes the first TLS pass re-detecting the strongest known signal), pre-mask all confirmed planet transits and TOI candidates before the search. This makes the first search pass immediately sensitive to new, weaker signals.

**Key Characteristics**:
- The NASA Exoplanet Archive TAP service provides period, epoch (T0), and duration for all confirmed planets and TOI candidates
- Pre-masking sets in-transit cadences to NaN before flattening and search
- Professional pipelines (SPOC DV, QLP) subtract fitted transit models rather than simple masking
- Model subtraction (using batman) preserves more data than NaN masking but requires accurate transit parameters
- Risk of masking real signals from incorrectly cataloged TOIs is low: confirmed planets are reliable; unconfirmed TOIs should be masked with wider windows or flagged

**Maturity**: The approach is standard practice. The NASA TAP API is production-grade. batman for model subtraction is mature (v2.4.9, 400+ GitHub stars).

**Ecosystem**: NASA Exoplanet Archive is the authoritative source. The pipeline already queries it in `crossmatch.py`.

**Compatibility**: High. The pipeline already has iterative masking infrastructure in `bls.py`. Pre-masking is a preprocessing step before the existing search loop.

**Known Limitations**: TOI ephemerides can be imprecise for long-period planets. Using a generous masking window (1.5× duration) mitigates this. For model subtraction, inaccurate parameters produce residual artifacts.

**Evidence**: The context notes that the pipeline "wastes its strongest BLS/TLS detection on re-finding known planets." For TOI-1260 (3 known planets), pre-masking all 3 would let the first TLS pass search for planet 4 directly, rather than requiring 3 iterative passes first.

### 3.3 Centroid / Pixel-Level Vetting

**Overview**: Centroid shift analysis checks whether the photometric center of the target moves during transit, which would indicate the signal originates from a nearby contaminating star rather than the target itself. This is the primary method for identifying nearby eclipsing binary (NEB) false positives.

**Key Characteristics**:
- `tesscentroidvetting` (PyPI, v0.1.0) provides automated centroid analysis for TESS targets using Target Pixel Files (TPFs)
- `vetting` (PyPI, v0.3.1) provides standalone centroid and odd-even tests for Kepler/K2/TESS
- lightkurve can download TPFs and compute centroids via `tpf.estimate_centroids()`
- Difference imaging compares in-transit vs out-of-transit pixel images to localize the transit source
- SPOC Data Validation (DV) reports include centroid offsets; these can be downloaded from MAST

**Maturity**: tesscentroidvetting is relatively new (2024) but functional. lightkurve centroid methods are mature. The vetting package is maintained by the Kepler/TESS team.

**Ecosystem**: lightkurve has 400+ GitHub stars and is the standard TESS data analysis package. tesscentroidvetting is a focused tool with limited but growing adoption.

**Compatibility**: The pipeline already uses lightkurve for data ingestion. Adding TPF download and centroid analysis extends the existing lightkurve dependency.

**Known Limitations**: TESS pixels are 21 arcsec — centroid analysis has limited spatial resolution. For very close contaminants (<1 TESS pixel), centroid shifts may be undetectable. Works best for contaminants 1-3 pixels away.

**Evidence**: The RAVEN pipeline (2025) uses centroid analysis as a primary vetting step and validated 100+ new planets. The LEO-Vetter pipeline (2025) combines flux-level and pixel-level diagnostics for automated TESS vetting.

### 3.4 Statistical Validation (TRICERATOPS)

**Overview**: TRICERATOPS computes the Bayesian false positive probability (FPP) for TESS candidates by modeling multiple astrophysical scenarios (planet transit, eclipsing binary, background EB, nearby EB) and comparing their likelihoods given the observed light curve and nearby star catalog.

**Key Characteristics**:
- pip-installable: `pip install triceratops`
- Requires: light curve, target coordinates, TESS sector, and optionally contrast curves from high-resolution imaging
- Outputs: FPP (false positive probability) and NFPP (nearby false positive probability)
- Validation threshold: FPP < 0.015 and NFPP < 0.001 is the standard for statistical validation
- Automatically queries TIC and Gaia for nearby stars
- Runtime: ~1-5 minutes per candidate

**Maturity**: Stable. Published in 2020, updated through 2024 (PyPI v1.0.6). Used in 50+ published validation papers.

**Ecosystem**: 80+ GitHub stars, active maintenance by Giacalone. TRICERATOPS+ (2025) extends it with multi-band photometry.

**Compatibility**: High. Takes a lightkurve LightCurve object or arrays. Can be called after the existing vetting step as an additional validation layer.

**Known Limitations**: Requires accurate stellar parameters from TIC. Performance degrades in very crowded fields. Does not account for all systematic noise sources. Cannot replace RV confirmation for mass measurement.

**Evidence**: TRICERATOPS was used to validate 12 planets in its original paper and has been used in 50+ subsequent validation papers. The recent RAVEN pipeline (2025) uses TRICERATOPS as its statistical validation backend.

### 3.5 Gaussian Process Detrending

**Overview**: GP detrending models stellar variability as a correlated noise process using a Gaussian Process, which can be jointly fit with a transit model to avoid distorting transit signals during detrending.

**Key Characteristics**:
- celerite2: O(N) GP inference for 1D time series. Supports JAX, PyMC, and numpy backends. pip-installable.
- tinygp: JAX-based GP library, lightweight, used by nuance. pip-installable.
- nuance: Transit detection algorithm that simultaneously searches for transits while modeling correlated noise with GPs. pip-installable. Published 2024.
- SavGol flattening (current approach) can eat shallow transits if the window is too small, or leave residual variability if too large
- GP detrending with a Matérn-3/2 or SHO kernel adapts to the stellar variability timescale automatically

**Maturity**: celerite2 is production-ready (v0.3+, Foreman-Mackey et al.). nuance is newer (2024, v0.6) but published in a peer-reviewed journal.

**Ecosystem**: celerite2 has 200+ GitHub stars, backed by the exoplanet ecosystem. nuance has 30+ stars, actively maintained.

**Compatibility**: Medium. Replacing SavGol with GP detrending requires changes to `preprocess.py`. nuance would replace both detrending and search steps. celerite2 alone replaces only detrending.

**Known Limitations**: GP detrending is 10-100× slower than SavGol per light curve. For batch processing of 3000+ targets, this significantly increases runtime. nuance requires JAX, which adds a heavy dependency. GP hyperparameter selection can be tricky for automated pipelines.

**Evidence**: The exoplanet documentation demonstrates simultaneous GP + transit fitting on Kepler data. nuance's paper shows improved detection of planets around active stars compared to standard BLS after SavGol detrending.

### 3.6 ARIMA + TCF Alternative Search

**Overview**: The Transit Comb Filter (TCF) applied to ARIMA-detrended residuals is an alternative to BLS/TLS that handles autocorrelated noise better, potentially improving small planet detection.

**Key Characteristics**:
- ARIMA modeling removes autocorrelated noise structure before periodogram search
- TCF uses a comb of delta functions matched to transit timing, rather than a box (BLS) or transit model (TLS)
- Gondhalekar & Feigelson (2023) show TCF+ARIMA with SNR metric outperforms BLS when autocorrelated noise is present
- BLS is more sensitive only under limited circumstances with the FAP metric and white Gaussian noise
- The R package `AutoSEARCH` implements ARIMA+TCF; no mature Python implementation exists

**Maturity**: The method is published and validated on TESS data. However, the primary implementation is in R, not Python. A Python port would require implementing both ARIMA fitting (statsmodels) and the TCF periodogram.

**Ecosystem**: Limited Python ecosystem. statsmodels provides ARIMA. The TCF periodogram would need custom implementation (~200-300 lines).

**Compatibility**: Low-medium. Would require a new search module parallel to `tls.py` and `bls.py`. The pipeline's modular design supports this, but it's a significant implementation effort.

**Known Limitations**: TCF assumes evenly-spaced cadence (TESS 2-min data satisfies this). ARIMA model order selection needs automation. The method has not been tested at scale on thousands of targets.

**Evidence**: Gondhalekar & Feigelson (2023, arXiv:2308.04282) demonstrate on simulated and real TESS light curves that ARIMA+TCF with SNR detection metric is preferred when short-memory autocorrelation is present.

### 3.7 Transit Model Fitting (batman)

**Overview**: Fitting a physical limb-darkened transit model (rather than a box) to candidates improves depth, duration, and timing precision. This is needed for credible candidate publication and enables transit timing variation (TTV) analysis.

**Key Characteristics**:
- batman (Kreidberg 2015): Fast transit model computation with C extensions and OpenMP parallelization. Supports quadratic, nonlinear, and custom limb darkening laws. pip-installable.
- PyTransit: Alternative transit model library, supports multiple parameterizations. pip-installable.
- Fitting workflow: generate batman model → optimize with scipy.minimize → optionally run MCMC with emcee for uncertainties
- MCMC fitting with emcee (~1000 walkers, ~5000 steps) takes 1-5 minutes per candidate
- Provides: precise depth (with limb darkening), duration, impact parameter, T0, and their uncertainties
- BLS/TLS box fits systematically underestimate depth for limb-darkened transits

**Maturity**: Production-ready. batman v2.4.9, 400+ GitHub stars, cited in 1000+ papers. emcee is the standard MCMC sampler in astronomy.

**Ecosystem**: batman is maintained by Kreidberg. emcee by Foreman-Mackey. Both are core astropy ecosystem tools.

**Compatibility**: High. Can be added as a post-processing step after TLS candidate detection. Does not require changes to the search pipeline — only adds a refinement stage.

**Known Limitations**: Requires stellar limb darkening coefficients (from TIC Teff/logg). Fitting can converge to local minima for low-SNR candidates. MCMC runtime scales with number of candidates.

**Evidence**: batman is used in virtually every published exoplanet characterization paper. The TLS wrapper in the pipeline already uses BLS for duration refinement — batman would replace this with a physically motivated model.

### 3.8 Nearby Star Contamination (Gaia DR3)

**Overview**: Query Gaia DR3 for all sources within the TESS aperture and compute the expected flux contamination ratio. This identifies targets where a nearby eclipsing binary could mimic a planetary transit.

**Key Characteristics**:
- `astroquery.gaia.Gaia.query_object()` returns all Gaia sources within a specified radius
- TESS pixels are 21 arcsec; typical apertures span 2-4 pixels (42-84 arcsec)
- Compute contamination as: sum(flux_neighbors) / flux_target using Gaia G-band magnitudes converted to TESS-band
- TIC already provides a `contamination_ratio` field, but it may be outdated or use a different aperture
- Gaia DR3 provides positions, magnitudes, and parallaxes for sources down to G~21

**Maturity**: Production-ready. Gaia DR3 is the definitive astrometric catalog. astroquery.gaia is mature.

**Ecosystem**: astroquery is maintained by the astropy project. Gaia DR3 contains 1.8 billion sources.

**Compatibility**: High. Can be added as a pre-check or post-vetting step. The pipeline already uses astroquery.

**Known Limitations**: Gaia completeness drops for very faint sources (G>21) and in crowded fields near the galactic plane. TESS-band flux estimation from Gaia G-band requires a color correction.

**Evidence**: The Gaia-TESS synergy paper (Panahi et al. 2022, A&A) demonstrates that cross-matching TESS candidates with Gaia improves false positive identification. TRICERATOPS internally queries Gaia for its contamination modeling.

### 3.9 Injection-Recovery Tests

**Overview**: Inject synthetic transit signals into real light curves, run the full pipeline, and measure what fraction are recovered as a function of period, depth, and stellar noise level. This produces a detection efficiency map that quantifies pipeline completeness.

**Key Characteristics**:
- Inject transits using batman: generate a model light curve and multiply it into the real flux
- Parameter grid: period (0.5-25 days), depth (50-5000 ppm), duration (1-8 hours)
- For each grid point, inject into 10-50 light curves and measure recovery rate
- Recovery criterion: pipeline detects a candidate within 1% of the injected period
- Computational cost: ~N_grid × N_targets × pipeline_runtime. For a 20×20 grid on 50 targets with 35-min TLS: ~500 hours. Requires parallelization or a reduced grid.
- Kepler's approach: precompute data conditioning, limit search to narrow parameter space around injection

**Maturity**: The methodology is standard (used by Kepler, TESS SPOC, K2). No off-the-shelf Python package exists — it requires custom scripting around the pipeline.

**Ecosystem**: batman for injection, the pipeline itself for recovery. No additional dependencies.

**Compatibility**: High. Injection-recovery is an external test harness that calls the pipeline as a black box. Does not require pipeline code changes.

**Known Limitations**: Computationally expensive. A full grid requires hundreds of pipeline runs. Can be made tractable by: (1) using quicklook preset, (2) testing on a small target subset, (3) parallelizing across CPU cores, (4) using a coarse grid first.

**Evidence**: The Kepler pipeline used injection-recovery to produce detection efficiency maps published in Christiansen et al. (2015, 2016). TESS SPOC uses similar tests. The RAVEN pipeline (2025) includes injection-recovery as part of its validation.

### 3.10 Secondary Eclipse and Phase Curve Analysis

**Overview**: For short-period candidates (P < 3 days), checking for thermal emission at orbital phase 0.5 (secondary eclipse) and ellipsoidal variations can distinguish hot Jupiters from eclipsing binaries.

**Key Characteristics**:
- Secondary eclipse depth > 0.1× primary depth strongly suggests an eclipsing binary
- Ellipsoidal variations (sinusoidal at half the orbital period) indicate a massive companion distorting the star
- The pipeline already checks secondary eclipse in `vetting.py` with a 0.3 threshold
- Phase curve analysis (binning flux by orbital phase) can reveal reflection, thermal emission, and Doppler beaming
- For TESS precision, secondary eclipses are detectable for hot Jupiters (depth > 100 ppm) but not for small planets

**Maturity**: The checks are straightforward signal processing. The pipeline already implements the basic version.

**Ecosystem**: No additional dependencies needed. lightkurve and numpy suffice.

**Compatibility**: The pipeline already has `_secondary_eclipse_check` in `vetting.py`. Enhancement would add ellipsoidal variation and Doppler beaming checks.

**Known Limitations**: TESS photometric precision limits detection to large secondary eclipses. For small planets, secondary eclipse is undetectable. The existing implementation is adequate for EB rejection.

**Evidence**: The existing vetting module already implements this check. The improvement would be incremental — adding ellipsoidal variation detection for P < 2 day candidates.

### 3.11 Multi-Sector Systematics Modeling (CBVs)

**Overview**: TESS Cotrending Basis Vectors (CBVs) capture common systematic trends across targets on the same CCD. Applying CBV correction before or instead of SavGol flattening can remove instrumental systematics while preserving astrophysical signals.

**Key Characteristics**:
- lightkurve provides `lk.correctors.CBVCorrector` for applying CBVs to TESS light curves
- CBVs are available for each TESS sector, camera, and CCD
- PDCSAP flux (which the pipeline already uses) has CBV correction applied by SPOC
- Additional CBV correction on top of PDCSAP is generally not needed and can over-correct
- For SAP flux, CBV correction is essential
- Joint multi-sector modeling (fitting a single noise model across sectors) is not supported by lightkurve out of the box

**Maturity**: Production-ready. CBVs are a standard TESS data product. lightkurve CBV support is mature.

**Ecosystem**: lightkurve (already a dependency). No additional packages needed.

**Compatibility**: High if using SAP flux. Low marginal benefit if already using PDCSAP flux.

**Known Limitations**: Since the pipeline uses PDCSAP flux (pre-corrected by SPOC), additional CBV correction provides minimal benefit. The main improvement would come from switching to SAP flux + custom CBV correction, which is a larger architectural change.

**Evidence**: lightkurve documentation demonstrates CBV correction. SPOC PDCSAP flux already incorporates CBV correction, so the marginal gain for this pipeline is small.

### 3.12 Machine Learning Vetting

**Overview**: Replace or augment hand-crafted vetting rules with a trained neural network classifier that evaluates phase-folded light curves and centroid data to classify candidates as planet or false positive.

**Key Characteristics**:
- Astronet-Triage: CNN trained on Kepler data, adapted for TESS. Classifies phase-folded light curves.
- WATSON-Net: achieves 0.93 precision and 0.76 recall on TESS confirmed planets vs false positives
- RAVEN (2025): uses a random forest classifier on extracted features (depth, duration, SNR, centroid offset)
- Training data: TESS confirmed planets (~400) and false positives (~2000) from ExoFOP dispositions
- Pre-trained models exist but may need fine-tuning for the pipeline's specific preprocessing

**Maturity**: Emerging. Multiple published models exist, but no single standard tool. Most require TensorFlow or PyTorch.

**Ecosystem**: Astronet-Triage (TensorFlow, Google), various research implementations. No single pip-installable "TESS vetting classifier" package.

**Compatibility**: Medium. Would require adding TensorFlow/PyTorch as a dependency (heavy). Could be implemented as an optional post-vetting step. The pipeline's existing vetting produces features that could feed a simpler sklearn classifier.

**Known Limitations**: Pre-trained models may not generalize well to the pipeline's specific preprocessing. Training a custom model requires labeled data. Heavy dependencies (TensorFlow ~500MB). The pipeline's hand-crafted rules are interpretable; ML classifiers are not.

**Evidence**: RAVEN (2025) validated 100+ planets using ML vetting. However, the hand-crafted rules in the pipeline already cover the main false positive scenarios (odd-even, secondary eclipse, alias/harmonic, depth consistency).

## 4. Comparison Matrix

| Criterion | Stellar Params | Pre-Masking | Centroid Vetting | TRICERATOPS | GP Detrending | ARIMA+TCF | batman Fitting | Gaia Contamination | Injection-Recovery | Phase Curves | CBV Correction | ML Vetting |
|-----------|---------------|-------------|-----------------|-------------|---------------|-----------|----------------|-------------------|-------------------|-------------|----------------|------------|
| Detection Sensitivity | +10% for non-solar hosts | High (first pass finds new signals) | None (vetting only) | None (validation only) | +5-20% for active stars | +10-15% for autocorrelated noise | None (characterization) | None (vetting only) | None (measures existing) | None (vetting only) | Minimal over PDCSAP | None (vetting only) |
| False Positive Reduction | Low | Low | High | High | Low | Low | Medium (better params) | Medium | None | Medium | Low | Medium-High |
| Implementation Effort | S (20-40 lines) | S-M (50-100 lines) | M (100-200 lines) | S (30-50 lines) | L (200-400 lines) | L (300-500 lines) | M (150-250 lines) | S (40-80 lines) | M (200-300 lines) | S (30-50 lines) | S (20-40 lines) | L (500+ lines) |
| Runtime Cost | +5 sec/target (TIC query) | -30 min/target (fewer iterations) | +2-5 min/target (TPF download) | +1-5 min/candidate | +10-100× detrending time | +5-10 min/target | +1-5 min/candidate | +5 sec/target | 100-500× pipeline cost | Negligible | Negligible | +1-2 sec/candidate |
| New Dependencies | None (astroquery exists) | None (batman optional) | tesscentroidvetting | triceratops | celerite2 or tinygp+JAX | statsmodels (+ custom TCF) | batman, emcee | None (astroquery exists) | batman (for injection) | None | None | sklearn or TF/PyTorch |
| Pip-Installable | Yes | Yes | Yes | Yes | Yes | Partially (custom TCF) | Yes | Yes | Yes | Yes | Yes | Yes |
| Maturity | Production | Standard practice | Emerging (2024) | Stable (2020+) | Production (celerite2) | Published, no Python pkg | Production | Production | Standard methodology | Already implemented | Already applied (PDCSAP) | Emerging |

## 5. Trade-offs

#### Stellar Parameter Integration
- **Advantages**: Minimal code change, immediate ~10% sensitivity gain for non-solar hosts, uses existing astroquery dependency, improves transit duration grid accuracy
- **Disadvantages**: ~5% of targets have missing TIC parameters requiring fallback, adds network query latency (5 sec/target), TIC uncertainties propagate to TLS
- **Best Suited For**: Pipelines processing diverse stellar types (M-dwarfs, subgiants)
- **Worst Suited For**: Solar-type-only target lists where defaults are adequate

#### Known Planet Pre-Masking
- **Advantages**: First search pass immediately sensitive to new signals, reduces total iterations needed (saves ~30 min/target for multi-planet systems), leverages existing NASA TAP infrastructure
- **Disadvantages**: Requires accurate ephemerides (imprecise for long-period planets), risk of masking incorrectly cataloged TOIs, adds preprocessing complexity
- **Best Suited For**: Searching for additional planets in known systems (the pipeline's primary use case)
- **Worst Suited For**: Blind surveys of stars with no known planets

#### Centroid / Pixel-Level Vetting
- **Advantages**: Directly identifies NEB false positives (the dominant false positive source for TESS), uses existing lightkurve dependency, standard practice in professional pipelines
- **Disadvantages**: TESS 21-arcsec pixels limit spatial resolution, requires TPF download (additional data), adds 2-5 min per target
- **Best Suited For**: Targets in moderately crowded fields with nearby bright stars
- **Worst Suited For**: Isolated targets with no nearby contaminants

#### TRICERATOPS Statistical Validation
- **Advantages**: Provides quantitative FPP for publication, standard tool used in 50+ papers, pip-installable, 1-5 min per candidate
- **Disadvantages**: Requires accurate stellar parameters, degrades in very crowded fields, cannot replace RV mass measurement
- **Best Suited For**: Candidates that pass vetting and need publication-grade validation
- **Worst Suited For**: Early-stage bulk screening (too slow for thousands of candidates)

#### GP Detrending (celerite2 / nuance)
- **Advantages**: Adapts to stellar variability timescale, protects transit signals during detrending, nuance simultaneously searches and detrends
- **Disadvantages**: 10-100× slower than SavGol, adds heavy dependencies (JAX for nuance), GP hyperparameter tuning is non-trivial for automation
- **Best Suited For**: Active/variable stars where SavGol distorts transits
- **Worst Suited For**: Quiet stars where SavGol is adequate, batch processing of thousands of targets

#### ARIMA + TCF
- **Advantages**: Better handles autocorrelated noise than BLS, published evidence of improved small planet detection
- **Disadvantages**: No mature Python implementation, requires custom TCF code, ARIMA order selection needs automation, untested at scale
- **Best Suited For**: Targets with significant autocorrelated noise after detrending
- **Worst Suited For**: Targets with clean white noise (where TLS already excels)

#### batman Transit Model Fitting
- **Advantages**: Physically motivated depth/duration/timing, enables TTV analysis, provides uncertainty estimates via MCMC, needed for credible publication
- **Disadvantages**: 1-5 min per candidate, requires limb darkening coefficients, MCMC can converge to local minima
- **Best Suited For**: Candidates that pass vetting and need precise parameters for publication
- **Worst Suited For**: Bulk screening where BLS/TLS box fits suffice

#### Gaia DR3 Contamination Check
- **Advantages**: Independent contamination estimate using the best available astrometric catalog, complements TIC contamination ratio, minimal code
- **Disadvantages**: Gaia completeness drops for faint sources, TESS-band flux estimation requires color correction, partially redundant with TRICERATOPS (which queries Gaia internally)
- **Best Suited For**: Pre-screening targets before detailed analysis
- **Worst Suited For**: Targets already validated by TRICERATOPS (which includes Gaia contamination)

#### Injection-Recovery Tests
- **Advantages**: Quantifies pipeline completeness, enables occurrence rate calculations, identifies sensitivity gaps, standard methodology
- **Disadvantages**: Extremely computationally expensive (100-500× pipeline cost), requires custom test harness, results are specific to pipeline version
- **Best Suited For**: Pipeline validation before publishing results, occurrence rate studies
- **Worst Suited For**: Rapid development iteration (too slow for frequent testing)

## 6. Risks and Limitations

### Research Risks

- **Risk**: Stellar parameter integration may not improve detection for the pipeline's current target list (mostly solar-type TOI hosts)
  - **Likelihood**: Low
  - **Impact**: Wasted implementation effort (small — only 20-40 lines)
  - **Mitigation**: Test on 10 non-solar targets before full rollout

- **Risk**: GP detrending runtime makes batch processing of 3000+ targets impractical
  - **Likelihood**: High
  - **Impact**: Cannot use GP detrending in batch mode without significant runtime increase
  - **Mitigation**: Use GP detrending only for targets flagged as variable, keep SavGol as default

- **Risk**: ARIMA+TCF custom implementation introduces bugs that reduce rather than improve sensitivity
  - **Likelihood**: Medium
  - **Impact**: False negatives from buggy implementation
  - **Mitigation**: Validate against published results on known TESS planets before deployment

- **Risk**: Pre-masking with inaccurate TOI ephemerides masks real transit data
  - **Likelihood**: Low
  - **Impact**: Reduced sensitivity for specific targets
  - **Mitigation**: Use 1.5× duration masking window, flag targets where masking removes >5% of data

### Research Limitations

- Runtime estimates for GP detrending and ARIMA+TCF are based on published benchmarks, not tested on this specific pipeline
- ML vetting accuracy claims are from papers using different preprocessing — actual performance on Exohunt data is unknown
- The comparison between nuance and TLS+SavGol has not been published on identical TESS datasets
- Injection-recovery cost estimates assume sequential processing; parallelization could reduce wall-clock time significantly

## 7. Recommendations

### Prioritized Improvement List (ordered by impact-to-effort ratio)

**P0 — Critical for pipeline credibility (Minimum Viable Subset: 6/10 → 8/10)**

1. **Stellar Parameter Integration** (Effort: S, Impact: High)
   - **Recommendation**: Query TIC for R_star, M_star, Teff, logg, [Fe/H] via astroquery and pass to TLS. Fall back to solar defaults when parameters are missing.
   - **Justification**: Findings 3.1 — TLS documentation confirms ~10% detection efficiency improvement. The pipeline already has astroquery. This is the highest impact-to-effort improvement.
   - **Confidence Level**: High
   - **Validation Step**: Run on TOI-1260 and compare SDE values with and without stellar parameters.

2. **Known Planet Pre-Masking** (Effort: S-M, Impact: High)
   - **Recommendation**: Query NASA Exoplanet Archive for confirmed planets and TOI candidates. Mask their transits (set to NaN with 1.5× duration window) before flattening and search. Optionally subtract a batman model instead of NaN masking.
   - **Justification**: Findings 3.2 — eliminates wasted iterations, makes first search pass immediately sensitive to new signals. The pipeline's primary use case is finding additional planets in known systems.
   - **Confidence Level**: High
   - **Validation Step**: Run on TOI-1260 with pre-masking of 3 known planets. The first TLS pass should find a new signal (if any) rather than re-detecting planet b.

3. **Centroid / Pixel-Level Vetting** (Effort: M, Impact: High)
   - **Recommendation**: Add centroid shift analysis using lightkurve TPF centroids or tesscentroidvetting. Flag candidates where the centroid shifts significantly during transit.
   - **Justification**: Findings 3.3 — NEB contamination is the dominant false positive source for TESS. No current check addresses this. Professional pipelines (SPOC, RAVEN) all include centroid analysis.
   - **Confidence Level**: High
   - **Validation Step**: Run on 10 known false positives from ExoFOP and verify centroid analysis flags them.

4. **TRICERATOPS Statistical Validation** (Effort: S, Impact: High)
   - **Recommendation**: Add TRICERATOPS FPP computation as a post-vetting step for candidates that pass all existing checks. Report FPP and NFPP in candidate output.
   - **Justification**: Findings 3.4 — provides publication-grade false positive probability. Standard tool used in 50+ papers. pip-installable, 1-5 min per candidate.
   - **Confidence Level**: High
   - **Validation Step**: Run on 5 confirmed planets and 5 known false positives. Confirmed planets should have FPP < 0.015.

**P1 — Strongly recommended (8/10 → 9/10)**

5. **batman Transit Model Fitting** (Effort: M, Impact: Medium-High)
   - **Recommendation**: After TLS detection and vetting, fit a batman limb-darkened transit model to each passing candidate. Use scipy.optimize for point estimates, optionally emcee for MCMC uncertainties.
   - **Justification**: Findings 3.7 — needed for credible publication, improves depth/duration accuracy, enables TTV analysis.
   - **Confidence Level**: High
   - **Validation Step**: Fit batman model to TOI-1260 b and compare depth/duration to published values.

6. **Gaia DR3 Contamination Check** (Effort: S, Impact: Medium)
   - **Recommendation**: Query Gaia DR3 for sources within 2 arcmin of target. Compute flux contamination ratio. Flag targets with >10% contamination for manual review.
   - **Justification**: Findings 3.8 — independent contamination estimate, complements centroid analysis. Partially redundant with TRICERATOPS but useful as a quick pre-screen.
   - **Confidence Level**: Medium (partially redundant with TRICERATOPS)
   - **Validation Step**: Compare Gaia-derived contamination with TIC contamination_ratio for 20 targets.

7. **Injection-Recovery Framework** (Effort: M, Impact: Medium-High)
   - **Recommendation**: Build a test harness that injects batman transits into real light curves and measures recovery rate on a coarse period-depth grid. Start with 10×10 grid on 20 targets using quicklook preset.
   - **Justification**: Findings 3.9 — quantifies pipeline completeness, required for occurrence rate studies, identifies sensitivity gaps.
   - **Confidence Level**: High
   - **Validation Step**: Verify that injected transits matching known planet parameters are recovered at >90% rate.

8. **Secondary Eclipse Enhancement** (Effort: S, Impact: Low-Medium)
   - **Recommendation**: Add ellipsoidal variation and Doppler beaming checks for P < 2 day candidates. The existing secondary eclipse check is adequate; this is incremental.
   - **Justification**: Findings 3.10 — the existing implementation covers the main case. Enhancement catches edge cases.
   - **Confidence Level**: Medium
   - **Validation Step**: Test on known short-period EBs from ExoFOP.

**P2 — Nice to have (9/10 → 10/10)**

9. **GP Detrending (celerite2)** (Effort: L, Impact: Medium)
   - **Recommendation**: Implement GP detrending as an optional alternative to SavGol, activated by config flag or when stellar variability exceeds a threshold. Use celerite2 with a Matérn-3/2 kernel.
   - **Justification**: Findings 3.5 — improves sensitivity for active/variable stars. Too slow for default batch processing.
   - **Confidence Level**: Medium (runtime concern for batch mode)
   - **Validation Step**: Compare detection on 10 known variable star hosts with SavGol vs GP detrending.

10. **ARIMA + TCF Search** (Effort: L, Impact: Medium)
    - **Recommendation**: Implement TCF periodogram in Python as an alternative search method. Use statsmodels ARIMA for detrending. Activate when autocorrelation is detected in TLS residuals.
    - **Justification**: Findings 3.6 — published evidence of improved small planet detection. No Python implementation exists, requiring custom code.
    - **Confidence Level**: Low (no Python implementation to validate against)
    - **Validation Step**: Reproduce Gondhalekar & Feigelson results on their published test cases before deploying.

11. **ML Vetting Classifier** (Effort: L, Impact: Medium)
    - **Recommendation**: Train a random forest classifier on extracted features (depth, duration, SNR, centroid offset, odd-even ratio, secondary eclipse depth) using ExoFOP dispositions as labels. Use sklearn to avoid heavy TF/PyTorch dependencies.
    - **Justification**: Findings 3.12 — could improve vetting accuracy, but the existing hand-crafted rules already cover main false positive scenarios.
    - **Confidence Level**: Low (requires labeled training data, unknown generalization)
    - **Validation Step**: Cross-validate on ExoFOP dispositions, compare precision/recall to existing rule-based vetting.

12. **Multi-Sector CBV Correction** (Effort: S, Impact: Low)
    - **Recommendation**: Skip. The pipeline uses PDCSAP flux which already has CBV correction applied by SPOC. Additional CBV correction provides minimal benefit.
    - **Justification**: Findings 3.11 — marginal gain over PDCSAP. Only useful if switching to SAP flux, which is a larger architectural change not justified by the expected improvement.
    - **Confidence Level**: High (that this is low priority)
    - **Validation Step**: N/A — not recommended for implementation.
