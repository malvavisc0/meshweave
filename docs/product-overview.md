# MeshWeave Product Overview

Find what prevents AI agents from reading your website correctly. MeshWeave finds the pages, text, and next steps AI agents miss — then tells you what to fix, so agents can understand your site. Every report answers three questions in the agent's journey order: can AI agents reach your site, answer from it, and act on it.

MeshWeave occupies the site side of AI search exclusively. It measures what the website itself makes possible for AI agents and ranks the fixes by expected score impact. Everything that happens inside third-party answer engines — tracking answers, brand mentions, share of voice — is out of scope by design.

## The Problem

Buyers increasingly ask AI agents instead of typing keywords into a search box. Whether your brand appears in those synthesized answers depends on **agent legibility** — whether the agent can reach your content, extract a supported answer from it, and identify a credible next step.

Today, no tool answers the real questions an owner has after AI search becomes part of the market's default research behavior:

1. Can AI agents **reach** my site and reconcile my business identity and evidence?
2. Can AI agents **answer** buyers' questions from my content?
3. Can AI agents **act** — identify my offer and find a credible next step?

MeshWeave answers all three with a score, evidence, and a prioritized fix plan.

## The Product

**Free site review** (core experience): enter a domain or URL, receive three check scores with factor breakdowns and a prioritized fix list.

**Scoring model** — three checks in the agent's journey order. Public copy uses only these names; internal code and API score-group keys keep `geo`, `aeo`, and `aax`:

| Check | Internal key | Scores | Output |
| --- | --- | --- | --- |
| **Reachable** | `geo` | Site-wide machine context — whether agents can reach the content and reconcile the business identity, evidence, and claims across the site | Score, rating band, evidence samples |
| **Answerable** | `aeo` | Answer extractability — whether the site provides clear, supported answers agents can extract | Score, rating band, evidence samples |
| **Actionable** | `aax` | Agent actionability — whether agents can identify the offer, understand the content, and find a credible next step | Score, rating band, agent-readable summary |

Full factor definitions, weights, and band thresholds: [docs/scoring-reference.md](scoring-reference.md).

**Every finding includes:**
- The signal that triggered it (with observed evidence)
- Expected point impact — the estimated score gain if fixed
- A short remediation instruction

**A grounded answerability test** runs a fixed decision-critical benchmark over the crawled pages and scores only answers those pages support (supported, partially supported, unsupported, contradictory), with source pages and missing facts as evidence.

**Re-check workflow:** run the same analysis after making changes to see resolved findings and observed per-check movement. The proof-of-work diff compares two runs and shows exactly which findings were resolved and how the observed per-check scores moved. This is the renewal artifact for agencies and the "look what we shipped" slide for in-house teams.

Scores are diagnostic measures of the website itself, not guarantees of outside outcomes. The actionability check is not an interactive browser-agent or transaction test.

## The Funnel

Full detail: [docs/funnel.md](funnel.md) and [docs/funnels/30-questions.md](funnels/30-questions.md).

