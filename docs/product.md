# MeshWeave — Product Definition

## Elevator Pitch

Find what prevents AI agents from reading your website correctly. MeshWeave diagnoses inaccessible content and empty or ambiguous copy, then prioritizes the fixes that make the site usable.

Every report answers three questions in the agent's journey order: can AI agents reach your site, answer from it, and act on it.

MeshWeave gives marketing, content, SEO, and product teams a clear, prioritized plan for making their site legible to the AI agents that increasingly mediate research and buying decisions.

## Problem

### The buyer's question has changed

Buyers increasingly ask AI agents — ChatGPT, Perplexity, Google's AI Overviews, Claude, Copilot — instead of typing keywords into a search box.

When they ask:

> "What should I use to monitor my Postgres database?"
> "Who can help me migrate from Elasticsearch to a vector database?"
> "What's the best project management tool for a 10-person design agency?"

...the AI synthesizes an answer from what it can read, extract, verify, and act on across the web.

Your brand's presence in that answer is not a ranking. It is a *legibility* problem: can the agent reach your content, extract a supported answer from it, and identify a credible next step?

### What makes a site legible to AI agents

Five things determine whether an AI agent can use your website:

1. **Machine context** — structured data, metadata, and crawl directives that tell the agent what it's reading.
2. **Extractable content** — clear, well-structured answers the agent can pull from pages: definitions, facts, use cases, comparisons, pricing signals.
3. **Consistent identity** — the same brand name, product description, and category language across every page. Agents resolve entities across sources; a fragmented site description reads as noise.
4. **Site-wide evidence** — the depth of supporting material (pages, internal links, case-studies, docs) across the domain, not just the homepage.
5. **Actionable surface** — a clear offer, clear contact paths, and clear next steps. An agent that can read you but cannot act for the buyer will stall.

### The problem today

- **Most SEO tools measure ranking signals for search engines.** They are not built to answer "can an AI agent use this page?"
- **Site owners don't know where they stand.** There is no score, no baseline, and no before/after measurement for agent legibility.
- **Even when they know, they don't know what to fix first.** A 47/100 with 23 findings and no expected impact per fix is not actionable.
- **Agencies and consultants have no proof-of-work artifact.** They make improvements to a client's site but cannot show a clean before/after score with concrete, completed fixes.

### What MeshWeave does instead

MeshWeave is a diagnostic pill for the site side only. It measures what the website itself makes possible for AI agents and ranks the fixes by expected score impact. It deliberately does not track external answers, brand mentions, or share of voice.

## Target Audience

### Primary: Agency Owners and Independent SEO/Marketing Consultants

The sharpest wedge is the agency owner or independent SEO/marketing consultant who runs sites for clients.

**Who they are:**
- Small agency (2–15 people) or solo consultant with 5–40 active retainer clients
- Sells recurring services: technical SEO, content strategy, web maintenance, "AI readiness" audits
- Clients are SMBs and mid-market companies who ask "are we visible in AI search?" and expect an answer
- Fee per client: $1.5k–$12k/month retainer, or $3k–$25k one-off project

**Their pain today:**
- Clients increasingly ask whether AI agents can use their site; the agency has no diagnostic to offer, only opinions
- Doing AI-readability audits by hand: prompt ChatGPT with the client's category, screenshot the answer, write a slide deck. Expensive, unscientific, not repeatable
- No proof-of-work artifact for renewals. Monthly reports show rankings and traffic; nothing demonstrates that the site works better for AI agents than when the engagement started
- Fixed-fee engagements get consumed by unfocused remediation work because nobody knows which fixes matter

**Why they buy MeshWeave:**
- A branded, client-ready report that answers the client's actual question in language a non-technical buyer understands
- A prioritized remediation roadmap with expected point impact per fix, so hours go to the fixes that move the score
- A proof-of-work diff between two runs showing exactly which findings were resolved and how the observed per-check scores moved
- Bulk workflow (up to 25 domains per call) so they can audit a book of clients without clicking through a form per site

