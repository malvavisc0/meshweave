# AAX data usage: where the AI money goes, and the plan to stop wasting it

Date: 2026-08-30 (updated: converted to actionable plan after strategy review)
Status: ready to implement

## The question

When someone runs an audit, we pay an AI model to look at their site and answer six questions about it. Is that money producing value for the reader, or are we paying for answers nobody looks at?

This document maps what the AI produces against what the code actually consumes, and ends in a concrete, ordered implementation plan. Every claim below was verified against the code on 2026-08-30.

## Product context (decided 2026-08-30)

The report is a **free consultation**, not a document. Its job is to give the visitor a sharp, specific idea of what's wrong and convert them — either to claim the report (sign-in), use the API + agent prompt to fix issues themselves, or (our bet) email us. Consequences:

- **Nobody reads a long report.** We keep only what adds value; depth belongs in the API payload for agents, not in the HTML.
- **The public preview is the consultation.** Today it is 100% deterministic (risk summary, score pills, top-3 fixes, CTA) — everything the AI was paid for sits behind the sign-in wall. The AI's best output is exactly what would make the preview feel like a consultation instead of a scorecard.
- **The funnel's key moment is the preview**, so AI value must be visible there, not just in the owner report.

## Pipeline map: what the AI does, and what it costs

The pipeline lives in `meshweave/ai/`, orchestrated by `meshweave/ai/analyses.py` (`run_aax_analysis`, tasks built at lines 92-116). Results are stored in `ScoreSnapshot.ai_analysis_json` (`webapp/services/scoring.py:260-263`), scored by `meshweave/scoring/engine.py`, turned into recommendations by `meshweave/scoring/recommendations.py`, and rendered by `webapp/templates/result.html`.

| Test | Code name | Input budget | Score weight | Verdict |
|---|---|---|---|---|
| Homepage comprehension | `homepage_comprehension` | 50,000 chars (`AAX_HOMEPAGE_MAX_CHARS`, `analyses.py:182-187`, enforced `prompts.py:58-63`) | 30% (`engine.py:153`) | Good value; budget likely generous |
| Meta optimization | `meta_optimization` | No limit (metadata only, `prompts.py:102-159`) | 20% (`engine.py:154`) | Cheap verdicts; redundant identity extraction |
| Content analysis | `content_delta` | 24,000 tokens shared across pages (`AAX_CONTENT_TOKEN_BUDGET`, `analyses.py:190-199`, ~96k chars at 4 chars/token, `prompts.py:19`) | 20% (`engine.py:155`) | Most expensive; thinnest value |
| Email validation | `email_validation` | Tiny (one line per email, `prompts.py:166-173`) | 15% (`engine.py:157`) | Cheap; output under-used |
| Contactability | `contactability` | Free — pure heuristic, no LLM (`analyses.py:349-391`) | 5% (`engine.py:157`, added 2026-08-30) | Keep; now scored |
| One-line summary | `aax_summary` | Embeds **full JSON dumps** of homepage + content results (`prompts.py:274-283`) — bigger than it looks | No weight; owner-only today | Best consultation asset we have |

Notes:

- There is a 7th scored signal: `llms.txt` presence (heuristic, free) carries **15%** (`engine.py:156`, `_add_llms_txt_factor` at `engine.py:317-343`).
- **Contactability is now scored (2026-08-30):** weights are homepage 30 / meta 20 / content 20 / llms.txt 15 / email 10 / contactability 5, summing to 100% (`engine.py:151-158`). Email validation gave up 5 points because it overlaps with contactability (both reward findable emails); llms.txt kept its weight because it's an independent signal. This also makes the existing recommendation's promised "AAX +10-20 points" (`recommendations.py:650`) partially real for the first time.
- The one-line summary renders as "Agent readout" (`result.html:393-398`) inside the **owner-only** Score Breakdown. Its 35-word limit is a prompt instruction only (`prompts.py:290`) with no code enforcement.

## The single most important finding

Two findings, actually — one about cost, one about the funnel.

**Cost: identity is extracted three times, shown once.** The AI is asked for the company's identity — who they are, what they sell, who they're for — three separate times:

- Homepage: `brand` / `product` / `target_audience` (`models.py:23-25`, prompt `prompts.py:83-85`) — the version shown in the report.
- Meta: identical fields (`models.py:39-41`, prompt `prompts.py:137-139`) — never displayed.
- Content: `company.name`, `product.name/description`, `target_audience` (`models.py:53-82`, prompt `prompts.py:234-237`) — never displayed.

The duplication is also baked into scoring: 40% of both the homepage and meta sub-scores comes from identity fields being filled (`engine.py:215-220`, `engine.py:253-254`), and content richness counts them again (`engine.py:299-314`). Measuring "can an AI figure out who you are" once is the product; measuring it three times is waste.

