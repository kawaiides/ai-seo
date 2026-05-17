"""Internal-linking suggestion endpoint (Phase B.3)."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException

from app.models.schemas import (
    LinkingSuggestRequest,
    LinkingSuggestResponse,
    PageRecordModel,
)
from app.services.linking.page_index import (
    PageRecord,
    build_page_index_from_records,
    build_page_index_from_urls,
)
from app.services.linking.sitemap_fetcher import fetch_sitemap_urls
from app.services.linking.suggest import suggest_links_for_subqueries

router = APIRouter()


@router.post(
    "/suggest",
    response_model=LinkingSuggestResponse,
)
async def suggest(req: LinkingSuggestRequest) -> LinkingSuggestResponse:
    if req.pages is not None:
        records = [
            PageRecord(url=p.url, title=p.title, excerpt=p.excerpt)
            for p in req.pages[: req.max_pages]
        ]
        index = build_page_index_from_records(records)
    else:
        urls = await fetch_sitemap_urls(req.sitemap_url, max_urls=req.max_pages)
        if not urls:
            raise HTTPException(
                status_code=422,
                detail={
                    "error": "empty_sitemap",
                    "message": "No URLs found in the sitemap.",
                    "detail": "sitemap parsed but contained zero <loc> entries",
                },
            )
        index = await build_page_index_from_urls(urls[: req.max_pages])

    suggestions = suggest_links_for_subqueries(
        req.sub_queries,
        index,
        source_url=req.source_url,
        top_k=req.top_k,
        min_similarity=req.min_similarity,
    )
    return LinkingSuggestResponse(
        pages_indexed=len(index.pages),
        failed_urls=[{"url": u, "detail": d} for u, d in index.failed_urls],
        suggestions=suggestions,
    )


__all__ = ["router", "PageRecordModel"]