### Secondary: In-House Marketing, Content, SEO, and Web Leads

**Who they are:**
- Head of content, SEO lead, marketing director, or web/product lead at a mid-market B2B company
- Owns the website as a buyer-facing surface and is measured on organic pipeline
- Hearing from their CMO or CEO: "are we showing up in ChatGPT?"

**Their pain:**
- No baseline. They cannot tell their leadership where the site stands for AI agents
- A vague sense that "AI can't find us" but no concrete evidence or fix list
- No way to show before/after after a quarter of content and technical work

**Why they buy:**
- A score they can put in a slide and trend over time
- A short, concrete fix list they can hand to a content writer or developer
- A re-check workflow ("Re-check now") with cooldown so they can prove progress after each content sprint

### Explicit Non-Targets

| Segment | Why not |
| --- | --- |
| Enterprise (500+ employees) | Requires SSO, audit logs, dedicated support, custom contracts. Wrong product shape and wrong price point. |
| Local businesses / real estate / law firms | Understand "SEO" but not "AI legibility." Too early in their mental model; high education cost. |
| Pure SEO agencies doing only backlink building | Not technical enough; their clients want backlinks and rankings. No budget for a legibility audit. |
| Agencies in emerging markets | Low fee base; the per-client price point is too high relative to retainer size. |

## Market Positioning

### The category: AI-friendly website diagnostics

The buyer's working category centers on presence in AI answers, but MeshWeave occupies the site-side part of it exclusively: what the website itself makes possible for AI agents. Everything that happens inside third-party answer engines — tracking answers, external brand presence, share of voice — is out of scope by design.

**The differentiator: the website is the controllable prerequisite.** Off-site visibility depends on models, indexes, and vendors; the website is the only surface the client fully controls. MeshWeave measures and fixes that surface.

### MeshWeave is NOT

- **Not a rank tracker.** No keyword rankings, SERP features, or position tracking.
- **Not a backlink or domain-authority tool.** No link graphs, no authority scores.
- **Not an answer-engine tracker.** It does not watch what ChatGPT or Perplexity say about your brand, and it is not an alternative to tools that do. It makes the website the controllable prerequisite those tools cannot fix for you.
- **Not a content generator.** It doesn't write blog posts or auto-publish fixes.
- **Not an AI agent or transaction tester.** The Actionable check is a structured evaluation over rendered pages. It does not operate an interactive browser agent, does not complete transactions, and does not confirm transaction success.
- **Not enterprise SEO.** No SSO, no custom contracts, no managed crawl infrastructure.

## Scoring Model

Three checks, scored in the agent's journey order. Public copy uses only these names; internal code and API score-group keys keep their identifiers (`geo`, `aeo`, `aax`).

| Check | Internal key | Scores |
| --- | --- | --- |
| Reachable | `geo` | Site-wide machine context — whether agents can reach the content and reconcile the business identity, evidence, and claims across the site |
| Answerable | `aeo` | Answer extractability — whether the site provides clear, supported answers agents can extract |
| Actionable | `aax` | Agent actionability — whether agents can identify the offer, understand the content, and find a credible next step |

### Reachable (`geo`) — machine context

The Reachable check scores **whether the site and the business can be parsed and trusted consistently across the domain** — machine context for an agent that resolves entities across sources.

It answers: "If an AI agent reads this website from multiple angles, does it get a consistent, well-evidenced picture of the business?"

**High Impact Factors (weight ≥ 0.20):**

| Factor | Weight | What it measures |
|---|---|---|
| `entity_consistency` | 0.35 | Alignment of name and description across key pages. Same brand name, same category language, same product description across the site. A fragmented site description reads as noise to a resolving agent. |
| `evidence_depth` | 0.30 | Cross-site support coverage and breadth. Evidence is the depth of supporting material (pages, internal links, case-studies, docs) across the domain, not just the homepage. |

**Medium Impact (weight 0.10–0.19):**

