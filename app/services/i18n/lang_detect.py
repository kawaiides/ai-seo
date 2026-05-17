"""Lightweight language detection.

The roadmap calls out `langdetect`, but pulling it in adds a 5 MB
profile dataset for what we use it for here. Instead we ship a
script-range + stop-word heuristic that's accurate enough to *route*
text into the correct pipeline for the languages we actively support.

If `langdetect` is installed in a future deployment, swap in a
`LangDetectDetector` that wraps it — the `Detector` Protocol below is
the seam.
"""

from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass
from typing import Protocol

from app.services.i18n.locale import SUPPORTED_LOCALES

# Unicode script ranges. We check these before stop-word heuristics
# because Indic / CJK scripts are essentially unambiguous compared to
# Latin-script European languages that share letters but differ in
# function-word frequency.
SCRIPT_RANGES: dict[str, tuple[int, int]] = {
    "hi": (0x0900, 0x097F),   # Devanagari (Hindi)
    "bn": (0x0980, 0x09FF),   # Bengali
    "ta": (0x0B80, 0x0BFF),   # Tamil
    "te": (0x0C00, 0x0C7F),   # Telugu
    "kn": (0x0C80, 0x0CFF),   # Kannada
    "ml": (0x0D00, 0x0D7F),   # Malayalam
}

# Per-language stop-word sets curated for high frequency + low
# overlap. Lower-case, accent-stripped — match against tokens
# normalised the same way.
STOP_WORDS: dict[str, frozenset[str]] = {
    "en": frozenset({"the", "and", "of", "to", "a", "in", "is", "that", "for", "it", "with", "as", "on", "by", "this"}),
    "es": frozenset({"el", "la", "de", "que", "y", "en", "un", "una", "los", "las", "es", "por", "con", "para", "se"}),
    "fr": frozenset({"le", "la", "les", "de", "et", "un", "une", "des", "que", "est", "en", "dans", "pour", "qui", "avec"}),
    "de": frozenset({"der", "die", "das", "und", "ist", "ein", "eine", "den", "zu", "von", "mit", "fur", "auf", "im", "auch"}),
    "it": frozenset({"il", "la", "di", "e", "un", "una", "che", "in", "per", "con", "non", "sono", "del", "della", "si"}),
    "pt": frozenset({"o", "a", "de", "que", "e", "um", "uma", "para", "com", "do", "da", "em", "no", "na", "os"}),
    "nl": frozenset({"de", "het", "een", "en", "van", "in", "is", "dat", "op", "te", "voor", "met", "zijn", "niet", "aan"}),
    "sv": frozenset({"och", "att", "det", "som", "en", "pa", "ar", "av", "for", "med", "den", "till", "har", "inte", "om"}),
    "da": frozenset({"og", "at", "det", "som", "en", "pa", "er", "af", "for", "med", "den", "til", "har", "ikke", "om"}),
    "no": frozenset({"og", "at", "det", "som", "en", "pa", "er", "av", "for", "med", "den", "til", "har", "ikke", "om"}),
}

_TOKEN_RE = re.compile(r"[A-Za-zÀ-ÖØ-öø-ÿ]+")


@dataclass(frozen=True)
class DetectionResult:
    code: str
    confidence: float  # 0..1
    method: str  # "script" | "stopwords" | "default"


class Detector(Protocol):
    def detect(self, text: str) -> DetectionResult: ...


class HeuristicDetector:
    """Default detector. Cheap, deterministic, no I/O."""

    def detect(self, text: str) -> DetectionResult:
        text = (text or "").strip()
        if not text:
            return DetectionResult(code="en", confidence=0.0, method="default")

        script_hit = _script_match(text)
        if script_hit is not None:
            return script_hit

        return _stopword_match(text)


def _script_match(text: str) -> DetectionResult | None:
    counts: Counter[str] = Counter()
    for ch in text:
        cp = ord(ch)
        for code, (lo, hi) in SCRIPT_RANGES.items():
            if lo <= cp <= hi:
                counts[code] += 1
                break
    if not counts:
        return None
    code, hits = counts.most_common(1)[0]
    # Total non-whitespace chars for a normalisation denominator.
    visible = sum(1 for c in text if not c.isspace())
    if visible <= 0:
        return None
    confidence = min(1.0, hits / max(visible // 2, 1))
    if confidence < 0.15:
        return None
    return DetectionResult(code=code, confidence=round(confidence, 3), method="script")


def _stopword_match(text: str) -> DetectionResult:
    tokens = _normalise_tokens(text)
    if not tokens:
        return DetectionResult(code="en", confidence=0.0, method="default")

    scores: dict[str, int] = {}
    for code, words in STOP_WORDS.items():
        scores[code] = sum(1 for t in tokens if t in words)
    code, hits = max(scores.items(), key=lambda kv: kv[1])
    if hits == 0:
        return DetectionResult(code="en", confidence=0.0, method="default")
    confidence = min(1.0, hits / max(len(tokens) // 3, 1))
    return DetectionResult(code=code, confidence=round(confidence, 3), method="stopwords")


def _normalise_tokens(text: str) -> list[str]:
    out: list[str] = []
    for m in _TOKEN_RE.finditer(text.lower()):
        word = m.group(0)
        word = _strip_accents(word)
        if word:
            out.append(word)
    return out


# 1:1 mapping for single-char replacements. œ/æ/ß need >1-char output, so
# they're handled by `_LIGATURES` instead of str.maketrans (which requires
# equal-length strings).
_ACCENT_MAP = str.maketrans(
    "áàäâãāéèëêēíìïîīóòöôõōúùüûūýŷÿñç",
    "aaaaaaeeeeeiiiiioooooouuuuuyyync",
)
_LIGATURES = {"œ": "oe", "æ": "ae", "ß": "ss"}


def _strip_accents(s: str) -> str:
    out = s.translate(_ACCENT_MAP)
    for src, dst in _LIGATURES.items():
        out = out.replace(src, dst)
    return out


def detect(text: str, *, detector: Detector | None = None) -> DetectionResult:
    return (detector or HeuristicDetector()).detect(text)


def is_supported(code: str) -> bool:
    return code in SUPPORTED_LOCALES
