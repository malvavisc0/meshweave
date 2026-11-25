# MeshWeave launch-readiness report

## 1. Launch blockers

**B1 — SSRF guard bypassable on the site-scope form via the `url` field**
- Journey 1, anonymous submit. `webapp/routers/submissions.py:126-135` (`_site_start_url`), called at `:391`.
- Now: only the `domain` field passes `reject_internal_target` (`submissions.py:119`); the `url` field's host becomes the crawl start URL verbatim, so `domain=example.com&url=http://169.254.169.254/latest` sends the crawler at the cloud-metadata address while the checked field is a public decoy. The funnel doc's claim ("wired into both browser form paths — page and site scope both") holds for page scope (`submissions.py:524`) and the site `domain` field only.
- Confirmed by repro: `POST /submit mode=site domain=example.com url=http://169.254.169.254/latest&public=1` → 303 accepted, report page shows the analysis ran for `https://169.254.169.254/latest`; the metadata IP alone via `domain=` or page `url=` correctly 400s.
- Smallest fix: run `reject_internal_target` on the `url` field's host inside `_site_start_url` (same extraction as `submissions.py:524`).

**B2 — Anonymous home form has no rate limit, no quota, and no cooldown**
- Journey 1, quotas + cooldown. `webapp/routers/submissions.py:383-386` ("Enforce quotas only when authenticated"); `_enforce_rate_limit` is only reached from the page path (`submissions.py:602`); `_upsert_site_crawl_row` always replaces (`submissions.py:487-506`) where page mode has a 60-min cooldown (`:1074-1081`).
- Now: every anonymous home submit spawns an unbounded background crawl (up to 10 pages, depth 5, default 1-hour time budget — `webapp/services/site_crawling.py:55-59`), with no concurrency cap anywhere. Spec expects cooldown and anonymous limits as real safeguards.
- Confirmed by repro: two immediate anonymous `mode=site` submits both 303 with no 429 and re-crawl on resubmit; page-mode resubmit correctly returns `/?notice=cooldown`.
- Smallest fix: call `_enforce_rate_limit` at the top of `_submit_site` and apply `_now_refreshing` before `_upsert_site_crawl_row`; set `ANON_SITE_TIME_BUDGET_MS_CAP` in compose.

**B3 — `X-Real-IP` is trusted even with `WEBAPP_TRUST_PROXY=false`, defeating the only anonymous throttle**
- Deployment / abuse. `webapp/utils/http.py:78-83` — when `trust_proxy` is False the code still falls back to `_real_ip_header(headers)` (line 81) before the connection host.
- Now: any client sending `X-Real-IP: <random>` gets a fresh rate-limit bucket per request (`submissions.py:790-803`) and poisons the IP audit log (`submissions.py:251`).
- Confirmed: code path; the limiter's sole counter key is the derived client IP + session cookie.
- Smallest fix: only read `X-Real-IP`/`X-Forwarded-For` when `trust_proxy` is true; else use the connection host.

**B4 — Request-body caps are bypassable → unauthenticated OOM**
- Deployment / abuse. `webapp/routers/api_v1.py:371-381` (`_enforce_body_size` returns early when `Content-Length` is absent; `await request.json()` at `:348` then reads unbounded); `POST /submit` and all form endpoints have no cap at all (`submissions.py:274-312` — FastAPI parses multipart first).
- Spec promises "a 64 KB body-size cap before parsing (413)". Confirmed by repro: 200 KB chunked body without `content-length` accepted (`queued`); same body with the header → 413.
- Smallest fix: count bytes from `request.stream()` (or a global middleware cap) and 413 past the limit; add a cap to form routes.

**B5 — Claim flow has no cookie proof: any signed-in user can take a crawl they didn't run**
- Journey 2, claiming. `webapp/routers/api.py:598-660` (`claim_public`) — preconditions are only `visibility=public`, `user_id IS NULL`, `created_at` ≥ 24 h old. No `mw_anon_id` check.
- Now vs spec: docs/conversion-funnel.md ("Ownership can never be taken without proof… both flows require the browser's anonymous id to match") and the journey ("the cookie must match; no one can take a crawl they didn't run") are violated for the claim path. The save path is correct — `webapp/services/funnel.py:203-239` (`save_own_analysis`) requires the cookie match and rejects the None-cookie case explicitly. Note the conflict: `webapp/templates/terms.html:94-95` and `privacy.html:69-71` currently promise the weaker "any signed-in user after 24 h" behaviour; the funnel spec wins, so those two paragraphs also need alignment.
- Confirmed: code path (endpoint never reads the anon cookie); save path verified line-by-line.
- Smallest fix: add the same `anonymous_user_id == anon_id` condition to the claim UPDATE (with the None-cookie rejection), and update the terms/privacy claiming paragraphs.

