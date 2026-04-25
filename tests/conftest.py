from exohunt.config import (
    RuntimeConfig, IOConfig, IngestConfig, PreprocessConfig,
    PlotConfig, BLSConfig, VettingConfig, ParameterConfig,
)


def _test_config(**overrides) -> RuntimeConfig:
    """Build a RuntimeConfig for tests with minimal defaults."""
    return RuntimeConfig(
        schema_version=1,
        preset=None,
        io=IOConfig(refresh_cache=overrides.get("refresh_cache", False)),
        ingest=IngestConfig(authors=("SPOC",)),
        preprocess=PreprocessConfig(
            enabled=overrides.get("preprocess_enabled", True),
            mode=overrides.get("preprocess_mode", "stitched"),
            outlier_sigma=5.0,
            flatten_window_length=401,
            flatten=True,
            iterative_flatten=False,
            transit_mask_padding_factor=1.5,
        ),
        plot=PlotConfig(
            enabled=overrides.get("plot_enabled", True),
            mode=overrides.get("plot_mode", "stitched"),
            interactive_html=False,
            interactive_max_points=200_000,
            smoothing_window=5,
        ),
        bls=BLSConfig(
            enabled=overrides.get("run_bls", True),
            mode=overrides.get("bls_mode", "stitched"),
            search_method="bls",
            period_min_days=0.5, period_max_days=20.0,
            duration_min_hours=0.5, duration_max_hours=10.0,
            n_periods=2000, n_durations=12, top_n=5,
            min_snr=7.0, compute_fap=False, fap_iterations=1000,
            iterative_masking=False,
            unique_period_separation_fraction=0.05,
            iterative_passes=1, subtraction_model="box_mask",
            iterative_top_n=1, transit_mask_padding_factor=1.5,
        ),
        vetting=VettingConfig(
            min_transit_count=2,
            odd_even_max_mismatch_fraction=0.30,
            alias_tolerance_fraction=0.02,
            secondary_eclipse_max_fraction=0.30,
            depth_consistency_max_fraction=0.50,
        ),
        parameters=ParameterConfig(
            stellar_density_kg_m3=1408.0,
            duration_ratio_min=0.05, duration_ratio_max=1.8,
            apply_limb_darkening_correction=False,
            limb_darkening_u1=0.4, limb_darkening_u2=0.2,
            tic_density_lookup=False,
        ),
    )
