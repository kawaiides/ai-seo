"""Blog routes — SEO-heavy article surface."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

from app.content.articles import get_article, list_articles
from app.db.models import User
from app.services.auth import get_current_user

router = APIRouter(tags=["blog"])

_TEMPLATES_DIR = Path(__file__).resolve().parent.parent / "templates"
_templates = Jinja2Templates(directory=str(_TEMPLATES_DIR))


def _site_url() -> str:
    return os.environ.get("AEGIS_SITE_URL", "https://aegis-autopilot.com").rstrip("/")


@router.get("/blog", response_class=HTMLResponse)
async def blog_index(
    request: Request,
    current_user: Optional[User] = Depends(get_current_user),
) -> HTMLResponse:
    articles = list_articles()
    return _templates.TemplateResponse(
        request,
        "blog/index.html",
        {
            "current_user": current_user,
            "articles": articles,
            "site_url": _site_url(),
        },
    )


@router.get("/blog/{slug}", response_class=HTMLResponse)
async def blog_article(
    slug: str,
    request: Request,
    current_user: Optional[User] = Depends(get_current_user),
) -> HTMLResponse:
    article = get_article(slug)
    if article is None:
        raise HTTPException(status_code=404, detail="Article not found")

    related_articles = [
        get_article(s) for s in article.get("related", []) if get_article(s) is not None
    ]

    site_url = _site_url()
    canonical = f"{site_url}/blog/{slug}"

    article_ld = {
        "@context": "https://schema.org",
        "@type": "Article",
        "headline": article["title"],
        "description": article["description"],
        "image": f"{site_url}/og-image.png",
        "datePublished": article["published"],
        "dateModified": article.get("updated") or article["published"],
        "author": {
            "@type": "Organization",
            "name": article.get("author", "AEGIS Team"),
            "url": site_url,
        },
        "publisher": {
            "@type": "Organization",
            "name": "AEGIS Autopilot",
            "url": site_url,
            "logo": {"@type": "ImageObject", "url": f"{site_url}/logo.png"},
        },
        "mainEntityOfPage": {"@type": "WebPage", "@id": canonical},
        "keywords": ", ".join(article.get("keywords", [])),
        "articleSection": article.get("category", "Guides"),
        "wordCount": len(article.get("body_html", "").split()),
        "timeRequired": f"PT{article.get('reading_minutes', 8)}M",
    }
    faq_ld = {
        "@context": "https://schema.org",
        "@type": "FAQPage",
        "mainEntity": [
            {
                "@type": "Question",
                "name": f["q"],
                "acceptedAnswer": {"@type": "Answer", "text": f["a"]},
            }
            for f in article.get("faqs", [])
        ],
    } if article.get("faqs") else None
    breadcrumb_ld = {
        "@context": "https://schema.org",
        "@type": "BreadcrumbList",
        "itemListElement": [
            {"@type": "ListItem", "position": 1, "name": "Home", "item": f"{site_url}/"},
            {"@type": "ListItem", "position": 2, "name": "Blog", "item": f"{site_url}/blog"},
            {"@type": "ListItem", "position": 3, "name": article["title"], "item": canonical},
        ],
    }

    return _templates.TemplateResponse(
        request,
        "blog/article.html",
        {
            "current_user": current_user,
            "article": article,
            "related_articles": related_articles,
            "canonical": canonical,
            "site_url": site_url,
            "article_ld_json": json.dumps(article_ld, ensure_ascii=False),
            "faq_ld_json": json.dumps(faq_ld, ensure_ascii=False) if faq_ld else None,
            "breadcrumb_ld_json": json.dumps(breadcrumb_ld, ensure_ascii=False),
        },
    )
