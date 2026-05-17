"""Check F — Citation Density.

Counts `<a>` references to authoritative roots (government, academic,
and a curated whitelist of top reference domains) per 1,000 body words.
LLMs preferentially cite content that is itself well-cited; sparse or
zero outbound citations is a strong negative AEO signal.

Authoritative classification is intentionally conservative:

- Anything under the `.gov`, `.mil`, `.edu` suffixes (US) and the common
  `.ac.<cc>` / `.gov.<cc>` patterns (UK, IN, AU, NZ, JP, SG, CA, ZA).
- A small curated whitelist of pan-domain authoritative roots — major
  intergovernmental orgs, primary scientific publishers, reference
  archives. We deliberately do NOT ship a Tranco top-N list inline; that
  belongs in a separate ingest module so the wheel stays small. The
  whitelist below is an MVP and can be swapped for a richer source.
"""

from __future__ import annotations

from typing import Any, ClassVar
from urllib.parse import urlparse

from app.models.schemas import CheckResultModel
from app.services.aeo_checks.base import BaseCheck
from app.services.content_parser import ParsedContent

MIN_WORDS_FOR_SCORE = 100

AUTHORITATIVE_TLD_SUFFIXES: tuple[str, ...] = (
    ".gov",
    ".mil",
    ".edu",
    ".gov.uk",
    ".ac.uk",
    ".gov.in",
    ".ac.in",
    ".gov.au",
    ".edu.au",
    ".gov.nz",
    ".ac.nz",
    ".go.jp",
    ".ac.jp",
    ".gov.sg",
    ".edu.sg",
    ".gc.ca",
    ".gov.za",
    ".ac.za",
    ".europa.eu",
)

AUTHORITATIVE_DOMAINS: frozenset[str] = frozenset(
    {
        "wikipedia.org",
        "wikimedia.org",
        "nature.com",
        "science.org",
        "sciencemag.org",
        "cell.com",
        "thelancet.com",
        "nejm.org",
        "pubmed.ncbi.nlm.nih.gov",
        "arxiv.org",
        "acm.org",
        "ieee.org",
        "springer.com",
        "sciencedirect.com",
        "tandfonline.com",
        "jstor.org",
        "doi.org",
        "crossref.org",
        "orcid.org",
        "who.int",
        "un.org",
        "imf.org",
        "worldbank.org",
        "oecd.org",
        "europa.eu",
        "iso.org",
        "ietf.org",
        "w3.org",
        "rfc-editor.org",
        "loc.gov",
        "archive.org",
        "britannica.com",
        "reuters.com",
        "apnews.com",
        "bbc.com",
        "bbc.co.uk",
        "nytimes.com",
        "washingtonpost.com",
        "ft.com",
        "economist.com",
    }
)


class CitationsCheck(BaseCheck):
    check_id: ClassVar[str] = "citations"
    name: ClassVar[str] = "Citation Density"
    max_score: ClassVar[int] = 20

    def run(self, content: ParsedContent) -> CheckResultModel:
        body = content.body_text or ""
        word_count = len(body.split())

        if word_count < MIN_WORDS_FOR_SCORE:
            return self._result(
                score=0,
                details={
                    "word_count": word_count,
                    "min_words_required": MIN_WORDS_FOR_SCORE,
                    "authoritative_links": [],
                    "authoritative_count": 0,
                    "total_external_links": 0,
                    "authoritative_per_1k": 0.0,
                    "note": "Insufficient text to compute a stable citation density.",
                },
                recommendation=(
                    "Add more substantive content before measuring citation density — "
                    "the per-1k-words metric is unstable on very short text."
                ),
            )

        if content.soup is None:
            return self._result(
                score=0,
                details={
                    "word_count": word_count,
                    "authoritative_links": [],
                    "authoritative_count": 0,
                    "total_external_links": 0,
                    "authoritative_per_1k": 0.0,
                    "note": "Plain-text input — no <a> tags to inspect.",
                },
                recommendation=(
                    "Citation density is measured from HTML links. Submit a URL "
                    "or paste HTML to surface this signal."
                ),
            )

        external, authoritative = _extract_links(content.soup)
        density = (len(authoritative) / word_count) * 1000.0
        score = _score(len(authoritative), density)

        details = {
            "word_count": word_count,
            "total_external_links": len(external),
            "authoritative_count": len(authoritative),
            "authoritative_per_1k": round(density, 2),
            "authoritative_links": [
                {"href": href, "host": host} for href, host in authoritative
            ],
        }
        return self._result(score, details, _recommendation(score, density))


def _extract_links(soup: Any) -> tuple[list[tuple[str, str]], list[tuple[str, str]]]:
    """Return (external, authoritative) lists of (href, host) tuples.

    Internal links (relative URLs, fragments, mailto, tel) are excluded
    from `external` so they can't game the density metric.
    """
    external: list[tuple[str, str]] = []
    authoritative: list[tuple[str, str]] = []
    for a in soup.find_all("a", href=True):
        href = a.get("href", "")
        if not isinstance(href, str):
            continue
        href = href.strip()
        if not href:
            continue
        parsed = urlparse(href)
        if parsed.scheme not in ("http", "https"):
            continue
        host = (parsed.hostname or "").lower()
        if not host:
            continue
        external.append((href, host))
        if _is_authoritative(host):
            authoritative.append((href, host))
    return external, authoritative


def _is_authoritative(host: str) -> bool:
    if host in AUTHORITATIVE_DOMAINS:
        return True
    # Match suffix on a dot boundary so "evilgov" doesn't pass ".gov".
    for suffix in AUTHORITATIVE_TLD_SUFFIXES:
        if host == suffix.lstrip(".") or host.endswith(suffix):
            return True
    # Also accept registrable domain matches against the whitelist
    # (e.g. "en.wikipedia.org" → "wikipedia.org").
    parts = host.split(".")
    for i in range(len(parts) - 1):
        candidate = ".".join(parts[i:])
        if candidate in AUTHORITATIVE_DOMAINS:
            return True
    return False


def _score(authoritative_count: int, density_per_1k: float) -> int:
    """Score by both absolute citation count AND per-1k density.

    The density-only formulation rewards a single citation on a short
    page (1/200 words ≈ 5/1k) the same as a thoroughly-cited long page
    (15/3000 words). Requiring both gates fire keeps short pages from
    over-scoring.
    """
    if authoritative_count == 0:
        return 0
    if authoritative_count >= 3 and density_per_1k >= 3.0:
        return 20
    if authoritative_count >= 2 and density_per_1k >= 1.5:
        return 14
    if authoritative_count >= 1 and density_per_1k >= 0.5:
        return 8
    return 2


def _recommendation(score: int, density: float) -> str | None:
    if score == 20:
        return None
    if score == 0:
        return (
            "No links to authoritative sources detected. Cite primary references "
            "(government statistics, academic papers, Wikipedia, reputable news) "
            "to reach at least 3 authoritative links per 1,000 words."
        )
    return (
        f"Authoritative-citation density is {density:.2f} per 1k words; AEO-strong "
        "content sits at ≥3.0. Add links to .gov/.edu sources, primary research, "
        "or established encyclopedic references."
    )
