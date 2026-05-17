"""Cold-outreach mailer.

Picks (contact, audit) pairs that:
  * have no existing Outreach row for the requested template variant, and
  * the audit's aeo_score is below `min_score_cutoff`.

For each match: insert a placeholder Outreach row with sent_at=NULL
(ON CONFLICT DO NOTHING — the UNIQUE(contact_id, audit_id, template_variant)
constraint guarantees one-and-only-one send across concurrent runners).
Then render + send via the active `MailTransport`, then stamp sent_at +
body_hash. Transport selection prefers Resend's API over self-managed
SMTP because Resend handles SPF/DKIM/DMARC alignment for our sender
domain (see `app.integrations.resend`); SMTP stays as a free fallback
for self-hosted dev (MailHog) or operators who can't use Resend.

Hard daily cap during warm-up to keep deliverability sane.
"""

from __future__ import annotations

import hashlib
import logging
import os
from dataclasses import dataclass, field
from datetime import datetime, timezone
from email.message import EmailMessage
from typing import Any, Protocol

import aiosmtplib
from jinja2 import Environment, FileSystemLoader, select_autoescape
from sqlalchemy import select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.autopilot.report_builder import RenderContext, build_context
from app.db.models import Audit, Contact, FunnelStage, Outreach, Prospect
from app.integrations.resend import ResendMailer, SendResult
from app.services.funnel import record as record_funnel

log = logging.getLogger(__name__)


# ---- config knobs ----


DEFAULT_TEMPLATE_VARIANT = "cold_outreach"
DEFAULT_DAILY_CAP = 30      # warm-up cap; raise once SPF/DKIM warmed
DEFAULT_SCORE_CUTOFF = 70


def _smtp_settings() -> dict[str, Any]:
    return {
        "host": os.environ.get("SMTP_HOST", "localhost"),
        "port": int(os.environ.get("SMTP_PORT", "1025")),
        "user": os.environ.get("SMTP_USER") or None,
        "password": os.environ.get("SMTP_PASS") or None,
        "from_addr": os.environ.get("SMTP_FROM", "audits@aegis.local"),
    }


def _from_addr() -> str:
    return os.environ.get("SMTP_FROM", "audits@aegis.local")


def _reply_to() -> str | None:
    return os.environ.get("MAIL_REPLY_TO") or None


# ---- transport selection ----


class MailTransport(Protocol):
    """Honour the same shape as `ResendMailer.send`. The SMTP path also
    wraps to this signature so the outbox loop is transport-agnostic."""

    name: str
    enabled: bool

    async def send(
        self,
        *,
        to: str,
        subject: str,
        html: str,
        text: str,
        from_addr: str,
        reply_to: str | None = None,
    ) -> SendResult: ...


def _build_message(
    from_addr: str,
    to_addr: str,
    rendered: "RenderedEmail",
    *,
    reply_to: str | None = None,
) -> EmailMessage:
    """Compose a MIME message with text + HTML parts.

    Kept module-level so direct callers (test suite, ad-hoc admin
    scripts) don't have to instantiate an SMTPTransport just to build a
    message preview.
    """
    msg = EmailMessage()
    msg["From"] = from_addr
    msg["To"] = to_addr
    msg["Subject"] = rendered.subject
    if reply_to:
        msg["Reply-To"] = reply_to
    msg.set_content(rendered.text)
    msg.add_alternative(rendered.html, subtype="html")
    return msg


class SMTPTransport:
    """Wraps aiosmtplib in the `MailTransport` shape so transports are
    interchangeable. Behaviour preserved from the pre-refactor path."""

    name = "smtp"

    def __init__(self, settings: dict[str, Any] | None = None) -> None:
        self._settings = settings or _smtp_settings()

    @property
    def enabled(self) -> bool:
        host = self._settings.get("host")
        return bool(host) and host != "localhost" or self._settings.get("port") == 1025

    async def send(
        self,
        *,
        to: str,
        subject: str,
        html: str,
        text: str,
        from_addr: str,
        reply_to: str | None = None,
    ) -> SendResult:
        rendered = RenderedEmail(subject=subject, text=text, html=html)
        msg = _build_message(from_addr, to, rendered, reply_to=reply_to)

        kwargs: dict[str, Any] = {
            "hostname": self._settings["host"],
            "port": self._settings["port"],
            "timeout": 30,
        }
        if self._settings["port"] == 587:
            kwargs["start_tls"] = True
        if self._settings.get("user"):
            kwargs["username"] = self._settings["user"]
            kwargs["password"] = self._settings["password"]
        try:
            await aiosmtplib.send(msg, **kwargs)
        except Exception as e:  # noqa: BLE001 — aiosmtplib has many sub-types
            # Treat SMTP errors as transient unless the message body is
            # being rejected. Keeping the bucket wide is safer than the
            # caller losing track of a hard reject.
            return SendResult(
                delivered=False,
                detail=f"smtp error: {type(e).__name__}: {e}",
                retryable=True,
            )
        return SendResult(delivered=True)


