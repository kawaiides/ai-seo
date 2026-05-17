"""Check G — Freshness signals.

Detects the most recent authoritative date the page advertises and
scores how stale it looks today. Sources checked, in priority order:

  1. JSON-LD `dateModified`
  2. JSON-LD `datePublished`
  3. `<meta property="article:modified_time">` / `<meta name=...>`
  4. `<meta property="article:published_time">` / `<meta name=...>`
  5. `<meta name="date">` / `<meta name="last-modified">`
  6. `<time datetime="...">` elements

We also collect explicit year mentions in the body ("as of 2024",
"in 2018") as a soft signal — surfaced in `details` so a reviewer can
see when the prose has drifted out of date even if metadata says
otherwise. Body years are not allowed to *raise* the score; they only
inform the recommendation.

The `today` argument is injectable so tests don't drift with the wall
clock.
"""

from __future__ import annotations

import json
import re
from datetime import date, datetime
from typing import Any, ClassVar

from app.models.schemas import CheckResultModel
from app.services.aeo_checks.base import BaseCheck
from app.services.content_parser import ParsedContent

# Order = priority. First non-empty hit wins.
META_MODIFIED_KEYS: tuple[str, ...] = (
    "article:modified_time",
    "og:updated_time",
    "last-modified",
    "lastmod",
)
META_PUBLISHED_KEYS: tuple[str, ...] = (
    "article:published_time",
    "og:article:published_time",
    "date",
    "pubdate",
    "publish_date",
    "datepublished",
)

# Capture stand-alone 4-digit years in body prose. Filters: 1990–2099.
YEAR_RE = re.compile(r"\b(19[9]\d|20\d{2})\b")


class FreshnessCheck(BaseCheck):
    check_id: ClassVar[str] = "freshness"
    name: ClassVar[str] = "Freshness Signals"
    max_score: ClassVar[int] = 20

    def __init__(self, today: date | None = None) -> None:
        self._today = today  # resolved at run time so default tracks wall clock

    def run(self, content: ParsedContent) -> CheckResultModel:
        today = self._today or date.today()
        soup = content.soup

        modified_date: date | None = None
        published_date: date | None = None
        source = None  # which signal won

        if soup is not None:
            modified_date, source_m = _find_modified(soup)
            published_date, source_p = _find_published(soup)
            source = source_m or source_p

        effective = modified_date or published_date
        body_years = _extract_body_years(content.body_text or "")
        latest_body_year = max(body_years) if body_years else None
        stalest_body_year = min(body_years) if body_years else None

        if effective is None:
            return self._result(
                score=0,
                details={
                    "date_modified": None,
                    "date_published": None,
                    "source": None,
                    "age_days": None,
                    "body_years": sorted(set(body_years)),
                    "latest_body_year": latest_body_year,
                    "stalest_body_year": stalest_body_year,
                },
                recommendation=(
                    "No `datePublished` or `dateModified` signals found. Add a "
                    "JSON-LD Article block or `article:published_time` / "
                    "`article:modified_time` meta tags so freshness can be "
                    "verified by answer engines."
                ),
            )

        age_days = (today - effective).days
        score = _score_for_age(age_days, has_modified=modified_date is not None)

        details = {
            "date_modified": modified_date.isoformat() if modified_date else None,
            "date_published": published_date.isoformat() if published_date else None,
            "source": source,
            "age_days": age_days,
            "body_years": sorted(set(body_years)),
            "latest_body_year": latest_body_year,
            "stalest_body_year": stalest_body_year,
        }
        return self._result(
            score,
            details,
            _recommendation(
                score, age_days, modified_date is not None, today, stalest_body_year
            ),
        )


def _find_modified(soup: Any) -> tuple[date | None, str | None]:
    d, src = _from_json_ld(soup, "dateModified")
    if d:
        return d, src
    d, src = _from_meta(soup, META_MODIFIED_KEYS)
    if d:
        return d, src
    return None, None


