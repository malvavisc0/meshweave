"""Pydantic response models for AAX analysis tests.

Each model defines the structured output that the LLM must return.
Every field the LLM fills is REQUIRED, with no default. Structured output
sends the model a JSON schema, and a field with a default is optional in
that schema, so the model may simply omit it (observed: answerability
answers came back with no source_pages, which zeroed the factor). An
empty string or empty list is still a legitimate "not found" answer; it
just has to be stated. Categorical fields use Literal types, so an
off-enum verdict fails validation and triggers the retry.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

# --- Test 2: Homepage Comprehension ---


class HomepageComprehensionResult(BaseModel):
    """What the LLM understands from reading only the homepage."""

    brand: str
    product: str
    target_audience: str
    key_features: list[str]
    call_to_action: str
    clarity: Literal["clear", "somewhat_clear", "unclear"]
    information_density: Literal["dense", "adequate", "sparse", "bloated"]
    would_remember: bool


# --- Test 3: Meta Optimization ---


class MetaOptimizationResult(BaseModel):
    """How well the meta tags communicate the site's purpose."""

    would_click_through: bool
    completeness: Literal["complete", "partial", "minimal"]
    clarity: Literal["clear", "somewhat_clear", "unclear"]
    llm_optimization: Literal["optimized", "adequate", "poor"]
    improvement_suggestions: list[str]


# --- Test 5: Content Delta ---


class CompanyInfo(BaseModel):
    """Company name extracted from multi-page content."""

    name: str


class ProductInfo(BaseModel):
    """Product name extracted from multi-page content."""

    name: str


class PricingInfo(BaseModel):
    """Pricing model extracted from multi-page content."""

    model: str | None


class ContentDeltaResult(BaseModel):
    """What the LLM understands from reading multiple pages."""

    company: CompanyInfo
    product: ProductInfo
    pricing: PricingInfo
    target_audience: str
    strengths: list[str]
    weaknesses: list[str]
    coherence: Literal["consistent", "somewhat_consistent", "contradictory"]
    completeness: Literal["comprehensive", "adequate", "incomplete"]


# --- Test 6: Contactability (heuristic — no LLM) ---


OBFUSCATED_EMAIL_PENALTY = "All emails are obfuscated-only (no mailto links)"
LEGAL_ONLY_EMAIL_PENALTY = (
    "Emails only found on legal pages (not intended as contact points)"
)
SAME_DOMAIN_EMAIL_PENALTY = "No same-domain email addresses found"
SAME_DOMAIN_EMAIL_CAP = 20

# Contactability heuristic point values, shared with the fix generator.
SAME_DOMAIN_EMAIL_POINTS = 20
THIRD_PARTY_EMAIL_POINTS = 5
MAILTO_POINTS = 10
CONTACT_PAGE_POINTS = 10
LISTED_EMAIL_POINTS = 15
CONTACT_POINT_SCHEMA_POINTS = 15
SOCIAL_LINK_POINTS = 10
OBFUSCATED_EMAIL_PENALTY_POINTS = 10
LEGAL_ONLY_EMAIL_PENALTY_POINTS = 15


class ContactabilityResult(BaseModel):
    """Heuristic score for how contactable the brand is.

    ``penalty_points`` maps each applied penalty reason to the points it
    removed from the score.
    """

    score: float = 0.0
    has_email: bool = False
    has_mailto: bool = False
    has_contact_page: bool = False
    has_contact_point_schema: bool = False
    has_social_links: bool = False
    email_count: int = 0
    penalties: list[str] = Field(default_factory=list)
    penalty_points: dict[str, float] = Field(default_factory=dict)


# --- Test 7: Email Validation ---


class ValidatedEmail(BaseModel):
    """A single validated email result."""

    email: str
    reason: str
    contact_type: Literal["sales", "support", "general", "legal", "invalid"]


class EmailValidationResult(BaseModel):
    """LLM-validated email contacts."""

    valid_contacts: list[ValidatedEmail]
    rejected_contacts: list[ValidatedEmail]
    best_contact: str | None
    confidence: Literal["high", "medium", "low"]


# --- Grounded Answerability Test ---


AnswerabilityVerdict = Literal[
    "supported",
    "partially_supported",
    "unsupported",
    "contradictory",
    "not_applicable",
]


class AnswerabilityAnswerResult(BaseModel):
    """One grounded answer attempt for a benchmark question.

    The model answers using ONLY the crawled pages and reports the
    supporting pages and one verdict. The answer is stored downstream
    only when it is grounded in the crawl (see the answerability
    orchestrator).
    """

    answer: str
    verdict: AnswerabilityVerdict
    source_pages: list[str]
    missing_facts: list[str]


class AnswerabilityQuestionResult(BaseModel):
    """Stored per-question record of the answerability exercise."""

    question_id: str
    question: str
    answer: str = ""
    verdict: AnswerabilityVerdict
    source_pages: list[str] = Field(default_factory=list)
    missing_facts: list[str] = Field(default_factory=list)
    error: str = ""


class AnswerabilityResult(BaseModel):
    """Aggregate grounded answerability test over the crawled pages."""

    status: Literal["completed", "skipped", "failed"] = "skipped"
    skip_reason: str = ""
    question_count: int = 0
    answer_support: float = 0.0
    evidence_coverage: float = 0.0
    questions: list[AnswerabilityQuestionResult] = Field(default_factory=list)


# --- AAX Aggregate ---


class AAXSummaryResult(BaseModel):
    """One-line diagnostic verdict for the hero card."""

    summary: str = ""


class AAXAnalysisResult(BaseModel):
    """Aggregate result of all AAX tests."""

    status: str = "pending"
    model_id: str = ""
    tests_completed: int = 0
    tests_skipped: int = 0
    homepage_comprehension: HomepageComprehensionResult | None = None
    meta_optimization: MetaOptimizationResult | None = None
    content_delta: ContentDeltaResult | None = None
    contactability: ContactabilityResult | None = None
    email_validation: EmailValidationResult | None = None
    answerability: AnswerabilityResult | None = None
    summary: str = ""
    skip_reasons: dict[str, str] = Field(default_factory=dict)