**Funnel: everything the AI produces is invisible until after sign-in.** The public preview (verified against a live report) contains only the deterministic risk summary, lens scores, and deterministic top-3 fixes. The AI one-line summary — the most specific, consultation-like sentence in the whole pipeline — only renders for owners. A generic "AI grabs fragments but can't see the whole story" applies to thousands of sites; the AI summary says something specific about *this* site, which is what converts.

## Test by test

### Homepage comprehension — used end-to-end, keep

Genuinely consumed: qualitative verdicts drive the score (`engine.py:222-232`), detected facts render in "What the Agent Understands" including key features (`result.html:691-698`, owner-only), and it feeds the summary. Only issue: the 50k-char budget is likely far more than needed. Env-configurable, so tuning, not a code change.

### Meta optimization — keep the verdicts, drop the identity extraction

The verdicts (completeness, clarity, llm_optimization, would_click_through) are cheap, scored (`engine.py:249-265`), and their `improvement_suggestions` are wired into recommendations (`recommendations.py:505-515`, tested in `tests/test_scoring_engine.py`). But `brand`/`product`/`target_audience` are written a second time here and never displayed. **Caveat:** those fields carry 40% of the meta sub-score (`engine.py:253-254`) — removing them requires rebalancing the sub-weights or meta scores silently drop.

### Content analysis (`content_delta`) — the biggest expense, the thinnest value

- Most expensive test (24k tokens across pages), only 20% of the score.
- `product.description`, `product.features`, `company.description` are stored but **never rendered** — the template only shows `strengths`/`weaknesses` (`result.html:702-727`). Their *existence* moves the richness score (`engine.py:307-308`) but their content is read by no one. Per the product context above: do **not** surface these in the report — the reader knows their own product. (They can stay in the stored JSON for the API/agent use case at no extra cost.)
- `weaknesses` are displayed (owner-only, `result.html:716-724`) and feed a recommendation — but that recommendation is **medium priority** and only emitted when `strengths_count < 3` (`recommendations.py:457-480`). Weaknesses are the most actionable AI output we have; they should reliably reach the top fixes.
- `coherence` (cross-page consistency — the one thing that genuinely needs multiple pages) is used in scoring (`engine.py:282, 291`) and fed to the summary, but never displayed as a verdict.

### Email validation — cheap, and we bury the good output

Produces `best_contact`, `valid_contacts` (each with `reason` and `contact_type`), `rejected_contacts`, `confidence` (`models.py:108-122`). The score uses only confidence/count/type/best-boolean (`engine.py:356-360`); recommendations check only low-confidence/no-contacts (`recommendations.py:596-620`). The report's "Contact Paths AI Can Find" section renders **raw crawl emails** (`result.html:859-890`, iterating `payload.emails.unique`) — zero references anywhere in `webapp/` to `best_contact`, `valid_contacts`, or `reason`.

### Contactability — free, fully used, now scored

No AI cost, drives a recommendation and a detailed report section. As of 2026-08-30 it also carries 5% of the AAX score (`_add_contactability_factor`, `engine.py`).

## Dead fields — confirmed, delete

Repo-wide grep confirms nothing reads these outside their schema and prompt definitions:

| Field | Defined at | Asked for at | Readers |
|---|---|---|---|
| Meta `missing_fields` | `models.py:46` | `prompts.py:131, 144` | None (the `missing_fields` in `recommendations.py:430-449` is an unrelated local built from homepage fields) |
| Content `product.category` | `models.py:64` | `prompts.py:235` | None |
| Content `pricing.tiers` | `models.py:73` | `prompts.py:236` | None (scoring reads only `pricing.model`, `engine.py:309`) |

---

# Implementation plan

Ordered by value-per-effort under the product context above: make the preview specific, keep the report short, cut paid waste.

## P0-1: Put the AI one-line summary in the public preview

**Why first:** already paid for, already ~35 words, and it's the single most consultation-like asset we have. One template change.

- [ ] Render `score_snapshot.aax_analysis.summary` in the public preview, at or near the risk-summary section (`result.html:219-235`) — e.g. as a labeled "Agent readout" line under the deterministic lede. The preview template already has the snapshot in scope.
- [ ] Keep the deterministic risk chips; the summary complements them with site-specific text.
- [ ] Decision to confirm before shipping: analyses are public-by-default ("Visibility: Public" badge), so exposing the summary to anonymous viewers matches current visibility — but it does mean anyone (including competitors) sees the AI's verdict. Assumed fine.
- [ ] Manual check: view an analysis logged-out; confirm the summary renders and the owner report still shows it once (no duplication).

## P0-2: Surface one AI-derived fix in the top-3

**Why:** all current top fixes are schema-checklist items. One content-weakness fix ("AI couldn't determine your pricing") proves the analysis actually read the site — the core consultation hook.

- [ ] In `recommendations.py:457-480` (`_content_delta_rec`), remove the `strengths_count < 3` gate and raise priority to `high` when weaknesses exist.
- [ ] Check how top-3 selection ranks recommendations and verify a weaknesses fix can actually land in the top 3 (it competes with deterministic high-priority items).
- [ ] Extend `tests/test_scoring_engine.py` for the new gating behavior.

