"""Unit tests for locale-aware readability metric routing (Phase F)."""

from __future__ import annotations

from app.services.i18n.readability_metrics import (
    HIGHER_IS_SIMPLER,
    LOWER_IS_SIMPLER,
    compute_metric,
    score_for_metric,
)


def test_lower_is_simpler_set_disjoint_from_higher():
    assert HIGHER_IS_SIMPLER.isdisjoint(LOWER_IS_SIMPLER)


def test_compute_metric_handles_empty_input():
    for metric in ("flesch_kincaid", "fernandez_huerta", "lix", "gulpease",
                   "syllable_density", "kandel_moles", "flesch_german"):
        assert compute_metric(metric, "") == 0.0


def test_compute_flesch_kincaid_returns_float():
    body = "This is a basic sentence to compute a Flesch-Kincaid grade level."
    value = compute_metric("flesch_kincaid", body)
    assert isinstance(value, float)
    assert value > 0


def test_compute_fernandez_huerta_returns_float():
    body = "El equipo escribió un texto sencillo para medir la legibilidad en español."
    value = compute_metric("fernandez_huerta", body)
    assert isinstance(value, float)


def test_score_for_metric_inside_band_is_max():
    assert score_for_metric("flesch_kincaid", 8.0, (7.0, 9.0)) == 20
    assert score_for_metric("fernandez_huerta", 65.0, (60.0, 70.0)) == 20
    assert score_for_metric("lix", 38.0, (30.0, 45.0)) == 20


def test_score_for_metric_just_outside_band_drops_to_14():
    # FK band 7-9, value 6.5 → distance 0.5, band width 2 → 0.25 → 14
    assert score_for_metric("flesch_kincaid", 6.5, (7.0, 9.0)) == 14
    # Fernandez 60-70, value 73 → distance 3, band width 10 → 0.3 → 14
    assert score_for_metric("fernandez_huerta", 73.0, (60.0, 70.0)) == 14


def test_score_for_metric_far_outside_band_returns_zero():
    assert score_for_metric("flesch_kincaid", 20.0, (7.0, 9.0)) == 0
    assert score_for_metric("flesch_kincaid", 0.5, (7.0, 9.0)) == 0


def test_score_for_metric_directionless_lower_band():
    # 8 mid; 10.1 → distance 1.1, units 0.55 → 8
    assert score_for_metric("flesch_kincaid", 10.1, (7.0, 9.0)) == 8


def test_syllable_density_devanagari():
    # Crude smoke test — Devanagari vowel marks light up the heuristic
    body = "यह एक छोटा हिंदी वाक्य है"
    value = compute_metric("syllable_density", body)
    assert value > 0
