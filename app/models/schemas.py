"""Pydantic request/response models for the AEGIS API."""

from collections import Counter
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

InputType = Literal["url", "text"]

SubQueryType = Literal[
    "comparative",
    "feature_specific",
    "use_case",
    "trust_signals",
    "how_to",
    "definitional",
]

ALL_SUB_QUERY_TYPES: tuple[SubQueryType, ...] = (
    "comparative",
    "feature_specific",
    "use_case",
    "trust_signals",
    "how_to",
    "definitional",
)


class AEOAnalyzeRequest(BaseModel):
    input_type: InputType
    input_value: str = Field(..., min_length=1)

    @field_validator("input_value")
    @classmethod
    def _strip_value(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("input_value must not be empty or whitespace only")
        return v


class CheckResultModel(BaseModel):
    check_id: str
    name: str
    passed: bool
    score: int
    max_score: int
    details: dict[str, Any]
    recommendation: str | None = None


class LockedCheckModel(BaseModel):
    """Placeholder shown when a Pro check is gated behind the paywall.

    The free response still lists the check_id + name so the UI can render
    a locked-state card with an upgrade CTA, but no scoring information is
    included.
    """

    check_id: str
    name: str
    locked: bool = True
    reason: str = "pro_required"


class AEOAnalyzeResponse(BaseModel):
    aeo_score: int
    band: str
    checks: list[CheckResultModel]
    suggested_target_query: str | None = None
    locked_checks: list[LockedCheckModel] | None = None
    plan: Literal["free", "pro"] = "free"


class URLFetchErrorResponse(BaseModel):
    error: str
    message: str
    detail: str


# -- Feature 2 — Query Fan-Out Engine --

# Strict parser-side schema for the LLM's raw JSON response.
# `extra="forbid"` makes hallucinated fields fail validation and trigger
# a retry, which is the core of the defensive parsing strategy.

class LLMSubQuery(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: SubQueryType
    query: str = Field(..., min_length=1)


class LLMFanoutResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    target_query: str
    sub_queries: list[LLMSubQuery]

    @model_validator(mode="after")
    def _validate_constraints(self) -> "LLMFanoutResponse":
        n = len(self.sub_queries)
        if not 10 <= n <= 15:
            raise ValueError(
                f"sub_queries must have 10–15 entries, got {n}"
            )
        type_counts = Counter(sq.type for sq in self.sub_queries)
        for required_type in ALL_SUB_QUERY_TYPES:
            if type_counts[required_type] < 2:
                raise ValueError(
                    f"type '{required_type}' must appear at least 2 times, "
                    f"got {type_counts[required_type]}"
                )
        seen: set[str] = set()
        for sq in self.sub_queries:
            if sq.query in seen:
                raise ValueError(f"duplicate sub-query string: {sq.query!r}")
            seen.add(sq.query)
        return self


# API-side schemas — what the endpoint actually returns.

class FanoutRequest(BaseModel):
    target_query: str = Field(..., min_length=1, max_length=500)
    existing_content: str | None = Field(default=None, max_length=50_000)
    target_locale: str | None = Field(default=None, max_length=16)

    @field_validator("target_query")
    @classmethod
    def _strip_query(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("target_query must not be empty or whitespace only")
        return v

    @field_validator("target_locale")
    @classmethod
    def _normalise_locale(cls, v: str | None) -> str | None:
        if v is None:
            return None
        v = v.strip().lower()
        return v or None


class SubQueryResponse(BaseModel):
    type: SubQueryType
    query: str
    covered: bool | None = None
    similarity_score: float | None = None


class GapSummary(BaseModel):
    covered: int
    total: int
    coverage_percent: int
    covered_types: list[SubQueryType]
    missing_types: list[SubQueryType]


class IntentCluster(BaseModel):
    """A cluster of semantically-similar sub-queries surfaced by Fan-Out v2.

    The cluster's `dominant_type` is the most common SubQueryType among
    its members; `member_indices` maps back into `FanoutResponse.sub_queries`
    so a UI can highlight the cluster without duplicating query strings.
    When content is provided, `covered_count` / `coverage_percent` report
    how much of the cluster the existing content already addresses — a
    cluster at 0% coverage is a "you're missing this whole intent" gap.
    """

    cluster_id: int
    dominant_type: SubQueryType
    label: str
    member_indices: list[int]
    member_count: int
    covered_count: int | None = None
    coverage_percent: int | None = None


class FanoutResponse(BaseModel):
    target_query: str
    model_used: str
    total_sub_queries: int
    sub_queries: list[SubQueryResponse]
    gap_summary: GapSummary | None = None
    intent_clusters: list[IntentCluster] | None = None


class LLMUnavailableErrorResponse(BaseModel):
    error: str
    message: str
    detail: str


# -- Phase B.1 — Rewrite assistance --

RewriteStyle = Literal["definition_first", "cause_effect", "outcome_first"]


class LLMRewriteVariant(BaseModel):
    """Strict parser-side schema for one rewrite the LLM returns."""

    model_config = ConfigDict(extra="forbid")

    style: RewriteStyle
    text: str = Field(..., min_length=1)


class LLMDirectAnswerRewrites(BaseModel):
    """Strict parser-side schema for the LLM's direct-answer rewrite payload."""

    model_config = ConfigDict(extra="forbid")

    variants: list[LLMRewriteVariant]

    @model_validator(mode="after")
    def _validate_constraints(self) -> "LLMDirectAnswerRewrites":
        if len(self.variants) != 3:
            raise ValueError(
                f"variants must have exactly 3 entries, got {len(self.variants)}"
            )
        styles = {v.style for v in self.variants}
        if len(styles) != 3:
            raise ValueError(
                "the 3 variants must use distinct `style` values"
            )
        texts = [v.text.strip() for v in self.variants]
        if len(set(texts)) != 3:
            raise ValueError("the 3 variants must have distinct `text` values")
        return self


class RewriteVariant(BaseModel):
    """API-side rewrite variant — adds the back-check signals so callers
    can render confidence per variant without re-running Check A."""

    style: RewriteStyle
    text: str
    word_count: int
    is_declarative: bool
    has_hedge_phrase: bool
    passes_check_a: bool
    check_a_score: int


class DirectAnswerRewriteRequest(BaseModel):
    input_type: InputType
    input_value: str = Field(..., min_length=1)
    target_query: str | None = Field(default=None, max_length=500)

    @field_validator("input_value")
    @classmethod
    def _strip_value(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("input_value must not be empty or whitespace only")
        return v


class DirectAnswerRewriteResponse(BaseModel):
    original_paragraph: str
    variants: list[RewriteVariant]
    model_used: str


class HeadingOperation(BaseModel):
    rule: Literal[
        "promote_to_h1",
        "demote_pre_h1",
        "demote_extra_h1",
        "collapse_skip",
    ]
    description: str
    from_level: int
    to_level: int
    index: int


class HeadingPair(BaseModel):
    level: int
    text: str


class HeadingsFixRequest(BaseModel):
    input_type: InputType
    input_value: str = Field(..., min_length=1)

    @field_validator("input_value")
    @classmethod
    def _strip_value(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("input_value must not be empty or whitespace only")
        return v


class HeadingsFixResponse(BaseModel):
    original_h_tags: list[HeadingPair]
    fixed_h_tags: list[HeadingPair]
    operations: list[HeadingOperation]
    fixed_html: str


# -- Phase B.2 — Schema generation + bulk rewrite --

DetectedIntent = Literal["FAQPage", "HowTo", "Article"]


class LLMSchemaGenResponse(BaseModel):
    """Strict parser-side schema for the LLM's JSON-LD emission."""

    model_config = ConfigDict(extra="forbid")

    json_ld: dict[str, Any] = Field(..., min_length=1)


class SchemaGenResult(BaseModel):
    intent: DetectedIntent
    types_emitted: list[str]
    json_ld: dict[str, Any]
    html_snippet: str
    model_used: str
    populated: bool


class SchemaGenRequest(BaseModel):
    input_type: InputType
    input_value: str = Field(..., min_length=1)
    intent: DetectedIntent | None = None

    @field_validator("input_value")
    @classmethod
    def _strip_value(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("input_value must not be empty or whitespace only")
        return v


class BulkRewriteRequest(BaseModel):
    input_type: InputType
    input_value: str = Field(..., min_length=1)
    target_query: str | None = Field(default=None, max_length=500)
    include_direct_answer: bool = True
    include_headings: bool = True
    include_schema: bool = True

    @field_validator("input_value")
    @classmethod
    def _strip_value(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("input_value must not be empty or whitespace only")
        return v


class BulkRewriteSection(BaseModel):
    """Per-fix section in the bulk-rewrite envelope.

    `status` enum:
      - "applied"  — the fix ran and produced output.
      - "skipped"  — the corresponding check already passed; no rewrite needed.
      - "failed"   — the rewriter ran but couldn't produce a valid fix.
      - "disabled" — the caller flagged the fix off in the request.
    """

    name: Literal["direct_answer", "headings", "schema"]
    status: Literal["applied", "skipped", "failed", "disabled"]
    detail: str | None = None
    payload: dict[str, Any] | None = None


class BulkRewriteResponse(BaseModel):
    aeo_score_before: int
    aeo_score_after_estimate: int
    band_before: str
    band_after_estimate: str
    sections: list[BulkRewriteSection]
    markdown_diff: str
    html_diff: str
    model_used: str | None = None


# -- Phase B.3 — Internal-linking suggestions --


class PageRecordModel(BaseModel):
    url: str = Field(..., min_length=1)
    title: str | None = None
    excerpt: str = Field(..., min_length=1)


class LinkSuggestion(BaseModel):
    target_url: str
    title: str | None
    anchor_text: str
    similarity_score: float
    matched_subquery: str
    matched_cluster_id: int | None = None


class LinkingSuggestRequest(BaseModel):
    """Request body for `POST /api/linking/suggest`.

    Caller supplies the sub-queries to find links for and *one* of:
      - `sitemap_url`: AEGIS will fetch the sitemap + each page.
      - `pages`:       pre-fetched records (used by tests and by
                       integrations that already have content cached).
    """

    sub_queries: list[str] = Field(..., min_length=1)
    sitemap_url: str | None = None
    pages: list[PageRecordModel] | None = None
    source_url: str | None = None
    top_k: int = Field(default=3, ge=1, le=10)
    min_similarity: float = Field(default=0.40, ge=0.0, le=1.0)
    max_pages: int = Field(default=50, ge=1, le=500)

    @model_validator(mode="after")
    def _exactly_one_source(self) -> "LinkingSuggestRequest":
        sources_provided = bool(self.sitemap_url) + bool(self.pages)
        if sources_provided == 0:
            raise ValueError(
                "provide either `sitemap_url` or `pages` so the index has content"
            )
        if sources_provided == 2:
            raise ValueError(
                "`sitemap_url` and `pages` are mutually exclusive; pick one"
            )
        return self


class LinkingSuggestResponse(BaseModel):
    pages_indexed: int
    failed_urls: list[dict[str, str]] = Field(default_factory=list)
    suggestions: list[LinkSuggestion]


# -- Phase C.1 — Site ingest + dashboard --

from uuid import UUID as _UUID  # noqa: E402 — defer to avoid top-level cycle perception


class SiteIngestRequest(BaseModel):
    root_url: str = Field(..., min_length=1)
    sitemap_url: str | None = None
    page_urls: list[str] | None = None
    competitor_root_urls: list[str] | None = None
    org_id: _UUID | None = None
    max_pages: int = Field(default=200, ge=1, le=2000)

    @model_validator(mode="after")
    def _exactly_one_source(self) -> "SiteIngestRequest":
        if not (self.sitemap_url or self.page_urls):
            raise ValueError("provide either `sitemap_url` or `page_urls`")
        if self.sitemap_url and self.page_urls:
            raise ValueError("`sitemap_url` and `page_urls` are mutually exclusive")
        return self


class SiteIngestResponse(BaseModel):
    site_id: _UUID
    root_url: str
    pages_total: int
    pages_added: int
    pages_existing: int
    org_id: _UUID | None = None


class SiteAuditRequest(BaseModel):
    limit: int = Field(default=20, ge=1, le=500)
    concurrency: int = Field(default=4, ge=1, le=16)


class SiteAuditPageResult(BaseModel):
    page_id: int
    url: str
    score: int | None
    band: str | None
    error: str | None = None


class SiteAuditResponse(BaseModel):
    site_id: _UUID
    audited: list[SiteAuditPageResult]
    failed: list[SiteAuditPageResult]


class SiteDashboardWorstPage(BaseModel):
    page_id: int
    url: str
    title: str | None
    score: int
    band: str
    failed_checks: list[str]


class SiteDashboardMissingCluster(BaseModel):
    type: str
    pages_missing: int


class SiteDashboardTrendPoint(BaseModel):
    day: str  # ISO date
    mean_score: float
    audits: int


class SiteDashboardResponse(BaseModel):
    site_id: _UUID
    root_url: str
    pages_total: int
    pages_audited: int
    mean_score: float | None
    median_score: float | None
    band_counts: dict[str, int]
    last_audited_at: str | None
    trend_7d: list[SiteDashboardTrendPoint]
    worst_pages: list[SiteDashboardWorstPage]
    missing_clusters: list[SiteDashboardMissingCluster]


# -- Phase C.2 — Competitor benchmarking --


class CompetitorRowResponse(BaseModel):
    root_url: str
    site_id: _UUID | None
    pages_total: int
    pages_audited: int
    mean_score: float | None
    median_score: float | None
    band_counts: dict[str, int]
    top_missing_types: list[tuple[str, int]]
    last_audited_at: str | None
    status: str


class CompetitorBenchmarkResponse(BaseModel):
    site_id: _UUID
    root_url: str
    primary: CompetitorRowResponse
    competitors: list[CompetitorRowResponse]
    delta_mean_score: dict[str, float | None]
    intent_gap: dict[str, list[str]]


class CompetitorIngestRequest(BaseModel):
    """Optional pre-supplied page URLs per competitor. Keys are competitor
    root URLs; values are the page URLs to seed `SitePage` rows for that
    competitor. Pages aren't audited synchronously — the next site-audit
    cron picks them up."""

    page_urls_per_competitor: dict[str, list[str]] | None = None


# -- Phase C.2 — GEO citation tracking --


class GEOQueryCreate(BaseModel):
    target_query: str = Field(..., min_length=1, max_length=512)
    locale: str = Field(default="en", min_length=1, max_length=16)


class GEOQueryRecord(BaseModel):
    id: int
    site_id: _UUID
    target_query: str
    locale: str
    enabled: bool
    last_probed_at: str | None
    last_contains_site: bool | None


class GEOProbeResultRecord(BaseModel):
    id: int
    geo_query_id: int
    provider: str
    model_name: str
    cited_urls: list[str]
    contains_site: bool
    site_position: int | None
    probed_at: str
    from_cache: bool = False
    cached_age_seconds: int | None = None
    locale: str | None = None
    egress_country: str | None = None
    egress_region: str | None = None


class GEOWeeklyRate(BaseModel):
    week_start: str
    probes: int
    cited: int
    rate: float


class GEOCitationHistoryResponse(BaseModel):
    site_id: _UUID
    geo_query_id: int
    target_query: str
    weekly: list[GEOWeeklyRate]
    overall_window_days: int
    overall_probes: int
    overall_cited: int
    overall_rate: float


# -- Phase D — Org / collaboration --

OrgRoleLiteral = Literal["owner", "editor", "viewer"]


class OrgCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=128)
    slug: str | None = Field(default=None, max_length=64)
    logo_url: str | None = None


class OrgRecord(BaseModel):
    id: _UUID
    name: str
    slug: str
    logo_url: str | None
    branded_subdomain: str | None
    created_at: str


class OrgMemberRecord(BaseModel):
    id: _UUID
    user_id: _UUID
    role: OrgRoleLiteral
    invited_email: str | None
    accepted_at: str | None
    created_at: str


class OrgInviteRequest(BaseModel):
    """Invite a teammate to an org.

    Accepts either `email` (preferred, used by the Customer Console UI —
    a placeholder User row is upserted if no account exists yet) or the
    legacy `user_id` form (kept for the existing API + tests). Exactly
    one of the two must be set.
    """

    user_id: _UUID | None = None
    email: str | None = Field(default=None, max_length=320)
    role: OrgRoleLiteral


class CommentCreateRequest(BaseModel):
    body: str = Field(..., min_length=1, max_length=4000)
    check_id: str | None = Field(default=None, max_length=64)


class CommentRecord(BaseModel):
    id: int
    site_page_audit_id: int
    author_user_id: str | None
    check_id: str | None
    body: str
    resolved: bool
    created_at: str | None


class CommentResolveRequest(BaseModel):
    resolved: bool = True


# -- Phase E — REST API keys + webhooks-out --


class ApiKeyCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=64)
    scopes: list[str] = Field(..., min_length=1)


class ApiKeyMintedRecord(BaseModel):
    """One-time response containing the plaintext token. Show in UI then
    discard — we don't persist the plaintext."""

    id: _UUID
    org_id: _UUID
    name: str
    prefix: str
    scopes: list[str]
    plaintext: str


class ApiKeyRecord(BaseModel):
    id: _UUID
    name: str
    prefix: str
    scopes: list[str]
    last_used_at: str | None
    revoked_at: str | None
    created_at: str


class WebhookCreate(BaseModel):
    url: str = Field(..., min_length=1)
    events: list[str] = Field(..., min_length=1)


class WebhookRecord(BaseModel):
    id: _UUID
    org_id: _UUID
    url: str
    events: list[str]
    enabled: bool
    created_at: str


class WebhookCreatedRecord(WebhookRecord):
    """Includes the signing secret. Shown ONCE at creation."""

    secret: str
