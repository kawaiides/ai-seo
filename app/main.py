from __future__ import annotations

import logging
import os
import threading
import uuid
from datetime import date
from pathlib import Path

from dotenv import load_dotenv
from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, JSONResponse, PlainTextResponse, Response
from fastapi.templating import Jinja2Templates
from starlette.exceptions import HTTPException as StarletteHTTPException

# Load .env once at import so OPENAI_API_KEY/OPENAI_MODEL are available
# wherever services need them (LLM client, etc.). override=False so a
# real shell env can still take precedence in production.
load_dotenv(override=False)

from app.api import (  # noqa: E402
    account,
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

# CORS allow-list. `ALLOWED_ORIGINS` is a comma-separated env var driven
# by Terraform — defaults to "*" only in dev/test. Production gets the
# apex + www origins or whatever the operator configured.
_cors_env = os.environ.get("ALLOWED_ORIGINS", "").strip()
if _cors_env:
    _cors_origins = [o.strip() for o in _cors_env.split(",") if o.strip()]
else:
    _cors_origins = ["*"]
_cors_allow_credentials = _cors_origins != ["*"]
app.add_middleware(
    CORSMiddleware,
    allow_origins=_cors_origins,
    allow_methods=["GET", "POST"],
    allow_headers=["Content-Type", "Authorization"],
    allow_credentials=_cors_allow_credentials,
)


# HSTS: instruct browsers never to talk to us over plaintext HTTP again
# once they've seen this header. Only emitted when the public origin is
# HTTPS — local dev (http://localhost) keeps the header off so it doesn't
# poison browsers used for both prod and local testing.
_HSTS_ENABLED = os.environ.get("APP_BASE_URL", "").lower().startswith("https://") or (
    os.environ.get("AEGIS_FORCE_HSTS", "").lower() in {"1", "true", "yes", "on"}
)
_HSTS_HEADER = "max-age=31536000; includeSubDomains"


@app.middleware("http")
async def _security_headers(request: Request, call_next):
    response = await call_next(request)
    if _HSTS_ENABLED:
        response.headers.setdefault("Strict-Transport-Security", _HSTS_HEADER)
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
    return response

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
app.include_router(account.router, tags=["account"])
app.include_router(billing.router)
app.include_router(payment_webhooks.router)
app.include_router(region.router)
app.include_router(blog.router)
app.include_router(admin.router)


# ---- Graceful error rendering ----
#
# API callers (anything under /api/* or that explicitly asked for JSON
# via Accept) get a stable JSON envelope. Anything else gets a styled
# HTML page rendered from templates/errors/error.html so a stray 404 or
# unhandled exception doesn't leak a Starlette traceback or a bare
# "Internal Server Error" string.
#
# request_id is a short opaque identifier that's safe to surface to the
# user — it shows up in server logs alongside the full traceback so an
# operator can grep without exposing the original error detail.

_log = logging.getLogger(__name__)

_STATUS_COPY: dict[int, tuple[str, str]] = {
    400: ("Bad request",
          "The request couldn't be understood. Check the input and try again."),
    401: ("Sign in required",
          "You need to sign in to view this page."),
    403: ("Not allowed",
          "You don't have access to this resource."),
    404: ("Page not found",
          "The page you're looking for doesn't exist or has moved."),
    405: ("Method not allowed",
          "That action isn't supported on this URL."),
    409: ("Conflict",
          "That action conflicts with the current state. Refresh and try again."),
    410: ("Gone",
          "This page has been permanently removed."),
    413: ("Too large",
          "The content you submitted is too big to process."),
    422: ("Couldn't process that",
          "The input didn't pass validation. Fix the highlighted issues and retry."),
    429: ("Too many requests",
          "You've hit the rate limit. Wait a moment and try again, "
          "or upgrade to remove the cap."),
    500: ("Something went wrong",
          "An unexpected error occurred on our side. Our team has been notified."),
    502: ("Upstream error",
          "A service we depend on returned an error. Please retry shortly."),
    503: ("Temporarily unavailable",
          "We're briefly unavailable. Please try again in a few moments."),
    504: ("Upstream timeout",
          "An upstream service took too long to respond. Please retry."),
}


def _wants_json(request: Request) -> bool:
    """True if the caller is API-style and expects a JSON envelope.

    Two signals: path under /api/* (every JSON route we own), or an
    Accept header that prefers JSON over HTML. Browsers send
    `text/html,application/xhtml+xml,...` so they fall through to HTML.
    """
    if request.url.path.startswith("/api/"):
        return True
    accept = request.headers.get("accept", "")
    if "application/json" in accept and "text/html" not in accept:
        return True
    return False


def _status_copy(status_code: int) -> tuple[str, str]:
    if status_code in _STATUS_COPY:
        return _STATUS_COPY[status_code]
    if 400 <= status_code < 500:
        return ("Request failed",
                "The request couldn't be completed. Check the URL and try again.")
    return _STATUS_COPY[500]


def _new_request_id() -> str:
    return uuid.uuid4().hex[:12]


def _render_error_html(
    request: Request,
    *,
    status_code: int,
    detail: str | None = None,
    request_id: str | None = None,
) -> HTMLResponse:
    headline, message = _status_copy(status_code)
    # Refuse to render the styled page if a template loader failure would
    # itself raise inside this handler — fall back to plain text so we
    # never end up in a handler-of-the-handler loop.
    try:
        # Pull current_user best-effort: if it raises (e.g. no DB), skip.
        current_user = None
        return templates.TemplateResponse(
            request,
            "errors/error.html",
            {
                "status_code": status_code,
                "headline": headline,
                "message": message,
                "detail": detail,
                "request_id": request_id or _new_request_id(),
                "current_user": current_user,
            },
            status_code=status_code,
        )
    except Exception as render_err:  # pragma: no cover — defensive only
        _log.exception("error template render failed: %s", render_err)
        return HTMLResponse(
            content=(
                f"<html><body style='font-family:system-ui;padding:48px;"
                f"max-width:640px;margin:auto;color:#1e293b'>"
                f"<h1 style='margin:0 0 12px'>{headline}</h1>"
                f"<p>{message}</p>"
                f"<p><a href='/'>Back to home</a></p>"
                f"</body></html>"
            ),
            status_code=status_code,
        )


def _api_error_envelope(
    status_code: int,
    detail: object,
    *,
    request_id: str,
) -> dict:
    # Preserve dict-shaped HTTPException details so existing API callers
    # keep their {error, message, ...} envelopes; wrap bare strings.
    if isinstance(detail, dict):
        envelope = dict(detail)
        envelope.setdefault("request_id", request_id)
        return envelope
    headline, message = _status_copy(status_code)
    return {
        "error": "http_error",
        "status": status_code,
        "message": message if detail is None else str(detail),
        "headline": headline,
        "request_id": request_id,
    }


@app.exception_handler(StarletteHTTPException)
async def _http_exception_handler(
    request: Request, exc: StarletteHTTPException
) -> Response:
    request_id = _new_request_id()
    if exc.status_code >= 500:
        _log.warning(
            "http_exception status=%s path=%s request_id=%s detail=%r",
            exc.status_code, request.url.path, request_id, exc.detail,
        )
    if _wants_json(request):
        return JSONResponse(
            status_code=exc.status_code,
            content=_api_error_envelope(exc.status_code, exc.detail, request_id=request_id),
            headers=exc.headers or None,
        )
    detail_str = (
        None
        if exc.detail in (None, "", "Not Found", "Method Not Allowed")
        else (
            "; ".join(f"{k}: {v}" for k, v in exc.detail.items())
            if isinstance(exc.detail, dict)
            else str(exc.detail)
        )
    )
    return _render_error_html(
        request,
        status_code=exc.status_code,
        detail=detail_str,
        request_id=request_id,
    )


@app.exception_handler(RequestValidationError)
async def _validation_error_handler(
    request: Request, exc: RequestValidationError
) -> Response:
    request_id = _new_request_id()
    if _wants_json(request):
        return JSONResponse(
            status_code=422,
            content=jsonable_encoder(
                {
                    "error": "validation_failed",
                    "status": 422,
                    "errors": exc.errors(),
                    "request_id": request_id,
                }
            ),
        )
    # Summarise field errors so the user sees what to fix, but cap to avoid
    # rendering 200 lines of pydantic.
    detail_lines = []
    for e in exc.errors()[:6]:
        loc = ".".join(str(p) for p in e.get("loc", []) if p not in ("body",))
        detail_lines.append(f"{loc or 'request'} — {e.get('msg', '')}")
    return _render_error_html(
        request,
        status_code=422,
        detail="\n".join(detail_lines) if detail_lines else None,
        request_id=request_id,
    )


@app.exception_handler(Exception)
async def _unhandled_exception_handler(request: Request, exc: Exception) -> Response:
    request_id = _new_request_id()
    _log.exception(
        "unhandled_exception path=%s request_id=%s", request.url.path, request_id,
    )
    if _wants_json(request):
        return JSONResponse(
            status_code=500,
            content={
                "error": "server_error",
                "status": 500,
                "message": "An unexpected error occurred.",
                "request_id": request_id,
            },
        )
    return _render_error_html(
        request,
        status_code=500,
        detail=None,  # never surface internals in HTML — log has the trace
        request_id=request_id,
    )


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
