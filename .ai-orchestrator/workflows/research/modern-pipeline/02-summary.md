---
agent: summary
sequence: 2
references: [research]
summary: "The research chain produced a comprehensive prioritized improvement plan for the Exohunt pipeline. 12 improvements were evaluated; a minimum viable subset of 4 (stellar params, pre-masking, centroid vetting, TRICERATOPS) would move the pipeline from 6/10 to 8/10. Overall chain quality: PASS with average score 8.8."
---

## 1. Executive Summary

The Exohunt pipeline processes TESS light curves to detect transiting exoplanet candidates. It currently has TLS/BLS transit search, iterative masking, candidate vetting, and batch processing, but lacks several capabilities used by professional survey pipelines. The goal of this research workflow was to identify and prioritize specific improvements that would make the pipeline competitive with TESS SPOC, QLP, and DTARPS-S for discovering new exoplanets.

The research agent investigated 12 improvement areas spanning detection sensitivity, false positive rejection, statistical validation, detrending, and pipeline completeness measurement. Each improvement was evaluated for impact, implementation effort, runtime cost, dependency requirements, and compatibility with the existing codebase. The research drew on published papers, tool documentation, and the pipeline's source code.

The outcome is a prioritized list of 12 improvements with clear effort/impact ratings. Four improvements form a "minimum viable subset" that would move the pipeline from 6/10 to 8/10: (1) stellar parameter integration into TLS, (2) known planet pre-masking, (3) centroid/pixel-level vetting, and (4) TRICERATOPS statistical validation. All four are pip-installable, require small-to-medium implementation effort, and are independently testable.

The most significant risk is that GP detrending and ARIMA+TCF — while promising for sensitivity — are computationally expensive and may not be practical for batch processing of thousands of targets. These are recommended as optional/conditional improvements rather than defaults.

The recommended next action is to implement the four P0 improvements sequentially, validating each on the TOI-1260 benchmark system before proceeding to the next.

## 2. Chain Overview

| Step | Agent | Artifact | Critic Verdict | Critic Average Score | Key Finding |
|------|-------|----------|----------------|---------------------|-------------|
| 01 | Researcher | 01-research.md | PASS | 8.8 | 12 improvements evaluated; 4-item minimum viable subset identified for 6/10 → 8/10 |
| 02 | Summarizer | 02-summary.md | — | — | Executive summary and next steps |

## 3. Key Artifacts

### 01-research.md (Sequence 1)
- **Purpose**: Comprehensive research into pipeline improvements for professional-grade exoplanet detection
- **Status**: Complete
- **Key Content**:
  - 12 improvement areas researched with maturity, ecosystem, compatibility, and limitation assessments
  - Comparison matrix across all 12 improvements on 7 evaluation criteria
  - Trade-off analysis for each improvement referencing specific pipeline requirements
  - Prioritized recommendations: 4 P0 (critical), 4 P1 (strongly recommended), 4 P2 (nice to have)
  - Validation steps defined for each recommendation
- **Issues Flagged by Critic**: 2 minor (table readability, runtime estimate precision), 1 suggestion (add documentation URLs)

## 4. Decisions Made

| Decision | Source Artifact | Rationale | Alternatives Rejected | Impact |
|----------|----------------|-----------|----------------------|--------|
| Stellar parameter integration is the #1 priority | 01-research.md | Highest impact-to-effort ratio: ~10% detection improvement for 20-40 lines of code | Treating all stars as solar-type | Improves TLS sensitivity for non-solar hosts |
| Pre-masking known planets before search | 01-research.md | Eliminates wasted iterations re-detecting known signals; saves ~30 min/target | Continuing with iterative-only approach | First search pass finds new signals directly |
| TRICERATOPS over VESPA for statistical validation | 01-research.md | TRICERATOPS is pip-installable, TESS-specific, used in 50+ papers; VESPA is older and harder to install | VESPA (Morton 2012) | Enables publication-grade FPP computation |
| Skip CBV correction (already in PDCSAP) | 01-research.md | Pipeline uses PDCSAP flux which already has CBV correction; additional correction provides minimal benefit | Switching to SAP + custom CBV | Avoids unnecessary complexity |
| GP detrending as optional, not default | 01-research.md | 10-100× slower than SavGol; impractical for batch processing of 3000+ targets | Replacing SavGol with GP for all targets | Preserves batch processing speed |
| sklearn random forest over TensorFlow CNN for ML vetting | 01-research.md | Avoids 500MB TensorFlow dependency; pipeline's extracted features are sufficient for a simpler classifier | Astronet-Triage (TensorFlow) | Lighter dependency, interpretable features |