class DryRunTransport:
    """Log-only transport. Used when no carrier is configured AND no
    `--dry-run` was requested, so the operator gets a loud reminder
    instead of silently dropping the queue."""

    name = "dry_run"
    enabled = True

    async def send(
        self,
        *,
        to: str,
        subject: str,
        html: str,
        text: str,
        from_addr: str,
        reply_to: str | None = None,
    ) -> SendResult:
        log.warning(
            "mail[no_transport]: not sent to %s (subject=%r). Set RESEND_API_KEY or SMTP_HOST.",
            to, subject,
        )
        return SendResult(delivered=False, detail="no transport configured", retryable=False)


def _select_transport() -> MailTransport:
    """Pick the best-available transport at call time so tests can
    monkey-patch env vars and see the change without re-importing."""
    resend = ResendMailer()
    if resend.enabled:
        return resend
    if os.environ.get("SMTP_HOST"):
        return SMTPTransport()
    return DryRunTransport()


# ---- template rendering ----


from app.autopilot.report_builder import TEMPLATES_DIR  # noqa: E402

_EMAILS_DIR = TEMPLATES_DIR / "emails"


def _jinja() -> Environment:
    return Environment(
        loader=FileSystemLoader(str(_EMAILS_DIR)),
        autoescape=select_autoescape(["html"]),
        trim_blocks=True,
        lstrip_blocks=True,
    )


@dataclass(frozen=True)
class RenderedEmail:
    subject: str
    text: str
    html: str

    def body_hash(self) -> str:
        return hashlib.sha256((self.subject + "\n" + self.text).encode("utf-8")).hexdigest()


def render_email(ctx: RenderContext, variant: str = DEFAULT_TEMPLATE_VARIANT) -> RenderedEmail:
    env = _jinja()
    return RenderedEmail(
        subject=env.get_template(f"{variant}.subject.j2").render(ctx=ctx).strip(),
        text=env.get_template(f"{variant}.txt.j2").render(ctx=ctx),
        html=env.get_template(f"{variant}.html.j2").render(ctx=ctx),
    )


def _attach_name(ctx: RenderContext, name: str | None) -> dict:
    """Wrap the frozen dataclass into a plain dict for Jinja, with contact name."""
    return {**ctx.__dict__, "contact_name": name}


# ---- pipeline ----


@dataclass
class SendStats:
    eligible: int = 0
    sent: int = 0
    skipped: int = 0
    failed: int = 0
    errors: list[str] = field(default_factory=list)


async def _select_candidates(
    session: AsyncSession,
    *,
    cutoff: int,
    variant: str,
    limit: int,
) -> list[tuple[Contact, Audit, Prospect]]:
    """Candidate join. Excludes contact/audit pairs that already have an
    Outreach row for this variant — that's our idempotency check."""
    stmt = (
        select(Contact, Audit, Prospect)
        .join(Prospect, Prospect.id == Contact.prospect_id)
        .join(Audit, Audit.prospect_id == Prospect.id)
        .outerjoin(
            Outreach,
            (Outreach.contact_id == Contact.id)
            & (Outreach.audit_id == Audit.id)
            & (Outreach.template_variant == variant),
        )
        .where(Audit.aeo_score < cutoff)
        .where(Outreach.id.is_(None))
        .order_by(Audit.created_at.desc())
        .limit(limit)
    )
    result = await session.execute(stmt)
    return [tuple(row) for row in result.all()]


async def _reserve_outreach(
    session: AsyncSession,
    contact_id: int,
    audit_id: int,
    variant: str,
    subject: str,
) -> int | None:
    """Insert a placeholder Outreach row idempotently. Returns its id or
    None if the row already exists (another runner won the race)."""
    stmt = (
        pg_insert(Outreach)
        .values(
            contact_id=contact_id,
            audit_id=audit_id,
            template_variant=variant,
            subject=subject,
        )
        .on_conflict_do_nothing(
            index_elements=["contact_id", "audit_id", "template_variant"]
        )
        .returning(Outreach.id)
    )
    row = (await session.execute(stmt)).first()
    return row[0] if row else None