def _find_published(soup: Any) -> tuple[date | None, str | None]:
    d, src = _from_json_ld(soup, "datePublished")
    if d:
        return d, src
    d, src = _from_meta(soup, META_PUBLISHED_KEYS)
    if d:
        return d, src
    d, src = _from_time_tag(soup)
    if d:
        return d, src
    return None, None


def _from_json_ld(soup: Any, key: str) -> tuple[date | None, str | None]:
    for tag in soup.find_all("script", type="application/ld+json"):
        raw = tag.string or tag.get_text() or ""
        raw = raw.strip()
        if not raw:
            continue
        try:
            payload = json.loads(raw)
        except (json.JSONDecodeError, ValueError):
            continue
        for obj in _walk_objects(payload):
            value = obj.get(key)
            if isinstance(value, str):
                parsed = _parse_date(value)
                if parsed is not None:
                    return parsed, f"json_ld.{key}"
    return None, None


def _walk_objects(payload: Any) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    if isinstance(payload, list):
        for item in payload:
            out.extend(_walk_objects(item))
    elif isinstance(payload, dict):
        if "@graph" in payload and isinstance(payload["@graph"], list):
            out.extend(_walk_objects(payload["@graph"]))
        else:
            out.append(payload)
    return out


def _from_meta(soup: Any, keys: tuple[str, ...]) -> tuple[date | None, str | None]:
    for key in keys:
        # Both `name=` and `property=` are valid carriers in the wild.
        for attr in ("property", "name"):
            tag = soup.find("meta", attrs={attr: key})
            if not tag:
                continue
            content_attr = tag.get("content", "")
            if not isinstance(content_attr, str) or not content_attr.strip():
                continue
            parsed = _parse_date(content_attr)
            if parsed is not None:
                return parsed, f"meta.{key}"
    return None, None


def _from_time_tag(soup: Any) -> tuple[date | None, str | None]:
    for time_tag in soup.find_all("time"):
        dt = time_tag.get("datetime", "")
        if not isinstance(dt, str) or not dt.strip():
            continue
        parsed = _parse_date(dt)
        if parsed is not None:
            return parsed, "time_tag"
    return None, None


def _parse_date(raw: str) -> date | None:
    """Parse an ISO-8601 date or date-time. Returns None on failure."""
    raw = raw.strip()
    if not raw:
        return None
    # Normalise trailing Z to +00:00 so fromisoformat accepts it.
    normalised = raw.replace("Z", "+00:00") if raw.endswith("Z") else raw
    try:
        return datetime.fromisoformat(normalised).date()
    except ValueError:
        pass
    # Try date-only forms like "2024-03-15"
    try:
        return date.fromisoformat(raw[:10])
    except ValueError:
        return None


def _extract_body_years(body: str) -> list[int]:
    return [int(m) for m in YEAR_RE.findall(body)]


def _score_for_age(age_days: int, *, has_modified: bool) -> int:
    """Score by age band. Future-dated content (negative age) is treated
    as today — almost always a publishing pipeline writing tomorrow's
    date, not a meaningful AEO win or loss."""
    age = max(age_days, 0)
    if age <= 183:  # ~6 months
        return 20 if has_modified else 14
    if age <= 365:
        return 14 if has_modified else 8
    if age <= 730:
        return 8 if has_modified else 4
    return 2


def _recommendation(
    score: int,
    age_days: int,
    has_modified: bool,
    today: date,
    stalest_body_year: int | None,
) -> str | None:
    if score == 20:
        return None
    months = max(age_days, 0) // 30
    parts: list[str] = []
    if not has_modified:
        parts.append(
            "expose a `dateModified` (JSON-LD or `article:modified_time`) so "
            "answer engines can verify recency"
        )
    if months >= 6:
        parts.append(
            f"content is roughly {months} months old — refresh and bump "
            "`dateModified` if the material still holds"
        )
    if stalest_body_year is not None and (today.year - stalest_body_year) >= 2:
        parts.append(
            f"body still references year {stalest_body_year}; update phrases "
            "like 'as of YYYY' to the current year"
        )
    if not parts:
        return None
    return "Freshness gaps: " + "; ".join(parts) + "."