| Factor | Weight | What it measures |
|---|---|---|
| `cross_page_coherence` | 0.20 | Internal-link and content coherence across key pages. Whether pages reference and support each other so the agent can reconcile claims site-wide. |
| `crawl_agency` | 0.15 | Whether GPTBot, OAI-SearchBot, ClaudeBot, Claude-Web, PerplexityBot, Google-Extended, Applebot-Extended, and CCBot can reach the site without being blocked, plus llms.txt readiness. |
| `offsite_consistency` | 0.10 | Alignment of Organization/Offer schema across the site. Structured data that matches what the pages say. |

**Low Impact (weight < 0.10):** None.

**Out of Scope (not scored):**

- **Third-party authority signals** — off-site presence, reviews, and links are outside the site side. They do not directly influence scores. They can be reviewed manually as supporting context, but a high score means the site does the work; it does not mean the site is prominent in any outside system.

**Weight sum: 1.10** (normalized at composite time)

**Rating bands:** Unreachable → Fragmented → Reachable → Connected → Fully connected (0–100).

### Answerable (`aeo`) — answer extractability

The Answerable check scores **whether the site can support a real answer** that an agent can extract and use.

It answers: "When a buyer asks an AI agent a question about this category, does the site give it something to extract and use?"

**High Impact Factors (weight ≥ 0.20):**

| Factor | Weight | What it measures |
|---|---|---|
| `extractable_answer` | 0.35 | Clear, well-structured answers on key pages. Answer-shaped content: definitions, facts, use cases, comparisons, pricing signals. Blocks and schema that let an agent extract a direct answer. |
| `evidence_density` | 0.25 | Crawl-internal evidence count (key pages and site-wide) and consistency. How much supporting material the crawled pages carry. |
| `answerability_test` | 0.20 | Grounded answerability test — a fixed decision-critical benchmark run over the crawled pages. Each answer is scored as supported, partially supported, unsupported, or contradictory by the site's own pages; only supported answers earn weight. Source pages and missing facts are recorded as evidence. |

**Medium Impact (weight 0.10–0.19):**

| Factor | Weight | What it measures |
|---|---|---|
| `content_schema_alignment` | 0.25 | Structured-data/content alignment. Whether the JSON-LD and the visible content tell the same story. |
| `trust_transparency` | 0.20 | Author and organizational trust markers. Authorship, org identity, contact and legal surfaces. |

**Low Impact (weight < 0.10):**

| Factor | Weight | What it measures |
|---|---|---|
| `question_surface` | 0.10 | Question coverage — how many decision questions the content actually answers (zero when coverage is empty). |

**Out of Scope (not scored):**

- **Third-party brand presence** — external brand presence and review volume do not score. They are out of scope for the answer-extractability calculation.

**Weight sum: 1.35** (normalized at composite time)

**Rating bands:** Not extractable → Limited extractability → Partially extractable → Reliably extractable → Fully extractable (0–100).

### Actionable (`aax`) — agent actionability

The Actionable check scores **whether a structured AI evaluation can understand the site, assess the offer, and identify a credible next step** from key pages. It does not test transactions or operate an interactive browser agent.

It answers: "If an AI agent evaluates this site on behalf of a buyer, can it understand the business and find a next step?"

**High Impact Factors (weight ≥ 0.20):**

| Factor | Weight | What it measures |
|---|---|---|
| `structure_schema` | 0.30 | Structure and schema audit: list/table density, JSON-LD/WebPage/WebSite/Organization coverage, key-fact alignment. |
| `information_architecture` | 0.30 | Heading hierarchy (H1→H3 depth) and page depth (clicks from homepage). |
| `actionability` | 0.25 | Actionability analysis: brand clarity, target audience clarity, offer clarity, key features, information density, CTA presence. |
| `contactability` | 0.15 | Contactability score (0–10 scaled ×10): email presence, contact page, form, social links, ContactPoint schema, pricing/quote route. |

**Medium Impact (weight 0.10–0.19):** None.

**Low Impact (weight < 0.10):** None.

**Weight sum: 1.00**

**Rating bands:** Opaque → Unclear → Readable → Clear → Fluent (0–100).