**B6 — No way to make an existing report public or private**
- Journey 2, visibility toggle. No route exists: `webapp/routers/analysis.py` has only `/analysis/{ref}`, `/export`, `/diff`; `jobs.py` has cancel/retry/list; `profile.py` has save/delete. Grep for toggle/publish/set-visibility endpoints returns nothing.
- Now: visibility is fixed at submit time (`webapp/utils/visibility.py:1-22`). The journey requires "making a report public or private"; a signed-in user with a private run cannot share it, and a public run cannot be made private (privacy.html:104-105 even promises a claimed analysis can "then be removed" — no per-analysis delete route exists either).
- Confirmed: exhaustive route grep across `webapp/routers/`.
- Smallest fix: one owner-only `POST /analysis/{id}/visibility` that flips `Crawl.visibility` (and, separately, an owner-only delete for the privacy-promise path) — or drop those promises from the pages.

**B7 — Retrying a non-latest revision mints two `is_latest` rows and bricks the series lookup**
- Journey 3, revision chain. `webapp/utils/revisions.py:69-95` — line 73 retires only the row being retried; line 87 inserts `is_latest=True` without retiring the series' actual latest. Reachable: revision strip (`webapp/templates/partials/revision_strip.html:19`) → old revision page → Retry (`result.html:297`; `analysis.py:441-443` computes `can_retry` with no `is_latest` check) → `jobs.py:508`.
- Spec: conversion-funnel.md:228-236 — "Exactly one `is_latest = True` row per series… eventually 500ing the lookup." After this, `_find_latest_crawl`'s `.one_or_none()` raises `MultipleResultsFound` → 500 on the next submit (`submissions.py:844`, `api_v1.py:298`).
- Confirmed: code path (no test retries a non-latest row; `tests/test_retry_revision_safety.py` only covers retry-of-latest).
- Smallest fix: in `replace_succeeded_crawl`, retire the series' current latest first (or refuse the retry with 409 unless `db_row.is_latest`).

**B8 — Bulk admission retires the previous revision before the quota check — rejected item destroys history and kills a queued job**
- Journey 5, bulk submit (also Journey 3). `webapp/routers/api_v1.py:298-313` — retire + queue-job drop at `:303-310` run before `budget.allows()` at `:311`.
- Now: a `quota`-rejected re-submit still retires the previous latest (`is_latest=False`, `key=None`) and drops its pending queue job, leaving zero latest rows and a stranded job. With prod defaults (2 concurrent, 20/day) `quota` is the common case for 25-URL batches.
- Confirmed by repro (batch cap forced to 0): response `{"status": "quota", "crawl_id": null}`, old row retired, `queue_status=None`, no new crawls. Two independent traces agree on the code order.
- Smallest fix: move the retire/cancel block after the budget check, inside the admitted branch.

**B9 — Browser report download emits no `report_exported`**
- Journey 4, exports. `webapp/routers/analysis.py:687-725` (`export_analysis`) has no `funnel.emit`; `EVENT_REPORT_EXPORTED` occurs only in `api_v1.py:454-462`.
- Spec: conversion-funnel.md:56-61 lists the event; ":388-389 Which users exported reports or diffs?" is a promised funnel answer. The signed-in promise "a downloadable client-ready report" (home.html:53, 375) is exactly this button (`result.html:602`) — so the primary export path is invisible to the funnel. (Anonymous rule holds: exports are owner-only.)
- Confirmed: grep shows both export events only in `api_v1.py`; `tests/test_funnel_emits.py` covers crawl outcomes only.
- Smallest fix: one `funnel.emit(s, row.user_id, EVENT_REPORT_EXPORTED, …)` after the ownership gate in `export_analysis`.

