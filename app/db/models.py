"""ORM models for AEGIS Autopilot.

Tables follow the build plan in `to-build-aegis-autopilot-synthetic-flask.md`.
All `*_at` timestamps are `timestamptz` (server defaults `now()`). JSONB used
for free-form payloads we expect to query later. Enums are Python enums
materialised as Postgres `CREATE TYPE`.
"""

from __future__ import annotations

import enum
import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    Enum,
    ForeignKey,
    Integer,
    SmallInteger,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import CITEXT, INET, JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base


# ---------- Enums ----------


class ProspectStatus(str, enum.Enum):
    queued = "queued"
    audited = "audited"
    failed = "failed"
    skipped = "skipped"


class UserPlan(str, enum.Enum):
    free = "free"
    pro_usd = "pro_usd"
    pro_inr = "pro_inr"


class Processor(str, enum.Enum):
    stripe = "stripe"
    razorpay = "razorpay"


class SubscriptionStatus(str, enum.Enum):
    active = "active"
    past_due = "past_due"
    canceled = "canceled"
    halted = "halted"


class BYOKProvider(str, enum.Enum):
    openai = "openai"
    gemini = "gemini"


class OrgRole(str, enum.Enum):
    """RBAC role inside an `Org`.

    `owner`  — billing seat; can invite, demote, transfer, delete the org.
    `editor` — runs audits, edits sites, adds comments.
    `viewer` — reads reports + comments only; cannot trigger billable work.
    """

    owner = "owner"
    editor = "editor"
    viewer = "viewer"


class FunnelStage(str, enum.Enum):
    discovered = "discovered"
    visited = "visited"
    audited = "audited"
    scanned = "scanned"
    contacted = "contacted"
    engaged = "engaged"
    paywalled = "paywalled"
    trialing = "trialing"
    converted = "converted"
    retained = "retained"
    at_risk = "at_risk"
    churned = "churned"


# ---------- Outbound pipeline ----------


class SeedKeyword(Base):
    __tablename__ = "seed_keyword"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    keyword: Mapped[str] = mapped_column(String(256), nullable=False)
    vertical: Mapped[str | None] = mapped_column(String(64))
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    __table_args__ = (UniqueConstraint("keyword", name="uq_seed_keyword_keyword"),)


class Prospect(Base):
    __tablename__ = "prospect"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    url: Mapped[str] = mapped_column(Text, nullable=False)
    domain: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    target_keyword: Mapped[str] = mapped_column(String(256), nullable=False, index=True)
    source: Mapped[str] = mapped_column(String(32), default="serpapi", nullable=False)
    status: Mapped[ProspectStatus] = mapped_column(
        Enum(ProspectStatus, name="prospect_status"),
        default=ProspectStatus.queued,
        nullable=False,
        index=True,
    )
    failure_reason: Mapped[str | None] = mapped_column(Text)
    discovered_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    audits: Mapped[list["Audit"]] = relationship(back_populates="prospect", cascade="all, delete-orphan")
    contacts: Mapped[list["Contact"]] = relationship(back_populates="prospect", cascade="all, delete-orphan")

    __table_args__ = (UniqueConstraint("url", name="uq_prospect_url"),)


class Audit(Base):
    __tablename__ = "audit"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    prospect_id: Mapped[int] = mapped_column(
        ForeignKey("prospect.id", ondelete="CASCADE"), nullable=False, index=True
    )
    aeo_score: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    band: Mapped[str] = mapped_column(String(32), nullable=False)
    fanout_payload: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    missing_gap_types: Mapped[list[str] | None] = mapped_column(JSONB)
    failed_checks: Mapped[list[dict[str, Any]] | None] = mapped_column(JSONB)
    report_path: Mapped[str | None] = mapped_column(Text)
    token_jti: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), default=uuid.uuid4, nullable=False, unique=True, index=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    prospect: Mapped[Prospect] = relationship(back_populates="audits")
    outreach_items: Mapped[list["Outreach"]] = relationship(back_populates="audit")


