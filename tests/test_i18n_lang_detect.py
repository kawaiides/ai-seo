"""Unit tests for heuristic language detector (Phase F)."""

from __future__ import annotations

from app.services.i18n.lang_detect import (
    HeuristicDetector,
    detect,
    is_supported,
)


def _code(text: str) -> str:
    return HeuristicDetector().detect(text).code


def test_english_stopwords_match():
    text = "The team and the engineer agreed that the new model is ready for production."
    result = detect(text)
    assert result.code == "en"
    assert result.method == "stopwords"
    assert result.confidence > 0


def test_spanish_stopwords_match():
    text = "El equipo y el ingeniero acordaron que el nuevo modelo es para producción."
    assert _code(text) == "es"


def test_french_stopwords_match():
    text = "L'équipe et l'ingénieur ont décidé que le nouveau modèle est pour la production."
    assert _code(text) == "fr"


def test_german_stopwords_match():
    text = "Der Ingenieur und das Team haben entschieden dass das neue Modell ist für Produktion bereit."
    assert _code(text) == "de"


def test_devanagari_script_routes_to_hindi():
    text = "यह एक हिंदी पाठ है"
    result = detect(text)
    assert result.code == "hi"
    assert result.method == "script"
    assert result.confidence > 0.15


def test_tamil_script_routes_to_tamil():
    assert _code("இது தமிழ் உரை") == "ta"


def test_bengali_script_routes_to_bengali():
    assert _code("এটি একটি বাংলা পাঠ্য") == "bn"


def test_empty_input_falls_back_to_english():
    result = detect("")
    assert result.code == "en"
    assert result.method == "default"


def test_random_punctuation_falls_back_to_english():
    result = detect("!!! @@@ ###")
    assert result.code == "en"


def test_is_supported_table_includes_indic_and_european():
    for code in ("en", "es", "hi", "ta", "sv"):
        assert is_supported(code)
    assert not is_supported("zz")