**B10 — Anti-nagging rule broken: gate offer and API hint still render at `inquiry`/`customer`**
- Journeys 5 and 6, nudge selection. `webapp/services/nudges.py:195-207` — `bulk_gate` returns on `not has_key` alone, `first_call_hint` on `has_key and not first_used` alone; only `services_offer`/`save_analysis` check `stage in ("registered","api_consumer")`.
- Spec: conversion-funnel.md:290-292 — "once a user shows contact intent (`inquiry`), gate and services offers stop rendering… Once they are a `customer`, all offers stop." Now: a services customer without a key is permanently re-pitched (dismissals resurface after 7 days), and each wrong render also emits `gated_feature_hit` (`nudges.py:96-104`), polluting the offered-to counts.
- Confirmed by repro against the real selector: `select_nudge("dashboard", stage=inquiry, has_key=False)` → `bulk_gate`; `customer` + unused key → `first_call_hint`. `tests/test_funnel_emits.py:282-291` masks it (asserts only `!= "services_offer"`).
- Smallest fix: add `stage in ("registered","api_consumer")` to both branches in `_eligible`; tighten the test to `assert nudge is None` at `inquiry`/`customer`.

**B11 — LLM down: Actionable publishes a high score from one heuristic, unlabeled**
- Journey 9, LLM failure. `meshweave/scoring/engine.py:107-118` + `meshweave/scoring/composite.py:98-102` — when all four LLM tests fail, only `contactability` survives and its 0.15 weight renormalizes to 100%; the skip notes that would explain it are suppressed by `webapp/templates/result.html:140` (`{% if factor.note and not factor.skipped %}`), and only the first missing factor gets a caveat (`webapp/routers/analysis.py:46-62`).
- Now: a client sees "Actionable: 83 — Fluent" (reproduced) computed from one rule-based factor while the AI review is down — a misleading score, not a labeled degradation. Answerability itself degrades correctly in the score (`score: None`, renormalized, "—" with a caveat — `aeo.py:58-62`, `result.html:195-197`).
- Confirmed: reproduction with all LLM calls failing (contactability 85 → composite 83.0).
- Smallest fix: when any test lands in `skip_reasons` as "Test failed", render the AAX composite as "—" or label it "AI review failed — rule-based signals only", and invert the `result.html:140` suppression for skipped factors.

**B12 — Agent-facing files link to three nonexistent routes**
- Deployment, llms.txt / llms-full.txt. `webapp/static/.well-known/llms.txt:15,42,46`; `llms-full.txt:30,50`.
- Now: `[API Reference](/api)` → 404 (no `GET /api` route), `POST /api/submit` → 404 (real route is `POST /submit`, `submissions.py:274`), `/analysis/public/{key}` → 404 (real shareable URL is `/analysis/{key}`). For an agent-facing audit product these are dead ends on the exact journey the files describe.
- Confirmed: live curl — `/api` 404, `/api/submit` 404, `/analysis/public/abc123` 404; all other referenced routes 200.
- Smallest fix: correct the three URLs in both files (and add a link-check test).

**B13 — Historical-crawl purge required by the scoring reset does not exist**
- Journey 9, data reset. `alembic/versions/` contains only `880764c4c4b4_initial_schema.py`; no purge migration and no CLI (only `crawl`, `mark-customer`).
- Spec: site-side-agent-readability.md step 3 — "delete every historical crawl and its dependent snapshots… before enabling the new model." Crawls stored before the answerability schema fix carry wrong Answerable data (the omission bug discarded every answer), the fix was never a scoring-version event, so `ensure_same_scoring_version` cannot separate pre/post rows, and they render live on the public promise. Conditional on pre-fix rows existing in the launch DB — if you launch on a fresh DB this drops to "have the purge ready before any restore of old data."
- Confirmed: migration listing; FK audit shows the purge *is* safe for the preserve-set (`funnel_events.crawl_id` and `prospects.crawl_id` are `ON DELETE SET NULL`, snapshots/submissions `CASCADE`, accounts/keys/usage/funnel-state/actor-domains have no crawl FK).
- Smallest fix: one migration (or documented one-shot SQL) `DELETE FROM crawls` — cascades do the rest; run before opening traffic.