| Stage | Target | Funnel |
| --- | --- | --- |
| **Attract** | SEO consultants, agencies, in-house marketers | [funnels/30-questions.md](funnels/30-questions.md) (16 core questions + funnel.md's 30); 5 pillars |
| **Capture** | Anonymous user | Site review form (core experience); unclaimed result page claim CTA gated by sign-in |
| **Convert** | Known user | CTA on every completed report: *"Get expert help — let our team walk you through the fix plan"*; nudge after 3rd result for agency segment |
| **Retain** | Signed-in user | Dashboard + score history; per-domain score history and run-diff; Re-check with cooldown; revision strip |
| **Expand** | Agency / consultant | Bulk site review (API tier, 25 per call); white-label reports (unbranded `report.md` export); proof-of-work diff exports (`diff.md`) |

### Lead Capture Logic

| Surface | Visitor type | Trigger | Capture |
| --- | --- | --- | --- |
| Result page | Anonymous | Result complete + unclaimed | **Sign-in-to-claim CTA** (replaces email capture) |
| Result page | Signed-in (report owner) | Report complete | **Expert-audit mailto CTA** (`report_cta_bottom` — "Need help turning these findings into a plan?") |
| Result page | Signed-in (report owner) | 3rd+ result, agency-ish | **Nudge** (`bulk_gate`) — "Audit multiple domains at once" → API tier waitlist |
| Result page | Signed-in (report owner) | 3rd+ result, any segment | **Nudge** (`offer_services`) — "Want the fixes done for you?" → contact |
| Nth result | Any | 1st, 3rd, 5th, 10th, 25th | `nth_result` milestone nudge (soft sell) |
| Historical result | Signed-in (report owner) | 7+ days after last activity | `comeback` re-engagement nudge |
| `/browse`, `/all`, `/d/{domain}` | Any | Card click | Result page (not gated) |
| Bottom CTA (home) | Anonymous | End of marketing page | "Analyze Your Website" |
| Bottom CTA (home) | Signed-in | End of marketing page | "Open Your Dashboard" |

### Funnel Events (emitted, stored)

| Event | When | Key properties |
| --- | --- | --- |
| `report_viewed` | Result page served (200, result view) | `crawl_id`, `surface`, `viewer_role` |
| `report_cta_rendered` | CTA block rendered on result page | `crawl_id`, `cta_kind` |
| `report_cta_clicked` | CTA interaction (mail link, expert contact) | `crawl_id`, `cta_kind`, `surface` |
| `contact_clicked` | Contact CTA click anywhere (footer, contact page, CTA) | `surface`, `crawl_id?`, `viewer_role` |
| `report_saved` | Anonymous user saves a result to a new account | `crawl_id`, `surface`, `viewer_role`, `segment` |
| `nudge_viewed` | Nudge panel rendered | `nudge_kind`, `surface`, `viewer_role`, `segment` |
| `nudge_clicked` | Nudge CTA clicked | `nudge_kind`, `surface`, `viewer_role`, `segment` |
| `nudge_dismissed` | Nudge "No thanks" clicked | `nudge_kind`, `surface`, `viewer_role`, `segment` |
| `report_exported` | Owner exports report.md | `crawl_id`, `format` |
| `report_claimed` | Anonymous claim becomes a save | `crawl_id`, `surface`, `viewer_role` |
| `diff_exported` | Owner exports diff.md | `crawl_id`, `format` |
| `user_upgraded` | API key created | `provider` |
| `rec_recheck` | Re-check action | `crawl_id`, `surface` |

Anonymous activity (submission volume, crawl failures, CTA impressions/clicks) is Prometheus aggregate counters (`meshweave_cta_clicks_total`, `meshweave_nudge_shown_total`, `meshweave_funnel_events_total`), never per-identity histories.

Retention: 90-day rolling window (configurable via `ANALYTICS_RETENTION_DAYS`).

### Segment Model

`funnel_events.segment` is coarse and only exists on known users.

| Segment | Definition | Primary use |
| --- | --- | --- |
| `smb` | Default for a new known user | General SMB copy |
| `agency` | ≥5 distinct domains analyzed in 30d, or ≥2 public reports with ≥2 unique domains | Bulk offer; white-label export; proof-of-work diff |

### Nudge Triggers (implementable)

| Trigger | Condition | Nudge | Surface |
|---|---|---|---|
| `first_call_hint` | First API call completed | `bulk_gate` → "Audit multiple domains at once" | Result page |
| `nth_result` | 1st, 3rd, 5th, 10th, 25th result view (known user) | `bulk_gate` (agency) or `services_offer` (smb) | Result page |
| `services_offer` | 3rd+ result view, any segment | `offer_services` → "Want the fixes done for you?" | Result page |
| `comeback` | 7+ days since last funnel event | `comeback` → "Track how AI-friendly your site becomes" | Dashboard |
| `export_never_used` | Known user, 3+ result views, `report_exported` in 30d | `export_never_used` → "Download the report" | Result page |
| `bulk_never_used` | Known user, 3+ result views, `bulk_submit` in 30d | `bulk_never_used` → "Run multiple domains" | Result page |

### Exit Capture (known user)

Trigger: user clicks an outbound link from a result page (site, pricing, docs, or mailto) and has not dismissed a nudge this session.

Surface: bottom CTA panel. Copy: *"Before you go — want the fix plan for [domain]?"* with two buttons: **Download report** (`report.md`) and **Get expert help** (mailto).

Emits `report_cta_clicked` with `cta_kind=exit_intent`.

## Customer Pains

The 16 decision-stage questions ([docs/funnels/30-questions.md](funnels/30-questions.md)) map to four failure modes MeshWeave diagnoses:

| Failure mode | What the owner feels | Questions |
|---|---|---|
| **Inaccessible content** | "AI agents can't reach the pages that matter." | Q4–Q7 |
| **Unsupported answers** | "AI gets our story wrong" / "AI can't extract clean answers from our pages" / "We never come up in AI answers" / "Other sites show up in AI answers and we don't" | Q8–Q14 |
| **Inconsistent site context** | "AI says things about us we can't verify" / "We have no idea if any of this is working" | Q15–Q22 |
| **Missing action path** | "AI agents can't find our offer or next step" | Q23–Q30 |

The top four by frequency: **"Other sites show up in AI answers and we don't"** (70%), **"We can't tell if our AI optimization is working"** (62%), **"AI gets our story wrong"** (55%), **"I don't know what to fix first"** (55%).

MeshWeave's answer to all four is the same deliverable: a score, evidence, and a prioritized fix plan. The website is the controllable prerequisite — MeshWeave fixes the surface the client controls rather than measuring what answer engines say.

## Content Pillars

| Pillar | Angle | Funnel stage | Target segment |
|---|---|---|---|
| AI Search | How AI-mediated research changes discovery | Attract | Marketer |
| Answer Extractability | Structure answers agents can extract | Attract | Content |
| Machine Context | Make your site legible to machines | Attract | Web/Dev |
| Agent Actionability | Give agents a next step | Attract | Product/Marketing |
| Proof-of-work | Re-check and show what changed | Retain | In-house, Agency |

Full detail: [docs/funnel.md](funnel.md) §5.

## Differentiation

The buyer's working category centers on presence in AI answers, but MeshWeave occupies the site side of it exclusively.

**The website is the controllable prerequisite.** Off-site visibility depends on models, indexes, and vendors nobody controls; the website is the only surface the client fully controls, and it is the surface every answer is grounded in. MeshWeave measures and fixes that surface.

MeshWeave is a diagnostic pill for site-side pains only: misdiagnosis, fix prioritization via expected_points, and proof-of-work diff exports. It deliberately does not address off-site visibility and is not an answer-engine tracker or tracker alternative.

### What We Sell

1. **A diagnosis.** A score and a named problem: "your agent legibility is 47/100 because your entity description is inconsistent across 8 pages and you have no extractable answer blocks."
2. **A plan.** A prioritized, expected-points-ranked remediation list that a content writer or developer can start on this week.
3. **Proof-of-work.** A diff between two runs showing exactly which findings were resolved and how the observed per-check scores moved.

### Three Customer Fears (positioning)

| Fear | Objection | Positioning |
|---|---|---|
| Being unreadable to AI agents | "I don't know if AI can even find me." | Scores + evidence show exactly what agents can reach on the site today. |
| Losing the answer slot to others | "Other sites get answered and we don't." | The differentiator isn't tracking answers — it's fixing the website. MeshWeave makes your site the one agents can read. |
| No measurable progress | "I fixed some content — did it work?" | Re-check shows resolved findings and observed score movement. The proof-of-work diff is the evidence. |

## Roadmap

| Phase | Deliverable | Status |
|---|---|---|
| 1 | Crawler + 4 AI check groups + scoring + dashboard | ✅ Shipped |
| 2 | Agency API (bulk site review, 25/call) + report export | ✅ Shipped |
| 3 | White-label reports (unbranded report.md export) | ✅ Shipped |
| 4 | Score history + trend charts + run-diff | ✅ Shipped |
| 5 | Expert-guided audit (services layer) | 🔲 Planned |
| 6 | Scheduled re-crawl + weekly delta email | ✅ Dropped (project-shaped agency lifecycle; monitoring is dead) |

## Open Questions

1. What is the right free/paid boundary for the agency tier?
2. Does the white-label export need agency branding (logo + name), or is a plain unbranded report sufficient for v1?
3. What is the minimum score history depth before trend charts are useful (3 runs? 5?)
4. Should the proof-of-work diff be a scheduled weekly export or on-demand only?

## Related Docs

- [docs/product.md](product.md) — full product definition (problem, audience, scoring, pricing)
- [docs/funnel.md](funnel.md) — the 30-question acquisition funnel
- [docs/funnels/30-questions.md](funnels/30-questions.md) — the 16-question decision-stage detail
- [docs/scoring-reference.md](scoring-reference.md) — scoring factors, weights, and bands
- [docs/conversion-funnel.md](conversion-funnel.md) — conversion funnel and lead-capture design
- [docs/market-research.md](market-research.md) — market research, ICP, competitive landscape
- [docs/style-guide.md](style-guide.md) — UI style guide and copy rules
