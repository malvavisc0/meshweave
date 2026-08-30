"""Pydantic response models for AAX analysis tests.

Each model defines the structured output that the LLM must return.
Categorical fields use Literal types and are REQUIRED: an off-enum or
omitted verdict fails validation and triggers the structured-output
retry instead of silently scoring as a default downstream. Free-text
extraction fields (brand, product, …) keep empty-string defaults
because "not found on the page" is a legitimate answer.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

# --- Test 2: Homepage Comprehension ---


class HomepageComprehensionResult(BaseModel):
    """What the LLM understands from reading only the homepage."""

    brand: str = ""
    product: str = ""
    target_audience: str = ""
    key_features: list[str] = Field(default_factory=list)
    call_to_action: str = ""
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
    improvement_suggestions: list[str] = Field(default_factory=list)


# --- Test 5: Content Delta ---


class CompanyInfo(BaseModel):
    """Company name extracted from multi-page content."""

    name: str = ""


class ProductInfo(BaseModel):
    """Product name extracted from multi-page content."""

    name: str = ""


class PricingInfo(BaseModel):
    """Pricing model extracted from multi-page content."""

    model: str | None = None


class ContentDeltaResult(BaseModel):
    """What the LLM understands from reading multiple pages."""

    company: CompanyInfo = Field(default_factory=CompanyInfo)
    product: ProductInfo = Field(default_factory=ProductInfo)
    pricing: PricingInfo = Field(default_factory=PricingInfo)
    target_audience: str = ""
    strengths: list[str] = Field(default_factory=list)
    weaknesses: list[str] = Field(default_factory=list)
    coherence: Literal["consistent", "somewhat_consistent", "contradictory"]
    completeness: Literal["comprehensive", "adequate", "incomplete"]


# --- Test 6: Contactability (heuristic — no LLM) ---


class ContactabilityResult(BaseModel):
    """Heuristic score for how contactable the brand is."""

    score: float = 0.0
    has_email: bool = False
    has_mailto: bool = False
    has_contact_page: bool = False
    has_contact_point_schema: bool = False
    has_social_links: bool = False
    email_count: int = 0
    penalties: list[str] = Field(default_factory=list)


# --- Test 7: Email Validation ---


class ValidatedEmail(BaseModel):
    """A single validated email result."""

    email: str
    reason: str = ""
    contact_type: Literal["sales", "support", "general", "legal", "invalid"]


class EmailValidationResult(BaseModel):
    """LLM-validated email contacts."""

    valid_contacts: list[ValidatedEmail] = Field(default_factory=list)
    rejected_contacts: list[ValidatedEmail] = Field(default_factory=list)
    best_contact: str | None = None
    confidence: Literal["high", "medium", "low"]


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
    llms_txt: dict | None = None
    summary: str = ""
    skip_reasons: dict[str, str] = Field(default_factory=dict)