### Missing Data Semantics (Compositional)

When factors are skipped (no score, `None`), the composite uses **re-normalization**, not zero-filling:

- Factors with `score: None` are excluded from the composite
- Remaining factor weights are re-normalized to sum to 1.0
- The composite reflects only the evidence that exists
- Zero-filling is never used — it would punish missing data as failure

The methodology page states this plainly: "Scores computed from available signals. Zero-filling is never used."

## Expected-Points Remediation Model

Every finding carries an **expected point impact** — the estimated score improvement if the finding is fixed. This is the prioritization engine.

### Finding Structure

```python
{
    "pillar": "aax" | "aeo" | "geo",  # which check the finding belongs to
    "priority": "high" | "medium" | "info",  # urgency tier
    "factor": "structure_schema",  # which factor is affected
    "title": "Add 3 FAQPage blocks to key pages",
    "detail": "Observed: 0 FAQPage blocks across 12 key pages. Expected: 3+...",
    "expected_points": 4.2,  # estimated score gain if fixed
}
```

### Priority Determination

Priority is determined **solely by expected points** — the estimated score impact:

| Priority | Rule | Use |
|---|---|---|
| High | `expected_points > 8.0` | Fix now — largest score impact |
| Medium | `expected_points > 3.0` | Fix next — meaningful improvement |
| Info | `expected_points <= 3.0` | Fix when convenient — polish |

Recommendations are sorted: high → medium → info, then expected_points descending within each tier.

There is no other signal that moves a finding into a higher priority tier. Expected impact is the only urgency input.

## Recommendations (Remediation Items)

A **recommendation** is a single actionable remediation item generated by a factor when its measured signal indicates a gap. Every finding — high, medium, and info — is included in the recommendations list. Findings appear only as recommendations.

### Generation

Recommendations are generated during scoring (both `aeo.py`/`geo.py` and `aax.py` emit them) and filtered by priority in `recommendations.py`. Only `high` and `medium` findings become recommendations; `info` findings are retained in factor data but do not appear in the remediation list.

### Structure

```python
{
    "pillar": "aax" | "aeo" | "geo",
    "priority": "high" | "medium" | "info",
    "factor": "structure_schema",
    "title": "Add 3 FAQPage blocks to key pages",
    "detail": "Observed: 0 FAQPage blocks across 12 key pages...",
    "guidance": "A grounded answerability test answered 12 decision questions using only your crawled pages...",
    "expected_points": 4.2,
}
```

`expected_points` is the estimated score improvement if the recommendation is implemented. It feeds both the `priority` tier and the sort order. See **Expected-Points Remediation Model** above.

### What Recommendations Are Not

- **Not a content plan.** A recommendation says "add FAQPage schema to these 3 pages," not "write a blog post about Postgres monitoring."
- **Not a dev ticket.** It describes the fix and the evidence, not the implementation.
- **Not a guarantee.** Expected points are estimates based on the factor weight and the observed gap, not a promise of a specific score outcome.

## Scoring Profile (What Data We Need)

MeshWeave scores from crawl data and the LLM analysis result. All inputs are generated by the system — no manual data entry is required or supported.

### Auto-collected by the Crawler

