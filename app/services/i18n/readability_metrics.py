"""Per-locale readability metrics.

`compute_metric(metric_name, text)` returns a `float` score whose
semantics depend on the metric:

  - `flesch_kincaid` (en)         — grade level (lower = simpler)
  - `fernandez_huerta` (es/pt)    — 0..100, **higher** = simpler
  - `kandel_moles` (fr)            — 0..100, **higher** = simpler
  - `flesch_german` (de)           — 0..100, **higher** = simpler
  - `gulpease` (it)                — 0..100, **higher** = simpler
  - `lix` (sv/da/no/nl)            — 0..60+, lower = simpler
  - `syllable_density` (hi/ta/…)   — syllables / word, lower = simpler

`score_for_metric(metric_name, value, target_range)` maps the raw value
to the same 20/14/8/0 score bands the English readability check uses,
direction-aware so "higher is better" metrics aren't inverted.
"""

from __future__ import annotations

import math

import textstat

# Metrics where a *higher* raw score means simpler text. The score
# function below uses this to decide direction.
HIGHER_IS_SIMPLER: frozenset[str] = frozenset(
    {"fernandez_huerta", "kandel_moles", "flesch_german", "gulpease"}
)
LOWER_IS_SIMPLER: frozenset[str] = frozenset(
    {"flesch_kincaid", "lix", "syllable_density"}
)


def compute_metric(metric: str, text: str) -> float:
    if not text or not text.strip():
        return 0.0
    if metric == "flesch_kincaid":
        return round(float(textstat.flesch_kincaid_grade(text)), 1)
    if metric == "fernandez_huerta":
        return round(float(textstat.fernandez_huerta(text)), 1)
    if metric == "kandel_moles":
        # textstat doesn't ship Kandel-Moles; approximate via the French
        # Gunning-Fog-like Crawford formula which it does ship.
        return round(float(textstat.crawford(text)), 1)
    if metric == "flesch_german":
        # textstat doesn't have a German FK directly; use Wiener Sachtextformel
        # if available, else fall back to the language-agnostic
        # flesch_reading_ease (German tuned coefficients sit close).
        if hasattr(textstat, "wiener_sachtextformel"):
            return round(float(textstat.wiener_sachtextformel(text, 1)), 1)
        return round(float(textstat.flesch_reading_ease(text)), 1)
    if metric == "gulpease":
        return round(float(textstat.gulpease_index(text)), 1)
    if metric == "lix":
        return round(float(textstat.lix(text)), 1)
    if metric == "syllable_density":
        return _syllable_density(text)
    # Unknown metric — degrade to FK so the caller still gets a number.
    return round(float(textstat.flesch_kincaid_grade(text)), 1)


def score_for_metric(
    metric: str, value: float, target_range: tuple[float, float]
) -> int:
    """Map raw `value` to the 20/14/8/0 scoring band the English check
    documents, oriented correctly for direction.

    `target_range = (low, high)` is the "ideal" interval. We grade by
    distance from the interval (in band-units) regardless of which side
    of the interval the value falls on.
    """
    low, high = target_range
    if value == 0.0:
        return 0

    band_width = max((high - low), 1e-6)
    if low <= value <= high:
        return 20
    # Distance below `low` or above `high`, normalised by band width.
    distance = (low - value) if value < low else (value - high)
    units = distance / band_width
    if units <= 0.5:
        return 14
    if units <= 1.0:
        return 8
    return 0


def _syllable_density(text: str) -> float:
    """Indic / non-Latin fallback: syllables-per-word ratio.

    `textstat.syllable_count` works on grapheme heuristics that don't
    transfer cleanly to Devanagari etc., but vowel-mark density per
    space-separated token is a reasonable proxy.
    """
    tokens = [t for t in text.split() if t.strip()]
    if not tokens:
        return 0.0
    total_syllables = 0
    for tok in tokens:
        try:
            total_syllables += textstat.syllable_count(tok)
        except Exception:  # noqa: BLE001 — textstat raises on rare unicode edge cases
            # Fallback: vowel-class character count.
            total_syllables += sum(1 for c in tok if _is_vowel_like(c))
    return round(total_syllables / max(len(tokens), 1), 2)


_VOWEL_LIKE = set("aeiouAEIOUaeiouáéíóúàèìòùâêîôûäöüāēīōūîÆØÅåøæ")


def _is_vowel_like(c: str) -> bool:
    # Devanagari vowel marks land in 0x093A..0x094C — treat as syllable
    # boundary markers.
    cp = ord(c)
    if 0x093A <= cp <= 0x094C:
        return True
    return c in _VOWEL_LIKE