class Contact(Base):
    __tablename__ = "contact"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    prospect_id: Mapped[int] = mapped_column(
        ForeignKey("prospect.id", ondelete="CASCADE"), nullable=False, index=True
    )
    email: Mapped[str] = mapped_column(CITEXT, nullable=False)
    name: Mapped[str | None] = mapped_column(String(128))
    role: Mapped[str | None] = mapped_column(String(128))
    source: Mapped[str | None] = mapped_column(String(64))
    verified: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    discovered_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    # Set when the contact clicks the List-Unsubscribe link (RFC 8058 / CAN-SPAM).
    # Once non-null, `outbox_mailer._select_candidates` filters this row out
    # of every future send — including re-runs and follow-up sequence variants.
    unsubscribed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    prospect: Mapped[Prospect] = relationship(back_populates="contacts")
    outreach_items: Mapped[list["Outreach"]] = relationship(back_populates="contact")

    __table_args__ = (UniqueConstraint("prospect_id", "email", name="uq_contact_prospect_email"),)


class Outreach(Base):
    __tablename__ = "outreach"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    contact_id: Mapped[int] = mapped_column(ForeignKey("contact.id", ondelete="CASCADE"), nullable=False, index=True)
    audit_id: Mapped[int] = mapped_column(ForeignKey("audit.id", ondelete="CASCADE"), nullable=False, index=True)
    template_variant: Mapped[str] = mapped_column(String(64), default="cold_outreach", nullable=False)
    subject: Mapped[str | None] = mapped_column(String(512))
    body_hash: Mapped[str | None] = mapped_column(String(64))
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    opens: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    clicks: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    replied: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    error: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    contact: Mapped[Contact] = relationship(back_populates="outreach_items")
    audit: Mapped[Audit] = relationship(back_populates="outreach_items")

    __table_args__ = (
        UniqueConstraint(
            "contact_id", "audit_id", "template_variant",
            name="uq_outreach_contact_audit_variant",
        ),
    )


# ---------- Inbound visitors + scan quota ----------


class Scan(Base):
    """Persisted record of every billable API hit. Replaces the in-memory
    `_counts` dict in `app/main.py` once the gating dependency lands in M5.
    """

    __tablename__ = "scan"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    ip: Mapped[str] = mapped_column(INET, nullable=False)
    user_session_id: Mapped[str | None] = mapped_column(String(64))
    path: Mapped[str] = mapped_column(String(128), nullable=False)
    scanned_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    __table_args__ = (
        # Index supports "how many scans from this IP today?" query.
        # Created via Alembic op.create_index for readability.
    )


class User(Base):
    """Anonymous-friendly user. `session_id` is a signed-cookie value; `email`
    only populates once they convert."""

    __tablename__ = "user_"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    session_id: Mapped[str] = mapped_column(String(64), nullable=False, unique=True, index=True)
    email: Mapped[str | None] = mapped_column(CITEXT, unique=True)
    ip_country: Mapped[str | None] = mapped_column(String(2))
    plan: Mapped[UserPlan] = mapped_column(
        Enum(UserPlan, name="user_plan"), default=UserPlan.free, nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    subscriptions: Mapped[list["Subscription"]] = relationship(back_populates="user")
    byok_keys: Mapped[list["BYOKValidation"]] = relationship(back_populates="user")


# ---------- Payments ----------


class Subscription(Base):
    __tablename__ = "subscription"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("user_.id", ondelete="CASCADE"), nullable=False, index=True
    )
    processor: Mapped[Processor] = mapped_column(Enum(Processor, name="processor"), nullable=False)
    external_id: Mapped[str] = mapped_column(String(128), nullable=False, unique=True, index=True)
    status: Mapped[SubscriptionStatus] = mapped_column(
        Enum(SubscriptionStatus, name="subscription_status"), nullable=False
    )
    current_period_end: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )

    user: Mapped[User] = relationship(back_populates="subscriptions")


class BYOKValidation(Base):
    """Per-key validation cache. Stores ONLY the sha256 hash — never the raw key."""

    __tablename__ = "byok_validation"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("user_.id", ondelete="CASCADE"), nullable=False, index=True
    )
    key_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    provider: Mapped[BYOKProvider] = mapped_column(Enum(BYOKProvider, name="byok_provider"), nullable=False)
    last_verified_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    valid: Mapped[bool] = mapped_column(Boolean, nullable=False)

    user: Mapped[User] = relationship(back_populates="byok_keys")

    __table_args__ = (UniqueConstraint("user_id", "key_hash", name="uq_byok_user_keyhash"),)


