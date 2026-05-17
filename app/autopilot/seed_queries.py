"""Buyer-intent seed queries + weighted round-robin selector.

The autopilot's `prospect` subcommand needs a query to feed SerpAPI.
Previously the operator had to pass `--seed "X"` on every cron tick,
which defeats the "set-and-forget" goal of the blueprint.

This module ships a small curated catalog of buyer-intent search terms
across SaaS verticals and a `pick_next_seed()` selector that:

  * picks the seed with the **oldest** `last_used_at` from
    `seed_query_usage` (so freshly-added seeds are tried first),
  * breaks ties by `weight` so the operator can bias toward verticals
    with higher conversion,
  * stamps `last_used_at = now()` so the next tick rotates away from
    this seed.

The catalog itself lives in code (not the DB) so adding/removing seeds
is a code review, not a manual psql session. Per-seed usage state goes
in the `seed_query_usage` table — keyed on the seed slug so renaming a
catalog entry doesn't reset rotation.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Sequence

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import SeedQueryUsage


@dataclass(frozen=True)
class SeedQuery:
    slug: str           # stable id, used as the usage-table key
    text: str           # the actual SerpAPI query
    vertical: str       # broad category, for analytics
    locale: str         # 'en-US' / 'en-IN' — lets the prospector route geo
    weight: int = 1     # tie-breaker; higher = chosen first when last_used_at ties
    enabled: bool = True


# Ordered roughly by Pro-conversion likelihood we've observed in the
# blueprint scenarios. Mix of US + IN locales so the geo-router has
# both currencies of leads to work with.
SEED_QUERIES: tuple[SeedQuery, ...] = (
    SeedQuery("ai-seo-platform-us", "best AI SEO platform 2026",
             vertical="seo_tools", locale="en-US", weight=5),
    SeedQuery("aeo-tool-us", "best Answer Engine Optimization tool",
             vertical="seo_tools", locale="en-US", weight=5),
    SeedQuery("content-opt-software-us", "best content optimization software for blogs",
             vertical="content_tools", locale="en-US", weight=4),
    SeedQuery("ai-writing-blogs-us", "best AI writing assistant for blogs",
             vertical="content_tools", locale="en-US", weight=4),
    SeedQuery("schema-markup-generator-us", "best schema markup generator for content teams",
             vertical="seo_tools", locale="en-US", weight=3),
    SeedQuery("cybersec-startups-us", "best cybersecurity tools for startups",
             vertical="cybersecurity", locale="en-US", weight=3),
    SeedQuery("agency-crm-us", "best CRM for marketing agencies",
             vertical="agency", locale="en-US", weight=3),
    SeedQuery("perplexity-citation-tracker-us", "tools to track Perplexity citations for a brand",
             vertical="geo_tracking", locale="en-US", weight=3),
    SeedQuery("chatgpt-visibility-us", "how to rank a website in ChatGPT search",
             vertical="geo_tracking", locale="en-US", weight=3),
    SeedQuery("seo-tools-india-in", "best SEO tools for Indian startups",
             vertical="seo_tools", locale="en-IN", weight=4),
    SeedQuery("content-platform-india-in", "best content marketing platform India",
             vertical="content_tools", locale="en-IN", weight=3),
    SeedQuery("d2c-seo-india-in", "best AI SEO for D2C brands in India",
             vertical="d2c", locale="en-IN", weight=3),
    SeedQuery("freelance-writer-tools-in", "AI writing tools for Indian freelance writers",
             vertical="content_tools", locale="en-IN", weight=2),
    SeedQuery("legal-tech-india-in", "best content tools for Indian legal-tech blogs",
             vertical="legal_tech", locale="en-IN", weight=2),
    SeedQuery("comparative-saas-reviews-us", "best alternatives to Surfer SEO",
             vertical="seo_tools", locale="en-US", weight=4),
    SeedQuery("comparative-clearscope-us", "Clearscope vs MarketMuse vs Frase comparison",
             vertical="seo_tools", locale="en-US", weight=3),
    SeedQuery("fintech-content-us", "best content optimization tool for fintech blogs",
             vertical="fintech", locale="en-US", weight=2),
    SeedQuery("dev-tools-saas-us", "best developer-content optimization tool",
             vertical="dev_tools", locale="en-US", weight=2),
)


def by_slug(slug: str) -> SeedQuery | None:
    for s in SEED_QUERIES:
        if s.slug == slug:
            return s
    return None


def enabled_seeds() -> list[SeedQuery]:
    return [s for s in SEED_QUERIES if s.enabled]


def _pick_pure(
    seeds: Sequence[SeedQuery],
    usage: dict[str, datetime],
) -> SeedQuery | None:
    """Pure selector — picks the seed with the oldest usage timestamp;
    breaks ties by `weight` (descending). Seeds with no usage row come
    first regardless of weight so newly-added catalog entries get a
    fair shot."""
    enabled = [s for s in seeds if s.enabled]
    if not enabled:
        return None
    never_used = [s for s in enabled if s.slug not in usage]
    if never_used:
        never_used.sort(key=lambda s: (-s.weight, s.slug))
        return never_used[0]
    enabled.sort(key=lambda s: (usage[s.slug], -s.weight, s.slug))
    return enabled[0]


async def pick_next_seed(session: AsyncSession) -> SeedQuery | None:
    """Pick the next seed to feed to `prospect`. Stamps usage atomically
    so concurrent autopilot ticks don't both pick the same seed."""
    rows = (
        await session.scalars(select(SeedQueryUsage))
    ).all()
    usage = {row.slug: row.last_used_at for row in rows}
    chosen = _pick_pure(SEED_QUERIES, usage)
    if chosen is None:
        return None
    now = datetime.now(tz=timezone.utc)
    stmt = (
        pg_insert(SeedQueryUsage)
        .values(slug=chosen.slug, last_used_at=now, use_count=1)
        .on_conflict_do_update(
            index_elements=["slug"],
            set_={
                "last_used_at": now,
                "use_count": SeedQueryUsage.use_count + 1,
            },
        )
    )
    await session.execute(stmt)
    return chosen
