# Scoring Reference

Canonical reference for MeshWeave's scoring model. Defines each check's factors, weights, band names, compositional semantics, and interpretation matrix. Read this before writing factors, tuning weights, or changing band thresholds.

Three checks in the agent's journey order. Public surfaces use only the names **Reachable / Answerable / Actionable**; internal code, score-group keys (`geo`, `aeo`, `aax`), and API field names keep their identifiers.

| Check | Internal key | Scores | Engine | Source |
| --- | --- | --- | --- | --- |
| Reachable | `geo` | Site-wide machine context: entity consistency, evidence depth, cross-page coherence | Heuristic (no LLM) | `meshweave/scoring/geo.py` |
| Answerable | `aeo` | Answer extractability: extractable answers, evidence density, content/schema alignment, trust | Heuristic (no LLM) | `meshweave/scoring/aeo.py` |
| Actionable | `aax` | Agent actionability: structure, information architecture, offer clarity, contactability | Heuristic + LLM | `meshweave/scoring/aax.py` |

---

## Factor Reference

### Reachable (`geo`) — machine context

| Factor | Weight | Meaning | Primary signal |
|---|---|---|---|
| `entity_consistency` | 0.35 | Alignment of name and description across key pages | Same brand name and category language across the site |
| `evidence_depth` | 0.30 | Cross-site support coverage and breadth | Depth of supporting material across the domain |
| `cross_page_coherence` | 0.20 | Internal-link and content coherence | Pages referencing and supporting each other |
| `crawl_agency` | 0.15 | Whether AI crawlers can reach the site | robots.txt directives for GPTBot/OAI-SearchBot/ClaudeBot/PerplexityBot + llms.txt |
| `offsite_consistency` | 0.10 | Organization/Offer schema consistency | Structured data matching page content |

Out of scope: third-party authority signals (off-site presence, reviews, links) are not scored.

**Weight sum: 1.10** (normalized at composite time)

**Rating bands:** Unreachable → Fragmented → Reachable → Connected → Fully connected.

### Answerable (`aeo`) — answer extractability

| Factor | Weight | Meaning | Primary signal |
|---|---|---|---|
| `extractable_answer` | 0.35 | Clear, well-structured answers on key pages | Answer-shaped blocks and schema |
| `evidence_density` | 0.25 | Crawl-internal evidence count and consistency | Evidence count on key pages and site-wide |
| `answerability_test` | 0.20 | Grounded answerability test verdict mix | Fixed decision-critical benchmark over the crawled pages; only site-supported answers earn weight |
| `content_schema_alignment` | 0.25 | Structured-data/content alignment | JSON-LD matching visible content |
| `trust_transparency` | 0.20 | Author and organizational trust markers | Authorship, org identity, contact and legal surfaces |
| `question_surface` | 0.10 | Question coverage | How many decision questions the content answers (zero when coverage is empty) |

Out of scope: third-party brand presence is not scored.

**Weight sum: 1.35** (normalized at composite time)

**Rating bands:** Not extractable → Limited extractability → Partially extractable → Reliably extractable → Fully extractable.

### Actionable (`aax`) — agent actionability

| Factor | Weight | Meaning | Primary signal |
|---|---|---|---|
| `structure_schema` | 0.30 | Structure and schema audit | List/table density, JSON-LD/WebPage/WebSite/Organization coverage, key-fact alignment |
| `information_architecture` | 0.30 | Heading hierarchy and page depth | H1→H3 depth, clicks from homepage |
| `actionability` | 0.25 | Actionability analysis | Brand clarity, target audience clarity, offer clarity, key features, info density, CTA |
| `contactability` | 0.15 | Contactability score (0–10 scaled ×10) | Email presence, contact page, form, social links, ContactPoint schema |

**Weight sum: 1.00**

**Rating bands:** Opaque → Unclear → Readable → Clear → Fluent.

This check is a structured evaluation over rendered pages. It does not test transactions or operate an interactive browser agent.

---

## Composite Semantics

All three checks share identical compositional rules (`meshweave/scoring/composite.py`).

### Weighted Composite

```
composite = Σ(factor_score × factor_weight) / Σ(factor_weight)
```

Only factors with non-`None` scores participate.

### Missing Factors (Compositional Re-normalization)

When a factor is skipped (`score is None`), its weight is re-normalized across remaining factors. Example: if `question_surface` is skipped in the answer-extractability check, the composite divides by 1.25 (the sum of remaining weights), not 1.35.

**Zero-filling is never used.** The methodology page states this plainly: "Scores computed from available signals. Zero-filling is never used."

### Calibration Curve

Upper-range compression via a power curve:

```
calibrated = 100 × (composite / 100) ^ 1.15
```

The exponent (1.15) compresses the upper range so near-perfect scores require near-perfect factors.

### Final Score

Capped at 100, rounded to 1 decimal place.

---

## Band Reference

### Reachable (`geo`) Bands

| Score | Band |
|---|---|
| 86–100 | Fully connected |
| 66–85 | Connected |
| 46–65 | Reachable |
| 26–45 | Fragmented |
| 0–25 | Unreachable |

### Answerable (`aeo`) Bands