class WebhookEvent(Base):
    """Idempotency log for processor webhooks. PK = provider event id."""

    __tablename__ = "webhook_event"

    id: Mapped[str] = mapped_column(String(128), primary_key=True)
    processor: Mapped[Processor] = mapped_column(Enum(Processor, name="processor"), nullable=False)
    event_type: Mapped[str] = mapped_column(String(128), nullable=False)
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    received_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    processed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    error: Mapped[str | None] = mapped_column(Text)


# ---------- Phase C.1 — Site ingest + dashboard ----------


class Site(Base):
    """A customer's site as a logical container for many audited pages.

    Ownership story (Critical Decision #4 in the roadmap):
      - `user_id` is the *legacy* owner. Pre-Phase-D code persists this
        and nothing else. Free-tier visitors who never converted to an
        org still live here.
      - `org_id` is the *new* owner. Phase D onwards, every new ingest
        records both; the comment-authorisation projection prefers
        `org_id` whenever present and falls back to user-id for legacy
        rows. Both are nullable so the migration is additive: no
        existing autopilot row needs touching, and the Subscription FK
        re-pivot (also held on `user_id` today) can keep moving on its
        own timeline.
    """

    __tablename__ = "site"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("user_.id", ondelete="CASCADE"), index=True
    )
    org_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("org.id", ondelete="CASCADE"), index=True
    )
    root_url: Mapped[str] = mapped_column(Text, nullable=False)
    sitemap_url: Mapped[str | None] = mapped_column(Text)
    competitor_root_urls: Mapped[list[str] | None] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    pages: Mapped[list["SitePage"]] = relationship(
        back_populates="site", cascade="all, delete-orphan"
    )

    __table_args__ = (
        UniqueConstraint("user_id", "root_url", name="uq_site_user_root"),
    )


class SitePage(Base):
    """One audited URL inside a `Site`.

    `last_*` columns are a denormalised rollup of the most recent
    `SitePageAudit` so dashboard queries don't need a per-page subquery.
    """

    __tablename__ = "site_page"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    site_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("site.id", ondelete="CASCADE"), nullable=False, index=True
    )
    url: Mapped[str] = mapped_column(Text, nullable=False)
    title: Mapped[str | None] = mapped_column(String(512))
    last_audited_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_score: Mapped[int | None] = mapped_column(SmallInteger)
    last_band: Mapped[str | None] = mapped_column(String(32))
    last_failed_checks: Mapped[list[str] | None] = mapped_column(JSONB)
    last_missing_types: Mapped[list[str] | None] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    site: Mapped[Site] = relationship(back_populates="pages")
    audits: Mapped[list["SitePageAudit"]] = relationship(
        back_populates="page", cascade="all, delete-orphan"
    )

    __table_args__ = (UniqueConstraint("site_id", "url", name="uq_site_page_site_url"),)


class SitePageAudit(Base):
    """Append-only history row per audit run; powers trend over time."""

    __tablename__ = "site_page_audit"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    site_page_id: Mapped[int] = mapped_column(
        ForeignKey("site_page.id", ondelete="CASCADE"), nullable=False, index=True
    )
    aeo_score: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    band: Mapped[str] = mapped_column(String(32), nullable=False)
    failed_checks: Mapped[list[str] | None] = mapped_column(JSONB)
    missing_types: Mapped[list[str] | None] = mapped_column(JSONB)
    audited_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False, index=True
    )

    page: Mapped[SitePage] = relationship(back_populates="audits")


# ---------- Phase D — Org / collaboration ----------


class Org(Base):
    """Multi-tenant container. Subscriptions, sites, and audits will
    progressively migrate from `User` → `Org` ownership; the autopilot
    track owns the timeline for the `Subscription` re-FK so for now
    `Org` is additive only (no destructive FK churn)."""

    __tablename__ = "org"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    slug: Mapped[str] = mapped_column(String(64), nullable=False, unique=True, index=True)
    logo_url: Mapped[str | None] = mapped_column(Text)
    branded_subdomain: Mapped[str | None] = mapped_column(String(64), unique=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    members: Mapped[list["OrgMember"]] = relationship(
        back_populates="org", cascade="all, delete-orphan"
    )


class OrgMember(Base):
    __tablename__ = "org_member"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    org_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("org.id", ondelete="CASCADE"), nullable=False, index=True
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("user_.id", ondelete="CASCADE"), nullable=False, index=True
    )
    role: Mapped[OrgRole] = mapped_column(
        Enum(OrgRole, name="org_role"), nullable=False
    )
    invited_email: Mapped[str | None] = mapped_column(CITEXT)
    accepted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    org: Mapped[Org] = relationship(back_populates="members")

    __table_args__ = (UniqueConstraint("org_id", "user_id", name="uq_org_member_org_user"),)


