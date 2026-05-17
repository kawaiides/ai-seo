"""Locale-aware Check C tests (Phase F)."""

from __future__ import annotations

import pytest

from app.services.aeo_checks.readability import ReadabilityCheck
from app.services.content_parser import ParsedContent


def _content(body: str) -> ParsedContent:
    return ParsedContent(raw=body, soup=None, first_paragraph=body, h_tags=[], body_text=body)


@pytest.mark.usefixtures("nlp")
def test_english_default_uses_flesch_kincaid():
    body = " ".join(
        [
            "Python is a programming language used for data science and web development.",
            "It is concise and reads well at an eighth-grade level.",
            "Many teams pick Python for its ecosystem and readability.",
        ]
    )
    result = ReadabilityCheck().run(_content(body))
    assert result.details["metric"] == "flesch_kincaid"
    assert result.details["locale"] == "en"


@pytest.mark.usefixtures("nlp")
def test_forced_spanish_uses_fernandez_huerta():
    body = (
        "El equipo escribió un texto en español para evaluar la legibilidad. "
        "Las oraciones son cortas y claras para los lectores."
    )
    result = ReadabilityCheck(locale="es").run(_content(body))
    assert result.details["metric"] == "fernandez_huerta"
    assert result.details["locale"] == "es"


@pytest.mark.usefixtures("nlp")
def test_forced_swedish_uses_lix():
    body = (
        "Teamet skrev en kort svensk text för att utvärdera läsbarheten. "
        "Meningarna är korta och tydliga för läsarna."
    )
    result = ReadabilityCheck(locale="sv").run(_content(body))
    assert result.details["metric"] == "lix"


@pytest.mark.usefixtures("nlp")
def test_forced_italian_uses_gulpease():
    body = (
        "Il team ha scritto un breve testo italiano per valutare la leggibilità. "
        "Le frasi sono corte e chiare per i lettori."
    )
    result = ReadabilityCheck(locale="it").run(_content(body))
    assert result.details["metric"] == "gulpease"


@pytest.mark.usefixtures("nlp")
def test_forced_hindi_uses_syllable_density():
    body = (
        "यह एक छोटा हिंदी पाठ है जिसका उद्देश्य पठनीयता का परीक्षण करना है। "
        "वाक्य सरल और संक्षिप्त हैं ताकि पाठक आसानी से समझ सकें।"
    )
    result = ReadabilityCheck(locale="hi").run(_content(body))
    assert result.details["metric"] == "syllable_density"


@pytest.mark.usefixtures("nlp")
def test_locale_detection_reports_method():
    body = (
        "El equipo escribió un texto en español para evaluar la legibilidad. "
        "Las oraciones son cortas y claras para los lectores."
    )
    result = ReadabilityCheck().run(_content(body))
    assert result.details["locale"] == "es"
    assert result.details["locale_detection"] in {"stopwords", "script"}


@pytest.mark.usefixtures("nlp")
def test_forced_locale_reports_forced_detection_method():
    body = "Plain English content that is detection-ambiguous."
    result = ReadabilityCheck(locale="fr").run(_content(body))
    assert result.details["locale_detection"] == "forced"


@pytest.mark.usefixtures("nlp")
def test_insufficient_text_returns_zero_with_locale_recorded():
    result = ReadabilityCheck(locale="es").run(_content("Hola mundo."))
    assert result.score == 0
    assert result.details["locale"] == "es"
    assert "Insufficient text" in result.details["note"]
