from __future__ import annotations

import os
import threading
from datetime import date
from pathlib import Path

from dotenv import load_dotenv
from fastapi import Depends, FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, JSONResponse, PlainTextResponse, Response
from fastapi.templating import Jinja2Templates

# Load .env once at import so OPENAI_API_KEY/OPENAI_MODEL are available
# wherever services need them (LLM client, etc.). override=False so a
# real shell env can still take precedence in production.
load_dotenv(override=False)

from app.api import (  # noqa: E402
    admin,
    aeo,
    auth,
    billing,
    blog,
    byok,
    comments,
    fanout,
    geo,
    keys,
    linking,
    orgs,
    payment_webhooks,
    region,
    reports,
    rewrite,
    site,
    v1,
    webhooks,
)
from app.services.auth import get_current_user  # noqa: E402
from app.services.content_parser import ContentParseError, URLFetchError  # noqa: E402
from app.services.llm_client import LLMUnavailableError  # noqa: E402

app = FastAPI(title="AEGIS — AI Engineer Assignment")

# Permissive CORS so the UI works whether served from FastAPI itself, a
# VSCode live-preview port, or a future agency-embedded widget on a third
# party domain. Tighten before production by replacing "*" with an allow-list.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["GET", "POST"],
    allow_headers=["Content-Type"],
)

TEMPLATES_DIR = Path(__file__).parent / "templates"
templates = Jinja2Templates(directory=str(TEMPLATES_DIR))

# ---- Rate limiting (3 scans / IP / day, in-memory) ----
# Counts requests that hit either rate-limited endpoint. Resets at UTC date
# change. Process-local — fine for a single uvicorn worker on a $15 VPS;
# swap for Redis if we scale horizontally.

# Fanout moved off middleware — gating dep on the endpoint handles its quota
# alongside BYOK/Sub checks. AEO stays here (no LLM cost; cheap to count).
RATE_LIMITED_PATHS = ("/api/aeo/analyze",)
DAILY_LIMIT = 3

_counts: dict[tuple[str, date], int] = {}
_counts_lock = threading.Lock()


def _client_ip(request: Request) -> str:
    fwd = request.headers.get("x-forwarded-for")
    if fwd:
        return fwd.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


@app.middleware("http")
async def rate_limit_middleware(request: Request, call_next):
    if request.url.path not in RATE_LIMITED_PATHS:
        return await call_next(request)
    if os.environ.get("AEGIS_DISABLE_RATE_LIMIT") == "1":
        return await call_next(request)

    today = date.today()
    ip = _client_ip(request)
    key = (ip, today)

    with _counts_lock:
        used = _counts.get(key, 0)
        if used >= DAILY_LIMIT:
            return JSONResponse(
                status_code=429,
                content={
                    "error": "rate_limited",
                    "message": f"Daily free scan limit reached ({DAILY_LIMIT}/day). Try again tomorrow.",
                    "detail": f"used={used} limit={DAILY_LIMIT}",
                },
            )
        _counts[key] = used + 1

    return await call_next(request)


app.include_router(aeo.router, prefix="/api/aeo", tags=["aeo"])
app.include_router(fanout.router, prefix="/api/fanout", tags=["fanout"])
app.include_router(reports.router, tags=["reports"])
app.include_router(rewrite.router, prefix="/api/rewrite", tags=["rewrite"])
app.include_router(linking.router, prefix="/api/linking", tags=["linking"])
app.include_router(site.router, prefix="/api/site", tags=["site"])
app.include_router(geo.router, prefix="/api/geo", tags=["geo"])
app.include_router(orgs.router, prefix="/api/orgs", tags=["orgs"])
app.include_router(comments.router, prefix="/api", tags=["comments"])
app.include_router(keys.router, prefix="/api/orgs/{org_id}/keys", tags=["api-keys"])
app.include_router(webhooks.router, prefix="/api/orgs/{org_id}/webhooks", tags=["webhooks"])
app.include_router(v1.router, prefix="/api/v1", tags=["v1"])
app.include_router(byok.router)
app.include_router(auth.router)
app.include_router(billing.router)
app.include_router(payment_webhooks.router)
app.include_router(region.router)
app.include_router(blog.router)
app.include_router(admin.router)


@app.exception_handler(URLFetchError)
async def _url_fetch_error_handler(_: Request, exc: URLFetchError) -> JSONResponse:
    return JSONResponse(
        status_code=422,
        content={
            "error": "url_fetch_failed",
            "message": "Could not retrieve content from the provided URL.",
            "detail": exc.detail,
        },
    )


