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

- Factors with `score: None` (e.g. freshness with no dates) are **excluded** and their weight is redistributed proportionally among available factors.
- A **calibration curve** is applied after the weighted average to compress the upper range and prevent score inflation for average sites:
  ```python
  calibrated = 100.0 * (composite / 100.0) ** 1.15
  ```
  This pulls 80→77, 70→66, 60→56, 50→45, 40→35 while leaving 100 untouched. Compress more by raising the exponent (e.g. 1.4 gives 80→73, 70→61).
- The result is capped at 100 and rounded to 1 decimal place.

**Source:** `meshweave/scoring/engine.py` → `_weighted_composite()`

---

## AEO — Answer Engine Optimization

**Purpose:** How well content is structured so AI agents can extract direct, supported answers from it.

**Composite = 100%, 4 factors:**

| # | Factor | Weight | Auto? | Source |
|---|--------|--------|-------|--------|
| A7 | Answerability | 40% | ✅ Auto | `aeo.score_answerability()` |
| A2 | Content Structure | 25% | ✅ Auto | `aeo.score_content_structure()` |
| A1 | Schema Implementation | 20% | ✅ Auto | `aeo.score_schema()` |
| A3 | Freshness | 15% | ✅ Auto | `aeo.score_freshness()` |

The answerability factor scores the grounded answer test over the crawl's
pages; when that test did not run, the factor is excluded and the composite
re-normalizes across the computed factors.

### A1. Schema Implementation (20%)

```
Base score = schema_coverage.coverage_pct (0-100)
+10 if FAQPage schema present
+5  if HowTo schema present
+10 if at least half the FAQ answers are in the optimal length range
    (faq_analysis.answers_in_optimal_range >= max(1, count // 2))
Cap: 100
```

A single in-range answer among many is not "FAQ quality" — the bonus requires half the answers to qualify.

**Source:** `meshweave/scoring/aeo.py` → `score_schema()`

### A2. Content Structure Quality (25%)

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

### A3. Freshness (15%)

Extracts `datePublished` / `dateModified` / `dateCreated` from JSON-LD across all unique pages (the start page is counted once — it appears both in `page` and in `markdowns` for site crawls, so dates are deduplicated by URL). Future-dated content is clamped to 0 days old so scheduled posts can't inflate the score.

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
| 0–25 | Not extractable |
| 26–45 | Limited extractability |
| 46–65 | Partially extractable |
| 66–85 | Reliably extractable |
| 86–100 | Fully extractable |

---

## GEO — Generative Engine Optimization

**Purpose:** Site-wide machine context — whether AI agents can reach the relevant material and reconcile the business identity, evidence, and claims across the site.

**Composite = 100%, 5 factors:**

| # | Factor | Weight | Auto? | Source |
|---|--------|--------|-------|--------|
| G4 | Crawl Access | 30% | ✅ Auto | `geo.score_crawl_access()` |
| G6 | Entity Consistency | 20% | ✅ Auto | `geo.score_entity_consistency()` |
| G5 | Content Depth | 20% | ✅ Auto | `geo.score_content_depth()` |
| G2 | Topical Authority | 15% | ✅ Auto | `geo.score_topical_authority()` |
| G3 | E-E-A-T Signals | 15% | ✅ Auto | `geo.score_eeat()` |

### G2. Topical Authority (15%)

Weighted blend of site-wide evidence (sameAs is raw evidence only — not a score driver):

| Sub-factor | Sub-weight | Calculation |
|-----------|------------|-------------|
| Schema coverage % | 0.35 | Direct from `schema_coverage.coverage_pct` |
| Schema type diversity | 0.25 | `min(unique_types / 10, 1.0) × 100` |
| Entity name consistent | 0.15 | 100 if consistent, else 0 |
| Description consistent | 0.15 | 100 if consistent, else 0 |
| Content page ratio | 0.10 | Pages with >300 words / total pages × 100 |