| Score | Band |
|---|---|
| 86–100 | Fully extractable |
| 66–85 | Reliably extractable |
| 46–65 | Partially extractable |
| 26–45 | Limited extractability |
| 0–25 | Not extractable |

### Actionable (`aax`) Bands

| Score | Band |
|---|---|
| 80–100 | Fluent |
| 60–79 | Clear |
| 40–59 | Readable |
| 25–39 | Unclear |
| 0–24 | Opaque |

---

## Interpretation Matrix

`meshweave/scoring/interpretation.py` — translates a score triple (Reachable, Answerable, Actionable) into a plain-language risk narrative: profile label, tone, headline, diagnosis, and fix priority. Runs at report render time; never persisted.

### Routing (ordered rules)

| # | Condition | Profile shape | Tone | Fix priority |
|---|---|---|---|---|
| 1 | Actionable ≤ 25 (any) | `high_invisibility` | high | Actionable |
| 2 | Reachable ≤ 25 (any) | `high_invisibility` | high | Reachable |
| 3 | Answerable ≤ 25 (any) | `high_invisibility` | high | Answerable |
| 4 | all < 55 | `broad_exposure` | high | lowest check |
| 5 | two ≥ 70, one < 55 | `single_bottleneck` | moderate | the < 55 check |
| 6 | one ≥ 70, one < 55, one in [55, 70) | `uneven_profile` | moderate | the < 55 check |
| 7 | all ≥ 70 | `highly_readable` | low | `—` |
| 8 | all in [55, 70) | `solid_baseline` | low | lowest check |
| 9 | all ≥ 55 | `partial_exposure` | moderate | lowest check |
| 10 | `needs_review` fallback | `needs_review` | moderate | lowest check |

`incomplete` shape (any score `None`) short-circuits before routing.

### Required Fields (always populated)

| Field | Type | Description |
|---|---|---|
| `profile_label` | `str` | Human label, e.g. "Several blind spots" |
| `tone` | `str` | Visual token: `low` / `moderate` / `high` |
| `headline` | `str` | Display line |
| `diagnosis` | `str` | ≥2 sentences explaining what's readable and what's not |
| `primary_exposure` | `str` | The main business risk — what's exposed |
| `fix_priority` | `str` | Which check to fix first (public label) or `—` |
| `next_step` | `str` | The one concrete action |
| `weakest_lens` | `str \| None` | Weakest check name (score-group key) or `None` |
| `strongest_lens` | `str \| None` | Strongest check name (score-group key) or `None` |
| `profile_shape` | `str` | Shape key from routing table |
| `lens_details` | `dict` | Per-check meaning + band label |
| `bands` | `dict` | Per-check band name (internal label) |

### Tone Tokens

| Token | Visual | Usage |
|---|---|---|
| `low` | Positive/neutral | Highly readable, solid baseline |
| `moderate` | Amber/warning | Partial exposure, uneven profile, single bottleneck, needs review |
| `high` | Red/critical | `high_invisibility`, `broad_exposure` shapes |

---

## Engine Differences

| Aspect | Reachable / Answerable (`geo`/`aeo`) | Actionable (`aax`) |
|---|---|---|
| LLM required | No | Yes (Gemini via LiteLLM) |
| Input | Cross-site crawl data | Cross-site crawl + LLM analysis |
| Output | `score`, `rating`, `factors`, `recommendations`, `skip_reasons` | Same + `ai_analysis` raw blob |
| Persisted to | `ScoreSnapshot.aeo_score` / `.geo_score` | `ScoreSnapshot.score_json.aax.composite` |
| Null state | `aeo_score: null` | `aax_score: null` |

### Recommendations

Every check emits **recommendations** (remediation items) with `pillar`, `priority` (derived from `expected_points`), `factor`, `title`, `detail`, `guidance`, and `expected_points`. All findings appear as recommendations; `recommendations.py` sorts them high → medium → info, then `expected_points` descending.

See [docs/product.md](product.md) for the full expected-points remediation model.

---

## Known Gaps / TODO

1. `question_surface` uses a flat `question_coverage_count` threshold. A normalized coverage ratio (questions / category questions) would be more discriminating. Requires a category-question baseline.
2. `content_schema_alignment` and `trust_transparency` are Medium but arguably High for the answer-extractability check. Pending calibration runs.
3. Reachable and Answerable share `evidence_count` / `key_page_*` inputs via cross-site crawl. The input contract is identical but the signals diverge at composite time.
4. `offsite_consistency` (Reachable, 0.10) is a stub — the org schema consistency check is incomplete.
5. Third-party authority signals remain out of scope across all checks.

---

## Source Files

| Check | Primary file | Tests |
|---|---|---|
| Reachable | `meshweave/scoring/aeo.py`, `geo.py` | `tests/test_scoring.py` |
| Answerable | `meshweave/scoring/aeo.py` | `tests/test_scoring.py`, `tests/test_recommendation_impact.py`, `tests/test_answerability.py` |
| Actionable | `meshweave/scoring/aax.py` | `tests/test_recommendation_impact.py` |
| Composite | `meshweave/scoring/composite.py` | `tests/test_scoring.py` |
| Interpretation | `meshweave/scoring/interpretation.py` | `tests/test_interpretation.py` |
| Recommendations | `meshweave/scoring/recommendations.py` | `tests/test_recommendation_impact.py` |
