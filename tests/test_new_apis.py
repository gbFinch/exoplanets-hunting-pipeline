"""Unit tests for new public APIs added during refactoring."""
from __future__ import annotations

import pytest

from exohunt.cache import content_hash
from exohunt.models import parse_tic_id
from exohunt.vetting import (
    CandidateVettingResult,
    check_known_period_subharmonics,
)
from exohunt.bls import BLSCandidate


# --- parse_tic_id ---

def test_parse_tic_id_standard_format():
    assert parse_tic_id("TIC 261136679") == 261136679


def test_parse_tic_id_no_space():
    assert parse_tic_id("TIC261136679") == 261136679


def test_parse_tic_id_extra_whitespace():
    assert parse_tic_id("  TIC  261136679  ") == 261136679


def test_parse_tic_id_invalid_raises_clear_error():
    with pytest.raises(ValueError, match="Cannot parse TIC ID"):
        parse_tic_id("not a tic")


def test_parse_tic_id_empty_raises():
    with pytest.raises(ValueError, match="Cannot parse TIC ID"):
        parse_tic_id("")


# --- content_hash ---

def test_content_hash_deterministic():
    payload = {"key": "value", "n": 42}
    assert content_hash(payload) == content_hash(payload)


def test_content_hash_key_order_independent():
    assert content_hash({"a": 1, "b": 2}) == content_hash({"b": 2, "a": 1})


def test_content_hash_default_length_is_16():
    assert len(content_hash({"x": 1})) == 16


def test_content_hash_custom_length():
    assert len(content_hash({"x": 1}, length=12)) == 12


def test_content_hash_different_payloads_differ():
    assert content_hash({"a": 1}) != content_hash({"a": 2})


# --- check_known_period_subharmonics ---

def _candidate(rank=1, period_days=5.0):
    return BLSCandidate(
        rank=rank, period_days=period_days, duration_hours=2.0,
        depth=0.001, depth_ppm=1000.0, power=10.0,
        transit_time=0.0, transit_count_estimate=6.0,
        snr=10.0,
    )


def _passing_vetting(rank=1, reasons="pass"):
    return CandidateVettingResult(
        pass_min_transit_count=True, pass_odd_even_depth=True,
        pass_alias_harmonic=True, vetting_pass=True,
        transit_count_observed=10, odd_depth_ppm=1000.0,
        even_depth_ppm=1000.0, odd_even_depth_mismatch_fraction=0.0,
        alias_harmonic_with_rank=-1, vetting_reasons=reasons,
        odd_even_status="pass",
    )


def test_subharmonics_flags_half_period():
    cand = _candidate(period_days=5.0)
    vetting = {1: _passing_vetting()}
    result = check_known_period_subharmonics([cand], vetting, [10.0])
    assert result[1].vetting_pass is False
    assert "subharmonic" in result[1].vetting_reasons


def test_subharmonics_ignores_unrelated_period():
    cand = _candidate(period_days=7.0)
    vetting = {1: _passing_vetting()}
    result = check_known_period_subharmonics([cand], vetting, [10.0])
    assert result[1].vetting_pass is True


def test_subharmonics_skips_already_failing():
    cand = _candidate(period_days=5.0)
    vr = CandidateVettingResult(
        pass_min_transit_count=False, pass_odd_even_depth=True,
        pass_alias_harmonic=True, vetting_pass=False,
        transit_count_observed=1, odd_depth_ppm=1000.0,
        even_depth_ppm=1000.0, odd_even_depth_mismatch_fraction=0.0,
        alias_harmonic_with_rank=-1, vetting_reasons="min_transit_count<2",
        odd_even_status="pass",
    )
    result = check_known_period_subharmonics([cand], {1: vr}, [10.0])
    # Already failing, should not be modified
    assert result[1] is vr or result[1].vetting_reasons == vr.vetting_reasons


def test_subharmonics_empty_known_periods_no_change():
    cand = _candidate(period_days=5.0)
    vetting = {1: _passing_vetting()}
    result = check_known_period_subharmonics([cand], vetting, [])
    assert result[1].vetting_pass is True


def test_subharmonics_does_not_corrupt_reasons_with_pass_substring():
    """Regression: vetting_reasons containing 'pass' as substring must not be mangled."""
    cand = _candidate(period_days=5.0)
    # Reason contains 'pass' as substring but not as a standalone token
    vr = _passing_vetting(reasons="pass;odd_even_inconclusive")
    result = check_known_period_subharmonics([cand], {1: vr}, [10.0])
    # The original 'pass' token is filtered out, but 'odd_even_inconclusive' is preserved
    assert "odd_even_inconclusive" in result[1].vetting_reasons
    assert "subharmonic" in result[1].vetting_reasons