**Source:** `meshweave/scoring/geo.py` → `score_topical_authority()`

### G3. E-E-A-T Signals (15%)

Additive scoring on site-side evidence (review, video, and sameAs signals are raw evidence only — not score drivers):

| Signal | Points |
|--------|--------|
| Organization schema present | +30 |
| Author info in articles | +30 |
| Contact page exists | +20 |
| Privacy/terms pages exist | +20 |

**Cap:** 100. Schema type matching is case-insensitive.

**Source:** `meshweave/scoring/geo.py` → `score_eeat()`

### G4. LLM Crawl Accessibility (30%)

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

Bot status matching: a status of exactly `allowed` earns full points; a partially-restricted status (allowed site-wide except specific paths) earns **half credit** (GPTBot 7, ClaudeBot 6, PerplexityBot 6). The structural maximum is 77 — the remaining 23 points don't exist to be earned. Optional llms.txt evidence is scored here only, once.

**Source:** `meshweave/scoring/geo.py` → `score_crawl_access()`

### G5. Content Depth & Originality (20%)

Weighted blend:

| Component | Weight | Calculation |
|-----------|--------|-------------|
| Avg word count tier | 0.35 | <200→10, <500→30, <1000→50, <2000→70, <5000→90, ≥5000→100 |
| Pages with 1000+ words ratio | 0.25 | `(pages_1000+ / total) × 100` |
| Content pages ratio (>200 words) | 0.15 | `(pages_200+ / total) × 100` |
| Has code blocks | 0.15 | 100 if any page has code blocks, else 0 |
| Has tables | 0.10 | 100 if any page has tables, else 0 |

Pages come from `markdowns`; the derived `pages` view is only used as a fallback when no markdowns exist (using both would double-count the same pages).

**Source:** `meshweave/scoring/geo.py` → `score_content_depth()`

### G6. Entity Consistency (20%)

Additive scoring:

| Signal | Points |
|--------|--------|
| Entity name consistent across pages | +20 |
| Description consistent across pages | +15 |
| sameAs links: 0 | +0 |
| sameAs links: 1–2 | +16 |
| sameAs links: 3–5 | +28 |
| sameAs links: 6+ | +40 |

**Cap:** 100. The sameAs points use the shared `_same_as_score` scale (0/40/70/100) scaled by 0.4, so one signal can't be "good" in one factor and "mediocre" in another.

**Source:** `meshweave/scoring/geo.py` → `score_entity_consistency()`

### Rating Scale

| Range | Label |
|-------|-------|
| 0–25 | Unreachable |
| 26–45 | Fragmented |
| 46–65 | Reachable |
| 66–85 | Connected |
| 86–100 | Fully connected |

---

## AAX — AI Agent Experience

**Purpose:** Whether an agent can understand the offer and a credible next step. Computed from LLM-powered analysis tests (requires `--ai-analysis` flag or `AAX_ENABLED=true`).

**Composite = 100%, 5 factors:**

| # | Factor | Weight | Auto? | Source |
|---|--------|--------|-------|--------|
| T2 | Homepage Comprehension | 35% | ✅ Auto | LLM reads homepage markdown |
| T5 | Content Delta | 25% | ✅ Auto | LLM reads multiple pages |
| T3 | Meta Optimization | 15% | ✅ Auto | LLM reads meta tags only |
| T6 | Contactability | 15% | ✅ Auto | Heuristic (no LLM) |
| T7 | Email Validation | 10% | ✅ Auto | LLM validates email addresses |

### T2. Homepage Comprehension (35%)

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

### T3. Meta Optimization (15%)

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

### T5. Content Delta (25%)

LLM reads multiple pages (selected by `select_pages_for_analysis()` within a token budget) and produces a comprehensive summary.

**Scoring formula:**

