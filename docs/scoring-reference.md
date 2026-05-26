# Scoring Reference

> How AAX, AEO, and GEO scores are calculated in the Meshweave scoring engine.
>
> Source: `meshweave/scoring/engine.py`, `meshweave/scoring/aeo.py`, `meshweave/scoring/geo.py`

---

## Shared Algorithm: Weighted Composite

All three scores use the same weighted composite function with **re-normalization**:

```python
composite = Σ(score_i × weight_i) / Σ(weight_i)    # only for factors with non-None scores
```

- Factors with `score: None` (e.g. manual inputs not yet provided, or freshness with no dates) are **excluded** and their weight is redistributed proportionally among available factors.
- A **calibration curve** is applied after the weighted average to compress the upper range and prevent score inflation for average sites:
  ```python
  calibrated = 100.0 * (composite / 100.0) ** 1.15
  ```
  This pulls 80→73, 70→62, 60→51, 50→42, 40→33 while leaving 100 untouched.
- The result is capped at 100 and rounded to 1 decimal place.
- Each score also produces an **auto-only composite** that excludes manual-input factors.

**Source:** `meshweave/scoring/engine.py` → `_weighted_composite()`

---

## AEO — Answer Engine Optimization

**Purpose:** How well content is structured for AI answer engines (featured snippets, AI Overviews, voice assistants).

**Composite = 100%, 6 factors:**

| # | Factor | Weight | Auto? | Source |
|---|--------|--------|-------|--------|
| A4 | Capture Rate | 30% | ❌ Manual | User enters % of keywords in featured snippets/AI Overviews |
| A1 | Schema Implementation | 20% | ✅ Auto | `aeo.score_schema()` |
| A2 | Content Structure | 20% | ✅ Auto | `aeo.score_content_structure()` |
| A5 | Query Match | 15% | ❌ Manual | User estimates content-to-query alignment |
| A6 | Voice Rate | 10% | ❌ Manual | User tests voice assistant responses |
| A3 | Freshness | 5% | ✅ Auto | `aeo.score_freshness()` |

### A1. Schema Implementation (20%)

```
Base score = schema_coverage.coverage_pct (0-100)
+10 if FAQPage schema present
+5  if HowTo schema present
+10 if faq_analysis.answers_in_optimal_range > 0
Cap: 100
```

**Source:** `meshweave/scoring/aeo.py` → `score_schema()`

### A2. Content Structure Quality (20%)

Per-page scoring (0-100), then **averaged across all pages**:

| Check | Points |
|-------|--------|
| Single H1 tag (`h1_count == 1`) | +15 |
| Heading depth ≥ 2 | +15 |
| Has lists (`lists > 0`) | +10 |
| Has tables (`tables > 0`) | +10 |
| Word count ≥ 300 | +15 |
| Word count ≥ 1000 | +10 bonus |
| Images with alt text ≥ 80% | +10 |
| Paragraphs ≥ 5 | +10 |
| Headings total ≥ 5 | +10 |

**Source:** `meshweave/scoring/aeo.py` → `score_content_structure()`, `_score_single_page()`

### A3. Freshness (5%)

Extracts `datePublished` / `dateModified` from JSON-LD across all pages:

| Avg days since publication | Score |
|---------------------------|-------|
| ≤ 30 days | 100 |
| 31–90 days | 80 |
| 91–180 days | 60 |
| 181–365 days | 40 |
| > 365 days | 20 |
| No dates found | `None` (excluded from composite) |

**Source:** `meshweave/scoring/aeo.py` → `score_freshness()`

### Rating Scale

| Range | Label |
|-------|-------|
| 0–25 | Poor |
| 26–45 | Below Average |
| 46–65 | Average |
| 66–85 | Strong |
| 86–100 | Excellent |

---

## GEO — Generative Engine Optimization

**Purpose:** How well content is positioned to be cited/referenced by LLMs (ChatGPT, Claude, Perplexity).

**Composite = 100%, 6 factors:**

| # | Factor | Weight | Auto? | Source |
|---|--------|--------|-------|--------|
| G1 | Citation | 30% | ❌ Manual | User estimates citation frequency across LLMs |
| G2 | Topical Authority | 20% | ✅ Auto | `geo.score_topical_authority()` |
| G3 | E-E-A-T Signals | 15% | ✅ Auto | `geo.score_eeat()` |
| G4 | Crawl Access | 15% | ✅ Auto | `geo.score_crawl_access()` |
| G5 | Content Depth | 10% | ✅ Auto | `geo.score_content_depth()` |
| G6 | Entity Consistency | 10% | ✅ Auto | `geo.score_entity_consistency()` |

### G2. Topical Authority (20%)

Weighted blend of 6 sub-factors:

| Sub-factor | Sub-weight | Calculation |
|-----------|------------|-------------|
| Schema coverage % | 0.30 | Direct from `schema_coverage.coverage_pct` |
| Schema type diversity | 0.20 | `min(unique_types / 10, 1.0) × 100` |
| Entity name consistent | 0.15 | 100 if consistent, else 0 |
| Description consistent | 0.15 | 100 if consistent, else 0 |
| sameAs link count | 0.10 | 0 links=0, 1-2=40, 3-5=70, 6+=100 |
| Content page ratio | 0.10 | Pages with >300 words / total pages × 100 |