## 5. Risks and Open Items

| # | Type | Description | Source Artifact | Severity | Recommended Action |
|---|------|-------------|-----------------|----------|-------------------|
| 1 | Risk | GP detrending runtime makes batch processing impractical | 01-research.md | High | Use GP only for targets flagged as variable; keep SavGol as default |
| 2 | Risk | ARIMA+TCF custom Python implementation may introduce bugs | 01-research.md | Medium | Validate against published results before deployment |
| 3 | Risk | Pre-masking with inaccurate TOI ephemerides masks real transit data | 01-research.md | Low | Use 1.5× duration window; flag targets where >5% data masked |
| 4 | Risk | ML vetting classifier may not generalize to pipeline's preprocessing | 01-research.md | Medium | Cross-validate on ExoFOP dispositions before deployment |
| 5 | Open Question | Optimal ARIMA order selection for automated pipeline use | 01-research.md | Medium | Prototype on 20 targets with auto_arima before committing |
| 6 | Open Question | nuance vs celerite2+TLS performance comparison on identical TESS data | 01-research.md | Medium | Run head-to-head benchmark on 10 targets |
| 7 | Assumption | TLS ~10% improvement with stellar params based on documentation claim, not pipeline-specific benchmark | 01-research.md | Low | Validate on TOI-1260 benchmark |
| 8 | Assumption | Injection-recovery cost estimates assume sequential processing | 01-research.md | Low | Parallelization across CPU cores would reduce wall-clock time |

## 6. Quality Assessment

- **Overall Verdict**: Ready with minor caveats. The research artifact is comprehensive and actionable.
- **Score Distribution**: Average 8.8 across the single research artifact. Range: 8 (structure) to 9 (relevance, depth, objectivity, actionability).
- **Strongest Area**: Relevance and actionability — every finding directly addresses a pipeline gap, and recommendations include specific validation steps.
- **Weakest Area**: Structure — the comparison matrix is wide and could benefit from splitting into sub-tables for readability.
- **Rework Needed**: None. The artifact passed the critic with only minor issues and suggestions.

## 7. Next Steps

| Priority | Action | Owner | Depends On | Expected Outcome |
|----------|--------|-------|------------|------------------|
| P0 | Implement stellar parameter integration (TIC → TLS) | Developer | None | ~10% detection improvement for non-solar hosts; ~20-40 lines in tls.py |
| P0 | Implement known planet pre-masking | Developer | Stellar params (for batman model subtraction, optional) | First search pass finds new signals; ~30 min/target saved |
| P0 | Add centroid/pixel-level vetting | Developer | None | NEB false positive detection; new vetting check in pipeline |
| P0 | Integrate TRICERATOPS statistical validation | Developer | Centroid vetting (provides complementary data) | Publication-grade FPP for candidates |
| P1 | Add batman transit model fitting | Developer | Stellar params (for limb darkening) | Precise depth/duration/timing for publication |
| P1 | Add Gaia DR3 contamination check | Developer | None | Independent contamination pre-screen |
| P1 | Build injection-recovery test framework | Developer | All P0 improvements (test the improved pipeline) | Detection efficiency map, completeness quantification |
| P1 | Enhance secondary eclipse checks | Developer | None | Improved EB rejection for short-period candidates |
| P2 | Implement GP detrending (optional mode) | Developer | None | Improved sensitivity for variable stars |
| P2 | Implement ARIMA+TCF search | Developer | None | Alternative search for autocorrelated noise |
| P2 | Train ML vetting classifier | Developer | Injection-recovery (for training data augmentation) | Automated vetting augmentation |