**B14 — Privacy policy promises a cookie/retention story the code does not keep**
- Journey 8, privacy. `webapp/templates/privacy.html:80` ("HttpOnly and Secure") vs prod compose setting only `WEBAPP_AUTH_COOKIE_SECURE` — `sid` and `mw_anon_id` ship without `Secure` (`webapp/utils/security.py:42`, `submissions.py:1104`; `docker-compose.prod.yaml` has no `WEBAPP_COOKIE_SECURE`). `privacy.html:96-98` ("HTML snapshots, raw page content… not permanently stored") vs the fetcher's on-disk page cache with no eviction (`meshweave/crawling/fetcher.py:135-141`, `cache_data` volume) and `submissions` rows retaining raw `client_ip` + `cookies_json` forever (`submissions.py:232-259`, `WEBAPP_MASK_IP` off by default).
- Confirmed: config grep + code paths.
- Smallest fix: add `WEBAPP_COOKIE_SECURE: "true"`, `WEBAPP_MASK_IP: "true"`, `IP_HASH_SALT` to prod compose (see `docker-compose.prod.yaml` env block) and either add cache/submissions retention or correct the policy text.

## 2. Fix this week

**W1 — Home "Recently audited sites" shows failed and unfinished crawls** (known problem: **open**). Journey 1 listing. `webapp/routers/home.py:107-122` — filters only visibility/owner/listed/key and deliberately sorts failed rows into the top 6 (`analysis_card.html:28-29` renders a "Failed" badge). `/browse` is correctly restricted (`all_public.py:222-231`). Repro: failed run listed on `/` with badge, absent from `/browse`. Fix: add `Crawl.status == "succeeded"` to `_fetch_recent_rows` and drop the status CASE ordering.

**W2 — Signed-in "Refresh" on a public report creates an ownerless row** (edge case of the `/browse` promise). Journey 1/2. `submissions.py:877-937` — page-mode public upserts never set `user_id`, while `/browse` uses `user_id IS NULL` as the "no account" test (`all_public.py:225-231`), so a signed-in user's run can end up under "Finished audits run without an account" and loses funnel attribution (`site_crawling.py` `_emit_outcome` branches on `row.user_id`). Fix: stamp `user_id` on rows created in `_upsert_public_page_crawl` when a user is present.

**W3 — Page-scope form bypasses per-user quotas.** Journey 1. `submissions.py:659-719` (`_submit_page` never calls `enforce_*`; they run only at `:384-386` and in retry, `jobs.py:467-469`). A signed-in user re-running via the Refresh form escapes the 3-concurrent / 10-per-day limits. Fix: call the enforce helpers inside `_submit_page` for authenticated users.

**W4 — Series key omits scope: root page runs and site runs collide.** Journey 3. `submissions.py:827-844` (`_find_latest_crawl` has no scope filter; site upsert matches any `path="/"` at `:495-504`) vs the documented series (owner, domain, path, query, **scope**, conversion-funnel.md:228) and `_same_series_scope` (`webapp/utils/diff.py:29-52`). A page re-run can retire the site-scope latest and leave that series with zero latest rows; `?vs=` checks use `bool(crawl_params)` (`api_v1.py:485`, `analysis.py:748`), disagreeing with the diff matcher for `crawl_params={}` rows. Fix: filter on scope in `_find_latest_crawl` / `_upsert_site_crawl_row` (reuse `_same_series_scope`) and use it for `?vs=`.

**W5 — Retire path is a read-modify-write with no lock.** Journey 3. `webapp/models.py:238-260` (non-unique `ix_crawls_domain_is_latest`); retire at `api_v1.py:307`, `submissions.py:863` after an unguarded read. Two concurrent admits can both insert `is_latest=True` → the B7 endgame. Fix: partial unique index on the series key `WHERE is_latest`, or claim-by-conditional-UPDATE (the pattern already used for `first_used_at`).