**Source:** `meshweave/scoring/geo.py` → `score_topical_authority()`

### G3. E-E-A-T Signals (15%)

Additive scoring:

| Signal | Points |
|--------|--------|
| Organization schema present | +15 |
| Author info in articles | +15 |
| Review/rating schema | +15 |
| sameAs links (social profiles) | +10 |
| Contact page exists | +8 |
| Privacy/terms pages exist | +7 |
| Video content schema | +5 |

**Cap:** 100. Schema type matching is case-insensitive.

**Source:** `meshweave/scoring/geo.py` → `score_eeat()`

### G4. LLM Crawl Accessibility (15%)

Additive scoring from robots.txt and llms.txt data:

| Signal | Points |
|--------|--------|
| robots.txt exists | +8 |
| GPTBot allowed | +15 |
| ClaudeBot allowed | +12 |
| PerplexityBot allowed | +12 |
| llms.txt exists | +15 |
| llms-full.txt exists | +8 |
| XML sitemap present | +7 |

**Cap:** 100. Returns `None` if robots/llms data is placeholder (page-scope crawl).

Bot status matching uses substring: `"allow" in status` to handle "allow", "allowed", "Allow", etc.

**Source:** `meshweave/scoring/geo.py` → `score_crawl_access()`

### G5. Content Depth & Originality (10%)

Weighted blend:

| Component | Weight | Calculation |
|-----------|--------|-------------|
| Avg word count tier | 0.35 | <200→10, <500→30, <1000→50, <2000→70, <5000→90, ≥5000→100 |
| Pages with 1000+ words ratio | 0.25 | `(pages_1000+ / total) × 100` |
| Content pages ratio (>200 words) | 0.15 | `(pages_200+ / total) × 100` |
| Has code blocks | 0.15 | 100 if any page has code blocks, else 0 |
| Has tables | 0.10 | 100 if any page has tables, else 0 |

Pages are deduplicated across `markdowns` and `pages` sources by URL.

**Source:** `meshweave/scoring/geo.py` → `score_content_depth()`

### G6. Entity Consistency (10%)

Additive scoring:

| Signal | Points |
|--------|--------|
| Entity name consistent across pages | +20 |
| Description consistent across pages | +15 |
| sameAs links: 0 | +0 |
| sameAs links: 1–2 | +20 |
| sameAs links: 3–4 | +30 |
| sameAs links: 5+ | +40 |

**Cap:** 100.

**Source:** `meshweave/scoring/geo.py` → `score_entity_consistency()`

### Rating Scale

| Range | Label |
|-------|-------|
| 0–25 | Invisible |
| 26–45 | Emerging |
| 46–65 | Visible |
| 66–85 | Authoritative |
| 86–100 | Dominant |

---

## AAX — AI Agent Experience

**Purpose:** How well an AI agent can understand, evaluate, and recommend a website. Computed from LLM-powered analysis tests (requires `--ai-analysis` flag or `AAX_ENABLED=true`).

**Composite = 100%, 5 factors:**

| # | Factor | Weight | Auto? | Source |
|---|--------|--------|-------|--------|
| T2 | Homepage Comprehension | 30% | ✅ Auto | LLM reads homepage markdown |
| T3 | Meta Optimization | 20% | ✅ Auto | LLM reads meta tags only |
| T5 | Content Delta | 20% | ✅ Auto | LLM reads multiple pages |
| T4 | llms.txt | 15% | ✅ Auto | Heuristic (no LLM) |
| T7 | Email Validation | 15% | ✅ Auto | LLM validates email addresses |

### T2. Homepage Comprehension (30%)

LLM reads the homepage markdown and extracts: brand, product, target audience, key features, call-to-action, clarity, information density, memorability.

**Scoring formula:**

```
field_completeness × 0.4    # how many of {brand, product, audience, CTA} were extracted
+ clarity × 0.2             # clear=100, somewhat_clear=50, unclear=15
+ density × 0.2             # dense=100, adequate=60, sparse=25, bloated=15
+ features_score × 0.1      # min(feature_count × 15, 60)
+ remember × 0.1            # true=100, false=0
```

**Source:** `meshweave/scoring/engine.py` → `compute_aax_score()`, `meshweave/ai/prompts.py` → `homepage_comprehension_prompt()`

### T3. Meta Optimization (20%)

LLM reads only the meta tags (title, description, OG tags, JSON-LD summary) and evaluates completeness, clarity, and LLM optimization.

**Scoring formula:**

```
field_completeness × 0.4    # how many of {brand, product, audience} were extracted
+ completeness × 0.2        # complete=100, partial=50, minimal=15
+ clarity × 0.15            # clear=100, somewhat_clear=50, unclear=15
+ llm_optimization × 0.15   # optimized=100, adequate=50, poor=15
+ click_through × 0.1       # true=100, false=0
```

**Source:** `meshweave/scoring/engine.py` → `compute_aax_score()`, `meshweave/ai/prompts.py` → `meta_optimization_prompt()`

