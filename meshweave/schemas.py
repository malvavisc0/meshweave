from typing import Any

from pydantic import BaseModel, Field


class OGMeta(BaseModel):
    """Open Graph metadata."""

    title: str = ""
    description: str = ""
    image: str = ""
    url: str = ""


class TwitterMeta(BaseModel):
    """Twitter Card metadata."""

    card: str = ""
    title: str = ""
    description: str = ""
    image: str = ""


class PageMeta(BaseModel):
    """Full page metadata extracted from HTML."""

    title: str = ""
    description: str = ""
    og: OGMeta = Field(default_factory=OGMeta)
    twitter: TwitterMeta = Field(default_factory=TwitterMeta)
    canonical: str = ""
    jsonld: list[dict[str, Any]] = Field(default_factory=list)


class Headings(BaseModel):
    """Heading hierarchy extracted from a page."""

    h1: list[str] = Field(default_factory=list)
    h2: list[str] = Field(default_factory=list)
    h3: list[str] = Field(default_factory=list)
    h4: list[str] = Field(default_factory=list)
    h5: list[str] = Field(default_factory=list)
    h6: list[str] = Field(default_factory=list)
    depth: int = 0
    h1_count: int = 0
    total: int = 0


class ContentMetrics(BaseModel):
    """Content richness metrics for a page."""

    words: int = 0
    paragraphs: int = 0
    lists: int = 0
    tables: int = 0
    code_blocks: int = 0
    images_total: int = 0
    images_with_alt: int = 0
    headings: int = 0


class MarkdownEntry(BaseModel):
    """A crawled page's markdown output (file-backed, CLI format).

    The CLI writes markdown to disk and replaces the inline text
    with file metadata.  For the raw API shape (with ``markdown``
    text inline), see :class:`RawMarkdownEntry`.
    """

    path: str
    lines: int
    size_bytes: int
    page: PageMeta = Field(default_factory=PageMeta)
    headings: Headings | None = None
    content_metrics: ContentMetrics | None = None


class RawMarkdownEntry(BaseModel):
    """A crawled page's markdown output (API/raw format).

    Used by :func:`meshweave.core.crawl` before CLI post-processing.
    """

    markdown: str = ""
    page: PageMeta = Field(default_factory=PageMeta)
    headings: Headings | None = None
    content_metrics: ContentMetrics | None = None


class Links(BaseModel):
    """Internal and external links from the start page."""

    internal: list[str] = Field(default_factory=list)
    external: list[str] = Field(default_factory=list)


class RenderMetrics(BaseModel):
    """Browser render performance metrics."""

    final_url: str = ""
    response_status: int = 0
    network_requests: int = 0
    content_length: int = 0
    load_time_ms: float = 0.0
    cache_hit: bool = False
    errors: list[str] = Field(default_factory=list)


class ExtractionMetrics(BaseModel):
    """Link extraction performance metrics."""

    total_candidates: int = 0
    unique_total: int = 0
    internal_count: int = 0
    external_count: int = 0
    base_domain: str = ""
    parse_time_ms: float = 0.0


class CrawlMetrics(BaseModel):
    """Combined render and extraction metrics."""

    render: RenderMetrics = Field(default_factory=RenderMetrics)
    extraction: ExtractionMetrics = Field(default_factory=ExtractionMetrics)


class EmailSource(BaseModel):
    """A single email occurrence found on a page."""

    email: str
    url: str
    found_as: list[str] = Field(default_factory=list)


class EmailCounts(BaseModel):
    """Aggregate email counts."""

    total_unique: int = 0
    total_mentions: int = 0


class Emails(BaseModel):
    """All email extraction results."""

    unique: list[str] = Field(default_factory=list)
    by_url: dict[str, list[str]] = Field(default_factory=dict)
    sources: list[EmailSource] = Field(default_factory=list)
    counts: EmailCounts = Field(default_factory=EmailCounts)


class CrawlLimits(BaseModel):
    """Crawl configuration limits."""

    max_pages: int = 25


class SitemapMeta(BaseModel):
    """Sitemap discovery metadata."""

    used: bool = False
    sources: list[str] = Field(default_factory=list)
    urls_seeded: int = 0
    discovered: int = 0


class CrawlInfo(BaseModel):
    """Crawl run summary."""

    enabled: bool = False
    start_url: str = ""
    visited: list[str] = Field(default_factory=list)
    limits: CrawlLimits = Field(default_factory=CrawlLimits)
    reason_stopped: str = ""
    sitemap: SitemapMeta = Field(default_factory=SitemapMeta)