## P0-3: Delete the three dead fields

**Why:** zero risk, immediate (small) token savings, smaller schema marginally improves quality of remaining fields.

- [ ] Remove `missing_fields` from `MetaOptimizationResult` (`meshweave/ai/models.py:46`) and the meta prompt (`meshweave/ai/prompts.py:131, 144`).
- [ ] Remove `category` from `ProductInfo` (`models.py:64`) and `tiers` from `PricingInfo` (`models.py:73`), and from the content-delta prompt (`prompts.py:235-236`).
- [ ] `uv run pytest -q`, `uv run ruff check .`, `uv run mypy` to confirm nothing referenced them.

## P1-4: Stop the meta test from re-extracting identity (with scoring rebalance)

**Why:** removes one of the three paid identity extractions. Requires the scoring fix in the same change, or meta scores drop ~40%.

- [ ] Remove `brand` / `product` / `target_audience` from `MetaOptimizationResult` (`models.py:39-41`) and the meta prompt (`prompts.py:137-139`).
- [ ] Rebalance the meta sub-weights in `_add_meta_optimization_factor` (`engine.py:249-265`): drop `field_score`, redistribute its 0.4 across the verdicts — e.g. completeness 0.35, clarity 0.25, llm_optimization 0.25, click-through 0.15.
- [ ] Update/extend tests in `tests/test_scoring_engine.py` for the new weights.
- [ ] Spot-check a few audits before/after to confirm meta scores stay in a sane range.

## P1-5: Narrow content_delta to what it's uniquely good at; cut its budget

**Why:** per the product context, we will NOT show product description/features in the report — so stop paying to extract them at breadth. Only `coherence` and `weaknesses` need multiple pages.

- [ ] Slim the content-delta prompt (`prompts.py:215-248`) to ask for weaknesses, strengths, coherence, completeness, and pricing model — dropping the long-form product/company description extraction.
- [ ] Adjust `_content_richness_score` (`engine.py:299-314`) to match the slimmer schema.
- [ ] Lower `AAX_CONTENT_TOKEN_BUDGET` (default 24000, `analyses.py:190-199`) after the slimming — try 12k on a sample of audits and diff `coherence`/`weaknesses` outputs.
- [ ] Fix the stale docstring at `analyses.py:192-194` ("~12.5k-token ceiling") while there.
- [ ] Surface the `coherence` verdict as one labeled line in the owner report (e.g. "Site-wide consistency: consistent") — it's scored but never shown, and "your pages disagree about what you sell" is a strong consultation hook.

## P1-6: Show the AI's email conclusions in the owner report

**Why:** second-funnel value (post-sign-in), supporting the "email us" conversion — one concrete, verifiable win: "your best contact is X, these 3 are junk."

- [ ] In `webapp/templates/result.html:859-890`, render `aax.email_validation` conclusions: `best_contact` highlighted, `valid_contacts` table (`email`, `contact_type`, `reason`), rejected count; fall back to the current raw table when `email_validation` is absent.
- [ ] Verify `aax.email_validation` reaches the template context (`webapp/utils/scoring.py:310-368`); add if missing.
- [ ] Manual check: audit a site with mixed-quality emails; confirm the section shows the AI pick and reasons.

## P2-7: Tune the homepage character budget

**Why:** 50k chars is likely over-generous for verdicts that don't change with more text. Env-configurable — experiment first.

- [ ] Re-run a sample of audits with `AAX_HOMEPAGE_MAX_CHARS=20000` and diff homepage verdicts (`clarity`, `information_density`, `would_remember`, detected facts).
- [ ] If stable, lower the default in `analyses.py:182-187`.

## P2-8: Measure real per-test token usage

**Why:** priorities above are argued from budgets, not measurements. The summary prompt embeds two full JSON dumps (`prompts.py:274-283`) and may not be cheap.

- [ ] Log input/output tokens per test per audit in `meshweave/ai/runner.py`.
- [ ] After a week of data, replace the "Input budget" column in this document with measured numbers and re-check the P1/P2 ordering.

## Out of scope (deliberately)

- Rendering `product.description`/`features`/`company.description` in the report — rejected per product context (report stays short; that data stays in the stored JSON for API/agent consumers).
- ~~Scoring contactability~~ — **done 2026-08-30** (5% weight, email_validation reduced 15→10).
- Changing the remaining AAX weights (30/20/20/15/10/5) — revisit only after P1 items land and measured costs are known.
- Enforcing the 35-word summary limit in code — prompt instruction is sufficient.
- Per-section report engagement analytics — the conversion metric is contact/sign-in, not reading time.

## The bottom line

The system isn't broken — the score, the recommendations, and the owner report are driven by real AI output. But the AI's most persuasive sentence is hidden behind sign-in, its most actionable findings are gated out of the top fixes, and we pay a premium for duplicated identity extraction and long-form content nobody reads. The plan: **surface the summary and the weaknesses where they convert, stop asking for what's never read, then cut budgets where measurement says so.**