| Field | Type | Example | Source |
|---|---|---|---|
| `company_name` | `str` | "Inngest" | Entity extraction |
| `company_description` | `str` | "Durable execution as a service..." | Entity extraction |
| `primary_offering` | `str` | "Durable execution platform" | Entity extraction |
| `target_market` | `str` | "Developers and engineering teams" | Entity extraction |
| `key_features` | `list[str]` | ["Durable functions", "Queues"] | Entity extraction |
| `evidence_count` | `int` | 23 | Cross-site crawl |
| `evidence_types` | `dict[str, int]` | `{...}` | Cross-site crawl |
| `comparisons_count` | `int` | 4 | Cross-site crawl |
| `case_studies_count` | `int` | 2 | Cross-site crawl |
| `docs_depth` | `int` | 8 | Cross-site crawl |
| `pricing_present` | `bool` | `True` | Cross-site crawl |
| `question_coverage_count` | `int` | 12 | Cross-site crawl |
| `key_page_slugs` / `names` | `list[str]` | ["pricing", "docs"] | Cross-site crawl |
| `llms_txt_exists` | `bool` | `True` | Cross-site crawl |
| `ai_crawler_access` | `dict[str, int]` | `{...}` | Cross-site crawl |
| `org_schema_present` / `offer_schema_present` | `bool` | `True` | Cross-site crawl |
| `contactability` | `dict` | `{...}` | Cross-site crawl |
| `structure_schema` | `dict` | `{...}` | Cross-site crawl |
| `content_metrics` | `dict` | `{...}` | Cross-site crawl |
| `pages` | `list[dict]` | `[...]` | Cross-site crawl |

**Crawl is automated.** No manual data entry. Every signal is observed from rendered pages, structured data, link graphs, and site metadata. The grounded answerability test runs over the same crawled pages and adds per-question verdicts with source pages and missing facts.

### What We Don't Need

- **Manual external signals.** Off-site signals are out of scope for scoring. They are reviewed manually as context in a consulting engagement, never scored.
- **Third-party tool data.** No Ahrefs, Moz, or SimilarWeb integration required.
- **Historical analytics.** No Google Search Console, GA4, or CRM data.

## Data Sources by Check (summary)

| Check | Data sources | Manual input? |
|---|---|---|
| Reachable | Cross-site crawl (entity consistency, evidence depth, cross-page coherence), crawl directives + llms.txt, Organization/Offer schema | No |
| Answerable | Key-page rendering (answer blocks, structured data/content alignment, trust surfaces), cross-site crawl (evidence density), grounded answerability test over crawled pages | No |
| Actionable | Key-page rendering (structure, information architecture, actionability), cross-site crawl (contactability) | No |

All signals are derived from the system's own crawl and rendering pipeline. Manual external signals and third-party tool data are out of scope for scoring.

## Price Positioning

### Reference Points

| Product | Price | Model |
|---|---|---|
| SEMrush | $140–$500/mo | SaaS subscription |
| Ahrefs | $125–$449/mo | SaaS subscription |
| Profound | $499/mo+ | Answer-tracking SaaS subscription |
| Peec.ai | €89–€289/mo | Answer-tracking SaaS subscription |
| Scrunch | $299–$799/mo | Answer-tracking SaaS subscription |
| OttoSEO (OTTO) | $30–$200/mo | Automated SEO optimization |
| Traditional agency audit | $3k–$25k one-off | Human-delivered project |
| SEO retainer | $1.5k–$12k/mo | Recurring agency service |

### MeshWeave Price

**Free**: site review, check scores, factor breakdowns, recommendations.

**Paid (planned, not yet implemented)**:
- **Agency/API tier: $299–$599/mo.** Bulk domain submissions (25 per call), client-ready Markdown reports, proof-of-work diff exports, unbranded white-label reports, score history and trend data.
- **Expert-guided audit: $2.5k–$8k one-off.** A human walks the report with the client's team, prioritizes the roadmap against business context, and delivers a written action plan.

**Why this price:**
- The free tier is the top-of-funnel: it proves the diagnosis is specific and actionable before anyone pays
- The agency tier sits below SEMrush/Ahrefs while covering a problem those tools don't measure; at 5–10 client sites the price is a small fraction of one client retainer
- The expert-guided audit is the high-margin services layer that agencies and consultants can resell or white-label

## What We're Selling (Three Things)

1. **A diagnosis.** A score and a named problem: "your agent legibility is 47/100 because your entity description is inconsistent across 8 pages and you have no extractable answer blocks."

2. **A plan.** A prioritized, expected-points-ranked remediation list that a content writer or developer can start on this week.

3. **Proof-of-work.** A diff between two runs showing exactly which findings were resolved and how the observed per-check scores moved. This is the renewal artifact for agencies and the "look what we shipped" slide for in-house teams.