async def send_pending(
    session: AsyncSession,
    *,
    min_score_cutoff: int = DEFAULT_SCORE_CUTOFF,
    max_sends: int = DEFAULT_DAILY_CAP,
    variant: str = DEFAULT_TEMPLATE_VARIANT,
    dry_run: bool = False,
    candidates: list[tuple[Contact, Audit, Prospect]] | None = None,
    transport: MailTransport | None = None,
) -> SendStats:
    """Send the next batch of `variant` emails.

    When `candidates` is provided, skip the default JOIN — the caller
    has already decided which (contact, audit, prospect) tuples need
    this variant. Used by `email_sequence` for state-aware follow-ups.

    `transport` is dependency-injected in tests; in production it's
    selected from env via `_select_transport()` (Resend → SMTP → dry-run).
    """
    active_transport = transport or _select_transport()
    from_addr = _from_addr()
    reply_to = _reply_to()
    log.info("mail: transport=%s dry_run=%s", active_transport.name, dry_run)
    stats = SendStats()

    if candidates is None:
        # Fetch enough candidates to cover the cap with headroom for failed reserves.
        candidates = await _select_candidates(
            session, cutoff=min_score_cutoff, variant=variant, limit=max_sends * 2
        )
    stats.eligible = len(candidates)

    for contact, audit, prospect in candidates:
        if stats.sent >= max_sends:
            break

        ctx = build_context(audit, prospect)
        ctx_for_jinja = _attach_name(ctx, contact.name)
        env = _jinja()
        rendered = RenderedEmail(
            subject=env.get_template(f"{variant}.subject.j2").render(ctx=ctx_for_jinja).strip(),
            text=env.get_template(f"{variant}.txt.j2").render(ctx=ctx_for_jinja),
            html=env.get_template(f"{variant}.html.j2").render(ctx=ctx_for_jinja),
        )

        outreach_id = await _reserve_outreach(
            session, contact.id, audit.id, variant, rendered.subject
        )
        if outreach_id is None:
            log.info("mail: %s/audit%d already sent; skipping", contact.email, audit.id)
            stats.skipped += 1
            continue

        if dry_run:
            log.info("mail[dry_run]: would send to %s subj=%r via %s",
                     contact.email, rendered.subject, active_transport.name)
            result = SendResult(delivered=True, detail="dry_run")
        else:
            try:
                result = await active_transport.send(
                    to=contact.email,
                    subject=rendered.subject,
                    html=rendered.html,
                    text=rendered.text,
                    from_addr=from_addr,
                    reply_to=reply_to,
                )
            except Exception as e:  # noqa: BLE001 — last-resort safety
                err = f"{type(e).__name__}: {e}"
                log.warning("mail: send raised for %s: %s", contact.email, err)
                stats.errors.append(err)
                stats.failed += 1
                await session.execute(
                    update(Outreach).where(Outreach.id == outreach_id).values(error=err)
                )
                continue

        if not result.delivered:
            err = result.detail or "send failed"
            log.warning("mail: send failed for %s: %s", contact.email, err)
            stats.errors.append(err)
            stats.failed += 1
            if result.retryable:
                # Free the slot so the next tick can retry; idempotency
                # guarantees we won't double-send if Resend later returns
                # a delayed success.
                await session.execute(
                    update(Outreach).where(Outreach.id == outreach_id).values(error=err)
                )
            else:
                await session.execute(
                    update(Outreach).where(Outreach.id == outreach_id).values(error=err)
                )
            continue

        await session.execute(
            update(Outreach)
            .where(Outreach.id == outreach_id)
            .values(
                sent_at=datetime.now(tz=timezone.utc),
                body_hash=rendered.body_hash(),
            )
        )
        await record_funnel(
            session,
            FunnelStage.contacted,
            contact_id=contact.id,
            audit_id=audit.id,
            meta={
                "variant": variant,
                "subject": rendered.subject,
                "transport": active_transport.name,
                "provider_id": result.provider_id,
            },
        )
        stats.sent += 1

    log.info(
        "mail: transport=%s eligible=%d sent=%d skipped=%d failed=%d",
        active_transport.name, stats.eligible, stats.sent, stats.skipped, stats.failed,
    )
    return stats