class AuditComment(Base):
    """Inline note on a `SitePageAudit`, optionally scoped to a `check_id`.

    Routes the content team → engineering team conversation in-app so
    customers don't have to copy-paste failed-check JSON into Slack.
    """

    __tablename__ = "audit_comment"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    site_page_audit_id: Mapped[int] = mapped_column(
        ForeignKey("site_page_audit.id", ondelete="CASCADE"), nullable=False, index=True
    )
    author_user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("user_.id", ondelete="SET NULL"), nullable=True, index=True
    )
    check_id: Mapped[str | None] = mapped_column(String(64), index=True)
    body: Mapped[str] = mapped_column(Text, nullable=False)
    resolved: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False, index=True
    )


# ---------- Phase E — REST API keys + outbound webhooks ----------


class ApiKey(Base):
    """Bearer token issued to an `Org` so customers can run audits
    programmatically inside CI/CMS pipelines.

    Storage rules:
      - `prefix` is shown in the UI for identification (`aegis_ak_xxx…`).
      - `key_hash` is sha256 of the full token; the raw token is shown
        ONCE at creation and never persisted.
      - `scopes` is a JSONB array of `"resource:action"` strings.
    """

    __tablename__ = "api_key"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    org_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("org.id", ondelete="CASCADE"), nullable=False, index=True
    )
    name: Mapped[str] = mapped_column(String(64), nullable=False)
    prefix: Mapped[str] = mapped_column(String(16), nullable=False, unique=True, index=True)
    key_hash: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    scopes: Mapped[list[str]] = mapped_column(JSONB, nullable=False)
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class Webhook(Base):
    """Outbound webhook subscription per `Org`.

    `secret` is used to HMAC-sign every delivery so the customer can
    verify authenticity. `events` is a JSONB array; supported event names
    live in `app.services.webhooks_out.SUPPORTED_EVENTS`.
    """

    __tablename__ = "webhook"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    org_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("org.id", ondelete="CASCADE"), nullable=False, index=True
    )
    url: Mapped[str] = mapped_column(Text, nullable=False)
    secret: Mapped[str] = mapped_column(String(64), nullable=False)
    events: Mapped[list[str]] = mapped_column(JSONB, nullable=False)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    deliveries: Mapped[list["WebhookDelivery"]] = relationship(
        back_populates="webhook", cascade="all, delete-orphan"
    )


class WebhookDelivery(Base):
    """Per-attempt log so we can retry + display history in the UI."""

    __tablename__ = "webhook_delivery"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    webhook_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("webhook.id", ondelete="CASCADE"), nullable=False, index=True
    )
    event: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    response_status: Mapped[int | None] = mapped_column(SmallInteger)
    response_body: Mapped[str | None] = mapped_column(Text)
    error: Mapped[str | None] = mapped_column(Text)
    attempt: Mapped[int] = mapped_column(SmallInteger, default=1, nullable=False)
    delivered: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    attempted_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False, index=True
    )

    webhook: Mapped[Webhook] = relationship(back_populates="deliveries")


# ---------- Phase C.2 — GEO citation tracking ----------


class GEOQuery(Base):
    """A target query a customer wants probed against answer engines.

    `site_id` is the site whose citation rate we're tracking; one row
    per (site, query, locale) combination so the same query under en-US
    and en-IN counts separately when GEO locale-probing lands.
    """

    __tablename__ = "geo_query"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    site_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("site.id", ondelete="CASCADE"), nullable=False, index=True
    )
    target_query: Mapped[str] = mapped_column(String(512), nullable=False)
    locale: Mapped[str] = mapped_column(String(16), default="en", nullable=False)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    last_probed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_contains_site: Mapped[bool | None] = mapped_column(Boolean)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    probes: Mapped[list["GEOProbeRecord"]] = relationship(
        back_populates="query", cascade="all, delete-orphan"
    )

    __table_args__ = (
        UniqueConstraint(
            "site_id", "target_query", "locale", name="uq_geo_query_site_query_locale"
        ),
    )