### T5. Content Delta (20%)

LLM reads multiple pages (selected by `select_pages_for_analysis()` within a token budget) and produces a comprehensive summary.

**Scoring formula:**

```
info_richness × 0.4         # how many of 7 fields extracted (company name, product name/desc/features, pricing, audience, strengths)
+ coherence × 0.3           # consistent=100, somewhat_consistent=50, contradictory=15
+ completeness × 0.3        # comprehensive=100, adequate=50, incomplete=15
```

**Source:** `meshweave/scoring/engine.py` → `compute_aax_score()`, `meshweave/ai/prompts.py` → `content_delta_prompt()`, `select_pages_for_analysis()`

### T4. llms.txt (15%)

Pure heuristic — no LLM call:

| Condition | Score |
|-----------|-------|
| Both llms.txt and llms-full.txt exist | 100 |
| Either one exists | 60 |
| Neither exists | 0 |

**Source:** `meshweave/scoring/engine.py` → `compute_aax_score()`

### T7. Email Validation (15%)

LLM validates email addresses found during crawling, classifying them by contact type and confidence.

**Scoring formula:**

```
contact_count_score          # min(valid_contacts × 20, 60)
+ best_contact_type          # sales=25, support=20, general=15, legal=5, invalid=0
+ confidence × 0.2           # high=90×0.2=18, medium=55×0.2=11, low=25×0.2=5
+ has_best_contact            # 15 if best_contact exists, else 0
```

**Source:** `meshweave/scoring/engine.py` → `compute_aax_score()`, `meshweave/ai/prompts.py` → `email_validation_prompt()`

### Contactability (separate signal, not in composite)

A 0–100 heuristic score based on crawl data — not part of the AAX composite but included in the AAX output:

| Signal | Points |
|--------|--------|
| Same-domain emails found | +20 |
| mailto links present | +10 |
| Contact/about page exists | +10 |
| Email on homepage or contact page | +15 |
| JSON-LD ContactPoint | +15 |
| Social links (sameAs) | +10 |
| Generic contact email (support@, info@, etc.) | +10 |
| Phone number in JSON-LD | +10 |
| **Penalty:** Emails only obfuscated (no mailto) | −10 |
| **Penalty:** Emails only on legal pages | −15 |
| **Penalty:** No same-domain emails | cap score at 20 |

**Source:** `meshweave/ai/analyses.py` → `_compute_contactability()`

### Rating Scale

| Range | Label |
|-------|-------|
| 0–24 | Opaque |
| 25–39 | Unclear |
| 40–59 | Readable |
| 60–79 | Clear |
| 80–100 | Fluent |

---

## Categorical → Numeric Mappings

Used by the AAX scoring engine to convert LLM categorical responses to 0–100 scores:

| Map | Values |
|-----|--------|
| `CLARITY_MAP` | clear=100, somewhat_clear=50, unclear=15 |
| `DENSITY_MAP` | dense=100, adequate=60, sparse=25, bloated=15 |
| `COMPLETENESS_MAP` | complete=100, partial=50, minimal=15 |
| `COHERENCE_MAP` | consistent=100, somewhat_consistent=50, contradictory=15 |
| `CONTENT_COMPLETENESS_MAP` | comprehensive=100, adequate=50, incomplete=15 |
| `LLM_OPT_MAP` | optimized=100, adequate=50, poor=15 |
| `CONFIDENCE_MAP` | high=90, medium=55, low=25, none=5 |

**Source:** `meshweave/ai/runner.py`

---

## Execution Flow

### CLI (`meshweave/cli.py`)

```
1. Crawl URL → payload
2. Always: compute_scores(payload) → AEO + GEO scores
3. If --ai-analysis: run_aax_analysis(payload) → AAX results
                     compute_aax_score(aax_result) → AAX score
                     Merge into payload["scores"]["aax"]
4. Store ScoreSnapshot in database
```

### Key Entry Points

| Function | File | Purpose |
|----------|------|---------|
| `compute_scores()` | `meshweave/scoring/engine.py` | Computes AEO + GEO composites and recommendations |
| `compute_aax_score()` | `meshweave/scoring/engine.py` | Computes AAX composite from LLM analysis results |
| `run_aax_analysis()` | `meshweave/ai/analyses.py` | Orchestrates all LLM-powered AAX tests |
| `_weighted_composite()` | `meshweave/scoring/engine.py` | Shared weighted average with re-normalization |

---

## Output Structure (`score_json`)

```json
{
  "aeo": {
    "composite": 55.0,
    "auto_only_composite": 72.3,
    "rating": "Average",
    "auto_rating": "Strong",
    "factors": { ... }
  },
  "geo": {
    "composite": 48.5,
    "auto_only_composite": 65.2,
    "rating": "Emerging",
    "auto_rating": "Visible",
    "factors": { ... }
  },
  "aax": {
    "composite": 68.0,
    "rating": "Clear",
    "factors": { ... },
    "contactability": { "score": 45.0, ... },
    "tests_completed": 4,
    "tests_skipped": 1
  },
  "recommendations": [ ... ]
}
```
