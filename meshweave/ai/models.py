"""Pydantic response models for AAX analysis tests.

Each model defines the structured output that the LLM must return.
All categorical fields use Literal types for stability across model versions.
"""

from __future__ import annotations

from pydantic import BaseModel, Field

# --- Test 2: Homepage Comprehension ---


class HomepageComprehensionResult(BaseModel):
    """What the LLM understands from reading only the homepage."""

    brand: str = ""
    product: str = ""
    target_audience: str = ""
    key_features: list[str] = Field(default_factory=list)
    call_to_action: str = ""
    clarity: str = Field(
        default="unclear",
        description="clear | somewhat_clear | unclear",
    )
    information_density: str = Field(
        default="sparse",
        description="dense | adequate | sparse | bloated",
    )
    would_remember: bool = False


# --- Test 3: Meta Optimization ---


class MetaOptimizationResult(BaseModel):
    """How well the meta tags communicate the site's purpose."""

    brand: str = ""
    product: str = ""
    target_audience: str = ""
    would_click_through: bool = False
    completeness: str = Field(
        default="minimal",
        description="complete | partial | minimal",
    )
    clarity: str = Field(
        default="unclear",
        description="clear | somewhat_clear | unclear",
    )
    llm_optimization: str = Field(
        default="poor",
        description="optimized | adequate | poor",
    )
    missing_fields: list[str] = Field(default_factory=list)
    improvement_suggestions: list[str] = Field(default_factory=list)


# --- Test 5: Content Delta ---


class CompanyInfo(BaseModel):
    """Company overview extracted from multi-page content."""

    name: str = ""
    description: str = ""


class ProductInfo(BaseModel):
    """Product details extracted from multi-page content."""

    name: str = ""
    category: str = ""
    description: str = ""
    features: list[str] = Field(default_factory=list)


class PricingInfo(BaseModel):
    """Pricing model extracted from multi-page content."""

    model: str = ""
    tiers: list[str] = Field(default_factory=list)


class ContentDeltaResult(BaseModel):
    """What the LLM understands from reading multiple pages."""

    company: CompanyInfo = Field(default_factory=CompanyInfo)
    product: ProductInfo = Field(default_factory=ProductInfo)
    pricing: PricingInfo = Field(default_factory=PricingInfo)
    target_audience: str = ""
    strengths: list[str] = Field(default_factory=list)
    weaknesses: list[str] = Field(default_factory=list)
    coherence: str = Field(
        default="somewhat_consistent",
        description="consistent | somewhat_consistent | contradictory",
    )
    completeness: str = Field(
        default="incomplete",
        description="comprehensive | adequate | incomplete",
    )


# --- Test 6: Contactability (heuristic — no LLM) ---


class ContactabilityResult(BaseModel):
    """Heuristic score for how contactable the brand is."""

    score: float = 0.0
    has_email: bool = False
    has_mailto: bool = False
    has_contact_page: bool = False
    has_contact_point_schema: bool = False
    has_social_links: bool = False
    has_generic_email: bool = False
    has_phone: bool = False
    email_count: int = 0
    penalties: list[str] = Field(default_factory=list)


# --- Test 7: Email Validation ---


class ValidatedEmail(BaseModel):
    """A single validated email result."""

    email: str
    reason: str = ""
    contact_type: str = Field(
        default="invalid",
        description="sales | support | general | legal | invalid",
    )


class EmailValidationResult(BaseModel):
    """LLM-validated email contacts."""

    valid_contacts: list[ValidatedEmail] = Field(default_factory=list)
    best_contact: str = ""
    confidence: str = Field(
        default="low",
        description="high | medium | low",
    )


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
