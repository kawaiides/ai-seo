"""API-key-gated v1 surface (Phase E).

Mirrors the cookie-authenticated `/api/aeo/analyze` endpoint behind
Bearer-token auth + scoped permissions so external CI/CMS integrations
can run audits programmatically.

Versioned under `/api/v1` so we can keep the existing browser-facing
endpoints stable while iterating on the public contract.
"""

from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, Depends

from app.api.aeo import _build_response
from app.models.schemas import AEOAnalyzeRequest, AEOAnalyzeResponse
from app.services.aeo_checks import default_checks
from app.services.api_keys import AuthorizedKey, require_scope
from app.services.content_parser import fetch_url, parse

router = APIRouter()


@router.post("/audit", response_model=AEOAnalyzeResponse)
async def audit(
    req: AEOAnalyzeRequest,
    authorized: AuthorizedKey = Depends(require_scope("audit:write")),
) -> AEOAnalyzeResponse:
    raw = await fetch_url(req.input_value) if req.input_type == "url" else req.input_value
    parsed = parse(raw, input_type=req.input_type)
    results = [check.run(parsed) for check in default_checks()]
    return _build_response(results)


@router.get("/ping")
async def ping(
    authorized: AuthorizedKey = Depends(require_scope("audit:read")),
) -> dict:
    """Cheap health check that exercises Bearer auth without spending
    audit cost. Used by CI plugins to validate key before running."""
    return {
        "ok": True,
        "org_id": str(authorized.org.id),
        "org_slug": authorized.org.slug,
        "key_prefix": authorized.api_key.prefix,
        "scopes": list(authorized.api_key.scopes),
        "now": datetime.now(tz=timezone.utc).isoformat(),
    }
