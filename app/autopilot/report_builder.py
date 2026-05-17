"""Render audit reports as HTML (always) + PDF (optional via WeasyPrint).

PDF is best-effort: if WeasyPrint can't be imported (missing native
libs on the dev machine), we still write the HTML artifact and stamp
`audit.report_path` with that. The /r/{token} HTML endpoint is the
primary customer-facing artifact; PDF is a download offered when
WeasyPrint is available.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from jinja2 import Environment, FileSystemLoader, select_autoescape
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Audit, Prospect
from app.services.tokens import sign_report

log = logging.getLogger(__name__)

TEMPLATES_DIR = Path(__file__).resolve().parent.parent / "templates"
REPORTS_DIR = Path(os.environ.get("REPORTS_DIR", "reports"))


@dataclass(frozen=True)
class RenderContext:
    """Everything the template needs. Plain dataclass so tests can build one
    without dragging in the whole ORM."""

    audit_id: int
    domain: str
    url: str
    target_keyword: str
    aeo_score: int
    band: str
    failed_checks: list[dict[str, Any]]
    top_missing_comparative: list[dict[str, Any]]
    missing_gap_types: list[str]
    readability_warning: str | None
    report_url: str
    checkout_url: str


def _env() -> Environment:
    return Environment(
        loader=FileSystemLoader(str(TEMPLATES_DIR)),
        autoescape=select_autoescape(["html"]),
        trim_blocks=True,
        lstrip_blocks=True,
    )


def _readability_warning(failed: list[dict[str, Any]]) -> str | None:
    """Extract the FK grade line if Check C (readability) failed."""
    for check in failed:
        if check.get("check_id") in {"readability", "snippet_readability"}:
            details = check.get("details") or {}
            grade = details.get("flesch_kincaid_grade") or details.get("grade")
            if grade is not None:
                return f"Flesch–Kincaid grade {grade} — above the 9.0 threshold for snippet extraction."
            return check.get("recommendation") or "Readability above target threshold."
    return None


def _top_comparative(fanout_payload: dict[str, Any] | None) -> list[dict[str, Any]]:
    """Top 3 uncovered comparative sub-queries. The email + report headline."""
    if not fanout_payload:
        return []
    out: list[dict[str, Any]] = []
    for sq in fanout_payload.get("sub_queries", []):
        if sq.get("type") != "comparative":
            continue
        if sq.get("covered") is True:
            continue
        out.append(sq)
        if len(out) == 3:
            break
    return out


def build_context(
    audit: Audit,
    prospect: Prospect,
    *,
    report_base_url: str | None = None,
    checkout_url: str | None = None,
) -> RenderContext:
    report_base_url = report_base_url or os.environ.get(
        "REPORT_BASE_URL", "http://localhost:8000"
    )
    token = sign_report(audit.id, str(audit.token_jti))
    failed = audit.failed_checks or []
    return RenderContext(
        audit_id=audit.id,
        domain=prospect.domain,
        url=prospect.url,
        target_keyword=prospect.target_keyword,
        aeo_score=audit.aeo_score,
        band=audit.band,
        failed_checks=failed,
        top_missing_comparative=_top_comparative(audit.fanout_payload),
        missing_gap_types=list(audit.missing_gap_types or []),
        readability_warning=_readability_warning(failed),
        report_url=f"{report_base_url.rstrip('/')}/r/{token}",
        checkout_url=checkout_url
        or f"{report_base_url.rstrip('/')}/?source=report&audit={audit.id}",
    )


def render_html(ctx: RenderContext) -> str:
    template = _env().get_template("report.html")
    return template.render(ctx=ctx)


def _try_render_pdf(html: str, path: Path) -> bool:
    """Best-effort PDF. Returns True on success, False if WeasyPrint or its
    native deps are unavailable. Never raises — PDFs are optional."""
    try:
        from weasyprint import HTML  # type: ignore
    except Exception as e:  # noqa: BLE001 — also catches OSError on missing libs
        log.info("report_builder: WeasyPrint unavailable (%s); skipping PDF", e)
        return False
    try:
        HTML(string=html).write_pdf(str(path))
        return True
    except Exception:  # noqa: BLE001
        log.exception("report_builder: WeasyPrint failed; HTML still served")
        return False


async def build(
    session: AsyncSession,
    audit_id: int,
    *,
    report_base_url: str | None = None,
    write_pdf: bool = True,
) -> Path:
    """Render + persist a report for a single audit. Returns HTML path."""
    audit = (
        await session.execute(select(Audit).where(Audit.id == audit_id))
    ).scalar_one()
    prospect = (
        await session.execute(select(Prospect).where(Prospect.id == audit.prospect_id))
    ).scalar_one()

    ctx = build_context(audit, prospect, report_base_url=report_base_url)
    html = render_html(ctx)

    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    html_path = REPORTS_DIR / f"{audit.token_jti}.html"
    html_path.write_text(html, encoding="utf-8")

    pdf_path = REPORTS_DIR / f"{audit.token_jti}.pdf"
    pdf_ok = write_pdf and _try_render_pdf(html, pdf_path)

    # Store the canonical artifact: PDF when available, else HTML.
    audit.report_path = str(pdf_path if pdf_ok else html_path)
    return html_path


async def build_pending(
    session: AsyncSession,
    *,
    limit: int = 50,
    write_pdf: bool = True,
) -> int:
    """Build reports for all audits whose `report_path` is still NULL."""
    rows = (
        await session.execute(
            select(Audit).where(Audit.report_path.is_(None)).limit(limit)
        )
    ).scalars().all()
    built = 0
    for audit in rows:
        try:
            await build(session, audit.id, write_pdf=write_pdf)
            built += 1
        except Exception:  # noqa: BLE001
            log.exception("report_builder: build failed for audit %s", audit.id)
    log.info("report_builder: built %d / %d reports", built, len(rows))
    return built