**W6 — Form site rows carry `crawl_params={}` and several dispatch sites still use truthiness.** Journey 3/4. `submissions.py:435`, `:477` (form posts no limit fields) vs conversion-funnel.md:240-244 ("site rows always carry real limit parameters… dispatched on `crawl_params is not None` everywhere"). Truthiness pockets: `jobs.py:456-461` (a `{}` site row's retry dispatches as a page crawl), `jobs.py:468`, `:499`, `revisions.py:93`, labels at `export.py:138`, `diff.py:113`, `analysis.py:763`. The bulk API is correct. Fix: fill server defaults at form insert and switch pockets to `is not None`.

**W7 — API `report.md` lacks the readiness gate the browser export has.** Journey 4. `api_v1.py:446-447` gates only on `status == "succeeded"`; the browser export also requires `score_snapshot` and `aax_status == "completed"` (`analysis.py:705-710`). Since AAX runs async, `report.md` can ship with "—" actionability and missing findings while labeled client-ready. Fix: apply the same gate (or stamp an explicit "actionability pending" line).

**W8 — Unbranded-branding test renders an empty body.** Journey 4. `tests/test_api_v1.py:137-171` builds a crawl with no `ScoreSnapshot`, so the executive summary, answerability and recommendations sections the assertion scans are empty (`webapp/utils/export.py:130,186-268`). Branding introduced via interpretation/recommendation text would not fail the suite. Fix: seed a populated snapshot in the branding fixture (as `test_export.py:107-164` does).

**W9 — `api_key_first_used` stamps on calls that then fail.** Journey 5. `webapp/routers/api.py:88-102` — the CAS runs at auth time, before the endpoint body; a valid key + garbage body (repro: `[]` → 400) still stamps, emits, and promotes to `api_consumer`. Invalid keys correctly never stamp (raise at `:81-82`). Spec: "the key actually works for them" is the activation signal. Fix: move the stamp to after a 2xx completion (or a response-status-aware hook).

**W10 — `gated_feature_taken` over-emitted; `record_gate_taken` has no allowlist.** Journey 5. `webapp/routers/api.py:869-875` maps every `nudge_cta_click` to `record_gate_taken`, and `funnel.py:308-323` accepts any name (unlike `record_nudge_dismissal`, `funnel.py:273-274`). CTA clicks on `first_call_hint`/`services_offer` (which never emit `gated_feature_hit`) mint taken-without-hit rows; Track E sequences are unpairable. Fix: restrict to the gate features (`bulk_gate`, `save_analysis`) or `NUDGE_NAMES` at minimum.

**W11 — `gates_seen`/`gates_taken` bookkeeping is read-check-write.** Journey 5. `funnel.py:293-323` — concurrent renders/clicks can double-emit the once-per-user events (sequential path is exactly-once). Fix: conditional UPDATE on the JSON column (or a partial unique index on `(user_id, event_type, feature)`).

**W12 — `meshweave mark-customer` crashes as the installed command.** Journey 6. `meshweave/cli.py:414` imports `webapp.db` but packaging ships only `meshweave*` (`pyproject.toml:55-56`) → `ModuleNotFoundError` on `uv run meshweave mark-customer` (reproduced); works via `uv run python -m meshweave.cli mark-customer …` and inside the app image (`PYTHONPATH=/app`). Semantics themselves are correct: exactly-once, terminal stage, bad `--source` rejected (`funnel.py:354-355`, `:326-371`). Fix: runbook note + include `webapp*` in packaging.

**W13 — Fix list is not in expected-points order despite the published promise.** Journey 9. `webapp/utils/scoring.py:245-266` sorts by (priority band, weakest-pillar-first), but `scoring.html:208-209` promises "in order of how many points each is expected to add" and the listed promise is "the report ranks fixes by expected points". Within the `high` band, three +3-point fixes can outrank a +12-point fix and push it out of "Fix these first" (`result.html:448-453`). Values themselves are honest (`composite.py:112-153`, pinned in `tests/test_recommendation_impact.py`). Also `scoring.py:253-255` treats a real 0.0 score as 100 when ranking pillars. Fix: sort by `-expected_points` (None last); band stays display-only.

**W14 — `freshness` can score 20/100 with no fix and no reason.** Journey 9. `meshweave/scoring/aeo.py:428-438` vs `recommendations.py` (no freshness generator anywhere). A stale site silently loses 15% of Answerable. Fix: one recommendation ("publish `dateModified` in JSON-LD") with a target derived from the freshness bands.

**W15 — `AAXSummaryResult.summary` is optional in the LLM schema.** Journey 9. `meshweave/ai/models.py:195-198` has `= ""`, so the schema sent to the model marks it optional — the same failure class as the recorded incident where omitted optional fields discarded every answer (`models.py:4-10`); the hero "Agent readout" can silently render blank. Missing from `TestStructuredOutputSchema`. Fix: drop the default, add the model to the schema test.

**W16 — Answerability panel shows a misleading 0% when every question errors.** Journey 9. `meshweave/ai/answerability.py:268-282` sets `status="completed"`, `answer_support=0.0`, `question_count=6` even with zero applicable questions; `result.html:158-164` then claims "answered 6 decision questions… 0%". Fix: derive status/metrics from applicable questions (e.g. `skipped` when none ran).

**W17 — No application-level LLM timeout.** Journey 9. `meshweave/ai/runner.py:56-64` runs unbounded; the AAX queue processes jobs sequentially (`webapp/services/scoring.py:644-650`), so one slow endpoint stalls every pending analysis. Fix: `asyncio.wait_for` per test with a budget, record a skip reason on timeout.

**W18 — Sitemap serves `/analysis/None`.** Deployment. `webapp/routers/api.py:556` includes succeeded listed rows whose `key` was cleared by retirement (`api_v1.py:308`). Live-confirmed in sitemap output. Fix: filter `Crawl.key.isnot(None)`.

**W19 — Prod config gaps.** Deployment. (a) CSP is dead config: `webapp/app.py:345,359` gates on `WEBAPP_ENABLE_CSP` which neither compose sets → zero CSP headers in prod — set it plus `WEBAPP_CSP` in `docker-compose.prod.yaml`. (b) `WEBAPP_SECRET_KEY` falls back to literal `"dev-secret"` (`webapp/utils/config.py:48-50`) — fail closed outside dev. (c) `DATABASE_URL` empty silently falls back to SQLite at `/db/app.db` (`webapp/db.py:19,64-68`) — log loudly or require. (d) `ANON_SITE_TIME_BUDGET_MS_CAP` unset in compose (default 1 h of shared CDP per free crawl) — set a sane cap. (e) Form submits/retries ride `BackgroundTasks` and are lost on restart — rows stay `pending`, `queue_status=NULL` forever (`progress.py:30-33,258-261`) — put form/retry jobs on the durable queue or add a reaper.

**W20 — Named abuse gaps for the unmetered free API** (spec defers limits; these are the specific holes an attacker or spike gets):
- No per-key rate limit on `/api/v1/*` (`api_v1.py:67-82` records usage only) — unlimited distinct-URL crawls → LLM spend + crawl egress + DB growth; page scope has no daily cap at all (`webapp/utils/quotas.py:69-82`).
- No global crawl concurrency cap or queue-depth bound — N identities saturate the single LightPanda and the DB pool (`crawl_queue.py:38`, `db.py:42-57`).
- The one anonymous throttle is fail-open and secretless: DB errors "proceed" and its counter lives in the `submissions` log table (`submissions.py:565-567,817-820`), so `WEBAPP_LOG_REQUESTS=false` disables it — combined with B3 it has three off-switches.
- `/api/track` is unmetered and takes an attacker-controlled `surface` into Prometheus labels with no cardinality bound (`api.py:839-854`, `:902-911`) → in-process memory growth/OOM; whitelist the label values.
- Unbounded growth stores: `submissions` rows (raw IP + cookies) never pruned; fetcher page cache never evicted (`fetcher.py:135-141`) — disk-fill outage risk on the `cache_data`/`postgres_data` volumes.

## 3. After launch

- **A1** — CSRF-failure path strands anonymous visitors on a raw 401 (`submissions.py:355-358` redirects to `/dashboard`, which 401s unauthenticated). Redirect to `/?notice=csrf_failed` instead. Journey 1.
- **A2** — Dead "status banner" on home (`home.py:461-465` reads `?submitted=` which nothing ever sets). Remove or wire up. Journey 1.
- **A3** — Cross-version comparison gaps outside the three refused paths: `_since_last_run` (`analysis.py:366-401`), dashboard deltas (`jobs.py:124-153`), CSV history (`api.py:405`) compare revisions with no `ensure_same_scoring_version`. Harmless until the next scoring-version bump. Journey 3.
- **A4** — `diff.md` honesty footer is unpinned by tests (`export.py:404-407`); the report analogue is pinned. Add one assertion. Journey 3.
- **A5** — `_begin_crawl` permits `succeeded → running` (`crawling.py:45-52`, `site_crawling.py:93-105`); no reachable caller today, but an in-place re-run would double-emit `analysis_completed`/`recheck_completed`. Journey 3.
- **A6** — "Audit up to 25 sites in one call" (home.html:399, llms.txt:13) vs default quotas: one call admits 3 (prod: 2) and returns the rest as `quota` — code matches spec (per-URL outcomes, batch-atomic counting); the copy overstates. Either raise launch defaults (`AUTH_USER_CONCURRENT_JOBS` ≥ 25) or qualify the copy. Journey 5.
- **A7** — `api_key_usage_daily.calls` undercounts: Bearer use of the legacy private route never calls `record_usage` (`api.py:289-303` vs `api_v1.py:76-81`). Journey 5.
- **A8** — Agency threshold check is load-bearing on garbage collection: the flip reads `distinct_domains` via an ORM identity-map instance (`funnel.py:98,106-111`); a refactor that retains that instance silently slides the trip point to the 6th domain (traced). Behavior today is correct at exactly 5 with exactly one `segment_upgraded`. Make the read a scalar `SELECT`/`UPDATE … RETURNING` and pin the exact threshold in `tests/test_funnel.py:333`. Also: `distinct_domains` freezes at 5 after the flip (`funnel.py:99-105`) — note for anyone querying it. Journey 7.
- **A9** — Non-weak imperfect factors (`topical_authority` ≥ 60, `content_depth`, empty `_meta_issues`) get neither fix nor reason (`recommendations.py:512-557`). Journey 9.
- **A10** — AAX contactability awards `SOCIAL_LINK_POINTS = 10` for sameAs presence (`meshweave/ai/analyses.py:513-518`, `models.py:99`) — a third-party marker earning points in Contact Paths (the GEO-side contradiction is fixed; this is the residual). Journey 9.
- **A11** — Deployment polish: `DEBUG` compose var is dead (`docker-compose.prod.yaml:123` — read nowhere); LightPanda "Pinned digest" comment vs mutable tag (`:34-35`); compose `CDP_ENDPOINT` name mismatch with `MESHWEAVE_CDP_ENDPOINT` (`:71` vs `fetcher.py:44`) and undocumented in `.env.prod.example`; bare image has no browser and no CDP default (`Dockerfile:122-123`); generic `container_name: postgres` collides (`:14`); `AUTH_SITE_MAX_PAGES_DEFAULT` code default 200 vs copy's 25 (`api_v1.py:241`); error pages build canonical/OG from `str(request.url)` → internal origin leak behind the proxy (`app.py:486-492`); JSON-LD `softwareVersion` falls back to `"v1"` (`home.py:402`); host-side `uv run alembic` needs `PYTHONPATH` (`alembic/env.py:11`); a queue job `running` at retire time keeps executing the retired row (`api_v1.py:309` condition).
- **A12** — Page-mode submissions persist raw `client_ip`/`cookies_json` per anonymous visit with no retention (`submissions.py:232-259`); aggregate-only anonymity holds in Prometheus and the funnel tables, but this DB residue sits next to the "no per-identity histories" ground rule. Mask + prune (part of B14's fix).

### Known problems — status

| Problem | Status |
|---|---|
| AAX contactability capped at 20 by same-domain-email penalty, no fix | **Already fixed** — `_PENALTY_FIXES` names it ("Publish a contact email on your own domain… removes N points", `recommendations.py:1069-1120`), impact respects the binding cap (`:1140-1156`), pinned `tests/test_fix_list.py:79-95`. |
| Fixes predict 0 points yet high priority; praise items in fix list | **Already fixed** — priority derives from `expected_points` (`recommendations.py:94,108-116`); zero-gain items are dropped (`:119-126`); pinned `test_zero_point_fix_is_dropped`, `test_strong_schema_yields_no_praise_item`. |
| `crawl_access` peaks at 77 | **Already fixed** — 77 points rescaled to 0-100 (`geo.py:21-24,333-340`); reproduced 100.0 for a perfect site; pinned `test_fix_list.py:132-156`. |
| `entity_consistency` gives points for sameAs | **Already fixed** (GEO) — score is name+description only, sameAs kept as evidence (`geo.py:592-621`); matches `scoring.html:64-65`. Residual AAX social-link points → A10. |
| Pre-schema-fix crawls have wrong Answerable; purge planned | **Open** — no purge migration/CLI exists (B13); it is FK-safe for accounts, keys, usage, prospects and funnel events. |
| Home "Recently audited" shows failed/unfinished | **Open** — confirmed by repro (W1). |

### Deployment readiness summary
- **Production config**: `meshweave-webapp` rename wired end-to-end (`webapp/pyproject.toml:46-47`, `Dockerfile:133`, verified in the running image); healthchecks/restart/`WEBAPP_AUTO_MIGRATE` all sound. Gaps: W19 items + A11 items.
- **Env vars**: prod compose requires `SITE_BASE_URL`, `WEBAPP_SECRET_KEY`, `FOOTER_CONTACT_EMAIL`, OAuth, `LLM_*`, `METRICS_AUTH_TOKEN` via `:?` — good; code defaults are the weak spot (B14, W19).
- **Alembic**: single head `880764c4c4b4`; runs clean from empty (scratch DB) and is a no-op from current — verified both directions.
- **Durable queue recovery**: works — stranded `running` reset every poll, terminal rows never re-crawled, 15-min timeout (`crawl_queue.py:42-146`, 19 tests pass). Exception: form-path jobs (W19e).
- **Agent-facing links**: three dead (B12) + sitemap `None` (W18); everything else 200.
- **Canonical/OG**: correct when `SITE_BASE_URL` is set (prod forces it); residuals A11.
- **Metrics**: anonymous activity is aggregate-only — labels are categorical, funnel rows require `request.state.current_user`, `/metrics` auth is fail-closed. Residuals: W20 label cardinality, A12.
- **Abuse limits**: inventory in W20 — batch cap 25, body cap (bypassable, B4), per-user quotas, cooldown, SSRF (hole, B1), login limit, honeypot, CSRF. With an unmetered free API the named gaps are per-key limits, a global concurrency cap, the spoofable/fail-open anonymous throttle, `/api/track`, and unbounded stores.

### Journey checklist

1. **Anonymous visitor — broken**: happy path, failure UX and `/browse` filtering work; SSRF hole via the site-form `url` field and no anonymous quota/cooldown (B1, B2).
2. **Sign-in and ownership — broken**: OAuth, `user_registered`, save-with-cookie-proof and private-by-default all work; claim lacks the cookie proof and no visibility toggle exists (B5, B6).
3. **Re-run and proof — broken**: diff honesty and cross-version refusal hold; the revision invariant breaks via retry-of-old-revision and the bulk retire-before-quota path (B7, B8).
4. **Report export — broken**: artifacts are correct (branded owner-only browser, unbranded API, branding pinned); browser export emits no `report_exported` (B9).
5. **Plan B — broken**: pipeline, owner scoping (404s), usage accounting and exactly-once activation CAS work; a `quota` rejection destroys history and a queued job (B8).
6. **Plan A — broken**: threshold, mailto-vs-navigation intent split, `contact_page_audit` (recorded as `contact_mailto_clicked` with its own surface, counter `contact_cta_clicks_total{surface="contact_page_audit"}` — correct, and footer nav is counter-only), and `mark-customer` semantics all work; offers keep rendering after inquiry/customer (B10).
7. **Agency detection — works**: fifth distinct domain flips at exactly 5 with one `segment_upgraded`, re-runs never re-count, failures never count (A8 is latent hardening only).
8. **Account deletion and privacy — broken**: deletion correctly cascades funnel history, keys, usage and crawls (`profile.py:92-111`, `models.py:362-367`, `user_id ON DELETE CASCADE` on all funnel tables) — the deletion half matches the spec; the privacy-policy retention/cookie promises don't match the code (B14).
9. **Scoring integrity — broken**: factors/weights/questions/bands/grounding/expected-points math all match the methodology page; LLM-down publishes a misleading Actionable score (B11), the purge is missing (B13), and the fix-list ordering promise is not what the sorter does (W13).

**Bottom line**: 14 launch blockers, of which B1–B4 and B20-style abuse holes are the ones a public URL form plus unmetered API will hit first; B5 and B7/B8 corrupt or surrender user data on plain user actions. None require redesign — each has a one-screen fix — but I would not open the free funnel to strangers until at least B1–B4, B7, B8, and B12 are in.