class GEOProbeRecord(Base):
    """Append-only history of one answer-engine probe per row.

    `cited_urls` stores the URLs the LLM said it would cite; `contains_site`
    is a denormalised "does any cited URL share the Site's root host" so
    the citation-rate-over-time query is a single GROUP BY.
    """

    __tablename__ = "geo_probe"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    geo_query_id: Mapped[int] = mapped_column(
        ForeignKey("geo_query.id", ondelete="CASCADE"), nullable=False, index=True
    )
    provider: Mapped[str] = mapped_column(String(32), nullable=False)
    model_name: Mapped[str] = mapped_column(String(64), nullable=False)
    cited_urls: Mapped[list[str]] = mapped_column(JSONB, nullable=False)
    contains_site: Mapped[bool] = mapped_column(Boolean, nullable=False)
    site_position: Mapped[int | None] = mapped_column(SmallInteger)
    raw_response: Mapped[str | None] = mapped_column(Text)
    probed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False, index=True
    )

    query: Mapped[GEOQuery] = relationship(back_populates="probes")


# ---------- Funnel telemetry ----------


class FunnelEvent(Base):
    """Append-only conversion log. Every pipeline + API success writes one row.
    Powers the admin dashboard CTE that computes stage-to-stage rates."""

    __tablename__ = "funnel_event"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("user_.id", ondelete="SET NULL"))
    prospect_id: Mapped[int | None] = mapped_column(ForeignKey("prospect.id", ondelete="SET NULL"))
    audit_id: Mapped[int | None] = mapped_column(ForeignKey("audit.id", ondelete="SET NULL"))
    contact_id: Mapped[int | None] = mapped_column(ForeignKey("contact.id", ondelete="SET NULL"))
    stage: Mapped[FunnelStage] = mapped_column(
        Enum(FunnelStage, name="funnel_stage"), nullable=False, index=True
    )
    occurred_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False, index=True
    )
    meta: Mapped[dict[str, Any] | None] = mapped_column("metadata", JSONB)


class SeedQueryUsage(Base):
    """Per-slug usage state for the autopilot seed-query rotation.

    Catalog itself lives in `app.autopilot.seed_queries.SEED_QUERIES`;
    this table only tracks "when was this slug last used + how often" so
    the selector can rotate fairly across cron ticks.
    """

    __tablename__ = "seed_query_usage"

    slug: Mapped[str] = mapped_column(String(64), primary_key=True)
    last_used_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    use_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)


class AccountAuditLog(Base):
    """Append-only security audit trail for account-level mutations.

    Distinct from `FunnelEvent` (product analytics) and `WebhookEvent`
    (delivery idempotency). This table answers SOC 2 / GDPR "show me
    every change someone made to their own account" queries.

    `actor_user_id` — the User who performed the action; nullable
    because some events (e.g. anonymous account self-delete) have no
    surviving actor row after the cascade.

    `subject_user_id` / `subject_org_id` — the row the action targeted.
    Either may be null depending on the event type.

    `action` — short stable identifier (e.g. `account.delete`,
    `api_key.create`, `api_key.revoke`, `org.member.invite`,
    `org.member.remove`, `subscription.cancel`).

    `meta` — JSONB sidebar with the minimum context to reconstruct the
    event (e.g. `{"api_key_prefix": "ak_abc", "scopes": [...]}`). Never
    store secret material here.
    """

    __tablename__ = "account_audit_log"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    actor_user_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("user_.id", ondelete="SET NULL"), index=True
    )
    subject_user_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("user_.id", ondelete="SET NULL"), index=True
    )
    subject_org_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("org.id", ondelete="SET NULL"), index=True
    )
    action: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    request_ip: Mapped[str | None] = mapped_column(String(64))
    user_agent: Mapped[str | None] = mapped_column(String(512))
    meta: Mapped[dict[str, Any] | None] = mapped_column("metadata", JSONB)
    occurred_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False, index=True
    )