@app.exception_handler(ContentParseError)
async def _content_parse_error_handler(_: Request, exc: ContentParseError) -> JSONResponse:
    return JSONResponse(
        status_code=422,
        content={
            "error": "content_unparseable",
            "message": "The provided content could not be parsed.",
            "detail": exc.detail,
        },
    )


@app.exception_handler(LLMUnavailableError)
async def _llm_unavailable_handler(_: Request, exc: LLMUnavailableError) -> JSONResponse:
    return JSONResponse(
        status_code=503,
        content={
            "error": "llm_unavailable",
            "message": "Fan-out generation failed. The LLM returned an invalid response after 3 retries.",
            "detail": exc.detail,
        },
    )


@app.get("/", response_class=HTMLResponse)
async def index(
    request: Request,
    current_user=Depends(get_current_user),
) -> HTMLResponse:
    return templates.TemplateResponse(
        request, "index.html", {"current_user": current_user}
    )


@app.get("/api/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


@app.on_event("startup")
async def _maybe_start_blog_scheduler() -> None:
    """Auto-blog every 3 days (in-process).

    Disabled by default to avoid surprise LLM bills. Set
    `AEGIS_BLOG_AUTOGEN=1` to enable. For multi-worker prod, run
    `python -m app.autopilot.blog_scheduler --once` from cron hourly instead.
    """
    if os.environ.get("AEGIS_BLOG_AUTOGEN") != "1":
        return
    from app.autopilot.blog_scheduler import start_in_process

    start_in_process()


SITE_URL = os.environ.get("AEGIS_SITE_URL", "https://aegis-autopilot.com").rstrip("/")


@app.get("/robots.txt", response_class=PlainTextResponse)
async def robots() -> str:
    return (
        "User-agent: *\n"
        "Allow: /\n"
        "Allow: /api/health\n"
        "Disallow: /api/aeo/\n"
        "Disallow: /api/fanout/\n"
        "Disallow: /api/rewrite/\n"
        "Disallow: /api/linking/\n"
        "Disallow: /api/auth/\n"
        "Disallow: /api/billing/\n"
        "Disallow: /reports/\n"
        "Disallow: /account\n"
        "Disallow: /admin\n"
        "Disallow: /checkout/\n"
        "\n"
        "User-agent: GPTBot\n"
        "Allow: /\n"
        "\n"
        "User-agent: ChatGPT-User\n"
        "Allow: /\n"
        "\n"
        "User-agent: PerplexityBot\n"
        "Allow: /\n"
        "\n"
        "User-agent: ClaudeBot\n"
        "Allow: /\n"
        "\n"
        "User-agent: Google-Extended\n"
        "Allow: /\n"
        "\n"
        f"Sitemap: {SITE_URL}/sitemap.xml\n"
    )


@app.get("/sitemap.xml")
async def sitemap() -> Response:
    from app.content.articles import list_articles  # local import: keeps module load lazy

    today = date.today().isoformat()
    static_paths = [
        ("/", "weekly", "1.0", today),
        ("/pricing", "monthly", "0.9", today),
        ("/blog", "weekly", "0.9", today),
        ("/#how", "monthly", "0.7", today),
        ("/#faq", "monthly", "0.7", today),
        ("/signup", "yearly", "0.5", today),
        ("/login", "yearly", "0.4", today),
    ]
    parts = ['<?xml version="1.0" encoding="UTF-8"?>',
             '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">']
    for path, freq, prio, lastmod in static_paths:
        parts.append(
            f"  <url>\n"
            f"    <loc>{SITE_URL}{path}</loc>\n"
            f"    <lastmod>{lastmod}</lastmod>\n"
            f"    <changefreq>{freq}</changefreq>\n"
            f"    <priority>{prio}</priority>\n"
            f"  </url>"
        )
    for a in list_articles():
        slug = a["slug"]
        lastmod = a.get("updated") or a.get("published") or today
        parts.append(
            f"  <url>\n"
            f"    <loc>{SITE_URL}/blog/{slug}</loc>\n"
            f"    <lastmod>{lastmod}</lastmod>\n"
            f"    <changefreq>monthly</changefreq>\n"
            f"    <priority>0.8</priority>\n"
            f"  </url>"
        )
    parts.append("</urlset>\n")
    xml = "\n".join(parts)
    return Response(content=xml, media_type="application/xml")