```
info_richness × 0.4         # how many of 7 fields extracted (company name, product name/desc/features, pricing, audience, strengths)
+ coherence × 0.3           # consistent=100, somewhat_consistent=50, contradictory=15
+ completeness × 0.3        # comprehensive=100, adequate=50, incomplete=15
```

**Source:** `meshweave/scoring/engine.py` → `compute_aax_score()`, `meshweave/ai/prompts.py` → `content_delta_prompt()`, `select_pages_for_analysis()`

### T7. Email Validation (10%)

LLM validates email addresses found during crawling, classifying them by contact type and confidence.

**Scoring formula:**

```
presence                    # 0 with no valid contacts; 20 for the first,
                            # +10 for the second, capped at 30
+ best_contact_type         # sales=25, support=20, general=15, legal=5, invalid=0
+ confidence × 0.35         # high=90×0.35=31.5, medium=55×0.35=19.25, low=25×0.35=8.75
+ has_best_contact          # 10 if best_contact exists, else 0
```

Presence saturates quickly: one contact earns most of the presence points, a second adds a little, more add nothing — quantity must not outweigh quality.

**Source:** `meshweave/scoring/engine.py` → `compute_aax_score()`, `meshweave/ai/prompts.py` → `email_validation_prompt()`

### T6. Contactability (15%)

A 0–100 heuristic score based on crawl data, weighted into the AAX composite (15%):

| Signal | Points |
|--------|--------|
| Same-domain emails found | +20 |
| Only third-party emails found | +5 |
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
0. Fail fast unless MESHWEAVE_CDP_ENDPOINT is set (exit code 2)
1. Crawl URL → payload (internal links are always crawled within the URL's path scope)
2. Always: compute_scores(payload) → AEO + GEO scores
3. If --ai-analysis: run_aax_analysis(payload) → AAX results
                     compute_aax_score(aax_result) → AAX score
                     Merge into payload["scores"]["aax"]
                     Re-score AEO (answerability lands here) and
                     re-generate recommendations
4. Write per-page markdown files, then JSON payload to --output/-o (required)
```

The CLI never touches the database — `ScoreSnapshot` persistence happens in the webapp.

### Webapp (`webapp/services/scoring.py`)

```
score_crawl(crawl_id, payload)
  → compute_scores(payload)
  → re-generate recommendations if AAX results are in the payload
  → persist Crawl score columns + ScoreSnapshot (score_json)

run_aax_for_crawl(crawl_id, payload)
  → run_aax_analysis(payload, trace_user_id, trace_session_id)
  → compute_aax_score() → merge into snapshot score_json + payload_json
  → re-score AEO (answerability lands here) and re-generate recommendations
```

### Key Entry Points

| Function | File | Purpose |
|----------|------|---------|
| `compute_scores()` | `meshweave/scoring/engine.py` | Computes AEO + GEO composites and recommendations |
| `compute_aax_score()` | `meshweave/scoring/engine.py` | Computes AAX composite from LLM analysis results |
| `run_aax_analysis()` | `meshweave/ai/analyses.py` | Orchestrates all LLM-powered AAX tests |
| `_weighted_composite()` | `meshweave/scoring/engine.py` | Shared weighted average with re-normalization |
| `score_crawl()` | `webapp/services/scoring.py` | Webapp scoring entry point with DB persistence |
| `run_aax_for_crawl()` | `webapp/services/scoring.py` | Webapp AAX orchestration with DB persistence |

---

## Output Structure (`score_json`)

```json
{
  "aeo": {
    "composite": 55.0,
    "rating": "Partially extractable",
    "factors": { ... }
  },
  "geo": {
    "composite": 48.5,
    "rating": "Fragmented",
    "factors": { ... }
  },
  "aax": {
    "composite": 68.0,
    "rating": "Clear",
    "factors": { ... },
    "contactability": { "score": 45.0, ... },
    "skip_reasons": { ... },
    "tests_completed": 4,
    "tests_skipped": 1,
    "model_id": "..."
  },
  "recommendations": [ ... ]
}
```
