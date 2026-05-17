"""Site-level dashboard aggregation.

Three rollup helpers, each pure over plain dataclasses so the math is
unit-testable without a live database:

  - `compute_summary`     — average / median score, count by band.
  - `compute_trend`       — per-day mean score for the trailing N days.
  - `compute_top_worst`   — top-K lowest-scoring pages.
  - `compute_missing_clusters` — top-K most-frequent missing fan-out types.

The SQL adapters (`fetch_dashboard_data`) are thin wrappers in
`app.api.site` that hand these helpers ORM rows; we keep IO and math
separate so tests don't need Postgres.
"""

from __future__ import annotations

import statistics
from collections import Counter
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Iterable

from app.api.aeo import _band_for


@dataclass(frozen=True)
class PageRow:
    page_id: int
    url: str
    title: str | None
    last_score: int | None
    last_band: str | None
    last_audited_at: datetime | None
    last_missing_types: tuple[str, ...]
    last_failed_checks: tuple[str, ...]


@dataclass(frozen=True)
class AuditRow:
    page_id: int
    score: int
    audited_at: datetime
    missing_types: tuple[str, ...]


@dataclass(frozen=True)
class SiteSummary:
    pages_total: int
    pages_audited: int
    mean_score: float | None
    median_score: float | None
    band_counts: dict[str, int]
    last_audited_at: datetime | None


@dataclass(frozen=True)
class TrendPoint:
    day: date
    mean_score: float
    audits: int


@dataclass(frozen=True)
class WorstPage:
    page_id: int
    url: str
    title: str | None
    score: int
    band: str
    failed_checks: tuple[str, ...]


@dataclass(frozen=True)
class MissingCluster:
    type: str
    pages_missing: int


def compute_summary(rows: Iterable[PageRow]) -> SiteSummary:
    page_list = list(rows)
    audited = [r for r in page_list if r.last_score is not None]
    scores = [r.last_score for r in audited if r.last_score is not None]
    band_counts: Counter[str] = Counter()
    last_audited: datetime | None = None
    for r in audited:
        if r.last_band:
            band_counts[r.last_band] += 1
        if r.last_audited_at is not None and (
            last_audited is None or r.last_audited_at > last_audited
        ):
            last_audited = r.last_audited_at
    mean = statistics.mean(scores) if scores else None
    median = statistics.median(scores) if scores else None
    return SiteSummary(
        pages_total=len(page_list),
        pages_audited=len(audited),
        mean_score=mean,
        median_score=median,
        band_counts=dict(band_counts),
        last_audited_at=last_audited,
    )


def compute_trend(audits: Iterable[AuditRow], *, days: int = 7, today: date | None = None) -> list[TrendPoint]:
    today = today or date.today()
    horizon_start = today - timedelta(days=days - 1)
    buckets: dict[date, list[int]] = {
        horizon_start + timedelta(days=i): [] for i in range(days)
    }
    for a in audits:
        day = a.audited_at.date()
        if day in buckets:
            buckets[day].append(a.score)
    out: list[TrendPoint] = []
    for day in sorted(buckets.keys()):
        scores = buckets[day]
        if scores:
            out.append(
                TrendPoint(day=day, mean_score=statistics.mean(scores), audits=len(scores))
            )
        else:
            out.append(TrendPoint(day=day, mean_score=0.0, audits=0))
    return out


def compute_top_worst(rows: Iterable[PageRow], *, k: int = 10) -> list[WorstPage]:
    audited = [r for r in rows if r.last_score is not None]
    audited.sort(key=lambda r: (r.last_score if r.last_score is not None else 999, r.url))
    out: list[WorstPage] = []
    for r in audited[:k]:
        out.append(
            WorstPage(
                page_id=r.page_id,
                url=r.url,
                title=r.title,
                score=r.last_score or 0,
                band=r.last_band or _band_for(r.last_score or 0),
                failed_checks=r.last_failed_checks,
            )
        )
    return out


def compute_missing_clusters(
    rows: Iterable[PageRow], *, k: int = 10
) -> list[MissingCluster]:
    """Frequency of each missing fan-out type across the site.

    A type that's missing on 12 pages is more actionable than one
    missing on 1, so we rank by count descending.
    """
    counts: Counter[str] = Counter()
    for r in rows:
        for t in r.last_missing_types or ():
            counts[t] += 1
    most = counts.most_common(k)
    return [MissingCluster(type=t, pages_missing=n) for t, n in most]