class RobotsInfo(BaseModel):
    """robots.txt analysis result."""

    url: str = ""
    exists: bool = False
    bots: dict[str, str] = Field(default_factory=dict)
    sitemaps: list[str] = Field(default_factory=list)
    size_bytes: int = 0


class LLMsTxtEntry(BaseModel):
    """A single llms.txt / llms-full.txt file."""

    exists: bool = False
    url: str | None = None
    size_bytes: int = 0
    content_preview: str = ""


class LLMsTxt(BaseModel):
    """llms.txt availability and content."""

    llms_txt: LLMsTxtEntry = Field(default_factory=LLMsTxtEntry)
    llms_full_txt: LLMsTxtEntry = Field(default_factory=LLMsTxtEntry)


class FAQQuestion(BaseModel):
    """A single FAQ question/answer pair from JSON-LD."""

    question: str = ""
    answer_words: int = 0
    in_optimal_range: bool = False


class FAQAnalysis(BaseModel):
    """Cross-page FAQ schema analysis for AEO scoring."""

    count: int = 0
    avg_answer_words: float = 0.0
    answers_in_optimal_range: int = 0
    answers_too_short: int = 0
    answers_too_long: int = 0
    optimal_range: str = ""
    details: list[FAQQuestion] = Field(default_factory=list)


class CanonicalIssue(BaseModel):
    """A page whose canonical URL doesn't match its own URL."""

    page: str
    canonical: str


class AuditMeta(BaseModel):
    """OG/meta uniqueness audit across crawled pages."""

    duplicate_og_titles: dict[str, list[str]] = Field(default_factory=dict)
    duplicate_og_descriptions: dict[str, list[str]] = Field(default_factory=dict)
    canonical_issues: list[CanonicalIssue] = Field(default_factory=list)
    unique_og_titles: int = 0
    unique_og_descriptions: int = 0
    total_pages_checked: int = 0


class AuditEntity(BaseModel):
    """Organization entity consistency audit."""

    name: str | None = None
    name_consistent: bool = True
    name_variants: dict[str, int] = Field(default_factory=dict)
    description_consistent: bool = True
    description_variants: list[str] = Field(default_factory=list)
    same_as: list[str] = Field(default_factory=list)
    pages_with_org_schema: int = 0


class AuditSchemaCoverage(BaseModel):
    """JSON-LD schema type coverage across crawled pages."""

    pages_with_schema: int = 0
    pages_without_schema: int = 0
    coverage_pct: float = 0.0
    type_counts: dict[str, int] = Field(default_factory=dict)
    per_page: dict[str, list[str]] = Field(default_factory=dict)


class Audit(BaseModel):
    """Cross-page audit results."""

    meta: AuditMeta = Field(default_factory=AuditMeta)
    entity: AuditEntity = Field(default_factory=AuditEntity)
    schema_coverage: AuditSchemaCoverage = Field(default_factory=AuditSchemaCoverage)


class CrawlOutput(BaseModel):
    """Full crawl output as produced by the CLI (``-o output.json``).

    This models the **file-backed** format where markdown content
    has been written to disk and replaced with file metadata.
    """

    page: PageMeta = Field(default_factory=PageMeta)
    markdowns: dict[str, MarkdownEntry] = Field(default_factory=dict)
    links: Links = Field(default_factory=Links)
    metrics: CrawlMetrics = Field(default_factory=CrawlMetrics)
    emails: Emails | None = None
    crawl: CrawlInfo = Field(default_factory=CrawlInfo)
    robots: RobotsInfo | None = None
    llms_txt: LLMsTxt | None = None
    faq_analysis: FAQAnalysis | None = None
    audit: Audit = Field(default_factory=Audit)
    markdown_dir: str = ""


class CrawlAPIOutput(BaseModel):
    """Full crawl output as returned by the FastAPI server.

    This models the **raw** format where markdown content is
    included inline in each ``markdowns`` entry.
    """

    page: PageMeta = Field(default_factory=PageMeta)
    markdowns: dict[str, RawMarkdownEntry] = Field(default_factory=dict)
    links: Links = Field(default_factory=Links)
    metrics: CrawlMetrics = Field(default_factory=CrawlMetrics)
    emails: Emails | None = None
    crawl: CrawlInfo = Field(default_factory=CrawlInfo)
    robots: RobotsInfo | None = None
    llms_txt: LLMsTxt | None = None
    faq_analysis: FAQAnalysis | None = None
    audit: Audit = Field(default_factory=Audit)
